"""Stratigraphic checks, run as Cypher inside the store.

``run_checks`` recomputes a site's matrix structure and writes what it finds
back into the graph, so the results travel with the file:

- ABOVE edges caught in a contradiction are set aside: retyped to
  ``:CONTESTED``, keeping every property, so the matrix stays a DAG and the
  disputed claims stay visible. Each run starts by restoring them to ABOVE.
- every Context gets ``depth``: its row in the Harris matrix (0 = top),
  computed by peeling the ABOVE graph layer by layer (Kahn's algorithm, one
  Cypher statement per layer). A unit that still cannot be placed reveals a
  longer loop, whose closing edge is contested in turn.
- every ABOVE edge implied by a longer path gets ``redundant = true``
  (the transitive reduction is what a Harris matrix draws).
- every problem becomes an ``(:Issue)-[:INVOLVES]->(:Context)``.

Issue kinds, most to least severe:

=====================  ==========================================================
self_contradiction     one record says A is both above and below B
enclosed               one record says A is above, below and within B: an inclusion,
                       not an order (info)
conflicting_records    A's record and B's record order the pair oppositely
cut_or_cover           one record says A cuts B, another says B lies above A
cycle                  a longer loop: A above B above ... above A
same_as_conflict       A and B are recorded as one unit, yet one lies above the other
elevation_conflict     A is recorded above B but its top lies below B's bottom
period_inversion       A is above B but dated to a period wholly earlier than B's
=====================  ==========================================================
"""

from __future__ import annotations

import hashlib
from typing import Any

from .store import Store

ELEVATION_TOLERANCE = 0.05  # metres

SEVERITY = {
    "self_contradiction": "error", "enclosed": "info", "conflicting_records": "error", "cut_or_cover": "warning",
    "cycle": "error", "same_as_conflict": "error",
    "elevation_conflict": "warning", "period_inversion": "warning",
}


def _issue_id(site: str, kind: str, uids: list[str]) -> str:
    return f"{site}:{kind}:" + hashlib.sha1("|".join(uids).encode()).hexdigest()[:10]


def contest(store: Store, edge_ids: list[str]) -> None:
    """Set ABOVE edges aside as CONTESTED, keeping their properties."""
    if edge_ids:
        store.run("""
            MATCH (a)-[r:ABOVE]->(b) WHERE elementId(r) IN $ids
            CREATE (a)-[c:CONTESTED]->(b) SET c = properties(r)
            DELETE r""", ids=edge_ids)


def restore_contested(store: Store, site: str) -> None:
    store.run("""
        MATCH (a:Context {site: $site})-[c:CONTESTED]->(b)
        CREATE (a)-[r:ABOVE]->(b) SET r = properties(c)
        DELETE c""", site=site)


def compute_depth(store: Store, site: str) -> dict[str, int]:
    store.run("MATCH (c:Context {site: $site}) REMOVE c.depth", site=site)
    layer = 0
    while True:
        n = store.value("""
            MATCH (c:Context {site: $site}) WHERE c.depth IS NULL
              AND NOT EXISTS { MATCH (u:Context)-[:ABOVE]->(c) WHERE u.depth IS NULL }
            WITH collect(c) AS layer
            FOREACH (x IN layer | SET x.depth = $layer)
            RETURN size(layer)""", site=site, layer=layer)
        if not n:
            break
        layer += 1
    stuck = store.value("MATCH (c:Context {site: $site}) WHERE c.depth IS NULL RETURN count(c)", site=site)
    return {"layers": layer, "unordered": stuck}


def mark_redundant(store: Store, site: str) -> int:
    """Flag ABOVE edges that a longer path already implies."""
    store.run("MATCH (:Context {site: $site})-[r:ABOVE]->() REMOVE r.redundant", site=site)
    return store.value("""
        MATCH (a:Context {site: $site})-[r:ABOVE]->(b:Context)
        WHERE b.depth - a.depth > 1
          AND EXISTS { MATCH (a)-[:ABOVE]->(m:Context)
                       WHERE m <> b AND m.depth < b.depth
                         AND EXISTS { MATCH p = shortestPath((m)-[:ABOVE*]->(b)) } }
        SET r.redundant = true
        RETURN count(r)""", site=site) or 0


