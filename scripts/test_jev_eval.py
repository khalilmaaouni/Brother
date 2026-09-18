"""Calibration for scripts/jev_eval.py.

Two things must be true about the scorer: a fallback row (the model
answered under wraps as a different model, or the bridge call itself
failed) can never be folded into an accuracy figure, and a results file
with nothing in it is NO-DATA rather than a printed zero. Both are proven
here from hand-computed synthetic rows, not from the frozen evidence file,
so a future change to the frozen rows cannot make this suite silently stop
testing anything.

The runner test never touches the real bridge or the network: it points
BROTHER_DECISION_BRIDGE and BROTHER_CHAT_BRIDGE at a small stand-in script
this file writes to a temp directory, and restores the environment
afterward.
"""
import io
import json
import os
import statistics
import sys
import tempfile
import textwrap
import threading
import unittest
from contextlib import redirect_stdout

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import jev_eval as je  # noqa: E402

try:
    import tmp_sandbox as _tmp_sandbox
    _tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                      % os.path.basename(__file__))


def _run_score(path, gate=0.9):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = je.score(path, gate)
    return rc, buf.getvalue()


class ComputeTaskTests(unittest.TestCase):
    """Hand-computed against four synthetic 'jev' rows for task A:
    i1 truth=True  pred=True  conf=.95 correct=True  cost=.001 secs=1.0
    i2 truth=True  pred=False conf=.60 correct=False cost=.001 secs=1.0
    i3 truth=False pred=False conf=.85 correct=True  cost=.001 secs=1.0
    i4 truth=False pred=None  (fallback, no cost, no conf, no correct)
    """

    def setUp(self):
        self.rows = [
            {"task": "A", "item": "i1", "truth": True, "system": "jev",
             "pred": True, "conf": 0.95, "correct": True, "cost": 0.001, "secs": 1.0},
            {"task": "A", "item": "i2", "truth": True, "system": "jev",
             "pred": False, "conf": 0.6, "correct": False, "cost": 0.001, "secs": 1.0},
            {"task": "A", "item": "i3", "truth": False, "system": "jev",
             "pred": False, "conf": 0.85, "correct": True, "cost": 0.001, "secs": 1.0},
            {"task": "A", "item": "i4", "truth": False, "system": "jev",
             "pred": None, "error": "answered as a different model: some/other", "secs": 0.5},
        ]

    def test_accuracy_excludes_fallback_row(self):
        c = je.compute(self.rows, gate=0.9)["A"]["systems"]["jev"]
        self.assertEqual(c["n"], 3)
        self.assertEqual(c["nodata"], 1)
        self.assertAlmostEqual(c["accuracy"], 2 / 3)

    def test_balanced_accuracy(self):
        c = je.compute(self.rows, gate=0.9)["A"]["systems"]["jev"]
        self.assertAlmostEqual(c["balanced_accuracy"], 0.75)

    def test_coverage_and_accuracy_at_gate(self):
        c = je.compute(self.rows, gate=0.9)["A"]["systems"]["jev"]
        self.assertAlmostEqual(c["coverage"], 1 / 3)
        self.assertAlmostEqual(c["accuracy_at_gate"], 1.0)

    def test_cost_per_success_and_cost_per_gated_success(self):
        c = je.compute(self.rows, gate=0.9)["A"]["systems"]["jev"]
        self.assertAlmostEqual(c["cost_per_success"], 0.003 / 2)
        self.assertAlmostEqual(c["cost_per_gated_success"], 0.003 / 1)
        self.assertNotAlmostEqual(c["cost_per_success"], c["cost_per_gated_success"])

    def test_calibration_bands(self):
        bands = je.compute(self.rows, gate=0.9)["A"]["calibration"]["jev"]
        by_range = dict(((b["lo"], b["hi"]), b) for b in bands)
        low = by_range[(0.5, 0.7)]
        mid = by_range[(0.7, 0.9)]
        high = by_range[(0.9, 1.01)]
        self.assertEqual(low["n"], 1)
        self.assertAlmostEqual(low["accuracy"], 0.0)
        self.assertEqual(mid["n"], 1)
        self.assertAlmostEqual(mid["accuracy"], 1.0)
        self.assertEqual(high["n"], 1)
        self.assertAlmostEqual(high["accuracy"], 1.0)

    def test_fallback_row_never_credited_as_correct(self):
        c = je.compute(self.rows, gate=0.9)["A"]["systems"]["jev"]
        # 4 rows total, only 3 carry a usable prediction; the fallback row
        # contributes to neither the accuracy numerator nor its denominator.
        self.assertEqual(c["n"] + c["nodata"], 4)
        self.assertEqual(c["n"], 3)


