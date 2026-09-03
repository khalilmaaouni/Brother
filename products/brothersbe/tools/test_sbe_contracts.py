#!/usr/bin/env python3
"""LP-0201 and BAND K: fixtures for `brothersbe.contracts`, the versioned
JSON contract registry. Two halves, and they are proved differently. Run:
python3 tools/test_sbe_contracts.py

LP-0201, the five surfaces that already exist (the task registry, `sbe status
--json`, `sbe status --team --json`, the work brief, the handover record):
one fixture per surface validates REAL output, captured live through the
real engine over the golden scenario `tools/fixtures/golden-scenario/
build_scenario.py` builds (never a hand-typed dict standing in for what a
command would print): a real `git init`, the golden scenario's own dossier
and plan, then `sbe work start`/`sbe evidence run`/`sbe work finish` for T01,
`sbe work brief` for T02 (still unclaimed), `sbe status`, `sbe status
--team` and `sbe handover prepare`, the same driving discipline `tools/
test_sbe_golden_scenario.py`'s own `GoldenScenarioFixture` already holds to,
mirrored here rather than reinvented.

Every refusal fixture (`TestRefusals` below) starts from one of those SAME
real captures, `copy.deepcopy`'d so the original is never mutated, then
changes exactly one thing (an unknown `schemaVersion`, a field popped out,
an extra field added) and asserts the validator moves the way that one
change should move it. Calibration for each: the original, un-mutated copy
is re-validated as `PASS` right after the mutated copy is checked, in the
same test, which is how this suite proves the failure a mutated copy earns
comes from the mutation and not from a validator that always says `FAIL`
(and, since every mutation lives on an in-memory `copy.deepcopy`, no file
this repository tracks is ever touched, so there is nothing on disk for a
`git diff` to catch: the calibration is the re-validated original passing
inside the same test).

BAND K, the six lifecycle objects below the Change Passport seam (the
passport, the decision packet, the human decision, the release receipt, the
observation contract, the verified-reality state): fixtures are HAND BUILT,
because no producer for any of them exists in this repository yet. That is
the weaker fixture and it is labelled as such where it starts, halfway down
this file; it proves the validators refuse what they must refuse, and it
proves nothing about what a future producer will write. The calibration rule
above binds both halves.
"""
import copy
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")
FIXTURE_DIR = os.path.join(HERE, "fixtures", "golden-scenario")
if FIXTURE_DIR not in sys.path:
    sys.path.insert(0, FIXTURE_DIR)
import build_scenario as bs  # noqa: E402  (path setup has to come first)

sys.path.insert(0, os.path.join(ROOT, "src"))
try:
    from brothersbe import contracts as mod  # noqa: E402
finally:
    sys.path.pop(0)

OUTGOING = "alice@example.com"
RECEIVER = "bob@example.com"


# ---------------------------------------------------------------------------
# Shared fixture: one golden scenario repository, driven far enough to hand
# back one real captured document per surface. Mirrors `tools/
# test_sbe_golden_scenario.py`'s own `GoldenScenarioFixture` (the process
# helpers, the registry reader) rather than reinventing either.
# ---------------------------------------------------------------------------

class ContractsFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-contracts-")
        self.repo = os.path.join(self.tmp, "repo")
        self.worktree_dir = os.path.join(self.tmp, "worktrees")
        os.makedirs(self.worktree_dir)
        built = bs.build(self.repo, SBE)
        self.assertEqual(built["validate_code"], 0,
                         "fixture setup wrote a plan `sbe plan` itself refuses; this is a "
                         "fixture bug, not an LP-0201 finding: %s" % built["validate_text"])
        self.base = built["base"]
        self.dossier = built["dossier"]
        self.plan_path = built["plan_path"]
        self.change_id = built["change_id"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- process helpers. Three values, not two: see `build_scenario.
    # run_sbe`'s own docstring for why every runner helper in this project
    # returns three values rather than a (verdict, evidence)-shaped pair.

    def sbe(self, *argv):
        return bs.run_sbe(SBE, list(argv), cwd=self.repo)

    def work(self, *argv):
        return self.sbe("work", *argv, "--cwd", self.repo)

    def worktree_path_for(self, task_id):
        return os.path.join(self.worktree_dir,
                            "%s-sbe-%s" % (os.path.basename(self.repo), task_id))

    def start_task(self, task_id, agent):
        code, text, _err = self.work("start", task_id, "--plan", self.plan_path,
                                     "--worktree-dir", self.worktree_dir, "--agent", agent)
        self.assertEqual(code, 0, "sbe work start %s failed: %s" % (task_id, text))
        return self.worktree_path_for(task_id)

    def run_evidence(self, task_id, argv, covers=None, out_name=None, kind="gate"):
        worktree = self.worktree_path_for(task_id)
        receipt = os.path.join(self.repo, ".sbe", "evidence",
                               out_name or ("%s-receipt.json" % task_id))
        extra = []
        for c in (covers or []):
            extra += ["--covers", c]
        code, text, _err = self.sbe("evidence", "run", "--out", receipt, "--kind", kind,
                                    *(extra + ["--cwd", worktree, "--"] + list(argv)))
        self.assertEqual(code, 0, "sbe evidence run for %s failed: %s" % (task_id, text))
        return receipt

    def finish_task(self, task_id):
        return self.work("finish", task_id)

    def registry(self):
        with io.open(os.path.join(self.repo, ".sbe", "tasks.json"), encoding="utf-8") as fh:
            return json.load(fh)


# ---------------------------------------------------------------------------
# One real, live capture per surface, driven through the real engine, each
# validated PASS. This IS the "one fixture per surface validating REAL
# output" the done-check names.
# ---------------------------------------------------------------------------

class TestRealFixturesValidate(ContractsFixture):
    def setUp(self):
        ContractsFixture.setUp(self)

        # T01: start, do the work, evidence it, finish clean. A populated,
        # closed task record is a far more real task registry fixture than
        # the empty one `build()` alone would leave behind.
        wt01 = self.start_task("T01", "alpha")
        bs.write(wt01, bs.BACKEND_PATH,
                "def lookup(widget_id, catalog):\n    return catalog.get(widget_id)\n")
        bs.git(wt01, "add", "-A")
        bs.git(wt01, "commit", "-qm", "backend: add the lookup service")
        self.run_evidence("T01", bs.BACKEND_VERIFY_ARGV)
        code, text, _err = self.finish_task("T01")
        self.assertEqual(code, 0, "sbe work finish T01 failed: %s" % text)

        self.registry_data = self.registry()

        code, text, _err = self.sbe("status", self.repo, "--json")
        self.assertIn(code, (0, 1), text)
        self.status_data = json.loads(text)

        code, text, _err = self.sbe("status", self.repo, "--team", "--json")
        self.assertIn(code, (0, 1), text)
        self.status_team_data = json.loads(text)

        # T02: still unclaimed (only T01 was started above), so `sbe work
        # brief` may write one for it (rule 4 in `work.cmd_brief` refuses a
        # brief for a task an OPEN registry record already owns).
        code, text, _err = self.work("brief", "--plan", self.plan_path, "--task", "T02",
                                     "--json")
        self.assertEqual(code, 0, "sbe work brief T02 failed: %s" % text)
        self.brief_data = json.loads(text)

        code, text, _err = self.sbe("handover", "prepare", self.dossier,
                                    "--outgoing", OUTGOING, "--receiver", RECEIVER)
        self.assertEqual(code, 0, "sbe handover prepare failed: %s" % text)
        with io.open(os.path.join(self.dossier, "12-handover.json"), encoding="utf-8") as fh:
            self.handover_data = json.load(fh)

    def test_task_registry_from_a_real_run_validates_pass(self):
        verdict, evidence, problems = mod.validate_task_registry(self.registry_data)
        self.assertEqual(verdict, "PASS", (evidence, problems, self.registry_data))
        self.assertEqual(problems, ())
        # Grounds the fixture: T01 really is in there, closed, so this is
        # not an empty registry accidentally validating for free.
        ids = [t["id"] for t in self.registry_data["tasks"]]
        self.assertIn("T01", ids, self.registry_data)

    def test_status_json_from_a_real_run_validates_pass(self):
        verdict, evidence, problems = mod.validate_status(self.status_data)
        self.assertEqual(verdict, "PASS", (evidence, problems, self.status_data))
        self.assertEqual(problems, ())

    def test_status_team_json_from_a_real_run_validates_pass(self):
        verdict, evidence, problems = mod.validate_status_team(self.status_team_data)
        self.assertEqual(verdict, "PASS", (evidence, problems, self.status_team_data))
        self.assertEqual(problems, ())
        # The real producer carries no schemaVersion as of 1.0.0-rc.16; this
        # fixture proves that reading, not an assumption:
        self.assertNotIn("schemaVersion", self.status_team_data, self.status_team_data)
        # The absent-version PASS evidence names the absence exactly once, in
        # its own clause: proves the branch the mutated-copy test below does
        # NOT exercise, so both halves of validate_status_team's evidence
        # sentence are pinned by a real fixture.
        self.assertIn("no schemaVersion field", evidence, evidence)

    def test_status_team_evidence_names_a_present_schema_version_without_contradicting_itself(self):
        # Regression: a copy of the SAME real capture above, with a
        # schemaVersion this registry recognizes ADDED, must still validate
        # PASS, and its evidence line must NAME that version rather than
        # asserting (in the same sentence) that no such field exists. Before
        # the fix, the note string hard-coded "no schemaVersion field ..." and
        # merely prefixed the version onto it, so a PASS with a version
        # present read: "schemaVersion '1.0', ... no schemaVersion field (not
        # yet emitted by this surface, accepted by name)": true and false in
        # one line.
        mutated = copy.deepcopy(self.status_team_data)
        mutated["schemaVersion"] = mod.STATUS_TEAM_KNOWN_SCHEMA_VERSIONS[0]
        verdict, evidence, problems = mod.validate_status_team(mutated)
        self.assertEqual(verdict, "PASS", (evidence, problems, mutated))
        self.assertEqual(problems, ())
        self.assertIn("schemaVersion %r" % mod.STATUS_TEAM_KNOWN_SCHEMA_VERSIONS[0], evidence,
                      evidence)
        self.assertNotIn("no schemaVersion field", evidence, evidence)
        # Calibration companion, in the same test: the untouched original
        # (no schemaVersion) still reads the other, correct way.
        _v, original_evidence, _p = mod.validate_status_team(self.status_team_data)
        self.assertIn("no schemaVersion field", original_evidence, original_evidence)

    def test_work_brief_json_from_a_real_run_validates_pass(self):
        verdict, evidence, problems = mod.validate_work_brief(self.brief_data)
        self.assertEqual(verdict, "PASS", (evidence, problems, self.brief_data))
        self.assertEqual(problems, ())
        self.assertEqual(self.brief_data["taskId"], "T02", self.brief_data)

    def test_handover_record_from_a_real_run_validates_pass(self):
        verdict, evidence, problems = mod.validate_handover(self.handover_data)
        self.assertEqual(verdict, "PASS", (evidence, problems, self.handover_data))
        self.assertEqual(problems, ())
        self.assertEqual(self.handover_data["status"], "prepared", self.handover_data)

    def test_the_generic_dispatcher_agrees_with_every_named_function(self):
        pairs = (
            ("task-registry", self.registry_data, mod.validate_task_registry),
            ("status", self.status_data, mod.validate_status),
            ("status-team", self.status_team_data, mod.validate_status_team),
            ("work-brief", self.brief_data, mod.validate_work_brief),
            ("handover", self.handover_data, mod.validate_handover),
        )
        for surface, data, fn in pairs:
            self.assertEqual(mod.validate(surface, data), fn(data), surface)

    # -- anti-drift: the three hand-typed field lists against their real
    # producers. `task-registry`'s and `handover`'s required fields are
    # IMPORTED (`tasks_mod.RECORD_FIELDS`, `handover_mod.SCHEMA_FIELDS`), so a
    # producer change there already breaks at import time; STATUS_FIELDS,
    # STATUS_TEAM_FIELDS and WORK_BRIEF_FIELDS are typed out by hand (no
    # exported constant exists on `status.build_report`,
    # `status.build_team_report` or `work._brief_document` to import instead,
    # per the module docstring), so nothing else in this suite would notice a
    # producer growing or dropping a field: "unknown fields are allowed" is
    # this module's own design, so a widened real document would still
    # validate PASS even while this registry silently under-covers it. Each
    # test below ties one hand-typed tuple to the SAME real, live-captured
    # document `TestRealFixturesValidate.setUp` already produced, by exact
    # key-set equality, so that gap is closed by a fixture rather than left a
    # standing trust-the-docstring claim.

    def test_status_fields_names_exactly_the_keys_a_real_status_document_carries(self):
        self.assertEqual(set(mod.STATUS_FIELDS), set(self.status_data),
                         (sorted(mod.STATUS_FIELDS), sorted(self.status_data)))

    def test_status_team_fields_names_exactly_the_keys_a_real_status_team_document_carries(self):
        self.assertEqual(set(mod.STATUS_TEAM_FIELDS), set(self.status_team_data),
                         (sorted(mod.STATUS_TEAM_FIELDS), sorted(self.status_team_data)))

    def test_work_brief_fields_names_exactly_the_keys_a_real_work_brief_document_carries(self):
        self.assertEqual(set(mod.WORK_BRIEF_FIELDS), set(self.brief_data),
                         (sorted(mod.WORK_BRIEF_FIELDS), sorted(self.brief_data)))


# ---------------------------------------------------------------------------
# Refusal fixtures: a wrong schemaVersion, a missing required field, an
# absent document, the wrong top-level JSON shape, and (the other
# direction) an unknown extra field, which must NOT be refused. Every
# mutation below runs against a `copy.deepcopy` of a REAL captured document
# from `ContractsFixture`, and every test re-validates the untouched
# original as `PASS` in the same method: that is the calibration this
# house rule asks for (mutate, prove red, prove the un-mutated original
# still passes).
# ---------------------------------------------------------------------------

class TestRefusals(ContractsFixture):
    def setUp(self):
        ContractsFixture.setUp(self)
        # A lighter capture than TestRealFixturesValidate: refusal fixtures
        # mutate copies of these, so richness beyond "one real, valid
        # document per surface" buys nothing extra here.
        wt01 = self.start_task("T01", "alpha")
        bs.write(wt01, bs.BACKEND_PATH,
                "def lookup(widget_id, catalog):\n    return catalog.get(widget_id)\n")
        bs.git(wt01, "add", "-A")
        bs.git(wt01, "commit", "-qm", "backend: add the lookup service")
        self.run_evidence("T01", bs.BACKEND_VERIFY_ARGV)
        code, text, _err = self.finish_task("T01")
        self.assertEqual(code, 0, text)
        self.registry_data = self.registry()

        code, text, _err = self.sbe("status", self.repo, "--json")
        self.status_data = json.loads(text)

        code, text, _err = self.sbe("status", self.repo, "--team", "--json")
        self.status_team_data = json.loads(text)

        code, text, _err = self.work("brief", "--plan", self.plan_path, "--task", "T02",
                                     "--json")
        self.assertEqual(code, 0, text)
        self.brief_data = json.loads(text)

        code, text, _err = self.sbe("handover", "prepare", self.dossier,
                                    "--outgoing", OUTGOING, "--receiver", RECEIVER)
        self.assertEqual(code, 0, text)
        with io.open(os.path.join(self.dossier, "12-handover.json"), encoding="utf-8") as fh:
            self.handover_data = json.load(fh)

    # -- wrong schemaVersion, one surface at a time -------------------------

    def _assert_wrong_version_refused(self, validate_fn, original, name_of_field="schemaVersion"):
        mutated = copy.deepcopy(original)
        mutated[name_of_field] = "9.9"
        verdict, evidence, problems = validate_fn(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("9.9" in p for p in problems), problems)
        # Calibration: the untouched original still validates PASS, proving
        # the FAIL above came from the mutation and not from a validator
        # that always says FAIL.
        restored_verdict, restored_evidence, restored_problems = validate_fn(original)
        self.assertEqual(restored_verdict, "PASS", restored_evidence)
        self.assertEqual(restored_problems, ())

    def test_task_registry_with_an_unknown_schema_version_is_refused(self):
        self._assert_wrong_version_refused(mod.validate_task_registry, self.registry_data)

    def test_status_json_with_an_unknown_schema_version_is_refused(self):
        self._assert_wrong_version_refused(mod.validate_status, self.status_data)

    def test_work_brief_with_an_unknown_schema_version_is_refused(self):
        self._assert_wrong_version_refused(mod.validate_work_brief, self.brief_data)

    def test_handover_with_an_unknown_schema_version_is_refused(self):
        self._assert_wrong_version_refused(mod.validate_handover, self.handover_data)

    def test_status_team_with_a_present_but_unknown_schema_version_is_refused(self):
        # This surface carries none by default (asserted in
        # TestRealFixturesValidate); a mutated copy ADDS the field with an
        # unrecognized value, which must still be refused by name.
        mutated = copy.deepcopy(self.status_team_data)
        mutated["schemaVersion"] = "9.9"
        verdict, evidence, problems = mod.validate_status_team(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("9.9" in p for p in problems), problems)
        restored_verdict, _e, restored_problems = mod.validate_status_team(self.status_team_data)
        self.assertEqual(restored_verdict, "PASS", _e)
        self.assertEqual(restored_problems, ())

    # -- missing required field, one per surface -----------------------------

    def _assert_missing_field_refused(self, validate_fn, original, field):
        mutated = copy.deepcopy(original)
        del mutated[field]
        verdict, evidence, problems = validate_fn(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any(field in p for p in problems), problems)
        restored_verdict, restored_evidence, restored_problems = validate_fn(original)
        self.assertEqual(restored_verdict, "PASS", restored_evidence)
        self.assertEqual(restored_problems, ())

    def test_task_registry_missing_tasks_is_refused(self):
        self._assert_missing_field_refused(mod.validate_task_registry, self.registry_data,
                                           "tasks")

    def test_task_registry_with_a_task_record_missing_a_field_is_refused(self):
        mutated = copy.deepcopy(self.registry_data)
        self.assertTrue(mutated["tasks"], "fixture bug: no task record to mutate")
        del mutated["tasks"][0]["role"]
        verdict, evidence, problems = mod.validate_task_registry(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("role" in p for p in problems), problems)
        restored_verdict, _e, restored_problems = mod.validate_task_registry(self.registry_data)
        self.assertEqual(restored_verdict, "PASS", _e)
        self.assertEqual(restored_problems, ())

    def test_task_registry_with_a_task_record_holding_an_unknown_role_is_refused(self):
        mutated = copy.deepcopy(self.registry_data)
        mutated["tasks"][0]["role"] = "owner"
        verdict, evidence, problems = mod.validate_task_registry(mutated)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("owner" in p for p in problems), problems)

    def test_optional_controller_leases_validate_and_malformed_leases_fail(self):
        # `_validate_lease` (src/brothersbe/tasks.py) requires `worktree` to
        # be its own `os.path.realpath`, matching how `work.py`'s
        # `_candidate_worktree` always canonicalizes a worktree path before
        # it is stored. A literal "/tmp/..." string fails that check on
        # macOS, where `/tmp` is itself a symlink to `/private/tmp`, so the
        # fixture value is canonicalized the same way here rather than
        # spelled out by hand.
        lease = {
            "leaseId": "a" * 32, "change": "widget-cache", "taskId": "T01",
            "worktree": os.path.realpath("/tmp/repo-sbe-T01"),
            "controller": "claude:fable:one",
            "acquiredAt": "2026-08-10T00:00:00Z",
            "renewedAt": "2026-08-10T00:00:00Z",
            "expiresAt": "2026-08-10T00:30:00Z",
            "releasedAt": None, "release": None,
        }
        valid = copy.deepcopy(self.registry_data)
        valid["controllerLeases"] = [lease]
        verdict, evidence, problems = mod.validate_task_registry(valid)
        self.assertEqual(verdict, "PASS", (evidence, problems))

        malformed = copy.deepcopy(valid)
        del malformed["controllerLeases"][0]["controller"]
        verdict, evidence, problems = mod.validate_task_registry(malformed)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("controller" in p for p in problems), problems)

    def test_status_json_missing_next_action_is_refused(self):
        self._assert_missing_field_refused(mod.validate_status, self.status_data, "nextAction")

    def test_status_team_json_missing_findings_is_refused(self):
        self._assert_missing_field_refused(mod.validate_status_team, self.status_team_data,
                                           "findings")

    def test_work_brief_missing_acceptance_is_refused(self):
        self._assert_missing_field_refused(mod.validate_work_brief, self.brief_data,
                                           "acceptance")

    def test_handover_missing_prepared_by_is_refused(self):
        self._assert_missing_field_refused(mod.validate_handover, self.handover_data,
                                           "preparedBy")

    # -- absent document and wrong top-level shape --------------------------

    def test_every_validator_reports_no_data_for_an_absent_document(self):
        for fn in (mod.validate_task_registry, mod.validate_status, mod.validate_status_team,
                  mod.validate_work_brief, mod.validate_handover):
            verdict, evidence, problems = fn(None)
            self.assertEqual(verdict, "NO-DATA", (fn.__name__, evidence))
            self.assertEqual(problems, ())

    def test_every_validator_refuses_a_wrong_top_level_shape_as_fail_not_no_data(self):
        # A list where a JSON object belongs is a broken claim, not an
        # absence: the same class evals/test_no_data_class.py's own
        # legacy_cases already fixes for every check in this project that
        # reads a JSON file.
        for fn in (mod.validate_task_registry, mod.validate_status, mod.validate_status_team,
                  mod.validate_work_brief, mod.validate_handover):
            verdict, evidence, problems = fn([1, 2, 3])
            self.assertEqual(verdict, "FAIL", (fn.__name__, evidence))
            self.assertTrue(problems, (fn.__name__, problems))

    def test_every_wrong_shape_problem_names_its_own_surface(self):
        # Companion to the structural guard in TestRegistryShape: every
        # validator's own wrong-shape problem string names ITS OWN surface,
        # called back to back in a fixed order so a reader can see each
        # call's evidence text does not echo a name any OTHER call in this
        # same sequence used.
        labels = {
            mod.validate_task_registry: "task registry",
            mod.validate_status: "sbe status --json document",
            mod.validate_status_team: "sbe status --team --json document",
            mod.validate_work_brief: "work brief document",
            mod.validate_handover: "handover record",
        }
        others = set(labels.values())
        for fn, own_label in labels.items():
            _v, evidence, _p = fn([1, 2, 3])
            self.assertIn(own_label, evidence, evidence)
            for other_label in others - {own_label}:
                self.assertNotIn(other_label, evidence, (own_label, other_label, evidence))

    # -- unknown fields are allowed, the other direction ---------------------

    def test_task_registry_with_an_unknown_extra_field_still_validates_pass(self):
        mutated = copy.deepcopy(self.registry_data)
        mutated["futureField"] = "something a newer tool wrote"
        mutated["tasks"][0]["futureTaskField"] = "also forward compatible"
        verdict, evidence, problems = mod.validate_task_registry(mutated)
        self.assertEqual(verdict, "PASS", evidence)
        self.assertEqual(problems, ())

    def test_handover_with_an_unknown_extra_field_still_validates_pass(self):
        mutated = copy.deepcopy(self.handover_data)
        mutated["futureField"] = "something a newer tool wrote"
        verdict, evidence, problems = mod.validate_handover(mutated)
        self.assertEqual(verdict, "PASS", evidence)
        self.assertEqual(problems, ())

    # -- the dispatcher's own refusal, a caller error rather than a finding --

    def test_the_dispatcher_refuses_an_unknown_surface_by_name(self):
        with self.assertRaises(ValueError) as ctx:
            mod.validate("not-a-real-surface", {})
        for name in mod.SURFACES:
            self.assertIn(name, str(ctx.exception))


