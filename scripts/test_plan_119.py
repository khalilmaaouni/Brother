#!/usr/bin/env python3
"""The 1.1.9 plan is split, specced and validated (the shape of scripts/test_plan_118.py, whose judges are reused):
  class Plan          the generator verifies and is fresh; every loop sub unit's files pass scripts/plan_lanes.py (R-S10);
                      the loop scope names exactly the loop units; every access unit closes on a JT0 journey row and
                      orders after JT0 (R-S11, owner AC1), and every named row belongs to a journey JT0 walks; no unit sits
                      in an earlier wave than an in-plan prerequisite; no carried stamp landing got weaker than its 1.1.8
                      row; the X01 cut gate is planned (AC2); plan_lint --report over the loop units exits 0, reports every
                      loop unit, and blocks only on the named BRIEF-BUDGET wait for SLICE1 (stability S7); no em or en dash
                      and no private term in any spec or record (counts only).
  class Harness*      HX119's groups: every loop sub unit's harness class exists and fails BY AN ASSERTION while its unit
                      is open, and passes once it is DONE (a skip is never red, stability S4).
  class HarnessVRT1   S11's check: VaultRoot and VaultWriters, red while VRT1 is open, green once it is DONE.
  class SliceClears   SLICE1's check: plan_lint reports no BRIEF-BUDGET on CJK1, FOOT1 and PAGE1.
  class LaunchReady   the launch gate: every after_release id is DONE in the plan that owns it (runner_pool does not read
                      after_release, so this is where it is enforced).
Only Plan is registered in check_all.sh; the others are done checks and the launch gate, red until their work lands.
HUB ONLY: in an exported tree the plan files are absent, so every class skips with NO-DATA by name, as test_plan_118 does.
Anything unreadable is a failure, never a clean list."""
import json
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
WBS = "docs/plan/BROTHER-1.1.9-WBS.json"
WBS_118 = "docs/plan/BROTHER-1.1.8-WBS.json"
GEN = "docs/plan/specs/1.1.9/plan_v119.py"
SPEC_DIR = "docs/plan/specs/1.1.9"
SLICE_WAIT = "SLICE1"
IN_HUB = os.path.isfile(os.path.join(ROOT, WBS))
HUB_ONLY = "NO-DATA: the plan files are not in this tree (an exported tree carries no docs/plan WBS); this test runs in the hub"
ACCESS = ("FIRST1H", "PROOF1", "CARD1", "PC1H", "FRUN1H", "HD1", "README118", "FOOT1H", "VRT2", "VRT1", "DOOR1", "ACC1")
S11_UNITS = ("VRT1",)   # their classes are S11's (HarnessVRT1), never HX119's
JT0_JOURNEYS = ("J1", "J2", "J4", "J5")
GROUPS = {"HarnessFirst": ("scripts/test_first_task.py",),
          "HarnessCard": ("scripts/test_proof_source.py", "scripts/test_pages_wellformed.py"),
          "HarnessSafety": ("scripts/test_routed_model_safety.py", "scripts/test_footprint.py"),
          "HarnessVault": ("scripts/test_owner_paths.py",)}


def wbs(path=WBS):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return json.load(fh)


def run(args, timeout=600):
    return subprocess.run([sys.executable, "-B"] + args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)


#: THE REGRESSION GUARD CARVE OUT, owner ruling 2026-10-25, in his words: "Accept a documented
#: regression guard in place of a red where the red case no longer reproduces."
#: WHY IT IS A DECLARED MAP AND NOT A RELAXED RULE. A class that passes without its own fix normally
#: proves nothing about that fix, which is what PROOF1 exists to enforce, so the rule stays: a green
#: class while its work is open is a problem. The exception is the case measured on 2026-10-25, where a
#: sub unit's stated red case was written against an older tree and no longer reproduces because the
#: work is partly already done. Such a class is still worth keeping, as a guard against the defect
#: coming back, but it must be NAMED here with the measurement that showed the red is gone. An
#: undeclared green is still refused, and a declaration with no reason is still refused, so this cannot
#: become a quiet way to pass a class nobody checked.
GUARDS = {
    ("scripts/test_first_task.py", "Messages"):
        "FRUN1.e: measured 2026-10-25, sbe_sessionstart.py carries two lines matching a refusal word and "
        "both are prose in a comment and a docstring, not a printed message, and neither it nor "
        "sbe_session_reconcile.py names a private repository. The stated red does not occur on this tree.",
}


