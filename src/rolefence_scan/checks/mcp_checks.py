"""Checks made from outside, against the MCP endpoint (a gateway or the question server
itself). Read-only: initialize, tools/list, and tools/call with empty arguments."""

from __future__ import annotations

import socket
from urllib.parse import urlsplit

from ..mcpclient import McpClient, call_refused, fetch_json, tool_names
from ..report import HIGH, LOW, MEDIUM, Finding, Report


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def check_open_tool_list(report: Report, url: str) -> list[str] | None:
    """Does the endpoint answer with a tool list when no token is sent?
    Returns the names it listed, or None when a login was required."""
    c = McpClient(url)
    try:
        init = c.initialize()
        if init.status in (401, 403):
            report.ok("open-tool-list")
            return None
        if not init.ok:
            report.skip("open-tool-list", f"could not initialize (HTTP {init.status})")
            return None
        tools = c.list_tools()
        names = tool_names(tools)
        if tools.ok and names:
            report.add(Finding(
                id="open-tool-list", title="Tools are listed without a login", severity=HIGH,
                what=f"The server at {url} answered with {len(names)} tool(s) when no token was sent.",
                why="Anyone who can reach this address can see every question the AI can ask and call them. "
                    "There is no record of who asked.",
                fix="Put a login in front: a gateway that checks tokens (agentgateway with Keycloak or your "
                    "identity provider), or the question server's own auth setting. Then check that the "
                    "server itself no longer answers without a token.",
                evidence={"tool_count": len(names), "tools": names[:20]},
            ))
            return names
        report.skip("open-tool-list", f"tools/list did not answer as expected (HTTP {tools.status})")
        return None
    finally:
        c.close()


def check_round_the_gateway(report: Report, backend_url: str) -> None:
    """Can the question server behind the gateway be reached directly, with no token?"""
    c = McpClient(backend_url)
    try:
        init = c.initialize()
        if init.status in (401, 403):
            report.ok("round-the-gateway")
            return
        if init.status == 0:
            report.ok("round-the-gateway")   # not reachable from here at all
            return
        tools = c.list_tools()
        names = tool_names(tools)
        if tools.ok:
            report.add(Finding(
                id="round-the-gateway", title="The question server can be reached without the gateway", severity=HIGH,
                what=f"{backend_url} answered directly, with no token, and listed {len(names)} tool(s).",
                why="The gateway only protects traffic that goes through it. A client that knows the "
                    "server's own address skips the login and the role rules entirely.",
                fix="Make the question server itself require a token (so both layers check), or bind it so "
                    "only the gateway can reach it (localhost or a private network, plus a firewall rule).",
                evidence={"tool_count": len(names)},
            ))
        else:
            report.skip("round-the-gateway", f"backend answered HTTP {init.status} but tools/list failed")
    finally:
        c.close()


def check_with_token(report: Report, url: str, token: str, role_label: str = "this token") -> list[str]:
    """Signed-in checks: token accepted? tool list filtered to what the caller may call?
    Returns the tool names listed for the token."""
    c = McpClient(url, token=token)
    try:
        init = c.initialize()
        if init.status == 401:
            report.add(Finding(
                id="token-refused", title="A valid token is refused", severity=MEDIUM,
                what=f"{url} answered 401 even though a token was sent.",
                why="Either the token is not accepted by this server, or a gateway in front has checked the "
                    "token and then removed it before passing the request on (the default in agentgateway), "
                    "so the server behind it sees no token. The person only sees '401'.",
                fix="If a gateway sits in front of a login-protected server, set the gateway to pass the "
                    "token through (agentgateway: backendAuth: passthrough). Otherwise check the token's "
                    "issuer and audience match what the server expects.",
                evidence={"status": 401},
            ))
            return []
        if not init.ok:
            report.skip("tool-list-filtered", f"could not initialize with token (HTTP {init.status})")
            return []
        tools = c.list_tools()
        names = tool_names(tools)
        if not tools.ok:
            report.skip("tool-list-filtered", f"tools/list failed with token (HTTP {tools.status})")
            return []
        refused, allowed = [], []
        for n in names:
            r = c.call_tool(n, {})
            (refused if call_refused(r) else allowed).append(n)
        if refused:
            report.add(Finding(
                id="tool-list-not-filtered", title="The tool list shows tools the caller may not use", severity=MEDIUM,
                what=f"{role_label} was shown {len(names)} tool(s) but {len(refused)} of them are refused when called.",
                why="The model sees questions it is not allowed to ask. It will try them, fail, and may tell "
                    "the person what exists. Hiding is cleaner than refusing.",
                fix="Filter the list per caller: a gateway rule that hides tools by role, or a per-role "
                    "toolset endpoint. Keep the refusal too, as the second lock.",
                evidence={"listed": len(names), "refused": refused[:20]},
            ))
        else:
            report.ok("tool-list-filtered")
        return names
    finally:
        c.close()


