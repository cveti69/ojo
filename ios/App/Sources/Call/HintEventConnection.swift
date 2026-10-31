import Foundation
import Shared

@MainActor
final class HintEventConnection {
    var onEvent: ((ServerEvent) -> Void)?
    var onDiagnostic: ((String) -> Void)?
    var onSessionExpired: (() -> Void)?
    var onReachable: ((Bool) -> Void)?
    private let client: ServerClient
    private var socket: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var pingTask: Task<Void, Never>?
    private var socketAvailable = false
    private var pongPending = false
    private var lastStepID: String?

    init(client: ServerClient) { self.client = client }

    func start(sessionID: String) {
        stop()
        lastStepID = nil
        receiveTask = Task { [weak self] in
            guard let self else { return }
            var delay: UInt64 = 1
            while !Task.isCancelled {
                do {
                    let socket = try self.client.eventSocket(sessionID: sessionID)
                    self.socket = socket
                    socket.resume()
                    // A pong only comes back while a receive is pending, so the pings run next to this loop, not before it.
                    self.startPings(socket)
                    while !Task.isCancelled {
                        let message = try await socket.receive()
                        try Task.checkCancellation()
                        let data: Data
                        switch message {
                        case .data(let value): data = value
                        case .string(let value): data = Data(value.utf8)
                        @unknown default: continue
                        }
                        do { self.deliver(try JSONDecoder().decode(ServerEvent.self, from: data)) }
                        catch { self.onDiagnostic?("Неизвестное событие сервера: \(error.localizedDescription)") }
                    }
                } catch {
                    if Task.isCancelled { break }
                    self.onDiagnostic?("WebSocket недоступен: \(error.localizedDescription); опрос /hint раз в секунду")
                }
                if self.socketAvailable { delay = 1 }
                self.socketAvailable = false
                self.pingTask?.cancel()
                self.socket?.cancel(with: .goingAway, reason: nil)
                self.socket = nil
                do { try await Task.sleep(nanoseconds: delay * 1_000_000_000) } catch { break }
                delay = min(delay * 2, 10)
            }
        }
        pollTask = Task { [weak self] in
            guard let self else { return }
            var previousError: String?
            while !Task.isCancelled {
                if !self.socketAvailable {
                    do {
                        let event = try await self.client.hint(sessionID: sessionID, after: self.lastStepID)
                        try Task.checkCancellation()
                        if let event { self.deliver(event) }
                        previousError = nil
                        self.onReachable?(true)
                    } catch {
                        if Task.isCancelled { break }
                        if error as? ClientError == .http(404) || error as? ClientError == .http(401) {
                            self.onSessionExpired?()
                            break
                        }
                        self.onReachable?(false)
                        if error.localizedDescription != previousError {
                            previousError = error.localizedDescription
                            self.onDiagnostic?("Ошибка /hint: \(error.localizedDescription)")
                        }
                    }
                }
                do { try await Task.sleep(nanoseconds: 1_000_000_000) } catch { break }
            }
        }
    }

    func stop() {
        receiveTask?.cancel()
        pollTask?.cancel()
        pingTask?.cancel()
        socket?.cancel(with: .goingAway, reason: nil)
        receiveTask = nil
        pollTask = nil
        pingTask = nil
        socket = nil
        socketAvailable = false
    }

    private func deliver(_ event: ServerEvent) {
        if case .hint(let hint) = event { lastStepID = hint.stepID }
        onEvent?(event)
    }

    private func startPings(_ socket: URLSessionWebSocketTask) {
        pingTask?.cancel()
        pongPending = false
        pingTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if self.pongPending {  // The socket went quiet: receive() now fails and the poll takes over.
                    socket.cancel(with: .goingAway, reason: nil)
                    return
                }
                self.pongPending = true
                socket.sendPing { [weak self] error in
                    Task { @MainActor [weak self] in
                        guard let self, self.socket === socket, error == nil else { return }
                        self.pongPending = false
                        if !self.socketAvailable {
                            self.socketAvailable = true
                            self.onDiagnostic?("WebSocket подключён")
                            self.onReachable?(true)
                        }
                    }
                }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
    }
}
