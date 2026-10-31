import Foundation

public enum TextEntry: String, Codable, CaseIterable, Sendable {
    case keyboard
    case clipboard
}

public enum ScreenTextMode: String, Codable, CaseIterable, Sendable {
    case app
    case `extension`
    case extensionFast
    case off
}

public struct ServerSettings: Sendable, Equatable {
    public let serverURL: URL
    public let token: String
    public let userLang: String
    public let targetLang: String
    public let textEntry: TextEntry
}

public final class SharedStore: @unchecked Sendable {
    private let defaults: UserDefaults
    private let defaultServerURL: URL

    public init(configuration: ProjectConfiguration, defaults: UserDefaults? = nil) throws {
        guard let defaults = defaults ?? configuration.sharedDefaults else {
            throw StoreError.unavailable
        }
        self.defaults = defaults
        defaultServerURL = configuration.defaultServerURL
        defaults.register(defaults: [
            "serverURL": configuration.defaultServerURL.absoluteString,
            "userLang": "ru", "targetLang": "id", "textEntry": TextEntry.keyboard.rawValue,
            "screenTextMode": ScreenTextMode.app.rawValue
        ])
    }

    public var settings: ServerSettings {
        refresh()
        return ServerSettings(
            serverURL: URL(string: defaults.string(forKey: "serverURL") ?? "") ?? defaultServerURL,
            token: defaults.string(forKey: "token") ?? "",
            userLang: defaults.string(forKey: "userLang") ?? "ru",
            targetLang: defaults.string(forKey: "targetLang") ?? "id",
            textEntry: TextEntry(rawValue: defaults.string(forKey: "textEntry") ?? "") ?? .keyboard
        )
    }

    public var sessionID: String? {
        get {
            refresh()
            return defaults.string(forKey: "sessionId").flatMap { $0.isEmpty ? nil : $0 }
        }
        set {
            defaults.set(newValue, forKey: "sessionId")
            defaults.synchronize()
        }
    }

    public var useVideoCallFallback: Bool {
        get { defaults.bool(forKey: "useVideoCallFallback") }
        set {
            defaults.set(newValue, forKey: "useVideoCallFallback")
            defaults.synchronize()
        }
    }

    public var screenTextMode: ScreenTextMode {
        get {
            refresh()
            return ScreenTextMode(rawValue: defaults.string(forKey: "screenTextMode") ?? "") ?? .app
        }
        set {
            defaults.set(newValue.rawValue, forKey: "screenTextMode")
            defaults.synchronize()
        }
    }

    public func seedDefaultToken(_ token: String) {
        refresh()
        guard (defaults.string(forKey: "token") ?? "").isEmpty, !token.isEmpty else { return }
        defaults.set(token, forKey: "token")
        defaults.synchronize()
    }

    public func save(serverAddress: String, token: String, userLang: String, targetLang: String, textEntry: TextEntry) throws {
        let address = serverAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: address), ["http", "https"].contains(url.scheme?.lowercased()),
              url.host != nil, url.query == nil, url.fragment == nil else {
            throw ProjectConfiguration.ConfigurationError.invalidServerURL
        }
        let user = userLang.trimmingCharacters(in: .whitespacesAndNewlines)
        let target = targetLang.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !user.isEmpty, !target.isEmpty else { throw StoreError.emptyLanguage }
        defaults.set(url.absoluteString, forKey: "serverURL")
        defaults.set(token.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "token")
        defaults.set(user, forKey: "userLang")
        defaults.set(target, forKey: "targetLang")
        defaults.set(textEntry.rawValue, forKey: "textEntry")
        defaults.synchronize()
    }

    private func refresh() {
        // Extensions read values written by a different process.
        defaults.synchronize()
    }

    private enum StoreError: LocalizedError {
        case unavailable
        case emptyLanguage

        var errorDescription: String? {
            switch self {
            case .unavailable: return "Общая группа приложения недоступна."
            case .emptyLanguage: return "Укажите код языка, например ru, id, en или auto."
            }
        }
    }
}
