#!/usr/bin/env python3
"""The grader's two containment walls: the safety screen, and the patch that must stay in the sandbox.

WHY THIS ONE. grade_build.py is the only thing standing between a model-written patch and code
this machine executes. Every build of every sub unit routes through it, and probe_build.py
imports its apply(), scratch() and safety regex rather than carrying its own, so a hole here is
a hole in both halves of the gate.

The two failures that would be expensive AND silent, which is what this test pins:

  1. unsafe() admitting a build that reads the keychain or opens a socket. The build is then
     EXECUTED in a sandbox that inherits the real environment. A widened screen produces no
     error and no red check, only a PASS on a build nobody read.
  2. apply() writing outside the scratch root. The grader advertises "never touches the real
     tree" and the whole loop is built on that sentence. A patch whose path escapes would edit
     the live checkout while the verdict line still reads PASS.

Two more directions are pinned because each was a measured incident, not a hypothesis:
  3. The screen must not refuse HONEST code. The module's own comment records two refusals on
     2026-09-20 (a scanner naming network symbols, a urllib.parse import) that cost real lanes.
     A screen tightened until nothing passes is as broken as one that passes everything.
  4. private_hits() with no readable private-names list must count every text as a hit. Its
     docstring says it fails closed; with an empty HOME that path is the one that actually runs.

Run: python3 scripts/test_grade_build_guard.py
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import grade_build as G  # noqa: E402


def build(*items, key="edits"):
    return {key: list(items)}


def edit(path, text, new=False):
    return {"path": path, "new_file_content": text} if new else {"path": path, "find": "X", "replace": text}


class SafetyScreenRefusesWhatItMustRefuse(unittest.TestCase):
    def test_network_keychain_and_dynamic_execution_are_refused(self):
        cases = {
            "socket import": "import socket\ns = socket.socket()\n",
            "urllib.request import": "import urllib.request\n",
            "from http.client": "from http.client import HTTPSConnection\n",
            "requests": "import requests\nrequests.get('x')\n",
            "keychain read": "cmd = 'security find-generic-password -s openrouter'\n",
            "the bridge itself": "import or_ask\n",
            "private key path": "open('~/.ssh/id_ed25519')\n",
            "eval": "def f(s):\n    return eval(s)\n",
            "exec": "def f(s):\n    exec(s)\n",
            "os.system": "import os\nos.system('ls')\n",
            "computed import": "import importlib\nimportlib.import_module(name)\n",
        }
        for why, text in cases.items():
            with self.subTest(why=why):
                self.assertIsNotNone(G.unsafe(build(edit("scripts/x.py", text))),
                                     "admitted a build that would %s" % why)

    def test_the_screen_covers_tests_and_mutations_not_only_edits(self):
        # a build is run as edits PLUS tests PLUS mutations; screening one list would be theatre
        for key in ("edits", "tests", "mutations"):
            with self.subTest(key=key):
                self.assertIsNotNone(G.unsafe(build(edit("scripts/x.py", "import socket\n"), key=key)))

    def test_an_unparseable_fragment_carrying_a_blocked_word_fails_closed(self):
        # cannot be judged by parsing, so it must fall back to the strict text match, never be waved through
        self.assertIsNotNone(G.unsafe(build(edit("scripts/x.py", "def f(:\n  import socket\n"))))

    def test_a_non_python_file_is_screened_as_text(self):
        self.assertIsNotNone(G.unsafe(build(edit("scripts/x.sh", "security find-generic-password -s openrouter\n"))))

    def test_honest_code_is_not_refused(self):
        # measured 2026-09-20: bare word matching refused two honest builds and blocked their lanes
        cases = {
            "urllib.parse": "from urllib.parse import urlparse\n",
            "a string naming a network symbol": "BLOCK = 'socket|urllib|requests'\n",
            "a literal local import": "import importlib\nm = importlib.import_module('json')\n",
            "a comment about sockets": "# this never opens a socket\ndef f():\n    return 1\n",
        }
        for why, text in cases.items():
            with self.subTest(why=why):
                self.assertIsNone(G.unsafe(build(edit("scripts/x.py", text, new=True))),
                                  "refused honest code: %s" % why)


class CommandRunnersMayRunLiteralCommandsAndNothingElse(unittest.TestCase):
    """Owner ruling 2026-09-22 (option A of docs/decisions/grader-subprocess-allow-list-2026-09-22.json): a path a
    unit names under command_runners may import subprocess; every call must carry a literal argv with no network
    binary, no remote git verb, no interpreter given code, no shell. Everything else is refused exactly as before."""
    R = {"scripts/git_location_guard.py"}

    def test_an_unlisted_path_is_refused_as_before(self):
        self.assertIsNotNone(G.unsafe(build(edit("scripts/x.py", "import subprocess\nsubprocess.run(['git', 'status'])\n")), runners=self.R))

    def test_a_listed_path_with_a_literal_local_git_command_passes(self):
        for text in ("import subprocess\nsubprocess.run(['git', 'rev-parse', '--git-dir'], capture_output=True, text=True)\n",
                     "from subprocess import run\nrun(('git', 'status', '--porcelain'), shell=False)\n",
                     "import subprocess as sp\nsp.check_output(args=['ls', '-la'])\n"):
            with self.subTest(text=text):
                self.assertIsNone(G.unsafe(build(edit("scripts/git_location_guard.py", text, new=True)), runners=self.R))

    def test_a_listed_path_is_still_refused_for_the_hostile_shapes(self):
        cases = {
            "a network binary": "import subprocess\nsubprocess.run(['curl', 'http://x'])\n",
            "a network binary by full path": "import subprocess\nsubprocess.run(['/usr/bin/curl', 'x'])\n",
            "a network binary behind env": "import subprocess\nsubprocess.run(['env', 'wget', 'x'])\n",
            "git reaching a remote": "import subprocess\nsubprocess.run(['git', 'fetch', 'origin'])\n",
            "an interpreter given code": "import subprocess\nsubprocess.run(['python3', '-c', 'import urllib.request'])\n",
            "a shell given code": "import subprocess\nsubprocess.run(['bash', '-c', 'curl x'])\n",
            "shell=True": "import subprocess\nsubprocess.run(['ls'], shell=True)\n",
            "shell from a variable": "import subprocess\nsubprocess.run(['ls'], shell=flag)\n",
            "a runtime composed argv": "import subprocess\nsubprocess.run(cmd)\n",
            "a non literal element": "import subprocess\nsubprocess.run(['git', verb])\n",
            "a shell string helper": "import subprocess\nsubprocess.getoutput('git status')\n",
            "an empty argv": "import subprocess\nsubprocess.run([])\n",
            "another network module beside subprocess": "import subprocess\nimport socket\n",
            "os.system in a runner": "import os\nos.system('ls')\n",
        }
        for why, text in cases.items():
            with self.subTest(why=why):
                self.assertIsNotNone(G.unsafe(build(edit("scripts/git_location_guard.py", text, new=True)), runners=self.R),
                                     "a command runner was allowed to %s" % why)

    def test_the_list_comes_from_the_plan_and_an_unreadable_plan_allows_nothing(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)   # left behind it landed in scripts/ once, where tempfile fell back to cwd
        plan = os.path.join(d, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            fh.write('{"units": [{"id": "R2", "command_runners": ["scripts/git_location_guard.py", " "]}, {"id": "X"}, "junk"]}')
        self.assertEqual(G.allowed_runners(plan), {"scripts/git_location_guard.py"})
        self.assertEqual(G.allowed_runners(os.path.join(d, "missing.json")), set())
        with open(plan, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(G.allowed_runners(plan), set())
        with open(plan, "w", encoding="utf-8") as fh:
            fh.write('{"units": "not a list"}')
        self.assertEqual(G.allowed_runners(plan), set())


class PatchesStayInsideTheSandbox(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="grade-guard-root-")
        self.outside = tempfile.mkdtemp(prefix="grade-guard-outside-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.outside, True)
        self.victim = os.path.join(self.outside, "victim.txt")
        with open(self.victim, "w", encoding="utf-8") as fh:
            fh.write("untouched")

    def test_an_escaping_path_is_refused_and_writes_nothing(self):
        rel = os.path.relpath(self.victim, self.root)
        self.assertTrue(rel.startswith(".."), "fixture is wrong: %s does not escape" % rel)
        problems = []
        n = G.apply(self.root, [{"path": rel, "new_file_content": "owned"}], problems, "edit")
        self.assertEqual(n, 0, "an escaping patch was applied")
        self.assertEqual(len(problems), 1, problems)
        with open(self.victim, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "untouched", "the grader wrote outside its scratch copy")

    def test_absolute_paths_and_dot_git_are_refused(self):
        for rel in (self.victim, "/etc/hosts", ".git/config", "a/.git/hooks/pre-commit"):
            with self.subTest(rel=rel):
                problems = []
                self.assertEqual(G.apply(self.root, [{"path": rel, "new_file_content": "x"}], problems, "edit"), 0)
                self.assertEqual(len(problems), 1, "%s was applied" % rel)
        self.assertFalse(os.path.exists("/etc/hosts.tmp"))

    def test_a_find_matching_twice_is_refused_and_the_file_is_left_alone(self):
        # a non unique find is the one edit shape that silently changes the wrong line
        target = os.path.join(self.root, "m.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("a = 1\na = 1\n")
        problems = []
        self.assertEqual(G.apply(self.root, [{"path": "m.py", "find": "a = 1", "replace": "a = 2"}], problems, "edit"), 0)
        self.assertEqual(len(problems), 1, problems)
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "a = 1\na = 1\n")

    def test_a_unique_find_does_apply(self):
        # the refusals above must not be a module that refuses everything
        target = os.path.join(self.root, "m.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("a = 1\nb = 2\n")
        problems = []
        self.assertEqual(G.apply(self.root, [{"path": "m.py", "find": "b = 2", "replace": "b = 3"}], problems, "edit"), 1)
        self.assertEqual(problems, [])
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "a = 1\nb = 3\n")


class OnlyAllowListedCommandsRun(unittest.TestCase):
    def test_an_arbitrary_done_check_is_dropped(self):
        for cmd in ("bash -c 'curl example.com'", "rm -rf /", "python3 -c 'import os'", "make test"):
            with self.subTest(cmd=cmd):
                cmds = G.test_cmds({"tests": [{"path": "scripts/test_ok.py"}], "done_check": cmd})
                self.assertNotIn(cmd, cmds, "an arbitrary command reached the runner")
                self.assertEqual(cmds, ["python3 -B scripts/test_ok.py"])


class PrivateTermsScanFailsClosed(unittest.TestCase):
    def test_an_unreadable_private_names_list_counts_every_text_as_a_hit(self):
        # with an empty HOME this IS the path that runs, and silence here would publish the terms
        home = tempfile.mkdtemp(prefix="grade-guard-home-")
        self.addCleanup(shutil.rmtree, home, True)
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            self.assertGreaterEqual(G.private_hits("an entirely ordinary sentence"), 1,
                                    "an unreadable private-names list read as clean")
        finally:
            if old is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old



class PreflightRefusesEachContractViolationAlone(unittest.TestCase):
    """One fixture per guard: deleting any single check inside preflight() turns exactly one of these red."""
    GOOD = {"edits": [{"path": "scripts/x.py", "find": "def f():", "replace": "def f():\n    return 1"}],
            "tests": [{"path": "scripts/test_x.py", "new_file_content": "import unittest\n"}],
            "done_check": "python3 scripts/test_x.py", "mutations": [{"path": "scripts/x.py", "find": "1", "replace": "2"}] * 3}
    def run_pf(self, **over):
        b = {k: (v if not isinstance(v, list) else list(v)) for k, v in self.GOOD.items()}; b.update(over)
        return G.preflight(b, exists=lambda p: p == "scripts", read=lambda p: "def f():\n    pass\n")
    def test_a_good_build_passes(self): self.assertEqual(self.run_pf(), "")
    def test_an_item_without_a_path_is_refused(self): self.assertIn("names no path", self.run_pf(edits=[{"find": "x", "replace": "y"}]))
    def test_a_new_file_that_does_not_parse_is_refused(self): self.assertIn("does not parse", self.run_pf(tests=[{"path": "scripts/test_x.py", "new_file_content": "def f(:\n"}]))
    def test_a_partial_replace_fragment_is_not_refused_for_parsing(self): self.assertEqual(self.run_pf(edits=[{"path": "scripts/x.py", "find": "def f():", "replace": "def f():\n    # header only"}]), "")
    def test_an_invented_tests_folder_is_refused(self): self.assertIn("folder", self.run_pf(tests=[{"path": "tests/test_x.py", "new_file_content": "import unittest\n"}]))
    def test_a_done_check_that_runs_no_test_is_refused(self): self.assertIn("runs no test", self.run_pf(done_check="echo ok"))
    def test_a_done_check_with_a_class_filter_or_verbose_is_accepted(self):
        # 2026-09-24: 32 spec sections wrote real invocations with a filter or -v and every copied one was refused
        for dc in ("python3 -B scripts/test_m.py LicenseTests -v", "python3 scripts/test_m.py TestX.test_y",
                   "python3 -B -m unittest pkg.test_m pkg.test_n -v"):
            self.assertNotIn("runs no test", self.run_pf(done_check=dc), dc)
    def test_a_test_beside_its_module_is_accepted(self):
        # 2026-09-25: H.md puts every NEW test beside its module (scripts/loop/test_*.py); H3.a's builder wrote
        # "python3 -B scripts/loop/test_unit_runner_h3a.py", this regex refused it in 7 grades, and H3.a exhausted
        for dc in ("python3 -B scripts/loop/test_unit_runner_h3a.py", "python3 scripts/loop/test_x.py Cls.test_y -v"):
            self.assertNotIn("runs no test", self.run_pf(done_check=dc), dc)
        for dc in ("python3 -B scripts/loop/deep/test_x.py", "python3 -B scripts/loop/x.py", "python3 -B scripts/../test_x.py"):
            self.assertIn("runs no test", self.run_pf(done_check=dc), dc)
    def test_a_chained_discover_or_one_liner_done_check_is_still_refused(self):
        for dc in ("python3 -B scripts/test_m.py && python3 -B scripts/test_n.py", "python3 -m unittest discover -s scripts -p test_x.py",
                   "python3 -B -c \"import m\"", "python3 scripts/loop/x.py --selftest"):
            self.assertIn("runs no test", self.run_pf(done_check=dc), dc)
    def test_a_find_that_is_not_unique_is_refused(self):
        b = dict(self.GOOD); self.assertIn("occurs 2 times", G.preflight(b, exists=lambda p: True, read=lambda p: "def f():\ndef f():\n"))
    def test_an_unreadable_target_is_refused(self):
        def boom(p): raise OSError("gone")
        self.assertIn("unreadable", G.preflight(dict(self.GOOD), exists=lambda p: True, read=boom))
    def test_fewer_than_three_mutations_is_refused(self): self.assertIn("mutation", self.run_pf(mutations=[self.GOOD["mutations"][0]]))

class SlotSandboxes(unittest.TestCase):
    """The persistent slot sandbox (2026-09-24, resource footprint): with a slot held, one git worktree per slot, repository and
    tag is reused and reset between grades; without a slot, a fresh clone as before. Each case isolates one property."""
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="sandbox-case-"); self.repo = os.path.join(self.d, "repo"); os.makedirs(self.repo)
        import subprocess
        self.git = lambda *a, cwd=None: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"] + list(a),
                                                       cwd=cwd or self.repo, capture_output=True, text=True, check=True)
        self.git("init", "-q"); open(os.path.join(self.repo, "a.txt"), "w").write("one\n"); self.git("add", "a.txt"); self.git("commit", "-q", "-m", "one")
        self.cwd = os.getcwd(); os.chdir(self.repo)
        self.sb, self.slot, self.per = G.SANDBOXES, G.SLOT[0], set(G.PERSISTENT)
        G.SANDBOXES = os.path.join(self.d, "sandboxes"); G.SLOT[0] = 0; G.PERSISTENT.clear()
    def tearDown(self):
        os.chdir(self.cwd); G.SANDBOXES, G.SLOT[0] = self.sb, self.slot; G.PERSISTENT.clear(); G.PERSISTENT.update(self.per)
        shutil.rmtree(self.d, ignore_errors=True)
    def test_a_slot_sandbox_is_a_worktree_reused_by_the_next_grade(self):
        r1 = G.scratch("r0"); r2 = G.scratch("r0")
        self.assertEqual(r1, r2); self.assertTrue(r1.startswith(G.SANDBOXES)); self.assertIn(r1, G.PERSISTENT)
        self.assertEqual(open(os.path.join(r1, "a.txt")).read(), "one\n")
    def test_the_next_grade_starts_from_a_reset_tree(self):
        r = G.scratch("r0"); open(os.path.join(r, "a.txt"), "w").write("mutated\n"); open(os.path.join(r, "junk.txt"), "w").write("x")
        os.makedirs(os.path.join(r, "tmp"), exist_ok=True); open(os.path.join(r, "tmp", "left"), "w").write("x")
        G.scratch("r0")
        self.assertEqual(open(os.path.join(r, "a.txt")).read(), "one\n"); self.assertFalse(os.path.exists(os.path.join(r, "junk.txt")))
        self.assertFalse(os.path.exists(os.path.join(r, "tmp", "left")))
    def test_the_working_tree_change_the_model_saw_is_carried_in(self):
        open(os.path.join(self.repo, "a.txt"), "w").write("two\n")
        self.assertEqual(open(os.path.join(G.scratch("r0"), "a.txt")).read(), "two\n")
    def test_tags_and_repositories_get_their_own_sandbox(self):
        r0, r1 = G.scratch("r0"), G.scratch("r1"); self.assertNotEqual(r0, r1)
        other = os.path.join(self.d, "other"); os.makedirs(other); self.git("init", "-q", cwd=other)
        open(os.path.join(other, "b.txt"), "w").write("b"); self.git("add", "b.txt", cwd=other); self.git("commit", "-q", "-m", "b", cwd=other)
        os.chdir(other); ro = G.scratch("r0"); self.assertNotEqual(ro, r0); self.assertTrue(os.path.exists(os.path.join(ro, "b.txt")))
        self.assertTrue(ro.startswith(G.SANDBOXES), "the second repository got a clone, not its own sandbox: %s" % ro)
    def test_without_a_slot_a_fresh_clone_is_made_and_is_not_persistent(self):
        G.SLOT[0] = None; r = G.scratch("r0")
        self.assertFalse(r.startswith(G.SANDBOXES)); self.assertNotIn(r, G.PERSISTENT); self.assertTrue(os.path.exists(os.path.join(r, "a.txt")))
        shutil.rmtree(r, ignore_errors=True)


class GradeEntryPoint(unittest.TestCase):
    """The grader at its entry point (auditor finding 2026-09-24: the in place mutation restore was exercised by no test).
    A tiny repository, a build that is red without its code and green with it, three mutations, a test module that
    writes a file into the tree on import. After the grade the green sandbox must hold the build's bytes, not the last
    mutation's, and none of the test's leftovers."""
    def setUp(self):
        import subprocess
        self.d = tempfile.mkdtemp(prefix="grade-entry-"); self.repo = os.path.join(self.d, "repo"); os.makedirs(os.path.join(self.repo, "pkg")); os.makedirs(os.path.join(self.repo, "docs", "plan"))
        g = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"] + list(a), cwd=self.repo, capture_output=True, text=True, check=True)
        open(os.path.join(self.repo, "pkg", "__init__.py"), "w").write(""); open(os.path.join(self.repo, "pkg", "m.py"), "w").write("def f(x):\n    return None\n")
        open(os.path.join(self.repo, "docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json"), "w").write('{"units": []}')
        g("init", "-q"); g("add", "."); g("commit", "-q", "-m", "base")
        self.build = os.path.join(self.d, "build.json")
        import json
        json.dump({"edits": [{"path": "pkg/m.py", "find": "return None", "replace": "return x + 1"}],
                   "tests": [{"path": "pkg/test_m.py", "new_file_content": "import unittest\nopen('junk.txt', 'w').write('x')\nfrom pkg import m\n\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(m.f(1), 2)\n"}],
                   "done_check": "python3 -B -m unittest pkg.test_m",
                   "mutations": [{"name": "off by one", "path": "pkg/m.py", "find": "return x + 1", "replace": "return x + 2"},
                                 {"name": "zero", "path": "pkg/m.py", "find": "return x + 1", "replace": "return 0"},
                                 {"name": "minus", "path": "pkg/m.py", "find": "return x + 1", "replace": "return x - 1"}]}, open(self.build, "w"))
        self.sandboxes = os.path.join(self.d, "sandboxes")
    def tearDown(self):
        import subprocess
        for s in os.listdir(self.sandboxes) if os.path.isdir(self.sandboxes) else []:
            subprocess.run(["git", "-C", self.repo, "worktree", "remove", "--force", os.path.join(self.sandboxes, s)], capture_output=True)
        shutil.rmtree(self.d, ignore_errors=True)
    def test_a_good_build_passes_and_the_green_sandbox_is_left_green(self):
        import subprocess
        if G.sandbox_ready():   # inside another sandbox (the hermetic push check) no grade can run: NO-DATA, never a verdict
            self.skipTest("NO-DATA: %s" % G.sandbox_ready())
        # the modern interpreter on PATH: the grader refuses to run AS the old one (one version would be exercised, not two)
        r = subprocess.run([shutil.which("python3") or "python3", "-B", G.__file__, self.build], cwd=self.repo, capture_output=True, text=True,
                           env=dict(os.environ, BROTHER_GRADE_SANDBOXES=self.sandboxes), timeout=600)
        self.assertIn("MUTATIONS          3 of 3 applied were caught", r.stdout, r.stdout + r.stderr)
        self.assertTrue(any(l.startswith("PASS") for l in r.stdout.splitlines()), r.stdout)
        r1 = [os.path.join(self.sandboxes, s) for s in os.listdir(self.sandboxes) if s.endswith("-r1")]
        self.assertEqual(len(r1), 1, os.listdir(self.sandboxes))
        self.assertIn("return x + 1", open(os.path.join(r1[0], "pkg", "m.py")).read(), "the last mutation was not undone")
        self.assertFalse(os.path.exists(os.path.join(r1[0], "junk.txt")), "a test's leftover survived into the next mutation")
    def test_a_grade_whose_only_reason_is_a_leg_that_never_ran_is_no_data_not_a_fail(self):
        # 2026-10-03: run AS the old interpreter, only one Python version is exercised; that reason alone judged nothing,
        # so the grader exits 3 with a NO-DATA verdict, never exit 1 (a FAIL unit_runner would pay a repair round for)
        import subprocess
        if G.sandbox_ready():
            self.skipTest("NO-DATA: %s" % G.sandbox_ready())
        if not os.path.exists(G.OLD_PYTHON):
            self.skipTest("NO-DATA: %s is absent here" % G.OLD_PYTHON)
        r = subprocess.run([G.OLD_PYTHON, "-B", G.__file__, self.build], cwd=self.repo, capture_output=True, text=True,
                           env=dict(os.environ, BROTHER_GRADE_SANDBOXES=self.sandboxes), timeout=600)
        lines = r.stdout.splitlines()
        self.assertEqual(r.returncode, 3, r.stdout[-1500:] + r.stderr[-500:])
        self.assertTrue(any(l.startswith("NO-DATA: ") for l in lines), r.stdout[-1500:])
        self.assertFalse(any(l.startswith("FAIL") for l in lines), r.stdout[-1500:])


import ast
import contextlib
import io
from unittest import mock


class SandboxedWrapsTheCommand(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="sandbox-root-"))
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="sandbox-tmp-"))
        self.profile = os.path.join(self.root, "sandbox.sb")
        with open(self.profile, "w", encoding="utf-8") as fh:
            fh.write("(version 1)\n")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ.pop("BROTHER_SANDBOX", None)
        # these tests describe a host where a profile applies; the probe's own answer is tested on its own below
        applies = mock.patch.object(G, "_APPLIES", [""]); applies.start(); self.addCleanup(applies.stop)

    def test_a_host_with_the_tool_gets_the_specified_wrapped_argv(self):
        cmd = ["python3", "-B", "scripts/test_x.py"]
        with mock.patch.object(G, "_sandbox_present", return_value=True), \
             mock.patch.object(G, "SANDBOX_PROFILE", self.profile), \
             mock.patch.object(G, "_proc_cap", return_value=750):
            out = G.sandboxed(cmd, self.root, self.tmp)
        expected = ["sandbox-exec", "-f", self.profile, "-D", "ROOT=" + self.root,
                    "-D", "TMP=" + self.tmp, "/bin/sh", "-c", 'ulimit -u 750 && exec "$@"', "sh"] + cmd
        self.assertEqual(out, expected)
        self.assertEqual(cmd, ["python3", "-B", "scripts/test_x.py"], "the input cmd was mutated")

    def test_a_host_without_the_tool_refuses_and_never_runs_bare(self):
        cmd = ["python3", "-B", "scripts/test_x.py"]
        with mock.patch.object(G, "_sandbox_present", return_value=False), \
             mock.patch.object(G, "SANDBOX_PROFILE", self.profile):
            with self.assertRaises(G.SandboxRefused) as ctx:
                G.sandboxed(cmd, self.root, self.tmp)
        self.assertIn("no sandbox-exec on this host", str(ctx.exception))

    def test_a_missing_profile_refuses_with_and_without_the_tool(self):
        missing = os.path.join(self.root, "missing.sb")
        cmd = ["python3", "-B", "scripts/test_x.py"]
        for present in (True, False):
            with self.subTest(present=present):
                os.environ.pop("BROTHER_SANDBOX", None)
                with mock.patch.object(G, "_sandbox_present", return_value=present), \
                     mock.patch.object(G, "SANDBOX_PROFILE", missing):
                    with self.assertRaises(G.SandboxRefused):
                        G.sandboxed(cmd, self.root, self.tmp)

    def test_the_opt_out_is_exactly_off_and_printed(self):
        cmd = ["python3", "-B", "scripts/test_x.py"]
        with mock.patch.object(G, "_sandbox_present", return_value=True), \
             mock.patch.object(G, "SANDBOX_PROFILE", self.profile):
            os.environ["BROTHER_SANDBOX"] = "off"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                out = G.sandboxed(cmd, self.root, self.tmp)
            self.assertEqual(out, cmd)
            self.assertIn("SANDBOX OFF: BROTHER_SANDBOX=off, running unsandboxed", buf.getvalue())
            for value in ("OFF", "0", "no", "on"):
                with self.subTest(value=value):
                    os.environ["BROTHER_SANDBOX"] = value
                    out = G.sandboxed(cmd, self.root, self.tmp)
                    self.assertNotEqual(out, cmd, "value %r was treated as an opt out" % value)
                    self.assertEqual(out[0], "sandbox-exec", "value %r did not get wrapped" % value)

    def test_hostile_arguments_are_refused_never_crash_never_accepted(self):
        good_cmd = ["python3", "-B", "scripts/test_x.py"]
        good_root = self.root
        good_tmp = self.tmp
        nan = float("nan")
        bad_cmds = [None, "ls", [], (), ["a", 1], ["a", None], [["x"]], [{}], True, nan, {}]
        for bad in bad_cmds:
            with self.subTest(kind="cmd", bad=bad):
                with self.assertRaises(G.SandboxRefused):
                    G.sandboxed(bad, good_root, good_tmp)
        bad_roots = [None, "", 0, True, [], b"x", "a\0b", nan]
        for bad in bad_roots:
            with self.subTest(kind="root", bad=bad):
                with self.assertRaises(G.SandboxRefused):
                    G.sandboxed(good_cmd, bad, good_tmp)
        bad_tmps = [None, "", 0, True, [], b"x", "a\0b", nan]
        for bad in bad_tmps:
            with self.subTest(kind="tmp", bad=bad):
                with self.assertRaises(G.SandboxRefused):
                    G.sandboxed(good_cmd, good_root, bad)

    def test_sandbox_ready_names_a_missing_profile_and_is_empty_when_opted_out(self):
        missing = os.path.join(self.root, "missing.sb")
        os.environ.pop("BROTHER_SANDBOX", None)
        with mock.patch.object(G, "SANDBOX_PROFILE", missing):
            why = G.sandbox_ready()
            self.assertIn(missing, why)
        os.environ["BROTHER_SANDBOX"] = "off"
        with mock.patch.object(G, "SANDBOX_PROFILE", missing):
            self.assertEqual(G.sandbox_ready(), "")
        with mock.patch.object(G, "SANDBOX_PROFILE", self.profile):
            self.assertEqual(G.sandbox_ready(), "")

    def test_sandbox_ready_names_a_host_without_sandbox_exec(self):
        # one condition: the profile exists and nobody opted out, only sandbox-exec is absent (2026-10-03: this read '',
        # ready, and sandboxed() refused on a second probe of its own)
        os.environ.pop("BROTHER_SANDBOX", None)
        with mock.patch.object(G, "SANDBOX_PROFILE", self.profile), mock.patch.object(G, "_sandbox_present", lambda: False):
            self.assertIn("no sandbox-exec", G.sandbox_ready())

    def test_sandbox_ready_names_a_profile_that_cannot_apply(self):
        os.environ.pop("BROTHER_SANDBOX", None)
        refused = G.subprocess.CompletedProcess([], 71, "", "sandbox-exec: sandbox_apply: Operation not permitted\n")
        with mock.patch.object(G, "SANDBOX_PROFILE", self.profile), mock.patch.object(G, "_APPLIES", []), \
                mock.patch.object(G, "_sandbox_present", lambda: True), mock.patch.object(G.subprocess, "run", lambda *a, **k: refused):
            self.assertIn("Operation not permitted", G.sandbox_ready())

    def test_a_grader_inside_the_sandbox_reads_not_ready(self):
        # the hermetic push check runs suites under sandbox.sb; a grader started there must say so before any run
        with mock.patch.object(G, "_APPLIES", []):   # the real probe, not this class's fixture answer
            if not G.contained() or G.sandbox_ready():
                self.skipTest("NO-DATA: no sandbox to nest inside here")
        loop = os.path.dirname(os.path.abspath(G.__file__))
        tmp = tempfile.mkdtemp(dir=self.root)
        c = G.sandboxed([sys.executable, "-B", "-c", "import sys; sys.path.insert(0, %r); import grade_build as G; "
                         "print('READY=' + repr(G.sandbox_ready()))" % loop], self.root, tmp)
        r = G.subprocess.run(c, capture_output=True, text=True, timeout=120, cwd=self.root,
                           env={k: v for k, v in os.environ.items() if k != "BROTHER_SANDBOX"})
        self.assertIn("READY=", r.stdout, r.stdout + r.stderr)
        self.assertIn("cannot apply", r.stdout, r.stdout + r.stderr)


class SandboxProfileIsSound(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop", "sandbox.sb")
        if not os.path.isfile(self.path):
            self.skipTest("sandbox.sb is missing: %s" % self.path)
        with open(self.path, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_it_uses_both_path_parameters(self):
        self.assertIn('(param "ROOT")', self.text)
        self.assertIn('(param "TMP")', self.text)

    def test_it_denies_network(self):
        self.assertIn("(deny network*)", self.text)

    def test_it_denies_file_write_broadly_then_allows_root_and_tmp(self):
        self.assertIn("(deny file-write*)", self.text)
        self.assertIn('(subpath (param "ROOT"))', self.text)
        self.assertIn('(subpath (param "TMP"))', self.text)

    def test_it_names_the_credential_store_by_character_class(self):
        self.assertIn("Key[c]hains", self.text)

    def test_the_loop_safety_screen_clears_the_profile(self):
        build = {"edits": [{"path": "scripts/loop/sandbox.sb", "new_file_content": self.text}]}
        self.assertIsNone(G.unsafe(build))


class SandboxIsWiredIntoEveryRunner(unittest.TestCase):
    def setUp(self):
        self.grade_path = G.__file__
        self.land_path = os.path.join(os.path.dirname(self.grade_path), "land_apply.py")

    def _parse(self, path):
        with open(path, encoding="utf-8") as fh:
            return ast.parse(fh.read())

    def _find_func(self, tree, name):
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        return None

    def test_run_wraps_the_argv_of_its_process_call(self):
        tree = self._parse(self.grade_path)
        run_fn = self._find_func(tree, "run")
        self.assertIsNotNone(run_fn, "no run function found in grade_build.py")
        found = False
        for node in ast.walk(run_fn):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                    if node.args:
                        first = node.args[0]
                        if isinstance(first, ast.Call):
                            if isinstance(first.func, ast.Name) and first.func.id == "sandboxed":
                                found = True
        self.assertTrue(found, "the process call in run is not wrapped in sandboxed")

    def test_main_refuses_early(self):
        tree = self._parse(self.grade_path)
        main_fn = self._find_func(tree, "_main")
        self.assertIsNotNone(main_fn, "no _main function found")
        sandbox_line = None
        scratch_line = None
        for node in ast.walk(main_fn):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "sandbox_ready":
                    if sandbox_line is None or node.lineno < sandbox_line:
                        sandbox_line = node.lineno
                if isinstance(node.func, ast.Name) and node.func.id == "scratch":
                    if scratch_line is None or node.lineno < scratch_line:
                        scratch_line = node.lineno
        self.assertIsNotNone(sandbox_line, "no sandbox_ready call in _main")
        self.assertIsNotNone(scratch_line, "no scratch call in _main")
        self.assertLess(sandbox_line, scratch_line, "sandbox_ready does not come before scratch")

    def test_land_apply_wraps_every_rerun_and_checks_first(self):
        tree = self._parse(self.land_path)
        main_fn = self._find_func(tree, "main")
        self.assertIsNotNone(main_fn, "no main function found in land_apply.py")
        sandbox_assignment_line = None
        process_line = None
        for node in ast.walk(main_fn):
            if isinstance(node, ast.Assign):
                if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "c":
                    if isinstance(node.value, ast.Call):
                        func = node.value.func
                        if isinstance(func, ast.Attribute) and func.attr == "sandboxed":
                            if isinstance(func.value, ast.Name) and func.value.id == "grade_build":
                                if node.value.args and isinstance(node.value.args[0], ast.Name) and node.value.args[0].id == "c":
                                    sandbox_assignment_line = node.lineno
            if isinstance(node, ast.Call):
                # land_apply starts each rerun through land_batch.run_group (its own process group) since 2026-10-02
                if isinstance(node.func, ast.Attribute) and node.func.attr in ("run", "run_group"):
                    if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "c":
                        process_line = node.lineno
        self.assertIsNotNone(sandbox_assignment_line, "no c = grade_build.sandboxed(c, ...) assignment found")
        self.assertIsNotNone(process_line, "no process call on c found")
        self.assertLess(sandbox_assignment_line, process_line, "wrapping does not come before the process call")
        sandbox_ready_line = None
        makedirs_line = None
        for node in ast.walk(main_fn):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "sandbox_ready":
                    if sandbox_ready_line is None or node.lineno < sandbox_ready_line:
                        sandbox_ready_line = node.lineno
                if isinstance(node.func, ast.Attribute) and node.func.attr == "makedirs":
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                        if makedirs_line is None or node.lineno < makedirs_line:
                            makedirs_line = node.lineno
        self.assertIsNotNone(sandbox_ready_line, "no sandbox_ready call in land_apply main")
        self.assertIsNotNone(makedirs_line, "no os.makedirs call in land_apply main")
        self.assertLess(sandbox_ready_line, makedirs_line, "sandbox_ready does not come before os.makedirs")


class AFailedRestoreIsCountedAsFailed(unittest.TestCase):
    """H4.c REQ-H-RESTORE, found by the BrotherSBE silent failure lint 2026-09-25 (grade_build.py:342): a git checkout that
    fails inside restore_tree was counted as undone, so the next mutation ran on a dirty tree."""

    def test_a_checkout_that_fails_returns_None(self):
        import subprocess
        root = tempfile.mkdtemp(); d = os.path.join(root, "d"); os.makedirs(d)
        run = lambda *a: subprocess.run(["git", "-C", root] + list(a), capture_output=True, text=True, check=True)
        run("init", "-q"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
        with open(os.path.join(d, "f.txt"), "w") as fh: fh.write("a\n")
        run("add", "."); run("commit", "-qm", "x")
        before = G.tree_state(root)
        with open(os.path.join(d, "f.txt"), "w") as fh: fh.write("b\n")
        os.chmod(os.path.join(d, "f.txt"), 0o444); os.chmod(d, 0o555)
        try:
            self.assertIsNone(G.restore_tree(root, before))
        finally:
            os.chmod(d, 0o755); shutil.rmtree(root, ignore_errors=True)


class ImportAllowlist(unittest.TestCase):
    def screen(self, code, **kwargs):
        return G.unsafe(build(edit("sample.py", code, new=True)), runners=set(), **kwargs)

    def test_unlisted_imports_are_refused(self):
        for code in ("import unexpected_vendor", "from unexpected_vendor import value",
                     "from . import local", "import socket", "import getpass",
                     "import importlib; importlib.import_module('unexpected_vendor')"):
            with self.subTest(code=code):
                self.assertIsNotNone(self.screen(code))

    def test_explicit_local_allowance_and_stdlib(self):
        for code in ("x = 1", "import json", "from pathlib import Path", "from urllib import parse",
                     "import urllib.parse", "import importlib; importlib.import_module('json')",
                     "# socket keychain\nvalue = 'socket'"):
            with self.subTest(code=code):
                self.assertIsNone(self.screen(code))
        self.assertIsNone(self.screen("from local_helper import value", allowed_imports={"local_helper"}))
        self.assertIsNotNone(self.screen("import local_helper_bad", allowed_imports={"local_helper"}))

    def test_computed_import_stays_refused(self):
        self.assertIsNotNone(self.screen("import importlib; importlib.import_module('j' + 'son')"))


class ContextAwareFragments(unittest.TestCase):
    """Codex B5 rehearsal, 2026-09-26: the deploy canary replays the recorded D6.6 build (landed 6a5a5d2e9 on base
    a5bab102f) and the screen refused it, "Python fragment cannot be parsed". Its test item appends module level code
    after an indented anchor line, and two of its mutations replace an if header with `if False:`: none parses alone,
    and every one is valid Python where it lands. A fragment that does not parse alone is judged IN CONTEXT: applied at
    its one anchor, after this build's own earlier items for that path, the whole file must parse, and every import and
    call inside the replaced lines meets the same screen. Missing, ambiguous or unreadable context is refused."""

    D66_TEST_PATH = "plugin/runtime/brother/core/test_dream_world.py"
    D66_TEST_BASE = 'import unittest\n\ntry:\n    from . import dream_world\nexcept (ImportError, ValueError):\n    import dream_world\n\n\nclass TestSchema(unittest.TestCase):\n    def test_parse_rejects_non_mapping(self):\n        for value in (None, [], "string", 123, 1.5, True):\n            with self.assertRaises(dream_world._ParseError):\n                dream_world._parse_record(value)\n\n    def test_digest_stable(self):\n        record = {"a": 1, "b": [2, 3], "c": {"d": "e"}}\n        d1 = dream_world._digest(record)\n        d2 = dream_world._digest(record)\n        self.assertEqual(d1, d2)\n        record2 = {"c": {"d": "e"}, "b": [2, 3], "a": 1}\n        self.assertEqual(dream_world._digest(record2), d1)\n\n    def test_digest_content_sensitive(self):\n        d1 = dream_world._digest({"a": 1})\n        d2 = dream_world._digest({"a": 2})\n        self.assertNotEqual(d1, d2)\n'
    D66_TEST_FIND = "        self.assertNotEqual(d1, d2)"
    # the first lines of the recorded 23015 character replacement, verbatim
    D66_TEST_REPLACE = "        self.assertNotEqual(d1, d2)\n\n\nimport ast\nimport dataclasses\nimport os\nimport tempfile\n"
    D66_SRC_PATH = "plugin/runtime/brother/core/dream_world.py"
    D66_SRC_BASE = (
        "def build_world(records, WorldBuildResult):\n"
        "    if records is None:\n"
        "        return WorldBuildResult(status=\"NO-DATA\", world=None, diagnostics=(\"no_records\",))\n"
        "    if isinstance(records, (str, bytes)):\n"
        "        return WorldBuildResult(status=\"BLOCKED\", world=None, diagnostics=(\"unparseable\",))\n"
        "    try:\n"
        "        records_list = list(records)\n"
        "    except TypeError:\n"
        "        return WorldBuildResult(status=\"BLOCKED\", world=None, diagnostics=(\"unparseable\",))\n"
        "    return records_list\n")
    D66_EDIT = {"path": D66_SRC_PATH,
                "find": "    if isinstance(records, (str, bytes)):\n        return WorldBuildResult(status=\"BLOCKED\", world=None, diagnostics=(\"unparseable\",))\n    try:\n        records_list = list(records)\n    except TypeError:\n        return WorldBuildResult(status=\"BLOCKED\", world=None, diagnostics=(\"unparseable\",))",
                "replace": "    if not isinstance(records, (list, tuple)):\n        return WorldBuildResult(status=\"BLOCKED\", world=None, diagnostics=(\"unparseable\",))\n    records_list = list(records)"}
    # recorded mutation 0, verbatim: its anchor exists only AFTER the build's own edit
    D66_MUT = {"path": D66_SRC_PATH, "find": "    if not isinstance(records, (list, tuple)):", "replace": "    if False:"}

    def screen(self, files, b):
        """unsafe() exactly as the grader's entry point calls it, from the root that holds the base files."""
        root = tempfile.mkdtemp(prefix="ctx-frag-")
        old = os.getcwd()
        try:
            for rel, text in files.items():
                os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
                with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
                    fh.write(text)
            os.chdir(root)
            return G.unsafe(b, allowed_imports=G._build_imports(b))
        finally:
            os.chdir(old); shutil.rmtree(root, ignore_errors=True)

    def test_the_recorded_d66_test_item_passes_in_context(self):
        item = {"path": self.D66_TEST_PATH, "find": self.D66_TEST_FIND, "replace": self.D66_TEST_REPLACE}
        self.assertIsNone(self.screen({self.D66_TEST_PATH: self.D66_TEST_BASE}, {"tests": [item]}))

    def test_the_recorded_d66_mutation_passes_after_the_build_edit(self):
        b = {"edits": [self.D66_EDIT], "mutations": [self.D66_MUT]}
        self.assertIsNone(self.screen({self.D66_SRC_PATH: self.D66_SRC_BASE}, b))

    def unsafe_line(self, line):
        # the recorded D6.6 test shape, with one hostile line inside the appended module level code
        item = {"path": self.D66_TEST_PATH, "find": self.D66_TEST_FIND, "replace": self.D66_TEST_REPLACE + line + "\n"}
        return self.screen({self.D66_TEST_PATH: self.D66_TEST_BASE}, {"tests": [item]})

    def test_an_unsafe_import_inside_the_replaced_lines_is_refused(self):
        self.assertIn("imports socket", self.unsafe_line("import socket") or "")

    def test_an_unsafe_call_inside_the_replaced_lines_is_refused(self):
        self.assertIn("calls system()", self.unsafe_line("os.system('true')") or "")

    def test_a_base_import_outside_the_replaced_lines_is_not_charged_to_the_build(self):
        base = "import subprocess\n" + self.D66_TEST_BASE
        item = {"path": self.D66_TEST_PATH, "find": self.D66_TEST_FIND, "replace": self.D66_TEST_REPLACE}
        self.assertIsNone(self.screen({self.D66_TEST_PATH: base}, {"tests": [item]}))

    def test_a_missing_anchor_is_refused(self):
        item = {"path": self.D66_TEST_PATH, "find": "        self.assertTrue(nothing_here)", "replace": self.D66_TEST_REPLACE}
        self.assertIn("anchor occurs 0 times", self.screen({self.D66_TEST_PATH: self.D66_TEST_BASE}, {"tests": [item]}) or "")

    def test_an_ambiguous_anchor_is_refused(self):
        base = self.D66_TEST_BASE + self.D66_TEST_FIND + "\n"
        item = {"path": self.D66_TEST_PATH, "find": self.D66_TEST_FIND, "replace": self.D66_TEST_REPLACE}
        self.assertIn("anchor occurs 2 times", self.screen({self.D66_TEST_PATH: base}, {"tests": [item]}) or "")

    def test_invalid_python_once_applied_is_refused(self):
        item = {"path": self.D66_SRC_PATH, "find": "    if records is None:", "replace": "    if records is None"}
        self.assertIn("not valid Python once", self.screen({self.D66_SRC_PATH: self.D66_SRC_BASE}, {"edits": [item]}) or "")

    def test_an_unreadable_target_is_refused(self):
        item = {"path": self.D66_TEST_PATH, "find": self.D66_TEST_FIND, "replace": self.D66_TEST_REPLACE}
        self.assertIn("target is unreadable", self.screen({}, {"tests": [item]}) or "")

    def test_an_unparseable_new_file_is_never_screened_through_a_replace_beside_it(self):
        # M-CTX-ANY-KEY survived without this: an item carrying BOTH a whole new file and a find/replace would have its
        # unparseable new file judged through the harmless replace text, and the new file would never be screened.
        item = {"path": self.D66_SRC_PATH, "new_file_content": "import socket\nif True\n",
                "find": "    return records_list", "replace": "    return records_list"}
        self.assertIn("cannot be parsed", self.screen({self.D66_SRC_PATH: self.D66_SRC_BASE}, {"edits": [item]}) or "")

    def test_a_fragment_that_parses_alone_is_screened_exactly_as_before(self):
        item = {"path": self.D66_SRC_PATH, "find": "    return records_list", "replace": "    import socket"}
        self.assertIn("imports socket", self.screen({self.D66_SRC_PATH: self.D66_SRC_BASE}, {"edits": [item]}) or "")



