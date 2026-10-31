import Shared
import Speech

@MainActor
protocol GoalTranscribing {
    func transcribe(wavURL: URL) async throws -> String
}

struct ServerGoalTranscriber: GoalTranscribing {
    let client: ServerClient

    func transcribe(wavURL: URL) async throws -> String {
        try await client.transcribe(wavURL: wavURL).text
    }
}

@MainActor
final class SpeechGoalTranscriber: GoalTranscribing {
    private var task: SFSpeechRecognitionTask?
    private var completion: CheckedContinuation<String, Error>?
    private var timeoutTask: Task<Void, Never>?

    func transcribe(wavURL: URL) async throws -> String {
        let authorization = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        try Task.checkCancellation()
        guard authorization == .authorized else { throw TranscriptionError.permission }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "ru-RU")),
              recognizer.supportsOnDeviceRecognition else { throw TranscriptionError.unavailable }
        let request = SFSpeechURLRecognitionRequest(url: wavURL)
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = false
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                completion = continuation
                timeoutTask = Task { [weak self] in
                    do { try await Task.sleep(nanoseconds: 20_000_000_000) } catch { return }
                    self?.finish(.failure(TranscriptionError.timeout))
                }
                task = recognizer.recognitionTask(with: request) { [weak self] result, error in
                    let text = result?.isFinal == true ? result?.bestTranscription.formattedString : nil
                    Task { @MainActor [weak self] in
                        if let error { self?.finish(.failure(error)) }
                        else if let text {
                            self?.finish(.success(text))
                        }
                    }
                }
            }
        } onCancel: {
            Task { @MainActor [weak self] in self?.finish(.failure(CancellationError())) }
        }
    }

    private func finish(_ result: Result<String, Error>) {
        guard let completion else { return }
        self.completion = nil
        timeoutTask?.cancel()
        timeoutTask = nil
        task?.cancel()
        task = nil
        completion.resume(with: result)
    }

    private enum TranscriptionError: LocalizedError {
        case permission, unavailable, timeout
        var errorDescription: String? {
            switch self {
            case .permission: return "Разрешите распознавание речи в настройках или введите цель текстом."
            case .unavailable: return "Распознавание ru-RU на устройстве недоступно. Введите цель текстом."
            case .timeout: return "Распознавание заняло слишком много времени. Повторите цель или введите её текстом."
            }
        }
    }
}

struct FallbackGoalTranscriber: GoalTranscribing {
    let primary: any GoalTranscribing
    let fallback: any GoalTranscribing
    let journal: SharedJournal?

    func transcribe(wavURL: URL) async throws -> String {
        do {
            let text = try await primary.transcribe(wavURL: wavURL)
            if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return text }
            throw ClientError.invalidResponse
        } catch {
            try Task.checkCancellation()
            journal?.append("Сервер не распознал цель: \(error.localizedDescription); пробуем Speech ru-RU")
            return try await fallback.transcribe(wavURL: wavURL)
        }
    }
}
