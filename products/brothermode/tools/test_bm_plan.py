#!/usr/bin/env python3
"""tools/test_bm_plan.py: calibration for F2 (the plan as a versioned
artifact) and the read side of R7 (the four clause gate), tools/bm_plan.py.
Standard library only. Run: python3 tools/test_bm_plan.py

Every test here proves a claim can go RED as well as GREEN (a gate that
cannot fail verifies nothing): validate_plan refuses an incomplete plan
AND accepts one whose gaps are all named unknown; check_plan_gate refuses
a plan with no approval or a drifted approval AND passes an honestly
approved one; the CLI's exit codes, never its printed text, are what the
subprocess tests assert."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLAN_FILE = os.path.join(HERE, "bm_plan.py")

import importlib.util

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))
_spec = importlib.util.spec_from_file_location("bm_plan", PLAN_FILE)
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)


def _plan(**overrides):
    plan = {
        "options": ["do nothing", "ship the small fix", "ship the redesign"],
        "recommended": "ship the small fix",
        "will_run": ["apply the one line patch", "run the unit test"],
        "will_not_run": ["the redesign", "any schema migration"],
        "blast_radius": "one file, one function, no callers elsewhere",
        "reversibility": "git revert, no data written",
        "evidence_promised": "test output pasted after the edit",
    }
    plan.update(overrides)
    return plan


class TestValidatePlan(unittest.TestCase):
    def test_a_complete_plan_validates(self):
        self.assertTrue(bp.validate_plan(_plan()))

    def test_a_missing_field_is_refused_by_name(self):
        plan = _plan()
        del plan["blast_radius"]
        with self.assertRaises(bp.PlanError) as ctx:
            bp.validate_plan(plan)
        self.assertEqual(ctx.exception.reason, "incomplete-plan")
        self.assertIn("blast_radius", str(ctx.exception))

    def test_an_empty_field_counts_as_missing_not_satisfied(self):
        plan = _plan(will_not_run=[])
        with self.assertRaises(bp.PlanError) as ctx:
            bp.validate_plan(plan)
        self.assertEqual(ctx.exception.reason, "incomplete-plan")
        self.assertIn("will_not_run", str(ctx.exception))

    def test_a_field_explicitly_marked_unknown_validates(self):
        plan = _plan(reversibility=None,
                     unknown_fields={"reversibility": "not yet measured"})
        self.assertTrue(bp.validate_plan(plan))

    def test_unknown_fields_entry_with_empty_reason_is_refused(self):
        plan = _plan(reversibility=None,
                     unknown_fields={"reversibility": "   "})
        with self.assertRaises(bp.PlanError) as ctx:
            bp.validate_plan(plan)
        self.assertEqual(ctx.exception.reason, "bad-unknown-fields")

    def test_unknown_fields_naming_an_out_of_schema_key_is_refused(self):
        plan = _plan(unknown_fields={"not_a_real_field": "typo"})
        with self.assertRaises(bp.PlanError) as ctx:
            bp.validate_plan(plan)
        self.assertEqual(ctx.exception.reason, "bad-unknown-fields")
        self.assertIn("not_a_real_field", str(ctx.exception))

    def test_a_non_dict_plan_is_refused(self):
        with self.assertRaises(bp.PlanError) as ctx:
            bp.validate_plan(["not", "a", "dict"])
        self.assertEqual(ctx.exception.reason, "bad-plan")


class TestPlanVersion(unittest.TestCase):
    def test_identical_content_fingerprints_identically(self):
        self.assertEqual(bp.plan_version(_plan()), bp.plan_version(_plan()))

    def test_a_content_change_changes_the_version(self):
        self.assertNotEqual(bp.plan_version(_plan()),
                            bp.plan_version(_plan(blast_radius="wider now")))

    def test_approval_bookkeeping_does_not_change_the_version(self):
        base = bp.plan_version(_plan())
        approved = dict(_plan())
        approved.update(approved=True, approved_version=base,
                        approved_by="khalil", approved_at="2026-08-29T00:00:00Z")
        self.assertEqual(base, bp.plan_version(approved))

    def test_an_unknown_fields_change_changes_the_version(self):
        plan = _plan(reversibility=None,
                     unknown_fields={"reversibility": "not yet measured"})
        other = _plan(reversibility=None,
                      unknown_fields={"reversibility": "a different reason"})
        self.assertNotEqual(bp.plan_version(plan), bp.plan_version(other))

    def test_version_is_twelve_lowercase_hex_characters(self):
        v = bp.plan_version(_plan())
        self.assertEqual(len(v), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in v))


class TestCheckPlanGate(unittest.TestCase):
    """R7's four clauses, calibrated to go red on each one independently
    and green only when all four hold at once."""

    def test_no_plan_at_all_refuses_no_plan(self):
        with self.assertRaises(bp.PlanError) as ctx:
            bp.check_plan_gate(None)
        self.assertEqual(ctx.exception.reason, "no-plan")

    def test_an_incomplete_plan_refuses_before_approval_is_even_read(self):
        plan = _plan()
        del plan["recommended"]
        plan["approved"] = True
        plan["approved_version"] = bp.plan_version(plan)
        with self.assertRaises(bp.PlanError) as ctx:
            bp.check_plan_gate(plan)
        self.assertEqual(ctx.exception.reason, "incomplete-plan")

    def test_a_complete_but_unapproved_plan_refuses_plan_not_approved(self):
        with self.assertRaises(bp.PlanError) as ctx:
            bp.check_plan_gate(_plan())
        self.assertEqual(ctx.exception.reason, "plan-not-approved")

    def test_a_drifted_approval_refuses_plan_drifted(self):
        plan = _plan()
        plan["approved"] = True
        plan["approved_version"] = bp.plan_version(plan)
        # mutate AFTER the version was captured, exactly the scenario R7
        # clause (d) exists to catch: approving a plausible v1 is not
        # approving whatever v2 eventually runs.
        plan["blast_radius"] = "quietly widened after approval"
        with self.assertRaises(bp.PlanError) as ctx:
            bp.check_plan_gate(plan)
        self.assertEqual(ctx.exception.reason, "plan-drifted")

    def test_a_complete_approved_undrifted_plan_passes(self):
        plan = _plan()
        plan["approved"] = True
        plan["approved_version"] = bp.plan_version(plan)
        self.assertTrue(bp.check_plan_gate(plan))


class TestPlanStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-plan-store-")
        self.addCleanup(self._cleanup)
        self.store = bp.PlanStore(self.tmp)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_before_any_record_is_none(self):
        self.assertIsNone(self.store.read("proj1"))

    def test_record_then_read_round_trips_the_content(self):
        record = self.store.record("proj1", _plan(), actor="khalil")
        self.assertEqual(record["blast_radius"], _plan()["blast_radius"])
        self.assertEqual(self.store.read("proj1")["version"],
                         record["version"])

    def test_record_refuses_an_unrecognised_field(self):
        content = _plan()
        content["not_a_field"] = "oops"
        with self.assertRaises(bp.PlanError) as ctx:
            self.store.record("proj1", content)
        self.assertEqual(ctx.exception.reason, "unknown-field")
        self.assertIsNone(self.store.read("proj1"))

    def test_record_refuses_an_incomplete_plan_and_writes_nothing(self):
        content = _plan()
        del content["will_run"]
        with self.assertRaises(bp.PlanError):
            self.store.record("proj1", content)
        self.assertIsNone(self.store.read("proj1"))

    def test_approve_with_no_prior_record_refuses_no_plan(self):
        with self.assertRaises(bp.PlanError) as ctx:
            self.store.approve("proj1", "khalil")
        self.assertEqual(ctx.exception.reason, "no-plan")

    def test_the_full_lifecycle_matches_the_r7_walkthrough(self):
        """Record v1, approve it (gate passes), mutate to v2 (gate
        refuses: drifted), approve v2 (gate passes again)."""
        self.store.record("proj1", _plan())
        with self.assertRaises(bp.PlanError):
            self.store.check("proj1")  # not yet approved

        v1 = self.store.approve("proj1", "khalil")["approved_version"]
        self.assertTrue(self.store.check("proj1"))

        self.store.record("proj1", _plan(blast_radius="wider now, v2"))
        with self.assertRaises(bp.PlanError) as ctx:
            self.store.check("proj1")
        self.assertEqual(ctx.exception.reason, "plan-drifted")

        v2 = self.store.approve("proj1", "khalil")["approved_version"]
        self.assertNotEqual(v1, v2)
        self.assertTrue(self.store.check("proj1"))

    def test_a_path_escaping_project_id_is_refused(self):
        with self.assertRaises(bp.PlanError) as ctx:
            self.store.read("../elsewhere")
        self.assertEqual(ctx.exception.reason, "bad-project-id")

    def test_a_crash_mid_write_never_corrupts_the_prior_plan(self):
        """The atomic write contract: record, then simulate a crash by
        making the write itself fail, and confirm the PRIOR file (or
        absence of one) is exactly what a fresh read still sees."""
        self.store.record("proj1", _plan())
        before = self.store.read("proj1")
        real_replace = os.replace

        def _boom(*a, **kw):
            raise OSError("simulated crash mid write")

        os.replace = _boom
        try:
            with self.assertRaises(OSError):
                self.store.record("proj1", _plan(blast_radius="v2"))
        finally:
            os.replace = real_replace
        self.assertEqual(self.store.read("proj1"), before)
        leftover = [n for n in os.listdir(self.store.dir)
                   if n.endswith(".tmp")]
        self.assertEqual(leftover, [],
                         "a failed write left a temp file behind")


class TestRecordRedirect(unittest.TestCase):
    """F1 (mid-stream steering): a redirect is a plan VERSION BUMP, never
    a silent edit and never a second, separate channel a controller run
    could miss. Calibrated to go red on the two shapes that would defeat
    the feature: no version change (a redirect nobody could later prove
    happened), and a redirect that reaches back and touches approval
    bookkeeping (a "correction" that also silently re-approves itself)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-plan-redirect-")
        self.addCleanup(self._cleanup)
        self.store = bp.PlanStore(self.tmp)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_redirect_on_a_project_with_no_plan_raises_plan_error(self):
        with self.assertRaises(bp.PlanError) as ctx:
            self.store.record_redirect("proj1", "course correction")
        self.assertEqual(ctx.exception.reason, "no-plan")

    def test_redirect_with_an_empty_note_is_refused(self):
        self.store.record("proj1", _plan())
        with self.assertRaises(bp.PlanError) as ctx:
            self.store.record_redirect("proj1", "   ")
        self.assertEqual(ctx.exception.reason, "bad-redirect")

    def test_redirect_returns_a_version_different_from_before(self):
        before = self.store.record("proj1", _plan())
        old_version = bp.plan_version(before)
        after = self.store.record_redirect(
            "proj1", "wrong abstraction, steer to the small fix",
            actor="khalil")
        self.assertNotEqual(after["version"], old_version)
        # and the store's own idea of the current version agrees.
        self.assertEqual(self.store.read("proj1")["version"],
                         after["version"])

    def test_redirect_leaves_approved_version_untouched(self):
        self.store.record("proj1", _plan())
        approved = self.store.approve("proj1", "khalil")
        approved_version = approved["approved_version"]
        after = self.store.record_redirect("proj1", "steer away from it")
        self.assertEqual(after["approved_version"], approved_version)
        self.assertTrue(after["approved"])
        # and the gate now correctly refuses: the approval is stale.
        with self.assertRaises(bp.PlanError) as ctx:
            self.store.check("proj1")
        self.assertEqual(ctx.exception.reason, "plan-drifted")

    def test_two_redirects_both_survive_in_order(self):
        self.store.record("proj1", _plan())
        self.store.record_redirect("proj1", "first correction")
        after = self.store.record_redirect("proj1", "second correction")
        notes = [r["note"] for r in after["redirects"]]
        self.assertEqual(notes, ["first correction", "second correction"])

    def test_re_recording_content_carries_redirects_forward(self):
        """A redirect is provenance, not content the next `record` call is
        asked to restate; losing it on an ordinary edit would be exactly
        the silent-discard failure F1 exists to prevent."""
        self.store.record("proj1", _plan())
        self.store.record_redirect("proj1", "course correction")
        after = self.store.record("proj1", _plan(blast_radius="v2, edited"))
        self.assertEqual(len(after["redirects"]), 1)


