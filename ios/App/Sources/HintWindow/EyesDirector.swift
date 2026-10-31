import Foundation

/// What the eyes look like at one moment. A picture that would come out the same is not drawn.
struct EyePose: Equatable {
    var x = 0.0  // Where they look: -1 is left, 1 is right.
    var y = 0.0  // -1 is up, 1 is down.
    var left = 1.0  // How open each eye is: 0 is shut, 1 is at rest, a little more is wide open.
    var right = 1.0
    var pupil = 1.0  // 1 is the resting size.
    var squint = 0.0
    var brow = 0.0
    var browLift = 0.0  // How far the brow goes up, in doubt.
    var cross = 0.0  // The pupils come together.
    var isHappy = false  // Arcs in place of the eyes.
}

enum EyesMood: String {
    case rest = "покой", reading = "читает", thinking = "думает", listening = "слушает"
    case speaking = "говорит", pointing = "показывает", happy = "радуется"
    case found = "нашёл", doubting = "сомневается", stopped = "стоп"
}

/// The model's own word for its face. It chooses by itself, and the eyes only show it.
enum EyesFace: String, CaseIterable {
    case thinking = "думаю", found = "нашёл", doubting = "сомневаюсь", happy = "рад", stopped = "стоп"
}

enum EyesJoke: CaseIterable {
    case circle, cross, squint

    var length: TimeInterval {
        switch self {
        case .circle: return 1.6
        case .cross: return 0.7
        case .squint: return 0.8
        }
    }
}

/// Decides how the eyes move. Time comes from outside and is never read here, so a whole call can be played through in a check.
struct EyesDirector {
    static let frame = 1.0 / 30
    static let givesUpAfter = 9.0  // Eyes must not hide a call that has stalled.
    private static let longestWait = 35.0  // Two tries of 15 s on the server and the look at the screen.
    private static let jump = 0.04...0.06
    private static let hold = 0.15...0.7
    private static let readingHold = 0.15...0.2
    private static let blink = 0.13...0.15
    private static let blinkGap = 3.0...6.0
    private static let restBlinkGap = 5.0...9.0
    private static let doubleBlink = 0.15
    private static let blinkOnTurn = 0.6
    private static let thinksAfter = 1.0  // A shorter wait changes nothing, or the eyes would flicker.
    private static let jokesAfter = 3.0
    private static let pausesPerJoke = 4...5
    private static let readingStops = 12  // Four on each of three lines.
    private static let readingFor = 2.7  // One pass over them.
    private static let pointingFor = 1.5
    private static let flareFor = 0.3
    private static let happyFor = 1.0
    private static let faceFor = 1.6
    private static let foundFor = 0.7
    private static let doubtHold = 0.75
    private static let doubtShut = 0.4  // Of the left eye.
    private static let stoppedOpen = 1.16
    private static let stoppedPupil = 0.6
    private static let hearingGap = 0.4  // Sound comes in pieces of 0.1 s, so this means it stopped.
    private static let softSpeed = 8.0  // Per second: the squint, the brow and the pupil take about 0.12 s.

    var isSpeaking = false
    var isCalm = false  // Payment, card, password: nothing but rest.
    var isOnNewScreen = false  // Nobody has said yet what this screen is, and it can be a payment. No jokes over it.
    private(set) var mood = EyesMood.rest
    private let random: () -> Double
    private var now = 0.0

    private var from = (x: 0.0, y: 0.0)
    private var to = (x: 0.0, y: 0.0)
    private var jumpStart = -Double.infinity
    private var jumpLength = 0.05
    private var nextLook = 0.0
    private var readingStep = 0
    private var thinkingSide = 1.0
    private var pointingAt = (x: 0.0, y: 0.0)

    private var blinkStart = -Double.infinity
    private var blinkLength = 0.14
    private var nextBlink: TimeInterval
    private var secondBlink = Double.infinity

    private var pupil = 1.0
    private var squint = 0.0
    private var brow = 0.0
    private var cross = 0.0
    private var wink = 1.0
    private var doubt = 0.0

    private var level = 0.0
    private var lastHeard = -Double.infinity
    private var readingUntil = -Double.infinity
    private var pointStart = -Double.infinity
    private var happyUntil = -Double.infinity
    private var face = EyesMood.rest
    private var faceStart = -Double.infinity
    private var waitingSince: TimeInterval?
    private var countedPause = false
    private var pausesToJoke: Int
    private var lastJoke: EyesJoke?
    private var joke: EyesJoke?
    private var jokeStart = 0.0
    private var jokeAngle = 0.0

    init(random: @escaping () -> Double = { Double.random(in: 0..<1) }) {
        self.random = random
        nextBlink = Self.blinkGap.lowerBound
        pausesToJoke = Self.pausesPerJoke.lowerBound + Int(random() * 2)
    }

    // MARK: What happens around the eyes

    mutating func screenChanged(at time: TimeInterval) {
        readingUntil = time + Self.readingFor
        readingStep = 0
        happyUntil = -.infinity  // A face belongs to the screen it was made for.
        faceStart = -.infinity
    }

