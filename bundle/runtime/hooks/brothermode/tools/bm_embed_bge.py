#!/usr/bin/env python3
"""bm_embed_bge: the estate's dense retrieval machine (BAAI bge-small-en-v1.5, 384 dims).

WHY THIS MODEL, measured rather than believed. Apple's on-device NLEmbedding was tried first
because it needs no dependency at all, and it FAILED the discrimination test on this corpus: it
scored an unrelated note (0.573) above the true answer (0.447) for the very query that motivated
the dense signal. bge-small, on the identical test, ranks the true answer first (0.623) with the
distractor second (0.585) and the unrelated sentence at 0.351. The download happens once; every
run after is fully local.

CONTRACT, same as bm-embed so the caller treats them interchangeably: JSON lines on stdin
{"id": int, "text": str, "query": optional bool}, JSON lines out {"id": int, "v": [float]}.
The "query" flag matters: bge is ASYMMETRIC, and a query embedded without its instruction prefix
lands in a slightly different space than the passages, which quietly costs rank quality.
Unparseable lines are reported and skipped, never zeroed. Exit 3 when the model cannot load,
which the caller must treat as the signal being ABSENT, not as zero matches.

Runs inside .venv-embed (created 2026-08-28; sentence-transformers 6.0.0). The shebang caller is
tools/bm-embed-bge, a two line shell wrapper pinning that interpreter.
"""
import json
import sys

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def main():
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    except Exception as e:
        sys.stderr.write("bm_embed_bge: model unavailable: %r\n" % (e,))
        return 3
    rows = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            rows.append((int(row["id"]), str(row["text"])[:1500], bool(row.get("query"))))
        except (ValueError, KeyError, TypeError):
            sys.stderr.write("bm_embed_bge: skipped one unparseable line\n")
    if not rows:
        return 3
    texts = [(QUERY_PREFIX + t) if is_q else t for _, t, is_q in rows]
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)
    for (rid, _, _), v in zip(rows, vecs):
        print(json.dumps({"id": rid, "v": [round(float(x), 5) for x in v]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
