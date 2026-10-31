#!/usr/bin/env python3
"""LM1.a: a batch that changed a mirrored scripts/ source regenerates the runtime mirror in the landing copy BEFORE the
bundle gate's check, so the check runs once instead of costing a red run first (docs/plan/specs/LM1.md, LM1.a).

WHAT WAS THERE. land_batch ran `bundle_runtime.py --check` first and regenerated only after it read red. The outcome was
right (existing case test_a_generator_that_removes_a_tracked_file_in_the_copy_removes_it_in_the_landing) but a loop edit
of a mirrored source always paid a wasted check, up to a 2400 s box, and leaned on the check reading red.

THE ENTRY POINT, NOT A HELPER. Every landing case runs land_batch.py's main as the loop runs it, through
test_land_batch_landings.Fixture (a scratch tree with a bare upstream and a fresh copy of land_batch.py, so a mutation under
test is the code that runs). The fixture's scripts/bundle_runtime.py is a stub: `--check` exits 1 unless every
scripts/loop/*.py equals its bundle/runtime/loop twin; the bare call copies them. The commands the landing ran are read
from the newest land log. mirror_touched is also tested directly, with hostile values, because it is the decision.
ONE CONDITION PER FIXTURE. Run: python3 -B scripts/loop/test_land_batch_mirror.py"""
import glob, json, os, re, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import land_batch as LB  # noqa: E402
from test_land_batch_landings import Fixture, SUB, UNIT, write  # noqa: E402

STUB = '''import glob, os, shutil, sys
pairs = [(p, "bundle/runtime/loop/" + os.path.basename(p)) for p in sorted(glob.glob("scripts/loop/*.py"))]
if "--check" in sys.argv:
    bad = [s for s, d in pairs if not os.path.exists(d) or open(s).read() != open(d).read()]
    sys.exit(1 if bad else 0)
%s
'''
COPY = "os.makedirs('bundle/runtime/loop', exist_ok=True)\nfor s, d in pairs:\n    shutil.copyfile(s, d)"
GEN = STUB % COPY
STRAY_GEN = STUB % (COPY + "\nopen('scripts/u1extra.py', 'w').write('X = 1\\n')")
FAILING_GEN = STUB % "sys.exit(1)"
TREE = {"scripts/bundle_runtime.py": GEN, "scripts/loop/x.py": "X = 0\n", "bundle/runtime/loop/x.py": "X = 0\n"}
X = "scripts/loop/x.py"
MIRROR = "bundle/runtime/loop/x.py"
CHECK_TAIL, GEN_TAIL = " -B scripts/bundle_runtime.py --check", " -B scripts/bundle_runtime.py"


