import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_ledger
import gate_order


class TestOrder(unittest.TestCase):
    def test_set_drift_raises(self):
        current_order = ["a", "b"]
        hist = {
            "a": {"runs": 10, "fails": 1, "mean_seconds": 1.0, "seconds": [1.0] * 10},
        }
        with self.assertRaises(ValueError):
            gate_order.pin_order(current_order, hist, "/tmp/c0-pin.json", "a" * 64, "b" * 64)

    def test_constrained_order_respects_dag(self):
        order = ["low", "high"]
        hist = {
            "low": {"runs": 10, "fails": 0, "mean_seconds": 1.0, "seconds": [1.0] * 10},
            "high": {"runs": 10, "fails": 10, "mean_seconds": 1.0, "seconds": [1.0] * 10},
        }
        dag = {"edges": [["low", "high"]]}
        got = gate_order.constrained_order(order, hist, dag)
        self.assertLess(got.index("low"), got.index("high"))

    def test_forecast_no_data_below_minimum(self):
        order = ["a"]
        hist = {"a": {"runs": 9, "fails": 0, "mean_seconds": 1.0, "seconds": [1.0] * 9}}
        with self.assertRaises(gate_order.NoDataError):
            gate_order.forecast_seconds(order, hist)

    def test_forecast_at_minimum_returns_numbers(self):
        order = ["a"]
        hist = {"a": {"runs": 10, "fails": 0, "mean_seconds": 1.0, "seconds": [1.0] * 10}}
        expected, margin = gate_order.forecast_seconds(order, hist)
        self.assertAlmostEqual(expected, 1.0)
        self.assertAlmostEqual(margin, 0.0)

    def test_empty_history_raises_no_data(self):
        with self.assertRaises(gate_order.NoDataError):
            gate_order.forecast_seconds(["a"], {})

    def test_empty_order_raises_no_data(self):
        with self.assertRaises(gate_order.NoDataError):
            gate_order.forecast_seconds([], {})

    def test_unknown_gate_in_dag_raises(self):
        order = ["a"]
        hist = {"a": {"runs": 10, "fails": 0, "mean_seconds": 1.0, "seconds": [1.0] * 10}}
        dag = {"edges": [["x", "a"]]}
        with self.assertRaises(ValueError):
            gate_order.constrained_order(order, hist, dag)

    def test_dag_cycle_refused_by_name(self):
        order = ["a", "b"]
        hist = {
            "a": {"runs": 10, "fails": 0, "mean_seconds": 1.0, "seconds": [1.0] * 10},
            "b": {"runs": 10, "fails": 0, "mean_seconds": 1.0, "seconds": [1.0] * 10},
        }
        dag = {"edges": [["a", "b"], ["b", "a"]]}
        with self.assertRaises(ValueError) as ctx:
            gate_order.constrained_order(order, hist, dag)
        self.assertIn("cycle", str(ctx.exception).lower())

    def test_read_pinned_order_invalid_inputs_raise_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            bin_path = os.path.join(d, "bin.json")
            with open(bin_path, "wb") as f:
                f.write(b"\xff\xfe")
            with self.assertRaises(gate_order.CorruptLogError):
                gate_order.read_pinned_order(bin_path)

            empty_path = os.path.join(d, "empty.json")
            with open(empty_path, "w", encoding="utf-8") as f:
                pass
            with self.assertRaises(gate_order.CorruptLogError):
                gate_order.read_pinned_order(empty_path)

            torn_path = os.path.join(d, "torn.json")
            with open(torn_path, "w", encoding="utf-8") as f:
                f.write('{"schema_version": "c0.orderpin.1"')
            with self.assertRaises(gate_order.CorruptLogError):
                gate_order.read_pinned_order(torn_path)

            list_path = os.path.join(d, "list.json")
            with open(list_path, "w", encoding="utf-8") as f:
                f.write('[]')
            with self.assertRaises(gate_order.CorruptLogError):
                gate_order.read_pinned_order(list_path)

            missing = os.path.join(d, "missing.json")
            with self.assertRaises(gate_order.CorruptLogError):
                gate_order.read_pinned_order(missing)

            with self.assertRaises(gate_order.CorruptLogError):
                gate_order.read_pinned_order(d)


