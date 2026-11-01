import AVKit
import CoreMedia
import CoreVideo
import OSLog
import UIKit

@MainActor
final class PictureInPictureHintWindow: NSObject, HintWindowPresenting {
    /// The video call window has no system buttons, so nothing dims it over its border. False brings back the sample buffer window.
    static let usesVideoCall = true
    private static let frameSize = CGSize(width: 720, height: 960)

    var inlineView: UIView { sourceView }
    var onStateChange: ((HintWindowState) -> Void)?
    var onRepeatHint: (() -> Void)?
    var onDiagnostic: ((String) -> Void)?

    private let sourceView = SampleBufferView()
    private let logger = Logger(subsystem: Bundle.main.bundleIdentifier ?? "App", category: "HintWindow")
    private var controller: AVPictureInPictureController?
    private var readinessObservation: NSKeyValueObservation?
    private var videoCallController: AVPictureInPictureVideoCallViewController?
    private var videoCallView: SampleBufferView?
    private var pixelBufferPool: CVPixelBufferPool?

    func prepare() {
        guard controller == nil else { return }
        guard AVPictureInPictureController.isPictureInPictureSupported() else {
            onStateChange?(.unavailable)
            return
        }

        let contentSource: AVPictureInPictureController.ContentSource
        if Self.usesVideoCall {
            let callController = AVPictureInPictureVideoCallViewController()
            callController.preferredContentSize = Self.frameSize
            let callView = SampleBufferView()
            callView.translatesAutoresizingMaskIntoConstraints = false
            callController.view.addSubview(callView)
            NSLayoutConstraint.activate([
                callView.leadingAnchor.constraint(equalTo: callController.view.leadingAnchor),
                callView.trailingAnchor.constraint(equalTo: callController.view.trailingAnchor),
                callView.topAnchor.constraint(equalTo: callController.view.topAnchor),
                callView.bottomAnchor.constraint(equalTo: callController.view.bottomAnchor)
            ])
            videoCallController = callController
            videoCallView = callView
            contentSource = .init(activeVideoCallSourceView: sourceView, contentViewController: callController)
        } else {
            contentSource = .init(sampleBufferDisplayLayer: sourceView.displayLayer, playbackDelegate: self)
        }

        let controller = AVPictureInPictureController(contentSource: contentSource)
        self.controller = controller
        controller.delegate = self
        controller.canStartPictureInPictureAutomaticallyFromInline = true
        controller.requiresLinearPlayback = true
        onStateChange?(.preparing)
        readinessObservation = controller.observe(\.isPictureInPicturePossible, options: [.initial, .new]) { [weak self] observed, _ in
            let identifier = ObjectIdentifier(observed)
            Task { @MainActor [weak self] in
                guard let self, let controller = self.controller, ObjectIdentifier(controller) == identifier else { return }
                self.publishState()
            }
        }
    }

    func display(jpegData: Data) throws {
        guard let image = UIImage(data: jpegData) else { throw FrameError.invalidImage }
        try display(image: image)
    }

    func display(image: UIImage) throws {
        enqueue(try makeSampleBuffer(from: image))
        controller?.invalidatePlaybackState()
    }

    func display(background: CGImage, drawing: (CGContext) -> Void) throws -> Bool {
        guard shownView.isReady else { return false }
        let sampleBuffer = try makeSampleBuffer { context in
            // Before the flip the picture goes in as it is, the cheapest way. This runs 30 times a second.
            context.draw(background, in: CGRect(origin: .zero, size: Self.frameSize))
            context.translateBy(x: 0, y: Self.frameSize.height)
            context.scaleBy(x: 1, y: -1)
            drawing(context)
        }
        enqueue(sampleBuffer)
        return true
    }

    /// The one the person sees. In the app that is the source view, and in the open window of a video call its own view.
    private var shownView: SampleBufferView {
        if let videoCallView, controller?.isPictureInPictureActive == true { return videoCallView }
        return sourceView
    }

    /// The other view can stand still for the whole call, so it only takes what it has room for.
    private func enqueue(_ sampleBuffer: CMSampleBuffer) {
        for view in [sourceView, videoCallView].compactMap({ $0 }) where view === shownView || view.isReady {
            view.enqueue(sampleBuffer)
        }
    }

    func startPictureInPicture() {
        onDiagnostic?("Запрошен старт окна")
        guard let controller, !controller.isPictureInPictureActive else { return }
        guard controller.isPictureInPicturePossible else {
            publishState()
            return
        }
        controller.startPictureInPicture()
    }

    func stop() {
        if controller != nil { onDiagnostic?("Запрошен стоп окна") }
        readinessObservation?.invalidate()
        readinessObservation = nil
        controller?.canStartPictureInPictureAutomaticallyFromInline = false
        controller?.stopPictureInPicture()
        controller?.contentSource = nil
        controller?.delegate = nil
        controller = nil
        sourceView.clear()
        videoCallView?.clear()
        videoCallView = nil
        videoCallController = nil
        pixelBufferPool = nil
        onStateChange?(.inactive)
    }

