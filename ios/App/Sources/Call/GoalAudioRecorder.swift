import AVFAudio

@MainActor
final class GoalAudioRecorder {
    private static let silenceDuration: TimeInterval = 1.2
    private static let speechThreshold: Float = -38
    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private var completion: CheckedContinuation<URL, Error>?
    private var startedAt = Date()
    private var lastSpeechAt: Date?
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

    func finishNow() { finish(error: lastSpeechAt == nil ? RecordingError.noSpeech : nil) }

    func cancel() { finish(error: CancellationError()) }

    private func checkSilence() {
        guard let recorder else { return }
        guard recorder.isRecording else { finish(error: RecordingError.interrupted); return }
        recorder.updateMeters()
        let now = Date()
        if recorder.averagePower(forChannel: 0) > Self.speechThreshold { lastSpeechAt = now }
        if let lastSpeechAt, now.timeIntervalSince(lastSpeechAt) >= Self.silenceDuration {
            finish(error: nil)
        } else if now.timeIntervalSince(startedAt) >= 30 {
            finish(error: lastSpeechAt == nil ? RecordingError.noSpeech : nil)
        } else if lastSpeechAt == nil, now.timeIntervalSince(startedAt) >= 8 {
            finish(error: RecordingError.noSpeech)
        }
    }

    private func finish(error: Error?) {
        timer?.invalidate()
        timer = nil
        guard let recorder, let completion else { return }
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
        case permission, start, noSpeech, interrupted
        var errorDescription: String? {
            switch self {
            case .permission: return "Разрешите микрофон в настройках или введите цель текстом."
            case .start: return "Не удалось включить микрофон. Введите цель текстом."
            case .noSpeech: return "Речь не услышана. Попробуйте ещё раз или введите цель текстом."
            case .interrupted: return "Запись прервана. Повторите цель или введите её текстом."
            }
        }
    }
}
