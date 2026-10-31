import AVFAudio
import Combine

/// Hears the person for the rest of the call. It has its own engine, so a failure here leaves the agent's voice alone.
@MainActor
final class CallMicrophone {
    private static let ringing: TimeInterval = 0.3  // The agent's last word still sounds in the room this long.
    private static let restartLimit = 5  // In a row, less than a minute apart: more means the route will not settle.

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
    private var sharedEngine: AVAudioEngine?
    private var configurationChange: AnyCancellable?
    private var restarts = 0
    private var lastRestart = Date.distantPast

    /// Hearing over the agent's voice takes the voice's own engine: only then can iOS take the voice out of what it hears.
    init(sharedEngine: AVAudioEngine? = nil) {
        self.sharedEngine = sharedEngine
    }

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
        configurationChange = nil
        engine?.inputNode.removeTap(onBus: 0)
        if sharedEngine == nil { engine?.stop() }
        engine = nil
        isOn = false
        listener.forget()
    }

    func measure(with probe: AppleSpeechProbe) {
        listener.measure { [probe] buffer, isSpeech in probe.hear(buffer, isSpeech: isSpeech) }
    }

    func agentSpeaks(_ isSpeaking: Bool) {
        if sharedEngine != nil {  // With iOS filtering the voice out, the question is what the filter lets through.
            if let echo = listener.watchEcho(isSpeaking) { onDiagnostic?("Перебивание: эхо голоса, пик \(Int(echo)) дБ") }
        } else {
            listener.deafen(until: isSpeaking ? .distantFuture : Date().addingTimeInterval(Self.ringing))
        }
    }

    private func startEngine() {
        guard AVAudioApplication.shared.recordPermission == .granted else {
            onDiagnostic?("Микрофон: нет разрешения")
            return
        }
        let engine = sharedEngine ?? AVAudioEngine()
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
        if restarts > Self.restartLimit || !start() { onLost?() }
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
