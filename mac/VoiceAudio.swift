import Foundation
@preconcurrency import AVFoundation

/// One native duplex engine supplies the echo reference and the processed mic.
/// Python owns utterance decisions; this class owns capture, buffering and playout.
@MainActor
final class VoiceAudio {
    private let engine = AVAudioEngine()
    private let output = AVAudioPlayerNode()
    private let conversionQueue = DispatchQueue(label: "Aloy.MicrophoneConversion", qos: .userInitiated)
    private var captureToken = UUID()
    private var playbackToken = UUID()
    private var pending: [AVAudioPCMBuffer] = []
    private var queuedFrames = 0
    private var scheduled = 0
    private var ended = false
    private var started = false
    private var renderTimer: Timer?
    private var configurationObserver: NSObjectProtocol?
    private(set) var capturing = false
    private(set) var streamID: String?
    var onPCM: ((Data) -> Void)?
    var onPlaybackFinished: (() -> Void)?
    var onMetric: ((String, Double?) -> Void)?
    var onError: ((String) -> Void)?
    private let playbackFormat = AVAudioFormat(standardFormatWithSampleRate: 24000, channels: 1)!

    var hasPlayback: Bool { streamID != nil }

    init(volume: Float = 1) {
        engine.attach(output)
        engine.connect(output, to: engine.mainMixerNode, format: playbackFormat)
        engine.mainMixerNode.outputVolume = volume
        configurationObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, self.capturing, !self.engine.isRunning else { return }
                self.onError?("Audio device changed. Reopen voice with Option–Z.")
            }
        }
    }

    deinit {
        if let configurationObserver { NotificationCenter.default.removeObserver(configurationObserver) }
    }

    func startCapture() throws {
        stopCapture()
        engine.stop()
        let input = engine.inputNode
        try input.setVoiceProcessingEnabled(true)
        input.isVoiceProcessingAGCEnabled = true
        // Do not substantially lower videos/music merely because the session is open.
        if #available(macOS 14.0, *) {
            input.voiceProcessingOtherAudioDuckingConfiguration = .init(
                enableAdvancedDucking: false, duckingLevel: .min)
        }
        let source = input.outputFormat(forBus: 0)
        guard source.sampleRate > 0, source.channelCount > 0,
              let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000,
                                         channels: 1, interleaved: false),
              let converter = AVAudioConverter(from: source, to: target) else {
            throw NSError(domain: "Aloy.Audio", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Microphone format unavailable"])
        }
        let token = UUID()
        captureToken = token
        input.installTap(onBus: 0, bufferSize: 1024, format: source) { [weak self] buffer, _ in
            // Audio tap buffers are reused by Core Audio. Copy before leaving the callback.
            guard let copy = AVAudioPCMBuffer(pcmFormat: source, frameCapacity: buffer.frameLength) else { return }
            copy.frameLength = buffer.frameLength
            let src = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
            let dst = UnsafeMutableAudioBufferListPointer(copy.mutableAudioBufferList)
            for index in 0..<min(src.count, dst.count) {
                if let from = src[index].mData, let to = dst[index].mData {
                    memcpy(to, from, Int(src[index].mDataByteSize))
                }
            }
            self?.conversionQueue.async { [weak self] in
                let capacity = AVAudioFrameCount(ceil(Double(copy.frameLength) * 16000 / source.sampleRate)) + 32
                guard let converted = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }
                var supplied = false
                var error: NSError?
                converter.convert(to: converted, error: &error) { _, status in
                    if supplied { status.pointee = .noDataNow; return nil }
                    supplied = true
                    status.pointee = .haveData
                    return copy
                }
                guard error == nil, let samples = converted.int16ChannelData?[0], converted.frameLength > 0 else { return }
                let data = Data(bytes: samples, count: Int(converted.frameLength) * 2)
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.capturing, self.captureToken == token else { return }
                    self.onPCM?(data)
                }
            }
        }
        capturing = true
        do { try engine.start() }
        catch { stopCapture(); throw error }
        onMetric?("voice_processing_enabled", 1)
        onMetric?("mic_open", nil)
    }

    func stopCapture() {
        captureToken = UUID()
        if capturing {
            engine.inputNode.removeTap(onBus: 0)
            capturing = false
            engine.stop()
            try? engine.inputNode.setVoiceProcessingEnabled(false)
            onMetric?("mic_closed", nil)
        }
    }

    func beginStream(_ id: String) throws {
        stopPlayback()
        if !engine.isRunning { try engine.start() }
        streamID = id
        ended = false
        onMetric?("audio_received", nil)
    }

    func append(_ data: Data, stream id: String) {
        guard streamID == id, data.count % 2 == 0, data.count <= 480_000,
              let buffer = AVAudioPCMBuffer(pcmFormat: playbackFormat,
                                            frameCapacity: AVAudioFrameCount(data.count / 2)),
              let samples = buffer.floatChannelData?[0] else { return }
        buffer.frameLength = buffer.frameCapacity
        data.withUnsafeBytes { bytes in
            for index in 0..<Int(buffer.frameLength) {
                samples[index] = Float(Int16(littleEndian: bytes.loadUnaligned(fromByteOffset: index * 2, as: Int16.self))) / 32768
            }
        }
        queuedFrames += Int(buffer.frameLength)
        pending.append(buffer)
        if started || queuedFrames >= 5760 { schedulePending() } // 240 ms startup cushion.
    }

    func endStream(_ id: String) {
        guard streamID == id else { return }
        ended = true
        schedulePending()
        finishIfDrained()
    }

    private func schedulePending() {
        let token = playbackToken
        for buffer in pending {
            scheduled += 1
            let frames = Int(buffer.frameLength)
            output.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                DispatchQueue.main.async { [weak self] in
                    guard let self, self.playbackToken == token else { return }
                    self.scheduled -= 1
                    self.queuedFrames -= frames
                    if self.scheduled == 0 && !self.ended {
                        self.onMetric?("playback_underrun", nil)
                    }
                    self.finishIfDrained()
                }
            }
        }
        pending.removeAll()
        if !started && scheduled > 0 {
            started = true
            output.play()
            // A render-clock observation, not a claim about acoustic speaker onset.
            renderTimer = Timer.scheduledTimer(withTimeInterval: 0.01, repeats: true) { [weak self] timer in
                MainActor.assumeIsolated {
                    guard let self, self.playbackToken == token else { timer.invalidate(); return }
                    if let time = self.output.lastRenderTime,
                       let playing = self.output.playerTime(forNodeTime: time), playing.sampleTime > 0 {
                        self.onMetric?("playback_render_started", nil)
                        timer.invalidate()
                    }
                }
            }
        }
    }

    private func finishIfDrained() {
        guard ended, scheduled == 0, pending.isEmpty, streamID != nil else { return }
        streamID = nil
        output.stop()
        renderTimer?.invalidate()
        onMetric?("playback_drained", nil)
        onPlaybackFinished?()
        if !capturing { engine.stop() }
    }

    func playFile(_ url: URL, id: String) throws {
        let file = try AVAudioFile(forReading: url)
        guard file.length > 0, file.length < 30_000_000,
              let source = AVAudioPCMBuffer(pcmFormat: file.processingFormat,
                                           frameCapacity: AVAudioFrameCount(file.length)),
              let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 24000,
                                         channels: 1, interleaved: false),
              let converter = AVAudioConverter(from: file.processingFormat, to: target) else {
            throw NSError(domain: "Aloy.Audio", code: 2)
        }
        try file.read(into: source)
        let capacity = AVAudioFrameCount(ceil(Double(source.frameLength) * 24000 / file.processingFormat.sampleRate)) + 32
        guard let converted = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }
        var supplied = false
        var error: NSError?
        converter.convert(to: converted, error: &error) { _, status in
            if supplied { status.pointee = .endOfStream; return nil }
            supplied = true
            status.pointee = .haveData
            return source
        }
        if let error { throw error }
        guard let samples = converted.int16ChannelData?[0] else { return }
        let data = Data(bytes: samples, count: Int(converted.frameLength) * 2)
        try beginStream("file:" + id)
        for offset in stride(from: 0, to: data.count, by: 9600) {
            append(data.subdata(in: offset..<min(offset + 9600, data.count)), stream: "file:" + id)
        }
        endStream("file:" + id)
    }

    func stopPlayback() {
        playbackToken = UUID()
        renderTimer?.invalidate()
        renderTimer = nil
        output.stop()
        pending.removeAll()
        queuedFrames = 0
        scheduled = 0
        started = false
        ended = false
        streamID = nil
    }

    func shutdown() {
        stopCapture()
        stopPlayback()
        engine.stop()
    }
}
