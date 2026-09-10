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
import shutil
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
    assert pathlib.Path(env["TMUX_LOG"]).read_text().splitlines() == [
        "set-environment CLAUDE_RESUME_ID sid-9", "set-environment DEV_AGENT claude"]


def test_hook_codex_by_ancestry_records_rollout_and_origin_beside_it(sandbox, tmp_path):
    home, env = sandbox
    # the npm launcher case: the agent process is a native codex-<triple> under node
    (tmp_path / "ps.txt").write_text(f"{os.getpid()} 1 codex-aarch64-apple-darwin\n")
    roll = home / ".codex" / "sessions" / "2026" / "09" / "09"
    roll.mkdir(parents=True)
    tx = roll / "rollout-2026-09-09T10-00-00-thr_1.jsonl"
    tx.write_text("{}\n")
    r = run_hook(env, {"session_id": "thr_1", "cwd": str(home), "transcript_path": str(tx)},
                 TMUX="/tmp/tmux-1/default,1,0")
    assert r.returncode == 0, r.stderr
    reg = home / ".cache" / "claude-sessions"
    assert (reg / str(os.getpid())).read_text() == f"thr_1\t{home}\n"     # registry: same 2 fields
    assert (reg / "rollouts" / "thr_1").read_text() == f"{tx}\n"          # sid → rollout path
    assert (roll / "rollout-2026-09-09T10-00-00-thr_1.origin").exists()   # origin beside the rollout
    assert not (home / ".claude").exists()                                # no claude project dir touched
    assert pathlib.Path(env["TMUX_LOG"]).read_text().splitlines() == [
        "set-environment CLAUDE_RESUME_ID thr_1", "set-environment DEV_AGENT codex"]


def test_hook_agent_flag_overrides_the_ancestry_guess(sandbox):
    home, env = sandbox
    # ancestor reads as claude (the fixture) but the registration says codex: the
    # registration wins for the stamps; the registry entry still keys on the found pid
    r = subprocess.run([str(HOOK), "--agent", "codex"], input=json.dumps(
        {"session_id": "t2", "cwd": str(home)}), env={**env, "TMUX": "x"},
        capture_output=True, text=True)
    assert r.returncode == 0
    assert (home / ".cache" / "claude-sessions" / str(os.getpid())).exists()
    assert not (home / ".cache" / "claude-sessions" / "rollouts").exists()   # no transcript_path → nothing to record
    assert "set-environment DEV_AGENT codex" in pathlib.Path(env["TMUX_LOG"]).read_text()


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


# ─── the zsh seam: _dev_agent_* sourced from the real .zshrc ───────────────────

ZSHRC = REPO_ROOT / ".zshrc"

UUIDGEN_STUB = "#!/bin/bash\necho 0F0E0D0C-0B0A-0908-0706-050403020100\n"

TMUX_LOG_STUB = r"""#!/bin/bash
# log every call; answer the two reads the seam makes
printf '%s\n' "$*" >> "$TMUX_LOG"
case "$1" in
  show-environment)
    [[ -n "${FAKE_DEV_AGENT:-}" && "$4" == DEV_AGENT ]] && echo "DEV_AGENT=$FAKE_DEV_AGENT"
    [[ -n "${FAKE_RESUME_ID:-}" && "$4" == CLAUDE_RESUME_ID ]] && echo "CLAUDE_RESUME_ID=$FAKE_RESUME_ID"
    exit 0 ;;
  capture-pane)     [[ -n "${FAKE_PANE:-}" ]] && printf '%s\n' "$FAKE_PANE" ;;
  has-session)      [[ -n "${FAKE_HAS_SESSION:-}" ]] || exit 1 ;;
  list-panes|list-sessions) : ;;
esac
exit 0
"""


