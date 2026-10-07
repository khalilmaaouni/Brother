"""Drives cut_preflight.py both ways on canned subprocess answers: every check
is shown refusing on the defect it exists for and passing without it. Each
case below is one of the refusals the 1.0.21 cut met late on 2026-09-20."""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_preflight as P  # noqa: E402

NOW = datetime.datetime(2026, 9, 20, 15, 0, 0)


class Runner(object):
    """Answers by the script or git verb in the command; records every call."""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.calls = []

    def __call__(self, cmd, **kw):
        key = os.path.basename(cmd[1]) if cmd[0] in (sys.executable, "sh") else " ".join(cmd[:2])
        self.calls.append((key, list(cmd), kw))
        code, out = self.answers.get(key, (0, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")


def rows(**verdicts):
    base = [{"id": "restore-drill", "title": "Restore drill", "critical": True,
             "verdict": "PASS"},
            {"id": "reproducible-release-artifact", "title": "Reproducible release artifact",
             "critical": False, "verdict": "FAIL"}]
    for r in base:
        r["verdict"] = verdicts.get(r["id"].replace("-", "_"), r["verdict"])
    return json.dumps({"rows": base})


class TheClocksAreReadAtTheHorizon(unittest.TestCase):
    def test_an_expired_exception_refuses(self):
        r = Runner({"battery_exception_audit.py": (1, "EXPIRED release-note-perturb\n")})
        verdict, name, detail = P.check_exceptions(".", NOW, r)
        self.assertEqual((verdict, name), (P.REFUSED, "exceptions"))
        self.assertIn("EXPIRED", detail)

    def test_the_audit_is_asked_about_the_horizon_date_not_today(self):
        r = Runner()
        P.check_exceptions(".", NOW + datetime.timedelta(hours=30), r)
        cmd = r.calls[0][1]
        self.assertEqual(cmd[cmd.index("--now") + 1], "2026-09-21")

    def test_an_unreadable_expectations_file_is_no_data_not_ok(self):
        r = Runner({"battery_exception_audit.py": (2, "NO-DATA\n")})
        self.assertEqual(P.check_exceptions(".", NOW, r)[0], P.NODATA)

    def test_the_pack_must_still_be_fresh_when_the_cut_ends(self):
        r = Runner()
        self.assertEqual(P.check_pack_clock(".", 6, r)[0], P.OK)
        cmd = r.calls[0][1]
        self.assertEqual(cmd[cmd.index("--max-age-hours") + 1], "18")

    def test_a_pack_that_lapses_mid_cut_refuses(self):
        r = Runner({"close_ceremony_check.py": (1, "FAIL: pack is 19.0 hours old\n")})
        verdict, _, detail = P.check_pack_clock(".", 6, r)
        self.assertEqual(verdict, P.REFUSED)
        self.assertIn("19.0 hours", detail)

    def test_a_stale_restore_drill_refuses_and_names_the_row(self):
        r = Runner({"readiness_gate.py": (1, rows(restore_drill="NO-DATA"))})
        verdict, _, detail = P.check_readiness_rows(".", NOW, r)
        self.assertEqual(verdict, P.REFUSED)
        self.assertIn("Restore drill (NO-DATA)", detail)

    def test_the_pre_bump_release_artifact_row_is_not_read(self):
        r = Runner({"readiness_gate.py": (1, rows())})
        self.assertEqual(P.check_readiness_rows(".", NOW, r)[0], P.OK)

    def test_unreadable_gate_json_is_no_data(self):
        r = Runner({"readiness_gate.py": (1, "Traceback\n")})
        self.assertEqual(P.check_readiness_rows(".", NOW, r)[0], P.NODATA)


class ThePublicRemoteIsReadFirst(unittest.TestCase):
    def test_a_leftover_release_branch_refuses_and_names_it(self):
        r = Runner({"git ls-remote": (0, "cd2a18\trefs/heads/release/1.0.21\n")})
        verdict, _, detail = P.check_public_remote(".", "1.0.21", "https://x", r)
        self.assertEqual(verdict, P.REFUSED)
        self.assertIn("refs/heads/release/1.0.21", detail)

    def test_a_clean_remote_is_ok_and_both_refs_were_asked(self):
        r = Runner()
        self.assertEqual(P.check_public_remote(".", "1.0.21", "https://x", r)[0], P.OK)
        cmd = r.calls[0][1]
        self.assertIn("refs/heads/release/1.0.21", cmd)
        self.assertIn("refs/tags/v1.0.21", cmd)

    def test_an_unreachable_remote_is_no_data(self):
        r = Runner({"git ls-remote": (128, "fatal: unable to access\n")})
        self.assertEqual(P.check_public_remote(".", "1.0.21", "https://x", r)[0], P.NODATA)


class TheChangelogTextIsScannedBeforeTheNoteExists(unittest.TestCase):
    # Built by concatenation: a contiguous key shaped literal in this file
    # would refuse this file's own push.
    KEY = "s" + "k-" + "A1b2C3d4E5f6G7h8I9j0K1l2"

    def test_a_key_shape_in_a_commit_subject_refuses_without_printing_it(self):
        r = Runner({"git log": (0, "fix: rotate %s\n" % self.KEY)})
        verdict, _, detail = P.check_changelog_text(".", "9674ed89", r)
        self.assertEqual(verdict, P.REFUSED)
        self.assertNotIn(self.KEY, detail)

    def test_a_branch_name_that_only_contains_the_letters_is_ok(self):
        r = Runner({"git log": (0, "Merge docs/or-ask-provenance-wbs-2026-09-19\n")})
        self.assertEqual(P.check_changelog_text(".", "9674ed89", r)[0], P.OK)

    def test_no_previous_release_is_no_data(self):
        self.assertEqual(P.check_changelog_text(".", None, Runner())[0], P.NODATA)


class TheVirginGate(unittest.TestCase):
    def test_a_red_on_the_export_tree_refuses_and_names_the_suites(self):
        r = Runner({"required_fast.sh": (1, "pass 30   fail 2   no-data 5\n"
                                            "FAILED: receipt-door export-public\n")})
        verdict, _, detail = P.check_virgin_gate(".", r, build=lambda d: None)
        self.assertEqual(verdict, P.REFUSED)
        self.assertIn("fail 2", detail)
        self.assertIn("receipt-door export-public", detail)

    def test_a_green_export_tree_is_ok(self):
        r = Runner({"required_fast.sh": (0, "pass 34   fail 0   no-data 3\n")})
        self.assertEqual(P.check_virgin_gate(".", r, build=lambda d: None)[0], P.OK)

    def test_no_summary_line_is_no_data_never_ok(self):
        r = Runner({"required_fast.sh": (0, "nothing useful\n")})
        self.assertEqual(P.check_virgin_gate(".", r, build=lambda d: None)[0], P.NODATA)

    def test_the_gate_runs_in_the_built_tree_with_an_empty_home(self):
        seen = {}
        r = Runner({"required_fast.sh": (0, "pass 1   fail 0   no-data 0\n")})
        P.check_virgin_gate(".", r, build=lambda d: seen.setdefault("tree", d))
        _, cmd, kw = r.calls[0]
        self.assertEqual(kw["cwd"], seen["tree"])
        self.assertTrue(cmd[1].startswith(seen["tree"]))
        home = kw["env"]["HOME"]
        self.assertNotEqual(home, os.path.expanduser("~"))
        self.assertIn("cut-preflight-home-", home)

    def test_no_git_identity_is_exported_because_the_runner_has_none(self):
        from unittest import mock
        r = Runner({"required_fast.sh": (0, "pass 1   fail 0   no-data 0\n")})
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "x", "GIT_COMMITTER_EMAIL": "y",
                                          "GIT_DIR": "/real/.git", "GIT_INDEX_FILE": "/real/index"}):
            P.check_virgin_gate(".", r, build=lambda d: None)
        leaked = [k for k in r.calls[0][2]["env"] if k.startswith("GIT_")]
        self.assertEqual(leaked, [])

    def test_a_tree_that_cannot_be_built_is_no_data(self):
        def boom(dest):
            raise RuntimeError("allowlist unreadable")
        self.assertEqual(P.check_virgin_gate(".", Runner(), build=boom)[0], P.NODATA)


