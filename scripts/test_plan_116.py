#!/usr/bin/env python3
"""BASE16: the 1.1.6 plan lints clean, every done check is one command, the loop lane owns nothing the landing holds,
the loop lane specs score 9 or more, and the base tree's baseline record is honest.

One judge per fact, each reused from the 1.1.5 suite where it already exists:
  plan_problems        test_plan_115's judge plus one rule: the unit done check matches grade_build.OK_CMD (U1).
  loop_lane_problems   a loop lane unit owning a path under scripts/loop/, in the deploy's CANDIDATE tuples, or one
                       plan_lint.protected_reason refuses (D7: the landing holds those, so the loop could never land it).
  spec_floor_problems  scripts/loop/spec_score.py --no-claim over the loop lane units (U4).
  baseline_problems    test_baseline_115's judge plus the two codex rows and the boot line the gate cannot supply.
Anything unreadable is a NO-DATA string, never a clean list. Fixtures live in temporary directories only.
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))   # the loop's own grade_build and plan_lint win
import grade_build  # noqa: E402
import plan_lint  # noqa: E402
import test_baseline_115 as B15  # noqa: E402
import test_plan_115 as P15  # noqa: E402

WBS = "docs/plan/BROTHER-1.1.6-WBS.json"
RECORD = "docs/plan/specs/1.1.6/BASELINE-1.1.6.md"
PLAN_BASE = "96bfb9299"
LOOP_PREFIX = "scripts/loop/"
CODEX_ROWS = ("codex-package-self", "codex-smoke-self")
LISTS = ("CANDIDATE_ENTRIES", "CANDIDATE_DATA")
HUB_ONLY = os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs"))


def _load(plan_path):
    try:
        with open(plan_path, "rb") as fh:
            plan = json.loads(fh.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return None
    return plan if isinstance(plan, dict) and isinstance(plan.get("units"), list) else None


def plan_problems(plan_path: str, root: str) -> list:
    """test_plan_115's problems, plus every unit done check that is not ONE command the landing gate runs."""
    problems = list(P15.plan_problems(plan_path, root))
    plan = _load(plan_path)
    for unit in (plan or {}).get("units") or []:
        if not isinstance(unit, dict):
            continue
        dc = unit.get("done_check")
        if not isinstance(dc, str) or not grade_build.OK_CMD.match(dc):
            problems.append("%s: the unit done_check is not one command OK_CMD accepts: %r" % (unit.get("id"), dc))
    return problems


def held_set(root: str) -> tuple:
    """(the deploy's named files, "") from scripts/loop/deploy_stamped.py, or (None, reason). Never an empty set for a
    file that cannot be read: an unread hold set would let every loop lane path through."""
    path = os.path.join(root, "scripts", "loop", "deploy_stamped.py")
    try:
        with open(path, "rb") as fh:
            tree = ast.parse(fh.read().decode("utf-8"))
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and getattr(node.targets[0], "id", None) in LISTS:
                found[node.targets[0].id] = ast.literal_eval(node.value)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
        return None, "NO-DATA: %s cannot be read as literal tuples: %s" % (path, exc)
    if set(found) != set(LISTS) or not all(isinstance(v, tuple) and all(isinstance(p, str) for p in v) for v in found.values()):
        return None, "NO-DATA: %s no longer carries %s as literal tuples" % (path, " and ".join(LISTS))
    return set(found["CANDIDATE_ENTRIES"]) | set(found["CANDIDATE_DATA"]), ""


