"""Desktop handoff: exact conversation, URL encoding, and stop-before-open failures."""

import io
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest


SID = "01234567-89ab-cdef-0123-456789abcdef"


@pytest.fixture
def app_slot(t_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(t_mod, "CONFIG", str(tmp_path / "no-config"))
    monkeypatch.setattr(t_mod, "_cache_root", lambda: str(tmp_path / "cache"))
    cfg = t_mod.Config()
    cfg.repos = {"api": str(tmp_path / "my-api"), "a": str(tmp_path / "my-api")}
    cfg.worktree_root = str(tmp_path / "worktrees")
    cwd = tmp_path / "worktrees" / "my-api" / "13"
    cwd.mkdir(parents=True)
    row = dict(host="local", sid=SID, cwd=str(cwd), slot="api-13",
               state="detached", context="active", agent="codex", summary="Fix settings")
    return cfg, row


@pytest.mark.parametrize("repo,slot", [("api", "13"), ("a", "13"), ("13", None), (None, None)])
def test_app_select_exact_slot(t_mod, app_slot, repo, slot):
    cfg, row = app_slot
    rows = [dict(row, slot="api-2"), row, dict(row, host="mini")]
    assert t_mod._app_select(cfg, rows, repo, slot, row["cwd"]) is row


def test_app_select_only_slot_from_canonical_repo(t_mod, app_slot):
    cfg, row = app_slot
    assert t_mod._app_select(cfg, [row], None, None, cfg.repos["api"]) is row
    assert t_mod._app_select(cfg, [row], "api", None, "/unrelated") is row
    # A stopped CLI's stamped slot can reopen the same thread in the app.
    stopped = dict(row, context="none")
    assert t_mod._app_select(cfg, [stopped], "api", "13", "/") is stopped


@pytest.mark.parametrize("repo,slot,message", [
    (None, None, "registered repo"), ("typo", "13", "registered repo"),
    ("api", "fg", "number"), ("api", "99", "no local slot"),
    ("api", None, "choose a slot"),
])
def test_app_select_refuses_guessing(t_mod, app_slot, repo, slot, message):
    cfg, row = app_slot
    with pytest.raises(ValueError, match=message):
        t_mod._app_select(cfg, [row, dict(row, slot="api-2")], repo, slot, "/elsewhere")


@pytest.mark.parametrize("changes,message", [
    ({"host": "mini"}, "no local slot"), ({"slot": "api:01234567"}, "no local slot"),
    ({"agent": "claude"}, "Codex conversations only"),
    ({"sid": "-"}, "no recorded"), ({"sid": "new?prompt=oops"}, "no recorded"),
    ({"context": "idle"}, "no recorded"),
])
def test_app_select_rejects_incompatible_rows(t_mod, app_slot, changes, message):
    cfg, row = app_slot
    with pytest.raises(ValueError, match=message):
        t_mod._app_select(cfg, [dict(row, **changes)], "api", "13", "/")


def test_app_link_roundtrips_route_and_query(t_mod):
    url = "http://localhost:5213/settings?q=a%20b&next=%2Fhome#chart"
    link = urlsplit(t_mod._app_link(SID, url))
    assert (link.scheme, link.netloc, link.path) == ("codex", "threads", "/" + SID)
    assert parse_qs(link.query) == {"browserUrl": [url], "browserTabId": ["t-preview-" + SID]}
    assert t_mod._app_link(SID) == "codex://threads/" + SID
    assert "https%3A" in t_mod._app_link(SID, "https://example.com")


@pytest.mark.parametrize("url", [
    "", "localhost:5213", "file:///tmp/site.html", "javascript:alert(1)",
    "https://user:secret@example.com", "https:///path", " https://example.com",
    "http://localhost/a b", "http://localhost/a\nb", "http://localhost:bad",
    "http://localhost:99999", "http://[broken", "http://@localhost",
])
def test_app_link_rejects_invalid_urls(t_mod, url):
    with pytest.raises(ValueError, match="--url must"):
        t_mod._app_link(SID, url)


def test_app_bundle_checks_identity_and_both_names(t_mod, monkeypatch):
    def fake_open(path, mode):
        assert mode == "rb"
        if path == "/Applications/ChatGPT.app/Contents/Info.plist":
            return io.BytesIO(plistlib.dumps({"CFBundleIdentifier": "other.app"}))
        if path == "/Applications/Codex.app/Contents/Info.plist":
            return io.BytesIO(b"broken plist")
        if path.endswith("/Applications/Codex.app/Contents/Info.plist"):
            return io.BytesIO(plistlib.dumps({"CFBundleIdentifier": "com.openai.codex"}))
        raise FileNotFoundError(path)
    monkeypatch.setattr(t_mod, "open", fake_open, raising=False)
    assert t_mod._app_bundle() == t_mod.HOME + "/Applications/Codex.app"
    monkeypatch.setattr(t_mod.plistlib, "load", lambda f: {})
    assert t_mod._app_bundle() is None


@pytest.fixture
def app_command(t_mod, app_slot, monkeypatch):
    cfg, row = app_slot
    text = "\t".join(row[k] for k in ("sid", "cwd", "slot", "state", "context", "summary", "agent"))
    monkeypatch.setattr(t_mod, "zsh_capture", lambda snippet: text)
    monkeypatch.setattr(t_mod.sys, "platform", "darwin")
    monkeypatch.setattr(t_mod, "_app_bundle", lambda: "/Applications/ChatGPT.app")
    class Window:
        def focus(self):
            pass
    monkeypatch.setattr(t_mod, "_app_new_window", lambda bundle: Window())
    monkeypatch.setattr(t_mod, "_dev_url", lambda key, cwd: "http://localhost:5213")
    events = []
    def stop(selected):
        events.append(("stop", selected))
        return subprocess.CompletedProcess([], 0, "", "")
    def run(argv, **kw):
        events.append(("open", argv))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(t_mod, "_app_stop_cli", stop)
    monkeypatch.setattr(t_mod, "_run", run)
    def call(*flags):
        args = t_mod.build_parser().parse_args(["app", "api", "13", *flags])
        return t_mod.cmd_app(cfg, args)
    return call, events, row


def test_app_command_stops_before_opening_same_thread(t_mod, app_command):
    call, events, row = app_command
    assert call() == 0
    assert events == [("stop", row), ("open", ["open", "-a", "/Applications/ChatGPT.app",
                        t_mod._app_link(SID, "http://localhost:5213")])]


def test_app_opens_referenced_plan_with_existing_thread_and_preview(t_mod, app_command, tmp_path, monkeypatch):
    call, events, row = app_command
    plan = tmp_path / "launch plan.md"
    plan.write_text("# Launch plan\nKeep the original.\n")
    monkeypatch.setattr(t_mod, "_app_find_plan", lambda selected: str(plan), raising=False)
    monkeypatch.setattr(t_mod, "_app_plan_start", lambda path: "http://127.0.0.1:12345/secret/", raising=False)
    assert call() == 0
    assert events[-1][1][:3] == ["open", "-a", "/Applications/ChatGPT.app"]
    links = events[-1][1][3:]
    assert len(links) == 2
    assert all(urlsplit(link).path == "/" + SID for link in links)
    assert parse_qs(urlsplit(links[0]).query)["browserUrl"] == ["http://localhost:5213"]
    assert parse_qs(urlsplit(links[1]).query)["browserUrl"] == ["http://127.0.0.1:12345/secret/"]
    assert parse_qs(urlsplit(links[0]).query)["browserTabId"] != parse_qs(urlsplit(links[1]).query)["browserTabId"]
    assert t_mod._app_preview_url(row) == "http://localhost:5213"


def test_app_command_dry_run_is_read_only(app_command, capsys):
    call, events, _ = app_command
    assert call("--dry-run") == 0
    assert events == []
    assert "a new app window" in capsys.readouterr().out


def test_app_creates_a_window_automatically_when_codex_is_running(t_mod, app_command, monkeypatch):
    call, events, row = app_command
    class Window:
        def focus(self):
            pass
    def new_window(bundle):
        events.append(("new-window", bundle))
        return Window()
    monkeypatch.setattr(t_mod, "_app_new_window", new_window, raising=False)
    assert call() == 0
    assert [event[0] for event in events] == ["new-window", "stop", "open"]


@pytest.mark.parametrize("with_plan", [False, True])
def test_app_failed_window_creation_does_not_stop_cli(t_mod, app_command, monkeypatch, capsys, tmp_path, with_plan):
    call, events, row = app_command
    def fail(bundle):
        raise ValueError("could not create a new window")
    monkeypatch.setattr(t_mod, "_app_new_window", fail)
    if with_plan:
        plan = tmp_path / "plan.md"
        plan.write_text("# Plan")
        monkeypatch.setattr(t_mod, "_app_find_plan", lambda row: str(plan))
        monkeypatch.setattr(t_mod, "_app_plan_start", lambda path: pytest.fail("must not start a plan server"))
    assert call() == 1
    assert events == [], "an unsupported launch must not stop the CLI or navigate another window"
    output = capsys.readouterr()
    assert "new window" in output.err
    assert "Sent to the desktop app" not in output.out
    assert not os.path.exists(t_mod._app_preview_path(row))


def test_app_os_acceptance_is_not_reported_as_delivery(app_command, capsys):
    call, _, _ = app_command
    assert call("--reuse-window") == 0
    output = capsys.readouterr().out
    assert "Open request sent" in output
    assert "Sent to the desktop app" not in output


def test_app_lost_window_after_cli_stop_does_not_navigate_elsewhere(t_mod, app_command, monkeypatch, capsys):
    call, events, _ = app_command
    class ClosedWindow:
        def focus(self):
            raise ValueError("the new window closed")
    monkeypatch.setattr(t_mod, "_app_new_window", lambda bundle: ClosedWindow())
    assert call() == 1
    assert [event[0] for event in events] == ["stop"]
    assert "codex resume " + SID in capsys.readouterr().err


def test_app_native_helper_uses_the_installed_repo(t_mod, monkeypatch):
    def load(path):
        assert path.endswith("/libexec/t_app_window.py") and os.path.isfile(path)
        return {"new_window": lambda bundle, run: (bundle, run)}
    monkeypatch.setattr(t_mod.runpy, "run_path", load)
    assert t_mod._app_new_window("test.app") == ("test.app", t_mod._run)


def test_app_missing_native_helper_is_an_actionable_error(t_mod, monkeypatch):
    def load(path):
        raise FileNotFoundError(path)
    monkeypatch.setattr(t_mod.runpy, "run_path", load)
    with pytest.raises(ValueError, match="native window helper"):
        t_mod._app_new_window("test.app")


@pytest.mark.parametrize("preview", [(), ("--no-preview", "--no-plan")])
def test_app_reuse_window_is_explicit(t_mod, app_command, preview, capsys, monkeypatch):
    call, events, row = app_command
    monkeypatch.setattr(t_mod, "_app_new_window", lambda bundle: pytest.fail("reuse must not create a window"))
    assert call("--reuse-window", "--dry-run", *preview) == 0
    assert events == []
    assert "open in the existing app window" in capsys.readouterr().out
    assert call("--reuse-window", *preview) == 0
    url = None if preview else "http://localhost:5213"
    assert events == [("stop", row), ("open", ["open", "-a", "/Applications/ChatGPT.app",
                       t_mod._app_link(SID, url)])]


def test_app_command_explicit_url_and_no_preview(t_mod, app_command):
    call, events, _ = app_command
    assert call("--url", "http://localhost:9000/a?x=1&y=2") == 0
    assert events[-1][1][-1] == t_mod._app_link(SID, "http://localhost:9000/a?x=1&y=2")
    events.clear()
    assert call("--no-preview") == 0
    assert events[-1][1][-1] == t_mod._app_link(SID)
    with pytest.raises(SystemExit):
        call("--url", "http://localhost:9000", "--no-preview")


def test_app_command_remembers_selected_page(t_mod, app_command):
    call, events, _ = app_command
    budget = "http://localhost:5213/#budget"
    assert call("--url", budget) == 0
    events.clear()
    assert call() == 0
    assert events[-1][1][-1] == t_mod._app_link(SID, budget)


@pytest.mark.parametrize("flags", [("--no-preview",), ("--url", "http://localhost:5213/#home", "--dry-run")])
def test_app_command_does_not_forget_page_when_skipping_preview(t_mod, app_command, flags):
    call, events, _ = app_command
    budget = "http://localhost:5213/#budget?month=2026-09"
    assert call("--url", budget) == 0
    assert call(*flags) == 0
    events.clear()
    assert call() == 0
    assert events[-1][1][-1] == t_mod._app_link(SID, budget)


@pytest.mark.parametrize("saved", ["{", "[]", '{"cwd":"/other","url":"http://localhost:5299/#budget"}',
                                   '{"url":"http://localhost:5213/#budget"}', "INVALID_URL", "MISSING_URL"])
def test_app_preview_ignores_unusable_saved_page(t_mod, app_slot, saved):
    _, row = app_slot
    if saved in ("INVALID_URL", "MISSING_URL"):
        saved = json.dumps({"cwd": row["cwd"], "url": "javascript:bad" if saved == "INVALID_URL" else None})
    path = t_mod._app_preview_path(row)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(saved)
    # This fixture has no dev server: an unusable cache must not create a URL.
    assert t_mod._app_preview_url(row) is None


def test_app_preview_stays_with_its_thread_and_worktree(t_mod, app_slot):
    _, row = app_slot
    budget = "http://localhost:5213/#budget"
    t_mod._app_remember_preview(row, budget)
    assert t_mod._app_preview_url(row) == budget
    assert t_mod._app_preview_url(dict(row, sid="different-thread")) is None
    assert t_mod._app_preview_url(dict(row, cwd="/different/worktree")) is None


def test_app_command_missing_preview_still_opens_thread(t_mod, app_command, monkeypatch, capsys):
    call, events, _ = app_command
    monkeypatch.setattr(t_mod, "_dev_url", lambda *a: None)
    assert call() == 0
    assert events[-1][1][-1] == t_mod._app_link(SID)
    assert "No running preview" in capsys.readouterr().out


@pytest.mark.parametrize("problem", ["platform", "app", "worktree", "url"])
def test_app_command_preflight_does_not_stop_cli(t_mod, app_command, monkeypatch, problem):
    call, events, row = app_command
    if problem == "platform":
        monkeypatch.setattr(t_mod.sys, "platform", "linux")
    elif problem == "app":
        monkeypatch.setattr(t_mod, "_app_bundle", lambda: None)
    elif problem == "worktree":
        shutil.rmtree(row["cwd"])
    assert call(*(["--url", "file:///tmp/page"] if problem == "url" else [])) == 1
    assert events == []


def test_app_command_failed_stop_does_not_open(t_mod, app_command, monkeypatch, capsys):
    call, events, _ = app_command
    monkeypatch.setattr(t_mod, "_app_stop_cli", lambda row:
                        subprocess.CompletedProcess([], 1, "", "still running"))
    assert call() == 1
    assert events == []
    assert "still running" in capsys.readouterr().err


def test_app_command_failed_launch_gives_recovery(t_mod, app_command, monkeypatch, capsys):
    call, events, row = app_command
    budget = "http://localhost:5213/#budget"
    t_mod._app_remember_preview(row, budget)
    monkeypatch.setattr(t_mod, "_run", lambda *a, **kw:
                        subprocess.CompletedProcess([], 1, "", "launch failed"))
    assert call("--url", "http://localhost:5213/#home") == 1
    assert [e[0] for e in events] == ["stop"]
    assert "codex resume " + SID in capsys.readouterr().err
    assert t_mod._app_preview_url(row) == budget


@pytest.mark.parametrize("scenario,ok", [
    ("normal", True), ("already_stopped", True), ("changed_sid", False),
    ("changed_cwd", False), ("changed_agent", False), ("missing", False),
    ("self", False), ("term_failed", False), ("still_running", False), ("stamp_failed", False),
])
def test_app_stop_shell_revalidates_and_waits(t_mod, app_slot, monkeypatch, tmp_path, scenario, ok):
    """Run the actual handoff shell with stub helpers/signals; never signal a real process."""
    if not shutil.which("zsh"):
        pytest.skip("zsh required")
    _, row = app_slot
    log = tmp_path / "signals"
    prelude = f'''
scenario={shlex.quote(scenario)}
tmux() {{
  [[ $scenario == missing ]] && return 1
  [[ $scenario == stamp_failed && $1 == set-environment ]] && return 1
  [[ $1 == display-message ]] && {{
    [[ $scenario == changed_cwd ]] && print /other || print -r -- {shlex.quote(row['cwd'])}
  }}
  return 0
}}
_dev_agent_of_session() {{ [[ $scenario == changed_agent ]] && print claude || print codex; }}
_dev_session_sid() {{ [[ $scenario == changed_sid ]] && print other || print {SID}; }}
_dev_session_claude_pid() {{
  [[ $scenario == already_stopped ]] && return 1
  [[ $scenario == self ]] && print $PPID || print 99999
}}
ps() {{ print 1; }}
kill() {{
  print -r -- "$*" >> {shlex.quote(str(log))}
  [[ $1 == -TERM ]] && {{ [[ $scenario != term_failed ]]; return; }}
  [[ $scenario == still_running ]]
}}
sleep() {{ :; }}
'''
    def run(argv, **kwargs):
        return subprocess.run(["zsh", "-f", "-c", prelude + argv[-1]], capture_output=True, text=True)
    monkeypatch.setattr(t_mod, "_run", run)
    result = t_mod._app_stop_cli(row)
    assert (result.returncode == 0) is ok, result.stderr
    signals = log.read_text().splitlines() if log.exists() else []
    if scenario in ("normal", "term_failed", "still_running"):
        assert signals[0] == "-TERM 99999"
        assert all(s == "-0 99999" for s in signals[1:])
    else:
        assert signals == []


@pytest.mark.parametrize("stop_process", [False, True], ids=["stopped", "pane-process"])
def test_app_stop_revalidates_directory_with_real_tmux(t_mod, app_slot, monkeypatch, tmp_path, stop_process):
    """Regression: =session is a session target, but display-message needs =session:.

    The old mocked tmux always returned the cwd and concealed this failure. Keep
    tmux real here, including an active decoy session in a different directory;
    only the Codex probes are stubbed. The pane-process case stops only this
    test's disposable sleep process and verifies that its slot remains reserved.
    """
    if not shutil.which("tmux") or not shutil.which("zsh"):
        pytest.skip("tmux and zsh required")
    _, row = app_slot
    socket = "t-app-" + uuid.uuid4().hex
    tmux = [shutil.which("tmux"), "-L", socket, "-f", os.devnull]
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TERM": "xterm-256color"}
    session = "dev-" + row["slot"]
    prelude = f'''
tmux() {{ command {shlex.join(tmux)} "$@"; }}
_dev_agent_of_session() {{ print codex; }}
_dev_session_sid() {{
  [[ $1 == {shlex.quote(session)} && $2 == {shlex.quote(row['cwd'])} ]] && print {SID}
}}
_dev_session_claude_pid() {{ return 1; }}
kill() {{ print -u2 'unexpected signal'; return 1; }}
'''
    if stop_process:
        prelude += f'''
unfunction kill
_dev_session_claude_pid() {{ tmux display-message -p -t "=$session:" '#{{pane_pid}}'; }}
'''
    def run(argv, **kwargs):
        return subprocess.run(["zsh", "-f", "-c", prelude + argv[-1]],
                              env=env, capture_output=True, text=True, timeout=10)
    monkeypatch.setattr(t_mod, "_run", run)
    try:
        subprocess.run(tmux + ["new-session", "-d", "-s", session, "-c", row["cwd"], "sleep 60"],
                       env=env, capture_output=True, text=True, check=True)
        subprocess.run(tmux + ["new-session", "-d", "-s", session + "0", "-c", str(tmp_path), "sleep 60"],
                       env=env, capture_output=True, text=True, check=True)
        result = t_mod._app_stop_cli(row)
        assert result.returncode == 0, result.stderr
        expected = row["cwd"] + ("|1" if stop_process else "|0")
        deadline = time.monotonic() + 2
        while True:
            state = subprocess.run(tmux + ["display-message", "-p", "-t", "=" + session + ":",
                                          "#{session_path}|#{pane_dead}"],
                                   env=env, capture_output=True, text=True, check=True)
            if state.stdout.strip() == expected or time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert state.stdout.strip() == expected, result.stderr
        if stop_process:
            stamp = subprocess.run(tmux + ["show-environment", "-t", session, "CLAUDE_RESUME_ID"],
                                   env=env, capture_output=True, text=True, check=True)
            assert stamp.stdout.strip() == "CLAUDE_RESUME_ID=" + SID
    finally:
        subprocess.run(tmux + ["kill-server"], env=env, capture_output=True)


def test_app_discovers_only_selected_conversation_messages(t_mod, app_slot, tmp_path, monkeypatch):
    _, row = app_slot
    monkeypatch.setattr(t_mod, "HOME", str(tmp_path))
    plans = tmp_path / '.claude' / 'plans'
    plans.mkdir(parents=True)
    first, latest, unrelated = [plans / name for name in ('old.md', 'launch plan.md', 'unrelated.md')]
    for plan in (first, latest, unrelated):
        plan.write_text('# Plan')
    def message(path, role='assistant'):
        return dict(type='response_item', payload=dict(type='message', role=role,
                    content=[{'type': 'output_text', 'text': f'Refreshed [plan](<{path}>).'}]))
    transcript = tmp_path / 'rollout.jsonl'
    records = [message(first), message(latest, 'user'),
               message(unrelated, 'system'), {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': str(unrelated)}},
               message(plans / 'missing.md'), [], {'type': 'response_item', 'payload': None},
               {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': None}},
               {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [None, {'text': None}]}}]
    transcript.write_text('\n'.join(json.dumps(rec) for rec in records) + '\n{partial')
    calls = []
    monkeypatch.setattr(t_mod, 'zsh_capture', lambda command: calls.append(command) or str(transcript))
    assert t_mod._app_find_plan(row) == str(latest)
    assert shlex.split(calls[0]) == ['_dev_agent_transcript', 'codex', SID, row['cwd']]
    latest.unlink()
    assert t_mod._app_find_plan(row) == str(first)


def test_app_explicit_plan_is_remembered_and_can_be_skipped(t_mod, app_command, tmp_path, monkeypatch, capsys):
    call, events, row = app_command
    plan = tmp_path / 'specific plan.md'
    plan.write_text('# Specific')
    starts = []
    monkeypatch.setattr(t_mod, '_app_plan_start', lambda path: starts.append(path) or 'http://127.0.0.1:12345/token/')
    assert call('--plan', str(plan), '--dry-run') == 0
    assert str(plan) in capsys.readouterr().out
    assert events == starts == []
    assert not os.path.exists(t_mod._app_plan_cache(row))
    assert call('--plan', str(plan), '--no-preview') == 0
    assert parse_qs(urlsplit(events[-1][1][-1]).query)['browserTabId'] == ['t-plan-' + SID]
    assert starts == [str(plan)]
    assert t_mod._app_find_plan(row) == str(plan)
    assert t_mod._app_find_plan(dict(row, cwd='/another/worktree')) is None
    assert call('--no-plan', '--no-preview') == 0
    assert events[-1][1][-1] == t_mod._app_link(SID)
    assert starts == [str(plan)]
    plan.unlink()
    assert t_mod._app_find_plan(row) is None


@pytest.mark.parametrize('content', ['[]', '{broken', '{"cwd": null}', '{"url":"https://example.com"}'])
def test_app_ignores_invalid_plan_cache(t_mod, app_slot, monkeypatch, content):
    _, row = app_slot
    monkeypatch.setattr(t_mod, 'zsh_capture', lambda command: '')
    path = t_mod._app_plan_cache(row)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as fh:
        fh.write(content)
    assert t_mod._app_find_plan(row) is None


def test_app_plan_failure_before_stopping(t_mod, app_command, tmp_path, monkeypatch, capsys):
    call, events, row = app_command
    assert call('--plan', str(tmp_path / 'missing.md')) == 1
    assert 'existing Markdown' in capsys.readouterr().err
    assert not events
    plan = tmp_path / 'unreadable.md'
    plan.write_bytes(b'\xff')
    assert call('--plan', str(plan)) == 1
    assert 'cannot read plan' in capsys.readouterr().err
    assert not events
    plan.write_text('# Plan')
    def fail(path):
        raise OSError('no listener')
    monkeypatch.setattr(t_mod, '_app_plan_start', fail)
    assert call('--plan', str(plan)) == 1
    assert 'no listener' in capsys.readouterr().err
    assert not events
    assert not os.path.exists(t_mod._app_plan_cache(row))


def test_app_plan_preview_only_serves_selected_file_and_refreshes(t_mod, tmp_path):
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    plan = tmp_path / 'plan.md'
    plan.write_text('# Launch <script>bad()</script>\n\n## Second\n- Keep original\n```js\nalert("x")\n```\n~~~\nunclosed')
    server = t_mod._app_plan_server(str(plan), 'token')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base + '/token/') as response:
            body = response.read().decode()
            assert '<h1>Launch &lt;script&gt;bad()&lt;/script&gt;</h1>' in body
            assert '<script>' not in body
            assert '<h2>Second</h2>' in body
            assert body.count('<pre') == body.count('</pre>') == 2
            assert "default-src 'none'" in response.headers['Content-Security-Policy']
        for path in ('/', '/token/../plan.md', '/token/?file=other.md', '/other.md'):
            with pytest.raises(HTTPError) as error:
                urlopen(base + path)
            assert error.value.code == 404
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + '/token/', headers={'Host': 'external.example'}))
        assert error.value.code == 404
        plan.write_text('# Revised')
        with urlopen(base + '/token/') as response:
            assert '<h1>Revised</h1>' in response.read().decode()
        with urlopen(Request(base + '/token/', method='HEAD')) as response:
            assert response.read() == b''
        plan.unlink()
        with pytest.raises(HTTPError) as error:
            urlopen(base + '/token/')
        assert error.value.code == 404
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_app_plan_daemon_starts_reuses_and_restarts(t_mod, tmp_path, monkeypatch):
    from urllib.request import urlopen
    plan = tmp_path / 'plan.md'
    plan.write_text('# Real background preview')
    monkeypatch.setattr(t_mod, '_cache_root', lambda: str(tmp_path / 'cache'))
    popen = subprocess.Popen
    children = []
    def launch(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(t_mod.subprocess, 'Popen', launch)
    try:
        first = t_mod._app_plan_start(str(plan))
        with urlopen(first) as response:
            assert b'Real background preview' in response.read()
        assert t_mod._app_plan_start(str(plan)) == first
        assert len(children) == 1
        children[0].terminate()
        children[0].wait(timeout=5)
        again = t_mod._app_plan_start(str(plan))
        assert again != first
        assert len(children) == 2
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)


