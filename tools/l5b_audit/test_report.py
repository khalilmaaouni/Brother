"""Tests for L5b.6 report emitter, audit main and self-mutation proof."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(REPO, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tools.l5b_audit import report as rp
from tools.l5b_audit import scanner as scn
import l5b_audit as la


class HostileArgumentsTest(unittest.TestCase):
    def test_hostile_arguments_refused(self):
        for value in (0, 1.5, True, b"x", {}, {1, 2}, object(), "x"):
            with self.assertRaises(ValueError):
                la.main(value)
        for value in (None, 0, True, -1, float("nan"), "", b"x", [], {}, (), {1, 2}, object()):
            with self.assertRaises(ValueError):
                la.inventory_skips(value)
        with self.assertRaises(ValueError):
            la.inventory_skips("/no/such/dir/xyz-abc")
        for value in (None, 0, True, [], {}, "x", object()):
            with self.assertRaises(ValueError):
                rp.emit(value, "/tmp/a", "/tmp/b", "/tmp/c")
        for bad in (None, [], "x", 0, True, object()):
            with self.assertRaises(ValueError):
                rp.assemble(bad, (), (), (), (), rp.SkipInventory(0, 0, ()))


class EmitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="l5b6-emit-")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sample_report(self):
        return rp.AuditReport(
            scheme_version="l5b-v1",
            boundary_calls_total=10,
            boundary_calls_stated=10,
            boundary_calls_tested=10,
            hits_unexempted=0,
            fail_closed={"empty": True, "corrupt": True, "unknown": True},
            blocked=False,
            reason="COMPLETE_PASS",
            exemptions=(),
            exemptions_applied=0,
            annotation_clearances=(),
            fixes_applied=(),
            proposed_patches=(),
            skips=rp.SkipInventory(5, 0, ()),
            fires=(),
            hits=(),
            score_decimal=9.0,
            report_hash="",
            produced_at="2026-01-01T00:00:00+00:00",
        )

    def _paths(self):
        return (
            os.path.join(self.tmp, "doc.md"),
            os.path.join(self.tmp, "audit.json"),
            os.path.join(self.tmp, "audit.patch"),
        )

    def test_T_JSON_DECODES(self):
        doc, js, patch = self._paths()
        digest = rp.emit(self._sample_report(), doc, js, patch)
        with open(js, "rb") as handle:
            raw = handle.read()
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data["scheme_version"], "l5b-v1")
        self.assertEqual(data["report_hash"], digest)

    def test_T_DOC_HASH(self):
        doc, js, patch = self._paths()
        digest = rp.emit(self._sample_report(), doc, js, patch)
        with open(doc, "r", encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("report_hash: %s" % digest, text)

    def test_T_PARTIAL_NO_WRITE(self):
        doc, js, patch = self._paths()
        before = sorted(os.listdir(self.tmp))
        bad = os.path.join(self.tmp, "missing", "audit.json")
        with self.assertRaises(ValueError):
            rp.emit(self._sample_report(), doc, bad, patch)
        self.assertEqual(sorted(os.listdir(self.tmp)), before)

    def test_T_IDEMPOTENT_RERUN(self):
        doc, js, patch = self._paths()
        first = rp.emit(self._sample_report(), doc, js, patch)
        second = rp.emit(self._sample_report(), doc, js, patch)
        self.assertEqual(first, second)

    def test_T_SUBPROCESS_RECALL(self):
        src = "import subprocess\ndef f():\n    subprocess.run(['ls'])\n"
        calls = scn.scan_source("x.py", src)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kind, "SUBPROCESS")
        self.assertEqual(calls[0].symbol, "subprocess.run")

    def test_T_SKIPPED_FILE_DETECTED(self):
        bad = os.path.join(self.tmp, "broken.py")
        with open(bad, "wb") as handle:
            handle.write(b"\xff\xfe\x00")
        inv = la.inventory_skips(self.tmp)
        self.assertGreaterEqual(inv.files_skipped, 1)
        self.assertTrue(inv.skip_reasons)


class ThresholdsTest(unittest.TestCase):
    def test_T_MAIN_ALL_THRESHOLDS(self):
        rpt = rp.AuditReport(
            scheme_version="l5b-v1",
            boundary_calls_total=10,
            boundary_calls_stated=9,
            boundary_calls_tested=9,
            hits_unexempted=0,
            fail_closed={"empty": True, "corrupt": True, "unknown": True},
            blocked=False,
            reason="COMPLETE_PASS",
            exemptions=(),
            exemptions_applied=0,
            annotation_clearances=(),
            fixes_applied=(),
            proposed_patches=(),
            skips=rp.SkipInventory(5, 0, ()),
            fires=(),
            hits=(),
            score_decimal=9.0,
            report_hash="",
            produced_at="2026-01-01T00:00:00+00:00",
        )
        failed = la._failed_thresholds(rpt)
        self.assertTrue(any("stated" in item for item in failed))


class SelfMutationTest(unittest.TestCase):
    def test_T_SELF_MUT_EMPTY(self):
        ok, ids = rp.self_mutation_proof()
        self.assertIn("M-L5B-SCORE-EMPTY-01", ids)

    def test_T_SELF_MUT_FAIL_OPEN(self):
        ok, ids = rp.self_mutation_proof()
        self.assertIn("M-L5B-FAIL-OPEN-01", ids)

    def test_T_SELF_MUT_EXCEPT_PASS(self):
        ok, ids = rp.self_mutation_proof()
        self.assertIn("M-L5B-EXCEPT-PASS-01", ids)

    def test_all_three_fire_in_order(self):
        ok, ids = rp.self_mutation_proof()
        self.assertTrue(ok)
        self.assertEqual(ids, (
            "M-L5B-SCORE-EMPTY-01",
            "M-L5B-FAIL-OPEN-01",
            "M-L5B-EXCEPT-PASS-01",
        ))


class RecheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="l5b6-recheck-")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_report(self, js):
        import hashlib as _hashlib
        body = {"scheme_version": "l5b-v1", "boundary_calls_total": 1}
        payload = json.dumps(body, ensure_ascii=True, sort_keys=True)
        digest = _hashlib.sha256(payload.encode("utf-8")).hexdigest()
        body["report_hash"] = digest
        body["produced_at"] = "2026-01-01T00:00:00+00:00"
        with open(js, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(body, ensure_ascii=True, sort_keys=True))
        return digest

    def test_recheck_zero_on_match(self):
        # L5b.8: a record that names no root, fires file and fire map cannot be re-derived
        # from the tree, so a matching hash alone is NO-DATA (2), never a pass; the 0 of a
        # real audit is proven by test_audit_recheck.test_a_recheck_of_a_real_audit_passes
        js = os.path.join(self.tmp, "audit.json")
        self._write_report(js)
        self.assertEqual(la.main(["--recheck", js]), 2)

    def test_recheck_one_on_mismatch(self):
        js = os.path.join(self.tmp, "audit.json")
        self._write_report(js)
        with open(js, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        data["boundary_calls_total"] = 99
        with open(js, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=True, sort_keys=True))
        self.assertEqual(la.main(["--recheck", js]), 1)

    def test_recheck_two_on_missing(self):
        js = os.path.join(self.tmp, "missing.json")
        self.assertEqual(la.main(["--recheck", js]), 2)

    def test_recheck_two_on_bad_json(self):
        js = os.path.join(self.tmp, "bad.json")
        with open(js, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(la.main(["--recheck", js]), 2)

    def test_no_args_returns_two(self):
        self.assertEqual(la.main([]), 2)


class ConcurrentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="l5b6-lock-")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_T_CONCURRENT_RUN(self):
        import fcntl
        out = os.path.join(self.tmp, "audit.json")
        lock_path = os.path.join(self.tmp, la.LOCK_NAME)
        handle = open(lock_path, "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            code = la.main([
                "--root", self.tmp,
                "--out", out,
                "--doc", os.path.join(self.tmp, "doc.md"),
                "--patch", os.path.join(self.tmp, "audit.patch"),
                "--fires", os.path.join(self.tmp, "fires.json"),
            ])
            self.assertEqual(code, 1)
            self.assertFalse(os.path.exists(out))
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


if __name__ == "__main__":
    unittest.main()
