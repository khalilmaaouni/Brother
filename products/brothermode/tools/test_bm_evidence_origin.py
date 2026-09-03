#!/usr/bin/env python3
"""TDD guard for evidence.origin (schema 22, tools/bm_store.py) and the
satisfaction invariant it exists to enforce: a synthetic or recovered
record can restore readable history and may NEVER satisfy a completion
claim on its own.

WHAT THIS SUITE IS ACTUALLY DEFENDING
  1. tools/bm_store.py carries ONE satisfaction function
     (origin_satisfies, built on EVIDENCE_ORIGINS/origin_class) that every
     reader consults rather than re-deriving the rule: EXECUTED and
     OBSERVED (and their pre-schema-22 aliases "local"/"ci") may satisfy;
     IMPORTED, RECOVERED, SYNTHETIC and INFERRED never do, on their own.
  2. A store history whose evidence carries a non-satisfying origin is
     reported by every reader this loop touches as a NAMED gap, never as
     an unqualified pass: tools/bm_passport.py's own
     whatWasNotEstablished names the offending origin(s) instead of
     falling back to its old "every gap category was found examined"
     justification, and tools/bm_cursor.py's adopt command prints an
     explicit INTERRUPTED / result NO-DATA / next-action line for a
     --skip-check (synthetic) adoption rather than only the bare
     "adopted" routing state.
  3. Removing the origin check is not invisible: a calibration test stubs
     origin_satisfies to always return True and proves the passport
     reader then WRONGLY promotes synthetic evidence to "no gap", so a
     later regression that quietly drops the check is caught by this
     suite going green when it should stay red.

Standard library only. Run: python3 tools/test_bm_evidence_origin.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PASSPORT_TOOL = os.path.join(HERE, "bm_passport.py")

sys.path.insert(0, HERE)
import bm_cursor as bc  # noqa: E402
import bm_passport as passport  # noqa: E402


def _load_bm_store_module():
    """Load bm_store.py fresh, the same technique test_bm_passport.py's
    own _load_bm_store_module uses, so a fixture can write real evidence
    rows through the Store API without shelling out to a CLI."""
    import importlib.util
    path = os.path.join(HERE, "bm_store.py")
    spec = importlib.util.spec_from_file_location(
        "bm_store_for_origin_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True,
                          check=True)


def make_repo(tmp):
    """A one commit repository at `tmp`. Returns (base, head), both the
    same commit: this suite never touches file content, only evidence."""
    with open(os.path.join(tmp, "a.txt"), "w") as fh:
        fh.write("one\n")
    _git(["init", "-q"], tmp)
    _git(["add", "-A"], tmp)
    _git(["-c", "user.email=t@example.com", "-c", "user.name=T",
         "commit", "-q", "-m", "one"], tmp)
    head = _git(["rev-parse", "HEAD"], tmp).stdout.strip()
    return head, head


def make_task_fixture(tmp, mod, project_id="proj-1"):
    """A real, healthy store at `tmp` carrying one project and one task,
    no evidence: callers add their own rows to control origin."""
    store = mod.Store(tmp, create=True)
    actor = {"actor_type": "human", "actor_name": "Fixture Person",
             "session_id": "sess-xyz"}
    try:
        store.upsert_project({
            "project_id": project_id, "name": "Fixture Project",
            "created_at": "2026-08-23T09:00:00Z",
            "updated_at": "2026-08-23T09:00:00Z",
        }, actor)
        task_id = store.create_task({
            "task_id": "task-1", "project_id": project_id,
            "title": "Do the thing", "status": "planned",
        }, actor)
    finally:
        store.close()
    return task_id, actor


class OriginVocabularyTests(unittest.TestCase):
    """The canonical vocabulary and satisfaction function, in isolation."""

    def setUp(self):
        self.bs = _load_bm_store_module()

    def test_eight_values_are_legal(self):
        self.assertEqual(
            set(self.bs.EVIDENCE_ORIGINS),
            {"local", "ci", "executed", "observed", "imported",
             "recovered", "synthetic", "inferred"})

    def test_local_and_ci_alias_to_executed(self):
        self.assertEqual(self.bs.origin_class("local"), "executed")
        self.assertEqual(self.bs.origin_class("ci"), "executed")
        self.assertEqual(self.bs.origin_class("executed"), "executed")

    def test_only_executed_and_observed_satisfy(self):
        satisfying = {o for o in self.bs.EVIDENCE_ORIGINS
                      if self.bs.origin_satisfies(o)}
        self.assertEqual(satisfying,
                         {"local", "ci", "executed", "observed"})

    def test_imported_recovered_synthetic_inferred_never_satisfy(self):
        for origin in ("imported", "recovered", "synthetic", "inferred"):
            self.assertFalse(
                self.bs.origin_satisfies(origin),
                "%r wrongly satisfies" % origin)


class StoreWriteTests(unittest.TestCase):
    """add_evidence's own validation and default, against a real store."""

    def test_fresh_store_defaults_origin_to_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = _load_bm_store_module()
            task_id, actor = make_task_fixture(tmp, mod)
            store = mod.Store(tmp, create=False)
            try:
                store.add_evidence({
                    "evidence_id": "ev-1", "subject_type": "task",
                    "subject_id": task_id, "kind": "test",
                    "ref": "python3 tools/test_all.py", "note": "green",
                    "created_at": "2026-08-23T09:30:00Z",
                }, "proj-1", actor)
                rows = store.list_evidence("task", task_id, raw=True)
            finally:
                store.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["origin"], "local")
            self.assertTrue(mod.origin_satisfies(rows[0]["origin"]))

    def test_add_evidence_rejects_unknown_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = _load_bm_store_module()
            task_id, actor = make_task_fixture(tmp, mod)
            store = mod.Store(tmp, create=False)
            try:
                with self.assertRaises(mod.OwnershipRefused) as ctx:
                    store.add_evidence({
                        "evidence_id": "ev-1", "subject_type": "task",
                        "subject_id": task_id, "kind": "test",
                        "origin": "made-up",
                        "created_at": "2026-08-23T09:30:00Z",
                    }, "proj-1", actor)
                self.assertEqual(ctx.exception.reason, "bad-origin")
            finally:
                store.close()

    def test_add_evidence_stores_explicit_synthetic_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod = _load_bm_store_module()
            task_id, actor = make_task_fixture(tmp, mod)
            store = mod.Store(tmp, create=False)
            try:
                store.add_evidence({
                    "evidence_id": "ev-1", "subject_type": "task",
                    "subject_id": task_id, "kind": "test",
                    "ref": "manufactured", "note": "stands in",
                    "origin": "synthetic",
                    "created_at": "2026-08-23T09:30:00Z",
                }, "proj-1", actor)
                rows = store.list_evidence("task", task_id, raw=True)
            finally:
                store.close()
            self.assertEqual(rows[0]["origin"], "synthetic")
            self.assertFalse(mod.origin_satisfies(rows[0]["origin"]))


