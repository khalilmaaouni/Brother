#!/usr/bin/env python3
"""Calibration for scripts/orchestrator_protocol.py and the three schemas
it enforces (docs/schema/night-run-v1.json, orchestrator-task-v1.json,
orchestrator-event-v1.json).

The property this file exists to assert is not that a valid document
passes (any validator can be made to do that), it is that the five bad
states a permissive validator would ALSO pass are each refused: a
manifest missing hard_stop_jst, an unknown task_class read as
"implementation", an unknown evidence_obligation read as OPTIONAL, an
extra undeclared field let through because additionalProperties was
forgotten somewhere, and a missing or malformed schema file reported as
"valid" instead of raising. TestEnumsMatchInvariants guards the other
failure mode: the schema's own enums and
scripts/orchestrator_invariants.py silently drifting apart.

Folded in after an independent adversarial review of a provider-neutral
draft of these same three schemas (six findings applied as requirements,
one design gap accepted): a malformed 'at' timestamp must be refused even
though JSON Schema's own 'format' keyword is annotation-only and enforces
nothing (TestMalformedTimestampIsRefused); the event oneOf's two action
shapes must require identical identity fields and must never both match
one document (TestOneOfBranchesMutuallyExclusive); task_id is required
only for actions that name a task and forbidden otherwise
(TestTaskIdConditionalRequirement); a task's id and an action's task_id
are the same join key (TestTaskIdJoinKeyConsistency); id, title,
objective and the entries of owns and depends_on reject the empty string
(TestMinLengthOnIdentifiers); and a task-scoped action now carries
observed_task_state and observed_attempt, a fourth precondition
scoped to the task entity, because observed_revision,
observed_journal_seq and epoch alone describe the run and the
orchestrator, never the task, and so cannot by themselves catch two
reasoners racing on the same task (TestObservedTaskStateAndAttempt).

Corrected once more after that round: QUEUE-HUMAN was first folded into
the task-scoped action branch on the reasoning that a founder escalation
is almost always about one blocked unit. That reasoning was flagged
rather than assumed, and the coordinator overturned it: the steering
document's own acceptance scenario has a RUN-level QUEUE-HUMAN (a RED
publication decision, naming no task at all), so QUEUE-HUMAN now has its
own branch, itself split into a run-level shape (no task_id) and a
task-scoped shape (task_id required, carrying the same staleness fields)
(TestQueueHumanCanBeRunLevelOrTaskScoped). TestOneOfBranchesMutuallyExclusive
was extended, not assumed to still hold, to cover the new branch and its
own internal two-way split.
"""
import copy
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import orchestrator_invariants as inv  # noqa: E402
import orchestrator_protocol as op  # noqa: E402
import evidence_obligation  # noqa: E402


def event_branches(schema):
    """The four branches of orchestrator-event-v1.json's top level oneOf,
    picked out by shape rather than by list position: the journal branch
    declares "event"; the QUEUE-HUMAN branch declares its own nested
    "oneOf" instead of "properties" (it has no single shape of its own);
    the task-scoped action branch declares "observed_task_state"; the
    no-task action branch declares "action" but none of the other three
    markers.
    """
    journal = task_scoped = no_task = queue_human = None
    for branch in schema["oneOf"]:
        if "oneOf" in branch:
            queue_human = branch
            continue
        props = branch.get("properties", {})
        if "event" in props:
            journal = branch
        elif "observed_task_state" in props:
            task_scoped = branch
        elif "action" in props:
            no_task = branch
    return journal, task_scoped, no_task, queue_human


def queue_human_branches(wrapper):
    """The two sub-branches of the QUEUE-HUMAN wrapper: (run_level,
    task_scoped), picked out the same way event_branches picks its own:
    by which fields the branch declares, not by list position.
    """
    run_level = task_scoped = None
    for branch in wrapper["oneOf"]:
        if "task_id" in branch.get("properties", {}):
            task_scoped = branch
        else:
            run_level = branch
    return run_level, task_scoped


def matches_branch(document, branch):
    """True when `document` validates clean against one oneOf branch in
    isolation, using the same recursive checker validate() uses
    internally. Used to prove oneOf exclusivity directly, rather than only
    through validate()'s own oneOf bookkeeping.
    """
    problems = []
    op._validate_node(document, branch, "", problems)
    return not problems


