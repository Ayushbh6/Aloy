import AppKit
import AVKit
import SwiftUI

private struct PlaygroundMediaPlayer: View {
    @State private var player: AVPlayer
    init(path: String) { _player = State(initialValue: AVPlayer(url: URL(fileURLWithPath: path))) }
    var body: some View { VideoPlayer(player: player).onDisappear { player.pause() } }
}

struct PlaygroundConversation: Identifiable, Equatable {
    let id: String
    let title: String
}

struct PlaygroundMessage: Identifiable {
    let id: String
    let role: String
    var text: String
    let status: String
    var runID: String?
    var artifactIDs: [String] = []
}

struct PlaygroundAudio: Identifiable {
    let id: String
    let path: String
    let direction: String
    let runID: String?
    let messageID: String?
}

struct PlaygroundEvent: Identifiable {
    let id = UUID()
    let title: String
    let detail: String
    let time: Date
    let isError: Bool
    var payload: String? = nil
}

enum CompanionPresence: String {
    case ready, recording, listening, thinking, speaking, error

    var label: String {
        switch self {
        case .ready: "Ready"
        case .recording: "Recording"
        case .listening: "Listening"
        case .thinking: "Thinking"
        case .speaking: "Speaking"
        case .error: "Needs attention"
        }
    }
}

@MainActor
final class PlaygroundModel: ObservableObject {
    let command: (String, [String: Any]) -> Void

    @Published var conversations: [PlaygroundConversation] = []
    @Published var conversationSearchResults: [PlaygroundConversation]?
    @Published var conversationQuery = ""
    @Published var selectedConversation: String?
    @Published var messages: [PlaygroundMessage] = []
    @Published var events: [PlaygroundEvent] = []
    @Published var sources: [(title: String, url: URL)] = []
    @Published var media: [[String: Any]] = []
    @Published var audioAssets: [PlaygroundAudio] = []
    @Published var attachments: [[String: Any]] = []
    @Published var artifacts: [CanvasArtifact] = []
    @Published private(set) var currentArtifact: CanvasArtifact? {
        didSet { notifyCanvasPresentation() }
    }
    @Published private(set) var canvasPresented = false {
        didSet { notifyCanvasPresentation() }
    }
    @Published var pinnedCanvasIDs = Set<String>()
    @Published var memories: [[String: Any]] = []
    @Published var searchResults: [[String: Any]] = []
    @Published var prompt = ""
    @Published var memoryQuery = ""
    @Published var provider = "gemini"
    @Published var visionRoute = "gemini"
    @Published var speechEngine = "chatterbox"
    @Published var voicesByEngine: [String: String] = [:]
    @Published var speechOptions: [[String: Any]] = []
    @Published var mode = "standard"
    @Published var agentPolicy = "read_only"
    @Published var hostAccess = "full"
    @Published var enabledTools = Set([
        "web.search", "memory.search", "memory.remember", "memory.forget",
        "screen.snapshot", "screen.record_clip", "media.inspect", "canvas.present",
        "read", "glob", "grep", "edit", "apply_patch", "terminal", "terminal_control",
        "context_retrieve", "capability_search", "capability_control"
    ])
    @Published var status = "Ready"
    @Published var lastError: String?
    @Published var companionStatus: CompanionPresence = .ready
    @Published var activeRun: String?
    @Published var isSending = false
    @Published var voiceSessionActive = false
    @Published var memoryMode = "hybrid"
    @Published var inspector = "Activity"
    @Published var storageBytes: Int64?
    @Published var retainedOrphans: Int?
    @Published var spendEstimate: Double?

    var onRecord: (() -> Void)?
    var onStop: (() -> Void)?
    var onReplayAudio: ((String) -> Void)?
    var onShowStorage: (() -> Void)?
    var onCanvasPresentationChanged: ((CanvasArtifact?, Bool) -> Void)?
    var onCanvasPinChanged: ((String, Bool) -> Void)?

    var speechVoice: String {
        voicesByEngine[speechEngine] ??
            (activeSpeechOption?["default_voice"] as? String) ?? "warm-male"
    }

    private var activeSpeechOption: [String: Any]? {
        speechOptions.first { $0["id"] as? String == speechEngine }
    }
    private var cancelledRunIDs = Set<String>()
    private var cancelledRunOrder: [String] = []
    private var completedRunIDs = Set<String>()

    init(command: @escaping (String, [String: Any]) -> Void) { self.command = command }

    func voice(for engine: String) -> String {
        if let selected = voicesByEngine[engine] { return selected }
        return speechOptions.first { $0["id"] as? String == engine }?["default_voice"] as? String ?? ""
    }

    func choices(for engine: String) -> [(id: String, title: String)] {
        (speechOptions.first { $0["id"] as? String == engine }?["voices"] as? [[String: String]] ?? [])
            .compactMap { option in
                guard let id = option["id"], let title = option["title"] else { return nil }
                return (id, title)
            }
    }