    private func publishState() {
        guard let controller else { return }
        if controller.isPictureInPictureActive {
            onStateChange?(.active)
        } else {
            onStateChange?(controller.isPictureInPicturePossible ? .ready : .preparing)
        }
    }

    private func makeSampleBuffer(from image: UIImage) throws -> CMSampleBuffer {
        guard image.size.width > 0, image.size.height > 0 else { throw FrameError.invalidImage }
        return try makeSampleBuffer { context in
            context.setFillColor(UIColor.black.cgColor)
            context.fill(CGRect(origin: .zero, size: Self.frameSize))
            context.translateBy(x: 0, y: Self.frameSize.height)
            context.scaleBy(x: 1, y: -1)
            let scale = min(Self.frameSize.width / image.size.width, Self.frameSize.height / image.size.height)
            let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
            let rect = CGRect(
                x: (Self.frameSize.width - size.width) / 2,
                y: (Self.frameSize.height - size.height) / 2, width: size.width, height: size.height
            )
            UIGraphicsPushContext(context)
            defer { UIGraphicsPopContext() }
            image.draw(in: rect)
        }
    }

    private func makeSampleBuffer(painting paint: (CGContext) -> Void) throws -> CMSampleBuffer {
        if pixelBufferPool == nil {
            let attributes: [CFString: Any] = [
                kCVPixelBufferWidthKey: Int(Self.frameSize.width),
                kCVPixelBufferHeightKey: Int(Self.frameSize.height),
                kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA,
                kCVPixelBufferCGImageCompatibilityKey: true,
                kCVPixelBufferCGBitmapContextCompatibilityKey: true,
                kCVPixelBufferIOSurfacePropertiesKey: [:] as [String: Any]
            ]
            let result = CVPixelBufferPoolCreate(kCFAllocatorDefault, nil, attributes as CFDictionary, &pixelBufferPool)
            guard result == kCVReturnSuccess else { throw FrameError.system(result) }
        }
        guard let pool = pixelBufferPool else { throw FrameError.invalidImage }
        var pixelBuffer: CVPixelBuffer?
        let allocationResult = CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &pixelBuffer)
        guard allocationResult == kCVReturnSuccess, let pixelBuffer else {
            throw FrameError.system(allocationResult)
        }
        try draw(into: pixelBuffer, painting: paint)

        var format: CMVideoFormatDescription?
        let formatResult = CMVideoFormatDescriptionCreateForImageBuffer(
            allocator: kCFAllocatorDefault, imageBuffer: pixelBuffer, formatDescriptionOut: &format
        )
        guard formatResult == noErr, let format else { throw FrameError.system(formatResult) }

        var timing = CMSampleTimingInfo(
            duration: .invalid,
            presentationTimeStamp: CMClockGetTime(CMClockGetHostTimeClock()),
            decodeTimeStamp: .invalid
        )
        var sample: CMSampleBuffer?
        let sampleResult = CMSampleBufferCreateReadyWithImageBuffer(
            allocator: kCFAllocatorDefault, imageBuffer: pixelBuffer, formatDescription: format,
            sampleTiming: &timing, sampleBufferOut: &sample
        )
        guard sampleResult == noErr, let sample else { throw FrameError.system(sampleResult) }

        // Sparse hints replace the current image immediately; no custom timebase is used.
        if let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: true),
           CFArrayGetCount(attachments) > 0 {
            let entry = unsafeBitCast(CFArrayGetValueAtIndex(attachments, 0), to: CFMutableDictionary.self)
            CFDictionarySetValue(
                entry, Unmanaged.passUnretained(kCMSampleAttachmentKey_DisplayImmediately).toOpaque(),
                Unmanaged.passUnretained(kCFBooleanTrue).toOpaque()
            )
        }
        return sample
    }

    private func draw(into buffer: CVPixelBuffer, painting paint: (CGContext) -> Void) throws {
        let result = CVPixelBufferLockBaseAddress(buffer, [])
        guard result == kCVReturnSuccess else { throw FrameError.system(result) }
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }

        let bitmapInfo = CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue
        guard let context = CGContext(
            data: CVPixelBufferGetBaseAddress(buffer),
            width: CVPixelBufferGetWidth(buffer), height: CVPixelBufferGetHeight(buffer),
            bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(buffer),
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: bitmapInfo
        ) else { throw FrameError.invalidImage }

        paint(context)
        Self.strokeBorder(on: context)
    }

    /// This colour tells the server which part of a screenshot is our own window.
    static func strokeBorder(on context: CGContext) {
        let thickness = (frameSize.width * 0.035).rounded()
        context.setStrokeColor(UIColor(red: 150.0 / 255, green: 40.0 / 255, blue: 245.0 / 255, alpha: 1).cgColor)
        context.setLineWidth(thickness)
        context.stroke(CGRect(origin: .zero, size: frameSize).insetBy(dx: thickness / 2, dy: thickness / 2))
    }

    private enum FrameError: LocalizedError {
        case invalidImage
        case system(OSStatus)

        var errorDescription: String? {
            switch self {
            case .invalidImage: return "Не удалось прочитать картинку подсказки."
            case .system(let code): return "Не удалось подготовить кадр (\(code))."
            }
        }
    }

    private final class SampleBufferView: UIView {
        override class var layerClass: AnyClass { AVSampleBufferDisplayLayer.self }
        var displayLayer: AVSampleBufferDisplayLayer { layer as! AVSampleBufferDisplayLayer }
        var isReady: Bool {  // A stuck renderer counts too: only `enqueue` brings it back.
            let renderer = displayLayer.sampleBufferRenderer
            return renderer.isReadyForMoreMediaData || renderer.status == .failed || renderer.requiresFlushToResumeDecoding
        }

        init() {
            super.init(frame: .zero)
            backgroundColor = .black
            displayLayer.videoGravity = .resizeAspect
            displayLayer.preventsDisplaySleepDuringVideoPlayback = true
        }

        required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

        func enqueue(_ sampleBuffer: CMSampleBuffer) {
            let renderer = displayLayer.sampleBufferRenderer
            if renderer.status == .failed || renderer.requiresFlushToResumeDecoding {
                renderer.flush()
            }
            renderer.enqueue(sampleBuffer)
        }

        func clear() {
            displayLayer.sampleBufferRenderer.flush(removingDisplayedImage: true, completionHandler: nil)
        }
    }
}