@pytest.fixture
def zsh(tmp_path):
    """zsh_call(snippet, **env) → CompletedProcess of `source .zshrc; <snippet>` under a
    sandbox HOME whose ~/.zshrc.local registers one repo (api) as a codex repo, with
    stub tmux / uuidgen / ps first on PATH."""
    if not shutil.which("zsh"):
        pytest.skip("zsh is not installed")
    home = tmp_path / "home"
    (home / ".cache").mkdir(parents=True)
    (home / ".zshrc.local").write_text(
        'DEV_REPOS[api]="$HOME/code/api"\nDEV_REPOS[web]="$HOME/code/web"\n'
        'DEV_AGENT[api]=codex\n')
    bins = tmp_path / "stubbin"
    bins.mkdir()
    for name, body in (("tmux", TMUX_LOG_STUB), ("uuidgen", UUIDGEN_STUB), ("ps", PS_STUB)):
        f = bins / name
        f.write_text(body)
        f.chmod(0o755)
    (tmp_path / "ps.txt").write_text("1 0 launchd\n")
    log = tmp_path / "tmux.log"

    def call(snippet, **extra):
        env = {"HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"),
               "PATH": f"{bins}:{os.environ.get('PATH', '')}", "TERM": "dumb",
               "TMUX_LOG": str(log), "FAKE_PS": str(tmp_path / "ps.txt"), **extra}
        return subprocess.run(["zsh", "-c", f"source {ZSHRC} >/dev/null 2>&1; {snippet}"],
                              env=env, capture_output=True, text=True, timeout=60)

    call.log = log
    call.home = home
    return call


def test_zsh_agent_for_precedence(zsh):
    assert zsh("_dev_agent_for api").stdout.strip() == "codex"          # DEV_AGENT[api]
    assert zsh("_dev_agent_for api claude").stdout.strip() == "claude"  # --claude wins
    assert zsh("_dev_agent_for web").stdout.strip() == "claude"         # the default
    assert zsh("_dev_agent_for web codex").stdout.strip() == "codex"    # --codex
    r = zsh("_dev_agent_for web gpt; echo rc=$?")
    assert "rc=1" in r.stdout and "unknown agent 'gpt'" in r.stderr and "~/.zshrc.local" in r.stderr
    # a typo in the local file is rejected too, never launched
    r = zsh("DEV_AGENT[web]=gpt5; _dev_agent_for web; echo rc=$?")
    assert "rc=1" in r.stdout


def test_zsh_agent_is_proc_and_of_comm(zsh):
    r = zsh("for c in claude /usr/local/bin/claude codex codex-aarch64-apple-darwin node zsh python3; do "
            "_dev_agent_is_proc $c && echo yes:$c || echo no:$c; done")
    assert r.stdout.split() == ["yes:claude", "yes:/usr/local/bin/claude", "yes:codex",
                                "yes:codex-aarch64-apple-darwin", "no:node", "no:zsh", "no:python3"]
    r = zsh("_dev_agent_of_comm codex-x86; _dev_agent_of_comm /x/claude; _dev_agent_of_comm node; echo end")
    assert r.stdout.split() == ["codex", "claude", "end"]


def test_zsh_agent_launch_lines(zsh):
    r = zsh("_dev_agent_new_cmd claude abc; _dev_agent_new_cmd codex abc; "
            "_dev_agent_resume_cmd claude abc; _dev_agent_resume_cmd codex abc")
    assert r.stdout.splitlines() == ["claude --session-id abc", "codex", "claude -r abc", "codex resume abc"]


def test_zsh_agent_check_points_at_t_install(zsh):
    r = zsh("_dev_agent_check definitely-not-a-binary; echo rc=$?")
    assert "rc=1" in r.stdout and "t install definitely-not-a-binary" in r.stderr
    assert zsh("_dev_agent_check zsh; echo rc=$?").stdout.strip() == "rc=0"


def test_zsh_agent_of_session_reads_the_stamp_when_no_process(zsh):
    # no live agent process in the (stub) process table → the DEV_AGENT tmux stamp,
    # and claude for an unstamped (pre-seam) slot
    assert zsh("_dev_agent_of_session dev-api-3", FAKE_DEV_AGENT="codex").stdout.strip() == "codex"
    assert zsh("_dev_agent_of_session dev-api-3", FAKE_DEV_AGENT="gpt").stdout.strip() == "claude"
    assert zsh("_dev_agent_of_session dev-api-3").stdout.strip() == "claude"


def test_zsh_agent_at_welcome(zsh):
    # claude: the Welcome back banner; codex: "no thread stamped yet" — codex mints its
    # thread (and fires the hook) at the first prompt, and its banner stays on screen
    # through a short exchange, so pane text would read a real conversation as idle
    assert zsh("_dev_agent_at_welcome claude s; echo rc=$?", FAKE_PANE="Welcome back!").stdout.strip() == "rc=0"
    assert zsh("_dev_agent_at_welcome claude s; echo rc=$?", FAKE_PANE="> fix the bug").stdout.strip() == "rc=1"
    assert zsh("_dev_agent_at_welcome codex s; echo rc=$?", FAKE_PANE="OpenAI Codex (v0.154.0)").stdout.strip() == "rc=0"
    assert zsh("_dev_agent_at_welcome codex s; echo rc=$?", FAKE_RESUME_ID="thr_1",
               FAKE_PANE="OpenAI Codex (v0.154.0)").stdout.strip() == "rc=1"