# ---------------------------------------------------------------------------
# The registry itself: shape independent of any one captured document.
# ---------------------------------------------------------------------------

class TestRegistryShape(unittest.TestCase):
    def test_contracts_schema_version_is_the_integer_one(self):
        self.assertIsInstance(mod.CONTRACTS_SCHEMA_VERSION, int)
        self.assertEqual(mod.CONTRACTS_SCHEMA_VERSION, 1)

    def test_surfaces_names_exactly_the_known_surfaces(self):
        self.assertEqual(set(mod.SURFACES),
                         {"task-registry", "status", "status-team", "work-brief", "handover",
                          "passport", "decision-packet", "human-decision", "release-receipt",
                          "observation-contract", "verified-reality"})
        self.assertEqual(set(mod.VALIDATORS), set(mod.SURFACES))

    def test_every_validator_returns_the_house_three_tuple(self):
        for surface in mod.SURFACES:
            result = mod.validate(surface, None)
            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), 3, (surface, result))
            verdict, evidence, problems = result
            self.assertIn(verdict, mod.VERDICTS, surface)
            self.assertIsInstance(evidence, str, surface)
            self.assertIsInstance(problems, tuple, surface)

    def test_no_module_level_mutable_surface_label_survives_a_call(self):
        # Regression: `_shape_problem` used to read "which surface is this"
        # off a module-level, mutable, shared list (`_CURRENT_SURFACE_LABEL`,
        # a single object every `validate_*` call reassigned right before
        # calling `_shape_problem`). Two `validate_*` calls interleaved (two
        # threads, one paused between setting the label and reading it back)
        # could then have the SECOND call's wrong-shape problem string name
        # whichever surface set that shared state last, not its own; nothing
        # about a single-threaded, non-interleaved call sequence can exercise
        # that race (each call sets its own label immediately before its own
        # read), so this pins the STRUCTURAL fix instead: the shared object
        # is gone from the module entirely, and `_shape_problem` takes the
        # surface name as an explicit parameter, so there is no shared slot
        # left for a second call to race on.
        called = mod.validate_work_brief([1, 2, 3])  # exercises the code path
        self.assertEqual(called[0], "FAIL", called)
        self.assertFalse(hasattr(mod, "_CURRENT_SURFACE_LABEL"),
                         "a module-level mutable surface label still exists on "
                         "brothersbe.contracts; two validate_* calls in flight at "
                         "once can cross-contaminate each other's problem text")
        params = list(inspect.signature(mod._shape_problem).parameters)
        self.assertIn("label", params,
                      "_shape_problem(%s) no longer takes the surface name as an "
                      "explicit parameter" % ", ".join(params))


# ---------------------------------------------------------------------------
# BAND K, THE CONTRACT SPINE: the six lifecycle objects below the Change
# Passport seam.
#
# WHY THESE FIXTURES ARE HAND BUILT, unlike every fixture above. The five
# surfaces above are validated against REAL captured output, because a real
# producer exists to capture from. Not one of the six objects below has a
# producer in this repository yet: bands R and L build them, and this band
# settles the identity rules FIRST so those producers have one shape to write
# rather than six private ones. A hand-built document is the only honest
# fixture for a surface nothing emits, and it is named as the weaker fixture
# it is rather than dressed up: these tests prove the VALIDATOR refuses what
# it should refuse, and they prove nothing whatever about what any future
# producer will actually write. The day `sbe decide` and `sbe release` exist,
# these fixtures should be re-pointed at their real output the way
# `ContractsFixture` above already does.
#
# Calibration, the same discipline `TestRefusals` above holds to: every
# refusal fixture starts from a document this suite has just validated as
# PASS, `copy.deepcopy`s it, changes exactly ONE thing, and asserts the
# verdict moves. The un-mutated original is re-checked in the same test, so a
# validator that always said FAIL could not pass these.
# ---------------------------------------------------------------------------

CHANGE_ID = "CHG-2026-08-19-contract-spine"
HEAD = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
OTHER_HEAD = "9876543210fedcba9876543210fedcba98765432"
PACKET_AT = "2026-08-19T09:00:00Z"
DECIDED_AT = "2026-08-19T10:30:00Z"
DECLARED_AT = "2026-08-19T10:45:00Z"
RELEASED_AT = "2026-08-19T11:00:00Z"


def spine(**over):
    """The six-field identity spine every lifecycle object carries, at the
    current lifecycle schema version, with `over` applied last."""
    doc = {
        "schemaVersion": mod.LIFECYCLE_SCHEMA_VERSION,
        "changeId": CHANGE_ID,
        "createdAt": PACKET_AT,
        "producer": "sbe 1.0.0",
        "producerClass": "tool",
        "origin": "git@example.invalid:acme/thing.git",
    }
    doc.update(over)
    return doc


def _built(base, over):
    """`base` with `over` applied last. Every builder below routes through
    this rather than splatting `**over` into its own keyword arguments: that
    shape raises TypeError the moment a test overrides a field the builder
    already names, which is exactly what an override IS for."""
    built = dict(base)
    built.update(over)
    return built


def a_passport(**over):
    return _built(spine(
        headCommit=HEAD,
        whatWasDone="two files changed under src/, one behaviour added",
        whoDidIt="an agent working under task lane-k2, human named in the task",
        whatWasRun="tools/test_sbe_contracts.py, exit 0",
        whatWasNotEstablished="no production observation exists for this change",
        whereItCameFrom="branch spine-work, plan docs/plans/2026-08-19.md"), over)


