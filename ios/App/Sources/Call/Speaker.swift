import AVFAudio
import Shared

@MainActor
protocol Speaker: AnyObject {
    var onSpeaking: ((Bool) -> Void)? { get set }
    var onSounding: ((Bool) -> Void)? { get set }
    var mayStart: (() -> Bool)? { get set }
    func prepare(client: ServerClient, journal: SharedJournal?)
    func speak(_ text: String)
    func speakAndWait(_ text: String) async
    /// A line in sentences. Says how many of them began to sound.
    func speakAndWait(_ parts: [String]) async -> Int
    /// The line ends with the sentence that is sounding now.
    func endSentence()
    func waitUntilQuiet() async
    func keep(_ texts: [String])
    func stop()
    func finish()
}

extension Speaker {
    var onSpeaking: ((Bool) -> Void)? { get { nil } set {} }
    var onSounding: ((Bool) -> Void)? { get { nil } set {} }
    var mayStart: (() -> Bool)? { get { nil } set {} }
    func prepare(client: ServerClient, journal: SharedJournal?) {}
    func speakAndWait(_ parts: [String]) async -> Int {
        await speakAndWait(parts.joined(separator: " "))
        return parts.count
    }
    func endSentence() {}
    func waitUntilQuiet() async {}
    func keep(_ texts: [String]) {}
    func finish() { stop() }
}

/// Plays the agent's words as they arrive from the server. With no sound in time, the fallback reads them instead.
@MainActor
final class ServerSpeaker: Speaker {
    private static let sampleRate = 24_000.0
    private static let bufferBytes = 4800  // 100 ms of 16-bit mono.
    private static let longestWait: TimeInterval = 6  // A loud room must not keep the agent silent.
    private static let breath: TimeInterval = 0.5  // The microphone is deaf through a line and just after it, so it gets time to hear.
    private static let sentenceGap = Data(count: 9600)  // 200 ms between two sentences, and time to stop before the next one sounds.
    var onSpeaking: ((Bool) -> Void)?
    var onSounding: ((Bool) -> Void)?
    var mayStart: (() -> Bool)?
    let engine = AVAudioEngine()
    private var isSounding = false
    private var lastSounded = Date.distantPast
    private let fallback: any Speaker
    private let player = AVAudioPlayerNode()
    private let format = AVAudioFormat(standardFormatWithSampleRate: ServerSpeaker.sampleRate, channels: 1)!
    private var client: ServerClient?
    private var journal: SharedJournal?
    private var playback: Task<Void, Never>?
    private var kept: [String: Data] = [:]
    private var lineNumber = 0  // Stopping fires the marks of what was on the player too, and this tells them from real ones.
    private var partsOut = 0  // Sentences with sound on the player.
    private var partsDone = 0  // Sentences played to their end.
    private var endsHere = false
    var hearsOverVoice = false
    var voice: String?  // Read for each line, so a new choice works from the next one.
    private var filtersVoice = false

