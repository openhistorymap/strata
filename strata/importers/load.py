"""Load one site's normalised source into the Strata store.

A site source is a directory ``data/sources/<site>/`` with:

- ``site.json``        id, name, wikidata, licence, attribution, ...
- ``contexts.csv``     one row per stratigraphic unit (``uid`` required)
- ``statements.csv``   stratigraphic statements as recorded: subject, verb, object, source
- ``finds.csv``        optional, finds with the ``context`` they came from

Statements are canonicalised here. Every temporal verb becomes one
``(:Context)-[:ABOVE]->(:Context)`` edge from the later (upper) unit to the
earlier (lower) one; reciprocal records of the same fact merge into that
one edge, which keeps the verbs used and the records that stated it
(``stated_by``), so a contradiction can always be traced to its records.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ..store import Store

# verb -> (relationship type, True if the subject is the upper/later unit)
VERBS: dict[str, tuple[str, bool]] = {
    "above": ("ABOVE", True), "below": ("ABOVE", False),
    "cuts": ("ABOVE", True), "cut_by": ("ABOVE", False),
    "fills": ("ABOVE", True), "filled_by": ("ABOVE", False),
    "seals": ("ABOVE", True), "sealed_by": ("ABOVE", False),
    "equals": ("SAME_AS", True), "equal": ("SAME_AS", True),
    "contemporary": ("SAME_AS", True),
    "abuts": ("ABUTS", True),
    "within": ("WITHIN", True), "contains": ("WITHIN", False),
}
# Canonical verb kept on the edge: "cut_by" is recorded as the cut "cuts".
CANONICAL = {"below": "above", "cut_by": "cuts", "filled_by": "fills", "sealed_by": "seals",
             "contains": "within", "equal": "equals"}
SYMMETRIC = {"SAME_AS", "ABUTS"}

# Period labels that say something about a unit but name no period.
NOT_A_PERIOD = {"uncertain", "mixed", "uncertain / mixed", "cleaning locus", "sterile"}

INT_FIELDS = {"start_year", "end_year"}
FLOAT_FIELDS = {"top_depth", "bottom_depth", "latitude", "longitude"}
BATCH = 2000


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if value in (None, ""):
            continue
        if key in INT_FIELDS:
            value = int(float(value))
        elif key in FLOAT_FIELDS:
            value = float(value)
        out[key] = value
    return out


def _batches(rows: list, size: int = BATCH) -> Iterable[list]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def read_source(source: Path) -> dict[str, Any]:
    """Read a source directory. A ``site.json`` with a ``matrix`` key names a
    Harris-matrix file in the directory, read with harris-mcp instead of CSVs."""
    site = json.loads((source / "site.json").read_text())
    if site.get("matrix"):
        from .harris import matrix_source
        return matrix_source(source / site["matrix"], site, site.get("matrix_format", "auto"))

    def table(name: str) -> list[dict]:
        p = source / f"{name}.csv"
        return [_clean(r) for r in csv.DictReader(p.open())] if p.exists() else []
    return {"site": site, "contexts": table("contexts"), "statements": table("statements"),
            "finds": table("finds")}


def canonical_edges(statements: list[dict]) -> dict[str, list[dict]]:
    """Merge recorded statements into one edge per (type, upper, lower)."""
    edges: dict[tuple[str, str, str], dict] = {}
    for s in statements:
        rel, subject_first = VERBS[s["verb"]]
        a, b = (s["subject"], s["object"]) if subject_first else (s["object"], s["subject"])
        if rel in SYMMETRIC:
            a, b = sorted((a, b))
        if a == b:
            continue
        e = edges.setdefault((rel, a, b), {"a": a, "b": b, "verbs": set(), "stated_by": set(), "sources": set()})
        e["verbs"].add(CANONICAL.get(s["verb"], s["verb"]))
        e["stated_by"].add(s["subject"])
        if s.get("source"):
            e["sources"].add(s["source"])
    out: dict[str, list[dict]] = {}
    for (rel, _, _), e in edges.items():
        out.setdefault(rel, []).append(
            {**e, "verbs": sorted(e["verbs"]), "stated_by": sorted(e["stated_by"]), "sources": sorted(e["sources"])})
    return out


def derive_periods(site_id: str, contexts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Period nodes from the contexts' period labels.

    A label like "Early Bronze Age I / Late Chalcolithic V" names two
    periods (the unit is dated to one or the other). Each period's date
    range is the one most often recorded on units dated to that period
    alone, so the ranges come from the excavators' own records.
    """
    ranges: dict[str, Counter] = {}
    links: list[dict] = []
    for c in contexts:
        label = c.get("period_label")
        if not label or label.strip().lower() in NOT_A_PERIOD:
            continue
        parts = [p.strip() for p in label.split("/") if p.strip()]
        for p in parts:
            ranges.setdefault(p, Counter())
            links.append({"context": c["uid"], "period": f"{site_id}:{slug(p)}",
                          "basis": "tentative" if len(parts) == 1 else "either"})
        if len(parts) == 1 and "start_year" in c and "end_year" in c:
            ranges[parts[0]][(c["start_year"], c["end_year"])] += 1
    periods = []
    for name, counter in ranges.items():
        p = {"uid": f"{site_id}:{slug(name)}", "site": site_id, "name": name}
        if counter:
            (start, end), _ = counter.most_common(1)[0]
            p.update(start_year=start, end_year=end)
        periods.append(p)
    return periods, links


