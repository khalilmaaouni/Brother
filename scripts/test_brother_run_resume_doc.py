"""RW1.a: the run folder's marker (target.json) names the Work document, and
every doubt refuses with a reason.

brother_run._find_work_doc_why(run_dir) is the one lookup. Pointer mode (the
marker is a JSON object carrying `work_document`): the value must be a plain
.json name in the folder, a regular file (never a link), and parse as a Work
document; anything else refuses and never falls back to another file.
Legacy mode (no marker, or an object without the key): the one candidate
rule, two or more refusing as ambiguous. A marker that exists but is
unreadable, corrupt or not a JSON object refuses (decision RW1, 2026-10-11),
and the marker is read through one helper shared with _read_run_target, so
discovery no longer crashes on a JSON array. Spec: docs/plan/specs/RW1.md,
section RW1.a, as tightened by decisions-v5/SYNTHESIS.md.

Done check: python3 -B scripts/test_brother_run_resume_doc.py
"""
import builtins
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brother_run as _br  # noqa: E402
import work_record as WR  # noqa: E402
from test_brother_run import setUpModule, tearDownModule  # noqa: E402,F401

BROTHER_RUN = os.path.join(HERE, "brother_run.py")


def _doc(folder, work_id):
    rec, problems = WR.create("outcome " + work_id, [
        {"id": "A1", "title": "create a1", "done_check": "test -f A1.txt",
         "owns": ["A1.txt"]}], work_id=work_id, store=folder)
    assert problems == [], problems
    return rec["path"]


def _write(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body if isinstance(body, str) else json.dumps(body))
    return path


