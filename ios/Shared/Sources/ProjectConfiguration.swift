import Foundation

public struct ProjectConfiguration {
    public let displayName: String
    public let appGroupIdentifier: String
    public let defaultServerURL: URL
    public let defaultServerToken: String

    // Read the host process bundle, including extensions.
    public init(bundle: Bundle = .main) throws {
        func value(_ key: String) throws -> String {
            guard let value = bundle.object(forInfoDictionaryKey: key) as? String,
                  !value.isEmpty, !value.contains("$(") else {
                throw ConfigurationError.missingValue(key)
            }
            return value
        }

        displayName = try value("CFBundleDisplayName")
        appGroupIdentifier = try value("AppGroupIdentifier")
        let serverAddress = try value("DefaultServerURL")
        guard let url = URL(string: serverAddress),
              ["https", "http"].contains(url.scheme), url.host != nil else {
            throw ConfigurationError.invalidServerURL
        }
        defaultServerURL = url
        let token = bundle.object(forInfoDictionaryKey: "DefaultServerToken") as? String ?? ""
        defaultServerToken = token.contains("$(") ? "" : token.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public var sharedDefaults: UserDefaults? {
        UserDefaults(suiteName: appGroupIdentifier)
    }

    public var screenMovedName: String { appGroupIdentifier + ".screen-moved" }

    public enum ConfigurationError: LocalizedError, ExplainedError {
        public func explained(in words: CallWords) -> String { words.setupFailed }

        case missingValue(String)
        case invalidServerURL

        public var errorDescription: String? {
            switch self {
            case .missingValue(let key):
                return "В Info.plist не задан параметр \(key)."
            case .invalidServerURL:
                return "В конфигурации задан неверный адрес сервера."
            }
        }
    }
}
