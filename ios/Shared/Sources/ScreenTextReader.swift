import CoreGraphics
import Darwin
import Foundation
import ImageIO
import Vision

public struct ScreenTextWord: Codable, Equatable, Sendable {
    public let text: String
    public let box: [Double]

    public init(text: String, box: [Double]) {
        self.text = text
        self.box = box
    }
}

public struct ScreenTextLine: Codable, Equatable, Sendable {
    public let text: String
    public let words: [ScreenTextWord]

    public init(text: String, words: [ScreenTextWord]) {
        self.text = text
        self.words = words
    }
}

public struct ScreenTextRequest: Codable, Sendable {
    public let frameTS: Int64
    public let lines: [ScreenTextLine]

    public init(frameTS: Int64, lines: [ScreenTextLine]) {
        self.frameTS = frameTS
        self.lines = lines
    }

    enum CodingKeys: String, CodingKey { case frameTS = "frame_ts", lines }
}

public struct ScreenTextReader: Sendable {
    public init() {}

    public static func recognitionLanguages(targetLang: String) -> [String] {
        targetLang.lowercased().hasPrefix("zh") ? ["en-US", "zh-Hans"] : ["en-US"]
    }

    public static func topLeftBox(_ rect: CGRect) -> [Double] {
        [rect.minX, 1 - rect.maxY, rect.maxX, 1 - rect.minY].map { min(1, max(0, Double($0))) }
    }

    public func read(_ image: CGImage, targetLang: String) throws -> [ScreenTextLine] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.recognitionLanguages = Self.recognitionLanguages(targetLang: targetLang)
        try VNImageRequestHandler(cgImage: image, orientation: .up, options: [:]).perform([request])
        return (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let text = candidate.string
            var words: [ScreenTextWord] = []
            text.enumerateSubstrings(in: text.startIndex..., options: .byWords) { word, range, _, _ in
                guard let word, let box = try? candidate.boundingBox(for: range)?.boundingBox else { return }
                words.append(ScreenTextWord(text: word, box: Self.topLeftBox(box)))
            }
            return ScreenTextLine(text: text, words: words)
        }
    }
}

public enum ProcessMemory {
    public static var physicalFootprintMB: Double? {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return result == KERN_SUCCESS ? Double(info.phys_footprint) / 1_048_576 : nil
    }

    public static var description: String {
        physicalFootprintMB.map { String(format: "%.1f", locale: Locale(identifier: "ru_RU"), $0) } ?? "недоступно"
    }
}

public struct ScreenTextReading: Sendable {
    public let lines: [ScreenTextLine]
    public let error: String?
    public let elapsedMS: Double
    public let footprintMB: Double?

    public var diagnostic: String {
        let words = lines.reduce(0) { $0 + $1.words.count }
        let memory = footprintMB.map { String(format: "%.1f", locale: Locale(identifier: "ru_RU"), $0) } ?? "недоступно"
        let failure = error.map { "; ошибка Vision: \($0)" } ?? ""
        return "\(Int(elapsedMS.rounded())) мс, слов: \(words), phys_footprint: \(memory) МБ\(failure)"
    }
}

public final class ScreenTextWorker: Sendable {
    private let queue = DispatchQueue(label: "screen-text.vision", qos: .utility)

    public init() {}

    public func read(jpegData: Data, targetLang: String, onStart: @escaping @Sendable () -> Void = {}) async -> ScreenTextReading {
        await withCheckedContinuation { continuation in
            queue.async {
                onStart()
                let started = ProcessInfo.processInfo.systemUptime
                let result: Result<[ScreenTextLine], Error> = Result {
                    try autoreleasepool {
                        guard let source = CGImageSourceCreateWithData(jpegData as CFData, nil),
                              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
                            throw ReaderError.invalidImage
                        }
                        return try ScreenTextReader().read(image, targetLang: targetLang)
                    }
                }
                let elapsed = (ProcessInfo.processInfo.systemUptime - started) * 1000
                let reading: ScreenTextReading
                switch result {
                case .success(let lines):
                    reading = ScreenTextReading(lines: lines, error: nil, elapsedMS: elapsed, footprintMB: ProcessMemory.physicalFootprintMB)
                case .failure(let error):
                    reading = ScreenTextReading(lines: [], error: error.localizedDescription, elapsedMS: elapsed, footprintMB: ProcessMemory.physicalFootprintMB)
                }
                continuation.resume(returning: reading)
            }
        }
    }

    private enum ReaderError: LocalizedError {
        case invalidImage
        var errorDescription: String? { "Не удалось прочитать JPEG для распознавания текста." }
    }
}
