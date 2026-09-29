// Disposable, windowless app for the macOS launch-routing regression.
// Its unique test scheme never registers or handles codex:// URLs.
import AppKit

final class Receiver: NSObject, NSApplicationDelegate {
    var urls: [String] = []
    var ready = false
    var current = ""

    func record() {
        let directory = Bundle.main.bundleURL.deletingLastPathComponent()
        let path = directory.appendingPathComponent("\(ProcessInfo.processInfo.processIdentifier).json")
        let state: [String: Any] = [
            "pid": ProcessInfo.processInfo.processIdentifier,
            "arguments": CommandLine.arguments,
            "urls": urls, "current": current, "ready": ready
        ]
        let data = try! JSONSerialization.data(withJSONObject: state)
        try! data.write(to: path, options: .atomic)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let initial = CommandLine.arguments.dropFirst().last(where: { $0.contains("://threads/") }) {
            current = initial
        }
        ready = true
        record()
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        self.urls += urls.map { $0.absoluteString }
        current = urls.last?.absoluteString ?? current
        record()
    }
}

let app = NSApplication.shared
let receiver = Receiver()
app.delegate = receiver
app.setActivationPolicy(.prohibited)
// A failed/interrupted test cannot leave background receivers running forever.
DispatchQueue.main.asyncAfter(deadline: .now() + 30) { app.terminate(nil) }
app.run()
