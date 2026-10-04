"""Dotfiles shell regression tests: csync, nosleep and zsh startup."""

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


RSYNC_STUB = "#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$RSYNC_LOG\"\nexit 0\n"


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


CSYNC = REPO_ROOT / "bin" / "csync"


def _csync_run(home, rsync_log, extra_bins):
    bins = home.parent / "csyncbin"
    bins.mkdir(exist_ok=True)
    (bins / "rsync").write_text(RSYNC_STUB)
    (bins / "rsync").chmod(0o755)
    (bins / "brctl").write_text("#!/bin/bash\nexit 0\n")   # macOS-only; a no-op stand-in
    (bins / "brctl").chmod(0o755)
    env = {**os.environ, "HOME": str(home), "RSYNC_LOG": str(rsync_log),
           "CSYNC_RSYNC": str(bins / "rsync"), "PATH": f"{bins}:{os.environ.get('PATH', '')}"}
    return subprocess.run([str(CSYNC)], env=env, capture_output=True, text=True)


def test_csync_codex_pair_is_gated_on_either_side(tmp_path):
    home = tmp_path / "home"
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    icloud.mkdir(parents=True)
    log = tmp_path / "rsync.log"
    # neither side exists → no codex pair (the claude + plan pairs always run)
    r = _csync_run(home, log, {})
    assert r.returncode == 0, r.stderr
    argv = log.read_text()
    assert "codex-sessions" not in argv and "claude-sessions" in argv
    # the local tree exists → the pair runs, sessions/ only, never the sqlite state
    (home / ".codex" / "sessions").mkdir(parents=True)
    (home / ".codex" / "state_5.sqlite").write_text("x")
    log.write_text("")
    assert _csync_run(home, log, {}).returncode == 0
    lines = [ln for ln in log.read_text().splitlines() if "codex-sessions/" in ln]
    assert len(lines) == 2                                   # both directions, once
    assert all("/.codex/sessions/" in ln for ln in lines)
    assert not any("state_5" in ln for ln in log.read_text().splitlines())
    # only the iCloud side exists (another machine ran codex) → still converges
    import shutil as _sh
    _sh.rmtree(home / ".codex")
    (icloud / "codex-sessions").mkdir(exist_ok=True)   # the 2nd run already mirrored it
    log.write_text("")
    assert _csync_run(home, log, {}).returncode == 0
    assert "codex-sessions" in log.read_text()
    assert (home / ".codex" / "sessions").is_dir()   # sync_pair mkdir -p's both sides


# ─── nosleep's busy probe: agent-agnostic ──────────────────────────────────────

PS_TABLE_STUB = r"""#!/bin/bash
# ps -Axo pid=,ppid=,comm= → the whole fixture table ("pid ppid comm")
[[ "$1" == -Axo ]] && { cat "$FAKE_PS"; exit 0; }
exit 1
"""

NETTOP_STUB = r"""#!/bin/bash
# log the argv (which pids nosleep asked about); answer cumulative per-process rows
# from $FAKE_NETTOP after the header nettop prints
printf '%s\n' "$*" >> "$NETTOP_LOG"
echo ",bytes_in,bytes_out,"
[[ -f "${FAKE_NETTOP:-}" ]] && cat "$FAKE_NETTOP"
exit 0
"""

IOREG_STUB = r"""#!/bin/bash
# ioreg -r -k AppleClamshellState … → the fixture's IOPMrootDomain property lines
# ($FAKE_IOREG); nothing at all when there is no such file, like a Mac with no lid
[[ -f "${FAKE_IOREG:-}" ]] && cat "$FAKE_IOREG"
exit 0
"""


@pytest.fixture
def nosleep(zsh, tmp_path):
    """The zsh fixture with a ps stub answering the whole-table form the probe reads, a
    nettop stub answering from $FAKE_NETTOP (cumulative per-process byte rows) and an
    ioreg stub answering the lid's IOPMrootDomain lines from $FAKE_IOREG."""
    bins = tmp_path / "stubbin"
    for name, body in (("ps", PS_TABLE_STUB), ("nettop", NETTOP_STUB), ("ioreg", IOREG_STUB)):
        f = bins / name
        f.write_text(body)
        f.chmod(0o755)
    table = tmp_path / "ps.txt"
    table.write_text("1 0 launchd\n20 1 codex\n30 1 /Users/me/.local/bin/cursor-agent\n"
                     "40 1 /Applications/ChatGPT.app/Contents/Resources/codex\n50 1 node\n60 1 zsh\n")
    net = tmp_path / "nettop.txt"
    log = tmp_path / "nettop.log"
    lid = tmp_path / "ioreg.txt"

    def call(snippet, **extra):
        return zsh(snippet, FAKE_NETTOP=str(net), NETTOP_LOG=str(log), FAKE_IOREG=str(lid), **extra)

    call.net, call.log, call.table, call.lid = net, log, table, lid
    return call


