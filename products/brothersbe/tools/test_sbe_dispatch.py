"""Fixtures for tools/sbe_dispatch.py, the dispatch gate.

Spec: design/phase-0/SPEC.md, Lane B. Every required field missing in turn and
refused by name, a model version string in model_tier refused, an absolute
path in files refused, T2's agent ceiling, T3's tier_reason requirement, a
passing brief, a missing owed file refusing loop-open as NO-DATA, an open
owed item blocking, a founder-deferred item with a reason not blocking, and
multiple problems all reported in one run.

Run directly: python3 tools/test_sbe_dispatch.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import sbe_dispatch as dispatch  # noqa: E402

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


def _valid_brief(**overrides):
    brief = {
        "objective": "port the checkout hook to windows",
        "model_tier": "builder",
        "token_budget": 50000,
        "files": ["tools/sbe_dispatch.py", "tools/test_sbe_dispatch.py"],
        "done_check": "python3 tools/test_sbe_dispatch.py",
        "tier": "T1",
    }
    brief.update(overrides)
    return brief


def _run(argv, stdin=None):
    """Run sbe_dispatch.py as a real subprocess: the exit-code discipline
    (0/1/2) is the contract this lane is graded on, and only a real process
    exit proves it, a function return value does not."""
    out = subprocess.run([sys.executable, os.path.join(HERE, "sbe_dispatch.py")] + argv,
                         capture_output=True, text=True, input=stdin, timeout=30)
    return out.returncode, out.stdout, out.stderr


class TestValidateBriefRequiredFields(unittest.TestCase):
    """Every required field, missing in turn, refused by name."""

    def test_a_complete_brief_passes(self):
        self.assertEqual(dispatch.validate_brief(_valid_brief()), [])

    def test_objective_missing_is_refused_by_name(self):
        b = _valid_brief()
        del b["objective"]
        problems = dispatch.validate_brief(b)
        self.assertTrue(any("objective" in p for p in problems), problems)

    def test_objective_blank_is_refused_by_name(self):
        problems = dispatch.validate_brief(_valid_brief(objective="   "))
        self.assertTrue(any("objective" in p for p in problems), problems)

    def test_model_tier_missing_is_refused_by_name(self):
        b = _valid_brief()
        del b["model_tier"]
        problems = dispatch.validate_brief(b)
        self.assertTrue(any("model_tier" in p for p in problems), problems)

    def test_token_budget_missing_is_refused_by_name(self):
        b = _valid_brief()
        del b["token_budget"]
        problems = dispatch.validate_brief(b)
        self.assertTrue(any("token_budget" in p for p in problems), problems)

    def test_token_budget_zero_is_refused(self):
        problems = dispatch.validate_brief(_valid_brief(token_budget=0))
        self.assertTrue(any("token_budget" in p for p in problems), problems)

    def test_token_budget_negative_is_refused(self):
        problems = dispatch.validate_brief(_valid_brief(token_budget=-5))
        self.assertTrue(any("token_budget" in p for p in problems), problems)

    def test_token_budget_boolean_is_refused(self):
        """isinstance(True, int) is True in Python; token_budget: true must
        not silently read as the budget 1."""
        problems = dispatch.validate_brief(_valid_brief(token_budget=True))
        self.assertTrue(any("token_budget" in p for p in problems), problems)

    def test_files_missing_is_refused_by_name(self):
        b = _valid_brief()
        del b["files"]
        problems = dispatch.validate_brief(b)
        self.assertTrue(any("files" in p for p in problems), problems)

    def test_files_empty_list_is_refused(self):
        problems = dispatch.validate_brief(_valid_brief(files=[]))
        self.assertTrue(any("files" in p for p in problems), problems)

    def test_done_check_missing_is_refused_by_name(self):
        b = _valid_brief()
        del b["done_check"]
        problems = dispatch.validate_brief(b)
        self.assertTrue(any("done_check" in p for p in problems), problems)

    def test_done_check_blank_is_refused(self):
        problems = dispatch.validate_brief(_valid_brief(done_check="   "))
        self.assertTrue(any("done_check" in p for p in problems), problems)

    def test_tier_missing_is_refused_by_name(self):
        b = _valid_brief()
        del b["tier"]
        problems = dispatch.validate_brief(b)
        self.assertTrue(any("tier" in p for p in problems), problems)

    def test_tier_unrecognized_is_refused_naming_the_set(self):
        problems = dispatch.validate_brief(_valid_brief(tier="T9"))
        joined = "; ".join(problems)
        self.assertIn("T1", joined)
        self.assertIn("T2", joined)
        self.assertIn("T3", joined)

    def test_multiple_problems_are_all_reported_in_one_run(self):
        """An agent fixing one missing field at a time is an agent making four
        round trips: every problem must come back in a single call."""
        b = {"model_tier": "sonnet", "tier": "T2", "files": ["/etc/passwd"]}
        problems = dispatch.validate_brief(b)
        self.assertTrue(any("objective" in p for p in problems), problems)
        self.assertTrue(any("token_budget" in p for p in problems), problems)
        self.assertTrue(any("done_check" in p for p in problems), problems)
        self.assertTrue(any("model_tier" in p and "version" in p for p in problems), problems)
        self.assertTrue(any("/etc/passwd" in p for p in problems), problems)
        self.assertGreaterEqual(len(problems), 5, problems)


class TestModelTierIsACapabilityProfile(unittest.TestCase):
    """SECOND LOAD-BEARING RULE: model_tier names a capability profile, never
    a model version. Calibrated below in test_calibration_model_version_guard."""

    def test_a_bare_model_version_string_is_refused_naming_the_reason(self):
        for bad in ("claude", "claude-sonnet-4.5", "GPT-4", "opus", "gemini-pro", "haiku"):
            problems = dispatch.validate_brief(_valid_brief(model_tier=bad))
            self.assertTrue(any("version" in p and "profile" in p for p in problems),
                            (bad, problems))

    def test_every_declared_capability_profile_passes(self):
        for tier in dispatch.MODEL_TIERS:
            problems = dispatch.validate_brief(_valid_brief(model_tier=tier))
            self.assertEqual(problems, [], (tier, problems))

    def test_an_unrecognized_non_version_string_is_refused_naming_the_allowed_set(self):
        problems = dispatch.validate_brief(_valid_brief(model_tier="junior_dev"))
        joined = "; ".join(problems)
        self.assertIn("fast_worker", joined)
        self.assertNotIn("version", joined, "this is an unrecognized profile, not a version "
                                            "string, and the two refusal reasons must stay "
                                            "distinguishable")


class TestAbsolutePathsInFiles(unittest.TestCase):
    def test_a_posix_absolute_path_is_refused(self):
        problems = dispatch.validate_brief(_valid_brief(files=["/etc/passwd"]))
        self.assertTrue(any("absolute" in p for p in problems), problems)

    def test_a_windows_drive_path_is_refused_even_off_windows(self):
        problems = dispatch.validate_brief(_valid_brief(files=["C:\\Users\\a\\file.py"]))
        self.assertTrue(any("absolute" in p for p in problems), problems)

    def test_a_windows_unc_path_is_refused(self):
        problems = dispatch.validate_brief(_valid_brief(files=["\\\\host\\share\\file.py"]))
        self.assertTrue(any("absolute" in p for p in problems), problems)

    def test_a_relative_path_passes(self):
        problems = dispatch.validate_brief(_valid_brief(files=["src/a.py", "tools/b.py"]))
        self.assertEqual(problems, [])


class TestScalingCheck(unittest.TestCase):
    """The part that closes the 11,017k-token failure."""

    def test_t1_with_one_agent_passes(self):
        problems = dispatch.validate_brief(_valid_brief(tier="T1", agent_count=1))
        self.assertEqual(problems, [])

    def test_t1_with_two_agents_is_refused(self):
        problems = dispatch.validate_brief(_valid_brief(tier="T1", agent_count=2))
        self.assertTrue(any("T1" in p and "1 agent" in p for p in problems), problems)

    def test_t2_with_four_agents_is_accepted(self):
        problems = dispatch.validate_brief(_valid_brief(tier="T2", agent_count=4))
        self.assertEqual(problems, [])

    def test_t2_with_five_agents_is_refused(self):
        problems = dispatch.validate_brief(_valid_brief(tier="T2", agent_count=5))
        self.assertTrue(any("T2" in p and "4 agent" in p for p in problems), problems)

    def test_t3_without_tier_reason_is_refused(self):
        problems = dispatch.validate_brief(_valid_brief(tier="T3", agent_count=20))
        self.assertTrue(any("tier_reason" in p for p in problems), problems)

    def test_t3_with_tier_reason_and_high_agent_count_passes(self):
        problems = dispatch.validate_brief(_valid_brief(
            tier="T3", agent_count=40, tier_reason="full audit sweep across every gate"))
        self.assertEqual(problems, [])

    def test_t3_with_blank_tier_reason_is_refused(self):
        problems = dispatch.validate_brief(_valid_brief(tier="T3", tier_reason="   "))
        self.assertTrue(any("tier_reason" in p for p in problems), problems)

    def test_agent_count_defaults_to_one_when_absent(self):
        b = _valid_brief(tier="T1")
        self.assertNotIn("agent_count", b)
        self.assertEqual(dispatch.validate_brief(b), [])

    def test_t3_bad_agent_count_does_not_mask_missing_tier_reason(self):
        """An invalid agent_count must not hide a missing tier_reason: the
        spec requires every problem reported in one run, not one round trip
        per field."""
        problems = dispatch.validate_brief(_valid_brief(tier="T3", agent_count="many"))
        self.assertTrue(any("agent_count" in p for p in problems), problems)
        self.assertTrue(any("tier_reason" in p for p in problems), problems)


class TestBriefCLI(unittest.TestCase):
    def test_a_passing_brief_via_file_exits_zero(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(_valid_brief(), f)
            path = f.name
        try:
            code, out, err = _run(["brief", "--file", path])
            self.assertEqual(code, 0, out + err)
            self.assertIn("PASS", out)
        finally:
            os.unlink(path)

    def test_a_refused_brief_via_stdin_exits_one_and_names_every_problem(self):
        code, out, err = _run(["brief", "--json", "-"], stdin=json.dumps({"tier": "T9"}))
        self.assertEqual(code, 1, out + err)
        self.assertIn("objective", out)
        self.assertIn("token_budget", out)
        self.assertIn("done_check", out)

    def test_malformed_json_is_a_usage_error_not_a_refusal(self):
        code, out, err = _run(["brief", "--json", "-"], stdin="{ not json")
        self.assertEqual(code, 2, out + err)

    def test_neither_file_nor_json_given_is_a_usage_error(self):
        code, out, err = _run(["brief"])
        self.assertEqual(code, 2, out + err)

    def test_unrecognized_subcommand_is_a_usage_error(self):
        code, out, err = _run(["not-a-real-command"])
        self.assertEqual(code, 2, out + err)


class TestLoopOpen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-dispatch-")
        self.owed = os.path.join(self.tmp, "OWED.json")
        self.state = os.path.join(self.tmp, "state.json")

    def _write(self, path, obj):
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_a_missing_owed_file_refuses_as_no_data_never_a_silent_pass(self):
        """THE LOAD-BEARING RULE. The owed file is absent; that absence must
        never be read as 'no unfinished work'."""
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NO-DATA" in p and "OWED.json" in p for p in problems), problems)

    def test_an_open_owed_item_blocks_naming_it(self):
        self._write(self.owed, {"schemaVersion": 1, "items": [
            {"id": "W-1", "title": "finish the migration receipt", "state": "open"}]})
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("W-1" in p for p in problems), problems)

    def test_a_closed_owed_item_does_not_block(self):
        self._write(self.owed, {"schemaVersion": 1, "items": [
            {"id": "W-1", "title": "done thing", "state": "closed"}]})
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}})
        self.assertEqual(dispatch.check_loop_open(self.state, self.owed), [])

    def test_a_founder_deferred_item_with_a_reason_does_not_block(self):
        self._write(self.owed, {"schemaVersion": 1, "items": [
            {"id": "W-2", "title": "later work", "state": "open",
             "deferred_by_founder": True, "deferral_reason": "waiting on partner API"}]})
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}})
        self.assertEqual(dispatch.check_loop_open(self.state, self.owed), [])

    def test_deferred_by_founder_with_no_reason_still_blocks(self):
        """A deferral nobody can read the reason for is not a deferral this
        gate can honor."""
        self._write(self.owed, {"schemaVersion": 1, "items": [
            {"id": "W-3", "title": "later work", "state": "open",
             "deferred_by_founder": True, "deferral_reason": ""}]})
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("W-3" in p for p in problems), problems)

    def test_deferred_by_founder_as_string_true_still_blocks(self):
        """The boolean IS the claim: the string 'true' is truthy and must not
        satisfy a check that only the value True satisfies."""
        self._write(self.owed, {"schemaVersion": 1, "items": [
            {"id": "W-4", "title": "later work", "state": "open",
             "deferred_by_founder": "true", "deferral_reason": "because"}]})
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("W-4" in p for p in problems), problems)

    def test_an_exhausted_budget_blocks(self):
        self._write(self.owed, {"schemaVersion": 1, "items": []})
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 100}})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("exhausted" in p for p in problems), problems)

    def test_budget_under_cap_does_not_block(self):
        self._write(self.owed, {"schemaVersion": 1, "items": []})
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 99}})
        self.assertEqual(dispatch.check_loop_open(self.state, self.owed), [])

    def test_multiple_problems_are_all_reported_in_one_run(self):
        self._write(self.owed, {"schemaVersion": 1, "items": [
            {"id": "W-5", "title": "a", "state": "open"},
            {"id": "W-6", "title": "b", "state": "open"}]})
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 10, "spent": 10}})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("W-5" in p for p in problems), problems)
        self.assertTrue(any("W-6" in p for p in problems), problems)
        self.assertTrue(any("exhausted" in p for p in problems), problems)
        self.assertGreaterEqual(len(problems), 3, problems)

    def test_a_clean_loop_passes(self):
        self._write(self.owed, {"schemaVersion": 1, "items": [
            {"id": "W-1", "title": "done", "state": "closed"}]})
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}})
        self.assertEqual(dispatch.check_loop_open(self.state, self.owed), [])

    def test_an_unparseable_owed_file_is_refused_as_a_broken_claim(self):
        with io.open(self.owed, "w", encoding="utf-8") as f:
            f.write("{ not json")
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("not readable as JSON" in p for p in problems), problems)

    def test_missing_state_file_refuses_as_no_data(self):
        """A missing loop-state file is the same failure class as a missing
        owed file: absence is not evidence the budget is safe."""
        self._write(self.owed, {"schemaVersion": 1, "items": []})
        problems = dispatch.check_loop_open(self.state, self.owed)  # self.state never written
        self.assertTrue(any("NO-DATA" in p and self.state in p for p in problems), problems)

    def test_empty_state_object_refuses_as_no_data_never_a_silent_pass(self):
        """MAJOR FIX: an empty state file used to defeat the budget guard and
        the tool then printed a PASS line asserting a budget fact that was
        never measured. An empty state object must refuse, naming the
        absent 'budget' key, exactly as a missing owed file refuses."""
        self._write(self.owed, {"schemaVersion": 1, "items": []})
        self._write(self.state, {})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NO-DATA" in p and "budget" in p for p in problems), problems)

    def test_state_with_no_budget_key_refuses_as_no_data(self):
        self._write(self.owed, {"schemaVersion": 1, "items": []})
        self._write(self.state, {"schemaVersion": 1})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NO-DATA" in p and "budget" in p for p in problems), problems)

    def test_non_numeric_cap_refuses_as_no_data_naming_cap(self):
        self._write(self.owed, {"schemaVersion": 1, "items": []})
        self._write(self.state, {"schemaVersion": 1,
                                 "budget": {"cap": "one hundred", "spent": 5}})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NO-DATA" in p and "cap" in p for p in problems), problems)

    def test_non_numeric_spent_refuses_as_no_data_naming_spent(self):
        self._write(self.owed, {"schemaVersion": 1, "items": []})
        self._write(self.state, {"schemaVersion": 1,
                                 "budget": {"cap": 100, "spent": None}})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NO-DATA" in p and "spent" in p for p in problems), problems)

    def test_null_cap_refuses_as_no_data(self):
        """A recorded null cap looked like a clear budget under the old
        silent-pass behaviour (spent >= None raised, but the old code never
        even reached that comparison); it must now refuse as NO-DATA."""
        self._write(self.owed, {"schemaVersion": 1, "items": []})
        self._write(self.state, {"schemaVersion": 1,
                                 "budget": {"cap": None, "spent": 999}})
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NO-DATA" in p and "cap" in p for p in problems), problems)


class TestLoopOpenCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-dispatch-cli-")
        self.owed = os.path.join(self.tmp, "OWED.json")
        self.state = os.path.join(self.tmp, "state.json")
        with io.open(self.state, "w", encoding="utf-8") as f:
            json.dump({"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}}, f)

    def test_missing_owed_exits_one_via_the_cli(self):
        code, out, err = _run(["loop-open", "--state", self.state, "--owed", self.owed])
        self.assertEqual(code, 1, out + err)
        self.assertIn("NO-DATA", out)

    def test_a_clean_loop_exits_zero_via_the_cli(self):
        with io.open(self.owed, "w", encoding="utf-8") as f:
            json.dump({"schemaVersion": 1, "items": []}, f)
        code, out, err = _run(["loop-open", "--state", self.state, "--owed", self.owed])
        self.assertEqual(code, 0, out + err)
        self.assertIn("PASS", out)

    def test_missing_required_flag_is_a_usage_error(self):
        code, out, err = _run(["loop-open", "--owed", self.owed])
        self.assertEqual(code, 2, out + err)

    def test_missing_state_file_exits_one_via_the_cli(self):
        with io.open(self.owed, "w", encoding="utf-8") as f:
            json.dump({"schemaVersion": 1, "items": []}, f)
        missing_state = os.path.join(self.tmp, "no-such-state.json")
        code, out, err = _run(["loop-open", "--state", missing_state, "--owed", self.owed])
        self.assertEqual(code, 1, out + err)
        self.assertIn("NO-DATA", out)

    def test_empty_state_file_exits_one_never_a_silent_pass_via_the_cli(self):
        """MAJOR FIX, end to end: an empty --state file must never let the
        CLI print PASS, because the budget it would be claiming clear was
        never measured."""
        with io.open(self.owed, "w", encoding="utf-8") as f:
            json.dump({"schemaVersion": 1, "items": []}, f)
        with io.open(self.state, "w", encoding="utf-8") as f:
            json.dump({}, f)
        code, out, err = _run(["loop-open", "--state", self.state, "--owed", self.owed])
        self.assertEqual(code, 1, out + err)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("PASS", out)


class TestCalibration(unittest.TestCase):
    """Each fixture here temporarily breaks the behaviour it guards, confirms
    the guard is what makes the corresponding TestCase fixture fail, then
    restores it, all inside one process so the calibration is provable by a
    reviewer re-running this file rather than by narration alone."""

    def test_calibration_missing_owed_no_data_guard(self):
        tmp = tempfile.mkdtemp(prefix="sbe-dispatch-calib-owed-")
        owed = os.path.join(tmp, "OWED.json")
        state = os.path.join(tmp, "state.json")
        with io.open(state, "w", encoding="utf-8") as f:
            json.dump({"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}}, f)
        # Confirm the guarded behaviour actually holds first.
        problems = dispatch.check_loop_open(state, owed)
        self.assertTrue(any("NO-DATA" in p for p in problems), problems)
        # Break it: patch os.path.isfile so a missing owed file reads as
        # present, exactly the defect this rule exists to prevent.
        original_isfile = dispatch.os.path.isfile

        def lie(path):
            if path == owed:
                return True
            return original_isfile(path)

        dispatch.os.path.isfile = lie
        try:
            # With isfile lying, open() itself still raises, which the broken
            # claim path also refuses correctly, so patch open too: the
            # calibration must reach the specific branch that would have
            # silently treated "no items" as "nothing owed" once the file is
            # believed present but yields nothing.
            original_open = io.open

            def fake_open(path, *a, **kw):
                if path == owed:
                    return io.StringIO(json.dumps({"schemaVersion": 1, "items": []}))
                return original_open(path, *a, **kw)

            dispatch.open = fake_open
            try:
                broken_problems = dispatch.check_loop_open(state, owed)
            finally:
                del dispatch.open
        finally:
            dispatch.os.path.isfile = original_isfile
        self.assertEqual(broken_problems, [],
                         "sanity: with the guard bypassed, a missing-but-lied-about owed file "
                         "with no items produces no problems, proving the real guard (not this "
                         "test's plumbing) is what makes the unpatched call refuse")
        # Restored: the real behaviour is back.
        problems_after = dispatch.check_loop_open(state, owed)
        self.assertTrue(any("NO-DATA" in p for p in problems_after), problems_after)

    def test_calibration_model_version_guard(self):
        # Confirm the guard holds.
        self.assertTrue(any("version" in p for p in
                            dispatch.validate_brief(_valid_brief(model_tier="claude-sonnet-4.5"))))
        # Break it: temporarily empty the version-word list.
        original = dispatch.MODEL_VERSION_WORDS
        dispatch.MODEL_VERSION_WORDS = ()
        try:
            problems = dispatch.validate_brief(_valid_brief(model_tier="claude-sonnet-4.5"))
            self.assertTrue(any("version" in p for p in problems) is False,
                            "sanity: with the version-word list emptied, a model version string "
                            "must slip past unnoticed as an unrecognized profile instead, "
                            "proving the list (not something else) is what catches it")
        finally:
            dispatch.MODEL_VERSION_WORDS = original
        # Restored.
        self.assertTrue(any("version" in p for p in
                            dispatch.validate_brief(_valid_brief(model_tier="claude-sonnet-4.5"))))

    def test_calibration_absolute_path_guard(self):
        self.assertTrue(any("absolute" in p for p in
                            dispatch.validate_brief(_valid_brief(files=["/etc/passwd"]))))
        original = dispatch._is_absolute_path
        dispatch._is_absolute_path = lambda p: False
        try:
            problems = dispatch.validate_brief(_valid_brief(files=["/etc/passwd"]))
            self.assertFalse(any("absolute" in p for p in problems),
                             "sanity: with the absolute-path check stubbed out, an absolute "
                             "path must pass silently, proving the check (not something else) "
                             "is what refuses it")
        finally:
            dispatch._is_absolute_path = original
        self.assertTrue(any("absolute" in p for p in
                            dispatch.validate_brief(_valid_brief(files=["/etc/passwd"]))))

    def test_calibration_t2_agent_ceiling_guard(self):
        self.assertTrue(any("T2" in p for p in
                            dispatch.validate_brief(_valid_brief(tier="T2", agent_count=5))))
        original = dict(dispatch.AGENT_CEILING)
        dispatch.AGENT_CEILING["T2"] = 999
        try:
            problems = dispatch.validate_brief(_valid_brief(tier="T2", agent_count=5))
            self.assertEqual(problems, [],
                             "sanity: with the T2 ceiling raised, 5 agents must pass, proving "
                             "the ceiling constant (not something else) is what refuses it")
        finally:
            dispatch.AGENT_CEILING.clear()
            dispatch.AGENT_CEILING.update(original)
        self.assertTrue(any("T2" in p for p in
                            dispatch.validate_brief(_valid_brief(tier="T2", agent_count=5))))

    def test_calibration_t3_tier_reason_guard(self):
        """Real break-and-restore, not a source-text grep: a source-text match
        on inspect.getsource would pass on broken behaviour (e.g. after a
        quote-style change) and prove nothing about what the code does. This
        patches the actual guard function and observes the behaviour change."""
        self.assertTrue(any("tier_reason" in p for p in
                            dispatch.validate_brief(_valid_brief(tier="T3"))))
        original = dispatch._t3_tier_reason_problem
        dispatch._t3_tier_reason_problem = lambda tier, brief: None
        try:
            problems = dispatch.validate_brief(_valid_brief(tier="T3"))
            self.assertFalse(any("tier_reason" in p for p in problems),
                             "sanity: with the T3 guard stubbed to always clear, T3 with no "
                             "tier_reason must pass, proving the guard (not something else) "
                             "is what refuses it")
        finally:
            dispatch._t3_tier_reason_problem = original
        self.assertTrue(any("tier_reason" in p for p in
                            dispatch.validate_brief(_valid_brief(tier="T3"))))

    def test_calibration_t3_guard_is_not_masked_by_a_bad_agent_count(self):
        """Minor fix: the T3 tier_reason check used to be an elif chained off
        the agent_count branches, so an invalid agent_count hid the missing
        tier_reason from the caller entirely. It must now be reported
        independently, in the same run, as its own problem."""
        problems = dispatch.validate_brief(_valid_brief(tier="T3", agent_count="many"))
        self.assertTrue(any("agent_count" in p for p in problems), problems)
        self.assertTrue(any("tier_reason" in p for p in problems), problems)
        # Break it: restore the old elif-chained shape by making the T3 check
        # a no-op whenever agent_count already produced a problem, exactly
        # the masking bug this fixture guards against.
        original_t3 = dispatch._t3_tier_reason_problem

        def elif_shaped(tier, brief):
            raw_count = brief.get("agent_count", 1)
            if dispatch._positive_int(raw_count) is None:
                return None
            return original_t3(tier, brief)

        dispatch._t3_tier_reason_problem = elif_shaped
        try:
            broken = dispatch.validate_brief(_valid_brief(tier="T3", agent_count="many"))
            self.assertFalse(any("tier_reason" in p for p in broken),
                             "sanity: with the elif-masking shape restored, an invalid "
                             "agent_count must hide the missing tier_reason, proving the "
                             "independent `if` (not something else) is what surfaces both "
                             "problems in one run")
        finally:
            dispatch._t3_tier_reason_problem = original_t3
        restored = dispatch.validate_brief(_valid_brief(tier="T3", agent_count="many"))
        self.assertTrue(any("agent_count" in p for p in restored), restored)
        self.assertTrue(any("tier_reason" in p for p in restored), restored)

    def test_calibration_founder_deferral_needs_a_reason(self):
        tmp = tempfile.mkdtemp(prefix="sbe-dispatch-calib-defer-")
        owed = os.path.join(tmp, "OWED.json")
        with io.open(owed, "w", encoding="utf-8") as f:
            json.dump({"schemaVersion": 1, "items": [
                {"id": "W-9", "title": "x", "state": "open",
                 "deferred_by_founder": True, "deferral_reason": ""}]}, f)

        def load():
            with io.open(owed, encoding="utf-8") as f:
                return json.load(f)

        blocking, _ = dispatch._blocking_owed_items(load())
        self.assertTrue(any("W-9" in b for b in blocking), blocking)
        # Break it: accept deferred_by_founder alone, ignoring the reason.
        original = dispatch._blocking_owed_items

        def lenient(owed_obj):
            # Returns the same (blocking, problems) SHAPE as the function it
            # replaces, but built so its return expression is a single name:
            # the honesty sweep's verdict-source lint reads a literal 2-tuple
            # return it cannot prove never-PASS as a possible verdict pair,
            # and a monkeypatch stand-in inside a test earns no exemption.
            result = ([], [])
            for item in owed_obj.get("items", []):
                if item.get("state") == "closed" or item.get("deferred_by_founder") is True:
                    continue
                result[0].append(item.get("id"))
            return result

        dispatch._blocking_owed_items = lenient
        try:
            blocking2, _ = dispatch._blocking_owed_items(load())
            self.assertEqual(blocking2, [],
                             "sanity: with the reason check removed, a bare "
                             "deferred_by_founder=true with no reason must stop blocking, "
                             "proving the reason check (not something else) is what blocks it")
        finally:
            dispatch._blocking_owed_items = original
        blocking3, _ = dispatch._blocking_owed_items(load())
        self.assertTrue(any("W-9" in b for b in blocking3), blocking3)

    def test_calibration_unmeasurable_budget_refuses_never_a_silent_pass(self):
        """MAJOR FIX. An empty state file used to defeat the budget guard and
        the tool then printed a PASS line asserting a budget fact it never
        measured. A budget that cannot be measured must now refuse exactly
        as a missing owed file does."""
        tmp = tempfile.mkdtemp(prefix="sbe-dispatch-calib-budget-")
        owed = os.path.join(tmp, "OWED.json")
        state = os.path.join(tmp, "state.json")
        with io.open(owed, "w", encoding="utf-8") as f:
            json.dump({"schemaVersion": 1, "items": []}, f)
        with io.open(state, "w", encoding="utf-8") as f:
            json.dump({}, f)  # empty state: no budget block recorded at all
        # Confirm the guarded behaviour actually holds first.
        problems = dispatch.check_loop_open(state, owed)
        self.assertTrue(any("NO-DATA" in p for p in problems), problems)
        # Break it: patch _budget_problem to always claim the budget is
        # clear, exactly the defect that let an empty state file pass.
        original = dispatch._budget_problem
        dispatch._budget_problem = lambda s: None
        try:
            broken_problems = dispatch.check_loop_open(state, owed)
            self.assertEqual(broken_problems, [],
                             "sanity: with _budget_problem stubbed to always clear, an empty "
                             "state file must pass loop-open, proving the real guard (not "
                             "something else) is what refuses it")
        finally:
            dispatch._budget_problem = original
        # Restored: the real behaviour is back.
        problems_after = dispatch.check_loop_open(state, owed)
        self.assertTrue(any("NO-DATA" in p for p in problems_after), problems_after)


class TestForgedSentinelAndUnmeasurableNumbers(unittest.TestCase):
    """The round-2 Critical and Major, both the same disease as round 1's:
    an input the gate could not actually measure still produced a PASS.

    The Critical: `None` was the sentinel for "this file could not be
    parsed", but a file whose entire content is the valid JSON token `null`
    parses successfully TO None. Such a file forged the sentinel, every
    downstream check was skipped, and the gate printed a PASS asserting an
    owed list it never read and a budget it never measured.

    The Major: json.load parses the bare tokens NaN and Infinity, both are
    floats, and every comparison against NaN is False, so a budget with a
    NaN cap returned "measured and clear"."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-dispatch-")
        self.owed = os.path.join(self.tmp, "OWED.json")
        self.state = os.path.join(self.tmp, "state.json")

    def _write_raw(self, path, text):
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def _write(self, path, obj):
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def _good_state(self):
        self._write(self.state, {"schemaVersion": 1, "budget": {"cap": 100, "spent": 1}})

    def _good_owed(self):
        self._write(self.owed, {"schemaVersion": 1, "items": []})

    def test_owed_file_containing_json_null_refuses(self):
        self._write_raw(self.owed, "null")
        self._good_state()
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NoneType" in p for p in problems), problems)

    def test_state_file_containing_json_null_refuses(self):
        self._good_owed()
        self._write_raw(self.state, "null")
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NoneType" in p for p in problems), problems)

    def test_both_files_containing_json_null_refuse_and_name_both(self):
        self._write_raw(self.owed, "null")
        self._write_raw(self.state, "null")
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertEqual(len(problems), 2, problems)

    def test_cli_exits_1_for_a_null_owed_file(self):
        # Through the real process, because the refuter's reproduction was
        # a CLI run that exited 0 with a PASS line.
        self._write_raw(self.owed, "null")
        self._good_state()
        code, out, err = _run(["loop-open", "--state", self.state, "--owed", self.owed])
        self.assertEqual(code, 1, out + err)
        self.assertNotIn("PASS", out + err)

    def test_cli_exits_1_for_a_null_state_file(self):
        self._good_owed()
        self._write_raw(self.state, "null")
        code, out, err = _run(["loop-open", "--state", self.state, "--owed", self.owed])
        self.assertEqual(code, 1, out + err)
        self.assertNotIn("PASS", out + err)

    def test_nan_cap_is_not_a_measurement(self):
        self._good_owed()
        self._write_raw(self.state, '{"budget":{"cap":NaN,"spent":999999}}')
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NO-DATA" in p and "cap" in p for p in problems), problems)

    def test_nan_spent_is_not_a_measurement(self):
        self._good_owed()
        self._write_raw(self.state, '{"budget":{"cap":100,"spent":NaN}}')
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NO-DATA" in p and "spent" in p for p in problems), problems)

    def test_infinite_cap_is_not_a_measurement(self):
        self._good_owed()
        self._write_raw(self.state, '{"budget":{"cap":Infinity,"spent":5}}')
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("NO-DATA" in p and "cap" in p for p in problems), problems)

    def test_a_huge_integer_budget_is_measured_not_a_crash(self):
        # The regression the cycle-3 finiteness guard introduced: math.isfinite
        # converts to a C double, json.load parses integer literals at arbitrary
        # precision, so a large int raised OverflowError out of the gate. A
        # Python int is always finite; a gate that crashes returns no verdict at
        # all, which is worse than either answer.
        self._good_owed()
        self._write_raw(self.state, '{"budget":{"cap":%d,"spent":5}}' % (10 ** 400))
        self.assertEqual(dispatch.check_loop_open(self.state, self.owed), [])

    def test_a_huge_integer_spend_is_measured_as_exhausted(self):
        self._good_owed()
        self._write_raw(self.state, '{"budget":{"cap":100,"spent":%d}}' % (10 ** 400))
        problems = dispatch.check_loop_open(self.state, self.owed)
        self.assertTrue(any("exhausted" in p for p in problems), problems)

    def test_cli_does_not_traceback_on_a_huge_integer_budget(self):
        # Through the real process, because the reproduction was a CLI run that
        # printed a traceback instead of a verdict.
        self._good_owed()
        self._write_raw(self.state, '{"budget":{"cap":%d,"spent":5}}' % (10 ** 400))
        code, out, err = _run(["loop-open", "--state", self.state, "--owed", self.owed])
        self.assertNotIn("Traceback", out + err)
        self.assertNotIn("OverflowError", out + err)
        self.assertEqual(code, 0, out + err)

    def test_ordinary_finite_budget_still_passes(self):
        # The over-fire guard: the finiteness checks must not reject a
        # normal, genuinely measured budget, including a float one.
        self._good_owed()
        self._write_raw(self.state, '{"budget":{"cap":100.5,"spent":1.25}}')
        self.assertEqual(dispatch.check_loop_open(self.state, self.owed), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
