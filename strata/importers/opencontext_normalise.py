"""Turn cached Open Context records into Strata's source CSVs.

Input  (``data/raw/<site>/``, not committed): one JSON-LD record per locus,
       plus JSON-lines search metadata for finds.
Output (``data/sources/<site>/``, committed):

- ``contexts.csv``    one row per locus (stratigraphic unit)
- ``statements.csv``  one row per stratigraphic statement *as recorded*:
                      "locus S is <verb> locus O", on the record of S.
                      Kept raw; canonical edges are built at import time,
                      so the store can say which record made which claim.
- ``finds.csv``       one row per find, with the locus it came from

Only CC BY 4.0 records are written; the licence is checked per record.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

LICENSE = "https://creativecommons.org/licenses/by/4.0"

# Open Context predicate suffix -> Strata verb, read as "<subject> is <verb> <object>".
STRAT_PREDICATES = {
    "above-stratigraphy": "above",
    "below-stratigraphy": "below",
    "cuts-stratigraphy": "cuts",
    "is-cut-by-stratigraphy": "cut_by",
    "equals-stratigraphy": "equals",
    "abuts-stratigraphy": "abuts",
    "within-stratigraphy": "within",
    "contains-stratigraphy": "contains",
}

CONTEXT_FIELDS = [
    "uid", "site", "label", "area", "trench", "path", "type", "period_label",
    "start_year", "end_year", "excavated_from", "excavated_to",
    "top_depth", "bottom_depth", "context_rating", "recorded_by",
    "latitude", "longitude", "description", "uri", "citation",
]
STATEMENT_FIELDS = ["subject", "verb", "object", "source"]
FIND_FIELDS = [
    "uid", "site", "label", "category", "context", "bag", "material", "type",
    "taxon", "taxon_uri", "start_year", "end_year", "uri", "thumbnail",
]


def _uuid(href: str) -> str:
    return href.rstrip("/").rsplit("/", 1)[-1]


def _text(values) -> str | None:
    """First human-readable value of an Open Context predicate list."""
    if not values:
        return None
    v = values[0]
    if isinstance(v, dict):
        for key in ("@en", "label", "@value"):
            if v.get(key) not in (None, ""):
                return str(v[key]).strip()
        return None
    return str(v).strip()


def _number(values) -> float | None:
    t = _text(values)
    try:
        return float(t) if t is not None else None
    except ValueError:
        return None


def _obs(record: dict) -> dict:
    """Merge every observation of a record into one predicate -> values map."""
    merged: dict = {}
    for obs in record.get("oc-gen:has-obs", []):
        for key, values in obs.items():
            if key.startswith("oc-pred:") and values:
                merged.setdefault(key.split(":", 1)[1], values)
    return merged


def _licensed(record: dict) -> bool:
    return any(lic.get("id", "").rstrip("/") == LICENSE for lic in record.get("dc-terms:license", []))


def normalise(site: str, prefix: str, raw: Path, out: Path) -> dict[str, int]:
    """``prefix`` is the project's predicate prefix in Open Context, e.g. ``22``."""
    out.mkdir(parents=True, exist_ok=True)
    loci = sorted(p for p in (raw / "loci").glob("*.json") if not p.name.startswith("_"))
    contexts, statements, path_to_uid = [], [], {}
    skipped = 0
    for p in loci:
        rec = json.loads(p.read_text())
        if not _licensed(rec):
            skipped += 1
            continue
        uid = rec["uuid"]
        chain = [c["label"] for c in rec.get("oc-gen:has-contexts", [])]
        site_ix = next((i for i, c in enumerate(rec.get("oc-gen:has-contexts", []))
                        if c.get("type") == "oc-gen:cat-site"), None)
        within_site = chain[site_ix + 1:] if site_ix is not None else chain
        path = "/".join(within_site + [rec["label"]])
        path_to_uid["/".join(chain + [rec["label"]])] = uid
        o = _obs(rec)
        feature = (rec.get("features") or [{}])[0]
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        when = feature.get("when") or {}
        description = " ".join(filter(None, (
            _text(o.get(f"{prefix}-general-remarks-1")),
            _text(o.get(f"{prefix}-description-remarks")),
        ))) or None
        contexts.append({
            "uid": uid, "site": site, "label": rec["label"],
            "area": next((c for c in within_site if c.startswith("Area")), None),
            "trench": "/".join(within_site) or None, "path": path,
            "type": _text(o.get(f"{prefix}-designator")),
            "period_label": _text(o.get(f"{prefix}-tentative-date-1")),
            "start_year": when.get("start"), "end_year": when.get("stop"),
            "excavated_from": _text(o.get(f"{prefix}-start-date")),
            "excavated_to": _text(o.get(f"{prefix}-end-date")),
            "top_depth": _number(o.get(f"{prefix}-top-depth-center")),
            "bottom_depth": _number(o.get(f"{prefix}-bottom-depth-center")),
            "context_rating": _text(o.get(f"{prefix}-context-rating")),
            "recorded_by": _text(o.get("recorded-by")),
            "latitude": coords[1], "longitude": coords[0],
            "description": description,
            "uri": rec["id"], "citation": (rec.get("dc-terms:identifier") or [None])[0],
        })
        for suffix, verb in STRAT_PREDICATES.items():
            for target in o.get(f"{prefix}-{suffix}", []):
                if isinstance(target, dict) and target.get("id"):
                    statements.append({"subject": uid, "verb": verb,
                                       "object": _uuid(target["id"]), "source": rec["id"]})

    known = {c["uid"] for c in contexts}
    dangling = sum(1 for s in statements if s["object"] not in known)
    statements = [s for s in statements if s["object"] in known]

    # The category searches overlap (a sherd is also an Object), so finds are
    # merged by uid: the first non-empty value of each field wins.
    by_uid: dict[str, dict] = {}
    for f in sorted(raw.glob("finds-*.jsonl")):
        for line in f.open():
            item = json.loads(line)
            ctx_path = item.get("context label", "")
            parent, _, leaf = ctx_path.rpartition("/")
            bag = leaf if leaf.startswith("Finds Bag") else None
            locus = path_to_uid.get(parent if bag else ctx_path)
            if locus is None:
                continue
            row = {
                "uid": _uuid(item["href"]), "site": site, "label": item.get("label"),
                "category": item.get("item category"), "context": locus, "bag": bag,
                "material": "; ".join(item.get("Material", []) or item.get("Consists of", [])) or None,
                "type": "; ".join(item.get("Type", []) or item.get("Has type", [])) or None,
                "taxon": "; ".join(item.get("Has taxonomic identifier", [])) or None,
                "taxon_uri": "; ".join(item.get("Has taxonomic identifier [URI]", [])) or None,
                "start_year": item.get("early bce/ce"), "end_year": item.get("late bce/ce"),
                "uri": item["href"], "thumbnail": item.get("thumbnail"),
            }
            seen = by_uid.setdefault(row["uid"], row)
            for key, value in row.items():
                if seen.get(key) in (None, "") and value not in (None, ""):
                    seen[key] = value
    finds = list(by_uid.values())

    for name, fields, rows in (("contexts", CONTEXT_FIELDS, contexts),
                               ("statements", STATEMENT_FIELDS, statements),
                               ("finds", FIND_FIELDS, finds)):
        with (out / f"{name}.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    counts = {"contexts": len(contexts), "statements": len(statements), "finds": len(finds),
              "dangling_statements": dangling, "unlicensed_skipped": skipped}
    print(f"{site}: {counts}", file=sys.stderr)
    return counts


if __name__ == "__main__":  # python -m strata.importers.opencontext_normalise SITE PREFIX RAW OUT
    normalise(sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
