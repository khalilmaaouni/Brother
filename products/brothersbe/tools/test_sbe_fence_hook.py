#!/usr/bin/env python3
"""Regression tests for the BrotherSBE PreToolUse fence hook.

Run: python3 tools/test_sbe_fence_hook.py

Standard library only, matching the zero-dependency ethos of the tools. Kept in
its own file rather than folded into tools/test_sbe.py so the fence hook's
guarantees can be run, and be seen to fail, on their own.

TWO PROPERTIES CARRY THIS FILE, and everything else here is supporting detail.

  FAIL OPEN. A broken, missing, undecodable or unreadable fence registry must
  never block a write. This hook sits in front of every edit the operator makes,
  so a hook that failed closed on its own bug would stop the operator's own work,
  which is strictly worse than having no hook at all. Every failure mode this
  file can construct is asserted to ALLOW, and to say on stderr that it allowed
  and why. `TestFailOpen` is the whole class of it.

  IT SAYS WHY, AND THE ESCAPE WORKS. Every refusal names the fence that owns the
  file and what the writer should do instead, and this project's standing rule is
  that a named escape must be one that actually works. So the escape is not
  merely asserted to be present in the string: `test_the_named_escape_actually_
  releases_the_fence` performs it (appends LANDED to the fence line, exactly as
  STATE.template.md prescribes) and then replays the IDENTICAL payload and
  asserts it is now allowed. A refusal naming a door that does not open is the
  failure this test exists to catch.
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


def _chmod_can_deny_reads():
    """True when chmod 000 on this host actually makes a file unreadable.

    Asked of the filesystem rather than of sys.platform, the same idiom
    `_posix_modes_enforced` in test_sbe.py uses for mode bits. The two tests
    guarded by this one build their scenario by revoking read access, and on
    Windows os.chmod writes no access list at all, so the file stays readable,
    the registry parses fine, and the hook correctly refuses a cross-fence
    write. The old guard asked only whether this process was root, which is the
    same question one platform later: can this host construct the condition
    under test. It could not, and two tests failed for a reason that had nothing
    to do with fail-open behaviour.

    A skip here is a genuine NO-DATA, not a pass: on a host where the scenario
    cannot be built, nothing was examined, and the skip line says so.
    """
    with tempfile.TemporaryDirectory() as d:
        probe = os.path.join(d, "probe")
        with open(probe, "w") as f:
            f.write("x")
        try:
            os.chmod(probe, 0o000)
            with open(probe):
                return False          # still readable, so chmod proved nothing
        except (OSError, PermissionError):
            return True
        finally:
            os.chmod(probe, 0o600)

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_PATH = os.path.join(HERE, "sbe_fence_hook.py")

# Import the module under test regardless of cwd, the same way tools/test_sbe.py
# imports sbe_telemetry.
_spec = importlib.util.spec_from_file_location("sbe_fence_hook", HOOK_PATH)
fh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fh)
sys.modules["sbe_fence_hook"] = fh

OWNER_SESSION = "owner-session-1111-2222"
MY_SESSION = "intruder-session-3333-4444"

#: A fence line in exactly the shape STATE.template.md prescribes.
FENCE_LINE = (
    "- agent: doc-writer (sole writer, session %s) | tier T1 | TTL 2026-12-31 |\n"
    "  objective: rewrite the setup guide |\n"
    "  files: docs/SETUP.md, tools/sbe_gate.py |\n"
    "  output: one commit |\n" % OWNER_SESSION)

REGISTRY_BODY = (
    "# STATE\n\n"
    "## Fence registry\n\n"
    + FENCE_LINE +
    "\n## Decisions\n"
)


def write(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


class FenceCase(unittest.TestCase):
    """A temp project with a STATE.md registry, and an environment that is put
    back exactly as it was found.

    The environment restore is not housekeeping: BROTHERSBE_REGISTRIES,
    BROTHERSBE_FENCE_SESSION and BROTHERSBE_FENCE_HOOK_OFF all change the hook's
    answer, so a test that leaked one of them would silently decide the verdict
    of the next test in the file."""

    ENV_KEYS = ("BROTHERSBE_REGISTRIES", "BROTHERSBE_FENCE_SESSION",
                "BROTHERSBE_FENCE_HOOK_OFF", "CLAUDE_CONFIG_DIR")

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "docs"))
        os.makedirs(os.path.join(self.root, "tools"))
        write(os.path.join(self.root, "docs", "SETUP.md"), "setup\n")
        self.registry = os.path.join(self.root, "STATE.md")
        write(self.registry, REGISTRY_BODY)
        # Companion detection reads CLAUDE_CONFIG_DIR (default ~/.claude) off
        # the real filesystem. Pinning it to a directory this test controls
        # (empty by default, so detection reads "nothing there" and answers
        # ABSENT) keeps every test in this file hermetic: without this, the
        # whole suite's behavior would depend on whether the machine actually
        # running it happens to have the companion installed.
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.root, "claude-config")

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Restore any mode this test dropped, so the temp cleanup can remove it.
        for dirpath, dirnames, filenames in os.walk(self.root):
            for name in list(dirnames) + list(filenames):
                target = os.path.join(dirpath, name)
                try:
                    os.chmod(target, 0o700)
                except OSError:
                    # A path the test already removed. The cleanup below reports
                    # anything that actually blocks removal, so nothing is hidden
                    # by this line.
                    continue
        self._tmp.cleanup()

    # -- payload builders ---------------------------------------------------

    def payload(self, path, session=MY_SESSION, tool="Write", **extra):
        p = {"tool_name": tool,
             "tool_input": {"file_path": path, "content": "x"},
             "session_id": session,
             "cwd": self.root,
             "project_dir": self.root}
        p.update(extra)
        return p

    def decide(self, payload):
        return fh.decide(payload)

    # -- assertions ---------------------------------------------------------

    def assertDenied(self, decision, why=""):
        self.assertIsNotNone(
            decision.payload,
            "expected a DENY %s; the hook allowed the write. stderr notes: %s"
            % (why, decision.notes))
        out = decision.payload["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        return out["permissionDecisionReason"]

    def assertAllowed(self, decision, why=""):
        self.assertIsNone(
            decision.payload,
            "expected an ALLOW %s; the hook DENIED with: %s"
            % (why, decision.payload))
        return "\n".join(decision.notes)


# ---------------------------------------------------------------------------
# Property 1: a genuine ownership conflict is refused, and the refusal says why.
# ---------------------------------------------------------------------------

class TestGenuineConflictIsRefused(FenceCase):

    def test_a_write_inside_another_sessions_fence_is_denied(self):
        d = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
        reason = self.assertDenied(d, "for a file another session's fence owns")
        self.assertIn("docs/SETUP.md", reason)

    def test_the_refusal_names_the_fence_that_owns_the_file(self):
        d = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
        reason = self.assertDenied(d)
        # The owner, by every handle the reader might need to go find them.
        self.assertIn("doc-writer", reason, "the refusal must name the owning agent")
        self.assertIn(OWNER_SESSION, reason, "the refusal must name the owning session")
        self.assertIn(self.registry, reason,
                      "the refusal must name the registry file the fence lives in")
        self.assertIn("L13", reason, "the refusal must name the law it enforces")

    def test_the_refusal_names_an_escape(self):
        d = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
        reason = self.assertDenied(d)
        self.assertIn("LANDED", reason,
                      "the refusal must name the closing marker that releases the fence")
        self.assertIn("ADOPTED", reason,
                      "the refusal must name the takeover marker")
        self.assertIn(MY_SESSION, reason,
                      "the takeover escape must name the session doing the taking")

    def test_the_named_escape_actually_releases_the_fence(self):
        """The escape is PERFORMED, not merely quoted.

        This is the test the whole file is built around. A refusal that names a
        door which does not open is worse than a refusal that names none, because
        the reader spends their time on it. So: deny, then close the fence the
        way the refusal said to close it, then replay the IDENTICAL payload."""
        target = os.path.join(self.root, "docs", "SETUP.md")
        before = self.decide(self.payload(target))
        reason = self.assertDenied(before, "before the fence is closed")
        self.assertIn("LANDED", reason)

        # Perform escape 2 exactly as STATE.template.md prescribes: append the
        # evidence block, marker first, to the fence line itself.
        with io.open(self.registry, "r", encoding="utf-8") as f:
            body = f.read()
        closed = body.replace(
            "  output: one commit |\n",
            "  output: one commit |\n"
            "  LANDED 2026-07-28, evidence (verbatim, run after last edit):\n"
            "    Ran 27 tests in 14.016s OK\n")
        self.assertNotEqual(body, closed, "the fixture's fence line was not found")
        write(self.registry, closed)

        after = self.decide(self.payload(target))
        self.assertAllowed(
            after, "after the fence line was closed with LANDED, which is exactly "
                   "what the refusal told the writer to do")

    def test_the_owner_of_the_fence_is_never_refused_its_own_file(self):
        d = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"),
                                     session=OWNER_SESSION))
        self.assertAllowed(d, "for the session the fence line names as sole writer")

    def test_an_abbreviated_session_id_in_the_registry_still_matches_its_owner(self):
        """A registry is hand-written and an operator abbreviates a UUID as often
        as they paste it. A false MISS here would refuse the rightful owner out
        of their own fence, so the match is a prefix in both directions."""
        write(self.registry, REGISTRY_BODY.replace(OWNER_SESSION, OWNER_SESSION[:8]))
        d = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"),
                                     session=OWNER_SESSION))
        self.assertAllowed(d, "for an owner whose id the registry abbreviated")

    def test_a_file_outside_every_fence_is_allowed(self):
        write(os.path.join(self.root, "README.md"), "readme\n")
        d = self.decide(self.payload(os.path.join(self.root, "README.md")))
        self.assertAllowed(d, "for a file no fence line claims")

    def test_a_directory_claim_covers_the_files_under_it(self):
        write(self.registry, REGISTRY_BODY.replace(
            "files: docs/SETUP.md, tools/sbe_gate.py", "files: docs/"))
        d = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
        self.assertDenied(d, "for a file under a claimed directory")

    def test_a_glob_claim_does_not_cross_a_separator_its_author_never_named(self):
        """`docs/*.md` claims docs/SETUP.md and must NOT silently swallow
        docs/guides/01-quickstart.md, which its author never named. fnmatch's '*'
        crosses '/' happily, which is why the separator guard exists."""
        os.makedirs(os.path.join(self.root, "docs", "guides"))
        write(os.path.join(self.root, "docs", "guides", "01-quickstart.md"), "g\n")
        write(self.registry, REGISTRY_BODY.replace(
            "files: docs/SETUP.md, tools/sbe_gate.py", "files: docs/*.md"))
        self.assertDenied(
            self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"))),
            "for the file the glob names")
        self.assertAllowed(
            self.decide(self.payload(
                os.path.join(self.root, "docs", "guides", "01-quickstart.md"))),
            "for a deeper file the glob's author never named")

    def test_a_relative_path_from_a_subdirectory_is_canonicalized_before_comparison(self):
        """Comparing unresolved strings is bypassed by a relative path typed from
        a subdirectory. `../docs/SETUP.md` with cwd=tools/ is the same file."""
        d = self.decide(self.payload("../docs/SETUP.md",
                                     cwd=os.path.join(self.root, "tools")))
        self.assertDenied(d, "for a relative path that resolves into the fence")

    def test_a_dot_dot_escape_inside_an_absolute_path_is_canonicalized(self):
        weird = os.path.join(self.root, "tools", "..", "docs", "SETUP.md")
        self.assertDenied(self.decide(self.payload(weird)),
                          "for an absolute path carrying '..' into the fence")

    def test_a_multiedit_payload_is_read_to_its_nested_paths(self):
        """A MultiEdit-shaped payload whose per-edit entries carry their own
        file_path must not be reduced to the top-level path."""
        p = {"tool_name": "MultiEdit",
             "tool_input": {"file_path": os.path.join(self.root, "README.md"),
                            "edits": [{"file_path": os.path.join(
                                self.root, "docs", "SETUP.md")}]},
             "session_id": MY_SESSION, "cwd": self.root, "project_dir": self.root}
        self.assertDenied(self.decide(p), "for a fenced path nested inside the edits list")

    def test_a_write_outside_the_project_root_is_allowed(self):
        """BrotherSBE fences a project, not the filesystem."""
        with tempfile.TemporaryDirectory() as other:
            elsewhere = os.path.join(other, "SETUP.md")
            self.assertAllowed(self.decide(self.payload(elsewhere)),
                               "for a path above the project root")

    def test_a_fence_line_inside_an_html_comment_is_not_enforced(self):
        """A fence a reader of the registry cannot see must not be a fence that
        refuses them. Same rendered-text rule every other reader in this project
        applies."""
        write(self.registry, "# STATE\n\n<!--\n" + FENCE_LINE + "\n-->\n")
        self.assertAllowed(
            self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"))),
            "for a fence line commented out of the rendered registry")

    def test_an_asterisk_bullet_fence_is_enforced(self):
        """The two bullets render identically. Enforcing only `- ` would leave a
        real fence written with `* ` unprotected, which is why this hook uses the
        broader of the two parses this project ships."""
        write(self.registry, REGISTRY_BODY.replace("- agent:", "* agent:", 1))
        self.assertDenied(
            self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"))),
            "for a fence line written with an asterisk bullet")

    def test_an_adopted_fence_no_longer_refuses(self):
        write(self.registry, REGISTRY_BODY.replace(
            "  output: one commit |\n", "  output: one commit | ADOPTED 2026-07-28\n"))
        self.assertAllowed(
            self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"))),
            "for a fence line closed with ADOPTED")

    def test_registries_named_by_the_env_var_are_enforced_too(self):
        """BROTHERSBE_REGISTRIES is the name the scorer and fence-lint already
        read. A fence declared there must bind here, or the three tools disagree
        about what is fenced."""
        with tempfile.TemporaryDirectory() as other:
            extra = os.path.join(other, "OTHER-STATE.md")
            write(extra, REGISTRY_BODY.replace("docs/SETUP.md", "README.md"))
            os.environ["BROTHERSBE_REGISTRIES"] = extra
            write(os.path.join(self.root, "README.md"), "r\n")
            self.assertDenied(
                self.decide(self.payload(os.path.join(self.root, "README.md"))),
                "for a fence declared in a registry named by BROTHERSBE_REGISTRIES")


# ---------------------------------------------------------------------------
# Property: companion deference
# (docs/adr/2026-08-12-where-the-shared-machinery-lives.md, ACCEPTED). With
# no companion detectable this file behaves exactly as
# TestGenuineConflictIsRefused already proved (property 1, unchanged). With
# the companion's own fence hook wired for PreToolUse, this file steps back
# and says so; with a detection signal that could not be read at all, it
# keeps deciding rather than guessing PRESENT.
# ---------------------------------------------------------------------------

class TestCompanionDeference(FenceCase):

    def _wire_companion_config_dir(self):
        """A CLAUDE_CONFIG_DIR carrying exactly what Claude Code itself would
        assemble for an operator whose settings.json wires the companion's
        own PreToolUse fence hook, whether pasted by hand or (for this
        fixture, textually identical either way) loaded from a plugin."""
        d = os.path.join(self.root, "companion-config")
        os.makedirs(d)
        write(os.path.join(d, "settings.json"), json.dumps({
            "hooks": {"PreToolUse": [
                {"matcher": "Edit|Write|MultiEdit|NotebookEdit",
                 "hooks": [{"type": "command",
                            "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/tools/'
                                       'bm_fence_hook.py"'}]}
            ]}
        }))
        return d

    # -- 2: deference is never silent ----------------------------------------

    def test_with_the_companion_wired_the_hook_defers_and_says_so(self):
        os.environ["CLAUDE_CONFIG_DIR"] = self._wire_companion_config_dir()
        d = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
        notes = self.assertAllowed(
            d, "when the companion's own fence hook is wired for PreToolUse")
        self.assertIn("DEFERRING", notes, "a deferral must say it deferred")
        self.assertIn("bm_fence_hook.py", notes,
                      "the deferral must name what it detected")
        self.assertIn("companion", notes.lower(),
                      "the deferral must name which hook is now authoritative")

    # -- 3: the deferral path writes no refusal ------------------------------

    def test_the_deferral_path_writes_no_refusal(self):
        """A write that would have been refused is now allowed through,
        because the companion, not this file, is authoritative for it."""
        target = os.path.join(self.root, "docs", "SETUP.md")
        baseline = self.decide(self.payload(target))
        self.assertDenied(baseline, "the baseline, with no companion, must still refuse")

        os.environ["CLAUDE_CONFIG_DIR"] = self._wire_companion_config_dir()
        deferred = self.decide(self.payload(target))
        self.assertAllowed(
            deferred, "the identical write, once the companion is wired, passes "
                      "through rather than being refused by this hook")

    # -- 4: NO-DATA keeps this hook deciding, the safety property ------------

    def test_no_data_when_the_signal_cannot_be_read_keeps_this_hook_deciding(self):
        """'Could not tell' must never default to 'the companion is present'.
        A settings.json that exists and will not parse is a genuine NO-DATA
        condition, and this hook must go on enforcing its own fences rather
        than guessing the companion has taken over."""
        d = os.path.join(self.root, "broken-config")
        os.makedirs(d)
        write(os.path.join(d, "settings.json"), "{ not json")
        os.environ["CLAUDE_CONFIG_DIR"] = d

        decision = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
        reason = self.assertDenied(
            decision, "NO-DATA must keep this hook deciding, never defer")
        self.assertIn("docs/SETUP.md", reason)

    def test_no_data_is_reported_not_silent(self):
        d = os.path.join(self.root, "broken-config-2")
        os.makedirs(d)
        write(os.path.join(d, "settings.json"), "{ not json")
        os.environ["CLAUDE_CONFIG_DIR"] = d

        write(os.path.join(self.root, "README.md"), "r\n")
        decision = self.decide(self.payload(os.path.join(self.root, "README.md")))
        notes = self.assertAllowed(decision, "a file no fence claims, even under NO-DATA")
        self.assertIn("could not", notes.lower())

    # -- 5: calibration --------------------------------------------------

    def test_calibration_an_always_present_detector_makes_the_genuine_conflict_case_go_red(self):
        """Refuter's check on the checker itself: if detect_companion always
        answered PRESENT, this file's very first property (a genuine conflict
        is refused, TestGenuineConflictIsRefused) would stop being true. This
        inverts the detector on purpose and re-runs the exact scenario
        test_a_write_inside_another_sessions_fence_is_denied asserts is
        DENIED; here it comes back ALLOWED, proving that assertion would fail
        (go red) under a detector this broken, so the real detector is
        load-bearing rather than a no-op."""
        original = fh.detect_companion
        fh.detect_companion = lambda cwd, root: fh.CompanionSignal(
            fh.CompanionSignal.PRESENT, "calibration probe: always present")
        try:
            d = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
            self.assertAllowed(
                d, "under a deliberately broken always-present detector; this is the "
                   "same write test_a_write_inside_another_sessions_fence_is_denied "
                   "asserts is DENIED, so seeing ALLOW here proves that assertion "
                   "would go red under this detector")
        finally:
            fh.detect_companion = original


# ---------------------------------------------------------------------------
# Property 2: FAIL OPEN. The class, not the instances.
# ---------------------------------------------------------------------------

class TestFailOpen(FenceCase):
    """Every one of these asserts the same two things: the write is ALLOWED, and
    a stderr note says it was allowed without the fence being checked, and why. A
    fail-open that is silent is only half a fail-open: the operator has to be
    able to tell "no fence owns this" apart from "the fence machinery is
    broken"."""

    def assertFailedOpen(self, decision, expect_in_reason, why):
        notes = self.assertAllowed(decision, why)
        self.assertIn("FAILING OPEN", notes,
                      "the fail-open must SAY it failed open (%s). notes: %s"
                      % (why, notes))
        self.assertIn("the fence was NOT checked", notes,
                      "the fail-open must say the fence went unchecked (%s)" % why)
        self.assertIn(expect_in_reason, notes,
                      "the fail-open must name its cause (%s). notes: %s"
                      % (why, notes))
        return notes

    # -- the three the brief names ------------------------------------------

    def test_fail_open_registry_file_absent(self):
        os.remove(self.registry)
        self.assertFailedOpen(
            self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"))),
            "no fence registry was opened",
            "the registry file does not exist")

    def test_fail_open_registry_file_corrupt(self):
        """Corrupt two ways in one file: bytes that are not valid UTF-8, and text
        that decodes but carries no fence line this hook can read. Both are a
        registry that cannot support a decision, and neither may block a write."""
        with open(self.registry, "wb") as f:
            f.write(b"\xff\xfe\x00\x01 not utf-8 and not a fence \xc3\x28\n")
        self.assertFailedOpen(
            self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"))),
            "none of them carries a live fence line",
            "the registry file holds undecodable bytes")

        write(self.registry, "# STATE\n\nthis file is prose and declares nothing\n")
        self.assertFailedOpen(
            self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"))),
            "none of them carries a live fence line",
            "the registry file parses but declares no fence")

    @unittest.skipUnless(_chmod_can_deny_reads(),
                         "this host cannot construct an unreadable file with "
                         "chmod, so the scenario under test cannot be built here")
    def test_fail_open_registry_file_unreadable(self):
        os.chmod(self.registry, 0o000)
        self.assertFailedOpen(
            self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"))),
            "could not be read",
            "the registry file exists and cannot be opened")

    # -- and the rest of the class ------------------------------------------

    def test_fail_open_when_the_registry_path_is_a_directory(self):
        os.remove(self.registry)
        os.makedirs(self.registry)
        self.assertFailedOpen(
            self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md"))),
            "not a regular file",
            "the registry path is a directory")

    @unittest.skipUnless(_chmod_can_deny_reads(),
                         "this host cannot construct an unenterable directory "
                         "with chmod, so the scenario under test cannot be "
                         "built here")
    def test_fail_open_when_a_registry_directory_cannot_be_entered(self):
        """glob returns FEWER paths, not an error, over a directory it cannot
        enter, so a denied parent is exactly how a configured registry set
        silently becomes smaller. It must fail OPEN and name the directory, never
        decide over the registries it happened to reach."""
        with tempfile.TemporaryDirectory() as other:
            shut = os.path.join(other, "locked")
            os.makedirs(shut)
            write(os.path.join(shut, "OTHER-STATE.md"), REGISTRY_BODY)
            os.chmod(shut, 0o000)
            try:
                os.environ["BROTHERSBE_REGISTRIES"] = os.path.join(shut, "*.md")
                self.assertFailedOpen(
                    self.decide(self.payload(
                        os.path.join(self.root, "docs", "SETUP.md"))),
                    "cannot be entered",
                    "a configured registry directory cannot be entered")
            finally:
                os.chmod(shut, 0o700)

    def test_fail_open_when_a_live_fence_declares_no_file_scope(self):
        """A fence with no `files:` field fences nothing this hook can compare
        against. Treating that as "no conflict" in silence is the exact shape of
        a check passing over evidence it never examined, so it is reported."""
        write(self.registry, "# STATE\n\n- agent: doc-writer (sole writer, session %s) "
                             "| tier T1 | objective: something |\n" % OWNER_SESSION)
        d = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
        notes = self.assertAllowed(d, "a fence with no readable file scope")
        self.assertIn("no readable `files:` scope", notes)
        self.assertIn("did NOT enforce it", notes)

    def test_fail_open_when_the_payload_is_not_an_object(self):
        for bad in ([], "a string", 7, None):
            self.assertFailedOpen(self.decide(bad),
                                  "hook payload was not a JSON object",
                                  "the payload is %r" % (bad,))

    def test_fail_open_when_tool_input_is_not_an_object(self):
        self.assertFailedOpen(
            self.decide({"tool_name": "Write", "tool_input": "oops",
                         "session_id": MY_SESSION, "cwd": self.root}),
            "was not a JSON object",
            "tool_input is not an object")

    def test_fail_open_when_no_target_path_is_present(self):
        self.assertFailedOpen(
            self.decide({"tool_name": "Write", "tool_input": {"content": "x"},
                         "session_id": MY_SESSION, "cwd": self.root}),
            "no target path found",
            "the write tool's input names no path")

    def test_fail_open_when_the_session_has_no_identity(self):
        """Without a session id there is nothing to compare against a fence
        line's declared writer, and refusing every write on that basis would stop
        the operator's own work."""
        p = self.payload(os.path.join(self.root, "docs", "SETUP.md"))
        p.pop("session_id")
        self.assertFailedOpen(self.decide(p), "no session_id",
                              "the payload carries no session id")

    def test_the_disable_switch_allows_and_says_so(self):
        os.environ["BROTHERSBE_FENCE_HOOK_OFF"] = "1"
        d = self.decide(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
        notes = self.assertAllowed(d, "the disable switch is set")
        self.assertIn("BROTHERSBE_FENCE_HOOK_OFF", notes)
        self.assertIn("the fence was NOT checked", notes)

    def test_no_failure_path_can_produce_a_deny(self):
        """The structural version of the property: over every malformed payload
        this file can think of, the hook is asserted never to deny. ALLOW and
        FAIL-OPEN are deliberately the same return value in decide(), and this is
        the test that would catch a future edit that split them."""
        fenced = os.path.join(self.root, "docs", "SETUP.md")
        malformed = [
            {}, {"tool_name": "Write"},
            {"tool_name": "Write", "tool_input": {}},
            {"tool_name": "Write", "tool_input": {"file_path": ""}},
            {"tool_name": "Write", "tool_input": {"file_path": None}},
            {"tool_name": None, "tool_input": {"file_path": fenced}},
            {"tool_name": "Write", "tool_input": {"file_path": fenced},
             "session_id": ""},
            {"tool_name": "Write", "tool_input": {"file_path": fenced},
             "session_id": 12345},
            {"tool_name": "Write", "tool_input": {"file_path": fenced},
             "session_id": MY_SESSION, "cwd": 99},
            {"tool_name": "Write", "tool_input": {"file_path": fenced},
             "session_id": MY_SESSION, "cwd": "/nonexistent/nowhere"},
        ]
        for p in malformed:
            self.assertAllowed(self.decide(p), "for malformed payload %r" % (p,))


# ---------------------------------------------------------------------------
# Case-insensitive filesystems: `paths_overlap` retries a missed comparison
# case-folded, but only trusts it once the filesystem confirms the fold is
# real, never on the string match alone.
# ---------------------------------------------------------------------------

class TestCaseFoldConfirmation(FenceCase):

    def test_a_real_case_collision_overlaps_at_the_unit_level(self):
        """docs/SETUP.md and docs/setup.md are one file on this machine
        (asserted by inode, the same proof the bypass fixture uses), so the
        case-folded retry must fire and `paths_overlap` must say True."""
        exact = os.path.join(self.root, "docs", "SETUP.md")
        variant = os.path.join(self.root, "docs", "setup.md")
        if not os.path.exists(variant):
            raise unittest.SkipTest(
                "this filesystem is case-sensitive; docs/setup.md is a different file here")
        self.assertEqual(os.stat(exact).st_ino, os.stat(variant).st_ino)
        self.assertTrue(fh.paths_overlap("docs/setup.md", "docs/SETUP.md", self.root),
                        "a real filesystem collision was not treated as an overlap")

    def test_a_case_folded_string_match_alone_is_never_trusted(self):
        """The Linux half of the fix: two honestly different files named
        `a.md` and `A.md` must not false-conflict just because their letters
        match once lowered. This machine's filesystem cannot construct that
        pair (case-insensitive by default), so the filesystem confirmation is
        forced to answer 'not the same entry', exactly what it would answer on
        a case-sensitive volume, and `paths_overlap` must still say False."""
        original = fh._same_entry_case_insensitive
        fh._same_entry_case_insensitive = lambda root, t, c: False
        try:
            self.assertFalse(
                fh.paths_overlap("docs/setup.md", "docs/SETUP.md", self.root),
                "a case-folded string match was trusted without filesystem confirmation")
        finally:
            fh._same_entry_case_insensitive = original

    def test_no_root_skips_the_fold_which_is_this_hooks_fail_open_bias(self):
        """A caller with no filesystem to confirm against (root=None) gets the
        exact-spelling answer only, never a guess."""
        self.assertFalse(fh.paths_overlap("docs/setup.md", "docs/SETUP.md"),
                         "the case-folded retry ran with nothing to confirm it against")


# ---------------------------------------------------------------------------
# The declared tool surface, including the gap that is declared rather than
# papered over.
# ---------------------------------------------------------------------------

class TestToolSurface(FenceCase):

    def test_bash_is_not_gated(self):
        """The skill's own sentence says this hook does not gate Bash, and the
        sentence has to stay true. A shell command can write any file and no
        reliable parse of arbitrary shell exists, so gating it would be a
        guarantee this file cannot keep. It is a declared gap, in docs/HOOKS.md,
        and this test is what stops it becoming an undeclared one."""
        self.assertNotIn("Bash", fh.WRITE_TOOLS)
        p = {"tool_name": "Bash",
             "tool_input": {"command": "echo x > %s"
                                       % os.path.join(self.root, "docs", "SETUP.md")},
             "session_id": MY_SESSION, "cwd": self.root, "project_dir": self.root}
        d = self.decide(p)
        self.assertAllowed(d, "Bash is outside this hook's tool surface")
        self.assertEqual(d.notes, [],
                         "a non-write tool must be silent, not loud: a stderr line "
                         "per Bash call is noise the operator learns to ignore")

    def test_read_only_tools_are_silent(self):
        for tool in ("Read", "Grep", "Glob", "TodoWrite", "WebFetch"):
            d = self.decide({"tool_name": tool,
                             "tool_input": {"file_path": os.path.join(
                                 self.root, "docs", "SETUP.md")},
                             "session_id": MY_SESSION, "cwd": self.root})
            self.assertAllowed(d, "%s is not a write tool" % tool)
            self.assertEqual(d.notes, [], "%s must be silent" % tool)

    def test_every_declared_write_tool_is_gated(self):
        for tool in sorted(fh.WRITE_TOOLS):
            key = "notebook_path" if tool == "NotebookEdit" else "file_path"
            p = {"tool_name": tool,
                 "tool_input": {key: os.path.join(self.root, "docs", "SETUP.md")},
                 "session_id": MY_SESSION, "cwd": self.root, "project_dir": self.root}
            self.assertDenied(self.decide(p), "for write tool %s" % tool)


# ---------------------------------------------------------------------------
# H9: `audit` counts live fence lines this project's own grammar cannot parse
# (a live-looking bullet with no readable `files:` scope), reusing the exact
# walk `read_fences` runs rather than a second parser.
# ---------------------------------------------------------------------------

class TestAuditSubcommand(FenceCase):

    def _run(self, *argv):
        out = subprocess.run([sys.executable, HOOK_PATH, "audit"] + list(argv),
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return out.returncode, out.stdout, out.stderr

    def test_zero_unenforceable_lines_on_a_clean_fixture_tree(self):
        code, out, err = self._run(self.root)
        self.assertEqual(code, 0, "audit exited %d: %s" % (code, out + err))
        self.assertIn("0 unenforceable fence line(s)", out,
                      "a registry carrying only a fully parseable fence line "
                      "must report zero: %r" % out)

    def test_exactly_one_reported_after_a_malformed_line_is_added(self):
        """A live-looking fence with no `files:` field: this project's own
        grammar (agent/owner-session/sole-writer markers, per
        sbe_score._is_live_fence) marks it live, but `fence_files` cannot
        resolve a scope for it, which is exactly the unenforceable case."""
        malformed = (
            "\n- agent: broken-writer (sole writer, session zzzz-1111) | tier T1 |\n"
            "  objective: a fence with no files field at all |\n")
        with io.open(self.registry, "a", encoding="utf-8") as f:
            f.write(malformed)
        code, out, err = self._run(self.root)
        self.assertEqual(code, 0, "audit exited %d: %s" % (code, out + err))
        self.assertIn("1 unenforceable fence line(s)", out,
                      "adding one malformed live fence must move the count "
                      "from zero to exactly one: stdout=%r stderr=%r" % (out, err))
        self.assertIn("no readable `files:` scope", err,
                      "the offending line must be named on stderr")

    def test_no_data_when_no_registry_exists(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        code, out, err = self._run(empty)
        self.assertEqual(code, 0, "audit exited %d over an empty tree: %s" % (code, out + err))
        self.assertIn("NO-DATA", out, "an empty tree examined nothing and must say so: %r" % out)


# ---------------------------------------------------------------------------
# Codex apply_patch: the H1 blind spot this port closes. apply_patch carried
# its targets in tool_input.command, a key WRITE_TOOLS never recognized and
# PATH_KEYS never named, so a call with tool_name apply_patch crossed any
# fence unrefused. These twelve cases are the ones the implementation
# contract's Phase 5 names by number, in its own order.
# ---------------------------------------------------------------------------

class TestApplyPatch(FenceCase):

    def patch_payload(self, command, session=MY_SESSION, **extra):
        p = {"tool_name": "apply_patch",
             "tool_input": {"command": command},
             "session_id": session,
             "cwd": self.root,
             "project_dir": self.root}
        p.update(extra)
        return p

    # -- 1. an in-fence Update File patch: allow --------------------------
    # "In-fence" here is a target this session may write: nothing else has
    # declared a live fence over it, the ordinary shape the vast majority of
    # apply_patch calls will have.

    def test_1_an_update_file_patch_with_no_conflicting_fence_is_allowed(self):
        write(os.path.join(self.root, "README.md"), "hello\n")
        command = ("*** Begin Patch\n"
                   "*** Update File: README.md\n"
                   "@@\n-hello\n+goodbye\n"
                   "*** End Patch")
        d = self.decide(self.patch_payload(command))
        self.assertAllowed(d, "for an Update File target no live fence claims")

    # -- 2. an out-of-fence Update File patch: deny ------------------------
    # "Out-of-fence" here is a target another session's live fence already
    # claims: the genuine ownership conflict this whole file exists to catch.

    def test_2_an_update_file_patch_into_another_sessions_fence_is_denied(self):
        command = ("*** Begin Patch\n"
                   "*** Update File: docs/SETUP.md\n"
                   "@@\n-setup\n+updated\n"
                   "*** End Patch")
        d = self.decide(self.patch_payload(command))
        reason = self.assertDenied(d, "for an Update File target another session's fence owns")
        self.assertIn("docs/SETUP.md", reason)
        self.assertIn("L13", reason)

    # -- 3. a multi-file patch, one permitted and one forbidden: deny whole -

    def test_3_a_multi_file_patch_with_one_forbidden_target_denies_the_whole_call(self):
        write(os.path.join(self.root, "README.md"), "hello\n")
        command = ("*** Begin Patch\n"
                   "*** Update File: README.md\n"
                   "@@\n-hello\n+goodbye\n"
                   "*** Update File: docs/SETUP.md\n"
                   "@@\n-setup\n+updated\n"
                   "*** End Patch")
        d = self.decide(self.patch_payload(command))
        reason = self.assertDenied(
            d, "for a multi-file patch that also names a forbidden target")
        self.assertIn("docs/SETUP.md", reason)

    # -- 4. an Add File inside the source scope: allow ---------------------

    def test_4_an_add_file_target_inside_the_worktree_is_allowed(self):
        command = ("*** Begin Patch\n"
                   "*** Add File: brand_new.py\n"
                   "+print('hi')\n"
                   "*** End Patch")
        d = self.decide(self.patch_payload(command))
        self.assertAllowed(d, "for an Add File target inside the worktree with no claim on it")

    # -- 5. a Delete File outside the scope: deny ---------------------------

    def test_5_a_delete_file_target_outside_the_worktree_is_denied(self):
        command = ("*** Begin Patch\n"
                   "*** Delete File: ../outside.py\n"
                   "*** End Patch")
        d = self.decide(self.patch_payload(command, cwd=self.root))
        self.assertDenied(d, "for a Delete File target that escapes the worktree")

    # -- 6. a move whose destination escapes the scope: deny ----------------

    def test_6_a_move_to_destination_that_escapes_the_worktree_is_denied(self):
        write(os.path.join(self.root, "inside.py"), "x = 1\n")
        command = ("*** Begin Patch\n"
                   "*** Update File: inside.py\n"
                   "*** Move to: ../outside.py\n"
                   "@@\n-x = 1\n+x = 2\n"
                   "*** End Patch")
        d = self.decide(self.patch_payload(command))
        self.assertDenied(d, "for a Move to destination that escapes the worktree")

    # -- 7. an authority-file edit: deny even when another file is permitted

    def test_7_a_patch_touching_a_fenced_authority_file_denies_the_whole_call(self):
        """STATE.md is exactly the shape of file BrotherSBE already protects
        once it is named in a live fence's own `files:` scope. A multi-file
        patch that also touches an ordinary, unclaimed file must still be
        denied for the whole call, never partially allowed."""
        write(self.registry, REGISTRY_BODY.replace(
            "files: docs/SETUP.md, tools/sbe_gate.py", "files: docs/SETUP.md, STATE.md"))
        write(os.path.join(self.root, "README.md"), "hello\n")
        command = ("*** Begin Patch\n"
                   "*** Update File: README.md\n"
                   "@@\n-hello\n+goodbye\n"
                   "*** Update File: STATE.md\n"
                   "@@\n-old\n+new\n"
                   "*** End Patch")
        d = self.decide(self.patch_payload(command))
        reason = self.assertDenied(
            d, "for a patch that also touches the fenced authority file STATE.md")
        self.assertIn("STATE.md", reason)

    # -- 8. traversal with ../: deny -----------------------------------------

    def test_8_traversal_with_dot_dot_is_denied(self):
        command = ("*** Begin Patch\n"
                   "*** Update File: ../outside.py\n"
                   "@@\n-x\n+y\n"
                   "*** End Patch")
        d = self.decide(self.patch_payload(command))
        self.assertDenied(d, "for a relative target that traverses out of the worktree")

    # -- 9. an absolute path outside the worktree: deny ----------------------

    def test_9_an_absolute_path_outside_the_worktree_is_denied(self):
        with tempfile.TemporaryDirectory() as other:
            elsewhere = os.path.join(other, "escape.py")
            command = ("*** Begin Patch\n"
                       "*** Add File: %s\n"
                       "+x = 1\n"
                       "*** End Patch") % elsewhere
            d = self.decide(self.patch_payload(command))
            self.assertDenied(d, "for an absolute target outside the worktree")

    # -- 10. a malformed apply_patch payload: deny ---------------------------

    def test_10_a_command_with_no_begin_or_end_markers_is_denied_not_assumed_safe(self):
        d = self.decide(self.patch_payload("this is not a patch at all"))
        reason = self.assertDenied(d, "for command text carrying no patch markers")
        self.assertIn("denied rather than assumed safe", reason)

    def test_10_a_missing_command_is_denied_not_assumed_safe(self):
        p = {"tool_name": "apply_patch", "tool_input": {},
             "session_id": MY_SESSION, "cwd": self.root, "project_dir": self.root}
        d = self.decide(p)
        self.assertDenied(d, "for an apply_patch payload carrying no command text at all")

    def test_10_a_header_naming_no_path_is_denied_not_assumed_safe(self):
        command = "*** Begin Patch\n*** Update File: \n*** End Patch"
        d = self.decide(self.patch_payload(command))
        self.assertDenied(d, "for an Update File header that names no path")

    def test_10_a_patch_with_no_file_section_at_all_is_denied_not_assumed_safe(self):
        command = "*** Begin Patch\n*** End Patch"
        d = self.decide(self.patch_payload(command))
        self.assertDenied(d, "for a patch naming no Add, Update, or Delete section")

    # -- 11. a normal Claude Write payload: retain existing behavior --------

    def test_11_a_normal_write_payload_is_unaffected_by_the_apply_patch_change(self):
        target = os.path.join(self.root, "docs", "SETUP.md")
        self.assertDenied(self.decide(self.payload(target)),
                          "a Write into a fenced file must still be denied")
        self.assertAllowed(
            self.decide(self.payload(target, session=OWNER_SESSION)),
            "a Write by the fence's own owner must still be allowed")

    # -- 12. a Bash payload: retain existing guard behavior ------------------

    def test_12_a_bash_payload_is_still_not_gated(self):
        p = {"tool_name": "Bash",
             "tool_input": {"command": "echo x > %s"
                                       % os.path.join(self.root, "docs", "SETUP.md")},
             "session_id": MY_SESSION, "cwd": self.root, "project_dir": self.root}
        d = self.decide(p)
        self.assertAllowed(d, "Bash remains outside this hook's tool surface")
        self.assertEqual(d.notes, [], "Bash must remain silent, unaffected by apply_patch")

    # -- direct coverage of the parser, beyond the twelve fixture cases -----

    def test_apply_patch_targets_collects_source_and_move_destination_both(self):
        command = ("*** Begin Patch\n"
                   "*** Update File: old_name.py\n"
                   "*** Move to: new_name.py\n"
                   "@@\n-a\n+b\n"
                   "*** End Patch")
        self.assertEqual(fh.apply_patch_targets(command),
                         ["old_name.py", "new_name.py"])

    def test_a_move_to_line_with_no_preceding_update_file_is_denied(self):
        command = ("*** Begin Patch\n"
                   "*** Move to: somewhere.py\n"
                   "*** End Patch")
        with self.assertRaises(fh.PatchDeny):
            fh.apply_patch_targets(command)


# ---------------------------------------------------------------------------
# The wire protocol, exercised as Claude Code actually invokes it: a subprocess
# with a JSON payload on stdin.
# ---------------------------------------------------------------------------

class TestWireProtocol(FenceCase):

    def run_hook(self, payload, env_extra=None):
        env = dict(os.environ)
        env.pop("BROTHERSBE_REGISTRIES", None)
        env.update(env_extra or {})
        # A Python-side timeout, not the `timeout` command, which does not exist
        # on this platform. A hook that hangs is a hook that hangs the editor, so
        # the bound is part of the contract and not a test convenience.
        return subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps(payload), capture_output=True, text=True,
            timeout=60, env=env, cwd=self.root)

    def test_a_deny_lands_on_stdout_as_json_at_exit_zero(self):
        r = self.run_hook(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
        self.assertEqual(r.returncode, 0,
                         "the hook must always exit 0; exit 2 means the hook "
                         "itself failed, and every failure here is a fail-open")
        obj = json.loads(r.stdout)
        out = obj["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("LANDED", out["permissionDecisionReason"])

    def test_stdout_carries_the_decision_and_nothing_else(self):
        """Claude Code parses stdout as JSON. A diagnostic there corrupts the
        protocol, which is why every note goes to stderr."""
        os.remove(self.registry)
        r = self.run_hook(self.payload(os.path.join(self.root, "docs", "SETUP.md")))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "",
                         "an allow writes NOTHING to stdout; got: %r" % r.stdout)
        self.assertIn("FAILING OPEN", r.stderr)

    def test_empty_stdin_fails_open_at_exit_zero(self):
        r = subprocess.run([sys.executable, HOOK_PATH], input="",
                           capture_output=True, text=True, timeout=60, cwd=self.root)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("FAILING OPEN", r.stderr)
        self.assertIn("stdin was empty", r.stderr)

    def test_unparseable_stdin_fails_open_at_exit_zero(self):
        r = subprocess.run([sys.executable, HOOK_PATH], input="{not json",
                           capture_output=True, text=True, timeout=60, cwd=self.root)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("FAILING OPEN", r.stderr)
        self.assertIn("not valid JSON", r.stderr)

    def test_the_fences_subcommand_reports_without_touching_stdout(self):
        r = subprocess.run([sys.executable, HOOK_PATH, "fences", self.root],
                           capture_output=True, text=True, timeout=60, cwd=self.root)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "",
                         "diagnostics must never reach the decision channel")
        self.assertIn("doc-writer", r.stderr)
        self.assertIn("1 live fence line(s) enforceable", r.stderr)

    def test_an_unknown_subcommand_is_refused_rather_than_read_as_a_hook_call(self):
        r = subprocess.run([sys.executable, HOOK_PATH, "claim"], input="",
                           capture_output=True, text=True, timeout=60, cwd=self.root)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown command", r.stderr)

    # -- the real Codex hook payload shape, not just the in-process decide() -

    def codex_payload(self, command):
        """The exact payload shape the implementation contract's Phase 5
        quotes for a Codex hook call: the fields sbe_fence_hook.py reads
        (tool_name, tool_input.command, session_id, cwd, project_dir) plus
        the ones it does not (transcript_path, permission_mode,
        hook_event_name, tool_use_id), present to prove they are tolerated
        rather than assumed absent."""
        return {
            "session_id": MY_SESSION,
            "transcript_path": "/tmp/fixture.jsonl",
            "cwd": self.root,
            "permission_mode": "default",
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": command},
            "tool_use_id": "fixture-tool",
            "project_dir": self.root,
        }

    def test_an_allowed_apply_patch_call_writes_nothing_to_stdout(self):
        write(os.path.join(self.root, "src_example.py"), "old\n")
        command = ("*** Begin Patch\n*** Update File: src_example.py\n"
                   "@@\n-old\n+new\n*** End Patch")
        r = self.run_hook(self.codex_payload(command))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "",
                         "an allowed apply_patch call must write nothing to stdout")

    def test_a_denied_apply_patch_call_lands_on_stdout_as_json_at_exit_zero(self):
        command = ("*** Begin Patch\n*** Update File: docs/SETUP.md\n"
                   "@@\n-old\n+new\n*** End Patch")
        r = self.run_hook(self.codex_payload(command))
        self.assertEqual(r.returncode, 0,
                         "the hook must always exit 0, even for a deny")
        obj = json.loads(r.stdout)
        out = obj["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("docs/SETUP.md", out["permissionDecisionReason"])

    def test_a_malformed_apply_patch_call_is_denied_at_exit_zero_not_allowed(self):
        command = "*** Begin Patch\n*** End Patch"
        r = self.run_hook(self.codex_payload(command))
        self.assertEqual(r.returncode, 0)
        obj = json.loads(r.stdout)
        self.assertEqual(obj["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("denied rather than assumed safe",
                      obj["hookSpecificOutput"]["permissionDecisionReason"])


# ---------------------------------------------------------------------------
# Housekeeping the project asserts about itself elsewhere, asserted here for the
# two files this port adds.
# ---------------------------------------------------------------------------

class TestShippedFileHygiene(unittest.TestCase):

    FILES = (HOOK_PATH,
             os.path.join(HERE, "test_sbe_fence_hook.py"),
             os.path.join(os.path.dirname(HERE), "docs", "HOOKS.md"))

    def test_no_em_or_en_dashes(self):
        # The two characters are built with chr(), never typed. Spelling them as
        # literals would put them in this file, and this file is one of the files
        # the rule covers: the test would then fail on its own assertion text,
        # which is a false alarm that teaches the reader to weaken the rule.
        banned = ((chr(0x2014), "em dash"), (chr(0x2013), "en dash"))
        for path in self.FILES:
            if not os.path.isfile(path):
                self.fail("%s is missing; the port ships all three" % path)
            with io.open(path, "r", encoding="utf-8") as f:
                body = f.read()
            for ch, name in banned:
                self.assertNotIn(ch, body, "%s carries an %s" % (path, name))

    def test_the_hook_imports_nothing_outside_the_standard_library(self):
        """BrotherSBE is standalone by design: clone it and it works with nothing
        else installed. The only non-stdlib names this file may reach for are its
        own siblings under tools/, and those are loaded by path at call time."""
        import ast
        with io.open(HOOK_PATH, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=HOOK_PATH)
        allowed = set(sys.builtin_module_names) | {
            "fnmatch", "json", "os", "posixpath", "re", "sys", "importlib",
            "importlib.util", "glob", "ast", "io", "subprocess", "stat"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertIn(a.name.split(".")[0], allowed,
                                  "%s is not a standard library module" % a.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertIn(node.module.split(".")[0], allowed,
                              "%s is not a standard library module" % node.module)

    def test_the_hook_makes_no_network_call_and_runs_no_subprocess(self):
        with io.open(HOOK_PATH, "r", encoding="utf-8") as f:
            body = f.read()
        for forbidden in ("subprocess", "urllib", "socket", "http.client",
                          "os.system", "os.popen"):
            self.assertNotIn(
                "import " + forbidden, body,
                "the hook must make no network call and run no subprocess; it "
                "runs in front of every edit")


class TestHelpMeansHelp(unittest.TestCase):
    """`-h` used to start with a dash, miss the command table, and fall into
    HOOK mode: the tool sat reading stdin for a JSON payload nobody was going
    to send, then failed open, and `fences --bogus` was read as a directory
    named --bogus and reported "no fence is enforceable", exit 0. Help is not
    an error and a flag is not a directory. The bare hook invocation is
    untouched: it still fails open on an empty stdin, because a hook must
    never block a session. Calibrated by reinjecting the missing help branch:
    the help fixture failed (usage absent, hook fail-open text present)
    before the fix was restored, the restore verified against the
    pre-recorded `git hash-object` of the fixed file."""

    def _run(self, *argv):
        out = subprocess.run([sys.executable, HOOK_PATH] + list(argv),
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return out.returncode, out.stdout, out.stderr

    def test_help_exits_0_with_usage_on_stderr_and_a_clean_stdout(self):
        """Usage goes to stderr because stdout is the hook's decision channel:
        a usage text on stdout would corrupt the protocol if this ever ran in
        the hook slot with a stray flag."""
        for argv in (("-h",), ("--help",), ("fences", "-h")):
            code, stdout, stderr = self._run(*argv)
            self.assertEqual(code, 0, "%s exited %d: %s"
                             % (" ".join(argv), code, stdout + stderr))
            self.assertIn("usage: sbe_fence_hook.py", stderr,
                          "%s printed no usage on stderr: %r" % (" ".join(argv), stderr))
            self.assertEqual(stdout, "",
                             "%s wrote to stdout, the decision channel" % " ".join(argv))

    def test_a_bad_flag_on_fences_exits_2_instead_of_reading_it_as_a_directory(self):
        code, stdout, stderr = self._run("fences", "--bogus")
        self.assertEqual(code, 2, "fences --bogus exited %d: %s" % (code, stdout + stderr))
        self.assertIn("unrecognized flag", stderr)
        self.assertEqual(stdout, "", "the refusal leaked onto the decision channel")

    def test_the_bare_hook_invocation_still_fails_open(self):
        code, stdout, stderr = self._run()
        self.assertEqual(code, 0,
                         "the bare hook invocation must never block a session; it "
                         "exited %d: %s" % (code, stderr))
        self.assertIn("FAILING OPEN", stderr)


# ---------------------------------------------------------------------------
# The SECOND registry shape: `.sbe/tasks.json`, written by the product's own
# `sbe task open` CLI, not a hand-built JSON fixture. Every fence here comes
# from a real subprocess call to `bin/sbe`, and every decision comes from a
# real subprocess call to the hook, JSON on stdin, exactly as Claude Code
# invokes it: these are the four orchestrator reproductions, replayed.
# ---------------------------------------------------------------------------

ROOT_DIR = os.path.dirname(HERE)
SBE_BIN = os.path.join(ROOT_DIR, "bin", "sbe")


def _git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


class TaskRegistryFenceCase(unittest.TestCase):
    """A real git repository, with `.sbe/tasks.json` written by the real
    `sbe task open` CLI rather than assembled by hand, matched against the
    hook run as a real subprocess."""

    ENV_KEYS = ("BROTHERSBE_REGISTRIES", "BROTHERSBE_FENCE_SESSION",
                "BROTHERSBE_FENCE_HOOK_OFF", "CLAUDE_CONFIG_DIR",
                "CLAUDE_SESSION_ID", "SBE_SESSION_ID")

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "fixture@example.invalid")
        _git(self.root, "config", "user.name", "fixture")
        os.makedirs(os.path.join(self.root, "tools"))
        write(os.path.join(self.root, "tools", "f.py"), "x = 1\n")
        write(os.path.join(self.root, "README.md"), "unrelated\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "base")
        self.base = _git(self.root, "rev-parse", "HEAD")
        # Same reason FenceCase pins this: companion detection reads
        # CLAUDE_CONFIG_DIR off the real filesystem, and pinning it to an
        # empty directory this test controls keeps the suite hermetic
        # regardless of what happens to be installed on the host running it.
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.root, "claude-config")

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def sbe_task_open(self, task_id="lane-x", agent="tester", role="writer",
                      owns=("tools/f.py",), session_env=None, extra=()):
        """`bin/sbe task open`, the real product CLI, exactly the invocation
        the brief reproduces the defect with. `session_env` is applied ONLY
        to this call's environment (never to the hook's, below), the same
        separation `src/brothersbe/tasks.py`'s own docstring draws between
        the writer's identity at open time and the reader's at decision
        time."""
        env = dict(os.environ)
        env.update(session_env or {})
        argv = [sys.executable, SBE_BIN, "task", "open", "--id", task_id,
               "--agent", agent, "--role", role, "--base", self.base,
               "--verify", "true", "--cwd", self.root]
        for p in owns:
            argv += ["--owns", p]
        argv += list(extra)
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=env)
        if out.returncode != 0:
            raise AssertionError("sbe task open failed: %s" % (out.stdout + out.stderr))
        return out

    def run_hook(self, payload, env_extra=None):
        env = dict(os.environ)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, HOOK_PATH], input=json.dumps(payload),
            capture_output=True, text=True, timeout=60, env=env, cwd=self.root)

    def payload(self, path, session, tool="Write"):
        return {"tool_name": tool, "tool_input": {"file_path": path, "content": "x"},
               "session_id": session, "cwd": self.root, "project_dir": self.root}

    def target(self, rel="tools/f.py"):
        return os.path.join(self.root, *rel.split("/"))


class TestTaskRegistryFences(TaskRegistryFenceCase):
    """Runs 1 to 4 of the orchestrator's own reproduction, replayed against
    the fixed hook, plus the two properties the brief names by name: a
    genuine conflict is refused, and an absent identity fails open rather
    than either denying or silently matching."""

    # -- Run 2 / Run 4: a real fence, the rightful holder -------------------

    def test_a_real_fence_holder_session_is_allowed_and_not_via_fail_open(self):
        holder = "holder-session-aaaa1111"
        self.sbe_task_open(session_env={"CLAUDE_SESSION_ID": holder})
        r = self.run_hook(self.payload(self.target(), holder))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "",
                         "the rightful holder must be ALLOWED: stderr=%s" % r.stderr)
        self.assertNotIn(
            "FAILING OPEN", r.stderr,
            "the holder's allow must be a genuine session match, not a "
            "fail-open path that would allow anybody: %s" % r.stderr)

    # -- Run 4: the same fence, a session that is not the holder -----------

    def test_the_same_fence_a_different_session_is_denied_naming_the_holding_task(self):
        holder = "holder-session-aaaa1111"
        self.sbe_task_open(task_id="lane-x", session_env={"CLAUDE_SESSION_ID": holder})
        r = self.run_hook(self.payload(self.target(), "INTRUDER"))
        self.assertEqual(r.returncode, 0, r.stderr)
        obj = json.loads(r.stdout)
        out = obj["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        reason = out["permissionDecisionReason"]
        self.assertIn("lane-x", reason, "the refusal must name the holding task id")
        self.assertIn("tester", reason, "the refusal must name the holding agent")
        self.assertIn("tools/f.py", reason, "the refusal must name the path")

    # -- Run 2 / Run 3 as originally reproduced: today's shape, no session --

    def test_a_task_record_with_no_session_field_is_allowed_and_fails_open_naming_it(self):
        """Exactly Run 2 and Run 3 as the orchestrator ran them: `sbe task
        open` with neither CLAUDE_SESSION_ID nor SBE_SESSION_ID set, the
        shape every registry written before this build carries. This must
        keep failing open, by design (see the module docstring's "TWO
        REGISTRY SHAPES" section): a record with no session cannot be proven
        to conflict with anybody, so an absent session is not a match-all
        that denies every later writer either."""
        self.sbe_task_open(task_id="lane-x")
        with io.open(os.path.join(self.root, ".sbe", "tasks.json"),
                     encoding="utf-8") as f:
            record = json.load(f)
        self.assertNotIn(
            "session", record["tasks"][0],
            "the fixture must reproduce today's shape: no session recorded")
        r = self.run_hook(self.payload(self.target(), "any-session-cccc3333"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout, "", "an unrecorded session must never deny: %s" % r.stderr)
        self.assertIn("FAILING OPEN", r.stderr)
        self.assertIn("no session identity", r.stderr)
        self.assertIn("lane-x", r.stderr, "the fail-open reason should still "
                      "be traceable to the task it came from")

    def test_registries_named_by_the_env_var_are_no_longer_needed_for_discovery(self):
        """Part 1 of the fix: discovery must work with BROTHERSBE_REGISTRIES
        UNSET (Run 2, exactly as reproduced). This is the same fixture as the
        no-session test above, asserted from the discovery angle: the
        registry is found without any environment variable naming it."""
        self.assertNotIn("BROTHERSBE_REGISTRIES", os.environ)
        holder = "holder-session-dddd4444"
        self.sbe_task_open(session_env={"CLAUDE_SESSION_ID": holder})
        r = self.run_hook(self.payload(self.target(), "INTRUDER"))
        self.assertEqual(r.returncode, 0, r.stderr)
        obj = json.loads(r.stdout)
        self.assertEqual(obj["hookSpecificOutput"]["permissionDecision"], "deny",
                         "the task registry must be found with no BROTHERSBE_"
                         "REGISTRIES set at all: %s" % r.stderr)

    # -- no registry anywhere ------------------------------------------------

    def test_no_registry_anywhere_is_allowed_and_fails_open_as_before(self):
        r = self.run_hook(self.payload(self.target(), "any-session"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("FAILING OPEN", r.stderr)
        self.assertIn("no fence registry was opened", r.stderr)

    # -- an unreadable / corrupt task registry -------------------------------

    def test_a_corrupt_tasks_json_is_allowed_and_fails_open_naming_the_reason(self):
        self.sbe_task_open()
        write(os.path.join(self.root, ".sbe", "tasks.json"), "{not json")
        r = self.run_hook(self.payload(self.target(), "any-session"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("FAILING OPEN", r.stderr)
        self.assertIn("could not be read", r.stderr)

    # -- outside every fence --------------------------------------------------

    def test_an_edit_outside_every_owned_path_is_allowed_with_a_live_fence_present(self):
        self.sbe_task_open(session_env={"CLAUDE_SESSION_ID": "holder-session-eeee5555"})
        r = self.run_hook(self.payload(self.target("README.md"), "INTRUDER"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout, "",
            "a path outside every declared ownedPaths must be allowed even "
            "while a real, session-bearing fence is live: %s" % r.stderr)

    # -- the Codex apply_patch exception is unaffected by any of this --------

    def test_apply_patch_into_a_real_task_fence_still_denies_rather_than_fail_open(self):
        """Not a new behavior: `decide()`'s apply_patch branch is untouched
        by this fix. Proven directly against a real task-registry fence
        (rather than only the markdown fixture TestApplyPatch already
        covers), so the two registry shapes are not left to diverge here
        silently the way they did before this file read only one of them."""
        holder = "holder-session-ffff6666"
        self.sbe_task_open(session_env={"CLAUDE_SESSION_ID": holder})
        command = ("*** Begin Patch\n*** Update File: tools/f.py\n"
                  "@@\n-x = 1\n+x = 2\n*** End Patch")
        payload = {"tool_name": "apply_patch",
                  "tool_input": {"command": command},
                  "session_id": "INTRUDER", "cwd": self.root, "project_dir": self.root}
        r = self.run_hook(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        obj = json.loads(r.stdout)
        self.assertEqual(obj["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("lane-x", obj["hookSpecificOutput"]["permissionDecisionReason"])


class TestShippedMatcherCoversEveryWriteTool(unittest.TestCase):
    """The wiring drift test the 2026-08-11 refuter earned: WRITE_TOOLS grew
    apply_patch, CreateDirectory and Delete while hooks/hooks.json still
    matched the original four, so neither hook ever RAN for the very tool the
    apply_patch parser was built for. A guard that is never invoked is
    indistinguishable from one that allows."""

    def _pretooluse_matchers(self):
        import json as _json
        path = os.path.join(os.path.dirname(HERE), "hooks", "hooks.json")
        with open(path) as fh:
            data = _json.load(fh)
        out = []
        for entry in data["hooks"]["PreToolUse"]:
            cmds = " ".join(h.get("command", "") for h in entry.get("hooks", []))
            out.append((entry.get("matcher", ""), cmds))
        return out

    def test_both_file_hooks_match_every_declared_write_tool(self):
        import re
        matchers = self._pretooluse_matchers()
        for hook_name in ("sbe_fence_hook.py", "sbe_authority_hook.py"):
            rows = [(m, c) for (m, c) in matchers if hook_name in c]
            self.assertTrue(rows, "no PreToolUse entry runs %s" % hook_name)
            for matcher, _cmds in rows:
                pat = re.compile(matcher)
                for tool in sorted(fh.WRITE_TOOLS):
                    self.assertIsNotNone(
                        pat.fullmatch(tool),
                        "%s's matcher %r does not cover write tool %r, so the "
                        "hook never runs for it" % (hook_name, matcher, tool))


DERIVED_LABEL = "bm1-0a17e1d9c18bb501fbc63a96"
RAW_HARNESS_ID = "5d94151e-07ec-4197-8430-5dce907e547c"


class TestDeclaredSessionAliases(FenceCase):
    """M13, from a MEASURED failure in a sibling estate, not a hypothetical.

    A sibling store claimed a fence under a label it derived from the harness
    session id, and announced while claiming that it was doing so precisely so
    the owner's own next edit would not be refused as a foreign writer. This
    hook refused that very session out of its own fence: a derived label shares
    no prefix with the raw id it came from, so the generous matching in
    `same_session` could not see them as one session.

    This hook cannot verify another tool's derivation and must not guess one,
    so the identity is DECLARED (SBE_SESSION_ALIASES) rather than inferred. The
    tests below pin all three arms, because closing only the false refusal
    would have cost the true one: a genuinely foreign session must still be
    refused."""

    def _fenced(self, owner):
        write(self.registry,
              "# State\n\n## Live fences\n"
              "- a6 (abc123, version 1, ephemeral) [T2] owner-session: %s, "
              "agent: (no owner) objective: x files: docs/SETUP.md |\n" % owner)
        return os.path.join(self.root, "docs", "SETUP.md")

    def test_the_rightful_owner_is_refused_when_the_label_is_not_declared(self):
        # The measured failure itself, pinned so the fix cannot silently
        # regress into it: without a declared alias the hook cannot know the
        # label is this session, so it still refuses, and the refusal must at
        # least TELL the owner how to declare it (asserted below).
        target = self._fenced(DERIVED_LABEL)
        reason = self.assertDenied(
            self.decide(self.payload(target, session=RAW_HARNESS_ID)),
            "the label is undeclared, so the hook cannot know it is this session")
        self.assertIn(fh.SESSION_ALIAS_ENV, reason)
        self.assertIn(DERIVED_LABEL, reason)

    def test_the_rightful_owner_is_allowed_once_the_label_is_declared(self):
        target = self._fenced(DERIVED_LABEL)
        os.environ[fh.SESSION_ALIAS_ENV] = DERIVED_LABEL
        self.addCleanup(lambda: os.environ.pop(fh.SESSION_ALIAS_ENV, None))
        decision = self.decide(self.payload(target, session=RAW_HARNESS_ID))
        self.assertIsNone(
            decision.payload,
            "the fence's rightful owner was refused out of its own fence even "
            "after declaring the label the claiming tool recorded")

    def test_a_genuinely_foreign_session_is_still_refused(self):
        # The true refusal the sibling also measured. Closing the false one
        # must not cost this one.
        target = self._fenced(DERIVED_LABEL)
        os.environ[fh.SESSION_ALIAS_ENV] = "bm1-someone-else-entirely"
        self.addCleanup(lambda: os.environ.pop(fh.SESSION_ALIAS_ENV, None))
        self.assertDenied(
            self.decide(self.payload(target, session="99999999-aaaa-bbbb-cccc-dddddddddddd")),
            "a session that owns nothing here declared an unrelated alias")

    def test_several_aliases_are_read_and_blanks_ignored(self):
        target = self._fenced(DERIVED_LABEL)
        os.environ[fh.SESSION_ALIAS_ENV] = " , bm1-unrelated , %s ,, " % DERIVED_LABEL
        self.addCleanup(lambda: os.environ.pop(fh.SESSION_ALIAS_ENV, None))
        self.assertIsNone(
            self.decide(self.payload(target, session=RAW_HARNESS_ID)).payload)

    def test_no_alias_variable_changes_nothing(self):
        target = self._fenced(RAW_HARNESS_ID)
        self.assertIsNone(
            self.decide(self.payload(target, session=RAW_HARNESS_ID)).payload,
            "an ordinary same-session match broke when aliases were added")


class TestALongFenceProtectsEveryPathItNames(FenceCase):
    """The sibling noticed a fence line quoted back TRUNCATED mid-path and
    asked, without claiming it, whether the scope is cut before MATCHING or
    only before printing. If it were cut before matching, a long fence would
    silently protect only its first few entries, which is worse than a fence
    that plainly does not work.

    Measured answer: the cut is display-only (`fence.line[:400]` in the refusal
    text). These tests pin that, so a future change that starts truncating the
    parsed scope fails here instead of in someone's estate."""

    def _long_fence(self, count):
        names = ["docs/a_rather_long_file_name_number_%d.md" % i
                 for i in range(1, count + 1)]
        for name in names:
            write(os.path.join(self.root, name), "x\n")
        write(self.registry,
              "# State\n\n## Live fences\n"
              "- huge (abc123, version 1, ephemeral) [T2] owner-session: owner-x, "
              "agent: (no owner) objective: a very long scope files: %s |\n"
              % ", ".join(names))
        return names

    def test_the_last_path_of_a_long_scope_is_still_refused(self):
        names = self._long_fence(300)
        for index in (0, len(names) // 2, len(names) - 1):
            target = os.path.join(self.root, names[index])
            self.assertDenied(
                self.decide(self.payload(target, session="INTRUDER")),
                "path %d of %d in a long fence scope was not compared at all, "
                "so the fence protected only its first entries"
                % (index + 1, len(names)))

    def test_the_refusal_text_stays_bounded_even_then(self):
        names = self._long_fence(300)
        reason = self.assertDenied(
            self.decide(self.payload(os.path.join(self.root, names[-1]),
                                     session="INTRUDER")))
        self.assertLess(len(reason), 4000,
                        "the refusal quoted an unbounded fence line")


if __name__ == "__main__":
    unittest.main(verbosity=2)