def _two_cycles(store: Store, site: str) -> list[dict]:
    rows = store.run("""
        MATCH (a:Context {site: $site})-[r1:ABOVE]->(b:Context)-[r2:ABOVE]->(a)
        WHERE a.uid < b.uid
        RETURN elementId(r1) AS r1, elementId(r2) AS r2, a.uid AS a, b.uid AS b, a.path AS a_path, b.path AS b_path,
               r1.verbs AS ab_verbs, r1.stated_by AS ab_by, r2.verbs AS ba_verbs, r2.stated_by AS ba_by,
               EXISTS { (a)-[:WITHIN]-(b) } AS within""",
                     site=site)
    contest(store, [x for r in rows for x in (r["r1"], r["r2"])])
    issues = []
    for r in rows:
        shared = set(r["ab_by"]) & set(r["ba_by"])
        if shared and r["within"]:
            kind = "enclosed"
            who = store.value("MATCH (c:Context {uid: $u}) RETURN c.path", u=sorted(shared)[0])
            detail = (f"The record of {who} places {r['a_path']} above, below and within "
                      f"{r['b_path']}: one unit enclosed in the other, so the pair has no order.")
        elif shared:
            kind = "self_contradiction"
            who = store.value("MATCH (c:Context {uid: $u}) RETURN c.path", u=sorted(shared)[0])
            detail = f"The record of {who} places {r['a_path']} both above and below {r['b_path']}."
        elif set(r["ab_verbs"]) != set(r["ba_verbs"]):
            kind = "cut_or_cover"
            cut_first = "cuts" in r["ab_verbs"]
            cutter, other = (r["a_path"], r["b_path"]) if cut_first else (r["b_path"], r["a_path"])
            detail = (f"{cutter} is recorded as cutting {other}, while {other} is recorded as lying "
                      f"above {cutter}. Typical of a cut and its fill recorded as one unit, or of an "
                      f"unrecorded recut.")
        else:
            kind = "conflicting_records"
            detail = (f"{r['a_path']} and {r['b_path']} are each recorded as "
                      f"{'/'.join(r['ab_verbs'])} the other, by different records.")
        issues.append({"kind": kind, "uids": [r["a"], r["b"]], "detail": detail,
                       "records": sorted(set(r["ab_by"]) | set(r["ba_by"]))})
    return issues


def _longer_cycles(store: Store, site: str) -> list[dict]:
    """Find loops among units the peel could not place; contest each loop's closing edge."""
    rows = store.run("""
        MATCH (a:Context {site: $site})-[e:ABOVE]->(b:Context)
        WHERE a.depth IS NULL AND b.depth IS NULL
        MATCH p = shortestPath((b)-[:ABOVE*]->(a))
        RETURN elementId(e) AS edge, [n IN nodes(p) | n.uid] AS uids, [n IN nodes(p) | n.path] AS paths""",
                     site=site)
    seen, issues, closing = set(), [], []
    for r in rows:
        key = frozenset(r["uids"])
        if key in seen:
            continue
        seen.add(key)
        closing.append(r["edge"])
        loop = r["paths"] + [r["paths"][0]]
        issues.append({"kind": "cycle", "uids": r["uids"],
                       "detail": "Loop in the recorded order: " + " above ".join(loop) + "."})
    contest(store, closing)
    return issues


def _same_as_conflicts(store: Store, site: str) -> list[dict]:
    rows = store.run("""
        MATCH (a:Context {site: $site})-[:SAME_AS]-(b:Context)
        WHERE a.uid < b.uid
        MATCH p = shortestPath((a)-[:ABOVE*]->(b))
        RETURN a.uid AS a, b.uid AS b, a.path AS a_path, b.path AS b_path, length(p) AS hops""", site=site)
    return [{"kind": "same_as_conflict", "uids": [r["a"], r["b"]],
             "detail": f"{r['a_path']} and {r['b_path']} are recorded as the same unit, but "
                       f"{r['a_path']} lies above {r['b_path']} ({r['hops']} step(s) apart)."} for r in rows]


