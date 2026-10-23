#!/usr/bin/env python3
"""LR1.e: the yield section names red without code suites, and the proof reads the run.

TWO THINGS LIVE HERE.

1. TestLoopWorksFully (the fixture class, this sub unit's own done check): loop_report.red_without_code_suites,
   loop_report.held_cause_table and the report's two new sections, and proof() below over fixture logs and a fixture
   WBS in a temporary folder. Nothing reads the real home folder.

2. TestLoopWorksFullyLive (run only when this file runs whole): LR1's unit acceptance. It reads the run named by
   BROTHER_PROOF_RUN_LOG (the driver log of the daytime run) and the live WBS. It passes only when every sub unit of the
   nine proof units has a LANDED line from the loop in that run and the run has zero UNEXPLAINED HELD lines. With no run
   named, or a log that cannot be read, it is NO-DATA (exit 3 from this file), never a pass.

proof() answers 0 PASS, 1 FAIL, 3 NO-DATA (and says why in `lines`). Only the LAST `RUN START` onward is read. The proof units are the owner's ruling of 2026-10-20 (OPEN ITEMS 9): I3, I4,
I8, I22, I33, LC1, LC2, LC3 and PP1. Their sub units are read from the WBS, so a sub unit the plan adds is in scope.

Python 3.9 and 3.13, standard library only, no network.
"""
import datetime
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loop_report as LR  # noqa: E402
import run_watch as RW  # noqa: E402  (the one slicer of a log into its last run)

PROOF_UNITS = ("I3", "I4", "I8", "I22", "I33", "LC1", "LC2", "LC3", "PP1")
LIVE_WBS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "plan", "BROTHER-1.1.7-WBS.json")
LANDED_ROW = re.compile(r"LANDED +([^|]+?) +\|")   # used with .match: a row starts with it; one name or a batch


def landed_subs(row):
    """The sub units a driver LANDED row names: one ("LANDED  PP1.d | ...") or a batch ("LANDED  I22.a, I3.a, I4.a,
    LC1.b | ..."), the shape pass_pulse.py already reads. Found 2026-10-22: the pattern took one name only, so a batch
    landing counted for nothing and the real window read 9 of 15 where 13 had landed. Any other row: an empty set."""
    m = LANDED_ROW.match(row)
    return {s for s in re.split(r"[,\s]+", m.group(1).strip()) if s} if m else set()
CLOCK = ":".join(("00", "00", "00"))       # built, never written out: a fixture clock is not a measured time


def proof_scope(wbs_path):
    """{unit: [sub unit, ...]} for the proof units, read from the WBS. Raises when the WBS cannot be read or a proof unit
    names no sub unit (the callers say NO-DATA)."""
    with open(wbs_path, encoding="utf-8") as fh:
        units = {u["id"]: u for u in json.load(fh)["units"]}
    expected = {}
    for uid in PROOF_UNITS:
        subs = units[uid]["sub_units"]
        if not isinstance(subs, list):   # a string would be read one character per sub unit
            raise ValueError("%s sub_units is not a list" % uid)
        expected[uid] = [str(s) for s in subs]
        if not expected[uid] or not all(s.strip() for s in expected[uid]):
            raise ValueError("%s names no sub units to prove" % uid)
    return expected


