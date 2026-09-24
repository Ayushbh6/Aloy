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
assert(cloud.masksToBounds && cloud.cornerRadius == 28)
assert(cloud.contents != nil)
// The circular mask remains within the window at every rotation angle.
for degrees in stride(from: 0, through: 360, by: 5) {
    let angle = Double(degrees) * .pi / 180
    cloud.transform = CATransform3DMakeRotation(angle, 0, 0, 1)
    let center = cloud.convert(CGPoint(x: 28, y: 28), to: root)
    assert(abs(center.x - 28) < 0.001 && abs(center.y - 28) < 0.001)
    for sample in 0..<72 {
        let theta = Double(sample) * .pi / 36
        let point = cloud.convert(CGPoint(x: 28 + 28 * cos(theta),
                                          y: 28 + 28 * sin(theta)), to: root)
        assert(point.x >= -0.001 && point.x <= 56.001 &&
               point.y >= -0.001 && point.y <= 56.001)
    }
}
cloud.transform = CATransform3DIdentity
orb.isRecording = true
assert(root.animation(forKey: "orbit") == nil && cloud.contents != nil)
print("Orb fixed-root and circular-layer checks passed")
