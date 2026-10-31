#!/usr/bin/env python3
"""Grade the journey harness's own row data, which until now nothing did.

THE FINDING THIS CLOSES, measured on the night JT0 landed and recorded in the vault: three sweeps, 17
mutations, 5 caught, 12 survivors, every one of a single class. No registered check read a row's own
`check` program, so a row's measurement could be deleted, pointed at the repository tree instead of the
install, stripped of its refusals, or replaced by a program that always exits 0, and every unit done
check stayed green. A grader nobody runs is not a grader, and row data nobody grades is not evidence.

THE SECOND FAILURE IT CATCHES is the one that cost real time the day this was written. S8's and S9's
harnesses were absent and the plan said so in the field itself, naming a unit that does not belong to
this plan. HX119 closed without them, and nothing noticed, because the checks that hold harnesses to
their bar scan loop lane units only. The same shape is available to every NO-DATA row here: each one
names the unit that will bring its measurement, and nothing made sure that unit had not already closed
and left the row behind. G8 below is that check.

WHERE THE RULES COME FROM. Not invented. Every rule below is read off the three rows that actually
measure something (J2 A.adopt-exits-0, J2 C.no-pass-for-unrun-check, J5 A.setup-vault-is-recall-vault):
an argv of strings, a body that finds its tool by walking the installed config, a body that refuses by
name on an unreadable precondition, and a body whose last act is a decided exit. A rule this file could
not point at a real row for would be this file inventing a contract rather than holding one.

THE EXEMPTIONS LIVE HERE, IN THE CHECKER, NEVER IN THE DATA. That is the estate's own pattern, stated in
run_journey's own GUARDS comment: "test_plan_119's GUARDS sits in the CHECKER, never in the harness files
it judges, so the thing being judged cannot write its own exemption". A row that could exempt itself in
journeys.json would be exempting itself from the only thing grading it.

Run it: python3 -B scripts/test_journey_rows.py
"""
import json
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JOURNEYS = os.path.join(HERE, "journey/journeys.json")
PLAN = os.path.join(ROOT, "docs/plan/BROTHER-1.1.9-WBS.json")
JOURNEY_IDS = ("J1", "J2", "J4", "J5")
#: the only three verdicts, so row_disposition answers in the estate's own words rather than in
#: booleans. NO-DATA is never a pass and never a hard block, which is the whole point of this
#: file no longer refusing a release over a row whose unit the plan deliberately slipped.
NODATA, FAIL = "NO-DATA", "FAIL"

#: the leading unit id of a measured_by sentence: "FRUN1.a, which ..." or "PROOF1. A unit whose ..."
UNIT_AT_START = re.compile(r"^([A-Z][A-Z0-9]*\d+[A-Z0-9]*)")
#: a body that reaches for a path inside this repository rather than inside the install
TREE_PATH = re.compile(r"""['"](?:products|scripts|bundle|docs)/""")
#: how all three real bodies find their tool: by walking the installed config
INSTALL_MARKER = "CLAUDE_CONFIG_DIR"
#: THE PLAN IS NOT IN AN EXPORT TREE, which is why one test below is skipped there rather than failing.
#: scripts/pre_push_gate.py runs every changed scripts/ test on an export shaped tree with an empty HOME
#: (hermetic_test_check.py), and an exported tree carries no docs/plan WBS. Measured 2026-10-28: this
#: file passed 9 of 9 in the checkout and in the checkout under an empty HOME, and REFUSED the push with
#: "FAILS on the export tree with an empty HOME ... failing: test_every_unmeasured_row_names_a_unit_still_open".
#: scripts/test_plan_119.py already had this exact shape and its own wording is mirrored here, so the two
#: cannot disagree about what an absent plan means. The skip covers ABSENT only: the assertIsNotNone inside
#: the test still fails on a plan that is PRESENT and unreadable, which is a real defect and not a tree shape.
IN_HUB = os.path.isfile(PLAN)
HUB_ONLY = ("NO-DATA: the plan is not in this tree (an exported tree carries no docs/plan WBS); "
            "this test runs in the hub")