class TheBuilderIsDeafToAGitHook(unittest.TestCase):
    """2026-09-20: launched from the pre-push hook, the exporter inherited
    GIT_DIR and built its tree from the wrong repository, and the check then
    read every test as not shipped, which is a green over a broken build."""

    def test_git_location_is_gone_inside_the_block_and_back_after(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"GIT_DIR": "/real/.git", "GIT_INDEX_FILE": "/i"}):
            with P.no_git_location():
                self.assertNotIn("GIT_DIR", os.environ)
                self.assertNotIn("GIT_INDEX_FILE", os.environ)
            self.assertEqual(os.environ["GIT_DIR"], "/real/.git")

    def test_the_real_builder_runs_the_exporter_without_it_and_checks_the_tree(self):
        from unittest import mock
        seen = {}
        fake = mock.Mock()
        fake.load_allowlist.return_value = []
        fake.build_orphan_commit.side_effect = \
            lambda dest, allow, root: seen.update(git_dir=os.environ.get("GIT_DIR"))
        dest = tempfile.mkdtemp(prefix="test-builder-")
        self.addCleanup(shutil.rmtree, dest, True)
        with mock.patch.dict(sys.modules, {"export_public": fake}), \
                mock.patch.dict(os.environ, {"GIT_DIR": "/real/.git"}):
            with self.assertRaises(RuntimeError) as ctx:   # nothing was built
                P.export_tree_builder(".")(dest)
        self.assertIsNone(seen["git_dir"])
        self.assertIn("not an export tree", str(ctx.exception))

    def test_the_builder_reads_the_allowlist_of_the_root_it_builds_for(self):
        """D13 (2026-10-02): the landing runs this builder from a frozen copy of the base commit against the landing
        tree, and which files the public repository receives is that tree's own allowlist, at the exporter's own
        relative path under it. For this checkout the path is the exporter's default, byte for byte."""
        from unittest import mock
        fake = mock.Mock()
        fake.ROOT = "/frozen"
        fake.DEFAULT_ALLOWLIST = "/frozen/docs/plan/EXPORT-ALLOWLIST.txt"
        fake.load_allowlist.return_value = []
        fake.build_orphan_commit.side_effect = lambda dest, allow, root: None
        dest = tempfile.mkdtemp(prefix="test-builder-allow-")
        self.addCleanup(shutil.rmtree, dest, True)
        with mock.patch.dict(sys.modules, {"export_public": fake}):
            with self.assertRaises(RuntimeError):   # nothing was built, which is not this test's question
                P.export_tree_builder("/landing")(dest)
            fake.load_allowlist.assert_called_once_with("/landing/docs/plan/EXPORT-ALLOWLIST.txt")
            fake.load_allowlist.reset_mock()
            with self.assertRaises(RuntimeError):
                P.export_tree_builder("/frozen")(dest)
            fake.load_allowlist.assert_called_once_with("/frozen/docs/plan/EXPORT-ALLOWLIST.txt")


