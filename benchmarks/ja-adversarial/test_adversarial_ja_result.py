#!/usr/bin/env python3
"""test_adversarial_ja_result: the result document's declared numbers are
what a fresh run of the shipped harness actually scores, on the FROZEN
adversarial corpus.

E96, 2026-09-04. ADVERSARIAL-JA-RESULT-2026-08-31.md declared negative 1/13
while the shipped harness (products/brothermode/tools/bm_vault_jbench.py,
which VB2-08 fixed after the document was written) scores 12/13 on the
identical corpus, sha1 f3920b31b83f. This test drives that gap red on the
old wording and green on the regenerated document: it runs the harness
in-process, parses the document's own per-class table, and asserts they
agree, per class and overall.

Exit contract: 0 every assertion held, 1 an assertion failed.
"""
import hashlib
import importlib.util
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
JBENCH_TOOLS = os.path.join(ROOT, "products", "brothermode", "tools")
JBENCH_PATH = os.path.join(JBENCH_TOOLS, "bm_vault_jbench.py")
CORPUS_PATH = os.path.join(HERE, "adversarial-ja-corpus.json")
RESULT_DOC = os.path.join(HERE, "ADVERSARIAL-JA-RESULT-2026-08-31.md")

#: The corpus is committed as permanent, frozen regression evidence; a run
#: against a corpus that quietly changed would not be a run against what the
#: document actually names. This is the same sha1[:12] named in the row and
#: in the document, computed the same way `git hash-object` is not (a plain
#: content hash, not git's blob-header form).
FROZEN_CORPUS_SHA1 = "f3920b31b83f"

#: One row of the document's markdown table:
#: | negative | 12/13 (92%) | 90% | OK |
TABLE_ROW_RE = re.compile(
    r"^\|\s*([a-z_]+)\s*\|\s*(\d+)/(\d+)\s*\(\d+%\)\s*\|")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def corpus_sha1():
    with open(CORPUS_PATH, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()[:12]


def run_harness():
    """{class: (hits, total)} plus "overall", from an in-process run of the
    shipped harness against the frozen corpus. Same call cmd_run() makes."""
    jb = _load("bm_vault_jbench", JBENCH_PATH)
    bm = jb._load_bm_vault()
    fixture, err = jb.load_fixture(CORPUS_PATH)
    if err:
        raise AssertionError("harness could not load the corpus: %s" % err)
    per_class, overall, _detail = jb.run_benchmark(bm, fixture)
    scores = {cls: result for cls, result in per_class.items()
              if result is not None}
    scores["overall"] = overall
    return scores


def result_doc_text():
    with open(RESULT_DOC, encoding="utf-8") as fh:
        return fh.read()


def declared_table(text, heading):
    """{class or "overall": (hits, total)} parsed from the first markdown
    table found after `heading` in `text`."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError("no %r heading in the result document" % heading)
    scores = {}
    for line in lines[start + 1:]:
        if line.startswith("##"):
            break
        m = TABLE_ROW_RE.match(line)
        if m:
            scores[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    return scores


class TheDocumentAgreesWithTheHarnessItClaimsToSummarize(unittest.TestCase):

    def test_the_corpus_is_still_the_frozen_one_the_document_names(self):
        self.assertEqual(
            corpus_sha1(), FROZEN_CORPUS_SHA1,
            "adversarial-ja-corpus.json no longer hashes to the frozen "
            "sha1 the row and the document both name; a changed corpus "
            "invalidates every number below.")

    def test_the_declared_per_class_table_matches_a_fresh_harness_run(self):
        harness = run_harness()
        declared = declared_table(
            result_doc_text(), "## Per-class, blind corpus (2026-09-04 run)")
        self.assertTrue(declared, "found no per-class table under the "
                         "2026-09-04 heading")
        mismatches = []
        for cls, (h_hits, h_total) in sorted(harness.items()):
            if cls not in declared:
                mismatches.append("%s: harness has %d/%d, document names "
                                   "no row" % (cls, h_hits, h_total))
                continue
            d_hits, d_total = declared[cls]
            if (d_hits, d_total) != (h_hits, h_total):
                mismatches.append(
                    "%s: document says %d/%d, harness scores %d/%d"
                    % (cls, d_hits, d_total, h_hits, h_total))
        self.assertFalse(
            mismatches,
            "ADVERSARIAL-JA-RESULT-2026-08-31.md's declared numbers do not "
            "match a fresh run of the shipped harness:\n" + "\n".join(mismatches))

    def test_the_old_2026_08_31_numbers_would_be_refused(self):
        """The positive control: the exact table the document originally
        shipped (negative 1/13, overall 64/78) is not what the harness
        scores today, so this class really discriminates rather than
        passing on any document."""
        harness = run_harness()
        self.assertNotEqual(harness["negative"], (1, 13))
        self.assertNotEqual(harness["overall"], (64, 78))
        stale_table_text = (
            "| negative | 1/13 (8%) | 90% | BELOW FLOOR |\n"
            "| overall | 64/78 (82%) | | |\n")
        declared = declared_table(
            "## heading\n" + stale_table_text, "## heading")
        mismatches = [
            cls for cls in declared
            if declared[cls] != harness.get(cls)]
        self.assertTrue(
            mismatches,
            "the old 2026-08-31 table would incorrectly be accepted "
            "against today's harness output")


if __name__ == "__main__":
    unittest.main(verbosity=2)
