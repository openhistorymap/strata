"""The Strata store: one Relate graph file holding every excavation.

All graph access goes through :class:`Store`. It owns one Relate
connection (Relate is single-writer and thread-affine), applies the schema,
and offers three ways to run Cypher:

- :meth:`Store.run`        autocommit, for importers and the CLI
- :meth:`Store.transaction` explicit transaction (commit or rollback)
- :meth:`Store.read`       any statement, always rolled back: what an agent's
                            free-form query runs through, so it can never write
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import relate

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "strata.rl"
SCHEMA_FILE = Path(__file__).with_name("schema.cypher")

_WRITE = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b", re.I)
_STRINGS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`")


def is_write(query: str) -> bool:
    """True if ``query`` has a write clause outside string literals."""
    return bool(_WRITE.search(_STRINGS.sub("''", query)))


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB, *, read_only: bool = False):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = relate.connect(self.path, read_only=read_only)

    # -- running Cypher ----------------------------------------------------

    def run(self, query: str, params: dict[str, Any] | None = None, **kw: Any) -> list[dict]:
        return self.conn.execute(query, {**(params or {}), **kw}).data()

    def one(self, query: str, params: dict[str, Any] | None = None, **kw: Any) -> dict | None:
        rows = self.run(query, params, **kw)
        return rows[0] if rows else None

    def value(self, query: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        row = self.one(query, params, **kw)
        return next(iter(row.values())) if row else None

    @contextmanager
    def transaction(self) -> Iterator["Store"]:
        """Run a block in one transaction: commit on success, roll back on error."""
        self.conn.autocommit = False
        try:
            yield self
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        finally:
            self.conn.autocommit = True

    def read(self, query: str, params: dict[str, Any] | None = None, *, limit: int = 200) -> dict:
        """Run ``query`` in a transaction that is always rolled back.

        Returns ``{"columns", "rows", "truncated"}``; at most ``limit`` rows.
        """
        if is_write(query):
            raise PermissionError(
                "This tool only reads. Use propose_relation / resolve_order / assign_phase / "
                "add_note to change the record, so the change is checked and logged.")
        self.conn.autocommit = False
        try:
            cur = self.conn.cursor()
            cur.execute(query, params or {})
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(limit + 1)
            data = [dict(zip(columns, r)) if not isinstance(r, dict) else dict(r) for r in rows]
        finally:
            self.conn.rollback()
            self.conn.autocommit = True
        return {"columns": columns, "rows": data[:limit], "truncated": len(data) > limit}

    # -- schema and lifecycle ----------------------------------------------

    def apply_schema(self) -> None:
        for statement in SCHEMA_FILE.read_text().split(";"):
            lines = [l for l in statement.splitlines() if l.strip() and not l.strip().startswith("//")]
            if lines:
                self.run("\n".join(lines))

    def stats(self) -> dict[str, int]:
        rows = self.run("MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY label")
        out = {r["label"]: r["n"] for r in rows}
        for r in self.run("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY type"):
            out[f"-[:{r['type']}]-"] = r["n"]
        return out

    def flush(self) -> None:
        self.conn.flush() if hasattr(self.conn, "flush") else None

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