class ScoreFileTests(unittest.TestCase):
    def test_empty_results_file_is_nodata_not_zero(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            path = fh.name
        try:
            rc, out = _run_score(path)
            self.assertEqual(rc, 3)
            self.assertIn(je.NODATA, out)
            self.assertNotIn("0%", out)
        finally:
            os.remove(path)

    def test_missing_results_file_is_nodata(self):
        rc, out = _run_score(os.path.join(tempfile.mkdtemp(), "does-not-exist.jsonl"))
        self.assertEqual(rc, 2)
        self.assertIn(je.NODATA, out)

    def test_real_frozen_file_scores_without_crashing(self):
        frozen = os.path.join(SCRIPTS, "..", "benchmarks", "jev_eval", "results-2026-09-18.jsonl")
        self.assertTrue(os.path.isfile(frozen), "frozen evidence file must ship beside the test")
        rc, out = _run_score(frozen)
        self.assertEqual(rc, 0)
        self.assertIn("TASK A", out)
        self.assertIn("spend this eval by system", out)


class BridgeResolutionTests(unittest.TestCase):
    def test_missing_default_and_no_override_is_nodata(self):
        os.environ.pop("BROTHER_DECISION_BRIDGE", None)
        argv, err = je._resolve_bridge("BROTHER_DECISION_BRIDGE", default_bridge="/nonexistent/or_ask.py")
        self.assertIsNone(argv)
        self.assertIn(je.NODATA, err)

    def test_override_is_shlex_split(self):
        import shlex
        target = "/tmp/fake with spaces/bridge.py"
        os.environ["BROTHER_DECISION_BRIDGE"] = "python3 %s" % shlex.quote(target)
        try:
            argv, err = je._resolve_bridge("BROTHER_DECISION_BRIDGE")
            self.assertIsNone(err)
            self.assertEqual(argv, ["python3", target])
        finally:
            os.environ.pop("BROTHER_DECISION_BRIDGE", None)


FAKE_BRIDGE = textwrap.dedent("""
    # Stand-in bridge for scripts/test_jev_eval.py. Never touches the
    # network. Answers deterministically; the runner tests only check that
    # rows arrive with a real prediction and the right model name, never
    # that the guessed label happens to match ground truth.
    import json
    import sys
    import time


    def main():
        argv = sys.argv[1:]
        if "--sleep" in argv:
            time.sleep(float(argv[argv.index("--sleep") + 1]))
        # A substitute model answering under wraps: this is not a made up
        # id, it is a real OpenRouter free-tier id, used only so the test
        # reads like a real substitution rather than a placeholder.
        wrong_model = "nvidia/nemotron-3-ultra-550b-a55b:free"
        if "--decisions" in argv:
            req = json.loads(sys.stdin.read())
            answers = {}
            for key, q in req["questions"].items():
                if q.get("type") == "noul":
                    answers[key] = {"type": "noul", "noul": "0.9"}
                else:
                    choice = sorted(q["criteria"])[0]
                    answers[key] = {"type": "choice", "choice": choice, "confidence": 0.9}
            model = wrong_model if "--wrong-model" in argv else "typesafe/jev"
            print(json.dumps({"answers": answers, "model": model,
                              "usage": {"cost": 0.0001}}))
            return 0
        model_arg = argv[argv.index("--model") + 1]
        full = {"jev": "typesafe/jev", "muse": "meta/muse-spark-1.3-contributor",
                "deepseek": "deepseek/deepseek-v4.1-flash"}.get(model_arg, model_arg)
        if "--wrong-model" in argv:
            full = wrong_model
        body = json.dumps({"answer": "yes", "probability": 0.8})
        print(json.dumps({"model": full, "usage": {"cost": 0.0002},
                          "choices": [{"message": {"content": body}}]}))
        return 0


    sys.exit(main())
""")

MINI_DATASET = {
    "about": "synthetic, for scripts/test_jev_eval.py only",
    "A_mutation_triage": {
        "instructions": "x", "true": "t", "false": "f",
        "items": [{"id": "A1", "caught": True, "rule": "r", "tests": ["t1"], "mutation": "m"}],
    },
    "B_unknown_reads_safe": {
        "instructions": "x", "true": "t", "false": "f",
        "items": [{"id": "B1", "unsafe": True, "rule": "r"}],
    },
    "C_routing": {
        "instructions": "x", "criteria": {"clean": "c", "needs_fix": "n"},
        "spec_dir": "specs", "labels": {"UNIT-1": "clean"},
    },
    "D_log_triage": {
        "instructions": "x", "criteria": {"blocking": "b", "warning": "w", "info": "i"},
        "items": [{"id": "D1", "label": "info", "line": "ok"}],
    },
}


class RunnerInjectedBridgeTests(unittest.TestCase):
    """Every call in this class goes through the stand-in bridge below.
    Nothing here reaches ~/.claude/bin/or_ask.py or the network."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jev-eval-test-")
        os.makedirs(os.path.join(self.tmp, "specs"), exist_ok=True)
        with open(os.path.join(self.tmp, "specs", "UNIT-1-spec.txt"), "w") as fh:
            fh.write("a tiny specification\n")
        self.dataset_path = os.path.join(self.tmp, "dataset.json")
        with open(self.dataset_path, "w") as fh:
            json.dump(MINI_DATASET, fh)
        self.bridge_path = os.path.join(self.tmp, "fake_bridge.py")
        with open(self.bridge_path, "w") as fh:
            fh.write(FAKE_BRIDGE)
        self._saved_env = dict(
            (k, os.environ.get(k)) for k in ("BROTHER_DECISION_BRIDGE", "BROTHER_CHAT_BRIDGE"))
        os.environ["BROTHER_DECISION_BRIDGE"] = "python3 %s" % self.bridge_path
        os.environ["BROTHER_CHAT_BRIDGE"] = "python3 %s" % self.bridge_path

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_run_writes_expected_row_count_and_models(self):
        out_path = os.path.join(self.tmp, "results.jsonl")
        rc = je.run(self.dataset_path, out_path, workers=2)
        self.assertEqual(rc, 0)
        with open(out_path) as fh:
            rows = [json.loads(l) for l in fh]
        # 4 items (one per task) x 4 base systems = 16, plus jev_para for
        # tasks A and B = 2 more, plus one batched call each for B and D
        # (1 item each) = 2 more. 20 rows total, none silently dropped.
        self.assertEqual(len(rows), 20)
        jev_rows = [r for r in rows if r["system"] == "jev"]
        self.assertTrue(all(r["model"] == "typesafe/jev" for r in jev_rows))
        self.assertTrue(all(r["pred"] is not None for r in jev_rows))

    def test_run_moves_previous_results_file_aside_instead_of_overwriting(self):
        out_path = os.path.join(self.tmp, "results.jsonl")
        with open(out_path, "w") as fh:
            fh.write(json.dumps({"old": "evidence"}) + "\n")
        je.run(self.dataset_path, out_path, workers=2)
        moved = [f for f in os.listdir(self.tmp) if f.startswith("results.jsonl.prev-")]
        self.assertEqual(len(moved), 1)
        with open(os.path.join(self.tmp, moved[0])) as fh:
            self.assertIn("old", fh.read())

    def test_a_timed_out_bridge_call_is_recorded_as_nodata_and_the_run_completes(self):
        """The defect this module exists to not repeat: the session's own
        run_eval.py let one subprocess.TimeoutExpired escape a pool worker
        and crash the whole run, silently dropping every row still queued.
        Here every bridge call is told to sleep 3 seconds while the runner
        is given a 0.2 second timeout, so every single call times out, and
        the assertion is that the run still returns 0 with every row
        present, each one carrying a timeout reason, none of them lost."""
        os.environ["BROTHER_DECISION_BRIDGE"] = "python3 %s --sleep 3" % self.bridge_path
        os.environ["BROTHER_CHAT_BRIDGE"] = "python3 %s --sleep 3" % self.bridge_path
        out_path = os.path.join(self.tmp, "results-timeout.jsonl")
        rc = je.run(self.dataset_path, out_path, workers=4, decision_timeout=0.2, chat_timeout=0.2)
        self.assertEqual(rc, 0, "a timing-out bridge must not crash the run")
        with open(out_path) as fh:
            rows = [json.loads(l) for l in fh]
        self.assertEqual(len(rows), 20, "every row must still be written, none silently dropped")
        self.assertTrue(all(r["pred"] is None for r in rows))
        self.assertTrue(all("timed out" in (r.get("error") or "") for r in rows))

    def test_jev_answered_by_a_different_model_is_nodata_not_scored(self):
        """The exact defect the orchestrator's mutation found: replacing
        `if not model.startswith(EXPECT["jev"]):` with `if False:` in
        ask_jev left every prior test green, which means a decision
        answered by a different model would have been silently credited
        to Jev. This drives the real check end to end with a bridge that
        actually answers as a named substitute model."""
        bridge = ["python3", self.bridge_path, "--wrong-model"]
        q = {"type": "noul", "instructions": "x", "criteria": {"true": "t", "false": "f"}}
        payload, secs, err = je.ask_jev(bridge, {"rule": "r"}, {"q": q})
        self.assertIsNone(payload)
        self.assertIn("different model", err)
        self.assertIn("nvidia/nemotron-3-ultra-550b-a55b:free", err)

    def test_jev_wrong_model_row_is_nodata_and_never_carries_correct(self):
        out_path = os.path.join(self.tmp, "results-wrongmodel-jev.jsonl")
        lock = threading.Lock()
        state = {"rule": "r"}
        q = {"type": "noul", "instructions": "x", "criteria": {"true": "t", "false": "f"}}
        je._job(["python3", self.bridge_path, "--wrong-model"], None, out_path, lock,
                "jev", "A", "i1", True, state, q)
        with open(out_path) as fh:
            rows = [json.loads(l) for l in fh]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsNone(row["pred"])
        self.assertNotIn("correct", row)
        self.assertIn("different model", row["error"])

    def test_muse_answered_by_a_different_model_is_nodata_not_scored(self):
        bridge = ["python3", self.bridge_path, "--wrong-model"]
        q = {"type": "choice", "instructions": "x", "criteria": {"clean": "c", "needs_fix": "n"}}
        pred, conf, cost, secs, model, err = je.ask_chat(bridge, "muse", {"specification": "s"}, q)
        self.assertIsNone(pred)
        self.assertIn("different model", err)
        self.assertIn("nvidia/nemotron-3-ultra-550b-a55b:free", err)

    def test_muse_wrong_model_row_is_nodata_and_never_carries_correct(self):
        out_path = os.path.join(self.tmp, "results-wrongmodel-muse.jsonl")
        lock = threading.Lock()
        state = {"specification": "s"}
        q = {"type": "choice", "instructions": "x", "criteria": {"clean": "c", "needs_fix": "n"}}
        je._job(None, ["python3", self.bridge_path, "--wrong-model"], out_path, lock,
                "muse", "C", "UNIT-1", "clean", state, q)
        with open(out_path) as fh:
            rows = [json.loads(l) for l in fh]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsNone(row["pred"])
        self.assertNotIn("correct", row)
        self.assertIn("different model", row["error"])

    def test_call_itself_reports_timeout_never_raises(self):
        rc, out, err, secs = je._call(["python3", "-c", "import time; time.sleep(3)"], "", timeout=0.2)
        self.assertIsNone(rc)
        self.assertIn("timed out", err)

    def test_call_reports_a_command_that_cannot_start_as_nodata_never_raises(self):
        rc, out, err, secs = je._call(["/nonexistent/not-a-real-binary"], "", timeout=5)
        self.assertIsNone(rc)
        self.assertIn("failed to start", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
