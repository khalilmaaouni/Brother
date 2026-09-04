#!/usr/bin/env python3
"""Drive release_invariant backwards: every verdict class must be reachable,
and a broken link must FAIL by name, never dissolve into a pass."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_invariant as ri  # noqa: E402

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
                }.get(rel)
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
                }.get(rel)
                verdict, detail = ri.installed_matches("1.0.0", "v1.0.0", cache)
                self.assertEqual(verdict, "mismatch")
                self.assertIn("RUNTIME-MANIFEST.json", detail)
            finally:
                ri._tag_bundle_bytes = saved

    def test_a_file_the_tag_predates_is_named_nodata_not_a_dead_check(self):
        """The Codex manifest joined CHECKED_FILES after v1.0.1 was cut, so a
        released tag can be missing one checked file while carrying the rest.
        That must stay a match with a named NO-DATA on the missing file: the
        earlier code returned "tree unreadable" for the WHOLE comparison on
        the first unreadable file, which would have retired a working check
        the moment a new file joined the list."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = self._install(tmp, {
                ".claude-plugin/plugin.json": b"P",
                "runtime/brother_run.py": b"R",
                "runtime/RUNTIME-MANIFEST.json": b"M"})
            saved = ri._tag_bundle_bytes
            try:
                ri._tag_bundle_bytes = lambda tag, rel, checkout=None: {
                    os.path.join(".claude-plugin", "plugin.json"): b"P",
                    os.path.join("runtime", "brother_run.py"): b"R",
                    os.path.join("runtime", "RUNTIME-MANIFEST.json"): b"M",
                }.get(rel)
                verdict, detail = ri.installed_matches("1.0.0", "v1.0.0", cache)
                self.assertEqual(verdict, "match")
                self.assertIn("NO-DATA", detail)
                self.assertIn(".codex-plugin", detail)
            finally:
                ri._tag_bundle_bytes = saved

    def test_the_codex_manifest_is_release_critical(self):
        """A regression guard on the list itself: dropping the Codex manifest
        out of CHECKED_FILES would silently stop comparing what a Codex user
        installs against what was released."""
        self.assertIn(os.path.join(".codex-plugin", "plugin.json"),
                      ri.CHECKED_FILES)

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