#: how a program refuses rather than passing when a precondition cannot be read. TWO WORDS, because the
#: data uses two: the three inline bodies say REFUSED, and the seven script programs added later say
#: NO-DATA, which is the estate's own verdict word for a measurement that did not happen. A rule naming
#: only the first was read off the three rows that existed when it was written, and it called seven
#: honest programs refusal-less.
REFUSAL_MARKERS = ("REFUSED", "NO-DATA")
#: a script that computes its exit rather than naming one: sys.exit(main()) and raise SystemExit(code)
COMPUTED_EXIT = re.compile(r"(?:sys\.exit|raise\s+SystemExit)\(\s*(?!\s*[01]\s*\)|\s*\))", re.M)
#: a zero verdict path and a non zero one, the shape the seven script programs use: return 0, / return 2,
ZERO_PATH = re.compile(r"\breturn\s+0\b", re.M)
NONZERO_PATH = re.compile(r"\breturn\s+[1-9]\d*\b", re.M)
#: how all three real bodies end: an exit decided by what they measured, never a bare exit(0). The two
#: spellings are both in the data, which is why this is a pattern and not a substring: A.adopt-exits-0
#: and A.setup-vault-is-recall-vault end on "sys.exit(0 if ... else 1)", C.no-pass-for-unrun-check ends
#: on "sys.exit(1 if bad else 0)". A first draft of this rule was read off two of the three and went red
#: on the third, which is the rule being wrong rather than the data.
DECIDED_EXIT = re.compile(r"sys\.exit\(\s*([01])\s+if\s+(.+?)\s+else\s+([01])\s*\)", re.S)
#: a condition that decides nothing: the exit is spelled like a decision and is really a constant
CONSTANT_CONDITION = re.compile(r"^(?:True|False|\d+|'[^']*'|\"[^\"]*\")$")

#: ROWS ALLOWED TO READ THIS TREE, each with the measurement that says why. One entry, and it is a
#: finding rather than a blessing: J5's row runs products/brothermode/scripts/setup.py and
#: products/brothermode/tools/bm_vault.py as repository paths, so it measures the maintainer's copy and
#: not the stranger's install. It reads FAIL today on a real defect (setup writes one file, recall reads
#: another), so pointing it at the install would change what is being measured while that defect is
#: open. FOOT1H owns the fix; when it lands, this entry comes out and the row names the install like its
#: two siblings. Recorded here rather than left silent, because an exemption nobody wrote down is
#: indistinguishable from a rule nobody enforced.
TREE_OK = {
    ("J5", "A.setup-vault-is-recall-vault"):
        "runs setup.py and bm_vault.py from this tree, not the install; the split it measures is open "
        "and owned by FOOT1H, and repointing it would change the measurement while that is true",
}


def journeys(path=JOURNEYS):
    """The whole row set as {journey: [row, ...]}, or None when it cannot be read. Never an empty dict:
    a row set that cannot be read has measured nothing, which is NO-DATA and not an empty pass."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(doc, dict):
        return None
    out = {}
    for jid in JOURNEY_IDS:
        entry = doc.get(jid)
        if isinstance(entry, dict):
            entry = entry.get("rows")
        if not isinstance(entry, list):
            return None
        out[jid] = entry
    return out


def plan_states(path=PLAN):
    """{unit id: state} from the plan, or None when the plan cannot be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    units = doc.get("units") if isinstance(doc, dict) else None
    if not isinstance(units, list):
        return None
    return dict((u.get("id"), u.get("state")) for u in units if isinstance(u, dict))


def code_only(body):
    """The program with comments and docstrings removed, so a rule about what the code DOES is not
    answered by what the prose MENTIONS.

    Measured 2026-10-30: the tree-path rule fired on check_hook_cost.py because its helper docstring
    opens with the words scripts/hook_latency.py and the regex saw a quote followed by that word. The
    program itself joins ROOT with "scripts" and "hook_latency.py" as separate parts, which is a
    borrowed parsing helper, not the thing measured: the subject stays the installed hook table. A
    rule that cannot tell prose from code was going to keep finding that."""
    if not isinstance(body, str):
        return ""
    marks = ('"""', "'''")
    out, i, n = [], 0, len(body)
    while i < n:
        if body[i] == "#":
            while i < n and body[i] != "\n":
                i += 1
            continue
        if body.startswith(marks, i):
            quote = body[i:i + 3]
            end = body.find(quote, i + 3)
            i = n if end == -1 else end + 3
            continue
        out.append(body[i])
        i += 1
    return "".join(out)


