"""test_fault_lab.py: proves scripts/fault_lab.py holds the one law that
makes it worth having.

THE LAW: the fault lab imports no product module. A harness that imported
door, loop_bridge, claim_store, brother_run, work_record, worktree_lane,
integrate, graph_loop, model_worker or scope_audit would recreate the exact
blind spot it exists to close (an observer sharing the implementation's own
abstraction boundary), so this is asserted mechanically from the file's own
AST rather than trusted from its docstring.

The second test is a fast, network-free smoke check: --list prints exactly
the six scenario names the roadmap names (the original four lifecycle
scenarios plus P13's two data-science golden-fixture scenarios, ds-leakage
and ds-seed), with no install and no subprocess fan-out.

The last two classes DRIVE P13's two scenario bodies, which nothing used to
do: --list proved only that their names print, so a scenario that injected
nothing and detected nothing passed this file. They cost tens of seconds
each, because they run the real product end to end; only the `claude plugin
install` is stubbed, out of this checkout's own bundle/runtime. The four
lifecycle scenarios still need a real binary and are not driven here.
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))
FAULT_LAB = os.path.join(HERE, "fault_lab.py")

#: Every module this estate ships that the spine (brother_run.py and what it
#: calls) is built from. Importing any of these from the harness would let
#: it drive the product's own internals instead of its public CLI.
PRODUCT_MODULES = {
    "door", "loop_bridge", "claim_store", "brother_run", "work_record",
    "worktree_lane", "integrate", "graph_loop", "model_worker", "scope_audit",
    "bm_worker_spawn", "bm_verify", "bm_repair",
}


def _imported_names(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class NoProductImport(unittest.TestCase):
    def test_fault_lab_imports_no_product_module(self):
        imported = _imported_names(FAULT_LAB)
        overlap = imported & PRODUCT_MODULES
        self.assertEqual(overlap, set(),
                         "fault_lab.py must treat the product as a "
                         "subprocess only; it imported %r directly, which "
                         "recreates the blind spot this file exists to "
                         "close" % sorted(overlap))

    def test_a_hand_added_product_import_would_fail_this_test(self):
        """The meta-test's own self-test: a name from PRODUCT_MODULES really
        would be caught, so a pass above is not merely an empty overlap by
        construction (e.g. a typo in PRODUCT_MODULES that matches nothing)."""
        fake_imports = {"door", "os", "sys"}
        overlap = fake_imports & PRODUCT_MODULES
        self.assertEqual(overlap, {"door"})


class ListSmoke(unittest.TestCase):
    def test_list_prints_exactly_the_six_scenarios(self):
        proc = subprocess.run([sys.executable, FAULT_LAB, "--list"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        names = set(proc.stdout.split())
        self.assertEqual(names, {"fail_then_repair", "crash_then_bare_invoke",
                                 "two_process_race", "kill_holding_lock",
                                 "ds-leakage", "ds-seed"},
                         proc.stdout)


#: A stand-in for `claude plugin ...` that fabricates exactly the layout
#: install_artifact() globs for, out of this checkout's own bundle/runtime and
#: products/brothermode. Only the INSTALL is stubbed: everything past it is
#: the real launcher, the real engine, the real split_check.py and the
#: shipped fixtures, which is the whole point of driving the scenarios at all.
_STUB_CLAUDE = '''#!/bin/sh
case "$1 $2" in
  "plugin marketplace")
    echo "Successfully added marketplace brother"
    exit 0 ;;
  "plugin install")
    d="$CLAUDE_CONFIG_DIR/plugins/cache/brother"
    mkdir -p "$d/brother/1.0.0" "$d/brothermode"
    ln -sfn "%(root)s/bundle/runtime" "$d/brother/1.0.0/runtime"
    ln -sfn "%(root)s/products/brothermode" "$d/brothermode/1.0.0"
    echo "Successfully installed plugin brother@brother"
    exit 0 ;;
esac
echo "stub claude: unsupported $*" >&2
exit 1
'''

ROOT = os.path.dirname(HERE)
SPLIT_CHECK = os.path.join(HERE, "split_check.py")
LEAKAGE_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "ds-leakage")


class TheLeakageFixtureIsGenuinelyLeaky(unittest.TestCase):
    """scenario_ds_leakage's own oracle, driven directly and cheaply. The
    scenario asserts the product refused a leaky split; that means nothing
    unless the fixture it points at is still leaky, and nothing in the tree
    checked that. Both directions are driven, because a checker that reports
    a leak in every tree would satisfy the FOUND half by itself."""

    def _split_check(self, train, test):
        return subprocess.run(
            [sys.executable, SPLIT_CHECK, "--train", train, "--test", test,
             "--key", "customer_id", "--time-col", "event_date",
             "--cutoff", "2026-01-01"], capture_output=True, text=True)

    def test_the_shipped_fixture_is_reported_FOUND(self):
        proc = self._split_check(os.path.join(LEAKAGE_FIXTURE, "train.csv"),
                                 os.path.join(LEAKAGE_FIXTURE, "test.csv"))
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("split-check: FAIL:", out)

    def test_a_clean_fixture_is_reported_NOT_FOUND(self):
        """The negative control. No shared entity and no train row past the
        cutoff, so the same checker with the same flags must say nothing is
        wrong."""
        d = tempfile.mkdtemp(prefix="clean-split-")
        self.addCleanup(shutil.rmtree, d, True)
        train = os.path.join(d, "train.csv")
        test = os.path.join(d, "test.csv")
        with open(train, "w", encoding="utf-8") as fh:
            fh.write("customer_id,event_date,feature1\n"
                     "C1,2025-06-01,10\nC2,2025-07-15,20\n")
        with open(test, "w", encoding="utf-8") as fh:
            fh.write("customer_id,event_date,feature1\n"
                     "C8,2025-08-01,25\nC9,2025-09-01,40\n")
        proc = self._split_check(train, test)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn("FAIL", out)


class TheDataScienceScenarioBodiesActuallyRun(unittest.TestCase):
    """ds-leakage and ds-seed had no test that ever executed their bodies:
    ListSmoke above proves only that the six names print, and this file's
    docstring said the scenarios need a real `claude` binary. A scenario that
    injected nothing and detected nothing passed that bar.

    Slower than everything else in this file (tens of seconds: each case is a
    real end-to-end product run), and deliberately so, because the cheap
    checks above cannot see a scenario whose assertions have gone vacuous."""

    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _run_scenario(self, name):
        stub_dir = tempfile.mkdtemp(prefix="stub-claude-")
        self._dirs.append(stub_dir)
        stub = os.path.join(stub_dir, "claude")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write(_STUB_CLAUDE % {"root": ROOT})
        os.chmod(stub, os.stat(stub).st_mode | 0o111)
        env = dict(os.environ)
        env["PATH"] = stub_dir + os.pathsep + env["PATH"]
        proc = subprocess.run([sys.executable, FAULT_LAB, name], env=env,
                              capture_output=True, text=True, timeout=900)
        # install_artifact()'s own refusal is the FIRST line and nothing
        # else; a NO-DATA further down belongs to the scenario's own report.
        first = proc.stdout.splitlines()[0] if proc.stdout.strip() else ""
        self.assertFalse(
            first.startswith("NO-DATA:"),
            "the stub install did not produce the layout install_artifact() "
            "expects: %s" % first)
        return proc

    def test_ds_leakage_reports_the_injected_leak_FOUND(self):
        proc = self._run_scenario("ds-leakage")
        out = proc.stdout + proc.stderr
        self.assertIn("split_check_failed=True", out, out[:2000])
        self.assertIn("check_cmd_used=True", out, out[:2000])
        self.assertEqual(proc.returncode, 0, out[-4000:])

    def test_ds_seed_reports_the_injected_unseeded_metric_FOUND(self):
        """The DETECTION clause only, never the scenario's overall verdict.

        Driving this body found scenario_ds_seed returning FAIL on an
        otherwise clean tree: its `both_delivered_by_exit_code` precondition
        looks for "S1 delivered:", which the installed artifact's own E18
        evidence gating never prints, because the same gating the scenario
        accepts as PATH A makes the unit read NO-DATA and the run exit 2. The
        two clauses contradict each other, and which one is wrong is a design
        call for whoever owns the scenario, not something to settle by
        editing a test. So this asserts what the scenario actually detected,
        and says nothing about its verdict either way.

        Ceiling: differing_value compares two unseeded draws printed to four
        decimals, so a collision would flake this at roughly one run in ten
        thousand. Left as is because the alternative, asserting the
        no_metric_recorded disjunct instead, would stay green on a fixture
        that had quietly become seeded, which is the regression this
        exists to catch."""
        proc = self._run_scenario("ds-seed")
        out = proc.stdout + proc.stderr
        self.assertIn("ds-seed", out, out[:2000])
        self.assertIn("differing_value=True", out, out[:2000])


if __name__ == "__main__":
    unittest.main()
