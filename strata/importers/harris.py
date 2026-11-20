"""Import any Harris-matrix file harris-mcp can read.

harris-mcp (https://github.com/openhistorymap/harris-mcp) parses HMDP JSON,
Harris Matrix Composer (.hmc/.hmcx), T. S. Dye's hm data packages, CSV and
XLSX into one in-memory ``Matrix``. This module turns that matrix into the
same source shape the Open Context importer produces, so every matrix lands
in the store through :func:`strata.importers.load.import_site`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harris_mcp import formats

# harris-mcp relation kinds -> Strata verbs ("<a> is <verb> <b>")
KINDS = {
    "above": "above", "below": "below", "cuts": "cuts", "cut_by": "cut_by",
    "fills": "fills", "filled_by": "filled_by", "equal": "equals",
    "contemporary": "contemporary", "abuts": "abuts",
}


def matrix_source(path: str | Path, site: dict[str, Any], fmt: str = "auto") -> dict[str, Any]:
    m = formats.load(path, fmt)
    sid = site["id"]
    contexts = []
    for c in m.iter_contexts():
        row = {"uid": f"{sid}:{c.id}", "label": c.id,
               "path": f"{c.group}/{c.id}" if c.group else c.id,
               "type": c.type, "period_label": c.period, "phase": c.phase,
               "group": c.group, "description": c.description}
        contexts.append({k: v for k, v in row.items() if v not in (None, "")})
    statements = [{"subject": f"{sid}:{r.a}", "verb": KINDS[r.kind], "object": f"{sid}:{r.b}",
                   "source": str(path)} for r in m.relations if r.kind in KINDS]
    return {"site": {**site, "source_file": Path(path).name},
            "contexts": contexts, "statements": statements, "finds": []}
