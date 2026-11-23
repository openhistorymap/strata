"""Harris-matrix formats read by harris-mcp.

harris-mcp (https://github.com/openhistorymap/harris-mcp, Apache-2.0) parses
HMDP JSON, Harris Matrix Composer (.hmc/.hmcx and its CSV export), T. S.
Dye's hm data packages, paired contexts/relations CSVs and XLSX into its
``Matrix``. Here each becomes a Strata :class:`~strata.importers.matrices.Matrix`
with the relations exactly as stated (harris-mcp keeps them in
``Matrix.relations`` before canonicalising them for its own graph).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .matrices import Format, Matrix, matrix_source  # noqa: F401  (matrix_source: old import path)

# harris-mcp relation kinds -> Strata verbs ("<a> is <verb> <b>")
KINDS = {
    "above": "above", "below": "below", "cuts": "cuts", "cut_by": "cut_by",
    "fills": "fills", "filled_by": "filled_by", "equal": "equals",
    "contemporary": "contemporary", "abuts": "abuts",
}


def _load(fmt: str):
    def read(path: Path, **_: Any) -> Matrix:
        try:
            from harris_mcp import formats as hf
        except ImportError as e:      # the Claude Desktop bundle ships it; a bare checkout may not
            raise RuntimeError(f"reading {fmt} files needs harris-mcp: "
                               "pip install git+https://github.com/openhistorymap/harris-mcp") from e
        hm = hf.load(path, fmt)
        m = Matrix()
        for c in hm.iter_contexts():
            m.unit(c.id, type=c.type, period=c.period, phase=c.phase, group=c.group,
                   description=c.description)
        for r in hm.relations:
            m.relate(r.a, KINDS.get(r.kind, r.kind), r.b, recorded_on=r.attrs.get("recorded_on") or r.a)
        return m
    return read


def _header(path: Path) -> list[str]:
    try:
        with path.open("rb") as f:
            first = f.readline().decode("utf-8-sig", "replace")    # HMC exports are often Latin-1
    except (IsADirectoryError, PermissionError):
        return []
    if first.startswith("HEADER;"):
        return ["HEADER;"]
    return [c.strip().lower() for c in next(csv.reader([first]), [])]


def _csv_sniff(p: Path) -> bool:
    if p.is_dir():
        return (p / "contexts.csv").exists() and (p / "relations.csv").exists()
    if p.suffix.lower() != ".csv":
        return False
    h = _header(p)
    return h == ["HEADER;"] or {"above", "below"} <= set(h) or {"younger", "older"} <= set(h)


def _datapackage_sniff(p: Path) -> bool:
    try:
        from harris_mcp.formats import datapackage as dp
    except ImportError:
        return False
    if p.is_dir():
        return dp.is_hm_dir(p)
    return p.suffix.lower() == ".json" and dp.is_descriptor(p)


def formats() -> list[Format]:
    via = "harris-mcp"
    return [
        Format("hmcx", "Harris Matrix Composer project (.hmcx)", (".hmcx",), _load("hmcx"), via=via),
        Format("hmc", "Harris Matrix Composer XML (.hmc)", (".hmc",), _load("hmc"), via=via),
        Format("datapackage", "T. S. Dye's hm data package (folder, .ini, .zip or datapackage.json)",
               (".zip", ".ini"), _load("datapackage"), _datapackage_sniff, via=via),
        Format("csv", "contexts.csv + relations.csv (a folder), an above/below edge list, "
               "or Harris Matrix Composer's CSV export", (), _load("csv"), _csv_sniff, via=via),
        Format("xlsx", "Excel workbook with contexts and relations sheets", (".xlsx",), _load("xlsx"), via=via),
        Format("hmdp", "HMDP JSON, harris-mcp's own format", (".json",), _load("hmdp"), via=via),
    ]
