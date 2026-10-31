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
        if path.hasSuffix("/goal") { return (200, #"{"reply_ru":"Включите трансляцию","ready_text":"sampo"}"#) }
        if path.hasSuffix("/frames") { return (202, #"{"accepted":false,"reason":"busy"}"#) }
        if path.hasSuffix("/speech") { return (200, "abcd") }
        if path.hasSuffix("/say") {
            return (200, #"{"reply_ru":"Там написано Войти","goal":"Купи кондиционер","timings":{"speech_ms":1200,"model_ms":2100}}"#)
        }
        if path.hasSuffix("/suggestions") { return (200, #"{"step_id":"s3","texts":["hello"],"hint_ru":"запрос"}"#) }
        if path.hasSuffix("/transcribe") || path.hasSuffix("/translate") { return (200, #"{"text":"Результат"}"#) }
        if path.hasSuffix("/hint") {
            return (200, #"{"type":"hint","step_id":"s3","instruction_ru":"Вставьте текст","speak":false,"image_jpeg_base64":"AA==","insert_texts":[],"insert_hint_ru":"","paste_text":"hello","needs_user_confirm":true,"frame_ts":123456789}"#)
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
        let wavURL = FileManager.default.temporaryDirectory.appendingPathComponent("protocol-\(UUID().uuidString).wav")
        try Data("RIFFtestWAVE".utf8).write(to: wavURL)
        defer { try? FileManager.default.removeItem(at: wavURL) }
        let transcript = try await client.transcribe(wavURL: wavURL)
        try check(transcript.text == "Результат", "transcribe response")
        let created = try await client.createSession()
        try check(created.sessionID == "session-test", "session_id decoding")
        let reply = try await client.setGoal("Цель", sessionID: created.sessionID)
        try check(reply.replyRU == "Включите трансляцию" && reply.readyText == "sampo", "goal response decoding")
        var spoken = 0
        for try await _ in try await client.speech("Привет") { spoken += 1 }
        try check(spoken == 4, "speech bytes")
        let said = try await client.say(Data("RIFFtestWAVE".utf8), sessionID: created.sessionID)
        try check(said.replyRU == "Там написано Войти" && said.goal == "Купи кондиционер" && said.timings.modelMS == 2100,
                  "spoken line response decoding")
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
        try check(value.pasteText == "hello" && value.needsUserConfirm && !value.speak, "confirmation and clipboard flags")
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

        let requests = ProtocolStub.state.captured()
        try check(requests.allSatisfy { $0.value(forHTTPHeaderField: "X-Token") == "test-token" }, "X-Token on every HTTP request")
        let audio = requests.first { $0.url!.path.hasSuffix("/transcribe") }!
        try check(audio.httpMethod == "POST" && audio.value(forHTTPHeaderField: "Content-Type") == "audio/wav", "audio upload format")
        let line = requests.first { $0.url!.path.hasSuffix("/say") }!
        try check(line.url!.path == "/api/sessions/session-test/say" && line.value(forHTTPHeaderField: "Content-Type") == "audio/wav",
                  "spoken line endpoint")
        let create = requests.first { $0.url!.path == "/api/sessions" }!
        let createBody = try JSONSerialization.jsonObject(with: body(of: create)) as! [String: String]
        try check(createBody == ["goal": "", "user_lang": "ru", "target_lang": "auto", "text_entry": "clipboard",
                                 "effort": "minimal", "character": "strict", "own_character": ""], "create-session body")
        let translate = requests.first { $0.url!.path.hasSuffix("/translate") }!
        let translateBody = try JSONSerialization.jsonObject(with: body(of: translate)) as! [String: String]
        try check(translateBody["from"] == "ru" && translateBody["to"] == "auto" && translateBody["session_id"] == created.sessionID,
                  "translation languages and session")
        let upload = requests.first { $0.url!.path.hasSuffix("/frames") }!
        try check(upload.httpMethod == "POST" && upload.value(forHTTPHeaderField: "Content-Type") == "image/jpeg", "frame format")
        try check(upload.value(forHTTPHeaderField: "X-Frame-Width") == "1170"
                  && upload.value(forHTTPHeaderField: "X-Frame-Height") == "2532"
                  && upload.value(forHTTPHeaderField: "X-Frame-Ts") == "123456789", "frame metadata")
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
        let status = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"status","state":"analyzing","text_ru":"Агент думает"}"#.utf8))
        guard case .status(let message) = status else { throw CheckFailure(description: "status event") }
        try check(message.state == "analyzing", "status state")
        let done = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"done","text_ru":"Готово"}"#.utf8))
        guard case .done = done else { throw CheckFailure(description: "done event") }
        try checkSpeechDetector()
        try await checkPhraseListener()
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
        let file = one[0].wav
        try check(file.count == 44 + one[0].samples.count * 2 && file.prefix(4) == Data("RIFF".utf8), "WAV size")
        let header = file.withUnsafeBytes { (rate: $0.loadUnaligned(fromByteOffset: 24, as: UInt32.self),
                                             size: $0.loadUnaligned(fromByteOffset: 40, as: UInt32.self)) }
        try check(header.rate == 16_000 && Int(header.size) == one[0].samples.count * 2, "WAV header")
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
        for step in 0..<40 {
            let now = start.addingTimeInterval(Double(step) * 16)
            guard let word = words.next(step % 3 == 0 ? .looking : .thinking, now: now) else {
                throw CheckFailure(description: "a word after a long pause")
            }
            try check(word != said.last, "the same pause word twice in a row: \(word)")
            said.append(word)
        }
        try check(Set(said).count == PauseWords.all.count, "every pause word gets its turn")
        try check(words.next(.thinking, now: start.addingTimeInterval(39 * 16 + 5)) == nil, "pause words stay rare")
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