@pytest.fixture
def nosleep_loop(zsh, tmp_path):
    """Run the actual control loop with a virtual clock and no real power/network changes."""
    log = tmp_path / "nosleep-loop.log"
    setup = r'''
        zmodload -u zsh/datetime
        typeset -gi EPOCHSECONDS=1000 online_probe=0 busy_probe=0 pmset_reset=0
        typeset -a online_results=( ${=FAKE_ONLINE} ) busy_results=( ${=FAKE_BUSY} )
        sudo() { print -r -- "sudo $EPOCHSECONDS $*" >> "$LOOP_LOG"; }
        ps() { return 0; }
        caffeinate() { return 0; }
        _nosleep_hold() { print -r -- "hold $EPOCHSECONDS $*" >> "$LOOP_LOG"; }
        _nosleep_lock() { print -r -- "lock $EPOCHSECONDS" >> "$LOOP_LOG"; }
        _nosleep_brightness() {
            print -r -- "brightness $EPOCHSECONDS ${1:-read}" >> "$LOOP_LOG"
            print -r -- 0.6200
        }
        pmset() { print -r -- "pmset $EPOCHSECONDS $*" >> "$LOOP_LOG"; }
        _nosleep_lid_shut() { (( EPOCHSECONDS >= ${FAKE_LID_AT:-99999} && EPOCHSECONDS < ${FAKE_LID_OPEN_AT:-99999} )); }
        _nosleep_lid_closed() { _nosleep_lid_shut; }
        _nosleep_pmset_held() {
            if (( ! pmset_reset && EPOCHSECONDS >= ${FAKE_RESET_AT:-99999} )); then
                pmset_reset=1
                return 1
            fi
            return 0
        }
        _nosleep_online() {
            print -r -- "online $EPOCHSECONDS" >> "$LOOP_LOG"
            (( online_probe < ${#online_results} )) && (( ++online_probe ))
            (( online_results[online_probe] ))
        }
        _nosleep_busy_at() {
            print -r -- "busy $EPOCHSECONDS baselines=${#_NOSLEEP_NET_AT}" >> "$LOOP_LOG"
            (( busy_probe < ${#busy_results} )) && (( ++busy_probe ))
            REPLY=0
            if (( busy_results[busy_probe] )); then
                REPLY=$EPOCHSECONDS
                _NOSLEEP_WHO=codex
            fi
            _NOSLEEP_NET_AT=( 42 $EPOCHSECONDS )
            (( EPOCHSECONDS += ${FAKE_BUSY_SECONDS:-0} ))
        }
        sleep() {
            (( EPOCHSECONDS += $1 ))
            if (( EPOCHSECONDS >= ${FAKE_STOP_AT:-99999} )); then kill -TERM $$; fi
            if (( EPOCHSECONDS > 5000 )); then print -u2 "virtual clock limit"; exit 99; fi
        }
    '''

    def call(args="", **env):
        tty = env.pop("_tty", False)
        columns = env.pop("_columns", None)
        if tty:
            env.setdefault("TERM", "xterm-256color")
        size = f"COLUMNS={int(columns)}; " if columns is not None else ""
        result = zsh(setup + f'\n{size}nosleep {args}; rc=$?; print -r -- "exit=$rc at=$EPOCHSECONDS"',
                     LOOP_LOG=str(log), FAKE_ONLINE=env.pop("FAKE_ONLINE", "1"),
                     FAKE_BUSY=env.pop("FAKE_BUSY", "0"),
                     FAKE_LID_AT=env.pop("FAKE_LID_AT", "1000"), _tty=tty, **env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not result.stderr, result.stderr
        return result, log.read_text().splitlines() if log.exists() else []

    return call


def test_zsh_nosleep_plain_snapshot_shows_sampling_then_idle(nosleep_loop):
    result, _ = nosleep_loop("--every 2", FAKE_LID_AT="99999", FAKE_STOP_AT="1006")
    output = result.stdout
    assert "nosleep — KEEPING MAC AWAKE" in output
    for label in ("Sleep", "Display", "Dim", "Locking", "Policy", "Agents", "Internet", "Next", "Stop"):
        assert re.search(rf"(?m)^  {label}\b", output), label
    snapshot = output.split("nosleep — KEEPING MAC AWAKE", 1)[1].split("  Stop", 1)[0]
    live, config = snapshot.split("Configuration", 1)
    assert "Live status" in live
    for label in ("Agents", "Internet", "Next"):
        assert re.search(rf"(?m)^  {label}\b", live), label
    for label in ("Sleep", "Display", "Dim", "Locking", "Policy"):
        assert not re.search(rf"(?m)^  {label}\b", live), label
        assert re.search(rf"(?m)^  {label}\b", config), label
    assert not re.search(r"(?m)^  Laptop\b", output)
    assert "Sampling activity" in output
    assert "No activity detected" in output
    assert re.search(r"(?m)^  Internet\b.*Online", output)
    assert "Ctrl-C" in output
    assert "exit=130 at=1006" in output


def test_zsh_nosleep_snapshot_distinguishes_active_from_last_seen(nosleep_loop):
    result, _ = nosleep_loop("--every 2", FAKE_BUSY="1 0 0", FAKE_STOP_AT="1006")
    assert re.search(r"(?m)^  Agents\b.*Active.*codex", result.stdout)
    assert re.search(r"(?m)^  Agents\b.*Quiet.*codex.*last active", result.stdout)
    assert "exit=130 at=1006" in result.stdout


def test_zsh_nosleep_snapshot_shows_offline_grace_and_retry(nosleep_loop):
    result, _ = nosleep_loop("--grace 4 --every 2 --backoff 2", FAKE_ONLINE="0",
                             FAKE_STOP_AT="1008")
    assert re.search(r"(?m)^  Internet\b.*Offline", result.stdout)
    assert re.search(r"(?m)^  Next\b.*Retry 1/3 in 2s", result.stdout)
    assert "exit=130 at=1008" in result.stdout


def test_zsh_nosleep_forever_snapshot_does_not_claim_to_check_signals(nosleep_loop):
    result, log = nosleep_loop("--forever", FAKE_STOP_AT="1004")
    assert "nosleep — KEEPING MAC AWAKE" in result.stdout
    assert re.search(r"(?m)^  Agents\b.*Not checked.*--forever", result.stdout)
    assert re.search(r"(?m)^  Internet\b.*Not checked.*--forever", result.stdout)
    assert re.search(r"(?m)^  Next\b.*No checks.*--forever", result.stdout)
    assert not any(line.startswith(("online ", "busy ")) for line in log)


def test_zsh_nosleep_tty_countdown_redraws_between_checks(nosleep_loop):
    result, _ = nosleep_loop(FAKE_LID_AT="99999", FAKE_STOP_AT="1004", _tty=True)
    assert "nosleep — KEEPING MAC AWAKE" in result.stdout
    assert re.search(r"Check in 30s", result.stdout)
    assert re.search(r"Check in 28s", result.stdout)
    assert "\x1b[" in result.stdout
    assert "exit=130 at=1004" in result.stdout


def test_zsh_nosleep_tty_grace_counts_down(nosleep_loop):
    result, _ = nosleep_loop("--grace 4 --every 2", FAKE_STOP_AT="1004", _tty=True)
    assert "grace 4s left" in result.stdout
    assert "grace 2s left" in result.stdout
    assert "Retry 1/" not in result.stdout


def test_zsh_nosleep_tty_retry_counts_down_and_releases_once(nosleep_loop):
    result, log = nosleep_loop("--grace 0 --every 2 --backoff 4", FAKE_STOP_AT="1006",
                              _tty=True)
    assert "Retry 1/3 in 4s" in result.stdout
    assert "Retry 1/3 in 2s" in result.stdout
    assert result.stdout.count("nosleep: stopped — sleep hold released") == 1
    assert [line for line in log if "disablesleep 0" in line] == [
        "sudo 1006 -n pmset -a disablesleep 0"]
    assert "exit=130 at=1006" in result.stdout


def test_zsh_nosleep_tty_narrow_terminal_clips_each_dashboard_row(nosleep_loop):
    result, _ = nosleep_loop(FAKE_STOP_AT="1002", _tty=True, _columns=40)
    visible = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result.stdout)
    dashboard = [line for line in visible.splitlines() if line.startswith(("nosleep —", "  "))]
    assert dashboard
    assert any(line.endswith("…") for line in dashboard)
    assert all(len(line) <= 39 for line in dashboard), dashboard


