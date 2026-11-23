"""GraphML, as written by yEd, Gephi, igraph, networkx and Cytoscape.

Same convention as DOT: an edge points from the later (upper) unit to the
earlier one, unless ``reverse=True``. Node data keys named like ``label``,
``type``, ``period``, ``phase``, ``group``, ``description`` are kept; yEd's
node and edge labels (``y:NodeLabel``, ``y:EdgeLabel``) are read too, and
yEd group nodes (nested graphs) become the units' group. An edge's
relation word comes from a data key named ``relation``, ``kind``,
``type``, ``label`` or from its yEd label; without one it is "above".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ..matrices import Matrix
from ..verbs import verb

NS = "{http://graphml.graphdrawing.org/xmlns}"
NODE_KEYS = {"label": "label", "name": "label", "type": "type", "unit_type": "type", "period": "period",
             "phase": "phase", "group": "group", "area": "group", "trench": "group",
             "description": "description"}
EDGE_WORD_KEYS = ("relation", "kind", "type", "label", "verb", "edge_type")


def sniff(path: Path) -> bool:
    if path.suffix.lower() == ".graphml":
        return True
    try:
        with path.open("rb") as f:
            return b"<graphml" in f.read(2048)
    except (IsADirectoryError, PermissionError):
        return False


def _text(el: ET.Element, suffix: str) -> str | None:
    """Text of the first descendant whose tag ends with suffix (any namespace)."""
    for d in el.iter():
        if d.tag.endswith(suffix) and (d.text or "").strip():
            return " ".join(d.text.split())
    return None


def read(path: Path, reverse: bool = False, **_: Any) -> Matrix:
    root = ET.parse(path).getroot()
    keys: dict[str, str] = {}
    for k in root.iter(f"{NS}key"):
        name = (k.get("attr.name") or k.get("yfiles.type") or k.get("id") or "").lower()
        keys[k.get("id")] = name

    def data(el: ET.Element) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for d in el.findall(f"{NS}data"):
            name = keys.get(d.get("key"), d.get("key", "")).lower()
            if name == "nodegraphics":
                out.setdefault("label", _text(d, "NodeLabel"))
            elif name == "edgegraphics":
                out.setdefault("label", _text(d, "EdgeLabel"))
            elif (d.text or "").strip():
                out[name] = d.text.strip()
        return out

    m = Matrix()
    directed_default = True

    def walk(graph: ET.Element, group: str | None) -> None:
        nonlocal directed_default
        if graph.get("edgedefault") == "undirected":
            directed_default = False
        for node in graph.findall(f"{NS}node"):
            d = data(node)
            sub = node.find(f"{NS}graph")
            if sub is not None:                       # yEd group node
                walk(sub, d.get("label") or node.get("id"))
                continue
            attrs = {NODE_KEYS[k]: v for k, v in d.items() if k in NODE_KEYS}
            attrs.setdefault("group", group)
            m.unit(node.get("id"), **attrs)
        for edge in graph.findall(f"{NS}edge"):
            d = data(edge)
            a, b = edge.get("source"), edge.get("target")
            directed = edge.get("directed", "true" if directed_default else "false") == "true"
            word = next((d[k] for k in EDGE_WORD_KEYS if d.get(k) and verb(d[k])), None)
            if word is None:
                word = "above" if directed else "contemporary"
            if reverse and directed:
                a, b = b, a
            m.relate(a, word, b, recorded_on=a)

    for g in root.findall(f"{NS}graph"):
        walk(g, None)
    # Edges may reference nodes that only exist as ids (labels elsewhere): fine,
    # relate() created them. Nodes inside groups can be referenced from the top level.
    return m
