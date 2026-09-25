import AppKit
import ApplicationServices
import CoreGraphics
import SwiftUI

struct FocusedAXField {
    let element: AXUIElement
    let window: AXUIElement
    let role: String

    func isSameAs(_ other: FocusedAXField) -> Bool {
        CFEqual(element, other.element) && CFEqual(window, other.window) && role == other.role
    }
}

enum ComputerActionKind: String, Equatable {
    case click = "desktop.click"
    case typeText = "desktop.type_text"
    case hideApp = "desktop.hide_app"

    var needsAccessibility: Bool { self != .hideApp }
    var title: String {
        switch self {
        case .click: "Click a control"
        case .typeText: "Type text"
        case .hideApp: "Hide an app"
        }
    }
}

struct ComputerActionProposal {
    let proposalID: String
    let operationID: String
    let runID: String
    let toolCallID: String
    let kind: ComputerActionKind
    let bundleID: String
    let processID: Int32
    let windowID: UInt32
    let appName: String
    let windowTitle: String
    let windowWidth: Double
    let windowHeight: Double
    let normalizedX: Double?
    let normalizedY: Double?
    let text: String?
    let focusedField: FocusedAXField?
    let expiresAtText: String
    let expiresAt: Date

    var requiresAccessibility: Bool { kind.needsAccessibility }

    static func decode(_ object: [String: Any],
                       capturedTarget: CapturedWindowTarget) -> ComputerActionProposal? {
        guard object["event"] as? String == "action_requested",
              Set(object.keys) == Set(["event", "proposal_id", "operation_id", "run_id",
                  "tool_call_id", "action", "target", "parameters", "expires_at"]),
              let proposalID = nonempty(object["proposal_id"]),
              let operationID = nonempty(object["operation_id"]),
              let runID = nonempty(object["run_id"]),
              let toolCallID = nonempty(object["tool_call_id"]),
              let kind = ComputerActionKind(rawValue: object["action"] as? String ?? ""),
              let target = object["target"] as? [String: Any],
              target["bundle_id"] as? String == capturedTarget.bundleID,
              let parameters = object["parameters"] as? [String: Any] else { return nil }

        let expectedTargetKeys: Set<String> = kind == .hideApp
            ? ["bundle_id"] : ["bundle_id", "window_id"]
        guard Set(target.keys) == expectedTargetKeys else { return nil }
        if kind.needsAccessibility,
           unsignedWindowID(target["window_id"]) != capturedTarget.windowID { return nil }

        let x: Double?
        let y: Double?
        let text: String?
        switch kind {
        case .click:
            guard Set(parameters.keys) == ["x", "y"] else { return nil }
            guard let px = finiteNumber(parameters["x"]), let py = finiteNumber(parameters["y"]),
                  (0...1).contains(px), (0...1).contains(py) else { return nil }
            x = px
            y = py
            text = nil
        case .typeText:
            guard Set(parameters.keys) == ["text"] else { return nil }
            guard let value = parameters["text"] as? String,
                  !value.isEmpty, value.utf16.count <= 4_000,
                  !value.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
            else { return nil }
            x = nil
            y = nil
            text = value
        case .hideApp:
            guard parameters.isEmpty else { return nil }
            x = nil
            y = nil
            text = nil
        }

        guard let expiryText = object["expires_at"] as? String,
              let expiry = strictISO8601Date(expiryText) else { return nil }
        guard expiry > Date() else { return nil }
        let focusedField = kind == .typeText
            ? ComputerActionExecution.captureFocusedField(target: capturedTarget) : nil
        return ComputerActionProposal(
            proposalID: proposalID, operationID: operationID, runID: runID,
            toolCallID: toolCallID, kind: kind, bundleID: capturedTarget.bundleID,
            processID: capturedTarget.processID, windowID: capturedTarget.windowID,
            appName: NSRunningApplication(processIdentifier: capturedTarget.processID)?.localizedName
                ?? capturedTarget.bundleID,
            windowTitle: capturedTarget.title,
            windowWidth: capturedTarget.width, windowHeight: capturedTarget.height,
            normalizedX: x, normalizedY: y, text: text, focusedField: focusedField,
            expiresAtText: expiryText, expiresAt: expiry)
    }

