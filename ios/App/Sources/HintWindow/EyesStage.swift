import Shared
import UIKit

/// What the eyes draw on: the floating window, or the page that shows them off.
@MainActor
protocol EyesSurface: AnyObject {
    /// The background goes in as it is, and the rest is drawn over it with the origin at the top left.
    /// False means this picture did not go out and is worth another try.
    func display(background: CGImage, drawing: (CGContext) -> Void) throws -> Bool
}

/// Keeps the window alive between hints: the eyes and the word for what is really going on, which real events decide and not the
/// model. A hint takes the whole window. Draws only when the picture changes: up to 30 pictures a second in motion, none at rest.
/// It runs on a plain timer, as a screen timer stops once the app is in the background.
@MainActor
final class EyesStage {
    enum Wait { case reply, screen }

    private enum Scene: Equatable { case pause, hint }

    private static let size = CGSize(width: 720, height: 960)
    private static let wordsBox = CGRect(x: 180, y: 130, width: 360, height: 360)
    private static let wordsArea = CGRect(x: 60, y: 510, width: 600, height: 400)
    private static let longWaitWords = "Жду ответа дольше обычного"
    private static let noLinkWords = "Нет связи"
    private static let restWords = "Я рядом"
    private static let slowestRetry = 8  // In frames: a window that keeps refusing is not worth 30 tries a second.

    private let surface: any EyesSurface
    private let journal: SharedJournal?
    private var director = EyesDirector()
    private var timer: DispatchSourceTimer?
    private var startedAt: TimeInterval = 0
    private var scene = Scene.pause
    private var hint: CGImage?
    private var words: String?
    private var question: String?
    private var isLinkDown = false
    private var notice: String?
    private var backdrop: CGImage?
    private var eyesBox: CGRect?
    private var composedWords: String?
    private var needsComposing = true
    private var shown: EyePose?
    private var mood = EyesMood.rest
    private var moodSince: TimeInterval = 0
    private var waits = Set<Wait>()
    private var frames = 0
    private var skipped = 0
    private var refused = 0
    private var drawing: TimeInterval = 0

    init(surface: any EyesSurface, journal: SharedJournal?) {
        self.surface = surface
        self.journal = journal
    }

    func start() {
        startedAt = ProcessInfo.processInfo.systemUptime
        journal?.append("Глаза: включены")
        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.setEventHandler { [weak self] in MainActor.assumeIsolated { self?.tick() } }
        timer.schedule(deadline: .distantFuture)
        timer.resume()
        self.timer = timer
        tick()
    }

    func stop() {
        guard let timer else { return }
        timer.cancel()
        self.timer = nil
        let seconds = clock
        let perFrame = frames > 0 ? drawing / Double(frames) * 1000 : 0
        journal?.append("Глаза: кадров \(frames) за \(Self.number(seconds)) с, \(Self.number(perFrame)) мс на кадр, не приняты \(skipped)")
    }

    private var clock: TimeInterval { ProcessInfo.processInfo.systemUptime - startedAt }

    // MARK: What happens in the call

    /// The hint goes out in this very call, so the eyes do not hold it back.
    func show(hint jpeg: Data, calm: Bool) throws {
        guard let image = UIImage(data: jpeg),
              let picture = Self.canvas({ _ in image.draw(in: CGRect(origin: .zero, size: Self.size)) }) else { throw StageError.picture }
        hint = picture
        scene = .hint
        question = nil
        needsComposing = true
        director.isCalm = calm
        director.stopReading()
        stopWaiting(for: .screen, redraw: false)
        tick()
    }

    /// The hint belongs to a screen the person has left, so it goes at once.
    func newScreen() {
        scene = .pause
        needsComposing = true
        director.screenChanged(at: clock)
        tick()
    }

    /// A pause with no hint to go back to. These words stand for rest, until the next change.
    func pause(words: String?) {
        hint = nil
        scene = .pause
        self.words = words
        question = nil
        needsComposing = true
        director.stopReading()
        tick()
    }

    /// On a payment screen the eyes keep still.
    func express(_ face: EyesFace?, calm: Bool) {
        director.isCalm = calm
        if let face { director.express(face, at: clock) }
        tick()
    }

    /// The agent asked the person something. With a hint on screen the hint matters more.
    func ask(_ text: String) {
        guard scene == .pause else { return }
        question = text
        needsComposing = true
        tick()
    }

    func wait(for reason: Wait) {
        waits.insert(reason)
        director.isOnNewScreen = waits.contains(.screen)
        director.beginWaiting(at: clock)
        if reason == .reply, question != nil {
            question = nil
            needsComposing = true
        }
        tick()
    }

    func stopWaiting(for reason: Wait, redraw: Bool = true) {
        waits.remove(reason)
        director.isOnNewScreen = waits.contains(.screen)
        if waits.isEmpty { director.endWaiting() }
        if redraw { tick() }
    }

    func speaks(_ isSpeaking: Bool) {
        director.isSpeaking = isSpeaking
        tick()
    }

