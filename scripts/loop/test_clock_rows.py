#!/usr/bin/env python3
"""LR1.b: clock bound rows name their stale record, and the launch refuses early.

2026-10-21: three standing exceptions expired at midnight and every tree's whole gate went red for a reason no build
caused; the judge's HELD line blamed the build. A record the gate reads against the clock (the close ceremony pack, the
restore drill record) is fresh when a run starts and stale when it lands. The two expiry_errors helpers ask, at launch,
whether each outlasts the run's deadline, and land_batch.held_reason names a STALE gate row as a clock cause.

Standard library only, python3 and /usr/bin/python3. Writes only under its own throwaway folders. The launcher cases run
start_run.sh through its seams (a throwaway repository, a throwaway HOME); nothing starts a real loop.

Run: python3 -B scripts/loop/test_clock_rows.py TestClockRows
"""
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)
import acc6_restore_evidence as AR  # noqa: E402
import close_ceremony_check as CC  # noqa: E402
import land_batch as LB  # noqa: E402

T0 = 1_700_000_000.0          # a fixed pack time: no case here reads the real clock to decide a verdict
DAY = 86400.0
CLOSE_FIX = "refresh with python3 scripts/close_ceremony_check.py"
DRILL_FIX = "refresh with python3 scripts/restore_drill_enterprise.py"
GATE = ("MAX_AGE_DAYS = 7\nITEMS = [{'id': 'restore-drill', 'critical': True, 'kind': 'record', "
        "'max_age_days': MAX_AGE_DAYS, 'path': 'docs/plan/DRILL.json', 'blocker': None}]\n")
BAD_DEADLINES = (True, False, None, float("nan"), float("inf"), "24", [], b"1")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def local_epoch(y, mo, d, h, mi):
    return datetime.datetime(y, mo, d, h, mi).timestamp()


class Folder(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="test-clock-rows-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def pack(self, name, mtime):
        d = os.path.join(self.tmp, "handovers", name)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "01-START-HERE.md"), "w").close()      # a pack marker: a bare folder is not a pack
        os.utime(d, (mtime, mtime))
        return os.path.join(self.tmp, "handovers")

    def tree(self, drill_date="2026-09-28", gate=GATE, record=True):
        root = os.path.join(self.tmp, "tree")
        if gate is not None:
            write(os.path.join(root, "scripts", "readiness_gate.py"), gate)
        if record:
            write(os.path.join(root, "docs", "plan", "DRILL.json"), json.dumps({"drill_date": drill_date}))
        os.makedirs(root, exist_ok=True)
        return root


