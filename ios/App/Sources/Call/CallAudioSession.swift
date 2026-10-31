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
            let player = try AVAudioPlayer(data: Self.silentWAV())
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

    private static func silentWAV() -> Data {
        let sampleCount: UInt32 = 16_000
        let byteCount = sampleCount * 2
        var data = Data("RIFF".utf8)
        func number<T: FixedWidthInteger>(_ value: T) {
            var value = value.littleEndian
            withUnsafeBytes(of: &value) { data.append(contentsOf: $0) }
        }
        number(byteCount + 36)
        data.append(contentsOf: "WAVEfmt ".utf8)
        number(UInt32(16))
        number(UInt16(1))
        number(UInt16(1))
        number(sampleCount)
        number(sampleCount * 2)
        number(UInt16(2))
        number(UInt16(16))
        data.append(contentsOf: "data".utf8)
        number(byteCount)
        data.append(Data(repeating: 0, count: Int(byteCount)))
        return data
    }

    private enum AudioError: LocalizedError {
        case playback
        var errorDescription: String? { "Не удалось запустить звук звонка." }
    }
}
