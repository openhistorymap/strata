"""End-to-end tests on small, hand-made excavations.

Each test builds a fresh single-file store in a temporary directory, so the
committed data/strata.rl is never touched.
"""

from __future__ import annotations

import json

import pytest

from strata import queries as Q
from strata.checks import run_checks
from strata.importers.load import import_site
from strata.store import Store

SITE = {"id": "toy", "name": "Toy dig", "license": "https://creativecommons.org/publicdomain/zero/1.0/"}


def ctx(uid, **kw):
    return {"uid": f"toy:{uid}", "label": uid, "path": f"T1/{uid}", "trench": "T1", **kw}


def st(a, verb, b):
    return {"subject": f"toy:{a}", "verb": verb, "object": f"toy:{b}", "source": f"record {a}"}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.rl")
    s.apply_schema()
    yield s
    s.close()


def clean_dig():
    #   1 topsoil
    #   2 fill of pit 3       3 cut of pit, cuts 4
    #   4 floor (MBA)         5 wall, under 4
    #   6 natural
    contexts = [ctx("1", type="Fill"), ctx("2", type="Fill"), ctx("3", type="Pit"),
                ctx("4", type="Floor", period_label="Middle Bronze Age", start_year=-1899, end_year=-1699),
                ctx("5", type="Wall", period_label="Early Bronze Age", start_year=-3099, end_year=-2099),
                ctx("6", type="Natural")]
    statements = [st("1", "above", "2"), st("2", "below", "1"), st("2", "fills", "3"),
                  st("3", "cuts", "4"), st("4", "cut_by", "3"), st("4", "above", "5"),
                  st("5", "above", "6"), st("1", "above", "6")]  # 1 above 6 is redundant
    finds = [{"uid": "f1", "label": "Sherd 1", "category": "Pottery", "context": "toy:4", "bag": "Bag 1"}]
    return {"site": SITE, "contexts": contexts, "statements": statements, "finds": finds}


def test_import_merges_reciprocal_records(store):
    counts = import_site(store, clean_dig())
    assert counts["contexts"] == 6
    # 1>2 is stated twice (by 1 and by 2) but is one edge
    edge = store.one("""MATCH (:Context {label: '1'})-[r:ABOVE]->(:Context {label: '2'})
                        RETURN r.stated_by AS by, r.verbs AS verbs""")
    assert edge == {"by": ["toy:1", "toy:2"], "verbs": ["above"]}
    assert store.value("MATCH (:Context {label: '3'})-[r:ABOVE]->(:Context {label: '4'}) RETURN r.verbs") == ["cuts"]


def test_clean_dig_has_no_issues_and_a_matrix(store):
    import_site(store, clean_dig())
    summary = run_checks(store, "toy")
    assert summary["unordered"] == 0 and summary["contested_edges"] == 0
    assert summary["issues"] == {}
    depth = {r["l"]: r["d"] for r in store.run("MATCH (c:Context) RETURN c.label AS l, c.depth AS d")}
    assert depth == {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5}
    assert summary["redundant_edges"] == 1  # 1 above 6


def test_contradictions_are_contested_and_explained(store):
    dig = clean_dig()
    dig["statements"] = [s for s in dig["statements"] if s != st("1", "above", "6")]
    dig["statements"] += [st("5", "above", "4"),                      # 4 says above 5, 5 says above 4
                          st("4", "above", "1")]                      # closes a long loop 1>2>3>4>1
    import_site(store, dig)
    summary = run_checks(store, "toy")
    assert summary["unordered"] == 0                                  # every unit still gets a row
    assert summary["issues"].get("conflicting_records") == 1
    assert summary["issues"].get("cycle") == 1
    assert store.value("MATCH ()-[r:CONTESTED]->() RETURN count(r)") >= 2
    rel = Q.relation(store, "T1/4", "T1/5")
    assert rel["contested_claims"]


def test_self_contradiction_and_enclosure(store):
    dig = clean_dig()
    dig["statements"] += [st("2", "above", "3"), st("2", "below", "3")]  # one record, both ways
    import_site(store, dig)
    assert run_checks(store, "toy")["issues"].get("self_contradiction") == 1
    dig["statements"] += [st("2", "within", "3")]
    import_site(store, dig)
    assert run_checks(store, "toy")["issues"].get("enclosed") == 1


def test_period_inversion(store):
    dig = clean_dig()
    # the EBA wall now lies above the MBA floor
    dig["statements"] = [s for s in dig["statements"] if s["subject"] != "toy:4" or s["verb"] != "above"]
    dig["statements"] += [st("5", "above", "4"), st("4", "above", "6")]
    import_site(store, dig)
    assert run_checks(store, "toy")["issues"].get("period_inversion") == 1


