#!/usr/bin/env python3
"""Regression tests for BR-20, "authority at sink": tools/bm_fence_hook.py.

WHAT "AUTHORITY AT SINK" MEANS, CONFIRMED AGAINST THIS CODE (not assumed)
  The sink is the function decide() in tools/bm_fence_hook.py (line 976),
  called by cmd_hook() (line 1185), which is the PreToolUse hook wired in
  hooks/hooks.json for the matcher "Edit|Write|MultiEdit|NotebookEdit|Bash".
  That is the point where a write actually happens: Claude Code calls this
  hook with the tool name and its input on stdin BEFORE the write runs, and
  a "deny" decision stops the call before any byte is written. bm_store.py's
  own CLAIM refusal (an overlapping claim() call is rejected) is a CALLER
  check: it only stops a session that goes through claim() honestly. Nothing
  in bm_store.py sits between an agent and an actual Edit/Write/MultiEdit/
  NotebookEdit call, and Bash is explicitly out of scope except for the one
  apply_patch shape decide() also gates (bm_fence_hook.py lines 120-137,
  994-1035). decide() is therefore the one place this repository enforces
  "one writer per file" as a refusal rather than a ledger entry, which is
  exactly the audit finding (finding 8) bm_fence_hook.py's own module
  docstring names as the reason it exists.

  Confirmed by reading, not inferred: tools/bm_store.py has no PreToolUse
  hook registration and no deny path reachable from a Read/Edit/Write call;
  hooks/hooks.json wires ONLY bm_fence_hook.py (plus bm_bash_audit.py, which
  is PostToutUse / advisory audit, not a blocking gate) to PreToolUse.

  Same discipline as tools/test_bm_fence_hook.py, the existing suite for
  this same file: every test is self-contained under
  tempfile.TemporaryDirectory(), scrubs the BrotherMode environment
  variables out of its own environment, and never touches this repo's own
  files, the real store, or the real home directory. This suite does not
  re-run that file's ~120 cases; it is the twelve BM-A5 asks for, chosen
  from what the sink actually does (confirmed by reading decide() and
  docs/HOOKS.md, not invented): a foreign session across an active claim,
  each of the four write tools the hook matches, containment inside an
  owned directory, a symlink resolving into owned territory, the one
  alternate command path (apply_patch under Bash) the hook also gates, the
  owner's own edit going through as the positive calibration, a missing
  session id read as NO-DATA (advisory) or a refusal (enforced), the
  documented same-session-id identity limit, retries never wearing the
  fence down, and a changed command or path never inheriting an earlier
  approval.

WHAT THIS SUITE DOES NOT CLAIM ABOUT SESSION IDENTITY
  decide() derives "my label" from payload["session_id"] alone
  (session_label() at bm_fence_hook.py line 332, session_label(root,
  session_id) = label_for_token(ensure_token(root, session_id))): a pure
  function of that one string. Two different session_id values always
  produce two different labels (SHA-256 of an HMAC-style domain-separated
  input, collision only by preimage break), so the sink DOES guarantee that
  two DISTINCT session ids can never share a label by accident. It does
  NOT, and cannot, guarantee that two different logical actors always carry
  two different session_id values: that string comes from whatever the
  calling harness put in the PreToolUse payload, and decide() takes it on
  faith (bm_fence_hook.py line 1038-1042 only checks that session_id is a
  non-empty string, never that it identifies a distinct human or task).
  docs/HOOKS.md's own "residual limitation, stated plainly" section says
  the same thing from a different angle: token files are 0600 inside a
  0700 directory, readable by any process running as this user, so
  "there is no secret this process can hold that another process under the
  same user cannot read." Two actors sharing one session_id, or one actor
  reading another's token off disk, both collapse to the same label and
  are mutually invisible to this fence. test_two_actors_sharing_one_
  session_id_are_indistinguishable below demonstrates the first case
  directly against the real code, as the honest limit rather than a
  hidden one. This suite does NOT assert that the sink can tell one
  session from another in general: it only asserts what decide() actually
  does with the session_id string the payload hands it.

Python 3.9, standard library only. Run: python3 tools/test_bm_authority_sink.py
No em or en dashes anywhere in this file, its comments, or its output.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_PATH = os.path.join(HERE, "bm_fence_hook.py")
STORE_PATH = os.path.join(HERE, "bm_store.py")

import importlib.util

_spec = importlib.util.spec_from_file_location("bm_store", STORE_PATH)
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)
sys.modules["bm_store"] = bs

_hspec = importlib.util.spec_from_file_location("bm_fence_hook", HOOK_PATH)
fh = importlib.util.module_from_spec(_hspec)
_hspec.loader.exec_module(fh)
sys.modules["bm_fence_hook"] = fh


def _clean_env(extra=None):
    """A child environment with every ambient BrotherMode variable removed,
    matching tools/test_bm_fence_hook.py's helper of the same name, so a
    shell export left over from another task can never decide a case here."""
    e = dict(os.environ)
    for k in ("BROTHERMODE_ROOT", "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID",
              "BM_FENCE_MODE", "BM_FENCE_BATTERY"):
        e.pop(k, None)
    if extra:
        e.update(extra)
    return e


def payload(session_id, cwd, tool_name="Edit", tool_input=None, **kw):
    """A PreToolUse payload shaped exactly as docs/HOOKS.md's own example
    shows it: session_id, cwd, hook_event_name, tool_name, tool_input.
    Mirrors tools/test_bm_fence_hook.py's helper of the same name so both
    suites build the wire shape identically and cannot silently drift."""
    p = {
        "session_id": session_id,
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input if tool_input is not None else {
            "file_path": "src/app.py", "old_string": "a", "new_string": "b"},
    }
    p.update(kw)
    return p


def apply_patch_command(*body_lines):
    """A well formed apply_patch heredoc around the given envelope body,
    identical shape to tools/test_bm_fence_hook.py's helper of the same
    name: this is how the Codex CLI actually emits a file write, captured
    2026-08-05 and recorded in docs/RUNTIMES.md."""
    return ("apply_patch <<'PATCH'\n*** Begin Patch\n"
            + "\n".join(body_lines)
            + "\n*** End Patch\nPATCH")


class AuthoritySinkBase(unittest.TestCase):
    """A throwaway project: a real .git directory (the sink resolves its
    root the same way bm_store does, which looks for .git as one anchor),
    a src/ tree with two files, and a BrotherMode store. Two session ids,
    VICTIM (the owner) and OTHER (the intruder)."""

    VICTIM = "sess-victim-0001"
    OTHER = "sess-other-0002"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # realpath up front: on macOS /var and /tmp are symlinks, and every
        # comparison inside decide() is against a realpath'd root.
        self.root = os.path.realpath(self._tmp.name)
        os.makedirs(os.path.join(self.root, ".git"))
        os.makedirs(os.path.join(self.root, "src"))
        for name in ("app.py", "other.py"):
            with io.open(os.path.join(self.root, "src", name), "w",
                         encoding="utf-8") as f:
                f.write("# %s\n" % name)
        with bs.Store(self.root):
            pass
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        for k in ("BROTHERMODE_ROOT", "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID",
                  "BM_FENCE_MODE", "BM_FENCE_BATTERY"):
            os.environ.pop(k, None)

    def tearDown(self):
        self._env_patch.stop()
        self._tmp.cleanup()

    def label(self, session_id):
        return fh.session_label(self.root, session_id)

    def claim(self, name, files, session_id_label):
        with bs.Store(self.root, create=False) as store:
            return store.claim(name, "ephemeral", objective="BR-20 sink test",
                               files=files, session_id=session_id_label)

    def bash(self, session_id, command):
        return payload(session_id, self.root, tool_name="Bash",
                       tool_input={"command": command})

    def deny_reason(self, decision):
        self.assertIsNotNone(decision, "expected a deny; got allow")
        return decision["hookSpecificOutput"]["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# The eight BLOCKED cases.
# ---------------------------------------------------------------------------

class TheEightBlockedCases(AuthoritySinkBase):

    # 1. A foreign session writing a path another session owns.
    def test_1_foreign_session_writing_owned_path_is_blocked(self):
        """Proves the core promise: ownership recorded in the store is
        enforced at the write itself, not merely at claim() time. OTHER
        never called claim(); it only tries to Edit a path VICTIM's active
        record covers, which is exactly what an agent that "forgot to ask"
        looks like."""
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        p = payload(self.OTHER, self.root, tool_input={"file_path": "src/app.py"})
        decision, notes = fh.decide(p)
        reason = self.deny_reason(decision)
        self.assertIn("BrotherMode fence", reason)
        self.assertIn("src/app.py", reason)
        self.assertEqual(notes, [], notes)

    # 2. The Edit tool.
    def test_2_edit_tool_is_blocked(self):
        """One of the four write tools the PreToolUse matcher names
        (hooks/hooks.json: "Edit|Write|MultiEdit|NotebookEdit|Bash")."""
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        p = payload(self.OTHER, self.root, tool_name="Edit",
                    tool_input={"file_path": "src/app.py",
                                "old_string": "a", "new_string": "b"})
        decision, _n = fh.decide(p)
        self.deny_reason(decision)

    # 3. The Write tool, including a file that does not exist yet.
    def test_3_write_tool_is_blocked_even_for_a_new_file(self):
        """Write CREATES files, so a fence that only checked existing paths
        would be bypassed by writing a brand new file inside owned
        territory. The claim covers the directory; the file is new."""
        owner = self.label(self.VICTIM)
        self.claim("api", ["src"], owner)
        p = payload(self.OTHER, self.root, tool_name="Write",
                    tool_input={"file_path": "src/brand_new.py", "content": "x"})
        decision, _n = fh.decide(p)
        self.deny_reason(decision)

    # 4. The MultiEdit tool, including a path nested in a per-edit entry.
    def test_4_multiedit_tool_is_blocked_including_nested_path(self):
        """A MultiEdit payload's top-level file_path can be innocuous while
        one of its edits[] entries carries its own file_path pointing at the
        owned file; extract_targets() walks nested dicts precisely so this
        is not a way to smuggle a write past the fence."""
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        p = payload(self.OTHER, self.root, tool_name="MultiEdit",
                    tool_input={"file_path": "README.md",
                                "edits": [{"file_path": "src/app.py",
                                           "old_string": "a", "new_string": "b"}]})
        decision, _n = fh.decide(p)
        self.deny_reason(decision)

    # 5. The NotebookEdit tool.
    def test_5_notebookedit_tool_is_blocked(self):
        """NotebookEdit carries its path under notebook_path, a different
        key from every other write tool (PATH_KEYS in bm_fence_hook.py
        includes both); the fence must recognize it too."""
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/nb.ipynb"], owner)
        p = payload(self.OTHER, self.root, tool_name="NotebookEdit",
                    tool_input={"notebook_path": "src/nb.ipynb",
                                "new_source": "x"})
        decision, _n = fh.decide(p)
        self.deny_reason(decision)

    # 6. A path inside an owned directory, not the exact claimed file.
    def test_6_path_inside_owned_directory_is_blocked(self):
        """The claim names the directory "src", not "src/other.py". A fence
        that only matched the literal claimed string would let a second
        session edit every file in the directory except the one named on
        the record; bm_store.paths_overlap's directory-containment rule is
        what closes that."""
        owner = self.label(self.VICTIM)
        self.claim("api", ["src"], owner)
        p = payload(self.OTHER, self.root, tool_input={"file_path": "src/other.py"})
        decision, _n = fh.decide(p)
        self.deny_reason(decision)

    # 7. A symlink resolving into owned territory.
    @unittest.skipIf(not hasattr(os, "symlink") or sys.platform == "win32",
                     "symlink creation needs privileges on Windows")
    def test_7_symlink_into_owned_territory_is_blocked(self):
        """canonical_target() realpaths every target before comparing it, so
        a symlink sitting outside src/ that points at the owned file is not
        a bypass: the LINK's own path is not claimed, but what it resolves
        to is."""
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        link = os.path.join(self.root, "link_to_app.py")
        os.symlink(os.path.join(self.root, "src", "app.py"), link)
        p = payload(self.OTHER, self.root, tool_input={"file_path": "link_to_app.py"})
        decision, _n = fh.decide(p)
        self.deny_reason(decision)

    # 8. The one alternate command path the hook also gates: apply_patch
    #    under Bash, the write shape the Codex CLI uses instead of Edit.
    def test_8_apply_patch_via_bash_is_blocked(self):
        """Bash is otherwise out of scope (an arbitrary shell command can
        write a file in ways no parser can enumerate, stated as a known gap
        in bm_fence_hook.py and docs/HOOKS.md). The one shape gated anyway
        is the apply_patch heredoc envelope, because that is how EVERY file
        write arrives under the Codex CLI (measured 2026-08-05). A second
        writer using that alternate path must be caught exactly like Edit."""
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        cmd = apply_patch_command("*** Update File: src/app.py", "@@", "-a", "+b")
        decision, _n = fh.decide(self.bash(self.OTHER, cmd))
        reason = self.deny_reason(decision)
        self.assertIn("src/app.py", reason)


# ---------------------------------------------------------------------------
# The calibration cases.
# ---------------------------------------------------------------------------

class CalibrationCase(AuthoritySinkBase):

    # 2. The owner editing its own claimed file: the positive calibration.
    def test_owner_edit_of_own_claimed_file_is_allowed(self):
        """THE OTHER CALIBRATION. Every one of the eight blocked cases above
        passes if decide() ever denies for ANY reason, including a bug that
        denies every write regardless of ownership. This test is the direct
        check on that failure mode: VICTIM, the session that actually holds
        the claim, edits the exact file its own record covers, and decide()
        must return None (allow), with no notes (a genuine verified allow,
        not a fail-open). If a future change made decide() deny
        unconditionally, TheEightBlockedCases would still show all green;
        only a test that asserts the owner gets through can catch that."""
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        p = payload(self.VICTIM, self.root,
                    tool_input={"file_path": "src/app.py",
                                "old_string": "a", "new_string": "b"})
        decision, notes = fh.decide(p)
        self.assertIsNone(decision, "the owner's own edit of its own claimed "
                          "file was denied")
        self.assertEqual(notes, [], notes)

    def test_calibrated_the_overlap_check_is_load_bearing(self):
        """THE POINT OF THIS SUITE. Every case above passes if decide() ever
        denies for ANY reason, including a bug that denies everything; a
        suite that never tries to make the real code allow proves nothing
        about enforcement. This test neutralizes the exact function decide()
        calls to decide ownership, bm_store.paths_overlap, the same way
        tools/test_bm_store.py neutralizes _ensure_git_excludes with
        mock.patch.object, and the same construction
        tools/test_bm_fence_hook.py already uses at its own
        test_calibrated_a_per_record_store_open_is_caught: patch the module
        object fh._load_store_module() ACTUALLY RETURNS (not this test
        file's own `bs` import, which is a separate exec_module() and would
        silently patch a module decide() never looks at), so the patch is
        guaranteed to reach the real call inside decide().

        Two assertions, in order:
          1. Against the REAL, unpatched paths_overlap: the write is denied.
             If a future change ever deletes or weakens the foreign-claim
             check in decide(), THIS assertion fails, as an assertion, not
             an error, exactly where the write should have been refused.
          2. With paths_overlap forced to report "nothing ever overlaps":
             the identical write is now allowed. This is what proves the
             deny in (1) was actually produced BY that check and not by
             some unrelated denial: if decide() did not truly route its
             ownership decision through bm_store.paths_overlap (for example
             if a rewrite inlined an equivalent comparison instead of
             calling the shared function), forcing this function to lie
             would change nothing, (2) would still see a deny, and THIS
             assertion would fail too. Either direction failing is the
             suite doing its job: a control asserted but never proven live
             reports PASS while enforcing nothing, and this is the proof
             that it is live.
        """
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        p = payload(self.OTHER, self.root, tool_input={"file_path": "src/app.py"})

        # (1) Real check: denied.
        decision, notes = fh.decide(p)
        self.assertIsNotNone(
            decision,
            "BASELINE FAILURE: the real, unpatched fence allowed a foreign "
            "write across an active claim (notes: %r). The enforcement this "
            "whole suite exists to prove has already stopped working; "
            "nothing below can prove anything if this line does not deny."
            % notes)

        # (2) Neutralized check: the SAME write is now allowed.
        hookbs = fh._load_store_module()
        self.assertIsNotNone(hookbs, "the hook could not load bm_store.py at all")
        real_overlap = hookbs.paths_overlap
        hookbs.paths_overlap = lambda a, b: False
        try:
            neutralized_decision, _n2 = fh.decide(p)
        finally:
            hookbs.paths_overlap = real_overlap

        self.assertIsNone(
            neutralized_decision,
            "CALIBRATION FAILURE: forcing bm_store.paths_overlap to report "
            "no overlap did not change decide()'s answer. That means "
            "decide() is not actually routing its ownership decision "
            "through paths_overlap, so patching it here proves nothing: "
            "the deny in step (1) could be coming from anywhere, and this "
            "test would report the fence as working even if the real "
            "overlap check were deleted entirely.")

        # Restore is not a bypass: prove the ORIGINAL function object is back
        # in place and the fence denies again, so no later test in this
        # process can inherit a neutralized check.
        self.assertIs(hookbs.paths_overlap, real_overlap)
        decision_after_restore, _n3 = fh.decide(p)
        self.assertIsNotNone(
            decision_after_restore,
            "the restore left the fence neutralized for every test that "
            "runs after this one in the same process")


# ---------------------------------------------------------------------------
# The documented identity limit, demonstrated rather than merely asserted.
# ---------------------------------------------------------------------------

class SessionIdentityLimit(AuthoritySinkBase):
    """Not one of the eight BLOCKED cases: this class demonstrates what the
    module docstring above calls out as a real limit, so the claim is
    checked against the running code rather than left as prose."""

    def test_two_actors_sharing_one_session_id_are_indistinguishable(self):
        """decide() trusts payload["session_id"] as the caller's whole
        identity (bm_fence_hook.py line 1038-1042: checked for "is a
        non-empty string", never for "identifies a distinct actor"). If two
        different logical callers present the SAME session_id string, one
        claims under it, and the other later writes under it, the write is
        allowed: the sink cannot see two actors where the harness reported
        one. This is the same fact docs/HOOKS.md's own "residual
        limitation, stated plainly" section states from the token-file
        angle (0600 inside a 0700 directory, readable by any process under
        this user): identity here is "whoever holds this session_id",
        never "whoever a human meant.\""""
        shared_session_id = "sess-shared-by-two-actors"
        owner_label = self.label(shared_session_id)
        self.claim("api", ["src/app.py"], owner_label)
        p = payload(shared_session_id, self.root,
                    tool_input={"file_path": "src/app.py"})
        decision, notes = fh.decide(p)
        self.assertIsNone(
            decision,
            "a second write under the SAME session_id was blocked (notes: "
            "%r); if this ever fails, the sink has started distinguishing "
            "actors within one session_id by some means this test did not "
            "expect, and the module docstring above needs updating to "
            "match, not the other way around." % notes)


# ---------------------------------------------------------------------------
# 9. A missing session id: NO-DATA in advisory mode, a refusal in enforced
#    (strict) mode. Never a silent, unqualified pass.
# ---------------------------------------------------------------------------

class MissingSessionId(AuthoritySinkBase):
    """BM-A5: session identity is stamped from the hook payload's own
    session_id field (decide(), bm_fence_hook.py line ~1038), never from an
    environment variable somebody has to remember to export. A payload that
    carries no session_id at all is therefore the "no verifiable identity"
    case decide() names "no-session-id" in _FAIL_REASONS, and it is handled
    exactly like every other _FailOpen: allowed with a loud stderr note in
    the DEFAULT advisory mode (fail open, C-01's deliberate default), denied
    in enforced mode (BM_FENCE_MODE=enforced, this estate's "strict mode").

    NO-DATA is never a silent pass: even where the write proceeds (advisory
    mode), decide() returns it bundled with a non-empty note stating plainly
    that the fence was NOT checked, so the caller can never mistake this for
    a verified ownership decision (compare TheEightBlockedCases and
    CalibrationCase, which assert notes == [] for a real, checked
    decision)."""

    def _no_session_payload(self):
        return payload("", self.root,
                       tool_input={"file_path": "src/app.py",
                                   "old_string": "a", "new_string": "b"})

    def test_9_missing_session_id_advisory_mode_is_NO_DATA_not_a_silent_pass(self):
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        decision, notes = fh.decide(self._no_session_payload())
        self.assertIsNone(
            decision, "advisory mode is documented fail-open for an "
            "unverifiable identity; a deny here would mean decide() started "
            "failing closed by default, which C-01 records as deliberately "
            "not this file's behaviour")
        self.assertTrue(
            any("no-session-id" in n or "no verifiable identity" in n
                for n in notes),
            "a missing session id must leave a NO-DATA note behind even "
            "when the write proceeds; got notes=%r" % notes)

    def test_9_missing_session_id_enforced_strict_mode_is_refused(self):
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        os.environ["BM_FENCE_MODE"] = "enforced"
        try:
            decision, _notes = fh.decide(self._no_session_payload())
        finally:
            os.environ.pop("BM_FENCE_MODE", None)
        reason = self.deny_reason(decision)
        self.assertIn("enforced mode", reason)
        self.assertIn("no verifiable identity", reason)


# ---------------------------------------------------------------------------
# 11. Queue, resume or retry cannot manufacture authority.
# ---------------------------------------------------------------------------

class RetryCannotManufactureAuthority(AuthoritySinkBase):
    """decide() holds no state across calls: it is a pure function of the
    payload it is handed (payload["session_id"], payload["tool_name"],
    payload["tool_input"]) plus the store's own on-disk claims, read fresh
    every time via active_claims(). Nothing in this file remembers a prior
    deny, queues a pending write, or counts attempts, so repeating,
    re-queuing or retrying the identical foreign write cannot wear the fence
    down: the tenth call is checked exactly as strictly as the first."""

    def test_11_repeated_retries_of_the_same_foreign_write_stay_denied(self):
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        p = payload(self.OTHER, self.root,
                    tool_input={"file_path": "src/app.py",
                                "old_string": "a", "new_string": "b"})
        reasons = []
        for _attempt in range(5):
            decision, notes = fh.decide(p)
            reasons.append(self.deny_reason(decision))
            self.assertEqual(notes, [], notes)
        # Every retry produced the identical, fully-checked refusal: no
        # attempt count, backoff or queued state ever let a later call
        # through, and none of them degraded into a fail-open note either.
        self.assertEqual(len(set(reasons)), 1, reasons)


# ---------------------------------------------------------------------------
# 12. A changed command or path after approval invalidates the approval.
# ---------------------------------------------------------------------------

class ChangedRequestInvalidatesApproval(AuthoritySinkBase):
    """decide() evaluates only the targets present in the CURRENT payload
    (extract_targets() / extract_patch_targets(), called fresh on every
    invocation); it never carries an earlier ALLOW forward onto a later,
    different request. An approval for one path or one apply_patch envelope
    body therefore does not "cover" a subsequent call whose path or command
    text has changed, even when the two calls arrive back to back in the
    same session and even when the first one was genuinely allowed."""

    def test_12_edit_allowed_then_path_changed_to_owned_file_is_denied(self):
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)

        # First call: an unclaimed file. Genuinely allowed, not a fail-open.
        first = payload(self.OTHER, self.root,
                        tool_input={"file_path": "src/unclaimed.py",
                                    "old_string": "a", "new_string": "b"})
        decision1, notes1 = fh.decide(first)
        self.assertIsNone(decision1, notes1)
        self.assertEqual(notes1, [], notes1)

        # Second call: same session, same tool, but the path changed to the
        # file VICTIM owns. The first call's allow buys it nothing here.
        second = payload(self.OTHER, self.root,
                         tool_input={"file_path": "src/app.py",
                                     "old_string": "a", "new_string": "b"})
        decision2, _notes2 = fh.decide(second)
        self.deny_reason(decision2)

    def test_12_apply_patch_allowed_then_command_changed_to_owned_file_is_denied(self):
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)

        # First call: apply_patch touching an unclaimed file. Allowed.
        first_cmd = apply_patch_command(
            "*** Update File: src/unclaimed.py", "@@", "-a", "+b")
        decision1, notes1 = fh.decide(self.bash(self.OTHER, first_cmd))
        self.assertIsNone(decision1, notes1)
        self.assertEqual(notes1, [], notes1)

        # Second call: the command text changed to target the owned file.
        # The prior approval for the FIRST command does not extend to this
        # different one; it is checked on its own targets from scratch.
        second_cmd = apply_patch_command(
            "*** Update File: src/app.py", "@@", "-a", "+b")
        decision2, _notes2 = fh.decide(self.bash(self.OTHER, second_cmd))
        reason = self.deny_reason(decision2)
        self.assertIn("src/app.py", reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
