#!/usr/bin/env python3
"""Sandbox first (plan E, 2026-10-01): the screen relaxes only under proven containment, and containment is proven here by
real escape attempts run through grade_build.sandboxed() under the real profile. Nothing here prints a secret's content.

Run: python3 -B scripts/loop/test_sandbox_first.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grade_build as G  # noqa: E402

REAL_HOME = os.path.realpath(os.path.expanduser("~"))
NEEDS = unittest.skipUnless(G._sandbox_present(), "NO-DATA: no sandbox-exec on this host")


def build(code, path="scripts/loop/x.py"):
    return {"edits": [{"path": path, "new_file_content": code}]}


@NEEDS
class EscapeBattery(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sbx-root-", dir=os.path.expanduser("~/.claude/brother-scratch"))
        self.tmp = os.path.join(self.root, "tmp"); os.makedirs(self.tmp)
        self.outside = tempfile.mkdtemp(prefix="sbx-outside-", dir=os.path.expanduser("~/.claude/brother-scratch"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True); shutil.rmtree(self.outside, ignore_errors=True)

    def run_py(self, code):
        cmd = G.sandboxed([sys.executable, "-c", code], self.root, self.tmp)
        return subprocess.run(cmd, cwd=self.root, capture_output=True, text=True, timeout=60)

    def test_the_network_is_closed(self):
        r = self.run_py("import socket\ns=socket.socket(); s.settimeout(5)\ntry:\n s.connect(('1.1.1.1',80)); print('OPEN')\nexcept OSError: print('CLOSED')")
        self.assertIn("CLOSED", r.stdout, r.stdout + r.stderr)

    def test_writing_outside_the_build_is_refused(self):
        target = os.path.join(self.outside, "escape.txt")
        self.run_py("open(%r,'w').write('x')" % target)
        self.assertFalse(os.path.exists(target), "a sandboxed build wrote outside ROOT and TMP")

    def test_writing_inside_the_build_works(self):
        r = self.run_py("open(%r,'w').write('x'); print('WROTE')" % os.path.join(self.root, "inside.txt"))
        self.assertIn("WROTE", r.stdout, r.stdout + r.stderr)

    def test_a_real_secret_folder_is_not_readable(self):
        ssh = os.path.join(REAL_HOME, ".ssh")
        if not os.path.isdir(ssh):
            self.skipTest("NO-DATA: no real ~/.ssh to test against")
        r = self.run_py("import os\ntry:\n os.listdir(%r); print('LISTED')\nexcept OSError: print('DENIED')" % ssh)
        self.assertIn("DENIED", r.stdout, r.stdout + r.stderr)

    def test_a_started_process_is_confined_too(self):
        target = os.path.join(self.outside, "child.txt")
        self.run_py("import subprocess\nsubprocess.run(['/bin/sh','-c','echo x > %s'])" % target)
        self.assertFalse(os.path.exists(target), "a child process escaped the sandbox")

    def test_the_process_count_is_capped(self):
        r = self.run_py("import subprocess\nprint(subprocess.run(['/bin/sh','-c','ulimit -u'],capture_output=True,text=True).stdout.strip())")
        cap = int(r.stdout.strip() or 0)
        self.assertGreater(cap, 0, r.stdout + r.stderr)
        self.assertLessEqual(cap, G._proc_cap() + 64, "the cap must be about the user's count plus the headroom, never unlimited")


class MandatoryIsolation(unittest.TestCase):
    def test_no_sandbox_refuses_instead_of_running_bare(self):
        orig = G._sandbox_present
        G._sandbox_present = lambda: False
        try:
            env = {k: v for k, v in os.environ.items() if k != "BROTHER_SANDBOX"}
            with unittest.mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(G.SandboxRefused):
                    G.sandboxed(["true"], "/tmp", "/tmp")
                self.assertFalse(G.contained(), "no sandbox is never contained")
        finally:
            G._sandbox_present = orig


class TheScreenRelaxesOnlyWhenContained(unittest.TestCase):
    PROCESS = "import subprocess\nsubprocess.run(['true'])\n"
    DYNAMIC = "import os\nf = getattr(os, 'getcwd')\n"

    def test_process_and_dynamic_code_pass_only_when_contained(self):
        for code in (self.PROCESS, self.DYNAMIC):
            self.assertIsNotNone(G.unsafe(build(code), runners=[], contained=False), code)
            self.assertIsNone(G.unsafe(build(code), runners=[], contained=True), code)

    def test_credentials_network_and_unparseable_code_stay_refused_when_contained(self):
        for code in ("import urllib.request\nurllib.request.urlopen('http://x')\n",
                     "import socket\nsocket.create_connection(('x', 80))\n",
                     "import importlib\nimportlib.import_module('socket')\n",
                     "def f(:\n"):
            self.assertIsNotNone(G.unsafe(build(code), runners=[], contained=True), code)
        ssh = "p = '~/.ssh/id_ed25519'\n"
        if G.unsafe(build(ssh), runners=[], contained=False):
            self.assertIsNotNone(G.unsafe(build(ssh), runners=[], contained=True), "a credential literal refused strictly must stay refused")


if __name__ == "__main__":
    import unittest.mock  # noqa: F401
    unittest.main()
