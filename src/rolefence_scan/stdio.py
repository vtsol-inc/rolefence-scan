"""Local-program ("stdio") MCP servers: the ones an AI app starts itself from a settings file
(`command` + `args` + `env`) instead of reaching at a web address.

Two things live here:

- reading those entries out of the AI apps' settings files (Claude Code, Claude Desktop,
  Cursor, Codex). The values (`env`, `args`) are kept in memory only, to start the program;
  `public()` is the only view that leaves this module, and it holds no value.
- a tiny stdio MCP client that starts the program, sends `initialize` and `tools/list`, and
  stops it. It has no way to send `tools/call`: there is no method for it.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .mcpclient import PROTOCOL

TIMEOUT = 30.0          # npx and uvx may download the program on first start
SAFE_ENV = ("HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER", "LANG", "TMPDIR",
            "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "USERPROFILE")


@dataclass
class StdioServer:
    app: str                 # "Claude Desktop", "Codex", ...
    name: str                # the server's name in the settings file
    command: str
    settings_path: str
    args: list[str] = field(default_factory=list, repr=False)
    env: dict[str, str] = field(default_factory=dict, repr=False)
    index: int = 0           # position among the local-program servers of its settings file

    @property
    def program(self) -> str:
        """The command's base name (e.g. `node`); safe to show."""
        return os.path.basename(self.command) or self.command

    @property
    def label(self) -> str:
        return f"{self.app}: {self.name} · runs as a local program"

    def public(self) -> dict:
        """Everything about this server that may be shown, logged or returned. No values."""
        return {"kind": "stdio", "app": self.app, "name": self.name, "program": self.program,
                "settings_path": self.settings_path, "label": self.label}


# ---------------------------------------------------------------- settings files
def _from_entries(entries: dict, app: str, path: str, out: list[StdioServer]) -> None:
    for name, spec in entries.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("command"), str) or not spec["command"]:
            continue
        args = spec.get("args") if isinstance(spec.get("args"), list) else []
        env = spec.get("env") if isinstance(spec.get("env"), dict) else {}
        out.append(StdioServer(app=app, name=str(name), command=spec["command"], settings_path=path,
                               args=[str(a) for a in args],
                               env={str(k): str(v) for k, v in env.items() if v is not None}))


def _walk_json(obj, app: str, path: str, out: list[StdioServer]) -> None:
    """`mcpServers` (Claude, Cursor) or `servers` (VS Code style) maps, at any depth: ~/.claude.json
    keeps one per project as well as a global one."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("mcpServers", "servers") and isinstance(v, dict):
                _from_entries(v, app, path, out)
            elif isinstance(v, (dict, list)):
                _walk_json(v, app, path, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_json(v, app, path, out)


def load_stdio_servers(path: str | Path, app: str = "") -> list[StdioServer]:
    """Local-program servers in one settings file (.json, or Codex's config.toml). Unreadable
    files give an empty list; the same server listed twice (e.g. in two projects) is kept once."""
    p = Path(path).expanduser()
    app = app or _guess_app(p)
    out: list[StdioServer] = []
    try:
        text = p.read_text(encoding="utf-8")
        if p.suffix == ".toml":
            doc = tomllib.loads(text)
            servers = doc.get("mcp_servers")
            if isinstance(servers, dict):
                _from_entries(servers, app, str(p), out)
        else:
            _walk_json(json.loads(text), app, str(p), out)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return []
    seen, unique = set(), []
    for s in out:
        key = (s.name, s.command, tuple(s.args), tuple(sorted(s.env.items())))
        if key not in seen:
            seen.add(key)
            s.index = len(unique)
            unique.append(s)
    return unique


def _guess_app(p: Path) -> str:
    s = str(p)
    if "claude_desktop_config" in s:
        return "Claude Desktop"
    if p.name in (".mcp.json", ".claude.json"):
        return "Claude Code"
    if ".cursor" in s:
        return "Cursor"
    if ".codex" in s:
        return "Codex"
    return "Settings file"


# ---------------------------------------------------------------- stdio client
class StdioError(Exception):
    """A plain-English reason the program could not be asked. Never holds a value from env or args."""


def _child_env(server: StdioServer) -> dict[str, str]:
    # like the AI apps: a small safe base plus what the settings file sets
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV}
    env.update(server.env)
    return env


def list_tools(server: StdioServer, timeout: float = TIMEOUT) -> list[dict]:
    """Start the program the way the AI app does, ask for its tools, stop it.
    Sends initialize, notifications/initialized and tools/list only. Returns
    [{"name", "description", "annotations"}]. Raises StdioError."""
    env = _child_env(server)
    exe = shutil.which(server.command, path=env.get("PATH")) or (
        server.command if os.path.isfile(server.command) else None)
    if not exe:
        raise StdioError(f"the program {server.program} was not found on this computer")
    try:
        proc = subprocess.Popen(
            [exe, *server.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,          # a program may print its settings; never read them
            env=env, cwd=os.path.expanduser("~"),
            start_new_session=(os.name == "posix"),
        )
    except OSError as e:
        raise StdioError(f"the program {server.program} could not be started ({e.__class__.__name__})") from None

    lines: queue.Queue = queue.Queue()

    def reader() -> None:
        for raw in proc.stdout:  # type: ignore[union-attr]
            lines.put(raw)
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + timeout

    def send(msg: dict) -> None:
        try:
            proc.stdin.write((json.dumps(msg) + "\n").encode())  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]
        except (OSError, ValueError):
            raise StdioError(f"{server.name} closed its input before answering") from None

    def wait_for(rid: int) -> dict:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise StdioError(f"{server.name} did not answer within {int(timeout)} seconds")
            try:
                raw = lines.get(timeout=left)
            except queue.Empty:
                continue
            if raw is None:
                raise StdioError(f"{server.name} stopped before answering")
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue                       # stray log line on stdout
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise StdioError(f"{server.name} answered with an error")
                return msg["result"] or {}
            if "method" in msg and "id" in msg:  # a request from the server (roots, sampling, ...): decline
                send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}})

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "rolefence-scan", "version": "0.1"}}})
        wait_for(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools: list[dict] = []
        cursor, rid = None, 2
        while True:
            send({"jsonrpc": "2.0", "id": rid, "method": "tools/list",
                  "params": {"cursor": cursor} if cursor else {}})
            res = wait_for(rid)
            for t in res.get("tools") or []:
                if isinstance(t, dict) and t.get("name"):
                    tools.append({"name": str(t["name"]), "description": str(t.get("description") or ""),
                                  "annotations": t.get("annotations") if isinstance(t.get("annotations"), dict) else {}})
            cursor = res.get("nextCursor")
            rid += 1
            if not cursor or rid > 50:
                return tools
    finally:
        _stop(proc)


def _stop(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.close()  # type: ignore[union-attr]
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, sig)   # npx and friends start children; stop the whole group
            else:
                proc.terminate() if sig == signal.SIGTERM else proc.kill()
        except (OSError, ProcessLookupError):
            return
        try:
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            continue


def find(servers: list[StdioServer], name: str) -> StdioServer | None:
    return next((s for s in servers if s.name == name), None)

