import AVFAudio
import Combine
import Shared
import UIKit

enum CallPhase {
    case idle, busy, needsScreen, guiding
}

@MainActor
final class CallViewModel: ObservableObject {
    @Published var typed = ""
    @Published private(set) var isCallActive = false
    @Published private(set) var isWorking = false
    @Published private(set) var isTestingWindow = false
    @Published private(set) var isGreeted = false
    @Published private(set) var isSharing = false
    @Published private(set) var audioIsActive = false
    @Published private(set) var isClipboardTestPending = false
    @Published private(set) var isTextTestPending = false
    @Published private(set) var screenTextMode: ScreenTextMode = .extension
    @Published private(set) var measuresAppleSpeech = false
    @Published private(set) var hearsOverVoice = false
    @Published private(set) var voices: [Voice] = []
    @Published private(set) var voice = ""
    @Published private(set) var frame = EyesFrame.lace
    @Published private(set) var language = ""
    @Published private(set) var character = AgentCharacter.warm
    @Published private(set) var ownCharacter = ""
    @Published private(set) var status = CallWords.device.ready
    @Published private(set) var lastInstruction = CallWords.device.noHints
    @Published private(set) var windowState: HintWindowState = .inactive
    @Published private(set) var callBegan: Date?
    @Published private(set) var lastCall: TimeInterval?
    @Published private(set) var isMuted = false
    @Published private(set) var isNotLooking = false
    @Published private(set) var isLinkDown = false
    @Published private(set) var soundOutput = ""

    let window: any HintWindowPresenting
    let screenShare = ScreenSharePicker()
    let configuration: ProjectConfiguration?
    let store: SharedStore?
    let journal: SharedJournal?
    var displayName: String { configuration?.displayName ?? "Звонок" }
    private(set) var words = CallWords.device  // Taken again at every call, so one call keeps one language.

    /// One thing the agent has to say. Replies go first, and of the hints only the newest is worth saying.
    private struct Line {
        enum Kind { case reply, hint, filler }
        let text: String
        var kind = Kind.reply
        var turn: Int?
        var id: Int?
        var frameTS: Int64?
        var sound: String?  // Sounds straight ahead of the text, as its first sentence, so the voice of the text is asked for at once.
    }

    private static let streamWait: UInt64 = 2_500_000_000  // Past this the server has not heard the phrase, so it goes as a file.
    private static let warmthGap: UInt64 = 20_000_000_000
    private static let screenWait = 1.0
    private static let firstWordAfter = 1.5  // A longer word waits this long: while the agent speaks it cannot hear the person.
    private static let earlySoundAfter = 0.7
    private static let longWait = 3000  // In ms from the end of speech.
    private static let silenceBeforeSound = 1.5
    private static let mostWords = 2  // On one turn, however long the wait.