def loop_lane_problems(plan: dict, held: set, protected) -> list:
    """Every loop lane unit path the landing holds or refuses; a missing hold set is NO-DATA, never clean."""
    if held is None:
        return ["NO-DATA: the deploy's named files are unknown, so no loop lane path can be judged"]
    if not isinstance(plan, dict) or not isinstance(plan.get("units"), list):
        return ["NO-DATA: the plan units are not a list"]
    problems = []
    for unit in plan["units"]:
        if not isinstance(unit, dict) or unit.get("lane") != "loop":
            continue
        owns = unit.get("owns")
        if not isinstance(owns, list) or not owns:
            problems.append("%s: a loop lane unit with no owns cannot be judged" % unit.get("id"))
            continue
        for p in owns:
            if not isinstance(p, str) or not p:
                problems.append("%s: an owns entry is not text" % unit.get("id"))
                continue
            norm = os.path.normpath(p)
            if norm.startswith(LOOP_PREFIX):
                problems.append("%s: %s is under scripts/loop/, which the landing holds" % (unit.get("id"), norm))
            elif norm in held:
                problems.append("%s: %s is one of the deploy's named files, which the landing holds" % (unit.get("id"), norm))
            else:
                why = protected(norm)
                if why:
                    problems.append("%s: %s is %s" % (unit.get("id"), norm, why))
    return problems


def spec_floor_problems(root: str, plan_rel: str, units: list, floor: int = 9) -> list:
    """The sub units of `units` the scorer puts under `floor`; a scorer that cannot run or scores nothing is NO-DATA."""
    scorer = os.path.join(root, "scripts", "loop", "spec_score.py")
    if not os.path.isfile(scorer):
        return ["NO-DATA: %s is not on disk" % scorer]
    env = dict(os.environ, BROTHER_WBS=plan_rel)
    try:
        r = subprocess.run([sys.executable, "-B", scorer] + list(units) + ["--no-claim", "--floor", str(floor)],
                           cwd=root, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return ["NO-DATA: the scorer could not run: %s" % exc]
    if r.returncode == 0:
        return []
    if r.returncode != 1:
        return ["NO-DATA: the scorer exited %d: %s" % (r.returncode, (r.stderr or r.stdout).strip()[-300:])]
    under = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].endswith("/10") and parts[1][:-3].isdigit() and int(parts[1][:-3]) < floor:
            under.append(line.strip())
    return under or ["the scorer exited 1 and named no sub unit: %s" % r.stdout.strip()[-300:]]


def split_record(text: str) -> tuple:
    """(base text for test_baseline_115, codex rows, boot text or None, bad lines)."""
    base, codex, boot, bad = [], [], None, []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("codex |"):
            f = [x.strip() for x in s.split(" | ")]
            m = B15.EXIT_RE.match(f[3]) if len(f) == 5 else None
            if not m or f[4] not in B15.RAW_STATES:
                bad.append("unreadable codex line: %s" % s)
                continue
            codex.append({"name": f[1], "verdict": f[2], "exit": int(m.group(1)), "raw": f[4]})
        elif s.startswith("boot |"):
            if boot is not None:
                bad.append("a second boot line: %s" % s)
            boot = s[len("boot |"):].strip()
        else:
            base.append(line)
    return "\n".join(base), codex, boot, bad


def baseline_problems(text: str, merge_base: str, names: list) -> list:
    """Every reason the 1.1.6 record is not an honest baseline; [] only when every fact holds."""
    if not isinstance(text, str) or not text.strip():
        return ["NO-DATA: the record is empty"]
    base, codex, boot, bad = split_record(text)
    problems = list(B15.record_problems(B15.parse_record(base), merge_base, names)) + bad
    seen = {}
    for row in codex:
        if row["name"] not in CODEX_ROWS:
            problems.append("a codex row outside %s: %s" % (CODEX_ROWS, row["name"]))
            continue
        if row["name"] in seen:
            problems.append("codex row repeated: %s" % row["name"])
        seen[row["name"]] = row
        if row["verdict"] not in B15.VERDICTS:
            problems.append("codex row %s counts %r, outside PASS, FAIL, NO-DATA" % (row["name"], row["verdict"]))
        elif row["verdict"] != B15.counted_for(row["exit"], row["raw"]):
            problems.append("codex row %s counts %s but exit %d with %s counts %s" % (
                row["name"], row["verdict"], row["exit"], row["raw"], B15.counted_for(row["exit"], row["raw"])))
    for name in CODEX_ROWS:
        if name not in seen:
            problems.append("no codex row for %s" % name)
    if not boot:
        problems.append("no boot line (sysctl kern.boottime)")
    return problems


