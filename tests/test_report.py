import json

from rolefence_scan.report import HIGH, INFO, LOW, MEDIUM, Finding, Report


def _f(id_, sev):
    return Finding(id=id_, title=f"Title {id_}", severity=sev, what="what", why="why", fix="fix",
                   evidence={"n": 1})


def test_empty_report_text_and_worst():
    r = Report(target="http://x/mcp")
    r.ok("open-tool-list")
    r.skip("db checks", "no uri")
    text = r.to_text()
    assert "No problems found" in text
    assert "Checks that passed: open-tool-list" in text
    assert "db checks: no uri" in text
    assert r.worst == INFO


def test_findings_sorted_by_severity_and_counted():
    r = Report(target="t")
    r.add(_f("low1", LOW))
    r.add(_f("high1", HIGH))
    r.add(_f("med1", MEDIUM))
    assert [f.id for f in r.sorted_findings()] == ["high1", "med1", "low1"]
    assert r.worst == HIGH
    text = r.to_text()
    assert "3 finding(s): 1 high, 1 medium, 1 low" in text
    assert text.index("[HIGH]") < text.index("[MEDIUM]") < text.index("[LOW]")
    assert "What we saw:" in text and "Why it matters:" in text and "How to close:" in text
    assert 'Details:        {"n": 1}' in text


def test_json_round_trip():
    r = Report(target="t")
    r.add(_f("x", MEDIUM))
    r.ok("p")
    r.skip("s", "because")
    d = json.loads(r.to_json())
    assert d["target"] == "t"
    assert d["worst"] == MEDIUM
    assert d["findings"][0]["id"] == "x"
    assert d["findings"][0]["evidence"] == {"n": 1}
    assert d["passed"] == ["p"]
    assert d["skipped"] == ["s: because"]
    assert d["started_at"].endswith("+00:00")
