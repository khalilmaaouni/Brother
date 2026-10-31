#!/usr/bin/env python3
"""PC1 and PC1H: the one proof source, exercised rather than named.

WHY THIS FILE EXISTS, and it is not a second copy of the frozen harness.
scripts/test_proof_source.py is the units' done check and it is frozen by sha256 in the plan
(HARN1 refuses a lane that edits it). Every assertion in it is a symbol lookup through ast or a
substring grep over a source file, so it cannot tell the work from no work. Measured on
2026-10-29 by applying the seven mutations the specs name, each by a text edit: it killed ONE of
seven and let SIX survive:

  M-PC1C-LONG-LINE       survived: test_the_line_fits only asks whether last_run_line is defined
  M-PC1C-TWO-SOURCES     survived: "last_run_line" also sits in controller comments
  M-PC1HA-SECOND-RENDER  survived: a second row loop beside the verdict_rows call keeps the word
  M-PC1HA-ORDER          survived: test_same_text only asks whether verdict_rows is defined
  M-PC1HB-SCAN           survived: test_index_is_read_not_scanned only asks whether read_index exists
  M-PC1HB-NO-CALL        survived: "update_index" also sits in brother_run comments
  M-PC1HB-LOAD-STORE     killed (the one text grep that happens to bite)

So the frozen harness stays untouched and this file is the adversary beside it, the same shape as
scripts/test_consent_gate.py beside scripts/test_first_task.py (D13, docs/handover/2026-10-28/
05-DECISIONS-TAKEN.md). Every case here CALLS the thing and judges what it did. The seven
mutations are re-run against THIS file alone and each must turn a named case red.

HERMETIC. Every HOME is a fresh temp directory; nothing here reads or writes the real
~/.brother, and every subprocess gets HOME pointed at a temp directory too.

    python3 -B scripts/test_proof_source_behaviour.py

Python 3.9 compatible, standard library only.
"""
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOLS = os.path.join(ROOT, "products", "brothermode", "tools")
sys.path.insert(0, HERE)
import proof_card  # noqa: E402

RESULT = ("BROTHER RESULT: DONE | units=3 PASS=3 FAIL=0 NO-DATA=0 | release=NOT_REQUESTED | "
          "next=review-receipt")
RESULT_LINE = "DONE | units=3 PASS=3 FAIL=0 NO-DATA=0 | release=NOT_REQUESTED | next=review-receipt"


def load_tool(name):
    spec = importlib.util.spec_from_file_location("%s_for_behaviour" % name,
                                                  os.path.join(TOOLS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def function_node(path, name):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("%s defines no %s" % (path, name))


def calls_of(node, attr):
    """Calls to `attr`, spelled `x.attr(...)` or bare `attr(...)`."""
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Attribute) and n.func.attr == attr)
                 or (isinstance(n.func, ast.Name) and n.func.id == attr))]


