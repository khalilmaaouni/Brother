#!/usr/bin/env python3
"""G12 (L20, L21): the landing runs the deploy's own freeze closure and tool parity before it commits.

WHAT WAS WRONG (2026-10-14, deploy8, deploy9, deploy10 in the 1.1.4 night evidence): builds landed green and the deploy then
refused them one at a time, after the loop had been stopped for it: a computed loader ("dynamic loader requires a resolved
import trace"), a file that pulled the release tooling into the frozen closure, two readers of one rule read as ambiguous by
the tool parity check. land_batch.py never called freeze_manifest or the parity check.

THE ENTRY POINT. Every landing case runs land_batch.py's main as the loop runs it, through the Fixture of
test_land_batch_landings.py (a scratch tree, a bare upstream, a scratch HOME and TMPDIR, a scratch bin holding a fresh copy
of the land_batch.py beside this file). This fixture's bin also carries the real deploy_stamped.py and tool_stamp.py, and its
tree carries a deployable loop: a stub deploy at HEAD, every candidate entry the deploy requires, and the real
scripts/test_loop_tool_parity.py with the install_loop_tools.py it imports. ONE CONDITION PER FIXTURE: each refusal case
breaks exactly one check, so the other passes and only the guard under test can refuse.
One in process case drives deploy_gate itself for a HEAD git cannot read, which a landing cannot reach (it fetches first).

Run: python3 -B scripts/loop/test_land_batch_closure.py
"""
import glob, io, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import deploy_stamped as DS                 # noqa: E402
import diag_notes as DN                     # noqa: E402
import land_batch as LB                     # noqa: E402
import test_land_batch_landings as LL       # noqa: E402

SUB, UNIT, BRANCH = LL.SUB, LL.UNIT, LL.BRANCH
TOOL = "scripts/loop/g12_tool.py"
READER = "scripts/g12_reader.py"
DEPLOY_TOOL = "scripts/loop/deploy_stamped.py"   # the deploy a tree ships; written out, so the base lander runs these cases too
COMPUTED = ('import importlib.util\n\n\ndef load(path):\n'
            '    return importlib.util.spec_from_file_location("g12_plugin", path)\n')
WORK_INPUT = "import importlib\n\n\ndef main(name):\n    return importlib.import_module(name)\n"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def loop_tree(**over):
    """A tree a deploy ships from: the stub deploy at HEAD, every candidate entry (the work input with its computed loader),
    one loop tool, one reader in scripts/, the real parity check and the module it imports."""
    files = {DEPLOY_TOOL: "import os\n", TOOL: "import os\n", READER: "import os\n"}
    for rel in DS.CANDIDATE_ENTRIES:
        files[rel] = WORK_INPUT if rel.endswith("land_fuzz_child.py") else "def main():\n    return 0\n"
    for rel in ("scripts/test_loop_tool_parity.py", "scripts/install_loop_tools.py"):
        files[rel] = read(os.path.join(REPO, rel))
    files.update(over)
    return files


def edit(path, text):
    return {"edits": [{"path": path, "new_file_content": text}], "tests": []}


