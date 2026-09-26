import Foundation
import AVFoundation

@main
@MainActor
struct VoiceChecks {
    static func spin(_ seconds: Double) {
        let until = Date().addingTimeInterval(seconds)
        while Date() < until {
            RunLoop.main.run(until: Date().addingTimeInterval(0.005))
        }
    }
    static func main() throws {
        if CommandLine.arguments.count == 3 {
            try replay(pcmPath: CommandLine.arguments[1], tracePath: CommandLine.arguments[2])
            return
        }
        let audio = VoiceAudio(volume: 0) // Real rendering, silent output; no mic access.
        var metrics: [String] = []
        var finished = 0
        audio.onMetric = { stage, _ in metrics.append(stage) }
        audio.onPlaybackFinished = { finished += 1 }
        try audio.beginStream("first")
        let frames = Data(repeating: 0, count: 9600) // 200 ms
        audio.append(frames, stream: "stale")
        audio.append(frames, stream: "first")
        spin(0.05)
        precondition(!metrics.contains("playback_render_started"), "Must buffer before playback")
        audio.append(frames, stream: "first")
        spin(0.15)
        precondition(metrics.contains("playback_render_started"), "Render must start before synthesis ends")
        audio.append(frames, stream: "first")
        audio.endStream("first")
        spin(0.8)
        precondition(finished == 1 && !audio.hasPlayback, "Must finish only after the buffers play")
        precondition(!metrics.contains("playback_underrun"), "Buffered stream must not underrun")
        try audio.beginStream("cancel")
        audio.append(frames, stream: "cancel")
        audio.append(frames, stream: "cancel")
        spin(0.05)
        let began = ProcessInfo.processInfo.systemUptime
        audio.stopPlayback()
        let stopMS = (ProcessInfo.processInfo.systemUptime - began) * 1000
        audio.append(frames, stream: "cancel")
        audio.endStream("cancel")
        spin(0.6)
        precondition(!audio.hasPlayback && finished == 1, "Cancelled buffers must never resume")
        audio.shutdown()
        print("Native streaming passed: early render, gap-free buffer drain, stale chunk rejection, cancellation. Stop handler: \(stopMS) ms. No microphone capture.")
    }
    static func replay(pcmPath: String, tracePath: String) throws {
        let data = try Data(contentsOf: URL(fileURLWithPath: pcmPath))
        let trace = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: tracePath))) as! [String: Any]
        let events = trace["events"] as! [[String: Any]]
        let audio = VoiceAudio(volume: 0)
        let began = ProcessInfo.processInfo.systemUptime
        var metrics: [[String: Any]] = []
        var finished = false
        audio.onMetric = { stage, _ in
            metrics.append(["stage": stage, "seconds": ProcessInfo.processInfo.systemUptime - began])
        }
        audio.onPlaybackFinished = { finished = true }
        var offset = 0
        for event in events {
            let deadline = event["seconds"] as! Double
            spin(max(0, deadline - (ProcessInfo.processInfo.systemUptime - began)))
            switch event["event"] as! String {
            case "audio_stream_start": try audio.beginStream("replay")
            case "audio_chunk":
                let count = event["bytes"] as! Int
                audio.append(data.subdata(in: offset..<(offset + count)), stream: "replay")
                offset += count
            case "audio_stream_end": audio.endStream("replay")
            default: break
            }
        }
        let deadline = Date().addingTimeInterval(Double(data.count) / 48000 + 2)
        while !finished && Date() < deadline { spin(0.01) }
        precondition(finished && offset == data.count, "Full reply must drain")
        precondition(!metrics.contains { $0["stage"] as? String == "playback_underrun" }, "Provider pacing must not underrun")
        audio.shutdown()
        print(String(data: try JSONSerialization.data(withJSONObject: metrics, options: [.prettyPrinted, .sortedKeys]), encoding: .utf8)!)
    }

}
