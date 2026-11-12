"""P1-2 / P2-1 - WebSocket fan-out reliability.

FastAPI serves every route AND every WebSocket on ONE asyncio event loop, so
the fan-out has to guarantee:

  * one failing client never interrupts the others,
  * one stalled client never blocks the event loop,
  * a closed client is removed, closed, and never left behind,
  * exactly one writer task owns each socket (no concurrent sends),
  * a slow client's backlog never delays a healthy client,
  * queue overflow drops thermal frames before critical messages.

The tests drive the real ConnectionManager with fake clients. Delivery is
asynchronous by design (one writer task per client), so the tests await the
writer instead of asserting immediately after ``broadcast()``.
"""
import asyncio
import unittest

# The test bootstrap must run BEFORE the backend is imported.
import _bootstrap  # noqa: F401

import main
from fakes import FakeWebSocket

TEST_SEND_TIMEOUT_S = 0.25


async def wait_until(predicate, timeout=2.0, interval=0.005):
    """Wait for an asynchronous writer-task condition to become true."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


class WebSocketFanOutTests(unittest.IsolatedAsyncioTestCase):
    """W1 - W12"""

    def setUp(self):
        self._orig_timeout = main.WS_SEND_TIMEOUT_S
        # A short timeout keeps the stalled-client tests fast. The production
        # values are asserted separately.
        self.manager = main.ConnectionManager(
            queue_max=8, send_timeout=TEST_SEND_TIMEOUT_S
        )

    async def asyncTearDown(self):
        # Close every socket still registered so no writer task leaks into
        # the next test (unittest would otherwise warn about pending tasks).
        for session in list(self.manager._sessions.values()):
            try:
                await session.close(code=1000, reason="test teardown")
            except Exception:
                pass

    def test_production_limits_are_sane(self):
        self.assertEqual(
            self._orig_timeout, 2.0,
            "the production send timeout must stay at 2 s",
        )
        self.assertEqual(main.WS_QUEUE_MAX, 32,
                         "the production per-client queue must stay bounded")
        self.assertEqual(main.WS_HEARTBEAT_S, 10.0,
                         "the server heartbeat must stay at 10 s")

    # ── W1: one failing client does not interrupt the others ──────────────
    async def test_w1_failing_client_does_not_interrupt_the_others(self):
        good_a = FakeWebSocket("a")
        broken = FakeWebSocket("broken", fail=True)
        good_b = FakeWebSocket("b")
        for ws in (good_a, broken, good_b):
            await self.manager.connect(ws)

        await self.manager.broadcast({"type": "system", "n": 1})

        received = await wait_until(
            lambda: len(good_a.received) == 1 and len(good_b.received) == 1
        )
        self.assertTrue(received, "healthy clients must receive")
        dropped = await wait_until(
            lambda: broken not in self.manager.active_connections
        )
        self.assertTrue(dropped, "the broken client must be dropped from the list")

    # ── W2: a client leaving during delivery must not abort it ────────────
    async def test_w2_disconnect_during_broadcast_does_not_abort(self):
        class Disconnecting(FakeWebSocket):
            """Removes itself from the fan-out list while the writer sends."""

            def __init__(self, manager):
                super().__init__("leaving")
                self._manager = manager

            async def send_json(self, message):
                self._manager.disconnect(self)
                self.received.append(message)

        survivor = FakeWebSocket("survivor")
        leaver = Disconnecting(self.manager)
        await self.manager.connect(survivor)
        await self.manager.connect(leaver)

        await self.manager.broadcast({"type": "system", "n": 2})

        delivered = await wait_until(lambda: len(survivor.received) == 1)
        self.assertTrue(delivered, "the remaining client must still receive")
        self.assertEqual(len(survivor.received), 1)

    # ── W3: a stalled client is bounded, closed, and never blocks ─────────
    async def test_w3_stalled_client_is_bounded_and_closed(self):
        stalled = FakeWebSocket("stalled", stall=True)
        healthy = FakeWebSocket("healthy")
        await self.manager.connect(stalled)
        await self.manager.connect(healthy)

        loop = asyncio.get_running_loop()
        start = loop.time()
        await self.manager.broadcast({"type": "system", "n": 3})
        elapsed = loop.time() - start

        # broadcast() only enqueues; it must never wait for the network.
        self.assertLess(elapsed, 0.1,
                        "broadcast() must not await a network send")
        delivered = await wait_until(lambda: len(healthy.received) == 1)
        self.assertTrue(delivered, "the healthy client must still receive")
        closed = await wait_until(lambda: stalled.closed)
        self.assertTrue(closed, "the stalled client must be closed")
        self.assertEqual(stalled.close_code, 1013,
                         "a slow client is closed with 1013 (try again later)")
        self.assertNotIn(stalled, self.manager.active_connections,
                         "the stalled client must be removed")

    # ── W4: a non-finite value must not kill the broadcast ────────────────
    async def test_w4_non_finite_values_do_not_abort_the_broadcast(self):
        client = FakeWebSocket("nan")
        await self.manager.connect(client)

        payload = {
            "type": "thermal_frame",
            "matrix": [1.0, float("nan"), float("inf"), -float("inf"), 2.5],
            "meta": {"value": float("nan")},
        }
        await self.manager.broadcast(payload)

        delivered = await wait_until(lambda: len(client.received) == 1)
        self.assertTrue(delivered, "the broadcast must be delivered")
        sent = client.received[0]
        matrix = sent["matrix"]
        self.assertEqual(matrix[0], 1.0)
        self.assertIsNone(matrix[1], "NaN must be transported as null")
        self.assertIsNone(matrix[2], "Inf must be transported as null")
        self.assertIsNone(matrix[3], "-Inf must be transported as null")
        self.assertEqual(matrix[4], 2.5)
        self.assertIsNone(sent["meta"]["value"])

    def test_w4b_json_safe_helper_behaviour(self):
        self.assertIsNone(main._json_safe(float("nan")))
        self.assertIsNone(main._json_safe(float("inf")))
        self.assertEqual(main._json_safe(3.5), 3.5)
        self.assertEqual(main._json_safe("text"), "text")
        self.assertIs(main._json_safe(True), True)
        self.assertIsNone(main._json_safe(None))
        self.assertEqual(main._json_safe([1, float("nan")]), [1, None])

    # ── W5: a closed client is never left in the list ─────────────────────
    async def test_w5_disconnect_removes_the_client(self):
        client = FakeWebSocket("gone")
        await self.manager.connect(client)
        self.assertIn(client, self.manager.active_connections)
        self.manager.disconnect(client)
        self.assertNotIn(client, self.manager.active_connections)
        self.assertEqual(len(self.manager.active_connections), 0)

    async def test_w5b_broadcast_over_an_empty_list_is_harmless(self):
        await self.manager.broadcast({"type": "system"})   # must not raise

    # ── W6: many stalled clients never delay a healthy client ─────────────
    async def test_w6_many_stalled_clients_do_not_delay_healthy_client(self):
        healthy = FakeWebSocket("healthy")
        await self.manager.connect(healthy)
        stalled = [FakeWebSocket(f"stalled-{i}", stall=True) for i in range(8)]
        for ws in stalled:
            await self.manager.connect(ws)

        loop = asyncio.get_running_loop()
        start = loop.time()
        await self.manager.broadcast({"type": "system", "n": 6})
        enqueue_elapsed = loop.time() - start

        self.assertLess(
            enqueue_elapsed, 0.1,
            "enqueueing for 8 stalled clients must not block the broadcaster",
        )
        delivered = await wait_until(lambda: len(healthy.received) == 1)
        self.assertTrue(delivered, "the healthy client must receive promptly")
        closed = await wait_until(
            lambda: all(ws.closed for ws in stalled), timeout=3.0
        )
        self.assertTrue(closed, "every stalled client must be closed")
        self.assertEqual(self.manager.dropped_slow_total, 0,
                         "send timeouts are not queue-overflow drops")

    # ── W7: a dropped client receives a close frame (browser reconnects) ──
    async def test_w7_dropped_client_receives_close_frame(self):
        stalled = FakeWebSocket("stalled", stall=True)
        await self.manager.connect(stalled)
        await self.manager.broadcast({"type": "system", "n": 7})

        closed = await wait_until(lambda: stalled.closed)
        self.assertTrue(closed, "dropped clients must be closed, not just removed")
        self.assertEqual(stalled.close_code, 1013)
        self.assertNotIn(stalled, self.manager.active_connections)
        self.assertGreaterEqual(self.manager.closed_total, 1)
        self.assertEqual(self.manager.diagnostics()["closed_by_code"].get("1013"), 1)
        self.assertIn("send timeout", self.manager.last_close_reason or "")

    # ── W8: no two coroutines may write the same socket concurrently ──────
    async def test_w8_single_writer_per_socket(self):
        class ConcurrencyProbe(FakeWebSocket):
            def __init__(self):
                super().__init__("probe")
                self.in_flight = 0
                self.max_in_flight = 0

            async def send_json(self, message):
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
                try:
                    await asyncio.sleep(0.01)
                    self.received.append(message)
                finally:
                    self.in_flight -= 1

        probe = ConcurrencyProbe()
        await self.manager.connect(probe)

        # Several broadcasts in flight at the same time, exactly like the
        # overlapping thermal/system broadcasts in production. Stay below
        # the per-client queue bound so this test probes concurrency only.
        await asyncio.gather(*(
            self.manager.broadcast({"type": "system", "n": i})
            for i in range(6)
        ))

        delivered = await wait_until(lambda: len(probe.received) == 6)
        self.assertTrue(delivered, "all six broadcasts must be delivered")
        self.assertEqual(
            probe.max_in_flight, 1,
            "exactly one writer task may own a socket at a time",
        )

    # ── W9: thermal frames coalesce instead of growing a backlog ──────────
    async def test_w9_thermal_frames_are_coalesced(self):
        class SlowReader(FakeWebSocket):
            async def send_json(self, message):
                await asyncio.sleep(0.2)
                self.received.append(message)

        manager = main.ConnectionManager(queue_max=2, send_timeout=0.1)
        slow = SlowReader("slow")
        await manager.connect(slow)
        try:
            # No await points inside broadcast(), so all five frames are
            # enqueued before the writer can drain the queue. A queue of two
            # therefore coalesces three of them.
            for i in range(5):
                await manager.broadcast({"type": "thermal_frame", "n": i})

            self.assertGreaterEqual(
                manager.coalesced_frames_total, 1,
                "superseded thermal frames must be coalesced, not queued",
            )
            self.assertLess(
                manager.broadcast_enqueue_ms_max, 50.0,
                "queue overflow must never block the broadcaster",
            )
            self.assertEqual(manager.dropped_slow_total, 0,
                             "a coalesced frame must not close the client")
        finally:
            for session in list(manager._sessions.values()):
                await session.close(code=1000, reason="test teardown")

    # ── W10: a critical message that cannot be queued closes the client ───
    async def test_w10_critical_queue_overflow_closes_slow_client(self):
        class SlowReader(FakeWebSocket):
            async def send_json(self, message):
                await asyncio.sleep(0.3)
                self.received.append(message)

        manager = main.ConnectionManager(queue_max=1, send_timeout=0.1)
        slow = SlowReader("slow")
        await manager.connect(slow)
        try:
            await manager.broadcast({"type": "system", "n": 1})
            await manager.broadcast({"type": "system", "n": 2})

            self.assertEqual(manager.dropped_slow_total, 1)
            closed = await wait_until(lambda: slow.closed)
            self.assertTrue(closed, "the slow client must be closed")
            self.assertEqual(slow.close_code, 1013)
        finally:
            for session in list(manager._sessions.values()):
                await session.close(code=1000, reason="test teardown")

    # ── W11: the server heartbeat reaches every client ────────────────────
    async def test_w11_heartbeat_emits_ping(self):
        client = FakeWebSocket("heartbeat")
        await self.manager.connect(client)

        orig_manager = main.manager
        orig_interval = main.WS_HEARTBEAT_S
        main.manager = self.manager
        main.WS_HEARTBEAT_S = 0.05
        task = asyncio.create_task(main._heartbeat_loop())
        try:
            received = await wait_until(
                lambda: any(m.get("type") == "ping" for m in client.received)
            )
            self.assertTrue(received, "the heartbeat must emit a ping frame")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            main.manager = orig_manager
            main.WS_HEARTBEAT_S = orig_interval

    # ── W12: diagnostics are present and secret-free ──────────────────────
    async def test_w12_diagnostics_report_hub_counters(self):
        a = FakeWebSocket("a")
        b = FakeWebSocket("b")
        await self.manager.connect(a)
        await self.manager.connect(b)
        await self.manager.broadcast({"type": "system", "n": 12})
        delivered = await wait_until(
            lambda: len(a.received) == 1 and len(b.received) == 1
        )
        self.assertTrue(delivered)

        diag = self.manager.diagnostics()
        self.assertEqual(diag["active_clients"], 2)
        self.assertEqual(len(diag["active_client_detail"]), 2)
        self.assertEqual(diag["accepted_total"], 2)
        self.assertEqual(diag["broadcast_calls_total"], 1)
        self.assertEqual(diag["messages_sent_total"], 2)
        self.assertEqual(diag["queue_max"], 8)
        self.assertEqual(diag["closed_by_code"], {})
        for detail in diag["active_client_detail"]:
            self.assertGreaterEqual(detail["age_s"], 0)
            self.assertGreaterEqual(detail["messages_sent"], 1)
        self.assertNotIn("token", str(diag).lower())
        self.assertNotIn("pass", str(diag).lower())


if __name__ == "__main__":
    unittest.main()