def source_of(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def guard_reason(path, cls, guards=None):
    """The recorded reason this class is allowed to be green while its work is open, or '' when it is
    not declared, its declaration carries no reason, or that reason is not even text. Never a silent
    True. ONE function answers this for every caller, including the tests: an injected table that was
    judged by different code would test something the product does not do, and the first version here
    did exactly that, crashing on a non-text reason instead of refusing it."""
    table = GUARDS if guards is None else guards
    reason = table.get((path, cls), "")
    return reason.strip() if isinstance(reason, str) else ""


def red_or_green_problems(path, classes, done, guards=None):
    """A class must fail by an assertion while its work is open, and pass once it is DONE. The one
    exception is a class declared in GUARDS with a reason: see the note above it."""
    problems = []
    if not os.path.isfile(os.path.join(ROOT, path)):
        return ["%s is not written" % path]
    for cls in classes:
        r = run([path, cls, "-v"])
        out = r.stdout + r.stderr
        if done:
            if r.returncode != 0:
                problems.append("%s %s: red although its work is DONE" % (path, cls))
        elif r.returncode == 0:
            allowed = guard_reason(path, cls, guards)
            if not allowed:
                problems.append("%s %s: green while its work is open, and no regression guard is declared "
                                "for it with a reason" % (path, cls))
        elif not re.search(r"failures=\d+", out) or re.search(r"errors=\d+|skipped=\d+", out):
            problems.append("%s %s: red for the wrong reason (an error or a skip, not an assertion)" % (path, cls))
    return problems


def group_problems(group):
    files = GROUPS[group]
    found, problems = {}, []
    for u in wbs()["units"]:
        if u["lane"] != "loop" or u["id"] in S11_UNITS:
            continue
        for sid, check in u["sub_done_checks"].items():
            m = re.match(r"python3 -B (\S+) (\w+)$", check)
            if m and m.group(1) in files:
                found.setdefault((m.group(1), u.get("state") == "DONE"), []).append(m.group(2))
    if not found:
        #: A HARNESS WHOSE EVERY CONSUMER THE OWNER CUT is not a harness nobody runs; it left with its
        #: units. The cut is read from the plan's own slipped record, which names each slipped unit's
        #: harness files, so this reads the decision rather than guessing at it. A slipped record with
        #: no harness lists settles nothing and the problem stands (2026-10-29, D2's 16 unit cut).
        gone = {h for rec in (wbs().get("slipped") or {}).get("units", {}).values()
                if isinstance(rec, dict) for h in rec.get("harness") or []}
        if files and all(f in gone for f in files):
            return []
        return ["no loop sub unit runs %s" % ", ".join(files)]
    for (path, done), classes in sorted(found.items()):
        problems += red_or_green_problems(path, classes, done)
    return problems


@unittest.skipUnless(IN_HUB, HUB_ONLY)
def _generator():
    """plan_v119 as a module, or None when it cannot be loaded (an exported tree carries no generator)."""
    path = os.path.join(ROOT, GEN)
    if not os.path.isfile(path):
        return None
    import importlib.util
    spec_dir = os.path.join(ROOT, SPEC_DIR)
    if spec_dir not in sys.path:
        sys.path.insert(0, spec_dir)
    loaded = importlib.util.spec_from_file_location("plan_v119_under_test", path)
    mod = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(mod)
    return mod


def _units(*rows):
    """The least a unit needs for the dependency walk: (id, after, depends_on)."""
    return [{"id": i, "wave": 0, "after": list(a), "depends_on": list(d)} for i, a, d in rows]


def _oracle_cyclic(units):
    """Is this graph cyclic, answered by Kahn's algorithm, which shares no code or idea with the detector's
    depth first colouring. It exists so a TYPO IN A FIXTURE cannot pass forever: a fixture meant to be
    cyclic that this calls acyclic fails its own test as malformed, rather than quietly agreeing with a
    detector that is also wrong."""
    by = {u["id"]: u for u in units}
    deps = {i: {d for f in ("after", "depends_on") for d in u.get(f, []) if d in by}
            for i, u in by.items()}
    left = dict(deps)
    while True:
        free = [i for i, d in left.items() if not d]
        if not free:
            return bool(left)
        for i in free:
            del left[i]
        for d in left.values():
            d.difference_update(free)


class LandedLabels(unittest.TestCase):
    """The landed carve out is exactly the DONE units, and no wider.

    verify skips its on disk label checks for units the live plan reads DONE, because their landing made
    their NEW files exist. plan_v119 dropped that argument entirely, so the first 1.1.9 unit to close
    made the generator refuse to run at all. Fixing that is not enough on its own: a landed set WIDER
    than DONE would stop every label being checked, and a mutation that returned every id survived the
    first sweep. These fixtures are what killed it."""

    def setUp(self):
        self.gen = _generator()
        if self.gen is None:
            self.skipTest(HUB_ONLY)

    def test_only_done_is_landed(self):
        live = {"A": {"state": "DONE"}, "B": {"state": "OPEN"}, "C": {"state": "DEFERRED"}}
        self.assertEqual(self.gen.landed_ids(live), frozenset({"A"}))

    def test_a_row_with_no_state_is_not_landed(self):
        self.assertEqual(self.gen.landed_ids({"A": {}}), frozenset())

    def test_a_row_that_is_not_a_record_is_not_landed(self):
        self.assertEqual(self.gen.landed_ids({"A": "DONE"}), frozenset())

    def test_a_landed_sub_unit_is_in_the_set(self):
        """A sub unit the plan's own evidence records as landed settles its files, because that landing
        is what made them exist. Without this the generator refused every regeneration between a unit's
        first and last sub unit, which is most of a unit's life."""
        live = {"A": {"state": "OPEN", "sub_units": ["A.a", "A.b"],
                      "evidence": "A.a landed 2026-10-25: the walker is in"}}
        got = self.gen.landed_ids(live)
        self.assertIn("A.a", got, "a landed sub unit must settle its own files")
        self.assertNotIn("A.b", got, "an unlanded sibling must still have its labels judged")
        self.assertNotIn("A", got, "the unit itself is not DONE and must not be settled")

    def test_evidence_that_is_not_text_settles_nothing(self):
        """An unreadable evidence field is unknown, never landed: every label is then judged, which is
        the fail direction."""
        for bad in (None, 7, [], {}):
            live = {"A": {"state": "OPEN", "sub_units": ["A.a"], "evidence": bad}}
            self.assertEqual(self.gen.landed_ids(live), frozenset(),
                             "evidence of %r must settle nothing" % (bad,))

    def test_a_negated_landing_is_not_landed(self):
        """plan_store's own rule, not a second copy of it: a negated mention is not a landing."""
        live = {"A": {"state": "OPEN", "sub_units": ["A.a"],
                      "evidence": "A.a not landed yet, the walker is unwritten"}}
        self.assertEqual(self.gen.landed_ids(live), frozenset(),
                         "a negated landing must not settle a file")

    def test_an_unreadable_live_plan_lands_nothing(self):
        for bad in (None, [], "DONE", 7):
            self.assertEqual(self.gen.landed_ids(bad), frozenset(),
                             "an unreadable live plan must land nothing, so every label is checked")


class Guards(unittest.TestCase):
    """The regression guard carve out of 2026-10-25 cannot become a quiet pass.

    The owner accepted a documented guard in place of a red where the red case no longer reproduces. The
    risk that creates is obvious: a green class nobody checked, waved through. So the carve out is a
    DECLARED map and these fixtures hold its three refusals: an undeclared green is refused, a
    declaration with no reason is refused, and a declaration whose reason is not even text is refused.
    The last fixture refuses a STALE declaration, which is the failure mode that would bite later: a
    class gets its real fix, stops being green, and its guard entry lingers."""

    def test_a_declared_guard_gives_its_reason(self):
        self.assertTrue(guard_reason("scripts/test_first_task.py", "Messages"),
                        "the declared guard for Messages carries no reason")

    def test_an_undeclared_green_is_not_allowed(self):
        self.assertEqual(guard_reason("scripts/test_first_task.py", "Consent"), "",
                         "an undeclared class must never be allowed to be green while its work is open")

    def test_a_declaration_with_no_reason_is_not_allowed(self):
        self.assertEqual(red_or_green_problems.__defaults__, (None,),
                         "red_or_green_problems lost its injectable guards argument")
        problems = red_or_green_problems("scripts/test_first_task.py", ["Messages"], False,
                                         guards={("scripts/test_first_task.py", "Messages"): "   "})
        self.assertNotEqual(problems, [], "a guard declared with a blank reason must still be refused")

    def test_a_declaration_that_is_not_text_is_not_allowed(self):
        problems = red_or_green_problems("scripts/test_first_task.py", ["Messages"], False,
                                         guards={("scripts/test_first_task.py", "Messages"): 1})
        self.assertNotEqual(problems, [], "a guard whose reason is not text must still be refused")

    def test_no_declaration_is_stale(self):
        """Every declared guard names a file that exists and a class defined in it. A guard left behind
        after its class got a real fix would pass forever on a rule nobody is applying."""
        stale = []
        for (path, cls), reason in GUARDS.items():
            full = os.path.join(ROOT, path)
            if not os.path.isfile(full):
                stale.append("%s (file absent)" % path)
            elif not re.search(r"^class %s\b" % re.escape(cls), source_of(full), re.M):
                stale.append("%s %s (class absent)" % (path, cls))
            elif not isinstance(reason, str) or not reason.strip():
                stale.append("%s %s (no reason)" % (path, cls))
        self.assertEqual(stale, [], "these guard declarations no longer describe anything: %s" % stale)


class Acyclic(unittest.TestCase):
    """Stability: the generator refuses a dependency graph no order can execute, and names every cycle.
    Written 2026-10-25, after plan_v119.py --verify-only printed verify OK on the real 1.1.9 plan while that
    plan carried TWELVE cycles trapping 14 of 39 units, 7 of them loop lane. The cause was in derive_waves:
    its recursion guard skipped a back edge (`d not in seen`) instead of refusing it, so the guard that
    stopped the recursion also hid the reason it had to. One fixture per guard, and nothing here reads the
    real plan: scripts/donecheck_cycles119.py is what holds the real plan to this bar."""

    def setUp(self):
        self.gen = _generator()
        if self.gen is None:
            self.skipTest(HUB_ONLY)

    def test_an_acyclic_graph_has_no_cycle(self):
        us = _units(("A", [], []), ("B", ["A"], ["A"]), ("C", ["B"], ["B"]))
        self.assertEqual(self.gen.plan_cycles(us), [])

    def test_a_two_unit_cycle_is_found(self):
        us = _units(("A", ["B"], ["B"]), ("B", ["A"], ["A"]))
        self.assertTrue(_oracle_cyclic(us), "fixture is malformed: the oracle calls it acyclic")
        self.assertTrue(self.gen.plan_cycles(us), "A after B after A is a cycle")

    def test_a_self_edge_is_found(self):
        us = _units(("A", ["A"], ["A"]))
        self.assertTrue(_oracle_cyclic(us), "fixture is malformed: the oracle calls it acyclic")
        self.assertTrue(self.gen.plan_cycles(us), "a unit that is its own prerequisite is a cycle")

    def test_a_cycle_only_in_depends_on_is_found(self):
        us = _units(("A", [], ["B"]), ("B", [], ["A"]))
        self.assertTrue(_oracle_cyclic(us), "fixture is malformed: the oracle calls it acyclic")
        self.assertTrue(self.gen.plan_cycles(us), "depends_on carries edges too, not only after")

    def test_a_diamond_is_not_a_cycle(self):
        """The false positive trap: a detector that flags a node VISITED TWICE instead of a node ON THE
        CURRENT PATH calls this cyclic. D is reached by two routes and the graph is still a DAG."""
        us = _units(("A", [], []), ("B", ["A"], ["A"]), ("C", ["A"], ["A"]), ("D", ["B", "C"], ["B", "C"]))
        self.assertFalse(_oracle_cyclic(us), "fixture is malformed: the oracle calls the diamond cyclic")
        self.assertEqual(self.gen.plan_cycles(us), [])

    def test_an_edge_to_a_unit_outside_the_plan_is_not_a_cycle(self):
        self.assertEqual(self.gen.plan_cycles(_units(("A", ["OUTSIDE"], ["OUTSIDE"]))), [])

    def test_an_unreadable_edge_field_refuses(self):
        us = [{"id": "A", "wave": 0, "after": "B", "depends_on": []}]
        with self.assertRaises(SystemExit):
            self.gen.plan_cycles(us)

    def test_verify_refuses_a_cyclic_plan_before_any_other_check(self):
        """THE ENTRY POINT, not a helper. verify is what main calls, and its cycle refusal runs before every
        other check, so a two unit fixture that would fail twenty other checks still refuses for the cycle
        and names both units. Without this the guard lived only in a function no caller test reached."""
        us = _units(("AA", ["BB"], ["BB"]), ("BB", ["AA"], ["AA"]))
        self.assertTrue(_oracle_cyclic(us), "fixture is malformed: the oracle calls it acyclic")
        with self.assertRaises(SystemExit) as caught:
            self.gen.G.verify(us, {"AA", "BB"}, "fixture")
        said = str(caught.exception)
        self.assertIn("cycle", said)
        self.assertIn("AA", said)
        self.assertIn("BB", said)

    def test_derive_waves_refuses_a_back_edge(self):
        us = _units(("A", ["B"], ["B"]), ("B", ["A"], ["A"]))
        with self.assertRaises(SystemExit):
            self.gen.derive_waves(us)


#: GUARDED FOR THE EXPORT TREE, 2026-10-28. This file's own docstring already claimed "in an exported
#: tree the plan files are absent, so every class skips with NO-DATA by name", and for this class that
#: was false: scripts/pre_push_gate.py REFUSED the push with "scripts/test_plan_119.py FAILS on the
#: export tree with an empty HOME (exit 1: FAILED (failures=1, errors=8, skipped=24))", and all nine
#: were this class's. Plan is the class that reads the plan (WBS, WBS_118, SPEC_DIR, wbs()), so the
#: guard goes here and nowhere else: LandedLabels, Guards and Acyclic all survive an absent plan and
#: keep running there, which is 21 tests of real coverage a whole file guard would have thrown away.
#: NOTHING IS LOST BY GUARDING THE WHOLE CLASS: nine of its ten tests broke on that tree, and the tenth
#: (test_specs_carry_no_dash_and_no_private_term) was already skipping there on its own, because it
#: reads the private term list from HOME and the hermetic run gives it an empty one.
@unittest.skipUnless(IN_HUB, HUB_ONLY)
class Plan(unittest.TestCase):
    def test_the_generator_verifies_and_is_fresh(self):
        v = run([GEN, "--verify-only"])
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        c = run([GEN, "--check"])
        self.assertEqual(c.returncode, 0, c.stdout + c.stderr)

    def test_every_loop_sub_unit_passes_the_lane_rule(self):
        import plan_lanes
        bad = []
        for u in wbs()["units"]:
            if u["lane"] != "loop":
                continue
            spec = open(os.path.join(ROOT, u["spec"]), encoding="utf-8").read()
            for sid in u["sub_units"]:
                m = re.search(r"^### %s .*?\n\nFiles: (.*?)\.\n" % re.escape(sid), spec, re.M | re.S)
                files = re.findall(r"`([^`]+)` \((?:existing|NEW)\)", m.group(1)) if m else []
                got, why, _n = plan_lanes.lane(files)
                if not files or got != "LOOP":
                    bad.append("%s: %s %s" % (sid, got, why or "no Files line"))
        self.assertEqual(bad, [])

    def test_the_loop_scope_names_exactly_the_loop_units(self):
        d = wbs()
        loop = sorted(u["id"] for u in d["units"] if u["lane"] == "loop")
        scope = re.fullmatch(r"\^\((.*)\)\$", d["loop_scope"])
        self.assertIsNotNone(scope, d["loop_scope"])
        self.assertEqual(sorted(scope.group(1).split("|")), loop)

    def test_every_access_unit_closes_on_a_journey_row_after_jt0(self):
        d = wbs()
        units = {u["id"]: u for u in d["units"]}
        slipped = (d.get("slipped") or {}).get("units", {})
        #: An access unit missing from the plan is allowed ONLY when the plan's slipped record names it
        #: with a reason; a silent disappearance is still a failure (2026-10-29, D2's cut).
        bad = [i for i in ACCESS if i not in units and not (isinstance(slipped.get(i), dict) and slipped[i].get("reason"))]
        bad += [i for i in ACCESS if i in units and ("scripts/journey/run_journey.py --fresh-install" not in units[i]["done_check"]
                                                      or "JT0" not in units[i]["after"])]
        self.assertEqual(bad, [])

    def test_every_named_row_belongs_to_a_journey_jt0_walks(self):
        d = wbs()
        named = {}
        for u in d["units"]:
            m = re.search(r"--journey (J\d) --expect (\S+)", u["done_check"])
            if m:
                named.update({row: m.group(1) for row in m.group(2).split(",")})
        self.assertEqual(named, d["journey_rows"])
        self.assertEqual(sorted(j for j in set(named.values()) if j not in JT0_JOURNEYS), [])

    def test_no_unit_is_in_an_earlier_wave_than_its_prerequisites(self):
        units = {u["id"]: u for u in wbs()["units"]}
        bad = ["%s (wave %d) after %s (wave %d)" % (u["id"], u["wave"], d, units[d]["wave"])
               for u in units.values() for d in u["after"] if d in units and units[d]["wave"] > u["wave"]]
        self.assertEqual(bad, [])

    def test_no_carried_stamp_landing_got_weaker(self):
        old = {u["id"]: u for u in wbs(WBS_118)["units"]}
        bad = []
        for u in wbs()["units"]:
            src = old.get(u["id"]) or old.get(u["id"][:-1] if u["id"].endswith("H") else None)
            if src and (src.get("landing") == "stamp" or src.get("lane") == "kernel") and u["landing"] != "stamp" and u["lane"] != "loop":
                bad.append("%s: %s in 1.1.8, %s here" % (u["id"], src.get("landing"), u["landing"]))
        self.assertEqual(bad, [])

    def test_the_x01_cut_gate_is_planned(self):
        gates = {g["id"]: g for g in wbs().get("cut_gates", [])}
        self.assertIn("X01", gates)
        self.assertIn("NO-DATA is never a pass", gates["X01"]["rule"])

    def test_plan_lint_blocks_only_on_the_named_slice_wait(self):
        d = wbs()
        r = run(["scripts/loop/plan_lint.py", "--report", WBS, "--scope", d["loop_scope"]])
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out[-600:])
        summary = re.search(r"LINT SUMMARY .*\| units (\d+) ", out)
        self.assertIsNotNone(summary, "plan_lint printed no LINT SUMMARY")
        self.assertEqual(int(summary.group(1)), sum(1 for u in d["units"] if u["lane"] == "loop"))
        units = {u["id"]: u for u in d["units"]}
        rows = [l for l in out.splitlines() if re.search(r"\b(BLOCKING|NO-DATA)\b", l) and not l.startswith("LINT SUMMARY")]
        bad = [l for l in rows if not ("BRIEF-BUDGET" in l and "bm_store.py" in l
                                       and SLICE_WAIT in units.get(l.split()[2], {}).get("after", []))]
        self.assertEqual(bad, [])

    def test_specs_carry_no_dash_and_no_private_term(self):
        terms_path = os.path.expanduser("~/.brothersbe-private-names")
        try:
            terms = [t.strip().lower() for t in open(terms_path, encoding="utf-8") if t.strip() and not t.startswith("#")]
        except OSError:
            self.skipTest("NO-DATA: no private term list at %s" % terms_path)
        dash, hits = 0, 0
        for name in sorted(os.listdir(os.path.join(ROOT, SPEC_DIR))):
            path = os.path.join(ROOT, SPEC_DIR, name)
            if not (os.path.isfile(path) and name.endswith((".md", ".json", ".py"))):
                continue
            text = open(path, encoding="utf-8").read()
            dash += text.count(chr(0x2013)) + text.count(chr(0x2014))
            hits += sum(text.lower().count(t) for t in terms)
        self.assertEqual((dash, hits), (0, 0), "dashes, private term hits (terms never printed)")


