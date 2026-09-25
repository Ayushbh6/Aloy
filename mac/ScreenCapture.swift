import AppKit
import AVFoundation
@preconcurrency import ScreenCaptureKit

struct CapturedWindowTarget {
    let bundleID: String
    let processID: Int32
    let windowID: UInt32
    let title: String
    let width: Double
    let height: Double

    var bridgeValue: [String: Any] {
        ["bundle_id": bundleID, "process_id": Int(processID), "window_id": Int(windowID),
         "title": title, "window_width": width, "window_height": height,
         "coordinate_space": "window_normalized_top_left"]
    }
}

struct ScreenCaptureResult {
    let path: String
    let target: CapturedWindowTarget
}

@available(macOS 15.0, *)
@MainActor
final class ScreenCaptureCoordinator: NSObject, SCRecordingOutputDelegate {
    private var lastExternalPID: pid_t?
    private var stream: SCStream?
    private var recording: SCRecordingOutput?
    private var recordingURL: URL?
    private var finishWaiter: CheckedContinuation<Void, Error>?
    private var recordingFinished = false
    private var recordingError: Error?
    private var indicator: NSPanel?
    private var indicatorLabel: NSTextField?
    private var timer: Timer?
    private var startedAt: Date?
    private var activationObserver: NSObjectProtocol?