def test_zsh_new_session_stamps_the_agent_and_launch_line(zsh):
    r = zsh("_dev_new_session dev-api-3 $HOME/code/api dev/x 1 codex")
    assert r.returncode == 0, r.stderr
    log = zsh.log.read_text().splitlines()
    assert "set-environment -t dev-api-3 DEV_AGENT codex" in log
    assert not any("CLAUDE_RESUME_ID" in ln for ln in log)          # codex mints its own id
    assert "send-keys -t dev-api-3 codex; exit Enter" in log
    zsh.log.write_text("")
    r = zsh("_dev_new_session dev-api-4 $HOME/code/api dev/x 1 claude")
    log = zsh.log.read_text().splitlines()
    assert "set-environment -t dev-api-4 DEV_AGENT claude" in log
    assert "set-environment -t dev-api-4 CLAUDE_RESUME_ID 0f0e0d0c-0b0a-0908-0706-050403020100" in log
    assert "send-keys -t dev-api-4 claude --session-id 0f0e0d0c-0b0a-0908-0706-050403020100; exit Enter" in log
    zsh.log.write_text("")
    # the default (4 args, every pre-seam caller) is claude
    zsh("_dev_new_session dev-api-5 $HOME/code/api dev/x 1")
    assert "set-environment -t dev-api-5 DEV_AGENT claude" in zsh.log.read_text()


def test_zsh_resume_session_uses_the_agent_resume_line(zsh):
    zsh("_dev_resume_session dev-api-7 $HOME/code/api thr_9 codex")
    log = zsh.log.read_text().splitlines()
    assert "set-environment -t dev-api-7 CLAUDE_RESUME_ID thr_9" in log
    assert "set-environment -t dev-api-7 DEV_AGENT codex" in log
    assert "send-keys -t dev-api-7 codex resume thr_9; exit Enter" in log
    zsh.log.write_text("")
    zsh("_dev_resume_session dev-api-8 $HOME/code/api sid-1")
    assert "send-keys -t dev-api-8 claude -r sid-1; exit Enter" in zsh.log.read_text()


def test_zsh_sync_config_emits_the_agent_keys(zsh):
    r = zsh("cat $HOME/.config/t/config.sh")
    lines = r.stdout.splitlines()
    assert "DEV_AGENT[api]=codex" in lines
    assert "DEV_AGENT_DEFAULT=claude" in lines


# ─── codex conversations: the thread store, the locators, titles, self-id, wrappers ──

FIXTURE_ROLLOUT = REPO_ROOT / "tests" / "fixtures" / "codex_rollout.jsonl"
SID = "01a089c2-53cf-7a41-bd9f-ae70d07c2de9"

THREADS_DDL = """
CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL, source TEXT NOT NULL, model_provider TEXT NOT NULL, cwd TEXT NOT NULL,
  title TEXT NOT NULL, sandbox_policy TEXT NOT NULL, approval_mode TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0, first_user_message TEXT NOT NULL DEFAULT '', name TEXT);
"""


def _codex_home(zsh, threads):
    """Materialise ~/.codex: a rollout per thread under sessions/YYYY/MM/DD plus a
    state_5.sqlite built from the real `threads` DDL. threads: [(sid, cwd, title,
    updated_at, archived, name)]. Returns {sid: rollout path}."""
    import sqlite3
    home = zsh.home
    day = home / ".codex" / "sessions" / "2026" / "09" / "09"
    day.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(home / ".codex" / "state_5.sqlite"))
    db.executescript(THREADS_DDL)
    paths = {}
    for sid, cwd, title, upd, archived, name in threads:
        p = day / f"rollout-2026-09-09T22-20-09-{sid}.jsonl"
        p.write_text(FIXTURE_ROLLOUT.read_text().replace(SID, sid))
        paths[sid] = p
        db.execute("insert into threads values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, str(p), upd, upd, "cli", "openai", cwd, title, "ws", "on-request",
                    archived, title, name))
    db.commit()
    db.close()
    return paths


