"""Command line. Secrets are named by environment variable, never passed as values:

    rolefence-scan --mcp http://127.0.0.1:3001/mcp --token-env SALES_TOKEN \\
                   --backend http://127.0.0.1:5002/mcp --db-env DEMO_SALES_URI \\
                   --config tools.yaml --json report.json

Exit code: 0 nothing found, 1 low/medium findings, 2 any high finding, 3 could not run.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .report import HIGH
from .scanner import scan


def _env(name: str | None) -> str | None:
    if not name:
        return None
    val = os.environ.get(name)
    if val is None:
        print(f"environment variable {name} is not set", file=sys.stderr)
        sys.exit(3)
    return val


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rolefence-scan", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mcp", help="MCP endpoint to scan (gateway or question server), e.g. http://127.0.0.1:3001/mcp")
    p.add_argument("--token-env", metavar="VAR", help="environment variable holding a bearer token for one role")
    p.add_argument("--role", default="the token", help="label for the role the token belongs to (report text only)")
    p.add_argument("--backend", help="the question server's own MCP address behind the gateway, to test the bypass")
    p.add_argument("--db-env", metavar="VAR", help="environment variable holding the MongoDB connection string the question server uses")
    p.add_argument("--config", help="Toolbox config file (tools.yaml) to check")
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
    if not (args.mcp or args.backend or args.db_env or args.config):
        print("nothing to scan: give --mcp, --backend, --db-env or --config", file=sys.stderr)
        return 3
    report = scan(mcp_url=args.mcp, token=_env(args.token_env), backend_url=args.backend,
                  db_uri=_env(args.db_env), config_path=args.config, role_label=args.role)
    print(report.to_text())
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(report.to_json())
    if not report.findings:
        return 0
    return 2 if report.worst == HIGH else 1


if __name__ == "__main__":
    sys.exit(main())
