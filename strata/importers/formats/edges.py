"""A relation table: one row per recorded statement.

    from,relation,to            us;rapporto;us2           12<TAB>cuts<TAB>13
    12,above,13                 12;Copre;13
    13,cut by,14                12;Taglia;14

The relation may be in any language Strata knows (see ``verbs.py``). The
header is optional; columns are found by name (from/unit/us/context/
subject/a, relation/verb/rapporto/kind/type, to/object/b/us2/target) or,
without a header, as the first three columns. Optional columns group/area/
trench, period, phase and description describe the first unit of the row.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from ..matrices import Matrix, delimiter
from ..verbs import verb

FROM = ("from", "unit", "us", "su", "context", "subject", "a", "source", "unita", "us1", "unit1", "context1",
        "befund", "locus", "ue")
REL = ("relation", "relationship", "verb", "rapporto", "relazione", "rapporti", "kind", "type", "relation_type",
       "beziehung", "relacion", "relation_kind")
TO = ("to", "object", "b", "target", "us2", "su2", "unit2", "context2", "other", "related", "with")
GROUP = ("group", "area", "trench", "saggio", "schnitt", "sector")
EXTRA = {"period": "period", "periodo": "period", "phase": "phase", "fase": "phase",
         "description": "description", "descrizione": "description"}


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except (IsADirectoryError, PermissionError):
        return ""


def _rows(text: str) -> list[list[str]]:
    return [r for r in csv.reader(io.StringIO(text), delimiter=delimiter(text)) if any(c.strip() for c in r)]


def _find(header: list[str], names: tuple[str, ...]) -> int | None:
    norm = [h.strip().lower().replace(" ", "_") for h in header]
    for n in names:
        if n in norm:
            return norm.index(n)
    return None


def _layout(rows: list[list[str]]) -> tuple[dict[str, int], bool] | None:
    if not rows:
        return None
    header = rows[0]
    cols = {"from": _find(header, FROM), "rel": _find(header, REL), "to": _find(header, TO)}
    if None not in cols.values() and len(set(cols.values())) == 3:
        for i, h in enumerate(header):
            k = h.strip().lower()
            if k in GROUP and "group" not in cols:
                cols["group"] = i
            elif k in EXTRA:
                cols[EXTRA[k]] = i
        return cols, True
    # headerless: first three columns, if the middle one reads as relations
    body = [r for r in rows[:50] if len(r) >= 3]
    if body and sum(1 for r in body if verb(r[1])) >= 0.8 * len(body):
        return {"from": 0, "rel": 1, "to": 2}, False
    return None


def sniff(path: Path) -> bool:
    if path.suffix.lower() not in (".csv", ".tsv", ".txt", ""):
        return False
    rows = _rows(_text(path)[:8192])
    lay = _layout(rows[:-1] if len(rows) > 2 else rows)    # last line may be cut by the sample
    if not lay:
        return False
    cols, has_header = lay
    body = rows[1:] if has_header else rows
    body = [r for r in body[:50] if len(r) > cols["rel"]]
    return bool(body) and sum(1 for r in body if verb(r[cols["rel"]])) >= 0.5 * len(body)


def read(path: Path, **_: Any) -> Matrix:
    rows = _rows(_text(path))
    lay = _layout(rows)
    if not lay:
        raise ValueError(f"{path.name}: expected columns from, relation, to (a header naming them, "
                         "or three columns whose middle one holds relations such as 'above' or 'cuts')")
    cols, has_header = lay
    m = Matrix()
    for r in rows[1:] if has_header else rows:
        cell = lambda k: r[cols[k]].strip() if k in cols and cols[k] < len(r) else ""   # noqa: E731
        a, word, b = cell("from"), cell("rel"), cell("to")
        if not a:
            continue
        m.unit(a, group=cell("group"), period=cell("period"), phase=cell("phase"),
               description=cell("description"))
        for target in [t for t in b.replace(",", ";").split(";") if t.strip()] if b else []:
            m.relate(a, word, target, recorded_on=a)
    return m
