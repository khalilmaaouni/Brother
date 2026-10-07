#!/usr/bin/env python3
"""FX-06.3: the driver's runflow decisions, asserted directly.

The block this suite pins lives in scripts/loop/loop_until.sh, right after
make_run_dir: it reads the mode verb exactly once and hands those bytes to
scripts/loop/loop_runflow.py. Driving the shell itself needs a subprocess,
which a file this build writes may not import, so every case here feeds the
module the exact bytes a mode verb can print: shadow, on, an unknown value
with its NOTE, and the empty answer of a missing or broken reader.

Run: python3 -B scripts/loop/test_loop_runflow_fx063.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import loop_runflow

UNKNOWN_VALUE = ("off\n"
                 "NOTE BROTHER_RUNFLOW=banana is not off, shadow or on; read as off\n")


class Resolve(unittest.TestCase):
    """The word the driver tests: the mode verb's first line, nothing else."""

    def test_shadow_and_on_are_the_two_recording_words(self):
        self.assertEqual(loop_runflow.resolve("shadow\n"), "shadow")
        self.assertEqual(loop_runflow.resolve("on\n"), "on")

    def test_any_other_word_reads_off(self):
        self.assertEqual(loop_runflow.resolve("off\n"), "off")
        self.assertEqual(loop_runflow.resolve("banana\n"), "off")
        self.assertEqual(loop_runflow.resolve(""), "off")
        self.assertEqual(loop_runflow.resolve("\n"), "off")

    def test_only_the_first_line_decides(self):
        self.assertEqual(loop_runflow.resolve("shadow\nNOTE ignored\n"), "shadow")
        self.assertEqual(loop_runflow.resolve(UNKNOWN_VALUE), "off")


class Notes(unittest.TestCase):
    """One NOTE line in the log for an unknown value, and none otherwise."""

    def test_an_unknown_value_is_noted_once(self):
        line = loop_runflow.note_line(UNKNOWN_VALUE)
        self.assertEqual(line.count("NOTE runflow:"), 1)
        self.assertIn("BROTHER_RUNFLOW=banana", line)

    def test_a_recording_word_writes_no_note_line(self):
        self.assertEqual(loop_runflow.note_line("shadow\n"), "")
        self.assertEqual(loop_runflow.note_line("on\n"), "")

    def test_a_reader_that_printed_nothing_writes_no_note_line(self):
        self.assertEqual(loop_runflow.note_line(""), "")
        self.assertEqual(loop_runflow.note_line("\n"), "")

    def test_a_second_line_without_a_note_is_not_a_note(self):
        self.assertEqual(loop_runflow.note_line("shadow\nextra\n"), "")


class Decisions(unittest.TestCase):
    """The two decisions the driver's begin and end calls sit behind."""

    def test_off_records_nothing(self):
        self.assertFalse(loop_runflow.records("off"))
        self.assertTrue(loop_runflow.records("shadow"))
        self.assertTrue(loop_runflow.records("on"))

    def test_a_failed_end_records_failed(self):
        self.assertEqual(loop_runflow.end_state(True), "FAILED")
        self.assertEqual(loop_runflow.end_state(False), "DONE")

    def test_plan_gives_the_word_then_the_note_line(self):
        word, line = loop_runflow.plan(UNKNOWN_VALUE).split("\n")
        self.assertEqual(word, "off")
        self.assertTrue(line.startswith("NOTE runflow: "))
        shadow = loop_runflow.plan("shadow\n").split("\n")
        self.assertEqual(shadow[0], "shadow")
        self.assertEqual(shadow[1], "")


class Hostile(unittest.TestCase):
    """A wrong type is refused by the module's own ValueError, never a crash
    and never a silent accept."""

    def test_a_wrong_type_is_refused_never_a_crash(self):
        bad = (None, 0, 1, False, True, b"off", [], {}, 3.5, float("nan"))
        for value in bad:
            for fn in (loop_runflow.resolve, loop_runflow.note,
                       loop_runflow.note_line, loop_runflow.plan,
                       loop_runflow.records):
                with self.assertRaises(ValueError):
                    fn(value)
        for value in (None, "FAILED", 0, 1, b"no", [], {}):
            with self.assertRaises(ValueError):
                loop_runflow.end_state(value)


if __name__ == "__main__":
    unittest.main()