def _elevation_conflicts(store: Store, site: str) -> list[dict]:
    rows = store.run("""
        MATCH (a:Context {site: $site})-[r:ABOVE]->(b:Context)
        WHERE NOT 'cuts' IN r.verbs AND a.top_depth IS NOT NULL AND b.bottom_depth IS NOT NULL
          AND a.top_depth < b.bottom_depth - $tol
        RETURN a.uid AS a, b.uid AS b, a.path AS a_path, b.path AS b_path,
               a.top_depth AS a_top, b.bottom_depth AS b_bottom, r.stated_by AS by""",
                     site=site, tol=ELEVATION_TOLERANCE)
    return [{"kind": "elevation_conflict", "uids": [r["a"], r["b"]], "records": r["by"],
             "detail": f"{r['a_path']} is recorded above {r['b_path']}, but its top "
                       f"({r['a_top']:.2f} m) is lower than the other's bottom ({r['b_bottom']:.2f} m)."}
            for r in rows]


def _period_inversions(store: Store, site: str) -> list[dict]:
    rows = store.run("""
        MATCH (a:Context {site: $site})-[r:ABOVE]->(b:Context)
        MATCH (a)-[:DATED_TO {basis: 'tentative'}]->(pa:Period),
              (b)-[:DATED_TO {basis: 'tentative'}]->(pb:Period)
        WHERE pa.end_year IS NOT NULL AND pb.start_year IS NOT NULL
          AND pa.end_year <= pb.start_year AND pa.start_year < pb.start_year
        RETURN a.uid AS a, b.uid AS b, a.path AS a_path, b.path AS b_path,
               pa.name AS pa, pb.name AS pb, r.stated_by AS by""", site=site)
    return [{"kind": "period_inversion", "uids": [r["a"], r["b"]], "records": r["by"],
             "detail": f"{r['a_path']} ({r['pa']}) lies above {r['b_path']} ({r['pb']}), a later "
                       f"period. Either the upper unit's date rests on residual material, the lower "
                       f"unit is intrusive, or the relation is wrong."} for r in rows]


def run_checks(store: Store, site: str) -> dict[str, Any]:
    """Recompute depth, contested and redundant edges, and issues for ``site``."""
    restore_contested(store, site)
    store.run("MATCH (:Context {site: $site})-[r:ABOVE]->() REMOVE r.redundant", site=site)
    issues = _two_cycles(store, site)
    for _ in range(25):
        structure = compute_depth(store, site)
        if not structure["unordered"]:
            break
        found = _longer_cycles(store, site)
        if not found:
            break
        issues += found
    issues += _same_as_conflicts(store, site) + _elevation_conflicts(store, site) + _period_inversions(store, site)
    structure["contested_edges"] = store.value(
        "MATCH (:Context {site: $site})-[r:CONTESTED]->() RETURN count(r)", site=site)
    structure["redundant_edges"] = mark_redundant(store, site)

    store.run("MATCH (i:Issue {site: $site}) DETACH DELETE i", site=site)
    rows = [{"id": _issue_id(site, i["kind"], i["uids"]), "site": site, "kind": i["kind"],
             "severity": SEVERITY[i["kind"]], "detail": i["detail"], "uids": i["uids"],
             "records": i.get("records", [])} for i in issues]
    rows = list({r["id"]: r for r in rows}.values())
    store.run("""
        UNWIND $rows AS r
        CREATE (i:Issue {id: r.id, site: r.site, kind: r.kind, severity: r.severity,
                         detail: r.detail, records: r.records, status: 'open'})
        WITH i, r
        UNWIND range(0, size(r.uids) - 1) AS k
        MATCH (c:Context {uid: r.uids[k]})
        CREATE (i)-[:INVOLVES {position: k}]->(c)""", rows=rows)
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    return {**structure, "issues": by_kind}
