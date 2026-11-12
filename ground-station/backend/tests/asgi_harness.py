"""A minimal ASGI HTTP client built on the standard library.

``httpx`` and ``requests`` are not installed on the target device (and the
device has to stay installable offline), and Starlette's ``TestClient``
depends on ``httpx``. This module therefore drives the ASGI application
directly: it builds a scope, feeds a single request body into the app and
collects the response messages.

Only what the AERIS tests need is implemented:

  * GET / POST / HEAD with a JSON or raw body,
  * arbitrary request headers (the bearer token),
  * a configurable client host (the middleware checks whether the caller is
    on the LAN),
  * a bounded read of a streaming response, so an endless MJPEG generator
    cannot block the test.
"""
import asyncio
import json


class Response:
    """The collected result of one ASGI request."""

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    def header(self, name):
        name = name.lower().encode()
        for key, value in self.headers:
            if key == name:
                return value.decode()
        return None

    def json(self):
        return json.loads(self.body.decode())

    def __repr__(self):
        return f"<Response {self.status} {len(self.body)}B>"


class ASGIClient:
    """Drive an ASGI application without any third-party dependency."""

    def __init__(self, app, client_host="127.0.0.1", server=("127.0.0.1", 80)):
        self.app = app
        self.client_host = client_host
        self.server = server

    def request(self, method, path, headers=None, body=b"", max_chunks=64,
                timeout=15.0):
        """Perform one request and return a Response.

        ``max_chunks`` bounds how many body chunks are collected, so an
        endless streaming response (the MJPEG feed) can be inspected without
        draining it.
        """
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()

        raw_headers = []
        for key, value in (headers or {}).items():
            raw_headers.append((key.lower().encode(), str(value).encode()))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": raw_headers,
            "client": (self.client_host, 54321),
            "server": self.server,
        }

        state = {"sent": False}
        status = {"code": 500}
        response_headers = []
        chunks = []

        async def receive():
            if not state["sent"]:
                state["sent"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            # Keep the app alive until it decides to stop.
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                response_headers.extend(message.get("headers", []))
            elif message["type"] == "http.response.body":
                if len(chunks) < max_chunks:
                    chunks.append(message.get("body", b""))
                else:
                    # Stop the generator: further chunks are dropped.
                    raise _StopStreaming()

        async def run():
            try:
                await self.app(scope, receive, send)
            except _StopStreaming:
                pass

        try:
            asyncio.run(asyncio.wait_for(run(), timeout=timeout))
        except asyncio.TimeoutError:
            return Response(status["code"], response_headers, b"".join(chunks))

        return Response(status["code"], response_headers, b"".join(chunks))


class _StopStreaming(Exception):
    """Internal signal used to stop an endless streaming response."""
