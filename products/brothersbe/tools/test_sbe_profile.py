#!/usr/bin/env python3
"""Profile fixtures: a module cannot leak into the default, a law cannot go unreachable.

The guarantee this file exists to hold, in one sentence: the default profile is the
safety floor plus design, implementation boundaries and verification, and every one of
the nineteen laws still resolves from SKILL.md's routing table whatever the profile is.

Every test here is CALIBRATED the way this repository requires: it copies the shipped
tree into a scratch directory, re-injects the exact defect the check exists to catch,
asserts the verdict goes FAIL naming the thing, restores the file, and asserts the
verdict goes back to PASS. A test that stays green with its guard removed is measuring
something else; each case below has been watched go red and green in that order, and
the restoration is asserted rather than assumed.

Run:
    python3 tools/test_sbe_profile.py
"""
import io, json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import sbe_profile as P                                    # noqa: E402

# The files a scratch copy needs for the profile surface to be checkable: the law
# file, the module index, every reference file a routing row can point at, the
# declaration, the hook whose emitters must be gated, and the tool itself.
COPY_DIRS = ("references", ".brothersbe")
COPY_FILES = (os.path.join("tools", "sbe_profile.py"),
              os.path.join("tools", "sbe_checks.py"),
              os.path.join("tools", "sbe_sessionstart.py"),
              "SKILL.md", "DIGEST.md")


def scratch():
    """A throwaway copy of the shipped profile surface. Never the real tree: a
    fixture that mutates the repository it is measuring reports on itself."""
    work = tempfile.mkdtemp(prefix="sbe-profile-")
    os.makedirs(os.path.join(work, "tools"))
    for d in COPY_DIRS:
        shutil.copytree(os.path.join(ROOT, d), os.path.join(work, d))
    for f in COPY_FILES:
        shutil.copy2(os.path.join(ROOT, f), os.path.join(work, f))
    return work


def verdicts(root, env=None):
    """{check name: (verdict, evidence)} from the REAL tool, run as a subprocess
    against `root`. Parsing the shipped report rather than calling the functions
    in-process is deliberate: the printed report is the interface CI and a human
    both read, so a change that broke it would pass an in-process test."""
    e = dict(os.environ)
    e.pop("SBE_PROFILE", None)
    e.pop("SBE_PROFILE_MODULES", None)
    e.update(env or {})
    r = subprocess.run([sys.executable, os.path.join(root, "tools", "sbe_profile.py"),
                        "check", "--root", root, "--strict"],
                       capture_output=True, text=True, env=e, timeout=120)
    out = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] in ("PASS", "FAIL", "NO-DATA"):
            out[parts[0]] = (parts[1], parts[2])
    return out, r.returncode, r.stdout


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


class ProfileFixture(unittest.TestCase):
    def setUp(self):
        self.work = scratch()
        self.skill = os.path.join(self.work, "SKILL.md")
        self.index = os.path.join(self.work, "references", "modules.md")
        self.hook = os.path.join(self.work, "tools", "sbe_sessionstart.py")
        self.decl = os.path.join(self.work, ".brothersbe", "profile.json")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def calibrate(self, path, mutate, check, needle):
        """Re-inject a defect, watch the named check go FAIL naming `needle`,
        restore, watch it go back to PASS. Returns nothing; every assertion that
        matters is made here, including the restoration."""
        original = read(path)
        got, code, out = verdicts(self.work)
        self.assertEqual("PASS", got.get(check, ("missing",))[0],
                         "the unmutated scratch copy must start green: %s" % out)
        self.assertEqual(0, code, "an unmutated copy must not block --strict")
        write(path, mutate(original))
        got, code, out = verdicts(self.work)
        self.assertEqual("FAIL", got.get(check, ("missing",))[0],
                         "the re-injected defect did not turn %s red, so that check is "
                         "measuring something else:\n%s" % (check, out))
        self.assertIn(needle, got[check][1],
                      "%s failed without naming the defect (%r)" % (check, got[check][1]))
        self.assertEqual(2, code, "--strict must exit 2 while a check FAILs")
        write(path, original)
        self.assertEqual(original, read(path), "the fixture did not restore its own file")
        got, code, _ = verdicts(self.work)
        self.assertEqual("PASS", got.get(check, ("missing",))[0],
                         "restoring the file did not turn %s green again" % check)
        self.assertEqual(0, code)


