import AppKit
import SwiftUI

/// Explicit click-through for controls in the non-activating companion panel.
final class BubbleControlButton: NSButton {
    var onPress: (() -> Void)?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override var mouseDownCanMoveWindow: Bool { false }

    init(symbol: String, label: String, action: @escaping () -> Void) {
        super.init(frame: NSRect(x: 0, y: 0, width: 36, height: 32))
        image = NSImage(systemSymbolName: symbol, accessibilityDescription: label)
        imagePosition = .imageOnly
        isBordered = false
        setButtonType(.momentaryChange)
        toolTip = label
        setAccessibilityLabel(label)
        onPress = action
        target = self
        self.action = #selector(pressed)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }
    @objc private func pressed() { onPress?() }
}

private struct BubbleControl: NSViewRepresentable {
    let symbol: String
    let label: String
    let action: () -> Void
    func makeNSView(context: Context) -> BubbleControlButton {
        BubbleControlButton(symbol: symbol, label: label, action: action)
    }
    func updateNSView(_ view: BubbleControlButton, context: Context) {
        view.onPress = action
    }
}

struct CompanionBubbleView: View {
    @ObservedObject var model: PlaygroundModel
    let onExpand: () -> Void
    let onDismiss: () -> Void

    private var latestUserIndex: Int? {
        model.messages.lastIndex(where: { $0.role == "user" })
    }

    private var latestQuestion: String? {
        latestUserIndex.map { model.messages[$0].text }
    }

    private var latestAnswer: String? {
        guard let index = latestUserIndex, index + 1 < model.messages.endIndex else { return nil }
        return model.messages[(index + 1)...].last(where: { $0.role == "assistant" })?.text
    }

    private var latestArtifact: CanvasArtifact? {
        model.currentArtifact ?? model.artifacts.last
    }

    private var answerText: String {
        if model.companionStatus == .error {
            return model.status.isEmpty ? "Aloy needs your attention." : model.status
        }
        if let latestAnswer, !latestAnswer.isEmpty { return latestAnswer }
        if model.companionStatus == .thinking { return "Thinking…" }
        if model.companionStatus == .recording || model.companionStatus == .listening {
            return "Listening…"
        }
        if model.companionStatus == .speaking { return "Speaking…" }
        return "Use Option–Z to talk"
    }

    private var statusLabel: String {
        switch model.companionStatus {
        case .ready: "Ready"
        case .recording: "Recording"
        case .listening: "Listening"
        case .thinking: "Thinking"
        case .speaking: "Speaking"
        case .error: "Needs you"
        }
    }

    private var statusColor: Color {
        switch model.companionStatus {
        case .ready: Color(nsColor: .secondaryLabelColor)
        case .recording: Color(nsColor: .systemRed)
        case .listening: Color(nsColor: .systemRed)
        case .thinking: Color(nsColor: .systemTeal)
        case .speaking: Color(nsColor: .systemGreen)
        case .error: Color(nsColor: .systemOrange)
        }
    }

    private var isActive: Bool {
        model.companionStatus == .recording || model.companionStatus == .listening || model.companionStatus == .thinking ||
            model.companionStatus == .speaking
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text("Aloy")
                    .font(.system(size: 14, weight: .semibold, design: .rounded))
                Circle().fill(statusColor).frame(width: 6, height: 6)
                Text(statusLabel)
                    .font(.system(size: 11, weight: .regular))
                    .foregroundStyle(.secondary)
                Spacer(minLength: 8)
                BubbleControl(symbol: "arrow.up.left.and.arrow.down.right",
                              label: "Open full conversation", action: onExpand)
                    .frame(width: 36, height: 32)
                BubbleControl(symbol: "minus", label: "Dismiss conversation bubble",
                              action: onDismiss)
                    .frame(width: 36, height: 32)
            }

            if let latestQuestion, !latestQuestion.isEmpty {
                Text("You · \(latestQuestion)")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 17)
            } else {
                Text("Ready when you are.")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .padding(.top, 17)
            }

            Text(answerText)
                .font(.system(size: 16, weight: .regular))
                .foregroundStyle(model.companionStatus == .error
                    ? Color(nsColor: .systemOrange) : Color(nsColor: .labelColor))
                .lineSpacing(3)
                .lineLimit(7)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 9)
                .padding(.bottom, 14)

            Rectangle()
                .fill(Color(nsColor: .separatorColor).opacity(0.6))
                .frame(height: 1)

            HStack {
                if let artifact = latestArtifact {
                    Button {
                        model.openCanvas(artifact.id)
                    } label: {
                        Label("Show me", systemImage: "rectangle.on.rectangle")
                            .font(.system(size: 12, weight: .medium))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(AppPalette.sage)
                    .help("Open \(artifact.title) on the desktop")
                } else {
                    Text("Option–Z to talk · Option–X to stop")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                if isActive {
                    Button(action: { model.onStop?() }) {
                        Image(systemName: "stop.fill")
                            .font(.system(size: 11, weight: .medium))
                    }
                    .buttonStyle(.plain)
                    .help("Stop current response")
                    .accessibilityLabel("Stop current response")
                }
            }
            .padding(.top, 9)
        }
        .padding(.horizontal, 19)
        .padding(.top, 16)
        .padding(.bottom, 13)
        .frame(width: 360, alignment: .leading)
        .background(Color(nsColor: .windowBackgroundColor).opacity(0.98),
                    in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .shadow(color: .black.opacity(0.17), radius: 22, x: 0, y: 9)
        .fixedSize(horizontal: false, vertical: true)
    }
}
