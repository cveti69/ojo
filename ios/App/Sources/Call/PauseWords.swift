import Foundation

/// What the agent says into the pause while it thinks. None comes again until the others of its kind were said.
struct PauseWords {
    static let gap: TimeInterval = 8

    private var phrases: Shuffled
    private var sounds: Shuffled
    private var lastSaid = Date.distantPast
    private var soundThisTurn = false
    private var soundLastTurn = false

    init(phrases: [String], sounds: [String]) {
        self.phrases = Shuffled(phrases)
        self.sounds = Shuffled(sounds)
    }

    mutating func next(now: Date = Date()) -> String? {
        guard now.timeIntervalSince(lastSaid) >= Self.gap, let phrase = phrases.next() else { return nil }
        lastSaid = now
        return phrase
    }

    /// A turn right after a phrase gets its own once this is over.
    func gapLeft(now: Date = Date()) -> TimeInterval {
        Self.gap - now.timeIntervalSince(lastSaid)
    }

    mutating func newTurn() {
        soundLastTurn = soundThisTurn
        soundThisTurn = false
    }

    /// One on a turn, and never on two in a row. Only if it is on the phone already: a late one is worse than none.
    /// The next phrase owes it nothing.
    mutating func sound(isReady: (String) -> Bool) -> String? {
        guard !soundThisTurn, !soundLastTurn, let sound = sounds.next(where: isReady) else { return nil }
        soundThisTurn = true
        return sound
    }

    /// About one wait in three.
    mutating func soundBeforeAnswer(isReady: (String) -> Bool) -> String? {
        guard Int.random(in: 0..<3) == 0 else { return nil }
        return sound(isReady: isReady)
    }
}

private struct Shuffled {
    private let all: [String]
    private var left: [String] = []
    private var last: String?

    init(_ all: [String]) {
        self.all = all
    }

    mutating func next(where isReady: (String) -> Bool = { _ in true }) -> String? {
        if !left.contains(where: isReady) { left = all.shuffled() }
        guard let index = left.firstIndex(where: { isReady($0) && $0 != last }) ?? left.firstIndex(where: isReady) else { return nil }
        last = left[index]
        return left.remove(at: index)
    }
}

enum KeptSound {
    private static let loud = 200  // About -44 dB. Below this the voice is over.
    private static let margin = 240  // 10 ms at 24 kHz, for the word to fade by itself.

    /// The voice leaves silence around a word, and through it the microphone is deaf and the answer waits.
    static func withoutSilence(_ sound: Data) -> Data {
        let samples = sound.count / 2
        let isLoud = { (index: Int) in
            let sample = sound.withUnsafeBytes { Int16(littleEndian: $0.loadUnaligned(fromByteOffset: index * 2, as: Int16.self)) }
            return abs(Int(sample)) > loud
        }
        guard let first = (0..<samples).first(where: isLoud), let last = (0..<samples).last(where: isLoud) else { return sound }
        return sound.subdata(in: sound.startIndex + max(first - margin, 0) * 2..<sound.startIndex + min(last + margin * 2, samples) * 2)
    }
}