def valid_manifest():
    return {
        "schema_version": "night-run-v1",
        "run_id": "run-2026-09-18",
        "goal": "ship the 1.0.20 orchestration control plane",
        "canonical_repo": "brother-hub",
        "base_revision": "61a4a4d451f4ac599179184401fa734a1db8bcf5",
        "hard_stop_jst": "07:00",
        "orchestrators": ["orch00", "orch01"],
        "builder_cap_policy": "fixed",
        "default_builder_cap": 3,
        "integration_cap": 1,
        "max_outer_attempts": 3,
        "max_same_strategy_attempts": 2,
        "red_actions": "refuse-and-queue",
        "mandatory_evidence": True,
        "cross_review_risk_classes": ["high", "critical"],
    }


def valid_task():
    return {
        "id": "ORCH-01",
        "title": "night-run-v1 contract and validator",
        "objective": "implement the three schemas and a validator",
        "depends_on": ["ORCH-00"],
        "owns": ["docs/schema/night-run-v1.json"],
        "done_check": "python3 scripts/test_orchestrator_protocol.py -v",
        "task_class": "implementation",
        "risk_class": "high",
        "evidence_obligation": "REQUIRED_FOR_MERGE",
        "worker_profile": "sonnet-high",
        "review_profile": "opus-high",
        "max_outer_attempts": 3,
        "max_repair_attempts": 1,
        "leaf_worker_only": True,
    }


def valid_journal_event():
    return {
        "schema_version": "orchestrator-event-v1",
        "run_id": "run-2026-09-18",
        "orchestrator": "orch00",
        "instance": "orch00-a",
        "epoch": 3,
        "at": "2026-09-18T05:22:00+09:00",
        "event": "task.dispatched",
    }


def valid_action_event():
    """A valid task-scoped action envelope (DISPATCH names a task)."""
    return {
        "schema_version": "orchestrator-event-v1",
        "run_id": "run-2026-09-18",
        "orchestrator": "orch00",
        "instance": "orch00-a",
        "epoch": 3,
        "at": "2026-09-18T05:22:00+09:00",
        "action": "DISPATCH",
        "task_id": "ORCH-02",
        "reason": "ready and unclaimed",
        "observed_revision": "61a4a4d",
        "observed_journal_seq": 12,
        "observed_task_state": "READY",
        "observed_attempt": 1,
    }


def valid_no_task_action_event():
    """A valid no-task action envelope (CLOSEOUT names no task)."""
    return {
        "schema_version": "orchestrator-event-v1",
        "run_id": "run-2026-09-18",
        "orchestrator": "orch00",
        "instance": "orch00-a",
        "epoch": 3,
        "at": "2026-09-18T05:22:00+09:00",
        "action": "CLOSEOUT",
        "reason": "run reached hard stop with nothing left ready",
        "observed_revision": "61a4a4d",
        "observed_journal_seq": 12,
    }


def valid_queue_human_run_level_event():
    """A valid run-level QUEUE-HUMAN envelope: no task_id, because the
    decision belongs to the run as a whole, not to any one unit. Driven
    by the steering document's own acceptance scenario: a RED publication
    decision, refused and queued rather than acted on automatically."""
    return {
        "schema_version": "orchestrator-event-v1",
        "run_id": "run-2026-09-18",
        "orchestrator": "fable",
        "instance": "fable-a",
        "epoch": 1,
        "at": "2026-09-18T05:22:00+09:00",
        "action": "QUEUE-HUMAN",
        "reason": "RED publication decision: releasing 1.0.20 publicly "
                  "needs the founder's own sign-off, refused and queued "
                  "rather than acted on automatically",
        "observed_revision": "61a4a4d",
        "observed_journal_seq": 12,
    }


def valid_queue_human_task_scoped_event():
    """A valid task-scoped QUEUE-HUMAN envelope: an escalation about one
    specific blocked unit, carrying the same staleness fields every other
    task-scoped action carries."""
    return {
        "schema_version": "orchestrator-event-v1",
        "run_id": "run-2026-09-18",
        "orchestrator": "fable",
        "instance": "fable-a",
        "epoch": 1,
        "at": "2026-09-18T05:22:00+09:00",
        "action": "QUEUE-HUMAN",
        "task_id": "ORCH-07",
        "reason": "unit is stuck awaiting a credential only the founder "
                  "can supply",
        "observed_revision": "61a4a4d",
        "observed_journal_seq": 12,
        "observed_task_state": "AWAITING-HUMAN",
        "observed_attempt": 2,
    }


