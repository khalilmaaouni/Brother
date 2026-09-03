"""Calibration for scripts/leaf_pin_check.py.

Every case asserts the EXIT CODE, never only the printed verdict. That is
deliberate and it is this estate's most expensive recent lesson: on 2026-08-23
a release gate printed FAIL and exited 0, and eleven tests passed over it
because every one of them asserted the verdict STRING. A gate that prints FAIL
and exits 0 manufactures evidence of a pass, because exit 0 is what every
wrapper and && chain above it reads.

The verdict cases run with the network stubbed out, so they are deterministic.
The NO-DATA case uses a path that cannot be a git remote, which needs no
network either.
"""
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import leaf_pin_check as lpc


def run_main():
    """Return (exit_code, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = lpc.main()
    return code, buf.getvalue()


class Verdicts(unittest.TestCase):

    def test_agreement_exits_zero(self):
        with mock.patch.object(lpc, "newest_published_tag", return_value="3.4.2"), \
             mock.patch.object(lpc, "declared",
                               return_value={"brothermode": [("a", "3.4.2")],
                                             "brothersbe": [("b", "3.4.2")]}):
            code, out = run_main()
        self.assertEqual(code, 0, out)
        self.assertIn("PASSED", out)

    def test_todays_real_defect_exits_one(self):
        """The umbrella advertising 3.4.1 while the leaf published 3.4.2."""
        with mock.patch.object(lpc, "newest_published_tag", return_value="3.4.2"), \
             mock.patch.object(lpc, "declared",
                               return_value={"brothermode": [("a", "3.4.2")],
                                             "brothersbe": [("b", "3.4.1")]}):
            code, out = run_main()
        self.assertEqual(code, 1, out)
        self.assertIn("MISMATCH", out)
        self.assertIn("3.4.1", out)

    def test_unreadable_tags_exit_two_and_two_is_not_zero(self):
        """NO-DATA is never a pass. The assertion that matters is that this is
        NOT 0; that it is 2 rather than 1 is the second-order detail."""
        with mock.patch.object(lpc, "newest_published_tag", return_value=None), \
             mock.patch.object(lpc, "declared",
                               return_value={"brothermode": [("a", "3.4.2")],
                                             "brothersbe": [("b", "3.4.2")]}):
            code, out = run_main()
        self.assertNotEqual(code, 0, "a check that could not look exited as a pass")
        self.assertEqual(code, 2, out)
        self.assertIn("NO-DATA", out)

    def test_a_leaf_declared_nowhere_is_no_data_not_a_pass(self):
        """An umbrella that stopped mentioning a leaf would otherwise have
        nothing to disagree with, and would pass by vacancy."""
        with mock.patch.object(lpc, "newest_published_tag", return_value="3.4.2"), \
             mock.patch.object(lpc, "declared",
                               return_value={"brothermode": [("a", "3.4.2")],
                                             "brothersbe": []}):
            code, out = run_main()
        self.assertNotEqual(code, 0, "a leaf declared nowhere passed by vacancy")
        self.assertEqual(code, 2, out)


class TagReading(unittest.TestCase):

    def test_unreachable_remote_returns_none_rather_than_raising(self):
        self.assertIsNone(lpc.newest_published_tag("/definitely/not/a/git/remote"))

    def test_newest_is_chosen_numerically_not_lexically(self):
        """3.4.10 beats 3.4.9, which string ordering gets wrong."""
        lines = "\n".join([
            "aaa\trefs/tags/v3.4.9",
            "bbb\trefs/tags/v3.4.10",
            "ccc\trefs/tags/v3.4.10^{}",
            "ddd\trefs/tags/brothersbe--v3.3.1",
        ])
        with mock.patch.object(lpc.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout=lines)):
            self.assertEqual(lpc.newest_published_tag("x"), "3.4.10")


class M6DeclaredReadsTheSubdirRef(unittest.TestCase):
    """M6 adaptation: post cutover, both leaves are pinned by their
    git-subdir `ref`, not by their cosmetic top-level `version` (a
    different number in a different space, see the module docstring). This
    exercises the real declared() against a fixture marketplace.json rather
    than mocking declared() away, so the new reading path is actually
    covered."""

    def _write_marketplace(self, tmp, brothermode_source, brothersbe_source):
        plugin_dir = tmp / ".claude-plugin"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "marketplace.json").write_text(json.dumps({
            "plugins": [
                {"name": "brothermode", "version": "3.4.2",
                 "source": brothermode_source},
                {"name": "brothersbe", "version": "3.7.0",
                 "source": brothersbe_source},
            ]
        }))

    def test_git_subdir_source_is_pinned_by_ref_not_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            subdir_source = {"source": "git-subdir",
                             "url": "https://github.com/khalilmaaouni/Brother",
                             "path": "products/brothermode", "ref": "v0.9.8"}
            self._write_marketplace(tmp, subdir_source,
                                    {**subdir_source,
                                     "path": "products/brothersbe"})
            with mock.patch.object(lpc, "ROOT", tmp):
                sites = lpc.declared()
        self.assertEqual(sites["brothermode"],
                         [(".claude-plugin/marketplace.json (ref)", "0.9.8")])
        self.assertEqual(sites["brothersbe"],
                         [(".claude-plugin/marketplace.json (ref)", "0.9.8")])

    def test_source_with_no_ref_falls_back_to_top_level_version(self):
        """A source with no `ref` at all (a bare string like the bundle
        plugin's "./bundle", or a dict that omits it) is read by its plain
        `version` field, exactly as before M6. A url-form source that DOES
        carry a `ref` (the pre-cutover shape) is read the same way a
        git-subdir source is: both are genuinely pinned by their ref, and
        pre-cutover the two numbers always agreed anyway."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            no_ref_source = {"source": "url",
                             "url": "https://example.invalid/x.git"}
            self._write_marketplace(tmp, no_ref_source, "./bundle")
            with mock.patch.object(lpc, "ROOT", tmp):
                sites = lpc.declared()
        self.assertEqual(sites["brothermode"],
                         [(".claude-plugin/marketplace.json (version)", "3.4.2")])
        self.assertEqual(sites["brothersbe"],
                         [(".claude-plugin/marketplace.json (version)", "3.7.0")])

    def test_both_leaves_share_one_url_post_cutover(self):
        """The drift class this check exists for (an umbrella advertising a
        stale version) is still catchable post cutover, now measured
        against the one repository both leaves share."""
        self.assertEqual(lpc.LEAVES["brothermode"], lpc.LEAVES["brothersbe"])


if __name__ == "__main__":
    unittest.main()