class PassportOriginIntegrationTests(unittest.TestCase):
    """tools/bm_passport.py, the one genuine existing reader of evidence
    origin, called in-process (not via subprocess) so the calibration
    test below can stub its store loader."""

    def _passport_for(self, tmp, base, head, project_id="proj-1"):
        result, err = passport.build_passport(
            tmp, project_id, base, head, "Fixture Person", None,
            "2026-08-23T10:00:00Z")
        self.assertIsNone(err, err)
        return result

    def test_synthetic_and_recovered_evidence_are_named_as_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            mod = _load_bm_store_module()
            task_id, actor = make_task_fixture(tmp, mod)
            store = mod.Store(tmp, create=False)
            try:
                store.add_evidence({
                    "evidence_id": "ev-synthetic", "subject_type": "task",
                    "subject_id": task_id, "kind": "test",
                    "ref": "manufactured", "note": "stands in for a run",
                    "origin": "synthetic",
                    "created_at": "2026-08-23T09:30:00Z",
                }, "proj-1", actor)
                store.add_evidence({
                    "evidence_id": "ev-recovered", "subject_type": "task",
                    "subject_id": task_id, "kind": "test",
                    "ref": "rebuilt from a log", "note": "history restored",
                    "origin": "recovered",
                    "created_at": "2026-08-23T09:31:00Z",
                }, "proj-1", actor)
            finally:
                store.close()

            doc = self._passport_for(tmp, base, head)
            gap_text = " ".join(doc["whatWasNotEstablished"])
            self.assertIn("synthetic", gap_text)
            self.assertIn("recovered", gap_text)
            # INTERRUPTED / NO-DATA in spirit: the passport must never
            # report this history as cleanly examined with nothing left
            # to establish.
            self.assertNotEqual(
                doc["whatWasNotEstablished"],
                ["every default gap category was found examined in "
                 "the store's own task and evidence text, and no "
                 "other gap was detected by this generator"])

    def test_executed_only_evidence_names_no_origin_gap(self):
        """Sanity: a satisfying origin must not produce a false alarm."""
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            mod = _load_bm_store_module()
            task_id, actor = make_task_fixture(tmp, mod)
            store = mod.Store(tmp, create=False)
            try:
                store.add_evidence({
                    "evidence_id": "ev-1", "subject_type": "task",
                    "subject_id": task_id, "kind": "test",
                    "ref": "python3 tools/test_all.py", "note": "green",
                    "origin": "executed",
                    "created_at": "2026-08-23T09:30:00Z",
                }, "proj-1", actor)
            finally:
                store.close()

            doc = self._passport_for(tmp, base, head)
            gap_text = " ".join(doc["whatWasNotEstablished"])
            self.assertNotIn("cannot satisfy a completion claim", gap_text)

    def test_calibration_stubbing_satisfies_wrongly_promotes(self):
        """Prove the suite actually depends on origin_satisfies: stub it
        to always say yes and watch the same synthetic-evidence fixture
        that named a gap above go quiet instead."""
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            mod = _load_bm_store_module()
            task_id, actor = make_task_fixture(tmp, mod)
            store = mod.Store(tmp, create=False)
            try:
                store.add_evidence({
                    "evidence_id": "ev-synthetic", "subject_type": "task",
                    "subject_id": task_id, "kind": "test",
                    "ref": "manufactured", "note": "stands in for a run",
                    "origin": "synthetic",
                    "created_at": "2026-08-23T09:30:00Z",
                }, "proj-1", actor)
            finally:
                store.close()

            real_loader = passport._load_bm_store

            def _stubbed_loader():
                stub_mod, err = real_loader()
                if stub_mod is not None:
                    # Ignore origin entirely: everything satisfies. This is
                    # exactly the regression this calibration test exists
                    # to catch if a later change makes it real.
                    stub_mod.origin_satisfies = lambda origin: True
                return stub_mod, err

            passport._load_bm_store = _stubbed_loader
            try:
                doc = self._passport_for(tmp, base, head)
            finally:
                passport._load_bm_store = real_loader

            gap_text = " ".join(doc["whatWasNotEstablished"])
            self.assertNotIn(
                "synthetic", gap_text,
                "with origin_satisfies stubbed to always True, the "
                "synthetic-evidence gap must disappear -- if it is still "
                "here, the reader is not actually calling the stub, which "
                "means it was not really calling the real function either")


