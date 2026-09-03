"""bundle_runtime.py: does the packaged runtime actually match its sources,
and does drift from it actually get caught?

Four things are proven, none of them against the real repository's own
bundle/runtime (that would either mutate the shipped output while the suite
runs or force every assertion to tolerate whatever state a prior run left):

  1. Regenerating into an EMPTY temp copy, unchanged, is byte-stable: a
     second run changes nothing.
  2. RUNTIME-MANIFEST.json names exactly the closure and its hashes match
     both the scripts/ source and the bundle/runtime copy.
  3. Driven BACKWARDS: --check is green on a fresh generation, then a source
     edit with no regeneration turns it red, naming the edited file.
  4. The launcher (bundle/runtime/brother-run) runs brother_run.py end to end
     through a stub decomposer and a stub model, invoked from a cwd that is
     not a Brother checkout at all, exactly like test_brother_run.py's own
     TwoUnitsIntegrate but through the installed entry point.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bundle_runtime as BR  # noqa: E402


def sh(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=300)


def write_stub(tmpdir, name, body):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    os.chmod(path, 0o755)
    return path


def make_repo(tmp):
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "t"]):
        sh(["git"] + args, cwd=repo)
    with open(os.path.join(repo, "base.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "commit", "-q", "-m", "R0"], cwd=repo)
    return repo


# Same stub shape test_brother_run.py already uses at this exact seam: a
# "model" that reads the write scope off the prompt and writes it.
WRITER_MODEL = """
    import re, sys
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
        if path:
            with open(path, "w") as fh:
                fh.write("written by the stub model\\n")
    print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
"""


def copy_scripts_subset(dst_scripts_dir):
    """A temp copy of exactly the real closure (computed against the real
    scripts/), so the byte-stable and drift tests never touch this
    repository's own scripts/ or bundle/runtime/."""
    os.makedirs(dst_scripts_dir, exist_ok=True)
    closure = BR.compute_closure(scripts_dir=HERE)
    for name in closure:
        shutil.copy2(os.path.join(HERE, name), os.path.join(dst_scripts_dir, name))
    return closure


class ByteStableRegeneration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-")
        self.scripts_dir = os.path.join(self.tmp, "scripts")
        self.runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        self.closure = copy_scripts_subset(self.scripts_dir)

    def test_second_run_on_an_unchanged_tree_changes_nothing(self):
        closure1, changed1 = BR.generate(scripts_dir=self.scripts_dir,
                                         runtime_dir=self.runtime_dir)
        self.assertEqual(sorted(closure1), sorted(self.closure))
        self.assertTrue(changed1, "first generation must write something")

        before = {name: _read(os.path.join(self.runtime_dir, name))
                 for name in os.listdir(self.runtime_dir)}

        closure2, changed2 = BR.generate(scripts_dir=self.scripts_dir,
                                         runtime_dir=self.runtime_dir)
        self.assertEqual(changed2, [], "a second run on unchanged sources "
                                       "must report no changes, found: %s"
                                       % changed2)

        after = {name: _read(os.path.join(self.runtime_dir, name))
                 for name in os.listdir(self.runtime_dir)}
        self.assertEqual(before, after, "bytes must be identical across runs")


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


class ManifestMatchesTheClosure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-manifest-")
        self.scripts_dir = os.path.join(self.tmp, "scripts")
        self.runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        self.closure = copy_scripts_subset(self.scripts_dir)
        BR.generate(scripts_dir=self.scripts_dir, runtime_dir=self.runtime_dir)

    def test_every_closure_file_present_with_a_matching_hash(self):
        import json
        manifest_path = os.path.join(self.runtime_dir, BR.MANIFEST_NAME)
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        by_path = {f["path"]: f["sha256"] for f in manifest["files"]}

        expected_names = set(self.closure) | {BR.LAUNCHER_NAME}
        self.assertEqual(set(by_path), expected_names,
                         "the manifest must name exactly the closure plus "
                         "the launcher, no more and no less")

        for name in self.closure:
            source = _read(os.path.join(self.scripts_dir, name))
            copy = _read(os.path.join(self.runtime_dir, name))
            self.assertEqual(source, copy, "%s: runtime copy must be "
                                           "byte-identical to its source" % name)
            self.assertEqual(BR._sha256(source), by_path[name],
                             "%s: manifest hash must match the source" % name)

    def test_door_and_model_worker_and_work_record_are_in_the_closure(self):
        # The task's own named set: these are reached only through subprocess
        # string literals (door.py from brother_run.py, model_worker.py from
        # loop_bridge.py), never a Python import, so their presence proves the
        # closure walk follows both edge kinds, not just AST imports.
        for name in ("door.py", "model_worker.py", "work_record.py",
                    "loop_bridge.py", "claim_store.py", "graph_loop.py",
                    "integrate.py", "scope_audit.py", "worktree_lane.py"):
            self.assertIn(name, self.closure)


