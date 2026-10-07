#!/usr/bin/env python3
"""The landing fuzz runs each new module in a bounded child process (FX-08.2) and prints worker text on one line
(FX-08.3).

WHY. 2026-09-28: a fuzzed call given True as a file name closed the lander's own stdout; the lander exited 120 with no
result line and a READY build (L4.4) was dropped with no usable reason. Another fuzzed call given "x" created a
directory in the landing tree, and a later build's fuzz then crashed on it. Every entry point case below runs
scripts/loop/land_apply.py AS A COMMAND in a fixture tree (a build JSON whose edits create the module), on python3 AND
/usr/bin/python3, and reads its stdout and exit code the way land_batch does. A missing /usr/bin/python3 FAILS, it never
skips. Each fixture trips ONE guard. Helper cases (fuzz_in_child, read_child_results, fuzz_timeout) are additional.
"""
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
LANDER = os.path.join(HERE, "land_apply.py")
sys.path.insert(0, HERE)
import land_apply  # noqa: E402

PYTHONS = (sys.executable, "/usr/bin/python3")
SUMMARY = "FUZZ    new modules %d | crashes %d | calls that returned instead of refusing %d"
N = len(land_apply.HOSTILE)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_LAND_FUZZ_")}
    env.update(extra)
    return env


GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid", GIT_CONFIG_NOSYSTEM="1")


