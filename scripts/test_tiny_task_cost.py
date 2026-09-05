"""Drive tiny_task_cost backwards as well as forwards.

The fixtures matter more than the live run here, the same argument
test_floor_score.py makes about its own board: a suite that only drives the
happy path proves today's engine, not the instrument. So every rule this
tool applies gets a fixture log that fails without it, and the live product
path gets one real end to end case of its own.

The three readings the instrument exists to tell apart:
  * the price said BEFORE the first worker line (a price), and
  * the same paragraph AFTER it (a receipt, which is what the founder's
    ruling of 2026-09-04 was written against), and
  * no price paragraph at all, which is the pre-E90 engine.

Plus row S18's own half: with no earlier run against this target, the
paragraph must still state a wait, quoted from a timed run rather than left
at NO-DATA. Driven backwards against the wording that shipped before this
change, which stops at NO-DATA and fails here.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import tiny_task_cost as ttc  # noqa: E402
import brother_run as _br  # noqa: E402

NODATA = "NO-DATA"

#: The exact paragraph the engine printed BEFORE this change, kept verbatim
#: as the backwards fixture: it names the price and then stops at NO-DATA
#: without ever telling the person how long the wait has been.
PARAGRAPH_BEFORE = (
    "Price, before anything is claimed or run: this run opens 2 model "
    "session(s), one to plan the work and one for each of the 1 piece(s) of "
    "work in the plan. The expected wall clock reads NO-DATA: no earlier run "
    "against this target left a measured one, and this estate will not "
    "invent a duration. What the same edit would cost you by hand is not "
    "measured here, and nothing below claims to beat it: the run proves what "
    "it does.")


def write_log(tmp, lines):
    """A run directory holding just a run.log, the one file read_price reads."""
    run_dir = os.path.join(tmp, "docs", "plan", "runs", "20260905T000000-x")
    os.makedirs(run_dir, exist_ok=True)
    path = os.path.join(run_dir, "run.log")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


class ThePriceIsReadByPosition(unittest.TestCase):
    """A price is a price only if it comes before the work."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ttc-fixture-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_price_before_the_first_worker_line_is_said_up_front(self):
        path = write_log(self.tmp, [
            "Work W-x created with 1 unit(s)",
            ttc.PRICE_OPENING + " this run opens 2 model session(s).",
            "brother_run: loop_bridge round 1 exited 0",
        ])
        said, line_no, paragraph, worker_at = ttc.read_price(path)
        self.assertTrue(said)
        self.assertEqual(line_no, 2)
        self.assertIn(ttc.PRICE_OPENING, paragraph)
        self.assertEqual(worker_at, 2)

    def test_a_log_with_no_price_line_fails(self):
        """THE FIXTURE THE ROW EXISTS FOR: the pre-E90 engine, which printed
        a bound and never a price. The instrument must call this what it is,
        not read the absence as a quiet pass."""
        path = write_log(self.tmp, [
            "Work W-x created with 1 unit(s)",
            "brother_run: 1 piece(s) of work, none finished yet.",
            "brother_run: loop_bridge round 1 exited 0",
        ])
        said, line_no, paragraph, _worker_at = ttc.read_price(path)
        self.assertFalse(said)
        self.assertIsNone(line_no)
        self.assertIn(NODATA, paragraph)
        self.assertIn("no price paragraph", paragraph)

    def test_a_price_after_the_first_worker_line_is_a_receipt_not_a_price(self):
        path = write_log(self.tmp, [
            "brother_run: loop_bridge round 1 exited 0",
            ttc.PRICE_OPENING + " this run opened 2 model session(s).",
        ])
        said, line_no, _paragraph, worker_at = ttc.read_price(path)
        self.assertFalse(said, "a price printed after the work started is a "
                               "receipt: the ruling asked for a price")
        self.assertEqual(line_no, 2)
        self.assertEqual(worker_at, 0)

    def test_a_missing_log_is_no_data_and_never_a_pass(self):
        said, line_no, paragraph, _worker_at = ttc.read_price(
            os.path.join(self.tmp, "nothing", "run.log"))
        self.assertFalse(said)
        self.assertIsNone(line_no)
        self.assertIn(NODATA, paragraph)


class TheWaitIsReadInThreeWays(unittest.TestCase):
    """Row S18 asks the intent screen to state the wait. NO-DATA is one
    honest answer to that and it is not a pass, so the reading has three
    values and the tool never collapses them into two."""

    def test_the_paragraph_that_shipped_before_this_change_reads_no_data(self):
        self.assertEqual(ttc.price_wait_figure(PARAGRAPH_BEFORE), NODATA)

    def test_a_median_from_this_targets_own_runs_is_named_as_such(self):
        paragraph = _br.price_paragraph(_br.build_price_block(2, [10.0, 30.0]))
        self.assertEqual(ttc.price_wait_figure(paragraph),
                         "measured on this target")

    def test_with_no_history_the_live_paragraph_quotes_a_timed_run(self):
        """S18's own half, driven against the live engine: a FIRST run
        against a repository has no history, and that is exactly the run a
        one line change makes, so the wait must still be stated. Fails
        against PARAGRAPH_BEFORE, which is the wording this replaced."""
        paragraph = _br.price_paragraph(_br.build_price_block(2, []))
        self.assertEqual(ttc.price_wait_figure(paragraph),
                         "quoted from a timed run elsewhere")
        self.assertIn(str(_br.MEASURED_TINY_TASK_REAL_MODEL_SECONDS),
                      paragraph)
        self.assertIn("scripts/tiny_task_cost.py", paragraph)
        # And it never turns a quoted measurement into a promise.
        self.assertIn("Neither figure is a prediction for this run",
                      paragraph)
        # The per-target answer stays honest beside it.
        self.assertIn("The expected wall clock reads %s" % NODATA, paragraph)

    def test_a_one_piece_plan_names_the_ceremony_it_skips(self):
        one = _br.price_paragraph(_br.build_price_block(2, []))
        self.assertIn("no dependency round", one)
        self.assertIn("no release screen", one)
        self.assertIn("It still runs the planning pass", one)
        # A larger plan must NOT claim to skip any of it.
        three = _br.price_paragraph(_br.build_price_block(4, []))
        self.assertNotIn("no dependency round", three)


class TheInstrumentMeasuresARealTinyTask(unittest.TestCase):
    """One real case through scripts/brother_run.py, the product's own
    public entry point, at the stub model seam. Slow by the standards of the
    fixtures above (a few seconds) and worth it: without it this file would
    only prove its own string matching."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ttc-live-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_docs_case_lands_and_its_price_is_recorded(self):
        result = ttc.case_docs(self.tmp)
        self.assertEqual(result["verdict"], "PASS", result)
        self.assertEqual(result["exit_code"], 0, result)
        # COUNTED, never estimated: one command is what a person issues.
        self.assertEqual(result["user_steps"], 1, result)
        self.assertGreater(result["wall_clock_seconds"], 0.0, result)
        self.assertIn("NOTES.md", result["files_written_in_repo"], result)
        self.assertTrue(result["price_said_up_front"], result)
        self.assertLess(result["price_line_number"],
                        result["first_worker_log_line"], result)
        self.assertEqual(result["price_states_a_wait"],
                         "quoted from a timed run elsewhere", result)
        # The engine's own cost is never quoted as the wait a person with a
        # real model pays, and the record says so in its own words.
        self.assertIn(NODATA, result["wall_clock_note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