class DriftDetectedOnSourceEditWithoutRegen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-drift-")
        self.scripts_dir = os.path.join(self.tmp, "scripts")
        self.runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        copy_scripts_subset(self.scripts_dir)
        BR.generate(scripts_dir=self.scripts_dir, runtime_dir=self.runtime_dir)

    def test_check_is_green_then_a_silent_source_edit_turns_it_red(self):
        ok, problems, _ = BR.check(scripts_dir=self.scripts_dir,
                                   runtime_dir=self.runtime_dir)
        self.assertTrue(ok, "a fresh generation must check clean: %s" % problems)
        self.assertEqual(problems, [])

        target = os.path.join(self.scripts_dir, "claim_store.py")
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n# drift probe: edited without regenerating\n")

        ok2, problems2, _ = BR.check(scripts_dir=self.scripts_dir,
                                     runtime_dir=self.runtime_dir)
        self.assertFalse(ok2, "an edited source with no regeneration must "
                             "check red")
        self.assertTrue(any("claim_store.py" in p for p in problems2), problems2)


class RealRepositoryCliIsClean(unittest.TestCase):
    """The CLI's own module-level defaults (this repository's real scripts/
    and bundle/runtime/), separate from every temp-copy test above. Proves
    the wiring, not the closure logic: this repository's own bundle/runtime
    must be committed current, since nothing under it is hand edited."""

    def test_check_on_this_repository_is_green(self):
        proc = sh([sys.executable, os.path.join(HERE, "bundle_runtime.py"),
                  "--check"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class LauncherRunsOutsideAnyCheckout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-launcher-")
        self.scripts_dir = os.path.join(self.tmp, "scripts")
        self.runtime_dir = os.path.join(self.tmp, "install", "bundle", "runtime")
        copy_scripts_subset(self.scripts_dir)
        BR.generate(scripts_dir=self.scripts_dir, runtime_dir=self.runtime_dir)
        self.launcher = os.path.join(self.runtime_dir, BR.LAUNCHER_NAME)

        # A cwd with no relationship whatsoever to any Brother checkout, and
        # no .git at all, standing in for an installed plugin's runtime
        # invoked from an arbitrary user directory.
        self.outside_cwd = os.path.join(self.tmp, "nowhere")
        os.makedirs(self.outside_cwd)

        self.repo = make_repo(self.tmp)
        self.decomposer = write_stub(self.tmp, "decomposer.py", """
            import json, sys
            sys.stdin.read()
            print(json.dumps([
                {"id": "L1", "objective": "create a file",
                 "done_check": "test -f launched.txt",
                 "writes": ["launched.txt"], "deps": []},
            ]))
        """)
        self.model = write_stub(self.tmp, "writer_model.py", WRITER_MODEL)
        self.env = dict(os.environ)
        self.env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, self.decomposer)
        self.env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, self.model)

    def test_launcher_integrates_a_stub_outcome_from_a_non_checkout_cwd(self):
        proc = sh([sys.executable, self.launcher, "a file exists",
                  "--cwd", self.repo, "--runs-root", self.tmp],
                 cwd=self.outside_cwd, env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "launched.txt")), out)
        self.assertIn("integrated (1):", out, out)
        self.assertIn("L1", out, out)

    def test_launcher_forwards_an_explicit_runs_root_without_duplicating_it(self):
        proc = sh([sys.executable, self.launcher, "--help"], cwd=self.outside_cwd)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("--runs-root", out, out)


class LauncherDefaultRunsRoot(unittest.TestCase):
    """The launcher's own default_runs_root(), loaded from the generated
    file so this proves the SHIPPED source, not a copy re-typed here."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-default-root-")
        self.runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        os.makedirs(self.runtime_dir)
        launcher_path = os.path.join(self.runtime_dir, BR.LAUNCHER_NAME)
        with open(launcher_path, "w", encoding="utf-8") as fh:
            fh.write(BR.LAUNCHER_SOURCE)
        # spec_from_file_location relies on the path SUFFIX to pick a loader,
        # and the launcher's shipped name ("brother-run") has none, so the
        # loader is named explicitly rather than guessed.
        loader = importlib.machinery.SourceFileLoader("brother_run_launcher",
                                                       launcher_path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.mod = importlib.util.module_from_spec(spec)
        loader.exec_module(self.mod)

    def test_env_override_wins_over_everything(self):
        got = self.mod.default_runs_root(launcher_dir=self.runtime_dir,
                                         env={"BROTHER_RUNS_ROOT": "/somewhere/named"})
        self.assertEqual(got, "/somewhere/named")

    def test_no_git_checkout_falls_back_to_a_per_user_state_dir(self):
        # self.runtime_dir has no .git anywhere above it inside self.tmp, so
        # this is the INSTALLED case: no writable checkout beside the
        # launcher.
        got = self.mod.default_runs_root(launcher_dir=self.runtime_dir, env={})
        self.assertEqual(got, os.path.expanduser(
            os.path.join("~", ".claude", "brother-run")))

    def test_inside_a_writable_checkout_uses_its_toplevel(self):
        repo = make_repo(self.tmp)
        got = self.mod.default_runs_root(launcher_dir=repo, env={})
        self.assertEqual(os.path.realpath(got), os.path.realpath(repo))


if __name__ == "__main__":
    unittest.main()
