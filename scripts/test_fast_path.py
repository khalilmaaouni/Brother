#!/usr/bin/env python3
"""Self-test for scripts/fast_path.py (design-P2.md section 4, item 1: the
eligibility table and the escalation marker; items 2-4, the stubbed-run
escalation drive, the receipt-format comparison and the benchmark
extension, are NOT built here on purpose: this lane never touches
brother_run.py, see fast_path.py's own docstring and the P2 report's NOT
DONE line."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fast_path
import receipt_door


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                           text=True)


def _clean_repo():
    """A fresh git repo, one commit, clean working tree."""
    d = tempfile.mkdtemp(prefix="fast-path-test-")
    _git(["init", "-q"], d)
    _git(["config", "user.email", "test@example.com"], d)
    _git(["config", "user.name", "Test"], d)
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write("seed\n")
    _git(["add", "."], d)
    _git(["commit", "-q", "-m", "seed"], d)
    return d


def _unit(**kw):
    base = {"id": "U1", "objective": "a small named change",
            "done_check": "false", "owns": ["file.txt"]}
    base.update(kw)
    return base


class EligibilityTable(unittest.TestCase):
    """Twelve unsafe rows (each False with a reason naming the refusing
    classifier) plus two positive rows, driven against real git repos so
    integrate.dirty_paths and the done_check subprocess are exercised for
    real, not mocked."""

    @classmethod
    def setUpClass(cls):
        cls.clean = _clean_repo()
        # SECURITY FIX (finding 3, driven adversarial review, 2026-09-10):
        # eligible()'s already-passes probe now only ever runs for a
        # test-module-shaped done_check (scripts/test_<name>.py). These
        # two fixtures give the suite one real module that always fails
        # (for the "not already passing, continue" scenarios many tests
        # below rely on) and one that always passes (for the "already
        # passes" refusal itself).
        os.makedirs(os.path.join(cls.clean, "scripts"))
        with open(os.path.join(cls.clean, "scripts", "test_readme.py"),
                  "w") as f:
            f.write('assert False, "typo not fixed yet"\n')
        with open(os.path.join(cls.clean, "scripts", "test_alreadypass.py"),
                  "w") as f:
            # a real, trivially-passing unittest.TestCase: this must exit
            # 0 run directly ("python3 scripts/test_alreadypass.py") AND
            # under "python3 -m unittest scripts.test_alreadypass" (which
            # discovers zero test methods as exit code 5, NOT 0, for a
            # module with no TestCase in it at all).
            f.write(
                "import unittest\n\n\n"
                "class Trivial(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        pass\n\n\n"
                "if __name__ == \"__main__\":\n"
                "    unittest.main()\n")
        _git(["add", "."], cls.clean)
        _git(["commit", "-q", "-m", "scripts test-module fixtures"],
             cls.clean)
        cls.dirty = _clean_repo()
        with open(os.path.join(cls.dirty, "scratch.txt"), "w") as f:
            f.write("uncommitted\n")
        cls.non_git = tempfile.mkdtemp(prefix="fast-path-nongit-")

    @classmethod
    def tearDownClass(cls):
        for d in (cls.clean, cls.dirty, cls.non_git):
            shutil.rmtree(d, ignore_errors=True)

    def _refused(self, unit, cwd, want_in_reason):
        ok, reason = fast_path.eligible("demo", unit, cwd)
        self.assertFalse(ok, "expected ineligible, got reason=%r" % (reason,))
        self.assertIn(want_in_reason, reason.lower(),
                       "reason %r does not name %r" % (reason, want_in_reason))

    # -- receipt_door.risk_triggers rows -----------------------------------

    def test_auth_wording_refused(self):
        self._refused(_unit(objective="rotate the login password hash"),
                       self.clean, "risk_triggers")

    def test_migration_wording_refused(self):
        self._refused(_unit(objective="backfill the orders table"),
                       self.clean, "risk_triggers")

    def test_money_wording_refused(self):
        self._refused(_unit(objective="process the refund payment"),
                       self.clean, "risk_triggers")

    def test_destructive_done_check_refused(self):
        self._refused(_unit(done_check="rm -rf build/"),
                       self.clean, "risk_triggers")

    def test_public_api_path_refused(self):
        self._refused(_unit(objective="change the public API endpoint"),
                       self.clean, "risk_triggers")

    def test_risk_word_only_in_owns_path_refused_even_with_innocent_objective(self):
        """Hostile case 1 (night run 2026-09-09 hostile-cases battery): a
        risky path disguised as a tiny change. The OBJECTIVE sentence names
        nothing risky at all; the risk word lives only in the declared
        write path itself. receipt_door._unit_text reads objective, title,
        name, done_check AND owns into one string before risk_triggers
        scans it (receipt_door.py, _unit_text), so a path a person might
        skim past (a filename, not a sentence) is caught the same way a
        risky sentence is: eligible() never normalizes or trims owns
        before handing it to risk_triggers, unlike objective."""
        self._refused(
            _unit(objective="polish the wording, nothing risky here",
                 owns=["login.py"]),
            self.clean, "risk_triggers")

    # -- fast_path's own rules ----------------------------------------------

    def test_three_declared_paths_refused(self):
        self._refused(_unit(owns=["a.txt", "b.txt", "c.txt"]),
                       self.clean, "declared write paths")

    def test_second_unit_dependency_refused(self):
        self._refused(_unit(depends_on=["U0"]),
                       self.clean, "depends")

    def test_empty_done_check_refused(self):
        self._refused(_unit(done_check=""), self.clean, "done_check")

    def test_manifest_write_refused(self):
        self._refused(_unit(owns=["requirements.txt"]),
                       self.clean, "fast_forbidden_paths")

    # -- SECURITY FIX finding 1 (driven adversarial review, 2026-09-10): --
    # -- FAST_FORBIDDEN_PATHS / FAST_FORBIDDEN_SEGMENT compared case-    --
    # -- insensitively; the filesystem itself is case insensitive, so a --
    # -- differently-cased spelling of a forbidden path used to pass.   --

    def test_forbidden_path_case_insensitive_requirements_refused(self):
        self._refused(_unit(owns=["Requirements.txt"]),
                       self.clean, "fast_forbidden_paths")

    def test_forbidden_path_case_insensitive_system_md_refused(self):
        self._refused(_unit(owns=["SYSTEM.MD"]),
                       self.clean, "fast_forbidden_paths")

    def test_forbidden_path_case_insensitive_bundle_refused(self):
        self._refused(_unit(owns=["Bundle/x.py"]),
                       self.clean, "fast_forbidden_paths")

    def test_forbidden_path_case_insensitive_github_workflows_refused(self):
        self._refused(_unit(owns=[".GitHub/workflows/ci.yml"]),
                       self.clean, "fast_forbidden_paths")

    def test_dirty_tree_refused(self):
        self._refused(_unit(done_check="false"), self.dirty, "dirty tree")

    def test_dirty_paths_none_refused(self):
        self._refused(_unit(done_check="false"), self.non_git,
                       "could not run")

    def test_check_already_passing_refused(self):
        """SECURITY FIX (finding 3): the already-passes probe only ever
        runs for a test-module-shaped done_check now, so this fixture uses
        scripts/test_alreadypass.py (setUpClass) rather than a bare "true"
        -- "true" is no longer even offered to the probe, see
        test_non_test_module_done_check_refused_as_not_probeable below."""
        self._refused(
            _unit(done_check="python3 scripts/test_alreadypass.py"),
            self.clean, "already passes")

    # -- SECURITY FIX finding 3 (driven adversarial review, 2026-09-10): --
    # -- the already-passes probe used to run subprocess.run(shell=True) --
    # -- on WHATEVER done_check held, including an arbitrary shell       --
    # -- command brother_run.py's fast-route candidate builder can pull  --
    # -- out of the target repository's own scripts/check_all.sh        --
    # -- registry, before any human screen. The probe now runs only for --
    # -- a done_check matching the fixed test-module shape.             --

    def test_non_test_module_done_check_refused_as_not_probeable(self):
        """A registry-style shell command (the concrete attack shape the
        finding named) carries no risk word at all, so it used to sail
        past receipt_door.risk_triggers and straight into a shell-mode
        subprocess call. It is now refused before the probe ever runs,
        with a reason naming why in plain words."""
        self._refused(
            _unit(done_check="bash scripts/check_all.sh"),
            self.clean, "not a test module")

    def test_test_module_done_check_module_form_recognized(self):
        """The "python3 -m unittest scripts.test_<name>" form
        brother_run.py's own _fast_route_test_module_check builds is the
        other accepted shape, not only the direct .py invocation.
        scripts/test_alreadypass.py (setUpClass) trivially exits 0
        whether run directly or loaded by unittest with zero test cases,
        so this reaching "already passes" (not "not a test module") is
        proof the module form reaches the probe."""
        self._refused(
            _unit(done_check="python3 -m unittest scripts.test_alreadypass"),
            self.clean, "already passes")

    # -- work_record.check_units row (a twelfth unsafe row: no write scope) -

    def test_no_write_scope_refused(self):
        self._refused(_unit(owns=[]), self.clean, "check_units")

    # -- consolidation additions (night run 2026-09-09): the filesystem ----
    # -- half of path canonicalization, and deny-term evasion normalization

    def test_symlink_write_scope_refused(self):
        d = tempfile.mkdtemp(prefix="fast-path-symlink-")
        try:
            _git(["init", "-q"], d)
            _git(["config", "user.email", "test@example.com"], d)
            _git(["config", "user.name", "Test"], d)
            with open(os.path.join(d, "real.txt"), "w") as f:
                f.write("real\n")
            os.symlink(os.path.join(d, "real.txt"),
                       os.path.join(d, "link.txt"))
            _git(["add", "real.txt"], d)
            _git(["commit", "-q", "-m", "seed"], d)
            self._refused(_unit(owns=["link.txt"]), d, "symlink")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_symlinked_parent_directory_write_scope_refused(self):
        """REPAIR ROUND 3 F2 (driven): the first cut of
        _owns_canonicalization_problem asked os.path.islink about the LEAF
        of the joined path only, so a symlinked PARENT directory -- never
        the leaf itself -- escaped the repository with every gate green.
        Here `d/subdir` is a symlink pointing at a directory OUTSIDE the
        repository entirely, and `subdir/file.txt` (the declared owns
        entry) is an ordinary, non-symlink file at the far end of it."""
        outside = tempfile.mkdtemp(prefix="fast-path-outside-")
        d = tempfile.mkdtemp(prefix="fast-path-symlinkdir-")
        try:
            _git(["init", "-q"], d)
            _git(["config", "user.email", "test@example.com"], d)
            _git(["config", "user.name", "Test"], d)
            with open(os.path.join(d, "README.md"), "w") as f:
                f.write("seed\n")
            _git(["add", "."], d)
            _git(["commit", "-q", "-m", "seed"], d)
            os.makedirs(os.path.join(outside, "escaped"))
            with open(os.path.join(outside, "escaped", "file.txt"), "w") as f:
                f.write("real\n")
            os.symlink(os.path.join(outside, "escaped"),
                      os.path.join(d, "subdir"))
            self._refused(_unit(owns=["subdir/file.txt"]), d, "symlink")
        finally:
            shutil.rmtree(d, ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)

    def test_directory_write_scope_refused(self):
        d = tempfile.mkdtemp(prefix="fast-path-dir-")
        try:
            _git(["init", "-q"], d)
            _git(["config", "user.email", "test@example.com"], d)
            _git(["config", "user.name", "Test"], d)
            os.makedirs(os.path.join(d, "adir"))
            with open(os.path.join(d, "README.md"), "w") as f:
                f.write("seed\n")
            _git(["add", "."], d)
            _git(["commit", "-q", "-m", "seed"], d)
            self._refused(_unit(owns=["adir"]), d, "not a plain file")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_not_yet_existing_write_scope_is_not_refused_by_canonicalization(self):
        """_owns_canonicalization_problem alone (the EARLY, symlink/non-file
        structural check) still tolerates a not-yet-existing path on
        purpose: it stays the cheap early gate so a fixture's own specific
        refusal reason (a risk word, a forbidden path, a dirty tree) is
        never masked by 'does not exist'. See the next test for the whole
        picture: eligible() itself still refuses a missing file, just at
        the very end (REPAIR ROUND 3 F4, _owns_missing_problem below)."""
        problem = fast_path._owns_canonicalization_problem(
            ["brand-new-file.txt"], self.clean)
        self.assertIsNone(problem)

    def test_owns_missing_file_refused_only_after_every_other_condition(self):
        """REPAIR ROUND 3 F4: brother_run.py's fast-route candidate builder
        (_fast_route_file_candidate) has always required os.path.exists
        for every named file; fast_path.py used to document and code a
        'not yet written is fine' allowance that route never reaches. A
        unit that clears every other FAST-0 condition but names a file
        that was never created is now refused here, and only here (no
        risk word, no FAST-0 word class, an existing, test-module-shaped
        check that already fails, a clean tree): the missing-file reason
        names the path. done_check is scripts/test_readme.py (setUpClass,
        always fails), not "false" (finding 3: "false" is not a
        test-module shape and would now be refused earlier)."""
        unit = _unit(objective="fix the typo in the notes",
                     done_check="python3 scripts/test_readme.py",
                     owns=["never-created.txt"])
        ok, reason = fast_path.eligible("demo", unit, self.clean)
        self.assertFalse(ok)
        self.assertIn("never-created.txt", reason)
        self.assertIn("does not exist", reason.lower())

    def test_owns_missing_problem_direct(self):
        self.assertIsNone(
            fast_path._owns_missing_problem(["README.md"], self.clean))
        problem = fast_path._owns_missing_problem(
            ["never-created.txt"], self.clean)
        self.assertIn("never-created.txt", problem)
        self.assertIn("does not exist", problem)

    def test_deny_term_zero_width_evasion_refused(self):
        self._refused(
            _unit(objective="update the notes for the au​th flow"),
            self.clean, "risk_triggers")

    def test_deny_term_hyphenated_evasion_refused(self):
        self._refused(
            _unit(objective="update the notes, touching a back-fill job"),
            self.clean, "risk_triggers")

    def test_deny_term_mixed_case_evasion_refused(self):
        self._refused(_unit(objective="rotate the LOGIN PassWord"),
                       self.clean, "risk_triggers")

    def test_deny_term_code_fence_evasion_refused(self):
        self._refused(
            _unit(objective="update the notes, see ```auth``` for context"),
            self.clean, "risk_triggers")

    # -- SECURITY FIX finding 2 (driven adversarial review, 2026-09-10): --
    # -- _normalize_for_risk never applied a Unicode normalization form, --
    # -- so a fullwidth or combining-mark spelling of a trigger word     --
    # -- missed risk_triggers on both the raw and the old normalized     --
    # -- copy. Cyrillic (or other script) homoglyphs are OUT OF SCOPE:   --
    # -- NFKD decomposes width/compatibility variants, it does not map   --
    # -- a look-alike letter from a different script to the Latin one;   --
    # -- closing that needs a confusables table, a separate fix.        --

    def test_deny_term_fullwidth_evasion_refused(self):
        self._refused(
            _unit(objective="update the notes for the "
                            "ａｕｔｈ flow"),
            self.clean, "risk_triggers")

    def test_deny_term_combining_mark_evasion_refused(self):
        # "a" + U+0301 (COMBINING ACUTE ACCENT) + "uth", not ASCII "auth".
        self._refused(
            _unit(objective="update the notes for the a\u0301uth flow"),
            self.clean, "risk_triggers")

    # -- FAST-0's own native word classes (REPAIR ROUND 3 F3, driven) ------

    def test_concurrency_wording_refused(self):
        unit = _unit(
            objective=("Add a lock around the counter in scripts/journal.py "
                      "to avoid a race between threads"),
            done_check="false", owns=["scripts/journal.py"])
        self._refused(unit, self.clean, "fast-0 word class")

    def test_broad_refactor_wording_refused(self):
        unit = _unit(
            objective=("Refactor every module under scripts/ to use the "
                      "new logger, starting with scripts/journal.py"),
            done_check="false", owns=["scripts/journal.py"])
        self._refused(unit, self.clean, "fast-0 word class")

    def test_schema_change_wording_refused(self):
        unit = _unit(
            objective="Change the schema of the claims store in scripts/claim_store.py",
            done_check="false", owns=["scripts/claim_store.py"])
        self._refused(unit, self.clean, "fast-0 word class")

    def test_rename_across_all_files_wording_refused(self):
        unit = _unit(
            objective=("Rename the helper across all files, starting in "
                      "scripts/journal.py"),
            done_check="false", owns=["scripts/journal.py"])
        self._refused(unit, self.clean, "fast-0 word class")

    def test_readme_typo_fix_still_eligible(self):
        """The FAST-0 word class table (F3) must never widen past the four
        driven classes above: an ordinary typo fix names none of
        concurrency, broad scope, or schema/data-shape wording, and stays
        eligible exactly as it did before F3. done_check is now
        scripts/test_readme.py (setUpClass), which always fails, rather
        than "false" (finding 3: "false" is not a test-module shape and
        would now be refused earlier as unprobeable)."""
        unit = _unit(objective="Fix the typo in README.md",
                     done_check="python3 scripts/test_readme.py",
                     owns=["README.md"])
        ok, reason = fast_path.eligible("demo", unit, self.clean)
        self.assertTrue(ok, "typo fix refused: %s" % reason)

    def test_done_check_hyphen_not_stripped_by_normalization(self):
        """The normalization above must never touch done_check: the
        irreversibility pattern's own "rm, whitespace, -rf" depends on the
        hyphen surviving. Proven directly here so a future edit that widens
        normalization to done_check breaks this test before it ships."""
        self._refused(_unit(done_check="rm -rf build/"), self.clean,
                       "risk_triggers")

    # -- two positive rows, docs and code, mirroring tiny_task_cost.py ------

    def test_docs_case_eligible(self):
        """REPAIR ROUND 3 F4: the candidate builder in brother_run.py has
        always required the named file to already exist
        (_fast_route_file_candidate's own os.path.exists check), so this
        fixture now creates NOTES.md up front rather than relying on the
        dead 'not yet written is fine' allowance F4 removed. A dedicated
        repo, not self.clean: writing NOTES.md there would leave every
        other test in this class reading a dirtied shared fixture.

        SECURITY FIX (finding 3): done_check is now a test module under
        scripts/ (the only shape the already-passes probe accepts), a
        stand-in for the old inline "test -f ... && grep ..." shell
        check, which is no longer a probeable shape."""
        d = tempfile.mkdtemp(prefix="fast-path-docs-")
        try:
            _git(["init", "-q"], d)
            _git(["config", "user.email", "test@example.com"], d)
            _git(["config", "user.name", "Test"], d)
            os.makedirs(os.path.join(d, "scripts"))
            with open(os.path.join(d, "NOTES.md"), "w") as f:
                f.write("notes\n")
            with open(os.path.join(d, "scripts", "test_docs.py"), "w") as f:
                f.write(
                    "assert \"written\" in open(\"NOTES.md\").read(), "
                    "\"not fixed yet\"\n")
            _git(["add", "."], d)
            _git(["commit", "-q", "-m", "seed"], d)
            unit = {"id": "D1", "objective": "add the missing line to the notes file",
                     "done_check": "python3 scripts/test_docs.py",
                     "owns": ["NOTES.md"]}
            ok, reason = fast_path.eligible("docs", unit, d)
            self.assertTrue(ok, "docs case refused: %s" % reason)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_code_case_eligible(self):
        """SECURITY FIX (finding 3): test_widget.py moved under scripts/
        (the only done_check shape the already-passes probe accepts now),
        so the module reaches up one directory to import widget.py from
        the repository root."""
        d = tempfile.mkdtemp(prefix="fast-path-code-")
        try:
            _git(["init", "-q"], d)
            _git(["config", "user.email", "test@example.com"], d)
            _git(["config", "user.name", "Test"], d)
            os.makedirs(os.path.join(d, "scripts"))
            with open(os.path.join(d, "widget.py"), "w") as f:
                f.write("def width():\n    return 2\n")
            with open(os.path.join(d, "scripts", "test_widget.py"),
                      "w") as f:
                f.write(
                    "import os, sys\n"
                    "sys.path.insert(0, os.path.dirname(os.path.dirname("
                    "os.path.abspath(__file__))))\n"
                    "import widget\n"
                    "assert widget.width() == 3, 'not fixed yet'\n"
                    "print('ok')\n")
            _git(["add", "."], d)
            _git(["commit", "-q", "-m", "seed"], d)
            unit = {"id": "C1", "objective": "make the existing test pass",
                     "done_check": "python3 scripts/test_widget.py",
                     "owns": ["widget.py"]}
            ok, reason = fast_path.eligible("code", unit, d)
            self.assertTrue(ok, "code case refused: %s" % reason)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # -- hostile cases 8 and 9 (night run 2026-09-09 hostile-cases battery) -

    def test_multiple_simultaneous_failures_report_only_the_first_reason(self):
        """Hostile case 8: the intent line for an ineligible outcome names
        the FIRST reason and only one reason. This unit fails three
        conditions at once (more than 2 declared paths, a risk word in its
        objective, a dirty tree); eligible() must return exactly the
        reason for the FIRST condition it checks in its own documented
        order (work_record, canonicalization, the 2-path cap, depends_on,
        risk_triggers, autonomy_dial, FAST_FORBIDDEN_PATHS, dirty tree,
        check-already-passes), never a combined listing of every reason
        that also happens to be true."""
        unit = _unit(owns=["a.txt", "b.txt", "c.txt"],
                     objective="rotate the login password hash")
        ok, reason = fast_path.eligible("demo", unit, self.dirty)
        self.assertFalse(ok)
        self.assertIn("declared write paths", reason)
        self.assertNotIn("risk_triggers", reason)
        self.assertNotIn("dirty tree", reason)

    def test_done_check_unrelated_to_owns_file_is_still_eligible(self):
        """Hostile case 9: an outcome naming a file and a check where the
        check exists but does not import or reference the named file.
        fast_path.eligible's own eligibility table (this file's own
        design-P2.md section 3, steering 8.3) has no condition that binds
        done_check to owns: it proves work_record scope, receipt_door risk
        wording, autonomy_dial classification, FAST_FORBIDDEN_PATHS, a
        clean git base and the check-already-passes rule, and stops there.
        A check that tests something else entirely is eligible as long as
        it independently fails before the work and the rest of the table
        holds, exactly as it is here: the done_check greps a file that has
        nothing to do with widget.py."""
        d = tempfile.mkdtemp(prefix="fast-path-mismatch-")
        try:
            _git(["init", "-q"], d)
            _git(["config", "user.email", "test@example.com"], d)
            _git(["config", "user.name", "Test"], d)
            os.makedirs(os.path.join(d, "scripts"))
            with open(os.path.join(d, "widget.py"), "w") as f:
                f.write("def width():\n    return 2\n")
            with open(os.path.join(d, "sentinel.txt"), "w") as f:
                f.write("not yet\n")
            # SECURITY FIX (finding 3): the old inline "grep -q done
            # sentinel.txt" is not a test-module shape and would now be
            # refused before the probe ever ran; this test module keeps
            # exactly the same unrelated-to-widget.py content check.
            with open(os.path.join(d, "scripts", "test_sentinel.py"),
                      "w") as f:
                f.write(
                    "assert open('sentinel.txt').read().strip() == "
                    "'done', 'not yet'\n")
            _git(["add", "."], d)
            _git(["commit", "-q", "-m", "seed"], d)
            unit = {"id": "M1", "objective": "update widget.py",
                     "done_check": "python3 scripts/test_sentinel.py",
                     "owns": ["widget.py"]}
            ok, reason = fast_path.eligible("mismatch", unit, d)
            self.assertTrue(
                ok, "a check unrelated to the owns file must still be "
                    "eligible: fast_path.py names no rule binding them "
                    "(%s)" % reason)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TheReceiptNamesTheRealCheckEvenWhenMismatched(unittest.TestCase):
    """Hostile case 9's other half: since fast_path.py has no rule binding
    done_check to owns (proven above), receipt_door.per_file_checks, the
    function that turns a run's own receipts into per-file entries, must
    never paper over that by CLAIMING the check tested the file. It must
    record the check_command exactly as it ran, honestly, whatever it
    was: a stranger reading the receipt can re-run that exact command and
    see for themselves that it never touched the file it is filed under.
    """

    def test_per_file_entry_records_the_actual_mismatched_check_command(self):
        record = {"rows": [{"id": "M1", "owns": ["widget.py"],
                            "files_changed_by_unit": ["widget.py"]}]}
        receipts = [{"id": "M1", "state": "verified",
                     "command": "grep -q done sentinel.txt",
                     "exit_code": 0, "output_location": "run.log",
                     "reason": ""}]
        entries = receipt_door.per_file_checks(record, receipts)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["file"], "widget.py")
        # HONEST: the real command that ran, not a synthesized claim that
        # it verified widget.py's own content.
        self.assertEqual(entry["check_command"], "grep -q done sentinel.txt")
        self.assertEqual(entry["exit_code"], 0)


class NeverRaises(unittest.TestCase):
    """eligible() on garbage input always returns a (bool, str) tuple."""

    def _safe(self, outcome, unit, cwd):
        result = fast_path.eligible(outcome, unit, cwd)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        ok, reason = result
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)
        self.assertFalse(ok)

    def test_all_none(self):
        self._safe(None, None, None)

    def test_wrong_types(self):
        self._safe(1, 2, 3)

    def test_unit_not_a_dict(self):
        self._safe("x", ["not", "a", "dict"], ".")

    def test_owns_not_a_list(self):
        self._safe("x", {"id": "U1", "done_check": "true", "owns": "a.txt"}, ".")

    def test_nonexistent_cwd(self):
        self._safe("x", {"id": "U1", "done_check": "true", "owns": ["a"]},
                    "/no/such/path/anywhere-xyz")

    def test_done_check_not_a_string(self):
        self._safe("x", {"id": "U1", "done_check": 12345, "owns": ["a"]}, ".")

    def test_internal_collaborator_raising_is_ineligible_not_raised(self):
        """Consolidation (night run 2026-09-09): "any exception in
        eligibility makes the outcome ineligible" must hold for a failure
        INSIDE a call eligible() makes, not only for malformed input to
        eligible() itself (the other tests in this class). work_record is
        a real dependency eligible() calls early; force it to raise and
        confirm the outer try/except still turns that into (False, reason)
        rather than letting the exception escape."""
        with mock.patch.object(fast_path.work_record, "check_units",
                               side_effect=RuntimeError("boom")):
            ok, reason = fast_path.eligible(
                "x", {"id": "U1", "done_check": "true", "owns": ["a.txt"]},
                ".")
        self.assertFalse(ok)
        self.assertIn("RuntimeError", reason)
        self.assertIn("boom", reason)


class Escalation(unittest.TestCase):

    def test_empty_undeclared_is_none(self):
        self.assertIsNone(fast_path.escalation([], "next command"))
        self.assertIsNone(fast_path.escalation(None, "next command"))

    def test_marker_and_paths_and_next_command(self):
        line = fast_path.escalation(["a/b.py", "c/d.py"], "brother_run.py --resume X")
        self.assertIsNotNone(line)
        self.assertTrue(line.startswith(fast_path.ESCALATION_MARKER))
        self.assertIn("a/b.py", line)
        self.assertIn("c/d.py", line)
        self.assertIn("brother_run.py --resume X", line)


if __name__ == "__main__":
    unittest.main()
