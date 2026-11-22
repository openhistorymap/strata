"""Which store the desktop extension uses, and that it never writes to its seed."""

import pytest

from strata.desktop import resolve_store


def test_first_run_copies_the_seed(tmp_path):
    seed = tmp_path / "bundle" / "strata.rl"
    seed.parent.mkdir()
    seed.write_bytes(b"seed")
    personal = tmp_path / "home" / "Documents" / "Strata" / "strata.rl"

    path, how = resolve_store(None, seed, personal)
    assert path == personal and how == "created from the bundled seed"
    assert personal.read_bytes() == b"seed"

    personal.write_bytes(b"edited")                      # edits survive a restart
    path, how = resolve_store("", seed, personal)
    assert (path, how) == (personal, "personal copy") and personal.read_bytes() == b"edited"
    assert seed.read_bytes() == b"seed"


def test_unset_setting_placeholder_is_ignored(tmp_path):
    seed = tmp_path / "s.rl"
    seed.write_bytes(b"x")
    path, how = resolve_store("${user_config.store}", seed, tmp_path / "p.rl")
    assert path == tmp_path / "p.rl"


def test_configured_store_wins_and_must_exist(tmp_path):
    mine = tmp_path / "mine.rl"
    mine.write_bytes(b"m")
    assert resolve_store(str(mine), tmp_path / "missing", tmp_path / "p.rl") == (mine, "configured")
    with pytest.raises(SystemExit):
        resolve_store(str(tmp_path / "nope.rl"), tmp_path / "missing", tmp_path / "p.rl")
