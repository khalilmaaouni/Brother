#!/usr/bin/env python3
"""The staged candidate: the code the loop runs from outside bin is frozen with bin (B5-04, B5-08, B5-15, U3 item 1).

WHY. The loop ran scripts and plugin modules from the launch worktree it lands commits into, so the four freeze
receipts could PASS while RC ran different code than RB. deploy_stamped.stage_candidate copies into bin/candidate
exactly the static import closure of a closed entry list, computed by the freeze's own resolver, so the freeze that
walks bin now hashes that code too. A whole tree copy is not an option: measured on hub/main, 126 of 1374 files
refuse the freeze's static resolution.

HOW. Staged from THIS working tree (stage_candidate reads a directory) into a scratch bin that also holds the flat
loop tools, then frozen through the real CLI with the candidate roots. Refusals are driven with fixture sources, one
condition each. DeployCandidate reruns every deploy_stamped case on a fixture that ships a plugin runtime, through
the real deploy_stamped.sh entry point, so the deploy's own guards are exercised where they live.

Run: python3 -B scripts/test_candidate_stage.py
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LOOP = ROOT / "scripts/loop"
sys.path.insert(0, str(LOOP))
sys.path.insert(0, str(ROOT / "scripts"))
import deploy_stamped as D  # noqa: E402
import freeze_manifest as F  # noqa: E402
import tool_stamp  # noqa: E402
import test_deploy_stamped  # noqa: E402  (by module: its own class must not run twice here)

HOOK = '"""Scratch stand in for the machine installed session cap hook."""\nimport json\n'
ROLLUPS_NO_DATA = ("NO-DATA optional capability rollups unavailable: "
                   "duckdb is absent under the frozen interpreter")
WORK_INPUT = "land_apply.py:main=build-receipt-covers-new-modules"


def scratch(prefix):
    root = Path.home() / ".claude/brother-scratch"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(root)))


def fixture_source(box, extra=None):
    """A minimal source tree holding every candidate entry, one loop tool and the land_apply work input."""
    src = box / "source"
    for rel in D.CANDIDATE_ENTRIES:
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("VALUE = 1\n")
    (src / "scripts/loop").mkdir(parents=True, exist_ok=True)
    (src / "scripts/loop/tool.py").write_text("import json\n")
    (src / "scripts/loop/land_apply.py").write_text(
        "import importlib\ndef main(name):\n    return importlib.import_module(name)\n")
    for rel, text in (extra or {}).items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    return src


class StageThisTree(unittest.TestCase):
    """F-5: this working tree stages, and the bin holding it freezes with no NO-DATA line except rollups."""

    @classmethod
    def setUpClass(cls):
        cls.box = scratch("run-cand-")
        try:
            cls.home = cls.box / "home"
            (cls.home / ".claude/hooks").mkdir(parents=True)
            (cls.home / ".claude/hooks/bm_session_cap.py").write_text(HOOK)
            cls.bin = cls.box / "bin"
            cls.bin.mkdir()
            tool_stamp.copy_tools(str(LOOP), str(cls.bin))   # the deploy's own copier: files and packages alike
            cls.candidate = cls.bin / "candidate"
            with mock.patch.dict(os.environ, {"HOME": str(cls.home)}):
                D.stage_candidate(str(ROOT), str(cls.candidate))
            cls.configs = []
            for kind in ("cap", "burn", "registry", "roles", "intake"):
                p = cls.box / (kind + ".json")
                p.write_text("{}\n")
                cls.configs += ["--config", "%s=%s" % (kind, p)]
            # The executables the frozen router selects (Codex audit D1): scratch stand ins, named the way a launch
            # names them, with the candidate as the code root the proof runs under.
            cls.cli = {}
            for name in ("claude", "codex"):
                p = cls.box / "cli" / name
                p.parent.mkdir(exist_ok=True)
                p.write_text("#!/bin/sh\necho %s stand in\n" % name)
                p.chmod(0o755)
                cls.cli[name] = str(p)
            cls.manifest = cls.box / "freeze.json"
            cls.written = cls.freeze("write", cls.manifest)
        except BaseException:
            shutil.rmtree(str(cls.box), ignore_errors=True)
            raise

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.box), ignore_errors=True)

    @classmethod
    def freeze(cls, verb, out, *extra, relocated=True):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "PYTHON"))}
        env.update(HOME=str(cls.home), PYTHONDONTWRITEBYTECODE="1", BROTHER_CODE_ROOT=str(cls.candidate),
                   BROTHER_CLAUDE_BIN=cls.cli["claude"], BROTHER_CODEX_BIN=cls.cli["codex"])
        args = [sys.executable, "-B", str(cls.bin / "freeze_manifest.py"), verb, str(out)]
        if verb == "write":
            args += ["--bin", str(cls.bin), "--module-root", str(cls.candidate),
                     "--module-root", str(cls.candidate / "scripts"),
                     "--module-root", str(cls.home / ".claude/hooks"),
                     "--optional-import", "duckdb=rollups", "--work-input", WORK_INPUT] + cls.configs
            if relocated:
                args += ["--work-input=" + w for w in D.candidate_work_inputs(cls.candidate)]
        return subprocess.run(args + list(extra), env=env, capture_output=True, text=True, timeout=600)

    def staged(self):
        return sorted(str(p.relative_to(self.candidate)) for p in self.candidate.rglob("*") if p.is_file())

    def test_the_bin_holding_the_candidate_freezes_with_no_refusal(self):
        out = self.written.stdout + self.written.stderr
        self.assertEqual(self.written.returncode, 0, out)
        self.assertEqual([l for l in out.splitlines() if l.startswith("NO-DATA") and l != ROLLUPS_NO_DATA], [])
        self.assertEqual(json.loads(self.manifest.read_text())["model_executables"], self.cli)

    def test_no_test_file_is_staged(self):
        self.assertEqual([f for f in self.staged() if os.path.basename(f).startswith("test_")], [])

    def test_every_entry_is_staged_byte_for_byte(self):
        for rel in D.CANDIDATE_ENTRIES:
            with self.subTest(entry=rel):
                self.assertEqual((self.candidate / rel).read_bytes(), (ROOT / rel).read_bytes())

    def test_the_journal_the_recorder_imports_is_staged(self):
        self.assertIn("scripts/journal.py", self.staged())

    def test_every_staged_package_carries_its_initializers(self):
        for rel in self.staged():
            parent = Path(rel).parent
            while str(parent) not in ("", "."):
                if (ROOT / parent / "__init__.py").is_file():
                    self.assertTrue((self.candidate / parent / "__init__.py").is_file(), "%s for %s" % (parent, rel))
                parent = parent.parent

    def test_the_candidate_is_the_closure_not_the_tree(self):
        staged = self.staged()
        self.assertIn("scripts/loop/land_apply.py", staged)   # reached: native_worker imports it (2026-10-05)
        self.assertEqual(D.candidate_work_inputs(self.candidate), ["candidate/scripts/loop/" + WORK_INPUT])
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "scripts", "plugin/runtime"],
                                 capture_output=True, text=True).stdout.split()
        self.assertLess(len(staged), len([t for t in tracked if t.endswith(".py")]) // 4)

    def test_a_staged_relocated_work_input_without_its_declaration_refuses_the_freeze(self):
        relocated = self.candidate / "scripts/loop/land_apply.py"
        self.assertEqual(relocated.read_bytes(), (LOOP / "land_apply.py").read_bytes())
        r = self.freeze("write", self.box / "relocated.json", relocated=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("computed import has no frozen resolution in %s" % relocated, r.stdout)


class StageRefusals(unittest.TestCase):
    """Fixture sources, one condition each, through stage_candidate itself."""

    def setUp(self):
        self.box = scratch("run-cand-refuse-")
        self.addCleanup(shutil.rmtree, str(self.box), True)
        self.dest = self.box / "bin/candidate"

    def stage(self, src):
        with mock.patch.dict(os.environ, {"HOME": str(self.box / "home")}):
            D.stage_candidate(str(src), str(self.dest))

    def test_a_clean_fixture_stages_its_entries(self):
        self.stage(fixture_source(self.box))
        self.assertEqual(sorted(str(p.relative_to(self.dest)) for p in self.dest.rglob("*") if p.is_file()),
                         sorted(D.CANDIDATE_ENTRIES))
        self.assertEqual(D.candidate_work_inputs(self.dest), [])   # nothing staged, nothing declared

    def test_an_entry_importing_a_computed_loader_refuses_naming_it(self):
        src = fixture_source(self.box, {
            "scripts/probe_round.py": "import computed_mod\n",
            "scripts/computed_mod.py": "import importlib\nimportlib.import_module(NAME)\n"})
        with self.assertRaises(F.NoData) as caught:
            self.stage(src)
        self.assertIn(str(src / "scripts/computed_mod.py"), str(caught.exception))
        self.assertFalse(self.dest.exists())

    def test_a_missing_entry_refuses_naming_it(self):
        src = fixture_source(self.box)
        (src / "scripts/diag_round.py").unlink()
        with self.assertRaises(F.NoData) as caught:
            self.stage(src)
        self.assertIn("scripts/diag_round.py", str(caught.exception))
        self.assertFalse(self.dest.exists())

    def test_a_reached_work_input_is_staged_with_its_relocated_declaration(self):
        # 2026-10-05: native_worker reached land_apply and the stage refused every deploy; the relocated copy is
        # staged and declared for the freeze instead, which refuses it undeclared (StageThisTree above)
        self.stage(fixture_source(self.box, {"scripts/close_unit.py": "import land_apply\n"}))
        self.assertTrue((self.dest / "scripts/loop/land_apply.py").is_file())
        self.assertEqual(D.candidate_work_inputs(self.dest), ["candidate/scripts/loop/" + WORK_INPUT])

    def test_an_existing_destination_refuses(self):
        self.dest.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.stage(fixture_source(self.box))


class DeployCandidate(test_deploy_stamped.DeployStamped):
    """Every deploy_stamped case again, on a fixture revision that ships a plugin runtime and the candidate
    entries, through the real deploy_stamped.sh: the deploy stages bin/candidate from the committed revision."""

    def setUp(self):
        super().setUp()
        entries = {rel: "VALUE = 1\n" for rel in D.CANDIDATE_ENTRIES}
        entries["plugin/runtime/brother/core/or_fanout.py"] = "from . import helper\n"
        entries["plugin/runtime/brother/core/helper.py"] = "VALUE = 2\n"
        for rel, text in entries.items():
            (self.source / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.source / rel).write_text(text)
        self.commit("candidate entries")

    def commit(self, message):
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "commit", "--quiet", "-m", message)
        self.revision = self.git("rev-parse", "HEAD").stdout.strip()

    def test_the_deploy_publishes_the_committed_candidate(self):
        (self.source / "scripts/probe_round.py").write_text("UNCOMMITTED = 1\n")
        self.success(self.deploy())
        cand = self.target / "candidate"
        self.assertEqual((cand / "scripts/probe_round.py").read_text(), "VALUE = 1\n")
        self.assertEqual((cand / "plugin/runtime/brother/core/helper.py").read_text(), "VALUE = 2\n")
        self.assertFalse((cand / "scripts/loop/a.py").exists())
        stamp = json.loads((self.target / ".deploy-stamp.json").read_text())
        self.assertIn("bin/candidate/scripts/probe_round.py", stamp["files"])
        self.assertEqual([p for p in self.target.parent.glob("brother-deploys/*/source")], [])

    def test_a_rollback_whose_bin_snapshot_is_gone_refuses_by_name(self):
        # Pinned because a sweep of deploy_stamped.py lands its first mutation on this guard. The hash check
        # runs first, so the manifest is made to agree with a snapshot that lost its bin directory.
        self.success(self.deploy())
        backup = self.artifact()
        shutil.rmtree(str(backup / "files/bin"))
        manifest = json.loads((backup / "manifest.json").read_text())
        manifest["files"] = {k: v for k, v in manifest["files"].items() if not k.startswith("bin/")}
        (backup / "manifest.json").write_text(json.dumps(manifest))
        deployed = test_deploy_stamped.files(self.target)
        result = self.deploy("restore", backup)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rollback bin snapshot is missing", result.stdout + result.stderr)
        self.assertEqual(test_deploy_stamped.files(self.target), deployed)

    def test_a_candidate_refusal_refuses_the_deploy_and_keeps_the_target(self):
        (self.source / "scripts/probe_round.py").write_text("import importlib\nimportlib.import_module(NAME)\n")
        self.commit("a computed loader in an entry")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scripts/probe_round.py", result.stdout + result.stderr)
        self.unchanged()


if __name__ == "__main__":
    unittest.main()
