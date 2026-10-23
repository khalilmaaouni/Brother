#!/usr/bin/env python3
"""LR1.f: the whole gate (scripts/required_fast.sh) runs the kernel list check as its own row.

WHY. A by-hand landing that adds a kernel file without regenerating scripts/kernel_closure.txt left the list stale and
the gate pair read 63 rows clean (measured at b41e164c4, which lacked scripts/test_receipt_door_landing.py). The check
existed (kernel_closure.py --check) and no gate row ran it. This pins the row, and pins the check's own answers: a list
missing a kernel file fails, a list naming a file the closure left fails, an equal list passes, an absent list reads
NO-DATA (exit 2), which run_check reads as a failed row, never a pass.

Standard library only; writes no file; reads no home directory.

Run: python3 -B scripts/test_kernel_closure_row.py TestKernelClosureRow
"""
import contextlib
import hashlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "required_fast.sh")
TOOL = os.path.join(HERE, "kernel_closure.py")
ROW = re.compile(r'^\s*run_check\s+"kernel-closure-list"\s+python3 scripts/kernel_closure\.py --check\s*$')
CLOSURE = ["scripts/a.py", "scripts/b.py", "scripts/loop/c.py"]


def load():
    spec = importlib.util.spec_from_file_location("kernel_closure_row_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_land_batch():
    spec = importlib.util.spec_from_file_location("land_batch_row_under_test", os.path.join(HERE, "loop", "land_batch.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def put(root, files):
    for rel, body in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
            fh.write(body)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def check(listed):
    """main(["--check"]) with the closure fixed at CLOSURE and the committed list fixed at `listed` (None: absent)."""
    mod = load()
    mod.closure = lambda roots=None, root=None: sorted(CLOSURE)
    mod.committed = lambda root=None: listed
    mod.specs_present = lambda: True   # a hub tree: the export tree's own SKIP and NO-DATA are the B2 cases below
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = mod.main(["--check"])
    return rc, out.getvalue()


class TestKernelClosureRow(unittest.TestCase):
    def test_the_whole_gate_runs_the_kernel_list_check(self):
        with open(GATE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        # the row text only: a commented row, another name or another command is not the row
        rows = [l for l in lines if ROW.match(l)]
        self.assertEqual(len(rows), 1, "required_fast.sh must carry exactly one live kernel-closure-list row")

    def test_a_list_missing_a_kernel_file_fails_the_check(self):
        rc, out = check(CLOSURE[:-1])
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL kernel gained", out)
        self.assertIn("scripts/loop/c.py", out)

    def test_a_list_naming_a_file_the_closure_left_fails_the_check(self):
        rc, out = check(CLOSURE + ["scripts/gone.py"])
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL kernel lost", out)
        self.assertIn("scripts/gone.py", out)

    def test_the_list_equal_to_the_closure_passes(self):
        rc, out = check(list(reversed(CLOSURE)))     # equal as sets, whatever the order
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS", out)

    def test_an_absent_list_reads_no_data_and_never_passes(self):
        rc, out = check(None)
        self.assertEqual(rc, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("PASS", out)

    # ---- B1: the row's real command against the real tree, no stubs
    @unittest.skipUnless(os.path.isdir(os.path.join(os.path.dirname(HERE), "docs", "plan", "specs")),
                         "the export tree ships no docs/plan/specs; the hub's gate runs this case")
    def test_the_rows_real_command_passes_on_this_real_tree(self):
        r = subprocess.run([sys.executable, "-I", "-B", TOOL, "--check"], cwd=os.path.dirname(HERE),
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"^(PASS: the kernel equals|SKIP: )", "the committed list must equal the closure (or the tree is the export)")

    # ---- B2: SKIP only when the export run says so; an absent docs/plan/specs alone is NO-DATA
    def fixture_tree(self, specs, listed):
        d = tempfile.mkdtemp(prefix="kernel-row-")
        self.addCleanup(shutil.rmtree, d, True)
        put(d, {"scripts/kernel_closure.txt": "# header\n" + "".join(x + "\n" for x in listed)})
        os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
        shutil.copy(TOOL, os.path.join(d, "scripts", "kernel_closure.py"))   # the tool reads its OWN tree, not a cwd or a ROOT
        if specs:
            os.makedirs(os.path.join(d, "docs", "plan", "specs"))
        return os.path.join(d, "scripts", "kernel_closure.py")

    def run_copy(self, tool, export=False):
        env = {k: v for k, v in os.environ.items() if k != "BROTHER_EXPORT_TREE"}
        if export:
            env["BROTHER_EXPORT_TREE"] = "1"
        r = subprocess.run([sys.executable, "-I", "-B", tool, "--check"], capture_output=True, text=True, timeout=120, env=env)
        return r.returncode, r.stdout

    WRONG = ["scripts/not-the-kernel.py"]

    def test_the_export_run_reports_skip_with_its_reason(self):
        rc, out = self.run_copy(self.fixture_tree(False, self.WRONG), export=True)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out.startswith("SKIP: this is the public export tree (BROTHER_EXPORT_TREE=1"), out)

    def test_a_hub_that_lost_docs_plan_specs_is_no_data_never_a_skip(self):
        rc, out = self.run_copy(self.fixture_tree(False, self.WRONG))
        self.assertEqual(rc, 2, out)
        self.assertTrue(out.startswith("NO-DATA: docs/plan/specs is absent"), out)
        self.assertNotIn("SKIP", out)

    def test_a_marker_file_a_batch_adds_to_the_hub_does_not_turn_the_check_off(self):
        tool = self.fixture_tree(False, self.WRONG)
        tree = os.path.dirname(os.path.dirname(tool))
        for forged in (".brother-export", ".brother-export-tree", "BROTHER_EXPORT_TREE", os.path.join("scripts", "export-tree")):
            put(tree, {forged: "1\n"})
        rc, out = self.run_copy(tool)
        self.assertEqual(rc, 2, "no file is a marker: only the environment of the export run is\n" + out)

    def test_a_tree_that_carries_the_specs_gets_the_real_check_even_if_the_variable_is_set(self):
        for export in (False, True):
            rc, out = self.run_copy(self.fixture_tree(True, self.WRONG), export=export)
            self.assertEqual(rc, 1, out)
            self.assertIn("FAIL kernel gained", out)

    def test_every_runner_of_the_gate_inside_an_export_tree_sets_the_variable(self):
        # the workflow: every step that runs the gate carries the variable
        with open(os.path.join(os.path.dirname(HERE), ".github", "workflows", "required-fast.yml"), encoding="utf-8") as fh:
            flow = fh.read()
        self.assertGreaterEqual(flow.count("sh scripts/required_fast.sh"), 2)
        self.assertEqual(flow.count('BROTHER_EXPORT_TREE: "1"'), flow.count("sh scripts/required_fast.sh"))
        # the cut preflight's virgin gate
        with open(os.path.join(HERE, "cut_preflight.py"), encoding="utf-8") as fh:
            self.assertIn('env["BROTHER_EXPORT_TREE"] = "1"', fh.read())
        # the exporter's own required-fast check, through its entry point with the run stubbed
        spec = importlib.util.spec_from_file_location("export_public_row_under_test", os.path.join(HERE, "export_public.py"))
        ep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ep)
        d = tempfile.mkdtemp(prefix="kernel-row-export-")
        self.addCleanup(shutil.rmtree, d, True)
        put(d, {"scripts/required_fast.sh": "echo ok\n"})
        seen = {}

        def fake_run(cmd, cwd, env=None, timeout=120):
            seen["env"] = env
            return subprocess.CompletedProcess(cmd, 0, "pass 1 fail 0 no-data 0\n", "")

        ep._world_for, ep._run = (lambda tree, name: ({"cwd": d}, None)), fake_run
        ok, lines = ep.check_required_fast(d)
        self.assertTrue(ok, lines)
        self.assertEqual((seen["env"] or {}).get("BROTHER_EXPORT_TREE"), "1")

    # ---- M3 + N1: the judge compares the box's closure with the closure of the bytes the box holds, on a REAL stamp
    def real_stamps(self, d):
        """Real deploy_stamped.stage_kernel stamps over fixture trees, derived by the real kernel_closure.py: generation N
        (close_unit.py with no import, a helper x.py imported by nobody) and its stamp. Returns (ds, derive, stamp_n, stage)
        where stage(tree_files, name) stamps the next generation over generation N."""
        spec = importlib.util.spec_from_file_location("deploy_stamped_row_under_test", os.path.join(HERE, "loop", "deploy_stamped.py"))
        ds = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ds)
        derive = ds.derive_with(TOOL)
        tool_src = open(TOOL, encoding="utf-8").read()
        base = {"scripts/kernel_closure.py": tool_src, "scripts/gate_ledger.py": "LEDGER = 'N'\n",
                "scripts/required_fast.sh": 'run_check "kernel-closure-list" python3 scripts/kernel_closure.py --check\n'}

        def stage(files, name, previous):
            tree = os.path.join(d, "tree-" + name)
            put(tree, dict(base, **files))
            kdir = os.path.join(d, "kernel-" + name)
            os.makedirs(kdir)
            kernel, pending, overlay = ds.stage_kernel(tree, kdir, previous, "rev-" + name, derive)
            return tree, {"revision": "rev-" + name, "kernel": kernel, "kernel_dir": kdir, "pending_kernel": pending,
                          "kernel_overlay": overlay}

        return ds, derive, stage

    def judge_row(self, stamp, tree_files, listed, overlay=None):
        """The real deploy_steps judge over a fixture: the judge tree holds the batch's (N+1) files and its committed list;
        only the sandbox (boxed) is replaced, by a plain run of the row's own command in that tree. Returns (rc, output)."""
        lb = load_land_batch()
        d = tempfile.mkdtemp(prefix="kernel-row-judge-")
        self.addCleanup(shutil.rmtree, d, True)
        tree = os.path.join(d, "judge")
        put(tree, dict(tree_files, **{"scripts/kernel_closure.txt": "# committed\n" + "".join(x + "\n" for x in listed)}))
        os.makedirs(os.path.join(tree, "docs", "plan", "specs"))
        if overlay is not None:
            stamp = dict(stamp, kernel_overlay=overlay)
        seen = {}

        def real_sh(cmd, log, timeout=2400, env=None, cwd=None, stdin=None):
            if "--whole-gate-snapshot" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "a" * 40 + "\n", "")
            return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=120)

        def row(argv, log, timeout, env, cwd=None):
            e = {k: v for k, v in os.environ.items() if k != "BROTHER_EXPORT_TREE"}
            r = subprocess.run([sys.executable, "-I", "-B", "scripts/kernel_closure.py", "--check"], cwd=cwd, capture_output=True,
                               text=True, env=e, timeout=120)
            seen["rc"], seen["out"] = r.returncode, r.stdout
            return subprocess.CompletedProcess(argv, r.returncode, r.stdout, "")

        lb.sh, lb.boxed, lb.make_copy = real_sh, row, lambda paths=None: tree
        lb.deploy_steps(stamp, io.StringIO(), {})["judge"]()
        return seen["rc"], seen["out"]

    def test_a_pending_root_that_adds_an_import_does_not_hold_the_judge(self):
        d = tempfile.mkdtemp(prefix="kernel-row-stamps-")
        self.addCleanup(shutil.rmtree, d, True)
        ds, derive, stage = self.real_stamps(d)
        tree_n, stamp_n = stage({"scripts/close_unit.py": "VERSION = 'N'\n"}, "n", None)
        batch = {"scripts/close_unit.py": "import m\nVERSION = 'N+1'\n", "scripts/m.py": "M = 1\n"}
        tree_b, stamp_b = stage(batch, "b", stamp_n)
        self.assertIn("scripts/close_unit.py", stamp_b["pending_kernel"], "the root is pending: the stamp holds N's bytes of it")
        self.assertIn("scripts/m.py", stamp_b["kernel"], "the stamp's kernel is derived from the DEPLOY tree (N+1 imports)")
        files = {k: open(os.path.join(tree_b, k), encoding="utf-8").read()
                 for k in ("scripts/close_unit.py", "scripts/m.py", "scripts/kernel_closure.py", "scripts/gate_ledger.py", "scripts/required_fast.sh")}
        listed = derive(tree_b)["kernel"]                      # the batch's committed list: N+1's real closure
        rc, out = self.judge_row(stamp_b, files, listed)
        self.assertEqual(rc, 0, "the overlay alone must not hold a landing\n" + out)
        self.assertIn("PASS", out)

    def test_an_unredeployed_by_hand_change_does_not_hold_the_judge(self):
        d = tempfile.mkdtemp(prefix="kernel-row-stamps-")
        self.addCleanup(shutil.rmtree, d, True)
        ds, derive, stage = self.real_stamps(d)
        tree_n, stamp_n = stage({"scripts/close_unit.py": "import x\n", "scripts/x.py": "X = 1\n"}, "n", None)
        self.assertTrue(stamp_n["kernel_overlay"], "a real stamp's overlay is never empty")
        # a by-hand landing since: a NON root file now imports a new module; nothing redeployed
        files = {"scripts/close_unit.py": "import x\n", "scripts/x.py": "import n\n", "scripts/n.py": "N = 1\n",
                 "scripts/kernel_closure.py": open(TOOL, encoding="utf-8").read(), "scripts/gate_ledger.py": "LEDGER = 'N'\n",
                 "scripts/required_fast.sh": 'run_check "kernel-closure-list" python3 scripts/kernel_closure.py --check\n'}
        tree_h = os.path.join(d, "tree-hand")
        put(tree_h, files)
        rc, out = self.judge_row(stamp_n, files, derive(tree_h)["kernel"])
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS", out)

    def test_without_an_overlay_the_committed_list_still_decides_in_the_judge(self):
        d = tempfile.mkdtemp(prefix="kernel-row-stamps-")
        self.addCleanup(shutil.rmtree, d, True)
        ds, derive, stage = self.real_stamps(d)
        tree_n, stamp_n = stage({"scripts/close_unit.py": "VERSION = 'N'\n"}, "n", None)
        files = {k: open(os.path.join(tree_n, k), encoding="utf-8").read()
                 for k in ("scripts/close_unit.py", "scripts/kernel_closure.py", "scripts/gate_ledger.py", "scripts/required_fast.sh")}
        rc, out = self.judge_row(stamp_n, files, derive(tree_n)["kernel"], overlay=[])
        self.assertEqual(rc, 0, out)
        rc, out = self.judge_row(stamp_n, files, ["scripts/not-the-kernel.py"], overlay=[])
        self.assertEqual(rc, 1, "nothing was overlaid, so a list that disagrees with the tree is a real finding\n" + out)

    def test_a_tool_the_stamp_does_not_vouch_for_leaves_no_list_and_reads_no_data(self):
        lb = load_land_batch()
        d = tempfile.mkdtemp(prefix="kernel-row-lay-")
        self.addCleanup(shutil.rmtree, d, True)
        tool = open(TOOL, encoding="utf-8").read()
        put(os.path.join(d, "k"), {"scripts/kernel_closure.py": tool})
        good = sha(tool)
        cases = {"kernel not a dict": None, "empty kernel": {}, "tool not named": {"scripts/x.py": "1"},
                 "tool bytes differ": {"scripts/kernel_closure.py": "0" * 64}}
        for label, kernel in cases.items():
            with self.subTest(case=label):
                put(d, {"root/scripts/kernel_closure.txt": "scripts/r.py\n"})
                laid = lb.lay_kernel_list(os.path.join(d, "root"), {"kernel_dir": os.path.join(d, "k"), "kernel": kernel}, io.StringIO())
                self.assertFalse(laid)
                self.assertFalse(os.path.exists(os.path.join(d, "root", "scripts", "kernel_closure.txt")), "the other generation's list is removed")
        mod = load()
        mod.ROOT, mod.ROOTS = os.path.join(d, "root"), ("scripts/r.py",)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(mod.main(["--check"]), 2)
        self.assertIn("NO-DATA", out.getvalue())
        self.assertTrue(good)


if __name__ == "__main__":
    unittest.main()
