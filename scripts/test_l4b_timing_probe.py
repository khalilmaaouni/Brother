#!/usr/bin/env python3
"""L4b.4 timing link probe test: hermetic timing record for PASS checks.

Specification, unit L4b.4 "Timing link probe": the probe wraps a minimal
run_check style loop, records start and end with second resolution, and
asserts durations are present and non negative. It does not change gate logic.

Requirements defended here:
  RQ-TIMING-LINK  before and after wall clock and per check durations are
                  recorded so output handling cost is visible.

Edges covered: an empty timing file blocks, a corrupt timing value blocks, a
missing duration never reads as fast, a stale timing file is truncated at
start, concurrent probes use distinct temp files, an already done measurement
is reread without rerun, hostile bytes lead to ValueError and a wrong type is
refused rather than crashing. Hermetic: temp HOME and temp TMPDIR only, no
live gate run, no process.

Red without the code: every test calls _probe(), which refuses loudly when
scripts/l4b_timing_probe.py is absent, and the assertions below read behaviour
the module adds (timing record written, parsed, and refused when corrupt).
"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import l4b_timing_probe
except ImportError as exc:
    l4b_timing_probe = None
    PROBE_IMPORT_ERROR = exc
else:
    PROBE_IMPORT_ERROR = None


def _probe():
    if l4b_timing_probe is None:
        raise AssertionError(
            "scripts/l4b_timing_probe.py is missing or unimportable: %s"
            % (PROBE_IMPORT_ERROR,)
        )
    return l4b_timing_probe


class TimingLinkProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="l4b4-")
        self.addCleanup(self._drop_tree, self.tmpdir)

    def _drop_tree(self, path):
        if not os.path.isdir(path):
            return
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                os.unlink(os.path.join(root, name))
            for name in dirs:
                os.rmdir(os.path.join(root, name))
        os.rmdir(path)

    def _write(self, name, data):
        path = os.path.join(self.tmpdir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_record_and_read_duration(self):
        probe = _probe()
        path = os.path.join(self.tmpdir, "timing.txt")
        duration = probe.run_timing_probe(path)
        self.assertGreaterEqual(duration, 0)
        self.assertTrue(probe.check_timing_link(path))

    def test_missing_file_blocks(self):
        probe = _probe()
        with self.assertRaises(probe.TimingError):
            probe.check_timing_link(os.path.join(self.tmpdir, "nope.txt"))

    def test_empty_file_blocks(self):
        probe = _probe()
        path = self._write("empty.txt", b"")
        with self.assertRaisesRegex(probe.TimingError, "empty"):
            probe.check_timing_link(path)

    def test_corrupt_value_blocks(self):
        probe = _probe()
        path = self._write("corrupt.txt", b"start=1\nend=2\nduration=fast\n")
        with self.assertRaisesRegex(probe.TimingError, "corrupt timing value"):
            probe.check_timing_link(path)

    def test_negative_duration_blocks(self):
        probe = _probe()
        path = self._write("negative.txt", b"start=2\nend=1\nduration=-1\n")
        with self.assertRaisesRegex(probe.TimingError, "negative"):
            probe.check_timing_link(path)

    def test_missing_duration_never_reads_as_fast(self):
        probe = _probe()
        path = self._write("nodur.txt", b"start=1\nend=2\n")
        with self.assertRaisesRegex(probe.TimingError, "missing"):
            probe.check_timing_link(path)

    def test_hostile_bytes_block(self):
        probe = _probe()
        path = self._write("hostile.txt", b"\xff\xfe\x00")
        with self.assertRaisesRegex(probe.TimingError, "UTF-8"):
            probe.check_timing_link(path)

    def test_wrong_type_blocks(self):
        probe = _probe()
        for bad in (None, 42, True, 3.5, b"path", ["p"]):
            with self.assertRaises(probe.TimingError):
                probe.check_timing_link(bad)

    def test_empty_path_blocks(self):
        probe = _probe()
        for bad in ("", "   "):
            with self.assertRaises(probe.TimingError):
                probe.check_timing_link(bad)

    def test_directory_blocks(self):
        probe = _probe()
        with self.assertRaises(probe.TimingError):
            probe.check_timing_link(self.tmpdir)

    def test_stale_file_is_truncated_at_start(self):
        probe = _probe()
        path = self._write("stale.txt", b"start=0\nend=0\nduration=0\n")
        probe.run_timing_probe(path)
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertNotIn(b"start=0\n", data)
        self.assertNotIn(b"end=0\n", data)
        self.assertTrue(probe.check_timing_link(path))

    def test_concurrent_probes_use_distinct_files(self):
        probe = _probe()
        path1 = os.path.join(self.tmpdir, "t1.txt")
        path2 = os.path.join(self.tmpdir, "t2.txt")
        probe.run_timing_probe(path1)
        probe.run_timing_probe(path2)
        self.assertNotEqual(path1, path2)
        self.assertTrue(probe.check_timing_link(path1))
        self.assertTrue(probe.check_timing_link(path2))

    def test_already_done_measurement_can_be_reread(self):
        probe = _probe()
        path = os.path.join(self.tmpdir, "done.txt")
        probe.run_timing_probe(path)
        self.assertTrue(probe.check_timing_link(path))
        self.assertTrue(probe.check_timing_link(path))

    def test_run_timing_probe_refuses_wrong_type(self):
        probe = _probe()
        with self.assertRaises(probe.TimingError):
            probe.run_timing_probe(None)
        with self.assertRaises(probe.TimingError):
            probe.run_timing_probe(self.tmpdir)


if __name__ == "__main__":
    unittest.main()
