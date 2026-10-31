@preconcurrency import AVFAudio

/// Turns what the microphone hears into phrases. The microphone calls it on its own thread.
final class PhraseListener: @unchecked Sendable {
    let phrases: AsyncStream<Phrase>
    let sound: AsyncStream<[Int16]>  // The phrase under way, for the stream to the server.
    private let output: AsyncStream<Phrase>.Continuation
    private let soundOutput: AsyncStream<[Int16]>.Continuation
    private let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: Double(SpeechDetector.sampleRate),
                                       channels: 1, interleaved: true)!
    private let lock = NSLock()
    private var detector = SpeechDetector()
    private var converter: AVAudioConverter?
    private var deafUntil = Date.distantPast
    private var echoPeak: Float?
    private var measure: (@Sendable (AVAudioPCMBuffer, Bool) -> Void)?

    /// Gets the same sound as the phrase detector, for the record only.
    func measure(with handler: (@Sendable (AVAudioPCMBuffer, Bool) -> Void)?) {
        lock.withLock { measure = handler }
    }

    init() {
        // A phrase waits while the last one is answered. Only the newest does: it is what the person means now.
        (phrases, output) = AsyncStream.makeStream(of: Phrase.self, bufferingPolicy: .bufferingNewest(1))
        (sound, soundOutput) = AsyncStream.makeStream(of: [Int16].self, bufferingPolicy: .bufferingNewest(100))
    }

    deinit {
        output.finish()
        soundOutput.finish()
    }

    /// The person is talking now. Heard from another thread, so the voice can wait for them to finish.
    var isHearing: Bool { lock.withLock { detector.isHearing } }
    var isTalking: Bool { lock.withLock { detector.isTalking } }

    /// When the last loud sound was heard, which is about when the person stopped.
    var lastVoice: Date { lock.withLock { detector.lastLoud } }

    /// How many sounds were too short to be speech, and the loudest of them.
    var dropped: (count: Int, loudest: Float) { lock.withLock { (detector.droppedSounds, detector.loudestDropped) } }

    func hear(_ buffer: AVAudioPCMBuffer) {
        lock.withLock {
            let samples = convert(buffer)
            if Date() < deafUntil {
                if let phrase = detector.finish() { output.yield(phrase) }
            } else {
                for phrase in detector.add(samples) { output.yield(phrase) }
            }
            let fresh = detector.takeFresh()
            if !fresh.isEmpty { soundOutput.yield(fresh) }
            echoPeak = echoPeak.map { max($0, detector.lastLevel) }
            measure?(buffer, detector.isHearing)
        }
    }

    /// Starts counting what reaches the microphone over the agent's voice. Stopping returns the loudest of it.
    func watchEcho(_ isWatching: Bool) -> Float? {
        lock.withLock {
            defer { echoPeak = isWatching ? -160 : nil }
            return echoPeak
        }
    }

    /// There is no echo filter, so the microphone must not take the agent's voice for the person's.
    func deafen(until date: Date) {
        lock.withLock { deafUntil = date }
    }

    func forget() {
        lock.withLock {
            let dropped = (detector.droppedSounds, detector.loudestDropped)
            detector = SpeechDetector()
            detector.keepDropped(dropped.0, loudest: dropped.1)
            converter = nil
        }
    }

    private func convert(_ buffer: AVAudioPCMBuffer) -> [Int16] {
        if converter?.inputFormat != buffer.format { converter = AVAudioConverter(from: buffer.format, to: target) }
        let capacity = (Double(buffer.frameLength) * target.sampleRate / buffer.format.sampleRate).rounded(.up) + 32
        guard let converter, let converted = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: AVAudioFrameCount(capacity)) else {
            return []
        }
        var isGiven = false
        var failure: NSError?
        converter.convert(to: converted, error: &failure) { _, status in
            guard !isGiven else {
                status.pointee = .noDataNow  // More sound will come, so the converter keeps its state.
                return nil
            }
            isGiven = true
            status.pointee = .haveData
            return buffer
        }
        guard failure == nil, let samples = converted.int16ChannelData?[0] else { return [] }
        return Array(UnsafeBufferPointer(start: samples, count: Int(converted.frameLength)))
    }
}