    func receive(_ object: [String: Any]) {
        guard let event = object["event"] as? String else { return }
        switch event {
        case "ready":
            let settings = object["settings"] as? [String: String] ?? [:]
            provider = settings["provider"] ?? provider
            visionRoute = settings["vision_route"] ?? visionRoute
            speechEngine = settings["speech"] ?? speechEngine
            mode = settings["mode"] ?? mode
            agentPolicy = settings["agent_policy"] ?? agentPolicy
            hostAccess = settings["host_access"] ?? hostAccess
            speechOptions = object["speech_options"] as? [[String: Any]] ?? speechOptions
            voicesByEngine = Dictionary(uniqueKeysWithValues: settings.compactMap { entry in
                let (key, value) = entry
                guard key.hasPrefix("voice:") else { return nil }
                return (String(key.dropFirst("voice:".count)), value)
            })
            if let encoded = settings["tools"]?.data(using: .utf8),
               let values = try? JSONSerialization.jsonObject(with: encoded) as? [String] {
                enabledTools = Set(values)
            }
            status = "Ready"
        case "conversations":
            conversations = Self.decodeConversations(object["items"])
        case "conversation_search":
            conversationSearchResults = Self.decodeConversations(object["items"])
        case "conversation":
            if let items = object["items"] { conversations = Self.decodeConversations(items) }
            let conversationID = object["conversation_id"] as? String
            if selectedConversation != conversationID {
                attachments.removeAll()
                events.removeAll()
                sources.removeAll()
                canvasPresented = false
                currentArtifact = nil
                artifacts.removeAll()
                pinnedCanvasIDs.removeAll()
            }
            selectedConversation = conversationID
            messages = (object["messages"] as? [[String: Any]] ?? []).compactMap { item in
                guard let id = item["id"] as? String else { return nil }
                return PlaygroundMessage(id: id,
                    role: item["role"] as? String ?? "assistant",
                    text: item["text"] as? String ?? "",
                    status: item["status"] as? String ?? "complete",
                    runID: item["run_id"] as? String)
            }
            media = object["media"] as? [[String: Any]] ?? []
            audioAssets = Self.decodeAudio(object["audio"])
            canvasPresented = false
            artifacts = Self.decodeArtifacts(object["artifacts"], conversationID: conversationID)
            completedRunIDs.formUnion(artifacts.map(\.runID))
            currentArtifact = artifacts.last
            attachArtifactsToMessages()
            activeRun = object["active_run"] as? String
            isSending = activeRun != nil
            if let activeRun {
                messages.append(PlaygroundMessage(id: UUID().uuidString, role: "assistant",
                    text: object["active_text"] as? String ?? "", status: "streaming", runID: activeRun))
            }
            receive(["event": "run_steps", "items": object["steps"] ?? [],
                     "sources": object["sources"] ?? []])
        case "started":
            lastError = nil
            isSending = true
            let runID = object["run_id"] as? String
            if let text = object["user_text"] as? String,
               messages.last?.role != "user" || messages.last?.text != text {
                messages.append(PlaygroundMessage(id: UUID().uuidString, role: "user",
                    text: text, status: "complete", runID: runID))
            }
            activeRun = runID
            events.removeAll()
            sources.removeAll()
            status = "Thinking"
            companionStatus = .thinking
            messages.append(PlaygroundMessage(id: UUID().uuidString, role: "assistant",
                text: "", status: "streaming", runID: runID))
        case "delta":
            guard object["run_id"] as? String == activeRun else { return }
            if messages.last?.role == "assistant" {
                messages[messages.count - 1].text += object["text"] as? String ?? ""
            }
        case "agent_event":
            guard let eventRun = object["run_id"] as? String,
                  eventRun == activeRun, !cancelledRunIDs.contains(eventRun) else { return }
            let kind = object["agent_kind"] as? String ?? "event"
            let data = object["data"] as? [String: Any] ?? [:]
            if kind == "artifact_presented" {
                guard let artifact = CanvasArtifact.decode(data),
                      artifact.conversationID == selectedConversation,
                      !cancelledRunIDs.contains(artifact.runID),
                      activeRun == artifact.runID else { return }
                if !artifacts.contains(where: { $0.id == artifact.id }) { artifacts.append(artifact) }
                if let index = messages.lastIndex(where: { $0.role == "assistant" && $0.runID == artifact.runID }),
                   !messages[index].artifactIDs.contains(artifact.id) {
                    messages[index].artifactIDs.append(artifact.id)
                }
                currentArtifact = artifact
                canvasPresented = true
            }
            if let run = object["run_id"] as? String, let activeRun, run != activeRun { return }
            if kind == "source", let title = data["title"] as? String,
               let link = data["url"] as? String, let url = URL(string: link),
               ["https", "http"].contains(url.scheme ?? "") {
                sources.append((title, url))
            }
            if kind == "media" {
                media.removeAll { ($0["id"] as? String) == (data["id"] as? String) }
                media.append(data)
            }
            let detail: String
            if kind == "tool_started" { detail = data["name"] as? String ?? "Tool" }
            else if kind == "tool_result" {
                detail = "\(data["name"] as? String ?? "Tool") · \((data["success"] as? Bool ?? false) ? "done" : "failed")"
            } else if kind == "context" {
                detail = "Revision \(data["revision"] ?? 0) · \(data["retrieved"] ?? 0) retrieved"
            } else if kind == "memory" { detail = data["text"] as? String ?? data["warning"] as? String ?? "Memory retrieved" }
            else if kind == "source" { detail = data["title"] as? String ?? "Citation" }
            else if kind == "media" { detail = "Visual asset captured" }
            else if kind == "artifact_presented" { detail = data["title"] as? String ?? "Canvas artifact" }
            else { detail = kind }
            events.append(PlaygroundEvent(title: kind.replacingOccurrences(of: "_", with: " ").capitalized,
                detail: detail, time: Date(), isError: data["success"] as? Bool == false,
                payload: kind == "tool_result" ? Self.json(data["result"]) : nil))
        case "completed":
            status = "Done"
            if let run = activeRun { command("run_steps", ["run_id": run]) }
        case "run_steps":
            events.removeAll { $0.title.hasPrefix("Step ") }
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            for item in object["items"] as? [[String: Any]] ?? [] {
                guard let name = item["name"] as? String else { continue }
                let stepStatus = item["status"] as? String ?? ""
                let start = formatter.date(from: item["started_at"] as? String ?? "")
                let end = formatter.date(from: item["ended_at"] as? String ?? "")
                let duration = start.flatMap { a in end.map { String(format: " · %.2fs", $0.timeIntervalSince(a)) } } ?? ""
                let error = item["error"] as? String ?? ""
                events.append(PlaygroundEvent(title: "Step \(item["sequence"] ?? "")",
                    detail: "\(name) · \(stepStatus)\(duration)\(error.isEmpty ? "" : " · " + error)",
                    time: start ?? Date(), isError: stepStatus == "failed" || stepStatus == "interrupted",
                    payload: item["result_json"] as? String ?? item["arguments_json"] as? String))
            }
            if let items = object["sources"] as? [[String: Any]] {
                sources = items.compactMap { item in
                    guard let link = item["url"] as? String, let url = URL(string: link),
                          ["https", "http"].contains(url.scheme ?? "") else { return nil }
                    return (item["title"] as? String ?? link, url)
                }
            }
        case "media_attached":
            if let asset = object["media"] as? [String: Any] { attachments.append(asset) }
        case "audio":
            guard let id = object["asset_id"] as? String, let path = object["path"] as? String else { return }
            audioAssets.removeAll { $0.id == id }
            audioAssets.append(PlaygroundAudio(id: id, path: path,
                direction: object["direction"] as? String ?? "output",
                runID: object["run_id"] as? String, messageID: object["message_id"] as? String))
        case "memory_list":
            memories = object["items"] as? [[String: Any]] ?? []
        case "memory_search":
            searchResults = object["items"] as? [[String: Any]] ?? []
        case "memory_deleted":
            command("memory_list", [:])
            searchMemory()
        case "settings_saved":
            status = "Settings saved"
        case "storage":
            storageBytes = (object["bytes"] as? NSNumber)?.int64Value
            retainedOrphans = object["retained_orphans"] as? Int
            spendEstimate = object["spend_usd"] as? Double
        case "deleted":
            selectedConversation = nil
            messages.removeAll()
            artifacts.removeAll()
            pinnedCanvasIDs.removeAll()
            canvasPresented = false
            currentArtifact = nil
            command("list", [:])
        case "capture_requested":
            status = "Recording visible screen…"
        case "turn_done":
            let finishedRun = activeRun
            isSending = false
            activeRun = nil
            if object["failed"] as? Bool != true, let finishedRun { completedRunIDs.insert(finishedRun) }
            status = object["failed"] as? Bool == true ? (lastError ?? "Failed") : "Ready"
            if object["failed"] as? Bool == true { companionStatus = .error }
            else { companionStatus = .ready }
            if object["failed"] as? Bool == true,
               let currentArtifact, currentArtifact.runID == finishedRun { canvasPresented = false }
        case "error":
            status = object["error"] as? String ?? "Error"
            lastError = status
            if activeRun == nil,
               ["send", "recorded", "canvas_interact"].contains(object["action"] as? String ?? "") {
                isSending = false
            }
            if let activeRun, object["run_id"] as? String == activeRun {
                command("run_steps", ["run_id": activeRun])
                invalidateRun(activeRun)
                if let currentArtifact, currentArtifact.runID == activeRun { removeTransientArtifact(currentArtifact) }
                isSending = false
                self.activeRun = nil
                companionStatus = .error
            } else if activeRun == nil {
                companionStatus = .error
            }
            events.append(PlaygroundEvent(title: "Error", detail: status, time: Date(), isError: true))
        case "stopped":
            if let activeRun {
                command("run_steps", ["run_id": activeRun])
                invalidateRun(activeRun)
                if let currentArtifact, currentArtifact.runID == activeRun { removeTransientArtifact(currentArtifact) }
            }
            isSending = false
            activeRun = nil
            status = "Stopped"
            companionStatus = .ready
        case "voice_activity":
            if object["state"] as? String == "speech" {
                if let activeRun { invalidateRun(activeRun) }
                activeRun = nil
                isSending = false
                companionStatus = .listening
            }
        case "no_speech":
            activeRun = nil
            isSending = false
        case "speech_activity":
            companionStatus = (object["state"] as? String == "speech") ? .listening : .recording
        default: break
        }
    }