class TestValidDocumentsPassClean(unittest.TestCase):
    def test_valid_manifest_validates_clean(self):
        self.assertEqual(op.validate(valid_manifest(), "night-run-v1"), [])

    def test_valid_task_validates_clean(self):
        self.assertEqual(
            op.validate(valid_task(), "orchestrator-task-v1"), [])

    def test_valid_journal_event_validates_clean(self):
        self.assertEqual(
            op.validate(valid_journal_event(), "orchestrator-event-v1"), [])

    def test_valid_action_event_validates_clean(self):
        self.assertEqual(
            op.validate(valid_action_event(), "orchestrator-event-v1"), [])

    def test_valid_no_task_action_event_validates_clean(self):
        self.assertEqual(
            op.validate(valid_no_task_action_event(), "orchestrator-event-v1"),
            [])

    def test_valid_queue_human_run_level_event_validates_clean(self):
        self.assertEqual(
            op.validate(valid_queue_human_run_level_event(),
                        "orchestrator-event-v1"), [])

    def test_valid_queue_human_task_scoped_event_validates_clean(self):
        self.assertEqual(
            op.validate(valid_queue_human_task_scoped_event(),
                        "orchestrator-event-v1"), [])


class TestMissingHardStopIsRefused(unittest.TestCase):
    # (a) A manifest with NO hard_stop_jst is REFUSED, never defaulted.
    def test_missing_hard_stop_jst_is_refused(self):
        doc = valid_manifest()
        del doc["hard_stop_jst"]
        errors = op.validate(doc, "night-run-v1")
        self.assertTrue(errors, "a manifest with no hard_stop_jst must not "
                                 "validate clean")
        self.assertTrue(any("hard_stop_jst" in e for e in errors), errors)

    def test_blank_hard_stop_jst_does_not_match_pattern(self):
        # Name the bad state a looser check would also pass: an empty
        # string is present (so a bare "required" check alone would miss
        # it) but must still fail the clock-format pattern.
        doc = valid_manifest()
        doc["hard_stop_jst"] = ""
        errors = op.validate(doc, "night-run-v1")
        self.assertTrue(errors)
        self.assertTrue(any("hard_stop_jst" in e for e in errors), errors)

    def test_out_of_range_hard_stop_jst_does_not_match_pattern(self):
        doc = valid_manifest()
        doc["hard_stop_jst"] = "25:00"
        errors = op.validate(doc, "night-run-v1")
        self.assertTrue(errors)
        self.assertTrue(any("hard_stop_jst" in e for e in errors), errors)


class TestUnknownTaskClassIsRefused(unittest.TestCase):
    # (b) An unknown task_class is REFUSED, never silently "implementation".
    def test_unknown_task_class_is_refused(self):
        doc = valid_task()
        doc["task_class"] = "not-a-real-class"
        errors = op.validate(doc, "orchestrator-task-v1")
        self.assertTrue(errors)
        self.assertTrue(any("task_class" in e for e in errors), errors)

    def test_unknown_task_class_does_not_secretly_pass_as_implementation(self):
        # The bad state a permissive validator would also pass: reading
        # the same document with a bogus class DOES NOT come back clean
        # just because "implementation" happens to be a valid value for
        # this field elsewhere.
        bad = valid_task()
        bad["task_class"] = "not-a-real-class"
        good = valid_task()
        good["task_class"] = "implementation"
        self.assertNotEqual(
            op.validate(bad, "orchestrator-task-v1"),
            op.validate(good, "orchestrator-task-v1"))
        self.assertEqual(op.validate(good, "orchestrator-task-v1"), [])


class TestUnknownEvidenceObligationIsRefused(unittest.TestCase):
    # (c) An unknown evidence_obligation is REFUSED, never silently OPTIONAL.
    def test_unknown_evidence_obligation_is_refused(self):
        doc = valid_task()
        doc["evidence_obligation"] = "SORT_OF_REQUIRED"
        errors = op.validate(doc, "orchestrator-task-v1")
        self.assertTrue(errors)
        self.assertTrue(
            any("evidence_obligation" in e for e in errors), errors)

    def test_unknown_evidence_obligation_does_not_secretly_pass_as_optional(self):
        bad = valid_task()
        bad["evidence_obligation"] = "SORT_OF_REQUIRED"
        optional = valid_task()
        optional["evidence_obligation"] = "OPTIONAL"
        self.assertNotEqual(
            op.validate(bad, "orchestrator-task-v1"),
            op.validate(optional, "orchestrator-task-v1"))
        self.assertEqual(op.validate(optional, "orchestrator-task-v1"), [])


class TestExtraPropertyIsRefused(unittest.TestCase):
    # (d) An extra, undeclared property is REFUSED: additionalProperties
    # is false, so a typo'd field name must not pass as a new field.
    def test_extra_property_on_manifest_is_refused(self):
        doc = valid_manifest()
        doc["hard_stop_jts"] = "07:00"  # typo of hard_stop_jst
        errors = op.validate(doc, "night-run-v1")
        self.assertTrue(errors)
        self.assertTrue(any("hard_stop_jts" in e for e in errors), errors)

    def test_extra_property_on_task_is_refused(self):
        doc = valid_task()
        doc["extra_undeclared_field"] = True
        errors = op.validate(doc, "orchestrator-task-v1")
        self.assertTrue(errors)
        self.assertTrue(
            any("extra_undeclared_field" in e for e in errors), errors)

    def test_extra_property_on_event_is_refused(self):
        doc = valid_journal_event()
        doc["extra_undeclared_field"] = True
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)


