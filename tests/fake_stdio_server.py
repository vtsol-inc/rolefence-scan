"""A tiny local-program MCP server for the tests (Brightline Movers, made up).

Speaks newline-delimited JSON-RPC on stdin/stdout. Lists a few tools, one of which writes.
Appends every method it receives to the file named by FAKE_MCP_RECORD, so a test can check
that tools/call never arrived. Prints its whole environment to stderr on start, so a test can
check the scanner never shows what a program prints there.
"""

import json
import os
import sys

TOOLS = [
    {"name": "list_moves", "description": "List booked moves for a date range.", "inputSchema": {"type": "object"}},
    {"name": "get_quote", "description": "Look up one quote by its number.", "inputSchema": {"type": "object"}},
    {"name": "add_crew_note", "description": "Append a note to a crew's job sheet.", "inputSchema": {"type": "object"}},
]


def record(method: str) -> None:
    path = os.environ.get("FAKE_MCP_RECORD")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(method + "\n")


def reply(rid, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": rid}
    if error:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    sys.stderr.write("starting with " + json.dumps(dict(os.environ)) + "\n")
    sys.stderr.flush()
    sys.stdout.write("a stray log line that is not JSON\n")
    sys.stdout.flush()
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method", "")
        record(method)
        rid = req.get("id")
        if rid is None:
            continue
        if method == "initialize":
            reply(rid, {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                        "serverInfo": {"name": "brightline-fake", "version": "0"}})
        elif method == "tools/list":
            reply(rid, {"tools": TOOLS})
        elif method == "tools/call":
            reply(rid, {"content": [{"type": "text", "text": "done"}], "isError": False})
        else:
            reply(rid, error={"code": -32601, "message": "method not found"})


if __name__ == "__main__":
    main()
