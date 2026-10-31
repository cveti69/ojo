@preconcurrency import AVFAudio
import Shared
import Speech

/// Measures how fast Apple's recognizer hears the person on the phone itself, in the background of a call.
/// Only for the record: nothing it hears reaches the agent, and no words go into the journal.
final class AppleSpeechProbe: @unchecked Sendable {
    private let journal: SharedJournal?
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "ru-RU"))
    private let lock = NSLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var started = Date.distantPast
    private var firstWords: Int?
    private var ended = Date.distantPast
    private var onScreen = true

    init(journal: SharedJournal?) {
        self.journal = journal
    }

    func sceneChanged(isActive: Bool) {
        lock.withLock { onScreen = isActive }
    }

    func prepare() {
        let journal = journal
        let recognizer = recognizer
        SFSpeechRecognizer.requestAuthorization { status in
            journal?.append("Apple Speech: разрешение \(status.rawValue); доступен: \(recognizer?.isAvailable == true ? "да" : "нет"); на устройстве: \(recognizer?.supportsOnDeviceRecognition == true ? "да" : "нет")")
        }
    }

    /// The microphone calls this on its own thread.
    func hear(_ buffer: AVAudioPCMBuffer, isSpeech: Bool) {
        lock.withLock {
            if isSpeech, request == nil { begin() }
            request?.append(buffer)
            if !isSpeech, let request {
                request.endAudio()
                self.request = nil
                ended = Date()
            }
        }
    }

    private func begin() {
        guard SFSpeechRecognizer.authorizationStatus() == .authorized, let recognizer, recognizer.supportsOnDeviceRecognition else { return }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        self.request = request
        started = Date()
        firstWords = nil
        let place = onScreen ? "на экране" : "в фоне"
        let journal = journal
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            let now = Date()
            self.lock.withLock {
                if let error {
                    journal?.append("Apple Speech: ошибка \((error as NSError).code); \(place)")
                    self.task = nil
                } else if let result {
                    if self.firstWords == nil, !result.bestTranscription.formattedString.isEmpty {
                        self.firstWords = Int(now.timeIntervalSince(self.started) * 1000)
                    }
                    if result.isFinal {
                        journal?.append("Apple Speech: первые слова \(self.firstWords ?? -1) мс от начала речи; итог \(Int(now.timeIntervalSince(self.ended) * 1000)) мс после конца; \(place)")
                        self.task = nil
                    }
                }
            }
        }
    }
}
