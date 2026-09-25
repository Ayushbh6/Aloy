import AppKit
import CoreFoundation
import SwiftUI

private let DRAWING_COLORS: Set<String> = ["#3A86FF", "#FF006E", "#8338EC", "#FB5607", "#06A77D", "#222222"]

struct CanvasArtifact: Decodable, Identifiable {
    let schemaVersion: Int
    let id: String
    let conversationID: String
    let runID: String
    let sequence: Int
    let createdAt: String?
    let title: String
    let blocks: [CanvasBlock]

    enum CodingKeys: String, CodingKey {
        case schemaVersion
        case id = "artifactId"
        case conversationID = "conversationId"
        case runID = "runId"
        case sequence
        case createdAt
        case title
        case blocks
    }

    static func decode(_ object: Any) -> CanvasArtifact? {
        guard validateEnvelope(object), JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else { return nil }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let artifact = try? decoder.decode(CanvasArtifact.self, from: data), artifact.isWithinBounds else { return nil }
        return artifact
    }

    private static func validateEnvelope(_ raw: Any) -> Bool {
        guard let envelope = raw as? [String: Any],
              object(envelope, required: ["schema_version", "artifact_id", "conversation_id", "run_id", "sequence", "title", "blocks"],
                     optional: ["created_at"]),
              integer(envelope["schema_version"]) == 1,
              let id = envelope["artifact_id"] as? String, isID(id),
              let conversation = envelope["conversation_id"] as? String, isID(conversation),
              let run = envelope["run_id"] as? String, isID(run),
              let sequence = integer(envelope["sequence"]), sequence >= 0,
              !envelope.keys.contains("created_at") || envelope["created_at"] is String,
              let title = envelope["title"] as? String, text(title, limit: 120),
              let blocks = envelope["blocks"] as? [[String: Any]], (1...24).contains(blocks.count) else { return false }

        var blockIDs = Set<String>()
        for block in blocks {
            guard let kind = block["kind"] as? String, let blockID = block["id"] as? String,
                  isID(blockID), blockIDs.insert(blockID).inserted else { return false }
            let required: Set<String>
            let optional: Set<String>
            switch kind {
            case "heading", "text": required = ["id", "kind", "text"]; optional = []
            case "sentence": required = ["id", "kind", "source", "target"]; optional = ["explanation"]
            case "choice": required = ["id", "kind", "prompt", "options"]; optional = ["correct_option_id", "explanation"]
            case "table": required = ["id", "kind", "columns", "rows"]; optional = []
            case "chart": required = ["id", "kind", "chart_kind", "title", "labels", "series"]; optional = []
            case "drawing": required = ["id", "kind", "strokes"]; optional = []
            default: return false
            }
            guard object(block, required: required, optional: optional) else { return false }
            switch kind {
            case "heading", "text":
                guard let value = block["text"] as? String, text(value, limit: 4_000) else { return false }
            case "sentence":
                guard let source = block["source"] as? String, text(source, limit: 1_200),
                      let target = block["target"] as? String, text(target, limit: 1_200),
                      optionalText(block["explanation"], limit: 1_000) else { return false }
            case "choice":
                guard let prompt = block["prompt"] as? String, text(prompt, limit: 1_000),
                      let options = block["options"] as? [[String: Any]], (2...8).contains(options.count) else { return false }
                var optionIDs = Set<String>()
                for option in options {
                    guard object(option, required: ["id", "label"]),
                          let optionID = option["id"] as? String, isID(optionID), optionIDs.insert(optionID).inserted,
                          let label = option["label"] as? String, text(label, limit: 500) else { return false }
                }
                if let correct = block["correct_option_id"] {
                    guard let value = correct as? String, optionIDs.contains(value) else { return false }
                }
                guard optionalText(block["explanation"], limit: 1_000) else { return false }
            case "table":
                guard let columns = block["columns"] as? [String], (1...8).contains(columns.count),
                      columns.allSatisfy({ text($0, limit: 200) }),
                      let rows = block["rows"] as? [[Any]], rows.count <= 30,
                      rows.allSatisfy({ $0.count == columns.count && $0.allSatisfy { ($0 as? String).map { text($0, limit: 500, allowEmpty: true) } == true } }) else { return false }
            case "chart":
                guard let chartKind = block["chart_kind"] as? String, ["bar", "line"].contains(chartKind),
                      let chartTitle = block["title"] as? String, text(chartTitle, limit: 120),
                      let labels = block["labels"] as? [String], (1...24).contains(labels.count),
                      labels.allSatisfy({ text($0, limit: 120) }),
                      let series = block["series"] as? [[String: Any]], (1...5).contains(series.count) else { return false }
                for line in series {
                    guard object(line, required: ["name", "values"]),
                          let name = line["name"] as? String, text(name, limit: 120),
                          let values = line["values"] as? [Any], values.count == labels.count,
                          values.allSatisfy({ finite($0, minimum: -1_000_000, maximum: 1_000_000) }) else { return false }
                }
            case "drawing":
                guard let strokes = block["strokes"] as? [[String: Any]], strokes.count <= 12 else { return false }
                var totalPoints = 0
                for stroke in strokes {
                    guard object(stroke, required: ["color", "width", "points"]),
                          let color = stroke["color"] as? String, DRAWING_COLORS.contains(color),
                          finite(stroke["width"], minimum: 0.5, maximum: 12),
                          let points = stroke["points"] as? [[String: Any]], (2...200).contains(points.count) else { return false }
                    totalPoints += points.count
                    guard totalPoints <= 500 else { return false }
                    for point in points {
                        guard object(point, required: ["x", "y"]),
                              finite(point["x"], minimum: 0, maximum: 1),
                              finite(point["y"], minimum: 0, maximum: 1) else { return false }
                    }
                }
            default: return false
            }
        }
        let payload: [String: Any] = ["title": title, "blocks": blocks]
        guard let bytes = try? JSONSerialization.data(withJSONObject: payload),
              bytes.count + separatorCount(payload) <= 20_000 else { return false }
        return true
    }

