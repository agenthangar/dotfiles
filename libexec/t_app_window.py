"""macOS window creation for t app, using the app's native menu and Accessibility.

No keystrokes, second app instances, profile changes, or private Codex IPC. Keep
the new AX window reference alive so each handoff can focus that exact window.
"""

import ctypes
import os
import time


class Element:
    def __init__(self, api, ref):
        self.api, self.ref = api, ref

    def __del__(self):
        self.api.cf.CFRelease(self.ref)

    def __eq__(self, other):
        return isinstance(other, Element) and bool(self.api.cf.CFEqual(self.ref, other.ref))


class Accessibility:
    """Small stdlib-only bridge; macOS calls are confined to this class."""

    def __init__(self):
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.ax = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        ptr, integer, boolean = ctypes.c_void_p, ctypes.c_long, ctypes.c_bool
        for lib, name, result, args in [
            (self.cf, "CFRelease", None, [ptr]),
            (self.cf, "CFRetain", ptr, [ptr]),
            (self.cf, "CFEqual", boolean, [ptr, ptr]),
            (self.cf, "CFGetTypeID", ctypes.c_ulong, [ptr]),
            (self.cf, "CFStringGetTypeID", ctypes.c_ulong, []),
            (self.cf, "CFArrayGetTypeID", ctypes.c_ulong, []),
            (self.cf, "CFBooleanGetTypeID", ctypes.c_ulong, []),
            (self.cf, "CFBooleanGetValue", boolean, [ptr]),
            (self.cf, "CFStringCreateWithCString", ptr, [ptr, ctypes.c_char_p, ctypes.c_uint32]),
            (self.cf, "CFStringGetLength", integer, [ptr]),
            (self.cf, "CFStringGetMaximumSizeForEncoding", integer, [integer, ctypes.c_uint32]),
            (self.cf, "CFStringGetCString", boolean, [ptr, ptr, integer, ctypes.c_uint32]),
            (self.cf, "CFArrayGetCount", integer, [ptr]),
            (self.cf, "CFArrayGetValueAtIndex", ptr, [ptr, integer]),
            (self.ax, "AXUIElementGetTypeID", ctypes.c_ulong, []),
            (self.ax, "AXIsProcessTrusted", boolean, []),
            (self.ax, "AXUIElementCreateApplication", ptr, [ctypes.c_int]),
            (self.ax, "AXUIElementCopyAttributeValue", ctypes.c_int, [ptr, ptr, ctypes.POINTER(ptr)]),
            (self.ax, "AXUIElementSetAttributeValue", ctypes.c_int, [ptr, ptr, ptr]),
            (self.ax, "AXUIElementPerformAction", ctypes.c_int, [ptr, ptr]),
        ]:
            function = getattr(lib, name)
            function.restype, function.argtypes = result, args
        if not self.ax.AXIsProcessTrusted():
            raise ValueError("automatic window creation needs Accessibility access for the terminal running t. "
                             "Enable it in System Settings → Privacy & Security → Accessibility, then rerun t app. "
                             "No CLI was stopped.")

    def application(self, pid):
        return Element(self, self.ax.AXUIElementCreateApplication(pid))

    def _string(self, value):
        return self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)

    def _unpack(self, ref):
        kind = self.cf.CFGetTypeID(ref)
        if kind == self.ax.AXUIElementGetTypeID():
            return Element(self, self.cf.CFRetain(ref))
        if kind == self.cf.CFArrayGetTypeID():
            return [self._unpack(self.cf.CFArrayGetValueAtIndex(ref, i))
                    for i in range(self.cf.CFArrayGetCount(ref))]
        if kind == self.cf.CFBooleanGetTypeID():
            return bool(self.cf.CFBooleanGetValue(ref))
        if kind == self.cf.CFStringGetTypeID():
            size = self.cf.CFStringGetMaximumSizeForEncoding(self.cf.CFStringGetLength(ref), 0x08000100) + 1
            buffer = ctypes.create_string_buffer(size)
            if self.cf.CFStringGetCString(ref, buffer, size, 0x08000100):
                return buffer.value.decode()
        return None

    def read(self, element, attribute):
        key, value = self._string(attribute), ctypes.c_void_p()
        try:
            error = self.ax.AXUIElementCopyAttributeValue(element.ref, key, ctypes.byref(value))
            if error in (-25204, -25205, -25212):  # not ready, unsupported, no value
                return None
            if error:
                raise ValueError(f"could not read the app's {attribute} (Accessibility error {error})")
            return self._unpack(value.value) if value.value else None
        finally:
            self.cf.CFRelease(key)
            if value.value:
                self.cf.CFRelease(value.value)

    def press(self, element, action="AXPress"):
        key = self._string(action)
        try:
            error = self.ax.AXUIElementPerformAction(element.ref, key)
            if error:
                raise ValueError(f"app window action {action} failed (Accessibility error {error})")
        finally:
            self.cf.CFRelease(key)

    def activate(self, application):
        key = self._string("AXFrontmost")
        try:
            yes = ctypes.c_void_p.in_dll(self.cf, "kCFBooleanTrue").value
            error = self.ax.AXUIElementSetAttributeValue(application.ref, key, yes)
            if error:
                raise ValueError(f"could not activate the app (Accessibility error {error})")
        finally:
            self.cf.CFRelease(key)


