#!/usr/bin/env python3
"""Regression tests for the BrotherSBE PreToolUse authority-file guard.

Run: python3 tools/test_sbe_authority_hook.py

Standard library only, matching the zero-dependency ethos of tools/. Structure
mirrors tools/test_sbe_fence_hook.py, this hook's closest sibling: a temp
project per test, an environment restored exactly as it was found, and the
same two properties carrying the file.

  FAIL OPEN, EXCEPT ONE RULE. Every failure this hook can construct (an
  unimportable helper, an unreadable or corrupt task registry, a malformed
  payload) must ALLOW and say why. The one exception is the rule this file
  exists to enforce: an authority-bearing file, positively identified, with
  no open task declaring it, in a detectable worker context, is REFUSED.
  `TestFailOpen` is the fail-open class; `TestTheOneRefusal` is the exception,
  proven both ways.

  IT SAYS WHY, AND THE RECOVERY IS NAMED. Every refusal states the task ID
  (or that none is open), the path, and the recovery action, in the exact
  words the brief specifies: "open a task record naming this path, or edit
  outside a worker context".
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOOK_PATH = os.path.join(HERE, "sbe_authority_hook.py")

# Import the module under test regardless of cwd, the same way
# tools/test_sbe_fence_hook.py imports sbe_fence_hook.
_spec = importlib.util.spec_from_file_location("sbe_authority_hook", HOOK_PATH)
ah = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ah)
sys.modules["sbe_authority_hook"] = ah

# Same module sbe_authority_hook.py itself loads by path, imported here too so
# tests can assert against its real WRITE_TOOLS rather than a second copy.
_fence_spec = importlib.util.spec_from_file_location(
    "sbe_fence_hook_for_test", os.path.join(HERE, "sbe_fence_hook.py"))
fh = importlib.util.module_from_spec(_fence_spec)
_fence_spec.loader.exec_module(fh)


def write(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _sha(byte=0x11):
    return ("%02x" % byte) * 20


class AuthorityCase(unittest.TestCase):
    """A temp git repository with no `.sbe/tasks.json` by default, and an
    environment restored exactly as found. `BROTHERSBE_AUTHORITY_HOOK_OFF` is
    the only environment variable this hook reads, but the fixture also
    creates a real git repo so `_worker_context`'s linked-worktree probe has
    something concrete (a real `.git` directory, which is NOT a linked
    worktree) to answer against, rather than an undefined answer over a
    directory with no `.git` at all."""

    ENV_KEYS = ("BROTHERSBE_AUTHORITY_HOOK_OFF",)

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        write(os.path.join(self.root, "CLAUDE.md"), "root instructions\n")
        write(os.path.join(self.root, "README.md"), "readme\n")

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    # -- registry helpers -----------------------------------------------

    def registry_path(self):
        return os.path.join(self.root, ".sbe", "tasks.json")

    def write_registry(self, tasks):
        write(self.registry_path(), json.dumps(
            {"schemaVersion": "1.0", "tasks": tasks}))

    def open_task(self, task_id="t1", agent="alpha", owns=None, status="open",
                  worktree=None):
        return {"id": task_id, "agent": agent, "role": "writer", "worktree": worktree,
                "ownedPaths": owns or [], "readOnlyPaths": [], "baseCommit": _sha(),
                "expiry": None, "status": status, "verifyCommand": "true",
                "evidenceId": None, "openedAt": "2026-01-01T00:00:00Z", "closedAt": None}

    # -- payload builders -------------------------------------------------

    def payload(self, path, tool="Write", session="worker-session-1", **extra):
        p = {"tool_name": tool,
             "tool_input": {"file_path": path, "content": "x"},
             "session_id": session,
             "cwd": self.root,
             "project_dir": self.root}
        p.update(extra)
        return p

    def decide(self, payload):
        return ah.decide(payload)

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
# Acceptance 1 and 2: the scoped task is allowed, the unscoped worker refused.
# ---------------------------------------------------------------------------

class TestTheOneRefusal(AuthorityCase):

    def test_a_scoped_task_can_edit_its_declared_authority_file(self):
        """Acceptance 1: an open registry record owning .claude/settings.json
        can edit exactly that file."""
        write(os.path.join(self.root, ".claude", "settings.json"), "{}\n")
        self.write_registry([self.open_task(owns=[".claude/settings.json"])])
        d = self.decide(self.payload(os.path.join(self.root, ".claude", "settings.json")))
        self.assertAllowed(d, "for a task that declared exactly this authority file")

    def test_a_worker_without_a_declaring_record_is_refused(self):
        """Acceptance 2: a worker without such a record cannot edit
        .claude/settings.json. Worker context here comes from an open task
        existing at all (t1, which owns something else), not a worktree."""
        write(os.path.join(self.root, ".claude", "settings.json"), "{}\n")
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.payload(os.path.join(self.root, ".claude", "settings.json")))
        reason = self.assertDenied(d, "for an authority file no open task declares")
        self.assertIn(".claude/settings.json", reason)

    def test_the_refusal_names_the_task_state_the_path_and_the_recovery(self):
        self.write_registry([self.open_task(task_id="wave5-alpha", owns=["src/unrelated.py"])])
        d = self.decide(self.payload(os.path.join(self.root, "CLAUDE.md")))
        reason = self.assertDenied(d)
        self.assertIn("wave5-alpha", reason, "the refusal must name the open task")
        self.assertIn("CLAUDE.md", reason, "the refusal must name the path")
        self.assertIn(
            "open a task record naming this path, or edit outside a worker context",
            reason, "the refusal must name the recovery action in the brief's own words")

    def test_the_refusal_says_no_task_is_open_when_none_is(self):
        """No registry at all, but a linked-worktree signal supplies worker
        context (see TestWorkerContextSignal for the direct proof of that
        signal); the refusal must say plainly that no task is open, not name
        one that does not exist."""
        git_file = os.path.join(self.root, ".git")
        # Replace the real .git directory with the FILE shape a linked
        # worktree leaves behind, to force the worktree signal on without
        # depending on a second real git checkout in this test.
        import shutil
        shutil.rmtree(git_file)
        write(git_file, "gitdir: /somewhere/else\n")
        d = self.decide(self.payload(os.path.join(self.root, "CLAUDE.md")))
        reason = self.assertDenied(d, "for a linked worktree with no open task")
        self.assertIn("no task is open", reason)

    def test_the_escape_the_refusal_names_actually_works(self):
        """The project's standing rule: a named escape must be one that
        actually works. Declare the path in the task's ownedPaths, exactly
        as the refusal's recovery action says to, and replay the identical
        payload."""
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        p = self.payload(os.path.join(self.root, "CLAUDE.md"))
        before = self.decide(p)
        self.assertDenied(before, "before the path is declared")

        with io.open(self.registry_path(), encoding="utf-8") as f:
            data = json.load(f)
        data["tasks"][0]["ownedPaths"].append("CLAUDE.md")
        write(self.registry_path(), json.dumps(data))

        after = self.decide(p)
        self.assertAllowed(after, "after the path was added to ownedPaths, exactly as "
                                  "the refusal's recovery action said to")

    def test_a_directory_scope_covers_a_file_beneath_it(self):
        self.write_registry([self.open_task(owns=[".claude/"])])
        write(os.path.join(self.root, ".claude", "settings.json"), "{}\n")
        d = self.decide(self.payload(os.path.join(self.root, ".claude", "settings.json")))
        self.assertAllowed(d, "for an authority file under a declared directory scope")

    def test_a_non_authority_file_is_never_blocked_even_undeclared(self):
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.payload(os.path.join(self.root, "README.md")))
        self.assertAllowed(d, "README.md is not one of the nine authority families")

    def test_a_closed_task_does_not_count_as_declaring_scope(self):
        self.write_registry([self.open_task(owns=["CLAUDE.md"], status="closed")])
        d = self.decide(self.payload(os.path.join(self.root, "CLAUDE.md")))
        # A closed task is not "open" (worker context requires a LIVE task),
        # and no linked worktree exists in this fixture, so this is an ALLOW
        # by the no-detectable-worker-context default, never a false refusal
        # over a task that no longer owns anything.
        self.assertAllowed(d, "a closed task's ownedPaths do not carry forward")


# ---------------------------------------------------------------------------
# The worker-context signal, proven directly.
# ---------------------------------------------------------------------------

class TestWorkerContextSignal(AuthorityCase):

    def test_no_registry_and_no_linked_worktree_allows_the_undeclared_edit(self):
        """The base case this hook must not break: an ordinary interactive
        session, no task registry anywhere, editing CLAUDE.md directly."""
        d = self.decide(self.payload(os.path.join(self.root, "CLAUDE.md")))
        notes = self.assertAllowed(d, "no registry, no worktree signal, no worker context")
        self.assertIn("no worker context was detected", notes)

    def test_an_open_task_alone_is_sufficient_worker_context(self):
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        self.assertDenied(
            self.decide(self.payload(os.path.join(self.root, "CLAUDE.md"))),
            "an open task, even one owning something else, is worker context")

    def test_a_linked_worktree_alone_is_sufficient_worker_context(self):
        import shutil
        shutil.rmtree(os.path.join(self.root, ".git"))
        write(os.path.join(self.root, ".git"), "gitdir: /somewhere/else\n")
        self.assertDenied(
            self.decide(self.payload(os.path.join(self.root, "CLAUDE.md"))),
            "a linked worktree's .git FILE is worker context with no registry at all")

    def test_a_real_git_directory_is_not_a_linked_worktree(self):
        """The fixture's own setUp runs a real `git init`, which leaves `.git`
        as a DIRECTORY. That must not itself be read as worker context."""
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".git")))
        d = self.decide(self.payload(os.path.join(self.root, "CLAUDE.md")))
        self.assertAllowed(d, "an ordinary .git directory is not a linked worktree")


# ---------------------------------------------------------------------------
# Acceptance 3: case variants.
# ---------------------------------------------------------------------------

class TestCaseVariants(AuthorityCase):

    def test_a_wrong_case_spelling_that_is_not_the_real_file_is_not_authority(self):
        """docs/CLAUDE.MD is not CLAUDE.md: the surface list is case-sensitive
        by design (tools/sbe_instruction_surface.py's own basename check), and
        a file that merely LOOKS similar is not the authority surface. Skipped
        on a case-insensitive filesystem, where writing "docs/CLAUDE.MD" IS
        writing "docs/CLAUDE.md": there is only one directory entry there, and
        blocking it is then correct, not a false positive. That collision is
        covered directly by
        test_a_case_folded_filesystem_collision_with_the_real_file_is_still_authority."""
        write(os.path.join(self.root, "docs", "CLAUDE.MD"), "not the real one\n")
        exact = os.path.join(self.root, "docs", "CLAUDE.md")
        if os.path.exists(exact):
            raise unittest.SkipTest(
                "this filesystem folds case; docs/CLAUDE.MD and docs/CLAUDE.md are one "
                "entry here, so this scenario cannot be constructed")
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.payload(os.path.join(self.root, "docs", "CLAUDE.MD")))
        self.assertAllowed(d, "docs/CLAUDE.MD is a different, non-authority file by design")

    def test_a_case_folded_filesystem_collision_with_the_real_file_is_still_authority(self):
        """The hazard: on a case-insensitive filesystem, writing to
        "claude.md" can land on the SAME on-disk entry as the real CLAUDE.md
        this fixture's setUp already created. Confirmed by inode, the same
        proof tools/test_sbe_fence_hook.py's own case-fold test uses on
        itself before trusting the fixture; skipped on a case-sensitive
        filesystem where the two are honestly different files."""
        exact = os.path.join(self.root, "CLAUDE.md")
        variant = os.path.join(self.root, "claude.md")
        if not os.path.exists(variant):
            raise unittest.SkipTest(
                "this filesystem is case-sensitive; claude.md is a different file here")
        self.assertEqual(os.stat(exact).st_ino, os.stat(variant).st_ino)
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.payload(variant))
        reason = self.assertDenied(
            d, "for a case-variant spelling the filesystem confirms is the real CLAUDE.md")
        self.assertIn("CLAUDE.md", reason)

    def test_a_case_folded_string_match_alone_is_never_trusted(self):
        """The Linux half of the fix: on a case-sensitive filesystem this
        machine cannot construct the real collision above, so the filesystem
        confirmation is forced to answer False and the case-folded candidate
        must not be trusted on the string match alone."""
        # This scenario needs the STRING MATCH to be the only evidence, which
        # is why the filesystem confirmation is forced to answer False. On a
        # host that canonicalises the spelling, the operating system supplies
        # real confirmation before this function is ever consulted: Windows
        # resolves "claude.md" to the true on-disk entry "CLAUDE.md", so the
        # guard matches exactly and denies, correctly, on evidence this test is
        # trying to withhold. That is not the behaviour under test failing, it
        # is the premise of the test being unbuildable, so it is skipped there
        # and the sibling above covers that world instead. Measured by asking
        # the filesystem, never by naming a platform.
        variant_probe = os.path.join(self.root, "claude.md")
        if os.path.exists(variant_probe) and \
                os.path.basename(os.path.realpath(variant_probe)) != "claude.md":
            raise unittest.SkipTest(
                "this filesystem canonicalises the spelling (realpath answers %r), so the "
                "operating system confirms the match and 'string match alone' cannot be "
                "constructed here" % os.path.basename(os.path.realpath(variant_probe)))
        original = fh._same_entry_case_insensitive
        fh_for_authority = ah.load_fence_module()
        original_loaded = fh_for_authority._same_entry_case_insensitive
        fh_for_authority._same_entry_case_insensitive = lambda root, t, c: False
        try:
            self.write_registry([self.open_task(owns=["src/unrelated.py"])])
            d = self.decide(self.payload(os.path.join(self.root, "claude.md")))
            self.assertAllowed(
                d, "a case-folded string match must not be trusted without filesystem "
                   "confirmation")
        finally:
            fh_for_authority._same_entry_case_insensitive = original_loaded
        self.assertIs(fh._same_entry_case_insensitive, original)


# ---------------------------------------------------------------------------
# Acceptance 4: symlink variants.
# ---------------------------------------------------------------------------

class TestSymlinkVariants(AuthorityCase):

    def test_a_symlink_to_an_authority_file_is_treated_as_that_file(self):
        link = os.path.join(self.root, "notes-link.md")
        target = os.path.join(self.root, "CLAUDE.md")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            raise unittest.SkipTest("this platform cannot create symlinks")
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.payload(link))
        reason = self.assertDenied(d, "for a symlink whose target is CLAUDE.md")
        self.assertIn("CLAUDE.md", reason)

    def test_a_symlink_into_an_authority_directory_is_treated_as_inside_it(self):
        os.makedirs(os.path.join(self.root, ".claude"))
        link_dir = os.path.join(self.root, "dotclaude-link")
        try:
            os.symlink(os.path.join(self.root, ".claude"), link_dir)
        except (OSError, NotImplementedError):
            raise unittest.SkipTest("this platform cannot create symlinks")
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.payload(os.path.join(link_dir, "settings.json")))
        self.assertDenied(d, "for a path reached through a symlink into .claude/")

    def test_a_symlink_to_a_non_authority_file_is_not_blocked(self):
        link = os.path.join(self.root, "readme-link.md")
        target = os.path.join(self.root, "README.md")
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            raise unittest.SkipTest("this platform cannot create symlinks")
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.payload(link))
        self.assertAllowed(d, "the symlink's target is not an authority file")


# ---------------------------------------------------------------------------
# Acceptance 5: hook unavailability is visible.
# ---------------------------------------------------------------------------

class TestFailOpen(AuthorityCase):

    def assertFailedOpen(self, decision, expect_in_reason, why):
        notes = self.assertAllowed(decision, why)
        self.assertIn("FAILING OPEN", notes,
                      "the fail-open must SAY it failed open (%s). notes: %s" % (why, notes))
        self.assertIn(
            "the authority-file guard was NOT checked", notes,
            "the fail-open must say the guard went unchecked (%s)" % why)
        self.assertIn(expect_in_reason, notes,
                      "the fail-open must name its cause (%s). notes: %s" % (why, notes))
        return notes

    def test_a_broken_registry_allows_a_non_protected_path_and_says_why(self):
        write(self.registry_path(), "{not json")
        d = self.decide(self.payload(os.path.join(self.root, "README.md")))
        # README.md is not authority-bearing, so it is never examined against
        # the registry at all: silent, matching a non-write tool's silence.
        self.assertAllowed(d, "a non-authority path with a broken registry")

    def test_a_broken_registry_allows_a_protected_path_but_says_so_loudly(self):
        """This is the fixture the brief names directly: a broken registry
        must not silently pass the protected rule. It DOES allow (every
        error path fails open), but the allow is loud, never silent."""
        write(self.registry_path(), "{not json")
        d = self.decide(self.payload(os.path.join(self.root, "CLAUDE.md")))
        self.assertFailedOpen(d, "could not be read", "the registry is corrupt JSON")

    def test_an_unparseable_hook_payload_fails_open_and_says_so(self):
        for bad in ([], "a string", 7, None):
            self.assertFailedOpen(self.decide(bad),
                                  "hook payload was not a JSON object",
                                  "the payload is %r" % (bad,))

    def test_tool_input_not_an_object_fails_open(self):
        self.assertFailedOpen(
            self.decide({"tool_name": "Write", "tool_input": "oops",
                         "session_id": "s", "cwd": self.root}),
            "was not a JSON object", "tool_input is not an object")

    def test_no_target_path_fails_open(self):
        self.assertFailedOpen(
            self.decide({"tool_name": "Write", "tool_input": {"content": "x"},
                         "session_id": "s", "cwd": self.root}),
            "no target path found", "the write tool's input names no path")

    def test_the_disable_switch_allows_and_says_so(self):
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        os.environ["BROTHERSBE_AUTHORITY_HOOK_OFF"] = "1"
        d = self.decide(self.payload(os.path.join(self.root, "CLAUDE.md")))
        notes = self.assertAllowed(d, "the disable switch is set")
        self.assertIn("BROTHERSBE_AUTHORITY_HOOK_OFF", notes)
        self.assertIn("the authority-file guard was NOT checked", notes)

    def test_an_unimportable_surface_module_fails_open(self):
        original = ah.load_surface_module
        ah.load_surface_module = lambda: None
        try:
            d = self.decide(self.payload(os.path.join(self.root, "CLAUDE.md")))
            self.assertFailedOpen(
                d, "could not be imported", "the surface module cannot be imported")
        finally:
            ah.load_surface_module = original

    def test_no_failure_path_can_produce_a_deny(self):
        """The structural version of the property, mirroring
        tools/test_sbe_fence_hook.py's own test of the same name: over every
        malformed payload this file can think of, the hook is asserted never
        to deny."""
        target = os.path.join(self.root, "CLAUDE.md")
        malformed = [
            {}, {"tool_name": "Write"},
            {"tool_name": "Write", "tool_input": {}},
            {"tool_name": "Write", "tool_input": {"file_path": ""}},
            {"tool_name": "Write", "tool_input": {"file_path": None}},
            {"tool_name": None, "tool_input": {"file_path": target}},
            {"tool_name": "Write", "tool_input": {"file_path": target}, "cwd": 99},
            {"tool_name": "Write", "tool_input": {"file_path": target},
             "cwd": "/nonexistent/nowhere"},
        ]
        for p in malformed:
            self.assertAllowed(self.decide(p), "for malformed payload %r" % (p,))


# ---------------------------------------------------------------------------
# The declared tool surface, reused from sbe_fence_hook.py rather than
# re-typed: a drift test, not a re-derivation.
# ---------------------------------------------------------------------------

class TestToolSurfaceReuse(AuthorityCase):

    def test_write_tools_is_the_fence_hooks_own_and_not_a_second_copy(self):
        # sbe_authority_hook.py loads sbe_fence_hook.py BY PATH under its own
        # private module name (see load_fence_module's docstring), so this is
        # a second exec of the same file, not a second definition: the two
        # WRITE_TOOLS objects are distinct frozenset instances that must
        # still be equal, proving the value travelled with the file and was
        # never re-typed here.
        loaded = ah.load_fence_module()
        self.assertEqual(loaded.WRITE_TOOLS, fh.WRITE_TOOLS)
        with io.open(HOOK_PATH, "r", encoding="utf-8") as f:
            self.assertNotIn(
                'WRITE_TOOLS = frozenset', f.read(),
                "sbe_authority_hook.py must not define its own WRITE_TOOLS; it reuses "
                "sbe_fence_hook.py's by import")

    def test_bash_is_not_gated_here_either(self):
        p = {"tool_name": "Bash",
             "tool_input": {"command": "echo x > %s" % os.path.join(self.root, "CLAUDE.md")},
             "session_id": "s", "cwd": self.root, "project_dir": self.root}
        d = self.decide(p)
        self.assertAllowed(d, "Bash is outside this hook's tool surface, same as the fence hook")
        self.assertEqual(d.notes, [], "a non-write tool must be silent")

    def test_read_only_tools_are_silent(self):
        for tool in ("Read", "Grep", "Glob", "TodoWrite"):
            d = self.decide({"tool_name": tool,
                             "tool_input": {"file_path": os.path.join(self.root, "CLAUDE.md")},
                             "session_id": "s", "cwd": self.root})
            self.assertAllowed(d, "%s is not a write tool" % tool)
            self.assertEqual(d.notes, [], "%s must be silent" % tool)

    def test_every_declared_write_tool_is_gated(self):
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        loaded = ah.load_fence_module()
        for tool in sorted(loaded.WRITE_TOOLS):
            key = "notebook_path" if tool == "NotebookEdit" else "file_path"
            p = {"tool_name": tool,
                 "tool_input": {key: os.path.join(self.root, "CLAUDE.md")},
                 "session_id": "s", "cwd": self.root, "project_dir": self.root}
            self.assertDenied(self.decide(p), "for write tool %s" % tool)


# ---------------------------------------------------------------------------
# apply_patch through the shared parser. Until 2026-08-11 extract_targets
# returned nothing for apply_patch and that nothing fell into the fail-open,
# so authority files were invisible to this guard on exactly the path the
# fence hook had already learned to parse. These tests force the condition
# both ways: the deny fires, and the declared-scope allow still works.
# ---------------------------------------------------------------------------

class TestApplyPatchThroughTheSharedParser(AuthorityCase):

    def patch_payload(self, *paths, **kw):
        kind = kw.pop("kind", "Update File")
        session = kw.pop("session", "worker-session-1")
        assert not kw, kw
        lines = ["*** Begin Patch"]
        for p in paths:
            lines.append("*** %s: %s" % (kind, p))
            lines.append("@@" if kind == "Update File" else "+x")
        lines.append("*** End Patch")
        return {"tool_name": "apply_patch",
                "tool_input": {"command": "\n".join(lines)},
                "session_id": session, "cwd": self.root,
                "project_dir": self.root}

    def test_a_patch_naming_an_undeclared_authority_file_is_refused(self):
        """The half of the hole this class exists for: worker context is
        present (an open task owning something else), the patch updates
        CLAUDE.md, and no task declares it. Before the shared dispatch this
        exact payload failed OPEN."""
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.patch_payload("CLAUDE.md"))
        reason = self.assertDenied(d, "for an apply_patch naming an authority file")
        self.assertIn("CLAUDE.md", reason)

    def test_a_patch_covered_by_its_declaring_task_is_allowed(self):
        self.write_registry([self.open_task(owns=["CLAUDE.md"])])
        d = self.decide(self.patch_payload("CLAUDE.md"))
        self.assertAllowed(d, "for a task that declared exactly this authority file")

    def test_a_patch_on_a_non_authority_file_is_allowed(self):
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.patch_payload("README.md"))
        self.assertAllowed(d, "for a patch touching nothing authority-bearing")

    def test_an_untrusted_patch_is_denied_not_assumed_safe(self):
        """A patch the shared parser cannot fully read is the one shape where
        'could not tell' must not default to 'allowed', mirroring the fence
        hook's own single fail-closed exception."""
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide({"tool_name": "apply_patch",
                         "tool_input": {"command": "+CLAUDE.md but no markers"},
                         "session_id": "s", "cwd": self.root,
                         "project_dir": self.root})
        reason = self.assertDenied(d, "for a patch with no Begin/End markers")
        self.assertIn("denied rather than assumed safe", reason)

    def test_a_patch_on_the_task_registry_itself_is_refused(self):
        """The refuter's two-step: rewrite .sbe/tasks.json to grant yourself
        CLAUDE.md, then edit it legitimately. The registry is control-plane,
        protected here by the same list the Bash guard protects."""
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.patch_payload(".sbe/tasks.json"))
        reason = self.assertDenied(d, "for a patch rewriting the task registry")
        self.assertIn("tasks.json", reason)

    def test_a_write_to_a_grant_file_is_refused_on_this_channel_too(self):
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        d = self.decide(self.payload(
            os.path.join(self.root, ".sbe", "break-glass.json")))
        self.assertDenied(d, "for a Write to the break-glass grant file")

    def test_a_missing_fence_module_denies_apply_patch_not_fails_open(self):
        """The refuter proved this with a syntax-broken fence hook on disk:
        no module used to mean fail-open even for the one tool whose targets
        only that module can parse."""
        saved_mod, saved_err = ah._FENCE_MOD, ah._FENCE_MOD_ERROR
        ah._FENCE_MOD, ah._FENCE_MOD_ERROR = None, "SyntaxError: forced by test"
        try:
            d = self.decide(self.patch_payload("CLAUDE.md"))
            reason = self.assertDenied(
                d, "for apply_patch with no fence module importable")
            self.assertIn("denied rather than assumed safe", reason)
            d2 = self.decide(self.payload(os.path.join(self.root, "CLAUDE.md")))
            self.assertAllowed(
                d2, "a Write still fails open when the module is missing")
        finally:
            ah._FENCE_MOD, ah._FENCE_MOD_ERROR = saved_mod, saved_err

    def test_a_foreign_exception_from_the_dispatch_denies_apply_patch(self):
        """Exception identity is not a security boundary: a fence module
        whose targets_for raises TypeError (or a PatchDeny from a different
        module object) must still deny for apply_patch."""
        class Shim(object):
            WRITE_TOOLS = fh.WRITE_TOOLS
            PatchDeny = fh.PatchDeny

            @staticmethod
            def targets_for(tool_name, tool_input):
                raise TypeError("forced by test")
        saved_mod, saved_err = ah._FENCE_MOD, ah._FENCE_MOD_ERROR
        ah._FENCE_MOD, ah._FENCE_MOD_ERROR = Shim(), None
        try:
            d = self.decide(self.patch_payload("CLAUDE.md"))
            reason = self.assertDenied(d, "for a TypeError out of targets_for")
            self.assertIn("TypeError", reason)
        finally:
            ah._FENCE_MOD, ah._FENCE_MOD_ERROR = saved_mod, saved_err

    def test_a_payload_without_cwd_still_resolves_against_the_project(self):
        """No cwd in the payload used to resolve relative patch paths against
        the hook process's own cwd, which is an accident of the harness."""
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        p = self.patch_payload("CLAUDE.md")
        del p["cwd"]
        d = self.decide(p)
        self.assertDenied(d, "for a relative patch path with no cwd supplied")

    def test_a_case_variant_decoy_header_is_refused_not_read_past(self):
        """`*** update file:` used to read as body text while a decoy section
        satisfied the parser; now any unclassifiable `***`-marker line is a
        refusal, and a case variant parses as the header it is."""
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        cmd = "\n".join([
            "*** Begin Patch",
            "*** Update File: README.md",
            "@@",
            "*** update file: CLAUDE.md",
            "@@",
            "*** End Patch"])
        d = self.decide({"tool_name": "apply_patch",
                         "tool_input": {"command": cmd},
                         "session_id": "s", "cwd": self.root,
                         "project_dir": self.root})
        reason = self.assertDenied(d, "for a lower-case Update File header")
        self.assertIn("CLAUDE.md", reason)

    def test_a_double_slash_absolute_path_is_refused(self):
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        cmd = "\n".join([
            "*** Begin Patch",
            "*** Update File: //CLAUDE.md",
            "@@",
            "*** End Patch"])
        d = self.decide({"tool_name": "apply_patch",
                         "tool_input": {"command": cmd},
                         "session_id": "s", "cwd": self.root,
                         "project_dir": self.root})
        reason = self.assertDenied(d, "for an absolute-spelled patch path")
        self.assertIn("absolute", reason)

    def test_a_move_to_destination_counts_as_a_target(self):
        """A rename that lands ON an authority file must be seen: the
        destination path is a target exactly as the source is."""
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        cmd = "\n".join([
            "*** Begin Patch",
            "*** Update File: README.md",
            "*** Move to: CLAUDE.md",
            "@@",
            "*** End Patch"])
        d = self.decide({"tool_name": "apply_patch",
                         "tool_input": {"command": cmd},
                         "session_id": "s", "cwd": self.root,
                         "project_dir": self.root})
        reason = self.assertDenied(d, "for a rename landing on an authority file")
        self.assertIn("CLAUDE.md", reason)