class Home(unittest.TestCase):
    """A fresh HOME per case, with helpers to put rows and a night file in it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="proof-source-")
        self.addCleanup(self._tmp.cleanup)
        self.home = os.path.realpath(self._tmp.name)

    def consent(self, home=None):
        cfg = os.path.join(home or self.home, ".brotherme")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"setup_complete": True}, fh)

    def run_dir(self, name, log=None):
        path = os.path.join(self.home, "runs", name)
        os.makedirs(path)
        if log is not None:
            with open(os.path.join(path, "run.log"), "w", encoding="utf-8") as fh:
                fh.write(log)
        return path

    def raw_row(self, run_dir, result=RESULT, t=None):
        row = {"run_dir": run_dir, "result": result}
        if t is not None:
            row["time"] = t
        path = proof_card.index_path(self.home)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "ab") as fh:
            fh.write(("\n" + json.dumps(row) + "\n").encode("utf-8"))

    def torn_tail(self):
        with open(proof_card.index_path(self.home), "ab") as fh:
            fh.write(b'{"run_dir": "/b", "res')

    def night(self, text, mtime=None):
        path = os.path.join(self.home, ".brother", "last-night.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(text if isinstance(text, bytes) else text.encode("utf-8"))
        if mtime is not None:
            os.utime(path, (mtime, mtime))


class VerdictRows(unittest.TestCase):
    """PC1H.a. receipt_door.verdict_lines renders its rows through proof_card.verdict_rows."""

    VIEW = {
        "state": "DONE", "release": "NOT_REQUESTED", "next": "review-receipt",
        "counts": {"PASS": 1, "FAIL": 1, "NO-DATA": 1}, "readable": True, "why": "ok",
        "units": [
            {"unit": "U1", "files": ["b.py", "a.py"], "command": "pytest -q", "exit_code": 0,
             "verdict": "PASS", "unproven_reason": None},
            {"unit": "U2", "files": [], "command": "make", "exit_code": 1, "verdict": "FAIL",
             "unproven_reason": "it  failed\n badly"},
            {"unit": "A0", "files": None, "command": "x", "exit_code": None,
             "verdict": "NO-DATA", "unproven_reason": None},
        ],
    }
    #: The bytes receipt_door.verdict_lines printed for these units BEFORE PC1H.a, captured from
    #: the old inline loop: the view's own unit order, the view's own file order, never sorted.
    ROWS = [
        "UNIT U1 | FILE b.py | CHECK pytest -q | EXIT 0 | PASS",
        "UNIT U1 | FILE a.py | CHECK pytest -q | EXIT 0 | PASS",
        "UNIT U2 | FILE none | CHECK make | EXIT 1 | FAIL | UNPROVEN: it failed badly",
        "UNIT A0 | FILE none | CHECK x | EXIT None | NO-DATA | UNPROVEN: None",
    ]

    def test_the_rows_keep_the_old_text_and_order(self):
        """REQ-PC1HA-01. A sorted or reordered renderer changes this list."""
        self.assertEqual(self.ROWS, proof_card.verdict_rows(self.VIEW))

    def test_verdict_lines_is_the_result_line_plus_those_rows(self):
        import receipt_door
        lines = receipt_door.verdict_lines(self.VIEW)
        self.assertTrue(lines[0].startswith("BROTHER RESULT: DONE | units=3 "), lines[0])
        self.assertEqual(self.ROWS, lines[1:])

    def test_receipt_door_has_no_row_loop_of_its_own(self):
        """REQ-PC1HA-02. verdict_lines holds no loop and no comprehension, and it calls
        proof_card.verdict_rows exactly once. A second renderer beside the call fails the first
        half; a dropped call fails the second."""
        node = function_node(os.path.join(HERE, "receipt_door.py"), "verdict_lines")
        loops = [n for n in ast.walk(node)
                 if isinstance(n, (ast.For, ast.While, ast.ListComp, ast.GeneratorExp))]
        self.assertEqual([], loops, "verdict_lines renders rows itself")
        self.assertEqual(1, len(calls_of(node, "verdict_rows")),
                         "verdict_lines does not call verdict_rows")

    def test_the_door_binds_the_renderer_as_a_public_name_defined_in_proof_card(self):
        """The J4 substitution probe finds a dependency through a callable on the door whose
        __globals__ file is proof_card's. A module attribute reference is invisible to it."""
        import receipt_door
        self.assertIs(proof_card.verdict_rows, receipt_door.verdict_rows)
        self.assertEqual(proof_card.__file__, receipt_door.verdict_rows.__globals__["__file__"])

    def test_the_same_view_renders_the_same_bytes(self):
        """REQ-PC1HA-03."""
        self.assertEqual(proof_card.verdict_rows(self.VIEW), proof_card.verdict_rows(self.VIEW))

    def test_edges_empty_junk_and_a_unit_without_an_id(self):
        self.assertEqual([], proof_card.verdict_rows({"units": []}))
        self.assertEqual([], proof_card.verdict_rows({"units": "x"}))
        self.assertEqual([], proof_card.verdict_rows(None))
        rows = proof_card.verdict_rows({"units": [{"verdict": "PASS", "files": ["f"]}]})
        self.assertEqual(["UNIT NO-DATA | FILE f | CHECK NO-DATA | EXIT NO-DATA | PASS"], rows)
        # A unit with no verdict is never a PASS.
        rows = proof_card.verdict_rows({"units": [{"unit": "U"}]})
        self.assertTrue(rows[0].endswith("| NO-DATA | UNPROVEN: NO-DATA"), rows)


