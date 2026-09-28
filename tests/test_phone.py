"""Phone reconnect identity, durable state, and portrait picker regressions.

These tests do not touch a live tmux server or the user's remembered sessions.
"""

import argparse
import io
import json
import os
import stat
import unicodedata
from types import SimpleNamespace

import pytest


def _row(**changes):
    row = {
        "id": "$1", "name": "dev-api-1", "created": "1750000000",
        "server": "1234", "socket": "/tmp/tmux-test/default",
        "cwd": "/work/api/1", "sid": "conversation-a", "agent": "claude",
        "summary": "Fix reconnect after the phone locks", "slot": "api-1",
    }
    row.update(changes)
    return row


def _display_width(text):
    return sum(
        0 if unicodedata.combining(char) else
        2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        for char in text
    )


@pytest.mark.parametrize("profile", ["phone", "phone-2", "Phone_3", "x" * 64])
def test_phone_profile_accepts_stable_connection_names(t_mod, profile):
    assert t_mod._phone_profile(profile) == profile


@pytest.mark.parametrize("profile", [
    "", "x" * 65, "../phone", "phone/2", "phone 2", "phone\n2",
    "$(touch marker)", ";exit", "télephone", ".", "phone\x00",
])
def test_phone_profile_rejects_paths_shell_input_and_controls(t_mod, profile):
    with pytest.raises(argparse.ArgumentTypeError):
        t_mod._phone_profile(profile)


def test_phone_state_round_trips_privately(t_mod, tmp_path):
    path = tmp_path / "phone" / "profiles" / "phone-1.json"
    saved = _row(summary="Réparer 接続")
    t_mod._phone_write(str(path), saved)

    assert t_mod._phone_load(str(path)) == saved
    assert json.loads(path.read_text()) == saved
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    # Replacing an older permissive file must not inherit its public mode.
    path.chmod(0o644)
    replacement = _row(sid="conversation-b")
    t_mod._phone_write(str(path), replacement)
    assert t_mod._phone_load(str(path)) == replacement
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("contents", [
    b"", b"{partial", b"null", b"[]", b'"phone"', b"12",
    b'{"sid":"\xff"}', b'{"sid":"' + b"a" * 70000 + b'"}',
])
def test_phone_bad_state_returns_to_picker(t_mod, tmp_path, contents):
    path = tmp_path / "phone.json"
    path.write_bytes(contents)
    assert t_mod._phone_load(str(path)) == {}


def test_phone_missing_or_unreadable_state_returns_to_picker(t_mod, tmp_path):
    assert t_mod._phone_load(str(tmp_path / "missing.json")) == {}
    assert t_mod._phone_load(str(tmp_path)) == {}


def test_phone_failed_state_replace_keeps_previous_assignment(t_mod, tmp_path, monkeypatch):
    path = tmp_path / "phone" / "phone.json"
    old = _row()
    t_mod._phone_write(str(path), old)
    original = path.read_bytes()

    def fail_replace(*args, **kwargs):
        raise OSError("simulated interrupted state replacement")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        t_mod._phone_write(str(path), _row(sid="conversation-b"))

    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


def test_phone_remembers_conversation_after_it_moves_slots(t_mod):
    saved = _row()
    reused_slot = _row(sid="new-task-in-old-slot")
    moved = _row(id="$9", name="dev-api-9", slot="api-9", created="1750000900")
    assert t_mod._phone_remembered([reused_slot, moved], saved) == moved


def test_phone_never_attaches_a_reused_slot_for_missing_conversation(t_mod):
    assert t_mod._phone_remembered([_row(sid="different-task")], _row()) is None


def test_phone_known_sid_does_not_fall_back_to_a_temporarily_unstamped_slot(t_mod):
    assert t_mod._phone_remembered([_row(sid="")], _row()) is None


def test_phone_sid_is_scoped_to_agent(t_mod):
    claude = _row()
    codex = _row(agent="codex", id="$2", name="dev-api-2")
    assert t_mod._phone_remembered([codex, claude], claude) == claude
    assert t_mod._phone_remembered([codex], claude) is None


def test_phone_duplicate_conversation_owners_require_a_choice(t_mod):
    first = _row()
    second = _row(id="$2", name="dev-api-2", slot="api-2")
    assert t_mod._phone_remembered([first, second], first) is None


