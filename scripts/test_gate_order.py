import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_order


class TestParseLog(unittest.TestCase):
    def _write_log(self, text):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_parses_pass_and_fail_lines(self):
        path = self._write_log(
            "PASS    exit 0   version-truth         12s  all good\n"
            "FAIL    exit 1   bundle-runtime       170s  boom\n"
        )
        results = gate_order.parse_log(path)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], {
            'name': 'version-truth',
            'status': 'PASS',
            'exit': 0,
            'seconds': 12,
            'summary': 'all good',
        })
        self.assertEqual(results[1]['name'], 'bundle-runtime')
        self.assertEqual(results[1]['status'], 'FAIL')
        self.assertEqual(results[1]['exit'], 1)
        self.assertEqual(results[1]['seconds'], 170)
        self.assertEqual(results[1]['summary'], 'boom')

    def test_parses_no_data_line(self):
        path = self._write_log(
            "NO-DATA exit 2   mobile-workflow        5s  missing binary\n"
        )
        results = gate_order.parse_log(path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'NO-DATA')
        self.assertEqual(results[0]['exit'], 2)

    def test_no_result_lines_raises_no_data(self):
        path = self._write_log("just a header\nand nothing else\n")
        with self.assertRaises(gate_order.NoDataError):
            gate_order.parse_log(path)

    def test_missing_file_raises_no_data(self):
        with self.assertRaises(gate_order.NoDataError):
            gate_order.parse_log("/nonexistent/path/to/log")

    def test_corrupt_result_line_raises_corrupt(self):
        path = self._write_log(
            "PASS    exit notanumber   name   12s  summary\n"
        )
        with self.assertRaises(gate_order.CorruptLogError):
            gate_order.parse_log(path)

    def test_ignores_non_result_lines(self):
        path = self._write_log(
            "Brother: required-fast\n"
            "\n"
            "PASS    exit 0   version-truth         12s  ok\n"
            "pass 1   fail 0   no-data 0\n"
        )
        results = gate_order.parse_log(path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'version-truth')

    def test_parse_log_reads_every_row_of_a_real_gate_log(self) -> None:
        path = self._write_log(
            "PASS    exit 0   check-a   12s  ok\n"
            "FAIL    exit 1   check-b   170s  boom\n"
            "NO-DATA exit 2   check-c   5s   missing\n"
            "PASS    exit 0   check-d   3s   ok\n"
        )
        results = gate_order.parse_log(path)
        self.assertEqual(len(results), 4)
        self.assertEqual(
            [r['name'] for r in results],
            ['check-a', 'check-b', 'check-c', 'check-d'],
        )
        self.assertEqual([r['exit'] for r in results], [0, 1, 2, 0])
        self.assertEqual(
            [r['status'] for r in results], ['PASS', 'FAIL', 'NO-DATA', 'PASS']
        )


class TestHistory(unittest.TestCase):
    def _write_log(self, text):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_aggregates_runs_fails_and_mean_seconds(self):
        p1 = self._write_log(
            "PASS    exit 0   a   10s  ok\n"
            "FAIL    exit 1   a   20s  bad\n"
            "PASS    exit 0   b   5s   ok\n"
        )
        p2 = self._write_log(
            "FAIL    exit 1   a   30s  bad again\n"
            "PASS    exit 0   b   15s  ok\n"
        )
        hist = gate_order.history([p1, p2])
        self.assertEqual(hist['a']['runs'], 3)
        self.assertEqual(hist['a']['fails'], 2)
        self.assertAlmostEqual(hist['a']['mean_seconds'], (10 + 20 + 30) / 3)
        self.assertEqual(hist['b']['runs'], 2)
        self.assertEqual(hist['b']['fails'], 0)
        self.assertAlmostEqual(hist['b']['mean_seconds'], (5 + 15) / 2)

    def test_history_propagates_no_data(self):
        p1 = self._write_log("no results here\n")
        with self.assertRaises(gate_order.NoDataError):
            gate_order.history([p1])


class TestProposeOrder(unittest.TestCase):
    def test_reorders_by_fail_rate_per_second(self):
        hist = {
            'slow-fail': {'runs': 10, 'fails': 5, 'mean_seconds': 100.0},
            'fast-fail': {'runs': 10, 'fails': 1, 'mean_seconds': 1.0},
            'never-fail': {'runs': 10, 'fails': 0, 'mean_seconds': 50.0},
        }
        current = ['slow-fail', 'never-fail', 'fast-fail']
        proposed = gate_order.propose_order(hist, current)
        self.assertEqual(proposed, ['fast-fail', 'slow-fail', 'never-fail'])

    def test_tie_break_preserves_current_order(self):
        hist = {
            'a': {'runs': 10, 'fails': 2, 'mean_seconds': 10.0},
            'b': {'runs': 10, 'fails': 2, 'mean_seconds': 10.0},
            'c': {'runs': 10, 'fails': 1, 'mean_seconds': 10.0},
        }
        current = ['c', 'a', 'b']
        proposed = gate_order.propose_order(hist, current)
        self.assertEqual(proposed, ['a', 'b', 'c'])

    def test_set_mismatch_raises_value_error(self):
        hist = {'a': {'runs': 1, 'fails': 1, 'mean_seconds': 1.0}}
        current = ['a', 'b']
        with self.assertRaises(ValueError):
            gate_order.propose_order(hist, current)


class TestExpectedTimeToFirstFailure(unittest.TestCase):
    def test_computes_conditional_expected_time(self):
        hist = {
            'a': {'runs': 10, 'fails': 2, 'mean_seconds': 10.0},
            'b': {'runs': 10, 'fails': 5, 'mean_seconds': 20.0},
        }
        order = ['a', 'b']
        ettf = gate_order.expected_time_to_first_failure(order, hist)
        self.assertAlmostEqual(ettf, 10.0 / 0.6)

    def test_no_fails_raises_no_data(self):
        hist = {
            'a': {'runs': 10, 'fails': 0, 'mean_seconds': 10.0},
        }
        with self.assertRaises(gate_order.NoDataError):
            gate_order.expected_time_to_first_failure(['a'], hist)


class TestParseCurrentOrder(unittest.TestCase):
    def test_extracts_unique_run_check_names_in_order(self):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(
                'run_check "first"  cmd1\n'
                'run_check "second" cmd2\n'
                'run_check "first"  cmd3\n'
                'if [ -f x ]; then\n'
                '  run_check "third" cmd4\n'
                'else\n'
                '  run_check "fourth" cmd5\n'
                'fi\n'
                'run_check "third" cmd6\n'
            )
        self.addCleanup(os.unlink, path)
        order = gate_order.parse_current_order(path)
        self.assertEqual(order, ['first', 'second', 'third', 'fourth'])

    def test_missing_gate_raises_no_data(self):
        with self.assertRaises(gate_order.NoDataError):
            gate_order.parse_current_order('/nonexistent/gate.sh')

    def test_gate_with_no_run_check_raises_no_data(self):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('nothing here\n')
        self.addCleanup(os.unlink, path)
        with self.assertRaises(gate_order.NoDataError):
            gate_order.parse_current_order(path)


if __name__ == '__main__':
    unittest.main()
