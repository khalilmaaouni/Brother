#!/usr/bin/env python3
"""BASE14.a: the 1.1.4 plan lints clean and every sub unit has its section.

plan_problems is the one check. It runs the loop's own plan reader (lint_plan)
and then reads the plan JSON for the facts the lint does not hold: every spec
on disk, every sub unit heading, every done check accepted by the screen the
loop executes through, every section done check by the grader's OK_CMD, no
sub unit claimed twice. Anything it cannot read is a NO-DATA string, never a
clean list. Fixtures live in temporary directories only.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))
import grade_build  # noqa: E402
import plan_lint  # noqa: E402

WBS = "docs/plan/BROTHER-1.1.4-WBS.json"
MAX_SPEC_BYTES = 4 * 1024 * 1024


def plan_problems(plan_path: str, root: str) -> list:
    """Every reason the plan is not fit for the loop, as strings; [] only when every fact was read and held."""
    problems = []
    try:
        report = plan_lint.lint_plan(plan_path, root=root)
    except Exception as exc:  # a reader that crashes has not judged the plan
        return ["NO-DATA: the plan lint crashed: %s: %s" % (type(exc).__name__, exc)]
    if not isinstance(report, dict) or report.get("exit") == 3:
        reason = report.get("reason") if isinstance(report, dict) else "no report"
        return ["NO-DATA: the plan lint could not read the plan: %s" % reason]
    for f in report.get("findings") or []:
        if not isinstance(f, dict):
            problems.append("NO-DATA: a lint finding is not a record")
            continue
        if f.get("severity") in ("BLOCKING", "NO-DATA"):
            problems.append("%s %s %s %s: %s" % (f.get("severity"), f.get("unit"), f.get("sub"), f.get("rule"), f.get("text")))
    try:
        with open(plan_path, "rb") as fh:
            plan = json.loads(fh.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        return problems + ["NO-DATA: the plan JSON is unreadable: %s" % exc]
    units = plan.get("units") if isinstance(plan, dict) else None
    if not isinstance(units, list):
        return problems + ["NO-DATA: the plan units are not a list"]
    if not units:
        return problems + ["NO-DATA: the plan has no units"]
    seen = {}
    for unit in units:
        if not isinstance(unit, dict):
            problems.append("NO-DATA: a plan unit is not a record")
            continue
        uid = unit.get("id") if isinstance(unit.get("id"), str) and unit.get("id") else "-"
        if plan_lint.screen_done_check(unit.get("done_check")) is None:
            problems.append("%s: the unit done_check is refused by screen_done_check: %r" % (uid, unit.get("done_check")))
        subs = unit.get("sub_units")
        if not isinstance(subs, list) or not subs:
            problems.append("%s: the unit lists no sub units" % uid)
            subs = []
        for sub in subs:
            if not isinstance(sub, str) or not sub:
                problems.append("%s: a sub unit id is not text" % uid)
                continue
            if sub in seen:
                problems.append("%s: sub unit %s is also listed under %s" % (uid, sub, seen[sub]))
            seen.setdefault(sub, uid)
        spec = unit.get("spec")
        if not isinstance(spec, str) or not spec or os.path.isabs(spec) or ".." in spec.split("/"):
            problems.append("%s: the spec path is not a relative path inside the tree: %r" % (uid, spec))
            continue
        spec_file = os.path.join(root, spec)
        if not os.path.isfile(spec_file):
            problems.append("%s: the spec file %s is not on disk" % (uid, spec))
            continue
        try:
            if os.path.getsize(spec_file) > MAX_SPEC_BYTES:
                problems.append("NO-DATA: %s: the spec file is over the size cap" % uid)
                continue
            with open(spec_file, "rb") as fh:
                text = fh.read().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append("NO-DATA: %s: the spec file is unreadable: %s" % (uid, exc))
            continue
        for sub in subs:
            if not isinstance(sub, str) or not sub:
                continue
            if plan_lint._section_by_sub(text, sub) is None:
                problems.append("%s: sub unit %s has no heading in %s" % (uid, sub, spec))
                continue
            check = plan_lint._section_done_check(text, sub)
            if check is None:
                problems.append("%s: sub unit %s has no done check line" % (uid, sub))
            elif not grade_build.OK_CMD.match(check):
                problems.append("%s: the done check of %s is refused by OK_CMD: %r" % (uid, sub, check))
    return problems


SPEC_TEXT = """# ZZ1: a fixture unit

## 2. Sub-units

### ZZ1.a The fixture sub unit

Files: `scripts/test_zz1.py` (NEW).

