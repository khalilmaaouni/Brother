import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import propose_mechanisms as pm


MECHANISMS = pm.MECHANISM_IDS


def _citation():
    return {
        "paper_ref": "SoL-Pi Table 2",
        "source_path": "/tmp/solpi-paper.pdf",
        "source_sha256": "a" * 64,
        "source_quote": "action fusion cuts repeated tool calls",
        "retrieval_date": "2026-09-20",
        "source_version": "v1",
    }


def _baseline():
    return dict((m, dict(_citation())) for m in MECHANISMS)


def _current(changed_file=None):
    out = []
    for m in MECHANISMS:
        out.append({
            "mechanism_id": m,
            "current_behavior": "Brother keeps every tool observation in context",
            "changed_file": changed_file if changed_file is not None else ("scripts/harness/" + m + ".py"),
            "changed_function": "run_check",
            "before_run_id": "before-" + m,
            "after_run_id": "after-" + m,
            "risk": "low",
            "rollback": "revert the change",
            "preservation_proof": "every RESULT_PREFIX_RE line kept byte for byte",
        })
    return out


def _measurements(before_total=1000, after_total=800):
    out = {}
    for m in MECHANISMS:
        common = {
            "task": "task-A",
            "tree_hash": "tree-A",
            "counter_name": "brother-session-tokens",
            "counter_version": "1.0",
            "log": "/tmp/" + m + ".jsonl",
        }
        out["before-" + m] = dict(common, total_tokens=before_total)
        out["after-" + m] = dict(common, total_tokens=after_total)
    return out


