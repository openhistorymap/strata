"""Read Harris matrices from the formats archaeologists already have.

Every reader returns a :class:`Matrix`: the units as recorded and the
relations as stated, each with the verb the file used mapped to a Strata
verb (``strata.importers.verbs``). :func:`to_source` turns a matrix into the
source shape :func:`strata.importers.load.import_site` loads, so a converted
matrix gets exactly the treatment of an Open Context site: canonical ABOVE
edges, contradiction checks, matrix depth, periods and phases.

Readers written here: ArchEd / BASP / Stratify ``.lst`` lists, pyArchInit
(its SQLite database or a CSV of the US table), Field Desktop CSV and JSON
Lines exports, Graphviz DOT, GraphML (yEd, Gephi, igraph...), and plain
relation tables (``from, relation, to``). Through harris-mcp, also Harris
Matrix Composer (.hmc, .hmcx and its CSV export), T. S. Dye's hm data
packages, HMDP JSON, paired contexts/relations CSVs and XLSX.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .verbs import verb as to_verb

UNIT_FIELDS = ("label", "type", "period", "phase", "group", "description")


@dataclass
class Matrix:
    """Units and stated relations, as read from one file."""

    units: dict[str, dict[str, Any]] = field(default_factory=dict)
    relations: list[dict[str, str]] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)   # relation words that are not stratigraphic
    notes: list[str] = field(default_factory=list)

    def unit(self, uid: str, **attrs: Any) -> dict[str, Any]:
        uid = str(uid).strip()
        u = self.units.setdefault(uid, {})
        for k, v in attrs.items():
            if v not in (None, "") and not u.get(k):
                u[k] = str(v).strip() if not isinstance(v, (int, float)) else v
        return u

    def relate(self, a: str, phrase: str, b: str, *, recorded_on: str | None = None) -> bool:
        """Record "<a> <phrase> <b>". Returns False (and counts the word) if
        the phrase is not a stratigraphic relation Strata knows."""
        a, b = str(a).strip(), str(b).strip()
        v = to_verb(phrase)
        if v is None:
            self.skipped[str(phrase).strip()] += 1
            return False
        if not a or not b or a == b:
            return False
        self.unit(a)
        self.unit(b)
        self.relations.append({"a": a, "verb": v, "b": b, "on": recorded_on or a})
        return True

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"units": len(self.units), "relations": len(self.relations),
                               "verbs": dict(Counter(r["verb"] for r in self.relations).most_common())}
        if self.skipped:
            out["skipped_relation_words"] = dict(self.skipped.most_common())
        if self.notes:
            out["notes"] = self.notes
        return out


@dataclass(frozen=True)
class Format:
    name: str
    label: str
    extensions: tuple[str, ...]
    read: Callable[..., Matrix]
    sniff: Callable[[Path], bool] | None = None
    via: str = "strata"


def _head(path: Path, n: int = 4096) -> str:
    try:
        with path.open("rb") as f:
            return f.read(n).decode("utf-8", "replace")
    except (IsADirectoryError, PermissionError):
        return ""


def _formats() -> list[Format]:
    from .formats import dot, edges, field_desktop, graphml, lst, pyarchinit
    from . import harris
    return [
        Format("lst", "ArchEd / BASP Harris / Stratify list (.lst)", (".lst",), lst.read, lst.sniff),
        Format("pyarchinit", "pyArchInit: its SQLite database or a CSV export of the US table",
               (".sqlite", ".db"), pyarchinit.read, pyarchinit.sniff),
        Format("field", "Field Desktop (iDAI.field) CSV or JSON Lines export", (".jsonl",),
               field_desktop.read, field_desktop.sniff),
        Format("dot", "Graphviz DOT drawing (edges point from the later unit down to the earlier one)",
               (".dot", ".gv"), dot.read, dot.sniff),
        Format("graphml", "GraphML from yEd, Gephi, igraph or networkx (same edge direction as DOT)",
               (".graphml",), graphml.read, graphml.sniff),
        Format("edges", "a relation table: one row per statement, columns from, relation, to "
               "(any language; CSV, TSV or semicolon-separated)", (), edges.read, edges.sniff),
        *harris.formats(),
    ]


def formats() -> list[Format]:
    return _formats()


def by_name(name: str) -> Format:
    for f in _formats():
        if f.name == name:
            return f
    raise ValueError(f"unknown format {name!r}; known: {', '.join(f.name for f in _formats())}")


def detect(path: str | Path) -> Format:
    """Pick the format by content first (CSV and JSON are shared by several), then by extension."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    fmts = _formats()
    for f in fmts:
        if f.sniff and f.sniff(p):
            return f
    for f in fmts:
        if p.suffix.lower() in f.extensions:
            return f
    raise ValueError(f"cannot tell the format of {p.name}; pass one of: {', '.join(f.name for f in fmts)}")


def read(path: str | Path, fmt: str = "auto", **options: Any) -> tuple[Matrix, Format]:
    f = detect(path) if fmt in ("auto", None, "") else by_name(fmt)
    m = f.read(Path(path), **options)
    if not m.units:
        raise ValueError(f"{Path(path).name}: read as {f.name}, but no stratigraphic units were found")
    return m, f


def delimiter(text: str) -> str:
    """The field separator of a CSV-like text, judged on its header line.
    (csv.Sniffer on whole rows is fooled by quoted cells holding lists.)"""
    first = text.lstrip("\ufeff").split("\n", 1)[0]
    counts = {d: len(re.findall(re.escape(d) + r'(?=(?:[^"]*"[^"]*")*[^"]*$)', first)) for d in ",;\t|"}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def site_id(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "matrix"


def to_source(m: Matrix, site: dict[str, Any], *, source_file: str, fmt: str) -> dict[str, Any]:
    """The shape :func:`strata.importers.load.import_site` loads."""
    sid = site["id"]
    contexts = []
    for uid, u in m.units.items():
        group = u.get("group")
        path = f"{group}/{uid}" if group and "/" not in uid else uid
        row = {"uid": f"{sid}:{uid}", "label": u.get("label") or uid, "path": path,
               "trench": u.get("group"), "type": u.get("type"), "period_label": u.get("period"),
               "phase": u.get("phase"), "description": u.get("description"),
               "start_year": u.get("start_year"), "end_year": u.get("end_year")}
        contexts.append({k: v for k, v in row.items() if v not in (None, "")})
    statements = [{"subject": f"{sid}:{r['a']}", "verb": r["verb"], "object": f"{sid}:{r['b']}",
                   "source": f"{source_file}: {r['on']}"} for r in m.relations]
    return {"site": {**site, "source_file": source_file, "source_format": fmt},
            "contexts": contexts, "statements": statements, "finds": []}


def matrix_source(path: str | Path, site: dict[str, Any], fmt: str = "auto", **options: Any) -> dict[str, Any]:
    m, f = read(path, fmt, **options)
    return to_source(m, site, source_file=Path(path).name, fmt=f.name)