def test_zsh_nosleep_dumb_tty_uses_plain_snapshots(nosleep_loop):
    result, _ = nosleep_loop("--every 2", FAKE_STOP_AT="1004", _tty=True, TERM="dumb")
    assert result.stdout.count("nosleep — KEEPING MAC AWAKE") >= 2
    assert "\x1b[" not in result.stdout
    assert "exit=130 at=1004" in result.stdout


@pytest.mark.parametrize("offline", [False, True])
def test_zsh_nosleep_open_lid_reports_missing_signals_without_releasing(nosleep_loop, offline):
    result, log = nosleep_loop("--grace 0 --every 2 --retries 0",
                               FAKE_LID_AT="99999", FAKE_STOP_AT="1010",
                               FAKE_ONLINE="0" if offline else "1")
    assert "nosleep: advisory — " in result.stdout
    assert "laptop open, sleep stays blocked" in result.stdout
    assert "retry " not in result.stdout and "letting go" not in result.stdout
    assert "exit=130 at=1010" in result.stdout
    assert [line for line in log if "disablesleep 0" in line] == [
        "sudo 1010 -n pmset -a disablesleep 0"]


def test_zsh_nosleep_closing_lid_starts_retries_after_open_lid_advisories(nosleep_loop):
    result, log = nosleep_loop("--grace 0 --every 2 --backoff 2 --retries 1",
                               FAKE_LID_AT="1010")
    assert "nosleep: advisory — no local agent activity" in result.stdout
    assert "no local agent activity for 10s — retry 1/1 in 2s" in result.stdout
    assert "1 retries exhausted" in result.stdout
    assert "exit=0 at=1012" in result.stdout
    assert "lock 1010" in log


def test_zsh_nosleep_opening_lid_cancels_pending_retry(nosleep_loop):
    result, log = nosleep_loop("--grace 0 --every 2 --backoff 2 --retries 1",
                               FAKE_LID_OPEN_AT="1004", FAKE_STOP_AT="1010")
    assert "retry 1/1 in 2s" in result.stdout
    assert "nosleep: advisory — no local agent activity" in result.stdout
    assert "letting go" not in result.stdout
    assert "exit=130 at=1010" in result.stdout


@pytest.mark.parametrize("offline", [False, True])
def test_zsh_nosleep_grace_then_three_backoff_retries(nosleep_loop, offline):
    result, log = nosleep_loop(FAKE_ONLINE="0" if offline else "1")
    assert "retry 1/3 in 30s" in result.stdout
    assert "retry 2/3 in 60s" in result.stdout
    assert "retry 3/3 in 120s" in result.stdout
    assert "3 retries exhausted" in result.stdout
    assert "exit=0 at=2140" in result.stdout
    assert [line for line in log if line.startswith("online ")][-4:] == [
        "online 1930", "online 1960", "online 2020", "online 2140"]
    assert [line for line in log if "disablesleep" in line] == [
        "sudo 1000 pmset -a disablesleep 1", "sudo 2140 -n pmset -a disablesleep 0"]


def test_zsh_nosleep_zero_grace_still_samples_then_retries(nosleep_loop):
    result, log = nosleep_loop("--grace 0 --every 2 --backoff 2")
    assert [line for line in log if line.startswith("online ")] == [
        "online 1000", "online 1002", "online 1004", "online 1008", "online 1016"]
    assert "exit=0 at=1016" in result.stdout


def test_zsh_nosleep_activity_recovery_resets_backoff(nosleep_loop):
    result, log = nosleep_loop("--grace 0 --every 2 --backoff 2",
                               FAKE_BUSY="0 0 0 1 0")
    assert "activity recovered" in result.stdout
    assert result.stdout.count("retry 1/3 in 2s") == 2
    assert "exit=0 at=1024" in result.stdout
    assert "online 1010" in log  # normal cadence resumes after recovery at 1008


def test_zsh_nosleep_reconnect_resets_activity_grace_and_baselines(nosleep_loop):
    result, log = nosleep_loop("--grace 4 --every 2 --backoff 2",
                               FAKE_ONLINE="0 0 0 0 0 1")
    assert "network recovered — restarting agent activity sampling and grace" in result.stdout
    assert "busy 1012 baselines=0" in log
    assert "online 1014" in log  # normal cadence resumes, without consuming a retry
    assert "no local agent activity for 6s — retry 1/3 in 2s" in result.stdout
    assert "exit=0 at=1032" in result.stdout


def test_zsh_nosleep_short_blips_stay_inside_grace(nosleep_loop):
    result, _ = nosleep_loop("--grace 8 --every 2", FAKE_ONLINE="1 0 1",
                             FAKE_BUSY="1 0 0 1", FAKE_STOP_AT="1008")
    assert "retry 1/" not in result.stdout and "letting go" not in result.stdout
    assert "exit=130 at=1008" in result.stdout


