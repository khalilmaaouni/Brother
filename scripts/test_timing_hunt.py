"""timing_hunt.py driven on fixture test files in a temporary root: nothing outside that root is read, no thread or timer is
started, no process is spawned. Each case isolates one guard (one trigger, the assertion rule, the seam, the marker, NO-DATA,
the inventory, the baseline)."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import timing_hunt as T  # noqa: E402

BARE = '''import time, unittest
class TheWait(unittest.TestCase):
    def test_beats(self):
        out = io.StringIO()
        beat = run_heartbeat.Heartbeat(interval=1.0, stream=out).start()
        run(_SlowWorker(3.0))
        named = [l for l in out.getvalue().splitlines() if "u1" in l]
        self.assertGreaterEqual(len(named), 2)
'''


def one(body):
    """A file holding one test whose body is `body` (already indented by 8 spaces)."""
    return "import unittest\nclass C(unittest.TestCase):\n    def test_one(self):\n%s" % body


COUNT = "        self.assertEqual(len(lines), 4)\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-timing-hunt-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def put(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return rel

    def main(self, *argv):
        out = io.StringIO()
        with mock.patch.object(T, "ROOT", self.root), redirect_stdout(out):
            code = T.main(list(argv))
        return code, out.getvalue()


class TheStaticHunt(Base):
    def test_a_bare_real_thread_heartbeat_test_is_flagged_uncovered(self):
        found = T.scan_text("scripts/test_x.py", BARE)
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0].id, "scripts/test_x.py::TheWait.test_beats")
        self.assertEqual(found[0].triggers, ["heartbeat"])
        self.assertFalse(found[0].covered)
        self.assertEqual(found[0].line, 3)

    def test_the_seam_and_the_marker_cover_a_flagged_test(self):
        ticked = BARE.replace("run(_SlowWorker(3.0))", "run(_SlowWorker(3.0)); beat.tick()")
        self.assertTrue(T.scan_text("t.py", ticked)[0].covered, "a tick( call is a seam")
        marked = BARE.replace("    def test_beats", "    # timing: deterministic because the worker calls tick\n"
                                                    "    def test_beats")
        self.assertTrue(T.scan_text("t.py", marked)[0].covered, "the marker on the line above the def covers")
        inside = BARE.replace("        out = io", "        # timing: deterministic because tick\n        out = io")
        self.assertTrue(T.scan_text("t.py", inside)[0].covered, "the marker inside the test covers")
        clocked = BARE.replace("stream=out)", "stream=out, clock=lambda: 1.0)")
        self.assertTrue(T.scan_text("t.py", clocked)[0].covered, "a clock= keyword is a seam")
        faked = BARE.replace("run(_SlowWorker(3.0))", "run(_SlowWorker(3.0), sleep=fake_sleep)")
        self.assertTrue(T.scan_text("t.py", faked)[0].covered, "a fake_sleep name is a seam")

    def test_a_marker_with_no_seam_word_does_not_cover(self):
        empty = BARE.replace("        out = io", "        # timing: deterministic because   \n        out = io")
        self.assertFalse(T.scan_text("t.py", empty)[0].covered)

    def test_a_tick_in_another_test_does_not_cover_this_one(self):
        two = BARE + "    def test_other(self):\n        beat.tick()\n"
        found = T.scan_text("t.py", two)
        self.assertEqual([f.test for f in found], ["TheWait.test_beats"])
        self.assertFalse(found[0].covered)

    def test_a_count_assertion_in_a_same_class_helper_is_seen(self):
        text = ("import unittest\nclass C(unittest.TestCase):\n"
                "    def test_beats(self):\n"
                "        beat = Heartbeat(interval=1.0).start()\n"
                "        self._check(out)\n"
                "    def _check(self, out):\n"
                "        self.assertGreaterEqual(len(out), 2)\n")
        found = T.scan_text("t.py", text)
        self.assertEqual([(f.test, f.covered) for f in found], [("C.test_beats", False)])


class EachTrigger(Base):
    """One fixture per trigger, each shown flagged WITH a count or order assertion and not flagged WITHOUT one."""

    TRIGGERS = {
        "heartbeat": "        b = Heartbeat(interval=2, stream=out)\n        b.start()\n",
        "timer": "        threading.Timer(0.5, fire).start()\n",
        "thread": "        threading.Thread(target=go).start()\n        time.sleep(0.2)\n",
        "alarm": "        signal.alarm(1)\n",
        "select": "        select.select([r], [], [], 0.5)\n",
    }

    def test_each_trigger_flags_only_with_a_count_or_order_assertion(self):
        for name, body in self.TRIGGERS.items():
            with self.subTest(trigger=name):
                found = T.scan_text("t.py", one(body + COUNT))
                self.assertEqual([f.triggers for f in found], [[name]])
                self.assertEqual(T.scan_text("t.py", one(body + "        self.assertIn('x', out)\n")), [],
                                 "a trigger with no count or order assertion is not flagged")

    def test_index_and_count_methods_are_order_and_count_assertions(self):
        body = self.TRIGGERS["timer"]
        self.assertEqual(len(T.scan_text("t.py", one(body + "        self.assertLess(xs.index(a), xs.index(b))\n"))), 1)
        self.assertEqual(len(T.scan_text("t.py", one(body + "        self.assertEqual(xs.count('u1'), 2)\n"))), 1)
        self.assertEqual(len(T.scan_text("t.py", one(body + "        assert len(xs) == 2\n"))), 1)

    def test_a_module_level_test_is_scanned(self):
        text = "def test_free():\n    signal.alarm(1)\n    assert len(xs) == 1\n"
        self.assertEqual([f.id for f in T.scan_text("t.py", text)], ["t.py::test_free"])

    def test_what_is_not_a_trigger_is_not_flagged(self):
        cases = {
            "a computed interval": "        Heartbeat(interval=step).start()\n",
            "a zero interval": "        Heartbeat(interval=0).start()\n",
            "a heartbeat never started": "        Heartbeat(interval=1.0)\n",
            "a thread with a long sleep": "        threading.Thread(target=go).start()\n        time.sleep(5)\n",
            "a thread with no sleep": "        threading.Thread(target=go).start()\n",
            "a select with no timeout": "        select.select([r], [], [])\n",
            "a select with a zero timeout": "        select.select([r], [], [], 0)\n",
        }
        for why, body in cases.items():
            with self.subTest(why):
                self.assertEqual(T.scan_text("t.py", one(body + COUNT)), [])

    def test_an_empty_file_or_one_with_no_test_has_no_finding(self):
        self.assertEqual(T.scan_text("t.py", ""), [])
        self.assertEqual(T.scan_text("t.py", "def helper():\n    signal.alarm(1)\n    assert len(x) == 1\n"), [])


class NoData(Base):
    def test_an_unparsable_file_is_no_data(self):
        self.put("scripts/test_broken.py", "def test_x(:\n")
        with self.assertRaises(SyntaxError):
            T.scan_text("scripts/test_broken.py", "def test_x(:\n")
        code, out = self.main("scripts/test_broken.py")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("scripts/test_broken.py", out)

    def test_a_missing_file_is_no_data(self):
        found, bad = T.scan_paths(["scripts/test_gone.py"], self.root)
        self.assertEqual((found, bad), ([], ["scripts/test_gone.py"]))
        code, out = self.main("scripts/test_gone.py")
        self.assertEqual(code, 2, out)

    def test_no_path_and_no_all_is_no_data(self):
        code, out = self.main()
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_an_unknown_flag_is_no_data(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err), self.assertRaises(SystemExit) as raised:
            T.main(["--nope"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("NO-DATA", err.getvalue())


class TheCommandLine(Base):
    def test_a_path_prints_one_line_per_flagged_test_and_exits_on_uncovered(self):
        self.put("scripts/test_bare.py", BARE)
        code, out = self.main("scripts/test_bare.py")
        self.assertEqual(code, 1, out)
        self.assertIn("UNCOVERED", out)
        self.assertIn("scripts/test_bare.py::TheWait.test_beats", out)
        self.put("scripts/test_bare.py", BARE.replace("run(_SlowWorker(3.0))", "beat.tick()"))
        code, out = self.main("scripts/test_bare.py")
        self.assertEqual(code, 0, out)
        self.assertIn("COVERED", out)
        self.assertNotIn("UNCOVERED", out)

    def test_test_files_are_the_four_shapes(self):
        for rel in ("scripts/test_a.py", "scripts/loop/test_b.py", "tests/deep/er/test_c.py", "products/p/tools/test_d.py",
                    "scripts/a.py", "scripts/sub/test_e.py", "products/p/test_f.py", "scripts/test_g.sh"):
            self.put(rel, "")
        self.assertEqual(T.test_files(self.root), ["products/p/tools/test_d.py", "scripts/loop/test_b.py",
                                                   "scripts/test_a.py", "tests/deep/er/test_c.py"])

    def test_all_prints_an_inventory_and_exits_zero(self):
        self.put("scripts/test_bare.py", BARE)
        self.put("scripts/loop/test_ok.py", BARE.replace("run(_SlowWorker(3.0))", "beat.tick()"))
        self.put("tests/x/test_none.py", "def test_plain():\n    assert 1\n")
        code, out = self.main("--all")
        self.assertEqual(code, 0, out)
        self.assertIn("method:", out)
        self.assertIn("files scanned: 3", out)
        self.assertIn("flagged: 2", out)
        self.assertIn("covered: 1", out)
        self.assertIn("uncovered: 1", out)
        self.assertIn("heartbeat: 2", out)
        self.put("products/p/tools/test_broken.py", "def test_x(:\n")
        code, out = self.main("--all")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_the_baseline_names_the_uncovered_ids(self):
        self.put("scripts/test_bare.py", BARE)
        self.put("scripts/test_ok.py", BARE.replace("run(_SlowWorker(3.0))", "beat.tick()"))
        code, out = self.main("--all", "--write-baseline")
        self.assertEqual(code, 0, out)
        path = os.path.join(self.root, T.BASELINE)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data, {"schema": T.SCHEMA, "tests": ["scripts/test_bare.py::TheWait.test_beats"]})
        self.assertEqual(T.baseline_ids(path), ["scripts/test_bare.py::TheWait.test_beats"])
        self.assertEqual(sorted(os.listdir(os.path.dirname(path))), ["test_bare.py", "test_ok.py",
                                                                     os.path.basename(T.BASELINE)],
                         "the write leaves no temporary file behind")

    def test_no_baseline_is_written_over_an_unreadable_file(self):
        self.put("scripts/test_broken.py", "def test_x(:\n")
        code, out = self.main("--all", "--write-baseline")
        self.assertEqual(code, 2, out)
        self.assertFalse(os.path.exists(os.path.join(self.root, T.BASELINE)))

    def test_write_baseline_needs_all(self):
        code, out = self.main("--write-baseline")
        self.assertEqual(code, 2, out)

    def test_an_unreadable_or_corrupt_baseline_reads_none(self):
        self.assertIsNone(T.baseline_ids(os.path.join(self.root, "missing.json")))
        for text in ("{not json", json.dumps({"schema": "other", "tests": []}), json.dumps({"schema": T.SCHEMA, "tests": [1]}),
                     json.dumps([])):
            with self.subTest(text=text):
                self.put("b.json", text)
                self.assertIsNone(T.baseline_ids(os.path.join(self.root, "b.json")))


if __name__ == "__main__":
    unittest.main()