CODEX_GOOD = ["codex | codex-package-self | PASS | exit 0 | PASS", "codex | codex-smoke-self | NO-DATA | exit 0 | SKIPPED",
              "boot | { sec = 1760541750, usec = 489000 }"]


class TestBASE16A(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.plan_path = P15.good_world(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def test_the_good_fixture_is_clean(self) -> None:
        self.assertEqual(plan_problems(self.plan_path, self.tmp), [])

    def test_a_unit_done_check_chain_is_refused(self) -> None:
        plan = P15.load_plan(self.plan_path)
        plan["units"][0]["done_check"] = "python3 -B scripts/test_zz1.py && python3 -B scripts/test_zz1.py"
        P15.write_plan(self.plan_path, plan)
        probs = plan_problems(self.plan_path, self.tmp)
        self.assertTrue(any("not one command OK_CMD accepts" in p for p in probs), probs)

    def test_a_loop_lane_unit_inside_the_hold_set_is_refused(self) -> None:
        first_draft = {"units": [{"id": "S3", "lane": "loop", "owns": ["scripts/loop/runner_pool.py"]}]}
        probs = loop_lane_problems(first_draft, set(), lambda p: "")
        self.assertTrue(any("under scripts/loop/" in p for p in probs), probs)

    def test_a_named_deploy_file_is_refused(self) -> None:
        plan = {"units": [{"id": "ZZ1", "lane": "loop", "owns": ["scripts/close_unit.py"]}]}
        probs = loop_lane_problems(plan, {"scripts/close_unit.py"}, lambda p: "")
        self.assertTrue(any("deploy's named files" in p for p in probs), probs)

    def test_a_protected_path_is_refused_and_other_lanes_are_not_judged(self) -> None:
        plan = {"units": [{"id": "ZZ1", "lane": "loop", "owns": ["scripts/cut.py"]},
                          {"id": "ZZ2", "lane": "builder-1", "owns": ["scripts/loop/x.py"]}]}
        probs = loop_lane_problems(plan, set(), lambda p: "a path the landing trusts" if p == "scripts/cut.py" else "")
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("ZZ1: scripts/cut.py", probs[0])

    def test_an_unreadable_hold_set_is_no_data(self) -> None:
        os.makedirs(os.path.join(self.tmp, "scripts", "loop"))
        with open(os.path.join(self.tmp, "scripts", "loop", "deploy_stamped.py"), "w") as fh:
            fh.write("CANDIDATE_ENTRIES = (\n")
        held, why = held_set(self.tmp)
        self.assertIsNone(held)
        self.assertTrue(why.startswith("NO-DATA"), why)
        self.assertTrue(loop_lane_problems({"units": []}, held, lambda p: "")[0].startswith("NO-DATA"))
        self.assertIsNone(held_set(os.path.join(self.tmp, "absent"))[0])

    def test_the_real_hold_set_reads(self) -> None:
        held, why = held_set(ROOT)
        self.assertEqual(why, "")
        self.assertIn("scripts/loop/sandbox.sb", held)

    @unittest.skipUnless(HUB_ONLY, "docs/plan/specs is hub only")
    def test_the_real_plan_has_no_problem(self) -> None:
        self.assertEqual(plan_problems(os.path.join(ROOT, WBS), ROOT), [])

    @unittest.skipUnless(HUB_ONLY, "docs/plan/specs is hub only")
    def test_the_real_loop_lane_is_outside_the_hold_set(self) -> None:
        plan = _load(os.path.join(ROOT, WBS))
        held, why = held_set(ROOT)
        self.assertEqual(why, "")
        self.assertEqual([u["id"] for u in plan["units"] if u.get("lane") == "loop"], ["C1", "C2", "PH1", "SG1"])
        self.assertEqual(loop_lane_problems(plan, held, plan_lint.protected_reason), [])

    @unittest.skipUnless(HUB_ONLY, "docs/plan/specs is hub only")
    def test_the_loop_lane_scores_nine(self) -> None:
        self.assertEqual(spec_floor_problems(ROOT, WBS, ["C1", "C2", "PH1", "SG1"]), [])

    @unittest.skipUnless(HUB_ONLY, "docs/plan/specs is hub only")
    def test_a_scorer_that_scores_nothing_is_no_data(self) -> None:
        probs = spec_floor_problems(ROOT, WBS, ["ZZ9"])
        self.assertTrue(probs and probs[0].startswith("NO-DATA"), probs)
        self.assertTrue(spec_floor_problems(self.tmp, WBS, ["C1"])[0].startswith("NO-DATA"))


class TestBASE16B(unittest.TestCase):
    def text(self, extra=None, drop=()):
        lines = [x for x in B15.good_text().splitlines() + CODEX_GOOD if not any(d in x for d in drop)]
        return "\n".join(lines + list(extra or [])) + "\n"

    def problems(self, text):
        return baseline_problems(text, B15.SHA, B15.NAMES)

    def test_a_good_record_is_clean(self) -> None:
        self.assertEqual(self.problems(self.text()), [])

    def test_a_record_without_the_codex_rows_is_refused(self) -> None:
        probs = self.problems(self.text(drop=("codex |",)))
        self.assertIn("no codex row for codex-package-self", probs)
        self.assertIn("no codex row for codex-smoke-self", probs)

    def test_a_skipped_codex_row_counted_pass_is_refused(self) -> None:
        bad = self.text(drop=("codex-smoke-self",), extra=["codex | codex-smoke-self | PASS | exit 0 | SKIPPED"])
        self.assertTrue(any("codex-smoke-self counts PASS" in p for p in self.problems(bad)))

    def test_a_record_without_boot_is_refused(self) -> None:
        self.assertIn("no boot line (sysctl kern.boottime)", self.problems(self.text(drop=("boot |",))))

    def test_hostile_codex_rows_are_refused(self) -> None:
        probs = self.problems(self.text(extra=["codex | codex-other | PASS | exit 0 | PASS",
                                               "codex | codex-package-self | PASS | exit 0 | PASS",
                                               "codex | codex-smoke-self | PASS"]))
        self.assertTrue(any("outside" in p for p in probs), probs)
        self.assertTrue(any("repeated" in p for p in probs), probs)
        self.assertTrue(any("unreadable codex line" in p for p in probs), probs)
        self.assertEqual(self.problems(""), ["NO-DATA: the record is empty"])

    @unittest.skipUnless(HUB_ONLY, "docs/plan/specs is hub only")
    def test_the_real_baseline_is_honest(self) -> None:
        path = os.path.join(ROOT, RECORD)
        if not os.path.exists(path):
            states = {u.get("id"): u.get("state") for u in (_load(os.path.join(ROOT, WBS)) or {}).get("units") or []}
            if states.get("BASE16") == "DONE":
                self.fail("BASE16 is DONE but %s is not on disk" % RECORD)
            self.skipTest("NO-DATA: %s is not written yet; BASE16 stays OPEN until the session records it" % RECORD)
        text, why = B15.read_record(path)
        self.assertEqual(why, "")
        with open(os.path.join(ROOT, B15.GATE), encoding="utf-8") as fh:
            names = B15.gate_check_names(fh.read())
        self.assertEqual(baseline_problems(text, B15.plan_merge_base(ROOT, PLAN_BASE), names), [])


if __name__ == "__main__":
    unittest.main()