class TestModuleLeak(ProfileFixture):
    def test_a_module_row_moved_into_the_law_file_fails(self):
        """THE defect this lane exists to prevent: somebody moves an optional
        module's routing row into SKILL.md, the default profile silently grows a
        surface, and nothing says so. The mutation is the smallest real version of
        it, one row appended to the law file's routing table."""
        def mutate(text):
            return text + ("\n| the team is coordinating work | `references/team-execution.md` "
                           "| team execution |\n")
        self.calibrate(self.skill, mutate, "profile-module-isolation",
                       "references/team-execution.md")

    def test_naming_a_module_file_anywhere_in_the_law_file_fails(self):
        """Not only in the table. A module file named in SKILL.md's prose is a
        surface the default profile pulls in just as surely, because the reader
        follows the path they were given and no table decided it."""
        def mutate(text):
            return text + "\nSee references/module-telemetry.md for the ledger.\n"
        self.calibrate(self.skill, mutate, "profile-module-isolation",
                       "references/module-telemetry.md")

    def test_an_ungated_startup_emitter_fails(self):
        """The hook is the other way a module reaches the default: an emitter that
        runs without asking the profile prints into every session's context. The
        mutation removes the guard around the telemetry nags and leaves the emitter
        exactly where it was."""
        def mutate(text):
            return text.replace(
                '    if _gate_open("enabled telemetry"):\n'
                '        head_parts.append(_tool("sbe_telemetry.py", "startup-nags"))\n',
                '    head_parts.append(_tool("sbe_telemetry.py", "startup-nags"))\n')
        original = read(self.hook)
        self.assertIn("enabled telemetry", original, "the shipped hook lost its own guard")
        self.calibrate(self.hook, mutate, "profile-module-isolation", "startup-nags")

    def test_the_default_profile_enables_no_module(self):
        """The shipped declaration, read by the real tool against the real tree."""
        name, enabled, problems = P.declared(ROOT)
        self.assertEqual("default", name)
        self.assertEqual([], enabled, "the shipped default profile enables %s" % enabled)
        self.assertEqual([], problems, "the shipped declaration has problems: %s" % problems)
        known, problem = P.module_ids(ROOT)
        self.assertEqual("", problem)
        self.assertEqual(["policy", "release", "team", "telemetry", "vault"], sorted(known),
                         "the five optional modules the assessment named are the five declared")


class TestLawReachability(ProfileFixture):
    def test_deleting_a_routing_row_makes_its_laws_unreachable_and_fails(self):
        """A shorter law file that loses a law is worse than a long one. Deleting
        the row that routes L7 to L10 is the exact accident: the file is still on
        disk, and nothing points at it any more."""
        def mutate(text):
            keep = [ln for ln in text.splitlines(True)
                    if "references/laws-hard-gates.md" not in ln]
            return "".join(keep)
        self.calibrate(self.skill, mutate, "profile-law-routing", "L7")

    def test_a_row_pointing_at_a_missing_file_fails(self):
        def mutate(text):
            return text.replace("`references/laws-decision-tables.md`",
                                "`references/laws-decision-tables-moved.md`")
        self.calibrate(self.skill, mutate, "profile-law-routing",
                       "references/laws-decision-tables-moved.md")

    def test_a_load_when_line_disagreeing_with_its_row_fails(self):
        """The constitution's rule: every reference file opens with a LOAD WHEN line
        stating its own trigger, so the routing table and the files cannot silently
        disagree. Editing one side and not the other is how they do."""
        path = os.path.join(self.work, "references", "phase-verification.md")

        def mutate(text):
            return text.replace("LOAD WHEN: the gates are about to run",
                                "LOAD WHEN: the gates have already run")
        self.calibrate(path, mutate, "profile-law-routing", "phase-verification.md")

    def test_a_reference_file_with_no_load_when_line_fails(self):
        path = os.path.join(self.work, "references", "phase-expression.md")

        def mutate(text):
            return text.replace("LOAD WHEN:", "Load it when:")
        self.calibrate(path, mutate, "profile-law-routing", "has no LOAD WHEN line")

    def test_every_law_and_phase_resolves_in_the_shipped_tree(self):
        """The positive half, against the real repository rather than a fixture:
        nineteen laws and six phases, each resolving to a file that exists."""
        name, verdict, evidence = P.check_law_routing(ROOT)
        self.assertEqual("PASS", verdict, "%s: %s" % (name, evidence))
        self.assertIn("19 law(s) and 6 phase(s)", evidence)


