"""One reader for the Claude call ledger, and the identity that pairs its rows (review 2026-09-26, findings 14 and 29).

model_call.py writes a START row per Claude call and a separate DONE row carrying the cost. Three readers counted them
three ways: the driver summed any numeric cost (a NaN or negative figure included) and cancelled starts against done rows
by COUNT, so an unrelated completion hid an uncosted call and a done row with cost null read as costed; claude_window
counted every row as a call. scripts/loop/claude_ledger.py is the one reader:
  - a call is a START row; a DONE row pairs with its start only through the shared call id;
  - cost evidence is a finite, non negative number that is not a bool; anything else leaves the call uncosted;
  - a start with no id cannot be paired, so it stays uncosted (historical rows are NO-DATA, never zero);
  - a ledger that cannot be read is NO-DATA; a missing ledger is a measured empty one; a torn line is counted.
Run from the repository root: python3 -B scripts/test_claude_ledger.py
"""
import datetime, json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP = os.path.join(ROOT, "scripts", "loop")
sys.path.insert(0, LOOP)
import claude_ledger as CL  # noqa: E402

T = "2099-01-01T00:00:00"


def ledger(rows, raw=""):
    d = tempfile.mkdtemp(prefix="claude-ledger-")
    p = os.path.join(d, "claude-calls.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        f.write(raw)
    return p


def start(cid=None, at=T):
    r = {"at": at, "model": "claude-sonnet-5", "effort": "high", "prompt_chars": 10}
    if cid is not None:
        r["call"] = cid
    return r


def done(cost, cid=None, at=T):
    r = {"at": at, "event": "done", "model": "claude-sonnet-5", "cost_usd": cost}
    if cid is not None:
        r["call"] = cid
    return r


class Tally(unittest.TestCase):
    def test_a_missing_ledger_is_a_measured_empty_one(self):
        t = CL.tally(os.path.join(tempfile.mkdtemp(), "none.jsonl"), since="2000-01-01T00:00:00")
        self.assertEqual((t["calls"], t["usd"], t["uncosted"], t["error"]), (0, 0.0, 0, None))
        self.assertEqual(CL.note(t), "")

    def test_a_paired_call_with_a_cost_is_costed(self):
        t = CL.tally(ledger([start("a"), done(0.5, "a")]), since="2000-01-01T00:00:00")
        self.assertEqual((t["calls"], round(t["usd"], 4), t["uncosted"]), (1, 0.5, 0))

    def test_a_done_row_with_a_null_cost_leaves_the_call_uncosted(self):
        t = CL.tally(ledger([start("a"), done(None, "a")]), since="2000-01-01T00:00:00")
        self.assertEqual((t["usd"], t["uncosted"]), (0.0, 1))
        self.assertIn("NO-DATA", CL.note(t))

    def test_nonfinite_bool_negative_and_text_costs_are_not_evidence(self):
        rows = []
        for i, c in enumerate((float("nan"), True, -1.0, "0.3", float("inf"))):
            rows += [start("c%d" % i), done(c, "c%d" % i)]
        p = ledger([]); open(p, "w").write("".join(json.dumps(r, allow_nan=True) + "\n" for r in rows))
        t = CL.tally(p, since="2000-01-01T00:00:00")
        self.assertEqual((t["usd"], t["uncosted"]), (0.0, 5))

    def test_an_unrelated_completion_never_cancels_an_uncosted_call(self):
        t = CL.tally(ledger([start("a"), done(0.2, "b")]), since="2000-01-01T00:00:00")
        self.assertEqual((round(t["usd"], 4), t["uncosted"]), (0.2, 1))

    def test_rows_without_identity_cannot_pair(self):
        t = CL.tally(ledger([start(), done(0.4)]), since="2000-01-01T00:00:00")
        self.assertEqual((round(t["usd"], 4), t["uncosted"]), (0.4, 1))
        self.assertIn("no call id", CL.note(t))

    def test_an_unreadable_ledger_is_no_data(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "claude-calls.jsonl"); os.makedirs(p)
        t = CL.tally(p, since="2000-01-01T00:00:00")
        self.assertIsNotNone(t["error"])
        self.assertIn("could not be read", CL.note(t))

    def test_a_torn_line_is_counted_not_skipped(self):
        t = CL.tally(ledger([start("a"), done(0.1, "a")], raw='{"at": "2099-01-01T00:0'), since="2000-01-01T00:00:00")
        self.assertEqual(t["unreadable_rows"], 1)
        self.assertIn("unreadable", CL.note(t))

    def test_rows_before_the_run_are_outside_it(self):
        t = CL.tally(ledger([start("a", at="2000-01-01T00:00:00"), done(0.3, "a", at="2000-01-01T00:00:01")]), since="2050-01-01T00:00:00")
        self.assertEqual((t["calls"], t["usd"], t["uncosted"]), (0, 0.0, 0))

    def test_the_cli_prints_the_money_and_the_note(self):
        p = ledger([start("a"), done(None, "a")])
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "claude_ledger.py"), "tally", p, "2000-01-01T00:00:00"],
                           capture_output=True, text=True, timeout=60)
        lines = r.stdout.splitlines()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(lines[0], "USD 0.0000")
        self.assertTrue(lines[1].startswith("NOTE ") and "NO-DATA" in lines[1])