class TestProposeMechanisms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_proposals = pm.PROPOSALS_PATH
        self._orig_lock = pm.LOCK_PATH
        pm.PROPOSALS_PATH = os.path.join(self.tmp, "proposals.json")
        pm.LOCK_PATH = os.path.join(self.tmp, "proposals.json.lock")

    def tearDown(self):
        pm.PROPOSALS_PATH = self._orig_proposals
        pm.LOCK_PATH = self._orig_lock
        try:
            os.unlink(pm.PROPOSALS_PATH)
        except OSError:
            pass
        try:
            os.unlink(pm.LOCK_PATH)
        except OSError:
            pass
        try:
            os.rmdir(self.tmp)
        except OSError:
            pass

    def test_returns_four_one_per_mechanism(self):
        proposals = pm.propose_mechanisms(_baseline(), _current(), _measurements())
        self.assertEqual(len(proposals), 4)
        self.assertEqual(set(p.mechanism_id for p in proposals), set(MECHANISMS))
        for p in proposals:
            self.assertIsInstance(p, pm.MechanismProposal)

    def test_writes_proposals_json(self):
        pm.propose_mechanisms(_baseline(), _current(), _measurements())
        with open(pm.PROPOSALS_PATH, "r", encoding="utf-8") as h:
            data = json.load(h)
        self.assertEqual(len(data), 4)
        self.assertEqual(set(row["mechanism_id"] for row in data), set(MECHANISMS))

    def test_applies_true_has_before_and_after_in_measurements(self):
        measurements = _measurements()
        proposals = pm.propose_mechanisms(_baseline(), _current(), measurements)
        any_applies = False
        for p in proposals:
            if p.applies:
                any_applies = True
                self.assertIn(p.before_run_id, measurements)
                self.assertIn(p.after_run_id, measurements)
                self.assertTrue(p.current_behavior_change)
                self.assertTrue(p.risk)
                self.assertTrue(p.rollback)
                self.assertTrue(p.preservation_proof)
                self.assertIsInstance(p.after_run_id, str)
                self.assertIsInstance(p.before_run_id, str)
        self.assertTrue(any_applies)

    def test_delta_tokens_and_percent_match_definition(self):
        proposals = pm.propose_mechanisms(_baseline(), _current(), _measurements(1000, 800))
        for p in proposals:
            self.assertEqual(p.delta_tokens, -200)
            self.assertAlmostEqual(p.delta_percent, -20.0)

    def test_fractional_delta_percent_is_valid(self):
        proposals = pm.propose_mechanisms(_baseline(), _current(), _measurements(3, 2))
        for p in proposals:
            self.assertEqual(p.delta_tokens, -1)
            self.assertAlmostEqual(p.delta_percent, 100.0 * -1 / 3)

    def test_empty_baseline_blocks(self):
        proposals = pm.propose_mechanisms({}, _current(), _measurements())
        self.assertEqual(len(proposals), 4)
        for p in proposals:
            self.assertEqual(p.status, "BLOCKED")
            self.assertFalse(p.applies)

    def test_empty_current_blocks(self):
        proposals = pm.propose_mechanisms(_baseline(), [], _measurements())
        self.assertEqual(len(proposals), 4)
        for p in proposals:
            self.assertEqual(p.status, "BLOCKED")
            self.assertFalse(p.applies)

    def test_stale_before_run_id_blocks_that_mechanism(self):
        current = _current()
        for r in current:
            if r["mechanism_id"] == "action_fusion":
                r["before_run_id"] = "no-such-run"
        proposals = pm.propose_mechanisms(_baseline(), current, _measurements())
        af = [p for p in proposals if p.mechanism_id == "action_fusion"][0]
        self.assertEqual(af.status, "BLOCKED")
        self.assertFalse(af.applies)

    def test_stale_after_run_id_blocks_that_mechanism(self):
        current = _current()
        for r in current:
            if r["mechanism_id"] == "action_fusion":
                r["after_run_id"] = "no-such-run"
        proposals = pm.propose_mechanisms(_baseline(), current, _measurements())
        af = [p for p in proposals if p.mechanism_id == "action_fusion"][0]
        self.assertEqual(af.status, "BLOCKED")
        self.assertFalse(af.applies)

    def test_gate_touch_is_blocked(self):
        proposals = pm.propose_mechanisms(
            _baseline(), _current(changed_file="scripts/required_fast.sh"), _measurements()
        )
        self.assertEqual(len(proposals), 4)
        for p in proposals:
            self.assertEqual(p.status, "BLOCKED")
            self.assertFalse(p.applies)

    def test_gate_order_touch_is_blocked(self):
        proposals = pm.propose_mechanisms(
            _baseline(), _current(changed_file="scripts/gate_order.py"), _measurements()
        )
        self.assertEqual(len(proposals), 4)
        for p in proposals:
            self.assertEqual(p.status, "BLOCKED")
            self.assertFalse(p.applies)

    def test_missing_citation_field_blocks_that_mechanism(self):
        baseline = _baseline()
        del baseline["observation_pack"]["source_sha256"]
        proposals = pm.propose_mechanisms(baseline, _current(), _measurements())
        op = [p for p in proposals if p.mechanism_id == "observation_pack"][0]
        self.assertEqual(op.status, "BLOCKED")
        self.assertFalse(op.applies)

    def test_task_mismatch_raises_no_data(self):
        m = _measurements()
        m["after-action_fusion"] = dict(m["after-action_fusion"], task="task-B")
        with self.assertRaises(pm.NoDataError):
            pm.propose_mechanisms(_baseline(), _current(), m)

    def test_counter_version_mismatch_raises_no_data(self):
        m = _measurements()
        m["after-action_fusion"] = dict(m["after-action_fusion"], counter_version="2.0")
        with self.assertRaises(pm.NoDataError):
            pm.propose_mechanisms(_baseline(), _current(), m)

    def test_bool_total_tokens_raises_value_error(self):
        m = _measurements()
        m["after-action_fusion"] = dict(m["after-action_fusion"], total_tokens=True)
        with self.assertRaises(ValueError):
            pm.propose_mechanisms(_baseline(), _current(), m)

    def test_nan_total_tokens_raises_value_error(self):
        m = _measurements()
        m["after-action_fusion"] = dict(m["after-action_fusion"], total_tokens=float("nan"))
        with self.assertRaises(ValueError):
            pm.propose_mechanisms(_baseline(), _current(), m)

    def test_missing_total_tokens_raises_no_data(self):
        m = _measurements()
        del m["after-action_fusion"]["total_tokens"]
        with self.assertRaises(pm.NoDataError):
            pm.propose_mechanisms(_baseline(), _current(), m)

    def test_concurrent_write_blocks(self):
        with open(pm.LOCK_PATH, "w", encoding="utf-8") as h:
            h.write("held")
        with self.assertRaises(pm.NoDataError):
            pm.propose_mechanisms(_baseline(), _current(), _measurements())

    def test_hostile_input_refused(self):
        b = _baseline()
        c = _current()
        m = _measurements()
        cases = (
            (None, c, m),
            ("", c, m),
            (True, c, m),
            (42, c, m),
            ([], c, m),
            (b, None, m),
            (b, "", m),
            (b, {"a": 1}, m),
            (b, [1, 2, 3], m),
            (b, c, None),
            (b, c, ""),
            (b, c, True),
            (b, c, []),
        )
        for baseline, current, measurements in cases:
            with self.assertRaises(ValueError):
                pm.propose_mechanisms(baseline, current, measurements)

    def test_read_measurements_corrupt_json_raises_no_data(self):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w", encoding="utf-8") as h:
            h.write("{not json")
        self.addCleanup(os.unlink, path)
        with self.assertRaises(pm.NoDataError):
            pm.read_measurements(path)


if __name__ == "__main__":
    unittest.main()