class RunIndex(Home):
    """PC1H.b. The index is written by update_index and read newest first without a scan."""

    def test_update_index_appends_one_row_per_call_with_the_logged_result(self):
        run = self.run_dir("r1", "noise\n%s\n" % RESULT)
        self.assertTrue(proof_card.update_index(run, self.home))
        self.assertTrue(proof_card.update_index(run, self.home))
        rows = proof_card.read_index(self.home)
        self.assertEqual(2, len(rows))
        self.assertEqual((run, RESULT), (rows[0]["run_dir"], rows[0]["result"]))
        self.assertIsInstance(rows[0]["time"], float)

    def test_a_run_with_no_result_line_is_no_data_not_a_neighbours_line(self):
        run = self.run_dir("r1", "nothing here\n")
        proof_card.update_index(run, self.home)
        self.assertTrue(proof_card.read_index(self.home)[0]["result"].startswith("NO-DATA"))

    def test_the_last_result_line_in_the_log_wins(self):
        run = self.run_dir("r1", "BROTHER RESULT: UNVERIFIED | units=0\n%s\n" % RESULT)
        proof_card.update_index(run, self.home)
        self.assertEqual(RESULT, proof_card.read_index(self.home)[0]["result"])

    def test_an_unwritable_home_returns_false(self):
        blocker = os.path.join(self.home, "blocker")
        open(blocker, "w").close()
        self.assertFalse(proof_card.update_index(self.run_dir("r1", RESULT), blocker))

    def test_absent_index_is_empty_and_not_a_problem(self):
        self.assertEqual(([], 0, ""), proof_card.read_index_counted(self.home))
        self.assertEqual([], proof_card.read_index(self.home))

    def test_a_torn_last_line_is_skipped_and_counted_and_the_next_row_survives(self):
        self.raw_row("/a", t=10)
        self.torn_tail()
        proof_card.update_index(self.run_dir("r1", RESULT), self.home)
        rows, skipped, problem = proof_card.read_index_counted(self.home)
        self.assertEqual((2, 1, ""), (len(rows), skipped, problem))

    def test_newest_first_and_rows_without_a_time_sort_last_later_one_first(self):
        self.raw_row("/old", t=100)
        self.raw_row("/none")
        self.raw_row("/new", t=300)
        self.raw_row("/bad", t="soon")
        self.assertEqual(["/new", "/old", "/bad", "/none"],
                         [r["run_dir"] for r in proof_card.read_index(self.home)])

    def test_a_non_text_result_reads_no_data(self):
        self.raw_row("/a", result=5, t=1)
        self.assertTrue(proof_card.read_index(self.home)[0]["result"].startswith("NO-DATA"))

    def test_an_unreadable_index_reports_a_problem(self):
        os.makedirs(proof_card.index_path(self.home))
        rows, skipped, problem = proof_card.read_index_counted(self.home)
        self.assertEqual(([], 0), (rows, skipped))
        self.assertTrue(problem)

    def test_index_line_dates_the_row_and_marks_a_missing_folder(self):
        gone = os.path.join(self.home, "deleted")
        self.assertEqual("1970-01-01 %s (run folder missing)" % RESULT_LINE,
                         proof_card.index_line({"run_dir": gone, "result": RESULT, "time": 0}))
        self.assertEqual("undated x", proof_card.index_line(
            {"run_dir": self.home, "result": "x", "time": None}))

    def test_read_index_makes_no_directory_scan_and_opens_only_the_index(self):
        """REQ-PC1HB-03. A Python audit hook, installed after the imports, records every scan
        event, every process and every file open while read_index runs over 60 rows."""
        for i in range(60):
            self.raw_row(self.run_dir("r%02d" % i, RESULT), t=1000 + i)
        code = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "import proof_card\n"
            "events = []\n"
            "def hook(name, args):\n"
            "    if name in ('os.listdir', 'os.scandir', 'glob.glob', 'os.walk',\n"
            "                'subprocess.Popen'):\n"
            "        events.append(name)\n"
            "    if name == 'open':\n"
            "        events.append('open ' + str(args[0]))\n"
            "sys.addaudithook(hook)\n"
            "rows = proof_card.read_index(%r)\n"
            "print(json.dumps([len(rows), events]))\n" % (HERE, self.home))
        out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                             env=dict(os.environ, HOME=self.home), cwd=self.home)
        self.assertEqual(0, out.returncode, out.stderr)
        count, events = json.loads(out.stdout)
        self.assertEqual(60, count)
        self.assertEqual(["open " + proof_card.index_path(self.home)], events)

    def test_brother_run_calls_update_index_at_the_end_of_a_run(self):
        """REQ-PC1HB-02. Judged on the code: _main holds a call to proof_card.update_index that
        no constant if fences off. A removed call leaves no node, whatever a comment still says."""
        node = function_node(os.path.join(HERE, "brother_run.py"), "_main")
        calls = calls_of(node, "update_index")
        self.assertEqual(1, len(calls), "brother_run._main does not call update_index")
        self.assertEqual("proof_card", calls[0].func.value.id)
        for parent in ast.walk(node):
            if isinstance(parent, ast.If) and isinstance(parent.test, ast.Constant):
                self.assertNotIn(calls[0], list(ast.walk(parent)), "the call sits under a constant if")


