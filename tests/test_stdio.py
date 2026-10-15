"""Local-program ("stdio") servers: reading settings files, the plain-text secret check, listing
tools over stdio (never calling one), and the CLI and wizard end to end. All names and values
are made up (Brightline Movers)."""

import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from rolefence_scan import cli
from rolefence_scan.checks import stdio_checks
from rolefence_scan.report import HIGH, MEDIUM, Report
from rolefence_scan.scanner import scan
from rolefence_scan.stdio import StdioServer, list_tools, load_stdio_servers, StdioError
from rolefence_scan.ui import detect
from rolefence_scan.ui.server import State, make_handler

FAKE = str(Path(__file__).with_name("fake_stdio_server.py"))
DB_PASSWORD = "Fake-Secret-Brightline-42"
DB_HOST = "db.brightline-movers.example"
API_KEY = "fake-key-0042-brightline"
SECRETS = (DB_PASSWORD, DB_HOST, API_KEY, "crm_ai:")


def _settings(tmp_path, record=None) -> Path:
    record = record or tmp_path / "record.txt"
    p = tmp_path / "claude_desktop_config.json"
    p.write_text(json.dumps({"mcpServers": {
        "brightline-crm": {
            "command": sys.executable, "args": [FAKE],
            "env": {"CRM_DB_URI": f"postgres://crm_ai:{DB_PASSWORD}@{DB_HOST}:5432/crm",
                    "MAPS_API_KEY": API_KEY, "FAKE_MCP_RECORD": str(record)},
        },
        "brightline-web": {"url": "http://127.0.0.1:5999/mcp"},
    }}))
    return p


def _assert_clean(text: str) -> None:
    for s in SECRETS:
        assert s not in text, f"{s!r} leaked"


def _record(tmp_path) -> list[str]:
    p = tmp_path / "record.txt"
    return p.read_text().split() if p.exists() else []


# ---------------------------------------------------------------- settings files
def test_reads_json_and_toml_shapes(tmp_path):
    servers = load_stdio_servers(_settings(tmp_path))
    assert [s.name for s in servers] == ["brightline-crm"]          # the url entry is not a local program
    assert servers[0].app == "Claude Desktop" and servers[0].program.startswith("python")
    pub = servers[0].public()
    assert set(pub) == {"kind", "app", "name", "program", "settings_path", "label"}
    assert pub["label"] == "Claude Desktop: brightline-crm · runs as a local program"
    _assert_clean(json.dumps(pub))

    claude = tmp_path / ".claude.json"
    claude.write_text(json.dumps({
        "mcpServers": {"moves": {"command": "node", "args": ["moves.js"]}},
        "projects": {"/work/a": {"mcpServers": {"quotes": {"type": "stdio", "command": "npx", "args": ["-y", "q"]},
                                                "moves": {"command": "node", "args": ["moves.js"]}}}},
    }))
    names = [(s.app, s.name, s.program) for s in load_stdio_servers(claude)]
    assert names == [("Claude Code", "moves", "node"), ("Claude Code", "quotes", "npx")]

    codex = tmp_path / "config.toml"
    codex.write_text('[mcp_servers.crews]\ncommand = "/usr/local/bin/uvx"\nargs = ["crews-mcp"]\n'
                     '[mcp_servers.crews.env]\nCREWS_TOKEN = "fake-token"\n'
                     '[mcp_servers.remote]\nurl = "https://mcp.brightline-movers.example/mcp"\n')
    (crews,) = load_stdio_servers(codex, app="Codex")
    assert (crews.name, crews.program, crews.env) == ("crews", "uvx", {"CREWS_TOKEN": "fake-token"})

    bad = tmp_path / "broken.json"
    bad.write_text("{not json")
    assert load_stdio_servers(bad) == [] and load_stdio_servers(tmp_path / "missing.json") == []


# ---------------------------------------------------------------- plain-text secrets
def _srv(env=None, args=None, name="s"):
    return StdioServer(app="Cursor", name=name, command="node", settings_path="/home/demo/.cursor/mcp.json",
                       args=args or [], env=env or {})


