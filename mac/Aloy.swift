import AppKit
import AVFoundation

// A quiet ink panel and one luminous blue orb; the orb breathes while Aloy speaks.
final class OrbView: NSView {
    var onClick: (() -> Void)?
    var isSpeaking = false { didSet { needsDisplay = true } }
    private var down: NSPoint?
    override var isOpaque: Bool { false }
    override func draw(_ dirtyRect: NSRect) {
        let outer = bounds.insetBy(dx: 2, dy: 2)
        NSColor(calibratedRed: 0.22, green: 0.44, blue: 0.94, alpha: isSpeaking ? 0.34 : 0.18).setFill()
        NSBezierPath(ovalIn: outer).fill()
        let inner = bounds.insetBy(dx: isSpeaking ? 7 : 9, dy: isSpeaking ? 7 : 9)
        NSColor(calibratedRed: 0.29, green: 0.51, blue: 0.98, alpha: 1).setFill()
        NSBezierPath(ovalIn: inner).fill()
        NSColor.white.withAlphaComponent(0.9).setFill()
        NSBezierPath(ovalIn: NSRect(x: bounds.midX - 4, y: bounds.midY - 4, width: 8, height: 8)).fill()
    }
    override func mouseDown(with event: NSEvent) { down = event.locationInWindow }
    override func mouseDragged(with event: NSEvent) {
        guard let previous = down, let window else { return }
        let current = event.locationInWindow
        window.setFrameOrigin(NSPoint(x: window.frame.origin.x + current.x - previous.x,
                                      y: window.frame.origin.y + current.y - previous.y))
    }
    override func mouseUp(with event: NSEvent) {
        if let previous = down {
            let dx = event.locationInWindow.x - previous.x
            let dy = event.locationInWindow.y - previous.y
            if abs(dx) + abs(dy) < 5 { onClick?() }
        }
        down = nil
    }
}

