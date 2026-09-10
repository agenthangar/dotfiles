"""End-to-end tests for install.sh's install_codex_hooks (the ~/.codex/hooks.json
seed), run the way test_install_migration.py runs install.sh: a real git checkout
and a throwaway $HOME. The seed sits in the links-only path so a plain `dots` lands
it, which is exactly why it must be add-only, idempotent, and silent when nothing
changes — this pins all three. PATH is narrowed so the developer's real `codex`
(if any) cannot flip the gate.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

from test_install_migration import _seed_worktree, git

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK_CMD = "$HOME/bin/claude-stamp-tmux --agent codex"


@pytest.fixture
def box(tmp_path):
    """(checkout, home): a seeded single-tree checkout on main + an empty HOME."""
    co = tmp_path / "dotfiles"
    git("init", "-q", "-b", "main", cwd=tmp_path, check=False)  # no-op guard for old git
    co.mkdir()
    git("init", "-q", "-b", "main", cwd=co)
    git("config", "user.email", "t@t.t", cwd=co)
    git("config", "user.name", "T", cwd=co)
    _seed_worktree(co)
    git("add", "-A", cwd=co)
    git("commit", "-qm", "seed", "--no-verify", cwd=co)
    home = tmp_path / "home"
    home.mkdir()
    return co, home


def relink(co, home, **extra):
    env = {
        **os.environ,
        "HOME": str(home),
        "DOTFILES_LINKS_ONLY": "1",
        "DOTFILES_NO_TMUX": "1",
        "DOTFILES_NO_MCP": "1",
        # no `codex` reachable: the gate must decide on ~/.codex alone
        "PATH": ":".join([os.path.dirname(sys.executable), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
    }
    env.update(extra)
    return subprocess.run(["./install.sh"], cwd=str(co), env=env, capture_output=True, text=True)


def hooks_of(home):
    return json.loads((home / ".codex" / "hooks.json").read_text())


def test_seeds_hooks_json_when_codex_dir_exists(box):
    co, home = box
    (home / ".codex").mkdir()
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    assert "Registered the SessionStart hook" in r.stdout
    data = hooks_of(home)
    assert data == {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": HOOK_CMD}]}]}}


def test_no_codex_dir_no_file(box):
    co, home = box
    assert relink(co, home).returncode == 0
    assert not (home / ".codex").exists()


def test_second_run_is_a_silent_noop(box):
    co, home = box
    (home / ".codex").mkdir()
    relink(co, home)
    before = (home / ".codex" / "hooks.json").read_text()
    r = relink(co, home)
    assert r.returncode == 0
    assert "SessionStart" not in r.stdout
    assert (home / ".codex" / "hooks.json").read_text() == before


def test_merges_into_an_existing_file_without_touching_other_hooks(box):
    co, home = box
    (home / ".codex").mkdir()
    existing = {"description": "mine", "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
        "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "python3 notes.py"}]}],
    }}
    (home / ".codex" / "hooks.json").write_text(json.dumps(existing))
    assert relink(co, home).returncode == 0
    data = hooks_of(home)
    assert data["description"] == "mine"
    assert data["hooks"]["Stop"] == existing["hooks"]["Stop"]
    assert data["hooks"]["SessionStart"][0] == existing["hooks"]["SessionStart"][0]
    assert data["hooks"]["SessionStart"][1] == {"hooks": [{"type": "command", "command": HOOK_CMD}]}


def test_existing_stamp_entry_is_left_alone(box):
    co, home = box
    (home / ".codex").mkdir()
    mine = {"hooks": {"SessionStart": [{"hooks": [{"type": "command",
                                                    "command": "/opt/bin/claude-stamp-tmux --agent codex --quiet"}]}]}}
    (home / ".codex" / "hooks.json").write_text(json.dumps(mine))
    r = relink(co, home)
    assert r.returncode == 0 and "SessionStart" not in r.stdout
    assert hooks_of(home) == mine


def test_invalid_json_is_never_repaired(box):
    co, home = box
    (home / ".codex").mkdir()
    (home / ".codex" / "hooks.json").write_text("{ this is not json")
    r = relink(co, home)
    assert r.returncode == 0
    assert (home / ".codex" / "hooks.json").read_text() == "{ this is not json"


def test_opt_out_env(box):
    co, home = box
    (home / ".codex").mkdir()
    assert relink(co, home, DOTFILES_NO_CODEX_HOOKS="1").returncode == 0
    assert not (home / ".codex" / "hooks.json").exists()
