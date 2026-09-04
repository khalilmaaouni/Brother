"""test_mutation_gate.py: proves scripts/mutation_gate.py actually mutates,
actually kills, and actually fails the gate by name when something does not
kill (R27.3, docs/plan/HARDENING-2026-08-30-CODEX.md mechanism 5).

Three things are proven:

  1. Every one of the four registered mutants has a UNIQUE anchor in the
     REAL current scripts/ source. If a later refactor moves or duplicates
     the seam, this fails here, before a real run silently starts reading
     NO-DATA for a class the brief says must stay seeded.

  2. THE FORWARD DRIVE: every one of the four real, registered mutants is
     actually run end to end (a fresh scratch copy, the real patch, the
     real named killer test file as a subprocess) and must come back
     KILLED. This is slower than a fixture test (it runs real product test
     suites against broken code) but it is the only way to know a killer
     test actually kills rather than merely existing.

  3. THE BACKWARDS DRIVE (the plan's own requirement): a mutant built to be
     genuinely unkillable (a comment-only change no test could ever notice)
     must come back SURVIVED from run_mutant(), and run_battery() over a
     registry containing it must return exit code 1 and name it by id in
     its own printed output. This is the mutation gate's meta-test proving
     the gate does not just always print KILLED.

Every mutation in this file lands in a tempfile.mkdtemp() scratch copy,
never in this repository's own scripts/ directory.
"""
import io
import os
import sys
import tokenize
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mutation_gate as MG  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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


class RegisteredMutantsMatchTheRealSource(unittest.TestCase):
    """Drive (1): every anchor is unique in the real, current source, right
    now, not as of whenever the anchor text was copied in."""

    def test_exactly_four_classes_are_registered(self):
        self.assertEqual(len(MG.MUTANTS), 4)
        classes = {e["class"] for e in MG.MUTANTS.values()}
        self.assertEqual(classes, {
            "termination-condition comparison flip",
            "tuple or dict field deletion",
            "boundary check removal",
            "parse-failure-to-continue",
        })

    def test_every_mutant_names_a_real_guard(self):
        for mutant_id, entry in MG.MUTANTS.items():
            self.assertTrue(entry["guards"].strip(),
                           "%s: guards must name the recorded miss it "
                           "protects, not be empty" % mutant_id)

    def test_every_anchor_is_unique_in_the_real_current_source(self):
        for mutant_id, entry in MG.MUTANTS.items():
            path = os.path.join(HERE, entry["target"])
            self.assertTrue(os.path.isfile(path),
                           "%s: target %s no longer exists" % (mutant_id, entry["target"]))
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            hits = text.count(entry["anchor_old"])
            self.assertEqual(hits, 1,
                            "%s: anchor now appears %d time(s) in %s (want "
                            "exactly 1); the seam moved or was duplicated "
                            "and this mutant's anchor needs updating"
                            % (mutant_id, hits, entry["target"]))

    def test_every_killer_test_file_exists(self):
        for mutant_id, entry in MG.MUTANTS.items():
            path = os.path.join(HERE, entry["killer"])
            self.assertTrue(os.path.isfile(path),
                           "%s: named killer %s does not exist"
                           % (mutant_id, entry["killer"]))


class TheForwardDriveEveryRealMutantIsActuallyKilled(unittest.TestCase):
    """Drive (2): each registered mutant, run for real, must die by its
    named killer. Slower (real subprocesses, real test suites) but this is
    the only way to know a killer test is not merely present but working."""

    def test_termination_flip_is_killed(self):
        r = MG.run_mutant("termination-flip", MG.MUTANTS["termination-flip"])
        self.assertEqual(r["verdict"], "KILLED", r["output"][-800:])

    def test_field_deletion_is_killed(self):
        r = MG.run_mutant("field-deletion", MG.MUTANTS["field-deletion"])
        self.assertEqual(r["verdict"], "KILLED", r["output"][-800:])
        self.assertIn("depends_on", r["output"])

    def test_boundary_removal_is_killed(self):
        r = MG.run_mutant("boundary-removal", MG.MUTANTS["boundary-removal"])
        self.assertEqual(r["verdict"], "KILLED", r["output"][-800:])

    def test_parse_failure_continue_is_killed(self):
        r = MG.run_mutant("parse-failure-continue",
                          MG.MUTANTS["parse-failure-continue"])
        self.assertEqual(r["verdict"], "KILLED", r["output"][-800:])
        self.assertIn("the decomposer was never asked", r["output"],
                     "the mutant should silence the real diagnostic and "
                     "leave only the placeholder reason behind")

    def test_the_real_battery_exits_zero_and_names_nothing_as_survived(self):
        results, code = MG.run_battery(MG.MUTANTS, out=io.StringIO())
        self.assertEqual(code, 0, results)
        self.assertTrue(all(r["verdict"] == "KILLED" for r in results), results)


