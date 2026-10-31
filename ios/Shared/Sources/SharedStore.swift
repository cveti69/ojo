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

/// `auto` leaves the choice to the server: quick for the pointer, deeper for talk.
public enum ThinkingLevel: String, Codable, CaseIterable, Sendable {
    case auto, minimal, low, medium, high, xhigh, max
}

public enum AgentCharacter: String, Codable, CaseIterable, Sendable {
    case warm, strict, brief, patient, own
}

public enum CallMode: String, Codable, CaseIterable, Sendable {
    case lead, beside
}

public struct ServerSettings: Sendable, Equatable {
    public let serverURL: URL
    public let token: String
    public let userLang: String
    public let targetLang: String
    public let textEntry: TextEntry
    public var thinking: ThinkingLevel = .auto
    public var character: AgentCharacter = .warm
    public var ownCharacter = ""
    public var mode: CallMode = .lead
    public var eyes = false
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
            "userLang": "ru", "targetLang": "id", "textInput": TextEntry.clipboard.rawValue,
            "screenReading": ScreenTextMode.extension.rawValue, "hearsDuringCall": true
        ])
    }

    public var settings: ServerSettings {
        refresh()
        return ServerSettings(
            serverURL: URL(string: defaults.string(forKey: "serverURL") ?? "") ?? defaultServerURL,
            token: defaults.string(forKey: "token") ?? "",
            userLang: defaults.string(forKey: "userLang") ?? "ru",
            targetLang: defaults.string(forKey: "targetLang") ?? "id",
            textEntry: TextEntry(rawValue: defaults.string(forKey: "textInput") ?? "") ?? .clipboard,
            thinking: ThinkingLevel(rawValue: defaults.string(forKey: "thinkingLevel") ?? "") ?? .auto,
            character: AgentCharacter(rawValue: defaults.string(forKey: "agentManner") ?? "") ?? .warm,
            ownCharacter: defaults.string(forKey: "ownCharacter") ?? "",
            mode: callMode,
            eyes: defaults.bool(forKey: "eyes")
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

    public var hearsDuringCall: Bool {
        get { defaults.bool(forKey: "hearsDuringCall") }
        set {
            defaults.set(newValue, forKey: "hearsDuringCall")
            defaults.synchronize()
        }
    }

    /// The app writes it, and the broadcast reads it to know whether frames may leave the phone.
    public var callMode: CallMode {
        get {
            refresh()
            return CallMode(rawValue: defaults.string(forKey: "callMode") ?? "") ?? .lead
        }
        set {
            defaults.set(newValue.rawValue, forKey: "callMode")
            defaults.synchronize()
        }
    }

    public var measuresAppleSpeech: Bool {
        get { defaults.bool(forKey: "measuresAppleSpeech") }
        set {
            defaults.set(newValue, forKey: "measuresAppleSpeech")
            defaults.synchronize()
        }
    }

    public var hearsOverVoice: Bool {
        get { defaults.bool(forKey: "hearsOverVoice") }
        set {
            defaults.set(newValue, forKey: "hearsOverVoice")
            defaults.synchronize()
        }
    }

    public var eyes: Bool {
        get { defaults.bool(forKey: "eyes") }
        set {
            defaults.set(newValue, forKey: "eyes")
            defaults.synchronize()
        }
    }

    public var screenTextMode: ScreenTextMode {
        get {
            refresh()
            return ScreenTextMode(rawValue: defaults.string(forKey: "screenReading") ?? "") ?? .extension
        }
        set {
            defaults.set(newValue.rawValue, forKey: "screenReading")
            defaults.synchronize()
        }
    }

    public func seedDefaultToken(_ token: String) {
        refresh()
        guard (defaults.string(forKey: "token") ?? "").isEmpty, !token.isEmpty else { return }
        defaults.set(token, forKey: "token")
        defaults.synchronize()
    }

    public func save(serverAddress: String, token: String, userLang: String, targetLang: String, textEntry: TextEntry,
                     thinking: ThinkingLevel, character: AgentCharacter, ownCharacter: String) throws {
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
        defaults.set(textEntry.rawValue, forKey: "textInput")
        defaults.set(thinking.rawValue, forKey: "thinkingLevel")
        defaults.set(character.rawValue, forKey: "agentManner")
        defaults.set(String(ownCharacter.trimmingCharacters(in: .whitespacesAndNewlines).prefix(500)), forKey: "ownCharacter")
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