class SplitHunksJudgedTogether(unittest.TestCase):
    """2026-10-03, D2.6 round 4 (EXHAUSTED after 5 rounds): a native session's diff opened a call in one hunk
    (`result = wrap(dispatch(`) and closed it in a later hunk of the same file (`))`). Applied in order the file parses;
    the screen judged each hunk with only the EARLIER ones applied, so the half open call read as "not valid Python once
    its fragment is applied". A fragment that is invalid alone in place is judged with the build's LATER items for the
    same path applied too; only its own replaced lines are charged to it, and a later item overlapping it is refused."""

    PATH = "scripts/loop/subject_mod.py"
    BASE = ("import json\n\n\ndef run(dispatch):\n    result = dispatch(\n        1,\n        2,\n    )\n"
            "    return json.dumps(result)\n")
    OPEN = {"path": PATH, "find": "    result = dispatch(\n", "replace": "    result = wrap(dispatch(\n"}
    CLOSE = {"path": PATH, "find": "        2,\n    )\n", "replace": "        2,\n    ))\n"}

    def screen(self, b, base=None):
        root = tempfile.mkdtemp(prefix="split-hunk-")
        old = os.getcwd()
        try:
            os.makedirs(os.path.join(root, os.path.dirname(self.PATH)))
            with open(os.path.join(root, self.PATH), "w", encoding="utf-8") as fh:
                fh.write(self.BASE if base is None else base)
            os.chdir(root)
            return G.unsafe(b, allowed_imports=G._build_imports(b))
        finally:
            os.chdir(old); shutil.rmtree(root, ignore_errors=True)

    def test_a_call_opened_in_one_hunk_and_closed_in_a_later_one_passes(self):
        self.assertIsNone(self.screen({"edits": [self.OPEN, self.CLOSE]}))

    def test_the_same_pair_as_tests_items_passes(self):
        self.assertIsNone(self.screen({"edits": [self.OPEN], "tests": [self.CLOSE]}))

    def test_an_unsafe_import_inside_the_opening_hunk_is_still_refused(self):
        bad = dict(self.OPEN, replace="    import socket\n" + self.OPEN["replace"])
        self.assertIn("imports socket", self.screen({"edits": [bad, self.CLOSE]}) or "")

    def test_the_span_follows_a_later_item_that_inserts_lines_above_it(self):
        bad = dict(self.OPEN, replace="    import socket\n" + self.OPEN["replace"])
        above = {"path": self.PATH, "find": "import json\n", "replace": "import json\n" + "X = 1\n" * 30}
        self.assertIn("imports socket", self.screen({"edits": [bad, self.CLOSE, above]}) or "")

    def test_a_base_import_outside_the_hunk_is_not_charged_to_it(self):
        base = "import subprocess\n" + self.BASE
        self.assertIsNone(self.screen({"edits": [self.OPEN, self.CLOSE]}, base=base))

    def test_a_pair_that_never_closes_is_refused(self):
        self.assertIn("not valid Python once", self.screen({"edits": [self.OPEN]}) or "")

    def test_a_later_item_overlapping_the_fragment_is_refused(self):
        over = {"path": self.PATH, "find": "wrap(dispatch(\n        1,", "replace": "wrap(dispatch(\n        3,"}
        self.assertIn("overlaps", self.screen({"edits": [self.OPEN, over, self.CLOSE]}) or "")

    def test_a_later_whole_file_rewrite_is_refused(self):
        whole = {"path": self.PATH, "new_file_content": "X = 1\n"}
        self.assertIn("rewrites the whole file", self.screen({"edits": [self.OPEN, whole]}) or "")


