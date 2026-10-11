"""test_charter_paths: drives scripts/charter_paths.py BACKWARDS.

The checker exists because a green check told nobody the charter's architecture
of record was missing from the tree (row E47). A checker that has only ever been
seen green is the same class of claim, so this drives all four verdicts over
temporary fixture trees, including the exact E47 shape: a charter naming a
record the tree does not hold must come back FAIL.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "charter_paths.py")
sys.path.insert(0, HERE)
import charter_paths  # noqa: E402

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


def run(root, charter=None):
    cmd = [sys.executable, SCRIPT, "--root", root]
    if charter is not None:
        cmd += ["--charter", charter]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout + proc.stderr


class Fixture(unittest.TestCase):
    """A throwaway tree with a charter written for the case under test."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="charter-paths-fixture-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "docs"))
        # N5.a: the charter check also asks whether each named path reaches
        # the public export, so every fixture carries an allowlist that holds
        # `docs`, which keeps each existing test's meaning.
        self.write_allowlist(["docs"])

    def write_allowlist(self, entries):
        """Rewrite the fixture's export allowlist, one entry per line."""
        self._write_plan("EXPORT-ALLOWLIST.txt", entries)

    def write_denylist(self, entries):
        """Write the fixture's export denylist, one entry per line."""
        self._write_plan("EXPORT-DENYLIST.txt", entries)

    def remove_allowlist(self):
        os.remove(os.path.join(self.root, "docs", "plan",
                               "EXPORT-ALLOWLIST.txt"))

    def _write_plan(self, name, lines):
        plan = os.path.join(self.root, "docs", "plan")
        os.makedirs(plan, exist_ok=True)
        with open(os.path.join(plan, name), "w", encoding="utf-8") as fh:
            fh.write("".join(line + "\n" for line in lines))

    def write_charter(self, tokens):
        """tokens: what the fixture charter names in backticks."""
        body = "# fixture\n\n" + "\n".join("names `%s` here" % t for t in tokens)
        path = os.path.join(self.root, "docs", "CHARTER.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        return path

    def touch(self, rel):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("fixture\n")


class CharterPaths(Fixture):
    EXEMPT = sorted(charter_paths.GENERIC)
    RECORD = os.path.join("docs", "plan",
                          "ADR-2026-08-23-one-brother-repository.md")

    def test_pass_when_every_named_path_exists(self):
        self.touch(self.RECORD)
        self.write_charter(self.EXEMPT + [self.RECORD])
        code, out = run(self.root)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)

    def test_fail_on_the_e47_shape_a_record_that_is_not_in_the_tree(self):
        # No touch: the charter names the architecture of record and the tree
        # does not hold it. This is the state row E47 was opened for.
        self.write_charter(self.EXEMPT + [self.RECORD])
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL", out)
        self.assertIn("ADR-2026-08-23-one-brother-repository.md", out)

    def test_fail_when_an_exemption_outlives_the_sentence_that_earned_it(self):
        self.touch(self.RECORD)
        self.write_charter(self.EXEMPT[1:] + [self.RECORD])
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("stale exemption", out)
        self.assertIn(self.EXEMPT[0], out)

    def test_no_data_when_the_charter_cannot_be_read(self):
        code, out = run(self.root)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("PASS", out)

    def test_no_data_when_the_charter_names_no_path_at_all(self):
        self.write_charter(["nothing", "path-shaped"])
        code, out = run(self.root)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_a_quoted_command_line_is_not_read_as_a_path(self):
        self.assertFalse(charter_paths.path_shaped(
            "/usr/bin/python3 -m unittest tests/test_surface.py"))
        self.assertTrue(charter_paths.path_shaped("tests/test_surface.py"))

    def test_the_real_charter_passes_in_this_repository(self):
        repo = os.path.dirname(HERE)
        code, out = run(repo)
        self.assertEqual(code, 0, out)


