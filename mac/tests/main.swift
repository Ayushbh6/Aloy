import Foundation
import AppKit
import CoreGraphics
var drag = OrbGesture()
drag.begin(at: CGPoint(x: 100, y: 100), windowOrigin: .zero)
assert(drag.move(to: CGPoint(x: 150, y: 170)) == CGPoint(x: 50, y: 70))
assert(!drag.end(at: CGPoint(x: 150, y: 170)))
// Returning to the starting point must never turn a drag into a click.
drag.begin(at: .zero, windowOrigin: .zero)
_ = drag.move(to: CGPoint(x: 20, y: 20))
assert(!drag.end(at: .zero))
drag.begin(at: .zero, windowOrigin: .zero)
assert(drag.end(at: CGPoint(x: 1, y: 1)))
drag.begin(at: .zero, windowOrigin: .zero)
drag.markDragged()
assert(!drag.end(at: .zero))
var gate = OperationGate()
let old = gate.id
gate.invalidate()
assert(!gate.accepts(old))
assert(gate.accepts(gate.id))
print("Native interaction checks passed")

var shortcut = ShortcutGesture()
assert(!shortcut.up())
shortcut.down()
shortcut.down() // OS repeat must not start and immediately send.
assert(shortcut.up())
assert(!shortcut.up())
shortcut.down()
assert(shortcut.up())
print("Shortcut press/release checks passed")

// Recording and cancellation shortcuts have independent pressed state.
var shortcuts: [UInt32: ShortcutGesture] = [:]
shortcuts[1, default: ShortcutGesture()].down()
assert(!shortcuts[2, default: ShortcutGesture()].up())
shortcuts[2, default: ShortcutGesture()].down()
assert(shortcuts[2, default: ShortcutGesture()].up())
assert(shortcuts[1, default: ShortcutGesture()].up())

// The AppKit-owned root must stay fixed: rotating it clips and loses the orb.
let orb = OrbView(frame: NSRect(x: 0, y: 0, width: 56, height: 56))
let host = NSWindow(contentRect: orb.frame, styleMask: [.borderless],
                    backing: .buffered, defer: false)
host.contentView = orb
let root = orb.layer!
let cloud = root.sublayers!.first!
assert(root.animation(forKey: "orbit") == nil)
assert(cloud.anchorPoint == CGPoint(x: 0.5, y: 0.5))
assert(cloud.position == CGPoint(x: 28, y: 28))
assert(cloud.bounds.size == CGSize(width: 56, height: 56))
assert(!cloud.masksToBounds && cloud.cornerRadius == 0)
assert(cloud.contents != nil)
assert(CATransform3DIsIdentity(cloud.transform))
// The desktop may be bright while macOS is dark (and vice versa). The actual
// raster must contain both dark edging and bright cores, with no solid backdrop.
let particleRaster = NSBitmapImageRep(cgImage: cloud.contents as! CGImage)
var darkMarks = 0
var brightMarks = 0
for y in 0..<particleRaster.pixelsHigh {
    for x in 0..<particleRaster.pixelsWide {
        guard let color = particleRaster.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB),
              color.alphaComponent > 0.4 else { continue }
        if color.greenComponent < 0.4 { darkMarks += 1 }
        if color.greenComponent > 0.7 { brightMarks += 1 }
    }
}
assert(darkMarks > 20 && brightMarks > 100)
assert(particleRaster.colorAt(x: 0, y: 0)!.alphaComponent == 0)
print("Orb dual-tone contrast and transparent-background checks passed")
orb.isRecording = true
orb.isSpeaking = true
assert(orb.accessibilityLabel() == "Aloy — Speaking")
orb.isSpeaking = false
orb.isProcessing = true
assert(orb.accessibilityLabel() == "Aloy — Thinking")
orb.isProcessing = false
assert(orb.accessibilityLabel() == "Aloy — Listening")
assert(root.animation(forKey: "orbit") == nil && cloud.contents != nil)
print("Orb fixed-footprint and transparent particle-layer checks passed")

