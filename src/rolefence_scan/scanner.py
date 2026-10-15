"""Runs the checks in order and returns a Report. All secrets arrive as values already read
from environment variables by the caller; nothing here reads argv."""

from __future__ import annotations

from typing import Callable

from .checks import config_checks, db_checks, mcp_checks
from .report import Report


def scan(mcp_url: str | None = None, token: str | None = None, backend_url: str | None = None,
         db_uri: str | None = None, config_path: str | None = None, role_label: str = "the token",
         on_event: Callable[[str, dict], None] | None = None) -> Report:
    target = mcp_url or backend_url or (config_path and f"config {config_path}") or "database"
    report = Report(target=target, on_event=on_event)
    hinted: list[str] = []

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
