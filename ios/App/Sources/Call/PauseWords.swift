import Foundation

/// Short words for the pauses while the agent works. None comes again until the others were said, and they stay rare.
struct PauseWords {
    enum Kind: CaseIterable { case thinking, looking, promise }

    private static let gap: TimeInterval = 15

    private var left: [Kind: [String]] = [:]
    private var last: String?
    private var lastSaid = Date.distantPast

    static var all: [String] { Kind.allCases.flatMap(words) }

    private static func words(_ kind: Kind) -> [String] {
        switch kind {
        case .thinking:
            return ["Сейчас подумаю.", "Секунду.", "Так, дай подумать.", "Минутку.", "Сейчас скажу.", "Погоди секунду."]
        case .looking:
            return ["Изучаю экран.", "Сейчас посмотрю.", "Так, смотрю.", "Секунду, разбираюсь.", "Смотрю, что тут.", "Так, новый экран."]
        case .promise:
            return ["Сейчас гляну.", "Сейчас посмотрю.", "Секунду, гляну.", "Так, смотрю.", "Дай гляну.", "Смотрю."]
        }
    }

    mutating func next(_ kind: Kind, now: Date = Date()) -> String? {
        guard now.timeIntervalSince(lastSaid) >= Self.gap else { return nil }
        return any(kind, now: now)
    }

    /// A word that answers the person, so it comes however recent the last one was.
    mutating func any(_ kind: Kind, now: Date = Date()) -> String {
        var words = left[kind] ?? []
        if words.isEmpty {
            words = Self.words(kind).shuffled()
            if words[0] == last { words.swapAt(0, words.count - 1) }
        }
        let word = words.removeFirst()
        left[kind] = words
        last = word
        lastSaid = now
        return word
    }
}
