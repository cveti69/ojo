import UIKit

@MainActor
final class BackgroundActivity {
    var onExpiration: (() -> Void)?
    private var identifier: UIBackgroundTaskIdentifier = .invalid
    var isGranted: Bool { identifier != .invalid }

    init(name: String) {
        identifier = UIApplication.shared.beginBackgroundTask(withName: name) { [weak self] in
            Task { @MainActor [weak self] in
                self?.onExpiration?()
                self?.finish()
            }
        }
    }

    func finish() {
        onExpiration = nil
        guard identifier != .invalid else { return }
        UIApplication.shared.endBackgroundTask(identifier)
        identifier = .invalid
    }
}
