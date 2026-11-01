import CoreGraphics
import Foundation

/// What the drawing inside the border is doing. With everything at zero it stands still, and the phone draws nothing.
struct EyesFrameMotion: Equatable {
    private static let speed = 5.0  // Per second: a change of state takes about 0.2 s.
    private static let levelSpeed = 8.0
    private static let gazeSpeed = 7.0
    private static let stirSpeed = 8.0  // Quick enough to follow the hop, and to end it when someone starts to talk.

    var speak = 0.0
    var think = 0.0
    var look = 0.0
    var level = 0.0  // The voice, from 0 to 1.
    var gaze = 0.0  // Where the eyes look while they think or read: -1 is left, 1 is right.
    var time = 0.0
    var stir = 0.0  // One detail off its place, from 0 to 1, in a rare moment of rest.
    var place = 0.0  // Where on the ring it is: 0 is the top left, and it goes clockwise.

    /// True while the picture changes, the step that brings it to rest included: that still picture has to go out too.
    mutating func settle(by passed: TimeInterval, mood: EyesMood, level heard: Double, gaze looking: Double, stir stirred: Double = 0, place spot: Double = 0) -> Bool {
        let before = self
        speak = EyesDirector.approach(speak, mood == .speaking ? 1 : 0, by: passed * Self.speed)
        think = EyesDirector.approach(think, mood == .thinking ? 1 : 0, by: passed * Self.speed)
        look = EyesDirector.approach(look, mood == .reading ? 1 : 0, by: passed * Self.speed)
        level = EyesDirector.approach(level, mood == .listening ? heard : 0, by: passed * Self.levelSpeed)
        let seeks = mood == .thinking || mood == .reading
        gaze = EyesDirector.approach(gaze, seeks ? looking : 0, by: passed * Self.gazeSpeed)
        time = speak + think + look + level + abs(gaze) > 0 ? time + passed : 0
        stir = EyesDirector.approach(stir, stirred, by: passed * Self.stirSpeed)
        if stirred > 0 { place = spot } else if stir == 0 { place = 0 }
        return self != before
    }
}

/// The drawing between the border and the eyes. The border tells the search which part of a screen is ours, so nothing here may pass
/// for it: red stays close to green, and min(R − G, B − G) of every pixel stays at 30 or below, under the search's 45.
/// The context has its origin at the top left, and the surface draws the border over all this.
enum EyesFrame: String, CaseIterable, Sendable {
    case lace, glow, ticks

    private typealias Ink = (red: Double, green: Double, blue: Double)
    private struct Scallop {
        let center: CGPoint
        let radius: CGFloat
        let turn: CGFloat
    }
    private struct Tick {
        let start: CGPoint
        let inward: CGVector
    }

    private static let inside = CGRect(x: 25, y: 25, width: 670, height: 910)
    private static let deep: Ink = (30, 24, 76)
    private static let iris: Ink = (108, 92, 255)
    private static let glowTone: Ink = (52, 44, 168)
    private static let glowDepth: CGFloat = 136
    private static let flareReach: CGFloat = 150
    private static let tickWidth: CGFloat = 9

    private static let scallops: [Scallop] = {
        let (across, down) = (8, 11)
        let (wide, tall) = (inside.width / CGFloat(across), inside.height / CGFloat(down))
        let top = (0..<across).map { Scallop(center: CGPoint(x: inside.minX + wide * (CGFloat($0) + 0.5), y: inside.minY), radius: wide / 2, turn: 0) }
        let right = (0..<down).map { Scallop(center: CGPoint(x: inside.maxX, y: inside.minY + tall * (CGFloat($0) + 0.5)), radius: tall / 2, turn: .pi / 2) }
        let bottom = (0..<across).map { Scallop(center: CGPoint(x: inside.maxX - wide * (CGFloat($0) + 0.5), y: inside.maxY), radius: wide / 2, turn: .pi) }
        let left = (0..<down).map { Scallop(center: CGPoint(x: inside.minX, y: inside.maxY - tall * (CGFloat($0) + 0.5)), radius: tall / 2, turn: -.pi / 2) }
        return top + right + bottom + left
    }()

