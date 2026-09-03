#!/usr/bin/env python3
"""Drive release_invariant backwards: every verdict class must be reachable,
and a broken link must FAIL by name, never dissolve into a pass."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_invariant as ri  # noqa: E402


def fixture_repo(tmp, bundle_version, market_version, notes_for=None):
    os.makedirs(os.path.join(tmp, "bundle", ".claude-plugin"))
    os.makedirs(os.path.join(tmp, ".claude-plugin"))
    os.makedirs(os.path.join(tmp, "docs", "releases"))
    with open(os.path.join(tmp, "bundle", ".claude-plugin", "plugin.json"),
              "w") as fh:
        json.dump({"name": "brother", "version": bundle_version}, fh)
    with open(os.path.join(tmp, ".claude-plugin", "marketplace.json"),
              "w") as fh:
        json.dump({"plugins": [{"name": "brother",
                                 "version": market_version}]}, fh)
    if notes_for:
        with open(os.path.join(tmp, "docs", "releases",
                               "%s.md" % notes_for), "w") as fh:
            fh.write("# Brother %s\n" % notes_for)


class DeclaredVersions(unittest.TestCase):
    def test_agreeing_sites_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.2.3", "1.2.3")
            self.assertEqual(ri.declared_versions(tmp), ("1.2.3", "1.2.3"))

    def test_a_disagreement_is_visible_not_averaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.2.3", "1.2.4")
            self.assertEqual(ri.declared_versions(tmp), ("1.2.3", "1.2.4"))

    def test_an_unreadable_repo_returns_none_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ri.declared_versions(tmp), (None, None))


class InstalledMatches(unittest.TestCase):
    """installed_matches compares the installed plugin against the TAG's
    bundle bytes, not the working tree. _tag_bundle_bytes is stubbed so the
    test never needs a real git tag; the point under test is that a match is
    measured against the RELEASED bytes."""

    def _install(self, tmp, files):
        inst = os.path.join(tmp, "cache", "1.0.0")
        os.makedirs(os.path.join(inst, ".claude-plugin"))
        os.makedirs(os.path.join(inst, "runtime"))
        with open(os.path.join(inst, ".claude-plugin", "plugin.json"),
                  "wb") as fh:
            fh.write(files[".claude-plugin/plugin.json"])
        for rel in ("brother_run.py", "RUNTIME-MANIFEST.json"):
            with open(os.path.join(inst, "runtime", rel), "wb") as fh:
                fh.write(files["runtime/" + rel])
        return os.path.join(tmp, "cache")

    def test_no_installed_copy_is_nodata_never_a_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict, detail = ri.installed_matches(
                "9.9.9", "v9.9.9", cache=os.path.join(tmp, "cache"))
            self.assertIsNone(verdict)
            self.assertIn("no installed copy", detail)

    def test_installed_matching_the_tag_is_a_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            same = {".claude-plugin/plugin.json": b"P",
                    "runtime/brother_run.py": b"R",
                    "runtime/RUNTIME-MANIFEST.json": b"M"}
            cache = self._install(tmp, same)
            saved = ri._tag_bundle_bytes
            try:
                ri._tag_bundle_bytes = lambda tag, rel, checkout=None: {
                    os.path.join(".claude-plugin", "plugin.json"): b"P",
                    os.path.join("runtime", "brother_run.py"): b"R",
                    os.path.join("runtime", "RUNTIME-MANIFEST.json"): b"M",
                }[rel]
                verdict, _ = ri.installed_matches("1.0.0", "v1.0.0", cache)
                self.assertEqual(verdict, "match")
            finally:
                ri._tag_bundle_bytes = saved

    def test_installed_differing_from_the_tag_fails_naming_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._install(tmp, {
                ".claude-plugin/plugin.json": b"P",
                "runtime/brother_run.py": b"R",
                "runtime/RUNTIME-MANIFEST.json": b"DRIFTED"})
            saved = ri._tag_bundle_bytes
            try:
                ri._tag_bundle_bytes = lambda tag, rel, checkout=None: {
                    os.path.join(".claude-plugin", "plugin.json"): b"P",
                    os.path.join("runtime", "brother_run.py"): b"R",
                    os.path.join("runtime", "RUNTIME-MANIFEST.json"): b"M",
                }[rel]
                verdict, detail = ri.installed_matches("1.0.0", "v1.0.0", cache)
                self.assertEqual(verdict, "mismatch")
                self.assertIn("RUNTIME-MANIFEST.json", detail)
            finally:
                ri._tag_bundle_bytes = saved

    def test_an_unreadable_tag_tree_is_nodata_not_a_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._install(tmp, {
                ".claude-plugin/plugin.json": b"P",
                "runtime/brother_run.py": b"R",
                "runtime/RUNTIME-MANIFEST.json": b"M"})
            saved = ri._tag_bundle_bytes
            try:
                ri._tag_bundle_bytes = lambda tag, rel, checkout=None: None
                verdict, detail = ri.installed_matches("1.0.0", "v1.0.0", cache)
                self.assertIsNone(verdict)
                self.assertIn("tree unreadable", detail)
            finally:
                ri._tag_bundle_bytes = saved

    def test_a_prerelease_version_needs_no_tag_and_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "0.9.8-dev", "0.9.8-dev")
            saved_root = ri.ROOT
            saved_cache = ri.INSTALL_CACHE
            try:
                ri.ROOT = tmp
                ri.INSTALL_CACHE = os.path.join(tmp, "no-cache")
                self.assertEqual(ri.main([]), 0)
            finally:
                ri.ROOT, ri.INSTALL_CACHE = saved_root, saved_cache

    def test_a_prerelease_with_disagreeing_sites_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "0.9.8-dev", "0.9.8-other")
            saved_root = ri.ROOT
            saved_cache = ri.INSTALL_CACHE
            try:
                ri.ROOT = tmp
                ri.INSTALL_CACHE = os.path.join(tmp, "no-cache")
                self.assertEqual(ri.main([]), 1)
            finally:
                ri.ROOT, ri.INSTALL_CACHE = saved_root, saved_cache


class MainVerdicts(unittest.TestCase):
    """main() is driven through module globals so no fixture ever touches
    the network: remote_has_tag is stubbed per case."""

    def _run(self, tmp, remote_answer):
        saved_root, saved_remote = ri.ROOT, ri.remote_has_tag
        saved_cache = ri.INSTALL_CACHE
        try:
            ri.ROOT = tmp
            ri.INSTALL_CACHE = os.path.join(tmp, "no-cache")
            ri.remote_has_tag = lambda tag, remote=None: remote_answer
            return ri.main([])
        finally:
            ri.ROOT, ri.remote_has_tag = saved_root, saved_remote
            ri.INSTALL_CACHE = saved_cache

    def test_agreement_with_unreachable_remote_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, None), 0)

    def test_version_site_disagreement_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.1", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, None), 1)

    def test_missing_tag_on_reachable_remote_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, False), 1)

    def test_missing_release_notes_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0")
            self.assertEqual(self._run(tmp, None), 1)

    def test_nothing_readable_is_nodata_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, None), 2)


if __name__ == "__main__":
    unittest.main()
