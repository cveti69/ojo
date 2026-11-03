import AVFAudio
import Combine

/// Hears the person for the rest of the call. It has its own engine, so a failure here leaves the agent's voice alone.
@MainActor
final class CallMicrophone {
    private static let ringing: TimeInterval = 0.3  // The agent's last word still sounds in the room this long.
    private static let restartLimit = 5  // In a row, less than a minute apart: more means the route will not settle.
    private static let secondTry: UInt64 = 500_000_000  // A new route can take a moment to give its sound.

    var onDiagnostic: ((String) -> Void)?
    var onLost: (() -> Void)?
    var phrases: AsyncStream<Phrase> { listener.phrases }
    var sound: AsyncStream<[Int16]> { listener.sound }
    var isHearing: Bool { listener.isHearing }
    var isSpeaking: Bool { listener.isSpeaking }
    var isTalking: Bool { listener.isTalking }
    var lastVoice: Date { listener.lastVoice }
    var dropped: (count: Int, loudest: Float) { listener.dropped }
    private(set) var isOn = false

    private let listener = PhraseListener()
    private var engine: AVAudioEngine?
    private var configurationChange: AnyCancellable?
    private var restarts = 0
    private var lastRestart = Date.distantPast
    private var nextTry: Task<Void, Never>?

    /// iOS lets the microphone start only while the app is on screen.
    @discardableResult
    func start() -> Bool {
        guard !isOn else { return true }
        #if targetEnvironment(simulator)
        isOn = true
        watchForSoundFile()
        #else
        startEngine()
        #endif
        return isOn
    }

    func stop() {
        nextTry?.cancel()
        nextTry = nil
        configurationChange = nil
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        engine = nil
        isOn = false
        listener.forget()
    }

    /// The person's own switch: the engine keeps running, so turning it back on needs no permission from iOS.
    func mute(_ isOn: Bool) {
        listener.mute(isOn)
    }

    func agentSpeaks(_ isSpeaking: Bool) {
        listener.deafen(until: isSpeaking ? .distantFuture : Date().addingTimeInterval(Self.ringing))
    }

    private func startEngine() {
        guard AVAudioApplication.shared.recordPermission == .granted else {
            onDiagnostic?("Микрофон: нет разрешения")
            return
        }
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            onDiagnostic?("Микрофон: нет входа")
            return
        }
        Self.tap(input, size: AVAudioFrameCount(format.sampleRate / 10), into: listener)
        do { if !engine.isRunning { try engine.start() } } catch {
            input.removeTap(onBus: 0)
            onDiagnostic?("Микрофон не запустился: \(error.localizedDescription)")
            return
        }
        self.engine = engine
        isOn = true
        configurationChange = NotificationCenter.default.publisher(for: .AVAudioEngineConfigurationChange, object: engine)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.restart() }
        onDiagnostic?("Микрофон слушает: \(Int(format.sampleRate)) Гц, каналов: \(format.channelCount)")
    }

    private func restart() {
        // iOS stops an engine when the sound route changes, for example when headphones connect.
        stop()
        restarts = Date().timeIntervalSince(lastRestart) < 60 ? restarts + 1 : 1
        lastRestart = Date()
        onDiagnostic?("Микрофон: звук изменился, перезапуск \(restarts)")
        guard restarts <= Self.restartLimit else {
            onLost?()
            return
        }
        guard !start() else { return }
        nextTry = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.secondTry)
            guard let self, !Task.isCancelled, !self.start() else { return }
            self.onLost?()
        }
    }

    // Nonisolated because the microphone calls back on its own thread.
    private nonisolated static func tap(_ input: AVAudioInputNode, size: AVAudioFrameCount, into listener: PhraseListener) {
        input.installTap(onBus: 0, bufferSize: size, format: nil) { buffer, _ in listener.hear(buffer) }
    }

    #if targetEnvironment(simulator)
    /// A simulator's microphone is the Mac's, so a sound file dropped into tmp plays the person's part.
    private func watchForSoundFile() {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("heard.wav")
        Task { [weak self] in
            while let self, self.isOn {
                if FileManager.default.fileExists(atPath: url.path) {
                    await self.hear(url)
                    try? FileManager.default.removeItem(at: url)
                }
                try? await Task.sleep(nanoseconds: 300_000_000)
            }
        }
    }

    private func hear(_ url: URL) async {
        guard let file = try? AVAudioFile(forReading: url) else { return }
        let size = AVAudioFrameCount(file.processingFormat.sampleRate / 10)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: size) else { return }
        while isOn, (try? file.read(into: buffer)) != nil, buffer.frameLength > 0 {
            listener.hear(buffer)
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
    }
    #endif
}
