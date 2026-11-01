import CoreGraphics
import CoreML
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

public enum ScreenTextLevel: String, Sendable {
    case accurate
    case fast
}

public struct ScreenTextReader: Sendable {
    public init() {}

    private static let firstLanguages = [
        ("zh-hant", "zh-Hant"), ("zh-tw", "zh-Hant"), ("zh-hk", "zh-Hant"), ("zh", "zh-Hans"), ("th", "th-TH"),
        ("ja", "ja-JP"), ("ko", "ko-KR"), ("vi", "vi-VT"), ("ru", "ru-RU"), ("ar", "ar-SA"),
    ]

    // Order matters: with English first, Vision reads Chinese, Thai, Japanese and Korean as nothing.
    public static func recognitionLanguages(screenLang: String?) -> [String] {
        let code = screenLang?.lowercased() ?? ""
        return (firstLanguages.first { code.hasPrefix($0.0) }.map { [$0.1] } ?? []) + ["en-US"]
    }

    public static func topLeftBox(_ rect: CGRect) -> [Double] {
        [rect.minX, 1 - rect.maxY, rect.maxX, 1 - rect.minY].map { min(1, max(0, Double($0))) }
    }

    public static func word(_ text: String, box: CGRect?, lineBox: CGRect) -> ScreenTextWord {
        ScreenTextWord(text: text, box: topLeftBox(box.flatMap { $0.width > 0 && $0.height > 0 ? $0 : nil } ?? lineBox))
    }

    /// Detection reads any language on its own. The language of the screen only keeps a rare word of it among the Latin ones,
    /// and a wrong one takes nothing away, so what the settings say does not matter here.
    public func read(_ image: CGImage, screenLang: String? = nil, level: ScreenTextLevel = .accurate, cpuOnly: Bool = false) throws -> [ScreenTextLine] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = level == .fast ? .fast : .accurate
        request.usesLanguageCorrection = false
        if level == .fast {
            request.recognitionLanguages = ["en-US"]
        } else {
            request.automaticallyDetectsLanguage = true
            request.recognitionLanguages = Self.recognitionLanguages(screenLang: screenLang)
        }
        if cpuOnly {
            if #available(iOS 17.0, macOS 14.0, *) {
                let devices = try request.supportedComputeStageDevices
                let cpu: MLComputeDevice? = devices[.main]?.first {
                    if case .cpu = $0 { return true }
                    return false
                }
                guard let cpu else { throw ReaderError.cpuUnavailable }
                request.setComputeDevice(cpu, for: .main)
            } else { throw ReaderError.cpuUnavailable }
        }
        try VNImageRequestHandler(cgImage: image, orientation: .up, options: [:]).perform([request])
        return (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let text = candidate.string
            var words: [ScreenTextWord] = []
            text.enumerateSubstrings(in: text.startIndex..., options: .byWords) { word, range, _, _ in
                guard let word, !word.trimmingCharacters(in: .whitespaces).isEmpty else { return }  // Vision makes them in mixed text.
                let box = try? candidate.boundingBox(for: range)?.boundingBox
                words.append(Self.word(word, box: box, lineBox: observation.boundingBox))
            }
            return ScreenTextLine(text: text, words: words)
        }
    }

    private enum ReaderError: LocalizedError {
        case cpuUnavailable
        var errorDescription: String? { "Vision не предоставил CPU для основной стадии распознавания." }
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
    public let level: ScreenTextLevel
    public let cpuOnly: Bool
    public let retriedOnCPU: Bool
    public let skipped: Bool

    public var diagnostic: String {
        let words = lines.reduce(0) { $0 + $1.words.count }
        let memory = footprintMB.map { String(format: "%.1f", locale: Locale(identifier: "ru_RU"), $0) } ?? "недоступно"
        let failure = error.map { "; ошибка Vision: \($0)" } ?? ""
        let retry = retriedOnCPU ? "; повтор с CPU" : ""
        return "\(Int(elapsedMS.rounded())) мс, слов: \(words), phys_footprint: \(memory) МБ; \(level.rawValue), \(cpuOnly ? "только CPU" : "устройство автоматически")\(retry)\(failure)"
    }
}