def test_zsh_codex_threads_for_cwd_orders_and_filters(zsh):
    wt = f"{zsh.home}/code/.worktrees/api/3"
    paths = _codex_home(zsh, [
        ("aaaaaaaa-0000-0000-0000-000000000001", wt, "older", 100, 0, None),
        ("aaaaaaaa-0000-0000-0000-000000000002", wt, "newer", 200, 0, "renamed"),
        ("aaaaaaaa-0000-0000-0000-000000000003", wt, "archived", 300, 1, None),
        ("aaaaaaaa-0000-0000-0000-000000000004", f"{zsh.home}/elsewhere", "other", 400, 0, None),
    ])
    r = zsh(f"_codex_threads_for_cwd {wt}")
    rows = [ln.split("\t") for ln in r.stdout.splitlines()]
    assert [x[0][-1] for x in rows] == ["2", "1"]           # newest first, archived + other cwd out
    assert rows[0][3] == "renamed"                            # /rename wins over the title
    assert rows[0][1] == str(paths["aaaaaaaa-0000-0000-0000-000000000002"])
    assert zsh("_codex_thread_lookup aaaaaaaa-0000-0000-0000-000000000004").stdout.count("\n") == 1
    # every transcript for the cwd, newest first, as paths
    r = zsh(f"_dev_agent_transcripts_for_cwd codex {wt}")
    assert r.stdout.splitlines() == [str(paths["aaaaaaaa-0000-0000-0000-000000000002"]),
                                     str(paths["aaaaaaaa-0000-0000-0000-000000000001"])]
    assert zsh(f"_dev_agent_newest_sid codex {wt}").stdout.strip() == "aaaaaaaa-0000-0000-0000-000000000002"


def test_zsh_codex_threads_missing_db_is_silent(zsh):
    r = zsh("_codex_threads_for_cwd /nowhere; echo rc=$?")
    assert r.stdout.strip() == "rc=0" and r.stderr == ""
    (zsh.home / ".codex").mkdir()
    (zsh.home / ".codex" / "state_5.sqlite").write_text("not a database")
    r = zsh("_codex_threads_for_cwd /nowhere; echo rc=$?")
    assert r.stdout.strip() == "rc=0" and r.stderr == ""


def test_zsh_transcript_sid_and_agent(zsh):
    r = zsh(f"_dev_transcript_sid /x/rollout-2026-09-09T22-20-09-{SID}.jsonl; "
            "_dev_transcript_sid /x/abc-123.jsonl; "
            f"_dev_transcript_agent /x/rollout-2026-09-09T22-20-09-{SID}.jsonl; _dev_transcript_agent /x/abc-123.jsonl")
    assert r.stdout.splitlines() == [SID, "abc-123", "codex", "claude"]


def test_zsh_agent_transcript_locator_ladder(zsh):
    wt = f"{zsh.home}/code/.worktrees/api/3"
    paths = _codex_home(zsh, [(SID, wt, "t", 100, 0, None)])
    real = str(paths[SID])
    # 1. the hook's rollouts/<sid> cache wins
    cache = zsh.home / ".cache" / "claude-sessions" / "rollouts"
    cache.mkdir(parents=True)
    (cache / SID).write_text(real + "\n")
    assert zsh(f"_dev_agent_transcript codex {SID}").stdout.strip() == real
    # 2. a stale cache entry (file gone) falls through to sqlite
    (cache / SID).write_text("/gone.jsonl\n")
    assert zsh(f"_dev_agent_transcript codex {SID}").stdout.strip() == real
    # 3. no cache, no row → the date-tree glob (a synced-in rollout)
    (cache / SID).unlink()
    (zsh.home / ".codex" / "state_5.sqlite").unlink()
    assert zsh(f"_dev_agent_transcript codex {SID}").stdout.strip() == real
    # unknown id → rc 1, nothing printed
    r = zsh("_dev_agent_transcript codex ffffffff-0000-0000-0000-000000000000; echo rc=$?")
    assert r.stdout.strip() == "rc=1"
    # claude: cwd-keyed project dir, or any project dir without a cwd
    proj = zsh.home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", wt)
    proj.mkdir(parents=True)
    (proj / "sid-1.jsonl").write_text("{}\n")
    assert zsh(f"_dev_agent_transcript claude sid-1 {wt}").stdout.strip() == str(proj / "sid-1.jsonl")
    assert zsh("_dev_agent_transcript claude sid-1").stdout.strip() == str(proj / "sid-1.jsonl")
    assert zsh(f"_dev_agent_transcript claude nope {wt}; echo rc=$?").stdout.strip() == "rc=1"
    assert zsh("_dev_agent_transcript claude -; echo rc=$?").stdout.strip() == "rc=1"


