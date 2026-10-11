#!/usr/bin/env python3
"""The loop reads ONE plan, named by BROTHER_WBS through scripts/loop/plan_path.py, with the 1.1.0 plan as the default.

Until 2026-10-10 the path docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json was spelled in about 45 loop tools, so the loop could
only ever run that plan. Every reader, writer, scorer, closer and finisher now asks plan_path.selected(). This suite
holds that in place, one fixture per guard:

  * the default when BROTHER_WBS is unset or blank; the environment selects; each invalid value is refused with its
    own reason (absolute, a .. segment, outside docs/plan/, not .json, an empty or . segment) and never replaced by
    the default; the CLI prints the path (exit 0) or the reason on stderr (exit 2);
  * a source scan: no non test file under scripts/ carries the 1.1.0 literal outside plan_path.py and ALLOWLIST, and
    every ALLOWLIST entry still exists and still carries it (so the list cannot rot);
  * the converted modules, imported with BROTHER_WBS pointed at a fixture plan in a temporary tree, read THAT plan: the
    import time values, runner_pool's and repair_wave's GENERATED tuples, and two entry points run read only in a tree
    that holds no default plan at all;
  * the intake: prepare --plan records the plan, prints it, exports it shell quoted into launch-env.sh; refuses a
    colliding unit id by name, an unreadable default plan, a plan the tree lacks, and one with no units;
  * the writer: plan_store.update_units (the one function close_unit and land_batch write the plan through) refuses a
    unit id the selected plan does not hold and leaves the file byte for byte as it was.

Every fixture lives in a temporary tree and a throwaway HOME under ~/.claude/brother-scratch (never /tmp, never the
real ~/.claude/evidence). Run: python3 -B scripts/loop/test_loop_plan_selection.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))           # scripts/loop
SCRIPTS = os.path.dirname(HERE)                               # scripts
REPO = os.path.dirname(SCRIPTS)
sys.path.insert(0, HERE)
import plan_path as PP  # noqa: E402
import plan_store  # noqa: E402

LITERAL = "BROTHER-1.1.0-LAUNCH-WBS"
SCRATCH = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")

#: Files that keep the 1.1.0 literal on purpose. Every entry names its reason; an entry whose file no longer carries
#: the literal, or no longer exists, fails the scan, so this list cannot outlive its reasons.
ALLOWLIST = {
    "scripts/loop/plan_path.py": "the selector itself: DEFAULT is the one place the historical path is spelled",
    "scripts/gen_launch_board.py": "reports on the 1.1.0 launch specifically, never on a selected plan",
    "scripts/gen_release_plan.py": "reports on the 1.1.0 launch specifically, never on a selected plan",
    "scripts/donecheck_L5.py": "the L5 done check of the 1.1.0 launch specifically",
    "scripts/loop/abc/pick_sample.py": "a finished experiment (A/B/C arms, 2026-09-23), kept as run",
    "scripts/loop/abc/score_arms.py": "a finished experiment (A/B/C arms, 2026-09-23), kept as run",
    "scripts/loop/abc/start_arm_c.sh": "a finished experiment (A/B/C arms, 2026-09-23), kept as run",
    "scripts/loop/wip_status.py": "docstring only: names the ACC3 row of the historical plan as prose",
    "scripts/loop/plan_lint.py": "docstring only: the usage example names the historical plan as prose",
    "scripts/loop/loop_canary.py": "N2: FIXTURE_PLAN, the plan the canary's fixtures were specified in, never the run's",
}

#: importable modules whose PLAN, PLAN_PATH or LINT_PLAN value is computed at import; runner_pool and repair_wave carry
#: the selection in GENERATED and are checked on their own below
CONVERTED = ["grade_build", "unit_ledger", "land_batch", "loop_done", "salvage", "check_wave",
             "loop_intake", "loop_report", "pass_digest", "spec_wave", "judge_calibrate", "diag_brief"]

FIXTURE_PLAN = {"units": [{"id": "ZZ1", "title": "fixture one", "state": "OPEN", "sub_units": ["ZZ1.a", "ZZ1.b"], "evidence": ""},
                          {"id": "ZZ2", "title": "fixture two", "state": "OPEN", "sub_units": ["ZZ2.a"], "evidence": ""}]}
DEFAULT_PLAN = {"units": [{"id": "M1", "state": "OPEN", "sub_units": ["M1.a"], "evidence": ""},
                          {"id": "U8", "state": "OPEN", "sub_units": ["U8.1", "U8.2"], "evidence": ""},
                          {"id": "D3", "state": "DONE", "sub_units": ["D3.1"], "evidence": "x"}]}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def text(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Fixture(object):
    """A temporary launch tree with docs/plan/FIX.json (and, unless told otherwise, the default plan too) plus a
    throwaway HOME, both under ~/.claude/brother-scratch."""

    def __init__(self, case, with_default=True, default_body=None):
        os.makedirs(SCRATCH, exist_ok=True)
        self.root = tempfile.mkdtemp(prefix="plan-selection-", dir=SCRATCH)
        case.addCleanup(shutil.rmtree, self.root, True)
        self.tree = os.path.join(self.root, "tree")
        self.home = os.path.join(self.root, "home")
        os.makedirs(os.path.join(self.home, ".claude", "evidence", "unit-runs"))
        os.makedirs(os.path.join(self.tree, "docs", "plan", "specs"))
        self.fix = "docs/plan/FIX.json"
        write(os.path.join(self.tree, self.fix), json.dumps(FIXTURE_PLAN, indent=1))
        if with_default:
            write(os.path.join(self.tree, PP.DEFAULT), default_body if default_body is not None else json.dumps(DEFAULT_PLAN, indent=1))

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_") and k not in ("HOME", "PYTHONPATH")}
        env.update(HOME=self.home, BROTHER_WBS=self.fix, PYTHONDONTWRITEBYTECODE="1")
        env.update(extra)
        return env

    def run(self, argv, timeout=300, **extra):
        return subprocess.run(argv, cwd=self.tree, env=self.env(**extra), capture_output=True, text=True, timeout=timeout)


class Selector(unittest.TestCase):
    def test_default_when_unset(self):
        self.assertEqual(PP.selected({}), PP.DEFAULT)
        self.assertEqual(PP.DEFAULT, "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json")

    def test_default_when_blank(self):
        self.assertEqual(PP.selected({PP.ENV: ""}), PP.DEFAULT)
        self.assertEqual(PP.selected({PP.ENV: "   "}), PP.DEFAULT)

    def test_environment_selects_and_strips(self):
        self.assertEqual(PP.selected({PP.ENV: "docs/plan/BROTHER-1.1.1-WBS.json"}), "docs/plan/BROTHER-1.1.1-WBS.json")
        self.assertEqual(PP.selected({PP.ENV: "  docs/plan/X.json\n"}), "docs/plan/X.json")
        self.assertEqual(PP.selected({PP.ENV: "docs/plan/sub/X.json"}), "docs/plan/sub/X.json")

    def test_absolute_path_refused(self):
        with self.assertRaisesRegex(ValueError, "absolute path"):
            PP.selected({PP.ENV: "/tmp/docs/plan/x.json"})

    def test_climbing_segment_refused(self):
        with self.assertRaisesRegex(ValueError, r"\.\. segment"):
            PP.selected({PP.ENV: "docs/plan/../plan/x.json"})

    def test_outside_docs_plan_refused(self):
        with self.assertRaisesRegex(ValueError, "not under docs/plan/"):
            PP.selected({PP.ENV: "docs/other/x.json"})
        with self.assertRaisesRegex(ValueError, "not under docs/plan/"):
            PP.selected({PP.ENV: "docs/plan/"})

    def test_not_json_refused(self):
        with self.assertRaisesRegex(ValueError, r"does not name a \.json file"):
            PP.selected({PP.ENV: "docs/plan/x.txt"})
        with self.assertRaisesRegex(ValueError, r"does not name a \.json file"):
            PP.selected({PP.ENV: "docs/plan/.json"})

    def test_empty_or_dot_segment_refused(self):
        with self.assertRaisesRegex(ValueError, "not a plain relative path"):
            PP.selected({PP.ENV: "docs/plan//x.json"})
        with self.assertRaisesRegex(ValueError, "not a plain relative path"):
            PP.selected({PP.ENV: "docs/plan/./x.json"})

    def test_invalid_is_never_the_default(self):
        """The guard the whole design rests on: a bad value blocks, it does not fall back."""
        for bad in ("/abs.json", "docs/plan/../x.json", "x.json", "docs/plan/x", "docs/plan//x.json"):
            with self.assertRaises(ValueError, msg=bad):
                PP.selected({PP.ENV: bad})

    def test_cli_prints_the_selection(self):
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "plan_path.py")], capture_output=True, text=True,
                           env=dict(os.environ, BROTHER_WBS="docs/plan/BROTHER-1.1.1-WBS.json"))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "docs/plan/BROTHER-1.1.1-WBS.json"), r.stderr)
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "plan_path.py")], capture_output=True, text=True,
                           env={k: v for k, v in os.environ.items() if k != "BROTHER_WBS"})
        self.assertEqual((r.returncode, r.stdout.strip()), (0, PP.DEFAULT), r.stderr)

    def test_cli_refuses_on_stderr_exit_2(self):
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "plan_path.py")], capture_output=True, text=True,
                           env=dict(os.environ, BROTHER_WBS="/etc/x.json"))
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("PLAN REFUSED", r.stderr)
        self.assertIn("absolute path", r.stderr)


class SourceScan(unittest.TestCase):
    def files(self):
        out = []
        for base, dirs, names in os.walk(SCRIPTS):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for n in names:
                if not n.endswith((".py", ".sh")) or n.startswith("test_"):
                    continue
                out.append(os.path.relpath(os.path.join(base, n), REPO))
        return sorted(out)

    def test_no_literal_outside_the_selector_and_the_allowlist(self):
        hits = []
        for rel in self.files():
            body = text(os.path.join(REPO, rel))   # a file that cannot be read fails the scan: unread is not clean
            if LITERAL in body and rel not in ALLOWLIST:
                hits.append(rel)
        self.assertEqual(hits, [], "the 1.1.0 plan path is spelled outside plan_path.py: route through plan_path.selected() or allowlist with a reason")

    def test_the_allowlist_cannot_rot(self):
        for rel, reason in sorted(ALLOWLIST.items()):
            self.assertTrue(reason.strip(), rel)
            path = os.path.join(REPO, rel)
            self.assertTrue(os.path.isfile(path), "%s is allowlisted but does not exist: drop the entry" % rel)
            self.assertIn(LITERAL, text(path), "%s no longer carries the literal: drop the entry" % rel)

    def test_the_scan_can_fail(self):
        """A scan that cannot go red proves nothing: a planted literal in a scratch copy of scripts/ is found."""
        d = tempfile.mkdtemp(prefix="plan-scan-", dir=SCRATCH if os.path.isdir(SCRATCH) else None)
        self.addCleanup(shutil.rmtree, d, True)
        write(os.path.join(d, "scripts", "loop", "planted.py"), 'PLAN = "docs/plan/%s.json"\n' % LITERAL)
        found = [os.path.join(b, n) for b, _, ns in os.walk(os.path.join(d, "scripts")) for n in ns if LITERAL in text(os.path.join(b, n))]
        self.assertEqual(len(found), 1)


IMPORT_PROBE = r"""
import json, os, sys
sys.path.insert(0, %(loop)r)
mods = %(mods)r
out = {}
for name in mods:
    m = __import__(name)
    out[name] = getattr(m, "PLAN", None) or getattr(m, "PLAN_PATH", None) or getattr(m, "LINT_PLAN", None)