class TheBuilderResolvesTheExporterThroughItsOwnRoot(unittest.TestCase):
    """D13, review 14 (2026-10-02): scripts/export_public.py is the one module a frozen check imports that a build may
    still write (open unit T1 edits it), so the frozen builder must resolve it beside ITSELF, never in the landing tree it
    runs against. Driven the way the lander drives it: a script run from this checkout (the frozen copy's shape, its own
    directory first on sys.path and no cwd entry) with the cwd a tree whose scripts/export_public.py is a tripwire."""

    def test_the_landing_trees_export_public_is_never_imported(self):
        box = tempfile.mkdtemp(prefix="test-frozen-exporter-")
        self.addCleanup(shutil.rmtree, box, True)
        landing = os.path.join(box, "landing")
        marker = os.path.join(box, "landing-copy-imported.marker")
        os.makedirs(os.path.join(landing, "scripts"))
        with open(os.path.join(landing, "scripts", "export_public.py"), "w", encoding="utf-8") as fh:
            fh.write("open(%r, 'w').write('the landing copy ran')\nraise SystemExit(97)\n" % marker)
        driver = os.path.join(box, "driver", "frozen_check.py")
        os.makedirs(os.path.dirname(driver))
        with open(driver, "w", encoding="utf-8") as fh:
            fh.write("import sys\nsys.path.insert(0, %r)\nimport cut_preflight\ncut_preflight.export_tree_builder(%r)\n"
                     "print(sys.modules['export_public'].__file__)\n" % (HERE, landing))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PYTHONPATH"))}
        r = subprocess.run([sys.executable, "-B", driver], cwd=landing, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(marker), "the landing tree's export_public.py was imported")
        self.assertEqual(os.path.dirname(os.path.realpath(r.stdout.strip())), os.path.realpath(HERE), r.stdout)


