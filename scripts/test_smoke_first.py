import os
import sys
import subprocess
import tempfile
import pathlib
import unittest
import time
import textwrap

SCRIPT = os.path.join(os.path.dirname(__file__), "smoke_first.py")

def run_script(args, cwd=None):
    cmd = [sys.executable, SCRIPT] + args
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, shell=False, timeout=60)
    return result

class TestSmokeFirst(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_items(self, items):
        p = pathlib.Path(self.tmp) / "items.txt"
        p.write_text("\n".join(items) + ("\n" if items else ""), encoding="utf-8")
        return str(p)

    def test_empty_items_exits_2(self):
        items_file = self.write_items([])
        r = run_script(["--items-file", items_file, "--", sys.executable, "-c", "print('hi')", "{item}"])
        self.assertEqual(r.returncode, 2)

    def test_empty_items_whitespace_only_exits_2(self):
        p = pathlib.Path(self.tmp) / "items2.txt"
        p.write_text("\n\n   \n", encoding="utf-8")
        r = run_script(["--items-file", str(p), "--", sys.executable, "-c", "print('hi')", "{item}"])
        self.assertEqual(r.returncode, 2)

    def test_smoke_fails_runs_nothing_else(self):
        items = ["a", "b", "c"]
        items_file = self.write_items(items)
        marker_dir = pathlib.Path(self.tmp) / "markers"
        marker_dir.mkdir(parents=True, exist_ok=True)
        # command creates marker file then fails on a, succeeds on others; smoke should fail and b,c markers not created
        prog = textwrap.dedent(f"""
            import sys, pathlib
            item = sys.argv[1]
            pathlib.Path(r"{marker_dir}/" + item).touch()
            if item == "a":
                print("failing smoke")
                sys.exit(1)
            else:
                print("ok")
                sys.exit(0)
        """)
        r = run_script(["--items-file", items_file, "--", sys.executable, "-c", prog, "{item}"])
        self.assertEqual(r.returncode, 1)
        self.assertTrue((marker_dir / "a").exists())
        self.assertFalse((marker_dir / "b").exists())
        self.assertFalse((marker_dir / "c").exists())
        # should print exit code and last lines
        self.assertIn("exit=", r.stdout + r.stderr)

    def test_smoke_succeeds_but_prints_nothing_fails(self):
        items = ["x", "y"]
        items_file = self.write_items(items)
        prog = "import sys; sys.exit(0)"
        r = run_script(["--items-file", items_file, "--", sys.executable, "-c", prog, "{item}"])
        self.assertEqual(r.returncode, 1)
        # should have printed smoke exit
        self.assertIn("exit=", r.stdout + r.stderr)

    def test_check_regex_not_match_fails_smoke(self):
        items = ["one", "two"]
        items_file = self.write_items(items)
        prog = "import sys; print('hello world'); sys.exit(0)"
        r = run_script(["--items-file", items_file, "--check-regex", "nomatch123", "--", sys.executable, "-c", prog, "{item}"])
        self.assertEqual(r.returncode, 1)
        # prove second item not run via marker
        marker_dir = pathlib.Path(self.tmp) / "markers2"
        marker_dir.mkdir(parents=True, exist_ok=True)
        prog2 = textwrap.dedent(f"""
            import sys, pathlib
            item = sys.argv[1]
            pathlib.Path(r"{marker_dir}/" + item).touch()
            print("hello")
        """)
        r2 = run_script(["--items-file", items_file, "--check-regex", "nomatch123", "--", sys.executable, "-c", prog2, "{item}"])
        self.assertEqual(r2.returncode, 1)
        self.assertTrue((marker_dir / "one").exists())
        self.assertFalse((marker_dir / "two").exists())

    def test_check_regex_match_passes(self):
        items = ["a", "b"]
        items_file = self.write_items(items)
        prog = "import sys; print('hello:'+sys.argv[1])"
        r = run_script(["--items-file", items_file, "--check-regex", "hello", "--", sys.executable, "-c", prog, "{item}"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("exit=", r.stdout)

    def test_all_items_pass_gives_exit_0_and_one_line_per_item(self):
        items = ["i1", "i2", "i3"]
        items_file = self.write_items(items)
        prog = "import sys; print('out:'+sys.argv[1])"
        r = run_script(["--items-file", items_file, "--jobs", "2", "--", sys.executable, "-c", prog, "{item}"])
        self.assertEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        for it in items:
            self.assertIn(f"{it} exit=0", out)
        # summary
        self.assertIn("smoke_first: 1 smoke + 2 items, 0 failed", out)

    def test_one_later_item_failing_gives_exit_1(self):
        items = ["a", "b", "c"]
        items_file = self.write_items(items)
        prog = textwrap.dedent("""
            import sys
            item = sys.argv[1]
            print("processing " + item)
            sys.exit(1 if item == "b" else 0)
        """)
        r = run_script(["--items-file", items_file, "--", sys.executable, "-c", prog, "{item}"])
        self.assertEqual(r.returncode, 1)
        out = r.stdout + r.stderr
        self.assertIn("a exit=0", out)
        self.assertIn("b exit=1", out)
        self.assertIn("c exit=0", out)
        self.assertIn("smoke_first: 1 smoke + 2 items, 1 failed", out)

    def test_timeout_counts_as_failure(self):
        items = ["s", "t"]
        items_file = self.write_items(items)
        prog = "import sys, time; print('start:'+sys.argv[1]); time.sleep(2); print('done')"
        r = run_script(["--items-file", items_file, "--timeout", "1", "--", sys.executable, "-c", prog, "{item}"])
        # smoke will timeout (first item sleeps 2 but timeout 1) -> smoke fails exit 1 and no second run
        # To test timeout as failure after smoke passes, make first item fast
        # redo with items where smoke passes but second times out
        items2 = ["fast", "slow"]
        items_file2 = self.write_items(items2)
        prog2 = textwrap.dedent("""
            import sys, time
            item = sys.argv[1]
            print("hi " + item)
            if item == "slow":
                time.sleep(2)
            sys.exit(0)
        """)
        r2 = run_script(["--items-file", items_file2, "--timeout", "1", "--", sys.executable, "-c", prog2, "{item}"])
        self.assertEqual(r2.returncode, 1)
        out2 = r2.stdout + r2.stderr
        self.assertIn("fast exit=0", out2)
        # slow should be timeout failure non-zero
        self.assertIn("slow exit=", out2)
        self.assertNotIn("slow exit=0", out2)
        self.assertIn("smoke_first: 1 smoke + 1 items, 1 failed", out2)

    def test_shlex_substitution_single_argument(self):
        p = pathlib.Path(self.tmp) / "items_space.txt"
        p.write_text("hello world\nsecond item\n", encoding="utf-8")
        marker_dir = pathlib.Path(self.tmp) / "markers_shlex"
        marker_dir.mkdir(parents=True, exist_ok=True)
        prog = textwrap.dedent(f"""
            import sys, pathlib
            item = sys.argv[1]
            safe = item.replace(" ", "_")
            pathlib.Path(r"{marker_dir}" + "/" + safe + ".txt").write_text(str(len(sys.argv)) + "\\n" + item, encoding="utf-8")
            print("hi " + item)
        """)
        r = run_script(["--items-file", str(p), "--", sys.executable, "-c", prog, "{item}"])
        self.assertEqual(r.returncode, 0)
        hello_path = marker_dir / "hello_world.txt"
        self.assertTrue(hello_path.exists())
        content = hello_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(content[0], "2")
        self.assertEqual(content[1], "hello world")
        second_path = marker_dir / "second_item.txt"
        self.assertTrue(second_path.exists())
        content2 = second_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(content2[0], "2")
        self.assertEqual(content2[1], "second item")

    def test_duplicate_items(self):
        items = ["dup", "dup", "other"]
        items_file = self.write_items(items)
        prog = "import sys; print('out:'+sys.argv[1])"
        r = run_script(["--items-file", items_file, "--", sys.executable, "-c", prog, "{item}"])
        self.assertEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertEqual(out.count("dup exit=0"), 2)
        self.assertIn("other exit=0", out)
        self.assertIn("smoke_first: 1 smoke + 2 items, 0 failed", out)

if __name__ == "__main__":
    unittest.main()
