"""Export the store for the browser viewer (``strata export-web``).

The WASM build of Relate runs in-memory only and cannot open a ``.rl`` file,
so the viewer rebuilds the graph in the browser from a JSON dump of this
store: every node and relationship, with all its properties, written as rows
that ``UNWIND $rows AS r CREATE ...`` loads back in batches.

Two files, so the page is usable before everything has arrived:

- ``core.json``  sites, trenches, units, periods, issues and the matrix
- ``finds.json`` finds, taxa and the rest (loaded in the background)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import SCHEMA_FILE, Store

# Every label's key property: what relationships are matched on when loading.
KEYS = {
    "Site": "id", "Trench": "uid", "Context": "uid", "Find": "uid", "Period": "uid",
    "Phase": "uid", "Taxon": "uri", "Edit": "id", "Issue": "id",
}
LATER = {"Find", "Taxon"}                     # labels loaded in the second file
LATER_RELS = {"FROM", "IDENTIFIED_AS", "ABUTS"}


def _plain(props: dict) -> tuple[dict, dict]:
    """Split properties into JSON-able values and points ({latitude, longitude})."""
    out, points = {}, {}
    for k, v in props.items():
        if hasattr(v, "latitude") and hasattr(v, "longitude"):
            points[k] = {"latitude": v.latitude, "longitude": v.longitude}
        elif v is not None:
            out[k] = v
    return out, points


def _schema() -> list[str]:
    statements = []
    for statement in SCHEMA_FILE.read_text().split(";"):
        lines = [l for l in statement.splitlines() if l.strip() and not l.strip().startswith("//")]
        if lines:
            statements.append("\n".join(lines))
    return statements


def export_web(store: Store, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: dict[str, dict] = {"core": {"nodes": [], "edges": []}, "finds": {"nodes": [], "edges": []}}

    labels = [r["label"] for r in store.run("MATCH (n) UNWIND labels(n) AS label RETURN DISTINCT label ORDER BY label")]
    for label in labels:
        key = KEYS.get(label)
        if key is None:
            raise ValueError(f"no key property known for label {label}; add it to web_export.KEYS")
        rows = []
        for r in store.run(f"MATCH (n:{label}) RETURN properties(n) AS p ORDER BY n.{key}"):
            props, points = _plain(r["p"])
            rows.append({"p": props, **({"pt": points} if points else {})})
        part = "finds" if label in LATER else "core"
        parts[part]["nodes"].append({"label": label, "key": key, "rows": rows})

    combos = store.run("MATCH (a)-[r]->(b) RETURN DISTINCT type(r) AS type, labels(a)[0] AS la, labels(b)[0] AS lb "
                       "ORDER BY type, la, lb")
    for c in combos:
        ka, kb = KEYS[c["la"]], KEYS[c["lb"]]
        rows = [[r["a"], r["b"], _plain(r["p"])[0]] for r in store.run(
            f"MATCH (a:{c['la']})-[r:{c['type']}]->(b:{c['lb']}) "
            f"RETURN a.{ka} AS a, b.{kb} AS b, properties(r) AS p ORDER BY a, b")]
        part = "finds" if c["type"] in LATER_RELS or c["la"] in LATER or c["lb"] in LATER else "core"
        parts[part]["edges"].append({"type": c["type"], "from": [c["la"], ka], "to": [c["lb"], kb], "rows": rows})

    built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary: dict[str, Any] = {}
    for name, part in parts.items():
        part["meta"] = {"part": name, "built": built, "schema": _schema() if name == "core" else []}
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(part, ensure_ascii=False, separators=(",", ":")))
        summary[name] = {
            "file": str(path),
            "bytes": path.stat().st_size,
            "nodes": sum(len(n["rows"]) for n in part["nodes"]),
            "relationships": sum(len(e["rows"]) for e in part["edges"]),
        }
    return summary