# ---------------------------------------------------------------------------
# The wire protocol, exercised as Claude Code actually invokes it.
# ---------------------------------------------------------------------------

class TestWireProtocol(AuthorityCase):

    def run_hook(self, payload, env_extra=None):
        env = dict(os.environ)
        env.pop("BROTHERSBE_AUTHORITY_HOOK_OFF", None)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps(payload), capture_output=True, text=True,
            timeout=60, env=env, cwd=self.root)

    def test_a_deny_lands_on_stdout_as_json_at_exit_zero(self):
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        r = self.run_hook(self.payload(os.path.join(self.root, "CLAUDE.md")))
        self.assertEqual(r.returncode, 0,
                         "the hook must always exit 0; exit 2 means the hook itself failed")
        obj = json.loads(r.stdout)
        out = obj["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("CLAUDE.md", out["permissionDecisionReason"])

    def test_an_apply_patch_deny_lands_on_stdout_through_the_real_entry_point(self):
        """The Loop 2 close condition, verbatim: an apply_patch payload naming
        an authority file is refused by the authority hook, driven through the
        real entry point, with the deny quoted."""
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        cmd = "\n".join(["*** Begin Patch", "*** Update File: CLAUDE.md",
                         "@@", "*** End Patch"])
        r = self.run_hook({"tool_name": "apply_patch",
                           "tool_input": {"command": cmd},
                           "session_id": "worker-session-1", "cwd": self.root,
                           "project_dir": self.root})
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("CLAUDE.md", out["permissionDecisionReason"])

    def test_stdout_carries_the_decision_and_nothing_else(self):
        r = self.run_hook(self.payload(os.path.join(self.root, "README.md")))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "",
                         "an allow writes NOTHING to stdout; got: %r" % r.stdout)

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

    def test_the_surfaces_subcommand_reports_without_touching_stdout(self):
        self.write_registry([self.open_task(owns=["src/unrelated.py"])])
        r = subprocess.run([sys.executable, HOOK_PATH, "surfaces", self.root],
                           capture_output=True, text=True, timeout=60, cwd=self.root)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "", "diagnostics must never reach the decision channel")
        self.assertIn("worker context detected: True", r.stderr)

    def test_an_unknown_subcommand_is_refused_rather_than_read_as_a_hook_call(self):
        r = subprocess.run([sys.executable, HOOK_PATH, "claim"], input="",
                           capture_output=True, text=True, timeout=60, cwd=self.root)
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown command", r.stderr)