class Tree:
    """A fixture tree (a git repository at one commit, as the lander requires since it runs the build in a worktree copy
    of HEAD) and a build JSON whose edits create each module (and an optional sibling test)."""

    def __init__(self, modules, tests=None, files=None):
        self.dir = tempfile.mkdtemp(prefix="fx08-tree-")
        for rel, text in (files or {}).items():
            os.makedirs(os.path.dirname(os.path.join(self.dir, rel)), exist_ok=True)
            with open(os.path.join(self.dir, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        subprocess.run(["git", "init", "-q", self.dir], check=True, env=GIT_ENV, capture_output=True)
        subprocess.run(["git", "-C", self.dir, "add", "-A"], check=True, env=GIT_ENV, capture_output=True)
        subprocess.run(["git", "-C", self.dir, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "base"],
                       check=True, env=GIT_ENV, capture_output=True)
        self.build = os.path.join(self.dir, "build.json")
        edits = [{"path": "scripts/%s.py" % n, "new_file_content": src} for n, src in modules.items()]
        tests = [{"path": "scripts/test_%s.py" % n, "new_file_content": src} for n, src in (tests or {}).items()]
        with open(self.build, "w", encoding="utf-8") as fh:
            json.dump({"edits": edits, "tests": tests}, fh)

    def run(self, py=sys.executable, env=None, timeout=90, build=None):
        """(exit code, stdout) of the lander run as a command. Its stdin is a pipe held open for the whole run, so a
        call that reads the lander's stdin blocks instead of seeing EOF; the lander's own process group is killed if
        it outlives timeout, and the test then fails."""
        with tempfile.TemporaryFile() as out:
            return self._run(out, py, env, timeout, build)

    def _run(self, out, py, env, timeout, build):
        p = subprocess.Popen([py, "-B", LANDER, build or self.build], cwd=self.dir, env=env or clean_env(),
                             stdin=subprocess.PIPE, stdout=out, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            raise AssertionError("the lander did not finish within %d s" % timeout)
        finally:
            p.stdin.close()
        out.seek(0)
        return rc, out.read().decode("utf-8", "replace")

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def both(test, modules, tests=None, env=None, timeout=90):
    """Run the lander on each interpreter in a fresh tree; returns [(python, rc, stdout)]."""
    got = []
    for py in PYTHONS:
        test.assertTrue(os.path.exists(py), "missing interpreter %s: this test fails, it never skips" % py)
        t = Tree(modules, tests)
        try:
            rc, out = t.run(py, env=env, timeout=timeout)
            got.append((py, rc, out, t))
        except BaseException:
            t.close()
            raise
    for _, _, _, t in got:
        test.addCleanup(t.close)
    return [(py, rc, out) for py, rc, out, _ in got]


def lines(out, prefix):
    return [l for l in out.splitlines() if l.startswith(prefix)]


def child(test, name, src, hostile=(None,), timeout=land_apply.FUZZ_TIMEOUT_S, i=0, home=None, root=None):
    """fuzz_in_child on one module file: ((crashes, returned, bad), lines said, tree)."""
    root = root or tempfile.mkdtemp(prefix="fx08-root-")
    test.addCleanup(shutil.rmtree, root, True)
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    with open(os.path.join(root, "scripts", name + ".py"), "w", encoding="utf-8") as fh:
        fh.write(src)
    if home is None:
        home = tempfile.mkdtemp(prefix="fx08-home-")
        test.addCleanup(shutil.rmtree, home, True)
    said = []
    got = land_apply.fuzz_in_child(name, list(hostile), land_apply.suite_env(clean_env(), home), home, i=i, root=root,
                                   timeout=timeout, say=said.append)
    return got, said, root


RETURNS = "def echo(value):\n    return value\n"
CRASHES = "def boom(value):\n    raise RuntimeError('boom')\n"
SIBLING = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\nif __name__ == '__main__':\n    unittest.main()\n"


class EntryPoint(unittest.TestCase):
    def test_no_new_module_starts_no_child(self):
        for py, rc, out in both(self, {}, {"only": SIBLING}):
            self.assertEqual(rc, 0, py)
            self.assertIn(SUMMARY % (0, 0, 0), out, py)
            self.assertIn("VERDICT SUITES GREEN", out, py)

    def test_module_without_callable_parameters_is_zero_work(self):
        for py, rc, out in both(self, {"noargs": "X = 1\ndef ping():\n    return 1\n"}):
            self.assertEqual((rc, lines(out, "   CRASH")), (0, []), out)
            self.assertIn(SUMMARY % (1, 0, 0), out, py)

    def test_two_modules_are_two_children_and_counts_add(self):
        for py, rc, out in both(self, {"amod": CRASHES + RETURNS, "bmod": CRASHES}):
            self.assertIn(SUMMARY % (2, 2 * N, N), out, py)
            crash = lines(out, "   CRASH")
            self.assertEqual(len(crash), 2 * N, out)
            self.assertTrue(all("amod.boom" in l for l in crash[:N]) and all("bmod.boom" in l for l in crash[N:]), out)
            self.assertIn("VERDICT SUITES GREEN", out)

    def test_inprocess_switch_is_exactly_one(self):
        for value in ("yes", "0", ""):
            for py, rc, out in both(self, {}, env=clean_env(BROTHER_LAND_FUZZ_INPROCESS=value)):
                self.assertIn("FUZZ    mode child (timeout 120 s)", out, (py, value))
        for py, rc, out in both(self, {}, env=clean_env(BROTHER_LAND_FUZZ_INPROCESS="1")):
            self.assertIn("FUZZ    mode in-process (BROTHER_LAND_FUZZ_INPROCESS=1)", out, py)

    def test_inprocess_switch_still_fuzzes_in_process(self):
        for py, rc, out in both(self, {"ipmod": CRASHES + RETURNS}, env=clean_env(BROTHER_LAND_FUZZ_INPROCESS="1")):
            self.assertIn(SUMMARY % (1, N, N), out, py)

    def test_timeout_setting_outside_range_keeps_default(self):
        for py, rc, out in both(self, {}, env=clean_env(BROTHER_LAND_FUZZ_TIMEOUT_S="0")):
            self.assertIn("FUZZ    timeout setting ignored: 0", out, py)
            self.assertIn("FUZZ    mode child (timeout 120 s)", out, py)

    def test_stdin_read_sees_eof(self):
        src = "import sys\ndef slurp(value):\n    return sys.stdin.read()\n"
        env = clean_env(BROTHER_LAND_FUZZ_TIMEOUT_S="10")
        for py, rc, out in both(self, {"slurp": src}, env=env, timeout=60):
            self.assertIn(SUMMARY % (1, 0, N), out, py)
            self.assertEqual(lines(out, "   CRASH"), [], out)

    def test_hung_call_times_out_and_next_module_runs(self):
        hang = "import time\ndef hang(value):\n    time.sleep(30)\n"
        env = clean_env(BROTHER_LAND_FUZZ_TIMEOUT_S="2")
        for py, rc, out in both(self, {"ahang": hang, "bnext": RETURNS}, env=env, timeout=60):
            self.assertIn("   CRASH ahang timed out after 2 s during hang(None x1), calls not run %d" % (N - 1), out, py)
            self.assertIn(SUMMARY % (2, 1, N), out, py)
            self.assertIn("VERDICT SUITES GREEN", out, py)

    def test_timeout_kills_the_child_process_group(self):
        # fuzz_in_child on a root this test owns: the pid file sits inside that root, the sandbox's ROOT, where the child
        # may write. (Until 2026-10-02 the lander's suites ran in the landing tree itself and this file sat there.)
        src = ("import os, subprocess, time\ndef spawn(value):\n"
               "    g = subprocess.Popen(['/bin/sleep', '300'])\n"
               "    open(os.environ['FX08_PID_FILE'], 'w').write(str(g.pid))\n"
               "    time.sleep(300)\n")
        root = tempfile.mkdtemp(prefix="fx08-root-")
        pid_file = os.path.join(root, "grandchild.pid")
        with unittest.mock.patch.dict(os.environ, {"FX08_PID_FILE": pid_file}):
            got, said, _ = child(self, "spawn", src, timeout=2, root=root)
        self.assertEqual(got[0], 1, said)
        self.assertTrue(any("timed out after 2 s during spawn(None x1)" in l for l in said), said)
        with open(pid_file) as fh:
            pid = int(fh.read())
        deadline = time.time() + 10
        while time.time() < deadline and alive(pid):
            time.sleep(0.2)
        if alive(pid):
            os.kill(pid, signal.SIGKILL)
            self.fail("the grandchild %d outlived the child's timeout" % pid)

    def test_a_suite_runs_in_a_copy_so_an_ignored_path_it_plants_never_reaches_the_tree(self):
        # ONE CONDITION (eleventh review 2026-10-02, channel a): a suite wrote scripts/loop/__pycache__/x.pyc and dist/,
        # both gitignored, in the landing tree; git status never showed them and a later unsandboxed gate loaded the .pyc
        plant = ("import os, unittest\nos.makedirs('scripts/loop/__pycache__', exist_ok=True)\n"
                 "open('scripts/loop/__pycache__/planted.pyc', 'wb').write(b'x')\nos.makedirs('dist', exist_ok=True)\n"
                 "open('dist/planted', 'w').write('x')\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
                 "if __name__ == '__main__':\n    unittest.main()\n")
        t = Tree({"planter": "X = 1\n"}, {"planter": plant}, files={".gitignore": "__pycache__/\n*.pyc\ndist/\n"})
        self.addCleanup(t.close)
        rc, out = t.run()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT SUITES GREEN", out)
        self.assertTrue(os.path.isfile(os.path.join(t.dir, "scripts", "planter.py")), "the graded bytes do land")
        self.assertFalse(os.path.exists(os.path.join(t.dir, "scripts", "loop", "__pycache__")), "the suite's ignored write stays in the copy")
        self.assertFalse(os.path.exists(os.path.join(t.dir, "dist")))

    def shell_tree(self, test_body):
        t = Tree({"shmain": "X = 1\n"})
        self.addCleanup(t.close)
        with open(t.build, encoding="utf-8") as fh:
            b = json.load(fh)
        b["edits"] += [{"path": "scripts/shmod.sh", "new_file_content": "#!/bin/sh\necho shmod\n"},
                       {"path": "scripts/test_shmod.sh", "new_file_content": test_body}]
        with open(t.build, "w", encoding="utf-8") as fh:
            json.dump(b, fh)
        return t

    def test_a_shell_suite_runs_once_under_its_own_shell_never_as_python(self):
        # 2026-10-04: MG1.a's scripts/test_merge_verified.sh was run as Python on both interpreters ("SyntaxError")
        t = self.shell_tree("#!/bin/bash\n[[ 1 == 1 ]] && echo OK && exit 0\nexit 1\n")
        rc, out = t.run()
        lines = [l for l in out.splitlines() if l.split()[:1] in (["sh"], ["py3"], ["py3.9"]) and "scripts/test_shmod.sh" in l]
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith("sh ") and "exit=0" in lines[0], out)
        self.assertNotIn("SyntaxError", out)

    def test_shebang_arguments_are_kept_and_an_unknown_shebang_is_red(self):
        import land_apply as LA
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d, True)
        def first(line):
            with open(os.path.join(d, "t.sh"), "w") as fh:
                fh.write(line + "\nexit 0\n")
            return LA.cmd_for("t.sh", d)
        self.assertEqual(first("#!/bin/bash -e"), ["/bin/bash", "-e", "t.sh"])
        self.assertEqual(first("#!/usr/bin/env -S bash -eu"), ["/bin/bash", "-eu", "t.sh"])
        self.assertEqual(first("#!/usr/bin/env sh"), ["/bin/sh", "t.sh"])
        self.assertEqual(first("#!/bin/bash -eu -o pipefail"), ["/bin/bash", "-eu", "-o", "pipefail", "t.sh"])
        for bad in ("#!/bin/zsh", "#!/usr/bin/env python3", "echo no shebang", "#!/bin/bash -c true", "#!/bin/sh -o vi"):
            c = first(bad)
            self.assertEqual(c[:2], ["/bin/sh", "-c"], bad)
            self.assertNotEqual(subprocess.run(c, cwd=d, capture_output=True).returncode, 0, bad)

    def test_a_red_shell_suite_is_a_red_verdict(self):
        t = self.shell_tree("#!/bin/sh\necho failing; exit 3\n")
        rc, out = t.run()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT RED", out)
        self.assertTrue(any(l.startswith("sh ") and "exit=3" in l for l in out.splitlines()), out)

    def test_a_red_suite_leaves_the_landing_tree_unwritten(self):
        # the tree receives the build only after its suites are green in the copy; a RED build touches nothing
        red = "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.fail('red')\nif __name__ == '__main__':\n    unittest.main()\n"
        t = Tree({"redmod": "X = 1\n"}, {"redmod": red})
        self.addCleanup(t.close)
        rc, out = t.run()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT RED", out)
        self.assertFalse(os.path.exists(os.path.join(t.dir, "scripts", "redmod.py")), "nothing of a red build reaches the tree")
        self.assertFalse(os.path.exists(os.path.join(t.dir, "scripts", "test_redmod.py")))

    def test_a_child_a_suite_leaves_behind_does_not_outlive_the_lander(self):
        # ONE CONDITION (eleventh review 2026-10-02, channel c): a suite started a detached child that outlived
        # subprocess.run; the lander now runs each suite as its own process group and kills the group when it answers
        mark = "300.%d%d" % (os.getpid() % 10000, int(time.time()) % 100000)
        leave = ("import subprocess, unittest\nsubprocess.Popen(['/bin/sleep', %r], stdin=subprocess.DEVNULL, "
                 "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\nclass T(unittest.TestCase):\n"
                 "    def test_ok(self):\n        pass\nif __name__ == '__main__':\n    unittest.main()\n" % mark)
        t = Tree({"leaver": "X = 1\n"}, {"leaver": leave})
        self.addCleanup(t.close)
        self.addCleanup(subprocess.run, ["pkill", "-9", "-f", "sleep " + mark])
        rc, out = t.run()
        self.assertEqual(rc, 0, out)
        ps = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout
        self.assertEqual([l for l in ps.splitlines() if "sleep " + mark in l], [], "a child the suite left behind survived the lander")

    def test_child_death_mid_module_names_the_call(self):
        src = "import os\ndef dies(value):\n    if value == '':\n        os._exit(0)\n    return 1\n"
        for py, rc, out in both(self, {"dies": src}):
            k = [repr(h) for h in land_apply.HOSTILE].index("''")
            self.assertIn("   CRASH dies child exit 0 after dies('' x1), calls not run %d" % (N - k - 1), out, py)
            self.assertIn(SUMMARY % (1, 1, 0), out, py)

    def test_relative_writes_stay_out_of_the_tree(self):
        src = "import os\ndef writer(value):\n    os.makedirs('x', exist_ok=True)\n    return 1\n"
        t = Tree({"writer": src})
        self.addCleanup(t.close)
        rc, out = t.run()
        self.assertIn(SUMMARY % (1, 0, N), out)
        self.assertFalse(os.path.exists(os.path.join(t.dir, "x")), "a fuzzed relative write landed in the tree")

    def test_rerun_sees_no_trace_of_the_first(self):
        t = Tree({"first": "import os\ndef writer(value):\n    os.makedirs('x', exist_ok=True)\n    return 1\n"})
        self.addCleanup(t.close)
        t.run()
        second = os.path.join(t.dir, "build2.json")
        with open(second, "w", encoding="utf-8") as fh:
            json.dump({"edits": [{"path": "scripts/second.py", "new_file_content":
                                  "import os\ndef look(value):\n    if os.path.exists('x'):\n"
                                  "        raise RuntimeError('trace of the first run')\n    return 1\n"}]}, fh)
        rc, out = t.run(build=second)
        self.assertIn(SUMMARY % (1, 0, N), out)

    def test_call_closing_stdout_keeps_the_landers_verdict(self):
        src = "print('module loaded')\ndef reader(path):\n    with open(path, 'rb') as h:\n        return h.read()\n"
        for py, rc, out in both(self, {"reader": src}, {"reader": SIBLING}):
            self.assertEqual(rc, 0, out)
            self.assertEqual(len(lines(out, "py3 ")), 1, out)
            self.assertIn("VERDICT SUITES GREEN", out, py)
            self.assertEqual(len(lines(out, "FUZZ    new modules 1 | crashes ")), 1, out)
            self.assertTrue(any("reader(True x1)" in l for l in lines(out, "   CRASH")), out)

    def test_call_closing_stdin_is_contained(self):
        # closes fd 0 and fd 1 once, with output buffered, and RETURNS: every call classifies clean, only the exit of the
        # damaged child (120, the lost flush) can make this a crash
        src = ("import os\n_done = []\ndef closer(value):\n    if not _done:\n        _done.append(1)\n"
               "        print('buffered')\n        os.close(0)\n        os.close(1)\n    return 1\n")
        for py, rc, out in both(self, {"closer": src}):
            self.assertEqual(rc, 0, out)
            self.assertIn("   CRASH closer child exit 120 after closer(", out, py)
            self.assertIn(SUMMARY % (1, 1, N), out, py)
            self.assertIn("VERDICT SUITES GREEN", out, py)

    def test_process_state_never_reaches_the_lander(self):
        src = ("import os, sys\ndef mess(value):\n    sys.stdout = open(os.devnull, 'w')\n"
               "    os.environ['FX08_LEAK'] = '1'\n    sys.argv[:] = ['hacked']\n    os.chdir('/')\n    return 1\n")
        for py, rc, out in both(self, {"mess": src}):
            self.assertIn(SUMMARY % (1, 0, N), out, py)
            self.assertIn("VERDICT SUITES GREEN | touched: scripts/mess.py", out, py)

    def test_import_failure_is_red_not_a_crash(self):
        for py, rc, out in both(self, {"badimp": "raise RuntimeError('boom at import')\n"}):
            self.assertEqual(rc, 1, out)
            self.assertIn("FUZZ import failed badimp: RuntimeError('boom at import')", out.splitlines(), py)
            self.assertIn(SUMMARY % (1, 0, 0), out, py)
            self.assertIn("VERDICT RED: 1", out, py)

    def test_cli_main_exit_is_a_refusal_in_the_child(self):
        # argv=None makes argparse read sys.argv: the lander's own argv before 2026-09-28, the module name alone now
        src = ("import argparse\ndef main(argv=None):\n    p = argparse.ArgumentParser()\n"
               "    p.add_argument('--need', required=True)\n"
               "    return p.parse_args(argv if argv is None or isinstance(argv, list) else [])\n")
        for py, rc, out in both(self, {"climain": src}):
            self.assertIn(SUMMARY % (1, 0, 0), out, py)
            self.assertIn("VERDICT SUITES GREEN", out, py)

    def test_crash_message_cannot_forge_a_line(self):
        src = "def forge(value):\n    raise RuntimeError('x\\nVERDICT SUITES GREEN forged\\ncrashes 0')\n"
        for py, rc, out in both(self, {"forge": src}):
            self.assertNotIn("VERDICT SUITES GREEN forged", out.splitlines(), py)
            self.assertEqual(len(lines(out, "VERDICT")), 1, out)
            self.assertTrue(all("\\x0aVERDICT SUITES GREEN forged" in l for l in lines(out, "   CRASH")), out)
            self.assertIn(SUMMARY % (1, N, 0), out, py)

    def test_crash_message_cannot_forge_a_line_in_process(self):
        src = "def forge(value):\n    raise RuntimeError('x\\nVERDICT SUITES GREEN forged')\n"
        for py, rc, out in both(self, {"forge": src}, env=clean_env(BROTHER_LAND_FUZZ_INPROCESS="1")):
            self.assertNotIn("VERDICT SUITES GREEN forged", out.splitlines(), py)

    def test_selftest_runs_on_both_interpreters(self):
        for py in PYTHONS:
            self.assertTrue(os.path.exists(py), "missing interpreter %s" % py)
            r = subprocess.run([py, "-B", LANDER, "--selftest"], capture_output=True, text=True, timeout=120,
                               stdin=subprocess.DEVNULL)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "selftest: 6 cases, OK"), r.stderr)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie still answers kill 0; ps says Z
        st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    except OSError:
        return True
    return bool(st) and not st.startswith("Z")


