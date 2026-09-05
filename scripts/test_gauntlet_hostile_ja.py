#!/usr/bin/env python3
"""test_gauntlet_hostile_ja: the gauntlet runner's COUNTING is right, proven
against a fake harness rather than against the real corpus.

WHY A FAKE HARNESS. gauntlet_hostile_ja.py scores whatever the shipped
harness returns. On this tree the real harness returns 78 of 78, so a test
that only ran the real thing would pass for as long as the corpus happens to
be perfect and would say nothing about the counting. These cases feed the
scorer the shapes the real harness can produce and pin what each one means:
all hits, all misses, a NO-DATA class excluded from n and never counted as a
pass, nothing scored at all, and a class the harness declares no floor for.

Mirrors benchmarks/ja-adversarial/test_adversarial_ja_result.py: a unittest
module, one class per claim, run directly.

Exit contract: 0 every assertion held, 1 an assertion failed.

No em or en dashes anywhere in this file.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER_PATH = os.path.join(HERE, "gauntlet_hostile_ja.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load("gauntlet_hostile_ja", RUNNER_PATH)

#: The shipped harness's own six classes and floors, copied here so the fake
#: harness returns the same shape the real one does.
FAKE_FLOORS = {
    "lexical_only": 0.90,
    "mixed": 0.70,
    "kana_alias": 0.70,
    "width_variant": 0.70,
    "dictionary_dependent": 0.90,
    "negative": 0.90,
}

#: Case counts of the frozen blind corpus, so n means something recognisable.
FAKE_SIZES = {
    "lexical_only": 14,
    "mixed": 13,
    "kana_alias": 15,
    "width_variant": 12,
    "dictionary_dependent": 11,
    "negative": 13,
}


def fake_harness(mode, empty_classes=()):
    """{class: (hits, total) or None}, the exact shape
    bm_vault_jbench.run_benchmark returns for per_class.

    mode "hit"  every case passes.
    mode "miss" every case fails.
    Classes named in empty_classes come back as None, which is what the
    shipped harness returns for a class with zero cases.
    """
    per_class = {}
    for cls, total in FAKE_SIZES.items():
        if cls in empty_classes:
            per_class[cls] = None
            continue
        per_class[cls] = (total if mode == "hit" else 0, total)
    return per_class


class TheCountingIsRight(unittest.TestCase):

    def test_all_hits_scores_every_case_and_passes(self):
        scored = G.score_classes(fake_harness("hit"), FAKE_FLOORS)
        self.assertEqual(scored["n"], 78)
        self.assertEqual(scored["hits"], 78)
        self.assertEqual(scored["misses"], 0)
        self.assertEqual(scored["below_floor"], [])
        self.assertEqual(scored["nodata"], [])
        self.assertEqual(G.exit_code(scored), 0)

    def test_all_misses_counts_every_case_and_fails(self):
        scored = G.score_classes(fake_harness("miss"), FAKE_FLOORS)
        self.assertEqual(scored["n"], 78)
        self.assertEqual(scored["hits"], 0)
        self.assertEqual(scored["misses"], 78)
        self.assertEqual(sorted(scored["below_floor"]), sorted(FAKE_FLOORS))
        self.assertEqual(G.exit_code(scored), 1)

    def test_a_nodata_class_is_excluded_from_n_and_is_not_a_pass(self):
        scored = G.score_classes(
            fake_harness("hit", empty_classes=("kana_alias",)), FAKE_FLOORS)
        self.assertEqual(scored["nodata"], ["kana_alias"])
        self.assertEqual(scored["n"], 78 - FAKE_SIZES["kana_alias"],
                          "the 15 cases of an empty class must not be "
                          "counted into n")
        self.assertEqual(scored["hits"], 63)
        row = next(r for r in scored["rows"] if r["class"] == "kana_alias")
        self.assertEqual(row["verdict"], "NO-DATA")
        self.assertIsNone(row["hits"])
        self.assertNotIn("kana_alias",
                          [r["class"] for r in scored["rows"]
                           if r["verdict"] == "OK"],
                          "a NO-DATA class must never read as a pass")

    def test_a_zero_case_tuple_is_nodata_too(self):
        """The harness reports an empty class as None, but a (0, 0) tuple is
        the same absence and must not divide by zero or read as a miss."""
        per_class = fake_harness("hit")
        per_class["negative"] = (0, 0)
        scored = G.score_classes(per_class, FAKE_FLOORS)
        self.assertEqual(scored["nodata"], ["negative"])
        self.assertEqual(scored["n"], 78 - FAKE_SIZES["negative"])

    def test_nothing_scored_at_all_exits_three(self):
        scored = G.score_classes(
            fake_harness("hit", empty_classes=tuple(FAKE_SIZES)), FAKE_FLOORS)
        self.assertEqual(scored["n"], 0)
        self.assertEqual(G.exit_code(scored), 3)

    def test_one_class_under_its_own_floor_fails_the_run(self):
        per_class = fake_harness("hit")
        per_class["negative"] = (11, 13)  # 85 percent, floor 90
        scored = G.score_classes(per_class, FAKE_FLOORS)
        self.assertEqual(scored["below_floor"], ["negative"])
        self.assertEqual(G.exit_code(scored), 1)
        self.assertEqual(G.CRITICAL_CLASS, "negative")

    def test_a_class_at_exactly_its_floor_is_ok(self):
        per_class = fake_harness("hit")
        per_class["mixed"] = (10, 13)  # 76.9 percent, floor 70
        scored = G.score_classes(per_class, FAKE_FLOORS)
        self.assertEqual(scored["below_floor"], [])
        self.assertEqual(G.exit_code(scored), 0)

    def test_an_undeclared_class_is_not_a_silent_pass(self):
        per_class = fake_harness("hit")
        per_class["brand_new_class"] = (4, 4)
        scored = G.score_classes(per_class, FAKE_FLOORS)
        self.assertIn("brand_new_class", scored["below_floor"])
        self.assertEqual(G.exit_code(scored), 1)


class TheBarsReportWhatTheyActuallyMeasured(unittest.TestCase):

    def test_the_blind_and_negative_bars_read_the_same_run(self):
        scored = G.score_classes(fake_harness("hit"), FAKE_FLOORS)
        self.assertEqual(G.bar_blind_corpus(scored)["verdict"], "MET")
        self.assertEqual(G.bar_negative_class(scored)["verdict"], "MET")
        scored = G.score_classes(fake_harness("miss"), FAKE_FLOORS)
        self.assertEqual(G.bar_blind_corpus(scored)["verdict"], "NOT MET")
        self.assertEqual(G.bar_negative_class(scored)["verdict"], "NOT MET")

    def test_an_unscored_run_reads_nodata_not_met(self):
        scored = G.score_classes(
            fake_harness("hit", empty_classes=tuple(FAKE_SIZES)), FAKE_FLOORS)
        self.assertEqual(G.bar_blind_corpus(scored)["verdict"], "NO-DATA")
        self.assertEqual(G.bar_negative_class(scored)["verdict"], "NO-DATA")

    def test_the_fresh_qualification_bar_is_nodata_from_the_spec(self):
        """The spec says NO INSTRUMENT YET for this measure, and the rubric
        forbids inferring it from the frozen corpus score."""
        spec = {"measures_outside_the_fifteen": [
            {"measure": "fresh unseen qualification",
             "instrument": "NO INSTRUMENT YET",
             "would_require": "a second corpus authored blind"}]}
        bar = G.bar_fresh_qualification(spec)
        self.assertEqual(bar["verdict"], "NO-DATA")
        self.assertIn("blind", bar["detail"])

    def test_a_missing_mutation_harness_reads_nodata_naming_the_file(self):
        original = G.MUTATION_PATH
        G.MUTATION_PATH = os.path.join(G.ROOT, "scripts", "no_such_file.py")
        try:
            bar = G.bar_mutation_sensitivity()
        finally:
            G.MUTATION_PATH = original
        self.assertEqual(bar["verdict"], "NO-DATA")
        self.assertIn("no_such_file.py", bar["detail"])


class TheFrozenCorpusGuardRefusesAMovedSpec(unittest.TestCase):
    """gauntlet_frozen.check() runs on the spec before the harness is ever
    called. Driven against a temp copy of the real spec (never the tree's
    own file): unmutated scores for real (the harness is the real, shipped
    one, proving the guard's own wiring rather than a stand-in), mutated
    refuses before a single case is scored."""

    def setUp(self):
        fd, self.spec_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        shutil.copyfile(G.SPEC_PATH, self.spec_path)
        self._real_spec_path = G.SPEC_PATH
        G.SPEC_PATH = self.spec_path

    def tearDown(self):
        G.SPEC_PATH = self._real_spec_path
        os.unlink(self.spec_path)

    def test_unmutated_copy_scores_for_real(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = G.main(["--no-record", "--quiet"])
        printed = out.getvalue()
        self.assertIn("frozen: OK", printed)
        self.assertNotIn("REFUSED", printed)
        self.assertIn(code, (0, 1), "a real scoring run must complete")

    def test_a_mutated_corpus_file_is_refused_before_the_harness_runs(self):
        """hostile-japanese-identity.json freezes a REAL corpus file (not
        the "none: ..." seed-hash kind), so the moved-hash case has to
        mutate that file, never the spec text, mirroring
        TestRealCorpusFile in test_gauntlet_frozen.py."""
        corpus_rel = os.path.join("benchmarks", "ja-adversarial",
                                  "adversarial-ja-corpus.json")
        fake_root = tempfile.mkdtemp()
        fake_corpus = os.path.join(fake_root, corpus_rel)
        os.makedirs(os.path.dirname(fake_corpus), exist_ok=True)
        shutil.copyfile(os.path.join(G.ROOT, corpus_rel), fake_corpus)
        with open(fake_corpus, "r+b") as fh:
            first = fh.read(1)
            fh.seek(0)
            fh.write(bytes([first[0] ^ 0xFF]))

        def _must_not_run(*_a, **_k):
            raise AssertionError(
                "real_harness must not run when the guard refuses")
        original_harness = G.real_harness
        original_gf_root = G.gauntlet_frozen.ROOT
        G.real_harness = _must_not_run
        G.gauntlet_frozen.ROOT = fake_root
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = G.main(["--no-record", "--quiet"])
        finally:
            G.real_harness = original_harness
            G.gauntlet_frozen.ROOT = original_gf_root
            shutil.rmtree(fake_root, ignore_errors=True)
        self.assertIn("REFUSED: corpus hash moved", out.getvalue())
        self.assertNotIn("frozen: OK", out.getvalue())
        self.assertEqual(code, 1)


class TheSummaryLineCarriesItsProvenance(unittest.TestCase):

    def test_the_summary_names_the_tree_and_the_corpus_and_the_verdict(self):
        scored = G.score_classes(fake_harness("hit"), FAKE_FLOORS)
        prov = {"tree_revision": "0123456789abcdef", "tree_dirty": False,
                "corpus_sha1": "f3920b31b83f", "corpus_commit": "df64b628"}
        bars = [{"verdict": "MET", "bar": "b1"}, {"verdict": "NO-DATA",
                                                   "bar": "b2"}]
        line = G.summary_line(scored, bars, prov, 0)
        self.assertEqual(len(line.splitlines()), 1)
        for token in ("n=78", "01234567", "f3920b31b83f", "df64b628",
                      "PASS", "1 of 2 MET", "1 NO-DATA"):
            self.assertIn(token, line)

    def test_a_failing_run_says_fail_and_an_unscored_run_says_nodata(self):
        scored = G.score_classes(fake_harness("miss"), FAKE_FLOORS)
        prov = {"tree_revision": None, "tree_dirty": True,
                "corpus_sha1": None, "corpus_commit": None}
        self.assertIn("-> FAIL", G.summary_line(scored, [], prov, 1))
        self.assertIn("UNKNOWN", G.summary_line(scored, [], prov, 1))
        empty = G.score_classes(
            fake_harness("hit", empty_classes=tuple(FAKE_SIZES)), FAKE_FLOORS)
        self.assertIn("-> NO-DATA", G.summary_line(empty, [], prov, 3))


if __name__ == "__main__":
    unittest.main(verbosity=2)
