"""Assemble the Claude Desktop extension (an MCP Bundle, .mcpb) for Strata.

    uv run python desktop/build.py            # -> dist/strata-desktop/ (the bundle's contents)
    npx -y @anthropic-ai/mcpb pack dist/strata-desktop dist/strata.mcpb

The bundle is a uv-type server: Claude Desktop installs the dependencies listed
in its own small pyproject.toml and runs server.py. It carries the store as a
read-only seed; see strata/desktop.py for where edits go. The matrix importers
come along, with harris-mcp's format readers copied in (Apache-2.0, same
authors) so that no git checkout is needed on the user's machine; the Open
Context fetcher stays out.

The manifest's version comes from pyproject.toml, and its tool list from the
MCP server itself, so neither can drift from the code.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "strata-desktop"

# The modules the MCP server needs (paths under strata/); the Open Context fetcher is not shipped.
MODULES = ["__init__.py", "store.py", "schema.cypher", "queries.py", "checks.py", "edits.py",
           "mcp_server.py", "desktop.py", "convert.py", "web_export.py",
           "importers/__init__.py", "importers/load.py", "importers/matrices.py", "importers/verbs.py",
           "importers/harris.py", "importers/formats/*.py"]
# harris-mcp's format readers and what they import (not its server)
HARRIS = ["__init__.py", "model.py", "registry.py", "formats/*.py"]
# dependencies of the bundled code, beyond relate-db and fastmcp (pydantic comes with fastmcp)
EXTRA_DEPS = ["networkx>=3.0", "openpyxl>=3.1"]

SERVER_PY = '''"""Entry point Claude Desktop runs: uv run --directory <this folder> server.py"""
from strata.desktop import main

main()
'''


async def tool_list() -> list[dict]:
    sys.path.insert(0, str(ROOT))
    from fastmcp import Client
    from strata.mcp_server import mcp
    async with Client(mcp) as client:
        tools = await client.list_tools()
    return [{"name": t.name, "description": " ".join((t.description or "").split())} for t in tools]


def _copy(src: Path, dst: Path, patterns: list[str]) -> None:
    for pattern in patterns:
        files = sorted(src.glob(pattern))
        if not files:
            raise SystemExit(f"nothing matches {src / pattern}")
        for f in files:
            (dst / f.relative_to(src)).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst / f.relative_to(src))


def build() -> Path:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    deps = [d for d in project["dependencies"] if d.split()[0].split(">")[0].split("<")[0].split("=")[0]
            in {"relate-db", "fastmcp"}]
    if len(deps) != 2:
        raise SystemExit(f"expected relate-db and fastmcp among the dependencies, found {deps}")

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "data").mkdir(parents=True)
    _copy(ROOT / "strata", OUT / "strata", MODULES)
    import harris_mcp
    _copy(Path(harris_mcp.__file__).parent, OUT / "harris_mcp", HARRIS)
    deps += EXTRA_DEPS
    shutil.copy2(ROOT / "data" / "strata.rl", OUT / "data" / "strata.rl")
    for name in ["LICENSE", "NOTICE"]:
        shutil.copy2(ROOT / name, OUT / name)
    shutil.copy2(ROOT / "desktop" / "icon.png", OUT / "icon.png")
    (OUT / "server.py").write_text(SERVER_PY)
    (OUT / "pyproject.toml").write_text(
        "[project]\n"
        'name = "strata-desktop"\n'
        f'version = "{project["version"]}"\n'
        'description = "Strata MCP server, as bundled for Claude Desktop"\n'
        f'requires-python = "{project["requires-python"]}"\n'
        "dependencies = [\n" + "".join(f'    "{d}",\n' for d in deps) + "]\n"
    )
    (OUT / ".mcpbignore").write_text(".venv/\n__pycache__/\n*.pyc\n")
    (OUT / "README.md").write_text(
        "# Strata for Claude Desktop\n\n"
        "An archaeological excavation as one Relate graph file, served over MCP.\n"
        "Source, documentation and licences: https://github.com/openhistorymap/strata\n\n"
        "`data/strata.rl` is a read-only seed. On first run it is copied to\n"
        "`Documents/Strata/strata.rl`, which the server reads and writes; the\n"
        "'Strata store' setting points it at another file. Ask for `store_info`.\n")

    manifest = json.loads((ROOT / "desktop" / "manifest.json").read_text())
    manifest["version"] = project["version"]
    manifest["tools"] = asyncio.run(tool_list())
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return OUT


if __name__ == "__main__":
    out = build()
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"{out} ({size / 1e6:.1f} MB, {len(json.loads((out / 'manifest.json').read_text())['tools'])} tools)")
