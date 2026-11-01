import Foundation

public final class ServerClient: Sendable {
    public let settings: ServerSettings
    let connection: ServerConnection
    // The slowest frame on real calls took 0.93 s, and 2 s after the words the server takes another one.
    private static let screenTimeout: TimeInterval = 5

    public init(settings: ServerSettings, configuration: URLSessionConfiguration? = nil, journal: (@Sendable (String) -> Void)? = nil) {
        self.settings = settings
        connection = ServerConnection(configuration: configuration ?? {
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForRequest = 20
            configuration.timeoutIntervalForResource = 60
            configuration.urlCache = nil
            return configuration
        }(), journal: journal)
    }

    deinit { connection.close() }

    public func createSession() async throws -> SessionResponse {
        try await json("POST", ["sessions"], body: CreateSessionRequest(settings: settings))
    }

    public func sendFrame(_ jpeg: Data, sessionID: String, width: Int, height: Int, timestampMS: Int64, small: Bool = false) async throws -> FrameResponse {
        var request = try request("POST", ["sessions", sessionID, "frames"])
        request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        request.setValue(String(width), forHTTPHeaderField: "X-Frame-Width")
        request.setValue(String(height), forHTTPHeaderField: "X-Frame-Height")
        request.setValue(String(timestampMS), forHTTPHeaderField: "X-Frame-Ts")
        if small { request.setValue("1", forHTTPHeaderField: "X-Frame-Small") }
        request.timeoutInterval = Self.screenTimeout
        let (data, response) = try await connection.session.upload(for: request, from: jpeg)
        try validate(response)
        return try JSONDecoder().decode(FrameResponse.self, from: data)
    }

    public func broadcast(_ state: BroadcastState, sessionID: String) async throws -> OKResponse {
        try await json("POST", ["sessions", sessionID, "broadcast"], body: BroadcastRequest(state: state))
    }

    public func sendScreenText(_ lines: [ScreenTextLine], sessionID: String, frameTS: Int64) async throws -> OKResponse {
        try await json("POST", ["sessions", sessionID, "text"], body: ScreenTextRequest(frameTS: frameTS, lines: lines), timeout: Self.screenTimeout)
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
        let (data, response) = try await connection.session.data(for: request)
        try validate(response)
        if (response as? HTTPURLResponse)?.statusCode == 204 { return nil }
        return try JSONDecoder().decode(ServerEvent.self, from: data)
    }

    /// A phrase the stream missed. The agent's reply comes as an event, like any other.
    public func say(_ wav: Data, sessionID: String) async throws -> SpokenReply {
        var request = try request("POST", ["sessions", sessionID, "say"])
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await connection.session.upload(for: request, from: wav)
        try validate(response)
        return try JSONDecoder().decode(SpokenReply.self, from: data)
    }

    /// The same as a spoken line, typed.
    public func words(_ text: String, sessionID: String) async throws -> OKResponse {
        try await json("POST", ["sessions", sessionID, "words"], body: WordsRequest(text: text))
    }

