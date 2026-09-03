#!/usr/bin/env python3
"""Tests for bm_private_scan, on tiny synthetic git repos built with subprocess.

Run: python3 tools/test_bm_private_scan.py      (unittest output, exit 0 or 1)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(TOOL_DIR, "bm_private_scan.py")

sys.path.insert(0, TOOL_DIR)
import bm_private_scan as PS  # noqa: E402

ENV = dict(os.environ)
ENV["GIT_AUTHOR_NAME"] = "Test"
ENV["GIT_AUTHOR_EMAIL"] = "test@example.com"
ENV["GIT_COMMITTER_NAME"] = "Test"
ENV["GIT_COMMITTER_EMAIL"] = "test@example.com"


def _git(repo, args):
    p = subprocess.run(["git", "-C", repo] + args, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, env=ENV)
    if p.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args, p.stderr.decode("utf-8", "replace")))
    return p.stdout.decode("utf-8", "replace")


def _init_repo():
    repo = tempfile.mkdtemp(prefix="bm_private_scan_test_")
    _git(repo, ["init", "-q", "-b", "main"])
    return repo


def _write(repo, relpath, text):
    full = os.path.join(repo, relpath)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, "w") as f:
        f.write(text)


def _commit(repo, message):
    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-q", "-m", message])


def _write_terms(path, terms):
    with open(path, "w") as f:
        f.write("# test terms\n")
        for t in terms:
            f.write(t + "\n")


def run(argv):
    p = subprocess.run([sys.executable, TOOL] + argv,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class PrivateContentScan(unittest.TestCase):
    """Each case builds its own throwaway repo, so these are independent and may
    run in any order. The fixture TERMS are invented (Norvantis, Atrium, RAFT)
    and deliberately keep the SHAPE of the real classes they stand in for: a
    long term matched case-insensitively, and a four character all-caps term
    that must not fire on an ordinary lowercase word containing those letters.
    """

    def setUp(self):
        self.tmp_terms = tempfile.mkdtemp(prefix="bm_private_scan_terms_")
        self.addCleanup(shutil.rmtree, self.tmp_terms, ignore_errors=True)

    def _repo(self):
        repo = _init_repo()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        return repo

    def _terms(self, name, terms):
        path = os.path.join(self.tmp_terms, name)
        _write_terms(path, terms)
        return path

    def test_defect_1_the_case_hole_is_caught(self):
        # A capitalized term in the terms file, a lowercase spelling of it in the sole
        # blob in range. Long terms (>5 chars) are matched case-insensitively, so this
        # must be caught. The HIT line withholds the term itself since 2026-09-03
        # (masked to a character count), so this checks for that masked phrase and
        # that the literal fixture term is absent, rather than the term text.
        repo = self._repo()
        _write(repo, "notes.txt", "internal memo for norvantis project kickoff\n")
        _commit(repo, "add notes")
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms1", ["Norvantis"])])
        self.assertTrue(code == 2 and "HIT" in out
                        and ("a term of %d characters" % len("Norvantis")) in out
                        and "Norvantis" not in out,
                        "exit=%d out=%r" % (code, out))

    def test_defect_2_the_reachability_hole_is_caught(self):
        # The tip is clean, but an earlier commit on the same branch carries a dirty
        # blob. HEAD~1..HEAD would miss it; scanning the whole HEAD ancestry must not.
        # Same masked-phrase note as test_defect_1 above.
        repo = self._repo()
        _write(repo, "notes.txt", "internal memo for atrium project kickoff\n")
        _commit(repo, "add notes with the name")
        _write(repo, "notes.txt", "internal memo for the project kickoff\n")
        _commit(repo, "scrub the name from the tip")
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms2", ["Atrium"])])
        self.assertTrue(code == 2 and "HIT" in out
                        and ("a term of %d characters" % len("Atrium")) in out
                        and "Atrium" not in out,
                        "exit=%d out=%r" % (code, out))
        # And prove the point: the tip has no matches, confirming this is a
        # history-only leak that a checkout-level grep is structurally blind to.
        tip_grep = subprocess.run(["git", "-C", repo, "grep", "-F", "Atrium", "HEAD"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(tip_grep.returncode, 0,
                            "git grep at HEAD unexpectedly found the term "
                            "(the test fixture is wrong, not the tool)")

    def test_a_clean_repo_exits_zero(self):
        repo = self._repo()
        _write(repo, "notes.txt", "ordinary project notes, nothing private here\n")
        _commit(repo, "add notes")
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms3", ["Norvantis", "RAFT"])])
        self.assertTrue(code == 0 and "OK" in out, "exit=%d out=%r" % (code, out))

    def test_a_missing_terms_file_is_no_data_never_a_clean_zero(self):
        repo = self._repo()
        _write(repo, "notes.txt", "anything\n")
        _commit(repo, "add notes")
        missing_terms = os.path.join(self.tmp_terms, "does-not-exist")
        code, out = run(["--repo", repo, "--range", "HEAD", "--terms", missing_terms])
        self.assertTrue(code == 3 and "NO-DATA" in out, "exit=%d out=%r" % (code, out))

    def test_a_short_term_does_not_fire_on_a_word_containing_it(self):
        # The documented false-positive class. "RAFT" is 4 characters (short, so it
        # is matched whole-word, case-insensitively since 2026-09-03); "draft"
        # contains the letters but the word boundary does not match (a word
        # character sits right before "raft"), so the whole-word bound alone
        # spares this case regardless of case-folding.
        repo = self._repo()
        _write(repo, "notes.txt", "let's draft up and ship this\n")
        _commit(repo, "add notes")
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms5", ["RAFT"])])
        self.assertTrue(code == 0 and "OK" in out, "exit=%d out=%r" % (code, out))


class TheUnderscoreIsABoundary20260903(unittest.TestCase):
    """E37: pass (a) bounded on `[A-Za-z0-9_]`, so path_<term>_file walked
    through while the assurance product's history test (isalnum bounds)
    refused it. Driven both ways with an INVENTED four character term:
    underscore-adjacent is a hit; glued inside a run of letters is not."""

    def setUp(self):
        self.tmp_terms = tempfile.mkdtemp(prefix="bm_private_scan_terms_")
        self.addCleanup(shutil.rmtree, self.tmp_terms, ignore_errors=True)

    def _repo(self):
        repo = _init_repo()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        return repo

    def _terms(self, name, terms):
        path = os.path.join(self.tmp_terms, name)
        _write_terms(path, terms)
        return path

    def test_an_underscore_adjacent_short_term_is_a_pass_a_hit(self):
        repo = self._repo()
        _write(repo, "notes.txt", "see path_raft_file for the export\n")
        _commit(repo, "add notes")
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms_us1", ["RAFT"])])
        self.assertTrue(code == 2 and "HIT" in out and PS.PASS_A in out,
                        "exit=%d out=%r" % (code, out))

    def test_a_short_term_glued_inside_letters_is_still_not_a_hit(self):
        repo = self._repo()
        _write(repo, "notes.txt", "an ordinary xxraftxx token here\n")
        _commit(repo, "add notes")
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms_us2", ["RAFT"])])
        self.assertTrue(code == 0 and "OK" in out, "exit=%d out=%r" % (code, out))


class TheShortTermCaseHoleClosed20260903(unittest.TestCase):
    """The 2026-09-03 fix: pass (a), terms of PS.SHORT_TERM_MAX_LEN characters or
    fewer, now matches any case as a whole word, not only the case stored in the
    terms file (a lowercase spelling of a short client term passed the old
    scanner for five weeks). The term under test is read from the real
    machine-level list at PS.DEFAULT_TERMS_PATH and used at runtime, never copied
    into this file as a literal, mirroring TheFixturesCarryNothingPrivate in
    test_bm_queue_numbers.py (reuse of PS._load_terms, and NO-DATA rather than a
    literal or a hard failure when that list is missing). Failure messages below
    report booleans and character counts only, never the captured subprocess
    output, so a real term is never printed even when an assertion fails.
    """

    def setUp(self):
        self.tmp_terms = tempfile.mkdtemp(prefix="bm_private_scan_terms_")
        self.addCleanup(shutil.rmtree, self.tmp_terms, ignore_errors=True)

    def _repo(self):
        repo = _init_repo()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        return repo

    def _terms(self, name, terms):
        path = os.path.join(self.tmp_terms, name)
        _write_terms(path, terms)
        return path

    def _first_short_real_term(self):
        terms, no_data_reason = PS._load_terms(PS.DEFAULT_TERMS_PATH)
        if terms is None:
            self.skipTest("NO-DATA: %s" % no_data_reason)
        short_term = next((t for t in terms if len(t) <= PS.SHORT_TERM_MAX_LEN), None)
        if short_term is None:
            self.skipTest("NO-DATA: %s has no term of %d characters or fewer"
                          % (PS.DEFAULT_TERMS_PATH, PS.SHORT_TERM_MAX_LEN))
        return short_term

    def test_a_lowercase_spelling_of_a_short_term_is_a_pass_a_hit(self):
        short_term = self._first_short_real_term()
        lowered = short_term.lower()
        repo = self._repo()
        _write(repo, "notes.txt", "a memo mentioning %s in passing\n" % lowered)
        _commit(repo, "add notes")
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms_real_short", [short_term])])
        hit = code == 2 and "HIT" in out and PS.PASS_A in out
        self.assertTrue(hit,
                        "exit=%d HIT-present=%s PASS_A-present=%s (term is %d "
                        "characters, output withheld)"
                        % (code, "HIT" in out, PS.PASS_A in out, len(short_term)))

    def test_a_word_merely_containing_the_short_terms_letters_is_not_a_hit(self):
        short_term = self._first_short_real_term()
        lowered = short_term.lower()
        embedded = "zz%szz" % lowered
        repo = self._repo()
        _write(repo, "notes.txt", "an ordinary %s here, nothing to see\n" % embedded)
        _commit(repo, "add notes")
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms_real_short_embed", [short_term])])
        clean = code == 0 and "OK" in out
        self.assertTrue(clean,
                        "exit=%d OK-present=%s (term is %d characters, output "
                        "withheld)" % (code, "OK" in out, len(short_term)))

    def test_a_ref_named_with_the_short_term_is_masked_not_printed(self):
        # An opus auditor drove the scanner backwards: refs scanned in _list_refs
        # use the refname ITSELF as obj_id, so a branch or tag named after a term
        # printed it in the HIT line even though term= was already masked. Clean
        # content, only the branch name carries the term, isolating this from a
        # content-triggered hit.
        short_term = self._first_short_real_term()
        lowered = short_term.lower()
        repo = self._repo()
        _write(repo, "notes.txt", "ordinary notes, nothing private here\n")
        _commit(repo, "add notes")
        _git(repo, ["branch", "wbs/%s-migration" % lowered])
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms_real_short_ref", [short_term])])
        masked_present = ("<%d>" % len(short_term)) in out
        term_absent = short_term.lower() not in out.lower()
        self.assertTrue(code == 2 and masked_present and term_absent,
                        "exit=%d masked-present=%s term-absent-from-output=%s "
                        "(term is %d characters, output withheld)"
                        % (code, masked_present, term_absent, len(short_term)))

    def test_a_blob_path_carrying_the_short_term_is_masked_not_printed(self):
        # Same finding, the blob side: path_of[sha] is a real tree path, and this
        # repository's own history holds paths shaped like this. The content also
        # carries the term so a hit is actually generated (a path alone never
        # triggers one); the path must still not leak it into the HIT line.
        short_term = self._first_short_real_term()
        lowered = short_term.lower()
        repo = self._repo()
        relpath = "10-Projects/%s/note.md" % lowered
        _write(repo, relpath, "a memo mentioning %s in passing\n" % lowered)
        _commit(repo, "add notes")
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms_real_short_path", [short_term])])
        masked_present = ("<%d>" % len(short_term)) in out
        term_absent = short_term.lower() not in out.lower()
        self.assertTrue(code == 2 and masked_present and term_absent,
                        "exit=%d masked-present=%s term-absent-from-output=%s "
                        "(term is %d characters, output withheld)"
                        % (code, masked_present, term_absent, len(short_term)))

    def test_the_hit_line_withholds_the_term_from_stdout_and_stderr(self):
        # 2026-09-03: the scanner's own HIT line used to print the matched term
        # via %r, so a clean run of the scanner itself leaked the private term
        # into whatever terminal, transcript or battery log captured it. It now
        # prints a character count in its place; run() combines stdout and
        # stderr, so checking `out` covers both streams.
        short_term = self._first_short_real_term()
        lowered = short_term.lower()
        repo = self._repo()
        _write(repo, "notes.txt", "a memo mentioning %s in passing\n" % lowered)
        _commit(repo, "add notes")
        code, out = run(["--repo", repo, "--range", "HEAD",
                         "--terms", self._terms("terms_real_short_print", [short_term])])
        masked_present = ("a term of %d characters" % len(short_term)) in out
        term_absent = short_term.lower() not in out.lower()
        self.assertTrue(code == 2 and masked_present and term_absent,
                        "exit=%d masked-phrase-present=%s term-absent-from-output=%s "
                        "(term is %d characters, output withheld)"
                        % (code, masked_present, term_absent, len(short_term)))

    def test_a_range_argument_naming_a_branch_with_the_short_term_is_masked(self):
        # Third finding: rng reaches the "range: %s" header line (and the
        # zero-blobs NO-DATA line) unmasked. --range here is set to a branch
        # named after the term, so if range-masking were missing the raw term
        # would appear in the header line even with the HIT-line masking fine.
        short_term = self._first_short_real_term()
        lowered = short_term.lower()
        repo = self._repo()
        _write(repo, "notes.txt", "ordinary notes, nothing private here\n")
        _commit(repo, "add notes")
        branch = "wbs/%s-migration" % lowered
        _git(repo, ["branch", branch])
        code, out = run(["--repo", repo, "--range", branch,
                         "--terms", self._terms("terms_real_short_rangearg", [short_term])])
        masked_present = ("<%d>" % len(short_term)) in out
        term_absent = short_term.lower() not in out.lower()
        self.assertTrue(masked_present and term_absent,
                        "exit=%d masked-present=%s term-absent-from-output=%s "
                        "(term is %d characters, output withheld)"
                        % (code, masked_present, term_absent, len(short_term)))

    def test_a_nonexistent_repo_path_carrying_the_short_term_is_masked_in_the_error(self):
        # Fourth finding: --repo reaches the "not a git repository" ERROR line
        # unmasked. A repo path that does not exist at all still carries the
        # term in its own name, and must not leak it into stderr.
        short_term = self._first_short_real_term()
        lowered = short_term.lower()
        missing_repo = os.path.join(self.tmp_terms, "no-such-%s-repo" % lowered)
        code, out = run(["--repo", missing_repo, "--range", "HEAD",
                         "--terms", self._terms("terms_real_short_repopath", [short_term])])
        masked_present = ("<%d>" % len(short_term)) in out
        term_absent = short_term.lower() not in out.lower()
        self.assertTrue(code == 1 and masked_present and term_absent,
                        "exit=%d masked-present=%s term-absent-from-output=%s "
                        "(term is %d characters, output withheld)"
                        % (code, masked_present, term_absent, len(short_term)))


if __name__ == "__main__":
    unittest.main(verbosity=1)
