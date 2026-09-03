#!/usr/bin/env python3
"""Tests for the reviewer one-pager (tools/sbe_onepager.py).

Run: python3 tools/test_sbe_onepager.py

The bar: a test that only proves the happy path proves nothing here. The
calibration test that matters hollows a receipt three different ways (empty
fields, an empty file, a deleted file) and asserts the SAME task line flips
from a checked line to a NOT CHECKED line and never simply vanishes.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))

# Built from codepoints, never typed as a literal glyph: a raw em/en dash
# sitting in THIS source file would itself trip the repo-wide dash scan this
# contract's own done-check runs, over the file whose job is to assert the
# rule against every OTHER file.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

_spec = importlib.util.spec_from_file_location(
    "sbe_onepager", os.path.join(HERE, "sbe_onepager.py"))
op = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(op)


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


class OnepagerCase(unittest.TestCase):
    """A real temp root and a one-shot subprocess runner, mirroring
    tools/test_stall_detector.py's DetectorCase shape."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def run_once(self, **overrides):
        argv = ["--root", self.root]
        for k, v in overrides.items():
            argv += ["--%s" % k.replace("_", "-"), str(v)]
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "sbe_onepager.py")] + argv,
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc


class TestCheckedLineNamesEvidence(OnepagerCase):

    def test_a_real_receipt_names_its_command_and_verdict(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["pytest", "tools/test_foo.py", "-v"], "exitCode": 0})
        out = self.run_once().stdout
        self.assertIn("T01", out)
        self.assertIn("pytest tools/test_foo.py -v", out, out)
        self.assertIn("PASS", out)
        # The section header itself always reads "WHAT WAS NOT CHECKED", so the
        # absence check is against a task-shaped gap line, not the substring.
        self.assertNotIn("T01: NOT CHECKED", out)
        self.assertIn("no gaps", out)

    def test_a_failing_receipt_still_names_its_command_and_a_fail_verdict(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"command": "pytest tools/test_foo.py", "verdict": "FAIL"})
        out = self.run_once().stdout
        self.assertIn("pytest tools/test_foo.py", out)
        self.assertIn("FAIL", out)
        self.assertIn("Fix the failing check", out, "the recommendation must react to a FAIL")


class TestHollowReceiptIsTheCalibrationThatMatters(OnepagerCase):
    """Same fixture as above; only the receipt is hollowed, three ways. Each
    variant must flip the SAME task's line to NOT CHECKED, and the line must
    still appear -- a line that silently vanished would be worse than a
    wrong verdict, because nobody would know to go look."""

    def setUp(self):
        OnepagerCase.setUp(self)
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        self.receipt = os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json")

    def test_emptied_fields_flip_the_line_to_not_checked(self):
        write_json(self.receipt, {"argv": [], "exitCode": None})
        out = self.run_once().stdout
        self.assertIn("T01", out)
        self.assertIn("NOT CHECKED", out)
        self.assertIn("missing", out)
        self.assertNotIn("the verdict was PASS", out)
        self.assertNotIn("the verdict was FAIL", out)

    def test_a_deleted_receipt_flips_the_line_to_not_checked(self):
        # No receipt file is written at all: the task is real, the evidence
        # for it is not.
        out = self.run_once().stdout
        self.assertIn("T01", out)
        self.assertIn("NOT CHECKED", out)
        self.assertIn("no receipt found", out)

    def test_an_empty_receipt_file_flips_the_line_to_not_checked(self):
        write_text(self.receipt, "")
        out = self.run_once().stdout
        self.assertIn("T01", out)
        self.assertIn("NOT CHECKED", out)
        self.assertIn("empty", out)

    def test_a_corrupt_receipt_file_flips_the_line_to_not_checked(self):
        write_text(self.receipt, "{not json")
        out = self.run_once().stdout
        self.assertIn("T01", out)
        self.assertIn("NOT CHECKED", out)
        self.assertIn("not valid JSON", out)

    def test_an_explicit_no_data_verdict_is_a_gap_not_a_check(self):
        write_json(self.receipt, {"command": "pytest tools/test_foo.py",
                                  "verdict": "NO-DATA"})
        out = self.run_once().stdout
        self.assertIn("T01", out)
        self.assertIn("NOT CHECKED", out)
        self.assertIn("NO-DATA", out)


