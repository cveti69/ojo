import Foundation

/// The last settled screens, kept on the phone. They leave it only when the person speaks to the agent.
public struct FrameShelf: Sendable {
    public static let capacity = 4
    private let folder: URL

    public init(configuration: ProjectConfiguration) throws {
        guard let directory = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: configuration.appGroupIdentifier) else {
            throw ShelfError.unavailable
        }
        self.init(directory: directory)
    }

    public init(directory: URL) {
        folder = directory.appendingPathComponent("kept-frames", isDirectory: true)
    }

    public func put(_ jpeg: Data, timestampMS: Int64) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try jpeg.write(to: folder.appendingPathComponent("\(timestampMS).jpg"), options: .atomic)
        for old in files().dropLast(Self.capacity) { try? FileManager.default.removeItem(at: old) }
    }

    /// Oldest first, and the last one is the screen now.
    public func recent() -> [Data] {
        files().compactMap { try? Data(contentsOf: $0) }
    }

    public func clear() {
        try? FileManager.default.removeItem(at: folder)
    }

    private func files() -> [URL] {
        let found = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        return found.filter { $0.pathExtension == "jpg" }.sorted {
            (Int64($0.deletingPathExtension().lastPathComponent) ?? 0) < (Int64($1.deletingPathExtension().lastPathComponent) ?? 0)
        }
    }

    private enum ShelfError: LocalizedError {
        case unavailable
        var errorDescription: String? { "Папка кадров в общей группе недоступна." }
    }
}