def test_secret_shapes():
    f = stdio_checks.scan_secrets
    assert f(_srv({"DB": f"postgres://crm_ai:{DB_PASSWORD}@{DB_HOST}:5432/crm"})) == \
        {"db": ["DB"], "token": [], "hosts": [f"{DB_HOST}:5432"]}
    local = f(_srv({"MONGO_URI": "mongodb://ai:pw@127.0.0.1:27017,localhost:27018/moves"}))
    assert local["db"] == ["MONGO_URI"] and stdio_checks._where(local["hosts"]) == "this computer"
    assert f(_srv({"MONGO_URI": "mongodb://127.0.0.1:27017/moves"}))["db"] == []      # no password
    assert f(_srv({"DB": "mysql://ai:${DB_PW}@db/x"}))["db"] == []                     # a reference, not a value
    assert f(_srv({"DB_PASSWORD": "hunter2-demo", "DB_HOST": "localhost"})) == \
        {"db": ["DB_PASSWORD"], "token": [], "hosts": ["localhost"]}
    assert f(_srv({"DB_PASSWORD": "${DB_PASSWORD}", "PWD": "/tmp"}))["db"] == []
    assert f(_srv({"GITHUB_TOKEN": "x1", "MAPS_API_KEY": "x2", "LOG_LEVEL": "debug"}))["token"] == \
        ["GITHUB_TOKEN", "MAPS_API_KEY"]
    assert f(_srv(args=["--uri", "mysql://ai:pw@[::1]:3306/x"]))["db"] == ["start-up argument 2"]


def test_secret_findings_name_keys_never_values(tmp_path):
    r = Report("t")
    servers = load_stdio_servers(_settings(tmp_path))
    stdio_checks.check_plaintext_secrets(r, servers)
    by = {f.id: f for f in r.findings}
    assert by["plaintext-db-password"].severity == HIGH
    assert by["plaintext-db-password"].evidence["settings"] == ["CRM_DB_URI"]
    assert by["plaintext-db-password"].evidence["database_on"] == "another computer"
    assert by["plaintext-token"].severity == MEDIUM
    assert by["plaintext-token"].evidence["settings"] == ["MAPS_API_KEY"]
    _assert_clean(r.to_text() + r.to_json() + r.to_html())
    assert _record(tmp_path) == []                         # nothing was started

    clean = Report("clean")
    stdio_checks.check_plaintext_secrets(clean, [_srv({"LOG_LEVEL": "info"})])
    assert clean.findings == [] and clean.passed == ["plaintext-secrets"]


# ---------------------------------------------------------------- listing tools over stdio
def test_list_tools_never_calls_a_tool(tmp_path):
    (srv,) = load_stdio_servers(_settings(tmp_path))
    tools = list_tools(srv, timeout=20)
    assert [t["name"] for t in tools] == ["list_moves", "get_quote", "add_crew_note"]
    assert _record(tmp_path) == ["initialize", "notifications/initialized", "tools/list"]
    assert "tools/call" not in _record(tmp_path)


def test_write_tool_words():
    w = stdio_checks.is_write_tool
    assert w({"name": "add_crew_note", "description": ""})
    assert w({"name": "updateBooking", "description": ""})
    assert w({"name": "crew_note", "description": "Deletes a crew note."})
    assert not w({"name": "list_moves", "description": "List booked moves."})
    assert not w({"name": "get_settings", "description": "Read the settings."})
    assert not w({"name": "delete_preview", "description": "", "annotations": {"readOnlyHint": True}})
    assert w({"name": "tidy", "description": "", "annotations": {"destructiveHint": True}})


def test_scan_of_a_local_program(tmp_path):
    (srv,) = load_stdio_servers(_settings(tmp_path))
    r = scan(stdio_servers=[srv], stdio_start=srv, stdio_timeout=20)
    ids = {f.id for f in r.findings}
    assert ids == {"plaintext-db-password", "plaintext-token", "stdio-write-tools", "local-tools-open"}
    by = {f.id: f for f in r.findings}
    assert by["stdio-write-tools"].evidence["tools"] == ["add_crew_note"]
    assert by["local-tools-open"].evidence["tool_count"] == 3
    for check in ("open-tool-list", "listening-everywhere", "round-the-gateway", "tool-list-filtered"):
        assert f"{check}: does not apply to a local program" in r.not_applicable
    assert r.skipped == []
    _assert_clean(r.to_text() + r.to_json() + r.to_html())
    assert "tools/call" not in _record(tmp_path)