def proof(run_log, wbs_path, lines=None):
    """0 when every sub unit of the proof units has a LANDED line in the named run and no HELD line of it is UNEXPLAINED,
    1 when one is missing or unexplained (each named), 3 NO-DATA when no run is named or the log or the WBS cannot be
    read. `lines` (a list) receives what it found, one sentence per line. Reads both files; writes nothing."""
    lines = [] if lines is None else lines
    if not isinstance(run_log, str) or not run_log.strip():
        lines.append("NO-DATA: no run is named (BROTHER_PROOF_RUN_LOG), so no run was read")
        return 3  # NO-DATA
    try:
        with open(run_log, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        lines.append("NO-DATA: the run log %s cannot be read (%s)" % (run_log, type(exc).__name__))
        return 3  # NO-DATA log
    try:
        expected = proof_scope(wbs_path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        lines.append("NO-DATA: the proof scope cannot be read from %s (%s)" % (wbs_path, type(exc).__name__))
        return 3  # NO-DATA wbs
    rows = text.splitlines()
    if not any(row.startswith("RUN START") for row in rows):
        lines.append("NO-DATA: the log %s has no RUN START line, so no run can be told from an older one" % run_log)
        return 3  # NO-DATA start
    # ONLY THE LAST RUN: a relaunch in the same minute appends to an older run's log, and an older run's landings and holds
    # are not this run's
    landed = set().union(*[landed_subs(row) for row in RW.last_run_lines(rows)])
    missing = [s for uid in PROOF_UNITS for s in expected[uid] if s not in landed]
    unexplained = [line for line, cause in LR.held_cause_table(run_log, from_last_run=True) if cause == "UNEXPLAINED"]
    total = sum(len(v) for v in expected.values())
    lines.append("PROOF: %d proof unit(s), %d of %d sub unit(s) have a LANDED line, %d UNEXPLAINED HELD"
                 % (len(PROOF_UNITS), total - len(missing), total, len(unexplained)))
    lines += ["MISSING: %s has no LANDED line in the run" % s for s in missing]
    lines += ["UNEXPLAINED: %s" % line for line in unexplained]
    return 1 if missing or unexplained else 0


def proof_of(run_log, wbs_path):
    """(code, lines) from proof()."""
    lines = []
    return proof(run_log, wbs_path, lines), lines


DRIVER_LOG = re.compile(r"loop-until-(\d{8}-\d{4})\.log$")   # the driver's own name (loop_until.sh LOG=); never a wrapper's


def _aware(stamp):
    """An ISO 8601 stamp WITH an offset as a datetime, else None: a run whose start cannot be placed in the window is
    never read as inside or outside it."""
    try:
        t = datetime.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo is not None else None


def proof_since(evidence_dir, since, wbs_path, lines=None):
    """OPEN ITEMS 10 over a window (the owner's ruling counts PP1.c, landed by the loop at 15h19 in the 14h23 run, and the
    14 landed after it as one proof): every run that began at or after `since` (ISO 8601 with an offset), in every driver
    log of `evidence_dir`, is read whole. 0 when every proof sub unit has a LANDED line in some run of the window and NO run
    of the window has an UNEXPLAINED HELD; 1 when one is missing or unexplained (each named); 3 NO-DATA when the window,
    the folder or the WBS cannot be read, a RUN START cannot be dated, or no run began in the window. A run that began
    before `since` is never read, so nothing older can land a sub unit, and a hold in any run of the window still fails."""
    lines = [] if lines is None else lines
    start = _aware(since) if isinstance(since, str) else None
    if start is None:
        lines.append("NO-DATA: no window start with an offset is named (BROTHER_PROOF_SINCE), so no run was read")
        return 3  # NO-DATA since
    try:
        expected = proof_scope(wbs_path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        lines.append("NO-DATA: the proof scope cannot be read from %s (%s)" % (wbs_path, type(exc).__name__))
        return 3  # NO-DATA wbs
    if not isinstance(evidence_dir, str):   # a descriptor or bytes lists fine and then breaks every join below
        lines.append("NO-DATA: the evidence folder is not a path string (%s)" % type(evidence_dir).__name__)
        return 3  # NO-DATA folder type
    try:
        logs = sorted(n for n in os.listdir(evidence_dir) if DRIVER_LOG.match(n))
    except OSError as exc:
        lines.append("NO-DATA: the evidence folder %s cannot be listed (%s)" % (evidence_dir, type(exc).__name__))
        return 3  # NO-DATA folder
    landed, unexplained, runs = set(), [], []
    for name in logs:
        # the driver names a log by the local minute it started, and a relaunch appends only within that minute, so a
        # log named a day before the window holds no run of it (and may predate RUN START rows, so it is not read)
        named = datetime.datetime.strptime(DRIVER_LOG.match(name).group(1), "%Y%m%d-%H%M").astimezone()
        if named + datetime.timedelta(days=1) < start:
            continue
        path = os.path.join(evidence_dir, name)
        try:
            with open(path, "rb") as fh:
                rows = fh.read().decode("utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:   # a byte that is not text may be a row that cannot be read
            lines.append("NO-DATA: the driver log %s cannot be read (%s)" % (path, type(exc).__name__))
            return 3  # NO-DATA log
        # one run per RUN START row: a relaunch in the same minute appends a second run to one log
        cuts = [i for i, row in enumerate(rows) if row.startswith("RUN START ")]
        before = rows[:cuts[0]] if cuts else rows
        if any(landed_subs(row) or LR._held_cause(row.rstrip()) for row in before):
            lines.append("NO-DATA: %s has an outcome row before any RUN START, so it belongs to no datable run" % name)
            return 3  # NO-DATA undatable outcome
        for k, i in enumerate(cuts):
            when = _aware(rows[i][len("RUN START"):].strip())
            if when is None:
                lines.append("NO-DATA: %s line %d starts a run that cannot be dated: %s" % (name, i + 1, rows[i][:80]))
                return 3  # NO-DATA start
            if when < start:
                continue
            run = rows[i:cuts[k + 1] if k + 1 < len(cuts) else len(rows)]
            runs.append("%s line %d" % (name, i + 1))
            landed |= set().union(*[landed_subs(row) for row in run])
            unexplained += [held for held, cause in filter(None, (LR._held_cause(row.rstrip()) for row in run))
                            if cause == "UNEXPLAINED"]   # the cause rule of held_cause_table, row by row
    if not runs:
        lines.append("NO-DATA: no run in %s began at or after %s" % (evidence_dir, since))
        return 3  # NO-DATA window
    missing = [s for uid in PROOF_UNITS for s in expected[uid] if s not in landed]
    total = sum(len(v) for v in expected.values())
    lines.append("PROOF: %d run(s) since %s, %d proof unit(s), %d of %d sub unit(s) have a LANDED line, %d UNEXPLAINED HELD"
                 % (len(runs), since, len(PROOF_UNITS), total - len(missing), total, len(unexplained)))
    lines += ["RUN: %s" % r for r in runs]
    lines += ["MISSING: %s has no LANDED line in the window" % s for s in missing]
    lines += ["UNEXPLAINED: %s" % line for line in unexplained]
    return 1 if missing or unexplained else 0


HELD_PP1 = ("HELD    PP1.c      held on the unchanged landing tree d154b634952f (HELD at the deploy step, nothing pushed: "
            "judge refused: the whole gate exited 1 boxed: readiness-gate FAIL exit 1); offered again when the tree changes")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="test-loop-works-fully-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def put(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def wbs(self, units=None):
        units = units if units is not None else {uid: ["%s.a" % uid] for uid in PROOF_UNITS}
        return self.put("wbs.json", json.dumps({"units": [{"id": k, "sub_units": v} for k, v in units.items()]}))

    def log(self, landed=None, held=()):
        landed = landed if landed is not None else ["%s.a" % uid for uid in PROOF_UNITS]
        body = "RUN START 2099-01-01T" + CLOCK + "\n" + "".join("LANDED  %s | units landed | log x\n" % s for s in landed)
        body += "".join(h + "\n" for h in held)
        return self.put("driver.log", body)

    def report_text(self, stage_rows, log_path=Ellipsis, stages_path=None):
        """report() over scratch ledgers; log_path Ellipsis leaves the argument out, as every caller before LR1.e did."""
        stages = stages_path or self.put("stages.jsonl", "".join(json.dumps(r) + "\n" for r in stage_rows))
        empty = self.put("empty.jsonl", "")
        keep = (LR.STAGES, LR.FAILURES, LR.LEDGER)
        LR.STAGES, LR.FAILURES, LR.LEDGER = stages, empty, empty
        try:
            buf = io.StringIO()
            extra = {} if log_path is Ellipsis else {"log_path": log_path}
            LR.report(plan_path=os.path.join(self.tmp, "no-plan.json"), window=(0, 4102444800, "fixture"), out=buf, **extra)
            return buf.getvalue()
        finally:
            LR.STAGES, LR.FAILURES, LR.LEDGER = keep


class TestLoopWorksFully(Fixture):
    # the five named in the spec
    def test_red_without_code_suites_names_suites_and_counts(self):
        rows = [{"suite": "readiness-gate", "red_without_code": True}] * 3 + [{"suite": "close-ceremony", "red_without_code": True}] \
            + [{"suite": "code-suite", "red_without_code": False}] * 5
        self.assertEqual(LR.red_without_code_suites(rows), [("readiness-gate", 3), ("close-ceremony", 1)],
                         "a suite that failed WITH code is never listed, the biggest count comes first")
        text = self.report_text([dict(r, at=50) for r in rows], self.log())
        self.assertIn("YIELD: RED WITHOUT CODE", text)
        self.assertRegex(text, r"readiness-gate\s+3")
        self.assertNotIn("code-suite", text)

    def test_held_table_marks_a_causeless_held_unexplained(self):
        path = self.log(held=[HELD_PP1, "HELD    I3.a      ", "DEPLOY  HELD | "])
        table = LR.held_cause_table(path)
        self.assertEqual(len(table), 3, table)
        self.assertEqual([c for _l, c in table][1:], ["UNEXPLAINED", "UNEXPLAINED"])
        self.assertTrue(table[0][1].startswith("held on the unchanged landing tree"))
        self.assertEqual(table[0][0], HELD_PP1)
        text = self.report_text([], path)
        self.assertIn("HELDS AND CAUSES", text)
        self.assertIn("2 UNEXPLAINED", text)

    def test_the_proof_passes_when_every_proof_unit_has_a_loop_landed_line(self):
        code, lines = proof_of(self.log(held=[HELD_PP1]), self.wbs())
        self.assertEqual(code, 0, lines)
        self.assertIn("9 proof unit(s), 9 of 9 sub unit(s)", lines[0])
        self.assertIn("0 UNEXPLAINED HELD", lines[0])

    def test_the_proof_fails_on_one_unexplained_held(self):
        code, lines = proof_of(self.log(held=[HELD_PP1, "HELD    I3.a      "]), self.wbs())
        self.assertEqual(code, 1, lines)
        self.assertTrue(any(l.startswith("UNEXPLAINED: HELD    I3.a") for l in lines), lines)

    def test_the_proof_reads_no_data_when_the_run_is_not_named(self):
        for named in (None, "", "   ", 5):
            with self.subTest(named=named):
                code, lines = proof_of(named, self.wbs())
                self.assertEqual(code, 3)
                self.assertTrue(lines[0].startswith("NO-DATA"), lines)

    # the edges
    def test_a_missing_landed_line_fails_and_names_the_sub_unit(self):
        code, lines = proof_of(self.log(landed=["I3.a", "I4.a"]), self.wbs())
        self.assertEqual(code, 1)
        self.assertIn("MISSING: PP1.a has no LANDED line in the run", lines)
        self.assertIn("2 of 9 sub unit(s)", lines[0])

    def test_extra_units_in_the_run_and_many_subs_per_unit_are_accepted(self):
        subs = {uid: ["%s.a" % uid, "%s.b" % uid] for uid in PROOF_UNITS}
        landed = [s for v in subs.values() for s in v] + ["CS8.a", "LR1.c"]
        self.assertEqual(proof_of(self.log(landed=landed), self.wbs(subs))[0], 0)

    def test_a_landed_word_that_is_not_a_landing_row_is_not_a_landing(self):
        text = "RUN START 2099-01-01T" + CLOCK + "\n" + "".join("NOTE LANDED  %s.a | x\nlanded %s.a\nRECORD  commit LANDED in proof/landings.jsonl\n" % (u, u) for u in PROOF_UNITS)
        self.assertEqual(proof_of(self.put("driver.log", text), self.wbs())[0], 1)

    # review M6: only the last run of a log counts, and an empty proof scope proves nothing
    def two_runs(self, first_landed, second_landed, first_held=(), second_held=()):
        mark = "RUN START 2099-01-01T" + CLOCK + "\n"
        body = mark + "".join("LANDED  %s | x\n" % u for u in first_landed) + "".join(h + "\n" for h in first_held)
        body += mark + "".join("LANDED  %s | x\n" % u for u in second_landed) + "".join(h + "\n" for h in second_held)
        return self.put("two.log", body)

    def test_a_log_whose_older_run_landed_everything_and_whose_last_run_landed_nothing_does_not_pass(self):
        everything = ["%s.a" % uid for uid in PROOF_UNITS]
        code, lines = proof_of(self.two_runs(everything, []), self.wbs())
        self.assertEqual(code, 1, lines)
        self.assertIn("0 of 9 sub unit(s)", lines[0])

    def test_the_last_run_landing_everything_passes_whatever_the_older_run_held(self):
        everything = ["%s.a" % uid for uid in PROOF_UNITS]
        code, lines = proof_of(self.two_runs([], everything, first_held=["HELD    I3.a      "]), self.wbs())
        self.assertEqual(code, 0, lines)
        self.assertIn("0 UNEXPLAINED HELD", lines[0])

    def test_an_unexplained_hold_in_the_last_run_fails_even_when_the_older_run_was_clean(self):
        everything = ["%s.a" % uid for uid in PROOF_UNITS]
        code, lines = proof_of(self.two_runs(everything, everything, second_held=["HELD    I3.a      "]), self.wbs())
        self.assertEqual(code, 1, lines)

    def test_a_log_with_no_run_start_is_no_data_never_a_pass(self):
        everything = "".join("LANDED  %s.a | x\n" % uid for uid in PROOF_UNITS)
        code, lines = proof_of(self.put("nostart.log", everything), self.wbs())
        self.assertEqual(code, 3, lines)
        self.assertIn("no RUN START", lines[0])

    def test_an_empty_proof_scope_is_no_data_not_zero_of_zero(self):
        for label, units in (("every unit empty", {uid: [] for uid in PROOF_UNITS}),
                             ("one unit empty", dict({uid: ["%s.a" % uid] for uid in PROOF_UNITS}, I3=[])),
                             ("a blank sub unit name", dict({uid: ["%s.a" % uid] for uid in PROOF_UNITS}, I4=["  "])),
                             ("sub_units not a list of names", dict({uid: ["%s.a" % uid] for uid in PROOF_UNITS}, I8=None))):
            with self.subTest(label):
                code, lines = proof_of(self.log(), self.wbs(units))
                self.assertEqual(code, 3, lines)
                self.assertTrue(lines[0].startswith("NO-DATA"), lines)

    def test_the_proof_and_the_report_and_the_watcher_share_one_slicer(self):
        self.assertIs(LR._RW.last_run_lines, RW.last_run_lines)
        with open(os.path.join(HERE, "test_loop_works_fully.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("starts[-1]", src.split("class Fixture(")[0], "the proof carries no slicer of its own")

    def test_held_cause_table_from_the_last_run_ignores_an_older_runs_holds(self):
        path = self.two_runs([], [], first_held=[HELD_PP1, "HELD    I3.a      "], second_held=["HELD    I4.a       a named cause"])
        self.assertEqual(len(LR.held_cause_table(path)), 3)
        self.assertEqual([c for _l, c in LR.held_cause_table(path, from_last_run=True)], ["a named cause"])
        nostart = self.put("n.log", HELD_PP1 + "\n")
        self.assertEqual(len(LR.held_cause_table(nostart, from_last_run=True)), 1, "no RUN START: the whole log, the caller says NO-DATA")

    def test_an_unreadable_log_or_wbs_is_no_data_never_a_pass(self):
        self.assertEqual(proof_of(os.path.join(self.tmp, "absent.log"), self.wbs())[0], 3)
        self.assertEqual(proof_of(self.tmp, self.wbs())[0], 3, "a directory is not a log")
        for label, text in (("torn", '{"units": ['), ("no units", "{}"), ("not a dict", "[1]")):
            with self.subTest(label):
                self.assertEqual(proof_of(self.log(), self.put("bad.json", text))[0], 3)
        self.assertEqual(proof_of(self.log(), os.path.join(self.tmp, "absent.json"))[0], 3)
        self.assertEqual(proof_of(self.log(), self.wbs({"I3": ["I3.a"]}))[0], 3, "a proof unit the WBS does not hold is not read as landed")

    def test_the_same_run_read_twice_gives_the_same_answer_and_the_log_is_not_written(self):
        path, wbs = self.log(held=[HELD_PP1]), self.wbs()
        before = os.stat(path).st_mtime_ns, os.path.getsize(path)
        first, second = proof_of(path, wbs), proof_of(path, wbs)
        self.assertEqual(first, second)
        self.assertEqual(before, (os.stat(path).st_mtime_ns, os.path.getsize(path)))

    def test_held_cause_table_ignores_other_lines_and_unreadable_logs(self):
        path = self.log(held=["HELD at the deploy step, nothing pushed: judge refused: x", "11h20 HELD: pass exit 45", "DEPLOY  PREPARED | ok"])
        got = LR.held_cause_table(path)
        self.assertEqual([c for _l, c in got], ["judge refused: x"])
        self.assertEqual(LR.held_cause_table(os.path.join(self.tmp, "absent.log")), [])
        self.assertEqual(LR.held_cause_table(self.tmp), [])
        self.assertEqual(LR.held_cause_table(None), [])

    def test_a_deploy_step_hold_with_no_text_after_the_colon_is_unexplained(self):
        got = LR.held_cause_table(self.log(held=["HELD at the deploy step, nothing pushed: "]))
        self.assertEqual([c for _l, c in got], ["UNEXPLAINED"])

    def test_red_without_code_suites_edges(self):
        self.assertEqual(LR.red_without_code_suites([]), [])
        self.assertEqual(LR.red_without_code_suites([{"suite": "a", "red_without_code": False}]), [])
        rows = ["junk", None, 5, {"red_without_code": True}, {"suite": "b", "red_without_code": True}, {"suite": "a", "red_without_code": True}]
        self.assertEqual(LR.red_without_code_suites(rows), [("UNNAMED", 1), ("a", 1), ("b", 1)], "ties by name; a row naming no suite is counted, not dropped")
        self.assertEqual(LR.red_without_code_suites(None), [])

    def test_the_report_says_no_data_not_zero_when_no_run_is_named_or_the_log_is_unreadable(self):
        for log in (None, os.path.join(self.tmp, "absent.log"), self.tmp):
            with self.subTest(log=log):
                text = self.report_text([], log)
                section = text[text.index("HELDS AND CAUSES"):].splitlines()
                self.assertIn("NO-DATA", section[1], section[:3])

    def test_a_report_called_without_a_run_says_no_data_for_the_held_lines(self):
        text = self.report_text([])
        self.assertIn("NO-DATA: no run is named", text)

    def test_a_row_repeated_every_pass_is_printed_once_with_its_count(self):
        text = self.report_text([], self.log(held=[HELD_PP1] * 3 + ["HELD    I3.a      "]))
        section = text[text.index("HELDS AND CAUSES"):]
        self.assertEqual(section.count("PP1.c      held on the unchanged"), 1)
        self.assertIn("(x3)", section)
        self.assertIn("4 HELD line(s), 1 UNEXPLAINED", section, "the totals count every row, not every distinct one")

    def test_the_run_flag_reads_the_named_logs_held_lines(self):
        """Through the command line under a throwaway HOME, so the report reads no real ledger."""
        import subprocess
        path = self.put("run.log", "RUN START 2099-01-01T" + CLOCK + "\n" + HELD_PP1 + "\nRUN END 2099-01-01T" + CLOCK + "\n")
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        env = {k: v for k, v in os.environ.items() if k not in ("BROTHER_WBS",)}
        env["HOME"] = home
        p = subprocess.run([sys.executable, "-B", os.path.join(HERE, "loop_report.py"), "--run", path, "--plan", os.path.join(self.tmp, "none.json")],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
        self.assertIn("1 HELD line(s), 0 UNEXPLAINED", p.stdout)

    def test_stage_rows_that_are_not_a_list_name_nothing(self):
        row = {"suite": "a", "red_without_code": True}
        for odd in (None, (row,), {"a": row}, 5, "abc"):
            with self.subTest(odd=odd):
                self.assertEqual(LR.red_without_code_suites(odd), [])

    def test_a_run_whose_held_lines_all_name_causes_reports_none_unexplained(self):
        text = self.report_text([], self.log(held=[HELD_PP1]))
        self.assertIn("0 UNEXPLAINED", text)

    def test_a_readable_ledger_with_no_marked_row_says_it_cannot_prove_none(self):
        text = self.report_text([{"at": 50, "stage": "grade", "event": "leave", "ok": False}], self.log())
        section = text[text.index("YIELD: RED WITHOUT CODE"):].splitlines()
        self.assertIn("none recorded", section[1])
        self.assertIn("not proof", section[1] + section[2])

    def test_the_stage_ledger_unreadable_is_no_data_in_the_yield_section(self):
        text = self.report_text([], self.log(), stages_path=os.path.join(self.tmp, "absent.jsonl"))
        section = text[text.index("YIELD: RED WITHOUT CODE"):].splitlines()
        self.assertIn("NO-DATA", section[1])

    def test_an_unexplained_held_is_a_next_step(self):
        text = self.report_text([], self.log(held=["HELD    I3.a      "]))
        steps = text[text.index("NEXT STEPS"):]
        self.assertIn("UNEXPLAINED", steps)


def stamp(day, hour, minute=0, offset="+00"):
    """A fixture RUN START stamp, built, never written out: a fixture clock is not a measured time."""
    return "%sT%s%s" % (day, ":".join(("%02d" % hour, "%02d" % minute, "00")), offset and offset + ":".join(("", "00")))


class TestProofSince(Fixture):
    """proof_since: the window read. Each case isolates one condition, so a guard removed turns exactly its case red."""
    SINCE = stamp("2099-01-01", 0)
    ALL = ["%s.a" % uid for uid in PROOF_UNITS]

    def ev(self, logs):
        """An evidence folder holding {name: [run, ...]}, each run (start, [row, ...])."""
        d = os.path.join(self.tmp, "ev")
        os.makedirs(d, exist_ok=True)
        for name, runs in logs.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                for start, rows in runs:
                    fh.write("RUN START %s\n" % start + "".join(r + "\n" for r in rows))
        return d

    def landed(self, subs):
        return ["LANDED  %s | units landed | log x" % s for s in subs]

    def run_(self, d, since=Ellipsis):
        lines = []
        return proof_since(d, self.SINCE if since is Ellipsis else since, self.wbs(), lines), lines

    def test_landings_split_across_two_runs_of_the_window_pass(self):
        d = self.ev({"loop-until-20990101-0010.log": [(stamp("2099-01-01", 0, 10), self.landed(self.ALL[:1]))],
                     "loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL[1:]))]})
        code, lines = self.run_(d)
        self.assertEqual(code, 0, lines)
        self.assertIn("2 run(s)", lines[0])

    def test_a_landing_before_the_window_never_counts(self):
        d = self.ev({"loop-until-20981231-2300.log": [(stamp("2098-12-31", 23), self.landed(self.ALL[:1]))],
                     "loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL[1:]))]})
        code, lines = self.run_(d)
        self.assertEqual(code, 1, lines)
        self.assertIn("MISSING: %s has no LANDED line in the window" % self.ALL[0], lines)

    def test_an_unexplained_hold_in_an_earlier_run_of_the_window_fails(self):
        d = self.ev({"loop-until-20990101-0010.log": [(stamp("2099-01-01", 0, 10), ["HELD    %s" % self.ALL[0]])],
                     "loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL))]})
        code, lines = self.run_(d)
        self.assertEqual(code, 1, lines)
        self.assertIn("UNEXPLAINED: HELD    %s" % self.ALL[0], lines)

    def test_a_second_run_appended_to_one_log_keeps_the_first_runs_hold(self):
        d = self.ev({"loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), ["HELD    %s" % self.ALL[0]]),
                                                      (stamp("2099-01-01", 1, 1), self.landed(self.ALL))]})
        code, lines = self.run_(d)
        self.assertEqual(code, 1, lines)
        self.assertEqual(len([x for x in lines if x.startswith("RUN: ")]), 2, lines)

    def test_a_run_that_cannot_be_dated_is_no_data(self):
        # beside a run that alone would PASS: an undatable run may be in the window and hold a hold, so it blocks
        d = self.ev({"loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL))],
                     "loop-until-20990101-0200.log": [("not a time", ["HELD    %s" % self.ALL[0]])]})
        code, lines = self.run_(d)
        self.assertEqual(code, 3, lines)
        self.assertIn("cannot be dated", lines[-1])

    def test_a_window_start_without_an_offset_is_no_data(self):
        d = self.ev({"loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL))]})
        for since in (stamp("2099-01-01", 0, offset=""), None, 7, ""):
            code, lines = self.run_(d, since)
            self.assertEqual(code, 3, (since, lines))

    def test_no_run_in_the_window_is_no_data(self):
        d = self.ev({"loop-until-20981231-2300.log": [(stamp("2098-12-31", 23), self.landed(self.ALL))]})
        code, lines = self.run_(d)
        self.assertEqual(code, 3, lines)
        self.assertIn("no run in", lines[0])

    def test_a_wrapper_log_is_never_read(self):
        d = self.ev({"loop-until-day-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL[:1]))],
                     "loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL[1:]))]})
        code, lines = self.run_(d)
        self.assertEqual(code, 1, lines)
        self.assertIn("MISSING: %s has no LANDED line in the window" % self.ALL[0], lines)

    def test_a_folder_that_cannot_be_listed_is_no_data(self):
        code, lines = self.run_(os.path.join(self.tmp, "absent"))
        self.assertEqual(code, 3, lines)

    def test_a_batch_landing_counts_every_sub_unit_it_names(self):
        # the driver writes one LANDED line for a batch: "LANDED  I22.a, I3.a, I4.a, LC1.b | ..." (2026-10-22, the real
        # window read 9 of 15 where 13 had landed, because the pattern took one name only)
        batch = "LANDED  %s | units with every sub unit landed (run the unit done-check): none | log x" % ", ".join(self.ALL)
        d = self.ev({"loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), [batch])]})
        code, lines = self.run_(d)
        self.assertEqual(code, 0, lines)

    def test_the_one_run_read_counts_a_batch_landing_too(self):
        batch = "LANDED  %s | units landed | log x" % ", ".join(self.ALL)
        log = self.put("batch.log", "RUN START 2099-01-01T" + CLOCK + "\n" + batch + "\n")
        code, lines = proof_of(log, self.wbs())
        self.assertEqual(code, 0, lines)

    # ----- an adversarial review of proof_since (2026-10-21), each a concrete input it built -----

    def test_an_outcome_before_any_run_start_is_no_data(self):
        d = self.ev({"loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL))]})
        with open(os.path.join(d, "loop-until-20990101-0000.log"), "w", encoding="utf-8") as fh:
            fh.write("HELD    %s\n" % self.ALL[0])
        code, lines = self.run_(d)
        self.assertEqual(code, 3, lines)
        self.assertIn("before any RUN START", lines[-1])

    def test_a_log_that_is_not_utf8_is_no_data(self):
        d = self.ev({"loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL))]})
        with open(os.path.join(d, "loop-until-20990101-0100.log"), "ab") as fh:
            fh.write(b"HELD    \xff\n")
        code, lines = self.run_(d)
        self.assertEqual(code, 3, lines)

    def test_sub_units_that_are_not_a_list_are_no_data(self):
        d = self.ev({"loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(["a"]))]})
        wbs = self.put("wbs-str.json", json.dumps({"units": [{"id": uid, "sub_units": "a"} for uid in PROOF_UNITS]}))
        lines = []
        self.assertEqual(proof_since(d, self.SINCE, wbs, lines), 3, lines)

    def test_a_line_that_only_begins_like_a_run_start_is_not_one(self):
        d = self.ev({"loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL))]})
        with open(os.path.join(d, "loop-until-20990101-0100.log"), "r+", encoding="utf-8") as fh:
            body = fh.read()
            fh.seek(0)
            fh.write("RUN STARTING diagnostics\n" + body)
        code, lines = self.run_(d)
        self.assertEqual(code, 0, lines)

    def test_a_folder_that_is_not_a_path_string_is_no_data(self):
        # a real folder, given as an open descriptor or as bytes: listdir answers both, and the reader must not run on
        d = self.ev({"loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL))]})
        fd = os.open(d, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        for folder in (fd, d.encode(), None):
            lines = []
            self.assertEqual(proof_since(folder, self.SINCE, self.wbs(), lines), 3, (folder, lines))

    def test_a_log_named_long_before_the_window_is_never_read(self):
        # an old log with no RUN START and a stray outcome: older than the window by its own name, so not read at all
        d = self.ev({"loop-until-20990101-0100.log": [(stamp("2099-01-01", 1), self.landed(self.ALL))]})
        with open(os.path.join(d, "loop-until-20980101-0000.log"), "w", encoding="utf-8") as fh:
            fh.write("HELD    %s\n" % self.ALL[0])
        code, lines = self.run_(d)
        self.assertEqual(code, 0, lines)


class TestLoopWorksFullyLive(unittest.TestCase):
    """LR1's unit acceptance: the named run against the live WBS. NO-DATA until a run is named, never a pass. With
    BROTHER_PROOF_SINCE set, the window read instead: every run since that start in ~/.claude/evidence (OPEN ITEMS 10)."""

    @unittest.skipUnless(os.path.isfile(LIVE_WBS), "NO-DATA: the live WBS %s is not in this tree" % LIVE_WBS)
    def test_the_named_run_landed_every_proof_unit_with_zero_unexplained_held(self):
        since = os.environ.get("BROTHER_PROOF_SINCE")
        if since is not None:
            lines = []
            code = proof_since(os.path.expanduser("~/.claude/evidence"), since, LIVE_WBS, lines)
        else:
            code, lines = proof_of(os.environ.get("BROTHER_PROOF_RUN_LOG"), LIVE_WBS)
        for line in lines:
            print(line)
        if code == 3:
            self.skipTest(lines[0])
        self.assertEqual(code, 0, "\n".join(lines))


if __name__ == "__main__":
    prog = unittest.main(exit=False)
    nodata = [why for _t, why in prog.result.skipped if why.startswith("NO-DATA")]
    if not prog.result.wasSuccessful():
        sys.exit(1)
    if nodata:
        print("NO-DATA (not a pass): %s" % nodata[0])
        sys.exit(3)
    sys.exit(0)
