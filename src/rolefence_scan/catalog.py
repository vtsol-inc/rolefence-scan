"""Sort an MCP server found in an AI app's settings into one of three groups, from the
settings alone (nothing is started, no value is shown or returned):

- "database": its settings carry a database connection (by shape), or it is a known
  database MCP server
- "tool":     a well-known MCP server that is not a database (data/known_servers.json)
- "unknown":  everything else; it may still reach your data

Values are only looked at in memory to decide the group; only the group and the known
server's public name leave this module.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from urllib.parse import urlsplit

DATABASE, TOOL, UNKNOWN = "database", "tool", "unknown"

DB_URI = re.compile(r"(?i)\b(mongodb(\+srv)?|postgres(ql)?|mysql|mariadb|redis|rediss|mssql|sqlserver|oracle|"
                    r"clickhouse|cockroachdb|snowflake|neo4j|sqlite|jdbc:[a-z0-9]+)://")
DB_KEY = re.compile(r"(?i)^(MONGO|DATABASE|DB_|PG[A-Z]|POSTGRES|MYSQL|MARIADB|REDIS|SQLITE|SUPABASE|NEO4J|"
                    r"CLICKHOUSE|SNOWFLAKE|BIGQUERY|MSSQL|ORACLE)|(_DB|_DATABASE|_DSN)$")


@lru_cache(maxsize=1)
def known_servers() -> dict:
    text = resources.files(__package__).joinpath("data/known_servers.json").read_text(encoding="utf-8")
    doc = json.loads(text)
    return {"databases": doc.get("databases", []), "tools": doc.get("tools", [])}


def _hosts(url: str | None, args: list[str]) -> list[str]:
    out = []
    for u in [url or "", *args]:
        if u.startswith(("http://", "https://")):
            h = (urlsplit(u).hostname or "").lower()
            if h:
                out.append(h)
    return out


def _bare(token: str) -> str:
    """'/usr/bin/toolbox' -> 'toolbox'; 'pkg@1.2' -> 'pkg'; 'mcp/sonarqube:latest' -> 'sonarqube'."""
    t = token.lower().rstrip("/").rsplit("/", 1)[-1]
    t = t.split(":", 1)[0]
    if "@" in t[1:]:
        t = t[0] + t[1:].split("@", 1)[0]
    return t


def _matches(pattern: str, tokens: list[str], hosts: list[str]) -> bool:
    p = pattern.lower()
    if "." in p and "/" not in p and not p.startswith("@"):          # a web host
        return any(h == p or h.endswith("." + p) for h in hosts)
    if "/" in p or p.startswith("@"):                                 # a package scope or image path
        return any(p in t.lower() for t in tokens)
    return any(_bare(t) == p for t in tokens)                        # a program or package name


def _known(entries: list[dict], tokens: list[str], hosts: list[str]) -> str | None:
    for e in entries:
        if any(_matches(p, tokens, hosts) for p in e.get("match", [])):
            return e.get("name") or "known"
    return None


def classify(command: str | None = None, args: list[str] | None = None, env: dict[str, str] | None = None,
             url: str | None = None, known: dict | None = None) -> tuple[str, str | None]:
    """(group, the known server's public name or None)."""
    known = known or known_servers()
    args = args or []
    env = env or {}
    tokens = ([command] if command else []) + args
    hosts = _hosts(url, args)
    name = _known(known["databases"], tokens, hosts)
    if name:
        return DATABASE, name
    if any(DB_URI.search(v) for v in [*env.values(), *args]) or any(DB_KEY.search(k) for k in env):
        return DATABASE, None
    name = _known(known["tools"], tokens, hosts)
    if name:
        return TOOL, name
    return UNKNOWN, None
