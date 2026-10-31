// Reads text and word boxes with Apple Vision, the same engine the iPhone has.
// Usage: swift ocr.swift <image> [more images]; writes <image name>.json next to the current directory.
import AppKit
import Foundation
import Vision

struct Word: Codable {
    let text: String
    let box: [Double]
}

func topLeftBox(_ rect: CGRect) -> [Double] {
    [rect.minX, 1 - rect.maxY, rect.maxX, 1 - rect.minY].map { Double($0) }
}

func recognize(_ path: String) throws -> (lines: [Word], ms: Double) {
    guard let image = NSImage(contentsOfFile: path),
          let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        throw NSError(domain: "ocr", code: 1)
    }
    let started = Date()
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US"]
    try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
    var words: [Word] = []
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let text = candidate.string
        text.enumerateSubstrings(in: text.startIndex..., options: .byWords) { word, range, _, _ in
            guard let word, let box = try? candidate.boundingBox(for: range)?.boundingBox else { return }
            words.append(Word(text: word, box: topLeftBox(box)))
        }
        words.append(Word(text: "\u{1}" + text, box: topLeftBox(observation.boundingBox)))
    }
    return (words, Date().timeIntervalSince(started) * 1000)
}

for path in CommandLine.arguments.dropFirst() {
    let result = try recognize(path)
    let name = URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent
    let data = try JSONEncoder().encode(result.lines)
    try data.write(to: URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent("\(name).json"))
    print(name, Int(result.ms), "ms", result.lines.count)
}
