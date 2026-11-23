"""Every matrix format Strata reads gives the same graph for the same dig.

The fixtures in tests/fixtures/matrices/ all record one invented excavation
(see the README there); each reader must produce the same canonical edges.
"""

import json
from pathlib import Path

import pytest

from strata.checks import run_checks
from strata.convert import SiteExists, convert, import_matrix
from strata.importers.load import canonical_edges
from strata.importers.matrices import detect, read, to_source
from strata.importers.verbs import verb
from strata.store import Store

FIX = Path(__file__).parent / "fixtures" / "matrices"

# the farm dig, canonical: ABOVE upper->lower with the verbs used, SAME_AS and ABUTS pairs
EXPECTED = {
    "ABOVE": {("1", "2"): ["above"], ("1", "4"): ["above"], ("4", "3"): ["fills"], ("3", "5"): ["cuts"],
              ("5", "7"): ["above"], ("6", "7"): ["above"], ("2", "7"): ["above"]},
    "SAME_AS": {("5", "6"): ["equals"]},
    "ABUTS": {("2", "5"): ["abuts"]},
}
FILES = sorted(p.name for p in FIX.iterdir() if p.name != "README.md")


def canonical(path: Path) -> dict:
    m, _ = read(path)
    src = to_source(m, {"id": "farm"}, source_file=path.name, fmt="x")
    out = {}
    for rel, edges in canonical_edges(src["statements"]).items():
        out[rel] = {(e["a"].removeprefix("farm:").split("/")[-1], e["b"].removeprefix("farm:").split("/")[-1]):
                    e["verbs"] for e in edges}
    return out


# LST only knows above / below / contemporary / equal: no cuts, fills or abuts
LST = {"ABOVE": {k: ["above"] for k in EXPECTED["ABOVE"]}, "SAME_AS": EXPECTED["SAME_AS"]}


@pytest.mark.parametrize("name", FILES)
def test_every_format_reads_the_same_dig(name):
    assert canonical(FIX / name) == (LST if name.endswith(".lst") else EXPECTED)


@pytest.mark.parametrize("name,fmt", [
    ("farm.dot", "dot"), ("farm.graphml", "graphml"), ("farm-relations.csv", "edges"),
    ("farm.lst", "lst"), ("farm-pyarchinit.csv", "pyarchinit"), ("farm-pyarchinit.sqlite", "pyarchinit"),
    ("farm-field.csv", "field"), ("farm-field.jsonl", "field"),
])
def test_format_is_detected(name, fmt):
    assert detect(FIX / name).name == fmt


def test_relation_words_in_many_languages():
    assert [verb(w) for w in ["Copre", "Tagliato da", "geschnitten von", "recouvert par", "isCutBy",
                              "relations.isContemporaryWith", "equal to:", "Si appoggia a"]] == \
        ["above", "cut_by", "cut_by", "below", "cut_by", "contemporary", "equals", "abuts"]
    assert verb("cut") is None and verb("fill") is None      # unit types, not relations


def test_unknown_words_are_reported_not_guessed(tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("from,relation,to\n1,above,2\n2,near,3\n")
    m, _ = read(f)
    assert len(m.relations) == 1 and m.summary()["skipped_relation_words"] == {"near": 1}


def test_lst_groups_and_extended_form(tmp_path):
    f = tmp_path / "x.lst"
    f.write_text("TST::G1\n    Unit class: group\nTST::2\n    below: TST::3\n    above: TST::1\n"
                 "    part of: TST::G1\n    Unit type: pit\n    First year: -1200\nTST::3\n    above: TST::2\n")
    m, f_ = read(f)
    assert f_.name == "lst" and "TST::G1" not in m.units
    assert m.units["TST::2"] == {"label": "2", "type": "pit", "start_year": -1200, "group": "TST::G1"}
    assert sorted((r["a"], r["verb"], r["b"]) for r in m.relations) == [
        ("TST::1", "above", "TST::2"), ("TST::2", "above", "TST::3"), ("TST::3", "below", "TST::2")]


def test_field_keeps_only_stratigraphy():
    m, _ = read(FIX / "farm-field.csv")
    assert "A" not in m.units and m.units["1"]["group"] == "A"
    assert m.summary()["skipped_relation_words"] == {"borders": 1}


def test_pyarchinit_areas_and_periods():
    m, _ = read(FIX / "farm-pyarchinit.sqlite")
    assert m.units["2/5"]["phase"] == "2-1" and m.units["1/2"]["type"] == "USM"
    assert m.units["1/1"]["label"] == "US 1" and m.units["1/1"]["period"] == "Periodo 3"


def test_reversed_drawing(tmp_path):
    f = tmp_path / "up.dot"
    f.write_text("digraph { natural -> floor -> topsoil }")
    m, _ = read(f, reverse=True)
    assert [(r["a"], r["b"]) for r in m.relations] == [("floor", "natural"), ("topsoil", "floor")]


def test_import_checks_and_refuses_to_overwrite(tmp_path):
    with Store(tmp_path / "s.rl") as store:
        store.apply_schema()
        out = import_matrix(store, FIX / "farm.dot", site="farm")
        assert out["format"] == "dot" and out["checks"]["issues"] == {}
        assert store.value("MATCH (c:Context {site: 'farm'}) RETURN count(c)") == 7
        assert store.value("MATCH (c:Context {uid: 'farm:7'}) RETURN c.depth") == 4
        assert store.value("MATCH (:Context {uid: 'farm:1'})-[:IN_TRENCH]->(t:Trench) RETURN t.name") == "Area A"
        with pytest.raises(SiteExists):
            import_matrix(store, FIX / "farm.graphml", site="farm")
        import_matrix(store, FIX / "farm.graphml", site="farm", replace=True)
        assert store.value("MATCH (s:Site {id: 'farm'}) RETURN s.source_format") == "graphml"


def test_contradiction_in_a_matrix_is_found(tmp_path):
    f = tmp_path / "loop.csv"
    f.write_text("from,relation,to\n1,above,2\n2,above,3\n3,cuts,1\n")
    with Store(tmp_path / "s.rl") as store:
        store.apply_schema()
        assert import_matrix(store, f)["checks"]["issues"] != {}


def test_convert_writes_a_viewer_bundle(tmp_path):
    out = convert(FIX / "farm.lst", tmp_path / "farm.strata.json", store_out=tmp_path / "farm.rl")
    bundle = json.loads((tmp_path / "farm.strata.json").read_text())
    assert bundle["format"] == "strata-web" and bundle["dataset"]["id"] == "farm"
    assert [p["meta"]["part"] for p in bundle["parts"]] == ["core", "finds"]
    assert out["checks"]["issues"] == {} and (tmp_path / "farm.rl").stat().st_size > 0
