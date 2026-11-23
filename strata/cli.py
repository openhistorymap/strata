"""strata: build, check and question the excavation graph.

    strata build                      rebuild data/strata.rl from data/sources/* and run every check
    strata ask overview               one line per site
    strata ask issues --site kenan-tepe
    strata ask describe "Area D/Trench 6/Locus 20"
    strata ask relation "Area F/Trench 7/Locus 7042" "Area F/Trench 7/Locus 7044"
    strata query "MATCH (c:Context) RETURN c.type, count(*)"     read-only Cypher
    strata mcp                        serve the store to an agent over MCP (stdio)
    strata export-web                 dump the store to web/data/ for the browser viewer
    strata convert matrix.lst         any Harris matrix -> matrix.strata.json for the viewer
    strata formats                    the matrix formats Strata reads
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import queries as Q
from .checks import run_checks
from .sites import OPEN_CONTEXT, RAW, SOURCES, source_sites
from .store import DEFAULT_DB, Store


def _print(result) -> None:
    if isinstance(result, list) and result and all(isinstance(r, dict) for r in result) \
            and all(not isinstance(v, (dict, list)) or len(json.dumps(v)) < 60 for r in result for v in r.values()):
        cols = list(result[0].keys())
        cells = [[json.dumps(r.get(c)) if isinstance(r.get(c), (dict, list)) else
                  ("" if r.get(c) is None else str(r.get(c))) for c in cols] for r in result]
        widths = [min(max(len(c), *(len(row[i]) for row in cells)), 90) for i, c in enumerate(cols)]
        print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
        print("  ".join("-" * w for w in widths))
        for row in cells:
            print("  ".join(v[:w].ljust(w) for v, w in zip(row, widths)))
        return
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_fetch(args) -> None:
    from .importers.opencontext import fetch, fetch_query
    from .importers.opencontext_normalise import normalise
    cfg = OPEN_CONTEXT[args.site]
    raw = RAW / args.site
    fetch(cfg["project"], cfg["units"], raw / "loci")
    for cat, attrs in cfg["finds"].items():
        fetch_query(cfg["project"], cat, raw / f"finds-{cat.split('cat-')[-1]}.jsonl", attributes=attrs)
    normalise(args.site, cfg["prefix"], raw, SOURCES / args.site)


def cmd_build(args) -> None:
    from .importers.load import import_site
    db = Path(args.db)
    tmp = db.with_name(db.stem + ".building.rl")  # the .rl suffix keeps Relate in single-file mode
    if tmp.exists():
        tmp.unlink()
    t0 = time.time()
    with Store(tmp) as store:
        store.apply_schema()
        for site in args.sites or source_sites():
            t = time.time()
            counts = import_site(store, SOURCES / site)
            summary = run_checks(store, site)
            print(f"{site}: {counts}\n  checks: {summary}  ({time.time() - t:.1f}s)", file=sys.stderr)
    tmp.replace(db)
    print(f"built {db} in {time.time() - t0:.1f}s", file=sys.stderr)
    if db.resolve() == DEFAULT_DB:
        from .web_export import export_web
        with Store(db, read_only=True) as store:
            out = export_web(store, DEFAULT_DB.parent.parent / "web" / "data")
        print(f"refreshed the viewer data in web/data/ ({', '.join(out)})", file=sys.stderr)


def _matrix_options(args) -> dict:
    meta = {"license": args.license, "license_label": args.license_label, "source": args.source,
            "source_uri": args.source_uri}
    return {"site": args.site, "name": args.name, "fmt": args.format, "reverse": args.reverse, "meta": meta}


def cmd_import_matrix(args) -> None:
    from .convert import SiteExists, import_matrix
    with Store(args.db) as store:
        store.apply_schema()
        try:
            _print(import_matrix(store, args.path, replace=args.replace, **_matrix_options(args)))
        except (SiteExists, ValueError, FileNotFoundError, RuntimeError) as e:
            raise SystemExit(str(e))


def cmd_convert(args) -> None:
    from .convert import convert
    try:
        _print(convert(args.path, args.out, store_out=args.store, **_matrix_options(args)))
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        raise SystemExit(str(e))


def cmd_formats(args) -> None:
    from .importers.matrices import formats
    _print([{"format": f.name, "reads": f.label, "extensions": " ".join(f.extensions), "via": f.via}
            for f in formats()])


def cmd_check(args) -> None:
    with Store(args.db) as store:
        for site in args.sites or [r["site"] for r in Q.overview(store)]:
            _print({site: run_checks(store, site)})


def cmd_ask(args) -> None:
    with Store(args.db, read_only=True) as store:
        q, a, site = args.question, args.args, args.site
        try:
            if q == "overview":
                result = Q.overview(store)
            elif q == "issues":
                result = Q.issues(store, site, args.kind, args.limit)
            elif q == "describe":
                result = Q.describe(store, a[0], site)
            elif q == "below":
                result = Q.below(store, a[0], site, args.limit)
            elif q == "above":
                result = Q.above(store, a[0], site, args.limit)
            elif q == "relation":
                result = Q.relation(store, a[0], a[1], site)
            elif q == "bracket":
                result = Q.date_bracket(store, a[0], site)
            elif q == "provenance":
                result = Q.provenance(store, a[0], site)
            elif q == "periods":
                result = Q.period_sequence(store, site or a[0])
            else:
                raise SystemExit(f"unknown question {q!r}")
        except Q.NotFound as e:
            raise SystemExit(str(e))
        except IndexError:
            raise SystemExit(f"'{q}' needs more arguments; see strata ask --help")
    _print(result)


def cmd_query(args) -> None:
    with Store(args.db, read_only=True) as store:
        try:
            out = store.read(args.cypher, json.loads(args.params) if args.params else None, limit=args.limit)
        except PermissionError as e:
            raise SystemExit(str(e))
    _print(out["rows"])
    if out["truncated"]:
        print(f"... truncated at {args.limit} rows", file=sys.stderr)


def cmd_stats(args) -> None:
    with Store(args.db, read_only=True) as store:
        _print(store.stats())


def cmd_export_web(args) -> None:
    from .web_export import export_bundle, export_web
    with Store(args.db, read_only=True) as store:
        if args.bundle:
            if not args.site:
                raise SystemExit("--bundle needs --site")
            _print(export_bundle(store, args.site, Path(args.bundle)))
        else:
            _print(export_web(store, Path(args.out)))


def cmd_mcp(args) -> None:
    from .mcp_server import serve
    how = "--db" if "--db" in sys.argv else "$STRATA_DB" if os.environ.get("STRATA_DB") else "repository default"
    serve(args.db, how=how)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="strata", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=os.environ.get("STRATA_DB") or str(DEFAULT_DB),
                   help="Relate store file (default: $STRATA_DB, else data/strata.rl)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch", help="download a site from Open Context and normalise it into data/sources/")
    s.add_argument("site", choices=sorted(OPEN_CONTEXT))
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("build", help="rebuild the store from data/sources/ and run all checks")
    s.add_argument("sites", nargs="*", help="sites to include (default: all)")
    s.set_defaults(func=cmd_build)

    def matrix_args(s, site_required: bool) -> None:
        s.add_argument("path", help="the matrix file (or folder, for paired CSVs and hm data packages)")
        s.add_argument("--site", required=site_required,
                       help="site id, e.g. ulm-eggingen" + ("" if site_required else " (default: from the file name)"))
        s.add_argument("--name", help="site name (default: the file name)")
        s.add_argument("--format", default="auto", help="see 'strata formats' (default: detect)")
        s.add_argument("--reverse", action="store_true",
                       help="DOT/GraphML: arrows run from the earlier unit to the later one")
        s.add_argument("--license")
        s.add_argument("--license-label")
        s.add_argument("--source", help="where the matrix comes from (default: the format and file name)")
        s.add_argument("--source-uri")

    s = sub.add_parser("import-matrix", help="add a Harris matrix from any supported format to the store")
    matrix_args(s, site_required=True)
    s.add_argument("--replace", action="store_true",
                   help="replace a site with the same id (drops its edit history)")
    s.set_defaults(func=cmd_import_matrix)

    s = sub.add_parser("convert", help="convert a Harris matrix into a viewer bundle (.strata.json), checked")
    matrix_args(s, site_required=False)
    s.add_argument("-o", "--out", help="bundle to write (default: <file name>.strata.json here)")
    s.add_argument("--store", metavar="FILE.rl", help="also keep the converted Relate store")
    s.set_defaults(func=cmd_convert)

    s = sub.add_parser("formats", help="list the Harris-matrix formats Strata reads")
    s.set_defaults(func=cmd_formats)

    s = sub.add_parser("check", help="recompute matrix depth, contested edges and issues")
    s.add_argument("sites", nargs="*")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("ask", help="named questions: overview, issues, describe, below, above, relation, bracket, provenance, periods")
    s.add_argument("question")
    s.add_argument("args", nargs="*")
    s.add_argument("--site")
    s.add_argument("--kind", help="issue kind, for 'issues'")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("query", help="run read-only Cypher (always rolled back)")
    s.add_argument("cypher")
    s.add_argument("--params", help="JSON object of parameters")
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(func=cmd_query)

    s = sub.add_parser("stats", help="node and relationship counts")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("export-web", help="dump the store as JSON for the browser viewer (web/)")
    s.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "web" / "data"),
                   help="folder for <site>/{core,finds}.json and datasets.json (default: web/data)")
    s.add_argument("--site", help="with --bundle: the site to export")
    s.add_argument("--bundle", metavar="FILE", help="write one site as a single file the viewer can open "
                   "from disk or a URL, e.g. my-dig.strata.json")
    s.set_defaults(func=cmd_export_web)

    s = sub.add_parser("mcp", help="serve the store over MCP (stdio)")
    s.set_defaults(func=cmd_mcp)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
