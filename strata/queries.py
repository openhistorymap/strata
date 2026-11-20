"""Named questions an archaeologist (or an agent) asks of the record.

Each function is one or two Cypher statements over the store; the CLI's
``strata ask`` and the MCP server both call these. Units can be named by
uid, by path ("Area D/Trench 6/Locus 20") or by an unambiguous label.

Run ``strata check`` first: the questions about order walk ABOVE edges,
which only form a clean matrix once contradictions are set aside as
CONTESTED.
"""

from __future__ import annotations

from typing import Any

from .store import Store


class NotFound(LookupError):
    pass


def resolve(store: Store, ref: str, site: str | None = None) -> dict:
    """Find one Context by uid, path or label; raise with candidates if ambiguous."""
    rows = store.run("""
        MATCH (c:Context)
        WHERE (c.uid = $ref OR c.path = $ref OR c.label = $ref OR c.path ENDS WITH '/' + $ref)
          AND ($site IS NULL OR c.site = $site)
        RETURN c.uid AS uid, c.path AS path, c.site AS site LIMIT 25""", ref=ref, site=site)
    if not rows:
        raise NotFound(f"No context matches {ref!r}. Try a full path such as 'Area D/Trench 6/Locus 20'.")
    if len(rows) > 1:
        exact = [r for r in rows if r["uid"] == ref or r["path"] == ref]
        if len(exact) == 1:
            return exact[0]
        raise NotFound(f"{ref!r} matches {len(rows)} contexts, e.g. "
                       + ", ".join(r["path"] for r in rows[:5]) + ". Give the full path.")
    return rows[0]


def resolve_find(store: Store, ref: str, site: str | None = None) -> dict:
    rows = store.run("""
        MATCH (f:Find) WHERE (f.uid = $ref OR f.label = $ref) AND ($site IS NULL OR f.site = $site)
        RETURN f.uid AS uid, f.label AS label LIMIT 10""", ref=ref, site=site)
    if len(rows) != 1:
        raise NotFound(f"{ref!r} matches {len(rows)} finds" + (": " + ", ".join(r['label'] for r in rows) if rows else "."))
    return rows[0]


def describe(store: Store, ref: str, site: str | None = None) -> dict[str, Any]:
    """A context with its immediate neighbourhood, dating, finds summary and open issues."""
    c = resolve(store, ref, site)
    return store.one("""
        MATCH (c:Context {uid: $uid})
        OPTIONAL MATCH (c)-[:DATED_TO]->(p:Period)
        WITH c, collect(DISTINCT p.name) AS periods
        RETURN c {.uid, .path, .type, .depth, .period_label, .start_year, .end_year,
                  .top_depth, .bottom_depth, .description, .uri} AS context,
               periods,
               [(u:Context)-[r:ABOVE]->(c) | {path: u.path, verbs: r.verbs}] AS directly_above,
               [(c)-[r:ABOVE]->(d:Context) | {path: d.path, verbs: r.verbs}] AS directly_below,
               [(c)-[:SAME_AS]-(s:Context) | s.path] AS same_as,
               [(c)-[r:CONTESTED]-(x:Context) | {path: x.path, verbs: r.verbs,
                   direction: CASE WHEN startNode(r) = c THEN 'claimed above' ELSE 'claimed below' END}] AS contested,
               COUNT { (:Find)-[:FROM]->(c) } AS finds,
               [(f:Find)-[:FROM]->(c) | f.category] AS find_categories,
               [(i:Issue)-[:INVOLVES]->(c) WHERE i.status = 'open' | {kind: i.kind, detail: i.detail}] AS issues
        """, uid=c["uid"])


def below(store: Store, ref: str, site: str | None = None, limit: int = 500) -> dict[str, Any]:
    """Everything stratigraphically earlier than a unit: what it seals."""
    c = resolve(store, ref, site)
    rows = store.run("""
        MATCH (c:Context {uid: $uid})-[:ABOVE*]->(d:Context)
        WITH DISTINCT d
        RETURN d.path AS path, d.type AS type, d.depth AS depth, d.period_label AS period,
               COUNT { (:Find)-[:FROM]->(d) } AS finds
        ORDER BY depth, path LIMIT $limit""", uid=c["uid"], limit=limit)
    return {"context": c["path"], "sealed_units": len(rows), "units": rows}


