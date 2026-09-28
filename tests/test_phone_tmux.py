"""Exercise the phone shortcut through real clients on an isolated tmux socket.

Small picker stubs isolate config behavior; real bin/t cases cover remembered
profiles and reconnect. No live tmux server, home directory, agent, or clipboard
is touched.
"""

import fcntl
import json
import os
import pathlib
import pty
import select
import shlex
import shutil
import struct
import subprocess
import sys
import termios
import tempfile
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


def eventually(probe, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = probe()
        if result:
            return result
        time.sleep(0.025)
    pytest.fail("timed out waiting for the isolated tmux server")


class PhoneServer:
    def __init__(self, tmp_path):
        # Spaces catch quoting failures through config -> run-shell -> popup.
        self.home = tmp_path / "phone home"
        (self.home / "bin").mkdir(parents=True)
        # macOS sockaddr_un paths are short; pytest's per-test path is too long.
        self.socket_dir = tempfile.TemporaryDirectory(prefix="t-phone-", dir="/tmp")
        self.socket = str(pathlib.Path(self.socket_dir.name) / "phone.sock")
        self.env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "TERM": "xterm-256color", "SHELL": "/bin/sh", "LC_ALL": "C.UTF-8"}
        self.clients = []
        picker = self.home / "bin" / "t"
        picker.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, subprocess, sys
home = pathlib.Path(os.environ["HOME"])
args = sys.argv[1:]
client = args[args.index("--tmux-client") + 1]
pid = args[args.index("--tmux-client-pid") + 1]
(home / ("picker-" + pid)).write_text(json.dumps({"args": args, "tmux": os.environ["TMUX"]}))
choice = sys.stdin.readline().strip()
if choice != "cancel":
    subprocess.run(["tmux", "switch-client", "-c", client, "-t", choice], check=True)
(home / ("done-" + pid)).touch()
''')
        picker.chmod(0o755)
        self.agent = self.home / "agent.py"
        self.agent.write_text('''import os, pathlib, select, sys, tty
root = pathlib.Path(sys.argv[1])
tty.setraw(sys.stdin.fileno())
while True:
    with (root / "heartbeat").open("a") as out:
        out.write(".")
    if select.select([sys.stdin], [], [], 0.025)[0]:
        with (root / "input").open("ab") as out:
            out.write(os.read(sys.stdin.fileno(), 4096))
