#!/usr/bin/env python3
"""The boxed gate order child answers on a history over the landing's gate set (D33, register row L19, 2026-10-16).

THE DEFECT, MEASURED. The order policy child land_batch runs (GATE_ORDER_CHILD) exited 1 with "NO-DATA: stats check set
mismatch" on every pass, so every landing ran the fixed order. The newest ten gates.tsv files under
~/.claude/evidence/loop-runs held all three landing gates (nothing missing) plus 18 extra names, every one of them a
landing.neighbour.<test> row: land_batch's fifth gate records each neighbour test into the SAME gates.tsv the policy reads,
and the child fed every row to dream_gate_policy.aggregate_history, whose key set then never equalled the three gate set.
One neighbour row anywhere in the ten logs is enough; 6 of the 10 live logs carried one.

THE RULE UNDER TEST. A history over the landing's own gate set yields a measured order, whether or not neighbour rows sit
beside it; a history over a genuinely different gate set (a gate missing, or a retired gate still recorded) still reads
NO-DATA and the fixed order stands, named.

ENTRY POINTS: the child itself, run with the exact argv land_batch builds (sys.executable -I -B -c GATE_ORDER_CHILD
<code root> <names json> <logs json>), and land_batch.gate_order, the function the landing calls, which boxes that child.
The second needs the grader's sandbox; where it cannot run (a nested box) it skips as NO-DATA, never a pass. Every fixture
lives under a temp directory; nothing reads this repository's evidence or writes into the tree.
Run: python3 -B scripts/loop/test_gate_order_child.py"""
import io, json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import land_batch as LB  # noqa: E402
import grade_build as GB  # noqa: E402

CORE = "plugin/runtime/brother/core"
INITS = ("plugin/__init__.py", "plugin/runtime/__init__.py", "plugin/runtime/brother/__init__.py", CORE + "/__init__.py")
NAMES = [g[0] for g in LB.GATES]
# bundle fails half its runs and is the cheapest, so a measured order puts it first; the other three never fail and keep
# their fixed positions (a tie breaks on the current position); product_manifests is 1.1.8 H1's gate
ROW = {"registration": (0, 10000), "system_doc": (0, 5000), "bundle": (1, 2000), "product_manifests": (0, 3000)}
MEASURED = ["bundle", "registration", "system_doc", "product_manifests"]


def line(name, rc, ms):
    return "%s\t%d\t%d\n" % (name, rc, ms)   # dream_gate.format_gate_line's shape, as record_gate writes it


