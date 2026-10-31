import Foundation

public struct CreateSessionRequest: Encodable, Sendable {
    public let userLang: String
    public let targetLang: String
    public let textEntry: TextEntry
    public let effort: ThinkingLevel?
    public let character: AgentCharacter
    public let ownCharacter: String
    public let eyes: Bool?  // Left out when off, so nothing changes for the server.

    public init(settings: ServerSettings) {
        userLang = settings.userLang
        targetLang = settings.targetLang
        textEntry = settings.textEntry
        effort = settings.thinking == .auto ? nil : settings.thinking
        character = settings.character
        ownCharacter = settings.ownCharacter
        eyes = settings.eyes ? true : nil
    }

    enum CodingKeys: String, CodingKey {
        case userLang = "user_lang", targetLang = "target_lang", textEntry = "text_entry"
        case effort, character, ownCharacter = "own_character", eyes
    }
}

public struct SessionResponse: Decodable, Sendable {
    public let sessionID: String
    enum CodingKeys: String, CodingKey { case sessionID = "session_id" }
}

public struct WordsRequest: Encodable, Sendable { public let text: String }
public struct PlayedRequest: Encodable, Sendable { public let turn: Int }
public struct SpokenReply: Decodable, Sendable {
    public let heard: Bool
    public let speechMS: Int
    enum CodingKeys: String, CodingKey { case heard, speechMS = "speech_ms" }
}
public struct TextResponse: Decodable, Sendable { public let text: String }
public struct SpeechRequest: Encodable, Sendable {
    public let text: String
    public let voice: String?
}
public struct Voice: Decodable, Sendable, Identifiable, Equatable {
    public let id: String
    public let name: String
}
public struct VoicesResponse: Decodable, Sendable {
    public let voices: [Voice]
    public let defaultVoice: String
    enum CodingKeys: String, CodingKey { case voices, defaultVoice = "default" }
}
public struct OKResponse: Decodable, Sendable { public let ok: Bool }
public struct FrameResponse: Decodable, Sendable {
    public let accepted: Bool
    public let reason: String?
}

public struct SuggestionsResponse: Decodable, Sendable {
    public let stepID: String?
    public let texts: [String]
    public let hintRU: String
    enum CodingKeys: String, CodingKey { case stepID = "step_id", texts, hintRU = "hint_ru" }
}

public struct TranslateRequest: Encodable, Sendable {
    public let text: String
    public let from: String
    public let to: String
    public let sessionID: String
    enum CodingKeys: String, CodingKey { case text, from, to, sessionID = "session_id" }
}

public enum BroadcastState: String, Encodable, Sendable { case started, finished }
public struct BroadcastRequest: Encodable, Sendable { public let state: BroadcastState }

public struct HintEvent: Decodable, Sendable {
    public let stepID: String
    public let instructionRU: String
    public let imageJPEGBase64: String
    public let frameTS: Int64?
    public let timings: HintTimings?
    public let eyesBox: [Double]?
    public let gaze: [Double]?
    public let calm: Bool?

    enum CodingKeys: String, CodingKey {
        case stepID = "step_id", instructionRU = "instruction_ru", timings, gaze, calm
        case eyesBox = "eyes_box", imageJPEGBase64 = "image_jpeg_base64", frameTS = "frame_ts"
    }
}

public struct HintTimings: Decodable, Sendable {
    public let modelMS: Int
    public let boxMS: Int
    public let boxSource: String
    public let window: Bool?
    enum CodingKeys: String, CodingKey {
        case modelMS = "model_ms", boxMS = "box_ms", boxSource = "box_source", window
    }
}

public struct StatusEvent: Decodable, Sendable {
    public let state: String
    public let textRU: String
    enum CodingKeys: String, CodingKey { case state, textRU = "text_ru" }
}

public struct DoneEvent: Decodable, Sendable {
    public let textRU: String
    enum CodingKeys: String, CodingKey { case textRU = "text_ru" }
}

public struct SayEvent: Decodable, Sendable {
    public let turn: Int
    public let textRU: String
    public let pasteText: String?
    public let timings: SayTimings
    enum CodingKeys: String, CodingKey { case turn, textRU = "text_ru", pasteText = "paste_text", timings }
}

public struct FaceEvent: Decodable, Sendable {
    public let turn: Int
    public let face: String?
    public let calm: Bool
}

public struct SayTimings: Decodable, Sendable {
    public let modelMS: Int
    enum CodingKeys: String, CodingKey { case modelMS = "model_ms" }
}

public struct TurnEvent: Decodable, Sendable {
    public let turn: Int
}

public enum ServerEvent: Decodable, Sendable {
    case hint(HintEvent)
    case status(StatusEvent)
    case done(DoneEvent)
    case say(SayEvent)
    case heard(TurnEvent)
    case look(TurnEvent)
    case quiet(TurnEvent)
    case left
    case reading
    case face(FaceEvent)

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        switch try container.decode(String.self, forKey: .type) {
        case "hint": self = .hint(try HintEvent(from: decoder))
        case "status": self = .status(try StatusEvent(from: decoder))
        case "done": self = .done(try DoneEvent(from: decoder))
        case "say": self = .say(try SayEvent(from: decoder))
        case "heard": self = .heard(try TurnEvent(from: decoder))
        case "look": self = .look(try TurnEvent(from: decoder))
        case "quiet": self = .quiet(try TurnEvent(from: decoder))
        case "left": self = .left
        case "reading": self = .reading
        case "face": self = .face(try FaceEvent(from: decoder))
        default:
            throw DecodingError.dataCorruptedError(forKey: .type, in: container, debugDescription: "Unknown event type")
        }
    }

    private enum CodingKeys: String, CodingKey { case type }
}