    private static let marks: [Tick] = {
        let (gap, step, clear): (CGFloat, CGFloat, CGFloat) = (14, 35, 96)
        let (wide, tall) = (inside.width - 2 * clear, inside.height - 2 * clear)
        let (across, down) = (Int((wide / step).rounded()), Int((tall / step).rounded()))
        let along = { (index: Int, count: Int, length: CGFloat) in length * (CGFloat(index) + 0.5) / CGFloat(count) }
        let top = (0..<across).map { Tick(start: CGPoint(x: inside.minX + clear + along($0, across, wide), y: inside.minY + gap), inward: CGVector(dx: 0, dy: 1)) }
        let right = (0..<down).map { Tick(start: CGPoint(x: inside.maxX - gap, y: inside.minY + clear + along($0, down, tall)), inward: CGVector(dx: -1, dy: 0)) }
        let bottom = (0..<across).map { Tick(start: CGPoint(x: inside.maxX - clear - along($0, across, wide), y: inside.maxY - gap), inward: CGVector(dx: 0, dy: -1)) }
        let left = (0..<down).map { Tick(start: CGPoint(x: inside.minX + gap, y: inside.maxY - clear - along($0, down, tall)), inward: CGVector(dx: 1, dy: 0)) }
        return top + right + bottom + left
    }()

    /// Which way the eyes go to see that place on the ring.
    static func direction(of place: Double) -> (x: Double, y: Double) {
        let spot = spot(at: place)
        let (x, y) = (Double((spot.x - inside.midX) / (inside.width / 2)), Double((spot.y - inside.midY) / (inside.height / 2)))
        let reach = 0.85 / max(1, hypot(x, y))
        return (x * reach, y * reach)
    }

    private static func spot(at place: Double) -> CGPoint {
        var along = CGFloat(place) * 2 * (inside.width + inside.height)
        if along < inside.width { return CGPoint(x: inside.minX + along, y: inside.minY) }
        along -= inside.width
        if along < inside.height { return CGPoint(x: inside.maxX, y: inside.minY + along) }
        along -= inside.height
        if along < inside.width { return CGPoint(x: inside.maxX - along, y: inside.maxY) }
        return CGPoint(x: inside.minX, y: inside.maxY - (along - inside.width))
    }

    func draw(_ motion: EyesFrameMotion, on context: CGContext) {
        context.saveGState()
        defer { context.restoreGState() }
        switch self {
        case .lace: Self.drawLace(motion, on: context)
        case .glow: Self.drawGlow(motion, on: context)
        case .ticks: Self.drawTicks(motion, on: context)
        }
    }

    private static func drawLace(_ motion: EyesFrameMotion, on context: CGContext) {
        let count = Double(scallops.count)
        let walker = (motion.time * 7).truncatingRemainder(dividingBy: count)
        let stirred = Int(motion.place * count)
        for (index, scallop) in scallops.enumerated() {
            let place = Double(index)
            let hop = index == stirred ? motion.stir : 0
            let lift = scallop.radius * CGFloat(hop)  // It leaves the row, straight inwards.
            let swell = motion.speak * 0.34 * wave(place * 2 * .pi / 9 - motion.time * 5)
                + motion.level * 0.3 * wave(place * 2.4 + motion.time * 9)
                + (motion.think + motion.look) * 0.3 * closeness(of: place, to: walker, among: count, within: 2.5)
            let center = CGPoint(x: scallop.center.x - sin(scallop.turn) * lift, y: scallop.center.y + cos(scallop.turn) * lift)
            context.addArc(center: center, radius: scallop.radius * CGFloat(1 + swell), startAngle: scallop.turn - 0.04,
                           endAngle: scallop.turn + .pi + 0.04, clockwise: false)
            context.closePath()
            let share = min(1, 0.52 + swell * 1.4 + hop * 0.48)
            context.setFillColor(red: CGFloat((deep.red + (iris.red - deep.red) * share) / 255),
                                 green: CGFloat((deep.green + (iris.green - deep.green) * share) / 255),
                                 blue: CGFloat((deep.blue + (iris.blue - deep.blue) * share) / 255), alpha: 1)
            context.fillPath()
        }
    }

