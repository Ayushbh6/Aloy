import AppKit
import QuartzCore

// Cached procedural particle cloud; only its centred child layer rotates.
final class OrbView: NSView {
    var onClick: (() -> Void)?
    var isRecording = false { didSet { updateAppearance() } }
    var isSpeaking = false { didSet { updateAppearance() } }
    var isProcessing = false { didSet { updateAppearance() } }
    var hasError = false { didSet { updateAppearance() } }
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
        orbit.duration = isProcessing ? 6 : (isSpeaking ? 14 : (isRecording ? 24 : 120))
        orbit.repeatCount = .infinity
        cloudLayer.add(orbit, forKey: "orbit")
    }
    override func accessibilityPerformPress() -> Bool { onClick?(); return true }
    private func drawCloud() {
        let shell = bounds.insetBy(dx: 3.5, dy: 3.5)
        let body = NSBezierPath(ovalIn: shell)
        let context = NSGraphicsContext.current!.cgContext
        let accent: NSColor? = isRecording ? .systemRed : (hasError ? .systemOrange :
            (isSpeaking ? .systemMint : (isProcessing ? .systemPurple : nil)))
        // The original cosmic cloud, inside a more visible sky-blue glass sphere.
        NSGradient(starting: NSColor(calibratedRed: 0.08, green: 0.16, blue: 0.36, alpha: 0.94),
                   ending: NSColor(calibratedRed: 0.30, green: 0.54, blue: 0.78, alpha: 0.73))?
            .draw(in: body, relativeCenterPosition: .zero)
        context.saveGState()
        context.addEllipse(in: shell.insetBy(dx: 1, dy: 1))
        context.clip()
        context.setBlendMode(.plusLighter)
        let paths = (0..<8).map { _ in CGMutablePath() }
        let colors = [accent ?? NSColor(calibratedRed: 0.74, green: 0.63, blue: 1, alpha: 1),
                      accent ?? NSColor(calibratedRed: 0.42, green: 0.83, blue: 1, alpha: 1)]
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
        NSColor(calibratedRed: 0.66, green: 0.86, blue: 1, alpha: 0.82).setStroke()
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