def a_packet(**over):
    return _built(spine(
        headCommit=HEAD,
        readinessState="READY_WITH_KNOWN_RISK",
        question="Release this change, knowing the one risk below?",
        knownRisks=["no production observation adapter exists yet"],
        notEstablished=["behaviour under a Bitbucket host is unmeasured"]), over)


def a_decision(packet, **over):
    return _built(spine(
        createdAt=DECIDED_AT,
        producer="the accountable engineer",
        producerClass="human",
        headCommit=packet["headCommit"],
        packetSha256=mod.canonical_digest(packet),
        decision="RELEASE"), over)


def a_receipt(**over):
    # `decisionSha256` defaults to a fixed dummy digest, the same pattern
    # `artifactSha256="f" * 64` already uses: most receipt fixtures below
    # validate the receipt on its own face and never pair it with a real
    # decision, so a fixed placeholder is enough. A test that DOES pair a
    # receipt with a real decision for `release_binds` overrides it with
    # `mod.canonical_digest(that_decision)`, or the digest recomputed inside
    # `release_binds` would (correctly) refuse the mismatch.
    return _built(spine(
        createdAt=RELEASED_AT,
        releasedCommit=HEAD,
        artifactSha256="f" * 64,
        runId="run-2026-08-19-0001",
        releasedAt=RELEASED_AT,
        decisionSha256="e" * 64), over)


def an_observation_contract(**over):
    return _built(spine(
        createdAt=DECLARED_AT,
        headCommit=HEAD,
        declaredAt=DECLARED_AT,
        observes=["checkout error rate", "p95 latency on the changed path"],
        window="72h after release"), over)


def a_reality_state(contract, **over):
    return _built(spine(
        createdAt="2026-08-22T11:00:00Z",
        releasedCommit=HEAD,
        observationContractSha256=mod.canonical_digest(contract),
        realityState="VERIFIED_IN_REALITY",
        observedAt="2026-08-22T11:00:00Z",
        basis="the two declared signals, read at the end of the declared window"), over)


class TestSpineShapes(unittest.TestCase):
    """Every new surface validates its own good document, and refuses the
    identity failures that are visible on one document alone."""

    def setUp(self):
        self.packet = a_packet()
        self.contract = an_observation_contract()

    def good(self):
        return (("passport", a_passport()),
                ("decision-packet", self.packet),
                ("human-decision", a_decision(self.packet)),
                ("release-receipt", a_receipt()),
                ("observation-contract", self.contract),
                ("verified-reality", a_reality_state(self.contract)))

    def test_every_spine_surface_passes_its_own_good_document(self):
        for surface, document in self.good():
            verdict, evidence, problems = mod.validate(surface, document)
            self.assertEqual(verdict, "PASS", (surface, evidence, problems))
            self.assertEqual(problems, (), surface)

    def test_every_spine_surface_answers_no_data_when_given_nothing(self):
        for surface, _document in self.good():
            verdict, evidence, problems = mod.validate(surface, None)
            self.assertEqual(verdict, "NO-DATA", (surface, evidence))
            self.assertEqual(problems, (), surface)

    def test_a_missing_identity_field_is_named_on_every_spine_surface(self):
        for surface, document in self.good():
            for field in mod.IDENTITY_SPINE:
                stripped = copy.deepcopy(document)
                stripped.pop(field)
                verdict, evidence, _problems = mod.validate(surface, stripped)
                self.assertEqual(verdict, "FAIL", (surface, field, evidence))
                self.assertIn(field, evidence, (surface, field))
            self.assertEqual(mod.validate(surface, document)[0], "PASS", surface)

    def test_an_identity_field_present_but_empty_binds_nothing_and_fails(self):
        # The difference between this and the test above is the whole reason
        # `_unanswered_fields` exists: `"changeId": ""` is PRESENT, so a
        # presence-only check passes it, and it identifies no change at all.
        for surface, document in self.good():
            empty = copy.deepcopy(document)
            empty["changeId"] = ""
            verdict, evidence, _problems = mod.validate(surface, empty)
            self.assertEqual(verdict, "FAIL", (surface, evidence))
            self.assertIn("changeId", evidence, surface)
            self.assertEqual(mod.validate(surface, document)[0], "PASS", surface)

    def test_an_unknown_schema_version_is_refused_rather_than_parsed_hopefully(self):
        for surface, document in self.good():
            future = copy.deepcopy(document)
            future["schemaVersion"] = "9.9"
            verdict, evidence, _problems = mod.validate(surface, future)
            self.assertEqual(verdict, "FAIL", (surface, evidence))
            self.assertIn("9.9", evidence, surface)
            self.assertEqual(mod.validate(surface, document)[0], "PASS", surface)

    def test_an_unknown_producer_class_is_refused_by_name(self):
        for surface, document in self.good():
            odd = copy.deepcopy(document)
            odd["producerClass"] = "committee"
            verdict, evidence, _problems = mod.validate(surface, odd)
            self.assertEqual(verdict, "FAIL", (surface, evidence))
            self.assertIn("producerClass", evidence, surface)
            self.assertEqual(mod.validate(surface, document)[0], "PASS", surface)

    def test_an_empty_field_four_reads_as_everything_was_established_and_fails(self):
        passport = a_passport()
        self.assertEqual(mod.validate_passport(passport)[0], "PASS")
        hollow = copy.deepcopy(passport)
        hollow["whatWasNotEstablished"] = ""
        verdict, evidence, _problems = mod.validate_passport(hollow)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("whatWasNotEstablished", evidence)

    def test_readiness_and_reality_states_are_never_a_fourth_check_verdict(self):
        # The ratified rail (`design/lifecycle-blockers/03-adr.md`): PASS,
        # FAIL and NO-DATA are a CLOSED vocabulary, so a readiness or reality
        # state must not be expressible as a check verdict, and no check
        # verdict may leak into a state vocabulary.
        for states in (mod.READINESS_STATES, mod.REALITY_STATES,
                       mod.DECLARATION_STATES, mod.DECISION_ANSWERS):
            self.assertEqual(set(states) & set(mod.VERDICTS), set(), states)

    def test_a_readiness_state_outside_the_vocabulary_is_refused(self):
        packet = a_packet()
        self.assertEqual(mod.validate_decision_packet(packet)[0], "PASS")
        invented = copy.deepcopy(packet)
        invented["readinessState"] = "PROBABLY_FINE"
        verdict, evidence, _problems = mod.validate_decision_packet(invented)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("PROBABLY_FINE", evidence)

    def test_a_reality_state_outside_the_vocabulary_is_refused(self):
        contract = an_observation_contract()
        reality = a_reality_state(contract)
        self.assertEqual(mod.validate_verified_reality(reality)[0], "PASS")
        invented = copy.deepcopy(reality)
        invented["realityState"] = "SHIPPED_AND_FINE"
        verdict, evidence, _problems = mod.validate_verified_reality(invented)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("SHIPPED_AND_FINE", evidence)


class TestSpineIdentityRefusals(unittest.TestCase):
    """The six failures this band exists to make impossible. Each one is the
    named case from the work brief, and each is written to fail before the
    guard that catches it exists."""

    def setUp(self):
        self.packet = a_packet()
        self.decision = a_decision(self.packet)
        self.receipt = a_receipt(decisionSha256=mod.canonical_digest(self.decision))
        self.contract = an_observation_contract()

    # 1 -------------------------------------------------------------------
    def test_a_decision_packet_digest_mismatch_is_rejected(self):
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS",
                         "calibration: the unmutated pair must bind before a mutated "
                         "one is asked to fail")
        edited = copy.deepcopy(self.packet)
        edited["knownRisks"] = []  # the risk paragraph removed AFTER the human read it
        self.assertNotEqual(mod.canonical_digest(edited), self.decision["packetSha256"])
        verdict, evidence, problems = mod.decision_binds(self.decision, edited, HEAD)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("digest" in p or "packetSha256" in p for p in problems),
                        "the refusal has to name the digest, not just fail: %r" % (problems,))
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")

    def test_a_packet_that_cannot_be_digested_refuses_instead_of_raising(self):
        # `json.loads('{"x": NaN}')` succeeds, and a document holding NaN has
        # no canonical encoding. An authorization path must leave through a
        # verdict, never through an exception a caller could read as "unknown".
        undigestable = copy.deepcopy(self.packet)
        undigestable["knownRisks"] = [float("nan")]
        verdict, evidence, problems = mod.decision_binds(self.decision, undigestable, HEAD)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("canonically encoded" in p for p in problems), problems)
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")

    # 2 -------------------------------------------------------------------
    def test_a_decision_bound_to_a_different_head_commit_is_rejected(self):
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")
        moved = copy.deepcopy(self.decision)
        moved["headCommit"] = OTHER_HEAD
        verdict, evidence, problems = mod.decision_binds(moved, self.packet, HEAD)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any(OTHER_HEAD in p for p in problems), problems)
        # And the same refusal when the CHANGE has moved under a decision that
        # is internally consistent with the packet it answered: the head the
        # caller names is the head that counts.
        stale = mod.decision_binds(self.decision, self.packet, OTHER_HEAD)
        self.assertEqual(stale[0], "FAIL", stale[1])
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")

    # 3 -------------------------------------------------------------------
    def test_an_agent_authored_decision_is_rejected(self):
        self.assertEqual(mod.validate_human_decision(self.decision)[0], "PASS")
        for machine in mod.NON_HUMAN_PRODUCER_CLASSES:
            forged = copy.deepcopy(self.decision)
            forged["producerClass"] = machine
            forged["producer"] = "brothersbe:implementation-worker"
            verdict, evidence, problems = mod.validate_human_decision(forged)
            self.assertEqual(verdict, "FAIL", (machine, evidence))
            self.assertTrue(any("producerClass" in p for p in problems), (machine, problems))
            # and it cannot reach the release either, through the binding path
            bound = mod.decision_binds(forged, self.packet, HEAD)
            self.assertEqual(bound[0], "FAIL", (machine, bound[1]))
        self.assertEqual(mod.validate_human_decision(self.decision)[0], "PASS")

    # 4 -------------------------------------------------------------------
    def test_a_release_receipt_naming_another_commit_than_the_decision_is_rejected(self):
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS",
                         "calibration: a receipt for the decided head must bind")
        elsewhere = copy.deepcopy(self.receipt)
        elsewhere["releasedCommit"] = OTHER_HEAD
        verdict, evidence, problems = mod.release_binds(elsewhere, self.decision)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any(OTHER_HEAD in p for p in problems), problems)
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")

    # 5 -------------------------------------------------------------------
    def test_an_observation_contract_dated_after_its_receipt_reads_late_declaration(self):
        self.assertEqual(mod.declaration_timing(self.contract, self.receipt), "PREDECLARED",
                         "calibration: a contract declared before the release is a "
                         "predeclaration")
        afterwards = copy.deepcopy(self.contract)
        afterwards["declaredAt"] = "2026-08-19T11:00:01Z"  # one second after release
        self.assertEqual(mod.declaration_timing(afterwards, self.receipt), "LATE_DECLARATION")
        much_later = copy.deepcopy(self.contract)
        much_later["declaredAt"] = "2026-08-22T09:00:00Z"
        self.assertEqual(mod.declaration_timing(much_later, self.receipt), "LATE_DECLARATION")
        # "after" means after: the same instant is not late.
        same = copy.deepcopy(self.contract)
        same["declaredAt"] = self.receipt["releasedAt"]
        self.assertEqual(mod.declaration_timing(same, self.receipt), "PREDECLARED")
        # and nothing is claimed where nothing could be compared
        undated = copy.deepcopy(self.contract)
        undated["declaredAt"] = "sometime last week"
        self.assertEqual(mod.declaration_timing(undated, self.receipt), "UNDETERMINED")
        self.assertEqual(mod.declaration_timing(self.contract, None), "UNDETERMINED")

    # 6 -------------------------------------------------------------------
    def test_a_record_at_an_older_schema_version_still_validates(self):
        # `origin` arrives at 1.1. A 1.0 record that never had the field is
        # judged by ITS OWN version's contract, exactly as an evidence receipt
        # declaring 1.3 is never failed for missing `ciRunUrl`.
        self.assertIn("origin", dict(mod.LIFECYCLE_FIELDS_INTRODUCED_IN)["1.1"])
        for surface, builder in (("decision-packet", a_packet),
                                 ("release-receipt", a_receipt),
                                 ("observation-contract", an_observation_contract)):
            old = builder(schemaVersion="1.0")
            old.pop("origin")
            verdict, evidence, problems = mod.validate(surface, old)
            self.assertEqual(verdict, "PASS", (surface, evidence, problems))
        # The same record at the version that DID introduce the field is
        # still failed for it: forward-only, never retroactive, in both
        # directions.
        current = a_packet()
        current.pop("origin")
        verdict, evidence, _problems = mod.validate_decision_packet(current)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("origin", evidence)
        # And the authorization field is NOT version scoped: an older human
        # decision cannot buy its way out of the producer-class rule by
        # declaring an older version.
        for machine in mod.NON_HUMAN_PRODUCER_CLASSES:
            forged = a_decision(self.packet, schemaVersion="1.0", producerClass=machine)
            forged.pop("origin")
            self.assertEqual(mod.validate_human_decision(forged)[0], "FAIL", machine)
        honest = a_decision(self.packet, schemaVersion="1.0")
        honest.pop("origin")
        self.assertEqual(mod.validate_human_decision(honest)[0], "PASS")


