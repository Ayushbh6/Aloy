import AppKit
import QuartzCore

// Cached procedural particle cloud; only its centred child layer rotates.
final class OrbView: NSView {
    var onClick: (() -> Void)?
    var isRecording = false { didSet { if oldValue != isRecording { updateAppearance() } } }
    var isSpeaking = false { didSet { if oldValue != isSpeaking { updateAppearance() } } }
    var isProcessing = false {
        didSet { if oldValue != isProcessing && !isSpeaking { updateAppearance() } }
    }
    var hasError = false { didSet { if oldValue != hasError { updateAppearance() } } }
    private var gesture = OrbGesture()
    private let cloudLayer = CALayer()
    override var isOpaque: Bool { false }
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        wantsLayer = true
        if cloudLayer.superlayer == nil { layer?.addSublayer(cloudLayer) }
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        updateAppearance()
    }
    private func updateAppearance() {
        // AppKit owns the root layer geometry. Never transform that layer.
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        cloudLayer.bounds = CGRect(origin: .zero, size: bounds.size)
        cloudLayer.anchorPoint = CGPoint(x: 0.5, y: 0.5)
        cloudLayer.position = CGPoint(x: bounds.midX, y: bounds.midY)
        cloudLayer.cornerRadius = min(bounds.width, bounds.height) / 2
        cloudLayer.masksToBounds = true
        let scale = window?.backingScaleFactor ?? 2
        if let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil,
            pixelsWide: Int(bounds.width * scale), pixelsHigh: Int(bounds.height * scale),
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
           let graphics = NSGraphicsContext(bitmapImageRep: bitmap) {
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = graphics
            graphics.cgContext.scaleBy(x: scale, y: scale)
            drawCloud()
            NSGraphicsContext.restoreGraphicsState()
            cloudLayer.contentsScale = scale
            cloudLayer.contents = bitmap.cgImage
        }
        CATransaction.commit()
        let state = isRecording ? "Recording" : (hasError ? "Error" :
            (isSpeaking ? "Speaking" : (isProcessing ? "Processing" : "Ready")))
        setAccessibilityLabel("Aloy — " + state)
        cloudLayer.removeAnimation(forKey: "orbit")
        guard window != nil, !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { return }
        // Animate the cached native layer on the compositor, not 1,500 paths per frame.
        let orbit = CABasicAnimation(keyPath: "transform.rotation.z")
        orbit.fromValue = 0
        orbit.toValue = Double.pi * 2
        orbit.duration = isRecording ? 24 : (isSpeaking ? 14 : (isProcessing ? 6 : 120))
        orbit.repeatCount = .infinity
        cloudLayer.add(orbit, forKey: "orbit")
    }
    override func accessibilityPerformPress() -> Bool { onClick?(); return true }
    private func drawCloud() {
        let shell = bounds.insetBy(dx: 3.5, dy: 3.5)
        let body = NSBezierPath(ovalIn: shell)
        let context = NSGraphicsContext.current!.cgContext
        let palette: (core: NSColor, edge: NSColor, rim: NSColor, near: NSColor, far: NSColor)
        if isRecording {
            palette = (
                NSColor(calibratedRed: 0.26, green: 0.04, blue: 0.09, alpha: 0.94),
                NSColor(calibratedRed: 0.66, green: 0.20, blue: 0.26, alpha: 0.73),
                NSColor(calibratedRed: 1, green: 0.54, blue: 0.57, alpha: 0.86),
                .systemRed, NSColor(calibratedRed: 1, green: 0.65, blue: 0.66, alpha: 1))
        } else if isSpeaking {
            palette = (
                NSColor(calibratedRed: 0.04, green: 0.25, blue: 0.12, alpha: 0.96),
                NSColor(calibratedRed: 0.16, green: 0.53, blue: 0.25, alpha: 0.78),
                NSColor(calibratedRed: 0.58, green: 1, blue: 0.58, alpha: 0.92),
                NSColor(calibratedRed: 0.47, green: 1, blue: 0.52, alpha: 1),
                NSColor(calibratedRed: 0.77, green: 1, blue: 0.76, alpha: 1))
        } else if hasError {
            palette = (
                NSColor(calibratedRed: 0.30, green: 0.13, blue: 0.04, alpha: 0.94),
                NSColor(calibratedRed: 0.65, green: 0.36, blue: 0.13, alpha: 0.73),
                NSColor(calibratedRed: 1, green: 0.75, blue: 0.38, alpha: 0.86),
                .systemOrange, NSColor(calibratedRed: 1, green: 0.85, blue: 0.55, alpha: 1))
        } else {
            palette = (
                NSColor(calibratedRed: 0.08, green: 0.16, blue: 0.36, alpha: 0.94),
                NSColor(calibratedRed: 0.30, green: 0.54, blue: 0.78, alpha: 0.73),
                NSColor(calibratedRed: 0.66, green: 0.86, blue: 1, alpha: 0.82),
                NSColor(calibratedRed: 0.74, green: 0.63, blue: 1, alpha: 1),
                NSColor(calibratedRed: 0.42, green: 0.83, blue: 1, alpha: 1))
        }
        // The original cosmic cloud, inside a more visible sky-blue glass sphere.
        NSGradient(starting: palette.core, ending: palette.edge)?
            .draw(in: body, relativeCenterPosition: .zero)
        context.saveGState()
        context.addEllipse(in: shell.insetBy(dx: 1, dy: 1))
        context.clip()
        context.setBlendMode(.plusLighter)
        let paths = (0..<8).map { _ in CGMutablePath() }
        let colors = [palette.near, palette.far]
        for i in 0..<1500 {
            let n = Double(i)
            let angle = n * 2.399963
            let inclination = n * 0.754877
            let radius = 2 + 22 * pow(Double(i % 97) / 97, 1.35)
            let x = radius * sin(angle)
            let y = radius * (i % 5 < 2 ? sin(angle) * cos(angle) : cos(angle))
            let z = y * sin(inclination)
            let projection = 1 + z / 90
            let px = bounds.midX + x * projection
            let py = bounds.midY + y * cos(inclination) * projection
            let depth = min(3, max(0, Int((z + radius) / (2 * radius) * 4)))
            let bucket = (i % 3 == 0 ? 0 : 4) + depth
            let size = (isSpeaking ? 1.3 : 0.9) * projection
            paths[bucket].addEllipse(in: CGRect(x: px, y: py, width: size, height: size))
        }
        for (index, path) in paths.enumerated() {
            context.setFillColor(colors[index / 4].withAlphaComponent(0.2 + Double(index % 4) * 0.12).cgColor)
            context.addPath(path)
            context.fillPath()
        }
        context.restoreGState()
        body.lineWidth = 1.3
        palette.rim.setStroke()
        body.stroke()
        let innerEdge = NSBezierPath(ovalIn: shell.insetBy(dx: 1.3, dy: 1.3))
        innerEdge.lineWidth = 0.65
        NSColor.white.withAlphaComponent(0.28).setStroke()
        innerEdge.stroke()
    }
    override func mouseDown(with event: NSEvent) {
        gesture.begin(at: window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation,
                      windowOrigin: window?.frame.origin ?? .zero)
    }
    override func mouseDragged(with event: NSEvent) {
        gesture.markDragged()
        if let origin = gesture.move(to: window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation) {
            window?.setFrameOrigin(origin)
        }
    }
    override func mouseUp(with event: NSEvent) {
        if gesture.end(at: window?.convertPoint(toScreen: event.locationInWindow) ?? NSEvent.mouseLocation) { onClick?() }
    }
}
