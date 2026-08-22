"""End-to-end tests for install.sh's two-tree -> one-tree migration.

This is the one piece of shell in the repo where a bug costs you your shell: the
migration repoints every managed $HOME symlink and removes the worktree those links
used to resolve through. So it is tested against a real git repo in a throwaway
$HOME rather than by reading the code.

Everything runs under tmp_path with HOME overridden, so no test touches the real
machine. Coverage is scoped to bin/t and bin/pr-watch in pyproject.toml, so these
subprocess-driven tests do not dilute the coverage ratchet.
"""

import os
import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"

# Everything install.sh links, plus the files it reads. Stand-ins are enough: the
# migration cares about paths and git state, never file contents.
STUB_BINS = [
    "sleep-manager", "csync", "cursor-beam", "pii-scan",
    "claude-stamp-tmux", "t", "pr-watch",
]


def git(*args, cwd, check=True, **kw):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=check,
        capture_output=True, text=True, **kw
    )


def _seed_worktree(path):
    """Populate `path` with a minimal but complete dotfiles checkout."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "bin").mkdir(exist_ok=True)
    (path / "claude" / "commands").mkdir(parents=True, exist_ok=True)
    (path / "ssh").mkdir(exist_ok=True)
    (path / "launchd").mkdir(exist_ok=True)
    (path / ".githooks").mkdir(exist_ok=True)

    (path / "install.sh").write_bytes(INSTALL_SH.read_bytes())
    (path / "install.sh").chmod(0o755)
    (path / ".zshrc").write_text("# zshrc\n")
    (path / ".tmux.conf").write_text("# tmux\n")
    (path / ".zshrc.local.example").write_text("# local\n")
    for b in STUB_BINS:
        f = path / "bin" / b
        f.write_text("#!/bin/sh\n")
        f.chmod(0o755)
    for c in ("tpush.md", "tpop.md", "todo.md"):
        (path / "claude" / "commands" / c).write_text("x\n")
    (path / "claude" / "settings.json.example").write_text("{}\n")
    (path / "ssh" / "dotfiles.conf").write_text("# ssh\n")
    # install.sh seds this into ~/Library/LaunchAgents; content is irrelevant.
    (path / "launchd" / "com.chrisobrien-ai.pr-watch.plist").write_text(
        "<plist>__DOTFILES_BIN__</plist>\n"
    )


@pytest.fixture
def legacy(tmp_path):
    """A machine on the OLD two-tree layout, with $HOME linked at the main worktree.

    Returns (home, primary, legacy_wt). `primary` is parked on dev/claude-1 exactly
    as the real dev clone was; `legacy_wt` holds `main` and is what $HOME points at.
    """
    home = tmp_path / "home"
    home.mkdir()
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", "--bare", cwd=origin)

    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    git("config", "user.email", "t@t.t", cwd=seed)
    git("config", "user.name", "T", cwd=seed)
    _seed_worktree(seed)
    git("add", "-A", cwd=seed)
    git("commit", "-qm", "initial", "--no-verify", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)

    primary = tmp_path / "primary"
    git("clone", "-q", str(origin), str(primary), cwd=tmp_path)
    git("config", "user.email", "t@t.t", cwd=primary)
    git("config", "user.name", "T", cwd=primary)
    git("checkout", "-q", "-b", "dev/claude-1", cwd=primary)

    legacy_wt = home / ".local" / "share" / "dotfiles-main"
    legacy_wt.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "-q", str(legacy_wt), "main", cwd=primary)

    # Point $HOME at the legacy worktree, as the old install.sh did.
    (home / "bin").mkdir(exist_ok=True)
    (home / ".zshrc").symlink_to(legacy_wt / ".zshrc")
    (home / "bin" / "t").symlink_to(legacy_wt / "bin" / "t")

    return home, primary, legacy_wt


def run_install(cwd, home, **extra_env):
    env = {
        **os.environ,
        "HOME": str(home),
        "DOTFILES_NO_BREW": "1",
        # Keep the test off the real launchd/tmux/brew surfaces.
        "PR_WATCH_NO_BOOTSTRAP": "1",
    }
    env.update(extra_env)
    return subprocess.run(
        ["./install.sh"], cwd=str(cwd), env=env,
        capture_output=True, text=True,
    )


def test_migration_flips_links_and_moves_primary_onto_main(legacy):
    home, primary, legacy_wt = legacy
    run_install(legacy_wt, home)

    # The links now resolve into the primary, and nothing dangles.
    for link in (home / ".zshrc", home / "bin" / "t"):
        assert link.is_symlink()
        assert link.exists(), f"{link} dangles after migration"
        assert str(link.resolve()).startswith(str(primary.resolve()))

    # The primary took `main`.
    head = git("symbolic-ref", "--short", "HEAD", cwd=primary).stdout.strip()
    assert head == "main"


def test_migration_removes_the_legacy_worktree(legacy):
    home, primary, legacy_wt = legacy
    # Two runs: the first can die partway on a sandbox-specific step, and the
    # cleanup is deliberately the last action of a *complete* install.
    run_install(legacy_wt, home)
    run_install(primary, home)

    assert not legacy_wt.exists()
    listing = git("worktree", "list", "--porcelain", cwd=primary).stdout
    assert "dotfiles-main" not in listing


def test_dirty_legacy_tree_is_salvaged_not_discarded(legacy):
    home, primary, legacy_wt = legacy
    # Reproduce the real machine's state: a STAGED edit plus an untracked file.
    (legacy_wt / ".zshrc").write_text("# zshrc\n# LOCAL EDIT\n")
    (legacy_wt / "stray.txt").write_text("untracked-content\n")
    git("add", ".zshrc", cwd=legacy_wt)

    out = run_install(legacy_wt, home).stdout
    assert "Salvaged" in out

    patches = list((home / ".local" / "share").glob("dotfiles-main.salvage-*.patch"))
    assert patches, "no salvage patch written"
    assert "# LOCAL EDIT" in patches[0].read_text()

    kept = patches[0].parent / (patches[0].name + ".untracked") / "stray.txt"
    assert kept.read_text() == "untracked-content\n"


def test_migration_is_idempotent(legacy):
    home, primary, legacy_wt = legacy
    run_install(legacy_wt, home)
    run_install(primary, home)
    out = run_install(primary, home).stdout

    # A settled machine neither migrates nor relinks.
    assert "Salvaged" not in out
    assert "Removed legacy main worktree" not in out
    assert "Linked " not in out
    assert git("symbolic-ref", "--short", "HEAD", cwd=primary).stdout.strip() == "main"


def test_partial_run_leaves_home_healthy_and_resumes(legacy):
    """The ordering guarantee: dying mid-install must never dangle a link.

    Force a failure in install_pr_watch — past link_all, before cleanup — by parking
    a regular FILE where it wants to mkdir a directory. Injecting via the checkout
    would not work: `git checkout main` restores anything deleted from the tree.
    """
    home, primary, legacy_wt = legacy
    (home / "Library").mkdir(parents=True, exist_ok=True)
    (home / "Library" / "LaunchAgents").write_text("not a directory\n")

    res = run_install(legacy_wt, home)
    assert res.returncode != 0, "failure injection did not fire"

    for link in (home / ".zshrc", home / "bin" / "t"):
        assert link.exists(), f"{link} dangles after a partial run"
    # The legacy tree survives, but DETACHED — the ref was released, not the files.
    assert legacy_wt.exists()
    assert git("symbolic-ref", "-q", "HEAD", cwd=legacy_wt, check=False).returncode != 0

    # Clear the blocker, then resume. Rule (2) of the enumeration is what makes this
    # work: the legacy tree is now DETACHED, so the "holds refs/heads/main" scan can
    # no longer see it and only the literal-path check finds it.
    (home / "Library" / "LaunchAgents").unlink()
    run_install(primary, home)
    assert not legacy_wt.exists()


def test_capability_probe_refuses_before_the_change_is_on_main(tmp_path):
    """A pre-merge install.sh must be a printed no-op, not a half-migration.

    Without the probe, running a feature branch's install.sh would migrate, then
    link from a primary on a `main` that lacks the new code — silently rolling the
    machine back, with the next `dots` rebuilding the worktree just removed.
    """
    home = tmp_path / "home"
    home.mkdir()
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", "--bare", cwd=origin)

    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    git("config", "user.email", "t@t.t", cwd=seed)
    git("config", "user.name", "T", cwd=seed)
    _seed_worktree(seed)
    # origin/main carries an OLD installer with no migration support.
    (seed / "install.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\necho 'OLD installer'\n"
    )
    git("add", "-A", cwd=seed)
    git("commit", "-qm", "old", "--no-verify", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)

    primary = tmp_path / "primary"
    git("clone", "-q", str(origin), str(primary), cwd=tmp_path)
    git("config", "user.email", "t@t.t", cwd=primary)
    git("config", "user.name", "T", cwd=primary)
    git("checkout", "-q", "-b", "dev/claude-1", cwd=primary)
    legacy_wt = home / ".local" / "share" / "dotfiles-main"
    legacy_wt.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "-q", str(legacy_wt), "main", cwd=primary)
    # The NEW installer exists only on the branch, as during development.
    _seed_worktree(primary)
    git("add", "-A", cwd=primary)
    git("commit", "-qm", "new installer", "--no-verify", cwd=primary)

    res = run_install(primary, home)

    assert "LEFT IN PLACE" in res.stdout
    assert res.returncode == 0, "a deliberate refusal must not look like a crash"
    # Nothing was touched.
    assert git("symbolic-ref", "--short", "HEAD", cwd=primary).stdout.strip() == "dev/claude-1"
    assert legacy_wt.exists()