    func select(_ conversationID: String) {
        guard selectedConversation != conversationID else { return }
        command("select", ["conversation_id": conversationID])
    }

    func newConversation() { command("new", [:]) }

    func deleteConversation(_ id: String) {
        command("delete", ["conversation_id": id])
    }

    func renameConversation(_ id: String, title: String) {
        let normalized = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return }
        command("rename", ["conversation_id": id, "title": String(normalized.prefix(100))])
    }

    func searchConversations() {
        let query = conversationQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else {
            conversationSearchResults = nil
            return
        }
        command("conversation_search", ["query": query])
    }

    func send() {
        guard let selectedConversation,
              !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              activeRun == nil, !isSending else { return }
        isSending = true
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        messages.append(PlaygroundMessage(id: UUID().uuidString, role: "user",
            text: text, status: "pending"))
        let payload: [String: Any] = [
            "conversation_id": selectedConversation,
            "text": text,
            "provider": provider,
            "speech": speechEngine,
            "voice": speechVoice,
            "mode": mode,
            "vision_route": visionRoute,
            "tools": Array(enabledTools).sorted(),
            "agent_policy": agentPolicy,
            "media_ids": attachments.compactMap { $0["id"] as? String }
        ]
        command("send", payload)
        prompt = ""
        attachments.removeAll()
    }

    func sendCanvasInteraction(artifact: CanvasArtifact, block: CanvasBlock,
                               interaction: String, value: String) {
        guard canInteractWithCanvas(artifact: artifact, block: block, interaction: interaction, value: value) else { return }
        isSending = true
        status = "Continuing from canvas"
        command("canvas_interact", [
            "conversation_id": artifact.conversationID,
            "artifact_id": artifact.id,
            "run_id": artifact.runID,
            "block_id": block.id,
            "interaction": interaction,
            "value": value
        ])
    }

    func canInteractWithCanvas(artifact: CanvasArtifact, block: CanvasBlock,
                               interaction: String, value: String) -> Bool {
        artifact.conversationID == selectedConversation && activeRun == nil && !isSending &&
            completedRunIDs.contains(artifact.runID) && artifacts.contains(where: { $0.id == artifact.id }) &&
            permittedInteraction(block: block, interaction: interaction, value: value)
    }

    func openCanvas(_ id: String) {
        guard let artifact = artifacts.first(where: { $0.id == id }),
              artifact.conversationID == selectedConversation else { return }
        currentArtifact = artifact
        canvasPresented = true
    }

    func showLatestCanvas() {
        guard let artifact = artifacts.last, artifact.conversationID == selectedConversation else { return }
        openCanvas(artifact.id)
    }

    func dismissCanvas() { canvasPresented = false }

    func pinCanvas(_ id: String, pinned: Bool) {
        if pinned { pinnedCanvasIDs.insert(id) }
        else { pinnedCanvasIDs.remove(id) }
        onCanvasPinChanged?(id, pinned)
    }

    func attach() {
        guard let selectedConversation else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png, .jpeg, .mpeg4Movie, .quickTimeMovie, .wav, .mp3, .mpeg4Audio]
        panel.allowsMultipleSelection = true
        if panel.runModal() == .OK {
            for url in panel.urls.prefix(max(0, 4 - attachments.count)) {
                command("attach_media", ["conversation_id": selectedConversation, "path": url.path])
            }
        }
    }

    func searchMemory() {
        if memoryQuery.isEmpty { command("memory_list", [:]) }
        else { command("memory_search", ["query": memoryQuery, "mode": memoryMode]) }
    }

    func saveTools() {
        guard let data = try? JSONSerialization.data(withJSONObject: Array(enabledTools).sorted()),
              let value = String(data: data, encoding: .utf8) else { return }
        persistSetting("tools", value)
    }

    func persistSetting(_ key: String, _ value: String) {
        command("set_setting", ["key": key, "value": value])
    }

    func replayOutput() {
        guard audioAssets.contains(where: { $0.direction == "output" }) else { return }
        onReplayAudio?("output")
    }

    func replayInput() {
        guard audioAssets.contains(where: { $0.direction == "input" }) else { return }
        onReplayAudio?("input")
    }

    func requestStorage() {
        if let onShowStorage { onShowStorage() }
        else { command("storage", [:]) }
    }

    private func attachArtifactsToMessages() {
        for artifact in artifacts {
            guard let index = messages.lastIndex(where: { $0.role == "assistant" && $0.runID == artifact.runID }),
                  !messages[index].artifactIDs.contains(artifact.id) else { continue }
            messages[index].artifactIDs.append(artifact.id)
        }
    }

    private func notifyCanvasPresentation() {
        onCanvasPresentationChanged?(currentArtifact, canvasPresented)
    }

    private func permittedInteraction(block: CanvasBlock, interaction: String, value: String) -> Bool {
        switch (block.content, interaction) {
        case (.choice(_, let options, _, _), "choose"):
            return options.contains { $0.id == value }
        case (.sentence(_, let target, _), "transform"):
            return value == target
        default:
            return false
        }
    }

    private func invalidateRun(_ id: String) {
        guard cancelledRunIDs.insert(id).inserted else { return }
        cancelledRunOrder.append(id)
        if cancelledRunOrder.count > 64 {
            cancelledRunIDs.remove(cancelledRunOrder.removeFirst())
        }
    }

    private func removeTransientArtifact(_ artifact: CanvasArtifact) {
        canvasPresented = false
        artifacts.removeAll { $0.id == artifact.id }
        currentArtifact = artifacts.last
    }

    private static func decodeConversations(_ raw: Any?) -> [PlaygroundConversation] {
        (raw as? [[String: Any]] ?? []).compactMap { item in
            guard let id = item["id"] as? String else { return nil }
            return PlaygroundConversation(id: id, title: item["title"] as? String ?? "Conversation")
        }
    }

    private static func decodeArtifacts(_ raw: Any?, conversationID: String?) -> [CanvasArtifact] {
        (raw as? [Any] ?? []).compactMap(CanvasArtifact.decode)
            .filter { conversationID == nil || $0.conversationID == conversationID }
            .sorted { $0.sequence < $1.sequence }
    }

    private static func decodeAudio(_ raw: Any?) -> [PlaygroundAudio] {
        (raw as? [[String: Any]] ?? []).compactMap { item in
            guard let id = item["id"] as? String, let path = item["path"] as? String else { return nil }
            return PlaygroundAudio(id: id, path: path, direction: item["direction"] as? String ?? "",
                runID: item["run_id"] as? String, messageID: item["message_id"] as? String)
        }
    }

    private static func json(_ value: Any?) -> String? {
        guard let value, JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value,
                                                    options: [.prettyPrinted, .sortedKeys]) else { return nil }
        return String(data: data, encoding: .utf8)
    }
}