def program_of(check, root=ROOT):
    """(program text, why it could not be read) for a row's argv. Exactly one of the two is non empty.

    THREE SHAPES ARE IN THE DATA and reading only one was this file's own worst defect, caught on
    2026-10-30 when seven of ten rows turned out to be unjudged:
      ['python3','-B','-c','<program>']                     the program follows -c
      ['python3','-B','scripts/journey/check_x.py']         the program is the file named
      ['python3','-B','scripts/journey/check_x.py','--row','accept']
                                                            same, with arguments AFTER the path
    The first version read check[-1]. For the second shape that is a PATH, which carries no decided
    exit, no refusal and no install marker, so the first script row failed three rules for the wrong
    reason and the six behind it were never reached. For the third shape check[-1] is "accept", six
    characters, which looked like an almost empty program and was really an argument. So the program is
    found by looking for it, never by position."""
    if not isinstance(check, (list, tuple)) or not check:
        return "", "check is not a runnable argv"
    parts = [c for c in check if isinstance(c, str)]
    if len(parts) != len(check):
        return "", "check carries a part that is not a string"
    if "-c" in parts:
        where = parts.index("-c")
        if where + 1 >= len(parts):
            return "", "check ends at -c with no program after it"
        return parts[where + 1], ""
    for part in parts:
        if not part.endswith(".py"):
            continue
        path = part if os.path.isabs(part) else os.path.join(root, part)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    return fh.read(), ""
            except OSError as exc:
                return "", "check names %s, which could not be read: %s" % (part, exc)
        return "", "check names %s, which is not a file in this tree" % part
    return "", "check names no program: neither -c nor a script in this tree"


def row_disposition(row, states, slipped, journey="J?"):
    """(verdict, line) for ONE unmeasured row: FAIL with the problem, NO-DATA with the plan's recorded
    disposition, or ('', '') when the row is fine.

    EXTRACTED so a fixture can judge it without the real plan. While this logic lived inline in the
    test, the only thing exercising it was the tree in front of it, which proved the rule for exactly
    the five rows orphaned today and for no other shape. A rule that has only ever met its own tree is
    not a tested rule."""
    rid = row.get("id") if isinstance(row, dict) else None
    where = "%s %s" % (journey, rid)
    uid = unit_named_by(row.get("measured_by") if isinstance(row, dict) else None)
    if not uid:
        return FAIL, "%s names no unit this file can resolve" % where
    if uid not in states:
        if uid in slipped:
            moved, reason = slipped[uid]
            return NODATA, ("NO-DATA %s: unmeasurable in this scope, %s is %s. Reason: %s"
                            % (where, uid, moved, reason))
        return FAIL, ("%s is waiting on %s, which is neither a unit of this plan nor recorded with a "
                      "reason in its slip record, so nothing will ever bring its measurement and "
                      "nothing says why" % (where, uid))
    if states[uid] == "DONE":
        return FAIL, ("%s is still NO-DATA while %s reads DONE, so that unit closed and left its row "
                      "behind" % (where, uid))
    return "", ""


