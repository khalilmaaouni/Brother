"""door.py, driven as the real command line, with a stub standing in for the
model. No network, no real claude invocation: each case writes its own
decomposer script into a tempdir and points --model-cmd at it, the same way
test_spine.py stands in a fake worker for the real one.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DOOR = os.path.join(HERE, "door.py")
import door as door_mod  # noqa: E402


class DoneCheckInterpreter(unittest.TestCase):
    """The harsh EVAD 2026-08-31 killer: a generated done_check named `python`
    on a machine that has only python3, so every check exited 127."""

    def test_python_is_resolved_to_python3_when_only_python3_exists(self):
        import shutil
        real = shutil.which
        try:
            shutil.which = lambda p: None if p == "python" else "/usr/bin/python3"
            cmd, note = door_mod.resolve_done_check_interpreter(
                "python -m pytest tests/test_x.py")
            self.assertEqual(cmd, "python3 -m pytest tests/test_x.py")
            self.assertIsNotNone(note)
        finally:
            shutil.which = real

    def test_python3_is_never_touched(self):
        cmd, note = door_mod.resolve_done_check_interpreter(
            "python3 -m pytest tests/test_x.py")
        self.assertEqual(cmd, "python3 -m pytest tests/test_x.py")
        self.assertIsNone(note)

    def test_normalize_unit_rewrites_the_units_own_check(self):
        import shutil
        real = shutil.which
        try:
            shutil.which = lambda p: None if p == "python" else "/usr/bin/python3"
            out = door_mod.normalize_unit(
                {"title": "t", "owns": ["a.py"],
                 "done_check": "python a.py"})
            self.assertEqual(out["done_check"], "python3 a.py")
        finally:
            shutil.which = real


def sh(args, env=None):
    return subprocess.run(args, capture_output=True, text=True, timeout=60,
                          env=env)


def write_stub(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n" + body)
    os.chmod(path, 0o755)


class Valid(unittest.TestCase):
    def test_a_valid_two_unit_decomposition_is_accepted(self):
        tmp = tempfile.mkdtemp(prefix="door-valid-")
        stub = os.path.join(tmp, "stub.py")
        write_stub(stub, (
            "import json, sys\n"
            "sys.stdin.read()\n"
            "print(json.dumps([\n"
            "  {'id': 'U1', 'objective': 'make one', "
            "'done_check': 'test -f one.txt', 'writes': ['one.txt'], "
            "'deps': []},\n"
            "  {'id': 'U2', 'objective': 'make two', "
            "'done_check': 'test -f two.txt', 'writes': ['two.txt'], "
            "'deps': ['U1']},\n"
            "]))\n"
        ))
        store = os.path.join(tmp, "store")

        proc = sh([sys.executable, DOOR, "two files exist",
                  "--model-cmd", "%s %s" % (sys.executable, stub),
                  "--store", store])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(proc.stdout.strip())
        work_id = proc.stdout.splitlines()[0].split()[1]
        self.assertTrue(work_id)

        files = os.listdir(store)
        self.assertEqual(len(files), 1)
        with open(os.path.join(store, files[0]), encoding="utf-8") as fh:
            record = json.load(fh)
        ids = {r["id"] for r in record["rows"]}
        self.assertEqual(ids, {"U1", "U2"})


class Retry(unittest.TestCase):
    def test_a_refused_answer_is_retried_with_the_refusal_text(self):
        tmp = tempfile.mkdtemp(prefix="door-retry-")
        stub = os.path.join(tmp, "stub.py")
        counter = os.path.join(tmp, "count")
        stdin2 = os.path.join(tmp, "stdin2.txt")
        write_stub(stub, (
            "import json, os, sys\n"
            "counter = %r\n"
            "stdin2 = %r\n"
            "n = 0\n"
            "if os.path.exists(counter):\n"
            "    n = int(open(counter).read() or '0')\n"
            "n += 1\n"
            "open(counter, 'w').write(str(n))\n"
            "stdin_text = sys.stdin.read()\n"
            "if n == 1:\n"
            "    units = [\n"
            "        {'id': 'U1', 'objective': 'make one', 'done_check': '', "
            "'writes': ['one.txt'], 'deps': []},\n"
            "        {'id': 'U2', 'objective': 'make two', "
            "'done_check': 'test -f two.txt', 'writes': ['two.txt'], "
            "'deps': ['U1']},\n"
            "    ]\n"
            "else:\n"
            "    open(stdin2, 'w').write(stdin_text)\n"
            "    units = [\n"
            "        {'id': 'U1', 'objective': 'make one', "
            "'done_check': 'test -f one.txt', 'writes': ['one.txt'], "
            "'deps': []},\n"
            "        {'id': 'U2', 'objective': 'make two', "
            "'done_check': 'test -f two.txt', 'writes': ['two.txt'], "
            "'deps': ['U1']},\n"
            "    ]\n"
            "print(json.dumps(units))\n"
        ) % (counter, stdin2))
        store = os.path.join(tmp, "store")

        proc = sh([sys.executable, DOOR, "two files exist",
                  "--model-cmd", "%s %s" % (sys.executable, stub),
                  "--store", store])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(counter, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "2")
        self.assertTrue(os.path.exists(stdin2))
        with open(stdin2, encoding="utf-8") as fh:
            second_stdin = fh.read()
        self.assertIn("no done_check", second_stdin)
        self.assertIn("U1", second_stdin)


class Refused(unittest.TestCase):
    def test_a_cyclic_decomposition_is_refused_and_nothing_is_stored(self):
        tmp = tempfile.mkdtemp(prefix="door-cyclic-")
        stub = os.path.join(tmp, "stub.py")
        write_stub(stub, (
            "import json, sys\n"
            "sys.stdin.read()\n"
            "print(json.dumps([\n"
            "  {'id': 'A', 'objective': 'a', 'done_check': 'true', "
            "'writes': ['a.txt'], 'deps': ['B']},\n"
            "  {'id': 'B', 'objective': 'b', 'done_check': 'true', "
            "'writes': ['b.txt'], 'deps': ['A']},\n"
            "]))\n"
        ))
        store = os.path.join(tmp, "store")

        proc = sh([sys.executable, DOOR, "a cycle nobody can schedule",
                  "--model-cmd", "%s %s" % (sys.executable, stub),
                  "--store", store])
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(os.path.exists(store))
        self.assertIn("cycle", proc.stdout + proc.stderr)


class NoData(unittest.TestCase):
    def test_a_missing_decomposer_is_NO_DATA_not_a_crash(self):
        tmp = tempfile.mkdtemp(prefix="door-nodata-")
        store = os.path.join(tmp, "store")
        env = dict(os.environ)
        env["DOOR_MODEL_CMD"] = "/no/such/decomposer --flag"

        proc = sh([sys.executable, DOOR, "an outcome", "--store", store],
                  env=env)
        self.assertEqual(proc.returncode, 44, proc.stdout + proc.stderr)
        self.assertTrue(any(l.startswith("NO-DATA")
                            for l in (proc.stdout + proc.stderr).splitlines()))
        self.assertFalse(os.path.exists(store))


if __name__ == "__main__":
    unittest.main()
