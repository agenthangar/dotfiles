"""nosleep config persists validated machine defaults without invoking power controls."""

import importlib.util
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import stat
import struct
import subprocess
import sys
import termios
import time

import pytest


MODULE = Path(__file__).resolve().parents[1] / "lib" / "nosleep_config.py"
spec = importlib.util.spec_from_file_location("nosleep_config", MODULE)
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


@pytest.fixture
def isolated(tmp_path):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    Path(env["HOME"]).mkdir()
    return env, Path(env["XDG_CONFIG_HOME"]) / "nosleep" / "config.json"


def run_config(isolated, *args):
    env, _ = isolated
    return subprocess.run([sys.executable, str(MODULE), *args], env=env,
                          text=True, capture_output=True)


def test_defaults_and_values_do_not_write(isolated):
    _, path = isolated
    result = run_config(isolated, "--values")
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["dim=0", "lock=1", "forever=0", "grace=900",
                                          "every=30", "retries=3", "backoff=30"]
    shown = run_config(isolated, "--show")
    assert shown.returncode == 0
    assert "Lock" in shown.stdout and "Display" in shown.stdout
    assert not path.exists()


@pytest.mark.parametrize("bad", [
    {"dim": 1}, {"lock": "no"}, {"every": 0}, {"retries": 11},
    {"backoff": 301}, {"grace": -1}, {"unknown": True}, [],
])
def test_invalid_config_blocks_all_modes(isolated, bad):
    _, path = isolated
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(bad))
    for option in ("--values", "--show"):
        result = run_config(isolated, option)
        assert result.returncode == 1
        assert not result.stdout


def test_atomic_write_preserves_symlink_and_rejects_stale_snapshot(tmp_path):
    target = tmp_path / "actual.json"
    target.write_text('{"dim": false}\n')
    target.chmod(0o640)
    link = tmp_path / "config.json"
    link.symlink_to(target)
    before = target.read_bytes()
    values = config.DEFAULTS | {"dim": True, "lock": True}
    config.write(link, before, values)
    assert link.is_symlink()
    assert json.loads(target.read_text())["dim"] is True
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    with pytest.raises(ValueError, match="changed while editing"):
        config.write(link, before, config.DEFAULTS)
    assert json.loads(target.read_text())["dim"] is True


def test_new_config_private(isolated):
    _, path = isolated
    config.write(path, None, config.DEFAULTS | {"dim": True})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def _pty_config(isolated, inputs, *args, expected_rc=0, columns=100, lines=24):
    env, _ = isolated
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", lines, columns, 0, 0))
    process = subprocess.Popen([sys.executable, str(MODULE), *args], stdin=slave, stdout=slave,
                               stderr=slave, env=env, close_fds=True)
    os.close(slave)
    output = bytearray()
    try:
        for expected, keys in inputs:
            deadline = time.monotonic() + 5
            while expected not in output:
                remaining = deadline - time.monotonic()
                assert remaining > 0, output.decode(errors="replace")
                ready, _, _ = select.select([master], [], [], remaining)
                assert ready, output.decode(errors="replace")
                try:
                    output.extend(os.read(master, 4096))
                except OSError:
                    break
            os.write(master, keys)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break  # Linux reports EIO when the slave closes, before waitpid may reap it.
                if not chunk:
                    break
                output.extend(chunk)
            elif process.poll() is not None:
                break
        rc = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        assert rc == expected_rc, output.decode(errors="replace")
        os.set_blocking(master, False)
        while select.select([master], [], [], 0)[0]:
            try:
                chunk = os.read(master, 4096)
                if not chunk:
                    break
                output.extend(chunk)
            except (OSError, BlockingIOError):
                break
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


def test_tty_menu_cancel_then_save_independent_dim_and_lock(isolated):
    _, path = isolated
    _pty_config(isolated, [(b"Local settings", b"\r"),
                           (b"dim on close", b"q"),
                           (b"Unsaved changes", b"d")])
    assert not path.exists()
    _pty_config(isolated, [(b"Local settings", b"\r"),
                           (b"dim on close", b"j" * 8 + b"\r"),
                           (b"Review changes", b"y")])
    saved = json.loads(path.read_text())
    assert saved["dim"] is True and saved["lock"] is True
    assert run_config(isolated, "--values").stdout.startswith("dim=1\nlock=1\n")


def test_unsaved_confirmation_supports_arrow_selection(isolated):
    _, path = isolated
    _pty_config(isolated, [(b"Local settings", b"\r"),
                           (b"dim on close", b"q"),
                           (b"Unsaved changes", b"\x1b[B\x1b[B\r")])
    assert not path.exists()
    _pty_config(isolated, [(b"Local settings", b"\r"),
                           (b"dim on close", b"q"),
                           (b"Unsaved changes", b"\x1b[B\r"),
                           (b"Review changes", b"y")])
    assert json.loads(path.read_text())["dim"] is True


def test_edit_validates_draft_before_replacing_config(isolated, tmp_path):
    env, path = isolated
    path.parent.mkdir(parents=True)
    path.write_text('{"dim": false}\n')
    original = path.read_bytes()
    editor = tmp_path / "editor.py"
    editor.write_text("import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('{\"lock\": 1}')\n")
    env["EDITOR"] = f"{sys.executable} {editor}"
    output = _pty_config(isolated, [], "--edit", expected_rc=1)
    assert b"invalid edited config" in output
    assert path.read_bytes() == original
    editor.write_text("import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('{\"dim\": true, \"lock\": true}')\n")
    output = _pty_config(isolated, [], "--edit")
    assert b"Saved nosleep defaults" in output
    assert json.loads(path.read_text())["dim"] is True
    path.write_text("{ broken")
    output = _pty_config(isolated, [], "--edit")
    assert b"Saved nosleep defaults" in output
    assert json.loads(path.read_text())["dim"] is True


def test_short_narrow_tty_stacks_values_and_interrupt_restores_terminal(isolated):
    env, path = isolated
    output = _pty_config(isolated, [(b"Local settings", b"q")], columns=42, lines=10)
    assert b"Display when lid closes" in output
    assert b"off on close" in output
    assert not path.exists()
    master, slave = pty.openpty()
    process = subprocess.Popen([sys.executable, str(MODULE)], stdin=slave, stdout=slave,
                               stderr=slave, env=env, close_fds=True)
    os.close(slave)
    try:
        output = bytearray()
        deadline = time.monotonic() + 5
        while b"Local settings" not in output and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                output.extend(os.read(master, 4096))
        assert b"Local settings" in output
        process.send_signal(signal.SIGINT)
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    output.extend(os.read(master, 4096))
                except OSError:
                    break
        assert process.poll() == 0, output.decode(errors="replace")
        assert not path.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