def test_zsh_meta_batch_titles_a_codex_rollout(zsh):
    wt = f"{zsh.home}/code/.worktrees/api/3"
    paths = _codex_home(zsh, [(SID, wt, "t", 100, 0, None)])
    r = zsh(f"_transcript_meta_batch {paths[SID]}")
    path, title, pr = r.stdout.rstrip("\n").split("\t")
    assert path == str(paths[SID])
    assert title == "Reply with exactly the word OK and nothing else."   # the <recommended_plugins> notice is skipped
    assert pr == "github.com/acme/api/pull/42"
    # cached: a second call answers from meta/ without re-reading (same output)
    assert zsh(f"_transcript_title {paths[SID]}").stdout.strip() == title
    # a claude transcript in the same batch still parses as claude
    proj = zsh.home / ".claude" / "projects" / "x"
    proj.mkdir(parents=True)
    (proj / "c1.jsonl").write_text('{"type":"user","message":{"content":"fix the login bug"}}\n')
    r = zsh(f"_transcript_meta_batch {proj / 'c1.jsonl'} {paths[SID]}")
    assert [ln.split("\t")[1] for ln in r.stdout.splitlines()] == ["fix the login bug", title]


def test_zsh_self_sid_and_agent(zsh, tmp_path):
    # a plain shell: nothing
    assert zsh("_dev_self_sid; echo rc=$?").stdout.strip() == "rc=1"
    # inside claude: the env var
    r = zsh("_dev_self_sid; _dev_self_agent", CLAUDE_CODE_SESSION_ID="cs-1")
    assert r.stdout.splitlines() == ["cs-1", "claude"]
    # inside codex: the registry entry keyed on the codex pid above this shell —
    # the fake process table puts a codex under launchd and makes it OUR ancestor
    # by claiming the zsh's own pid (the harness runs zsh -c, so $$ is that zsh)
    reg = zsh.home / ".cache" / "claude-sessions"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "4242").write_text(f"{SID}\t{zsh.home}\n")
    (tmp_path / "ps.txt").write_text("4242 1 codex\n")
    r = zsh("pid=$$; print -r -- \"$pid 4242 zsh\" >> $FAKE_PS; _dev_self_sid; _dev_self_agent")
    assert r.stdout.splitlines() == [SID, "codex"]


def test_zsh_agent_wrap_spawns_the_recorded_agent(zsh, tmp_path):
    # a stub `codex` that writes a SPAWN instruction into the sentinel, the way
    # `t push` does from inside a session; the wrapper must resume it as codex
    stub = tmp_path / "stubbin" / "codex"
    stub.write_text('#!/bin/bash\nprintf "SPAWN\\tdev-api-3\\t%s\\t%s\\tcodex\\n" "$HOME/code/.worktrees/api/3" "thr_7" > "$CLAUDE_TPUSH_ATTACH"\n')
    stub.chmod(0o755)
    r = zsh("codex; echo rc=$?")
    log = zsh.log.read_text().splitlines()
    assert "send-keys -t dev-api-3 codex resume thr_7; exit Enter" in log
    assert "set-environment -t dev-api-3 DEV_AGENT codex" in log
    # a payload WITHOUT the agent field (an older t push) resumes as claude
    stub.write_text('#!/bin/bash\nprintf "SPAWN\\tdev-api-4\\t%s\\t%s\\n" "$HOME/code/.worktrees/api/4" "sid-9" > "$CLAUDE_TPUSH_ATTACH"\n')
    zsh.log.write_text("")
    zsh("codex")
    log = zsh.log.read_text().splitlines()
    assert "send-keys -t dev-api-4 claude -r sid-9; exit Enter" in log


def test_zsh_repo_slots_sees_codex_only_slots(zsh):
    # a slot whose only trace is a codex thread recorded in its (swept) worktree path
    wt_root = f"{zsh.home}/code/.worktrees/api"
    _codex_home(zsh, [("aaaaaaaa-0000-0000-0000-000000000007", f"{wt_root}/7", "t", 100, 0, None),
                      ("aaaaaaaa-0000-0000-0000-000000000008", f"{wt_root}/12/sub", "t", 100, 0, None),
                      ("aaaaaaaa-0000-0000-0000-000000000009", f"{zsh.home}/code/api", "t", 100, 0, None)])
    (zsh.home / "code" / "api").mkdir(parents=True)
    assert zsh("_dev_repo_slots api").stdout.split() == ["7", "12"]