    override init() {
        super.init()
        if let app = NSWorkspace.shared.frontmostApplication,
           app.bundleIdentifier != Bundle.main.bundleIdentifier {
            lastExternalPID = app.processIdentifier
        }
        activationObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
        ) { [weak self] notification in
            guard let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey]
                as? NSRunningApplication, app.bundleIdentifier != Bundle.main.bundleIdentifier
            else { return }
            let pid = app.processIdentifier
            Task { @MainActor in self?.lastExternalPID = pid }
        }
    }

    deinit {
        if let activationObserver {
            NSWorkspace.shared.notificationCenter.removeObserver(activationObserver)
        }
        timer?.invalidate()
    }

    private func foregroundWindow() async throws -> SCWindow {
        let app = NSWorkspace.shared.frontmostApplication
        let pid = app?.bundleIdentifier == Bundle.main.bundleIdentifier
            ? lastExternalPID : app?.processIdentifier
        guard let pid else { throw CaptureError.noForegroundWindow }
        let shareable = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: true)
        let eligible = Dictionary(uniqueKeysWithValues: shareable.windows
            .filter { $0.owningApplication?.processID == pid && $0.frame.width > 100
                && $0.frame.height > 80 }
            .map { ($0.windowID, $0) })
        let ordered = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID)
            as? [[String: Any]] ?? []
        for entry in ordered {
            guard let id = entry[kCGWindowNumber as String] as? UInt32 else { continue }
            if let window = eligible[id] { return window }
        }
        throw CaptureError.noForegroundWindow
    }

    func capture(kind: String, seconds: Int, root: String) async throws -> ScreenCaptureResult {
        guard kind == "screen.snapshot" || kind == "screen.record_clip",
              (kind == "screen.snapshot" && seconds == 0) ||
              (kind == "screen.record_clip" && (1...60).contains(seconds)) else {
            throw CaptureError.invalidRequest
        }
        let window = try await foregroundWindow()
        try Task.checkCancellation()
        guard let app = window.owningApplication else {
            throw CaptureError.noForegroundWindow
        }
        let targetMetadata = CapturedWindowTarget(
            bundleID: app.bundleIdentifier, processID: app.processID, windowID: window.windowID,
            title: window.title ?? "", width: Double(window.frame.width), height: Double(window.frame.height))
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let config = SCStreamConfiguration()
        let scale = min(2.0, min(1280 / window.frame.width, 720 / window.frame.height))
        config.width = max(2, Int(window.frame.width * scale) / 2 * 2)
        config.height = max(2, Int(window.frame.height * scale) / 2 * 2)
        config.showsCursor = true
        let staging = URL(fileURLWithPath: root).appendingPathComponent("tmp", isDirectory: true)
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: staging.path)
        let target = staging.appendingPathComponent(UUID().uuidString +
            (kind == "screen.snapshot" ? ".png" : ".mp4"))
        if kind == "screen.snapshot" {
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter, configuration: config)
            try Task.checkCancellation()
            guard let data = NSBitmapImageRep(cgImage: image).representation(using: .png,
                                                                             properties: [:])
            else { throw CaptureError.encodingFailed }
            try data.write(to: target, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
            return ScreenCaptureResult(path: target.path, target: targetMetadata)
        }
        config.capturesAudio = true
        config.captureMicrophone = false
        config.excludesCurrentProcessAudio = true
        config.minimumFrameInterval = CMTime(value: 1, timescale: 15)
        config.queueDepth = 4
        let captureStream = SCStream(filter: filter, configuration: config, delegate: nil)
        let outputConfig = SCRecordingOutputConfiguration()
        outputConfig.outputURL = target
        let output = SCRecordingOutput(configuration: outputConfig, delegate: self)
        try captureStream.addRecordingOutput(output)
        stream = captureStream
        recording = output
        recordingURL = target
        recordingFinished = false
        recordingError = nil
        showIndicator()
        do {
            try await captureStream.startCapture()
            try Task.checkCancellation()
            try await Task.sleep(nanoseconds: UInt64(seconds) * 1_000_000_000)
            try await captureStream.stopCapture()
            stream = nil
            try await waitForRecording()
            try Task.checkCancellation()
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
            hideIndicator()
            recording = nil
            recordingURL = nil
            return ScreenCaptureResult(path: target.path, target: targetMetadata)
        } catch {
            await cancel()
            throw error
        }
    }

    func cancel() async {
        let previousStream = stream
        let previousURL = recordingURL
        stream = nil
        recording = nil
        recordingURL = nil
        hideIndicator()
        finishWaiter?.resume(throwing: CaptureError.cancelled)
        finishWaiter = nil
        if let previousStream { try? await previousStream.stopCapture() }
        if let previousURL { try? FileManager.default.removeItem(at: previousURL) }
    }

    private func waitForRecording() async throws {
        if let recordingError { throw recordingError }
        if recordingFinished { return }
        try await withTaskCancellationHandler {
            try Task.checkCancellation()
            try await withCheckedThrowingContinuation { continuation in
                finishWaiter = continuation
            }
        } onCancel: {
            Task { @MainActor in await self.cancel() }
        }
    }

    nonisolated func recordingOutputDidStartRecording(_ recordingOutput: SCRecordingOutput) {}

    nonisolated func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        DispatchQueue.main.async {
            guard self.recording === recordingOutput else { return }
            self.recordingFinished = true
            self.finishWaiter?.resume()
            self.finishWaiter = nil
        }
    }

    nonisolated func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) {
        DispatchQueue.main.async {
            guard self.recording === recordingOutput else { return }
            self.recordingError = error
            self.finishWaiter?.resume(throwing: error)
            self.finishWaiter = nil
        }
    }

    private func showIndicator() {
        let visible = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
        let panel = NSPanel(contentRect: NSRect(x: visible.maxX - 270, y: visible.maxY - 66,
                                                width: 250, height: 42),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.level = .statusBar
        panel.isOpaque = false
        panel.backgroundColor = NSColor.windowBackgroundColor.withAlphaComponent(0.96)
        panel.hasShadow = true
        panel.ignoresMouseEvents = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        let label = NSTextField(labelWithString: "● Recording screen · 00:00")
        label.frame = NSRect(x: 14, y: 10, width: 225, height: 20)
        label.textColor = .systemRed
        label.font = .monospacedDigitSystemFont(ofSize: 13, weight: .semibold)
        panel.contentView?.addSubview(label)
        panel.orderFrontRegardless()
        indicator = panel
        indicatorLabel = label
        startedAt = Date()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, let startedAt = self.startedAt else { return }
                let elapsed = min(60, Int(Date().timeIntervalSince(startedAt)))
                self.indicatorLabel?.stringValue = String(format: "● Recording screen · %02d:%02d",
                                                           elapsed / 60, elapsed % 60)
            }
        }
    }

    private func hideIndicator() {
        timer?.invalidate()
        timer = nil
        indicator?.close()
        indicator = nil
        indicatorLabel = nil
        startedAt = nil
    }
}

enum CaptureError: LocalizedError {
    case noForegroundWindow, invalidRequest, encodingFailed, cancelled

    var errorDescription: String? {
        switch self {
        case .noForegroundWindow: "No foreground app window is available to capture."
        case .invalidRequest: "The screen capture request is invalid."
        case .encodingFailed: "The screen capture could not be saved."
        case .cancelled: "Screen capture cancelled."
        }
    }
}
