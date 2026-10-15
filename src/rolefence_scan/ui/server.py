"""The wizard's local server. Standard library only, so `uvx rolefence-scan ui` works offline.

Safety:
- binds to 127.0.0.1 only; nothing is reachable from the network
- every API call must carry a per-start random token that only the served page knows, so a
  web page open in another tab cannot drive the scanner
- tokens and connection strings live in memory for one job and are dropped when it finishes
- local-program servers are offered by reference only: the page sees the app, the server's name,
  the program's base name and the settings file's path, never the command's arguments or its
  settings' values; a program is started only when the page sends stdio_start (the admin ticked
  the confirmation), and only a server the detector itself found
- responses are marked no-store
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlsplit

from ..mcpclient import McpClient, tool_names
from ..scanner import scan
from ..stdio import StdioServer, load_stdio_servers

HOST = "127.0.0.1"
MAX_BODY = 2_000_000


class Job:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.done = False
        self.error: str | None = None
        self.report = None
        self.lock = threading.Lock()

    def event(self, kind: str, payload: dict) -> None:
        with self.lock:
            self.events.append({"kind": kind, **payload})


class State:
    def __init__(self) -> None:
        self.page_token = secrets.token_urlsafe(24)
        self.jobs: dict[str, Job] = {}
        # values found in running servers' environments; the page only ever sees the ref id
        self.secrets: dict[str, str] = {}
        self.config_paths: set[str] = set()
        # local-program servers by ref: (settings file, app, position in that file). Re-read from
        # the file when used, so their settings' values are not held here.
        self.stdio: dict[str, tuple[str, str, int]] = {}
        # database logins inside a local-program server's settings, by ref: (stdio ref, setting name)
        self.stdio_vars: dict[str, tuple[str, str]] = {}

    def stdio_server(self, ref: str) -> StdioServer | None:
        where = self.stdio.get(ref)
        if not where:
            return None
        path, app, i = where
        servers = load_stdio_servers(path, app=app)
        return servers[i] if i < len(servers) else None

    def secret(self, ref: str) -> str | None:
        if ref in self.secrets:
            return self.secrets[ref]
        if ref in self.stdio_vars:
            sref, key = self.stdio_vars[ref]
            srv = self.stdio_server(sref)
            return srv.env.get(key) if srv else None
        return None


def _detect(state: State) -> dict:
    from . import detect

    servers = []
    for s in detect.find_servers():
        env = s.pop("env", {})
        entry = {k: s.get(k) for k in ("kind", "label", "address", "backend", "config_path")}
        entry["vars"] = []
        if s.get("config_path"):
            state.config_paths.add(s["config_path"])
            for name in detect.config_env_refs(s["config_path"]):
                ref = None
                if name in env and env[name].startswith(("mongodb://", "mongodb+srv://")):
                    ref = uuid.uuid4().hex
                    state.secrets[ref] = env[name]
                entry["vars"].append({"name": name, "ref": ref})
        servers.append(entry)
    stdio = []
    positions: dict[str, int] = {}
    for srv in detect.find_app_stdio_servers():
        key = f"{srv.app}\0{srv.settings_path}"
        i = positions.get(key, 0)
        positions[key] = i + 1
        ref = uuid.uuid4().hex
        state.stdio[ref] = (srv.settings_path, srv.app, i)
        entry = {**srv.public(), "ref": ref, "vars": []}
        for name, value in srv.env.items():
            if value.startswith(("mongodb://", "mongodb+srv://")):
                vref = uuid.uuid4().hex
                state.stdio_vars[vref] = (ref, name)
                entry["vars"].append({"name": name, "ref": vref})
        stdio.append(entry)
    return {"servers": servers, "apps": detect.find_app_servers(), "stdio": stdio}


def _run_job(job: Job, params: dict) -> None:
    config_path = None
    try:
        if params.get("config_text"):
            fd, config_path = tempfile.mkstemp(prefix="rolefence-", suffix=".yaml")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(params["config_text"])
        job.report = scan(
            mcp_url=params.get("mcp_url") or None,
            token=params.get("token") or None,
            backend_url=params.get("backend_url") or None,
            db_uri=params.get("db_uri") or None,
            config_path=config_path,
            role_label=params.get("role") or "the token",
            on_event=job.event,
            stdio_servers=[params["stdio"]] if params.get("stdio") else None,
            stdio_start=params["stdio"] if params.get("stdio") and params.get("stdio_start") else None,
        )
    except Exception as e:  # shown to the admin, never swallowed
        job.error = f"{e.__class__.__name__}: {e}"
    finally:
        if config_path:
            try:
                os.unlink(config_path)
            except OSError:
                pass
        for k in ("token", "db_uri", "config_text", "stdio"):
            params.pop(k, None)
        job.done = True


def _test_db(uri: str) -> dict:
    """Ping the database as the given user and say who it is. Read-only."""
    from pymongo import MongoClient
    from pymongo.errors import ConfigurationError, OperationFailure, PyMongoError

    if not uri.startswith("mongodb://") and not uri.startswith("mongodb+srv://"):
        return {"ok": False, "message": "That does not look like a MongoDB login."}
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000)
        try:
            name = client.get_default_database(default=None)
            if name is None:
                return {"ok": False, "message": "The database name is missing."}
            db = client[name.name]
            db.command("ping")
            status = db.command("connectionStatus")
            users = status.get("authInfo", {}).get("authenticatedUsers", [])
            who = users[0]["user"] if users else None
            n = len(db.list_collection_names(authorizedCollections=True, nameOnly=True))
            if who:
                return {"ok": True, "message": f"Logged in as {who}; this user can see {n} collection(s) in {name.name}."}
            return {"ok": True, "message": f"Connected to {name.name} with no login (the database has authentication off)."}
        finally:
            client.close()
    except OperationFailure as e:
        if e.code == 18:
            return {"ok": False, "message": "The database refused the username or password."}
        return {"ok": False, "message": f"The database refused the login: {e.details.get('errmsg', str(e))[:140]}"}
    except ConfigurationError as e:
        return {"ok": False, "message": f"The login details are not valid: {str(e)[:140]}"}
    except PyMongoError as e:
        return {"ok": False, "message": "Nothing answered at that server and port. Is the database running, and reachable from this machine?"}


def make_handler(state: State):
    index_html = resources.files(__package__).joinpath("static/index.html").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "rolefence-scan"

        def log_message(self, fmt, *args):  # quiet; nothing useful for the admin here
            pass

        # -- helpers -------------------------------------------------------
        def _send(self, status: int, body: bytes, ctype: str, download: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj, default=str).encode(), "application/json")

        def _authorised(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-RF-Token", ""), state.page_token)

        def _read_json(self) -> dict | None:
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return None
            if n <= 0 or n > MAX_BODY:
                return None
            try:
                return json.loads(self.rfile.read(n))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None

        def _job(self, query: dict) -> Job | None:
            jid = (query.get("job") or [""])[0]
            return state.jobs.get(jid)

        # -- routes --------------------------------------------------------
        def do_OPTIONS(self):  # refuse cross-origin preflights
            self._send(HTTPStatus.FORBIDDEN, b"", "text/plain")

        def do_GET(self):
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                page = index_html.replace("__PAGE_TOKEN__", state.page_token)
                self._send(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8")
                return
            if not u.path.startswith("/api/"):
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            if not self._authorised():
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad page token; reload the page"})
                return
            job = self._job(q)
            if u.path == "/api/detect":
                try:
                    self._json(HTTPStatus.OK, _detect(state))
                except Exception as e:
                    self._json(HTTPStatus.OK, {"servers": [], "apps": [], "error": f"{e.__class__.__name__}: {e}"})
                return
            if u.path == "/api/status":
                if not job:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "no such job"})
                    return
                with job.lock:
                    events = list(job.events)
                out = {"events": events, "done": job.done, "error": job.error}
                if job.done and job.report:
                    out["summary"] = {"worst": job.report.worst, "findings": len(job.report.findings),
                                      "passed": job.report.passed, "skipped": job.report.skipped}
                self._json(HTTPStatus.OK, out)
                return
            if u.path in ("/api/report.html", "/api/report.json"):
                if not (job and job.done and job.report):
                    self._json(HTTPStatus.NOT_FOUND, {"error": "report not ready"})
                    return
                if u.path.endswith(".html"):
                    self._send(HTTPStatus.OK, job.report.to_html().encode(), "text/html; charset=utf-8",
                               download="rolefence-scan-report.html")
                else:
                    self._send(HTTPStatus.OK, job.report.to_json().encode(), "application/json",
                               download="rolefence-scan-report.json")
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            u = urlsplit(self.path)
            if not u.path.startswith("/api/") or not self._authorised():
                self._json(HTTPStatus.FORBIDDEN, {"error": "bad page token; reload the page"})
                return
            body = self._read_json()
            if body is None:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "bad request body"})
                return
            if u.path == "/api/test":
                url = str(body.get("mcp_url", "")).strip()
                if not url.startswith(("http://", "https://")):
                    self._json(HTTPStatus.OK, {"ok": False, "message": "The address must start with http:// or https://"})
                    return
                c = McpClient(url, token=(body.get("token") or None))
                try:
                    init = c.initialize()
                    if init.status == 0:
                        msg = "Nothing answered at that address. Is the server running, and is the port right?"
                        self._json(HTTPStatus.OK, {"ok": False, "message": msg})
                    elif init.status in (401, 403):
                        self._json(HTTPStatus.OK, {"ok": True, "login": True,
                                                   "message": "The server is there and asks for a login. Good sign."})
                    elif init.ok:
                        n = len(tool_names(c.list_tools()))
                        self._json(HTTPStatus.OK, {"ok": True, "login": False, "tools": n,
                                                   "message": f"The server answered and listed {n} tool(s) without a login."})
                    else:
                        self._json(HTTPStatus.OK, {"ok": False, "message": f"The server answered HTTP {init.status} but did not speak MCP."})
                finally:
                    c.close()
                return
            if u.path == "/api/readconfig":
                path = str(body.get("path", ""))
                if path not in state.config_paths:   # only files the detector itself found
                    self._json(HTTPStatus.FORBIDDEN, {"error": "not a detected config file"})
                    return
                try:
                    with open(path, encoding="utf-8") as fh:
                        self._json(HTTPStatus.OK, {"text": fh.read(MAX_BODY)})
                except OSError as e:
                    self._json(HTTPStatus.OK, {"error": f"could not read the file: {e.strerror}"})
                return
            if u.path == "/api/testdb":
                uri = state.secret(str(body.get("db_ref", ""))) or str(body.get("db_uri", ""))
                self._json(HTTPStatus.OK, _test_db(uri))
                return
            if u.path == "/api/scan":
                params = {k: body.get(k) for k in ("mcp_url", "token", "role", "backend_url", "db_uri", "config_text")}
                if body.get("db_ref"):
                    params["db_uri"] = state.secret(str(body["db_ref"]))
                if body.get("stdio_ref"):
                    srv = state.stdio_server(str(body["stdio_ref"]))   # only servers the detector found
                    if not srv:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "that local program is no longer in its settings file"})
                        return
                    params["stdio"] = srv
                    params["stdio_start"] = body.get("stdio_start") is True
                if not any(params.get(k) for k in ("mcp_url", "backend_url", "db_uri", "config_text", "stdio")):
                    # (db_ref was already resolved into db_uri above)
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "nothing to scan"})
                    return
                jid = uuid.uuid4().hex
                job = Job()
                state.jobs[jid] = job
                threading.Thread(target=_run_job, args=(job, params), daemon=True).start()
                self._json(HTTPStatus.OK, {"job": jid})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    return Handler


def serve_ui(port: int = 8765, open_browser: bool = True) -> None:
    state = State()
    httpd = ThreadingHTTPServer((HOST, port), make_handler(state))
    url = f"http://{HOST}:{httpd.server_address[1]}/"
    print(f"rolefence-scan wizard: {url}  (Ctrl-C to stop; only this machine can reach it)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