class TestLedger(unittest.TestCase):
    def _record(self, **kw):
        base = {
            "schema": 1,
            "cut_id": "a" * 32,
            "cut_version": "1.0.0",
            "gate_name": "g",
            "gate_argv": ["python3", "x.py"],
            "status": "PASS",
            "exit_code": 0,
            "started_unix": 1.0,
            "ended_unix": 2.0,
            "duration_s": 1.0,
            "worktree_key": "w",
            "tree_sha": "UNKNOWN",
            "inputs_hash": None,
            "code_hash": "b" * 64,
            "tool_versions": {},
            "verdict_source": "full",
            "cache_key": None,
            "deadline_unix": None,
        }
        base.update(kw)
        return base

    def test_corrupt_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"schema":1,"cut_id":"' + "a" * 32 + '","gate_name":"g","status":"PASS"}\n')
            self.assertFalse(gate_ledger.verify_chain(path))

    def test_concurrent_appenders_keep_one_chain(self):
        """ACC2, 2026-09-26: the fast gate now runs checks side by side and each
        appends here. append_ledger reads the last line, chains to it and
        appends, so two writers at once could both chain to the same line."""
        import subprocess
        child = ("import json, sys; sys.path.insert(0, sys.argv[1]); import gate_ledger\n"
                 "rec = json.loads(sys.argv[3])\n"
                 "for i in range(25):\n"
                 "    r = dict(rec); r['gate_name'] = 'g%s-%d' % (sys.argv[4], i)\n"
                 "    gate_ledger.append_ledger(sys.argv[2], r)\n")
        here = os.path.dirname(os.path.abspath(__file__))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            procs = [subprocess.Popen([sys.executable, "-c", child, here, path,
                                       json.dumps(self._record()), str(n)])
                     for n in range(8)]
            self.assertEqual([p.wait(timeout=120) for p in procs], [0] * 8)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(len(fh.readlines()), 200)
            self.assertTrue(gate_ledger.verify_chain(path),
                            "concurrent appenders forked the hash chain")

    def test_append_and_verify(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            gate_ledger.append_ledger(path, self._record())
            gate_ledger.append_ledger(path, self._record(gate_name="h"))
            self.assertTrue(gate_ledger.verify_chain(path))

    def test_append_refuses_missing_gate_name(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            rec = self._record()
            del rec["gate_name"]
            with self.assertRaises(ValueError):
                gate_ledger.append_ledger(path, rec)

    def test_append_refuses_bad_prev_hash(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            gate_ledger.append_ledger(path, self._record())
            rec = self._record(gate_name="h")
            rec["prev_hash"] = "0" * 64
            with self.assertRaises(ValueError):
                gate_ledger.append_ledger(path, rec)

    def test_tampered_chain_fails_verify(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            gate_ledger.append_ledger(path, self._record())
            gate_ledger.append_ledger(path, self._record(gate_name="h"))
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            rec = json.loads(lines[0])
            rec["gate_name"] = "tampered"
            lines[0] = json.dumps(rec, sort_keys=True, separators=(',', ':')) + "\n"
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            self.assertFalse(gate_ledger.verify_chain(path))

    def test_verify_chain_non_str_path_refuses(self):
        for bad in (None, 123, b"x"):
            with self.assertRaises(ValueError):
                gate_ledger.verify_chain(bad)

    def test_verify_chain_missing_file_is_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(gate_ledger.verify_chain(os.path.join(d, "missing.jsonl")))

    def test_hostile_inputs_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            bads = [None, "x", [], {"schema": True, "gate_name": "g", "status": "PASS"}]
            for bad in bads:
                with self.assertRaises((ValueError, TypeError)):
                    gate_ledger.append_ledger(path, bad)
            for key, val in [
                ("schema", True),
                ("exit_code", True),
                ("duration_s", float("nan")),
                ("gate_argv", "x"),
                ("tool_versions", set()),
            ]:
                rec = self._record()
                rec[key] = val
                with self.assertRaises(ValueError):
                    gate_ledger.append_ledger(path, rec)


class TestLedgerPathIsOneGuard(unittest.TestCase):
    """Found by the landing fuzz behind 85 clean probes: append_ledger crashed raw on a path that is not text,
    while verify_chain refused it. One guard serves both, so they can never disagree again."""

    def test_every_entry_point_refuses_a_path_that_is_not_text(self):
        import gate_ledger
        for hostile in ({}, {"a": 1}, None, 7, True, b"ledger.jsonl", ["x"], ""):
            for name, call in (("append_ledger", lambda h=hostile: gate_ledger.append_ledger(h, {})),
                               ("verify_chain", lambda h=hostile: gate_ledger.verify_chain(h))):
                with self.subTest(entry=name, path=repr(hostile)):
                    with self.assertRaises(ValueError) as caught:
                        call()
                    self.assertIn("path", str(caught.exception))



class AnUnreadableLedgerRefuses(unittest.TestCase):
    """2026-09-30: an unreadable ledger used to read as an empty chain and fork it at GENESIS."""

    def test_append_to_an_unreadable_ledger_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.jsonl")
            gate_ledger.append_ledger(path, TestLedger._record(TestLedger()))
            with open(path, "rb") as f:
                before = f.read()
            os.chmod(path, 0o200)  # write only: append can open it, the chain read cannot
            try:
                with self.assertRaises(OSError):
                    gate_ledger.append_ledger(path, TestLedger._record(TestLedger(), gate_name="h"))
            finally:
                os.chmod(path, 0o600)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), before)

if __name__ == "__main__":
    unittest.main()
