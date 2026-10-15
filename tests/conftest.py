"""Shared fixtures. A tiny fake MCP server (threaded http.server) so the client and the
outside checks can be tested with no database and no network beyond 127.0.0.1."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class FakeMcp:
    """Scripted MCP endpoint.

    mode: "open"   - answers every request, lists `tools`
          "401"    - refuses everything with 401
          "scoped" - needs Authorization: Bearer <token>; lists `tools`; tools/call on names
                     in `refuse` answers a JSON-RPC error 'insufficient scope', others succeed
    sse: answer with text/event-stream bodies instead of JSON
    """

    def __init__(self, mode="open", tools=("a_tool", "b_tool"), refuse=(), sse=False, token="good"):
        self.mode, self.tools, self.refuse, self.sse, self.token = mode, list(tools), set(refuse), sse, token
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _send(self, status, body=None):
                if body is None:
                    self.send_response(status)
                    self.end_headers()
                    return
                if fake.sse:
                    data = f"event: message\ndata: {json.dumps(body)}\n\n".encode()
                    ctype = "text/event-stream"
                else:
                    data = json.dumps(body).encode()
                    ctype = "application/json"
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Mcp-Session-Id", "sess-1")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.startswith("/.well-known/oauth-protected-resource"):
                    body = json.dumps({"resource": "x", "scopes_supported": fake.scopes}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self._send(404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                req["_auth"] = self.headers.get("Authorization")
                req["_session"] = self.headers.get("Mcp-Session-Id")
                fake.requests.append(req)
                if fake.mode == "401":
                    return self._send(401)
                if fake.mode == "scoped" and req["_auth"] != f"Bearer {fake.token}":
                    return self._send(401)
                rid = req.get("id")
                if rid is None:                       # notification
                    return self._send(202)
                m = req.get("method")
                if m == "initialize":
                    return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
                        "protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "fake"}}})
                if m == "tools/list":
                    return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
                        "tools": [{"name": t, "inputSchema": {}} for t in fake.tools]}})
                if m == "tools/call":
                    name = req["params"]["name"]
                    if name in fake.refuse:
                        if fake.mode == "scoped" and fake.refuse_with_403:
                            return self._send(403)
                        return self._send(200, {"jsonrpc": "2.0", "id": rid,
                                                "error": {"code": -32000, "message": "insufficient scope"}})
                    return self._send(200, {"jsonrpc": "2.0", "id": rid, "result": {
                        "content": [{"type": "text", "text": "[]"}], "isError": False}})
                return self._send(200, {"jsonrpc": "2.0", "id": rid,
                                        "error": {"code": -32601, "message": "method not found"}})

        self.scopes: list[str] = []
        self.refuse_with_403 = False
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def start(self):
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._srv.server_address[1]}/mcp"

    def stop(self):
        self._srv.shutdown()
        self._srv.server_close()


@pytest.fixture
def fake_server():
    started: list[FakeMcp] = []

    def make(**kw) -> FakeMcp:
        f = FakeMcp(**kw).start()
        started.append(f)
        return f

    yield make
    for f in started:
        f.stop()
