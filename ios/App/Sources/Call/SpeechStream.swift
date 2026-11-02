import Foundation
import Shared

/// Sends the person's voice to the server while it sounds. The socket stays open for the whole call, so no word pays for
/// opening it. What goes missing while it reopens, the phrase sent as a file makes up for.
@MainActor
final class SpeechStream {
    var onDiagnostic: ((String) -> Void)?
    private(set) var isOpen = false
    private let client: ServerClient
    private let sessionID: String
    private var socket: URLSessionWebSocketTask?
    private var keeper: Task<Void, Never>?
    private var pings: Task<Void, Never>?
    private var delay: UInt64 = 1

    init(client: ServerClient, sessionID: String) {
        self.client = client
        self.sessionID = sessionID
    }

    func start() {
        guard keeper == nil else { return }
        delay = 1
        keeper = Task { [weak self] in
            while let self, !Task.isCancelled {
                do {
                    let socket = try self.client.listenSocket(sessionID: self.sessionID)
                    self.socket = socket
                    socket.resume()
                    self.startPings(socket)
                    _ = try await socket.receive()  // The server only listens, so this ends when the socket does.
                } catch {
                    if Task.isCancelled { return }
                    if self.socket != nil { self.onDiagnostic?("Поток речи закрылся: \(error.localizedDescription)") }
                }
                if Task.isCancelled { return }
                self.isOpen = false
                self.pings?.cancel()
                self.socket?.cancel(with: .goingAway, reason: nil)
                self.socket = nil
                let wait = self.delay
                self.delay = min(wait * 2, 10)
                do { try await Task.sleep(nanoseconds: wait * 1_000_000_000) } catch { return }
            }
        }
    }

    func stop() {
        keeper?.cancel()
        keeper = nil
        pings?.cancel()
        pings = nil
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
        isOpen = false
    }

    /// A new socket at once: the old one lives on a way the phone has left.
    func reopen() {
        guard keeper != nil else { return }
        stop()
        start()
    }

    @discardableResult
    func send(_ samples: [Int16]) -> Bool {
        guard isOpen, let socket else { return false }
        let data = samples.withUnsafeBytes { Data($0) }  // Little-endian, as the server expects.
        Task { [weak self] in
            do { try await socket.send(.data(data)) } catch {
                self?.lost(socket, "звук не ушёл: \(error.localizedDescription)")
            }
        }
        return true
    }

    /// On a dead path a receive can wait for half a minute, so a failed send or ping ends the socket here.
    /// The next one comes after the keeper's wait: one that opens and fails again cannot spin.
    private func lost(_ socket: URLSessionWebSocketTask, _ reason: String) {
        guard self.socket === socket else { return }  // The sounds after the first fail too: one break, one line.
        if isOpen { onDiagnostic?("Поток речи оборвался: \(reason)") }
        isOpen = false
        self.socket = nil
        socket.cancel(with: .goingAway, reason: nil)
    }

    private func startPings(_ socket: URLSessionWebSocketTask) {
        pings?.cancel()
        pings = Task { [weak self] in
            var answered = true
            while !Task.isCancelled {
                guard let self else { return }
                if !answered {
                    self.lost(socket, "нет ответа на пинг")
                    return
                }
                answered = false
                socket.sendPing { [weak self] error in
                    Task { @MainActor [weak self] in
                        guard let self, self.socket === socket else { return }
                        if let error {
                            if self.isOpen { self.lost(socket, "пинг не прошёл: \(error.localizedDescription)") }  // One that never opened ends through its receive, which says why.
                            return
                        }
                        answered = true
                        if !self.isOpen {
                            self.isOpen = true
                            self.delay = 1
                            self.onDiagnostic?("Поток речи открыт")
                        }
                    }
                }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
    }
}