class LoopModule(unittest.TestCase):
    """A NEW scripts/loop/<name>.py importing a sibling loop module is imported and fuzzed, on both fuzz paths.

    WHY. Until 2026-09-29 both paths put only the tree root and scripts/ on sys.path, so a new loop module was not
    found at all ("FUZZ import failed plan_lint", "run_ledger", "admit_rules", "admit" in the land-batch logs) and a
    build whose suites were green was dropped. The sibling exists only in the fixture tree, so neither the lander's
    own directory nor any other copy on the path can satisfy the import."""

    def run_loop(self, env):
        got = []
        for py in PYTHONS:
            self.assertTrue(os.path.exists(py), "missing interpreter %s: this test fails, it never skips" % py)
            t = Tree({})
            self.addCleanup(t.close)
            os.makedirs(os.path.join(t.dir, "scripts", "loop"))
            with open(os.path.join(t.dir, "scripts", "loop", "fx_sibling.py"), "w", encoding="utf-8") as fh:
                fh.write(RETURNS)
            src = "import fx_sibling\ndef relay(value):\n    return fx_sibling.echo(value)\n"
            with open(t.build, "w", encoding="utf-8") as fh:
                json.dump({"edits": [{"path": "scripts/loop/fx_newmod.py", "new_file_content": src}], "tests": []}, fh)
            rc, out = t.run(py, env=env)
            got.append((py, rc, out))
        return got

    def check(self, env):
        for py, rc, out in self.run_loop(env):
            self.assertEqual(lines(out, "FUZZ import failed"), [], (py, out))
            self.assertIn(SUMMARY % (1, 0, N), out, (py, out))
            self.assertEqual(rc, 0, (py, out))

    def test_child_path_imports_a_new_loop_module(self):
        self.check(clean_env())

    def test_inprocess_path_imports_a_new_loop_module(self):
        self.check(clean_env(BROTHER_LAND_FUZZ_INPROCESS="1"))


