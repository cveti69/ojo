import AVFAudio
import Combine
import Shared
import UIKit

@MainActor
final class CallAudioSession {
    private(set) var isActive = false
    var onActivityChange: ((Bool) -> Void)?
    var onDiagnostic: ((String) -> Void)?
    var onRouteChange: (() -> Void)?
    var mode = AVAudioSession.Mode.default  // Taken at every start of the sound, so a new choice works from the next call.
    private var keepAlivePlayer: AVAudioPlayer?
    private var routeChange: AnyCancellable?

    init() {
        routeChange = NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification)
            .receive(on: RunLoop.main)
            .sink { [weak self] notification in
                guard let self, self.isActive else { return }
                let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt ?? 0
                self.onDiagnostic?("Звук сменил путь, причина \(reason): \(Self.route)")
                self.onRouteChange?()
                if self.keepAlivePlayer?.isPlaying == false { self.keepAlivePlayer?.play() }  // Headphones taken off can pause it.
            }
    }

    /// Kinds of port and never their names: the name of a pair of headphones can be the person's own.
    private static var route: String {
        let session = AVAudioSession.sharedInstance()
        let ports = session.currentRoute
        let names = { (ports: [AVAudioSessionPortDescription]) in ports.isEmpty ? "нет" : ports.map(\.portType.rawValue).joined(separator: "+") }
        return "режим \(session.mode.rawValue.replacingOccurrences(of: "AVAudioSessionMode", with: "")), вход \(names(ports.inputs)), выход \(names(ports.outputs)), доступно \(names(session.availableInputs ?? [])), \(Int(session.sampleRate)) Гц, опции \(session.categoryOptions.rawValue)"
    }

    /// Where the sound goes now, for the button that changes it.
    func output(in words: CallWords) -> String {
        guard let port = AVAudioSession.sharedInstance().currentRoute.outputs.first else { return words.soundSpeaker }
        switch port.portType {
        case .builtInSpeaker: return words.soundSpeaker
        case .builtInReceiver: return UIDevice.current.localizedModel
        case .bluetoothHFP, .bluetoothA2DP, .bluetoothLE, .headphones, .usbAudio:
            return port.portName.contains("AirPods") ? "AirPods" : words.soundHeadphones
        default: return port.portName
        }
    }

    func activate() throws {
        let session = AVAudioSession.sharedInstance()
        // AirPods come in by the Bluetooth option. The speaker is only the way when nothing else is connected.
        try session.setCategory(.playAndRecord, mode: mode, options: [.defaultToSpeaker, .allowAirPlay, .allowBluetoothHFP])
        try session.setActive(true)
        onDiagnostic?("Звук: \(Self.route)")
        onRouteChange?()
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