    private static func object(_ value: [String: Any], required: Set<String>, optional: Set<String> = []) -> Bool {
        required.isSubset(of: Set(value.keys)) && Set(value.keys).isSubset(of: required.union(optional))
    }

    fileprivate static func isID(_ value: String) -> Bool {
        (1...64).contains(value.unicodeScalars.count) && value.unicodeScalars.allSatisfy {
            (48...57).contains($0.value) || (65...90).contains($0.value) || (97...122).contains($0.value) || $0 == "_" || $0 == "-"
        }
    }

    fileprivate static func text(_ value: String, limit: Int, allowEmpty: Bool = false) -> Bool {
        (allowEmpty || !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) &&
            value.unicodeScalars.count <= limit && value.unicodeScalars.allSatisfy {
                $0.value >= 32 || $0 == "\n" || $0 == "\t"
            }
    }

    private static func optionalText(_ value: Any?, limit: Int) -> Bool {
        guard let value else { return true }
        guard let string = value as? String else { return false }
        return text(string, limit: limit)
    }

    private static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite, number.doubleValue.rounded(.towardZero) == number.doubleValue else { return nil }
        return number.intValue
    }

    private static func finite(_ value: Any?, minimum: Double, maximum: Double) -> Bool {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return false }
        return number.doubleValue.isFinite && number.doubleValue >= minimum && number.doubleValue <= maximum
    }

    private static func separatorCount(_ value: Any) -> Int {
        if let dictionary = value as? [String: Any] {
            return dictionary.count + max(0, dictionary.count - 1) +
                dictionary.values.reduce(0) { $0 + separatorCount($1) }
        }
        if let array = value as? [Any] {
            return max(0, array.count - 1) + array.reduce(0) { $0 + separatorCount($1) }
        }
        return 0
    }

    var isWithinBounds: Bool {
        schemaVersion == 1 && Self.isID(id) && Self.isID(conversationID) && Self.isID(runID) && sequence >= 0 &&
            Self.text(title, limit: 120) && (1...24).contains(blocks.count) &&
            blocks.allSatisfy(\.isWithinBounds)
    }
}

struct CanvasBlock: Decodable, Identifiable {
    let id: String
    let content: Content