class TreePaths(unittest.TestCase):
    """tree_paths is scripts/, scripts/loop/, the tree root, in that order, and both fuzz paths honour it.

    WHY THE ROOT. A NEW module outside scripts/ (a plugin/runtime/... module) is fuzzed by its dotted path. A script
    run puts its own directory at sys.path[0], never the cwd, so without the root that module is not found and a
    build whose suites are green is dropped at "FUZZ import failed".

    WHY THE ORDER. A scripts/ or scripts/loop/ module is fuzzed by its bare stem, and the first folder on the path that
    holds the stem wins; a stem can live in both folders (grade_build does). With scripts/ first, a NEW scripts/X.py is
    the X fuzzed even when scripts/loop/X.py exists. With scripts/loop/ first the loop twin would shadow it: the fuzz
    would exercise the old loop file and report its calls (here zero) as the NEW module's, a silent pass. The same
    first match rule is the price of this order, named so a reorder is a decision and not an accident: a NEW
    scripts/loop/X.py whose stem already names a scripts/X.py is shadowed by that scripts/ file, so a NEW loop module
    must not reuse a scripts/ stem."""

    def land(self, present, rel, env):
        got = []
        for py in PYTHONS:
            self.assertTrue(os.path.exists(py), "missing interpreter %s: this test fails, it never skips" % py)
            t = Tree({})
            self.addCleanup(t.close)
            for p, text in present.items():
                os.makedirs(os.path.dirname(os.path.join(t.dir, p)), exist_ok=True)
                with open(os.path.join(t.dir, p), "w", encoding="utf-8") as fh:
                    fh.write(text)
            with open(t.build, "w", encoding="utf-8") as fh:
                json.dump({"edits": [{"path": rel, "new_file_content": RETURNS}], "tests": []}, fh)
            rc, out = t.run(py, env=env)
            got.append((py, rc, out))
        return got

    def fuzzes_the_new_module(self, present, rel):
        for env in (clean_env(), clean_env(BROTHER_LAND_FUZZ_INPROCESS="1")):
            for py, rc, out in self.land(present, rel, env):
                self.assertEqual(lines(out, "FUZZ import failed"), [], (py, out))
                self.assertIn(SUMMARY % (1, 0, N), out, (py, out))
                self.assertEqual(rc, 0, (py, out))

    def test_tree_paths_is_scripts_then_loop_then_the_given_root(self):
        # the order and the root are the contract above, read directly: the given root, never the cwd
        for root in (tempfile.gettempdir(), os.path.join(os.sep, "elsewhere", "tree")):
            with self.subTest(root=root):
                self.assertNotEqual(os.path.abspath(root), os.getcwd())
                self.assertEqual(land_apply.tree_paths(root), [os.path.join(root, "scripts"),
                                                               os.path.join(root, "scripts", "loop"), root])

    def test_a_new_module_outside_scripts_is_found_through_the_root(self):
        self.fuzzes_the_new_module({"fx_rootpkg/__init__.py": ""}, "fx_rootpkg/fx_rootmod.py")

    def test_a_new_scripts_module_is_fuzzed_not_its_loop_twin(self):
        self.fuzzes_the_new_module({"scripts/loop/fx_twin.py": "X = 1\n"}, "scripts/fx_twin.py")


