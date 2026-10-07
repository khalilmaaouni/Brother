#!/usr/bin/env python3
"""Unit L6 done check: every catalogued Jev use case links a real RESERVE plus RECONCILE in the dispatch ledger.

The plan's done check was a sentence ("each catalogued use case's entry links a real ledger entry from
openrouter_ledger.jsonl proving it was actually called"), which the closer refuses to run. This is that sentence as a
command, reading the catalogue and the proof link L6 itself shipped (tools/jev_catalogue). Exit 0 GREEN, 1 RED naming
the unproven entries, 3 NO-DATA (catalogue or ledger unreadable, or no entry). The ledger is the dispatch state root's
(BROTHER_OR_STATE_ROOT, default ~/.claude/brother-or-dispatch-state): the proof that a call happened lives on the
machine that made it, so under an empty home this reads NO-DATA, never GREEN.
SECOND HALF (2026-09-28): sixty of those seventy calls audit registry anchors, so a GREEN catalogue says the
registry is real, not that each use case is documented. The owner asked for every use case to say how it is used,
when, how it fits the Brother flow, which persona uses it and when not to. So the check also reads the guide: every
registry row exactly once, the eight required parts in each, a known status, real persona files, and every WIRED or
SHADOW claim backed by the row id appearing in code.
Run: python3 scripts/donecheck_L6.py
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools.jev_catalogue import contract, proof  # noqa: E402

CATALOGUE = os.path.join(ROOT, "docs", "plan", "JEV-USE-CASE-CATALOGUE.md")
GUIDE = os.path.join(ROOT, "docs", "plan", "JEV-USE-CASE-GUIDE.md")
REGISTRY = os.path.join(ROOT, "data", "jev-registry.json")
PERSONAS = os.path.join(ROOT, "docs", "personas")
SEAMS = os.path.join(ROOT, "data", "jev-seams.json")
PARTS = ("Surface and flow step:", "What Jev decides:", "How to call it:", "When it fires:",
         "How it integrates:", "Who uses it:", "Do not use it when:", "Proof:")
STATUSES = ("WIRED", "SHADOW", "ANCHORED", "UNBUILT")


def check_guide():
    """(code, message): 0 GREEN, 1 RED, 3 NO-DATA."""
    try:
        with open(REGISTRY, encoding="utf-8") as fh:
            ids = [row["id"] for row in json.load(fh)]
        with open(GUIDE, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return 3, "NO-DATA: the registry or the guide cannot be read (%s)" % type(exc).__name__
    if not ids:
        return 3, "NO-DATA: the registry lists no row"
    try:
        with open(SEAMS, encoding="utf-8") as fh:
            modes = set((json.load(fh).get("modes") or {}).keys())
    except (OSError, ValueError, AttributeError) as exc:
        return 3, "NO-DATA: %s cannot be read (%s)" % (SEAMS, type(exc).__name__)
    heads = re.findall(r"(?m)^### (J\d{3}):", text)
    bad = ["%s missing" % i for i in ids if i not in heads]
    bad += ["%s appears %d times" % (i, heads.count(i)) for i in sorted(set(heads)) if heads.count(i) > 1]
    bad += ["%s is not a registry row" % i for i in sorted(set(heads) - set(ids))]
    sections = dict(re.findall(r"(?ms)^### (J\d{3}):.*?\n(.*?)(?=^### |^## |\Z)", text))
    for i in ids:
        sec = sections.get(i, "")
        if not sec:
            continue
        missing = [part for part in PARTS if part not in sec]
        if missing:
            bad.append("%s lacks %s" % (i, ", ".join(missing)))
        integ = sec.split("How it integrates:", 1)[-1].split("Who uses it:", 1)[0]
        # the LEADING status word is the row's status; prose after it may name others ("not SHADOW")
        lead = re.search(r"\b(%s)\b" % "|".join(STATUSES), integ)
        status = lead.group(1) if lead else None
        if status is None:
            bad.append("%s has no status" % i)
            continue
        # SHADOW is mechanical: the seam has a mode in data/jev-seams.json; a mode means SHADOW or WIRED
        if status == "SHADOW" and i not in modes:
            bad.append("%s claims SHADOW but data/jev-seams.json sets no mode for it" % i)
        if i in modes and status not in ("SHADOW", "WIRED"):
            bad.append("%s has a seam mode but reads %s" % (i, status))
        if status == "WIRED":
            # Python sources only, fixtures excluded: scripts/fixtures carries a copy of the whole registry, so a
            # plain grep found every row id and this check could never fail (caught by its own mutation, 2026-09-28)
            hit = subprocess.run(["git", "-C", ROOT, "grep", "-q", "-w", i, "--", "*.py", ":(exclude)**/fixtures/**"],
                                 capture_output=True)
            if hit.returncode != 0:
                bad.append("%s claims WIRED but no code names it" % i)
        users = sec.split("Who uses it:", 1)[-1].split("Do not use it when:", 1)[0]
        for persona in set(re.findall(r"([a-z0-9-]+)\.md", users)):
            if not os.path.isfile(os.path.join(PERSONAS, persona + ".md")):
                bad.append("%s names persona %s, which has no file" % (i, persona))
    if bad:
        return 1, "RED: the guide fails %d check(s): %s" % (len(bad), "; ".join(bad[:12]))
    return 0, "GREEN: the guide documents all %d registry rows with every required part" % len(ids)


def main():
    state = os.environ.get("BROTHER_OR_STATE_ROOT") or os.path.expanduser("~/.claude/brother-or-dispatch-state")
    ledger = os.path.join(state, "openrouter-ledger.jsonl")
    entries = contract.load_catalogue_md(CATALOGUE)
    if not entries:
        print("NO-DATA: no catalogue entry could be read from %s" % CATALOGUE)
        return 3
    if not os.path.isfile(ledger):
        print("NO-DATA: the dispatch ledger %s cannot be read" % ledger)
        return 3
    unproven = []
    for e in entries:
        gid = e.get("holder_id")
        if not isinstance(gid, str) or not gid:
            unproven.append("%s (no holder id)" % e.get("number"))
            continue
        ok, _why = proof.verify_proof(e, proof.find_ledger_lines(ledger, gid))
        if not ok:
            unproven.append("%s %s" % (e.get("number"), gid[:48]))
    if unproven:
        print("RED: %d of %d catalogued use cases have no RESERVE plus RECONCILE in %s: %s"
              % (len(unproven), len(entries), ledger, "; ".join(unproven[:12])))
        return 1
    print("GREEN: all %d catalogued use cases are proven by a RESERVE plus RECONCILE in %s" % (len(entries), ledger))
    code, message = check_guide()
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
