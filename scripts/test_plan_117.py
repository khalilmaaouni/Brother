#!/usr/bin/env python3
"""The 1.1.7 WBS is the generator's output, never a hand edit, and it lints to the record it carries.

  python3 -B scripts/test_plan_117.py

Four facts, each judged at its entry point:
  check_run         python3 docs/plan/specs/1.1.7/plan_v117.py --check exits 0 on the committed tree: the generator
                    reproduces docs/plan/BROTHER-1.1.7-WBS.json and every seed spec byte for byte. A copy of the plan
                    files with one hand edit, or a unit file whose acceptance is a chain, exits non zero (red fixtures).
  blocking_problems plan_lint.lint_plan over the real WBS: its BLOCKING and NO-DATA findings equal the plan's own
                    lint_blocking_open list exactly, so a new finding or a cleared one is refused.
  id_problems       the WBS units are UNITS-1.1.7.json's units, same ids in the same order, each with its acceptance as
                    the done check, its lane, its council depends_on kept, loop_gradable for the loop lane only, and a
                    loop_scope naming exactly the loop and kernel lanes.
  scope_problems    the WBS frozen_loop_scope is recomputed here from the units alone (lane, kept, held_for_cut,
                    depends_on plus after): the loop units the loop can land, the kept_as rows out and DONE with
                    evidence, the loop units behind a kernel unit (they wait on its landing through the deploy step),
                    the loop units behind a unit held for the cut y and the share they bound; a unit that does not say
                    whether it is held for the cut is NO-DATA; the figures of COUNCIL D2 as worded (kernel_blocked,
                    max_share_before_kernel_landing) are refused since the owner ruled STABILIZE.md OPEN ITEMS 1 (A,
                    2026-10-18); a field unit never shares LC10's done check.
  cut_problems      the WBS carries the cut council of 2026-10-18 evening (COUNCIL-1.1.7.json cut_council, CUT-SPEED.md
                    CUT IN MINUTES): CS1 to CS13 present, CS12 on the loop lane inside the frozen scope, every CS done
                    check one python3 -B scripts/test_*.py command, CS11 behind every other CS unit, CF1 behind CS11,
                    CS12, LC10 and A2, CS4 behind A2 (its gate rows reuse A2's ledger), I26 owning
                    scripts/release_body.py, and version_of_plan naming the cut council; since the owner ruled
                    STABILIZE.md OPEN ITEMS 4 (option B, 2026-10-19) also CS14, the per landing mutation unit, on the
                    loop lane inside the frozen scope behind A2 and CS1, with CS11 and CF1 behind it, item 4 under
                    ruled_items with his words and no longer among the open items or the decisions waiting.
Where docs/plan/specs is not shipped (the export tree) every real file case skips by name. Fixtures live in temporary
directories only; HOME is never read. Python 3.9, standard library only.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))
WBS = "docs/plan/BROTHER-1.1.7-WBS.json"
UNITS = "docs/plan/seeds/1.1.7/UNITS-1.1.7.json"
GEN = "docs/plan/specs/1.1.7/plan_v117.py"
SPEC_DIR = "docs/plan/specs/1.1.7"
HUB_ONLY = os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs"))   # test_plan_116's rule: the export ships no specs
LOOP_LANES = ("loop", "kernel")
#: The owner's ruling on STABILIZE.md OPEN ITEMS 1, verbatim (2026-10-18, the question UI): a kernel unit lands through the
#: deploy step and only the stamp waits for his approval batched with the cut y, so no unit's landing waits on the cut.
RULING_1 = "Merge now, switch at cut (Recommended)"
#: The owner's ruling on STABILIZE.md OPEN ITEMS 4, verbatim (2026-10-19 after midnight JST, the question UI): option B,
#: the mutation rows run as units land (CS14), so the first rehearsal of a candidate runs warm.
RULING_4 = "Yes, per landing (Recommended)"
#: The owner's ruling on the reviewed route (STABILIZE.md OPEN ITEMS 5, 2026-10-19), his words verbatim.
#: The owner's ruling on OPEN ITEMS 6 (2026-10-19), his words with the one time code removed.
RULING_6 = "Here is the code activate now"
RULING_7 = "Finish the rest by hand and make sure the loop works fully next time"
RULING_8 = "Cut core now (Recommended)"
CORE_8 = ("CS8", "CS12", "LC6", "I11")
RULING_9 = "1.1.7 has to be the version where the loop starts to work perfectly I am tired of this"
CORE_9 = CORE_8 + ("I3", "I4", "I8", "I22", "I33", "LC1", "LC2", "LC3", "PP1", "LR1")
OWNER_WORDS_10 = ('Figure out the blockers and remove them yourself', 'Hold for full proof (Recommended)', 'I shut down most of it just go', 'Start the work and then do a handover as you do not have enough tokens')
RULING_10 = 'Hold for full proof: the loop lands all 15 proof sub units, then one unattended run with zero unexplained HELDs, then the cut.'
PROOF_9 = ("I3", "I4", "I8", "I22", "I33", "LC1", "LC2", "LC3", "PP1")
CORE_10 = CORE_9
RULING_5 = ("What needs to be landed by hand should be landed by hand the rest would then become 100% and we judge "
            "against it")
SUPERSEDED = ("kernel_blocked", "max_share_before_kernel_landing")
#: The cut council of 2026-10-18 evening (K1 to K8): the units it rewrote, added or re-wired, as cut_problems reads them.
CUT_UNITS = ["CS%d" % i for i in range(1, 15)]
CUT_CHECK = re.compile(r"^python3 -B scripts/test_\w+\.py$")
CUT_EDGES = {"CF1": ("CS11", "CS12", "CS14", "LC10", "A2"), "CS11": tuple(u for u in CUT_UNITS if u != "CS11"),
             "CS4": ("A2",), "CS14": ("A2", "CS1")}
LOOP_CUT_UNITS = ("CS12", "CS14")   # each a loop lane unit inside the frozen scope


def _json(path):
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("units"), list) else None


def check_run(cwd, gen=None):
    """(exit code, output) of the generator's --check run with `cwd` as the plan root."""
    r = subprocess.run([sys.executable, "-B", gen or os.path.join(ROOT, GEN), "--check"], cwd=cwd,
                       capture_output=True, text=True, timeout=300)
    return r.returncode, (r.stdout + r.stderr).strip()


