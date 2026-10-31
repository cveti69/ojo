import Foundation

public struct CreateSessionRequest: Encodable, Sendable {
    public let goal: String
    public let userLang: String
    public let targetLang: String
    public let textEntry: TextEntry
    public let effort: ThinkingLevel?
    public let character: AgentCharacter
    public let ownCharacter: String
    public let mode: CallMode
    public let eyes: Bool?  // Left out when off, so nothing changes for the server.

    public init(settings: ServerSettings, goal: String = "") {
        self.goal = goal
        userLang = settings.userLang
        targetLang = settings.targetLang
        textEntry = settings.textEntry
        effort = settings.thinking == .auto ? nil : settings.thinking
        character = settings.character
        ownCharacter = settings.ownCharacter
        mode = settings.mode
        eyes = settings.eyes ? true : nil
    }

    enum CodingKeys: String, CodingKey {
        case goal, userLang = "user_lang", targetLang = "target_lang", textEntry = "text_entry"
        case effort, character, ownCharacter = "own_character", mode, eyes
    }
}

public struct SessionResponse: Decodable, Sendable {
    public let sessionID: String
    enum CodingKeys: String, CodingKey { case sessionID = "session_id" }
}

public struct GoalRequest: Encodable, Sendable { public let goal: String }
public struct GoalResponse: Decodable, Sendable {
    public let replyRU: String
    public let readyText: String?
    public let mode: CallMode?
    enum CodingKeys: String, CodingKey { case replyRU = "reply_ru", readyText = "ready_text", mode }
}
public struct ModeRequest: Encodable, Sendable { public let mode: CallMode }
public struct PlayedRequest: Encodable, Sendable { public let turn: Int }
public struct SpokenReply: Decodable, Sendable {
    public let heard: Bool
    public let speechMS: Int
    enum CodingKeys: String, CodingKey { case heard, speechMS = "speech_ms" }
}
public struct TextResponse: Decodable, Sendable { public let text: String }
public struct SpeechRequest: Encodable, Sendable { public let text: String }
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
    public let speak: Bool
    public let imageJPEGBase64: String
    public let insertTexts: [String]
    public let insertHintRU: String
    public let pasteText: String?
    public let needsUserConfirm: Bool
    public let frameTS: Int64?
    public let timings: HintTimings?
    public let eyesBox: [Double]?
    public let gaze: [Double]?
    public let calm: Bool?

    enum CodingKeys: String, CodingKey {
        case stepID = "step_id", instructionRU = "instruction_ru", speak, timings, gaze, calm
        case eyesBox = "eyes_box"
        case imageJPEGBase64 = "image_jpeg_base64", insertTexts = "insert_texts"
        case insertHintRU = "insert_hint_ru", pasteText = "paste_text", needsUserConfirm = "needs_user_confirm"
        case frameTS = "frame_ts"
    }
}

public struct HintTimings: Decodable, Sendable {
    public let queueMS: Int
    public let modelMS: Int
    public let boxMS: Int
    public let boxSource: String
    public let window: Bool?
    enum CodingKeys: String, CodingKey {
        case queueMS = "queue_ms", modelMS = "model_ms", boxMS = "box_ms", boxSource = "box_source", window
    }
}

public struct StatusEvent: Decodable, Sendable {
    public let state: String
    public let textRU: String
    public let newScreen: Bool?
    enum CodingKeys: String, CodingKey { case state, textRU = "text_ru", newScreen = "new_screen" }
}

public struct DoneEvent: Decodable, Sendable {
    public let textRU: String
    enum CodingKeys: String, CodingKey { case textRU = "text_ru" }
}

public struct SayEvent: Decodable, Sendable {
    public let turn: Int
    public let layer: String
    public let textRU: String
    public let goal: String?
    public let mode: CallMode?
    public let looks: Bool
    public let timings: SayTimings
    enum CodingKeys: String, CodingKey { case turn, layer, textRU = "text_ru", goal, mode, looks, timings }
}

public struct SayTimings: Decodable, Sendable {
    public let quickMS: Int?
    public let screenMS: Int?
    enum CodingKeys: String, CodingKey { case quickMS = "quick_ms", screenMS = "screen_ms" }
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
        default:
            throw DecodingError.dataCorruptedError(forKey: .type, in: container, debugDescription: "Unknown event type")
        }
    }

    private enum CodingKeys: String, CodingKey { case type }
}