''')

    def start(self):
        for name in ("dev-a-1", "dev-b-1"):
            root = self.home / name
            root.mkdir()
            cmd = shlex.join([sys.executable, str(self.agent), str(root)])
            opts = ("-f", str(REPO_ROOT / ".tmux.conf")) if name == "dev-a-1" else ()
            self.tmux(*opts, "new-session", "-d", "-s", name, "-c", str(root), cmd)
            eventually(lambda: self.beats(name))

    def tmux(self, *args, check=True):
        return subprocess.run(["tmux", "-S", self.socket, *args], env=self.env,
                              capture_output=True, text=True, check=check)

    def attach(self, name="dev-a-1"):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 44, 0, 0))
        proc = subprocess.Popen(["tmux", "-S", self.socket, "attach-session", "-t", name],
                                stdin=slave, stdout=slave, stderr=slave, env=self.env,
                                start_new_session=True)
        client = {"process": proc, "master": master, "slave": slave, "tty": os.ttyname(slave)}
        self.clients.append(client)
        eventually(lambda: client["tty"] in self.locations())
        return client

    def locations(self):
        return dict(line.split("|", 1) for line in self.tmux(
            "list-clients", "-F", "#{client_name}|#{client_session}").stdout.splitlines())

    def beats(self, name):
        heartbeat = self.home / name / "heartbeat"
        return heartbeat.stat().st_size if heartbeat.exists() else 0

    def open_picker(self, client):
        # Real F1 terminal bytes exercise the root key binding. No send-keys to
        # the pane: that would inject picker input straight into the agent.
        os.write(client["master"], b"\x1bOP")
        record = self.home / ("picker-" + str(client["process"].pid))
        eventually(record.exists)
        return json.loads(record.read_text())

    def choose(self, client, choice):
        os.write(client["master"], choice.encode() + b"\n")
        eventually((self.home / ("done-" + str(client["process"].pid))).exists)

    def no_agent_input(self):
        return all(not (self.home / name / "input").exists() for name in ("dev-a-1", "dev-b-1"))

    def use_real_phone(self):
        if shutil.which("zsh") is None:
            pytest.skip("zsh is not installed")
        # Only transcript metadata is substituted. Its cwd must agree with the
        # actual tmux session; session lookup and client identity stay real.
        (self.home / ".zshrc").write_text('''_dev_session_rows() {
  printf '%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \\
    sid-a "$HOME/dev-a-1" a-1 attached active "Task A" claude \\
    sid-b "$HOME/dev-b-1" b-1 detached active "Task B" codex
}
''')
        (self.home / "bin" / "t").unlink()
        (self.home / "bin" / "t").symlink_to(REPO_ROOT / "bin" / "t")

    def phone(self, profile):
        # A plain SSH shell has no $TMUX. Route native bin/t's tmux calls to our
        # socket through PATH so this startup test cannot reach the live server.
        bindir = self.home / "isolated-bin"
        bindir.mkdir(exist_ok=True)
        wrapper = bindir / "tmux"
        wrapper.write_text("#!/bin/sh\nexec " + shlex.join([
            shutil.which("tmux"), "-S", self.socket]) + ' "$@"\n')
        wrapper.chmod(0o755)
        env = dict(self.env, PATH=str(bindir) + os.pathsep + self.env["PATH"])
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 44, 0, 0))
        proc = subprocess.Popen([sys.executable, str(REPO_ROOT / "bin" / "t"),
                                 "phone", "--client", profile],
                                stdin=slave, stdout=slave, stderr=slave, env=env,
                                start_new_session=True)
        client = {"process": proc, "master": master, "slave": slave, "tty": os.ttyname(slave)}
        self.clients.append(client)
        return client

    def await_screen(self, client, text):
        screen = bytearray()

        def shown():
            if select.select([client["master"]], [], [], 0.025)[0]:
                screen.extend(os.read(client["master"], 65536))
            return text in screen

        eventually(shown)
        return bytes(screen)

    def close(self):
        self.tmux("kill-server", check=False)
        for client in self.clients:
            # An unread test PTY can fill with tmux's final redraw. Closing it
            # first lets the client exit without waiting for a nonexistent UI.
            for key in ("master", "slave"):
                if client[key] is not None:
                    os.close(client[key])
            client["process"].wait(timeout=5)
        self.socket_dir.cleanup()


@pytest.fixture
def phone_server(tmp_path, request):
    server = PhoneServer(tmp_path)
    server.env["LC_ALL"] = getattr(request, "param", "C.UTF-8")
    try:
        server.start()
        yield server
    finally:
        server.close()


def test_f1_passes_initiating_client_and_cancel_leaves_both_agents_running(phone_server):
    s = phone_server
    first, second = s.attach(), s.attach()
    record = s.open_picker(second)
    assert record["args"] == ["phone", "--pick", "--tmux-client", second["tty"],
                              "--tmux-client-pid", str(second["process"].pid)]
    assert record["tmux"].split(",", 1)[0] == s.socket
    before = {name: s.beats(name) for name in ("dev-a-1", "dev-b-1")}
    eventually(lambda: all(s.beats(name) > beats for name, beats in before.items()))
    s.choose(second, "cancel")
    assert s.locations() == {first["tty"]: "dev-a-1", second["tty"]: "dev-a-1"}
    assert s.no_agent_input()


def test_f1_switches_only_its_client_and_disconnect_does_not_stop_work(phone_server):
    s = phone_server
    first, second = s.attach(), s.attach()
    s.open_picker(second)
    s.choose(second, "dev-b-1")
    assert s.locations() == {first["tty"]: "dev-a-1", second["tty"]: "dev-b-1"}
    assert s.no_agent_input()
    # Terminating the viewing clients stands in for losing both SSH transports.
    # Neither the picker nor its reconnect workflow should own the pane process.
    for client in (first, second):
        client["process"].terminate()
        client["process"].wait(timeout=5)
    before = {name: s.beats(name) for name in ("dev-a-1", "dev-b-1")}
    eventually(lambda: all(s.beats(name) > beats for name, beats in before.items()))
    assert not s.locations()
    reconnected = s.attach("dev-b-1")
    assert s.locations() == {reconnected["tty"]: "dev-b-1"}


@pytest.mark.parametrize("phone_server", ["C.UTF-8", "C"], indirect=True)
def test_real_phone_picker_remembers_only_the_initiating_profile_after_switch(phone_server):
    s = phone_server
    s.use_real_phone()
    first, second = s.attach(), s.attach()
    state = s.home / ".local" / "state" / "t" / "phone"
    (state / "connections").mkdir(parents=True)
    (state / "profiles").mkdir()
    server_pid = s.tmux("display-message", "-p", "#{pid}").stdout.strip()
    for n, client in enumerate((first, second), 1):
        profile = f"phone-{n}"
        record = {"profile": profile, "pid": str(client["process"].pid),
                  "name": client["tty"], "server": server_pid, "socket": s.socket}
        (state / "connections" / (record["pid"] + ".json")).write_text(json.dumps(record))
        (state / "profiles" / (profile + ".json")).write_text('{"original": true}\n')

    os.write(second["master"], b"\x1bOP")
    s.await_screen(second, b"Task B")
    os.write(second["master"], b"2")
    selected = state / "profiles" / "phone-2.json"
    eventually(lambda: json.loads(selected.read_text()).get("sid") == "sid-b")
    assert s.locations() == {first["tty"]: "dev-a-1", second["tty"]: "dev-b-1"}
    assert json.loads((state / "profiles" / "phone-1.json").read_text()) == {"original": True}
    remembered = json.loads(selected.read_text())
    assert remembered["agent"] == "codex"
    assert remembered["name"] == "dev-b-1"
    assert s.no_agent_input()


@pytest.mark.parametrize("phone_server", ["C.UTF-8", "C"], indirect=True)
def test_real_phone_reconnects_after_pty_loss_without_restarting_agents(phone_server):
    s = phone_server
    s.use_real_phone()
    pane_pids = s.tmux("list-panes", "-a", "-F", "#{session_name}|#{pane_pid}").stdout
    first = s.phone("phone-1")
    s.await_screen(first, b"Task B")
    os.write(first["master"], b"2")
    eventually(lambda: s.locations().get(first["tty"]) == "dev-b-1")
    state = s.home / ".local" / "state" / "t" / "phone"
    record = json.loads((state / "connections" / (str(first["process"].pid) + ".json")).read_text())
    assert record["profile"] == "phone-1"
    assert record["name"] == first["tty"]
    assert record["socket"] == s.socket
    # Actually close the PTY rather than only requesting a polite tmux detach.
    for key in ("master", "slave"):
        os.close(first[key])
        first[key] = None
    first["process"].wait(timeout=5)
    eventually(lambda: not s.locations())
    before = {name: s.beats(name) for name in ("dev-a-1", "dev-b-1")}
    eventually(lambda: all(s.beats(name) > beats for name, beats in before.items()))

    # No picker input on reconnect: the saved exact conversation is selected.
    second = s.phone("phone-1")
    eventually(lambda: s.locations().get(second["tty"]) == "dev-b-1")
    assert s.tmux("list-panes", "-a", "-F", "#{session_name}|#{pane_pid}").stdout == pane_pids
    assert json.loads((state / "profiles" / "phone-1.json").read_text())["sid"] == "sid-b"
    assert s.no_agent_input()


def test_reconnect_excludes_remembered_task_retained_as_a_dead_pane(phone_server):
    s = phone_server
    s.use_real_phone()
    first = s.phone("phone-1")
    s.await_screen(first, b"Task B")
    os.write(first["master"], b"2")
    eventually(lambda: s.locations().get(first["tty"]) == "dev-b-1")
    for key in ("master", "slave"):
        os.close(first[key])
        first[key] = None
    first["process"].wait(timeout=5)

    # t app keeps its completed pane visible. Its tmux session still exists,
    # but reconnect must offer the remaining running tasks instead of that pane.
    s.tmux("set-option", "-w", "-t", "dev-b-1", "remain-on-exit", "on")
    s.tmux("respawn-pane", "-k", "-t", "dev-b-1", "exit 0")
    eventually(lambda: s.tmux("display-message", "-p", "-t", "dev-b-1",
                               "#{pane_dead}").stdout.strip() == "1")
    second = s.phone("phone-1")
    screen = s.await_screen(second, b"Task A")
    assert b"previous session is no longer running" in screen
    assert b"Task B" not in screen
    assert not s.locations()
    os.write(second["master"], b"1")
    eventually(lambda: s.locations().get(second["tty"]) == "dev-a-1")
    assert s.tmux("display-message", "-p", "-t", "dev-b-1", "#{pane_dead}").stdout.strip() == "1"
