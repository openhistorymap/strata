"""pyArchInit (QGIS): its SQLite database, or a CSV export of the US table.

Units are rows of ``us_table`` (sito, area, us, unita_tipo, d_stratigrafica,
d_interpretativa, periodo_iniziale, fase_iniziale, ...). Relations are in
``rapporti``: the ``str()`` of a Python list, each entry
``[relation, us, area, site]`` (older data: ``[relation, us]``), with the
relation in Italian, English or German as pyArchInit's form offers it
("Copre", "Covered by", "Wird geschnitten"...). Extended Matrix connectors
(``>``, ``<``, ``>>``, ``<<``, ``<->``) are reported as skipped, not guessed.

A unit is named by its US number, prefixed with its area (``A/12``) when
the file has several areas and with its site when it has several sites;
pass ``sito=...`` to read one site only.
"""

from __future__ import annotations

import ast
import csv
import io
import sqlite3
from pathlib import Path
from typing import Any

from ..matrices import Matrix, delimiter

COLUMNS = ("sito", "area", "us", "unita_tipo", "d_stratigrafica", "d_interpretativa",
           "periodo_iniziale", "fase_iniziale", "rapporti")


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except (IsADirectoryError, PermissionError):
        return False


def _csv_header(path: Path) -> list[str]:
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as f:
            first = f.readline()
    except (IsADirectoryError, PermissionError):
        return []
    return [c.strip().lower() for c in next(csv.reader([first], delimiter=delimiter(first)), [])]


def sniff(path: Path) -> bool:
    if _is_sqlite(path):
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
                return db.execute("SELECT 1 FROM sqlite_master WHERE name = 'us_table'").fetchone() is not None
        except sqlite3.Error:
            return False
    if path.suffix.lower() not in (".csv", ".tsv", ".txt"):
        return False
    h = set(_csv_header(path))
    return {"us", "rapporti"} <= h


def _rows(path: Path) -> list[dict[str, Any]]:
    if _is_sqlite(path):
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            have = {r[1] for r in db.execute("PRAGMA table_info(us_table)")}
            cols = [c for c in COLUMNS if c in have]
            return [dict(r) for r in db.execute(f"SELECT {', '.join(cols)} FROM us_table")]
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return [{k.strip().lower(): v for k, v in r.items() if k}
            for r in csv.DictReader(io.StringIO(text), delimiter=delimiter(text))]


def _relations(cell: Any) -> list[list[str]]:
    if not cell or not str(cell).strip():
        return []
    try:
        value = ast.literal_eval(str(cell))
    except (ValueError, SyntaxError):
        return []
    return [[str(x).strip() for x in e] for e in value if isinstance(e, (list, tuple)) and len(e) >= 2]


def _num(v: Any) -> str:
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s


def read(path: Path, sito: str | None = None, **_: Any) -> Matrix:
    rows = [r for r in _rows(path) if r.get("us") not in (None, "")]
    if sito:
        rows = [r for r in rows if str(r.get("sito") or "").strip() == sito]
    sites = {str(r.get("sito") or "").strip() for r in rows}
    areas = {(str(r.get("sito") or "").strip(), str(r.get("area") or "").strip()) for r in rows}

    def name(site: str, area: str, us: str) -> str:
        parts = ([site] if len(sites) > 1 else []) + ([area] if len(areas) > 1 and area else []) + [us]
        return "/".join(parts)

    m = Matrix()
    for r in rows:
        site, area, us = str(r.get("sito") or "").strip(), str(r.get("area") or "").strip(), _num(r["us"])
        period, phase = (str(r.get(k) or "").strip() for k in ("periodo_iniziale", "fase_iniziale"))
        tipo = str(r.get("unita_tipo") or "").strip()
        m.unit(name(site, area, us), label=f"{tipo} {us}".strip() if tipo else us, type=tipo or None,
               group=area or None, period=f"Periodo {period}" if period else None,
               phase=f"{period}-{phase}" if period and phase else None,
               description=str(r.get("d_interpretativa") or r.get("d_stratigrafica") or "").strip() or None)
    for r in rows:
        site, area, us = str(r.get("sito") or "").strip(), str(r.get("area") or "").strip(), _num(r["us"])
        me = name(site, area, us)
        for e in _relations(r.get("rapporti")):
            word, other = e[0], _num(e[1])
            o_area = e[2] if len(e) > 2 and e[2] else area
            o_site = e[3] if len(e) > 3 and e[3] else site
            if sito and o_site != sito:
                m.skipped[f"(relation to another site: {o_site})"] += 1
                continue
            m.relate(me, word, name(o_site, o_area, other), recorded_on=me)
    return m
