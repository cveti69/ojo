import CoreImage
import ImageIO
import os
import ReplayKit
import Shared
import UniformTypeIdentifiers

final class SampleHandler: RPBroadcastSampleHandler, @unchecked Sendable {
    private enum Capture {
        static let interval: TimeInterval = 0.5
        static let maximumDimension: CGFloat = 1000
        static let jpegQuality = 0.6
        static let thumbnailSide = 256  // At 64 a price digit or a thin check mark changes nothing.
        static let changeThreshold = 0.025
        static let movedShare = 0.3  // With our window left out. On public pages a banner or a video reaches 0.29, the keyboard 0.33, a new screen 0.63.
        static let memoryReserve = 8 * 1024 * 1024
        static let textMemoryReserve = 16 * 1024 * 1024
        static let windowGoneAfter = 6  // Looks in a row without it: they closed it, and the place counts again.
    }

    private struct Run: Sendable {
        let id: UUID
        let sessionID: String
        let store: SharedStore
        let journal: SharedJournal
        let client: ServerClient
        let mailbox: ScreenTextMailbox?
        let shelf: FrameShelf?
    }

    private struct FrameCheck {
        let run: Run
        let previousThumbnail: [UInt8]?
        let previousSize: CGSize
    }

    private struct Sample {
        let buffer: CVPixelBuffer
        let orientation: CGImagePropertyOrientation?
        let capturedAt: Date
    }

    private struct KeptFrame {
        let jpeg: Data
        let thumbnail: [UInt8]
        let size: CGSize
        let timestamp: Int64
    }