class TestDeclaration(ProfileFixture):
    def test_an_unknown_module_id_is_refused_by_name(self):
        def mutate(_text):
            return json.dumps({"schemaVersion": "1.0", "profile": "default",
                               "modules": ["telemetery"]}, indent=2) + "\n"
        self.calibrate(self.decl, mutate, "profile-declaration", "telemetery")

    def test_an_unparseable_declaration_fails_and_falls_back_to_the_default(self):
        write(self.decl, "{not json")
        got, code, out = verdicts(self.work)
        self.assertEqual("FAIL", got.get("profile-declaration", ("missing",))[0], out)
        self.assertEqual(2, code)
        # The fallback direction is the point: a broken declaration must not switch
        # a module ON. The smallest surface is the safe way to fail.
        _name, enabled, problems = P.declared(self.work)
        self.assertEqual([], enabled)
        self.assertTrue(problems, "a broken declaration must carry its reason out")

    def test_an_unknown_profile_name_is_refused_by_name(self):
        def mutate(_text):
            return json.dumps({"schemaVersion": "1.0", "profile": "everything",
                               "modules": []}, indent=2) + "\n"
        self.calibrate(self.decl, mutate, "profile-declaration", "everything")

    def test_full_turns_on_every_declared_module(self):
        write(self.decl, json.dumps({"schemaVersion": "1.0", "profile": "full",
                                     "modules": []}, indent=2) + "\n")
        _name, enabled, problems = P.declared(self.work)
        self.assertEqual([], problems)
        self.assertEqual(["policy", "release", "team", "telemetry", "vault"], enabled)

    def test_the_environment_overrides_the_file(self):
        os.environ["SBE_PROFILE_MODULES"] = "team"
        try:
            _name, enabled, problems = P.declared(self.work)
        finally:
            del os.environ["SBE_PROFILE_MODULES"]
        self.assertEqual([], problems)
        self.assertEqual(["team"], enabled)

    def test_a_module_index_declaring_nothing_is_no_data_never_pass(self):
        """Absent evidence is NO-DATA here as everywhere: an index that declares no
        module is no id space to validate a declaration against and no leak to look
        for, so neither check may say PASS, and neither may block."""
        write(self.index, "# Optional modules\n\nLOAD WHEN: never, this fixture declares none.\n")
        got, code, out = verdicts(self.work)
        self.assertEqual("NO-DATA", got.get("profile-declaration", ("missing",))[0], out)
        self.assertEqual("NO-DATA", got.get("profile-module-isolation", ("missing",))[0], out)
        self.assertEqual("PASS", got.get("profile-law-routing", ("missing",))[0],
                         "the laws still resolve: the module index holds no law")
        self.assertEqual(0, code, "NO-DATA is never a pass and never a block")

    def test_deleting_the_module_index_the_law_file_points_at_fails(self):
        """The other half of the same absence. A file SKILL.md tells the reader to
        open is a CLAIM, and a claim whose file is gone is broken rather than
        absent, so it FAILs instead of reporting NO-DATA."""
        original = read(self.index)
        got, _code, out = verdicts(self.work)
        self.assertEqual("PASS", got.get("profile-law-routing", ("missing",))[0], out)
        os.remove(self.index)
        got, code, out = verdicts(self.work)
        self.assertEqual("FAIL", got.get("profile-law-routing", ("missing",))[0], out)
        self.assertIn("references/modules.md", got["profile-law-routing"][1])
        self.assertEqual(2, code)
        write(self.index, original)
        got, code, _ = verdicts(self.work)
        self.assertEqual("PASS", got.get("profile-law-routing", ("missing",))[0],
                         "restoring the index did not turn the check green again")
        self.assertEqual(0, code)