class TestCli(unittest.TestCase):
    """Exit codes only, per house rule: a gate that prints FAIL and exits
    0 verifies nothing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm-plan-cli-")
        self.addCleanup(self._cleanup)
        self.env = dict(os.environ)
        self.env["BROTHERMODE_ROOT"] = self.tmp
        home = os.path.join(self.tmp, "home")
        os.makedirs(home, exist_ok=True)
        self.env["HOME"] = home
        self.env.pop("USERPROFILE", None)
        self.plan_path = os.path.join(self.tmp, "plan.json")
        with io.open(self.plan_path, "w", encoding="utf-8") as fh:
            json.dump(_plan(), fh)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, PLAN_FILE] + list(args), cwd=self.tmp,
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)

    def test_show_before_any_record_exits_refused(self):
        r = self._run("show", "--project", "proj1")
        self.assertEqual(r.returncode, bp.EXIT_REFUSED, r.stderr)

    def test_check_before_any_record_exits_refused(self):
        r = self._run("check", "--project", "proj1")
        self.assertEqual(r.returncode, bp.EXIT_REFUSED, r.stderr)

    def test_record_then_check_unapproved_exits_refused(self):
        r = self._run("record", "--project", "proj1", "--file",
                      self.plan_path)
        self.assertEqual(r.returncode, bp.EXIT_OK, r.stderr)
        r = self._run("check", "--project", "proj1")
        self.assertEqual(r.returncode, bp.EXIT_REFUSED, r.stderr)

    def test_record_approve_check_exits_ok(self):
        self._run("record", "--project", "proj1", "--file", self.plan_path)
        r = self._run("approve", "--project", "proj1", "--actor-name",
                      "khalil")
        self.assertEqual(r.returncode, bp.EXIT_OK, r.stderr)
        r = self._run("check", "--project", "proj1")
        self.assertEqual(r.returncode, bp.EXIT_OK, r.stderr)

    def test_a_mutation_after_approval_flips_check_back_to_refused(self):
        self._run("record", "--project", "proj1", "--file", self.plan_path)
        self._run("approve", "--project", "proj1", "--actor-name", "khalil")
        r = self._run("check", "--project", "proj1")
        self.assertEqual(r.returncode, bp.EXIT_OK, r.stderr)

        mutated = _plan(blast_radius="widened after approval, v2")
        with io.open(self.plan_path, "w", encoding="utf-8") as fh:
            json.dump(mutated, fh)
        self._run("record", "--project", "proj1", "--file", self.plan_path)
        r = self._run("check", "--project", "proj1")
        self.assertEqual(r.returncode, bp.EXIT_REFUSED, r.stderr)

    def test_missing_project_flag_exits_usage(self):
        r = self._run("check")
        self.assertEqual(r.returncode, bp.EXIT_USAGE)

    def test_unknown_command_exits_usage(self):
        r = self._run("not-a-command")
        self.assertEqual(r.returncode, bp.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
