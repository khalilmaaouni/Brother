"""Row LL-6: scripts/gauntlet_memoryagentbench_conflict.py, driven by the REAL
bm_vault.py mechanism against a small INVENTED three item fixture.

WHY THE REAL MECHANISM, unlike the fake recall test_gauntlet_memory_recurrence.py
uses for its own counting. That suite tests arithmetic over a fixed observation;
this gauntlet's whole job is to prove what bm_vault.py's real search and
_superseded_by actually do with a conflict, so faking the observation would test
nothing about the product. build_fixture_vault and run_benchmark are called
exactly as the real fixture uses them; only the three items are invented, never
the real vault, never a network call.

THE THREE ITEMS, one per outcome this scorer can produce for a single item:

  item 1, "mascot": a normal declared conflict (the new note's frontmatter
  carries supersedes: [[mascot-old]], exactly like the real fixture). Both
  notes share every content word in the question, so the older note is a real
  retrieval candidate; bm_vault.py's own _superseded_by then withholds it.
  Expected: conflict_resolution correct, selective_forgetting correct.

  item 2, "hq": no_supersession=True, so nothing in the vault says which fact
  is current, the exact gap bm_vault_contradiction.py's own module docstring
  names ("nothing decides which one an engineering session should act on").
  The older note repeats the query's content words three times, the newer
  note once, so the older note's own text match score dominates and it is
  served first with nothing to withhold it. Expected: conflict_resolution
  wrong (the stale fact was served), selective_forgetting wrong (the same
  stale note was served with no withholding to catch it): one mechanism gap
  fails both readings at once, which is the correct combined reading, not two
  independent coincidences.

  item 3, "unrelated-old": no_supersession=True AND the superseded note's own
  text shares no content word with the query at all (a different entity, a
  different relation). Both conditions matter: bm_vault.py's own _search
  pulls in a note LINKED from a match ("linked from a match", proven by
  reading its own `why` output while writing this test) even with zero
  lexical overlap, so a declared supersedes: edge alone would still make the
  older note a retrieval candidate through the link, not through wording.
  Dropping the declared edge here removes that link, leaving only the
  lexical channel, which the unrelated wording then genuinely fails. So this
  item is never a candidate for either reason, which is the state this
  script's selective_forgetting NO-DATA branch exists for.
  Expected: conflict_resolution correct (the current note is served), and
  selective_forgetting NO-DATA (nothing was there to withhold, so the
  mechanism was never exercised for this item; NO-DATA is never scored as a
  pass, per the estate's own rule, and summarize() must exclude it from both
  the count and the denominator).

Any file this suite writes goes under tempfile.mkdtemp(), never the real tree,
per this row's own rule and the pattern test_gauntlet_memory_recurrence.py
already sets for this family of suites.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gauntlet_memoryagentbench_conflict as G  # noqa: E402


def three_item_fixture():
    return {
        "_provenance": {
            "row_used": "INVENTED (this test's own fixture, not the paper's data)",
            "total_questions_in_row": 3,
            "no_data_count": 0,
            "mapped_count": 3,
        },
        "items": [
            {
                "id": "mascot",
                "question": "What is the mascot of Acme Corp?",
                "latest_value": "falcon",
                "latest_position": 2,
                "latest_fact_text": "The mascot of Acme Corp is a falcon.",
                "superseded_value": "wolf",
                "superseded_position": 1,
                "superseded_fact_text": "The mascot of Acme Corp is a wolf.",
            },
            {
                "id": "hq",
                "question": "Where is the headquarters of Globex located?",
                "latest_value": "Seattle",
                "latest_position": 2,
                "latest_fact_text": "The headquarters of Globex is located in Seattle.",
                "superseded_value": "Denver",
                "superseded_position": 1,
                "superseded_fact_text": ("The headquarters of Globex is located in Denver. "
                                          "The Globex headquarters location is Denver, and "
                                          "every Globex headquarters filing names Denver."),
                "no_supersession": True,
            },
            {
                "id": "unrelated-old",
                "question": "Who is the treasurer of the Riverside garden club?",
                "latest_value": "Priya Nair",
                "latest_position": 2,
                "latest_fact_text": "The treasurer of the Riverside garden club is Priya Nair.",
                "superseded_value": "n/a",
                "superseded_position": 1,
                "superseded_fact_text": "A comet was observed over the harbor on a clear night.",
                "no_supersession": True,
            },
        ],
    }


class RealMechanismScoresTheThreeOutcomes(unittest.TestCase):
    def setUp(self):
        self.bm = G._load_bm_vault()
        self.fixture = three_item_fixture()
        self.rows = {r["id"]: r for r in G.run_benchmark(self.bm, self.fixture)}

    def test_declared_conflict_withheld_scores_correct_on_both(self):
        row = self.rows["mascot"]
        self.assertEqual(row["served_note"], "current")
        self.assertEqual(row["conflict_resolution"], "correct")
        self.assertTrue(row["old_was_retrieval_candidate"])
        self.assertEqual(row["selective_forgetting"], "correct")

    def test_undeclared_conflict_with_stale_dominance_scores_wrong_on_both(self):
        row = self.rows["hq"]
        self.assertEqual(row["served_note"], "superseded")
        self.assertEqual(row["conflict_resolution"], "wrong")
        self.assertTrue(row["old_was_retrieval_candidate"])
        self.assertEqual(row["selective_forgetting"], "wrong")

    def test_superseded_note_never_a_candidate_reads_no_data(self):
        row = self.rows["unrelated-old"]
        self.assertEqual(row["served_note"], "current")
        self.assertEqual(row["conflict_resolution"], "correct")
        self.assertFalse(row["old_was_retrieval_candidate"])
        self.assertEqual(row["selective_forgetting"], G.NODATA)

    def test_summarize_excludes_no_data_from_the_forgetting_denominator(self):
        rows = list(self.rows.values())
        summary = G.summarize(rows)
        self.assertEqual(summary["conflict_resolution"], {"correct": 2, "total": 3})
        self.assertEqual(summary["selective_forgetting"],
                         {"correct": 1, "total": 2, "no_data": 1})


class LoadFixtureReadsATempFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gauntlet-memoryagentbench-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_written_fixture_round_trips(self):
        path = os.path.join(self.tmp, "fixture.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(three_item_fixture(), fh)
        fixture, err = G.load_fixture(path)
        self.assertIsNone(err)
        self.assertEqual(len(fixture["items"]), 3)

    def test_a_missing_file_reads_no_data_never_raises(self):
        fixture, err = G.load_fixture(os.path.join(self.tmp, "does-not-exist.json"))
        self.assertIsNone(fixture)
        self.assertTrue(err.startswith(G.NODATA))

    def test_a_fixture_with_no_items_reads_no_data(self):
        path = os.path.join(self.tmp, "empty.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"items": []}, fh)
        fixture, err = G.load_fixture(path)
        self.assertIsNone(fixture)
        self.assertTrue(err.startswith(G.NODATA))


if __name__ == "__main__":
    unittest.main()
