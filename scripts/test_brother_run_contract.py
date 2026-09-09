"""U6, A-prime amendment 2: the door refuses to plan without a contracted
outcome record.

WHAT THIS SUITE PINS (docs/plan/PLAN-THREE-ENGINES-2026-09-08.md step 5, the
2026-09-08 debate judgment unit U6). Before this, a session could hand
brother_run an outcome in words and a plan in JSON, and nothing anywhere held
the QUESTION that was actually asked, the LANGUAGE the answer owes, or the
checks that would prove the outcome reached. The persona scenarios this
retires are the ones where the answer never read the question: an answer in
the wrong language, an outcome nobody asked for, a delivery whose proof
nobody named.

THE RULE, in three parts, one class each below:
  1. `--contract FILE` is checked by scripts/contract_check.py BEFORE any
     plan is read: a record that fails the schema refuses at exit 1 with the
     checker's own FAIL lines, a record still in `draft` refuses at exit 1
     naming the open question, and a contracted record passes and lands in
     the run's own target marker.
  2. Inside a Claude Code session, a run that is about to plan without a
     contract refuses at exit 2 with one NO-DATA line, exactly the shape
     D-001 used for a missing plan. Outside a session nothing changes.
  3. Every command the contract's `success_checks` promise must be run by
     some unit of the plan, or the plan is refused naming the checks by id.

No network and no real model: the worker is the same stub seam
scripts/test_brother_run_plan.py uses, and its shadowed PATH is reused
wholesale rather than spelled a second time here.
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BROTHER_RUN = os.path.join(HERE, "brother_run.py")
FIXTURE = os.path.join(HERE, "fixtures", "outcome-contract", "complete.json")

import brother_paths  # noqa: E402
# The repository's own fixture, its stub worker, its shadowed PATH and its
# plan writer, reused rather than rebuilt: two copies of that setUp would
# drift the first time either suite moved.
from test_brother_run_plan import (  # noqa: E402
    PlanFileRunBase, run_dirs, sh, two_units, write_plan,
)


def contract_at(tmp, question, commands=("test -f one.txt",),
                name="contract.json", **overrides):
    """A copy of the complete fixture, asking `question`, promising
    `commands`. `overrides` replaces top level fields (state, questions) and
    a value of None DELETES the field, which is how the missing-language
    record below is built without hand writing a second fixture."""
    with open(FIXTURE, encoding="utf-8") as fh:
        record = json.load(fh)
    record["question"] = question
    record["success_checks"] = [
        {"id": "sc-%d" % (i + 1), "command": command, "expect": "exit-0"}
        for i, command in enumerate(commands)]
    for key, value in overrides.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
    return path


def target_of(runs_root):
    """The one run's target marker, parsed. None when no run opened."""
    dirs = run_dirs(runs_root)
    if len(dirs) != 1:
        return None
    path = os.path.join(runs_root, "docs", "plan", "runs", dirs[0],
                        "target.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class AContractedRecordReachesTheWorkerAndTheRunRemembersIt(PlanFileRunBase):

    def test_a_contract_and_a_covering_plan_integrate_and_land_in_the_marker(self):
        """U6's own done-check. A contracted record plus a plan whose
        done_checks run the checks it promises reaches the workers, the
        integration and the receipt, and the run's target marker names the
        contract, the language the answer owes and the question asked, so
        delivery reads them from the run rather than being told again."""
        outcome = "two files exist"
        contract = contract_at(self.tmp, outcome,
                               commands=("test -f one.txt", "test -f two.txt"))
        plan = write_plan(self.tmp, two_units())
        proc = sh([sys.executable, BROTHER_RUN, outcome,
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan, "--contract", contract], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (2):", out, out)
        self.assertIn("brother_run: receipt: ", out, out)

        marker = target_of(self.tmp)
        self.assertIsNotNone(marker, "no run target marker was written: %s" % out)
        self.assertIn("contract", marker,
                      "the run forgot the contract it was given: %r" % marker)
        self.assertEqual(marker["contract"]["path"], os.path.abspath(contract))
        self.assertEqual(marker["contract"]["language"], "en")
        self.assertEqual(marker["contract"]["question"], outcome)

    def test_a_draft_record_is_refused_by_its_own_open_question(self):
        """A draft carries a question nobody has answered. Planning against
        it plans for the wrong outcome, so the run refuses at exit 1 and
        prints the question itself, not a code."""
        question = "Which ticket covers this change?"
        contract = contract_at(
            self.tmp, "two files exist", state="draft",
            questions=[{"field": "ticket", "question": question}])
        plan = write_plan(self.tmp, two_units())
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan, "--contract", contract], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("draft", out, out)
        self.assertIn(question, out, out)
        self.assertEqual(run_dirs(self.tmp), [],
                         "a draft contract opened a run")
        self.assertEqual(self.spawned(), [], out)

    def test_a_record_the_checker_rejects_is_refused_in_the_checkers_words(self):
        """The verdict is contract_check's, not a second implementation of
        the schema: a record with no `language` refuses at exit 1 carrying
        the checker's own FAIL line."""
        contract = contract_at(self.tmp, "two files exist", language=None)
        plan = write_plan(self.tmp, two_units())
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan, "--contract", contract], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("contract_check: FAIL: language: missing required field",
                      out, out)
        self.assertEqual(run_dirs(self.tmp), [], out)
        self.assertEqual(self.spawned(), [], out)


