"""Command line. Secrets are named by environment variable, never passed as values:

    rolefence-scan --mcp http://127.0.0.1:3001/mcp --token-env SALES_TOKEN \\
                   --backend http://127.0.0.1:5002/mcp --db-env DEMO_SALES_URI \\
                   --config tools.yaml --json report.json

Local-program MCP servers (started by the AI app from its settings file) are read from that file:

    rolefence-scan --stdio-from ~/Library/Application\\ Support/Claude/claude_desktop_config.json
    rolefence-scan --stdio-from .mcp.json --server crm     # also starts it once to list its tools
    rolefence-scan --stdio "npx -y some-mcp-server"        # start a command and list its tools

Exit code: 0 nothing found, 1 low/medium findings, 2 any high finding, 3 could not run.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys

from . import __version__
from .report import HIGH
from .scanner import scan
from .stdio import StdioServer, find, load_stdio_servers


def _env(name: str | None) -> str | None:
    if not name:
        return None
    val = os.environ.get(name)
    if val is None:
        print(f"environment variable {name} is not set", file=sys.stderr)
        sys.exit(3)
    return val


def _stdio_targets(args) -> tuple[list[StdioServer] | None, StdioServer | None]:
    """(servers whose settings to check, server to start). (None, None) means stop with exit 3."""
    if args.server and not args.stdio_from:
        print("--server needs --stdio-from", file=sys.stderr)
        return None, None
    if args.stdio:
        try:
            parts = shlex.split(args.stdio)
        except ValueError:
            parts = []
        if not parts:
            print("--stdio needs a command", file=sys.stderr)
            return None, None
        start = StdioServer(app="command line", name=os.path.basename(parts[0]), command=parts[0],
                            settings_path="(command line)", args=parts[1:],
                            env=dict(os.environ))   # a command typed in a shell runs with that shell's settings
        return [], start
    if not args.stdio_from:
        return [], None
    if not os.path.isfile(os.path.expanduser(args.stdio_from)):
        print(f"settings file not found: {args.stdio_from}", file=sys.stderr)
        return None, None
    servers = load_stdio_servers(args.stdio_from)
    if args.server:
        chosen = find(servers, args.server)
        if not chosen:
            names = ", ".join(s.name for s in servers) or "none"
            print(f"no local-program server named {args.server} in {args.stdio_from} (found: {names})", file=sys.stderr)
            return None, None
        return [chosen], chosen
    if not servers:
        print(f"no local-program servers in {args.stdio_from}", file=sys.stderr)
        return None, None
    return servers, None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rolefence-scan", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mcp", help="MCP endpoint to scan (gateway or question server), e.g. http://127.0.0.1:3001/mcp")
    p.add_argument("--token-env", metavar="VAR", help="environment variable holding a bearer token for one role")
    p.add_argument("--role", default="the token", help="label for the role the token belongs to (report text only)")
    p.add_argument("--backend", help="the question server's own MCP address behind the gateway, to test the bypass")
    p.add_argument("--db-env", metavar="VAR", help="environment variable holding the MongoDB connection string the question server uses")
    p.add_argument("--config", help="Toolbox config file (tools.yaml) to check")
    p.add_argument("--stdio-from", metavar="FILE", help="an AI app's settings file (Claude Code .mcp.json or "
                   "~/.claude.json, claude_desktop_config.json, Cursor mcp.json, Codex config.toml): check its "
                   "local-program servers for passwords and keys written in plain text (nothing is started)")
    p.add_argument("--server", metavar="NAME", help="with --stdio-from: check only this server, and start it once "
                   "to list its tools (no tool is run)")
    p.add_argument("--stdio", metavar="COMMAND", help="start this command as a local-program MCP server, list its "
                   "tools, and stop it (no tool is run)")
    p.add_argument("--stdio-timeout", type=float, default=30.0, metavar="SECONDS",
                   help="how long to wait for a local program to answer (default 30)")
    p.add_argument("--json", metavar="FILE", help="also write the report as JSON to FILE")
    p.add_argument("--version", action="version", version=f"rolefence-scan {__version__}")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("serve", help="run as an MCP server (stdio) so an AI assistant can run scans")
    ui = sub.add_parser("ui", help="open the step-by-step wizard in your browser (local only)")
    ui.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765; 0 picks a free one)")
    ui.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        from .server import serve
        serve()
        return 0
    if args.command == "ui":
        from .ui.server import serve_ui
        serve_ui(port=args.port, open_browser=not args.no_browser)
        return 0
    stdio_servers, stdio_start = _stdio_targets(args)
    if stdio_servers is None:
        return 3
    if not (args.mcp or args.backend or args.db_env or args.config or stdio_servers or stdio_start):
        print("nothing to scan: give --mcp, --backend, --db-env, --config, --stdio-from or --stdio", file=sys.stderr)
        return 3
    report = scan(mcp_url=args.mcp, token=_env(args.token_env), backend_url=args.backend,
                  db_uri=_env(args.db_env), config_path=args.config, role_label=args.role,
                  stdio_servers=stdio_servers or None, stdio_start=stdio_start,
                  stdio_timeout=args.stdio_timeout)
    print(report.to_text())
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(report.to_json())
    if not report.findings:
        return 0
    return 2 if report.worst == HIGH else 1


if __name__ == "__main__":
    sys.exit(main())