class TestSpineReviewRefusals(unittest.TestCase):
    """The eight holes an adversarial review reproduced against this spine
    after `TestSpineIdentityRefusals` above was already green.

    Every test here was written to FAIL against the spine as it stood, and
    each was run red against it before the guard that catches it existed: a
    suite that was green while `release_binds` accepted an agent-authored
    `HOLD` is the reason this class states that discipline in its own
    docstring rather than assuming it. Same calibration rule as every class
    above: the honest document or pair is checked in the SAME test, so a
    guard that refused everything could not pass these either.
    """

    def setUp(self):
        self.packet = a_packet()
        self.decision = a_decision(self.packet)
        self.receipt = a_receipt(decisionSha256=mod.canonical_digest(self.decision))
        self.contract = an_observation_contract()
        self.reality = a_reality_state(self.contract)

    # 1, CRITICAL: `release_binds` was a third, unguarded reader ----------
    def test_release_binds_refuses_a_decision_no_human_authored(self):
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS",
                         "calibration: an honest human RELEASE must still bind")
        for machine in mod.NON_HUMAN_PRODUCER_CLASSES:
            forged = copy.deepcopy(self.decision)
            forged["producerClass"] = machine
            forged["producer"] = "brothersbe:implementation-worker"
            # the other two readers already refuse it; the third has to agree,
            # or the route around the rule is to call this one.
            self.assertEqual(mod.validate_human_decision(forged)[0], "FAIL", machine)
            self.assertEqual(mod.decision_binds(forged, self.packet, HEAD)[0], "FAIL",
                             machine)
            verdict, evidence, problems = mod.release_binds(self.receipt, forged)
            self.assertEqual(verdict, "FAIL", (machine, evidence))
            self.assertTrue(any("producerClass" in p for p in problems),
                            (machine, problems))
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")

    def test_release_binds_refuses_a_decision_that_does_not_say_release(self):
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")
        held = copy.deepcopy(self.decision)
        held["decision"] = "HOLD"
        verdict, evidence, problems = mod.release_binds(self.receipt, held)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("HOLD" in p for p in problems), problems)
        # ... and the two shapes that carry no answer at all: a decision key
        # that was never written, and one holding an invented word.
        silent = copy.deepcopy(self.decision)
        silent.pop("decision")
        self.assertEqual(mod.release_binds(self.receipt, silent)[0], "FAIL")
        invented = copy.deepcopy(self.decision)
        invented["decision"] = "SHIP_IT"
        self.assertEqual(mod.release_binds(self.receipt, invented)[0], "FAIL")
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")

    # 2, CRITICAL: a binder that compares two nothings ---------------------
    def test_neither_binder_passes_on_identities_that_bind_nothing(self):
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS",
                         "calibration: the honest pair must still bind")
        # Two almost-empty documents agreed perfectly: None == None, and the
        # digest of an empty packet is a real digest of a real (empty) packet.
        hollow_packet = {}
        hollow_decision = {"producerClass": "human",
                           "packetSha256": mod.canonical_digest(hollow_packet)}
        verdict, evidence, problems = mod.decision_binds(hollow_decision, hollow_packet)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("changeId" in p for p in problems), problems)
        self.assertNotIn("all three matching", evidence)
        # The same hole with a head commit that is the empty string, which is
        # what a `git rev-parse` that failed hands its caller.
        blank_packet = a_packet(headCommit="")
        blank_decision = a_decision(blank_packet, headCommit="")
        verdict, evidence, problems = mod.decision_binds(blank_decision, blank_packet, "")
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("headCommit" in p or "head to compare" in p for p in problems),
                        problems)
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")

    def test_release_binds_refuses_a_receipt_and_decision_that_identify_nothing(self):
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")
        blank_receipt = a_receipt(changeId="", releasedCommit="")
        blank_decision = a_decision(self.packet, changeId="", headCommit="")
        verdict, evidence, problems = mod.release_binds(blank_receipt, blank_decision)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("records nothing" in p for p in problems), problems)
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")

    # 3, MAJOR: the release date nothing guarded --------------------------
    def test_a_receipt_whose_release_date_is_not_an_instant_is_refused(self):
        self.assertEqual(mod.validate_release_receipt(self.receipt)[0], "PASS",
                         "calibration: the honest receipt must still validate")
        undated = copy.deepcopy(self.receipt)
        undated["releasedAt"] = "shortly after the standup"
        verdict, evidence, _problems = mod.validate_release_receipt(undated)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertIn("releasedAt", evidence)
        # and the reason it matters: an unguarded one turns every lateness
        # question about that release into UNDETERMINED instead of LATE.
        late = copy.deepcopy(self.contract)
        late["declaredAt"] = "2026-08-22T09:00:00Z"
        self.assertEqual(mod.declaration_timing(late, self.receipt), "LATE_DECLARATION")
        self.assertEqual(mod.declaration_timing(late, undated), "UNDETERMINED")

    # 4, MAJOR: a declared binding nothing recomputed ---------------------
    def test_a_reality_state_citing_a_contract_it_does_not_match_is_refused(self):
        self.assertEqual(mod.reality_binds(self.reality, self.contract)[0], "PASS",
                         "calibration: the honest pair must bind")
        nonsense = copy.deepcopy(self.reality)
        nonsense["observationContractSha256"] = "deadbeef" * 8
        verdict, evidence, problems = mod.reality_binds(nonsense, self.contract)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("digest" in p for p in problems), problems)
        # a contract edited after the fact digests differently, exactly as an
        # edited packet does for `decision_binds`
        edited = copy.deepcopy(self.contract)
        edited["observes"] = ["only the signal that came back clean"]
        self.assertEqual(mod.reality_binds(self.reality, edited)[0], "FAIL")
        # and a reality state about a commit the contract never watched
        elsewhere = copy.deepcopy(self.reality)
        elsewhere["releasedCommit"] = OTHER_HEAD
        self.assertEqual(mod.reality_binds(elsewhere, self.contract)[0], "FAIL")
        self.assertEqual(mod.reality_binds(self.reality, None)[0], "NO-DATA")
        self.assertEqual(mod.reality_binds(self.reality, self.contract)[0], "PASS")

    # 5, MAJOR: a timestamp that is not an instant ------------------------
    def test_a_timestamp_without_a_time_or_an_offset_is_not_an_instant(self):
        self.assertEqual(mod.declaration_timing(self.contract, self.receipt), "PREDECLARED",
                         "calibration: a Z-stamped instant still compares")
        # Date only: parsed as midnight, so a contract written late on release
        # day read as declared eleven hours before the release.
        date_only = copy.deepcopy(self.contract)
        date_only["declaredAt"] = RELEASED_AT[:10]
        self.assertEqual(mod.declaration_timing(date_only, self.receipt), "UNDETERMINED")
        self.assertEqual(mod.validate_observation_contract(date_only)[0], "FAIL")
        # No offset: the same wall time is before OR after the release
        # depending on a clock this document never states. Stated, it is late;
        # unstated, nothing is claimed.
        naive = copy.deepcopy(self.contract)
        naive["declaredAt"] = "2026-08-19T10:45:00"
        self.assertEqual(mod.declaration_timing(naive, self.receipt), "UNDETERMINED")
        self.assertEqual(mod.validate_observation_contract(naive)[0], "FAIL")
        stated = copy.deepcopy(self.contract)
        stated["declaredAt"] = "2026-08-19T10:45:00-05:00"  # 15:45Z, after the release
        self.assertEqual(mod.declaration_timing(stated, self.receipt), "LATE_DECLARATION")
        # the same two holes on the receipt's own side of the comparison
        for bad in (RELEASED_AT[:10], "2026-08-19T11:00:00"):
            receipt = a_receipt(releasedAt=bad)
            self.assertEqual(mod.declaration_timing(self.contract, receipt), "UNDETERMINED",
                             bad)
            self.assertEqual(mod.validate_release_receipt(receipt)[0], "FAIL", bad)
        self.assertEqual(mod.declaration_timing(self.contract, self.receipt), "PREDECLARED")

    # 6, MINOR: a version table cannot hole a binding ---------------------
    def test_no_version_table_can_make_an_identity_or_binding_field_optional(self):
        introduced = set(n for _v, names in mod.LIFECYCLE_FIELDS_INTRODUCED_IN
                         for n in names)
        self.assertEqual(introduced & set(mod.NEVER_VERSION_SCOPED), set(),
                         "the shipped table and the protected names must be disjoint")
        for binding in (mod.PASSPORT_BINDING, mod.DECISION_PACKET_BINDING,
                        mod.HUMAN_DECISION_BINDING, mod.RELEASE_RECEIPT_BINDING,
                        mod.OBSERVATION_CONTRACT_BINDING, mod.VERIFIED_REALITY_BINDING):
            for field in binding:
                self.assertIn(field, mod.NEVER_VERSION_SCOPED, field)
        # and the enforcement, not just the tuple: a FUTURE table that names a
        # binding field cannot buy an older record out of carrying it.
        saved = mod.LIFECYCLE_FIELDS_INTRODUCED_IN
        mod.LIFECYCLE_FIELDS_INTRODUCED_IN = (("1.1", ("origin", "packetSha256",
                                                       "headCommit", "releasedCommit")),)
        try:
            hollowed = a_decision(self.packet, schemaVersion="1.0")
            hollowed.pop("origin")
            hollowed.pop("packetSha256")
            verdict, evidence, _problems = mod.validate_human_decision(hollowed)
            self.assertEqual(verdict, "FAIL", evidence)
            self.assertIn("packetSha256", evidence)
            stripped = a_receipt(schemaVersion="1.0")
            stripped.pop("origin")
            stripped.pop("releasedCommit")
            self.assertEqual(mod.validate_release_receipt(stripped)[0], "FAIL")
            # `origin` is the ONE declared exception and stays scopable, so
            # this guard is not a blanket freeze of the version table.
            honest = a_decision(self.packet, schemaVersion="1.0")
            honest.pop("origin")
            self.assertEqual(mod.validate_human_decision(honest)[0], "PASS")
        finally:
            mod.LIFECYCLE_FIELDS_INTRODUCED_IN = saved
        self.assertEqual(mod.validate_human_decision(self.decision)[0], "PASS")

    # 7, MINOR: two documents, one digest ---------------------------------
    def test_a_non_string_key_is_refused_rather_than_coerced_into_a_collision(self):
        self.assertEqual(mod.canonical_digest({"a": {"1": "x"}}),
                         mod.canonical_digest({"a": {"1": "x"}}),
                         "calibration: an ordinary document still digests")
        # `json.dumps` coerces a non-string key to its string form, so these
        # two DIFFERENT documents used to share one digest, and a digest two
        # documents share binds neither.
        for colliding in ({"a": {1: "x"}}, {"a": {True: "x"}}, {"a": [{None: "x"}]}):
            with self.assertRaises(TypeError):
                mod.canonical_digest(colliding)
        # and at the authorization boundary it leaves as a verdict, not a raise
        keyed = a_packet(knownRisks=[{2: "a risk keyed by an integer"}])
        verdict, evidence, problems = mod.decision_binds(self.decision, keyed, HEAD)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("canonically encoded" in p for p in problems), problems)
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")

    # 8, MINOR: a binder leaves through a verdict, always ------------------
    def test_a_deeply_nested_document_refuses_instead_of_raising(self):
        deep = {}
        node = deep
        for _ in range(30000):  # past what the C JSON encoder unwinds for itself
            node["nested"] = {}
            node = node["nested"]
        packet = a_packet(knownRisks=[deep])
        verdict, evidence, problems = mod.decision_binds(self.decision, packet, HEAD)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("canonically encoded" in p for p in problems), problems)
        contract = an_observation_contract(observes=[deep])
        reality = a_reality_state(self.contract)
        verdict, evidence, problems = mod.reality_binds(reality, contract)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("canonically encoded" in p for p in problems), problems)
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")