class StatusLine(Home):
    """PC1H.b: bm_statusline reads the index, never the store, and prints NO-DATA with no index."""

    def setUp(self):
        super().setUp()
        self.consent()

    def status(self, home=None, stdin="{}"):
        """(stdout, opened file paths) of one real bm_statusline run, with an audit hook that
        records every file the run opened."""
        code = (
            "import sys, runpy\n"
            "opened = []\n"
            "def hook(name, args):\n"
            "    if name == 'open':\n"
            "        opened.append(str(args[0]))\n"
            "sys.addaudithook(hook)\n"
            "try:\n"
            "    runpy.run_path(%r, run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "sys.stderr.write('OPENED ' + repr(opened))\n" % os.path.join(TOOLS, "bm_statusline.py"))
        out = subprocess.run([sys.executable, "-B", "-c", code], input=stdin, capture_output=True,
                             text=True, env=dict(os.environ, HOME=home or self.home), cwd=self.home)
        self.assertEqual(0, out.returncode, out.stderr)
        opened = ast.literal_eval(out.stderr.split("OPENED ", 1)[1])
        return out.stdout, opened

    def test_the_line_is_the_newest_index_row(self):
        self.raw_row("/gone", t=100)
        self.raw_row(self.home, t=200)
        out, _opened = self.status()
        self.assertEqual("1970-01-01 %s\n" % RESULT_LINE, out)

    def test_no_index_prints_the_no_data_line(self):
        out, _opened = self.status()
        self.assertEqual("NO-DATA: no run recorded yet\n", out)

    def test_a_torn_last_line_is_skipped(self):
        self.raw_row(self.home, t=200)
        self.torn_tail()
        out, _opened = self.status()
        self.assertEqual("1970-01-01 %s\n" % RESULT_LINE, out)

    def test_without_consent_it_prints_nothing(self):
        self.raw_row(self.home, t=200)
        bare = tempfile.mkdtemp(prefix="proof-source-bare-")
        self.addCleanup(shutil.rmtree, bare, True)
        out, _opened = self.status(home=bare)
        self.assertEqual("", out)

    def test_garbage_stdin_prints_nothing_and_exits_zero(self):
        self.raw_row(self.home, t=200)
        out, _opened = self.status(stdin="not json")
        self.assertEqual("", out)

    def test_it_opens_no_store_and_exactly_two_data_files(self):
        """REQ-PC1HB-01 and the two file read bound. Every file the run opened is recorded."""
        self.raw_row(self.home, t=200)
        _out, opened = self.status()
        for path in opened:
            for forbidden in ("bm_store", "bm_lead", "store.sqlite3"):
                self.assertNotIn(forbidden, path)
        data = [os.path.basename(p) for p in opened
                if p.startswith(self.home) and not p.endswith((".py", ".pyc"))]
        self.assertEqual(["config.json", "runs-index.jsonl"], sorted(data), opened)

    def test_the_source_never_names_the_store(self):
        with open(os.path.join(TOOLS, "bm_statusline.py"), encoding="utf-8") as fh:
            self.assertNotIn("bm_store", fh.read())


class LastRunLine(Home):
    """PC1.c: one line, at most 160 bytes, the same in session start and the controller."""

    @classmethod
    def setUpClass(cls):
        cls.start = load_tool("bm_sessionstart")

    def test_the_line_fits_160_bytes_and_ends_in_three_dots(self):
        """REQ-PC1C-01. 300 two byte characters is 600 bytes; the cut stays on a character."""
        self.night("é" * 300)
        line = self.start.last_run_line(self.home)
        self.assertLessEqual(len(line.encode("utf-8")), 160)
        self.assertTrue(line.endswith("..."))

    def test_a_short_line_is_untouched(self):
        self.night("Night: 5 runs, 4 done\nsecond line\n")
        self.assertEqual("Night: 5 runs, 4 done", self.start.last_run_line(self.home))

    def test_an_index_line_over_the_limit_is_cut_too(self):
        self.raw_row(self.home, t=5, result="BROTHER RESULT: " + "x" * 400)
        self.assertEqual(160, len(self.start.last_run_line(self.home).encode("utf-8")))

    def test_the_night_wins_when_newer_than_the_newest_row(self):
        """REQ-PC1C-02."""
        self.raw_row(self.home, t=1000)
        self.night("Night: ok", mtime=2000)
        self.assertEqual("Night: ok", self.start.last_run_line(self.home))

    def test_the_row_wins_when_newer_than_the_night(self):
        self.raw_row(self.home, t=3000)
        self.night("Night: ok", mtime=2000)
        self.assertEqual("1970-01-01 %s" % RESULT_LINE, self.start.last_run_line(self.home))

    def test_nothing_at_all_is_an_empty_line(self):
        self.assertEqual("", self.start.last_run_line(self.home))

    def test_a_night_that_is_not_text_reads_no_data(self):
        self.night(b"\xff\xfe\x00bad")
        self.assertEqual("NO-DATA: last-night.txt is not readable text",
                         self.start.last_run_line(self.home))

    def test_an_unreadable_index_prints_nothing_but_the_night_still_shows(self):
        os.makedirs(proof_card.index_path(self.home))
        self.night("Night: ok")
        code = ("import importlib.util as u\n"
                "s = u.spec_from_file_location('ss', %r)\nm = u.module_from_spec(s)\n"
                "s.loader.exec_module(m)\nprint(m.last_run_line(%r))\n"
                % (os.path.join(TOOLS, "bm_sessionstart.py"), self.home))
        out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                             env=dict(os.environ, HOME=self.home))
        self.assertEqual("Night: ok\n", out.stdout)
        self.assertEqual(1, len(out.stderr.strip().splitlines()), out.stderr)

    def test_session_start_and_the_controller_print_the_same_line(self):
        """REQ-PC1C-03, judged by running both. The controller's helper takes its home from HOME,
        so the case points HOME at the fixture for the duration and restores it."""
        self.raw_row(self.home, t=3000)
        controller = load_tool("bm_controller")
        saved = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        try:
            from_controller = controller._last_run_line()
            from_start = self.start.last_run_line()
        finally:
            if saved is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved
        self.assertTrue(from_start)
        self.assertEqual(from_start, from_controller)

    def test_session_start_prints_the_line_in_its_output(self):
        """The hook itself, as a subprocess in a consented fixture HOME: the line lands in what
        session start says, once."""
        self.raw_row(self.home, t=3000)
        self.consent()
        project = os.path.join(self.home, "project")
        os.makedirs(project)
        env = {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "PYTHONDONTWRITEBYTECODE": "1"}
        out = subprocess.run([sys.executable, os.path.join(TOOLS, "bm_sessionstart.py")],
                             input=b"{}", capture_output=True, env=env, cwd=project, timeout=300)
        text = out.stdout.decode("utf-8", "replace")
        self.assertEqual(1, text.count("1970-01-01 DONE | units=3"), text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
