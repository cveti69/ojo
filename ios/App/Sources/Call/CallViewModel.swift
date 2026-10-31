import AVFAudio
import Combine
import Shared
import UIKit

@MainActor
final class CallViewModel: ObservableObject {
    @Published var goal = ""
    @Published private(set) var recognizedText = ""
    @Published private(set) var isCallActive = false
    @Published private(set) var isWorking = false
    @Published private(set) var isRecording = false
    @Published private(set) var isTestingWindow = false
    @Published private(set) var hasGoal = false
    @Published private(set) var needsUserConfirm = false
    @Published private(set) var audioIsActive = false
    @Published private(set) var isClipboardTestPending = false
    @Published private(set) var isTextTestPending = false
    @Published private(set) var screenTextMode: ScreenTextMode = .app
    @Published private(set) var useVideoCallFallback = false
    @Published private(set) var status = "Готов к звонку"
    @Published private(set) var lastInstruction = "Подсказок пока нет"
    @Published private(set) var windowState: HintWindowState = .inactive

    let window: any HintWindowPresenting
    let configuration: ProjectConfiguration?
    let store: SharedStore?
    let journal: SharedJournal?
    var displayName: String { configuration?.displayName ?? "Звонок" }

    private let audioSession = CallAudioSession()
    private let recorder = GoalAudioRecorder()
    private let speaker: any Speaker
    private var client: ServerClient?
    private var sessionID: String?
    private var connection: HintEventConnection?
    private var workflow: Task<Void, Never>?
    private var workflowID = UUID()
    private var clipboardTask: Task<Void, Never>?
    private var clipboardBackgroundTask: UIBackgroundTaskIdentifier = .invalid
    private var textTestTask: Task<Void, Never>?
    private var textCoordinator: ScreenTextCoordinator?
    private var startWindowWhenReady = false
    private var testTimer: Timer?
    private var frameNumber = 0
    private var lastJPEG: Data?
    private var lastSpokenHint = ""
    private var seenSteps = Set<String>()
    private var subscriptions = Set<AnyCancellable>()