def test_missing_program_and_silent_program_are_skipped():
    r = Report("t")
    gone = StdioServer(app="Cursor", name="gone", command="no-such-brightline-program", settings_path="x")
    assert stdio_checks.check_stdio_tools(r, gone) is None
    assert r.skipped == ["stdio-tool-list: the program no-such-brightline-program was not found on this computer"]

    silent = StdioServer(app="Cursor", name="silent", command=sys.executable, settings_path="x",
                         args=["-c", "import time; time.sleep(60)"])
    t0 = time.monotonic()
    with pytest.raises(StdioError, match="did not answer within 1 seconds"):
        list_tools(silent, timeout=1)
    assert time.monotonic() - t0 < 10                       # it was stopped, not waited for


# ---------------------------------------------------------------- CLI
def test_cli_never_shows_a_secret(tmp_path, capfd):
    settings = _settings(tmp_path)
    out_json = tmp_path / "r.json"
    code = cli.main(["--stdio-from", str(settings), "--server", "brightline-crm", "--json", str(out_json)])
    assert code == 2
    out, err = capfd.readouterr()            # includes anything the program printed to stderr
    assert "A database password is stored in a plain text file" in out
    _assert_clean(out + err + out_json.read_text())
    assert "tools/call" not in _record(tmp_path)

    code = cli.main(["--stdio-from", str(settings)])        # settings only: nothing is started
    out, _ = capfd.readouterr()
    assert code == 2 and "plain text file" in out and "change things" not in out
    assert _record(tmp_path) == ["initialize", "notifications/initialized", "tools/list"]

    assert cli.main(["--stdio-from", str(settings), "--server", "nope"]) == 3
    assert cli.main(["--server", "brightline-crm"]) == 3


def test_cli_stdio_command(tmp_path, capfd, monkeypatch):
    monkeypatch.setenv("FAKE_MCP_RECORD", str(tmp_path / "record.txt"))
    code = cli.main(["--stdio", f'"{sys.executable}" "{FAKE}"'])
    out, _ = capfd.readouterr()
    assert code == 2 and "The AI is given tools that change things" in out
    assert "tools/call" not in _record(tmp_path)


# ---------------------------------------------------------------- wizard
@pytest.fixture
def wizard(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(detect, "_app_files", lambda: [("Claude Desktop", settings)])
    monkeypatch.setattr(detect, "find_servers", lambda: [])
    state = State()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    client = httpx.Client(base_url=base, headers={"X-RF-Token": state.page_token}, timeout=30)
    bodies: list[str] = []

    def call(method, path, **kw):
        r = client.request(method, path, **kw)
        bodies.append(r.text)
        return r

    yield call, bodies
    client.close()
    httpd.shutdown()
    httpd.server_close()


def _run(call, body) -> str:
    job = call("POST", "/api/scan", json=body).json()["job"]
    for _ in range(200):
        st = call("GET", f"/api/status?job={job}").json()
        if st["done"]:
            assert st["error"] is None
            return job
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


def test_wizard_offers_and_scans_a_local_program(wizard, tmp_path):
    call, bodies = wizard
    d = call("GET", "/api/detect").json()
    assert [a["name"] for a in d["apps"]] == ["brightline-web"]
    (entry,) = d["stdio"]
    assert entry["label"] == "Claude Desktop: brightline-crm · runs as a local program"
    assert FAKE not in json.dumps(entry) and "FAKE_MCP_RECORD" not in json.dumps(entry)

    # without the confirmation tick nothing is started; the settings file is still checked
    job = _run(call, {"stdio_ref": entry["ref"]})
    rep = call("GET", f"/api/report.json?job={job}").json()
    assert {f["id"] for f in rep["findings"]} == {"plaintext-db-password", "plaintext-token"}
    assert _record(tmp_path) == []

    job = _run(call, {"stdio_ref": entry["ref"], "stdio_start": True})
    rep = call("GET", f"/api/report.json?job={job}").json()
    call("GET", f"/api/report.html?job={job}")
    assert {"stdio-write-tools", "local-tools-open"} <= {f["id"] for f in rep["findings"]}
    assert rep["not_applicable"]
    assert "tools/call" not in _record(tmp_path) and "tools/list" in _record(tmp_path)

    # only servers the detector found can be started
    assert call("POST", "/api/scan", json={"stdio_ref": "made-up", "stdio_start": True}).status_code == 400
    for b in bodies:
        _assert_clean(b)
