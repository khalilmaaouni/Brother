// bm-embed: on-device sentence embeddings for the estate's memory index.
//
// WHY THIS SHAPE. The founder's order (2026-08-28): add embedding machines, because lexical
// retrieval keeps missing notes that describe a problem in different words than the question.
// This machine ships with macOS: Apple's NaturalLanguage NLEmbedding, 512 dimensions, entirely
// on device. No model download, no network, no Python dependency, nothing to rotate or pay for.
// Measured before this file was written: a symptom and its paraphrase score 0.41 cosine, the
// same symptom against an unrelated sentence 0.056, which is exactly the separation the lexical
// index cannot provide.
//
// CONTRACT. Reads JSON lines on stdin: {"id": <int>, "text": <string>}. Writes JSON lines on
// stdout: {"id": <int>, "v": [<double> x 512]}. A line that cannot be parsed or embedded is
// reported on stderr and SKIPPED, never silently zeroed: a zero vector would rank everywhere
// and poison every cosine around it. Exit 0 if anything embedded, 3 if the model itself is
// unavailable (the caller treats that as NO-DATA for the whole signal, not as emptiness).
//
// Build once: swiftc -O tools/bm_embed.swift -o tools/bm-embed
import Foundation
import NaturalLanguage

guard let emb = NLEmbedding.sentenceEmbedding(for: .english) else {
    FileHandle.standardError.write("bm-embed: NLEmbedding unavailable on this OS\n".data(using: .utf8)!)
    exit(3)
}

struct In: Decodable { let id: Int; let text: String }
var embedded = 0
let dec = JSONDecoder()

while let line = readLine(strippingNewline: true) {
    guard !line.isEmpty else { continue }
    guard let data = line.data(using: .utf8), let row = try? dec.decode(In.self, from: data) else {
        FileHandle.standardError.write("bm-embed: skipped one unparseable line\n".data(using: .utf8)!)
        continue
    }
    // The model embeds a SENTENCE; a whole note dilutes to mush. The caller sends title,
    // description and the opening of the body, which is where a note says what it is about.
    let text = String(row.text.prefix(1200))
    guard let v = emb.vector(for: text), !v.isEmpty else {
        FileHandle.standardError.write("bm-embed: no vector for id \(row.id)\n".data(using: .utf8)!)
        continue
    }
    let nums = v.map { String(format: "%.5f", $0) }.joined(separator: ",")
    print("{\"id\":\(row.id),\"v\":[\(nums)]}")
    embedded += 1
}
exit(embedded > 0 ? 0 : 3)
