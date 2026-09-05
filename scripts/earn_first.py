"""earn_first: is a switching or positioning claim allowed to publish yet.

THE RULE THIS ENFORCES, docs/plan/SWITCHING-STRATEGY-2026-09-04.md's own
status line: "Claims marked EARN FIRST must not be published until their
proof gate is green." Section 21 spells the gate for each named competitor
(GSD, Superpowers, Compound, BMAD) as a short list of floor conditions in
prose. This tool turns that prose into a checkable gate against
docs/plan/FLOOR-2026-09-05.json, the one instrument (scripts/floor_score.py)
this estate already built to answer "is Brother behind, measured, not
asserted" for exactly this kind of claim.

THE DOCUMENTS IT WATCHES: every docs/how-to/MIGRATE-FROM-*.md guide (one per
competitor) plus docs/marketing/POSITIONING.md, whose own EARN FIRST section
carries the same four competitor claims. A document earns publication only
when EVERY gate declared in it reads MATCH (or a DOMINATE gate reads Brother
leads); one gate short of that is NOT EARNED, and a gate whose competitor
cell was never measured is NO-DATA, never a pass.

THE MACHINE-READABLE FORM, one HTML comment per gate so it renders invisibly
in the published guide:

    <!-- earn-first: capability=Release/CI, floor=gsd, require=parity -->

capability names a row on FLOOR-2026-09-05.json's own capabilities list.
floor names one of that file's competitor keys (gsd, superpowers, compound,
bmad). require is "parity" (the capability's MUST MATCH gap rule, within
0.15 of the best measured competitor) or "lead" (the DOMINATE rule, Brother
at or above the named competitor); if omitted it is read off the
capability's own role on the board. group, when given, marks an OR set: a
document needs only one gate in a shared group to read MATCH, which is how
section 21's "Brother wins proof, memory, or acceptance benchmark" (an "or",
not an "and") is represented honestly instead of being tightened into three
separate requirements the strategy never asked for.

THE TRANSLATION FROM PROSE TO CAPABILITY, done once here and named plainly
because it is a judgement call, not a measurement:
  GSD: required CI + reproducible release -> Release/CI (parity, this is the
    file's own "parity with GSD" target already); recovery/Git safety
    comparable -> Crash/resume and Worktree isolation (parity); wins proof,
    memory or acceptance -> Falsifiable verification, Active memory or
    Acceptance Compression (lead, any one, grouped).
  Superpowers: first use simple -> One-command onboarding (parity);
    tiny-task overhead comparable -> Tiny-task friction (parity); wins
    unattended assurance -> Resume days later (lead). Row S10 (the Safe
    Unwatched Time benchmark this clause actually names) is OPEN and has no
    capability of its own on this board yet, so the honest proxy is the
    nearest DOMINATE capability this board already marks as never measured
    for any competitor, which reads NO-DATA rather than inventing a pass out
    of a capability that measures something else (crash recovery within one
    round, already won, is not the same claim as unattended assurance over
    time).
  Compound: high-risk defect/review quality comparable -> Review depth
    (parity, the file's own competitive_target names "parity on high-risk
    work"); wins persistence/acceptance -> Acceptance Compression (lead).
  BMAD: requirement and decision rigor comparable -> Planning quality
    (parity); materially simpler to operate -> One-command onboarding
    (lead, since "materially" asks for more than parity).
A capability this board never named for a given clause (nothing on this
board measures "persistence" or "unattended assurance" directly) is
represented by the closest capability that exists rather than invented from
nothing; if that reads NO-DATA, the tool says so rather than guessing a
number.

WHAT THIS TOOL ADDS ON ITS OWN: if a watched document carries no
earn-first comment at all, it inserts the default gate set above, once,
right after that document's own "Floor, quoted verbatim" (a migration
guide) or "Floor, his wording:" (positioning, once per competitor section)
line, and evaluates against the freshly written text. A document that
already carries its own blocks is never rewritten; hand-authored gates always
win.

IT REPORTS, IT DOES NOT DECIDE. Exit code is always 0, this is a report:
add a document to docs/plan/EXPORT-ALLOWLIST.txt only on a human or
downstream gate reading its line as EARNED.

Python 3, standard library only. No network.
"""
import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import floor_score  # noqa: E402

NODATA = "NO-DATA"
GATE_RE = re.compile(r"<!--\s*earn-first:\s*(.*?)-->")

# ---------------------------------------------------------------------------
# The default gate sets, section 21 read as capability checks. See the
# module docstring for why each capability was picked.

