"""F21's second clause (MECHANISM-AUDIT item 21): probe_build.py's lessons() trusted a probe
log's file mtime for its 24 hour window, and mtime is not a trustworthy timestamp: a checkout,
restore or copy can reset or preserve it independent of when the probe actually ran.
probe_wave.py now writes a "# probe-at <epoch>" first line when it creates a probe log, and
probe_build.py's lessons() reads that marker instead of mtime; a log with no marker is excluded,
never guessed.

Imports probe_build directly and calls its real entry point, lessons(); does not read or
pattern-match probe_build.py's source text for this behaviour. probe_wave.py is not safely
importable the way probe_build.py is (its own real dispatch work sits at bare module level, the
same reason repair_wave.py cannot be import'd either), so its write side is proven by reading its
real defining line in isolation, the same method used throughout this sweep.
"""
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_build  # noqa: E402


def _make_log(base, sub, folder, name, marker_at, body):
    d = os.path.join(base, "%s-%s" % (sub, folder), "round0", "probes", "logs")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as fh:
        if marker_at is not None:
            fh.write("# probe-at %.6f\n" % marker_at)
        fh.write(body)
    return path


class TestLessonsReadsTheMarkerNeverMtime(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="probe-log-ts-")
        self.now = time.time()

    def test_a_recent_marker_is_in_the_window_even_with_an_old_mtime(self):
        path = _make_log(self.root, "D1.1", "000001", "D1.1-a.log", self.now, "CRASH x AttributeError y\n")
        os.utime(path, (self.now - 90000, self.now - 90000))
        out = probe_build.lessons(self.root, now=self.now)
        self.assertIn("1 findings", out, out)

    def test_an_old_marker_is_excluded_even_with_a_fresh_mtime(self):
        path = _make_log(self.root, "D1.2", "000002", "D1.2-a.log", self.now - 90000, "CRASH x AttributeError y\n")
        os.utime(path, (self.now, self.now))
        out = probe_build.lessons(self.root, now=self.now)
        self.assertEqual(out, "", out)

    def test_a_log_with_no_marker_is_excluded_never_guessed(self):
        _make_log(self.root, "D1.3", "000003", "D1.3-a.log", None, "CRASH x AttributeError y\n")
        out = probe_build.lessons(self.root, now=self.now)
        self.assertEqual(out, "", "a log with no probe-at marker was included: " + out)

    def test_a_corrupt_marker_line_is_excluded_never_guessed(self):
        d = os.path.join(self.root, "D1.4-000004", "round0", "probes", "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "D1.4-a.log"), "w", encoding="utf-8") as fh:
            fh.write("# probe-at not-a-number\nCRASH x AttributeError y\n")
        out = probe_build.lessons(self.root, now=self.now)
        self.assertEqual(out, "", out)


def _defining_line(path, needle):
    """The exact source line in `path` containing `needle`. Reads the REAL file so a test
    proves what its own code does, never a hand-written stand-in for it."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if needle in line:
                return line
    raise AssertionError("no line containing %r found in %s" % (needle, path))


class TestProbeWaveWritesTheMarker(unittest.TestCase):
    def test_probe_wave_stamps_a_probe_at_header_when_it_writes_a_fresh_log(self):
        path = os.path.join(HERE, "probe_wave.py")
        write_line = _defining_line(path, 'open(log, "w").write(')
        self.assertIn("probe-at", write_line,
                      "probe_wave.py's fresh-log write no longer stamps a probe-at marker: %r" % write_line)


if __name__ == "__main__":
    unittest.main()
