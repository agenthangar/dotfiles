// Disposable native-window app for the macOS handoff regression.
// Its unique test scheme never registers or handles codex:// URLs.
import AppKit

final class Receiver: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var urls: [String] = []
    var ready = false
    var current = ""
    var windows: [NSWindow] = []
    var windowURLs: [Int: [String]] = [:]
    var lastWindow: NSWindow?
    var newWindowActions = 0

    func record() {
        let directory = Bundle.main.bundleURL.deletingLastPathComponent()
        let path = directory.appendingPathComponent("\(ProcessInfo.processInfo.processIdentifier).json")
        let state: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier,
            "arguments": CommandLine.arguments,
            "urls": urls, "current": current, "ready": ready,
            "newWindowActions": newWindowActions,
            "windows": windows.map { ["id": $0.windowNumber, "urls": windowURLs[$0.windowNumber] ?? []] as [String: Any] }
        ]
        let data = try! JSONSerialization.data(withJSONObject: state)
        try! data.write(to: path, options: .atomic)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        appItem.submenu = NSMenu()
        appItem.submenu?.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let fileItem = NSMenuItem(title: "File", action: nil, keyEquivalent: "")
        menu.addItem(fileItem)
        fileItem.submenu = NSMenu(title: "File")
        let newItem = NSMenuItem(title: "New Window", action: #selector(newWindow(_:)), keyEquivalent: "")
        newItem.target = self
        fileItem.submenu?.addItem(newItem)
        NSApp.mainMenu = menu
        createWindow()
        if let initial = CommandLine.arguments.dropFirst().last(where: { $0.contains("://threads/") }) {
            current = initial
            windowURLs[lastWindow!.windowNumber] = [initial]
        }
        ready = true
        record()
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        if windows.isEmpty { createWindow() }
        self.urls += urls.map { $0.absoluteString }
        current = urls.last?.absoluteString ?? current
        windowURLs[lastWindow!.windowNumber, default: []] += urls.map { $0.absoluteString }
        record()
    }

    func createWindow() {
        let window = NSWindow(contentRect: NSRect(x: 100, y: 100, width: 360, height: 150),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "t app test — closes automatically"
        window.isReleasedWhenClosed = false
        window.delegate = self
        windows.append(window)
        lastWindow = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func newWindow(_ sender: Any?) {
        newWindowActions += 1
        createWindow()
        record()
    }

    func windowDidBecomeKey(_ notification: Notification) {
        lastWindow = notification.object as? NSWindow
    }
}

let app = NSApplication.shared
let receiver = Receiver()
app.delegate = receiver
app.setActivationPolicy(.regular)
// A failed/interrupted test cannot leave background receivers running forever.
DispatchQueue.main.asyncAfter(deadline: .now() + 60) { app.terminate(nil) }
app.run()
