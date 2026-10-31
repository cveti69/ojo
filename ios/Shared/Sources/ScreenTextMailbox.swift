import Foundation

public struct ScreenTextFrame: Codable, Equatable, Sendable {
    public let sessionID: String
    public let frameTS: Int64
    public let jpeg: Data

    public init(sessionID: String, frameTS: Int64, jpeg: Data) {
        self.sessionID = sessionID
        self.frameTS = frameTS
        self.jpeg = jpeg
    }
}

public struct ScreenTextMailbox: Sendable {
    public let notificationName: String
    private let fileURL: URL

    public init(configuration: ProjectConfiguration) throws {
        guard let directory = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: configuration.appGroupIdentifier) else {
            throw MailboxError.unavailable
        }
        self.init(directory: directory, notificationName: configuration.appGroupIdentifier + ".screen-text-frame")
    }

    public init(directory: URL, notificationName: String) {
        fileURL = directory.appendingPathComponent("screen-text-frame.plist")
        self.notificationName = notificationName
    }

    public func publish(_ frame: ScreenTextFrame, notify: Bool = true) throws {
        let encoder = PropertyListEncoder()
        encoder.outputFormat = .binary
        // JPEG and metadata move together in one atomic replacement.
        try encoder.encode(frame).write(to: fileURL, options: .atomic)
        if notify {
            CFNotificationCenterPostNotification(CFNotificationCenterGetDarwinNotifyCenter(), CFNotificationName(notificationName as CFString), nil, nil, true)
        }
    }

    public func latest() throws -> ScreenTextFrame? {
        do {
            return try PropertyListDecoder().decode(ScreenTextFrame.self, from: Data(contentsOf: fileURL))
        } catch CocoaError.fileReadNoSuchFile {
            return nil
        }
    }

    private enum MailboxError: LocalizedError {
        case unavailable
        var errorDescription: String? { "Файл кадра в общей группе недоступен." }
    }
}

public final class ScreenTextNotification: @unchecked Sendable {
    private let name: CFString
    private let callback: @Sendable () -> Void

    public init(name: String, callback: @escaping @Sendable () -> Void) {
        self.name = name as CFString
        self.callback = callback
        CFNotificationCenterAddObserver(CFNotificationCenterGetDarwinNotifyCenter(), Unmanaged.passUnretained(self).toOpaque(), { _, observer, _, _, _ in
            guard let observer else { return }
            Unmanaged<ScreenTextNotification>.fromOpaque(observer).takeUnretainedValue().callback()
        }, self.name, nil, .deliverImmediately)
    }

    deinit {
        CFNotificationCenterRemoveObserver(CFNotificationCenterGetDarwinNotifyCenter(), Unmanaged.passUnretained(self).toOpaque(), CFNotificationName(name), nil)
    }
}
