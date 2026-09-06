"""The Long-Horizon Recovery 2026-09-06 resume record: proven, not just written.

Same reasoning as scripts/test_lhr_checkpoint.py, applied to
benchmarks/results/long-horizon-recovery/2026-09-06-resume/MANIFEST.json: a
manifest nobody drives backwards is a claim, not a control. The opus
evidence audit that found the first version of this record unmergeable
(~/.claude/evidence/audit-383-lhr-2026-09-06.md) checked MANIFEST.json's
hashes by hand; this makes that check a runnable one, reusing
test_lhr_checkpoint.check_manifest outright rather than reimplementing it
(the schema is identical: {"verdict": ..., "artefacts": [{"path", "sha256"},
...]}).
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RECORD = os.path.join(
    ROOT, "benchmarks", "results", "long-horizon-recovery",
    "2026-09-06-resume")

sys.path.insert(0, HERE)
from test_lhr_checkpoint import check_manifest, _sha256  # noqa: E402


class TheResumeRecordIsHonest(unittest.TestCase):
    def test_manifest_parses_and_every_artefact_matches_its_hash(self):
        manifest, problems = check_manifest(RECORD)
        self.assertIsNotNone(manifest, problems)
        self.assertEqual(problems, [], problems)
        self.assertGreater(len(manifest["artefacts"]), 0, "an empty artefact list proves nothing")

    def test_the_verdict_line_reads_partial(self):
        with open(os.path.join(RECORD, "MANIFEST.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertIn("verdict", manifest)
        self.assertTrue(manifest["verdict"].startswith("PARTIAL"),
                        manifest["verdict"])

    def test_the_seven_measures_are_all_present(self):
        with open(os.path.join(RECORD, "result.json"), encoding="utf-8") as fh:
            result = json.load(fh)
        measures = result["measures"]
        for key in ("repeated_work", "lost_decisions", "lost_evidence",
                    "wrong_resumed_state", "human_interventions",
                    "recovery_time_seconds", "false_claims_after_recovery"):
            self.assertIn(key, measures, "measure %r missing from result.json" % key)
        self.assertIn("drift_detection", result)

    def test_killed_run_captured_before_the_resume_could_overwrite_it(self):
        # Audit fix 1: the first version of this record had no killed_run/
        # at all, because the resumed process's own run.log write clobbers
        # the killed run's log in place. This is the regression test for
        # that gap: killed_run/run.log must exist and must NOT contain the
        # resume's own first line, which only the resumed process prints.
        killed_log = os.path.join(RECORD, "killed_run", "run.log")
        self.assertTrue(os.path.exists(killed_log), "killed_run/run.log is missing")
        with open(killed_log, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("an unfinished run already covers", text,
                         "killed_run/run.log carries the RESUME's own line: "
                         "it was captured too late, after being overwritten")

    def test_record_and_driver_log_and_capsule_are_present(self):
        for name in ("RECORD.md", "driver.log", "driver.py",
                    "continuity_capsule_after_kill.json",
                    "continuity_screen_after_kill.txt",
                    "continuity_capsule_after_resume.json",
                    "continuity_screen_after_resume.txt"):
            self.assertTrue(os.path.exists(os.path.join(RECORD, name)),
                            "%s missing from the resume record" % name)

    def test_no_temp_paths_or_hostname_leaked_into_the_frozen_files(self):
        forbidden = ("/private/tmp", "/Users/", "/private/var/folders",
                    "BAP-00048")
        hits = []
        for root, _dirs, files in os.walk(RECORD):
            for name in files:
                p = os.path.join(root, name)
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                for term in forbidden:
                    if term in text:
                        hits.append("%s contains %r" % (p, term))
        self.assertEqual(hits, [], hits)


class AResumeManifestMissingAnArtefactFails(unittest.TestCase):
    """The same backwards-drive proof test_lhr_checkpoint.py's own
    AManifestMissingAnArtefactFails class runs, exercised here against
    check_manifest directly (already proven generic there); kept as its own
    class per this file's own fixture rather than importing that one, since
    unittest does not re-run an imported TestCase by merely importing it."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="lhr-resume-record-selftest-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_missing_listed_artefact_is_reported_as_a_problem(self):
        present = os.path.join(self.tmp, "present.txt")
        with open(present, "w", encoding="utf-8") as fh:
            fh.write("real content\n")
        manifest = {
            "verdict": "PARTIAL: some reason",
            "artefacts": [
                {"path": "present.txt", "sha256": _sha256(present)},
                {"path": "gone.txt", "sha256": "0" * 64},
            ],
        }
        with open(os.path.join(self.tmp, "MANIFEST.json"), "w",
                 encoding="utf-8") as fh:
            json.dump(manifest, fh)

        loaded, problems = check_manifest(self.tmp)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("gone.txt", problems[0])

    def test_no_manifest_at_all_is_no_data_never_a_pass(self):
        loaded, problems = check_manifest(self.tmp)
        self.assertIsNone(loaded)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no MANIFEST.json", problems[0])


if __name__ == "__main__":
    unittest.main()
