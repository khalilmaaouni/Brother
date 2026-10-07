#!/usr/bin/env python3
"""L5d.c: gate log ingest, reuse only (REQ-L5D-PARSE).

parse_log must read every row of a real gate log. Hostile input (None, a
wrong type, an unhashable element, undecodable bytes) is refused with the
module's own NoDataError, CorruptLogError or ValueError, never a raw
interpreter exception. This suite lives beside scripts/gate_order.py.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_order


REAL_GATE_LOG = (
    "PASS    exit 0   version-truth    12s  ok\n"
    "PASS    exit 0   bundle-runtime   170s  ok\n"
    "FAIL    exit 1   mobile-workflow  5s   boom\n"
    "NO-DATA exit 2   native-build     3s   missing\n"
)


class TestParseLogRowIngestL5dc(unittest.TestCase):
    def _write_log(self, text: str) -> str:
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_parse_log_reads_every_row_of_a_real_gate_log(self) -> None:
        path = self._write_log(REAL_GATE_LOG)
        results = gate_order.parse_log(path)
        self.assertEqual(len(results), 4)
        self.assertEqual(
            [r['name'] for r in results],
            ['version-truth', 'bundle-runtime', 'mobile-workflow', 'native-build'],
        )
        self.assertEqual(
            [r['status'] for r in results],
            ['PASS', 'PASS', 'FAIL', 'NO-DATA'],
        )
        self.assertEqual([r['exit'] for r in results], [0, 0, 1, 2])
        self.assertEqual([r['seconds'] for r in results], [12, 170, 5, 3])

    def test_parse_log_rows_reads_every_row_l5dc(self) -> None:
        path = self._write_log(REAL_GATE_LOG)
        self.assertEqual(len(gate_order.parse_log_rows(path)), 4)


class TestParseLogHostileL5dc(unittest.TestCase):
    def test_parse_log_none_path_refuses_l5dc(self) -> None:
        with self.assertRaises(gate_order.NoDataError):
            gate_order.parse_log(None)

    def test_parse_log_int_path_refuses_l5dc(self) -> None:
        with self.assertRaises(gate_order.NoDataError):
            gate_order.parse_log(42)

    def test_parse_log_list_path_refuses_l5dc(self) -> None:
        with self.assertRaises(gate_order.NoDataError):
            gate_order.parse_log(['nope'])

    def test_parse_log_missing_file_refuses_l5dc(self) -> None:
        with self.assertRaises(gate_order.NoDataError):
            gate_order.parse_log('/nonexistent/l5dc/no-such-gate-log')

    def test_parse_log_directory_refuses_l5dc(self) -> None:
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, tmpdir)
        with self.assertRaises(gate_order.NoDataError):
            gate_order.parse_log(tmpdir)

    def test_parse_log_non_utf8_bytes_refuses_l5dc(self) -> None:
        fd, path = tempfile.mkstemp()
        os.close(fd)
        with open(path, 'wb') as f:
            f.write(b"PASS    exit 0   x   1s  ok\n\xff\xfe not utf8")
        self.addCleanup(os.unlink, path)
        with self.assertRaises(gate_order.CorruptLogError):
            gate_order.parse_log(path)

    def test_parse_log_rows_refuses_non_string_path_l5dc(self) -> None:
        with self.assertRaises(gate_order.NoDataError):
            gate_order.parse_log_rows(None)


class TestHistoryHostileL5dc(unittest.TestCase):
    def test_history_none_refuses_l5dc(self) -> None:
        with self.assertRaises(gate_order.NoDataError):
            gate_order.history(None)

    def test_history_int_refuses_l5dc(self) -> None:
        with self.assertRaises(gate_order.NoDataError):
            gate_order.history(42)

    def test_history_string_refuses_l5dc(self) -> None:
        with self.assertRaises(gate_order.NoDataError) as cm:
            gate_order.history("not a list of paths")
        self.assertIn("list or tuple", str(cm.exception))


class TestProposeOrderHostileL5dc(unittest.TestCase):
    def test_propose_order_history_none_refuses_l5dc(self) -> None:
        with self.assertRaises(ValueError):
            gate_order.propose_order(None, ['a'])

    def test_propose_order_current_none_refuses_l5dc(self) -> None:
        hist = {'a': {'runs': 1, 'fails': 0, 'mean_seconds': 1.0}}
        with self.assertRaises(ValueError):
            gate_order.propose_order(hist, None)

    def test_propose_order_unhashable_refuses_l5dc(self) -> None:
        with self.assertRaises(ValueError):
            gate_order.propose_order({}, [['a']])


class TestExpectedTimeToFirstFailureHostileL5dc(unittest.TestCase):
    def test_expected_ttff_order_none_refuses_l5dc(self) -> None:
        with self.assertRaises(ValueError):
            gate_order.expected_time_to_first_failure(None, {})

    def test_expected_ttff_history_none_refuses_l5dc(self) -> None:
        with self.assertRaises(ValueError):
            gate_order.expected_time_to_first_failure(['a'], None)


if __name__ == '__main__':
    unittest.main()