class Landing(unittest.TestCase):
    def fixture(self, edits, gen=GEN, extra=None):
        root = tempfile.mkdtemp(prefix="lm1a-")
        self.addCleanup(shutil.rmtree, root, True)
        files = dict(TREE, **{"scripts/bundle_runtime.py": gen})
        # unit BL, since 1.1.4 a landing that adds a loop file needs a plan that names BL, else it is refused (the cases that add y.py)
        files["docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"] = json.dumps({"units": [
            {"id": UNIT, "sub_units": [SUB], "spec": "docs/spec-u1.md", "evidence": "", "state": "OPEN", "done_check": "python3 scripts/test_u1a.py"},
            {"id": "BL", "sub_units": [], "evidence": "", "state": "OPEN", "owns": []}]}, indent=1)
        files.update(extra or {})
        fx = Fixture(root, tree_files=files)
        fx.edits({"edits": edits, "tests": []})
        return fx

    def bundle_runs(self, fx):
        """[(kind, exit code)] of the bundle gate's commands, in the order the newest land log shows them."""
        logs = sorted(glob.glob(os.path.join(fx.home, ".claude", "evidence", "land-batch", "*.log")), key=os.path.getmtime)
        self.assertTrue(logs, "no land log")
        with open(logs[-1], encoding="utf-8") as fh:
            text = fh.read()
        out = []
        for cmd, rc in re.findall(r"^\$ (.*)\nenv: .*\nexit=(-?\d+)$", text, re.M):
            if cmd.endswith(CHECK_TAIL):
                out.append(("check", int(rc)))
            elif cmd.endswith(GEN_TAIL):
                out.append(("generate", int(rc)))
        return out

    def test_regenerate_runs_before_the_bundle_check_when_a_loop_source_changed(self):
        fx = self.fixture([{"path": X, "new_file_content": "X = 1\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.bundle_runs(fx), [("generate", 0), ("check", 0)], "generator first, then ONE check")

    def test_a_loop_source_edit_lands_with_its_mirror_regenerated(self):
        fx = self.fixture([{"path": X, "new_file_content": "X = 1\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out)
        self.assertEqual(fx.git("show", "main:" + MIRROR, cwd=fx.hub), "X = 1\n", "the landing commit carries the mirror bytes")
        self.assertEqual(fx.git("show", "main:" + X, cwd=fx.hub), "X = 1\n")

    def test_a_batch_touching_no_mirrored_file_checks_first_and_never_generates(self):
        fx = self.fixture([{"path": "docs/note.txt", "new_file_content": "n\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.bundle_runs(fx), [("check", 0)], "today's order: one check, no generator")

    def test_an_unmapped_suffix_with_no_twin_is_not_mirrored(self):
        fx = self.fixture([{"path": "scripts/loop/x.txt", "new_file_content": "n\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.bundle_runs(fx), [("check", 0)])

    def test_two_loop_sources_and_a_doc_run_one_generator_and_one_check(self):
        fx = self.fixture([{"path": X, "new_file_content": "X = 1\n"}, {"path": "scripts/loop/y.py", "new_file_content": "Y = 1\n"},
                           {"path": "docs/note.txt", "new_file_content": "n\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.bundle_runs(fx), [("generate", 0), ("check", 0)])
        self.assertEqual(fx.git("show", "main:bundle/runtime/loop/y.py", cwd=fx.hub), "Y = 1\n")

    def test_a_new_loop_file_with_no_twin_is_mirrored(self):
        fx = self.fixture([{"path": "scripts/loop/y.py", "new_file_content": "Y = 1\n"}])
        rc, out = fx.land()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.bundle_runs(fx), [("generate", 0), ("check", 0)], "a new file has no twin yet and still regenerates first")
        self.assertEqual(fx.git("show", "main:bundle/runtime/loop/y.py", cwd=fx.hub), "Y = 1\n")

    def test_a_stray_generator_write_still_refuses_when_the_generator_runs_first(self):
        fx = self.fixture([{"path": X, "new_file_content": "X = 1\n"}], gen=STRAY_GEN)
        rc, out = fx.land()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("outside its declared output set", out)
        self.assertIn("scripts/u1extra.py", out)
        self.assertFalse(os.path.exists(os.path.join(fx.tree, "scripts", "u1extra.py")), "the stray path never left the copy")
        self.assertEqual(fx.rev("main", cwd=fx.hub), fx.base, "nothing reached the remote")
        self.assertEqual(fx.git("status", "--porcelain", "-uall"), "", "the tree is left clean")

    def test_a_generator_that_exits_nonzero_is_judged_by_the_check(self):
        # S2 (2026-10-25): the tree keeps the WORKING generator and this build installs the failing
        # one, so the bundle row is red because of the build rather than red on the base as well.
        fx = self.fixture([{"path": X, "new_file_content": "X = 1\n"},
                           {"path": "scripts/bundle_runtime.py", "new_file_content": FAILING_GEN}])
        rc, out = fx.land()
        self.assertNotEqual(rc, 0, out)
        # S2 (2026-10-25): THE THIRD RUN IS THE BASE JUDGEMENT, and it is the point. A gate row red with
        # the build is now re-run in a tree of the base commit before the landing decides, so a row that
        # was already rotten does not hold an unrelated build. Here the base tree carries the WORKING
        # generator, so that third run exits 0, which is exactly the evidence that this build broke the
        # row and the landing must still refuse. Expecting only two runs would assert the absence of the
        # base judgement.
        self.assertEqual(self.bundle_runs(fx), [("generate", 1), ("check", 1), ("check", 0)],
                         "the check ran after the failed generator and read red, then once more in the "
                         "base tree where it reads green, which is what makes this the build's fault")
        self.assertEqual(fx.rev("main", cwd=fx.hub), fx.base, "a red gate lands nothing")

    def test_mirror_touched_skips_hostile_paths(self):
        tracked = lambda rel: False
        for bad in (None, 5, "", "../scripts/loop/x.py", "/scripts/loop/x.py", "scripts/loop/../../x.py", "scripts/loop/x\x00.py",
                    ("scripts/loop/../x.py",), ["scripts/loop/x.txt"], "scripts", "scripts/", "docs/x.py", "scripts/loop/sub/x.py"):
            self.assertEqual(LB.mirror_touched([bad], tracked), [], repr(bad))
        self.assertEqual(LB.mirror_touched(None, tracked), [])
        self.assertEqual(LB.mirror_touched(5, tracked), [])
        self.assertEqual(LB.mirror_touched([], tracked), [])
        self.assertEqual(LB.mirror_touched({"scripts/loop/x.py": 1}, tracked), [X], "a set or dict of paths is read by its members")

    def test_mirror_touched_normalizes_and_reads_the_twin(self):
        tracked = lambda rel: rel == "bundle/runtime/door.py"
        self.assertEqual(LB.mirror_touched(["scripts//loop/x.py", "./scripts/loop/./y.py", "SCRIPTS/Loop/Z.PY"],
                                           tracked), ["SCRIPTS/Loop/Z.PY", "scripts/loop/x.py", "scripts/loop/y.py"])
        for ext in ("sh", "md", "c"):
            self.assertEqual(LB.mirror_touched(["scripts/loop/t.%s" % ext], tracked), ["scripts/loop/t.%s" % ext])
        self.assertEqual(LB.mirror_touched(["scripts/door.py", "scripts/other.py"], tracked), ["scripts/door.py"], "a twin that is tracked")

        def boom(rel):
            raise OSError("unreadable")
        self.assertEqual(LB.mirror_touched(["scripts/door.py"], boom), [], "an unreadable twin lookup is not mirrored")


if __name__ == "__main__":
    unittest.main()