    enum Content: Decodable {
        case heading(String)
        case text(String)
        case sentence(source: String, target: String, explanation: String?)
        case choice(prompt: String, options: [CanvasChoice], correctOptionID: String?, explanation: String?)
        case table(columns: [String], rows: [[String]])
        case chart(kind: String, title: String, labels: [String], series: [CanvasChartSeries])
        case drawing(strokes: [CanvasStroke])

        private enum Keys: String, CodingKey {
            case kind, text, source, target, explanation, prompt, options
            case correctOptionID = "correctOptionId"
            case columns, rows, chartKind, title, labels, series, strokes
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: Keys.self)
            switch try container.decode(String.self, forKey: .kind) {
            case "heading": self = .heading(try container.decode(String.self, forKey: .text))
            case "text": self = .text(try container.decode(String.self, forKey: .text))
            case "sentence":
                self = .sentence(
                    source: try container.decode(String.self, forKey: .source),
                    target: try container.decode(String.self, forKey: .target),
                    explanation: try container.decodeIfPresent(String.self, forKey: .explanation))
            case "choice":
                self = .choice(
                    prompt: try container.decode(String.self, forKey: .prompt),
                    options: try container.decode([CanvasChoice].self, forKey: .options),
                    correctOptionID: try container.decodeIfPresent(String.self, forKey: .correctOptionID),
                    explanation: try container.decodeIfPresent(String.self, forKey: .explanation))
            case "table":
                self = .table(
                    columns: try container.decode([String].self, forKey: .columns),
                    rows: try container.decode([[String]].self, forKey: .rows))
            case "chart":
                self = .chart(
                    kind: try container.decode(String.self, forKey: .chartKind),
                    title: try container.decode(String.self, forKey: .title),
                    labels: try container.decode([String].self, forKey: .labels),
                    series: try container.decode([CanvasChartSeries].self, forKey: .series))
            case "drawing": self = .drawing(strokes: try container.decode([CanvasStroke].self, forKey: .strokes))
            default: throw DecodingError.dataCorruptedError(
                forKey: .kind, in: container, debugDescription: "Unsupported canvas block")
            }
        }
    }

    private enum Keys: String, CodingKey { case id, kind }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: Keys.self)
        id = try container.decode(String.self, forKey: .id)
        content = try CanvasBlock.Content(from: decoder)
    }

    var kind: String {
        switch content {
        case .heading: "heading"
        case .text: "text"
        case .sentence: "sentence"
        case .choice: "choice"
        case .table: "table"
        case .chart: "chart"
        case .drawing: "drawing"
        }
    }

    var isWithinBounds: Bool {
        guard CanvasArtifact.isID(id) else { return false }
        switch content {
        case .heading(let text), .text(let text): return CanvasArtifact.text(text, limit: 4_000)
        case .sentence(let source, let target, let explanation):
            return CanvasArtifact.text(source, limit: 1_200) && CanvasArtifact.text(target, limit: 1_200) &&
                (explanation.map { CanvasArtifact.text($0, limit: 1_000) } ?? true)
        case .choice(let prompt, let options, let correct, let explanation):
            let ids = options.map(\.id)
            return CanvasArtifact.text(prompt, limit: 1_000) && (2...8).contains(options.count) &&
                Set(ids).count == ids.count && options.allSatisfy {
                    CanvasArtifact.isID($0.id) && CanvasArtifact.text($0.label, limit: 500)
                } &&
                (correct == nil || options.contains { $0.id == correct }) &&
                (explanation.map { CanvasArtifact.text($0, limit: 1_000) } ?? true)
        case .table(let columns, let rows):
            return (1...8).contains(columns.count) && columns.allSatisfy { CanvasArtifact.text($0, limit: 200) } &&
                rows.count <= 30 && rows.allSatisfy {
                    $0.count == columns.count && $0.allSatisfy { CanvasArtifact.text($0, limit: 500, allowEmpty: true) }
                }
        case .chart(let kind, let title, let labels, let series):
            return ["bar", "line"].contains(kind) && CanvasArtifact.text(title, limit: 120) && (1...24).contains(labels.count) &&
                labels.allSatisfy { CanvasArtifact.text($0, limit: 120) } && (1...5).contains(series.count) &&
                series.allSatisfy { CanvasArtifact.text($0.name, limit: 120) && $0.values.count == labels.count &&
                    $0.values.allSatisfy { $0.isFinite && abs($0) <= 1_000_000 } }
        case .drawing(let strokes):
            return strokes.count <= 12 && strokes.reduce(0) { $0 + $1.points.count } <= 500 && strokes.allSatisfy { stroke in
                (2...200).contains(stroke.points.count) && stroke.width.isFinite &&
                    (0.5...12).contains(stroke.width) && DRAWING_COLORS.contains(stroke.color) &&
                    stroke.points.allSatisfy { $0.x.isFinite && $0.y.isFinite &&
                        (0...1).contains($0.x) && (0...1).contains($0.y) }
            }
        }
    }
}

