#!/usr/bin/env python3
"""AERIS Phase 2 - end-to-end WebSocket multi-client stress harness.

This is NOT part of the unit-test suite (it is not named test_*.py). It drives
a RUNNING backend over real TCP/WebSocket/HTTP, so it can measure the deployed
station exactly as a laptop/phone/tablet sees it.

What it measures
  * every client receives auth_ok + the initial telemetry frame,
  * every steady client keeps receiving system reports and heartbeats,
  * the worst inter-message gap per client (a stalled server shows up here),
  * browser-refresh churn (repeated connect/auth/close) while stable clients
    stay connected,
  * the backend's own hub diagnostics before/after (accepted/closed counts,
    queue depth, coalescing, slow-client drops).

Usage
    ./venv/bin/python tests/ws_stress.py --url http://127.0.0.1:80 \
        --token-file .aeris_auth_token --clients 10 --seconds 120

The token is read from --token-file (default: the backend's real token file)
or --token. It is never printed. No hardware command is issued.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

import websockets


def http_json(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


class ClientResult:
    def __init__(self, name: str):
        self.name = name
        self.connected = False
        self.auth_ok = False
        self.telemetry = 0
        self.system = 0
        self.ping = 0
        self.total = 0
        self.silent_timeouts = 0
        self.gaps: list[float] = []
        self.error: str | None = None
        self.first_message_s: float | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "connected": self.connected,
            "auth_ok": self.auth_ok,
            "telemetry_frames": self.telemetry,
            "system_frames": self.system,
            "heartbeats": self.ping,
            "messages": self.total,
            "silent_timeouts": self.silent_timeouts,
            "first_message_s": (
                round(self.first_message_s, 3)
                if self.first_message_s is not None
                else None
            ),
            "max_gap_s": round(max(self.gaps), 3) if self.gaps else None,
            "p95_gap_s": (
                round(statistics.quantiles(self.gaps, n=20)[18], 3)
                if len(self.gaps) >= 20
                else (round(max(self.gaps), 3) if self.gaps else None)
            ),
            "error": self.error,
        }


async def run_client(
    ws_url: str,
    token: str,
    name: str,
    seconds: float,
    connect_delay: float = 0.0,
) -> ClientResult:
    result = ClientResult(name)
    if connect_delay:
        await asyncio.sleep(connect_delay)
    started = time.monotonic()
    try:
        async with websockets.connect(
            ws_url,
            open_timeout=8,
            close_timeout=3,
            # Disable library pings: this harness measures the SERVER heartbeat.
            ping_interval=None,
            max_queue=64,
        ) as ws:
            result.connected = True
            await ws.send(json.dumps({"type": "auth", "token": token}))
            deadline = started + seconds
            last_message: float | None = None
            while time.monotonic() < deadline:
                remaining = max(0.05, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(2.0, remaining))
                except asyncio.TimeoutError:
                    result.silent_timeouts += 1
                    continue
                now = time.monotonic()
                if result.first_message_s is None:
                    result.first_message_s = now - started
                if last_message is not None:
                    result.gaps.append(now - last_message)
                last_message = now
                result.total += 1
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    result.error = "non-JSON frame"
                    continue
                kind = message.get("type")
                if kind == "auth_ok":
                    result.auth_ok = True
                elif kind == "telemetry":
                    result.telemetry += 1
                elif kind == "system":
                    result.system += 1
                elif kind == "ping":
                    result.ping += 1
                    # Answer exactly like the dashboard, so the server sees a
                    # live client while the harness measures delivery.
                    await ws.send(json.dumps({"type": "pong"}))
    except Exception as exc:  # noqa: BLE001 - report, never crash the harness
        result.error = f"{type(exc).__name__}: {exc}"
    return result


async def churn(
    ws_url: str,
    token: str,
    rounds: int,
    delay_s: float,
) -> dict:
    """Browser-refresh pattern: connect, auth, receive one frame, close."""
    accepted = 0
    errors: list[str] = []
    for i in range(rounds):
        try:
            async with websockets.connect(
                ws_url, open_timeout=8, close_timeout=3, ping_interval=None
            ) as ws:
                await ws.send(json.dumps({"type": "auth", "token": token}))
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                message = json.loads(raw)
                if message.get("type") == "auth_ok":
                    accepted += 1
                else:
                    errors.append(f"round {i}: first frame {message.get('type')}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"round {i}: {type(exc).__name__}: {exc}")
        await asyncio.sleep(delay_s)
    return {"rounds": rounds, "accepted": accepted, "errors": errors}


async def main_async(args: argparse.Namespace) -> int:
    token = args.token or ""
    if not token:
        try:
            with open(args.token_file) as handle:
                token = handle.read().strip()
        except OSError as exc:
            print(f"cannot read token file {args.token_file}: {exc}", file=sys.stderr)
            return 2
    if not token:
        print("no token available", file=sys.stderr)
        return 2

    base = args.url.rstrip("/")
    ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    diag_url = base + "/api/diagnostics"

    try:
        before = http_json(diag_url, token)
        weather_before = http_json(base + "/api/weather", token)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"backend not reachable at {base}: {exc}", file=sys.stderr)
        return 2

    print(f"target        : {base}")
    print(f"clients       : {args.clients} steady + {args.churn} refresh rounds")
    print(f"duration      : {args.seconds}s")
    print(f"before        : {json.dumps(before, sort_keys=True)}")
    print(f"weather       : status={weather_before.get('status')} "
          f"live={weather_before.get('is_live')} "
          f"age={weather_before.get('age_seconds')}")

    tasks = []
    for i in range(args.clients):
        tasks.append(
            run_client(
                ws_url,
                token,
                f"steady-{i + 1}",
                args.seconds,
                # Stagger the connections by 50 ms so the server sees a
                # realistic multi-device arrival pattern, not one instant.
                connect_delay=i * 0.05,
            )
        )
    churn_task = None
    if args.churn:
        # Start churn after the steady clients have authenticated.
        async def delayed_churn():
            await asyncio.sleep(1.0)
            return await churn(ws_url, token, args.churn, args.churn_delay)

        churn_task = asyncio.create_task(delayed_churn())

    results = await asyncio.gather(*tasks)
    churn_result = await churn_task if churn_task is not None else None

    # Give the server a moment to process the close frames.
    await asyncio.sleep(1.0)
    after = http_json(diag_url, token)

    payload = {
        "target": base,
        "clients_requested": args.clients,
        "duration_s": args.seconds,
        "clients": [r.as_dict() for r in results],
        "churn": churn_result,
        "diagnostics_before": before,
        "diagnostics_after": after,
        "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    failures: list[str] = []
    for result in results:
        if not result.connected:
            failures.append(f"{result.name}: did not connect")
            continue
        if result.error:
            failures.append(f"{result.name}: {result.error}")
        if not result.auth_ok:
            failures.append(f"{result.name}: no auth_ok")
        if result.telemetry < 1:
            failures.append(f"{result.name}: no initial telemetry")
        if result.system < 1:
            failures.append(f"{result.name}: no system frame")
        if args.seconds >= 12 and result.ping < 1:
            failures.append(f"{result.name}: no heartbeat in {args.seconds}s")
        if args.seconds >= 20 and result.silent_timeouts > args.seconds / 2:
            failures.append(f"{result.name}: link silent for most of the run")
    if churn_result is not None:
        if churn_result["accepted"] != churn_result["rounds"]:
            failures.append(
                f"refresh churn: {churn_result['accepted']}/{churn_result['rounds']} accepted"
            )
    if after.get("active_clients") != 0:
        failures.append(f"server still has {after.get('active_clients')} clients after close")
    if after.get("dropped_slow_total", 0) != before.get("dropped_slow_total", 0):
        failures.append("healthy clients triggered slow-client drops")

    payload["failures"] = failures
    payload["ok"] = not failures

    if args.json_out:
        with open(args.json_out, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        print(f"json written  : {args.json_out}")

    print("")
    print(f"{'client':<10} {'msgs':>6} {'sys':>5} {'ping':>5} {'auth':>5} "
          f"{'max_gap':>8} {'p95_gap':>8} {'err':>4}")
    for result in results:
        item = result.as_dict()
        print(
            f"{item['name']:<10} {item['messages']:>6} {item['system_frames']:>5} "
            f"{item['heartbeats']:>5} {str(item['auth_ok']):>5} "
            f"{str(item['max_gap_s']):>8} {str(item['p95_gap_s']):>8} "
            f"{str(bool(item['error'])):>4}"
        )
    if churn_result is not None:
        print(f"refresh churn : {churn_result['accepted']}/{churn_result['rounds']} "
              f"accepted, {len(churn_result['errors'])} errors")
    print("")
    print(f"after         : {json.dumps(after, sort_keys=True)}")
    print("")
    if failures:
        print("RESULT: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("RESULT: PASS")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1", help="backend base URL")
    parser.add_argument("--token", default="", help="operator token (avoid: visible in ps)")
    parser.add_argument(
        "--token-file",
        default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".aeris_auth_token"),
        help="file containing the operator token",
    )
    parser.add_argument("--clients", type=int, default=3, help="steady clients")
    parser.add_argument("--seconds", type=float, default=30.0, help="steady run duration")
    parser.add_argument("--churn", type=int, default=0, help="refresh rounds")
    parser.add_argument("--churn-delay", type=float, default=0.5, help="seconds between refreshes")
    parser.add_argument("--json-out", default="", help="write the full result as JSON")
    return parser.parse_args(argv)


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