    func matchesExecution(_ object: [String: Any]) -> Bool {
        guard object["event"] as? String == "action_execute",
              Set(object.keys) == Set(["event", "proposal_id", "operation_id", "run_id",
                  "tool_call_id", "action", "target", "parameters", "expires_at"]),
              object["proposal_id"] as? String == proposalID,
              object["operation_id"] as? String == operationID,
              object["run_id"] as? String == runID,
              object["tool_call_id"] as? String == toolCallID,
              object["action"] as? String == kind.rawValue,
              object["expires_at"] as? String == expiresAtText,
              strictISO8601Date(expiresAtText) == expiresAt,
              expiresAt > Date(),
              let target = object["target"] as? [String: Any],
              target["bundle_id"] as? String == bundleID,
              let parameters = object["parameters"] as? [String: Any] else { return false }
        let expectedTargetKeys: Set<String> = kind == .hideApp
            ? ["bundle_id"] : ["bundle_id", "window_id"]
        guard Set(target.keys) == expectedTargetKeys else { return false }
        if kind.needsAccessibility && Self.unsignedWindowID(target["window_id"]) != windowID {
            return false
        }
        switch kind {
        case .click:
            return Set(parameters.keys) == ["x", "y"] &&
                Self.finiteNumber(parameters["x"]) == normalizedX &&
                Self.finiteNumber(parameters["y"]) == normalizedY
        case .typeText:
            return Set(parameters.keys) == ["text"] && parameters["text"] as? String == text
        case .hideApp:
            return parameters.isEmpty
        }
    }

    private static func nonempty(_ value: Any?) -> String? {
        guard let value = value as? String, !value.isEmpty else { return nil }
        return value
    }

    private static func finiteNumber(_ value: Any?) -> Double? {
        guard let number = value as? NSNumber else { return nil }
        guard CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let result = number.doubleValue
        return result.isFinite ? result : nil
    }

    private static func unsignedWindowID(_ value: Any?) -> UInt32? {
        guard let number = value as? NSNumber else { return nil }
        guard CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let integer = number.int64Value
        guard integer > 0, integer <= Int64(UInt32.max),
              number.doubleValue == Double(integer) else { return nil }
        return UInt32(integer)
    }
}

private func strictISO8601Date(_ text: String) -> Date? {
    for options: ISO8601DateFormatter.Options in [
        [.withInternetDateTime, .withFractionalSeconds], [.withInternetDateTime]
    ] {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = options
        if let date = formatter.date(from: text) { return date }
    }
    return nil
}

struct ActionExecutionTicket: Equatable {
    let proposalID: String
    let operationID: String
    let runID: String
    let operationGateID: String
    let expiresAt: Date

    init(proposal: ComputerActionProposal, operationGateID: String) {
        proposalID = proposal.proposalID
        operationID = proposal.operationID
        runID = proposal.runID
        self.operationGateID = operationGateID
        expiresAt = proposal.expiresAt
    }

    func isCurrent(proposalID: String, operationID: String, runID: String,
                   operationGateID: String, currentRunID: String?, now: Date = Date()) -> Bool {
        self.proposalID == proposalID && self.operationID == operationID && self.runID == runID &&
            self.operationGateID == operationGateID && currentRunID == runID && expiresAt > now
    }
}

struct PendingActionExecution {
    private(set) var ticket: ActionExecutionTicket?

    mutating func schedule(_ proposal: ComputerActionProposal,
                           operationGateID: String) -> ActionExecutionTicket? {
        guard ticket == nil else { return nil }
        let next = ActionExecutionTicket(proposal: proposal, operationGateID: operationGateID)
        ticket = next
        return next
    }

