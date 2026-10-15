#!/usr/bin/env python3
"""FC1.a (docs/plan/specs/FC1.md): a preflight that did not clear ends the
rehearsal before any worktree or chain command, and a preflight row that reads
NO-DATA spares the expensive rows exactly as a REFUSED row does.

Measured 2026-10-11: a 1.1.1 rehearsal printed its preflight refusal at
03:16:02, then made a worktree and ran the whole chain for 50 more minutes
before writing the NOT-READY it had already reached.

HOW. cut.main --check runs on a scripted runner keyed the way test_cut.py
keys commands; the composed case runs cut_rehearsal.main in a throwaway git
repository whose child cut.py is cut.main itself, called in process. The
preflight cases drive cut_preflight.run_all through test_cut_preflight's own
fixture.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import cut_preflight as P  # noqa: E402
import cut_rehearsal as CR  # noqa: E402
import test_cut_preflight as TP  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

V = "9.9.9"
HEAD = "e" * 40
CHAIN_LABELS = ("git worktree", "retire_catalogs.py", "cut_v1.0.0.sh", "plugin_bump_gate.py",
                "required_fast.sh", "refresh_cut.py", "release_invariant.py")
GREEN = {"git rev-parse HEAD": (0, HEAD + "\n"), "git status --porcelain": (0, "")}


def label(cmd):
    """git plus two words; the basename of cmd[1] for sh and interpreters (test_cut._key's shape)."""
    if cmd[0] == "git":
        return "git " + " ".join(cmd[1:3])
    return os.path.basename(cmd[1])


def chain_calls(labels):
    return [l for l in labels for c in CHAIN_LABELS if l == c or l.startswith(c + " ")]


class Runner(object):
    """Answers from `answers` (label -> (code, stdout)), default exit 0 and no output; records every label."""

    def __init__(self, answers):
        self.answers = dict(answers)
        self.labels = []

    def __call__(self, cmd, **kw):
        key = label(list(cmd))
        self.labels.append(key)
        code, out = self.answers.get(key, (0, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")


def _inner(argv, runner, root):
    # RC1.b1: the chain's export rows build a real export tree, which a fake root cannot; test_cut.py proves them
    with mock.patch.object(C, "_load_bm_store", return_value=(None, None, "absent")), \
            mock.patch.object(C, "release_paths", return_value=["scripts/"]), \
            mock.patch.object(C, "export_rows", return_value=(True, ["export rows: stubbed"])), \
            mock.patch.object(C, "start_canary", return_value=None), \
            mock.patch.object(C, "await_canary", return_value=(True, ["linux canary: stubbed"])):
        # LX1.b1: the Linux canary is proven in test_cut.py's TheChainDispatchesTheCanary
        return C.main(argv, root=root, runner=runner)


def run_check(answers, tmp_root):
    """(code, stdout, labels) of cut.main --check on a scripted runner; any mkdtemp lands in tmp_root."""
    r = Runner(dict(GREEN, **answers))
    out = io.StringIO()
    with mock.patch.object(tempfile, "tempdir", tmp_root), contextlib.redirect_stdout(out):
        code = _inner(["--check", "--version", V], r, "/fake/root")
    return code, out.getvalue(), r.labels


class TheRehearsalStopsAtThePreflight(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-cut-rehearsal-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_refused_preflight_runs_no_chain(self):
        code, out, labels = run_check({"cut_preflight.py": (1, "cut-preflight: REFUSED\n")}, self.tmp)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("rehearsal: not run", out)
        self.assertIn("NOT READY for %s" % V, out)
        self.assertIn(C.PREFLIGHT_REFUSED, out)
        self.assertEqual(chain_calls(labels), [], labels)

    def test_no_data_and_a_missing_preflight_stop_the_same_way(self):
        for code_in in (2, 127):
            with self.subTest(exit=code_in):
                code, out, labels = run_check({"cut_preflight.py": (code_in, "")}, self.tmp)
                self.assertEqual(code, C.EXIT_REFUSED)
                self.assertIn("rehearsal: not run", out)
                self.assertEqual(chain_calls(labels), [], labels)

    def test_any_other_reason_still_runs_the_chain(self):
        code, out, labels = run_check({"git status --porcelain": (0, " M README.md\n")}, self.tmp)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("rehearsal: not run", out)
        self.assertIn("cut_v1.0.0.sh", labels)
        self.assertIn("required_fast.sh", labels)

    def test_all_green_is_ready_after_the_chain(self):
        code, out, labels = run_check({}, self.tmp)
        self.assertEqual(code, C.EXIT_OK, out)
        self.assertIn("READY for %s" % V, out)
        self.assertIn("cut_v1.0.0.sh", labels)

    def test_the_stop_line_does_not_call_a_no_data_row_a_refusal(self):
        code, out, labels = run_check({"cut_preflight.py": (2, "cut-preflight: NO-DATA\n")}, self.tmp)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("did not clear (a row REFUSED or read NO-DATA)", out)
        self.assertNotIn("cut_preflight.py refused", out)
        self.assertEqual(chain_calls(labels), [], labels)


class TheComposedRehearsal(unittest.TestCase):
    """cut_rehearsal.main around a child cut.py --check whose preflight refuses: NOT-READY, no chain, minutes."""

    def setUp(self):
        base = tempfile.mkdtemp(prefix="test-cut-rehearsal-rig-")
        self.addCleanup(shutil.rmtree, base, True)
        self.repo, self.ev, self.bs = (os.path.join(base, n) for n in ("repo", "evidence", "scratch"))
        home = os.path.join(base, "home")
        for d in (self.repo, home, self.bs):
            os.makedirs(d)
        env = dict((k, v) for k, v in os.environ.items()
                   if not k.startswith(("GIT_", "BROTHER_")) and k != "XDG_CONFIG_HOME")
        env.update(HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.test",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.test", BROTHER_SCRATCH=self.bs,
                   BROTHER_CUT_TEST="1", BROTHER_CUT_EVIDENCE_DIR=self.ev)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        files = {"scripts/cut.py": "cut\n"}
        for rel in CR.SCRIPT_DIRS:
            files[rel + "/m.py"] = "m\n"
        for rel, text in files.items():
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
            proc = C._run(["git"] + args, self.repo)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.fake_root = os.path.join(base, "fake-root")
        self.inner = Runner(dict(GREEN, **{"cut_preflight.py": (1, "cut-preflight: REFUSED\n")}))
        self.worktrees = []

    def outer(self, cmd, **kw):
        cmd = list(cmd)
        if cmd[0] != "git":
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch.object(tempfile, "tempdir", self.bs):
                code = _inner(cmd[2:], self.inner, self.fake_root)
            return subprocess.CompletedProcess(cmd, code, out.getvalue(), "")
        if cmd[1:3] == ["worktree", "add"]:
            self.worktrees.append(cmd)
            os.makedirs(cmd[-2])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1:3] == ["worktree", "remove"]:
            self.worktrees.append(cmd)
            shutil.rmtree(cmd[-1], ignore_errors=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return C._run(cmd, kw.get("cwd"), timeout=kw.get("timeout"))

    def test_a_refused_preflight_is_recorded_not_ready_in_minutes(self):
        out = io.StringIO()
        started = time.monotonic()
        with contextlib.redirect_stdout(out):
            code = CR.main(["--version", V], root=self.repo, runner=self.outer)
        seconds = time.monotonic() - started
        last = out.getvalue().strip().splitlines()[-1]
        self.assertTrue(last.startswith("REHEARSAL NOT-READY"), out.getvalue())
        self.assertEqual(code, 1)
        self.assertNotIn("cut_v1.0.0.sh", self.inner.labels)
        self.assertEqual(chain_calls(self.inner.labels), [], self.inner.labels)
        self.assertLess(seconds, 600)


class ANoDataRowSparesTheExpensiveRows(unittest.TestCase):
    """The preflight's two expensive rows run only when every earlier row is OK."""

    def run_all(self, answers):
        tp = TP.CheapestFirst("test_all_green_is_exit_zero_and_the_virgin_gate_ran")
        self.addCleanup(tp.doCleanups)
        return tp._all(answers)

    def assert_spared(self, answers, row):
        res, built = self.run_all(answers)
        rows = dict((n, (v, d)) for v, n, d in res)
        self.assertEqual(rows[row][0], P.NODATA, res)
        for name in ("plugin-bumps", "virgin-gate"):
            with self.subTest(row=row, spared=name):
                self.assertEqual(rows[name][0], P.NODATA, res)
                self.assertIn("not run", rows[name][1])
        self.assertEqual(built, [], "an expensive row built a tree after a NO-DATA row")
        self.assertEqual(P.exit_code(res), P.EXIT_NODATA, res)

    def test_an_unreadable_exception_audit_spares_both(self):
        self.assert_spared({"battery_exception_audit.py": (2, "NO-DATA\n")}, "exceptions")

    def test_every_other_cheap_no_data_row_spares_both(self):
        for answers, row in (({"close_ceremony_check.py": (2, "NO-DATA\n")}, "pack-clock"),
                             ({"readiness_gate.py": (0, "not json\n")}, "readiness-rows"),
                             ({"git log": (128, "fatal: bad revision\n")}, "changelog-text"),
                             ({"git ls-remote": (128, "fatal: unreachable\n")}, "public-remote")):
            with self.subTest(row=row):
                self.assert_spared(answers, row)

    def test_only_ok_rows_clear(self):
        self.assertTrue(P._cleared([(P.OK, "a", ""), (P.OK, "b", "")]))
        self.assertTrue(P._cleared([]))
        self.assertFalse(P._cleared([(P.OK, "a", ""), (P.NODATA, "b", "")]))
        self.assertFalse(P._cleared([(P.REFUSED, "a", "")]))
        self.assertFalse(P._cleared([(P.OK, "a", ""), ("MAYBE", "b", "")]))


KEY = "a" * 40
TREES = dict((d, "b" * 40) for d in CR.SCRIPT_DIRS)


class TheRowsAreReusedOnlyOnAnExactMatch(unittest.TestCase):
    """RC1.a (docs/plan/specs/RC1.md): the record carries rows, and a row is reusable only on its exact key, its
    home, a PASS verdict, a READY record younger than 24 hours and a log still hashing to the recorded sha256."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-cut-rows-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = time.time()
        self.n = 0

    def log(self, text="row log\n"):
        self.n += 1
        path = os.path.join(self.tmp, "row-%d.log" % self.n)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path, CR._sha256_file(path)

    def row(self, **over):
        path, sha = self.log()
        row = {"row": "export-real-home", "argv": ["python3", "scripts/export_public.py", "--dry-run"], "key": KEY,
               "home": "real", "verdict": "PASS", "seconds": 1.5, "log_path": path, "log_sha256": sha}
        row.update(over)
        return row

    def write(self, rows, verdict="READY", age_h=1.0):
        log, _sha = self.log("rehearsal log\n")
        return CR.record_rehearsal(V, HEAD, "c" * 40, 0 if verdict == "READY" else 1, verdict, log, 1.0, "d" * 64,
                                   directory=self.tmp, now=self.now - age_h * 3600, script_trees=TREES, rows=rows)

    def record(self, rows, verdict="READY", age_h=1.0):
        rec, why = CR.load_record(self.write(rows, verdict, age_h))
        self.assertEqual(why, "")
        return rec

    def ask(self, rec, key=KEY, home="real", row="export-real-home"):
        return CR.reusable_row(rec, row, key, home, now=self.now)

    def rewrite(self, path, **fields):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for k, v in fields.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    # REQ-RC1-03, one case per rule
    def test_a_matching_row_is_reused(self):
        rec = self.record([self.row()])
        self.assertEqual(rec["schema"], "cv1.rehearsal.2")
        got, why = self.ask(rec)
        self.assertEqual(why, "")
        self.assertEqual(got["key"], KEY)

    def test_a_key_differing_in_the_last_byte_is_not_reused(self):
        got, why = self.ask(self.record([self.row()]), key=KEY[:-1] + "b")
        self.assertIsNone(got)
        self.assertIn("key", why)

    def test_a_real_row_asked_as_empty_is_not_reused(self):
        got, why = self.ask(self.record([self.row()]), home="empty")
        self.assertIsNone(got)
        self.assertTrue(why)

    def test_a_fail_or_no_data_row_is_not_reused(self):
        for verdict in ("FAIL", "NO-DATA"):
            with self.subTest(verdict=verdict):
                got, why = self.ask(self.record([self.row(verdict=verdict)]))
                self.assertIsNone(got)
                self.assertIn(verdict, why)

    def test_a_25_h_record_is_not_reused_and_a_23_h_one_is(self):
        got, why = self.ask(self.record([self.row()], age_h=25.0))
        self.assertIsNone(got)
        self.assertIn("stale", why)
        got, why = self.ask(self.record([self.row()], age_h=23.0))
        self.assertEqual(why, "")

    def test_a_record_from_the_future_is_not_reused(self):
        got, why = self.ask(self.record([self.row()], age_h=-1.0))
        self.assertIsNone(got)
        self.assertIn("future", why)

    def test_an_altered_or_removed_log_is_not_reused(self):
        row = self.row()
        rec = self.record([row])
        with open(row["log_path"], "a", encoding="utf-8") as fh:
            fh.write("one more line\n")
        got, why = self.ask(rec)
        self.assertIsNone(got)
        self.assertIn("altered", why)
        os.remove(row["log_path"])
        got, why = self.ask(rec)
        self.assertIsNone(got)
        self.assertIn("unreadable", why)

    # REQ-RC1-04
    def test_a_not_ready_or_no_data_record_never_yields_a_row(self):
        for verdict in ("NOT-READY", "NO-DATA"):
            with self.subTest(verdict=verdict):
                got, why = self.ask(self.record([self.row()], verdict=verdict))
                self.assertIsNone(got)
                self.assertIn(verdict, why)

    def test_the_last_row_of_a_name_and_home_wins(self):
        got, why = self.ask(self.record([self.row(), self.row(verdict="FAIL")]))
        self.assertIsNone(got, "an earlier PASS row outranked the later FAIL")
        got, why = self.ask(self.record([self.row(verdict="FAIL"), self.row()]))
        self.assertEqual(why, "")
        got, why = self.ask(self.record([self.row(), self.row(home="empty", key="f" * 40)]))
        self.assertEqual(why, "", "a row of another home hid this one")

    def test_no_rows_and_unknown_names_reuse_nothing(self):
        got, why = self.ask(self.record([]))
        self.assertIsNone(got)
        got, why = self.ask(self.record([self.row()]), row="not-a-row")
        self.assertIsNone(got)

    def test_a_hostile_record_or_argument_returns_a_reason(self):
        rec = self.record([self.row()])
        for bad in (None, [], "x", {}, dict(rec, rows="x"), dict(rec, finished_at=7), dict(rec, rows=[None])):
            with self.subTest(record=bad):
                got, why = CR.reusable_row(bad, "export-real-home", KEY, "real", now=self.now)
                self.assertIsNone(got)
                self.assertTrue(why)
        for key in (None, "", KEY.upper(), KEY + "0", 7):
            with self.subTest(key=key):
                self.assertIsNone(CR.reusable_row(rec, "export-real-home", key, "real", now=self.now)[0])
        self.assertIsNone(CR.reusable_row(rec, "export-real-home", KEY, "real", now=float("nan"))[0])

    # the row format
    def test_validate_row_names_the_first_broken_rule_and_never_raises(self):
        self.assertEqual(CR.validate_row(self.row()), "")
        good = self.row()
        bad = [None, [], "row", dict(good, row="push"), dict(good, argv="python3 x"), dict(good, argv=["a", 1]),
               dict(good, key=KEY[:39]), dict(good, key=KEY.upper()), dict(good, home="virgin"),
               dict(good, verdict="OK"), dict(good, seconds=-1), dict(good, seconds=float("nan")),
               dict(good, seconds=True), dict(good, log_path=7), dict(good, log_path=""),
               dict(good, log_sha256="0" * 63), dict(good, extra=1), dict((k, v) for k, v in good.items() if k != "key"),
               dict(good, row=["export-real-home"])]
        for row in bad:
            with self.subTest(row=row):
                why = CR.validate_row(row)
                self.assertTrue(why)
                with self.assertRaises(ValueError):
                    self.write([row])

    def test_row_line_and_parse_rows_round_trip(self):
        good, other = self.row(), self.row(row="virgin-gate", home="empty")
        text = "\n".join(["chatter", CR.row_line(good), "CUTROW {not json", CR.row_line(dict(good, home="moon")),
                          "  " + CR.row_line(other), CR.row_line(other), "done"])
        rows, problems = CR.parse_rows(text)
        self.assertEqual(rows, [good, other])
        self.assertEqual(len(problems), 2, problems)
        rows, problems = CR.parse_rows(CR.row_line(good) + "\n" + CR.row_line(good) + "\n")
        self.assertEqual(rows, [good, good], "a line printed twice is two rows; the last wins at reuse")
        self.assertEqual(CR.parse_rows(None), ([], ["the output is not text"]))

    def test_the_export_tree_line_is_one_full_line(self):
        self.assertEqual(CR.EXPORT_TREE_RE.findall("x\nEXPORT-TREE: %s\n" % KEY), [KEY])
        self.assertEqual(CR.EXPORT_TREE_RE.findall("EXPORT-TREE: %s0\nEXPORT-TREE: %s\n" % (KEY, KEY[:39])), [])

    # REQ-RC1-02
    def test_a_rehearsal_1_record_still_reads_with_no_rows(self):
        path = self.write([self.row()])
        self.rewrite(path, schema="cv1.rehearsal.1", rows=None)
        rec, why = CR.load_record(path)
        self.assertEqual(why, "")
        self.assertEqual(rec["rows"], [])
        self.assertIsNone(self.ask(rec)[0])
        records, why = CR._load_records(self.tmp, V)
        self.assertEqual(why, "")
        self.assertEqual([r["rows"] for r in records], [[]])

    def test_a_malformed_rehearsal_2_record_refuses(self):
        for rows in ("x", [{"row": "virgin-gate"}], None):
            with self.subTest(rows=rows):
                path = self.write([self.row()])
                self.rewrite(path, rows=rows)
                rec, why = CR.load_record(path)
                self.assertIsNone(rec)
                self.assertIn("malformed", why)
                records, why = CR._load_records(self.tmp, V)
                self.assertEqual(records, [])
                self.assertIn("malformed", why)
                os.remove(path)

    def test_an_unknown_schema_refuses(self):
        for schema in ("cv1.rehearsal.0", "cv1.rehearsal.3", None, 2):
            with self.subTest(schema=schema):
                path = self.write([self.row()])
                self.rewrite(path, schema=schema)
                self.assertIsNone(CR.load_record(path)[0])
                self.assertEqual(CR._load_records(self.tmp, V)[0], [])
                os.remove(path)

    def test_a_missing_corrupt_or_truncated_record_returns_the_reason(self):
        path = self.write([self.row()])
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text[: len(text) // 2])
        for p in (path, os.path.join(self.tmp, "absent.json"), self.tmp, "", None):
            with self.subTest(path=p):
                rec, why = CR.load_record(p)
                self.assertIsNone(rec)
                self.assertTrue(why)


class TheRehearsalKeepsOnlyRowsWithTheirLog(unittest.TestCase):
    """REQ-RC1-01: the rehearsal parent keeps a CUTROW line of its child only when the row's log still hashes to the
    row's log_sha256; a dropped row is named in the record's note."""

    setUp = TheComposedRehearsal.setUp

    def outer(self, cmd, **kw):
        cmd = list(cmd)
        if cmd[0] != "git":
            return subprocess.CompletedProcess(cmd, 0, self.child_out, "")
        return TheComposedRehearsal.outer(self, cmd, **kw)

    def test_a_good_and_a_bad_sha_row_yield_one_row_and_a_note(self):
        good_log, bad_log = os.path.join(self.bs, "good.log"), os.path.join(self.bs, "bad.log")
        for path in (good_log, bad_log):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("row output\n")
        base = {"argv": ["sh", "scripts/required_fast.sh"], "key": KEY, "home": "real", "verdict": "PASS",
                "seconds": 2.0}
        good = dict(base, row="required-fast-hub", log_path=good_log, log_sha256=CR._sha256_file(good_log))
        bad = dict(base, row="export-real-home", log_path=bad_log, log_sha256="0" * 64)
        self.child_out = "\n".join([CR.row_line(good), CR.row_line(bad), "CUTROW garbage", "READY for %s" % V, ""])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = CR.main(["--version", V], root=self.repo, runner=self.outer)
        last = out.getvalue().strip().splitlines()[-1]
        self.assertEqual(code, 0, out.getvalue())
        rec, why = CR.load_record(last.split(" record ", 1)[1])
        self.assertEqual(why, "")
        self.assertEqual(rec["rows"], [good])
        self.assertIn("export-real-home", rec.get("note", ""))
        self.assertIn("CUTROW", rec.get("note", ""))


FR1E_EXPORT_TREE = "e" * 40


class TestFR1E(unittest.TestCase):
    """FR1.e (docs/plan/specs/FR1.md): a --check rehearsal reuses a finished PASS row of an earlier rehearsal on its
    exact key, and stops the long rows after a refusal it cannot change."""

    def setUp(self):
        base = tempfile.mkdtemp(prefix="fr1e-")
        self.addCleanup(shutil.rmtree, base, True)
        self.base, self.ev = base, os.path.join(base, "ev")
        os.makedirs(self.ev)
        patcher = mock.patch.dict(os.environ, {"BROTHER_CUT_TEST": "1", "BROTHER_CUT_EVIDENCE_DIR": self.ev})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.n = 0

    def row(self, name="required-fast-hub", key=KEY, home="real", verdict="PASS", text="row output\n"):
        self.n += 1
        log = os.path.join(self.base, "row%d.log" % self.n)
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(text)
        return {"row": name, "argv": ["sh", "scripts/required_fast.sh"], "key": key, "home": home,
                "verdict": verdict, "seconds": 1.0, "log_path": log, "log_sha256": CR._sha256_file(log)}

    def record(self, rows, verdict="NOT-READY", age_s=60):
        self.n += 1
        sha = ("%040x" % self.n)
        kw = {}
        if verdict == "READY":
            kw["script_trees"] = TREES
            log = os.path.join(self.base, "rec%d.log" % self.n)
            with open(log, "w", encoding="utf-8") as fh:
                fh.write("READY for %s\n" % V)
        else:
            log = ""
        return CR.record_rehearsal(V, sha, "f" * 40, 0 if verdict == "READY" else 1, verdict, log, 1.0, "d" * 64,
                                   self.ev, now=time.time() - age_s, rows=rows, **kw)

    def reuse(self, key=KEY, name="required-fast-hub", home="real"):
        return CR.reusable_check_row(V, name, key, home, self.ev)

    def test_a_pass_row_of_a_not_ready_record_is_reused_on_check(self):
        for verdict in ("NOT-READY", "READY"):
            with self.subTest(record=verdict):
                shutil.rmtree(self.ev)
                os.makedirs(self.ev)
                held = self.row()
                self.record([held], verdict)
                got, why = self.reuse()
                self.assertEqual((got, why), (held, ""))
        held = self.row()
        self.record([held])
        code, out, labels, _ = self.check({})
        self.assertNotIn("required_fast.sh", labels, out)
        self.assertIn("REUSED required-fast-hub %s %s" % (KEY[:12], held["log_path"]), out)

    def test_a_fail_row_is_never_reused(self):
        for verdict in ("FAIL", "NO-DATA"):
            with self.subTest(row=verdict):
                shutil.rmtree(self.ev)
                os.makedirs(self.ev)
                self.record([self.row(verdict=verdict)])
                got, why = self.reuse()
                self.assertIsNone(got)
                self.assertIn("not PASS", why)

    def test_a_row_measured_on_other_bytes_runs(self):
        self.record([self.row(key=KEY[:12] + "b" * 28)])
        got, why = self.reuse()
        self.assertIsNone(got, why)
        self.assertIn("no record holds", why)

    def test_a_row_older_than_24_h_runs(self):
        self.record([self.row()], age_s=25 * 3600)
        got, why = self.reuse()
        self.assertIsNone(got)
        self.assertIn("stale", why)
        shutil.rmtree(self.ev)
        os.makedirs(self.ev)
        self.record([self.row()], age_s=23 * 3600)
        self.assertEqual(self.reuse()[1], "")

    def test_a_record_from_the_future_yields_no_row(self):
        self.record([self.row()], age_s=-3600)
        got, why = self.reuse()
        self.assertIsNone(got)
        self.assertIn("future", why)

    def test_an_altered_row_log_runs(self):
        held = self.row()
        self.record([held])
        with open(held["log_path"], "a", encoding="utf-8") as fh:
            fh.write("edited afterwards\n")
        got, why = self.reuse()
        self.assertIsNone(got)
        self.assertIn("altered", why)
        os.remove(held["log_path"])
        got, why = self.reuse()
        self.assertIsNone(got)
        self.assertIn("missing or unreadable", why)

    def test_a_no_data_record_yields_no_row(self):
        self.record([self.row()], "NO-DATA")
        records, reasons = CR.row_sources(V, self.ev)
        self.assertEqual(records, [])
        self.assertIn("NO-DATA", reasons[0])
        self.assertIsNone(self.reuse()[0])

    def test_a_newer_fail_beats_an_older_pass_row(self):
        self.record([self.row()], age_s=3600)
        self.record([self.row(verdict="FAIL")], age_s=60)
        got, why = self.reuse()
        self.assertIsNone(got)
        self.assertIn("FAIL", why)

    def test_a_newer_pass_beats_an_older_fail_row(self):
        self.record([self.row(verdict="FAIL")], age_s=3600)
        held = self.row()
        self.record([held], age_s=60)
        self.assertEqual(self.reuse(), (held, ""))

    def test_a_corrupt_record_beside_a_good_one_does_not_hide_it(self):
        held = self.row()
        self.record([held])
        prefix = os.path.join(self.ev, "rehearsal-%s-" % V)
        with open(prefix + "zz-1.json", "w", encoding="utf-8") as fh:
            fh.write('{"schema": "cv1.rehearsal.2", "ver')
        with open(prefix + "yy-1.json", "w", encoding="utf-8") as fh:
            json.dump({"schema": "cv1.rehearsal.2", "rows": "no"}, fh)
        records, reasons = CR.row_sources(V, self.ev)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(reasons), 2, reasons)
        self.assertEqual(self.reuse(), (held, ""))
        # the strict reader that decides a cut still refuses the folder
        self.assertNotEqual(CR._load_records(self.ev, V)[1], "")

    def test_an_empty_or_missing_folder_reuses_nothing(self):
        got, why = self.reuse()
        self.assertIsNone(got)
        self.assertTrue(why)
        got, why = CR.reusable_check_row(V, "required-fast-hub", KEY, "real", os.path.join(self.base, "absent"))
        self.assertIsNone(got)
        self.assertIn("unreadable", why)

    def test_hostile_arguments_to_reusable_check_row_return_a_reason(self):
        self.record([self.row()])
        cases = [(V, None, KEY, "real"), (V, "required-fast-hub", None, "real"), (V, "required-fast-hub", KEY, []),
                 (V, [], KEY, "real"), (V, "required-fast-hub", KEY[:39], "real"),
                 (V, "required-fast-hub", KEY.upper(), "real"), (V, "required-fast-hub", 123, "real"),
                 (V, "nope", KEY, "real"), (V, "required-fast-hub", KEY, "nowhere"), (None, "required-fast-hub", KEY, "real"),
                 ("abc", "required-fast-hub", KEY, "real"), (["9.9.9"], "required-fast-hub", KEY, "real")]
        for args in cases:
            with self.subTest(args=args):
                got, why = CR.reusable_check_row(*args, directory=self.ev)
                self.assertIsNone(got)
                self.assertTrue(isinstance(why, str) and why)
        for now in ("x", float("nan"), [1]):
            with self.subTest(now=now):
                got, why = CR.reusable_check_row(V, "required-fast-hub", KEY, "real", self.ev, now)
                self.assertIsNone(got)
                self.assertIn("clock", why)
        got, why = CR.reusable_check_row(V, "required-fast-hub", KEY, "real", 123)
        self.assertIsNone(got)

    def check(self, answers, argv=None, handle=None):
        """(code, stdout, labels, mocks) of cut.main --check with the fake rehearsal tree KEY. Every long seam is a
        mock so a test can say whether it was reached."""
        runner = Runner(dict(GREEN, **dict({"git rev-parse HEAD^{tree}": (0, KEY + "\n")}, **answers)))
        out = io.StringIO()
        with mock.patch.object(C, "_load_bm_store", return_value=(None, None, "absent")), \
                mock.patch.object(C, "release_paths", return_value=["scripts/"]), \
                mock.patch.object(C, "export_tree", return_value=(FR1E_EXPORT_TREE, "")), \
                mock.patch.object(C, "export_rows", return_value=(True, ["export rows: stubbed"])) as rows, \
                mock.patch.object(C, "start_canary", return_value=handle), \
                mock.patch.object(C, "stop_canary") as stopped, \
                mock.patch.object(C, "await_canary", return_value=(True, ["linux canary: stubbed"])) as awaited, \
                mock.patch.object(tempfile, "tempdir", self.base), contextlib.redirect_stdout(out):
            code = C.main(["--check", "--version", V] + list(argv or []), root="/fake/root", runner=runner)
        return code, out.getvalue(), runner.labels, {"export_rows": rows, "stop_canary": stopped,
                                                      "await_canary": awaited}

    def test_full_chain_runs_every_row(self):
        self.record([self.row()])
        code, out, labels, _ = self.check({}, ["--full-chain"])
        self.assertIn("required_fast.sh", labels, out)
        self.assertNotIn("REUSED required-fast-hub", out)
        code, out, labels, _ = self.check({})
        self.assertNotIn("required_fast.sh", labels, out)

    def test_a_refused_plugin_bump_gate_skips_required_fast_but_runs_the_cheap_rows(self):
        code, out, labels, mocks = self.check({"plugin_bump_gate.py": (1, "plugin bump gate: REFUSED\n")})
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertNotIn("required_fast.sh", labels, out)
        self.assertIn("required_fast: skipped, plugin_bump_gate refused earlier (stop early for long rows)", out)
        for cheap in ("refresh_cut.py", "release_invariant.py", "donecheck_u8.py", "retire_catalogs.py"):
            self.assertIn(cheap, labels)
        self.assertIn("NOT READY for %s" % V, out)
        self.assertNotIn("READY for %s:" % V, out.replace("NOT READY", ""))
        mocks["export_rows"].assert_not_called()

    def test_a_refused_plugin_bump_gate_still_runs_required_fast_under_full_chain(self):
        code, out, labels, _ = self.check({"plugin_bump_gate.py": (1, "")}, ["--full-chain"])
        self.assertIn("required_fast.sh", labels, out)
        self.assertEqual(code, C.EXIT_REFUSED)

    def test_a_refused_hub_row_ends_the_chain_before_the_export_rows(self):
        code, out, labels, mocks = self.check({"required_fast.sh": (1, "FAILED: a check\n")})
        self.assertEqual(code, C.EXIT_REFUSED, out)
        mocks["export_rows"].assert_not_called()
        self.assertIn("refresh_cut.py", labels)

    def test_a_refused_row_stops_the_canary(self):
        handle = object()
        code, out, labels, mocks = self.check({"plugin_bump_gate.py": (1, "")}, handle=handle)
        mocks["stop_canary"].assert_called_once_with(handle)
        mocks["await_canary"].assert_not_called()
        code, out, labels, mocks = self.check({}, handle=handle)
        mocks["await_canary"].assert_called_once()
        mocks["stop_canary"].assert_not_called()

    def test_export_rows_are_reused_on_the_export_tree_id(self):
        rows = [self.row("export-real-home", FR1E_EXPORT_TREE, "real"), self.row("virgin-gate", FR1E_EXPORT_TREE, "empty")]
        self.record(rows)
        code, out, labels, mocks = self.check({})
        self.assertIn("REUSED export-real-home %s %s" % (FR1E_EXPORT_TREE[:12], rows[0]["log_path"]), out)
        self.assertIn("REUSED virgin-gate %s %s" % (FR1E_EXPORT_TREE[:12], rows[1]["log_path"]), out)
        mocks["export_rows"].assert_not_called()

    def test_an_unreadable_export_tree_reuses_nothing_and_runs_the_rows(self):
        self.record([self.row("export-real-home", FR1E_EXPORT_TREE, "real")])
        with mock.patch.object(C, "export_tree", return_value=("", "no tree")):
            runner = Runner(dict(GREEN, **{"git rev-parse HEAD^{tree}": (0, KEY + "\n")}))
            with mock.patch.object(C, "export_rows", return_value=(False, ["NO-DATA: stub"])) as rows:
                ok, lines = C.reused_or_run_export_rows("/fake/root", V, C.CheckReuse(V, self.ev), "", runner)
        self.assertFalse(ok)
        rows.assert_called_once()

    def test_a_record_dict_still_routes_to_reusable_row(self):
        held = self.row()
        record = {"verdict": "READY", "finished_at": CR._iso(time.time()), "rows": [held]}
        self.assertEqual(C._reusable(record, "required-fast-hub", KEY, "real"), (held, ""))
        self.assertIsNone(C._reusable(None, "required-fast-hub", KEY, "real")[0])
        self.assertEqual(C.CheckReuse(V, self.ev).row("required-fast-hub", KEY, "real")[0], None)


if __name__ == "__main__":
    unittest.main()