def blocking_problems(findings, expected):
    """Every difference between the lint's BLOCKING and NO-DATA findings and the plan's lint_blocking_open list."""
    if not isinstance(findings, list) or not isinstance(expected, list):
        return ["NO-DATA: the findings or the expected list is not a list"]
    seen = []
    for f in findings:
        if not isinstance(f, dict):
            return ["NO-DATA: a finding is not a record"]
        if f.get("severity") in ("BLOCKING", "NO-DATA"):
            seen.append(f)
    problems = []
    for f in seen:
        if f["severity"] == "NO-DATA" or not any(
                isinstance(e, dict) and e.get("unit") == f.get("unit") and e.get("rule") == f.get("rule")
                and e.get("path", "") in (f.get("text") or "") for e in expected):
            problems.append("an unrecorded %s finding: %s %s %s" % (f["severity"], f.get("unit"), f.get("rule"), f.get("text")))
    for e in expected:
        if not isinstance(e, dict) or not e.get("why"):
            problems.append("a lint_blocking_open entry without its reason: %r" % (e,))
            continue
        if not any(f.get("unit") == e.get("unit") and f.get("rule") == e.get("rule")
                   and e.get("path", "") in (f.get("text") or "") for f in seen):
            problems.append("lint_blocking_open names %s %s %s, which the lint no longer reports: drop it from the "
                            "generator" % (e.get("unit"), e.get("rule"), e.get("path")))
    return problems


def id_problems(wbs, units):
    """Every way the WBS units are not the unit file's units."""
    if wbs is None or units is None:
        return ["NO-DATA: the WBS or the unit file cannot be read"]
    problems = []
    w_ids, u_ids = [x.get("id") for x in wbs["units"]], [x.get("id") for x in units["units"]]
    if w_ids != u_ids:
        problems.append("the WBS ids differ from the unit file: %s" % (sorted(set(w_ids) ^ set(u_ids)) or "same ids, other order"))
    src = {x.get("id"): x for x in units["units"]}
    for w in wbs["units"]:
        u = src.get(w.get("id"))
        if u is None:
            continue
        if w.get("done_check") != u.get("acceptance"):
            problems.append("%s: done_check %r is not the acceptance %r" % (w["id"], w.get("done_check"), u.get("acceptance")))
        if w.get("lane") != u.get("lane"):
            problems.append("%s: lane %r is not %r" % (w["id"], w.get("lane"), u.get("lane")))
        if not set(u.get("depends_on") or []) <= set(w.get("depends_on") or []):
            problems.append("%s: a council depends_on edge is missing" % w["id"])
        if w.get("after") != w.get("depends_on"):
            problems.append("%s: after and depends_on differ" % w["id"])
        if w.get("loop_gradable") is not (u.get("lane") == "loop"):
            problems.append("%s: loop_gradable %r for lane %r" % (w["id"], w.get("loop_gradable"), u.get("lane")))
        hosts = w.get("both_hosts")
        if not isinstance(hosts, dict) or not hosts.get("claude_code") or not hosts.get("codex"):
            problems.append("%s: both_hosts lacks the claude_code or codex proof" % w["id"])
        if w.get("state") != "OPEN" and not w.get("evidence"):
            problems.append("%s: state %r with no evidence" % (w["id"], w.get("state")))
    scope = wbs.get("loop_scope")
    try:
        rx = re.compile(scope)
    except (TypeError, re.error):
        return problems + ["loop_scope %r is not a regular expression" % (scope,)]
    inside = [x["id"] for x in units["units"] if rx.search(x["id"])]
    want = [x["id"] for x in units["units"] if x.get("lane") in LOOP_LANES]
    if inside != want:
        problems.append("loop_scope admits %s, not the loop and kernel lanes" % sorted(set(inside) ^ set(want)))
    return problems


def _closure(deps, uid):
    seen, todo = set(), list(deps.get(uid, ()))
    while todo:
        x = todo.pop()
        if x not in seen:
            seen.add(x)
            todo.extend(deps.get(x, ()))
    return seen