class TestBrokenSchemaFileRaises(unittest.TestCase):
    # (e) A schema file that is missing or malformed JSON raises
    # ProtocolError. It never returns "valid" and never returns an empty
    # error list.
    def test_missing_schema_file_raises(self):
        with self.assertRaises(op.ProtocolError):
            op.load_schema("no-such-schema-exists")

    def test_missing_schema_file_via_validate_raises(self):
        with self.assertRaises(op.ProtocolError):
            op.validate(valid_manifest(), "no-such-schema-exists")

    def test_malformed_schema_json_raises(self):
        tmpdir = tempfile.mkdtemp()
        try:
            broken_path = os.path.join(tmpdir, "broken-schema.json")
            with open(broken_path, "w", encoding="utf-8") as handle:
                handle.write("{ this is not valid json")
            original_dir = op.SCHEMA_DIR
            op.SCHEMA_DIR = tmpdir
            try:
                with self.assertRaises(op.ProtocolError):
                    op.load_schema("broken-schema")
            finally:
                op.SCHEMA_DIR = original_dir
        finally:
            for name in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, name))
            os.rmdir(tmpdir)

    def test_schema_that_is_not_a_json_object_raises(self):
        tmpdir = tempfile.mkdtemp()
        try:
            not_object_path = os.path.join(tmpdir, "not-object.json")
            with open(not_object_path, "w", encoding="utf-8") as handle:
                json.dump([1, 2, 3], handle)
            original_dir = op.SCHEMA_DIR
            op.SCHEMA_DIR = tmpdir
            try:
                with self.assertRaises(op.ProtocolError):
                    op.load_schema("not-object")
            finally:
                op.SCHEMA_DIR = original_dir
        finally:
            for name in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, name))
            os.rmdir(tmpdir)


class TestEnumsMatchInvariants(unittest.TestCase):
    # The schema's own enums and orchestrator_invariants.py's sets must
    # never be able to drift apart silently.
    def test_task_class_enum_equals_invariants(self):
        schema = op.load_schema("orchestrator-task-v1")
        enum_values = set(schema["properties"]["task_class"]["enum"])
        self.assertEqual(enum_values, set(inv.TASK_CLASSES))

    def test_evidence_obligation_enum_equals_invariants(self):
        # If this goes red alone (while the evidence_obligation.LEVELS
        # test below stays green), that means orchestrator_invariants.py
        # is mid-edit elsewhere (it is being changed, outside this unit's
        # scope, to derive EVIDENCE_OBLIGATIONS from evidence_obligation
        # itself): re-run rather than editing that file from here.
        schema = op.load_schema("orchestrator-task-v1")
        enum_values = set(schema["properties"]["evidence_obligation"]["enum"])
        self.assertEqual(enum_values, set(inv.EVIDENCE_OBLIGATIONS))

    def test_evidence_obligation_enum_equals_evidence_obligation_levels(self):
        # The estate's one frozen definition site (EV-3,
        # docs/decisions/evidence-vocabulary-2026-09-13.json) is
        # scripts/evidence_obligation.py's LEVELS, not
        # orchestrator_invariants.py. This schema must match LEVELS
        # directly and exactly: three values, no REQUIRED_FOR_DELIVERY.
        schema = op.load_schema("orchestrator-task-v1")
        enum_values = set(schema["properties"]["evidence_obligation"]["enum"])
        self.assertEqual(enum_values, set(evidence_obligation.LEVELS))
        self.assertEqual(len(enum_values), 3)
        self.assertNotIn("REQUIRED_FOR_DELIVERY", enum_values)

    def test_action_enums_together_equal_invariants_with_no_overlap(self):
        # ACTIONS is now split three ways: the always-task-scoped branch,
        # the no-task branch, and QUEUE-HUMAN's own branch (finding 4b,
        # then reopened for QUEUE-HUMAN specifically). Their union must
        # still equal the full ACTIONS set, and no action name may appear
        # in two DIFFERENT top-level branches (QUEUE-HUMAN's own two
        # nested sub-branches sharing "QUEUE-HUMAN" is fine and expected:
        # they are distinguished by task_id's presence, not by action
        # name, and TestOneOfBranchesMutuallyExclusive proves they can
        # never both match one document).
        schema = op.load_schema("orchestrator-event-v1")
        _journal, task_scoped, no_task, queue_human = event_branches(schema)
        self.assertIsNotNone(task_scoped, schema["oneOf"])
        self.assertIsNotNone(no_task, schema["oneOf"])
        self.assertIsNotNone(queue_human, schema["oneOf"])
        task_scoped_actions = set(task_scoped["properties"]["action"]["enum"])
        no_task_actions = set(no_task["properties"]["action"]["enum"])
        run_level, task_scoped_qh = queue_human_branches(queue_human)
        queue_human_actions = {
            run_level["properties"]["action"]["const"],
            task_scoped_qh["properties"]["action"]["const"],
        }
        self.assertEqual(queue_human_actions, {"QUEUE-HUMAN"})
        self.assertEqual(
            task_scoped_actions & no_task_actions, set(),
            "an action name must not appear in both of these two branches")
        self.assertEqual(
            task_scoped_actions & queue_human_actions, set(),
            "QUEUE-HUMAN must not also appear in the task-scoped branch")
        self.assertEqual(
            no_task_actions & queue_human_actions, set(),
            "QUEUE-HUMAN must not also appear in the no-task branch")
        self.assertEqual(
            task_scoped_actions | no_task_actions | queue_human_actions,
            set(inv.ACTIONS))

    def test_observed_task_state_enum_equals_invariants(self):
        schema = op.load_schema("orchestrator-event-v1")
        _journal, task_scoped, _no_task, _queue_human = event_branches(schema)
        self.assertIsNotNone(task_scoped, schema["oneOf"])
        enum_values = set(
            task_scoped["properties"]["observed_task_state"]["enum"])
        self.assertEqual(enum_values, set(inv.TASK_STATES))

    def test_queue_human_task_scoped_observed_task_state_equals_invariants(self):
        schema = op.load_schema("orchestrator-event-v1")
        _journal, _task_scoped, _no_task, queue_human = event_branches(schema)
        _run_level, task_scoped_qh = queue_human_branches(queue_human)
        enum_values = set(
            task_scoped_qh["properties"]["observed_task_state"]["enum"])
        self.assertEqual(enum_values, set(inv.TASK_STATES))