    mutating func stopReading() { readingUntil = -.infinity }

    mutating func beginWaiting(at time: TimeInterval) {
        guard waitingSince == nil else { return }
        waitingSince = time
        countedPause = false
    }

    mutating func endWaiting() { waitingSince = nil }

    mutating func hear(level: Double, at time: TimeInterval) {
        self.level = level
        lastHeard = time
    }

    /// The hint is on screen by now. `x` and `y` are the way to the real button, or zero to look ahead.
    mutating func point(x: Double, y: Double, at time: TimeInterval) {
        pointingAt = (x, y)
        pointStart = time
        readingUntil = -.infinity
        nextLook = time
    }

    mutating func rejoice(at time: TimeInterval) { happyUntil = time + Self.happyFor }

    mutating func express(_ face: EyesFace, at time: TimeInterval) {
        switch face {
        case .happy: return rejoice(at: time)
        case .thinking: self.face = .thinking
        case .found: self.face = .found
        case .doubting: self.face = .doubting
        case .stopped: self.face = .stopped
        }
        faceStart = time
        nextLook = time
    }

    mutating func perform(_ joke: EyesJoke, at time: TimeInterval) {
        self.joke = joke
        lastJoke = joke
        jokeStart = time
        let here = gaze(at: time)
        jokeAngle = atan2(here.y, here.x)
        if joke == .cross { jump(to: (0, 0.15), at: time) }
    }

    func hasGivenUp(at time: TimeInterval) -> Bool {
        waitingSince.map { time - $0 >= Self.givesUpAfter } ?? false
    }

    /// No answer takes this long, so it is lost, with the connection for one, and nothing else would end the wait.
    func hasWaitedOut(at time: TimeInterval) -> Bool {
        waitingSince.map { time - $0 >= Self.longestWait } ?? false
    }

    static func level(of samples: [Int16]) -> Double {
        guard !samples.isEmpty else { return 0 }
        let power = samples.reduce(0.0) { $0 + Double($1) * Double($1) } / Double(samples.count)
        let decibels = 10 * log10(max(power, 1) / (32768.0 * 32768.0))
        return min(1, max(0, (decibels + 50) / 30))
    }

    // MARK: The next picture

    mutating func advance(to time: TimeInterval) -> EyePose {
        let passed = max(0, time - now)
        now = time
        let current = moodNow(at: time)
        if current != mood {
            mood = current
            nextLook = time  // A new mood looks somewhere new at once.
            if current != .thinking { joke = nil }
        }
        if let joke, time - jokeStart >= joke.length {
            self.joke = nil
            nextLook = time
        }
        if joke == nil {
            considerJoke(at: time)
            look(at: time)
        }
        if time >= nextBlink, mood != .stopped {
            startBlink(at: time)
            nextBlink = time + choose(mood == .rest ? Self.restBlinkGap : Self.blinkGap)
            if random() < Self.doubleBlink { secondBlink = time + 0.25 }
        }
        if time >= secondBlink {
            startBlink(at: time)
            secondBlink = .infinity
        }
        settle(by: passed, at: time)
        return pose(at: time)
    }

    /// When the picture can change next. The eyes draw nothing until then.
    var nextChange: TimeInterval {
        if isMoving { return now + Self.frame }
        let moments = [nextLook, nextBlink, secondBlink, readingUntil, pointStart + Self.flareFor, pointStart + Self.pointingFor, happyUntil,
                       faceStart + Self.foundFor, faceStart + Self.faceFor,
                       lastHeard + Self.hearingGap, (waitingSince ?? -.infinity) + Self.thinksAfter,
                       (waitingSince ?? -.infinity) + Self.jokesAfter, (waitingSince ?? -.infinity) + Self.givesUpAfter,
                       (waitingSince ?? -.infinity) + Self.longestWait]
        return moments.filter { $0 > now }.min() ?? now + 1
    }

    private func moodNow(at time: TimeInterval) -> EyesMood {
        if isCalm { return .rest }
        if time < happyUntil { return .happy }
        if time < pointStart + Self.pointingFor { return .pointing }
        if time - lastHeard < Self.hearingGap { return .listening }
        if time < faceStart + Self.faceFor { return face }
        if isSpeaking { return .speaking }
        if time < readingUntil { return .reading }
        if let waitingSince, time - waitingSince >= Self.thinksAfter { return .thinking }
        return .rest
    }

    private var isMoving: Bool {
        now < jumpStart + jumpLength || now < blinkStart + blinkLength || joke != nil
            || pupil != pupilTarget || squint != squintTarget || brow != browTarget || cross != crossTarget || wink != winkTarget
            || doubt != doubtTarget
    }

    private var pupilTarget: Double {
        switch mood {
        case .listening: return 1 + 0.55 * level
        case .pointing: return now - pointStart < Self.flareFor ? 1.3 : 1
        case .found: return now - faceStart < Self.foundFor ? 1.4 : 1
        case .stopped: return Self.stoppedPupil
        default: return 1
        }
    }