def test_phone_unstamped_live_session_uses_exact_tmux_identity(t_mod):
    saved = _row(sid="")
    # Renaming the same live tmux session does not change its identity.
    renamed = _row(sid="", name="dev-api-2", slot="api-2")
    assert t_mod._phone_remembered([renamed], saved) == renamed


@pytest.mark.parametrize("changed", [
    {"id": "$2"}, {"created": "1750009999"}, {"server": "9999"},
    {"socket": "/tmp/tmux-test/other"},
])
def test_phone_unstamped_recreated_or_foreign_session_is_not_reconnected(t_mod, changed):
    saved = _row(sid="")
    assert t_mod._phone_remembered([_row(sid="", **changed)], saved) is None


def test_phone_empty_or_incomplete_identity_cannot_reconnect(t_mod):
    assert t_mod._phone_remembered([_row()], {}) is None
    assert t_mod._phone_remembered([_row(sid="")], {"name": "dev-api-1"}) is None
    assert t_mod._phone_remembered([_row(sid=""), _row(sid="")], _row(sid="")) is None


def _client(**changes):
    client = {
        "pid": "4242", "name": "/dev/ttys023",
        "socket": "/tmp/tmux-test/default", "server": "1234",
    }
    client.update(changes)
    return client


def test_phone_connection_matches_only_its_own_client(t_mod):
    record = dict(_client(), profile="phone-2")
    assert t_mod._phone_connection(record, _client()) == "phone-2"


@pytest.mark.parametrize("changed", [
    {"pid": "4243"}, {"name": "/dev/ttys024"},
    {"socket": "/tmp/tmux-test/other"}, {"server": "1235"},
])
def test_phone_stale_client_binding_cannot_change_another_phones_memory(t_mod, changed):
    record = dict(_client(), profile="phone-2")
    assert t_mod._phone_connection(record, _client(**changed)) is None


@pytest.mark.parametrize("record", [
    {}, {"profile": "phone"}, dict(_client(), profile="../phone"),
    dict(_client(), profile=""),
])
def test_phone_invalid_connection_record_is_ignored(t_mod, record):
    assert t_mod._phone_connection(record, _client()) is None


def _rows(count):
    return [
        _row(id=f"${n}", name=f"dev-api-{n}", slot=f"api-{n}", sid=f"task-{n}")
        for n in range(count)
    ]


@pytest.mark.parametrize("columns,lines", [(38, 18), (24, 12), (16, 8), (8, 6)])
def test_phone_picker_fits_portrait_screen_and_keeps_selection_visible(t_mod, columns, lines):
    rows = _rows(24)
    for selected in (0, 8, 9, 17, 23):
        rendered, visible = t_mod._phone_page(rows, selected, columns, lines)
        assert rows[selected] in visible
        assert 1 <= len(visible) <= 9
        assert len(rendered) <= lines
        assert all(_display_width(line) <= columns for line in rendered)


def test_phone_picker_clamps_selection_and_handles_no_sessions(t_mod):
    rows = _rows(24)
    assert rows[0] in t_mod._phone_page(rows, -200, 38, 18)[1]
    assert rows[-1] in t_mod._phone_page(rows, 200, 38, 18)[1]
    rendered, visible = t_mod._phone_page([], 0, 24, 12)
    assert visible == []
    assert rendered
    assert len(rendered) <= 12
    assert all(_display_width(line) <= 24 for line in rendered)


def test_phone_picker_sanitizes_session_titles_and_counts_wide_characters(t_mod):
    row = _row(
        slot="api\n1\r\x1b[2J",
        summary="\x1b[31m接続の修復 e\u0301\x1b[0m\t\x00\x7f" * 4,
    )
    rendered, visible = t_mod._phone_page([row], 0, 24, 12)
    assert visible == [row]
    assert all(_display_width(line) <= 24 for line in rendered)
    assert all(
        unicodedata.category(char) != "Cc"
        for line in rendered for char in line
    )
    # Rendering must not rewrite the identity/title used to attach the session.
    assert row["slot"] == "api\n1\r\x1b[2J"


class _Attached(Exception):
    """Stop at exec so command tests never attach the test runner to tmux."""