    private let audioSession = CallAudioSession()
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
    private var unwrittenPaste: String?
    private var microphone: CallMicrophone?
    private var conversation: Task<Void, Never>?
    private var speechStream: SpeechStream?
    private var networkWatch: NetworkWatch?
    private var streaming: Task<Void, Never>?
    private var warmth: Task<Void, Never>?
    private var interruption: Task<Void, Never>?
    private var probe: AppleSpeechProbe?
    private var shelf: FrameShelf?
    private var lines: [Line] = []
    private var waitingHint: Line?
    private var currentLine: Line?
    private var speaking: Task<Void, Never>?
    private var isSounding = false
    private var lastSounded = Date.distantPast
    private var heardTurn = 0
    private var answeredTurn = 0
    private var lastHeard = Date.distantPast
    private var voiceEnded = Date.distantPast
    private var droppedSounds = 0
    private var pauseWords = PauseWords(phrases: CallWords.device.pause, sounds: CallWords.device.sounds)
    private var pause: Task<Void, Never>?
    private var wordsThisTurn = 0
    private var eyes: EyesStage?
    private var movedNotice: GroupNotification?
    private var movedAt: Int64 = 0
    private var hintOnWindow = false
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
        voice = store?.voice ?? ""
        measuresAppleSpeech = store?.measuresAppleSpeech ?? false
        hearsOverVoice = store?.hearsOverVoice ?? false
        frame = EyesFrame(rawValue: store?.windowFrame ?? "") ?? .lace
        language = store?.language ?? ""
        character = store?.character ?? .warm
        ownCharacter = store?.ownCharacter ?? ""
        if let store {
            takeWords(from: store)
            status = words.ready
            lastInstruction = words.noHints
        }
        if let configuration {
            do { shelf = try FrameShelf(configuration: configuration) }
            catch { journal?.append("Полка кадров недоступна: \(error.localizedDescription)") }
            movedNotice = GroupNotification(name: configuration.screenMovedName) { [weak self] in
                Task { @MainActor [weak self] in self?.screenMoved() }
            }
        }
        if let configuration, let store, let journal {
            do { textCoordinator = try ScreenTextCoordinator(configuration: configuration, store: store, journal: journal) }
            catch { journal.append("Текст в приложении недоступен: \(error.localizedDescription)") }
        }
        window.onStateChange = { [weak self] state in
            guard let self else { return }
            if self.windowState != state { self.journal?.append(state.message) }
            let viewChanged = (state == .active) != (self.windowState == .active)
            self.windowState = state
            if viewChanged { self.eyes?.redraw() }  // The view now on show took only what it had room for, and at rest no other picture comes.
            if state == .ready, self.startWindowWhenReady {
                self.startWindowWhenReady = false
                self.window.startPictureInPicture()
            }
        }
        window.onDiagnostic = { [weak self] in self?.journal?.append($0) }
        self.speaker.onSounding = { [weak self] in self?.soundChanged($0) }
        self.speaker.onSentence = { [weak self] in self?.sentenceSounds($0) }
        self.speaker.mayStart = { [weak self] in self?.microphone?.isSpeaking != true }
        window.onRepeatHint = { [weak self] in self?.repeatHint() }
        audioSession.onActivityChange = { [weak self] active in
            self?.audioIsActive = active
            self?.journal?.append("Аудиосессия активна: \(active ? "да" : "нет")")
        }
        audioSession.onDiagnostic = { [weak self] in self?.journal?.append($0) }
        audioSession.onRouteChange = { [weak self] in
            guard let self else { return }
            self.soundOutput = self.audioSession.output(in: self.words)
        }
        NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)
            .receive(on: RunLoop.main)
            .sink { [weak self] in self?.handleAudioInterruption($0) }
            .store(in: &subscriptions)
        NotificationCenter.default.publisher(for: AVAudioSession.mediaServicesWereResetNotification)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.resumeAudio() }
            .store(in: &subscriptions)
        journal?.append("Приложение открыто; сборка \(Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"); iOS \(UIDevice.current.systemVersion); источник окна: \(PictureInPictureHintWindow.usesVideoCall ? "видеозвонок" : "sample buffer")")
    }

    var phase: CallPhase {
        guard isCallActive else { return isWorking ? .busy : .idle }
        if isTestingWindow || isSharing { return .guiding }
        return isGreeted ? .needsScreen : .busy
    }

    var windowFailed: Bool {
        if case .failed = windowState { return isCallActive }
        return false
    }

    func startCall() {
        guard !isCallActive, !isWorking, let store else { return }
        lastCall = nil
        isMuted = false
        soundOutput = audioSession.output(in: words)
        let settings = store.settings
        store.sessionID = nil
        store.notLooking = false
        shelf?.clear()
        if let speaker = speaker as? ServerSpeaker {
            speaker.hearsOverVoice = hearsOverVoice
            speaker.voice = store.voice
        }
        textCoordinator?.prepareForCall(targetLang: settings.targetLang)
        isWorking = true
        takeWords(from: store)
        status = words.connecting
        let id = newWorkflow()
        workflow = Task { [weak self] in
            guard let self else { return }
            defer { if self.workflowID == id { self.isWorking = false } }
            do {
                let client = ServerClient(settings: settings, journal: { [journal = self.journal] in journal?.append($0) })
                let response = try await client.createSession()
                guard !Task.isCancelled, self.workflowID == id else {
                    self.endRemoteSession(client: client, sessionID: response.sessionID)
                    return
                }
                self.client = client
                self.sessionID = response.sessionID
                store.sessionID = response.sessionID
                try self.beginLocalCall(withEyes: true)
                self.speaker.prepare(client: client, journal: self.journal)
                let own = settings.character == .own ? ", \(settings.ownCharacter.count) знаков" : ""
                self.journal?.append("Сессия создана; рассуждения: \(settings.thinking.rawValue); характер: \(settings.character.rawValue)\(own)")
                self.connect(client: client, sessionID: response.sessionID)
                self.status = self.words.greets
                await self.speaker.speakAndWait(self.words.greeting)
                try Task.checkCancellation()
                _ = await AVAudioApplication.requestRecordPermission()
                try Task.checkCancellation()
                self.isGreeted = true
                self.status = self.words.talk
                self.hearPerson()
                self.speaker.keep(self.words.sounds + self.words.pause)
            } catch {
                guard !Task.isCancelled, self.workflowID == id else { return }
                if !self.isCallActive { self.endCall() }
                self.status = error.localizedDescription
                self.journal?.append("Ошибка звонка: \(error.localizedDescription)")
            }
        }
    }

    func sendTyped() {
        let text = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, isCallActive, !isTestingWindow, let client, let sessionID else { return }
        typed = ""
        let journal = journal
        Task {
            do { _ = try await client.words(text, sessionID: sessionID) }
            catch { journal?.append("Ошибка реплики текстом: \(error.localizedDescription)") }
        }
    }

    /// The call and the screen go on: only the person's sound stops, here on the phone.
    func toggleMute() {
        isMuted.toggle()
        microphone?.mute(isMuted)
        eyes?.muted(isMuted)
        journal?.append("Микрофон \(isMuted ? "выключен" : "включён") кнопкой")
    }

    func askForScreen() {
        // iOS may want a real tap here, so the big button stays under the system sheet either way.
        journal?.append(screenShare.open() ? "Запрошено окно трансляции" : "Окно трансляции: системная кнопка не найдена")
    }

    func endCall() {
        let endingClient = client
        let endingID = sessionID
        _ = newWorkflow()
        noteDroppedSounds()
        stopHearing()
        pause?.cancel()
        speaking?.cancel()
        speaking = nil
        lines.removeAll()
        waitingHint = nil
        currentLine = nil
        speaker.finish()
        connection?.stop()
        connection = nil
        networkWatch?.stop()
        networkWatch = nil
        warmth?.cancel()
        warmth = nil
        shelf?.clear()
        heardTurn = 0
        answeredTurn = 0
        lastHeard = .distantPast
        droppedSounds = 0
        movedAt = 0
        hintOnWindow = false
        if let endingID, store?.sessionID == endingID { store?.sessionID = nil }
        store?.notLooking = false
        isNotLooking = false
        isLinkDown = false
        textCoordinator?.stop()
        startWindowWhenReady = false
        testTimer?.invalidate()
        testTimer = nil
        client = nil
        sessionID = nil
        isTestingWindow = false
        isCallActive = false
        isWorking = false
        isGreeted = false
        isSharing = false
        if let callBegan { lastCall = Date().timeIntervalSince(callBegan) }
        callBegan = nil
        seenSteps.removeAll()
        unwrittenPaste = nil
        lastJPEG = nil
        lastSpokenHint = ""
        eyes?.stop()
        eyes = nil
        window.stop()
        deactivateAudio()
        status = words.ended
        lastInstruction = words.noHints
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

    private func restoreWindowImage() throws {
        if isTestingWindow { try displayTestFrame() }
        else if let eyes { eyes.redraw() }
        else if let lastJPEG { try window.display(jpegData: lastJPEG) }
        else { try window.display(image: TestFrameFactory.make(title: words.waitingHint)) }
    }

    func setMeasuresAppleSpeech(_ value: Bool) {
        measuresAppleSpeech = value
        store?.measuresAppleSpeech = value
    }

    func setHearsOverVoice(_ value: Bool) {
        hearsOverVoice = value
        store?.hearsOverVoice = value
    }

    func setFrame(_ value: EyesFrame) {
        frame = value
        store?.windowFrame = value.rawValue
        eyes?.change(frame: value)
    }

    /// From the next call on: one call keeps one language. With no call the screen changes at once.
    func setLanguage(_ value: String) {
        language = value
        store?.language = value
        guard let store, !isCallActive, !isWorking else { return }
        takeWords(from: store)
        status = words.ready
        lastInstruction = words.noHints
    }

    func setCharacter(_ value: AgentCharacter) {
        character = value
        store?.character = value
    }

    func setOwnCharacter(_ value: String) {
        ownCharacter = value
        store?.ownCharacter = value
    }

    /// The next line sounds in it, in the middle of a call too.
    func setVoice(_ id: String) {
        voice = id
        store?.voice = id
        (speaker as? ServerSpeaker)?.voice = id
        if isGreeted { speaker.keep(words.sounds + words.pause) }
    }

    func loadVoices() async {
        guard let settings = store?.settings else { return }
        do {
            let list = try await ServerClient(settings: settings).voices()
            voices = list.voices
            if !list.voices.contains(where: { $0.id == voice }) { voice = list.defaultVoice }
        } catch { journal?.append("Список голосов: \(error.localizedDescription)") }
    }

    private func showRestCard() {
        if let eyes { return eyes.pause(words: words.rest) }
        guard let card = TestFrameFactory.make(title: words.rest).jpegData(compressionQuality: 0.9) else { return }
        lastJPEG = card
        try? window.display(jpegData: card)
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

    func sceneChanged(_ name: String, isActive: Bool) {
        journal?.append("Сцена: \(name); аудиосессия активна: \(audioIsActive ? "да" : "нет")")
        if isActive, let text = unwrittenPaste { placeOnClipboard(text, reason: "Буфер при возврате") }
        if isActive { hearPerson() }
        probe?.sceneChanged(isActive: isActive)
    }

    private func placeOnClipboard(_ text: String, reason: String) {
        // With no window iOS can refuse a write from the background, so the text waits for the person to come back.
        unwrittenPaste = ClipboardWriter.write(text, reason: reason, journal: journal) ? nil : text
    }

    private func takeWords(from store: SharedStore) {
        words = .of(store.settings.userLang)
        pauseWords = PauseWords(phrases: words.pause, sounds: words.sounds)
    }

    private func newWorkflow() -> UUID {
        workflow?.cancel()
        workflowID = UUID()
        return workflowID
    }

    private func beginLocalCall(withEyes: Bool = false) throws {
        try audioSession.activate()
        window.prepare()
        if withEyes {
            let stage = EyesStage(surface: window, phrases: words, frame: frame, journal: journal)
            stage.start()
            stage.muted(isMuted)  // The button works from the first moment of a call, before the eyes are there.
            eyes = stage
        } else { try window.display(image: TestFrameFactory.make(title: words.waitingHint)) }
        callBegan = Date()
        isCallActive = true
        lastInstruction = words.waitingHint
    }

    private func hearPerson() {
        guard isCallActive, isGreeted, let client, let sessionID else { return }
        openStream(client: client, sessionID: sessionID)
        (microphone ?? makeMicrophone(client: client, sessionID: sessionID)).start()
    }

    private func openStream(client: ServerClient, sessionID: String) {
        guard speechStream == nil else { return }
        let stream = SpeechStream(client: client, sessionID: sessionID)
        stream.onDiagnostic = { [weak self] in self?.journal?.append($0) }
        speechStream = stream
        stream.start()
    }

    private func makeMicrophone(client: ServerClient, sessionID: String) -> CallMicrophone {
        let microphone = CallMicrophone(sharedEngine: hearsOverVoice ? (speaker as? ServerSpeaker)?.engine : nil)
        microphone.mute(isMuted)
        microphone.onDiagnostic = { [weak self] in self?.journal?.append($0) }
        microphone.onLost = { [weak self] in
            guard let self else { return }
            self.announce(self.words.lostHearing)
        }
        if measuresAppleSpeech {
            let probe = AppleSpeechProbe(journal: journal)
            probe.prepare()
            microphone.measure(with: probe)
            self.probe = probe
        }
        self.microphone = microphone
        conversation = Task { [weak self] in
            for await phrase in microphone.phrases {
                self?.heardPhrase(phrase, client: client, sessionID: sessionID)
            }
        }
        streaming = Task { [weak self] in
            for await samples in microphone.sound {
                if self?.speechStream?.send(samples) == true { self?.eyes?.heard(samples) }
            }
        }
        return microphone
    }

    private func stopHearing() {
        conversation?.cancel()
        conversation = nil
        streaming?.cancel()
        streaming = nil
        speechStream?.stop()
        speechStream = nil
        interruption?.cancel()
        interruption = nil
        microphone?.stop()
        microphone = nil
        probe = nil
    }

    /// The stream normally beats this phrase to the server. If it stayed silent, the phrase itself goes as a file.
    private func heardPhrase(_ phrase: Phrase, client: ServerClient, sessionID: String) {
        let seconds = String(format: "%.1f", locale: Locale(identifier: "ru_RU"), phrase.seconds)
        journal?.append("Реплика \(seconds) с; фон \(Int(phrase.noiseFloor)) дБ, пик \(Int(phrase.loudest)) дБ")
        noteDroppedSounds()
        guard lastHeard < Date().addingTimeInterval(-phrase.seconds - 1) else { return }
        let turn = heardTurn
        let waits = speechStream?.isOpen == true
        Task { [weak self] in
            if waits { try? await Task.sleep(nanoseconds: Self.streamWait) }
            guard let self, self.isCallActive, self.heardTurn == turn else { return }
            self.journal?.append("Поток речи не услышал реплику, отправляю файлом")
            do {
                let reply = try await client.say(phrase.wav, sessionID: sessionID)
                self.journal?.append("Реплика файлом: распознавание \(reply.speechMS) мс; \(reply.heard ? "есть слова" : "пусто")")
            } catch {
                self.journal?.append("Ошибка реплики: \(error.localizedDescription)")
                self.enqueue(Line(text: self.words.failed, turn: turn))
            }
        }
    }

    private func noteDroppedSounds() {
        guard let dropped = microphone?.dropped, dropped.count > droppedSounds else { return }
        droppedSounds = dropped.count
        journal?.append("Коротких звуков отброшено: \(dropped.count), громче всех \(Int(dropped.loudest)) дБ")
    }

    /// A hint waits for the end of an answer, so the agent never cuts itself off.
    private func announce(_ text: String) {
        enqueue(Line(text: text, kind: .hint))
    }

    private func enqueue(_ line: Line) {
        guard isCallActive else { return }  // A task from a call that has ended.
        if line.kind == .hint {
            waitingHint = line
        } else {
            lines.append(line)
            // What the person asked goes before what has not sounded yet, and a word on a pause stops for it at once.
            if line.kind == .reply, let currentLine, currentLine.kind == .filler || (!isSounding && currentLine.kind != .reply) {
                if currentLine.kind == .hint, waitingHint == nil { waitingHint = currentLine }
                speaker.stop()
            }
        }
        speakLines()
    }

    private func takeLine() -> Line? {
        if !lines.isEmpty { return lines.removeFirst() }
        defer { waitingHint = nil }
        return waitingHint
    }

    private func speakLines() {
        guard speaking == nil, isCallActive else { return }
        speaking = Task { [weak self] in
            while let self, !Task.isCancelled, let line = self.takeLine() {
                self.currentLine = line
                let parts = Sentences.of(line.text)
                let sound = line.sound.map { [$0] } ?? []
                let heard = await self.speaker.speakAndWait(sound + parts) - sound.count
                self.currentLine = nil
                if heard < parts.count { self.tellCut(line.id, said: parts.prefix(max(heard, 0)).joined(separator: " ")) }
            }
            self?.speaking = nil
        }
    }

    /// Lines that have not sounded yet and that no longer say what is needed.
    private func dropLines(where isOutdated: (Line) -> Bool) {
        for line in lines where isOutdated(line) { tellCut(line.id, said: "") }
        lines.removeAll(where: isOutdated)
        if !isSounding, let currentLine, isOutdated(currentLine) { speaker.stop() }
    }

    private func silence() {
        dropLines { _ in true }
        waitingHint = nil
        speaker.stop()
    }

    /// The model is left with what the person heard.
    private func tellCut(_ line: Int?, said: String) {
        guard let line, let client, let sessionID else { return }
        let journal = journal
        Task {
            do { _ = try await client.spoken(line: line, said: said, sessionID: sessionID) }
            catch { journal?.append("Ошибка /spoken: \(error.localizedDescription)") }
        }
    }

    private func isAboutOldScreen(_ line: Line) -> Bool {
        line.frameTS.map { $0 < movedAt } ?? false
    }

    /// The person is leaving this screen. What is shown goes at once, nothing about it sounds from here on, and a line that
    /// is already sounding ends with its sentence. The server takes their question on to the next frame.
    private func screenMoved() {
        guard isCallActive, let at = store?.screenMovedAt, at > movedAt else { return }
        movedAt = at
        journal?.append("Экран сдвинулся")
        if let eyes { eyes.newScreen() } else if hintOnWindow { showRestCard() }
        if hintOnWindow { lastInstruction = words.rest }
        hintOnWindow = false
        dropLines(where: isAboutOldScreen)
        if isSounding, let currentLine, isAboutOldScreen(currentLine) { speaker.endSentence() }
    }

    private func soundChanged(_ sounding: Bool) {
        isSounding = sounding
        if !sounding { lastSounded = Date() }
        microphone?.agentSpeaks(sounding)
        eyes?.speaks(sounding)
        guard sounding else { return }
        if hearsOverVoice { watchForInterruption() }
        if let line = currentLine, line.sound != nil, let turn = line.turn {
            journal?.append("Ход \(turn): звук перед ответом через \(sinceVoiceEnded()) мс после конца речи")
        } else { answerSounds() }
    }

    /// With a sound ahead of it the answer begins at its second sentence.
    private func sentenceSounds(_ index: Int) {
        if index == 1, currentLine?.sound != nil { answerSounds() }
    }

    private func answerSounds() {
        guard let line = currentLine, let turn = line.turn, let client, let sessionID else { return }
        journal?.append("Ход \(turn): первый звук через \(sinceVoiceEnded()) мс после конца речи")
        let journal = journal
        Task {
            do { _ = try await client.played(turn: turn, sessionID: sessionID) }
            catch { journal?.append("Ошибка /played: \(error.localizedDescription)") }
        }
    }

    private func sinceVoiceEnded() -> Int { Int(Date().timeIntervalSince(voiceEnded) * 1000) }

    /// Only with iOS filtering the voice out of the microphone. The person's words then reach the stream by themselves.
    private func watchForInterruption() {
        interruption?.cancel()
        interruption = Task { [weak self] in
            while let self, self.isSounding, !Task.isCancelled {
                if self.microphone?.isTalking == true {
                    self.journal?.append("Перебивание: голос остановлен")
                    self.speaker.stop()
                    return
                }
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
    }

    /// A short ready word while the agent thinks, so the line never goes dead. It only ever fills silence.
    private func fillPause(after seconds: Double, early: Bool = false) {
        pause?.cancel()
        pause = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            guard let self, !Task.isCancelled, self.isCallActive, self.currentLine == nil, self.lines.isEmpty,
                  self.waitingHint == nil, self.microphone?.isSpeaking != true,
                  self.wordsThisTurn < Self.mostWords,
                  let words = early ? self.pauseWords.earlySound(isReady: { self.speaker.holds($0) }) : self.pauseWords.next() else { return }
            self.wordsThisTurn += 1
            self.journal?.append(early ? "Ранний звук" : "Фраза на паузе")
            self.enqueue(Line(text: words, kind: .filler))
            self.fillPause(after: PauseWords.gap)
        }
    }

    private func keepQuiet() {
        pause?.cancel()
    }

    private func connect(client: ServerClient, sessionID: String) {
        textCoordinator?.start(client: client, sessionID: sessionID)
        let connection = HintEventConnection(client: client)
        connection.onEvent = { [weak self] in self?.receive($0) }
        connection.onDiagnostic = { [weak self] in self?.journal?.append($0) }
        connection.onReachable = { [weak self] isUp in
            self?.isLinkDown = !isUp
            self?.eyes?.linkChanged(isUp: isUp)
        }
        connection.onSessionExpired = { [weak self] in
            guard let self else { return }
            self.endCall()
            self.status = self.words.refused
            self.journal?.append(self.status)
        }
        self.connection = connection
        connection.start(sessionID: sessionID)
        openStream(client: client, sessionID: sessionID)
        let watch = NetworkWatch()
        watch.onDiagnostic = { [weak self] in self?.journal?.append($0) }
        watch.onNewWay = { [weak self] in
            self?.connection?.reopen()
            self?.speechStream?.reopen()
        }
        networkWatch = watch
        watch.start()
        warmth = Task {
            while !Task.isCancelled {
                try? await client.health()
                try? await Task.sleep(nanoseconds: Self.warmthGap)
            }
        }
    }

    private func receive(_ event: ServerEvent) {
        guard isCallActive, !isTestingWindow else { return }
        switch event {
        case .hint(let hint):
            guard seenSteps.insert(hint.stepID).inserted else { return }
            isSharing = true
            if let timestamp = hint.frameTS {
                let elapsed = Date().timeIntervalSince1970 - Double(timestamp) / 1000
                let seconds = String(format: "%.1f", locale: Locale(identifier: "ru_RU"), elapsed)
                let timings = hint.timings.map {
                    "; модель \($0.modelMS) мс, рамка \($0.boxMS) мс, \($0.boxSource); окно на кадре: \($0.window == true ? "да" : "нет")"
                } ?? ""
                journal?.append("Подсказка \(hint.stepID) через \(seconds) с после кадра\(timings)")
                guard timestamp >= movedAt else {
                    journal?.append("Подсказка \(hint.stepID) о прежнем экране не показана")
                    return
                }
            }
            lastInstruction = hint.instructionRU
            do {
                guard let data = Data(base64Encoded: hint.imageJPEGBase64) else { throw CallError.image }
                if let eyes {
                    try eyes.show(hint: data, calm: hint.calm == true)
                } else { try window.display(jpegData: data) }
                lastJPEG = data
                hintOnWindow = true
                journal?.append("Показана подсказка")
            } catch { journal?.append("Ошибка картинки подсказки: \(error.localizedDescription)") }
        case .status(let status):
            if !isWorking { self.status = status.textRU }
            journal?.append("Сервер: \(status.state); \(status.textRU)")
            updateSharing(status)
        case .done(let done):
            status = done.textRU
            journal?.append("Сервер закрыл сессию")
        case .heard(let event):
            guard event.turn != heardTurn else { break }  // A reconnect names the turn again, and the line it brings back is the same.
            heardTurn = event.turn
            lastHeard = Date()
            let voice = microphone?.lastVoice ?? lastHeard
            voiceEnded = voice == .distantPast ? lastHeard : voice
            journal?.append("Ход \(event.turn): услышан через \(sinceVoiceEnded()) мс после конца речи")
            eyes?.wait(for: .reply)
            dropLines { $0.kind != .hint }
            wordsThisTurn = 0
            let early = pauseWords.startsEarly() && words.sounds.contains { speaker.holds($0) }
            fillPause(after: early ? Self.earlySoundAfter : Self.firstWordAfter, early: early)
        case .say(let say):
            hear(say)
        case .look(let event):
            look(turn: event.turn)
        case .quiet(let event):
            guard event.turn == heardTurn else { break }
            pause?.cancel()
            eyes?.stopWaiting(for: .reply)
            eyes?.stopWaiting(for: .screen)
            dropLines { $0.kind == .filler }
        case .left:
            hintOnWindow = false
            if let eyes { eyes.newScreen() } else { showRestCard() }
            lastInstruction = words.rest
        case .reading:
            eyes?.newScreen()
            eyes?.wait(for: .screen)
        case .face(let event):
            eyes?.express(nil, calm: event.calm)
        case .read(let event):
            store?.textAsked = event.frameTS
        case .notLooking(let event):
            isNotLooking = event.on
            store?.notLooking = event.on
            eyes?.notLooking(event.on)
            journal?.append("Сервер: \(event.on ? "не смотрим" : "смотрим снова")")
            if event.on {
                hintOnWindow = false
                textCoordinator?.stopLooking()
            }
            lastInstruction = event.on ? words.notLooking : words.rest
        }
    }

    private func hear(_ say: SayEvent) {
        if let line = say.line, currentLine?.id == line || lines.contains(where: { $0.id == line }) { return }  // Back from a reconnect.
        journal?.append("Ход \(say.turn): ответ через \(sinceVoiceEnded()) мс после конца речи; модель \(say.timings.modelMS) мс")
        guard say.turn == heardTurn else {  // The person has said more since.
            tellCut(say.line, said: "")
            return
        }
        if let frame = say.frameTS, frame < movedAt {  // A question goes on to the next frame, so the eyes go on waiting for its answer.
            journal?.append("Ход \(say.turn): ответ о прежнем экране не сказан")
            eyes?.stopWaiting(for: .screen)
            tellCut(say.line, said: "")
            return
        }
        pause?.cancel()
        eyes?.stopWaiting(for: .reply)
        eyes?.stopWaiting(for: .screen)
        if let text = say.pasteText { placeOnClipboard(text, reason: "Буфер из ответа") }
        guard !say.textRU.isEmpty else { return }
        status = say.textRU
        lastInstruction = say.textRU
        lastSpokenHint = say.textRU
        if say.textRU.hasSuffix("?") { eyes?.ask(say.textRU) }
        enqueue(Line(text: say.textRU, turn: say.turn, id: say.line, frameTS: say.frameTS, sound: soundBeforeAnswer(say)))
        answeredTurn = say.turn
    }

    /// After a long wait in silence a short sound goes first, so the person knows the answer is coming.
    private func soundBeforeAnswer(_ say: SayEvent) -> String? {
        let start = say.textRU.lowercased()
        guard say.turn > answeredTurn, sinceVoiceEnded() >= Self.longWait, !isSounding, currentLine == nil, lines.isEmpty,
              waitingHint == nil, microphone?.isSpeaking != true, Date().timeIntervalSince(lastSounded) >= Self.silenceBeforeSound else { return nil }
        return pauseWords.soundBeforeAnswer { speaker.holds($0) && !start.hasPrefix($0.dropLast().lowercased()) }
    }

    /// The screen as it is now for the line just heard. If it does not come, the server goes on with the last frame it has.
    private func look(turn: Int) {
        guard let client, let sessionID else { return }
        let journal = journal
        Task { [weak self] in
            guard let screen = await self?.screenNow() else {
                journal?.append("Кадры не отправлены")
                return
            }
            do {
                _ = try await client.look([screen], sessionID: sessionID)
                journal?.append("Кадры отправлены")
            } catch ClientError.http(409) {
                journal?.append("Кадры не отправлены")  // The person went on, and a new line replaced this one.
            } catch { journal?.append("Ошибка /look: \(error.localizedDescription)") }
        }
    }

    /// The broadcast looks for the question four times a second and puts the screen on the shelf, however little it changed.
    private func screenNow() async -> KeptScreen? {
        guard isSharing, let store else { return nil }
        let started = Date()
        let asked = store.screenAsked + 1
        store.screenAsked = asked
        while store.screenAnswered != asked, Date().timeIntervalSince(started) < Self.screenWait {
            try? await Task.sleep(nanoseconds: 40_000_000)
        }
        let waited = Int(Date().timeIntervalSince(started) * 1000)
        journal?.append(store.screenAnswered == asked ? "Свежий кадр за \(waited) мс" : "Свежий кадр не успел за \(waited) мс")
        return shelf?.recent().last
    }

    private func updateSharing(_ status: StatusEvent) {
        if status.state == "broadcast_stopped" {
            isSharing = false
        } else if status.state == "broadcasting", !isSharing {
            isSharing = true
            lastInstruction = words.openApp
        }
    }

    private func repeatHint() {
        guard isCallActive else { return }
        announce(lastSpokenHint.isEmpty ? words.noHintYet : lastSpokenHint)
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
                guard let data = image.jpegData(compressionQuality: 0.9) else { throw CallError.image }
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
            isWorking = false
            silence()
            microphone?.stop()
            status = words.interrupted
            journal?.append(status)
        } else {
            let options = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            if AVAudioSession.InterruptionOptions(rawValue: options).contains(.shouldResume) { resumeAudio() }
            else {
                endCall()
                status = words.endedByInterruption
            }
        }
    }

    private func resumeAudio() {
        guard isCallActive else { return }
        do {
            try audioSession.activate()
            status = isTestingWindow ? "Тест окна: новый кадр каждые 3 секунды" : words.goesOn
            if !isTestingWindow { isGreeted = true }  // The break may have cut the greeting off.
            hearPerson()
        } catch {
            endCall()
            status = "\(words.notResumed): \(error.localizedDescription)"
        }
    }

    private func deactivateAudio() {
        do { try audioSession.deactivate() }
        catch { journal?.append("Ошибка выключения аудиосессии: \(error.localizedDescription)") }
    }

    private enum CallError: LocalizedError {
        case image
        var errorDescription: String? { "Не удалось прочитать картинку подсказки." }
    }

    deinit {
        testTimer?.invalidate()
        workflow?.cancel()
        conversation?.cancel()
        streaming?.cancel()
        warmth?.cancel()
        speaking?.cancel()
        interruption?.cancel()
        pause?.cancel()
        clipboardTask?.cancel()
        textTestTask?.cancel()
    }
}
