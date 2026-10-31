#!/usr/bin/env python3
"""A landing writes unit BL's owns into the plan that HOLDS unit BL, not only the run's plan (Brother 1.1.4, 2026-10-14).

THE DEFECT. The first live 1.1.4 landing ran on the 1.1.4 WBS, which has no unit BL. land_batch looked for BL only in the
run's plan, printed "unit BL is not in the plan" and left every new loop file unowned; scripts/test_bl_owns_complete.py,
which reads the launch WBS, went red and the cut refused hours later. The owner unit's plan was assumed to be the run's.

THE RULE UNDER TEST. The run's plan when it holds BL (unchanged); else LOOP_OWNS_PLAN, the file test_bl_owns_complete reads;
else the build is DROPPED naming the unowned files (an unknown blocks). The write is under plan_store's lock with owns
only, the file leaves in the landing commit, and a later refusal takes the entry back byte for byte.

ENTRY POINT, ONE CONDITION PER FIXTURE: every case runs land_batch.py's main as the loop runs it (test_land_batch_landings.
Fixture, a fresh copy of the lander, the run plan chosen by BROTHER_WBS); the plans are built in the fixture, so nothing
reads this repository's docs/plan. Run: python3 -B scripts/loop/test_land_batch_bl_owns_plan.py"""
import json, os, re, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import land_batch as LB  # noqa: E402
import subprocess  # noqa: E402
from test_land_batch_landings import Fixture, OUTSIDE_ACTOR, SUB, UNIT, WAITING_GATE  # noqa: E402

RUN = "docs/plan/RUN-WBS.json"
LAUNCH = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
OLD = "scripts/loop/old_tool.py"
NEW = "scripts/loop/new_tool.py"
NEW2 = "scripts/loop/test_new_tool.py"
U1 = {"id": UNIT, "sub_units": [SUB], "spec": "docs/spec-u1.md", "evidence": "", "state": "OPEN",
      "done_check": "python3 scripts/test_u1a.py", "owns": []}
BL = {"id": "BL", "sub_units": [], "evidence": "BL base.", "state": "OPEN", "owns": [OLD]}
#: S2 (2026-10-25): the tree's gate is GREEN and the BUILD breaks it, so the row is red because of
#: this build. A gate script committed permanently broken is red on the base too, which the landing
#: now reads as BASE-RED and does not hold, so such a fixture would stop testing a gate refusal.
GREEN_GATE = {"scripts/test_battery_registration.py": "import sys\nsys.exit(0)\n"}
BREAK_GATE = {"path": "scripts/test_battery_registration.py", "new_file_content": "import sys\nsys.exit(1)\n"}


def text(units):
    """the bytes plan_store writes, so a write that changes nothing leaves the file byte identical"""
    return json.dumps({"units": units}, indent=1, ensure_ascii=False) + "\n"


