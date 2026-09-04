#!/usr/bin/env python3
"""Tests for bm_vault_catalog, on tiny synthetic fixture vaults.

Run: python3 tools/test_bm_vault_catalog.py      (unittest output, exit 0 or 1)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
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

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(TOOL_DIR, "bm_vault_catalog.py")


def run(argv):
    p = subprocess.run([sys.executable, TOOL] + argv,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# Two projects (alpha with two typed groups plus one frontmatter-less note, beta with
# one note), a _template project directory that must never be baked, two failure notes
# carrying a symptom field under type: failure, and one failure-directory note carrying
# a symptom field under a different type (must be excluded and warned about, never
# silently included or dropped). Exercises per-project grouping, newest-first ordering
# within a group, the untyped bucket, the flat alphabetical symptom list, the type
# filter, and the template-directory skip, all in one fixture.
FIXTURE = {
    "10-Projects/alpha/Overview.md":
        "---\ntype: overview\nstatus: open\ncreated: 2026-08-01\n---\nAlpha overview.\n",
    "10-Projects/alpha/Sessions/2026-08-05-first.md":
        "---\ntype: session-log\nstatus: closed\ncreated: 2026-08-05\n---\nFirst.\n",
    "10-Projects/alpha/Sessions/2026-08-10-second.md":
        "---\ntype: session-log\nstatus: closed\ncreated: 2026-08-10\n---\nSecond.\n",
    "10-Projects/alpha/Notes/scratch.md":
        "Plain note, no frontmatter block at all.\n",
    "10-Projects/beta/Overview.md":
        "---\ntype: overview\nstatus: open\ncreated: 2026-08-02\n---\nBeta overview.\n",
    "10-Projects/_template/Overview.md":
        "---\ntype: overview\nstatus: open\ncreated: 2026-08-01\n---\nTemplate, never baked.\n",
    "40-Failures/a-thing-broke.md":
        "---\ntype: failure\nstatus: open\ncreated: 2026-08-03\n"
        "symptom: the button does nothing when clicked\n---\nBody.\n",
    "40-Failures/another-thing-broke.md":
        "---\ntype: failure\nstatus: open\ncreated: 2026-08-04\n"
        "symptom: the export silently drops rows\n---\nBody.\n",
    "40-Failures/off-type-with-symptom.md":
        "---\ntype: finding\nstatus: open\ncreated: 2026-08-06\n"
        "symptom: this looks like a failure but is not one\n---\nBody.\n",
}


def make_vault(files):
    tmp = tempfile.mkdtemp(prefix="bm-vault-catalog-")
    vault = os.path.join(tmp, "vault")
    for rel, text in files.items():
        write(os.path.join(vault, rel), text)
    return tmp, vault


class BakedCatalog(unittest.TestCase):
    """One fixture vault, baked once, then read from many angles.

    The methods are NUMBERED because the last of them mutates the vault: adding
    a note without rebaking is what makes the staleness case meaningful, and it
    must run after the case that asserts a freshly baked vault checks clean.
    unittest runs methods alphabetically rather than in source order.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.vault = make_vault(FIXTURE)
        cls.code, cls.out = run(["bake", "--vault", cls.vault])
        cls.alpha_path = os.path.join(cls.vault, "10-Projects", "alpha", "Catalog.md")
        cls.beta_path = os.path.join(cls.vault, "10-Projects", "beta", "Catalog.md")
        cls.template_path = os.path.join(cls.vault, "10-Projects", "_template", "Catalog.md")
        cls.fpath = os.path.join(cls.vault, "40-Failures", "Failures-by-Symptom.md")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_01_bake_exits_zero_and_reports_what_it_skipped(self):
        self.assertEqual(self.code, 0, "bake exited %d: %s" % (self.code, self.out[:300]))
        self.assertIn("skipped template project: 10-Projects/_template", self.out,
                      "bake did not report skipping _template: %s" % self.out[:300])
        self.assertIn("baked 2 project catalog(s)", self.out,
                      "bake did not report 2 project catalogs (_template must not count): %s"
                      % self.out[:300])

    def test_02_bake_warns_about_the_off_type_symptom_note(self):
        self.assertIn("warning: 40-Failures/off-type-with-symptom.md carries symptom: "
                      "without type: failure", self.out,
                      "bake did not warn about the off-type symptom note: %s" % self.out[:400])

    def test_03_the_expected_catalogs_exist_and_the_template_has_none(self):
        for p in (self.alpha_path, self.beta_path, self.fpath):
            self.assertTrue(os.path.isfile(p), "bake did not create %s" % p)
        self.assertFalse(os.path.isfile(self.template_path),
                         "bake created a Catalog.md for the _template project")

    def test_04_alpha_frontmatter_and_generated_by_header(self):
        alpha = read(self.alpha_path)
        self.assertTrue("type: index" in alpha and "tags: [catalog, generated]" in alpha,
                        "alpha catalog frontmatter wrong: %s" % alpha[:300])
        # The header must NAME a regeneration command without pinning the
        # repo-relative spelling: P17 (test_bm_store.py) forbids a shipped
        # string containing "python3 tools/bm_", because a packaged install
        # has no tools/ directory and such a string names a file the reader
        # does not have. Asserting the resolved shape keeps the check honest
        # while letting the command resolve to whatever layout is in use.
        self.assertTrue("Generated by bm_vault_catalog.py" in alpha
                        and "regenerate with:" in alpha
                        and " bake" in alpha,
                        "alpha catalog missing the generated-by header line")

    def test_05_alpha_groups_are_present_and_newest_first(self):
        alpha = read(self.alpha_path)
        for heading in ("## No frontmatter yet", "## overview", "## session-log"):
            self.assertIn(heading, alpha,
                          "alpha catalog missing an expected group heading: %s" % alpha[:600])
        # newest-first within the session-log group: 08-10 before 08-05.
        i10 = alpha.find("2026-08-10-second")
        i05 = alpha.find("2026-08-05-first")
        self.assertTrue(i10 != -1 and i05 != -1 and i10 < i05,
                        "session-log group not newest-first: %s" % alpha[:600])

    def test_06_alpha_links_are_full_path_and_never_self_referential(self):
        alpha = read(self.alpha_path)
        want_link = "[[10-Projects/alpha/Sessions/2026-08-10-second|2026-08-10-second]]"
        self.assertIn(want_link, alpha, "expected full-path wikilink missing: %s" % want_link)
        self.assertIn("[[10-Projects/alpha/Notes/scratch|scratch]]", alpha,
                      "untyped note link missing or malformed")
        # Catalog.md must never link itself.
        self.assertNotIn("Catalog|Catalog", alpha, "alpha catalog links itself")

    def test_07_failures_by_symptom_is_a_flat_sorted_and_type_filtered_list(self):
        fdoc = read(self.fpath)
        self.assertNotIn("\n## ", fdoc,
                         "failures-by-symptom is a flat list, must have no ## headings: %s"
                         % fdoc[:600])
        want = "[[40-Failures/a-thing-broke|a-thing-broke]]: the button does nothing when clicked"
        self.assertIn(want, fdoc, "failures-by-symptom missing the expected entry: %s" % want)
        # Type filter: the off-type note's symptom text must never appear.
        self.assertNotIn("this looks like a failure but is not one", fdoc,
                         "failures-by-symptom included an off-type symptom note")
        # Flat list sorted alphabetically by symptom text: "the button..." (b) before
        # "the export..." (e).
        i_button = fdoc.find("the button does nothing when clicked")
        i_export = fdoc.find("the export silently drops rows")
        self.assertTrue(i_button != -1 and i_export != -1 and i_button < i_export,
                        "failures-by-symptom not sorted alphabetically by symptom: %s"
                        % fdoc[:600])

    def test_08_a_second_bake_is_byte_identical(self):
        before = {p: read(p) for p in (self.alpha_path, self.beta_path, self.fpath)
                  if os.path.isfile(p)}
        code, out = run(["bake", "--vault", self.vault])
        self.assertEqual(code, 0, "second bake exited %d: %s" % (code, out[:300]))
        for p, text in before.items():
            self.assertEqual(read(p), text, "second bake changed %s (not byte-identical)" % p)

    def test_09_check_is_clean_right_after_a_bake(self):
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 0, "check after bake exited %d, want 0: %s" % (code, out[:300]))

    def test_10_an_unbaked_addition_makes_check_report_stale(self):
        # Mutates the vault on purpose, which is why it is numbered last.
        write(os.path.join(self.vault, "10-Projects", "alpha", "Notes", "new.md"),
              "---\ntype: overview\nstatus: open\ncreated: 2026-08-20\n---\nNew note.\n")
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 2,
                         "check after an unbaked addition exited %d, want 2: %s"
                         % (code, out[:300]))
        self.assertIn("stale", out, "check did not name what is stale: %s" % out[:300])


