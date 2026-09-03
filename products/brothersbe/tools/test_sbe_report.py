#!/usr/bin/env python3
"""Tests for the reporting-only CI entry point (tools/sbe_report.py).

Run: python3 tools/test_sbe_report.py

The bar, same as tools/test_sbe_onepager.py: a test that only proves the
happy path proves nothing. The tests that matter here are (a) a deliberately
broken change still exits 0 while the report names the defect by task id and
verdict, and (b) an internal crash of the reporter itself is caught and
turned into an honest line instead of a nonzero exit or a stack trace.
"""
import html as html_lib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

# Built from codepoints, never typed as a literal glyph: a raw em/en dash in
# THIS file would itself trip the repo-wide dash scan this contract's own
# done-check runs, over the very file asserting the rule.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

_spec = importlib.util.spec_from_file_location(
    "sbe_report", os.path.join(HERE, "sbe_report.py"))
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def write_json(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj))


def write_text(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def tasks_registry(tasks):
    return {"schemaVersion": "1.1", "tasks": tasks}


def one_task(task_id="T01", owned=None, verify="pytest tools/test_foo.py"):
    return {
        "id": task_id,
        "change": "",
        "agent": "builder",
        "role": "writer",
        "ownedPaths": owned if owned is not None else ["tools/foo.py"],
        "readOnlyPaths": [],
        "baseCommit": "0" * 40,
        "expiry": None,
        "status": "closed",
        "verifyCommand": verify,
        "evidenceId": None,
        "openedAt": "t",
        "closedAt": "t",
    }


class ReportCase(unittest.TestCase):
    """A real temp root and a one-shot subprocess runner, mirroring
    tools/test_sbe_onepager.py's OnepagerCase shape."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def run_once(self, target=None, out=None, html=None):
        argv = [target if target is not None else self.root]
        if out is not None:
            argv += ["--out", out]
        if html is not None:
            argv += ["--html", html]
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "sbe_report.py")] + argv,
            capture_output=True, text=True, timeout=60)
        return proc


class TestForcedBrokenChangeStillExitsZero(ReportCase):
    """THE done-check that must not be softened: a deliberately broken
    change (a receipt recording a real FAIL) produces a report NAMING the
    defect, and the process exit code is 0."""

    def test_a_failing_receipt_is_named_and_the_process_still_exits_zero(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task("T-BROKEN")]))
        write_json(os.path.join(self.root, ".sbe", "evidence",
                                 "T-BROKEN-receipt.json"),
                   {"command": "pytest tools/test_foo.py", "verdict": "FAIL"})
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("T-BROKEN", proc.stdout)
        self.assertIn("FAIL", proc.stdout)
        self.assertIn("pytest tools/test_foo.py", proc.stdout,
                      "the report must name the command that proved the defect")
        self.assertIn("1 FAIL", proc.stdout, "the SUMMARY line must count the FAIL")

    def test_a_gap_task_is_also_named_and_the_process_still_exits_zero(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task("T-GAP")]))
        # No receipt written at all: a real, un-swept gap.
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("T-GAP", proc.stdout)
        self.assertIn("NOT CHECKED", proc.stdout)
        self.assertIn("1 unverified", proc.stdout)


class TestCrashContainment(unittest.TestCase):
    """If the reporter itself breaks (a bug, an unreadable root, anything),
    main() must still return 0 and print the honest could-not-report line
    instead of letting the exception propagate as a stack trace / nonzero
    exit. Forced in-process by monkeypatching onepager.render, which is the
    one call build_report cannot avoid making."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self._real_render = sr.onepager.render

    def tearDown(self):
        sr.onepager.render = self._real_render
        self._tmp.cleanup()

    def test_a_crash_in_the_renderer_is_caught_and_reported_honestly(self):
        def boom(root):
            raise RuntimeError("deliberately broken for the calibration test")
        sr.onepager.render = boom

        buf = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = buf
        try:
            code = sr.main([self.root])
        finally:
            sys.stdout = real_stdout

        self.assertEqual(code, 0, "an internal crash must still return 0")
        out = buf.getvalue()
        self.assertIn("could not report because", out)
        self.assertIn("deliberately broken for the calibration test", out)
        self.assertIn("SUMMARY", out)
        self.assertNotIn("Traceback", out)


class TestHealthyFixtureNamesReceiptsThroughout(ReportCase):

    def test_every_claim_line_in_a_healthy_packet_names_its_receipt(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task("T01"), one_task("T02")]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["pytest", "tools/test_foo.py", "-v"], "exitCode": 0})
        write_json(os.path.join(self.root, ".sbe", "evidence", "T02-receipt.json"),
                   {"command": "pytest tools/test_bar.py", "verdict": "PASS"})
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        self.assertIn("T01: the check", out)
        self.assertIn("pytest tools/test_foo.py -v", out)
        self.assertIn("T02: the check", out)
        self.assertIn("pytest tools/test_bar.py", out)
        self.assertIn("2 PASS", out)
        self.assertIn("no gaps", out)

    def test_summary_distinguishes_pass_fail_no_data_and_unverified(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task("T-PASS"), one_task("T-FAIL"),
                                    one_task("T-NODATA"), one_task("T-GAP")]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T-PASS-receipt.json"),
                   {"command": "true", "verdict": "PASS"})
        write_json(os.path.join(self.root, ".sbe", "evidence", "T-FAIL-receipt.json"),
                   {"command": "false", "verdict": "FAIL"})
        write_json(os.path.join(self.root, ".sbe", "evidence", "T-NODATA-receipt.json"),
                   {"command": "true", "verdict": "NO-DATA"})
        # T-GAP: no receipt at all.
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SUMMARY: 4 task(s): 1 PASS, 1 FAIL, 1 NO-DATA, 1 unverified.",
                      proc.stdout)


