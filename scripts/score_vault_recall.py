#!/usr/bin/env python3
"""Honest before/after measurement of bm_vault.py's dense/embedding signal, using the
`recall` code path (_search called with text= set), because the `check`/--paths path
never sets text= and structurally cannot exercise dense scoring at all.

WHY THIS SHAPE (2026-09-13 investigation): the production tool is
products/brothermode/tools/bm_vault.py (3931 lines; this is score_vault_retrieval.py's
own DEFAULT_TOOL). It has NO separate _rerank_dense() function -- that only exists in
the older, structurally different ~/.claude/vault-tools/tools/bm_vault.py copy (763
lines). Production instead folds the dense signal directly into the RRF fusion lists
inside _search() (products/brothermode/tools/bm_vault.py, roughly line 2013-2105),
gated by the `need_dense` decision at roughly line 2060-2070: dense scoring runs only
when anchor+bm25 combined is empty, returns fewer than --limit results, or anchor and
bm25 disagree (no overlap in their top 10).

So "disable the rerank" here means: make the dense signal a no-op, the same no-op the
code already falls back to when no embedder is installed (`_embed_texts` returning
None -> why["__nodata__"] -> the dense-scored lists are simply never appended to the
RRF fusion). Monkeypatching `_embed_texts` to always return None reuses that exact,
already-tested fallback branch instead of reaching into _search's internals.

This calls _search() directly rather than shelling out to `bm_vault.py recall`:
cmd_recall's ranking IS _search(text=...) -- the rest of cmd_recall is bookkeeping
(answer ledger, read audit, event id) that would otherwise write ~2x len(queries)
synthetic rows into the *real* vault's ledger/audit trail for a benchmark run. Calling
_search directly gets the identical ranking behavior with no side effects, and the
sqlite connection is never committed, so nothing (including the query-embedding cache)
is persisted to the live index by this script either.

Usage:
    BROTHERMODE_EMBED_PYTHON=/path/to/venv/python HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
        python3 scripts/score_vault_recall.py [--queries path.json] [--limit 6]

Prerequisite: the live index needs dense vectors built at all (`bm_vault.py index` with
the embedder env vars set) -- an empty `vectors` table means the dense stage can find
nothing to score no matter how well the query is chosen. This was checked and fixed as
part of this run (see the printed vector count below).
"""
import argparse
import importlib.util
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TOOL = os.path.join(REPO, "products", "brothermode", "tools", "bm_vault.py")
DEFAULT_QUERIES = os.path.join(REPO, "docs", "plan",
                                "vault-recall-benchmark-queries-2026-09-13.json")


def _load_bm_vault(tool_path):
    spec = importlib.util.spec_from_file_location("bm_vault_under_test", tool_path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(tool_path))  # bm_vault imports sibling modules by bare name
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def _titles(con, ids):
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = con.execute("SELECT id, title, path FROM notes WHERE id IN (%s)" % marks, ids).fetchall()
    return {r["id"]: (r["title"], r["path"]) for r in rows}


def _run_once(bm, query, limit, disable_dense):
    """One _search() call, in its own connection so no state (query-embedding cache,
    anything) leaks between the enabled and disabled runs. Never committed: read-only
    in effect against the live index."""
    con = bm._connect()
    bm._schema(con)
    explain = []
    real_embed = bm._embed_texts
    if disable_dense:
        bm._embed_texts = lambda pairs, query=False: None  # the code's own "no embed machine" path
    try:
        fused, why, total = bm._search(con, text=query, limit=limit, fast=False, explain=explain)
    finally:
        bm._embed_texts = real_embed
    dense_note = next((line for line in explain if line.startswith("dense:")), "dense: <no line>")
    return fused, dense_note, total, con


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tool", default=DEFAULT_TOOL)
    ap.add_argument("--queries", default=DEFAULT_QUERIES)
    ap.add_argument("--limit", type=int, default=6)
    args = ap.parse_args()

    if not os.path.exists(args.tool):
        print("NO-DATA: no bm_vault.py at %r" % args.tool)
        return 2
    with open(args.queries, encoding="utf-8") as f:
        corpus = json.load(f)

    bm = _load_bm_vault(args.tool)

    # Prove the prerequisite rather than assuming it: an empty vectors table means
    # need_dense can fire all day and still never move a ranking.
    probe_con = bm._connect()
    vec_count = probe_con.execute("SELECT COUNT(*) c FROM vectors").fetchone()["c"]
    note_count = probe_con.execute("SELECT COUNT(*) c FROM notes").fetchone()["c"]
    probe_con.close()
    print("live index: %d note(s), %d dense vector(s) built" % (note_count, vec_count))
    if vec_count == 0:
        print("NO-DATA: vectors table is empty -- dense scoring cannot fire regardless of "
              "query choice. Run `bm_vault.py index` with the embedder env vars first.")
        return 1

    fired = 0
    changed = 0
    results = []
    for item in corpus["queries"]:
        qid, query = item["id"], item["query"]
        fused_with, dense_note_with, _, con_with = _run_once(bm, query, args.limit, disable_dense=False)
        fused_without, dense_note_without, _, con_without = _run_once(bm, query, args.limit, disable_dense=True)

        ids_with = [nid for nid, _ in fused_with]
        ids_without = [nid for nid, _ in fused_without]
        titles = _titles(con_with, list(set(ids_with) | set(ids_without)))
        con_with.close()
        con_without.close()

        need_dense_fired = dense_note_with.startswith("dense: loading") or \
            dense_note_with.startswith("dense: query cache hit")
        order_changed = ids_with != ids_without
        if need_dense_fired:
            fired += 1
        if order_changed:
            changed += 1

        top_title_with = titles.get(ids_with[0], ("<none>", ""))[0] if ids_with else "<none>"
        top_title_without = titles.get(ids_without[0], ("<none>", ""))[0] if ids_without else "<none>"

        results.append({
            "id": qid,
            "query": query,
            "grounded_in": item.get("grounded_in"),
            "need_dense_fired": need_dense_fired,
            "dense_note": dense_note_with,
            "order_changed": order_changed,
            "top_result_with_dense": top_title_with,
            "top_result_without_dense": top_title_without,
            "ranked_ids_with_dense": ids_with,
            "ranked_ids_without_dense": ids_without,
        })

        print("\n[%s] %s" % (qid, query))
        print("  grounded in: %s" % item.get("grounded_in"))
        print("  %s" % dense_note_with)
        print("  order changed by dense signal: %s" % order_changed)
        print("  top with dense:    %s" % top_title_with)
        print("  top without dense: %s" % top_title_without)
        if order_changed:
            print("  ranked ids with:    %s" % ids_with)
            print("  ranked ids without: %s" % ids_without)

    n = len(corpus["queries"])
    print("\n=== summary ===")
    print("%d/%d quer(y/ies) triggered need_dense (dense signal actually loaded/scored)" % (fired, n))
    print("%d/%d quer(y/ies) had their top-%d ranking changed by the dense signal"
          % (changed, n, args.limit))
    if fired == 0:
        print("HONEST NOTE: need_dense never fired on this corpus -- the lexical signals "
              "(anchor + BM25) were sufficient every time, so no before/after difference "
              "exists to report. This would be true regardless of embedder availability.")
    elif changed == 0:
        print("HONEST NOTE: dense scoring fired but never changed the top-%d order on this "
              "corpus -- it agreed with the lexical ranking every time it ran." % args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