class EmptyVault(unittest.TestCase):
    def test_an_empty_vault_is_no_data_never_a_silent_pass_and_never_a_crash(self):
        empty_tmp = tempfile.mkdtemp(prefix="bm-vault-catalog-empty-")
        self.addCleanup(shutil.rmtree, empty_tmp, ignore_errors=True)
        code, out = run(["check", "--vault", empty_tmp])
        self.assertTrue(code == 3 and "NO-DATA" in out,
                        "empty vault did not report NO-DATA/exit 3: code=%d out=%s"
                        % (code, out[:200]))


class GitTrackedVault(unittest.TestCase):
    """Regression case for the 2026-08-28 dangling-link incident: in a git-tracked
    vault, a note sitting on disk but never committed (another session's
    staged-not-committed work, say) must never earn a link in a baked, committed
    catalog. A catalog describes the repository, not the working directory."""

    def test_a_staged_but_uncommitted_note_never_earns_a_link(self):
        git_tmp, git_vault = make_vault({
            "10-Projects/gamma/Overview.md":
                "---\ntype: overview\nstatus: open\ncreated: 2026-08-01\n---\nGamma.\n",
        })
        self.addCleanup(shutil.rmtree, git_tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q", git_vault], check=True)
        subprocess.run(["git", "-C", git_vault, "config", "user.email", "t@example.com"],
                        check=True)
        subprocess.run(["git", "-C", git_vault, "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", git_vault, "add", "10-Projects/gamma/Overview.md"],
                        check=True)
        subprocess.run(["git", "-C", git_vault, "commit", "-q", "-m", "gamma overview"],
                        check=True)
        write(os.path.join(git_vault, "10-Projects", "gamma", "Sessions", "untracked.md"),
              "---\ntype: session-log\nstatus: closed\ncreated: 2026-08-27\n---\n"
              "Written to disk, staged but never committed.\n")
        subprocess.run(["git", "-C", git_vault, "add",
                         "10-Projects/gamma/Sessions/untracked.md"], check=True)
        code, out = run(["bake", "--vault", git_vault])
        self.assertEqual(code, 0,
                         "bake in a git-tracked vault exited %d: %s" % (code, out[:300]))
        gamma_path = os.path.join(git_vault, "10-Projects", "gamma", "Catalog.md")
        self.assertTrue(os.path.isfile(gamma_path), "bake did not create %s" % gamma_path)
        gamma = read(gamma_path)
        # Both directions, or the check is vacuous: a filter bug that drops
        # everything (as a broken pathspec silently does) would still pass a
        # check that only looks for the excluded name's absence.
        self.assertIn("[[10-Projects/gamma/Overview|Overview]]", gamma,
                      "catalog dropped the COMMITTED note it must include: %s" % gamma[:400])
        self.assertNotIn("untracked", gamma,
                         "catalog linked to a staged-not-committed note: %s" % gamma[:400])


def _lock_json(holder, iso):
    return '{"session": "%s", "acquired": "%s", "note": "under test"}' % (holder, iso)


def _iso(offset_seconds=0):
    from datetime import datetime, timedelta, timezone
    t = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class BakeRefusesAForeignWriter(unittest.TestCase):
    """The bake keeps a REFUSAL where the pre-commit hook only warns, and the
    asymmetry is the point rather than an inconsistency. A bake replaces generated
    files wholesale, so its blast radius is exactly the bake and a refusal matches
    it. A commit's danger is sweeping someone else's staged files, which an
    explicit pathspec already avoids, so blocking every commit would guard far
    more than the act."""

    def _git_vault(self):
        tmp, vault = make_vault({
            "10-Projects/gamma/Overview.md":
                "---\ntype: overview\nstatus: open\ncreated: 2026-08-01\n---\nGamma.\n",
        })
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q", vault], check=True)
        subprocess.run(["git", "-C", vault, "config", "user.email", "t@example.com"], check=True)
        subprocess.run(["git", "-C", vault, "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", vault, "add", "-A"], check=True)
        subprocess.run(["git", "-C", vault, "commit", "-q", "-m", "gamma"], check=True)
        return vault, os.path.join(vault, ".git", "vault-writer.lock")

    def _bake(self, vault, session):
        env = dict(os.environ)
        env["CLAUDE_SESSION_ID"] = session
        p = subprocess.run([sys.executable, TOOL, "bake", "--vault", vault], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")

    def test_a_live_foreign_lock_refuses_the_bake_and_names_the_holder(self):
        vault, lockpath = self._git_vault()
        write(lockpath, _lock_json("peer-session-xyz", _iso()))
        code, out = self._bake(vault, "me")
        self.assertEqual(code, 75, "bake against a live foreign lock exited %d: %s"
                         % (code, out[:300]))
        self.assertIn("peer-session-xyz", out,
                      "the refusal did not name the holder: %s" % out[:300])
        self.assertTrue(os.path.exists(lockpath),
                        "a refused bake removed the OTHER session's lock")

    def test_my_own_lock_does_not_refuse_my_bake(self):
        vault, lockpath = self._git_vault()
        write(lockpath, _lock_json("me", _iso()))
        code, out = self._bake(vault, "me")
        self.assertEqual(code, 0, "my own lock blocked my bake: %s" % out[:300])

    def test_a_stale_lock_does_not_wedge_the_vault(self):
        vault, lockpath = self._git_vault()
        write(lockpath, _lock_json("dead-session", _iso(-99999)))
        code, out = self._bake(vault, "me")
        self.assertEqual(code, 0, "a stale lock still blocked the bake: %s" % out[:300])

    def test_no_lock_bakes_normally(self):
        vault, _ = self._git_vault()
        code, out = self._bake(vault, "me")
        self.assertEqual(code, 0, "bake with no lock exited %d: %s" % (code, out[:300]))

    def test_an_unreadable_lock_is_not_given_a_stricter_reading_than_its_owner(self):
        # bm_vault_lock treats a corrupt lock as no lock. This reader must not
        # invent a stricter rule, or the two disagree about one file.
        vault, lockpath = self._git_vault()
        write(lockpath, "this is not json at all")
        code, out = self._bake(vault, "me")
        self.assertEqual(code, 0, "a corrupt lock was read as a live writer: %s" % out[:300])


class PrecommitHookWarnsAndNeverBlocks(unittest.TestCase):
    """Every branch of warning_for(), plus the property that matters most: this
    hook exits 0 on every path. A warning hook that can fail a commit is a
    blocking hook with an inconsistent temper."""

    def setUp(self):
        import importlib.util
        path = os.path.join(os.path.dirname(TOOL_DIR), "scripts",
                            "bm_vault_precommit_hook.py")
        spec = importlib.util.spec_from_file_location("bm_vault_precommit_hook", path)
        self.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hook)
        self.script = path
        self.tmp = tempfile.mkdtemp(prefix="bm-vault-precommit-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        os.makedirs(os.path.join(self.tmp, ".git"))
        self.lockpath = os.path.join(self.tmp, ".git", "vault-writer.lock")

    def test_a_live_foreign_lock_produces_a_warning_naming_the_holder(self):
        write(self.lockpath, _lock_json("peer-session-xyz", _iso()))
        text = self.hook.warning_for(self.tmp, me="me")
        self.assertIsNotNone(text, "no warning for a live foreign lock")
        self.assertIn("peer-session-xyz", text)
        self.assertIn("pathspec", text,
                      "the warning did not tell the reader what to do instead: %s" % text)
        self.assertIn("Not blocking", text,
                      "the warning did not say it is not blocking: %s" % text)

    def test_my_own_lock_produces_no_warning(self):
        write(self.lockpath, _lock_json("me", _iso()))
        self.assertIsNone(self.hook.warning_for(self.tmp, me="me"))

    def test_a_stale_lock_produces_no_warning(self):
        write(self.lockpath, _lock_json("dead", _iso(-99999)))
        self.assertIsNone(self.hook.warning_for(self.tmp, me="me"))

    def test_no_lock_produces_no_warning(self):
        self.assertIsNone(self.hook.warning_for(self.tmp, me="me"))

    def test_a_corrupt_lock_produces_no_warning_matching_its_owner(self):
        write(self.lockpath, "not json")
        self.assertIsNone(self.hook.warning_for(self.tmp, me="me"))

    def test_the_hook_exits_zero_even_when_it_warns(self):
        # THE LOAD BEARING PROPERTY. Run as a real process, not a function call.
        write(self.lockpath, _lock_json("peer-session-xyz", _iso()))
        env = dict(os.environ)
        env["CLAUDE_SESSION_ID"] = "me"
        p = subprocess.run([sys.executable, self.script], cwd=self.tmp, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        err = p.stderr.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 0,
                         "the warning hook blocked a commit, exit %d" % p.returncode)
        self.assertIn("peer-session-xyz", err, "it exited 0 but said nothing: %r" % err)

    def test_a_missing_lock_module_does_not_break_every_commit(self):
        self.assertIsNone(self.hook._load_lock_module("/nonexistent/bm_vault_lock.py"))

    def test_all_three_readers_agree_on_who_this_session_is(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("bm_vault_catalog", TOOL)
        cat = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cat)
        os.environ["CLAUDE_SESSION_ID"] = "agreement-check"
        try:
            self.assertEqual(self.hook._session_label(), cat._session_label(),
                             "the hook and the baker would not recognise the same lock as "
                             "their own")
        finally:
            del os.environ["CLAUDE_SESSION_ID"]


class BakePreservesStableId(unittest.TestCase):
    """A bake must read back an id: assigned by bm_vault_ids, like created:.

    Observed on the real vault 2026-08-30: assign added id: to 4 baked catalogs,
    the next bake stripped them, and the id check and the staleness check then
    oscillated forever. The id is identity, not content.
    """

    def setUp(self):
        self.tmp, self.vault = make_vault(FIXTURE)
        code, out = run(["bake", "--vault", self.vault])
        self.assertEqual(code, 0, out[:300])
        self.alpha_path = os.path.join(self.vault, "10-Projects", "alpha", "Catalog.md")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _inject_id(self, path, note_id):
        text = read(path)
        self.assertTrue(text.startswith("---\n"), "no frontmatter to inject into")
        write(path, text.replace("---\n", "---\nid: %s\n" % note_id, 1))

    def test_a_rebake_keeps_an_assigned_id(self):
        self._inject_id(self.alpha_path, "n-cat0123456789ab")
        code, out = run(["bake", "--vault", self.vault])
        self.assertEqual(code, 0, out[:300])
        self.assertIn("id: n-cat0123456789ab", read(self.alpha_path),
                      "rebake stripped the assigned id: field")

    def test_check_agrees_with_a_rebaked_id_bearing_catalog(self):
        self._inject_id(self.alpha_path, "n-cat0123456789ab")
        code, out = run(["bake", "--vault", self.vault])
        self.assertEqual(code, 0, out[:300])
        code, out = run(["check", "--vault", self.vault])
        self.assertEqual(code, 0,
                         "check called an id-bearing freshly baked catalog stale: %s"
                         % out[:300])

    def test_a_first_bake_emits_no_id_line(self):
        text = read(self.alpha_path)
        self.assertNotIn("\nid:", text.split("---\n\n")[0],
                         "first bake invented an id: it had nothing to read back")

    def test_a_rebake_keeps_the_failures_catalog_id_too(self):
        # QA finding: the Failures-by-Symptom.md call site was code-identical but
        # untested; a mutation reverting only that call site survived the suite.
        fpath = os.path.join(self.vault, "40-Failures", "Failures-by-Symptom.md")
        self._inject_id(fpath, "n-fail0123456789ab")
        code, out = run(["bake", "--vault", self.vault])
        self.assertEqual(code, 0, out[:300])
        self.assertIn("id: n-fail0123456789ab", read(fpath),
                      "rebake stripped the failures catalog's id: field")

    def test_bake_preserves_the_id_the_sibling_tool_actually_writes(self):
        # QA finding: the oscillation was an interaction between two tools, and
        # injecting the id by hand pins only this author's assumption about where
        # the sibling puts it. Use bm_vault_ids.add_id itself, so a change in the
        # sibling's placement re-breaks this test rather than passing silently.
        import importlib.util
        ids_path = os.path.join(os.path.dirname(TOOL), "bm_vault_ids.py")
        spec = importlib.util.spec_from_file_location("bm_vault_ids", ids_path)
        ids = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ids)
        text = read(self.alpha_path)
        write(self.alpha_path, ids.add_id(text, "n-abcdef0123456789"))
        code, out = run(["bake", "--vault", self.vault])
        self.assertEqual(code, 0, out[:300])
        self.assertIn("id: n-abcdef0123456789", read(self.alpha_path),
                      "rebake dropped the id the sibling tool wrote")

    def test_a_malformed_id_value_is_preserved_not_silently_dropped(self):
        # QA finding: an id value with a space read as absent on both sides and
        # was silently dropped on the next bake, the exact loss this fix exists
        # to prevent. Preservation is unconditional; validity is the id tool's
        # check to flag, not the baker's to erase.
        self._inject_id(self.alpha_path, "n-abc def")
        code, out = run(["bake", "--vault", self.vault])
        self.assertEqual(code, 0, out[:300])
        self.assertIn("id: n-abc def", read(self.alpha_path),
                      "rebake silently dropped a malformed id value")


if __name__ == "__main__":
    unittest.main(verbosity=1)
