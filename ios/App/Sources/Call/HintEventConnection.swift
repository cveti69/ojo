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
    private var sessionID: String?
    private var delay: UInt64 = 1

    init(client: ServerClient) { self.client = client }

    func start(sessionID: String) {
        stop()
        lastStepID = nil
        self.sessionID = sessionID
        open(sessionID: sessionID)
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
        pollTask?.cancel()
        pollTask = nil
        sessionID = nil
        close()
    }

    /// A new socket at once, and the poll covers the gap: the old one lives on a way the phone has left.
    func reopen() {
        guard let sessionID else { return }
        close()
        open(sessionID: sessionID)
    }

    private func open(sessionID: String) {
        delay = 1
        receiveTask = Task { [weak self] in
            guard let self else { return }
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
                    if self.socket != nil { self.onDiagnostic?("WebSocket недоступен: \(error.localizedDescription); опрос /hint раз в секунду") }
                }
                self.socketAvailable = false
                self.pingTask?.cancel()
                self.socket?.cancel(with: .goingAway, reason: nil)
                self.socket = nil
                let wait = self.delay
                self.delay = min(wait * 2, 10)
                do { try await Task.sleep(nanoseconds: wait * 1_000_000_000) } catch { break }
            }
        }
    }

    private func close() {
        receiveTask?.cancel()
        pingTask?.cancel()
        socket?.cancel(with: .goingAway, reason: nil)
        receiveTask = nil
        pingTask = nil
        socket = nil
        socketAvailable = false
    }

    /// On a dead path a receive can wait for half a minute, so a failed ping ends the socket here and the poll starts now.
    /// The next one comes after the loop's wait: one that opens and fails again cannot spin.
    private func lost(_ socket: URLSessionWebSocketTask, _ reason: String) {
        guard self.socket === socket else { return }
        if socketAvailable { onDiagnostic?("WebSocket оборвался: \(reason); опрос /hint раз в секунду") }
        socketAvailable = false
        self.socket = nil
        socket.cancel(with: .goingAway, reason: nil)
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
                if self.pongPending {
                    self.lost(socket, "нет ответа на пинг")
                    return
                }
                self.pongPending = true
                socket.sendPing { [weak self] error in
                    Task { @MainActor [weak self] in
                        guard let self, self.socket === socket else { return }
                        if let error {
                            if self.socketAvailable { self.lost(socket, "пинг не прошёл: \(error.localizedDescription)") }  // One that never opened ends through its receive, which says why.
                            return
                        }
                        self.pongPending = false
                        if !self.socketAvailable {
                            self.socketAvailable = true
                            self.delay = 1
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
