#!/usr/bin/env python3
"""C13: the deployed bin resolves its own code root from the deploy stamp, or refuses by name (model_router.code_root).

WHY. A hand run of `python3 ~/.claude/bin/native_worker.py refresh` from ~/Brother (an old branch) failed "no-answer
(AttributeError)": code_root() fell back to the cwd's checkout, whose brother_paths lacked claude_candidates (REGISTER
C13, measured 2026-10-17). The deployed bin carries .deploy-stamp.json beside its programs; the deploy now records the
source tree there, and code_root reads it before any cwd fallback. Every unreadable stamp REFUSES naming the folder to
run from; only a folder with no stamp at all behaves as before.

THE ENTRY POINT. Each case copies the real scripts/loop/model_router.py into a fixture bin, loads it from there under a
fresh module name, and calls code_root() from a cwd inside ANOTHER git repository: the shape of the hand run. One
condition per fixture: every stamp case starts from a good stamp and breaks one thing.
Run: python3 -B scripts/loop/test_code_root_from_bin.py"""
import contextlib, importlib.util, io, json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import model_router as R  # noqa: E402
import native_worker as NW  # noqa: E402

SCRIPTS = os.path.dirname(HERE)
if SCRIPTS not in sys.path:
    sys.path.insert(1, SCRIPTS)
import test_deploy_stamped as TDS  # noqa: E402  the deploy's own fixture, so the round trip runs the real deploy

KEYS = ("BROTHER_CODE_ROOT", "BROTHER_PROOF_PHASE", "BROTHER_CODEX_ROOT", "BROTHER_REPO_ROOT")
STAMP = ".deploy-stamp.json"


