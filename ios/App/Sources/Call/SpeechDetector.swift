import Foundation

/// One thing the person said: 16 kHz, 16-bit mono, the format the server hears.
struct Phrase: Sendable {
    let samples: [Int16]
    let noiseFloor: Float
    let loudest: Float

    var seconds: Double { Double(samples.count) / Double(SpeechDetector.sampleRate) }
    var wav: Data { WAV.file(samples: samples, sampleRate: UInt32(SpeechDetector.sampleRate)) }
}

enum WAV {
    static func file(samples: [Int16], sampleRate: UInt32) -> Data {
        let byteCount = UInt32(samples.count * 2)
        var data = Data("RIFF".utf8)
        func number<T: FixedWidthInteger>(_ value: T) {
            var value = value.littleEndian
            withUnsafeBytes(of: &value) { data.append(contentsOf: $0) }
        }
        number(byteCount + 36)
        data.append(contentsOf: "WAVEfmt ".utf8)
        number(UInt32(16))
        number(UInt16(1))
        number(UInt16(1))
        number(sampleRate)
        number(sampleRate * 2)
        number(UInt16(2))
        number(UInt16(16))
        data.append(contentsOf: "data".utf8)
        number(byteCount)
        for sample in samples { number(sample) }
        return data
    }
}

/// Finds phrases in the endless sound of a call. "Loud" means loud for this room, so it works in a quiet flat and on a street.
struct SpeechDetector {
    static let sampleRate = 16_000
    private static let blockSize = sampleRate / 10  // The level is read every 100 ms.
    private static let speechMargin: Float = 10  // Speech stands this many dB above the room.
    private static let quietestSpeech: Float = -50
    private static let floorRise: Float = 0.1  // dB per block, so a long phrase never becomes the new silence.
    private static let leadBlocks = 5  // A word starts before it is loud enough to notice.
    private static let pauseBlocks = 8  // This much quiet ends a phrase.
    private static let tailBlocks = 2  // The quiet kept after the last word.
    private static let shortestSpeech = 3  // A tap on the phone is shorter than this many blocks.
    private static let longestPhrase = 150

    private var pending: [Int16] = []
    private var lead: [[Int16]] = []
    private var blocks: [[Int16]] = []
    private var speechBlocks = 0
    private var quietBlocks = 0
    private var noiseFloor: Float?
    private var loudest: Float = -160
    private var fresh: [Int16] = []
    private(set) var droppedSounds = 0
    private(set) var loudestDropped: Float = -160
    private(set) var lastLoud = Date.distantPast
    private(set) var lastLevel: Float = -160

    /// The count goes on when the microphone restarts.
    mutating func keepDropped(_ count: Int, loudest: Float) {
        droppedSounds = count
        loudestDropped = loudest
    }

    /// A phrase is under way, even in the quiet that may yet end it.
    var isHearing: Bool { !blocks.isEmpty }

    /// Long enough to be a person and not a click.
    var isTalking: Bool { speechBlocks >= Self.shortestSpeech }

    /// The sound of the phrase under way since the last call, ready to stream.
    mutating func takeFresh() -> [Int16] {
        defer { fresh = [] }
        return fresh
    }

    mutating func add(_ samples: [Int16]) -> [Phrase] {
        pending += samples
        var phrases: [Phrase] = []
        var start = 0
        while pending.count - start >= Self.blockSize {
            if let phrase = take(Array(pending[start..<start + Self.blockSize])) { phrases.append(phrase) }
            start += Self.blockSize
        }
        pending.removeFirst(start)
        return phrases
    }

    /// Ends what was heard so far, for when the agent starts talking over it.
    mutating func finish() -> Phrase? {
        defer {
            lead = []
            blocks = []
            speechBlocks = 0
            quietBlocks = 0
            loudest = -160
        }
        guard speechBlocks >= Self.shortestSpeech else {
            if speechBlocks > 0 {
                droppedSounds += 1
                loudestDropped = max(loudestDropped, loudest)
            }
            return nil
        }
        let kept = blocks.dropLast(max(quietBlocks - Self.tailBlocks, 0))
        return Phrase(samples: Array(kept.joined()), noiseFloor: noiseFloor ?? -160, loudest: loudest)
    }

    private mutating func take(_ block: [Int16]) -> Phrase? {
        let level = Self.level(of: block)
        lastLevel = level
        var isLoud = false
        if level > -120 {  // Lower means the voice filter muted the sound, which says nothing about the room.
            let floor = noiseFloor ?? level
            noiseFloor = min(level, floor + Self.floorRise)
            isLoud = level > max(floor + Self.speechMargin, Self.quietestSpeech)
        }
        guard isLoud || !blocks.isEmpty else {
            lead.append(block)
            if lead.count > Self.leadBlocks { lead.removeFirst() }
            return nil
        }
        if blocks.isEmpty {
            swap(&blocks, &lead)
            fresh += blocks.joined()
        }
        blocks.append(block)
        fresh += block
        if isLoud {
            speechBlocks += 1
            quietBlocks = 0
            loudest = max(loudest, level)
            lastLoud = Date()
        } else {
            quietBlocks += 1
        }
        return quietBlocks >= Self.pauseBlocks || blocks.count >= Self.longestPhrase ? finish() : nil
    }

    private static func level(of block: [Int16]) -> Float {
        var power: Float = 0
        for sample in block {
            let value = Float(sample) / 32768
            power += value * value
        }
        power /= Float(block.count)
        return power > 0 ? 10 * log10(power) : -160
    }
}
