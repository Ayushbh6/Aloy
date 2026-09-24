import AppKit
import AVFoundation
import Carbon

// A quiet ink panel and one luminous blue orb; the orb breathes while Aloy speaks.
final class OrbView: NSView {
    var onClick: (() -> Void)?
    var isRecording = false { didSet { needsDisplay = true } }
    var isSpeaking = false { didSet { needsDisplay = true } }
    private var gesture = OrbGesture()
    override var isOpaque: Bool { false }
    override func draw(_ dirtyRect: NSRect) {
        let outer = bounds.insetBy(dx: 2, dy: 2)
        NSColor(calibratedRed: 0.22, green: 0.44, blue: 0.94, alpha: isSpeaking ? 0.34 : 0.18).setFill()
        NSBezierPath(ovalIn: outer).fill()
        let inner = bounds.insetBy(dx: isSpeaking ? 7 : 9, dy: isSpeaking ? 7 : 9)
        (isRecording ? NSColor.systemRed : NSColor(calibratedRed: 0.29, green: 0.51, blue: 0.98, alpha: 1)).setFill()
        NSBezierPath(ovalIn: inner).fill()
        NSColor.white.withAlphaComponent(0.9).setFill()
        NSBezierPath(ovalIn: NSRect(x: bounds.midX - 4, y: bounds.midY - 4, width: 8, height: 8)).fill()
    }
    override func mouseDown(with event: NSEvent) {
        gesture.begin(at: window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation, windowOrigin: window?.frame.origin ?? .zero)
    }
    override func mouseDragged(with event: NSEvent) {
        gesture.markDragged()
        let point = window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation
        if let origin = gesture.move(to: point) { window?.setFrameOrigin(origin) }
    }
    override func mouseUp(with event: NSEvent) {
        if gesture.end(at: window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation) { onClick?() }
    }

}

