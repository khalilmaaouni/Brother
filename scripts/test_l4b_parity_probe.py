#!/usr/bin/env python3
"""L4b.3 parity gate for counts and transition line: the runnable guard.

This is the file the specification names for unit L4b.3 (NEW,
scripts/test_l4b_parity_probe.py) and its done_check runs it directly. It
drives, on fixtures only, the probe in scripts/l4b_parity_probe.py, which is
the module under test living beside it:

  RQ-PARITY-COUNTS   two saved gate outputs agree on their PASS, FAIL and
                     NO-DATA counts and on the named checks their transition
                     line carries; a drift in either is reported as failure.
  RQ-SUMMARY-STABLE  the comparison reads the run_check summary line shape and
                     rewrites nothing; the same pair gives the same verdict
                     twice.

Edges covered: an empty log blocks, a corrupt log blocks, concurrent runs are
compared pairwise only, a stale log is screened by mtime plus size, and an
already done comparison is repeatable without side effects. Hermetic: fixture
strings and temp files in a temp directory only, no live gate log, no process,
no gate file written. Unrecognized input raises ValueError, never a crash.
Red without the code: every test calls the probe module through _probe(),
which fails loudly when that module is missing, and the fixtures are built so
that a probe that ignores the transition line or the counts cannot pass.
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import l4b_parity_probe
except ImportError as exc:
    l4b_parity_probe = None
    PROBE_IMPORT_ERROR = exc
else:
    PROBE_IMPORT_ERROR = None


def _probe():
    """The probe module under test, or a loud failure when it is absent."""
    if l4b_parity_probe is None:
        raise AssertionError(
            "scripts/l4b_parity_probe.py is missing or unimportable: %s"
            % (PROBE_IMPORT_ERROR,)
        )
    return l4b_parity_probe


TRANSITION_LINE = "TRANSITION: version-truth brother-run export-public plugin-manifest"

GATE_OUTPUT = (
    "Brother: required-fast, the pre-merge contract\n"
    "\n"
    "PASS    exit 0   version-truth        0s   OK\n"
    "PASS    exit 0   brother-run          12s  OK  [full: /tmp/required-fast-pass-brother-run-wt1-1.txt]\n"
    "FAIL    exit 1   export-public        3s   FAIL tests/test_export_public.py\n"
    "NO-DATA exit 2   plugin-manifest      0s   claude binary not found\n"
    "COUNTS: pass=2 fail=1 nodata=1\n"
    + TRANSITION_LINE
    + "\n"
)

NAME_DRIFT = GATE_OUTPUT.replace(
    TRANSITION_LINE,
    TRANSITION_LINE + " shared-config",
)

COUNT_DRIFT = GATE_OUTPUT.replace(
    "COUNTS: pass=2 fail=1 nodata=1",
    "COUNTS: pass=3 fail=1 nodata=1",
).replace(
    "FAIL    exit 1   export-public",
    "PASS    exit 0   shared-config        1s   OK\n"
    "FAIL    exit 1   export-public",
)

CORRUPT_COUNTS = GATE_OUTPUT.replace(
    "COUNTS: pass=2 fail=1 nodata=1",
    "COUNTS: pass=9 fail=1 nodata=1",
)

NO_TRANSITION = GATE_OUTPUT.replace(TRANSITION_LINE + "\n", "")
EMPTY_TRANSITION = GATE_OUTPUT.replace(TRANSITION_LINE, "TRANSITION:")
DOUBLE_TRANSITION = GATE_OUTPUT + TRANSITION_LINE + "\n"
DOUBLE_COUNTS = GATE_OUTPUT + "COUNTS: pass=2 fail=1 nodata=1\n"
MALFORMED_SUMMARY = GATE_OUTPUT.replace(
    "PASS    exit 0   version-truth        0s   OK\n",
    "PASS    exit\n",
)
ONLY_PROSE = "Brother: required-fast, the pre-merge contract\n\n"


class ParityCountsTest(unittest.TestCase):

    def test_identical_pair_passes(self):
        probe = _probe()
        self.assertTrue(probe.check_parity_counts(GATE_OUTPUT, GATE_OUTPUT))
        parsed = probe.parse_gate_output(GATE_OUTPUT)
        self.assertEqual((parsed["pass"], parsed["fail"], parsed["nodata"]), (2, 1, 1))
        self.assertEqual(
            parsed["transition_names"],
            ("brother-run", "export-public", "plugin-manifest", "version-truth"),
        )

    def test_transition_drift_is_reported(self):
        probe = _probe()
        self.assertFalse(probe.check_parity_counts(GATE_OUTPUT, NAME_DRIFT))
        self.assertNotEqual(
            probe.parse_gate_output(GATE_OUTPUT)["transition_names"],
            probe.parse_gate_output(NAME_DRIFT)["transition_names"],
        )

    def test_count_drift_is_reported(self):
        probe = _probe()
        self.assertFalse(probe.check_parity_counts(GATE_OUTPUT, COUNT_DRIFT))
        self.assertEqual(probe.parse_gate_output(COUNT_DRIFT)["pass"], 3)

    def test_missing_after_blocks(self):
        probe = _probe()
        with self.assertRaises(ValueError):
            probe.check_parity_counts(GATE_OUTPUT)
        with self.assertRaises(ValueError):
            probe.check_parity_counts(GATE_OUTPUT, None)

    def test_empty_log_blocks(self):
        probe = _probe()
        for empty in ("", "   \n\n\t\n"):
            with self.assertRaises(ValueError):
                probe.parse_gate_output(empty)
        with self.assertRaises(ValueError):
            probe.check_parity_counts(GATE_OUTPUT, "")
        with self.assertRaises(ValueError):
            probe.check_parity_counts("", GATE_OUTPUT)

    def test_corrupt_log_blocks(self):
        probe = _probe()
        for corrupt in (
            CORRUPT_COUNTS,
            NO_TRANSITION,
            EMPTY_TRANSITION,
            DOUBLE_TRANSITION,
            DOUBLE_COUNTS,
            MALFORMED_SUMMARY,
            ONLY_PROSE,
        ):
            with self.assertRaises(ValueError):
                probe.parse_gate_output(corrupt)
        with self.assertRaises(ValueError):
            probe.check_parity_counts(GATE_OUTPUT, CORRUPT_COUNTS)

    def test_hostile_input_is_refused(self):
        probe = _probe()
        bad_values = (None, 5, 5.0, True, False, [], {}, b"bytes", float("nan"), object())
        for bad in bad_values:
            with self.assertRaises(ValueError):
                probe.parse_gate_output(bad)
            with self.assertRaises(ValueError):
                probe.check_parity_counts(bad, GATE_OUTPUT)
            with self.assertRaises(ValueError):
                probe.check_parity_counts(GATE_OUTPUT, bad)
        with self.assertRaises(ValueError):
            probe.parse_gate_output(GATE_OUTPUT, marker=None)
        with self.assertRaises(ValueError):
            probe.parse_gate_output(GATE_OUTPUT, marker=7)
        with self.assertRaises(ValueError):
            probe.parse_gate_output(GATE_OUTPUT, marker="   ")

    def test_comparison_is_repeatable(self):
        probe = _probe()
        first = probe.check_parity_counts(GATE_OUTPUT, GATE_OUTPUT)
        second = probe.check_parity_counts(GATE_OUTPUT, GATE_OUTPUT)
        self.assertTrue(first)
        self.assertEqual(first, second)
        self.assertFalse(probe.check_parity_counts(GATE_OUTPUT, NAME_DRIFT))
        self.assertFalse(probe.check_parity_counts(GATE_OUTPUT, NAME_DRIFT))

    def test_concurrent_runs_are_compared_pairwise(self):
        probe = _probe()
        lane_a_before = GATE_OUTPUT
        lane_a_after = GATE_OUTPUT
        lane_b_before = GATE_OUTPUT.replace("brother-run", "worktree-lane")
        lane_b_after = GATE_OUTPUT.replace("brother-run", "worktree-lane")
        self.assertTrue(probe.check_parity_counts(lane_a_before, lane_a_after))
        self.assertTrue(probe.check_parity_counts(lane_b_before, lane_b_after))
        self.assertFalse(probe.check_parity_counts(lane_a_before, lane_b_after))


class ReadGateOutputTest(unittest.TestCase):

    def _write_bytes(self, path, raw):
        with open(path, "wb") as handle:
            handle.write(raw)

    def test_read_returns_the_saved_text(self):
        probe = _probe()
        with tempfile.TemporaryDirectory() as scratch:
            path = os.path.join(scratch, "gate.out")
            self._write_bytes(path, GATE_OUTPUT.encode("utf-8"))
            self.assertTrue(probe.check_parity_counts(
                probe.read_gate_output(path), GATE_OUTPUT))

    def test_read_refuses_non_utf8(self):
        probe = _probe()
        with tempfile.TemporaryDirectory() as scratch:
            path = os.path.join(scratch, "gate.out")
            self._write_bytes(path, b"\xff\xfe\x00bad")
            with self.assertRaises(ValueError):
                probe.read_gate_output(path)

    def test_read_refuses_missing_directory_and_empty(self):
        probe = _probe()
        with tempfile.TemporaryDirectory() as scratch:
            missing = os.path.join(scratch, "absent.out")
            blank = os.path.join(scratch, "blank.out")
            self._write_bytes(blank, b"")
            with self.assertRaises(ValueError):
                probe.read_gate_output(missing)
            with self.assertRaises(ValueError):
                probe.read_gate_output(scratch)
            with self.assertRaises(ValueError):
                probe.read_gate_output(blank)
        for bad in (None, 5, True, [], b"bytes", float("nan"), ""):
            with self.assertRaises(ValueError):
                probe.read_gate_output(bad)


class StaleScreenTest(unittest.TestCase):

    def _write(self, path, body):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)

    def test_stale_pair_is_screened(self):
        probe = _probe()
        with tempfile.TemporaryDirectory() as scratch:
            before = os.path.join(scratch, "before.out")
            after = os.path.join(scratch, "after.out")
            self._write(before, GATE_OUTPUT)
            self._write(after, GATE_OUTPUT)
            self.assertTrue(probe.screen_stale(before, before))
            stat = os.stat(before)
            os.utime(after, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertTrue(probe.screen_stale(before, after))
            os.utime(after, ns=(stat.st_atime_ns + 1000000000, stat.st_mtime_ns + 1000000000))
            self.assertFalse(probe.screen_stale(before, after))

    def test_zero_byte_side_is_stale(self):
        probe = _probe()
        with tempfile.TemporaryDirectory() as scratch:
            before = os.path.join(scratch, "before.out")
            after = os.path.join(scratch, "after.out")
            self._write(before, GATE_OUTPUT)
            self._write(after, "")
            self.assertTrue(probe.screen_stale(before, after))
            self.assertTrue(probe.screen_stale(after, before))

    def test_missing_file_blocks(self):
        probe = _probe()
        with tempfile.TemporaryDirectory() as scratch:
            before = os.path.join(scratch, "before.out")
            self._write(before, GATE_OUTPUT)
            missing = os.path.join(scratch, "absent.out")
            with self.assertRaises(ValueError):
                probe.screen_stale(before, missing)

    def test_directory_where_file_belongs_blocks(self):
        probe = _probe()
        with tempfile.TemporaryDirectory() as scratch:
            before = os.path.join(scratch, "before.out")
            self._write(before, GATE_OUTPUT)
            with self.assertRaises(ValueError):
                probe.screen_stale(before, scratch)

    def test_screen_refuses_hostile_input(self):
        probe = _probe()
        for bad in (None, 5, True, [], b"bytes", float("nan")):
            with self.assertRaises(ValueError):
                probe.screen_stale(bad, "somewhere")
            with self.assertRaises(ValueError):
                probe.screen_stale("somewhere", bad)
        with self.assertRaises(ValueError):
            probe.screen_stale("", "somewhere")


if __name__ == "__main__":
    unittest.main()