class CheapestFirst(unittest.TestCase):
    MARKET = json.dumps({"plugins": [{"name": "alpha", "version": "3.4.5",
                                      "source": {"path": "products/alpha"}}]})

    def _all(self, answers, root=None, version="1.0.21"):
        built = []

        class TagRunner(Runner):
            """check_public_remote reads any ls-remote line as a leftover of
            this version, so only the --tags listing answers with tags."""
            def __call__(self, cmd, **kw):
                if list(cmd[:3]) == ["git", "ls-remote", "--tags"]:
                    self.calls.append(("git ls-remote --tags", list(cmd), kw))
                    return subprocess.CompletedProcess(
                        cmd, 0, "abc\trefs/tags/v1.0.20\n", "")
                return Runner.__call__(self, cmd, **kw)

        r = TagRunner(dict({"readiness_gate.py": (0, rows()),
                            "required_fast.sh": (0, "pass 34   fail 0   no-data 3\n"),
                            "git rev-parse": (0, "abc\n"),
                            "git show": (0, self.MARKET),
                            "git diff": (0, "")},
                           **answers))

        def build(dest):
            built.append(dest)
            os.makedirs(os.path.join(dest, ".claude-plugin"))
            with open(os.path.join(dest, ".claude-plugin", "marketplace.json"), "w") as fh:
                fh.write(self.MARKET)
        if root is None:
            # Its own root: the tree this runs in may ship no docs/releases.
            root = tempfile.mkdtemp(prefix="test-cut-preflight-")
            self.addCleanup(shutil.rmtree, root, True)
            os.makedirs(os.path.join(root, "docs", "releases"))
            with open(os.path.join(root, "docs", "releases", "1.0.20.md"), "w") as fh:
                fh.write("Cut from hub commit `9674ed8955ad`\n")
        # CV1.d: the release-plan row reads NO-DATA when its script is absent; the scripted runner answers its exit.
        os.makedirs(os.path.join(root, "scripts"))
        open(os.path.join(root, "scripts", "gen_release_plan.py"), "w").close()
        res = P.run_all(root, version, remote="https://x", runner=r, now=NOW,
                        build=build)
        return res, built

    def test_the_previous_cut_is_read_from_the_newest_release_note(self):
        res, _ = self._all({})
        self.assertIn("9674ed8955ad", [d for v, n, d in res if n == "changelog-text"][0])

    def test_all_green_is_exit_zero_and_the_virgin_gate_ran(self):
        res, built = self._all({})
        self.assertEqual(P.exit_code(res), P.EXIT_OK, res)
        # One export tree for the plugin bump gate, one for the virgin gate.
        self.assertEqual(len(built), 2)
        self.assertEqual([n for _, n, _ in res][-2:], ["plugin-bumps", "virgin-gate"])

    def test_a_changed_plugin_with_no_bump_refuses_and_spares_the_virgin_gate(self):
        res, built = self._all({"git diff": (0, "products/alpha/tool.py\n")})
        self.assertEqual(P.exit_code(res), P.EXIT_REFUSED, res)
        bump = [(v, d) for v, n, d in res if n == "plugin-bumps"][0]
        self.assertEqual(bump[0], P.REFUSED)
        self.assertIn("alpha", bump[1])
        self.assertEqual(len(built), 1, "the minutes long gate ran after the refusal")

    def test_an_unreadable_previous_release_is_exit_two_never_zero(self):
        res, _ = self._all({"git show": (128, "fatal: bad object\n")})
        self.assertEqual(P.exit_code(res), P.EXIT_NODATA, res)

    def test_a_one_second_red_costs_one_second(self):
        res, built = self._all({"battery_exception_audit.py": (1, "EXPIRED x\n")})
        self.assertEqual(P.exit_code(res), P.EXIT_REFUSED)
        self.assertEqual(built, [], "the minutes long gate ran after a cheap refusal")

    def test_no_data_alone_is_exit_two_never_zero(self):
        self.assertEqual(P.exit_code([(P.OK, "a", ""), (P.NODATA, "b", "")]), P.EXIT_NODATA)
        self.assertEqual(P.exit_code([(P.NODATA, "b", ""), (P.REFUSED, "c", "")]),
                         P.EXIT_REFUSED)