class TestNoEvidenceRoot(OnepagerCase):

    def test_a_root_with_no_task_registry_prints_a_plain_page_not_a_crash(self):
        proc = self.run_once()
        out = proc.stdout
        self.assertNotEqual(out.strip(), "", "an evidence-free root must not print an empty page")
        self.assertIn("no task registry was found", out)
        self.assertIn("RECOMMENDED NEXT ACTION", out)

    def test_an_empty_task_list_also_prints_a_plain_page(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"), tasks_registry([]))
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotEqual(proc.stdout.strip(), "")
        self.assertIn("records no tasks", proc.stdout)

    def test_a_corrupt_registry_also_prints_a_plain_page(self):
        write_text(os.path.join(self.root, ".sbe", "tasks.json"), "{not json")
        proc = self.run_once()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("not valid JSON", proc.stdout)


class TestSectionsAndProvenance(OnepagerCase):

    def test_what_changed_names_the_owned_files_from_the_registry(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task(owned=["tools/a.py", "tools/b.py"])]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["true"], "exitCode": 0})
        out = self.run_once().stdout
        self.assertIn("WHAT CHANGED", out)
        self.assertIn("tools/a.py, tools/b.py", out)

    def test_a_task_missing_its_receipt_beside_a_checked_one_leaves_both_visible(self):
        """Provenance must never be all-or-nothing: one task's real receipt
        must not paper over a sibling task's missing one."""
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task("T01"), one_task("T02")]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["true"], "exitCode": 0})
        out = self.run_once().stdout
        self.assertIn("T01: the check", out)
        self.assertIn("T02: NOT CHECKED", out)


class TestRunIdBinding(OnepagerCase):
    """The A1 defect found 2026-08-11 by a journey 3 walk: `sbe evidence run
    --check <id> --out <name>.json` seals the receipt with `runId` (a
    content hash, see src/brothersbe/evidence.py), and a task's own
    `evidenceId` is the SAME runId per status.py's documented contract
    ("when a registry record's evidenceId equals the receipt's runId").
    Treating evidenceId as a literal filename missed a receipt sitting at
    any other path and reported a checked task as NOT CHECKED."""

    def test_evidence_id_as_a_run_id_binds_to_the_receipt_by_content_not_filename(self):
        run_id = "a" * 64  # a sha256-shaped runId, the real receipt shape
        task = one_task("T01")
        task["evidenceId"] = run_id
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([task]))
        # Deliberately NOT named after the task id or the runId: the whole
        # point is a receipt sitting at an arbitrary --out path.
        write_json(os.path.join(self.root, ".sbe", "evidence", "custom-run-name.json"),
                   {"command": "pytest tools/test_foo.py", "verdict": "PASS",
                    "runId": run_id})
        out = self.run_once().stdout
        self.assertIn("T01: the check", out, out)
        self.assertIn("PASS", out)
        # As in TestCheckedLineNamesEvidence above: the section header itself
        # always reads "WHAT WAS NOT CHECKED", so assert against the
        # task-shaped gap line, not the bare substring.
        self.assertNotIn("T01: NOT CHECKED", out)

    def test_two_receipts_sharing_a_run_id_are_unverified_as_ambiguous_not_a_coin_flip(self):
        run_id = "b" * 64
        task = one_task("T01")
        task["evidenceId"] = run_id
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([task]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "one.json"),
                   {"command": "true", "verdict": "PASS", "runId": run_id})
        write_json(os.path.join(self.root, ".sbe", "evidence", "two.json"),
                   {"command": "true", "verdict": "PASS", "runId": run_id})
        out = self.run_once().stdout
        self.assertIn("T01", out)
        self.assertIn("NOT CHECKED", out)
        self.assertIn("ambig", out.lower())

    def test_old_filename_interpretation_still_works_when_that_file_exists(self):
        task = one_task("T01")
        task["evidenceId"] = "custom.json"
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([task]))
        # No runId field at all: nothing to content-match, so this must fall
        # back to the existing filename interpretation exactly as before.
        write_json(os.path.join(self.root, ".sbe", "evidence", "custom.json"),
                   {"command": "true", "verdict": "PASS"})
        out = self.run_once().stdout
        self.assertIn("T01: the check", out, out)
        self.assertIn("PASS", out)


class TestNoDashes(OnepagerCase):

    def test_the_page_never_carries_an_em_or_en_dash(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["pytest", "tools/test_foo.py"], "exitCode": 1})
        out = self.run_once().stdout
        # Written as escapes, not literal glyphs: a raw em/en dash living in
        # THIS file would itself trip the repo-wide scan this contract's own
        # done-check runs, over the very file that asserts the rule.
        self.assertNotIn(EM_DASH, out)
        self.assertNotIn(EN_DASH, out)

    def test_the_no_evidence_page_never_carries_an_em_or_en_dash(self):
        out = self.run_once().stdout
        self.assertNotIn(EM_DASH, out)
        self.assertNotIn(EN_DASH, out)