class TestRunIdBindingFlowsThroughSummarize(ReportCase):
    """The A1 defect found 2026-08-11: a receipt at a custom --out path,
    bound to its task by runId content match rather than filename, must be
    counted PASS in the SUMMARY line, not unverified. summarize() only
    counts what onepager.evaluate_task reports, so this is the same fix as
    tools/test_sbe_onepager.py's TestRunIdBinding, proven through the CI
    entry point instead of the one-pager directly."""

    def test_evidence_id_as_a_run_id_shows_one_pass_zero_unverified(self):
        run_id = "c" * 64
        task = one_task("T01")
        task["evidenceId"] = run_id
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([task]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "custom-out-name.json"),
                   {"command": "true", "verdict": "PASS", "runId": run_id})
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SUMMARY: 1 task(s): 1 PASS, 0 FAIL, 0 NO-DATA, 0 unverified.",
                      proc.stdout)


class TestNoEvidenceRootStillReportsAndExitsZero(ReportCase):

    def test_a_root_with_no_task_registry_reports_plainly_and_exits_zero(self):
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SUMMARY", proc.stdout)
        self.assertIn("no task registry was found", proc.stdout)


class TestOutFlag(ReportCase):

    def test_out_writes_the_same_report_to_a_file_and_still_prints_to_stdout(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["true"], "exitCode": 0})
        out_path = os.path.join(self.root, "report.txt")
        proc = self.run_once(out=out_path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SUMMARY", proc.stdout, "sbe_report always prints to stdout too")
        with open(out_path) as fh:
            content = fh.read()
        self.assertIn("SUMMARY", content)
        self.assertEqual(content, proc.stdout)

    def test_an_unwritable_out_path_is_reported_but_still_exits_zero(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["true"], "exitCode": 0})
        # A path under a file (not a directory) can never be created.
        blocker = os.path.join(self.root, "blocker")
        write_text(blocker, "not a directory")
        bad_out = os.path.join(blocker, "report.txt")
        proc = self.run_once(out=bad_out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SUMMARY", proc.stdout, "the report itself must still print")
        self.assertIn("could not write", proc.stderr)


class TestNoDashes(ReportCase):

    def test_the_report_never_carries_an_em_or_en_dash(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["true"], "exitCode": 1})
        out = self.run_once().stdout
        self.assertNotIn(EM_DASH, out)
        self.assertNotIn(EN_DASH, out)


# ---------------------------------------------------------------------------
# --html: the self-contained forwardable artifact (loop R0.9.5).
# ---------------------------------------------------------------------------

EXTERNAL_REF_PATTERNS = [
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"<link\b", re.IGNORECASE),
    re.compile(r"<script\b[^>]*\bsrc=", re.IGNORECASE),
    re.compile(r"@import", re.IGNORECASE),
    re.compile(r"url\(", re.IGNORECASE),
    re.compile(r"\bsrc\s*=\s*[\"'](?!data:)", re.IGNORECASE),
]


def find_external_references(html_text):
    """Every external-reference pattern that fired against `html_text`, by
    its regex source. Empty means clean. This is the check the calibration
    test below proves is not a rubber stamp: it is run once against a
    template with a real injected reference (must be non-empty) and once
    against the restored template (must be empty)."""
    return [p.pattern for p in EXTERNAL_REF_PATTERNS if p.search(html_text)]


def visible_text(html_text):
    """`html_text` with every tag stripped and every entity unescaped, so a
    test can assert a plain-text claim line is present in the rendered page
    without caring how it was marked up."""
    no_tags = re.sub(r"<[^>]+>", "", html_text)
    return html_lib.unescape(no_tags)


