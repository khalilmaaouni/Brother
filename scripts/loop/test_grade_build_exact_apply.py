#!/usr/bin/env python3
"""A build is applied byte for byte and only at its one true place, by the grader and by the lander alike (2026-10-02).

Two defects found replaying native builds through the grader: str.count counts non overlapping matches, so a periodic
find ("ab\\nab\\n" inside "ab\\nab\\nab\\n") read as unique and was applied at the first match; and the grader read and
wrote in text mode, which rewrites carriage returns, while the old lander read bytes. Both sides now route through
grade_build.occurrences and replace_once and open with newline="".

THE ENTRY POINTS: grade_build.apply as the grader calls it, and land_apply run as a command in a scratch tree, the way
land_batch runs it. ONE CONDITION PER CASE.
Run: python3 -B scripts/loop/test_grade_build_exact_apply.py"""
import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import grade_build as G  # noqa: E402
import test_land_apply as TLA  # noqa: E402

PERIODIC = "ab\nab\nab\n"
CRLF = b"x = 1\r\ny = 2\r\n"


class TheGraderApplies(unittest.TestCase):
    def tree(self, files):
        d = tempfile.mkdtemp(prefix="exact-apply-")
        self.addCleanup(shutil.rmtree, d, True)
        for rel, data in files.items():
            with open(os.path.join(d, rel), "wb") as fh:
                fh.write(data)
        return d

    def apply(self, d, items):
        problems = []
        n = G.apply(d, items, problems, "build")
        return n, problems

    def test_a_periodic_find_is_refused(self):
        d = self.tree({"a.txt": PERIODIC.encode()})
        n, problems = self.apply(d, [{"path": "a.txt", "find": "ab\nab\n", "replace": "zz\n"}])
        self.assertEqual(n, 0)
        self.assertTrue(any("find matches 2 times" in p for p in problems), problems)
        with open(os.path.join(d, "a.txt"), "rb") as fh:
            self.assertEqual(fh.read(), PERIODIC.encode())

    def test_carriage_returns_are_kept_byte_for_byte(self):
        d = self.tree({"a.txt": CRLF})
        n, problems = self.apply(d, [{"path": "a.txt", "find": "y = 2\r\n", "replace": "y = 3\r\n"}])
        self.assertEqual((n, problems), (1, []))
        with open(os.path.join(d, "a.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"x = 1\r\ny = 3\r\n")

    def test_a_new_file_keeps_its_line_endings(self):
        d = self.tree({})
        n, problems = self.apply(d, [{"path": "n.txt", "new_file_content": "a\r\nb\r\n"}])
        self.assertEqual((n, problems), (1, []))
        with open(os.path.join(d, "n.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"a\r\nb\r\n")

    def test_occurrences_counts_overlaps_and_refuses_hostile_input(self):
        self.assertEqual(G.occurrences(PERIODIC, "ab\nab\n"), [0, 3])
        self.assertEqual(G.occurrences("abc", "b"), [1])
        for bad in ((None, "a"), ("a", None), ("a", ""), (b"a", "a")):
            self.assertEqual(G.occurrences(*bad), [])
        self.assertIsNone(G.replace_once(PERIODIC, "ab\nab\n", "x"))
        self.assertEqual(G.replace_once("abc", "b", "X"), "aXc")


class TheScreenPlacesFragments(unittest.TestCase):
    """grade_build.unsafe judges a fragment where it lands, after replaying the build's earlier items for that path."""

    def test_a_periodic_earlier_item_is_refused_not_replayed_at_its_first_match(self):
        body = "if 1:\n    a = 0\n    a = 0\n    a = 0\n"
        build = {"edits": [{"path": "m.py", "find": "    a = 0\n    a = 0\n", "replace": "    a = 1\n"}],
                 "mutations": [{"path": "m.py", "find": "if 1:", "replace": "if False:"}]}
        why = G.unsafe(build, runners=[], read=lambda path: body)
        self.assertIsNotNone(why)
        self.assertIn("earlier item for this path cannot be placed", why)


class TheLanderApplies(unittest.TestCase):
    """land_apply as a command, in a scratch tree holding one existing data file (no Python module: no fuzz child)."""

    def land(self, data, edit):
        t = TLA.Tree({})
        self.addCleanup(t.close)
        with open(os.path.join(t.dir, "a.txt"), "wb") as fh:
            fh.write(data)
        build = os.path.join(t.dir, "exact.json")
        with open(build, "w", encoding="utf-8") as fh:
            json.dump({"edits": [dict(edit, path="a.txt")], "tests": []}, fh)
        # run directly so the refusal's own words (stderr, where sys.exit writes them) are read, never only its exit
        p = subprocess.run([sys.executable, "-B", TLA.LANDER, build], cwd=t.dir, env=TLA.clean_env(),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        with open(os.path.join(t.dir, "a.txt"), "rb") as fh:
            return p.returncode, p.stdout + p.stderr, fh.read()

    def test_a_periodic_find_is_refused_and_the_file_untouched(self):
        rc, out, after = self.land(PERIODIC.encode(), {"find": "ab\nab\n", "replace": "zz\n"})
        self.assertNotEqual(rc, 0, out)
        self.assertIn("find not unique", out)
        self.assertEqual(after, PERIODIC.encode())

    def test_carriage_returns_land_byte_for_byte(self):
        rc, out, after = self.land(CRLF, {"find": "y = 2\r\n", "replace": "y = 3\r\n"})
        self.assertEqual(after, b"x = 1\r\ny = 3\r\n", out)

    def test_a_new_file_without_a_final_newline_lands_as_graded(self):
        # sixth review 2026-10-02: the grader wrote b"abc", the lander appended a newline
        t = TLA.Tree({})
        self.addCleanup(t.close)
        build = os.path.join(t.dir, "exact.json")
        with open(build, "w", encoding="utf-8") as fh:
            json.dump({"edits": [{"path": "n.txt", "new_file_content": "abc"}], "tests": []}, fh)
        p = subprocess.run([sys.executable, "-B", TLA.LANDER, build], cwd=t.dir, env=TLA.clean_env(),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        with open(os.path.join(t.dir, "n.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"abc", p.stdout + p.stderr)


class TheGitDirStaysShut(unittest.TestCase):
    """Seventh review 2026-10-02: a case variant of .git reached the real hooks folder on a case insensitive volume."""

    def test_dest_refuses_every_case_of_a_git_part(self):
        d = tempfile.mkdtemp(prefix="git-case-")
        self.addCleanup(shutil.rmtree, d, True)
        for path in (".GIT/hooks/pre-commit", "a/.Git/config", ".gIt", "x/.git/y", ".git"):
            full, why = G.dest(d, path)
            self.assertIsNone(full, path)
            self.assertIn("escapes the tree", why)
        self.assertEqual(G.dest(d, "docs/gitnotes.md")[1], "")
        self.assertEqual(G.dest(d, "a/.github/workflows/x.yml")[1], "")

    def test_dest_refuses_what_the_landing_trusts(self):
        # eighth review 2026-10-02: a build could land a workflow and the landing's own guard files
        d = tempfile.mkdtemp(prefix="trusted-")
        self.addCleanup(shutil.rmtree, d, True)
        for path in (".github/workflows/burn.yml", ".GitHub/x.yml", ".claude/settings.json", "pkg/.gitattributes",
                     ".gitmodules", "scripts/pre_push_hook.sh", "Scripts/Check_All.sh", "scripts/hermetic_test_check.py",
                     "scripts/loop/sandbox.sb", "scripts/system_doc.py", "scripts/bundle_runtime.py",
                     "scripts/test_battery_registration.py", "scripts/pre_push_gate.py", "scripts/self_check_staged.py",
                     "scripts/close_unit.py", "scripts/adaptive_sizing.py", "scripts/worktree_sentry.py",
                     "scripts/diag_round.py", "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", "docs/plan/specs/D2.md",
                     "Docs/Plan/Specs/x.md", ".gitignore", "scripts/.GitIgnore",
                     # review 14 finding 1 (2026-10-02): every module the frozen checks import, from an AST walk of
                     # self_check_staged, pre_push_gate, hermetic_test_check and cut_preflight; export_public since T1 closed
                     "scripts/private_terms_scan.py", "scripts/loop/secret_scan.py", "scripts/edition_guard.py",
                     "scripts/codex_skills.py", "docs/plan/EXPORT-ALLOWLIST.txt", "scripts/cut_preflight.py",
                     "scripts/plugin_bump_gate.py", "scripts/tmp_sandbox.py", "scripts/git_location_guard.py",
                     "scripts/loop/grade_build.py", "scripts/loop/loop_switches.py", "scripts/export_public.py"):
            full, why = G.dest(d, path)
            self.assertIsNone(full, path)
            self.assertIn("the landing trusts", why)
        for path in ("scripts/loop/unit_runner.py", "docs/github-notes.md", "scripts/check_all_helper.py"):
            self.assertEqual(G.dest(d, path)[1], "", path)

    def test_the_lander_refuses_a_case_variant_and_writes_nothing(self):
        t = TLA.Tree({})
        self.addCleanup(t.close)
        build = os.path.join(t.dir, "hook.json")
        with open(build, "w", encoding="utf-8") as fh:
            json.dump({"edits": [{"path": ".GIT/hooks/pre-commit", "new_file_content": "#!/bin/sh\nexit 0\n"}], "tests": []}, fh)
        p = subprocess.run([sys.executable, "-B", TLA.LANDER, build], cwd=t.dir, env=TLA.clean_env(),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("escapes the tree", p.stdout + p.stderr)
        self.assertFalse(os.path.exists(os.path.join(t.dir, ".git", "hooks", "pre-commit")))


@unittest.skipUnless(shutil.which("sandbox-exec"), "the sandbox exists on macOS only")
class TheLandingSandboxesNeighbours(unittest.TestCase):
    """Eighth review 2026-10-02: the landing imported a graded build's module in its neighbour tests with no sandbox."""

    def boxed(self, code):
        import io
        import land_batch as LB
        scratch = os.path.expanduser("~/.claude/brother-scratch")
        os.makedirs(scratch, exist_ok=True)
        root = os.path.realpath(tempfile.mkdtemp(prefix="boxed-", dir=scratch))
        self.addCleanup(shutil.rmtree, root, True)
        tree, outside = os.path.join(root, "tree"), os.path.join(root, "outside")
        os.makedirs(tree); os.makedirs(outside)
        old = os.getcwd(); os.chdir(tree)
        try:
            r = LB.boxed([sys.executable, "-c", code.replace("OUTSIDE", outside)], io.StringIO(), 120, dict(os.environ))
        finally:
            os.chdir(old)
        return r.returncode, tree, outside

    def test_a_write_outside_the_tree_is_refused(self):
        rc, tree, outside = self.boxed("open('OUTSIDE/leak', 'w').write('x')")
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(outside, "leak")))

    def test_a_write_inside_the_tree_works(self):
        rc, tree, outside = self.boxed("open('inside', 'w').write('x')")
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(tree, "inside")))

    def test_the_real_home_is_never_the_suites_home(self):
        rc, tree, outside = self.boxed("import os, sys; sys.exit(0 if os.environ['HOME'] != %r else 3)" % os.path.expanduser("~"))
        self.assertEqual(rc, 0)

    def test_register_never_pastes_a_path_that_is_not_a_plain_name(self):
        # ninth review 2026-10-02: check_all.sh is a shell script; the second line behind main()'s refusal
        import land_batch as LB
        text = "a\n" + LB.ANCHOR + "\n"
        self.assertEqual(LB.register(text, ["scripts/test_$(touch X).py", "scripts/test_a;b.py", "scripts/test_a b.py"]), text)
        self.assertIn("scripts/test_plain_one.py", LB.register(text, ["scripts/test_plain_one.py"]))

    def test_the_trees_own_git_is_not_writable(self):
        # tenth review 2026-10-02: a test rewrote the worktree's .git link inside ROOT
        rc, tree, outside = self.boxed("import os; os.makedirs('.git/hooks'); open('.git/hooks/post-commit', 'w').write('x')")
        self.assertNotEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(tree, ".git")))

    def test_a_non_ascii_name_comes_back_exact(self):
        import land_batch as LB
        scratch = os.path.expanduser("~/.claude/brother-scratch")
        root = os.path.realpath(tempfile.mkdtemp(prefix="names-", dir=scratch))
        self.addCleanup(shutil.rmtree, root, True)
        subprocess.run(["git", "init", "-q", root], check=True)
        name = "scripts/test_t\u00e9st.py"
        os.makedirs(os.path.join(root, "scripts"))
        open(os.path.join(root, name), "w").close()
        old = os.getcwd(); os.chdir(root)
        try:
            self.assertEqual(LB.changed_paths(), {name})
        finally:
            os.chdir(old)

    def test_a_suite_never_inherits_a_credential_shaped_name(self):
        import loop_switches as LS
        env = LS.suite_env({"PATH": "/bin", "SOME_API_KEY": "v", "X_TOKEN": "v", "PLAIN": "v"}, "/tmp/h")
        self.assertEqual({k for k in env if k in ("SOME_API_KEY", "X_TOKEN")}, set())
        self.assertEqual(env["PLAIN"], "v")
        self.assertIs(G.SECRET_ENV, LS.SECRET_ENV, "one rule, shared")

    def test_a_suite_cannot_signal_a_process_outside_its_sandbox(self):
        # twelfth review 2026-10-02: a sandboxed kill -TERM stopped a process outside (the lander, the driver)
        victim = subprocess.Popen(["/bin/sleep", "60"])
        self.addCleanup(victim.kill)
        rc, tree, outside = self.boxed("import os, signal; os.kill(%d, signal.SIGTERM)" % victim.pid)
        self.assertNotEqual(rc, 0)
        self.assertIsNone(victim.poll(), "the outside process must survive")

    def test_a_suite_can_still_stop_its_own_child(self):
        rc, tree, outside = self.boxed("import subprocess; p = subprocess.Popen(['/bin/sleep', '60']); p.kill(); p.wait()")
        self.assertEqual(rc, 0)

    def test_a_detached_grandchild_holding_output_never_wedges_the_lander(self):
        # twelfth review 2026-10-02: communicate() after the kill waited for a grandchild outside the group
        import time, land_batch as LB
        code = ("import subprocess, sys, time; subprocess.Popen(['/bin/sleep', '30'], start_new_session=True,"
                " stdout=sys.stdout, stderr=sys.stderr); time.sleep(30)")
        t0 = time.time()
        r = LB.run_group([sys.executable, "-c", code], 1, dict(os.environ))
        self.assertEqual(r.returncode, 124)
        self.assertLess(time.time() - t0, 15)

    def test_non_utf8_output_never_crashes_the_lander(self):
        import land_batch as LB
        r = LB.run_group([sys.executable, "-c", "import os; os.write(1, b'ok \\xff\\xfe'); os.write(2, b'\\xfe')"], 30, dict(os.environ))
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("ok "))

    def test_both_neighbour_call_sites_run_boxed(self):
        with open(os.path.join(HERE, "land_batch.py"), encoding="utf-8") as fh:
            body = fh.read().split("def main(", 1)[1]
        # the neighbours run in a disposable copy of the batch's applied state (eleventh review 2026-10-02)
        self.assertIn("copy = make_copy()", body)
        self.assertIn("r = boxed(nb_argv, log, 1200, env, cwd=copy)", body)
        self.assertIn("base_run_red(base_root, tp, dotted, py, lambda argv, genv=None: boxed(argv, log, 1200, env))", body)
        self.assertNotIn("r = sh(nb_argv", body)
        self.assertNotIn("r = boxed(nb_argv, log, 1200, env)", body)


if __name__ == "__main__":
    unittest.main()