class TestValidateManifestFromDisk(unittest.TestCase):
    def test_valid_manifest_on_disk_reads_ok(self):
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "night-run.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(valid_manifest(), handle)
            ok, errors = op.validate_manifest(path)
            self.assertTrue(ok, errors)
            self.assertEqual(errors, [])
        finally:
            for name in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, name))
            os.rmdir(tmpdir)

    def test_missing_manifest_file_returns_false_never_raises(self):
        ok, errors = op.validate_manifest(
            "/no/such/directory/night-run.json")
        self.assertFalse(ok)
        self.assertTrue(errors)
        self.assertTrue(any("not found" in e for e in errors), errors)

    def test_malformed_manifest_json_returns_false_never_raises(self):
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "night-run.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{ not valid json")
            ok, errors = op.validate_manifest(path)
            self.assertFalse(ok)
            self.assertTrue(errors)
            self.assertTrue(any("not valid JSON" in e for e in errors), errors)
        finally:
            for name in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, name))
            os.rmdir(tmpdir)

    def test_manifest_that_is_a_json_array_returns_false(self):
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "night-run.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([1, 2, 3], handle)
            ok, errors = op.validate_manifest(path)
            self.assertFalse(ok)
            self.assertTrue(
                any("not a JSON object" in e for e in errors), errors)
        finally:
            for name in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, name))
            os.rmdir(tmpdir)

    def test_missing_hard_stop_on_disk_is_refused_not_ok(self):
        doc = valid_manifest()
        del doc["hard_stop_jst"]
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "night-run.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(doc, handle)
            ok, errors = op.validate_manifest(path)
            self.assertFalse(ok)
            self.assertTrue(errors)
        finally:
            for name in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, name))
            os.rmdir(tmpdir)