class ABuildsOwnModuleByItsBareName(unittest.TestCase):
    """2026-09-28: tests import their sibling by its stem, so the build's own new module must pass under its bare name
    as it did under its dotted one, while the module's content is still screened and no stem aliases a guarded name."""

    def screen(self, b):
        root = tempfile.mkdtemp(prefix="bare-name-")
        old = os.getcwd()
        try:
            os.chdir(root)
            return G.unsafe(b, allowed_imports=G._build_imports(b))
        finally:
            os.chdir(old); shutil.rmtree(root, ignore_errors=True)

    def build(self, path, content, test_import):
        return {"edits": [{"path": path, "new_file_content": content}],
                "tests": [{"path": "scripts/test_subject.py", "new_file_content": test_import + "\n"}]}

    def test_a_new_scripts_module_imported_by_its_stem_passes(self):
        self.assertIsNone(self.screen(self.build("scripts/coe_x_driver.py", "def f():\n    return 1\n", "import coe_x_driver")))

    def test_a_new_loop_module_imported_by_its_stem_passes(self):
        self.assertIsNone(self.screen(self.build("scripts/loop/coe_y.py", "X = 1\n", "from coe_y import X")))

    def test_the_modules_own_content_is_still_screened(self):
        self.assertIn("imports subprocess", self.screen(self.build("scripts/coe_z.py", "import subprocess\n", "import coe_z")) or "")

    def test_a_stem_that_names_a_denied_module_is_still_refused(self):
        self.assertIn("imports ctypes", self.screen(self.build("scripts/ctypes.py", "X = 1\n", "import ctypes")) or "")

    def test_a_deeper_path_admits_no_bare_stem(self):
        self.assertNotIn("deep_mod", G._build_imports(self.build("scripts/pkg/deep_mod.py", "X = 1\n", "import os")))


