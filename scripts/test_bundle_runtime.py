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
  5. The source stamp (harness-identity-v1): the manifest names the hub
     revision it was generated from, says NO-DATA rather than guessing when
     it was generated outside a checkout, and --check TOLERATES a stamp that
     differs from a fresh generation while still refusing a hash that does.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
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

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


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


def _manifest(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


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

        # The launcher and the verifier are the two generated files with no
        # scripts/ source (E80 added the verifier); everything else is the
        # closure.
        expected_names = set(self.closure) | {BR.LAUNCHER_NAME,
                                              BR.VERIFIER_NAME}
        self.assertEqual(set(by_path), expected_names,
                         "the manifest must name exactly the closure plus "
                         "the launcher and the verifier, no more and no less")

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


class DataDirectoriesAreMirrored(unittest.TestCase):
    """DATA_DIRS ("packs"): a data directory a closure file references by a
    bare string constant (door.py's PACKS_DIR) is mirrored recursively into
    bundle/runtime, hashed into the manifest, and checked, exactly like a
    closure .py file. An unreferenced or absent directory is left alone."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-datadirs-")

    def test_a_referenced_data_dir_is_mirrored_and_hashed(self):
        scripts_dir = os.path.join(self.tmp, "scripts")
        runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        copy_scripts_subset(scripts_dir)
        shutil.copytree(os.path.join(HERE, "packs"),
                        os.path.join(scripts_dir, "packs"))

        closure, changed = BR.generate(scripts_dir=scripts_dir,
                                       runtime_dir=runtime_dir)
        self.assertIn("packs/core.json", changed, changed)
        self.assertIn("packs/data-science.json", changed, changed)

        manifest = _manifest(os.path.join(runtime_dir, BR.MANIFEST_NAME))
        by_path = {f["path"]: f["sha256"] for f in manifest["files"]}
        for rel in ("packs/core.json", "packs/data-science.json"):
            copy_path = os.path.join(runtime_dir, *rel.split("/"))
            source_path = os.path.join(scripts_dir, *rel.split("/"))
            source_bytes = _read(source_path)
            self.assertTrue(os.path.isfile(copy_path), rel)
            self.assertEqual(_read(copy_path), source_bytes, rel)
            self.assertIn(rel, by_path, by_path)
            self.assertEqual(by_path[rel],
                             hashlib.sha256(source_bytes).hexdigest(), rel)

        ok, problems, _ = BR.check(scripts_dir=scripts_dir,
                                   runtime_dir=runtime_dir)
        self.assertTrue(ok, problems)

        _closure2, changed2 = BR.generate(scripts_dir=scripts_dir,
                                          runtime_dir=runtime_dir)
        self.assertEqual(changed2, [], changed2)

    def test_an_edited_pack_turns_check_red(self):
        scripts_dir = os.path.join(self.tmp, "scripts")
        runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        copy_scripts_subset(scripts_dir)
        shutil.copytree(os.path.join(HERE, "packs"),
                        os.path.join(scripts_dir, "packs"))
        BR.generate(scripts_dir=scripts_dir, runtime_dir=runtime_dir)

        with open(os.path.join(scripts_dir, "packs", "core.json"),
                  "a", encoding="utf-8") as fh:
            fh.write("\n")

        ok, problems, _ = BR.check(scripts_dir=scripts_dir,
                                   runtime_dir=runtime_dir)
        self.assertFalse(ok, "an edited pack with no regeneration must "
                             "check red")
        self.assertTrue(any("packs/core.json" in p for p in problems),
                        problems)

    def test_an_unreferenced_data_dir_is_not_mirrored(self):
        scripts_dir = os.path.join(self.tmp, "scripts")
        runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        os.makedirs(scripts_dir)
        write_stub(scripts_dir, "brother_run.py", """
            print("stub entry, no data dir reference")
        """)
        packs_dir = os.path.join(scripts_dir, "packs")
        os.makedirs(packs_dir)
        with open(os.path.join(packs_dir, "core.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{}\n")

        BR.generate(scripts_dir=scripts_dir, runtime_dir=runtime_dir)

        self.assertFalse(os.path.exists(os.path.join(runtime_dir, "packs")))
        manifest = _manifest(os.path.join(runtime_dir, BR.MANIFEST_NAME))
        self.assertFalse(any(f["path"].startswith("packs/")
                             for f in manifest["files"]), manifest["files"])

    def test_absent_data_dir_is_fine(self):
        scripts_dir = os.path.join(self.tmp, "scripts")
        runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        copy_scripts_subset(scripts_dir)
        self.assertFalse(os.path.isdir(os.path.join(scripts_dir, "packs")))

        BR.generate(scripts_dir=scripts_dir, runtime_dir=runtime_dir)
        ok, problems, _ = BR.check(scripts_dir=scripts_dir,
                                   runtime_dir=runtime_dir)
        self.assertTrue(ok, problems)
        manifest = _manifest(os.path.join(runtime_dir, BR.MANIFEST_NAME))
        self.assertFalse(any(f["path"].startswith("packs/")
                             for f in manifest["files"]), manifest["files"])


class TheManifestStampsItsSourceRevision(unittest.TestCase):
    """harness-identity-v1 (the zero-context critic on a fresh clone of
    v1.0.0, 2026-09-03): an installed copy has no .git, so `git rev-parse`
    cannot name the engine that wrote a receipt and the manifest's stamp is
    the only honest source left. It has to be REAL when a checkout was
    there, and NO-DATA when one was not."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-stamp-")

    def test_generated_from_this_checkout_the_stamp_is_this_revision(self):
        if not os.path.exists(os.path.join(os.path.dirname(HERE), ".git")):
            self.skipTest("NO-DATA: not a git checkout, so there is no "
                          "revision for the generator to stamp")
        runtime_dir = os.path.join(self.tmp, "from-checkout", "runtime")
        # scripts_dir is THIS repository's real scripts/, so git can answer;
        # runtime_dir is a temp directory, so nothing here writes into the
        # repository's own bundle/runtime.
        BR.generate(scripts_dir=HERE, runtime_dir=runtime_dir)
        manifest = _manifest(os.path.join(runtime_dir, BR.MANIFEST_NAME))
        head = sh(["git", "rev-parse", "HEAD"], cwd=HERE).stdout.strip()
        self.assertEqual(manifest["source_revision"], head)
        self.assertNotIn(BR.NODATA, manifest["source_describe"],
                         manifest["source_describe"])

    def test_generated_outside_a_checkout_the_stamp_is_no_data(self):
        scripts_dir = os.path.join(self.tmp, "scripts")
        runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        copy_scripts_subset(scripts_dir)
        BR.generate(scripts_dir=scripts_dir, runtime_dir=runtime_dir)
        manifest = _manifest(os.path.join(runtime_dir, BR.MANIFEST_NAME))
        for field in BR.STAMP_FIELDS:
            self.assertTrue(manifest[field].startswith(BR.NODATA),
                            "%s: %s" % (field, manifest[field]))


