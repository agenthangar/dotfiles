"""Retire the former sync entry points without reading or deleting user data."""

import os
from pathlib import Path
import plistlib
import subprocess

import pytest

from install_helpers import box, relink  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.example.csync"


def arm(home, **overrides):
    data = {
        "Label": LABEL,
        "ProgramArguments": [str(home / "bin/csync")],
        "StartInterval": 900,
        "RunAtLoad": True,
        "StandardOutPath": str(home / "Library/Logs/csync.log"),
        "StandardErrorPath": str(home / "Library/Logs/csync.log"),
    }
    data.update(overrides)
    plist = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(data))
    return plist


def test_fresh_install_exposes_no_sync_command(box):
    co, home = box
    result = relink(co, home)
    assert result.returncode == 0, result.stderr
    assert not (home / "bin/csync").is_symlink()
    assert not (ROOT / "bin/csync").exists()
    assert not (home / "Library/LaunchAgents" / f"{LABEL}.plist").exists()
    assert "csync" not in result.stdout


@pytest.mark.parametrize("target", ["current", "legacy", "old-source", "homebrew-arm", "homebrew-intel"])
def test_links_only_retires_owned_dangling_links_idempotently(box, target):
    co, home = box
    targets = {
        "current": co / "bin/csync",
        "legacy": home / ".local/share/dotfiles-main/bin/csync",
        "old-source": home / "code/dotfiles/bin/csync",
        "homebrew-arm": Path("/opt/homebrew/opt/dotfiles/libexec/bin/csync"),
        "homebrew-intel": Path("/usr/local/opt/dotfiles/libexec/bin/csync"),
    }
    link = home / "bin/csync"
    link.parent.mkdir()
    link.symlink_to(targets[target])
    plist = arm(home)
    # Preserve content and metadata in every former sync tree, including state
    # outside those trees. No final sync or cloud materialization is allowed.
    paths = [
        ".claude/projects/project/session.jsonl", ".claude/plans/plan.md",
        ".codex/sessions/session.jsonl", ".cursor/chats/chat/store.db",
        "Library/Mobile Documents/com~apple~CloudDocs/claude-sessions/session.jsonl",
        "Library/Mobile Documents/com~apple~CloudDocs/claude-plans/plan.md",
        "Library/Mobile Documents/com~apple~CloudDocs/codex-sessions/session.jsonl",
        "Library/Mobile Documents/com~apple~CloudDocs/cursor-chats/chat/store.db",
        "Library/Logs/csync.log", ".cache/csync-last-run",
    ]
    before = {}
    for name in paths:
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic retained data\n")
        before[path] = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
    for _ in range(2):
        result = relink(co, home)
        assert result.returncode == 0, result.stderr
        assert not link.is_symlink()
        assert not plist.exists()
        for path, expected in before.items():
            assert (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino) == expected


@pytest.mark.parametrize("kind", ["regular", "custom-link", "custom-plist", "malformed-plist", "plist-link"])
def test_preserves_custom_entries(box, kind):
    co, home = box
    command = home / "bin/csync"
    command.parent.mkdir()
    if kind == "regular":
        command.write_text("custom utility\n")
    elif kind == "custom-link":
        command.symlink_to(home / "custom/bin/csync")
    elif kind == "custom-plist":
        arm(home, ProgramArguments=["/custom/csync"])
    elif kind == "malformed-plist":
        plist = arm(home)
        plist.write_text("not a plist\n")
    else:
        plist = arm(home)
        contents = plist.read_bytes()
        plist.unlink()
        target = home / "custom.plist"
        target.write_bytes(contents)
        plist.symlink_to(target)
    result = relink(co, home)
    assert result.returncode == 0, result.stderr
    if kind == "regular":
        assert command.read_text() == "custom utility\n"
    elif kind == "custom-link":
        assert command.is_symlink()
        assert os.readlink(command) == str(home / "custom/bin/csync")
    else:
        assert (home / "Library/LaunchAgents" / f"{LABEL}.plist").exists()


def run_retirement(home, *, real_home, bootout_status=0, loaded=False):
    """Exercise the real retirement function with fake launchctl and account home.

    Shell functions prevent any operation on the developer's actual GUI domain.
    Full-installer tests above cover wiring into links-only updates.
    """
    source = (ROOT / "install.sh").read_text()
    function = source[source.index("retire_csync() {"):source.index("\nretire_csync\n")]
    script = r'''
set -euo pipefail
LINK_SRC=/nonexistent/dotfiles
DOTFILES_DIR=$LINK_SRC
PRIMARY=$LINK_SRC
LEGACY_MAIN_WT=$HOME/.local/share/dotfiles-main
uname() { echo Darwin; }
id() { if [[ $1 == -un ]]; then echo synthetic; else echo 501; fi; }
eval() { printf '%s\n' "$FAKE_REAL_HOME"; }
launchctl() {
    printf '%s\n' "$*" >> "$HOME/launchctl.log"
    if [[ $1 == bootout ]]; then return "$BOOTOUT_STATUS"; fi
    [[ $LOADED == 1 ]]
}
'''
    return subprocess.run(
        ["bash", "-c", script + function + "\nretire_csync\n"],
        env={**os.environ, "HOME": str(home), "FAKE_REAL_HOME": str(real_home),
             "BOOTOUT_STATUS": str(bootout_status), "LOADED": str(int(loaded))},
        text=True, capture_output=True,
    )


def test_sandbox_home_never_calls_launchctl(tmp_path):
    home = tmp_path / "sandbox"
    plist = arm(home)
    result = run_retirement(home, real_home=tmp_path / "real-account")
    assert result.returncode == 0, result.stderr
    assert not plist.exists()
    assert not (home / "launchctl.log").exists()


@pytest.mark.parametrize("bootout_status", [0, 3])
def test_real_account_boots_out_only_retired_label(tmp_path, bootout_status):
    home = tmp_path / "home"
    plist = arm(home)
    result = run_retirement(home, real_home=home, bootout_status=bootout_status)
    assert result.returncode == 0, result.stderr
    assert not plist.exists()
    lines = (home / "launchctl.log").read_text().splitlines()
    assert lines[0] == f"bootout gui/501/{LABEL}"
    assert all(line.endswith(f"gui/501/{LABEL}") for line in lines)


def test_failed_unload_preserves_plist_for_retry(tmp_path):
    home = tmp_path / "home"
    plist = arm(home)
    result = run_retirement(home, real_home=home, bootout_status=1, loaded=True)
    assert result.returncode == 1
    assert plist.exists()
    assert "Could not retire" in result.stderr
