#!/usr/bin/env python3
"""AERIS Phase 2 - long-run monitor.

Polls the LIVE ground station over HTTP (never touches hardware directly) and
records the evidence required for the acceptance report:

  * /health availability and latency,
  * hub diagnostics (active clients, accepted/closed, slow drops, queue depth),
  * per-client session ages (proves clients stayed connected),
  * backend PID / NRestarts (proves no restart),
  * AP power-save state, associated station count,
  * CPU temperature and load average.

The token is read from a file and never printed. Output is JSONL plus a CSV
summary-friendly table, and a final summary.json.

Usage (root, so it can read the operator token):
    sudo ./venv/bin/python tests/phase2_monitor.py \
        --token-file .aeris_auth_token --seconds 7200 --interval 60 \
        --out-dir /home/piaeris/aeris-backups/phase2-validation-...
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

STOP = False


def _stop(_signum, _frame):
    global STOP
    STOP = True


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path) as handle:
            return handle.read().strip()
    except OSError:
        return default


def systemctl_show(unit: str) -> dict:
    """Read a few systemd properties without requiring systemd-python."""
    try:
        result = subprocess.run(
            ["systemctl", "show", unit,
             "-p", "MainPID", "-p", "NRestarts", "-p", "ActiveEnterTimestampMonotonic"],
            capture_output=True, text=True, timeout=4, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    info: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            info[key] = value
    return info


def run_cmd(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=4, check=False)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def http_get(url: str, token: str, timeout: float = 5.0):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        status = exc.code
    latency_ms = (time.monotonic() - started) * 1000.0
    try:
        payload = json.loads(body) if body else None
    except json.JSONDecodeError:
        payload = None
    return status, latency_ms, payload


def wifi_interface() -> str:
    for entry in os.listdir("/sys/class/net"):
        if os.path.isdir(os.path.join("/sys/class/net", entry, "wireless")):
            return entry
    return ""


def sample(base: str, token: str, iface: str) -> dict:
    status, latency_ms, health = http_get(base + "/health", token)
    diag_status, diag_latency_ms, diag = http_get(base + "/api/diagnostics", token)
    service = systemctl_show("aeris-backend")
    power_save = run_cmd(["iw", "dev", iface, "get", "power_save"]) if iface else ""
    stations = run_cmd(["iw", "dev", iface, "station", "dump"]) if iface else ""
    station_count = stations.count("Station ") if stations else None
    temp_raw = read_text("/sys/class/thermal/thermal_zone0/temp")
    try:
        temp_c = round(int(temp_raw) / 1000.0, 2)
    except (TypeError, ValueError):
        temp_c = None
    try:
        load1 = round(float(read_text("/proc/loadavg").split()[0]), 2)
    except (IndexError, ValueError):
        load1 = None

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "health_http": status,
        "health_ok": bool(health and health.get("status") == "ok"),
        "health_latency_ms": round(latency_ms, 2),
        "diagnostics_http": diag_status,
        "diagnostics_latency_ms": round(diag_latency_ms, 2),
        "active_clients": (diag or {}).get("active_clients"),
        "active_client_detail": (diag or {}).get("active_client_detail"),
        "accepted_total": (diag or {}).get("accepted_total"),
        "closed_total": (diag or {}).get("closed_total"),
        "closed_by_code": (diag or {}).get("closed_by_code"),
        "dropped_slow_total": (diag or {}).get("dropped_slow_total"),
        "coalesced_frames_total": (diag or {}).get("coalesced_frames_total"),
        "messages_sent_total": (diag or {}).get("messages_sent_total"),
        "queue_depth_max": (diag or {}).get("queue_depth_max"),
        "broadcast_enqueue_ms_max": (diag or {}).get("broadcast_enqueue_ms_max"),
        "last_close_reason": (diag or {}).get("last_close_reason"),
        "last_close_at": (diag or {}).get("last_close_at"),
        "nrestarts": service.get("NRestarts"),
        "main_pid": service.get("MainPID"),
        "power_save": power_save.replace("Power save: ", "") if power_save else None,
        "associated_stations": station_count,
        "cpu_temp_c": temp_c,
        "load1": load1,
    }


def summarize(samples: list[dict]) -> dict:
    if not samples:
        return {"samples": 0}
    health_latencies = [
        s["health_latency_ms"] for s in samples if s.get("health_http") == 200
    ]
    diag_latencies = [
        s["diagnostics_latency_ms"] for s in samples if s.get("diagnostics_http") == 200
    ]

    def series(key):
        values = [s.get(key) for s in samples if isinstance(s.get(key), (int, float))]
        return values

    accepted = series("accepted_total")
    closed = series("closed_total")
    dropped = series("dropped_slow_total")
    clients = series("active_clients")
    ages = [
        detail["age_s"]
        for s in samples
        for detail in (s.get("active_client_detail") or [])
        if isinstance(detail.get("age_s"), (int, float))
    ]
    pids = sorted({str(s.get("main_pid")) for s in samples if s.get("main_pid")})
    restarts = sorted({str(s.get("nrestarts")) for s in samples if s.get("nrestarts") is not None})
    power_states = sorted({str(s.get("power_save")) for s in samples if s.get("power_save")})

    return {
        "samples": len(samples),
        "first_timestamp": samples[0]["timestamp"],
        "last_timestamp": samples[-1]["timestamp"],
        "health_failures": sum(1 for s in samples if not s.get("health_ok")),
        "diagnostics_failures": sum(1 for s in samples if s.get("diagnostics_http") != 200),
        "health_latency_avg_ms": round(sum(health_latencies) / len(health_latencies), 2)
        if health_latencies else None,
        "health_latency_max_ms": round(max(health_latencies), 2) if health_latencies else None,
        "diagnostics_latency_avg_ms": round(sum(diag_latencies) / len(diag_latencies), 2)
        if diag_latencies else None,
        "diagnostics_latency_max_ms": round(max(diag_latencies), 2) if diag_latencies else None,
        "accepted_total_delta": (accepted[-1] - accepted[0]) if len(accepted) >= 2 else None,
        "closed_total_delta": (closed[-1] - closed[0]) if len(closed) >= 2 else None,
        "dropped_slow_total_delta": (dropped[-1] - dropped[0]) if len(dropped) >= 2 else None,
        "active_clients_min": min(clients) if clients else None,
        "active_clients_max": max(clients) if clients else None,
        "longest_client_age_s": max(ages) if ages else None,
        "main_pids_seen": pids,
        "nrestarts_values_seen": restarts,
        "power_save_values_seen": power_states,
        "max_cpu_temp_c": max(series("cpu_temp_c")) if series("cpu_temp_c") else None,
        "max_load1": max(series("load1")) if series("load1") else None,
        "max_queue_depth": max(series("queue_depth_max")) if series("queue_depth_max") else None,
        "max_broadcast_enqueue_ms": max(series("broadcast_enqueue_ms_max"))
        if series("broadcast_enqueue_ms_max") else None,
        "last_closed_by_code": samples[-1].get("closed_by_code"),
        "last_close_reason": samples[-1].get("last_close_reason"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1")
    parser.add_argument(
        "--token-file",
        default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".aeris_auth_token"),
    )
    parser.add_argument("--seconds", type=float, default=7200.0)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    try:
        with open(args.token_file) as handle:
            token = handle.read().strip()
    except OSError as exc:
        print(f"cannot read token file {args.token_file}: {exc}", file=sys.stderr)
        return 2
    if not token:
        print("empty token file", file=sys.stderr)
        return 2

    os.makedirs(args.out_dir, exist_ok=True)
    jsonl_path = os.path.join(args.out_dir, "monitor.jsonl")
    csv_path = os.path.join(args.out_dir, "monitor.csv")
    summary_path = os.path.join(args.out_dir, "summary.json")
    iface = wifi_interface()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    columns = [
        "timestamp", "health_http", "health_latency_ms", "diagnostics_http",
        "active_clients", "accepted_total", "closed_total", "dropped_slow_total",
        "queue_depth_max", "nrestarts", "main_pid", "power_save",
        "associated_stations", "cpu_temp_c", "load1",
    ]
    samples: list[dict] = []
    started = time.monotonic()
    next_sample = started
    with open(jsonl_path, "w") as jsonl, open(csv_path, "w") as csv:
        csv.write(",".join(columns) + "\n")
        while not STOP:
            now = time.monotonic()
            if now - started >= args.seconds:
                break
            if now < next_sample:
                time.sleep(min(0.5, next_sample - now))
                continue
            item = sample(args.url.rstrip("/"), token, iface)
            samples.append(item)
            jsonl.write(json.dumps(item, sort_keys=True) + "\n")
            jsonl.flush()
            row = []
            for column in columns:
                value = item.get(column)
                if value is None:
                    row.append("")
                else:
                    row.append(str(value).replace(",", ";"))
            csv.write(",".join(row) + "\n")
            csv.flush()
            print(
                f"{item['timestamp']} health={item['health_http']} "
                f"{item['health_latency_ms']}ms clients={item['active_clients']} "
                f"power_save={item['power_save']} temp={item['cpu_temp_c']} "
                f"pid={item['main_pid']} restarts={item['nrestarts']}",
                flush=True,
            )
            next_sample = now + args.interval

    summary = summarize(samples)
    with open(summary_path, "w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
    print("")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"written: {jsonl_path}")
    print(f"written: {csv_path}")
    print(f"written: {summary_path}")
    return 0 if summary.get("samples", 0) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