    /// Only sound that really went to the server comes here, so a deaf stream shows at once as a still pupil.
    func heard(_ samples: [Int16]) {
        director.hear(level: EyesDirector.level(of: samples), at: clock)
        tick()
    }

    func linkChanged(isUp: Bool) {
        guard isLinkDown == isUp else { return }
        isLinkDown = !isUp
        journal?.append("Глаза: связь \(isUp ? "есть" : "пропала")")
        tick()
    }

    func perform(_ joke: EyesJoke) {
        director.perform(joke, at: clock)
        tick()
    }

    func redraw() {
        needsComposing = true
        shown = nil
        tick()
    }

    // MARK: The picture

    private func tick() {
        guard let timer else { return }
        let now = clock
        if director.hasWaitedOut(at: now) {
            waits.removeAll()
            director.isOnNewScreen = false
            director.endWaiting()
        }
        let pose = director.advance(to: now)
        if director.mood != mood {
            journal?.append("Глаза: \(director.mood.rawValue), до этого \(mood.rawValue) \(Self.number(now - moodSince)) с")
            mood = director.mood
            moodSince = now
        }
        let wanted = isLinkDown ? Self.noLinkWords : director.hasGivenUp(at: now) ? Self.longWaitWords : nil
        if wanted != notice || needsComposing || underEyes != composedWords {
            if wanted != notice { journal?.append("Глаза: \(wanted == nil ? "снова на месте" : "убраны, текст на чёрном")") }
            notice = wanted
            compose()
        }
        if pose != shown { draw(pose) }
        let next = pose == shown ? director.nextChange : now + EyesDirector.frame * Double(min(max(refused, 1), Self.slowestRetry))
        timer.schedule(deadline: .now() + max(0, next - now), leeway: .milliseconds(3))
    }

    private var underEyes: String {
        if let question { return question }
        switch mood {
        case .listening: return "Слушаю"
        case .reading: return "Смотрю"
        case .thinking: return "Думаю"
        case .speaking: return "Говорю"
        default: return words ?? Self.restWords
        }
    }

    private func compose() {
        needsComposing = false
        shown = nil
        composedWords = underEyes
        if scene == .hint, let hint {
            backdrop = hint
            eyesBox = nil
        } else if let notice {
            backdrop = Self.wordsPicture(notice, in: CGRect(x: 60, y: 200, width: 600, height: 560))
            eyesBox = nil
        } else {
            backdrop = Self.wordsPicture(underEyes, in: Self.wordsArea)
            eyesBox = Self.wordsBox
        }
    }

    private func draw(_ pose: EyePose) {
        guard let backdrop else { return }
        let started = ProcessInfo.processInfo.systemUptime
        let box = eyesBox
        do {
            let sent = try surface.display(background: backdrop) { context in
                if let box { EyesPainter.draw(pose, in: box, on: context) }
            }
            if sent {
                shown = pose
                frames += 1
                refused = 0
            } else {
                skipped += 1
                refused += 1
            }
        } catch { journal?.append("Глаза: кадр не собран: \(error.localizedDescription)") }
        drawing += ProcessInfo.processInfo.systemUptime - started
    }

    /// The same bytes as the window's own pictures, so it only has to copy them.
    private static func canvas(_ paint: (CGContext) -> Void) -> CGImage? {
        guard let context = CGContext(
            data: nil, width: Int(size.width), height: Int(size.height), bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue
        ) else { return nil }
        context.setFillColor(UIColor.black.cgColor)
        context.fill(CGRect(origin: .zero, size: size))
        context.translateBy(x: 0, y: size.height)
        context.scaleBy(x: 1, y: -1)
        UIGraphicsPushContext(context)
        defer { UIGraphicsPopContext() }
        paint(context)
        return context.makeImage()
    }

    private static func wordsPicture(_ text: String, in area: CGRect) -> CGImage? {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        paragraph.lineBreakMode = .byWordWrapping
        var attributes: [NSAttributedString.Key: Any] = [.foregroundColor: UIColor.white, .paragraphStyle: paragraph]
        let options: NSStringDrawingOptions = [.usesLineFragmentOrigin, .truncatesLastVisibleLine]
        var height: CGFloat = 0
        for size in [64, 56, 48, 40, 34] as [CGFloat] {
            attributes[.font] = UIFont.systemFont(ofSize: size, weight: .semibold)
            height = (text as NSString).boundingRect(with: CGSize(width: area.width, height: .greatestFiniteMagnitude), options: options,
                                                     attributes: attributes, context: nil).height
            if height <= area.height { break }
        }
        let top = area.minY + max(0, area.height - height) / 2
        return canvas { _ in
            (text as NSString).draw(with: CGRect(x: area.minX, y: top, width: area.width, height: area.maxY - top), options: options,
                                    attributes: attributes, context: nil)
        }
    }

    private static func number(_ value: Double) -> String {
        String(format: "%.1f", locale: Locale(identifier: "ru_RU"), value)
    }

    private enum StageError: LocalizedError {
        case picture
        var errorDescription: String? { "Не удалось прочитать картинку подсказки." }
    }
}