@pytest.fixture
def phone_command(t_mod, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr(t_mod.shutil, "which", lambda name: "/test/bin/tmux")
    terminal = SimpleNamespace(isatty=lambda: True, fileno=lambda: 0)
    errors = io.StringIO()
    monkeypatch.setattr(t_mod, "sys", SimpleNamespace(
        stdin=terminal, stdout=SimpleNamespace(isatty=lambda: True), stderr=errors))
    monkeypatch.setattr(t_mod.os, "ttyname", lambda fd: "/dev/ttys023")

    state = SimpleNamespace(
        root=tmp_path / "state" / "t" / "phone",
        args=SimpleNamespace(client="phone", pick=False, tmux_client=None, tmux_client_pid=None),
        rows=[_row()], chosen=_row(), selections=[], execs=[], switches=[],
        target=None, switch_error="", errors=errors,
    )

    def choose(rows):
        state.selections.append(rows)
        return state.chosen

    def attach(binary, argv):
        state.execs.append((binary, argv))
        raise _Attached

    def switch(*args):
        state.switches.append(args)
        return SimpleNamespace(returncode=bool(state.switch_error), stderr=state.switch_error)

    monkeypatch.setattr(t_mod, "_phone_sessions", lambda: state.rows)
    monkeypatch.setattr(t_mod, "_phone_picker", choose)
    monkeypatch.setattr(t_mod, "_phone_client", lambda name, pid: state.target)
    monkeypatch.setattr(t_mod, "_phone_tmux", switch)
    monkeypatch.setattr(t_mod.os, "execvp", attach)
    return state


@pytest.mark.parametrize("missing", ["tmux", "stdin", "stdout"])
def test_phone_command_requires_tmux_and_interactive_terminal(t_mod, phone_command, monkeypatch, missing):
    state = phone_command
    if missing == "tmux":
        monkeypatch.setattr(t_mod.shutil, "which", lambda name: None)
    else:
        monkeypatch.setattr(getattr(t_mod.sys, missing), "isatty", lambda: False)
    assert t_mod.cmd_phone(None, state.args) == 1
    assert not state.selections and not state.execs and not state.switches
    assert not state.root.exists()


def test_phone_command_first_connection_saves_assignment_without_detaching_desktop(t_mod, phone_command):
    state = phone_command
    with pytest.raises(_Attached):
        t_mod.cmd_phone(None, state.args)

    assert state.execs == [("tmux", ["tmux", "-u", "attach-session", "-t", "$1"])]
    assert len(state.selections) == 1
    assert not state.switches
    assert t_mod._phone_load(str(state.root / "profiles" / "phone.json")) == _row()
    connection = t_mod._phone_load(str(state.root / "connections" / f"{os.getpid()}.json"))
    assert connection == dict(_client(pid=str(os.getpid())), profile="phone")


def test_phone_command_reconnects_each_profiles_last_conversation(t_mod, phone_command):
    state = phone_command
    first, second = _row(), _row(sid="conversation-b", id="$2", slot="api-2", name="dev-api-2")
    state.rows = [first, second]
    for profile, choice in (("phone-1", first), ("phone-2", second)):
        state.args.client, state.chosen = profile, choice
        with pytest.raises(_Attached):
            t_mod.cmd_phone(None, state.args)

    state.selections.clear()
    state.args.client = "phone-1"
    state.chosen = None
    with pytest.raises(_Attached):
        t_mod.cmd_phone(None, state.args)
    assert state.execs[-1] == ("tmux", ["tmux", "-u", "attach-session", "-t", "$1"])
    assert state.selections == []
    assert t_mod._phone_load(str(state.root / "profiles" / "phone-1.json")) == first
    assert t_mod._phone_load(str(state.root / "profiles" / "phone-2.json")) == second


def test_phone_command_pick_overrides_remembered_conversation(t_mod, phone_command):
    state = phone_command
    second = _row(sid="conversation-b", id="$2", slot="api-2", name="dev-api-2")
    t_mod._phone_write(str(state.root / "profiles" / "phone.json"), _row())
    state.args.pick, state.rows, state.chosen = True, [_row(), second], second
    with pytest.raises(_Attached):
        t_mod.cmd_phone(None, state.args)
    assert len(state.selections) == 1
    assert state.execs[-1][1][-1] == "$2"
    assert t_mod._phone_load(str(state.root / "profiles" / "phone.json")) == second


@pytest.mark.parametrize("rows", [[], [_row()]])
def test_phone_command_empty_list_or_cancel_creates_no_assignment(t_mod, phone_command, rows):
    state = phone_command
    state.rows, state.chosen = rows, None
    assert t_mod.cmd_phone(None, state.args) == 0
    assert state.selections == [rows]
    assert not state.execs and not state.switches
    assert not state.root.exists()


def test_phone_command_dead_remembered_session_prompts_without_starting_an_agent(t_mod, phone_command):
    state = phone_command
    path = state.root / "profiles" / "phone.json"
    t_mod._phone_write(str(path), _row(sid="ended-conversation"))
    state.chosen = None
    assert t_mod.cmd_phone(None, state.args) == 0
    assert len(state.selections) == 1
    assert "previous session is no longer running" in state.errors.getvalue()
    assert t_mod._phone_load(str(path))["sid"] == "ended-conversation"
    assert not state.execs and not state.switches


def test_phone_command_revalidates_choice_before_overwriting_memory(t_mod, phone_command, monkeypatch):
    state = phone_command
    path = state.root / "profiles" / "phone.json"
    old = _row(sid="previous-choice")
    t_mod._phone_write(str(path), old)
    scans = iter([[_row()], [_row(sid="replacement-in-same-slot")]])
    monkeypatch.setattr(t_mod, "_phone_sessions", lambda: next(scans))
    assert t_mod.cmd_phone(None, state.args) == 1
    assert t_mod._phone_load(str(path)) == old
    assert not state.execs and not state.switches
    assert not (state.root / "connections").exists()


def _popup(state):
    state.target = _client(session="$1")
    state.args.tmux_client = state.target["name"]
    state.args.tmux_client_pid = state.target["pid"]
    state.chosen = _row(id="$2", sid="conversation-b", name="dev-api-2", slot="api-2")
    state.rows.append(state.chosen)


@pytest.mark.parametrize("registered", [True, False])
def test_phone_popup_switches_only_explicit_client_and_remembers_only_registered_profile(t_mod, phone_command, registered):
    state = phone_command
    _popup(state)
    default = state.root / "profiles" / "phone.json"
    t_mod._phone_write(str(default), _row())
    if registered:
        t_mod._phone_write(str(state.root / "connections" / "4242.json"),
                           dict(state.target, profile="phone-2"))

    assert t_mod.cmd_phone(None, state.args) == 0
    assert state.switches == [("switch-client", "-c", "/dev/ttys023", "-t", "$2")]
    assert not state.execs
    assert t_mod._phone_load(str(default)) == _row()
    profile = state.root / "profiles" / "phone-2.json"
    assert t_mod._phone_load(str(profile)) == (state.chosen if registered else {})


def test_phone_popup_refuses_client_that_disconnected_before_opening(t_mod, phone_command):
    state = phone_command
    state.args.tmux_client, state.args.tmux_client_pid = "/dev/ttys023", "4242"
    assert t_mod.cmd_phone(None, state.args) == 1
    assert not state.selections and not state.execs and not state.switches
    assert not state.root.exists()


@pytest.mark.parametrize("current", [None, _client(pid="5555"), _client(session="$9")])
def test_phone_popup_refuses_disconnected_reused_or_switched_client(t_mod, phone_command, monkeypatch, current):
    state = phone_command
    _popup(state)
    clients = iter([state.target, current])
    monkeypatch.setattr(t_mod, "_phone_client", lambda name, pid: next(clients))
    assert t_mod.cmd_phone(None, state.args) == 1
    assert not state.execs and not state.switches
    assert not state.root.exists()


def test_phone_popup_failed_switch_preserves_assignment(t_mod, phone_command):
    state = phone_command
    _popup(state)
    state.switch_error = "client disappeared"
    path = state.root / "profiles" / "phone-2.json"
    t_mod._phone_write(str(path), _row())
    t_mod._phone_write(str(state.root / "connections" / "4242.json"),
                       dict(state.target, profile="phone-2"))
    assert t_mod.cmd_phone(None, state.args) == 1
    assert "client disappeared" in state.errors.getvalue()
    assert t_mod._phone_load(str(path)) == _row()
    assert not state.execs


def test_phone_command_reports_state_write_failure_before_attaching(t_mod, phone_command, monkeypatch):
    state = phone_command

    def fail_write(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(t_mod, "_phone_write", fail_write)
    assert t_mod.cmd_phone(None, state.args) == 1
    assert "disk full" in state.errors.getvalue()
    assert not state.execs and not state.switches


def test_phone_command_inside_tmux_directs_to_client_specific_popup(t_mod, phone_command, monkeypatch):
    state = phone_command
    monkeypatch.setenv("TMUX", "/tmp/tmux-test/default,1234,0")
    assert t_mod.cmd_phone(None, state.args) == 1
    assert "press F1" in state.errors.getvalue()
    assert not state.selections and not state.execs and not state.switches
