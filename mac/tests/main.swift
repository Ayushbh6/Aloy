import Foundation
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
