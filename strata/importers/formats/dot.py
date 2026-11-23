"""Graphviz DOT: Harris matrices drawn by hand or exported by other tools.

Convention: an edge points from the later (upper) unit down to the earlier
one, which is how a matrix is drawn top to bottom. ``reverse=True`` flips
that for drawings whose arrows run from earlier to later. An edge's
``label``, ``relation``, ``type`` or ``kind`` attribute (in any language
Strata knows) overrides the default "above"; an undirected edge (``--``,
or ``dir=none``) without a word is read as "contemporary". Node attributes
``label``, ``type``, ``period``, ``phase``, ``description`` are kept, and a
``cluster`` subgraph's label becomes the units' group.

A small parser covering the DOT language (statements, chains, subgraphs,
default attributes, comments, quoted and HTML strings), so no dependency.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..matrices import Matrix
from ..verbs import verb

TOKEN = re.compile(r"""
    (?P<ws>\s+|//[^\n]*|\#[^\n]*|/\*.*?\*/)
  | (?P<edge>->|--)
  | (?P<punct>[{}\[\];,=:])
  | (?P<quoted>"(?:\\.|[^"\\])*")
  | (?P<html><)
  | (?P<id>[A-Za-z_\x80-￿][\w\x80-￿]*|-?(?:\.\d+|\d+(?:\.\d*)?))