struct CanvasChoice: Decodable, Identifiable {
    let id: String
    let label: String
}

struct CanvasChartSeries: Decodable {
    let name: String
    let values: [Double]
}

struct CanvasStroke: Decodable {
    let color: String
    let width: Double
    let points: [CanvasPoint]
}

struct CanvasPoint: Decodable {
    let x: Double
    let y: Double
}

@MainActor
final class CanvasCoordinator: NSObject, NSWindowDelegate {
    private let model: PlaygroundModel
    private var panel: NSPanel?
    private var drawingPanel: NSPanel?
    private var displayedArtifactID: String?

    init(model: PlaygroundModel) {
        self.model = model
        super.init()
        model.onCanvasPresentationChanged = { [weak self] artifact, presented in
            guard let self else { return }
            if presented, let artifact { self.show(artifact: artifact) }
            else { self.dismissWindow() }
        }
        model.onCanvasPinChanged = { [weak self] id, isPinned in
            guard let self, !isPinned, self.displayedArtifactID == id,
                  let latest = model.currentArtifact, latest.id != id, model.canvasPresented else { return }
            self.show(artifact: latest)
        }
        if model.canvasPresented, let artifact = model.currentArtifact { show(artifact: artifact) }
    }

    func show(artifact: CanvasArtifact) {
        guard artifact.isWithinBounds else { return }
        if let displayedArtifactID, displayedArtifactID != artifact.id,
           model.pinnedCanvasIDs.contains(displayedArtifactID) { return }
        displayedArtifactID = artifact.id
        let screen = NSScreen.main ?? NSScreen.screens.first
        guard let screen else { return }
        if panel == nil {
            let window = NSPanel(contentRect: NSRect(origin: .zero, size: screen.visibleFrame.size),
                                 styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            configure(window, ignoresMouse: false)
            panel = window
        }
        if drawingPanel == nil {
            let window = NSPanel(contentRect: screen.frame,
                                 styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            configure(window, ignoresMouse: true)
            drawingPanel = window
        }
        guard let panel, let drawingPanel else { return }
        panel.contentView = NSHostingView(rootView: CanvasPanelView(
            model: model, artifact: artifact))
        let frame = screen.visibleFrame
        let width = min(frame.width - 48, min(820, max(540, frame.width * 0.66)))
        let height = min(frame.height - 72, 740)
        panel.setFrame(NSRect(x: frame.midX - width / 2, y: frame.midY - height / 2,
                              width: width, height: height), display: false)
        drawingPanel.setFrame(screen.frame, display: false)
        let drawingStrokes = artifact.blocks.flatMap { block -> [CanvasStroke] in
            if case .drawing(let strokes) = block.content { return strokes }
            return []
        }
        // A click-through focus veil leaves the underlying app open and visible.
        // The explanation itself has no card, title bar or conventional window chrome.
        drawingPanel.contentView = NSHostingView(rootView: CanvasDrawingOverlay(strokes: drawingStrokes)
            .background(CanvasPalette.warm.opacity(0.94)))
        drawingPanel.orderFrontRegardless()
        panel.level = .floating
        panel.orderFrontRegardless()
    }

    func dismiss() {
        model.dismissCanvas()
    }

    func hideTemporarily() {
        panel?.orderOut(nil)
        drawingPanel?.orderOut(nil)
    }

    func restoreIfPresented() {
        guard model.canvasPresented, let artifact = model.currentArtifact else { return }
        show(artifact: artifact)
    }

    private func dismissWindow() {
        displayedArtifactID = nil
        panel?.orderOut(nil)
        drawingPanel?.orderOut(nil)
    }

    private func configure(_ window: NSPanel, ignoresMouse: Bool) {
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.isMovableByWindowBackground = false
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        window.level = .floating
        window.ignoresMouseEvents = ignoresMouse
        window.isReleasedWhenClosed = false
    }
}

struct CanvasPanelView: View {
    @ObservedObject var model: PlaygroundModel
    let artifact: CanvasArtifact

    init(model: PlaygroundModel, artifact: CanvasArtifact) {
        self.model = model
        self.artifact = artifact
    }

    init(model: PlaygroundModel, artifact: CanvasArtifact, isPinned: Bool) {
        self.init(model: model, artifact: artifact)
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Circle().fill(CanvasPalette.sage).frame(width: 7, height: 7)
                Text("ALOY  /  CANVAS").font(.system(size: 10, weight: .semibold, design: .rounded))
                    .tracking(1.15).foregroundStyle(CanvasPalette.secondary)
                Spacer()
                Button {
                    model.pinCanvas(artifact.id, pinned: !isPinned)
                } label: {
                    Image(systemName: isPinned ? "pin.fill" : "pin")
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.plain).foregroundStyle(isPinned ? CanvasPalette.sage : CanvasPalette.secondary)
                .help(isPinned ? "Unpin canvas" : "Pin this canvas")
                Button { model.dismissCanvas() } label: {
                    Image(systemName: "xmark").font(.system(size: 12, weight: .semibold))
                        .frame(width: 27, height: 27)
                        .background(CanvasPalette.surface.opacity(0.84), in: Circle())
                }
                .buttonStyle(.plain).help("Dismiss canvas")
            }
            .padding(.horizontal, 19).padding(.top, 15).padding(.bottom, 8)
            Rectangle().fill(CanvasPalette.line.opacity(0.8)).frame(height: 1)
            ScrollView {
                CanvasArtifactContent(model: model, artifact: artifact)
            }
            .scrollIndicators(.hidden)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .padding(18)
        .background(Color.clear)
    }

    private var isPinned: Bool {
        model.pinnedCanvasIDs.contains(artifact.id)
    }
}

struct CanvasArtifactContent: View {
    @ObservedObject var model: PlaygroundModel
    let artifact: CanvasArtifact

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(artifact.title)
                .font(.system(size: 27, weight: .medium, design: .rounded))
                .tracking(-0.8).foregroundStyle(CanvasPalette.ink)
            ForEach(artifact.blocks) { block in
                CanvasBlockView(block: block,
                    canInteract: { interaction, value in
                        model.canInteractWithCanvas(artifact: artifact, block: block,
                                                    interaction: interaction, value: value)
                    }) { interaction, value in
                        model.sendCanvasInteraction(artifact: artifact, block: block,
                                                    interaction: interaction, value: value)
                    }
            }
        }
        .frame(maxWidth: 700, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 20).padding(.vertical, 19)
    }
}

