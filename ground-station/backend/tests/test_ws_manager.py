"""P1-2 - WebSocket fan-out reliability.

FastAPI serves every route AND every WebSocket on ONE asyncio event loop, so
the fan-out has to guarantee three things:

  * one failing client never interrupts the others,
  * one stalled client never blocks the event loop,
  * a closed client is never left behind in the list.

The tests drive the real ConnectionManager with fake clients.
"""
import asyncio
import unittest

# The test bootstrap must run BEFORE the backend is imported.
import _bootstrap  # noqa: F401

import main
from fakes import FakeWebSocket


class WebSocketFanOutTests(unittest.IsolatedAsyncioTestCase):
    """W1 - W5"""

    def setUp(self):
        self.manager = main.ConnectionManager()
        self._orig_timeout = main.WS_SEND_TIMEOUT_S
        # A short timeout keeps the stalled-client test fast. The production
        # value (2 s) is asserted separately.
        main.WS_SEND_TIMEOUT_S = 0.25

    def tearDown(self):
        main.WS_SEND_TIMEOUT_S = self._orig_timeout

    def test_production_send_timeout_is_two_seconds(self):
        self.assertEqual(
            self._orig_timeout, 2.0,
            "the production send timeout must stay at 2 s",
        )

    # ── W1: one failing client does not interrupt the others ──────────────
    async def test_w1_failing_client_does_not_interrupt_the_others(self):
        good_a = FakeWebSocket("a")
        broken = FakeWebSocket("broken", fail=True)
        good_b = FakeWebSocket("b")
        for ws in (good_a, broken, good_b):
            await self.manager.connect(ws)

        await self.manager.broadcast({"type": "system", "n": 1})

        self.assertEqual(len(good_a.received), 1, "client a must receive")
        self.assertEqual(len(good_b.received), 1, "client b must receive")
        self.assertNotIn(broken, self.manager.active_connections,
                         "the broken client must be dropped from the list")

    # ── W2: a disconnect during a broadcast must not abort it ─────────────
    async def test_w2_disconnect_during_broadcast_does_not_abort(self):
        class Disconnecting(FakeWebSocket):
            """Removes itself from the fan-out list while the broadcast runs."""

            def __init__(self, manager):
                super().__init__("leaving")
                self._manager = manager

            async def send_json(self, message):
                # The disconnect happens exactly between the await points of
                # the broadcast loop.
                self._manager.disconnect(self)
                self.received.append(message)

        survivor = FakeWebSocket("survivor")
        leaver = Disconnecting(self.manager)
        await self.manager.connect(survivor)
        await self.manager.connect(leaver)

        # The old implementation iterated the live list and raised
        # "RuntimeError: list changed size during iteration".
        await self.manager.broadcast({"type": "system", "n": 2})

        self.assertEqual(len(survivor.received), 1,
                         "the remaining client must still receive the broadcast")

    # ── W3: a stalled client is dropped and never blocks the loop ─────────
    async def test_w3_stalled_client_is_bounded_and_dropped(self):
        stalled = FakeWebSocket("stalled", stall=True)
        healthy = FakeWebSocket("healthy")
        await self.manager.connect(stalled)
        await self.manager.connect(healthy)

        loop = asyncio.get_running_loop()
        start = loop.time()
        await self.manager.broadcast({"type": "system", "n": 3})
        elapsed = loop.time() - start

        self.assertLess(elapsed, 2.0,
                        "the broadcast must be bounded by the send timeout")
        self.assertEqual(len(healthy.received), 1,
                         "the healthy client must still receive")
        self.assertNotIn(stalled, self.manager.active_connections,
                         "the stalled client must be dropped")

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

        self.assertEqual(len(client.received), 1, "the broadcast must be delivered")
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


if __name__ == "__main__":
    unittest.main()
