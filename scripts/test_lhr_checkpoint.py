"""The Long-Horizon Recovery morning checkpoint: proven, not just written.

benchmarks/results/long-horizon-recovery/2026-09-05-checkpoint/MANIFEST.json
freezes a real killed run (crash during execution, workload family 7 of
benchmarks/gauntlets/long-horizon-recovery.json) because the gauntlet's
temporal arm (24-72 hour drift, family 8) cannot finish in one morning and
must not be faked. A manifest nobody drives backwards is a claim, not a
control (the same reasoning benchmarks/gauntlets/validate.py already applies
to the frozen specs), so this checks the real checkpoint AND proves the
check bites: a manifest missing one listed artefact must fail, not pass.
"""
import hashlib
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHECKPOINT = os.path.join(
    ROOT, "benchmarks", "results", "long-horizon-recovery",
    "2026-09-05-checkpoint")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_manifest(manifest_dir):
    """(ok, problems): loads MANIFEST.json in manifest_dir and verifies every
    listed artefact exists on disk with the listed sha256. Returns a list of
    every problem found, empty when the manifest is fully honest. Never
    raises on a missing file: a missing artefact is a PROBLEM to report, not
    a crash, mirroring benchmarks/gauntlets/validate.py's own style."""
    path = os.path.join(manifest_dir, "MANIFEST.json")
    if not os.path.exists(path):
        return None, ["no MANIFEST.json in %s" % manifest_dir]
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    problems = []
    for entry in manifest.get("artefacts", []):
        rel = entry.get("path")
        want = entry.get("sha256")
        full = os.path.join(manifest_dir, rel or "")
        if not rel or not os.path.exists(full):
            problems.append("listed artefact missing: %r" % rel)
            continue
        got = _sha256(full)
        if got != want:
            problems.append("hash mismatch for %s: manifest says %s, disk is %s"
                            % (rel, want, got))
    return manifest, problems


class TheRealCheckpointIsHonest(unittest.TestCase):
    def test_manifest_parses_and_every_artefact_matches_its_hash(self):
        manifest, problems = check_manifest(CHECKPOINT)
        self.assertIsNotNone(manifest, problems)
        self.assertEqual(problems, [], problems)
        self.assertGreater(len(manifest["artefacts"]), 0, "an empty artefact list proves nothing")

    def test_the_verdict_line_reads_partial(self):
        with open(os.path.join(CHECKPOINT, "MANIFEST.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertIn("verdict", manifest)
        self.assertTrue(manifest["verdict"].startswith("PARTIAL"),
                        manifest["verdict"])
        self.assertIn("PENDING TEMPORAL ARM", manifest["verdict"])

    def test_record_and_driver_log_and_capsule_are_present(self):
        for name in ("RECORD.md", "driver.log", "driver.py",
                    "continuity_capsule_after_kill.json",
                    "continuity_screen_after_kill.txt"):
            self.assertTrue(os.path.exists(os.path.join(CHECKPOINT, name)),
                            "%s missing from the checkpoint" % name)

    def test_no_temp_paths_or_hostname_leaked_into_the_frozen_files(self):
        # The same scrub check the freeze step itself ran: no absolute
        # scratch path or this machine's hostname should have survived
        # into the committed checkpoint.
        forbidden = ("/private/tmp", "/Users/", "BAP-00048")
        hits = []
        for root, _dirs, files in os.walk(CHECKPOINT):
            for name in files:
                p = os.path.join(root, name)
                try:
                    with open(p, encoding="utf-8") as fh:
                        text = fh.read()
                except UnicodeDecodeError:
                    continue
                for term in forbidden:
                    if term in text:
                        hits.append("%s contains %r" % (p, term))
        self.assertEqual(hits, [], hits)


class AManifestMissingAnArtefactFails(unittest.TestCase):
    """The backwards drive: a manifest is a claim, not a control, until
    something is shown refusing a broken one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lhr-checkpoint-selftest-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_missing_listed_artefact_is_reported_as_a_problem(self):
        present = os.path.join(self.tmp, "present.txt")
        with open(present, "w", encoding="utf-8") as fh:
            fh.write("real content\n")
        manifest = {
            "verdict": "PARTIAL, PENDING TEMPORAL ARM, resume not before later",
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

    def test_a_tampered_hash_is_reported_as_a_mismatch_not_a_pass(self):
        present = os.path.join(self.tmp, "present.txt")
        with open(present, "w", encoding="utf-8") as fh:
            fh.write("real content\n")
        manifest = {
            "verdict": "PARTIAL, PENDING TEMPORAL ARM, resume not before later",
            "artefacts": [
                {"path": "present.txt", "sha256": "f" * 64},
            ],
        }
        with open(os.path.join(self.tmp, "MANIFEST.json"), "w",
                 encoding="utf-8") as fh:
            json.dump(manifest, fh)

        loaded, problems = check_manifest(self.tmp)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("hash mismatch", problems[0])

    def test_no_manifest_at_all_is_no_data_never_a_pass(self):
        loaded, problems = check_manifest(self.tmp)
        self.assertIsNone(loaded)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("no MANIFEST.json", problems[0])


if __name__ == "__main__":
    unittest.main()