""", re.X | re.S)

WORD_ATTRS = ("relation", "kind", "type", "label", "xlabel")


def sniff(path: Path) -> bool:
    if path.suffix.lower() in (".dot", ".gv"):
        return True
    head = re.sub(r"//[^\n]*|/\*.*?\*/|#[^\n]*", "", _read(path)[:2000], flags=re.S).lstrip().lower()
    return bool(re.match(r"(strict\s+)?(di)?graph\b[^{]*\{", head))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except (IsADirectoryError, PermissionError):
        return ""


def tokens(text: str) -> list[tuple[str, str]]:
    out, i = [], 0
    while i < len(text):
        m = TOKEN.match(text, i)
        if not m:
            raise ValueError(f"DOT: unexpected {text[i:i + 20]!r} at character {i}")
        kind = m.lastgroup
        if kind == "html":                      # <...> with nesting
            depth, j = 0, i
            while j < len(text):
                depth += {"<": 1, ">": -1}.get(text[j], 0)
                j += 1
                if depth == 0:
                    break
            out.append(("id", re.sub(r"<[^>]*>", " ", text[i + 1:j - 1]).strip()))
            i = j
            continue
        if kind == "quoted":
            out.append(("id", m.group()[1:-1].replace('\\"', '"').replace("\\\n", "")))
        elif kind == "id":
            out.append(("id", m.group()))
        elif kind != "ws":
            out.append((kind, m.group()))
        i = m.end()
    return out


class _Parser:
    def __init__(self, toks: list[tuple[str, str]]):
        self.t, self.i = toks, 0
        self.nodes: dict[str, dict[str, str]] = {}
        self.edges: list[tuple[str, str, dict[str, str], bool]] = []
        self.directed = True

    def peek(self, k: int = 0) -> tuple[str, str]:
        return self.t[self.i + k] if self.i + k < len(self.t) else ("eof", "")

    def take(self, value: str | None = None) -> str:
        kind, v = self.peek()
        if value is not None and v.lower() != value:
            raise ValueError(f"DOT: expected {value!r}, found {v!r}")
        self.i += 1
        return v

    def graph(self) -> None:
        if self.peek()[1].lower() == "strict":
            self.take()
        kw = self.take().lower()
        if kw not in ("graph", "digraph"):
            raise ValueError("DOT: a file starts with 'graph' or 'digraph'")
        self.directed = kw == "digraph"
        if self.peek()[0] == "id":
            self.take()
        self.block({"node": {}, "edge": {}}, None)

    def attrs(self) -> dict[str, str]:
        out: dict[str, str] = {}
        while self.peek()[1] == "[":
            self.take("[")
            while self.peek()[1] != "]":
                key = self.take()
                val = "true"
                if self.peek()[1] == "=":
                    self.take("=")
                    val = self.take()
                out[key.lower()] = val
                if self.peek()[1] in (",", ";"):
                    self.take()
            self.take("]")
        return out

    def block(self, defaults: dict[str, dict[str, str]], group: str | None) -> tuple[list[str], str | None]:
        """Parse { stmt_list }; returns the node ids it mentions and the graph label."""
        defaults = {k: dict(v) for k, v in defaults.items()}
        self.take("{")
        members: list[str] = []
        graph_label = None
        while self.peek()[1] != "}":
            if self.peek()[0] == "eof":
                raise ValueError("DOT: missing '}'")
            kind, v = self.peek()
            lv = v.lower()
            if kind == "punct" and v == ";":
                self.take()
                continue
            if kind == "id" and lv in ("node", "edge") and self.peek(1)[1] == "[":
                self.take()
                defaults[lv].update(self.attrs())
                continue
            if kind == "id" and lv == "graph" and self.peek(1)[1] == "[":
                self.take()
                graph_label = self.attrs().get("label", graph_label)
                continue
            if kind == "id" and self.peek(1)[1] == "=":
                key = self.take().lower()
                self.take("=")
                val = self.take()
                if key == "label":
                    graph_label = val
                continue
            ends = [self.endpoint(defaults, group)]
            ops = []
            while self.peek()[0] == "edge":
                ops.append(self.take())
                ends.append(self.endpoint(defaults, group))
            a = self.attrs()
            if not ops:
                for n in ends[0]:
                    self.nodes.setdefault(n, {}).update(a)
            else:
                ea = {**defaults["edge"], **a}
                for (left, op, right) in zip(ends, ops, ends[1:]):
                    for x in left:
                        for y in right:
                            self.edges.append((x, y, ea, op == "->" and ea.get("dir", "") != "none"))
            for e in ends:
                members.extend(e)
        self.take("}")
        return members, graph_label

    def endpoint(self, defaults: dict[str, dict[str, str]], group: str | None) -> list[str]:
        kind, v = self.peek()
        if v.lower() == "subgraph" or v == "{":
            name = None
            if v.lower() == "subgraph":
                self.take()
                if self.peek()[0] == "id":
                    name = self.take()
            sub_group = group
            members, label = self.block(defaults, group)
            if name and name.lower().startswith("cluster"):
                sub_group = label or name[len("cluster"):].lstrip("_ ") or name
                for n in members:
                    self.nodes.setdefault(n, {}).setdefault("_group", sub_group)
            return members
        node = self.take()
        if self.peek()[1] == ":":                 # port
            self.take()
            self.take()
            if self.peek()[1] == ":":
                self.take()
                self.take()
        attrs = self.nodes.setdefault(node, {})
        for k, val in defaults["node"].items():
            attrs.setdefault(k, val)
        return [node]


def read(path: Path, reverse: bool = False, **_: Any) -> Matrix:
    p = _Parser(tokens(_read(path)))
    p.graph()
    m = Matrix()
    for n, a in p.nodes.items():
        label = a.get("label")
        if label in (None, "", "\\N"):
            label = n
        m.unit(n, label=" ".join(label.replace("\\n", " ").replace("\\l", " ").split()),
               type=a.get("unit_type") or a.get("unittype") or a.get("type"), period=a.get("period"),
               phase=a.get("phase"), description=a.get("description") or a.get("tooltip"),
               group=a.get("group") or a.get("_group"))
    for a, b, attrs, directed in p.edges:
        word = next((attrs[k] for k in WORD_ATTRS if attrs.get(k) and verb(attrs[k])), None)
        if word is None:
            if not directed or not p.directed:
                word = "contemporary"
            else:
                word = "above"
        if reverse and directed:
            a, b = b, a
        m.relate(a, word, b, recorded_on=a)
    if not p.directed and not any(r["verb"] != "contemporary" for r in m.relations) and m.relations:
        m.notes.append("an undirected graph without relation words gives no order: "
                       "every edge was read as 'contemporary'")
    return m