    /// The screens the phone kept, oldest first, for the line that asked for them.
    public func look(_ frames: [KeptScreen], sessionID: String) async throws -> OKResponse {
        let boundary = "ojo-" + UUID().uuidString
        var body = Data()
        for frame in frames {
            body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"frame\"; filename=\"\(frame.timestampMS).jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".utf8))
            body.append(frame.jpeg)
            body.append(Data("\r\n".utf8))
        }
        body.append(Data("--\(boundary)--\r\n".utf8))
        var request = try request("POST", ["sessions", sessionID, "look"])
        request.setValue("multipart/form-data; boundary=" + boundary, forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = Self.screenTimeout
        let (data, response) = try await connection.session.upload(for: request, from: body)
        try validate(response)
        return try JSONDecoder().decode(OKResponse.self, from: data)
    }

    /// The screen started to change, so a hint for the old one is no use.
    /// The first sound of a reply: from here on, new words are a new line, not the end of the old one.
    public func played(turn: Int, sessionID: String) async throws -> OKResponse {
        try await json("POST", ["sessions", sessionID, "played"], body: PlayedRequest(turn: turn))
    }

    /// Keeps the connection warm, so the voice's first request does not pay for opening it.
    public func health() async throws {
        let request = try request("GET", ["health"])
        let (_, response) = try await connection.session.data(for: request)
        try validate(response)
    }

    /// The agent's words as raw 24 kHz, 16-bit mono audio, while it is being made.
    public func speech(_ text: String, voice: String? = nil) async throws -> URLSession.AsyncBytes {
        var request = try request("POST", ["speech"])
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(SpeechRequest(text: text, voice: voice))
        request.timeoutInterval = 2.5  // Later than this, the system voice is the better answer.
        let (audio, response) = try await connection.session.bytes(for: request)
        try validate(response)
        return audio
    }

    public func voices() async throws -> VoicesResponse {
        try await getJSON(request("GET", ["voices"]))
    }

    public func eventSocket(sessionID: String) throws -> URLSessionWebSocketTask {
        try socket(["sessions", sessionID, "events"])
    }

    /// Carries the person's voice to the server while it sounds.
    public func listenSocket(sessionID: String) throws -> URLSessionWebSocketTask {
        try socket(["sessions", sessionID, "listen"])
    }

    private func socket(_ path: [String]) throws -> URLSessionWebSocketTask {
        var request = try request("GET", path)
        guard let url = request.url, var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            throw ClientError.invalidAddress
        }
        components.scheme = components.scheme == "https" ? "wss" : "ws"
        request.url = components.url
        return connection.session.webSocketTask(with: request)
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

    private func json<Body: Encodable, Response: Decodable>(_ method: String, _ path: [String], body: Body, timeout: TimeInterval? = nil) async throws -> Response {
        var request = try request(method, path)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        if let timeout { request.timeoutInterval = timeout }
        return try await getJSON(request)
    }

    private func getJSON<Response: Decodable>(_ request: URLRequest) async throws -> Response {
        let (data, response) = try await connection.session.data(for: request)
        try validate(response)
        return try JSONDecoder().decode(Response.self, from: data)
    }

    private func validate(_ response: URLResponse) throws {
        guard let response = response as? HTTPURLResponse else { throw ClientError.invalidResponse }
        guard (200..<300).contains(response.statusCode) else { throw ClientError.http(response.statusCode) }
    }
}

/// The connections of one client. One that hangs stays inside its URLSession and takes the next requests down with it,
/// so a timeout or a lost network starts a new URLSession. Sockets on the old one go on until they end by themselves.
final class ServerConnection: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private static let lost: Set<URLError.Code> = [.timedOut, .networkConnectionLost, .notConnectedToInternet]
    // A request that has just picked up the old session would crash if it were invalidated under it.
    private static let invalidateAfter: TimeInterval = 30
    private let configuration: URLSessionConfiguration
    private let journal: (@Sendable (String) -> Void)?
    private let lock = NSLock()
    private var current: URLSession?

    init(configuration: URLSessionConfiguration, journal: (@Sendable (String) -> Void)?) {
        self.configuration = configuration
        self.journal = journal
    }

    var session: URLSession {
        lock.withLock {
            if let current { return current }
            let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
            current = session
            return session
        }
    }

    func close() { lock.withLock { current }?.finishTasksAndInvalidate() }

    /// The system reports this ahead of the error, so a hung request names the connection it hung on.
    func urlSession(_ session: URLSession, task: URLSessionTask, didFinishCollecting metrics: URLSessionTaskMetrics) {
        let used = metrics.transactionMetrics.last
        let described = "\(task.originalRequest?.url?.lastPathComponent ?? "запрос"), \(used?.networkProtocolName ?? "неизвестно"), порт \(used?.localPort ?? 0)"
        if used?.networkProtocolName != nil, used?.isReusedConnection == false { journal?("Новое соединение: \(described)") }
        guard let code = (task.error as? URLError)?.code, Self.lost.contains(code) else { return }
        let first = lock.withLock {
            guard current === session else { return false }  // Another request has done it already.
            current = nil
            return true
        }
        guard first else { return }
        journal?("Соединение заменено, код \(code.rawValue): \(described)")
        DispatchQueue.global().asyncAfter(deadline: .now() + Self.invalidateAfter) { session.finishTasksAndInvalidate() }
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