    private let lock = NSLock()
    private let context = CIContext(options: [.cacheIntermediates: false])
    private let colorSpace = CGColorSpaceCreateDeviceRGB()
    private let graySpace = CGColorSpaceCreateDeviceGray()
    private let textWorker = ScreenTextWorker()
    private let mailboxQueue = DispatchQueue(label: "broadcast.text-mailbox", qos: .utility)
    private var run: Run?
    private var runID = UUID()
    private var journal: SharedJournal?
    private var heartbeat: DispatchSourceTimer?
    private var memoryPressure: DispatchSourceMemoryPressure?
    private var processingFrame = false
    private var requestInFlight = false
    private var requestID: UUID?
    private var requestTask: Task<Void, Never>?
    private var textInFlight = false
    private var textTask: Task<Void, Never>?
    private var firstTextReadLogged = false
    private var heldSample: Sample?
    private var newestSample: Sample?
    private var answeredAsk = 0
    private var stillness = ScreenStillness()
    private var screenMoved = false
    private var windowPatch: ScreenPatch?
    private var windowLooks = 0
    private var windowFinds = 0
    private var windowMisses = 0
    private var keptFrame: KeptFrame?
    private var screenLang: String?
    private var finished = true
    private var finishReported = false
    private var lastCheck: TimeInterval = -.infinity
    private var previousThumbnail: [UInt8]?
    private var previousSize = CGSize.zero
    private var sent = 0
    private var skipped = 0
    private var lastStatistics = ProcessInfo.processInfo.systemUptime

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        let id = resetForStart()
        do {
            let configuration = try ProjectConfiguration()
            let journal = SharedJournal(configuration: configuration, source: "Трансляция")
            lock.withLock { self.journal = journal }
            let store = try SharedStore(configuration: configuration)
            guard let sessionID = store.sessionID else { throw BroadcastError.noSession }
            let settings = store.settings
            guard !settings.token.isEmpty else { throw ClientError.missingToken }
            let run = Run(id: id, sessionID: sessionID, store: store, journal: journal,
                          client: ServerClient(settings: settings, journal: { journal.append($0) }), mailbox: try? ScreenTextMailbox(configuration: configuration),
                          shelf: try? FrameShelf(configuration: configuration))
            let ticket = UUID()
            let asked = store.screenAsked
            lock.withLock {
                self.run = run
                answeredAsk = asked
                requestInFlight = true
                requestID = ticket
            }
            journal.append("Старт; только видео, 500 мс, JPEG 0,6, длинная сторона \(Int(Capture.maximumDimension)) px; текст: \(store.screenTextMode.rawValue); глаза: \(settings.eyes ? "да" : "нет")")
            startMonitoring(run: run)
            let task = Task { [self] in
                do {
                    _ = try await run.client.broadcast(.started, sessionID: sessionID)
                    journal.append("Сервер уведомлён: started")
                } catch {
                    if !Task.isCancelled { journal.append("Ошибка started: \(error.localizedDescription)") }
                    stopForError(error, runID: id)
                }
                completeRequest(runID: id, ticket: ticket)
            }
            installRequest(task, runID: id, ticket: ticket)
        } catch {
            currentJournal?.append("Ошибка старта: \(error.localizedDescription)")
            stopForError(error, runID: id)
        }
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        guard sampleBufferType == .video, let buffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        var orientation: CGImagePropertyOrientation?
        if let value = CMGetAttachment(sampleBuffer, key: RPVideoSampleOrientationKey as CFString, attachmentModeOut: nil) as? NSNumber {
            orientation = CGImagePropertyOrientation(rawValue: value.uint32Value)
        }
        let sample = Sample(buffer: buffer, orientation: orientation, capturedAt: Date())
        let now = ProcessInfo.processInfo.systemUptime
        let check: FrameCheck? = lock.withLock {
            guard !finished, run != nil else { return nil }
            newestSample = sample
            let check = beginCheck(now: now)
            // The system sends nothing once the screen goes still, so the newest skipped sample waits for the monitor.
            heldSample = check == nil ? sample : nil
            if check == nil { skipped += 1 }
            return check
        }
        if let check { process(sample, check: check) }
    }

    /// Call with the lock held.
    private func beginCheck(now: TimeInterval) -> FrameCheck? {
        guard let run, !requestInFlight, !processingFrame, now - lastCheck >= Capture.interval else { return nil }
        lastCheck = now
        processingFrame = true
        return FrameCheck(run: run, previousThumbnail: previousThumbnail, previousSize: previousSize)
    }

    private func sendHeldSample() {
        let now = ProcessInfo.processInfo.systemUptime
        let ready: (Sample, FrameCheck)? = lock.withLock {
            guard !finished, let sample = heldSample, let check = beginCheck(now: now) else { return nil }
            heldSample = nil
            return (sample, check)
        }
        if let ready { process(ready.0, check: ready.1) }
    }

    private func process(_ sample: Sample, check: FrameCheck) {
        defer {
            lock.withLock { if runID == check.run.id { processingFrame = false } }
        }
        if os_proc_available_memory() < Capture.memoryReserve {
            skipFrame(runID: check.run.id, clearThumbnail: true)
            context.clearCaches()
            check.run.journal.append("Предупреждение о памяти: осталось меньше 8 МБ, кадр пропущен")
            return
        }
        // ReplayKit owns the sample; only the pixel buffer of the newest one is ever kept.
        autoreleasepool {
            let timestamp = Int64(sample.capturedAt.timeIntervalSince1970 * 1000)
            let image = upright(sample)
            let size = image.extent.size
            guard size.width > 0, size.height > 0 else { return }
            let thumbnail = makeThumbnail(image)
            let patch = findWindow(image)
            let previous = size == check.previousSize ? check.previousThumbnail : nil
            let change = previous.map { ScreenStillness.difference(thumbnail, $0, outside: patch) }
            let cells = previous.map { ScreenStillness.changedCells(thumbnail, $0, outside: patch) } ?? 0
            let now = ProcessInfo.processInfo.systemUptime
            let (settled, isQuiet, afterMove, waited) = lock.withLock {
                (stillness.see(thumbnail, at: now, outside: patch), stillness.isQuiet, screenMoved, stillness.waited)
            }
            let large = (change ?? 1) >= Capture.changeThreshold || afterMove
            guard large || cells >= ScreenStillness.smallChange else {
                skipFrame(runID: check.run.id)
                return
            }
            guard large ? settled : isQuiet else {
                hold(sample, runID: check.run.id)  // Frames stop with the screen, so the monitor looks at this one again.
                if large, let previous, ScreenStillness.changedShare(thumbnail, previous, outside: patch) >= Capture.movedShare {
                    lock.withLock { if runID == check.run.id { screenMoved = true } }
                }
                skipFrame(runID: check.run.id)
                return
            }
            if waited > 0 {
                check.run.journal.append("Экран успокоился за \(String(format: "%.1f", locale: Locale(identifier: "ru_RU"), waited)) с")
            }
            guard let jpeg = makeSmallJPEG(image) else {
                skipFrame(runID: check.run.id)
                check.run.journal.append("Не удалось сжать кадр")
                return
            }
            keep(KeptFrame(jpeg: jpeg, thumbnail: thumbnail, size: size, timestamp: timestamp), outside: patch, run: check.run)
            upload(jpeg, sample: sample, thumbnail: thumbnail, size: size, timestamp: timestamp, small: !large, run: check.run)
        }
    }

    private func upright(_ sample: Sample) -> CIImage {
        var image = CIImage(cvPixelBuffer: sample.buffer)
        if let orientation = sample.orientation { image = image.oriented(orientation) }
        return image.transformed(by: CGAffineTransform(translationX: -image.extent.minX, y: -image.extent.minY))
    }

    private func makeSmallJPEG(_ image: CIImage) -> Data? {
        let size = image.extent.size
        let scale = min(1, Capture.maximumDimension / max(size.width, size.height))
        let bounds = CGRect(x: 0, y: 0, width: floor(size.width * scale), height: floor(size.height * scale))
        return makeJPEG(image.transformed(by: CGAffineTransform(scaleX: scale, y: scale)).cropped(to: bounds))
    }

    private func hold(_ sample: Sample, runID id: UUID) {
        lock.withLock { if runID == id, !finished, heldSample == nil { heldSample = sample } }
    }

    private func keep(_ frame: KeptFrame, outside patch: ScreenPatch?, run: Run) {
        let sameScreen = lock.withLock { () -> Bool in
            guard runID == run.id else { return false }
            defer { keptFrame = frame }
            guard let kept = keptFrame, kept.size == frame.size else { return false }
            return ScreenStillness.changedShare(frame.thumbnail, kept.thumbnail, outside: patch) < Capture.movedShare
        }
        do { try run.shelf?.put(frame.jpeg, timestampMS: frame.timestamp, sameScreen: sameScreen) }
        catch { run.journal.append("Не удалось сохранить кадр: \(error.localizedDescription)") }
    }

    /// The person spoke to the agent, so the app wants the screen as it is now: however little it changed, settled or not.
    /// This goes past every check above and only onto the shelf, and the app sends it for that line.
    private func answerAsk(run: Run) {
        let asked = run.store.screenAsked
        let found: (isNew: Bool, sample: Sample?) = lock.withLock {
            guard runID == run.id, !finished, asked != answeredAsk else { return (false, nil) }
            answeredAsk = asked
            return (true, newestSample)
        }
        guard found.isNew else { return }
        defer { run.store.screenAnswered = asked }
        guard let sample = found.sample, os_proc_available_memory() >= Capture.memoryReserve else { return }
        autoreleasepool {
            let image = upright(sample)
            let timestamp = Int64(sample.capturedAt.timeIntervalSince1970 * 1000)
            guard image.extent.width > 0, image.extent.height > 0, let jpeg = makeSmallJPEG(image) else { return }
            let frame = KeptFrame(jpeg: jpeg, thumbnail: makeThumbnail(image), size: image.extent.size, timestamp: timestamp)
            keep(frame, outside: findWindow(image), run: run)
            scheduleScreenText(jpeg: jpeg, timestamp: timestamp, run: run)
        }
    }

    override func broadcastPaused() { currentJournal?.append("Трансляция приостановлена системой") }

    override func broadcastResumed() {
        lock.withLock {
            previousThumbnail = nil
            stillness.forget()
        }
        currentJournal?.append("Трансляция возобновлена")
    }

    override func broadcastFinished() {
        _ = markFinished(runID: lock.withLock { runID })
    }

    private var currentJournal: SharedJournal? { lock.withLock { journal } }

    private func resetForStart() -> UUID {
        let id = UUID()
        let now = ProcessInfo.processInfo.systemUptime
        let old = lock.withLock { () -> (Task<Void, Never>?, Task<Void, Never>?, DispatchSourceTimer?, DispatchSourceMemoryPressure?) in
            let old = (requestTask, textTask, heartbeat, memoryPressure)
            runID = id
            run = nil
            journal = nil
            finished = false
            finishReported = false
            sent = 0
            skipped = 0
            previousThumbnail = nil
            previousSize = .zero
            lastCheck = -.infinity
            lastStatistics = now
            processingFrame = false
            requestInFlight = false
            requestID = nil
            requestTask = nil
            textInFlight = false
            textTask = nil
            firstTextReadLogged = false
            heldSample = nil
            newestSample = nil
            answeredAsk = 0
            stillness = ScreenStillness()
            screenMoved = false
            windowPatch = nil
            windowLooks = 0
            windowFinds = 0
            windowMisses = 0
            keptFrame = nil
            screenLang = nil
            heartbeat = nil
            memoryPressure = nil
            return old
        }
        old.0?.cancel()
        old.1?.cancel()
        old.2?.cancel()
        old.3?.cancel()
        return id
    }

    private func upload(_ jpeg: Data, sample: Sample, thumbnail: [UInt8], size: CGSize, timestamp: Int64, small: Bool, run: Run) {
        let ticket = UUID()
        let reserved = lock.withLock { () -> Bool in
            guard runID == run.id, !finished, !requestInFlight else { return false }
            requestInFlight = true
            requestID = ticket
            return true
        }
        guard reserved else { return }
        let task = Task { [self] in
            do {
                let reply = try await run.client.sendFrame(jpeg, sessionID: run.sessionID, width: Int(size.width), height: Int(size.height), timestampMS: timestamp, small: small)
                if let lang = reply.lang { lock.withLock { if runID == run.id { screenLang = lang } } }
                // Any decoded server response confirms delivery, including all skip reasons.
                frameCompleted(delivered: true, thumbnail: thumbnail, size: size, runID: run.id)
            } catch {
                // A refused frame goes out again, or a still screen would stay unseen.
                frameCompleted(delivered: false, thumbnail: thumbnail, size: size, runID: run.id)
                lock.withLock {
                    if runID == run.id, !finished, heldSample == nil { heldSample = sample }
                }
                if !Task.isCancelled { run.journal.append("Ошибка отправки кадра: \(error.localizedDescription)") }
                if error as? ClientError == .http(404) || error as? ClientError == .http(401) {
                    stopForError(error, runID: run.id)
                }
            }
            completeRequest(runID: run.id, ticket: ticket)
        }
        installRequest(task, runID: run.id, ticket: ticket)
        scheduleScreenText(jpeg: jpeg, timestamp: timestamp, run: run)
    }

    private func installRequest(_ task: Task<Void, Never>, runID id: UUID, ticket: UUID) {
        let cancel = lock.withLock { () -> Bool in
            guard runID == id, requestID == ticket else { return true }
            requestTask = task
            return finished
        }
        if cancel { task.cancel() }
    }

    private func scheduleScreenText(jpeg: Data, timestamp: Int64, run: Run) {
        let mode = run.store.screenTextMode
        guard mode != .off else { return }
        let reserved = lock.withLock { () -> Bool in
            guard runID == run.id, !finished, !textInFlight else { return false }
            textInFlight = true
            return true
        }
        guard reserved else { return }
        if mode == .app {
            mailboxQueue.async { [self] in
                defer { completeText(runID: run.id) }
                guard isActive(runID: run.id), run.store.sessionID == run.sessionID,
                      run.store.screenTextMode == .app else { return }
                do {
                    guard let mailbox = run.mailbox else { throw BroadcastError.noMailbox }
                    try mailbox.publish(ScreenTextFrame(sessionID: run.sessionID, frameTS: timestamp, jpeg: jpeg))
                } catch { run.journal.append("Ошибка передачи кадра приложению: \(error.localizedDescription)") }
            }
        } else {
            let task = Task { [self] in
                defer { completeText(runID: run.id) }
                guard isActive(runID: run.id), !Task.isCancelled else { return }
                let level: ScreenTextLevel = mode == .extensionFast ? .fast : .accurate
                let lang = lock.withLock { screenLang }
                let reading = await textWorker.read(jpegData: jpeg, screenLang: lang, level: level,
                    canRead: { [self] in
                        guard isActive(runID: run.id), run.store.sessionID == run.sessionID,
                              run.store.screenTextMode == mode else { return false }
                        guard os_proc_available_memory() >= Capture.textMemoryReserve else {
                            run.journal.append("Текст в расширении пропущен: доступно менее 16 МБ")
                            return false
                        }
                        return true
                    }, onDiagnostic: { run.journal.append($0) }) { [self] in
                    let first = lock.withLock { () -> Bool in
                        guard runID == run.id, !firstTextReadLogged else { return false }
                        firstTextReadLogged = true
                        return true
                    }
                    if first { run.journal.append("Первое чтение текста в расширении начинается; \(level.rawValue); phys_footprint: \(ProcessMemory.description) МБ") }
                }
                guard !reading.skipped else { return }
                run.journal.append("Текст в расширении: \(reading.diagnostic); язык: \(lang ?? "сам")")
                guard reading.error == nil, !Task.isCancelled, isActive(runID: run.id), run.store.screenTextMode == mode else { return }
                do { _ = try await run.client.sendScreenText(reading.lines, sessionID: run.sessionID, frameTS: timestamp) }
                catch { if !Task.isCancelled { run.journal.append("Ошибка /text в расширении: \(error.localizedDescription)") } }
            }
            let cancel = lock.withLock { () -> Bool in
                guard runID == run.id, !finished else { return true }
                textTask = task
                return false
            }
            if cancel { task.cancel() }
        }
    }

    private func completeText(runID id: UUID) {
        lock.withLock {
            if runID == id { textInFlight = false; textTask = nil }
        }
    }

    private func isActive(runID id: UUID) -> Bool { lock.withLock { runID == id && !finished } }

    private func makeThumbnail(_ image: CIImage) -> [UInt8] {
        let side = Capture.thumbnailSide
        let small = image.transformed(by: CGAffineTransform(scaleX: CGFloat(side) / image.extent.width, y: CGFloat(side) / image.extent.height))
        var bytes = [UInt8](repeating: 0, count: side * side)
        bytes.withUnsafeMutableBytes { buffer in
            if let address = buffer.baseAddress {
                context.render(small, toBitmap: address, rowBytes: side,
                               bounds: CGRect(x: 0, y: 0, width: side, height: side), format: .L8, colorSpace: graySpace)
            }
        }
        return bytes
    }

    /// A missed look keeps the last place: a window that seems to jump would count as a change.
    private func findWindow(_ image: CIImage) -> ScreenPatch? {
        let scale = CGFloat(WindowFinder.longSide) / max(image.extent.width, image.extent.height)
        let width = Int((image.extent.width * scale).rounded())
        let height = Int((image.extent.height * scale).rounded())
        let small = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        bytes.withUnsafeMutableBytes { buffer in
            if let address = buffer.baseAddress {
                context.render(small, toBitmap: address, rowBytes: width * 4,
                               bounds: CGRect(x: 0, y: 0, width: width, height: height), format: .RGBA8, colorSpace: colorSpace)
            }
        }
        let found = WindowFinder.find(bytes, width: width, height: height, side: Capture.thumbnailSide)
        return lock.withLock {
            windowLooks += 1
            if let found {
                windowFinds += 1
                windowMisses = 0
                windowPatch = found
            } else {
                windowMisses += 1
                if windowMisses >= Capture.windowGoneAfter { windowPatch = nil }
            }
            return windowPatch
        }
    }

    private func makeJPEG(_ image: CIImage) -> Data? {
        guard let cgImage = context.createCGImage(image, from: image.extent, format: .RGBA8, colorSpace: colorSpace) else { return nil }
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(destination, cgImage, [kCGImageDestinationLossyCompressionQuality: Capture.jpegQuality] as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { return nil }
        return data as Data
    }

    private func skipFrame(runID id: UUID, clearThumbnail: Bool = false) {
        lock.withLock {
            guard runID == id else { return }
            skipped += 1
            if clearThumbnail { previousThumbnail = nil }
        }
    }

    private func frameCompleted(delivered: Bool, thumbnail: [UInt8], size: CGSize, runID id: UUID) {
        lock.withLock {
            guard runID == id, !finished else { return }
            if delivered {
                sent += 1
                previousThumbnail = thumbnail
                previousSize = size
                screenMoved = false
            } else { skipped += 1 }
        }
    }

    private func completeRequest(runID id: UUID, ticket: UUID) {
        let shouldFinish = lock.withLock { () -> Bool in
            guard runID == id, requestID == ticket else { return false }
            requestInFlight = false
            requestID = nil
            requestTask = nil
            return finished
        }
        if shouldFinish { reportFinished(runID: id) }
    }

    private func markFinished(runID id: UUID) -> Bool {
        let stoppingRun = lock.withLock { runID == id ? run : nil }
        let state = lock.withLock { () -> (Bool, Bool, Task<Void, Never>?, Task<Void, Never>?, DispatchSourceTimer?, DispatchSourceMemoryPressure?, SharedJournal?, Int, Int) in
            guard runID == id, !finished else { return (false, false, nil, nil, nil, nil, nil, 0, 0) }
            finished = true
            previousThumbnail = nil
            heldSample = nil
            newestSample = nil
            keptFrame = nil
            let state = (true, !requestInFlight, requestTask, textTask, heartbeat, memoryPressure, journal, sent, skipped)
            heartbeat = nil
            memoryPressure = nil
            return state
        }
        guard state.0 else { return false }
        state.2?.cancel()
        state.3?.cancel()
        state.4?.cancel()
        state.5?.cancel()
        state.6?.append("Стоп; кадров доставлено: \(state.7), пропущено: \(state.8)")
        if state.1 { reportFinished(runID: id) }
        if let stoppingRun {
            // Drain earlier writes before removing the frame.
            mailboxQueue.sync { [self] in
                guard lock.withLock({ runID == id && finished }) else { return }
                do { try stoppingRun.mailbox?.clear() }
                catch { stoppingRun.journal.append("Не удалось удалить общий кадр: \(error.localizedDescription)") }
                stoppingRun.shelf?.clear()
            }
        }
        return true
    }

    private func reportFinished(runID id: UUID) {
        let context = lock.withLock { () -> Run? in
            guard runID == id, !finishReported, !requestInFlight, let run else { return nil }
            finishReported = true
            requestInFlight = true
            return run
        }
        guard let run = context else { return }
        Task {
            do {
                _ = try await run.client.broadcast(.finished, sessionID: run.sessionID)
                run.journal.append("Сервер уведомлён: finished")
            } catch { run.journal.append("Ошибка finished: \(error.localizedDescription)") }
        }
    }

    private func stopForError(_ error: Error, runID: UUID) {
        if markFinished(runID: runID) { finishBroadcastWithError(Self.shown(error)) }
    }

    // iOS puts this into its own alert, and only a plain NSError brings its text across to the system.
    private static func shown(_ error: Error) -> NSError {
        NSError(domain: "OjO", code: 1, userInfo: [NSLocalizedDescriptionKey: error.localizedDescription])
    }

    private func startMonitoring(run: Run) {
        let queue = DispatchQueue(label: "broadcast.monitor")
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 0.25, repeating: 0.25)
        timer.setEventHandler { [weak self] in self?.monitor(run: run) }
        timer.resume()
        let pressure = DispatchSource.makeMemoryPressureSource(eventMask: [.warning, .critical], queue: queue)
        pressure.setEventHandler { [weak self] in
            guard let self, self.isActive(runID: run.id) else { return }
            self.lock.withLock {
                if self.runID == run.id, !self.finished {
                    self.previousThumbnail = nil
                    self.heldSample = nil
                    self.newestSample = nil
                }
            }
            self.context.clearCaches()
            run.journal.append("Предупреждение о памяти: кэш CIContext очищен")
        }
        pressure.resume()
        let keep = lock.withLock { () -> Bool in
            guard runID == run.id, !finished else { return false }
            heartbeat = timer
            memoryPressure = pressure
            return true
        }
        if !keep { timer.cancel(); pressure.cancel() }
    }

    private func monitor(run: Run) {
        let now = ProcessInfo.processInfo.systemUptime
        let statistics = lock.withLock { () -> (Bool, Int, Int, Bool, String)? in
            guard runID == run.id, !finished else { return nil }
            let report = now - lastStatistics >= 5
            if report { lastStatistics = now }
            return (report, sent, skipped, requestInFlight, "; окно на кадрах: \(windowFinds) из \(windowLooks)")
        }
        guard let statistics else { return }
        answerAsk(run: run)
        sendHeldSample()
        if run.store.sessionID != run.sessionID {
            run.journal.append("Сессия закрыта приложением, останавливаем трансляцию")
            stopForError(BroadcastError.ended, runID: run.id)  // Apple has no stop without this system alert.
        } else if statistics.0 {
            let availableMB = os_proc_available_memory() / 1_048_576
            run.journal.append("Кадров доставлено: \(statistics.1), пропущено: \(statistics.2); запрос кадра в полёте: \(statistics.3 ? "да" : "нет"); доступно памяти: \(availableMB) МБ\(statistics.4)")
        }
    }

    private enum BroadcastError: LocalizedError {
        case noSession, ended, noMailbox
        var errorDescription: String? {
            switch self {
            case .noSession: return "Нет активного звонка. Откройте приложение и нажмите «Позвонить», затем включите трансляцию."
            case .ended: return "Звонок завершён"
            case .noMailbox: return "Файл кадра в общей группе недоступен."
            }
        }
    }
}