def above(store: Store, ref: str, site: str | None = None, limit: int = 500) -> dict[str, Any]:
    """Everything stratigraphically later than a unit."""
    c = resolve(store, ref, site)
    rows = store.run("""
        MATCH (u:Context)-[:ABOVE*]->(c:Context {uid: $uid})
        WITH DISTINCT u
        RETURN u.path AS path, u.type AS type, u.depth AS depth, u.period_label AS period
        ORDER BY depth DESC, path LIMIT $limit""", uid=c["uid"], limit=limit)
    return {"context": c["path"], "later_units": len(rows), "units": rows}


def relation(store: Store, a: str, b: str, site: str | None = None) -> dict[str, Any]:
    """How two units relate, with the chain of records that proves it."""
    ca, cb = resolve(store, a, site), resolve(store, b, site)
    contested = store.run("""MATCH (x:Context {uid: $a})-[r:CONTESTED]-(y:Context {uid: $b})
                             RETURN startNode(r).path AS claimed_upper, r.verbs AS verbs,
                                    size(r.stated_by) AS records""", a=ca["uid"], b=cb["uid"])
    for upper, lower, verdict in ((ca, cb, "later"), (cb, ca, "earlier")):
        row = store.one("""
            MATCH (x:Context {uid: $u}), (y:Context {uid: $l})
            MATCH p = shortestPath((x)-[:ABOVE*]->(y))
            RETURN [n IN nodes(p) | n.path] AS chain,
                   [r IN relationships(p) | {verbs: r.verbs, records: size(r.stated_by)}] AS steps""",
                        u=upper["uid"], l=lower["uid"])
        if row:
            return {"a": ca["path"], "b": cb["path"], "a_is": verdict, **row,
                    "contested_claims": contested}
    same = store.value("""MATCH (x:Context {uid: $a})-[:SAME_AS*1..3]-(y:Context {uid: $b}) RETURN count(*) > 0""",
                       a=ca["uid"], b=cb["uid"])
    return {"a": ca["path"], "b": cb["path"],
            "a_is": "the same unit" if same else ("contested" if contested else "unrelated"),
            "contested_claims": contested,
            "note": None if same or contested else
                    "No recorded chain of superposition links them: their order is not known from stratigraphy."}


def date_bracket(store: Store, ref: str, site: str | None = None) -> dict[str, Any]:
    """Terminus post quem / ante quem from the dated units below and above.

    A unit cannot be earlier than anything it seals (TPQ = latest start
    among dated units below) nor later than anything sealing it (TAQ =
    earliest end among dated units above). Years are astronomical (-1899 = 1900 BCE).
    """
    c = resolve(store, ref, site)
    row = store.one("""MATCH (c:Context {uid: $uid})
                       RETURN c.path AS context, c.period_label AS recorded_period,
                              c.start_year AS recorded_start, c.end_year AS recorded_end""", uid=c["uid"])
    tpq = store.one("""
        MATCH (:Context {uid: $uid})-[:ABOVE*]->(d:Context) WHERE d.start_year IS NOT NULL
        WITH DISTINCT d ORDER BY d.start_year DESC LIMIT 1
        RETURN d.start_year AS year, d.path AS path, d.period_label AS period""", uid=c["uid"])
    taq = store.one("""
        MATCH (u:Context)-[:ABOVE*]->(:Context {uid: $uid}) WHERE u.end_year IS NOT NULL
        WITH DISTINCT u ORDER BY u.end_year ASC LIMIT 1
        RETURN u.end_year AS year, u.path AS path, u.period_label AS period""", uid=c["uid"])
    row["not_earlier_than"] = tpq
    row["not_later_than"] = taq
    if tpq and taq:
        row["consistent"] = tpq["year"] <= taq["year"]
    if row.get("recorded_start") is not None:
        problems = []
        if tpq and row["recorded_end"] < tpq["year"]:
            problems.append(f"recorded date ends before {tpq['path']} below it begins")
        if taq and row["recorded_start"] > taq["year"]:
            problems.append(f"recorded date begins after {taq['path']} above it ends")
        row["recorded_date_fits"] = not problems
        row["problems"] = problems
    return row