    private var squintTarget: Double { mood == .thinking ? 0.35 : 0 }
    private var browTarget: Double { mood == .thinking || mood == .doubting ? 1 : 0 }
    private var doubtTarget: Double { mood == .doubting ? 1 : 0 }
    private var crossTarget: Double { joke == .cross ? 1 : 0 }
    private var winkTarget: Double { joke == .squint ? 0.45 : 1 }

    private mutating func settle(by passed: TimeInterval, at time: TimeInterval) {
        let step = passed * Self.softSpeed
        pupil = Self.approach(pupil, pupilTarget, by: step)
        squint = Self.approach(squint, squintTarget, by: step)
        brow = Self.approach(brow, browTarget, by: step)
        cross = Self.approach(cross, crossTarget, by: step)
        wink = Self.approach(wink, winkTarget, by: step)
        doubt = Self.approach(doubt, doubtTarget, by: step)
    }

    private static func approach(_ value: Double, _ target: Double, by step: Double) -> Double {
        abs(target - value) <= step ? target : value + (target > value ? step : -step)
    }

    private mutating func considerJoke(at time: TimeInterval) {
        guard mood == .thinking, !isOnNewScreen, !countedPause, let waitingSince, time - waitingSince >= Self.jokesAfter else { return }
        countedPause = true
        pausesToJoke -= 1
        guard pausesToJoke <= 0 else { return }
        pausesToJoke = Self.pausesPerJoke.lowerBound + Int(random() * 2)
        let choices = EyesJoke.allCases.filter { $0 != lastJoke }
        perform(choices[Int(random() * Double(choices.count))], at: time)
    }

    private mutating func look(at time: TimeInterval) {
        guard joke == nil, time >= nextLook else { return }
        switch mood {
        case .reading:
            let (row, column) = readingStep.quotientAndRemainder(dividingBy: 4)
            jump(to: (-0.75 + Double(column) * 0.5, -0.6 + Double(row % 3) * 0.6), at: time)
            readingStep = (readingStep + 1) % Self.readingStops
            nextLook = time + choose(Self.readingHold)
        case .thinking:
            thinkingSide = -thinkingSide
            jump(to: (0.7 * thinkingSide, -0.75), at: time)
            nextLook = time + choose(0.35...Self.hold.upperBound)
        case .doubting:
            thinkingSide = -thinkingSide
            jump(to: (0.55 * thinkingSide, 0.2), at: time)
            nextLook = time + Self.doubtHold
        case .pointing:
            jump(to: pointingAt, at: time)
            nextLook = .infinity
        case .rest, .listening, .speaking, .happy, .found, .stopped:
            jump(to: (0, 0), at: time)
            nextLook = .infinity
        }
    }

    private mutating func jump(to place: (x: Double, y: Double), at time: TimeInterval) {
        let here = gaze(at: time)
        let distance = hypot(place.x - here.x, place.y - here.y)
        guard distance > 0.01 else { return }
        from = here
        to = place
        jumpStart = time
        jumpLength = choose(Self.jump)
        if distance >= 1, random() < Self.blinkOnTurn { startBlink(at: time) }
    }

    private mutating func startBlink(at time: TimeInterval) {
        guard time >= blinkStart + blinkLength else { return }
        blinkStart = time
        blinkLength = choose(Self.blink)
    }

    private func choose(_ range: ClosedRange<Double>) -> Double {
        range.lowerBound + random() * (range.upperBound - range.lowerBound)
    }

    private func gaze(at time: TimeInterval) -> (x: Double, y: Double) {
        let done = min(1, (time - jumpStart) / jumpLength)
        return (from.x + (to.x - from.x) * done, from.y + (to.y - from.y) * done)
    }

    private func lid(at time: TimeInterval) -> Double {
        let phase = (time - blinkStart) / blinkLength
        guard (0..<1).contains(phase) else { return 1 }
        return phase < 0.4 ? 1 - phase / 0.4 : (phase - 0.4) / 0.6
    }

    private func opening(at time: TimeInterval) -> Double {
        switch mood {
        case .pointing: return time - pointStart < Self.flareFor ? 1.12 : 1
        case .found: return time - faceStart < Self.foundFor ? 1.12 : 1
        case .stopped: return Self.stoppedOpen
        default: return 1
        }
    }

    private func pose(at time: TimeInterval) -> EyePose {
        var pose = EyePose()
        var place = gaze(at: time)
        if joke == .circle {
            let angle = jokeAngle + 2 * .pi * (time - jokeStart) / EyesJoke.circle.length
            place = (0.85 * cos(angle), 0.85 * sin(angle))
        }
        pose.x = place.x
        pose.y = place.y
        pose.left = lid(at: time) * opening(at: time)
        pose.right = pose.left * wink
        pose.left *= 1 - Self.doubtShut * doubt
        pose.pupil = pupil
        pose.squint = squint
        pose.brow = brow
        pose.browLift = doubt
        pose.cross = cross
        pose.isHappy = mood == .happy
        return pose
    }
}
