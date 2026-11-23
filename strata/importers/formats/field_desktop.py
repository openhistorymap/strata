"""Field Desktop (iDAI.field, German Archaeological Institute).

Two shapes are read:

- **CSV export**: one category per file, named ``<project>.<Category>.csv``.
  Columns: ``identifier``, ``shortDescription`` (or ``shortDescription.en``
  ...), the configured fields, ``period.value``, ``dating.N.*`` and one
  ``relations.<name>`` column per relation, its targets joined by ``;``
  (identifiers, not internal ids). A folder of such files is read whole.
- **JSON Lines** (the import format): one object per line with
  ``identifier``, ``category`` and ``relations: {name: [identifiers]}``.

Stratigraphic relations: isAbove/isBelow, cuts/isCutBy, fills/isFilledBy,
isAfter/isBefore, abuts/isAbuttedBy, isContemporaryWith, bondsWith,
isSameAs. The hierarchy (isRecordedIn, liesWithin, isChildOf) gives the
unit's group; borders and isPresentIn are not stratigraphic and are skipped.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from ..matrices import Matrix, delimiter

HIERARCHY = ("isRecordedIn", "liesWithin", "isChildOf")
NOT_ORDER = {"borders", "isPresentIn", "isDepictedIn", "depicts", "isMapLayerOf", "hasMapLayer",
             "isInstanceOf", "hasInstance"}


def _csv_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _csv_header(path: Path) -> list[str]:
    try:
        first = _csv_text(path).split("\n", 1)[0]
    except (IsADirectoryError, PermissionError):
        return []
    return next(csv.reader([first], delimiter=delimiter(first)), [])


def _is_field_csv(path: Path) -> bool:
    h = _csv_header(path)
    return bool(h) and h[0].strip() == "identifier" and any(c.startswith("relations.") for c in h)


def _is_field_jsonl(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8-sig") as f:
            first = json.loads(f.readline())
    except (ValueError, UnicodeDecodeError, IsADirectoryError, PermissionError):
        return False
    return isinstance(first, dict) and "identifier" in first and isinstance(first.get("relations"), dict)


def sniff(path: Path) -> bool:
    if path.is_dir():
        return any(_is_field_csv(p) for p in sorted(path.glob("*.csv")))
    if path.suffix.lower() in (".jsonl", ".ndjson", ".json"):
        return _is_field_jsonl(path)
    return path.suffix.lower() in (".csv", ".tsv", ".txt") and _is_field_csv(path)


def _year(kind: str, year: str) -> int | None:
    try:
        y = int(float(year))
    except (TypeError, ValueError):
        return None
    kind = (kind or "").lower()
    return -y if kind == "bce" else 1950 - y if kind == "bp" else y


def _records_csv(path: Path) -> list[dict[str, Any]]:
    text = _csv_text(path)
    parts = path.name.split(".")
    category = parts[-2] if len(parts) >= 3 else None
    out = []
    for row in csv.DictReader(io.StringIO(text), delimiter=delimiter(text)):
        rels: dict[str, list[str]] = {}
        for k, v in row.items():
            if k and k.startswith("relations.") and v:
                rels[k.split(".", 1)[1]] = [t.strip() for t in v.split(";") if t.strip()]
        desc = next((v for k, v in row.items() if k and re.fullmatch(r"shortDescription(\.\w+)?", k) and v), None)
        out.append({"identifier": row.get("identifier"), "category": row.get("category") or category,
                    "shortDescription": desc, "period": row.get("period.value") or row.get("period"),
                    "start_year": _year(row.get("dating.0.begin.inputType"), row.get("dating.0.begin.inputYear")),
                    "end_year": _year(row.get("dating.0.end.inputType"), row.get("dating.0.end.inputYear")),
                    "relations": rels})
    return out


def _records_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        period = r.get("period")
        desc = r.get("shortDescription")
        out.append({**r, "period": period.get("value") if isinstance(period, dict) else period,
                    "shortDescription": next(iter(desc.values()), None) if isinstance(desc, dict) else desc})
    return out


def read(path: Path, **_: Any) -> Matrix:
    if path.is_dir():
        records = [r for p in sorted(path.glob("*.csv")) if _is_field_csv(p) for r in _records_csv(p)]
    elif _is_field_jsonl(path):
        records = _records_jsonl(path)
    else:
        records = _records_csv(path)
    m = Matrix()
    for r in records:
        rels = r.get("relations") or {}
        parent = next((rels[k] for k in HIERARCHY if rels.get(k)), None)
        if isinstance(parent, list):
            parent = parent[0] if parent else None
        m.unit(r["identifier"], type=r.get("category"), description=r.get("shortDescription"),
               period=r.get("period"), group=parent, start_year=r.get("start_year"), end_year=r.get("end_year"))
    stratigraphic = {r["identifier"] for r in records
                     if any(k not in HIERARCHY and k not in NOT_ORDER for k in (r.get("relations") or {}))}
    for r in records:
        for name, targets in (r.get("relations") or {}).items():
            if name in HIERARCHY:
                continue
            if name in NOT_ORDER:
                m.skipped[name] += len(targets if isinstance(targets, list) else [targets])
                continue
            for t in targets if isinstance(targets, list) else [targets]:
                m.relate(r["identifier"], name, t, recorded_on=r["identifier"])
    # trenches, features and finds come in the same export: keep only the stratigraphy
    related = {x["a"] for x in m.relations} | {x["b"] for x in m.relations}
    for ident in [i for i in m.units if i not in stratigraphic and i not in related]:
        del m.units[ident]
    return m