class OrdinaryRunIdentity(unittest.TestCase):
    """2026-10-04: outside a proof phase every Claude row was untagged (run_identity answers None there by contract), so
    a native session's START read as an uncosted call in its own run's tally. claude_ledger.start now tags an ordinary
    run's rows from the run's own start record; proof admission is untouched."""
    def run_dir(self, run_id=None, attempt="a" * 32, phase="NO-DATA: no proof phase", body=None):
        root = tempfile.mkdtemp(prefix="ordrun-")
        d = os.path.join(root, "run-20261004-000000-1")
        os.makedirs(os.path.join(d, "proof"))
        rec = body if body is not None else json.dumps({"schema": "loop-proof-start-v1", "run_id": run_id or os.path.basename(d),
                                                         "attempt_id": attempt, "phase": phase})
        open(os.path.join(d, "proof", "start.json"), "w").write(rec)
        return d, os.path.join(root, "claude-calls.jsonl")

    def call(self, path, env, cid):
        started = CL.start(path, {"model": "m", "call": cid}, 600, env=env)
        done = {"event": "done", "model": "m", "call": cid, "cost_usd": 0.25, "outcome": "exit 0"}
        done.update((k, started[k]) for k in ("run", "attempt") if k in started)   # model_call._claude_run's own copy
        CL.finish(path, done)
        return started

    def env(self, d):
        return {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_PROOF")} | {"BROTHER_RUN_DIR": d}

    def test_start_and_done_carry_the_runs_identity_and_the_run_tally_counts_the_cost(self):
        d, path = self.run_dir()
        st = self.call(path, self.env(d), "c1")
        self.assertEqual((st["run"], st["attempt"]), (os.path.basename(d), "a" * 32))
        t = CL.tally(path, run_id=os.path.basename(d))
        self.assertEqual((t["usd"], t["untagged"], t["uncosted"]), (0.25, 0, 0))

    def test_a_call_outside_every_run_stays_untagged(self):
        d, path = self.run_dir()
        env = {k: v for k, v in self.env(d).items() if k != "BROTHER_RUN_DIR"}
        self.assertNotIn("run", self.call(path, env, "c2"))

    def test_a_mismatched_or_corrupt_start_record_is_refused_and_writes_nothing(self):
        for kw in ({"run_id": "run-other"}, {"body": "{not json"}, {"attempt": "short"}, {"phase": "RB"}):
            d, path = self.run_dir(**kw)
            with self.assertRaises(ValueError, msg=repr(kw)):
                CL.start(path, {"model": "m", "call": "c3"}, 600, env=self.env(d))
            self.assertFalse(os.path.exists(path) and open(path).read().strip(), kw)

    def test_a_tagged_call_still_in_flight_is_pending_not_uncosted(self):
        d, path = self.run_dir()
        CL.start(path, {"model": "m", "call": "c4"}, 600, env=self.env(d))
        t = CL.tally(path, run_id=os.path.basename(d))
        self.assertEqual((t["inflight"], t["uncosted"], t["untagged"]), (1, 0, 0))

    def test_a_proof_phase_never_reads_the_ordinary_identity(self):
        import proof_ledger
        d, _ = self.run_dir()
        self.assertIsNone(proof_ledger.ordinary_run_identity({"BROTHER_RUN_DIR": d, "BROTHER_PROOF_PHASE": "RB"}))


class Integrity(unittest.TestCase):
    """CC11 candidate review, 2026-09-26: three fixtures broke the first reader, and the lifecycle suite a fourth."""
    def test_a_huge_integer_cost_is_no_data_not_a_crash(self):
        p = ledger([]); open(p, "w").write(json.dumps(start("a")) + "\n" + '{"at": "%s", "event": "done", "call": "a", "cost_usd": %s}\n' % (T, "1" + "0" * 1000))
        t = CL.tally(p, since="2000-01-01T00:00:00")
        self.assertEqual((t["usd"], t["uncosted"]), (0.0, 1))
        self.assertIn("NO-DATA", CL.note(t))

    def test_a_lone_done_row_with_a_null_cost_is_no_data(self):
        t = CL.tally(ledger([done(None, "a")]), since="2000-01-01T00:00:00")
        self.assertEqual(t["usd"], 0.0)
        self.assertIn("NO-DATA", CL.note(t))
        self.assertIn("no usable cost", CL.note(t))

    def test_an_overflowing_total_is_no_data(self):
        t = CL.tally(ledger([start("a"), done(1e308, "a"), start("b"), done(1e308, "b")]), since="2000-01-01T00:00:00")
        self.assertIsNone(t["usd"], "an infinite total is not a measurement")
        self.assertIn("not a finite number", CL.note(t))

    def test_a_row_carrying_a_cost_is_money_whatever_its_event(self):
        """Known money never drops out: a row with a cost and no event field (the lifecycle suite's fixture) is spend."""
        t = CL.tally(ledger([{"at": T, "model": "claude-sonnet-5", "cost_usd": 0.5}]), since="2000-01-01T00:00:00")
        self.assertEqual((round(t["usd"], 4), t["calls"]), (0.5, 0))


@unittest.skipUnless(os.path.isfile(os.path.join(ROOT, "plugin", "runtime", "brother", "core", "dispatch_semaphore.py")),
                     "the plugin runtime is not shipped here, so model_call refuses every call (test_model_call_no_plugin covers that)")
class Writer(unittest.TestCase):
    def test_model_call_writes_one_call_id_on_both_rows(self):
        import model_call as MC
        d = tempfile.mkdtemp(prefix="mc-ledger-")
        led = os.path.join(d, "claude-calls.jsonl")
        reg = {"k": {"id": "claude-x", "transport": "claude", "privacy": "private", "kinds": {"build"}, "cost": 1, "quality": {"build": 5}}}
        answer = json.dumps({"result": "ok", "is_error": False, "total_cost_usd": 0.01, "usage": {"input_tokens": 1, "output_tokens": 1}})
        env = {"BROTHER_CLAUDE_CALLS_LEDGER": led, "BROTHER_OR_STATE_ROOT": os.path.join(d, "state"), "HOME": d}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            MC.call_one("k", "hello", "build", "private", timeout=30, reg=reg, runner=lambda argv, stdin, timeout: {"returncode": 0, "stdout": answer, "stderr": ""})
        finally:
            for k, v in old.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        rows = [json.loads(l) for l in open(led, encoding="utf-8") if l.strip()]
        self.assertEqual(len(rows), 2, rows)
        ids = {r.get("call") for r in rows}
        self.assertEqual(len(ids), 1, rows)
        self.assertTrue(ids.pop(), "the call id is missing")


class ClaudeWindowCalls(unittest.TestCase):
    def test_claude_window_counts_calls_not_rows(self):
        """claude_window --calls counted every ledger row as a call, so each costed call counted twice."""
        home = tempfile.mkdtemp(prefix="cw-home-")
        os.makedirs(os.path.join(home, ".claude", "evidence")); os.makedirs(os.path.join(home, ".claude", "projects"))
        with open(os.path.join(home, ".claude", "model-registry.json"), "w") as f:
            json.dump({"models": {"sonnet": {"id": "claude-sonnet-5"}}}, f)
        with open(os.path.join(home, ".claude", "evidence", "claude-calls.jsonl"), "w") as f:
            f.write(json.dumps(start("a", at="2030-01-01T10:00:00")) + "\n" + json.dumps(done(0.1, "a", at="2030-01-01T10:00:05")) + "\n")
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "abc", "claude_window.py"), "2030-01-01 09:00", "2030-01-01 11:00", "--calls"],
                           capture_output=True, text=True, timeout=60, env=dict(os.environ, HOME=home))
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        calls = json.loads(r.stdout)["calls"]["by_model"]["claude-sonnet-5"]
        self.assertEqual(calls["logged_calls"], 1, calls)


