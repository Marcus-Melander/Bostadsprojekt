// Reads the text of a scanned PDF with macOS's own text recognition (Vision), for the förening's annual
// report when it's a scan: `ocr <file.pdf> [max pages]` prints each page's lines, pages split by a form feed.
// Swedish isn't among the languages on macOS 13, so German (which has ä and ö) reads the words; the key
// figures are numbers, which any language reads.
import Foundation
import PDFKit
import Vision

let args = CommandLine.arguments
guard args.count > 1, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
    FileHandle.standardError.write("usage: ocr file.pdf [max pages]\n".data(using: .utf8)!)
    exit(2)
}
let maxPages = args.count > 2 ? Int(args[2]) ?? 30 : 30
for i in 0..<min(doc.pageCount, maxPages) {
    guard let page = doc.page(at: i) else { continue }
    let box = page.bounds(for: .mediaBox)
    let scale: CGFloat = 2.5
    let size = CGSize(width: box.width * scale, height: box.height * scale)
    let image = NSImage(size: size)
    image.lockFocus()
    if let ctx = NSGraphicsContext.current?.cgContext {
        ctx.setFillColor(.white)
        ctx.fill(CGRect(origin: .zero, size: size))
        ctx.scaleBy(x: scale, y: scale)
        page.draw(with: .mediaBox, to: ctx)
    }
    image.unlockFocus()
    guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["de-DE", "en-US"]
    req.usesLanguageCorrection = false
    try? VNImageRequestHandler(cgImage: cg).perform([req])
    // Lines top to bottom; pieces of one table row (label, then each year's number) joined left to right
    let obs = (req.results ?? []).compactMap { o -> (CGRect, String)? in
        guard let t = o.topCandidates(1).first?.string else { return nil }
        return (o.boundingBox, t)
    }
    var rows: [(y: CGFloat, h: CGFloat, parts: [(CGFloat, String)])] = []
    for (b, t) in obs.sorted(by: { $0.0.midY > $1.0.midY }) {
        if let k = rows.lastIndex(where: { abs($0.y - b.midY) < max($0.h, b.height) * 0.5 }) {
            rows[k].parts.append((b.minX, t))
        } else {
            rows.append((b.midY, b.height, [(b.minX, t)]))
        }
    }
    for r in rows { print(r.parts.sorted(by: { $0.0 < $1.0 }).map { $0.1 }.joined(separator: " ")) }
    print("\u{0C}")
}