class TestModuleEnforcement(ProfileFixture):
    """An enforcement claim must stand up, or carry the exact words that admit it does not.

    Four of the five declared modules have no mechanism that reads the declaration.
    A table that lists them beside the one that does tells a reader five switches
    exist when one does, so the claim itself is checked.
    """

    @staticmethod
    def _set_team_enforcement(text, cell):
        out = []
        for line in text.splitlines(True):
            if line.startswith("| `team` |"):
                cells = line.rstrip("\n").strip("|").split("|")
                cells[-1] = " %s " % cell
                line = "|" + "|".join(cells) + "|\n"
            out.append(line)
        return "".join(out)

    def test_claiming_a_file_that_never_asks_about_the_module_fails(self):
        """The defect this check exists to catch: a module is promoted from
        DECLARED BUT NOT ENFORCED to a named enforcement point that does not
        actually consult the profile for it. The hook named here is real, and it
        really does gate telemetry, and it says nothing about team."""
        def mutate(text):
            return self._set_team_enforcement(
                text, "`tools/sbe_sessionstart.py`, which gates it.")
        self.calibrate(self.index, mutate, "profile-module-enforcement",
                       "never asks the profile about team")

    def test_a_cell_that_is_neither_the_phrase_nor_a_path_fails(self):
        """Prose in the column is how the admission gets softened into something
        that reads like enforcement. Only the exact phrase, or a path that can be
        opened, is accepted."""
        def mutate(text):
            return self._set_team_enforcement(text, "a human reviews it.")
        self.calibrate(self.index, mutate, "profile-module-enforcement",
                       "neither the exact phrase")

    def test_the_shipped_table_admits_which_four_are_not_enforced(self):
        """The positive half, against the real repository. ONE enforcement point
        exists, in the SessionStart hook, and the other four modules carry the
        exact phrase rather than an implied switch."""
        name, verdict, evidence = P.check_module_enforcement(ROOT)
        self.assertEqual("PASS", verdict, "%s: %s" % (name, evidence))
        self.assertIn("1 with an enforcement point", evidence)
        self.assertIn("4 marked DECLARED BUT NOT ENFORCED (vault, team, policy, release)",
                      evidence)
        rows, problem = P.module_rows(ROOT)
        self.assertEqual("", problem)
        for mid, _t, _rel, _h, cell in rows:
            if mid in ("vault", "team", "policy", "release"):
                self.assertTrue(cell.startswith(P.NOT_ENFORCED),
                                "%s must carry the exact phrase, not an implied switch: %r"
                                % (mid, cell))

    def test_the_team_surface_is_still_loaded_unconditionally_and_the_table_says_so(self):
        """The claim the table must never make again. `references/team-execution.md`
        is read by three shipped surfaces that consult no profile at all, so no
        declaration can switch it off, and the row admits it by name."""
        loaders = (os.path.join("skills", "work", "SKILL.md"),
                   os.path.join("skills", "handover", "SKILL.md"),
                   os.path.join("agents", "implementation-worker.md"))
        for rel in loaders:
            text, problem = P.read_text(os.path.join(ROOT, rel))
            self.assertEqual("", problem)
            self.assertIn("references/team-execution.md", text,
                          "%s stopped loading the team surface; the table's admission is stale"
                          % rel)
            self.assertNotIn("sbe_profile", text,
                             "%s now consults the profile; promote team out of %s"
                             % (rel, P.NOT_ENFORCED))
        index, problem = P.read_text(os.path.join(ROOT, "references", "modules.md"))
        self.assertEqual("", problem)
        for rel in loaders:
            self.assertIn(rel.replace(os.sep, "/"), index,
                          "the team row must name %s as an unconditional loader" % rel)


