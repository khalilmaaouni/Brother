import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent / "regen_generated.py"


def run_git(args, cwd, timeout=30):
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_regen(repo, extra=None, timeout=30):
    cmd = [sys.executable, str(SCRIPT), "--repo", str(repo)]
    if extra:
        cmd.extend(extra)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def write_bundle_runtime(repo, fail=False):
    p = repo / "scripts" / "bundle_runtime.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    if fail:
        p.write_text("import sys\nsys.exit(1)\n")
    else:
        p.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "repo = Path('.')\n"
            "out = repo / 'bundle' / 'out.txt'\n"
            "expected = 'bundle ok'\n"
            "if '--check' in sys.argv:\n"
            "    if not out.is_file():\n"
            "        print('missing bundle/out.txt')\n"
            "        sys.exit(1)\n"
            "    if out.read_text() != expected:\n"
            "        print('bundle check failed')\n"
            "        sys.exit(1)\n"
            "    sys.exit(0)\n"
            "else:\n"
            "    out.parent.mkdir(parents=True, exist_ok=True)\n"
            "    out.write_text(expected)\n"
            "    sys.exit(0)\n"
        )


def write_system_doc(repo):
    p = repo / "scripts" / "system_doc.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "out = Path('SYSTEM.md')\n"
        "expected = '# System\\n'\n"
        "if '--check' in sys.argv:\n"
        "    if not out.is_file():\n"
        "        print('missing SYSTEM.md')\n"
        "        sys.exit(1)\n"
        "    if out.read_text() != expected:\n"
        "        print('system doc check failed')\n"
        "        sys.exit(1)\n"
        "    sys.exit(0)\n"
        "else:\n"
        "    out.write_text(expected)\n"
        "    sys.exit(0)\n"
    )


def write_checksums(repo, prod):
    script = repo / prod / "scripts" / "checksums.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/bin/sh\n"
        "echo dummy > \"$1\"\n"
    )
    script.chmod(0o755)


def git_init_repo(repo):
    run_git(["init"], repo)
    run_git(["config", "user.name", "t"], repo)
    run_git(["config", "user.email", "t@t"], repo)


class TestRegen(unittest.TestCase):
    def test_clean_regenerates(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git_init_repo(repo)
            write_bundle_runtime(repo)
            write_system_doc(repo)
            write_checksums(repo, "products/brothermode")
            write_checksums(repo, "products/brothersbe")
            (repo / "README.md").write_text("init\n")
            run_git(["add", "."], repo)
            run_git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"], repo)
            r = run_regen(repo)
            self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
            self.assertIn("python3 scripts/bundle_runtime.py: exit 0", r.stdout)
            self.assertIn("python3 scripts/system_doc.py: exit 0", r.stdout)
            self.assertIn("python3 scripts/bundle_runtime.py --check: exit 0", r.stdout)
            self.assertIn("python3 scripts/system_doc.py --check: exit 0", r.stdout)
            self.assertIn("bundle", r.stdout)
            self.assertIn("stage these: git add", r.stdout)
            self.assertTrue((repo / "bundle" / "out.txt").is_file())
            self.assertTrue((repo / "SYSTEM.md").is_file())

    def test_refuses_dirty_non_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git_init_repo(repo)
            write_bundle_runtime(repo)
            write_system_doc(repo)
            (repo / "README.md").write_text("init\n")
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "app.py").write_text("hello\n")
            run_git(["add", "."], repo)
            run_git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"], repo)
            (repo / "src" / "app.py").write_text("changed\n")
            bundle_out = repo / "bundle" / "out.txt"
            if bundle_out.exists():
                bundle_out.unlink()
            r = run_regen(repo)
            self.assertEqual(r.returncode, 1)
            self.assertIn("src/app.py", r.stdout + r.stderr)
            self.assertFalse(bundle_out.is_file(), "generator should not run when refusing dirty tree")

    def test_generator_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git_init_repo(repo)
            write_bundle_runtime(repo, fail=True)
            write_system_doc(repo)
            (repo / "README.md").write_text("init\n")
            run_git(["add", "."], repo)
            run_git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"], repo)
            r = run_regen(repo)
            self.assertEqual(r.returncode, 1)
            self.assertIn("python3 scripts/bundle_runtime.py", r.stdout + r.stderr)

    def test_missing_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git_init_repo(repo)
            write_system_doc(repo)
            (repo / "README.md").write_text("init\n")
            run_git(["add", "."], repo)
            run_git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"], repo)
            r = run_regen(repo)
            self.assertEqual(r.returncode, 2)
            self.assertIn("bundle_runtime.py", r.stdout + r.stderr)

    def test_check_only_runs_only_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git_init_repo(repo)
            write_bundle_runtime(repo)
            write_system_doc(repo)
            (repo / "README.md").write_text("init\n")
            run_git(["add", "."], repo)
            run_git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"], repo)
            r0 = run_regen(repo)
            self.assertEqual(r0.returncode, 0)
            run_git(["add", "."], repo)
            run_git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "gen"], repo)
            (repo / "bundle" / "out.txt").write_text("corrupted\n")
            (repo / "src").mkdir(exist_ok=True)
            (repo / "src" / "dirty.py").write_text("dirty\n")
            run_git(["add", "src/dirty.py"], repo)
            r = run_regen(repo, extra=["--check-only"])
            self.assertEqual(r.returncode, 1)
            self.assertIn("python3 scripts/bundle_runtime.py --check", r.stdout + r.stderr)
            lines = r.stdout.splitlines()
            gen_without_check = [l for l in lines if l.strip() == "python3 scripts/bundle_runtime.py: exit 0" or "python3 scripts/bundle_runtime.py: exit" in l and "--check" not in l]
            self.assertEqual(len(gen_without_check), 0, msg="--check-only should not run generators: " + r.stdout)

    def test_rename_either_side_outside_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git_init_repo(repo)
            write_bundle_runtime(repo)
            write_system_doc(repo)
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "app.py").write_text("hello\n")
            (repo / "README.md").write_text("init\n")
            run_git(["add", "."], repo)
            run_git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init"], repo)
            (repo / "bundle").mkdir(parents=True, exist_ok=True)
            r_mv = run_git(["mv", "src/app.py", "bundle/app.py"], repo)
            self.assertEqual(r_mv.returncode, 0, msg=r_mv.stdout + r_mv.stderr)
            bundle_out = repo / "bundle" / "out.txt"
            if bundle_out.exists():
                bundle_out.unlink()
            r = run_regen(repo)
            self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
            self.assertIn("app.py", r.stdout + r.stderr)
            self.assertFalse(bundle_out.is_file(), "generator should not run when rename from outside to inside is present")


if __name__ == "__main__":
    unittest.main()