class TestClockRows(Folder):
    # ---- close ceremony pack
    def test_close_ceremony_expiry_names_record_before_deadline(self):
        root = self.pack("2026-10-20-close", T0)
        self.assertEqual(CC.expiry_errors(root, T0 + DAY - 1), [])
        # the pack stands through its 24 hours; at the instant it expires, a deadline there is already past it
        want = ["close ceremony: newest pack 2026-10-20-close expires before %s; %s"
                % (time.strftime("%Y-%m-%d %H:%M", time.localtime(T0 + DAY)), CLOSE_FIX)]
        self.assertEqual(CC.expiry_errors(root, T0 + DAY), want)
        self.assertEqual(len(CC.expiry_errors(root, T0 + 5 * DAY)), 1)

    def test_close_ceremony_reads_the_newest_pack_and_an_empty_root_is_a_refusal(self):
        root = self.pack("old-pack", T0 - 9 * DAY)
        self.pack("new-pack", T0)
        self.assertEqual(CC.expiry_errors(root, T0 + 3600), [], "the newest pack decides, not the oldest")
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.assertEqual(CC.expiry_errors(empty, T0), ["close ceremony: no pack under %s; %s" % (empty, CLOSE_FIX)])

    def test_close_ceremony_refuses_hostile_input_before_any_read(self):
        root = self.pack("p", T0)
        for bad in BAD_DEADLINES:
            with self.subTest(deadline=bad):
                with self.assertRaises(ValueError):
                    CC.expiry_errors(root, bad)
        for bad in (os.path.join(self.tmp, "absent"), None, 5, os.path.join(root, "p")):
            with self.subTest(root=bad):
                if bad == os.path.join(root, "p"):
                    shutil.rmtree(bad)
                    write(bad, "a file, not a folder")
                with self.assertRaises(ValueError):
                    CC.expiry_errors(bad, T0)

    # ---- restore drill record
    def test_restore_drill_expiry_names_record_before_deadline(self):
        root = self.tree("2026-09-28")           # the bar is 7 days: it stands through 2026-10-05, stale from 2026-10-06
        self.assertEqual(AR.expiry_errors(root, local_epoch(2026, 10, 5, 23, 59)), [])
        want = ["restore drill: docs/plan/DRILL.json stale on 2026-10-06; %s" % DRILL_FIX]
        self.assertEqual(AR.expiry_errors(root, local_epoch(2026, 10, 6, 0, 1)), want)
        self.assertEqual(AR.expiry_errors(root, local_epoch(2026, 12, 1, 9, 0)), want)

    def test_restore_drill_unreadable_inputs_are_named_refusals_never_a_pass(self):
        deadline = local_epoch(2026, 10, 1, 9, 0)
        cases = {
            "no gate": dict(gate=None),
            "gate declares no restore item": dict(gate="ITEMS = [{'id': 'other'}]\n"),
            "no record": dict(record=False),
            "record not a date": dict(drill_date="soon"),
        }
        for label, kw in cases.items():
            with self.subTest(case=label):
                shutil.rmtree(os.path.join(self.tmp, "tree"), True)
                out = AR.expiry_errors(self.tree(**kw), deadline)
                self.assertEqual(len(out), 1, out)
                self.assertTrue(out[0].startswith("restore drill: ") and out[0].endswith(DRILL_FIX), out)
        shutil.rmtree(os.path.join(self.tmp, "tree"), True)
        root = self.tree()
        write(os.path.join(root, "docs", "plan", "DRILL.json"), "{torn")
        self.assertIn("not readable JSON", AR.expiry_errors(root, deadline)[0])

    def test_restore_drill_refuses_hostile_input_before_any_read(self):
        root = self.tree()
        for bad in BAD_DEADLINES:
            with self.subTest(deadline=bad):
                with self.assertRaises(ValueError):
                    AR.expiry_errors(root, bad)
        for bad in ("2026-10-01", 20261001, True):
            with self.subTest(today=bad):
                with self.assertRaises(ValueError):
                    AR.expiry_errors(root, T0, today=bad)
        for bad in (os.path.join(self.tmp, "absent"), None, "", 5):
            with self.subTest(root=bad):
                with self.assertRaises(ValueError):
                    AR.expiry_errors(bad, T0)
        self.assertEqual(AR.expiry_errors(root, local_epoch(2026, 10, 1, 9, 0), today=datetime.date(2026, 10, 1)), [])

    # ---- the judge
    def test_judge_held_reason_names_a_stale_clock_row(self):
        text = ("FAIL    exit 1   restore-drill        2s  STALE: record drill_date 2026-09-28 is 23 day(s) old, over "
                "the 7 day bar declared by scripts/readiness_gate.py")
        self.assertEqual(LB.held_reason(text), "clock: restore-drill scripts/readiness_gate.py stale")
        # the same through the judge's verdict: the clock cause first, the code failure kept after it
        out = ("PASS    exit 0   version-truth        0s  ok\n" + text + "\nFAIL    exit 1   surface  3s  AssertionError\n")
        ok, codes, why = LB.judge_verdict(1, out)
        self.assertFalse(ok)
        self.assertTrue(why.startswith("clock: restore-drill scripts/readiness_gate.py stale; the whole gate exited 1 boxed: "), why)
        self.assertIn("surface FAIL exit 1", why)

    def test_judge_held_reason_leaves_a_code_failure_alone(self):
        self.assertEqual(LB.held_reason([("D1.1", "INHERITED"), ("D1.2", "NO-DATA")]),
                         "D1.1 INHERITED (red on the base too), D1.2 NO-DATA (base run unreadable); "
                         "the build is neither landed nor quarantined")
        self.assertEqual(LB.held_reason([]), "")
        for bad in (None, 7, "FAIL    exit 1   surface  3s  AssertionError: a plain code failure"):
            with self.subTest(held=bad):
                self.assertTrue(LB.held_reason(bad).startswith("NO-DATA"), "a non STALE value keeps its old answer")
        out = "PASS    exit 0   a  0s  ok\nFAIL    exit 1   surface  3s  AssertionError\n"
        ok, _codes, why = LB.judge_verdict(1, out)
        self.assertEqual((ok, why), (False, "the whole gate exited 1 boxed: surface FAIL exit 1"), "a code failure is never relabelled")
        # a STALE word on a PASS row is not a failure at all
        ok, _codes, why = LB.judge_verdict(0, "PASS    exit 0   a  0s  STALE: nothing, passed\n")
        self.assertTrue(ok, why)

    def test_a_stale_text_that_names_no_row_or_no_record_is_refused_not_guessed(self):
        for text in ("STALE: scripts/readiness_gate.py with no gate row line",
                     "FAIL    exit 1   restore-drill  2s  STALE: the record is old and no file is named"):
            with self.subTest(text=text):
                self.assertTrue(LB.held_reason(text).startswith("NO-DATA"), LB.held_reason(text))


    # ---- M1: real gate shaped lines
    STALE_LINE = ("STALE: record drill_date 2026-09-28 is 23 day(s) old, over the 7 day bar declared by "
                  "scripts/readiness_gate.py")

    def boxed_gate(self, script):
        """The REAL chain: boxed() runs `script` under the sandbox with its own TMPDIR (the gate's keep files live there and
        die with the box), then judge_verdict reads the verdict with what boxed kept. Returns (ok, why)."""
        work = os.path.join(self.tmp, "work")
        os.makedirs(work, exist_ok=True)
        with open(os.path.join(self.tmp, "box.log"), "w") as log:
            r = LB.boxed(["sh", "-c", script], log, 60, None, cwd=work)
        if r.returncode == 125:
            self.skipTest("no sandbox on this host: %s" % r.stderr.strip()[:100])
        ok, _codes, why = LB.judge_verdict(r.returncode, (r.stdout or "") + (r.stderr or ""), getattr(r, "kept", None))
        return ok, why, r

    def gate_script(self, keep_body, row_tail, suffix_path='"$k"', extra=""):
        return ('k="$TMPDIR/required-fast-fail-readiness-gate-abc.txt"; printf "%%s\\n" "%s" > "$k"; %s'
                'printf "PASS    exit 0   a  0s  ok\\nFAIL    exit 1   readiness-gate      3s  %s  [full: %%s]\\n" %s; exit 1'
                % (keep_body, extra, row_tail, suffix_path))

    def test_the_judge_reads_a_clock_cause_the_row_line_cut_off_from_the_kept_fail_file(self):
        # the gate's line: the last 72 characters of the row's output cut inside the STALE text, then the keep suffix
        script = self.gate_script("noise\\n" + self.STALE_LINE, "STALE: record drill_date 2026-09-28 is 23 day(s) old, ov")
        ok, why, r = self.boxed_gate(script)
        self.assertFalse(ok)
        self.assertTrue(why.startswith("clock: readiness-gate scripts/readiness_gate.py stale; the whole gate exited 1"), why)
        kept = list(r.kept)
        self.assertEqual(len(kept), 1, r.kept)
        self.assertFalse(os.path.exists(kept[0]), "the keep file died with the box: only what boxed kept could name the cause")

    def test_the_kept_files_are_read_before_the_box_is_removed_not_after(self):
        script = self.gate_script("x\\n" + self.STALE_LINE, "STALE: cut off here")
        ok, why, r = self.boxed_gate(script)
        path = list(r.kept)[0]
        self.assertFalse(os.path.exists(path))
        # verdict formed from the same output WITHOUT what boxed kept: the cause is unreadable, the code failure stands
        _ok, plain, _c = False, LB.judge_verdict(r.returncode, (r.stdout or "") + (r.stderr or ""))[2], None
        self.assertTrue(plain.startswith("the whole gate exited 1 boxed"), plain)

    def test_only_the_gates_own_last_suffix_under_the_boxs_tmpdir_is_read(self):
        outside = os.path.join(self.tmp, "required-fast-fail-outside.txt")
        write(outside, self.STALE_LINE + "\n")
        planted_valid = ('p="$TMPDIR/required-fast-fail-planted.txt"; printf "%s\\n" "' + self.STALE_LINE + '" > "$p"; ')
        cases = {
            # the build plants a valid keep file with a STALE text inside the 72 character tail; the gate's own (last) suffix names a clean one
            "an earlier planted suffix": self.gate_script("clean output", "x [full: $p]", extra=planted_valid),
            # the last suffix is a relative path
            "a relative path": self.gate_script("clean output", "x", suffix_path="required-fast-fail-rel.txt"),
            # the last suffix is an absolute path outside the box's tmp, holding a STALE text
            "an absolute path outside": self.gate_script("clean output", "x", suffix_path="'" + outside + "'"),
        }
        for label, script in cases.items():
            with self.subTest(case=label):
                ok, why, r = self.boxed_gate(script)
                self.assertFalse(ok)
                self.assertTrue(why.startswith("the whole gate exited 1 boxed"), "no clock cause from a path the gate did not make: " + why)
        # the same reader, called directly: a path under the right tmp but not the gate's file name reads nothing
        tmp = os.path.join(self.tmp, "gatetmp")
        os.makedirs(tmp)
        other = os.path.join(tmp, "notes.txt")
        write(other, self.STALE_LINE)
        line = "FAIL    exit 1   a  1s  x  [full: %s]\n" % other
        self.assertEqual(LB.read_kept(line, tmp), {})
        keep = os.path.join(tmp, "required-fast-fail-a-k.txt")
        write(keep, self.STALE_LINE)
        self.assertEqual(list(LB.read_kept("FAIL    exit 1   a  1s  x  [full: %s]\n" % keep, tmp)), [keep])
        rel = os.path.join(tmp, "required-fast-fail-rel.txt")
        write(rel, self.STALE_LINE)
        here = os.getcwd()
        os.chdir(tmp)
        try:
            self.assertEqual(LB.read_kept("FAIL    exit 1   a  1s  x  [full: required-fast-fail-rel.txt]\n", tmp), {}, "a relative path is never read")
        finally:
            os.chdir(here)
        target = os.path.join(tmp, "required-fast-fail-target.txt")
        write(target, self.STALE_LINE)
        link = os.path.join(tmp, "required-fast-fail-link.txt")
        os.symlink(target, link)          # a link INSIDE the box's tmp to a real keep file: only the link rule refuses it
        self.assertEqual(LB.read_kept("FAIL    exit 1   a  1s  x  [full: %s]\n" % link, tmp), {}, "a link is never followed")

    def test_clock_record_and_held_reason_never_raise(self):
        odd = ["x past review_by (a):\n  -  \n", "past review_by\n  -\n", "past review_by\n  - \n  - ok\n", "STALE:", "STALE: \x00\x00",
               "FAIL    exit 1   a  1s  STALE: \U0010ffff past review_by (z):\n  -   \n", "", "\n\n", "  - \n"]
        for text in odd:
            with self.subTest(text=text):
                self.assertIn(type(LB.clock_record(text)), (str, type(None)))
                self.assertIsInstance(LB.held_reason(text), str)
        for bad in (None, 5, b"STALE: x.json", ["STALE:"]):
            self.assertIsNone(LB.clock_record(bad), "a non string is no clock cause, and never an exception")
        # a bare dash item is skipped, the next real item is the cause
        self.assertEqual(LB.clock_record("past review_by (a):\n  -  \n  - second-one\n"), "exception:second-one")
        out = "FAIL    exit 1   readiness-gate  1s  x\npast review_by (a):\n  -  \n"
        self.assertTrue(LB.judge_verdict(1, out)[2].startswith("the whole gate exited 1"))

    def test_the_real_stale_lines_of_the_gate_map_to_clock_causes(self):
        row = "FAIL    exit 1   %s   2s  x\n"
        cases = [
            ("readiness-gate", "  Restore drill                  NO-DATA\n        docs/plan/RESTORE-DRILL-ENTERPRISE-RESULT.json: drill_date 2026-09-01 "
             "is 20 day(s) old, over the 7 day freshness bar\n",
             "clock: readiness-gate docs/plan/RESTORE-DRILL-ENTERPRISE-RESULT.json stale"),
            ("closing-ceremony-real", "FAIL: pack 2026-10-19-close is 30.1 hours old, over the 24 hour freshness bar: a stale pack cannot green a new close\n",
             "clock: closing-ceremony-real 2026-10-19-close stale"),
            ("readiness-gate", "GATE: NOT READY.\n  3 declared battery exception(s) past review_by (a failure cemetery is not allowed):\n"
             "  - release-note-perturb\n  - second\n", "clock: readiness-gate exception:release-note-perturb stale"),
        ]
        for name, body, want in cases:
            with self.subTest(row=name):
                self.assertEqual(LB.held_reason((row % name) + body), want)
        # a plain code failure with none of those words is never a clock cause
        self.assertTrue(LB.held_reason((row % "surface") + "AssertionError: 3 != 4\n").startswith("NO-DATA"))

    def test_a_stale_line_names_its_record_not_the_keep_file_the_suffix_points_at(self):
        # each guard alone: the suffix is stripped (a keep file named anything), and a gate keep file is never a record
        for keep in ("/t/required-fast-fail-acc6-k.txt", "/t/keep-abc.json"):
            text = "FAIL    exit 1   acc6   1s  STALE: nothing readable here  [full: %s]" % keep
            self.assertTrue(LB.held_reason(text).startswith("NO-DATA"), LB.held_reason(text))
        body = "FAIL    exit 1   acc6   1s  x\nSTALE: see /t/required-fast-fail-acc6-k.txt\n"
        self.assertTrue(LB.held_reason(body).startswith("NO-DATA"), LB.held_reason(body))

    # ---- m2 and m4
    def test_a_scratch_folder_with_no_pack_marker_cannot_make_a_stale_pack_look_fresh(self):
        root = self.pack("2026-10-01-close", T0 - 5 * DAY)
        scratch = os.path.join(root, "release-cut-scratch")
        os.makedirs(scratch)
        write(os.path.join(scratch, "notes.md"), "scratch")
        os.utime(scratch, (T0, T0))                       # newer than the real pack, and not a pack
        out = CC.expiry_errors(root, T0 + 3600)
        self.assertEqual(len(out), 1, out)
        self.assertIn("newest pack 2026-10-01-close expires before", out[0])
        empty = os.path.join(self.tmp, "only-scratch")
        os.makedirs(os.path.join(empty, "scratch"))
        self.assertIn("no pack under", CC.expiry_errors(empty, T0)[0])

    def test_a_drill_date_in_the_future_is_a_refusal_not_a_fresh_record(self):
        root = self.tree("2026-12-25")
        out = AR.expiry_errors(root, local_epoch(2026, 12, 26, 9, 0), today=datetime.date(2026, 10, 21))
        self.assertEqual(out, ["restore drill: docs/plan/DRILL.json drill_date 2026-12-25 is in the future (INVALID); %s" % DRILL_FIX])
        self.assertEqual(AR.expiry_errors(root, local_epoch(2026, 12, 26, 9, 0), today=datetime.date(2026, 12, 25)), [])

    # ---- the launcher
    def launcher(self, deadline, **extra):
        from test_run_watch import Launch
        launch = Launch(self)
        env = launch.env(**extra)
        if "START_RUN_EVIDENCE" not in extra:
            env.pop("START_RUN_EVIDENCE", None)      # the suites' own scratch evidence folder would turn the check off
        p = subprocess.run(["bash", os.path.join(HERE, "start_run.sh"), "--deadline", deadline, "--budget-usd", "5"],
                           cwd=launch.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=90)
        return launch, p.returncode, p.stdout

    def soon(self):
        return (datetime.datetime.now() + datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")

    def test_start_run_refuses_early_on_a_stale_close_pack(self):
        root = self.pack("old-close", time.time() - 3 * DAY)
        launch, rc, out = self.launcher(self.soon(), START_RUN_HANDOVER_ROOT=root)
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [clock]: close ceremony: newest pack old-close expires before ", out)
        self.assertIn(CLOSE_FIX, out)
        self.assertFalse(os.path.exists(os.path.join(launch.evd, "driver.pid")), "no driver started")

    def test_start_run_refuses_on_the_helper_that_refuses_when_the_other_reads_fresh(self):
        root = self.pack("fresh-close", time.time())
        launch, rc, out = self.launcher(self.soon(), START_RUN_HANDOVER_ROOT=root)
        self.assertEqual(rc, 2, out)           # the fixture checkout holds no restore drill declaration
        self.assertIn("START REFUSED [clock]: restore drill: scripts/readiness_gate.py declares no readable", out)
        self.assertFalse(os.path.exists(os.path.join(launch.evd, "driver.pid")))

    def test_a_missing_handover_root_is_a_note_not_a_refusal_and_the_restore_drill_still_decides(self):
        launch, rc, out = self.launcher(self.soon(), START_RUN_HANDOVER_ROOT=os.path.join(self.tmp, "nowhere"))
        self.assertIn("NOTE clock: no handover root at ", out)
        self.assertEqual(rc, 2, out)           # the fixture checkout holds no restore drill declaration
        self.assertIn("START REFUSED [clock]: restore drill: scripts/readiness_gate.py declares no readable", out)

    def test_the_helpers_stderr_is_never_read_as_the_verdict(self):
        launch, rc, out = self.launcher(self.soon(), START_RUN_HANDOVER_ROOT=os.path.join(self.tmp, "nowhere"), PYTHONVERBOSE="1")
        self.assertIn("START REFUSED [clock]: restore drill: scripts/readiness_gate.py declares no readable", out)

    def test_the_plugin_route_without_the_helpers_notes_it_and_goes_on(self):
        from test_run_watch import Launch
        launch = Launch(self)
        env = launch.env(START_RUN_HANDOVER_ROOT=os.path.join(self.tmp, "nowhere"))
        env.pop("START_RUN_EVIDENCE", None)
        bundle = os.path.join(os.path.dirname(os.path.dirname(HERE)), "bundle", "runtime", "loop", "start_run.sh")
        self.assertTrue(os.path.isfile(bundle), "the bundle mirror of start_run.sh is part of this unit")
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(bundle), "..", "close_ceremony_check.py")))
        p = subprocess.run(["bash", bundle, "--deadline", self.soon(), "--budget-usd", "5"], cwd=launch.repo, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=90)
        self.assertIn("NOTE clock: not checked on this route (helpers absent", p.stdout)
        self.assertNotIn("START REFUSED [clock]", p.stdout)

    def test_only_the_off_word_or_a_scratch_evidence_folder_turns_the_check_off(self):
        root = self.pack("old-close", time.time() - 3 * DAY)
        # an evidence folder outside every temp and scratch path, with no off word: the check runs and refuses
        launch, rc, out = self.launcher(self.soon(), START_RUN_HANDOVER_ROOT=root,
                                        START_RUN_EVIDENCE="/nonexistent-clock-rows/evidence")
        self.assertEqual(rc, 2, out)
        self.assertIn("START REFUSED [clock]: close ceremony: newest pack old-close", out)
        # the off word turns it off, loudly
        launch, rc, out = self.launcher(self.soon(), START_RUN_HANDOVER_ROOT=root, START_RUN_CLOCK_ROWS="off",
                                        START_RUN_EVIDENCE="/nonexistent-clock-rows/evidence")
        self.assertIn("NOTE clock: the close pack and the restore drill were NOT checked", out)
        self.assertNotIn("START REFUSED [clock]", out)

    def test_start_run_checks_by_default_when_no_test_evidence_folder_is_named(self):
        from test_run_watch import Launch
        root = self.pack("old-close", time.time() - 3 * DAY)
        launch = Launch(self)
        env = launch.env(START_RUN_HANDOVER_ROOT=root)
        for gone in ("START_RUN_CLOCK_ROWS", "START_RUN_EVIDENCE"):
            env.pop(gone, None)
        p = subprocess.run(["bash", os.path.join(HERE, "start_run.sh"), "--deadline", self.soon(), "--budget-usd", "5"],
                           cwd=launch.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=90)
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertIn("START REFUSED [clock]: close ceremony: newest pack old-close expires before ", p.stdout)

    def test_start_run_says_when_it_did_not_check_the_clock_rows(self):
        from test_run_watch import Launch
        launch = Launch(self)
        rc, out = launch.launch()            # the suites' own throwaway evidence folder: off by default, and loudly
        self.assertIn("NOTE clock: the close pack and the restore drill were NOT checked", out)
        self.assertNotIn("START REFUSED [clock]", out)


if __name__ == "__main__":
    unittest.main()
