#!/usr/bin/env python3
"""Diagnose the adversarial corpus's negative-class results: for each negative
case, report where the forbidden note actually RANKS in the vault's results, so
a genuine false positive (forbidden ranked at or near the top, above the
relevant notes) is told apart from a small-corpus artifact (forbidden merely
inside a wide top-k over a small note set). Read-only: it builds jbench's own
in-memory fixture vault and runs its own _search; it changes no product code.

Needs the vault tools that ship in this repository. Resolution order for
that directory: $BROTHERMODEUP_TOOLS, else the in-tree sibling
products/brothermode/tools (the shipped harness, bm_vault_jbench.py).
The corpus defaults to the sibling adversarial-ja-corpus.json.

Usage: python3 diag_negatives.py [corpus.json]
       BROTHERMODEUP_TOOLS=<other tools dir> python3 diag_negatives.py [corpus.json]
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IN_TREE_TOOLS = os.path.normpath(
    os.path.join(HERE, "..", "..", "products", "brothermode", "tools"))
TOOLS = os.environ.get("BROTHERMODEUP_TOOLS", IN_TREE_TOOLS)
CORPUS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    HERE, "adversarial-ja-corpus.json")

JBENCH = os.path.join(TOOLS, "bm_vault_jbench.py")
if not os.path.isfile(JBENCH):
    print("NO-DATA: bm_vault_jbench.py not found under %r; set "
          "BROTHERMODEUP_TOOLS to a directory carrying it"
          % TOOLS)
    sys.exit(2)

sys.path.insert(0, TOOLS)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


jb = _load("bm_vault_jbench", JBENCH)
bm = jb._load_bm_vault()

with open(CORPUS, encoding="utf-8") as fh:
    fixture = json.load(fh)

con, stem_to_id = jb.build_fixture_vault(bm, fixture)
id_to_stem = {v: k for k, v in stem_to_id.items()}

LIMIT = 10
worst = []
for case in fixture["cases"]:
    if case["class"] != "negative":
        continue
    forbidden_stem = case.get("forbidden_note")
    forbidden_id = stem_to_id.get(forbidden_stem)
    fused, _ = bm._search(con, text=case["query"], limit=LIMIT, fast=True)
    ranked = [nid for nid, _s in fused]
    rank = ranked.index(forbidden_id) + 1 if forbidden_id in ranked else None
    top3 = [id_to_stem.get(nid, "?") for nid in ranked[:3]]
    status = "PASS(absent)" if rank is None else ("rank #%d" % rank)
    print("%-6s forbidden=%-22s -> %-12s top3=%s"
          % (case["id"], forbidden_stem, status, top3))
    if rank is not None:
        worst.append(rank)

print("\nnegative cases where forbidden appeared:", len(worst))
if worst:
    print("forbidden ranks:", sorted(worst))
    print("at rank 1 (top hit):", sum(1 for r in worst if r == 1))
    print("in top 3:", sum(1 for r in worst if r <= 3))
