"""Tests for scripts/donecheck_L5b.py (FX-15.3).

Every test builds its own fixture under a temporary directory and drives
main(["--root", tmp]). The real tools/l5b_audit/score.py is copied into the
fixture so the score is recomputed by the real scorer. No test imports
subprocess.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import donecheck_L5b  # noqa: E402

REAL_SCORE = os.path.join(REPO, "tools", "l5b_audit", "score.py")
REPORT_REL = os.path.join("docs", "architecture", "l5b_reliability_audit.json")
AUDIT_REL = os.path.join("scripts", "l5b_audit.py")


def _green_report():
    return {
        "scheme_version": "l5b-v1",
        "boundary_calls_total": 10,
        "boundary_calls_stated": 10,
        "boundary_calls_tested": 10,
        "hits_unexempted": 0,
        "fail_closed": {"empty": True, "corrupt": True, "unknown": True},
        "blocked": False,
        "reason": "COMPLETE_PASS",
        "exemptions": [],
        "exemptions_applied": 0,
        "annotation_clearances": [],
        "fixes_applied": [],
        "proposed_patches": [],
        "score_decimal": 10.0,
    }


def _write(root, rel, content, binary=False):
    path = os.path.join(root, rel)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    if binary:
        with open(path, "wb") as handle:
            handle.write(content)
    else:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
    return path


def _copy_score(root):
    with open(REAL_SCORE, "rb") as handle:
        raw = handle.read()
    _write(root, os.path.join("tools", "l5b_audit", "score.py"), raw, binary=True)


def _run(report=None, audit_body=None, write_audit=True, write_report=True,
         raw_report=None, timeout=None):
    with tempfile.TemporaryDirectory(prefix="fx153_l5b_") as tmp:
        if write_audit:
            body = audit_body if audit_body is not None else "import sys\nsys.exit(0)\n"
            _write(tmp, AUDIT_REL, body)
        _copy_score(tmp)
        if write_report:
            if raw_report is not None:
                _write(tmp, REPORT_REL, raw_report)
            else:
                payload = report if report is not None else _green_report()
                _write(tmp, REPORT_REL, json.dumps(payload))
        argv = ["--root", tmp]
        if timeout is not None:
            argv += ["--recheck-timeout", str(timeout)]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = donecheck_L5b.main(argv)
        return code, buffer.getvalue()


def _snapshot(root):
    out = {}
    for base, dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root)
            try:
                stat = os.stat(full)
            except OSError:
                out[rel] = None
            else:
                out[rel] = (stat.st_mtime_ns, stat.st_size)
        for name in dirs:
            rel = os.path.relpath(os.path.join(base, name), root)
            out[rel + os.sep] = None
    return out


class GreenTest(unittest.TestCase):
    def test_green_fixture_passes_through_main(self):
        code, out = _run()
        self.assertEqual(code, 0, out)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertTrue(lines)
        self.assertTrue(lines[-1].startswith("PASS:"), lines[-1])

    def test_zero_waivers_is_printed_and_allowed(self):
        code, out = _run()
        self.assertEqual(code, 0, out)
        self.assertIn("waivers: 0", out)


class HostileArgvTest(unittest.TestCase):
    """A hostile argv value is REFUSED with NO-DATA, never a raw TypeError.

    Both findings the red team fired (an int and a bytes value) are pinned
    here plus one list holding a non string.
    """

    def test_argv_int_is_refused(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = donecheck_L5b.main(7)
        out = buffer.getvalue()
        self.assertEqual(code, 2, out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("NO-DATA:"), out)

    def test_argv_bytes_is_refused(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = donecheck_L5b.main(b"--root /tmp")
        out = buffer.getvalue()
        self.assertEqual(code, 2, out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("NO-DATA:"), out)

    def test_argv_with_non_string_element_is_refused(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = donecheck_L5b.main(["--root", 7])
        out = buffer.getvalue()
        self.assertEqual(code, 2, out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("NO-DATA:"), out)

    def test_argv_dict_is_refused(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = donecheck_L5b.main({"--root": "/tmp"})
        out = buffer.getvalue()
        self.assertEqual(code, 2, out)


class NoDataTest(unittest.TestCase):
    def test_one_probe_of_three_is_nodata(self):
        report = _green_report()
        report["fail_closed"] = {"empty": True}
        code, out = _run(report=report)
        self.assertEqual(code, 2, out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("NO-DATA:"), out)

    def test_unknown_probe_name_is_nodata(self):
        report = _green_report()
        report["fail_closed"] = {"empty": True, "corrupt": True, "other": True}
        code, out = _run(report=report)
        self.assertEqual(code, 2, out)

    def test_unknown_scheme_is_nodata(self):
        report = _green_report()
        report["scheme_version"] = "l5b-v2"
        code, out = _run(report=report)
        self.assertEqual(code, 2, out)

    def test_truncated_report_is_nodata(self):
        code, out = _run(raw_report="{not json")
        self.assertEqual(code, 2, out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("NO-DATA:"), out)

    def test_invalid_utf8_report_is_nodata(self):
        with tempfile.TemporaryDirectory(prefix="fx153_l5b_") as tmp:
            _write(tmp, AUDIT_REL, "import sys\nsys.exit(0)\n")
            _copy_score(tmp)
            _write(tmp, REPORT_REL, b"\xff\xfe\x00{", binary=True)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = donecheck_L5b.main(["--root", tmp])
            self.assertEqual(code, 2, buffer.getvalue())

    def test_missing_l5b_audit_is_nodata(self):
        code, out = _run(write_audit=False)
        self.assertEqual(code, 2, out)
        self.assertIn("L5b.6 has not landed", out)

    def test_missing_root_is_nodata(self):
        with tempfile.TemporaryDirectory(prefix="fx153_l5b_") as tmp:
            missing = os.path.join(tmp, "absent")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = donecheck_L5b.main(["--root", missing])
            self.assertEqual(code, 2, buffer.getvalue())

    def test_recheck_exit_2_is_nodata(self):
        code, out = _run(audit_body="import sys\nsys.exit(2)\n")
        self.assertEqual(code, 2, out)

    def test_recheck_timeout_is_nodata(self):
        code, out = _run(audit_body="import time\ntime.sleep(30)\n", timeout=1)
        self.assertEqual(code, 2, out)
        self.assertIn("timed out", out)

    def test_hostile_report_types_are_nodata(self):
        cases = [
            {"boundary_calls_total": "10"},
            {"boundary_calls_total": True},
            {"boundary_calls_total": float("nan")},
            {"fail_closed": "BLOCK"},
            {"exemptions": {"a": "b"}},
            {"proposed_patches": "patch"},
            {"boundary_calls_total": None},
        ]
        for override in cases:
            with self.subTest(override=override):
                report = _green_report()
                report.update(override)
                code, out = _run(report=report)
                self.assertEqual(code, 2, "override %r -> %s" % (override, out))


class FailTest(unittest.TestCase):
    def test_recheck_exit_1_is_red(self):
        code, out = _run(audit_body="import sys\nsys.exit(1)\n")
        self.assertEqual(code, 1, out)
        self.assertTrue(out.strip().splitlines()[-1].startswith("FAIL:"), out)

    def test_typed_score_is_ignored_records_decide(self):
        report = _green_report()
        report["score_decimal"] = 9.9
        report["boundary_calls_stated"] = 1
        report["boundary_calls_tested"] = 0
        code, out = _run(report=report)
        self.assertEqual(code, 1, out)

    def test_hidden_waiver_is_red(self):
        report = _green_report()
        report["exemptions"] = [{"hit_id": "H1", "reason": "a" * 30}]
        report["exemptions_applied"] = 0
        code, out = _run(report=report)
        self.assertEqual(code, 1, out)
        self.assertIn("hidden waiver", out)

    def test_marker_added_by_fix_is_red(self):
        marker = "l5b: " + "PROPAGATE" + "_SAFE"
        report = _green_report()
        diff = "--- a/x\n+++ b/x\n+ # " + marker + "\n"
        report["proposed_patches"] = [{"change": "PROPOSED_PATCH_ONLY", "patch_unified_diff": diff}]
        code, out = _run(report=report)
        self.assertEqual(code, 1, out)
        self.assertIn(marker, out)

    def test_fix_applied_in_place_is_red(self):
        report = _green_report()
        report["fixes_applied"] = ["x"]
        code, out = _run(report=report)
        self.assertEqual(code, 1, out)

    def test_bad_change_kind_is_red(self):
        report = _green_report()
        report["proposed_patches"] = [{"change": "SOMETHING_ELSE"}]
        code, out = _run(report=report)
        self.assertEqual(code, 1, out)

    def test_bad_clearance_is_red(self):
        report = _green_report()
        report["annotation_clearances"] = [{"call_id": "C1", "covered_by": "BARE_HAND"}]
        code, out = _run(report=report)
        self.assertEqual(code, 1, out)


class ReadOnlyTest(unittest.TestCase):
    def test_main_writes_nothing_under_root(self):
        with tempfile.TemporaryDirectory(prefix="fx153_l5b_") as tmp:
            _write(tmp, AUDIT_REL, "import sys\nsys.exit(0)\n")
            _copy_score(tmp)
            _write(tmp, REPORT_REL, json.dumps(_green_report()))
            before = _snapshot(tmp)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                donecheck_L5b.main(["--root", tmp])
            after = _snapshot(tmp)
            self.assertEqual(before, after)

    def test_repeat_run_gives_the_same_verdict(self):
        code1, out1 = _run()
        code2, out2 = _run()
        self.assertEqual(code1, code2)
        self.assertEqual(out1.strip().splitlines()[-1], out2.strip().splitlines()[-1])


class SecretsTest(unittest.TestCase):
    def test_approval_token_is_never_printed(self):
        token = "APPROVAL" + "-" + "TOKEN" + "-" + "XYZ"
        report = _green_report()
        report["exemptions"] = [
            {"hit_id": "H1", "reason": "a" * 30, "approver": "role-approver", "approval_token": token}
        ]
        report["exemptions_applied"] = 1
        code, out = _run(report=report)
        self.assertEqual(code, 0, out)
        self.assertIn("H1", out)
        self.assertNotIn(token, out)
        self.assertNotIn("role-approver", out)


class RealTreeTest(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir(os.path.join(REPO, "docs", "plan")), "real tree not present")
    def test_real_tree_case(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = donecheck_L5b.main(["--root", REPO])
        self.assertIn(code, (0, 1, 2))
        lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
        self.assertTrue(lines)
        words = {0: "PASS:", 1: "FAIL:", 2: "NO-DATA:"}
        self.assertTrue(lines[-1].startswith(words[code]), lines[-1])


if __name__ == "__main__":
    unittest.main()