    private static func drawGlow(_ motion: EyesFrameMotion, on context: CGContext) {
        let color = [CGFloat(glowTone.red / 255), CGFloat(glowTone.green / 255), CGFloat(glowTone.blue / 255)]
        guard let gradient = CGGradient(colorSpace: CGColorSpaceCreateDeviceRGB(), colorComponents: color + [1] + color + [0],
                                        locations: [0, 1], count: 2) else { return }
        let strength = 0.46 + 0.42 * motion.level + 0.3 * motion.speak * wave(motion.time * 5.2)
        let lean = motion.gaze * 1.4
        let left = strength * (1 - motion.think * 0.6 * max(0, min(1, lean)))  // Looking one way, the far side dims.
        let right = strength * (1 + motion.think * 0.6 * max(-1, min(0, lean)))
        let band = { (start: CGPoint, end: CGPoint, area: CGRect, alpha: Double) in
            context.saveGState()
            context.clip(to: area)
            context.setAlpha(CGFloat(min(1, alpha)))
            context.drawLinearGradient(gradient, start: start, end: end, options: [])
            context.restoreGState()
        }
        let depth = glowDepth
        band(CGPoint(x: 0, y: inside.minY), CGPoint(x: 0, y: inside.minY + depth),
             CGRect(x: inside.minX, y: inside.minY, width: inside.width, height: depth), strength)
        band(CGPoint(x: 0, y: inside.maxY), CGPoint(x: 0, y: inside.maxY - depth),
             CGRect(x: inside.minX, y: inside.maxY - depth, width: inside.width, height: depth), strength)
        band(CGPoint(x: inside.minX, y: 0), CGPoint(x: inside.minX + depth, y: 0),
             CGRect(x: inside.minX, y: inside.minY, width: depth, height: inside.height), left)
        band(CGPoint(x: inside.maxX, y: 0), CGPoint(x: inside.maxX - depth, y: 0),
             CGRect(x: inside.maxX - depth, y: inside.minY, width: depth, height: inside.height), right)
        let ink = [CGFloat(iris.red / 255), CGFloat(iris.green / 255), CGFloat(iris.blue / 255)]
        guard motion.stir > 0, let flare = CGGradient(colorSpace: CGColorSpaceCreateDeviceRGB(), colorComponents: ink + [1] + ink + [0],
                                                      locations: [0, 1], count: 2) else { return }
        let spot = spot(at: motion.place)  // A patch of the light flares up and goes out.
        context.clip(to: inside)
        context.setAlpha(CGFloat(motion.stir))
        context.drawRadialGradient(flare, startCenter: spot, startRadius: 0, endCenter: spot, endRadius: flareReach, options: [])
    }

    private static func drawTicks(_ motion: EyesFrameMotion, on context: CGContext) {
        let count = Double(marks.count)
        let walker = (motion.time * 20).truncatingRemainder(dividingBy: count)
        let stirred = Int(motion.place * count)
        context.setLineCap(.round)
        context.setLineWidth(tickWidth)
        for (index, tick) in marks.enumerated() {
            let place = Double(index)
            let bounce = 0.5 + 0.5 * sin(place * 1.9 + motion.time * 7) * sin(place * 0.7 - motion.time * 3.1)
            let ripple = wave(place * 0.34 - motion.time * 5.5)
            let near = closeness(of: place, to: walker, among: count, within: 5)
            let hop = index == stirred ? motion.stir : 0
            let length = 10 + motion.level * (14 + 62 * bounce) + motion.speak * (6 + 44 * ripple) + (motion.think + motion.look) * 30 * near + 64 * hop
            let alpha = 0.78 + 0.22 * (motion.level + motion.speak * ripple + near * (motion.think + motion.look) + hop)
            context.setStrokeColor(red: CGFloat(iris.red / 255), green: CGFloat(iris.green / 255), blue: CGFloat(iris.blue / 255), alpha: CGFloat(min(1, alpha)))
            context.move(to: tick.start)
            context.addLine(to: CGPoint(x: tick.start.x + tick.inward.dx * CGFloat(length), y: tick.start.y + tick.inward.dy * CGFloat(length)))
            context.strokePath()
        }
    }

    private static func wave(_ angle: Double) -> Double { 0.5 + 0.5 * sin(angle) }

    /// How close a place on the ring is to the one that walks around it: 1 right there, 0 past `reach`.
    private static func closeness(of place: Double, to walker: Double, among count: Double, within reach: Double) -> Double {
        let away = abs(place - walker)
        return max(0, 1 - min(away, count - away) / reach)
    }
}
