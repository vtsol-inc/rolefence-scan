from pymongo.errors import OperationFailure

from rolefence_scan.checks.db_checks import check_cross_collection, cross_collection_verdict
from rolefence_scan.report import LOW, MEDIUM, Report

# Brightline Movers demo collections.
ALL = ["customers", "enquiries", "orders", "orders_sales", "supplier_payments"]


def test_whole_database_user_is_not_contained_when_only_system_views_refused():
    reached = ["customers", "orders", "orders_sales", "supplier_payments"]
    f = cross_collection_verdict("enquiries", ALL, reached, ["system.views"])
    assert f.id == "cross-collection"
    assert f.severity == MEDIUM
    assert "whole database" in f.why
    assert f.evidence["refused"] == ["system.views"]


def test_contained_when_a_real_collection_is_refused():
    f = cross_collection_verdict("enquiries", ["customers", "enquiries", "orders_sales"],
                                 ["customers", "orders_sales"], ["supplier_payments", "system.views"])
    assert f.id == "cross-collection-contained"
    assert f.severity == LOW
    assert f.what.endswith("refused on supplier_payments.")
    assert "system.views" not in f.what


def test_contained_message_never_prints_an_empty_list():
    for refused in (["system.views"], ["supplier_payments"], ["supplier_payments", "system.views"]):
        f = cross_collection_verdict("enquiries", ALL, ["customers"], refused)
        assert "on ." not in f.what
        assert ", ." not in f.what and "()" not in f.what


def test_reach_beyond_listed_collections_is_medium():
    f = cross_collection_verdict("orders_sales", ["orders_sales"], ["supplier_payments"], ["system.views"])
    assert f.id == "cross-collection"
    assert f.severity == MEDIUM
    assert "cannot list: supplier_payments" in f.what


def test_nothing_reached_is_no_finding():
    assert cross_collection_verdict("enquiries", ["enquiries"], [], ["orders", "system.views"]) is None
    assert cross_collection_verdict("enquiries", ["enquiries"], [], []) is None


class _Coll:
    def __init__(self, refuse):
        self.refuse = refuse

    def aggregate(self, pipeline):
        target = pipeline[1]["$lookup"]["from"]
        if target in self.refuse:
            raise OperationFailure("not authorized")
        return [{"n": 1}]


class _DB:
    def __init__(self, refuse):
        self.refuse = refuse

    def __getitem__(self, name):
        return _Coll(self.refuse)


def test_check_reports_readwrite_user_as_not_a_boundary():
    r = Report("demo")
    check_cross_collection(r, _DB({"system.views"}), sorted(ALL), [])
    assert [f.id for f in r.findings] == ["cross-collection"]
    assert r.findings[0].severity == MEDIUM


def test_check_reports_role_user_as_contained():
    r = Report("demo")
    check_cross_collection(r, _DB({"supplier_payments", "system.views"}),
                           ["customers", "enquiries", "orders_sales"], ["supplier_payments"])
    assert [f.id for f in r.findings] == ["cross-collection-contained"]
