@preconcurrency import AVFAudio

/// Turns what the microphone hears into phrases. The microphone calls it on its own thread.
final class PhraseListener: @unchecked Sendable {
    let phrases: AsyncStream<Phrase>
    private let output: AsyncStream<Phrase>.Continuation
    private let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: Double(SpeechDetector.sampleRate),
                                       channels: 1, interleaved: true)!
    private let lock = NSLock()
    private var detector = SpeechDetector()
    private var converter: AVAudioConverter?
    private var deafUntil = Date.distantPast

    init() {
        // A phrase waits while the last one is answered. Only the newest does: it is what the person means now.
        (phrases, output) = AsyncStream.makeStream(of: Phrase.self, bufferingPolicy: .bufferingNewest(1))
    }

    deinit { output.finish() }

    func hear(_ buffer: AVAudioPCMBuffer) {
        lock.withLock {
            let samples = convert(buffer)
            if Date() < deafUntil {
                if let phrase = detector.finish() { output.yield(phrase) }
            } else {
                for phrase in detector.add(samples) { output.yield(phrase) }
            }
        }
    }

    /// There is no echo filter, so the microphone must not take the agent's voice for the person's.
    func deafen(until date: Date) {
        lock.withLock { deafUntil = date }
    }

    func forget() {
        lock.withLock {
            detector = SpeechDetector()
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