class TestMalformedTimestampIsRefused(unittest.TestCase):
    # Finding 1: JSON Schema's "format" keyword is annotation-only in
    # draft 2020-12; a hand-written validator that never checks it would
    # accept "not-a-date-time" as a timestamp. This schema enforces "at"
    # with an explicit "pattern" instead, and the validator's generic
    # pattern support (already used for hard_stop_jst) checks it.
    def test_junk_at_on_journal_event_is_refused(self):
        doc = valid_journal_event()
        doc["at"] = "not-a-date-time-at-all"
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_junk_at_on_task_scoped_action_is_refused(self):
        doc = valid_action_event()
        doc["at"] = "not-a-date-time-at-all"
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_junk_at_on_no_task_action_is_refused(self):
        doc = valid_no_task_action_event()
        doc["at"] = "not-a-date-time-at-all"
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_the_exact_coordinator_repro_is_refused(self):
        # The literal document reported as a false pass: a heartbeat
        # journal event carrying "at": "not-a-date-time-at-all".
        doc = {
            "schema_version": "orchestrator-event-v1", "run_id": "r1",
            "orchestrator": "fable", "instance": "i1", "epoch": 1,
            "event": "orchestrator.heartbeat", "at": "not-a-date-time-at-all",
        }
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors, "a malformed 'at' must never validate clean")

    def test_valid_at_with_z_suffix_is_accepted(self):
        doc = valid_journal_event()
        doc["at"] = "2026-09-18T05:22:00Z"
        self.assertEqual(op.validate(doc, "orchestrator-event-v1"), [])

    def test_empty_at_is_refused(self):
        doc = valid_journal_event()
        doc["at"] = ""
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)


class TestOneOfBranchesMutuallyExclusive(unittest.TestCase):
    # Finding 4, reopened when QUEUE-HUMAN got its own branch: prove, not
    # assume, that no document can satisfy more than one of the four
    # top-level event shapes at once (which would make the oneOf
    # ambiguous and validate() would refuse it as "matches more than one
    # shape" rather than accepting it), and that QUEUE-HUMAN's own two
    # nested sub-branches are exclusive of each other too.
    def test_valid_journal_event_matches_only_the_journal_branch(self):
        schema = op.load_schema("orchestrator-event-v1")
        journal, task_scoped, no_task, queue_human = event_branches(schema)
        doc = valid_journal_event()
        self.assertTrue(matches_branch(doc, journal))
        self.assertFalse(matches_branch(doc, task_scoped))
        self.assertFalse(matches_branch(doc, no_task))
        self.assertFalse(matches_branch(doc, queue_human))

    def test_valid_task_scoped_action_matches_only_that_branch(self):
        schema = op.load_schema("orchestrator-event-v1")
        journal, task_scoped, no_task, queue_human = event_branches(schema)
        doc = valid_action_event()
        self.assertFalse(matches_branch(doc, journal))
        self.assertTrue(matches_branch(doc, task_scoped))
        self.assertFalse(matches_branch(doc, no_task))
        self.assertFalse(matches_branch(doc, queue_human))

    def test_valid_no_task_action_matches_only_that_branch(self):
        schema = op.load_schema("orchestrator-event-v1")
        journal, task_scoped, no_task, queue_human = event_branches(schema)
        doc = valid_no_task_action_event()
        self.assertFalse(matches_branch(doc, journal))
        self.assertFalse(matches_branch(doc, task_scoped))
        self.assertTrue(matches_branch(doc, no_task))
        self.assertFalse(matches_branch(doc, queue_human))

    def test_valid_queue_human_run_level_matches_only_queue_human_branch(self):
        schema = op.load_schema("orchestrator-event-v1")
        journal, task_scoped, no_task, queue_human = event_branches(schema)
        doc = valid_queue_human_run_level_event()
        self.assertFalse(matches_branch(doc, journal))
        self.assertFalse(matches_branch(doc, task_scoped))
        self.assertFalse(matches_branch(doc, no_task))
        self.assertTrue(matches_branch(doc, queue_human))

    def test_valid_queue_human_task_scoped_matches_only_queue_human_branch(self):
        schema = op.load_schema("orchestrator-event-v1")
        journal, task_scoped, no_task, queue_human = event_branches(schema)
        doc = valid_queue_human_task_scoped_event()
        self.assertFalse(matches_branch(doc, journal))
        # Must not be mistaken for the always-task-scoped action branch,
        # even though it also names a task_id: the action name QUEUE-HUMAN
        # is outside that branch's action enum.
        self.assertFalse(matches_branch(doc, task_scoped))
        self.assertFalse(matches_branch(doc, no_task))
        self.assertTrue(matches_branch(doc, queue_human))

    def test_queue_human_run_level_and_task_scoped_sub_branches_are_exclusive(self):
        # Inside the QUEUE-HUMAN wrapper itself: a run-level document must
        # not also satisfy the task-scoped sub-branch, and the reverse.
        schema = op.load_schema("orchestrator-event-v1")
        _journal, _task_scoped, _no_task, queue_human = event_branches(schema)
        run_level_branch, task_scoped_branch = queue_human_branches(queue_human)
        run_level_doc = valid_queue_human_run_level_event()
        task_scoped_doc = valid_queue_human_task_scoped_event()
        self.assertTrue(matches_branch(run_level_doc, run_level_branch))
        self.assertFalse(matches_branch(run_level_doc, task_scoped_branch))
        self.assertFalse(matches_branch(task_scoped_doc, run_level_branch))
        self.assertTrue(matches_branch(task_scoped_doc, task_scoped_branch))

    def test_validate_agrees_exactly_one_branch_matches_each_fixture(self):
        # validate()'s own oneOf bookkeeping must reach the same verdict
        # as matches_branch() above, for every fixture, including both
        # QUEUE-HUMAN shapes.
        for doc in (valid_journal_event(), valid_action_event(),
                    valid_no_task_action_event(),
                    valid_queue_human_run_level_event(),
                    valid_queue_human_task_scoped_event()):
            self.assertEqual(op.validate(doc, "orchestrator-event-v1"), [])