def test_zsh_nosleep_slow_successful_probe_does_not_expire_zero_grace(nosleep_loop):
    result, _ = nosleep_loop("--grace 0 --every 2", FAKE_BUSY="1",
                             FAKE_BUSY_SECONDS="2", FAKE_STOP_AT="1012")
    assert "retry 1/" not in result.stdout and "letting go" not in result.stdout
    assert "exit=130 at=1012" in result.stdout


def test_zsh_nosleep_lid_sudo_and_interrupt_work_during_backoff(nosleep_loop):
    result, log = nosleep_loop("--grace 0", FAKE_LID_AT="1010", FAKE_STOP_AT="1200",
                               FAKE_RESET_AT="1160")
    assert "retry 3/3 in 120s" in result.stdout
    assert "lock 1010" in log  # lid handling continues between signal checks
    assert "sudo 1180 -n -v" in log  # keep sudo alive during the 120s wait
    assert "sudo 1180 -n pmset -a disablesleep 1" in log  # re-arm during backoff too
    assert "exit=130 at=1200" in result.stdout
    assert [line for line in log if "disablesleep 0" in line] == [
        "sudo 1200 -n pmset -a disablesleep 0"]


def test_zsh_nosleep_retry_delay_is_capped(nosleep_loop):
    result, _ = nosleep_loop("--grace 0 --every 2 --backoff 200 --retries 4")
    assert "retry 1/4 in 200s" in result.stdout
    assert all(f"retry {i}/4 in 300s" in result.stdout for i in (2, 3, 4))
    assert "exit=0 at=2102" in result.stdout


def test_zsh_nosleep_retries_can_be_disabled(nosleep_loop):
    result, _ = nosleep_loop("--grace 0 --every 2 --retries 0")
    assert "retry 1/" not in result.stdout
    assert "exit=0 at=1002" in result.stdout


def test_zsh_nosleep_forever_skips_probes(nosleep_loop):
    result, log = nosleep_loop("--forever", FAKE_STOP_AT="1060")
    assert not any(line.startswith(("online ", "busy ")) for line in log)
    assert "exit=130 at=1060" in result.stdout


@pytest.mark.parametrize("args", ["--grace", "--every", "--retries", "--backoff",
                                  "--retries -1", "--retries 11", "--retries abc",
                                  "--backoff 0", "--backoff 301", "--backoff abc"])
def test_zsh_nosleep_invalid_retry_options_do_not_change_power_settings(zsh, args):
    result = zsh('sudo() { echo unexpected-sudo; }; ' + f'nosleep {args}; echo rc=$?')
    assert result.stdout.strip() == "rc=2"
    assert "nosleep:" in result.stderr


@pytest.mark.parametrize("outcomes, expected, rc", [
    ("0", ["https://api.anthropic.com/"], 0),
    ("6 0", ["https://api.anthropic.com/", "https://api.openai.com/"], 0),
    ("6 28", ["https://api.anthropic.com/", "https://api.openai.com/"], 1),
])
def test_zsh_nosleep_connectivity_tries_another_provider(zsh, outcomes, expected, rc):
    result = zsh(r'''
        typeset -a results=( ${=FAKE_CURL_RESULTS} )
        typeset -i n=0
        curl() { print -r -- "$*"; (( ++n )); return $results[n]; }
        _nosleep_online; print -r -- "rc=$?"
    ''', FAKE_CURL_RESULTS=outcomes)
    assert result.stdout.splitlines() == [
        f"-s -o /dev/null --max-time 4 {url}" for url in expected] + [f"rc={rc}"]


def test_zsh_nosleep_agent_of_comm(zsh):
    # the slot seam's match (claude, codex, the npm codex-<triple>) plus cursor-agent —
    # by basename, since ps reports argv[0] and cursor-agent's launcher execs its own path
    r = zsh("for c in claude /usr/local/bin/claude codex codex-aarch64-apple-darwin cursor-agent "
            "/Users/me/.local/bin/cursor-agent node zsh cursor; do _nosleep_agent_of_comm $c; echo \"$c=$REPLY\"; done")
    assert r.stdout.split() == ["claude=claude", "/usr/local/bin/claude=claude", "codex=codex",
                                "codex-aarch64-apple-darwin=codex", "cursor-agent=cursor",
                                "/Users/me/.local/bin/cursor-agent=cursor", "node=", "zsh=", "cursor="]


def test_zsh_nosleep_agent_of_comm_counts_the_desktop_apps_agents(zsh):
    # the desktop apps run the same agent cores from inside a bundle (the paths as ps
    # printed them, 2026-09-25) — those count; every other bundled binary does not
    apps = [
        ("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex", "ChatGPT"),
        ("/Users/me/Library/Application Support/Claude/claude-code/2.1.281/claude.app/Contents/MacOS/claude", "Claude app"),
        ("/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node", ""),
        ("/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper", ""),
        ("/Users/me/.codex/computer-use/Codex Computer Use.app/Contents/MacOS/SkyComputerUseService", ""),
        ("/Applications/Other.app/Contents/MacOS/claude", ""),
    ]
    script = "; ".join(f"_nosleep_agent_of_comm {shlex.quote(c)}; echo \"[$REPLY]\"" for c, _ in apps)
    assert zsh(script).stdout.splitlines() == [f"[{want}]" for _, want in apps]


PMSET_STUB = r"""#!/bin/bash
# pmset -g → the settings dump, with the flag from $FAKE_SLEEP_DISABLED
[[ "$1" == -g ]] && { printf ' SleepDisabled\t\t%s\n sleep                10\n' "${FAKE_SLEEP_DISABLED:-0}"; exit 0; }
exit 1
"""


def test_zsh_nosleep_pmset_held_reads_the_flag(zsh, tmp_path):
    # the lid-close protection is pmset's global disablesleep flag, which any nosleep's
    # exit clears for every other run — the loop re-arms it when a probe reads 0
    stub = tmp_path / "stubbin" / "pmset"
    stub.write_text(PMSET_STUB)
    stub.chmod(0o755)
    assert zsh("_nosleep_pmset_held; echo rc=$?", FAKE_SLEEP_DISABLED="1").stdout.strip() == "rc=0"
    assert zsh("_nosleep_pmset_held; echo rc=$?", FAKE_SLEEP_DISABLED="0").stdout.strip() == "rc=1"


