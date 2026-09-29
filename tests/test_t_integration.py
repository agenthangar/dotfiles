"""The staged split must never let dots reclaim or orphan standalone t links."""

import os
from pathlib import Path
import subprocess

from test_install_migration import _seed_worktree, git, run_install


def checkout(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = home / "code" / "dotfiles"
    _seed_worktree(repo)
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.name", "T", cwd=repo)
    git("config", "user.email", "t@example.test", cwd=repo)
    git("add", ".", cwd=repo)
    git("commit", "-qm", "seed", cwd=repo)
    return home, repo


def standalone(home):
    repo = home / "code" / "t"
    (repo / "bin").mkdir(parents=True)
    (repo / ".t-install-version").write_text("1\n")
    (repo / "t.plugin.zsh").write_text("# plugin\n")
    for name in ("t", "claude-stamp-tmux", "cursor-beam"):
        p = repo / "bin" / name
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)
    p = repo / "install.sh"
    p.write_text('''#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$HOME/bin"
for name in t claude-stamp-tmux cursor-beam; do
    ln -sfn "$root/bin/$name" "$HOME/bin/$name"
done
printf '%s\\n' "$T_LOCAL_RC" "$T_PERMISSIONS_DIR" "$T_AUTO_TRUST" > "$root/adapter.txt"
''')
    p.chmod(0o755)
    return repo


def test_repeated_dotfiles_relink_keeps_standalone_ownership(tmp_path):
    home, repo = checkout(tmp_path)
    t = standalone(home)
    for _ in range(2):
        r = run_install(repo, home, DOTFILES_LINKS_ONLY="1")
        assert r.returncode == 0, r.stderr
        for name in ("t", "claude-stamp-tmux", "cursor-beam"):
            assert (home / "bin" / name).resolve() == t / "bin" / name
    assert (t / "adapter.txt").read_text().splitlines() == [
        str(home / ".zshrc.local"), str(repo / "agents"), "1"
    ]


def test_invalid_standalone_marker_keeps_bundled_t(tmp_path):
    home, repo = checkout(tmp_path)
    t = standalone(home)
    (t / ".t-install-version").write_text("99\n")
    r = run_install(repo, home, DOTFILES_LINKS_ONLY="1")
    assert r.returncode == 0, r.stderr
    assert (home / "bin" / "t").resolve() == repo / "bin" / "t"
    assert not (t / "adapter.txt").exists()


def preflight(home, repo):
    env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
    return subprocess.run(
        ["bash", "-c", 'source "$1/lib/t-integration.sh"; _dots_t_preflight "$1"', "_", str(repo)],
        env=env, capture_output=True, text=True,
    )


def removal_ref(repo):
    git("checkout", "-qb", "removal", cwd=repo)
    git("rm", "bin/t", cwd=repo)
    git("commit", "-qm", "remove bundled t", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    git("checkout", "-q", "main", cwd=repo)


def test_removal_preflight_stops_before_old_executable_disappears(tmp_path):
    home, repo = checkout(tmp_path)
    removal_ref(repo)
    r = preflight(home, repo)
    assert r.returncode == 1
    assert "must be installed" in r.stderr
    assert (repo / "bin" / "t").exists()
    assert git("branch", "--show-current", cwd=repo).stdout.strip() == "main"


def test_removal_preflight_relinks_standalone_before_fast_forward(tmp_path):
    home, repo = checkout(tmp_path)
    t = standalone(home)
    removal_ref(repo)
    r = preflight(home, repo)
    assert r.returncode == 0, r.stderr
    git("merge", "--ff-only", "origin/main", cwd=repo)
    assert not (repo / "bin" / "t").exists()
    assert (home / "bin" / "t").resolve() == t / "bin" / "t"
    assert (home / "bin" / "t").exists()