def test_queries(store):
    import_site(store, clean_dig())
    run_checks(store, "toy")
    assert {u["path"] for u in Q.below(store, "T1/3")["units"]} == {"T1/4", "T1/5", "T1/6"}
    assert Q.relation(store, "T1/1", "T1/5")["a_is"] == "later"
    assert Q.relation(store, "T1/6", "T1/2")["a_is"] == "earlier"
    bracket = Q.date_bracket(store, "T1/3")
    assert bracket["not_earlier_than"]["path"] == "T1/4"
    prov = Q.provenance(store, "Sherd 1")
    assert prov["context"]["path"] == "T1/4" and prov["bag"] == "Bag 1"
    assert prov["sealed_by"] == ["T1/3"]
    with pytest.raises(Q.NotFound):
        Q.resolve(store, "T9/99")


def test_read_never_writes(store):
    import_site(store, clean_dig())
    with pytest.raises(PermissionError):
        store.read("MATCH (c:Context) DETACH DELETE c")
    # a write hidden from the keyword guard is still rolled back
    store.read("CALL { MATCH (c:Context {label: '1'}) RETURN c } RETURN c.label")
    assert store.value("MATCH (c:Context) RETURN count(c)") == 6


def test_harris_matrix_source(store, tmp_path):
    hmdp = tmp_path / "toy.json"
    hmdp.write_text(json.dumps({
        "name": "toy", "profile": "harris-matrix-data-package-v1",
        "contexts": [{"id": "A", "phase": "1"}, {"id": "B", "phase": "1"}, {"id": "C", "phase": "2"}],
        "relations": [{"a": "A", "b": "B", "kind": "above"}, {"a": "C", "b": "B", "kind": "below"},
                      {"a": "A", "b": "C", "kind": "cuts"}],
    }))
    from strata.importers.harris import matrix_source
    import_site(store, matrix_source(hmdp, {"id": "hm", "name": "Harris toy"}))
    summary = run_checks(store, "hm")
    assert summary["issues"] == {}
    assert store.value("MATCH (p:Phase {site: 'hm'}) RETURN count(p)") == 2
    assert [r["l"] for r in store.run("MATCH (c:Context {site: 'hm'}) RETURN c.label AS l ORDER BY c.depth")] == ["A", "B", "C"]


def test_edits_are_checked_logged_and_revertible(store):
    from strata import edits as E
    import_site(store, clean_dig())
    run_checks(store, "toy")
    # 6 (natural) above 1 (topsoil) would make a loop: refused, nothing written
    with pytest.raises(E.Rejected) as err:
        E.propose_relation(store, "T1/6", "T1/1", note="test")
    assert err.value.evidence["chain"][0] == "T1/1"
    assert store.value("MATCH (:Context {label: '6'})-[r:ABOVE]->(:Context {label: '1'}) RETURN count(r)") == 0
    assert store.value("MATCH (e:Edit) RETURN count(e)") == 0
    # a new, consistent relation is accepted and logged
    ok = E.propose_relation(store, "T1/2", "T1/5", verb="above", note="seen in section", author="tester")
    assert ok["accepted"] and ok["checks"]["issues"] == {}
    assert E.history(store, "toy")[0]["op"] == "add_relation"
    E.revert(store, ok["edit"])
    assert store.value("MATCH (:Context {label: '2'})-[r:ABOVE]->(:Context {label: '5'}) RETURN count(r)") == 0


def test_resolve_contested_pair(store):
    from strata import edits as E
    dig = clean_dig()
    dig["statements"] += [st("5", "above", "4")]
    import_site(store, dig)
    assert run_checks(store, "toy")["issues"] == {"conflicting_records": 1}
    issue = Q.issues(store, "toy")[0]
    explained = Q.explain_issue(store, issue["id"])
    assert {c["relation"] for c in explained["claims"]} == {"CONTESTED"}
    out = E.resolve_order(store, "T1/4", "T1/5", note="floor runs over the wall")
    assert out["checks"]["issues"] == {}
    assert store.value("MATCH (:Context {label: '5'})-[r:REJECTED]->(:Context {label: '4'}) RETURN count(r)") == 1
    assert Q.relation(store, "T1/4", "T1/5")["a_is"] == "later"
    E.revert(store, out["edit"])
    assert run_checks(store, "toy")["issues"] == {"conflicting_records": 1}


def test_assign_phase(store):
    from strata import edits as E
    import_site(store, clean_dig())
    run_checks(store, "toy")
    out = E.assign_phase(store, ["T1/2", "T1/4"], "Phase 2")
    assert out["warnings"] and "T1/3" in out["warnings"][0]
    assert store.value("MATCH (:Phase {name: 'Phase 2'})<-[:IN_PHASE]-(c) RETURN count(c)") == 2
