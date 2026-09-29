"""Real LaunchServices delivery, with a disposable receiver and no Codex launch."""

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
def test_app_url_delivery_and_running_desktop_guard(t_mod, tmp_path, monkeypatch):
    scheme = "t-handoff-" + uuid.uuid4().hex
    bundle = tmp_path / "Handoff Receiver.app"
    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    executable = macos / "receiver"
    with (bundle / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleIdentifier": "test." + scheme,
                      "CFBundleName": "Handoff Receiver", "CFBundleExecutable": "receiver",
                      "CFBundlePackageType": "APPL", "LSBackgroundOnly": True,
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

    # A windowless test receiver, never Codex. This checks OS URL delivery and
    # the real process guard, not Codex's rendering or new-window support.
    try:
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
        assert not t_mod._app_running(str(bundle))
        assert t_mod.cmd_app(None, args) == 0
        expected = t_mod._app_link(sid, "http://localhost:5213/#debug").replace("codex://", scheme + "://", 1)
        old = wait_for(lambda rows: len(rows) == 1 and rows[0]["ready"] and rows[0]["current"] == expected)[0]
        assert old["urls"] == [expected]
        assert expected not in old["arguments"]
        assert t_mod._app_running(str(bundle))

        # A running app must not cause the old silent-success/no-window behavior,
        # or a fallback navigation. In particular, keep its terminal agent alive.
        args.url = "http://localhost:5213/#another-page"
        assert t_mod.cmd_app(None, args) == 1
        assert states() == [old]
        assert len(stopped) == 1
        assert len(launched) == 1

        args.reuse_window = True
        assert t_mod.cmd_app(None, args) == 0
        reused_link = t_mod._app_link(sid, args.url).replace("codex://", scheme + "://", 1)
        reused = wait_for(lambda rows: len(rows) == 1 and rows[0]["current"] == reused_link)[0]
        assert reused["pid"] == old["pid"]
        assert reused["urls"] == [expected, reused_link]
        assert len(stopped) == len(launched) == 2
    finally:
        for row in states():
            # Kill only test receivers still running this exact disposable binary.
            cmd = subprocess.run(["ps", "-p", str(row["pid"]), "-o", "command="], capture_output=True, text=True).stdout
            if str(executable) in cmd:
                os.kill(row["pid"], 15)
