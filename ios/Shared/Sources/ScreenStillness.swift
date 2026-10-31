import Foundation

/// The part of a small square picture that does not count: our own window, whose picture moves.
public struct ScreenPatch: Equatable, Sendable {
    public let side: Int
    public let left: Int
    public let top: Int
    public let right: Int
    public let bottom: Int

    /// The edges are in 0-1 coordinates. The patch takes a little more on each side, as the small picture smears the edge.
    public init(left: Double, top: Double, right: Double, bottom: Double, side: Int) {
        let margin = max(1, side / 64)
        self.side = side
        self.left = max(0, Int((left * Double(side)).rounded(.down)) - margin)
        self.top = max(0, Int((top * Double(side)).rounded(.down)) - margin)
        self.right = min(side, Int((right * Double(side)).rounded(.up)) + margin)
        self.bottom = min(side, Int((bottom * Double(side)).rounded(.up)) + margin)
    }

    func covers(_ index: Int) -> Bool {
        let (y, x) = index.quotientAndRemainder(dividingBy: side)
        return left <= x && x < right && top <= y && y < bottom
    }
}

/// Tells when the screen has stopped changing. A hint for a screen in motion is out of date when it arrives.
public struct ScreenStillness {
    public static let stillFor: TimeInterval = 1.5
    public static let longestWait: TimeInterval = 5  // A video on the page never stops.
    public static let threshold = 0.008  // A blinking cursor stays under it.
    public static let smallChange = 8  // Pixels of a 256 picture: one digit of a price changes 10, a thin check mark 12.
    private static let quietFor: TimeInterval = 1.2  // A blinking cursor and a countdown never stay still this long, a new check mark does.
    private static let statusBar = 0.07  // Its clock ticks by itself, so this share of the picture is left out of small changes.
    private static let pixelChanged = 12  // As on the server.

    public private(set) var waited: TimeInterval = 0  // How long the newest settled picture took to settle.
    public private(set) var isQuiet = false  // Not even a small change for a while.
    private var last: [UInt8]?
    private var stillSince: TimeInterval = 0
    private var quietSince: TimeInterval = 0
    private var waitingSince: TimeInterval?

    public init() {}

    /// Takes the newest small picture and says whether it is worth a look. Time is in seconds on any one clock.
    public mutating func see(_ picture: [UInt8], at time: TimeInterval, outside patch: ScreenPatch? = nil) -> Bool {
        if let last, Self.difference(last, picture, outside: patch) < Self.threshold {} else { stillSince = time }
        if let last, Self.changedCells(last, picture, outside: patch) < Self.smallChange {} else { quietSince = time }
        isQuiet = time - quietSince >= Self.quietFor
        last = picture
        let started = waitingSince ?? time
        let settled = time - stillSince >= Self.stillFor || time - started >= Self.longestWait
        waitingSince = settled ? nil : started
        waited = time - started
        return settled
    }

    public mutating func forget() {
        last = nil
        waitingSince = nil
    }

    /// The mean change per pixel, from 0 to 1. What the patch covers counts as unchanged.
    public static func difference(_ left: [UInt8], _ right: [UInt8], outside patch: ScreenPatch? = nil) -> Double {
        guard left.count == right.count, !left.isEmpty else { return 1 }
        var sum = 0
        for index in left.indices where patch?.covers(index) != true { sum += abs(Int(left[index]) - Int(right[index])) }
        return Double(sum) / Double(left.count * 255)
    }

    /// How many pixels changed below the status bar.
    public static func changedCells(_ left: [UInt8], _ right: [UInt8], outside patch: ScreenPatch? = nil) -> Int {
        guard left.count == right.count else { return left.count }
        var changed = 0
        for index in Int(Double(left.count) * statusBar)..<left.count
        where patch?.covers(index) != true && abs(Int(left[index]) - Int(right[index])) > pixelChanged {
            changed += 1
        }
        return changed
    }

    /// The share of pixels that changed, from 0 to 1. A banner or a video on the page stay under a new screen by this measure.
    public static func changedShare(_ left: [UInt8], _ right: [UInt8], outside patch: ScreenPatch? = nil) -> Double {
        guard left.count == right.count, !left.isEmpty else { return 1 }
        var changed = 0
        for index in left.indices where patch?.covers(index) != true && abs(Int(left[index]) - Int(right[index])) > pixelChanged {
            changed += 1
        }
        return Double(changed) / Double(left.count)
    }
}