def declares(version):
    """The text of a marketplace file whose manifests declare `version`."""
    return json.dumps({"metadata": {"version": version}, "plugins": []})


class TheUncutDraftOfTheVersionBeingCut(unittest.TestCase):
    """Measured 2026-10-05 on the 1.1.0 candidate. Its manifests declared the
    new version ahead of the tag, so release_invariant.py required the note of
    that version to exist, and precut_review.py forbade that note a cut commit
    line before the cut. previous_cut_commit read the draft as the newest
    note, found no line, and the changelog check read NO-DATA: a preflight
    that could never clear. Every fixture below is that tree's shape (manifests
    at the version being cut, its draft without the line, an older note with
    it) with ONE thing changed, so only the guard a case names can answer."""

    MARKET = CheapestFirst.MARKET
    CUTTING = "1.0.21"
    CUT = "9674ed8955ad"
    STAMPED = "# Brother 1.0.20\n\nCut from hub commit `%s` (hub, private).\n" % CUT
    DRAFT = "# Brother 1.0.21\n\nWhat moved, drafted before the cut.\n"

    def tree(self, manifest=declares(CUTTING), notes=None):
        root = tempfile.mkdtemp(prefix="test-cut-preflight-draft-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "docs", "releases"))
        os.makedirs(os.path.join(root, ".claude-plugin"))
        if manifest is not None:
            with open(os.path.join(root, ".claude-plugin", "marketplace.json"), "w") as fh:
                fh.write(manifest)
        if notes is None:
            notes = {"1.0.21.md": self.DRAFT, "1.0.20.md": self.STAMPED}
        for name, text in notes.items():
            with open(os.path.join(root, "docs", "releases", name), "w") as fh:
                fh.write(text)
        return root

    def changelog_row(self, root):
        res, _ = CheapestFirst._all(self, {}, root=root, version=self.CUTTING)
        return [(v, d) for v, n, d in res if n == "changelog-text"][0], P.exit_code(res)

    # The defect, at the seam main() calls.
    def test_the_draft_is_looked_past_and_the_preflight_clears(self):
        (verdict, detail), code = self.changelog_row(self.tree())
        self.assertEqual(verdict, P.OK, detail)
        self.assertIn(self.CUT, detail)
        self.assertEqual(code, P.EXIT_OK)

    def test_the_previous_cut_is_the_newest_note_that_is_not_the_draft(self):
        self.assertEqual(P.previous_cut_commit(self.tree(), self.CUTTING), self.CUT)

    # One guard per case. Unknown blocks.
    def test_without_the_version_being_cut_nothing_is_looked_past(self):
        self.assertIsNone(P.previous_cut_commit(self.tree()))

    def test_another_versions_note_without_its_line_still_blocks(self):
        older_bare = self.tree(notes={"1.0.21.md": self.DRAFT,
                                      "1.0.20.md": "# Brother 1.0.20\n\nNo line here.\n"})
        self.assertIsNone(P.previous_cut_commit(older_bare, self.CUTTING))
        # A newer note that is not the version being cut is no draft of this cut.
        newer_bare = self.tree(notes={"1.1.0.md": "# Brother 1.1.0\n\nDrafted early.\n",
                                      "1.0.21.md": self.DRAFT, "1.0.20.md": self.STAMPED})
        self.assertIsNone(P.previous_cut_commit(newer_bare, self.CUTTING))
        (verdict, _), code = self.changelog_row(newer_bare)
        self.assertEqual((verdict, code), (P.NODATA, P.EXIT_NODATA))

    def test_a_note_of_the_version_being_cut_that_names_a_cut_is_read_not_skipped(self):
        named = "# Brother 1.0.21\n\nCut from hub commit `aaaaaaaaaaaa` (hub, private).\n"
        root = self.tree(notes={"1.0.21.md": named, "1.0.20.md": self.STAMPED})
        self.assertEqual(P.previous_cut_commit(root, self.CUTTING), "aaaaaaaaaaaa")

    def test_a_draft_that_carries_the_cut_line_in_any_shape_is_no_draft(self):
        for line in ("Cut from hub commit `PENDING`", "Cut from hub commit", "x Cut from hub commit `zz`"):
            with self.subTest(line=line):
                root = self.tree(notes={"1.0.21.md": self.DRAFT + "\n" + line + "\n",
                                        "1.0.20.md": self.STAMPED})
                self.assertIsNone(P.previous_cut_commit(root, self.CUTTING))

    def test_the_manifests_must_declare_the_version_being_cut(self):
        for label, manifest in (("another version", declares("1.0.20")),
                                ("no file", None),
                                ("not json", "{not json"),
                                ("no metadata", json.dumps({"plugins": []})),
                                ("metadata is a list", json.dumps({"metadata": []})),
                                ("a number", json.dumps({"metadata": {"version": 1.0}})),
                                ("empty", "")):
            with self.subTest(manifest=label):
                root = self.tree(manifest=manifest)
                self.assertIsNone(P.previous_cut_commit(root, self.CUTTING))
                self.assertFalse(P.is_uncut_draft(root, self.CUTTING, self.DRAFT))
        (verdict, _), code = self.changelog_row(self.tree(manifest=declares("1.0.20")))
        self.assertEqual((verdict, code), (P.NODATA, P.EXIT_NODATA))

    #: The real draft's shape: hand written sections, the generator's none.
    REAL_SHAPE = ("# Brother 1.0.21\n\n"
                  "Umbrella version 1.0.21 reconciles the declared release identity with the\n"
                  "bytes this repository now ships.\n\n"
                  "## What moved\n\n- The canonical umbrella version moves.\n\n"
                  "## What this note does not claim\n\n"
                  "- No tag, no GitHub Release and no installed copy verification.\n"
                  "- The export manifest for this version is produced by\n"
                  "  `scripts/refresh_cut.py` on the settled candidate.\n\n"
                  "## Known limits\n\nA limit the owner ruled on.\n")
    #: A finished note in the generator's shape, and its cut line.
    CUT_LINE = "Cut from hub commit `%s` (hub, private).\n\n" % ("a" * 40)
    DIGEST_LINE = "Export manifest digest `%s` over 3 exported file(s).\n\n" % ("b" * 64)
    FINISHED = ("# Brother 1.0.21\n\n## Source revision\n\n" + CUT_LINE + DIGEST_LINE
                + "## Changelog\n\n- #460 wbs/one\n")
    #: Review round 1: every one of these read as the draft, so a finished
    #: note damaged by hand was looked past or had its body replaced.
    DAMAGED = {
        "the cut line deleted": FINISHED.replace(CUT_LINE, ""),
        "the cut line in lower case": FINISHED.replace("Cut from hub", "cut from hub"),
        "the cut line double spaced": FINISHED.replace("Cut from hub commit",
                                                       "Cut  from  hub  commit"),
        "the cut line wrapped": FINISHED.replace("Cut from hub commit", "Cut from hub\ncommit"),
        "only the digest line left": FINISHED.replace(CUT_LINE, "").replace(
            "## Source revision\n\n", ""),
        "only the section header left": FINISHED.replace(CUT_LINE, "").replace(DIGEST_LINE, ""),
        "the note emptied": "",
        "the note blanked to white space": " \n\t\n",
    }

    def test_the_real_drafts_shape_is_still_the_draft(self):
        root = self.tree(notes={"1.0.21.md": self.REAL_SHAPE, "1.0.20.md": self.STAMPED})
        self.assertIs(P.is_uncut_draft(root, self.CUTTING, self.REAL_SHAPE), True)
        self.assertEqual(P.previous_cut_commit(root, self.CUTTING), self.CUT)

    def test_a_damaged_finished_note_is_never_the_draft(self):
        for label, text in sorted(self.DAMAGED.items(), key=lambda kv: kv[0]):
            with self.subTest(damage=label):
                root = self.tree(notes={"1.0.21.md": text, "1.0.20.md": self.STAMPED})
                self.assertIs(P.is_uncut_draft(root, self.CUTTING, text), False)
                # Not looked past: no cut can be read from it, so NO-DATA.
                self.assertIsNone(P.previous_cut_commit(root, self.CUTTING))
                (verdict, _), code = self.changelog_row(root)
                self.assertEqual((verdict, code), (P.NODATA, P.EXIT_NODATA))

    def test_the_marks_the_predicate_knows_are_the_generators_own(self):
        """The marks are literals here, so the predicate imports nothing and
        cannot raise; this pins them to the text the generator really writes."""
        import export_public
        import reproduce_export
        with open(os.path.join(HERE, "release_note_from_tree.py"), encoding="utf-8") as fh:
            generator = fh.read()
        self.assertEqual(P.GENERATED_NOTE_MARKS[0], P.CUT_COMMIT_MARK.lower())
        self.assertIn(P.CUT_COMMIT_MARK + " `", generator)
        self.assertTrue(reproduce_export.NOTE_STAMP_LINE_RE.search(P.CUT_COMMIT_MARK + " `x`"))
        self.assertIn(export_public.SOURCE_REVISION_HEADER.lower(), P.GENERATED_NOTE_MARKS)
        digest = "Export manifest digest `%s`" % ("0" * 64)
        self.assertTrue(reproduce_export.NOTE_DIGEST_MASK_RE.search(digest))
        self.assertIn("Export manifest digest `", generator)
        self.assertIn("export manifest digest", P.GENERATED_NOTE_MARKS)
        for mark in P.GENERATED_NOTE_MARKS:
            self.assertEqual(mark, " ".join(mark.split()).lower())

    def test_an_unreadable_note_blocks_and_is_never_looked_past(self):
        older = "# Brother 1.0.19\n\nCut from hub commit `bbbbbbbbbbbb` (hub, private).\n"
        for unreadable in ("1.0.21.md", "1.0.20.md"):
            for how in ("a directory", "not utf-8"):
                with self.subTest(unreadable=unreadable, how=how):
                    notes = {"1.0.21.md": self.DRAFT, "1.0.20.md": self.STAMPED,
                             "1.0.19.md": older}
                    del notes[unreadable]
                    root = self.tree(notes=notes)
                    path = os.path.join(root, "docs", "releases", unreadable)
                    if how == "a directory":
                        os.mkdir(path)
                    else:
                        with open(path, "wb") as fh:
                            fh.write(b"\xff\xfe a note in another encoding\n")
                    self.assertIsNone(P.previous_cut_commit(root, self.CUTTING))
                    (verdict, _), code = self.changelog_row(root)
                    self.assertEqual((verdict, code), (P.NODATA, P.EXIT_NODATA))

    def test_the_predicate_is_true_only_for_that_draft(self):
        root = self.tree()
        self.assertIs(P.is_uncut_draft(root, self.CUTTING, self.DRAFT), True)
        for bad in ((root, "1.0.20", self.DRAFT),      # not the version the manifests declare
                    (root, self.CUTTING, self.STAMPED),  # names a cut commit
                    (root, self.CUTTING, None), (root, self.CUTTING, b"draft"),
                    (root, None, self.DRAFT), (root, "", self.DRAFT), (root, 1, self.DRAFT),
                    (None, self.CUTTING, self.DRAFT), ("", self.CUTTING, self.DRAFT),
                    (os.path.join(root, "no-such-root"), self.CUTTING, self.DRAFT)):
            with self.subTest(args=bad[1:] + (bad[0] == root,)):
                self.assertIs(P.is_uncut_draft(*bad), False)
        # A declared version that is not text matches nothing, itself included.
        numbered = self.tree(manifest=json.dumps({"metadata": {"version": 1.0}}))
        self.assertIs(P.is_uncut_draft(numbered, 1.0, self.DRAFT), False)

    def test_an_empty_root_never_reads_the_working_directory(self):
        root = self.tree()
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(root)
        self.assertIs(P.is_uncut_draft(".", self.CUTTING, self.DRAFT), True)
        self.assertIs(P.is_uncut_draft("", self.CUTTING, self.DRAFT), False)

    def test_a_hostile_version_is_refused_at_the_door(self):
        for bad in ("", 7, ["1.0.21"], True):
            with self.assertRaises(ValueError):
                P.previous_cut_commit(self.tree(), bad)


class RunnerEnvIsTheSingleConditionsEntryPoint(unittest.TestCase):
    """R1.1: runner_env refuses hostile HOME state with ValueError, sets HOME
    to the caller's directory (never the operator's) and strips every GIT_
    variable, so a check runs in the world that judges it."""

    def _fresh_home(self):
        home = tempfile.mkdtemp(prefix="r11-home-")
        self.addCleanup(shutil.rmtree, home, True)
        return home

    def test_none_home_refused(self):
        self.assertRaises(ValueError, P.runner_env, None)

    def test_non_string_home_refused(self):
        for bad in (123, [], {}, b"/tmp/x", 0.0, True):
            self.assertRaises(ValueError, P.runner_env, bad)

    def test_empty_string_home_refused(self):
        self.assertRaises(ValueError, P.runner_env, "")

    def test_missing_home_refused(self):
        missing = os.path.join(tempfile.gettempdir(),
                               "r11-missing-%d" % os.getpid())
        self.assertRaises(ValueError, P.runner_env, missing)

    def test_file_instead_of_dir_refused(self):
        path = os.path.join(tempfile.gettempdir(),
                            "r11-file-%d" % os.getpid())
        with open(path, "w") as fh:
            fh.write("x")
        self.addCleanup(os.remove, path)
        self.assertRaises(ValueError, P.runner_env, path)

    def test_fresh_empty_dir_accepted_and_home_is_the_caller_home(self):
        home = self._fresh_home()
        env = P.runner_env(home)
        self.assertEqual(env["HOME"], home)
        self.assertNotEqual(env["HOME"], os.path.expanduser("~"))

    def test_operator_home_is_never_used(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"HOME": "/operator/home"}):
            home = self._fresh_home()
            env = P.runner_env(home)
        self.assertEqual(env["HOME"], home)

    def test_no_git_variables_survive(self):
        from unittest import mock
        home = self._fresh_home()
        with mock.patch.dict(os.environ, {"GIT_DIR": "/real/.git",
                                          "GIT_AUTHOR_NAME": "x",
                                          "GIT_INDEX_FILE": "/i"}):
            env = P.runner_env(home)
        leaked = [k for k in env if k.startswith("GIT_")]
        self.assertEqual(leaked, [])

    def test_stale_content_from_a_prior_run_is_cleaned(self):
        home = self._fresh_home()
        with open(os.path.join(home, "leftover"), "w") as fh:
            fh.write("x")
        P.runner_env(home)
        self.assertEqual(os.listdir(home), [])

    def test_already_done_home_reuse_blocked(self):
        home = self._fresh_home()
        with open(os.path.join(home, ".cut_preflight_used"), "w") as fh:
            fh.write("x")
        self.assertRaises(ValueError, P.runner_env, home)


if __name__ == "__main__":
    unittest.main()
