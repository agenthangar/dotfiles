"""Exercise consent and sudo failures without invoking real sudo."""
import os
from pathlib import Path
import pty
import select
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_trust(tmp_path, answer="n", interactive=True, platform="Darwin", fail="", **extra):
    log = tmp_path / "sudo.log"
    rule = tmp_path / "rule"
    script = '''
set -eu
source "$HELPER"
uname() { echo "$PLATFORM"; }
id() { echo 987654; }
sudo() {
    printf '%s\\n' "$*" >> "$LOG"
    case "$*" in *"$FAIL"*) [[ -z "$FAIL" ]] || return 1 ;; esac
    if [[ "$1" == /usr/sbin/visudo ]]; then cp "$3" "$RULE"; fi
}
install_sudo_trust
echo completed
'''
    env = {**os.environ, "HELPER": str(ROOT / "lib/sudo-trust.sh"),
           "PLATFORM": platform, "LOG": str(log), "RULE": str(rule),
           "FAIL": fail, "DOTFILES_NO_SUDO": "", "DOTFILES_LINKS_ONLY": "", **extra}
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(["bash", "-c", script], env=env,
                                   stdin=slave if interactive else subprocess.DEVNULL,
                                   stdout=slave, stderr=slave)
        if interactive:
            os.write(master, (answer + "\n").encode())
        process.wait(timeout=10)
        output = b""
        while select.select([master], [], [], 0.2)[0]:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            output += data
    finally:
        os.close(master)
        if slave is not None:
            os.close(slave)
    assert process.returncode == 0, output.decode()
    return output.decode(), log.read_text() if log.exists() else "", rule


@pytest.mark.parametrize("answer", ["n", "", "no", "anything"])
def test_decline_never_uses_sudo(tmp_path, answer):
    output, log, _ = run_trust(tmp_path, answer)
    assert "Enable this access? [y/N]" in output
    assert "Skipped sudo trust" in output
    assert not log


def test_accept_installs_exact_commands_after_validation(tmp_path):
    output, log, rule = run_trust(tmp_path, "yes")
    assert "Enabled passwordless" in output
    calls = log.splitlines()
    assert calls[0].startswith("/usr/sbin/visudo -cf ")
    assert calls[1] == "/usr/bin/install -d -o root -g wheel -m 0755 /etc/sudoers.d"
    assert calls[2].endswith("/etc/sudoers.d/dotfiles-sleep-987654")
    assert "-m 0440" in calls[2]
    assert rule.read_text() == (
        "#987654 ALL=(root) NOPASSWD: /usr/bin/pmset -a disablesleep 1, "
        "/usr/bin/pmset -a disablesleep 0, "
        "/usr/bin/pmset -a disablesleep 0 sleep 10 displaysleep 10 disksleep 10\n"
    )
    assert not Path(calls[0].split()[-1]).exists()


@pytest.mark.parametrize("kwargs", [{"interactive": False}, {"platform": "Linux"},
                                    {"DOTFILES_NO_SUDO": "1"}, {"DOTFILES_LINKS_ONLY": "1"}])
def test_skip_paths(tmp_path, kwargs):
    output, log, _ = run_trust(tmp_path, "yes", **kwargs)
    assert "Enable this access" not in output
    assert not log


def test_validation_failure_does_not_install(tmp_path):
    output, log, _ = run_trust(tmp_path, "y", fail="visudo")
    assert "Could not enable" in output
    assert "completed" in output
    assert "/usr/bin/install" not in log
    assert not Path(log.split()[-1]).exists()
