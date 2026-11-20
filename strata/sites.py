"""Where each site's data comes from."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "data" / "sources"
RAW = ROOT / "data" / "raw"

# Open Context projects: search slug, predicate prefix, and which finds to pull.
OPEN_CONTEXT = {
    "kenan-tepe": {
        "project": "22-kenan-tepe",
        "prefix": "22",
        "units": "oc-gen:cat-locus",
        "finds": {
            "oc-gen:cat-object": ("22-material-3", "22-type", "cidoc-crm-p45-consists-of", "cidoc-crm-p2-has-type"),
            "oc-gen:cat-animal-bone": ("obo-foodon-00001303", "22-element"),
            "oc-gen:cat-pottery": ("22-type", "cidoc-crm-p2-has-type", "cidoc-crm-p45-consists-of"),
            "oc-gen:cat-plant-remains": ("obo-foodon-00001303",),
        },
    },
}


def source_sites() -> list[str]:
    """Sites with a normalised source directory, in name order."""
    return sorted(p.name for p in SOURCES.iterdir() if (p / "site.json").exists()) if SOURCES.exists() else []
