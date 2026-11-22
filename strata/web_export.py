"""Export the store for the browser viewer (``strata export-web``).

The WASM build of Relate runs in-memory only and cannot open a ``.rl`` file,
so the viewer rebuilds the graph in the browser from a JSON dump of this
store: every node and relationship, with all its properties, written as rows
that ``UNWIND $rows AS r CREATE ...`` loads back in batches.

Each site becomes a dataset of two parts, so the page is usable before
everything has arrived:

- ``core``  the site, trenches, units, periods, issues and the matrix
- ``finds`` finds, taxa and the rest (loaded in the background)

``strata export-web`` writes ``web/data/<site>/{core,finds}.json`` for every
site plus ``web/data/datasets.json``, the list the viewer offers.
``strata export-web --site S --bundle FILE`` writes one self-contained file
instead, which the viewer opens from disk or from any URL.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import SCHEMA_FILE, Store

FORMAT = "strata-web"
VERSION = 1

# Every label's key property: what relationships are matched on when loading.
KEYS = {
    "Site": "id", "Trench": "uid", "Context": "uid", "Find": "uid", "Period": "uid",
    "Phase": "uid", "Taxon": "uri", "Edit": "id", "Issue": "id",
}
LATER = {"Find", "Taxon"}                     # labels loaded in the second part
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


def sites(store: Store) -> list[dict]:
    return store.run("MATCH (s:Site) RETURN s.id AS id, s.name AS name, s.source AS source, "
                     "s.source_uri AS source_uri, s.license_label AS license ORDER BY s.id")


def dataset(store: Store, site: str) -> dict[str, Any]:
    """One site as a bundle: {format, version, dataset, parts: [core, finds]}."""
    info = next((s for s in sites(store) if s["id"] == site), None)
    if info is None:
        raise ValueError(f"no site {site!r} in {store.path}")
    labels = [r["label"] for r in store.run("MATCH (n) UNWIND labels(n) AS label RETURN DISTINCT label ORDER BY label")]
    for label in labels:
        if label not in KEYS:
            raise ValueError(f"no key property known for label {label}; add it to web_export.KEYS")

    # Nodes of this site: those with site = S, the Site itself, then anything
    # without a site property (taxa) that one of them points to.
    nodes: dict[str, list[dict]] = {}
    keep: dict[str, set] = {}
    for label in labels:
        key = KEYS[label]
        where = "n.id = $site" if label == "Site" else "n.site = $site"
        rows = store.run(f"MATCH (n:{label}) WHERE {where} RETURN properties(n) AS p ORDER BY n.{key}", site=site)
        nodes[label] = [r["p"] for r in rows]
        keep[label] = {p[key] for p in nodes[label]}
    for label in labels:
        if nodes[label]:
            continue
        key = KEYS[label]
        rows = store.run(f"MATCH (m)-->(n:{label}) WHERE m.site = $site "
                         f"RETURN DISTINCT properties(n) AS p ORDER BY p.{key}", site=site)
        nodes[label] = [r["p"] for r in rows]
        keep[label] = {p[key] for p in nodes[label]}

    parts: dict[str, dict] = {"core": {"nodes": [], "edges": []}, "finds": {"nodes": [], "edges": []}}
    for label in labels:
        if not nodes[label]:
            continue
        rows = []
        for p in nodes[label]:
            props, points = _plain(p)
            rows.append({"p": props, **({"pt": points} if points else {})})
        parts["finds" if label in LATER else "core"]["nodes"].append({"label": label, "key": KEYS[label], "rows": rows})

    combos = store.run("MATCH (a)-[r]->(b) RETURN DISTINCT type(r) AS type, labels(a)[0] AS la, labels(b)[0] AS lb "
                       "ORDER BY type, la, lb")
    for c in combos:
        ka, kb = KEYS[c["la"]], KEYS[c["lb"]]
        rows = [[r["a"], r["b"], _plain(r["p"])[0]] for r in store.run(
            f"MATCH (a:{c['la']})-[r:{c['type']}]->(b:{c['lb']}) "
            f"RETURN a.{ka} AS a, b.{kb} AS b, properties(r) AS p ORDER BY a, b")
            if r["a"] in keep[c["la"]] and r["b"] in keep[c["lb"]]]
        if not rows:
            continue
        part = "finds" if c["type"] in LATER_RELS or c["la"] in LATER or c["lb"] in LATER else "core"
        parts[part]["edges"].append({"type": c["type"], "from": [c["la"], ka], "to": [c["lb"], kb], "rows": rows})

    built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {**info, "built": built, "store": Path(store.path).name}
    for name, part in parts.items():
        part["meta"] = {"part": name, "dataset": site, "built": built, "schema": _schema() if name == "core" else []}
    return {"format": FORMAT, "version": VERSION, "dataset": meta, "parts": [parts["core"], parts["finds"]]}


def _counts(part: dict) -> dict[str, int]:
    return {"nodes": sum(len(n["rows"]) for n in part["nodes"]),
            "relationships": sum(len(e["rows"]) for e in part["edges"])}


def _dump(obj: Any, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    return path.stat().st_size


def export_web(store: Store, out_dir: Path) -> dict[str, Any]:
    """Every site as web/data/<site>/{core,finds}.json, and datasets.json listing them."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest, summary = [], {}
    for s in sites(store):
        bundle = dataset(store, s["id"])
        folder = out_dir / s["id"]
        if folder.exists():
            shutil.rmtree(folder)
        files = []
        for part in bundle["parts"]:
            rel = f"{s['id']}/{part['meta']['part']}.json"
            size = _dump(part, out_dir / rel)
            files.append({"path": rel, "bytes": size, **_counts(part)})
        manifest.append({**bundle["dataset"], "parts": files})
        summary[s["id"]] = files
    _dump({"format": FORMAT, "version": VERSION, "datasets": manifest}, out_dir / "datasets.json")
    return summary


def export_bundle(store: Store, site: str, path: Path) -> dict[str, Any]:
    """One site as one self-contained file the viewer can open locally or by URL."""
    bundle = dataset(store, site)
    size = _dump(bundle, path)
    return {"file": str(path), "bytes": size, **{p["meta"]["part"]: _counts(p) for p in bundle["parts"]}}