private struct CanvasBlockView: View {
    let block: CanvasBlock
    let canInteract: (String, String) -> Bool
    let interact: (String, String) -> Void
    @State private var showingTarget = false

    var body: some View {
        Group {
            switch block.content {
            case .heading(let text):
                Text(text).font(.system(size: 20, weight: .medium, design: .rounded))
                    .foregroundStyle(CanvasPalette.ink).padding(.top, 3)
            case .text(let text):
                Text(text).font(.system(size: 15)).lineSpacing(5)
                    .foregroundStyle(CanvasPalette.ink.opacity(0.88)).fixedSize(horizontal: false, vertical: true)
            case .sentence(let source, let target, let explanation):
                VStack(alignment: .leading, spacing: 13) {
                    HStack {
                        Text(showingTarget ? "TRY THIS" : "BEFORE")
                            .font(.system(size: 10, weight: .semibold)).tracking(1.1)
                            .foregroundStyle(CanvasPalette.secondary)
                        Spacer()
                        Button {
                            withAnimation(.easeInOut(duration: 0.24)) { showingTarget.toggle() }
                        } label: {
                            Label(showingTarget ? "Show original" : "See transformation",
                                  systemImage: "arrow.left.arrow.right")
                                .font(.system(size: 11, weight: .medium))
                        }
                        .buttonStyle(.plain).foregroundStyle(CanvasPalette.sage)
                    }
                    Text(showingTarget ? target : source)
                        .font(.system(size: 22, weight: .medium, design: .serif))
                        .foregroundStyle(CanvasPalette.ink)
                        .fixedSize(horizontal: false, vertical: true)
                        .id(showingTarget)
                        .transition(.opacity.combined(with: .offset(y: 4)))
                        .accessibilityLabel(showingTarget ? "Transformed sentence" : "Original sentence")
                    if let explanation, !explanation.isEmpty {
                        Text(explanation).font(.system(size: 13)).foregroundStyle(CanvasPalette.secondary)
                    }
                    Button {
                        interact("transform", target)
                    } label: {
                        Label("Ask Aloy to try this", systemImage: "arrow.uturn.forward")
                    }
                    .buttonStyle(.bordered).tint(CanvasPalette.sage)
                    .disabled(!canInteract("transform", target))
                }
                .animation(.easeInOut(duration: 0.24), value: showingTarget)
            case .choice(let prompt, let options, _, let explanation):
                VStack(alignment: .leading, spacing: 12) {
                    Text(prompt).font(.system(size: 15, weight: .medium)).foregroundStyle(CanvasPalette.ink)
                    ForEach(options) { option in
                        Button { interact("choose", option.id) } label: {
                            HStack(spacing: 11) {
                                Text(option.label).foregroundStyle(CanvasPalette.ink)
                                Spacer(minLength: 8)
                                Image(systemName: "arrow.up.right").font(.system(size: 10))
                                    .foregroundStyle(CanvasPalette.secondary)
                            }
                            .padding(.horizontal, 14).padding(.vertical, 11)
            .background(CanvasPalette.surface.opacity(0.78), in: RoundedRectangle(cornerRadius: 10))
                            .overlay(RoundedRectangle(cornerRadius: 10).stroke(CanvasPalette.line, lineWidth: 1))
                        }
                        .buttonStyle(.plain).disabled(!canInteract("choose", option.id))
                    }
                    if let explanation, !explanation.isEmpty {
                        Text(explanation).font(.system(size: 12)).foregroundStyle(CanvasPalette.secondary)
                    }
                }
            case .table(let columns, let rows):
                CanvasTable(columns: columns, rows: rows)
            case .chart(let kind, let title, let labels, let series):
                CanvasChart(kind: kind, title: title, labels: labels, series: series)
            case .drawing:
                EmptyView()
            }
        }
    }

}

