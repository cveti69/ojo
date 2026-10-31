import CoreImage
import ImageIO
import os
import ReplayKit
import Shared
import UniformTypeIdentifiers

final class SampleHandler: RPBroadcastSampleHandler, @unchecked Sendable {
    private enum Capture {
        static let interval: TimeInterval = 0.7
        static let maximumDimension: CGFloat = 1000
        static let jpegQuality = 0.6
        static let thumbnailSide = 64
        static let changeThreshold = 0.025
        static let memoryReserve = 8 * 1024 * 1024
        static let textMemoryReserve = 16 * 1024 * 1024
    }

    private struct Run: Sendable {
        let id: UUID
        let sessionID: String
        let store: SharedStore
        let journal: SharedJournal
        let client: ServerClient
        let mailbox: ScreenTextMailbox?
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
    private var textPaused = false
    private var heldSample: Sample?
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
                          client: ServerClient(settings: settings), mailbox: try? ScreenTextMailbox(configuration: configuration))
            let ticket = UUID()
            lock.withLock {
                self.run = run
                requestInFlight = true
                requestID = ticket
            }
            journal.append("Старт; только видео, 700 мс, JPEG 0,6, длинная сторона \(Int(Capture.maximumDimension)) px; текст: \(store.screenTextMode.rawValue)")
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
            var image = CIImage(cvPixelBuffer: sample.buffer)
            if let orientation = sample.orientation { image = image.oriented(orientation) }
            image = image.transformed(by: CGAffineTransform(translationX: -image.extent.minX, y: -image.extent.minY))
            let size = image.extent.size
            guard size.width > 0, size.height > 0 else { return }
            let thumbnail = makeThumbnail(image)
            if size == check.previousSize, let previous = check.previousThumbnail,
               difference(thumbnail, previous) < Capture.changeThreshold {
                skipFrame(runID: check.run.id)
                return
            }
            let scale = min(1, Capture.maximumDimension / max(size.width, size.height))
            let bounds = CGRect(x: 0, y: 0, width: floor(size.width * scale), height: floor(size.height * scale))
            let resized = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale)).cropped(to: bounds)
            guard let jpeg = makeJPEG(resized) else {
                skipFrame(runID: check.run.id)
                check.run.journal.append("Не удалось сжать кадр")
                return
            }
            upload(jpeg, sample: sample, thumbnail: thumbnail, size: size, timestamp: timestamp, run: check.run)
        }
    }

    override func broadcastPaused() { currentJournal?.append("Трансляция приостановлена системой") }

    override func broadcastResumed() {
        lock.withLock { previousThumbnail = nil }
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
            textPaused = false
            heldSample = nil
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

    private func upload(_ jpeg: Data, sample: Sample, thumbnail: [UInt8], size: CGSize, timestamp: Int64, run: Run) {
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
                let reply = try await run.client.sendFrame(jpeg, sessionID: run.sessionID, width: Int(size.width), height: Int(size.height), timestampMS: timestamp)
                // With the goal reached or waiting for the person, nothing reads this text.
                lock.withLock { textPaused = reply.reason == "done" || reply.reason == "user_confirm" }
                // Any decoded server response confirms delivery, including all skip reasons.
                frameCompleted(delivered: true, thumbnail: thumbnail, size: size, runID: run.id)
            } catch {
                // A refused frame goes out again, or a still screen would stay unseen after the goal arrives.
                frameCompleted(delivered: false, thumbnail: thumbnail, size: size, runID: run.id)
                lock.withLock { if runID == run.id, !finished, heldSample == nil { heldSample = sample } }
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
            guard runID == run.id, !finished, !textInFlight, !textPaused else { return false }
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
                let reading = await textWorker.read(jpegData: jpeg, targetLang: run.client.settings.targetLang, level: level,
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
                run.journal.append("Текст в расширении: \(reading.diagnostic)")
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

    private func difference(_ left: [UInt8], _ right: [UInt8]) -> Double {
        let sum = zip(left, right).reduce(0) { $0 + abs(Int($1.0) - Int($1.1)) }
        return Double(sum) / Double(left.count * 255)
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

    /// A finished call is no error, yet Apple offers only a stop with an alert. An empty error stops with none. That is
    /// not documented, so the alert stays as the way out if the broadcast is still alive a moment later.
    private func stopQuietly(run: Run) {
        guard markFinished(runID: run.id) else { return }
        run.journal.append("Трансляция: стоп без окна iOS")
        _ = perform(NSSelectorFromString("finishBroadcastWithError:"), with: nil)
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [self] in
            guard lock.withLock({ runID == run.id }) else { return }
            run.journal.append("Трансляция: ещё жива, стоп с окном «Звонок завершён»")
            finishBroadcastWithError(Self.shown(BroadcastError.ended))
        }
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
        let statistics = lock.withLock { () -> (Bool, Int, Int, Bool)? in
            guard runID == run.id, !finished else { return nil }
            let report = now - lastStatistics >= 5
            if report { lastStatistics = now }
            return (report, sent, skipped, requestInFlight)
        }
        guard let statistics else { return }
        sendHeldSample()
        if run.store.sessionID != run.sessionID {
            run.journal.append("Сессия закрыта приложением, останавливаем трансляцию")
            stopQuietly(run: run)
        } else if statistics.0 {
            let availableMB = os_proc_available_memory() / 1_048_576
            run.journal.append("Кадров доставлено: \(statistics.1), пропущено: \(statistics.2); запрос кадра в полёте: \(statistics.3 ? "да" : "нет"); доступно памяти: \(availableMB) МБ")
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