class Window(unittest.TestCase):
    """D-22 (reproduced 2026-09-26): the driver's window start is aware UTC ('...+00:00') while model_call writes
    local time with no offset, and the text comparison counted up to nine hours of earlier spend on this +0900
    machine. The window compares instants; a naive row is the writer's local time; a row or a window start that
    cannot be placed on the clock once is NO-DATA. Each case runs the CLI with its own pinned TZ."""

    def cli(self, rows, since, tz):
        p = ledger(rows)
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "claude_ledger.py"), "tally", p, since],
                           env=dict(os.environ, TZ=tz), capture_output=True, text=True, timeout=60)
        return r.returncode, dict(line.split(" ", 1) for line in r.stdout.splitlines() if " " in line)

    def test_spend_before_an_aware_utc_start_is_outside_the_window(self):
        # 16:47 JST is 07:47 UTC, two hours before the 09:47 UTC start.
        rc, out = self.cli([start("a", at="2026-09-26T16:47:13"), done(5.0, "a", at="2026-09-26T16:47:20")],
                           "2026-09-26T09:47:13.881040+00:00", "Asia/Tokyo")
        self.assertEqual(out.get("USD"), "0.0000", out)

    def test_spend_after_an_aware_utc_start_is_inside_the_window(self):
        rc, out = self.cli([start("a", at="2026-09-26T18:50:00"), done(1.0, "a", at="2026-09-26T18:50:05")],
                           "2026-09-26T09:47:13.881040+00:00", "Asia/Tokyo")
        self.assertEqual(out.get("USD"), "1.0000", out)

    def test_an_aware_row_is_compared_as_an_instant(self):
        rc, out = self.cli([done(2.0, at="2026-09-26T18:50:00+09:00")], "2026-09-26T09:47:13+00:00", "UTC")
        self.assertEqual(out.get("USD"), "2.0000", out)

    def test_a_row_whose_stamp_is_not_a_time_is_unreadable_not_money(self):
        rc, out = self.cli([done(3.0, at="yesterday")], "2026-09-26T09:47:13+00:00", "Asia/Tokyo")
        self.assertEqual(out.get("USD"), "0.0000", out)
        self.assertIn("1 unreadable ledger row", out.get("NOTE", ""), out)

    def test_a_local_time_the_clock_shows_twice_is_unreadable(self):
        # 01:30 on 2026-11-01 happens twice in New York (the clocks fall back at 02:00 EDT).
        rc, out = self.cli([done(4.0, at="2026-11-01T01:30:00")], "2026-11-01T00:00:00+00:00", "America/New_York")
        self.assertEqual(out.get("USD"), "0.0000", out)
        self.assertIn("unreadable", out.get("NOTE", ""), out)

    def test_a_local_time_the_clock_skips_is_unreadable(self):
        # 02:30 on 2026-03-08 never happens in New York (the clocks spring forward at 02:00 EST).
        rc, out = self.cli([done(4.0, at="2026-03-08T02:30:00")], "2026-03-08T00:00:00+00:00", "America/New_York")
        self.assertEqual(out.get("USD"), "0.0000", out)
        self.assertIn("unreadable", out.get("NOTE", ""), out)

    def test_a_window_start_that_is_not_a_time_is_no_data(self):
        rc, out = self.cli([done(1.0, at="2026-09-26T18:50:00")], "not-a-time", "Asia/Tokyo")
        self.assertTrue(out.get("NOTE", "").startswith("NO-DATA"), out)
        self.assertNotEqual(out.get("USD"), "1.0000", out)