class NamedButNotExported(Fixture):
    """N5.a: a path the charter names must also reach the public export."""

    EXEMPT = sorted(charter_paths.GENERIC)
    SPEC = os.path.join("docs", "plan", "specs", "n5.md")
    RECORD = os.path.join("docs", "plan",
                          "ADR-2026-08-23-one-brother-repository.md")

    def charter_with(self, *tokens):
        return self.write_charter(self.EXEMPT + list(tokens))

    def test_a_path_the_allowlist_does_not_carry_fails(self):
        self.touch(self.SPEC)
        self.charter_with(self.SPEC)
        self.write_allowlist([os.path.join("docs", "CHARTER.md")])
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn(self.SPEC, out)
        self.assertIn("export", out)
        self.assertIn("1 not exported", out)

    def test_a_parent_directory_entry_carries_the_path(self):
        self.touch(self.SPEC)
        self.charter_with(self.SPEC)
        code, out = run(self.root)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)

    def test_a_directory_token_is_carried_by_an_entry_under_it(self):
        self.touch(os.path.join(".github", "workflows", "virgin-install.yml"))
        self.charter_with(".github/workflows/")
        self.write_allowlist(["docs", ".github/workflows/virgin-install.yml"])
        code, out = run(self.root)
        self.assertEqual(code, 0, out)

    def test_the_denylist_withholds_a_carried_path(self):
        self.touch(self.SPEC)
        self.charter_with(self.SPEC)
        self.write_denylist([self.SPEC])
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn(self.SPEC, out)

    def test_a_shared_prefix_is_not_a_directory_boundary(self):
        self.touch(self.RECORD)
        self.charter_with(self.RECORD)
        self.write_allowlist(["docs/plan/ADR-2026-08-23-one-brother-repository"])
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("not exported", out)

    def test_a_dot_dot_token_is_normalised_before_matching(self):
        self.touch(os.path.join("scripts", "x.py"))
        self.charter_with("docs/../scripts/x.py")
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("docs/../scripts/x.py", out)

    def test_a_hard_excluded_entry_never_carries_a_path(self):
        self.touch(os.path.join("editions", "x.md"))
        self.charter_with("editions/x.md")
        self.write_allowlist(["editions"])
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("editions/x.md", out)

    def test_matching_is_case_sensitive(self):
        self.touch(self.SPEC)
        self.charter_with(self.SPEC)
        self.write_allowlist(["Docs"])
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn(self.SPEC, out)

    def test_no_allowlist_file_is_no_data(self):
        self.touch(self.SPEC)
        self.charter_with(self.SPEC)
        self.remove_allowlist()
        code, out = run(self.root)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("PASS", out)

    def test_a_comment_only_allowlist_is_no_data(self):
        self.touch(self.SPEC)
        self.charter_with(self.SPEC)
        self.write_allowlist(["# only a comment", "", "# and another"])
        code, out = run(self.root)
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)
        self.assertNotIn("PASS", out)

    def test_a_fail_outranks_no_data(self):
        self.charter_with(self.SPEC)  # named, never touched: missing
        self.remove_allowlist()
        code, out = run(self.root)
        self.assertEqual(code, 1, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("1 missing", out)

    def test_export_rules_reads_the_fixture(self):
        self.write_denylist(["docs/secret.md"])
        allow, deny, why = charter_paths.export_rules(self.root)
        self.assertEqual((allow, deny, why), (["docs"], ["docs/secret.md"], ""))
        self.remove_allowlist()
        allow, _, why = charter_paths.export_rules(self.root)
        self.assertIsNone(allow)
        self.assertIn("no allowlist file under", why)

    def test_exported_edges(self):
        ex = charter_paths.exported
        hard = {"editions", ".brother-edition"}
        for tok in ("/", "//", "..", "../x", "docs/../../x", "", "."):
            self.assertFalse(ex(tok, ["docs"], [], hard), tok)
        for tok in ("docs/a.md", "./docs/a.md", "docs/a.md/", "docs/"):
            self.assertTrue(ex(tok, ["docs/"], [], hard), tok)
        self.assertFalse(ex("docs/a.md", ["docs"], ["docs/"], hard))
        self.assertFalse(ex(".brother-edition", [".brother-edition"], [], hard))

    def test_hostile_inputs_are_refused_or_denied(self):
        values = [None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"],
                  {}, {"a": 1}, (), {1, 2}, object()]
        for v in values:
            self.assertFalse(charter_paths.exported(v, v, v, v), repr(v))
            self.assertFalse(charter_paths.exported("docs/a", [v], [v], [v]),
                             repr(v))
            if not isinstance(v, str):
                with self.assertRaises(ValueError):
                    charter_paths.export_rules(v)
                with self.assertRaises(ValueError):
                    charter_paths.check(v, v)


class Night0912CharterPaths(unittest.TestCase):
    def test_invalid_utf8_charter_is_nodata(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            charter = os.path.join(tmp, "docs", "CHARTER.md")
            with open(charter, "wb") as fh:
                fh.write(b"\xff\xfe")

            self.assertIsNone(charter_paths.read_charter(charter))
            self.assertEqual(charter_paths.main(["--root", tmp]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