class TestN1RefuterFindings(unittest.TestCase):
    """Five findings an adversarial review (N1) reproduced against this spine
    with runnable fixtures, after `TestSpineReviewRefusals` above was already
    green. Same discipline as that class: every test here was run RED against
    the spine as it stood, against the refuter's own fixture, before the
    guard that catches it existed, and the honest document or pair is
    re-checked in the SAME test so a guard that refused everything could not
    pass these either.
    """

    def setUp(self):
        self.packet = a_packet()
        self.decision = a_decision(self.packet)
        self.receipt = a_receipt(decisionSha256=mod.canonical_digest(self.decision))
        self.contract = an_observation_contract()
        self.reality = a_reality_state(self.contract)

    # 1, CRITICAL: a falsy cross-type identity binds -----------------------
    # `answered()` reads `False`/`0`/`0.0` as "recording something", right
    # for a row count and wrong for an identity: Python's own cross-type
    # equality (`0 == False == 0.0`, `1 == True`) would then bind it to an
    # unrelated document by nothing more than that coercion, not by naming
    # the same thing twice.
    def test_a_falsy_cross_type_identity_is_refused_on_every_surface(self):
        for value in (False, 0, 0.0, True, 1):
            packet = a_packet(changeId=value, headCommit=value)
            verdict, evidence, problems = mod.validate_decision_packet(packet)
            self.assertEqual(verdict, "FAIL", (value, evidence))
            self.assertTrue(any("not a string" in p for p in problems), (value, problems))
            decision = a_decision(packet, changeId=value, headCommit=value)
            self.assertEqual(mod.validate_human_decision(decision)[0], "FAIL", value)
            receipt = a_receipt(changeId=value, releasedCommit=value,
                                artifactSha256=value, runId=value)
            self.assertEqual(mod.validate_release_receipt(receipt)[0], "FAIL", value)
        self.assertEqual(mod.validate_decision_packet(self.packet)[0], "PASS")

    def test_decision_binds_refuses_the_refuters_own_false_vs_zero_chain(self):
        # The refuter's own chain: a packet identified by `0`, a decision
        # identified by `False`, and `0 == False` in Python.
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS",
                         "calibration: the honest pair must still bind")
        packet = a_packet(changeId=0, headCommit=0)
        decision = a_decision(packet, changeId=False, headCommit=False)
        verdict, evidence, problems = mod.decision_binds(decision, packet)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("not a string" in p for p in problems), problems)
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")

    def test_release_binds_refuses_the_refuters_own_falsy_receipt_chain(self):
        # The refuter's own chain: released=0.0, artifact=False, run=0.
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS",
                         "calibration: the honest pair must still bind")
        receipt = a_receipt(changeId=False, releasedCommit=0.0, artifactSha256=False,
                            runId=0)
        decision = a_decision(self.packet, changeId=False, headCommit=0.0)
        verdict, evidence, problems = mod.release_binds(receipt, decision)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("not a string" in p for p in problems), problems)
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")

    def test_reality_binds_refuses_a_falsy_cross_type_pair(self):
        self.assertEqual(mod.reality_binds(self.reality, self.contract)[0], "PASS",
                         "calibration: the honest pair must still bind")
        contract = an_observation_contract(changeId=True, headCommit=1)
        reality = a_reality_state(contract, changeId=1, releasedCommit=True)
        verdict, evidence, problems = mod.reality_binds(reality, contract)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("not a string" in p for p in problems), problems)
        self.assertEqual(mod.reality_binds(self.reality, self.contract)[0], "PASS")

    # 2, MAJOR: `_instant` raised OverflowError on an extreme offset --------
    def test_a_receipt_at_an_extreme_offset_refuses_instead_of_raising(self):
        # Go's zero time (`0001-01-01T00:00:00`), rendered in any location
        # east of UTC, and the common "never expires" sentinel
        # (`9999-12-31T23:59:59`), rendered west of UTC, both shift past what
        # `datetime` can represent once normalized to UTC.
        for ts in ("0001-01-01T00:00:00+09:00", "0001-01-01T00:00:00+14:00",
                  "0001-01-01T00:00:00+00:01", "9999-12-31T23:59:59-05:00",
                  "9999-12-31T23:59:59-14:00"):
            self.assertIsNone(mod._instant(ts), ts)
            receipt = a_receipt(releasedAt=ts)
            verdict, evidence, _problems = mod.validate_release_receipt(receipt)
            self.assertEqual(verdict, "FAIL", (ts, evidence))
            self.assertEqual(mod.declaration_timing(self.contract, receipt),
                             "UNDETERMINED", ts)
        self.assertEqual(mod.validate_release_receipt(self.receipt)[0], "PASS")

    def test_instant_behaviors_pinned_before_this_fix_are_unchanged(self):
        # Guard against widening `_instant` past what it already promised
        # while fixing the overflow: lowercase `z` still refuses, a `+09:00`
        # offset still converts to the same UTC instant, and surrounding
        # whitespace is still tolerated.
        self.assertIsNone(mod._instant("2026-08-19T10:00:00z"))
        self.assertEqual(mod._instant("2026-08-19T10:00:00+09:00"),
                         mod._instant("2026-08-19T01:00:00+00:00"))
        self.assertIsNotNone(mod._instant("  2026-08-19T10:00:00Z"))
        self.assertIsNotNone(mod._instant("2026-08-19T10:00:00Z "))

    # 3, MAJOR: a NUL byte in a lease worktree raised out of `realpath` -----
    def test_a_nul_byte_in_a_lease_worktree_refuses_instead_of_raising(self):
        registry = {"schemaVersion": "1.0", "tasks": [],
                   "controllerLeases": [{"leaseId": "a" * 32, "change": "c",
                                        "taskId": "t", "controller": "ctl",
                                        "worktree": "/tmp/a\x00b",
                                        "acquiredAt": "2026-08-19T10:00:00Z",
                                        "renewedAt": "2026-08-19T10:00:00Z",
                                        "expiresAt": "2026-08-19T11:00:00Z",
                                        "releasedAt": None, "release": None}]}
        verdict, evidence, problems = mod.validate_task_registry(registry)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("not checkable" in p for p in problems), problems)
        # calibration: an ordinary (if non-canonical) worktree string still
        # names its own, already-covered problem, rather than crashing.
        #
        # The non-canonical path is BUILT here rather than assumed. This line
        # used to read "/tmp/a/b", which is non-canonical only where /tmp is a
        # symlink: true on macOS, where this test was written, and false on
        # Linux, where /tmp is a real directory and "/tmp/a/b" is genuinely
        # absolute and canonical, so PASS is the correct answer and the
        # calibration failed. The suite is not wired into any workflow, so
        # nothing reported that for however long it has been true. Constructing
        # a real symlink makes the premise hold on every platform and makes the
        # assertion stronger, since it no longer depends on the host's
        # filesystem layout agreeing with the author's.
        link_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, link_root, True)
        real = os.path.join(link_root, "real")
        os.mkdir(real)
        link = os.path.join(link_root, "link")
        os.symlink(real, link)
        non_canonical = os.path.join(link, "b")
        self.assertNotEqual(
            os.path.realpath(non_canonical), non_canonical,
            "the symlink this calibration needs was not built, so the path is "
            "already canonical and the assertion below would prove nothing")
        registry["controllerLeases"][0]["worktree"] = non_canonical
        self.assertEqual(mod.validate_task_registry(registry)[0], "FAIL")

    # 4, MINOR: an unknown version in the field-introduction table ----------
    def test_an_unknown_version_in_the_field_table_refuses_by_name(self):
        saved = mod.LIFECYCLE_FIELDS_INTRODUCED_IN
        mod.LIFECYCLE_FIELDS_INTRODUCED_IN = (("1.2", ("origin",)),)
        try:
            verdict, evidence, problems = mod.validate_passport(a_passport())
            self.assertEqual(verdict, "FAIL", evidence)
            self.assertIn("1.2", evidence)
            self.assertTrue(any("1.2" in p for p in problems), problems)
        finally:
            mod.LIFECYCLE_FIELDS_INTRODUCED_IN = saved
        self.assertEqual(mod.validate_passport(a_passport())[0], "PASS")

    # 5, MINOR: an invented reality state, and either far-end binder fed the
    # other surface's document -----------------------------------------
    def test_reality_binds_refuses_an_invented_reality_state(self):
        self.assertEqual(mod.reality_binds(self.reality, self.contract)[0], "PASS",
                         "calibration: the honest pair must still bind")
        invented = copy.deepcopy(self.reality)
        invented["realityState"] = "EVERYTHING_IS_FINE"
        verdict, evidence, problems = mod.reality_binds(invented, self.contract)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("EVERYTHING_IS_FINE" in p for p in problems), problems)
        self.assertEqual(mod.reality_binds(self.reality, self.contract)[0], "PASS")

    def test_decision_binds_refuses_an_observation_contract_fed_as_the_packet(self):
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS",
                         "calibration: the honest pair must still bind")
        decision = a_decision(self.contract)
        verdict, evidence, problems = mod.decision_binds(decision, self.contract)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("does not validate as a decision packet" in p
                            for p in problems), problems)
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")

    def test_reality_binds_refuses_a_decision_packet_fed_as_the_contract(self):
        self.assertEqual(mod.reality_binds(self.reality, self.contract)[0], "PASS",
                         "calibration: the honest pair must still bind")
        reality = a_reality_state(self.packet)
        verdict, evidence, problems = mod.reality_binds(reality, self.packet)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("does not validate as an observation contract" in p
                            for p in problems), problems)
        self.assertEqual(mod.reality_binds(self.reality, self.contract)[0], "PASS")


class TestRoundThreeAndReplayFindings(unittest.TestCase):
    """The eleven findings from the round-three refuter and the decision
    replay, closed after `TestN1RefuterFindings` above was already green.
    Same discipline as every class above: each fixture below was built and
    run RED against the spine as it stood, using the exact construction
    shown in its own test, before the guard that catches it existed, and the
    honest document or pair is re-checked in the SAME test so a guard that
    refused everything could not pass these either.

    Finding 6 is not here: "status-team format crash: an unexpected team
    field format crashes instead of refusing" did not reproduce. An
    exhaustive fuzz (`test_status_team_does_not_crash_on_any_json_shaped_
    field_value` below) fed every `STATUS_TEAM_FIELDS` name, and
    `schemaVersion`, every JSON-representable value type, and nothing
    raised: `validate_status_team` is `isinstance`-guarded at every point
    that reads a field's shape. The module is left untouched for that
    finding; the fuzz stays as a permanent guard against a future
    regression, not as a red-then-green fix, because there was no red to
    fix.
    """

    def setUp(self):
        self.packet = a_packet()
        self.decision = a_decision(self.packet)
        self.receipt = a_receipt(decisionSha256=mod.canonical_digest(self.decision))
        self.contract = an_observation_contract()
        self.reality = a_reality_state(self.contract)

    # 1, release_binds validated neither of its two documents, and no binder
    # validated the decision document itself -------------------------------
    def test_release_binds_validates_the_receipt_and_the_decision_on_their_own_face(self):
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS",
                         "calibration: the honest pair must still bind")
        bad_receipt = copy.deepcopy(self.receipt)
        bad_receipt["schemaVersion"] = "9.9"
        verdict, evidence, problems = mod.release_binds(bad_receipt, self.decision)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("does not validate as a release receipt" in p
                            for p in problems), problems)
        bad_decision = copy.deepcopy(self.decision)
        bad_decision["schemaVersion"] = "9.9"
        verdict, evidence, problems = mod.release_binds(self.receipt, bad_decision)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("does not validate as a human decision" in p
                            for p in problems), problems)
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")

    def test_decision_binds_also_validates_the_decision_it_binds(self):
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS",
                         "calibration: the honest pair must still bind")
        bad_decision = copy.deepcopy(self.decision)
        bad_decision["schemaVersion"] = "9.9"
        verdict, evidence, problems = mod.decision_binds(bad_decision, self.packet, HEAD)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("does not validate as a human decision" in p
                            for p in problems), problems)
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")

    # 2, reality_binds confused absence with invention ----------------------
    def test_reality_binds_refuses_a_definitive_state_with_no_evidence_behind_it(self):
        self.assertEqual(mod.reality_binds(self.reality, self.contract)[0], "PASS",
                         "calibration: the honest pair must still bind")
        invented = copy.deepcopy(self.reality)
        invented["basis"] = ""
        verdict, evidence, problems = mod.reality_binds(invented, self.contract)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("no evidence produced is invented" in p for p in problems),
                        problems)
        # a DIFFERENT finding, in different words, than basis simply being
        # absent: the two must never share one sentence.
        missing = copy.deepcopy(self.reality)
        del missing["basis"]
        verdict, evidence, problems = mod.validate_verified_reality(missing)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("missing required field(s): basis" in p for p in problems),
                        problems)
        self.assertFalse(any("invented" in p for p in problems), problems)
        # and an honestly unobserved release, citing no evidence, is not
        # refused for it: absence reads as its OWN arm, not as invention.
        not_observed = copy.deepcopy(self.reality)
        not_observed["realityState"] = "REALITY_NOT_OBSERVED"
        not_observed["basis"] = ""
        verdict, evidence, problems = mod.validate_verified_reality(not_observed)
        self.assertEqual(verdict, "PASS", (evidence, problems))
        self.assertEqual(mod.reality_binds(self.reality, self.contract)[0], "PASS")

    # 3, _fields_for crashed or silently mismeasured on a malformed row -----
    def test_fields_for_refuses_malformed_table_rows_instead_of_crashing(self):
        saved = mod.LIFECYCLE_FIELDS_INTRODUCED_IN
        # wrong arity: used to raise ValueError straight out of tuple
        # unpacking (`for version_name, names in ...`).
        mod.LIFECYCLE_FIELDS_INTRODUCED_IN = (("1.1",),)
        try:
            verdict, evidence, problems = mod.validate_passport(a_passport())
        finally:
            mod.LIFECYCLE_FIELDS_INTRODUCED_IN = saved
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("not a (version, field-names) pair" in p for p in problems),
                        problems)

        # names not a list of field names: used to raise TypeError out of
        # `set.update(None)`.
        packet_10 = a_packet(schemaVersion="1.0")
        packet_10.pop("origin")
        mod.LIFECYCLE_FIELDS_INTRODUCED_IN = (("1.1", None),)
        try:
            verdict, evidence, problems = mod.validate_decision_packet(packet_10)
        finally:
            mod.LIFECYCLE_FIELDS_INTRODUCED_IN = saved
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("is not a list of field names" in p for p in problems), problems)
        self.assertEqual(mod.validate_passport(a_passport())[0], "PASS")

    # 4, a body field's own version-introduction table row had no effect ----
    def test_a_body_field_introduced_later_is_optional_for_an_older_document(self):
        packet_10 = a_packet(schemaVersion="1.0")
        packet_10.pop("origin")
        packet_10.pop("notEstablished")
        saved = mod.LIFECYCLE_FIELDS_INTRODUCED_IN
        mod.LIFECYCLE_FIELDS_INTRODUCED_IN = (("1.1", ("origin", "notEstablished")),)
        try:
            verdict, evidence, problems = mod.validate_decision_packet(packet_10)
            self.assertEqual(verdict, "PASS", (evidence, problems))
            # forward-only, never retroactive: the SAME field, missing from a
            # document declaring the CURRENT version (which the table says
            # already carries it), is still required.
            current = a_packet()
            current.pop("notEstablished")
            verdict, evidence, _problems = mod.validate_decision_packet(current)
            self.assertEqual(verdict, "FAIL", evidence)
            self.assertIn("notEstablished", evidence)
        finally:
            mod.LIFECYCLE_FIELDS_INTRODUCED_IN = saved
        self.assertEqual(mod.validate_decision_packet(self.packet)[0], "PASS")

    # 5, the 3.9 floor and 3.13 read the same timestamp as two instants -----
    def test_instant_parses_nanosecond_and_basic_offset_forms_identically_on_the_floor(self):
        # Run this file on BOTH `python3` (3.13) and `/usr/bin/python3` (the
        # 3.9 floor) as its own done-check: every assertion below names a
        # FIXED expected instant, so whichever interpreter disagrees with it
        # fails here, rather than this test merely comparing two live runs
        # against each other.
        from datetime import datetime as dt, timezone as tz
        nanosecond = "2026-08-19T10:00:00.123456789Z"
        self.assertEqual(mod._instant(nanosecond),
                         dt(2026, 8, 19, 10, 0, 0, 123456, tzinfo=tz.utc),
                         mod._instant(nanosecond))

        basic_offset = "2026-08-19T10:00:00+0900"
        self.assertEqual(mod._instant(basic_offset),
                         dt(2026, 8, 19, 1, 0, 0, tzinfo=tz.utc),
                         mod._instant(basic_offset))

        combined = "2026-08-19T10:00:00.123456789+0900"
        self.assertEqual(mod._instant(combined),
                         dt(2026, 8, 19, 1, 0, 0, 123456, tzinfo=tz.utc),
                         mod._instant(combined))

        # and a document carrying one reads the same `declaration_timing`
        # verdict on both interpreters, not `UNDETERMINED` on one (the 3.9
        # floor previously refused to parse a nanosecond-precision
        # timestamp at all) and a real comparison on the other.
        late_nanosecond = an_observation_contract(
            declaredAt="2026-08-19T11:00:01.123456789Z")
        self.assertEqual(mod.declaration_timing(late_nanosecond, self.receipt),
                         "LATE_DECLARATION")

    # 6, NOT-REPRODUCED: see the class docstring -----------------------------
    def test_status_team_does_not_crash_on_any_json_shaped_field_value(self):
        base = {"tool": "t", "root": "r", "headCommit": "h", "changes": [], "findings": [],
               "handover": {}, "planOnly": False, "completedTasks": [], "basisLegend": {}}
        weird_values = (None, 1, 1.5, True, False, "str", [], {}, [1, 2], {"a": 1},
                       [{"a": 1}], [[1, 2]], {"nested": {"deep": [1, {"x": None}]}})
        for field in list(base) + ["schemaVersion"]:
            for value in weird_values:
                mutated = json.loads(json.dumps(dict(base, **{field: value})))
                mod.validate_status_team(mutated)  # must not raise
        self.assertEqual(mod.validate_status_team(base)[0], "PASS")

    # 7, a decision recorded after its own release read as ordinary
    # authorization ----------------------------------------------------------
    def test_release_binds_refuses_a_decision_recorded_after_its_own_release(self):
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS",
                         "calibration: the honest, timely pair must still bind")
        rubber_stamped = copy.deepcopy(self.decision)
        rubber_stamped["createdAt"] = "2026-08-19T11:00:01Z"  # one second after RELEASED_AT
        receipt_matching = a_receipt(decisionSha256=mod.canonical_digest(rubber_stamped))
        verdict, evidence, problems = mod.release_binds(receipt_matching, rubber_stamped)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("rubber stamp" in p for p in problems), problems)
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")

    # 8, the packet validator accepted hollow body content -------------------
    def test_decision_packet_body_fields_route_through_the_central_hollow_rule(self):
        self.assertEqual(mod.validate_decision_packet(self.packet)[0], "PASS",
                         "calibration: the honest packet must still validate")
        whitespace_question = copy.deepcopy(self.packet)
        whitespace_question["question"] = "   "
        verdict, evidence, problems = mod.validate_decision_packet(whitespace_question)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("question" in p for p in problems), problems)

        todo_question = copy.deepcopy(self.packet)
        todo_question["question"] = "TODO"
        verdict, evidence, problems = mod.validate_decision_packet(todo_question)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("question" in p for p in problems), problems)

        empty_risks = copy.deepcopy(self.packet)
        empty_risks["knownRisks"] = []
        verdict, evidence, problems = mod.validate_decision_packet(empty_risks)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("knownRisks" in p for p in problems), problems)
        # routed through the ONE hollow rule (`answered()`), by name, never a
        # second copy.
        self.assertTrue(any("hollow rule this registry uses everywhere else" in p
                            for p in problems), problems)
        self.assertEqual(mod.validate_decision_packet(self.packet)[0], "PASS")

    # 9, decisionSha256 was absent from the receipt binding ------------------
    def test_release_binds_verifies_decisionsha256_against_the_real_decision(self):
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS",
                         "calibration: the honest pair must still bind")
        # `decisionSha256` absent entirely: `release_binds` is where this is
        # required (never `RELEASE_RECEIPT_BINDING`/`validate_release_receipt`,
        # which other consumers of this constant read on a single document
        # with no decision yet to digest against).
        stripped = copy.deepcopy(self.receipt)
        del stripped["decisionSha256"]
        verdict, evidence, problems = mod.release_binds(stripped, self.decision)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("decisionSha256" in p for p in problems), problems)

        # `decisionSha256` present but stale: the decision changed after the
        # receipt was minted, and nothing recomputed it before this fix.
        edited_decision = copy.deepcopy(self.decision)
        edited_decision["producer"] = "a different accountable engineer"
        self.assertNotEqual(mod.canonical_digest(edited_decision),
                            self.receipt["decisionSha256"])
        verdict, evidence, problems = mod.release_binds(self.receipt, edited_decision)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("decision digest" in p for p in problems), problems)
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")

    # 10, producer identity fields bypassed typed-identity validation --------
    def test_producer_identity_is_refused_when_it_is_not_a_string(self):
        self.assertEqual(mod.validate_passport(a_passport())[0], "PASS",
                         "calibration: the honest passport must still validate")
        for value in (0, False, 0.0):
            bad = a_passport(producer=value)
            verdict, evidence, problems = mod.validate_passport(bad)
            self.assertEqual(verdict, "FAIL", (value, evidence))
            self.assertTrue(any("producer" in p and "not a string" in p for p in problems),
                            (value, problems))
        self.assertEqual(mod.validate_passport(a_passport())[0], "PASS")

    # 11, the head check was off by default at the binder --------------------
    def test_decision_binds_head_check_is_on_by_default(self):
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS",
                         "calibration: an explicit, matching head must still bind")
        # omitting head_commit used to silently skip the freshness check; a
        # decision that agrees with the packet but says nothing about where
        # the change stands NOW must not validate PASS in silence.
        verdict, evidence, problems = mod.decision_binds(self.decision, self.packet)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("on by default" in p for p in problems), problems)
        # explicit opt-out is still allowed, and it is visible in the evidence.
        verdict, evidence, problems = mod.decision_binds(self.decision, self.packet,
                                                          skip_head_check=True)
        self.assertEqual(verdict, "PASS", (evidence, problems))
        self.assertIn("explicitly skipped", evidence, evidence)
        self.assertEqual(mod.decision_binds(self.decision, self.packet, HEAD)[0], "PASS")


