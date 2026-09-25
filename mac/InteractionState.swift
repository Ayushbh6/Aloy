import Foundation
import CoreGraphics

// Shared by the native shell and its offline behavioral checks.
struct OrbGesture {
    private var start: CGPoint?
    private var origin = CGPoint.zero
    private var dragged = false

    mutating func begin(at point: CGPoint, windowOrigin: CGPoint) {
        start = point
        origin = windowOrigin
        dragged = false
    }
    mutating func move(to point: CGPoint) -> CGPoint? {
        guard let start else { return nil }
        let dx = point.x - start.x, dy = point.y - start.y
        if hypot(dx, dy) >= 5 { dragged = true }
        return CGPoint(x: origin.x + dx, y: origin.y + dy)
    }
    mutating func markDragged() { dragged = true }
    mutating func end(at point: CGPoint) -> Bool {
        guard start != nil else { return false }
        _ = move(to: point)
        let click = !dragged
        start = nil
        return click
    }
}

struct OperationGate {
    private(set) var id = UUID().uuidString
    mutating func invalidate() { id = UUID().uuidString }
    func accepts(_ operationID: String) -> Bool { operationID == id }
}

// A held shortcut is one gesture, regardless of key-repeat events.
struct ShortcutGesture {
    private var pressed = false
    mutating func down() { pressed = true }
    mutating func up() -> Bool {
        let fire = pressed
        pressed = false
        return fire
    }
}

// A reply remains in its speaking state while the next audio chunk is generated.
struct ReplyAudioState {
    private(set) var generationComplete = true
    private(set) var receivedAudio = false

    mutating func begin() {
        generationComplete = false
        receivedAudio = false
    }
    mutating func audioArrived() { receivedAudio = true }
    mutating func finishGeneration() { generationComplete = true }
    mutating func reset() {
        generationComplete = true
        receivedAudio = false
    }
    func isSpeaking(hasPlayback: Bool) -> Bool {
        receivedAudio && (!generationComplete || hasPlayback)
    }
}

enum OrbMotionState: CaseIterable {
    case quiet, listening, thinking, speaking, attention

    var angularSpeed: Double {
        switch self {
        case .quiet: return 0.55
        case .listening: return 1.2
        case .thinking: return 2.2
        case .speaking: return 0.55
        case .attention: return 0.55
        }
    }

    var breathingAmplitude: Double {
        switch self {
        case .speaking: return 0.075
        default: return 0.025
        }
    }
}

struct OrbParticlePosition: Equatable {
    let x: Double
    let y: Double
    let z: Double
    let depth: Double
}

/// The Thinking-state distribution is shared by every state. The caller advances
/// phase continuously across state changes; state changes only speed and breath.
enum OrbParticleGeometry {
    static let count = 1_050
    static let referenceScale = 56.0 / 252.0
    static let referenceRadius = 91.0 / 252.0

    static func breathScale(state: OrbMotionState, phase: Double) -> Double {
        1 + sin(phase * 4) * state.breathingAmplitude
    }

    static func positions(state: OrbMotionState, phase: Double) -> [OrbParticlePosition] {
        let breath = breathScale(state: state, phase: phase)
        let tilt = 0.48
        return (0..<count).map { index in
            let n = Double(index)
            let latitude = 1 - 2 * ((n * 0.61803398875).truncatingRemainder(dividingBy: 1))
            let azimuthUnit = (n * 0.754877666).truncatingRemainder(dividingBy: 1)
            let radialUnit = (n * 0.569840291).truncatingRemainder(dividingBy: 1)
            let radial = sqrt(max(0, 1 - latitude * latitude))
            let angle = azimuthUnit * .pi * 2 + phase + latitude * 2
            let falloff = 0.2 + 0.8 * sqrt(radialUnit)
            let x = cos(angle) * radial * falloff
            let z = sin(angle) * radial * falloff
            let py = latitude * falloff
            let y = py * cos(tilt) - z * sin(tilt)
            let depthAxis = py * sin(tilt) + z * cos(tilt)
            return OrbParticlePosition(x: x * breath, y: y * breath, z: depthAxis * breath,
                                       depth: (depthAxis + 1.4) / 2.8)
        }
    }
}

enum CompanionBubblePlacement {
    static func frame(orb: CGRect, visibleScreen: CGRect, size: CGSize,
                      gap: CGFloat = 12, inset: CGFloat = 8) -> CGRect {
        let safe = visibleScreen.insetBy(dx: inset, dy: inset)
        let fitted = CGSize(width: min(size.width, safe.width),
                            height: min(size.height, safe.height))
        let leftX = orb.minX - gap - fitted.width
        let rightX = orb.maxX + gap
        let x: CGFloat
        if leftX >= safe.minX {
            x = leftX
        } else if rightX + fitted.width <= safe.maxX {
            x = rightX
        } else {
            x = min(max(orb.midX - fitted.width / 2, safe.minX), safe.maxX - fitted.width)
        }

        let aboveY = orb.maxY + gap
        let belowY = orb.minY - gap - fitted.height
        let y = aboveY + fitted.height <= safe.maxY
            ? aboveY
            : min(max(belowY, safe.minY), safe.maxY - fitted.height)
        return CGRect(origin: CGPoint(x: x, y: y), size: fitted)
    }
}