class TestDeclarationDiscovery(unittest.TestCase):
    """WHICH file answered, and whether the walk finds it at all.

    The defect these calibrate against: the resolution used to consult the working
    directory ONLY, so a session started in any subdirectory of a declared project
    silently got the shipped install default, while the evidence line printed the
    bare relative string `.brothersbe/profile.json` and never said which file it had
    opened.
    """

    def setUp(self):
        # realpath, not the raw mkdtemp result: on macOS /var is a symlink to
        # /private/var, and a subprocess launched with cwd=/var/... reports
        # /private/var/... from getcwd(). Comparing an absolute path the tool
        # printed against one this test built would then fail for a reason that
        # has nothing to do with the behaviour under test.
        self.install = os.path.realpath(scratch())
        self.tool = os.path.join(self.install, "tools", "sbe_profile.py")
        self.proj = os.path.realpath(tempfile.mkdtemp(prefix="sbe-profile-proj-"))
        os.makedirs(os.path.join(self.proj, ".brothersbe"))
        write(os.path.join(self.proj, ".brothersbe", "profile.json"),
              json.dumps({"schemaVersion": "1.0", "profile": "full", "modules": []}) + "\n")
        self.deep = os.path.join(self.proj, "services", "ingest")
        os.makedirs(self.deep)
        self.bare = os.path.realpath(tempfile.mkdtemp(prefix="sbe-profile-bare-"))
        os.makedirs(os.path.join(self.bare, ".git"))
        os.makedirs(os.path.join(self.bare, "src"))

    def tearDown(self):
        for d in (self.install, self.proj, self.bare):
            shutil.rmtree(d, ignore_errors=True)

    def run_in(self, cwd, *args):
        """The tool with NO --root and NO --project, so the walk decides. That is
        the SessionStart hook's case, and it is the only one the defect could reach.

        Returns the CompletedProcess itself rather than a (stdout, code) pair, so
        this helper cannot be misread as a verdict producer by the source lint in
        evals/test_no_data_class.py, which flags any two-tuple whose first element
        it cannot prove is never "PASS".
        """
        e = dict(os.environ)
        e.pop("SBE_PROFILE", None)
        e.pop("SBE_PROFILE_MODULES", None)
        return subprocess.run([sys.executable, self.tool] + list(args),
                              capture_output=True, text=True, cwd=cwd, env=e, timeout=120)

    def test_a_declaration_in_the_working_directory_answers_and_is_named_absolutely(self):
        r = self.run_in(self.proj, "resolve")
        out, code = r.stdout, r.returncode
        self.assertEqual(0, code, out)
        self.assertIn("BROTHERSBE PROFILE: full", out)
        self.assertIn(os.path.join(self.proj, ".brothersbe", "profile.json"), out,
                      "the verdict must name the absolute path of the file that answered")
        self.assertIn("found in the working directory", out)

    def test_a_declaration_two_directories_up_answers(self):
        """The whole of the defect, in one case: a session started two levels down
        must read its own project's declaration."""
        r = self.run_in(self.deep, "resolve")
        out, code = r.stdout, r.returncode
        self.assertEqual(0, code, out)
        self.assertIn("BROTHERSBE PROFILE: full", out)
        self.assertIn(os.path.join(self.proj, ".brothersbe", "profile.json"), out)
        self.assertIn("2 directory level(s) above the working directory", out)
        code = self.run_in(self.deep, "enabled", "telemetry").returncode
        self.assertEqual(0, code, "the project's own profile must decide `enabled`, "
                                  "not the install's")

    def test_the_walk_stops_at_a_git_boundary_and_says_the_install_answered(self):
        """No declaration anywhere: the shipped install default answers, and the
        line SAYS so and names where that default lives. Silence here is the
        silent-wrong-source failure."""
        r = self.run_in(os.path.join(self.bare, "src"), "resolve")
        out, code = r.stdout, r.returncode
        self.assertEqual(0, code, out)
        self.assertIn("SHIPPED INSTALL DEFAULT", out)
        self.assertIn(os.path.join(self.install, ".brothersbe", "profile.json"), out,
                      "the line must name where the shipped default lives")
        self.assertIn("it holds .git", out, "the walk must say where it stopped")
        self.assertNotIn(self.proj, out)
        self.assertIn("BROTHERSBE PROFILE: default", out)

    def test_the_check_report_also_names_the_file_that_answered(self):
        r = self.run_in(self.deep, "check", "--strict")
        out, code = r.stdout, r.returncode
        self.assertEqual(0, code, out)
        for line in out.splitlines():
            if line.strip().startswith(("profile-declaration", "profile-module-isolation")):
                self.assertIn(os.path.join(self.proj, ".brothersbe", "profile.json"), line,
                              "a profile verdict line must name the file that answered: %r"
                              % line)

    def _mutated_tool(self, old, new):
        """Re-inject a defect into the scratch copy of the tool itself. Returns a
        callable that restores it, so the green half is asserted from the same
        bytes the red half ran against."""
        original = read(self.tool)
        self.assertIn(old, original, "the shipped tool no longer holds the line this "
                                     "calibration mutates: %r" % old)
        write(self.tool, original.replace(old, new, 1))

        def restore():
            write(self.tool, original)
        return restore

    def test_calibration_the_old_cwd_only_lookup_gets_the_wrong_file(self):
        """RED half. Stopping the walk after the first directory is exactly the
        shipped defect, and it makes the two-levels-down case answer with the
        install instead of the project."""
        restore = self._mutated_tool('        if os.path.exists(os.path.join(here, ".git")):',
                                     '        if True:')
        try:
            r = self.run_in(self.deep, "resolve")
            out, code = r.stdout, r.returncode
            self.assertEqual(0, code, out)
            self.assertIn("SHIPPED INSTALL DEFAULT", out,
                          "with the walk removed, the deep directory must fall back to the "
                          "install; if it does not, this test is measuring something else")
            self.assertNotIn(os.path.join(self.proj, ".brothersbe", "profile.json"), out)
        finally:
            restore()
        r = self.run_in(self.deep, "resolve")
        out, code = r.stdout, r.returncode
        self.assertEqual(0, code, out)
        self.assertIn(os.path.join(self.proj, ".brothersbe", "profile.json"), out,
                      "restoring the walk did not bring the project's declaration back")

    def test_calibration_a_bare_relative_string_no_longer_satisfies_the_report(self):
        """RED half for the other half of the same defect: printing the relative
        constant instead of the resolved path. The evidence then names no file that
        can be checked, and this test says so."""
        restore = self._mutated_tool(
            '            % (name, len(enabled), len(known), ", ".join(enabled) or "none", src.note))',
            '            % (name, len(enabled), len(known), ", ".join(enabled) or "none", DECLARATION))')
        try:
            r = self.run_in(self.deep, "check", "--strict")
            out, code = r.stdout, r.returncode
            self.assertEqual(0, code, out)
            line = [ln for ln in out.splitlines() if ln.strip().startswith("profile-declaration")]
            self.assertEqual(1, len(line), out)
            self.assertNotIn(self.proj, line[0],
                             "the mutation must remove the absolute path; if the path survives, "
                             "this test proves nothing")
            self.assertIn(".brothersbe/profile.json", line[0])
        finally:
            restore()
        r = self.run_in(self.deep, "check", "--strict")
        out, code = r.stdout, r.returncode
        self.assertEqual(0, code, out)
        line = [ln for ln in out.splitlines() if ln.strip().startswith("profile-declaration")]
        self.assertIn(os.path.join(self.proj, ".brothersbe", "profile.json"), line[0])


