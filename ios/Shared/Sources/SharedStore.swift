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

/// `auto` leaves the choice to the server.
public enum ThinkingLevel: String, Codable, CaseIterable, Sendable {
    case auto, minimal, low, medium, high, xhigh, max
}

public enum AgentCharacter: String, Codable, CaseIterable, Sendable {
    case warm, strict, brief, patient, own
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
            "targetLang": "id", "textInput": TextEntry.clipboard.rawValue,
            "screenReading": ScreenTextMode.extension.rawValue
        ])
    }

    public var settings: ServerSettings {
        refresh()
        return ServerSettings(
            serverURL: URL(string: defaults.string(forKey: "serverURL") ?? "") ?? defaultServerURL,
            token: defaults.string(forKey: "token") ?? "",
            userLang: Locale.preferredLanguages.first ?? "en",  // The phone's language at every call, and never a saved one.
            targetLang: defaults.string(forKey: "targetLang") ?? "id",
            textEntry: TextEntry(rawValue: defaults.string(forKey: "textInput") ?? "") ?? .clipboard,
            thinking: ThinkingLevel(rawValue: defaults.string(forKey: "thinkingLevel") ?? "") ?? .auto,
            character: AgentCharacter(rawValue: defaults.string(forKey: "agentManner") ?? "") ?? .warm,
            ownCharacter: defaults.string(forKey: "ownCharacter") ?? "",
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

    /// The next line sounds in it. Nothing chosen leaves it to the server.
    public var voice: String? {
        get {
            refresh()
            return defaults.string(forKey: "voice")
        }
        set {
            defaults.set(newValue, forKey: "voice")
            defaults.synchronize()
        }
    }

    /// The app raises it to get the screen as it is now, and the broadcast sets `screenAnswered` to it once that is on the shelf.
    public var screenAsked: Int {
        get {
            refresh()
            return defaults.integer(forKey: "screenAsked")
        }
        set {
            defaults.set(newValue, forKey: "screenAsked")
            defaults.synchronize()
        }
    }

    public var screenAnswered: Int {
        get {
            refresh()
            return defaults.integer(forKey: "screenAnswered")
        }
        set {
            defaults.set(newValue, forKey: "screenAnswered")
            defaults.synchronize()
        }
    }

    /// When the broadcast saw the person leave the screen, on the clock of the frames.
    public var screenMovedAt: Int64 {
        get {
            refresh()
            return Int64(defaults.integer(forKey: "screenMovedAt"))
        }
        set {
            defaults.set(newValue, forKey: "screenMovedAt")
            defaults.synchronize()
        }
    }

    /// The frame that went to the model with no words read on it yet.
    public var textAsked: Int64 {
        get {
            refresh()
            return Int64(defaults.integer(forKey: "textAsked"))
        }
        set {
            defaults.set(newValue, forKey: "textAsked")
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

    /// The token a build carries is the current one, so a new one reaches a phone with the update. A build with none keeps the typed one.
    public func seedDefaultToken(_ token: String) {
        refresh()
        guard !token.isEmpty, token != defaults.string(forKey: "token") else { return }
        defaults.set(token, forKey: "token")
        defaults.synchronize()
    }

    public func save(serverAddress: String, token: String, targetLang: String, textEntry: TextEntry,
                     thinking: ThinkingLevel, character: AgentCharacter, ownCharacter: String) throws {
        let address = serverAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: address), ["http", "https"].contains(url.scheme?.lowercased()),
              url.host != nil, url.query == nil, url.fragment == nil else {
            throw ProjectConfiguration.ConfigurationError.invalidServerURL
        }
        let target = targetLang.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !target.isEmpty else { throw StoreError.emptyLanguage }
        defaults.set(url.absoluteString, forKey: "serverURL")
        defaults.set(token.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "token")
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