public final class ScreenTextWorker: Sendable {
    private let queue = DispatchQueue(label: "screen-text.vision", qos: .utility)
    private static let cpuFallback = CPUFallbackState()

    public init() {}

    public func read(
        jpegData: Data, screenLang: String? = nil, level: ScreenTextLevel = .accurate, cpuOnly: Bool = false,
        canRead: @escaping @Sendable () -> Bool = { true },
        onDiagnostic: (@Sendable (String) -> Void)? = nil,
        onStart: @escaping @Sendable () -> Void = {}
    ) async -> ScreenTextReading {
        await withCheckedContinuation { continuation in
            queue.async {
                let started = ProcessInfo.processInfo.systemUptime
                var useCPU = cpuOnly || Self.cpuFallback.isEnabled
                var retriedOnCPU = false
                var skipped = false
                Self.cpuFallback.reportIfNeeded(onDiagnostic)
                let result: Result<[ScreenTextLine], Error> = Result {
                    try autoreleasepool {
                        guard let source = CGImageSourceCreateWithData(jpegData as CFData, nil),
                              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
                            throw ReaderError.invalidImage
                        }
                        guard canRead() else { skipped = true; return [] }
                        onStart()
                        do {
                            return try ScreenTextReader().read(image, screenLang: screenLang, level: level, cpuOnly: useCPU)
                        } catch {
                            guard !useCPU, canRead() else { throw error }
                            let originalError = error.localizedDescription
                            retriedOnCPU = true
                            useCPU = true
                            do {
                                let lines = try ScreenTextReader().read(image, screenLang: screenLang, level: level, cpuOnly: true)
                                Self.cpuFallback.enable()
                                Self.cpuFallback.reportIfNeeded(onDiagnostic)
                                return lines
                            } catch { throw ReaderError.cpuRetryFailed(originalError, error.localizedDescription) }
                        }
                    }
                }
                let elapsed = (ProcessInfo.processInfo.systemUptime - started) * 1000
                let reading: ScreenTextReading
                switch result {
                case .success(let lines):
                    reading = ScreenTextReading(lines: lines, error: nil, elapsedMS: elapsed, footprintMB: ProcessMemory.physicalFootprintMB,
                                                level: level, cpuOnly: useCPU, retriedOnCPU: retriedOnCPU, skipped: skipped)
                case .failure(let error):
                    reading = ScreenTextReading(lines: [], error: error.localizedDescription, elapsedMS: elapsed, footprintMB: ProcessMemory.physicalFootprintMB,
                                                level: level, cpuOnly: useCPU, retriedOnCPU: retriedOnCPU, skipped: false)
                }
                continuation.resume(returning: reading)
            }
        }
    }

    private enum ReaderError: LocalizedError {
        case invalidImage
        case cpuRetryFailed(String, String)
        var errorDescription: String? {
            switch self {
            case .invalidImage: return "Не удалось прочитать JPEG для распознавания текста."
            case .cpuRetryFailed(let original, let cpu): return "Автоматически: \(original); повтор с CPU: \(cpu)"
            }
        }
    }

    private final class CPUFallbackState: @unchecked Sendable {
        private let lock = NSLock()
        private var enabled = false
        private var reported = false

        var isEnabled: Bool { lock.withLock { enabled } }

        func enable() { lock.withLock { enabled = true } }

        func reportIfNeeded(_ callback: (@Sendable (String) -> Void)?) {
            guard let callback else { return }
            let report = lock.withLock { () -> Bool in
                guard enabled, !reported else { return false }
                reported = true
                return true
            }
            if report { callback("Vision: запасной CPU сработал; до конца процесса читаем только на CPU") }
        }
    }
}