@pytest.mark.parametrize('ready,reply', [(False, b''), (True, b'bad json'), (True, b'{}')])
def test_app_plan_start_failure_cleans_up(t_mod, tmp_path, monkeypatch, ready, reply):
    from types import SimpleNamespace
    monkeypatch.setattr(t_mod, '_cache_root', lambda: str(tmp_path))
    events = []
    process = SimpleNamespace(stdout=io.BytesIO(reply), terminate=lambda: events.append('terminate'),
                              wait=lambda **kw: events.append('wait'))
    monkeypatch.setattr(t_mod.subprocess, 'Popen', lambda *a, **kw: process)
    monkeypatch.setattr(t_mod.select, 'select', lambda *a: ([process.stdout] if ready else [], [], []))
    with pytest.raises(ValueError, match='could not start plan preview'):
        t_mod._app_plan_start('/tmp/plan.md')
    assert events == ['terminate', 'wait']
    assert process.stdout.closed


def test_app_plan_serve_expires_after_idle_hour(t_mod, monkeypatch, capsys):
    from types import SimpleNamespace
    times = iter([0, 3601])
    server = SimpleNamespace(server_port=12345, last_access=0, handle_request=lambda: None)
    class Context:
        def __enter__(self): return server
        def __exit__(self, *a): pass
    monkeypatch.setattr(t_mod, '_app_plan_server', lambda *a: Context())
    monkeypatch.setattr(t_mod.time, 'monotonic', lambda: next(times))
    output = io.StringIO()
    monkeypatch.setattr(output, 'close', lambda: None)
    monkeypatch.setattr(t_mod.sys, 'stdout', output)
    t_mod._app_plan_serve('/tmp/plan.md')
    assert json.loads(output.getvalue())['url'].startswith('http://127.0.0.1:12345/')
