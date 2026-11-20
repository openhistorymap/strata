"""Fetch excavation records from Open Context (https://opencontext.org).

Stage 1, ``fetch``: page through the search API for every item of one
category in a project, then download each item's JSON-LD record into
``data/raw/<site>/<uuid>.json``. Records already on disk are skipped, so an
interrupted fetch resumes where it stopped.

Stage 2, ``normalise`` (in ``opencontext_normalise``): turn the cached
records into the CSV files under ``data/sources/<site>/`` that the importer
loads into Relate.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

BASE = "https://opencontext.org"
# A plain client name: Open Context answers browser user agents with a bot
# challenge, and plain clients with JSON.
HEADERS = {"User-Agent": "strata-importer/0.1"}


def list_items(project: str, category: str, *, rows: int = 1000) -> list[str]:
    """Return the record URLs of every item of ``category`` in ``project``."""
    hrefs: list[str] = []
    start = 0
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
        while True:
            r = client.get(
                f"{BASE}/query/.json",
                params={"proj": project, "cat": category, "rows": rows,
                        "start": start, "response": "uri-meta"},
            )
            r.raise_for_status()
            page = r.json()
            results = page if isinstance(page, list) else page.get("oc-api:has-results", [])
            if not results:
                break
            hrefs.extend(item["href"] for item in results)
            start += len(results)
            if len(results) < rows:
                break
    return hrefs


def _fetch_one(client: httpx.Client, href: str, out_dir: Path) -> str:
    uuid = href.rstrip("/").rsplit("/", 1)[-1]
    path = out_dir / f"{uuid}.json"
    if path.exists():
        return "cached"
    for attempt in range(5):
        try:
            r = client.get(f"{href}.json")
            if r.status_code == 200:
                tmp = path.with_suffix(".part")
                tmp.write_bytes(r.content)
                tmp.rename(path)
                return "fetched"
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            return f"http {r.status_code}"
        except httpx.HTTPError:
            time.sleep(2 ** attempt)
    return "failed"


def fetch(project: str, category: str, out_dir: Path, *, workers: int = 4) -> dict[str, int]:
    """Download every record of ``category`` in ``project`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    hrefs = list_items(project, category)
    (out_dir / f"_index-{category.replace(':', '_')}.json").write_text(json.dumps(hrefs))
    print(f"{project}: {len(hrefs)} records of {category}", file=sys.stderr)
    counts: dict[str, int] = {}
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client, \
            ThreadPoolExecutor(max_workers=workers) as pool:
        for i, status in enumerate(pool.map(lambda h: _fetch_one(client, h, out_dir), hrefs), 1):
            counts[status] = counts.get(status, 0) + 1
            if i % 250 == 0:
                print(f"  {i}/{len(hrefs)} {counts}", file=sys.stderr)
    print(f"  done {counts}", file=sys.stderr)
    return counts


def fetch_query(project: str, category: str, out_file: Path, *,
                attributes: tuple[str, ...] = (), rows: int = 1000) -> int:
    """Save the search-API metadata of every item of ``category`` as JSON lines.

    One request per page of ``rows`` items, instead of one per item: enough
    for finds, whose search metadata already carries the locus path, the
    date range and the requested ``attributes``.
    """
    out_file.parent.mkdir(parents=True, exist_ok=True)
    if out_file.exists():
        return sum(1 for _ in out_file.open())
    n = 0
    start = 0
    params = {"proj": project, "cat": category, "rows": rows, "response": "uri-meta"}
    if attributes:
        params["attributes"] = ",".join(attributes)
    tmp = out_file.with_suffix(".part")
    with httpx.Client(headers=HEADERS, timeout=120, follow_redirects=True) as client, tmp.open("w") as out:
        while True:
            for attempt in range(5):
                r = client.get(f"{BASE}/query/.json", params={**params, "start": start})
                if r.status_code == 200:
                    break
                time.sleep(2 ** attempt)
            r.raise_for_status()
            page = r.json()
            results = page if isinstance(page, list) else page.get("oc-api:has-results", [])
            for item in results:
                out.write(json.dumps(item) + "\n")
            n += len(results)
            start += len(results)
            print(f"  {category}: {n}", file=sys.stderr)
            if len(results) < rows:
                break
    tmp.rename(out_file)
    return n


if __name__ == "__main__":  # python -m strata.importers.opencontext PROJECT CATEGORY OUT_DIR
    fetch(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
