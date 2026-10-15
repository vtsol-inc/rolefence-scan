"""Find things on this machine so the wizard can offer them instead of asking:

- running MCP Toolbox and agentgateway processes (address, config file, port)
- the connection-string variables those processes were started with (names only are shown;
  the values stay inside the wizard's memory and are used only if the admin picks them)
- MCP server addresses in the local AI apps' settings (Claude Code, Claude Desktop, Cursor, Codex)
- local-program MCP servers in those same settings (`command` + `args` + `env`); only the app,
  the server's name, the program's base name and the settings file's path are shown

Each app server is sorted into "database", "tool" (well-known, not a database) or "unknown"
(see catalog.py), and the same server listed in several places is returned once, with the
list of apps.

Read-only; nothing is changed. Works on macOS and Linux; on other systems it finds nothing.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

from ..catalog import DATABASE, TOOL, UNKNOWN, classify
from ..stdio import StdioServer, load_stdio_servers

ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


# ---------------------------------------------------------------- processes
def _processes() -> list[tuple[int, str]]:
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, cmd = line.partition(" ")
        if pid.isdigit():
            rows.append((int(pid), cmd.strip()))
    return rows


def _process_env(pid: int) -> dict[str, str]:
    """Environment of one of this user's processes. Linux: /proc; macOS: ps -E."""
    try:
        if sys.platform.startswith("linux"):
            raw = Path(f"/proc/{pid}/environ").read_bytes()
            return dict(kv.split("=", 1) for kv in raw.decode(errors="replace").split("\0") if "=" in kv)
        if sys.platform == "darwin":
            out = subprocess.run(["ps", "-wwE", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True, timeout=5).stdout
            env: dict[str, str] = {}
            for tok in out.split(" "):
                if "=" in tok and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
                    k, v = tok.split("=", 1)
                    env[k] = v
            return env
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        pass
    return {}


def _arg(tokens: list[str], *names: str) -> str | None:
    for i, t in enumerate(tokens):
        for n in names:
            if t == n and i + 1 < len(tokens):
                return tokens[i + 1]
            if t.startswith(n + "="):
                return t.split("=", 1)[1]
    return None


def _resolve(path: str, cwd: str | None) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute() and cwd:
        p = Path(cwd) / p
    return str(p)


def _process_cwd(pid: int) -> str | None:
    try:
        if sys.platform.startswith("linux"):
            return os.readlink(f"/proc/{pid}/cwd")
        if sys.platform == "darwin":
            out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if line.startswith("n"):
                    return line[1:]
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def find_servers() -> list[dict]:
    found = []
    for pid, cmd in _processes():
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = cmd.split()
        if not tokens:
            continue
        # the program may be wrapped (sh, a launcher), so look at the first few tokens
        exe = next((os.path.basename(t) for t in tokens[:3] if os.path.basename(t) in ("toolbox", "agentgateway")), "")
        if exe == "toolbox" and ("--config" in cmd or "--prebuilt" in cmd):
            port = _arg(tokens, "--port", "-p") or "5000"
            cfg = _arg(tokens, "--config")
            cwd = _process_cwd(pid)
            found.append({
                "kind": "toolbox", "pid": pid,
                "address": f"http://127.0.0.1:{port}/mcp",
                "config_path": _resolve(cfg, cwd) if cfg else None,
                "env": _process_env(pid),
                "label": f"MCP Toolbox on port {port}",
            })
        elif exe == "agentgateway":
            cfg = _arg(tokens, "-f", "--file")
            cwd = _process_cwd(pid)
            entry = {"kind": "agentgateway", "pid": pid, "address": None, "backend": None,
                     "config_path": _resolve(cfg, cwd) if cfg else None, "env": {}, "label": "agentgateway"}
            if entry["config_path"]:
                entry.update(_read_gateway_config(entry["config_path"]))
            if entry["address"]:
                found.append(entry)
    return found


def _read_gateway_config(path: str) -> dict:
    out: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        mcp = doc.get("mcp") or {}
        port = mcp.get("port")
        if port:
            out["address"] = f"http://127.0.0.1:{port}/mcp"
            out["label"] = f"agentgateway on port {port}"
        for t in mcp.get("targets") or []:
            host = ((t.get("mcp") or {}).get("host"))
            if host:
                out["backend"] = host
                break
    except (OSError, yaml.YAMLError):
        pass
    return out


# ---------------------------------------------------------------- config files
def config_env_refs(path: str) -> list[str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    return sorted(set(ENV_REF.findall(text)))


# ---------------------------------------------------------------- AI app settings
def _app_files() -> list[tuple[str, Path]]:
    home = Path.home()
    files = [
        ("Claude Code (this folder)", Path.cwd() / ".mcp.json"),
        ("Claude Code", home / ".claude.json"),
        ("Claude Desktop", home / "Library/Application Support/Claude/claude_desktop_config.json"),
        ("Claude Desktop", home / ".config/Claude/claude_desktop_config.json"),
        ("Cursor", home / ".cursor/mcp.json"),
        ("Codex", home / ".codex/config.toml"),
    ]
    return [(n, p) for n, p in files if p.is_file()]


def _urls_in_json(obj, out: list[tuple[str, str]], name: str = "") -> None:
    if isinstance(obj, dict):
        url = obj.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            out.append((name, url))
        for k, v in obj.items():
            _urls_in_json(v, out, k if isinstance(v, dict) else name)
    elif isinstance(obj, list):
        for v in obj:
            _urls_in_json(v, out, name)


def app_name(app: str) -> str:
    """'Claude Code (this folder)' and 'Claude Code' are one app in a list of apps."""
    return app.replace(" (this folder)", "")


def _add_app(apps: list[str], app: str) -> None:
    if app_name(app) not in apps:
        apps.append(app_name(app))


def find_app_servers() -> list[dict]:
    """MCP servers with a web address, one row per address, with every app that lists it."""
    by_url: dict[str, dict] = {}
    for app, path in _app_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        pairs: list[tuple[str, str]] = []
        if path.suffix == ".json":
            try:
                _urls_in_json(json.loads(text), pairs)
            except json.JSONDecodeError:
                continue
        else:  # codex config.toml: url = "..." under [mcp_servers.<name>]
            section = ""
            for line in text.splitlines():
                m = re.match(r"\s*\[mcp_servers\.([^\]]+)\]", line)
                if m:
                    section = m.group(1)
                m = re.match(r'\s*url\s*=\s*"([^"]+)"', line)
                if m and m.group(1).startswith(("http://", "https://")):
                    pairs.append((section, m.group(1)))
        for name, url in pairs:
            if url in by_url:
                _add_app(by_url[url]["apps"], app)
                continue
            group, known = classify(url=url)
            by_url[url] = {"kind": "app", "apps": [app_name(app)], "name": name, "address": url,
                           "group": group, "known": known}
    found = []
    for e in by_url.values():
        e["app"] = ", ".join(e["apps"])
        e["label"] = f"{e['app']}: {e['name'] or e['address']}"
        found.append(e)
    return found


_RANK = {DATABASE: 0, UNKNOWN: 1, TOOL: 2}


def find_app_stdio_groups() -> list[dict]:
    """Local-program servers, one row per server (same name, program and arguments), with every
    app and settings file that lists it: {"servers": [StdioServer, ...], "apps", "group", "known"}.
    The caller must show only `.public()` of the servers."""
    groups: dict[tuple, dict] = {}
    for app, path in _app_files():
        for srv in load_stdio_servers(path, app=app):
            key = (srv.name, srv.program, tuple(srv.args))
            g = groups.setdefault(key, {"servers": [], "apps": []})
            g["servers"].append(srv)
            _add_app(g["apps"], app)
    out = []
    for g in groups.values():
        # the most telling copy decides: a database login in any of them makes it a database server
        g["group"], g["known"] = min((classify(command=s.command, args=s.args, env=s.env) for s in g["servers"]),
                                     key=lambda r: _RANK[r[0]])
        out.append(g)
    return out
