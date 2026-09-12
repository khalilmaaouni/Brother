import unittest
import subprocess
import sys
import os
import pathlib
import tempfile
import shutil

SCRIPT = pathlib.Path(__file__).resolve().parent / "red_before.py"
TIMEOUT = 30

def run_git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, text=True)

def init_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)

def commit_file(repo, rel, content, msg):
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    subprocess.run(["git", "add", rel], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", msg], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)

def run_red_before(repo, base, files, cmd, env=None):
    use_env = None
    if env is not None:
        use_env = os.environ.copy()
        use_env.update(env)
    args = [sys.executable, str(SCRIPT), "--base", base, "--files"] + files + ["--"] + cmd
    return subprocess.run(args, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TIMEOUT, text=True, env=use_env)

class TestRedBefore(unittest.TestCase):
    def test_proven(self):
        repo = tempfile.mkdtemp(prefix="test_proven_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            commit_file(repo, "a.py", "x=2\n", "fix")
            with open(os.path.join(repo, "check.py"), "w", encoding="utf-8") as f:
                f.write("import a\nimport sys\nsys.exit(0 if a.x==2 else 1)\n")
            subprocess.run(["git", "add", "check.py"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "add check"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            base = "HEAD~2"
            res = run_red_before(repo, base, ["a.py"], [sys.executable, "check.py"])
            self.assertEqual(res.returncode, 0, msg=res.stdout)
            self.assertIn("PROVEN", res.stdout)
            self.assertIn("red_before: RED", res.stdout)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_not_proven_pass_both(self):
        repo = tempfile.mkdtemp(prefix="test_notproven_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            commit_file(repo, "a.py", "x=2\n", "fix")
            with open(os.path.join(repo, "check.py"), "w", encoding="utf-8") as f:
                f.write("import sys\nsys.exit(0)\n")
            subprocess.run(["git", "add", "check.py"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "add check"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            base = "HEAD~2"
            res = run_red_before(repo, base, ["a.py"], [sys.executable, "check.py"])
            self.assertEqual(res.returncode, 1, msg=res.stdout)
            self.assertIn("NOT PROVEN", res.stdout)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_working_tree_preserved(self):
        repo = tempfile.mkdtemp(prefix="test_wt_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "original\n", "base")
            path = os.path.join(repo, "a.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write("dirty\n")
            with open(path, "rb") as f:
                before = f.read()
            res = run_red_before(repo, "HEAD", ["a.py"], [sys.executable, "-c", "import sys; sys.exit(0)"])
            with open(path, "rb") as f:
                after = f.read()
            self.assertEqual(before, after, "working tree was modified")
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_stash_empty(self):
        repo = tempfile.mkdtemp(prefix="test_stash_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            commit_file(repo, "a.py", "x=2\n", "fix")
            before = run_git(["stash", "list"], cwd=repo)
            self.assertEqual(before.stdout.strip(), "")
            res = run_red_before(repo, "HEAD~1", ["a.py"], [sys.executable, "-c", "import sys; sys.exit(0)"])
            after = run_git(["stash", "list"], cwd=repo)
            self.assertEqual(after.stdout.strip(), "", msg=f"stash not empty: {after.stdout} {res.stdout}")
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_no_temp_left(self):
        repo = tempfile.mkdtemp(prefix="test_temp_")
        tmp_root = tempfile.mkdtemp(prefix="tmp_root_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            commit_file(repo, "a.py", "x=2\n", "fix")
            env = {"TMPDIR": tmp_root}
            self.assertEqual(os.listdir(tmp_root), [])
            res = run_red_before(repo, "HEAD~1", ["a.py"], [sys.executable, "-c", "import sys; sys.exit(0)"], env=env)
            remaining = os.listdir(tmp_root)
            self.assertEqual(remaining, [], msg=f"temp dirs left: {remaining} stdout:{res.stdout}")
        finally:
            shutil.rmtree(repo, ignore_errors=True)
            shutil.rmtree(tmp_root, ignore_errors=True)

    def test_unknown_base_exits_2(self):
        repo = tempfile.mkdtemp(prefix="test_unknown_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            res = run_red_before(repo, "does-not-exist-zzz", ["a.py"], [sys.executable, "-c", "import sys; sys.exit(0)"])
            self.assertEqual(res.returncode, 2, msg=res.stdout)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_green_fail_returns_1(self):
        repo = tempfile.mkdtemp(prefix="test_greenfail_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            commit_file(repo, "a.py", "x=2\n", "fix")
            with open(os.path.join(repo, "check.py"), "w", encoding="utf-8") as f:
                f.write("import sys\nsys.exit(1)\n")
            subprocess.run(["git", "add", "check.py"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "add check"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            base = "HEAD~2"
            res = run_red_before(repo, base, ["a.py"], [sys.executable, "check.py"])
            self.assertEqual(res.returncode, 1)
            self.assertIn("NOT PROVEN", res.stdout)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_deleted_at_base(self):
        repo = tempfile.mkdtemp(prefix="test_deleted_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            commit_file(repo, "b.py", "y=1\n", "add b")
            with open(os.path.join(repo, "check.py"), "w", encoding="utf-8") as f:
                f.write("import os\nimport sys\nsys.exit(0 if os.path.exists('b.py') else 1)\n")
            subprocess.run(["git", "add", "check.py"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "add check"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            base = "HEAD~2"
            res = run_red_before(repo, base, ["b.py"], [sys.executable, "check.py"])
            self.assertEqual(res.returncode, 0, msg=res.stdout)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_runs_from_subdirectory(self):
        repo = tempfile.mkdtemp(prefix="test_subdir_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            commit_file(repo, "a.py", "x=2\n", "fix")
            with open(os.path.join(repo, "check.py"), "w", encoding="utf-8") as f:
                f.write("import a\nimport sys\nsys.exit(0 if a.x==2 else 1)\n")
            subprocess.run(["git", "add", "check.py"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "add check"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            base = "HEAD~2"
            sub = os.path.join(repo, "sub")
            os.makedirs(sub, exist_ok=True)
            args = [sys.executable, str(SCRIPT), "--base", base, "--files", "a.py", "--", sys.executable, "check.py"]
            res = subprocess.run(args, cwd=sub, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TIMEOUT, text=True)
            self.assertEqual(res.returncode, 0, msg=res.stdout)
            self.assertIn("PROVEN", res.stdout)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_path_traversal_rejected(self):
        repo = tempfile.mkdtemp(prefix="test_traversal_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            res = run_red_before(repo, "HEAD", ["../a.py"], [sys.executable, "-c", "import sys; sys.exit(0)"])
            self.assertEqual(res.returncode, 2, msg=res.stdout)
            res2 = run_red_before(repo, "HEAD", ["a/../b.py"], [sys.executable, "-c", "import sys; sys.exit(0)"])
            self.assertEqual(res2.returncode, 2, msg=res2.stdout)
            res3 = run_red_before(repo, "HEAD", ["/tmp/a.py"], [sys.executable, "-c", "import sys; sys.exit(0)"])
            self.assertEqual(res3.returncode, 2, msg=res3.stdout)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_cannot_start_exits_2(self):
        repo = tempfile.mkdtemp(prefix="test_nodata_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            res = run_red_before(repo, "HEAD", ["a.py"], ["nonexistent_command_xyz_12345"])
            self.assertEqual(res.returncode, 2, msg=res.stdout)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_timeout_counts_as_fail(self):
        repo = tempfile.mkdtemp(prefix="test_timeout_")
        try:
            init_repo(repo)
            commit_file(repo, "a.py", "x=1\n", "base")
            commit_file(repo, "a.py", "x=2\n", "fix")
            with open(os.path.join(repo, "check.py"), "w", encoding="utf-8") as f:
                f.write("import time\ntime.sleep(5)\n")
            subprocess.run(["git", "add", "check.py"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "add check"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, check=True)
            base = "HEAD~2"
            args = [sys.executable, str(SCRIPT), "--base", base, "--files", "a.py", "--timeout", "1", "--", sys.executable, "check.py"]
            res = subprocess.run(args, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TIMEOUT, text=True)
            self.assertEqual(res.returncode, 1, msg=res.stdout)
            self.assertIn("RED 124", res.stdout)
            self.assertIn("GREEN 124", res.stdout)
            self.assertIn("NOT PROVEN", res.stdout)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