class ABaseLiteralIsNotTheBuildsOwn(unittest.TestCase):
    """2026-10-02: a whole file rewrite was judged on every string in it, so a test file that already names the bridge
    it tests refused every build touching it. Only a protected literal the build ADDS is refused."""
    BASE = "import unittest\nBRIDGE = 'or_ask.py'\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(1)\n"

    def _build(self, content):
        return {"edits": [], "tests": [{"path": "core/test_x.py", "new_file_content": content}], "mutations": []}

    def test_a_literal_the_base_already_holds_passes(self):
        content = self.BASE + "    def test_b(self):\n        self.assertEqual(BRIDGE, 'or_ask.py')\n"
        self.assertIsNone(G.unsafe(self._build(content), read=lambda p: self.BASE))

    def test_a_new_protected_literal_is_still_refused(self):
        content = self.BASE + "    def test_b(self):\n        self.assertTrue('security find-generic-password')\n"
        why = G.unsafe(self._build(content), read=lambda p: self.BASE)
        self.assertIn("protected path or credential", why or "")

    def test_a_whole_file_replace_keeps_its_base_literals_too(self):
        # 2026-10-02: the Claude native adapter emits find (the whole base file) and replace (the new file)
        content = self.BASE + "    def test_b(self):\n        self.assertEqual(BRIDGE, 'or_ask.py')\n"
        b = {"edits": [], "tests": [{"path": "core/test_x.py", "find": self.BASE, "replace": content}], "mutations": []}
        self.assertIsNone(G.unsafe(b, read=lambda p: self.BASE))
        b["tests"][0]["replace"] = content + "X = 'security find-generic-password'\n"
        self.assertIn("protected path or credential", G.unsafe(b, read=lambda p: self.BASE) or "")

    def test_an_unreadable_base_judges_every_literal(self):
        def gone(p):
            raise OSError("no such file")
        why = G.unsafe(self._build(self.BASE), read=gone)
        self.assertIn("protected path or credential", why or "")