    mutating func cancel(proposalID: String? = nil) -> ActionExecutionTicket? {
        guard let ticket, proposalID == nil || ticket.proposalID == proposalID else { return nil }
        self.ticket = nil
        return ticket
    }

    mutating func consume(proposalID: String, operationID: String, runID: String,
                          operationGateID: String, currentRunID: String?, now: Date = Date()) -> Bool {
        guard let ticket, ticket.proposalID == proposalID,
              ticket.operationID == operationID, ticket.runID == runID else { return false }
        self.ticket = nil
        return ticket.isCurrent(proposalID: proposalID, operationID: operationID,
                runID: runID, operationGateID: operationGateID, currentRunID: currentRunID,
                now: now)
    }
}

enum ComputerActionExecution {
    case completed([String: Any])
    case stale(String)
    case failed(String)

    static var accessibilityTrusted: Bool { AXIsProcessTrusted() }

    static func prepareFocus(_ proposal: ComputerActionProposal) -> Bool {
        guard proposal.expiresAt > Date(),
              let window = windowInfo(id: proposal.windowID, pid: proposal.processID),
              matches(window, proposal: proposal) else { return false }
        if proposal.kind == .hideApp { return true }
        return raiseExactWindowIfNeeded(proposal, window: window)
    }

    private static func focusedTarget(_ proposal: ComputerActionProposal, window: WindowInfo) -> Bool {
        NSWorkspace.shared.frontmostApplication?.processIdentifier == proposal.processID &&
            isTopmost(windowID: proposal.windowID,
                      at: CGPoint(x: window.frame.midX, y: window.frame.midY))
    }

    static func targetStillMatches(_ proposal: ComputerActionProposal) -> Bool {
        guard let app = NSRunningApplication(processIdentifier: proposal.processID),
              !app.isTerminated, app.bundleIdentifier == proposal.bundleID,
              let window = windowInfo(id: proposal.windowID, pid: proposal.processID) else { return false }
        return matches(window, proposal: proposal)
    }

    static func perform(_ proposal: ComputerActionProposal) -> ComputerActionExecution {
        guard proposal.expiresAt > Date() else { return .stale("This approval expired.") }
        if proposal.requiresAccessibility && !AXIsProcessTrusted() {
            return .failed("Accessibility permission is required. Enable it in System Settings, then ask again.")
        }
        guard let application = NSRunningApplication(processIdentifier: proposal.processID),
              !application.isTerminated, application.bundleIdentifier == proposal.bundleID else {
            return .stale("The approved app is no longer available.")
        }
        guard let window = windowInfo(id: proposal.windowID, pid: proposal.processID),
              matches(window, proposal: proposal) else {
            return .stale("The approved window changed size, title, or closed.")
        }

        switch proposal.kind {
        case .click:
            guard let x = proposal.normalizedX, let y = proposal.normalizedY else {
                return .failed("The click coordinates are invalid.")
            }
            guard focusedTarget(proposal, window: window) else {
                return .stale("Aloy could not verify and focus only the approved window.")
            }
            guard let focusedWindow = windowInfo(id: proposal.windowID, pid: proposal.processID),
                  matches(focusedWindow, proposal: proposal) else {
                return .stale("The approved window changed while it was being focused.")
            }
            guard proposal.expiresAt > Date() else { return .stale("This approval expired before the click.") }
            let point = CGPoint(x: focusedWindow.frame.minX + focusedWindow.frame.width * x,
                                y: focusedWindow.frame.minY + focusedWindow.frame.height * y)
            guard isTopmost(windowID: proposal.windowID, at: point) else {
                return .stale("The approved point is covered by another window.")
            }
            guard postClick(at: point) else { return .failed("macOS could not send the click.") }
            return .completed(["clicked": true, "window_id": Int(proposal.windowID),
                               "x": x, "y": y])
        case .typeText:
            guard focusedTarget(proposal, window: window),
                  let text = proposal.text, let originalField = proposal.focusedField,
                  let currentField = captureFocusedField(target: proposal),
                  originalField.isSameAs(currentField) else {
                return .stale("The approved window no longer has the same focused text field.")
            }
            guard proposal.expiresAt > Date() else { return .stale("This approval expired before the text was inserted.") }
            guard AXUIElementSetAttributeValue(originalField.element,
                    kAXSelectedTextAttribute as CFString, text as CFString) == .success else {
                return .failed("macOS could not insert the approved text into the verified field.")
            }
            return .completed(["typed": true, "character_count": text.count,
                               "window_id": Int(proposal.windowID)])
        case .hideApp:
            guard proposal.expiresAt > Date() else { return .stale("This approval expired before the app could be hidden.") }
            guard application.hide() else { return .failed("macOS could not hide the approved app.") }
            return .completed(["hidden": true, "bundle_id": proposal.bundleID])
        }
    }

