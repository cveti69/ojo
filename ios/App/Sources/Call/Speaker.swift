import AVFAudio
import Shared

@MainActor
protocol Speaker: AnyObject {
    var onSpeaking: ((Bool) -> Void)? { get set }
    func prepare(client: ServerClient, journal: SharedJournal?)
    func speak(_ text: String)
    func speakAndWait(_ text: String) async
    func waitUntilQuiet() async
    func keep(_ texts: [String])
    func stop()
    func finish()
}

extension Speaker {
    var onSpeaking: ((Bool) -> Void)? { get { nil } set {} }
    func prepare(client: ServerClient, journal: SharedJournal?) {}
    func waitUntilQuiet() async {}
    func keep(_ texts: [String]) {}
    func finish() { stop() }
}

/// Plays the agent's words as they arrive from the server. With no sound in time, the fallback reads them instead.
@MainActor
final class ServerSpeaker: Speaker {
    private static let sampleRate = 24_000.0
    private static let bufferBytes = 4800  // 100 ms of 16-bit mono.
    var onSpeaking: ((Bool) -> Void)?
    private let fallback: any Speaker
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let format = AVAudioFormat(standardFormatWithSampleRate: ServerSpeaker.sampleRate, channels: 1)!
    private var client: ServerClient?
    private var journal: SharedJournal?
    private var playback: Task<Void, Never>?
    private var kept: [String: Data] = [:]

    init(fallback: any Speaker) {
        self.fallback = fallback
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    func prepare(client: ServerClient, journal: SharedJournal?) {
        self.client = client
        self.journal = journal
        // iOS can refuse this start from the background, so it happens while the app is on screen.
        do { try engine.start() }
        catch { journal?.append("Голос сервера: движок не запустился: \(error.localizedDescription)") }
    }

    func speak(_ text: String) {
        stop()
        onSpeaking?(true)
        playback = Task { [weak self] in
            await self?.play(text)
            if !Task.isCancelled { self?.onSpeaking?(false) }
        }
    }

    func speakAndWait(_ text: String) async {
        guard !Task.isCancelled else { return }
        speak(text)
        let playback = playback
        await withTaskCancellationHandler { await playback?.value } onCancel: { playback?.cancel() }
    }

    func waitUntilQuiet() async {
        await playback?.value
    }

    /// Short phrases are fetched ahead, so they sound the moment they are needed.
    func keep(_ texts: [String]) {
        guard let client else { return }
        Task { [weak self] in
            for text in texts where self?.kept[text] == nil {
                var sound = Data()
                do { for try await byte in try await client.speech(text) { sound.append(byte) } } catch { return }
                self?.kept[text] = sound
            }
        }
    }

    func stop() {
        playback?.cancel()
        playback = nil
        player.stop()
        fallback.stop()
        onSpeaking?(false)
    }

    func finish() {
        stop()
        engine.stop()
        client = nil
    }

    private func play(_ text: String) async {
        guard !text.isEmpty else { return }
        guard let client else {
            await fallback.speakAndWait(text)
            return
        }
        let started = Date()
        var frames = 0
        do {
            if !engine.isRunning { try engine.start() }
            if let sound = kept[text] {
                frames = schedule(sound)
            } else {
                var pending = Data(capacity: Self.bufferBytes)
                for try await byte in try await client.speech(text) {
                    pending.append(byte)
                    guard pending.count == Self.bufferBytes else { continue }
                    if frames == 0 {
                        journal?.append("Голос сервера: первый звук через \(Int(Date().timeIntervalSince(started) * 1000)) мс")
                    }
                    frames += schedule(pending)
                    pending.removeAll(keepingCapacity: true)
                }
                frames += schedule(pending)
            }
            try Task.checkCancellation()
            guard frames > 0 else { throw ClientError.invalidResponse }
            await finishPlaying(seconds: Double(frames) / Self.sampleRate)
        } catch {
            guard !Task.isCancelled else { return }
            journal?.append("Голос сервера: \(error.localizedDescription)")
            if frames == 0 { await fallback.speakAndWait(text) }
        }
    }

    private func schedule(_ data: Data) -> Int {
        let frames = data.count / 2
        guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
              let samples = buffer.floatChannelData?[0] else { return 0 }
        buffer.frameLength = AVAudioFrameCount(frames)
        data.withUnsafeBytes { raw in
            for index in 0..<frames {
                samples[index] = Float(Int16(littleEndian: raw.loadUnaligned(fromByteOffset: index * 2, as: Int16.self))) / 32768
            }
        }
        player.scheduleBuffer(buffer)
        if !player.isPlaying, engine.isRunning { player.play() }  // Playing on a stopped engine crashes the app.
        return frames
    }

    private func finishPlaying(seconds: TimeInterval) async {
        guard player.isPlaying, let end = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1) else { return }
        end.frameLength = 1
        end.floatChannelData?[0][0] = 0
        let watchdog = Task { [weak self] in  // A route change stops the engine with no callback at all.
            try? await Task.sleep(nanoseconds: UInt64((seconds + 2) * 1_000_000_000))
            if !Task.isCancelled { self?.player.stop() }
        }
        await withCheckedContinuation { continuation in
            Self.schedule(end, on: player) { continuation.resume() }
        }
        watchdog.cancel()
    }

    // Nonisolated because the player calls back on its own thread.
    private nonisolated static func schedule(_ buffer: AVAudioPCMBuffer, on player: AVAudioPlayerNode,
                                             then done: @escaping @Sendable () -> Void) {
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { _ in done() }
    }
}

@MainActor
final class SystemSpeaker: NSObject, Speaker, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var currentUtterance: AVSpeechUtterance?
    private var completion: CheckedContinuation<Void, Never>?
    private var waitingID: UUID?

    override init() {
        super.init()
        synthesizer.delegate = self
        synthesizer.usesApplicationAudioSession = true
    }

    func speak(_ text: String) {
        stop()
        start(text)
    }

    func speakAndWait(_ text: String) async {
        guard !Task.isCancelled else { return }
        let identifier = UUID()
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                stop()
                completion = continuation
                waitingID = identifier
                start(text)
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                guard self?.waitingID == identifier else { return }
                self?.stop()
            }
        }
    }

    func stop() {
        currentUtterance = nil
        waitingID = nil
        synthesizer.stopSpeaking(at: .immediate)
        completion?.resume()
        completion = nil
    }

    private func start(_ text: String) {
        guard !text.isEmpty else {
            completion?.resume()
            completion = nil
            waitingID = nil
            return
        }
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "ru-RU")
        currentUtterance = utterance
        synthesizer.speak(utterance)
    }

    private func finished(_ identifier: ObjectIdentifier) {
        guard let currentUtterance, ObjectIdentifier(currentUtterance) == identifier else { return }
        self.currentUtterance = nil
        waitingID = nil
        completion?.resume()
        completion = nil
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        let identifier = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in self?.finished(identifier) }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        let identifier = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in self?.finished(identifier) }
    }
}