def test_zsh_nosleep_claude_caffeinate_is_busy_at_once(nosleep):
    # Claude Code's own caffeinate child: instant, no baseline needed
    nosleep.table.write_text("1 0 launchd\n10 1 claude\n11 10 caffeinate\n12 1 caffeinate\n")
    r = nosleep('_nosleep_busy_at; echo "$REPLY $_NOSLEEP_WHO"')
    when, who = r.stdout.split()
    assert int(when) > 1_700_000_000 and who == "claude"
    # a caffeinate parented to anything else (sleep-manager's, an orphan) is not a turn
    nosleep.table.write_text("1 0 launchd\n10 1 claude\n12 1 caffeinate\n")
    assert nosleep('_nosleep_busy_at; echo "$REPLY"').stdout.strip() == "0"


def test_zsh_nosleep_bytes_moved_since_the_last_probe(nosleep):
    # nettop without -P: a "<name>.<pid>" row, then one row per open socket with its
    # LIFETIME bytes (the process row's numbers are the sum over those sockets)
    nosleep.net.write_text("codex.20,1000,1000,\ntcp4 10.0.0.2:1<->1.2.3.4:443,1000,1000,\n"
                           "cursor-agent.30,5000,100,\ntcp4 10.0.0.2:2<->5.6.7.8:443,5000,100,\n"
                           "codex.40,900000,900000,\ntcp4 10.0.0.2:3<->9.9.9.9:443,900000,900000,\n")
    # probe 1 baselines; probe 2 sees codex move 60 KB and cursor 50 B (keepalives);
    # probe 3 sees nothing move; probe 4 runs after every agent has exited
    r = nosleep(
        '_nosleep_busy_at; echo "p1=$REPLY who=$_NOSLEEP_WHO"; '
        "printf '%s\\n' 'codex.20,31000,31000,' 'tcp4 10.0.0.2:1<->1.2.3.4:443,31000,31000,' "
        "'cursor-agent.30,5040,110,' 'tcp4 10.0.0.2:2<->5.6.7.8:443,5040,110,' "
        "'codex.40,5000000,5000000,' 'tcp4 10.0.0.2:3<->9.9.9.9:443,5000000,5000000,' > $FAKE_NETTOP; "
        '_nosleep_busy_at; echo "p2=$REPLY who=$_NOSLEEP_WHO"; '
        '_nosleep_busy_at; echo "p3=$REPLY who=$_NOSLEEP_WHO pids=${(k)_NOSLEEP_NET_AT} socks=${#_NOSLEEP_NET}"; '
        "echo '1 0 launchd' > $FAKE_PS; "
        '_nosleep_busy_at; echo "p4=$REPLY pids=${(k)_NOSLEEP_NET_AT} socks=${#_NOSLEEP_NET}"')
    p1, p2, p3, p4 = r.stdout.splitlines()
    assert p1 == "p1=0 who="                                        # first sighting: baselines only
    when, who = p2[3:].split(" who=")
    assert int(when) > 1_700_000_000                                # 60 KB in a probe = working; 50 B = not
    assert sorted(who.split(", ")) == ["ChatGPT", "codex"]          # the ChatGPT app's codex counts (2026-09-25)
    assert p3.startswith("p3=0 who=")                               # quiet; WHO keeps the last names (status line)
    pids, socks = p3.split("pids=")[1].split(" socks=")
    assert sorted(pids.split()) == ["20", "30", "40"] and socks == "3"   # every agent keeps its baselines
    assert p4 == "p4=0 pids= socks=0"                               # gone agents drop them
    # only agents were asked about — the CLIs and the ChatGPT app's bundled codex (40),
    # not node/zsh — never with name resolution (5 s a probe), and never collapsed with -P
    # (the per-process total is not cumulative — see the next tests)
    asked = nosleep.log.read_text().splitlines()[0]
    assert "-p 20" in asked and "-p 30" in asked and "-p 40" in asked and "-p 50" not in asked
    assert "-n" in asked.split() and "-P" not in asked.split()


def test_zsh_nosleep_idle_bursts_stay_under_the_floor(nosleep):
    # the idle patterns measured at the prompt (5 s samples over 150 s, 2026-09-13) —
    # every one of them cleared the old 256 B/s × elapsed floor, which is how a nosleep
    # reported "claude, codex active 0s ago" with both waiting for input. nettop names
    # a claude by its binary, the version number, so its row is "2.1.270.<pid>".
    nosleep.table.write_text("1 0 launchd\n10 1 claude\n20 1 codex\n")
    nosleep.net.write_text("2.1.270.10,10283,8382,\ntcp6 [a]:1<->[b]:443,10283,8382,\n"
                           "codex.20,89336,798974,\ntcp6 [c]:52251<->[d]:443,89336,798974,\n")
    r = nosleep(
        '_nosleep_busy_at; '
        # claude: a 39 B keepalive plus its short-lived telemetry connection (~2.4 KB in /
        # 3.1 KB out); codex: a ~15 KB post on its persistent socket plus a ~7 KB short
        # connection — the most either was seen moving inside one window
        "printf '%s\\n' '2.1.270.10,12792,11452,' 'tcp6 [a]:1<->[b]:443,10322,8382,' 'tcp6 [a]:2<->[e]:443,2470,3070,' "
        "'codex.20,94779,815685,' 'tcp6 [c]:52251<->[d]:443,90798,812779,' 'tcp6 [c]:9<->[f]:443,3981,2906,' > $FAKE_NETTOP; "
        '_nosleep_busy_at; echo "p2=$REPLY who=$_NOSLEEP_WHO"; '
        # two minutes on, the same again (the short connections are gone, the posts
        # repeat): the rate bound, 1 KiB/s × 120 s, is higher still
        '_NOSLEEP_NET_AT[10]=$(( EPOCHSECONDS - 120 )); _NOSLEEP_NET_AT[20]=$(( EPOCHSECONDS - 120 )); '
        "printf '%s\\n' '2.1.270.10,10361,8382,' 'tcp6 [a]:1<->[b]:443,10361,8382,' "
        "'codex.20,92778,826584,' 'tcp6 [c]:52251<->[d]:443,92778,826584,' > $FAKE_NETTOP; "
        '_nosleep_busy_at; echo "p3=$REPLY who=$_NOSLEEP_WHO socks=${#_NOSLEEP_NET}"')
    assert r.stdout.splitlines() == ["p2=0 who=", "p3=0 who= socks=2"]   # the closed sockets dropped their baselines


