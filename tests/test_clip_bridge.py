"""Tests for bin/clip-bridge — a tmux copy lands on the clipboard of the machine you
are sitting at (the laptop), not the one tmux runs on (the mini).

The script's decisions are all silent when wrong (a copy that quietly lands on the
wrong machine's clipboard is the very bug), so each branch is driven for real: stub
`ps`/`lsof` on PATH answer from fixture tables, a real TCP listener on a free port
stands in for the laptop's launchd pbcopy, and a fake `pbcopy` records a local copy.
The three places that spell the ports out (the script's pool, ssh/config's
RemoteForwards, the launchd plist) are pinned against each other.
"""

import os
import pathlib
import plistlib
import re
import socket
import subprocess
import threading

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "bin" / "clip-bridge"
PLIST = REPO_ROOT / "launchd" / "com.chrisobrien-ai.clip-bridge.plist"
SSH_CONFIG = REPO_ROOT / "ssh" / "config"


class Listener:
    """A one-shot TCP server on 127.0.0.1 that records what it is sent — the laptop's
    socket-activated pbcopy, as seen from the far end of the tunnel."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.received = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        self.sock.settimeout(10)
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            data = b""
            while chunk := conn.recv(4096):
                data += chunk
        self.received.append(data.decode())

    def close(self):
        self.sock.close()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def box(tmp_path):
    """A sandbox: stub bin dir first on PATH, a fake pbcopy, fixture tables."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    clip = tmp_path / "clipboard.txt"
    (bindir / "pbcopy").write_text(f'#!/bin/sh\ncat > "{clip}"\n')
    # ps answers `ps -o comm= -p P` / `ps -o ppid= -p P` from "pid ppid comm" lines.
    (tmp_path / "ps.txt").write_text("1 0 launchd\n")
    (bindir / "ps").write_text(
        "#!/bin/sh\n"
        'field=$2; pid=$4\n'
        f'awk -v pid="$pid" -v f="$field" \'$1 == pid {{ if (f == "ppid=") print $2; '
        f'else {{ $1 = ""; $2 = ""; sub(/^  /, ""); print }} }}\' "{tmp_path}/ps.txt"\n'
    )
    (tmp_path / "lsof.txt").write_text("")
    (bindir / "lsof").write_text(f'#!/bin/sh\ncat "{tmp_path}/lsof.txt"\n')
    for f in ("pbcopy", "ps", "lsof"):
        (bindir / f).chmod(0o755)

    def run(*args, stdin="", **env):
        e = {
            "PATH": f"{bindir}:/usr/bin:/bin",
            "HOME": str(tmp_path / "home"),
            "TMPDIR": str(tmp_path),
            **env,
        }
        return subprocess.run([str(SCRIPT), *args], input=stdin, env=e,
                              capture_output=True, text=True, timeout=20)

    class Box:
        pass

    b = Box()
    b.path, b.run, b.clip = tmp_path, run, clip
    b.ps = tmp_path / "ps.txt"
    b.lsof = tmp_path / "lsof.txt"
    return b


def _lsof_rows(*conns):
    """lsof -FpTn output for (pid, peer_ip, [listen ports]) tuples."""
    out = []
    for pid, peer, ports in conns:
        out += [f"p{pid}", "n100.123.117.5:22->%s:50623" % peer, "TST=ESTABLISHED"]
        for port in ports:
            out += [f"n127.0.0.1:{port}", "TST=LISTEN", f"n[::1]:{port}", "TST=LISTEN"]
    return "\n".join(out) + "\n"


# --- copy: which clipboard -------------------------------------------------------


def test_local_client_copies_with_pbcopy(box):
    box.ps.write_text("1 0 launchd\n100 1 Terminal\n200 100 -zsh\n300 200 tmux\n")
    r = box.run("copy", stdin="LOCAL-COPY", CLIP_BRIDGE_CLIENT_PID="300")
    assert r.returncode == 0, r.stderr
    assert box.clip.read_text() == "LOCAL-COPY"