class CursorAdoptOriginTests(unittest.TestCase):
    """tools/bm_cursor.py's adopt command: the writer that mints a
    verdict from exit_code==0, and the one place --skip-check lets a
    packet leave outbox with no check having actually run."""

    def _run(self, argv, cwd):
        return subprocess.run(
            [sys.executable] + list(argv), cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=120)

    def _dispatch_claim_return(self, tmp, tool):
        d = self._run(
            [tool, "dispatch", "--objective", "touch marker",
             "--write-scope", "marker.txt",
             "--done-check", "test -f marker.txt",
             "--actor", "fable", "--project", tmp, "--json"], tmp)
        self.assertEqual(d.returncode, 0, d.stderr)
        pid = json.loads(d.stdout)["packet_id"]

        c = self._run(
            [tool, "claim-next", "--project", tmp, "--actor", "cursor",
             "--json"], tmp)
        self.assertEqual(c.returncode, 0, c.stderr)

        r = self._run(
            [tool, "record-result", "--packet-id", pid,
             "--worker-claim", "wrote marker", "--project", tmp,
             "--json"], tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return pid

    def test_skip_check_adoption_carries_synthetic_origin_and_interrupts(self):
        bs = _load_bm_store_module()
        tool = os.path.join(HERE, "bm_cursor.py")
        with tempfile.TemporaryDirectory() as tmp:
            pid = self._dispatch_claim_return(tmp, tool)

            a_json = self._run(
                [tool, "adopt", "--packet-id", pid, "--project", tmp,
                 "--skip-check", "--json"], tmp)
            packet = json.loads(a_json.stdout)
            self.assertEqual(packet["adoption"]["origin"], "synthetic")
            self.assertFalse(bs.origin_satisfies(
                packet["adoption"]["origin"]))

            # A second packet, same story, but human-readable output: the
            # invariant is that this never reads as a plain pass.
            pid2 = self._dispatch_claim_return(tmp, tool)
            a_text = self._run(
                [tool, "adopt", "--packet-id", pid2, "--project", tmp,
                 "--skip-check"], tmp)
            self.assertEqual(a_text.returncode, 0, a_text.stderr)
            self.assertIn("INTERRUPTED", a_text.stdout)
            self.assertIn("NO-DATA", a_text.stdout)
            self.assertIn("next action", a_text.stdout)
            self.assertNotIn("passed", a_text.stdout)

    def test_real_check_adoption_carries_executed_origin_no_interrupt(self):
        bs = _load_bm_store_module()
        tool = os.path.join(HERE, "bm_cursor.py")
        with tempfile.TemporaryDirectory() as tmp:
            pid = self._dispatch_claim_return(tmp, tool)
            with open(os.path.join(tmp, "marker.txt"), "w") as fh:
                fh.write("ok\n")

            a_json = self._run(
                [tool, "adopt", "--packet-id", pid, "--project", tmp,
                 "--json"], tmp)
            packet = json.loads(a_json.stdout)
            self.assertEqual(packet["adoption"]["origin"], "executed")
            self.assertTrue(bs.origin_satisfies(
                packet["adoption"]["origin"]))

            a_text = self._run(
                [tool, "adopt", "--packet-id",
                 self._dispatch_claim_return(tmp, tool), "--project", tmp],
                tmp)
            # The second packet's marker already exists from the first
            # adoption's own worktree (project root, no isolation here),
            # so this run also passes; either way INTERRUPTED must not
            # appear for a check that actually ran.
            self.assertNotIn("INTERRUPTED", a_text.stdout)


if __name__ == "__main__":
    unittest.main()
