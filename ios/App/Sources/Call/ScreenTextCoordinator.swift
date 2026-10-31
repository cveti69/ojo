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
    }

    func modeChanged() { consumeLatest() }

    func recognizeTest(jpeg: Data, targetLang: String, label: String) async {
        let background = UIApplication.shared.applicationState == .background
        let journal = journal
        let reading = await worker.read(jpegData: jpeg, targetLang: targetLang) {
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
                let reading = await worker.read(jpegData: frame.jpeg, targetLang: client.settings.targetLang) {
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

    deinit { task?.cancel() }
}
