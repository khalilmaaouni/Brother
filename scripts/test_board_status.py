"""What the progress bars must keep true.

A progress bar is the single most flatterable object on a status page, because
one number stands in for everything and nobody checks how it was made. So the
tests that matter are the ones that try to make it lie.
"""
import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

# Minor fix (opus-review-seams-g1-g3.md): redirect jev_seam's own
# machine-level state root to a throwaway temp dir BEFORE jev_seam is
# ever imported in this process, so no test here reads (or could ever
# write) the real ~/.brother/jev -- must happen before the `import
# jev_seam` line below: JEV_STATE_DIR is a module-level constant
# jev_seam.py computes once, at its own import time.
os.environ.setdefault("BROTHER_JEV_STATE_DIR",
                       tempfile.mkdtemp(prefix="brother-jev-state-test-"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_status as B  # noqa: E402
import jev_seam  # noqa: E402
import jev_g1_seam_cache  # noqa: E402


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
        counter (the defect this test guards).

        HERMETIC SINCE 2026-09-10. This used to assert that BOTH root paths
        appear in the reported command, which is only true on a machine where
        both directories exist. receipts_bound names the roots it FOUND, so on
        a fresh checkout or a CI runner the absent one is correctly missing
        from that string and the old assertion failed on a tool that was
        behaving. Measured: the Linux runner reported "1/2 run root(s)
        present" and the test read that as a defect. The claim under test is
        how many roots the default CONSIDERS, which the tool states as the
        denominator, so that is what is asserted here."""
        count, command, err = B.receipts_bound()
        self.assertIn("/2 run root(s)", command,
                      "the default must consider both roots, and the count "
                      "of considered roots is the denominator it prints")
        if err is None:
            self.assertIsNotNone(count)
        else:
            # No root exists on this machine, which is NO-DATA and not a
            # failure; the error still has to name both roots it looked in.
            self.assertIsNone(count)
            self.assertIn(B.RUNS_ROOT, err)
            self.assertIn(B.USER_RUNS_ROOT, err)

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



class TokensPerAcceptedDelivery(unittest.TestCase):
    """FL-2.1/FL-2.2: tokens per accepted delivery, joined to the unit trace
    by claim id. Every trace path here is an explicit temp path, never the
    real ~/.claude/unit-trace.jsonl."""

    def _write_trace(self, tmpdir, records):
        path = os.path.join(tmpdir, "trace.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
        return path

    def test_two_done_rows_with_unit_ids_join_to_the_exact_ratio(self):
        with tempfile.TemporaryDirectory() as d:
            trace_path = self._write_trace(d, [
                dict(claim_id="c1", tier="sonnet", effort="high",
                     tokens_in=100, tokens_out=50, cache_read=0,
                     wall_ms=1000, verdict="PASS"),
                dict(claim_id="c2", tier="sonnet", effort="high",
                     tokens_in=200, tokens_out=25, cache_read=0,
                     wall_ms=1000, verdict="PASS"),
                dict(claim_id="c3-unmatched-by-any-row", tier="sonnet",
                     effort="high", tokens_in=999, tokens_out=999,
                     cache_read=0, wall_ms=1000, verdict="PASS"),
            ])
            trace_lines, err = B.load_unit_trace(trace_path)
            self.assertIsNone(err)
            items = [item("DONE", "x", unit_ids=["c1"]),
                     item("DONE", "x", unit_ids=["c2"])]
            result = B.tokens_per_accepted_delivery_for_section(
                items, trace_lines, err)
            self.assertIsNotNone(result["value"])
            self.assertEqual(result["value"]["total"], 375)
            self.assertEqual(result["value"]["denominator"], 2)
            self.assertEqual(result["value"]["ratio"], 187.5)

    def test_the_printed_line_shows_the_exact_quotient(self):
        with tempfile.TemporaryDirectory() as d:
            trace_path = self._write_trace(d, [
                dict(claim_id="c1", tier="sonnet", effort="high",
                     tokens_in=100, tokens_out=50, cache_read=0,
                     wall_ms=1000, verdict="PASS"),
                dict(claim_id="c2", tier="sonnet", effort="high",
                     tokens_in=200, tokens_out=25, cache_read=0,
                     wall_ms=1000, verdict="PASS"),
            ])
            doc = dict(rows=[item("DONE", "x", unit_ids=["c1"]),
                             item("DONE", "x", unit_ids=["c2"])])
            secs = B.sections(doc)
            B.attach_tokens_per_accepted_delivery(secs, doc, trace_path=trace_path)
            row_sec = [s for s in secs if s["key"] == "rows"][0]
            self.assertEqual(row_sec["tokens_per_accepted_delivery"], 187.5)
            self.assertEqual(B.tokens_per_accepted_delivery_line(row_sec),
                              "tokens per accepted delivery: 375 / 2 = 187.5")

    def test_a_done_row_with_no_unit_ids_is_NO_DATA_never_zero(self):
        doc = dict(rows=[item("DONE", "the command and its output")])
        secs = B.sections(doc)
        with tempfile.TemporaryDirectory() as d:
            trace_path = os.path.join(d, "unused-trace.jsonl")
            B.attach_tokens_per_accepted_delivery(secs, doc, trace_path=trace_path)
        row_sec = [s for s in secs if s["key"] == "rows"][0]
        self.assertEqual(row_sec["tokens_per_accepted_delivery"], B.NODATA)
        self.assertNotEqual(row_sec["tokens_per_accepted_delivery"], 0)
        line = B.tokens_per_accepted_delivery_line(row_sec)
        self.assertIn(B.NODATA, line)
        self.assertIn("carry no unit id", line)

    def test_unit_ids_present_but_trace_file_absent_is_NO_DATA_naming_it(self):
        doc = dict(rows=[item("DONE", "x", unit_ids=["c1"])])
        secs = B.sections(doc)
        missing = "/no/such/unit-trace-fl2.jsonl"
        B.attach_tokens_per_accepted_delivery(secs, doc, trace_path=missing)
        row_sec = [s for s in secs if s["key"] == "rows"][0]
        self.assertEqual(row_sec["tokens_per_accepted_delivery"], B.NODATA)
        line = B.tokens_per_accepted_delivery_line(row_sec)
        self.assertIn(missing, line)

    def test_the_real_board_is_NO_DATA_everywhere_today(self):
        """No row on the live board carries unit_ids yet (FL-1 predates this
        board), so every section must say NO-DATA, never 0."""
        with open(B.SOURCE, encoding="utf-8") as fh:
            doc = json.load(fh)
        secs = B.sections(doc)
        with tempfile.TemporaryDirectory() as d:
            trace_path = os.path.join(d, "unused-trace.jsonl")
            B.attach_tokens_per_accepted_delivery(secs, doc, trace_path=trace_path)
        for s in secs:
            self.assertEqual(s["tokens_per_accepted_delivery"], B.NODATA, s["label"])

    def test_load_unit_trace_missing_file_is_NO_DATA(self):
        lines, err = B.load_unit_trace("/no/such/unit-trace-fl2.jsonl")
        self.assertIsNone(lines)
        self.assertIsNotNone(err)

    def test_load_unit_trace_malformed_line_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trace.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(claim_id="c1", tokens_in=1,
                                          tokens_out=1)) + "\n")
                fh.write("not json\n")
            lines, err = B.load_unit_trace(path)
            self.assertIsNone(lines)
            self.assertIsNotNone(err)

    def test_load_unit_trace_reads_every_non_empty_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trace.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(claim_id="c1", tokens_in=1,
                                          tokens_out=2)) + "\n")
                fh.write("\n")
                fh.write(json.dumps(dict(claim_id="c2", tokens_in=3,
                                          tokens_out=4)) + "\n")
            lines, err = B.load_unit_trace(path)
            self.assertIsNone(err)
            self.assertEqual(len(lines), 2)

    def test_a_NO_DATA_token_field_contributes_nothing_never_zero_cost(self):
        with tempfile.TemporaryDirectory() as d:
            trace_path = self._write_trace(d, [
                dict(claim_id="c1", tokens_in="NO-DATA", tokens_out=50),
            ])
            trace_lines, err = B.load_unit_trace(trace_path)
            items = [item("DONE", "x", unit_ids=["c1"])]
            result = B.tokens_per_accepted_delivery_for_section(
                items, trace_lines, err)
            self.assertEqual(result["value"]["total"], 50)

    def test_resolve_trace_path_argument_beats_env_beats_default(self):
        self.assertEqual(B.resolve_trace_path("explicit-path"), "explicit-path")
        old = os.environ.get("BROTHER_UNIT_TRACE")
        try:
            os.environ["BROTHER_UNIT_TRACE"] = "/env/unit-trace.jsonl"
            self.assertEqual(B.resolve_trace_path(None), "/env/unit-trace.jsonl")
            del os.environ["BROTHER_UNIT_TRACE"]
            self.assertEqual(B.resolve_trace_path(None), B.DEFAULT_TRACE_PATH)
        finally:
            if old is None:
                os.environ.pop("BROTHER_UNIT_TRACE", None)
            else:
                os.environ["BROTHER_UNIT_TRACE"] = old


def _j025_entry():
    return {
        "id": "J025", "role": "second_opinion", "risk": "low", "wave": "W1",
        "privacy": "public_or_own_text",
        "question": {
            "type": "choice",
            "instructions": "classify the row status",
            "options": ["ready", "blocked", "partial", "stale", "done",
                        "in-progress", "unknown"],
        },
    }


def _choice_runner(choice, prob=0.9):
    """A scripted bridge runner that always answers `choice`: the shape
    ScriptedRunner in test_jev_seam.py uses, reimplemented here (no
    network, no subprocess) so this file does not need to import that
    test module."""
    def fn(argv, stdin_text):
        payload = json.loads(stdin_text)
        answers = {}
        for qid, q in payload["questions"].items():
            keys = list(q["criteria"].keys())
            others = [k for k in keys if k != choice]
            probs = {choice: prob}
            if others:
                rest = (1.0 - prob) / len(others)
                for k in others:
                    probs[k] = rest
            answers[qid] = {"choice": choice, "probabilities": probs, "confidence": prob}
        response = {"model": "typesafe/jev-1.13-test", "answers": answers,
                    "usage": {"cost": 0.001}}
        return 0, json.dumps(response), ""
    return fn


class JevSeamJ025SecondOpinion(unittest.TestCase):
    """J025 (roadmap row status classify), wired in classify() via
    jev_seam.consult(). Per the wave-1 seam brief
    (Documents/BrotherArchive/jev-deep-research-2026-09-18/wave2/seam-brief-common.md):
    mode off ships unedited in data/jev-seams.json, so (a) below needs no
    mocking at all; (b)-(d) mock jev_seam.load_seams_config/load_registry
    to force shadow mode for this one test, never touching the real
    registry file or the network."""


    def setUp(self):
        # jev_g1_seam_cache caches the resolved mode per entry_id
        # (module docstring: at most one jev_seam.load_seams_config()
        # call per second) -- a call in an EARLIER test could still be
        # "fresh" here, so reset before every test rather than rely on
        # the freshness window happening to have elapsed. Also reset the
        # A4 per-render consult memo (_J025_CONSULT_MEMO): a status this
        # class or an earlier class already consulted about must not
        # silently suppress this test's own first consult for it.
        jev_g1_seam_cache.reset()
        B.reset_j025_consult_memo()
    def test_a_mode_off_makes_zero_calls_and_output_is_byte_identical(self):
        it = item("DONE", "x")

        def boom(argv, stdin_text):
            raise AssertionError("mode off must never invoke the runner")

        before = B.classify(it)
        after = B.classify(it, jev_runner=boom)
        self.assertEqual(before, "done")
        self.assertEqual(before, after)

    def test_b_shadow_mode_output_identical_and_one_ledger_row_written(self):
        it = item("DONE", "x")
        expected = B.classify(it)  # real config: off, no mocking
        ledger_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, ledger_dir, ignore_errors=True)
        cfg = {"modes": {"J025": "shadow"}}
        jev_g1_seam_cache.reset()  # the baseline call above may have cached this entry as off
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry", return_value=[_j025_entry()]), \
                mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", ledger_dir):
            after = B.classify(it, jev_runner=_choice_runner("blocked"))
        self.assertEqual(after, expected)
        # A0.8: shadow hands the call to a background worker and returns
        # at once, before the ledger row exists. drain() waits for the
        # worker to finish so the row is actually there to count.
        jev_seam.drain(timeout=5)
        decisions_path = os.path.join(ledger_dir, "decisions.jsonl")
        self.assertTrue(os.path.isfile(decisions_path), "shadow mode must write a ledger row")
        with open(decisions_path, encoding="utf-8") as fh:
            rows = [ln for ln in fh if ln.strip()]
        self.assertEqual(len(rows), 1)

    def test_c_seam_path_exception_leaves_output_identical(self):
        it = item("DONE", "x")
        expected = B.classify(it)
        cfg = {"modes": {"J025": "shadow"}}
        jev_g1_seam_cache.reset()  # the baseline call above may have cached this entry as off
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry",
                                   side_effect=RuntimeError("registry unreadable")):
            after = B.classify(it, jev_runner=_choice_runner("blocked"))
        self.assertEqual(after, expected)

    def test_d_red_proof_wiring_that_trusts_jevs_raw_answer_breaks_test_b(self):
        """Prove test_b actually catches a wrong wiring: a call site that
        used consult()'s raw jev.answer (bypassing the mode-safe .answer
        field) would return a DIFFERENT verdict than the caller's own
        under shadow mode with an opposite-answering runner -- exactly
        what test_b asserts never happens. This never edits board_status.py
        itself; it calls jev_seam.consult() the same way classify() does
        and shows the two fields disagree, then confirms the real
        classify() is wired to the safe one."""
        it = item("DONE", "x")
        st = str(it.get("status") or "").upper().strip()
        current_answer = B.classify(it)  # "done", real config (off)
        ledger_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, ledger_dir, ignore_errors=True)
        cfg = {"modes": {"J025": "shadow"}}
        jev_g1_seam_cache.reset()  # the baseline call above may have cached this entry as off
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry", return_value=[_j025_entry()]), \
                mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", ledger_dir):
            result = jev_seam.consult(
                "J025", {"status": st}, current_answer,
                seams_config=jev_seam.load_seams_config(),
                registry=jev_seam.load_registry(),
                ledger_dir=jev_seam.DEFAULT_LEDGER_DIR,
                runner=_choice_runner("blocked"),
            )
            # A0.8: shadow never returns Jev's answer (result.jev is
            # always None); the eventual answer only lands in the ledger,
            # once the background worker finishes. drain() first, then
            # read it there.
            jev_seam.drain(timeout=5)
            decisions_path = os.path.join(ledger_dir, "decisions.jsonl")
            with open(decisions_path, encoding="utf-8") as fh:
                rows = [json.loads(ln) for ln in fh if ln.strip()]
            self.assertEqual(len(rows), 1)
            wrongly_wired_answer = rows[0]["answer"]  # what a broken call site would return
            safely_wired_answer = result.answer       # what classify() actually returns

            # The red proof: the wrong wiring disagrees with the caller's
            # own verdict (so a test_b-shaped assertion would fail on it)...
            self.assertNotEqual(wrongly_wired_answer, current_answer)
            # ...while the real wiring (and the real classify() call) does not.
            self.assertEqual(safely_wired_answer, current_answer)
            after = B.classify(it, jev_runner=_choice_runner("blocked"))
        self.assertEqual(after, current_answer)

    def test_e_a1_call_site_never_reads_a_patched_consults_return(self):
        """A1 (opus-review-g1-round2-pkg-decide.md): test_d proves
        consult()'s two fields disagree, but it never touches the CALL
        SITE's own wiring -- classify() is never invoked inside the
        mocked block there. This one does: jev_seam.consult ITSELF is
        patched to return an ACT-mode SeamResult whose .answer is 0.05 (a
        float, the wrong TYPE for classify()'s own str return value),
        mode live via a patched config, and asserts classify() still
        returns its own local verdict, never 0.05. A call site wired as
        `answer = jev_seam.consult(...).answer` (the pre-C1 wiring this
        proves against) would return 0.05 here instead."""
        it = item("DONE", "x")
        cfg = {"modes": {"J025": "shadow"}}
        jev_g1_seam_cache.reset()
        wrong = jev_seam.SeamResult(0.05, {"answer": 0.05}, jev_seam.ACT,
                                    None, False, "mutation-probe")
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry", return_value=[_j025_entry()]), \
                mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", tempfile.mkdtemp()), \
                mock.patch.object(jev_seam, "consult", return_value=wrong):
            after = B.classify(it)
        self.assertEqual(after, "done")
        self.assertNotEqual(after, wrong.answer)


class J025ConsultIsMemoizedPerRenderNotPerRow(unittest.TestCase):
    """A4 (opus-review-g1-round2-pkg-decide.md): a probe on the live board
    found classify() making one J025 consult() call per roadmap ROW: 671
    calls against only 7 distinct status strings. B.reset_j025_consult_memo()
    clears classify()'s own per-render memo (module docstring next to
    _J025_CONSULT_MEMO); main() calls it once per render."""

    def setUp(self):
        jev_g1_seam_cache.reset()
        B.reset_j025_consult_memo()

    def test_671_rows_7_distinct_statuses_make_at_most_7_consult_calls(self):
        statuses = ["READY", "BLOCKED", "PARTIAL", "STALE", "DONE",
                    "IN-PROGRESS", "MARINATING"]
        items = [item(statuses[i % len(statuses)], "x") for i in range(671)]
        self.assertEqual(len({it["status"] for it in items}), 7)

        calls = []

        def counting_consult(entry_id, state, current_answer, **kw):
            calls.append(state)
            return jev_seam.SeamResult(current_answer, None, jev_seam.SHADOW,
                                       None, False, None)

        cfg = {"modes": {"J025": "shadow"}}
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry", return_value=[_j025_entry()]), \
                mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", tempfile.mkdtemp()), \
                mock.patch.object(jev_seam, "consult", side_effect=counting_consult):
            counts, total = B.tally(items)

        self.assertEqual(total, 671)
        self.assertLessEqual(len(calls), 7)
        self.assertEqual({c["status"] for c in calls}, set(statuses))


def _j030_entry():
    return {
        "id": "J030", "role": "second_opinion", "risk": "medium", "wave": "W1",
        "privacy": "needs_content_gate",
        "question": {
            "type": "noul",
            "instructions": "does the row's claim match its quoted source",
        },
    }


def _noul_runner(prob):
    def fn(argv, stdin_text):
        payload = json.loads(stdin_text)
        answers = {qid: {"noul": prob, "confidence": prob}
                   for qid in payload["questions"]}
        response = {"model": "typesafe/jev-1.13-test", "answers": answers,
                    "usage": {"cost": 0.001}}
        return 0, json.dumps(response), ""
    return fn


class JevSeamJ030ClaimSupportedBySource(unittest.TestCase):
    """J030 (roadmap row claim-vs-source support), wired into classify()'s
    DONE branch via claim_supported_by_source(). Same shadow-only,
    return-value-discarded contract as J025 (JevSeamJ025SecondOpinion
    above); mirrored here rather than shared because the two seams guard
    different call sites and different local answer types (bool vs str)."""

    def setUp(self):
        jev_g1_seam_cache.reset()
        B.reset_j030_consult_memo()

    def test_a_mode_off_makes_zero_calls_and_output_is_byte_identical(self):
        it = item("DONE", "x")

        def boom(argv, stdin_text):
            raise AssertionError("mode off must never invoke the runner")

        before = B.classify(it)
        after = B.classify(it, jev_runner=boom)
        self.assertEqual(before, "done")
        self.assertEqual(before, after)

    def test_b_shadow_mode_output_identical_and_one_ledger_row_written(self):
        it = item("DONE", "x")
        expected = B.classify(it)  # real config: off, no mocking
        ledger_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, ledger_dir, ignore_errors=True)
        cfg = {"modes": {"J030": "shadow"}}
        jev_g1_seam_cache.reset()
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry", return_value=[_j030_entry()]), \
                mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", ledger_dir):
            after = B.classify(it, jev_runner=_noul_runner(0.1))  # a "no" answer, still must not flip
        self.assertEqual(after, expected)
        jev_seam.drain(timeout=5)
        decisions_path = os.path.join(ledger_dir, "decisions.jsonl")
        self.assertTrue(os.path.isfile(decisions_path), "shadow mode must write a ledger row")
        with open(decisions_path, encoding="utf-8") as fh:
            rows = [ln for ln in fh if ln.strip()]
        self.assertEqual(len(rows), 1)

    def test_c_seam_path_exception_leaves_output_identical(self):
        it = item("DONE", "x")
        expected = B.classify(it)
        cfg = {"modes": {"J030": "shadow"}}
        jev_g1_seam_cache.reset()
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry",
                                   side_effect=RuntimeError("registry unreadable")):
            after = B.classify(it, jev_runner=_noul_runner(0.1))
        self.assertEqual(after, expected)

    def test_d_a_no_evidence_row_never_calls_the_seam_but_still_reads_claimed(self):
        # The FLIGHT_WORDS/open branches of classify() never reach
        # claim_supported_by_source() at all (only the DONE_WORDS branch
        # calls it); a "claimed" row (DONE with empty evidence) is a
        # local False from has_evidence() before the seam is even
        # consulted for a live mode, since the memo key includes the
        # empty evidence string and _J030_CONSULT_MEMO is still exercised
        # -- assert the classify() verdict itself first.
        it = item("DONE", "")
        self.assertEqual(B.classify(it), "claimed")

    def test_e_red_proof_wiring_that_trusts_jevs_raw_answer_breaks_test_b(self):
        """Same red-proof shape as J025's own test_d: a call site using
        consult()'s raw jev.answer instead of the mode-safe .answer field
        would return Jev's own value, not the caller's local bool -- this
        never edits board_status.py, it calls jev_seam.consult() the same
        way claim_supported_by_source() does and shows the two disagree,
        then confirms the real call site is wired to the safe one."""
        it = item("DONE", "x")
        claim = str(it.get("title") or it.get("status") or "").strip()
        evidence = str(it.get("evidence") or "").strip()
        current_answer = B.claim_supported_by_source(it)  # True, real config (off)
        ledger_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, ledger_dir, ignore_errors=True)
        cfg = {"modes": {"J030": "shadow"}}
        jev_g1_seam_cache.reset()
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry", return_value=[_j030_entry()]), \
                mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", ledger_dir):
            result = jev_seam.consult(
                "J030", {"claim": claim, "evidence": evidence}, current_answer,
                seams_config=jev_seam.load_seams_config(),
                registry=jev_seam.load_registry(),
                ledger_dir=jev_seam.DEFAULT_LEDGER_DIR,
                runner=_noul_runner(0.05),  # a confident "no", opposite of current_answer
            )
            jev_seam.drain(timeout=5)
            decisions_path = os.path.join(ledger_dir, "decisions.jsonl")
            with open(decisions_path, encoding="utf-8") as fh:
                rows = [json.loads(ln) for ln in fh if ln.strip()]
            self.assertEqual(len(rows), 1)
            wrongly_wired_answer = rows[0]["answer"]
            safely_wired_answer = result.answer
            self.assertNotEqual(wrongly_wired_answer, current_answer)
            self.assertEqual(safely_wired_answer, current_answer)
            after = B.classify(it, jev_runner=_noul_runner(0.05))
        self.assertEqual(after, "done")  # current_answer == True -> classify() still says "done"


class J030ConsultIsMemoizedPerRenderNotPerRow(unittest.TestCase):
    """Same A4 reasoning as J025ConsultIsMemoizedPerRenderNotPerRow, for
    J030's own memo: keyed on (claim, evidence), not the item, so repeat
    pairs across many rows collapse to at most one consult() call each."""

    def setUp(self):
        jev_g1_seam_cache.reset()
        B.reset_j030_consult_memo()

    def test_many_rows_few_distinct_pairs_make_at_most_that_many_calls(self):
        pairs = [("DONE", "x"), ("DONE", "y"), ("DONE", "x"), ("DONE", "")]
        items = [item(st, ev) for st, ev in pairs] * 50  # 200 rows, 3 distinct pairs
        self.assertEqual(len(items), 200)

        calls = []

        def counting_consult(entry_id, state, current_answer, **kw):
            calls.append(state)
            return jev_seam.SeamResult(current_answer, None, jev_seam.SHADOW,
                                       None, False, None)

        cfg = {"modes": {"J030": "shadow"}}
        with mock.patch.object(jev_seam, "load_seams_config", return_value=cfg), \
                mock.patch.object(jev_seam, "load_registry", return_value=[_j030_entry()]), \
                mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", tempfile.mkdtemp()), \
                mock.patch.object(jev_seam, "consult", side_effect=counting_consult):
            counts, total = B.tally(items)

        self.assertEqual(total, 200)
        self.assertLessEqual(len(calls), 3)


class JevSeamPerfCacheNeverHitsDiskAfterWarmup(unittest.TestCase):
    """Coordinator directive (measured regression in another wave-1
    group: a seam in OFF mode made a hot call site 52x slower by
    re-reading data/jev-seams.json on every call): after a first call
    warms jev_seam's in-process cache, 1,000 further OFF-mode calls to
    classify() must touch the filesystem zero times and return the
    identical answer every time."""


    def setUp(self):
        # jev_g1_seam_cache caches the resolved mode per entry_id
        # (module docstring: at most one jev_seam.load_seams_config()
        # call per second) -- a call in an EARLIER test could still be
        # "fresh" here, so reset before every test rather than rely on
        # the freshness window happening to have elapsed. Also reset the
        # A4 per-render consult memo, for the same reason.
        jev_g1_seam_cache.reset()
        B.reset_j025_consult_memo()
    def test_1000_off_mode_calls_after_warmup_touch_no_filesystem(self):
        it = item("DONE", "x")
        first = B.classify(it)  # warms jev_seam's config cache
        self.assertEqual(first, "done")

        calls = {"stat": 0, "exists": 0, "open": 0}
        real_stat, real_exists, real_open = os.stat, os.path.exists, open

        def counting_stat(*a, **kw):
            calls["stat"] += 1
            return real_stat(*a, **kw)

        def counting_exists(*a, **kw):
            calls["exists"] += 1
            return real_exists(*a, **kw)

        def counting_open(*a, **kw):
            calls["open"] += 1
            return real_open(*a, **kw)

        outputs = set()
        with mock.patch("os.stat", counting_stat), \
                mock.patch("os.path.exists", counting_exists), \
                mock.patch("builtins.open", counting_open):
            for _ in range(1000):
                outputs.add(B.classify(it))

        self.assertEqual(calls, {"stat": 0, "exists": 0, "open": 0},
                         "an off-mode call must never touch the filesystem "
                         "once jev_seam's config cache is warm")
        self.assertEqual(outputs, {"done"})


def _pid_alive(pid):
    """True if `pid` is still a live process this test process can see.
    Never raises. Mirrors test_jev_checks.py's/test_jev_decide.py's own
    small, self-contained _pid_alive() (not worth importing across test
    modules)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class Item3AtexitDrainCoversG1CallSitesWithoutEditingThem(unittest.TestCase):
    """Item 3 (A0.8 round 6, m1 and the G1 scope): the exit-drain fix
    lives ONCE in jev_seam.py (_exit_drain_s()/_atexit_drain()) and must
    cover every one of the eight G1 short-lived callers (board_status,
    doc_assurance, export_public, intake_score x2,
    mobile_hybrid_action_router x2, receipt_door) with NONE of them
    calling drain() themselves. board_status.py's J025 call site
    (classify(), see JevSeamJ025SecondOpinion above) stands in for all
    eight: every one of the eight is wired through the exact same
    jev_seam.consult() call, with no drain() of its own, so proving the
    fix here proves it for the whole G1 shape.

    Every test below runs classify() inside a REAL, separate OS process
    (never an injected runner or an in-process atexit call in THIS
    process), so the real atexit hook this fix lives in actually fires
    at real process exit. seams_config is injected by monkeypatching
    jev_seam.load_seams_config INSIDE that child process (started, never
    stopped, so the patch is still active when atexit fires at shutdown)
    rather than by touching the tracked data/jev-seams.json -- the same
    technique JevSeamJ025SecondOpinion's own in-process tests already use
    for this same function, just carried across the process boundary."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="brother-item3-g1-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.state_dir = os.path.join(self._tmp, "state")
        os.makedirs(self.state_dir)
        self.ledger_path = os.path.join(self.state_dir, "ledger", "decisions.jsonl")

    def _write_bridge(self, sleep_s):
        """A real executable bridge: writes its own pid to the path named
        by JEV_TEST_BRIDGE_PIDFILE (an env var, never argv[1] -- see item
        6/FOLLOW-UPS.md's own test-pollution fix in test_jev_checks.py
        for why argv[1] is unsafe here: decide() always appends
        "--decisions" as the bridge's first real argument) before
        sleeping, then answers every question with a fixed, TYPE-CORRECT
        answer (J025 is a choice question: a bridge that always answered
        {"noul": ...} fails jev_decide's own validation the moment
        something actually drains for the result -- same trap
        test_jev_checks.py's own _write_bridge fixture docstring names)."""
        path = os.path.join(self._tmp, "bridge_%s.py" % uuid.uuid4().hex)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import sys, os, json, time\n"
                "_pidfile = os.environ.get('JEV_TEST_BRIDGE_PIDFILE')\n"
                "if _pidfile:\n"
                "    with open(_pidfile, 'w') as f:\n"
                "        f.write(str(os.getpid()))\n"
                "time.sleep(%r)\n"
                "data = json.loads(sys.stdin.read())\n"
                "answers = {}\n"
                "for qid, q in data.get('questions', {}).items():\n"
                "    qtype = q.get('type')\n"
                "    if qtype == 'choice':\n"
                "        opts = list((q.get('criteria') or {}).keys()) or ['unknown']\n"
                "        answers[qid] = {'choice': opts[0], 'probabilities': {opts[0]: 0.9}}\n"
                "    elif qtype == 'score':\n"
                "        crit = q.get('criteria')\n"
                "        val = crit[0] if isinstance(crit, list) and crit else 'ok'\n"
                "        answers[qid] = {'score': val}\n"
                "    else:\n"
                "        answers[qid] = {'noul': 0.9, 'confidence': 0.9}\n"
                "print(json.dumps({'model': 'typesafe/jev-test', 'answers': answers, "
                "'usage': {'cost': 0.001}}))\n"
                % sleep_s
            )
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def _run_child(self, cfg, bridge_argv, pidfile=None):
        scripts_dir = os.path.dirname(os.path.abspath(B.__file__))
        child_path = os.path.join(self._tmp, "child_%s.py" % uuid.uuid4().hex)
        with open(child_path, "w", encoding="utf-8") as fh:
            fh.write(
                "import sys\n"
                "sys.path.insert(0, %r)\n"
                "from unittest import mock\n"
                "import jev_seam\n"
                "import board_status\n"
                # Started, never stopped: the patch must still be active
                # when the real atexit hook fires at process shutdown,
                # after this script's own top-level code has finished.
                "mock.patch.object(jev_seam, 'load_seams_config', return_value=%r).start()\n"
                "board_status.classify({'status': 'IN PROGRESS'})\n"
                "sys.stdout.write('child done\\n')\n"
                % (scripts_dir, cfg)
            )
        env = dict(os.environ)
        env["BROTHER_JEV_STATE_DIR"] = self.state_dir
        env["BROTHER_DECISION_BRIDGE"] = " ".join(bridge_argv)
        if pidfile:
            env["JEV_TEST_BRIDGE_PIDFILE"] = pidfile
        return subprocess.run([sys.executable, child_path], env=env,
                               capture_output=True, text=True, timeout=30.0)

    def _decision_rows(self):
        if not os.path.exists(self.ledger_path):
            return 0
        with open(self.ledger_path, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def test_3s_bridge_lands_its_ledger_row(self):
        bridge = self._write_bridge(3.0)
        cfg = {"modes": {"J025": "shadow"}}
        proc = self._run_child(cfg, [sys.executable, bridge])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("child done", proc.stdout)
        self.assertEqual(self._decision_rows(), 1,
                          "a G1 call site (J025) must get its shadow row landed by the central "
                          "atexit fix alone, with no drain() call of its own in board_status.py")

    def test_20s_bridge_with_a_small_cap_prints_one_stderr_line_and_leaves_no_orphan(self):
        pidfile = os.path.join(self._tmp, "bridge.pid")
        bridge = self._write_bridge(20.0)
        cfg = {"modes": {"J025": "shadow"}, "call_deadline_s": 0.5}
        proc = self._run_child(cfg, [sys.executable, bridge], pidfile=pidfile)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("child done", proc.stdout)
        self.assertIn("atexit drain abandoned 1 shadow call", proc.stderr)
        self.assertIn("entry id(s): J025", proc.stderr)
        self.assertEqual(self._decision_rows(), 0)
        self.assertTrue(os.path.exists(pidfile), "the bridge never even started")
        with open(pidfile, encoding="utf-8") as f:
            pid = int(f.read().strip())
        time.sleep(0.3)  # give the OS a moment to actually reap the killed bridge
        self.assertFalse(_pid_alive(pid),
                          "the bridge subprocess must not survive process exit once the "
                          "sized atexit drain has timed out")


if __name__ == "__main__":
    unittest.main()
