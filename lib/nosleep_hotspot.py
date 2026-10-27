#!/usr/bin/env python3
"""Join an explicitly configured, broadcasting hotspot without exposing its password."""

import argparse
import os
import re
import signal
import subprocess
import sys
import time
import unicodedata

SERVICE = "agenthangar.nosleep.hotspot"
NETWORKSETUP = "/usr/sbin/networksetup"
SECURITY = "/usr/bin/security"


class HotspotError(Exception):
    pass


class Cancelled(HotspotError):
    pass


def validate_ssid(ssid):
    if (not isinstance(ssid, str) or not ssid or len(ssid.encode("utf-8")) > 32
            or any(unicodedata.category(char) == "Cc" for char in ssid)):
        raise ValueError("hotspot_ssid must contain 1–32 UTF-8 bytes without control characters")
    return ssid


class Runner:
    """One deadline and an owned process group, including cancellation during spawn."""

    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout
        self.child = None
        self.cancelled = False
        self.closing = False
        self.handlers = {sig: signal.signal(sig, self.cancel) for sig in (signal.SIGINT, signal.SIGTERM)}

    def cancel(self, signum, frame):
        self.cancelled = True
        # During Popen, defer raising until the child is registered. Children
        # inherit normal signal masks, and cancellation cannot lose ownership.
        if self.child is not None and not self.closing:
            raise Cancelled("hotspot attempt cancelled")

    def finish(self):
        try:
            self.close()
        finally:
            for sig, handler in self.handlers.items():
                signal.signal(sig, handler)

    def close(self):
        child = self.child
        if child is None:
            return
        self.closing = True
        # Signal the group even if its leader already exited: a command can leave
        # descendants holding the pipes open. The session/group belongs to us.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        limit = time.monotonic() + 0.2
        try:
            child.communicate(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        # A leader may exit and close pipes while descendants remain. Escalation
        # is based on the owned group, independently of communicate's result.
        while time.monotonic() < limit:
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.communicate()
        self.child = None
        self.closing = False

    def run(self, argv, *, input=None, include_stderr=False):
        remaining = self.deadline - time.monotonic()
        if self.cancelled:
            raise Cancelled("hotspot attempt cancelled")
        if remaining <= 0:
            raise HotspotError("hotspot attempt timed out")
        self.child = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True,
                env={**os.environ, "LC_ALL": "C"})
        if self.cancelled:
            raise Cancelled("hotspot attempt cancelled")
        try:
            output, errors = self.child.communicate(input=input, timeout=max(0.001, self.deadline - time.monotonic()))
            code = self.child.returncode
        except subprocess.TimeoutExpired as exc:
            raise HotspotError("hotspot attempt timed out") from exc
        # Reap/cancel any descendants before dropping ownership of this group.
        self.close()
        if code:
            raise HotspotError("hotspot command failed; check Wi-Fi and unlock Keychain, then rerun nosleep hotspot setup")
        return (output or b"") + (errors if include_stderr else b"")


def connect(ssid, timeout=30, *, networksetup=NETWORKSETUP, security=SECURITY):
    validate_ssid(ssid)
    runner = Runner(timeout)
    try:
        ports = runner.run([networksetup, "-listallhardwareports"]).decode("utf-8", "replace")
        match = re.search(r"Hardware Port: (?:Wi-Fi|AirPort)\r?\nDevice: ([^\s]+)", ports)
        if not match:
            raise HotspotError("no Wi-Fi interface found")
        device = match[1]
        power = runner.run([networksetup, "-getairportpower", device])
        if not re.search(rb":\s*On\s*$", power):
            raise HotspotError("Wi-Fi is off; turn it on before using hotspot fallback")
        password = runner.run([security, "find-generic-password", "-a", ssid, "-s", SERVICE, "-w"])
        if password.endswith(b"\n"):
            password = password[:-1]
        if not password or b"\n" in password or b"\r" in password or b"\0" in password:
            raise HotspotError("hotspot password unavailable; unlock Keychain or run nosleep hotspot setup")
        output = runner.run([networksetup, "-setairportnetwork", device, ssid, "-"],
                            input=password + b"\n", include_stderr=True)
        if re.search(rb"error|failed|could not|cannot|not find|incorrect|unsupported", output, re.I):
            raise HotspotError("could not join hotspot; confirm it is broadcasting and rerun nosleep hotspot setup")
    finally:
        runner.finish()


def setup(*, security=SECURITY):
    import nosleep_config

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise HotspotError("run nosleep hotspot setup in an unlocked Mac terminal")
    values, before = nosleep_config.read()
    print("Use a broadcasting Personal Hotspot. The password is saved in this Mac's Keychain.")
    ssid = validate_ssid(input("Hotspot Wi-Fi name (SSID): "))
    updated = nosleep_config.validate({**values, "hotspot": True, "hotspot_ssid": ssid})
    # Bare -w LAST makes security prompt privately; the password never enters argv,
    # our input handling, environment, configuration, or status output.
    result = subprocess.run([security, "add-generic-password", "-U", "-a", ssid,
                             "-s", SERVICE, "-T", SECURITY, "-w"], timeout=120)
    if result.returncode:
        raise HotspotError("Keychain setup failed; configuration unchanged")
    nosleep_config.write(nosleep_config.config_path(), before, updated)
    nosleep_config.config_path().chmod(0o600)
    print("Hotspot fallback enabled for this Mac. Keep the hotspot broadcasting nearby.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("connect", "setup", "validate"))
    parser.add_argument("--ssid")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 300:
        parser.error("timeout must be 1–300 seconds")

    def cancel(signum, frame):
        raise Cancelled("hotspot attempt cancelled")

    handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        if args.mode == "setup":
            setup()
        elif args.mode == "validate":
            validate_ssid(args.ssid)
        else:
            connect(args.ssid, args.timeout)
        return 0
    except (HotspotError, OSError, ValueError, subprocess.TimeoutExpired, EOFError):
        # Never print subprocess output, argv, SSID, password, or exception data.
        if args.mode == "setup":
            message = "hotspot setup failed; use an unlocked Mac terminal and confirm Keychain access; configuration was not saved"
        elif args.mode == "validate":
            message = "hotspot SSID must contain 1–32 UTF-8 bytes without control characters; run nosleep hotspot setup"
        else:
            message = "hotspot attempt failed; confirm broadcasting Wi-Fi, Wi-Fi permissions and unlocked Keychain, or run nosleep hotspot setup"
        print("nosleep: " + message, file=sys.stderr)
        return 1
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
