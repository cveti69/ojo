import UIKit

@MainActor
protocol HintWindowPresenting: AnyObject {
    var inlineView: UIView { get }
    var onStateChange: ((HintWindowState) -> Void)? { get set }
    var onRepeatHint: (() -> Void)? { get set }
    var onDiagnostic: ((String) -> Void)? { get set }

    func setVideoCallFallback(_ enabled: Bool)
    func prepare()
    func display(image: UIImage) throws
    func display(jpegData: Data) throws
    func startPictureInPicture()
    func stop()
}

enum HintWindowState: Equatable {
    case inactive
    case unavailable
    case preparing
    case ready
    case active
    case failed(String)

    var message: String {
        switch self {
        case .inactive: return "Окно выключено"
        case .unavailable: return "Окно недоступно на этом устройстве"
        case .preparing: return "Подготовка окна…"
        case .ready: return "Окно готово: перейдите в другое приложение"
        case .active: return "Окно открыто"
        case .failed(let reason): return "Ошибка окна: \(reason)"
        }
    }
}
