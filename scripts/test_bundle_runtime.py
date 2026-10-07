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
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

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

    def test_native_evidence_support_tool_is_in_the_closure(self):
        for name in ("native_evidence.py", "mobile_workflow.py", "mobile_design.py"):
            self.assertIn(name, self.closure)

    def test_custom_closure_entry_does_not_gain_runtime_support_roots(self):
        self.assertEqual(BR.compute_closure(entry="door.py", scripts_dir=HERE),
                         BR._closure_from_entries(["door.py"], HERE))


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
        # A checkout property: an export shaped tree ships bundle/ and products/ without plugin/, so a fresh generation
        # there cannot list the recorder it never had the source for, and the manifest reads stale by construction
        # (2026-09-24, the hermetic check on the export tree). Skipped by name there, proven from a checkout.
        src = os.path.join(os.path.dirname(HERE), "plugin", "runtime", "brother", "core", "dream_record.py")
        if not os.path.isfile(src):
            self.skipTest("the recorder source is not in this tree (export shape): %s" % src)
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
        self.env["DOOR_MODEL_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(self.decomposer))
        self.env["MODEL_WORKER_CMD"] = "%s %s" % (shlex.quote(sys.executable), shlex.quote(self.model))
        # A generated runtime lives under self.tmp, not under this hub
        # checkout, so loop_bridge's own HUB_CANDIDATE (relative to the
        # launcher's file) never resolves here: on a virgin machine with
        # no plugin install and no developer-home checkout, resolution
        # fell through to DEV_CANDIDATE, which exists only on the machine
        # that wrote it (measured: repro-*.log). Pointing this test at the
        # hub's OWN engine keeps it proving the shipped launcher without
        # borrowing an engine from outside the tree.
        self.env["BROTHER_RUNTIME_ROOT"] = os.path.normpath(
            os.path.join(HERE, "..", "products", "brothermode", "tools"))

    def test_launcher_integrates_a_stub_outcome_from_a_non_checkout_cwd(self):
        proc = sh([sys.executable, self.launcher, "a file exists",
                  "--cwd", self.repo, "--runs-root", self.tmp],
                 cwd=self.outside_cwd, env=self.env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "launched.txt")), out)
        self.assertIn("integrated (1):", out, out)
        self.assertIn("L1", out, out)

    def test_with_no_engine_reachable_the_launcher_says_no_data_not_a_crash(self):
        """The other half of the fix above: with BROTHER_RUNTIME_ROOT unset
        AND HOME pointed at an empty directory (so neither a plugin cache
        nor a developer checkout answers), the launcher must resolve NO
        engine and print the NO-DATA adapter line, never silently borrow
        one from outside the tree. This is what would have failed on the
        runner before this fix, had the runner also lacked the hub's own
        engine at HUB_CANDIDATE; it is kept here so a future regression
        that reintroduces a machine-only fallback is caught locally too."""
        env = dict(self.env)
        env.pop("BROTHER_RUNTIME_ROOT", None)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("BROTHER_CONFIG_DIR", None)
        env["HOME"] = tempfile.mkdtemp(prefix="bundle-runtime-empty-home-")
        proc = sh([sys.executable, self.launcher, "a file exists",
                  "--cwd", self.repo, "--runs-root", self.tmp],
                 cwd=self.outside_cwd, env=env)
        out = proc.stdout + proc.stderr
        self.assertIn("no worker adapter could be loaded, so no worker ran",
                      out, out)
        self.assertFalse(
            os.path.exists(os.path.join(self.repo, "launched.txt")), out)

    def test_launcher_forwards_an_explicit_runs_root_without_duplicating_it(self):
        proc = sh([sys.executable, self.launcher, "--help"], cwd=self.outside_cwd)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("--runs-root", out, out)