def test_ssh_client_copies_through_its_own_tunnel(box):
    """The reported bug: attached to the mini from the laptop, a copy must go back
    over THAT connection's forwarded port, not into the mini's pbcopy."""
    lis = Listener()
    try:
        box.ps.write_text(
            "1 0 launchd\n50812 1 sshd-session: me [priv]\n"
            "50815 50812 sshd-session: me@ttys000\n50816 50815 -zsh\n50900 50816 tmux\n"
        )
        box.lsof.write_text(_lsof_rows((50815, "100.80.236.32", [lis.port])))
        r = box.run("copy", stdin="REMOTE-COPY", CLIP_BRIDGE_CLIENT_PID="50900",
                    CLIP_BRIDGE_PORTS=f"{lis.port}-{lis.port}")
        assert r.returncode == 0, r.stderr
        lis.thread.join(5)
        assert lis.received == ["REMOTE-COPY"]
        assert not box.clip.exists(), "a remote copy must not also land on the mini"
    finally:
        lis.close()


def test_falls_back_to_another_connection_from_the_same_peer(box):
    """Pool exhausted, or this connection lost the race for a port: another
    connection from the SAME laptop carries it just as well."""
    lis = Listener()
    try:
        box.ps.write_text("1 0 launchd\n10 1 sshd-session: me@ttys001\n11 10 tmux\n")
        box.lsof.write_text(_lsof_rows(
            (10, "100.80.236.32", []),
            (20, "100.80.236.32", [lis.port]),
        ))
        r = box.run("copy", stdin="SIBLING", CLIP_BRIDGE_CLIENT_PID="11",
                    CLIP_BRIDGE_PORTS=f"{lis.port}-{lis.port}")
        assert r.returncode == 0, r.stderr
        lis.thread.join(5)
        assert lis.received == ["SIBLING"]
    finally:
        lis.close()


def test_never_sends_to_a_different_peers_tunnel(box):
    """A copy made from the phone (no tunnel of its own) must not land on the
    laptop's clipboard just because the laptop is also connected."""
    lis = Listener()
    try:
        box.ps.write_text("1 0 launchd\n10 1 sshd-session: me@ttys001\n11 10 tmux\n")
        box.lsof.write_text(_lsof_rows(
            (10, "100.99.1.2", []),                 # the phone
            (20, "100.80.236.32", [lis.port]),      # the laptop
        ))
        r = box.run("copy", stdin="PHONE", CLIP_BRIDGE_CLIENT_PID="11",
                    CLIP_BRIDGE_PORTS=f"{lis.port}-{lis.port}")
        assert r.returncode == 0, r.stderr
        assert box.clip.read_text() == "PHONE"
        assert lis.received == []
    finally:
        lis.close()


def test_a_dead_tunnel_falls_back_to_the_local_clipboard(box):
    """A port lsof still lists but nothing answers (the connection just closed):
    the copy is not lost."""
    port = _free_port()
    box.ps.write_text("1 0 launchd\n10 1 sshd-session: me@ttys001\n11 10 tmux\n")
    box.lsof.write_text(_lsof_rows((10, "100.80.236.32", [port])))
    r = box.run("copy", stdin="NOBODY-HOME", CLIP_BRIDGE_CLIENT_PID="11",
                CLIP_BRIDGE_PORTS=f"{port}-{port}")
    assert r.returncode == 0, r.stderr
    assert box.clip.read_text() == "NOBODY-HOME"


def test_ports_outside_the_pool_are_ignored(box):
    """Other forwards on the same connection (a dev server's -R) are not ours."""
    lis = Listener()
    try:
        box.ps.write_text("1 0 launchd\n10 1 sshd-session: me@ttys001\n11 10 tmux\n")
        box.lsof.write_text(_lsof_rows((10, "100.80.236.32", [lis.port])))
        r = box.run("copy", stdin="NOT-OURS", CLIP_BRIDGE_CLIENT_PID="11",
                    CLIP_BRIDGE_PORTS="1-2")
        assert r.returncode == 0, r.stderr
        assert box.clip.read_text() == "NOT-OURS"
        assert lis.received == []
    finally:
        lis.close()


# --- ssh-match: which connections carry the tunnel -------------------------------


def _match_home(box, listener=True, hosts=("me@mini", '"Box-Two.local"')):
    home = box.path / "home"
    if listener:
        agents = home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / PLIST.name).write_text("<plist/>\n")
    cfg = home / ".config" / "t"
    cfg.mkdir(parents=True)
    lines = ["DEV_REPOS[dot]=/x/dotfiles"]
    lines += [f"REMOTE_HOSTS[h{i}]={h}" for i, h in enumerate(hosts)]
    (cfg / "config.sh").write_text("\n".join(lines) + "\n")


