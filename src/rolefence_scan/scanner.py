"""Runs the checks in order and returns a Report. All secrets arrive as values already read
from environment variables by the caller; nothing here reads argv."""

from __future__ import annotations

from typing import Callable

from .checks import config_checks, db_checks, mcp_checks, stdio_checks
from .report import Report
from .stdio import StdioServer


def scan(mcp_url: str | None = None, token: str | None = None, backend_url: str | None = None,
         db_uri: str | None = None, config_path: str | None = None, role_label: str = "the token",
         on_event: Callable[[str, dict], None] | None = None,
         stdio_servers: list[StdioServer] | None = None, stdio_start: StdioServer | None = None,
         stdio_timeout: float | None = None) -> Report:
    """stdio_servers: local-program servers whose settings are checked for plain-text secrets
    (nothing is started). stdio_start: one local-program server to start, ask for its tool list,
    and stop. Only names, counts and the settings file's path reach the report."""
    stdio_target = None
    if stdio_start:
        stdio_target = f'local program "{stdio_start.name}" ({stdio_start.app})'
    elif stdio_servers:
        paths = sorted({s.settings_path for s in stdio_servers})
        stdio_target = f"local programs in {', '.join(paths)}"
    target = (mcp_url or backend_url or stdio_target or (config_path and f"config {config_path}")
              or "database")
    report = Report(target=target, on_event=on_event)
    hinted: list[str] = []

    if stdio_servers or stdio_start:
        if stdio_servers:
            stdio_checks.check_plaintext_secrets(report, stdio_servers)
        if stdio_start:
            stdio_checks.check_stdio_tools(report, stdio_start, timeout=stdio_timeout)
        if not (mcp_url or backend_url):
            stdio_checks.mark_outside_checks_not_applicable(report)

    if config_path:
        config_checks.run_config_checks(report, config_path)
        try:
            hinted = config_checks.collections_in(config_checks.load_tools(config_path))
        except Exception:  # already reported by run_config_checks
            hinted = []

    if mcp_url:
        open_names = mcp_checks.check_open_tool_list(report, mcp_url)
        if open_names is None:
            mcp_checks.check_scopes_advertised(report, mcp_url)
        listed: list[str] = open_names or []
        if token:
            listed = mcp_checks.check_with_token(report, mcp_url, token, role_label) or listed
        mcp_checks.check_default_toolset(report, mcp_url, token, listed)
        mcp_checks.check_listening_everywhere(report, mcp_url)

    if backend_url:
        mcp_checks.check_round_the_gateway(report, backend_url)
        if backend_url != mcp_url:
            mcp_checks.check_listening_everywhere(report, backend_url)

    if db_uri:
        db_checks.run_db_checks(report, db_uri, hinted)

    return report