class Landing(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="blowns_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def fixture(self, run=(U1,), launch=(BL,), launch_text=None, tree_files=None, edits=None):
        """The run's plan is RUN (BROTHER_WBS); the launch WBS is `launch` units, `launch_text` verbatim, or absent (None, None)."""
        root = os.path.join(self.root, "f")
        files = {RUN: text(list(run))}
        if launch_text is not None:
            files[LAUNCH] = launch_text
        elif launch is not None:
            files[LAUNCH] = text(list(launch))
        files.update(tree_files or {})
        fx = Fixture(root, tree_files=files, extra_env={"BROTHER_WBS": RUN})
        if launch is None and launch_text is None:
            fx.git("rm", "-q", "-f", LAUNCH); fx.git("commit", "-q", "-m", "no launch wbs"); fx.git("push", "-q", "hub", "HEAD")
            fx.base = fx.rev("HEAD")
        if edits is not None:
            fx.edits({"edits": edits, "tests": []})
        return fx

    def outside(self, action):
        """(gate stub text, start): the gate waits for an OUTSIDE actor that runs `action` (Python, cwd the landing tree)."""
        fx, done = os.path.join(self.root, "f"), os.path.join(self.root, "outside.done")
        script = os.path.join(self.root, "outside_actor.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(OUTSIDE_ACTOR % {"bin": os.path.join(fx, "bin"), "tree": os.path.join(fx, "tree"), "tmp": os.path.join(fx, "tmp"),
                                      "done": done, "action": action})

        def start(f):
            q = subprocess.Popen([sys.executable, "-B", script], env=f.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.addCleanup(lambda: q.poll() is None and q.kill())
        return WAITING_GATE % {"done": done, "said": "gate answered", "rc": 0}, start

    def committed(self, fx, path):
        return json.loads(fx.git("show", "HEAD:" + path))

    def owns(self, fx, path, unit="BL"):
        return next((u.get("owns") for u in self.committed(fx, path)["units"] if u["id"] == unit), None)

    def files_in_head(self, fx):
        return fx.git("show", "--name-only", "--format=", "HEAD").split()

    def assert_dropped(self, fx, rc, out, *named):
        self.assertNotEqual(rc, 0, out[-800:])
        for n in named:
            for lead in ("OWNS ", "REFUSED:"):   # the note and the refusal each name the file, so neither can lean on the other
                line = next((l for l in out.splitlines() if l.startswith(lead)), "")
                self.assertIn(n, line, "the %s line does not name %s: %s" % (lead.strip(), n, out[-800:]))
        self.assertEqual(fx.rev("HEAD"), fx.base, out[-600:])
        self.assertEqual(fx.git("status", "--porcelain").strip(), "", "a dropped build left the tree dirty: " + out[-600:])

    def assert_every_plan_as_committed(self, fx, rc, out, *paths):
        self.assertNotEqual(rc, 0, out[-800:])
        for p in paths:
            with open(os.path.join(fx.tree, p), "rb") as fh:
                on_disk = fh.read()
            self.assertEqual(on_disk, fx.git("show", "HEAD:" + p).encode("utf-8"), "%s changed by a refused landing: %s" % (p, out[-600:]))
        self.assertEqual(fx.git("status", "--porcelain").strip(), "", out[-600:])
        self.assertEqual(fx.rev("HEAD"), fx.base, out[-600:])

    # ---- the new home: BL only in the launch WBS
    def test_a_run_plan_without_bl_owns_the_new_loop_file_in_the_launch_wbs(self):
        fx = self.fixture(edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertEqual(self.owns(fx, LAUNCH), [OLD, NEW], out[-800:])
        self.assertIn(LAUNCH, self.files_in_head(fx), "the launch WBS is not in the landing commit")
        self.assertIn(RUN, self.files_in_head(fx), "the run plan keeps its own evidence line in the same commit")
        self.assertNotIn("BL", [u["id"] for u in self.committed(fx, RUN)["units"]], "BL must not be invented in the run plan")
        self.assertNotIn("is not in the plan", out)
        self.assertRegex(out, r"OWNS +unit BL in %s now names %s" % (re.escape(LAUNCH), re.escape(NEW)))

    def test_two_new_loop_files_are_both_owned_in_the_launch_wbs_in_order(self):
        fx = self.fixture(edits=[{"path": NEW, "new_file_content": "print('tool')\n"}, {"path": NEW2, "new_file_content": "print('t')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertEqual(self.owns(fx, LAUNCH), [OLD, NEW, NEW2], out[-800:])

    def test_only_unit_bl_changes_in_the_launch_wbs_and_only_its_owns(self):
        other = {"id": "OTHER", "sub_units": [], "evidence": "other.", "state": "OPEN", "owns": ["x.py"]}
        fx = self.fixture(launch=(other, BL), edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        before = json.loads(fx.git("show", fx.base + ":" + LAUNCH))
        after = self.committed(fx, LAUNCH)
        self.assertEqual(after["units"][0], before["units"][0])
        self.assertEqual({k: v for k, v in after["units"][1].items() if k != "owns"}, {k: v for k, v in before["units"][1].items() if k != "owns"})
        self.assertEqual(after["units"][1]["owns"], [OLD, NEW])

    def test_a_path_bl_already_names_in_the_launch_wbs_is_not_doubled_and_the_file_stays_out_of_the_commit(self):
        fx = self.fixture(edits=[{"path": OLD, "new_file_content": "print('old')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertEqual(self.owns(fx, LAUNCH), [OLD])
        self.assertNotIn(LAUNCH, self.files_in_head(fx))

    def test_a_landing_with_no_new_loop_file_never_touches_the_launch_wbs_even_when_it_is_unreadable(self):
        fx = self.fixture(launch_text="not json {", edits=[{"path": "docs/landed-u1a.txt", "new_file_content": "landed\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertNotIn(LAUNCH, self.files_in_head(fx))

    def test_an_owns_that_is_not_a_list_in_the_launch_wbs_is_left_alone(self):
        fx = self.fixture(launch=(dict(BL, owns="scripts/loop/old_tool.py"),), edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertEqual(self.owns(fx, LAUNCH), "scripts/loop/old_tool.py")

    # ---- today's behaviour, unchanged
    def test_a_run_plan_that_holds_bl_is_written_and_the_launch_wbs_is_left_alone(self):
        fx = self.fixture(run=(U1, BL), launch=(dict(BL, owns=["scripts/loop/launch_only.py"]),),
                          edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out[-800:])
        self.assertEqual(self.owns(fx, RUN), [OLD, NEW], out[-800:])
        self.assertEqual(self.owns(fx, LAUNCH), ["scripts/loop/launch_only.py"])
        self.assertNotIn(LAUNCH, self.files_in_head(fx))

    # ---- no plan holds BL: the build is dropped, never landed unowned
    def test_bl_in_neither_plan_drops_the_build_naming_the_file(self):
        fx = self.fixture(launch=({"id": "OTHER", "sub_units": [], "evidence": "", "state": "OPEN", "owns": []},),
                          edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assert_dropped(fx, rc, out, NEW, "unowned")

    def test_a_missing_launch_wbs_drops_the_build(self):
        fx = self.fixture(launch=None, edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assert_dropped(fx, rc, out, NEW)

    def test_a_launch_wbs_that_is_not_json_drops_the_build(self):
        fx = self.fixture(launch_text="not json {", edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assert_dropped(fx, rc, out, NEW)

    def test_a_launch_wbs_without_a_units_list_drops_the_build(self):
        fx = self.fixture(launch_text=json.dumps({"units": {"BL": BL}}), edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assert_dropped(fx, rc, out, NEW)

    def test_a_launch_wbs_that_is_a_json_list_drops_the_build(self):
        fx = self.fixture(launch_text=json.dumps([BL]), edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assert_dropped(fx, rc, out, NEW)

    def test_the_dropped_build_is_quarantined_not_offered_again(self):
        fx = self.fixture(launch_text="not json {", edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assert_dropped(fx, rc, out)
        with open(fx.status, encoding="utf-8") as fh:
            self.assertFalse(fh.read().startswith("READY"), "the dropped build is still READY: " + out[-400:])

    def test_the_run_plan_equal_to_a_launch_wbs_without_bl_drops_the_build(self):
        """BROTHER_WBS unset: the run's plan IS the launch WBS, so there is no second file to fall back to"""
        root = tempfile.mkdtemp(prefix="blowns_")
        self.addCleanup(shutil.rmtree, root, True)
        fx = Fixture(root, tree_files={LAUNCH: text([U1])})
        fx.edits({"edits": [{"path": NEW, "new_file_content": "print('tool')\n"}], "tests": []})
        rc, out = fx.land()
        self.assert_dropped(fx, rc, out, NEW)

    # ---- a later refusal takes the owns entry back from the plan it was written to
    def test_a_gate_refusal_takes_the_launch_wbs_entry_back_byte_for_byte(self):
        fx = self.fixture(tree_files=GREEN_GATE, edits=[{"path": NEW, "new_file_content": "print('tool')\n"}, BREAK_GATE])
        rc, out = fx.land()
        self.assert_every_plan_as_committed(fx, rc, out, LAUNCH, RUN)

    def test_a_commit_scan_refusal_takes_the_launch_wbs_entry_back_byte_for_byte(self):
        fx = self.fixture(edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        with open(os.path.join(fx.bin, "commit_scan.py"), "w", encoding="utf-8") as fh:
            fh.write("import sys\nprint('commit_scan: BLOCK')\nsys.exit(1)\n")
        rc, out = fx.land()
        self.assert_every_plan_as_committed(fx, rc, out, LAUNCH, RUN)

    def test_a_gate_refusal_of_two_new_files_takes_both_back(self):
        fx = self.fixture(tree_files=GREEN_GATE, edits=[BREAK_GATE, {"path": NEW, "new_file_content": "print('tool')\n"}, {"path": NEW2, "new_file_content": "print('t')\n"}])
        rc, out = fx.land()
        self.assert_every_plan_as_committed(fx, rc, out, LAUNCH, RUN)

    # one refusal point per fixture: each of the six returns that takes a landing back must take the launch WBS back too
    def test_a_held_neighbour_refusal_takes_the_launch_wbs_entry_back(self):
        red = "import unittest\nclass T(unittest.TestCase):\n    def test_t(self):\n        self.fail('red on both trees')\nunittest.main(argv=['x'], exit=True)\n"
        fx = self.fixture(tree_files={"scripts/u1mod.py": "X = 0\n", "scripts/test_u1mod.py": red,
                                      "scripts/check_all.sh": '#!/bin/sh\nrun_check "u1mod" python3 scripts/test_u1mod.py\n# LAST, on purpose\n'},
                          edits=[{"path": "scripts/u1mod.py", "new_file_content": "X = 1\n"}, {"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assertIn("INHERITED", out, out[-800:])
        self.assert_every_plan_as_committed(fx, rc, out, LAUNCH, RUN)

    def test_a_landed_file_rewritten_before_git_add_takes_the_launch_wbs_entry_back(self):
        gate, start = self.outside("open('%s', 'w').write('print(777)\\n')" % NEW)
        fx = self.fixture(tree_files={"scripts/test_battery_registration.py": gate}, edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        start(fx)
        rc, out = fx.land()
        self.assertIn("changed after the lander wrote them", out, out[-800:])
        self.assert_every_plan_as_committed(fx, rc, out, LAUNCH, RUN)

    def test_a_pre_commit_check_refusal_takes_the_launch_wbs_entry_back(self):
        fx = self.fixture(tree_files={"scripts/self_check_staged.py": "import sys\nsys.exit(1)\n"}, edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        rc, out = fx.land()
        self.assertIn("pre-commit check", out, out[-800:])
        self.assert_every_plan_as_committed(fx, rc, out, LAUNCH, RUN)

    def test_a_failed_commit_takes_the_launch_wbs_entry_back(self):
        fx = self.fixture(edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        fx.git("config", "commit.gpgsign", "true"); fx.git("config", "gpg.program", "/usr/bin/false")
        rc, out = fx.land()
        self.assertIn("commit", out, out[-800:])
        self.assert_every_plan_as_committed(fx, rc, out, LAUNCH, RUN)

    def test_revert_landing_keeps_an_owns_entry_another_writer_added_to_the_launch_wbs_and_removes_the_new_file(self):
        """revert_landing on its own inputs, in a scratch git repo: it never checks the launch WBS out over a writer's entry"""
        import plan_store
        d = tempfile.mkdtemp(prefix="blowns_")
        self.addCleanup(shutil.rmtree, d, True)
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"] + list(a),
                                        cwd=d, env=env, capture_output=True, check=True)
        run("init", "-q"); os.makedirs(os.path.join(d, "docs", "plan"))
        with open(os.path.join(d, LAUNCH), "w", encoding="utf-8") as fh:
            fh.write(text([BL]))
        run("add", "-A"); run("commit", "-q", "-m", "base")
        seen = LB.Appended()
        seen.homes[LAUNCH] = {}
        launch = os.path.join(d, LAUNCH)
        plan_store.update_units(launch, LB.landing_records({}, seen.homes[LAUNCH], owns=[NEW]), fields=("owns",))
        plan_store.update_units(launch, {"BL": lambda u: dict(u, owns=u["owns"] + ["scripts/loop/other.py"])}, fields=("owns",))
        os.makedirs(os.path.join(d, "scripts", "loop"))
        with open(os.path.join(d, NEW), "w", encoding="utf-8") as fh:
            fh.write("print('tool')\n")
        here = os.getcwd()
        os.chdir(d)
        try:
            t, c, note = LB.revert_landing({LAUNCH, NEW}, os.path.join(d, "log"), seen, plan_path=RUN)
        finally:
            os.chdir(here)
        self.assertEqual((t, c), (0, 1), "the new file is removed; the launch WBS is not checked out")
        self.assertFalse(os.path.exists(os.path.join(d, NEW)))
        self.assertEqual(plan_store.load(launch)["units"][0]["owns"], [OLD, "scripts/loop/other.py"])
        self.assertIn("other writers added", note, "the plan still differs from HEAD by another writer's entry, and the note says so")

    def test_revert_landing_leaves_a_plan_alone_when_it_is_not_among_the_paths(self):
        """a plan this landing wrote but whose path is not in the reverted set is not this call's to undo"""
        import plan_store
        d = tempfile.mkdtemp(prefix="blowns_")
        self.addCleanup(shutil.rmtree, d, True)
        launch = os.path.join(d, "launch.json")
        with open(launch, "w", encoding="utf-8") as fh:
            fh.write(text([BL]))
        seen = LB.Appended()
        seen.homes[launch] = {}
        plan_store.update_units(launch, LB.landing_records({}, seen.homes[launch], owns=[NEW]), fields=("owns",))
        before = self.raw(launch)
        self.assertEqual(LB.revert_landing(set(), os.path.join(d, "log"), seen, plan_path=RUN)[2], "")
        self.assertEqual(self.raw(launch), before)

    @staticmethod
    def raw(path):
        with open(path, "rb") as fh:
            return fh.read()

    OTHER = "scripts/loop/other_writer.py"

    def writer(self, fx):
        """Python that adds OTHER to BL's owns in the launch WBS under plan_store's lock: a second writer, as the loop has"""
        return ("import sys\nsys.path.insert(0, %r)\nimport plan_store\n"
                "plan_store.update_units(%r, {'BL': lambda u: dict(u, owns=u['owns'] + [%r])}, fields=('owns',))\n"
                % (fx.bin, os.path.join(fx.tree, LAUNCH), self.OTHER))

    def read_owns(self, fx):
        with open(os.path.join(fx.tree, LAUNCH), encoding="utf-8") as fh:
            return json.load(fh)["units"][0]["owns"]

    def assert_only_this_landings_entry_left(self, fx, rc, out):
        self.assertNotEqual(rc, 0, out[-800:])
        self.assertEqual(self.read_owns(fx), [OLD, self.OTHER],
                         "the refusal took back more or less than its own owns entry: " + out[-600:])
        self.assertFalse(os.path.exists(os.path.join(fx.tree, NEW)), "the refused build's file is still in the tree")
        self.assertEqual(fx.rev("HEAD"), fx.base, out[-600:])

    def test_a_commit_scan_refusal_keeps_the_entry_another_writer_added_to_the_launch_wbs(self):
        fx = self.fixture(edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        with open(os.path.join(fx.bin, "commit_scan.py"), "w", encoding="utf-8") as fh:
            fh.write(self.writer(fx) + "print('commit_scan: BLOCK')\nsys.exit(1)\n")
        rc, out = fx.land()
        self.assert_only_this_landings_entry_left(fx, rc, out)

    def test_a_failed_commit_keeps_the_entry_another_writer_added_to_the_launch_wbs(self):
        fx = self.fixture(edits=[{"path": NEW, "new_file_content": "print('tool')\n"}])
        prog = os.path.join(self.root, "gpg_stub.py")
        with open(prog, "w", encoding="utf-8") as fh:
            fh.write(self.writer(fx) + "sys.exit(1)\n")
        os.chmod(prog, 0o755)
        with open(prog, "r+", encoding="utf-8") as fh:
            body = fh.read(); fh.seek(0); fh.write("#!%s\n" % sys.executable + body)
        fx.git("config", "commit.gpgsign", "true"); fx.git("config", "gpg.program", prog)
        rc, out = fx.land()
        self.assertIn("commit", out, out[-800:])
        self.assert_only_this_landings_entry_left(fx, rc, out)

    def test_a_refusal_keeps_an_owns_entry_another_writer_added_to_the_launch_wbs(self):
        """the undo is plan_store's, under the lock: an entry another writer added meanwhile is not this landing's to take"""
        import plan_store
        d = tempfile.mkdtemp(prefix="blowns_")
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, "launch.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text([BL]))
        seen = {}
        plan_store.update_units(p, LB.landing_records({}, seen, owns=[NEW]), fields=("owns",))
        plan_store.update_units(p, {"BL": lambda u: dict(u, owns=u["owns"] + ["scripts/loop/other.py"])}, fields=("owns",))
        self.assertEqual(plan_store.undo_appended(p, seen), [])
        self.assertEqual(plan_store.load(p)["units"][0]["owns"], [OLD, "scripts/loop/other.py"])


class CallSites(unittest.TestCase):
    """Every call that takes a landing back passes `appended`, the record of what it wrote to EVERY plan. The pre-commit
    refusal runs sandboxed (a second writer cannot interleave there), so this reads the source: refuse_landing's default is
    None, which would silently undo nothing under the lock and check the plans out over a concurrent writer."""

    def test_every_refusal_and_revert_call_passes_appended(self):
        import ast
        with open(os.path.join(HERE, "land_batch.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("refuse_landing", "revert_landing")]
        self.assertGreaterEqual(len(calls), 8, "the lander's refusal call sites changed shape: %d found" % len(calls))
        for c in calls:
            named = any(k.arg == "appended" for k in c.keywords)
            positional = c.func.id == "revert_landing" and len(c.args) >= 3
            self.assertTrue(named or positional, "%s at line %d does not pass appended" % (c.func.id, c.lineno))


class Home(unittest.TestCase):
    """owner_plan, the one decision, on its own inputs (the landing cases above prove it is the one the lander asks)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="blowns_")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.launch = os.path.join(self.d, "launch.json")

    def put(self, body):
        with open(self.launch, "w", encoding="utf-8") as fh:
            fh.write(body)

    def test_the_run_plan_wins_when_it_holds_bl(self):
        self.put(text([BL]))
        self.assertEqual(LB.owner_plan({"units": [BL]}, "run.json", self.launch), ("run.json", ""))

    def test_the_launch_wbs_is_the_fallback(self):
        self.put(text([BL]))
        self.assertEqual(LB.owner_plan({"units": [U1]}, "run.json", self.launch), (self.launch, ""))

    def test_a_run_plan_that_failed_to_load_falls_back_and_never_raises(self):
        self.put(text([BL]))
        self.assertEqual(LB.owner_plan(None, "run.json", self.launch), (self.launch, ""))

    def test_every_other_shape_has_no_home_and_says_why(self):
        for body in (text([U1]), "nope {", json.dumps([BL]), json.dumps({"units": "BL"}), json.dumps({"units": 3}), json.dumps({"units": [None, 3]})):
            self.put(body)
            path, why = LB.owner_plan({"units": [U1]}, "run.json", self.launch)
            self.assertEqual(path, "", body)
            self.assertIn("BL", why)
        os.remove(self.launch)
        self.assertEqual(LB.owner_plan({"units": [U1]}, "run.json", self.launch)[0], "")

    def test_a_fallback_equal_to_the_run_plan_is_not_asked_twice(self):
        self.put(text([BL]))
        path, why = LB.owner_plan({"units": [U1]}, self.launch, self.launch)
        self.assertEqual(path, "")
        self.assertNotIn("neither", why, "the same file is not read a second time as if it were another plan")


class Contract(unittest.TestCase):
    def test_the_fallback_is_the_file_the_check_reads(self):
        scripts = os.path.join(os.path.dirname(HERE))
        src = os.path.join(scripts, "test_bl_owns_complete.py")
        if not os.path.exists(src):
            self.skipTest("scripts/test_bl_owns_complete.py does not ship in this tree")
        with open(src, encoding="utf-8") as fh:
            m = re.search(r'^PLAN = "([^"]+)"', fh.read(), re.M)
        self.assertIsNotNone(m, "test_bl_owns_complete.py no longer spells its PLAN as a plain string")
        self.assertEqual(LB.LOOP_OWNS_PLAN, m.group(1))

    def test_the_owner_unit_is_bl(self):
        self.assertEqual(LB.LOOP_UNIT, "BL")


if __name__ == "__main__":
    unittest.main(verbosity=1)
