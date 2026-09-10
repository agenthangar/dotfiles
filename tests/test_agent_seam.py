"""End-to-end tests for the shell side of the agent tooling, driven the way
test_install_migration.py drives install.sh: real scripts under a sandbox $HOME with
stub `ps` / `tmux` binaries on PATH that answer from fixtures and log their argv.

Today this covers bin/claude-stamp-tmux, the SessionStart hook every "which slot is
running what" answer depends on (registry, opened stamp, origin stamp, tmux stamp).
The zsh `_dev_agent_*` seam lands here next. Coverage is scoped to bin/t and
bin/pr-watch in pyproject.toml, so these subprocess tests do not move the ratchet.
"""

import json
import os
import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "bin" / "claude-stamp-tmux"

PS_STUB = r"""#!/bin/bash
# ps -o comm= -p <pid> | ps -o ppid= -p <pid>, answered from $FAKE_PS ("pid ppid comm")
pid="${@: -1}"
while read -r p pp c; do
  if [[ "$p" == "$pid" ]]; then
    case "$2" in comm=) echo "$c" ;; ppid=) echo "$pp" ;; esac
    exit 0
  fi
done < "$FAKE_PS"
exit 1
"""

TMUX_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$TMUX_LOG"
"""


@pytest.fixture
def sandbox(tmp_path):
    """(home, env) — a fake HOME + XDG cache, stub ps/tmux first on PATH, and a fake
    process table in which the test process itself is the `claude` ancestor the hook
    walks up to (its $PPID is our pid when run without a shell)."""
    home = tmp_path / "home"
    home.mkdir()
    bins = tmp_path / "stubbin"
    bins.mkdir()
    for name, body in (("ps", PS_STUB), ("tmux", TMUX_STUB)):
        f = bins / name
        f.write_text(body)
        f.chmod(0o755)
    table = tmp_path / "ps.txt"
    table.write_text(f"{os.getpid()} 1 claude\n")
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "PATH": f"{bins}:{os.environ.get('PATH', '')}",
        "FAKE_PS": str(table),
        "TMUX_LOG": str(tmp_path / "tmux.log"),
    }
    env.pop("TMUX", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return home, env


def run_hook(env, payload, **extra):
    env = {**env, **extra}
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([str(HOOK)], input=stdin, env=env, capture_output=True, text=True)


def test_hook_writes_registry_opened_and_origin_stamps(sandbox):
    home, env = sandbox
    cwd = home / "code" / "proj"
    proj = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    proj.mkdir(parents=True)
    r = run_hook(env, {"session_id": "abc-123", "cwd": str(cwd)})
    assert r.returncode == 0, r.stderr
    reg = home / ".cache" / "claude-sessions"
    assert (reg / str(os.getpid())).read_text() == f"abc-123\t{cwd}\n"
    assert (reg / "opened" / "abc-123").exists()
    origin = (proj / "abc-123.origin").read_text().strip()
    assert origin and origin != "abc-123"
    # no $TMUX → no tmux stamp attempted
    assert not (pathlib.Path(env["TMUX_LOG"])).exists()


def test_hook_stamps_tmux_when_inside_a_pane(sandbox):
    home, env = sandbox
    r = run_hook(env, {"session_id": "sid-9", "cwd": str(home)}, TMUX="/tmp/tmux-1/default,1,0")
    assert r.returncode == 0
    assert pathlib.Path(env["TMUX_LOG"]).read_text().splitlines() == ["set-environment CLAUDE_RESUME_ID sid-9"]


def test_hook_falls_back_to_the_session_env_var(sandbox):
    home, env = sandbox
    r = run_hook(env, "not json at all", CLAUDE_CODE_SESSION_ID="env-sid")
    assert r.returncode == 0
    assert (home / ".cache" / "claude-sessions" / "opened" / "env-sid").exists()


def test_hook_never_fails_without_an_id(sandbox):
    home, env = sandbox
    r = run_hook(env, "{}")
    assert r.returncode == 0
    assert not (home / ".cache").exists()


def test_hook_skips_the_registry_when_no_agent_ancestor(sandbox, tmp_path):
    home, env = sandbox
    # the process table knows only a shell above us: no registry entry, stamps still land
    (tmp_path / "ps.txt").write_text(f"{os.getpid()} 1 zsh\n1 0 launchd\n")
    r = run_hook(env, {"session_id": "s1", "cwd": str(home)})
    assert r.returncode == 0
    reg = home / ".cache" / "claude-sessions"
    assert not (reg / str(os.getpid())).exists()
    assert (reg / "opened" / "s1").exists()
