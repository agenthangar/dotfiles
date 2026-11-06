"""Dotfiles shell regression tests: nosleep and zsh startup."""

import json
import os
import pathlib
import pty
import re
import select
import shlex
import shutil
import subprocess
import sys
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


ZSHRC = REPO_ROOT / ".zshrc"


# compinit's one-keystroke question when a dir on $fpath is group-writable. It is asked
# ONLY on a tty (without one `read -q` hits EOF and compinit aborts, silently under the
# `source … >/dev/null 2>&1`) — which is why the non-pty tests never met it and the pty
# ones hung on it: GitHub's ubuntu runner ships such a dir. Answered `y`, what a person
# types; the sandbox never uses completion.
COMPINIT_PROMPT = b"Ignore insecure directories and continue [y] or abort compinit [n]? "


def _run_under_pty(argv, env, timeout=60):
    """Run argv under a pseudo-terminal — for the branches gated on `-t 0 && -t 1` (the
    fzf pickers). stdout + stderr come back merged as .stdout; EOF once the child closes
    its side (an empty read, or EIO on Linux). A compinit prompt is answered `y` once."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(argv, env=env, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    out, deadline, answered = bytearray(), time.monotonic() + timeout, False
    while True:
        ready, _, _ = select.select([master], [], [], max(0.0, deadline - time.monotonic()))
        if not ready:
            proc.kill()
            break
        try:
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
        if not answered and out.rstrip().endswith(COMPINIT_PROMPT.rstrip()):
            os.write(master, b"y")
            answered = True
    os.close(master)
    return subprocess.CompletedProcess(argv, proc.wait(timeout=10), out.decode(errors="replace"), "")


def test_run_under_pty_answers_the_compinit_prompt():
    """The harness's own environment guard, driven against a real `read -q` prompt so it
    is exercised on every OS, not only where a group-writable fpath dir happens to exist."""
    prompt = COMPINIT_PROMPT.decode()
    r = _run_under_pty(["zsh", "-c", f'if read -q "?{prompt}"; then echo continued; else echo aborted; fi; echo rc=$?'],
                       {**os.environ, "TERM": "dumb"}, timeout=15)
    assert "continued" in r.stdout and "aborted" not in r.stdout and "rc=0" in r.stdout, r.stdout
    # and a command that never asks is untouched
    r = _run_under_pty(["zsh", "-c", "echo plain; echo rc=$?"], {**os.environ, "TERM": "dumb"}, timeout=15)
    assert "plain" in r.stdout and "rc=0" in r.stdout


@pytest.fixture
def zsh(tmp_path):
    """zsh_call(snippet, **env) → CompletedProcess of `source .zshrc; <snippet>` under a
    sandbox HOME with a local config and stub tools on PATH."""
    if not shutil.which("zsh"):
        pytest.skip("zsh is not installed")
    home = tmp_path / "home"
    (home / ".cache").mkdir(parents=True)
    (home / ".zshrc.local").write_text(
        'DEV_REPOS[api]="$HOME/code/api"\nDEV_REPOS[web]="$HOME/code/web"\n'
        'DEV_AGENT[api]=codex\n')
    bins = tmp_path / "stubbin"
    bins.mkdir()
    (tmp_path / "ps.txt").write_text("1 0 launchd\n")

    def call(snippet, _tty=False, **extra):
        env = {"HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"),
               "PATH": f"{bins}:{os.environ.get('PATH', '')}", "TERM": "dumb",
               "FAKE_PS": str(tmp_path / "ps.txt"), **extra}
        argv = ["zsh", "-c", f"source {ZSHRC} >/dev/null 2>&1; {snippet}"]
        if not _tty:
            return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
        return _run_under_pty(argv, env)

    call.home = home
    return call


def test_dotfiles_without_standalone_t_keeps_local_config_and_explains_recovery(zsh):
    result = zsh('print -r -- "repo=$DEV_REPOS[api]"; t ls; print -r -- "rc=$?"')
    assert "repo=" in result.stdout and "/code/api" in result.stdout
    assert "rc=127" in result.stdout
    assert "standalone shell integration is missing" in result.stderr


_ZSH_TIED_SPECIALS = {"path", "fpath", "cdpath", "manpath", "mailpath", "module_path", "prompt"}
_ZSH_DECL_RE = re.compile(
    r"^\s*(?:local|typeset|declare|integer|float|readonly)\b"
    r"((?:\s+-[A-Za-z]+)*)"                              # flags
    r"((?:\s+[A-Za-z_][A-Za-z0-9_]*(?:=\S*)?)*)")        # name[=value] …


def test_zshrc_never_declares_a_tied_special_as_a_plain_local():
    bad = []
    for n, line in enumerate(ZSHRC.read_text().splitlines(), 1):
        m = _ZSH_DECL_RE.match(line)
        if not m or "g" in m.group(1) or "h" in m.group(1):   # typeset -g / -h are deliberate
            continue
        names = {tok.split("=", 1)[0] for tok in m.group(2).split()}
        bad += [f".zshrc:{n}: local {name}" for name in sorted(names & _ZSH_TIED_SPECIALS)]
    assert not bad, ("a plain local shadows a tied zsh special parameter (blanks $PATH & co. "
                     "for the rest of the function):\n" + "\n".join(bad))


def test_reload_removes_retired_sync_hook_and_function(zsh):
    result = zsh('''
        csync() { print unexpected-sync; }
        _csync_periodic() { csync; }
        _keep_prompt_hook() { :; }
        precmd_functions+=(_csync_periodic _keep_prompt_hook)
        source "$DOTFILES_TEST_ZSHRC" >/dev/null 2>&1
        print -r -- "sync=${+functions[csync]} hook=${+functions[_csync_periodic]}"
        print -r -- "hooks=${(j:,:)precmd_functions}"
        print -r -- "workspace=${+functions[_clawsync_periodic]}"
    ''', DOTFILES_TEST_ZSHRC=str(ZSHRC))
    assert result.returncode == 0, result.stderr
    assert "sync=0 hook=0" in result.stdout
    assert "_csync_periodic" not in result.stdout
    assert "_keep_prompt_hook" in result.stdout
    assert "workspace=1" in result.stdout
    assert "unexpected-sync" not in result.stdout


def test_nosleep_without_standalone_command_explains_recovery(zsh):
    result = zsh('unhash -m nosleep 2>/dev/null; PATH=/usr/bin:/bin; nosleep --help; print rc=$?')
    assert "rc=127" in result.stdout
    assert "nosleep is now bundled" in result.stderr


def test_nosleep_fallback_forwards_arguments_without_loading_runtime(zsh, tmp_path):
    executable = tmp_path / "nosleep"
    executable.write_text('#!/bin/sh\nprintf "forwarded:%s\\n" "$@"\n')
    executable.chmod(0o755)
    result = zsh('PATH="$TEST_BIN:$PATH"; rehash; nosleep config --show', TEST_BIN=str(tmp_path))
    assert result.returncode == 0
    assert "forwarded:config" in result.stdout and "forwarded:--show" in result.stdout
