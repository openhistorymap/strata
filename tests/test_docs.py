"""The documentation page names every tool, command and issue kind there is."""

import asyncio
import re
from pathlib import Path

from strata.checks import SEVERITY

ROOT = Path(__file__).resolve().parent.parent
DOCS = (ROOT / "web" / "docs" / "index.html").read_text()


def test_every_mcp_tool_is_documented():
    from fastmcp import Client
    from strata.mcp_server import mcp

    async def names():
        async with Client(mcp) as c:
            return [t.name for t in await c.list_tools()]

    missing = [n for n in asyncio.run(names()) if f"<code>{n}</code>" not in DOCS]
    assert not missing, f"MCP tools missing from web/docs/index.html: {missing}"


def test_every_webmcp_tool_is_documented():
    names = re.findall(r'name: "(strata_[a-z_]+)"', (ROOT / "web" / "webmcp.js").read_text())
    assert len(names) >= 9
    assert not [n for n in names if f"<code>{n}</code>" not in DOCS]


def test_every_cli_command_and_question_is_documented():
    cli = (ROOT / "strata" / "cli.py").read_text()
    commands = re.findall(r'sub\.add_parser\("([a-z-]+)"', cli)
    questions = re.findall(r'q == "([a-z]+)"', cli)
    assert commands and questions
    assert not [c for c in commands if f"<code>{c}" not in DOCS]
    assert not [q for q in questions if f"<code>{q}</code>" not in DOCS]


def test_every_issue_kind_is_documented():
    assert not [k for k in SEVERITY if f"<code>{k}</code>" not in DOCS]


def test_every_matrix_format_is_documented():
    from strata.importers.matrices import formats
    assert not [f.name for f in formats() if f"<code>{f.name}</code>" not in DOCS]


def test_harris_tools_offered_by_the_viewer_exist_and_only_read():
    """web/harris.js offers some harris-mcp tools to the model: they must exist, and must not edit."""
    from fastmcp import Client
    from harris_mcp.server import mcp

    js = (ROOT / "web" / "harris.js").read_text()
    offered = re.findall(r'"([a-z_]+)"', js[js.index("const READ_ONLY"):js.index("];", js.index("const READ_ONLY"))])

    async def tools():
        async with Client(mcp) as c:
            return {t.name: t for t in await c.list_tools()}

    have = asyncio.run(tools())
    assert len(offered) >= 20
    assert not [n for n in offered if n not in have], "harris-mcp has no such tool"
    edits = {"add_context", "update_context", "delete_context", "add_relation", "remove_relation", "assign_phase",
             "mark_contemporary", "attach_note", "revert", "save_matrix", "link_ohm_feature", "assert_correspondence"}
    assert not edits & set(offered)
    assert "harris_open" in js and "<code>harris_open</code>" in DOCS