def wait_for(probe, message, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        result = probe()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise ValueError(message)
        time.sleep(.05)


def app_pid(bundle, run):
    result = run(["ps", "-x", "-o", "pid=,comm="], timeout=5)
    if result.returncode:
        raise ValueError("could not locate the desktop app process")
    prefix = os.path.realpath(bundle) + "/Contents/MacOS/"
    matches = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and fields[0].isdigit() and os.path.realpath(fields[1]).startswith(prefix):
            matches.append(int(fields[0]))
    if len(matches) > 1:
        raise ValueError("multiple desktop app processes are running; cannot choose a window safely")
    return matches[0] if matches else None


class Window:
    def __init__(self, api, application, element, original):
        self.api, self.application, self.element, self.original = api, application, element, original

    def focus(self):
        windows = self.api.read(self.application, "AXWindows") or []
        if self.element not in windows or any(old not in windows for old in self.original):
            raise ValueError("the app's windows changed during handoff; no link was sent")
        self.api.activate(self.application)
        self.api.press(self.element, "AXRaise")
        wait_for(lambda: self.api.read(self.application, "AXFocusedWindow") == self.element,
                 "could not focus the new app window; no link was sent", timeout=5)


def new_window(bundle, run, api=None):
    # Check Accessibility BEFORE activating anything or stopping a terminal agent.
    api = api or Accessibility()
    result = run(["open", "-a", bundle], timeout=15)
    if result.returncode:
        raise ValueError("could not launch the desktop app: " + result.stderr.strip())
    pid = wait_for(lambda: app_pid(bundle, run), "the desktop app did not start")
    application = api.application(pid)

    def find_menu():
        bar = api.read(application, "AXMenuBar")
        if bar is None:
            return None
        # Only native menu descendants; never search or click conversation content.
        pending = [(bar, 0)]
        while pending:
            element, depth = pending.pop()
            if api.read(element, "AXTitle") == "New Window" and api.read(element, "AXRole") == "AXMenuItem":
                return element if api.read(element, "AXEnabled") else None
            if depth < 4:
                pending.extend((child, depth + 1) for child in api.read(element, "AXChildren") or [])
        return None

    menu = wait_for(find_menu, "could not find an enabled New Window menu item in the desktop app")
    def snapshot():
        windows = api.read(application, "AXWindows")
        # AX can be temporarily unavailable after launch/activation. Treating
        # that as [] makes existing windows appear newly created after the press.
        # Wrap a successful empty list so wait_for distinguishes it from failure.
        return (windows,) if windows is not None else None

    original, = wait_for(snapshot, "could not read the app's window list; no CLI was stopped")
    api.press(menu)

    def created():
        windows = api.read(application, "AXWindows") or []
        fresh = [window for window in windows if window not in original
                 and api.read(window, "AXSubrole") == "AXStandardWindow"]
        if len(fresh) > 1:
            raise ValueError("multiple new app windows appeared; no handoff link was sent")
        return fresh[0] if fresh else None

    element = wait_for(created, "New Window did not create a distinct app window; no CLI was stopped")
    window = Window(api, application, element, original)
    window.focus()
    return window
