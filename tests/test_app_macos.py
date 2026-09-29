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
def test_app_new_instance_receives_arguments_without_navigating_existing(t_mod, tmp_path, monkeypatch):
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

    # Existing "in-flight" session in a windowless test receiver, never Codex.
    previous = scheme + "://threads/in-flight"
    subprocess.run(["open", "-g", "-n", "-a", str(bundle), "--args", previous], check=True)
    try:
        old = wait_for(lambda rows: len(rows) == 1 and rows[0]["ready"])[0]
        assert old["current"] == previous

        sid = "01234567-89ab-cdef-0123-456789abcdef"
        row = dict(host="local", sid=sid, cwd=str(tmp_path), slot="api-13",
                   state="detached", context="active", agent="codex", summary="Handoff")
        monkeypatch.setattr(t_mod, "_app_select", lambda *args: row)
        monkeypatch.setattr(t_mod, "zsh_capture", lambda *args: "")
        monkeypatch.setattr(t_mod, "_app_bundle", lambda: str(bundle))
        monkeypatch.setattr(t_mod, "_app_stop_cli", lambda row: subprocess.CompletedProcess([], 0, "", ""))
        monkeypatch.setattr(t_mod, "_cache_root", lambda: str(tmp_path / "cache"))
        run = t_mod._run
        launched = []
        def launch(argv, **kwargs):
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
        rows = wait_for(lambda rows: len(rows) == 2 and all(r["ready"] for r in rows))
        old_after = next(r for r in rows if r["pid"] == old["pid"])
        new = next(r for r in rows if r["pid"] != old["pid"])
        expected = t_mod._app_link(sid, "http://localhost:5213/#debug").replace("codex://", scheme + "://", 1)
        assert old_after == old, "the existing session received a handoff event"
        assert expected in new["arguments"], "handoff was dispatched as an OS URL event instead of a process argument"
        assert new["current"] == expected
        assert new["urls"] == []
        assert len(launched) == 1
    finally:
        for row in states():
            # Kill only test receivers still running this exact disposable binary.
            cmd = subprocess.run(["ps", "-p", str(row["pid"]), "-o", "command="], capture_output=True, text=True).stdout
            if str(executable) in cmd:
                os.kill(row["pid"], 15)
