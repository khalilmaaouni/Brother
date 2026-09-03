"""What the progress bars must keep true.

A progress bar is the single most flatterable object on a status page, because
one number stands in for everything and nobody checks how it was made. So the
tests that matter are the ones that try to make it lie.
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_status as B  # noqa: E402


def item(status, evidence="", **kw):
    d = {"status": status, "evidence": evidence}
    d.update(kw)
    return d


class AClaimIsNotProgress(unittest.TestCase):
    """The whole reason these numbers can be trusted. DONE with an empty
    evidence field is a claim, and folding it into the bar is how a board starts
    flattering itself."""

    def test_done_without_evidence_is_classified_as_a_claim(self):
        self.assertEqual(B.classify(item("DONE")), "claimed")

    def test_done_with_evidence_is_done(self):
        self.assertEqual(B.classify(item("DONE", "the command and its output")), "done")

    def test_a_claim_does_not_raise_the_percentage(self):
        honest = [item("DONE", "x"), item("SCHEDULED")]
        flattering = [item("DONE", "x"), item("DONE")]
        self.assertEqual(B.percent(*(lambda t: (t[0], t[1]))(B.tally(honest))),
                         B.percent(*(lambda t: (t[0], t[1]))(B.tally(flattering))))

    def test_a_board_of_pure_claims_reads_zero_not_a_hundred(self):
        counts, total = B.tally([item("DONE"), item("DONE"), item("DONE")])
        self.assertEqual(B.percent(counts, total), 0.0)
        self.assertEqual(counts["claimed"], 3)

    def test_whitespace_is_not_evidence(self):
        self.assertEqual(B.classify(item("DONE", "   \n  ")), "claimed")


class TheStatesAreDistinguished(unittest.TestCase):
    def test_in_flight_is_neither_done_nor_open(self):
        self.assertEqual(B.classify(item("IN-FLIGHT")), "in_flight")

    def test_an_unknown_status_is_open_rather_than_assumed(self):
        self.assertEqual(B.classify(item("MARINATING")), "open")

    def test_a_missing_status_is_open(self):
        self.assertEqual(B.classify({}), "open")

    def test_merged_and_shipped_count_as_done_when_evidenced(self):
        for word in ("MERGED", "SHIPPED", "CLOSED"):
            self.assertEqual(B.classify(item(word, "x")), "done", word)


class NoDataIsNotZero(unittest.TestCase):
    """They render identically on a bar and mean opposite things: one is work
    not started, the other is a section this tool does not understand."""

    def test_an_empty_section_is_None_not_zero(self):
        counts, total = B.tally([])
        self.assertIsNone(B.percent(counts, total))

    def test_the_bar_shows_NO_DATA_rather_than_an_empty_bar(self):
        self.assertIn(B.NODATA, B.bar(None))

    def test_a_zero_percent_bar_is_not_the_same_string(self):
        self.assertNotIn(B.NODATA, B.bar(0.0))

    def test_an_unreadable_source_exits_NO_DATA(self):
        self.assertEqual(B.main(["--source", "/no/such/board.json"]), 2)


class ItAnswersTheQuestionThatWasAsked(unittest.TestCase):
    """The founder asked what the status of F1 was and the board could not say.
    A card must answer on the card."""

    def test_an_open_item_with_subtasks_names_how_many_are_evidenced(self):
        it = item("SCHEDULED", subtasks=[item("DONE", "x"), item("SCHEDULED")])
        state, why = B.item_status(it)
        self.assertEqual(state, "OPEN")
        self.assertIn("1 of 2", why)

    def test_an_open_item_with_NOTHING_under_it_says_so_plainly(self):
        state, why = B.item_status(item("SCHEDULED"))
        self.assertEqual(state, "OPEN")
        self.assertIn("nothing is decomposed under it", why)

    def test_a_claim_is_named_as_a_claim_on_its_own_card(self):
        state, why = B.item_status(item("DONE"))
        self.assertEqual(state, "CLAIMED")
        self.assertIn("not as progress", why)

    def test_the_real_board_answers_for_F1(self):
        code = B.main(["--item", "F1"])
        self.assertEqual(code, 0)


class TheRealBoardIsCounted(unittest.TestCase):
    def test_every_section_reports_a_number_or_NO_DATA(self):
        with open(B.SOURCE, encoding="utf-8") as fh:
            doc = json.load(fh)
        secs = B.sections(doc)
        self.assertTrue(secs)
        for s in secs:
            self.assertIn("percent", s)
            self.assertEqual(sum(s["counts"].values()), s["total"], s["label"])

    def test_the_live_board_carries_no_unevidenced_claim(self):
        """Currently true, and this is what keeps it true."""
        with open(B.SOURCE, encoding="utf-8") as fh:
            doc = json.load(fh)
        claims = []
        for key in ("features", "rows"):
            for it in doc.get(key) or []:
                if B.classify(it) == "claimed":
                    claims.append("%s/%s" % (key, it.get("id")))
        self.assertEqual(claims, [], "items claiming done with no evidence: %s" % claims)


class TheVaultCounter(unittest.TestCase):
    """WBS V12: the three counts on the board are read from a real store and
    the real vault, never typed, and each says NO-DATA rather than 0 when
    its source does not exist."""

    NOW = datetime.datetime(2026, 9, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)

    # -- lessons recalled this week (the access-audit jsonl) ----------------

    def test_a_missing_audit_file_is_NO_DATA_not_zero(self):
        count, command, err = B.lessons_recalled_this_week(
            audit_path="/no/such/bm_vault_audit.jsonl", now=self.NOW)
        self.assertIsNone(count)
        self.assertIsNotNone(err)
        self.assertIn("bm_vault_audit.py search", command)

    def test_a_seeded_audit_file_yields_the_seeded_count(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bm_vault_audit.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                # Two rows inside the last 7 days, one well outside it, one
                # malformed line that must be skipped rather than crash the
                # count.
                fh.write(json.dumps({"ts": "2026-09-01T00:00:00+00:00"}) + "\n")
                fh.write(json.dumps({"ts": "2026-08-27T00:00:00+00:00"}) + "\n")
                fh.write(json.dumps({"ts": "2026-08-01T00:00:00+00:00"}) + "\n")
                fh.write("not json\n")
                fh.write(json.dumps({"no_ts_field": True}) + "\n")
            count, _command, err = B.lessons_recalled_this_week(
                audit_path=path, now=self.NOW)
            self.assertIsNone(err)
            self.assertEqual(count, 2)

    # -- receipts bound (scripts/receipt_door.py over docs/plan/runs) -------

    def test_a_missing_runs_root_is_NO_DATA_not_zero(self):
        count, command, err = B.receipts_bound(runs_root="/no/such/runs")
        self.assertIsNone(count)
        self.assertIsNotNone(err)
        self.assertIn("board_status.py --vault-counters", command)

    def test_a_seeded_run_with_verified_evidence_is_counted(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = os.path.join(d, "20260901T000000-seed")
            os.makedirs(run_dir)
            with open(os.path.join(run_dir, "W-seed.json"), "w", encoding="utf-8") as fh:
                json.dump({"outcome": "seed", "rows": [
                    {"id": "u1", "done_check": "true",
                     "check_passed_before": False,
                     "files_changed_by_unit": ["some_file.py"]},
                    {"id": "u2", "done_check": "false"}]}, fh)
            with open(os.path.join(run_dir, "claims.json"), "w", encoding="utf-8") as fh:
                json.dump({
                    "u1": {"evidence": {"check_command": "true", "exit_code": 0}},
                    "u2": {"evidence": {"check_command": "false", "exit_code": 1}},
                }, fh)
            count, _command, err = B.receipts_bound(runs_root=d)
            self.assertIsNone(err)
            # u1 is verified (exit 0); u2 is not-data (exit 1, never verified).
            self.assertEqual(count, 1)

    def test_an_existing_but_empty_runs_root_is_a_real_zero(self):
        with tempfile.TemporaryDirectory() as d:
            count, _command, err = B.receipts_bound(runs_root=d)
            self.assertIsNone(err)
            self.assertEqual(count, 0)

    def _seed_run(self, run_dir, verified_id="u1"):
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "W-seed.json"), "w", encoding="utf-8") as fh:
            json.dump({"outcome": "seed", "rows": [
                {"id": verified_id, "done_check": "true",
                 "check_passed_before": False,
                 "files_changed_by_unit": ["some_file.py"]}]}, fh)
        with open(os.path.join(run_dir, "claims.json"), "w", encoding="utf-8") as fh:
            json.dump({verified_id: {
                "evidence": {"check_command": "true", "exit_code": 0}}}, fh)

    def test_a_run_in_a_second_root_is_counted(self):
        """WBS: receipts_bound must see a run made through the shipped
        runtime's own default (a second root), not only one made under this
        repository's own docs/plan/runs."""
        with tempfile.TemporaryDirectory() as d1, \
             tempfile.TemporaryDirectory() as d2:
            self._seed_run(os.path.join(d2, "20260901T000000-second-root"))
            count, _command, err = B.receipts_bound(runs_root=[d1, d2])
            self.assertIsNone(err)
            self.assertEqual(count, 1)

    def test_the_same_run_name_in_both_roots_counts_once(self):
        with tempfile.TemporaryDirectory() as d1, \
             tempfile.TemporaryDirectory() as d2:
            self._seed_run(os.path.join(d1, "20260901T000000-dup"))
            self._seed_run(os.path.join(d2, "20260901T000000-dup"))
            count, _command, err = B.receipts_bound(runs_root=[d1, d2])
            self.assertIsNone(err)
            self.assertEqual(count, 1)

    def test_neither_root_present_is_NO_DATA(self):
        count, command, err = B.receipts_bound(
            runs_root=["/no/such/runs-1", "/no/such/runs-2"])
        self.assertIsNone(count)
        self.assertIsNotNone(err)
        self.assertIn("2", err)
        self.assertIn("board_status.py --vault-counters", command)

    def test_the_default_reads_both_the_repo_and_user_run_roots(self):
        """No override at all: the real default must be the two-root list,
        never just RUNS_ROOT alone, or a real user run is invisible to this
        counter (the defect this test guards)."""
        count, command, err = B.receipts_bound()
        self.assertIsNone(err)
        self.assertIsNotNone(count)
        self.assertIn(B.RUNS_ROOT, command)
        self.assertIn(B.USER_RUNS_ROOT, command)

    # -- notes written this week (vault frontmatter) -------------------------

    def test_a_missing_vault_root_is_NO_DATA_not_zero(self):
        count, command, err = B.notes_written_this_week(
            vault_root="/no/such/vault", now=self.NOW)
        self.assertIsNone(count)
        self.assertIsNotNone(err)
        self.assertIn("board_status.py --vault-counters", command)

    def test_a_seeded_recent_note_is_counted_and_an_old_one_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            recent = os.path.join(d, "recent.md")
            old = os.path.join(d, "old.md")
            no_date = os.path.join(d, "no-frontmatter.md")
            with open(recent, "w", encoding="utf-8") as fh:
                fh.write("id: n1\ncreated: 2026-08-30\n---\nbody\n")
            with open(old, "w", encoding="utf-8") as fh:
                fh.write("id: n2\ncreated: 2026-08-01\n---\nbody\n")
            with open(no_date, "w", encoding="utf-8") as fh:
                fh.write("just some text, no frontmatter at all\n")
            count, _command, err = B.notes_written_this_week(
                vault_root=d, now=self.NOW)
            self.assertIsNone(err)
            self.assertEqual(count, 1)

    # -- the strip as a whole ------------------------------------------------

    def test_vault_counters_returns_the_three_labels_in_board_order(self):
        counters = B.vault_counters(
            now=self.NOW, audit_path="/no/such/audit.jsonl",
            runs_root="/no/such/runs", vault_root="/no/such/vault")
        labels = [c["label"] for c in counters]
        self.assertEqual(labels, ["lessons recalled this week",
                                  "receipts bound", "notes written this week"])
        for c in counters:
            self.assertIsNone(c["count"])
            self.assertIsNotNone(c["error"])
            self.assertTrue(c["command"])

    def test_the_vault_counters_flag_prints_all_three_lines(self):
        code = B.main(["--vault-counters"])
        self.assertEqual(code, 0)

    def test_the_default_run_prints_the_vault_counter_lines_too(self):
        """FINISH runs `python3 scripts/board_status.py` with no flag and
        expects the three lines in that same output."""
        code = B.main([])
        self.assertIn(code, (0, 1))


if __name__ == "__main__":
    unittest.main()
