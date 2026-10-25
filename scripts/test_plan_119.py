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


def red_or_green_problems(path, classes, done):
    """A class must fail by an assertion while its work is open, and pass once it is DONE."""
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
            problems.append("%s %s: green while its work is open" % (path, cls))
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
        return ["no loop sub unit runs %s" % ", ".join(files)]
    for (path, done), classes in sorted(found.items()):
        problems += red_or_green_problems(path, classes, done)
    return problems


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
        units = {u["id"]: u for u in wbs()["units"]}
        bad = [i for i in ACCESS if "scripts/journey/run_journey.py --fresh-install" not in units[i]["done_check"]
               or "JT0" not in units[i]["after"]]
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


if __name__ == "__main__":
    unittest.main()