class TestRoundFourRefuterFindings(unittest.TestCase):
    """The round-four refuter's six findings against `contracts.py`, closed
    the same discipline every class above holds to: the fixture that
    reproduces the finding, built and run RED against the spine as it stood,
    re-checked GREEN here.

    Finding 1's own fixture is different in KIND from the other five: it is
    not one hostile document, it is a whole INTERPRETER. `_instant` used to
    teach the Python 3.9 floor two lenient forms by hand and leave 73 of a
    216-form ISO-8601 corpus disagreeing with 3.13 (a decision `createdAt`
    of `"2026-08-19T21:00:00+09"` flipped `release_binds` from `FAIL` on
    3.13 to `PASS` on 3.9, an hour-only offset the floor could not parse at
    all). `test_instant_agrees_with_the_other_interpreter_over_a_216_form_
    corpus` below is the permanent guard against that regression: it runs
    the SAME corpus through THIS process and through the OTHER Python this
    repository supports, over a real subprocess, and asserts the two answer
    identically, rather than asserting either interpreter's own answer is
    "correct." `skipTest`, never a silent pass, when the other interpreter
    is not on this machine.
    """

    def setUp(self):
        self.packet = a_packet()
        self.decision = a_decision(self.packet)
        self.receipt = a_receipt(decisionSha256=mod.canonical_digest(self.decision))
        self.contract = an_observation_contract()
        self.reality = a_reality_state(self.contract)

    # ---- shared machinery for the two cross-interpreter checks -------------

    @staticmethod
    def _other_interpreter():
        """The absolute path to a Python interpreter whose (major, minor)
        differs from the one running this suite, or None when this machine
        carries none. `/usr/bin/python3` is this repository's pinned 3.9
        floor (the module docstring: "Python floor is 3.9"); `python3`
        resolved from `PATH` is checked too, in case this suite is itself
        being run BY the floor interpreter and the newer one is the one
        missing. Never assumes which one is "the other": it asks each
        candidate its own version and compares against `sys.version_info`.
        """
        this = tuple(sys.version_info[:2])
        seen = set()
        for candidate in ("/usr/bin/python3", shutil.which("python3")):
            if not candidate or candidate in seen or not os.path.exists(candidate):
                continue
            seen.add(candidate)
            try:
                probe = subprocess.run(
                    [candidate, "-c", "import sys; print(sys.version_info[0], "
                                      "sys.version_info[1])"],
                    capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                continue  # sbe: allow-silent a candidate that cannot even be launched is
                # not a usable "other interpreter"; the caller falls through to
                # skipTest, which names the search as not having found one, so
                # nothing here is dropped without being reported
            if probe.returncode != 0:
                continue
            try:
                major, minor = (int(x) for x in probe.stdout.split())
            except ValueError:
                continue  # sbe: allow-silent an unparseable version probe means this
                # candidate cannot be trusted as "the other interpreter" either;
                # same fallthrough to skipTest as the launch failure above
            if (major, minor) != this:
                return candidate
        return None

    @staticmethod
    def _instant_corpus():
        """One BASE date/time crossed with every fractional-seconds spelling
        and every offset spelling the round-four and round-five refuters'
        own sweeps used, including the garbage classes (a double `Z`, a bare
        `UTC`, a unicode minus sign, an out-of-range offset minute): 12
        fractions x 18 offsets = 216 forms, built here rather than typed out
        as 216 literals so a spelling added to either list is covered
        without a second edit. `+09:75` and `-05:99` are round five's own
        addition (finding 4: the grammar used to admit offset minutes 60
        through 99), carried here too so the interpreter-agreement sweep
        covers them alongside the dedicated grammar test in
        `TestRoundFiveRefuterFindings`."""
        base = "2026-08-19T10:00:00"
        fractions = ["", ".1", ".12", ".123", ".1234", ".123456", ".1234567",
                    ".123456789", ",1", ",123", ",123456", ",1234567"]
        offsets = ["Z", "z", "+00:00", "-00:00", "+0900", "-0530", "+09", "-05",
                  "+09:00:00", "+00:00:00", "", " Z", "UTC", "+9:00", "+090000",
                  "−09:00", "+09:75", "-05:99"]
        return [base + f + o for f in fractions for o in offsets]

    # 1, CRITICAL: `_instant` diverged across interpreters -------------------
    def test_instant_agrees_with_the_other_interpreter_over_a_216_form_corpus(self):
        other = self._other_interpreter()
        if other is None:
            self.skipTest("no second Python interpreter found on this machine "
                          "(checked /usr/bin/python3 and PATH's python3); the "
                          "interpreter-agreement sweep was not run")
        corpus = self._instant_corpus()
        here = [None if mod._instant(t) is None else mod._instant(t).isoformat()
               for t in corpus]
        script = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "from brothersbe import contracts as C\n"
            "corpus = json.loads(sys.stdin.read())\n"
            "out = [None if C._instant(t) is None else C._instant(t).isoformat() "
            "for t in corpus]\n"
            "print(json.dumps(out))\n"
        ) % (os.path.join(ROOT, "src"),)
        proc = subprocess.run([other, "-c", script], input=json.dumps(corpus),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        there = json.loads(proc.stdout)
        divergent = [(t, h, o) for t, h, o in zip(corpus, here, there) if h != o]
        self.assertEqual(divergent, [],
                         "%d of %d forms disagree between this interpreter "
                         "(%s) and %s: %r"
                         % (len(divergent), len(corpus), sys.version.split()[0],
                            other, divergent[:10]))
        # calibration: the honest, ordinary form still parses, on THIS
        # interpreter, so a grammar that refused everything could not pass
        # the sweep above either.
        self.assertIsNotNone(mod._instant("2026-08-19T10:00:00Z"))

    # 2, CRITICAL: an unasked timing question read PASS -----------------------
    def test_release_binds_never_silently_skips_the_rubber_stamp_question(self):
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS",
                         "calibration: the honest, timely pair must still bind")
        for label, created in (("offset omitted", "2026-08-20T23:00:00"),
                               ("prose", "whenever, mate"),
                               ("date-only", "2026-08-20")):
            late_no_offset = a_decision(self.packet, createdAt=created)
            receipt = a_receipt(decisionSha256=mod.canonical_digest(late_no_offset))
            verdict, evidence, problems = mod.validate_human_decision(late_no_offset)
            self.assertEqual(verdict, "FAIL", (label, evidence))
            self.assertTrue(any("createdAt" in p and "instant" in p for p in problems),
                            (label, problems))
            verdict, evidence, problems = mod.release_binds(receipt, late_no_offset)
            self.assertEqual(verdict, "FAIL", (label, evidence))
            self.assertTrue(any("cannot be asked" in p for p in problems),
                            (label, problems))
        # control: the SAME lateness, with an offset, is still caught by the
        # ordinary rubber-stamp comparison, not by the new unaskable-timing
        # path.
        late_with_offset = a_decision(self.packet, createdAt="2026-08-20T23:00:00+00:00")
        receipt = a_receipt(decisionSha256=mod.canonical_digest(late_with_offset))
        verdict, evidence, problems = mod.release_binds(receipt, late_with_offset)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("rubber stamp" in p for p in problems), problems)
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")

    # 3, minor: `observedAt` was never instant-checked ------------------------
    def test_verified_reality_instant_checks_observed_at(self):
        self.assertEqual(mod.validate_verified_reality(self.reality)[0], "PASS",
                         "calibration: the honest reality state must still validate")
        for bad in ("sometime last week", "2026-08-26", "2026-08-26T00:00:00", 12345, ""):
            broken = a_reality_state(self.contract, observedAt=bad)
            verdict, evidence, problems = mod.validate_verified_reality(broken)
            self.assertEqual(verdict, "FAIL", (bad, evidence))
            self.assertTrue(any("observedAt" in p and "instant" in p for p in problems),
                            (bad, problems))
        self.assertEqual(mod.validate_verified_reality(self.reality)[0], "PASS")

    # 4, minor: a non-iterable field-introduction table raised ---------------
    def test_fields_for_refuses_a_non_iterable_table_instead_of_raising(self):
        saved = mod.LIFECYCLE_FIELDS_INTRODUCED_IN
        mod.LIFECYCLE_FIELDS_INTRODUCED_IN = None
        try:
            verdict, evidence, problems = mod.validate_passport(a_passport())
            self.assertEqual(verdict, "FAIL", evidence)
            self.assertTrue(any("not iterable" in p for p in problems), problems)
        finally:
            mod.LIFECYCLE_FIELDS_INTRODUCED_IN = saved
        self.assertEqual(mod.validate_passport(a_passport())[0], "PASS")

    # 5, minor: `basis` could be version-scoped out of a definitive claim ----
    def test_basis_is_never_version_scoped_out_of_a_definitive_reality_claim(self):
        self.assertEqual(mod.validate_verified_reality(self.reality)[0], "PASS",
                         "calibration: the honest reality state must still validate")
        saved = mod.LIFECYCLE_FIELDS_INTRODUCED_IN
        mod.LIFECYCLE_FIELDS_INTRODUCED_IN = (("1.1", ("origin", "basis")),)
        try:
            gapped = a_reality_state(self.contract, schemaVersion="1.0")
            del gapped["basis"]
            verdict, evidence, problems = mod.validate_verified_reality(gapped)
            self.assertEqual(verdict, "FAIL", evidence)
            self.assertTrue(any("invented, not verified" in p for p in problems), problems)
            gapped_contract = an_observation_contract(schemaVersion="1.0")
            gapped_reality = a_reality_state(gapped_contract, schemaVersion="1.0")
            del gapped_reality["basis"]
            self.assertEqual(mod.reality_binds(gapped_reality, gapped_contract)[0], "FAIL")
            # a `basis` that is simply MISSING, not version-scoped away, still
            # reports only the missing-field finding, never both in one
            # sentence (the round-three fix this must not regress).
            plain_missing = copy.deepcopy(self.reality)
            del plain_missing["basis"]
        finally:
            mod.LIFECYCLE_FIELDS_INTRODUCED_IN = saved
        verdict, evidence, problems = mod.validate_verified_reality(plain_missing)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("missing required field(s): basis" in p for p in problems),
                        problems)
        self.assertFalse(any("invented" in p for p in problems), problems)
        self.assertEqual(mod.validate_verified_reality(self.reality)[0], "PASS")

    # 6, minor: a NaN's digest-failure text was interpreter-specific ---------
    def test_digest_of_problem_text_names_the_exception_class_not_its_message(self):
        undigestable = {**self.packet, "extra": float("nan")}
        digest, problem = mod._digest_of(undigestable, "decision packet")
        self.assertIsNone(digest)
        self.assertIn("canonically encoded", problem)
        self.assertIn("ValueError", problem)
        # never the interpreter's own wording for the NaN case: 3.13's
        # `json.dumps(..., allow_nan=False)` appends ": nan" to its message
        # and the 3.9 floor does not, which used to make this sentence
        # differ by interpreter over the identical hostile document.
        self.assertNotIn("nan", problem.lower())

        other = self._other_interpreter()
        if other is None:
            self.skipTest("no second Python interpreter found on this machine "
                          "(checked /usr/bin/python3 and PATH's python3); the "
                          "NaN evidence-text parity check was not run")
        script = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "from brothersbe import contracts as C\n"
            "packet = json.loads(sys.stdin.read())\n"
            "packet['extra'] = float('nan')\n"
            "_, problem = C._digest_of(packet, 'decision packet')\n"
            "print(json.dumps(problem))\n"
        ) % (os.path.join(ROOT, "src"),)
        proc = subprocess.run([other, "-c", script], input=json.dumps(self.packet),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        there_problem = json.loads(proc.stdout)
        self.assertEqual(problem, there_problem,
                         "the NaN evidence line differs between this interpreter "
                         "(%s) and %s" % (sys.version.split()[0], other))


class TestRoundFiveRefuterFindings(unittest.TestCase):
    """The round-five refuter's four findings against `contracts.py`, closed
    the same discipline every class above holds to: the fixture that
    reproduces the finding, built and run RED against the spine as it stood
    at `97aba04` (the commit this lane is pinned to), re-checked GREEN here.

    Finding 1 reuses `TestRoundFourRefuterFindings._other_interpreter`
    rather than growing a second copy of that lookup: this class needs the
    same "find the other Python this machine carries" answer, not a second
    idea of how to find it.
    """

    def setUp(self):
        self.packet = a_packet()
        self.decision = a_decision(self.packet)
        self.receipt = a_receipt(decisionSha256=mod.canonical_digest(self.decision))
        self.contract = an_observation_contract()
        self.reality = a_reality_state(self.contract)

    # 1, CRITICAL: `canonical_digest` inherited each interpreter's own
    # int-to-string conversion limit ------------------------------------
    def test_canonical_digest_states_its_own_integer_bound_on_every_interpreter(self):
        # calibration: an ordinary document, no oversized int anywhere in
        # it, still digests and binds normally.
        self.assertEqual(mod.decision_binds(self.decision, self.packet,
                                            skip_head_check=True)[0], "PASS")

        # `10 ** N - 1` builds an N-digit integer through arithmetic, never
        # through a decimal string of that many characters, so building the
        # hostile document itself does not trip Python's own conversion
        # limit before this module's check ever runs.
        huge = 10 ** (mod.MAX_INTEGER_DIGITS + 1) - 1
        hostile_packet = {**self.packet, "hugeInt": huge}

        # `canonical_digest` itself: this module's own bound, in this
        # module's own words, refused before `json.dumps` is ever reached.
        with self.assertRaises(ValueError) as raised:
            mod.canonical_digest(hostile_packet)
        self.assertIn("decimal digit", str(raised.exception))
        self.assertIn(str(mod.MAX_INTEGER_DIGITS), str(raised.exception))

        # `_digest_of`, the one place any authorization path digests
        # anything: the raise above turns into a named problem, not a
        # crash, per `_digest_of`'s own rule of naming only the exception's
        # CLASS (never the message, which is where the interpreter
        # divergence this finding closes used to leak through).
        digest, problem = mod._digest_of(hostile_packet, "decision packet")
        self.assertIsNone(digest)
        self.assertIn("ValueError", problem)

        # the same hostile packet through the actual authorization path
        # (`decision_binds`): a verdict, `FAIL`, never an uncaught
        # exception. `self.decision`'s own `packetSha256` need not match
        # `hostile_packet`: the digest problem is what fails this binder,
        # and the mismatch comparison is skipped once `expected` is None.
        verdict, evidence, problems = mod.decision_binds(self.decision, hostile_packet,
                                                          skip_head_check=True)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("ValueError" in p for p in problems), problems)

        # THE INTERPRETER-DIVERGENT FAILURE ITSELF. Before this fix,
        # `canonical_digest` had no bound of its own: `json.dumps` converted
        # every int through the running interpreter's own conversion limit,
        # which is 4300 digits by default from Python 3.11 onward and
        # UNLIMITED on the 3.9 floor. The identical hostile document
        # therefore used to digest cleanly on 3.9 (`decision_binds` PASS)
        # and raise `ValueError` straight out of `json.dumps` on 3.13
        # (`decision_binds` FAIL, once the raise reached `_digest_of`'s own
        # `except`, or an uncaught crash from any caller that digests
        # directly). Proved here by running the SAME hostile packet through
        # the OTHER Python this repository supports and asserting it
        # refuses too, never that either interpreter's own answer is
        # "correct."
        other = TestRoundFourRefuterFindings._other_interpreter()
        if other is None:
            self.skipTest("no second Python interpreter found on this machine "
                          "(checked /usr/bin/python3 and PATH's python3); the "
                          "oversized-integer agreement check was not run")
        script = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "from brothersbe import contracts as C\n"
            "packet = json.loads(sys.stdin.read())\n"
            "packet['hugeInt'] = 10 ** (C.MAX_INTEGER_DIGITS + 1) - 1\n"
            "try:\n"
            "    C.canonical_digest(packet)\n"
            "    text = None\n"
            "except ValueError as exc:\n"
            "    text = str(exc)\n"
            "print(json.dumps(text))\n"
        ) % (os.path.join(ROOT, "src"),)
        proc = subprocess.run([other, "-c", script], input=json.dumps(self.packet),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        there_text = json.loads(proc.stdout)
        self.assertIsNotNone(there_text,
                             "the other interpreter (%s) did not refuse the same "
                             "oversized integer this interpreter (%s) refuses"
                             % (other, sys.version.split()[0]))
        self.assertIn("decimal digit", there_text)
        self.assertEqual(str(raised.exception), there_text,
                         "this module's own refusal text for the identical hostile "
                         "document differs between this interpreter (%s) and %s: this "
                         "is exactly the divergence MAX_INTEGER_DIGITS exists to close"
                         % (sys.version.split()[0], other))

    # 2, minor: `observedAt` could be version-scoped out of a definitive
    # reality claim with no binder backstop, the same shape `basis` closed -
    def test_observed_at_is_never_version_scoped_out_of_a_definitive_reality_claim(self):
        self.assertEqual(mod.validate_verified_reality(self.reality)[0], "PASS",
                         "calibration: the honest reality state must still validate")
        saved = mod.LIFECYCLE_FIELDS_INTRODUCED_IN
        mod.LIFECYCLE_FIELDS_INTRODUCED_IN = (("1.1", ("origin", "observedAt")),)
        try:
            gapped = a_reality_state(self.contract, schemaVersion="1.0")
            del gapped["observedAt"]
            verdict, evidence, problems = mod.validate_verified_reality(gapped)
            self.assertEqual(verdict, "FAIL", evidence)
            self.assertTrue(any("no instant to date it from" in p for p in problems),
                            problems)
            gapped_contract = an_observation_contract(schemaVersion="1.0")
            gapped_reality = a_reality_state(gapped_contract, schemaVersion="1.0")
            del gapped_reality["observedAt"]
            self.assertEqual(mod.reality_binds(gapped_reality, gapped_contract)[0], "FAIL")
            # `observedAt` that is simply MISSING, not version-scoped away,
            # still reports only the missing-field finding, never both in
            # one sentence (the same rule the `basis` fix already holds to).
            plain_missing = copy.deepcopy(self.reality)
            del plain_missing["observedAt"]
        finally:
            mod.LIFECYCLE_FIELDS_INTRODUCED_IN = saved
        verdict, evidence, problems = mod.validate_verified_reality(plain_missing)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("missing required field(s): observedAt" in p for p in problems),
                        problems)
        self.assertFalse(any("no instant to date it from" in p for p in problems), problems)
        self.assertEqual(mod.validate_verified_reality(self.reality)[0], "PASS")

    # 3, minor: a GENERATOR passed as the field table exhausted on its first
    # read and validated the SAME document PASS then FAIL on consecutive
    # calls -------------------------------------------------------------
    def test_fields_for_materializes_a_generator_table_so_repeated_validations_agree(self):
        # calibration: the honest, real table still validates the honest
        # document, before and after the generator table is swapped in.
        self.assertEqual(mod.validate_passport(a_passport())[0], "PASS")

        saved = mod.LIFECYCLE_FIELDS_INTRODUCED_IN

        def one_shot_rows():
            # a real `for row in LIFECYCLE_FIELDS_INTRODUCED_IN` this table
            # is read through is exactly the shape that exhausts a
            # generator: iterated once, entirely, and never again.
            yield ("1.1", ("origin",))

        mod.LIFECYCLE_FIELDS_INTRODUCED_IN = one_shot_rows()
        try:
            doc = a_passport(schemaVersion="1.0")
            del doc["origin"]  # scoped out of a 1.0 document by the row above
            first = mod.validate_passport(doc)
            second = mod.validate_passport(doc)
        finally:
            mod.LIFECYCLE_FIELDS_INTRODUCED_IN = saved
        self.assertEqual(first[0], "PASS",
                         "the first call must see the row that scopes origin out of a "
                         "1.0 document: %r" % (first,))
        self.assertEqual(second[0], first[0],
                         "a generator table used to validate the SAME document PASS "
                         "then FAIL on consecutive calls: _fields_for exhausted it "
                         "reading it the first time, and the module attribute still "
                         "named the spent generator the second time, so origin was no "
                         "longer scoped out and read as missing instead: %r then %r"
                         % (first, second))
        self.assertEqual(mod.validate_passport(a_passport())[0], "PASS")

    # 4, minor: the instant grammar admitted offset minutes 60 through 99 --
    def test_instant_grammar_refuses_an_out_of_range_offset_minute(self):
        # calibration: an honest offset minute still parses, on both
        # interpreters this repository supports.
        self.assertIsNotNone(mod._instant("2026-08-19T10:00:00+09:59"))
        for bad in ("2026-08-19T10:00:00+09:60", "2026-08-19T10:00:00+09:75",
                   "2026-08-19T10:00:00-05:99"):
            self.assertIsNone(mod._instant(bad),
                             "%r has an offset minute outside 00-59 and must not parse "
                             "as an instant" % (bad,))

        other = TestRoundFourRefuterFindings._other_interpreter()
        if other is None:
            self.skipTest("no second Python interpreter found on this machine "
                          "(checked /usr/bin/python3 and PATH's python3); the "
                          "offset-minute agreement check was not run")
        forms = ["2026-08-19T10:00:00+09:59", "2026-08-19T10:00:00+09:60",
                 "2026-08-19T10:00:00+09:75", "2026-08-19T10:00:00-05:99"]
        here = [mod._instant(t) is not None for t in forms]
        script = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "from brothersbe import contracts as C\n"
            "forms = json.loads(sys.stdin.read())\n"
            "print(json.dumps([C._instant(t) is not None for t in forms]))\n"
        ) % (os.path.join(ROOT, "src"),)
        proc = subprocess.run([other, "-c", script], input=json.dumps(forms),
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        there = json.loads(proc.stdout)
        self.assertEqual(here, there,
                         "this interpreter (%s) and %s disagree on which of %r parse "
                         "as an instant: %r vs %r"
                         % (sys.version.split()[0], other, forms, here, there))
        self.assertEqual(here, [True, False, False, False], (forms, here))


class TestK2rGateBlockerFindings(unittest.TestCase):
    """The Codex K2r gate blocker, reproduced independently against the
    spine as it stood at `0ab8bba` (the commit this lane is pinned to):
    `MAX_INTEGER_DIGITS = 4300` assumed CPython's DEFAULT int-to-string
    conversion limit, not an interpreter's CONFIGURED one. The limit is
    legally configurable, lower as well as higher, through
    `sys.set_int_max_str_digits()` or the `PYTHONINTMAXSTRDIGITS`
    environment variable, down to a documented minimum of 640
    (`sys.int_info.str_digits_check_threshold`). Under a configured limit of
    1000, a 1001-digit integer makes `json.dumps` raise `ValueError` on 3.13
    while an unconfigured 3.9 floor (which has no such mechanism at all)
    still digests it without complaint: `decision_binds` answered `FAIL` on
    the configured 3.13 process and `PASS` on 3.9 for the identical
    document, the exact interpreter-divergence class `MAX_INTEGER_DIGITS`
    exists to close, reopened by a LEGAL configuration this module's own
    4300-digit bound never accounted for.

    Fixed by lowering `MAX_INTEGER_DIGITS` to 600, BELOW 640: every integer
    this module ever hands to `json.dumps` is now at most 600 digits,
    refused by this module's own check before `json.dumps` is ever reached,
    so no configuration on any supported interpreter can make `json.dumps`
    see an oversized integer at all, and the divergence class cannot
    reappear under any legal configuration, present or future.

    Finding 2 below closes a gap adjacent to this one: the round-six
    refuter's boundary fix (the sentence claiming "more than N" used to
    refuse N itself) landed with no PERMANENT regression test at its own
    boundary, only the once-run fixture that proved it at the time. This
    class adds that guard at the NEW boundary this finding moves the bound
    to, so a future bound change cannot silently reopen either the off-by-
    one or the interpreter divergence.
    """

    def setUp(self):
        self.packet = a_packet()
        self.decision = a_decision(self.packet)

    @staticmethod
    def _decision_binds_over_a_hostile_int(interpreter, digit_limit, huge_digits,
                                           packet, decision):
        """`(verdict, digest_error, problems)` for `decision_binds` over
        `packet` with a `huge_digits`-digit integer added, run in a fresh
        `interpreter` subprocess with `PYTHONINTMAXSTRDIGITS` set to
        `digit_limit` (left UNSET, not merely empty, when `digit_limit` is
        None, so "unconfigured" means the interpreter's own default, never
        an empty string CPython would refuse to parse). The huge int is
        built by `10 ** N - 1` INSIDE the subprocess, arithmetic, never a
        decimal string of that many characters crossing the JSON boundary,
        so building the hostile document does not itself trip whatever
        conversion limit is under test. `digest_error` is `canonical_digest`'s
        own raised text (or None); `problems` is what `decision_binds`
        itself reports, which per `_digest_of`'s rule names only the
        exception's CLASS, never its message.
        """
        env = dict(os.environ)
        if digit_limit is None:
            env.pop("PYTHONINTMAXSTRDIGITS", None)
        else:
            env["PYTHONINTMAXSTRDIGITS"] = str(digit_limit)
        script = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "from brothersbe import contracts as C\n"
            "payload = json.loads(sys.stdin.read())\n"
            "packet, decision = payload['packet'], payload['decision']\n"
            "packet['hugeInt'] = 10 ** %d - 1\n"
            "try:\n"
            "    C.canonical_digest(packet)\n"
            "    digest_error = None\n"
            "except (TypeError, ValueError) as exc:\n"
            "    digest_error = str(exc)\n"
            "verdict, evidence, problems = C.decision_binds(decision, packet, "
            "skip_head_check=True)\n"
            "print(json.dumps([verdict, digest_error, problems]))\n"
        ) % (os.path.join(ROOT, "src"), huge_digits)
        proc = subprocess.run([interpreter, "-c", script],
                              input=json.dumps({"packet": packet, "decision": decision}),
                              capture_output=True, text=True, timeout=30, env=env)
        if proc.returncode != 0:
            raise AssertionError(proc.stderr)
        return json.loads(proc.stdout)

    # 1, THE K2r FINDING: a legally configured limit reopened the
    # interpreter divergence MAX_INTEGER_DIGITS exists to close -------------
    def test_a_700_digit_integer_refuses_identically_under_every_legal_configuration(self):
        # calibration: the honest, unmutated pair still binds, on this
        # interpreter, before any hostile integer is added.
        self.assertEqual(mod.decision_binds(self.decision, self.packet,
                                            skip_head_check=True)[0], "PASS")

        other = TestRoundFourRefuterFindings._other_interpreter()
        if other is None:
            self.skipTest("no second Python interpreter found on this machine "
                          "(checked /usr/bin/python3 and PATH's python3); the "
                          "configured-limit agreement check was not run")

        # 700 digits: comfortably under the OLD bound (4300, so the
        # module's own check used to wave it through untouched) and
        # comfortably over 640 (the lowest limit PYTHONINTMAXSTRDIGITS can
        # legally be configured to), so a configured-640 3.13 process used
        # to hand this integer straight to json.dumps, which then raised
        # ITS OWN ValueError, never this module's. It is also over the NEW
        # bound (600), so this module's own check now refuses it before
        # json.dumps is ever reached, on every interpreter and every
        # configuration alike.
        this = sys.executable
        runs = {
            "this interpreter, unconfigured":
                self._decision_binds_over_a_hostile_int(
                    this, None, 700, self.packet, self.decision),
            "this interpreter, configured to 640 (the legal minimum)":
                self._decision_binds_over_a_hostile_int(
                    this, 640, 700, self.packet, self.decision),
            "%s, configured to 640" % other:
                self._decision_binds_over_a_hostile_int(
                    other, 640, 700, self.packet, self.decision),
            "%s, unconfigured (which ignores PYTHONINTMAXSTRDIGITS entirely)"
            % other:
                self._decision_binds_over_a_hostile_int(
                    other, None, 700, self.packet, self.decision),
        }

        verdicts = {label: verdict for label, (verdict, _, __) in runs.items()}
        digest_errors = {label: err for label, (_, err, __) in runs.items()}
        self.assertEqual(len(set(verdicts.values())), 1,
                         "the SAME document must answer the SAME verdict on every "
                         "interpreter and every legal PYTHONINTMAXSTRDIGITS "
                         "configuration; a configured limit split it: %r"
                         % (verdicts,))
        self.assertEqual(list(verdicts.values())[0], "FAIL", verdicts)
        self.assertEqual(len(set(digest_errors.values())), 1,
                         "the refusal text must be this module's OWN words on "
                         "every interpreter and configuration, never json.dumps's "
                         "own version- and configuration-dependent message; "
                         "got %r" % (digest_errors,))
        text = list(digest_errors.values())[0]
        self.assertIn("decimal digit", text, text)
        self.assertIn(str(mod.MAX_INTEGER_DIGITS), text, text)
        # never json.dumps's OWN wording for its own conversion limit, which
        # is exactly what used to leak through and name a different number
        # (the CONFIGURED limit, not this module's bound) on the 640 run.
        self.assertNotIn("for integer string conversion", text, text)

    # 2, PERMANENT boundary fixtures at the NEW bound: 599 and 600 accept
    # and digest, 601 refuses naming 600, on both interpreters --------------
    def test_the_599_600_and_601_digit_boundary_agrees_with_the_other_interpreter(self):
        # calibration, on THIS interpreter, before asking the other one:
        # 599 and 600 digest cleanly.
        for digits in (599, 600):
            value = 10 ** digits - 1
            self.assertEqual(mod._decimal_digit_count(value), digits, digits)
            digest = mod.canonical_digest({"boundaryInt": value})  # must not raise
            self.assertEqual(len(digest), 64, digest)  # a real sha256 hex digest

        # 601 refuses, naming the bound (600), never the interpreter's own
        # int-to-str wording.
        over = 10 ** 601 - 1
        self.assertEqual(mod._decimal_digit_count(over), mod.MAX_INTEGER_DIGITS + 1)
        with self.assertRaises(ValueError) as raised:
            mod.canonical_digest({"boundaryInt": over})
        self.assertIn("more than %d decimal digits" % mod.MAX_INTEGER_DIGITS,
                      str(raised.exception))

        other = TestRoundFourRefuterFindings._other_interpreter()
        if other is None:
            self.skipTest("no second Python interpreter found on this machine "
                          "(checked /usr/bin/python3 and PATH's python3); the "
                          "boundary agreement check was not run")
        script = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "from brothersbe import contracts as C\n"
            "out = {}\n"
            "for digits in (599, 600, 601):\n"
            "    value = 10 ** digits - 1\n"
            "    try:\n"
            "        C.canonical_digest({'boundaryInt': value})\n"
            "        out[digits] = None\n"
            "    except ValueError as exc:\n"
            "        out[digits] = str(exc)\n"
            "print(json.dumps(out))\n"
        ) % (os.path.join(ROOT, "src"),)
        proc = subprocess.run([other, "-c", script], capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        there = json.loads(proc.stdout)
        self.assertIsNone(there["599"], there)
        self.assertIsNone(there["600"], there)
        self.assertIsNotNone(there["601"], there)
        self.assertIn("more than %d decimal digits" % mod.MAX_INTEGER_DIGITS,
                      there["601"])


class TestIntegratedHostileReviewFindingA(unittest.TestCase):
    """Finding A, CRITICAL, of the integrated hostile review: attack 10 of
    the ratified battery ("deployment of different artifact"). Before this
    fix, `RELEASE_RECEIPT_BINDING` declared `artifactSha256` a BINDING field
    and NOTHING in this module ever recomputed or compared it: two release
    receipts for the SAME decided commit, differing only in artifact digest,
    both returned `PASS`, and the `PASS` sentence named the digest it never
    checked. Exactly the failure `reality_binds`'s own docstring already
    legislates against: "a binding nothing recomputes is a citation."

    `release_binds` cannot recompute the digest itself (it never sees the
    released bytes), so the fix is an OPTIONAL `expected_artifact_sha256`
    a caller who DOES know the expected digest can pass: supplied and
    mismatching is `FAIL`, naming both; supplied and matching, the evidence
    says the artifact was checked; not supplied at all, the evidence still
    names the artifact but as RECORDED, never as verified.
    """

    def setUp(self):
        self.packet = a_packet()
        self.decision = a_decision(self.packet)  # producerClass "human"
        self.receipt = a_receipt(producerClass="pipeline",
                                 decisionSha256=mod.canonical_digest(self.decision))

    # 1, THE FINDING, RED THEN GREEN: a swapped artifact digest, with an
    # expected digest supplied, is refused, naming both -------------------
    def test_a_release_of_a_swapped_artifact_is_refused_when_an_expected_digest_is_given(self):
        expected = self.receipt["artifactSha256"]
        honest = mod.release_binds(self.receipt, self.decision,
                                   expected_artifact_sha256=expected)
        self.assertEqual(honest[0], "PASS", honest[1])

        swapped = copy.deepcopy(self.receipt)
        # a real (canonical, 64 lowercase hex) digest, just a different one
        # than `expected`: this test is about a MISMATCH between two real
        # digests, not about either one's shape (`TestArtifactDigestShapeGuard`
        # below owns the shape question).
        swapped["artifactSha256"] = "d" * 64
        verdict, evidence, problems = mod.release_binds(
            swapped, self.decision, expected_artifact_sha256=expected)
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any(swapped["artifactSha256"] in p and expected in p
                            for p in problems), problems)
        # calibration held afterward: the honest pairing still binds
        self.assertEqual(mod.release_binds(self.receipt, self.decision,
                                           expected_artifact_sha256=expected)[0],
                         "PASS")

    # 2, THE FIX'S OTHER HALF: with no expected digest given, the PASS
    # evidence must not read as though the artifact were verified ---------
    def test_without_an_expected_digest_the_pass_evidence_names_the_artifact_unverified(self):
        verdict, evidence, _problems = mod.release_binds(self.receipt, self.decision)
        self.assertEqual(verdict, "PASS", evidence)
        self.assertIn(self.receipt["artifactSha256"], evidence)
        self.assertIn("not verified", evidence)
        self.assertNotIn("checked", evidence)

        # and a caller that DOES supply a matching digest gets evidence
        # that reads differently, saying the artifact was actually checked
        checked_verdict, checked_evidence, _ = mod.release_binds(
            self.receipt, self.decision,
            expected_artifact_sha256=self.receipt["artifactSha256"])
        self.assertEqual(checked_verdict, "PASS", checked_evidence)
        self.assertIn("checked", checked_evidence)
        self.assertNotIn("not verified", checked_evidence)

    # 3: an expected digest that is itself hollow (empty string) is refused
    # rather than silently read as "no expectation at all" -----------------
    def test_an_empty_expected_artifact_digest_is_refused_not_ignored(self):
        verdict, evidence, problems = mod.release_binds(
            self.receipt, self.decision, expected_artifact_sha256="")
        self.assertEqual(verdict, "FAIL", evidence)
        self.assertTrue(any("expected artifact digest" in p for p in problems), problems)
        self.assertEqual(mod.release_binds(self.receipt, self.decision)[0], "PASS")