def test_zsh_nosleep_a_replaced_socket_does_not_hide_a_turn(nosleep):
    # nettop's per-process total is a sum over the sockets open RIGHT NOW, so when a
    # request's connection closes and the next request opens a fresh one the total
    # DROPS — a process-level delta reads this busy turn as -600 KB. Per socket, the
    # fresh connection's 200 KB is exactly what moved since the last probe.
    nosleep.table.write_text("1 0 launchd\n20 1 codex\n")
    nosleep.net.write_text("codex.20,300000,500000,\ntcp4 [a]:1<->[b]:443,300000,500000,\n")
    r = nosleep('_nosleep_busy_at; '
                "printf '%s\\n' 'codex.20,20000,180000,' 'tcp4 [a]:2<->[b]:443,20000,180000,' > $FAKE_NETTOP; "
                '_nosleep_busy_at; echo "$REPLY $_NOSLEEP_WHO"')
    when, who = r.stdout.split()
    assert int(when) > 1_700_000_000 and who == "codex"


def test_zsh_nosleep_net_floor_is_tunable(nosleep):
    # each snippet rewrites the rows itself: the probes mutate $FAKE_NETTOP, so a call
    # starting from the previous call's moved rows would see a zero delta
    base = "printf '%s\\n' 'codex.20,0,0,' 'tcp4 [a]:1<->[b]:443,0,0,' > $FAKE_NETTOP; _nosleep_busy_at; "
    move = "printf '%s\\n' 'codex.20,40000,40000,' 'tcp4 [a]:1<->[b]:443,40000,40000,' > $FAKE_NETTOP; _nosleep_busy_at; echo $REPLY"
    assert nosleep(base + move).stdout.strip() != "0"                            # 80 KB in a probe clears the 48 KiB default
    assert nosleep(base + move, NOSLEEP_NET_MIN="100000").stdout.strip() == "0"  # not a 100 KB one
    # the rate bound: the same 80 KB after a 120 s gap is under 1 KiB/s × 120 s …
    gap = base + "_NOSLEEP_NET_AT[20]=$(( EPOCHSECONDS - 120 )); " + move
    assert nosleep(gap).stdout.strip() == "0"
    assert nosleep(gap, NOSLEEP_NET_BPS="100").stdout.strip() != "0"             # … but over 100 B/s × 120 s



def test_zsh_nosleep_lock_locks_then_sleeps_the_display(nosleep, tmp_path):
    # lid close: lock the screen (SACLockScreenImmediate via python ctypes), THEN put the
    # display to sleep — under disablesleep macOS leaves the panel lit behind a closed lid
    # until the displaysleep timer. Both calls are stubbed: the real ones would lock and
    # blank the developer's screen mid-test.
    bins = tmp_path / "stubbin"
    for name, tag in (("python3", ""), ("pmset", "pmset ")):
        stub = bins / name
        stub.write_text('#!/bin/bash\nprintf "%s%%s\\n" "$*" >> "$LOCK_LOG"\nexit 0\n' % tag)
        stub.chmod(0o755)
    log = tmp_path / "lock.log"
    r = nosleep("_nosleep_lock", LOCK_LOG=str(log))
    calls = log.read_text().splitlines()
    assert len(calls) == 2 and "SACLockScreenImmediate" in calls[0]     # lock first …
    assert calls[1] == "pmset displaysleepnow"                          # … then the display off
    assert r.stdout.strip() == "nosleep: laptop closed — screen locked, display off"


def test_zsh_nosleep_lid_closed_only_when_macos_would_sleep_on_it(nosleep):
    # AppleClamshellCausesSleep is the kernel's own verdict (shouldSleepOnClamshellClosed:
    # No while an external display on power drives the Mac) and it ignores pmset
    # disablesleep — so a docked Mac never reads closed (the first version locked its
    # external display the moment nosleep started), while a plain laptop under nosleep's
    # own disablesleep still does
    def closed(*props):
        nosleep.lid.write_text("".join(f'      "{k}" = {v}\n' for k, v in props))
        return nosleep("_nosleep_lid_closed; echo $?").stdout.strip() == "0"
    assert closed(("AppleClamshellCausesSleep", "Yes"), ("AppleClamshellState", "Yes"))
    assert not closed(("AppleClamshellCausesSleep", "No"), ("AppleClamshellState", "Yes"))   # clamshell mode
    assert not closed(("AppleClamshellCausesSleep", "Yes"), ("AppleClamshellState", "No"))   # lid open
    assert not closed()                                                                      # no lid at all


def test_zsh_nosleep_physical_lid_state_includes_docked_clamshell(nosleep):
    nosleep.lid.write_text('"AppleClamshellCausesSleep" = No\n"AppleClamshellState" = Yes\n')
    assert nosleep('_nosleep_lid_shut; echo $?').stdout.strip() == "0"
    nosleep.lid.write_text('"AppleClamshellState" = No\n')
    assert nosleep('_nosleep_lid_shut; echo $?').stdout.strip() == "1"


ASSERTIONS_STUB = r"""#!/bin/bash
# pmset -g assertions → the fixture's per-process listing ($FAKE_ASSERT)
[[ "$1 $2" == "-g assertions" && -f "${FAKE_ASSERT:-}" ]] && cat "$FAKE_ASSERT"
exit 0
"""


