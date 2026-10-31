#!/usr/bin/env python3
"""CARD1: the end card is short by default, counts its egress and guard rows, and names what waits.

WHAT THIS PROVES. It runs the card renderer on fixture receipts and judges the bytes: the default
card is at most 6 lines and 720 bytes plus 120 per parked piece, never one line per changed file,
the old 19 row card survives behind --full, the egress row is COUNTED from a ledger and carries
its label, and an absent ledger reads NO-DATA, never a zero. Nothing here looks a symbol up: a
function that exists and prints a constant fails.

THIS IS CARD1'S FROZEN HARNESS, written before any of its code exists (docs/plan/specs/1.1.9/
CARD1.md). The builder never edits it. Every "red before green" case below fails today with an
AssertionError (a missing function is asserted, never called blind), so the base reads
FAILED with errors=0.

FROZEN SHAPES (EG0 and HD1 are not built, so nothing else in the tree fixes these; the builder
reads them here):
  render_short(fields, parked) -> str      fields is proof_card.build_card(run_dir); parked is a list
                                           of piece ids (str), the shape brother_run already passes
                                           to delivery_conclusion.
  egress_row(ledger_path, since) -> str    ONE line. Ledger = JSONL, one object per line:
                                           {"ts": <epoch seconds, float>, "verdict": "allowed"|"refused"}.
                                           A row counts when ts > since. A row with no verdict is
                                           "unknown"; a line that is not a JSON object is
                                           "unreadable". Output carries "allowed N", "refused M" (a
                                           count and its word, either order) and ends with
                                           "model calls through the gate; other sockets NO-DATA".
                                           Absent or unreadable ledger: "egress: NO-DATA (no ledger)".
  guard_row(ledger_path, since) -> str     Ledger rows: {"ts": <epoch>, "guard": str, "rule": str,
                                           "action": "deny"|"park"}. Output "guards: denied N,
                                           parked M"; a row without a rule is "unknown"; a bad line
                                           is "unreadable"; absent or unreadable ledger is NO-DATA.
  waiting_row(stamps, reviews) -> str      stamps: list of {"id": str, "approve": <the one approval
                                           command>}; reviews: list of {"stamp": <id>, "verdict":
                                           "PASS"|"FAIL"}. Output "waiting on you: K stamps,
                                           <command>"; a stamp with no review row is named with
                                           the word "verdict".

Hermetic: a scratch HOME and temp dirs, nothing survives. Python 3.9 compatible, standard library.

    python3 -B scripts/test_end_card.py Short
    python3 -B scripts/test_end_card.py Egress
    python3 -B scripts/test_end_card.py Waiting
"""
import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LABEL = "model calls through the gate; other sockets NO-DATA"
OLD_CARD_MARKS = ("BROTHER VERIFIED AUTOPILOT", "Top-level oracle", "Peak concurrency",
                  "Sequential est.", "Duplicate work", "Human interrupts")
T0 = 1800000000.0   # the fixtures' "since": rows are placed relative to it
APPROVE = "brother approve night-119-2"


def sh(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=120)


def lines_of(text):
    return [ln for ln in text.splitlines() if ln.strip()]


def counts(text, words):
    """Every reading of `text` in which each of `words` carries an integer, either right after it
    ('refused 3', 'refused: 3') or right before it ('3 refused'); one dict per reading that holds
    for ALL the words. Empty when some word has no number beside it in either style. The harness
    does not fix the builder's sentence, only that a count sits next to its word."""
    found = []
    for style in ("after", "before"):
        got = {}
        for w in words:
            if style == "after":
                m = re.search(r"\b%s\b\s*[:=]?\s*(\d+)\b" % w, text, re.I)
            else:
                m = re.search(r"\b(\d+)\s*[:=]?\s*%s\b" % w, text, re.I)
            if not m:
                break
            got[w] = int(m.group(1))
        else:
            found.append(got)
    return found


def has_count(text, word, n):
    return {word: n} in counts(text, [word])


def good_text(rows):
    return "".join(json.dumps(r) + "\n" for r in rows)