class TestHtmlSelfContained(ReportCase):
    """(a) the produced file contains zero external references, calibrated
    by injecting a reference into the generator and watching the test go
    red, then restoring."""

    def _healthy_html(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["true"], "exitCode": 0})
        return sr.build_html(self.root)

    def test_generated_html_carries_zero_external_references(self):
        self.assertEqual(find_external_references(self._healthy_html()), [])

    def test_calibration_an_injected_reference_is_actually_caught(self):
        original_template = sr.HTML_TEMPLATE
        sr.HTML_TEMPLATE = original_template.replace(
            "<head>",
            "<head>\n<link rel=\"stylesheet\" "
            "href=\"https://example.com/injected.css\">")
        try:
            broken_html = self._healthy_html()
            hits = find_external_references(broken_html)
            self.assertNotEqual(
                hits, [],
                "the injected reference was not caught, this check proves "
                "nothing: went red as expected")
        finally:
            sr.HTML_TEMPLATE = original_template
        restored_html = self._healthy_html()
        self.assertEqual(find_external_references(restored_html), [],
                          "the check must be clean again once the template "
                          "is restored")

    def test_html_is_a_standalone_document_with_no_script(self):
        html_text = self._healthy_html()
        self.assertTrue(html_text.strip().lower().startswith("<!doctype html>"))
        self.assertNotIn("<script", html_text.lower())

    def test_html_never_carries_an_em_or_en_dash(self):
        html_text = self._healthy_html()
        self.assertNotIn(EM_DASH, html_text)
        self.assertNotIn(EN_DASH, html_text)


class TestHtmlPreservesEveryEvidenceLine(ReportCase):
    """(b) the file still renders every claim line the text packet carried,
    so the HTML cannot silently drop evidence."""

    def test_every_line_of_the_text_packet_appears_in_the_html(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task("T01"), one_task("T02")]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["pytest", "tools/test_foo.py", "-v"], "exitCode": 0})
        write_json(os.path.join(self.root, ".sbe", "evidence", "T02-receipt.json"),
                   {"command": "pytest tools/test_bar.py", "verdict": "FAIL"})
        text = sr.build_report(self.root)
        visible = visible_text(sr.build_html(self.root))
        for line in text.splitlines():
            if line.strip() == "":
                continue
            self.assertIn(line, visible,
                          "the HTML dropped an evidence line: %r" % line)

    def test_a_gap_and_no_data_line_both_survive_into_the_html(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task("T-GAP"), one_task("T-NODATA")]))
        write_json(os.path.join(self.root, ".sbe", "evidence",
                                 "T-NODATA-receipt.json"),
                   {"command": "true", "verdict": "NO-DATA"})
        text = sr.build_report(self.root)
        visible = visible_text(sr.build_html(self.root))
        for line in text.splitlines():
            if line.strip() == "":
                continue
            self.assertIn(line, visible, "missing evidence line: %r" % line)
        self.assertIn("NOT CHECKED", visible)
        self.assertIn("NO-DATA", visible)


class TestHtmlFlagAlwaysExitsZero(ReportCase):
    """(c) exit code is zero even when the target is broken."""

    def test_html_written_for_a_healthy_root_and_exit_code_is_zero(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["true"], "exitCode": 0})
        html_out = os.path.join(self.root, "report.html")
        proc = self.run_once(html=html_out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(html_out) as fh:
            content = fh.read()
        self.assertIn("<!doctype html>", content.lower())
        self.assertIn("SUMMARY", content)

    def test_html_written_for_a_root_with_no_task_registry_and_exit_zero(self):
        # self.root is an empty temp dir: no .sbe/tasks.json at all.
        html_out = os.path.join(self.root, "report.html")
        proc = self.run_once(html=html_out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(html_out) as fh:
            content = fh.read()
        self.assertIn("no task registry was found", content)

    def test_an_unwritable_html_path_is_reported_but_still_exits_zero(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["true"], "exitCode": 0})
        blocker = os.path.join(self.root, "blocker")
        write_text(blocker, "not a directory")
        bad_html = os.path.join(blocker, "report.html")
        proc = self.run_once(html=bad_html)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SUMMARY", proc.stdout, "the text report must still print")
        self.assertIn("could not write html", proc.stderr)


class TestHtmlCrashContainment(unittest.TestCase):
    """A crash inside the renderer itself must still leave the HTML write
    honest and the process exit code at 0, mirroring TestCrashContainment
    above but proven through the HTML path."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self._real_render = sr.onepager.render

    def tearDown(self):
        sr.onepager.render = self._real_render
        self._tmp.cleanup()

    def test_html_write_still_happens_honestly_when_the_renderer_crashes(self):
        def boom(root):
            raise RuntimeError("deliberately broken for the html calibration test")
        sr.onepager.render = boom

        html_out = os.path.join(self.root, "report.html")
        buf = io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = buf
        try:
            code = sr.main([self.root, "--html", html_out])
        finally:
            sys.stdout = real_stdout

        self.assertEqual(code, 0, "an internal crash must still return 0")
        with open(html_out) as fh:
            content = fh.read()
        self.assertIn("could not report because", content)
        self.assertIn("deliberately broken for the html calibration test",
                      content)
        self.assertNotIn("Traceback", content)
        self.assertEqual(find_external_references(content), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
