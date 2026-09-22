"""End-to-end tests for install.sh's retire_pr_watch + the pr-watch link removal.

pr-watch was an autonomous PR fixer on a launchd StartInterval timer, so its
retirement is the one that MUST land by itself: the plist is a real file install.sh
wrote into ~/Library/LaunchAgents, and deleting bin/pr-watch without booting it out
would leave launchd firing a vanished command every 5 minutes on exactly the machines
that were opted in. Both halves therefore sit in the links-only path, which a plain
`dots` runs — and that is what these pin, against a real checkout and a throwaway
$HOME, the way test_install_migration.py runs install.sh.

The `box` + `relink` fixtures are shared with the codex-hooks suite (same seeded
checkout, same links-only invocation). Note the bootout reaches the REAL user domain
if a plist is present in the sandbox HOME — launchctl has no notion of $HOME — but it
names only our own retired label and tolerates its absence, so it is a no-op here.
"""

import pathlib

import pytest

from test_install_codex_hooks import box, relink  # noqa: F401  (pytest fixtures)

LABEL = "com.chrisobrien-ai.pr-watch"


def _arm(home):
    """A machine as pr-watch left it: the materialized plist, and the PATH symlink —
    dangling, since bin/pr-watch is gone from the checkout."""
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    plist = agents / f"{LABEL}.plist"
    plist.write_text("<plist><!-- StartInterval 300 --></plist>\n")
    (home / "bin").mkdir(exist_ok=True)
    link = home / "bin" / "pr-watch"
    link.symlink_to("/nonexistent/dotfiles/bin/pr-watch")
    return plist, link


def test_retires_the_launchagent_and_the_path_symlink(box):
    co, home = box
    plist, link = _arm(home)
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    assert not plist.exists(), "the timer's plist must be gone, not just the script"
    # islink, not exists: a dangling symlink reports exists() == False either way
    assert not link.is_symlink(), "a dangling ~/bin/pr-watch on $PATH is an exec error"
    assert "Removed retired link: ~/bin/pr-watch" in r.stdout
    assert "Retired the pr-watch LaunchAgent" in r.stdout
    # and it never comes back: the link call is gone, so a second run re-adds nothing
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    assert not (home / "bin" / "pr-watch").is_symlink()
    assert not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()


def test_says_nothing_on_a_machine_that_never_had_it(box):
    """Silent unless it changed something — it runs on every single `dots`."""
    co, home = box
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    assert "pr-watch" not in r.stdout, r.stdout
    assert not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
    # the other managed bins still land; only pr-watch stopped being linked
    assert (home / "bin" / "t").is_symlink()
    assert not (home / "bin" / "pr-watch").is_symlink()


def test_nothing_in_the_repo_can_re_arm_it():
    """The script and the plist it ran from are deleted — a reintroduction should have
    to notice this test, since the danger was never the code but the TIMER."""
    root = pathlib.Path(__file__).resolve().parent.parent
    assert not (root / "bin" / "pr-watch").exists()
    # launchd/ itself is back (the clip-bridge listener), so pin the TIMER's plist
    # by name and pin that nothing tracked there runs on an interval.
    assert not list(root.glob("launchd/*pr-watch*"))
    for plist in root.glob("launchd/*.plist"):
        assert "StartInterval" not in plist.read_text(), plist