Done check: `python3 -B scripts/test_zz1.py`
"""


def good_world(tmp: str) -> str:
    """A plan with one unit ZZ1 and sub unit ZZ1.a that satisfies every fact; returns the plan path."""
    os.makedirs(os.path.join(tmp, "docs", "plan", "specs"), exist_ok=True)
    with open(os.path.join(tmp, "docs", "plan", "specs", "ZZ1.md"), "w") as fh:
        fh.write(SPEC_TEXT)
    plan = {"units": [{
        "id": "ZZ1", "state": "OPEN", "title": "fixture", "spec": "docs/plan/specs/ZZ1.md",
        "sub_units": ["ZZ1.a"], "owns": ["scripts/test_zz1.py"], "after": [], "depends_on": [],
        "done_check": "python3 -B scripts/test_zz1.py",
    }]}
    path = os.path.join(tmp, "plan.json")
    write_plan(path, plan)
    return path


def write_plan(path, plan):
    with open(path, "w") as fh:
        json.dump(plan, fh)


def load_plan(path):
    with open(path) as fh:
        return json.load(fh)


class TestBASE14A(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.plan_path = good_world(self.tmp)

    def tearDown(self):
        self._td.cleanup()

    def edit(self, fn):
        plan = load_plan(self.plan_path)
        fn(plan)
        write_plan(self.plan_path, plan)

    def assertProblem(self, needle):
        probs = plan_problems(self.plan_path, self.tmp)
        self.assertTrue(any(needle in p for p in probs), "expected %r in %r" % (needle, probs))

    def test_the_good_fixture_is_clean(self) -> None:
        self.assertEqual(plan_problems(self.plan_path, self.tmp), [])

    # Hub only: docs/plan is not in the export allowlist, so the public tree runs every fixture case and skips this one.
    @unittest.skipUnless(os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs")), "docs/plan/specs is hub only")
    def test_the_real_plan_has_no_problem(self) -> None:
        self.assertEqual(plan_problems(os.path.join(ROOT, WBS), ROOT), [])

    def test_a_missing_spec_file_is_a_problem(self) -> None:
        os.remove(os.path.join(self.tmp, "docs", "plan", "specs", "ZZ1.md"))
        self.assertProblem("ZZ1: the spec file docs/plan/specs/ZZ1.md is not on disk")

    def test_a_missing_sub_unit_heading_is_a_problem(self) -> None:
        self.edit(lambda p: p["units"][0]["sub_units"].append("ZZ1.b"))
        self.assertProblem("ZZ1.b has no heading")

    def test_a_done_check_with_dash_c_is_refused(self) -> None:
        self.edit(lambda p: p["units"][0].update(done_check='python3 -c "import shutil"'))
        self.assertProblem("refused by screen_done_check")

    def test_a_section_done_check_that_is_a_chain_is_refused(self) -> None:
        with open(os.path.join(self.tmp, "docs", "plan", "specs", "ZZ1.md"), "w") as fh:
            fh.write(SPEC_TEXT.replace("`python3 -B scripts/test_zz1.py`",
                                       "`python3 -B scripts/test_zz1.py && python3 -B scripts/test_zz2.py`"))
        self.assertProblem("refused by OK_CMD")

    def test_a_blocking_lint_finding_is_a_problem(self) -> None:
        # A file labelled NEW that already exists is a BLOCKING lint finding (NEW-EXISTS).
        os.makedirs(os.path.join(self.tmp, "scripts"), exist_ok=True)
        with open(os.path.join(self.tmp, "scripts", "test_zz1.py"), "w") as fh:
            fh.write("# exists\n")
        self.assertProblem("BLOCKING")

    def test_an_unreadable_plan_is_no_data_not_clean(self) -> None:
        with open(self.plan_path, "w") as fh:
            fh.write("{not json")
        probs = plan_problems(self.plan_path, self.tmp)
        self.assertTrue(probs and probs[0].startswith("NO-DATA:"), probs)
        probs = plan_problems(self.tmp, self.tmp)  # a directory is not a plan
        self.assertTrue(probs and probs[0].startswith("NO-DATA:"), probs)

    def test_a_plan_with_no_units_is_not_clean(self) -> None:
        self.edit(lambda p: p.update(units=[]))
        self.assertProblem("NO-DATA: the plan has no units")

    def test_a_sub_unit_listed_twice_is_a_problem(self) -> None:
        def dup(p):
            other = dict(p["units"][0])
            other["id"] = "ZZ2"
            p["units"].append(other)
        self.edit(dup)
        self.assertProblem("also listed under ZZ1")

    def test_hostile_inputs_are_problems_not_crashes(self) -> None:
        self.edit(lambda p: p["units"][0].update(spec="../outside.md"))
        self.assertProblem("not a relative path")
        self.edit(lambda p: p["units"].append("not a record"))
        self.assertProblem("not a record")
        self.edit(lambda p: p["units"][0].update(done_check=7))
        self.assertProblem("refused by screen_done_check")


if __name__ == "__main__":
    unittest.main()