@unittest.skipUnless(IN_HUB, HUB_ONLY)
class HarnessFirst(unittest.TestCase):
    def test_classes_red_while_open_green_when_done(self):
        self.assertEqual(group_problems("HarnessFirst"), [])


@unittest.skipUnless(IN_HUB, HUB_ONLY)
class HarnessCard(unittest.TestCase):
    def test_classes_red_while_open_green_when_done(self):
        self.assertEqual(group_problems("HarnessCard"), [])


@unittest.skipUnless(IN_HUB, HUB_ONLY)
class HarnessSafety(unittest.TestCase):
    def test_classes_red_while_open_green_when_done(self):
        self.assertEqual(group_problems("HarnessSafety"), [])


@unittest.skipUnless(IN_HUB, HUB_ONLY)
class HarnessVault(unittest.TestCase):
    def test_classes_red_while_open_green_when_done(self):
        self.assertEqual(group_problems("HarnessVault"), [])


@unittest.skipUnless(IN_HUB, HUB_ONLY)
class HarnessVRT1(unittest.TestCase):
    def test_vault_classes_follow_vrt1s_state(self):
        here = {u["id"]: u for u in wbs()["units"]}
        vrt1 = here.get("VRT1") or {u["id"]: u for u in wbs(WBS_118)["units"]}.get("VRT1")
        self.assertIsNotNone(vrt1, "VRT1 is in no plan in this tree")
        done = vrt1.get("state") == "DONE"
        self.assertEqual(red_or_green_problems("scripts/test_owner_paths.py", ("VaultRoot", "VaultWriters"), done), [])