def test_zsh_nosleep_app_power_assertions_read_as_working(nosleep, tmp_path):
    # the Claude app takes an Electron idle-sleep assertion while it works (pmset -g log,
    # 2026-09-25); the ChatGPT app's "Capturing" is display-only, and a caffeinate is the
    # CLI signal's business, not this one's
    stub = tmp_path / "stubbin" / "pmset"
    stub.write_text(ASSERTIONS_STUB)
    stub.chmod(0o755)
    listing = tmp_path / "assert.txt"
    listing.write_text(
        "Listed by owning process:\n"
        "   pid 25974(caffeinate): [0x1] 02:05:39 PreventUserIdleSystemSleep named: \"caffeinate command-line tool\"  \n"
        "   pid 53916(ChatGPT): [0x2] 00:00:03 NoDisplaySleepAssertion named: \"Capturing\"  \n")
    nosleep.table.write_text("1 0 launchd\n")
    probe = '_nosleep_busy_at; echo "$REPLY|$_NOSLEEP_WHO"'
    assert nosleep(probe, FAKE_ASSERT=str(listing)).stdout.strip() == "0|"
    listing.write_text(listing.read_text() +
        "   pid 81176(Claude): [0x3] 00:03:35 PreventUserIdleSystemSleep named: \"Electron\"  \n")
    when, who = nosleep(probe, FAKE_ASSERT=str(listing)).stdout.strip().split("|")
    assert int(when) > 1_700_000_000 and who == "Claude app"


@pytest.mark.parametrize("assertion", [
    "PreventUserIdleSystemSleep", "PreventSystemSleep", "NoIdleSleepAssertion",
])
def test_zsh_nosleep_app_assertions_tolerate_invalid_utf8(nosleep, tmp_path, assertion):
    locales = subprocess.check_output(["locale", "-a"], text=True).splitlines()
    utf8_locale = next((name for name in locales if name.lower().replace("-", "").endswith(".utf8")), None)
    if utf8_locale is None:
        pytest.skip("a UTF-8 locale is required to exercise macOS regex decoding")
    stub = tmp_path / "stubbin" / "pmset"
    stub.write_text(ASSERTIONS_STUB)
    stub.chmod(0o755)
    listing = tmp_path / "assert.txt"
    # pmset can include non-UTF-8 bytes anywhere, even on unrelated lines or in
    # the description of an assertion we must still recognize as active work.
    listing.write_bytes(
        b'Listed by owning process:\n'
        b'   pid 10(other\xff): [0x1] 00:00:03 PreventSystemSleep named: "other"\n'
        b'   pid 20(ChatGPT): [0x2] 00:00:03 NoDisplaySleepAssertion named: "Capturing\xff"\n'
        + f'   pid 30(Claude): [0x3] 00:00:03 {assertion} named: "'.encode()
        + b'Electron\xff"\n'
        b'   pid 40(ChatGPT): [0x4] 00:00:03 UserIsActive named: "activity\xff"\n')
    nosleep.table.write_text("1 0 launchd\n")
    result = nosleep('_nosleep_busy_at; print -r -- "$REPLY|$_NOSLEEP_WHO"; print -r -- "$LC_ALL"',
                     FAKE_ASSERT=str(listing), LC_ALL=utf8_locale)
    assert result.returncode == 0
    assert result.stderr == ""
    activity, restored_locale = result.stdout.splitlines()
    when, who = activity.split("|")
    assert int(when) > 1_700_000_000 and who == "Claude app"
    assert restored_locale == utf8_locale


