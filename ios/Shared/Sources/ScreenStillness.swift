import Foundation

/// Tells when the screen has stopped changing. A hint for a screen in motion is out of date when it arrives.
public struct ScreenStillness {
    public static let stillFor: TimeInterval = 1.5
    public static let longestWait: TimeInterval = 5  // A video on the page never stops.
    public static let threshold = 0.008  // A blinking cursor stays under it.

    private var last: [UInt8]?
    private var stillSince: TimeInterval = 0
    private var waitingSince: TimeInterval?

    public init() {}

    /// Takes the newest small picture and says whether it is worth a look. Time is in seconds on any one clock.
    public mutating func see(_ picture: [UInt8], at time: TimeInterval) -> Bool {
        if let last, Self.difference(last, picture) < Self.threshold {} else { stillSince = time }
        last = picture
        let started = waitingSince ?? time
        let settled = time - stillSince >= Self.stillFor || time - started >= Self.longestWait
        waitingSince = settled ? nil : started
        return settled
    }

    public mutating func forget() {
        last = nil
        waitingSince = nil
    }

    /// The mean change per pixel, from 0 to 1.
    public static func difference(_ left: [UInt8], _ right: [UInt8]) -> Double {
        guard left.count == right.count, !left.isEmpty else { return 1 }
        let sum = zip(left, right).reduce(0) { $0 + abs(Int($1.0) - Int($1.1)) }
        return Double(sum) / Double(left.count * 255)
    }
}
