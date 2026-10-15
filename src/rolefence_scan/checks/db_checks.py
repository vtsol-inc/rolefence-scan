"""Checks made as the database user the question server uses. Read-only: the scanner reads
the user's privileges and runs small read queries; it never inserts, updates or deletes."""

from __future__ import annotations

from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

from ..report import HIGH, LOW, MEDIUM, Finding, Report

WRITE_ACTIONS = {"insert", "update", "remove", "anyAction", "createCollection", "dropCollection",
                 "dropDatabase", "createIndex", "dropIndex", "bypassDocumentValidation", "renameCollectionSameDB"}
WRITE_ROLES = {"readWrite", "readWriteAnyDatabase", "dbOwner", "dbAdmin", "dbAdminAnyDatabase", "root",
               "userAdmin", "userAdminAnyDatabase", "clusterAdmin"}


def _connect(uri: str) -> MongoClient:
    return MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)


def _db_name(client: MongoClient, uri: str) -> str:
    name = client.get_default_database(default=None)
    if name is not None:
        return name.name
    raise ValueError("the connection string must name a database (…/dbname?authSource=…)")


def _readable_collections(db) -> list[str]:
    try:
        names = db.list_collection_names(authorizedCollections=True, nameOnly=True)
    except OperationFailure:
        return []
    return sorted(n for n in names if not n.startswith("system."))


def check_writes_allowed(report: Report, db, dbname: str) -> None:
    try:
        status = db.command("connectionStatus", showPrivileges=True)
    except PyMongoError as e:
        report.skip("writes-allowed", f"connectionStatus failed: {e.__class__.__name__}")
        return
    info = status.get("authInfo", {})
    roles = [f"{r.get('role')}@{r.get('db')}" for r in info.get("authenticatedUserRoles", [])]
    risky_roles = [r for r in roles if r.split("@")[0] in WRITE_ROLES]
    risky_privs = []
    for p in info.get("authenticatedUserPrivileges", []):
        res = p.get("resource", {})
        if res.get("db") in (dbname, "") or res.get("cluster") or res.get("anyResource"):
            acts = sorted(set(p.get("actions", [])) & WRITE_ACTIONS)
            if acts:
                risky_privs.append({"resource": res, "actions": acts})
    if not roles and not info.get("authenticatedUsers"):
        report.add(Finding(
            id="db-no-auth", title="The database accepts connections without a login", severity=HIGH,
            what="The connection was accepted with no authenticated user.",
            why="Without authentication, MongoDB ignores users, roles and views: nothing at the data layer "
                "limits what the AI can read or write.",
            fix="Turn on security.authorization in mongod.conf, create one read-only user per role, and "
                "restart the database.",
        ))
        return
    if risky_roles or risky_privs:
        report.add(Finding(
            id="writes-allowed", title="The AI's database user can write", severity=HIGH,
            what=f"The user the question server connects with holds write rights on {dbname}.",
            why="A tool mistake, a prompt injection, or a pipeline with $out/$merge can change or delete "
                "data. Read-only is the only safe default for a model.",
            fix="Create a user with a custom read-only role (find on the exact collections or views the "
                "role needs) and point the question server at it.",
            evidence={"roles": risky_roles[:10], "privileges": risky_privs[:10]},
        ))
    else:
        report.ok("writes-allowed")


def check_server_side_js(report: Report, db, collections: list[str]) -> None:
    if not collections:
        report.skip("server-side-js", "no readable collection to test on")
        return
    coll = db[collections[0]]
    ran = []
    try:
        out = list(coll.aggregate([
            {"$limit": 1},
            {"$project": {"_id": 0, "x": {"$function": {"body": "function() { return 42; }", "args": [], "lang": "js"}}}},
        ]))
        if out and out[0].get("x") == 42:
            ran.append("$function")
    except OperationFailure:
        pass
    try:
        if coll.count_documents({"$where": "true"}, limit=1) >= 0:
            ran.append("$where")
    except OperationFailure:
        pass
    if ran:
        report.add(Finding(
            id="server-side-js", title="Server-side JavaScript runs for the AI's database user", severity=HIGH,
            what=f"{' and '.join(ran)} executed on {collections[0]}.",
            why="A model can run arbitrary code inside the database: slow it down with a loop, read data "
                "in ways the tool never intended, and bypass the shape of a fixed query.",
            fix="Set security.javascriptEnabled: false in mongod.conf (needs a restart), and keep aggregate "
                "tools fixed rather than free-form.",
            evidence={"ran": ran},
        ))
    else:
        report.ok("server-side-js")


