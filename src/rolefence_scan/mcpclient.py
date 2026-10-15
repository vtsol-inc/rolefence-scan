"""A small MCP client over Streamable HTTP, written by hand so the scanner can see raw
status codes (401, 403) and bodies instead of a library's exception. Read-only: it only ever
sends initialize, tools/list and tools/call."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

PROTOCOL = "2025-03-26"
TIMEOUT = 15.0


@dataclass
class McpResponse:
    status: int
    body: Any = None          # parsed JSON-RPC body if there was one
    text: str = ""
    headers: dict = field(default_factory=dict)

    @property
    def result(self) -> Any:
        return self.body.get("result") if isinstance(self.body, dict) else None

    @property
    def error(self) -> dict | None:
        return self.body.get("error") if isinstance(self.body, dict) else None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None and self.result is not None


def _parse_body(resp: httpx.Response) -> Any:
    ctype = resp.headers.get("content-type", "")
    text = resp.text
    if "text/event-stream" in ctype:
        # take the last JSON "data:" line
        last = None
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    last = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    pass
        return last
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return None


class McpClient:
    def __init__(self, url: str, token: str | None = None, timeout: float = TIMEOUT):
        self.url = url
        self.token = token
        self.session_id: str | None = None
        self._id = 0
        self._http = httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def rpc(self, method: str, params: dict | None = None) -> McpResponse:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        try:
            r = self._http.post(self.url, headers=self._headers(), content=json.dumps(payload))
        except httpx.HTTPError as e:
            return McpResponse(status=0, text=str(e))
        sid = r.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        return McpResponse(status=r.status_code, body=_parse_body(r), text=r.text[:2000],
                           headers=dict(r.headers))

    def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        try:
            self._http.post(self.url, headers=self._headers(), content=json.dumps(payload))
        except httpx.HTTPError:
            pass

    def initialize(self) -> McpResponse:
        r = self.rpc("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "rolefence-scan", "version": "0.1.0"},
        })
        if r.ok:
            self.notify("notifications/initialized")
        return r

    def list_tools(self) -> McpResponse:
        return self.rpc("tools/list")

    def call_tool(self, name: str, arguments: dict | None = None) -> McpResponse:
        return self.rpc("tools/call", {"name": name, "arguments": arguments or {}})


def tool_names(resp: McpResponse) -> list[str]:
    res = resp.result or {}
    return [t.get("name", "?") for t in res.get("tools", [])]


def call_refused(resp: McpResponse) -> bool:
    """True when a tools/call was turned away for lack of permission (not a bad argument)."""
    if resp.status in (401, 403):
        return True
    err = resp.error
    if err:
        msg = str(err.get("message", "")).lower()
        if any(k in msg for k in ("unauthorized", "forbidden", "insufficient", "scope", "permission")):
            return True
    res = resp.result
    if isinstance(res, dict) and res.get("isError"):
        text = " ".join(c.get("text", "") for c in res.get("content", []) if isinstance(c, dict)).lower()
        if any(k in text for k in ("unauthorized", "forbidden", "insufficient", "not authorized")):
            return True
    return False


def call_unknown_tool(resp: McpResponse) -> bool:
    err = resp.error
    if err and "unknown tool" in str(err.get("message", "")).lower():
        return True
    return False


def fetch_json(url: str, token: str | None = None) -> tuple[int, Any]:
    h = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    try:
        r = httpx.get(url, headers=h, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError:
        return 0, None
    try:
        return r.status_code, r.json()
    except (json.JSONDecodeError, ValueError):
        return r.status_code, None