class TestHelpMeansHelp(unittest.TestCase):

    def _run(self, *argv):
        out = subprocess.run([sys.executable, HOOK_PATH] + list(argv),
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return out.returncode, out.stdout, out.stderr

    def test_help_exits_0_with_usage_on_stderr_and_a_clean_stdout(self):
        for argv in (("-h",), ("--help",)):
            code, stdout, stderr = self._run(*argv)
            self.assertEqual(code, 0, "%s exited %d: %s" % (argv, code, stdout + stderr))
            self.assertIn("usage: sbe_authority_hook.py", stderr)
            self.assertEqual(stdout, "", "%s wrote to stdout, the decision channel" % (argv,))

    def test_a_bad_flag_on_surfaces_exits_2_instead_of_reading_it_as_a_directory(self):
        code, stdout, stderr = self._run("surfaces", "--bogus")
        self.assertEqual(code, 2, "surfaces --bogus exited %d: %s" % (code, stdout + stderr))
        self.assertIn("unrecognized flag", stderr)
        self.assertEqual(stdout, "", "the refusal leaked onto the decision channel")

    def test_the_bare_hook_invocation_still_fails_open(self):
        code, stdout, stderr = self._run()
        self.assertEqual(code, 0,
                         "the bare hook invocation must never block a session; it exited "
                         "%d: %s" % (code, stderr))
        self.assertIn("FAILING OPEN", stderr)


# ---------------------------------------------------------------------------
# Housekeeping the project asserts about itself elsewhere, asserted here for
# the two files this port adds.
# ---------------------------------------------------------------------------

class TestShippedFileHygiene(unittest.TestCase):

    FILES = (HOOK_PATH, os.path.join(HERE, "test_sbe_authority_hook.py"))

    def test_no_em_or_en_dashes(self):
        # The two characters are built with chr(), never typed, for the same
        # reason tools/test_sbe_fence_hook.py builds them this way: spelling
        # them as literals would put them in this file, which this rule also
        # covers, and the test would fail on its own assertion text.
        banned = ((chr(0x2014), "em dash"), (chr(0x2013), "en dash"))
        for path in self.FILES:
            if not os.path.isfile(path):
                self.fail("%s is missing" % path)
            with io.open(path, "r", encoding="utf-8") as f:
                body = f.read()
            for ch, name in banned:
                self.assertNotIn(ch, body, "%s carries an %s" % (path, name))

    def test_no_co_author_trailer_or_agent_name(self):
        # "Claude Code" is the product this hook plugs into and is named
        # throughout tools/sbe_fence_hook.py's own docstring for the same
        # reason; what the house rule bans is an AGENT attribution (a model
        # or session naming itself as the author), never a product mention.
        # Checked against HOOK_PATH only: this test file necessarily quotes
        # the banned strings as assertion arguments, and checking itself
        # would be the same false-alarm-on-its-own-assertion-text shape
        # test_no_em_or_en_dashes avoids by building its characters with
        # chr() instead of typing them.
        with io.open(HOOK_PATH, "r", encoding="utf-8") as f:
            body = f.read()
        self.assertNotIn("Co-Authored-By", body, "%s carries a co-author trailer" % HOOK_PATH)
        self.assertNotIn("Written by", body, "%s carries an agent attribution" % HOOK_PATH)
        self.assertNotIn("Generated by", body, "%s carries an agent attribution" % HOOK_PATH)

    def test_the_hook_imports_nothing_outside_the_standard_library(self):
        import ast
        with io.open(HOOK_PATH, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=HOOK_PATH)
        allowed = set(sys.builtin_module_names) | {
            "json", "os", "sys", "importlib", "importlib.util"}
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
                "the hook must make no network call and run no subprocess; it runs in "
                "front of every edit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