    init(fallback: any Speaker) {
        self.fallback = fallback
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    func prepare(client: ServerClient, journal: SharedJournal?) {
        self.client = client
        self.journal = journal
        fallback.prepare(client: client, journal: journal)
        if hearsOverVoice != filtersVoice, !engine.isRunning {
            do {
                try engine.inputNode.setVoiceProcessingEnabled(hearsOverVoice)
                filtersVoice = hearsOverVoice
            } catch { journal?.append("Перебивание: фильтр голоса не переключился: \(error.localizedDescription)") }
        }
        // iOS can refuse this start from the background, so it happens while the app is on screen.
        do { try engine.start() }
        catch { journal?.append("Голос сервера: движок не запустился: \(error.localizedDescription)") }
    }

    func speak(_ text: String) {
        speak(parts: [text])
    }

    private func speak(parts: [String]) {
        stop()
        (partsOut, partsDone, endsHere) = (0, 0, false)
        onSpeaking?(true)
        playback = Task { [weak self] in
            await self?.play(parts)
            if !Task.isCancelled { self?.onSpeaking?(false) }
        }
    }

    func speakAndWait(_ text: String) async {
        _ = await speakAndWait([text])
    }

    func speakAndWait(_ parts: [String]) async -> Int {
        guard !Task.isCancelled else { return 0 }
        speak(parts: parts)
        let playback = playback
        await withTaskCancellationHandler { await playback?.value } onCancel: { playback?.cancel() }
        return min(partsOut, partsDone + 1)
    }

    func endSentence() {
        endsHere = true
        if partsOut == partsDone { stop() }  // The next sentence is late and nothing sounds right now.
    }

    func waitUntilQuiet() async {
        await playback?.value
    }

    /// Short phrases are fetched ahead, so they sound the moment they are needed.
    func keep(_ texts: [String]) {
        guard let client else { return }
        let voice = voice
        Task { [weak self] in
            for text in texts where self?.kept[Self.key(text, voice)] == nil {
                var sound = Data()
                do { for try await byte in try await client.speech(text, voice: voice) { sound.append(byte) } } catch { return }
                self?.kept[Self.key(text, voice)] = sound
            }
        }
    }

    private static func key(_ text: String, _ voice: String?) -> String { (voice ?? "") + ":" + text }

    func stop() {
        lineNumber += 1
        playback?.cancel()
        playback = nil
        player.stop()
        fallback.stop()
        sounds(false)
        onSpeaking?(false)
    }

    func finish() {
        stop()
        engine.stop()
        client = nil
    }

    /// Each sentence is a request of its own, so the line can end between two of them. The voice makes sound faster than it
    /// plays, so the next one is on the player before this one is over.
    private func play(_ parts: [String]) async {
        guard !parts.joined().isEmpty else { return }
        guard let client else {
            await speakOurselves(parts, from: 0)
            return
        }
        let line = lineNumber
        let started = Date()
        var frames = 0
        var whole = 0  // Sentences with all their sound on the player.
        do {
            if !engine.isRunning { try engine.start() }
            for (index, part) in parts.enumerated() {
                if endsHere { break }
                if index > 0 { frames += schedule(Self.sentenceGap) }
                try await put(part, index, frames: &frames, client: client, started: started)
                try Task.checkCancellation()
                mark(endOf: index, line: line)
                whole = index + 1
            }
            guard frames > 0 else { throw ClientError.invalidResponse }
            await finishPlaying(seconds: Double(frames) / Self.sampleRate)
            if !Task.isCancelled { sounds(false) }
        } catch {
            guard !Task.isCancelled else { return }
            journal?.append("Голос сервера: \(error.localizedDescription)")
            if frames == 0 {
                await waitForThePerson()
                sounds(true)
            } else { await finishPlaying(seconds: Double(frames) / Self.sampleRate) }  // What is on the player still sounds.
            await speakOurselves(parts, from: whole)
            if !Task.isCancelled { sounds(false) }
        }
    }

    /// The phone's own voice takes the sentences the server gave no sound for, and they end where the line is told to end.
    private func speakOurselves(_ parts: [String], from first: Int) async {
        for index in first..<parts.count where !endsHere && !Task.isCancelled {
            partsOut = index + 1
            await fallback.speakAndWait(parts[index])
            if !Task.isCancelled { partsDone = index + 1 }
        }
    }

    private func put(_ text: String, _ index: Int, frames: inout Int, client: ServerClient, started: Date) async throws {
        if let sound = kept[Self.key(text, voice)] {
            try await begin(index)
            frames += schedule(sound)
            return
        }
        var begun = false
        var pending = Data(capacity: Self.bufferBytes)
        for try await byte in try await client.speech(text, voice: voice) {
            pending.append(byte)
            guard pending.count == Self.bufferBytes else { continue }
            if !begun {
                if index == 0 { journal?.append("Голос сервера: первый звук готов через \(Int(Date().timeIntervalSince(started) * 1000)) мс") }
                try await begin(index)
                begun = true
            }
            try Task.checkCancellation()
            frames += schedule(pending)
            pending.removeAll(keepingCapacity: true)
        }
        if !begun { try await begin(index) }
        try Task.checkCancellation()
        frames += schedule(pending)
    }

    /// Only the first sound of a line waits for the person.
    private func begin(_ index: Int) async throws {
        if index == 0 { await waitForThePerson() }
        try Task.checkCancellation()
        sounds(true)
        partsOut = index + 1
    }

    private func mark(endOf index: Int, line: Int) {
        guard let end = silentPoint else { return }
        Self.schedule(end, on: player) { [weak self] in
            Task { @MainActor [weak self] in self?.partEnded(index, line: line) }
        }
    }

    private func partEnded(_ index: Int, line: Int) {
        guard line == lineNumber else { return }
        partsDone = max(partsDone, index + 1)
        guard endsHere else { return }
        partsOut = partsDone  // What is on the player past here never sounds.
        stop()
    }

    private var silentPoint: AVAudioPCMBuffer? {
        guard let point = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1) else { return nil }
        point.frameLength = 1
        point.floatChannelData?[0][0] = 0
        return point
    }

    /// The answer may be ready before the person has finished. It holds its first sound until they have.
    private func waitForThePerson() async {
        let started = Date()
        while !Task.isCancelled, Date().timeIntervalSince(started) < Self.longestWait,
              Date().timeIntervalSince(lastSounded) < Self.breath || mayStart?() == false {
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
    }

    private func sounds(_ value: Bool) {
        guard value != isSounding else { return }
        isSounding = value
        if !value { lastSounded = Date() }
        onSounding?(value)
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
        guard player.isPlaying, let end = silentPoint else { return }
        let played = OneWait()
        let watchdog = Task { [weak self] in  // A route change stops the engine with no callback at all.
            try? await Task.sleep(nanoseconds: UInt64((seconds + 2) * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.player.stop()
            played.end()
        }
        Self.schedule(end, on: player) { played.end() }
        await played.wait()
        watchdog.cancel()
    }

    // Nonisolated because the player calls back on its own thread.
    private nonisolated static func schedule(_ buffer: AVAudioPCMBuffer, on player: AVAudioPlayerNode,
                                             then done: @escaping @Sendable () -> Void) {
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { _ in done() }
    }
}

/// A wait that whichever comes first can end, and the other finds already over.
private final class OneWait: @unchecked Sendable {
    private let lock = NSLock()
    private var isOver = false
    private var waiting: CheckedContinuation<Void, Never>?

    func wait() async {
        await withCheckedContinuation { continuation in
            lock.withLock {
                if isOver { continuation.resume() } else { waiting = continuation }
            }
        }
    }

    func end() {
        lock.withLock {
            isOver = true
            waiting?.resume()
            waiting = nil
        }
    }
}

@MainActor
final class SystemSpeaker: NSObject, Speaker, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var currentUtterance: AVSpeechUtterance?
    private var completion: CheckedContinuation<Void, Never>?
    private var waitingID: UUID?
    private var language = Locale.preferredLanguages.first

    override init() {
        super.init()
        synthesizer.delegate = self
        synthesizer.usesApplicationAudioSession = true
    }

    func prepare(client: ServerClient, journal: SharedJournal?) { language = client.settings.userLang }

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
        utterance.voice = AVSpeechSynthesisVoice(language: language)
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
