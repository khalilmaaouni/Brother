import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import claim_store
import integrate


def _valid_evidence(n):
    return {"command": "check %d" % n,
            "exit_code": 0,
            "output_digest": "%064x" % n,
            "revision": "rev%d" % n}


def _valid_link(n):
    return {"state": "done", "evidence": _valid_evidence(n)}


def _make_chain(store_path, unit_id="U1", count=3):
    claims = []
    parent = None
    for i in range(count):
        claim, problem = claim_store.acquire(
            store_path, unit_id, "owner", parent_attempt_id=parent)
        if problem:
            raise AssertionError(problem)
        claims.append(claim)
        parent = claim["attempt_id"]
        _released, problem = claim_store.release(
            store_path, unit_id, "owner", state="done",
            attempt=claim["attempt"], evidence=_valid_evidence(i + 1))
        if problem:
            raise AssertionError(problem)
    return claims


class TestD17AttemptChain(unittest.TestCase):

    def test_attempt_chain_returns_full_chain_head_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "claims.json")
            claims = _make_chain(store, "U1")
            chain, problem = integrate.attempt_chain(store, "U1")
            self.assertEqual(problem, "")
            self.assertIsInstance(chain, list)
            self.assertEqual(len(chain), 3)
            self.assertEqual(chain[0]["attempt_id"], claims[2]["attempt_id"])
            self.assertEqual(chain[1]["attempt_id"], claims[1]["attempt_id"])
            self.assertEqual(chain[2]["attempt_id"], claims[0]["attempt_id"])

    def test_broken_chain_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "claims.json")
            _make_chain(store, "U1")
            with open(store, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            data["attempts"]["U1"] = [data["attempts"]["U1"][0],
                                      data["attempts"]["U1"][2]]
            with open(store, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            chain, problem = integrate.attempt_chain(store, "U1")
            self.assertIsNone(chain)
            self.assertNotEqual(problem, "")

    def test_cycle_in_chain_is_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "claims.json")
            _make_chain(store, "U1")
            with open(store, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            rows = data["attempts"]["U1"]
            rows[0]["parent_attempt_id"] = rows[2]["attempt_id"]
            with open(store, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            chain, problem = integrate.attempt_chain(store, "U1")
            self.assertIsNone(chain)
            self.assertNotEqual(problem, "")

    def test_attempt_chain_hostile_input_is_problem_never_crash(self):
        for bad in (None, 42, [], {}, 0.0):
            chain, problem = integrate.attempt_chain(bad, "U1")
            self.assertIsNone(chain)
            self.assertNotEqual(problem, "")
        for bad in (None, 42, [], {}, 0.0):
            chain, problem = integrate.attempt_chain("store.json", bad)
            self.assertIsNone(chain)
            self.assertNotEqual(problem, "")


class TestD17AssertIntegrable(unittest.TestCase):

    def test_assert_integrable_chain_returns_empty_when_all_links_done(self):
        self.assertEqual(
            integrate.assert_integrable_chain([_valid_link(1), _valid_link(2)]),
            "")

    def test_assert_integrable_chain_raises_for_non_list(self):
        for bad in ({}, {"a": 1}, 42, None, 0.0, "not-a-list", (1, 2)):
            with self.assertRaises(ValueError) as cm:
                integrate.assert_integrable_chain(bad)
            self.assertIn("list", str(cm.exception))

    def test_assert_integrable_chain_raises_for_generator(self):
        def gen():
            yield _valid_link(1)
        with self.assertRaises(ValueError) as cm:
            integrate.assert_integrable_chain(gen())
        self.assertIn("list", str(cm.exception))

    def test_assert_integrable_chain_raises_for_empty_list(self):
        with self.assertRaises(ValueError) as cm:
            integrate.assert_integrable_chain([])
        self.assertIn("empty", str(cm.exception))

    def test_assert_integrable_chain_raises_for_non_mapping_link(self):
        for bad in (None, 42, "link", [], True):
            with self.assertRaises(ValueError) as cm:
                integrate.assert_integrable_chain([bad])
            self.assertIn("mapping", str(cm.exception))

    def test_assert_integrable_chain_raises_for_empty_link_dict(self):
        with self.assertRaises(ValueError) as cm:
            integrate.assert_integrable_chain([{}])
        self.assertIn("state", str(cm.exception))

    def test_assert_integrable_chain_raises_for_state_not_done(self):
        for bad_state in ("claimed", "released", "running", None, 42, True, []):
            link = {"state": bad_state, "evidence": _valid_evidence(1)}
            with self.assertRaises(ValueError) as cm:
                integrate.assert_integrable_chain([link])
            self.assertIn("state", str(cm.exception))

    def test_assert_integrable_chain_raises_for_missing_evidence(self):
        for bad in (None, "evidence", 42, [], True):
            link = {"state": "done", "evidence": bad}
            with self.assertRaises(ValueError) as cm:
                integrate.assert_integrable_chain([link])
            self.assertIn("evidence", str(cm.exception))

    def test_assert_integrable_chain_raises_for_command_empty_or_missing(self):
        for bad in ("", None, 42, [], True):
            link = {"state": "done", "evidence": dict(_valid_evidence(1))}
            link["evidence"]["command"] = bad
            with self.assertRaises(ValueError) as cm:
                integrate.assert_integrable_chain([link])
            self.assertIn("command", str(cm.exception))
        link = {"state": "done", "evidence": dict(_valid_evidence(1))}
        del link["evidence"]["command"]
        with self.assertRaises(ValueError) as cm:
            integrate.assert_integrable_chain([link])
        self.assertIn("command", str(cm.exception))

    def test_assert_integrable_chain_raises_for_bad_exit_code(self):
        for bad in (True, False, 1.5, float("inf"), float("nan"), "0",
                    None, []):
            link = {"state": "done", "evidence": dict(_valid_evidence(1))}
            link["evidence"]["exit_code"] = bad
            with self.assertRaises(ValueError) as cm:
                integrate.assert_integrable_chain([link])
            self.assertIn("exit_code", str(cm.exception))

    def test_assert_integrable_chain_raises_for_bad_output_digest(self):
        for bad in (None, "", "zz", "not-hex", "  ", 42, [], True):
            link = {"state": "done", "evidence": dict(_valid_evidence(1))}
            link["evidence"]["output_digest"] = bad
            with self.assertRaises(ValueError) as cm:
                integrate.assert_integrable_chain([link])
            self.assertIn("output_digest", str(cm.exception))

    def test_assert_integrable_chain_raises_for_bad_revision(self):
        for bad in (None, "", 42, [], True):
            link = {"state": "done", "evidence": dict(_valid_evidence(1))}
            link["evidence"]["revision"] = bad
            with self.assertRaises(ValueError) as cm:
                integrate.assert_integrable_chain([link])
            self.assertIn("revision", str(cm.exception))


class TestD17ChainIntegrate(unittest.TestCase):

    def test_chain_integrate_calls_real_integrate_one_and_wraps_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "claims.json")
            claims = _make_chain(store, "U1")
            called = []

            def fake_integrate_one(repo, lane_branch, unit, runner=None,
                                   check_runner=None, run_id=None,
                                   harness_revision=None):
                called.append((repo, lane_branch, unit, runner, check_runner,
                               run_id, harness_revision))
                return {"verdict": integrate.INTEGRATED,
                        "canonical": "cafebabe12345678",
                        "reason": "merged",
                        "evidence": {"check_command": "check",
                                     "exit_code": 0,
                                     "output": "all good",
                                     "canonical_rev": "cafebabe12345678"}}

            original = integrate.integrate_one
            integrate.integrate_one = fake_integrate_one
            try:
                result = integrate.chain_integrate_one(
                    "repo", "lane/U1", {"id": "U1"}, store)
            finally:
                integrate.integrate_one = original
            self.assertEqual(len(called), 1)
            self.assertEqual(result["verdict"], "DONE")
            self.assertEqual(
                result["attempt_ids"],
                [claims[2]["attempt_id"], claims[1]["attempt_id"],
                 claims[0]["attempt_id"]])
            self.assertEqual(result["canonical_revision"],
                             "cafebabe12345678")
            self.assertEqual(result["revalidation"]["check"], "check")
            self.assertEqual(result["revalidation"]["exit_code"], 0)
            self.assertIsInstance(result["revalidation"]["output_digest"], str)
            self.assertEqual(len(result["revalidation"]["output_digest"]), 64)
            self.assertEqual(result["revalidation"]["revision"],
                             "cafebabe12345678")

    def test_chain_integrate_no_data_on_non_integrated_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "claims.json")
            _make_chain(store, "U1")

            def fake_integrate_one(*args, **kwargs):
                return {"verdict": integrate.CONFLICT, "canonical": None,
                        "reason": "conflict"}

            original = integrate.integrate_one
            integrate.integrate_one = fake_integrate_one
            try:
                result = integrate.chain_integrate_one(
                    "repo", "lane/U1", {"id": "U1"}, store)
            finally:
                integrate.integrate_one = original
            self.assertEqual(result["verdict"], "NO-DATA")
            self.assertEqual(result["reason"], "conflict")

    def test_chain_integrate_refuses_incomplete_chain_without_integrating(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "claims.json")
            _make_chain(store, "U1")
            with open(store, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            del data["attempts"]["U1"][0]["evidence"]["output_digest"]
            with open(store, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            called = []

            def fake_integrate_one(*args, **kwargs):
                called.append(1)
                return {"verdict": integrate.INTEGRATED, "canonical": "rev"}

            original = integrate.integrate_one
            integrate.integrate_one = fake_integrate_one
            try:
                result = integrate.chain_integrate_one(
                    "repo", "lane/U1", {"id": "U1"}, store)
            finally:
                integrate.integrate_one = original
            self.assertEqual(result["verdict"], "NO-DATA")
            self.assertIn("output_digest", result["reason"])
            self.assertEqual(called, [])

    def test_chain_integrate_returns_no_data_when_integrate_one_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "claims.json")
            _make_chain(store, "U1")

            def fake_integrate_one(*args, **kwargs):
                raise TimeoutError("the lock was held")

            original = integrate.integrate_one
            integrate.integrate_one = fake_integrate_one
            try:
                result = integrate.chain_integrate_one(
                    "repo", "lane/U1", {"id": "U1"}, store)
            finally:
                integrate.integrate_one = original
            self.assertEqual(result["verdict"], "NO-DATA")
            self.assertNotEqual(result["reason"], "")

    def test_chain_integrate_one_hostile_input_refused(self):
        for bad in (None, 42, "not-a-dict", [], True):
            result = integrate.chain_integrate_one(
                "repo", "lane", bad, "store.json")
            self.assertEqual(result["verdict"], "NO-DATA")
            self.assertNotEqual(result["reason"], "")
        result = integrate.chain_integrate_one(
            "repo", "lane", {"id": "U1"}, None)
        self.assertEqual(result["verdict"], "NO-DATA")
        self.assertNotEqual(result["reason"], "")


if __name__ == "__main__":
    unittest.main()