var replyAudio = ReplyAudioState()
replyAudio.begin()
assert(!replyAudio.isSpeaking(hasPlayback: false)) // Waiting for first audio stays blue.
replyAudio.audioArrived()
assert(replyAudio.isSpeaking(hasPlayback: true))
assert(replyAudio.isSpeaking(hasPlayback: false)) // The next sentence is still generating.
replyAudio.finishGeneration()
assert(replyAudio.isSpeaking(hasPlayback: true)) // Playback may outlast generation.
assert(!replyAudio.isSpeaking(hasPlayback: false))
replyAudio.begin()
replyAudio.audioArrived()
replyAudio.reset() // Either shortcut cancels the reply.
assert(!replyAudio.isSpeaking(hasPlayback: false))
print("Reply colour continuity checks passed")

// Every semantic state uses one particle silhouette and radial falloff. Breathing
// is a uniform scale; it must not change the Thinking-state curvature.
let quietShape = OrbParticleGeometry.positions(state: .quiet, phase: 0)
for state in OrbMotionState.allCases {
    assert(OrbParticleGeometry.positions(state: state, phase: 0) == quietShape)
}
assert(quietShape.count == 1_050)
let phase = 0.73
let quietScale = OrbParticleGeometry.breathScale(state: .quiet, phase: phase)
let speakingScale = OrbParticleGeometry.breathScale(state: .speaking, phase: phase)
assert(abs(quietScale - speakingScale) > 0.001)
let quietRadii = OrbParticleGeometry.positions(state: .quiet, phase: phase).map {
    ($0.x * $0.x + $0.y * $0.y + $0.z * $0.z).squareRoot() / quietScale
}.sorted()
let speakingRadii = OrbParticleGeometry.positions(state: .speaking, phase: phase).map {
    ($0.x * $0.x + $0.y * $0.y + $0.z * $0.z).squareRoot() / speakingScale
}.sorted()
assert(zip(quietRadii, speakingRadii).allSatisfy { pair in abs(pair.0 - pair.1) < 1e-10 })
let phaseBeforeTransition = 0.4
let phaseAfterTransition = phaseBeforeTransition + 0.25 * 0.12 * OrbMotionState.thinking.angularSpeed
assert(phaseAfterTransition > phaseBeforeTransition)
assert(abs(phaseAfterTransition -
    (phaseBeforeTransition + 0.25 * 0.12 * OrbMotionState.quiet.angularSpeed)) > 0.01)
print("Shared orb curvature, continuous phase, and speaking-breath checks passed")

// The compact bubble remains fully within the visible screen around orb placements.
let screenFrame = CGRect(x: 0, y: 0, width: 1440, height: 900)
let bubbleSize = CGSize(width: 360, height: 232)
for orbFrame in [
    CGRect(x: 1360, y: 370, width: 56, height: 56),
    CGRect(x: 24, y: 370, width: 56, height: 56),
    CGRect(x: 680, y: 835, width: 56, height: 56),
    CGRect(x: 680, y: 10, width: 56, height: 56),
] {
    let frame = CompanionBubblePlacement.frame(orb: orbFrame, visibleScreen: screenFrame,
                                               size: bubbleSize)
    assert(screenFrame.insetBy(dx: 8, dy: 8).contains(frame))
    assert(frame.intersects(orbFrame) == false)
}
let smallScreen = CGRect(x: 0, y: 0, width: 320, height: 260)
let fittedBubble = CompanionBubblePlacement.frame(
    orb: CGRect(x: 250, y: 100, width: 56, height: 56), visibleScreen: smallScreen,
    size: CGSize(width: 360, height: 232))
assert(smallScreen.insetBy(dx: 8, dy: 8).contains(fittedBubble))
print("Bubble screen-bound placement checks passed")

// A background panel must accept the first click, including the blank padding
// around its tiny symbol. This invokes no live app or desktop interaction.
var dismissCount = 0
let dismissButton = BubbleControlButton(symbol: "minus", label: "Dismiss") { dismissCount += 1 }
assert(dismissButton.acceptsFirstMouse(for: nil))
assert(!dismissButton.mouseDownCanMoveWindow)
assert(dismissButton.bounds.size == NSSize(width: 36, height: 32))
assert(dismissButton.hitTest(NSPoint(x: 2, y: 2)) === dismissButton)
dismissButton.performClick(nil)
assert(dismissCount == 1)
print("Bubble first-click, padded hit target, and single-dispatch checks passed")

