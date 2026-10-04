#!/usr/bin/env python3
"""Machine defaults for nosleep. The file is data, never shell code."""

import json
import os
from pathlib import Path
import select
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import termios
import tty


DEFAULTS = {
    "dim": False,
    "lock": True,
    "forever": False,
    "grace": 900,
    "every": 30,
    "retries": 3,
    "backoff": 30,
}
LABELS = {
    "dim": "Display when lid closes",
    "lock": "Lock when lid closes",
    "forever": "Sleep hold",
    "grace": "Idle/offline grace",
    "every": "Check interval",
    "retries": "Retries",
    "backoff": "Retry delay",
}


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "nosleep" / "config.json"


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("configuration must be a JSON object")
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ValueError("unknown setting: " + ", ".join(sorted(unknown)))
    result = DEFAULTS.copy()
    result.update(data)
    for key in ("dim", "lock", "forever"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key} must be true or false")
    limits = {"grace": (0, None), "every": (1, None),
              "retries": (0, 10), "backoff": (1, 300)}
    for key, (minimum, maximum) in limits.items():
        value = result[key]
        if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
            bound = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
            raise ValueError(f"{key} must be an integer {bound}")
    return result


def read(path=None):
    path = path or config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return DEFAULTS.copy(), None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    return validate(data), raw


def write(path, before, values):
    """Replace only the target of a symlink and only if it has not changed."""
    path = Path(path)
    target = Path(os.path.realpath(path))
    try:
        current = target.read_bytes()
    except FileNotFoundError:
        current = None
    if current != before:
        raise ValueError("config changed while editing; run nosleep config again")
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if current is not None else 0o600
    payload = (json.dumps(validate(values), indent=2) + "\n").encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".nosleep-config-", delete=False) as file:
            temporary = Path(file.name)
            os.fchmod(file.fileno(), mode)
            file.write(payload)
        if (target.read_bytes() if target.exists() else None) != before or Path(os.path.realpath(path)) != target:
            raise ValueError("config changed while editing; run nosleep config again")
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def describe(values):
    return [
        f"  Display     {'dim on close' if values['dim'] else 'off on close'}    (--dim / --no-dim)",
        f"  Lock        {'on close' if values['lock'] else 'skip on close'}    (--lock / --no-lock)",
        f"  Sleep hold  {'until Ctrl-C' if values['forever'] else 'release after idle/offline while closed'}    (--forever / --no-forever)",
        f"  Grace       {values['grace']}s    (--grace SECONDS)",
        f"  Check       {values['every']}s    (--every SECONDS)",
        f"  Retries     {values['retries']}    (--retries COUNT)",
        f"  Backoff     {values['backoff']}s    (--backoff SECONDS)",
    ]


def key():
    try:
        char = os.read(sys.stdin.fileno(), 1)
    except KeyboardInterrupt:
        return "cancel"
    if char == b"\x1b":
        ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        if ready and os.read(sys.stdin.fileno(), 1) == b"[":
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if ready:
                return {b"A": "up", b"B": "down"}.get(os.read(sys.stdin.fileno(), 1), "esc")
        return "esc"
    return {b"\r": "enter", b"\n": "enter", b"\x03": "cancel"}.get(char, char.decode("utf-8", "ignore"))


