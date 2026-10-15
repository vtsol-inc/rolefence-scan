from pathlib import Path

import pytest

from rolefence_scan.checks.config_checks import collections_in, load_tools, run_config_checks
from rolefence_scan.report import Report

DEMO = Path(__file__).resolve().parents[1] / "demo" / "toolbox"


def _ids(report):
    return {f.id for f in report.findings}


def test_weak_config_lights_up():
    r = Report("weak")
    run_config_checks(r, str(DEMO / "weak.yaml"))
    ids = _ids(r)
    assert {"write-tools", "free-form-queries", "aggregate-not-readonly", "no-row-cap", "whole-documents"} <= ids
    by_id = {f.id: f for f in r.findings}
    assert by_id["write-tools"].evidence["tools"] == ["insert_enquiry"]
    assert set(by_id["free-form-queries"].evidence["find"]) == {"find_customers", "find_enquiries", "find_orders"}
    assert set(by_id["free-form-queries"].evidence["aggregate"]) == {"aggregate_orders", "aggregate_enquiries"}
    assert r.passed == []


@pytest.mark.parametrize("name", ["governed.yaml", "governed_auth.yaml"])
def test_governed_configs_are_clean(name):
    r = Report(name)
    run_config_checks(r, str(DEMO / name))
    assert r.findings == [], [f.id for f in r.findings]
    assert set(r.passed) == {"write-tools", "free-form-queries", "aggregate-not-readonly", "no-row-cap",
                             "whole-documents"}


def test_governed_auth_matches_governed_tools_plus_scopes():
    plain = load_tools(str(DEMO / "governed.yaml"))
    auth = load_tools(str(DEMO / "governed_auth.yaml"))
    assert set(plain) == set(auth)
    for name, spec in auth.items():
        assert spec["authRequired"] == ["demo_keycloak"], name
        expected = "demo:sales" if name.startswith("sales_") else "demo:finance"
        assert spec["scopesRequired"] == [expected], name


def test_collections_in_and_multi_document_shape(tmp_path):
    assert collections_in(load_tools(str(DEMO / "governed.yaml"))) == ["enquiries", "orders", "orders_sales",
                                                                        "supplier_payments"]
    p = tmp_path / "multi.yaml"
    p.write_text("kind: source\nname: s\ntype: mongodb\n---\nkind: tool\nname: t1\ntype: mongodb-find\n"
                 "source: s\ncollection: c1\nfilterPayload: '{{json .f}}'\n")
    tools = load_tools(str(p))
    assert tools["t1"]["kind"] == "mongodb-find"
    r = Report("multi")
    run_config_checks(r, str(p))
    assert {"free-form-queries", "whole-documents"} <= _ids(r)


def test_unreadable_or_empty_config_is_skipped(tmp_path):
    r = Report("missing")
    run_config_checks(r, str(tmp_path / "nope.yaml"))
    assert r.findings == [] and r.skipped and "could not read" in r.skipped[0]
    empty = tmp_path / "empty.yaml"
    empty.write_text("sources: {}\n")
    r2 = Report("empty")
    run_config_checks(r2, str(empty))
    assert r2.skipped == [f"config checks: no tools found in {empty}"]
