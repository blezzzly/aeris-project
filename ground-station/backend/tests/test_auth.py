"""P1-5 - Authentication of the thermal feed + the unchanged API surface.

The dashboard shell, the static assets and /health stay public (LAN only).
Everything that carries data - /api/* and /thermal_feed - needs a bearer
token. The thermal feed streams live imagery, so it used to be readable by
ANY client on the local network; that hole is closed here.

The tests drive the real ASGI application through the minimal harness in
asgi_harness.py - no httpx, no TestClient.
"""
import unittest

# The test bootstrap must run BEFORE the backend is imported.
import _bootstrap  # noqa: F401

import main
from asgi_harness import ASGIClient
from _bootstrap import TEST_OPERATOR_TOKEN, TEST_VIEWER_TOKEN


class ThermalFeedAuthTests(unittest.TestCase):
    """A1 - A6"""

    def setUp(self):
        self.client = ASGIClient(main.app, client_host="127.0.0.1")
        # A tiny valid JPEG so the MJPEG generator produces its first chunk
        # immediately instead of waiting for a real frame.
        main._thermal_jpeg = b"\xff\xd8\xff\xd9"

    def tearDown(self):
        main._thermal_jpeg = None

    # ── A1: no credential -> 401 ──────────────────────────────────────────
    def test_a1_thermal_feed_requires_a_credential(self):
        response = self.client.request("GET", "/thermal_feed", max_chunks=1)
        self.assertEqual(
            response.status, 401,
            "the thermal feed must not be readable without a credential",
        )
        self.assertNotIn(
            b"\xff\xd8", response.body,
            "no JPEG image data may be returned without a credential",
        )
        self.assertNotIn(b"Content-Type: image/jpeg", response.body)

    def test_a1b_invalid_credential_is_rejected(self):
        response = self.client.request(
            "GET", "/thermal_feed",
            headers={"Authorization": "Bearer not-the-right-token"},
            max_chunks=1,
        )
        self.assertEqual(response.status, 401)

    # ── A2: a valid operator token -> 200 + the MJPEG stream ─────────────
    def test_a2_thermal_feed_serves_the_operator_token(self):
        response = self.client.request(
            "GET", "/thermal_feed",
            headers={"Authorization": f"Bearer {TEST_OPERATOR_TOKEN}"},
            max_chunks=1,
        )
        self.assertEqual(response.status, 200)
        self.assertIn(
            "multipart/x-mixed-replace",
            (response.header("content-type") or ""),
            "the MJPEG stream must still be served",
        )
        self.assertIn(b"Content-Type: image/jpeg", response.body)

    # ── A3: a viewer token is enough for a read-only stream ──────────────
    def test_a3_thermal_feed_accepts_the_viewer_token(self):
        response = self.client.request(
            "GET", "/thermal_feed",
            headers={"Authorization": f"Bearer {TEST_VIEWER_TOKEN}"},
            max_chunks=1,
        )
        self.assertEqual(response.status, 200,
                         "a viewer token must be enough for the read-only feed")

    # ── A4: the public surface stays public ──────────────────────────────
    def test_a4_dashboard_shell_and_health_stay_public(self):
        for path in ("/", "/health"):
            response = self.client.request("GET", path)
            self.assertEqual(response.status, 200,
                             f"{path} must stay publicly readable on the LAN")

    def test_a4b_static_assets_stay_public(self):
        response = self.client.request("GET", "/assets/index-Cas6iiYi.css")
        self.assertIn(response.status, (200, 404),
                      "static assets must not require a credential")

    # ── A5: the /api/* surface keeps its behaviour ───────────────────────
    def test_a5_api_requires_a_credential(self):
        response = self.client.request("GET", "/api/system")
        self.assertEqual(response.status, 401)

    # ── A6: a viewer token stays read-only ───────────────────────────────
    def test_a6_viewer_token_cannot_write(self):
        response = self.client.request(
            "POST", "/api/telemetry",
            headers={
                "Authorization": f"Bearer {TEST_VIEWER_TOKEN}",
                "Content-Type": "application/json",
            },
            body={"altitude": 100},
        )
        self.assertEqual(response.status, 403,
                         "a write with a viewer token must be refused")

    def test_a6b_operator_token_can_write(self):
        response = self.client.request(
            "POST", "/api/telemetry",
            headers={
                "Authorization": f"Bearer {TEST_OPERATOR_TOKEN}",
                "Content-Type": "application/json",
            },
            body={"altitude": 100, "speed": 5},
        )
        self.assertEqual(response.status, 200,
                         "the operator token must still be able to write")
        payload = response.json()
        self.assertEqual(payload["telemetry"]["altitude"], 100)

    # ── W6: the unchanged API surface ────────────────────────────────────
    def test_w6_authenticated_api_calls_work(self):
        for path in ("/api/system", "/api/hardware_status", "/api/sensors",
                     "/api/thermal/status"):
            response = self.client.request(
                "GET", path,
                headers={"Authorization": f"Bearer {TEST_OPERATOR_TOKEN}"},
            )
            self.assertEqual(response.status, 200, f"{path} must answer 200")

    def test_w6b_non_lan_clients_are_refused(self):
        client = ASGIClient(main.app, client_host="203.0.113.9")
        response = client.request("GET", "/api/system")
        self.assertEqual(response.status, 403,
                         "a client outside the LAN must be refused")

    def test_w6c_the_real_token_file_is_never_touched(self):
        """The suite must use its own token, never the production one."""
        import os
        from _bootstrap import BACKEND_DIR
        token_file = os.path.join(BACKEND_DIR, ".aeris_auth_token")
        self.assertTrue(
            os.environ.get("AERIS_AUTH_TOKEN"),
            "the suite must export its own AERIS_AUTH_TOKEN",
        )
        self.assertNotEqual(
            os.environ["AERIS_AUTH_TOKEN"], TEST_VIEWER_TOKEN,
            "the operator and viewer test tokens must differ",
        )
        # The module must have loaded the TEST token, not the file.
        self.assertEqual(main._OPERATOR_TOKEN, TEST_OPERATOR_TOKEN)


if __name__ == "__main__":
    unittest.main()