class GateOrderChild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gate-order-child-")
        self.frozen, self.runs, self.cwd = (os.path.join(self.tmp, n) for n in ("frozen", "runs", "cwd"))
        shutil.copytree(os.path.join(REPO, CORE), os.path.join(self.frozen, CORE),
                        ignore=shutil.ignore_patterns("__pycache__", "test_*.py"))
        os.makedirs(os.path.join(self.frozen, "scripts"))
        shutil.copy2(os.path.join(REPO, "scripts", "gate_order.py"), os.path.join(self.frozen, "scripts"))
        for init in INITS:
            if not os.path.exists(os.path.join(self.frozen, init)):
                open(os.path.join(self.frozen, init), "w").close()
        os.makedirs(self.runs); os.makedirs(self.cwd)
        self.assertEqual(sorted(NAMES), sorted(ROW), "the landing's gate set changed; this fixture names the old one")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def history(self, runs=10, names=None, neighbours=(), only_neighbours=False):
        """`runs` run directories, each one gates.tsv in record_gate's shape: one row per landing gate in `names` (rows
        alternate pass and fail for a failing gate), then the neighbour rows the fifth gate appends."""
        names = list(NAMES if names is None else names)
        for i in range(runs):
            d = os.path.join(self.runs, "run-%02d" % i); os.makedirs(d)
            rows = [] if only_neighbours else [line("landing." + n, ROW.get(n, (0, 3000))[0] * (i % 2), ROW.get(n, (0, 3000))[1]) for n in names]
            rows += [line("landing.neighbour." + t, 0, 4000) for t in neighbours]
            with open(os.path.join(d, "gates.tsv"), "w", encoding="utf-8") as fh:
                fh.write("".join(rows))

    def child(self):
        """the child with the argv land_batch.gate_order builds, unboxed: its own exit, stdout and last stderr line"""
        logs = sorted(os.path.join(self.runs, d, "gates.tsv") for d in os.listdir(self.runs))[-10:]
        home = os.path.join(self.tmp, "home"); os.makedirs(home, exist_ok=True)
        env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TMPDIR": self.tmp}
        r = subprocess.run([sys.executable, "-I", "-B", "-c", LB.GATE_ORDER_CHILD, self.frozen, json.dumps(NAMES), json.dumps(logs)],
                           cwd=self.cwd, env=env, capture_output=True, text=True, timeout=120)
        last = (r.stderr.strip().splitlines() or [""])[-1]
        return r.returncode, r.stdout.strip(), last

    def order(self):
        """land_batch.gate_order as the landing calls it (frozen copy, the live env shape: no code root, no proof phase)"""
        ready = GB.sandbox_ready()
        if ready:
            self.skipTest("NO-DATA: the grader's sandbox cannot run here (%s)" % ready)
        env = {k: v for k, v in os.environ.items() if k not in ("BROTHER_CODE_ROOT", "BROTHER_PROOF_PHASE")}
        old = os.getcwd()
        try:
            os.chdir(self.cwd)
            with mock.patch.dict(os.environ, env, clear=True):
                got, why = LB.gate_order(runs_root=self.runs, frozen=self.frozen, log=io.StringIO())
        finally:
            os.chdir(old)
        return [g[0] for g in got], why

    # ---- the child, its own entry point ------------------------------------------------------------------------
    def test_child_a_history_over_the_gate_set_yields_the_measured_order(self):
        self.history()
        rc, out, err = self.child()
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), {"order": ["landing." + n for n in MEASURED]})

    def test_child_neighbour_rows_beside_the_gate_set_still_yield_the_measured_order(self):
        # L19: the live shape, the fifth gate's rows in the same gates.tsv (one neighbour in one log is enough)
        self.history(neighbours=("test_alpha", "test_beta"))
        rc, out, err = self.child()
        self.assertEqual(rc, 0, "the child refused a history over the gate set because neighbour rows sat beside it: %s" % err)
        self.assertEqual(json.loads(out), {"order": ["landing." + n for n in MEASURED]})

    def test_child_a_gate_missing_from_the_history_reads_no_data(self):
        # a renamed or newly added gate: the history never measured it, so no order is measured
        self.history(names=["registration", "system_doc", "bundle_old"], neighbours=("test_alpha",))
        rc, out, err = self.child()
        self.assertNotEqual(rc, 0, out)
        self.assertEqual(out, "")
        self.assertIn("NO-DATA: stats check set mismatch", err)

    def test_child_a_retired_gate_still_in_the_history_reads_no_data(self):
        # the superset: every current gate present plus a gate the landing no longer runs; a different set, refused
        self.history(names=NAMES + ["retired"])
        rc, out, err = self.child()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("NO-DATA: stats check set mismatch", err)

    def test_child_a_history_of_only_neighbour_rows_holds_no_gate_rows(self):
        self.history(runs=3, neighbours=("test_alpha",), only_neighbours=True)
        rc, out, err = self.child()
        self.assertNotEqual(out, json.dumps({"order": ["landing." + n for n in NAMES]}), "no gate row measured, no order")
        self.assertTrue(rc != 0 or json.loads(out) == {"order": None}, (rc, out, err))

    # ---- land_batch.gate_order, the landing's entry point ------------------------------------------------------
    def test_gate_order_runs_the_measured_order_on_the_live_history_shape(self):
        self.history(neighbours=("test_alpha",))
        got, why = self.order()
        self.assertEqual(got, MEASURED, why)
        self.assertEqual(why, "D13 order from 10 gate log(s): " + ", ".join(MEASURED))

    def test_gate_order_keeps_the_fixed_order_named_on_a_different_set(self):
        self.history(names=["registration", "system_doc", "bundle_old"], neighbours=("test_alpha",))
        got, why = self.order()
        self.assertEqual(got, NAMES, why)
        self.assertTrue(why.startswith("fixed order: the policy refused (NO-DATA:"), why)
        self.assertIn("stats check set mismatch", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