def check_scopes_advertised(report: Report, url: str) -> None:
    """When a login is required, does the server tell clients which scopes to ask for?"""
    p = urlsplit(url)
    candidates = [
        f"{_origin(url)}/.well-known/oauth-protected-resource{p.path}",
        f"{_origin(url)}/.well-known/oauth-protected-resource",
    ]
    for cand in candidates:
        status, body = fetch_json(cand)
        if status == 200 and isinstance(body, dict):
            scopes = body.get("scopes_supported") or []
            if scopes:
                report.ok("scopes-advertised")
            else:
                report.add(Finding(
                    id="scopes-not-advertised", title="Clients are not told which scopes to ask for", severity=LOW,
                    what=f"{cand} exists but lists no scopes_supported.",
                    why="If tools need scopes, a client that asks for a plain token is refused even on the "
                        "tools its role may use, and nothing tells it why.",
                    fix="List the scopes in the resource metadata (agentgateway: resourceMetadata.scopesSupported; "
                        "or a hand-written protected-resource metadata file for the question server).",
                    evidence={"metadata_url": cand},
                ))
            return
    report.add(Finding(
        id="no-resource-metadata", title="No OAuth resource metadata is published", severity=LOW,
        what="Neither well-known protected-resource URL answered with metadata.",
        why="AI apps use this to find the login server and the scopes to ask for. Without it, each app "
            "has to be set up by hand.",
        fix="Publish /.well-known/oauth-protected-resource (the gateway can generate it).",
    ))


def check_default_toolset(report: Report, url: str, token: str | None, listed_for_role: list[str]) -> None:
    """If the URL names a toolset (/mcp/<name>), does the bare /mcp serve more tools?"""
    p = urlsplit(url)
    parts = p.path.rstrip("/").split("/")
    if len(parts) < 3 or parts[-2] != "mcp":
        return
    bare = f"{_origin(url)}/mcp"
    c = McpClient(bare, token=token)
    try:
        if not c.initialize().ok:
            report.ok("default-toolset")
            return
        names = tool_names(c.list_tools())
        extra = sorted(set(names) - set(listed_for_role))
        if extra:
            report.add(Finding(
                id="default-toolset", title="The bare /mcp address serves every tool", severity=HIGH,
                what=f"{url} lists {len(listed_for_role)} tool(s); {bare} lists {len(names)}, "
                     f"including {len(extra)} not meant for this caller.",
                why="Per-role toolsets are a menu, not a boundary. Anyone who drops the toolset name from "
                    "the address gets everything.",
                fix="Do not rely on toolset paths for access control. Enforce roles at a gateway or with "
                    "per-tool scopes, and give each role its own database user so the data layer holds too.",
                evidence={"extra_tools": extra[:20]},
            ))
        else:
            report.ok("default-toolset")
    finally:
        c.close()


def _outbound_ip() -> str | None:
    """This machine's address on the local network (no packet is sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))   # TEST-NET, never routed
        ip = s.getsockname()[0]
        s.close()
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def check_listening_everywhere(report: Report, url: str) -> None:
    """For a local target: is the same port open on the machine's network address too?"""
    p = urlsplit(url)
    host = p.hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        report.skip("listening-everywhere", "only checked for local targets")
        return
    port = p.port or (443 if p.scheme == "https" else 80)
    lan = _outbound_ip()
    if not lan:
        report.skip("listening-everywhere", "no network address found on this machine")
        return
    try:
        with socket.create_connection((lan, port), timeout=1.5):
            open_on_lan = True
    except OSError:
        open_on_lan = False
    if open_on_lan:
        report.add(Finding(
            id="listening-everywhere", title="The server accepts connections from the whole network", severity=MEDIUM,
            what=f"Port {port} answers on this machine's network address as well as on localhost.",
            why="Every device on the same network can reach it. A login still stands in the way, but the "
                "door should not be visible at all; one setup mistake later and nothing else protects the data.",
            fix="Bind the server to 127.0.0.1 (or the private address the gateway uses) and keep the "
                "firewall closed. agentgateway's mcp listener has no bind setting yet; put it behind a "
                "local reverse proxy or a firewall rule.",
            evidence={"port": port},
        ))
    else:
        report.ok("listening-everywhere")
