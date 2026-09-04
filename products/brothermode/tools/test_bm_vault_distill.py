#!/usr/bin/env python3
"""Tests for bm_vault_distill, on a small fixture vault (never the real one).

Isolation follows tools/test_bm_vault.py's own pattern exactly: HOME is pointed at a throwaway
tmp dir so bm_vault.py's hardcoded index path (~/.claude/bm_vault_index.sqlite3) resolves under
it, and BROTHERMODE_ROOT is pinned to an empty dir so the correction-rule store this repo
actually has never leaks into the fixture index.

Run: python3 tools/test_bm_vault_distill.py      (unittest output, exit 0 or 1)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

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

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault_distill.py")

# The pre-existing note: real frontmatter, a title that overlaps heavily with input item 1's
# title, and a slug (its filename) that MATCHES item 1's slug exactly -- both signals the tool
# checks, so the SKIP does not depend on recall's fuzzy ranking succeeding by luck.
EXISTING_NOTE = """---
type: failure
project: brother
status: standing
created: 2026-08-20
tags: [search-first]
verified-by: "a prior session, in a test fixture"
name: Search before write skips a silent duplicate
symptom: a note nearly identical to one already in the vault got written a second time
---

# Search before write skips a silent duplicate

Writing without searching first duplicates a lesson the vault already holds.

