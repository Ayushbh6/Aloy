import AppKit
import QuartzCore

/// A transparent, 56-point field of particles. The state changes only motion speed
/// and tint; the Thinking-state distribution and curvature are shared everywhere.
final class OrbView: NSView {
    var onClick: (() -> Void)?
    var onMove: ((NSPoint) -> Void)?
    var isRecording = false { didSet { if oldValue != isRecording { updateAppearance() } } }
    var isSpeaking = false { didSet { if oldValue != isSpeaking { updateAppearance() } } }
    var isProcessing = false {
        didSet { if oldValue != isProcessing && !isSpeaking { updateAppearance() } }
    }
    var hasError = false { didSet { if oldValue != hasError { updateAppearance() } } }

    private var gesture = OrbGesture()
    private let cloudLayer = CALayer()
    private var motionTimer: Timer?
    private var phase: Double = 0
    private var lastFrameTime = ProcessInfo.processInfo.systemUptime
    private var reduceMotionObserver: NSObjectProtocol?
    override var isOpaque: Bool { false }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        renderCloud()
    }

    // Desktop backdrops do not necessarily match the system appearance. Each
    // particle carries its own dark edge and light core; no screen sampling,
    // opaque sphere, or outer rim is needed for contrast.
    private static let particleColor = NSColor(calibratedRed: 0.79, green: 0.96, blue: 0.84, alpha: 1)
    private static let particleEdge = NSColor(calibratedRed: 0.035, green: 0.12, blue: 0.075, alpha: 1)

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        wantsLayer = true
        if let layer, cloudLayer.superlayer == nil {
            cloudLayer.bounds = bounds
            cloudLayer.position = CGPoint(x: bounds.midX, y: bounds.midY)
            cloudLayer.contentsScale = window?.backingScaleFactor ?? 2
            cloudLayer.masksToBounds = false
            layer.addSublayer(cloudLayer)
        }
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        updateAppearance()
        if window == nil {
            stopMotion()
            if let reduceMotionObserver {
                NSWorkspace.shared.notificationCenter.removeObserver(reduceMotionObserver)
                self.reduceMotionObserver = nil
            }
        } else {
            observeReduceMotionChanges()
            applyMotionPreference()
        }
    }

    deinit {
        motionTimer?.invalidate()
        if let reduceMotionObserver {
            NSWorkspace.shared.notificationCenter.removeObserver(reduceMotionObserver)
        }
    }

    private var motionState: OrbMotionState {
        if hasError { return .attention }
        if isRecording { return .listening }
        if isSpeaking { return .speaking }
        if isProcessing { return .thinking }
        return .quiet
    }

    private var stateLabel: String {
        if hasError { return "Needs you" }
        if isRecording { return "Listening" }
        if isSpeaking { return "Speaking" }
        if isProcessing { return "Thinking" }
        return "Quiet"
    }

    private func updateAppearance() {
        cloudLayer.bounds = bounds
        cloudLayer.position = CGPoint(x: bounds.midX, y: bounds.midY)
        cloudLayer.contentsScale = window?.backingScaleFactor ?? 2
        setAccessibilityLabel("Aloy — " + stateLabel)
        renderCloud()
        if window != nil { applyMotionPreference() }
    }

    private func startMotion() {
        guard motionTimer == nil, window != nil,
              !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { return }
        lastFrameTime = ProcessInfo.processInfo.systemUptime
        let timer = Timer(timeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            guard let self else { return }
            let now = ProcessInfo.processInfo.systemUptime
            let delta = min(0.05, max(0, now - self.lastFrameTime))
            self.phase += delta * 0.12 * self.motionState.angularSpeed
            self.lastFrameTime = now
            self.renderCloud()
        }
        RunLoop.main.add(timer, forMode: .common)
        motionTimer = timer
    }

    private func stopMotion() {
        motionTimer?.invalidate()
        motionTimer = nil
    }

    private func observeReduceMotionChanges() {
        guard reduceMotionObserver == nil else { return }
        reduceMotionObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,
            object: nil, queue: .main
        ) { [weak self] _ in self?.applyMotionPreference() }
    }

    private func applyMotionPreference() {
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            stopMotion()
            renderCloud()
        } else {
            startMotion()
        }
    }

    private func renderCloud() {
        guard bounds.width > 0, bounds.height > 0,
              let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil,
                  pixelsWide: max(1, Int(bounds.width * (window?.backingScaleFactor ?? 2))),
                  pixelsHigh: max(1, Int(bounds.height * (window?.backingScaleFactor ?? 2))),
                  bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                  colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
              let graphics = NSGraphicsContext(bitmapImageRep: bitmap) else { return }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = graphics
        let context = graphics.cgContext
        context.clear(CGRect(origin: .zero, size: bitmap.size))
        let scale = window?.backingScaleFactor ?? 2
        context.scaleBy(x: scale, y: scale)

        let points = OrbParticleGeometry.positions(state: motionState, phase: phase)
        let paths = (0..<6).map { _ in CGMutablePath() }
        let edges = (0..<6).map { _ in CGMutablePath() }
        for point in points {
            let depth = min(1, max(0, point.depth))
            let bucket = min(5, max(0, Int(depth * 6)))
            // Keep fine particles, but not the subpixel, low-opacity marks that
            // disappeared over real desktop backgrounds at the 56-point size.
            let dotSize = 0.65 + depth * 0.65
            let x = bounds.midX + point.x * bounds.width * OrbParticleGeometry.referenceRadius - dotSize / 2
            let y = bounds.midY + point.y * bounds.height * OrbParticleGeometry.referenceRadius - dotSize / 2
            paths[bucket].addEllipse(in: CGRect(x: x, y: y, width: dotSize, height: dotSize))
            edges[bucket].addEllipse(in: CGRect(x: x - 0.3, y: y - 0.3,
                                               width: dotSize + 0.6, height: dotSize + 0.6))
        }
        for (index, path) in edges.enumerated() {
            context.setFillColor(Self.particleEdge.withAlphaComponent(0.48 + Double(index) * 0.065).cgColor)
            context.addPath(path)
            context.fillPath()
        }
        for (index, path) in paths.enumerated() {
            let alpha = 0.58 + Double(index) * 0.075
            context.setFillColor(Self.particleColor.withAlphaComponent(alpha).cgColor)
            context.addPath(path)
            context.fillPath()
        }
        NSGraphicsContext.restoreGraphicsState()
        cloudLayer.contents = bitmap.cgImage
        cloudLayer.opacity = 1
    }

    override func accessibilityPerformPress() -> Bool { onClick?(); return true }

    override func mouseDown(with event: NSEvent) {
        gesture.begin(at: window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation,
                      windowOrigin: window?.frame.origin ?? .zero)
    }

    override func mouseDragged(with event: NSEvent) {
        gesture.markDragged()
        if let origin = gesture.move(to: window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation) {
            window?.setFrameOrigin(origin)
            onMove?(origin)
        }
    }

    override func mouseUp(with event: NSEvent) {
        if gesture.end(at: window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation) {
            onClick?()
        }
    }
}
