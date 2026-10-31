import AVFAudio

@MainActor
protocol Speaker: AnyObject {
    func speak(_ text: String)
    func speakAndWait(_ text: String) async
    func stop()
}

@MainActor
final class SystemSpeaker: NSObject, Speaker, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var currentUtterance: AVSpeechUtterance?
    private var completion: CheckedContinuation<Void, Never>?
    private var waitingID: UUID?

    override init() {
        super.init()
        synthesizer.delegate = self
        synthesizer.usesApplicationAudioSession = true
    }

    func speak(_ text: String) {
        stop()
        start(text)
    }

    func speakAndWait(_ text: String) async {
        guard !Task.isCancelled else { return }
        let identifier = UUID()
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                stop()
                completion = continuation
                waitingID = identifier
                start(text)
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                guard self?.waitingID == identifier else { return }
                self?.stop()
            }
        }
    }

    func stop() {
        currentUtterance = nil
        waitingID = nil
        synthesizer.stopSpeaking(at: .immediate)
        completion?.resume()
        completion = nil
    }

    private func start(_ text: String) {
        guard !text.isEmpty else {
            completion?.resume()
            completion = nil
            waitingID = nil
            return
        }
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "ru-RU")
        currentUtterance = utterance
        synthesizer.speak(utterance)
    }

    private func finished(_ identifier: ObjectIdentifier) {
        guard let currentUtterance, ObjectIdentifier(currentUtterance) == identifier else { return }
        self.currentUtterance = nil
        waitingID = nil
        completion?.resume()
        completion = nil
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        let identifier = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in self?.finished(identifier) }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        let identifier = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in self?.finished(identifier) }
    }
}
