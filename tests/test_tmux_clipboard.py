"""End-to-end tests for .tmux.conf's system-clipboard mirroring.

This block exists because copying silently failed in some Claude sessions: Claude
Code latches its copy strategy at process start, and a slot born while an ssh
client was attached copies into the *tmux* buffer forever — invisible to Cmd+V.
The fix mirrors the buffer to the system clipboard instead of fighting that.

Every failure mode here is a SILENT no-op, which is why this is tested end-to-end
against a real tmux server rather than by reading the config:

  * the `if-shell` guard not applying (block skipped, nothing set, no error)
  * a copy binding attached to the key table `mode-keys` did not select
  * the hook not firing for the command Claude Code actually runs

A fake `pbcopy` is put FIRST on PATH, so the tests both work on Linux CI (where
there is no pbcopy) and never touch the developer's real clipboard on macOS.

Everything runs on a throwaway `tmux -L` socket under tmp_path. Coverage is scoped
to bin/t and bin/pr-watch in pyproject.toml, so these do not dilute the ratchet.
"""

import os
import pathlib
import shutil
import subprocess
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TMUX_CONF = REPO_ROOT / ".tmux.conf"

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux is not installed"
)


class Server:
    """A throwaway tmux server loaded from the repo's real .tmux.conf."""

    def __init__(self, socket, env, clip_file):
        self.socket = socket
        self.env = env
        self.clip_file = clip_file

    def tmux(self, *args, check=True):
        return subprocess.run(
            ["tmux", "-L", self.socket, *args],
            env=self.env, capture_output=True, text=True, check=check,
        )

    def option(self, name):
        return self.tmux("show-options", "-gv", name, check=False).stdout.strip()

    def hook(self, name):
        """The command bound to a hook, or '' if unset.

        `show-hooks -g` lists every hook NAME whether or not it is set, appending
        `[0] <command>` only when it is — so a substring check for the name alone
        can never fail. Return just the command.
        """
        out = self.tmux("show-hooks", "-g", name, check=False).stdout.strip()
        return out[len(name):].strip() if out.startswith(name) else out

    def clipboard(self, timeout=5.0):
        """Wait for the fake pbcopy to receive something; '' if it never does."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.clip_file.exists():
                text = self.clip_file.read_text()
                if text:
                    return text
            time.sleep(0.05)
        return ""


def _make_server(tmp_path, with_pbcopy=True):
    """Build a tmux server whose PATH does (or does not) contain a pbcopy."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    clip_file = tmp_path / "clipboard.txt"

    if with_pbcopy:
        fake = bindir / "pbcopy"
        fake.write_text(f'#!/bin/sh\ncat > "{clip_file}"\n')
        fake.chmod(0o755)
    else:
        # A PATH with tmux and /bin but no pbcopy (which lives in /usr/bin on
        # macOS), so the guard evaluates false on a machine that really has one.
        (bindir / "tmux").symlink_to(shutil.which("tmux"))

    # The fake dir goes FIRST so it shadows a real pbcopy on macOS, which keeps
    # the tests off the developer's actual clipboard.
    inherited = os.environ.get("PATH", "/usr/bin:/bin")
    path = f"{bindir}:{inherited}" if with_pbcopy else f"{bindir}:/bin"
    env = {"PATH": path, "HOME": str(tmp_path), "TERM": "xterm-256color"}

    socket = f"clip-{tmp_path.name}-{'y' if with_pbcopy else 'n'}"
    server = Server(socket, env, clip_file)
    server.tmux("kill-server", check=False)
    server.tmux("-f", str(TMUX_CONF), "new-session", "-d", "-s", "t", "-x", "80", "-y", "24")
    return server


@pytest.fixture
def server(tmp_path):
    s = _make_server(tmp_path, with_pbcopy=True)
    yield s
    s.tmux("kill-server", check=False)


def test_config_applies_clipboard_mirroring_when_a_clipboard_tool_exists(server):
    """The if-shell guard must actually apply the block — skipping is silent."""
    assert server.option("copy-command") == "pbcopy"

    assert "pbcopy" in server.hook("after-set-buffer")
    assert "pbcopy" in server.hook("after-load-buffer")


def test_claude_codes_copy_reaches_the_system_clipboard(server):
    """The reported bug: Claude Code runs `tmux load-buffer -w -`, which lands in
    the tmux buffer only. The hook must mirror it to the clipboard."""
    subprocess.run(
        ["tmux", "-L", server.socket, "load-buffer", "-w", "-"],
        env=server.env, input="CLAUDE-COPY-PAYLOAD", text=True, check=True,
    )
    assert server.clipboard() == "CLAUDE-COPY-PAYLOAD"


def test_set_buffer_also_reaches_the_system_clipboard(server):
    """tmux's own right-click Copy Line / Copy Word menu uses set-buffer."""
    server.tmux("set-buffer", "MENU-COPY-PAYLOAD")
    assert server.clipboard() == "MENU-COPY-PAYLOAD"


@pytest.mark.parametrize(
    "mode_keys,copy_key", [("emacs", "M-w"), ("vi", "Enter")]
)
def test_copy_mode_selection_reaches_the_clipboard_in_either_key_table(
    server, mode_keys, copy_key
):
    """copy-mode sets its buffer internally and does NOT fire the hooks, so this
    path is carried by `copy-command` instead.

    Parametrised over both key tables on purpose: this server is `mode-keys emacs`,
    so the usual `bind -T copy-mode-vi` advice would have been a silent no-op. The
    copy key is driven through the real key table, not via `send-keys -X`.
    """
    server.tmux("set-option", "-g", "mode-keys", mode_keys)
    server.tmux("send-keys", "-t", "t", "clear; echo SELECTION-PAYLOAD", "Enter")
    _wait_for_pane_text(server, "SELECTION-PAYLOAD")

    server.tmux("copy-mode", "-t", "t")
    server.tmux("send-keys", "-t", "t", "-X", "cursor-up")
    server.tmux("send-keys", "-t", "t", "-X", "cursor-up")
    server.tmux("send-keys", "-t", "t", "-X", "select-line")
    server.tmux("send-keys", "-t", "t", copy_key)

    assert "SELECTION-PAYLOAD" in server.clipboard()


def test_guard_skips_the_block_without_a_clipboard_tool(tmp_path):
    """Linux nodes (openclaw) have no pbcopy: the block must skip cleanly rather
    than error or set a copy-command that would fail on every copy."""
    s = _make_server(tmp_path, with_pbcopy=False)
    try:
        assert s.option("copy-command") == ""
        assert s.hook("after-set-buffer") == ""
        assert s.hook("after-load-buffer") == ""
    finally:
        s.tmux("kill-server", check=False)


def _wait_for_pane_text(server, needle, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = server.tmux("capture-pane", "-p", "-t", "t", check=False).stdout
        if needle in out:
            return
        time.sleep(0.05)
    pytest.fail(f"pane never showed {needle!r}")
