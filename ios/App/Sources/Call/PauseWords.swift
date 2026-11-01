import Foundation

/// Short words for the pause while the agent thinks. None comes again until the others were said.
struct PauseWords {
    static let gap: TimeInterval = 8

    private let all: [String]
    private var left: [String] = []
    private var last: String?
    private var lastSaid = Date.distantPast

    init(_ all: [String]) {
        self.all = all
    }

    mutating func next(now: Date = Date()) -> String? {
        guard now.timeIntervalSince(lastSaid) >= Self.gap else { return nil }
        if left.isEmpty {
            left = all.shuffled()
            if left[0] == last { left.swapAt(0, left.count - 1) }
        }
        let word = left.removeFirst()
        last = word
        lastSaid = now
        return word
    }
}
