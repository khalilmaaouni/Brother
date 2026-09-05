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
scripts/bundle_runtime.py. The bundle mirror is NOT checked here:
bundle_runtime.py --check already owns that comparison and a second opinion
about the same bytes is how two checks disagree.

THE BROTHERMODE COPY stays byte identical to the source, checked by sha256.

THE BROTHERSBE COPY is a deliberate SUPERSET as of commit 4de7547b1 ("The
honesty lint reads the C3 files"): it adds _LINE_BREAKS/one_line/say so the
sbe fence hook's output cannot carry a forged line, and it routes main()'s
two print() calls through say() to actually use them, which is why main() is
the one shared function this test permits to differ. A sha256 comparison
cannot express "identical except for a documented superset", so this copy is
compared at the AST level instead: every top-level function and constant the
source defines must exist in the copy with identical source text, except the
one documented divergent shared name, and the copy's extra top-level names
must be exactly the documented set. Both directions are driven backwards with
a temp copy so an undocumented change in either direction still fails.
"""

import ast
import hashlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import brother_paths  # noqa: E402

#: The copy that must equal scripts/brother_paths.py byte for byte.
BROTHERMODE_COPY = os.path.join(REPO, "products/brothermode/tools/brother_paths.py")

#: The copy that is a documented superset, checked at the AST level below.
BROTHERSBE_COPY = os.path.join(REPO, "products/brothersbe/tools/brother_paths.py")

#: An environment with nothing in it. Passed explicitly so no test result
#: depends on what the session running it happens to export.
EMPTY = {}


def _top_level_defs(path):
    """Every top-level function and simple assignment in `path`, name ->
    exact source text (ast.get_source_segment, so comments and formatting
    inside the def are part of the identity check, not just the AST shape).
    """
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = ast.get_source_segment(source, node)
        elif isinstance(node, ast.Assign):
            segment = ast.get_source_segment(source, node)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = segment
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = ast.get_source_segment(source, node)
    return out


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

    def test_a_codex_turn_inside_a_claude_session_is_codex(self):
        """Measured 2026-09-05 inside a real `codex exec` turn started from a
        Claude Code session: Codex exports CODEX_SESSION_ID, CODEX_THREAD_ID
        and CODEX_SANDBOX, the turn inherits the session's CLAUDECODE, and
        with Claude's markers read first this answered 'claude' inside Codex.
        Codex's markers are per turn, CLAUDECODE is per session, so the
        nearer host wins."""
        env = {"CLAUDECODE": "1", "CODEX_SESSION_ID": "s_1",
               "CODEX_THREAD_ID": "t_1", "CODEX_SANDBOX": "seatbelt"}
        self.assertEqual(brother_paths.client(env), "codex")

    def test_a_claude_session_that_merely_exports_codex_home_is_claude(self):
        """The other direction, and why CODEX_HOME is not a marker: people
        leave it in a shell profile."""
        self.assertEqual(
            brother_paths.client({"CLAUDECODE": "1",
                                  "CODEX_HOME": "/somewhere"}), "claude")

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
    SOURCE = os.path.join(HERE, "brother_paths.py")

    #: Names the brothersbe copy is documented to add beyond the source
    #: (commit 4de7547b1): a line-breaking regex plus the two functions it
    #: feeds. Read off the copy itself, not assumed: it is three names, not
    #: the two the commit message calls out by function.
    DOCUMENTED_EXTRA_NAMES = frozenset({"_LINE_BREAKS", "one_line", "say"})

    #: Shared names the brothersbe copy is documented to have MODIFIED rather
    #: than merely left alone. main() is the only one: it routes its two
    #: print() calls through say()/one_line() so CLI output cannot carry a
    #: forged line (commit 4de7547b1). Every other shared name must stay
    #: identical; this set exists so that guarantee is explicit rather than
    #: silently widened.
    DOCUMENTED_DIVERGENT_SHARED = frozenset({"main"})

    def test_brothermode_copy_is_byte_identical_to_the_source(self):
        want = _sha256(self.SOURCE)
        self.assertTrue(os.path.isfile(BROTHERMODE_COPY),
                         "missing copy: %s" % BROTHERMODE_COPY)
        self.assertEqual(_sha256(BROTHERMODE_COPY), want,
                         "products/brothermode/tools/brother_paths.py has "
                         "drifted from scripts/brother_paths.py; copy the "
                         "source over it rather than editing the copy")

    def test_brothersbe_copy_is_a_documented_superset(self):
        self._assert_documented_superset(self.SOURCE, BROTHERSBE_COPY)

    def test_the_documented_main_divergence_is_pinned(self):
        """The one shared name allowed to differ is pinned to its known
        shape, so a further, undocumented change to main() in either file
        still fails until this pin is updated by hand alongside the reason.
        """
        source_main = _top_level_defs(self.SOURCE)["main"]
        copy_main = _top_level_defs(BROTHERSBE_COPY)["main"]
        self.assertNotEqual(
            source_main, copy_main,
            "main() no longer differs between the two files; drop it from "
            "DOCUMENTED_DIVERGENT_SHARED so drift in it is caught again")
        self.assertIn("print(", source_main)
        self.assertIn("say(", copy_main)
        self.assertNotIn("say(", source_main)

    def test_a_drifted_shared_function_is_caught(self):
        """Driven backwards: a shared function (not main) mutated in a temp
        copy must fail the superset check."""
        import tempfile
        with open(BROTHERSBE_COPY, encoding="utf-8") as fh:
            text = fh.read()
        mutated = text.replace(
            "def client(env=None):", "def client(env=None):\n    pass", 1)
        self.assertNotEqual(text, mutated, "fixture found nothing to mutate")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "brother_paths.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(mutated)
            with self.assertRaises(AssertionError):
                self._assert_documented_superset(self.SOURCE, path)

    def test_an_undocumented_extra_name_is_caught(self):
        """Driven backwards: a name the commit never documented must fail
        the superset check even though it is merely additive."""
        import tempfile
        with open(BROTHERSBE_COPY, encoding="utf-8") as fh:
            text = fh.read()
        mutated = text + "\n\ndef undocumented_helper():\n    return 1\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "brother_paths.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(mutated)
            with self.assertRaises(AssertionError):
                self._assert_documented_superset(self.SOURCE, path)

    def test_a_dropped_shared_name_is_caught(self):
        """Driven backwards: the copy must not be allowed to quietly drop
        something the source still defines."""
        import tempfile
        with open(BROTHERSBE_COPY, encoding="utf-8") as fh:
            lines = fh.readlines()
        mutated = "".join(line for line in lines
                          if "def config_path(" not in line)
        self.assertNotEqual("".join(lines), mutated,
                            "fixture found nothing to drop")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "brother_paths.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(mutated)
            with self.assertRaises(AssertionError):
                self._assert_documented_superset(self.SOURCE, path)

    def _assert_documented_superset(self, source_path, copy_path):
        source_defs = _top_level_defs(source_path)
        copy_defs = _top_level_defs(copy_path)
        source_names = set(source_defs)
        copy_names = set(copy_defs)

        missing = source_names - copy_names
        self.assertEqual(missing, set(),
                         "%s dropped names the source still defines: %s" %
                         (copy_path, sorted(missing)))

        extra = copy_names - source_names
        self.assertEqual(extra, self.DOCUMENTED_EXTRA_NAMES,
                         "%s's extra names no longer match the documented "
                         "set; name the real set: %s" %
                         (copy_path, sorted(extra)))

        for name in sorted(source_names):
            if name in self.DOCUMENTED_DIVERGENT_SHARED:
                continue
            self.assertEqual(copy_defs[name], source_defs[name],
                             "%s has drifted in %s; copy the source over it "
                             "or document the divergence" % (name, copy_path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