class TheFrozenChecksImportClosureIsProtected(unittest.TestCase):
    """Review 15 finding 3 (2026-10-03): PROTECTED_FILES carried a hand written slice of the modules the pre-commit check,
    the push gate, the hermetic check and the export builder import, and missed 21 of them, so a build could edit a module
    the NEXT landing's frozen checks would import. The closure is computed here, mechanically, with the hermetic check's
    own import walker over scripts/, scripts/loop/ and plugin/runtime/brother/core/, and every module in it must be a
    protected path. A new import in any of those checks goes red here until it is protected."""

    # Review 16 (2026-10-03): the entries are every file the lander runs or imports from the frozen copy outside boxed(),
    # not only the four frozen checks: the closer (close_unit.py, a plain process at closing_pass), the failure ledger
    # (record_close_red), the D13 policy and dream_gate (imported in process by gate_order and record_gate), and the
    # lander itself. No exemption remains: export_public.py was writable only while unit T1 was open, and T1 is DONE.
    ENTRIES = ("scripts/self_check_staged.py", "scripts/pre_push_gate.py", "scripts/hermetic_test_check.py",
               "scripts/cut_preflight.py", "scripts/close_unit.py", "scripts/failure_ledger.py", "scripts/loop/land_batch.py",
               "plugin/runtime/brother/core/dream_gate.py", "plugin/runtime/brother/core/dream_gate_policy.py")

    @classmethod
    def closure(cls):
        import sysconfig
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import hermetic_test_check as H
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bodies = {}
        for folder in H.REACH_DIRS:
            directory = os.path.join(repo, folder)
            for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
                if name.endswith(".py"):
                    with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as fh:
                        bodies[folder + "/" + name] = fh.read()
        modules, packages = {}, set()   # the index tests_reaching builds, so the walk resolves the same spellings
        for path in bodies:
            parts = path[:-3].split("/")
            for start in range(len(parts)):
                modules.setdefault(".".join(parts[start:]), set()).add(path)
                for end in range(start + 1, len(parts)):
                    packages.add(".".join(parts[start:end]))
        stdlib = set(sys.builtin_module_names)
        for directory in (sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib")):
            for folder in (directory, os.path.join(directory, "lib-dynload")):
                if os.path.isdir(folder):
                    stdlib.update(name.split(".")[0] for name in os.listdir(folder))
        seen, todo = set(), list(cls.ENTRIES)
        while todo:
            path = todo.pop()
            if path in seen or path not in bodies:
                continue
            seen.add(path)
            found, _ = H._reach_imports(path, bodies[path], modules, packages, stdlib)
            todo.extend(f for f in found if f not in seen)
        return seen

    def test_every_module_the_frozen_checks_import_is_protected(self):
        protected = {p.lower() for p in G.PROTECTED_FILES}
        closure = self.closure()
        self.assertTrue(set(self.ENTRIES) <= closure, "the entries read themselves")
        self.assertGreater(len(closure), len(self.ENTRIES), "the walk reached past the entry points")
        # every member, test_ modules included (review 17 finding 5: they were dropped, and a dropped member is a hole)
        missing = sorted(p for p in closure if p.lower() not in protected)
        self.assertEqual(missing, [], "%d module(s) the frozen checks import are not protected: %s" % (len(missing), missing))

    def test_nothing_that_shadows_a_closure_member_is_a_build_write(self):
        # REVIEW 17 FINDING 5: the walk modelled modules only. For each member: every __init__ on its package path, a
        # package directory and an extension module named for it in its folder; and a stdlib named module at the top of
        # each folder the loop puts on sys.path. Each must be refused by dest, the one check every build write goes through.
        d = tempfile.mkdtemp(prefix="review17-")
        self.addCleanup(shutil.rmtree, d, True)
        open_ = []
        for member in sorted(self.closure()):
            parts = member[:-3].split("/")
            shapes = ["/".join(parts[:i]) + "/__init__.py" for i in range(1, len(parts))]
            shapes += [member[:-3] + "/__init__.py", member[:-3] + ".so", member[:-3] + ".cpython-313-darwin.so"]
            open_ += [p for p in shapes if G.dest(d, p)[0] is not None]
        for folder in G.SHADOW_DIRS:
            for name in ("json", "shlex", "subprocess", "_winapi", "msvcrt", "tomllib", "imp"):
                open_ += [p for p in (folder + "/" + name + ".py", folder + "/" + name + "/__init__.py") if G.dest(d, p)[0] is not None]
        self.assertEqual(open_, [], "%d shadowing path(s) a build may still write: %s" % (len(open_), open_[:10]))

    def test_the_shadow_check_can_fail(self):
        with mock.patch.object(G, "shadow_reason", lambda rel: ""):
            with self.assertRaises(AssertionError):
                self.test_nothing_that_shadows_a_closure_member_is_a_build_write()

    def test_the_walk_can_fail(self):
        # a protected list missing one closure member reads red: the check is not a tautology over the entries alone
        closure = self.closure()
        inner = sorted(closure - set(self.ENTRIES))
        self.assertTrue(inner)
        with mock.patch.object(G, "PROTECTED_FILES", tuple(p for p in G.PROTECTED_FILES if p.lower() != inner[0].lower())):
            with self.assertRaises(AssertionError):
                self.test_every_module_the_frozen_checks_import_is_protected()


class TheLandersOwnUnboxedReadsAreProtected(unittest.TestCase):
    """Review 16 (2026-10-03), one regression per finding, each through grade_build.dest, the one check every build
    write goes through: a build that could write these lands them in the frozen copy the NEXT landing runs unboxed."""

    def refused(self, path):
        d = tempfile.mkdtemp(prefix="review16-")
        self.addCleanup(shutil.rmtree, d, True)
        full, why = G.dest(d, path)
        return full is None and "the landing trusts" in why

    def test_finding_1_the_closers_imports_are_never_a_build_write(self):
        # close_unit.py runs at closing_pass as a plain process and imports plan_store, provenance and plan_lint
        for path in ("scripts/loop/plan_store.py", "scripts/loop/provenance.py", "scripts/loop/plan_lint.py",
                     "scripts/loop/spec_check.py", "scripts/loop/land_batch.py"):
            self.assertTrue(self.refused(path), path)

    def test_finding_2_what_gate_order_and_the_ledger_load_is_never_a_build_write(self):
        # gate_order and record_gate import these in process; record_close_red runs the ledger unboxed
        for path in ("plugin/runtime/brother/core/dream_gate_policy.py", "plugin/runtime/brother/core/dream_gate.py",
                     "scripts/failure_ledger.py"):
            self.assertTrue(self.refused(path), path)

    def test_finding_3_export_public_is_never_a_build_write(self):
        # the frozen push gate imports it in process (hermetic_test_check.check, export_tree_builder)
        self.assertTrue(self.refused("scripts/export_public.py"))


class RootShadowsNeverLand(unittest.TestCase):
    """D13 follow-up (2026-10-03, Astra option A): the driver's inline `python3 -c` and stdin launches run with the landing
    tree as their working directory, which Python puts first on sys.path (3.9's -I keeps it). A build that wrote a
    standard library name or a startup hook at the repository root would load there, outside the sandbox. Through
    land_apply.apply_build, the one writer of a landed build: each payload shape is refused and never written, one shape
    per case; the controls still write."""

    def setUp(self):
        import land_apply
        self.apply = land_apply.apply_build
        self.root = tempfile.mkdtemp(prefix="root-shadow-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def refused(self, path):
        with self.assertRaises(SystemExit) as cm:
            self.apply(self.root, [{"path": path, "new_file_content": "open('MARK', 'w').write('ran')\n"}])
        self.assertIn("REFUSED", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.root, path)), "the payload was written: %s" % path)

    def test_a_standard_library_module_at_the_root_is_refused(self):
        self.refused("re.py")

    def test_a_standard_library_package_at_the_root_is_refused(self):
        self.refused("json/__init__.py")

    def test_a_sitecustomize_at_the_root_is_refused(self):
        self.refused("sitecustomize.py")

    def test_a_usercustomize_in_an_import_folder_is_refused(self):
        self.refused("scripts/usercustomize.py")

    def test_a_path_file_at_the_root_is_refused(self):
        self.refused("x.pth")

    def test_the_controls_still_write(self):
        for path in ("code.md", "docs/re.py", "myre.py"):
            with self.subTest(path=path):
                self.assertEqual(self.apply(self.root, [{"path": path, "new_file_content": "x = 1\n"}]), [path])




class AGraderCrashJudgedNothing(unittest.TestCase):
    """review 2026-10-03 A1: any crash of the grader itself exits 3 with a NO-DATA line, never exit 1 (a FAIL that
    unit_runner would read as a judged rejection and pay a repair round for)."""
    def test_a_crash_inside_the_grade_is_no_data_exit_3(self):
        out = io.StringIO()
        with mock.patch.object(G, "_main", side_effect=RuntimeError("boom")), \
                mock.patch.object(G, "local_slot", contextlib.nullcontext), contextlib.redirect_stdout(out):
            rc = G.main()
        self.assertEqual(rc, 3)
        self.assertIn("NO-DATA grader crashed: RuntimeError: boom", out.getvalue())

if __name__ == "__main__":
    unittest.main()