class TheCheckIgnoresTheStampAndNothingElse(unittest.TestCase):
    """The stamp says where the bytes came from; the hashes say what they
    are. A tip that moved with no source edit must not turn --check red, and
    a hash that moved must."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-tolerance-")
        self.scripts_dir = os.path.join(self.tmp, "scripts")
        self.runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        copy_scripts_subset(self.scripts_dir)
        BR.generate(scripts_dir=self.scripts_dir, runtime_dir=self.runtime_dir)
        self.manifest_path = os.path.join(self.runtime_dir, BR.MANIFEST_NAME)

    def _rewrite(self, mutate):
        doc = _manifest(self.manifest_path)
        mutate(doc)
        with open(self.manifest_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")

    def test_a_stamp_from_another_revision_still_checks_green(self):
        self._rewrite(lambda d: d.update(
            {"source_revision": "a" * 40, "source_describe": "v9.9.9"}))
        ok, problems, _ = BR.check(scripts_dir=self.scripts_dir,
                                   runtime_dir=self.runtime_dir)
        self.assertTrue(ok, "only the stamp differs from a fresh generation, "
                            "which is a moved tip, not drift: %s" % problems)

    def test_that_same_stamp_survives_a_regeneration_untouched(self):
        self._rewrite(lambda d: d.update(
            {"source_revision": "a" * 40, "source_describe": "v9.9.9"}))
        _closure, changed = BR.generate(scripts_dir=self.scripts_dir,
                                        runtime_dir=self.runtime_dir)
        self.assertEqual(changed, [], "unchanged sources must rewrite "
                                      "nothing, stamp included: %s" % changed)
        self.assertEqual(_manifest(self.manifest_path)["source_revision"],
                         "a" * 40)

    def test_a_provisional_dirty_stamp_is_refreshed_instead(self):
        """The one stamp generate() does NOT carry forward: taken over
        uncommitted edits, it names the wrong commit and says so, so the
        next generation replaces it. Here the temp sources are not a
        checkout at all, so the refreshed value is an honest NO-DATA."""
        self._rewrite(lambda d: d.update(
            {"source_revision": "a" * 40, "source_describe": "v9.9.9-dirty"}))
        _closure, changed = BR.generate(scripts_dir=self.scripts_dir,
                                        runtime_dir=self.runtime_dir)
        self.assertEqual(changed, [BR.MANIFEST_NAME], changed)
        self.assertTrue(_manifest(self.manifest_path)["source_describe"]
                        .startswith(BR.NODATA),
                        _manifest(self.manifest_path)["source_describe"])

    def test_a_wrong_hash_is_still_refused(self):
        self._rewrite(lambda d: d["files"].__setitem__(
            0, {"path": d["files"][0]["path"], "sha256": "b" * 64}))
        ok, problems, _ = BR.check(scripts_dir=self.scripts_dir,
                                   runtime_dir=self.runtime_dir)
        self.assertFalse(ok, "a hash that does not match a fresh generation "
                             "is drift")
        self.assertTrue(any("stale" in p for p in problems), problems)

    def test_a_manifest_with_no_stamp_at_all_is_refused(self):
        self._rewrite(lambda d: [d.pop(f) for f in BR.STAMP_FIELDS])
        ok, problems, _ = BR.check(scripts_dir=self.scripts_dir,
                                   runtime_dir=self.runtime_dir)
        self.assertFalse(ok, "a manifest with no stamp cannot name the "
                             "engine an installed copy runs")
        self.assertTrue(any("source_revision" in p for p in problems), problems)


class RealRepositoryCliIsClean(unittest.TestCase):
    """The CLI's own module-level defaults (this repository's real scripts/
    and bundle/runtime/), separate from every temp-copy test above. Proves
    the wiring, not the closure logic: this repository's own bundle/runtime
    must be committed current, since nothing under it is hand edited."""

    def test_check_on_this_repository_is_green(self):
        proc = sh([sys.executable, os.path.join(HERE, "bundle_runtime.py"),
                  "--check"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_real_manifest_lists_the_real_data_packs(self):
        # This repository's own door.py references "packs", so its
        # committed bundle/runtime/RUNTIME-MANIFEST.json must name both
        # real pack files, not just the closure.
        manifest_path = os.path.join(os.path.dirname(HERE), "bundle",
                                     "runtime", BR.MANIFEST_NAME)
        manifest = _manifest(manifest_path)
        paths = {f["path"] for f in manifest["files"]}
        self.assertIn("packs/core.json", paths, paths)
        self.assertIn("packs/data-science.json", paths, paths)


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


class ShippedVerifierReadsTheManifestWithNoScriptsBesideIt(unittest.TestCase):
    """E80 item 5 (2026-09-04, external release integrity trial on the
    public v1.0.1 clone): RUNTIME-MANIFEST.json carried a sha256 for every
    shipped file and NOTHING on an installed plugin ever read one.
    bundle_runtime.py --check cannot: it compares bundle/runtime against
    scripts/, and an installed plugin has no scripts/. So the runtime now
    ships verify_runtime.py beside the manifest.

    Every case here runs the GENERATED file out of a runtime directory with
    no scripts/ anywhere near it, which is the situation the verifier exists
    for. Driven backwards: a tamper reads FAIL and a missing manifest reads
    NO-DATA, so a green PASS is not the only outcome the code can produce."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-verify-")
        self.scripts_dir = os.path.join(self.tmp, "scripts")
        # The runtime lands OUTSIDE the scripts directory and nothing copies
        # scripts/ next to it: an installed plugin has only bundle/runtime.
        self.runtime_dir = os.path.join(self.tmp, "install", "bundle", "runtime")
        copy_scripts_subset(self.scripts_dir)
        BR.generate(scripts_dir=self.scripts_dir, runtime_dir=self.runtime_dir)
        self.verifier = os.path.join(self.runtime_dir, BR.VERIFIER_NAME)

    def _run(self):
        return sh([sys.executable, self.verifier], cwd=self.tmp)

    def test_the_verifier_ships_in_the_runtime_and_is_manifested(self):
        self.assertTrue(os.path.isfile(self.verifier), self.verifier)
        manifest = _manifest(os.path.join(self.runtime_dir, BR.MANIFEST_NAME))
        paths = [f["path"] for f in manifest["files"]]
        self.assertIn(BR.VERIFIER_NAME, paths, paths)

    def test_an_untouched_runtime_reads_pass(self):
        proc = self._run()
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("PASS", out, out)

    def test_a_one_byte_tamper_reads_fail_and_names_the_file(self):
        target = os.path.join(self.runtime_dir, BR.ENTRY)
        with open(target, "ab") as fh:
            fh.write(b"#")
        proc = self._run()
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("FAIL", out, out)
        self.assertIn(BR.ENTRY, out, out)

    def test_a_deleted_shipped_file_reads_fail(self):
        os.remove(os.path.join(self.runtime_dir, BR.ENTRY))
        proc = self._run()
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("FAIL", out, out)

    def test_a_missing_manifest_reads_no_data_and_never_passes(self):
        os.remove(os.path.join(self.runtime_dir, BR.MANIFEST_NAME))
        proc = self._run()
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn(BR.NODATA, out, out)
        self.assertNotIn("PASS", out, out)

    def test_check_notices_the_verifier_being_removed(self):
        os.remove(self.verifier)
        ok, problems, _closure = BR.check(scripts_dir=self.scripts_dir,
                                          runtime_dir=self.runtime_dir)
        self.assertFalse(ok)
        self.assertTrue(any(BR.VERIFIER_NAME in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