class LauncherAddsNoRunsRootOfItsOwn(unittest.TestCase):
    """The generated launcher, loaded from the generated file so this proves
    the SHIPPED source, not a copy re-typed here.

    Until 2026-10-06 the launcher computed a default runs root of its own
    (BROTHER_RUNS_ROOT, then a writable git toplevel, then
    ~/.claude/brother-run) and three cases here held it to that. The engine
    run directly decided otherwise, so the two forms kept a run's records in
    two places. The rule now lives in the engine alone
    (brother_run.default_runs_root) and is proven for BOTH forms, as
    processes, by scripts/test_runs_root_one_rule.py. What is held here is
    the launcher's half: it passes the caller's arguments and nothing else."""

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

    def launched(self, args):
        """The argv the launcher would start, with no process started."""
        started = []
        with mock.patch.object(self.mod.subprocess, "call",
                               lambda argv: started.append(argv) or 0):
            self.assertEqual(self.mod.main(list(args)), 0)
        return started

    def test_the_launcher_passes_the_callers_arguments_and_nothing_else(self):
        args = ["--continue", "--cwd", "/some/target"]
        self.assertEqual(self.launched(args),
                         [[sys.executable, self.mod.BROTHER_RUN] + args])

    def test_an_explicit_runs_root_reaches_the_engine_untouched(self):
        args = ["an outcome", "--runs-root", "/somewhere/named"]
        self.assertEqual(self.launched(args),
                         [[sys.executable, self.mod.BROTHER_RUN] + args])

    def test_the_launcher_defines_no_default_of_its_own(self):
        self.assertFalse(hasattr(self.mod, "default_runs_root"),
                         "the launcher computes a runs root of its own again")


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



class HookPackageFilesFollowSiblingPathJoins(unittest.TestCase):
    """F-002. bm_store.py own _schema() loads brotherme/core/schema.py by
    a path computed from its own location
    (os.path.join(candidate_root, "brotherme", "core", "schema.py")),
    climbing one level above tools/ into a sibling PACKAGE the flat,
    same-directory closure walk (_closure_from_entries, which only
    matches a bare "name.py" constant against files inside tools/ itself)
    never saw. Mirrors the F-001 shape (hub PR 520,
    HookClosureFollowsTestPrefixedSiblings: the closure follows a path
    expression read from the AST, never a hand list) one directory
    further out. Both tests here fail on the untouched pre-fix generator
    (compute_hook_package_files did not exist and the shipped bundle
    carried no brotherme/ at all, which is the exact FileNotFoundError
    this item closes) and pass once bundle_runtime.py derives the
    reference from the source and generate_hooks() mirrors it."""

    def test_schema_py_named_as_a_hook_package_file(self):
        tools_dir, closure = BR.compute_hook_closure("brothermode")
        pkgs = BR.compute_hook_package_files("brothermode", tools_dir,
                                             closure)
        self.assertIn("brotherme/core/schema.py", pkgs, pkgs)

    def test_shipped_bundle_carries_schema_py_at_the_path_bm_store_computes(self):
        # The exact relative path bm_store.py _schema() joins for the
        # checkout layout: tools_dir one level up, then brotherme/core.
        repo_root = os.path.dirname(HERE)
        shipped = os.path.join(repo_root, "bundle", "runtime", "hooks",
                               "brothermode", "brotherme", "core",
                               "schema.py")
        source = os.path.join(repo_root, "products", "brothermode",
                              "brotherme", "core", "schema.py")
        self.assertTrue(
            os.path.isfile(shipped),
            "%s missing: an installed plugin's bm_store.py._schema() "
            "crashes with FileNotFoundError (F-002)" % shipped)
        with open(shipped, "rb") as fh:
            shipped_bytes = fh.read()
        with open(source, "rb") as fh:
            source_bytes = fh.read()
        self.assertEqual(shipped_bytes, source_bytes)

    def test_a_flat_tools_sibling_referenced_via_join_is_not_duplicated(self):
        # vault_recall_hook.py own os.path.join(_ROOT, "tools",
        # "bm_vault.py") names a file the bare-constant closure walk
        # already carries as "bm_vault.py"; compute_hook_package_files
        # must not re-list it under a second manifest path.
        tools_dir, closure = BR.compute_hook_closure("brothermode")
        pkgs = BR.compute_hook_package_files("brothermode", tools_dir,
                                             closure)
        self.assertNotIn("tools/bm_vault.py", pkgs, pkgs)

    def test_brothersbe_tasks_py_is_also_found_by_the_same_general_rule(self):
        # sbe_authority_hook.py and three siblings all load
        # src/brothersbe/tasks.py the same way bm_store.py loads
        # schema.py; the rule is general (read from the AST), not a
        # brothermode-only hand list, so this second product's own real
        # gap is found by the identical mechanism.
        tools_dir, closure = BR.compute_hook_closure("brothersbe")
        pkgs = BR.compute_hook_package_files("brothersbe", tools_dir,
                                             closure)
        self.assertIn("src/brothersbe/tasks.py", pkgs, pkgs)
        repo_root = os.path.dirname(HERE)
        shipped = os.path.join(repo_root, "bundle", "runtime", "hooks",
                               "brothersbe", "src", "brothersbe",
                               "tasks.py")
        self.assertTrue(os.path.isfile(shipped), shipped)