class LoopPackage(unittest.TestCase):
    """A NEW package under scripts/loop/ (its __init__.py and a module using a relative import) is imported by its
    package name and fuzzed, on both fuzz paths and both interpreters.

    WHY. Until 2026-09-29 the lander named every new scripts/ file by its bare stem, so a NEW
    scripts/loop/adapters/__init__.py was imported as the module "__init__" and every FX-31 landing died at
    "FUZZ import failed __init__: ModuleNotFoundError" (eight drops in the 2026-09-29 land-batch logs), and a module
    inside the package, named by its stem, could not resolve "from . import"."""

    INIT = "def base(value):\n    return value\n"
    MOD = "from . import base\ndef relay(value):\n    return base(value)\n"

    def check(self, env):
        for py in PYTHONS:
            self.assertTrue(os.path.exists(py), "missing interpreter %s: this test fails, it never skips" % py)
            t = Tree({})
            self.addCleanup(t.close)
            with open(t.build, "w", encoding="utf-8") as fh:
                json.dump({"edits": [{"path": "scripts/loop/pkgx/__init__.py", "new_file_content": self.INIT},
                                     {"path": "scripts/loop/pkgx/mod.py", "new_file_content": self.MOD}], "tests": []}, fh)
            rc, out = t.run(py, env=env)
            self.assertEqual(lines(out, "FUZZ import failed"), [], (py, out))
            self.assertIn(SUMMARY % (2, 0, 2 * N), out, (py, out))
            self.assertEqual(rc, 0, (py, out))

    def test_child_path_imports_a_new_package(self):
        self.check(clean_env())

    def test_inprocess_path_imports_a_new_package(self):
        self.check(clean_env(BROTHER_LAND_FUZZ_INPROCESS="1"))

    def test_a_new_file_with_no_importable_name_is_red(self):
        # A NEW root __init__.py has no module name the fuzz can import: the landing is RED at the entry point, never
        # a quiet skip that reads SUITES GREEN (verify of 2026-09-29: M10 "continue" and M11 dropped "bad += 1" both
        # survived because only the helper was tested).
        for py in PYTHONS:
            self.assertTrue(os.path.exists(py), "missing interpreter %s: this test fails, it never skips" % py)
            t = Tree({})
            self.addCleanup(t.close)
            with open(t.build, "w", encoding="utf-8") as fh:
                json.dump({"edits": [{"path": "__init__.py", "new_file_content": "X = 1\n"}], "tests": []}, fh)
            rc, out = t.run(py, env=clean_env())
            # since D13 (2026-10-03) the grader's path rule refuses a root __init__.py before the fuzz (it would shadow
            # the protected scripts/hermetic_test_check.py's package path), so the RED comes from the earlier guard;
            # either guard is the entry point refusing, and neither may ever read SUITES GREEN
            self.assertEqual(rc, 1, (py, out))
            self.assertNotIn("SUITES GREEN", out, (py, out))

    def test_module_names(self):
        cases = {"scripts/loop/adapters/__init__.py": "adapters", "scripts/loop/adapters/claude.py": "adapters.claude",
                 "scripts/x/__init__.py": "x", "scripts/loop/__init__.py": "loop", "scripts/loop/flat.py": "flat",
                 "scripts/flat.py": "flat", "plugin/runtime/brother/core/__init__.py": "plugin.runtime.brother.core",
                 "products/a/b.py": "products.a.b", "__init__.py": None, "../outside.py": None}
        root = os.path.abspath(os.sep + "tree")
        self.assertEqual({rel: land_apply.module_name(root, rel) for rel in cases}, cases)