class InsideASessionAPlanWithoutAContractIsRefused(PlanFileRunBase):

    def test_a_plan_and_no_contract_in_a_session_is_no_data_at_exit_2(self):
        """The session half of A-prime amendment 2, in D-001's own shape: one
        NO-DATA line naming the flag and what writes the record, nothing
        claimed, nothing run, no run directory."""
        env = dict(self.env)
        env["CLAUDECODE"] = "1"
        plan = write_plan(self.tmp, two_units())
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("NO-DATA", out, out)
        self.assertIn("--contract", out, out)
        self.assertIn("bm_project.py adopt", out, out)
        self.assertEqual(run_dirs(self.tmp), [], "a refusal opened a run")
        self.assertEqual(self.spawned(), [], out)


class OutsideASessionTheContractStaysOptional(PlanFileRunBase):
    """THE REGRESSION GUARD, and it is honestly labelled: this test passes
    before this unit and after it. Its job is to prove the rule above is a
    ROUTING rule rather than a new requirement on the documented headless
    path, which is exactly the property a refusal written one line too early
    would break."""

    def test_a_headless_plan_run_with_no_contract_is_unchanged(self):
        env = dict(self.env)
        for var in (tuple(brother_paths.CLAUDE_MARKER_VARS)
                    + tuple(brother_paths.CODEX_MARKER_VARS)):
            env.pop(var, None)
        plan = write_plan(self.tmp, two_units())
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan], env=env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (2):", out, out)
        self.assertEqual(self.spawned(), [], out)


class ThePlanMustRunEveryCheckTheContractPromises(PlanFileRunBase):

    def test_a_plan_missing_one_promised_check_is_refused_by_that_checks_id(self):
        """A contract promising two checks against a plan that runs one: the
        run refuses at exit 1, names the uncovered check by id and prints the
        command nobody runs, and nothing is claimed."""
        contract = contract_at(
            self.tmp, "two files exist",
            commands=("test -f one.txt", "python3 scripts/never_run.py"))
        plan = write_plan(self.tmp, two_units())
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan, "--contract", contract], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("sc-2", out, out)
        self.assertIn("python3 scripts/never_run.py", out, out)
        self.assertNotIn("sc-1", out,
                         "a check the plan DOES run was reported missing")
        self.assertEqual(run_dirs(self.tmp), [], out)
        self.assertEqual(self.spawned(), [], out)

    def test_a_done_check_that_starts_with_the_promised_command_counts(self):
        """Covered means the command IS a done_check or STARTS one, so a unit
        may add its own arguments or chain a second command after it. This is
        the boundary the rule above is drawn at, driven from the other side."""
        contract = contract_at(self.tmp, "two files exist",
                               commands=("test -f one.txt",))
        units = two_units()
        units[0]["done_check"] = "test -f one.txt && echo covered"
        plan = write_plan(self.tmp, units)
        proc = sh([sys.executable, BROTHER_RUN, "two files exist",
                   "--cwd", self.repo, "--runs-root", self.tmp,
                   "--plan", plan, "--contract", contract], env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("integrated (2):", out, out)


class AnInstalledRuntimeCarriesTheSchemaItChecksAgainst(unittest.TestCase):
    """The packaging half. bundle/runtime has no docs/ tree above it, so
    without a copy of the schema beside contract_check.py an installed
    `brother-run --contract` would read NO-DATA on every record, and the
    session rule above would refuse every installed session build. Measured
    on the shipped bundle, by running the shipped checker."""

    RUNTIME = os.path.join(HERE, "..", "bundle", "runtime")

    def test_the_shipped_checker_passes_the_complete_fixture_with_no_schema_flag(self):
        checker = os.path.join(self.RUNTIME, "contract_check.py")
        if not os.path.isfile(checker):
            self.skipTest("NO-DATA: %s is not in this tree" % checker)
        proc = sh([sys.executable, checker, FIXTURE])
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0,
                         "the shipped checker could not check a record with "
                         "no --schema flag, so an installed --contract run "
                         "reads NO-DATA on every record: %s" % out)
        self.assertIn("PASS", out, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
