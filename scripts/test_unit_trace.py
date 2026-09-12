"""What the trace must keep true.

Every case here comes from FL-1's own done-check rather than from imagination.
The two that matter most are the NO-DATA case and the concurrency case, because
each is a way for a trace to look complete while being wrong: a zero where the
host knew nothing poisons every average built on it, and a lost line silently
understates a cost that nobody can then detect.
"""
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unit_trace as U  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

HERE = os.path.dirname(os.path.abspath(__file__))


class OneDispatchWritesOneJoinableLine(unittest.TestCase):

    def setUp(self):
        d = tempfile.mkdtemp(prefix="unit-trace-")
        self.path = os.path.join(d, "trace.jsonl")

    def test_exactly_one_line_per_dispatch(self):
        row, problem = U.record("claim-abc/FL-1/1", tier="builder",
                                effort="high", tokens_in=1200,
                                tokens_out=340, cache_read=900,
                                wall_ms=41200, verdict="pass", path=self.path)
        self.assertEqual(problem, "")
        rows, problem = U.read_all(self.path)
        self.assertEqual(problem, "")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["claim_id"], "claim-abc/FL-1/1")

    def test_the_line_carries_all_eight_contract_fields(self):
        U.record("claim-abc/FL-1/1", tier="builder", effort="high",
                 tokens_in=1, tokens_out=2, cache_read=3, wall_ms=4,
                 verdict="pass", path=self.path)
        rows, _ = U.read_all(self.path)
        for field in U.FIELDS:
            self.assertIn(field, rows[0])

    def test_the_stored_line_is_one_json_object_per_line(self):
        U.record("c1", path=self.path)
        U.record("c2", path=self.path)
        with open(self.path, encoding="utf-8") as fh:
            lines = [l for l in fh.read().split("\n") if l.strip()]
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertIsInstance(json.loads(line), dict)

    def test_a_second_append_never_rewrites_the_first(self):
        U.record("c1", tier="scout", path=self.path)
        U.record("c2", tier="builder", path=self.path)
        rows, _ = U.read_all(self.path)
        self.assertEqual([r["tier"] for r in rows], ["scout", "builder"])


class ALineWithoutAClaimIsRefused(unittest.TestCase):

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="unit-trace-"),
                                 "trace.jsonl")

    def test_no_claim_id_is_refused(self):
        row, problem = U.record(None, path=self.path)
        self.assertIsNone(row)
        self.assertIn("claim_id", problem)

    def test_a_blank_claim_id_is_refused(self):
        row, problem = U.record("   ", path=self.path)
        self.assertIsNone(row)
        self.assertIn("claim_id", problem)

    def test_the_refusal_writes_nothing_at_all(self):
        U.record("", path=self.path)
        U.record("  ", path=self.path)
        rows, problem = U.read_all(self.path)
        self.assertEqual(problem, "")
        self.assertEqual(rows, [])

    def test_a_refused_record_does_not_create_the_store(self):
        U.record(None, path=self.path)
        self.assertFalse(os.path.exists(self.path))


class AFieldTheHostCannotSupplyIsNoData(unittest.TestCase):

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="unit-trace-"),
                                 "trace.jsonl")

    def test_an_absent_field_is_the_string_no_data(self):
        row, _ = U.record("c1", tier="builder", path=self.path)
        self.assertEqual(row["tokens_in"], U.NODATA)
        self.assertEqual(row["tokens_out"], U.NODATA)
        self.assertEqual(row["cache_read"], U.NODATA)
        self.assertEqual(row["wall_ms"], U.NODATA)

    def test_no_data_is_never_written_as_a_zero(self):
        """The whole point of the sentinel: a host that knows nothing must not
        look like a host that spent nothing."""
        U.record("c1", path=self.path)
        rows, _ = U.read_all(self.path)
        for field in ("tokens_in", "tokens_out", "cache_read", "wall_ms"):
            self.assertNotEqual(rows[0][field], 0)
            self.assertEqual(rows[0][field], U.NODATA)

    def test_a_real_zero_passed_by_the_host_survives_as_zero(self):
        """A cache read of zero is a measurement, not a missing value."""
        U.record("c1", cache_read=0, path=self.path)
        rows, _ = U.read_all(self.path)
        self.assertEqual(rows[0]["cache_read"], 0)

    def test_an_empty_string_counts_as_not_supplied(self):
        U.record("c1", verdict="", path=self.path)
        rows, _ = U.read_all(self.path)
        self.assertEqual(rows[0]["verdict"], U.NODATA)


class TwoWritersLoseNoLine(unittest.TestCase):
    """Threads rather than processes on purpose. The atomicity being tested
    belongs to the single O_APPEND os.write, which is the same syscall path
    either way, and a thread based test cannot hang on a spawn import in a
    sandbox that ships this file without the rest of the tree."""

    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(prefix="unit-trace-"),
                                 "trace.jsonl")

    def test_concurrent_appends_lose_no_line(self):
        writers, per_writer = 8, 25
        errors = []

        def worker(n):
            for i in range(per_writer):
                _, problem = U.record("claim-%d/%d" % (n, i), tier="builder",
                                      tokens_in=i, path=self.path)
                if problem:
                    errors.append(problem)

        threads = [threading.Thread(target=worker, args=(n,))
                   for n in range(writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        rows, problem = U.read_all(self.path)
        self.assertEqual(problem, "")
        self.assertEqual(len(rows), writers * per_writer)
        self.assertEqual(len({r["claim_id"] for r in rows}),
                         writers * per_writer)


class BROTHER_UNIT_TRACEPicksTheStoreWhenNoPathIsGiven(unittest.TestCase):
    """The FL-2 reader on another branch already depends on this variable
    name to find the trace without being handed a path argument: a path
    argument still wins over it (a caller that knows exactly where it wants
    the line written is never overridden by ambient state), but a caller
    with no path argument at all must land on the file this variable names,
    never on DEFAULT_STORE, and never a placeholder."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="unit-trace-env-")
        self.env_path = os.path.join(self.dir, "env-trace.jsonl")
        self.old = os.environ.get("BROTHER_UNIT_TRACE")
        os.environ["BROTHER_UNIT_TRACE"] = self.env_path

    def tearDown(self):
        if self.old is None:
            os.environ.pop("BROTHER_UNIT_TRACE", None)
        else:
            os.environ["BROTHER_UNIT_TRACE"] = self.old

    def test_no_path_argument_writes_to_the_env_path(self):
        row, problem = U.record("c1", tier="builder")
        self.assertEqual(problem, "")
        self.assertTrue(os.path.exists(self.env_path))
        rows, problem = U.read_all()
        self.assertEqual(problem, "")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["claim_id"], "c1")

    def test_an_explicit_path_argument_still_wins_over_the_variable(self):
        explicit = os.path.join(self.dir, "explicit-trace.jsonl")
        U.record("c1", path=explicit)
        self.assertFalse(os.path.exists(self.env_path))
        rows, _ = U.read_all(explicit)
        self.assertEqual(len(rows), 1)


class TheBatteryRunsThisFile(unittest.TestCase):
    """FL-1's done-check ends with the row being registered, so the row is only
    done when the battery actually runs it. A registration that was written and
    then lost to a later merge is the failure this catches."""

    def test_check_all_registers_unit_trace_self(self):
        with open(os.path.join(HERE, "check_all.sh"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("unit-trace-self", text)
        self.assertIn("scripts/test_unit_trace.py", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
