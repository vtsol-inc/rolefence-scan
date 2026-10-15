import pytest

from rolefence_scan.checks import mcp_checks
from rolefence_scan.mcpclient import McpClient, McpResponse, call_refused, call_unknown_tool, tool_names
from rolefence_scan.report import Report


@pytest.mark.parametrize("sse", [False, True])
def test_initialize_list_and_call_over_json_and_sse(fake_server, sse):
    f = fake_server(mode="open", tools=["x", "y"], sse=sse)
    c = McpClient(f.url)
    try:
        init = c.initialize()
        assert init.status == 200 and init.ok
        assert init.result["serverInfo"]["name"] == "fake"
        assert c.session_id == "sess-1"
        tools = c.list_tools()
        assert tool_names(tools) == ["x", "y"]
        call = c.call_tool("x", {})
        assert call.ok and call.result["isError"] is False
    finally:
        c.close()
    methods = [r["method"] for r in f.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert f.requests[-1]["_session"] == "sess-1"
    assert f.requests[0]["_auth"] is None


def test_401_is_surfaced_not_raised(fake_server):
    f = fake_server(mode="401")
    c = McpClient(f.url, token="whatever")
    try:
        r = c.initialize()
        assert r.status == 401 and not r.ok and r.body is None
        assert f.requests[0]["_auth"] == "Bearer whatever"
        assert len(f.requests) == 1          # no notification after a refused initialize
    finally:
        c.close()


def test_403_and_error_bodies_count_as_refused(fake_server):
    f = fake_server(mode="scoped", tools=["a_tool", "b_tool"], refuse=["b_tool"])
    c = McpClient(f.url, token="good")
    try:
        assert c.initialize().ok
        assert not call_refused(c.call_tool("a_tool"))
        assert call_refused(c.call_tool("b_tool"))        # JSON-RPC error 'insufficient scope'
        f.refuse_with_403 = True
        r = c.call_tool("b_tool")
        assert r.status == 403 and call_refused(r)
    finally:
        c.close()


def test_wrong_token_gets_401(fake_server):
    f = fake_server(mode="scoped")
    c = McpClient(f.url, token="bad")
    try:
        assert c.initialize().status == 401
    finally:
        c.close()


def test_connection_refused_is_status_zero():
    c = McpClient("http://127.0.0.1:9/mcp", timeout=1.0)
    try:
        r = c.initialize()
        assert r.status == 0 and r.text
    finally:
        c.close()


def test_helpers_on_synthetic_responses():
    assert call_unknown_tool(McpResponse(200, {"error": {"message": "Unknown tool: x"}}))
    assert not call_unknown_tool(McpResponse(200, {"result": {}}))
    assert call_refused(McpResponse(200, {"result": {"isError": True, "content": [{"type": "text", "text": "Not authorized"}]}}))
    assert not call_refused(McpResponse(200, {"result": {"isError": True, "content": [{"type": "text", "text": "missing parameter"}]}}))
    assert tool_names(McpResponse(200, {"result": {"tools": [{"name": "t"}, {}]}})) == ["t", "?"]


# ---- the outside checks, driven against the fake --------------------------------------

def _ids(report):
    return {f.id for f in report.findings}


def test_open_tool_list_fires_on_open_server(fake_server):
    f = fake_server(mode="open", tools=["t1", "t2", "t3"])
    r = Report(f.url)
    names = mcp_checks.check_open_tool_list(r, f.url)
    assert names == ["t1", "t2", "t3"]
    assert _ids(r) == {"open-tool-list"}
    assert r.findings[0].evidence == {"tool_count": 3, "tools": ["t1", "t2", "t3"]}


def test_open_tool_list_passes_when_login_required(fake_server):
    f = fake_server(mode="401")
    r = Report(f.url)
    assert mcp_checks.check_open_tool_list(r, f.url) is None
    assert r.findings == [] and r.passed == ["open-tool-list"]


def test_round_the_gateway(fake_server):
    open_backend = fake_server(mode="open")
    r = Report("gw")
    mcp_checks.check_round_the_gateway(r, open_backend.url)
    assert _ids(r) == {"round-the-gateway"}
    locked = fake_server(mode="401")
    r2 = Report("gw")
    mcp_checks.check_round_the_gateway(r2, locked.url)
    assert r2.findings == [] and r2.passed == ["round-the-gateway"]
    r3 = Report("gw")
    mcp_checks.check_round_the_gateway(r3, "http://127.0.0.1:9/mcp")
    assert r3.passed == ["round-the-gateway"]      # unreachable counts as closed


def test_token_refused_when_server_answers_401_to_a_token(fake_server):
    f = fake_server(mode="401")
    r = Report(f.url)
    assert mcp_checks.check_with_token(r, f.url, "tok", "sales") == []
    assert _ids(r) == {"token-refused"}


def test_tool_list_not_filtered_when_listed_tools_are_refused(fake_server):
    f = fake_server(mode="scoped", tools=["sales_a", "finance_b"], refuse=["finance_b"], token="tok")
    r = Report(f.url)
    names = mcp_checks.check_with_token(r, f.url, "tok", "sales")
    assert names == ["sales_a", "finance_b"]
    assert _ids(r) == {"tool-list-not-filtered"}
    assert r.findings[0].evidence == {"listed": 2, "refused": ["finance_b"]}


def test_tool_list_filtered_passes(fake_server):
    f = fake_server(mode="scoped", tools=["sales_a"], token="tok")
    r = Report(f.url)
    mcp_checks.check_with_token(r, f.url, "tok", "sales")
    assert r.findings == [] and r.passed == ["tool-list-filtered"]


def test_scopes_advertised(fake_server):
    f = fake_server(mode="401")
    f.scopes = ["openid", "demo:sales"]
    r = Report(f.url)
    mcp_checks.check_scopes_advertised(r, f.url)
    assert r.passed == ["scopes-advertised"]
    f.scopes = []
    r2 = Report(f.url)
    mcp_checks.check_scopes_advertised(r2, f.url)
    assert _ids(r2) == {"scopes-not-advertised"}
    r3 = Report("x")
    mcp_checks.check_scopes_advertised(r3, "http://127.0.0.1:9/mcp")
    assert _ids(r3) == {"no-resource-metadata"}
