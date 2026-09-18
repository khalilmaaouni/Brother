#!/usr/bin/env python3
"""Tests for scripts/orchestrator_resume.py (ORCH-08).

Fixtures are built with the REAL producers wherever one exists
(claim_store.acquire, journal.append via it, orchestrator_authority.acquire,
a real tiny git repository for canonical_revision) rather than hand-typed
JSON that could drift from what those modules actually write. The Work
document and orchestrator_control.json have no producer of their own yet in
this build, so they are written directly, matching the shapes documented in
orchestrator_resume.py's own module docstring.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import claim_store  # noqa: E402
import orchestrator_authority  # noqa: E402
import orchestrator_resume as resume  # noqa: E402


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _write_work_doc(run_dir, outcome, rows, name="work.json"):
    _write_json(os.path.join(run_dir, name), {"outcome": outcome, "rows": rows})


def _write_control(run_dir, **overrides):
    doc = dict(resume._CONTROL_DEFAULTS)
    doc.update(overrides)
    path = os.path.join(run_dir, resume.CONTROL_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _write_json(path, doc)


def _init_repo(root):
    """A real one-commit git repository, so canonical_revision is a real
    sha this test can independently capture and compare against, never a
    value re-read from the module under test."""
    repo = os.path.join(root, "repo")
    os.makedirs(repo)
    for args in (["init", "-q"],
                 ["config", "user.email", "test@example.com"],
                 ["config", "user.name", "Test"]):
        subprocess.run(["git"] + args, cwd=repo, check=True,
                        capture_output=True, text=True)
    with open(os.path.join(repo, "f.txt"), "w", encoding="utf-8") as fh:
        fh.write("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True,
                    capture_output=True, text=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True,
                    capture_output=True, text=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()
    return repo, sha


def _minimal_fixture(tmp, run_name="run-min"):
    """A run_dir with one claimed-and-live unit, a work document, and a
    control file -- everything build_capsule() needs to succeed cleanly.
    Individual tests then delete or corrupt one file at a time."""
    run_dir = os.path.join(tmp, run_name)
    os.makedirs(run_dir)
    _write_work_doc(run_dir, "a minimal goal",
                     [{"id": "UX", "title": "unit x", "status": "RUNNING",
                       "done_check": "true"}])
    claim, problem = claim_store.acquire(
        os.path.join(run_dir, "claims.json"), "UX", "worker-x",
        ttl=600, clock=lambda: 1000.0, attempt=1)
    assert problem == "", problem
    _write_control(run_dir)
    return run_dir


class CompleteFixtureTest(unittest.TestCase):
    """Requirement 1: every fact named against an independent literal."""

    def test_complete_fixture_names_every_fact(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "run-alpha")
            os.makedirs(run_dir)
            repo, sha = _init_repo(tmp)
            _write_json(os.path.join(run_dir, "target.json"), {"cwd": repo})
            _write_work_doc(run_dir, "ship the thing", [
                {"id": "U1", "title": "unit one", "status": "DONE",
                 "done_check": "true"},
                {"id": "U2", "title": "unit two", "status": "RUNNING",
                 "done_check": "true", "owns": ["a"]},
            ])
            now = 100000.0
            claim, problem = claim_store.acquire(
                os.path.join(run_dir, "claims.json"), "U2", "worker-x",
                ttl=600, clock=lambda: now, attempt=1)
            self.assertEqual(problem, "", problem)
            _write_control(
                run_dir,
                hard_stop_jst="05:30",
                required_gates=["required_fast.sh"],
                resource_capacity={"builder_cap": 3, "in_use": 1},
                ready_now=["U3"],
                awaiting_review=["U4"],
                red_queue=[{"task_id": "U5", "reason": "irreversible"}],
                amber_rulings=[{"task_id": "U6", "ruling": "proceed"}],
            )
            lease = orchestrator_authority.acquire(
                os.path.join(run_dir, resume.AUTHORITY_FILE), "run-alpha",
                resume.AUTHORITY_SCOPE, "fable", "instance-1", 600, now=now)
            self.assertEqual(lease.epoch, 1)

            cap = resume.build_capsule(run_dir, "run-alpha", "fable",
                                        "instance-1", now=now)

            self.assertEqual(cap["unreadable"], [])
            self.assertEqual(cap["run_id"], "run-alpha")
            self.assertEqual(cap["goal"], "ship the thing")
            self.assertEqual(cap["hard_stop"], "05:30")
            self.assertEqual(cap["canonical_revision"], sha)
            self.assertEqual(cap["required_gates"], ["required_fast.sh"])
            self.assertEqual(cap["resource_capacity"],
                              {"builder_cap": 3, "in_use": 1})
            self.assertEqual(cap["ready_now"], ["U3"])
            self.assertEqual(cap["awaiting_review"], ["U4"])
            self.assertEqual(cap["red_queue"],
                              [{"task_id": "U5", "reason": "irreversible"}])
            self.assertEqual(cap["amber_rulings"],
                              [{"task_id": "U6", "ruling": "proceed"}])
            self.assertEqual(cap["attempt_counts"].get("U2"), 1)
            self.assertEqual([u["id"] for u in cap["active_claims"]], ["U2"])
            self.assertEqual(cap["active_lanes"], ["worker-x"])
            self.assertEqual(cap["failed_units"], [])
            self.assertTrue(cap["my_authority"]["held"])
            self.assertEqual(cap["my_authority"]["epoch"], 1)
            self.assertEqual(cap["my_authority"]["instance"], "instance-1")
            self.assertEqual(cap["last_journal_seq"], 1)
            self.assertIn("digest", cap)


class KillAndRestartTest(unittest.TestCase):
    """Requirement 2, the headline case: a fresh instance, no other input,
    must correctly name what was in flight when the old one died."""

    def test_restart_with_new_instance_sees_the_in_flight_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "run-beta")
            os.makedirs(run_dir)
            repo, _sha = _init_repo(tmp)
            _write_json(os.path.join(run_dir, "target.json"), {"cwd": repo})
            _write_work_doc(run_dir, "finish the killed unit", [
                {"id": "U9", "title": "killed unit", "status": "RUNNING",
                 "done_check": "true"},
            ])
            _write_control(run_dir)
            acquire_time = 1000.0
            claim_store.acquire(
                os.path.join(run_dir, "claims.json"), "U9", "worker-old",
                ttl=10, clock=lambda: acquire_time, attempt=1)
            orchestrator_authority.acquire(
                os.path.join(run_dir, resume.AUTHORITY_FILE), "run-beta",
                resume.AUTHORITY_SCOPE, "fable", "instance-old", 10,
                now=acquire_time)

            # The old orchestrator died. A NEW instance restarts, long
            # after both leases expired, with no input besides run_dir,
            # run_id and its own new identity.
            restart_now = acquire_time + 10000
            cap = resume.build_capsule(run_dir, "run-beta", "fable",
                                        "instance-new", now=restart_now)

            self.assertEqual(cap["unreadable"], [])
            failed_ids = {f["id"] for f in cap["failed_units"]}
            self.assertIn("U9", failed_ids)
            self.assertEqual(cap["attempt_counts"].get("U9"), 1)
            self.assertEqual(cap["active_claims"], [])
            self.assertEqual(cap["active_lanes"], [])
            self.assertFalse(cap["my_authority"]["held"])


class UnreadableSourcesTest(unittest.TestCase):
    """Requirement 3: missing or malformed claim store, journal, and work
    document each produce an `unreadable` entry AND a None field, never an
    empty default. Requirement 4: none of this raises."""

    def _build(self, run_dir, now=2000.0):
        return resume.build_capsule(run_dir, "run-min", "fable",
                                     "instance-1", now=now)

    def test_claims_missing_then_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _minimal_fixture(tmp)
            claims_path = os.path.join(run_dir, "claims.json")

            os.remove(claims_path)
            cap = self._build(run_dir)
            self.assertIsNone(cap["active_claims"])
            self.assertIsNone(cap["active_lanes"])
            self.assertTrue(any("claims" in u for u in cap["unreadable"]),
                             cap["unreadable"])

            with open(claims_path, "w", encoding="utf-8") as fh:
                fh.write("{not valid json")
            cap2 = self._build(run_dir)
            self.assertIsNone(cap2["active_claims"])
            self.assertIsNone(cap2["active_lanes"])
            self.assertTrue(any("claims" in u for u in cap2["unreadable"]),
                             cap2["unreadable"])

    def test_journal_missing_then_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _minimal_fixture(tmp)
            journal_path = os.path.join(run_dir, "journal.jsonl")

            os.remove(journal_path)
            cap = self._build(run_dir)
            self.assertIsNone(cap["last_journal_seq"])
            self.assertTrue(any("journal" in u for u in cap["unreadable"]),
                             cap["unreadable"])
            # even a totally missing journal must not raise, and run_id
            # still falls back to the run directory's own name.
            self.assertEqual(cap["run_id"], os.path.basename(run_dir))

            with open(journal_path, "w", encoding="utf-8") as fh:
                fh.write("this is not json\n")
            cap2 = self._build(run_dir)
            self.assertIsNone(cap2["last_journal_seq"])
            self.assertTrue(any("journal" in u for u in cap2["unreadable"]),
                             cap2["unreadable"])

    def test_work_document_missing_then_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _minimal_fixture(tmp)
            doc_path = os.path.join(run_dir, "work.json")

            os.remove(doc_path)
            cap = self._build(run_dir)
            self.assertIsNone(cap["goal"])
            self.assertIsNone(cap["work_graph_summary"])
            self.assertIsNone(cap["attempt_counts"])
            self.assertIsNone(cap["failed_units"])
            self.assertTrue(
                any("work_document" in u for u in cap["unreadable"]),
                cap["unreadable"])

            with open(doc_path, "w", encoding="utf-8") as fh:
                fh.write("{not valid json")
            cap2 = self._build(run_dir)
            self.assertIsNone(cap2["goal"])
            self.assertIsNone(cap2["work_graph_summary"])
            self.assertIsNone(cap2["attempt_counts"])
            self.assertIsNone(cap2["failed_units"])
            self.assertTrue(
                any("work_document" in u for u in cap2["unreadable"]),
                cap2["unreadable"])

    def test_multiple_unreadable_sources_at_once_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _minimal_fixture(tmp)
            os.remove(os.path.join(run_dir, "claims.json"))
            os.remove(os.path.join(run_dir, "journal.jsonl"))
            os.remove(os.path.join(run_dir, "work.json"))
            cap = self._build(run_dir)  # must not raise
            self.assertGreaterEqual(len(cap["unreadable"]), 3)
            self.assertIn("digest", cap)
            self.assertIsInstance(cap["digest"], str)


class NoLeaseTest(unittest.TestCase):
    """Requirement 7: an instance holding no lease is told so plainly,
    never handed someone else's lease as its own."""

    def test_no_authority_store_at_all_reads_as_not_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _minimal_fixture(tmp)
            cap = resume.build_capsule(run_dir, "run-min", "fable",
                                        "instance-1", now=2000.0)
            self.assertIsNotNone(cap["my_authority"])
            self.assertFalse(cap["my_authority"]["held"])
            self.assertFalse(
                any("authority" in u for u in cap["unreadable"]),
                cap["unreadable"])

    def test_another_instances_live_lease_is_never_reported_as_mine(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _minimal_fixture(tmp)
            now = 2000.0
            orchestrator_authority.acquire(
                os.path.join(run_dir, resume.AUTHORITY_FILE), "run-min",
                resume.AUTHORITY_SCOPE, "fable", "the-other-instance", 600,
                now=now)
            cap = resume.build_capsule(run_dir, "run-min", "fable",
                                        "instance-1", now=now)
            self.assertFalse(cap["my_authority"]["held"])
            self.assertEqual(cap["my_authority"]["held_by"],
                              "the-other-instance")


class DigestTest(unittest.TestCase):
    """Requirement 5: stable for identical content, changes on one field."""

    def test_digest_stable_for_identical_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _minimal_fixture(tmp)
            now = 5000.0
            cap1 = resume.build_capsule(run_dir, "run-min", "fable",
                                         "instance-1", now=now)
            cap2 = resume.build_capsule(run_dir, "run-min", "fable",
                                         "instance-1", now=now)
            self.assertEqual(cap1["digest"], cap2["digest"])

    def test_digest_changes_on_one_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _minimal_fixture(tmp)
            now = 5000.0
            cap1 = resume.build_capsule(run_dir, "run-min", "fable",
                                         "instance-1", now=now)
            control_path = os.path.join(run_dir, resume.CONTROL_FILE)
            with open(control_path, encoding="utf-8") as fh:
                doc = json.load(fh)
            doc["red_queue"] = [{"task_id": "ZZ", "reason": "changed"}]
            _write_json(control_path, doc)
            cap2 = resume.build_capsule(run_dir, "run-min", "fable",
                                         "instance-1", now=now)
            self.assertNotEqual(cap1["digest"], cap2["digest"])

    def test_capsule_digest_excludes_generated_at_and_digest_itself(self):
        cap_a = {"a": 1, "generated_at": 111.0, "digest": "stale"}
        cap_b = {"a": 1, "generated_at": 999.0, "digest": "different-stale"}
        self.assertEqual(resume.capsule_digest(cap_a),
                          resume.capsule_digest(cap_b))
        cap_c = {"a": 2, "generated_at": 111.0, "digest": "stale"}
        self.assertNotEqual(resume.capsule_digest(cap_a),
                             resume.capsule_digest(cap_c))


class RenderBoundedTest(unittest.TestCase):
    """Requirement 6: render() is bounded and names the omitted count."""

    def test_render_caps_a_long_list_and_names_what_it_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _minimal_fixture(tmp)
            ready = ["R%02d" % i for i in range(30)]
            _write_control(run_dir, ready_now=ready)
            cap = resume.build_capsule(run_dir, "run-min", "fable",
                                        "instance-1", now=9000.0)
            text = resume.render(cap)
            self.assertIn("READY NOW (30):", text)
            self.assertIn("... and %d more omitted"
                          % (30 - resume.RENDER_LIMIT), text)
            shown_lines = [line for line in text.splitlines()
                           if line.strip().startswith("- R")]
            self.assertLessEqual(len(shown_lines), resume.RENDER_LIMIT)

    def test_render_shows_unreadable_prominently(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = _minimal_fixture(tmp)
            os.remove(os.path.join(run_dir, "claims.json"))
            cap = resume.build_capsule(run_dir, "run-min", "fable",
                                        "instance-1", now=9000.0)
            text = resume.render(cap)
            self.assertIn("UNREADABLE (", text)
            first_unreadable_line = next(
                i for i, line in enumerate(text.splitlines())
                if line.startswith("UNREADABLE"))
            active_claims_line = next(
                i for i, line in enumerate(text.splitlines())
                if line.startswith("ACTIVE CLAIMS"))
            self.assertLess(first_unreadable_line, active_claims_line)


if __name__ == "__main__":
    unittest.main()