def scope_problems(wbs):
    """Every way the WBS frozen_loop_scope is not what its own units say (the review of 2026-10-18, findings 1 and 2):
    recomputed here over depends_on plus after, with no help from the generator."""
    if not isinstance(wbs, dict) or not isinstance(wbs.get("units"), list):
        return ["NO-DATA: the WBS cannot be read"]
    scope = wbs.get("frozen_loop_scope")
    if not isinstance(scope, dict):
        return ["NO-DATA: the WBS carries no frozen_loop_scope record"]
    units = [u for u in wbs["units"] if isinstance(u, dict)]
    deps = {u.get("id"): list(dict.fromkeys((u.get("depends_on") or []) + (u.get("after") or []))) for u in units}
    kernel = {u["id"] for u in units if u.get("lane") == "kernel"}
    kept = [u["id"] for u in units if u.get("kept")]
    loop = [u["id"] for u in units if u.get("lane") == "loop" and not u.get("kept")]
    problems = []
    held = set()
    for u in units:
        if not isinstance(u.get("held_for_cut"), bool):
            problems.append("%s: NO-DATA, held_for_cut is not true or false (does its landing wait on the cut y?)" % u.get("id"))
        elif u["held_for_cut"]:
            held.add(u["id"])
    behind = sorted(x for x in loop if _closure(deps, x) & kernel)
    blocked = sorted(x for x in loop if ({x} | _closure(deps, x)) & held)
    for key in SUPERSEDED:
        if key in scope:
            problems.append("frozen_loop_scope.%s is COUNCIL D2's figure as worded, superseded by the owner's ruling on "
                            "OPEN ITEMS 1" % key)
    if scope.get("units") != loop:
        problems.append("frozen_loop_scope.units is not the loop lane less the kept rows")
    if scope.get("count") != len(loop):
        problems.append("frozen_loop_scope.count %r is not %d" % (scope.get("count"), len(loop)))
    if scope.get("kept_as_excluded") != kept:
        problems.append("kept_as_excluded %r is not the kept rows %r" % (scope.get("kept_as_excluded"), kept))
    if scope.get("behind_a_kernel_unit") != behind:
        problems.append("behind_a_kernel_unit %r is not the loop units behind a kernel unit %r"
                        % (scope.get("behind_a_kernel_unit"), behind))
    if scope.get("cut_blocked") != blocked:
        problems.append("cut_blocked %r is not the loop units behind a unit held for the cut %r" % (scope.get("cut_blocked"), blocked))
    want = "%d of %d, %.2f" % (len(loop) - len(blocked), len(loop), (len(loop) - len(blocked)) / len(loop)) if loop else "NO-DATA"
    if scope.get("max_share_before_cut") != want:
        problems.append("max_share_before_cut %r is not %r" % (scope.get("max_share_before_cut"), want))
    for u in units:
        if u.get("kept") and (u.get("state") != "DONE" or "kept_as" not in str(u.get("evidence"))):
            problems.append("%s: a kept row that is not DONE on its kept_as evidence" % u.get("id"))
        if u.get("lane") == "field" and u.get("done_check") == next(
                (x.get("done_check") for x in units if x.get("id") == "LC10"), None):
            problems.append("%s: a field unit closing on the certifier's own fixture test" % u.get("id"))
    return problems


def cut_problems(wbs):
    """Every way the WBS does not carry the cut council of 2026-10-18 evening: a CS unit or CF1 missing, CS12 not a loop
    lane unit inside the frozen scope, a CS done check that is not one python3 -B scripts/test_*.py command, an edge of
    CUT_EDGES missing, I26 not owning scripts/release_body.py, a version_of_plan silent about the cut council."""
    if not isinstance(wbs, dict) or not isinstance(wbs.get("units"), list):
        return ["NO-DATA: the WBS cannot be read"]
    units = {u.get("id"): u for u in wbs["units"] if isinstance(u, dict)}
    problems = ["%s: missing" % uid for uid in CUT_UNITS + ["CF1", "I26"] if uid not in units]
    for uid in LOOP_CUT_UNITS:
        if uid not in units:
            continue
        if units[uid].get("lane") != "loop" or units[uid].get("loop_gradable") is not True:
            problems.append("%s: not a loop lane unit" % uid)
        scope = wbs.get("frozen_loop_scope")
        if not isinstance(scope, dict) or uid not in (scope.get("units") or []):
            problems.append("%s: not in frozen_loop_scope.units" % uid)
    for uid in CUT_UNITS:
        if uid in units and not CUT_CHECK.match(str(units[uid].get("done_check"))):
            problems.append("%s: done_check is not one python3 -B scripts/test_*.py command: %r" % (uid, units[uid].get("done_check")))
    for uid, wanted in CUT_EDGES.items():
        if uid in units:
            deps = set(units[uid].get("depends_on") or [])
            problems.extend("%s: does not depend on %s" % (uid, d) for d in wanted if d not in deps)
    if "I26" in units and "scripts/release_body.py" not in (units["I26"].get("owns") or []):
        problems.append("I26: does not own scripts/release_body.py")
    if "cut council" not in str(wbs.get("version_of_plan", "")):
        problems.append("version_of_plan does not name the cut council")
    return problems


def _copy_plan(dst):
    for rel in (UNITS, WBS):
        os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, rel), os.path.join(dst, rel))
    shutil.copytree(os.path.join(ROOT, SPEC_DIR), os.path.join(dst, SPEC_DIR))


