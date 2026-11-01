import Shared
import UIKit

@MainActor
final class ScreenTextCoordinator {
    private let store: SharedStore
    private let journal: SharedJournal
    private let mailbox: ScreenTextMailbox
    private let worker = ScreenTextWorker()
    private var notification: ScreenTextNotification?
    private var sessionID: String?
    private var client: ServerClient?
    private var task: Task<Void, Never>?
    private var isReading = false
    private var lastFrameTS: Int64?
    private var firstRead = true
    private var warmupTask: Task<Void, Never>?
    private var warmupID = UUID()

    init(configuration: ProjectConfiguration, store: SharedStore, journal: SharedJournal) throws {
        self.store = store
        self.journal = journal
        mailbox = try ScreenTextMailbox(configuration: configuration)
        notification = ScreenTextNotification(name: mailbox.notificationName) { [weak self] in
            Task { @MainActor [weak self] in self?.consumeLatest() }
        }
    }

    func start(client: ServerClient, sessionID: String) {
        self.client = client
        self.sessionID = sessionID
        lastFrameTS = nil
        firstRead = true
        consumeLatest()
    }

    func stop() {
        sessionID = nil
        client = nil
        lastFrameTS = nil
        task?.cancel()
        warmupTask?.cancel()
        warmupTask = nil
        warmupID = UUID()
        clearMailbox()
    }

    func prepareForCall(targetLang: String) {
        clearMailbox()
        warmupTask?.cancel()
        warmupTask = nil
        let id = UUID()
        warmupID = id
        guard store.screenTextMode == .app, UIApplication.shared.applicationState == .active,
              let jpeg = TestFrameFactory.makeScreenText(targetLang: targetLang).jpegData(compressionQuality: 0.9) else { return }
        let journal = journal
        warmupTask = Task { [weak self] in
            guard let self else { return }
            defer { if self.warmupID == id { self.warmupTask = nil } }
            guard !Task.isCancelled, self.store.screenTextMode == .app,
                  UIApplication.shared.applicationState == .active else { return }
            let background = UIApplication.shared.applicationState == .background
            let reading = await self.worker.read(jpegData: jpeg, screenLang: targetLang,
                                                 onDiagnostic: { journal.append($0) }) {
                journal.append("Прогрев текста: чтение начинается; phys_footprint: \(ProcessMemory.description) МБ")
            }
            self.log(reading, label: "Прогрев текста", backgroundBefore: background)
        }
    }

    private func clearMailbox() {
        do { try mailbox.clear() }
        catch { journal.append("Не удалось удалить общий кадр: \(error.localizedDescription)") }
    }

    func modeChanged() { consumeLatest() }

    func recognizeTest(jpeg: Data, targetLang: String, label: String, cpuOnly: Bool = false) async {
        let background = UIApplication.shared.applicationState == .background
        let journal = journal
        let reading = await worker.read(jpegData: jpeg, screenLang: targetLang, cpuOnly: cpuOnly,
                                        onDiagnostic: { journal.append($0) }) {
            journal.append("\(label): чтение начинается; фон перед чтением: \(background ? "да" : "нет"); phys_footprint: \(ProcessMemory.description) МБ")
        }
        log(reading, label: label, backgroundBefore: background)
    }

    private func consumeLatest() {
        guard !isReading, store.screenTextMode == .app, let client, let sessionID,
              store.sessionID == sessionID else { return }
        do {
            guard let frame = try mailbox.latest(), frame.sessionID == sessionID, frame.frameTS != lastFrameTS else { return }
            lastFrameTS = frame.frameTS
            isReading = true
            let background = UIApplication.shared.applicationState == .background
            let logFirst = firstRead
            firstRead = false
            let journal = journal
            task = Task { [self] in
                defer {
                    isReading = false
                    task = nil
                    consumeLatest()
                }
                let reading = await worker.read(jpegData: frame.jpeg, screenLang: client.settings.targetLang,
                                                onDiagnostic: { journal.append($0) }) {
                    if logFirst {
                        journal.append("Первое чтение текста в приложении начинается; фон перед чтением: \(background ? "да" : "нет"); phys_footprint: \(ProcessMemory.description) МБ")
                    }
                }
                log(reading, label: "Текст в приложении", backgroundBefore: background)
                guard !Task.isCancelled, reading.error == nil, self.sessionID == sessionID,
                      store.sessionID == sessionID, store.screenTextMode == .app else { return }
                do { _ = try await client.sendScreenText(reading.lines, sessionID: sessionID, frameTS: frame.frameTS) }
                catch { if !Task.isCancelled { journal.append("Ошибка /text в приложении: \(error.localizedDescription)") } }
            }
        } catch { journal.append("Ошибка чтения общего кадра: \(error.localizedDescription)") }
    }

    private func log(_ reading: ScreenTextReading, label: String, backgroundBefore: Bool) {
        let backgroundAfter = UIApplication.shared.applicationState == .background
        journal.append("\(label): \(reading.diagnostic); фон перед чтением: \(backgroundBefore ? "да" : "нет"), после: \(backgroundAfter ? "да" : "нет")")
    }

    deinit {
        task?.cancel()
        warmupTask?.cancel()
    }
}