@pytest.mark.parametrize("host,rc", [
    ("mini", 0), ("MINI", 0), ("box-two.local", 0),
    ("github.com", 1), ("mini.example.com", 1), ("", 1),
])
def test_ssh_match_only_our_remote_hosts(box, host, rc):
    _match_home(box)
    assert box.run("ssh-match", host).returncode == rc


def test_ssh_match_says_no_without_the_listener(box):
    """No launchd listener here (Linux, opted out) = nothing for the tunnel to reach."""
    _match_home(box, listener=False)
    assert box.run("ssh-match", "mini").returncode == 1


def test_ssh_match_reads_the_config_bridge_through_xdg(box):
    _match_home(box)
    other = box.path / "xdg"
    (other / "t").mkdir(parents=True)
    (other / "t" / "config.sh").write_text("REMOTE_HOSTS[x]=elsewhere\n")
    assert box.run("ssh-match", "mini", XDG_CONFIG_HOME=str(other)).returncode == 1
    assert box.run("ssh-match", "elsewhere", XDG_CONFIG_HOME=str(other)).returncode == 0


# --- the three spellings of the ports agree --------------------------------------


def test_ports_agree_across_script_ssh_config_and_plist():
    src = SCRIPT.read_text()
    lo, hi = map(int, re.search(r'CLIP_BRIDGE_PORTS:-(\d+)-(\d+)', src).groups())
    listen = int(plistlib.loads(PLIST.read_bytes())["Sockets"]["Listener"]["SockServiceName"])
    fwd = re.findall(r"^\s*RemoteForward\s+(\d+)\s+127\.0\.0\.1:(\d+)\s*$",
                     SSH_CONFIG.read_text(), re.M)
    assert [int(r) for r, _ in fwd] == list(range(lo, hi + 1))
    assert {int(t) for _, t in fwd} == {listen}
    assert listen not in range(lo, hi + 1)


def test_plist_is_a_socket_activated_pbcopy_on_loopback():
    p = plistlib.loads(PLIST.read_bytes())
    assert p["Label"] == PLIST.stem
    assert p["ProgramArguments"] == ["/usr/bin/pbcopy"]
    assert p["inetdCompatibility"] == {"Wait": False}
    assert p["Sockets"]["Listener"]["SockNodeName"] == "127.0.0.1"


def test_ssh_config_scopes_the_tunnel_to_clip_bridges_match():
    """The forwards must sit under the Match, never under `Host *` — every git push
    to github.com would otherwise request them."""
    text = SSH_CONFIG.read_text()
    match_at = text.index('Match exec "test -x ~/bin/clip-bridge && ~/bin/clip-bridge ssh-match %h"')
    assert text.index("RemoteForward") > match_at
    assert "Host " not in text[match_at:], "a later Host block would re-scope the forwards"


# --- install.sh's install_clip_bridge --------------------------------------------

from test_install_codex_hooks import box as install_box, relink  # noqa: E402,F401


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="the listener is macOS-only")
def test_install_copies_the_plist_but_never_loads_a_sandbox_one(install_box):
    """launchctl has no notion of $HOME, so a sandboxed install must stop short of
    bootstrapping — it would load the sandbox's plist into the REAL gui domain."""
    co, home = install_box
    (co / "launchd").mkdir(exist_ok=True)
    (co / "launchd" / PLIST.name).write_bytes(PLIST.read_bytes())
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    dst = home / "Library" / "LaunchAgents" / PLIST.name
    assert dst.is_file() and not dst.is_symlink()
    assert dst.read_bytes() == PLIST.read_bytes()
    assert "not loaded" in r.stdout
    assert (home / "bin" / "clip-bridge").is_symlink()
    # unchanged on the next dots: silent
    r = relink(co, home)
    assert r.returncode == 0, r.stderr
    assert "clip-bridge" not in r.stdout


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="the listener is macOS-only")
def test_install_opt_out(install_box):
    co, home = install_box
    (co / "launchd").mkdir(exist_ok=True)
    (co / "launchd" / PLIST.name).write_bytes(PLIST.read_bytes())
    r = relink(co, home, DOTFILES_NO_CLIP_BRIDGE="1")
    assert r.returncode == 0, r.stderr
    assert not (home / "Library" / "LaunchAgents" / PLIST.name).exists()