private struct CanvasTable: View {
    let columns: [String]
    let rows: [[String]]

    var body: some View {
        ScrollView(.horizontal) {
            Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 0) {
                GridRow {
                    ForEach(columns.indices, id: \.self) { index in
                        Text(columns[index]).font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(CanvasPalette.secondary).padding(.vertical, 10)
                    }
                }
                .overlay(alignment: .bottom) { Rectangle().fill(CanvasPalette.line).frame(height: 1) }
                ForEach(rows.indices, id: \.self) { rowIndex in
                    GridRow {
                        ForEach(columns.indices, id: \.self) { columnIndex in
                            Text(rows[rowIndex][columnIndex]).font(.system(size: 13))
                                .foregroundStyle(CanvasPalette.ink).padding(.vertical, 11)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .overlay(alignment: .bottom) { Rectangle().fill(CanvasPalette.line.opacity(0.6)).frame(height: 1) }
                }
            }
        }
        .padding(.horizontal, 14)
        .background(CanvasPalette.surface, in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct CanvasChart: View {
    let kind: String
    let title: String
    let labels: [String]
    let series: [CanvasChartSeries]

    private let colors = [CanvasPalette.sage, CanvasPalette.ochre, CanvasPalette.blue, CanvasPalette.rose, CanvasPalette.violet]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(.system(size: 13, weight: .medium)).foregroundStyle(CanvasPalette.ink)
            GeometryReader { geometry in
                let width = max(geometry.size.width - 52, 80)
                let height = geometry.size.height - 32
                let values = series.flatMap(\.values)
                let low = min(0, values.min() ?? 0)
                let high = max(0, values.max() ?? 0)
                let span = max(high - low, 1)
                let middle = low + span / 2
                HStack(spacing: 8) {
                    VStack(alignment: .trailing, spacing: 0) {
                        Text(Self.number(high))
                        Spacer(minLength: 0)
                        Text(Self.number(middle))
                        Spacer(minLength: 0)
                        Text(Self.number(low))
                    }
                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                    .foregroundStyle(CanvasPalette.secondary)
                    .frame(width: 39, height: height, alignment: .top)
                    ZStack(alignment: .topLeading) {
                        ForEach(0..<3, id: \.self) { grid in
                            Path { path in
                                let y = CGFloat(grid) * height / 2
                                path.move(to: CGPoint(x: 0, y: y))
                                path.addLine(to: CGPoint(x: width, y: y))
                            }.stroke(CanvasPalette.line, style: StrokeStyle(lineWidth: 1, dash: [3, 4]))
                        }
                        ForEach(series.indices, id: \.self) { seriesIndex in
                            let points = series[seriesIndex].values.enumerated().map { index, value in
                                CGPoint(x: labels.count < 2 ? width / 2 : CGFloat(index) * width / CGFloat(labels.count - 1),
                                        y: height - CGFloat((value - low) / span) * height)
                            }
                            if kind == "line" {
                                Path { path in
                                    guard let first = points.first else { return }
                                    path.move(to: first)
                                    for point in points.dropFirst() { path.addLine(to: point) }
                                }.stroke(colors[seriesIndex % colors.count], style: StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                                ForEach(points.indices, id: \.self) { pointIndex in
                                    Circle().fill(colors[seriesIndex % colors.count])
                                        .frame(width: 7, height: 7)
                                        .position(points[pointIndex])
                                        .accessibilityLabel("\(labels[pointIndex]): \(Self.number(series[seriesIndex].values[pointIndex]))")
                                    if labels.count <= 8 {
                                        Text(Self.number(series[seriesIndex].values[pointIndex]))
                                            .font(.system(size: 9, weight: .medium, design: .monospaced))
                                            .foregroundStyle(CanvasPalette.ink)
                                            .position(x: points[pointIndex].x, y: max(7, points[pointIndex].y - 11))
                                    }
                                }
                            } else {
                                ForEach(points.indices, id: \.self) { pointIndex in
                                    let slot = width / CGFloat(max(labels.count, 1))
                                    let group = slot * 0.72 / CGFloat(series.count)
                                    let baseline = height - CGFloat((0 - low) / span) * height
                                    let point = points[pointIndex]
                                    let barWidth = max(3, group - 3)
                                    let barHeight = max(2, abs(baseline - point.y))
                                    let seriesOffset = CGFloat(seriesIndex) - CGFloat(series.count - 1) / 2
                                    let barX = slot * (CGFloat(pointIndex) + 0.5) + group * seriesOffset
                                    let barY = (baseline + point.y) / 2
                                    CanvasBar(color: colors[seriesIndex % colors.count],
                                              width: barWidth, height: barHeight,
                                              center: CGPoint(x: barX, y: barY))
                                    if labels.count <= 8 {
                                        Text(Self.number(series[seriesIndex].values[pointIndex]))
                                            .font(.system(size: 9, weight: .medium, design: .monospaced))
                                            .foregroundStyle(CanvasPalette.ink)
                                            .position(x: barX, y: max(7, point.y - 9))
                                    }
                                }
                            }
                        }
                        .frame(width: width, height: height, alignment: .topLeading)
                        .overlay(alignment: .bottom) {
                            HStack(spacing: 2) {
                                ForEach(labels.indices, id: \.self) { index in
                                    Text(labels[index]).lineLimit(1).truncationMode(.middle).frame(maxWidth: .infinity)
                                }
                            }
                            .font(.system(size: 10)).foregroundStyle(CanvasPalette.secondary)
                            .offset(y: 25)
                        }
                    }
                }
            }
            .frame(height: 196)
            HStack(spacing: 13) {
                ForEach(series.indices, id: \.self) { index in
                    HStack(spacing: 5) {
                        Circle().fill(colors[index % colors.count]).frame(width: 7, height: 7)
                        Text(series[index].name).font(.system(size: 10)).foregroundStyle(CanvasPalette.secondary)
                    }
                }
            }
        }
        .padding(16).background(CanvasPalette.surface.opacity(0.72), in: RoundedRectangle(cornerRadius: 12))
    }

    private static func number(_ value: Double) -> String {
        value.rounded() == value ? String(format: "%.0f", value) : String(format: "%.1f", value)
    }
}

private struct CanvasBar: View {
    let color: Color
    let width: CGFloat
    let height: CGFloat
    let center: CGPoint

    var body: some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(color)
            .frame(width: width, height: height)
            .position(center)
    }
}

private struct CanvasDrawing: View {
    let strokes: [CanvasStroke]

    var body: some View {
        GeometryReader { geometry in
            Canvas { context, size in
                for stroke in strokes {
                    guard let first = stroke.points.first else { continue }
                    var path = Path()
                    path.move(to: CGPoint(x: first.x * size.width, y: first.y * size.height))
                    for point in stroke.points.dropFirst() {
                        path.addLine(to: CGPoint(x: point.x * size.width, y: point.y * size.height))
                    }
                    context.stroke(path, with: .color(Self.color(stroke.color)),
                                   style: StrokeStyle(lineWidth: CGFloat(stroke.width),
                                                      lineCap: .round, lineJoin: .round))
                }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private static func color(_ name: String) -> Color {
        guard DRAWING_COLORS.contains(name), let number = UInt32(name.dropFirst(), radix: 16) else { return CanvasPalette.ink }
        return Color(red: Double((number >> 16) & 0xFF) / 255,
                     green: Double((number >> 8) & 0xFF) / 255,
                     blue: Double(number & 0xFF) / 255)
    }
}

struct CanvasDrawingOverlay: View {
    let strokes: [CanvasStroke]
    var body: some View {
        CanvasDrawing(strokes: strokes)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.clear)
    }
}

private enum CanvasPalette {
    static let warm = adaptive(light: (0.973, 0.969, 0.949), dark: (0.125, 0.141, 0.129))
    static let surface = adaptive(light: (0.992, 0.990, 0.978), dark: (0.160, 0.180, 0.164))
    static let ink = adaptive(light: (0.173, 0.192, 0.180), dark: (0.925, 0.937, 0.918))
    static let secondary = adaptive(light: (0.405, 0.443, 0.412), dark: (0.690, 0.722, 0.686))
    static let line = adaptive(light: (0.851, 0.863, 0.827), dark: (0.267, 0.302, 0.271))
    static let sage = adaptive(light: (0.416, 0.541, 0.459), dark: (0.576, 0.725, 0.600))
    static let ochre = adaptive(light: (0.741, 0.576, 0.353), dark: (0.835, 0.663, 0.431))
    static let blue = adaptive(light: (0.373, 0.541, 0.600), dark: (0.537, 0.694, 0.761))
    static let rose = adaptive(light: (0.722, 0.482, 0.459), dark: (0.843, 0.600, 0.576))
    static let violet = adaptive(light: (0.514, 0.416, 0.659), dark: (0.694, 0.600, 0.843))

    private static func adaptive(light: (Double, Double, Double), dark: (Double, Double, Double)) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let values = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
            return NSColor(calibratedRed: values.0, green: values.1, blue: values.2, alpha: 1)
        })
    }
}

struct CanvasDesktopSnapshotView: View {
    let model: PlaygroundModel
    let artifact: CanvasArtifact

    var body: some View {
        ZStack(alignment: .center) {
            CanvasDrawingOverlay(strokes: artifact.blocks.flatMap { block in
                if case .drawing(let strokes) = block.content { return strokes }
                return []
            })
            .allowsHitTesting(false)
            CanvasPanelView(model: model, artifact: artifact)
                .frame(width: 740, height: 660, alignment: .top)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(CanvasPalette.warm.opacity(0.94))
    }
}