struct AgentPlayground: View {
    @ObservedObject var model: PlaygroundModel
    @State private var page = "chat"
    @State private var inspectorVisible = false
    @State private var renameTarget: PlaygroundConversation?
    @State private var renameTitle = ""
    @State private var deleteTarget: PlaygroundConversation?
    @State private var deleteMemoryTarget: String?

    private let providers: [(String, String)] = [
        ("Gemini Lite", "gemini"), ("OpenRouter · GLM", "openrouter"),
        ("Gemini Flash", "gemini-quality"), ("Codex", "codex"), ("Offline fake", "fake")
    ]
    private let toolLabels: [(String, String)] = [
        ("read", "Read files and visual media"), ("glob", "Find files"), ("grep", "Search file contents"),
        ("edit", "Write and edit files"), ("apply_patch", "Patch files"),
        ("terminal", "Run terminal commands"), ("terminal_control", "Control terminal sessions"),
        ("context_retrieve", "Retrieve original context"),
        ("capability_search", "Discover skills and tools"), ("capability_control", "Use skills and tools"),
        ("web.search", "Web search"), ("memory.search", "Search memory"),
        ("memory.remember", "Save a memory"), ("memory.forget", "Forget a memory"),
        ("screen.snapshot", "Capture a screenshot"), ("screen.record_clip", "Record a short screen clip"),
        ("media.inspect", "Inspect attached media"), ("desktop.click", "Click on the desktop"),
        ("desktop.type_text", "Type text on the desktop"), ("desktop.hide_app", "Hide an app")
    ]

