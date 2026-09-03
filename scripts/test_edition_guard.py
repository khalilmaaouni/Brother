"""What the edition guard must keep true. docs/plan/HUB-MIGRATION-PLAN-
2026-08-30.md step 5: no working session ever holds the public remote, and
the guard is what makes that fail SAFE (refused) rather than fail public.

Driven backwards, the migration plan's own cases:
  * a push toward a public-named remote from editions/personal is refused,
    naming the law
  * the exporter's own marked invocation is the single allow
  * no .brother-edition anywhere above the directory reads NO-DATA, never
    a pass
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import edition_guard as EG  # noqa: E402

ROOT = os.path.dirname(HERE)
PUBLIC_HTTPS = "https://github.com/khalilmaaouni/Brother"
PUBLIC_SSH = "git@github.com:khalilmaaouni/Brother.git"


class FindingTheNearestEditionFile(unittest.TestCase):
    def test_it_finds_the_edition_file_directly_above(self):
        path = EG.find_edition_file(os.path.join(ROOT, "editions", "personal"))
        self.assertEqual(
            path, os.path.join(ROOT, "editions", "personal", ".brother-edition"))

    def test_a_directory_below_the_edition_root_still_finds_it(self):
        """Walks UP: a directory nested under editions/personal inherits its
        edition without needing its own marker file."""
        nested = os.path.join(ROOT, "editions", "personal", "does", "not",
                               "need", "to", "exist")
        # find_edition_file only reads directories, never requires the
        # start path itself to exist.
        path = EG.find_edition_file(nested)
        self.assertEqual(
            path, os.path.join(ROOT, "editions", "personal", ".brother-edition"))

    def test_no_brother_edition_anywhere_above_is_None(self):
        with tempfile.TemporaryDirectory() as tmp:
            isolated = os.path.join(tmp, "a", "b", "c")
            os.makedirs(isolated)
            self.assertIsNone(EG.find_edition_file(isolated))


class ParsingTheMarkerFile(unittest.TestCase):
    def test_edition_and_vault_are_read(self):
        edition, vault = EG.parse_edition_file(
            os.path.join(ROOT, "editions", "personal", ".brother-edition"))
        self.assertEqual(edition, "personal")
        self.assertTrue(vault and "brother-personal" in vault)

    def test_a_missing_edition_line_is_None_not_a_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".brother-edition")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("vault: somewhere\n")
            edition, vault = EG.parse_edition_file(path)
            self.assertIsNone(edition)
            self.assertEqual(vault, "somewhere")


class NoBrotherEditionReadsNoData(unittest.TestCase):
    """The migration plan's own case: a directory carrying none is refused,
    never treated as public-core by default."""

    def test_where_on_an_isolated_directory_is_NO_DATA(self):
        with tempfile.TemporaryDirectory() as tmp:
            isolated = os.path.join(tmp, "nowhere")
            os.makedirs(isolated)
            self.assertEqual(EG.main(["--where", isolated]), EG.EXIT_NODATA)

    def test_check_push_on_an_isolated_directory_is_NO_DATA(self):
        with tempfile.TemporaryDirectory() as tmp:
            isolated = os.path.join(tmp, "nowhere")
            os.makedirs(isolated)
            code, msg = EG.check_push(PUBLIC_HTTPS, cwd=isolated, env={})
            self.assertEqual(code, EG.EXIT_NODATA)
            self.assertIn("NO-DATA", msg)


class APushTowardThePublicRemoteFromAnEditionIsRefused(unittest.TestCase):
    """docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 5's driven-backwards
    case, run against the REAL editions/personal already in this repo."""

    def setUp(self):
        self.personal = os.path.join(ROOT, "editions", "personal")
        self.core = ROOT  # carries the root .brother-edition (public-core)

    def test_from_editions_personal_https_form(self):
        code, msg = EG.check_push(PUBLIC_HTTPS, cwd=self.personal, env={})
        self.assertEqual(code, EG.EXIT_REFUSED)
        self.assertIn("personal", msg)
        self.assertIn("REFUSED", msg)
        self.assertIn("read-only export target", msg)

    def test_from_editions_personal_ssh_form(self):
        """Both remote spellings of the same GitHub repository must match:
        an owner switching between https and ssh must not slip past this."""
        code, msg = EG.check_push(PUBLIC_SSH, cwd=self.personal, env={})
        self.assertEqual(code, EG.EXIT_REFUSED)

    def test_the_law_binds_public_core_too_not_only_private_editions(self):
        """The ground rule is that NO session holds the public remote, not
        merely that private editions may not: the root checkout is bound
        exactly the same way."""
        code, _msg = EG.check_push(PUBLIC_HTTPS, cwd=self.core, env={})
        self.assertEqual(code, EG.EXIT_REFUSED)

    def test_a_non_public_remote_is_never_refused(self):
        code, msg = EG.check_push(
            "https://github.com/khalilmaaouni/brother-hub",
            cwd=self.personal, env={})
        self.assertEqual(code, EG.EXIT_OK)


class TheExportersOwnMarkedInvocationIsTheSingleAllow(unittest.TestCase):
    def test_the_marker_lets_the_same_push_through(self):
        env = {EG.EXPORT_ENV: EG.EXPORT_MARK}
        code, msg = EG.check_push(
            PUBLIC_HTTPS, cwd=os.path.join(ROOT, "editions", "personal"),
            env=env)
        self.assertEqual(code, EG.EXIT_OK)
        self.assertIn(EG.EXPORT_MARK, msg)

    def test_the_wrong_value_does_not_count(self):
        """A session cannot invent the escape hatch by setting the variable
        to anything convenient; only the exporter's own exact marker
        counts."""
        env = {EG.EXPORT_ENV: "something-else"}
        code, _msg = EG.check_push(
            PUBLIC_HTTPS, cwd=os.path.join(ROOT, "editions", "personal"),
            env=env)
        self.assertEqual(code, EG.EXIT_REFUSED)


class TheWhereCLIMatchesStepThreesDoneCheck(unittest.TestCase):
    def test_where_prints_edition_and_vault(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = EG.main(["--where",
                             os.path.join(ROOT, "editions", "personal")])
        self.assertEqual(code, EG.EXIT_OK)
        out = buf.getvalue()
        self.assertIn("edition: personal", out)
        self.assertIn("vault:", out)


if __name__ == "__main__":
    unittest.main()