    private struct WindowInfo {
        let frame: CGRect
        let title: String
    }

    static func matchesCapturedTarget(_ target: CapturedWindowTarget) -> Bool {
        guard let window = windowInfo(id: target.windowID, pid: target.processID) else { return false }
        return window.title == target.title && abs(window.frame.width - target.width) <= 1 &&
            abs(window.frame.height - target.height) <= 1
    }

    static func captureFocusedField(target: CapturedWindowTarget) -> FocusedAXField? {
        guard AXIsProcessTrusted(), matchesCapturedTarget(target) else {
            return nil
        }
        let app = AXUIElementCreateApplication(target.processID)
        guard let element = attributeElement(app, kAXFocusedUIElementAttribute),
              let pid = processID(of: element), pid == target.processID,
              let role = attributeString(element, kAXRoleAttribute),
              [kAXTextFieldRole, kAXTextAreaRole].contains(role),
              attributeString(element, kAXSubroleAttribute) != kAXSecureTextFieldSubrole,
              let window = attributeElement(element, kAXWindowAttribute),
              let windowTitle = attributeString(window, kAXTitleAttribute),
              let windowFrame = accessibilityFrame(window),
              let targetWindow = windowInfo(id: target.windowID, pid: target.processID),
              windowTitle == target.title, framesMatch(windowFrame, targetWindow.frame) else {
            return nil
        }
        var settable = DarwinBoolean(false)
        guard AXUIElementIsAttributeSettable(element, kAXSelectedTextAttribute as CFString,
                                              &settable) == .success, settable.boolValue else {
            return nil
        }
        return FocusedAXField(element: element, window: window, role: role)
    }

    static func captureFocusedField(target: ComputerActionProposal) -> FocusedAXField? {
        captureFocusedField(target: CapturedWindowTarget(
            bundleID: target.bundleID, processID: target.processID, windowID: target.windowID,
            title: target.windowTitle, width: target.windowWidth, height: target.windowHeight))
    }

    static func focusedFieldStillMatches(_ proposal: ComputerActionProposal) -> Bool {
        guard let original = proposal.focusedField,
              let current = captureFocusedField(target: proposal) else { return false }
        return original.isSameAs(current)
    }

    private static func matches(_ window: WindowInfo, proposal: ComputerActionProposal) -> Bool {
        window.title == proposal.windowTitle && abs(window.frame.width - proposal.windowWidth) <= 1 &&
            abs(window.frame.height - proposal.windowHeight) <= 1
    }

    private static func windowInfo(id: UInt32, pid: Int32) -> WindowInfo? {
        let rows = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID)
            as? [[String: Any]] ?? []
        for row in rows {
            guard (row[kCGWindowNumber as String] as? NSNumber)?.uint32Value == id,
                  (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
                  let frame = rowFrame(row) else { continue }
            return WindowInfo(frame: frame, title: row[kCGWindowName as String] as? String ?? "")
        }
        return nil
    }