class HookCommandsSmokeRunClean(unittest.TestCase):
    """Every Stop, SessionStart, PreCompact and SessionEnd command in the
    real, committed bundle/hooks/hooks.json (see
    bundle_runtime.HOOKS_JSON_NAME), run from a temporary HOME and
    a temporary git repository with CLAUDE_PLUGIN_ROOT pointed at the real
    bundle/, fed an empty JSON payload on stdin exactly like a live hook
    invocation. A crash (a Python traceback on stderr) is a failure; a
    non-zero exit with no traceback is fine, because every chained program
    fails OPEN by design (bm_hookchain.py's own module docstring). This is
    the end-to-end version of HookPackageFilesFollowSiblingPathJoins
    above: that class checks the missing FILE, this one checks that
    nothing which loads it by path still crashes at runtime. Fails on the
    untouched pre-fix bundle (bm_hookchain.py stop, and every command that
    chains through bm_view.py, printed "Traceback (most recent call
    last)" from the missing brotherme/core/schema.py) and passes once the
    file ships."""

    EVENTS = ("Stop", "SessionStart", "PreCompact", "SessionEnd")

    @classmethod
    def setUpClass(cls):
        repo_root = os.path.dirname(HERE)
        cls.bundle_dir = os.path.join(repo_root, "bundle")
        # Since 2026-09-30 (bundle_runtime.HOOKS_JSON_NAME, the one-plugin
        # ADR) this IS hooks.json, the path Claude Code auto-loads; every
        # command runs through runtime/hooks/hook_guard.py, which under the
        # temporary HOME below finds no old plugin and execs the command.
        hooks_json = os.path.join(cls.bundle_dir, "hooks", BR.HOOKS_JSON_NAME)
        with open(hooks_json, encoding="utf-8") as fh:
            cls.hooks_doc = json.load(fh)

    def _commands(self):
        cmds = []
        for event in self.EVENTS:
            for group in self.hooks_doc.get("hooks", {}).get(event, []):
                for h in group.get("hooks", []):
                    cmds.append((event, h.get("command", "")))
        return cmds

    def test_every_stop_sessionstart_precompact_sessionend_command_runs_clean(self):
        import shlex
        cmds = self._commands()
        self.assertTrue(cmds, "no commands found for %s" % (self.EVENTS,))
        tmp = tempfile.mkdtemp(prefix="hook-smoke-")
        home = os.path.join(tmp, "home")
        repo = os.path.join(tmp, "repo")
        os.makedirs(home)
        os.makedirs(repo)
        sh(["git", "init", "-q"], cwd=repo)
        sh(["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit",
           "--allow-empty", "-q", "-m", "x"], cwd=repo)
        env = dict(os.environ)
        env["HOME"] = home
        env["CLAUDE_PLUGIN_ROOT"] = self.bundle_dir
        failures = []
        for event, command in cmds:
            args = shlex.split(command.replace(
                "${CLAUDE_PLUGIN_ROOT}", self.bundle_dir))
            proc = subprocess.run(args, cwd=repo, env=env, input="{}",
                                  capture_output=True, text=True,
                                  timeout=60)
            if "Traceback" in proc.stderr:
                failures.append("%s: %s\n%s"
                                % (event, command, proc.stderr[-2000:]))
        self.assertEqual([], failures,
                         "hook command(s) crashed instead of failing "
                         "open:\n\n" + "\n\n".join(failures))


