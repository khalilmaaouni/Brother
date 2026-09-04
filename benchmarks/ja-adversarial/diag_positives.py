#!/usr/bin/env python3
"""Diagnose the adversarial corpus's POSITIVE-class results: for each positive
case (every class except negative), report where the expected note actually
RANKS in the vault's results, which analyzed query tokens the vault produced,
and which of those tokens the expected note and the note that outranked it
actually contain. A case that misses because no token reaches the right note
is told apart from one that merely ranks it low.

Mirrors diag_negatives.py in this directory: read-only, it builds jbench's own
in-memory fixture vault and runs its own _search, and it changes no product
code. Written for the 2026-09-05 diagnostic of the two remaining blind-corpus
misses (one kana_alias, one width_variant), because the shipped helper covers
the negative class only.

Needs the vault tools that ship in this repository. Resolution order for
that directory: $BROTHERMODEUP_TOOLS, else the in-tree sibling
products/brothermode/tools (the shipped harness, bm_vault_jbench.py).
The corpus defaults to the sibling adversarial-ja-corpus.json.

Usage: python3 diag_positives.py [corpus.json] [case_id ...]
       BROTHERMODEUP_TOOLS=<other tools dir> python3 diag_positives.py
With no case ids, every MISSING positive case is reported.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IN_TREE_TOOLS = os.path.normpath(
    os.path.join(HERE, "..", "..", "products", "brothermode", "tools"))
TOOLS = os.environ.get("BROTHERMODEUP_TOOLS", IN_TREE_TOOLS)

JBENCH = os.path.join(TOOLS, "bm_vault_jbench.py")
if not os.path.isfile(JBENCH):
    print("NO-DATA: bm_vault_jbench.py not found under %r; set "
          "BROTHERMODEUP_TOOLS to a directory carrying it" % TOOLS)
    sys.exit(2)

sys.path.insert(0, TOOLS)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    corpus = argv[0] if argv and argv[0].endswith(".json") else os.path.join(
        HERE, "adversarial-ja-corpus.json")
    wanted = set(a for a in argv if not a.endswith(".json"))

    jb = _load("bm_vault_jbench", JBENCH)
    bm = jb._load_bm_vault()
    an = _load("bm_vault_analyzer", os.path.join(TOOLS, "bm_vault_analyzer.py"))

    with open(corpus, encoding="utf-8") as fh:
        fixture = json.load(fh)

    con, stem_to_id = jb.build_fixture_vault(bm, fixture)
    id_to_stem = {v: k for k, v in stem_to_id.items()}
    # Lower-cased on purpose: the scan this diagnoses is a SQLite LIKE, which
    # case-folds ASCII, so a case-sensitive containment check here would under-
    # report exactly the ASCII tokens ("co", "ltd") that decide these cases.
    body_of = {n["stem"]: (n.get("title", "") + " " + n.get("body", "")).lower()
               for n in fixture["notes"]}

    # The dictionary the benchmark installs for the whole run, so the tokens
    # printed here are the tokens the scored run actually used.
    import shutil
    import tempfile
    vault_dir = tempfile.mkdtemp(prefix="bm-diagpos-vault-")
    orig_default_vault = bm._default_vault
    LIMIT = 10
    misses = 0
    try:
        jb.write_dictionary(
            vault_dir, fixture.get("_meta", {}).get("dictionary_terms", []))
        bm._default_vault = lambda: vault_dir

        for case in fixture["cases"]:
            if case["class"] == "negative":
                continue
            expected_stem = case.get("expected_note")
            expected_id = stem_to_id.get(expected_stem)
            fused, _ = bm._search(con, text=case["query"], limit=LIMIT, fast=True)
            ranked = [nid for nid, _s in fused]
            rank = ranked.index(expected_id) + 1 if expected_id in ranked else None
            if rank is not None and not wanted:
                continue
            if wanted and case["id"] not in wanted:
                continue
            if rank is None:
                misses += 1
            top3 = [id_to_stem.get(nid, "?") for nid in ranked[:3]]
            tokens = [t for t in an.analyze(case["query"], vault_dir=vault_dir)
                      if len(t) >= 2]
            print("%s  class=%s" % (case["id"], case["class"]))
            print("  query            %s" % case["query"])
            print("  expected         %s" % expected_stem)
            print("  rank             %s" % (rank if rank else "MISSING from top %d" % LIMIT))
            print("  top3             %s" % top3)
            print("  analyzed tokens  %s" % tokens)
            hit_expected = [t for t in tokens if t.lower() in body_of.get(expected_stem, "")]
            print("  tokens the expected note contains  %s" % hit_expected)
            for stem in top3:
                got = [t for t in tokens if t.lower() in body_of.get(stem, "")]
                print("  tokens %-22s contains  %s" % (stem, got))
            print("")
    finally:
        bm._default_vault = orig_default_vault
        con.close()
        shutil.rmtree(vault_dir, ignore_errors=True)

    if not wanted:
        print("positive cases missing the expected note from the top %d: %d"
              % (LIMIT, misses))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