## Related
-
"""

INPUT_ITEMS = [
    {
        "slug": "search-before-write-skips-duplicates",
        "title": "Search before write skips a silent duplicate",
        "detail": "This is the distilled write path, and it must SKIP because a close match "
                  "already exists in the fixture vault.",
        "symptom": "a note nearly identical to one already in the vault got written a second "
                   "time",
    },
    {
        "slug": "root-cause-not-symptom-fix",
        "title": "Root cause, not symptom, is the fix",
        "detail": "A report names a symptom. Before editing, grep every caller of the touched "
                  "function and fix it once, where every caller routes through.",
        "symptom": "a guard was added at the one call site the ticket named, and a sibling "
                   "caller kept failing",
    },
    {
        "slug": "a-brake-is-not-a-wall",
        "title": "A brake is not a wall",
        "detail": "When the founder names a budget or tells a session to raise the ceiling, "
                  "that is an exception and it is honored in the same turn.",
        "symptom": "a spend guard refusal was treated as absolute even after the founder named "
                   "a budget",
    },
]


def run(argv, env):
    p = subprocess.run([sys.executable, TOOL] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


class Distill(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-distill-")
        cls.vault = os.path.join(cls.tmp, "vault")
        failures = os.path.join(cls.vault, "40-Failures")
        os.makedirs(failures)
        with open(os.path.join(failures, "search-before-write-skips-duplicates.md"), "w",
                  encoding="utf-8") as f:
            f.write(EXISTING_NOTE)

        cls.input_path = os.path.join(cls.tmp, "input.json")
        with open(cls.input_path, "w", encoding="utf-8") as f:
            json.dump(INPUT_ITEMS, f)

        cls.env = dict(os.environ)
        cls.env["HOME"] = cls.tmp                       # moves bm_vault.py's INDEX_PATH
        cls.env["BROTHERMODE_ROOT"] = os.path.join(cls.tmp, "empty-store-root")
        os.makedirs(os.path.join(cls.tmp, ".claude"))
        os.makedirs(cls.env["BROTHERMODE_ROOT"])

        cls.code, cls.out = run(["distill", "--vault", cls.vault, "--input", cls.input_path],
                                cls.env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_run_exits_zero(self):
        self.assertEqual(self.code, 0, "distill exited %d:\n%s" % (self.code, self.out))

    def test_close_match_is_skipped_not_overwritten(self):
        self.assertIn("SKIP search-before-write-skips-duplicates:", self.out,
                      "expected a SKIP line for the close-matching item:\n%s" % self.out)
        # The body must be untouched: still exactly the fixture content, no WROTE line for it.
        path = os.path.join(self.vault, "40-Failures",
                            "search-before-write-skips-duplicates.md")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), EXISTING_NOTE, "the append-only note body was rewritten")
        self.assertNotIn("WROTE search-before-write-skips-duplicates", self.out)

    def test_new_items_are_written_with_real_frontmatter(self):
        for slug, title, symptom in (
                ("root-cause-not-symptom-fix", "Root cause, not symptom, is the fix",
                 "a guard was added at the one call site the ticket named, and a sibling "
                 "caller kept failing"),
                ("a-brake-is-not-a-wall", "A brake is not a wall",
                 "a spend guard refusal was treated as absolute even after the founder named "
                 "a budget")):
            self.assertIn("WROTE %s" % slug, self.out,
                          "expected a WROTE line for %s:\n%s" % (slug, self.out))
            path = os.path.join(self.vault, "40-Failures", slug + ".md")
            self.assertTrue(os.path.isfile(path), "%s was not written" % path)
            with open(path, encoding="utf-8") as f:
                body = f.read()
            self.assertTrue(body.startswith("---\n"), "no frontmatter block in %s" % path)
            self.assertIn("type: failure\n", body)
            self.assertIn("project: all\n", body)
            self.assertIn("status: standing\n", body)
            self.assertIn("tags: []\n", body)
            self.assertIn('symptom: "%s"' % symptom, body)
            self.assertIn("# %s" % title, body)
            self.assertIn("## Related", body)

    def test_a_malformed_item_is_a_parse_error_not_a_write(self):
        bad_path = os.path.join(self.tmp, "bad-input.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            json.dump([{"slug": "missing-fields"}], f)
        code, out = run(["distill", "--vault", self.vault, "--input", bad_path], self.env)
        self.assertEqual(code, 2, "expected exit 2 on a missing required field:\n%s" % out)
        self.assertIn("missing required field", out)

    def test_an_unreadable_input_file_is_a_real_error(self):
        code, out = run(["distill", "--vault", self.vault,
                         "--input", os.path.join(self.tmp, "does-not-exist.json")], self.env)
        self.assertEqual(code, 2, "expected exit 2 on an unreadable input file:\n%s" % out)


def _note(title, created="2026-08-01", extra_front=""):
    return ("---\ntype: failure\nstatus: standing\ncreated: %s\n%s---\n\n# %s\n\nBody.\n"
           % (created, extra_front, title))


class ScanDuplicates(unittest.TestCase):
    """No HOME/BROTHERMODE_ROOT isolation needed: scan-duplicates never calls bm_vault.py
    or touches the recall index, pure filesystem and frontmatter regex, so a plain
    subprocess env is enough."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bm-vault-distill-scan-")
        cls.vault = os.path.join(cls.tmp, "vault")
        failures = os.path.join(cls.vault, "40-Failures")
        os.makedirs(failures)

        def write(fn, text):
            with open(os.path.join(failures, fn), "w", encoding="utf-8") as f:
                f.write(text)

        # A clear duplicate pair: same content words, different surface phrasing, the
        # exact shape found in the real vault's own first run of this scanner (a lock
        # orphaned by an ended session, worded two ways).
        write("orphaned-lock-a.md", _note("A lock orphaned by an ended session"))
        write("orphaned-lock-b.md", _note("An ended session orphaned the lock"))
        # Genuinely unrelated: zero content-word overlap with every other fixture note,
        # deliberately, so a coincidental shared word (the first version of this fixture
        # used "branch" here, which collided with stopword-a's own "branch" and made this
        # test fail for a real if narrow reason: two short titles need only one shared
        # content word to clear 0.5) can never happen again by accident.
        write("unrelated.md", _note("A timezone offset breaks the nightly export"))
        # A pair that shares only stopwords ("is", "not", "a", "the"): must NOT surface
        # at the default threshold, the exact false-positive class the real vault's
        # first run produced before the stopword filter.
        write("stopword-a.md", _note("A branch is not a delivery"))
        write("stopword-b.md", _note("A ceiling nothing computes is not a ceiling"))
        # An already-declared pair: real content overlap, but linked via supersedes:,
        # so it must be excluded even though its score alone would clear the threshold.
        write("declared-a.md", _note("A stale cache serves an old answer",
                                     extra_front="supersedes: [[declared-b]]\n"))
        write("declared-b.md", _note("A cache serves a stale old answer"))
        # Routing pages: must never appear as either side of a candidate pair.
        write("Failures-Index.md", _note("A stale cache serves an old answer"))
        write("Failures-by-Symptom.md", _note("A stale cache serves an old answer"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_real_duplicate_pair_is_found(self):
        code, out = run(["scan-duplicates", "--vault", self.vault, "--json"], dict(os.environ))
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        pairs = {frozenset((c["a"], c["b"])) for c in data["candidates"]}
        self.assertIn(frozenset(("orphaned-lock-a", "orphaned-lock-b")), pairs, out)

    def test_an_unrelated_note_is_never_paired_with_anything(self):
        code, out = run(["scan-duplicates", "--vault", self.vault, "--json"], dict(os.environ))
        data = json.loads(out)
        for c in data["candidates"]:
            self.assertNotIn("unrelated", (c["a"], c["b"]), out)

    def test_a_stopword_only_match_does_not_surface_at_the_default_threshold(self):
        code, out = run(["scan-duplicates", "--vault", self.vault, "--json"], dict(os.environ))
        data = json.loads(out)
        pairs = {frozenset((c["a"], c["b"])) for c in data["candidates"]}
        self.assertNotIn(frozenset(("stopword-a", "stopword-b")), pairs, out)

    def test_a_pair_already_linked_by_supersedes_is_excluded_even_above_threshold(self):
        code, out = run(["scan-duplicates", "--vault", self.vault, "--json"], dict(os.environ))
        data = json.loads(out)
        pairs = {frozenset((c["a"], c["b"])) for c in data["candidates"]}
        self.assertNotIn(frozenset(("declared-a", "declared-b")), pairs, out)

    def test_routing_pages_are_never_candidates(self):
        code, out = run(["scan-duplicates", "--vault", self.vault, "--json"], dict(os.environ))
        data = json.loads(out)
        for c in data["candidates"]:
            self.assertNotIn("Failures-Index", (c["a"], c["b"]), out)
            self.assertNotIn("Failures-by-Symptom", (c["a"], c["b"]), out)

    def test_nothing_is_ever_written_to_disk(self):
        before = sorted(os.listdir(os.path.join(self.vault, "40-Failures")))
        code, out = run(["scan-duplicates", "--vault", self.vault], dict(os.environ))
        after = sorted(os.listdir(os.path.join(self.vault, "40-Failures")))
        self.assertEqual(before, after, "scan-duplicates must never write a file:\n%s" % out)

    def test_no_failures_directory_is_no_data_not_a_crash(self):
        empty = tempfile.mkdtemp(prefix="bm-vault-distill-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        code, out = run(["scan-duplicates", "--vault", empty], dict(os.environ))
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out, out)


if __name__ == "__main__":
    unittest.main()
