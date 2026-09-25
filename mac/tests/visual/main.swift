// Offscreen synthetic rendering only: no screen capture, activation, clicking or typing.
import AppKit
import SwiftUI

struct OrbPreview: NSViewRepresentable {
    let state: Int
    func makeNSView(context: Context) -> OrbView {
        let view = OrbView(frame: NSRect(x: 0, y: 0, width: 56, height: 56))
        view.isRecording = state == 1
        view.isProcessing = state == 2
        view.isSpeaking = state == 3
        view.hasError = state == 4
        return view
    }
    func updateNSView(_ view: OrbView, context: Context) {}
}

@main
struct VisualReview {
@MainActor static func main() throws {
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
let model = PlaygroundModel { _, _ in }
let fixtureData = try Data(contentsOf: URL(fileURLWithPath: "tests/fixtures/chunk2_canvas.json"))
let fixture = try JSONSerialization.jsonObject(with: fixtureData)
guard let artifact = CanvasArtifact.decode(fixture) else { fatalError("Invalid shared fixture") }
model.receive(["event": "ready", "settings": [:]])
model.receive([
    "event": "conversation", "conversation_id": "synthetic-conversation",
    "items": [["id": "synthetic-conversation", "title": "German word order"]],
    "artifacts": [fixture],
    "messages": [
        ["id": "u", "role": "user", "run_id": "synthetic-run",
         "text": "Why is ‘ich’ after the verb?", "status": "complete"],
        ["id": "a", "role": "assistant", "run_id": "synthetic-run",
         "text": "The verb stays in second position. ‘Heute’ comes first, so ‘ich’ follows the verb.",
         "status": "complete"]
    ]
])

func snapshot<V: View>(_ view: V, name: String, size: NSSize, dark: Bool) throws {
    let host = NSHostingView(rootView: view)
    let window = NSWindow(contentRect: NSRect(origin: NSPoint(x: -20000, y: -20000), size: size),
                          styleMask: .borderless, backing: .buffered, defer: false)
    window.isReleasedWhenClosed = false
    window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
    window.contentView = host
    host.frame = NSRect(origin: .zero, size: size)
    host.layoutSubtreeIfNeeded()
    RunLoop.main.run(until: Date().addingTimeInterval(0.15))
    host.layoutSubtreeIfNeeded()
    guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else {
        fatalError("Cannot allocate offscreen snapshot")
    }
    host.cacheDisplay(in: host.bounds, to: bitmap)
    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        fatalError("Cannot encode offscreen snapshot")
    }
    try png.write(to: output.appendingPathComponent(name + ".png"))
    window.close()
}

for dark in [false, true] {
    let theme = dark ? "dark" : "light"
    try snapshot(HStack(spacing: 0) {
        ForEach(0..<3) { index in
            OrbPreview(state: 2).frame(width: 56, height: 56)
                .frame(width: 160, height: 160)
                .background([Color.white, Color(red: 0.38, green: 0.03, blue: 0.78),
                             Color(red: 0.09, green: 0.09, blue: 0.09)][index])
        }
    }, name: "orb-contrast-" + theme, size: NSSize(width: 480, height: 160), dark: dark)
    try snapshot(AgentPlayground(model: model), name: "app-" + theme,
                 size: NSSize(width: 1120, height: 740), dark: dark)
    try snapshot(CompanionBubbleView(model: model, onExpand: {}, onDismiss: {}),
                 name: "bubble-" + theme, size: NSSize(width: 360, height: 280), dark: dark)
    try snapshot(CanvasPanelView(model: model, artifact: artifact, isPinned: false),
                 name: "canvas-" + theme, size: NSSize(width: 820, height: 900), dark: dark)
    try snapshot(CanvasDesktopSnapshotView(model: model, artifact: artifact),
                 name: "desktop-" + theme, size: NSSize(width: 1120, height: 740), dark: dark)
    try snapshot(HStack(spacing: 34) {
        ForEach(0..<5) { index in
            VStack(spacing: 16) {
                OrbPreview(state: index).frame(width: 56, height: 56)
                Text(["Quiet", "Listening", "Thinking", "Speaking", "Needs you"][index])
                    .font(.system(size: 12))
            }
        }
    }.padding(32).background(Color(nsColor: .windowBackgroundColor)),
    name: "orbs-" + theme, size: NSSize(width: 600, height: 160), dark: dark)
}
print("Offscreen synthetic app/bubble/canvas snapshots saved to \(output.path)")
}
}
