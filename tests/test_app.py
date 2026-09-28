"""Desktop handoff: exact conversation, URL encoding, and stop-before-open failures."""

import io
import os
import plistlib
import shlex
import shutil
import subprocess
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest


SID = "01234567-89ab-cdef-0123-456789abcdef"


@pytest.fixture
def app_slot(t_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(t_mod, "CONFIG", str(tmp_path / "no-config"))
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
    assert parse_qs(link.query) == {"browserUrl": [url]}
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


def test_app_command_dry_run_is_read_only(app_command, capsys):
    call, events, _ = app_command
    assert call("--dry-run") == 0
    assert events == []
    assert "Would stop" in capsys.readouterr().out


def test_app_command_explicit_url_and_no_preview(t_mod, app_command):
    call, events, _ = app_command
    assert call("--url", "http://localhost:9000/a?x=1&y=2") == 0
    assert events[-1][1][-1] == t_mod._app_link(SID, "http://localhost:9000/a?x=1&y=2")
    events.clear()
    assert call("--no-preview") == 0
    assert events[-1][1][-1] == t_mod._app_link(SID)
    with pytest.raises(SystemExit):
        call("--url", "http://localhost:9000", "--no-preview")


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
    call, events, _ = app_command
    monkeypatch.setattr(t_mod, "_run", lambda *a, **kw:
                        subprocess.CompletedProcess([], 1, "", "launch failed"))
    assert call() == 1
    assert [e[0] for e in events] == ["stop"]
    assert "codex resume " + SID in capsys.readouterr().err


@pytest.mark.parametrize("scenario,ok", [
    ("normal", True), ("already_stopped", True), ("changed_sid", False),
    ("changed_cwd", False), ("changed_agent", False), ("missing", False),
    ("self", False), ("term_failed", False), ("still_running", False),
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


def test_app_stop_revalidates_directory_with_real_tmux(t_mod, app_slot, monkeypatch, tmp_path):
    """Regression: =session is a session target, but display-message needs =session:.

    The old mocked tmux always returned the cwd and concealed this failure. Keep
    tmux real here, including an active decoy session in a different directory;
    only the Codex probes are stubbed. No real agent is needed or signaled.
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
    finally:
        subprocess.run(tmux + ["kill-server"], env=env, capture_output=True)
