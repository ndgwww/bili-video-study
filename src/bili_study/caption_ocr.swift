// Read only the caption band selected from a verified frame. All coordinates
// passed on the command line use a top-left origin normalized to the frame.
import AVFoundation
import Foundation
import Vision

struct Cue: Codable {
    let id: Int
    let start: Double
    var end: Double
    let text: String
    let confidence: Float
}

guard CommandLine.arguments.count == 7 else { exit(2) }
let args = CommandLine.arguments
let asset = AVURLAsset(url: URL(fileURLWithPath: args[1]))
let duration = asset.duration.seconds
guard duration.isFinite, duration > 0 else { exit(3) }
let generator = AVAssetImageGenerator(asset: asset)
generator.appliesPreferredTrackTransform = true
generator.requestedTimeToleranceBefore = .zero
generator.requestedTimeToleranceAfter = .zero
let roi = args[3...6].compactMap { Double($0) }
guard roi.count == 4 else { exit(2) }
let sampleInterval = 1.0
var cues = [Cue]()
var examinedFrames = 0
var noReading = 0
for second in 0..<Int(ceil(duration)) {
    try autoreleasepool {
        let requested = CMTime(seconds: Double(second), preferredTimescale: 600)
        var actual = CMTime.zero
        let frame: CGImage
        do {
            frame = try generator.copyCGImage(at: requested, actualTime: &actual)
        } catch {
            return // Fragmented assets may have a final partial second.
        }
        let width = Double(frame.width), height = Double(frame.height)
        let rect = CGRect(x: roi[0]*width, y: roi[1]*height,
                          width: roi[2]*width, height: roi[3]*height).integral
        guard let crop = frame.cropping(to: rect) else { return }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = false
        if #available(macOS 13, *) { request.automaticallyDetectsLanguage = false }
        try VNImageRequestHandler(cgImage: crop).perform([request])
        examinedFrames += 1
        // Preserve spatial reading order when Vision splits a caption at spaces.
        // Suppress chart-axis labels and lower thirds much smaller than the
        // dominant caption line. Keep split lines of similar character size.
        let all = request.results ?? []
        let tallest = all.map { $0.boundingBox.height }.max() ?? 0
        let observations = all.filter {
            $0.boundingBox.height >= max(0.15, tallest * 0.58)
        }.sorted { first, second in
            if abs(first.boundingBox.midY - second.boundingBox.midY) > 0.2 {
                return first.boundingBox.midY > second.boundingBox.midY
            }
            return first.boundingBox.minX < second.boundingBox.minX
        }
        let candidates = observations.compactMap { $0.topCandidates(1).first }
        let pieces = candidates.map(\.string)
        let raw = pieces.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        // Join broken Chinese lines while retaining spaces between Latin words.
        let text = raw.replacingOccurrences(
            of: "(?<=[\\p{Han}]) (?=[\\p{Han}])", with: "", options: .regularExpression)
        let hasHan = text.range(of: "[\\p{Han}]",options: .regularExpression) != nil
        let hasForeignScript = text.range(of: "[\\p{Hiragana}\\p{Katakana}\\p{Hangul}]",options: .regularExpression) != nil
        guard hasHan, !hasForeignScript else {
            noReading += 1
            return
        }
        let start = max(0, actual.seconds), end = min(duration, start + sampleInterval)
        guard end > start else { return }
        if let last = cues.last, last.text == text, abs(last.end - start) < 0.15 {
            cues[cues.count - 1].end = end
        } else {
            cues.append(Cue(id: cues.count, start: start, end: end, text: text,
                            confidence: candidates.map(\.confidence).min() ?? 0))
        }
    }
}
guard examinedFrames > 0 else { exit(4) }
struct OCRResult: Codable {
    let segments: [Cue]
    let examined_frames: Int
    let unread_frames: Int
}
let result = OCRResult(segments: cues, examined_frames: examinedFrames, unread_frames: noReading)
try JSONEncoder().encode(result).write(to: URL(fileURLWithPath: args[2]), options: .atomic)