final class AppController: NSObject, NSApplicationDelegate, AVAudioRecorderDelegate,
                           AVAudioPlayerDelegate, NSTextFieldDelegate {
    private var hotKey: EventHotKeyRef?
    private var hotKeyHandler: EventHandlerRef?
    private var shortcutGesture = ShortcutGesture()
    private var backend: Process?
    private var backendInput: FileHandle?
    private var orbWindow: NSWindow!
    private var panel: NSWindow!
    private var orb: OrbView!
    private var transcript: NSTextView!
    private var entry: NSTextField!
    private var status: NSTextField!
    private var recordButton: NSButton!
    private var stopButton: NSButton!
    private var historyPicker: NSPopUpButton!
    private var providerPicker: NSPopUpButton!
    private var speechPicker: NSPopUpButton!
    private var modePicker: NSPopUpButton!
    private var conversationID: String?
    private var dataRoot: String?
    private var recorder: AVAudioRecorder?
    private var recordedPath: String?
    private var microphoneRequestPending = false
    private var player: AVAudioPlayer?
    private var audioQueue: [(path: String, id: String)] = []
    private var inputReplay: (path: String, id: String)?
    private var replayAssets: [(path: String, id: String)] = []
    private var playingAssetID: String?
    private var lastAudioAssetID: String?
    private var operation = OperationGate()
    private var replyBuffer = ""
    private var lastAudioPath: String?
    private var currentRunID: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        buildOrb()
        buildPanel()
        startBackend()
        registerVoiceShortcut()
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let hotKey { UnregisterEventHotKey(hotKey) }
        if let hotKeyHandler { RemoveEventHandler(hotKeyHandler) }
        recorder?.stop()
        // Unfinished capture remains in Aloy/tmp for recovery on next launch.
        player?.stop()
        backend?.terminate()
    }

    private func registerVoiceShortcut() {
        var events = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased))
        ]
        let context = Unmanaged.passUnretained(self).toOpaque()
        let installed = InstallEventHandler(GetApplicationEventTarget(), { _, event, context in
            guard let event, let context else { return OSStatus(eventNotHandledErr) }
            let owner = Unmanaged<AppController>.fromOpaque(context).takeUnretainedValue()
            if GetEventKind(event) == UInt32(kEventHotKeyPressed) {
                owner.shortcutGesture.down()
            } else if owner.shortcutGesture.up() {
                owner.toggleRecording()
            }
            return noErr
        }, events.count, &events, context, &hotKeyHandler)
        let registered = installed == noErr ? RegisterEventHotKey(
            UInt32(kVK_Space), UInt32(optionKey),
            EventHotKeyID(signature: 0x414C4F59, id: 1),
            GetApplicationEventTarget(), 0, &hotKey
        ) : installed
        if registered != noErr {
            status.stringValue = "Option–Space unavailable; use Record"
            let alert = NSAlert()
            alert.messageText = "Aloy could not register Option–Space"
            alert.informativeText = "Another application may own this shortcut. Record and Finish & Send still work in Aloy's panel."
            alert.runModal()
        }
        orb.toolTip = "Option–Space: record / send. Click to open Aloy."
        recordButton.toolTip = "Option–Space works from other apps too"
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
        panel.isReleasedWhenClosed = false
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

        let scroll = NSScrollView(frame: NSRect(x: 20, y: 188, width: 352, height: 221))
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

        entry = NSTextField(frame: NSRect(x: 20, y: 147, width: 270, height: 29))
        entry.placeholderString = "Talk to Aloy…"
        entry.delegate = self
        canvas.addSubview(entry)
        canvas.addSubview(button("Send", frame: NSRect(x: 299, y: 147, width: 73, height: 29),
                                 action: #selector(sendText)))
        recordButton = button("Record", frame: NSRect(x: 20, y: 109, width: 166, height: 29),
                              action: #selector(toggleRecording))
        canvas.addSubview(recordButton)
        stopButton = button("Stop", frame: NSRect(x: 195, y: 109, width: 177, height: 29),
                            action: #selector(stopAction))
        canvas.addSubview(stopButton)
        let replayButton = button("Replay", frame: NSRect(x: 20, y: 73, width: 108, height: 29),
                                  action: #selector(replayAudio))
        replayButton.toolTip = "Replay the last reply. Right-click to replay your last recording."
        let replayMenu = NSMenu()
        let replayInput = NSMenuItem(title: "Replay last recording", action: #selector(replayInputAudio), keyEquivalent: "")
        replayInput.target = self
        replayMenu.addItem(replayInput)
        replayButton.menu = replayMenu
        canvas.addSubview(replayButton)
        canvas.addSubview(button("Storage", frame: NSRect(x: 142, y: 73, width: 108, height: 29),
                                 action: #selector(showStorage)))
        canvas.addSubview(button("Delete", frame: NSRect(x: 264, y: 73, width: 108, height: 29),
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
        let errors = Pipe()
        process.standardError = errors
        // Drain stderr so dependency diagnostics cannot block the subprocess.
        errors.fileHandleForReading.readabilityHandler = { handle in
            if handle.availableData.isEmpty { handle.readabilityHandler = nil }
        }
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
        object["operation_id"] = operation.id
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
        if let id = object["operation_id"] as? String, !operation.accepts(id) { return }
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
            if let input = (object["audio"] as? [[String: Any]])?.last(where: { $0["direction"] as? String == "input" }),
               let path = input["path"] as? String, let id = input["id"] as? String {
                inputReplay = (path, id)
            } else { inputReplay = nil }
            let outputAssets = (object["audio"] as? [[String: Any]] ?? []).filter { $0["direction"] as? String == "output" }
            let latestRun = outputAssets.last?["run_id"] as? String
            replayAssets = outputAssets.filter { $0["run_id"] as? String == latestRun }.compactMap {
                guard let path = $0["path"] as? String, let id = $0["id"] as? String else { return nil }
                return (path, id)
            }
            lastAudioAssetID = (object["audio"] as? [[String: Any]])?.last(where: {
                $0["direction"] as? String == "output"
            })?["id"] as? String
            for item in object["messages"] as? [[String: Any]] ?? [] {
                let role = item["role"] as? String == "user" ? "You" : "Aloy"
                let body = item["text"] as? String ?? ""
                let completion = item["status"] as? String ?? "complete"
                let marker = completion == "complete" ? "" : " [\(completion)]"
                append("\(role)\(marker): \(body)\n\n")
            }
        case "started":
            replayAssets.removeAll()
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
            if let path = object["path"] as? String, let id = object["asset_id"] as? String {
                lastAudioPath = path
                lastAudioAssetID = id
                replayAssets.append((path, id))
                audioQueue.append((path, id))
                playNext()
            }
        case "turn_done":
            if player?.isPlaying != true && object["failed"] as? Bool != true {
                status.stringValue = "Ready"
            }
            command("list")
        case "recording_retained":
            status.stringValue = "Saved locally. Right-click Replay to hear it. Not sent."
            if let id = conversationID { command("select", ["conversation_id": id]) }
        case "storage":
            let bytes = object["bytes"] as? Int ?? 0
            let spend = object["spend_usd"] as? Double ?? 0
            let alert = NSAlert()
            alert.messageText = "Aloy storage"
            alert.informativeText = String(format: "%.1f MB locally · $%.4f estimated this month",
                                           Double(bytes) / 1_000_000, spend)
            let orphans = object["retained_orphans"] as? Int ?? 0
            if orphans > 0 { alert.informativeText += " · \(orphans) recovered files retained" }
            alert.runModal()
        case "deleted":
            command("list")
        case "error", "speech_error":
            stopPlayback()
            status.stringValue = object["error"] as? String ?? "Error"
        case "stopped":
            if recorder == nil { status.stringValue = "Ready" }
        default: break
        }
    }

    private func playNext() {
        guard player?.isPlaying != true, !audioQueue.isEmpty else { return }
        let asset = audioQueue.removeFirst()
        playingAssetID = asset.id
        do {
            player = try AVAudioPlayer(contentsOf: URL(fileURLWithPath: asset.path))
            player?.delegate = self
            guard player?.play() == true else {
                player = nil
                command("playback", ["asset_id": asset.id, "status": "failed"])
                status.stringValue = "Audio playback could not start"
                return
            }
            command("playback", ["asset_id": asset.id, "status": "playing"])
            orb.isSpeaking = true
            status.stringValue = "Speaking…"
        } catch {
            command("playback", ["asset_id": asset.id, "status": "failed"])
            status.stringValue = "Audio playback failed"
        }
    }
    func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        if let id = playingAssetID { command("playback", ["asset_id": id, "status": flag ? "played" : "failed"]) }
        playingAssetID = nil
        self.player = nil
        orb.isSpeaking = false
        if !flag { status.stringValue = "Audio playback stopped unexpectedly" }
        else if audioQueue.isEmpty { status.stringValue = "Ready" }
        else { playNext() }
    }

    private func stopPlayback() {
        let began = ProcessInfo.processInfo.systemUptime
        let wasPlaying = player?.isPlaying == true
        player?.stop()
        player = nil
        let cancelled = audioQueue
        audioQueue.removeAll()
        orb.isSpeaking = false
        let elapsed = (ProcessInfo.processInfo.systemUptime - began) * 1000
        if let id = playingAssetID { command("playback", ["asset_id": id, "status": "cancelled"]) }
        playingAssetID = nil
        for asset in cancelled { command("playback", ["asset_id": asset.id, "status": "cancelled"]) }
        if wasPlaying { command("metric", ["kind": "playback_stop_ms", "value": elapsed]) }
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
        stopAction()
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
        operation.invalidate()
        let text = entry.stringValue
        entry.stringValue = ""
        stopPlayback()
        append("You: \(text)\n\n")
        command("send", ["conversation_id": id, "text": text,
                         "provider": provider, "speech": speech as Any? ?? NSNull()])
    }

    @objc private func toggleRecording() {
        if recorder != nil { finishRecording(); return }
        guard dataRoot != nil, conversationID != nil, !microphoneRequestPending else { NSSound.beep(); return }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            startRecording()
        case .notDetermined:
            microphoneRequestPending = true
            recordButton.isEnabled = false
            status.stringValue = "Waiting for microphone permission…"
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] granted in
                DispatchQueue.main.async {
                    guard let self, self.microphoneRequestPending else { return }
                    self.microphoneRequestPending = false
                    self.recordButton.isEnabled = true
                    if granted { self.startRecording() }
                    else { self.status.stringValue = "Microphone access was not granted" }
                }
            }
        case .denied, .restricted:
            status.stringValue = "Enable Aloy microphone access in System Settings"
        @unknown default:
            status.stringValue = "Microphone permission unavailable"
        }
    }

    private func finishRecording() {
        let previous = recorder
        recorder = nil
        orb.isRecording = false
        orb.toolTip = "Processing — Option–Space to start a new recording"
        previous?.stop()
        recordButton.title = "Record"
        stopButton.title = "Stop"
        guard let id = conversationID, let path = recordedPath else { return }
        recordedPath = nil
        command("recorded", ["conversation_id": id, "path": path,
                             "provider": provider, "speech": speech as Any? ?? NSNull(),
                             "mode": modePicker.indexOfSelectedItem == 1 ? "live" : "standard"])
        status.stringValue = "Processing recording…"
    }

    func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        guard self.recorder === recorder else { return }
        if flag { finishRecording() }
        else { stopAction(); status.stringValue = "Recording failed" }
    }

    private func startRecording() {
        guard let root = dataRoot, conversationID != nil else { return }
        operation.invalidate()
        stopPlayback()
        command("stop")
        let folder = URL(fileURLWithPath: root).appendingPathComponent("tmp")
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent(UUID().uuidString + ".m4a")
        do {
            let candidate = try AVAudioRecorder(url: url, settings: [
                AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
                AVSampleRateKey: 44100.0, AVNumberOfChannelsKey: 1,
                AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
            ])
            guard candidate.record(forDuration: 300) else {
                try? FileManager.default.removeItem(at: url)
                status.stringValue = "Microphone could not start recording"
                return
            }
            candidate.delegate = self
            recorder = candidate
            orb.isRecording = true
            orb.toolTip = "Recording — Option–Space to send"
            recordedPath = url.path
            recordButton.title = "Finish & Send"
            status.stringValue = "Recording… Finish & Send, or Cancel recording"
            stopButton.title = "Cancel recording"
        } catch {
            try? FileManager.default.removeItem(at: url)
            status.stringValue = "Microphone unavailable"
        }
    }

    @objc private func stopAction() {
        operation.invalidate()
        currentRunID = nil
        microphoneRequestPending = false
        recordButton.isEnabled = true
        let duration = recorder?.currentTime ?? 0
        let previousRecorder = recorder
        recorder = nil
        orb.isRecording = false
        previousRecorder?.stop()
        let cancelledPath = recordedPath
        recordedPath = nil
        recordButton.title = "Record"
        stopButton.title = "Stop"
        stopPlayback()
        command("stop")
        status.stringValue = "Stopped"
        if let path = cancelledPath, let id = conversationID {
            command("cancel_recording", ["conversation_id": id, "path": path, "duration": duration])
        }
    }

    @objc private func newConversation() {
        stopAction()
        command("new")
    }
    @objc private func selectConversation() {
        if let id = historyPicker.selectedItem?.representedObject as? String {
            stopAction()
            command("select", ["conversation_id": id])
        }
    }
    @objc private func showStorage() { command("storage") }
    @objc private func replayAudio() {
        guard !replayAssets.isEmpty else { status.stringValue = "No audio to replay"; return }
        stopPlayback()
        audioQueue = replayAssets
        playNext()
    }

    @objc private func replayInputAudio() {
        guard let input = inputReplay else { status.stringValue = "No input recording"; return }
        stopPlayback()
        audioQueue = [input]
        playNext()
    }

    @objc private func deleteConversation() {
        guard let id = conversationID else { return }
        let alert = NSAlert()
        alert.messageText = "Delete this conversation and its recordings?"
        alert.addButton(withTitle: "Delete")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn {
            stopAction()
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

@main
struct AloyMain {
    static func main() {
        let app = NSApplication.shared
        let controller = AppController()
        app.delegate = controller
        app.run()
        withExtendedLifetime(controller) {}
    }
}
