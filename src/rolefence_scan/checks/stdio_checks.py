"""Checks for local-program ("stdio") MCP servers.

- plain-text secrets: read from the AI app's settings file only; nothing is started. Judged by
  shape (a connection string with a password, a key named like a password or token). Findings
  name the server, the file and the setting's NAME; no value, and no part of a connection
  string, ever leaves this module.
- tools: the program is started once, asked for its tool list, and stopped. No tool is run.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import unquote

from ..report import HIGH, MEDIUM, Finding, Report
from ..stdio import StdioError, StdioServer, list_tools

# scheme://user:password@hosts/...   (hosts may be a comma list, as in MongoDB)
URI_LOGIN = re.compile(
    r"(?i)\b(mongodb(?:\+srv)?|postgres(?:ql)?|mysql|mariadb|redis|rediss|mssql|sqlserver|oracle|"
    r"amqps?|clickhouse|cockroachdb|snowflake)://([^:/@\s]*):([^@\s]+)@([^/?#\s]+)")
PASSWORD_KEY = re.compile(r"(?i)(PASSWORD|PASSWD|_PWD$|^PWD_|DB_PASS$|_PASS$)")
TOKEN_KEY = re.compile(r"(?i)(SECRET|TOKEN|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL|AUTH_KEY)")
HOST_KEY = re.compile(r"(?i)(HOST|SERVER|ADDR)")
REFERENCE = re.compile(r"^\s*(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|<[^>]*>|\*+|x+|changeme)?\s*$", re.I)

OUTSIDE_CHECKS = ("open-tool-list", "default-toolset", "scopes-advertised", "listening-everywhere",
                  "tool-list-filtered", "round-the-gateway")

WRITE_WORDS = {"insert", "update", "delete", "append", "create", "set", "remove", "send", "write", "drop",
               "put", "patch", "upsert", "replace", "modify", "edit", "add", "rename", "move", "truncate",
               "execute", "exec", "post", "upload", "publish", "merge", "destroy", "purge", "grant", "revoke",
               "alter", "store", "save", "clear", "reset", "kill"}
WRITE_DESC = re.compile(r"(?i)\b(insert|update|delete|append|create|remove|send|write|drop|upsert|"
                        r"modify|truncate|overwrite|destroy|purge|alter)s?\b")


def _is_value(v: str) -> bool:
    """True when the setting holds a real value, not an empty one or a reference to another variable."""
    return bool(v) and not REFERENCE.match(v)


def _host_is_local(host: str) -> bool:
    h = unquote(host).strip().lower()
    if h.startswith("["):              # [::1]:5432
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:            # host:port
        h = h.split(":", 1)[0]
    if h in ("", "localhost") or h.endswith(".localhost") or h.startswith("/"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _where(hosts: list[str]) -> str:
    if not hosts:
        return "unknown"
    local = [_host_is_local(h) for h in hosts]
    if all(local):
        return "this computer"
    if not any(local):
        return "another computer"
    return "this computer and another computer"


def _uri_login(value: str) -> list[str] | None:
    """Host list of a connection string that carries a password, else None. Only the host list
    is returned, and only so it can be classed as local or remote; it is never shown."""
    m = URI_LOGIN.search(value)
    if not m or not _is_value(unquote(m.group(3))):
        return None
    return [h for h in m.group(4).split(",") if h]


def scan_secrets(server: StdioServer) -> dict:
    """Names only: {"db": [setting names], "token": [...], "hosts": [...internal...]}"""
    db, token, hosts, host_keys = [], [], [], []
    for key, value in server.env.items():
        uri_hosts = _uri_login(value)
        if uri_hosts is not None:
            db.append(key)
            hosts.extend(uri_hosts)
        elif _is_value(value) and PASSWORD_KEY.search(key) and key.upper() != "PWD":
            db.append(key)
        elif _is_value(value) and TOKEN_KEY.search(key):
            token.append(key)
        elif _is_value(value) and HOST_KEY.search(key) and not URI_LOGIN.search(value):
            host_keys.append(value)
    for i, arg in enumerate(server.args, 1):
        uri_hosts = _uri_login(arg)
        if uri_hosts is not None:
            db.append(f"start-up argument {i}")
            hosts.extend(uri_hosts)
    if db and not hosts:
        hosts = host_keys
    return {"db": db, "token": token, "hosts": hosts}


def check_plaintext_secrets(report: Report, servers: list[StdioServer]) -> None:
    any_found = False
    for s in servers:
        found = scan_secrets(s)
        if found["db"]:
            any_found = True
            where = _where(found["hosts"])
            place = {"this computer": "The database is on this computer.",
                     "another computer": "The database is on another computer, so this login works from "
                                         "anywhere that can reach it, not just here.",
                     "this computer and another computer": "The login points at this computer and another one.",
                     }.get(where, "The scanner could not tell where the database is.")
            report.add(Finding(
                id="plaintext-db-password", title="A database password is stored in a plain text file", severity=HIGH,
                what=f'The settings file {s.settings_path} starts the local program "{s.name}" with a database '
                     f"login, password included, written out in {_names(found['db'])}. {place}",
                why="Anyone or anything that can read this file (another program, a backup, a synced folder, a "
                    "shared screen) gets the login. The AI app hands it to the program, so the AI can do "
                    "whatever this login allows.",
                fix="Give the AI its own read-only database user that can reach only what it needs, and change "
                    "this password. Keep the secret out of the file: in the operating system's keychain or a "
                    "secrets manager, handed to the program when it starts (for example by a small launcher "
                    "that reads it from the keychain).",
                evidence={"server": s.name, "settings_file": s.settings_path, "settings": found["db"],
                          "database_on": where},
            ))
        if found["token"]:
            any_found = True
            report.add(Finding(
                id="plaintext-token", title="A token or API key is stored in a plain text file", severity=MEDIUM,
                what=f'The settings file {s.settings_path} starts the local program "{s.name}" with '
                     f"{_names(found['token'])} written out in full.",
                why="Anyone or anything that can read this file can use the key as if they were this program, "
                    "with everything the key is allowed to do.",
                fix="Give the AI a key of its own with the fewest rights it needs, and replace this one. Keep it "
                    "in the operating system's keychain or a secrets manager instead of the settings file.",
                evidence={"server": s.name, "settings_file": s.settings_path, "settings": found["token"]},
            ))
    if not any_found:
        report.ok("plaintext-secrets")


def _names(keys: list[str]) -> str:
    quoted = [k if k.startswith("start-up argument") else f"the setting {k}" for k in keys[:6]]
    return ", ".join(quoted)


def _words(name: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return {w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w}


def is_write_tool(tool: dict) -> bool:
    ann = tool.get("annotations") or {}
    if ann.get("readOnlyHint") is True:
        return False
    if ann.get("destructiveHint") is True:
        return True
    if _words(tool["name"]) & WRITE_WORDS:
        return True
    return bool(WRITE_DESC.search(tool.get("description", "")[:300]))


def mark_outside_checks_not_applicable(report: Report) -> None:
    for check in OUTSIDE_CHECKS:
        report.na(check, "does not apply to a local program")


def check_stdio_tools(report: Report, server: StdioServer, timeout: float | None = None) -> list[str] | None:
    """Start the program, list its tools, stop it. Returns the tool names, or None if it could not be asked."""
    try:
        tools = list_tools(server, **({"timeout": timeout} if timeout else {}))
    except StdioError as e:
        report.skip("stdio-tool-list", str(e))
        return None
    names = [t["name"] for t in tools]
    writers = [t["name"] for t in tools if is_write_tool(t)]
    if writers:
        report.add(Finding(
            id="stdio-write-tools", title="The AI is given tools that change things", severity=HIGH,
            what=f'"{server.name}" offers {len(writers)} tool(s) whose name or description says they '
                 f"insert, update, delete, create or send: {', '.join(writers[:10])}.",
            why="A model with a write tool will use it; one bad instruction, or a sentence hidden in a "
                "document it reads, changes real data.",
            fix="Turn the write tools off in this program's settings (many have a read-only switch), and give "
                "it a database login that can only read, so the database refuses writes even if a tool tries.",
            evidence={"tools": writers[:20]},
        ))
    else:
        report.ok("stdio-write-tools")
    if names:
        report.add(Finding(
            id="local-tools-open", title="Every tool is open to whoever uses this computer", severity=MEDIUM,
            what=f'"{server.name}" offers {len(names)} tool(s) to any app or person on this computer that starts '
                 "it. There is no login and no role: every caller gets every tool.",
            why="Roles, a record of who asked, and a way to switch one person off do not exist here. Anyone who "
                "can use this computer account (or any AI app on it) reaches everything the program's database "
                "login can reach.",
            fix="Keep the program's own database login read-only and narrow. For shared or sensitive data, run "
                "the server once, behind a login and per-role rules (a gateway), and point the AI apps at its "
                "address instead of starting a copy on each computer.",
            evidence={"tool_count": len(names), "tools": names[:20]},
        ))
    else:
        report.skip("local-tools-open", f"{server.name} listed no tools")
    return names
