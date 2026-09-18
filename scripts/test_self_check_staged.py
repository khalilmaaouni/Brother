#!/usr/bin/env python3
"""What scripts/self_check_staged.py must keep true: it is LIVE, right now,
in .git/hooks/pre-commit of this repository (installed via --install-hook,
reusing scripts/private_terms_scan.py's own load_terms()/scan_text()), so a
defect here is not theoretical, it changes what a real commit is allowed to
carry. The property under test is that a forbidden dash or a forbidden term
added on a STAGED line is REFUSED (never silently passed), and that
NO-DATA (no terms file, an empty terms file, an unreadable diff) is never
treated as a clean pass either.

Every test here builds and destroys its OWN throwaway git repository under
the system temp directory. Nothing is ever staged or written into this
task's real repository, and no real private term is ever used as a fixture:
the synthetic term below is this test file's own invention, chosen to be
unlike anything on any real private-terms list, per this estate's own
recorded incident that a scanner's test data must never leak the terms it
forbids.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import self_check_staged as S  # noqa: E402

try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

# A synthetic forbidden term, invented for this suite only. Not a real
# client, team member or product name; chosen to be unmistakably a test
# fixture rather than a leaked real term.
FAKE_TERM = "zzzTestOnlyForbiddenTermQuoll"

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)


def sh(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=60)


class ScratchRepo(unittest.TestCase):
    """Base: a throwaway git repository, never the real one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scs-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        for args in (["init", "-q", "-b", "main"],
                     ["config", "user.email", "a@b.c"],
                     ["config", "user.name", "t"]):
            sh(["git"] + args, cwd=self.repo)
        with open(os.path.join(self.repo, "base.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("clean base\n")
        sh(["git", "add", "-A"], cwd=self.repo)
        sh(["git", "commit", "-q", "-m", "clean base"], cwd=self.repo)
        self.terms_path = os.path.join(self.tmp, "terms.txt")
        with open(self.terms_path, "w", encoding="utf-8") as fh:
            fh.write("%s\n" % FAKE_TERM)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stage(self, name, content):
        path = os.path.join(self.repo, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        sh(["git", "add", "-A"], cwd=self.repo)

    def scan(self, terms_path="__default__"):
        tp = self.terms_path if terms_path == "__default__" else terms_path
        return S.run_scan(cwd=self.repo, terms_path=tp)


class CleanStagedContentPasses(ScratchRepo):
    def test_clean_added_lines_with_a_real_terms_file_pass(self):
        self.stage("clean.md", "nothing wrong here\njust plain text\n")
        code, lines = self.scan()
        self.assertEqual(code, S.EXIT_CLEAN, lines)
        self.assertTrue(any("PASS" in l for l in lines), lines)

    def test_no_staged_changes_at_all_is_clean(self):
        # "empty" edge: nothing staged.
        code, lines = self.scan()
        self.assertEqual(code, S.EXIT_CLEAN, lines)
        self.assertTrue(any("0 added line" in l for l in lines), lines)


class DashesOnAddedLinesAreBlocked(ScratchRepo):
    def test_an_em_dash_on_an_added_line_is_blocked(self):
        self.stage("dash.md", "a line with an em dash %s in it\n" % EM_DASH)
        code, lines = self.scan()
        self.assertEqual(code, S.EXIT_FOUND, lines)
        self.assertTrue(any("BLOCK dash: dash.md:1" in l for l in lines),
                        lines)

    def test_an_en_dash_on_an_added_line_is_blocked(self):
        self.stage("dash2.md", "range 1%s2\n" % EN_DASH)
        code, lines = self.scan()
        self.assertEqual(code, S.EXIT_FOUND, lines)
        self.assertTrue(any("BLOCK dash: dash2.md:1" in l for l in lines),
                        lines)

    def test_many_offending_lines_across_multiple_files_are_all_reported(self):
        self.stage("a.md", "clean first line\nbad %s line\n" % EM_DASH)
        self.stage("b.md", "also bad %s line\n" % EN_DASH)
        code, lines = self.scan()
        self.assertEqual(code, S.EXIT_FOUND, lines)
        joined = "\n".join(lines)
        self.assertIn("a.md:2", joined)
        self.assertIn("b.md:1", joined)

    def test_a_dash_only_on_a_removed_line_is_never_flagged(self):
        # Scope: this hook reads only ADDED ("+") lines of the staged diff.
        # A dash that only ever existed in already-committed history, and is
        # being REMOVED, is pre_push_gate's job over the outgoing range, not
        # this hook's job over one commit in progress.
        self.stage("prior.md", "an old line with %s in it\n" % EM_DASH)
        sh(["git", "commit", "-q", "-m", "prior with dash"], cwd=self.repo)
        # Now remove that line and add a clean replacement.
        self.stage("prior.md", "a clean replacement line\n")
        code, lines = self.scan()
        self.assertEqual(code, S.EXIT_CLEAN, lines)


class PrivateTermsOnAddedLinesAreBlocked(ScratchRepo):
    def test_the_synthetic_forbidden_term_is_blocked(self):
        self.stage("secret.md", "this line names %s directly\n" % FAKE_TERM)
        code, lines = self.scan()
        self.assertEqual(code, S.EXIT_FOUND, lines)
        joined = "\n".join(lines)
        self.assertIn("BLOCK private term: secret.md:1", joined)

    def test_the_matched_term_itself_is_never_printed_only_its_length(self):
        self.stage("secret2.md", "again, %s appears here\n" % FAKE_TERM)
        code, lines = self.scan()
        self.assertEqual(code, S.EXIT_FOUND, lines)
        joined = "\n".join(lines)
        self.assertNotIn(FAKE_TERM, joined,
                         "the forbidden term leaked into the tool's own "
                         "output")
        self.assertIn("matched term length %d" % len(FAKE_TERM), joined)

    def test_a_term_glued_to_other_letters_is_not_a_whole_word_match(self):
        # Boundary: private_terms_scan.pattern_for() matches whole words
        # only; this is inherited behaviour, exercised here to prove the
        # composition (self_check_staged calling scan_text) preserves it
        # rather than, say, doing its own looser substring check.
        glued = FAKE_TERM + "suffixnotaboundary"
        self.stage("glued.md", "a word %s glued on\n" % glued)
        code, lines = self.scan()
        self.assertEqual(code, S.EXIT_CLEAN, lines)


class NoDataIsNeverAPass(ScratchRepo):
    """The law this module exists to serve: a check that could not run
    reports NO-DATA and is never mistaken for a clean result, even when the
    staged diff itself is perfectly innocent."""

    def test_missing_terms_file_is_nodata_even_with_a_clean_diff(self):
        self.stage("clean.md", "nothing wrong here\n")
        missing = os.path.join(self.tmp, "does-not-exist.txt")
        code, lines = self.scan(terms_path=missing)
        self.assertEqual(code, S.EXIT_NO_DATA, lines)
        self.assertTrue(any("NO-DATA" in l for l in lines), lines)

    def test_empty_terms_file_is_nodata_even_with_a_clean_diff(self):
        self.stage("clean.md", "nothing wrong here\n")
        empty = os.path.join(self.tmp, "empty-terms.txt")
        with open(empty, "w", encoding="utf-8"):
            pass
        code, lines = self.scan(terms_path=empty)
        self.assertEqual(code, S.EXIT_NO_DATA, lines)

    def test_not_a_git_repository_is_nodata_not_a_crash(self):
        outside = tempfile.mkdtemp(prefix="scs-not-a-repo-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        code, lines = S.run_scan(cwd=outside, terms_path=self.terms_path)
        self.assertEqual(code, S.EXIT_NO_DATA, lines)
        self.assertTrue(any("could not read the staged diff" in l
                            for l in lines), lines)

    def test_a_dash_still_reported_even_when_terms_file_is_also_missing(self):
        # A real finding must not be swallowed just because a second,
        # unrelated check is also NO-DATA: both problems should be visible.
        self.stage("dash.md", "bad %s line\n" % EM_DASH)
        missing = os.path.join(self.tmp, "does-not-exist.txt")
        code, lines = self.scan(terms_path=missing)
        self.assertEqual(code, S.EXIT_FOUND, lines)
        joined = "\n".join(lines)
        self.assertIn("BLOCK dash", joined)
        self.assertIn("NO-DATA", joined)


class ParseAddedLinesIsRobustToMalformedDiffText(unittest.TestCase):
    """"corrupt or truncated input" at the parser level, without needing a
    real git process at all: text that is not a well-formed unified diff
    must not raise."""

    def test_garbage_before_any_file_header_is_ignored_not_crashed_on(self):
        garbage = "not a real diff\njust some +garbage line\n"
        added = list(S.parse_added_lines(garbage))
        self.assertEqual(added, [])

    def test_a_plus_line_with_no_hunk_header_yet_is_ignored(self):
        text = ("diff --git a/f b/f\n"
                "--- a/f\n"
                "+++ b/f\n"
                "+orphan line with no @@ header\n")
        added = list(S.parse_added_lines(text))
        self.assertEqual(added, [])

    def test_a_well_formed_single_hunk_is_parsed_correctly(self):
        text = ("diff --git a/f b/f\n"
                "--- a/f\n"
                "+++ b/f\n"
                "@@ -0,0 +1,2 @@\n"
                "+first\n"
                "+second\n")
        added = list(S.parse_added_lines(text))
        self.assertEqual(added, [("f", 1, "first"), ("f", 2, "second")])


class HookInstallationOnAThrowawayRepo(ScratchRepo):
    """install_hook / check_hook, exercised only against this test's own
    throwaway repository's .git/hooks, never the real one."""

    def test_a_fresh_repo_reports_not_installed(self):
        code = S.check_hook(cwd=self.repo)
        self.assertEqual(code, S.EXIT_FOUND)

    def test_install_then_check_reports_installed(self):
        code = S.install_hook(cwd=self.repo)
        self.assertEqual(code, S.EXIT_CLEAN)
        code2 = S.check_hook(cwd=self.repo)
        self.assertEqual(code2, S.EXIT_CLEAN)

    def test_installing_twice_is_idempotent_already_done(self):
        S.install_hook(cwd=self.repo)
        hooks_dir = S.hooks_dir(cwd=self.repo)
        hook_path = os.path.join(hooks_dir, "pre-commit")
        with open(hook_path, encoding="utf-8") as fh:
            after_first = fh.read()
        code = S.install_hook(cwd=self.repo)
        self.assertEqual(code, S.EXIT_CLEAN)
        with open(hook_path, encoding="utf-8") as fh:
            after_second = fh.read()
        self.assertEqual(after_first, after_second,
                         "installing a second time must not duplicate the "
                         "hook block")

    def test_a_foreign_pre_existing_hook_is_composed_with_not_replaced(self):
        hooks_dir = S.hooks_dir(cwd=self.repo)
        os.makedirs(hooks_dir, exist_ok=True)
        hook_path = os.path.join(hooks_dir, "pre-commit")
        with open(hook_path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho somebody else's hook\n")
        code = S.check_hook(cwd=self.repo)
        self.assertEqual(code, S.EXIT_FOUND)  # "FOREIGN HOOK", not installed
        S.install_hook(cwd=self.repo)
        with open(hook_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("somebody else's hook", content,
                      "a foreign hook must be composed with, never "
                      "replaced")
        self.assertIn(S.MARK, content)


# Standing edge list, the rest: "a concurrent second actor" installing the
# hook at the same instant is a genuine, undemonstrated gap (install_hook's
# own check-then-append has no lock: two simultaneous --install-hook runs
# against a repo with an existing foreign hook could both read "MARK
# absent" before either writes, and both append, duplicating the block).
# Not exercised here as a deterministic test because reproducing that exact
# interleaving without editing the module (out of scope for this unit)
# would mean a timing-dependent, flaky test; it is reported as a possible
# defect instead, per this brief's instruction to report rather than fix.
# "Expired or stale" does not apply: a scan reads the CURRENT staged diff
# fresh at call time, there is nothing that can go stale between one
# invocation and the next. "The actor is the same as last time" does not
# apply either: nothing here tracks caller identity.


if __name__ == "__main__":
    unittest.main()
