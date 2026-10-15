"""Sorting found servers into database / well-known tool / unknown, and showing each server
once. Uses a made-up known list (Brightline Movers), never the shipped one's names."""

import json
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest

from rolefence_scan import catalog
from rolefence_scan.catalog import DATABASE, TOOL, UNKNOWN, classify
from rolefence_scan.ui import detect
from rolefence_scan.ui.server import State, make_handler

KNOWN = {
    "databases": [{"name": "Brightline Ledger", "match": ["brightline-ledger-mcp", "ledger.brightline-movers.example"]}],
    "tools": [{"name": "Brightline Route Planner",
               "match": ["@brightline/route-mcp", "routes.brightline-movers.example", "route-helper"]}],
}
SECRET = "Fake-Secret-Crates-77"
SECRET_HOST = "db.brightline-movers.example"


@pytest.fixture(autouse=True)
def made_up_catalog(monkeypatch):
    monkeypatch.setattr(catalog, "known_servers", lambda: KNOWN)


def test_shipped_list_is_well_formed():
    doc = json.loads((catalog.resources.files("rolefence_scan") / "data/known_servers.json").read_text())
    for group in ("databases", "tools"):
        assert doc[group], group
        for e in doc[group]:
            assert isinstance(e["name"], str) and e["name"]
            assert e["match"] and all(isinstance(p, str) and p == p.lower() for p in e["match"])


@pytest.mark.parametrize("kw, expected", [
    (dict(command="/opt/brightline/bin/brightline-ledger-mcp"), (DATABASE, "Brightline Ledger")),
    (dict(command="npx", args=["-y", "brightline-ledger-mcp@2.1.0"]), (DATABASE, "Brightline Ledger")),
    (dict(url="https://ledger.brightline-movers.example/mcp"), (DATABASE, "Brightline Ledger")),
    (dict(command="node", args=["crm.js"], env={"CRM_URI": f"postgres://ai:{SECRET}@{SECRET_HOST}/crm"}), (DATABASE, None)),
    (dict(command="node", args=["crm.js"], env={"MONGO_HOST": "x"}), (DATABASE, None)),
    (dict(command="node", args=["crm.js"], env={"DB_NAME": "x"}), (DATABASE, None)),
    (dict(command="node", args=["--uri", "mysql://127.0.0.1/moves"]), (DATABASE, None)),
    (dict(command="npx", args=["-y", "@brightline/route-mcp@latest"]), (TOOL, "Brightline Route Planner")),
    (dict(command="npx", args=["mcp-remote", "https://routes.brightline-movers.example/sse"]), (TOOL, "Brightline Route Planner")),
    (dict(url="https://eu.routes.brightline-movers.example/mcp"), (TOOL, "Brightline Route Planner")),
    (dict(command="docker", args=["run", "-i", "ghcr.example/brightline/route-helper:1.0"]), (TOOL, "Brightline Route Planner")),
    (dict(command="npx", args=["-y", "@brightline/route-mcp"], env={"MONGODB_URI": "mongodb://x"}), (DATABASE, None)),
    (dict(command="node", args=["quotes.js"], env={"LOG_LEVEL": "debug", "DEBUG": "1"}), (UNKNOWN, None)),
    (dict(url="https://notroutes.brightline-movers.example/mcp"), (UNKNOWN, None)),
    (dict(command="route-helper-extra"), (UNKNOWN, None)),
])
def test_classify(kw, expected):
    assert classify(**kw) == expected


# ---------------------------------------------------------------- detection on made-up settings
def _write(path, servers):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


@pytest.fixture
def settings(tmp_path, monkeypatch):
    route = {"command": "npx", "args": ["-y", "@brightline/route-mcp@latest"]}
    crm_plain = {"command": "node", "args": ["/srv/brightline/crm.js"]}
    crm_secret = {**crm_plain, "env": {"CRM_URI": f"postgres://crm_ai:{SECRET}@{SECRET_HOST}:5432/crm"}}
    project = _write(tmp_path / "project/.mcp.json", {
        "route-planner": route, "crm": crm_plain,
        "status-board": {"url": "https://status.brightline-movers.example/mcp"}})
    home = _write(tmp_path / "home/.claude.json", {"route-planner": route})
    cursor = _write(tmp_path / "home/.cursor/mcp.json", {
        "route-planner": route, "crm": crm_secret, "quotes": {"command": "python3", "args": ["quotes.py"]},
        "status-board": {"url": "https://status.brightline-movers.example/mcp"}})
    files = [("Claude Code (this folder)", project), ("Claude Code", home), ("Cursor", cursor)]
    monkeypatch.setattr(detect, "_app_files", lambda: files)
    monkeypatch.setattr(detect, "find_servers", lambda: [])
    return cursor


def test_groups_and_duplicates(settings):
    stdio = {g["servers"][0].name: g for g in detect.find_app_stdio_groups()}
    assert set(stdio) == {"route-planner", "crm", "quotes"}             # route-planner was listed 3 times
    assert stdio["route-planner"]["apps"] == ["Claude Code", "Cursor"]
    assert len(stdio["route-planner"]["servers"]) == 3
    assert (stdio["route-planner"]["group"], stdio["route-planner"]["known"]) == (TOOL, "Brightline Route Planner")
    assert stdio["crm"]["apps"] == ["Claude Code", "Cursor"]
    assert stdio["crm"]["group"] == DATABASE                            # the Cursor copy holds a login
    assert stdio["quotes"]["group"] == UNKNOWN

    (web,) = detect.find_app_servers()
    assert web["apps"] == ["Claude Code", "Cursor"] and web["group"] == UNKNOWN
    assert web["label"] == "Claude Code, Cursor: status-board"


@pytest.fixture
def wizard(settings):
    state = State()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    client = httpx.Client(base_url=f"http://127.0.0.1:{httpd.server_address[1]}",
                          headers={"X-RF-Token": state.page_token}, timeout=30)
    yield client
    client.close()
    httpd.shutdown()
    httpd.server_close()


def test_wizard_detect_is_grouped_and_never_shows_a_secret(wizard, settings):
    r = wizard.get("/api/detect")
    bodies = [r.text]
    d = r.json()
    rows = {e["name"]: e for e in d["stdio"]}
    assert len(d["stdio"]) == 3 and len(d["apps"]) == 1
    assert rows["crm"]["group"] == "database" and rows["crm"]["app"] == "Claude Code, Cursor"
    assert rows["crm"]["label"] == "Claude Code, Cursor: crm · runs as a local program"
    assert rows["route-planner"]["group"] == "tool" and rows["quotes"]["group"] == "unknown"
    assert "/srv/brightline/crm.js" not in r.text

    # the crm row stands for both copies: the secret check covers the Cursor one too, nothing is started
    job = wizard.post("/api/scan", json={"stdio_ref": rows["crm"]["ref"]}).json()["job"]
    for _ in range(100):
        st = wizard.get(f"/api/status?job={job}")
        bodies.append(st.text)
        if st.json()["done"]:
            break
        time.sleep(0.1)
    rep = wizard.get(f"/api/report.json?job={job}")
    bodies += [rep.text, wizard.get(f"/api/report.html?job={job}").text]
    (f,) = rep.json()["findings"]
    assert f["id"] == "plaintext-db-password" and f["evidence"]["settings_file"] == str(settings)
    for b in bodies:
        assert SECRET not in b and SECRET_HOST not in b and "crm_ai" not in b