class TestOutFlag(OnepagerCase):

    def test_out_writes_the_same_page_to_a_file_and_stdout_stays_empty(self):
        write_json(os.path.join(self.root, ".sbe", "tasks.json"),
                   tasks_registry([one_task()]))
        write_json(os.path.join(self.root, ".sbe", "evidence", "T01-receipt.json"),
                   {"argv": ["pytest", "tools/test_foo.py"], "exitCode": 0})
        out_path = os.path.join(self.root, "page.txt")
        proc = self.run_once(out=out_path)
        self.assertEqual(proc.stdout, "")
        with open(out_path) as fh:
            content = fh.read()
        self.assertIn("PASS", content)
        self.assertIn("BROTHERSBE ONE-PAGER", content)


class TestEvidenceIdCannotReachOutsideTheRoot(OnepagerCase):
    """An evidenceId is registry CONTENT, and this tool is handed a root and
    asked to report what is under it. Unguarded, three shapes read files
    outside that root and reported them as this root's own evidence: a
    parent traversal, an absolute path, and a symlink planted inside the
    root. The guard is `contained` in tools/sbe_onepager.py; remove it and
    every test here goes red.

    The refusal is NAMED, never a silent redirect to the default receipt
    name: substituting a different file quietly would hide that the registry
    pointed outside the tree at all."""

    def _outside_receipt(self):
        outside = os.path.join(os.path.dirname(self.root), "outside-receipt.json")
        with io.open(outside, "w", encoding="utf-8") as fh:
            json.dump({"runId": "outside-run", "argv": ["a-command-from-outside"],
                       "exitCode": 0, "schemaVersion": "1.0"}, fh)
        self.addCleanup(lambda: os.path.exists(outside) and os.remove(outside))
        return outside

    def _ref(self, evidence_id):
        return op.resolve_receipt_path(
            self.root, {"id": "T01", "evidenceId": evidence_id, "verify": "x"})

    def test_a_parent_traversal_is_refused_by_name(self):
        self._outside_receipt()
        ref = self._ref(os.path.join("..", "outside-receipt.json"))
        self.assertIsNone(ref.path)
        self.assertIn("outside the root", ref.problem)

    def test_an_absolute_path_is_refused_by_name(self):
        outside = self._outside_receipt()
        ref = self._ref(outside)
        self.assertIsNone(ref.path)
        self.assertIn("outside the root", ref.problem)

    def test_a_symlink_planted_inside_the_root_cannot_escape(self):
        outside = self._outside_receipt()
        # The bare-name form resolves under .sbe/evidence/, so the symlink
        # has to be planted where the resolver actually looks; a link left
        # at the root would simply not be the file under test.
        evidence_dir = os.path.join(self.root, ".sbe", "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        os.symlink(outside, os.path.join(evidence_dir, "link-out.json"))
        ref = self._ref("link-out.json")
        self.assertIsNone(ref.path, "a symlink inside the root resolved to a "
                                    "file outside it and was accepted")
        self.assertIn("outside the root", ref.problem)

    def test_a_task_id_that_would_escape_is_refused_by_name(self):
        # The default receipt name is built from the task id, which is
        # registry content exactly as evidenceId is; guarding only the
        # evidenceId branch left this one open.
        # The default name is built under .sbe/evidence/, so two parent
        # segments land back in the root and are legitimately contained;
        # three actually leave it, which is what this pins.
        ref = op.resolve_receipt_path(
            self.root,
            {"id": os.path.join("..", "..", "..", "escaped"), "verify": "x"})
        self.assertIsNone(ref.path)
        self.assertIn("outside the root", ref.problem)

    def test_a_runid_match_on_a_link_out_of_the_root_is_not_returned(self):
        # The runId scan is the second place a path is built, and it returns
        # its match straight to the caller.
        outside = self._outside_receipt()
        evidence_dir = os.path.join(self.root, ".sbe", "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        os.symlink(outside, os.path.join(evidence_dir, "by-run-id.json"))
        ref = op.resolve_receipt_path(
            self.root, {"id": "T01", "evidenceId": "outside-run", "verify": "x"})
        self.assertNotEqual(
            ref.path, os.path.join(evidence_dir, "by-run-id.json"),
            "a runId match resolved through a link to a file outside the root")

    def test_an_ordinary_in_root_evidence_id_still_resolves(self):
        ref = self._ref("T01-receipt.json")
        self.assertIsNotNone(ref.path)
        self.assertIsNone(ref.problem)
        self.assertTrue(op.contained(self.root, ref.path))

    def test_a_nested_in_root_path_still_resolves(self):
        ref = self._ref(os.path.join("sub", "dir", "r.json"))
        self.assertIsNotNone(ref.path)
        self.assertIsNone(ref.problem)


if __name__ == "__main__":
    unittest.main(verbosity=1)
