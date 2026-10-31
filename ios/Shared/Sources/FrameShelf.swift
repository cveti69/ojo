import Foundation

public struct KeptScreen: Sendable, Equatable {
    public let jpeg: Data
    public let timestampMS: Int64
}

/// The last different screens, kept on the phone. They leave it only when the person speaks to the agent.
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

    /// A newer look at the same screen takes the place of the old one, or repeats would push out the page before it.
    public func put(_ jpeg: Data, timestampMS: Int64, sameScreen: Bool = false) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent("\(timestampMS).jpg")
        let replaced = sameScreen ? files().last : nil
        try jpeg.write(to: file, options: .atomic)
        if let replaced, replaced.lastPathComponent != file.lastPathComponent { try? FileManager.default.removeItem(at: replaced) }
        for old in files().dropLast(Self.capacity) { try? FileManager.default.removeItem(at: old) }
    }

    /// Oldest first, and the last one is the screen now.
    public func recent() -> [KeptScreen] {
        files().compactMap { file in
            guard let jpeg = try? Data(contentsOf: file), let time = Int64(file.deletingPathExtension().lastPathComponent) else { return nil }
            return KeptScreen(jpeg: jpeg, timestampMS: time)
        }
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
