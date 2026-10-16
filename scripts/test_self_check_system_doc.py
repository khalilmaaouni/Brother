"""test_self_check_system_doc: SP7, the pre-commit hook regenerates and stages SYSTEM.md, tested through a real git commit.

Every case builds a throwaway git repository holding copies of scripts/system_doc.py, scripts/self_check_staged.py and
scripts/private_terms_scan.py plus a minimal battery, installs the pre-commit hook with self_check_staged.py
--install-hook (the tool's own installer, the same `python3 <toplevel>/scripts/self_check_staged.py` call the estate's
hook makes), and runs `git commit`. `python3` on the hook's PATH is this interpreter, HOME is a temp directory holding a
fake terms file, so nothing reads this machine's home and nothing is written into the repository tree.

Each guard has its own fixture that only it can refuse:
  regenerate      a staged new or removed script, or `git commit -a` over a rewritten purpose, makes SYSTEM.md stale:
                  the commit regenerates, stages it, and passes
  unrelated       a change touching nothing described never touches SYSTEM.md, even a stale one
  current         a described change that leaves SYSTEM.md current does not regenerate
  dirty           an untracked described file, or an unstaged hand edit of SYSTEM.md, means nothing is regenerated,
                  NO-DATA says so, the commit goes on
  temporary_index `git commit -- <paths>` stages through a temporary index: nothing is regenerated, NO-DATA says so
  regen_fails     the generator exits non zero on write (its check passes on the second call): refused with the command
  still_stale     the generator writes but its check still fails: refused with the command
  add_fails       a second git process holds the index lock when the hook stages SYSTEM.md: refused with the command
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_TERM = "zzzTestOnlyForbiddenTermQuoll"
FIX = "python3 scripts/system_doc.py && git add SYSTEM.md"

#: A stand in generator for the failure fixtures: a counter file outside the repo (path in SP7_COUNTER) makes the first
#: --check fail and every later one obey CHECK_LATER; a write exits WRITE_EXIT, and with TAKE_LOCK it first takes
#: .git/index.lock the way a concurrent second git process would. Only run by path, so -I does not matter.
FAKE_GEN = '''"""system_doc: a stand in generator for one failure fixture."""
import os, sys
WRITE_EXIT = %d
CHECK_LATER = %d
TAKE_LOCK = %d
counter = os.environ["SP7_COUNTER"]
if "--check" in sys.argv:
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    sys.exit(1 if n == 0 else CHECK_LATER)
if TAKE_LOCK:
    open(os.path.join(".git", "index.lock"), "w").close()
sys.exit(WRITE_EXIT)
'''


class CommitHook(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sp7-")
        self.repo = os.path.join(self.tmp, "repo")
        home = os.path.join(self.tmp, "home")
        shim = os.path.join(self.tmp, "bin")
        for d in (os.path.join(self.repo, "scripts"), home, shim):
            os.makedirs(d)
        os.symlink(sys.executable, os.path.join(shim, "python3"))
        with open(os.path.join(home, ".brothersbe-private-names"), "w") as fh:
            fh.write(FAKE_TERM + "\n")
        self.env = dict(os.environ, HOME=home, PATH=shim + os.pathsep + os.environ.get("PATH", ""),
                        GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1",
                        SP7_COUNTER=os.path.join(self.tmp, "counter"))
        for k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
            self.env.pop(k, None)
        for name in ("system_doc.py", "self_check_staged.py", "private_terms_scan.py"):
            shutil.copy(os.path.join(HERE, name), os.path.join(self.repo, "scripts", name))
        self.write("scripts/check_all.sh", 'run_check "alpha" python3 scripts/test_alpha.py\n')
        self.write("scripts/alpha.py", '"""alpha: the one described part of this fixture."""\n')
        self.write(".gitignore", "__pycache__/\n")
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "a@b.c"], ["config", "user.name", "t"]):
            self.git(*args)
        self.py("scripts/system_doc.py")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        out = self.py("scripts/self_check_staged.py", "--install-hook")
        self.assertEqual(out.returncode, 0, out.stderr)

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

    def system_doc_at_head(self):
        return self.git("show", "HEAD:SYSTEM.md").stdout

    def fake_generator(self, write_exit, check_later, take_lock=0):
        """Commit a stand in generator past the hook, so the next commit is the one under test."""
        self.write("scripts/system_doc.py", FAKE_GEN % (write_exit, check_later, take_lock))
        self.git("add", "-A")
        done = self.git("commit", "-q", "--no-verify", "-m", "fake generator")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_regenerate_a_staged_new_script_regenerates_and_stages_system_md(self):
        self.write("scripts/beta.py", '"""beta: a new part the record must follow."""\n')
        done = self.commit("scripts/beta.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("SYSTEM.md regenerated and staged", done.stdout + done.stderr)
        self.assertIn("| `beta` |", self.system_doc_at_head())
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        self.assertEqual(self.py("scripts/system_doc.py", "--check").returncode, 0)

    def test_regenerate_a_removed_script_is_followed_too(self):
        os.remove(os.path.join(self.repo, "scripts", "alpha.py"))
        done = self.commit("scripts/alpha.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertNotIn("| `alpha` |", self.system_doc_at_head())
        self.assertEqual(self.py("scripts/system_doc.py", "--check").returncode, 0)

    def test_unrelated_change_never_touches_system_md_even_a_stale_one(self):
        self.write("SYSTEM.md", "stale on purpose\n")
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", "stale record")
        os.makedirs(os.path.join(self.repo, "docs"))
        self.write("docs/note.txt", "nothing system_doc reads\n")
        done = self.commit("docs/note.txt")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertEqual(self.system_doc_at_head(), "stale on purpose\n")
        self.assertEqual(self.read("SYSTEM.md"), "stale on purpose\n")
        self.assertNotIn("SYSTEM.md", done.stdout + done.stderr)

    def test_current_a_described_change_that_keeps_the_record_current_does_not_regenerate(self):
        self.write("scripts/alpha.py", '"""alpha: the one described part of this fixture."""\nX = 1\n')
        before = self.system_doc_at_head()
        done = self.commit("scripts/alpha.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertNotIn("regenerated", done.stdout + done.stderr)
        self.assertEqual(self.system_doc_at_head(), before)

    def test_dirty_an_untracked_described_file_leaves_system_md_alone_and_says_no_data(self):
        self.write("scripts/beta.py", '"""beta: staged."""\n')
        self.write("scripts/gamma.py", '"""gamma: untracked, not in this commit."""\n')
        before = self.system_doc_at_head()
        done = self.commit("scripts/beta.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("NO-DATA: SYSTEM.md was not regenerated", done.stdout + done.stderr)
        self.assertEqual(self.system_doc_at_head(), before)
        self.assertNotIn("gamma", self.read("SYSTEM.md"))

    def test_dirty_an_unstaged_hand_edit_of_system_md_is_never_overwritten(self):
        self.write("SYSTEM.md", self.read("SYSTEM.md") + "a hand edit nobody staged\n")
        self.write("scripts/beta.py", '"""beta: staged."""\n')
        done = self.commit("scripts/beta.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("NO-DATA: SYSTEM.md was not regenerated", done.stdout + done.stderr)
        self.assertIn("a hand edit nobody staged", self.read("SYSTEM.md"))

    def test_regenerate_commit_all_stages_into_the_index_git_will_keep(self):
        self.write("scripts/alpha.py", '"""alpha: a purpose rewritten in place."""\n')
        done = self.git("commit", "-q", "-a", "-m", "all")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("a purpose rewritten in place", self.system_doc_at_head())
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_temporary_index_a_commit_naming_its_paths_leaves_system_md_alone(self):
        before = self.system_doc_at_head()
        self.write("scripts/alpha.py", '"""alpha: a purpose rewritten in place."""\n')
        done = self.git("commit", "-q", "-m", "only", "--", "scripts/alpha.py")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("NO-DATA: SYSTEM.md was not regenerated: this commit names its paths",
                      done.stdout + done.stderr)
        self.assertEqual(self.system_doc_at_head(), before)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_regen_fails_refuses_the_commit_with_the_command(self):
        self.fake_generator(write_exit=3, check_later=0)
        before = self.head()
        self.write("scripts/beta.py", '"""beta."""\n')
        done = self.commit("scripts/beta.py")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("regenerating SYSTEM.md failed (exit 3)", done.stderr)
        self.assertIn(FIX, done.stderr)
        self.assertEqual(self.head(), before)

    def test_still_stale_after_regenerating_refuses_the_commit_with_the_command(self):
        self.fake_generator(write_exit=0, check_later=1)
        before = self.head()
        self.write("scripts/beta.py", '"""beta."""\n')
        done = self.commit("scripts/beta.py")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("still stale after regenerating", done.stderr)
        self.assertIn(FIX, done.stderr)
        self.assertEqual(self.head(), before)

    def test_add_fails_refuses_the_commit_with_the_command(self):
        # A plain git commit runs the hook with no index lock held (probed 2026-10-16: GIT_INDEX_FILE=.git/index), so a
        # second git process taking the lock between the check and the add is what makes the add fail; the stand in
        # generator plays that process, and its check passes on the second call so only the add guard can refuse.
        self.fake_generator(write_exit=0, check_later=0, take_lock=1)
        before = self.head()
        self.write("scripts/beta.py", '"""beta."""\n')
        done = self.commit("scripts/beta.py")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("git add SYSTEM.md failed", done.stderr)
        self.assertIn(FIX, done.stderr)
        self.assertEqual(self.head(), before)


if __name__ == "__main__":
    unittest.main()