class TestJudges(unittest.TestCase):
    def test_blocking_problems_equal_lists_are_clean(self) -> None:
        f = [{"unit": "A2", "rule": "TEST-FOUND", "severity": "BLOCKING", "text": "the test file scripts/test_x.py ..."},
             {"unit": "B1", "rule": "SHARED-FILE", "severity": "ADVISORY", "text": "ignored"}]
        e = [{"unit": "A2", "rule": "TEST-FOUND", "path": "scripts/test_x.py", "why": "named"}]
        self.assertEqual(blocking_problems(f, e), [])

    def test_a_new_blocking_finding_is_refused(self) -> None:
        f = [{"unit": "C1", "rule": "SHARED-FILE", "severity": "BLOCKING", "text": "scripts/cut.py in both"}]
        self.assertTrue(any("unrecorded BLOCKING" in p for p in blocking_problems(f, [])))

    def test_a_cleared_finding_left_on_the_list_is_refused(self) -> None:
        e = [{"unit": "A2", "rule": "TEST-FOUND", "path": "scripts/test_x.py", "why": "named"}]
        self.assertTrue(any("no longer reports" in p for p in blocking_problems([], e)))

    def test_no_data_and_a_reasonless_entry_are_refused(self) -> None:
        f = [{"unit": "-", "rule": "SCOPE", "severity": "NO-DATA", "text": "x"}]
        self.assertTrue(any("NO-DATA finding" in p for p in blocking_problems(f, [])))
        self.assertTrue(any("without its reason" in p for p in blocking_problems([], [{"unit": "A2"}])))
        self.assertTrue(blocking_problems(None, [])[0].startswith("NO-DATA"))

    def test_scope_problems_refuse_a_hand_edited_scope(self) -> None:
        # X1 is held for the cut in this fixture (the shape of COUNCIL D2 as worded), LC10 is not (the ruling's shape)
        good = {"frozen_loop_scope": {"units": ["B1", "C1"], "count": 2, "kept_as_excluded": ["K1"],
                                      "behind_a_kernel_unit": ["C1"], "cut_blocked": ["C1"],
                                      "max_share_before_cut": "1 of 2, 0.50"},
                "units": [{"id": "A1", "lane": "bootstrap", "depends_on": [], "after": []},
                          {"id": "K1", "lane": "loop", "kept": True, "state": "DONE", "evidence": "kept_as: landed", "depends_on": [], "after": []},
                          {"id": "B1", "lane": "loop", "depends_on": ["A1"], "after": ["A1"]},
                          {"id": "X1", "lane": "kernel", "held_for_cut": True, "depends_on": [], "after": []},
                          {"id": "C1", "lane": "loop", "depends_on": ["B1"], "after": ["B1", "X1"]},
                          {"id": "LC10", "lane": "kernel", "done_check": "python3 -B scripts/test_c.py", "depends_on": [], "after": []},
                          {"id": "F1", "lane": "field", "done_check": "python3 -B scripts/test_s.py Soak", "depends_on": [], "after": []}]}
        for u in good["units"]:
            u.setdefault("held_for_cut", False)
        self.assertEqual(scope_problems(good), [])
        for field, value, word in (("cut_blocked", [], "cut_blocked"), ("count", 3, "count"),
                                   ("max_share_before_cut", "2 of 2, 1.00", "max_share_before_cut"),
                                   ("behind_a_kernel_unit", [], "behind_a_kernel_unit"),
                                   ("kept_as_excluded", [], "kept_as_excluded"),
                                   ("kernel_blocked", ["C1"], "superseded"),
                                   ("max_share_before_kernel_landing", "1 of 2, 0.50", "superseded")):
            bad = json.loads(json.dumps(good))
            bad["frozen_loop_scope"][field] = value
            self.assertTrue(any(word in p for p in scope_problems(bad)), (field, scope_problems(bad)))
        # the ruling's shape: no unit held for the cut, so the kernel's dependents wait on its landing only
        ruled = json.loads(json.dumps(good))
        ruled["units"][3]["held_for_cut"] = False
        ruled["frozen_loop_scope"].update(cut_blocked=[], max_share_before_cut="2 of 2, 1.00")
        self.assertEqual(scope_problems(ruled), [])
        self.assertTrue(any("cut_blocked" in p for p in scope_problems(dict(good, units=ruled["units"]))))
        # a loop unit itself held for the cut is blocked, not only its dependents
        own = json.loads(json.dumps(ruled))
        own["units"][2]["held_for_cut"] = True
        self.assertTrue(any("cut_blocked" in p for p in scope_problems(own)))
        own["frozen_loop_scope"].update(cut_blocked=["B1", "C1"], max_share_before_cut="0 of 2, 0.00")
        self.assertEqual(scope_problems(own), [])
        bad = json.loads(json.dumps(good))
        del bad["units"][3]["held_for_cut"]
        self.assertTrue(any("X1: NO-DATA, held_for_cut" in p for p in scope_problems(bad)))
        bad["units"][3]["held_for_cut"] = "no"
        self.assertTrue(any("X1: NO-DATA, held_for_cut" in p for p in scope_problems(bad)))
        bad = json.loads(json.dumps(good))
        bad["units"][1]["state"] = "OPEN"
        self.assertTrue(any("kept row" in p for p in scope_problems(bad)))
        bad = json.loads(json.dumps(good))
        bad["units"][6]["done_check"] = "python3 -B scripts/test_c.py"
        self.assertTrue(any("certifier's own fixture" in p for p in scope_problems(bad)))
        self.assertTrue(scope_problems({"units": []})[0].startswith("NO-DATA"))
        self.assertTrue(scope_problems(None)[0].startswith("NO-DATA"))

    def test_cut_problems_refuse_a_plan_without_the_cut_council(self) -> None:
        def unit(uid, deps=(), **over):
            u = {"id": uid, "lane": "loop", "loop_gradable": True, "done_check": "python3 -B scripts/test_%s.py" % uid.lower(),
                 "depends_on": list(deps), "owns": []}
            u.update(over)
            return u
        good = {"version_of_plan": "v4: the cut council of the evening",
                "frozen_loop_scope": {"units": CUT_UNITS + ["CF1", "I26"]},
                "units": [unit(uid, CUT_EDGES.get(uid, ())) for uid in CUT_UNITS if uid not in ("CS4", "CS11")]
                + [unit("CS4", ["A2"], lane="kernel", loop_gradable=False), unit("CS11", CUT_EDGES["CS11"]),
                   unit("CF1", CUT_EDGES["CF1"]), unit("I26", owns=["scripts/release_body.py"])]}
        self.assertEqual(cut_problems(good), [])
        for edit, word in (
                (lambda w: w["units"].pop(9), "CS12: missing"),   # CS1, CS2, CS3, CS5 to CS10, CS12, then CS4, CS11, CF1, I26
                (lambda w: w["units"][9].update(lane="kernel"), "CS12: not a loop lane unit"),
                (lambda w: w["frozen_loop_scope"]["units"].remove("CS12"), "CS12: not in frozen_loop_scope.units"),
                (lambda w: w["units"][0].update(done_check="sh scripts/test_cs1.sh"), "CS1: done_check is not one"),
                (lambda w: w["units"][-2].update(depends_on=["CS11"]), "CF1: does not depend on CS12"),
                (lambda w: w["units"][-3].update(depends_on=[]), "CS11: does not depend on CS1"),
                (lambda w: w["units"][-4].update(depends_on=[]), "CS4: does not depend on A2"),
                (lambda w: w["units"][-1].update(owns=[]), "I26: does not own scripts/release_body.py"),
                (lambda w: w.update(version_of_plan="v3"), "version_of_plan does not name the cut council"),
                (lambda w: w["units"].pop(11), "CS14: missing"),   # CS14 sits after CS12 and CS13
                (lambda w: w["units"][11].update(lane="kernel"), "CS14: not a loop lane unit"),
                (lambda w: w["frozen_loop_scope"]["units"].remove("CS14"), "CS14: not in frozen_loop_scope.units"),
                (lambda w: w["units"][11].update(depends_on=["A2"]), "CS14: does not depend on CS1"),
                (lambda w: w["units"][11].update(depends_on=["CS1"]), "CS14: does not depend on A2"),
                (lambda w: w["units"][-2].update(depends_on=["CS11", "CS12", "LC10", "A2"]), "CF1: does not depend on CS14"),
                (lambda w: w["units"][-3].update(depends_on=[u for u in CUT_UNITS if u not in ("CS11", "CS14")]),
                 "CS11: does not depend on CS14")):
            bad = json.loads(json.dumps(good))
            edit(bad)
            self.assertTrue(any(word in p for p in cut_problems(bad)), (word, cut_problems(bad)))
        self.assertEqual([u["id"] for u in good["units"]][9:12], ["CS12", "CS13", "CS14"], "the indexes the edits use")
        self.assertTrue(cut_problems(None)[0].startswith("NO-DATA"))

    def test_id_problems_refuse_a_changed_check_lane_and_scope(self) -> None:
        units = {"units": [{"id": "A1", "lane": "bootstrap", "acceptance": "python3 -B scripts/test_a.py", "depends_on": []},
                           {"id": "B1", "lane": "loop", "acceptance": "python3 -B scripts/test_b.py", "depends_on": ["A1"]}]}
        good = {"loop_scope": r"^(B1)(\.|$)", "units": [
            {"id": "A1", "lane": "bootstrap", "done_check": "python3 -B scripts/test_a.py", "depends_on": [], "after": [],
             "loop_gradable": False, "both_hosts": {"claude_code": "x", "codex": "y"}, "state": "OPEN"},
            {"id": "B1", "lane": "loop", "done_check": "python3 -B scripts/test_b.py", "depends_on": ["A1"], "after": ["A1"],
             "loop_gradable": True, "both_hosts": {"claude_code": "x", "codex": "y"}, "state": "OPEN"}]}
        self.assertEqual(id_problems(good, units), [])
        bad = json.loads(json.dumps(good))
        bad["units"][1].update(done_check="python3 -B scripts/test_c.py", depends_on=[], after=[], loop_gradable=False)
        bad["loop_scope"] = r"^(A1|B1)(\.|$)"
        probs = id_problems(bad, units)
        for word in ("not the acceptance", "depends_on edge is missing", "loop_gradable", "loop_scope admits"):
            self.assertTrue(any(word in p for p in probs), (word, probs))
        self.assertTrue(id_problems(None, units)[0].startswith("NO-DATA"))


