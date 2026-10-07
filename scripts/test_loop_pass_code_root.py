#!/usr/bin/env python3
"""U3 (B5-04, B5-08, objection 17) for loop_pass.sh: its six sites run the frozen code root, never the landing tree.

THE SIX SITES (DESIGN-FINAL.md U3 item 4, loop_pass.sh lines as read at 8de64b498): the sizing eval (22), the
detached reprobe (133) and diagnosis (141), the worktree fingerprint before (169) and at (178) the landing, and the
closer (216). test_code_root_tripwire.py claims them for Lane D; this file drives them.

THE SHAPE. A scratch landing tree T (a git checkout with a bare upstream, so the pass reaches its landing and refill)
holds a TRIPWIRE copy of each of those five scripts: it records itself and exits 97. A code root C holds a WITNESS copy
of each: it records itself. HOME's bin holds zero cost stubs for everything the pass runs from the bin. The real
loop_pass.sh runs with cwd T; data stays on T (the closer and the reprobe read docs/plan relative to it).
Cases, one condition each:
  1. BROTHER_CODE_ROOT=C: every site leaves a witness from C, the tripwire log stays empty.
  2. The code root's fingerprint read fails (no output and exit 2; output and exit 2; no output and exit 0): the
     pass refuses to land (exit 44, LOOP BLOCKED) and land_batch is never called. It used to skip the foreign write
     check on an empty snapshot and land.
  3. A proof phase with no BROTHER_CODE_ROOT: BLOCKED before any site runs, landing tree code included.
  4. Outside a proof, no BROTHER_CODE_ROOT: no landing tree code runs unboxed (review 17 finding 4): sizing and
     diagnosis are skipped by name, the tree's sentry runs boxed through the lander, and nothing escapes.
Run: python3 -B scripts/test_loop_pass_code_root.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PASS = os.path.join(HERE, "loop", "loop_pass.sh")
SITES = ("adaptive_sizing.py", "probe_round.py", "diag_round.py", "worktree_sentry.py", "close_unit.py")
WITNESS = ('#!/usr/bin/env python3\nimport os, sys\n'
           'open(os.path.join(os.environ["HOME"], "witness"), "a").write("%s " + os.path.basename(__file__) + " " + " ".join(sys.argv[1:]) + "\\n")\n')
TRIPWIRE = ('#!/usr/bin/env python3\nimport os, sys\n'
            'open(os.path.join(os.environ["HOME"], "tripwire"), "a").write(os.path.realpath(__file__) + "\\n")\nsys.exit(97)\n')
LAND_BATCH_STUB = '''import os, subprocess, sys
if sys.argv[1:2] == ["--boxed"]:   # the real lander's box (review 17 finding 4): the pass runs the tree's sentry through it
    os.execv(sys.executable, [sys.executable, "-B", REAL_LANDER] + sys.argv[1:])
open(os.path.join(os.environ["HOME"], "landed"), "a").write(" ".join(sys.argv[1:]) + "\\n")
if "--close" in sys.argv:   # the driver's closer (review 15 finding 2): from a frozen copy of hub's head, nothing eligible here
    print("nothing to close: no unit has every sub unit landed with a done_check waiting"); sys.exit(1)
if "--push" in sys.argv:
    g = ["git", "-c", "core.hooksPath=/dev/null"]
    if subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
        subprocess.run(g + ["commit", "-q", "-am", "close: plan"], check=True)
    subprocess.run(g + ["push", "-q", "origin", "HEAD:main"], check=True)
    print("PUSH    exit 0 | PARITY OK"); sys.exit(0)
print("LANDED U1")
'''
# the installed git hooks, standing in for the tree's scripts they exec (pre_push_hook.sh, self_check_staged.py): each
# leaves a tripwire and refuses, so a pass that reaches one is read from the file, never guessed from its exit code
REFUSING_HOOK = '#!/bin/sh\necho "$0" >> "$HOME/tripwire"\nexit 1\n'


def w(path, body, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, mode)


def git(cwd, *args):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout


class PassCodeRoot(unittest.TestCase):
    def setUp(self):
        root = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
        os.makedirs(root, exist_ok=True)
        self.box = tempfile.mkdtemp(prefix="run-pass-coderoot-", dir=root)
        self.home = os.path.join(self.box, "home")
        self.bin = os.path.join(self.home, ".claude", "bin")
        self.tree = os.path.join(self.box, "tree")
        self.code = os.path.join(self.box, "code")
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))
        for name in SITES:
            w(os.path.join(self.tree, "scripts", name), TRIPWIRE, 0o755)
            body = WITNESS % "CODE"
            if name == "worktree_sentry.py":
                body += 'print("fp-1") if sys.argv[1:2] == ["snapshot"] else None\n'
            if name == "close_unit.py":
                body += 'print("CLOSED  0"); sys.exit(1)\n'
            w(os.path.join(self.code, "scripts", name), body, 0o755)
        bare = os.path.join(self.box, "upstream.git")
        git(self.box, "init", "-q", "--bare", "-b", "main", bare)
        git(self.box, "init", "-q", "-b", "main", self.tree)
        for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
            git(self.tree, "config", k, v)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "tree")
        git(self.tree, "remote", "add", "origin", bare)
        git(self.tree, "push", "-q", "-u", "origin", "main")
        stubs = {
            "loop_heartbeat.py": "", "salvage.py": 'print("SALVAGE nothing")\n',
            "loop_done.py": "import sys\nsys.exit(1)\n",
            "pass_digest.py": 'print("DIGEST")\nprint("   /builds/U1-build.json")\n',
            # the lander stub: a landing prints LANDED; --push (the reconcile and the closure, review 14 finding 3) commits a
            # dirty plan and pushes, both with hooks off, as the real lander does from a frozen copy
            "land_batch.py": "REAL_LANDER = %r\n" % os.path.join(HERE, "loop", "land_batch.py") + LAND_BATCH_STUB,
            "runner_pool.py": 'print("started 1")\n',
        }
        for name, body in stubs.items():
            w(os.path.join(self.bin, name), body, 0o755)
        shutil.copy2(os.path.join(HERE, "loop", "loop_procs.py"), self.bin)   # the one ownership rule, deployed beside the pass
        shutil.copy2(os.path.join(HERE, "loop", "loop_switches.py"), self.bin)   # plan E switches: sizing runs only under BROTHER_TUNING=on

    def tearDown(self):
        shutil.rmtree(self.box, ignore_errors=True)

    def run_pass(self, **env):
        base = {k: v for k, v in os.environ.items()
                if not k.startswith("BROTHER_") and k not in ("LOCAL_SLOTS", "WORKERS_PER_ROUND")}
        base.update(HOME=self.home, BROTHER_LAUNCH_WORKTREE=self.tree, BROTHER_SCOPE=".")
        base.update(env)
        r = subprocess.run(["bash", PASS], cwd=self.tree, env=base, capture_output=True, text=True, timeout=120)
        time.sleep(1.0)     # the reprobe and the diagnosis are detached: give each its moment to leave a line
        return r

    def read(self, name):
        p = os.path.join(self.home, name)
        return open(p, encoding="utf-8").read() if os.path.isfile(p) else ""

    def test_every_site_runs_the_code_root(self):
        # BROTHER_TUNING=on reaches the sizing site (frozen by plan E otherwise); the reprobe is retired, so no probe_round line
        r = self.run_pass(BROTHER_CODE_ROOT=self.code, BROTHER_TUNING="on")
        witness = self.read("witness")
        self.assertEqual(self.read("tripwire"), "", r.stdout + r.stderr)
        for line in ("CODE adaptive_sizing.py --env", "CODE diag_round.py --limit 6",
                     "CODE worktree_sentry.py snapshot", "CODE worktree_sentry.py verify fp-1"):
            self.assertIn(line, witness, r.stdout + r.stderr)
        # THE CLOSER IS THE LANDER'S (review 15 finding 2, 2026-10-03): no close_unit.py of any tree runs from the pass;
        # land_batch.py --close runs it from a frozen copy of hub's head with every done check confined
        self.assertNotIn("close_unit.py", witness, r.stdout + r.stderr)
        self.assertIn("--close", self.read("landed"), r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("U1-build.json", self.read("landed"))

    def test_a_failed_fingerprint_read_refuses_to_land(self):
        w(os.path.join(self.code, "scripts", "worktree_sentry.py"), "import sys\nsys.exit(2)\n", 0o755)
        r = self.run_pass(BROTHER_CODE_ROOT=self.code)
        self.assertEqual(r.returncode, 44, r.stdout + r.stderr)
        self.assertIn("LOOP BLOCKED:", r.stdout)
        self.assertIn("fingerprint", r.stdout)
        self.assertEqual(self.read("landed"), "")

    def test_a_fingerprint_read_that_exits_nonzero_refuses_whatever_it_printed(self):
        # the snapshot prints and exits 2; verify would agree (exit 0), so only the exit code guard can refuse this
        w(os.path.join(self.code, "scripts", "worktree_sentry.py"),
          'import sys\nprint("partial")\nsys.exit(2 if sys.argv[1:2] == ["snapshot"] else 0)\n', 0o755)
        r = self.run_pass(BROTHER_CODE_ROOT=self.code)
        self.assertEqual(r.returncode, 44, r.stdout + r.stderr)
        self.assertEqual(self.read("landed"), "")

    def test_an_empty_fingerprint_refuses_even_at_exit_0(self):
        w(os.path.join(self.code, "scripts", "worktree_sentry.py"), 'import sys\nsys.exit(0)\n', 0o755)
        r = self.run_pass(BROTHER_CODE_ROOT=self.code)
        self.assertEqual(r.returncode, 44, r.stdout + r.stderr)
        self.assertEqual(self.read("landed"), "")

    def test_a_proof_phase_without_a_code_root_runs_nothing(self):
        r = self.run_pass(BROTHER_PROOF_PHASE="RB")
        self.assertEqual(r.returncode, 44, r.stdout + r.stderr)
        self.assertIn("LOOP BLOCKED:", r.stdout)
        self.assertIn("BROTHER_CODE_ROOT", r.stdout)
        self.assertEqual(self.read("tripwire") + self.read("witness") + self.read("landed"), "")

    def install_refusing_hooks(self):
        for hook in ("pre-commit", "pre-push"):
            w(os.path.join(self.tree, ".git", "hooks", hook), REFUSING_HOOK, 0o755)

    # REVIEW 14 FINDING 3 (2026-10-02): a local branch ahead of hub with a clean tree was pushed with plain git, whose
    # installed pre-push hook execs the TREE's scripts/pre_push_hook.sh: just landed code, run unsandboxed by the driver.
    def test_the_reconcile_push_goes_through_the_lander_never_the_trees_hook(self):
        self.install_refusing_hooks()
        w(os.path.join(self.tree, "docs", "note.txt"), "ahead\n")
        git(self.tree, "add", "-A")
        git(self.tree, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "ahead of hub")
        r = self.run_pass(BROTHER_CODE_ROOT=self.code)
        self.assertEqual(self.read("tripwire"), "", "the tree's hook ran: " + r.stdout + r.stderr)
        self.assertIn("RECONCILE parity restored", r.stdout, r.stdout + r.stderr)
        self.assertIn("--push", self.read("landed"), "the reconcile did not go through the lander")
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), git(self.tree, "rev-parse", "origin/main"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_closure_goes_through_the_lander_never_the_trees_closer_or_hooks(self):
        # ONE CONDITION: the tree and the code root both carry a close_unit.py that would dirty the plan and leave a
        # witness; the pass used to run one of them itself (review 15 finding 2: the TREE's, outside a proof, with every
        # done check bare) and then `git commit` and `git push` through the installed hooks (review 14 finding 3). The
        # closure is the lander's (--close) from a frozen copy of hub's head: no tree closer runs, no hook runs, parity holds.
        self.install_refusing_hooks()
        plan = os.path.join("docs", "plan", "BROTHER-1.1.0-LAUNCH-WBS.json")
        w(os.path.join(self.tree, plan), '{"units": []}\n')
        for root, tag in ((self.code, "CODE"), (self.tree, "TREE")):
            w(os.path.join(root, "scripts", "close_unit.py"),
              WITNESS % tag + 'open(%r, "a").write("\\n")\nprint("DONE U1: done check passed")\n' % plan, 0o755)
        git(self.tree, "add", "-A")
        git(self.tree, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "plan and a tree closer")
        git(self.tree, "-c", "core.hooksPath=/dev/null", "push", "-q")
        r = self.run_pass(BROTHER_CODE_ROOT=self.code)
        self.assertEqual(self.read("tripwire"), "", "a tree hook ran: " + r.stdout + r.stderr)
        self.assertNotIn("close_unit.py", self.read("witness"), "a closer ran from the pass itself: " + r.stdout + r.stderr)
        self.assertIn("--close", self.read("landed"), "the closure did not go through the lander: " + r.stdout)
        self.assertEqual(git(self.tree, "status", "--porcelain"), "", "the pass left the tree dirty: " + r.stdout)
        self.assertEqual(git(self.tree, "rev-parse", "HEAD"), git(self.tree, "rev-parse", "origin/main"), r.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_outside_a_proof_no_tree_code_runs_unboxed(self):
        # REVIEW 17 FINDING 4 (2026-10-03, executed): with no BROTHER_CODE_ROOT outside a proof the landing tree WAS the code
        # root, so its diag_round.py and worktree_sentry.py ran unsandboxed every pass. Every site's script in the tree
        # now tries to write OUTSIDE any sandbox write area; the sentry also answers as a sentry. The pass skips sizing and
        # diagnosis by name, runs the tree's sentry boxed through the lander, and lands: nothing escaped.
        sys.path.insert(0, os.path.join(HERE, "loop"))
        import grade_build
        if grade_build.sandbox_ready():
            self.skipTest("NO-DATA: %s" % grade_build.sandbox_ready())
        escape = os.path.join(self.box, "escaped")
        payload = ('#!/usr/bin/env python3\nimport os, sys\ntry:\n    open(%r, "a").write(os.path.basename(__file__) + "\\n")\n'
                   'except OSError:\n    pass\n' % escape)
        for name in SITES:
            body = payload + ('print("fp-1") if sys.argv[1:2] == ["snapshot"] else None\n' if name == "worktree_sentry.py" else "")
            w(os.path.join(self.tree, "scripts", name), body, 0o755)
        git(self.tree, "commit", "-q", "-am", "every site a would-be escape")
        git(self.tree, "push", "-q")
        r = self.run_pass(BROTHER_TUNING="on")
        out = r.stdout + r.stderr
        self.assertEqual(open(escape).read() if os.path.exists(escape) else "", "",
                         "landing tree code ran outside the sandbox: " + out)
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("SIZING  adaptive sizing skipped: no frozen code root", r.stdout, out)
        self.assertIn("DIAGNOSE skipped: no frozen code root", r.stdout, out)
        self.assertIn("U1-build.json", self.read("landed"), "the boxed sentry fingerprinted the tree and the pass landed: " + out)
        self.assertEqual(self.read("tripwire") + self.read("witness"), "", out)


if __name__ == "__main__":
    unittest.main()