def slipped_units(path=PLAN):
    """{unit id: (disposition, reason)} read from the PLAN's own slip record, or None when it cannot be
    read. Never a hand list in this file.

    WHY THE PLAN AND NOT A LIST HERE. The first version carried five rows and their reasons typed into
    this checker. That is a list somebody must remember to update, it goes stale the moment the scope
    moves again, and it says nothing about who decided. The plan already records all of it: slipped.to
    names the release the work moved to, slipped.decision names the record and the date, and
    slipped.units[id].reason carries the reason verbatim. So a row reports the disposition the plan
    actually took, quoted, and a unit that comes BACK into the plan is measurable again with no edit
    here at all.

    IT IS NOT SELF EXEMPTION, which the rule about exemptions living in the checker exists to prevent.
    The thing judged here is the row data in journeys.json; the plan is a different artifact that the
    row data cannot write, and it is the authority that actually decided the scope. Excusing a row by
    editing the plan means passing the plan's own generator and its checks, which is far harder to do
    quietly than editing a list in this file would have been.

    A SLIP WITH NO RECORDED REASON EXCUSES NOTHING: such a unit is left out of the map, so its rows
    stay red under G8."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    record = doc.get("slipped") if isinstance(doc, dict) else None
    if not isinstance(record, dict):
        return {}
    units = record.get("units")
    if not isinstance(units, dict):
        return {}
    where, decided = record.get("to"), record.get("decision")
    out = {}
    for uid, entry in units.items():
        if not isinstance(uid, str) or not isinstance(entry, dict):
            continue
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            continue
        moved = "slipped to %s" % where if isinstance(where, str) and where.strip() else "slipped"
        if isinstance(decided, str) and decided.strip():
            moved += ", decided by %s" % decided.strip()
        out[uid] = (moved, reason.strip())
    return out


def body_of(check, root=ROOT):
    """Just the program text, for callers that have already established it is there."""
    return program_of(check, root)[0]


def scan_problem(check, root=ROOT):
    """Why no rule can judge this row's program, or '' when one can.

    THE GAP THIS CLOSES, named in my own report before it was fixed: program_of answers WHY it could
    not find a program, and only argv_problem read that answer. So when resolution failed, G5, G6 and
    G7 went on scanning whatever text came back, which is how a path got judged as a program twice.
    Every rule that reads a body now asks this first, so an unidentifiable program is a refusal with a
    reason rather than a scan of the wrong string."""
    body, why = program_of(check, root)
    if why:
        return why
    if not body.strip():
        return "the program resolved to nothing, so there is no text to judge"
    return ""


def decided_exit_problem(body):
    """Why this program cannot report both a pass and a failure, or '' when it can.

    TWO CONVENTIONS, BOTH REAL, and a rule naming only the first called seven honest programs broken.
    The three inline bodies decide in the exit itself, sys.exit(0 if r.returncode==0 else 1). The seven
    script programs do it properly instead: a measuring function returns one of 0 PASS, 1 FAIL or 2
    NO-DATA with its reason, main() reduces those to a code, and the file ends sys.exit(main()). That is
    a better shape than the one this rule was written against, so the rule follows the data.

    What is still refused either way: a program with no exit but a literal one (sys.exit(0)), a program
    with no non zero path at all, and a decision that turns on a constant. Those are the shapes a
    mutation reaches for when it wants a row that cannot fail."""
    if not isinstance(body, str):
        return "the body is %s, not program text" % type(body).__name__
    for low, condition, high in DECIDED_EXIT.findall(body):
        if low != high and not CONSTANT_CONDITION.match(condition.strip()):
            return ""                     # the inline shape, decided in the exit
    computed = COMPUTED_EXIT.search(body)
    zero, nonzero = ZERO_PATH.search(body), NONZERO_PATH.search(body)
    if computed and zero and nonzero:
        return ""                         # the script shape, decided before the exit
    if not computed and not DECIDED_EXIT.search(body):
        return "every exit names a literal code, so nothing it measured can change the verdict"
    if not nonzero:
        return "no path reaches a non zero verdict, so this program cannot report a failure"
    if not zero:
        return "no path reaches a zero verdict, so this program cannot report a pass"
    return "the only decided exit reaches one verdict, or turns on a constant"


def argv_problem(check):
    """Why this argv is not a runnable check, or '' when it is one."""
    if not isinstance(check, (list, tuple)):
        return "check is %s, not an argv list" % type(check).__name__
    if not check:
        return "check is an empty argv, so it runs nothing"
    for part in check:
        if not isinstance(part, str):
            return "check carries %r, which is not a string" % (part,)
    last = check[-1]
    if isinstance(last, str) and last.endswith(".py") and "\n" not in last:
        where = last if os.path.isabs(last) else os.path.join(ROOT, last)
        if not os.path.isfile(where):
            return "check names %s, which is not a file in this tree" % last
    if not body_of(check).strip():
        return "check carries no program text"
    return ""


def unit_named_by(measured_by):
    """The unit id a NO-DATA row points at, or '' when it points at nothing nameable."""
    if not isinstance(measured_by, str):
        return ""
    found = UNIT_AT_START.match(measured_by.strip())
    if not found:
        return ""
    return found.group(1).split(".", 1)[0]


class RowData(unittest.TestCase):
    """One case per rule, each able to refuse on its own. The subject is the real row set."""

    def setUp(self):
        self.rows = journeys()
        if self.rows is None:
            self.fail("NO-DATA: %s could not be read as a row set, so nothing here can grade it"
                      % os.path.relpath(JOURNEYS, ROOT))

    def measuring(self):
        for jid, rows in sorted(self.rows.items()):
            for row in rows:
                if isinstance(row, dict) and row.get("check"):
                    yield jid, row

    # G1
    def test_every_row_id_is_unique_in_its_journey(self):
        for jid, rows in sorted(self.rows.items()):
            seen = [r.get("id") for r in rows if isinstance(r, dict)]
            self.assertEqual(len(seen), len(set(seen)),
                             "%s repeats a row id, so one row's verdict hides another's: %s"
                             % (jid, sorted(i for i in set(seen) if seen.count(i) > 1)))
            for one in seen:
                self.assertTrue(isinstance(one, str) and one.strip(),
                                "%s carries a row with no id" % jid)

    # G2
    def test_a_row_either_measures_or_says_which_unit_will(self):
        for jid, rows in sorted(self.rows.items()):
            for row in rows:
                self.assertIsInstance(row, dict, "%s carries a row that is not an object" % jid)
                if row.get("check"):
                    continue
                said = row.get("measured_by")
                self.assertTrue(isinstance(said, str) and said.strip(),
                                "%s %s declares no check and names no unit that will bring one, so it "
                                "is NO-DATA with no route to a measurement"
                                % (jid, row.get("id")))

    # G3, the contradiction the walker already refuses: the data must not contain it either.
    def test_no_row_both_measures_and_is_exempt(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "run_journey_for_guards", os.path.join(HERE, "journey/run_journey.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        guards = getattr(module, "GUARDS", None)
        self.assertIsInstance(guards, dict, "run_journey declares no GUARDS map to read")
        for jid, row in self.measuring():
            self.assertNotIn((jid, row.get("id")), guards,
                             "%s %s declares a check AND an exemption, which the walker reads as a "
                             "contradiction, so the row can never read PASS" % (jid, row.get("id")))

    # G4
    def test_every_declared_check_is_a_runnable_argv(self):
        bad = []
        for jid, row in self.measuring():
            problem = argv_problem(row.get("check"))
            if problem:
                bad.append("%s %s: %s" % (jid, row.get("id"), problem))
        self.assertEqual([], bad, "\n".join(bad))

    # G5, the rule that stops a row becoming a program that always exits 0.
    def test_every_declared_check_ends_on_what_it_measured(self):
        bad = []
        for jid, row in self.measuring():
            unreadable = scan_problem(row.get("check"))
            if unreadable:
                bad.append("%s %s: %s" % (jid, row.get("id"), unreadable))
                continue
            problem = decided_exit_problem(body_of(row.get("check")))
            if problem:
                bad.append("%s %s: %s" % (jid, row.get("id"), problem))
        self.assertEqual([], bad, "\n".join(bad))

    # G6, the rule that stops a row reading an unreadable precondition as a pass.
    def test_every_declared_check_refuses_rather_than_passing(self):
        bad = []
        for jid, row in self.measuring():
            unreadable = scan_problem(row.get("check"))
            if unreadable:
                bad.append("%s %s: %s" % (jid, row.get("id"), unreadable))
                continue
            body = body_of(row.get("check"))
            if not any(word in body for word in REFUSAL_MARKERS):
                bad.append("%s %s refuses nothing by name (%s), so a missing precondition reads as a "
                           "result" % (jid, row.get("id"), " or ".join(REFUSAL_MARKERS)))
        self.assertEqual([], bad, "\n".join(bad))

    # G7, the trap the walker exists to avoid: the repository tree is not the product.
    def test_a_check_reads_the_install_and_not_this_tree(self):
        bad = []
        for jid, row in self.measuring():
            unreadable = scan_problem(row.get("check"))
            if unreadable:
                bad.append("%s %s: %s" % (jid, row.get("id"), unreadable))
                continue
            body = body_of(row.get("check"))
            key = (jid, row.get("id"))
            if key in TREE_OK:
                if not TREE_OK[key].strip():
                    bad.append("%s %s is exempt with no recorded reason" % key)
                continue
            # BOTH HALVES, and dropping the first was a regression this file's own sweep caught on
            # 2026-10-30: with only the marker test, pointing a check at this tree still passed,
            # because the marker survives inside the REFUSED message even when the code has stopped
            # reading the variable. A mention is not a read.
            reaches = TREE_PATH.search(code_only(body))
            if reaches:
                bad.append("%s %s reaches into this tree at %r, so it measures the maintainer's copy "
                           "and not the stranger's install" % (key + (reaches.group(0),)))
            if INSTALL_MARKER not in body:
                bad.append("%s %s never reads the installed config, so what it walked is unknown" % key)
        self.assertEqual([], bad, "\n".join(bad))

    # G8, the gap that cost a day: a unit can close and leave the row that names it behind.
    @unittest.skipUnless(IN_HUB, HUB_ONLY)
    def test_every_unmeasured_row_names_a_unit_still_open(self):
        states = plan_states()
        self.assertIsNotNone(states, "NO-DATA: the plan could not be read, so no unit state is known")
        bad, nodata = [], []
        slipped = slipped_units()
        self.assertIsNotNone(slipped, "NO-DATA: the plan's slip record could not be read, so no row's "
                                      "disposition is known and no row may read NO-DATA")
        for jid, rows in sorted(self.rows.items()):
            for row in rows:
                if not isinstance(row, dict) or row.get("check"):
                    continue
                verdict, line = row_disposition(row, states, slipped, jid)
                if verdict == FAIL:
                    bad.append(line)
                elif verdict == NODATA:
                    nodata.append(line)
        for line in nodata:
            print(line)                   # named, never swallowed: NO-DATA is not a silent pass
        self.assertEqual([], bad, "\n".join(bad))

    # G8's rule on fixtures, so every branch is proven and not only the five rows orphaned today.
    def test_fixture_an_unknown_unit_fails_and_a_slipped_one_reads_no_data(self):
        states = {"HD1": "OPEN", "PROOF1": "DONE"}
        slipped = {"FRUN1": ("slipped to 1.1.10", "the first screen work, the single largest item")}
        cases = [
            ({"id": "r", "measured_by": "HD1 brings it"}, "", "a live open unit is fine"),
            ({"id": "r", "measured_by": "PROOF1 brings it"}, FAIL, "a DONE unit left its row behind"),
            ({"id": "r", "measured_by": "FRUN1 brings it"}, NODATA, "a slipped unit is unmeasurable"),
            ({"id": "r", "measured_by": "FRUN1.a brings it"}, NODATA, "a sub unit resolves to its unit"),
            ({"id": "r", "measured_by": "NOSUCH9 brings it"}, FAIL, "an unknown unit still fails"),
            ({"id": "r", "measured_by": "a sentence naming nothing"}, FAIL, "no resolvable unit fails"),
            ({"id": "r", "measured_by": ""}, FAIL, "an empty pointer fails"),
            ({"id": "r"}, FAIL, "a missing pointer fails"),
        ]
        for row, want, why in cases:
            got, line = row_disposition(row, states, slipped)
            self.assertEqual(want, got, "%s: measured_by=%r answered %r (%s)"
                             % (why, row.get("measured_by"), got, line))
        got, line = row_disposition({"id": "r", "measured_by": "FRUN1 brings it"}, states, {})
        self.assertEqual(FAIL, got, "a unit absent from both the plan and the slip record must fail")

    # G8's keeper. Staleness takes care of itself now: a unit back in the plan is simply measurable
    # again, with nothing here to update. What still needs saying is that the two halves of the plan
    # cannot disagree, because a unit that is BOTH live and recorded as slipped would let a row read
    # NO-DATA against a unit sitting right there in the plan.
    @unittest.skipUnless(IN_HUB, HUB_ONLY)
    def test_the_plan_does_not_both_hold_and_slip_a_unit(self):
        states, slipped = plan_states(), slipped_units()
        self.assertIsNotNone(states, "NO-DATA: the plan could not be read")
        self.assertIsNotNone(slipped, "NO-DATA: the plan's slip record could not be read")
        both = sorted(set(states) & set(slipped))
        self.assertEqual([], both,
                         "the plan holds these as live units and also records them slipped out of the "
                         "release, so a row waiting on one would read NO-DATA against a unit that is "
                         "right there: %s" % ", ".join(both))

    # G9
    def test_every_exemption_names_a_row_that_exists_and_says_why(self):
        ids = set()
        for jid, rows in sorted(self.rows.items()):
            for row in rows:
                if isinstance(row, dict):
                    ids.add((jid, row.get("id")))
        for key, why in sorted(TREE_OK.items()):
            self.assertIn(key, ids, "%s %s is exempt but is not a row of this set" % key)
            self.assertTrue(isinstance(why, str) and why.strip(),
                            "%s %s is exempt with no recorded reason" % key)


if __name__ == "__main__":
    unittest.main(verbosity=1)