class TestTaskIdConditionalRequirement(unittest.TestCase):
    # Finding 4b: task_id (and its two staleness companions) is required
    # exactly for actions that name a task, and forbidden otherwise.
    def test_task_scoped_action_missing_task_id_is_refused(self):
        doc = valid_action_event()
        del doc["task_id"]
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_no_task_action_carrying_task_id_is_refused(self):
        # A NOOP or CLOSEOUT must not be allowed to carry a task_id: it
        # is forbidden, not merely optional, because additionalProperties
        # is false and no branch that could accept "action": "CLOSEOUT"
        # declares task_id in its properties.
        doc = valid_no_task_action_event()
        doc["task_id"] = "ORCH-99"
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_noop_action_forced_to_invent_a_task_id_is_refused(self):
        doc = valid_action_event()
        doc["action"] = "NOOP"
        # doc still carries task_id, observed_task_state, observed_attempt
        # from valid_action_event(): a NOOP must not validate with them.
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_closeout_action_with_no_task_id_validates_clean(self):
        doc = valid_no_task_action_event()
        doc["action"] = "CLOSEOUT"
        self.assertEqual(op.validate(doc, "orchestrator-event-v1"), [])

    def test_dispatch_action_is_never_valid_in_the_no_task_branch(self):
        schema = op.load_schema("orchestrator-event-v1")
        _journal, _task_scoped, no_task, _queue_human = event_branches(schema)
        doc = valid_no_task_action_event()
        doc["action"] = "DISPATCH"
        self.assertFalse(matches_branch(doc, no_task))


class TestQueueHumanCanBeRunLevelOrTaskScoped(unittest.TestCase):
    # Coordinator correction: QUEUE-HUMAN must not be forced into the
    # task-scoped branch. The steering document's own acceptance scenario
    # ("A RED publication decision appears. Fable refuses and queues it.
    # All unrelated engineering work continues.") is run-level: it names
    # no task, and forcing a task_id onto it would train people to invent
    # one, the same defect already fixed for NOOP and CLOSEOUT.
    def test_run_level_queue_human_with_no_task_id_validates_clean(self):
        doc = valid_queue_human_run_level_event()
        self.assertNotIn("task_id", doc)
        self.assertEqual(op.validate(doc, "orchestrator-event-v1"), [])

    def test_task_scoped_queue_human_with_task_id_validates_clean(self):
        doc = valid_queue_human_task_scoped_event()
        self.assertIn("task_id", doc)
        self.assertEqual(op.validate(doc, "orchestrator-event-v1"), [])

    def test_task_scoped_queue_human_requires_observed_task_state(self):
        # Once task_id is present, the staleness fields the other
        # task-scoped actions require are required here too.
        doc = valid_queue_human_task_scoped_event()
        del doc["observed_task_state"]
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_task_scoped_queue_human_requires_observed_attempt(self):
        doc = valid_queue_human_task_scoped_event()
        del doc["observed_attempt"]
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_run_level_queue_human_forbids_task_id(self):
        doc = valid_queue_human_run_level_event()
        doc["task_id"] = "ORCH-99"
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_run_level_queue_human_forbids_observed_task_state(self):
        # Meaningless without a task_id: forbidden, not merely optional.
        doc = valid_queue_human_run_level_event()
        doc["observed_task_state"] = "DONE"
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_run_level_queue_human_missing_reason_is_refused(self):
        # reason is the load-bearing field for the run-level shape: a
        # queued human decision with no stated reason is useless to the
        # person who wakes up to it.
        doc = valid_queue_human_run_level_event()
        del doc["reason"]
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_run_level_queue_human_empty_reason_is_refused(self):
        doc = valid_queue_human_run_level_event()
        doc["reason"] = ""
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)