@unittest.skipUnless(HUB_ONLY, "docs/plan/specs is hub only, not shipped in the export tree")
class TestRealPlan(unittest.TestCase):
    def test_the_generator_reproduces_the_committed_plan(self) -> None:
        code, out = check_run(ROOT)
        self.assertEqual(code, 0, out)
        self.assertIn("check OK", out)

    def test_the_spec_steps_sub_units_survive_a_regeneration(self) -> None:
        """2026-10-19 (A1): the generator rendered sub_units [] over whatever the spec step wrote, so no 1.1.7 unit could
        ever close; sub_units and spec are live fields now, like state and evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            _copy_plan(tmp)
            path = os.path.join(tmp, WBS)
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            # a row the closer has not written yet, outside the frozen scope: a frozen scope unit's sub_units feed the two
            # route sets of the owner's ruling on OPEN ITEMS 5, so changing them re-derives the WBS (the next case)
            # (OPEN ITEMS 8 gives every DEFERRED row its evidence, so the row is picked by the closer's own state_at)
            unit = next(u for u in doc["units"] if "state_at" not in u
                        and u["id"] not in doc["frozen_loop_scope"]["units"])
            want = [unit["id"] + ".a"]
            unit["sub_units"] = want
            # the closer's own keys, appended in the order close_unit writes them (evidence, state_at, provenance's four),
            # in the writers' own format (plan_store: indent 1, a trailing newline), with NO regeneration in between
            closer = {"evidence": " X.a landed. UNIT DONE.", "state_at": "2026-10-19", "changed_by": "t",
                      "changed_via": "close_unit.py", "changed_at": "2026-10-19", "changed_because": "c"}
            unit.update(closer)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(doc, indent=1) + "\n")
            code, out = check_run(tmp)
            r = subprocess.run([sys.executable, "-B", os.path.join(ROOT, GEN)], cwd=tmp, capture_output=True,
                               text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(path, encoding="utf-8") as fh:
                kept = next(u for u in json.load(fh)["units"] if u["id"] == unit["id"])
        self.assertEqual(code, 0, out)
        self.assertEqual(kept["sub_units"], want)
        self.assertEqual({k: kept.get(k) for k in closer}, closer)

    def test_a_frozen_scope_sub_unit_change_re_derives_the_route_sets(self) -> None:
        """A writer that changes a frozen scope unit's sub_units changes by_hand or loop_built, so --check reads the WBS
        STALE until the generator runs, and the regenerated sets count the new sub unit (fail direction: a derived
        count never goes silently out of date)."""
        with tempfile.TemporaryDirectory() as tmp:
            _copy_plan(tmp)
            path = os.path.join(tmp, WBS)
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            uid = doc["frozen_loop_scope"]["units"][0]
            unit = next(u for u in doc["units"] if u["id"] == uid)
            before = len(doc["frozen_loop_scope"]["loop_built"]["sub_units"]) + len(doc["frozen_loop_scope"]["by_hand"]["sub_units"])
            unit["sub_units"] = list(unit["sub_units"]) + [unit["sub_units"][0] + "x"]
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(doc, indent=1) + "\n")
            code, out = check_run(tmp)
            self.assertEqual(code, 1, out)
            self.assertIn("STALE %s" % WBS, out)
            r = subprocess.run([sys.executable, "-B", os.path.join(ROOT, GEN)], cwd=tmp, capture_output=True,
                               text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(path, encoding="utf-8") as fh:
                scope = json.load(fh)["frozen_loop_scope"]
        # the added id has no section of its own, so its route reads NO-DATA, never loop built
        self.assertIn(unit["sub_units"][-1], scope["route_no_data"])
        self.assertEqual(len(scope["loop_built"]["sub_units"]) + len(scope["by_hand"]["sub_units"]), before)

    def test_a_hand_edit_to_the_wbs_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _copy_plan(tmp)
            path = os.path.join(tmp, WBS)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8") as fh:
                # state, evidence and remains survive a regeneration by design; any other field is the generator's
                fh.write(text.replace('"size": "', '"size": "X', 1))
            code, out = check_run(tmp)
        self.assertEqual(code, 1, out)
        self.assertIn("STALE %s" % WBS, out)

    def test_a_chained_acceptance_stops_the_generator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _copy_plan(tmp)
            path = os.path.join(tmp, UNITS)
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            cmd = doc["units"][0]["acceptance"]
            doc["units"][0]["acceptance"] = "%s && %s" % (cmd, cmd)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            code, out = check_run(tmp)
        self.assertNotEqual(code, 0, out)
        self.assertIn("not one command the landing gate runs", out)

    def test_the_lint_reports_only_what_the_plan_records(self) -> None:
        import plan_lint
        plan = _json(os.path.join(ROOT, WBS))
        self.assertIsNotNone(plan)
        report = plan_lint.lint_plan(os.path.join(ROOT, WBS), root=ROOT, ledger=None)
        self.assertEqual(report.get("exit"), 0, report.get("reason"))
        self.assertEqual(report["read"]["units"], len(plan["units"]))
        self.assertEqual(blocking_problems(report["findings"], plan.get("lint_blocking_open")), [])

    def test_the_ids_are_the_unit_files(self) -> None:
        self.assertEqual(id_problems(_json(os.path.join(ROOT, WBS)), _json(os.path.join(ROOT, UNITS))), [])

    def test_the_frozen_scope_is_what_the_units_say(self) -> None:
        plan = _json(os.path.join(ROOT, WBS))
        self.assertEqual(scope_problems(plan), [])
        # the review's finding 2: the kept rows landed in 1.1.6 and never count against the loop
        self.assertEqual(plan["frozen_loop_scope"]["kept_as_excluded"], ["I6", "I7", "I24", "I25", "I31"])

    def test_the_cut_council_is_applied(self) -> None:
        self.assertEqual(cut_problems(_json(os.path.join(ROOT, WBS))), [])

    def test_the_owners_ruling_on_open_item_1_is_applied(self) -> None:
        plan = _json(os.path.join(ROOT, WBS))
        # no unit's landing waits on the cut: a kernel unit lands through the deploy step, its stamp waits for the y
        self.assertEqual(plan["frozen_loop_scope"]["cut_blocked"], [])
        for u in plan["units"]:
            self.assertIs(u.get("held_for_cut"), False, u["id"])
            if u["lane"] == "kernel":
                # OPEN ITEMS 6 (2026-10-19) flipped item 1 to (B): the stamp moves on each batch's approval mid run
                self.assertIn("phone code", u.get("stamp_moves_on", ""), u["id"])
        # items 2 and 3 from the review of 2026-10-18 afternoon stay open; item 4 (the second review of the cut
        # design) was RULED 2026-10-19, see the next test
        # OPEN ITEMS 10 (2026-10-21, the cut scope) was open, then RULED the same evening
        self.assertEqual([x.get("id") for x in plan["open_items"]], [2, 3])
        self.assertFalse([d for d in plan["decisions_waiting"] if str(d.get("question", "")).startswith("OPEN ITEMS 1:")])
        ruled = {x.get("id"): x for x in plan.get("ruled_items", [])}
        self.assertEqual(ruled.get(1, {}).get("ruling"), RULING_1)
        self.assertEqual(ruled[1].get("option"), "A")
        self.assertEqual(ruled[1].get("date"), "2026-10-18")

    def test_the_owners_ruling_on_open_item_6_flips_item_1_to_b(self) -> None:
        plan = _json(os.path.join(ROOT, WBS))
        ruled = {x.get("id"): x for x in plan.get("ruled_items", [])}
        self.assertEqual((ruled.get(6, {}).get("ruling"), ruled[6].get("option"), ruled[6].get("date")),
                         (RULING_6, "B", "2026-10-19"))
        self.assertNotRegex(json.dumps(ruled[6]), r"\b\d{6}\b", "the one time code is never recorded")
        kernel = [u for u in plan["units"] if u["lane"] == "kernel"]
        self.assertTrue(kernel)
        for u in kernel:
            self.assertNotIn("batched with the cut y", u.get("stamp_moves_on", "") + u.get("owner_decision", ""), u["id"])
        self.assertTrue(any("mid run" in str(d.get("question", "")) for d in plan["decisions_waiting"]))

    def test_the_owners_ruling_on_open_item_7_reports_the_loop_share_in_1_1_7(self) -> None:
        plan = _json(os.path.join(ROOT, WBS))
        ruled = {x.get("id"): x for x in plan.get("ruled_items", [])}
        self.assertEqual((ruled.get(7, {}).get("ruling"), ruled[7].get("date")), (RULING_7, "2026-10-20"))
        scope = plan["frozen_loop_scope"]
        self.assertIn("binding from 1.1.8", scope["bar"])
        self.assertIn("OPEN ITEMS 7", scope["loop_built"]["rule"])

    def test_the_owners_ruling_on_open_item_8_defers_everything_outside_the_core(self) -> None:
        plan = _json(os.path.join(ROOT, WBS))
        ruled = {x.get("id"): x for x in plan.get("ruled_items", [])}
        self.assertEqual((ruled.get(8, {}).get("ruling"), ruled[8].get("date")), (RULING_8, "2026-10-20"))
        states = {u["id"]: u["state"] for u in plan["units"]}
        # OPEN ITEMS 9 widened the core with LR1 and its proof scope; OPEN ITEMS 10 narrowed it to CORE_10
        for uid in CORE_10:
            self.assertIn(states[uid], ("OPEN", "DONE"), uid)
        outside = {k: v for k, v in states.items() if k not in CORE_10}
        self.assertTrue(outside)
        self.assertEqual(sorted(set(outside.values()) - {"DONE"}), ["DEFERRED"], "outside the core: DONE or DEFERRED")
        self.assertTrue(any(v == "DONE" for v in outside.values()))

    def test_the_owners_ruling_on_open_item_9_brings_lr1_and_its_proof_scope_back(self) -> None:
        plan = _json(os.path.join(ROOT, WBS))
        ruled = {x.get("id"): x for x in plan.get("ruled_items", [])}
        self.assertEqual((ruled.get(9, {}).get("ruling"), ruled[9].get("date")), (RULING_9, "2026-10-20"))
        units = {u["id"]: u for u in plan["units"]}
        self.assertEqual(units["LR1"]["done_check"], "python3 -B scripts/loop/test_loop_works_fully.py")
        for uid in CORE_9:
            self.assertNotIn("OPEN ITEMS 8", str(units[uid].get("evidence", "")), uid)

    def test_the_owners_ruling_on_open_item_10_holds_the_cut_for_the_full_proof(self) -> None:
        plan = _json(os.path.join(ROOT, WBS))
        ruled = {x.get("id"): x for x in plan.get("ruled_items", [])}
        self.assertEqual((ruled.get(10, {}).get("ruling"), ruled[10].get("date")), (RULING_10, "2026-10-21"))
        for words in OWNER_WORDS_10:
            self.assertIn(words, ruled[10]["question"])
        self.assertFalse([d for d in plan["decisions_waiting"] if str(d.get("question", "")).startswith("OPEN ITEMS 10:")])
        units = {u["id"]: u for u in plan["units"]}
        for uid in PROOF_9 + ("LR1",):
            self.assertIn(units[uid]["state"], ("OPEN", "DONE"), uid)
            self.assertNotIn("OPEN ITEMS 10", str(units[uid].get("evidence", "")), uid)
        self.assertIn("PP1.c landed", units["PP1"]["evidence"], "a landed sub unit keeps its line")

    def test_the_owners_ruling_on_open_item_4_is_applied(self) -> None:
        plan = _json(os.path.join(ROOT, WBS))
        ruled = {x.get("id"): x for x in plan.get("ruled_items", [])}
        self.assertEqual(ruled.get(4, {}).get("ruling"), RULING_4)
        self.assertEqual(ruled[4].get("option"), "B")
        self.assertEqual(ruled[4].get("date"), "2026-10-19")
        self.assertNotIn(4, [x.get("id") for x in plan["open_items"]])
        self.assertFalse([d for d in plan["decisions_waiting"] if str(d.get("question", "")).startswith("OPEN ITEMS 4:")])
        units = {u["id"]: u for u in plan["units"]}
        self.assertIn("CS14", units, "the per landing mutation unit")
        self.assertIn("CS14", plan["frozen_loop_scope"]["units"])
        self.assertTrue(CUT_CHECK.match(units["CS14"]["done_check"]), units["CS14"]["done_check"])
        self.assertIn(re.search(r"scripts/test_\w+\.py", units["CS14"]["done_check"]).group(0), units["CS14"]["owns"])


def _section(text, sub):
    """One sub unit's section, the scorer's reading: its heading to the next heading of its level or higher."""
    h = re.search(r"^(#{2,4}) %s\b" % re.escape(sub), text, flags=re.M)
    if not h:
        return ""
    m = re.search(r"^#{2,4} %s\b.*?(?=^#{2,%d} |\Z)" % (re.escape(sub), len(h.group(1))), text, flags=re.M | re.S)
    return m.group(0) if m else ""


def recompute_routes(plan, root):
    """by_hand and loop_built over the frozen scope's units, recomputed from the units and their specs alone, with the
    adapter's own rule (plan_lint.protected_reason over grade_build.dest) as a second reading: (by_hand, loop_built,
    problems). A sub unit is by_hand when its Built by line names the reviewed route or the session; the second reading
    must agree, a refused path on its Files line, unless the Built by line names its other measured reason
    (BRIEF-BUDGET); a loop built sub unit may write no refused path."""
    import plan_lint
    units = {u["id"]: u for u in plan["units"]}
    by_hand, loop_built, problems = [], [], []
    for uid in plan["frozen_loop_scope"]["units"]:
        u = units[uid]
        try:
            with open(os.path.join(root, u["spec"]), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, KeyError, TypeError):
            problems.append("%s: its spec cannot be read" % uid)
            continue
        if not u.get("sub_units"):
            problems.append("%s: no sub units" % uid)
        for sub in u.get("sub_units") or []:
            sec = _section(text, sub)
            built = re.search(r"(?m)^Built by:(.*)$", sec)
            files = next((ln for ln in sec.splitlines() if ln.lstrip().startswith("Files:")), "")
            files = re.split(r"\bReads?:|\.\s", files + " ")[0]
            refused = [q for q in re.findall(r"`([A-Za-z0-9_./-]+/[A-Za-z0-9_.-]+\.[A-Za-z]+)`", files)
                       if plan_lint.protected_reason(q)]
            if not built:
                problems.append("%s: no Built by line" % sub)
                continue
            hand = "reviewed route" in built.group(1) or "the session" in built.group(1)
            (by_hand if hand else loop_built).append(sub)
            if hand and not refused and "BRIEF-BUDGET" not in built.group(1):
                problems.append("%s: by hand, but its Files line names no refused path and no other reason" % sub)
            if not hand and refused:
                problems.append("%s: loop built, but grade_build.dest refuses %s" % (sub, refused[0]))
    return by_hand, loop_built, problems


@unittest.skipUnless(HUB_ONLY, "docs/plan/specs is hub only, not shipped in the export tree")
class TheOwnersRulingOnTheReviewedRoute(unittest.TestCase):
    """STABILIZE.md OPEN ITEMS 5, RULED 2026-10-19: sub units on the reviewed route are landed by hand and counted
    separately; the certificate judges the loop built remainder at 100 percent. The WBS states both sets with their
    counts, and this case recomputes them from the units and their specs alone."""

    def test_the_owners_ruling_on_open_item_5_is_applied(self) -> None:
        plan = _json(os.path.join(ROOT, WBS))
        ruled = {x.get("id"): x for x in plan.get("ruled_items", [])}
        self.assertEqual(ruled.get(5, {}).get("ruling"), RULING_5)
        self.assertEqual((ruled[5].get("option"), ruled[5].get("date")), ("A", "2026-10-19"))
        scope = plan["frozen_loop_scope"]
        self.assertIn("100 percent of loop_built", scope["bar"])
        by_hand, loop_built, problems = recompute_routes(plan, ROOT)
        self.assertEqual(problems, [])
        self.assertEqual(scope["by_hand"]["sub_units"], by_hand)
        self.assertEqual(scope["loop_built"]["sub_units"], loop_built)
        self.assertEqual((scope["by_hand"]["count"], scope["loop_built"]["count"]), (len(by_hand), len(loop_built)))
        self.assertEqual((scope["route_no_data"], scope["no_sub_units"]), ([], []))
        self.assertTrue(by_hand and loop_built, "both sets are measured, neither is empty by accident")

    def test_a_sub_unit_whose_route_cannot_be_read_is_never_loop_built(self) -> None:
        plan = _json(os.path.join(ROOT, WBS))
        uid = plan["frozen_loop_scope"]["units"][0]
        unit = next(u for u in plan["units"] if u["id"] == uid)
        tmp = tempfile.mkdtemp(prefix="plan117-route-")
        try:
            spec = os.path.join(tmp, unit["spec"])
            os.makedirs(os.path.dirname(spec))
            with open(os.path.join(ROOT, unit["spec"]), encoding="utf-8") as fh:
                text = fh.read()
            with open(spec, "w", encoding="utf-8") as fh:
                fh.write(re.sub(r"(?m)^Built by:.*$", "Built by", text, count=1))
            _by_hand, _loop_built, problems = recompute_routes({"units": [unit], "frozen_loop_scope": {"units": [uid]}}, tmp)
            self.assertIn("%s: no Built by line" % unit["sub_units"][0], problems)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