def utc(seconds_from_now=0.0):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds_from_now)).isoformat()


def tagged(row, run="RB", attempt="a" * 32):
    return dict(row, run=run, attempt=attempt)


class Pending(unittest.TestCase):
    """M2-2 (U7, objection 12): a Claude call inside its own timeout is PENDING, not unknown (Q2 default); past its
    expires_at, or with none, it is uncosted (NO-DATA). A run tally counts only its own tagged rows. One condition each."""

    def test_a_call_before_its_expiry_is_pending_not_unknown(self):
        t = CL.tally(ledger([dict(start("a", at=utc()), expires_at=utc(600))]), since="2000-01-01T00:00:00")
        self.assertEqual((t["calls"], t["inflight"], t["uncosted"]), (1, 1, 0))
        self.assertNotIn("NO-DATA", CL.note(t))
        self.assertIn("1 pending", CL.note(t))

    def test_a_call_past_its_expiry_is_uncosted(self):
        t = CL.tally(ledger([dict(start("a", at=utc(-900)), expires_at=utc(-1))]), since="2000-01-01T00:00:00")
        self.assertEqual((t["inflight"], t["uncosted"]), (0, 1))
        self.assertIn("NO-DATA", CL.note(t))

    def test_the_expiry_is_judged_at_the_given_now(self):
        p = ledger([dict(start("a", at="2030-01-01T00:00:00+00:00"), expires_at="2030-01-01T00:10:00+00:00")])
        before = CL.tally(p, now="2030-01-01T00:05:00+00:00")
        after = CL.tally(p, now="2030-01-01T00:10:01+00:00")
        self.assertEqual((before["inflight"], before["uncosted"]), (1, 0))
        self.assertEqual((after["inflight"], after["uncosted"]), (0, 1))

    def test_a_legacy_start_without_an_expiry_is_uncosted(self):
        t = CL.tally(ledger([start("a", at=utc())]), since="2000-01-01T00:00:00")
        self.assertEqual((t["inflight"], t["uncosted"]), (0, 1))
        self.assertIn("NO-DATA", CL.note(t))

    def test_an_expiry_that_is_not_a_time_is_uncosted(self):
        t = CL.tally(ledger([dict(start("a", at=utc()), expires_at="soon")]), since="2000-01-01T00:00:00")
        self.assertEqual((t["inflight"], t["uncosted"]), (0, 1))

    def test_a_finished_call_with_no_cost_is_uncosted_even_before_its_expiry(self):
        t = CL.tally(ledger([dict(start("a", at=utc()), expires_at=utc(600)), done(None, "a", at=utc())]), since="2000-01-01T00:00:00")
        self.assertEqual((t["inflight"], t["uncosted"]), (0, 1))

    def test_a_costed_call_is_neither_pending_nor_uncosted(self):
        t = CL.tally(ledger([dict(start("a", at=utc()), expires_at=utc(600)), done(0.3, "a", at=utc())]), since="2000-01-01T00:00:00")
        self.assertEqual((t["inflight"], t["uncosted"], round(t["usd"], 4)), (0, 0, 0.3))
        self.assertEqual(CL.note(t), "")

    def test_another_runs_rows_are_excluded_by_run_id(self):
        rows = [tagged(start("a", at=utc())), tagged(done(0.5, "a", at=utc())),
                tagged(start("b", at=utc()), run="RC"), tagged(done(2.0, "b", at=utc()), run="RC")]
        t = CL.tally(ledger(rows), run_id="RB")
        self.assertEqual((t["calls"], round(t["usd"], 4), t["uncosted"]), (1, 0.5, 0))
        self.assertEqual(CL.note(t), "")

    def test_an_untagged_start_in_a_run_tally_is_uncosted(self):
        t = CL.tally(ledger([start("a", at=utc()), done(0.5, "a", at=utc())]), run_id="RB")
        self.assertGreaterEqual(t["uncosted"], 1)
        self.assertIn("NO-DATA", CL.note(t))

    def test_an_untagged_done_row_in_a_run_tally_is_not_this_runs_money(self):
        # the run's own call is fully costed, so only the untagged row can make this tally NO-DATA (one guard per fixture)
        t = CL.tally(ledger([tagged(start("a", at=utc())), tagged(done(0.5, "a", at=utc())), done(0.7, "z", at=utc())]), run_id="RB")
        self.assertEqual((round(t["usd"], 4), t["uncosted"], t["untagged"]), (0.5, 0, 1))
        self.assertIn("NO-DATA", CL.note(t))
        self.assertIn("no run tag", CL.note(t))

    def test_rows_after_until_are_outside_the_window(self):
        t = CL.tally(ledger([start("a", at="2030-01-01T00:00:00+00:00"), done(0.4, "a", at="2030-01-01T00:00:01+00:00"),
                             start("b", at="2030-01-01T02:00:00+00:00")]), until="2030-01-01T01:00:00+00:00")
        self.assertEqual((t["calls"], round(t["usd"], 4), t["uncosted"]), (1, 0.4, 0))

    def test_an_until_that_is_not_a_time_is_no_data(self):
        t = CL.tally(ledger([start("a", at=utc())]), until="later")
        self.assertIsNotNone(t["error"])
        self.assertIn("NO-DATA", CL.note(t))

    def test_the_cli_prints_a_pending_call_without_no_data(self):
        p = ledger([dict(start("a", at=utc()), expires_at=utc(600))])
        r = subprocess.run([sys.executable, "-B", os.path.join(LOOP, "claude_ledger.py"), "tally", p, "2000-01-01T00:00:00+00:00"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "USD 0.0000")
        self.assertNotIn("NO-DATA", r.stdout)
        self.assertIn("pending", r.stdout)


class StartFinish(unittest.TestCase):
    """The writer (U7): start registers before provider contact, under the ledger lock mark_ending also takes, with an
    aware UTC stamp, an expiry of timeout + 90 s and, in a proof phase, the run's tags; finish writes the terminal."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="cl-writer-")
        self.path = os.path.join(self.d, "sub", "claude-calls.jsonl")   # the folder does not exist yet

    def rows(self):
        return [json.loads(l) for l in open(self.path, encoding="utf-8") if l.strip()]

    def test_start_writes_an_aware_utc_row_with_its_expiry(self):
        row = CL.start(self.path, {"model": "claude-x", "call": "c1"}, 300, env={})
        at, exp = CL.instant(row["at"]), CL.instant(row["expires_at"])
        self.assertIsNotNone(at.tzinfo)
        self.assertEqual(at.utcoffset(), datetime.timedelta(0))
        self.assertIn(".", row["at"], "the stamp carries microseconds")
        self.assertEqual((exp - at).total_seconds(), 390.0)
        self.assertEqual(self.rows(), [row])
        self.assertNotIn("run", row)

    def test_finish_writes_one_terminal_row(self):
        CL.start(self.path, {"call": "c1"}, 300, env={})
        CL.finish(self.path, {"event": "done", "call": "c1", "cost_usd": 0.1, "outcome": "exit 0"})
        t = CL.tally(self.path, since="2000-01-01T00:00:00+00:00")
        self.assertEqual((t["calls"], round(t["usd"], 4), t["uncosted"], t["inflight"]), (1, 0.1, 0, 0))
        self.assertIsNotNone(CL.instant(self.rows()[1]["at"]).tzinfo)

    def test_start_on_an_unwritable_ledger_raises(self):
        os.makedirs(os.path.dirname(self.path)); open(self.path, "w").close(); os.chmod(self.path, 0o444)
        self.addCleanup(os.chmod, self.path, 0o644)
        with self.assertRaises(OSError):
            CL.start(self.path, {"call": "c1"}, 300, env={})

    def test_start_refuses_a_timeout_that_is_not_a_number(self):
        for bad in (None, "300", float("nan"), True, -1):
            with self.subTest(timeout=bad):
                with self.assertRaises(ValueError):
                    CL.start(self.path, {"call": "c1"}, bad, env={})
        self.assertFalse(os.path.exists(self.path))

    def test_an_fsync_failure_refuses_the_start(self):
        """START must be durable before provider contact: a row the disk did not accept is not a registration."""
        from unittest import mock
        with mock.patch.object(CL.os, "fsync", side_effect=OSError("disk said no")):
            with self.assertRaises(OSError):
                CL.start(self.path, {"call": "c1"}, 300, env={})

    def test_finish_waits_for_the_ledger_lock(self):
        """settle and the end snapshot read under this lock, so a terminal row must not land while they hold it."""
        import fcntl, threading
        os.makedirs(os.path.dirname(self.path))
        lock = open(os.path.splitext(self.path)[0] + ".lock", "a")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        done = threading.Event()
        t = threading.Thread(target=lambda: (CL.finish(self.path, {"call": "c1", "cost_usd": 0}), done.set()))
        t.start()
        self.assertFalse(done.wait(1.0), "finish wrote while another process held the ledger lock")
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN); lock.close()
        self.assertTrue(done.wait(10.0))
        t.join(10)

    def test_the_selftest_verdict_follows_its_cases(self):
        from unittest import mock
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(CL.selftest(), 0)
            with mock.patch.object(CL, "valid_cost", lambda c: True):
                self.assertEqual(CL.selftest(), 1, "a failing selftest case must fail the selftest")

    def test_start_waits_for_the_ledger_lock(self):
        """mark_ending holds this lock while it creates the ending marker, so a start must wait for it."""
        import fcntl, threading
        os.makedirs(os.path.dirname(self.path))
        lock = open(os.path.splitext(self.path)[0] + ".lock", "a")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        done = threading.Event()
        t = threading.Thread(target=lambda: (CL.start(self.path, {"call": "c1"}, 300, env={}), done.set()))
        t.start()
        self.assertFalse(done.wait(1.0), "start wrote while another process held the ledger lock")
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN); lock.close()
        self.assertTrue(done.wait(10.0))
        t.join(10)


class Identity(unittest.TestCase):
    """Finding 5 (Codex audit 2026-09-27): a call id names ONE call, yet two DONE rows for one id added its cost twice and
    one DONE closed two STARTs sharing an id, both with an empty note. Two STARTs for one id, two terminals for one id,
    or a terminal whose START is nowhere in the ledger cannot be counted exactly once: NO-DATA. The census is the WHOLE
    ledger, whatever the window or run of the tally. One condition per fixture: every other row is a paired, costed call."""

    def test_two_starts_sharing_an_id_are_no_data(self):
        t = CL.tally(ledger([start("a"), start("a"), done(0.5, "a")]), since="2000-01-01T00:00:00")
        self.assertIn("NO-DATA", CL.note(t))
        self.assertIn("more than one START", CL.note(t))
        self.assertEqual((t["duplicate_starts"], t["duplicate_done"], t["orphan_done"]), (1, 0, 0))

    def test_two_terminals_for_one_call_are_no_data(self):
        t = CL.tally(ledger([start("a"), done(0.5, "a"), done(0.5, "a")]), since="2000-01-01T00:00:00")
        self.assertIn("NO-DATA", CL.note(t))
        self.assertIn("more than one terminal", CL.note(t))
        self.assertEqual((t["duplicate_starts"], t["duplicate_done"], t["orphan_done"]), (0, 1, 0))

    def test_a_terminal_with_no_start_is_no_data(self):
        t = CL.tally(ledger([start("a"), done(0.5, "a"), done(0.2, "b")]), since="2000-01-01T00:00:00")
        self.assertIn("NO-DATA", CL.note(t))
        self.assertIn("no START", CL.note(t))
        self.assertEqual((t["duplicate_starts"], t["duplicate_done"], t["orphan_done"]), (0, 0, 1))

    def test_a_fault_before_the_window_still_counts(self):
        rows = [start("a", at="2000-01-01T00:00:00"), done(0.5, "a", at="2000-01-01T00:00:01"), done(0.5, "a", at="2000-01-01T00:00:02")]
        t = CL.tally(ledger(rows), since="2050-01-01T00:00:00")
        self.assertIn("NO-DATA", CL.note(t))

    def test_a_fault_in_another_run_still_counts_in_a_run_tally(self):
        rows = [tagged(start("a", at=utc())), tagged(done(0.5, "a", at=utc())),
                tagged(start("b", at=utc()), run="RC"), tagged(start("b", at=utc()), run="RC"), tagged(done(0.1, "b", at=utc()), run="RC")]
        t = CL.tally(ledger(rows), run_id="RB")
        self.assertIn("NO-DATA", CL.note(t))

    def test_a_start_before_the_window_pairs_its_terminal_inside_it(self):
        """A call that began before the run and ended inside it is not an orphan: the census reads the whole ledger."""
        t = CL.tally(ledger([start("a", at="2000-01-01T00:00:00"), done(0.5, "a")]), since="2050-01-01T00:00:00")
        self.assertEqual((round(t["usd"], 4), t["orphan_done"]), (0.5, 0))
        self.assertEqual(CL.note(t), "")

    def test_legacy_rows_without_an_id_are_money_never_an_identity_fault(self):
        t = CL.tally(ledger([done(0.4), done(0.4)]), since="2000-01-01T00:00:00")
        self.assertEqual((round(t["usd"], 4), t["duplicate_done"], t["orphan_done"]), (0.8, 0, 0))
        self.assertEqual(CL.note(t), "")


class StartReadsTheLedger(unittest.TestCase):
    """Finding 4 (Codex audit 2026-09-27): start() appended under the lock without reading the ledger, so a ledger that
    already read NO-DATA admitted another paid call before any polling guard reacted. Under the same lock start now
    reads the whole ledger and refuses, writing nothing, when it cannot be read or does not read as a ledger: a line
    that is not a row, an incomplete last row, an identity fault. Unknown MONEY is not corruption: a pending, uncosted
    or legacy id-less call is the driver's to judge (U7), and the real ledger holds 1852 of them. One condition each."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="cl-guard-")
        self.path = os.path.join(self.d, "claude-calls.jsonl")

    def write(self, rows, raw=""):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows) + raw)

    def assertRefused(self, exc):
        before = open(self.path, "rb").read()
        with self.assertRaises(exc):
            CL.start(self.path, {"call": "new"}, 300, env={})
        self.assertEqual(open(self.path, "rb").read(), before, "a refused start wrote to the ledger")

    def test_a_line_that_is_not_a_row_refuses(self):
        self.write([start("a"), done(0.1, "a")], raw="{torn ledger row\n")
        self.assertRefused(ValueError)

    def test_an_incomplete_last_row_refuses(self):
        """The last row parses but lacks its newline: an append would weld the new START onto it."""
        self.write([start("a"), done(0.1, "a")], raw=json.dumps(start("b")))
        self.assertRefused(ValueError)

    def test_a_duplicate_start_elsewhere_refuses(self):
        self.write([start("a"), start("a"), done(0.1, "a")])
        self.assertRefused(ValueError)

    def test_a_duplicate_terminal_elsewhere_refuses(self):
        self.write([start("a"), done(0.1, "a"), done(0.1, "a")])
        self.assertRefused(ValueError)

    def test_a_terminal_with_no_start_elsewhere_refuses(self):
        self.write([start("a"), done(0.1, "a"), done(0.1, "b")])
        self.assertRefused(ValueError)

    def test_a_ledger_that_cannot_be_read_refuses_and_creates_nothing(self):
        gone = os.path.join(self.d, "gone.jsonl")
        os.symlink(gone, self.path)
        with self.assertRaises(OSError):
            CL.start(self.path, {"call": "new"}, 300, env={})
        self.assertFalse(os.path.exists(gone), "a dangling ledger link was recreated as an empty, clean ledger")

    def test_unknown_money_is_not_corruption(self):
        """A legacy id-less START, a pending call and a null-cost terminal: the ledger reads NO-DATA, and start admits."""
        self.write([start(), dict(start("p", at=utc()), expires_at=utc(600)), start("u"), done(None, "u")])
        self.assertIn("NO-DATA", CL.note(CL.tally(self.path)))
        CL.start(self.path, {"call": "new"}, 300, env={})
        self.assertEqual(json.loads(open(self.path).read().splitlines()[-1])["call"], "new")