extension PictureInPictureHintWindow: AVPictureInPictureSampleBufferPlaybackDelegate {
    nonisolated func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController, setPlaying playing: Bool) {
        // Keep hints live; the system control repeats the spoken instruction.
        let identifier = ObjectIdentifier(pictureInPictureController)
        Task { @MainActor [weak self] in
            guard let self, let controller = self.controller, ObjectIdentifier(controller) == identifier else { return }
            self.onDiagnostic?("Системная кнопка: \(playing ? "плей" : "пауза")")
            controller.invalidatePlaybackState()
            try? await Task.sleep(nanoseconds: 1_200_000_000)  // A closing window sends a pause first and stops 0.83 s later.
            guard self.controller === controller, controller.isPictureInPictureActive else { return }
            self.onDiagnostic?("Повтор голоса")
            self.onRepeatHint?()
        }
    }

    nonisolated func pictureInPictureControllerTimeRangeForPlayback(_ pictureInPictureController: AVPictureInPictureController) -> CMTimeRange {
        CMTimeRange(start: .zero, duration: .positiveInfinity)
    }

    nonisolated func pictureInPictureControllerIsPlaybackPaused(_ pictureInPictureController: AVPictureInPictureController) -> Bool {
        false
    }

    nonisolated func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController, didTransitionToRenderSize newRenderSize: CMVideoDimensions) {
        Task { @MainActor [weak self] in
            self?.logger.debug("PiP render size: \(newRenderSize.width)x\(newRenderSize.height)")
        }
    }

    nonisolated func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController, skipByInterval skipInterval: CMTime, completion: @escaping () -> Void) {
        completion()
    }

    nonisolated func pictureInPictureControllerShouldProhibitBackgroundAudioPlayback(_ pictureInPictureController: AVPictureInPictureController) -> Bool {
        false
    }
}

extension PictureInPictureHintWindow: AVPictureInPictureControllerDelegate {
    nonisolated func pictureInPictureControllerWillStartPictureInPicture(_ pictureInPictureController: AVPictureInPictureController) {
        let identifier = ObjectIdentifier(pictureInPictureController)
        Task { @MainActor [weak self] in
            guard let self, let controller = self.controller, ObjectIdentifier(controller) == identifier else { return }
            self.onDiagnostic?("Окно начинает открываться")
        }
    }

    nonisolated func pictureInPictureControllerDidStartPictureInPicture(_ pictureInPictureController: AVPictureInPictureController) {
        let identifier = ObjectIdentifier(pictureInPictureController)
        Task { @MainActor [weak self] in
            guard let self, let controller = self.controller, ObjectIdentifier(controller) == identifier else { return }
            self.onDiagnostic?("Старт окна подтверждён системой")
            self.onStateChange?(.active)
        }
    }

    nonisolated func pictureInPictureControllerDidStopPictureInPicture(_ pictureInPictureController: AVPictureInPictureController) {
        let identifier = ObjectIdentifier(pictureInPictureController)
        Task { @MainActor [weak self] in
            guard let self, let controller = self.controller, ObjectIdentifier(controller) == identifier else { return }
            self.onDiagnostic?("Стоп окна подтверждён системой")
            self.publishState()
        }
    }

    nonisolated func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController, failedToStartPictureInPictureWithError error: Error) {
        let identifier = ObjectIdentifier(pictureInPictureController)
        let message = error.localizedDescription
        Task { @MainActor [weak self] in
            guard let self, let controller = self.controller, ObjectIdentifier(controller) == identifier else { return }
            self.logger.error("PiP start failed: \(message, privacy: .public)")
            self.onStateChange?(.failed(message))
        }
    }

    nonisolated func pictureInPictureController(_ pictureInPictureController: AVPictureInPictureController, restoreUserInterfaceForPictureInPictureStopWithCompletionHandler completionHandler: @escaping (Bool) -> Void) {
        completionHandler(true)
    }
}
