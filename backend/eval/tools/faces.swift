import CoreGraphics
import CoreText
import Foundation
import ImageIO

/// The window between hints as the phone draws it, with the phone's own painters: every frame in every state, at a few moments.
/// Built together with EyesDirector.swift, EyesPainter.swift and EyesFrame.swift, it writes <frame>__<state><moment>.png, border included,
/// into the folder it is given. Words are drawn with a Mac font, close to the phone's and no more.
@main
enum Faces {
    private static let moments = [0.4, 0.9, 1.7, 2.6, 3.4]
    private static let box = CGRect(x: 180, y: 170, width: 360, height: 360)

    static func main() throws {
        let folder = URL(fileURLWithPath: CommandLine.arguments[1])
        var states: [(name: String, word: String, pose: EyePose, motion: EyesFrameMotion)] = []
        states.append(("rest", "Я рядом", EyePose(), EyesFrameMotion()))
        states.append(("listening", "Слушаю", EyePose(pupil: 1.5), EyesFrameMotion(level: 1)))
        states.append(("reading", "Смотрю", EyePose(x: 0.75, y: -0.6), EyesFrameMotion(look: 1, gaze: 0.75)))
        states.append(("thinking", "Думаю", EyePose(x: 0.7, y: -0.75, squint: 0.35, brow: 1), EyesFrameMotion(think: 1, gaze: 0.7)))
        states.append(("speaking", "Говорю", EyePose(), EyesFrameMotion(speak: 1)))
        for frame in EyesFrame.allCases {
            for state in states {
                for (index, moment) in moments.enumerated() {
                    var motion = state.motion
                    motion.time = moment
                    let name = "\(frame.rawValue)__\(state.name)\(index).png"
                    try save(picture(frame, motion, state.pose, state.word), to: folder.appendingPathComponent(name))
                }
            }
        }
    }

    private static func picture(_ frame: EyesFrame, _ motion: EyesFrameMotion, _ pose: EyePose, _ word: String) -> CGImage {
        let context = CGContext(data: nil, width: 720, height: 960, bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
        context.setFillColor(red: 0, green: 0, blue: 0, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: 720, height: 960))
        context.translateBy(x: 0, y: 960)
        context.scaleBy(x: 1, y: -1)
        frame.draw(motion, on: context)
        EyesPainter.drawNose(in: box, on: context)
        let font = CTFontCreateWithName("Helvetica-Bold" as CFString, 64, nil)
        let attributes: [NSAttributedString.Key: Any] = [kCTFontAttributeName as NSAttributedString.Key: font,
                                                         kCTForegroundColorAttributeName as NSAttributedString.Key: CGColor(gray: 1, alpha: 1)]
        let line = CTLineCreateWithAttributedString(NSAttributedString(string: word, attributes: attributes))
        context.textMatrix = CGAffineTransform(scaleX: 1, y: -1)
        context.textPosition = CGPoint(x: 360 - CTLineGetTypographicBounds(line, nil, nil, nil) / 2, y: 592 + CTFontGetAscent(font))
        CTLineDraw(line, context)
        EyesPainter.draw(pose, in: box, on: context)
        context.setStrokeColor(red: 150.0 / 255, green: 40.0 / 255, blue: 245.0 / 255, alpha: 1)
        context.setLineWidth(25)
        context.stroke(CGRect(x: 12.5, y: 12.5, width: 695, height: 935))
        return context.makeImage()!
    }

    private static func save(_ image: CGImage, to url: URL) throws {
        guard let destination = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else { throw CocoaError(.fileWriteUnknown) }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else { throw CocoaError(.fileWriteUnknown) }
    }
}
