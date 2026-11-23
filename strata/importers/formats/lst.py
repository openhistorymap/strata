"""The LST list of BASP Harris (Bonn), ArchEd and Stratify.

    Stratigraphic Dataset bonntest           <- header: ignored up to the first unit

  Name
  1
              above: +
              contemporary with:
              equal to:
              below: 2, 3

A line starting in column 0 begins a unit (its name, optionally followed by
a free label); indented ``key: value`` lines describe it. ``above: X``
under unit U means X lies above U. Multiple targets are comma-separated.
Stratify's extended form adds ``part of:``, ``Unit class:`` (context,
group, work area), ``Unit type:``, ``Phase:``, ``Description:``, years and
any free attribute; several excavations write units as ``SITE::unit``.
``+`` and ``-`` are BASP's top and bottom markers and are dropped.

No formal specification exists; this follows the Stratify 1.5 manual
(pp. 21-24) and the sample files shipped with stratigraphr and harris2gv.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..matrices import Matrix
from ..verbs import verb

HEADER = re.compile(r"^\s*(stratigraphic dataset\b.*|name)\s*$", re.I)
ATTR = re.compile(r"^\s+([^:]+?)\s*:\s?(.*)$")
MARKERS = {"+", "-"}
# relation keys in an LST record: the unit is the object of the stated relation
INVERSE = {"above": "below", "below": "above", "contemporary with": "contemporary with",
           "equal to": "equal to", "part of": "part of"}
GROUP_CLASSES = {"group", "work area"}
ATTRS = {"unit type": "type", "phase": "phase", "period": "period", "description": "description",
         "first year": "start_year", "last year": "end_year", "label": "label"}


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("cp850", "replace")


def sniff(path: Path) -> bool:
    if path.suffix.lower() == ".lst":
        return True
    try:
        head = _decode(path.read_bytes()[:4096])
    except (IsADirectoryError, PermissionError):
        return False
    return bool(re.search(r"^\s*Stratigraphic Dataset\b", head, re.I | re.M)
                or len(re.findall(r"^\s+(above|below|contemporary with|equal to):", head, re.I | re.M)) >= 2)


def _year(value: str) -> int | None:
    m = re.match(r"^\s*(-?\d+)(?:[.,]\d+)?\s*$", value)
    return int(m.group(1)) if m else None


def read(path: Path, **_: Any) -> Matrix:
    text = _decode(path.read_bytes())
    units: dict[str, dict[str, Any]] = {}
    links: list[tuple[str, str, str]] = []           # (unit, key, target)
    current: str | None = None
    started = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if not started and HEADER.match(line):
            continue
        if not line[0].isspace():
            started = True
            name, _, label = line.strip().partition(" ")
            current = name
            u = units.setdefault(name, {})
            if label.strip():
                u.setdefault("label", label.strip())
            continue
        m = ATTR.match(line)
        if not m or current is None:
            continue
        key, value = m.group(1).strip().lower(), m.group(2).strip()
        if key in INVERSE:
            for target in (t.strip() for t in value.split(",")):
                if target:
                    links.append((current, key, target))
        elif key == "unit class":
            units[current]["class"] = value.lower()
        elif key in ATTRS and value:
            v = _year(value) if ATTRS[key].endswith("_year") else value
            if v is not None:
                units[current].setdefault(ATTRS[key], v)

    groups = {n for n, u in units.items() if u.get("class") in GROUP_CLASSES}
    m = Matrix()
    for name, u in units.items():
        if name in MARKERS or name in groups:
            continue
        excavation, _, short = name.rpartition("::")
        m.unit(name, label=u.get("label") or short, type=u.get("type"), phase=u.get("phase"),
               period=u.get("period"), description=u.get("description"),
               start_year=u.get("start_year"), end_year=u.get("end_year"), group=excavation or None)
    for unit, key, target in links:
        if unit in MARKERS or target in MARKERS or unit in groups:
            continue
        if key == "part of":
            if target in groups:
                m.units[unit]["group"] = target
            else:
                m.relate(unit, "within", target, recorded_on=unit)
            continue
        if target in groups:
            continue
        # "above: X" under U: X is above U
        m.relate(target, key, unit, recorded_on=unit)
    if groups:
        m.notes.append(f"{len(groups)} group / work-area units became the units' groups")
    return m