class HookClosureFollowsTestPrefixedSiblings(unittest.TestCase):
    """F-001: a hook's own runtime dependency can be named test_*.py (the
    shipped brother 1.0.10 bundle dropped tools/test_all.py, which
    bm_fence_hook.py loads by path as its battery gate module, because
    _script_files used to exclude every test_*.py name from the sibling-
    reference candidate pool). Template: ManifestMatchesTheClosure and
    DriftDetectedOnSourceEditWithoutRegen above, adapted from the scripts/
    closure to the products/*/tools/ hook closure."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-hook-closure-")
        self.products_dir = os.path.join(self.tmp, "products")
        self.tools_dir = os.path.join(self.products_dir, "fakeprod", "tools")
        os.makedirs(self.tools_dir)
        hooks_dir = os.path.join(self.products_dir, "fakeprod", "hooks")
        os.makedirs(hooks_dir)
        with open(os.path.join(hooks_dir, BR.PRODUCT_HOOKS_JSON_NAME), "w",
                 encoding="utf-8") as fh:
            json.dump({"hooks": {"PreToolUse": [{"hooks": [{
                "type": "command",
                "command": '$env:PY "${CLAUDE_PLUGIN_ROOT}/tools/fake_hook.py"',
            }]}]}}, fh)
        # fake_hook.py: same shape as bm_fence_hook.py's own
        # os.path.join(HERE, "test_all.py") load, naming a test-prefixed
        # sibling it needs at runtime.
        with open(os.path.join(self.tools_dir, "fake_hook.py"), "w",
                 encoding="utf-8") as fh:
            fh.write(
                "import os\n"
                "HERE = os.path.dirname(os.path.abspath(__file__))\n"
                "GATE = os.path.join(HERE, \"test_gate.py\")\n")
        # test_gate.py: the real dependency. Its OWN body names a second
        # test-prefixed file the way test_all.py names its whole suite; that
        # name must NOT cascade into the closure just because test_gate.py
        # got swept in.
        with open(os.path.join(self.tools_dir, "test_gate.py"), "w",
                 encoding="utf-8") as fh:
            fh.write(
                "OTHER_SUITES = (\"test_unrelated.py\",)\n")
        with open(os.path.join(self.tools_dir, "test_unrelated.py"), "w",
                 encoding="utf-8") as fh:
            fh.write("# a sibling test file test_gate.py merely NAMES\n")

    def test_a_hook_naming_a_test_prefixed_sibling_pulls_it_in_without_cascading(self):
        tools_dir, closure = BR.compute_hook_closure(
            "fakeprod", products_dir=self.products_dir)
        self.assertEqual(tools_dir, self.tools_dir)
        self.assertEqual(closure, ["fake_hook.py", "test_gate.py"],
                         "the rule must discover test_gate.py because "
                         "fake_hook.py names it by path, and must not also "
                         "sweep in test_unrelated.py, which only test_gate.py "
                         "itself mentions")

    def test_the_real_brothermode_closure_includes_test_all_py_beside_the_fence_hook(self):
        # Against the real repository tree (read-only: compute_hook_closure
        # takes no write action), never a synthetic fixture: this is the
        # exact shape of the shipped defect, bm_fence_hook.py beside
        # test_all.py in products/brothermode/tools/.
        tools_dir, closure = BR.compute_hook_closure("brothermode")
        self.assertIn("bm_fence_hook.py", closure)
        self.assertIn("test_all.py", closure,
                      "bm_fence_hook.py loads test_all.py as its gate "
                      "module; the closure must ship it beside the hook")


class EveryMirroredHookFileMatchesItsProductSource(unittest.TestCase):
    """F-mirror-drift (release 1.0.13 hardening audit, 2026-09-11):
    bundle/runtime/hooks/brothermode/tools/ ships 142 files (an installed
    plugin runs whatever is actually IN that directory), but
    compute_hook_closure's own hooks.json walk only reached 54 of them, so
    check_hooks() never looked at the other 88. Four of those 88 drifted
    from products/brothermode/tools/: bm_project.py (missing cmd_adopt
    entirely, while brother_run.py's own ADOPT_COMMAND still tells users to
    run "bm_project.py adopt", so they get "unknown command" exit 2),
    bm_project_facts.py (stale PUBLIC_INSTALL_TAG), bm_recurrence.py and
    bm_vault_jbench.py. Against the real repository tree, read-only, same
    template as RealRepositoryCliIsClean above: this fails on the untouched
    pre-fix tree (the four files really do differ) and passes once every
    mirrored file is regenerated from its source."""

    def test_every_py_file_the_mirror_ships_is_byte_identical_to_its_source(self):
        repo_root = os.path.dirname(HERE)
        mismatches = []
        for product in BR.HOOK_PRODUCTS:
            mirror_dir = os.path.join(repo_root, "bundle", "runtime", "hooks",
                                      product, "tools")
            source_dir = os.path.join(repo_root, "products", product, "tools")
            if not os.path.isdir(mirror_dir):
                continue
            for name in sorted(os.listdir(mirror_dir)):
                if not name.endswith(".py"):
                    continue
                source_path = os.path.join(source_dir, name)
                mirror_path = os.path.join(mirror_dir, name)
                if not os.path.isfile(source_path):
                    mismatches.append("%s/tools/%s: ships in bundle/runtime "
                                      "but products/%s/tools/%s no longer "
                                      "exists" % (product, name, product, name))
                    continue
                with open(source_path, "rb") as fh:
                    source_bytes = fh.read()
                with open(mirror_path, "rb") as fh:
                    mirror_bytes = fh.read()
                if source_bytes != mirror_bytes:
                    mismatches.append("%s/tools/%s: bundle/runtime copy does "
                                      "not match its products/ source"
                                      % (product, name))
        self.assertEqual(mismatches, [], mismatches)


class HookMirrorFilesOutsideTheHooksJsonClosureAreStillTracked(unittest.TestCase):
    """The mechanism behind EveryMirroredHookFileMatchesItsProductSource,
    reproduced with a fake product so it does not depend on the real tree's
    current (now fixed) drift: a tool that ships in the mirror because it
    is already there (bm_project.py's shape: never named by hooks.json, but
    invoked directly by users) must still be regenerated by generate_hooks()
    and must still fail --check when its source moves without it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-hook-mirror-")
        self.products_dir = os.path.join(self.tmp, "products")
        self.runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        self.product = "fakeprod3"
        tools_dir = os.path.join(self.products_dir, self.product, "tools")
        hooks_dir = os.path.join(self.products_dir, self.product, "hooks")
        os.makedirs(tools_dir)
        os.makedirs(hooks_dir)
        with open(os.path.join(hooks_dir, BR.PRODUCT_HOOKS_JSON_NAME), "w",
                 encoding="utf-8") as fh:
            json.dump({"hooks": {"PreToolUse": [{"hooks": [{
                "type": "command",
                "command": '$env:PY "${CLAUDE_PLUGIN_ROOT}/tools/fake_hook.py"',
            }]}]}}, fh)
        with open(os.path.join(tools_dir, "fake_hook.py"), "w",
                 encoding="utf-8") as fh:
            fh.write("# a real hook tool, reached by hooks.json\n")
        # orphan_tool.py: never named by hooks.json, exactly bm_project.py's
        # own shape -- a tool users invoke directly, that ended up mirrored
        # once outside the generator.
        self.orphan_source = os.path.join(tools_dir, "orphan_tool.py")
        with open(self.orphan_source, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")

        # Generate through the real hooks.json closure first (mirrors only
        # fake_hook.py, exactly as the untouched generator would).
        BR.generate_hooks(products=[self.product],
                          products_dir=self.products_dir,
                          runtime_dir=self.runtime_dir)
        # Simulate the historical hand copy that put bm_project.py and its
        # three siblings into the mirror once, outside the generator.
        self.orphan_mirror = os.path.join(self.runtime_dir, "hooks",
                                          self.product, "tools",
                                          "orphan_tool.py")
        shutil.copyfile(self.orphan_source, self.orphan_mirror)

    def test_a_mirrored_file_outside_the_hooks_json_closure_is_regenerated(self):
        with open(self.orphan_source, "a", encoding="utf-8") as fh:
            fh.write("VALUE = 2\n")
        BR.generate_hooks(products=[self.product],
                          products_dir=self.products_dir,
                          runtime_dir=self.runtime_dir)
        with open(self.orphan_source, "rb") as fh:
            source_bytes = fh.read()
        with open(self.orphan_mirror, "rb") as fh:
            mirror_bytes = fh.read()
        self.assertEqual(source_bytes, mirror_bytes,
                         "orphan_tool.py: mirror was not regenerated from "
                         "its product source")

    def test_check_hooks_reports_drift_in_a_file_outside_the_closure(self):
        with open(self.orphan_source, "a", encoding="utf-8") as fh:
            fh.write("VALUE = 2\n")
        ok, problems = BR.check_hooks(products=[self.product],
                                      products_dir=self.products_dir,
                                      runtime_dir=self.runtime_dir)
        self.assertFalse(ok, "a drifted file that already ships in the "
                             "mirror must fail --check, not pass silently: "
                             "%s" % problems)
        self.assertTrue(any("orphan_tool.py" in p for p in problems), problems)


class GenerateHooksRemovesAMirrorWhoseSourceIsGone(unittest.TestCase):
    """The write-side counterpart of the DRIFT class above: check_hooks()
    already reports (read-only) when a mirrored tool's products/ source has
    been deleted entirely (2026-09-11, F-mirror-drift), but generate_hooks()
    had no code to actually remove that stale copy, so a regeneration could
    never clear the drift check_hooks itself was reporting. Ported from PR
    690 (2026-09-14)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-orphan-cleanup-")
        self.products_dir = os.path.join(self.tmp, "products")
        self.runtime_dir = os.path.join(self.tmp, "bundle", "runtime")
        self.product = "fakeprod4"
        # check_hooks() reads the bundle manifest since the one-plugin ADR
        # (an unreadable one blocks), so the temp bundle carries one; this
        # class measures only the orphaned mirror.
        manifest_dir = os.path.join(self.tmp, "bundle", ".claude-plugin")
        os.makedirs(manifest_dir)
        with open(os.path.join(manifest_dir, "plugin.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"name": "brother", "version": "1.1.0"}, fh)
        tools_dir = os.path.join(self.products_dir, self.product, "tools")
        hooks_dir = os.path.join(self.products_dir, self.product, "hooks")
        os.makedirs(tools_dir)
        os.makedirs(hooks_dir)
        with open(os.path.join(hooks_dir, BR.PRODUCT_HOOKS_JSON_NAME), "w",
                 encoding="utf-8") as fh:
            json.dump({"hooks": {"PreToolUse": [{"hooks": [{
                "type": "command",
                "command": '$env:PY "${CLAUDE_PLUGIN_ROOT}/tools/fake_hook.py"',
            }]}]}}, fh)
        with open(os.path.join(tools_dir, "fake_hook.py"), "w",
                 encoding="utf-8") as fh:
            fh.write("# a real hook tool, reached by hooks.json\n")
        self.orphan_source = os.path.join(tools_dir, "gone_tool.py")
        with open(self.orphan_source, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")
        BR.generate_hooks(products=[self.product],
                          products_dir=self.products_dir,
                          runtime_dir=self.runtime_dir)
        self.orphan_mirror = os.path.join(self.runtime_dir, "hooks",
                                          self.product, "tools",
                                          "gone_tool.py")
        shutil.copyfile(self.orphan_source, self.orphan_mirror)
        # Now delete the source entirely: this is the condition
        # check_hooks() already detects read-only.
        os.remove(self.orphan_source)

    def test_check_hooks_reports_the_deleted_source_before_any_regen(self):
        ok, problems = BR.check_hooks(products=[self.product],
                                      products_dir=self.products_dir,
                                      runtime_dir=self.runtime_dir)
        self.assertFalse(ok, problems)
        self.assertTrue(any("gone_tool.py" in p and "no longer exists" in p
                           for p in problems), problems)

    def test_generate_hooks_removes_the_stale_mirror_and_clears_the_drift(self):
        self.assertTrue(os.path.isfile(self.orphan_mirror))
        BR.generate_hooks(products=[self.product],
                          products_dir=self.products_dir,
                          runtime_dir=self.runtime_dir)
        self.assertFalse(
            os.path.isfile(self.orphan_mirror),
            "gone_tool.py: generate_hooks() left a mirror copy in place "
            "whose products/ source no longer exists anywhere")
        ok, problems = BR.check_hooks(products=[self.product],
                                      products_dir=self.products_dir,
                                      runtime_dir=self.runtime_dir)
        self.assertTrue(ok, problems)


INSTALLED_RECORDING_PROBE = r"""
import json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
import dream_bridge
recorder = dream_bridge.recorder()
run_dir = tempfile.mkdtemp(prefix="installed-recording-")
event_id = recorder.record_decision(run_dir, "probe", {"seen": 1}, ["a", "b"], "a", "v1")
journal = recorder._journal()
print(json.dumps({"recorder": recorder.__file__, "journal": journal.__file__,
                  "journal_path": recorder.journal_path(), "event_id": event_id,
                  "events": len(journal.read(run_dir))}))
"""


class InstalledRuntimeCanRecordLearningEvents(unittest.TestCase):
    """REQ-BUNDLE: the recorder, its bridge and the journal ship in
    bundle/runtime, and the INSTALLED copy can write an event with no
    checkout beside it. Comparing bytes would not show a recorder that ships
    and cannot find its journal, so this records a real decision."""

    def setUp(self):
        # An EXPORT shaped tree ships bundle/ and products/ and not plugin/, so the recorder's SOURCE is not beside
        # scripts/ there and generate() cannot ship it from this tree (2026-09-24: the push gate's hermetic check ran
        # this class on the export tree for the first time and read three latent reds). The installed copy is proven
        # from a checkout; on a tree without the source this class says so and skips, which the gate reads as not
        # failing, never as a pass of the property.
        src = os.path.join(os.path.dirname(HERE), "plugin", "runtime", "brother", "core", "dream_record.py")
        if not os.path.isfile(src):
            self.skipTest("the recorder source is not in this tree (export shape): %s" % src)
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-recorder-")
        self.runtime_dir = os.path.join(self.tmp, "installed", "runtime")
        BR.generate(scripts_dir=HERE, runtime_dir=self.runtime_dir)

    def tearDown(self):
        if not getattr(self, "tmp", None): return
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_three_files_ship_and_are_in_the_manifest(self):
        manifest = _manifest(os.path.join(self.runtime_dir, BR.MANIFEST_NAME))
        listed = {entry["path"] for entry in manifest["files"]}
        for name in ("dream_bridge.py", "dream_record.py", "journal.py"):
            with self.subTest(name=name):
                self.assertTrue(os.path.isfile(os.path.join(self.runtime_dir, name)))
                self.assertIn(name, listed)

    def test_the_installed_copy_records_a_decision_with_no_checkout_beside_it(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("BROTHER_DREAM_RECORD_PATH", "BROTHER_JOURNAL_PATH", "PYTHONPATH")
               and not k.startswith("GIT_")}
        proc = subprocess.run([sys.executable, "-B", "-c", INSTALLED_RECORDING_PROBE],
                              cwd=self.runtime_dir, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
        seen = json.loads(proc.stdout.strip().splitlines()[-1])
        installed = os.path.realpath(self.runtime_dir)
        for key in ("recorder", "journal", "journal_path"):
            self.assertTrue(os.path.realpath(seen[key]).startswith(installed + os.sep),
                            "%s came from outside the installed copy: %s" % (key, seen[key]))
        self.assertEqual(os.path.realpath(seen["journal"]), os.path.realpath(seen["journal_path"]),
                         "the recorder reports one journal and loads another")
        self.assertTrue(seen["event_id"])
        self.assertEqual(seen["events"], 1)

    def test_an_edited_installed_recorder_turns_check_red(self):
        with open(os.path.join(self.runtime_dir, "dream_record.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# edited after install\n")
        ok, problems, _closure = BR.check(scripts_dir=HERE, runtime_dir=self.runtime_dir)
        self.assertFalse(ok)
        self.assertTrue(any("dream_record.py" in p for p in problems), problems)


class LoopMirror(unittest.TestCase):
    """M3 (2026-09-24): scripts/loop rides in bundle/runtime/loop byte for byte with its own manifest."""
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="loop-mirror-"); self.scripts = os.path.join(self.d, "scripts"); self.runtime = os.path.join(self.d, "runtime")
        os.makedirs(os.path.join(self.scripts, "loop")); os.makedirs(self.runtime)
        for n, body in (("a.py", "print(1)\n"), ("b.sh", "echo b\n"), ("c.py.bak-old", "old\n")): open(os.path.join(self.scripts, "loop", n), "w").write(body)
    def tearDown(self): shutil.rmtree(self.d, ignore_errors=True)
    def test_generate_mirrors_every_tool_and_writes_the_manifest(self):
        changed = BR.generate_loop(self.scripts, self.runtime)
        self.assertEqual(sorted(changed), ["loop/LOOP-MANIFEST.json", "loop/a.py", "loop/b.sh"])
        self.assertEqual(open(os.path.join(self.runtime, "loop", "a.py")).read(), "print(1)\n")
        self.assertFalse(os.path.exists(os.path.join(self.runtime, "loop", "c.py.bak-old")))
        self.assertEqual(BR.check_loop(self.scripts, self.runtime), [])
    def test_check_names_a_changed_a_missing_and_an_extra_tool(self):
        BR.generate_loop(self.scripts, self.runtime)
        open(os.path.join(self.runtime, "loop", "a.py"), "w").write("print(2)\n"); os.remove(os.path.join(self.runtime, "loop", "b.sh")); open(os.path.join(self.runtime, "loop", "z.py"), "w").write("x")
        p = BR.check_loop(self.scripts, self.runtime)
        self.assertTrue(any("a.py: bundle copy does not match" in x for x in p), p); self.assertTrue(any("b.sh: missing" in x for x in p), p); self.assertTrue(any("z.py: in the bundle" in x for x in p), p)
    def test_a_retired_tool_leaves_the_bundle_and_a_stale_manifest_is_named(self):
        BR.generate_loop(self.scripts, self.runtime); os.remove(os.path.join(self.scripts, "loop", "b.sh"))
        self.assertIn("loop/b.sh (removed)", BR.generate_loop(self.scripts, self.runtime))
        open(os.path.join(self.scripts, "loop", "a.py"), "a").write("# more\n")
        self.assertTrue(any("LOOP-MANIFEST.json: stale" in x for x in BR.check_loop(self.scripts, self.runtime)))
    def test_no_loop_directory_is_nothing_to_mirror_and_no_problem(self):
        shutil.rmtree(os.path.join(self.scripts, "loop"))
        self.assertEqual(BR.generate_loop(self.scripts, self.runtime), []); self.assertEqual(BR.check_loop(self.scripts, self.runtime), [])


def setUpModule():
    # Test-owned disk premise: worker admission reads this host's free
    # disk, so a full disk would otherwise read as a failing suite.
    from hermetic_worker_env import worker_environment
    _disk = worker_environment()
    _disk.__enter__()
    unittest.addModuleCleanup(_disk.__exit__, None, None, None)


class OnePluginHooksRefuseDrift(unittest.TestCase):
    """docs/architecture/ADR-ONE-PLUGIN-HOOKS.md (2026-09-30): the merged
    hooks live at bundle/hooks/hooks.json, every command runs through the
    double-fire guard, the retired union.json stays gone, and the manifest
    carries no "dependencies". Generated into a temp bundle from the real
    products/, then broken one way per test; check_hooks() must name each
    break. Never touches the committed bundle."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bundle-runtime-oneplugin-")
        self.bundle = os.path.join(self.tmp, "bundle")
        self.runtime = os.path.join(self.bundle, "runtime")
        os.makedirs(os.path.join(self.bundle, ".claude-plugin"))
        self.manifest = os.path.join(self.bundle, ".claude-plugin",
                                     "plugin.json")
        with open(self.manifest, "w", encoding="utf-8") as fh:
            json.dump({"name": "brother", "version": "1.1.0"}, fh)
        BR.generate_hooks(runtime_dir=self.runtime)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _check(self):
        return BR.check_hooks(runtime_dir=self.runtime)

    def test_fresh_generation_is_green_and_wrapped(self):
        ok, problems = self._check()
        self.assertTrue(ok, problems)
        with open(os.path.join(self.bundle, "hooks", "hooks.json"),
                  encoding="utf-8") as fh:
            doc = json.load(fh)
        commands = [h["command"] for groups in doc["hooks"].values()
                    for g in groups for h in g["hooks"]]
        self.assertTrue(commands)
        for c in commands:
            self.assertTrue(c.startswith(
                'python3 "${CLAUDE_PLUGIN_ROOT}/runtime/hooks/hook_guard.py" '), c)

    def test_matcher_reaches_the_guard_and_a_shell_active_matcher_is_refused(self):
        c = BR._rewrite_plugin_root_command(
            'python3 "${CLAUDE_PLUGIN_ROOT}/tools/x.py"', "brothermode",
            "PreToolUse", "Edit|Bash")
        self.assertIn(' brothermode PreToolUse "--matcher=Edit|Bash" python3 ', c)
        for event in ("PreToolUse;touch PWNED;", "pretooluse", "", None):
            with self.assertRaises(ValueError):
                BR._rewrite_plugin_root_command("python3 x.py", "brothermode",
                                                event, "Bash")
        for bad in ('a"b', "$(x)", "`x`", "a\\b", None):
            with self.assertRaises(ValueError):
                BR._rewrite_plugin_root_command("python3 x.py", "brothermode",
                                                "PreToolUse", bad)

    def test_union_json_is_refused(self):
        with open(os.path.join(self.bundle, "hooks", "union.json"), "w") as fh:
            fh.write("{}")
        ok, problems = self._check()
        self.assertFalse(ok)
        self.assertTrue(any("union.json" in p for p in problems), problems)

    def test_missing_hooks_json_is_refused(self):
        os.remove(os.path.join(self.bundle, "hooks", "hooks.json"))
        ok, problems = self._check()
        self.assertFalse(ok)
        self.assertTrue(any("hooks/hooks.json: missing" in p
                            for p in problems), problems)

    def test_unwrapped_command_is_stale(self):
        path = os.path.join(self.bundle, "hooks", "hooks.json")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace(
            'python3 \\"${CLAUDE_PLUGIN_ROOT}/runtime/hooks/hook_guard.py\\" '
            'brothermode ', '', 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        ok, problems = self._check()
        self.assertFalse(ok)
        self.assertTrue(any("hooks/hooks.json: stale" in p for p in problems),
                        problems)

    def test_tampered_guard_is_refused(self):
        with open(os.path.join(self.runtime, "hooks", "hook_guard.py"),
                  "a") as fh:
            fh.write("\n# tampered\n")
        ok, problems = self._check()
        self.assertFalse(ok)
        self.assertTrue(any("hook_guard.py" in p for p in problems), problems)

    def test_dependencies_key_is_refused(self):
        with open(self.manifest, "w", encoding="utf-8") as fh:
            json.dump({"name": "brother", "version": "1.1.0",
                       "dependencies": ["brothermode@^3.4.2"]}, fh)
        ok, problems = self._check()
        self.assertFalse(ok)
        self.assertTrue(any("dependencies" in p for p in problems), problems)

    def test_unreadable_manifest_blocks(self):
        with open(self.manifest, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        ok, problems = self._check()
        self.assertFalse(ok)
        self.assertTrue(any("unreadable" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