    private var visibleConversations: [PlaygroundConversation] {
        model.conversationSearchResults ?? model.conversations.filter {
            model.conversationQuery.isEmpty || $0.title.localizedCaseInsensitiveContains(model.conversationQuery)
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            sidebar.frame(width: 235)
            Rectangle().fill(AppPalette.line).frame(width: 1)
            pageContent.frame(maxWidth: .infinity, maxHeight: .infinity)
            if page == "chat" && inspectorVisible {
                Rectangle().fill(AppPalette.line).frame(width: 1)
                inspector.frame(width: 292)
            }
        }
        .background(AppPalette.paper)
        .onChange(of: model.provider) { _, value in model.persistSetting("provider", value) }
        .onChange(of: model.visionRoute) { _, value in model.persistSetting("vision_route", value) }
        .onChange(of: model.speechEngine) { _, value in model.persistSetting("speech", value) }
        .onChange(of: model.mode) { _, value in model.persistSetting("mode", value) }
        .onChange(of: model.speechVoice) { _, value in model.persistSetting("voice:\(model.speechEngine)", value) }
        .onChange(of: model.enabledTools) { _, _ in model.saveTools() }
        .onChange(of: model.hostAccess) { _, value in model.persistSetting("host_access", value) }
        .onChange(of: model.agentPolicy) { _, value in model.persistSetting("agent_policy", value) }
        .onChange(of: model.conversationQuery) { _, _ in model.searchConversations() }
        .sheet(item: $renameTarget) { conversation in
            VStack(alignment: .leading, spacing: 16) {
                Text("Rename conversation").font(.headline)
                TextField("Title", text: $renameTitle)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { saveRename(conversation) }
                HStack {
                    Spacer()
                    Button("Cancel") { renameTarget = nil }
                    Button("Save") { saveRename(conversation) }
                        .buttonStyle(.borderedProminent).tint(AppPalette.sage)
                }
            }
            .padding(22).frame(width: 360)
        }
        .alert("Delete conversation?", isPresented: Binding(
            get: { deleteTarget != nil }, set: { if !$0 { deleteTarget = nil } })) {
            Button("Delete", role: .destructive) {
                if let id = deleteTarget?.id { model.deleteConversation(id) }
                deleteTarget = nil
            }
            Button("Cancel", role: .cancel) { deleteTarget = nil }
        } message: {
            Text("Its messages and attached media will be removed from this Mac.")
        }
        .alert("Forget this memory?", isPresented: Binding(
            get: { deleteMemoryTarget != nil }, set: { if !$0 { deleteMemoryTarget = nil } })) {
            Button("Forget", role: .destructive) {
                if let id = deleteMemoryTarget { model.command("memory_delete", ["memory_id": id]) }
                deleteMemoryTarget = nil
            }
            Button("Cancel", role: .cancel) { deleteMemoryTarget = nil }
        } message: {
            Text("This removes the memory from future recall. The original conversation remains saved.")
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text("Aloy").font(.system(size: 25, weight: .medium, design: .rounded))
                    .tracking(-0.8).foregroundStyle(AppPalette.ink)
                Circle().fill(AppPalette.sage).frame(width: 6, height: 6)
                Spacer()
                Button(action: model.newConversation) {
                    Image(systemName: "square.and.pencil").font(.system(size: 14, weight: .medium))
                        .frame(width: 30, height: 30)
                }
                .buttonStyle(.plain).help("New conversation")
            }
            .padding(.bottom, 21)
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").font(.system(size: 12)).foregroundStyle(AppPalette.muted)
                TextField("Search conversations", text: $model.conversationQuery)
                    .textFieldStyle(.plain).font(.system(size: 12))
            }
            .padding(.horizontal, 10).padding(.vertical, 9)
            .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 9))
            .padding(.bottom, 13)
            HStack {
                Text("SESSIONS").font(.system(size: 9, weight: .semibold)).tracking(1.2).foregroundStyle(AppPalette.muted)
                Spacer()
                Text("\(visibleConversations.count)").font(.system(size: 10)).foregroundStyle(AppPalette.muted)
            }
            .padding(.horizontal, 7).padding(.bottom, 8)
            ScrollView {
                LazyVStack(spacing: 3) {
                    ForEach(visibleConversations) { conversation in
                        Button { page = "chat"; model.select(conversation.id) } label: {
                            HStack(spacing: 9) {
                                RoundedRectangle(cornerRadius: 1).fill(conversation.id == model.selectedConversation
                                    ? AppPalette.sage : .clear).frame(width: 2, height: 22)
                                Text(conversation.title).lineLimit(2).multilineTextAlignment(.leading)
                                    .font(.system(size: 12, weight: conversation.id == model.selectedConversation ? .medium : .regular))
                                    .foregroundStyle(AppPalette.ink.opacity(conversation.id == model.selectedConversation ? 1 : 0.77))
                                Spacer(minLength: 0)
                            }
                            .padding(.horizontal, 7).padding(.vertical, 8)
                            .background(conversation.id == model.selectedConversation ? AppPalette.surface : .clear,
                                        in: RoundedRectangle(cornerRadius: 8))
                        }
                        .buttonStyle(.plain)
                        .contextMenu {
                            Button("Rename") {
                                renameTarget = conversation
                                renameTitle = conversation.title
                            }
                            Button("Delete", role: .destructive) { deleteTarget = conversation }
                        }
                    }
                    if visibleConversations.isEmpty && !model.conversationQuery.isEmpty {
                        Text("No matching conversations")
                            .font(.system(size: 11)).foregroundStyle(AppPalette.muted)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(10)
                    }
                }
            }
            Spacer(minLength: 15)
            Rectangle().fill(AppPalette.line).frame(height: 1).padding(.bottom, 10)
            sidebarDestination("Memory", symbol: "sparkle.magnifyingglass", target: "memory")
            sidebarDestination("Settings", symbol: "slider.horizontal.3", target: "settings")
            HStack(spacing: 7) {
                Circle().fill(presenceColor).frame(width: 6, height: 6)
                Text(model.companionStatus.label).font(.system(size: 10)).foregroundStyle(AppPalette.muted)
                Spacer()
                Text("On this Mac").font(.system(size: 9)).foregroundStyle(AppPalette.muted)
            }
            .padding(.horizontal, 8).padding(.top, 15)
        }
        .padding(.horizontal, 15).padding(.top, 21).padding(.bottom, 16)
        .background(AppPalette.sageWash.opacity(0.54))
    }

    private func sidebarDestination(_ title: String, symbol: String, target: String) -> some View {
        Button { page = target } label: {
            HStack(spacing: 10) {
                Image(systemName: symbol).font(.system(size: 13))
                    .frame(width: 18).foregroundStyle(page == target ? AppPalette.sage : AppPalette.muted)
                Text(title).font(.system(size: 12, weight: page == target ? .medium : .regular))
                    .foregroundStyle(AppPalette.ink)
                Spacer()
            }
            .padding(.horizontal, 9).padding(.vertical, 9)
            .background(page == target ? AppPalette.surface : .clear, in: RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
    }

    private var presenceColor: Color {
        switch model.companionStatus {
        case .ready: AppPalette.sage
        case .recording, .listening: AppPalette.rose
        case .thinking: AppPalette.blue
        case .speaking: AppPalette.sage
        case .error: AppPalette.ochre
        }
    }

    @ViewBuilder private var pageContent: some View {
        switch page {
        case "memory": memoryPage
        case "settings": settingsPage
        default: chatPage
        }
    }

    private var chatPage: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.conversations.first(where: { $0.id == model.selectedConversation })?.title ?? "New conversation")
                        .font(.system(size: 14, weight: .medium)).foregroundStyle(AppPalette.ink).lineLimit(1)
                    Text(model.status).font(.system(size: 10)).foregroundStyle(AppPalette.muted)
                }
                Spacer()
                if model.audioAssets.contains(where: { $0.direction == "output" }) {
                    Button { model.onReplayAudio?("output") } label: {
                        Label("Replay", systemImage: "play.fill")
                    }.buttonStyle(.plain).font(.system(size: 11)).foregroundStyle(AppPalette.secondary)
                }
                if model.audioAssets.contains(where: { $0.direction == "input" }) {
                    Button { model.onReplayAudio?("input") } label: {
                        Image(systemName: "waveform")
                    }
                    .buttonStyle(.plain).font(.system(size: 11)).foregroundStyle(AppPalette.secondary)
                    .help("Replay the last recording")
                }
                Button { inspectorVisible.toggle() } label: {
                    Image(systemName: inspectorVisible ? "sidebar.right" : "sidebar.right")
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.plain).help(inspectorVisible ? "Hide details" : "Show details")
            }
            .padding(.horizontal, 24).padding(.vertical, 14)
            Rectangle().fill(AppPalette.line).frame(height: 1)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 23) {
                        if model.messages.isEmpty {
                            emptyState.frame(maxWidth: .infinity, alignment: .center).padding(.top, 104)
                        }
                        ForEach(model.messages) { message in
                            MessageRow(message: message,
                                artifacts: model.artifacts.filter { message.artifactIDs.contains($0.id) },
                                openArtifact: model.openCanvas)
                                .id(message.id)
                        }
                    }
                    .frame(maxWidth: 730)
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, 38).padding(.top, 28).padding(.bottom, 27)
                }
                .onChange(of: model.messages.count) { _, _ in
                    if let last = model.messages.last { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
            Rectangle().fill(AppPalette.line).frame(height: 1)
            composer
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("A little German, every day.")
                .font(.system(size: 30, weight: .medium, design: .rounded))
                .tracking(-1).foregroundStyle(AppPalette.ink)
            Text("Ask a question, try a sentence, or bring Aloy into what you’re working on.")
                .font(.system(size: 14)).lineSpacing(4).foregroundStyle(AppPalette.secondary)
                .frame(maxWidth: 430, alignment: .leading)
            HStack(spacing: 7) {
                Image(systemName: "mic").font(.system(size: 10))
                Text("Option–Z to talk from anywhere")
            }
            .font(.system(size: 11)).foregroundStyle(AppPalette.muted).padding(.top, 7)
        }
        .padding(.horizontal, 24).frame(maxWidth: 520, alignment: .leading)
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 9) {
            if !model.attachments.isEmpty {
                ScrollView(.horizontal) {
                    HStack(spacing: 8) {
                        ForEach(model.attachments.indices, id: \.self) { index in
                            HStack(spacing: 6) {
                                Image(systemName: "paperclip")
                                Text(model.attachments[index]["kind"] as? String ?? "Attachment").lineLimit(1)
                                Button { model.attachments.remove(at: index) } label: {
                                    Image(systemName: "xmark.circle.fill")
                                }.buttonStyle(.plain)
                            }
                            .font(.system(size: 10)).foregroundStyle(AppPalette.secondary)
                            .padding(.horizontal, 9).padding(.vertical, 6)
                            .background(AppPalette.surface, in: Capsule())
                        }
                    }
                }
            }
            ZStack(alignment: .topLeading) {
                if model.prompt.isEmpty {
                    Text("Ask Aloy…").font(.system(size: 14)).foregroundStyle(AppPalette.muted)
                        .padding(.leading, 7).padding(.top, 8)
                }
                TextEditor(text: $model.prompt)
                    .font(.system(size: 14)).scrollContentBackground(.hidden)
                    .frame(height: 64).padding(.horizontal, 1)
            }
            HStack(spacing: 13) {
                Button(action: model.attach) { Image(systemName: "paperclip") }
                    .help("Attach an image, audio, or video")
                Button { model.onRecord?() } label: {
                    Label(model.mode == "live" ? (model.companionStatus == .recording ? "Finish & Send" : "Record")
                        : (model.voiceSessionActive ? "Close voice" : "Talk to Aloy"), systemImage: "mic")
                }
                .help("Option–Z opens or closes a voice conversation")
                Button {
                    if let onStop = model.onStop { onStop() }
                    else { model.command("stop", [:]) }
                } label: {
                    Image(systemName: "stop.fill")
                }
                .disabled(!model.voiceSessionActive && !model.isSending && model.companionStatus != .speaking && model.companionStatus != .recording && model.companionStatus != .listening)
                .help("Stop the current turn or cancel recording")
                Spacer()
                Text("\(model.speechEngine == "chatterbox" ? "Local voice" : "\(model.speechEngine) voice")")
                    .font(.system(size: 10)).foregroundStyle(AppPalette.muted).lineLimit(1)
                Button(action: model.send) {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white).frame(width: 31, height: 31)
                        .background(AppPalette.sage, in: Circle())
                }
                .buttonStyle(.plain).help("Send message")
                .disabled(model.activeRun != nil || model.isSending || model.prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .keyboardShortcut(.return, modifiers: .command)
            }
            .font(.system(size: 11)).foregroundStyle(AppPalette.secondary)
        }
        .padding(.horizontal, 14).padding(.top, 10).padding(.bottom, 11)
        .background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 15))
        .overlay(RoundedRectangle(cornerRadius: 15).stroke(AppPalette.line, lineWidth: 1))
        .padding(.horizontal, 23).padding(.vertical, 15)
    }

    private var memoryPage: some View {
        VStack(spacing: 0) {
            pageHeader("Memory", subtitle: "Details Aloy may use in future conversations.")
            Rectangle().fill(AppPalette.line).frame(height: 1)
            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass").foregroundStyle(AppPalette.muted)
                TextField("Search saved memories", text: $model.memoryQuery)
                    .textFieldStyle(.plain).onSubmit(model.searchMemory)
                Picker("Search mode", selection: $model.memoryMode) {
                    Text("Hybrid").tag("hybrid")
                    Text("Words").tag("fts")
                    Text("Exact").tag("exact")
                    Text("Contains").tag("substring")
                    Text("Regex").tag("regex")
                }
                .frame(width: 135)
                Button(action: model.searchMemory) { Image(systemName: "arrow.right") }
                    .buttonStyle(.plain).help("Search memory")
            }
            .padding(.horizontal, 24).padding(.vertical, 14)
            Rectangle().fill(AppPalette.line).frame(height: 1)
            ScrollView {
                LazyVStack(spacing: 0) {
                    let items = model.memoryQuery.isEmpty ? model.memories : model.searchResults
                    if items.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(model.memoryQuery.isEmpty ? "No saved memories yet" : "No matching memories")
                                .font(.system(size: 17, weight: .medium)).foregroundStyle(AppPalette.ink)
                            Text(model.memoryQuery.isEmpty
                                ? "When something is saved, it will appear here."
                                : "Try another phrase or search mode.")
                                .font(.system(size: 13)).foregroundStyle(AppPalette.muted)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 46)
                    }
                    ForEach(items.indices, id: \.self) { index in
                        MemoryRow(item: items[index]) {
                            deleteMemoryTarget = items[index]["id"] as? String
                        }
                        Rectangle().fill(AppPalette.line.opacity(0.8)).frame(height: 1)
                    }
                }
                .frame(maxWidth: 760).frame(maxWidth: .infinity)
                .padding(.horizontal, 38).padding(.vertical, 10)
            }
        }
        .onAppear { model.searchMemory() }
    }

    private var settingsPage: some View {
        VStack(spacing: 0) {
            pageHeader("Settings", subtitle: "Choose how Aloy listens, responds, and uses tools.")
            Rectangle().fill(AppPalette.line).frame(height: 1)
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    settingsSection("Conversation", detail: "Select the text provider for new turns.") {
                        Picker("Text provider", selection: $model.provider) {
                            ForEach(providers, id: \.1) { Text($0.0).tag($0.1) }
                        }
                        .frame(width: 230)
                    }
                    settingsSection("Vision", detail: "Choose the service that inspects attached media.") {
                        Picker("Vision route", selection: $model.visionRoute) {
                            Text("Gemini Flash").tag("gemini")
                            Text("Qwen Omni").tag("openrouter")
                        }
                        .frame(width: 230)
                    }
                    settingsSection("Speech", detail: "Standard speech reads Aloy’s replies. Voice choices are saved per engine.") {
                        VStack(alignment: .leading, spacing: 13) {
                            HStack(spacing: 12) {
                                Picker("Speech engine", selection: $model.speechEngine) {
                                    ForEach(model.speechOptions.indices, id: \.self) { index in
                                        let option = model.speechOptions[index]
                                        if let id = option["id"] as? String {
                                            Text(option["title"] as? String ?? id).tag(id)
                                        }
                                    }
                                }
                                .frame(width: 260)
                                Picker("Voice", selection: Binding(
                                    get: { model.voice(for: model.speechEngine) },
                                    set: { model.voicesByEngine[model.speechEngine] = $0 })) {
                                    ForEach(model.choices(for: model.speechEngine), id: \.id) { choice in
                                        Text(choice.title).tag(choice.id)
                                    }
                                }
                                .frame(width: 230).disabled(model.choices(for: model.speechEngine).isEmpty)
                            }
                            Picker("Audio mode", selection: $model.mode) {
                                Text("Standard · transcription and speech").tag("standard")
                                Text("Live audio · Gemini Live").tag("live")
                            }
                            .frame(width: 310)
                            if let detail = model.speechOptions.first(where: { $0["id"] as? String == model.speechEngine })?["detail"] as? String {
                                Text(detail).font(.system(size: 11)).foregroundStyle(AppPalette.muted)
                            }
                        }
                    }
                    settingsSection("Tools", detail: "Enabled tools are available to new runs.") {
                        VStack(alignment: .leading, spacing: 10) {
                            Picker("Files and terminal", selection: $model.hostAccess) {
                                Text("Full Mac access").tag("full")
                                Text("Disabled").tag("disabled")
                            }.frame(width: 230)
                            Picker("Desktop policy", selection: $model.agentPolicy) {
                                Text("Read only").tag("read_only")
                                Text("Require approval").tag("approval_required")
                            }
                            .frame(width: 230)
                            Text("Desktop actions require both an enabled tool and approval-required policy.")
                                .font(.system(size: 11)).foregroundStyle(AppPalette.muted)
                            Rectangle().fill(AppPalette.line).frame(height: 1).padding(.vertical, 4)
                            ForEach(toolLabels, id: \.0) { tool, label in
                                Toggle(isOn: Binding(
                                    get: { model.enabledTools.contains(tool) },
                                    set: { enabled in
                                        if enabled { model.enabledTools.insert(tool) }
                                        else { model.enabledTools.remove(tool) }
                                    })) {
                                        Text(label).font(.system(size: 12)).foregroundStyle(AppPalette.ink)
                                    }
                                .toggleStyle(.switch).controlSize(.small)
                            }
                        }
                    }
                    settingsSection("Local storage", detail: "Conversations and memories remain on this Mac.") {
                        VStack(alignment: .leading, spacing: 8) {
                            Button("Refresh storage details", action: model.requestStorage)
                            if let storageBytes = model.storageBytes {
                                Text("\(ByteCountFormatter.string(fromByteCount: storageBytes, countStyle: .file)) · \(model.retainedOrphans ?? 0) retained temporary files")
                                    .font(.system(size: 11)).foregroundStyle(AppPalette.muted)
                            }
                            if let spend = model.spendEstimate {
                                Text("Estimated spend this month: $\(String(format: "%.4f", spend))")
                                    .font(.system(size: 11)).foregroundStyle(AppPalette.muted)
                            }
                        }
                    }
                }
                .frame(maxWidth: 760, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.horizontal, 38).padding(.vertical, 27)
            }
        }
        .onAppear { model.requestStorage() }
    }

    private func pageHeader(_ title: String, subtitle: String) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 17, weight: .medium)).foregroundStyle(AppPalette.ink)
                Text(subtitle).font(.system(size: 11)).foregroundStyle(AppPalette.muted)
            }
            Spacer()
            if page == "memory" {
                Text("\(model.memories.count) saved")
                    .font(.system(size: 10)).foregroundStyle(AppPalette.muted)
            }
        }
        .padding(.horizontal, 24).padding(.vertical, 15)
    }

    private func settingsSection<Content: View>(_ title: String, detail: String,
                                                @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(title.uppercased()).font(.system(size: 10, weight: .semibold))
                    .tracking(1.1).foregroundStyle(AppPalette.ink)
                Spacer()
            }
            Text(detail).font(.system(size: 11)).foregroundStyle(AppPalette.muted)
            content()
        }
        .padding(.bottom, 20)
        .overlay(alignment: .bottom) { Rectangle().fill(AppPalette.line).frame(height: 1) }
    }

    private var inspector: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Details").font(.system(size: 12, weight: .medium)).foregroundStyle(AppPalette.ink)
                Spacer()
                Button { inspectorVisible = false } label: { Image(systemName: "xmark") }
                    .buttonStyle(.plain).help("Hide details")
            }
            .padding(.bottom, 14)
            Picker("Inspect", selection: $model.inspector) {
                Text("Activity").tag("Activity")
                Text("Sources").tag("Sources")
                Text("Media").tag("Media")
            }
            .pickerStyle(.segmented).padding(.bottom, 12)
            Rectangle().fill(AppPalette.line).frame(height: 1)
            if model.inspector == "Activity" { activity }
            else if model.inspector == "Sources" { sourceList }
            else { mediaList }
        }
        .padding(15).background(AppPalette.sageWash.opacity(0.36))
    }

    private var activity: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 13) {
                if model.events.isEmpty {
                    Text("Activity will appear as Aloy works.")
                        .font(.system(size: 11)).foregroundStyle(AppPalette.muted).padding(.top, 14)
                }
                ForEach(model.events) { event in
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(spacing: 7) {
                            Circle().fill(event.isError ? AppPalette.rose : AppPalette.sage).frame(width: 5, height: 5)
                            Text(event.title).font(.system(size: 10, weight: .semibold)).foregroundStyle(AppPalette.ink)
                            Spacer()
                            Text(event.time, style: .time).font(.system(size: 9)).foregroundStyle(AppPalette.muted)
                        }
                        Text(event.detail).font(.system(size: 10)).foregroundStyle(AppPalette.secondary)
                            .textSelection(.enabled)
                        if let payload = event.payload, !payload.isEmpty {
                            DisclosureGroup("Inspect details") {
                                Text(payload).font(.system(size: 9, design: .monospaced))
                                    .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .font(.system(size: 9)).foregroundStyle(AppPalette.muted)
                        }
                    }
                }
            }
            .padding(.top, 12)
        }
    }

    private var sourceList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 11) {
                if model.sources.isEmpty {
                    Text("No sources in this exchange.").font(.system(size: 11)).foregroundStyle(AppPalette.muted).padding(.top, 14)
                }
                ForEach(model.sources.indices, id: \.self) { index in
                    Link(destination: model.sources[index].url) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(model.sources[index].title).font(.system(size: 11, weight: .medium))
                                .foregroundStyle(AppPalette.ink).multilineTextAlignment(.leading)
                            Text(model.sources[index].url.host ?? "Source").font(.system(size: 9)).foregroundStyle(AppPalette.muted)
                        }
                    }
                    Rectangle().fill(AppPalette.line).frame(height: 1)
                }
            }.padding(.top, 12)
        }
    }

    private var mediaList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                if model.media.isEmpty {
                    Text("No media in this exchange.").font(.system(size: 11)).foregroundStyle(AppPalette.muted).padding(.top, 14)
                }
                ForEach(model.media.indices, id: \.self) { index in
                    if let path = model.media[index]["path"] as? String {
                        if model.media[index]["kind"] as? String == "image",
                           let image = NSImage(contentsOfFile: path) {
                            Image(nsImage: image).resizable().scaledToFit().frame(maxHeight: 180)
                        } else {
                            PlaygroundMediaPlayer(path: path).id(path).frame(height: 150)
                        }
                    }
                }
            }.padding(.top, 12)
        }
    }

    private func saveRename(_ conversation: PlaygroundConversation) {
        model.renameConversation(conversation.id, title: renameTitle)
        renameTarget = nil
    }
}

