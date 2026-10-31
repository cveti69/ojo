import AVFAudio
import Combine
import Shared
import UIKit

enum CallPhase {
    case idle, busy, listening, needsGoal, needsScreen, guiding
}

@MainActor
final class CallViewModel: ObservableObject {
    @Published var goal = ""
    @Published private(set) var recognizedText = ""
    @Published private(set) var isCallActive = false
    @Published private(set) var isWorking = false
    @Published private(set) var isRecording = false
    @Published private(set) var isTestingWindow = false
    @Published private(set) var hasGoal = false
    @Published private(set) var isSharing = false
    @Published private(set) var needsUserConfirm = false
    @Published private(set) var audioIsActive = false
    @Published private(set) var isClipboardTestPending = false
    @Published private(set) var isTextTestPending = false
    @Published private(set) var screenTextMode: ScreenTextMode = .extension
    @Published private(set) var useVideoCallFallback = false
    @Published private(set) var hearsDuringCall = true
    @Published private(set) var status = "Позвоните и скажите, что нужно. Нажимаете всегда вы."
    @Published private(set) var lastInstruction = "Подсказок пока нет"
    @Published private(set) var windowState: HintWindowState = .inactive

    let window: any HintWindowPresenting
    let screenShare = ScreenSharePicker()
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
    private var doneDelivered = false
    private var unwrittenPaste: String?
    private var isLooking = false
    private var microphone: CallMicrophone?
    private var conversation: Task<Void, Never>?
    private var isAnswering = false
    private var heldWords: String?
    private var agentIsSpeaking = false
    private var pauseWords = PauseWords()
    private var pause: Task<Void, Never>?
    private var isFilling = false
    private var subscriptions = Set<AnyCancellable>()

    init(speaker: (any Speaker)? = nil) {
        self.speaker = speaker ?? ServerSpeaker(fallback: SystemSpeaker())
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
        screenTextMode = store?.screenTextMode ?? .extension
        if let configuration, let store, let journal {
            do { textCoordinator = try ScreenTextCoordinator(configuration: configuration, store: store, journal: journal) }
            catch { journal.append("Текст в приложении недоступен: \(error.localizedDescription)") }
        }
        useVideoCallFallback = store?.useVideoCallFallback ?? false
        hearsDuringCall = store?.hearsDuringCall ?? true
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
        recorder.onDiagnostic = { [weak self] in self?.journal?.append($0) }
        self.speaker.onSpeaking = { [weak self] isSpeaking in
            self?.agentIsSpeaking = isSpeaking
            self?.microphone?.agentSpeaks(isSpeaking)
        }
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
        journal?.append("Приложение открыто; iOS \(UIDevice.current.systemVersion); источник окна: \(useVideoCallFallback ? "видеозвонок" : "sample buffer")")
    }

    var phase: CallPhase {
        guard isCallActive else { return isWorking ? .busy : .idle }
        if isRecording { return .listening }
        if isTestingWindow || (hasGoal && isSharing) { return .guiding }
        if hasGoal { return .needsScreen }
        return isWorking ? .busy : .needsGoal
    }

