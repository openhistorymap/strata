"""Strata over MCP: an excavation record an agent can question and, carefully, change.

    strata mcp                              stdio, for Claude Code / Claude Desktop / any MCP host
    claude mcp add strata -- uv run --directory /path/to/strata strata mcp

Relate connections belong to the thread that opened them, so the store is
opened on one dedicated worker thread and every tool call is handed to it.
That also makes the server a single writer: two agents calling at once
are serialised, and each edit is its own transaction.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Literal, Optional

from fastmcp import FastMCP

from . import edits as E
from . import queries as Q
from .checks import run_checks
from .store import DEFAULT_DB, Store

INSTRUCTIONS = """\
Strata holds archaeological excavations as a graph in one Relate file.

Model: (:Site)<-[:IN_SITE]-(:Trench)<-[:IN_TRENCH]-(:Context) — a Context is a
stratigraphic unit (locus, layer, cut, fill, wall). (a)-[:ABOVE]->(b) means a is
LATER than b (lies over it, cuts it, fills it, seals it); r.verbs says which and
r.stated_by lists the unit records that state it. :CONTESTED is an ABOVE claim set
aside because it contradicts another; :REJECTED is one an archaeologist ruled out.
(:Find)-[:FROM {bag}]->(:Context); (:Context)-[:DATED_TO]->(:Period);
(:Context)-[:IN_PHASE]->(:Phase); (:Issue)-[:INVOLVES]->(:Context) are check
results; (:Edit)-[:TOUCHED]->(:Context) is the interpretive history.
Context.depth is the unit's row in the Harris matrix (0 = top/latest).

Name units by full path, e.g. "Area D/Trench 6/Locus 20".

Rules:
- Answer from the graph. Cite unit paths and, for claims, the records that state them.
- Never invent a stratigraphic relation. Propose one only when the user or the
  record's own text supports it, and put that evidence in `note`.
- resolve_order settles a dispute between excavators' records: ask the user to
  confirm before calling it, and quote the competing claims (explain_issue).
- A refused edit is information, not an error: report the chain that refuses it.
"""

mcp = FastMCP(name="Strata", instructions=INSTRUCTIONS)

_db_path: str = str(DEFAULT_DB)
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="relate")
_store: Store | None = None


def _get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(_db_path)
    return _store


async def _on_db(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()

    def call():
        try:
            return fn(_get_store(), *args, **kwargs)
        except Q.NotFound as e:
            return {"error": str(e)}
        except E.Rejected as e:
            return {"accepted": False, "refused": e.reason, **e.evidence}
        except PermissionError as e:
            return {"error": str(e)}

    return await loop.run_in_executor(_pool, call)


# ---------------------------------------------------------------- reading --


@mcp.tool
async def overview() -> list[dict]:
    """Every site in the store: contexts, relations, contested claims, finds, open issues."""
    return await _on_db(Q.overview)


@mcp.tool
async def query(cypher: str, params: Optional[dict] = None, limit: int = 100) -> dict:
    """Run a read-only Cypher query against the store (it is always rolled back).
    Returns columns, rows (at most `limit`) and whether rows were cut."""
    return await _on_db(lambda s: s.read(cypher, params, limit=min(limit, 1000)))


@mcp.tool
async def describe(unit: str, site: Optional[str] = None) -> dict:
    """One unit: its record, matrix depth, what lies directly above/below, same-as units,
    contested claims, finds and open issues."""
    return await _on_db(Q.describe, unit, site)


@mcp.tool
async def below(unit: str, site: Optional[str] = None, limit: int = 200) -> dict:
    """Every unit stratigraphically earlier than `unit` (everything it seals)."""
    return await _on_db(Q.below, unit, site, limit)


@mcp.tool
async def above(unit: str, site: Optional[str] = None, limit: int = 200) -> dict:
    """Every unit stratigraphically later than `unit`."""
    return await _on_db(Q.above, unit, site, limit)


@mcp.tool
async def relation(a: str, b: str, site: Optional[str] = None) -> dict:
    """How two units relate: earlier, later, same, contested or unknown, with the chain of
    records that proves it and any contested direct claims."""
    return await _on_db(Q.relation, a, b, site)


@mcp.tool
async def date_bracket(unit: str, site: Optional[str] = None) -> dict:
    """Terminus post quem and ante quem for a unit from the dated units below and above it,
    and whether its recorded date fits. Years are astronomical: -1899 is 1900 BCE."""
    return await _on_db(Q.date_bracket, unit, site)


@mcp.tool
async def provenance(find: str, site: Optional[str] = None) -> dict:
    """Where a find came from and what dates it: find -> bag -> unit -> period -> site,
    the units sealing it, open caveats, and its date bracket."""
    return await _on_db(Q.provenance, find, site)


@mcp.tool
async def issues(site: Optional[str] = None,
                 kind: Optional[Literal["self_contradiction", "enclosed", "conflicting_records", "cut_or_cover",
                                        "cycle", "same_as_conflict", "elevation_conflict",
                                        "period_inversion"]] = None,
                 limit: int = 50) -> list[dict]:
    """Open problems found by the checks, most severe first."""
    return await _on_db(Q.issues, site, kind, limit)


@mcp.tool
async def explain_issue(issue_id: str) -> dict:
    """An issue with its units and every claim recorded between them, and which record made it."""
    return await _on_db(Q.explain_issue, issue_id)


@mcp.tool
async def period_sequence(site: str) -> list[dict]:
    """Which period lies directly over which, counted over the matrix: the site's sequence."""
    return await _on_db(Q.period_sequence, site)


