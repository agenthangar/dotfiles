"""Window handoff state machine; the native macOS test covers the AX bridge."""

from pathlib import Path
import runpy
import subprocess

import pytest


@pytest.fixture
def window_module():
    return runpy.run_path(str(Path(__file__).parents[1] / "libexec" / "t_app_window.py"))


class FakeAccessibility:
    def __init__(self, action="create"):
        self.action = action
        self.windows = ["old"]
        self.focused = "old"
        self.actions = []
        self.enabled = True

    def application(self, pid):
        assert pid == 42
        return "app"

    def read(self, element, attribute):
        if element == "app":
            return {"AXWindows": self.windows[:], "AXFocusedWindow": self.focused, "AXMenuBar": "bar"}.get(attribute)
        return {
            ("bar", "AXChildren"): ["file"], ("file", "AXChildren"): ["menu"],
            ("menu", "AXTitle"): "New Window", ("menu", "AXRole"): "AXMenuItem",
            ("menu", "AXEnabled"): self.enabled, ("new", "AXSubrole"): "AXStandardWindow",
            ("extra", "AXSubrole"): "AXStandardWindow", ("dialog", "AXSubrole"): "AXDialog",
        }.get((element, attribute))

    def press(self, element, action="AXPress"):
        self.actions.append((element, action))
        if element == "menu":
            self.windows += {"create": ["new"], "noop": [], "dialog": ["dialog"],
                             "ambiguous": ["new", "extra"]}[self.action]
        elif action == "AXRaise":
            self.focused = element

    def activate(self, application):
        assert application == "app"


def successful_run(argv, **kwargs):
    return subprocess.CompletedProcess(argv, 0, "42 /Applications/Test.app/Contents/MacOS/Test\n", "")


def test_creates_and_keeps_focusing_only_the_new_window(window_module):
    api = FakeAccessibility()
    window = window_module["new_window"]("/Applications/Test.app", successful_run, api)
    assert api.windows == ["old", "new"]
    api.focused = "old"  # Another window became active before URL delivery.
    window.focus()
    assert api.focused == "new"
    assert api.actions == [("menu", "AXPress"), ("new", "AXRaise"), ("new", "AXRaise")]


@pytest.mark.parametrize("action,message", [("noop", "distinct"), ("dialog", "distinct"), ("ambiguous", "multiple")])
def test_accepting_menu_action_is_not_proof_of_a_window(window_module, monkeypatch, action, message):
    create = window_module["new_window"]
    wait = window_module["wait_for"]
    monkeypatch.setitem(create.__globals__, "wait_for", lambda probe, message, **kw: wait(probe, message, timeout=0))
    api = FakeAccessibility(action)
    with pytest.raises(ValueError, match=message):
        create("/Applications/Test.app", successful_run, api)
    assert api.focused == "old"
    assert api.actions == [("menu", "AXPress")]


def test_disabled_menu_does_not_press_anything(window_module, monkeypatch):
    create = window_module["new_window"]
    wait = window_module["wait_for"]
    monkeypatch.setitem(create.__globals__, "wait_for", lambda probe, message, **kw: wait(probe, message, timeout=0))
    api = FakeAccessibility()
    api.enabled = False
    with pytest.raises(ValueError, match="enabled New Window"):
        create("/Applications/Test.app", successful_run, api)
    assert api.actions == []


@pytest.mark.parametrize("closed", ["new", "old"])
def test_closed_window_prevents_routing_elsewhere(window_module, closed):
    api = FakeAccessibility()
    window = window_module["new_window"]("/Applications/Test.app", successful_run, api)
    api.windows.remove(closed)
    before = api.actions[:]
    with pytest.raises(ValueError, match="windows changed"):
        window.focus()
    assert api.actions == before


def test_failed_focus_does_not_claim_readiness(window_module, monkeypatch):
    api = FakeAccessibility()
    window = window_module["new_window"]("/Applications/Test.app", successful_run, api)
    api.focused = "old"
    api.press = lambda *args: None
    wait = window_module["wait_for"]
    monkeypatch.setitem(window.focus.__globals__, "wait_for", lambda probe, message, **kw: wait(probe, message, timeout=0))
    with pytest.raises(ValueError, match="could not focus"):
        window.focus()


def test_missing_accessibility_permission_prevents_app_launch(window_module, monkeypatch):
    create = window_module["new_window"]
    def denied():
        raise ValueError("Accessibility permission required")
    monkeypatch.setitem(create.__globals__, "Accessibility", denied)
    with pytest.raises(ValueError, match="Accessibility"):
        create("/Applications/Test.app", lambda *a, **kw: pytest.fail("must not launch"))


def test_launch_failure_does_not_press_menu(window_module):
    api = FakeAccessibility()
    with pytest.raises(ValueError, match="could not launch"):
        window_module["new_window"]("/Applications/Test.app",
            lambda *a, **kw: subprocess.CompletedProcess([], 1, "", "failed"), api)
    assert api.actions == []


@pytest.mark.parametrize("processes,expected", [
    ("42 /Applications/Test.app/Contents/MacOS/Test\n", 42),
    (" 42 /Applications/Test.app/Contents/MacOS/Test\n43 /Applications/Other.app/Contents/MacOS/Test\n", 42),
    ("43 /Applications/Test.app/Contents/Frameworks/Helper.app/Contents/MacOS/Helper\n", None),
    ("", None),
])
def test_app_pid_targets_only_the_selected_bundle(window_module, processes, expected):
    assert window_module["app_pid"]("/Applications/Test.app", lambda *a, **kw:
        subprocess.CompletedProcess([], 0, processes, "")) == expected


@pytest.mark.parametrize("result", [subprocess.CompletedProcess([], 1, "", "failed"),
    subprocess.CompletedProcess([], 0, "42 /Applications/Test.app/Contents/MacOS/Test\n43 /Applications/Test.app/Contents/MacOS/Test", "")])
def test_app_pid_does_not_guess_after_failure_or_multiple_instances(window_module, result):
    with pytest.raises(ValueError):
        window_module["app_pid"]("/Applications/Test.app", lambda *a, **kw: result)


def test_wait_for_handles_delayed_window(window_module, monkeypatch):
    waiting = window_module["wait_for"]
    monkeypatch.setattr(waiting.__globals__["time"], "sleep", lambda _: None)
    responses = iter([None, None, "new window"])
    assert waiting(lambda: next(responses), "timeout") == "new window"