    var windowFailed: Bool {
        if case .failed = windowState { return isCallActive }
        return false
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
                self.speaker.prepare(client: client, journal: self.journal)
                let own = settings.character == .own ? ", \(settings.ownCharacter.count) знаков" : ""
                self.journal?.append("Сессия создана; рассуждения: \(settings.thinking.rawValue); характер: \(settings.character.rawValue)\(own)")
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
            defer {
                if self.workflowID == id {
                    self.isWorking = false
                    self.hearPerson()
                }
            }
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

    func askForScreen() {
        // iOS may want a real tap here, so the big button stays under the system sheet either way.
        journal?.append(screenShare.open() ? "Запрошено окно трансляции" : "Окно трансляции: системная кнопка не найдена")
    }

    func endCall() {
        let endingClient = client
        let endingID = sessionID
        _ = newWorkflow()
        recorder.cancel()
        stopHearing()
        pause?.cancel()
        isFilling = false
        speaker.finish()
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
        isSharing = false
        needsUserConfirm = false
        seenSteps.removeAll()
        doneDelivered = false
        unwrittenPaste = nil
        isLooking = false
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

    func setHearsDuringCall(_ value: Bool) {
        hearsDuringCall = value
        store?.hearsDuringCall = value
        if value { hearPerson() } else { stopHearing() }
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

    func sceneChanged(_ name: String, isBackground: Bool, isActive: Bool) {
        journal?.append("Сцена: \(name); аудиосессия активна: \(audioIsActive ? "да" : "нет")")
        if isActive, let text = unwrittenPaste { placeOnClipboard(text, reason: "Буфер при возврате") }
        if isActive { hearPerson() }
        if isBackground, isRecording {
            _ = newWorkflow()
            recorder.cancel()
            isRecording = false
            isWorking = false
            status = "Вернитесь в приложение, чтобы произнести цель, или введите её текстом"
        }
    }

    private func placeOnClipboard(_ text: String, reason: String) {
        // With no window iOS can refuse a write from the background, so the text waits for the person to come back.
        unwrittenPaste = ClipboardWriter.write(text, reason: reason, journal: journal) ? nil : text
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
        var text = try await listen(client: client)
        if text.isEmpty {
            await speaker.speakAndWait("Не слышу, повтори, пожалуйста.")
            try Task.checkCancellation()
            text = try await listen(client: client)
        }
        guard !text.isEmpty else { throw GoalError.empty }
        recognizedText = text
        goal = text
        journal?.append("Цель распознана; отправляем /goal")
        try await sendGoal(text, client: client, sessionID: sessionID)
    }

    private func listen(client: ServerClient) async throws -> String {
        guard UIApplication.shared.applicationState == .active else {
            throw GoalError.foregroundRequired
        }
        isRecording = true
        status = "Слушаю. Скажите, что нужно"
        journal?.append("Запись цели: WAV, 16 кГц, моно, 16 бит")
        let wavURL: URL
        do { wavURL = try await recordGoal(client: client) }
        catch { isRecording = false; throw error }
        defer { try? FileManager.default.removeItem(at: wavURL) }
        isRecording = false
        try Task.checkCancellation()
        status = "Распознаю цель…"
        let transcriber: any GoalTranscribing = FallbackGoalTranscriber(
            primary: ServerGoalTranscriber(client: client), fallback: SpeechGoalTranscriber(), journal: journal
        )
        let started = Date()
        let text = try await transcriber.transcribe(wavURL: wavURL).trimmingCharacters(in: .whitespacesAndNewlines)
        try Task.checkCancellation()
        let seconds = String(format: "%.1f", locale: Locale(identifier: "ru_RU"), Date().timeIntervalSince(started))
        journal?.append("Распознавание \(seconds) с; \(text.isEmpty ? "пусто" : "есть текст")")
        return text
    }

    private func recordGoal(client: ServerClient) async throws -> URL {
        // Recording was proven on the iPhone without the voice engine next to it.
        speaker.finish()
        microphone?.stop()
        defer { if isCallActive { speaker.prepare(client: client, journal: journal) } }
        return try await recorder.record()
    }

    private func sendGoal(_ text: String, client: ServerClient, sessionID: String) async throws {
        status = "Передаю цель агенту…"
        doneDelivered = false
        unwrittenPaste = nil
        let reply = try await client.setGoal(text, sessionID: sessionID)
        try Task.checkCancellation()
        if let ready = reply.readyText { placeOnClipboard(ready, reason: "Буфер заранее") }
        hasGoal = true
        needsUserConfirm = false
        status = "Включите трансляцию экрана и перейдите в нужное приложение"
        lastInstruction = reply.replyRU
        lastSpokenHint = reply.replyRU
        _ = await AVAudioApplication.requestRecordPermission()  // Only a typed goal has not asked yet.
        hearPerson()
        speaker.speak(reply.replyRU)
        if speaksRussian { speaker.keep(PauseWords.all) }
        journal?.append("Цель принята; ответ произнесён; можно включать трансляцию")
    }

    private func hearPerson() {
        guard isCallActive, hasGoal, !isRecording, hearsDuringCall, let client, let sessionID else { return }
        (microphone ?? makeMicrophone(client: client, sessionID: sessionID)).start()
    }

    private func makeMicrophone(client: ServerClient, sessionID: String) -> CallMicrophone {
        let microphone = CallMicrophone()
        microphone.onDiagnostic = { [weak self] in self?.journal?.append($0) }
        microphone.onLost = { [weak self] in
            self?.announce("Я перестал тебя слышать. Открой наше приложение на секунду, и снова услышу.")
        }
        self.microphone = microphone
        conversation = Task { [weak self] in
            for await phrase in microphone.phrases {
                await self?.answer(phrase, client: client, sessionID: sessionID)
            }
        }
        return microphone
    }

    private func stopHearing() {
        conversation?.cancel()
        conversation = nil
        microphone?.stop()
        microphone = nil
        isAnswering = false
        heldWords = nil
    }

    private func answer(_ phrase: Phrase, client: ServerClient, sessionID: String) async {
        let seconds = String(format: "%.1f", locale: Locale(identifier: "ru_RU"), phrase.seconds)
        journal?.append("Реплика \(seconds) с; фон \(Int(phrase.noiseFloor)) дБ, пик \(Int(phrase.loudest)) дБ")
        fillPause(.thinking, after: 2.5)
        let started = Date()
        let id = workflowID
        var words: String?
        do {
            let reply = try await client.say(phrase.wav, sessionID: sessionID)
            guard !Task.isCancelled, workflowID == id else { return }  // A new goal went in meanwhile.
            let elapsed = Int(Date().timeIntervalSince(started) * 1000)
            journal?.append("Ответ на реплику через \(elapsed) мс; распознавание \(reply.timings.speechMS) мс, модель \(reply.timings.modelMS) мс; \(reply.replyRU == nil ? "промолчал" : "ответил")\(reply.goal == nil ? "" : "; цель изменена")")
            if let newGoal = reply.goal {
                goal = newGoal
                doneDelivered = false
                unwrittenPaste = nil
                needsUserConfirm = false
            }
            words = reply.replyRU
        } catch {
            guard !Task.isCancelled else { return }
            journal?.append("Ошибка реплики: \(error.localizedDescription)")
            words = "Не получилось ответить, повтори, пожалуйста."
        }
        keepQuiet()
        guard let words, !isRecording else { return }
        isAnswering = true
        await speaker.waitUntilQuiet()
        guard isAnswering else { return }
        status = words
        await speaker.speakAndWait(words)
        guard isAnswering else { return }
        isAnswering = false
        speakHeldWords()
    }

    /// A hint waits for the end of an answer, so the agent never cuts itself off.
    private func announce(_ text: String) {
        if isAnswering || isFilling { heldWords = text } else { speaker.speak(text) }
    }

    private func speakHeldWords() {
        guard !isAnswering, !isFilling, let held = heldWords else { return }
        heldWords = nil
        speaker.speak(held)
    }

    /// A short word while the agent works, so the line never goes dead. It only ever fills silence.
    private func fillPause(_ kind: PauseWords.Kind, after seconds: Double) {
        guard !isFilling, speaksRussian else { return }
        pause?.cancel()
        pause = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            guard let self, !Task.isCancelled, self.isCallActive, !self.agentIsSpeaking, !self.isAnswering, !self.isRecording,
                  let words = self.pauseWords.next(kind) else { return }
            self.isFilling = true
            self.journal?.append("Фраза на паузе")
            await self.speaker.speakAndWait(words)
            self.isFilling = false
            self.speakHeldWords()
        }
    }

    private var speaksRussian: Bool { store?.settings.userLang == "ru" }

    private func keepQuiet() {
        if !isFilling { pause?.cancel() }
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
            keepQuiet()
            isSharing = true
            if let timestamp = hint.frameTS {
                let elapsed = Date().timeIntervalSince1970 - Double(timestamp) / 1000
                let seconds = String(format: "%.1f", locale: Locale(identifier: "ru_RU"), elapsed)
                let timings = hint.timings.map {
                    "; очередь \($0.queueMS) мс, модель \($0.modelMS) мс, рамка \($0.boxMS) мс, \($0.boxSource)"
                } ?? ""
                journal?.append("Подсказка \(hint.stepID) через \(seconds) с после кадра\(timings)")
            }
            if let text = hint.pasteText { placeOnClipboard(text, reason: "Буфер из hint") }
            lastInstruction = hint.instructionRU
            needsUserConfirm = hint.needsUserConfirm
            status = hint.needsUserConfirm ? "Требуется ваше подтверждение в приложении" : "Новая подсказка"
            lastSpokenHint = hint.instructionRU
            if hint.needsUserConfirm { lastSpokenHint += " Подтвердите действие самостоятельно в приложении." }
            if hint.speak || hint.needsUserConfirm { announce(lastSpokenHint) }
            do {
                guard let data = Data(base64Encoded: hint.imageJPEGBase64) else { throw GoalError.image }
                try window.display(jpegData: data)
                lastJPEG = data
                isLooking = false
                journal?.append("Показана подсказка; подтверждение: \(hint.needsUserConfirm ? "да" : "нет")")
            } catch { journal?.append("Ошибка картинки подсказки: \(error.localizedDescription)") }
        case .status(let status):
            if !isRecording && !isWorking { self.status = status.textRU }
            journal?.append("Сервер: \(status.state); \(status.textRU)")
            updateLookingCard(status)
            updateSharing(status)
            let firstLook = status.state == "analyzing" && seenSteps.isEmpty
            if status.newScreen == true || firstLook { fillPause(.looking, after: 1.5) }
        case .done(let done):
            guard !doneDelivered else { return }  // The server repeats it on every reconnect.
            doneDelivered = true
            keepQuiet()
            status = done.textRU
            lastInstruction = done.textRU
            lastSpokenHint = done.textRU
            needsUserConfirm = false
            announce(done.textRU)
            isLooking = false
            if let card = TestFrameFactory.make(title: "Готово").jpegData(compressionQuality: 0.9) {
                lastJPEG = card
                try? window.display(jpegData: card)
            }
            journal?.append("Сервер сообщил о завершении цели")
        }
    }

    private func updateLookingCard(_ status: StatusEvent) {
        // The highlighted button belongs to a screen the person has left, so it goes until the next hint comes.
        if status.newScreen == true, !isLooking {
            isLooking = (try? window.display(image: TestFrameFactory.make(title: "Смотрю\nэкран…"))) != nil
        } else if status.state != "analyzing", isLooking {
            isLooking = false
            try? restoreWindowImage()
        }
    }

    private func updateSharing(_ status: StatusEvent) {
        if status.state == "broadcast_stopped" {
            isSharing = false
        } else if ["broadcasting", "analyzing"].contains(status.state), !isSharing {
            isSharing = true
            lastInstruction = "Открой нужное приложение"
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
            microphone?.stop()
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
            hearPerson()
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
        conversation?.cancel()
        pause?.cancel()
        clipboardTask?.cancel()
        textTestTask?.cancel()
    }
}