def put(path, text, mode="w"):
    with open(path, mode, encoding=None if "b" in mode else "utf-8") as fh:
        fh.write(text)


class Scratch(unittest.TestCase):
    """A scratch HOME and temp root per case; no case reads this machine's ledgers or ~/.brother."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="end-card-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        patcher = mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def assertCounts(self, text, **expected):
        self.assertIn(expected, counts(text, list(expected)), "wanted %r beside their words in %r" % (expected, text))

    def short(self, fields, parked=()):
        """render_short on `fields`, or an AssertionError (never an error) while the function is absent."""
        import proof_card
        if not hasattr(proof_card, "render_short"):
            self.fail("proof_card.render_short does not exist yet")
        return proof_card.render_short(fields, list(parked))

    def ledger(self, rows=(), raw=None, name="ledger.jsonl"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            if raw is not None:
                fh.write(raw)
            else:
                for r in rows:
                    fh.write((json.dumps(r) + "\n").encode("utf-8"))
        return path

    def run_dir(self, name, units=3, files=None, refused_files=0, receipt="good"):
        """A run directory on disk, in the shape proof_card.build_card reads. `units` pieces, all
        done; `files` changed file names (proved), `refused_files` more whose proof is NO-DATA.
        receipt: "good", "none" (never written) or "garbage" (bytes that are not JSON)."""
        run = os.path.join(self.tmp, name)
        os.makedirs(run)
        ids = ["piece-%02d" % i for i in range(units)]
        rows = [{"id": uid, "files_changed_by_unit": []} for uid in ids]
        claims = {}
        for i, uid in enumerate(ids):
            claims[uid] = {"unit_id": uid, "state": "done", "attempt": 1,
                           "claimed_at": T0 + i * 10, "released_at": T0 + i * 10 + 9,
                           "evidence": {"check_command": "python3 -c pass", "exit_code": 0}}
        put(os.path.join(run, "claims" + ".json"), json.dumps(claims))
        put(os.path.join(run, "W-%s.json" % name), json.dumps({"rows": rows}))
        if receipt == "none":
            return run
        os.makedirs(os.path.join(run, "receipt"))
        path = os.path.join(run, "receipt", "receipt.json")
        if receipt == "garbage":
            put(path, b"\xff\xfe{ this is not json", "wb")
            return run
        owner = ids[0] if ids else "u"
        changed = [{"file": f, "unit": owner, "check_command": "x", "exit_code": 0,
                    "output_location": "receipt/x.log", "check_passed_before": False,
                    "state": "verified", "reason": ""} for f in (files or [])]
        changed += [{"file": "zz_refused_%02d.py" % i, "unit": owner, "check_command": "x",
                     "exit_code": 1, "output_location": "receipt/x.log",
                     "check_passed_before": False, "state": "refused",
                     "reason": "check did not run to a verdict"} for i in range(refused_files)]
        put(path, json.dumps({"scope": {"changed": changed}}))
        return run

    def fields(self, run):
        import proof_card
        return proof_card.build_card(run)


# =====================================================================================
# CARD1.a  Short
# =====================================================================================

class Short(Scratch):
    """REQ-CARD1A-01 to 03. Mutations: M-CARD1A-LONG-DEFAULT, M-CARD1A-PER-FILE."""

    def drive(self, extra=()):
        """(stdout, run_dir). One real one piece drain through brother_run.main that changes three
        files, the loop stubbed (the seam test_brother_run.py's proof card class uses). An option
        main does not know is a FAILED assertion, not an error."""
        import brother_run as br
        import claim_store
        import loop_bridge
        import work_record as WR
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "a@b.c"],
                     ["config", "user.name", "t"]):
            sh(["git"] + args, repo)
        put(os.path.join(repo, "base.txt"), "base\n")
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-q", "-m", "R0"], repo)
        run_dir = tempfile.mkdtemp(prefix="run-", dir=self.tmp)
        names = ["f1.txt", "f2.txt", "f3.txt"]
        rec, problems = WR.create("one plain piece of work",
                                  [{"id": "A1", "title": "create three files", "done_check": "true",
                                    "owns": names}], store=run_dir)
        self.assertEqual(problems, [])

        def loop(plan_path, claims_path, cwd, slots):
            for n in names:
                put(os.path.join(repo, n), n + "\n")
            sh(["git", "add", "-A"], repo)
            sh(["git", "commit", "-q", "-m", "A1"], repo)
            claim_store.acquire(claims_path, "A1", "t")
            claim_store.release(claims_path, "A1", "t", state="done",
                                evidence={"check_command": "true", "exit_code": 0, "output": "ok",
                                          "output_truncated": False, "canonical_rev": br._head(repo),
                                          "files_changed": names})
            return 0, "A1 done scope=CLEAN integrated=True"

        env = {"BROTHER_MACHINE_RESERVATION_PATH": os.path.join(self.tmp, "reservation.json"),
               "BROTHER_LOAD_REFUSALS_LOG": os.path.join(self.tmp, "refusals.jsonl"),
               "BROTHER_JEV_SEAMS_OFF": "1", "BROTHER_JEV_STATE_DIR": os.path.join(self.tmp, "jev")}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env))
            stack.enter_context(mock.patch.object(br, "MACHINE_RESERVATION_PATH",
                                                  env["BROTHER_MACHINE_RESERVATION_PATH"]))
            stack.enter_context(mock.patch.object(br, "run_loop", loop))
            stack.enter_context(mock.patch.object(loop_bridge, "LOAD_REFUSALS_LOG",
                                                  env["BROTHER_LOAD_REFUSALS_LOG"]))
            stack.enter_context(mock.patch.object(loop_bridge.resource_gate, "read",
                                                  lambda *a, **k: {"disk_free_gib": 500.0}))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            try:
                br.main(["ignored", "--resume", run_dir, "--cwd", repo] + list(extra))
            except SystemExit as exc:
                self.fail("brother_run refused its arguments %r (exit %s): %s"
                          % (list(extra), exc.code, err.getvalue()[-300:]))
        return out.getvalue(), run_dir

    # ---- REQ-CARD1A-01 ----

    def test_the_default_card_is_short(self):
        """The default end card of a real three file run is render_short's output and none of the
        19 row card. Kills M-CARD1A-LONG-DEFAULT (brother_run prints render_text by default)."""
        import proof_card
        if not hasattr(proof_card, "render_short"):
            self.fail("proof_card.render_short does not exist yet")
        printed, run_dir = self.drive()
        for mark in OLD_CARD_MARKS:
            self.assertNotIn(mark, printed, "the default card still carries the 19 row card (%r)" % mark)
        want = proof_card.render_short(proof_card.build_card(run_dir), [])
        self.assertTrue(want.strip(), "render_short returned nothing for a finished run")
        self.assertIn(want, printed, "the default card is not render_short's output")

    def test_the_short_card_fits_the_budget(self):
        """At most 6 lines and 720 bytes with nothing parked. The fixture has pieces but no changed
        files so that only the size of the card, never a per file loop, is under test."""
        run = self.run_dir("budget", units=3, files=[])
        text = self.short(self.fields(run), [])
        self.assertLessEqual(len(lines_of(text)), 6, text)
        self.assertLessEqual(len(text.encode("utf-8")), 720, text)

    # ---- REQ-CARD1A-02 ----

    def test_no_line_per_file(self):
        """40 changed files print exactly as many lines as 3, and no file name. Kills
        M-CARD1A-PER-FILE (render_short prints one line per file)."""
        three = self.run_dir("three", units=3, files=["zz_file_%02d.py" % i for i in range(3)])
        forty = self.run_dir("forty", units=3, files=["zz_file_%02d.py" % i for i in range(40)])
        a = self.short(self.fields(three), [])
        b = self.short(self.fields(forty), [])
        self.assertEqual(len(lines_of(a)), len(lines_of(b)),
                         "the card grew with the files:\n%s\n---\n%s" % (a, b))
        self.assertNotIn("zz_file_", b, "a changed file's name was printed")

    # ---- REQ-CARD1A-03 ----

    def test_full_keeps_the_old_card(self):
        """--full prints render_text's 19 row card, byte for byte."""
        import proof_card
        printed, run_dir = self.drive(["--full"])
        old = proof_card.render_text(proof_card.build_card(run_dir))
        self.assertIn(old, printed, "--full did not print the old card unchanged")

    # ---- the edges the spec names ----

    def test_a_run_with_no_pieces_prints_the_result_and_the_receipt_path(self):
        run = self.run_dir("empty", units=0, files=[], receipt="good")
        text = self.short(self.fields(run), [])
        self.assertTrue(lines_of(text), "an empty run printed nothing")
        self.assertLessEqual(len(lines_of(text)), 6, text)
        self.assertIn(os.path.join(run, "receipt", "receipt.json"), text)

    def test_many_pieces_list_only_the_parked_ones(self):
        """40 pieces, 3 parked: each parked piece is one line, the other 37 are not named, and the
        card stays inside 720 bytes plus 120 per parked piece."""
        run = self.run_dir("many", units=40, files=[])
        parked = ["piece-07", "piece-19", "piece-33"]
        text = self.short(self.fields(run), parked)
        for pid in parked:
            self.assertEqual(sum(1 for ln in text.splitlines() if pid in ln), 1,
                             "%s is not on exactly one line:\n%s" % (pid, text))
        for i in range(40):
            pid = "piece-%02d" % i
            if pid not in parked:
                self.assertNotIn(pid, text, "an unparked piece was listed")
        self.assertLessEqual(len(lines_of(text)), 6 + len(parked), text)
        self.assertLessEqual(len(text.encode("utf-8")), 720 + 120 * len(parked), text)

    def test_a_parked_line_is_clipped_to_120_bytes(self):
        """A parked piece whose name is 400 bytes of multi byte text still fits 120 bytes on its line."""
        run = self.run_dir("clip", units=3, files=[])
        text = self.short(self.fields(run), ["PARKEDX-" + "é" * 200])
        mine = [ln for ln in text.splitlines() if "PARKEDX-" in ln]
        self.assertEqual(len(mine), 1, text)
        self.assertLessEqual(len(mine[0].encode("utf-8")), 120, mine[0])

    def test_the_counts_line_carries_proven_and_no_data(self):
        """2 proved files and 1 whose proof is NO-DATA: the 2 sits beside 'proven', and a NO-DATA
        count of 1 is on a line of its own words."""
        run = self.run_dir("counts", units=3, files=["zz_a.py", "zz_b.py"], refused_files=1)
        text = self.short(self.fields(run), [])
        self.assertTrue(any(has_count(ln, "proven", 2) for ln in text.splitlines()), text)
        self.assertTrue(any("NO-DATA" in ln and re.search(r"\b1\b", ln) for ln in text.splitlines()), text)

    def test_a_field_that_is_not_text_prints_as_no_data(self):
        """A None, a number and a list in a field print as NO-DATA: no 'None', no raise."""
        run = self.run_dir("hostile", units=3, files=["zz_a.py", "zz_b.py"])
        good = self.fields(run)
        base = self.short(good, [])
        for bad in (None, 7, ["x"]):
            fields = copy.deepcopy(good)
            fields["proven"]["value"] = bad
            text = self.short(fields, [])
            self.assertNotIn("None", text, "value %r leaked into the card:\n%s" % (bad, text))
            self.assertGreater(text.count("NO-DATA"), base.count("NO-DATA"),
                               "value %r was not printed as NO-DATA:\n%s" % (bad, text))

    def test_an_unreadable_receipt_prints_the_receipt_line_with_the_reason(self):
        run = self.run_dir("garbage", units=3, files=[], receipt="garbage")
        text = self.short(self.fields(run), [])
        line = [ln for ln in text.splitlines() if ln.strip().upper().startswith("RECEIPT")]
        self.assertTrue(line, "no RECEIPT line:\n%s" % text)
        self.assertRegex(line[0], r"(?i)NO-DATA|unreadable|not valid|cannot|could not|corrupt|parse")

    def test_a_run_that_ended_early_prints_what_it_has(self):
        """No receipt at all: the card still renders, inside its size, and says NO-DATA."""
        run = self.run_dir("early", units=3, files=[], receipt="none")
        text = self.short(self.fields(run), [])
        self.assertTrue(lines_of(text))
        self.assertLessEqual(len(lines_of(text)), 6, text)
        self.assertIn("NO-DATA", text)

    def test_rendering_twice_is_the_same_and_leaves_the_fields_alone(self):
        run = self.run_dir("twice", units=3, files=["zz_a.py"])
        fields = self.fields(run)
        before = copy.deepcopy(fields)
        first = self.short(fields, ["piece-01"])
        second = self.short(fields, ["piece-01"])
        self.assertEqual(first, second)
        self.assertEqual(fields, before, "render_short changed the fields it was given")


# =====================================================================================
# CARD1.b  Egress
# =====================================================================================

def ledger_rows(allowed=0, refused=0, ts=None):
    ts = T0 + 100 if ts is None else ts
    rows = [{"ts": ts + i, "verdict": "allowed"} for i in range(allowed)]
    rows += [{"ts": ts + 1000 + i, "verdict": "refused"} for i in range(refused)]
    return rows


class Egress(Scratch):
    """REQ-CARD1B-01 to 03. Mutations: M-CARD1B-CONST-ZERO, M-CARD1B-NO-LABEL, M-CARD1B-ABSENT-ZERO."""

    def row(self, path, since=T0):
        import proof_card
        if not hasattr(proof_card, "egress_row"):
            self.fail("proof_card.egress_row does not exist yet")
        text = proof_card.egress_row(path, since)
        self.assertIsInstance(text, str)
        self.assertNotIn("\n", text.strip(), "egress_row must be one line: %r" % text)
        return text

    # ---- REQ-CARD1B-01 ----

    def test_refused_is_counted(self):
        """7 allowed and 3 refused rows print 7 and 3; a second ledger prints its own. Kills
        M-CARD1B-CONST-ZERO (a constant '0 refused')."""
        text = self.row(self.ledger(ledger_rows(allowed=7, refused=3)))
        self.assertCounts(text, allowed=7, refused=3)
        other = self.row(self.ledger(ledger_rows(allowed=2, refused=11), name="second.jsonl"))
        self.assertCounts(other, allowed=2, refused=11)

    def test_rows_before_since_are_not_counted(self):
        old = [{"ts": T0 - 500 + i, "verdict": "refused"} for i in range(4)]
        text = self.row(self.ledger(old + ledger_rows(allowed=2, refused=1)))
        self.assertCounts(text, allowed=2, refused=1)

    def test_ten_thousand_rows_are_counted_in_one_pass(self):
        text = self.row(self.ledger(ledger_rows(allowed=7500, refused=2500)))
        self.assertCounts(text, allowed=7500, refused=2500)

    def test_an_empty_ledger_is_a_real_zero_not_no_data(self):
        text = self.row(self.ledger([]))
        self.assertCounts(text, allowed=0, refused=0)
        self.assertNotIn("no ledger", text)

    def test_a_row_without_a_verdict_is_counted_unknown(self):
        text = self.row(self.ledger(ledger_rows(allowed=2, refused=1) + [{"ts": T0 + 50}]))
        self.assertCounts(text, unknown=1)
        self.assertCounts(text, allowed=2, refused=1)

    def test_a_line_that_is_not_json_is_counted_unreadable_not_dropped(self):
        raw = (good_text(ledger_rows(allowed=2, refused=1)) + "this is not json\n").encode("utf-8")
        text = self.row(self.ledger(raw=raw))
        self.assertCounts(text, unreadable=1)
        self.assertCounts(text, allowed=2, refused=1)

    def test_a_torn_last_line_is_unreadable(self):
        raw = (good_text(ledger_rows(allowed=2, refused=1)) + '{"ts": %s, "verd' % (T0 + 9)).encode("utf-8")
        text = self.row(self.ledger(raw=raw))
        self.assertCounts(text, unreadable=1)
        self.assertCounts(text, allowed=2, refused=1)

    def test_bytes_that_are_not_text_are_unreadable_not_a_crash(self):
        raw = good_text(ledger_rows(allowed=1, refused=1)).encode("utf-8") + b"\xff\xfe\x00\n"
        text = self.row(self.ledger(raw=raw))
        self.assertCounts(text, unreadable=1)

    # ---- REQ-CARD1B-02 ----

    def test_no_ledger_is_no_data(self):
        """An absent ledger prints the NO-DATA row, never a zero. Kills M-CARD1B-ABSENT-ZERO."""
        text = self.row(os.path.join(self.tmp, "does-not-exist.jsonl"))
        self.assertIn("egress: NO-DATA (no ledger)", text)
        self.assertNotRegex(text, r"(?i)\b0\s*refused|refused\D{0,3}\b0\b")

    def test_an_unreadable_ledger_is_no_data_not_a_zero(self):
        """A directory where the ledger should be cannot be read: NO-DATA, not 'allowed 0'."""
        path = os.path.join(self.tmp, "a-directory-not-a-ledger")
        os.makedirs(path)
        text = self.row(path)
        self.assertIn("NO-DATA", text)
        self.assertNotRegex(text, r"(?i)\b0\s*refused|refused\D{0,3}\b0\b")

    # ---- REQ-CARD1B-03 ----

    def test_the_label_is_printed(self):
        """A real ledger's line ends with the label, full or empty. Kills M-CARD1B-NO-LABEL."""
        for rows in (ledger_rows(allowed=4, refused=2), []):
            text = self.row(self.ledger(rows, name="l%d.jsonl" % len(rows)))
            self.assertTrue(text.rstrip().endswith(LABEL), "no label at the end of %r" % text)

    # ---- the short card carries the row ----

    def test_the_short_card_carries_an_egress_row(self):
        """Under a scratch HOME no ledger exists, so the short card's egress line is the NO-DATA row."""
        run = self.run_dir("egress-card", units=3, files=[])
        text = self.short(self.fields(run), [])
        self.assertIn("egress:", text)
        self.assertIn("NO-DATA (no ledger)", text)


# =====================================================================================
# CARD1.c  Waiting
# =====================================================================================

def guard_rows(deny=0, park=0, ts=None):
    ts = T0 + 100 if ts is None else ts
    rows = [{"ts": ts + i, "guard": "g1", "rule": "r%d" % i, "action": "deny"} for i in range(deny)]
    rows += [{"ts": ts + 1000 + i, "guard": "g2", "rule": "p%d" % i, "action": "park"} for i in range(park)]
    return rows


class Waiting(Scratch):
    """REQ-CARD1C-01 to 03. Mutations: M-CARD1C-CONST-DENY, M-CARD1C-HIDE-WAIT."""

    def grow(self, path, since=T0):
        import proof_card
        if not hasattr(proof_card, "guard_row"):
            self.fail("proof_card.guard_row does not exist yet")
        text = proof_card.guard_row(path, since)
        self.assertIsInstance(text, str)
        self.assertNotIn("\n", text.strip(), "guard_row must be one line: %r" % text)
        return text

    def wait(self, stamps, reviews):
        import proof_card
        if not hasattr(proof_card, "waiting_row"):
            self.fail("proof_card.waiting_row does not exist yet")
        text = proof_card.waiting_row(stamps, reviews)
        self.assertIsInstance(text, str)
        return text

    # ---- REQ-CARD1C-01 ----

    def test_denials_are_counted(self):
        """5 denies and 2 parks print 5 and 2; a second ledger prints its own. Kills
        M-CARD1C-CONST-DENY (a constant)."""
        text = self.grow(self.ledger(guard_rows(deny=5, park=2)))
        self.assertCounts(text, denied=5, parked=2)
        other = self.grow(self.ledger(guard_rows(deny=1, park=6), name="second.jsonl"))
        self.assertCounts(other, denied=1, parked=6)

    def test_no_denial_is_a_real_zero(self):
        text = self.grow(self.ledger([]))
        self.assertCounts(text, denied=0, parked=0)
        self.assertNotIn("no ledger", text)

    def test_guard_rows_before_since_are_not_counted(self):
        old = [{"ts": T0 - 9, "guard": "g", "rule": "r", "action": "deny"}] * 4
        text = self.grow(self.ledger(old + guard_rows(deny=2, park=1)))
        self.assertCounts(text, denied=2, parked=1)

    def test_ten_thousand_guard_rows_are_counted_in_one_pass(self):
        text = self.grow(self.ledger(guard_rows(deny=6000, park=4000)))
        self.assertCounts(text, denied=6000, parked=4000)

    def test_a_row_without_a_rule_is_counted_unknown(self):
        rows = guard_rows(deny=2, park=1) + [{"ts": T0 + 50, "guard": "g", "action": "deny"}]
        text = self.grow(self.ledger(rows))
        self.assertCounts(text, unknown=1)

    def test_a_bad_guard_line_is_counted_unreadable_not_dropped(self):
        raw = (good_text(guard_rows(deny=2, park=1)) + "not json at all\n").encode("utf-8")
        text = self.grow(self.ledger(raw=raw))
        self.assertCounts(text, unreadable=1)
        self.assertCounts(text, denied=2, parked=1)

    def test_a_torn_last_guard_line_is_unreadable(self):
        raw = (good_text(guard_rows(deny=2, park=1)) + '{"ts": %s, "gua' % (T0 + 9)).encode("utf-8")
        text = self.grow(self.ledger(raw=raw))
        self.assertCounts(text, unreadable=1)

    # ---- REQ-CARD1C-03 ----

    def test_absent_guard_ledger_is_no_data(self):
        text = self.grow(os.path.join(self.tmp, "no-such-guard-ledger.jsonl"))
        self.assertIn("NO-DATA", text)
        self.assertNotRegex(text, r"(?i)denied\D{0,3}\b0\b|\b0\s*denied")

    def test_an_unreadable_guard_ledger_is_no_data_not_a_zero(self):
        path = os.path.join(self.tmp, "a-directory-not-a-ledger")
        os.makedirs(path)
        text = self.grow(path)
        self.assertIn("NO-DATA", text)
        self.assertNotRegex(text, r"(?i)denied\D{0,3}\b0\b|\b0\s*denied")

    # ---- REQ-CARD1C-02 ----

    def test_pending_stamps_are_shown(self):
        """Two stamps wait: the row says 2 beside 'stamps' and carries the approval command.
        Kills M-CARD1C-HIDE-WAIT (an empty row)."""
        stamps = [{"id": "S-1", "approve": APPROVE}, {"id": "S-2", "approve": APPROVE}]
        reviews = [{"stamp": "S-1", "verdict": "PASS"}, {"stamp": "S-2", "verdict": "PASS"}]
        text = self.wait(stamps, reviews)
        self.assertTrue(text.strip(), "waiting_row returned nothing for two pending stamps")
        self.assertCounts(text, stamps=2)
        self.assertIn(APPROVE, text)
        self.assertTrue(text.startswith("waiting on you"), text)

    def test_a_stamp_with_no_verdict_is_named(self):
        """S-2 has no review row: the row names S-2 and says 'verdict'. With its verdict present
        the word is gone, so it is the missing verdict that carries it."""
        stamps = [{"id": "S-1", "approve": APPROVE}, {"id": "S-2", "approve": APPROVE}]
        text = self.wait(stamps, [{"stamp": "S-1", "verdict": "PASS"}])
        self.assertIn("S-2", text)
        self.assertRegex(text, r"(?i)verdict")
        both = self.wait(stamps, [{"stamp": "S-1", "verdict": "PASS"}, {"stamp": "S-2", "verdict": "PASS"}])
        self.assertNotRegex(both, r"(?i)verdict")


def main(argv):
    names = [a for a in argv if a != "--selftest"]
    unittest.main(module=sys.modules[__name__], argv=[sys.argv[0]] + names, verbosity=1)


if __name__ == "__main__":
    main(sys.argv[1:])