class Rail:
    """Small inline picker: redraw only its own lines and keep finished steps in scrollback."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.original = termios.tcgetattr(self.fd)
        self.painted = 0
        self.color = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"

    def cols(self):
        return max(20, shutil.get_terminal_size((100, 24)).columns)

    def height(self):
        return max(8, shutil.get_terminal_size((100, 24)).lines)

    def clip(self, text, reserve=0):
        width = max(1, self.cols() - reserve)
        return text if len(text) <= width else text[:max(0, width - 1)] + "…"

    def style(self, text, code):
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def start(self):
        tty.setcbreak(self.fd)
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()

    def stop(self):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.original)
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()

    def repaint(self, lines):
        if self.painted:
            sys.stdout.write(f"\x1b[{self.painted}A\r\x1b[J")
        sys.stdout.write("\n".join(lines) + "\n")
        self.painted = len(lines)
        sys.stdout.flush()

    def commit(self, lines):
        self.repaint(lines)
        self.painted = 0

    def frame(self, title, body, focus, pending=0, hint="↑↓/jk move · enter change · q back", flash="", primary="s  SAVE CHANGES"):
        width = self.cols()
        head = [f"  {self.style('◆', '36')}  {self.style(self.clip(title, 6), '1;36')}",
                f"  {self.style('│', '33')}"]
        footer = [f"  {self.style('│', '33')}",
                  f"      {self.style(self.clip(hint, 6), '33')}"]
        if primary:
            footer.append(f"      {self.style(self.clip(' ' + primary + (f'  ·  {pending} pending' if pending else ''), 6), '1;32')}")
        if flash:
            footer.append(f"      {self.style(self.clip(flash, 6), '33')}")
        if self.height() < 12:
            head = head[:1]
            footer = [f"      {self.style(self.clip(primary or hint, 6), '33')}"]
        physical = []
        for index, (_, label, value) in enumerate(body):
            section = {0: "LID BEHAVIOR", 3: "ACTIVITY CHECKS", 7: "FINISH"}.get(index) if len(body) == 10 else None
            if section:
                physical.append((f"  {self.style('│', '33')}   {self.style(section, '1;36')}", None))
            if width < 60 and value is not None:
                label_text = self.clip(label, 10)
                detail = self.clip(value, 14)
                physical.append((f"  {self.style('│', '33')}  {'▸' if index == focus else ' '} " +
                                 (self.style(label_text, '7;1') if index == focus else label_text), index))
                physical.append((f"  {self.style('│', '33')}      " +
                                 (self.style(detail, '7;1') if index == focus else detail), index))
            else:
                text = self.clip(f"{label:<24} {value}" if value is not None else label, 10)
                if index == focus:
                    text = self.style(text.ljust(max(1, width - 10)), "7;1")
                physical.append((f"  {self.style('│', '33')}  {'▸' if index == focus else ' '} {text}", index))
        capacity = max(2, self.height() - len(head) - len(footer) - 2)
        positions = [i for i, (_, item) in enumerate(physical) if item == focus]
        focus_pos = positions[0] if positions else 0
        start = max(0, min(focus_pos - capacity // 2, len(physical) - capacity))
        start = max(start, (positions[-1] - capacity + 1) if positions else 0)
        end = min(len(physical), start + capacity)
        rows = []
        if start:
            rows.append(f"  {self.style('│', '33')}   ↑ more above")
        rows.extend(line for line, _ in physical[start:end])
        if end < len(physical):
            rows.append(f"  {self.style('│', '33')}   ↓ more below")
        self.repaint(head + rows + footer)

    def prompt_int(self, name, value):
        self.commit([f"  {self.style('◇', '36')}  {LABELS[name]}: {value}", "  │"])
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.original)
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()
        try:
            entered = input("      New value (blank keeps current): ").strip()
        except (EOFError, KeyboardInterrupt):
            entered = ""
        finally:
            tty.setcbreak(self.fd)
            sys.stdout.write("\x1b[?25l")
            sys.stdout.flush()
        if not entered:
            return value, ""
        try:
            return validate({name: int(entered)})[name], ""
        except (ValueError, OverflowError) as exc:
            return value, str(exc)


def _choice(name, values):
    value = values[name]
    if name == "dim": return "dim on close" if value else "off on close"
    if name == "lock": return "lock on close" if value else "skip locking"
    if name == "forever": return "until Ctrl-C" if value else "release after idle/offline while closed"
    return f"{value}{'s' if name != 'retries' else ''}"


def menu(values):
    edited = values.copy()
    names = list(DEFAULTS) + ["defaults", "save", "cancel"]
    selected = 0
    ui = Rail()
    sys.stdout.write("\n  nosleep config\n      Settings for this Mac · changes stay pending until saved.\n\n")
    ui.start()
    try:
        flash = ""
        while True:
            pending = sum(edited[name] != values[name] for name in DEFAULTS)
            body = [(name, LABELS[name], _choice(name, edited)) for name in DEFAULTS]
            body += [("defaults", "Restore defaults", None),
                     ("save", "Save changes", f"{pending} pending"),
                     ("cancel", "Cancel", None)]
            ui.frame("Local settings", body, selected, pending, flash=flash)
            flash = ""
            pressed = key()
            if pressed in ("up", "k"):
                selected = (selected - 1) % len(names)
                continue
            if pressed in ("down", "j"):
                selected = (selected + 1) % len(names)
                continue
            name = "save" if pressed == "s" else names[selected] if pressed == "enter" else "cancel" if pressed in ("q", "esc", "cancel") else None
            if name is None:
                flash = "No binding for that key"
            elif name in ("dim", "lock", "forever"):
                edited[name] = not edited[name]
            elif name in DEFAULTS:
                edited[name], flash = ui.prompt_int(name, edited[name])
            elif name == "defaults":
                edited = DEFAULTS.copy()
            elif name == "cancel":
                if not pending:
                    ui.commit(["  ◇  cancelled", "      nothing written", ""])
                    return None
                options = [("keep", "Keep editing", None),
                           ("save", "Review and save", None),
                           ("discard", "Discard changes and exit", None)]
                confirm_focus = 0
                while True:
                    ui.frame("Unsaved changes", options, confirm_focus,
                             hint="↑↓/jk move · enter select · q/esc keep",
                             primary="s SAVE · d DISCARD")
                    answer = key()
                    if answer in ("up", "k"):
                        confirm_focus = (confirm_focus - 1) % len(options)
                    elif answer in ("down", "j"):
                        confirm_focus = (confirm_focus + 1) % len(options)
                    elif answer in ("q", "esc", "cancel"):
                        break
                    elif answer == "s" or (answer == "enter" and confirm_focus == 1):
                        name = "save"
                        break
                    elif answer == "d" or (answer == "enter" and confirm_focus == 2):
                        name = "discard"
                        break
                    elif answer == "enter":
                        break
                if name == "discard":
                    ui.commit(["  ◇  cancelled", "      nothing written", ""])
                    return None
                if name != "save":
                    continue
            if name == "save":
                if not pending:
                    ui.commit(["  ◇  settings unchanged", ""])
                    return edited
                changes = [(name, LABELS[name], f"{_choice(name, values)} → {_choice(name, edited)}")
                           for name in DEFAULTS if values[name] != edited[name]]
                review_focus = 0
                while True:
                    ui.frame("Review changes", changes, review_focus, pending,
                             hint="↑↓/jk scroll · y / enter save · n / esc back · q cancel",
                             primary="y  SAVE CHANGES")
                    answer = key()
                    if answer in ("up", "k"):
                        review_focus = max(0, review_focus - 1)
                    elif answer in ("down", "j"):
                        review_focus = min(len(changes) - 1, review_focus + 1)
                    elif answer in ("y", "enter"):
                        ui.commit(["  ◇  saving settings", ""])
                        return edited
                    elif answer in ("q", "cancel"):
                        ui.commit(["  ◇  cancelled", "      nothing written", ""])
                        return None
                    elif answer in ("n", "esc"):
                        break
    except KeyboardInterrupt:
        ui.commit(["  ◇  cancelled", "      nothing written", ""])
        return None
    finally:
        ui.stop()


def edit(path, before, values):
    editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi")
    if not editor:
        raise ValueError("VISUAL or EDITOR must name an editor")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nosleep-edit-") as directory:
        draft = Path(directory) / "config.json"
        draft.write_bytes(before if before is not None else (json.dumps(DEFAULTS, indent=2) + "\n").encode())
        if subprocess.call([*editor, str(draft)]) != 0:
            raise ValueError("editor failed; configuration unchanged")
        try:
            updated = validate(json.loads(draft.read_text()))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid edited config: {exc}") from exc
        if values is None or updated != values:
            write(path, before, updated)
            return True
    return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["config"]:
        argv.pop(0)
    if argv in (["--help"], ["-h"]):
        print("usage: nosleep config [--show|--edit]\n"
              "\nOpen an interactive menu to set defaults for the next nosleep run.\n"
              "  --show  Print current defaults\n"
              "  --edit  Edit the JSON configuration in VISUAL/EDITOR")
        return 0
    if len(argv) > 1 or (argv and argv[0] not in ("--show", "--edit", "--values")):
        print("usage: nosleep config [--show|--edit]", file=sys.stderr)
        return 2
    mode = argv[0] if argv else ""
    path = config_path()
    try:
        if mode == "--edit":
            try:
                before = path.read_bytes()
            except FileNotFoundError:
                before = None
            try:
                values = validate(json.loads(before)) if before is not None else DEFAULTS.copy()
            except (ValueError, UnicodeDecodeError):
                values = None  # the draft preserves malformed bytes so the editor can repair them
        else:
            values, before = read(path)
        if mode == "--values":
            for name in DEFAULTS:
                print(f"{name}={int(values[name]) if isinstance(values[name], bool) else values[name]}")
            return 0
        if mode == "--show":
            print("nosleep defaults" + (f" · {path}" if before is not None else " · built in"))
            print("\n".join(describe(values)))
            print("\nRun `nosleep config` to change defaults. Flags override them for one run.")
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("open this menu in a terminal; use `nosleep config --show` to view defaults")
        if mode == "--edit":
            changed = edit(path, before, values)
        else:
            updated = menu(values)
            changed = updated is not None and updated != values
            if changed:
                write(path, before, updated)
        print("Saved nosleep defaults. They apply to the next run." if changed else "Settings unchanged.")
        return 0
    except (OSError, ValueError) as exc:
        print(f"nosleep config: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