@mcp.tool
async def history(site: Optional[str] = None, limit: int = 30) -> list[dict]:
    """The interpretive edits made to the record, newest first."""
    return await _on_db(E.history, site, limit)


# ---------------------------------------------------------------- writing --


@mcp.tool
async def propose_relation(upper: str, lower: str, verb: Literal["above", "cuts", "fills", "seals"] = "above",
                           note: str = "", author: str = "agent", site: Optional[str] = None) -> dict:
    """Record that `upper` is later than `lower`. Checked in a transaction: refused, with the
    chain of records that forbids it, if `lower` is already later than `upper`. Put the
    evidence in `note`."""
    return await _on_db(E.propose_relation, upper, lower, verb, note=note or None, author=author, site=site)


@mcp.tool
async def resolve_order(upper: str, lower: str, note: str, author: str = "agent",
                        site: Optional[str] = None) -> dict:
    """Settle a disputed pair: `upper` is the later unit. Contrary claims become :REJECTED
    (kept, not deleted). Confirm with the user first."""
    return await _on_db(E.resolve_order, upper, lower, note=note, author=author, site=site)


@mcp.tool
async def assign_phase(units: list[str], phase: str, note: str = "", author: str = "agent",
                       site: Optional[str] = None) -> dict:
    """Put units into a phase; warns if units of other phases lie between its members."""
    return await _on_db(E.assign_phase, units, phase, note=note or None, author=author, site=site)


@mcp.tool
async def add_note(unit: str, note: str, author: str = "agent", site: Optional[str] = None) -> dict:
    """Attach an interpretive note to a unit (kept in the edit history)."""
    return await _on_db(E.add_note, unit, note, author=author, site=site)


@mcp.tool
async def revert(edit_id: str, note: str = "", author: str = "agent") -> dict:
    """Undo an edit from history(); the reversal is itself logged."""
    return await _on_db(E.revert, edit_id, author=author, note=note or None)


@mcp.tool
async def recheck(site: str) -> dict:
    """Recompute matrix depth, contested claims and issues for a site."""
    return await _on_db(run_checks, site)


def serve(db_path: str = str(DEFAULT_DB)) -> None:
    global _db_path
    _db_path = db_path
    try:
        mcp.run(show_banner=False)
    finally:
        if _store is not None:
            _pool.submit(_store.close).result()
