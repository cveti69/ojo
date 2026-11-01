import Foundation

/// A line in sentences, so that it can end between two of them. A piece too short to stand alone stays with its neighbour.
public enum Sentences {
    private static let shortest = 12
    private static let ends: Set<Character> = [".", "!", "?", "…"]

    public static func of(_ text: String) -> [String] {
        var parts: [String] = []
        var part = ""
        for word in text.split(whereSeparator: \.isWhitespace) {
            part += (part.isEmpty ? "" : " ") + word
            if let last = word.last, ends.contains(last), part.count >= shortest {
                parts.append(part)
                part = ""
            }
        }
        if part.isEmpty { return parts }
        if parts.isEmpty || part.count >= shortest { return parts + [part] }
        parts[parts.count - 1] += " " + part
        return parts
    }
}
