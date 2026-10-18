"""test_self_check_bundle_runtime: C3, the pre-commit hook regenerates and stages the bundle mirror, tested through a real
git commit.

The shape of scripts/test_self_check_system_doc.py: every case builds a throwaway git repository holding copies of
scripts/self_check_staged.py and scripts/private_terms_scan.py plus a stand in scripts/bundle_runtime.py, installs the
pre-commit hook with self_check_staged.py --install-hook, and runs `git commit`. `python3` on the hook's PATH is this
interpreter, HOME is a temp directory holding a fake terms file, so nothing reads this machine's home and nothing is
written into the repository tree. The fixture has no scripts/system_doc.py, so refresh_system_doc has nothing to keep
and only the bundle guard can answer.

The stand in generator keeps the two mirrors the real one keeps, at fixture scale: scripts/eng_*.py copied byte for byte
into bundle/runtime/ (the closure), and bundle/skills/<name>/SKILL.md copied into bundle/codex-skills/<name>/SKILL.md
with the `disable-model-invocation` line stripped (the Codex mirror). Every run appends one line to a counter file
outside the repository (C3_COUNTER), so a case can tell whether the check ran at all.

Each guard has its own fixture that only it can refuse:
  stale mirror     a staged closure file with a stale mirror: regenerated, staged, the commit passes
  codex mirror     a staged skill file: the Codex mirror is regenerated and staged (the Codex host's half)
  docs stage       a docs only stage never runs the check, even over a stale mirror
  fresh            a staged closure change that keeps the mirror current runs the check once and regenerates nothing
  no generator     a tree without scripts/bundle_runtime.py has nothing to keep
  dirty            an untracked file under scripts/ means nothing is regenerated, NO-DATA says so, the commit goes on
  temporary index  `git commit -- <paths>` stages through a temporary index: nothing is regenerated, NO-DATA says so
  failing          the generator exits non zero on write: refused with the command
  corrupt          the generator prints nothing and exits 1: refused
  still stale      the generator writes but its check still fails: refused with the command
  partial          the generator writes outside bundle/, so the tree still differs from the index after staging: refused
  add fails        a second git process holds the index lock when the hook stages the mirror: refused with the command
  hang             a generator that hangs past the deadline refuses (in process, the deadline shortened)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import self_check_staged as SCS  # noqa: E402

FAKE_TERM = "zzzTestOnlyForbiddenTermQuoll"
FIX = "python3 scripts/bundle_runtime.py && git add -A bundle"

#: A working stand in for scripts/bundle_runtime.py: the closure mirror and the Codex mirror, --check compares.
GOOD_GEN = '''"""bundle_runtime: a stand in generator for the C3 fixture."""
import os, sys
open(os.environ["C3_COUNTER"], "a").write(" ".join(sys.argv[1:]) + "\\n")
def want():
    out = {}
    for n in sorted(os.listdir("scripts")):
        if n.startswith("eng_") and n.endswith(".py"):
            out[os.path.join("bundle", "runtime", n)] = open(os.path.join("scripts", n)).read()
    src = os.path.join("bundle", "skills")
    for n in sorted(os.listdir(src)):
        lines = open(os.path.join(src, n, "SKILL.md")).read().splitlines(True)
        out[os.path.join("bundle", "codex-skills", n, "SKILL.md")] = "".join(
            l for l in lines if not l.startswith("disable-model-invocation:"))
    return out
def have():
    out = {}
    for d in (os.path.join("bundle", "runtime"), os.path.join("bundle", "codex-skills")):
        for base, _, files in os.walk(d):
            for f in files:
                p = os.path.join(base, f)
                out[p] = open(p).read()
    return out
if "--check" in sys.argv:
    sys.exit(0 if want() == have() else 1)
for p in set(have()) - set(want()):
    os.remove(p)
for p, text in want().items():
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)
'''

#: A stand in for the failure fixtures, the shape of test_self_check_system_doc.FAKE_GEN: the first --check fails and
#: every later one obey CHECK_LATER; a write prints SAY, exits WRITE_EXIT, and with TAKE_LOCK first takes .git/index.lock
#: the way a concurrent second git process would; with OUTSIDE it also writes a file under scripts/, outside bundle/.
FAKE_GEN = '''"""bundle_runtime: a stand in generator for one failure fixture."""
import os, sys
WRITE_EXIT, CHECK_LATER, TAKE_LOCK, OUTSIDE, SAY, HANG = %d, %d, %d, %d, %r, %d
counter = os.environ["C3_COUNTER"]
n = len(open(counter).read().splitlines()) if os.path.exists(counter) else 0
open(counter, "a").write(" ".join(sys.argv[1:]) + "\\n")
if HANG:
    import time; time.sleep(HANG)
if "--check" in sys.argv:
    sys.exit(1 if n == 0 else CHECK_LATER)
if TAKE_LOCK:
    open(os.path.join(".git", "index.lock"), "w").close()
if OUTSIDE:
    open(os.path.join("scripts", "eng_stray.txt"), "w").write("written outside bundle/\\n")
if SAY:
    print(SAY)
os.makedirs(os.path.join("bundle", "runtime"), exist_ok=True)
open(os.path.join("bundle", "runtime", "eng_alpha.py"), "w").write("regenerated\\n")
sys.exit(WRITE_EXIT)
'''

SKILL = "---\nname: door\ndescription: the one door\ndisable-model-invocation: true\n---\nRoute the ask.\n"


class CommitHook(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="c3-")
        self.repo = os.path.join(self.tmp, "repo")
        self.counter = os.path.join(self.tmp, "counter")
        home = os.path.join(self.tmp, "home")
        shim = os.path.join(self.tmp, "bin")
        for d in (os.path.join(self.repo, "scripts"), os.path.join(self.repo, "bundle", "skills", "door"),
                  os.path.join(self.repo, "docs"), home, shim):
            os.makedirs(d)
        os.symlink(sys.executable, os.path.join(shim, "python3"))
        with open(os.path.join(home, ".brothersbe-private-names"), "w") as fh:
            fh.write(FAKE_TERM + "\n")
        self.env = dict(os.environ, HOME=home, PATH=shim + os.pathsep + os.environ.get("PATH", ""),
                        GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1", C3_COUNTER=self.counter)
        for k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
            self.env.pop(k, None)
        for name in ("self_check_staged.py", "private_terms_scan.py"):
            shutil.copy(os.path.join(HERE, name), os.path.join(self.repo, "scripts", name))
        self.write("scripts/bundle_runtime.py", GOOD_GEN)
        self.write("scripts/eng_alpha.py", '"""eng_alpha: the one closure file of this fixture."""\n')
        self.write("bundle/skills/door/SKILL.md", SKILL)
        self.write("docs/note.txt", "a page nothing mirrors\n")
        self.write(".gitignore", "__pycache__/\n")
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "a@b.c"], ["config", "user.name", "t"]):
            self.git(*args)
        self.py("scripts/bundle_runtime.py")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        out = self.py("scripts/self_check_staged.py", "--install-hook")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.py("scripts/bundle_runtime.py", "--check").returncode, 0)
        os.remove(self.counter)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text):
        with open(os.path.join(self.repo, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(self, rel):
        with open(os.path.join(self.repo, rel), encoding="utf-8") as fh:
            return fh.read()

    def run_in(self, cmd):
        return subprocess.run(cmd, cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=120)

    def git(self, *args):
        return self.run_in(["git"] + list(args))

    def py(self, *args):
        return self.run_in([sys.executable, "-I"] + list(args))

    def commit(self, *paths):
        self.git("add", "--", *paths)
        return self.git("commit", "-q", "-m", "change")

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def at_head(self, rel):
        return self.git("show", "HEAD:" + rel).stdout

    def runs(self):
        return open(self.counter).read().splitlines() if os.path.exists(self.counter) else []

    def no_verify(self, msg):
        self.git("add", "-A")
        done = self.git("commit", "-q", "--no-verify", "-m", msg)
        self.assertEqual(done.returncode, 0, done.stderr)
        if os.path.exists(self.counter):
            os.remove(self.counter)

    def fake_generator(self, write_exit=0, check_later=0, take_lock=0, outside=0, say="", hang=0):
        """Commit a stand in generator past the hook, so the next commit is the one under test."""
        self.write("scripts/bundle_runtime.py", FAKE_GEN % (write_exit, check_later, take_lock, outside, say, hang))
        self.no_verify("fake generator")

    def test_a_stale_mirror_is_regenerated(self):
        self.write("scripts/eng_alpha.py", '"""eng_alpha: a closure file rewritten."""\n')
        done = self.commit("scripts/eng_alpha.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("bundle mirror regenerated and staged", done.stdout + done.stderr)
        self.assertEqual(self.at_head("bundle/runtime/eng_alpha.py"), '"""eng_alpha: a closure file rewritten."""\n')
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        self.assertEqual(self.py("scripts/bundle_runtime.py", "--check").returncode, 0)

    def test_a_new_closure_file_is_mirrored_and_a_removed_one_dropped(self):
        self.write("scripts/eng_beta.py", '"""eng_beta: new."""\n')
        os.remove(os.path.join(self.repo, "scripts", "eng_alpha.py"))
        done = self.commit("scripts/eng_beta.py", "scripts/eng_alpha.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.at_head("bundle/runtime/eng_beta.py"), '"""eng_beta: new."""\n')
        self.assertNotIn("bundle/runtime/eng_alpha.py", self.git("ls-files").stdout)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_a_skill_regenerates_the_codex_mirror(self):
        self.write("bundle/skills/door/SKILL.md", SKILL.replace("Route the ask.", "Route every ask."))
        done = self.commit("bundle/skills/door/SKILL.md")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        mirror = self.at_head("bundle/codex-skills/door/SKILL.md")
        self.assertIn("Route every ask.", mirror)
        self.assertNotIn("disable-model-invocation", mirror)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_a_docs_stage_skips_the_check(self):
        self.write("bundle/runtime/eng_alpha.py", "stale on purpose\n")
        self.no_verify("stale mirror")
        self.write("docs/note.txt", "an edit nothing mirrors\n")
        done = self.commit("docs/note.txt")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.at_head("bundle/runtime/eng_alpha.py"), "stale on purpose\n")
        self.assertNotIn("bundle mirror", done.stdout + done.stderr)

    def test_a_fresh_mirror_costs_one_check(self):
        before = self.at_head("bundle/runtime/eng_alpha.py")
        self.write("scripts/not_mirrored.py", '"""not_mirrored: under scripts/, outside the closure."""\n')
        done = self.commit("scripts/not_mirrored.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.runs(), ["--check"])
        self.assertNotIn("regenerated", done.stdout + done.stderr)
        self.assertEqual(self.at_head("bundle/runtime/eng_alpha.py"), before)

    def test_a_tree_without_the_generator_has_nothing_to_keep(self):
        os.remove(os.path.join(self.repo, "scripts", "bundle_runtime.py"))
        self.no_verify("no generator")
        self.write("scripts/eng_alpha.py", '"""eng_alpha: rewritten with no generator."""\n')
        done = self.commit("scripts/eng_alpha.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertNotIn("bundle mirror", done.stdout + done.stderr)

    def test_dirty_an_untracked_source_leaves_the_mirror_alone_and_says_no_data(self):
        before = self.at_head("bundle/runtime/eng_alpha.py")
        self.write("scripts/eng_alpha.py", '"""eng_alpha: staged."""\n')
        self.write("scripts/eng_gamma.py", '"""eng_gamma: untracked, not in this commit."""\n')
        done = self.commit("scripts/eng_alpha.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("NO-DATA: the bundle mirror was not regenerated", done.stdout + done.stderr)
        self.assertEqual(self.at_head("bundle/runtime/eng_alpha.py"), before)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "bundle", "runtime", "eng_gamma.py")))
        self.assertEqual(self.runs(), [])

    def test_temporary_index_a_commit_naming_its_paths_leaves_the_mirror_alone(self):
        before = self.at_head("bundle/runtime/eng_alpha.py")
        self.write("scripts/eng_alpha.py", '"""eng_alpha: named."""\n')
        done = self.git("commit", "-q", "-m", "only", "--", "scripts/eng_alpha.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("NO-DATA: the bundle mirror was not regenerated: this commit names its paths",
                      done.stdout + done.stderr)
        self.assertEqual(self.at_head("bundle/runtime/eng_alpha.py"), before)

    def test_a_failing_generator_refuses(self):
        self.fake_generator(write_exit=3)
        before = self.head()
        self.write("scripts/eng_alpha.py", '"""eng_alpha: changed."""\n')
        done = self.commit("scripts/eng_alpha.py")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("regenerating the bundle mirror failed (exit 3)", done.stderr)
        self.assertIn(FIX, done.stderr)
        self.assertEqual(self.head(), before)

    def test_corrupt_a_silent_generator_exiting_1_refuses(self):
        self.fake_generator(write_exit=1)
        before = self.head()
        self.write("scripts/eng_alpha.py", '"""eng_alpha: changed."""\n')
        done = self.commit("scripts/eng_alpha.py")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("regenerating the bundle mirror failed (exit 1)", done.stderr)
        self.assertEqual(self.head(), before)

    def test_still_stale_after_regenerating_refuses_with_the_command(self):
        self.fake_generator(check_later=1)
        before = self.head()
        self.write("scripts/eng_alpha.py", '"""eng_alpha: changed."""\n')
        done = self.commit("scripts/eng_alpha.py")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("the bundle mirror is still stale after regenerating", done.stderr)
        self.assertIn(FIX, done.stderr)
        self.assertEqual(self.head(), before)

    def test_partial_a_generator_writing_outside_bundle_refuses(self):
        self.fake_generator(outside=1)
        before = self.head()
        self.write("scripts/eng_alpha.py", '"""eng_alpha: changed."""\n')
        done = self.commit("scripts/eng_alpha.py")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("still differ from the index after staging", done.stderr)
        self.assertIn("scripts/eng_stray.txt", done.stderr)
        self.assertEqual(self.head(), before)

    def test_add_fails_refuses_with_the_command(self):
        self.fake_generator(take_lock=1)
        before = self.head()
        self.write("scripts/eng_alpha.py", '"""eng_alpha: changed."""\n')
        done = self.commit("scripts/eng_alpha.py")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("git add the bundle mirror failed", done.stderr)
        self.assertIn(FIX, done.stderr)
        self.assertEqual(self.head(), before)

    def test_hang_a_generator_past_the_deadline_refuses(self):
        self.fake_generator(hang=30)
        self.write("scripts/eng_alpha.py", '"""eng_alpha: changed."""\n')
        self.git("add", "--", "scripts/eng_alpha.py")
        saved, SCS.GEN_TIMEOUT = SCS.GEN_TIMEOUT, 2
        old = {k: os.environ.get(k) for k in ("C3_COUNTER", "GIT_INDEX_FILE")}
        os.environ["C3_COUNTER"] = self.counter
        os.environ.pop("GIT_INDEX_FILE", None)
        try:
            code, lines = SCS.refresh_bundle_runtime(self.repo)
        finally:
            SCS.GEN_TIMEOUT = saved
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(code, SCS.EXIT_FOUND, lines)
        self.assertIn("BLOCK bundle mirror: could not run", lines[0])


class Trigger(unittest.TestCase):

    def test_the_four_prefixes_trigger(self):
        for p in ("scripts/x.py", "plugin/a.json", "bundle/skills/door/SKILL.md", "products/brothersbe/tools/t.py"):
            self.assertTrue(SCS.bundle_trigger([p]), p)

    def test_a_docs_stage_and_lookalikes_do_not(self):
        self.assertFalse(SCS.bundle_trigger([]))
        self.assertFalse(SCS.bundle_trigger(["docs/x.md", "README.md", "scriptsx/a.py", "my-bundle/a", "x/scripts/a"]))

    def test_one_trigger_among_many_is_enough_and_a_newline_path_is_read_whole(self):
        self.assertTrue(SCS.bundle_trigger(["docs/a.md"] * 50 + ["scripts/loop/a.py"]))
        self.assertFalse(SCS.bundle_trigger(["docs/a\nscripts/b.py"]))


if __name__ == "__main__":
    unittest.main()
