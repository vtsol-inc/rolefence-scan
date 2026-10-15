"""MCP server mode: lets an AI assistant (Claude, Codex, …) run the scan by asking in plain
words. Secrets are still named by environment variable; the assistant never sees them.

    rolefence-scan serve            # stdio transport

Register it with the assistant, e.g. in Claude Code's .mcp.json:
    {"mcpServers": {"rolefence-scan": {"command": "rolefence-scan", "args": ["serve"]}}}
"""

from __future__ import annotations

import os

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .scanner import scan
from .stdio import find, load_stdio_servers

app = _Server("rolefence-scan", instructions=(
    "Read-only exposure scanner for MCP servers in front of databases. Call `scan_mcp` with the "
    "endpoint to check. Tokens and database connection strings are named by environment variable "
    "(set in the shell where this server runs), never passed as values."
))


@app.tool()
def scan_mcp(mcp_url: str = "", token_env: str = "", role: str = "the token", backend_url: str = "",
             db_env: str = "", config_path: str = "") -> str:
    """Scan an MCP endpoint (and optionally the question server behind it, its database user and
    its Toolbox config) and return a plain-English report of what a model could reach.

    mcp_url:     the gateway or question server address, e.g. http://127.0.0.1:3001/mcp
    token_env:   name of an environment variable holding a bearer token for one role (optional)
    role:        label for that role, used in the report text
    backend_url: the question server's own address behind the gateway, to test the bypass (optional)
    db_env:      name of an environment variable holding the MongoDB URI the question server uses (optional)
    config_path: path to a Toolbox tools.yaml to check (optional)
    """
    token = os.environ.get(token_env) if token_env else None
    db_uri = os.environ.get(db_env) if db_env else None
    missing = [n for n, v in ((token_env, token), (db_env, db_uri)) if n and v is None]
    if missing:
        return "These environment variables are not set where rolefence-scan runs: " + ", ".join(missing)
    if not (mcp_url or backend_url or db_uri or config_path):
        return "Nothing to scan: give mcp_url, backend_url, db_env or config_path."
    report = scan(mcp_url=mcp_url or None, token=token, backend_url=backend_url or None,
                  db_uri=db_uri, config_path=config_path or None, role_label=role)
    return report.to_text()


@app.tool()
def scan_local_program(settings_file: str, server: str = "", list_tools: bool = False) -> str:
    """Check the local-program ("stdio") MCP servers in an AI app's settings file: passwords or keys
    written there in plain text, and (with list_tools) what tools the program offers.

    settings_file: e.g. ~/Library/Application Support/Claude/claude_desktop_config.json, .mcp.json,
                   ~/.claude.json, ~/.cursor/mcp.json, ~/.codex/config.toml
    server:        check only the server with this name (required for list_tools)
    list_tools:    start that server once the way the AI app does, ask for its tool list, and stop
                   it. No tool is run. Ask the person before setting this.

    The report names settings, never their values.
    """
    path = os.path.expanduser(settings_file)
    if not os.path.isfile(path):
        return f"Settings file not found: {settings_file}"
    servers = load_stdio_servers(path)
    if not servers:
        return f"No local-program servers in {settings_file}."
    if server:
        chosen = find(servers, server)
        if not chosen:
            return f"No local-program server named {server}. Found: " + ", ".join(s.name for s in servers)
        servers = [chosen]
    elif list_tools:
        return "list_tools needs a server name. Found: " + ", ".join(s.name for s in servers)
    report = scan(stdio_servers=servers, stdio_start=servers[0] if list_tools else None)
    return report.to_text()


@app.tool()
def list_checks() -> str:
    """List the checks rolefence-scan runs and what each needs."""
    return (
        "From outside (needs mcp_url): open tool list; default /mcp toolset; scopes advertised; "
        "listening on the whole network. With a token: token refused (gateway dropping it); tool list "
        "not filtered. With backend_url: question server reachable round the gateway.\n"
        "As the database user (needs db_env): writes allowed; server-side JavaScript; cross-collection "
        "$lookup; host name and query echoed in replies.\n"
        "From the config file (needs config_path): write tools; free-form queries; aggregate not "
        "read-only; no row cap; find without projection.\n"
        "Local-program servers (scan_local_program, from an AI app's settings file): passwords and keys "
        "written there in plain text. With list_tools (starts the program once, runs no tool): write "
        "tools; every tool open to whoever uses the computer."
    )


def serve() -> None:
    app.run()