def check_cross_collection(report: Report, db, readable: list[str], hinted: list[str]) -> None:
    """Can a pipeline that starts on one collection pull rows from another?"""
    if not readable:
        report.skip("cross-collection", "no readable collection to start from")
        return
    start = readable[0]
    targets = sorted(set(readable) | set(hinted) | {"system.views"})
    reached, refused = [], []
    for t in targets:
        if t == start:
            continue
        try:
            out = list(db[start].aggregate([
                {"$limit": 1},
                {"$lookup": {"from": t, "pipeline": [{"$limit": 1}, {"$project": {"_id": 1}}], "as": "x"}},
                {"$project": {"_id": 0, "n": {"$size": "$x"}}},
            ]))
            if out and out[0].get("n", 0) > 0:
                reached.append(t)
        except OperationFailure:
            refused.append(t)
    finding = cross_collection_verdict(start, readable, reached, refused)
    if finding:
        report.add(finding)
    else:
        report.ok("cross-collection")


# Often refused even for a user that can read every collection, so a refusal here says
# nothing about whether the role is contained.
NOT_A_BOUNDARY = {"system.views"}


def cross_collection_verdict(start: str, readable: list[str], reached: list[str],
                             refused: list[str]) -> Finding | None:
    """Turn the $lookup results into a finding; None means no other collection was reached."""
    beyond = [t for t in reached if t not in readable]
    real_refused = [t for t in refused if t not in NOT_A_BOUNDARY]
    if beyond:
        return Finding(
            id="cross-collection", title="A tool's collection is not a boundary", severity=MEDIUM,
            what=f"From {start}, $lookup returned rows from collections the user cannot list: {', '.join(beyond)}.",
            why="A tool that is 'about enquiries' can still read any other collection the database user "
                "may read. Fixed queries help; the real limit is the database user's rights.",
            fix="Give each role a database user that can read only its own collections or views, and keep "
                "aggregate tools fixed (no caller-supplied pipeline).",
            evidence={"from": start, "reached": reached[:20], "refused": refused[:20]},
        )
    if reached and real_refused:
        return Finding(
            id="cross-collection-contained", title="Cross-collection reach is contained by the database role", severity=LOW,
            what=f"From {start}, $lookup reached only the role's own collections ({', '.join(reached)}) "
                 f"and was refused on {', '.join(real_refused)}.",
            why="Good: the database user, not the tool, is the boundary. A tool still reads any collection "
                "in that set, so keep pipelines fixed.",
            fix="Nothing to change at the database. Keep aggregate tools fixed (no caller-supplied pipeline).",
            evidence={"from": start, "reached": reached[:20], "refused": refused[:20]},
        )
    if reached:
        return Finding(
            id="cross-collection", title="A tool's collection is not a boundary", severity=MEDIUM,
            what=f"From {start}, $lookup returned rows from every other collection: {', '.join(reached)}.",
            why="The database user can read the whole database, so any tool can read any collection.",
            fix="Give each role a database user that can read only its own collections or views.",
            evidence={"from": start, "reached": reached[:20], "refused": refused[:20]},
        )
    return None


def check_leaks(report: Report, db, collections: list[str]) -> None:
    if not collections:
        report.skip("leaks-in-errors", "no readable collection to test on")
        return
    coll = db[collections[0]]
    leaks = []
    try:
        out = list(coll.aggregate([{"$collStats": {"storageStats": {}}}]))
        if out and "host" in out[0]:
            leaks.append("$collStats returns the server's host name and collection sizes")
    except OperationFailure:
        pass
    marker = "rolefence_probe_7f3a"
    try:
        list(coll.find({"$badOperator": marker}, limit=1))
    except OperationFailure as e:
        if marker in str(e):
            leaks.append("error messages echo the full query back")
    if leaks:
        report.add(Finding(
            id="leaks-in-errors", title="Small leaks: host name and full queries in replies", severity=LOW,
            what="; ".join(leaks) + ".",
            why="These go to the model, into the chat, and possibly into the AI vendor's logs. They tell "
                "an outsider what your servers are called and how big the data is.",
            fix="Keep aggregate tools fixed so $collStats cannot be requested; put a gateway in front that "
                "rewrites database errors to a short message.",
            evidence={"collection": collections[0]},
        ))
    else:
        report.ok("leaks-in-errors")


def run_db_checks(report: Report, uri: str, hinted_collections: list[str] | None = None) -> None:
    try:
        client = _connect(uri)
        dbname = _db_name(client, uri)
        db = client[dbname]
        db.command("ping")
    except (PyMongoError, ValueError) as e:
        report.skip("database checks", f"could not connect: {e.__class__.__name__}: {str(e)[:120]}")
        return
    try:
        readable = _readable_collections(db)
        check_writes_allowed(report, db, dbname)
        check_server_side_js(report, db, readable)
        check_cross_collection(report, db, readable, hinted_collections or [])
        check_leaks(report, db, readable)
    finally:
        client.close()