def import_site(store: Store, source: str | Path | dict, *, replace: bool = True) -> dict[str, int]:
    data = source if isinstance(source, dict) else read_source(Path(source))
    site = data["site"]
    sid = site["id"]
    if replace:
        delete_site(store, sid)

    site_props = {k: v for k, v in site.items() if k not in ("location",) and v is not None}
    store.run("MERGE (s:Site {id: $id}) SET s += $props", id=sid, props=site_props)
    if site.get("location"):
        store.run("MATCH (s:Site {id: $id}) SET s.location = point({latitude: $lat, longitude: $lon})",
                  id=sid, lat=site["location"][0], lon=site["location"][1])

    contexts = []
    for c in data["contexts"]:
        c = {**c, "site": sid}
        c.setdefault("label", c["uid"])
        contexts.append(c)
    trenches = sorted({c["trench"] for c in contexts if c.get("trench")})
    store.run("""UNWIND $rows AS t MATCH (s:Site {id: $site})
                 MERGE (tr:Trench {uid: $site + ':' + t}) SET tr.name = t, tr.site = $site
                 MERGE (tr)-[:IN_SITE]->(s)""", rows=trenches, site=sid)

    for batch in _batches(contexts):
        store.run("""
            UNWIND $rows AS r
            CREATE (c:Context)
            SET c = r
            SET c.location = CASE WHEN r.latitude IS NULL THEN null
                                  ELSE point({latitude: r.latitude, longitude: r.longitude}) END
            REMOVE c.latitude, c.longitude""", rows=batch)
    store.run("""MATCH (c:Context {site: $site}) WHERE c.trench IS NOT NULL
                 MATCH (t:Trench {uid: $site + ':' + c.trench}) CREATE (c)-[:IN_TRENCH]->(t)""", site=sid)
    store.run("""MATCH (c:Context {site: $site}) WHERE c.trench IS NULL
                 MATCH (s:Site {id: $site}) CREATE (c)-[:IN_SITE]->(s)""", site=sid)

    edges = canonical_edges(data["statements"])
    for rel, rows in edges.items():
        for batch in _batches(rows):
            store.run(f"""
                UNWIND $rows AS e
                MATCH (a:Context {{uid: e.a}}), (b:Context {{uid: e.b}})
                CREATE (a)-[:{rel} {{verbs: e.verbs, stated_by: e.stated_by, sources: e.sources}}]->(b)""",
                      rows=batch)

    periods, period_links = derive_periods(sid, contexts)
    store.run("""UNWIND $rows AS p MATCH (s:Site {id: $site})
                 CREATE (pe:Period) SET pe = p CREATE (pe)-[:OF_SITE]->(s)""", rows=periods, site=sid)
    for batch in _batches(period_links):
        store.run("""UNWIND $rows AS l
                     MATCH (c:Context {uid: l.context}), (p:Period {uid: l.period})
                     CREATE (c)-[:DATED_TO {basis: l.basis}]->(p)""", rows=batch)

    phases = sorted({c["phase"] for c in contexts if c.get("phase")})
    store.run("""UNWIND $rows AS name MATCH (s:Site {id: $site})
                 CREATE (p:Phase {uid: $site + ':' + name, site: $site, name: name})-[:OF_SITE]->(s)""",
              rows=phases, site=sid)
    store.run("""MATCH (c:Context {site: $site}) WHERE c.phase IS NOT NULL
                 MATCH (p:Phase {uid: $site + ':' + c.phase}) CREATE (c)-[:IN_PHASE]->(p)""", site=sid)

    finds = [{**f, "site": sid} for f in data["finds"]]
    for batch in _batches(finds):
        store.run("""
            UNWIND $rows AS r
            MATCH (c:Context {uid: r.context})
            CREATE (f:Find) SET f = r REMOVE f.context, f.bag, f.taxon_uri
            CREATE (f)-[:FROM {bag: r.bag}]->(c)""", rows=batch)
    taxa = [{"find": f["uid"], "uri": u.strip(), "name": n.strip()}
            for f in finds if f.get("taxon_uri")
            for u, n in zip(f["taxon_uri"].split(";"), f.get("taxon", "").split(";"))]
    for batch in _batches(taxa):
        store.run("""UNWIND $rows AS t
                     MERGE (x:Taxon {uri: t.uri}) ON CREATE SET x.name = t.name
                     WITH x, t MATCH (f:Find {uid: t.find}) CREATE (f)-[:IDENTIFIED_AS]->(x)""", rows=batch)

    return {"contexts": len(contexts), "trenches": len(trenches),
            **{f"edges_{k.lower()}": len(v) for k, v in edges.items()},
            "periods": len(periods), "phases": len(phases), "finds": len(finds), "taxa_links": len(taxa)}


def delete_site(store: Store, site_id: str) -> None:
    """Remove a site and everything recorded in it (contexts, finds, issues, edits...)."""
    store.run("MATCH (f:Find {site: $s}) DETACH DELETE f", s=site_id)
    store.run("MATCH (c:Context {site: $s}) DETACH DELETE c", s=site_id)
    for label in ("Trench", "Period", "Phase", "Issue", "Edit"):
        store.run(f"MATCH (n:{label} {{site: $s}}) DETACH DELETE n", s=site_id)
    store.run("MATCH (s:Site {id: $s}) DETACH DELETE s", s=site_id)
    store.run("MATCH (t:Taxon) WHERE NOT (t)--() DELETE t")
