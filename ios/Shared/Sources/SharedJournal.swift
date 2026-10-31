import Darwin
import Foundation
import OSLog

public final class SharedJournal: Sendable {
    private let fileURL: URL?
    private let source: String
    private let logger = Logger(subsystem: "App", category: "Journal")
    private static let maximumBytes = 256 * 1024

    public init(configuration: ProjectConfiguration, source: String) {
        fileURL = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: configuration.appGroupIdentifier)?
            .appendingPathComponent("events.log")
        self.source = source
    }

    public func append(_ message: String) {
        let time = Date().formatted(.dateTime.hour(.twoDigits(amPM: .omitted)).minute(.twoDigits).second(.twoDigits).secondFraction(.fractional(3)))
        let line = "\(time) [\(source)] \(message.replacingOccurrences(of: "\n", with: " "))\n"
        guard let fileURL else {
            logger.error("App Group journal unavailable")
            return
        }
        let descriptor = open(fileURL.path, O_CREAT | O_RDWR | O_APPEND, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { return }
        defer { close(descriptor) }
        guard flock(descriptor, LOCK_EX) == 0 else { return }
        defer { flock(descriptor, LOCK_UN) }
        if lseek(descriptor, 0, SEEK_END) > Self.maximumBytes {
            var tail = [UInt8](repeating: 0, count: Self.maximumBytes / 2)
            lseek(descriptor, -off_t(tail.count), SEEK_END)
            let count = read(descriptor, &tail, tail.count)
            if count > 0, let newline = tail.prefix(count).firstIndex(of: 10) {
                let kept = Data(tail[(newline + 1)..<count])
                ftruncate(descriptor, 0)
                kept.withUnsafeBytes { buffer in
                    if let address = buffer.baseAddress { _ = write(descriptor, address, buffer.count) }
                }
            }
        }
        Data(line.utf8).withUnsafeBytes { buffer in
            if let address = buffer.baseAddress { _ = write(descriptor, address, buffer.count) }
        }
    }

    public func contents() -> String {
        guard let fileURL else { return "Журнал недоступен: проверьте App Group в подписи приложения." }
        let descriptor = open(fileURL.path, O_RDONLY)
        guard descriptor >= 0 else { return "Журнал пока пуст." }
        defer { close(descriptor) }
        guard flock(descriptor, LOCK_SH) == 0 else { return "Не удалось прочитать журнал." }
        defer { flock(descriptor, LOCK_UN) }
        var bytes = [UInt8](repeating: 0, count: Self.maximumBytes + 4096)
        let count = read(descriptor, &bytes, bytes.count)
        guard count > 0 else { return "Журнал пока пуст." }
        return String(decoding: bytes.prefix(count), as: UTF8.self)
    }
}
