"""End-to-end tests for bin/dots-sync (keep every host on origin/main) and the
.zshrc half that reloads an open shell when the live `main` moves under it.

Real git throughout — a bare origin, a canonical clone that ~/.zshrc links into,
a sandbox $HOME — and a stub ssh (DOTS_SYNC_SSH) that records the fan-out instead
of reaching a host.
"""

import os
import pathlib
import subprocess
import sys

import pytest

from test_install_migration import _seed_worktree, git

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOTS_SYNC = REPO_ROOT / "bin" / "dots-sync"


@pytest.fixture
def world(tmp_path):
    """(origin-seed clone, canonical checkout, home, ssh log)."""
    seed = tmp_path / "seed"
    _seed_worktree(seed)
    git("init", "-q", "-b", "main", cwd=seed)
    git("config", "user.email", "t@t.t", cwd=seed)
    git("config", "user.name", "T", cwd=seed)
    git("add", "-A", cwd=seed)
    git("commit", "-qm", "seed", "--no-verify", cwd=seed)
    origin = tmp_path / "origin.git"
    git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)

    home = tmp_path / "home"
    home.mkdir()
    co = home / "code" / "dotfiles"
    git("clone", "-q", str(origin), str(co), cwd=tmp_path)
    (home / ".zshrc").symlink_to(co / ".zshrc")

    cfg = home / ".config" / "t"
    cfg.mkdir(parents=True)
    (cfg / "config.sh").write_text(
        "DEV_REPOS[dot]=/x\nREMOTE_HOSTS[mini]=me@mini\nREMOTE_HOSTS[claw]=claw.ts\n"
    )
    log = tmp_path / "ssh.log"
    stub = tmp_path / "ssh"
    stub.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        'case "$*" in *claw.ts*) exit 255 ;; esac\n'
        "echo 'login noise'\n"
        "echo '✓ updated live main a → b, reloaded'\n"
    )
    stub.chmod(0o755)
    return seed, co, home, log, stub


def run(home, stub, *args):
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "DOTS_SYNC_SSH": str(stub),
        "DOTFILES_NO_TMUX": "1",
        "DOTFILES_NO_MCP": "1",
        "DOTFILES_NO_PERMISSIONS": "1",
        "DOTFILES_NO_TRUST": "1",
        "DOTFILES_NO_CODEX_HOOKS": "1",
    }
    return subprocess.run([str(DOTS_SYNC), *args], env=env, capture_output=True, text=True)


def land(seed, name="new.txt"):
    """A PR merging on GitHub: a new commit on origin/main."""
    (seed / name).write_text("x\n")
    git("add", "-A", cwd=seed)
    git("commit", "-qm", f"add {name}", "--no-verify", cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    return git("rev-parse", "HEAD", cwd=seed).stdout.strip()


def head(co):
    return git("rev-parse", "HEAD", cwd=co).stdout.strip()


def test_nothing_new_is_silent_and_fans_out_nowhere(world):
    seed, co, home, log, stub = world
    r = run(home, stub)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "" and r.stderr == ""
    assert not log.exists()


def test_a_merge_fast_forwards_and_reaches_every_host(world):
    seed, co, home, log, stub = world
    tip = land(seed)
    r = run(home, stub)
    assert r.returncode == 0, r.stderr
    assert head(co) == tip
    assert f"→ {tip[:7]}" in r.stdout
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert all(c.endswith("zsh -lic dots") and "BatchMode=yes" in c for c in calls)
    # the host's own summary line, not its login noise; the dead host said so
    assert "mini: ✓ updated live main" in r.stdout
    assert "claw: unreachable" in r.stdout
    # and the next tick is quiet again — no second fan-out
    log.unlink()
    r = run(home, stub)
    assert r.stdout == "" and not log.exists()


def test_a_dirty_live_tree_is_never_touched_and_says_why(world):
    seed, co, home, log, stub = world
    before = head(co)
    (co / ".zshrc").write_text("# hand edit on the live surface\n")
    land(seed)
    r = run(home, stub)
    assert head(co) == before
    assert (co / ".zshrc").read_text() == "# hand edit on the live surface\n"
    assert "has local edits" in r.stderr
    assert not log.exists(), "nothing moved here, so nothing fans out"
    r = run(home, stub)
    assert "has local edits" in r.stderr, "an asked-for run always says why it declined"


def test_off_main_or_dev_linked_is_left_to_a_human(world):
    seed, co, home, log, stub = world
    git("checkout", "-qb", "side", cwd=co)
    land(seed)
    r = run(home, stub)
    assert "not on main" in r.stderr
    git("checkout", "-q", "main", cwd=co)
    # dots --dev: links into a linked worktree (a .git FILE) → hands off
    wt = home / "code" / ".worktrees" / "dotfiles" / "1"
    git("worktree", "add", "-q", "-b", "dev/dotfiles-1", str(wt), cwd=co)
    (home / ".zshrc").unlink()
    (home / ".zshrc").symlink_to(wt / ".zshrc")
    before = head(co)
    r = run(home, stub)
    assert "not the canonical checkout" in r.stderr
    assert head(co) == before


def test_hosts_only_fans_out_without_syncing(world):
    seed, co, home, log, stub = world
    before = head(co)
    land(seed)
    r = run(home, stub, "--hosts-only")
    assert r.returncode == 0
    assert head(co) == before
    assert len(log.read_text().splitlines()) == 2


def test_no_hosts_syncs_only_here(world):
    seed, co, home, log, stub = world
    tip = land(seed)
    run(home, stub, "--no-hosts")
    assert head(co) == tip
    assert not log.exists()


def test_bad_arg(world):
    seed, co, home, log, stub = world
    assert run(home, stub, "--bogus").returncode == 2


# ── the open-shell half: .zshrc reloads itself when the live main moves ──────────


@pytest.mark.skipif(not subprocess.run(["which", "zsh"], capture_output=True).stdout, reason="no zsh")
def test_zsh_open_shell_reloads_when_live_main_moves(world, tmp_path):
    seed, co, home, log, stub = world
    # the real .zshrc must be what ~/.zshrc resolves to, inside the canonical checkout
    (co / ".zshrc").write_bytes((REPO_ROOT / ".zshrc").read_bytes())
    script = tmp_path / "probe.zsh"
    script.write_text(
        "source ~/.zshrc >/dev/null 2>&1\n"
        "_dots_reload_if_moved; print -r -- quiet=$?\n"
        f"git -C {co} -c user.email=t@t.t -c user.name=T commit -q --allow-empty -m bump --no-verify\n"
        "RELOADED=0; _t_sync_config() { :; }\n"
        "_dots_reload_if_moved 2>&1\n"
        "_dots_reload_if_moved 2>&1; print -r -- again\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("TMUX")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), ZDOTDIR=str(home))
    r = subprocess.run(["zsh", "-f", str(script)], env=env, capture_output=True, text=True, timeout=60)
    out = r.stdout
    assert "quiet=0" in out
    assert out.count("reloaded ~/.zshrc") == 1, out
    assert out.rstrip().endswith("again")