final class AppController: NSObject, NSApplicationDelegate, AVAudioRecorderDelegate,
                           AVAudioPlayerDelegate, NSTextFieldDelegate {
    private var backend: Process?
    private var backendInput: FileHandle?
    private var orbWindow: NSWindow!
    private var panel: NSWindow!
    private var orb: OrbView!
    private var transcript: NSTextView!
    private var entry: NSTextField!
    private var status: NSTextField!
    private var recordButton: NSButton!
    private var historyPicker: NSPopUpButton!
    private var providerPicker: NSPopUpButton!
    private var speechPicker: NSPopUpButton!
    private var modePicker: NSPopUpButton!
    private var conversationID: String?
    private var dataRoot: String?
    private var recorder: AVAudioRecorder?
    private var recordedPath: String?
    private var player: AVAudioPlayer?
    private var audioQueue: [String] = []
    private var replyBuffer = ""
    private var lastAudioPath: String?
    private var currentRunID: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        buildOrb()
        buildPanel()
        startBackend()
    }

    func applicationWillTerminate(_ notification: Notification) {
        recorder?.stop()
        player?.stop()
        backend?.terminate()
    }

    private func label(_ title: String, frame: NSRect, size: CGFloat = 12,
                       weight: NSFont.Weight = .regular) -> NSTextField {
        let field = NSTextField(labelWithString: title)
        field.frame = frame
        field.font = .systemFont(ofSize: size, weight: weight)
        field.textColor = .secondaryLabelColor
        return field
    }

    private func button(_ title: String, frame: NSRect, action: Selector) -> NSButton {
        let button = NSButton(title: title, target: self, action: action)
        button.frame = frame
        button.bezelStyle = .rounded
        return button
    }

    private func buildOrb() {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
        orbWindow = NSWindow(contentRect: NSRect(x: screen.maxX - 84, y: screen.midY, width: 56, height: 56),
                             styleMask: .borderless, backing: .buffered, defer: false)
        orbWindow.level = .floating
        orbWindow.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        orbWindow.backgroundColor = .clear
        orbWindow.isOpaque = false
        orbWindow.hasShadow = false
        orb = OrbView(frame: NSRect(x: 0, y: 0, width: 56, height: 56))
        orb.onClick = { [weak self] in self?.togglePanel() }
        orbWindow.contentView = orb
        orbWindow.orderFrontRegardless()
    }

    private func buildPanel() {
        let rect = NSRect(x: orbWindow.frame.minX - 418, y: orbWindow.frame.minY - 470,
                          width: 400, height: 525)
        panel = NSWindow(contentRect: rect, styleMask: [.titled, .closable, .resizable],
                         backing: .buffered, defer: false)
        panel.title = "Aloy"
        panel.level = .floating
        panel.minSize = NSSize(width: 380, height: 500)
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        let canvas = NSView(frame: NSRect(origin: .zero, size: rect.size))
        canvas.wantsLayer = true
        canvas.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        panel.contentView = canvas

        canvas.addSubview(label("Aloy", frame: NSRect(x: 20, y: 480, width: 120, height: 28),
                                size: 22, weight: .semibold))
        status = label("Starting…", frame: NSRect(x: 20, y: 456, width: 355, height: 18))
        canvas.addSubview(status)

        historyPicker = NSPopUpButton(frame: NSRect(x: 20, y: 421, width: 260, height: 28))
        historyPicker.target = self
        historyPicker.action = #selector(selectConversation)
        canvas.addSubview(historyPicker)
        canvas.addSubview(button("New", frame: NSRect(x: 290, y: 421, width: 82, height: 28),
                                 action: #selector(newConversation)))

        let scroll = NSScrollView(frame: NSRect(x: 20, y: 154, width: 352, height: 255))
        scroll.hasVerticalScroller = true
        scroll.borderType = .noBorder
        transcript = NSTextView(frame: scroll.bounds)
        transcript.isEditable = false
        transcript.isSelectable = true
        transcript.drawsBackground = false
        transcript.font = .systemFont(ofSize: 14)
        transcript.textContainerInset = NSSize(width: 6, height: 8)
        scroll.documentView = transcript
        canvas.addSubview(scroll)

        entry = NSTextField(frame: NSRect(x: 20, y: 115, width: 270, height: 29))
        entry.placeholderString = "Talk to Aloy…"
        entry.delegate = self
        canvas.addSubview(entry)
        canvas.addSubview(button("Send", frame: NSRect(x: 299, y: 115, width: 73, height: 29),
                                 action: #selector(sendText)))
        recordButton = button("Record", frame: NSRect(x: 20, y: 77, width: 75, height: 29),
                              action: #selector(toggleRecording))
        canvas.addSubview(recordButton)
        canvas.addSubview(button("Stop", frame: NSRect(x: 99, y: 77, width: 58, height: 29),
                                 action: #selector(stopAction)))
        canvas.addSubview(button("Replay", frame: NSRect(x: 161, y: 77, width: 65, height: 29),
                                 action: #selector(replayAudio)))
        canvas.addSubview(button("Storage", frame: NSRect(x: 230, y: 77, width: 70, height: 29),
                                 action: #selector(showStorage)))
        canvas.addSubview(button("Delete", frame: NSRect(x: 304, y: 77, width: 68, height: 29),
                                 action: #selector(deleteConversation)))

        providerPicker = NSPopUpButton(frame: NSRect(x: 20, y: 24, width: 122, height: 28))
        providerPicker.addItems(withTitles: ["Gemini Lite", "OpenRouter", "Gemini Flash", "Codex", "Fake"])
        providerPicker.target = self
        providerPicker.action = #selector(settingsChanged)
        canvas.addSubview(providerPicker)
        speechPicker = NSPopUpButton(frame: NSRect(x: 148, y: 24, width: 105, height: 28))
        speechPicker.addItems(withTitles: ["Pocket local", "Gemini voice", "Text only"])
        speechPicker.target = self
        speechPicker.action = #selector(settingsChanged)
        canvas.addSubview(speechPicker)
        modePicker = NSPopUpButton(frame: NSRect(x: 259, y: 24, width: 113, height: 28))
        modePicker.addItems(withTitles: ["Standard", "Live audio"])
        modePicker.target = self
        modePicker.action = #selector(settingsChanged)
        canvas.addSubview(modePicker)
    }

    private func togglePanel() {
        if panel.isVisible { panel.orderOut(nil) }
        else {
            let frame = orbWindow.frame
            panel.setFrameOrigin(NSPoint(x: frame.minX - panel.frame.width - 12,
                                         y: max(50, frame.midY - panel.frame.height / 2)))
            panel.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    private func startBackend() {
        guard let url = Bundle.main.url(forResource: "launch", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let config = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let python = config["python"], let project = config["project"] else {
            status.stringValue = "Build configuration missing"
            return
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-m", "aloy.bridge"]
        process.currentDirectoryURL = URL(fileURLWithPath: project)
        process.environment = ["HOME": NSHomeDirectory(), "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
                               "LANG": "en_US.UTF-8"]
        let input = Pipe(), output = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = Pipe()
        do { try process.run() } catch {
            status.stringValue = "Backend failed to start: \(error.localizedDescription)"
            return
        }
        backend = process
        backendInput = input.fileHandleForWriting
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var buffer = Data()
            while true {
                let chunk = output.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                buffer.append(chunk)
                while let newline = buffer.firstIndex(of: 10) {
                    let line = buffer.prefix(upTo: newline)
                    buffer.removeSubrange(...newline)
                    if let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                        DispatchQueue.main.async { self?.handle(object) }
                    }
                }
            }
            DispatchQueue.main.async { self?.status.stringValue = "Backend stopped" }
        }
    }

    private func command(_ action: String, _ fields: [String: Any] = [:]) {
        var object = fields
        object["v"] = 1
        object["action"] = action
        object["id"] = UUID().uuidString
        guard let data = try? JSONSerialization.data(withJSONObject: object),
              let input = backendInput else { return }
        input.write(data + Data([10]))
    }

    private func append(_ text: String) {
        transcript.string += text
        transcript.scrollToEndOfDocument(nil)
    }

    private func handle(_ object: [String: Any]) {
        guard let event = object["event"] as? String else { return }
        switch event {
        case "ready":
            dataRoot = object["root"] as? String
            let settings = object["settings"] as? [String: String] ?? [:]
            if let provider = settings["provider"],
               let index = ["gemini", "openrouter", "gemini-quality", "codex", "fake"].firstIndex(of: provider) {
                providerPicker.selectItem(at: index)
            }
            if let speech = settings["speech"],
               let index = ["pocket", "gemini", "none"].firstIndex(of: speech) {
                speechPicker.selectItem(at: index)
            }
            if settings["mode"] == "live" { modePicker.selectItem(at: 1) }
            status.stringValue = "Ready"
            command("list")
        case "conversations":
            let items = object["items"] as? [[String: Any]] ?? []
            historyPicker.removeAllItems()
            for item in items {
                historyPicker.addItem(withTitle: item["title"] as? String ?? "Conversation")
                historyPicker.lastItem?.representedObject = item["id"]
            }
            if let first = items.first, let id = first["id"] as? String {
                command("select", ["conversation_id": id])
            } else { command("new") }
        case "conversation":
            conversationID = object["conversation_id"] as? String
            if let id = conversationID {
                if !historyPicker.itemArray.contains(where: { $0.representedObject as? String == id }) {
                    historyPicker.addItem(withTitle: "New conversation")
                    historyPicker.lastItem?.representedObject = id
                }
                if let index = historyPicker.itemArray.firstIndex(where: {
                    $0.representedObject as? String == id
                }) { historyPicker.selectItem(at: index) }
            }
            transcript.string = ""
            lastAudioPath = (object["audio"] as? [[String: Any]])?.last(where: {
                $0["direction"] as? String == "output"
            })?["path"] as? String
            for item in object["messages"] as? [[String: Any]] ?? [] {
                let role = item["role"] as? String == "user" ? "You" : "Aloy"
                let body = item["text"] as? String ?? ""
                append("\(role): \(body)\n\n")
            }
        case "started":
            currentRunID = object["run_id"] as? String
            replyBuffer = ""
            status.stringValue = "Thinking…"
            append("Aloy: ")
        case "live_processing":
            status.stringValue = "Listening and responding…"
        case "delta":
            guard object["run_id"] as? String == currentRunID else { return }
            let text = object["text"] as? String ?? ""
            replyBuffer += text
            append(text)
        case "completed":
            append("\n\n")
            status.stringValue = "Speaking…"
            if object["budget_warning"] as? Bool == true { status.stringValue = "Spending warning: $20+" }
        case "transcript":
            append("You: \(object["text"] as? String ?? "")\n\n")
            status.stringValue = "Thinking…"
        case "audio":
            if let path = object["path"] as? String {
                lastAudioPath = path
                audioQueue.append(path)
                playNext()
            }
        case "turn_done":
            if player?.isPlaying != true && object["failed"] as? Bool != true {
                status.stringValue = "Ready"
            }
            command("list")
        case "storage":
            let bytes = object["bytes"] as? Int ?? 0
            let spend = object["spend_usd"] as? Double ?? 0
            let alert = NSAlert()
            alert.messageText = "Aloy storage"
            alert.informativeText = String(format: "%.1f MB locally · $%.4f estimated this month",
                                           Double(bytes) / 1_000_000, spend)
            alert.runModal()
        case "deleted":
            command("list")
        case "error", "speech_error":
            status.stringValue = object["error"] as? String ?? "Error"
        case "stopped":
            if recorder == nil { status.stringValue = "Ready" }
        default: break
        }
    }

    private func playNext() {
        guard player?.isPlaying != true, !audioQueue.isEmpty else { return }
        let path = audioQueue.removeFirst()
        do {
            player = try AVAudioPlayer(contentsOf: URL(fileURLWithPath: path))
            player?.delegate = self
            player?.play()
            orb.isSpeaking = true
            status.stringValue = "Speaking…"
        } catch { status.stringValue = "Audio playback failed" }
    }
    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        self.player = nil
        orb.isSpeaking = false
        if audioQueue.isEmpty { status.stringValue = "Ready" } else { playNext() }
    }

    private func stopPlayback() {
        player?.stop()
        player = nil
        audioQueue.removeAll()
        orb.isSpeaking = false
    }

    private var provider: String {
        ["gemini", "openrouter", "gemini-quality", "codex", "fake"][providerPicker.indexOfSelectedItem]
    }
    private var speech: String? {
        let choices = ["pocket", "gemini", "none"]
        let selected = choices[speechPicker.indexOfSelectedItem]
        return selected == "none" ? nil : selected
    }

    @objc private func settingsChanged() {
        command("set_setting", ["key": "provider", "value": provider])
        command("set_setting", ["key": "speech", "value": speech ?? "none"])
        command("set_setting", ["key": "mode",
                                "value": modePicker.indexOfSelectedItem == 1 ? "live" : "standard"])
    }

    @objc private func sendText() {
        guard let id = conversationID, !entry.stringValue.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        if modePicker.indexOfSelectedItem == 1 {
            status.stringValue = "Live audio uses Record"
            return
        }
        let text = entry.stringValue
        entry.stringValue = ""
        stopPlayback()
        append("You: \(text)\n\n")
        command("send", ["conversation_id": id, "text": text,
                         "provider": provider, "speech": speech as Any? ?? NSNull()])
    }

    @objc private func toggleRecording() {
        if let recorder {
            recorder.stop()
            self.recorder = nil
            recordButton.title = "Record"
            guard let id = conversationID, let path = recordedPath else { return }
            recordedPath = nil
            command("recorded", ["conversation_id": id, "path": path,
                                 "provider": provider, "speech": speech as Any? ?? NSNull(),
                                 "mode": modePicker.indexOfSelectedItem == 1 ? "live" : "standard"])
            status.stringValue = "Transcribing…"
            return
        }
        guard let root = dataRoot, conversationID != nil else { return }
        stopPlayback()
        command("stop")
        let folder = URL(fileURLWithPath: root).appendingPathComponent("tmp")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent(UUID().uuidString + ".m4a")
        recordedPath = url.path
        do {
            recorder = try AVAudioRecorder(url: url, settings: [
                AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                AVSampleRateKey: 44100.0, AVNumberOfChannelsKey: 1,
                AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
            ])
            recorder?.delegate = self
            recorder?.record(forDuration: 300)
            recordButton.title = "Finish"
            status.stringValue = "Recording… click Finish"
        } catch { status.stringValue = "Microphone unavailable" }
    }

    @objc private func stopAction() {
        recorder?.stop()
        recorder = nil
        if let path = recordedPath { try? FileManager.default.removeItem(atPath: path) }
        recordedPath = nil
        recordButton.title = "Record"
        stopPlayback()
        command("stop")
        status.stringValue = "Stopped"
    }

    @objc private func newConversation() {
        stopPlayback()
        command("stop")
        command("new")
    }
    @objc private func selectConversation() {
        if let id = historyPicker.selectedItem?.representedObject as? String {
            stopPlayback()
            command("stop")
            command("select", ["conversation_id": id])
        }
    }
    @objc private func showStorage() { command("storage") }
    @objc private func replayAudio() {
        guard let path = lastAudioPath else { status.stringValue = "No audio to replay"; return }
        stopPlayback()
        audioQueue.append(path)
        playNext()
    }
    @objc private func deleteConversation() {
        guard let id = conversationID else { return }
        let alert = NSAlert()
        alert.messageText = "Delete this conversation and its recordings?"
        alert.addButton(withTitle: "Delete")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn {
            stopPlayback()
            command("stop")
            command("delete", ["conversation_id": id])
            conversationID = nil
            transcript.string = ""
        }
    }
    func controlTextDidEndEditing(_ notification: Notification) {
        if let text = notification.userInfo?["NSTextMovement"] as? Int,
           text == NSReturnTextMovement { sendText() }
    }
}

let app = NSApplication.shared
let controller = AppController()
app.delegate = controller
app.run()
