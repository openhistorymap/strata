"""Strata in Claude Desktop (and any MCP host that launches it as a bundle).

The .mcpb bundle carries a read-only seed of the store (data/strata.rl). The
server never writes to it: edits go to a store of your own, so they survive
updates of the extension and you can see, copy and back up the file.

Which file is used, in order:

1. ``STRATA_DB`` (the extension's "Strata store" setting, or the environment)
2. ``~/Documents/Strata/strata.rl``, created from the bundled seed on first run

``store_info`` (an MCP tool) always reports the path in use.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SEED = Path(__file__).resolve().parent.parent / "data" / "strata.rl"
PERSONAL = Path.home() / "Documents" / "Strata" / "strata.rl"


def _configured(value: str | None) -> Path | None:
    # An unset optional setting can arrive empty or as the literal placeholder.
    if not value or not value.strip() or value.strip().startswith("${"):
        return None
    return Path(value.strip()).expanduser()


def resolve_store(configured: str | None = None, seed: Path = SEED, personal: Path = PERSONAL) -> tuple[Path, str]:
    """Return (path, how) where how says why this file was chosen."""
    chosen = _configured(configured)
    if chosen is not None:
        if not chosen.exists():
            raise SystemExit(f"Strata store not found: {chosen} (check the extension's 'Strata store' setting)")
        return chosen, "configured"
    if personal.exists():
        return personal, "personal copy"
    if not seed.exists():
        raise SystemExit(f"No Strata store: {personal} does not exist and the bundled seed {seed} is missing")
    personal.parent.mkdir(parents=True, exist_ok=True)
    tmp = personal.with_name(personal.name + ".copying")
    shutil.copyfile(seed, tmp)
    tmp.replace(personal)
    print(f"strata: created {personal} from the bundled Kenan Tepe store", file=sys.stderr)
    return personal, "created from the bundled seed"


def main() -> None:
    from .mcp_server import serve
    path, how = resolve_store(os.environ.get("STRATA_DB"))
    print(f"strata: serving {path} ({how})", file=sys.stderr)
    serve(str(path), how=how)


if __name__ == "__main__":
    main()