class Helpers(unittest.TestCase):
    def test_one_call_one_outcome(self):
        self.assertEqual(child(self, "fx08one", RETURNS)[:2], ((0, 1, 0), []))
        got, said, _ = child(self, "fx08two", CRASHES)
        self.assertEqual(got, (1, 0, 0))
        self.assertEqual(len(said), 1)
        self.assertIn("fx08two.boom(None x1) -> RuntimeError: boom", said[0])

    def test_parent_never_imports_the_module_in_child_mode(self):
        child(self, "fx08_never_here", RETURNS)
        self.assertNotIn("fx08_never_here", sys.modules)

    def test_process_state_is_unchanged_in_the_parent(self):
        src = ("import os, sys\ndef mess(value):\n    sys.stdout = None\n    os.environ['FX08_LEAK'] = '1'\n"
               "    sys.argv[:] = ['hacked']\n    os.chdir('/')\n    return 1\n")
        before = (os.getcwd(), sys.argv[:], sys.stdout, os.environ.get("FX08_LEAK"))
        self.assertEqual(child(self, "fx08mess", src)[0], (0, 1, 0))
        self.assertEqual((os.getcwd(), sys.argv[:], sys.stdout, os.environ.get("FX08_LEAK")), before)

    def test_two_concurrent_landers_share_no_results_path(self):
        out = {}

        def go(key, src):
            out[key] = child(self, "fx08same", src, hostile=land_apply.HOSTILE)[0]
        threads = [threading.Thread(target=go, args=("r", RETURNS)), threading.Thread(target=go, args=("c", CRASHES))]
        for th in threads:
            th.start()
        for th in threads:
            th.join(120)
        self.assertEqual(out, {"r": (0, N, 0), "c": (N, 0, 0)})

    def test_existing_results_file_is_refused(self):
        home = tempfile.mkdtemp(prefix="fx08-home-")
        self.addCleanup(shutil.rmtree, home, True)
        with open(os.path.join(home, "fuzz-0.jsonl"), "w") as fh:
            fh.write('{"plan": 1}\n{"start": "echo", "value": "None", "n": 1}\n{"end": true, "crashes": 0, "returned": 1}\n')
        got, said, _ = child(self, "fx08stale", RETURNS, home=home)
        self.assertEqual(got, (1, 0, 0))
        self.assertIn("results path already existed", said[0])

    def test_sandbox_refusal_at_fuzz_is_red(self):
        real = land_apply.grade_build.sandboxed

        def refuse(cmd, root, tmp):
            raise land_apply.grade_build.SandboxRefused("profile vanished")
        land_apply.grade_build.sandboxed = refuse
        try:
            got, said, _ = child(self, "fx08sb", RETURNS)
        finally:
            land_apply.grade_build.sandboxed = real
        self.assertEqual((got, said), ((0, 0, 1), ["FUZZ sandbox refused profile vanished"]))

    def test_hostile_values_outside_the_list_are_refused(self):
        with self.assertRaises(ValueError):
            child(self, "fx08odd", RETURNS, hostile=[object()])


