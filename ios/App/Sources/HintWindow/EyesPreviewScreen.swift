import Shared
import SwiftUI

/// Plays every state in half a minute, with no call, on the same code the floating window uses.
@MainActor
private final class EyesPreview: ObservableObject, EyesSurface {
    @Published private(set) var picture: UIImage?
    @Published private(set) var caption = ""
    @Published private(set) var isPlaying = false
    private var show: Task<Void, Never>?
    private var talk: Task<Void, Never>?

    func display(background: CGImage, drawing: (CGContext) -> Void) throws -> Bool {
        guard let context = CGContext(
            data: nil, width: background.width, height: background.height, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue
        ) else { return false }
        context.draw(background, in: CGRect(x: 0, y: 0, width: background.width, height: background.height))
        context.translateBy(x: 0, y: CGFloat(background.height))
        context.scaleBy(x: 1, y: -1)
        drawing(context)
        PictureInPictureHintWindow.strokeBorder(on: context)
        picture = context.makeImage().map { UIImage(cgImage: $0) }
        return true
    }

    func play() {
        stop()
        isPlaying = true
        show = Task { [weak self] in
            guard let self else { return }
            let stage = EyesStage(surface: self, phrases: .device, journal: nil)
            stage.start()
            defer {
                stage.stop()
                self.isPlaying = false
            }
            await self.run(on: stage)
        }
    }

    func stop() {
        show?.cancel()
        show = nil
        talk?.cancel()
        talk = nil
    }

    private func step(_ title: String, _ seconds: Double) async {
        caption = title
        try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }

    private func run(on stage: EyesStage) async {
        await step("Покой", 3)
        stage.newScreen()
        stage.wait(for: .screen)
        await step("Экран сменился: читает", 2.7)
        await step("Ждём ответа: думает", 3)
        for (title, joke) in [("Шутка: круг", EyesJoke.circle), ("Шутка: в кучу", .cross), ("Шутка: прищур", .squint)] {
            guard !Task.isCancelled else { return }
            stage.perform(joke)
            await step(title, joke.length + 0.5)
        }
        stage.stopWaiting(for: .screen)
        guard !Task.isCancelled else { return }
        talk = Task {
            var moment = 0.0
            while !Task.isCancelled {
                let loud = 3000 + 9000 * abs(sin(moment * 2.3))
                stage.heard((0..<1600).map { Int16(loud * sin(Double($0) * 0.2)) })
                moment += 0.1
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
        await step("Слушает: зрачок растёт от голоса", 4)
        talk?.cancel()
        try? await Task.sleep(nanoseconds: 500_000_000)
        stage.speaks(true)
        await step("Говорит", 2)
        stage.speaks(false)
        guard !Task.isCancelled, let jpeg = Self.sampleHint() else { return }
        try? stage.show(hint: jpeg, calm: false)
        await step("Подсказка: глаз нет", 2.5)
        guard !Task.isCancelled else { return }
        stage.pause(words: "Я на связи")
        await step("Пауза с текстом", 1.5)
        guard !Task.isCancelled else { return }
        stage.pause(words: nil)
        stage.ask("Какой шампунь взять?")
        await step("Вопрос человеку", 2)
        guard !Task.isCancelled else { return }
        stage.linkChanged(isUp: false)
        await step("Нет связи: глаза убраны", 2)
    }

    /// A made-up page laid out the way the server lays out a hint for the eyes.
    private static func sampleHint() -> Data? {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let picture = UIGraphicsImageRenderer(size: CGSize(width: 720, height: 960), format: format).image { context in
            UIColor.black.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 720, height: 960))
            UIColor.white.setFill()
            context.fill(CGRect(x: 32, y: 32, width: 656, height: 896))
            UIColor.systemGray5.setFill()
            for top in [90, 270, 450] as [CGFloat] { context.fill(CGRect(x: 60, y: top, width: 600, height: 140)) }
            UIColor.systemGreen.setFill()
            context.fill(CGRect(x: 180, y: 620, width: 360, height: 80))
            UIColor(red: 1, green: 223.0 / 255, blue: 0, alpha: 1).setStroke()
            let outline = UIBezierPath(roundedRect: CGRect(x: 170, y: 610, width: 380, height: 100), cornerRadius: 22)
            outline.lineWidth = 10
            outline.stroke()
        }
        return picture.jpegData(compressionQuality: 0.8)
    }
}

struct EyesPreviewScreen: View {
    @StateObject private var preview = EyesPreview()

    var body: some View {
        VStack(spacing: 16) {
            if let picture = preview.picture {
                Image(uiImage: picture).resizable().scaledToFit()
                    .frame(maxWidth: 300)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            Text(preview.caption).font(.headline).multilineTextAlignment(.center)
            Button("Ещё раз") { preview.play() }
                .disabled(preview.isPlaying)
        }
        .padding()
        .navigationTitle("Глазки")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { preview.play() }
        .onDisappear { preview.stop() }
    }
}
