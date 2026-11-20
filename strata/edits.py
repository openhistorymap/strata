"""Interpretive edits: how an agent (or a person) changes the record.

Every edit runs in one Relate transaction. The change is written, then
checked against the graph *inside* the transaction; if it would break the
matrix (a loop in the order of deposition) the transaction is rolled back
and the caller gets the chain of records that forbids it. Accepted edits
commit together with an ``(:Edit)`` node linked to every unit they touched,
so the file carries its own interpretive history, and each one can be
reverted.

Relationships an edit creates carry ``edit: <edit id>``. Relationships an
archaeologist rules out are not deleted: they are retyped ``:REJECTED`` and
keep their original records, so the evidence never disappears.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from . import queries as Q
from .checks import run_checks
from .store import Store

VERBS = ("above", "cuts", "fills", "seals")


class Rejected(Exception):
    """The edit would break the record; nothing was written."""

    def __init__(self, reason: str, **evidence: Any):
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_edit(store: Store, site: str, op: str, args: dict, uids: list[str], note: str | None,
                 author: str) -> str:
    edit_id = uuid.uuid4().hex[:12]
    store.run("""
        CREATE (e:Edit {id: $id, site: $site, op: $op, args: $args, note: $note, author: $author,
                        ts: $ts, status: 'applied'})
        WITH e UNWIND $uids AS u MATCH (c:Context {uid: u}) CREATE (e)-[:TOUCHED]->(c)""",
              id=edit_id, site=site, op=op, args=json.dumps(args), note=note, author=author, ts=_now(), uids=uids)
    return edit_id


def _loop_through(store: Store, upper_uid: str, lower_uid: str) -> dict | None:
    """The chain proving ``lower`` is already above ``upper``, if one exists."""
    row = store.one("""
        MATCH (l:Context {uid: $lower}), (u:Context {uid: $upper})
        MATCH p = shortestPath((l)-[:ABOVE*]->(u))
        RETURN [n IN nodes(p) | n.path] AS chain,
               [r IN relationships(p) | {verbs: r.verbs, stated_by: r.stated_by, sources: r.sources}] AS records""",
                    upper=upper_uid, lower=lower_uid)
    if row:
        ids = sorted({x for r in row["records"] for x in (r["stated_by"] or [])})
        who = {r["uid"]: r["path"] for r in store.run(
            "MATCH (c:Context) WHERE c.uid IN $ids RETURN c.uid AS uid, c.path AS path", ids=ids)}
        for r in row["records"]:
            r["stated_by"] = [who.get(x, x) for x in (r["stated_by"] or [])]
    return row


def _warnings(store: Store, upper_uid: str, lower_uid: str) -> list[str]:
    out = []
    row = store.one("""
        MATCH (u:Context {uid: $u}), (l:Context {uid: $l})
        OPTIONAL MATCH (u)-[:DATED_TO {basis: 'tentative'}]->(pu:Period)
        OPTIONAL MATCH (l)-[:DATED_TO {basis: 'tentative'}]->(pl:Period)
        RETURN u.path AS up, l.path AS lp, pu.name AS pu, pl.name AS pl,
               pu.end_year <= pl.start_year AND pu.start_year < pl.start_year AS inverted,
               u.top_depth AS ut, l.bottom_depth AS lb,
               EXISTS { (u)-[:SAME_AS]-(l) } AS same""", u=upper_uid, l=lower_uid)
    if row["inverted"]:
        out.append(f"{row['up']} is dated {row['pu']}, earlier than {row['lp']} ({row['pl']}): "
                   f"this makes a period inversion.")
    if row["ut"] is not None and row["lb"] is not None and row["ut"] < row["lb"] - 0.05:
        out.append(f"{row['up']}'s top ({row['ut']:.2f} m) is lower than {row['lp']}'s bottom ({row['lb']:.2f} m).")
    if row["same"]:
        out.append(f"{row['up']} and {row['lp']} are recorded as the same unit.")
    return out


def propose_relation(store: Store, upper: str, lower: str, verb: str = "above", *, note: str | None = None,
                     author: str = "agent", site: str | None = None, recheck: bool = True) -> dict[str, Any]:
    """Record that ``upper`` is later than ``lower`` (above it, cuts it, fills it or seals it)."""
    if verb not in VERBS:
        raise ValueError(f"verb must be one of {VERBS}")
    u, l = Q.resolve(store, upper, site), Q.resolve(store, lower, site)
    if u["site"] != l["site"]:
        raise Rejected("Units from different sites have no stratigraphic relation; "
                       "use a correspondence instead.")
    if u["uid"] == l["uid"]:
        raise Rejected("A unit cannot lie above itself.")
    if store.value("MATCH (:Context {uid: $u})-[:ABOVE]->(:Context {uid: $l}) RETURN count(*) > 0",
                   u=u["uid"], l=l["uid"]):
        return {"accepted": False, "already_recorded": True,
                "detail": f"{u['path']} is already recorded above {l['path']}."}
    with store.transaction():
        store.run("""
            MATCH (u:Context {uid: $u}), (l:Context {uid: $l})
            CREATE (u)-[:ABOVE {verbs: [$verb], stated_by: ['proposal'], sources: [], edit: 'pending'}]->(l)""",
                  u=u["uid"], l=l["uid"], verb=verb)
        loop = _loop_through(store, u["uid"], l["uid"])
        if loop:
            raise Rejected(
                f"Refused: {l['path']} is already recorded as later than {u['path']}, "
                f"so {u['path']} cannot be above it. The chain: " + " above ".join(loop["chain"]) + ".",
                chain=loop["chain"], records=loop["records"])
        warnings = _warnings(store, u["uid"], l["uid"])
        edit_id = _record_edit(store, u["site"], "add_relation",
                               {"upper": u["uid"], "lower": l["uid"], "verb": verb},
                               [u["uid"], l["uid"]], note, author)
        store.run("""MATCH (:Context {uid: $u})-[r:ABOVE {edit: 'pending'}]->(:Context {uid: $l})
                     SET r.edit = $id, r.stated_by = ['edit:' + $id]""", u=u["uid"], l=l["uid"], id=edit_id)
    result = {"accepted": True, "edit": edit_id, "detail": f"{u['path']} {verb} {l['path']}",
              "warnings": warnings}
    if recheck:
        result["checks"] = run_checks(store, u["site"])
    return result


def resolve_order(store: Store, upper: str, lower: str, *, note: str | None = None, author: str = "agent",
                  site: str | None = None, recheck: bool = True) -> dict[str, Any]:
    """Settle a disputed pair: ``upper`` is the later unit; claims to the contrary are rejected."""
    u, l = Q.resolve(store, upper, site), Q.resolve(store, lower, site)
    with store.transaction():
        rejected = store.value("""
            MATCH (l:Context {uid: $l})-[r:ABOVE|CONTESTED]->(u:Context {uid: $u})
            WITH l, u, collect(r) AS rs
            FOREACH (r IN rs | CREATE (l)-[x:REJECTED]->(u) SET x = properties(r))
            FOREACH (r IN rs | DELETE r)
            RETURN size(rs)""", u=u["uid"], l=l["uid"]) or 0
        restored = store.value("""
            MATCH (u:Context {uid: $u})-[r:CONTESTED]->(l:Context {uid: $l})
            WITH u, l, collect(r) AS rs
            FOREACH (r IN rs | CREATE (u)-[x:ABOVE]->(l) SET x = properties(r))
            FOREACH (r IN rs | DELETE r)
            RETURN size(rs)""", u=u["uid"], l=l["uid"]) or 0
        created = 0
        if not store.value("MATCH (:Context {uid: $u})-[:ABOVE]->(:Context {uid: $l}) RETURN count(*) > 0",
                           u=u["uid"], l=l["uid"]):
            store.run("""MATCH (u:Context {uid: $u}), (l:Context {uid: $l})
                         CREATE (u)-[:ABOVE {verbs: ['above'], stated_by: ['proposal'], sources: [], edit: 'pending'}]->(l)""",
                      u=u["uid"], l=l["uid"])
            created = 1
        loop = _loop_through(store, u["uid"], l["uid"])
        if loop:
            raise Rejected(
                f"Refused: other records still place {l['path']} above {u['path']}: "
                + " above ".join(loop["chain"]) + ". Resolve those first.",
                chain=loop["chain"], records=loop["records"])
        edit_id = _record_edit(store, u["site"], "resolve_order",
                               {"upper": u["uid"], "lower": l["uid"], "rejected": rejected,
                                "restored": restored, "created": created},
                               [u["uid"], l["uid"]], note, author)
        store.run("""MATCH (:Context {uid: $l})-[x:REJECTED]->(:Context {uid: $u}) WHERE x.rejected_by IS NULL
                     SET x.rejected_by = $id""", u=u["uid"], l=l["uid"], id=edit_id)
        store.run("""MATCH (:Context {uid: $u})-[r:ABOVE]->(:Context {uid: $l})
                     SET r.confirmed_by = $id, r.edit = CASE r.edit WHEN 'pending' THEN $id ELSE r.edit END,
                         r.stated_by = CASE WHEN r.edit = $id THEN ['edit:' + $id] ELSE r.stated_by END""",
                  u=u["uid"], l=l["uid"], id=edit_id)
    result = {"accepted": True, "edit": edit_id,
              "detail": f"{u['path']} is later than {l['path']}; {rejected} contrary claim(s) rejected."}
    if recheck:
        result["checks"] = run_checks(store, u["site"])
    return result


def assign_phase(store: Store, units: list[str], phase: str, *, note: str | None = None, author: str = "agent",
                 site: str | None = None) -> dict[str, Any]:
    """Group units into a phase. A phase must not be split by a unit of another phase
    lying between its members; that is reported as a warning."""
    resolved = [Q.resolve(store, x, site) for x in units]
    sites = {r["site"] for r in resolved}
    if len(sites) != 1:
        raise Rejected("All units of a phase must come from one site.")
    sid = sites.pop()
    uids = [r["uid"] for r in resolved]
    with store.transaction():
        previous = store.run("""UNWIND $uids AS u MATCH (c:Context {uid: u})-[r:IN_PHASE]->(p:Phase)
                                RETURN u AS uid, p.name AS phase""", uids=uids)
        store.run("UNWIND $uids AS u MATCH (:Context {uid: u})-[r:IN_PHASE]->() DELETE r", uids=uids)
        store.run("""MATCH (s:Site {id: $site})
                     MERGE (p:Phase {uid: $site + ':' + $phase})
                     ON CREATE SET p.site = $site, p.name = $phase
                     MERGE (p)-[:OF_SITE]->(s)""", site=sid, phase=phase)
        edit_id = _record_edit(store, sid, "assign_phase", {"units": uids, "phase": phase, "previous": previous},
                               uids, note, author)
        store.run("""UNWIND $uids AS u MATCH (c:Context {uid: u}), (p:Phase {uid: $site + ':' + $phase})
                     CREATE (c)-[:IN_PHASE {edit: $id}]->(p)""", uids=uids, site=sid, phase=phase, id=edit_id)
        intruders = store.run("""
            MATCH (a:Context)-[:IN_PHASE]->(p:Phase {uid: $site + ':' + $phase})<-[:IN_PHASE]-(b:Context)
            MATCH path = shortestPath((a)-[:ABOVE*]->(b))
            UNWIND nodes(path)[1..-1] AS m
            WITH DISTINCT m WHERE NOT (m)-[:IN_PHASE]->(:Phase {uid: $site + ':' + $phase})
            RETURN m.path AS path LIMIT 10""", site=sid, phase=phase)
    return {"accepted": True, "edit": edit_id, "phase": phase, "units": len(uids),
            "warnings": [f"{r['path']} lies between members of phase {phase} but is not in it."
                         for r in intruders]}


def add_note(store: Store, unit: str, note: str, *, author: str = "agent", site: str | None = None) -> dict:
    c = Q.resolve(store, unit, site)
    edit_id = _record_edit(store, c["site"], "note", {"unit": c["uid"]}, [c["uid"]], note, author)
    return {"accepted": True, "edit": edit_id}


def history(store: Store, site: str | None = None, limit: int = 50) -> list[dict]:
    rows = store.run("""
        MATCH (e:Edit) WHERE $site IS NULL OR e.site = $site
        RETURN e.id AS id, e.ts AS ts, e.op AS op, e.author AS author, e.status AS status, e.note AS note,
               e.args AS args, [(e)-[:TOUCHED]->(c) | c.path] AS units
        ORDER BY e.ts DESC LIMIT $limit""", site=site, limit=limit)
    for r in rows:
        r["args"] = json.loads(r["args"]) if r["args"] else {}
    return rows


def revert(store: Store, edit_id: str, *, author: str = "agent", note: str | None = None) -> dict[str, Any]:
    e = store.one("MATCH (e:Edit {id: $id}) RETURN e.op AS op, e.args AS args, e.status AS status, e.site AS site",
                  id=edit_id)
    if not e:
        raise Rejected(f"No edit {edit_id}.")
    if e["status"] != "applied":
        raise Rejected(f"Edit {edit_id} is already {e['status']}.")
    args = json.loads(e["args"])
    with store.transaction():
        if e["op"] == "add_relation":
            store.run("MATCH ()-[r:ABOVE|CONTESTED {edit: $id}]->() DELETE r", id=edit_id)
        elif e["op"] == "resolve_order":
            store.run("""MATCH (a)-[x:REJECTED {rejected_by: $id}]->(b)
                         CREATE (a)-[r:ABOVE]->(b) SET r = properties(x) REMOVE r.rejected_by DELETE x""", id=edit_id)
            store.run("MATCH ()-[r:ABOVE|CONTESTED {edit: $id}]->() DELETE r", id=edit_id)
            store.run("MATCH ()-[r:ABOVE|CONTESTED {confirmed_by: $id}]->() REMOVE r.confirmed_by", id=edit_id)
        elif e["op"] == "assign_phase":
            store.run("MATCH ()-[r:IN_PHASE {edit: $id}]->() DELETE r", id=edit_id)
            for prev in args.get("previous", []):
                store.run("""MATCH (c:Context {uid: $u}), (p:Phase {uid: $site + ':' + $phase})
                             CREATE (c)-[:IN_PHASE]->(p)""", u=prev["uid"], site=e["site"], phase=prev["phase"])
            store.run("MATCH (p:Phase {site: $site}) WHERE NOT ()-[:IN_PHASE]->(p) DETACH DELETE p", site=e["site"])
        store.run("MATCH (e:Edit {id: $id}) SET e.status = 'reverted'", id=edit_id)
        revert_id = _record_edit(store, e["site"], "revert", {"edit": edit_id},
                                 [r["u"] for r in store.run("MATCH (:Edit {id: $id})-[:TOUCHED]->(c) RETURN c.uid AS u",
                                                            id=edit_id)], note, author)
    return {"reverted": edit_id, "edit": revert_id, "checks": run_checks(store, e["site"])}