def results(test, text):
    d = tempfile.mkdtemp(prefix="fx08-res-")
    test.addCleanup(shutil.rmtree, d, True)
    p = os.path.join(d, "fuzz-0.jsonl")
    if text is not None:
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
    return p


CLEAN = '{"plan": 1}\n{"start": "f", "value": "None", "n": 1}\n{"end": true, "crashes": 0, "returned": 1}\n'


class ChildResults(unittest.TestCase):
    def read(self, text, rc=0, timed_out=False):
        return land_apply.read_child_results(results(self, text), "m", rc, timed_out, 120)

    def test_a_clean_file_reads_clean(self):
        self.assertEqual(self.read(CLEAN), (0, 1, [], 0))

    def test_unknown_record_is_a_crash(self):
        c, r, said, bad = self.read(CLEAN.replace('{"plan": 1}', '{"plan": 1, "extra": 1}'))
        self.assertEqual((c, r, bad), (1, 0, 0))
        self.assertIn("unreadable child result", said[0])

    def test_record_with_a_wrong_field_type_is_a_crash(self):
        c, r, said, bad = self.read(CLEAN.replace('{"plan": 1}', '{"plan": "1"}'))
        self.assertEqual((c, r, bad), (1, 0, 0))
        self.assertIn("unknown record shape", said[0])

    def test_two_end_records_are_a_crash(self):
        c, r, said, bad = self.read(CLEAN + '{"end": true, "crashes": 0, "returned": 1}\n')
        self.assertEqual((c, r, bad), (1, 0, 0))
        self.assertIn("out of order", said[0])

    def test_truncated_results_line_is_a_crash(self):
        c, r, said, bad = self.read(CLEAN[:-12])
        self.assertEqual((c, r, bad), (1, 0, 0))
        self.assertIn("unreadable child result", said[0])

    def test_missing_results_file_is_a_crash(self):
        c, r, said, bad = self.read(None, rc=-9)
        self.assertEqual((c, r, bad), (1, 0, 0))
        self.assertIn("unreadable child result: no results file", said[0])

    def test_missing_end_record_is_a_crash(self):
        c, r, said, bad = self.read('{"plan": 2}\n{"start": "f", "value": "None", "n": 1}\n', rc=0)
        self.assertEqual((c, bad), (1, 0))
        self.assertIn("child exit 0 after f(None x1), calls not run 1", said[0])

    def test_end_record_disagreeing_with_starts_is_a_crash(self):
        c, r, said, bad = self.read('{"plan": 2}\n{"start": "f", "value": "None", "n": 1}\n'
                                    '{"end": true, "crashes": 0, "returned": 1}\n')
        self.assertEqual((c, r, bad), (1, 0, 0))
        self.assertIn("unreadable child result", said[0])

    def test_end_record_disagreeing_with_crash_records_is_a_crash(self):
        c, r, said, bad = self.read('{"plan": 1}\n{"start": "f", "value": "None", "n": 1}\n'
                                    '{"end": true, "crashes": 1, "returned": 0}\n')
        self.assertEqual((c, r, bad), (1, 0, 0))
        self.assertIn("unreadable child result", said[0])

    def test_nonzero_exit_with_a_clean_end_is_a_crash(self):
        c, r, said, bad = self.read(CLEAN, rc=120)
        self.assertEqual((c, r, bad), (1, 1, 0))
        self.assertIn("child exit 120", said[0])

    def test_import_failure_record_is_red_not_a_crash(self):
        self.assertEqual(self.read('{"import_failed": "ImportError()"}\n', rc=3),
                         (0, 0, ["FUZZ import failed m: ImportError()"], 1))

    def test_timeout_names_the_last_started_call(self):
        c, r, said, bad = self.read('{"plan": 3}\n{"start": "f", "value": "0", "n": 2}\n', rc=-9, timed_out=True)
        self.assertEqual((c, bad), (1, 0))
        self.assertEqual(said, ["   CRASH m timed out after 120 s during f(0 x2), calls not run 2"])


class TimeoutSetting(unittest.TestCase):
    def test_timeout_setting_outside_range_keeps_default(self):
        for raw in ("abc", "0", "-5", "nan", "inf", "99999", "", "1800.5"):
            self.assertEqual(land_apply.fuzz_timeout(raw), (120, raw), raw)

    def test_a_valid_setting_replaces_the_default(self):
        self.assertEqual(land_apply.fuzz_timeout("2"), (2.0, None))
        self.assertEqual(land_apply.fuzz_timeout("1800"), (1800.0, None))
        self.assertEqual(land_apply.fuzz_timeout(None), (120, None))


if __name__ == "__main__":
    unittest.main()
