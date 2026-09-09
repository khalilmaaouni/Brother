#!/usr/bin/env python3
"""Tests for scripts/score_vault_retrieval.py and the fixture corpora it scores.

THE MUTATION IS THE POINT. test_mutation_scores_zero builds a corpus whose every
expected path names a file that does not exist, leaving query_sent untouched, and
asserts every metric is exactly 0.0. On its own that would be a tautology (a
scorer that returns zero for everything passes it), so the SAME test also runs
the real corpus in the SAME run and asserts recall@1 is strictly above 0.0.

THE CALL IS CHECKED, NOT ASSUMED. The scorer claims to make the point of need
hook's own call, so two tests hold it to that: one asserts every recorded argv
carries the --context the HOOK's _context_path derives (a scorer that quietly
reverted to the bare basename of 62f49ded fails there rather than reporting the
old number as the new one), and one drives the option probe both ways against
fake tools, because an older bm_vault.py must be scorable with the call it can
answer or no before and after comparison is possible.

Python 3.9, standard library only, no network. Whole suite runs in well under a
minute: three scorer passes over 40 queries, plus static checks.

No em or en dashes anywhere in this file.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCORER = os.path.join(HERE, "score_vault_retrieval.py")
BM_VAULT = os.path.join(REPO, "products", "brothermode", "tools", "bm_vault.py")
FIXTURE = os.path.join(REPO, "benchmarks", "fixtures", "vault-retrieval", "vault")
TUNING = os.path.join(REPO, "benchmarks", "retrieval", "vault-retrieval-v1.json")
HELDOUT = os.path.join(REPO, "benchmarks", "retrieval", "vault-retrieval-heldout-v1.json")

METRICS = ("recall@1", "recall@2", "recall@5", "MRR", "nDCG@5")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _corpus(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _score(corpus_path, out_path, k=5, limit=None):
    """Run the scorer as its own process, the way a builder runs it, and return the
    written result. Exit code is asserted here so a crash cannot read as a score."""
    cmd = [sys.executable, SCORER, "--vault", FIXTURE, "--corpus", corpus_path,
           "--k", str(k), "--out", out_path]
    if limit is not None:
        cmd += ["--limit", str(limit)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if proc.returncode != 0:
        raise AssertionError("scorer exited %d: %s"
                             % (proc.returncode, proc.stderr.decode("utf-8", "replace")))
    with open(out_path, encoding="utf-8") as fh:
        return json.load(fh), proc.stdout.decode("utf-8", "replace")


class TestScorer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="test-score-vault-retrieval-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_true_corpus_and_mutation_in_one_run(self):
        """(a) the real corpus scores above zero, and (b) a corpus whose expected
        paths all point nowhere scores exactly zero on every metric. Both in one
        run, because either assertion alone is passable by a broken scorer."""
        true_result, stdout = _score(TUNING, os.path.join(self.tmp, "true.json"))
        self.assertEqual(true_result["status"], "OK", stdout)
        self.assertEqual(true_result["aggregate"]["n"], 40)
        self.assertGreater(true_result["aggregate"]["recall@1"], 0.0,
                           "the unmutated corpus must score above zero, or the "
                           "mutation below proves nothing")

        doc = _corpus(TUNING)
        for i, q in enumerate(doc["queries"]):
            q["primary_path"] = "40-Failures/no-such-note-%d.md" % i
            q["expected_paths"] = ["40-Failures/no-such-note-%d.md" % i]
        mutant_path = os.path.join(self.tmp, "mutant-corpus.json")
        with open(mutant_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        mutant, _out = _score(mutant_path, os.path.join(self.tmp, "mutant.json"))

        self.assertEqual(mutant["status"], "OK")
        self.assertEqual(mutant["aggregate"]["n"], 40)
        for metric in METRICS:
            self.assertEqual(mutant["aggregate"][metric], 0.0,
                             "mutant %s was %r, expected 0.0"
                             % (metric, mutant["aggregate"][metric]))
        for band, agg in mutant["by_band"].items():
            for metric in METRICS:
                self.assertEqual(agg[metric], 0.0, "mutant band %s %s" % (band, metric))

    def test_short_corpus_is_no_data_with_null_aggregates(self):
        """(c) a population too small to measure writes nulls, never zeros, and
        still exits 0."""
        doc = _corpus(TUNING)
        doc["queries"] = doc["queries"][:10]
        short_path = os.path.join(self.tmp, "short-corpus.json")
        with open(short_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        result, stdout = _score(short_path, os.path.join(self.tmp, "short.json"))
        self.assertEqual(result["status"], "NO-DATA")
        self.assertIn("NO-DATA", stdout)
        self.assertEqual(result["per_query"], [])
        for metric in METRICS:
            self.assertIsNone(result["aggregate"][metric],
                              "NO-DATA %s must be null, was %r"
                              % (metric, result["aggregate"][metric]))

    def test_every_expected_note_exists_and_carries_its_anchor(self):
        """(d) the oracle holds: each expected note is a real file in the fixture
        and bm_vault.py's OWN anchor regex extracts the query string from it. The
        regex is imported from bm_vault.py, never re-implemented, because a second
        copy of it would be a second opinion about what the indexer sees."""
        anchor_re = _load(BM_VAULT, "bm_vault_under_test").ANCHOR
        checked = 0
        for corpus_path in (TUNING, HELDOUT):
            doc = _corpus(corpus_path)
            for q in doc["queries"]:
                paths = set(q["expected_paths"]) | {q["primary_path"]}
                self.assertIn(q["primary_path"], q["expected_paths"],
                              "%s primary is not in its own expected set" % q["qid"])
                for rel in sorted(paths):
                    full = os.path.join(FIXTURE, rel)
                    self.assertTrue(os.path.isfile(full),
                                    "%s names a missing note %s" % (q["qid"], rel))
                    with open(full, encoding="utf-8") as fh:
                        body = fh.read()
                    self.assertIn(q["query_sent"], set(anchor_re.findall(body)),
                                  "%s: %s does not carry %s as an extractable anchor"
                                  % (q["qid"], rel, q["query_sent"]))
                    checked += 1
                self.assertEqual(len(q["expected_paths"]), q["anchor_note_count"],
                                 "%s anchor_note_count disagrees with its own "
                                 "expected set" % q["qid"])
        self.assertGreater(checked, 60)

    def test_heldout_is_scorable_and_disjoint_from_tuning(self):
        """(e) the held-out set is scored once, at the close, against the final tree.
        Two things have to hold. It must be BIG ENOUGH TO SCORE: the scorer prints
        NO-DATA and writes null aggregates below MIN_QUERIES, so a held out set under
        that floor can never produce a number, whatever the retrieval does. And it
        must be DISJOINT from tuning on both keys that could leak it: a shared qid
        would let a tuning run read a held out row, and a shared primary_path would
        let tuning tune against a note the held out set is about to grade."""
        scorer = _load(SCORER, "score_vault_retrieval_floor")
        tuning_doc = _corpus(TUNING)["queries"]
        heldout_doc = _corpus(HELDOUT)["queries"]
        self.assertEqual(len(tuning_doc), 40)
        self.assertGreaterEqual(
            len(heldout_doc), scorer.MIN_QUERIES,
            "the held out corpus holds %d queries, below the scorer's floor of %d, so "
            "scoring it can only print NO-DATA" % (len(heldout_doc), scorer.MIN_QUERIES))
        self.assertGreaterEqual(len(heldout_doc), 30)

        tuning_qids = {q["qid"] for q in tuning_doc}
        heldout_qids = {q["qid"] for q in heldout_doc}
        self.assertEqual(len(tuning_qids), len(tuning_doc))
        self.assertEqual(len(heldout_qids), len(heldout_doc))
        self.assertEqual(tuning_qids & heldout_qids, set())

        tuning_primaries = {q["primary_path"] for q in tuning_doc}
        heldout_primaries = {q["primary_path"] for q in heldout_doc}
        self.assertEqual(tuning_primaries & heldout_primaries, set(),
                         "a note is the graded answer in both splits")

    def test_band_and_language_mix_is_what_the_design_asked_for(self):
        """The corpus is only a benchmark if it still exercises the failure shapes:
        the band mix, and at least one Japanese query in each split."""
        for path, quota in ((TUNING, {"rare": 15, "mid": 15, "crowded": 10}),
                            (HELDOUT, {"rare": 12, "mid": 10, "crowded": 8})):
            counts = {}
            langs = set()
            for q in _corpus(path)["queries"]:
                counts[q["band"]] = counts.get(q["band"], 0) + 1
                langs.add(q["lang"])
                if q["band"] == "rare":
                    self.assertLessEqual(q["anchor_note_count"], 2, q["qid"])
                elif q["band"] == "mid":
                    self.assertTrue(3 <= q["anchor_note_count"] <= 8, q["qid"])
                else:
                    self.assertGreaterEqual(q["anchor_note_count"], 9, q["qid"])
            self.assertEqual(counts, quota, path)
            self.assertIn("ja", langs, "%s carries no Japanese query" % path)

    def test_the_argv_is_the_call_the_point_of_need_hook_makes(self):
        """(f) every per-query argv carries --context with the value the HOOK'S OWN
        _context_path derives from that row's file_path, and --project with that
        row's own project whenever the tool under test lists the flag. A scorer that
        quietly went back to the basename-only call of 62f49ded fails here, instead
        of reporting the old number as the new one.

        THE EXPECTED ARGV IS BUILT FROM WHAT THE TOOL LISTS, never from a fixed list
        written here: --context must be there (a tool that stopped naming it is the
        pre-VR3 call and that is a failure), while --project is asserted present
        exactly when the tool names it, because an older bm_vault.py that lists
        neither is a valid arm of a before and after comparison and this suite must
        not be the thing that makes it unscorable."""
        scorer = _load(SCORER, "score_vault_retrieval_argv")
        derive, note = scorer.context_deriver(scorer.DEFAULT_HOOK)
        self.assertEqual(note, "", "the hook must import, or no context is sent at all")
        options = scorer.tool_options(BM_VAULT)
        self.assertIn("--context", options,
                      "the tool under test no longer lists --context, so the scorer "
                      "would silently score the pre-VR3 call")
        result, _out = _score(TUNING, os.path.join(self.tmp, "argv.json"), k=2, limit=2)
        rows = result["per_query"]
        self.assertEqual(len(rows), 40)
        self.assertEqual(result["tool_options"], list(options))
        by_qid = dict((q["qid"], q) for q in _corpus(TUNING)["queries"])
        for row in rows:
            query = by_qid[row["qid"]]
            want = derive(query["hook_tool_input"]["file_path"])
            self.assertEqual(row["context"], want, row["qid"])
            # Every fixture path is deliberately in no repository, so the hook's own
            # git-root route can name no project for it; the CORPUS ROW carries the
            # project the hook would resolve for a real checkout of that project, and
            # that is the value sent. The argv is asserted whole, so an added or
            # dropped flag fails.
            self.assertEqual(row["project"], query["project"], row["qid"])
            expected = ["check", "--paths", row["query_sent"], "--context", want]
            if "--project" in options and query["project"]:
                expected += ["--project", query["project"]]
            self.assertEqual(row["argv"], expected + ["--limit", "2"], row["qid"])

    def test_an_option_the_tool_does_not_list_is_never_sent(self):
        """(g) the options are read off the TOOL UNDER TEST. Driven both ways with
        fake tools, because bm_vault.py's argv parser accepts any unknown --flag
        silently, so a probe that merely sent the flag would pass on every tool ever
        written and this control would prove nothing."""
        scorer = _load(SCORER, "score_vault_retrieval_options")
        new_tool = os.path.join(self.tmp, "tool_new.py")
        old_tool = os.path.join(self.tmp, "tool_old.py")
        _write_text(new_tool,
                    "print('check --paths F [F ...] --context P --project N --limit N')\n")
        _write_text(old_tool, "print('check --paths F [F ...] --limit N')\n")
        self.assertEqual(scorer.tool_options(new_tool), ("--context", "--project"))
        self.assertEqual(scorer.tool_options(old_tool), ())
        self.assertEqual(
            scorer.check_argv(new_tool, "a.py", "p/q/a.py", "brother", 2,
                              scorer.tool_options(new_tool))[2:],
            ["check", "--paths", "a.py", "--context", "p/q/a.py",
             "--project", "brother", "--limit", "2"])
        self.assertEqual(
            scorer.check_argv(old_tool, "a.py", "p/q/a.py", "brother", 2,
                              scorer.tool_options(old_tool))[2:],
            ["check", "--paths", "a.py", "--limit", "2"])

    def test_a_crowded_query_is_identifiable_from_its_context(self):
        """(h) the crowded band is winnable AT ALL. For every crowded query in both
        splits, exactly one note carrying that anchor names the file under the
        directory the hook's context points at, and it is the primary. Without this
        a crowded query is a nine way coin toss and the band measures nothing,
        whatever a ranker does with it. The segments are compared with bm_vault.py's
        OWN _context_segments, never a second copy of it."""
        vault_mod = _load(BM_VAULT, "bm_vault_for_segments")
        scorer = _load(SCORER, "score_vault_retrieval_winnable")
        derive, note = scorer.context_deriver(scorer.DEFAULT_HOOK)
        self.assertEqual(note, "")
        crowded = 0
        for corpus_path in (TUNING, HELDOUT):
            for q in _corpus(corpus_path)["queries"]:
                if q["band"] != "crowded":
                    continue
                crowded += 1
                want = vault_mod._context_segments(
                    derive(q["hook_tool_input"]["file_path"]))
                self.assertTrue(want, "%s sends a context with no directory" % q["qid"])
                named = []
                for rel in q["expected_paths"]:
                    with open(os.path.join(FIXTURE, rel), encoding="utf-8") as fh:
                        body = fh.read()
                    if any(vault_mod._context_segments(a) == want
                           for a in set(vault_mod.ANCHOR.findall(body))):
                        named.append(rel)
                self.assertEqual(named, [q["primary_path"]],
                                 "%s: the notes naming %s are %s, and only the primary "
                                 "may" % (q["qid"], sorted(want), named))
        self.assertEqual(crowded, 18, "10 tuning plus 8 held out crowded queries")

    def test_parser_reads_served_and_withheld_blocks(self):
        """The rank parser is the one piece of this scorer that is not the tool's
        own output, so it gets its own check: a withheld block must never be
        counted as served, and a path containing a space must still parse."""
        scorer = _load(SCORER, "score_vault_retrieval_under_test")
        vault = "/tmp/Kay Vault"
        text = "\n".join([
            "RECORDED FAILURES in the files you are about to touch:",
            "",
            "  WITHHELD (stale)  a withheld note  [lesson, vault]",
            "    reason: no cited anchor resolves",
            "    /tmp/Kay Vault/40-Failures/withheld.md",
            scorer.WITHHELD_MARKER,
            "",
            "  a served note  [lesson, vault]",
            "    a description line",
            "    matched on: names a.py, path",
            "    /tmp/Kay Vault/40-Failures/served.md",
            "Vault: 7 more lesson(s) matched a.py and were not shown (limit 2)",
            "",
            "possible pattern match (content, not filename): /tmp/x.py",
            "  a content fallback note  [lesson, vault]",
            "    /tmp/Kay Vault/40-Failures/fallback.md",
        ])
        blocks, truncated = scorer.parse_hits(text, vault)
        self.assertEqual(len(blocks), 2, blocks)
        self.assertTrue(blocks[0]["withheld"])
        self.assertFalse(blocks[1]["withheld"])
        self.assertEqual(blocks[1]["path"], "/tmp/Kay Vault/40-Failures/served.md")
        self.assertEqual(blocks[1]["matched_on"], ["names a.py", "path"])
        self.assertEqual(truncated, 7)
        self.assertIsNone(scorer._rank_of(blocks, {"/tmp/Kay Vault/40-Failures/withheld.md"},
                                          served_only=True))
        self.assertEqual(scorer._rank_of(blocks, {"/tmp/Kay Vault/40-Failures/withheld.md"},
                                         served_only=False), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
