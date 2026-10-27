"""Hotspot joins use fake macOS commands; process tests own all spawned children."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
spec = importlib.util.spec_from_file_location("nosleep_hotspot", LIB / "nosleep_hotspot.py")
hotspot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hotspot)
PASSWORD = " fake-password-with-spaces "


@pytest.fixture
def commands(tmp_path, monkeypatch):
    log = tmp_path / "commands.jsonl"
    monkeypatch.setenv("HOTSPOT_TEST_LOG", str(log))
    body = '''import hashlib, json, os, pathlib, sys, time
args = sys.argv[1:]
with open(os.environ["HOTSPOT_TEST_LOG"], "a") as f:
    f.write(json.dumps(args) + "\\n")
mode = os.environ.get("HOTSPOT_TEST_MODE", "")
if args[0] == "-listallhardwareports":
    if mode == "timeout": time.sleep(60)
    print("Hardware Port: Ethernet\\nDevice: en9\\n\\nHardware Port: Wi-Fi\\nDevice: en7" if mode != "no-wifi" else "Hardware Port: Ethernet\\nDevice: en9")
elif args[0] == "-getairportpower": print("Wi-Fi Power (en7): " + ("Off" if mode == "off" else "On"))
elif args[0] == "find-generic-password":
    if mode == "keychain":
        print("private diagnostic fake-password-with-spaces", file=sys.stderr)
        sys.exit(44)
    print(" fake-password-with-spaces ")
elif args[0] == "-setairportnetwork":
    secret = sys.stdin.buffer.read()
    with open(os.environ["HOTSPOT_TEST_LOG"], "a") as f:
        f.write(json.dumps({"input_sha256": hashlib.sha256(secret).hexdigest()}) + "\\n")
    if mode == "association": print("Failed to join network: secret diagnostic")
    if mode == "stderr": print("Error joining network", file=sys.stderr)
'''
    paths = []
    for name in ("networksetup", "security"):
        path = tmp_path / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)
        paths.append(str(path))
    return paths, log


def test_join_discovers_wifi_and_transfers_password_only_via_stdin(commands):
    (networksetup, security), log = commands
    ssid = "📱 Test's;$(touch BAD)"
    hotspot.connect(ssid, networksetup=networksetup, security=security)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows[1] == ["-getairportpower", "en7"]
    assert rows[2] == ["find-generic-password", "-a", ssid, "-s", hotspot.SERVICE, "-w"]
    assert rows[3] == ["-setairportnetwork", "en7", ssid, "-"]
    assert rows[4] == {"input_sha256": hashlib.sha256((PASSWORD + "\n").encode()).hexdigest()}
    assert PASSWORD not in log.read_text()
    assert not (log.parent / "BAD").exists()


@pytest.mark.parametrize("mode", ["no-wifi", "off", "keychain", "association", "stderr", "timeout"])
def test_failures_are_bounded_and_do_not_toggle_wifi(commands, monkeypatch, mode):
    (networksetup, security), log = commands
    monkeypatch.setenv("HOTSPOT_TEST_MODE", mode)
    start = time.monotonic()
    with pytest.raises(hotspot.HotspotError) as error:
        hotspot.connect("Test Phone", .1 if mode == "timeout" else 5,
                        networksetup=networksetup, security=security)
    assert time.monotonic() - start < 3
    assert PASSWORD not in str(error.value)
    recorded = log.read_text() if log.exists() else ""
    assert "-setairportpower" not in recorded
    if mode in ("no-wifi", "off", "keychain"):
        assert "-setairportnetwork" not in recorded


@pytest.mark.parametrize("ssid", ["", "a\nb", "a\x00b", "a\x85b", "📱" * 9, "a" * 33])
def test_invalid_ssid_never_runs_a_command(commands, ssid):
    (networksetup, security), log = commands
    with pytest.raises(ValueError):
        hotspot.connect(ssid, networksetup=networksetup, security=security)
    assert not log.exists()


def test_cli_redacts_exception_data(monkeypatch, capsys):
    def fail(*args):
        raise hotspot.HotspotError(PASSWORD)
    monkeypatch.setattr(hotspot, "connect", fail)
    assert hotspot.main(["connect", "--ssid", "Phone"]) == 1
    captured = capsys.readouterr()
    assert PASSWORD not in captured.err and "Keychain" in captured.err


@pytest.mark.parametrize("keychain_success", [True, False])
def test_setup_prompts_locally_and_only_enables_after_keychain_success(tmp_path, monkeypatch, keychain_success):
    monkeypatch.syspath_prepend(str(LIB))
    import nosleep_config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = nosleep_config.config_path()
    path.parent.mkdir()
    path.write_text('{"every": 7}\n')
    before = path.read_bytes()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "Test Phone")
    def save(argv, **kwargs):
        assert argv == ["fake-security", "add-generic-password", "-U", "-a", "Test Phone",
                        "-s", hotspot.SERVICE, "-T", hotspot.SECURITY, "-w"]
        assert kwargs == {"timeout": 120}
        assert path.read_bytes() == before
        return subprocess.CompletedProcess(argv, 0 if keychain_success else 1)
    monkeypatch.setattr(hotspot.subprocess, "run", save)
    if keychain_success:
        hotspot.setup(security="fake-security")
        saved = json.loads(path.read_text())
        assert saved["hotspot"] is True and saved["hotspot_ssid"] == "Test Phone"
        assert saved["every"] == 7 and path.stat().st_mode & 0o777 == 0o600
    else:
        with pytest.raises(hotspot.HotspotError):
            hotspot.setup(security="fake-security")
        assert path.read_bytes() == before


def test_setup_write_conflict_preserves_concurrent_changes(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(LIB))
    import nosleep_config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = nosleep_config.config_path()
    path.parent.mkdir()
    path.write_text('{}\n')
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "Test Phone")
    def save(argv, **kwargs):
        path.write_text('{"every": 11}\n')
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(hotspot.subprocess, "run", save)
    with pytest.raises(ValueError, match="changed while editing"):
        hotspot.setup(security="fake-security")
    assert json.loads(path.read_text()) == {"every": 11}


def test_setup_headless_never_runs_keychain(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.syspath_prepend(str(LIB))
    monkeypatch.setattr(hotspot.subprocess, "run", lambda *a, **k: pytest.fail("Keychain called headlessly"))
    with pytest.raises(hotspot.HotspotError, match="unlocked Mac terminal"):
        hotspot.setup()


def test_cancellation_during_spawn_keeps_child_registered_for_cleanup(monkeypatch):
    original = subprocess.Popen
    children = []
    def interrupted_spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        os.kill(os.getpid(), signal.SIGTERM)
        return child
    runner = hotspot.Runner(5)
    monkeypatch.setattr(hotspot.subprocess, "Popen", interrupted_spawn)
    try:
        with pytest.raises(hotspot.Cancelled):
            runner.run([sys.executable, "-c", "import time; time.sleep(60)"])
    finally:
        runner.finish()
    assert children and children[0].poll() is not None


def _alive(pid):
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return result.returncode == 0 and result.stdout.strip() and not result.stdout.lstrip().startswith("Z")


@pytest.mark.parametrize("mode", ["cancel", "timeout", "leader-exits"])
def test_owned_command_group_reaps_descendants_even_after_leader_exits(tmp_path, mode):
    pidfile = tmp_path / "descendant.pid"
    command = tmp_path / "fork.py"
    command.write_text('''import os, pathlib, signal, sys, time
pidfile, mode = sys.argv[1:]
if os.fork() == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2): os.dup2(null, fd)
    pathlib.Path(pidfile).write_text(str(os.getpid()))
    while True: time.sleep(1)
while not pathlib.Path(pidfile).exists(): time.sleep(.01)
if mode == "leader-exits": sys.exit(0)
while True: time.sleep(1)
''')
    script = f'''import sys
sys.path.insert(0, {str(LIB)!r})
from nosleep_hotspot import Runner, HotspotError
runner = Runner({.3 if mode == 'timeout' else 10})
try:
    runner.run({[sys.executable, str(command), str(pidfile), mode]!r})
except HotspotError:
    pass
finally:
    runner.finish()
'''
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    descendant = None
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert pidfile.exists()
        descendant = int(pidfile.read_text())
        if mode == "cancel":
            process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        deadline = time.monotonic() + 2
        while _alive(descendant) and time.monotonic() < deadline:
            time.sleep(.01)
        assert not _alive(descendant)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        if descendant is not None and _alive(descendant):
            os.kill(descendant, signal.SIGKILL)
