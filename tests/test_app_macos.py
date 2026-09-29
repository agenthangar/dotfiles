"""Native New Window action + URL routing, with a disposable app, never Codex."""

import ctypes
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
import uuid

import pytest


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"),
                    reason="requires macOS LaunchServices and Swift")
@pytest.mark.parametrize("already_running", [False, True])
def test_app_creates_a_real_window_and_preserves_the_existing_session(t_mod, tmp_path, monkeypatch, already_running):
    accessibility = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    accessibility.AXIsProcessTrusted.restype = ctypes.c_bool
    if not accessibility.AXIsProcessTrusted():
        pytest.skip("native window test requires Accessibility access")
    scheme = "t-handoff-" + uuid.uuid4().hex
    bundle = tmp_path / "Handoff Receiver.app"
    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    executable = macos / "receiver"
    with (bundle / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleIdentifier": "test." + scheme,
                      "CFBundleName": "Handoff Receiver", "CFBundleExecutable": "receiver",
                      "CFBundlePackageType": "APPL",
                      "CFBundleURLTypes": [{"CFBundleURLSchemes": [scheme]}]}, fh)
    source = Path(__file__).parent / "fixtures" / "handoff-receiver.swift"
    subprocess.run(["swiftc", str(source), "-o", str(executable)], check=True,
                   capture_output=True, timeout=60)

    def states():
        return [json.loads(p.read_text()) for p in tmp_path.glob("*.json")]

    def wait_for(predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = states()
            if predicate(rows):
                return rows
            time.sleep(.05)
        pytest.fail("receiver did not reach expected state: " + repr(states()))

    # Start with an in-flight session in a real window. Only the unique test
    # bundle and URL scheme ever reach native UI APIs or LaunchServices.
    previous = scheme + "://threads/in-flight"
    if already_running:
        subprocess.run(["open", "-a", str(bundle), "--args", previous], check=True)
    try:
        old = wait_for(lambda rows: len(rows) == 1 and rows[0]["ready"])[0] if already_running else None
        if old:
            assert old["windows"][0]["urls"] == [previous]
        sid = "01234567-89ab-cdef-0123-456789abcdef"
        row = dict(host="local", sid=sid, cwd=str(tmp_path), slot="api-13",
                   state="detached", context="active", agent="codex", summary="Handoff")
        monkeypatch.setattr(t_mod, "_app_select", lambda *args: row)
        monkeypatch.setattr(t_mod, "zsh_capture", lambda *args: "")
        monkeypatch.setattr(t_mod, "_app_bundle", lambda: str(bundle))
        stopped = []
        def stop(row):
            stopped.append(row)
            return subprocess.CompletedProcess([], 0, "", "")
        monkeypatch.setattr(t_mod, "_app_stop_cli", stop)
        monkeypatch.setattr(t_mod, "_cache_root", lambda: str(tmp_path / "cache"))
        run = t_mod._run
        launched = []
        def launch(argv, **kwargs):
            if argv[0] == "ps":
                return run(argv, **kwargs)
            # The production command is exercised, but ALL URLs are translated
            # to our disposable scheme before anything reaches LaunchServices.
            assert argv[0] == "open" and str(bundle) in argv
            argv = [a.replace("codex://", scheme + "://", 1) if a.startswith("codex://") else a for a in argv]
            assert not any("codex://" in a for a in argv)
            launched.append(argv)
            return run([argv[0], "-g", *argv[1:]], **kwargs)
        monkeypatch.setattr(t_mod, "_run", launch)
        args = t_mod.build_parser().parse_args(["app", "api", "13", "--no-plan", "--url", "http://localhost:5213/#debug"])
        assert t_mod.cmd_app(None, args) == 0
        expected = t_mod._app_link(sid, "http://localhost:5213/#debug").replace("codex://", scheme + "://", 1)
        first = wait_for(lambda rows: len(rows) == 1 and rows[0]["current"] == expected)[0]
        if old:
            assert first["pid"] == old["pid"]
            assert first["windows"][0] == old["windows"][0]
        else:
            assert first["windows"][0]["urls"] == []
        assert first["newWindowActions"] == 1
        assert len(first["windows"]) == 2
        assert first["windows"][1]["urls"] == [expected]
        assert first["windows"][1]["id"] != first["windows"][0]["id"]
        assert expected not in first["arguments"]

        # Every default handoff gets a distinct window, keeping BOTH old views.
        args.url = "http://localhost:5213/#another-page"
        assert t_mod.cmd_app(None, args) == 0
        second_link = t_mod._app_link(sid, args.url).replace("codex://", scheme + "://", 1)
        second = wait_for(lambda rows: len(rows) == 1 and rows[0]["current"] == second_link)[0]
        assert second["newWindowActions"] == 2
        assert len(second["windows"]) == 3
        assert second["windows"][:2] == first["windows"]
        assert second["windows"][2]["urls"] == [second_link]
        assert len(stopped) == 2

        args.reuse_window = True
        args.url = "http://localhost:5213/#reuse"
        assert t_mod.cmd_app(None, args) == 0
        reused_link = t_mod._app_link(sid, args.url).replace("codex://", scheme + "://", 1)
        reused = wait_for(lambda rows: len(rows) == 1 and rows[0]["current"] == reused_link)[0]
        assert reused["pid"] == first["pid"]
        assert reused["newWindowActions"] == 2
        assert reused["windows"][:2] == first["windows"]
        assert reused["windows"][2]["urls"] == [second_link, reused_link]
        assert len(stopped) == 3
        # Two blank activations to create windows, then three URL requests.
        assert len(launched) == 5
    finally:
        for row in states():
            # Kill only test receivers still running this exact disposable binary.
            cmd = subprocess.run(["ps", "-p", str(row["pid"]), "-o", "command="], capture_output=True, text=True).stdout
            if str(executable) in cmd:
                os.kill(row["pid"], 15)
