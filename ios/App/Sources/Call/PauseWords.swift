import Foundation

/// Short words for the pauses while the agent works. None comes again until the others were said, and they stay rare.
struct PauseWords {
    enum Kind: CaseIterable { case promise, stillLooking, reading, stillReading, comparing, stillComparing, thinking, looking }

    private static let gap: TimeInterval = 15

    private var left: [Kind: [String]] = [:]
    private var last: String?
    private var lastSaid = Date.distantPast

    /// The ones needed first go first, as they are fetched one by one.
    static var all: [String] {
        Kind.allCases.flatMap(words).reduce(into: []) { if !$0.contains($1) { $0.append($1) } }
    }

    /// While the head works: a word to start with, and one more if it takes a while.
    static func waiting(for task: String?) -> (first: Kind, second: Kind) {
        switch task {
        case "read": return (.reading, .stillReading)
        case "compare": return (.comparing, .stillComparing)
        default: return (.promise, .stillLooking)
        }
    }

    private static func words(_ kind: Kind) -> [String] {
        switch kind {
        case .thinking:
            return ["Сейчас подумаю.", "Секунду.", "Так, дай подумать.", "Минутку.", "Сейчас скажу.", "Погоди секунду."]
        case .looking:
            return ["Изучаю экран.", "Сейчас посмотрю.", "Так, смотрю.", "Секунду, разбираюсь.", "Смотрю, что тут.", "Так, новый экран."]
        case .promise:
            return ["Сейчас гляну.", "Ага, смотрю.", "Секунду, посмотрю.", "Так, гляну.", "Момент, смотрю.", "Давай посмотрим."]
        case .reading:
            return ["Сейчас прочитаю.", "Ага, читаю.", "Так, переведу.", "Секунду, прочту."]
        case .comparing:
            return ["Сейчас сравню.", "Давай сравним.", "Так, сравню.", "Ага, сравниваю."]
        case .stillLooking:
            return ["Угу.", "Так, смотрю.", "Ещё секунду.", "Почти всё."]
        case .stillReading:
            return ["Ага, перевожу.", "Угу.", "Так, читаю.", "Ещё секунду."]
        case .stillComparing:
            return ["Сравниваю.", "Угу.", "Так, взвешиваю.", "Ещё секунду."]
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
