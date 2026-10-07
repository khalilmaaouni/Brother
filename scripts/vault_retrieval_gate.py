#!/usr/bin/env python3
"""Regression gate for the Kay Vault's real retrieval tool (bm_vault.py recall).

WHAT THIS PROTECTS. docs/plan/vault-golden-queries-2026-09-14.json is a fixture
of 13 real queries against the live vault, each carrying its OWN recorded
baseline_in_top5 (7 pass today, 6 already fail -- that is the honest floor, not
a defect this gate exists to fix). The gate re-runs every query through the
exact invocation H1 confirmed (`recall --query "..." --limit 5 --explain`) and
fails ONLY on a REGRESSION: a query whose baseline was True and is now False.
A query that was already failing and stays failing is not a regression -- the
gate's job is to catch new damage to the RRF fusion / rarity rewrite pipeline,
never to demand 13/13 immediately.

Exit 0: no regressions. Exit 1: regressions found, named. Exit 2: the fixture
or the tool could not be read/run at all (a boundary failure, never silently
folded into "no regressions").

Python 3.9 floor, standard library only.
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

DEFAULT_GOLDEN_QUERIES = os.path.join(REPO, "docs", "plan", "vault-golden-queries-2026-09-14.json")
DEFAULT_TOOL = os.path.expanduser("~/.claude/vault-tools/tools/bm_vault.py")
LIMIT = 5           # matches how baseline_in_top5 was measured (H1's own invocation)
TIMEOUT_S = 120
_ID_PREFIX_RE = re.compile(r"^\d+:\s*")


def _title_substring(expected):
    """'10945: concurrent-session-is-a-hazard.md' -> 'concurrent-session-is-a-hazard.md'.

    Strips only a leading '<digits>: ' id prefix; a value with no such prefix is
    used as-is rather than guessed at."""
    return _ID_PREFIX_RE.sub("", expected.strip())


def default_query_runner(tool_path):
    """A runner(query_text) -> str that shells out to the real tool, the exact
    invocation H1 confirmed. Raises OSError/subprocess.TimeoutExpired on a
    boundary failure -- never swallowed, since a tool that cannot run is not
    evidence the query fails, it is evidence nothing was measured."""
    def run(query_text):
        proc = subprocess.run(
            [sys.executable, tool_path, "recall", "--query", query_text,
             "--limit", str(LIMIT), "--explain"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=TIMEOUT_S,
        )
        return proc.stdout.decode("utf-8", "replace")
    return run


def load_golden_queries(path):
    """(doc, None) or (None, reason) -- an explicit failure path for the file I/O
    and JSON-decode boundary calls, never a bare crash."""
    if not os.path.isfile(path):
        return None, "no golden-query fixture at %r" % path
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        return None, "%r is not readable JSON (%s)" % (path, e)
    if not isinstance(doc, dict) or not isinstance(doc.get("queries"), list):
        return None, "%r carries no 'queries' list" % path
    return doc, None


def check_retrieval(golden_queries_path, tool_path, query_runner=None):
    """(all_meet_baseline, regressions).

    regressions is a list of {"id", "query", "expected", "baseline_rank_note"}
    for every query whose recorded baseline_in_top5 was True and is now False.
    A query whose baseline was already False and stays False is NOT a
    regression -- it is the honest floor the fixture already documents.

    query_runner(query_text) -> str defaults to the real subprocess call
    against tool_path; tests inject a fake one so the unit tests never touch
    the live vault. Raises ValueError if the fixture cannot be read (an
    explicit failure path, not a silent pass)."""
    doc, why = load_golden_queries(golden_queries_path)
    if doc is None:
        raise ValueError(why)

    runner = query_runner or default_query_runner(tool_path)
    regressions = []
    for q in doc["queries"]:
        baseline = bool(q.get("baseline_in_top5"))
        if not baseline:
            continue  # already failing today: not this gate's job to fix, can't regress further
        expected_substring = _title_substring(q["expected_note_id_or_title_substring"])
        try:
            output_text = runner(q["query"])
        except (OSError, subprocess.TimeoutExpired) as e:
            regressions.append({
                "id": q["id"], "query": q["query"], "expected": expected_substring,
                "reason": "tool did not run (%s)" % e,
            })
            continue
        if expected_substring not in output_text:
            regressions.append({
                "id": q["id"], "query": q["query"], "expected": expected_substring,
                "reason": "expected note no longer in top %d" % LIMIT,
            })

    return (len(regressions) == 0, regressions)


def main(argv):
    parser = argparse.ArgumentParser(
        description=(
            "Gate protecting Kay Vault retrieval quality: re-runs the golden-query "
            "fixture's queries against the real bm_vault.py recall path and fails "
            "only on a REGRESSION (baseline_in_top5 True, now False). A query "
            "already failing at baseline is not this gate's concern. Exit 0: no "
            "regressions. Exit 1: regressions found, named. Exit 2: fixture or "
            "tool could not be read/run."
        )
    )
    parser.add_argument("--golden-queries", default=DEFAULT_GOLDEN_QUERIES,
                        help="path to the golden-query fixture JSON")
    parser.add_argument("--tool", default=DEFAULT_TOOL,
                        help="path to bm_vault.py")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.tool):
        print("NO-DATA: no bm_vault.py at %r, nothing measured" % args.tool)
        return 2

    try:
        ok, regressions = check_retrieval(args.golden_queries, args.tool)
    except ValueError as e:
        print("NO-DATA: %s" % e)
        return 2

    if ok:
        print("PASS: 0 regressions against %s" % os.path.basename(args.golden_queries))
        return 0

    print("FAIL: %d regression(s) against %s" % (len(regressions), os.path.basename(args.golden_queries)))
    for r in regressions:
        print("  %s: %r -- %s (expected %r)" % (r["id"], r["query"], r["reason"], r["expected"]))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