@pytest.mark.skipif(sys.platform != "darwin", reason="requires real macOS power assertions")
def test_zsh_nosleep_reads_live_macos_power_assertions(zsh, tmp_path):
    # Give the test its own process name so other caffeinate users cannot make a
    # broken parser pass. The helper creates only temporary, user-level assertions.
    source = tmp_path / "assertion.c"
    binary = tmp_path / f"ns{os.getpid():x}"
    source.write_text(r"""
#include <IOKit/pwr_mgt/IOPMLib.h>
#include <CoreFoundation/CoreFoundation.h>
#include <unistd.h>
int main(int argc, char **argv) {
    IOPMAssertionID assertion;
    CFStringRef kind = argc > 1 && argv[1][0] == 'd'
        ? kIOPMAssertionTypePreventUserIdleDisplaySleep
        : kIOPMAssertionTypePreventUserIdleSystemSleep;
    IOReturn status = IOPMAssertionCreateWithName(kind, kIOPMAssertionLevelOn,
                                                  CFSTR("nosleep macOS test"), &assertion);
    if (status != kIOReturnSuccess) return 1;
    sleep(30);
    IOPMAssertionRelease(assertion);
    return 0;
}""")
    subprocess.run(["cc", "-framework", "IOKit", "-framework", "CoreFoundation",
                    str(source), "-o", str(binary)], check=True, capture_output=True, text=True)

    def detected():
        result = zsh(f'NOSLEEP_APPS=({binary.name} probe); '
                     '_nosleep_app_asserting; print -r -- "rc=$? labels=${(j:, :)reply}"')
        assert result.returncode == 0 and not result.stderr, result.stderr
        return result.stdout.strip()

    assert detected() == "rc=1 labels="
    for mode, assertion, expected in (
        ("display", "PreventUserIdleDisplaySleep", "rc=1 labels="),
        ("system", "PreventUserIdleSystemSleep", "rc=0 labels=probe"),
    ):
        proc = subprocess.Popen([str(binary), mode], stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        try:
            for _ in range(30):
                listing = subprocess.run(["pmset", "-g", "assertions"],
                                         check=True, capture_output=True, text=True,
                                         errors="replace").stdout
                if any(f"pid {proc.pid}({binary.name}):" in line and assertion in line
                       for line in listing.splitlines()):
                    break
                assert proc.poll() is None, proc.stderr.read().decode(errors="replace")
                time.sleep(0.1)
            else:
                pytest.fail(f"macOS did not report the test process's {assertion} assertion")
            assert detected() == expected
        finally:
            proc.terminate()
            proc.wait(timeout=5)
    assert detected() == "rc=1 labels="


def test_zsh_nosleep_desktop_claude_caffeinate_is_labelled_the_app(nosleep):
    # the Claude app's Code sessions run the same binary, caffeinate child and all
    claude = "/Users/me/Library/Application Support/Claude/claude-code/2.1.281/claude.app/Contents/MacOS/claude"
    nosleep.table.write_text(f"1 0 launchd\n10 1 {claude}\n11 10 caffeinate\n")
    when, who = nosleep('_nosleep_busy_at; echo "$REPLY|$_NOSLEEP_WHO"').stdout.strip().split("|")
    assert int(when) > 1_700_000_000 and who == "Claude app"


def test_zsh_nosleep_dim_dims_then_restores_the_builtin_panel(nosleep, tmp_path):
    # --no-lock's lid close: brightness down (remembering the level it was at), no lock, no
    # display sleep; lid open puts the level back. python3 is stubbed — the real call
    # would dim the developer's screen mid-test.
    stub = tmp_path / "stubbin" / "python3"
    stub.write_text('#!/bin/bash\ncat >/dev/null\nprintf "%s\\n" "$*" >> "$DIM_LOG"\necho 0.6200\n')
    stub.chmod(0o755)
    log = tmp_path / "dim.log"
    r = nosleep('_NOSLEEP_BRIGHT=""; _nosleep_dim; echo "saved=$_NOSLEEP_BRIGHT"; '
                '_nosleep_dim; echo "saved=$_NOSLEEP_BRIGHT"; '
                '_nosleep_undim; echo "saved=[$_NOSLEEP_BRIGHT]"',
                DIM_LOG=str(log), NOSLEEP_DIM_LEVEL="0.05")
    assert r.stdout.splitlines() == ["nosleep: laptop closed — no lock requested, display dimmed", "saved=0.6200",
                                     "nosleep: laptop closed — no lock requested, display dimmed", "saved=0.6200",
                                     "saved=[]"]
    # a second dim (auto-brightness re-applied) never overwrites the level to restore
    assert log.read_text().splitlines() == ["- 0.05", "- 0.05", "- 0.6200"]


@pytest.mark.parametrize("option", ["--no-lock", "-d", "--dim"])
def test_zsh_nosleep_no_lock_dims_without_locking_and_restores_on_open(nosleep_loop, option):
    result, log = nosleep_loop(f"{option} --every 2", FAKE_LID_AT="1002",
                               FAKE_LID_OPEN_AT="1006", FAKE_STOP_AT="1008")
    assert "laptop closed — no lock requested, display dimmed" in result.stdout
    assert "exit=130 at=1008" in result.stdout
    assert [line for line in log if line.startswith("hold ")] == ["hold 1000 -dims"]
    assert not any(line.startswith("lock ") for line in log)
    assert not any("displaysleepnow" in line for line in log)
    assert "brightness 1002 0" in log
    assert "brightness 1006 0.6200" in log
    assert [line for line in log if "disablesleep 0" in line] == [
        "sudo 1008 -n pmset -a disablesleep 0"]


def test_zsh_nosleep_no_lock_restores_brightness_on_interrupt_behind_closed_lid(nosleep_loop):
    result, log = nosleep_loop("--no-lock --forever", FAKE_STOP_AT="1004")
    assert "exit=130 at=1004" in result.stdout
    assert "brightness 1000 0" in log
    assert "brightness 1004 0.6200" in log
    assert not any(line.startswith("lock ") for line in log)
    assert [line for line in log if line.startswith("hold ")] == ["hold 1000 -dims"]
    assert not any(line.startswith(("online ", "busy ")) for line in log)
    assert [line for line in log if "disablesleep 0" in line] == [
        "sudo 1004 -n pmset -a disablesleep 0"]


@pytest.mark.parametrize("args", ["--help", "--no-lock --help"])
def test_zsh_nosleep_help_is_concise_and_does_not_change_power(zsh, args):
    (zsh.home / ".zshrc").symlink_to(ZSHRC)
    result = zsh(f'sudo() {{ print -r -- "unexpected sudo"; }}; nosleep {args}')
    assert result.returncode == 0
    assert not result.stderr
    assert "nosleep" in result.stdout
    assert "--no-lock" in result.stdout
    assert "--forever" in result.stdout
    assert "unexpected sudo" not in result.stdout
    assert len(result.stdout.splitlines()) <= 24



def test_zsh_nosleep_hold_swaps_the_caffeinate(nosleep, tmp_path):
    # "display should stay on unless the lid is closed" (2026-09-25): nosleep runs its
    # caffeinate with -dims while the lid is open and swaps to -ims on a lid close, so
    # each swap must stop the previous hold and keep the new pid for the restore
    stub = tmp_path / "stubbin" / "caffeinate"
    stub.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CAF_LOG"\nexec sleep 30\n')
    stub.chmod(0o755)
    log = tmp_path / "caf.log"
    # Wait for each stub to start before replacing it. A fixed 0.3s delay races
    # process scheduling when the agent and both repositories run tests together.
    r = nosleep('setopt nomonitor; _NOSLEEP_CAF=""; _nosleep_hold -dims; a=$_NOSLEEP_CAF; '
                'for n in {1..50}; do [[ -s $CAF_LOG ]] && break; sleep 0.1; done; '
                '_nosleep_hold -ims; b=$_NOSLEEP_CAF; '
                'for n in {1..50}; do [[ $(<"$CAF_LOG") == *-ims* ]] && break; sleep 0.1; done; '
                'kill -0 $a 2>/dev/null && echo a-alive || echo a-gone; '
                'kill -0 $b 2>/dev/null && echo b-alive; kill $b', CAF_LOG=str(log))
    assert r.stdout.split() == ["a-gone", "b-alive"]
    assert log.read_text().splitlines() == ["-dims", "-ims"]
    body = open(ZSHRC).read().split("\nnosleep() {", 1)[1].split("\n}\n", 1)[0]
    assert "_nosleep_hold -dims" in body and "_nosleep_hold -ims; _nosleep_lock" in body
