import AVFAudio

@MainActor
final class GoalAudioRecorder {
    private static let silenceDuration: TimeInterval = 1
    private static let speechMargin: Float = 10  // Speech stands this many dB above the room.
    private static let quietestSpeech: Float = -50
    private static let floorRise: Float = 0.1  // dB per check, so a long phrase never becomes the new silence.
    private static let listenLimit: TimeInterval = 8
    var onDiagnostic: ((String) -> Void)?
    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private var completion: CheckedContinuation<URL, Error>?
    private var startedAt = Date()
    private var lastSpeechAt: Date?
    private var noiseFloor: Float?
    private var loudest: Float = -160
    private var recordingID: UUID?

    func record() async throws -> URL {
        guard await AVAudioApplication.requestRecordPermission() else { throw RecordingError.permission }
        try Task.checkCancellation()
        let identifier = UUID()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                do {
                    cancel()
                    let url = FileManager.default.temporaryDirectory.appendingPathComponent("goal-\(UUID().uuidString).wav")
                    let recorder = try AVAudioRecorder(url: url, settings: [
                        AVFormatIDKey: kAudioFormatLinearPCM,
                        AVSampleRateKey: 16_000,
                        AVNumberOfChannelsKey: 1,
                        AVLinearPCMBitDepthKey: 16,
                        AVLinearPCMIsBigEndianKey: false,
                        AVLinearPCMIsFloatKey: false
                    ])
                    recorder.isMeteringEnabled = true
                    guard recorder.prepareToRecord(), recorder.record() else { throw RecordingError.start }
                    self.recorder = recorder
                    recordingID = identifier
                    completion = continuation
                    startedAt = Date()
                    lastSpeechAt = nil
                    noiseFloor = nil
                    loudest = -160
                    let timer = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in
                        Task { @MainActor [weak self] in self?.checkSilence() }
                    }
                    self.timer = timer
                    RunLoop.main.add(timer, forMode: .common)
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                guard self?.recordingID == identifier else { return }
                self?.cancel()
            }
        }
    }

    func finishNow() { finish(error: nil) }

    func cancel() { finish(error: CancellationError()) }

    private func checkSilence() {
        guard let recorder else { return }
        guard recorder.isRecording else { finish(error: RecordingError.interrupted); return }
        recorder.updateMeters()
        let now = Date()
        let level = recorder.averagePower(forChannel: 0)
        if level > -120 {  // The meter reads lower only until sound comes in, or when the voice filter mutes it.
            let floor = noiseFloor ?? level
            noiseFloor = min(level, floor + Self.floorRise)
            loudest = max(loudest, level)
            if level > max(floor + Self.speechMargin, Self.quietestSpeech) { lastSpeechAt = now }
        }
        if let lastSpeechAt, now.timeIntervalSince(lastSpeechAt) >= Self.silenceDuration {
            finish(error: nil)
        } else if now.timeIntervalSince(startedAt) >= (lastSpeechAt == nil ? Self.listenLimit : 30) {
            finish(error: nil)  // A quiet voice can slip past the level check, so the recognizer decides.
        }
    }

    private func finish(error: Error?) {
        timer?.invalidate()
        timer = nil
        guard let recorder, let completion else { return }
        let seconds = String(format: "%.1f", locale: Locale(identifier: "ru_RU"), Date().timeIntervalSince(startedAt))
        onDiagnostic?("Запись \(seconds) с; речь \(lastSpeechAt == nil ? "не замечена" : "замечена"); фон \(Int(noiseFloor ?? -160)) дБ, пик \(Int(loudest)) дБ")
        recorder.stop()
        self.recorder = nil
        recordingID = nil
        self.completion = nil
        if let error {
            try? FileManager.default.removeItem(at: recorder.url)
            completion.resume(throwing: error)
        } else {
            completion.resume(returning: recorder.url)
        }
    }

    private enum RecordingError: LocalizedError {
        case permission, start, interrupted
        var errorDescription: String? {
            switch self {
            case .permission: return "Разрешите микрофон в настройках или введите цель текстом."
            case .start: return "Не удалось включить микрофон. Введите цель текстом."
            case .interrupted: return "Запись прервана. Повторите цель или введите её текстом."
            }
        }
    }
}
