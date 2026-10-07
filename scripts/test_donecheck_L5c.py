#!/usr/bin/env python3
"""Regression tests for scripts/donecheck_L5c.py (FX-15.4)."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import donecheck_L5c  # noqa: E402

REPO = os.path.dirname(HERE)
REAL_MODULE = os.path.join(HERE, "l5c_audit.py")


GREEN_VERIFY = """

def verify_evidence(doc_path, ledger_path, root):
    return []
"""

PROBLEM_VERIFY = """

def verify_evidence(doc_path, ledger_path, root):
    return ["HASH: fixture drift recorded for the test"]
"""

DELETE_VERIFY = """

del verify_evidence
"""


def _sample_ids():
    return ["VAULT-1", "VAULT-2", "VAULT-3", "VAULT-4",
            "DISPATCH-1", "DISPATCH-2", "DISPATCH-3",
            "HOOK-1", "HOOK-2", "HOOK-3"]


def _per_sample():
    return [
        {"id": "VAULT-1", "category": "vault", "status": "KILLED",
         "attributed": True, "state_backed": True},
        {"id": "VAULT-2", "category": "vault", "status": "KILLED",
         "attributed": True, "state_backed": True},
        {"id": "VAULT-3", "category": "vault", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "VAULT-4", "category": "vault", "status": "SURVIVED",
         "attributed": False, "state_backed": False},
        {"id": "DISPATCH-1", "category": "dispatch", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "DISPATCH-2", "category": "dispatch", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "DISPATCH-3", "category": "dispatch", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "HOOK-1", "category": "hook", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "HOOK-2", "category": "hook", "status": "KILLED",
         "attributed": True, "state_backed": False},
        {"id": "HOOK-3", "category": "hook", "status": "KILLED",
         "attributed": True, "state_backed": False},
    ]


def _meta():
    return [{"id": "META-%d" % i, "status": "KILLED", "attributed": True}
            for i in range(1, 10)]


def _ledger(per_sample=None, meta=None, stored_gate=True, status="RUN"):
    return {
        "status": status,
        "meta": _meta() if meta is None else meta,
        "per_sample": _per_sample() if per_sample is None else per_sample,
        "gate": {"gate": stored_gate, "conditions": {}},
    }


def _build(tmp, module_append=GREEN_VERIFY, ledger=None, ledger_present=True,
           samples=None):
    scripts = os.path.join(tmp, "scripts")
    os.makedirs(scripts, exist_ok=True)
    if os.path.isfile(REAL_MODULE):
        with open(REAL_MODULE, "rb") as handle:
            data = handle.read()
    else:
        data = b""
    with open(os.path.join(scripts, "l5c_audit.py"), "wb") as handle:
        handle.write(data + module_append.encode("utf-8"))
    l5c_dir = os.path.join(tmp, "docs", "plan", "l5c")
    os.makedirs(l5c_dir, exist_ok=True)
    if samples is None:
        samples = [{"id": sid} for sid in _sample_ids()]
    with open(os.path.join(l5c_dir, "samples.json"), "wb") as handle:
        handle.write(json.dumps(samples).encode("utf-8"))
    if ledger_present:
        if ledger is None:
            ledger = _ledger()
        with open(os.path.join(l5c_dir, "run-ledger.json"), "wb") as handle:
            handle.write(json.dumps(ledger).encode("utf-8"))
    arch = os.path.join(tmp, "docs", "architecture")
    os.makedirs(arch, exist_ok=True)
    with open(os.path.join(arch, "L5C-TEST-INTEGRITY-AUDIT.md"), "wb") as handle:
        handle.write(b"# fixture\n")


def _run_main(root):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = donecheck_L5c.main(["--root", root])
    text = buf.getvalue()
    lines = text.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    return code, (lines[-1] if lines else "")


def _survived(row):
    out = dict(row)
    out["status"] = "SURVIVED"
    out["attributed"] = False
    out["state_backed"] = False
    return out


def _snapshot(root):
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            st = os.stat(full)
            out[os.path.relpath(full, root)] = (st.st_size, st.st_mtime_ns)
    return out


class ExitCodeTests(unittest.TestCase):
    def test_three_exit_codes_through_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            _build(tmp)
            code, last = _run_main(tmp)
            self.assertEqual(code, 0)
            self.assertTrue(last.startswith("PASS:"), last)
        with tempfile.TemporaryDirectory() as tmp:
            rows = _per_sample()
            rows[7] = _survived(rows[7])
            _build(tmp, ledger=_ledger(per_sample=rows, stored_gate=False))
            code, last = _run_main(tmp)
            self.assertEqual(code, 1)
            self.assertTrue(last.startswith("FAIL:"), last)
        with tempfile.TemporaryDirectory() as tmp:
            _build(tmp, ledger={"status": "NO-DATA", "rows": []})
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertTrue(last.startswith("NO-DATA:"), last)

    def test_selftest_passes(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = donecheck_L5c.main(["--selftest"])
        text = buf.getvalue().rstrip("\n")
        lines = text.split("\n")
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], "selftest: 7 cases, OK")

    def test_main_refuses_a_wrong_argv_type(self):
        bad = [0, True, -1, float("nan"), "", "x", b"x", {}, {"a": 1},
               {1, 2}, object(), ["--root", 1]]
        for value in bad:
            with self.assertRaises(ValueError):
                donecheck_L5c.main(value)


class MetaTests(unittest.TestCase):
    def test_one_meta_row_of_nine_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            _build(tmp, ledger=_ledger(meta=_meta()[:8]))
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertTrue(last.startswith("NO-DATA:"), last)

    def test_unattributed_meta_kill_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = list(_meta())
            meta[3] = dict(meta[3], attributed=False)
            _build(tmp, ledger=_ledger(meta=meta))
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertTrue(last.startswith("NO-DATA:"), last)

    def test_duplicate_meta_id_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = list(_meta())
            meta[1] = dict(meta[1], id=meta[0]["id"])
            _build(tmp, ledger=_ledger(meta=meta))
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertTrue(last.startswith("NO-DATA:"), last)

    def test_meta_timeout_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = list(_meta())
            meta[2] = dict(meta[2], status="TIMEOUT")
            _build(tmp, ledger=_ledger(meta=meta))
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertTrue(last.startswith("NO-DATA:"), last)

    def test_meta_survivor_is_nodata_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            meta = list(_meta())
            meta[5] = dict(meta[5], status="SURVIVED")
            _build(tmp, ledger=_ledger(meta=meta))
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertFalse(last.startswith("PASS:"))


class SampleTests(unittest.TestCase):
    def test_sample_ids_differ_from_registered_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = _per_sample()
            rows[5] = dict(rows[5], id="HOOK-99")
            _build(tmp, ledger=_ledger(per_sample=rows))
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertTrue(last.startswith("NO-DATA:"), last)

    def test_stored_gate_true_recomputed_false_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = _per_sample()
            rows[7] = _survived(rows[7])
            _build(tmp, ledger=_ledger(per_sample=rows, stored_gate=True))
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertTrue(last.startswith("NO-DATA:"), last)


class EvidenceTests(unittest.TestCase):
    def test_hash_drift_is_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            _build(tmp, module_append=PROBLEM_VERIFY)
            code, last = _run_main(tmp)
            self.assertEqual(code, 1)
            self.assertTrue(last.startswith("FAIL:"), last)

    def test_missing_verify_evidence_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            _build(tmp, module_append=DELETE_VERIFY)
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertTrue(last.startswith("NO-DATA:"), last)


class LedgerCorruptionTests(unittest.TestCase):
    def test_empty_ledger_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            _build(tmp, ledger={"status": "NO-DATA", "rows": []})
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertTrue(last.startswith("NO-DATA:"), last)

    def test_truncated_ledger_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            _build(tmp)
            path = os.path.join(tmp, "docs", "plan", "l5c", "run-ledger.json")
            with open(path, "wb") as handle:
                handle.write(b'{"meta": [')
            code, last = _run_main(tmp)
            self.assertEqual(code, 2)
            self.assertTrue(last.startswith("NO-DATA:"), last)


class ReadOnlyTests(unittest.TestCase):
    def test_main_writes_nothing_under_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            _build(tmp)
            before = _snapshot(tmp)
            code, last = _run_main(tmp)
            after = _snapshot(tmp)
            self.assertEqual(code, 0)
            self.assertEqual(before, after)


class RealTreeTests(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(os.path.join(REPO, "scripts", "l5c_audit.py")),
                         "requires scripts/l5c_audit.py")
    def test_real_tree_returns_a_verdict_line(self):
        code, last = _run_main(REPO)
        self.assertIn(code, (0, 1, 2))
        prefix = {0: "PASS:", 1: "FAIL:", 2: "NO-DATA:"}[code]
        self.assertTrue(last.startswith(prefix), "code=%s last=%r" % (code, last))


class RmtreeLinkTests(unittest.TestCase):
    def test_removes_a_link_to_a_directory_and_never_its_target(self):
        # os.walk lists a link to a directory under dirnames, and rmdir refuses a link: the copy stayed on disk
        with tempfile.TemporaryDirectory() as root:
            outside = os.path.join(root, "outside")
            os.makedirs(outside)
            open(os.path.join(outside, "keep.txt"), "w").close()
            d = os.path.join(root, "copy")
            os.makedirs(os.path.join(d, "skills"))
            os.symlink("../skills", os.path.join(d, "inner-link"))
            os.symlink(outside, os.path.join(d, "outer-link"))
            donecheck_L5c._rmtree(d)
            self.assertFalse(os.path.lexists(d))
            self.assertTrue(os.path.isfile(os.path.join(outside, "keep.txt")))


if __name__ == "__main__":
    unittest.main()