class _Fixture(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="brother-resume-doc-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.run_dir = os.path.join(self.root, "run")
        os.makedirs(self.run_dir)
        self.outside = _doc(self.root, "W-outside")

    def marker(self, body):
        return _write(os.path.join(self.run_dir, _br.TARGET_FILENAME), body)

    def pointer(self, name):
        return self.marker({"cwd": self.root, "work_document": name})

    def why(self):
        return _br._find_work_doc_why(self.run_dir)


class APointerPicksItsDocument(_Fixture):
    """REQ-RW1-A-PICK, R01 and R02."""

    def test_r01_a_stray_json_does_not_hide_the_named_document(self):
        doc = _doc(self.run_dir, "W-one")
        _write(os.path.join(self.run_dir, "diagnostics.json"), {"x": 1})
        self.pointer(os.path.basename(doc))
        self.assertEqual(self.why(), (doc, ""))
        self.assertEqual(_br._find_work_doc(self.run_dir), doc)

    def test_r02_a_second_work_document_is_not_chosen(self):
        doc = _doc(self.run_dir, "W-one")
        _doc(self.run_dir, "delivery_summary")
        self.pointer(os.path.basename(doc))
        self.assertEqual(self.why(), (doc, ""))


class APointerThatIsNotAPlainNameRefuses(_Fixture):
    """REQ-RW1-A-TRAVERSAL and REQ-RW1-A-ENGINE, R03: refused before any
    filesystem call touches the named file (M-RW1A-BASENAME), and never a
    fall back to the one legacy candidate present (M-RW1A-FALLBACK)."""

    def _recorded(self):
        seen = []

        def spy(real):
            def call(path, *a, **k):
                seen.append(os.fspath(path) if isinstance(
                    path, (str, bytes, os.PathLike)) else path)
                return real(path, *a, **k)
            return call
        patches = [mock.patch.object(os, name, spy(getattr(os, name)))
                   for name in ("open", "stat", "lstat", "listdir")]
        patches.append(mock.patch.object(builtins, "open", spy(builtins.open)))
        for p in patches:
            p.start()
        try:
            result = self.why()
        finally:
            for p in reversed(patches):
                p.stop()
        return result, seen

    def test_r03_traversal_names_refuse_without_touching_the_target(self):
        _doc(self.run_dir, "W-one")
        os.makedirs(os.path.join(self.run_dir, "sub"))
        _doc(os.path.join(self.run_dir, "sub"), "W-sub")
        for name in ("../W-outside.json", self.outside, "sub/W-sub.json",
                     "sub\\W-sub.json", "..", ".", "W-one.json\x00",
                     "W-one.json\n", ""):
            with self.subTest(name=name):
                self.pointer(name)
                (path, why), seen = self._recorded()
                self.assertIsNone(path)
                self.assertIn("not a plain .json file name", why)
                self.assertIn("did not fall back", why)
                touched = [p for p in seen if isinstance(p, str)
                           and ("W-outside" in p or "W-sub" in p)]
                self.assertEqual(touched, [], seen)

    def test_engine_files_and_non_json_names_refuse(self):
        _doc(self.run_dir, "W-one")
        for name in sorted(_br.ENGINE_JSON_FILES) + ["notes.txt", "W-one"]:
            with self.subTest(name=name):
                self.pointer(name)
                path, why = self.why()
                self.assertIsNone(path)
                self.assertIn("not a plain .json file name", why)

    def test_a_pointer_that_is_not_a_string_refuses(self):
        """M-RW1A-KEY-NONSTR: a null or non-string value is a pointer that
        is wrong, never an absent pointer."""
        _doc(self.run_dir, "W-one")
        for value in (None, 3, ["W-one.json"], {"name": "W-one.json"}, True):
            with self.subTest(value=value):
                self.pointer(value)
                path, why = self.why()
                self.assertIsNone(path)
                self.assertIn("not a plain .json file name", why)


class APointerToALinkOrFolderRefuses(_Fixture):
    """REQ-RW1-A-LINK, R04 (M-RW1A-FOLLOW): nothing outside is read."""

    def test_r04_a_link_to_an_outside_document_refuses(self):
        _doc(self.run_dir, "W-one")
        os.symlink(self.outside, os.path.join(self.run_dir, "W-link.json"))
        self.pointer("W-link.json")
        path, why = self.why()
        self.assertIsNone(path)
        self.assertIn("is a link, not a regular file", why)

    def test_r04_a_folder_refuses(self):
        os.makedirs(os.path.join(self.run_dir, "W-dir.json"))
        _doc(self.run_dir, "W-one")
        self.pointer("W-dir.json")
        path, why = self.why()
        self.assertIsNone(path)
        self.assertIn("is not a regular file", why)


class ABadPointerTargetNeverFallsBack(_Fixture):
    """REQ-RW1-A-NOFALLBACK, R05 (M-RW1A-FALLBACK, M-RW1A-NOPARSE)."""

    def test_r05_a_missing_target_refuses_beside_one_valid_document(self):
        doc = _doc(self.run_dir, "W-other")
        self.pointer("W-missing.json")
        path, why = self.why()
        self.assertIsNone(path)
        self.assertIn("does not exist", why)
        self.assertIn("did not fall back", why)
        self.assertNotIn(os.path.basename(doc), why)

    def test_r05_a_target_that_is_not_a_work_document_refuses(self):
        _doc(self.run_dir, "W-other")
        for body, words in (
                ("not json", "is not valid JSON"),
                ("[]", "is not a Work document"),
                ({"rows": []}, "is not a Work document"),
                ({"rows": [{"id": ""}]}, "is not a Work document"),
                ({"rows": ["A1"]}, "is not a Work document"),
                ({"outcome": "x"}, "is not a Work document")):
            with self.subTest(body=body):
                _write(os.path.join(self.run_dir, "W-bad.json"), body)
                self.pointer("W-bad.json")
                path, why = self.why()
                self.assertIsNone(path)
                self.assertIn(words, why)


class ACorruptMarkerRefuses(_Fixture):
    """Decision RW1 (M-RW1A-CORRUPT-IS-LEGACY): a marker that exists but is
    unreadable, corrupt or not a JSON object refuses with its reason, even
    beside exactly one legacy candidate."""

    def test_a_corrupt_marker_refuses(self):
        _doc(self.run_dir, "W-one")
        for body in ("not json", "", "[]", '"W-one.json"', "3", "null",
                     "[" * 200000):
            with self.subTest(body=body):
                self.marker(body)
                path, why = self.why()
                self.assertIsNone(path)
                self.assertIn("target.json", why)
                self.assertIn("resume does not guess", why)

    def test_a_marker_that_is_a_folder_or_a_link_refuses(self):
        _doc(self.run_dir, "W-one")
        target = os.path.join(self.run_dir, _br.TARGET_FILENAME)
        os.makedirs(target)
        self.assertIn("resume does not guess", self.why()[1])
        os.rmdir(target)
        elsewhere = _write(os.path.join(self.root, "elsewhere.json"),
                           {"cwd": self.root})
        os.symlink(elsewhere, target)
        self.assertEqual(self.why()[0], None)
        self.assertIn("resume does not guess", self.why()[1])


class TheLegacyRuleIsUnchanged(_Fixture):
    """REQ-RW1-A-LEGACY, R06 (a pin) and R07 (M-RW1A-FIRST-OF-MANY)."""

    def test_r06_one_candidate_without_a_pointer(self):
        doc = _doc(self.run_dir, "W-one")
        self.assertEqual(self.why(), (doc, ""))
        self.marker({"cwd": self.root})
        self.assertEqual(self.why(), (doc, ""))
        self.assertEqual(_br._find_work_doc(self.run_dir), doc)

    def test_r07_two_candidates_refuse_as_ambiguous(self):
        _doc(self.run_dir, "W-one")
        _write(os.path.join(self.run_dir, "diagnostics.json"), {"x": 1})
        self.marker({"cwd": self.root})
        path, why = self.why()
        self.assertIsNone(path)
        self.assertEqual(why, "ambiguous: 2 candidate files (W-one.json, "
                              "diagnostics.json) and target.json names none "
                              "of them")
        self.assertIsNone(_br._find_work_doc(self.run_dir))

    def test_empty_and_missing_folders(self):
        path, why = self.why()
        self.assertIsNone(path)
        self.assertIn("no Work document", why)
        gone = os.path.join(self.root, "gone")
        self.assertEqual(_br._find_work_doc_why(gone),
                         (None, "%s is not a directory" % gone))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root lists any folder")
    def test_an_unlistable_folder_refuses(self):
        _doc(self.run_dir, "W-one")
        os.chmod(self.run_dir, 0)
        self.addCleanup(os.chmod, self.run_dir, 0o755)
        path, why = self.why()
        self.assertIsNone(path)
        self.assertIn(_br.WORK_DOC_UNLISTABLE, why)

    def test_the_same_answer_twice_and_nothing_written(self):
        doc = _doc(self.run_dir, "W-one")
        _write(os.path.join(self.run_dir, "diagnostics.json"), {})
        self.pointer(os.path.basename(doc))
        before = sorted(os.listdir(self.run_dir))
        self.assertEqual(self.why(), self.why())
        self.assertEqual(sorted(os.listdir(self.run_dir)), before)


class DiscoveryReadsTheMarkerThroughTheSameHelper(_Fixture):
    """Decision RW1-DISCOVERY (M-RW1A-MARKER-GET): find_unfinished_runs over a
    run whose marker is a JSON array, a number or garbage neither crashes
    nor offers that run; a well formed sibling is still found."""

    def test_a_malformed_marker_is_skipped_never_a_crash(self):
        runs = os.path.join(self.root, "docs", "plan", "runs")
        repo = os.path.join(self.root, "repo")
        os.makedirs(repo)
        for name, body in (("1-array", "[]"), ("2-number", "3"),
                           ("3-garbage", "{"), ("4-cwd-not-text",
                                                {"cwd": 7})):
            folder = os.path.join(runs, name)
            _doc(folder, "W-" + name)
            _write(os.path.join(folder, _br.TARGET_FILENAME), body)
        good = os.path.join(runs, "5-good")
        _doc(good, "W-good")
        _br._write_run_target(good, repo)
        found = _br.find_unfinished_runs(self.root, repo)
        self.assertEqual([os.path.basename(d) for d, _o, _r in found],
                         ["5-good"])
        self.assertIsNone(_br._read_run_target(os.path.join(runs, "1-array")))


class ResumePrintsTheReason(_Fixture):
    """REQ-RW1-A-WHY (M-RW1A-SILENT): --resume says why it refused."""

    def test_resume_over_an_ambiguous_folder_names_the_reason(self):
        _doc(self.run_dir, "W-one")
        _write(os.path.join(self.run_dir, "diagnostics.json"), {})
        proc = subprocess.run(
            [sys.executable, BROTHER_RUN, "--resume", self.run_dir, "--cwd",
             self.root], capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("brother_run: --resume could not find the Work document "
                      "in %s: ambiguous: 2 candidate files" % self.run_dir,
                      proc.stderr)


if __name__ == "__main__":
    unittest.main()