class GitHubReporting(unittest.TestCase):
    """The four new links widen the chain to what GitHub itself reports:
    a Release for the tag, not draft/prerelease, the tag's commit agreeing
    between git and the GitHub API, and a main ruleset requiring pull
    requests. Every scenario patches ri._call, the one subprocess helper
    behind all four, so no test touches the network."""

    def _run(self, tmp, call_fn, notes_for="1.0.0"):
        saved_root = ri.ROOT
        saved_cache = ri.INSTALL_CACHE
        saved_remote = ri.remote_has_tag
        saved_call = ri._call
        saved_checkout = ri.PUBLIC_CHECKOUT
        try:
            ri.ROOT = tmp
            ri.INSTALL_CACHE = os.path.join(tmp, "no-cache")
            # THE TAG EXISTS in every scenario of this class: _clean_call's
            # ls-remote answer says so, and the four links under test are
            # about what GitHub reports ON TOP of an existing tag. Stubbing
            # this to None (unreachable) made the class self-contradictory
            # once a missing Release became FAIL only when the tag is known
            # to exist: the v1.0.1 defect (a tag nobody turned into a
            # Release) would have read NO-DATA here and proved nothing.
            ri.remote_has_tag = lambda tag, remote=None: True
            # ...and with the tag present, the tag-tree links run, so the
            # checkout is pointed away from the real repository: unstubbed
            # they would read the maintainer's own tags.
            ri.PUBLIC_CHECKOUT = os.path.join(tmp, "no-checkout")
            ri._call = call_fn
            return ri.main([])
        finally:
            ri.ROOT = saved_root
            ri.INSTALL_CACHE = saved_cache
            ri.remote_has_tag = saved_remote
            ri._call = saved_call
            ri.PUBLIC_CHECKOUT = saved_checkout

    RELEASE_OK = {"tagName": "v1.0.0", "isDraft": False,
                  "isPrerelease": False, "targetCommitish": "main",
                  "name": "Brother 1.0.0"}

    def _clean_call(self, args, parse_json=True):
        """A fully agreeing gh/git world: release present and clean, tag
        commits agree, ruleset present. Individual tests override one
        branch of this to isolate a single broken link."""
        if args[:3] == ["gh", "release", "view"]:
            return True, dict(self.RELEASE_OK)
        if args[:2] == ["git", "ls-remote"]:
            return True, "deadbeef\trefs/tags/v1.0.0\n" \
                         "cafef00d\trefs/tags/v1.0.0^{}\n"
        if args[:2] == ["gh", "api"] and "git/ref/tags" in args[2]:
            return True, {"object": {"sha": "tagobjsha", "type": "tag"}}
        if args[:2] == ["gh", "api"] and "git/tags/" in args[2]:
            return True, {"object": {"sha": "cafef00d"}}
        if args[:2] == ["gh", "api"] and "rules/branches" in args[2]:
            return True, [{"type": "pull_request"}]
        raise AssertionError("unexpected call: %s" % args)

    def test_clean_github_world_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, self._clean_call), 0)

    def test_release_absent_fails(self):
        def call(args, parse_json=True):
            if args[:3] == ["gh", "release", "view"]:
                return False, "release not found"
            return self._clean_call(args, parse_json)
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, call), 1)

    def test_draft_release_fails(self):
        def call(args, parse_json=True):
            if args[:3] == ["gh", "release", "view"]:
                r = dict(self.RELEASE_OK)
                r["isDraft"] = True
                return True, r
            return self._clean_call(args, parse_json)
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, call), 1)

    def test_prerelease_on_a_production_version_fails(self):
        def call(args, parse_json=True):
            if args[:3] == ["gh", "release", "view"]:
                r = dict(self.RELEASE_OK)
                r["isPrerelease"] = True
                return True, r
            return self._clean_call(args, parse_json)
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, call), 1)

    def test_target_commit_mismatch_fails(self):
        def call(args, parse_json=True):
            if args[:2] == ["git", "ls-remote"]:
                return True, "deadbeef\trefs/tags/v1.0.0\n" \
                             "DIFFERENT\trefs/tags/v1.0.0^{}\n"
            return self._clean_call(args, parse_json)
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, call), 1)

    def test_ruleset_present_is_ok(self):
        # _clean_call already carries a pull_request rule; a run that
        # exits 0 through it is this case, covered by test_clean_github_
        # world_exits_0 above. This test isolates the rule list itself.
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            status, detail = "", ""
            saved_call = ri._call
            try:
                ri._call = self._clean_call
                status, detail = ri.branch_ruleset_requires_pr()
            finally:
                ri._call = saved_call
            self.assertEqual(status, "ok")
            self.assertIn("pull requests", detail)

    def test_ruleset_absent_fails(self):
        def call(args, parse_json=True):
            if args[:2] == ["gh", "api"] and "rules/branches" in args[2]:
                return True, [{"type": "deletion"}]
            return self._clean_call(args, parse_json)
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, call), 1)

    def test_gh_unavailable_is_nodata_not_a_failure(self):
        def call(args, parse_json=True):
            if args[0] == "gh":
                return False, "gh not installed"
            return self._clean_call(args, parse_json)
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            # gh absent leaves the Release, commit-agreement and ruleset
            # links all NO-DATA; nothing else in the fixture contradicts,
            # so the run still exits 0 (NO-DATA never fails it, per this
            # file's existing convention).
            self.assertEqual(self._run(tmp, call), 0)

    def test_permanent_nodata_population_lines_always_print(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            saved_root = ri.ROOT
            saved_cache = ri.INSTALL_CACHE
            saved_remote = ri.remote_has_tag
            saved_call = ri._call
            import io
            import contextlib
            buf = io.StringIO()
            try:
                ri.ROOT = tmp
                ri.INSTALL_CACHE = os.path.join(tmp, "no-cache")
                ri.remote_has_tag = lambda tag, remote=None: None
                ri._call = self._clean_call
                with contextlib.redirect_stdout(buf):
                    ri.main([])
            finally:
                ri.ROOT = saved_root
                ri.INSTALL_CACHE = saved_cache
                ri.remote_has_tag = saved_remote
                ri._call = saved_call
            out = buf.getvalue()
            self.assertIn("NO-DATA: tag signature not examined", out)
            self.assertIn("NO-DATA: hook scoping not examined", out)
            self.assertIn(
                "NO-DATA: virtual-install run for v1.0.0 not examined"
                .replace("virtual", "virgin"), out)

class MainVerdicts(unittest.TestCase):
    """main() is driven through module globals so no fixture ever touches
    the network: remote_has_tag is stubbed per case."""

    def _run(self, tmp, remote_answer, release_missing=False):
        # These cases are about the pre-existing links (version sites,
        # release notes, the public git tag); the new GitHub-reporting
        # links must stay hermetic here too, or they hit the real network
        # against the real public repository and this file's fixture
        # version. Stubbed to NO-DATA, same as an unreachable remote.
        # PUBLIC_CHECKOUT is pointed at a directory that is not a git
        # checkout for the same reason: with remote_answer True the tag
        # links run, and unstubbed they would read the REAL repository's
        # tags on the maintainer's machine.
        saved_root, saved_remote = ri.ROOT, ri.remote_has_tag
        saved_cache = ri.INSTALL_CACHE
        saved_call = ri._call
        saved_checkout = ri.PUBLIC_CHECKOUT

        def fake_call(args, parse_json=True):
            if release_missing and "release" in args:
                return False, "release not found"
            return False, "not mocked in this test"

        try:
            ri.ROOT = tmp
            ri.INSTALL_CACHE = os.path.join(tmp, "no-cache")
            ri.PUBLIC_CHECKOUT = os.path.join(tmp, "no-checkout")
            ri.remote_has_tag = lambda tag, remote=None: remote_answer
            ri._call = fake_call
            return ri.main([])
        finally:
            ri.ROOT, ri.remote_has_tag = saved_root, saved_remote
            ri.INSTALL_CACHE = saved_cache
            ri._call = saved_call
            ri.PUBLIC_CHECKOUT = saved_checkout

    def test_agreement_with_unreachable_remote_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, None), 0)

    def test_version_site_disagreement_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.1", notes_for="1.0.0")
            self.assertEqual(self._run(tmp, None), 1)

    def test_missing_tag_on_reachable_remote_is_nodata_not_a_fail(self):
        """THE MOMENT BETWEEN THE CUT AND THE TAG. This case asserted exit 1
        until 2026-09-04, which made the tag unreachable: export_public.py
        runs the readiness gate at tag time, that gate runs this invariant,
        and its FAIL refused the push that would have created the tag."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = self._run(tmp, False)
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("NO-DATA: public repository carries no tag v1.0.0 "
                          "yet; the cut precedes the tag", text)
            self.assertNotIn("FAIL:", text)
            # The summary must SAY how many links read NO-DATA, so a reader
            # can never mistake this exit 0 for a clean release.
            self.assertRegex(text, r"link\(s\) NO-DATA")

    def test_present_tag_whose_tree_declares_another_version_exits_1(self):
        """The tag EXISTS and disagrees: still a contradiction, still FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            saved = ri.tag_bundle_version
            out = io.StringIO()
            try:
                ri.tag_bundle_version = lambda tag, checkout=None: "0.9.0"
                with contextlib.redirect_stdout(out):
                    code = self._run(tmp, True)
            finally:
                ri.tag_bundle_version = saved
            self.assertEqual(code, 1)
            self.assertIn("tag v1.0.0 ships bundle version 0.9.0, not 1.0.0",
                          out.getvalue())

    def test_present_tag_with_no_github_release_exits_1(self):
        """A tag nobody turned into a Release is the v1.0.1 defect. It stays
        a FAIL even though the identical gh answer reads NO-DATA when the tag
        does not exist yet."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0", notes_for="1.0.0")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = self._run(tmp, True, release_missing=True)
            self.assertEqual(code, 1)
            self.assertIn("FAIL: no GitHub Release exists for v1.0.0",
                          out.getvalue())

    def test_missing_release_notes_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_repo(tmp, "1.0.0", "1.0.0")
            self.assertEqual(self._run(tmp, None), 1)

    def test_nothing_readable_is_nodata_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, None), 2)


class ShippedRuntimeDrift(unittest.TestCase):
    """E80: drift used to be measured over ONE file, runtime/RUNTIME-
    MANIFEST.json, so a one byte tamper anywhere else in the shipped runtime
    passed. Every case here would have read clean under that check."""

    SHIPPED = {
        "bundle/runtime/brother_run.py": b"# runtime entry point\n",
        "bundle/runtime/RUNTIME-MANIFEST.json": b'{"files": 2}\n',
        "bundle/runtime/lib/helper.py": b"def helper():\n    return 1\n",
    }

    def _drive(self, local, tag_bytes=None, tag_files=None):
        """local: what sits in the working tree. tag_bytes: what the tag
        shipped (defaults to SHIPPED). tag_files: the tag's own file list
        (None means "the tree is unreadable", the NO-DATA case)."""
        shipped = self.SHIPPED if tag_bytes is None else tag_bytes
        saved = (ri.tag_tree_files, ri._tag_bundle_bytes)
        tmp = tempfile.mkdtemp(prefix="runtime-drift-")
        try:
            for rel, data in local.items():
                full = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "wb") as fh:
                    fh.write(data)
            listing = (list(shipped) if tag_files is None else tag_files)
            ri.tag_tree_files = lambda tag, prefix, checkout=None: (
                None if tag_files is False else listing)
            ri._tag_bundle_bytes = lambda tag, rel, checkout=None: shipped.get(
                "bundle/" + rel.replace(os.sep, "/"))
            return ri.runtime_drift("v9.9.9", tmp, "/nowhere")
        finally:
            (ri.tag_tree_files, ri._tag_bundle_bytes) = saved

    def test_an_untampered_runtime_reports_no_drift_over_every_file(self):
        drifted, reason, checked = self._drive(dict(self.SHIPPED))
        self.assertEqual(drifted, [])
        self.assertIsNone(reason)
        self.assertEqual(checked, 3)

    def test_a_one_byte_tamper_in_a_shipped_runtime_file_is_caught(self):
        # THE case the old one-file check missed: the manifest is untouched
        # and byte-identical, and only the entry point moved by one byte.
        local = dict(self.SHIPPED)
        local["bundle/runtime/brother_run.py"] = b"# runtime entry poinT\n"
        drifted, reason, checked = self._drive(local)
        self.assertEqual(drifted, ["bundle/runtime/brother_run.py"])
        self.assertIsNone(reason)
        self.assertEqual(checked, 3)

    def test_a_tamper_in_a_nested_runtime_file_is_caught_too(self):
        local = dict(self.SHIPPED)
        local["bundle/runtime/lib/helper.py"] = b"def helper():\n    return 2\n"
        drifted, _reason, _checked = self._drive(local)
        self.assertEqual(drifted, ["bundle/runtime/lib/helper.py"])

    def test_a_shipped_file_missing_here_is_drift_not_no_data(self):
        local = {k: v for k, v in self.SHIPPED.items()
                 if k != "bundle/runtime/lib/helper.py"}
        drifted, reason, _checked = self._drive(local)
        self.assertEqual(drifted, ["bundle/runtime/lib/helper.py"])
        self.assertIsNone(reason)

    def test_an_unreadable_tag_tree_is_no_data_never_a_clean_pass(self):
        drifted, reason, checked = self._drive(dict(self.SHIPPED),
                                                tag_files=False)
        self.assertIsNone(drifted)
        self.assertIn("unreadable", reason)
        self.assertEqual(checked, 0)

    def test_a_tag_shipping_no_runtime_file_is_no_data_never_a_clean_pass(self):
        drifted, reason, _checked = self._drive({}, tag_bytes={},
                                                 tag_files=[])
        self.assertIsNone(drifted)
        self.assertIn("no file under", reason)


class ShippedRuntimeAgreesWithItsSource(unittest.TestCase):
    """E80 item 4: bundle_runtime.check() already hashed the shipped runtime
    against its scripts/ source, and the release identity chain never asked
    it. These drive the wiring, not a second comparison."""

    def _drive(self, ok, problems):
        import bundle_runtime as BR
        saved = BR.check
        try:
            BR.check = lambda scripts_dir=None, runtime_dir=None: (
                ok, problems, [])
            return ri.shipped_runtime_matches_its_source(ri.ROOT)
        finally:
            BR.check = saved

    def test_an_agreeing_tree_is_ok(self):
        self.assertEqual(self._drive(True, []), (True, []))

    def test_a_runtime_that_does_not_match_its_source_is_a_failure(self):
        ok, problems = self._drive(
            False, ["brother_run.py: bundle/runtime copy does not match its "
                    "scripts/ source"])
        self.assertFalse(ok)
        self.assertIn("brother_run.py", problems[0])

    def test_the_real_tree_answers_one_way_or_the_other_never_none(self):
        # The wiring must actually reach bundle_runtime here, not silently
        # sit on the NO-DATA branch for the life of the check.
        ok, detail = ri.shipped_runtime_matches_its_source(ri.ROOT)
        self.assertIsNotNone(ok, detail)


class TheReadmeDoesNotTellTheReaderToOverwriteWhatTheyVerify(unittest.TestCase):
    """E80 item 7 (2026-09-04, external release integrity trial on the public
    v1.0.1 clone). The README's release verification line read "run
    `sh scripts/checksums.sh CHECKSUMS.sha256` and then
    `bash scripts/verify-install.sh`". checksums.sh REGENERATES the manifest
    from whatever is on disk, so following that order overwrites the
    published manifest with one derived from the possibly tampered bytes and
    then compares those bytes against themselves: the verification always
    passes, and a real tamper is erased by the reader's own first command.

    Driven against the pre-fix README and observed failing on the ordering
    assertion."""

    README = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "README.md")

    def setUp(self):
        try:
            with open(self.README, encoding="utf-8") as fh:
                self.text = fh.read()
        except OSError as exc:
            self.skipTest("NO-DATA: README.md unreadable (%s)" % exc)

    def test_verify_install_is_named_before_checksums_regeneration(self):
        verify = self.text.find("verify-install.sh")
        regen = self.text.find("checksums.sh CHECKSUMS.sha256")
        self.assertNotEqual(verify, -1, "README no longer names verify-install.sh")
        self.assertNotEqual(regen, -1, "README no longer names checksums.sh")
        self.assertLess(
            verify, regen,
            "README tells the reader to regenerate the manifest before "
            "verifying against it; that overwrites the file under test and "
            "makes any tamper verify clean (E80 item 7)")

    def test_the_readme_says_regeneration_is_the_maintainer_step(self):
        self.assertIn("maintainer's step", self.text,
                      "README no longer says who regenerates the manifest, "
                      "so a reader has no reason not to run checksums.sh")

    def test_the_readme_names_the_shipped_runtime_verifier(self):
        # E80 item 5: the runtime verifier is only useful if the reader is
        # told it exists.
        self.assertIn("bundle/runtime/verify_runtime.py", self.text,
                      "README does not name the runtime verifier, so nothing "
                      "tells an installed user how to check the engine bytes")


if __name__ == "__main__":
    unittest.main()
