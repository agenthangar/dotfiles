"""Sandboxed dotfiles install fixtures shared by retained shell integration tests."""

import os
import pathlib
import subprocess
import sys

import pytest

from test_install_migration import _seed_worktree, git

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


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
        "DOTFILES_NO_T": "1",
        "DOTFILES_NO_MCP": "1",
        # no `codex` reachable: the gate must decide on ~/.codex alone
        "PATH": ":".join([os.path.dirname(sys.executable), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
    }
    env.update(extra)
    return subprocess.run(["./install.sh"], cwd=str(co), env=env, capture_output=True, text=True)
