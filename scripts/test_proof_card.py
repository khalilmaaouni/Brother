"""Calibration for scripts/proof_card.py, driven in both directions (P0-D, P0-E).

The property this file exists to assert is not that a card prints. It is that
the card NEVER upgrades uncertainty into success: a missing record reads
NO-DATA rather than a flattering zero, a file whose proof is absent keeps
Outcome off PASS, and a check that was already green before the change never
counts as Proven. Every fixture below is built so that mutating one fact
flips exactly the row that fact governs, mirroring
scripts/test_safe_unwatched_time.py's own fixture style (a temp run
directory per case, one journal/claims/receipt shape reused and perturbed).
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'scripts'))
import proof_card as pc  # noqa: E402
import codex_battery  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

import datetime  # noqa: E402

BASE = datetime.datetime(2026, 9, 10, 20, 0, 0, tzinfo=datetime.timezone.utc)


def at(minute):
    return (BASE + datetime.timedelta(minutes=minute)).isoformat()


def event(minute, kind, payload=None, unit_id=None):
    return {"at": at(minute), "type": kind, "unit_id": unit_id,
            "payload": payload or {}}


def claim(unit_id, claimed_min, released_min, exit_code=0, state="done",
          attempt=1):
    """A claims.json entry, epoch stamped like claim_store.release() writes
    one: claimed_at/released_at as epoch floats (safe_unwatched_time._epoch
    is the reader), state the lane ended in, evidence carrying the check's
    own exit code."""
    claimed_at = (BASE + datetime.timedelta(minutes=claimed_min)).timestamp()
    released_at = (BASE + datetime.timedelta(minutes=released_min)).timestamp()
    entry = {"unit_id": unit_id, "state": state, "attempt": attempt,
             "claimed_at": claimed_at, "released_at": released_at}
    if exit_code is not None:
        entry["evidence"] = {"check_command": "python3 -c pass",
                              "exit_code": exit_code}
    return entry


def receipt_entry(file, unit, check_command="python3 -m unittest x -v",
                   exit_code=0, check_passed_before=False, state="verified",
                   reason="", output_location="receipt/x.log"):
    """One receipt.json scope.changed entry, receipt_door.per_file_checks'
    own shape (file, unit, check_command, exit_code, output_location,
    check_passed_before, state, reason)."""
    return {"file": file, "unit": unit, "check_command": check_command,
            "exit_code": exit_code, "output_location": output_location,
            "check_passed_before": check_passed_before, "state": state,
            "reason": reason}


def write_run(root, name, events=None, claims=None, rows=None, receipt=None):
    """One run directory on disk, writing only the files given (None skips
    that file entirely, matching a real run that never wrote it). Returns
    the run directory path."""
    run_dir = os.path.join(root, name)
    os.makedirs(run_dir)
    if events is not None:
        with open(os.path.join(run_dir, "journal.jsonl"), "w",
                  encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")
    if claims is not None:
        with open(os.path.join(run_dir, "claims.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(claims, fh, indent=1)
    if rows is not None:
        with open(os.path.join(run_dir, "W-%s.json" % name), "w",
                  encoding="utf-8") as fh:
            json.dump({"rows": rows}, fh, indent=1)
    if receipt is not None:
        os.makedirs(os.path.join(run_dir, "receipt"), exist_ok=True)
        with open(os.path.join(run_dir, "receipt", "receipt.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(receipt, fh, indent=1)
    return run_dir


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="proof-card-")
        self.addCleanup(shutil.rmtree, self.root, True)


#: Two units, both merged, a receipt proving both files, reused and
#: perturbed by every case below so only the perturbation can move the
#: result.
GREEN_ROWS = [{"id": "u1", "files_changed_by_unit": ["a.py"]},
              {"id": "u2", "files_changed_by_unit": ["b.py"]}]
GREEN_CLAIMS = {"u1": claim("u1", 0, 10), "u2": claim("u2", 10, 20)}
GREEN_EVENTS = [event(0, "run.opened", {"units": 2}),
                event(20, "receipt.issued", {"receipts": 2, "unproven": 0})]
GREEN_RECEIPT = {"scope": {"changed": [
    receipt_entry("a.py", "u1"), receipt_entry("b.py", "u2")]}}


class TheOutcomeClosure(Sandbox):
    """P0-E: outcome_status() never prints PASS on less than the full bar,
    and never drops a quarantine to look tidier."""

    def test_a_full_green_run_reads_unit_complete_with_oracle_no_data(self):
        """No writer in this estate stamps a top-level oracle event today
        (the module docstring's own known limit), so the honest ceiling
        for a run this clean is UNIT-COMPLETE, not PASS."""
        run = write_run(self.root, "green", GREEN_EVENTS, GREEN_CLAIMS,
                        GREEN_ROWS, GREEN_RECEIPT)
        fields = pc.build_card(run)
        self.assertEqual(fields["outcome"]["value"], "UNIT-COMPLETE")
        self.assertEqual(fields["top_level_oracle"]["value"], "NO-DATA")

    def test_a_recorded_top_level_check_upgrades_the_same_run_to_pass(self):
        """The ONLY difference from the case above is one journal event.
        Driven backwards: remove it and PASS must disappear again."""
        events = GREEN_EVENTS + [
            event(21, "oracle.recorded", {"exit_code": 0, "command": "make check"})]
        run = write_run(self.root, "green-oracle", events, GREEN_CLAIMS,
                        GREEN_ROWS, GREEN_RECEIPT)
        fields = pc.build_card(run)
        self.assertEqual(fields["outcome"]["value"], "PASS")
        self.assertEqual(fields["top_level_oracle"]["value"], "PASS")

        # Backwards: the same run with the oracle event removed reads
        # UNIT-COMPLETE again, proving the event (not the fixture in
        # general) is what carried PASS.
        run2 = write_run(self.root, "green-no-oracle", GREEN_EVENTS,
                         GREEN_CLAIMS, GREEN_ROWS, GREEN_RECEIPT)
        fields2 = pc.build_card(run2)
        self.assertEqual(fields2["outcome"]["value"], "UNIT-COMPLETE")

    def test_one_no_data_file_forces_outcome_off_pass_even_with_a_top_pass(self):
        """A card is never allowed to say Outcome PASS while one changed
        file's own proof is missing, even when the top-level check itself
        passed. Top-level oracle still reads PASS on its own: the two rows
        answer different questions."""
        receipt = {"scope": {"changed": [
            receipt_entry("a.py", "u1"),
            receipt_entry("b.py", "u2", exit_code=1, check_passed_before=False,
                          state="refused", reason="check did not run to a verdict")]}}
        events = GREEN_EVENTS + [
            event(21, "oracle.recorded", {"exit_code": 0, "command": "make check"})]
        run = write_run(self.root, "one-no-data-file", events, GREEN_CLAIMS,
                        GREEN_ROWS, receipt)
        fields = pc.build_card(run)
        self.assertNotEqual(fields["outcome"]["value"], "PASS")
        self.assertEqual(fields["outcome"]["value"], "UNIT-COMPLETE")
        self.assertEqual(fields["top_level_oracle"]["value"], "PASS")
        self.assertEqual(fields["no_data_files"]["value"], "1")

    def test_a_quarantine_verdict_forces_fail_and_counts_one_scope_violation(self):
        """FAIL is checked before "are all units terminal": a run that
        quarantined still needed a human, whatever its siblings did next."""
        rows = [{"id": "u1", "files_changed_by_unit": ["a.py"]}]
        claims = {"u1": claim("u1", 0, 10, state="claimed")}
        events = [event(0, "run.opened", {"units": 1}),
                 event(5, "integrate.refused", {
                     "reason": "QUARANTINE: 1 path(s) changed that u1 never "
                              "declared: stray.txt. Held, not discarded."})]
        run = write_run(self.root, "quarantined", events, claims, rows)
        fields = pc.build_card(run)
        self.assertEqual(fields["outcome"]["value"], "FAIL")
        self.assertEqual(fields["scope_violations"]["value"], "1")

        # Backwards: the same fixture with the refusal reason not naming a
        # quarantine must not trip the scope-violation count.
        events_clean = [event(0, "run.opened", {"units": 1}),
                        event(5, "integrate.refused", {
                            "reason": "did not pass: exit 1"})]
        run2 = write_run(self.root, "not-quarantined", events_clean, claims, rows)
        fields2 = pc.build_card(run2)
        self.assertEqual(fields2["scope_violations"]["value"], "0")

    def test_missing_records_read_no_data_never_a_guess(self):
        """No journal, no claims.json, no receipt: rows exist but nothing
        else does, so this run cannot be told PASS from FAIL from anything.
        Every reader downstream of the missing files renders NO-DATA and
        the card still prints end to end rather than crashing."""
        run = write_run(self.root, "no-records", events=None, claims=None,
                        rows=GREEN_ROWS, receipt=None)
        fields = pc.build_card(run)
        for key in ("execution", "peak_concurrency", "files_changed",
                   "proven", "green_only", "no_data_files",
                   "scope_violations", "duplicate_work", "wall_clock",
                   "sequential_est", "human_interrupts", "receipt"):
            self.assertEqual(fields[key]["value"], pc.NODATA,
                             "%s should be NO-DATA with no records" % key)
        text = pc.render_text(fields)
        self.assertIn(pc.TITLE, text)
        self.assertIn("NO-DATA", text)

    def test_outcome_status_itself_reads_no_data_on_an_unparseable_shape(self):
        """Direct calibration of outcome_status(): rows present but claims
        not even a dict (a corrupt claims.json) must not be misread as
        "zero units done"."""
        records = {"rows": GREEN_ROWS, "claims": "not-a-dict",
                  "top_level_check": None, "files": [], "quarantine_count": 0}
        outcome, oracle = pc.outcome_status(records)
        self.assertEqual(outcome, pc.NODATA)
        self.assertEqual(oracle, pc.NODATA)


class ThePerFileVocabulary(Sandbox):
    """P0-D: PROVES CHANGE, GREEN ONLY, NO-DATA, never PASS."""

    def test_a_check_green_before_the_change_reads_green_only_not_proven(self):
        entry = receipt_entry("a.py", "u1", exit_code=0,
                              check_passed_before=True, state="verified")
        label, _reason = pc.file_proof_label(entry)
        self.assertEqual(label, "GREEN ONLY")

        run = write_run(self.root, "green-before", [event(0, "run.opened")],
                        {"u1": claim("u1", 0, 10)},
                        [{"id": "u1", "files_changed_by_unit": ["a.py"]}],
                        {"scope": {"changed": [entry]}})
        fields = pc.build_card(run)
        self.assertEqual(fields["green_only"]["value"], "1")
        self.assertEqual(fields["proven"]["value"], "0")

        # Backwards: the identical entry with check_passed_before False
        # (the ordinary case) reads PROVES CHANGE, not GREEN ONLY.
        proved = receipt_entry("a.py", "u1", exit_code=0,
                               check_passed_before=False, state="verified")
        label2, _r2 = pc.file_proof_label(proved)
        self.assertEqual(label2, "PROVES CHANGE")

    def test_no_entry_is_ever_labelled_pass(self):
        for entry in (receipt_entry("a.py", "u1"),
                     receipt_entry("a.py", "u1", exit_code=1, state="refused"),
                     receipt_entry("a.py", "u1", check_passed_before=True),
                     {}, {"exit_code": 0}):
            label, _reason = pc.file_proof_label(entry)
            self.assertNotEqual(label, "PASS")
            self.assertIn(label, ("PROVES CHANGE", "GREEN ONLY", pc.NODATA))


class TheConcurrencyReaders(Sandbox):
    def test_peak_concurrency_of_three_intervals_where_only_two_overlap(self):
        """a: 0..10, b: 5..15 overlap for [5, 10); c: 20..30 never overlaps
        either. The peak live set is 2, never 3."""
        claims = {"a": claim("a", 0, 10), "b": claim("b", 5, 15),
                 "c": claim("c", 20, 30)}
        self.assertEqual(pc.peak_concurrency(claims), 2)

        # Backwards: shift c to overlap both a and b at once and the peak
        # must rise to 3.
        claims_all_three = {"a": claim("a", 0, 10), "b": claim("b", 5, 15),
                            "c": claim("c", 6, 8)}
        self.assertEqual(pc.peak_concurrency(claims_all_three), 3)

    def test_peak_concurrency_is_none_with_no_usable_timestamps(self):
        self.assertIsNone(pc.peak_concurrency({}))
        self.assertIsNone(pc.peak_concurrency(
            {"a": {"unit_id": "a", "state": "done"}}))


class TheTraceabilityContract(Sandbox):
    """T20: every number on the card names the record it came from."""

    def test_every_field_carries_a_nonempty_source(self):
        run = write_run(self.root, "traced", GREEN_EVENTS, GREEN_CLAIMS,
                        GREEN_ROWS, GREEN_RECEIPT)
        fields = pc.build_card(run)
        for key, entry in fields.items():
            self.assertIn("value", entry)
            self.assertIn("source", entry)
            self.assertTrue(entry["source"].strip(),
                            "%s has no source" % key)

    def test_the_json_cli_output_carries_the_same_fields_and_sources(self):
        run = write_run(self.root, "traced-cli", GREEN_EVENTS, GREEN_CLAIMS,
                        GREEN_ROWS, GREEN_RECEIPT)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = pc.main(["--json", run])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        expected_keys = {key for key, _label in pc.CARD_ORDER}
        self.assertEqual(set(payload.keys()), expected_keys)
        for key in expected_keys:
            self.assertTrue(payload[key]["source"].strip())

    def test_the_text_card_prints_every_labelled_row_once(self):
        """Row-by-row, not a bare substring count: several labels (e.g.
        "NO-DATA" is itself one row's label) also appear as OTHER rows'
        printed values on a run this incomplete, so counting substrings
        across the whole card would double count those on sight."""
        run = write_run(self.root, "traced-text", GREEN_EVENTS, GREEN_CLAIMS,
                        GREEN_ROWS, GREEN_RECEIPT)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = pc.main([run])
        self.assertEqual(code, 0)
        lines = buf.getvalue().splitlines()
        self.assertEqual(lines[0], pc.TITLE)
        self.assertEqual(len(lines) - 1, len(pc.CARD_ORDER))
        for line, (_key, label) in zip(lines[1:], pc.CARD_ORDER):
            self.assertTrue(line.startswith(label),
                            "expected row %r, got %r" % (label, line))


class TheOutFlag(Sandbox):
    def test_out_writes_a_text_and_a_json_projection_and_nothing_else_new(self):
        run = write_run(self.root, "out-flag", GREEN_EVENTS, GREEN_CLAIMS,
                        GREEN_ROWS, GREEN_RECEIPT)
        before = set(os.listdir(run))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = pc.main(["--out", run])
        self.assertEqual(code, 0)
        after = set(os.listdir(run))
        self.assertEqual(after - before, {"proof_card.txt", "proof_card.json"})
        with open(os.path.join(run, "proof_card.json"), encoding="utf-8") as fh:
            written = json.load(fh)
        self.assertEqual(written, pc.build_card(run))


class TestReuseAgreesWithCodexBattery(Sandbox):
    """codex_battery.receipt_proves_change (scripts/codex_battery.py:459)
    is a stricter, multi-file-only gate, not this file's general per-file
    discriminator (see the proof_card.py module docstring). This class
    proves the two agree on the one shape both can judge, and that
    codex_battery's extra "two or more files" requirement is exactly what
    the addendum said it was: a guard scoped to its own battery leg, not
    a general rule this file's per-file labels also need."""

    def test_both_discriminations_agree_on_a_receipt_that_satisfies_both(self):
        entries = [receipt_entry("a.py", "u1", exit_code=0,
                                 check_passed_before=False, state="verified"),
                  receipt_entry("b.py", "u1", exit_code=0,
                                 check_passed_before=False, state="verified")]
        receipt = {"scope": {"changed": entries}}
        self.assertTrue(codex_battery.receipt_proves_change(receipt))
        for entry in entries:
            label, _reason = pc.file_proof_label(entry)
            self.assertEqual(label, "PROVES CHANGE")

    def test_a_single_file_receipt_codex_battery_rejects_still_reads_proved_here(self):
        """The precise case the addendum names: codex_battery.
        receipt_proves_change requires two or more scope.changed entries
        before it will call anything proven, which is a fact about ITS
        gate, not about whether one file's own check discriminated
        before/after. Calling codex_battery here would wrongly print
        NO-DATA for a perfectly good single-file proof."""
        entry = receipt_entry("a.py", "u1", exit_code=0,
                              check_passed_before=False, state="verified")
        receipt = {"scope": {"changed": [entry]}}
        self.assertFalse(codex_battery.receipt_proves_change(receipt))
        label, _reason = pc.file_proof_label(entry)
        self.assertEqual(label, "PROVES CHANGE")


if __name__ == '__main__':
    unittest.main()
