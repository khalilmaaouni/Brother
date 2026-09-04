#!/usr/bin/env python3
"""C3: brother_paths driven backwards, plus the drift check on its copies.

DRIVEN BACKWARDS is the point, not coverage. The estate's recorded lesson is
that a control nobody drove backwards is a claim: the interesting question is
not "does config_dir() return ~/.claude on this machine" (it did before this
module existed) but "with EVERY variable unset, does the client read NO-DATA,
and does nothing outside the repository get touched". Both are asserted here
against an environment scrubbed of every marker, never against os.environ as
this session happens to hold it.

THE COPIES. brother_paths.py is one source (scripts/) with two product copies
(products/brothermode/tools/, products/brothersbe/tools/) so a product's tools
import it with no hub checkout present, and one bundle mirror written by
scripts/bundle_runtime.py. Three hand-maintained copies drift, so the drift is
checked here by sha256 rather than by anybody remembering. The bundle mirror is
NOT checked here: bundle_runtime.py --check already owns that comparison and a
second opinion about the same bytes is how two checks disagree.
"""

import hashlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import brother_paths  # noqa: E402

#: Every copy that must equal scripts/brother_paths.py byte for byte.
COPIES = ("products/brothermode/tools/brother_paths.py",
          "products/brothersbe/tools/brother_paths.py")

#: An environment with nothing in it. Passed explicitly so no test result
#: depends on what the session running it happens to export.
EMPTY = {}


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class ClientIdentification(unittest.TestCase):
    def test_empty_environment_reads_no_data(self):
        """The backwards drive: nothing set, nothing guessed. scripts/ is not
        a plugin package, so the manifest rung finds neither manifest and the
        answer is "" rather than a confident wrong client."""
        self.assertEqual(brother_paths.client(EMPTY), "")

    def test_explicit_override_wins_over_every_marker(self):
        env = {"BROTHER_CLIENT": "codex", "CLAUDECODE": "1"}
        self.assertEqual(brother_paths.client(env), "codex")

    def test_unrecognised_override_is_ignored_not_trusted(self):
        self.assertEqual(brother_paths.client({"BROTHER_CLIENT": "cursor"}), "")

    def test_claude_marker_identifies_claude(self):
        self.assertEqual(brother_paths.client({"CLAUDECODE": "1"}), "claude")

    def test_codex_marker_identifies_codex(self):
        self.assertEqual(brother_paths.client({"CODEX_THREAD_ID": "t_1"}),
                         "codex")

    def test_a_non_string_value_degrades_rather_than_raising(self):
        """A hook must not die because something put an int in the mapping."""
        self.assertEqual(brother_paths.client({"BROTHER_CLIENT": 5}), "")

    def test_manifest_beside_the_plugin_root_identifies_the_client(self):
        import tempfile
        for name, rel in brother_paths.CLIENT_MANIFEST.items():
            with tempfile.TemporaryDirectory() as tmp:
                target = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(target))
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write("{}")
                self.assertEqual(
                    brother_paths.client({"BROTHER_PLUGIN_ROOT": tmp}), name)

    def test_both_manifests_present_is_ambiguous_and_reads_no_data(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for rel in brother_paths.CLIENT_MANIFEST.values():
                target = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(target))
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write("{}")
            self.assertEqual(brother_paths.client({"BROTHER_PLUGIN_ROOT": tmp}),
                             "")


class PluginRoot(unittest.TestCase):
    def test_rung_order(self):
        env = {"BROTHER_PLUGIN_ROOT": "/a", "CLAUDE_PLUGIN_ROOT": "/b",
               "PLUGIN_ROOT": "/c"}
        self.assertEqual(brother_paths.plugin_root(env), "/a")
        del env["BROTHER_PLUGIN_ROOT"]
        self.assertEqual(brother_paths.plugin_root(env), "/b")
        del env["CLAUDE_PLUGIN_ROOT"]
        self.assertEqual(brother_paths.plugin_root(env), "/c")

    def test_empty_environment_falls_back_to_this_package(self):
        """The backwards drive for paths: with nothing set, the answer is this
        checkout, and nothing outside the repository is named."""
        root = brother_paths.plugin_root(EMPTY)
        self.assertEqual(root, REPO)
        self.assertTrue(os.path.isdir(root))


class ConfigDir(unittest.TestCase):
    def test_claude_behaviour_is_unchanged(self):
        """The hard requirement of this whole row. A Claude session resolves
        exactly what every call site typed literally before: CLAUDE_CONFIG_DIR
        when set, else ~/.claude."""
        self.assertEqual(brother_paths.config_dir({"CLAUDECODE": "1"}),
                         os.path.join(os.path.expanduser("~"), ".claude"))
        self.assertEqual(
            brother_paths.config_dir({"CLAUDECODE": "1",
                                      "CLAUDE_CONFIG_DIR": "/tmp/cfg"}),
            "/tmp/cfg")

    def test_codex_home_never_moves_a_claude_store(self):
        """The deliberate deviation from the brief's literal ordering, pinned:
        a Claude session that happens to export CODEX_HOME keeps ~/.claude."""
        env = {"CLAUDECODE": "1", "CODEX_HOME": "/tmp/codexhome"}
        self.assertEqual(brother_paths.config_dir(env),
                         os.path.join(os.path.expanduser("~"), ".claude"))

    def test_codex_client_uses_codex_home_then_dot_codex(self):
        self.assertEqual(
            brother_paths.config_dir({"BROTHER_CLIENT": "codex",
                                      "CODEX_HOME": "/tmp/codexhome"}),
            "/tmp/codexhome")
        self.assertEqual(
            brother_paths.config_dir({"BROTHER_CLIENT": "codex"}),
            os.path.join(os.path.expanduser("~"), ".codex"))

    def test_brother_override_wins_everywhere(self):
        env = {"BROTHER_CONFIG_DIR": "/tmp/brother", "CLAUDE_CONFIG_DIR": "/x",
               "CODEX_HOME": "/y", "CLAUDECODE": "1"}
        self.assertEqual(brother_paths.config_dir(env), "/tmp/brother")

    def test_unknown_client_keeps_the_claude_default(self):
        """NO-DATA on the client must never relocate a store: an unknown host
        gets the pre-existing directory, and says so through client()."""
        self.assertEqual(brother_paths.client(EMPTY), "")
        self.assertEqual(brother_paths.config_dir(EMPTY),
                         os.path.join(os.path.expanduser("~"), ".claude"))

    def test_config_path_joins(self):
        self.assertEqual(
            brother_paths.config_path("bm_vault.json",
                                      env={"BROTHER_CONFIG_DIR": "/tmp/b"}),
            "/tmp/b/bm_vault.json")


class Describe(unittest.TestCase):
    def test_describe_reports_no_data_as_a_word(self):
        facts = brother_paths.describe(EMPTY)
        self.assertEqual(facts["client"], "NO-DATA")
        self.assertEqual(facts["plugin_root"], REPO)


class CopiesDoNotDrift(unittest.TestCase):
    def test_every_product_copy_is_byte_identical_to_the_source(self):
        source = os.path.join(HERE, "brother_paths.py")
        want = _sha256(source)
        for rel in COPIES:
            path = os.path.join(REPO, rel)
            self.assertTrue(os.path.isfile(path), "missing copy: %s" % rel)
            self.assertEqual(_sha256(path), want,
                             "%s has drifted from scripts/brother_paths.py; "
                             "copy the source over it rather than editing the "
                             "copy" % rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