import runner_pool
out["runner_pool.GENERATED"] = runner_pool.GENERATED[-1]
import grade_build
out["protected"] = [p for p in grade_build.PROTECTED_FILES if p.endswith(".json") or p.endswith("plan_path.py")]
print(json.dumps(out))
"""



class ConvertedModulesReadTheSelectedPlan(unittest.TestCase):
    def test_import_time_values_follow_the_environment(self):
        f = Fixture(self)
        r = f.run([sys.executable, "-B", "-c", IMPORT_PROBE % {"loop": HERE, "mods": CONVERTED}])
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        for name in CONVERTED:
            self.assertEqual(got[name], f.fix, name)
        self.assertEqual(got["runner_pool.GENERATED"], f.fix)
        self.assertIn("docs/plan/fix.json", got["protected"], "the selected plan is protected from builds")
        self.assertIn(PP.DEFAULT.lower(), got["protected"], "the historical plan stays protected")
        self.assertIn("scripts/loop/plan_path.py", got["protected"], "the selector is protected from builds")

    def test_repair_wave_generated_tuple_names_the_selected_plan(self):
        """repair_wave.py parses argv at import, so its GENERATED assign is read from the source: a tuple whose last
        element is the call _PP.selected() and whose string elements never spell the historical plan. Nothing is
        executed, so no eval; the module's own import of plan_path is checked beside it."""
        import ast
        src = text(os.path.join(HERE, "repair_wave.py"))
        tree = ast.parse(src)
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "GENERATED")
        self.assertIsInstance(node.value, ast.Tuple)
        last = node.value.elts[-1]
        self.assertIsInstance(last, ast.Call, "GENERATED's last element is the selector call, not a literal")
        self.assertEqual((last.func.value.id, last.func.attr), ("_PP", "selected"))
        strings = [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        self.assertFalse(any(LITERAL in s for s in strings), strings)
        self.assertTrue(any(isinstance(n, ast.Import) and any(a.name == "plan_path" and a.asname == "_PP" for a in n.names) for n in tree.body))

    def test_an_invalid_selection_blocks_the_import(self):
        f = Fixture(self)
        r = f.run([sys.executable, "-B", "-c", IMPORT_PROBE % {"loop": HERE, "mods": ["grade_build"]}], BROTHER_WBS="/etc/x.json")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("absolute path", r.stderr)

    def test_loop_done_runs_against_a_tree_with_no_default_plan(self):
        """An entry point: loop_done.py --scope . in a tree where ONLY the fixture plan exists answers one of its
        documented codes with no traceback, which it could not do if anything in it still opened the default."""
        f = Fixture(self, with_default=False)
        r = f.run([sys.executable, "-B", os.path.join(HERE, "loop_done.py"), "--scope", "."])
        self.assertIn(r.returncode, (0, 1), r.stdout[-2000:] + r.stderr[-2000:])
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn(LITERAL, r.stdout + r.stderr)

    def test_runner_pool_dry_runs_against_a_tree_with_no_default_plan(self):
        f = Fixture(self, with_default=False)
        r = f.run([sys.executable, "-B", os.path.join(HERE, "runner_pool.py"), "--dry"])
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn(LITERAL, r.stdout + r.stderr)


def _intake():
    import loop_intake
    return loop_intake


def _roles_and_reg():
    reg = {"strong": {"transport": "claude", "privacy": "private", "kinds": {"build", "grade", "plan", "prose"}, "quality": {"build": 9, "grade": 9}}}
    base = {"does": "x", "when": "inside", "kind": "build", "content": "public", "must_be_chosen": False, "default": "strong"}
    roles = {"worker": dict(base, setting="BROTHER_PIN_MODEL"), "finisher": dict(base, when="after_run", content="private", must_be_chosen=True, default=None)}
    return roles, reg


def _good_probes():
    import datetime
    noon = datetime.datetime(2026, 1, 1, 12, 0)
    ok_reach = lambda plan: [{"status": "OK", "role": r, "model": m, "program": "/p", "version": "1.0", "cause": "fixture", "cached": False, "binding": None} for r, m in plan]
    return {"reach": ok_reach, "canary": lambda: ("OK", "went end to end"), "alive": lambda: ("OK", "nothing alive"), "lease": lambda: ("OK", "free"),
            "parity": lambda: ("OK", "equal"), "switch": lambda: ("OK", "unset"), "tree": lambda: ("OK", "clean"), "digest": lambda: ("OK", "answers"),
            "done": lambda: ("OK", "answers"), "salvage": lambda: ("OK", "answers"), "pool": lambda: ("OK", "answers"), "lint": lambda: ("OK", "clean"),
            "headroom": lambda: 50.0, "hold": lambda: False, "now": lambda: noon}


class IntakePlan(unittest.TestCase):
    def prepare(self, f, plan, scope=None, env=None):
        LI = _intake()
        roles, reg = _roles_and_reg()
        return LI.prepare({"finisher": "strong"}, "18:00", 10.0, scope, roles, reg, _good_probes(), plan=plan, tree_root=f.tree,
                          env={} if env is None else env)

    def refusals(self, rec):
        return [l for l in rec["lines"] if l.startswith("REFUSED")]

    def test_plan_is_recorded_printed_and_exported(self):
        f = Fixture(self)
        rec, lines = self.prepare(f, f.fix)
        self.assertEqual(rec["verdict"], "READY", "\n".join(lines))
        self.assertEqual(rec["plan"], f.fix)
        self.assertIn("PLAN     docs/plan/FIX.json", lines)
        roles, _ = _roles_and_reg()
        env_text = _intake().launch_env(rec, roles, authority=lambda key: "off")
        self.assertIn("\nexport BROTHER_WBS=docs/plan/FIX.json\n", env_text)

    def test_export_is_shell_quoted(self):
        LI = _intake()
        roles, _ = _roles_and_reg()
        rec = {"at": "t", "verdict": "READY", "roles": {}, "plan": "docs/plan/odd name.json"}
        self.assertIn("export BROTHER_WBS='docs/plan/odd name.json'", LI.launch_env(rec, roles, authority=lambda key: "off"))
        rec["plan"] = "/abs/docs/plan/x.json"   # a record whose plan the selector refuses exports nothing, and says so
        text = LI.launch_env(rec, roles, authority=lambda key: "off")
        self.assertNotIn("export BROTHER_WBS", text)
        self.assertIn("BROTHER_WBS NOT exported", text)

    def test_without_plan_the_record_carries_the_selected_default(self):
        f = Fixture(self)
        rec, lines = self.prepare(f, None)
        self.assertEqual(rec["plan"], PP.DEFAULT)
        self.assertIn("PLAN     " + PP.DEFAULT, lines)
        rec, lines = self.prepare(f, None, env={PP.ENV: f.fix})
        self.assertEqual(rec["plan"], f.fix)

    def test_colliding_unit_id_is_refused_by_name(self):
        f = Fixture(self)
        collide = {"units": [{"id": "ZZ1", "sub_units": ["ZZ1.a"]}, {"id": "M1", "sub_units": ["M1.x"]}, {"id": "U8", "sub_units": []}]}
        write(os.path.join(f.tree, f.fix), json.dumps(collide))
        rec, lines = self.prepare(f, f.fix)
        self.assertEqual(rec["verdict"], "NOT READY")
        bad = self.refusals(rec)
        self.assertEqual(len(bad), 1, "\n".join(lines))
        self.assertIn("M1, U8", bad[0])
        self.assertIn("exist in both", bad[0])

    def test_a_sub_unit_prefix_collision_is_refused(self):
        """D3 is a default unit whose sub unit D3.1 exists; a selected plan naming D3 collides through that prefix even
        when the default plan listed D3 only as a sub unit holder."""
        f = Fixture(self)
        write(os.path.join(f.tree, PP.DEFAULT), json.dumps({"units": [{"id": "Q", "sub_units": ["D3.1", "Q.a"]}]}))
        write(os.path.join(f.tree, f.fix), json.dumps({"units": [{"id": "D3", "sub_units": []}, {"id": "ZZ1", "sub_units": []}]}))
        rec, lines = self.prepare(f, f.fix)
        bad = self.refusals(rec)
        self.assertEqual(len(bad), 1, "\n".join(lines))
        self.assertIn("unit id(s) D3 exist", bad[0])

    def test_scope_narrows_the_collision_check(self):
        """The rule applies to the units the scope ADMITS: a colliding id outside the scope is not this run's."""
        f = Fixture(self)
        collide = {"units": [{"id": "ZZ1", "sub_units": ["ZZ1.a"]}, {"id": "M1", "sub_units": ["M1.x"]}]}
        write(os.path.join(f.tree, f.fix), json.dumps(collide))
        rec, lines = self.prepare(f, f.fix, scope="^ZZ")
        self.assertEqual(self.refusals(rec), [], "\n".join(lines))
        rec, lines = self.prepare(f, f.fix, scope="^M")
        self.assertEqual(len(self.refusals(rec)), 1, "\n".join(lines))

    def test_unreadable_default_plan_refuses(self):
        f = Fixture(self, default_body="{not json")
        rec, lines = self.prepare(f, f.fix)
        bad = self.refusals(rec)
        self.assertEqual(len(bad), 1, "\n".join(lines))
        self.assertIn("default plan cannot be read", bad[0])
        f2 = Fixture(self, with_default=False)
        rec, lines = self.prepare(f2, f2.fix)
        self.assertIn("default plan cannot be read", self.refusals(rec)[0])

    def test_missing_or_empty_selected_plan_refuses(self):
        f = Fixture(self)
        rec, lines = self.prepare(f, "docs/plan/absent.json")
        self.assertIn("cannot be read", self.refusals(rec)[0])
        write(os.path.join(f.tree, f.fix), json.dumps({"units": []}))
        rec, lines = self.prepare(f, f.fix)
        self.assertIn("no non empty units list", self.refusals(rec)[0])
        write(os.path.join(f.tree, f.fix), json.dumps({"units": [{"state": "OPEN"}]}))
        rec, lines = self.prepare(f, f.fix)
        self.assertIn("without a text id", self.refusals(rec)[0])

    def test_invalid_plan_value_is_refused_not_replaced(self):
        f = Fixture(self)
        rec, lines = self.prepare(f, "/abs/docs/plan/x.json")
        self.assertEqual(rec["verdict"], "NOT READY")
        self.assertIn("absolute path", self.refusals(rec)[0])
        self.assertEqual(rec["plan"], "/abs/docs/plan/x.json", "the record names what was asked, never a substitute")
        self.assertIn("PLAN     /abs/docs/plan/x.json", lines)

    def test_the_entry_point_writes_the_export(self):
        """prepare --plan through loop_intake.py's main, with the stub probes (INTAKE_PROBES=stub, which can never read
        READY), against the real repository plans: 1.1.1 collides on M1 and U8 without a scope, and exports with one."""
        f = Fixture(self)
        me = os.path.join(HERE, "loop_intake.py")
        env = f.env(INTAKE_EVIDENCE=os.path.join(f.home, ".claude", "evidence"), INTAKE_PROBES="stub", BROTHER_OR_BALANCE_FILE=os.path.join(f.root, "no-balance.json"))
        env.pop("BROTHER_WBS")
        r = subprocess.run([sys.executable, "-B", me, "prepare", "finisher=strong", "--deadline", "23:59", "--budget-usd", "1",
                            "--plan", "docs/plan/BROTHER-1.1.1-WBS.json", "--scope", "^U2$"], capture_output=True, text=True, env=env, cwd=REPO, timeout=600)
        self.assertIn("PLAN     docs/plan/BROTHER-1.1.1-WBS.json", r.stdout, r.stdout + r.stderr)
        self.assertIn("OK       plan docs/plan/BROTHER-1.1.1-WBS.json", r.stdout)
        env_sh = os.path.join(f.home, ".claude", "evidence", "loop-intake", "launch-env.sh")
        self.assertTrue(os.path.isfile(env_sh), r.stdout + r.stderr)
        self.assertIn("export BROTHER_WBS=docs/plan/BROTHER-1.1.1-WBS.json\n", text(env_sh))
        rec = load(os.path.join(f.home, ".claude", "evidence", "loop-intake", "CURRENT.json"))
        self.assertEqual((rec["plan"], rec["plan_refused"]), ("docs/plan/BROTHER-1.1.1-WBS.json", ""))
        r2 = subprocess.run([sys.executable, "-B", me, "prepare", "finisher=strong", "--deadline", "23:59", "--budget-usd", "1",
                             "--plan", "docs/plan/BROTHER-1.1.1-WBS.json"], capture_output=True, text=True, env=env, cwd=REPO, timeout=600)
        self.assertRegex(r2.stdout, r"REFUSED  plan docs/plan/BROTHER-1\.1\.1-WBS\.json: unit id\(s\) M1, U8 exist in both")
        self.assertNotIn("export BROTHER_WBS", text(env_sh), "a refused plan is never exported")
        self.assertIn("# BROTHER_WBS NOT exported: the intake refused the plan", text(env_sh))


class WriterRefusesAForeignUnit(unittest.TestCase):
    def test_update_units_refuses_an_id_the_selected_plan_lacks(self):
        f = Fixture(self)
        path = os.path.join(f.tree, f.fix)
        before = read(path)
        with self.assertRaisesRegex(KeyError, "M1"):
            plan_store.update_units(path, {"M1": {"id": "M1", "state": "DONE", "evidence": "closed"}})
        self.assertEqual(read(path), before, "a refused write leaves the plan byte for byte")
        self.assertEqual([u["id"] for u in load(path)["units"]], ["ZZ1", "ZZ2"], "never appended")

    def test_update_units_writes_a_unit_the_selected_plan_holds(self):
        f = Fixture(self)
        path = os.path.join(f.tree, f.fix)
        plan_store.update_units(path, {"ZZ2": lambda u: dict(u, evidence="landed")}, fields=("evidence",))
        self.assertEqual(load(path)["units"][1]["evidence"], "landed")

    def test_close_unit_reads_the_selected_plan(self):
        """close_unit.py --dry in a tree whose ONLY plan is the fixture: it lists nothing closable and exits 1 with no
        traceback, which it could not do if it still opened the default plan."""
        f = Fixture(self, with_default=False)
        r = f.run([sys.executable, "-B", os.path.join(SCRIPTS, "close_unit.py"), "--dry"])
        self.assertNotIn("Traceback", r.stderr, r.stderr[-2000:])
        self.assertNotIn(LITERAL, r.stdout + r.stderr)
        self.assertIn(r.returncode, (0, 1), r.stdout[-1500:] + r.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()