    private static func isTopmost(windowID: UInt32, at point: CGPoint) -> Bool {
        let rows = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID)
            as? [[String: Any]] ?? []
        for row in rows {
            guard let frame = rowFrame(row), frame.contains(point) else {
                continue
            }
            if let alpha = row[kCGWindowAlpha as String] as? NSNumber, alpha.doubleValue <= 0.01 {
                continue
            }
            return (row[kCGWindowNumber as String] as? NSNumber)?.uint32Value == windowID
        }
        return false
    }

    private static func raiseExactWindowIfNeeded(_ proposal: ComputerActionProposal,
                                                  window: WindowInfo) -> Bool {
        guard AXIsProcessTrusted() else { return false }
        let center = CGPoint(x: window.frame.midX, y: window.frame.midY)
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == proposal.processID,
           isTopmost(windowID: proposal.windowID, at: center) { return true }

        let app = AXUIElementCreateApplication(proposal.processID)
        guard let value = attribute(app, kAXWindowsAttribute),
              let windows = value as? [AXUIElement] else { return false }
        let matching = windows.filter { element in
            attributeString(element, kAXTitleAttribute) == proposal.windowTitle &&
                accessibilityFrame(element).map { framesMatch($0, window.frame) } == true
        }
        guard matching.count == 1, let targetWindow = matching.first,
              AXUIElementPerformAction(targetWindow, kAXRaiseAction as CFString) == .success else {
            return false
        }
        guard let application = NSRunningApplication(processIdentifier: proposal.processID),
              application.bundleIdentifier == proposal.bundleID else { return false }
        // Activation is asynchronous. The shell waits briefly and rechecks its
        // cancellation ticket before perform() verifies the exact focused target.
        return application.activate(options: [])
    }

    private static func rowFrame(_ row: [String: Any]) -> CGRect? {
        guard let bounds = row[kCGWindowBounds as String] as? [String: Any],
              let x = number(bounds["X"]), let y = number(bounds["Y"]),
              let width = number(bounds["Width"]), let height = number(bounds["Height"]),
              width > 0, height > 0 else { return nil }
        return CGRect(x: x, y: y, width: width, height: height)
    }

    private static func number(_ value: Any?) -> CGFloat? {
        guard let value = value as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(),
              value.doubleValue.isFinite else { return nil }
        return CGFloat(value.doubleValue)
    }

    private static func processID(of element: AXUIElement) -> pid_t? {
        var pid: pid_t = 0
        return AXUIElementGetPid(element, &pid) == .success ? pid : nil
    }

    private static func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else {
            return nil
        }
        return value
    }

    private static func attributeElement(_ element: AXUIElement, _ name: String) -> AXUIElement? {
        guard let value = attribute(element, name), CFGetTypeID(value) == AXUIElementGetTypeID() else {
            return nil
        }
        return unsafeBitCast(value, to: AXUIElement.self)
    }

    private static func attributeString(_ element: AXUIElement, _ name: String) -> String? {
        attribute(element, name) as? String
    }

    private static func framesMatch(_ lhs: CGRect, _ rhs: CGRect) -> Bool {
        abs(lhs.minX - rhs.minX) <= 2 && abs(lhs.minY - rhs.minY) <= 2 &&
            abs(lhs.width - rhs.width) <= 2 && abs(lhs.height - rhs.height) <= 2
    }

    private static func accessibilityFrame(_ element: AXUIElement) -> CGRect? {
        var positionValue: CFTypeRef?
        var sizeValue: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXPositionAttribute as CFString,
                                             &positionValue) == .success,
              AXUIElementCopyAttributeValue(element, kAXSizeAttribute as CFString,
                                             &sizeValue) == .success,
              let positionValue, let sizeValue,
              CFGetTypeID(positionValue) == AXValueGetTypeID(),
              CFGetTypeID(sizeValue) == AXValueGetTypeID() else { return nil }
        var point = CGPoint.zero
        var size = CGSize.zero
        guard AXValueGetValue(unsafeBitCast(positionValue, to: AXValue.self), .cgPoint, &point),
              AXValueGetValue(unsafeBitCast(sizeValue, to: AXValue.self), .cgSize, &size) else { return nil }
        return CGRect(origin: point, size: size)
    }

    private static func postClick(at point: CGPoint) -> Bool {
        guard let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown,
                                 mouseCursorPosition: point, mouseButton: .left),
              let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp,
                               mouseCursorPosition: point, mouseButton: .left) else { return false }
        down.post(tap: .cghidEventTap)
        up.post(tap: .cghidEventTap)
        return true
    }

}

