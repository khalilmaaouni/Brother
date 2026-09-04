"""Calibration for scripts/cleanse.sh, the push gate's client term scan.

Drives cleanse.sh over a TEMPORARY tree with a FIXTURE terms file supplied
through BROTHER_PRIVATE_TERMS, never the real one, and never the real
repository. Fixture terms are invented and cannot appear by accident:
QZXW (four characters, takes the short/whole-word branch) and LONGVENDOR
(ten characters, takes the long/substring branch).

The point of this file is case (a) below: without the length-and-case
branch in cleanse.sh, a short fixture term embedded inside an ordinary
word would be reported, exactly the false-positive class that made the
real gate refuse over the English word "hurry" containing a real four
letter client term. If the length rule is ever removed, case (a) fails.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEANSE = os.path.join(REPO_ROOT, 'scripts', 'cleanse.sh')

SHORT_TERM = 'QZXW'
LONG_TERM = 'LONGVENDOR'


def run_cleanse(root, terms_path):
    """Run the copy of cleanse.sh living at <root>/scripts/cleanse.sh."""
    script = os.path.join(root, 'scripts', 'cleanse.sh')
    env = dict(os.environ)
    if terms_path is None:
        env.pop('BROTHER_PRIVATE_TERMS', None)
    else:
        env['BROTHER_PRIVATE_TERMS'] = terms_path
    proc = subprocess.run(
        ['sh', script], capture_output=True, text=True, cwd=root, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def make_tree(note_text):
    """A scratch tree: a copy of cleanse.sh plus one content file."""
    root = tempfile.mkdtemp()
    scripts_dir = os.path.join(root, 'scripts')
    os.makedirs(scripts_dir)
    shutil.copy(CLEANSE, os.path.join(scripts_dir, 'cleanse.sh'))
    with open(os.path.join(root, 'note.txt'), 'w') as f:
        f.write(note_text)
    return root


def make_tree_at(rel_path, note_text):
    """A scratch tree: a copy of cleanse.sh plus one content file at rel_path.

    Used for the D9 products/ exclusion tests below, where the seeded
    violation's LOCATION inside the tree is the thing under test, not just
    its content.
    """
    root = tempfile.mkdtemp()
    scripts_dir = os.path.join(root, 'scripts')
    os.makedirs(scripts_dir)
    shutil.copy(CLEANSE, os.path.join(scripts_dir, 'cleanse.sh'))
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w') as f:
        f.write(note_text)
    return root


def make_terms_file(lines):
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    return path


def git_run(args, cwd):
    subprocess.run(['git'] + args, cwd=cwd, capture_output=True, text=True,
                    timeout=30, check=True)


def make_history_tree(commit_texts, excluded_indices=(), rel_path='note.txt',
                       messages=None):
    """A scratch git repository: cleanse.sh copied in, rel_path committed
    once per entry in commit_texts, in order (rel_path defaults to note.txt
    at the root; the ATTRIBUTION_HISTORY_EXEMPT test points it at
    scripts/pre_push_gate.py to seed a commit under that exact path).
    messages, if given, supplies the commit message for each index in turn
    (default 'c<i>'), used by the commit-message-is-a-hit test to plant a
    needle in the MESSAGE rather than the file content. excluded_indices
    seeds docs/plan/IMPORTED-HISTORY-ROOTS.txt with the shas at those
    positions, so the D9 exclusion cleanse.sh already has (unchanged by
    this task) drops them and their ancestors from the history scans,
    exactly the way it drops a real imported commit. Mirrors
    test_portable_pack.py's git_init.
    """
    root = tempfile.mkdtemp()
    scripts_dir = os.path.join(root, 'scripts')
    os.makedirs(scripts_dir)
    shutil.copy(CLEANSE, os.path.join(scripts_dir, 'cleanse.sh'))
    for args in (['init', '-q'], ['config', 'user.email', 'a@b.c'],
                 ['config', 'user.name', 't']):
        git_run(args, root)
    note = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(note), exist_ok=True)
    shas = []
    for i, text in enumerate(commit_texts):
        with open(note, 'w') as f:
            f.write(text)
        git_run(['add', '-A'], root)
        msg = messages[i] if messages else 'c%d' % i
        git_run(['commit', '-q', '-m', msg], root)
        out = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root,
                              capture_output=True, text=True, check=True)
        shas.append(out.stdout.strip())
    if excluded_indices:
        docs_dir = os.path.join(root, 'docs', 'plan')
        os.makedirs(docs_dir, exist_ok=True)
        roots_file = os.path.join(docs_dir, 'IMPORTED-HISTORY-ROOTS.txt')
        with open(roots_file, 'w') as f:
            for idx in excluded_indices:
                f.write('%s fixture-root\n' % shas[idx])
    return root


class CleanseCalibration(unittest.TestCase):

    def setUp(self):
        self.terms_path = make_terms_file([SHORT_TERM, LONG_TERM])
        self.roots = []

    def tearDown(self):
        os.remove(self.terms_path)
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def tree(self, note_text):
        root = make_tree(note_text)
        self.roots.append(root)
        return root

    def tree_at(self, rel_path, note_text):
        root = make_tree_at(rel_path, note_text)
        self.roots.append(root)
        return root

    def git_tree(self, commit_texts, excluded_indices=(), rel_path='note.txt',
                 messages=None):
        root = make_history_tree(commit_texts, excluded_indices, rel_path,
                                  messages)
        self.roots.append(root)
        return root

    def test_a_short_term_inside_longer_word_not_reported(self):
        # This is the exact false-positive class the fix exists to close:
        # a short fixture term glued inside a longer ordinary word must
        # not fire. Removing the length branch from cleanse.sh makes this
        # assertion fail.
        root = self.tree('we ran into an aQZXWb situation today\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 0, msg=out + err)
        self.assertNotIn('FAIL:', out,
                          msg='a short term embedded inside a longer word must not '
                              'be reported; the length-and-case branch in cleanse.sh '
                              'is what stops this false positive')

    def test_b_short_term_whole_word_same_case_reported(self):
        root = self.tree('the vendor code is QZXW in this record\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-1', out)

    def test_c_short_term_whole_word_different_case_reported(self):
        # CORRECTED 2026-08-26 in cleanse.sh (a lowercase client name walked
        # straight through the case-sensitive gate); this test caught up on
        # 2026-08-30. Whole-word matching, not case, is what stops the
        # inside-a-word false positive, so a lowercase whole-word term hits.
        root = self.tree('the vendor code is qzxw in this record\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-1', out)

    def test_d_long_term_whole_word_different_case_reported(self):
        root = self.tree('the contract cites LongVendor as the party\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-2', out)

    def test_d2_long_term_inside_longer_word_not_reported(self):
        # CORRECTED 2026-08-30: substring matching for long terms refused this
        # gate on English prose (17 history hits for one real term, all inside
        # ordinary words, zero standalone). Embedded-in-a-word no longer hits.
        root = self.tree('the contract cites xxLongVendorxx as the party\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertNotIn('FAIL: NAME-2', out, msg=out + err)

    def test_d3_long_term_hyphen_compound_still_reported(self):
        # The stated trade of the whole-word correction: hyphens and spaces
        # are word boundaries, so a hyphenated compound must still hit.
        root = self.tree('shipped under the longvendor-app banner\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-2', out)

    def test_d4_underscore_adjacent_short_term_reported(self):
        # E37, 2026-09-03. grep -w counted the underscore as a word
        # character, so path_<term>_file walked through while the assurance
        # product's history test refused it. Driven both ways with test_a
        # (glued inside letters, still not a hit) and here (underscore on
        # both sides, a hit), for the short term in the working tree.
        root = self.tree('see path_qzxw_file for the export\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-1', out)

    def test_d5_underscore_adjacent_long_term_reported(self):
        # The same for the long term, underscore on one side only; test_d2
        # is the glued-inside-letters half for this term.
        root = self.tree('written to the longvendor_export dir\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-2', out)

    def test_d6_underscore_adjacent_term_in_history_reported(self):
        # The history pipeline uses its own matcher (term_grep_stream), so
        # it is driven separately: the term is added with underscores on
        # both sides in the first commit and gone from the tree by HEAD.
        root = self.git_tree([
            'see path_QZXW_file for the export\n',
            'the path was renamed entirely\n',
        ])
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-1', out)

    def test_e_missing_terms_file_is_no_data_not_pass(self):
        root = self.tree('nothing sensitive here\n')
        missing = os.path.join(tempfile.gettempdir(), 'no-such-private-terms.txt')
        self.assertFalse(os.path.exists(missing))
        code, out, err = run_cleanse(root, missing)
        self.assertEqual(code, 2, msg=out + err)
        self.assertIn('NO-DATA:', out)
        self.assertNotIn('PASS', out)

    def test_f_term_values_never_printed(self):
        cases = [
            'we ran into an aQZXWb situation today\n',
            'the vendor code is QZXW in this record\n',
            'the vendor code is qzxw in this record\n',
            'the contract cites xxLongVendorxx as the party\n',
        ]
        for note_text in cases:
            root = self.tree(note_text)
            code, out, err = run_cleanse(root, self.terms_path)
            combined = out + err
            self.assertNotIn(SHORT_TERM, combined,
                              msg='the term value must never appear in cleanse.sh output')
            self.assertNotIn(LONG_TERM, combined,
                              msg='the term value must never appear in cleanse.sh output')
            self.assertNotIn(LONG_TERM.lower(), combined,
                              msg='the term value must never appear in cleanse.sh output')

    def test_g_products_path_excluded_from_term_scan(self):
        # D9 extension, addendum recorded 2026-08-31 in
        # docs/decisions/2026-08-31-scanner-scope-after-subtree-imports.html.
        # products/ holds imported files that cannot be edited pre-M6, so the
        # term tree scan excludes a products/ path NOT on the allowlist, same
        # as the dash scan already did. RE-SEATED 2026-09-03: an auditor
        # found this narrowing fired even with NO allowlist file anywhere in
        # the tree, which is exactly THE BIG BUG (a missing allowlist must
        # scan everything, never nothing; see test_o). This behavior is now
        # gated behind an ALLOWLIST FILE actually present (narrow mode), so
        # this fixture seeds one that does not cover the seeded path.
        root = self.tree_at('products/imported-note.txt',
                             'the vendor code is QZXW in this record\n')
        os.makedirs(os.path.join(root, 'docs', 'plan'), exist_ok=True)
        with open(os.path.join(root, 'docs', 'plan',
                                'EXPORT-ALLOWLIST.txt'), 'w') as f:
            f.write('products/some-other-allowlisted-path\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 0, msg=out + err)
        self.assertNotIn('FAIL:', out)

    def test_h_root_path_same_content_still_refused(self):
        # Same content as test_g, but at the repo root rather than under
        # products/: the exclusion is scoped to the imported subtree, never
        # a blanket weakening of the term scan.
        root = self.tree_at('note.txt',
                             'the vendor code is QZXW in this record\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-1', out)

    def test_k_former_detector_path_no_longer_exempt(self):
        # scripts/pre_push_gate.py was one of the five paths named in
        # cleanse.sh's DETECTORS exemption, removed 2026-09-03; it is named
        # here only in this comment, never in cleanse.sh itself any more. A
        # term seeded at that same relative path used to be filtered out of
        # the working-tree scan by filename and must now be refused like any
        # other file. Driven backwards: this assertion fails against the
        # pre-2026-09-03 cleanse.sh, which exempted this exact path.
        root = self.tree_at('scripts/pre_push_gate.py',
                             'the vendor code is QZXW in this record\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-1', out)

    def test_l_history_hit_on_commit_that_adds_a_term(self):
        # Two commits: the first adds LONG_TERM to note.txt, the second
        # edits it away. Nothing here is excluded, so the add commit's own
        # diff (a "+" line carrying the term) is in scope and must still be
        # caught, even though the term is gone from the working tree by the
        # time cleanse.sh runs.
        root = self.git_tree([
            'the contract cites LONGVENDOR as the party\n',
            'the contract was renamed entirely\n',
        ])
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-2', out)

    def test_m_history_no_hit_on_commit_that_only_removes_a_term(self):
        # Same two commits, but the add commit is now excluded the way a
        # real imported commit is (docs/plan/IMPORTED-HISTORY-ROOTS.txt),
        # leaving only the second commit's diff in scope: a "-" line taking
        # the term OUT, no "+" line putting one in. That must not read as a
        # hit, or a privacy cleanup commit that only deletes a term would
        # refuse this gate on its own history forever.
        root = self.git_tree([
            'the contract cites LONGVENDOR as the party\n',
            'the contract was renamed entirely\n',
        ], excluded_indices=[0])
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 0, msg=out + err)
        self.assertNotIn('FAIL:', out)

    def test_n_attribution_history_exempt_scoped_to_its_one_path(self):
        # ATTRIBUTION_HISTORY_EXEMPT in cleanse.sh names scripts/pre_push_gate.py
        # after commit 4a79a7e8 added a literal attribution line there and
        # 3f1cfb2a reassembled it from fragments the same day; that historical
        # "+" line cannot be rewritten, so the attribution history scan alone
        # skips that one path. Fragment-assembled below for the reason
        # scripts/probe_attribution_patterns.sh already documents: a tracked
        # file carrying the literal trips the very scan under test. Driven
        # both ways with the same two-commit shape as test_l/test_m (add,
        # then edit away, so the working tree stays clean and only the
        # history scan is in play): not a hit under the exempt path, still a
        # hit under any other path.
        line = 'Co-' + 'Authored-' + 'By: Claude <noreply@' + 'anthropic' + '.com>\n'

        exempt_root = self.git_tree([line, 'clean now\n'],
                                     rel_path='scripts/pre_push_gate.py')
        code, out, err = run_cleanse(exempt_root, self.terms_path)
        self.assertEqual(code, 0, msg=out + err)
        self.assertNotIn('FAIL:', out)

        other_root = self.git_tree([line, 'clean now\n'], rel_path='other.py')
        code, out, err = run_cleanse(other_root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: attribution', out)

    def test_o_products_path_scanned_when_allowlist_absent(self):
        # THE BIG FIX, 2026-09-03. An auditor drove this backwards inside a
        # CANDIDATE EXPORT TREE, where docs/plan/EXPORT-ALLOWLIST.txt is
        # itself never exported: with no allowlist file anywhere (this
        # fixture's ordinary state, same as test_g used to rely on before
        # its own re-seating), a missing allowlist must mean scan every
        # file git sees, never scan none. Same fixture shape as test_g's
        # OLD, buggy expectation, opposite verdict: this refuses now, and
        # the mode line names which rule fired.
        root = self.tree_at('products/imported-note.txt',
                             'the vendor code is QZXW in this record\n')
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-1', out)
        self.assertIn('no docs/plan/EXPORT-ALLOWLIST.txt found', out)

    def test_p_history_message_text_is_a_hit_removed_diff_line_is_not(self):
        # CORRECTED 2026-09-03. git log -p indents the commit MESSAGE with
        # four literal spaces, never a "+", so the added-lines-only filter
        # (test_l/test_m's fix) silently dropped a term that appeared only
        # in a message, where the pre-fix pipeline (which kept every line
        # of the stream) still caught it. Driven both ways: a term said
        # only in a commit's own message, never in its file content, is a
        # hit; a term only on a line a commit REMOVES, with a message that
        # never says it either, is still not one.
        message_root = self.git_tree(
            ['clean content, never mentions it\n'],
            messages=['a tidy commit about LONGVENDOR, no file change '
                      'carries it'],
        )
        code, out, err = run_cleanse(message_root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-2', out)

        removal_root = self.git_tree(
            ['the contract cites LONGVENDOR as the party\n',
             'the contract was renamed entirely\n'],
            excluded_indices=[0],
            messages=['c0', 'an unrelated tidy-up, nothing named here'],
        )
        code, out, err = run_cleanse(removal_root, self.terms_path)
        self.assertEqual(code, 0, msg=out + err)
        self.assertNotIn('FAIL:', out)

    def test_q_empty_or_comment_only_term_list_is_no_data_not_pass(self):
        # An empty file, or one holding only a comment line, used to run
        # the term loop zero times, then print "checked 0 client term(s)"
        # and PASS: this file's own header rule (a control that opened
        # nothing about the one thing it checks is NO-DATA, never a pass)
        # applied to file scope but not to this loop. Two shapes, same
        # NO-DATA: a blank file, and one holding only a comment line.
        root = self.tree('nothing sensitive here\n')

        blank_terms = make_terms_file([])
        try:
            code, out, err = run_cleanse(root, blank_terms)
            self.assertEqual(code, 2, msg=out + err)
            self.assertIn('NO-DATA:', out)
            self.assertNotIn('PASS', out)
        finally:
            os.remove(blank_terms)

        comment_only_terms = make_terms_file(['# just a comment, no term'])
        try:
            code, out, err = run_cleanse(root, comment_only_terms)
            self.assertEqual(code, 2, msg=out + err)
            self.assertIn('NO-DATA:', out)
            self.assertNotIn('PASS', out)
        finally:
            os.remove(comment_only_terms)

    def test_r_comment_line_skipped_real_term_below_still_counted(self):
        # The same loop used to treat "# a comment" as a literal needle to
        # search for, never skipping it. It is now dropped like a blank
        # line, and a real term on a later line is still read and enforced
        # (and still the only one counted).
        root = self.tree('the vendor code is QZXW in this record\n')
        commented_terms = make_terms_file(['# a leading comment, not a term',
                                            SHORT_TERM])
        try:
            code, out, err = run_cleanse(root, commented_terms)
            self.assertEqual(code, 1, msg=out + err)
            self.assertIn('checked 1 client term(s)', out)
            self.assertIn('FAIL: NAME-1', out)
        finally:
            os.remove(commented_terms)


class CleansePathsWithASpaceAreStillOpened(unittest.TestCase):
    """E78, 2026-09-03: the term, attribution and dash scans all pipe
    SCAN_FILES (one path per line, spaces preserved) into `xargs` without
    -0. xargs without -0 splits on WHITESPACE as well as newlines, so a
    tracked path holding a space was handed to the scanner as two or more
    non-existent paths; the open failure went to /dev/null and the real
    file was never opened, with no visible sign anything was skipped.
    cleanse.sh now converts SCAN_FILES to NUL-delimited right before every
    xargs call and reads it with -0, so a path with a space is the one
    argument it is."""

    def setUp(self):
        self.terms_path = make_terms_file([SHORT_TERM, LONG_TERM])
        self.roots = []

    def tearDown(self):
        os.remove(self.terms_path)
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def test_a_dash_inside_a_filename_with_a_space_is_caught(self):
        # Assembled from a code point, never a literal em dash, so this
        # test file itself is never a hit for the very scan it drives.
        text = 'a note with an em dash %s in it\n' % chr(0x2014)
        root = make_tree_at('my file.md', text)
        self.roots.append(root)
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: em or en dash', out)

    def test_a_client_term_inside_a_filename_with_a_space_is_caught(self):
        root = make_tree_at('my file.md', 'the vendor code is QZXW here\n')
        self.roots.append(root)
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-1', out)


#: E34's real list, outside every repository. Read at run time only, never
#: copied into a literal here: this source file must not carry what it
#: exists to test the refusal of.
PRIVATE_NAMES_FILE = os.path.expanduser('~/.brothersbe-private-names')


def real_short_term():
    """The first term of five characters or fewer in the estate's real
    private-term list, in its own stored spelling. None when the list is
    absent or holds no term that short, which the caller must treat as
    NO-DATA and skip, never as nothing to test."""
    if not os.path.isfile(PRIVATE_NAMES_FILE):
        return None
    with open(PRIVATE_NAMES_FILE, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith('#') and len(line) <= 5:
                return line
    return None


class CleanseAgainstTheRealShortTerm(unittest.TestCase):
    """E34, calibrated against the ACTUAL production list rather than the
    invented QZXW/LONGVENDOR fixtures above: a lowercase spelling of a real
    short client term is exactly what walked through the case-sensitive
    gate and reached the public repository. Skips with a NO-DATA reason
    when the real list carries no term this short, which is not a pass."""

    def setUp(self):
        term = real_short_term()
        if term is None:
            self.skipTest('NO-DATA: no term of 5 characters or fewer in '
                           '~/.brothersbe-private-names')
        self._term = term
        self.terms_path = make_terms_file([term])
        self.roots = []

    def tearDown(self):
        os.remove(self.terms_path)
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def tree(self, note_text):
        root = make_tree(note_text)
        self.roots.append(root)
        return root

    def test_i_lowercase_spelling_in_content_is_refused(self):
        lower = self._term.lower()
        root = self.tree('the record mentions %s in passing\n' % lower)
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 1, msg=out + err)
        self.assertIn('FAIL: NAME-1', out)

    def test_j_ordinary_word_containing_the_letters_is_allowed(self):
        # The false-positive class the whole-word branch exists to avoid:
        # the term's letters glued inside a longer ordinary-looking token.
        word = 'h' + self._term.lower()
        root = self.tree('an ordinary %s appears in this note\n' % word)
        code, out, err = run_cleanse(root, self.terms_path)
        self.assertEqual(code, 0, msg=out + err)
        self.assertNotIn('FAIL:', out)


if __name__ == '__main__':
    unittest.main()
