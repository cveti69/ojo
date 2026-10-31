@preconcurrency import AVFAudio
import Foundation
import CoreGraphics
import CoreText
import Darwin
import ImageIO

private struct CheckFailure: Error, CustomStringConvertible {
    let description: String
}

private func check(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw CheckFailure(description: message) }
}

private final class MemoryDefaults: UserDefaults, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: Any] = [:]
    private var registered: [String: Any] = [:]

    init(memory: Void = ()) { super.init(suiteName: nil)! }
    override func object(forKey key: String) -> Any? { lock.withLock { values[key] ?? registered[key] } }
    override func string(forKey key: String) -> String? { object(forKey: key) as? String }
    override func set(_ value: Any?, forKey key: String) { lock.withLock { values[key] = value } }
    override func register(defaults: [String: Any]) { lock.withLock { registered.merge(defaults) { _, new in new } } }
    override func synchronize() -> Bool { true }
}

private final class StubState: @unchecked Sendable {
    private let lock = NSLock()
    private var requests: [URLRequest] = []
    private var status: Int?
    private var noHint = false

    func next(status: Int? = nil, noHint: Bool = false) {
        lock.lock()
        defer { lock.unlock() }
        self.status = status
        self.noHint = noHint
    }

    func response(to request: URLRequest) -> (Int, String) {
        lock.lock()
        defer { lock.unlock() }
        requests.append(request)
        if let status { self.status = nil; return (status, "{}") }
        let path = request.url!.path
        if path.hasSuffix("/hint"), noHint { noHint = false; return (204, "") }
        if path == "/api/sessions" { return (201, #"{"session_id":"session-test"}"#) }
        if path.hasSuffix("/voices") { return (200, #"{"voices":[{"id":"voice-1","name":"George"}],"default":"voice-1"}"#) }
        if path.hasSuffix("/frames") { return (202, #"{"accepted":false,"reason":"busy"}"#) }
        if path.hasSuffix("/speech") { return (200, "abcd") }
        if path.hasSuffix("/say") {
            return (200, #"{"heard":true,"speech_ms":1200}"#)
        }
        if path.hasSuffix("/suggestions") { return (200, #"{"step_id":"s3","texts":["hello"],"hint_ru":"запрос"}"#) }
        if path.hasSuffix("/translate") { return (200, #"{"text":"Результат"}"#) }
        if path.hasSuffix("/hint") {
            return (200, #"{"type":"hint","step_id":"s3","instruction_ru":"Вставьте текст","image_jpeg_base64":"AA==","frame_ts":123456789,"timings":{"model_ms":2710,"box_ms":3,"box_source":"text","window":true}}"#)
        }
        return (200, #"{"ok":true}"#)
    }

    func captured() -> [URLRequest] {
        lock.lock()
        defer { lock.unlock() }
        return requests
    }
}

private final class ProtocolStub: URLProtocol, @unchecked Sendable {
    static let state = StubState()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let (status, body) = Self.state.response(to: request)
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil,
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@main
private enum ProtocolChecks {
    static func main() async {
        do { try await run() }
        catch {
            let description = String(describing: error)
            let native = error as NSError
            FileHandle.standardError.write(Data("Checks failed: \(description); \(native.domain) (\(native.code))\n".utf8))
            exit(EXIT_FAILURE)
        }
    }

    private static func run() async throws {
        let settings = ServerSettings(serverURL: URL(string: "https://example.invalid/api")!, token: "test-token",
                                      userLang: "ru", targetLang: "auto", textEntry: .clipboard)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ProtocolStub.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = ServerClient(settings: settings, session: session)
        let created = try await client.createSession()
        try check(created.sessionID == "session-test", "session_id decoding")
        _ = try await client.words("Что это?", sessionID: created.sessionID)
        var spoken = 0
        for try await _ in try await client.speech("Привет", voice: "voice-1") { spoken += 1 }
        try check(spoken == 4, "speech bytes")
        let voices = try await client.voices()
        try check(voices.voices == [Voice(id: "voice-1", name: "George")] && voices.defaultVoice == "voice-1", "the list of voices")
        let said = try await client.say(Data("RIFFtestWAVE".utf8), sessionID: created.sessionID)
        try check(said.heard && said.speechMS == 1200, "spoken line response decoding")
        let looked = try await client.look([KeptScreen(jpeg: Data([1, 2]), timestampMS: 15), KeptScreen(jpeg: Data([3, 4]), timestampMS: 16)],
                                           sessionID: created.sessionID)
        try check(looked.ok, "look response")
        _ = try await client.played(turn: 3, sessionID: created.sessionID)
        try await client.health()
        let frame = try await client.sendFrame(Data([0xff, 0xd8, 0xff, 0xd9]), sessionID: created.sessionID,
                                               width: 1170, height: 2532, timestampMS: 123456789)
        try check(!frame.accepted && frame.reason == "busy", "busy frame response")
        let lines = [ScreenTextLine(text: "Beli sekarang", words: [
            ScreenTextWord(text: "Beli", box: [0.1, 0.8, 0.2, 0.83]),
            ScreenTextWord(text: "sekarang", box: [0.21, 0.8, 0.4, 0.83])
        ])]
        let textAccepted = try await client.sendScreenText(lines, sessionID: created.sessionID, frameTS: 123456789)
        try check(textAccepted.ok, "screen text response")
        let suggestions = try await client.suggestions(sessionID: created.sessionID)
        try check(suggestions.stepID == "s3" && suggestions.texts == ["hello"], "suggestions decoding")
        _ = try await client.translate("Привет", sessionID: created.sessionID)
        _ = try await client.broadcast(.started, sessionID: created.sessionID)
        _ = try await client.broadcast(.finished, sessionID: created.sessionID)
        _ = try await client.end(sessionID: created.sessionID)
        let hint = try await client.hint(sessionID: created.sessionID, after: "s 2+?")
        guard case .hint(let value) = hint else { throw CheckFailure(description: "hint decoding") }
        try check(value.instructionRU == "Вставьте текст" && value.timings?.boxSource == "text", "hint fields")
        try check(value.frameTS == 123456789, "hint frame_ts decoding")
        ProtocolStub.state.next(noHint: true)
        let empty = try await client.hint(sessionID: created.sessionID, after: "s3")
        try check(empty == nil, "204 response")
        for status in [401, 404, 503] {
            ProtocolStub.state.next(status: status)
            do {
                _ = try await client.suggestions(sessionID: created.sessionID)
                throw CheckFailure(description: "HTTP \(status) accepted")
            } catch let error as ClientError {
                try check(error == .http(status), "HTTP error mapping")
            }
        }
        let socket = try client.eventSocket(sessionID: created.sessionID)
        try check(socket.originalRequest?.url?.scheme == "wss", "WebSocket scheme")
        try check(socket.originalRequest?.url?.path == "/api/sessions/session-test/events", "WebSocket path")
        try check(socket.originalRequest?.value(forHTTPHeaderField: "X-Token") == "test-token", "WebSocket token")
        socket.cancel(with: .goingAway, reason: nil)
        let listening = try client.listenSocket(sessionID: created.sessionID)
        try check(listening.originalRequest?.url?.scheme == "wss", "speech stream scheme")
        try check(listening.originalRequest?.url?.path == "/api/sessions/session-test/listen", "speech stream path")
        try check(listening.originalRequest?.value(forHTTPHeaderField: "X-Token") == "test-token", "speech stream token")
        listening.cancel(with: .goingAway, reason: nil)

        let requests = ProtocolStub.state.captured()
        try check(requests.allSatisfy { $0.value(forHTTPHeaderField: "X-Token") == "test-token" }, "X-Token on every HTTP request")
        let line = requests.first { $0.url!.path.hasSuffix("/say") }!
        try check(line.url!.path == "/api/sessions/session-test/say" && line.value(forHTTPHeaderField: "Content-Type") == "audio/wav",
                  "spoken line endpoint")
        let create = requests.first { $0.url!.path == "/api/sessions" }!
        let createBody = try JSONSerialization.jsonObject(with: body(of: create)) as! [String: String]
        try check(createBody == ["user_lang": "ru", "target_lang": "auto", "text_entry": "clipboard",
                                 "character": "warm", "own_character": ""], "create-session body leaves the level to the server")
        var chosen = settings
        chosen.thinking = .high
        let chosenBody = try JSONSerialization.jsonObject(with: JSONEncoder().encode(CreateSessionRequest(settings: chosen))) as! [String: String]
        try check(chosenBody["effort"] == "high", "a chosen level reaches the server")
        let typedLine = requests.first { $0.url!.path.hasSuffix("/words") }!
        let typedBody = try JSONSerialization.jsonObject(with: body(of: typedLine)) as! [String: String]
        try check(typedBody == ["text": "Что это?"], "typed line body")
        let speech = requests.first { $0.url!.path.hasSuffix("/speech") }!
        let speechBody = try JSONSerialization.jsonObject(with: body(of: speech)) as! [String: String]
        try check(speechBody == ["text": "Привет", "voice": "voice-1"], "the chosen voice goes with the line")
        let lookRequest = requests.first { $0.url!.path.hasSuffix("/look") }!
        let lookType = lookRequest.value(forHTTPHeaderField: "Content-Type") ?? ""
        try check(lookType.hasPrefix("multipart/form-data; boundary="), "look format")
        let lookBody = String(decoding: try body(of: lookRequest), as: UTF8.self)
        try check(lookBody.components(separatedBy: #"name="frame""#).count == 3 && lookBody.hasSuffix("--\r\n"), "look sends every frame")
        try check(lookBody.contains(#"filename="15.jpg""#) && lookBody.contains(#"filename="16.jpg""#), "each frame says when it was taken")
        let playedRequest = requests.first { $0.url!.path.hasSuffix("/played") }!
        let playedBody = try JSONSerialization.jsonObject(with: body(of: playedRequest)) as! [String: Int]
        try check(playedBody == ["turn": 3], "played body")
        let translate = requests.first { $0.url!.path.hasSuffix("/translate") }!
        let translateBody = try JSONSerialization.jsonObject(with: body(of: translate)) as! [String: String]
        try check(translateBody["from"] == "ru" && translateBody["to"] == "auto" && translateBody["session_id"] == created.sessionID,
                  "translation languages and session")
        let upload = requests.first { $0.url!.path.hasSuffix("/frames") }!
        try check(upload.httpMethod == "POST" && upload.value(forHTTPHeaderField: "Content-Type") == "image/jpeg", "frame format")
        try check(upload.value(forHTTPHeaderField: "X-Frame-Width") == "1170"
                  && upload.value(forHTTPHeaderField: "X-Frame-Height") == "2532"
                  && upload.value(forHTTPHeaderField: "X-Frame-Ts") == "123456789"
                  && upload.value(forHTTPHeaderField: "X-Frame-Small") == nil, "frame metadata")
        let screenText = requests.first { $0.url!.path.hasSuffix("/text") }!
        let textBody = try JSONDecoder().decode(ScreenTextRequest.self, from: body(of: screenText))
        try check(screenText.url!.path == "/api/sessions/session-test/text" && screenText.httpMethod == "POST"
                  && screenText.value(forHTTPHeaderField: "Content-Type") == "application/json", "screen text endpoint")
        try check(textBody.frameTS == Int64(upload.value(forHTTPHeaderField: "X-Frame-Ts")!) && textBody.lines == lines,
                  "text payload matches uploaded frame timestamp and word boxes")
        let poll = requests.first { $0.url!.path.hasSuffix("/hint") }!
        let cursor = URLComponents(url: poll.url!, resolvingAgainstBaseURL: false)?.queryItems?.first?.value
        try check(cursor == "s 2+?", "poll cursor escaping")
        let states = try requests.filter { $0.url!.path.hasSuffix("/broadcast") }.map {
            try JSONSerialization.jsonObject(with: body(of: $0)) as! [String: String]
        }
        try check(states == [["state": "started"], ["state": "finished"]], "broadcast lifecycle bodies")
        let status = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"status","state":"broadcasting","text_ru":"Демонстрация экрана включена"}"#.utf8))
        guard case .status(let message) = status else { throw CheckFailure(description: "status event") }
        try check(message.state == "broadcasting", "status state")
        let done = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"done","text_ru":"Готово"}"#.utf8))
        guard case .done = done else { throw CheckFailure(description: "done event") }
        let sayEvent = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"say","turn":3,"text_ru":"Жми зелёную.","paste_text":"sampo","timings":{"model_ms":900}}"#.utf8))
        guard case .say(let say) = sayEvent else { throw CheckFailure(description: "say event") }
        try check(say.turn == 3 && say.textRU == "Жми зелёную." && say.pasteText == "sampo" && say.timings.modelMS == 900, "say event fields")
        guard case .heard(let heard) = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"heard","turn":4}"#.utf8)), heard.turn == 4 else {
            throw CheckFailure(description: "heard event")
        }
        guard case .left = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"left"}"#.utf8)) else {
            throw CheckFailure(description: "left event")
        }
        guard case .look(let look) = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"look","turn":5}"#.utf8)), look.turn == 5 else {
            throw CheckFailure(description: "look event")
        }
        guard case .reading = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"reading","turn":6}"#.utf8)) else {
            throw CheckFailure(description: "the eyes get a new screen to read")
        }
        guard case .face(let face) = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"face","turn":6,"face":"стоп","calm":false}"#.utf8)),
              face.face.flatMap(EyesFace.init(rawValue:)) == .stopped, !face.calm else {
            throw CheckFailure(description: "the eyes get the model's face")
        }
        guard case .face(let calm) = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"face","turn":6,"face":null,"calm":true}"#.utf8)),
              calm.face == nil, calm.calm else {
            throw CheckFailure(description: "a payment screen has no face")
        }
        guard case .quiet(let quiet) = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"quiet","turn":6}"#.utf8)), quiet.turn == 6 else {
            throw CheckFailure(description: "quiet event")
        }
        try checkSpeechDetector()
        try await checkPhraseListener()
        try checkStillness()
        try checkShelf()
        try checkPauseWords()
        try checkScreenTextGeometry()
        try checkSettingsAndMailbox()
        try await checkMemoryGate()
        if CommandLine.arguments.contains("--vision") { try await checkVision() }
        print("Protocol checks passed: HTTP, /text, frame_ts, word fallback, languages, mailbox clear, extensionFast, memory gate, token defaults, WAV, lifecycle, events, errors, WebSocket.")
    }

    /// A tone at the given level in dB. Each repetition of the seed gives a slightly different noise.
    private static func sound(seconds: Double, level: Double, hertz: Double = 220, rate: Double = 16_000) -> [Float] {
        let amplitude = pow(10, level / 20) * 2.0.squareRoot()
        return (0..<Int(seconds * rate)).map { index in
            let wave = hertz > 0 ? sin(2 * .pi * hertz * Double(index) / rate) : Double.random(in: -1...1)
            return Float(amplitude * wave)
        }
    }

    private static func pcm(_ samples: [Float]) -> [Int16] { samples.map { Int16(max(-1, min(1, $0)) * 32767) } }

    private static func checkSpeechDetector() throws {
        func phrases(_ parts: [Float]...) -> [Phrase] {
            var detector = SpeechDetector()
            return parts.flatMap { detector.add(pcm($0)) }
        }
        func room(_ seconds: Double) -> [Float] { sound(seconds: seconds, level: -60, hertz: 0) }
        func voice(_ seconds: Double) -> [Float] { sound(seconds: seconds, level: -25) }
        try check(phrases(room(3)).isEmpty, "a quiet room is no speech")
        try check(phrases(room(1), sound(seconds: 0.1, level: -15), room(2)).isEmpty, "a tap on the phone is no speech")
        let one = phrases(room(1), voice(1.5), room(0.4), voice(1), room(1.2))
        try check(one.count == 1, "a short pause stays inside the phrase, got \(one.count)")
        try check(abs(one[0].seconds - 3.6) < 0.25, "phrase keeps its start and a little quiet, got \(one[0].seconds)")
        try check(one[0].noiseFloor < -55 && one[0].loudest > -30, "phrase levels")
        try check(phrases(room(1), voice(1), room(0.5)).isEmpty, "a phrase is not over before the pause is")
        try check(phrases(room(1), voice(1), room(1.2), voice(1), room(1.2)).count == 2, "a long pause splits phrases")
        let long = phrases(room(1), voice(20))
        try check(long.count == 1 && abs(long[0].seconds - 15) < 0.1, "the longest phrase, got \(long.map(\.seconds))")
        let noisy = phrases(sound(seconds: 1, level: -35, hertz: 0), sound(seconds: 1, level: -28), sound(seconds: 1.2, level: -35, hertz: 0))
        try check(noisy.isEmpty, "speech has to stand out from a loud room")
        var muted = SpeechDetector()
        _ = muted.add([Int16](repeating: 0, count: 16_000))
        let afterMute = muted.add(pcm(room(1) + voice(1) + room(1.2)))
        try check(afterMute.count == 1, "the voice filter's silence does not teach the room level")
        var cut = SpeechDetector()
        _ = cut.add(pcm(room(1) + voice(0.6)))
        try check(cut.finish() != nil && cut.finish() == nil, "finishing hands over what was heard, once")
        var short = SpeechDetector()
        _ = short.add(pcm(room(1) + voice(0.15)))
        try check(short.finish() == nil, "finishing drops a click")
        try check(short.droppedSounds == 1 && short.loudestDropped > -30, "a dropped click is counted")
        var streaming = SpeechDetector()
        _ = streaming.add(pcm(room(1)))
        try check(streaming.takeFresh().isEmpty && !streaming.isHearing, "a quiet room goes nowhere")
        _ = streaming.add(pcm(voice(0.5)))
        try check(streaming.isHearing && streaming.isTalking, "the detector knows the person is talking")
        try check(streaming.isSpeaking, "the voice waits for a person")
        var distant = SpeechDetector()
        _ = distant.add(pcm(room(1) + sound(seconds: 0.5, level: -48)))
        try check(distant.isHearing && !distant.isSpeaking, "but not for a sound in the room")
        let opening = streaming.takeFresh()
        try check(opening.count == 16_000 && streaming.takeFresh().isEmpty, "the stream starts with the sound before the first word, got \(opening.count)")
        _ = streaming.add(pcm(voice(0.2)))
        try check(streaming.takeFresh().count == 3_200, "each new block follows")
        try check(Date().timeIntervalSince(streaming.lastLoud) < 1, "the last loud sound has a time")
        let ended = streaming.add(pcm(room(1)))
        try check(ended.count == 1 && !streaming.isHearing, "the phrase ends for the voice to start")
        var tailing = SpeechDetector()
        _ = tailing.add(pcm(room(1) + voice(1)))
        _ = tailing.takeFresh()
        _ = tailing.add(pcm(room(0.3)))
        try check(tailing.isHearing, "the quiet inside a phrase is still the phrase")
        let file = one[0].wav
        try check(file.count == 44 + one[0].samples.count * 2 && file.prefix(4) == Data("RIFF".utf8), "WAV size")
        let header = file.withUnsafeBytes { (rate: $0.loadUnaligned(fromByteOffset: 24, as: UInt32.self),
                                             size: $0.loadUnaligned(fromByteOffset: 40, as: UInt32.self)) }
        try check(header.rate == 16_000 && Int(header.size) == one[0].samples.count * 2, "WAV header")
    }

    private static func checkStillness() throws {
        let side = [UInt8](repeating: 100, count: 16)
        var other = side
        for index in 0..<8 { other[index] = 200 }
        var eyes = ScreenStillness()
        try check(!eyes.see(side, at: 0), "the first look decides nothing")
        try check(!eyes.see(side, at: 1), "a second is not still enough")
        try check(eyes.see(side, at: 1.6), "a screen that stopped is worth a look")
        try check(!eyes.see(other, at: 2), "a change starts the count again")
        try check(!eyes.see(side, at: 2.5) && !eyes.see(other, at: 3) && !eyes.see(side, at: 4), "a moving screen is not worth a look")
        try check(eyes.see(other, at: 7.01), "a screen that never stops gets a look after all")
        var cursor = other
        cursor[15] &+= 3
        var calm = ScreenStillness()
        _ = calm.see(other, at: 0)
        try check(calm.see(cursor, at: 2), "a blinking cursor is no change")
        try check(ScreenStillness.difference(side, other) > 0.1 && ScreenStillness.difference(side, side) == 0, "difference")
        try check(ScreenStillness.difference(side, []) == 1, "different sizes count as a new screen")
        try checkWindowLeftOut()
        try checkSmallChange()
        try checkWindowFinder()
        try checkEyes()
    }

    /// A page with a picture of its own, and a window whose picture moves every look.
    private static func movingWindow(_ look: Int) -> [UInt8] {
        var picture = (0..<4096).map { UInt8(120 + ($0 * 7) % 90) }
        for y in 20..<46 {
            for x in 6..<28 {
                let inPupil = abs(x - 12 - look % 3 * 3) + abs(y - 30) < 6
                picture[y * 64 + x] = inPupil ? 0 : 240
            }
        }
        return picture
    }

    private static func checkWindowLeftOut() throws {
        let patch = ScreenPatch(left: 6.0 / 64, top: 20.0 / 64, right: 28.0 / 64, bottom: 46.0 / 64, side: 64)
        try check(patch.left == 5 && patch.top == 19 && patch.right == 29 && patch.bottom == 47, "the patch takes one more cell")
        try check(ScreenPatch(left: 0, top: 0, right: 1, bottom: 1, side: 64).right == 64, "the patch stays on the picture")
        try check(ScreenPatch(left: 0.25, top: 0.25, right: 0.5, bottom: 0.5, side: 256).left == 60, "the edge is as wide on a larger picture")
        try check(ScreenStillness.difference(movingWindow(0), movingWindow(1)) >= ScreenStillness.threshold, "eyes do move the small picture")
        try check(ScreenStillness.difference(movingWindow(0), movingWindow(1), outside: patch) == 0, "the window is left out")
        func settles(outside patch: ScreenPatch?) -> Double {
            var stillness = ScreenStillness()
            var look = 0
            while !stillness.see(movingWindow(look), at: Double(look) * 0.5 + 0.03 * Double(look % 2), outside: patch) { look += 1 }
            return stillness.waited
        }
        try check(settles(outside: nil) >= ScreenStillness.longestWait, "with the window counted a hint waits for the limit")
        try check(settles(outside: patch) <= ScreenStillness.stillFor + 0.5, "with the window left out it waits as on a still screen")
        let page = movingWindow(0)
        var another = page
        for index in 0..<3000 { another[index] = 30 }
        try check(ScreenStillness.difference(page, another, outside: patch) > 0.1, "a new screen still counts")
        try check(ScreenStillness.changedShare(page, another, outside: patch) > 0.5, "and it counts as a new screen")
        var banner = page
        for index in 0..<(64 * 14) { banner[index] = 30 }
        try check(ScreenStillness.changedShare(page, banner, outside: patch) < 0.3, "a banner does not")
    }

    private static func checkSmallChange() throws {
        let side = 256
        let page = [UInt8](repeating: 200, count: side * side)
        var checked = page
        for y in 100..<104 { for x in 50..<53 { checked[y * side + x] = 40 } }
        try check(ScreenStillness.changedCells(page, checked) == 12 && 12 >= ScreenStillness.smallChange, "a check mark counts")
        let patch = ScreenPatch(left: 0.1, top: 0.3, right: 0.4, bottom: 0.5, side: side)
        try check(ScreenStillness.changedCells(page, checked, outside: patch) == 0, "our window does not")
        var clock = page
        for y in 5..<15 { for x in 20..<40 { clock[y * side + x] = 40 } }
        try check(ScreenStillness.changedCells(page, clock) == 0, "nor does the clock in the status bar")
        var stillness = ScreenStillness()
        _ = stillness.see(page, at: 0)
        _ = stillness.see(checked, at: 0.5)
        _ = stillness.see(checked, at: 1)
        try check(!stillness.isQuiet, "a small change is not trusted at once")
        _ = stillness.see(checked, at: 1.75)
        try check(stillness.isQuiet, "once it stays, it is real")
        var blinking = ScreenStillness()
        for look in 0..<12 {
            _ = blinking.see(look.isMultiple(of: 2) ? page : checked, at: Double(look) * 0.5)
            try check(!blinking.isQuiet, "a cursor or a countdown never gets quiet")
        }
    }

    private static func picture(width: Int, height: Int, window: (x: Int, y: Int, width: Int)?) -> [UInt8] {
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        for y in 0..<height {
            for x in 0..<width {
                let shade = UInt8(200 + (x / 7 + y / 11) % 40)
                pixels.replaceSubrange((y * width + x) * 4..<(y * width + x) * 4 + 4, with: [shade, shade, shade, 255])
            }
        }
        guard let window else { return pixels }
        let tall = window.width * 4 / 3
        let line = max(2, window.width * 35 / 1000)
        for y in window.y..<(window.y + tall) {
            for x in window.x..<(window.x + window.width) {
                let onBorder = x < window.x + line || x >= window.x + window.width - line || y < window.y + line || y >= window.y + tall - line
                let color: [UInt8] = onBorder ? [245, 12, 165, 255] : (x + y).isMultiple(of: 5) ? [255, 255, 255, 255] : [0, 0, 0, 255]
                pixels.replaceSubrange((y * width + x) * 4..<(y * width + x) * 4 + 4, with: color)
            }
        }
        return pixels
    }

    private static func checkWindowFinder() throws {
        let (width, height) = (185, 400)
        for (x, y, side) in [(10, 150, 74), (90, 40, 60), (20, 200, 90), (5, 10, 150)] {
            let patch = try require(WindowFinder.find(picture(width: width, height: height, window: (x, y, side)), width: width, height: height, side: 64),
                                    "the window at \(x), \(y), \(side) is found")
            let tall = side * 4 / 3
            try check(abs(patch.left - x * 64 / width) <= 2 && abs(patch.right - (x + side) * 64 / width) <= 2 &&
                      abs(patch.top - y * 64 / height) <= 2 && abs(patch.bottom - (y + tall) * 64 / height) <= 2, "the window's place")
        }
        try check(WindowFinder.find(picture(width: width, height: height, window: nil), width: width, height: height, side: 64) == nil,
                  "a page with no window")
        var banner = picture(width: width, height: height, window: nil)
        for y in 60..<110 {
            for x in 0..<width { banner.replaceSubrange((y * width + x) * 4..<(y * width + x) * 4 + 4, with: [245, 12, 165, 255]) }
        }
        try check(WindowFinder.find(banner, width: width, height: height, side: 64) == nil, "a pink banner is not a window")
        try check(WindowFinder.find([], width: width, height: height, side: 64) == nil, "no picture")
    }

    private static func require<Value>(_ value: Value?, _ message: String) throws -> Value {
        guard let value else { throw CheckFailure(description: message) }
        return value
    }

    /// Plays the eyes the way the timer does, by the moments they ask for. Returns each picture and when it was drawn.
    private static func play(_ director: inout EyesDirector, from start: Double, to end: Double) -> [(time: Double, pose: EyePose)] {
        var pictures: [(time: Double, pose: EyePose)] = []
        var time = start
        var shown: EyePose?
        while time < end {
            let pose = director.advance(to: time)
            if pose != shown { pictures.append((time, pose)) }
            shown = pose
            let next = director.nextChange
            guard next > time else { return pictures }
            time = next
        }
        return pictures
    }

    private static func sameRandom() -> () -> Double {
        var value = 0.31
        return {
            value = (value * 9301 + 0.49297).truncatingRemainder(dividingBy: 1)
            return value
        }
    }

    private static func checkEyes() throws {
        var rest = EyesDirector(random: sameRandom())
        let quiet = play(&rest, from: 0, to: 60)
        let gaps = zip(quiet, quiet.dropFirst()).map { $1.time - $0.time }.filter { $0 > 1 }.dropFirst()  // The first blink comes early.
        try check(gaps.count >= 3 && gaps.allSatisfy { $0 >= 4 }, "at rest nothing is drawn between rare blinks")
        try check(quiet.count < 120, "at rest there are only a few pictures")
        try check(quiet.allSatisfy { abs($0.pose.x) < 0.001 && abs($0.pose.y) < 0.001 && $0.pose.brow == 0 }, "at rest they look straight ahead")

        var short = EyesDirector(random: sameRandom())
        _ = short.advance(to: 1)
        short.beginWaiting(at: 1)
        let brief = play(&short, from: 1, to: 1.9)
        try check(short.mood == .rest && brief.allSatisfy { $0.pose.brow == 0 && $0.pose.x == 0 }, "a wait under a second changes nothing")
        short.endWaiting()

        var thinking = EyesDirector(random: sameRandom())
        _ = thinking.advance(to: 1)
        thinking.beginWaiting(at: 1)
        let thoughts = play(&thinking, from: 1, to: 3.9).filter { $0.time > 2.2 }
        try check(thinking.mood == .thinking && thoughts.contains { $0.pose.brow == 1 && $0.pose.squint > 0.3 && $0.pose.y < -0.5 }, "a longer wait looks up, with the brow")
        try check(Set(thoughts.map { $0.pose.x > 0 }).count == 2, "and to both sides")
        var moves = 0
        for (one, two) in zip(thoughts, thoughts.dropFirst()) where abs(one.pose.x - two.pose.x) > 0.01 && two.pose.left > 0.99 { moves += 1 }
        try check(moves < 12, "jumps are short: a side change takes a couple of pictures, not a slide")
        try check(!thinking.hasGivenUp(at: 9.9) && thinking.hasGivenUp(at: 10.1), "after nine seconds the eyes give way to words")
        try check(!thinking.hasWaitedOut(at: 35.9) && thinking.hasWaitedOut(at: 36.1), "no wait lasts past the longest answer")

        var reader = EyesDirector(random: sameRandom())
        _ = reader.advance(to: 1)
        reader.screenChanged(at: 1)
        let reading = play(&reader, from: 1, to: 3.5)
        try check(reader.mood == .reading, "a new screen is read")
        let line = reading.filter { $0.time > 1.1 && $0.time < 1.6 && $0.pose.left > 0.99 }.map(\.pose.x)
        try check(line.count >= 3 && zip(line, line.dropFirst()).allSatisfy { $1 >= $0 - 0.001 }, "left to right along a line")
        let rows = reading.map { ($0.pose.y * 10).rounded() / 10 }.reduce(into: [Double]()) { if $0.last != $1 { $0.append($1) } }
        try check(rows.filter { [-0.6, 0, 0.6].contains($0) }.count >= 3 && (rows.first { $0 != 0 } ?? 0) < 0, "line after line, from the top")

        var listener = EyesDirector(random: sameRandom())
        _ = listener.advance(to: 1)
        listener.hear(level: 1, at: 1)
        var pupil = 0.0
        for step in 1...3 { pupil = listener.advance(to: 1 + Double(step) * 0.1).pupil }
        try check(listener.mood == .listening && pupil > 1.4, "the pupil grows with the voice")
        try check(listener.advance(to: 1.6).x == 0 && listener.mood == .rest, "and silence ends the listening")
        try check(EyesDirector.level(of: []) == 0 && EyesDirector.level(of: [0, 0, 0]) == 0, "a deaf stream shows no level")
        try check(EyesDirector.level(of: [Int16](repeating: 12000, count: 1600)) > 0.9, "a loud voice fills the level")

        var pointer = EyesDirector(random: sameRandom())
        _ = pointer.advance(to: 1)
        pointer.point(x: 0.8, y: -0.6, at: 1)
        _ = pointer.advance(to: 1)
        let flare = pointer.advance(to: 1.2)
        try check(pointer.mood == .pointing && flare.x > 0.79 && flare.y < -0.59 && flare.left >= 1 && flare.pupil > 1.2, "a hint: wide open, towards the button")
        try check(pointer.advance(to: 1.9).pupil == 1 && pointer.mood == .pointing, "only for a moment")
        var look = pointer.advance(to: 2.6)
        look = pointer.advance(to: 2.7)
        try check(pointer.mood == .rest && look.x == 0, "then back to rest")

        var calm = EyesDirector(random: sameRandom())
        calm.isCalm = true
        calm.beginWaiting(at: 0)
        calm.screenChanged(at: 0)
        calm.hear(level: 1, at: 0)
        calm.rejoice(at: 0)
        try check(play(&calm, from: 0, to: 12).allSatisfy { $0.pose.x == 0 && $0.pose.brow == 0 && !$0.pose.isHappy }, "payment: nothing but rest")

        var happy = EyesDirector(random: sameRandom())
        _ = happy.advance(to: 1)
        happy.rejoice(at: 1)
        try check(happy.advance(to: 1.5).isHappy && !happy.advance(to: 2.1).isHappy, "arcs for a second")

        var moved = EyesDirector(random: sameRandom())
        _ = moved.advance(to: 1)
        moved.rejoice(at: 1)
        moved.express(.found, at: 1)
        moved.screenChanged(at: 1.2)
        let carried = moved.advance(to: 1.3)
        try check(moved.mood == .reading && !carried.isHappy, "a face stays on its own screen")

        var found = EyesDirector(random: sameRandom())
        _ = found.advance(to: 1)
        found.isSpeaking = true
        found.express(.found, at: 1)
        var flash = found.advance(to: 1)
        flash = found.advance(to: 1.2)
        try check(found.mood == .found && flash.left > 1.1 && flash.pupil > 1.3 && flash.x == 0, "found: wide open with a big pupil")
        try check(found.advance(to: 2).left == 1 && found.mood == .found, "only for a moment, and the face holds a little longer")
        _ = found.advance(to: 2.7)
        try check(found.mood == .speaking, "then the eyes go back to speaking")

        var doubt = EyesDirector(random: sameRandom())
        _ = doubt.advance(to: 1)
        doubt.express(.doubting, at: 1)
        let doubts = play(&doubt, from: 1, to: 2.5).filter { $0.time > 1.07 }.map(\.pose)
        try check(doubts.contains { $0.left < $0.right * 0.7 && $0.brow == 1 && $0.browLift == 1 }, "doubt: one eye narrows and the brow goes up")
        try check(Set(doubts.map { $0.x > 0 }).count == 2, "and it looks to both sides")
        try check(doubt.advance(to: 3).browLift < 1, "the doubt passes")

        var stopped = EyesDirector(random: sameRandom())
        _ = stopped.advance(to: 1)
        stopped.express(.stopped, at: 1)
        let stare = play(&stopped, from: 1, to: 2.5).filter { $0.time > 1.3 }.map(\.pose)
        try check(stare.allSatisfy { $0.left > 1.1 && $0.pupil < 0.7 && $0.x == 0 }, "stop: a wide still stare with no blink")

        var thinks = EyesDirector(random: sameRandom())
        _ = thinks.advance(to: 1)
        thinks.express(.thinking, at: 1)
        _ = thinks.advance(to: 1.2)
        try check(thinks.mood == .thinking, "thinking is the same face as a long wait")

        var glad = EyesDirector(random: sameRandom())
        _ = glad.advance(to: 1)
        glad.express(.happy, at: 1)
        try check(glad.advance(to: 1.5).isHappy, "glad: arcs")

        var calmFace = EyesDirector(random: sameRandom())
        _ = calmFace.advance(to: 1)
        calmFace.isCalm = true
        for face in EyesFace.allCases { calmFace.express(face, at: 1) }
        try check(play(&calmFace, from: 1, to: 3).allSatisfy { $0.pose.brow == 0 && !$0.pose.isHappy && $0.pose.pupil == 1 }, "payment: no face at all")

        var hearing = EyesDirector(random: sameRandom())
        _ = hearing.advance(to: 1)
        hearing.express(.stopped, at: 1)
        hearing.hear(level: 1, at: 1.1)
        _ = hearing.advance(to: 1.2)
        try check(hearing.mood == .listening, "the person's voice comes before the face")

        var jokes = EyesDirector(random: sameRandom())
        var seen: [Int] = []
        var time = 1.0
        for pause in 0..<40 {
            jokes.beginWaiting(at: time)
            let pictures = play(&jokes, from: time, to: time + 5.5)
            let joked = pictures.contains { abs($0.pose.cross - 1) < 0.01 || $0.pose.right < $0.pose.left * 0.6 || hypot($0.pose.x, $0.pose.y) > 0.84 && $0.pose.y > 0.3 }
            if joked { seen.append(pause) }
            jokes.endWaiting()
            time += 6
            _ = jokes.advance(to: time)
        }
        try check(seen.count >= 7 && seen.count <= 10, "a joke once in four or five pauses: \(seen.count) of 40")
        try check(zip(seen, seen.dropFirst()).allSatisfy { $1 - $0 >= 4 }, "never two pauses running")
        var unknown = EyesDirector(random: sameRandom())
        unknown.isOnNewScreen = true
        var quietTime = 1.0
        for _ in 0..<10 {
            unknown.beginWaiting(at: quietTime)
            let pictures = play(&unknown, from: quietTime, to: quietTime + 5.5)
            try check(!pictures.contains { abs($0.pose.cross - 1) < 0.01 || $0.pose.right < $0.pose.left * 0.6 || hypot($0.pose.x, $0.pose.y) > 0.84 && $0.pose.y > 0.3 }, "no joke over a screen nobody has named")
            unknown.endWaiting()
            quietTime += 6
            _ = unknown.advance(to: quietTime)
        }

        var painted = [UInt8](repeating: 0, count: 720 * 960 * 4)
        let inked: (EyePose) throws -> Int = { pose in
            painted = [UInt8](repeating: 0, count: 720 * 960 * 4)
            try painted.withUnsafeMutableBytes { bytes in
                guard let context = CGContext(data: bytes.baseAddress, width: 720, height: 960, bitsPerComponent: 8, bytesPerRow: 2880,
                                              space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
                    throw CheckFailure(description: "eyes context")
                }
                context.translateBy(x: 0, y: 960)
                context.scaleBy(x: 1, y: -1)
                EyesPainter.draw(pose, in: CGRect(x: 108, y: 228, width: 504, height: 504), on: context)
            }
            return painted.lazy.filter { $0 > 128 }.count
        }
        let open = try inked(EyePose())
        try check(open > 100_000, "the eyes are drawn")
        let shut = try inked(EyePose(left: 0.05, right: 0.05))
        try check(shut * 4 < open, "a blink shuts them")
        _ = try inked(EyePose(x: 1))
        let onRight = painted[(480 * 720 + 300) * 4] < 128 && painted[(480 * 720 + 150) * 4] > 128
        try check(onRight, "looking right leaves the left of the eye white")
        let plainBrow = try inked(EyePose(brow: 1))
        try check(plainBrow > open, "the brow is drawn")
        _ = try inked(EyePose(brow: 1, browLift: 1))
        let raised = (0..<720).contains { painted[(250 * 720 + $0) * 4] > 128 }
        _ = try inked(EyePose(brow: 1))
        try check(raised && !(0..<720).contains { painted[(250 * 720 + $0) * 4] > 128 }, "the brow goes up in doubt")
    }

    private static func checkShelf() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("shelf-check-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let shelf = FrameShelf(directory: directory)
        try check(shelf.recent().isEmpty, "an empty shelf")
        for number in [9, 10, 11, 100, 101, 102] { try shelf.put(Data([UInt8(number)]), timestampMS: Int64(number)) }
        try check(shelf.recent() == [11, 100, 101, 102].map { KeptScreen(jpeg: Data([UInt8($0)]), timestampMS: Int64($0)) },
                  "the newest four, oldest first")
        try shelf.put(Data([103]), timestampMS: 103, sameScreen: true)
        try check(shelf.recent().map(\.timestampMS) == [11, 100, 101, 103], "a new look at the same screen keeps the page before it")
        try shelf.put(Data([104]), timestampMS: 103, sameScreen: true)
        try check(shelf.recent().last == KeptScreen(jpeg: Data([104]), timestampMS: 103), "the same moment twice is one file")
        shelf.clear()
        shelf.clear()
        try check(shelf.recent().isEmpty, "clearing leaves nothing, and twice is harmless")
    }

    private static func checkPhraseListener() async throws {
        func buffers(_ samples: [Float], rate: Double) -> [AVAudioPCMBuffer] {
            let format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1)!
            let size = Int(rate / 10)
            return stride(from: 0, to: samples.count, by: size).map { start in
                let part = samples[start..<min(start + size, samples.count)]
                let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(part.count))!
                buffer.frameLength = AVAudioFrameCount(part.count)
                part.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: part.count) }
                return buffer
            }
        }
        func speech(rate: Double) -> [AVAudioPCMBuffer] {
            buffers(sound(seconds: 1, level: -60, hertz: 0, rate: rate) + sound(seconds: 1.5, level: -25, rate: rate)
                    + sound(seconds: 1.2, level: -60, hertz: 0, rate: rate), rate: rate)
        }
        for rate in [48_000.0, 44_100, 24_000, 16_000] {
            let listener = PhraseListener()
            speech(rate: rate).forEach(listener.hear)
            var heard = listener.phrases.makeAsyncIterator()
            guard let phrase = await heard.next() else { throw CheckFailure(description: "no phrase at \(rate) Hz") }
            try check(abs(phrase.seconds - 2.2) < 0.3, "phrase length at \(rate) Hz, got \(phrase.seconds)")
            try check(phrase.loudest > -29 && phrase.loudest < -21, "level survives \(rate) Hz, got \(phrase.loudest)")
        }
        let listener = PhraseListener()
        listener.deafen(until: .distantFuture)
        speech(rate: 48_000).forEach(listener.hear)
        listener.deafen(until: .distantPast)
        let late = buffers(sound(seconds: 1, level: -60, hertz: 0, rate: 48_000) + sound(seconds: 1, level: -25, rate: 48_000), rate: 48_000)
        late.forEach(listener.hear)
        listener.deafen(until: .distantFuture)
        buffers(sound(seconds: 0.1, level: -25, rate: 48_000), rate: 48_000).forEach(listener.hear)
        var heard = listener.phrases.makeAsyncIterator()
        guard let cut = await heard.next() else { throw CheckFailure(description: "the agent's voice cut a phrase and lost it") }
        try check(cut.seconds < 2, "only the person's words get through, got \(cut.seconds)")
    }

    private static func checkPauseWords() throws {
        var words = PauseWords()
        let start = Date()
        var said: [String] = []
        for step in 0..<18 {
            guard let word = words.next(now: start.addingTimeInterval(Double(step) * (PauseWords.gap + 1))) else {
                throw CheckFailure(description: "a word after a long pause")
            }
            try check(word != said.last, "the same pause word twice in a row: \(word)")
            said.append(word)
        }
        try check(Set(said).count == PauseWords.all.count, "every pause word gets its turn")
        try check(words.next(now: start.addingTimeInterval(17 * (PauseWords.gap + 1) + 5)) == nil, "pause words stay rare")
    }

    private static func checkScreenTextGeometry() throws {
        try check(ScreenTextReader.recognitionLanguages(targetLang: "id") == ["en-US"], "Latin recognition language")
        try check(ScreenTextReader.recognitionLanguages(targetLang: "auto") == ["en-US"], "auto recognition language")
        try check(ScreenTextReader.recognitionLanguages(targetLang: "zh") == ["zh-Hans", "en-US"], "Chinese recognition languages")
        try check(ScreenTextReader.recognitionLanguages(targetLang: "zh-Hant") == ["zh-Hant", "en-US"], "Traditional Chinese recognition languages")
        try check(ScreenTextReader.recognitionLanguages(targetLang: "th") == ["th-TH", "en-US"], "Thai recognition languages")
        let box = ScreenTextReader.topLeftBox(CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0.4))
        try check(zip(box, [0.1, 0.4, 0.4, 0.8]).allSatisfy { abs($0 - $1) < 0.00001 }, "top-left normalized coordinates")
        let clipped = ScreenTextReader.topLeftBox(CGRect(x: -0.1, y: -0.1, width: 1.2, height: 1.2))
        try check(clipped == [0, 0, 1, 1], "normalized box bounds")
        let lineBox = CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0.4)
        let fallback = ScreenTextReader.word("Beli", box: nil, lineBox: lineBox)
        try check(fallback.text == "Beli", "keep word when its box is missing")
        try check(zip(fallback.box, [0.1, 0.4, 0.4, 0.8]).allSatisfy { abs($0 - $1) < 0.00001 }, "line box for missing word box")
        let precise = ScreenTextReader.word("Beli", box: CGRect(x: 0.2, y: 0.3, width: 0.1, height: 0.2), lineBox: lineBox)
        try check(zip(precise.box, [0.2, 0.5, 0.3, 0.7]).allSatisfy { abs($0 - $1) < 0.00001 }, "prefer available word box")
    }

    private static func checkSettingsAndMailbox() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("screen-text-check-\(UUID().uuidString)")
        let bundleURL = directory.appendingPathComponent("Fixture.bundle")
        try FileManager.default.createDirectory(at: bundleURL, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        var info: [String: String] = ["CFBundleDisplayName": "Fixture", "CFBundleIdentifier": "test.screen-text",
                                    "AppGroupIdentifier": "group.test.screen-text", "DefaultServerURL": "https://example.invalid",
                                    "DefaultServerToken": "fixture-token"]
        let infoURL = bundleURL.appendingPathComponent("Info.plist")
        try PropertyListEncoder().encode(info).write(to: infoURL)
        guard let bundle = Bundle(url: bundleURL) else { throw CheckFailure(description: "fixture bundle") }
        let configuration = try ProjectConfiguration(bundle: bundle)
        try check(configuration.defaultServerToken == "fixture-token", "token from Info.plist")
        let defaults = MemoryDefaults()
        let store = try SharedStore(configuration: configuration, defaults: defaults)
        try check(store.screenTextMode == .extension, "default screenTextMode")
        try check(store.voice == nil, "the server's voice by default")
        try check(!store.eyes && !store.settings.eyes, "the eyes are off by default")
        store.voice = "voice-1"
        try check(defaults.string(forKey: "voice") == "voice-1", "a chosen voice stays")
        try check(store.settings.thinking == .auto && store.settings.character == .warm, "default level and manner")
        try store.save(serverAddress: "https://example.invalid", token: "", userLang: "ru", targetLang: "id", textEntry: .clipboard,
                       thinking: .medium, character: .brief, ownCharacter: "")
        try check(store.settings.thinking == .medium && store.settings.character == .brief, "saved level and manner")
        store.screenTextMode = .extension
        try check(defaults.string(forKey: "screenReading") == "extension", "persisted screenTextMode")
        store.screenTextMode = .extensionFast
        let reloaded = try SharedStore(configuration: configuration, defaults: defaults)
        try check(defaults.string(forKey: "screenReading") == "extensionFast" && reloaded.screenTextMode == .extensionFast,
                  "restore extensionFast from App Group")
        store.seedDefaultToken(configuration.defaultServerToken)
        try check(store.settings.token == "fixture-token", "seed missing token")
        defaults.set("owner-token", forKey: "token")
        store.seedDefaultToken(configuration.defaultServerToken)
        try check(store.settings.token == "owner-token", "keep existing token")
        defaults.set(nil, forKey: "token")
        store.seedDefaultToken("")
        try check(store.settings.token.isEmpty, "missing local token")
        for (name, value) in [("Missing", ""), ("Unexpanded", "$(DEFAULT_SERVER_TOKEN)")] {
            let otherURL = directory.appendingPathComponent(name + ".bundle")
            try FileManager.default.createDirectory(at: otherURL, withIntermediateDirectories: true)
            info["CFBundleIdentifier"] = "test.screen-text." + name
            info["DefaultServerToken"] = value.isEmpty ? nil : value
            try PropertyListEncoder().encode(info).write(to: otherURL.appendingPathComponent("Info.plist"))
            let config = try ProjectConfiguration(bundle: Bundle(url: otherURL)!)
            try check(config.defaultServerToken.isEmpty, "optional token setting")
        }
        let mailbox = ScreenTextMailbox(directory: directory, notificationName: "test.screen-text")
        try mailbox.clear()
        let missing = try mailbox.latest()
        try check(missing == nil, "missing shared frame")
        for timestamp in [Int64(42), 43] {
            let frame = ScreenTextFrame(sessionID: "session-\(timestamp)", frameTS: timestamp, jpeg: Data([UInt8(timestamp)]))
            try mailbox.publish(frame, notify: false)
            let read = try mailbox.latest()
            try check(read == frame, "atomic JPEG/session/timestamp round trip")
        }
        try mailbox.clear()
        try mailbox.clear()
        let cleared = try mailbox.latest()
        try check(cleared == nil, "clear removes JPEG and metadata; repeated clear is harmless")
    }

    private static func syntheticJPEG() throws -> Data {
        guard let context = CGContext(data: nil, width: 900, height: 300, bitsPerComponent: 8, bytesPerRow: 3600,
                                      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            throw CheckFailure(description: "synthetic image context")
        }
        context.setFillColor(CGColor(gray: 1, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: 900, height: 300))
        let string = NSAttributedString(string: "Beli sekarang", attributes: [
            NSAttributedString.Key(kCTFontAttributeName as String): CTFontCreateWithName("Helvetica" as CFString, 72, nil),
            NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(gray: 0, alpha: 1)
        ])
        context.textPosition = CGPoint(x: 60, y: 120)
        CTLineDraw(CTLineCreateWithAttributedString(string), context)
        let image = context.makeImage()!
        let data = NSMutableData()
        let destination = CGImageDestinationCreateWithData(data, "public.jpeg" as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, image, nil)
        try check(CGImageDestinationFinalize(destination), "synthetic JPEG")
        return data as Data
    }

    private static func checkMemoryGate() async throws {
        let jpeg = try syntheticJPEG()
        let reading = await ScreenTextWorker().read(jpegData: jpeg, targetLang: "id", canRead: { false })
        try check(reading.skipped && reading.lines.isEmpty && reading.error == nil && !reading.retriedOnCPU,
                  "memory gate skips Vision and CPU retry")
    }

    private static func checkVision() async throws {
        let jpeg = try syntheticJPEG()
        var failures: [String] = []
        let variants: [(String, ScreenTextLevel, Bool)] = [
            ("accurate", .accurate, false), ("fast", .fast, false), ("accurate, CPU", .accurate, true)
        ]
        for (label, level, cpuOnly) in variants {
            let reading = await ScreenTextWorker().read(jpegData: jpeg, targetLang: "id", level: level, cpuOnly: cpuOnly,
                                                        onDiagnostic: { print($0) })
            print("Vision synthetic check (\(label)): \(reading.diagnostic)")
            if let error = reading.error {
                failures.append("\(label): \(error)")
                continue
            }
            do {
                let words = reading.lines.flatMap(\.words)
                try check(words.map { $0.text.lowercased() } == ["beli", "sekarang"], "Vision synthetic word recognition (\(label))")
                try check(words.allSatisfy { $0.box.count == 4 && $0.box.allSatisfy { (0...1).contains($0) }
                          && $0.box[0] < $0.box[2] && $0.box[1] < $0.box[3] }, "Vision word box validity (\(label))")
                try check(reading.level == level && (!cpuOnly || reading.cpuOnly), "Vision level/device (\(label))")
            } catch { failures.append(String(describing: error)) }
        }
        try check(failures.isEmpty, "Vision checks failed: " + failures.joined(separator: "; "))
        print("Vision synthetic checks passed: accurate, fast and forced CPU on the same JPEG.")
    }

    private static func body(of request: URLRequest) throws -> Data {
        if let body = request.httpBody { return body }
        guard let stream = request.httpBodyStream else { throw CheckFailure(description: "missing body") }
        stream.open()
        defer { stream.close() }
        var result = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            guard count >= 0 else { throw CheckFailure(description: "body stream error") }
            if count == 0 { break }
            result.append(contentsOf: buffer.prefix(count))
        }
        return result
    }
}