class TestArtifactDigestShapeGuard(unittest.TestCase):
    """Adversarial review finding on `_artifact_digest_problem` (the rule
    `release_binds` and `evidence.verify_release_receipt_for_release` share):
    an EQUALITY-ONLY comparison let two equal strings that were never
    digests at all reach the match arm and report a digest "checked ...
    and matching". The reviewer's own reproduction: `artifactSha256=
    "banana"` paired with `expected_artifact_sha256="banana"`. This class is
    `release_binds`'s share of the fix; `TestVerifyReleaseReceiptForRelease
    ArtifactDigest` in `tools/test_sbe_evidence.py` is the other caller's.

    THE WEAKNESS WAS INHERITED, not introduced by this class: the equality-
    only comparison lived inline in `release_binds` before `TestIntegrated
    HostileReviewFindingA`'s own fix extracted it into `_artifact_digest_
    problem`, so `release_binds` has always had it. Fixing the one place
    both callers now share fixes both at once, which is exactly why this
    class exercises `release_binds`, not the private helper directly (no
    test anywhere in this suite calls `_artifact_digest_problem` itself;
    every arm is proved through a public caller, same as always).
    """

    WELL_FORMED = "f" * 64  # `a_receipt()`'s own default artifactSha256

    def setUp(self):
        self.packet = a_packet()
        self.decision = a_decision(self.packet)
        self.receipt = a_receipt(decisionSha256=mod.canonical_digest(self.decision))

    def _swapped(self, artifact_sha256):
        swapped = copy.deepcopy(self.receipt)
        swapped["artifactSha256"] = artifact_sha256
        return swapped

    # 1, THE REVIEWER'S OWN CASE, RED THEN GREEN: two equal strings that are
    # not digests at all must never reach a PASS that claims one was checked
    def test_the_reviewers_equal_but_malformed_case_does_not_reach_pass(self):
        swapped = self._swapped("banana")
        verdict, evidence, problems = mod.release_binds(
            swapped, self.decision, expected_artifact_sha256="banana")
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertNotIn("checked against the expected digest and matching", evidence)
        # calibration: the honest, well-formed pairing still binds
        self.assertEqual(mod.release_binds(
            self.receipt, self.decision,
            expected_artifact_sha256=self.receipt["artifactSha256"])[0], "PASS")

    # 2, RED THEN GREEN: a malformed RECORDED value is a defective document
    def test_a_malformed_recorded_digest_is_named_a_defective_document(self):
        swapped = self._swapped("banana")
        verdict, evidence, problems = mod.release_binds(
            swapped, self.decision, expected_artifact_sha256=self.WELL_FORMED)
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertTrue(any("defective document" in p for p in problems), problems)

    # 3, RED THEN GREEN: a malformed EXPECTED value is a defective caller,
    # never blamed on the document that recorded a perfectly good digest
    def test_a_malformed_expected_digest_is_named_a_defective_caller(self):
        verdict, evidence, problems = mod.release_binds(
            self.receipt, self.decision, expected_artifact_sha256="banana")
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertTrue(any("defective caller" in p for p in problems), problems)

    # 4, RED THEN GREEN: the three boundary shapes the brief names by name
    def test_boundary_lengths_and_a_non_hex_character_are_refused(self):
        too_short = "a" * 63
        too_long = "a" * 65
        one_non_hex_char = "g" + "a" * 63
        for malformed in (too_short, too_long, one_non_hex_char):
            with self.subTest(malformed=malformed, length=len(malformed)):
                swapped = self._swapped(malformed)
                verdict, evidence, problems = mod.release_binds(
                    swapped, self.decision, expected_artifact_sha256=self.WELL_FORMED)
                self.assertEqual(verdict, "FAIL", (evidence, problems))
                self.assertTrue(any("defective document" in p for p in problems), problems)

    # 5, RED THEN GREEN, THE CASE DECISION: uppercase hex is refused, not
    # folded, even when both sides carry the identical uppercase string (so
    # a naive equality check, or a case-insensitive fold, would have let
    # this one through as a "match")
    def test_uppercase_hex_is_refused_even_though_the_strings_are_equal(self):
        swapped = self._swapped("F" * 64)
        verdict, evidence, problems = mod.release_binds(
            swapped, self.decision, expected_artifact_sha256="F" * 64)
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertTrue(any("defective document" in p for p in problems), problems)

    # 6, THE THREE EXISTING ARMS, UNCHANGED: calibration that this guard did
    # not touch the wording `TestIntegratedHostileReviewFindingA` already
    # locks in for "no expectation", "empty expectation" and "genuine
    # mismatch of two well-formed digests" -- those three tests are that
    # proof and are left exactly as they were; this is the fourth arm only.

    # 7, ORDERING: `release_binds` does not short-circuit (it collects every
    # problem it finds), so "an earlier refusal still wins" here means the
    # new problem does not SWALLOW an older one -- both are still named,
    # side by side, in the same `problems` tuple.
    def test_a_wrong_released_commit_problem_survives_alongside_a_malformed_digest(self):
        swapped = self._swapped("banana")
        swapped["releasedCommit"] = OTHER_HEAD
        verdict, evidence, problems = mod.release_binds(
            swapped, self.decision, expected_artifact_sha256="banana")
        self.assertEqual(verdict, "FAIL", (evidence, problems))
        self.assertTrue(any("what shipped is not what was decided on" in p
                            for p in problems), problems)
        self.assertTrue(any("defective document" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
