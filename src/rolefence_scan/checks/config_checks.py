"""Checks on a Toolbox config file, when the administrator has one to hand. Reads the file
only. Supports both config shapes: the classic map (sources:/tools:/toolsets:) and the
multi-document stream (kind: source / kind: tool / kind: toolset)."""

from __future__ import annotations

import re

import yaml

from ..report import LOW, MEDIUM, HIGH, Finding, Report

FIND_KINDS = {"mongodb-find", "mongodb-find-one"}
AGG_KINDS = {"mongodb-aggregate"}
WRITE_KINDS = {"mongodb-insert-one", "mongodb-insert-many", "mongodb-update-one", "mongodb-update-many",
               "mongodb-delete-one", "mongodb-delete-many"}
PARAM_ONLY = re.compile(r"^\s*\{\{\s*json\s+\.\w+\s*\}\}\s*$")


def load_tools(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8") as fh:
        docs = [d for d in yaml.safe_load_all(fh) if isinstance(d, dict)]
    tools: dict[str, dict] = {}
    for d in docs:
        if "tools" in d and isinstance(d["tools"], dict):
            for name, spec in d["tools"].items():
                if isinstance(spec, dict):
                    tools[name] = spec
        elif d.get("kind") == "tool" and "name" in d:
            spec = dict(d)
            spec["kind"] = spec.pop("type", spec.get("kind"))
            tools[d["name"]] = spec
    return tools


def collections_in(tools: dict[str, dict]) -> list[str]:
    return sorted({t.get("collection") for t in tools.values() if t.get("collection")})


def run_config_checks(report: Report, path: str) -> None:
    try:
        tools = load_tools(path)
    except (OSError, yaml.YAMLError) as e:
        report.skip("config checks", f"could not read {path}: {e.__class__.__name__}")
        return
    if not tools:
        report.skip("config checks", f"no tools found in {path}")
        return

    writers = [n for n, t in tools.items() if t.get("kind") in WRITE_KINDS]
    free_filter = [n for n, t in tools.items() if t.get("kind") in FIND_KINDS
                   and PARAM_ONLY.match(str(t.get("filterPayload", "")))]
    free_pipeline = [n for n, t in tools.items() if t.get("kind") in AGG_KINDS
                     and PARAM_ONLY.match(str(t.get("pipelinePayload", "")))]
    no_projection = [n for n, t in tools.items() if t.get("kind") in FIND_KINDS and not t.get("projectPayload")]
    not_readonly = [n for n, t in tools.items() if t.get("kind") in AGG_KINDS and not t.get("readOnly")]
    no_limit = [n for n, t in tools.items() if t.get("kind") in AGG_KINDS
                and "$limit" not in str(t.get("pipelinePayload", ""))]

    if writers:
        report.add(Finding(
            id="write-tools", title="Write tools are defined", severity=HIGH,
            what=f"{len(writers)} tool(s) insert, update or delete: {', '.join(writers[:10])}.",
            why="A model with a write tool will use it; one bad instruction changes real data.",
            fix="Remove write tools from any toolset a model reaches, or route writes through a human "
                "approval step outside the question server.",
            evidence={"tools": writers[:20]},
        ))
    if free_filter or free_pipeline:
        report.add(Finding(
            id="free-form-queries", title="Tools accept any query from the model", severity=HIGH,
            what=f"{len(free_filter)} find tool(s) take the whole filter and {len(free_pipeline)} aggregate tool(s) "
                 "take the whole pipeline from the caller.",
            why="A 'fixed question' tool limits what can be asked; a free-form one lets the model write any "
                "query, including $lookup into other collections, $where, or $out.",
            fix="Write each tool as a fixed query with typed parameters for the values only.",
            evidence={"find": free_filter[:10], "aggregate": free_pipeline[:10]},
        ))
    if not_readonly:
        report.add(Finding(
            id="aggregate-not-readonly", title="Aggregate tools are not marked read-only", severity=MEDIUM,
            what=f"{len(not_readonly)} aggregate tool(s) have no readOnly: true: {', '.join(not_readonly[:10])}.",
            why="Without it, a pipeline may end in $out or $merge and write a new collection (if the database "
                "user allows writes).",
            fix="Add readOnly: true to every mongodb-aggregate tool.",
            evidence={"tools": not_readonly[:20]},
        ))
    if no_limit:
        report.add(Finding(
            id="no-row-cap", title="Aggregate tools have no row cap", severity=MEDIUM,
            what=f"{len(no_limit)} aggregate tool(s) have no $limit stage: {', '.join(no_limit[:10])}.",
            why="Find tools cap rows; aggregate tools return the whole collection unless the pipeline limits it.",
            fix="End every fixed pipeline with a $limit stage.",
            evidence={"tools": no_limit[:20]},
        ))
    if no_projection:
        report.add(Finding(
            id="whole-documents", title="Find tools return whole documents", severity=LOW,
            what=f"{len(no_projection)} find tool(s) have no projectPayload: {', '.join(no_projection[:10])}.",
            why="Every field in the record reaches the model, including ones nobody meant to share.",
            fix="Add a projectPayload that lists only the fields the role needs.",
            evidence={"tools": no_projection[:20]},
        ))
    for check, bad in (("write-tools", writers), ("free-form-queries", free_filter + free_pipeline),
                       ("aggregate-not-readonly", not_readonly), ("no-row-cap", no_limit),
                       ("whole-documents", no_projection)):
        if not bad:
            report.ok(check)
