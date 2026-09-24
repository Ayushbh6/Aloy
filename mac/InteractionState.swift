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
