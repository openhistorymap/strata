"""Turn an existing Harris matrix, in whatever format it was kept, into Strata.

:func:`import_matrix` adds it to a store as a site, and runs every check on
it; :func:`convert` does that in a fresh store and writes the result as a
viewer bundle (and, if asked, as a Relate file of its own).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from .checks import run_checks
from .importers.load import import_site
from .importers.matrices import read, site_id, to_source
from .store import Store


class SiteExists(Exception):
    pass


def import_matrix(store: Store, path: str | Path, *, site: str | None = None, name: str | None = None,
                  fmt: str = "auto", reverse: bool = False, replace: bool = False,
                  meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read a matrix file and load it into ``store`` as one site, then run the checks.

    ``reverse`` is for drawings (DOT, GraphML) whose arrows run from the
    earlier unit to the later one. An existing site with the same id is
    only replaced when ``replace`` is set, since that drops its edits too.
    """
    path = Path(path).expanduser()
    sid = site_id(site or path.stem.removesuffix(".strata"))
    exists = store.run("MATCH (s:Site {id: $id}) RETURN count(s) AS n", id=sid)[0]["n"]
    if exists and not replace:
        raise SiteExists(f"the store already has a site '{sid}'; choose another site id, "
                         "or replace it (which also drops its edit history)")
    m, f = read(path, fmt, reverse=reverse)
    info = {"id": sid, "name": name or path.stem, "source": f"{path.name} (read as {f.name})",
            **{k: v for k, v in (meta or {}).items() if v}}
    loaded = import_site(store, to_source(m, info, source_file=path.name, fmt=f.name))
    checks = run_checks(store, sid)
    return {"site": sid, "format": f.name, "read": m.summary(), "loaded": loaded, "checks": checks}


def convert(path: str | Path, out: str | Path | None = None, *, store_out: str | Path | None = None,
            **options: Any) -> dict[str, Any]:
    """Convert one matrix file into a viewer bundle (``<name>.strata.json``)."""
    from .web_export import export_bundle

    path = Path(path).expanduser()
    out = Path(out) if out else Path.cwd() / f"{path.stem}.strata.json"
    with tempfile.TemporaryDirectory(prefix="strata-") as tmp:
        db = Path(tmp) / "convert.rl"
        with Store(db) as store:
            store.apply_schema()
            result = import_matrix(store, path, **options)
        with Store(db, read_only=True) as store:
            result["bundle"] = export_bundle(store, result["site"], out)
        if store_out:
            shutil.copyfile(db, store_out)
            result["store"] = str(store_out)
    return result
