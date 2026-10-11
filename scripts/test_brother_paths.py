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
        self.assertEqual(brother_paths.client({"BROTHER_CLIENT": "notepad"}), "")

    def test_explicit_cursor_override(self):
        self.assertEqual(brother_paths.client({"BROTHER_CLIENT": "cursor"}),
                         "cursor")

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

    def test_cursor_client_uses_dot_cursor_and_ignores_codex_home(self):
        self.assertEqual(
            brother_paths.config_dir({"BROTHER_CLIENT": "cursor"}),
            os.path.join(os.path.expanduser("~"), ".cursor"))
        self.assertEqual(
            brother_paths.config_dir({"BROTHER_CLIENT": "cursor",
                                      "CODEX_HOME": "/tmp/codexhome"}),
            os.path.join(os.path.expanduser("~"), ".cursor"))

    def test_unknown_client_still_honours_codex_home(self):
        """An unknown client kept honouring CODEX_HOME before Cursor support,
        and must keep doing so; only Claude and Cursor ignore it."""
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            env = {"BROTHER_PLUGIN_ROOT": root, "CODEX_HOME": "/tmp/codexhome"}
            self.assertEqual(brother_paths.config_dir(env), "/tmp/codexhome")

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


class CodexBinary(unittest.TestCase):
    """ACC5, 2026-09-26: six tools each declared their own copy of the app's
    Codex path, the app moved the binary, and every copy went stale at once
    (virgin-unit-proof printed "no executable Codex binary" on every gate run).
    One resolver, newest app location first, never PATH."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="codex-bin-test-")
        self.new = os.path.join(self.tmp, "new", "codex")
        self.old = os.path.join(self.tmp, "old", "codex")
        self.saved = brother_paths.CODEX_APP_BINS
        brother_paths.CODEX_APP_BINS = (self.new, self.old)

    def tearDown(self):
        import shutil
        brother_paths.CODEX_APP_BINS = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, path, executable=True):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, 0o755 if executable else 0o644)

    def test_the_override_wins(self):
        self.make(self.new)
        self.assertEqual(brother_paths.codex_bin({"BROTHER_CODEX_BIN": "/opt/codex"}), "/opt/codex")

    def test_the_newest_app_location_comes_first(self):
        self.make(self.new)
        self.make(self.old)
        self.assertEqual(brother_paths.codex_bin({}), self.new)

    def test_the_old_location_still_works_alone(self):
        self.make(self.old)
        self.assertEqual(brother_paths.codex_bin({}), self.old)

    def test_a_file_that_cannot_run_is_skipped(self):
        self.make(self.new, executable=False)
        self.make(self.old)
        self.assertEqual(brother_paths.codex_bin({}), self.old)

    def test_nothing_installed_names_where_it_is_expected_now(self):
        self.assertEqual(brother_paths.codex_bin({}), self.new)

    def test_never_the_codex_on_path(self):
        shim_dir = os.path.join(self.tmp, "path")
        self.make(os.path.join(shim_dir, "codex"))
        self.assertEqual(brother_paths.codex_bin({"PATH": shim_dir}), self.new)

    def test_no_other_script_declares_its_own_app_path(self):
        """The caller count lives here: a sibling that re-declares the literal
        path goes stale on the next app move, exactly as six did."""
        literal = '"/Applications/ChatGPT.app/Contents/Resources/codex"'
        offenders = []
        for root, _dirs, files in os.walk(HERE):
            for name in files:
                if not name.endswith(".py") or name.startswith("test_") or name == "brother_paths.py":
                    continue
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as fh:
                    for number, line in enumerate(fh, 1):
                        code = line.split("#", 1)[0]
                        if literal in code:
                            offenders.append("%s:%d" % (os.path.relpath(path, REPO), number))
        self.assertEqual(offenders, [], "route these through brother_paths.codex_bin()")


class ClaudeDesktopBinary(unittest.TestCase):
    """2026-10-03: the desktop app updated 2.1.284 to 2.1.286 and now nests each version's binary one directory deeper
    (<version>/<build hash>/claude.app/...). claude_candidates scanned only <version>/claude.app/..., found nothing,
    and the loop fell back to an old npm CLI that answered unrecognized_model, so every restart was refused."""

    def setUp(self):
        import tempfile
        self.home = tempfile.mkdtemp(prefix="claude-bin-test-")
        self.root = os.path.join(self.home, "Library", "Application Support", "Claude", "claude-code")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def make(self, *parts):
        path = os.path.join(self.root, *parts, "claude.app", "Contents", "MacOS", "claude")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return os.path.realpath(path)

    def env(self):
        return {"HOME": self.home, "PATH": ""}

    def test_the_nested_layout_is_found(self):
        nested = self.make("2.1.286", "f2326db61802")
        self.assertIn(nested, brother_paths.claude_candidates(self.env()))

    def test_both_layouts_side_by_side(self):
        flat = self.make("2.1.284")
        nested = self.make("2.1.286", "f2326db61802")
        got = brother_paths.claude_candidates(self.env())
        self.assertIn(flat, got)
        self.assertIn(nested, got)

    def test_a_non_executable_or_missing_bundle_is_not_a_candidate(self):
        os.makedirs(os.path.join(self.root, "2.1.290", "abc", "claude.app"))
        self.assertEqual([p for p in brother_paths.claude_candidates(self.env()) if "2.1.290" in p], [])


class ClaudeForCheck(unittest.TestCase):
    """C10, 2026-10-10: the merge gate pins PATH to the system directories, so the plugin-manifest row's
    `command -v claude` read NO-DATA there. claude_for_check resolves the CLI without the caller's PATH: the owner's
    pin, then the proven program, then the installed copies. Each case isolates one condition."""

    def setUp(self):
        import tempfile
        self.home = tempfile.mkdtemp(prefix="claude-for-check-")
        self.other = tempfile.mkdtemp(prefix="claude-for-check-bin-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.other, ignore_errors=True)

    def program(self, directory, mode=0o755):
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "claude")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, mode)
        return path

    def env(self, **extra):
        env = {"HOME": self.home, "PATH": ""}
        env.update(extra)
        return env

    def premise(self):
        """The absolute package directories (/opt/homebrew/bin, /usr/local/bin) are this machine's: a CLI there is
        found whatever the fixture HOME says, so the nothing-installed premise cannot be built here."""
        found = brother_paths.claude_candidates(self.env())
        if found:
            self.skipTest("NO-DATA: a Claude Code CLI is installed outside the fixture at %s" % found[0])

    def test_the_owners_pin_is_used_when_it_is_an_absolute_executable(self):
        pin = self.program(self.other)
        self.assertEqual(brother_paths.claude_for_check(self.env(BROTHER_CLAUDE_BIN=pin)), (pin, None))

    def test_a_relative_pin_is_refused_and_names_the_variable(self):
        path, reason = brother_paths.claude_for_check(self.env(BROTHER_CLAUDE_BIN="claude"))
        self.assertIsNone(path)
        self.assertIn("BROTHER_CLAUDE_BIN", reason)

    def test_a_pin_that_is_not_executable_is_refused(self):
        pin = self.program(self.other, mode=0o644)
        path, reason = brother_paths.claude_for_check(self.env(BROTHER_CLAUDE_BIN=pin))
        self.assertIsNone(path)
        self.assertIn("not an absolute path to an executable file", reason)

    def test_a_refused_pin_never_switches_to_an_installed_copy(self):
        self.program(os.path.join(self.home, ".local", "bin"))
        path, reason = brother_paths.claude_for_check(self.env(BROTHER_CLAUDE_BIN="relative/claude"))
        self.assertIsNone(path)
        self.assertIn("BROTHER_CLAUDE_BIN", reason)

    def test_with_no_pin_an_installed_copy_under_home_is_found_without_path(self):
        installed = os.path.realpath(self.program(os.path.join(self.home, ".local", "bin")))
        path, reason = brother_paths.claude_for_check(self.env())
        self.assertIsNone(reason)
        self.assertEqual(os.path.realpath(path), installed)

    def test_nothing_installed_is_a_reason_never_a_path(self):
        self.premise()
        path, reason = brother_paths.claude_for_check(self.env())
        self.assertIsNone(path)
        self.assertIn("no Claude Code CLI", reason)

    def test_the_command_line_prints_the_path_or_a_no_data_line(self):
        import subprocess
        script = os.path.join(HERE, "brother_paths.py")
        pin = self.program(self.other)
        ok = subprocess.run([sys.executable, "-B", script, "--claude-for-check"], capture_output=True, text=True,
                            env=self.env(BROTHER_CLAUDE_BIN=pin), timeout=30)
        self.assertEqual((ok.returncode, ok.stdout.strip()), (0, pin))
        bad = subprocess.run([sys.executable, "-B", script, "--claude-for-check"], capture_output=True, text=True,
                             env=self.env(BROTHER_CLAUDE_BIN="claude"), timeout=30)
        self.assertEqual(bad.returncode, 2)
        self.assertTrue(bad.stdout.startswith("NO-DATA: "), bad.stdout)


class CopiesDoNotDrift(unittest.TestCase):
    SOURCE = os.path.join(HERE, "brother_paths.py")

    #: Names the brothersbe copy is documented to add beyond the source
    #: (commit 4de7547b1): a line-breaking regex plus the two functions it
    #: feeds. Read off the copy itself, not assumed: it is three names, not
    #: the two the commit message calls out by function. Since 2026-10-11 the
    #: source carries _LINE_BREAKS and one_line too, byte for byte the same
    #: (the assertion below compares every shared name), because its
    #: --claude-for-check lines print through one_line under BrotherMode's
    #: print choke point; say() remains the copy's only extra name.
    DOCUMENTED_EXTRA_NAMES = frozenset({"say"})

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