private struct MessageRow: View {
    let message: PlaygroundMessage
    let artifacts: [CanvasArtifact]
    let openArtifact: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            if message.role == "user" {
                HStack {
                    Spacer(minLength: 30)
                    Text(message.text).font(.system(size: 14)).lineSpacing(4).foregroundStyle(AppPalette.ink)
                        .textSelection(.enabled).padding(.horizontal, 16).padding(.vertical, 12)
                        .background(AppPalette.sageWash, in: RoundedRectangle(cornerRadius: 16))
                }
            } else {
                HStack(spacing: 8) {
                    Text("ALOY").font(.system(size: 9, weight: .semibold)).tracking(1.1)
                        .foregroundStyle(AppPalette.sage)
                    if message.status == "streaming" {
                        Circle().fill(AppPalette.sage.opacity(0.6)).frame(width: 5, height: 5)
                    }
                }
                Text(message.text.isEmpty ? "…" : message.text)
                    .font(.system(size: 14)).lineSpacing(5).foregroundStyle(AppPalette.ink)
                    .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                ForEach(artifacts) { artifact in
                    Button { openArtifact(artifact.id) } label: {
                        HStack(spacing: 13) {
                            Text("a ↔ b").font(.system(size: 17, weight: .medium, design: .serif))
                                .foregroundStyle(AppPalette.sage).frame(width: 60, height: 52)
                                .background(AppPalette.sageWash, in: RoundedRectangle(cornerRadius: 9))
                            VStack(alignment: .leading, spacing: 4) {
                                Text(artifact.title).font(.system(size: 12, weight: .medium)).foregroundStyle(AppPalette.ink)
                                Text("Interactive canvas · Open on desktop")
                                    .font(.system(size: 10)).foregroundStyle(AppPalette.muted)
                            }
                            Spacer()
                            Image(systemName: "arrow.up.right").font(.system(size: 10)).foregroundStyle(AppPalette.muted)
                        }
                        .padding(10).background(AppPalette.surface, in: RoundedRectangle(cornerRadius: 11))
                        .overlay(RoundedRectangle(cornerRadius: 11).stroke(AppPalette.line, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct MemoryRow: View {
    let item: [String: Any]
    let forget: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 15) {
            VStack(alignment: .leading, spacing: 8) {
                Text(item["text"] as? String ?? "")
                    .font(.system(size: 13)).lineSpacing(4).foregroundStyle(AppPalette.ink)
                    .textSelection(.enabled)
                HStack(spacing: 9) {
                    Text(item["category"] as? String ?? item["kind"] as? String ?? "Memory")
                    if let scope = item["scope"] as? String { Text("· \(scope)") }
                    if let confidence = item["confidence"] as? Double {
                        Text("· \(Int(confidence * 100))% confidence")
                    }
                }
                .font(.system(size: 10)).foregroundStyle(AppPalette.muted)
            }
            Spacer(minLength: 10)
            if item["category"] != nil || item["kind"] as? String == "memory" {
                Button(action: forget) {
                    Image(systemName: "trash").font(.system(size: 11)).foregroundStyle(AppPalette.muted)
                        .frame(width: 28, height: 28)
                }
                .buttonStyle(.plain).help("Forget this memory")
            }
        }
        .padding(.vertical, 15)
    }
}

enum AppPalette {
    static let paper = adaptive(light: (0.981, 0.976, 0.957), dark: (0.118, 0.133, 0.122))
    static let surface = adaptive(light: (0.995, 0.992, 0.980), dark: (0.169, 0.192, 0.176))
    static let ink = adaptive(light: (0.169, 0.188, 0.176), dark: (0.925, 0.937, 0.918))
    static let secondary = adaptive(light: (0.390, 0.422, 0.396), dark: (0.690, 0.722, 0.686))
    static let muted = adaptive(light: (0.520, 0.548, 0.522), dark: (0.506, 0.553, 0.510))
    static let line = adaptive(light: (0.860, 0.870, 0.842), dark: (0.267, 0.302, 0.271))
    static let sage = adaptive(light: (0.416, 0.541, 0.459), dark: (0.576, 0.725, 0.600))
    static let sageWash = adaptive(light: (0.906, 0.929, 0.897), dark: (0.184, 0.239, 0.196))
    static let rose = adaptive(light: (0.720, 0.478, 0.455), dark: (0.843, 0.600, 0.576))
    static let blue = adaptive(light: (0.386, 0.549, 0.608), dark: (0.537, 0.694, 0.761))
    static let ochre = adaptive(light: (0.745, 0.576, 0.353), dark: (0.835, 0.663, 0.431))

    private static func adaptive(light: (Double, Double, Double), dark: (Double, Double, Double)) -> Color {
        Color(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
            let values = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
            return NSColor(calibratedRed: values.0, green: values.1, blue: values.2, alpha: 1)
        }))
    }
}
