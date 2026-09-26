import AppKit
import AVFoundation
import Carbon
import Combine
import SwiftUI

@MainActor
final class AppController: NSObject, NSApplicationDelegate, @preconcurrency AVAudioRecorderDelegate,
                           @preconcurrency AVAudioPlayerDelegate {
    private var hotKeys: [EventHotKeyRef] = []
    private var hotKeyHandler: EventHandlerRef?
    private var shortcutGestures: [UInt32: ShortcutGesture] = [:]
    private var backend: Process?
    private var backendInput: FileHandle?
    private let backendWriteQueue = DispatchQueue(label: "Aloy.BackendWrites")
    private var orbWindow: NSWindow!
    private var bubbleWindow: NSPanel!
    private var playgroundWindow: NSWindow?
    private var playgroundModel: PlaygroundModel!
    private var canvasCoordinator: CanvasCoordinator!
    private let actionApproval = ComputerActionApprovalCoordinator()
    private var pendingActionExecution = PendingActionExecution()
    private var actionExecutionWorkItem: DispatchWorkItem?
    private var restoreBubbleAfterAction = false
    private var modelObservation: AnyCancellable?
    private var capturedTargetsByRun: [String: [CapturedTargetRecord]] = [:]
    private var captureTask: Task<Void, Never>?
    private var captureID: String?
    private var enabledAgentTools: Set<String>?
    private var screenCapture: ScreenCaptureCoordinator?
    private var orb: OrbView!
    private let status = NSTextField(labelWithString: "Starting…")
    private var currentProvider = "gemini"
    private var currentSpeech = "chatterbox"
    private var currentVoice = "warm-male"
    private var currentMode = "standard"
    private var conversationID: String?
    private var dataRoot: String?
    private var visionRoute = "gemini"
    private var recorder: AVAudioRecorder?
    private var voiceMonitor: AVAudioEngine?
    private var voiceMonitorID: String?
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
    private var replyAudio = ReplyAudioState()
    private let voiceAudio = VoiceAudio()
    private var voiceSessionID: String?
    private var voiceEpoch = 0
    private var audioRunID: String?


    private struct CapturedTargetRecord {
        let operationID: String
        let target: CapturedWindowTarget
    }

    private func updateReplyAppearance() {
        orb.isSpeaking = replyAudio.isSpeaking(
            hasPlayback: player?.isPlaying == true || !audioQueue.isEmpty || voiceAudio.hasPlayback)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "Aloy")
        appMenu.addItem(withTitle: "Quit Aloy", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)
        NSApp.mainMenu = menu
        playgroundModel = makePlaygroundModel()
        playgroundModel.onRecord = { [weak self] in self?.toggleRecording() }
        playgroundModel.onStop = { [weak self] in self?.stopAction() }
        playgroundModel.onReplayAudio = { [weak self] direction in
            if direction == "input" { self?.replayInputAudio() }
            else { self?.replayAudio() }
        }
        playgroundModel.onShowStorage = { [weak self] in self?.showStorage() }
        canvasCoordinator = CanvasCoordinator(model: playgroundModel)
        actionApproval.onDecision = { [weak self] proposal, decision in
            self?.command("action_decision", ["proposal_id": proposal.proposalID,
                "operation_id": proposal.operationID, "run_id": proposal.runID,
                "decision": decision], version: 2, operationID: proposal.operationID)
        }
        voiceAudio.onPCM = { [weak self] data in
            guard let self, let session = self.voiceSessionID else { return }
            self.command("voice_frames", ["session_id": session, "pcm": data.base64EncodedString()])
        }
        voiceAudio.onError = { [weak self] message in
            self?.stopAction()
            self?.showNativeError(message)
        }
        voiceAudio.onMetric = { [weak self] stage, value in self?.voiceMetric(stage, value: value) }
        voiceAudio.onPlaybackFinished = { [weak self] in
            guard let self else { return }
            if let id = self.playingAssetID {
                self.command("playback", ["asset_id": id, "status": "played"])
            }
            self.playingAssetID = nil
            if !self.audioQueue.isEmpty { self.playNext() }
            else {
                self.replyAudio.finishGeneration()
                self.restoreListeningAppearance()
            }
        }
        buildOrb()
        buildBubble()
        screenCapture = ScreenCaptureCoordinator()
        startBackend()
        registerVoiceShortcut()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        stopAction()
        // EOF runs the bridge's normal cancellation, worker cleanup and DB close.
        try? backendInput?.close()
        backendInput = nil
        guard let backend, backend.isRunning else { return .terminateNow }
        DispatchQueue.global(qos: .utility).async {
            backend.waitUntilExit()
            DispatchQueue.main.async { sender.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        for hotKey in hotKeys { UnregisterEventHotKey(hotKey) }
        if let hotKeyHandler { RemoveEventHandler(hotKeyHandler) }
        voiceAudio.shutdown()
        recorder?.stop()
        voiceMonitor?.inputNode.removeTap(onBus: 0)
        voiceMonitor?.stop()
        // Unfinished capture remains in Aloy/tmp for recovery on next launch.
        player?.stop()
        captureTask?.cancel()
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
            var key = EventHotKeyID()
            guard GetEventParameter(event, EventParamName(kEventParamDirectObject),
                EventParamType(typeEventHotKeyID), nil, MemoryLayout<EventHotKeyID>.size,
                nil, &key) == noErr, key.signature == 0x414C4F59 else { return OSStatus(eventNotHandledErr) }
            if GetEventKind(event) == UInt32(kEventHotKeyPressed) {
                owner.shortcutGestures[key.id, default: ShortcutGesture()].down()
            } else if owner.shortcutGestures[key.id, default: ShortcutGesture()].up() {
                if key.id == 1 { owner.toggleRecording() }
                else { owner.stopAction() }
            }
            return noErr
        }, events.count, &events, context, &hotKeyHandler)
        var failed = installed != noErr
        if !failed {
            for (id, code) in [(UInt32(1), kVK_ANSI_Z), (UInt32(2), kVK_ANSI_X)] {
                var reference: EventHotKeyRef?
                let result = RegisterEventHotKey(UInt32(code), UInt32(optionKey),
                    EventHotKeyID(signature: 0x414C4F59, id: id), GetApplicationEventTarget(), 0, &reference)
                if result == noErr, let reference { hotKeys.append(reference) }
                else { failed = true }
            }
        }
        if failed {
            let alert = NSAlert()
            alert.messageText = "An Aloy shortcut is unavailable"
            alert.informativeText = "Option–Z opens/closes voice; Option–X stops immediately. Another app may own a shortcut. Open Aloy to use the conversation controls."
            alert.runModal()
        }
        orb.toolTip = "Option–Z: open/close voice · Option–X: stop · Click for chat"
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
        orb.onMove = { [weak self] _ in
            guard let self, self.bubbleWindow?.isVisible == true else { return }
            self.placeBubble()
        }
        orbWindow.contentView = orb
        orbWindow.orderFrontRegardless()
    }

    private func buildBubble() {
        let size = CGSize(width: 360, height: 260)
        let screen = visibleScreen(for: orbWindow.frame)
        let frame = CompanionBubblePlacement.frame(orb: orbWindow.frame,
                                                   visibleScreen: screen, size: size)
        bubbleWindow = NSPanel(contentRect: frame,
                               styleMask: [.borderless, .nonactivatingPanel],
                               backing: .buffered, defer: false)
        bubbleWindow.isReleasedWhenClosed = false
        bubbleWindow.level = .floating
        bubbleWindow.isOpaque = false
        bubbleWindow.backgroundColor = .clear
        bubbleWindow.hasShadow = false
        bubbleWindow.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        bubbleWindow.contentView = NSHostingView(rootView: CompanionBubbleView(
            model: playgroundModel,
            onExpand: { [weak self] in self?.openPlayground() },
            onDismiss: { [weak self] in self?.hideBubble() }))
        modelObservation = playgroundModel.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { self?.resizeBubbleToFit() }
        }
    }

    private func visibleScreen(for rect: NSRect) -> NSRect {
        NSScreen.screens.first(where: { $0.frame.intersects(rect) })?.visibleFrame
            ?? NSScreen.main?.visibleFrame
            ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
    }

    private func placeBubble() {
        guard bubbleWindow != nil else { return }
        let screen = visibleScreen(for: orbWindow.frame)
        let frame = CompanionBubblePlacement.frame(orb: orbWindow.frame,
                                                   visibleScreen: screen,
                                                   size: bubbleWindow.frame.size)
        bubbleWindow.setFrame(frame, display: true)
    }

    private func resizeBubbleToFit() {
        guard let hosting = bubbleWindow?.contentView as? NSHostingView<CompanionBubbleView> else { return }
        let fitted = hosting.fittingSize
        let screen = visibleScreen(for: orbWindow.frame)
        let height = min(max(fitted.height, 210), max(210, screen.height - 16))
        bubbleWindow.setContentSize(NSSize(width: 360, height: height))
        placeBubble()
    }

    private func showBubble() {
        resizeBubbleToFit()
        bubbleWindow.alphaValue = 0
        bubbleWindow.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.18
            bubbleWindow.animator().alphaValue = 1
        }
    }

    private func hideBubble() {
        guard bubbleWindow?.isVisible == true else { return }
        bubbleWindow.orderOut(nil)
    }

    private func togglePanel() {
        if bubbleWindow.isVisible { hideBubble() }
        else { showBubble() }
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

    private func command(_ action: String, _ fields: [String: Any] = [:], version: Int = 1,
                         operationID: String? = nil) {
        var object = fields
        object["v"] = version
        object["operation_id"] = operationID ?? operation.id
        object["action"] = action
        object["id"] = UUID().uuidString
        guard let data = try? JSONSerialization.data(withJSONObject: object),
              let input = backendInput else { return }
        let packet = data + Data([10])
        backendWriteQueue.async { input.write(packet) }
    }

    private func handle(_ object: [String: Any]) {
        guard let event = object["event"] as? String else { return }
        if let id = object["operation_id"] as? String, !operation.accepts(id) { return }
        if let epoch = object["voice_epoch"] as? Int {
            guard epoch >= voiceEpoch else { return }
            voiceEpoch = epoch
        }
        playgroundModel.receive(object)
        if let request = actionPayload(object, kind: "action_requested") {
            handleActionRequest(request)
            return
        }
        if let execution = actionPayload(object, kind: "action_execute") {
            handleActionExecution(execution)
            return
        }
        if let cancellation = actionPayload(object, kind: "action_cancelled") {
            actionApproval.handleCancellation(cancellation)
            cancelScheduledActionExecution(
                proposalID: cancellation["proposal_id"] as? String, report: false)
            return
        }
        switch event {
        case "capture_cancelled":
            if object["capture_id"] as? String == captureID { captureTask?.cancel() }
        case "capture_requested":
            guard let captureID = object["capture_id"] as? String,
                  let kind = object["kind"] as? String,
                  let seconds = object["seconds"] as? Int,
                  let runID = object["run_id"] as? String,
                  let root = dataRoot, let screenCapture else { return }
            let operationID = object["operation_id"] as? String ?? operation.id
            let previousCapture = captureTask
            previousCapture?.cancel()
            self.captureID = captureID
            captureTask = Task { [weak self] in
                guard let self else { return }
                do {
                    await previousCapture?.value
                    try Task.checkCancellation()
                    let captured = try await screenCapture.capture(kind: kind, seconds: seconds,
                                                                  root: root)
                    try Task.checkCancellation()
                    self.capturedTargetsByRun[runID, default: []].append(
                        CapturedTargetRecord(operationID: operationID, target: captured.target))
                    self.command("capture_result", ["capture_id": captureID,
                        "path": captured.path, "target": captured.target.bridgeValue],
                        version: 2, operationID: operationID)
                } catch {
                    self.command("capture_result", ["capture_id": captureID,
                        "error": error.localizedDescription], version: 2, operationID: operationID)
                }
            }
        case "ready":
            dataRoot = object["root"] as? String
            let settings = object["settings"] as? [String: String] ?? [:]
            currentProvider = settings["provider"] ?? currentProvider
            currentSpeech = settings["speech"] ?? currentSpeech
            currentVoice = settings["voice:\(currentSpeech)"] ?? currentVoice
            currentMode = settings["mode"] ?? currentMode
            if let encoded = settings["tools"]?.data(using: .utf8),
               let tools = try? JSONSerialization.jsonObject(with: encoded) as? [String] {
                enabledAgentTools = Set(tools)
            }
            status.stringValue = "Ready"
            playgroundModel.companionStatus = .ready
            command("list")
        case "conversations":
            let items = object["items"] as? [[String: Any]] ?? []
            if let id = conversationID, items.contains(where: { $0["id"] as? String == id }) { break }
            if let first = items.first, let id = first["id"] as? String {
                command("select", ["conversation_id": id])
            } else { command("new") }
        case "conversation":
            conversationID = object["conversation_id"] as? String
            capturedTargetsByRun.removeAll()
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
        case "voice_ready":
            status.stringValue = "Listening — Option–Z closes voice"
            playgroundModel.companionStatus = .listening
        case "voice_cue":
            replyAudio.begin()
            status.stringValue = object["text"] as? String ?? "Welcome back"
            playgroundModel.status = status.stringValue
        case "voice_activity":
            guard voiceSessionID != nil else { return }
            if object["state"] as? String == "speech" {
                let began = ProcessInfo.processInfo.systemUptime
                stopPlayback()
                voiceMetric("interrupt_stop_ms", value: (ProcessInfo.processInfo.systemUptime - began) * 1000)
                status.stringValue = "Hearing you…"
                playgroundModel.companionStatus = .listening
            } else {
                orb.isProcessing = true
                status.stringValue = "Thinking…"
                playgroundModel.companionStatus = .thinking
            }
        case "audio_stream_start":
            guard let id = object["stream_id"] as? String else { return }
            audioRunID = object["run_id"] as? String
            do { try voiceAudio.beginStream(id) }
            catch { showNativeError("Audio output unavailable"); stopAction() }
        case "audio_chunk":
            guard let id = object["stream_id"] as? String,
                  let pcm = object["pcm"] as? String, let data = Data(base64Encoded: pcm) else { return }
            replyAudio.audioArrived()
            voiceAudio.append(data, stream: id)
            orb.isProcessing = false
            updateReplyAppearance()
            playgroundModel.companionStatus = .speaking
        case "audio_stream_end":
            guard let id = object["stream_id"] as? String else { return }
            playingAssetID = object["asset_id"] as? String
            voiceAudio.endStream(id)
        case "started":
            replyAudio.begin()
            orb.isProcessing = true
            orb.hasError = false
            replayAssets.removeAll()
            currentRunID = object["run_id"] as? String
            replyBuffer = ""
            status.stringValue = "Thinking…"
            playgroundModel.companionStatus = .thinking
        case "live_processing":
            status.stringValue = "Listening and responding…"
        case "delta":
            guard object["run_id"] as? String == currentRunID else { return }
            replyBuffer += object["text"] as? String ?? ""
        case "completed":
            status.stringValue = "Speaking…"
            if object["budget_warning"] as? Bool == true { status.stringValue = "Spending warning: $20+" }
        case "transcript":
            status.stringValue = "Thinking…"
            playgroundModel.companionStatus = .thinking
        case "speech_activity":
            guard recorder != nil, object["session_id"] as? String == voiceMonitorID else { return }
            status.stringValue = object["state"] as? String == "speech"
                ? "Hearing you…"
                : "Paused — keep speaking, or Option–Z to send"
            playgroundModel.companionStatus = object["state"] as? String == "speech"
                ? .listening : .recording
        case "audio":
            if let path = object["path"] as? String, let id = object["asset_id"] as? String {
                replyAudio.audioArrived()
                lastAudioPath = path
                lastAudioAssetID = id
                replayAssets.append((path, id))
                if object["streamed"] as? Bool != true {
                    audioRunID = object["run_id"] as? String
                    audioQueue.append((path, id))
                    playNext()
                }
                updateReplyAppearance()
                playgroundModel.companionStatus = .speaking
            }
        case "turn_done":
            replyAudio.finishGeneration()
            orb.isProcessing = false
            updateReplyAppearance()
            if object["failed"] as? Bool == true {
                playgroundModel.companionStatus = .error
            } else if orb.isSpeaking {
                playgroundModel.companionStatus = .speaking
            } else {
                playgroundModel.companionStatus = .ready
            }
            if !orb.isSpeaking && object["failed"] as? Bool != true {
                status.stringValue = "Ready"
            }
            if let currentRunID { capturedTargetsByRun.removeValue(forKey: currentRunID) }
            currentRunID = nil
            restoreListeningAppearance()
            command("list")
        case "recording_retained":
            status.stringValue = "Saved locally. Right-click Replay to hear it. Not sent."
            playgroundModel.companionStatus = .ready
            if let id = conversationID { command("select", ["conversation_id": id]) }
        case "storage":
            break
        case "deleted":
            command("list")
        case "no_speech":
            replyAudio.reset()
            orb.isProcessing = false
            status.stringValue = "No speech detected — nothing sent"
            orb.toolTip = status.stringValue
            playgroundModel.companionStatus = .ready
            restoreListeningAppearance()
        case "error", "speech_error":
            if voiceSessionID != nil {
                voiceAudio.stopCapture()
                voiceSessionID = nil
                playgroundModel.voiceSessionActive = false
                orb.isRecording = false
                command("voice_close", ["goodbye": false])
            }
            orb.hasError = true
            orb.isProcessing = false
            stopPlayback()
            status.stringValue = object["error"] as? String ?? "Error"
            playgroundModel.status = status.stringValue
            playgroundModel.companionStatus = .error
        case "stopped":
            replyAudio.reset()
            orb.isProcessing = false
            updateReplyAppearance()
            playgroundModel.companionStatus = .ready
            if recorder == nil { status.stringValue = "Ready" }
            capturedTargetsByRun.removeAll()
        default: break
        }
    }

    private func actionPayload(_ object: [String: Any], kind: String) -> [String: Any]? {
        if object["event"] as? String == kind { return object }
        guard object["event"] as? String == "agent_event",
              object["agent_kind"] as? String == kind,
              var payload = object["data"] as? [String: Any] else { return nil }
        payload["event"] = kind
        payload["operation_id"] = object["operation_id"] ?? payload["operation_id"]
        payload["run_id"] = object["run_id"] ?? payload["run_id"]
        return payload
    }

    private func handleActionRequest(_ payload: [String: Any]) {
        guard let proposalID = payload["proposal_id"] as? String,
              let operationID = payload["operation_id"] as? String,
              let runID = payload["run_id"] as? String else { return }
        let action = payload["action"] as? String ?? ""
        guard playgroundModel.agentPolicy == "approval_required",
              playgroundModel.enabledTools.contains(action) else {
            showNativeError("Desktop actions are disabled. Enable the tool and choose approval-required policy in Settings.")
            command("action_decision", ["proposal_id": proposalID, "operation_id": operationID,
                "run_id": runID, "decision": "deny"], version: 2, operationID: operationID)
            return
        }
        guard runID == currentRunID, operation.accepts(operationID),
              let records = capturedTargetsByRun[runID],
              let record = records.last(where: { $0.operationID == operationID }) else {
            showNativeError("A desktop action needs a successful screen capture in this run to identify its exact target. Capture metadata is not approval.")
            command("action_decision", ["proposal_id": proposalID, "operation_id": operationID,
                "run_id": runID, "decision": "deny"], version: 2, operationID: operationID)
            return
        }
        guard let proposal = ComputerActionProposal.decode(payload, capturedTarget: record.target) else {
            showNativeError("Aloy refused a malformed, expired, or unsupported desktop action.")
            command("action_decision", ["proposal_id": proposalID, "operation_id": operationID,
                "run_id": runID, "decision": "deny"], version: 2, operationID: operationID)
            return
        }
        actionApproval.present(proposal)
    }

    private func showNativeError(_ message: String) {
        status.stringValue = message
        playgroundModel.status = message
        playgroundModel.companionStatus = .error
    }

    private func handleActionExecution(_ payload: [String: Any]) {
        guard let proposalID = payload["proposal_id"] as? String,
              let operationID = payload["operation_id"] as? String,
              let runID = payload["run_id"] as? String else { return }
        guard let proposal = actionApproval.takeApproved(for: payload),
              playgroundModel.agentPolicy == "approval_required",
              playgroundModel.enabledTools.contains(proposal.kind.rawValue),
              operation.accepts(operationID), currentRunID == runID,
              capturedTargetsByRun[runID]?.contains(where: {
                  $0.operationID == operationID && $0.target.bundleID == proposal.bundleID &&
                      $0.target.windowID == proposal.windowID && $0.target.processID == proposal.processID
              }) == true else {
            sendActionResult(proposalID: proposalID, operationID: operationID, runID: runID,
                             status: "stale", result: ["reason": "No matching live approval."])
            return
        }
        guard pendingActionExecution.schedule(proposal, operationGateID: operation.id) != nil else {
            sendActionResult(proposalID: proposalID, operationID: operationID, runID: runID,
                             status: "stale", result: ["reason": "Another approved action is already queued."])
            return
        }
        restoreBubbleAfterAction = bubbleWindow?.isVisible == true
        hideBubble()
        orbWindow.orderOut(nil)
        canvasCoordinator.hideTemporarily()
        guard ComputerActionExecution.prepareFocus(proposal) else {
            _ = pendingActionExecution.cancel(proposalID: proposalID)
            sendActionResult(proposalID: proposalID, operationID: operationID, runID: runID,
                             status: "stale", result: ["reason": "The exact approved window could not be focused."])
            restoreActionPresentation()
            return
        }
        let workItem = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.actionExecutionWorkItem = nil
            guard self.playgroundModel.agentPolicy == "approval_required",
                  self.playgroundModel.enabledTools.contains(proposal.kind.rawValue) else {
                _ = self.pendingActionExecution.cancel(proposalID: proposalID)
                self.sendActionResult(proposalID: proposalID, operationID: operationID,
                                      runID: runID, status: "cancelled",
                                      result: ["reason": "Desktop permission was disabled."])
                self.restoreActionPresentation()
                return
            }
            guard self.pendingActionExecution.consume(proposalID: proposalID,
                    operationID: operationID, runID: runID, operationGateID: self.operation.id,
                    currentRunID: self.currentRunID) else {
                self.sendActionResult(proposalID: proposalID, operationID: operationID,
                                      runID: runID, status: "stale",
                                      result: ["reason": "The approved action is no longer current."])
                self.restoreActionPresentation()
                return
            }
            switch ComputerActionExecution.perform(proposal) {
            case .completed(let result):
                self.sendActionResult(proposalID: proposalID, operationID: operationID,
                                      runID: runID, status: "completed", result: result)
            case .stale(let reason):
                self.sendActionResult(proposalID: proposalID, operationID: operationID,
                                      runID: runID, status: "stale", result: ["reason": reason])
            case .failed(let reason):
                self.sendActionResult(proposalID: proposalID, operationID: operationID,
                                      runID: runID, status: "failed", result: ["reason": reason])
            }
            self.restoreActionPresentation()
        }
        actionExecutionWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15, execute: workItem)
    }

    private func cancelScheduledActionExecution(proposalID: String? = nil, report: Bool) {
        guard let ticket = pendingActionExecution.cancel(proposalID: proposalID) else { return }
        actionExecutionWorkItem?.cancel()
        actionExecutionWorkItem = nil
        if report {
            sendActionResult(proposalID: ticket.proposalID, operationID: ticket.operationID,
                             runID: ticket.runID, status: "cancelled",
                             result: ["reason": "Cancelled before the approved action ran."])
        }
        restoreActionPresentation()
    }

    private func restoreActionPresentation() {
        orbWindow?.orderFrontRegardless()
        canvasCoordinator?.restoreIfPresented()
        if restoreBubbleAfterAction { showBubble() }
        restoreBubbleAfterAction = false
    }

    private func sendActionResult(proposalID: String, operationID: String, runID: String,
                                  status: String, result: [String: Any]) {
        command("action_result", ["proposal_id": proposalID, "operation_id": operationID,
            "run_id": runID, "status": status, "result": result],
            version: 2, operationID: operationID)
    }

    private func playNext() {
        guard player?.isPlaying != true, !voiceAudio.hasPlayback, !audioQueue.isEmpty else { return }
        let asset = audioQueue.removeFirst()
        playingAssetID = asset.id
        do {
            if voiceSessionID != nil {
                try voiceAudio.playFile(URL(fileURLWithPath: asset.path), id: asset.id)
                command("playback", ["asset_id": asset.id, "status": "playing"])
                return
            }
            player = try AVAudioPlayer(contentsOf: URL(fileURLWithPath: asset.path))
            player?.delegate = self
            guard player?.play() == true else {
                player = nil
                command("playback", ["asset_id": asset.id, "status": "failed"])
                status.stringValue = "Audio playback could not start"
                return
            }
            command("playback", ["asset_id": asset.id, "status": "playing"])
            updateReplyAppearance()
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
        if !flag {
            replyAudio.reset()
            status.stringValue = "Audio playback stopped unexpectedly"
            playgroundModel.status = status.stringValue
            playgroundModel.companionStatus = .error
        }
        else if !audioQueue.isEmpty { playNext() }
        else {
            status.stringValue = replyAudio.generationComplete ? "Ready" : "Speaking…"
            playgroundModel.companionStatus = replyAudio.generationComplete ? .ready : .speaking
        }
        updateReplyAppearance()
    }

    private func stopPlayback() {
        let began = ProcessInfo.processInfo.systemUptime
        let wasPlaying = player?.isPlaying == true || voiceAudio.hasPlayback
        voiceAudio.stopPlayback()
        player?.stop()
        player = nil
        let cancelled = audioQueue
        audioQueue.removeAll()
        replyAudio.reset()
        updateReplyAppearance()
        orb.isProcessing = false
        if recorder == nil { playgroundModel.companionStatus = .ready }
        let elapsed = (ProcessInfo.processInfo.systemUptime - began) * 1000
        if let id = playingAssetID { command("playback", ["asset_id": id, "status": "cancelled"]) }
        playingAssetID = nil
        for asset in cancelled { command("playback", ["asset_id": asset.id, "status": "cancelled"]) }
        if wasPlaying { command("metric", ["kind": "playback_stop_ms", "value": elapsed]) }
    }

    private var provider: String {
        currentProvider
    }
    private var speech: String {
        currentSpeech
    }
    private var voice: String {
        playgroundModel?.speechVoice ?? currentVoice
    }

    private func voiceMetric(_ stage: String, value: Double? = nil) {
        var fields: [String: Any] = ["stage": stage, "stamp": ProcessInfo.processInfo.systemUptime]
        if let value { fields["value"] = value }
        if let audioRunID { fields["run_id"] = audioRunID }
        command("voice_timing", fields)
    }

    private func restoreListeningAppearance() {
        updateReplyAppearance()
        if voiceSessionID != nil && !voiceAudio.hasPlayback && player?.isPlaying != true && !orb.isProcessing {
            status.stringValue = "Listening — Option–Z closes voice"
            playgroundModel.companionStatus = .listening
            orb.isRecording = true
        } else if voiceSessionID == nil && !voiceAudio.hasPlayback && player?.isPlaying != true {
            updateReplyAppearance()
            if !orb.isProcessing {
                status.stringValue = "Ready"
                playgroundModel.companionStatus = .ready
            }
        }
    }

    @objc private func toggleRecording() {
        if currentMode == "live" { toggleManualRecording(); return }
        if voiceSessionID != nil {
            voiceAudio.stopCapture()
            voiceSessionID = nil
            playgroundModel.voiceSessionActive = false
            orb.isRecording = false
            stopPlayback()
            invalidateOperation()
            command("voice_close", ["goodbye": true])
            status.stringValue = "Goodbye…"
            return
        }
        guard dataRoot != nil, conversationID != nil, !microphoneRequestPending else { return }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: startVoiceSession()
        case .notDetermined:
            microphoneRequestPending = true
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] granted in
                DispatchQueue.main.async {
                    guard let self, self.microphoneRequestPending else { return }
                    self.microphoneRequestPending = false
                    if granted { self.startVoiceSession() }
                    else { self.showNativeError("Microphone access was not granted") }
                }
            }
        default: showNativeError("Enable Aloy microphone access in System Settings")
        }
    }

    private func startVoiceSession() {
        guard let cid = conversationID else { return }
        stopAction()
        audioRunID = nil
        let session = UUID().uuidString
        voiceSessionID = session
        do {
            try voiceAudio.startCapture()
            playgroundModel.voiceSessionActive = true
            orb.isRecording = true
            orb.hasError = false
            orb.toolTip = "Microphone open — Option–Z closes · Option–X stops"
            status.stringValue = "Opening voice…"
            playgroundModel.companionStatus = .listening
            command("voice_open", ["conversation_id": cid, "session_id": session,
                                   "provider": provider, "speech": speech, "voice": voice])
            // A quiet, local activation cue; it never waits for network inference.
            NSSound(named: "Pop")?.play()
        } catch {
            voiceSessionID = nil
            voiceAudio.shutdown()
            showNativeError("Voice processing could not start: \(error.localizedDescription)")
        }
    }

    private func toggleManualRecording() {
        if recorder != nil { finishRecording(); return }
        guard dataRoot != nil, conversationID != nil, !microphoneRequestPending else { NSSound.beep(); return }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            startRecording()
        case .notDetermined:
            microphoneRequestPending = true
            status.stringValue = "Waiting for microphone permission…"
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] granted in
                DispatchQueue.main.async {
                    guard let self, self.microphoneRequestPending else { return }
                    self.microphoneRequestPending = false
                    if granted { self.startRecording() }
                    else {
                        self.status.stringValue = "Microphone access was not granted"
                        self.playgroundModel.status = self.status.stringValue
                        self.playgroundModel.companionStatus = .error
                    }
                }
            }
        case .denied, .restricted:
            status.stringValue = "Enable Aloy microphone access in System Settings"
            playgroundModel.status = status.stringValue
            playgroundModel.companionStatus = .error
        @unknown default:
            status.stringValue = "Microphone permission unavailable"
            playgroundModel.status = status.stringValue
            playgroundModel.companionStatus = .error
        }
    }

    private func finishRecording() {
        stopVoiceMonitor()
        let previous = recorder
        recorder = nil
        orb.isRecording = false
        orb.toolTip = "Processing — Option–Z to start a new recording"
        previous?.stop()
        guard let id = conversationID, let path = recordedPath else { return }
        recordedPath = nil
        command("recorded", ["conversation_id": id, "path": path,
                             "provider": provider, "speech": speech, "voice": voice,
                             "mode": currentMode])
        orb.isProcessing = true
        status.stringValue = "Processing recording…"
        playgroundModel.companionStatus = .thinking
    }

    func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        guard self.recorder === recorder else { return }
        if flag { finishRecording() }
        else { stopAction(); status.stringValue = "Recording failed" }
    }

    private func startRecording() {
        guard let root = dataRoot, conversationID != nil else { return }
        orb.hasError = false
        invalidateOperation()
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
            orb.toolTip = "Recording — Option–Z sends · Option–X cancels"
            recordedPath = url.path
            status.stringValue = "Recording… Finish & Send, or Cancel recording"
            playgroundModel.companionStatus = .recording
            if currentMode != "live" {
                command("prewarm_speech", ["speech": speech, "voice": voice])
                startVoiceMonitor()
            }
        } catch {
            try? FileManager.default.removeItem(at: url)
            status.stringValue = "Microphone unavailable"
        }
    }

    private func startVoiceMonitor() {
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate >= 8000, format.channelCount > 0 else { return }
        let session = UUID().uuidString
        voiceMonitorID = session
        command("vad_start", ["session_id": session, "sample_rate": Int(format.sampleRate)])
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
            let count = Int(buffer.frameLength)
            guard count > 0 else { return }
            var samples = [Int16](repeating: 0, count: count)
            if let channel = buffer.floatChannelData?[0] {
                for index in 0..<count {
                    let value = max(-1.0, min(1.0, channel[index]))
                    samples[index] = Int16(littleEndian: Int16(value * 32767))
                }
            } else if let channel = buffer.int16ChannelData?[0] {
                for index in 0..<count { samples[index] = channel[index].littleEndian }
            } else { return }
            let data = samples.withUnsafeBytes { Data($0) }
            DispatchQueue.main.async { [weak self] in
                guard let self, self.voiceMonitorID == session, self.recorder != nil else { return }
                self.command("vad_audio", ["session_id": session,
                                           "pcm": data.base64EncodedString()])
            }
        }
        do {
            try engine.start()
            voiceMonitor = engine
        } catch {
            input.removeTap(onBus: 0)
            voiceMonitorID = nil
            command("vad_end", ["session_id": session])
            status.stringValue = "Recording… speech activity monitor unavailable"
        }
    }

    private func stopVoiceMonitor() {
        let session = voiceMonitorID
        voiceMonitorID = nil
        if let engine = voiceMonitor {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
        }
        voiceMonitor = nil
        if let session { command("vad_end", ["session_id": session]) }
    }

    @objc private func stopAction() {
        voiceAudio.stopCapture()
        voiceSessionID = nil
        playgroundModel.voiceSessionActive = false
        captureTask?.cancel()
        stopVoiceMonitor()
        invalidateOperation()
        orb.hasError = false
        orb.isProcessing = false
        microphoneRequestPending = false
        let duration = recorder?.currentTime ?? 0
        let previousRecorder = recorder
        recorder = nil
        orb.isRecording = false
        previousRecorder?.stop()
        let cancelledPath = recordedPath
        recordedPath = nil
        stopPlayback()
        command("stop")
        status.stringValue = "Stopped"
        playgroundModel.companionStatus = .ready
        if let path = cancelledPath, let id = conversationID {
            command("cancel_recording", ["conversation_id": id, "path": path, "duration": duration])
        }
    }

    private func invalidateOperation() {
        cancelScheduledActionExecution(report: true)
        actionApproval.cancelPending()
        actionApproval.clear()
        operation.invalidate()
        voiceEpoch = 0
        capturedTargetsByRun.removeAll()
        currentRunID = nil
    }

    private func makePlaygroundModel() -> PlaygroundModel {
        PlaygroundModel { [weak self] action, fields in
            self?.handlePlaygroundCommand(action, fields)
        }
    }

    private func handlePlaygroundCommand(_ action: String, _ fields: [String: Any]) {
        guard action != "stop" else { stopAction(); return }
        if ["new", "select", "delete", "send", "canvas_interact", "set_setting"].contains(action) {
            captureTask?.cancel()
            stopAction()
            stopPlayback()
        }
        currentProvider = playgroundModel.provider
        currentSpeech = playgroundModel.speechEngine
        currentVoice = playgroundModel.speechVoice
        currentMode = playgroundModel.mode
        if action == "set_setting", let key = fields["key"] as? String,
           let value = fields["value"] as? String {
            if key == "provider" { currentProvider = value }
            else if key == "speech" { currentSpeech = value; currentVoice = playgroundModel.speechVoice }
            else if key == "mode" { currentMode = value }
            else if key.hasPrefix("voice:") && key == "voice:\(currentSpeech)" { currentVoice = value }
            else if key == "tools", let data = value.data(using: .utf8),
                    let tools = try? JSONSerialization.jsonObject(with: data) as? [String] {
                enabledAgentTools = Set(tools)
            }
        }
        command(action, fields, version: 2)
    }

    @objc private func openPlayground() {
        hideBubble()
        if let playgroundWindow {
            playgroundWindow.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let frame = NSRect(x: 0, y: 0, width: 1120, height: 740)
        let window = NSWindow(contentRect: frame,
                              styleMask: [.titled, .closable, .resizable, .miniaturizable],
                              backing: .buffered, defer: false)
        window.title = "Aloy"
        window.minSize = NSSize(width: 900, height: 600)
        window.contentView = NSHostingView(rootView: AgentPlayground(model: playgroundModel))
        window.center()
        window.isReleasedWhenClosed = false
        window.makeKeyAndOrderFront(nil)
        playgroundWindow = window
        NSApp.activate(ignoringOtherApps: true)
        command("list", version: 2)
        command("memory_list", version: 2)
        if let conversationID {
            command("inspect_conversation", ["conversation_id": conversationID], version: 2)
        }
    }

    @objc private func showStorage() { command("storage", version: 2) }
    @objc private func replayAudio() {
        guard !replayAssets.isEmpty else { status.stringValue = "No audio to replay"; return }
        stopPlayback()
        replyAudio.begin()
        replyAudio.audioArrived()
        replyAudio.finishGeneration()
        audioQueue = replayAssets
        playNext()
    }

    @objc private func replayInputAudio() {
        guard let input = inputReplay else { status.stringValue = "No input recording"; return }
        stopPlayback()
        audioQueue = [input]
        playNext()
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
