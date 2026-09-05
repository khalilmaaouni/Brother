"""What scripts/repeat_rate_page.py must keep true.

Every test here feeds the script CANNED instrument output (never runs a
real subprocess) through the `run` injection point in gather()/build_page()
/main(), so the assertions are about this script's own parsing, redaction
and refusal logic, not about what happens to be in this machine's real
evidence store today. tempfile only, mirroring scripts/test_repeat_control.py's
own style: unittest, no real filesystem state left behind.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import repeat_rate_page as P  # noqa: E402

try:
    import tmp_sandbox as _sandbox
    _sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: this test leaves its temp trees behind\n")


#: One good canned line per instrument call this script makes, keyed the
#: same way real_run's `cmd_key` argument is: by name, not by the exact
#: argv (which would embed sys.executable and an absolute path).
GOOD_OUTPUT = {
    "repeat_control": (0, (
        "attempt ledger: 78 record(s) at /Users/someone/.claude/attempt-ledger/"
        "attempts.jsonl (no session id or timestamp in this file's shape)\n"
        "hook outcome: vault_recall shown 1469 lesson(s) over 22 session(s), "
        "costing about 270396 token(s) of context (estimate)\n"
        "primary repeat signal (E53.5 replay, reused from lesson_repeat_trial.py): "
        "shown before the repeat: 4 of 4 failure(s) with recall as it happened, "
        "0 of 4 with memory off, replayed from 1190 command(s) in "
        "/Users/someone/.claude/evidence against 54 lesson(s) in "
        "/Users/someone/.claude/repeat-guard/lessons.jsonl\n"
        "secondary repeat signal (same-sig cross-session collision detector):\n"
        "recall on: 26 session(s), 28633 tool call(s), 26 lesson(s) shown, NO-DATA: x\n"
        "recall off: 531 session(s), 65125 tool call(s), 0 lesson(s) shown, NO-DATA: x\n"
        "comparison: NO-DATA: repeat signal never collided across sessions\n"
    ), ""),
    "repeat_control_controlled": (0, (
        "comparison: NO-DATA: repeat signal never collided across sessions "
        "(secondary signal, same-sig cross-session collision detector)\n"
    ), ""),
    "bm_recurrence": (0, (
        "NO-DATA: 0 applicable work unit(s) recorded, need at least 5 for a rate "
        "(denominator=0, 0 total receipt(s))\n"
    ), ""),
    "board_status": (0, (
        "Lessons recalled this week: 40581 (python3 products/brothermode/tools/"
        "bm_vault_audit.py search --since 2026-08-29T10:04:14+00:00)\n"
    ), ""),
}


def make_run(overrides=None):
    """Returns a `run(cmd_key, argv)` callable backed by GOOD_OUTPUT, with
    any per-key override applied (an override of None removes the line
    that key would otherwise supply, simulating an absent instrument)."""
    table = dict(GOOD_OUTPUT)
    if overrides:
        table.update(overrides)

    def run(cmd_key, argv):
        if cmd_key not in table:
            raise AssertionError("unexpected instrument call: %s %r" % (cmd_key, argv))
        return table[cmd_key]
    return run


class EveryCellRenders(unittest.TestCase):
    def test_all_named_cells_present(self):
        text = P.build_page(run=make_run(), today="2026-09-05")
        self.assertIn("4 of 4 failure(s) with recall as it happened", text)
        self.assertIn("0 of 4 with memory off", text)
        self.assertIn("shown 1469 lesson(s)", text)
        self.assertIn("22 session(s)", text)
        self.assertIn("66.8 lesson(s) shown per session", text)
        self.assertIn("Lessons recalled this week: 40581", text)
        self.assertIn(
            "NO-DATA: 0 applicable work unit(s) recorded, need at least 5", text)
        self.assertIn("comparison: NO-DATA", text)
        self.assertIn("2026-09-05", text)

    def test_no_data_cell_carries_its_date(self):
        text = P.build_page(run=make_run(), today="2026-09-05")
        self.assertIn("NO-DATA until 2026-09-18", text)
        self.assertIn("Check\nagain on or after 2026-09-18", text)

    def test_every_command_is_quoted_beside_its_cell(self):
        text = P.build_page(run=make_run(), today="2026-09-05")
        self.assertIn("python3 scripts/repeat_control.py", text)
        self.assertIn(
            "python3 scripts/repeat_control.py --start 2026-09-05", text)
        self.assertIn(
            "python3 products/brothermode/tools/bm_recurrence.py report", text)
        self.assertIn("python3 scripts/board_status.py --vault-counters", text)

    def test_no_machine_path_survives_into_the_page(self):
        text = P.build_page(run=make_run(), today="2026-09-05")
        self.assertNotIn("/Users/", text)
        self.assertIn("(local path)", text)

    def test_research_briefs_named(self):
        text = P.build_page(run=make_run(), today="2026-09-05")
        self.assertIn("LL-RESEARCH-chinese.md", text)
        self.assertIn("LL-RESEARCH-frontier.md", text)
        self.assertIn("LL-RESEARCH-western.md", text)

    def test_no_em_or_en_dash(self):
        text = P.build_page(run=make_run(), today="2026-09-05")
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2013", text)


class RefusesRatherThanInvents(unittest.TestCase):
    """Every instrument this script reads must be able to make the whole
    run refuse (InstrumentMissing) on its own, one at a time, never
    silently skipped or filled with a placeholder."""

    def test_missing_primary_line_refuses(self):
        overrides = {"repeat_control": (0, "nothing useful here\n", "")}
        with self.assertRaises(P.InstrumentMissing):
            P.build_page(run=make_run(overrides))

    def test_repeat_control_nonzero_exit_refuses(self):
        overrides = {"repeat_control": (1, "", "boom")}
        with self.assertRaises(P.InstrumentMissing):
            P.build_page(run=make_run(overrides))

    def test_missing_hook_outcome_line_refuses(self):
        overrides = {"repeat_control": (0, (
            "primary repeat signal (E53.5 replay, reused from x): shown before "
            "the repeat: 4 of 4 failure(s) with recall as it happened, 0 of 4 "
            "with memory off\n"
            "secondary repeat signal (same-sig cross-session collision detector):\n"
            "recall on: 1 session(s)\n"
            "recall off: 1 session(s)\n"
            "comparison: NO-DATA: x\n"
        ), "")}
        with self.assertRaises(P.InstrumentMissing):
            P.build_page(run=make_run(overrides))

    def test_missing_secondary_block_refuses(self):
        overrides = {"repeat_control": (0, (
            "hook outcome: vault_recall shown 5 lesson(s) over 2 session(s), x\n"
            "primary repeat signal (E53.5 replay, reused from x): shown before "
            "the repeat: 1 of 1 failure(s) with recall as it happened, 0 of 1 "
            "with memory off\n"
        ), "")}
        with self.assertRaises(P.InstrumentMissing):
            P.build_page(run=make_run(overrides))

    def test_controlled_arm_failure_refuses(self):
        overrides = {"repeat_control_controlled": (1, "", "no such flag")}
        with self.assertRaises(P.InstrumentMissing):
            P.build_page(run=make_run(overrides))

    def test_missing_controlled_comparison_line_refuses(self):
        overrides = {"repeat_control_controlled": (0, "nothing here\n", "")}
        with self.assertRaises(P.InstrumentMissing):
            P.build_page(run=make_run(overrides))

    def test_recurrence_failure_refuses(self):
        overrides = {"bm_recurrence": (1, "", "traceback")}
        with self.assertRaises(P.InstrumentMissing):
            P.build_page(run=make_run(overrides))

    def test_recurrence_empty_output_refuses(self):
        overrides = {"bm_recurrence": (0, "", "")}
        with self.assertRaises(P.InstrumentMissing):
            P.build_page(run=make_run(overrides))

    def test_board_status_failure_refuses(self):
        overrides = {"board_status": (1, "", "boom")}
        with self.assertRaises(P.InstrumentMissing):
            P.build_page(run=make_run(overrides))

    def test_missing_weekly_line_refuses(self):
        overrides = {"board_status": (0, "nothing useful\n", "")}
        with self.assertRaises(P.InstrumentMissing):
            P.build_page(run=make_run(overrides))


class MainWritesOrRefuses(unittest.TestCase):
    """main() either writes the page and exits 0, or writes nothing and
    exits 2. It is never allowed to write a partial or invented page."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="repeat-rate-page-test-")
        self._orig_out = P.OUT_PATH
        P.OUT_PATH = os.path.join(self._tmp, "REPEAT-RATE.md")

    def tearDown(self):
        P.OUT_PATH = self._orig_out

    def test_main_writes_the_page_on_success(self):
        rc = P.main(run=make_run())
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(P.OUT_PATH))
        with open(P.OUT_PATH, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("NO-DATA until 2026-09-18", text)

    def test_main_refuses_and_writes_nothing_when_an_instrument_is_absent(self):
        overrides = {"board_status": (1, "", "no such script")}
        rc = P.main(run=make_run(overrides))
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(P.OUT_PATH))


def demo():
    """The ponytail self-check: the happy path renders every cell, and one
    missing instrument refuses instead of inventing a number."""
    text = P.build_page(run=make_run(), today="2026-09-05")
    assert "NO-DATA until 2026-09-18" in text
    assert "/Users/" not in text
    try:
        P.build_page(run=make_run({"board_status": (1, "", "boom")}))
        raise AssertionError("expected InstrumentMissing")
    except P.InstrumentMissing:
        pass
    print("demo: ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        unittest.main()
