import Foundation

public final class ServerClient: Sendable {
    public let settings: ServerSettings
    private let session: URLSession

    public init(settings: ServerSettings, session: URLSession? = nil) {
        self.settings = settings
        if let session {
            self.session = session
        } else {
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForRequest = 20
            configuration.timeoutIntervalForResource = 60
            configuration.urlCache = nil
            self.session = URLSession(configuration: configuration)
        }
    }

    public func createSession() async throws -> SessionResponse {
        try await json("POST", ["sessions"], body: CreateSessionRequest(settings: settings))
    }

    public func setGoal(_ goal: String, sessionID: String) async throws -> GoalResponse {
        try await json("POST", ["sessions", sessionID, "goal"], body: GoalRequest(goal: goal))
    }

    public func transcribe(wavURL: URL) async throws -> TextResponse {
        var request = try request("POST", ["transcribe"])
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 45
        let (data, response) = try await session.upload(for: request, fromFile: wavURL)
        try validate(response)
        return try JSONDecoder().decode(TextResponse.self, from: data)
    }

    public func sendFrame(_ jpeg: Data, sessionID: String, width: Int, height: Int, timestampMS: Int64) async throws -> FrameResponse {
        var request = try request("POST", ["sessions", sessionID, "frames"])
        request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        request.setValue(String(width), forHTTPHeaderField: "X-Frame-Width")
        request.setValue(String(height), forHTTPHeaderField: "X-Frame-Height")
        request.setValue(String(timestampMS), forHTTPHeaderField: "X-Frame-Ts")
        let (data, response) = try await session.upload(for: request, from: jpeg)
        try validate(response)
        return try JSONDecoder().decode(FrameResponse.self, from: data)
    }

    public func broadcast(_ state: BroadcastState, sessionID: String) async throws -> OKResponse {
        try await json("POST", ["sessions", sessionID, "broadcast"], body: BroadcastRequest(state: state))
    }

    public func sendScreenText(_ lines: [ScreenTextLine], sessionID: String, frameTS: Int64) async throws -> OKResponse {
        try await json("POST", ["sessions", sessionID, "text"], body: ScreenTextRequest(frameTS: frameTS, lines: lines))
    }

    public func end(sessionID: String) async throws -> OKResponse {
        try await getJSON(request("POST", ["sessions", sessionID, "end"]))
    }

    public func suggestions(sessionID: String) async throws -> SuggestionsResponse {
        try await getJSON(request("GET", ["sessions", sessionID, "suggestions"]))
    }

    public func translate(_ text: String, sessionID: String) async throws -> TextResponse {
        try await json("POST", ["translate"], body: TranslateRequest(
            text: text, from: settings.userLang, to: settings.targetLang, sessionID: sessionID
        ))
    }

    public func hint(sessionID: String, after: String?) async throws -> ServerEvent? {
        var request = try request("GET", ["sessions", sessionID, "hint"])
        if let after, var components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false) {
            components.queryItems = [URLQueryItem(name: "after", value: after)]
            request.url = components.url
        }
        let (data, response) = try await session.data(for: request)
        try validate(response)
        if (response as? HTTPURLResponse)?.statusCode == 204 { return nil }
        return try JSONDecoder().decode(ServerEvent.self, from: data)
    }

    public func eventSocket(sessionID: String) throws -> URLSessionWebSocketTask {
        var request = try request("GET", ["sessions", sessionID, "events"])
        guard let url = request.url, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            throw ClientError.invalidAddress
        }
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        request.url = components.url
        return session.webSocketTask(with: request)
    }

    private func request(_ method: String, _ path: [String]) throws -> URLRequest {
        guard !settings.token.isEmpty else { throw ClientError.missingToken }
        guard ["https", "http"].contains(settings.serverURL.scheme?.lowercased()), settings.serverURL.host != nil else {
            throw ClientError.invalidAddress
        }
        var url = settings.serverURL
        for component in path { url.appendPathComponent(component) }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue(settings.token, forHTTPHeaderField: "X-Token")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    private func json<Body: Encodable, Response: Decodable>(_ method: String, _ path: [String], body: Body) async throws -> Response {
        var request = try request(method, path)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        return try await getJSON(request)
    }

    private func getJSON<Response: Decodable>(_ request: URLRequest) async throws -> Response {
        let (data, response) = try await session.data(for: request)
        try validate(response)
        return try JSONDecoder().decode(Response.self, from: data)
    }

    private func validate(_ response: URLResponse) throws {
        guard let response = response as? HTTPURLResponse else { throw ClientError.invalidResponse }
        guard (200..<300).contains(response.statusCode) else { throw ClientError.http(response.statusCode) }
    }
}

public enum ClientError: LocalizedError, Equatable {
    case missingToken
    case invalidAddress
    case invalidResponse
    case http(Int)

    public var errorDescription: String? {
        switch self {
        case .missingToken: return "Укажите токен сервера в настройках."
        case .invalidAddress: return "Проверьте адрес сервера в настройках."
        case .invalidResponse: return "Сервер прислал неверный ответ."
        case .http(401): return "Сервер отклонил токен. Проверьте настройки."
        case .http(404): return "Сессия не найдена. Завершите звонок и позвоните снова."
        case .http(let code): return "Ошибка сервера (HTTP \(code))."
        }
    }
}