@MainActor
final class ComputerActionApprovalCoordinator: NSObject, NSWindowDelegate {
    private(set) var pending: ComputerActionProposal?
    private var approved: [String: ComputerActionProposal] = [:]
    private var window: NSPanel?
    var onDecision: ((ComputerActionProposal, String) -> Void)?

    func present(_ proposal: ComputerActionProposal) {
        if let pending { deny(pending) }
        self.pending = proposal
        let frame = NSRect(x: 0, y: 0, width: 430, height: 360)
        let panel = NSPanel(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.isFloatingPanel = true
        panel.level = .modalPanel
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isReleasedWhenClosed = false
        panel.contentView = NSHostingView(rootView: ComputerActionConfirmationView(
            proposal: proposal,
            onAllow: { [weak self] in self?.allow(proposal) },
            onDeny: { [weak self] in self?.deny(proposal) },
            onOpenSettings: { Self.openAccessibilitySettings() }))
        panel.delegate = self
        panel.center()
        window = panel
        panel.orderFrontRegardless()
    }

    func cancelPending() {
        if let proposal = pending { deny(proposal) }
        approved.removeAll()
    }

    func clear() {
        dismissPanel()
        pending = nil
        approved.removeAll()
    }

    func cancelFromUser() {
        cancelPending()
    }

    func takeApproved(for event: [String: Any]) -> ComputerActionProposal? {
        guard let proposalID = event["proposal_id"] as? String,
              let proposal = approved.removeValue(forKey: proposalID),
              proposal.matchesExecution(event),
              ComputerActionExecution.targetStillMatches(proposal),
              !proposal.requiresAccessibility || ComputerActionExecution.accessibilityTrusted,
              proposal.kind != .typeText || ComputerActionExecution.focusedFieldStillMatches(proposal)
        else { return nil }
        return proposal
    }

    func handleCancellation(_ event: [String: Any]) {
        guard let proposalID = event["proposal_id"] as? String,
              let operationID = event["operation_id"] as? String,
              let runID = event["run_id"] as? String else { return }
        if pending?.proposalID == proposalID, pending?.operationID == operationID,
           pending?.runID == runID {
            pending = nil
            dismissPanel()
        }
        if let proposal = approved[proposalID], proposal.operationID == operationID,
           proposal.runID == runID { approved.removeValue(forKey: proposalID) }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        cancelPending()
        dismissPanel()
        return true
    }

    private func allow(_ proposal: ComputerActionProposal) {
        guard pending?.proposalID == proposal.proposalID,
              pending?.operationID == proposal.operationID, pending?.runID == proposal.runID,
              proposal.expiresAt > Date(), ComputerActionExecution.targetStillMatches(proposal),
              proposal.kind != .typeText || ComputerActionExecution.focusedFieldStillMatches(proposal)
        else {
            deny(proposal)
            return
        }
        if proposal.requiresAccessibility && !ComputerActionExecution.accessibilityTrusted { return }
        pending = nil
        approved[proposal.proposalID] = proposal
        dismissPanel()
        onDecision?(proposal, "approve")
    }

    private func deny(_ proposal: ComputerActionProposal) {
        guard pending?.proposalID == proposal.proposalID else { return }
        pending = nil
        dismissPanel()
        onDecision?(proposal, "deny")
    }

    private func dismissPanel() {
        window?.delegate = nil
        window?.orderOut(nil)
        window = nil
    }

    private static func openAccessibilitySettings() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") else {
            return
        }
        NSWorkspace.shared.open(url)
    }
}

private struct ComputerActionConfirmationView: View {
    let proposal: ComputerActionProposal
    let onAllow: () -> Void
    let onDeny: () -> Void
    let onOpenSettings: () -> Void
    @State private var permissionTrusted = ComputerActionExecution.accessibilityTrusted

