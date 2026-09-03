#!/usr/bin/env python3
"""The consumer action's minting step, and the discovery it rests on.

Run: python3 tools/test_sbe_consumer_mint.py

WHAT THIS GUARDS. The consumer job now runs the registered checks a diff
requires and evaluates its own output, because evidence belongs to the run that
does the work. The alternative that needed no code was committing receipts into
the repository, and that is the anti-pattern this project exists to refuse: a
job that merely READS a checked-in receipt proves nothing about the run that
made it.

The discovery step is the part that can go quietly wrong. If it under-reports,
a required check is never minted and the evaluation reports it MISSING, which
is loud and safe. If it OVER-reports it wastes a run, which is cheap. The
dangerous direction is a parse failure that looks like "nothing required", so
that case is asserted explicitly: an unreadable report yields no check names
AND says so on standard error, leaving the evaluation to report every
requirement it cannot see satisfied.
"""

import io
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
ACTION_DIR = os.path.join(ROOT, ".github", "actions", "sbe-consumer")
SCRIPT = os.path.join(ACTION_DIR, "required_checks.py")
ACTION = os.path.join(ACTION_DIR, "action.yml")


def discover(payload):
    """Run the real script the action runs, and return (names, stderr, code)."""
    out = subprocess.run([sys.executable, SCRIPT], input=payload,
                         capture_output=True, text=True)
    return out.stdout.split(), out.stderr, out.returncode


class WhatDiscoveryNames(unittest.TestCase):
    def test_it_names_check_requirements_and_nothing_else(self):
        report = {"requirements": [
            {"id": "check:control-plane-tests", "kind": "check"},
            {"id": "approval:control-plane-owner", "kind": "approval"},
            {"id": "tier:T3", "kind": "tier"},
            {"id": "check:contract-compatibility"},
        ]}
        names, _, code = discover(json.dumps(report))
        self.assertEqual(code, 0)
        self.assertEqual(names, ["control-plane-tests", "contract-compatibility"],
                         "discovery named %r; an approval or a tier is not a check and "
                         "cannot be minted by running a command" % names)

    def test_it_reads_both_shapes_this_report_has_used(self):
        """`kind` and the `check:` prefix have coexisted. Reading only one would
        silently mint nothing for the other, and the miss would look like a
        requirement that simply was not there."""
        by_kind, _, _ = discover(json.dumps({"requirements": [
            {"id": "migration-rehearsal", "kind": "check"}]}))
        by_prefix, _, _ = discover(json.dumps({"requirements": [
            {"id": "check:migration-rehearsal"}]}))
        self.assertEqual(by_kind, ["migration-rehearsal"])
        self.assertEqual(by_prefix, ["migration-rehearsal"])

    def test_a_repeated_requirement_is_run_once(self):
        names, _, _ = discover(json.dumps({"requirements": [
            {"id": "check:a"}, {"id": "check:a"}, {"id": "check:b"}]}))
        self.assertEqual(names, ["a", "b"])

    def test_an_unreadable_report_names_nothing_and_says_so(self):
        """The one direction that could hide a requirement. It must not look
        like 'nothing was required'."""
        names, err, code = discover("this is not json")
        self.assertEqual(names, [])
        self.assertEqual(code, 0, "a parse failure that exits nonzero would fail the job "
                                  "before the evaluation could report what is missing")
        self.assertIn("did not parse", err,
                      "the report was unreadable and nothing said so on standard error")

    def test_an_empty_report_names_nothing(self):
        for payload in ("{}", json.dumps({"requirements": []})):
            names, _, _ = discover(payload)
            self.assertEqual(names, [])


class WhatTheActionWiresUp(unittest.TestCase):
    def setUp(self):
        with io.open(ACTION, encoding="utf-8") as handle:
            self.action = handle.read()

    def test_the_action_manifest_parses(self):
        """It has not, once, and the failure took the whole job with it."""
        try:
            import yaml
        except ImportError:
            sys.stderr.write("NO-DATA: PyYAML is absent here, so the manifest was not "
                             "parsed by this run. That is an absence, not a pass.\n")
            return
        data = yaml.safe_load(self.action)
        steps = data["runs"]["steps"]
        self.assertTrue(any(step.get("id") == "mint" for step in steps),
                        "the minting step is gone, so the job asks for evidence it never "
                        "produces and every control-plane change stays blocked")

    def test_the_minting_step_has_no_if_that_could_skip_it(self):
        """Every step in this file once carried an `if:` that let an omitted
        input skip a control. That is the defect the whole file was rewritten to
        close, and a new step is exactly where it would come back."""
        block = self.action.split("id: mint", 1)[1].split("- name:", 1)[0]
        # A YAML `if:` is a step key at six spaces of indent. The bash `if [`
        # inside the run: scalar is not one, and an earlier version of this
        # assertion could not tell them apart, which would have failed a
        # correct step forever.
        keys = [line.strip() for line in block.split("\n")
                if line.startswith("      ") and not line.startswith("       ")]
        self.assertFalse([k for k in keys if k.startswith("if:")],
                         "the minting step carries an `if:` key, so omitting an input "
                         "skips it, which is the defect this whole file was rewritten "
                         "to close")

    def test_every_sbe_invocation_names_the_binary_not_the_path(self):
        """sbe-path is a DIRECTORY. Invoking it as a command produces empty
        output and an unparseable report, which the discovery script then
        correctly reports as "did not parse" while the job fails for a reason
        that looks like a policy problem and is not.

        Caught in CI rather than here, which is why this assertion exists: the
        first version of the minting step wrote `"$SBE_PATH" "$@"` while every
        other step in the same file wrote `"$SBE_PATH/bin/sbe"`."""
        block = self.action.split("id: mint", 1)[1].split("- name:", 1)[0]
        bad = [line.strip() for line in block.split("\n")
               if '"$SBE_PATH"' in line and "/bin/sbe" not in line]
        self.assertEqual(bad, [],
                         "the minting step invokes the sbe PATH as a command: %r. It is a "
                         "directory; every invocation must name $SBE_PATH/bin/sbe" % bad)

    def test_a_supplied_receipts_dir_is_respected_rather_than_overridden(self):
        self.assertIn('if [ -n "$SBE_RECEIPTS_DIR" ]; then', self.action,
                      "the step no longer yields to a caller-supplied receipts directory")

    def test_the_evaluation_consumes_what_the_step_minted(self):
        self.assertIn("steps.mint.outputs.dir", self.action,
                      "the evaluation does not read the minting step's output, so the "
                      "receipts are written and then ignored")


if __name__ == "__main__":
    unittest.main(verbosity=2)
