#!/usr/bin/env python3
"""What development method is already at work in this repository.

WHY THIS EXISTS. The founder's direction of 2026-08-16 is to integrate with the
methods people already use rather than compete with them: complementarity rather
than an attrition war on features. The research that preceded this file
established the constraint that shapes it, and it is not the one anyone expected:
**we cannot plug into any of them, and neither can anyone else.** Exactly one of
the five surveyed exposes a hook, and it registers that hook for itself. None
documents a plugin interface or a third party callback.

So integration here is PASSIVE READING. Each method writes files at known paths,
and this tool reads them. It calls nothing, registers nothing, and writes nothing
into anyone else's tree.

WHAT IT IS FOR. Two consumers, and it is useful to a person before either exists:

  - a session start hook, which turns this index into one short line saying what
    was found, so assurance commands stop being pointed by hand;
  - a human, who can run it directly to see what this repository already contains.

THE THREE RULES IT OBEYS, each one load bearing:

  1. **Never compete.** Where a method's plan exists, it is the plan of record.
     This tool reports it as INTENT. It does not judge it, rewrite it, or propose
     a second one.
  2. **Never be a dependency in the other direction.** A repository with none of
     these installed produces an empty index and exit 0. Nothing here degrades
     the suite's own chain, and nothing advertises an install. An absent method
     is simply absent: it is not a finding, not a warning, and not a NO-DATA line
     either, because a method nobody uses is not evidence that went missing.
  3. **Read only, and only this repository.** No writes anywhere. No walking out
     of the tree to hunt for artifacts elsewhere.

WHAT IT DELIBERATELY DOES NOT DO. It does not read file CONTENTS to summarise or
grade them. It reports that an artifact exists, where, when it changed, and which
method it belongs to. Reading a plan's contents to decide whether the plan is good
is the competing this direction rules out.

Unverified paths are marked in the table below rather than being presented as
facts, because the research separated the two and this file must not lose that
distinction.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import say  # noqa: E402  (path setup has to come first)

#: (label, kind, relative path, verified) for every artifact a method writes.
#:
#: kind is INTENT (what somebody meant to do) or EVIDENCE (a claim about what
#: happened). The distinction is the whole point: intent is consumed and yielded
#: to, evidence is quoted with attribution and never restated as our own verdict.
#:
#: verified is False where the research could not confirm the path from a source
#: it opened. A False row is still searched, because searching costs nothing and
#: finding one is informative, but its label says the path is unconfirmed so
#: nobody builds on it as though it were established.
SOURCES = (
    ("superpowers", "INTENT", os.path.join("docs", "superpowers", "specs"), True),
    ("superpowers", "INTENT", os.path.join("docs", "superpowers", "plans"), True),
    ("bmad", "INTENT", os.path.join("_bmad-output", "planning-artifacts"), True),
    ("bmad", "INTENT", os.path.join("_bmad-output", "implementation-artifacts"), True),
    ("bmad-legacy", "INTENT", os.path.join("docs", "prd.md"), False),
    ("bmad-legacy", "INTENT", os.path.join("docs", "architecture.md"), False),
    ("bmad-legacy", "INTENT", os.path.join("docs", "epics"), False),
    ("bmad-legacy", "INTENT", os.path.join("docs", "stories"), False),
    ("gsd", "INTENT", os.path.join(".planning", "PROJECT.md"), True),
    ("gsd", "INTENT", os.path.join(".planning", "REQUIREMENTS.md"), True),
    ("gsd", "INTENT", os.path.join(".planning", "ROADMAP.md"), True),
    ("gsd", "INTENT", os.path.join(".planning", "MILESTONES.md"), True),
    ("gsd", "INTENT", os.path.join(".planning", "config.json"), True),
    ("gsd", "INTENT", os.path.join(".planning", "phases"), True),
    ("gsd", "EVIDENCE", os.path.join(".planning", "STATE.md"), True),
    ("gsd", "EVIDENCE", os.path.join(".planning", "HANDOFF.json"), True),
    ("spec-kit", "INTENT", "specs", True),
    ("brothersbe", "EVIDENCE", os.path.join(".sbe", "passport.json"), True),
    ("brothersbe", "EVIDENCE", os.path.join(".sbe", "evidence"), True),
)

#: The presence detectors. Finding one means the method governs this tree even if
#: it has not written an artifact yet, which is a different and useful statement.
DETECTORS = (
    ("bmad", "_bmad"),
    ("superpowers", os.path.join("docs", "superpowers")),
    ("gsd", ".planning"),
)

#: BMAD's test module writes under a configurable root this research could NOT
#: resolve, so its three folder names are searched for anywhere in the tree rather
#: than assumed to sit in one place. Guessing the root would have been the exact
#: mistake this project has already paid for once.
TEA_DIRS = ("test_design_output", "trace_output", "test_review_output")

#: The four states BMAD's test gate emits. Recognised so the gate can be QUOTED,
#: never translated: converting somebody else's verdict into ours is laundering,
#: and dropping it is ignoring. Both are refused by the design this implements.
TEA_GATE_STATES = ("PASS", "CONCERNS", "FAIL", "WAIVED")

MAX_DEPTH = 6
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".claude"}


def stamp(path):
    """The artifact's modification time, or None when it cannot be read.

    Unreadable is reported rather than defaulted: a timestamp this tool made up
    would be indistinguishable from one it read, and a stale passport is detected
    by comparing timestamps, so an invented one could hide exactly the staleness
    the comparison exists to catch.
    """
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
    except (OSError, ValueError):
        return None


def find_tea_dirs(root):
    """Every directory under root whose name is one of the test module's three.

    Bounded by depth and by a skip list so this stays cheap on a large tree. The
    module's configured root was not verified by the research, so the folder names
    are the only thing searched for, and where they are found is reported rather
    than assumed.
    """
    hits = []
    root = os.path.abspath(root)
    for here, dirs, _files in os.walk(root):
        rel = os.path.relpath(here, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= MAX_DEPTH:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for d in list(dirs):
            if d in TEA_DIRS:
                hits.append(os.path.relpath(os.path.join(here, d), root))
    return sorted(hits)


def discover(root):
    """Every artifact found, as dicts. Empty list for a tree with no method."""
    root = os.path.abspath(root)
    rows = []

    for method, marker in DETECTORS:
        p = os.path.join(root, marker)
        if os.path.exists(p):
            rows.append({"method": method, "kind": "PRESENT", "path": marker,
                         "modified": stamp(p), "verified_path": True,
                         "note": "this method governs this tree"})

    for method, kind, rel, verified in SOURCES:
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue
        note = "" if verified else "path not confirmed by an opened source; treat as a lead"
        if os.path.isdir(p):
            try:
                n = len([e for e in os.listdir(p) if not e.startswith(".")])
            except OSError:
                n = None
            note = (note + "; " if note else "") + (
                "%d entr%s" % (n, "y" if n == 1 else "ies") if n is not None
                else "directory could not be listed")
        rows.append({"method": method, "kind": kind, "path": rel,
                     "modified": stamp(p), "verified_path": verified, "note": note})

    for rel in find_tea_dirs(root):
        note = "test module output; its root was not confirmed, so this was found by name"
        if os.path.basename(rel) == "trace_output":
            note += ("; the gate decision with states %s is emitted here, and it is QUOTED "
                     "with its path and timestamp, never restated as our own verdict"
                     % ", ".join(TEA_GATE_STATES))
        rows.append({"method": "bmad-tea", "kind": "EVIDENCE", "path": rel,
                     "modified": stamp(os.path.join(root, rel)),
                     "verified_path": False, "note": note})
    return rows


def render_text(root, rows):
    if not rows:
        say("DISCOVERY: no development method artifact found in %s. That is not a finding: "
            "a method nobody uses is not evidence that went missing, and this suite fills "
            "every node of its own chain without one." % root)
        return
    say("DISCOVERY: %d artifact(s) found in %s. Intent is yielded to, evidence is quoted "
        "with attribution, and nothing here is judged." % (len(rows), root))
    for r in rows:
        lead = "" if r["verified_path"] else " [lead]"
        say("  %-12s %-8s %-46s %s%s%s"
            % (r["method"], r["kind"], r["path"], r["modified"] or "unreadable time",
               lead, ("  " + r["note"]) if r["note"] else ""))


def render_brief(rows):
    """One short line for a session start injection, or NOTHING at all.

    Nothing is the important half. The session start hook works to a hard
    character cap and everything competing for it is law the session must not
    miss, so a line saying "no method found" would spend that budget to report an
    absence that changes nobody's next action. An absent method is absent, and
    silence is the honest rendering of it.

    Deliberately not a summary of the artifacts: it names the methods and the
    count, and points at the command. A session that needs the detail runs the
    tool; a session that does not should not pay for it in context.
    """
    if not rows:
        return ""
    methods = []
    for r in rows:
        if r["method"] not in methods:
            methods.append(r["method"])
    leads = sum(1 for r in rows if not r["verified_path"])
    tail = (", %d at a path this project has not confirmed" % leads) if leads else ""
    return ("BROTHERSBE DISCOVERY: this repository already carries %d artifact(s) from %s%s. "
            "Their plans are the plan of record and are not re-planned here; their verdicts are "
            "quoted with attribution, never restated. Full index: python3 tools/sbe_discover.py"
            % (len(rows), ", ".join(methods), tail))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="List the development method artifacts already in this repository. "
                    "Read only. An empty result is exit 0, because an absent method is "
                    "absent rather than missing.")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--json", action="store_true", help="machine readable index")
    ap.add_argument("--brief", action="store_true",
                    help="one line for a session start injection, or nothing at all")
    ns = ap.parse_args(argv)

    if not os.path.isdir(ns.root):
        say("DISCOVERY: %s is not a directory, so nothing was searched." % ns.root)
        return 2

    rows = discover(ns.root)
    if ns.json:
        say(json.dumps({"root": os.path.abspath(ns.root), "artifacts": rows},
                       sort_keys=True))
    elif ns.brief:
        line = render_brief(rows)
        if line:
            say(line)
    else:
        render_text(os.path.abspath(ns.root), rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