class Base(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="code-root-bin-")
        self.addCleanup(t.cleanup)
        box = os.path.realpath(t.name)
        self.bin = os.path.join(box, "bin")
        self.tree = os.path.join(box, "tree")       # the checkout the deploy came from
        self.other = os.path.join(box, "old")       # an unrelated checkout the hand run stands in
        for d in (self.bin, self.tree, self.other):
            os.makedirs(d)
        subprocess.run(["git", "init", "--quiet", self.other], check=True, capture_output=True)
        shutil.copy2(os.path.join(HERE, "model_router.py"), os.path.join(self.bin, "model_router.py"))
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.other)
        clean = {k: v for k, v in os.environ.items() if k not in KEYS}
        patch = mock.patch.dict(os.environ, clean, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def stamp(self, doc=None, raw=None):
        doc = {"schema": 1, "revision": "a" * 40, "source_dirty": False, "files": {}, "rollback": "/x",
               "source": self.tree} if doc is None else doc
        with open(os.path.join(self.bin, STAMP), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc) if raw is None else raw)

    def deployed(self):
        """model_router as the bin holds it, loaded from the bin's own file."""
        name = "model_router_bin_%d" % id(self)
        spec = importlib.util.spec_from_file_location(name, os.path.join(self.bin, "model_router.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def refused(self, mod, *words):
        with self.assertRaises(mod.Refused) as caught:
            mod.code_root()
        text = str(caught.exception)
        self.assertIn("run it from", text)
        for w in words:
            self.assertIn(w, text)
        return text


class FromTheBin(Base):
    def test_a_stamped_bin_resolves_to_the_stamp_tree(self):
        self.stamp()
        self.assertEqual(self.deployed().code_root(), self.tree)

    def test_a_missing_stamp_tree_refuses_by_name(self):
        gone = os.path.join(self.tree, "removed")
        self.stamp(dict(source=gone))
        self.refused(self.deployed(), gone)

    def test_the_environment_still_wins(self):
        self.stamp()
        os.environ["BROTHER_CODE_ROOT"] = self.other
        self.assertEqual(self.deployed().code_root(), self.other)

    def test_the_environment_still_wins_over_a_corrupt_stamp(self):
        self.stamp(raw="{torn")
        os.environ["BROTHER_CODE_ROOT"] = self.other
        self.assertEqual(self.deployed().code_root(), self.other)

    def test_a_proof_phase_still_refuses_before_the_stamp_is_read(self):
        self.stamp()
        os.environ["BROTHER_PROOF_PHASE"] = "RB"
        mod = self.deployed()
        with self.assertRaises(mod.Refused) as caught:
            mod.code_root()
        self.assertIn("BROTHER_CODE_ROOT is not set", str(caught.exception))

    def test_an_unstamped_folder_behaves_as_today(self):
        mod = self.deployed()
        self.assertEqual(mod.code_root(), mod.repo_root())
        self.assertEqual(mod.stamp_root(self.bin), "")

    def test_the_same_hand_run_gives_the_same_answer(self):
        self.stamp()
        mod = self.deployed()
        self.assertEqual([mod.code_root(), mod.code_root()], [self.tree, self.tree])

    def test_an_unknown_key_is_ignored(self):
        self.stamp(dict(source=self.tree, flavour="unknown", schema=99))
        self.assertEqual(self.deployed().code_root(), self.tree)


class TheStampRefuses(Base):
    """Every unreadable stamp refuses; it never falls back to the cwd's checkout."""

    def test_an_empty_stamp_refuses(self):
        self.stamp(raw="")
        self.refused(self.deployed(), self.bin)

    def test_a_stamp_that_is_not_json_refuses(self):
        self.stamp(raw="{\"source\": ")
        self.refused(self.deployed(), self.bin)

    def test_a_stamp_that_is_not_a_mapping_refuses(self):
        self.stamp(doc=[self.tree])
        self.refused(self.deployed(), self.bin)

    def test_a_stamp_with_no_tree_key_refuses(self):
        self.stamp(doc={"schema": 1, "revision": "a" * 40, "source_dirty": False, "files": {}, "rollback": "/x"})
        self.refused(self.deployed(), self.bin)

    def test_a_relative_tree_refuses(self):
        os.makedirs(os.path.join(self.other, "tree"))   # a relative name the cwd does hold: still never a code root
        self.stamp(dict(source="tree"))
        self.refused(self.deployed(), "'tree'")

    def test_a_tree_that_is_a_file_refuses(self):
        f = os.path.join(self.tree, "file")
        open(f, "w").close()
        self.stamp(dict(source=f))
        self.refused(self.deployed(), f)

    def test_a_symlink_loop_refuses(self):
        loop = os.path.join(self.tree, "loop")
        os.symlink(loop, loop)
        self.stamp(dict(source=loop))
        self.refused(self.deployed(), loop)

    def test_a_tree_holding_a_nul_refuses(self):
        self.stamp(dict(source=self.tree + "\0x"))
        self.refused(self.deployed())

    def test_a_tree_that_is_not_a_string_refuses(self):
        self.stamp(dict(source=["/"]))
        self.refused(self.deployed())

    def test_a_dangling_stamp_link_refuses(self):
        os.symlink(os.path.join(self.bin, "nowhere.json"), os.path.join(self.bin, STAMP))
        self.refused(self.deployed(), self.bin)

    def test_a_stamp_that_cannot_be_opened_refuses(self):
        os.makedirs(os.path.join(self.bin, STAMP))   # present, and no open() can read it
        self.refused(self.deployed(), self.bin)


class Remedy(unittest.TestCase):
    def test_the_remedy_names_the_folder(self):
        folder = "/some/checkout"
        claude = R.refresh_remedy("claude", folder)
        self.assertEqual(claude, "python3 /some/checkout/scripts/loop/native_worker.py refresh")
        codex = R.refresh_remedy("codex", folder)
        self.assertTrue(codex.startswith("codex login"), codex)
        self.assertIn(folder, codex)

    def test_an_unknown_transport_or_a_relative_folder_refuses(self):
        with self.assertRaises(R.Refused):
            R.refresh_remedy("bridge", "/some/checkout")
        with self.assertRaises(R.Refused):
            R.refresh_remedy("claude", "some/checkout")

    def test_the_seat_hint_names_the_code_root_for_each_pin(self):
        with mock.patch.object(NW.R, "code_root", lambda: "/the/tree"):
            self.assertIn("python3 /the/tree/scripts/loop/native_worker.py refresh", NW.refresh_hint())
            codex = NW.refresh_hint("codex")
            self.assertTrue(codex.startswith("a Codex pin needs this machine's Codex login: codex login"), codex)
            self.assertIn("/the/tree", codex)

    def test_the_seat_hint_names_the_refusal_when_no_code_root_can_be_named(self):
        def refuse():
            raise R.Refused("the deploy stamp /b/.deploy-stamp.json names '/gone': run it from /gone")
        with mock.patch.object(NW.R, "code_root", refuse):
            self.assertIn("run it from /gone", NW.refresh_hint())

    def test_a_failed_refresh_prints_the_remedy(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(NW, "refresh", lambda: ("FAILED", "login")), \
                mock.patch.object(NW.R, "code_root", lambda: "/the/tree"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = NW.main(["refresh"])
        self.assertEqual((rc, out.getvalue().strip()), (1, "REFRESH FAILED login"))   # the driver's one line, unchanged
        self.assertIn("python3 /the/tree/scripts/loop/native_worker.py refresh", err.getvalue())


class TheDeployRecordsTheTree(unittest.TestCase):
    def test_a_real_deploy_stamps_the_tree_code_root_reads(self):
        fx = TDS.DeployStamped("test_stamp_records_revision_and_every_deployed_hash")
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        fx.success(fx.deploy())
        self.assertEqual(R.stamp_root(str(fx.target)), str(fx.source.resolve()))


if __name__ == "__main__":
    unittest.main()
