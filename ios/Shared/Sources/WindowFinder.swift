import Foundation

/// Finds our floating window on a small picture of the screen by its coloured border, the way `window.py` does on the server.
public enum WindowFinder {
    public static let longSide = 400  // The border of the smallest window is still two pixels wide here.
    private static let hotMargin = 45
    private static let minSide = 31  // Out of 255, as on the server.
    private static let maxThickness = 0.03
    private static let minFilled = 0.6
    private static let maxInside = 0.25
    private static let ratio = 0.6...0.9

    /// `pixels` are RGBA, four bytes each, row after row. Returns the window's outer edges as a patch of a square picture.
    public static func find(_ pixels: [UInt8], width: Int, height: Int, side: Int) -> ScreenPatch? {
        guard width > 0, height > 0, pixels.count >= width * height * 4 else { return nil }
        var hot = [Int32](repeating: 0, count: (width + 1) * (height + 1))  // Hot pixels above and to the left of each point.
        var columns = [Int32](repeating: 0, count: width)
        var rows = [Int32](repeating: 0, count: height)
        for y in 0..<height {
            var inRow: Int32 = 0
            for x in 0..<width {
                let at = (y * width + x) * 4
                let green = Int(pixels[at + 1])
                if min(Int(pixels[at]) - green, Int(pixels[at + 2]) - green) >= hotMargin {
                    inRow += 1
                    columns[x] += 1
                }
                hot[(y + 1) * (width + 1) + x + 1] = hot[y * (width + 1) + x + 1] + inRow
            }
            rows[y] = inRow
        }
        func share(_ x1: Int, _ y1: Int, _ x2: Int, _ y2: Int) -> Double {
            guard x2 > x1, y2 > y1 else { return 0 }
            let line = width + 1
            let sum = hot[y2 * line + x2] - hot[y1 * line + x2] - hot[y2 * line + x1] + hot[y1 * line + x1]
            return Double(sum) / Double((x2 - x1) * (y2 - y1))
        }
        let thickest = Int((Double(max(width, height)) * maxThickness).rounded())
        let across = bands(columns, length: height, thickest: thickest)
        let down = bands(rows, length: width, thickest: thickest)
        for (index, left) in across.enumerated() {
            for right in across[(index + 1)...] {
                for (other, top) in down.enumerated() {
                    for bottom in down[(other + 1)...] {
                        let (x1, x2, y1, y2) = (left.0, right.1, top.0, bottom.1)
                        guard ratio.contains(Double(x2 - x1) / Double(y2 - y1)),
                              share(x1, y1, left.1, y2) >= minFilled, share(right.0, y1, x2, y2) >= minFilled,
                              share(x1, y1, x2, top.1) >= minFilled, share(x1, bottom.0, x2, y2) >= minFilled,
                              share(left.1, top.1, right.0, bottom.0) <= maxInside else { continue }
                        return ScreenPatch(left: Double(x1) / Double(width), top: Double(y1) / Double(height),
                                           right: Double(x2) / Double(width), bottom: Double(y2) / Double(height), side: side)
                    }
                }
            }
        }
        return nil
    }

    /// Runs of columns or rows with enough border in them.
    private static func bands(_ profile: [Int32], length: Int, thickest: Int) -> [(Int, Int)] {
        var found: [(Int, Int)] = []
        var start: Int?
        for index in 0...profile.count {
            let isFull = index < profile.count && Int(profile[index]) * 255 / length >= minSide
            if isFull, start == nil {
                start = index
            } else if !isFull, let first = start {
                if index - first <= thickest { found.append((first, index)) }
                start = nil
            }
        }
        return found
    }
}