class TheBackwardsDriveAnUnkillableMutantFailsTheGateByName(unittest.TestCase):
    """Drive (3), the plan's own required backwards drive: seed a mutant no
    observer covers (a comment-only change) and prove run_mutant() reports
    SURVIVED, and run_battery() fails the gate NAMING it, not just going
    red for an unrelated reason."""

    def _unkillable_entry(self):
        # A real, unique anchor in a real product module, but a change to a
        # COMMENT only: no test asserts anything about comment text, so no
        # killer can ever notice it moved. This is "mutate something no
        # observer covers", the plan's own second option for this drive.
        #
        # THE ANCHOR IS READ OUT OF THE REAL SOURCE, NEVER PINNED HERE. An
        # earlier version hard coded one docstring sentence from
        # claim_store.py, which made this fixture a second, silent copy of
        # product prose: the E85 refactor split live() into dead_reason()
        # plus a wrapper, moved that sentence one function down, and both
        # backwards drive tests went red on a stale fixture assumption
        # rather than on anything the gate actually guards. tokenize is
        # asked for the real comment tokens (never a "#" that only lives
        # inside a string literal), and the first whole line unique one
        # becomes the anchor, so a later comment edit leaves this drive
        # standing.
        target_path = os.path.join(HERE, "claim_store.py")
        with open(target_path, encoding="utf-8") as fh:
            text = fh.read()
        with open(target_path, "rb") as fh:
            try:
                tokens = list(tokenize.tokenize(fh.readline))
            except (tokenize.TokenError, SyntaxError) as exc:
                self.fail("claim_store.py did not tokenize, so no comment "
                          "anchor can be derived: %s" % exc)
        candidates = [t.line for t in tokens
                      if t.type == tokenize.COMMENT
                      and t.line.strip().startswith("#")
                      and t.line.endswith("\n")
                      and text.count(t.line) == 1]
        self.assertTrue(candidates,
                       "claim_store.py has no unique whole line comment left "
                       "to mutate, so this drive has nothing uncovered to "
                       "seed; point it at another product module rather than "
                       "dropping the drive")
        anchor_old = candidates[0]
        self.assertEqual(text.count(anchor_old), 1,
                        "the derived comment anchor is not unique in the "
                        "real current source")
        anchor_new = (anchor_old[:-1]
                      + " (rewritten by the mutation gate self test)\n")
        return {
            "class": "comment-only (deliberately unkillable, for this test)",
            "guards": "nothing; this entry exists only to prove the gate "
                      "reports an uncaught mutant honestly",
            "target": "claim_store.py",
            "anchor_old": anchor_old,
            "anchor_new": anchor_new,
            "killer": "test_claim_store.py",
        }

    def test_run_mutant_reports_survived_for_an_uncovered_change(self):
        entry = self._unkillable_entry()
        r = MG.run_mutant("unkillable-fixture", entry)
        self.assertEqual(r["verdict"], "SURVIVED",
                        "a comment-only change was caught by something; "
                        "either a test greps source text (fragile) or the "
                        "fixture accidentally touched real behavior: %s"
                        % r["detail"])

    def test_run_battery_fails_by_name_not_just_a_bare_nonzero(self):
        entry = self._unkillable_entry()
        mutants = {"unkillable-fixture": entry}
        out = io.StringIO()
        results, code = MG.run_battery(mutants, out=out)
        self.assertEqual(code, 1, results)
        printed = out.getvalue()
        self.assertIn("unkillable-fixture", printed,
                     "the gate must name the surviving mutation, not just "
                     "fail silently")
        self.assertIn("SURVIVED", printed)


class Mechanics(unittest.TestCase):
    def test_patch_unique_refuses_a_missing_anchor(self):
        patched, problem = MG._patch_unique("abc", "xyz", "q")
        self.assertIsNone(patched)
        self.assertIn("not found", problem)

    def test_patch_unique_refuses_a_duplicated_anchor(self):
        patched, problem = MG._patch_unique("aa", "a", "b")
        self.assertIsNone(patched)
        self.assertIn("not unique", problem)

    def test_a_missing_target_in_the_real_tree_is_no_data_not_a_crash(self):
        fake = dict(MG.MUTANTS["field-deletion"])
        fake["target"] = "no_such_module_at_all.py"
        results, code = MG.run_battery({"fake": fake}, out=io.StringIO())
        self.assertEqual(code, 2)
        self.assertEqual(results[0]["verdict"], MG.NODATA)

    def test_list_and_main_do_not_crash(self):
        self.assertEqual(MG.main(["--list"]), 0)


if __name__ == "__main__":
    unittest.main()