// Shared backend/native proposal contract. These checks decode synthetic data only;
// they never target a live application or invoke ComputerActionExecution.perform.
let fixtureURL = URL(fileURLWithPath: "tests/fixtures/chunk2_action_contract.json")
let fixtureData = try Data(contentsOf: fixtureURL)
let fixtureObject = try JSONSerialization.jsonObject(with: fixtureData) as! [String: Any]
let fixtureTarget = CapturedWindowTarget(bundleID: "com.apple.Safari", processID: getpid(),
    windowID: 123, title: "Synthetic target window", width: 960, height: 640)
let proposal = ComputerActionProposal.decode(fixtureObject, capturedTarget: fixtureTarget)!
assert(proposal.kind == .click && proposal.normalizedX == 0.25 && proposal.normalizedY == 0.75)
var executeEvent = fixtureObject
executeEvent["event"] = "action_execute"
assert(proposal.matchesExecution(executeEvent))
executeEvent["parameters"] = ["x": 0.5, "y": 0.75]
assert(!proposal.matchesExecution(executeEvent))
var booleanCoordinate = fixtureObject
booleanCoordinate["parameters"] = ["x": true, "y": 0.75]
assert(ComputerActionProposal.decode(booleanCoordinate, capturedTarget: fixtureTarget) == nil)
var staleExpiry = fixtureObject
staleExpiry["expires_at"] = "not-an-ISO8601-date"
assert(ComputerActionProposal.decode(staleExpiry, capturedTarget: fixtureTarget) == nil)
var fractionalExpiry = fixtureObject
fractionalExpiry["expires_at"] = "2099-01-01T00:00:00.123Z"
assert(ComputerActionProposal.decode(fractionalExpiry, capturedTarget: fixtureTarget) != nil)
var controlText = fixtureObject
controlText["action"] = "desktop.type_text"
controlText["target"] = ["bundle_id": "com.apple.Safari", "window_id": 123]
controlText["parameters"] = ["text": "submit\nnow"]
assert(ComputerActionProposal.decode(controlText, capturedTarget: fixtureTarget) == nil)
var shortName = fixtureObject
shortName["action"] = "click"
assert(ComputerActionProposal.decode(shortName, capturedTarget: fixtureTarget) == nil)

var deniedQueue = PendingActionExecution()
assert(deniedQueue.ticket == nil) // A denied proposal is never scheduled for execution.
assert(!deniedQueue.consume(proposalID: proposal.proposalID, operationID: proposal.operationID,
    runID: proposal.runID, operationGateID: "gate", currentRunID: proposal.runID))
var cancelledQueue = PendingActionExecution()
assert(cancelledQueue.schedule(proposal, operationGateID: "gate") != nil)
assert(cancelledQueue.cancel(proposalID: proposal.proposalID) != nil)
assert(!cancelledQueue.consume(proposalID: proposal.proposalID, operationID: proposal.operationID,
    runID: proposal.runID, operationGateID: "gate", currentRunID: proposal.runID))
var replayQueue = PendingActionExecution()
assert(replayQueue.schedule(proposal, operationGateID: "gate") != nil)
assert(replayQueue.consume(proposalID: proposal.proposalID, operationID: proposal.operationID,
    runID: proposal.runID, operationGateID: "gate", currentRunID: proposal.runID))
assert(!replayQueue.consume(proposalID: proposal.proposalID, operationID: proposal.operationID,
    runID: proposal.runID, operationGateID: "gate", currentRunID: proposal.runID))
var staleQueue = PendingActionExecution()
assert(staleQueue.schedule(proposal, operationGateID: "old-gate") != nil)
assert(!staleQueue.consume(proposalID: proposal.proposalID, operationID: proposal.operationID,
    runID: proposal.runID, operationGateID: "new-gate", currentRunID: proposal.runID))
assert(staleQueue.ticket == nil)
assert(staleQueue.schedule(proposal, operationGateID: "new-gate") != nil)
let expiringTicket = ActionExecutionTicket(proposal: proposal, operationGateID: "gate")
assert(!expiringTicket.isCurrent(proposalID: proposal.proposalID, operationID: proposal.operationID,
    runID: proposal.runID, operationGateID: "gate", currentRunID: proposal.runID,
    now: .distantFuture))
print("Synthetic approval schema, denial, cancellation, expiry, and replay checks passed")
