import json
import re
from pathlib import Path

from rolefence_scan import ai_rmf
from rolefence_scan.report import HIGH, LOW, MEDIUM, Finding, Report

CHECKS = Path(__file__).resolve().parents[1] / "src" / "rolefence_scan" / "checks"


def _f(id_, sev):
    return Finding(id=id_, title=f"Title {id_}", severity=sev, what="w", why="y", fix="x")


def _rows(r):
    return {row["id"]: row for row in ai_rmf.summary(r)}


def test_mapping_uses_only_known_subcategories():
    m = ai_rmf.mapping()
    known = {s["id"] for s in m["subcategories"]}
    assert known == {"GOVERN 1.6", "GOVERN 6.1", "MEASURE 2.7", "MEASURE 2.8", "MEASURE 2.10",
                     "MANAGE 1.3", "MANAGE 3.1", "MANAGE 4.1"}
    for check, ids in m["checks"].items():
        assert ids and set(ids) <= known, check
    for ids in m["by_severity"].values():
        assert set(ids) <= known
    assert set(m["every_report"]) <= known
    for s in m["subcategories"]:
        assert s["label"] and s["official"]


def test_every_check_in_the_code_is_mapped():
    src = "\n".join(p.read_text() for p in CHECKS.glob("*.py"))
    names = set(re.findall(r'\bid="([a-z0-9-]+)"', src))
    names |= set(re.findall(r'report\.ok\("([a-z0-9-]+)"\)', src))
    names |= {"tool-list-filtered", "scopes-advertised", "default-toolset", "write-tools",
              "free-form-queries", "aggregate-not-readonly", "no-row-cap", "whole-documents"}
    missing = names - set(ai_rmf.mapping()["checks"])
    assert not missing, f"add these to data/ai_rmf.json: {sorted(missing)}"


def test_tags_follow_the_mapping_and_severity():
    assert ai_rmf.tags_for("whole-documents") == ["MEASURE 2.10"]
    assert ai_rmf.tags_for("write-tools", HIGH) == ["MEASURE 2.7", "MANAGE 1.3"]
    assert ai_rmf.tags_for("write-tools", MEDIUM) == ["MEASURE 2.7"]
    assert ai_rmf.tags_for("no-such-check") == []


def test_summary_statuses():
    r = Report(target="http://127.0.0.1:5000/mcp")
    r.add(_f("whole-documents", LOW))
    r.ok("writes-allowed")
    r.skip("open-tool-list", "down")
    rows = _rows(r)
    assert rows["MEASURE 2.10"]["status"] == ai_rmf.GAPS
    assert "Title whole-documents" in rows["MEASURE 2.10"]["showed"]
    assert rows["MEASURE 2.7"]["status"] == ai_rmf.EVIDENCE
    assert "writes-allowed" in rows["MEASURE 2.7"]["showed"]
    assert rows["MEASURE 2.8"]["status"] == ai_rmf.NOT_CHECKED
    assert rows["MANAGE 1.3"]["status"] == ai_rmf.EVIDENCE      # no high findings
    assert rows["GOVERN 1.6"]["status"] == ai_rmf.EVIDENCE
    assert "http://127.0.0.1:5000/mcp" in rows["GOVERN 1.6"]["showed"]
    assert r.started_at in rows["MANAGE 4.1"]["showed"]
    last = ai_rmf.summary(r)[-1]
    assert last["status"] == ai_rmf.NOT_COVERED
    assert "Accuracy, bias and model testing" in last["label"]


def test_high_finding_is_a_gap_for_manage_1_3():
    r = Report(target="t")
    r.add(_f("db-no-auth", HIGH))
    rows = _rows(r)
    assert rows["MANAGE 1.3"]["status"] == ai_rmf.GAPS
    assert rows["MEASURE 2.8"]["status"] == ai_rmf.GAPS


def test_nothing_ran_means_not_checked():
    r = Report(target="t")
    r.skip("database checks", "could not connect")
    rows = _rows(r)
    assert rows["MANAGE 1.3"]["status"] == ai_rmf.NOT_CHECKED
    assert rows["MEASURE 2.7"]["status"] == ai_rmf.NOT_CHECKED


def test_reports_carry_tags_summary_and_disclaimer():
    r = Report(target="t")
    r.add(_f("write-tools", HIGH))
    r.ok("whole-documents")
    text, page, doc = r.to_text(), r.to_html(), r.to_json()
    assert "   AI RMF:         MEASURE 2.7, MANAGE 1.3" in text
    assert "NIST AI RMF: what this scan gives evidence for" in text
    for out in (text, page, doc):
        assert "it does not certify compliance" in out
        assert not re.search(r"(?i)\b(compliant|certified)\b", out)
    assert "<b>AI RMF</b>MEASURE 2.7, MANAGE 1.3" in page
    d = json.loads(doc)
    assert d["findings"][0]["ai_rmf"] == ["MEASURE 2.7", "MANAGE 1.3"]
    assert d["ai_rmf"]["disclaimer"] == ai_rmf.disclaimer()
    assert [row["status"] for row in d["ai_rmf"]["summary"]][-1] == ai_rmf.NOT_COVERED