def provenance(store: Store, ref: str, site: str | None = None) -> dict[str, Any]:
    """Where a find came from, and everything that dates it: find -> unit -> period -> site."""
    f = resolve_find(store, ref, site)
    row = store.one("""
        MATCH (f:Find {uid: $uid})-[fr:FROM]->(c:Context)
        OPTIONAL MATCH (c)-[:IN_TRENCH]->(t:Trench)-[:IN_SITE]->(s:Site)
        OPTIONAL MATCH (c)-[:DATED_TO]->(p:Period)
        OPTIONAL MATCH (f)-[:IDENTIFIED_AS]->(x:Taxon)
        RETURN f {.label, .category, .material, .type, .start_year, .end_year, .uri} AS find,
               fr.bag AS bag, x {.name, .uri} AS taxon,
               c {.path, .type, .depth, .period_label, .uri} AS context,
               collect(DISTINCT p {.name, .start_year, .end_year, .periodo, .wikidata}) AS periods,
               s {.name, .wikidata, .ohm, .source, .source_uri, .license} AS site,
               [(u:Context)-[:ABOVE]->(c) | u.path] AS sealed_by,
               [(i:Issue)-[:INVOLVES]->(c) WHERE i.status = 'open' | i.detail] AS caveats""", uid=f["uid"])
    row["date_bracket"] = date_bracket(store, row["context"]["path"], site)
    row["date_bracket"].pop("context", None)
    return row


def issues(store: Store, site: str | None = None, kind: str | None = None, limit: int = 100) -> list[dict]:
    rows = store.run("""
        MATCH (i:Issue) WHERE ($site IS NULL OR i.site = $site) AND ($kind IS NULL OR i.kind = $kind)
          AND i.status = 'open'
        RETURN i.id AS id, i.kind AS kind, i.severity AS severity, i.detail AS detail,
               [(i)-[r:INVOLVES]->(c) | {position: r.position, path: c.path}] AS units
        ORDER BY CASE i.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, kind, id
        LIMIT $limit""", site=site, kind=kind, limit=limit)
    for r in rows:
        r["units"] = [u["path"] for u in sorted(r["units"], key=lambda u: u["position"])]
    return rows


def overview(store: Store) -> list[dict]:
    return store.run("""
        MATCH (s:Site)
        RETURN s.id AS site, s.name AS name, s.license_label AS license,
               COUNT { (c:Context {site: s.id}) } AS contexts,
               COUNT { (:Context {site: s.id})-[:ABOVE]->() } AS above,
               COUNT { (:Context {site: s.id})-[:CONTESTED]->() } AS contested,
               COUNT { (f:Find {site: s.id}) } AS finds,
               COUNT { (i:Issue {site: s.id, status: 'open'}) } AS open_issues
        ORDER BY site""")


def period_sequence(store: Store, site: str) -> list[dict]:
    """Which period overlies which, counted over every direct ABOVE edge: the site's sequence."""
    return store.run("""
        MATCH (a:Context {site: $site})-[:ABOVE]->(b:Context),
              (a)-[:DATED_TO {basis: 'tentative'}]->(pa:Period),
              (b)-[:DATED_TO {basis: 'tentative'}]->(pb:Period)
        WHERE pa <> pb
        RETURN pa.name AS upper, pb.name AS lower, count(*) AS edges,
               pa.start_year >= pb.start_year AS agrees_with_dates
        ORDER BY edges DESC""", site=site)


def explain_issue(store: Store, issue_id: str) -> dict[str, Any]:
    """An issue with the units it involves and every claim recorded between them."""
    issue = store.one("""MATCH (i:Issue {id: $id})
                         RETURN i {.id, .kind, .severity, .detail, .status, .site} AS issue""", id=issue_id)
    if not issue:
        raise NotFound(f"No issue {issue_id!r}; list them with issues().")
    units = store.run("""
        MATCH (:Issue {id: $id})-[r:INVOLVES]->(c:Context)
        RETURN c.uid AS uid, c.path AS path, c.type AS type, c.period_label AS period,
               c.top_depth AS top, c.bottom_depth AS bottom, c.uri AS record
        ORDER BY r.position""", id=issue_id)
    uids = [u["uid"] for u in units]
    claims = store.run("""
        MATCH (a:Context)-[r:ABOVE|CONTESTED|REJECTED|SAME_AS|WITHIN]->(b:Context)
        WHERE a.uid IN $uids AND b.uid IN $uids
        RETURN a.path AS from, type(r) AS relation, r.verbs AS verbs,
               r.stated_by AS stated_by, r.sources AS records""", uids=uids)
    who = {r["uid"]: r["path"] for r in store.run(
        "MATCH (c:Context) WHERE c.uid IN $ids RETURN c.uid AS uid, c.path AS path",
        ids=sorted({x for c in claims for x in (c["stated_by"] or [])}))}
    for c in claims:
        c["stated_by"] = [who.get(x, x) for x in (c["stated_by"] or [])]
    return {**issue["issue"], "units": units, "claims": claims}