GSD_GATES = [
    {"capability": "Release/CI", "floor": "gsd", "require": "parity"},
    {"capability": "Crash/resume", "floor": "gsd", "require": "parity"},
    {"capability": "Worktree isolation", "floor": "gsd", "require": "parity"},
    {"capability": "Falsifiable verification", "floor": "gsd", "require": "lead",
     "group": "gsd-wins-proof-memory-acceptance"},
    {"capability": "Active memory", "floor": "gsd", "require": "lead",
     "group": "gsd-wins-proof-memory-acceptance"},
    {"capability": "Acceptance Compression", "floor": "gsd", "require": "lead",
     "group": "gsd-wins-proof-memory-acceptance"},
]

SUPERPOWERS_GATES = [
    {"capability": "One-command onboarding", "floor": "superpowers", "require": "parity"},
    {"capability": "Tiny-task friction", "floor": "superpowers", "require": "parity"},
    {"capability": "Resume days later", "floor": "superpowers", "require": "lead"},
]

COMPOUND_GATES = [
    {"capability": "Review depth", "floor": "compound", "require": "parity"},
    {"capability": "Acceptance Compression", "floor": "compound", "require": "lead"},
]

BMAD_GATES = [
    {"capability": "Planning quality", "floor": "bmad", "require": "parity"},
    {"capability": "One-command onboarding", "floor": "bmad", "require": "lead"},
]

#: basename -> (anchor line prefix, [gate list per occurrence of that anchor,
#: in the order the anchor appears in the document]).
INSERTION_PLANS = {
    "MIGRATE-FROM-GSD.md": (
        "Floor, quoted verbatim from docs/plan/SWITCHING-STRATEGY-2026-09-04.md section 21",
        [GSD_GATES]),
    "MIGRATE-FROM-SUPERPOWERS.md": (
        "Floor, quoted verbatim from docs/plan/SWITCHING-STRATEGY-2026-09-04.md section 21",
        [SUPERPOWERS_GATES]),
    "MIGRATE-FROM-COMPOUND.md": (
        "Floor, quoted verbatim from docs/plan/SWITCHING-STRATEGY-2026-09-04.md section 21",
        [COMPOUND_GATES]),
    "MIGRATE-FROM-BMAD.md": (
        "Floor, quoted verbatim from docs/plan/SWITCHING-STRATEGY-2026-09-04.md section 21",
        [BMAD_GATES]),
    "POSITIONING.md": (
        "Floor, his wording:",
        [GSD_GATES, SUPERPOWERS_GATES, COMPOUND_GATES, BMAD_GATES]),
}


def discover_docs(root):
    """The real watched documents: every migration guide plus positioning."""
    migrate = sorted(glob.glob(
        os.path.join(root, "docs", "how-to", "MIGRATE-FROM-*.md")))
    positioning = os.path.join(root, "docs", "marketing", "POSITIONING.md")
    docs = list(migrate)
    if os.path.exists(positioning):
        docs.append(positioning)
    return docs


def gate_to_comment(gate):
    parts = ["capability=%s" % gate["capability"], "floor=%s" % gate["floor"]]
    if gate.get("require"):
        parts.append("require=%s" % gate["require"])
    if gate.get("group"):
        parts.append("group=%s" % gate["group"])
    return "<!-- earn-first: %s -->" % ", ".join(parts)


def insert_gates(content, anchor_prefix, gates_lists):
    """Insert one comment block after each occurrence of anchor_prefix.

    Raises ValueError if the number of occurrences does not match the
    number of gate lists: a structural mismatch means the document changed
    shape since this plan was written, and guessing where to insert is
    exactly the kind of assertion this tool refuses to make elsewhere.
    """
    lines = content.split("\n")
    occurrences = [i for i, line in enumerate(lines)
                   if line.startswith(anchor_prefix)]
    if len(occurrences) != len(gates_lists):
        raise ValueError(
            "found %d occurrence(s) of %r, expected %d"
            % (len(occurrences), anchor_prefix, len(gates_lists)))
    for idx, gates in sorted(zip(occurrences, gates_lists), reverse=True):
        block = [""] + [gate_to_comment(g) for g in gates]
        lines[idx + 1:idx + 1] = block
    return "\n".join(lines)


def maybe_insert_defaults(path, content):
    """Write the default gate block into path if it carries none yet."""
    if "<!-- earn-first:" in content:
        return content
    plan = INSERTION_PLANS.get(os.path.basename(path))
    if plan is None:
        return content
    anchor, gates_lists = plan
    try:
        new_content = insert_gates(content, anchor, gates_lists)
    except ValueError:
        return content
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_content)
    return new_content


