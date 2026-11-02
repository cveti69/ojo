import Foundation
import Network

/// The way out to the server. A socket lives on the interface it was opened on.
struct NetworkWay: Equatable {
    let interface: String?  // None while there is no way out.
    let kind: String

    static let none = NetworkWay(interface: nil, kind: "нет связи")

    init(interface: String?, kind: String) {
        self.interface = interface
        self.kind = kind
    }

    init(_ path: NWPath) {
        guard path.status == .satisfied, let first = path.availableInterfaces.first else {
            self = .none
            return
        }
        self.init(interface: first.name, kind: Self.word(for: first.type))
    }

    private static func word(for type: NWInterface.InterfaceType) -> String {
        switch type {
        case .wifi: return "Wi‑Fi"
        case .cellular: return "мобильная сеть"
        case .wiredEthernet: return "провод"
        default: return "другая сеть"
        }
    }

    /// Paths update for many reasons. Only another interface, or a way out after none, leaves the sockets behind.
    func leavesSocketsBehind(after previous: NetworkWay) -> Bool {
        interface != nil && interface != previous.interface
    }
}

/// Sees the phone move to another network as it happens, long before the sockets on the old one find out.
@MainActor
final class NetworkWatch {
    var onDiagnostic: ((String) -> Void)?
    var onNewWay: (() -> Void)?
    private let monitor = NWPathMonitor()
    private var way: NetworkWay?

    func start() {
        monitor.pathUpdateHandler = { [weak self] path in
            let way = NetworkWay(path)
            MainActor.assumeIsolated { self?.update(way) }
        }
        monitor.start(queue: .main)
    }

    func stop() {
        monitor.cancel()
    }

    private func update(_ new: NetworkWay) {
        let previous = way
        guard new != previous else { return }
        way = new
        let reopens = previous.map { new.leavesSocketsBehind(after: $0) } ?? false
        onDiagnostic?("Сеть: \(new.kind)\(new.interface.map { ", \($0)" } ?? "")\(reopens ? "; новые соединения" : "")")
        if reopens { onNewWay?() }
    }
}
