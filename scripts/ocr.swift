// Reads the text of a scanned PDF with macOS's own text recognition (Vision), for the förening's annual
// report when it's a scan: `ocr <file.pdf> [max pages] [tiles]` prints each page's lines, pages split by a form feed.
// Swedish isn't among the languages on macOS 13, so German (which has ä and ö) reads the words; the key
// figures are numbers, which any language reads.
// With "tiles", each page is also read in six parts: Vision passes over small grey text on a whole page
// (HSB's flerårsöversikt is a screenshot of a table, a fifth the size of the body text), but reads it in a part.
import Foundation
import PDFKit
import Vision

let args = CommandLine.arguments
guard args.count > 1, let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
    FileHandle.standardError.write("usage: ocr file.pdf [max pages] [tiles]\n".data(using: .utf8)!)
    exit(2)
}
let maxPages = args.count > 2 ? Int(args[2]) ?? 30 : 30
let tiles = args.count > 3 && args[3] == "tiles"

func recognize(_ cg: CGImage) -> [(CGRect, String)] {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["de-DE", "en-US"]
    req.usesLanguageCorrection = false
    try? VNImageRequestHandler(cgImage: cg).perform([req])
    return (req.results ?? []).compactMap { o -> (CGRect, String)? in
        guard let t = o.topCandidates(1).first?.string else { return nil }
        return (o.boundingBox, t)
    }
}

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
    var obs = recognize(cg)
    if tiles {
        // Two columns by three rows, each widened by a margin so a line cut at its edge is whole in the next;
        // what a part reads is kept where the whole page read nothing
        let w = CGFloat(cg.width), h = CGFloat(cg.height), pad: CGFloat = 0.15
        for c in 0..<2 {
            for r in 0..<3 {
                let rect = CGRect(x: max(0, (CGFloat(c) - pad) * w / 2), y: max(0, (CGFloat(r) - pad) * h / 3),
                                  width: w / 2 * (1 + 2 * pad), height: h / 3 * (1 + 2 * pad))
                    .intersection(CGRect(x: 0, y: 0, width: w, height: h))
                guard let part = cg.cropping(to: rect) else { continue }
                for (b, t) in recognize(part) {
                    // From the part's coordinates (origin bottom left) to the page's
                    let pb = CGRect(x: (rect.minX + b.minX * rect.width) / w, y: 1 - (rect.minY + (1 - b.minY) * rect.height) / h,
                                    width: b.width * rect.width / w, height: b.height * rect.height / h)
                    let seen = obs.contains { o in
                        o.0.insetBy(dx: -0.002, dy: -0.002).contains(CGPoint(x: pb.midX, y: pb.midY)) || pb.contains(CGPoint(x: o.0.midX, y: o.0.midY))
                    }
                    if !seen { obs.append((pb, t)) }
                }
            }
        }
    }
    // Lines top to bottom; pieces of one table row (label, then each year's number) joined left to right
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