class TestTaskIdJoinKeyConsistency(unittest.TestCase):
    # Finding 5: orchestrator-task-v1.json's "id" and
    # orchestrator-event-v1.json's "task_id" are the same join key under
    # two names, one per document. A task's id must be usable, unchanged,
    # as an action's task_id.
    def test_a_tasks_id_validates_as_an_actions_task_id(self):
        task = valid_task()
        action = valid_action_event()
        action["task_id"] = task["id"]
        self.assertEqual(op.validate(task, "orchestrator-task-v1"), [])
        self.assertEqual(op.validate(action, "orchestrator-event-v1"), [])
        self.assertEqual(task["id"], action["task_id"])

    def test_id_and_task_id_are_documented_as_the_same_key(self):
        # A prose regression guard: each schema's description of its half
        # of the join key must name the other schema's field, so a future
        # edit that quietly drops the cross reference is caught.
        task_schema = op.load_schema("orchestrator-task-v1")
        event_schema = op.load_schema("orchestrator-event-v1")
        _journal, task_scoped, _no_task, _queue_human = event_branches(
            event_schema)
        id_description = task_schema["properties"]["id"]["description"]
        task_id_description = task_scoped["properties"]["task_id"]["description"]
        self.assertIn("task_id", id_description)
        self.assertIn("orchestrator-task-v1", task_id_description)


class TestMinLengthOnIdentifiers(unittest.TestCase):
    # Finding 6: id, title, objective and the entries of owns and
    # depends_on reject the empty string, the same way done_check
    # already did.
    def test_empty_id_is_refused(self):
        doc = valid_task()
        doc["id"] = ""
        errors = op.validate(doc, "orchestrator-task-v1")
        self.assertTrue(errors)

    def test_empty_title_is_refused(self):
        doc = valid_task()
        doc["title"] = ""
        errors = op.validate(doc, "orchestrator-task-v1")
        self.assertTrue(errors)

    def test_empty_objective_is_refused(self):
        doc = valid_task()
        doc["objective"] = ""
        errors = op.validate(doc, "orchestrator-task-v1")
        self.assertTrue(errors)

    def test_empty_owns_entry_is_refused(self):
        doc = valid_task()
        doc["owns"] = [""]
        errors = op.validate(doc, "orchestrator-task-v1")
        self.assertTrue(errors)

    def test_empty_depends_on_entry_is_refused(self):
        doc = valid_task()
        doc["depends_on"] = [""]
        errors = op.validate(doc, "orchestrator-task-v1")
        self.assertTrue(errors)


class TestObservedTaskStateAndAttempt(unittest.TestCase):
    # Design finding: observed_revision, observed_journal_seq and epoch
    # describe the canonical repository, the journal and the leadership
    # term, never the TASK itself. Two orchestrators can observe an
    # identical triple of those three and still race on the same task, so
    # a task-scoped action also carries observed_task_state and
    # observed_attempt, compared and swapped against the task's real
    # current state and attempt count at apply time (the same
    # compare-and-swap discipline scripts/claim_store.py already applies
    # to claims). This schema can only require the field and constrain
    # its shape to a real TASK_STATES member; judging whether a given
    # value is actually stale against the live store is an apply-time
    # concern outside a document schema's reach, so the closest
    # schema-expressible proxy for "stale" is tested here: a state that
    # was never a real one, which can never be the task's true current
    # state either.
    def test_missing_observed_task_state_is_refused(self):
        doc = valid_action_event()
        del doc["observed_task_state"]
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_missing_observed_attempt_is_refused(self):
        doc = valid_action_event()
        del doc["observed_attempt"]
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_unrecognised_observed_task_state_is_refused(self):
        # The value the store could never have actually held: not a
        # member of TASK_STATES at all. Closest schema-level stand in for
        # "stale" without a live store to compare against.
        doc = valid_action_event()
        doc["observed_task_state"] = "STATE_THE_STORE_NO_LONGER_HOLDS"
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_zero_observed_attempt_is_refused(self):
        doc = valid_action_event()
        doc["observed_attempt"] = 0
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_no_task_action_forbids_observed_task_state(self):
        doc = valid_no_task_action_event()
        doc["observed_task_state"] = "DONE"
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)

    def test_no_task_action_forbids_observed_attempt(self):
        doc = valid_no_task_action_event()
        doc["observed_attempt"] = 1
        errors = op.validate(doc, "orchestrator-event-v1")
        self.assertTrue(errors)


class TestDeepCopyIsolation(unittest.TestCase):
    # Guards against a fixture helper accidentally sharing mutable state
    # between tests (each valid_*() call must return an independent dict).
    def test_valid_manifest_calls_are_independent(self):
        first = valid_manifest()
        second = valid_manifest()
        first["run_id"] = "mutated"
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(copy.deepcopy(second), valid_manifest())


if __name__ == "__main__":
    unittest.main()
