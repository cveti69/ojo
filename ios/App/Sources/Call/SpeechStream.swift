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

    init(client: ServerClient, sessionID: String) {
        self.client = client
        self.sessionID = sessionID
    }

    func start() {
        guard keeper == nil else { return }
        keeper = Task { [weak self] in
            var delay: UInt64 = 1
            while let self, !Task.isCancelled {
                do {
                    let socket = try self.client.listenSocket(sessionID: self.sessionID)
                    self.socket = socket
                    socket.resume()
                    self.startPings(socket)
                    _ = try await socket.receive()  // The server only listens, so this ends when the socket does.
                } catch {
                    if Task.isCancelled { return }
                    if self.isOpen { delay = 1 }
                    self.onDiagnostic?("Поток речи закрылся: \(error.localizedDescription)")
                }
                self.isOpen = false
                self.pings?.cancel()
                self.socket?.cancel(with: .goingAway, reason: nil)
                self.socket = nil
                do { try await Task.sleep(nanoseconds: delay * 1_000_000_000) } catch { return }
                delay = min(delay * 2, 10)
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

    @discardableResult
    func send(_ samples: [Int16]) -> Bool {
        guard isOpen, let socket else { return false }
        let data = samples.withUnsafeBytes { Data($0) }  // Little-endian, as the server expects.
        let log = onDiagnostic
        Task {
            do { try await socket.send(.data(data)) }
            catch { log?("Поток речи: звук не ушёл: \(error.localizedDescription)") }
        }
        return true
    }

    private func startPings(_ socket: URLSessionWebSocketTask) {
        pings?.cancel()
        pings = Task { [weak self] in
            var answered = true
            while !Task.isCancelled {
                guard let self else { return }
                if !answered {  // The socket went quiet: the pending receive now fails and the keeper opens a new one.
                    socket.cancel(with: .goingAway, reason: nil)
                    return
                }
                answered = false
                socket.sendPing { [weak self] error in
                    Task { @MainActor [weak self] in
                        guard let self, self.socket === socket, error == nil else { return }
                        answered = true
                        if !self.isOpen {
                            self.isOpen = true
                            self.onDiagnostic?("Поток речи открыт")
                        }
                    }
                }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
    }
}