    init(speaker: (any Speaker)? = nil) {
        self.speaker = speaker ?? SystemSpeaker()
        window = PictureInPictureHintWindow()
        var configuration: ProjectConfiguration?
        var store: SharedStore?
        var journal: SharedJournal?
        do {
            let value = try ProjectConfiguration()
            configuration = value
            journal = SharedJournal(configuration: value, source: "Приложение")
            store = try SharedStore(configuration: value)
            store?.seedDefaultToken(value.defaultServerToken)
        } catch {
            status = "Ошибка конфигурации: \(error.localizedDescription)"
        }
        self.configuration = configuration
        self.store = store
        self.journal = journal
        screenTextMode = store?.screenTextMode ?? .app
        if let configuration, let store, let journal {
            do { textCoordinator = try ScreenTextCoordinator(configuration: configuration, store: store, journal: journal) }
            catch { journal.append("Текст в приложении недоступен: \(error.localizedDescription)") }
        }
        useVideoCallFallback = store?.useVideoCallFallback ?? false
        window.setVideoCallFallback(useVideoCallFallback)
        window.onStateChange = { [weak self] state in
            guard let self else { return }
            if self.windowState != state { self.journal?.append(state.message) }
            self.windowState = state
            if state == .ready, self.startWindowWhenReady {
                self.startWindowWhenReady = false
                self.window.startPictureInPicture()
            }
        }
        window.onDiagnostic = { [weak self] in self?.journal?.append($0) }
        window.onRepeatHint = { [weak self] in self?.repeatHint() }
        audioSession.onActivityChange = { [weak self] active in
            self?.audioIsActive = active
            self?.journal?.append("Аудиосессия активна: \(active ? "да" : "нет")")
        }
        NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)
            .receive(on: RunLoop.main)
            .sink { [weak self] in self?.handleAudioInterruption($0) }
            .store(in: &subscriptions)
        NotificationCenter.default.publisher(for: AVAudioSession.mediaServicesWereResetNotification)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.resumeAudio() }
            .store(in: &subscriptions)
        journal?.append("Приложение открыто; источник окна: \(useVideoCallFallback ? "видеозвонок" : "sample buffer")")
    }

    func startCall() {
        guard !isCallActive, !isWorking, let store else { return }
        let settings = store.settings
        store.sessionID = nil
        textCoordinator?.prepareForCall(targetLang: settings.targetLang)
        recognizedText = ""
        isWorking = true
        status = "Соединяемся…"
        let id = newWorkflow()
        workflow = Task { [weak self] in
            guard let self else { return }
            defer { if self.workflowID == id { self.isWorking = false } }
            do {
                let client = ServerClient(settings: settings)
                let response = try await client.createSession()
                guard !Task.isCancelled, self.workflowID == id else {
                    self.endRemoteSession(client: client, sessionID: response.sessionID)
                    return
                }
                self.client = client
                self.sessionID = response.sessionID
                store.sessionID = response.sessionID
                try self.beginLocalCall()
                self.journal?.append("Сессия создана")
                self.connect(client: client, sessionID: response.sessionID)
                self.status = "Агент здоровается"
                await self.speaker.speakAndWait("Привет, чем помочь?")
                try Task.checkCancellation()
                try await self.captureAndSendGoal(client: client, sessionID: response.sessionID)
            } catch {
                guard !Task.isCancelled, self.workflowID == id else { return }
                self.isRecording = false
                if !self.isCallActive { self.endCall() }
                self.status = error.localizedDescription
                self.journal?.append("Ошибка звонка или цели: \(error.localizedDescription)")
            }
        }
    }

    func submitGoal() {
        let text = goal.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, isCallActive, !isTestingWindow, let client, let sessionID else { return }
        let id = newWorkflow()
        isWorking = true
        isRecording = false
        recorder.cancel()
        speaker.stop()
        workflow = Task { [weak self] in
            guard let self else { return }
            defer { if self.workflowID == id { self.isWorking = false } }
            do { try await self.sendGoal(text, client: client, sessionID: sessionID) }
            catch {
                guard !Task.isCancelled, self.workflowID == id else { return }
                self.status = error.localizedDescription
                self.journal?.append("Ошибка отправки цели: \(error.localizedDescription)")
            }
        }
    }

    func listenAgain() {
        guard isCallActive, !isTestingWindow, let client, let sessionID else { return }
        let id = newWorkflow()
        isWorking = true
        recorder.cancel()
        speaker.stop()
        workflow = Task { [weak self] in
            guard let self else { return }
            defer { if self.workflowID == id { self.isWorking = false } }
            do { try await self.captureAndSendGoal(client: client, sessionID: sessionID) }
            catch {
                guard !Task.isCancelled, self.workflowID == id else { return }
                self.isRecording = false
                self.status = error.localizedDescription
                self.journal?.append("Ошибка голосовой цели: \(error.localizedDescription)")
            }
        }
    }

    func finishRecording() { recorder.finishNow() }

    func endCall() {
        let endingClient = client
        let endingID = sessionID
        _ = newWorkflow()
        recorder.cancel()
        speaker.stop()
        connection?.stop()
        connection = nil
        if let endingID, store?.sessionID == endingID { store?.sessionID = nil }
        textCoordinator?.stop()
        startWindowWhenReady = false
        testTimer?.invalidate()
        testTimer = nil
        client = nil
        sessionID = nil
        isTestingWindow = false
        isCallActive = false
        isWorking = false
        isRecording = false
        hasGoal = false
        needsUserConfirm = false
        seenSteps.removeAll()
        lastJPEG = nil
        lastSpokenHint = ""
        window.stop()
        deactivateAudio()
        status = "Звонок завершён"
        lastInstruction = "Подсказок пока нет"
        journal?.append("Звонок завершён; расширение получит сигнал остановки через App Group")
        if let endingClient, let endingID {
            endRemoteSession(client: endingClient, sessionID: endingID)
        }
    }

    private func endRemoteSession(client: ServerClient, sessionID: String) {
        let journal = journal
        let activity = BackgroundActivity(name: "End session")
        let task = Task {
            defer { activity.finish() }
            do {
                _ = try await client.end(sessionID: sessionID)
                journal?.append("Сервер подтвердил /end")
            } catch { journal?.append("Ошибка /end: \(error.localizedDescription)") }
        }
        activity.onExpiration = {
            journal?.append("Фоновое время для /end истекло")
            task.cancel()
        }
    }

    func startWindowTest() {
        guard !isTestingWindow else { return }
        if isCallActive || isWorking { endCall() }
        do {
            try beginLocalCall()
            isTestingWindow = true
            frameNumber = 0
            renderNextTestFrame()
            guard isTestingWindow else { return }
            let timer = Timer(timeInterval: 3, repeats: true) { [weak self] _ in
                Task { @MainActor [weak self] in self?.renderNextTestFrame() }
            }
            testTimer = timer
            RunLoop.main.add(timer, forMode: .common)
        } catch {
            endCall()
            status = "Ошибка теста окна: \(error.localizedDescription)"
            journal?.append(status)
        }
    }

    var canOpenWindow: Bool {
        guard isCallActive else { return false }
        if case .failed = windowState { return true }
        return windowState == .ready
    }

    func openWindow() {
        guard isCallActive else { return }
        if case .failed = windowState {
            startWindowWhenReady = true
            window.stop()
            window.prepare()
            do { try restoreWindowImage() }
            catch {
                startWindowWhenReady = false
                journal?.append("Ошибка повторной подготовки окна: \(error.localizedDescription)")
            }
        } else {
            window.prepare()
            window.startPictureInPicture()
        }
    }

    func setVideoCallFallback(_ value: Bool) {
        useVideoCallFallback = value
        store?.useVideoCallFallback = value
        window.setVideoCallFallback(value)
        guard isCallActive else { return }
        window.prepare()
        do {
            try restoreWindowImage()
        } catch { journal?.append("Ошибка смены источника окна: \(error.localizedDescription)") }
    }

    private func restoreWindowImage() throws {
        if isTestingWindow { try displayTestFrame() }
        else if let lastJPEG { try window.display(jpegData: lastJPEG) }
        else { try window.display(image: TestFrameFactory.make()) }
    }

    func setScreenTextMode(_ mode: ScreenTextMode) {
        screenTextMode = mode
        store?.screenTextMode = mode
        textCoordinator?.modeChanged()
        journal?.append("Режим текста экрана: \(mode.rawValue)")
    }

    func testScreenText() {
        guard !isTextTestPending, let textCoordinator else { return }
        let targetLang = store?.settings.targetLang ?? "auto"
        guard let jpeg = TestFrameFactory.makeScreenText(targetLang: targetLang).jpegData(compressionQuality: 0.9) else {
            journal?.append("Тест текста: не удалось нарисовать тестовый кадр")
            return
        }
        isTextTestPending = true
        let deadline = Date().addingTimeInterval(8)
        let activity = BackgroundActivity(name: "Screen text test")
        let journal = journal
        journal?.append("Тест текста: сейчас и через 8 секунд. Перейдите в другое приложение")
        journal?.append("Тест текста: фоновое время \(activity.isGranted ? "предоставлено" : "не предоставлено")")
        textTestTask = Task { [weak self] in
            defer {
                self?.isTextTestPending = false
                self?.textTestTask = nil
                activity.finish()
            }
            await textCoordinator.recognizeTest(jpeg: jpeg, targetLang: targetLang, label: "Тест текста, сразу")
            guard !Task.isCancelled else { return }
            let remaining = max(0, deadline.timeIntervalSinceNow)
            do { try await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000)) } catch { return }
            await textCoordinator.recognizeTest(jpeg: jpeg, targetLang: targetLang, label: "Тест текста, через 8 секунд")
            guard !Task.isCancelled else { return }
            await textCoordinator.recognizeTest(jpeg: jpeg, targetLang: targetLang,
                                                label: "Тест текста, через 8 секунд, только CPU", cpuOnly: true)
        }
        activity.onExpiration = { [weak self] in
            journal?.append("Тест текста: фоновое время истекло")
            self?.textTestTask?.cancel()
        }
    }

    func testClipboard() {
        guard !isClipboardTestPending else { return }
        isClipboardTestPending = true
        journal?.append("Тест буфера: запись через 8 секунд. Перейдите в другое приложение")
        clipboardBackgroundTask = UIApplication.shared.beginBackgroundTask(withName: "Clipboard test") { [weak self] in
            Task { @MainActor [weak self] in
                self?.journal?.append("Тест буфера: система завершила фоновое время до записи")
                self?.clipboardTask?.cancel()
                self?.finishClipboardTest()
            }
        }
        journal?.append("Тест буфера: фоновое время \(clipboardBackgroundTask == .invalid ? "не предоставлено" : "предоставлено")")
        clipboardTask = Task { [weak self] in
            do { try await Task.sleep(nanoseconds: 8_000_000_000) } catch { return }
            guard let self else { return }
            let time = Date().formatted(date: .numeric, time: .standard)
            ClipboardWriter.write("Тест буфера \(time)", reason: "Тест буфера", journal: self.journal)
            self.finishClipboardTest()
        }
    }

    func sceneChanged(_ name: String, isBackground: Bool) {
        journal?.append("Сцена: \(name); аудиосессия активна: \(audioIsActive ? "да" : "нет")")
        if isBackground, isRecording {
            _ = newWorkflow()
            recorder.cancel()
            isRecording = false
            isWorking = false
            status = "Вернитесь в приложение, чтобы произнести цель, или введите её текстом"
        }
    }

    private func newWorkflow() -> UUID {
        workflow?.cancel()
        workflowID = UUID()
        return workflowID
    }

    private func beginLocalCall() throws {
        try audioSession.activate()
        window.prepare()
        try window.display(image: TestFrameFactory.make())
        isCallActive = true
        lastInstruction = "Ожидание подсказки"
    }

    private func captureAndSendGoal(client: ServerClient, sessionID: String) async throws {
        try Task.checkCancellation()
        guard UIApplication.shared.applicationState == .active else {
            throw GoalError.foregroundRequired
        }
        isRecording = true
        status = "Слушаю цель. После фразы помолчите чуть больше секунды"
        journal?.append("Запись цели: WAV, 16 кГц, моно, 16 бит")
        let wavURL: URL
        do { wavURL = try await recorder.record() }
        catch { isRecording = false; throw error }
        defer { try? FileManager.default.removeItem(at: wavURL) }
        isRecording = false
        try Task.checkCancellation()
        status = "Распознаю цель…"
        let transcriber: any GoalTranscribing = FallbackGoalTranscriber(
            primary: ServerGoalTranscriber(client: client), fallback: SpeechGoalTranscriber(), journal: journal
        )
        let text = try await transcriber.transcribe(wavURL: wavURL).trimmingCharacters(in: .whitespacesAndNewlines)
        try Task.checkCancellation()
        guard !text.isEmpty else { throw GoalError.empty }
        recognizedText = text
        goal = text
        journal?.append("Цель распознана; отправляем /goal")
        try await sendGoal(text, client: client, sessionID: sessionID)
    }

    private func sendGoal(_ text: String, client: ServerClient, sessionID: String) async throws {
        status = "Передаю цель агенту…"
        let reply = try await client.setGoal(text, sessionID: sessionID)
        try Task.checkCancellation()
        hasGoal = true
        needsUserConfirm = false
        status = "Включите трансляцию экрана и перейдите в нужное приложение"
        lastInstruction = reply.replyRU
        lastSpokenHint = reply.replyRU
        speaker.speak(reply.replyRU)
        journal?.append("Цель принята; ответ произнесён; можно включать трансляцию")
    }

    private func connect(client: ServerClient, sessionID: String) {
        textCoordinator?.start(client: client, sessionID: sessionID)
        let connection = HintEventConnection(client: client)
        connection.onEvent = { [weak self] in self?.receive($0) }
        connection.onDiagnostic = { [weak self] in self?.journal?.append($0) }
        connection.onSessionExpired = { [weak self] in
            guard let self else { return }
            self.endCall()
            self.status = "Сервер отклонил сессию или токен. Проверьте настройки и позвоните снова"
            self.journal?.append(self.status)
        }
        self.connection = connection
        connection.start(sessionID: sessionID)
    }

    private func receive(_ event: ServerEvent) {
        guard isCallActive, !isTestingWindow else { return }
        switch event {
        case .hint(let hint):
            guard seenSteps.insert(hint.stepID).inserted else { return }
            if let timestamp = hint.frameTS {
                let elapsed = Date().timeIntervalSince1970 - Double(timestamp) / 1000
                let seconds = String(format: "%.1f", locale: Locale(identifier: "ru_RU"), elapsed)
                journal?.append("Подсказка \(hint.stepID) через \(seconds) с после кадра")
            }
            if let text = hint.pasteText {
                ClipboardWriter.write(text, reason: "Буфер из hint", journal: journal)
            }
            lastInstruction = hint.instructionRU
            needsUserConfirm = hint.needsUserConfirm
            status = hint.needsUserConfirm ? "Требуется ваше подтверждение в приложении" : "Новая подсказка"
            lastSpokenHint = hint.instructionRU
            if hint.needsUserConfirm { lastSpokenHint += " Подтвердите действие самостоятельно в приложении." }
            if hint.speak || hint.needsUserConfirm { speaker.speak(lastSpokenHint) }
            do {
                guard let data = Data(base64Encoded: hint.imageJPEGBase64) else { throw GoalError.image }
                try window.display(jpegData: data)
                lastJPEG = data
                journal?.append("Показана подсказка; подтверждение: \(hint.needsUserConfirm ? "да" : "нет")")
            } catch { journal?.append("Ошибка картинки подсказки: \(error.localizedDescription)") }
        case .status(let status):
            if !isRecording && !isWorking { self.status = status.textRU }
            journal?.append("Сервер: \(status.state); \(status.textRU)")
        case .done(let done):
            status = done.textRU
            lastInstruction = done.textRU
            lastSpokenHint = done.textRU
            needsUserConfirm = false
            speaker.speak(done.textRU)
            journal?.append("Сервер сообщил о завершении цели")
        }
    }

    private func repeatHint() {
        guard isCallActive else { return }
        let text = lastSpokenHint.isEmpty ? "Пока нет подсказки. Ожидайте." : lastSpokenHint
        speaker.speak(text)
        journal?.append("Последняя подсказка повторена голосом; аудиосессия активна: \(audioIsActive ? "да" : "нет")")
    }

    private func renderNextTestFrame() {
        guard isCallActive, isTestingWindow else { return }
        frameNumber += 1
        do {
            try displayTestFrame()
            lastInstruction = "Тестовый кадр №\(frameNumber)"
            lastSpokenHint = "Тестовый кадр номер \(frameNumber)"
            status = "Тест окна: новый кадр каждые 3 секунды"
            journal?.append("Тестовая картинка №\(frameNumber); аудиосессия активна: \(audioIsActive ? "да" : "нет")")
        } catch {
            endCall()
            status = "Ошибка теста окна: \(error.localizedDescription)"
            journal?.append(status)
        }
    }

    private func displayTestFrame() throws {
        try autoreleasepool {
            let image = TestFrameFactory.make(number: frameNumber)
            if frameNumber.isMultiple(of: 2) {
                guard let data = image.jpegData(compressionQuality: 0.9) else { throw GoalError.image }
                try window.display(jpegData: data)
            } else { try window.display(image: image) }
        }
    }

    private func finishClipboardTest() {
        isClipboardTestPending = false
        clipboardTask = nil
        if clipboardBackgroundTask != .invalid {
            UIApplication.shared.endBackgroundTask(clipboardBackgroundTask)
            clipboardBackgroundTask = .invalid
        }
    }

    private func handleAudioInterruption(_ notification: Notification) {
        guard isCallActive,
              let rawType = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: rawType) else { return }
        if type == .began {
            audioSession.interrupted()
            _ = newWorkflow()
            recorder.cancel()
            isRecording = false
            isWorking = false
            speaker.stop()
            status = "Звонок прерван системой"
            journal?.append(status)
        } else {
            let options = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            if AVAudioSession.InterruptionOptions(rawValue: options).contains(.shouldResume) { resumeAudio() }
            else {
                endCall()
                status = "Звонок завершён после прерывания"
            }
        }
    }

    private func resumeAudio() {
        guard isCallActive else { return }
        do {
            try audioSession.activate()
            status = isTestingWindow ? "Тест окна: новый кадр каждые 3 секунды" : "Звонок продолжается"
        } catch {
            endCall()
            status = "Не удалось возобновить звонок: \(error.localizedDescription)"
        }
    }

    private func deactivateAudio() {
        do { try audioSession.deactivate() }
        catch { journal?.append("Ошибка выключения аудиосессии: \(error.localizedDescription)") }
    }

    private enum GoalError: LocalizedError {
        case foregroundRequired, empty, image
        var errorDescription: String? {
            switch self {
            case .foregroundRequired: return "Вернитесь в приложение, чтобы произнести цель, или введите её текстом."
            case .empty: return "Цель не распознана. Повторите её или введите текстом."
            case .image: return "Не удалось прочитать картинку подсказки."
            }
        }
    }

    deinit {
        testTimer?.invalidate()
        workflow?.cancel()
        clipboardTask?.cancel()
        textTestTask?.cancel()
    }
}
