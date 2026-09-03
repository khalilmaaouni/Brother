"""What the pattern store must keep true.

The gap it closes is measurable: 186 vault notes typed failure, zero typed as a
pattern that worked. The risk in closing it is writing a second archive nobody
can search, so the tests that matter here are the FINDING ones.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pattern_note as P  # noqa: E402


def vault():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, P.FOLDER))
    return d


def add(v, name, solves, what="do the thing", ev="it worked once"):
    return P.write(name, solves, what, ev, vault=v)


class FindableByTheProblemNotTheTitle(unittest.TestCase):
    """Nobody searching for help types the name of a technique they have never
    heard of. They type the trouble they are in."""

    def setUp(self):
        self.v = vault()
        add(self.v, "Drive every control backwards",
            "My check passes but I do not know whether it can ever fail")
        add(self.v, "Judge a branch against unchanged main",
            "The suite is red and I cannot tell whether my branch caused it")

    def test_a_problem_query_sharing_no_word_with_the_title_still_finds_it(self):
        hits = P.find("suite is red cannot tell whether my branch caused it", self.v)
        self.assertTrue(hits)
        self.assertIn("unchanged-main", hits[0][1])

    def test_the_better_match_ranks_first(self):
        hits = P.find("my check passes but can it ever fail", self.v)
        self.assertIn("backwards", hits[0][1])

    def test_the_solves_line_comes_back_with_the_hit(self):
        """So a searcher can tell from the result list whether it is their
        problem, without opening anything."""
        self.assertIn("check passes", P.find("check passes fail", self.v)[0][2])

    def test_an_unmatched_problem_is_NO_DATA_rather_than_silence(self):
        self.assertEqual(P.find("quantum tunnelling in a kettle", self.v), [])

    def test_a_query_of_only_short_words_returns_nothing_rather_than_everything(self):
        """Otherwise 'is it a' matches every note and the tool is noise."""
        self.assertEqual(P.find("is it a", self.v), [])

    def test_a_missing_vault_is_None_not_an_empty_result(self):
        """Empty means nothing matched, which is knowable. None means nothing
        was searched, which is not."""
        self.assertIsNone(P.find("anything", "/no/such/vault"))


class ItObeysTheVaultConstitution(unittest.TestCase):
    def test_a_second_write_of_the_same_name_changes_nothing(self):
        v = vault()
        path, first = add(v, "A pattern", "some problem")
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
        path2, second = P.write("A pattern", "DIFFERENT problem", "x", "y", vault=v)
        with open(path2, encoding="utf-8") as fh:
            after = fh.read()
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(before, after)

    def test_it_writes_into_the_existing_folder_rather_than_a_new_one(self):
        """Adding a type is not a reorganisation. Adding a folder is."""
        v = vault()
        path, _ = add(v, "A pattern", "some problem")
        self.assertEqual(os.path.basename(os.path.dirname(path)), P.FOLDER)

    def test_a_missing_vault_writes_nothing_and_says_so(self):
        path, written = P.write("n", "s", "w", "e", vault="/no/such/vault")
        self.assertIsNone(path)
        self.assertFalse(written)

    def test_the_index_gains_one_routing_line_per_pattern(self):
        v = vault()
        add(v, "One", "problem one")
        add(v, "Two", "problem two")
        with open(os.path.join(v, P.FOLDER, P.INDEX), encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count("\n- [["), 2)
        self.assertIn("problem one", text)

    def test_the_index_does_not_duplicate_a_line(self):
        v = vault()
        add(v, "One", "problem one")
        P.write("One", "problem one", "w", "e", vault=v)
        with open(os.path.join(v, P.FOLDER, P.INDEX), encoding="utf-8") as fh:
            self.assertEqual(fh.read().count("\n- [["), 1)


class TheNoteCarriesWhatMakesItUsable(unittest.TestCase):
    def test_it_is_typed_as_a_pattern_so_it_is_distinguishable(self):
        body = P.note_body("n", "s", "w", "e", "all")
        self.assertIn("type: pattern", body)

    def test_it_carries_the_problem_in_its_frontmatter(self):
        """The whole finding mechanism depends on this field existing."""
        self.assertIn("solves: the problem", P.note_body("n", "the problem", "w", "e", "all"))

    def test_it_carries_how_it_is_known_to_work(self):
        self.assertIn("How it is known to work", P.note_body("n", "s", "w", "e", "all"))

    def test_a_pattern_without_evidence_is_still_forced_to_have_the_field(self):
        """An unevidenced pattern is folklore. The section must exist so its
        emptiness is visible rather than absent."""
        self.assertIn("verified-by:", P.note_body("n", "s", "w", "", "all"))


class ItRoutesThroughTheSameGateAdmitUses(unittest.TestCase):
    """V4: a pattern note must go through bm_vault_intake.hard_gate, the same
    gate `admit` and `capture` run, never a direct ungated write."""

    def test_a_credential_shaped_pattern_is_refused_and_not_written(self):
        v = vault()
        path, written = P.write(
            "Leaky pattern", "some problem", "do the thing",
            "AKIA" + "ABCDEFGHIJKLMNOP" + " was live in the log", vault=v)
        self.assertIsNone(path)
        self.assertFalse(written)
        self.assertEqual(os.listdir(os.path.join(v, P.FOLDER)), [])

    def test_an_injected_gate_refusal_writes_nothing_driven_backwards(self):
        """Drive the gate seam backwards: a gate that refuses for any reason
        must stop the write, proven without depending on a real credential
        shape or the real bm_vault_intake module."""
        v = vault()

        def refuse(text, deny_list_path):
            return False, "class=deny-list-term"

        path, written = P.write("Another pattern", "problem", "what", "ev",
                                vault=v, gate=refuse)
        self.assertIsNone(path)
        self.assertFalse(written)
        self.assertEqual(os.listdir(os.path.join(v, P.FOLDER)), [])

    def test_an_injected_gate_that_passes_still_writes(self):
        v = vault()

        def allow(text, deny_list_path):
            return True, None

        path, written = P.write("A clean pattern", "problem", "what", "ev",
                                vault=v, gate=allow)
        self.assertTrue(written)
        self.assertTrue(os.path.exists(path))

    def test_a_gate_that_cannot_load_fails_closed(self):
        """gate_text itself, with a loader that cannot find the module,
        refuses rather than silently skipping the gate."""
        ok, reason = P.gate_text("anything", loader=lambda: None)
        self.assertFalse(ok)
        self.assertIn("NO-DATA", reason)


class TheReceiptTravelsWithTheNote(unittest.TestCase):
    """V4: the note that closes a green run links the receipt it came from."""

    def test_the_receipt_is_in_the_frontmatter_when_given(self):
        body = P.note_body("n", "s", "w", "e", "all",
                           receipt="scripts/test_x.py exit=0")
        self.assertIn("receipt: scripts/test_x.py exit=0", body)

    def test_no_receipt_line_when_none_given(self):
        body = P.note_body("n", "s", "w", "e", "all")
        self.assertNotIn("receipt:", body)

    def test_write_passes_the_receipt_through_to_the_file(self):
        v = vault()
        path, written = P.write("Receipted pattern", "problem", "what", "ev",
                                vault=v, receipt="scripts/foo.py exit=0")
        self.assertTrue(written)
        with open(path, encoding="utf-8") as fh:
            self.assertIn("receipt: scripts/foo.py exit=0", fh.read())


class ThreePatternsAreFindableByOneBroadProblemQuery(unittest.TestCase):
    """The earlier version of this check read the LIVE vault and asserted it
    held three patterns, which coupled a repo gate to machine state: the
    vault's ingestion pipeline rewrote those notes' frontmatter to
    `type: reference` on 2026-08-30 and the gate went red on an untouched
    checkout. The invariant worth keeping is hermetic: three patterns written
    here all come back for one problem-worded query."""

    def test_three_seeded_patterns_all_come_back_together(self):
        v = vault()
        add(v, "Drive every control backwards",
            "My check passes but I do not know whether it can ever fail")
        add(v, "Judge a branch against unchanged main",
            "The suite is red and I cannot tell whether my branch caused it")
        add(v, "Scope a scanner to what can leave the machine",
            "My push gate keeps refusing on files nobody could ever ship")
        hits = P.find("check gate branch scanner suite red fail ship", v)
        self.assertGreaterEqual(len(hits), 3)


if __name__ == "__main__":
    unittest.main()