    private var canAllow: Bool {
        !proposal.requiresAccessibility ||
            (permissionTrusted && (proposal.kind != .typeText || proposal.focusedField != nil))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "hand.raised.fill")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(Color(nsColor: .controlAccentColor))
                VStack(alignment: .leading, spacing: 3) {
                    Text("Aloy needs your approval")
                        .font(.system(size: 17, weight: .semibold))
                    Text(proposal.kind == .hideApp
                         ? "This one-time action runs only after you allow it."
                         : "Allow once focuses only this exact window, then runs this action.")
                        .font(.system(size: 12)).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(.bottom, 18)

            Text(proposal.kind.title)
                .font(.system(size: 13, weight: .semibold))
            Text("\(proposal.appName) · \(proposal.bundleID)")
                .font(.system(size: 12)).foregroundStyle(.secondary)
                .padding(.top, 5)
            if proposal.kind != .hideApp {
                Text("Window · \(proposal.windowTitle.isEmpty ? "Untitled window" : proposal.windowTitle)")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                    .padding(.top, 3)
            }
            actionDetails
                .padding(.top, 12)

            if proposal.requiresAccessibility && !permissionTrusted {
                VStack(alignment: .leading, spacing: 8) {
                    Text(proposal.kind == .typeText && proposal.focusedField == nil
                         ? "Aloy could not verify an editable field. Enable Accessibility, focus the intended field, then request this action again."
                         : "Accessibility access is required. Aloy will wait until you enable it.")
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                    HStack {
                        Button("Open Accessibility Settings", action: onOpenSettings)
                        Button("Check access") { permissionTrusted = ComputerActionExecution.accessibilityTrusted }
                    }
                    .controlSize(.small)
                }
                .padding(11)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .controlBackgroundColor),
                            in: RoundedRectangle(cornerRadius: 10))
                .padding(.top, 14)
            }

            Spacer(minLength: 15)
            HStack {
                Button("Not now", action: onDeny)
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Allow once", action: onAllow)
                    .buttonStyle(.borderedProminent)
                    .disabled(!canAllow)
            }
        }
        .padding(22)
        .frame(width: 430, height: 360)
        .background(Color(nsColor: .windowBackgroundColor),
                    in: RoundedRectangle(cornerRadius: 17, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 17).stroke(Color(nsColor: .separatorColor), lineWidth: 0.5))
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            permissionTrusted = ComputerActionExecution.accessibilityTrusted
        }
    }

    @ViewBuilder private var actionDetails: some View {
        switch proposal.kind {
        case .click:
            let x = (proposal.normalizedX ?? 0) * proposal.windowWidth
            let y = (proposal.normalizedY ?? 0) * proposal.windowHeight
            Text(String(format: "Bring this exact window forward, then click at %.0f%% across, %.0f%% down (%.0f × %.0f points).",
                        (proposal.normalizedX ?? 0) * 100, (proposal.normalizedY ?? 0) * 100,
                        proposal.windowWidth, proposal.windowHeight))
                .font(.system(size: 12))
            Text(String(format: "Window-local point: %.0f, %.0f", x, y))
                .font(.system(size: 11, design: .monospaced)).foregroundStyle(.secondary)
                .padding(.top, 4)
        case .typeText:
            Text("Bring this exact window forward and insert exactly this text into the same focused field:")
                .font(.system(size: 12))
            ScrollView {
                Text(proposal.text ?? "")
                    .font(.system(size: 12, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(9)
            }
            .frame(maxHeight: 94)
            .background(Color(nsColor: .controlBackgroundColor),
                        in: RoundedRectangle(cornerRadius: 8))
            .padding(.top, 5)
        case .hideApp:
            Text("Aloy needs a screen capture of this app from the current run to verify the target. Approval is separate: this hides only this app. Bring it back from the Dock or app switcher.")
                .font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
        }
    }
}