class OneRowPerIdentity(unittest.TestCase):
    """Finding 5, the writers: start refuses a second START for an id and finish a second terminal for it, each with
    FileExistsError (an OSError: model_call treats it as a row it could not write), writing nothing; a row with no
    call id could never be paired and is refused with ValueError. finish still closes a call in a damaged ledger."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="cl-ident-")
        self.path = os.path.join(self.d, "claude-calls.jsonl")

    def rows(self):
        return [json.loads(l) for l in open(self.path, encoding="utf-8") if l.strip()]

    def test_a_second_start_for_one_id_is_refused(self):
        CL.start(self.path, {"call": "c1"}, 300, env={})
        with self.assertRaises(FileExistsError):
            CL.start(self.path, {"call": "c1"}, 300, env={})
        self.assertEqual(len(self.rows()), 1)

    def test_a_start_without_a_call_id_is_refused(self):
        for row in ({}, {"call": ""}, {"call": 7}):
            with self.subTest(row=row):
                with self.assertRaises(ValueError):
                    CL.start(self.path, row, 300, env={})
        self.assertFalse(os.path.exists(self.path))

    def test_a_second_terminal_for_one_id_is_refused(self):
        CL.start(self.path, {"call": "c1"}, 300, env={})
        CL.finish(self.path, {"call": "c1", "cost_usd": 0.5})
        with self.assertRaises(FileExistsError):
            CL.finish(self.path, {"call": "c1", "cost_usd": 0.5})
        t = CL.tally(self.path)
        self.assertEqual((len(self.rows()), round(t["usd"], 4), CL.note(t)), (2, 0.5, ""))

    def test_a_terminal_without_a_call_id_is_refused(self):
        CL.start(self.path, {"call": "c1"}, 300, env={})
        with self.assertRaises(ValueError):
            CL.finish(self.path, {"cost_usd": 0.1})
        self.assertEqual(len(self.rows()), 1)

    def test_finish_on_a_ledger_that_cannot_be_read_creates_nothing(self):
        gone = os.path.join(self.d, "gone.jsonl")
        os.symlink(gone, self.path)
        with self.assertRaises(OSError):
            CL.finish(self.path, {"call": "c1", "cost_usd": 0.1})
        self.assertFalse(os.path.exists(gone))

    def test_finish_still_closes_a_call_in_a_ledger_with_a_bad_row(self):
        """A call admitted before the damage must still be closed: its terminal is the only record of its cost."""
        CL.start(self.path, {"call": "c1"}, 300, env={})
        with open(self.path, "a") as f:
            f.write("{torn ledger row\n")
        CL.finish(self.path, {"call": "c1", "cost_usd": 0.1})
        last = json.loads(open(self.path, encoding="utf-8").read().splitlines()[-1])
        self.assertEqual((last["call"], last["cost_usd"]), ("c1", 0.1))


if __name__ == "__main__":
    unittest.main(verbosity=1)