class Closure(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="g12-closure-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.code = os.path.join(self.root, "code")
        LL.write(os.path.join(self.code, "scripts", "close_unit.py"), LL.CLOSE_UNIT_STUB)

    def fixture(self, tree=None, bin_deploy=True):
        f = LL.Fixture(os.path.join(self.root, "f"), code_root=self.code, tree_files=loop_tree() if tree is None else tree)
        for name in ("tool_stamp.py",) + (("deploy_stamped.py",) if bin_deploy else ()):
            shutil.copy2(os.path.join(HERE, name), os.path.join(f.bin, name))
        return f

    def land(self, f, build):
        f.edits(build)
        LL.write(f.status, "READY %s (round 0)\n" % f.build)
        rc, out = f.land()
        self.assertEqual(glob.glob(os.path.join(f.tmpdir, "land-deploy-*")), [], "the scratch stage leaves no residue")
        return rc, out

    def hub(self, f):
        return f.git("rev-parse", BRANCH, cwd=f.hub).strip()

    def note(self, f):
        return DN.note_for(UNIT, SUB, notes=os.path.join(f.home, ".claude", "evidence", "diag-notes"))

    def assert_refused(self, f, rc, out, line):
        """Refused through the gates' path: nonzero, nothing pushed, the edit reverted, quarantined, the line in the note."""
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.hub(f), f.base, "nothing reached hub:\n" + out)
        self.assertEqual(read(os.path.join(f.tree, TOOL)), "import os\n", "the build's edit is reverted")
        self.assertIn("gates RED (", out)
        self.assertTrue(read(f.status).startswith("QUARANTINE refused at the gates alone"), read(f.status))
        self.assertIn(line, self.note(f), "the refusal becomes the sub unit's diagnosis note")

    # --- the deploy's refusals, each the only check that fails ---------------------------------------------------------
    def test_a_loop_file_with_a_computed_loader_is_refused_with_the_deploys_line(self):
        f = self.fixture()
        rc, out = self.land(f, edit(TOOL, COMPUTED))
        said = [l for l in out.splitlines() if l.startswith("DEPLOY  deploy closure REFUSED: ")]
        self.assertEqual(len(said), 1, out)
        self.assertRegex(said[0], r"dynamic loader requires a resolved import trace: \S+/scripts/loop/g12_tool\.py$")
        self.assertNotIn("deploy parity REFUSED", out, "one condition: the parity passes")
        self.assert_refused(f, rc, out, "dynamic loader requires a resolved import trace: ")

    def test_an_ambiguous_reader_in_scripts_is_refused_with_the_parity_line(self):
        f = self.fixture()
        rc, out = self.land(f, edit(READER, "import g12_tool\n"))
        self.assertIn("DEPLOY  deploy parity REFUSED: FAIL ambiguous g12_reader.py", out)
        self.assertIn("imports g12_tool without saying which copy it means", out)
        self.assertNotIn("deploy closure REFUSED", out, "one condition: the closure passes")
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.hub(f), f.base, out)
        self.assertEqual(read(os.path.join(f.tree, READER)), "import os\n")
        self.assertIn("FAIL ambiguous g12_reader.py", self.note(f))

    # --- what passes, and what never runs ------------------------------------------------------------------------------
    def test_a_clean_loop_edit_passes_both_checks_and_lands(self):
        f = self.fixture()
        rc, out = self.land(f, edit(TOOL, "import json\n"))
        self.assertEqual(rc, 0, out)
        self.assertIn("DEPLOY  closure and parity pass on a scratch stage of the candidate", out)
        self.assertNotEqual(self.hub(f), f.base, "the landing reached hub")

    def test_a_batch_that_ships_no_loop_or_candidate_file_never_runs_the_deploy_checks(self):
        f = self.fixture(bin_deploy=False)   # were they run, the missing deploy would refuse NO-DATA
        rc, out = self.land(f, edit("docs/landed-u1a.txt", "landed\n"))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DEPLOY", out)

    def test_a_loop_test_edit_ships_nowhere_and_never_runs_the_deploy_checks(self):
        test = "scripts/loop/test_g12_tool.py"   # tracked, so the edit is not a new loop file the plan must own
        f = self.fixture(tree=loop_tree(**{test: LL.GREEN_TEST}), bin_deploy=False)
        rc, out = self.land(f, edit(test, LL.GREEN_TEST + "\n"))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("DEPLOY", out)

    def test_a_tree_whose_head_holds_no_deploy_is_not_applicable_and_lands(self):
        tree = loop_tree(); [tree.pop(rel) for rel in [DEPLOY_TOOL] + list(DS.CANDIDATE_ENTRIES)]
        f = self.fixture(tree=tree)   # were the checks run, the missing candidate entries would refuse
        rc, out = self.land(f, edit(TOOL, "import json\n"))
        self.assertEqual(rc, 0, out)
        self.assertIn("DEPLOY  not applicable", out)
        self.assertNotIn("REFUSED", out)

    # --- the checker is the base's, and nothing is staged through a link (G12 follow up) ----------------------------------
    def test_a_build_that_rewrites_the_parity_checker_to_pass_is_still_refused(self):
        f = self.fixture()
        build = edit(READER, "import g12_tool\n")   # the real parity fails this candidate (the ambiguous reader above)
        build["edits"].append({"path": "scripts/test_loop_tool_parity.py", "new_file_content": "print('PASS: forged')\n"})
        rc, out = self.land(f, build)
        self.assertIn("DEPLOY  deploy parity REFUSED: REFUSED: the batch edits the deploy parity checker (scripts/test_loop_tool_parity.py)", out)
        self.assertNotIn("PASS: forged", out)
        self.assert_refused(f, rc, out, "edits the deploy parity checker")

    def test_a_planted_stdlib_name_beside_the_checker_cannot_make_it_pass(self):
        f = self.fixture()
        build = edit(READER, "import g12_tool\n")
        build["edits"].append({"path": "scripts/hashlib.py", "new_file_content": "print('PASS: planted')\nraise SystemExit(0)\n"})
        rc, out = self.land(f, build)
        self.assertIn("DEPLOY  deploy parity REFUSED: FAIL ambiguous g12_reader.py", out, "the standard library's hashlib ran, not the build's")
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.hub(f), f.base, out)

    def test_a_checker_that_differs_from_the_base_is_never_run(self):
        copy = os.path.join(self.root, "copy")
        f = LL.Fixture(os.path.join(self.root, "f"), code_root=self.code, tree_files=loop_tree())
        cwd = os.getcwd(); os.chdir(f.tree)
        try:
            LL.write(os.path.join(f.tree, "scripts", "install_loop_tools.py"), "raise SystemExit(0)\n")
            red, ran = LB.deploy_gate(f.tree, [TOOL], io.StringIO(), None, runner=lambda a, e: self.fail("the parity never runs"))
        finally:
            os.chdir(cwd)
        self.assertEqual(ran, [])
        self.assertEqual(red, [("deploy parity", "NO-DATA: scripts/install_loop_tools.py in the candidate is not the landing base's, so the parity is not run from it")])

    def test_a_symlink_to_a_file_outside_the_tree_is_refused_before_staging(self):
        secret = os.path.join(self.root, "outside-secret.txt")
        LL.write(secret, "OUTSIDE-SECRET-G12-7f3a\n")
        f = self.fixture()
        evil = "scripts/loop/evil.py"
        build = {"edits": [{"path": evil, "new_file_content": "import os\n"}], "tests": [], "symlink": {evil: secret}}
        rc, out = self.land(f, build)
        self.assertIn("DEPLOY  deploy closure REFUSED: NO-DATA: scripts/loop/evil.py is a symlink or not a regular file", out)
        self.assertNotIn("OUTSIDE-SECRET", out)
        self.assertNotIn("OUTSIDE-SECRET", self.note(f))
        self.assertNotIn("OUTSIDE-SECRET", read(f.status))
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(self.hub(f), f.base, out)

    # --- a check that cannot run reads NO-DATA and refuses ---------------------------------------------------------------
    def test_a_deploy_that_cannot_be_imported_reads_no_data_and_refuses(self):
        f = self.fixture(bin_deploy=False)
        rc, out = self.land(f, edit(TOOL, "import json\n"))
        self.assertIn("DEPLOY  deploy closure REFUSED: NO-DATA: the deploy's checks could not be imported", out)
        self.assert_refused(f, rc, out, "NO-DATA: the deploy's checks could not be imported")

    def test_a_closure_that_raises_reads_no_data_and_refuses(self):
        f = self.fixture()
        with open(os.path.join(f.bin, "deploy_stamped.py"), "a", encoding="utf-8") as fh:
            fh.write("\n\ndef stage_candidate(source_dir, dest):\n    raise TypeError('g12 boom')\n")
        rc, out = self.land(f, edit(TOOL, "import json\n"))
        self.assertIn("DEPLOY  deploy closure REFUSED: NO-DATA: the closure could not run (TypeError: g12 boom)", out)
        self.assertNotIn("deploy parity REFUSED", out, "one condition: the parity passes")
        self.assert_refused(f, rc, out, "NO-DATA: the closure could not run")

    def test_a_parity_check_with_no_verdict_reads_no_data_and_refuses(self):
        f = self.fixture(tree=loop_tree(**{"scripts/test_loop_tool_parity.py": "print('NOT APPLICABLE: nothing')\n"}))
        rc, out = self.land(f, edit(TOOL, "import json\n"))
        self.assertIn("DEPLOY  deploy parity REFUSED: NO-DATA: the tool parity check exit 0 gave no verdict: NOT APPLICABLE", out)
        self.assertNotIn("deploy closure REFUSED", out, "one condition: the closure passes")
        self.assert_refused(f, rc, out, "NO-DATA: the tool parity check exit 0 gave no verdict")

    def test_a_head_git_cannot_read_reads_no_data(self):
        cwd = os.getcwd(); bare = tempfile.mkdtemp(prefix="g12-nogit-", dir=self.root)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["GIT_CEILING_DIRECTORIES"] = self.root
        old = dict(os.environ)
        os.chdir(bare); os.environ.clear(); os.environ.update(env)
        try:
            red, ran = LB.deploy_gate(bare, [TOOL], io.StringIO(), None, runner=lambda a, e: self.fail("no parity without a HEAD"))
        finally:
            os.chdir(cwd); os.environ.clear(); os.environ.update(old)
        self.assertEqual(ran, [])
        self.assertEqual(len(red), 1, red)
        self.assertTrue(red[0][1].startswith("NO-DATA: the tree's HEAD could not be read"), red)


if __name__ == "__main__":
    unittest.main()
