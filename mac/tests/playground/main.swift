import Foundation
import AppKit
import SwiftUI

private func changed(_ object: Any, _ edit: (inout [String: Any]) -> Void) -> [String: Any] {
    var value = object as! [String: Any]
    edit(&value)
    return value
}

private func pythonJSONSize(_ value: Any) -> Int {
    func separators(_ value: Any) -> Int {
        if let object = value as? [String: Any] {
            return object.count + max(0, object.count - 1) + object.values.reduce(0) { $0 + separators($1) }
        }
        if let array = value as? [Any] {
            return max(0, array.count - 1) + array.reduce(0) { $0 + separators($1) }
        }
        return 0
    }
    return (try! JSONSerialization.data(withJSONObject: value)).count + separators(value)
}

@main
struct PlaygroundModelChecks {
    @MainActor
    static func main() {
        var commands: [(String, [String: Any])] = []
        let model = PlaygroundModel { commands.append(($0, $1)) }
        assert(model.enabledTools.contains("canvas.present"))
        assert(!model.enabledTools.contains("desktop.click"))
        let rejected = PlaygroundModel { _, _ in }
        rejected.receive(["event": "conversation", "conversation_id": "rejected", "messages": []])
        rejected.prompt = "Synthetic validation check"
        rejected.send()
        assert(rejected.isSending)
        rejected.receive(["event": "error", "action": "memory_search", "error": "Unrelated"])
        assert(rejected.isSending)
        rejected.receive(["event": "error", "action": "send", "error": "Invalid attachment"])
        assert(!rejected.isSending)
        rejected.receive(["event": "turn_done", "failed": true])
        assert(rejected.status == "Invalid attachment")

        model.receive(["event": "ready", "settings": [
            "provider": "openrouter", "vision_route": "openrouter", "speech": "gemini-lite",
            "voice:gemini-lite": "Achird", "mode": "live", "agent_policy": "read_only",
            "tools": "[\"web.search\"]"
        ], "speech_options": [["id": "gemini-lite", "default_voice": "Achird",
                               "voices": [["id": "Achird", "title": "Achird · Friendly"]]]]])
        assert(model.provider == "openrouter" && model.visionRoute == "openrouter")
        assert(model.speechEngine == "gemini-lite" && model.speechVoice == "Achird")
        assert(model.mode == "live" && model.enabledTools == ["web.search"])

        let fixtureURL = URL(fileURLWithPath: "tests/fixtures/chunk2_canvas.json")
        let fixtureData = try! Data(contentsOf: fixtureURL)
        let envelope = try! JSONSerialization.jsonObject(with: fixtureData)
        let artifact = CanvasArtifact.decode(envelope)
        assert(artifact?.id == "synthetic-artifact" && artifact?.runID == "synthetic-run")
        assert(artifact?.title == "German word order" && artifact?.blocks.count == 7)
        assert(Set(artifact?.blocks.map(\.kind) ?? []) ==
               Set(["heading", "text", "sentence", "choice", "table", "chart", "drawing"]))
        let sentence = artifact?.blocks.first { $0.id == "sentence" }
        assert(sentence != nil)

        let baseEnvelope = envelope as! [String: Any]
        let blocks = baseEnvelope["blocks"] as! [[String: Any]]
        func decoded(_ edit: (inout [String: Any]) -> Void) -> Bool {
            CanvasArtifact.decode(changed(envelope, edit)) != nil
        }
        assert(decoded { $0["title"] = String(repeating: "T", count: 120) })
        assert(!decoded { $0["title"] = String(repeating: "T", count: 121) })
        assert(decoded { $0["blocks"] = [["id": "long-text", "kind": "text", "text": String(repeating: "x", count: 4_000)]] })
        assert(!decoded { $0["blocks"] = [["id": "long-text", "kind": "text", "text": String(repeating: "x", count: 4_001)]] })
        var boundaryText = changed(envelope, { $0["title"] = "T" })
        let emptyBlocks = (0..<5).map { ["id": "long-\($0)", "kind": "text", "text": ""] as [String: Any] }
        let emptyTextSize = pythonJSONSize(["title": "T", "blocks": emptyBlocks])
        let exactBytes = 20_000 - emptyTextSize
        func sizedBlocks(_ count: Int) -> [[String: Any]] {
            var remaining = count
            return (0..<5).map { index in
                let length = min(4_000, remaining)
                remaining -= length
                return ["id": "long-\(index)", "kind": "text", "text": String(repeating: "x", count: length)]
            }
        }
        boundaryText["blocks"] = sizedBlocks(exactBytes)
        assert(pythonJSONSize(["title": "T", "blocks": boundaryText["blocks"]!]) == 20_000)
        assert(CanvasArtifact.decode(boundaryText) != nil)
        boundaryText["blocks"] = sizedBlocks(exactBytes + 1)
        assert(CanvasArtifact.decode(boundaryText) == nil)
        assert(decoded { $0["blocks"] = (0..<24).map { index in
            ["id": "block-\(index)", "kind": "text", "text": "within bound"]
        } })
        assert(!decoded { $0["blocks"] = (0..<25).map { ["id": "block-\($0)", "kind": "text", "text": "too many"] } })
        var maxSentence = blocks.first { $0["kind"] as? String == "sentence" }!
        maxSentence["source"] = String(repeating: "s", count: 1_200)
        maxSentence["target"] = String(repeating: "t", count: 1_200)
        assert(decoded { $0["blocks"] = blocks.map { ($0["id"] as? String) == "sentence" ? maxSentence : $0 } })
        maxSentence["source"] = String(repeating: "s", count: 1_201)
        assert(!decoded { $0["blocks"] = blocks.map { ($0["id"] as? String) == "sentence" ? maxSentence : $0 } })
        assert(!decoded { $0["extra"] = "not allowed" })
        assert(!decoded { $0["blocks"] = blocks.enumerated().map { index, block in
            var copy = block
            if index == 0 { copy["html"] = "<script>" }
            return copy
        } })
        assert(!decoded { $0["blocks"] = blocks.enumerated().map { index, block in
            var copy = block
            if index == 1 { copy["id"] = "heading" }
            return copy
        } })
        assert(!decoded { $0["blocks"] = blocks.map { block in
            guard block["kind"] as? String == "choice" else { return block }
            var copy = block
            copy["options"] = [["id": "same", "label": "one"], ["id": "same", "label": "two"]]
            return copy
        } })
        assert(!decoded { $0["blocks"] = blocks.map { block in
            guard block["kind"] as? String == "drawing" else { return block }
            var copy = block
            var strokes = copy["strokes"] as! [[String: Any]]
            strokes[0]["color"] = "#06a77d"
            copy["strokes"] = strokes
            return copy
        } })
        assert(!decoded { $0["blocks"] = blocks.map { block in
            guard block["kind"] as? String == "drawing" else { return block }
            var copy = block
            var strokes = copy["strokes"] as! [[String: Any]]
            strokes[0]["width"] = 0.49
            copy["strokes"] = strokes
            return copy
        } })
        model.receive(["event": "conversation", "conversation_id": "synthetic-conversation", "messages": [],
                       "active_run": "synthetic-run", "active_text": "Already streamed"])
        assert(model.activeRun == "synthetic-run" && model.isSending)
        assert(model.messages.last?.text == "Already streamed")
        model.receive(["event": "delta", "run_id": "old", "text": "STALE"])
        assert(model.messages.last?.text == "Already streamed")
        model.receive(["event": "delta", "run_id": "synthetic-run", "text": " tail"])
        assert(model.messages.last?.text == "Already streamed tail")
        model.receive(["event": "agent_event", "run_id": "synthetic-run",
                       "conversation_id": "synthetic-conversation",
                       "agent_kind": "artifact_presented", "data": envelope])
        assert(model.artifacts.map(\.id) == ["synthetic-artifact"])
        assert(model.currentArtifact?.title == "German word order")
        assert(model.messages.last?.artifactIDs == ["synthetic-artifact"])

        model.receive(["event": "stopped"])
        assert(!model.isSending && model.activeRun == nil)
        assert(model.currentArtifact == nil && model.artifacts.isEmpty && !model.canvasPresented)
        model.receive(["event": "agent_event", "run_id": "synthetic-run",
                       "agent_kind": "artifact_presented", "data": envelope])
        assert(model.artifacts.isEmpty && model.currentArtifact == nil)
        let eventsAfterStop = model.events.count
        model.receive(["event": "agent_event", "run_id": "synthetic-run",
                       "agent_kind": "source", "data": ["title": "Stale", "url": "https://example.com"]])
        assert(model.sources.isEmpty && model.events.count == eventsAfterStop)

        model.receive(["event": "conversation", "conversation_id": "synthetic-conversation", "messages": [],
                       "artifacts": [envelope]])
        assert(model.artifacts.first?.id == "synthetic-artifact")
        assert(model.currentArtifact?.id == "synthetic-artifact" && !model.canvasPresented)
        model.openCanvas("synthetic-artifact")
        assert(model.canvasPresented)
        model.dismissCanvas()
        assert(!model.canvasPresented && model.currentArtifact?.id == "synthetic-artifact")
        model.openCanvas("synthetic-artifact")
        assert(model.canvasPresented)
        model.pinCanvas("synthetic-artifact", pinned: true)
        assert(model.pinnedCanvasIDs.contains("synthetic-artifact"))
        model.pinCanvas("synthetic-artifact", pinned: false)
        assert(!model.pinnedCanvasIDs.contains("synthetic-artifact"))

        model.prompt = "Try another sentence"
        model.send()
        model.send()
        assert(commands.filter { $0.0 == "send" }.count == 1)
        let sendFields = commands.last(where: { $0.0 == "send" })?.1 ?? [:]
        assert(sendFields["speech"] as? String == "gemini-lite")
        assert(sendFields["voice"] as? String == "Achird")
        assert(sendFields["mode"] as? String == "live")
        model.receive(["event": "started", "run_id": "synthetic-run-2", "user_text": "Try another sentence"])
        assert(model.messages.filter { $0.role == "user" }.count == 1)
        model.receive(["event": "agent_event", "run_id": "synthetic-run-2", "agent_kind": "tool_result",
                       "data": ["name": "media.inspect", "success": true,
                                "result": ["observations": ["timestamp_seconds": 1]]]])
        assert(model.events.last?.payload?.contains("timestamp_seconds") == true)
        let runStepCount = commands.filter { $0.0 == "run_steps" }.count
        model.receive(["event": "error", "error": "Synthetic error"])
        assert(model.activeRun == "synthetic-run-2" && model.isSending)
        model.receive(["event": "error", "run_id": "unrelated", "error": "Other operation failed" ])
        assert(model.activeRun == "synthetic-run-2" && model.isSending)
        assert(commands.filter { $0.0 == "run_steps" }.count == runStepCount)
        model.receive(["event": "stopped"])
        assert(!model.isSending && model.activeRun == nil)

        model.receive(["event": "conversation", "conversation_id": "synthetic-conversation", "messages": [],
                       "artifacts": [envelope]])
        assert(model.artifacts.first?.id == "synthetic-artifact")
        assert(model.currentArtifact?.id == "synthetic-artifact" && !model.canvasPresented)
        model.sendCanvasInteraction(artifact: artifact!, block: sentence!,
                                    interaction: "transform", value: "not the validated target")
        assert(!commands.contains { $0.0 == "canvas_interact" })
        model.sendCanvasInteraction(artifact: artifact!, block: sentence!,
                                    interaction: "transform", value: "Ich lerne heute Deutsch.")
        let interaction = commands.last(where: { $0.0 == "canvas_interact" })?.1 ?? [:]
        assert(interaction["artifact_id"] as? String == "synthetic-artifact")
        assert(interaction["block_id"] as? String == "sentence")
        assert(interaction["interaction"] as? String == "transform")
        assert(interaction["value"] as? String == "Ich lerne heute Deutsch.")
        assert(Set(interaction.keys) == Set(["conversation_id", "artifact_id", "run_id", "block_id", "interaction", "value"]))
        model.sendCanvasInteraction(artifact: artifact!, block: sentence!,
                                    interaction: "transform", value: "Ich lerne heute Deutsch.")
        assert(commands.filter { $0.0 == "canvas_interact" }.count == 1)
        model.receive(["event": "turn_done", "failed": false])

        if let snapshotPath = ProcessInfo.processInfo.environment["ALOY_CANVAS_SNAPSHOT_PATH"] {
            let renderer = ImageRenderer(content: CanvasDesktopSnapshotView(model: model, artifact: artifact!)
                .frame(width: 820, height: 900, alignment: .topTrailing)
                .environment(\.colorScheme, .light))
            renderer.scale = 1
            if let image = renderer.nsImage,
               let tiff = image.tiffRepresentation,
               let bitmap = NSBitmapImageRep(data: tiff),
               let png = bitmap.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: snapshotPath))
            } else {
                assertionFailure("Canvas snapshot could not be rendered offscreen")
            }
        }

        model.conversationQuery = "word"
        model.searchConversations()
        assert(commands.last?.0 == "conversation_search")
        model.attachments = [["id": "attachment"]]
        model.receive(["event": "conversation", "conversation_id": "different", "messages": []])
        assert(model.attachments.isEmpty && model.messages.isEmpty)
        assert(model.currentArtifact == nil && !model.canvasPresented)
        print("Playground model, strict artifact validation, cancellation, and interaction checks passed")
    }
}