class TestStartupSurface(unittest.TestCase):
    """The measurable half: what the SessionStart hook actually prints."""

    def _run(self, env):
        e = dict(os.environ)
        e.pop("SBE_PROFILE", None)
        e.pop("SBE_PROFILE_MODULES", None)
        e["BROTHERSBE_VAULT"] = self.vault
        e.update(env)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sbe_sessionstart.py")],
                           input="{}", capture_output=True, text=True, env=e, timeout=180)
        self.assertEqual(0, r.returncode, "SessionStart must always exit 0")
        return r.stdout

    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix="sbe-profile-vault-")
        self.tel = os.path.join(self.vault, "99-System", "telemetry")
        os.makedirs(self.tel)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_the_update_notice_prints_at_the_default_profile_and_at_full(self):
        """The one startup line the profile may NOT take away. Gating this behind
        the release module was measured, refused, and undone: an upgraded install
        that stops saying the law changed under it is trusted unread. The
        merge-path half of this control is tools/test_sbe.py's
        TestTheUpdateNoticeIsUnconditional, because CI does not run this file.

        The marker is rewritten before EACH run because check-update writes it
        itself; without that, the second run prints nothing and an assertion about
        silence would pass over the wrong cause.
        """
        notice = "BROTHERSBE: the skill changed since your last session"

        def with_stale_marker(env):
            write(os.path.join(self.tel, "installed-skill-version-brothersbe"),
                  "0" * 40 + "\n")
            return self._run(env)

        first = with_stale_marker({})
        if "the update check is skipped" in first:
            self.skipTest("not a git checkout the update check can read")
        self.assertIn(notice, first,
                      "the DEFAULT profile lost the skill-changed notice:\n%s" % first)
        self.assertIn(notice, with_stale_marker({"SBE_PROFILE": "full"}),
                      "the notice must also survive the full profile")

    def test_the_telemetry_nag_is_absent_by_default_and_present_when_switched_on(self):
        """An empty vault makes `startup-nags` print the weekly-review nag every
        time, which is what makes this a calibration rather than an assertion about
        silence: the line provably exists, and the default profile is what keeps it
        out of the session's context."""
        on = self._run({"SBE_PROFILE": "full"})
        self.assertIn("weekly review has NEVER run", on,
                      "with the telemetry module on, the nag must print; otherwise this "
                      "test's negative half proves nothing")
        off = self._run({})
        self.assertNotIn("weekly review has NEVER run", off,
                         "the telemetry module leaked into the default profile")
        self.assertLess(len(off.encode("utf-8")), len(on.encode("utf-8")),
                        "the default profile must inject fewer bytes than the full one")

    def test_the_compaction_hint_is_floor_and_survives_the_smallest_profile(self):
        """The recovery pointer is exempt from every reduction. It prints first, at
        every profile, and no declaration can switch it off."""
        hook = read(os.path.join(ROOT, "tools", "sbe_sessionstart.py"))
        hint = hook.find("compact-hint")
        gate = hook.find("enabled telemetry")
        self.assertTrue(0 < hint < gate,
                        "the compaction hint must be emitted before any profile-gated "
                        "emitter, so a shrinking profile can never cost the recovery pointer")
        self.assertNotIn("enabled ", hook[:hint],
                         "nothing may gate the compaction hint")

    def test_the_floor_lines_survive_in_the_injected_digest(self):
        """What the default profile still carries. The floor is exempt from the
        reduction, so these lines are asserted in the bytes the hook injects, not
        only in the file on disk."""
        out = self._run({})
        for needle in ("L6 stop immediately on any forcing condition",
                       "L11 silent-failure lints",
                       "L14 blast radius: no apply rights on production state",
                       "NO-DATA is never a pass",
                       "After compaction or resume: re-read SKILL.md"):
            self.assertIn(needle, out, "the default profile dropped a floor line: %r" % needle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
