"""test_wire_birth_gate: proves the birth gate refuses what it must and lets
through what it must not touch.

Every scenario builds a throwaway git repository under a TemporaryDirectory
(never the real /private/tmp/lanes/ORCH-1020 checkout, so this suite never
depends on, or perturbs, this estate's own scripts/check_all.sh or history)
with its own fake scripts/ directory and its own fake check_all.sh, so
"registered" and "new" are both under this test's control rather than
whatever happens to be true of the real repository on the day it runs.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wire_birth_gate as gate


def _git(args, cwd):
    proc = subprocess.run(["git"] + args, cwd=cwd,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args, cwd, proc.stderr))
    return proc.stdout


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class FakeRepo(object):
    """A committed baseline: scripts/check_all.sh plus a couple of parts.

    Baseline carries:
      scripts/registered.py     -- named in check_all.sh's run_check line
      scripts/existing_unwired.py -- a real part, never registered, already
                                      committed (stands in for the 71)
    Both exist at HEAD before any test touches anything, so committing more
    never invalidates the baseline a test built on top of it.
    """

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="wire-birth-gate-test-")
        self.scripts = os.path.join(self.root, "scripts")
        os.makedirs(self.scripts)
        _git(["init", "-q"], self.root)
        _git(["config", "user.email", "test@example.com"], self.root)
        _git(["config", "user.name", "Test"], self.root)
        self.battery = os.path.join(self.scripts, "check_all.sh")
        _write(self.battery,
               '#!/bin/sh\n'
               'run_check "registered-check" python3 scripts/registered.py\n')
        _write(os.path.join(self.scripts, "registered.py"),
               '"""registered: a baseline part the battery already runs."""\n')
        _write(os.path.join(self.scripts, "existing_unwired.py"),
               '"""existing_unwired: a baseline part nothing in the battery '
               'runs, standing in for the estate\'s real 71."""\n')
        _git(["add", "-A"], self.root)
        _git(["commit", "-q", "-m", "baseline"], self.root)
        self.exemptions = os.path.join(self.root, "exemptions.json")  # not created unless a test wants it

    def add_module(self, name, sub=None, docstring=None):
        """Writes (but does not commit) scripts/[sub/]name.py. Returns its path."""
        directory = os.path.join(self.scripts, sub) if sub else self.scripts
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "%s.py" % name)
        _write(path, '"""%s: %s"""\n' % (name, docstring or "a part added by a test"))
        return path

    def register(self, name, check_name=None):
        """Appends a run_check line for `name` to the battery, on disk only."""
        with open(self.battery, "a", encoding="utf-8") as fh:
            fh.write('run_check "%s" python3 scripts/%s.py\n' %
                      (check_name or ("%s-check" % name), name))

    def commit_all(self, message="more"):
        _git(["add", "-A"], self.root)
        _git(["commit", "-q", "-m", message], self.root)

    def write_exemptions(self, obj_or_text):
        text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
        _write(self.exemptions, text)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class BirthGateTests(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepo()

    def tearDown(self):
        self.repo.cleanup()

    def check(self, paths, exemptions=True):
        exemptions_path = self.repo.exemptions if exemptions else None
        return gate.check_paths(paths, battery_path=self.repo.battery,
                                 exemptions_path=exemptions_path)

    # --- the core rule -----------------------------------------------------

    def test_empty_path_list_is_allowed(self):
        result = self.check([])
        self.assertTrue(result.allowed)
        self.assertEqual(result.unwired, [])

    def test_new_part_registered_in_the_same_call_is_allowed(self):
        path = self.repo.add_module("brand_new")
        self.repo.register("brand_new")  # on disk, uncommitted: same-commit case
        result = self.check([path])
        self.assertTrue(result.allowed, result.reason)
        self.assertIn("brand_new", result.registered)

    def test_new_unregistered_part_is_refused(self):
        path = self.repo.add_module("orphan")
        result = self.check([path])
        self.assertFalse(result.allowed)
        self.assertIn("orphan", result.unwired)
        self.assertIn("orphan", result.reason)
        self.assertIn("Tier C", result.reason)
        self.assertIn("run_check", result.reason)

    def test_refusal_names_the_part_and_both_ways_forward(self):
        path = self.repo.add_module("orphan2")
        result = self.check([path])
        self.assertIn("orphan2", result.reason)
        self.assertIn("register it", result.reason)
        self.assertIn("Tier C", result.reason)

    # --- rule 4: modifying an existing unwired part is never refused -------

    def test_editing_an_existing_unwired_part_is_allowed(self):
        path = os.path.join(self.repo.scripts, "existing_unwired.py")
        _write(path, '"""existing_unwired: edited, still nothing runs it."""\n')
        result = self.check([path])
        self.assertTrue(result.allowed, result.reason)
        self.assertEqual(result.unwired, [])

    def test_editing_an_existing_registered_part_is_allowed(self):
        path = os.path.join(self.repo.scripts, "registered.py")
        _write(path, '"""registered: edited but still the same part."""\n')
        result = self.check([path])
        self.assertTrue(result.allowed, result.reason)

    # --- exemptions ----------------------------------------------------

    def test_valid_tier_c_exemption_allows_a_birth(self):
        path = self.repo.add_module("reporter_only")
        self.repo.write_exemptions({
            "reporter_only": {"tier": "C", "reason": "prints a summary, decides nothing"}
        })
        result = self.check([path])
        self.assertTrue(result.allowed, result.reason)
        self.assertIn("reporter_only", result.exempt)

    def test_tier_a_exemption_is_refused(self):
        path = self.repo.add_module("gatekeeper")
        self.repo.write_exemptions({
            "gatekeeper": {"tier": "A", "reason": "decides the merge verdict"}
        })
        result = self.check([path])
        self.assertFalse(result.allowed)
        self.assertIn("gatekeeper", result.unwired)
        self.assertIn("Tier A", result.reason)

    def test_tier_b_exemption_is_refused(self):
        path = self.repo.add_module("influencer")
        self.repo.write_exemptions({
            "influencer": {"tier": "B", "reason": "feeds the merge decision"}
        })
        result = self.check([path])
        self.assertFalse(result.allowed)
        self.assertIn("influencer", result.unwired)

    def test_exemption_with_empty_reason_is_refused(self):
        """THE MUTATION TARGET: delete the reason check in _validate_exemption
        and this goes green on a part that should still be refused."""
        path = self.repo.add_module("no_reason_given")
        self.repo.write_exemptions({
            "no_reason_given": {"tier": "C", "reason": ""}
        })
        result = self.check([path])
        self.assertFalse(result.allowed)
        self.assertIn("no_reason_given", result.unwired)
        self.assertIn("empty or missing reason", result.reason)

    def test_exemption_with_missing_reason_key_is_refused(self):
        path = self.repo.add_module("no_reason_key")
        self.repo.write_exemptions({"no_reason_key": {"tier": "C"}})
        result = self.check([path])
        self.assertFalse(result.allowed)
        self.assertIn("no_reason_key", result.unwired)

    def test_missing_exemptions_file_is_treated_as_no_exemptions(self):
        path = self.repo.add_module("orphan3")
        # self.repo.exemptions was never written: file does not exist.
        result = self.check([path])
        self.assertFalse(result.allowed)  # still refused: no exemption exists
        self.assertIn("orphan3", result.unwired)

    def test_exemptions_path_none_is_treated_as_no_exemptions(self):
        path = self.repo.add_module("orphan4")
        result = gate.check_paths([path], battery_path=self.repo.battery,
                                   exemptions_path=None)
        self.assertFalse(result.allowed)

    def test_malformed_exemptions_file_raises_not_silently_empty(self):
        path = self.repo.add_module("orphan5")
        self.repo.write_exemptions("{ not valid json")
        with self.assertRaises(gate.BirthGateError):
            self.check([path])

    def test_exemptions_file_that_is_a_json_list_raises(self):
        path = self.repo.add_module("orphan6")
        self.repo.write_exemptions("[1, 2, 3]")
        with self.assertRaises(gate.BirthGateError):
            self.check([path])

    # --- battery failure modes ----------------------------------------

    def test_missing_battery_refuses_rather_than_allows(self):
        path = self.repo.add_module("orphan7")
        missing_battery = os.path.join(self.repo.scripts, "no_such_check_all.sh")
        with self.assertRaises(gate.BirthGateError):
            gate.check_paths([path], battery_path=missing_battery,
                              exemptions_path=self.repo.exemptions)

    def test_missing_battery_never_blocks_a_pure_modification(self):
        """A broken battery should not even be consulted when nothing in the
        call is a birth: lazy evaluation, and the safe direction stays safe
        without punishing unrelated work."""
        path = os.path.join(self.repo.scripts, "existing_unwired.py")
        _write(path, '"""existing_unwired: edited."""\n')
        missing_battery = os.path.join(self.repo.scripts, "no_such_check_all.sh")
        result = gate.check_paths([path], battery_path=missing_battery,
                                   exemptions_path=self.repo.exemptions)
        self.assertTrue(result.allowed)

    # --- the false-match and nested-directory edges --------------------

    def test_substring_of_a_registered_name_is_not_a_false_match(self):
        # "registered" is a real registered module; "regis" and
        # "registered_extra" must not be confused with it by any substring
        # test, only by exact module-name equality.
        path = self.repo.add_module("regis")
        result = self.check([path])
        self.assertFalse(result.allowed)
        self.assertIn("regis", result.unwired)

        path2 = self.repo.add_module("registered_extra")
        result2 = self.check([path2])
        self.assertFalse(result2.allowed)
        self.assertIn("registered_extra", result2.unwired)

    def test_nested_new_part_is_always_unwired_even_if_battery_would_match(self):
        # Register the bare name "nested_one" at top level (so a naive
        # basename-only lookup would wrongly call it registered), but the
        # actual new file lives in a subdirectory, which system_doc can never
        # prove registered.
        self.repo.register("nested_one")
        path = self.repo.add_module("nested_one", sub="sub")
        result = self.check([path])
        self.assertFalse(result.allowed)
        self.assertIn("nested_one", result.unwired)
        self.assertIn("subdirectory", result.reason)

    def test_nested_new_part_can_still_be_exempted(self):
        path = self.repo.add_module("nested_reporter", sub="sub")
        self.repo.write_exemptions({
            "nested_reporter": {"tier": "C", "reason": "a nested generator"}
        })
        result = self.check([path])
        self.assertTrue(result.allowed, result.reason)
        self.assertIn("nested_reporter", result.exempt)

    # --- renames ---------------------------------------------------------

    def test_renamed_part_old_name_registered_new_name_is_not(self):
        old_path = os.path.join(self.repo.scripts, "registered.py")
        new_path = os.path.join(self.repo.scripts, "registered_v2.py")
        os.rename(old_path, new_path)
        # check_all.sh still names "registered", not "registered_v2".
        result = self.check([new_path])
        self.assertFalse(result.allowed)
        self.assertIn("registered_v2", result.unwired)

    # --- paths this gate should never touch -----------------------------

    def test_path_outside_scripts_directory_is_ignored(self):
        outside = os.path.join(self.repo.root, "docs", "plan.py")
        os.makedirs(os.path.dirname(outside))
        _write(outside, '"""not a scripts/ part."""\n')
        result = self.check([outside])
        self.assertTrue(result.allowed)
        self.assertEqual(result.unwired, [])

    def test_a_test_file_is_never_refused(self):
        path = os.path.join(self.repo.scripts, "test_orphan.py")
        _write(path, '"""test_orphan: a new test file, never itself a part."""\n')
        result = self.check([path])
        self.assertTrue(result.allowed)
        self.assertEqual(result.unwired, [])

    def test_non_python_file_is_ignored(self):
        path = os.path.join(self.repo.scripts, "notes.md")
        _write(path, "not code\n")
        result = self.check([path])
        self.assertTrue(result.allowed)

    # --- git edge: brand new repository, no HEAD yet ---------------------

    def test_repository_with_no_commits_treats_every_path_as_a_birth(self):
        empty_root = tempfile.mkdtemp(prefix="wire-birth-gate-empty-")
        try:
            scripts = os.path.join(empty_root, "scripts")
            os.makedirs(scripts)
            _git(["init", "-q"], empty_root)
            battery = os.path.join(scripts, "check_all.sh")
            _write(battery, "#!/bin/sh\n")
            path = os.path.join(scripts, "brand_new_in_empty_repo.py")
            _write(path, '"""brand_new_in_empty_repo: no HEAD to compare to."""\n')
            result = gate.check_paths([path], battery_path=battery, exemptions_path=None)
            self.assertFalse(result.allowed)
            self.assertIn("brand_new_in_empty_repo", result.unwired)
        finally:
            shutil.rmtree(empty_root, ignore_errors=True)

    def test_path_outside_any_git_repository_raises(self):
        outside_git = tempfile.mkdtemp(prefix="wire-birth-gate-no-git-")
        try:
            scripts = os.path.join(outside_git, "scripts")
            os.makedirs(scripts)
            battery = os.path.join(scripts, "check_all.sh")
            _write(battery, "#!/bin/sh\n")
            path = os.path.join(scripts, "orphan_no_git.py")
            _write(path, '"""orphan_no_git: not inside any git repository."""\n')
            with self.assertRaises(gate.BirthGateError):
                gate.check_paths([path], battery_path=battery, exemptions_path=None)
        finally:
            shutil.rmtree(outside_git, ignore_errors=True)

    # --- main(), the CLI / pre-commit-hook surface ------------------------

    def test_main_returns_zero_when_allowed(self):
        path = self.repo.add_module("cli_ok")
        self.repo.register("cli_ok")
        log = os.path.join(self.repo.root, "refusals.log")
        code = gate.main([path, "--battery", self.repo.battery,
                           "--exemptions", self.repo.exemptions, "--log", log])
        self.assertEqual(code, 0)

    def test_main_returns_one_and_logs_on_refusal(self):
        path = self.repo.add_module("cli_orphan")
        log = os.path.join(self.repo.root, "refusals.log")
        code = gate.main([path, "--battery", self.repo.battery,
                           "--exemptions", self.repo.exemptions, "--log", log])
        self.assertEqual(code, 1)
        self.assertEqual(gate._count_refusals(log), 1)

        code2 = gate.main([path, "--battery", self.repo.battery,
                            "--exemptions", self.repo.exemptions, "--log", log])
        self.assertEqual(code2, 1)
        self.assertEqual(gate._count_refusals(log), 2)

    def test_main_returns_two_on_gate_mechanism_failure(self):
        path = self.repo.add_module("cli_orphan2")
        missing_battery = os.path.join(self.repo.scripts, "does_not_exist.sh")
        log = os.path.join(self.repo.root, "refusals.log")
        code = gate.main([path, "--battery", missing_battery,
                           "--exemptions", self.repo.exemptions, "--log", log])
        self.assertEqual(code, 2)
        self.assertEqual(gate._count_refusals(log), 1)

    def test_count_refusals_is_zero_for_a_log_that_does_not_exist(self):
        log = os.path.join(self.repo.root, "never-written.log")
        self.assertEqual(gate._count_refusals(log), 0)

    def test_count_refusals_flag_prints_the_count(self):
        log = os.path.join(self.repo.root, "refusals.log")
        gate._log_refusal(log, ["a"], "reason a")
        gate._log_refusal(log, ["b"], "reason b")
        code = gate.main(["--count-refusals", "--log", log])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
