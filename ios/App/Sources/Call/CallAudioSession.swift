import AVFAudio

@MainActor
final class CallAudioSession {
    private(set) var isActive = false
    var onActivityChange: ((Bool) -> Void)?
    private var keepAlivePlayer: AVAudioPlayer?

    func activate() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowAirPlay])
        try session.setActive(true)
        if keepAlivePlayer == nil {
            // Keep the call's audio I/O running between spoken hints.
            let player = try AVAudioPlayer(data: WAV.file(samples: [Int16](repeating: 0, count: 16_000), sampleRate: 16_000))
            player.numberOfLoops = -1
            keepAlivePlayer = player
        }
        guard keepAlivePlayer?.play() == true else { throw AudioError.playback }
        isActive = true
        onActivityChange?(true)
    }

    func deactivate() throws {
        keepAlivePlayer?.stop()
        keepAlivePlayer = nil
        try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        isActive = false
        onActivityChange?(false)
    }

    func interrupted() {
        isActive = false
        onActivityChange?(false)
    }

    private enum AudioError: LocalizedError {
        case playback
        var errorDescription: String? { "Не удалось запустить звук звонка." }
    }
}