@unittest.skipUnless(IN_HUB, HUB_ONLY)
class SliceClears(unittest.TestCase):
    def test_no_brief_budget_block_on_the_slice_waiters(self):
        r = run(["scripts/loop/plan_lint.py", "--report", WBS, "--scope", "^(CJK1|FOOT1|PAGE1)$"])
        out = r.stdout + r.stderr
        self.assertRegex(out, r"LINT SUMMARY", "plan_lint printed no LINT SUMMARY")
        self.assertEqual([l for l in out.splitlines() if "BRIEF-BUDGET" in l and "BLOCKING" in l], [])


@unittest.skipUnless(IN_HUB, HUB_ONLY)
class LaunchReady(unittest.TestCase):
    def test_every_after_release_id_is_done_in_its_own_plan(self):
        states, unread = {}, []
        for ref, path in (("hub/main", "docs/plan/BROTHER-1.1.7-WBS.json"), ("hub/main", WBS_118),
                          ("plan/1.1.8-line-2026-10-23", WBS_118),
                          ("hub/main", "docs/plan/specs/1.1.8/additions-117/ADDITIONS-1.1.7-WBS.json")):
            r = subprocess.run(["git", "show", "%s:%s" % (ref, path)], cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                unread.append(ref)
                continue
            for u in json.loads(r.stdout)["units"]:
                states[u["id"]] = u.get("state") if u.get("state") == "DONE" else states.get(u["id"], u.get("state"))
        waiting = sorted({"%s (%s)" % (d, states.get(d, "in no plan")) for u in wbs()["units"]
                          for d in u["after_release"] if states.get(d) != "DONE"})
        self.assertEqual((unread, waiting), ([], []), "refs unread, after_release ids not DONE")


def _full_unit(uid, after=(), owner_hand=False, done_check="python3 -B scripts/test_plan_119.py Plan",
               files=(("scripts/brother_run.py", "existing"),), harness=("scripts/test_plan_119.py",), **extra):
    """The least a unit needs to pass every check verify already runs, so a fixture built from it trips
    exactly the one new rule its test is about: one session lane sub unit over an existing file, whose
    done check runs a harness class that exists in this tree."""
    sub = {"id": uid + ".a", "title": "t", "files": list(files), "done_check": done_check, "reads": [],
           "harness": list(harness), "worker": None, "sigs": "", "behavior": "", "red": "", "reqs": [],
           "edges": "", "muts": [], "checks": {}}
    u = {"id": uid, "wave": 0, "lane": "session", "landing": "record", "after": list(after),
         "depends_on": list(after), "owner_hand": owner_hand, "done_check": done_check, "subs": [sub]}
    u.update(extra)
    return u


class Shape(unittest.TestCase):
    """The generator refuses a plan whose SHAPE cannot close inside its own window, and a done check that
    could never judge anything. Written 2026-10-29, red first, after four days in which 1.1.9 closed zero
    units while every structural check printed OK. Measured that day on the live plan: 24 open units on
    a critical path of 13 levels inside an 11 day window (plan_start 2026-10-26 to plan_end 2026-11-06);
    one owner hand unit, S9, with 7 direct dependents and no written contingency, so when his hand did
    not come nothing said what happened instead; and a sub done check, ACC1.a's, that named a class its
    file does not define, generated clean and erroring on every run. Each rule here is tested at the
    ENTRY POINT, verify, with a fixture that passes every older check, so a refusal is the new rule's.
    One fixture per guard; nothing here reads the live plan."""

    def setUp(self):
        self.gen = _generator()
        if self.gen is None:
            self.skipTest(HUB_ONLY)
        self.G = self.gen.G

    def _verify(self, units, landed=frozenset(), window_days=30):
        import inspect
        params = inspect.signature(self.G.verify).parameters
        self.assertIn("window_days", params,
                      "verify takes no window_days, so no plan is ever judged against its own cut band")
        return self.G.verify(units, {u["id"] for u in units}, "fixture", landed, window_days=window_days)

    def test_a_chain_longer_than_the_window_is_refused(self):
        us = [_full_unit("A"), _full_unit("B", ["A"]), _full_unit("C", ["B"])]
        with self.assertRaises(SystemExit) as caught:
            self._verify(us, window_days=2)
        said = str(caught.exception)
        for word in ("window", "A", "B", "C"):
            self.assertIn(word, said, "the refusal must name the window and every unit on the chain")

    def test_a_chain_inside_the_window_passes(self):
        us = [_full_unit("A"), _full_unit("B", ["A"]), _full_unit("C", ["B"])]
        self.assertIsNone(self._verify(us, window_days=3))

    def test_done_units_do_not_count_toward_the_chain(self):
        us = [_full_unit("A"), _full_unit("B", ["A"]), _full_unit("C", ["B"])]
        self.assertIsNone(self._verify(us, landed=frozenset({"A"}), window_days=2))

    def test_an_owner_hand_unit_mid_chain_needs_a_contingency(self):
        us = [_full_unit("A", owner_hand=True), _full_unit("B", ["A"])]
        with self.assertRaises(SystemExit) as caught:
            self._verify(us)
        said = str(caught.exception)
        self.assertIn("contingency", said)
        self.assertIn("A", said)

    def test_an_owner_hand_unit_with_a_contingency_passes(self):
        us = [_full_unit("A", owner_hand=True, contingency="A slips to the next release and B's edge to it is cut"),
              _full_unit("B", ["A"])]
        self.assertIsNone(self._verify(us))

    def test_a_blank_contingency_is_refused(self):
        us = [_full_unit("A", owner_hand=True, contingency="   "), _full_unit("B", ["A"])]
        with self.assertRaises(SystemExit) as caught:
            self._verify(us)
        self.assertIn("contingency", str(caught.exception))

    def test_an_owner_hand_leaf_needs_no_contingency(self):
        self.assertIsNone(self._verify([_full_unit("A", owner_hand=True)]))

    def test_a_done_check_naming_an_absent_class_is_refused(self):
        us = [_full_unit("A", done_check="python3 -B scripts/test_plan_119.py NoSuchClassAnywhere119")]
        with self.assertRaises(SystemExit) as caught:
            self._verify(us)
        said = str(caught.exception)
        self.assertIn("A.a", said)
        self.assertIn("NoSuchClassAnywhere119", said)

    def test_a_done_check_naming_an_absent_file_is_refused(self):
        us = [_full_unit("A", done_check="python3 -B scripts/test_nothing_here_119.py Plan",
                         harness=("scripts/test_nothing_here_119.py",))]
        with self.assertRaises(SystemExit) as caught:
            self._verify(us)
        self.assertIn("scripts/test_nothing_here_119.py", str(caught.exception))

    def test_a_harness_labelled_new_may_be_absent(self):
        us = [_full_unit("A", done_check="python3 -B scripts/test_nothing_here_119.py Plan",
                         harness=("scripts/test_nothing_here_119.py",),
                         files=(("scripts/test_nothing_here_119.py", "NEW"),))]
        self.assertIsNone(self._verify(us))

    def test_a_journey_row_no_journey_declares_is_refused(self):
        us = [_full_unit("A")]
        us[0]["done_check"] = ("python3 -B scripts/journey/run_journey.py --fresh-install --journey J1 "
                               "--expect Z.no-such-row-119")
        with self.assertRaises(SystemExit) as caught:
            self._verify(us)
        self.assertIn("Z.no-such-row-119", str(caught.exception))

    def test_a_journey_row_the_walker_declares_passes(self):
        us = [_full_unit("A")]
        us[0]["done_check"] = ("python3 -B scripts/journey/run_journey.py --fresh-install --journey J1 "
                               "--expect A.first-screen-paths-exist")
        self.assertIsNone(self._verify(us))


class Slip(unittest.TestCase):
    """A unit the owner cuts from the release LEAVES the plan with its reason, and every edge that named
    it is cut with the same reason, so the ship set verifies on its own. Written 2026-10-29, red first:
    the plan had no way to record a cut (D2, 2026-10-29: 13 units ship, 16 slip to 1.1.10) other than
    deleting rows by hand, which the generator would then call "after names X, in no plan"."""

    def setUp(self):
        self.gen = _generator()
        if self.gen is None:
            self.skipTest(HUB_ONLY)

    def test_a_slipped_unit_leaves_the_plan_and_its_edges_carry_the_reason(self):
        self.assertTrue(hasattr(self.gen, "apply_slips"), "the generator has no apply_slips")
        us = [_full_unit("A"), _full_unit("B", ["A"]), _full_unit("C", ["B"])]
        kept = self.gen.apply_slips(us, {"B": "slipped to 1.1.10 (fixture)"})
        self.assertEqual([u["id"] for u in kept], ["A", "C"])
        c = [u for u in kept if u["id"] == "C"][0]
        self.assertNotIn("B", c["after"])
        self.assertNotIn("B", c["depends_on"])
        self.assertEqual(c["cut_edges"].get("B"), "slipped to 1.1.10 (fixture)")

    def test_a_slip_naming_no_unit_is_refused(self):
        self.assertTrue(hasattr(self.gen, "apply_slips"), "the generator has no apply_slips")
        with self.assertRaises(SystemExit):
            self.gen.apply_slips([_full_unit("A")], {"ZZZ": "not in the plan"})

    def test_a_slip_with_no_reason_is_refused(self):
        self.assertTrue(hasattr(self.gen, "apply_slips"), "the generator has no apply_slips")
        with self.assertRaises(SystemExit):
            self.gen.apply_slips([_full_unit("A"), _full_unit("B")], {"B": "  "})


if __name__ == "__main__":
    unittest.main()