def parse_gates(content):
    """([gate dict, ...], [malformed block message, ...])."""
    gates = []
    errors = []
    for match in GATE_RE.finditer(content):
        fields = {}
        for part in match.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                errors.append("unparseable earn-first field %r" % part)
                continue
            key, value = part.split("=", 1)
            fields[key.strip()] = value.strip()
        if "capability" not in fields or "floor" not in fields:
            errors.append(
                "earn-first block missing capability or floor: %r"
                % match.group(1).strip())
            continue
        gates.append(fields)
    return gates, errors


def eval_gate(gate, cap_lookup):
    """(status, brother_score_or_None, competitor_score_or_None, reason).

    status is one of MATCH, BEHIND, NO-DATA.
    """
    cap = cap_lookup.get(gate["capability"])
    if cap is None:
        return (NODATA, None, None,
                "%s is not on the floor board" % gate["capability"])
    floor_key = gate["floor"]
    cell = (cap.get("competitors") or {}).get(floor_key)
    comp_score = None if cell is None else cell.get("score")
    brother = float(cap["brother"]["score"])
    if comp_score is None:
        return (NODATA, brother, None,
                "%s never measured against %s" % (gate["capability"], floor_key))
    comp_score = float(comp_score)
    require = gate.get("require") or (
        "parity" if cap["role"] == "MUST MATCH" else "lead")
    if require not in ("parity", "lead"):
        return (NODATA, brother, comp_score,
                "%s declares require=%r, which this tool does not know"
                % (gate["capability"], require))
    if require == "parity":
        ok = (comp_score - brother) <= floor_score.FLOOR_GAP + 1e-9
    else:
        ok = brother >= comp_score - 1e-9
    return ("MATCH" if ok else "BEHIND", brother, comp_score, None)


def judge(gates, cap_lookup):
    """One verdict line's payload for a document: (status, gate, brother,
    competitor, reason). status is EARNED, NOT EARNED or NO-DATA.
    """
    groups = {}
    order = []
    for i, gate in enumerate(gates):
        key = gate.get("group") or ("__solo__%d" % i)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(gate)

    for key in order:
        first_behind = None
        first_nodata = None
        passed = False
        for gate in groups[key]:
            status, brother, comp, reason = eval_gate(gate, cap_lookup)
            if status == "MATCH":
                passed = True
                break
            if status == "BEHIND" and first_behind is None:
                first_behind = (gate, brother, comp)
            if status == NODATA and first_nodata is None:
                first_nodata = (gate, brother, comp, reason)
        if passed:
            continue
        if first_behind is not None:
            gate, brother, comp = first_behind
            return ("NOT EARNED", gate, brother, comp, None)
        gate, brother, comp, reason = first_nodata
        return (NODATA, gate, brother, comp, reason)
    return ("EARNED", None, None, None, None)


def evaluate_document(path, cap_lookup, root):
    rel = os.path.relpath(path, root)
    if not os.path.exists(path):
        return "%s: %s (document does not exist)" % (rel, NODATA)
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    content = maybe_insert_defaults(path, content)
    gates, errors = parse_gates(content)
    if errors:
        return "%s: %s (%s)" % (rel, NODATA, "; ".join(errors))
    if not gates:
        return "%s: %s (no earn-first gate declared)" % (rel, NODATA)
    status, gate, brother, comp, reason = judge(gates, cap_lookup)
    if status == "EARNED":
        return "%s: EARNED" % rel
    if status == "NOT EARNED":
        return ("%s: NOT EARNED (%s: %.2f against %s %.2f)"
                % (rel, gate["capability"], brother, gate["floor"], comp))
    return "%s: %s (%s)" % (rel, NODATA, reason)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=floor_score.SOURCE,
                     help="the floor scoring file (default: %(default)s)")
    ap.add_argument("--root", default=floor_score.ROOT)
    ap.add_argument("--doc", action="append", default=None,
                     help="override the watched document list (repeatable); "
                          "defaults to every docs/how-to/MIGRATE-FROM-*.md "
                          "guide plus docs/marketing/POSITIONING.md")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        doc, _keys = floor_score.load(args.source)
    except floor_score.Malformed as exc:
        print("%s: %s" % (NODATA, exc))
        return 0
    floor_score.resolve_evidence(doc, root=args.root)
    cap_lookup = {cap["capability"]: cap for cap in doc["capabilities"]}

    docs = args.doc if args.doc else discover_docs(args.root)
    if not docs:
        print("%s: no migration guide or positioning document found under %s"
              % (NODATA, args.root))
        return 0

    for path in docs:
        print(evaluate_document(path, cap_lookup, args.root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
