"""NIST AI RMF tags for findings and a summary for the report. The mapping lives in one data
file, data/ai_rmf.json, so it can be corrected without touching code.

The report gives evidence for some AI RMF subcategories; it never says a system meets the
framework (the AI RMF is voluntary guidance with no certification)."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .report import Finding, Report

EVIDENCE = "Evidence"
GAPS = "Gaps to fix"
NOT_CHECKED = "Not checked in this scan"
NOT_COVERED = "Not covered by this tool"


@lru_cache(maxsize=1)
def mapping() -> dict:
    text = resources.files(__package__).joinpath("data/ai_rmf.json").read_text(encoding="utf-8")
    return json.loads(text)


def disclaimer() -> str:
    return mapping()["disclaimer"]


def _order() -> list[str]:
    return [s["id"] for s in mapping()["subcategories"]]


def tags_for(check: str, severity: str | None = None) -> list[str]:
    """Subcategory ids for a finding id or a passed-check name, in the data file's order."""
    m = mapping()
    ids = set(m["checks"].get(check, []))
    if severity:
        ids.update(m["by_severity"].get(severity, []))
    return [i for i in _order() if i in ids]


def finding_tags(f: "Finding") -> list[str]:
    return tags_for(f.id, f.severity)


def summary(report: "Report") -> list[dict]:
    """One row per subcategory: what this scan showed and a status, then the fixed
    'not covered' row."""
    m = mapping()
    gaps: dict[str, list[str]] = {}
    passed: dict[str, list[str]] = {}
    for f in report.sorted_findings():
        for sid in finding_tags(f):
            if f.title not in gaps.setdefault(sid, []):
                gaps[sid].append(f.title)
    for check in report.passed:
        for sid in tags_for(check):
            if check not in passed.setdefault(sid, []):
                passed[sid].append(check)
    anything_ran = bool(report.findings or report.passed)
    severity_ids = {sid for ids in m["by_severity"].values() for sid in ids}

    rows = []
    for sub in m["subcategories"]:
        sid = sub["id"]
        if sid in gaps:
            titles = gaps[sid]
            status = GAPS
            showed = f"{len(titles)} finding(s) to fix: " + "; ".join(titles) + "."
            if sid in severity_ids:
                showed += " Each has a \"how to close it\" step; decide and record your response to each (fix it, accept it, or avoid it)."
        elif sid in m["every_report"]:
            status = EVIDENCE
            showed = m["every_report"][sid].format(target=report.target, started_at=report.started_at)
        elif sid in passed:
            status = EVIDENCE
            showed = "Checked, no problem found: " + ", ".join(passed[sid]) + "."
        elif sid in severity_ids and anything_ran:
            status = EVIDENCE
            showed = "No high-risk findings in the checks that ran."
        else:
            status = NOT_CHECKED
            showed = ("None of the checks for this ran in this scan. Add the MCP address or the database "
                      "login to cover it, or see the checks that could not run.")
        rows.append({"id": sid, "label": sub["label"], "status": status, "showed": showed})

    nc = m["not_covered"]
    rows.append({"id": nc["id"], "label": f"{nc['label']} ({', '.join(nc['subcategories'])})",
                 "status": NOT_COVERED, "showed": nc["showed"]})
    return rows


def to_dict(report: "Report") -> dict:
    return {"source": mapping()["source"], "disclaimer": disclaimer(), "summary": summary(report)}
