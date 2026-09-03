#!/usr/bin/env python3
"""Regression tests for tools/bm_fence_hook.py, the PreToolUse fence gate.
Standard library only. Run: python3 tools/test_bm_fence_hook.py

Same discipline as tools/test_bm_store.py: every test is self-contained under
tempfile.TemporaryDirectory(), scrubs BROTHERMODE_ROOT out of its own
environment and out of every subprocess it spawns, and never touches this
repo's own files, the real store, or the real home directory.

The calibrated_* tests each prove one property the fence would be worthless
without, stated as the thing that must NOT happen:
  1. a write to a path THIS session owns must not be blocked;
  2. a write to a path ANOTHER live record owns must not be allowed, and the
     refusal must name the owner and the exact takeover command;
  3. presenting another session's PUBLIC label as your own must not grant
     ownership, whether you pass it on the wire or write it into the store;
  4. a broken, missing or empty store must not turn into a refusal, because
     this hook sits in front of every edit the founder makes;
  5. a file write that arrives dressed as Bash (the Codex CLI's apply_patch
     heredoc, captured 2026-08-05) must go through the SAME fence check as
     an Edit, and a plain shell command must stay exactly as silent and
     cheap as it was before Bash was matched at all.
"""
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_PATH = os.path.join(HERE, "bm_fence_hook.py")
STORE_PATH = os.path.join(HERE, "bm_store.py")
DOCS_PATH = os.path.join(os.path.dirname(HERE), "docs", "HOOKS.md")

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
    so a shell export left over from another task can never decide the
    outcome of a test that is trying to prove something about root
    resolution, identity, or strict mode."""
    e = dict(os.environ)
    for k in ("BROTHERMODE_ROOT", "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID"):
        e.pop(k, None)
    if extra:
        e.update(extra)
    return e


def payload(session_id, cwd, tool_name="Edit", tool_input=None, **kw):
    """A PreToolUse payload shaped exactly as the hooks documentation shows
    it (read 2026-07-27): session_id, cwd, hook_event_name, tool_name,
    tool_input, tool_use_id. Tests that need a MALFORMED payload build it by
    deleting from this, so the well-formed shape lives in exactly one place."""
    p = {
        "session_id": session_id,
        "transcript_path": os.path.join(cwd, "transcript.jsonl"),
        "cwd": cwd,
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input if tool_input is not None else {
            "file_path": "src/app.py", "old_string": "a", "new_string": "b"},
        "tool_use_id": "toolu_01TEST",
    }
    p.update(kw)
    return p


class FenceHookBase(unittest.TestCase):
    """A throwaway project with a store, a src/ tree, and two sessions."""

    VICTIM = "sess-owner-0001"
    OTHER = "sess-other-0002"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # realpath up front: on macOS /var and /tmp are symlinks, and every
        # comparison in the hook is against a realpath'd root.
        self.root = os.path.realpath(self._tmp.name)
        os.makedirs(os.path.join(self.root, ".git"))
        os.makedirs(os.path.join(self.root, "src"))
        for name in ("app.py", "other.py"):
            with io.open(os.path.join(self.root, "src", name), "w",
                         encoding="utf-8") as f:
                f.write("# %s\n" % name)
        with bs.Store(self.root) as store:
            pass
        self._env_patch = mock.patch.dict(
            os.environ, {}, clear=False)
        self._env_patch.start()
        # BM_FENCE_MODE is scrubbed here too: ~/.claude/settings.json can set
        # it machine-wide (BM_FENCE_MODE=enforced, a separate decision this
        # file does not touch), and every test in this file that means
        # "default mode" must mean it regardless of the machine it runs on.
        # A test that wants enforced mode sets it itself, on top of this.
        for k in ("BROTHERMODE_ROOT", "BM_FENCE_STRICT", "BM_FENCE_SESSION_ID",
                  "BM_FENCE_MODE"):
            os.environ.pop(k, None)

    def tearDown(self):
        self._env_patch.stop()
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def label(self, session_id):
        return fh.session_label(self.root, session_id)

    def claim(self, name, files, session_id_label, lifetime="ephemeral"):
        with bs.Store(self.root, create=False) as store:
            rec = store.claim(name, lifetime, objective="test fence",
                              files=files, session_id=session_id_label)
        # The CLI refreshes the generated view after every mutation; the
        # Python API does not, and the identity tests below assert on what a
        # human actually reads out of STATE.md.
        bs.write_state_view(self.root)
        return rec

    def decide(self, p):
        """Run the decision in process. Returns (decision, stderr_notes)."""
        return fh.decide(p)

    def run_hook(self, p, env=None, cwd=None):
        """Run the hook exactly as Claude Code would: a bare command with a
        JSON payload on stdin. Returns the CompletedProcess."""
        return subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps(p), text=True, capture_output=True,
            cwd=cwd or self.root, env=_clean_env(env))

    def assertAllowed(self, decision, notes=None):
        self.assertIsNone(
            decision,
            "expected no decision (allow); got a deny: %r" % (decision,))

    def assertDenied(self, decision):
        self.assertIsNotNone(decision, "expected a deny; got allow")
        self.assertEqual(
            decision["hookSpecificOutput"]["permissionDecision"], "deny")
        return decision["hookSpecificOutput"]["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# Calibration 1 and 2: the fence allows its owner and refuses everyone else.
# ---------------------------------------------------------------------------

class CalibratedOwnership(FenceHookBase):

    def test_calibrated_1_owner_may_write_its_own_claim(self):
        mine = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], mine)
        decision, notes = self.decide(payload(self.VICTIM, self.root))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], "the owner's write must not print a "
                                    "fail-open warning: %r" % (notes,))

    def test_calibrated_2_foreign_record_is_denied_and_named(self):
        mine = self.label(self.VICTIM)
        rec = self.claim("api", ["src/app.py"], mine)
        decision, _notes = self.decide(payload(self.OTHER, self.root))
        reason = self.assertDenied(decision)
        # The refusal must be actionable: WHICH record, WHICH owner, and the
        # exact command that moves the fence. A deny that says only "no" sends
        # the reader back to guessing, which is how fences get bypassed.
        self.assertIn("api", reason)
        self.assertIn(rec.lifecycle_uuid, reason)
        self.assertIn(mine, reason)
        self.assertIn(self.label(self.OTHER), reason)
        self.assertIn("adopt %s --version %s" % (rec.lifecycle_uuid, rec.version),
                      reason)
        self.assertIn("--adopt-from-live-session", reason)

    def test_unclaimed_path_is_allowed_by_default(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        p = payload(self.OTHER, self.root,
                    tool_input={"file_path": "src/other.py"})
        decision, _n = self.decide(p)
        self.assertAllowed(decision)

    def test_parked_record_does_not_fence(self):
        """Only ACTIVE records hold a fence: this is the same rule the store
        applies when admitting a claim, and the two must not diverge."""
        mine = self.label(self.VICTIM)
        rec = self.claim("api", ["src/app.py"], mine)
        with bs.Store(self.root, create=False) as store:
            store.transition(rec.lifecycle_uuid, rec.version, "parked",
                             session_id=mine)
        decision, notes = self.decide(payload(self.OTHER, self.root))
        self.assertAllowed(decision)
        # Zero ACTIVE claims left, so this is the loud fail-open, not a
        # silent allow.
        self.assertTrue(any("no active claims" in n for n in notes), notes)


# ---------------------------------------------------------------------------
# Calibration 3: identity (audit finding 8B).
# ---------------------------------------------------------------------------

class CalibratedIdentity(FenceHookBase):

    def test_calibrated_3a_public_label_presented_as_session_id_gains_nothing(self):
        """The exact forgery the old design could not survive: the owning
        value was printed in plaintext into STATE.md, which every session
        reads, so the guard compared a public value against itself. Here the
        forger reads the label out of STATE.md and presents it as their own
        session id. The hook derives the label from the TOKEN this process
        could open, never from the string it was handed, so the forger gets a
        third, unrelated label and is refused."""
        mine = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], mine)
        state = io.open(os.path.join(self.root, "STATE.md"),
                        encoding="utf-8").read()
        self.assertIn(mine, state, "the public label must be visible to humans")
        decision, _n = self.decide(payload(mine, self.root))
        reason = self.assertDenied(decision)
        self.assertNotIn("This session is %s," % mine, reason)

    def test_calibrated_3b_claiming_under_a_stolen_label_gains_nothing(self):
        """The store-side version of the same forgery: the attacker does not
        pass the label on the wire, they write it into the store as the owner
        of their OWN claim. It still buys nothing, because ownership is
        checked against the attacker's own derived label at write time."""
        victim_label = self.label(self.VICTIM)
        self.claim("stolen", ["src/other.py"], victim_label)
        p = payload(self.OTHER, self.root,
                    tool_input={"file_path": "src/other.py"})
        decision, _n = self.decide(p)
        reason = self.assertDenied(decision)
        self.assertIn(victim_label, reason)

    def test_label_is_stable_for_a_session_and_distinct_across_sessions(self):
        a1 = self.label(self.VICTIM)
        a2 = self.label(self.VICTIM)
        b = self.label(self.OTHER)
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, b)
        self.assertTrue(a1.startswith("bm1-"), a1)

    def test_token_is_never_printed_by_any_command(self):
        token = fh.ensure_token(self.root, self.VICTIM)
        label = fh.label_for_token(token)
        self.claim("api", ["src/app.py"], label)
        state = io.open(os.path.join(self.root, "STATE.md"),
                        encoding="utf-8").read()
        self.assertNotIn(token, state)
        r = subprocess.run([sys.executable, HOOK_PATH, "whoami",
                            "--session-id", self.VICTIM],
                           capture_output=True, text=True, cwd=self.root,
                           env=_clean_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(token, r.stdout)
        self.assertNotIn(token, r.stderr)
        self.assertIn(label, r.stdout)
        # And the deny message, the one string that reaches the model.
        decision, _n = self.decide(payload(self.OTHER, self.root))
        reason = self.assertDenied(decision)
        self.assertNotIn(token, reason)
        self.assertNotIn(fh.ensure_token(self.root, self.OTHER), reason)

    def test_label_cannot_be_derived_without_the_token(self):
        """A one-way hash, asserted as a property rather than assumed: the
        label must not be a function of the session id alone, or every reader
        of a session id could compute the owner's label and the token would
        be decoration."""
        token = fh.ensure_token(self.root, self.VICTIM)
        label = fh.label_for_token(token)
        os.remove(fh.token_path(self.root, self.VICTIM))
        self.assertNotEqual(fh.session_label(self.root, self.VICTIM), label,
                            "a fresh token must yield a different label")

    @unittest.skipIf(sys.platform == "win32",
                     "POSIX mode bits; on Windows os.chmod only toggles the "
                     "read-only bit and the ACL is the real protection")
    def test_token_file_is_owner_only_on_posix(self):
        fh.ensure_token(self.root, self.VICTIM)
        p = fh.token_path(self.root, self.VICTIM)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        d = fh.fence_dir(self.root)
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)

    def test_missing_session_id_fails_open(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        p = payload(self.VICTIM, self.root)
        del p["session_id"]
        decision, notes = self.decide(p)
        self.assertAllowed(decision)
        self.assertTrue(any("no verifiable identity" in n for n in notes), notes)


# ---------------------------------------------------------------------------
# Calibration 4: fail open, loudly, on every broken-store condition.
# ---------------------------------------------------------------------------

class CalibratedFailOpen(FenceHookBase):

    def _assert_fails_open(self, decision, notes, fragment):
        self.assertAllowed(decision)
        self.assertTrue(notes, "a fail-open must be LOUD; stderr was empty")
        joined = " ".join(notes)
        self.assertIn("FAILING OPEN", joined)
        self.assertIn(fragment, joined)

    def test_calibrated_4_store_deleted_mid_session_fails_open(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        for suffix in ("", "-wal", "-shm"):
            p = bs.store_path(self.root) + suffix
            if os.path.exists(p):
                os.remove(p)
        decision, notes = self.decide(payload(self.OTHER, self.root))
        self._assert_fails_open(decision, notes, "no store at")

    def test_zero_byte_store_fails_open(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        with io.open(bs.store_path(self.root), "w", encoding="utf-8") as f:
            f.write("")
        decision, notes = self.decide(payload(self.OTHER, self.root))
        self._assert_fails_open(decision, notes, "could not be opened read-only")

    def test_garbage_store_fails_open(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        with io.open(bs.store_path(self.root), "w", encoding="utf-8") as f:
            f.write("this is not a sqlite database, not even a little bit")
        decision, notes = self.decide(payload(self.OTHER, self.root))
        self.assertAllowed(decision)
        self.assertTrue(any("FAILING OPEN" in n for n in notes), notes)

    def test_no_claims_at_all_fails_open(self):
        decision, notes = self.decide(payload(self.OTHER, self.root))
        self._assert_fails_open(decision, notes, "no active claims")

    def test_no_project_root_fails_open(self):
        outside = os.path.realpath(tempfile.mkdtemp())
        try:
            p = payload(self.OTHER, outside)
            decision, notes = self.decide(p)
            self._assert_fails_open(decision, notes, "no BrotherMode project root")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_malformed_payload_fails_open(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        for bad in (None, [], "string", 42):
            decision, notes = self.decide(bad)
            self.assertAllowed(decision)
            self.assertTrue(any("FAILING OPEN" in n for n in notes), (bad, notes))

    def test_write_tool_with_no_path_fails_open(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        p = payload(self.OTHER, self.root, tool_input={"content": "x"})
        decision, notes = self.decide(p)
        self._assert_fails_open(decision, notes, "no target path found")

    def test_unimportable_store_module_fails_open(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        with mock.patch.object(fh, "_load_store_module", return_value=None):
            decision, notes = self.decide(payload(self.OTHER, self.root))
        self._assert_fails_open(decision, notes, "could not be imported")

    def test_unexpected_exception_fails_open(self):
        """The blanket catch, asserted rather than assumed. An unforeseen bug
        in this hook must never become a refusal in front of the founder."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        def boom(root):
            raise RuntimeError("simulated internal defect")
        with mock.patch.object(fh, "active_claims", boom):
            decision, notes = self.decide(payload(self.OTHER, self.root))
        self.assertAllowed(decision)
        joined = " ".join(notes)
        self.assertIn("FAILING OPEN after an unexpected error", joined)
        self.assertIn("simulated internal defect", joined)

    def test_broken_store_fails_open_through_the_real_process_too(self):
        """The in-process assertions above prove decide(); this proves the
        whole executable, because a fail-open that only holds when called as a
        function is not the thing installed in front of the founder's edits."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        os.remove(bs.store_path(self.root))
        r = self.run_hook(payload(self.OTHER, self.root))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "", "fail-open must emit NO decision JSON")
        self.assertIn("FAILING OPEN", r.stderr)


# ---------------------------------------------------------------------------
# Path canonicalization: a comparison on unresolved strings is no fence.
# ---------------------------------------------------------------------------

class EnforcedModeFailsClosed(FenceHookBase):
    """C-01 (2026-08-03). An adversarial probe found that EVERY failure path in
    this hook produced an allow: nine conditions each let a write through
    another session's active fence with exit 0, and BM_FENCE_STRICT could not
    help because it is read after all of them and is a no-op on a zero-claims
    store. BM_FENCE_MODE=enforced makes those same conditions refuse.

    Every test here asserts BOTH directions on the same condition: deny under
    enforced, allow under the default. The second half is the important one.
    It is what stops a later change from quietly making fail-closed the
    default for people who never asked for it, which this project decided
    against on purpose."""

    def _both_ways(self, break_it, fragment=None):
        """Apply break_it(), then assert deny in enforced and allow by default."""
        break_it()
        p = payload(self.OTHER, self.root)
        with mock.patch.dict(os.environ, {"BM_FENCE_MODE": "enforced"}):
            decision, notes = self.decide(p)
        reason = self.assertDenied(decision)
        self.assertIn("enforced mode", reason)
        self.assertTrue(any("FAILING CLOSED" in n for n in notes), notes)
        if fragment is not None:
            self.assertIn(fragment, " ".join(notes))
        with mock.patch.dict(os.environ, {"BM_FENCE_MODE": ""}):
            decision2, notes2 = self.decide(p)
        self.assertAllowed(decision2)
        self.assertTrue(any("FAILING OPEN" in n for n in notes2), notes2)

    def _remove_store(self):
        for suffix in ("", "-wal", "-shm"):
            p = bs.store_path(self.root) + suffix
            if os.path.exists(p):
                os.remove(p)

    def test_store_missing_denies_when_enforced(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        self._both_ways(self._remove_store, "no store at")

    def test_store_zero_bytes_denies_when_enforced(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))

        def blank():
            with io.open(bs.store_path(self.root), "w", encoding="utf-8") as f:
                f.write("")
        self._both_ways(blank)

    def test_store_corrupt_denies_when_enforced(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))

        def garbage():
            with io.open(bs.store_path(self.root), "w", encoding="utf-8") as f:
                f.write("this is not a sqlite database, not even a little bit")
        self._both_ways(garbage)

    def test_zero_active_claims_denies_when_enforced(self):
        """The state a FRESH project sits in, and the one the probe called the
        worst of the nine: with no active claims the fence allowed everything,
        and BM_FENCE_STRICT short-circuited before it could tighten anything."""
        self._both_ways(lambda: None, "no active claims")

    def test_no_project_root_allows_even_when_enforced(self):
        """The ONE condition that still allows under enforcement, and it has to
        be. A directory with no BrotherMode project has no fence to enforce, so
        denying here would stop editing in every unrelated directory on the
        machine the moment somebody exported the variable. That is the brick
        this hook exists to avoid, and an earlier draft of this very feature
        got it wrong and asserted the deny."""
        outside = os.path.realpath(tempfile.mkdtemp())
        try:
            p = payload(self.OTHER, outside)
            with mock.patch.dict(os.environ, {"BM_FENCE_MODE": "enforced"}):
                decision, notes = self.decide(p)
            self.assertAllowed(decision)
            self.assertTrue(any("FAILING OPEN" in n for n in notes), notes)
            with mock.patch.dict(os.environ, {"BM_FENCE_MODE": ""}):
                decision2, _ = self.decide(p)
            self.assertAllowed(decision2)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_deny_reason_never_leaks_a_path_or_payload_content(self):
        """The stderr note may name paths; the deny REASON may not. It is read
        by the model and lands in a transcript, and at the moment it is
        produced nothing has been verified, so no verified context justifies
        quoting a path. Every enforced-mode reason is drawn from a literal
        table for exactly this reason."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        for suffix in ("", "-wal", "-shm"):
            q = bs.store_path(self.root) + suffix
            if os.path.exists(q):
                os.remove(q)
        p = payload(self.OTHER, self.root)
        with mock.patch.dict(os.environ, {"BM_FENCE_MODE": "enforced"}):
            decision, notes = self.decide(p)
        reason = self.assertDenied(decision)
        self.assertNotIn(self.root, reason)
        self.assertNotIn("src/app.py", reason)
        self.assertNotIn(os.path.expanduser("~"), reason)
        # The operator's channel is allowed to be specific, and must be, or
        # the refusal is not actionable at the terminal.
        self.assertTrue(any(self.root in n for n in notes),
                        "stderr must still name the path for the operator")

    def test_an_unrecognized_mode_value_runs_advisory_and_warns(self):
        """Denying on a typo would brick editing over a missing letter;
        silently accepting one would let somebody who asked for enforcement
        quietly not get it. So: advisory, plus a warning that prints every
        time rather than once."""
        mode, warning = fh.fence_mode({"BM_FENCE_MODE": "enfoced"})
        self.assertEqual(mode, fh.MODE_ADVISORY)
        self.assertIsNotNone(warning)
        self.assertIn("not a recognized mode", warning)
        self.assertEqual(fh.fence_mode({"BM_FENCE_MODE": "enforced"}),
                         (fh.MODE_ENFORCED, None))
        self.assertEqual(fh.fence_mode({}), (fh.MODE_ADVISORY, None))

    def test_a_raise_without_a_code_still_denies_when_enforced(self):
        """The default code is 'unknown' and 'unknown' DENIES, so a raise added
        later that forgets to pass a code refuses rather than quietly
        reopening the hole this class was audited for."""
        self.assertEqual(fh._FailOpen("something").code, "unknown")
        self.assertEqual(fh._FailOpen("x", "not-a-real-code").code, "unknown")
        self.assertNotIn("unknown", fh._ALLOW_EVEN_WHEN_ENFORCED)

    def test_malformed_payloads_deny_when_enforced(self):
        """The five shapes the probe drove, each its own allow before this."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        good = payload(self.OTHER, self.root)
        shapes = [
            ("not an object", []),
            ("tool_input not an object",
             dict(good, tool_input="nope")),
            ("no session_id",
             {k: v for k, v in good.items() if k != "session_id"}),
            ("no target path",
             dict(good, tool_input={})),
            ("unrecognized path key",
             dict(good, tool_input={"some_future_key": "src/app.py"})),
        ]
        for name, shape in shapes:
            with mock.patch.dict(os.environ, {"BM_FENCE_MODE": "enforced"}):
                decision, _ = self.decide(shape)
            self.assertIsNotNone(
                decision,
                "enforced mode allowed a malformed payload (%s)" % name)
            with mock.patch.dict(os.environ, {"BM_FENCE_MODE": ""}):
                decision2, _ = self.decide(shape)
            self.assertIsNone(
                decision2,
                "default mode must still allow a malformed payload (%s)" % name)

    def test_internal_exception_denies_when_enforced(self):
        """The blanket catch: in advisory it turns any bug in this file into an
        allow, which is deliberate. Somebody who asked for fail-closed has said
        they would rather be stopped by that bug than wave a write through."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        p = payload(self.OTHER, self.root)
        boom = mock.patch.object(
            fh, "session_label", side_effect=RuntimeError("seeded"))
        with mock.patch.dict(os.environ, {"BM_FENCE_MODE": "enforced"}), boom:
            decision, notes = self.decide(p)
        self.assertIn("enforced mode", self.assertDenied(decision))
        self.assertTrue(any("FAILING CLOSED" in n for n in notes), notes)
        with mock.patch.dict(os.environ, {"BM_FENCE_MODE": ""}), mock.patch.object(
                fh, "session_label", side_effect=RuntimeError("seeded")):
            decision2, _ = self.decide(p)
        self.assertAllowed(decision2)

    def test_owner_is_still_allowed_when_enforced(self):
        """Enforcement must not break the happy path: the session that HOLDS
        the claim still writes freely."""
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        p = payload(self.VICTIM, self.root)
        with mock.patch.dict(os.environ, {"BM_FENCE_MODE": "enforced"}):
            decision, _ = self.decide(p)
        self.assertAllowed(decision)

    def test_mode_value_is_exact_and_case_insensitive(self):
        self.assertTrue(fh.enforced_mode({"BM_FENCE_MODE": "enforced"}))
        self.assertTrue(fh.enforced_mode({"BM_FENCE_MODE": " ENFORCED "}))
        self.assertFalse(fh.enforced_mode({"BM_FENCE_MODE": "advisory"}))
        self.assertFalse(fh.enforced_mode({"BM_FENCE_MODE": "1"}))
        self.assertFalse(fh.enforced_mode({"BM_FENCE_MODE": ""}))
        self.assertFalse(fh.enforced_mode({}))


class Canonicalization(FenceHookBase):

    def setUp(self):
        FenceHookBase.setUp(self)
        self.mine = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], self.mine)

    def _denied(self, raw, cwd=None):
        p = payload(self.OTHER, cwd or self.root, tool_input={"file_path": raw})
        decision, notes = self.decide(p)
        self.assertIsNotNone(
            decision,
            "path %r reached the fenced file but was ALLOWED (notes: %r)"
            % (raw, notes))

    def test_dot_dot_traversal_is_resolved(self):
        self._denied("src/../src/app.py")
        self._denied("./src/./app.py")

    def test_absolute_path_is_resolved(self):
        self._denied(os.path.join(self.root, "src", "app.py"))

    def test_relative_path_from_a_subdirectory_is_resolved(self):
        self._denied("app.py", cwd=os.path.join(self.root, "src"))

    @unittest.skipIf(not hasattr(os, "symlink") or sys.platform == "win32",
                     "symlink creation needs privileges on Windows")
    def test_symlinked_path_is_resolved(self):
        link = os.path.join(self.root, "link.py")
        os.symlink(os.path.join(self.root, "src", "app.py"), link)
        self._denied("link.py")
        linkdir = os.path.join(self.root, "srclink")
        os.symlink(os.path.join(self.root, "src"), linkdir)
        self._denied("srclink/app.py")

    def test_path_outside_the_root_is_allowed(self):
        outside = os.path.realpath(tempfile.mkdtemp())
        try:
            p = payload(self.OTHER, self.root,
                        tool_input={"file_path": os.path.join(outside, "x.py")})
            decision, _n = self.decide(p)
            self.assertAllowed(decision)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_directory_claim_covers_its_children(self):
        with bs.Store(self.root, create=False) as store:
            rec = store.claim("api", "ephemeral", files=["src"],
                              session_id=self.mine)
        self._denied("src/other.py")
        self._denied("src/deeper/new_file.py")

    def test_glob_claim_covers_a_concrete_file(self):
        with bs.Store(self.root, create=False) as store:
            store.claim("api", "ephemeral", files=["src/*.py"],
                        session_id=self.mine)
        self._denied("src/other.py")

    @unittest.skipIf(sys.platform not in ("darwin", "win32"),
                     "only normalization-insensitive filesystems fold NFC "
                     "and NFD onto one inode")
    def test_calibrated_unicode_normalization_is_not_a_bypass(self):
        """Regression, fix-round 2026-07-29, reproduced end to end before the
        fix: 'src/cafe.py' with an accented e claimed in NFC was ONE inode
        with the same name in NFD and TWO different strings, so a foreign
        session Editing the NFD spelling found covering=[], foreign=[], and
        the hook returned no decision, which is ALLOW. Appending through the
        NFD path really did add a line to the NFC-claimed file. That is a
        supported-tool bypass, not an arbitrary-shell limit."""
        nfc = unicodedata.normalize("NFC", "src/café_日本.py")
        nfd = unicodedata.normalize("NFD", "src/café_日本.py")
        self.assertNotEqual(nfc, nfd)
        with io.open(os.path.join(self.root, nfc), "w",
                     encoding="utf-8") as f:
            f.write("ORIG\n")
        with bs.Store(self.root, create=False) as store:
            store.claim("uni", "ephemeral", files=[nfc],
                        session_id=self.mine)
        for spelling in (nfc, nfd):
            decision, notes = self.decide(
                payload(self.OTHER, self.root,
                        tool_input={"file_path": spelling}))
            self.assertIsNotNone(
                decision,
                "%r was ALLOWED across the fence (notes: %r)"
                % (spelling, notes))
        # Restore half: the OWNER is still allowed either spelling, so the
        # fix denies the intruder without bricking the writer.
        for spelling in (nfc, nfd):
            decision, notes = self.decide(
                payload(self.VICTIM, self.root,
                        tool_input={"file_path": spelling}))
            self.assertIsNone(decision, notes)

    def test_a_file_that_does_not_exist_yet_is_still_fenced(self):
        """Write creates files. A fence that only worked on existing paths
        would be bypassed by creating the file first."""
        with bs.Store(self.root, create=False) as store:
            store.claim("api", "ephemeral", files=["src"], session_id=self.mine)
        p = payload(self.OTHER, self.root, tool_name="Write",
                    tool_input={"file_path": "src/brand_new.py", "content": "x"})
        decision, _n = self.decide(p)
        self.assertIsNotNone(decision)


# ---------------------------------------------------------------------------
# Tool surface.
# ---------------------------------------------------------------------------

class ToolSurface(FenceHookBase):

    def setUp(self):
        FenceHookBase.setUp(self)
        self.mine = self.label(self.VICTIM)
        self.claim("api", ["src"], self.mine)

    def test_non_write_tools_pass_through_silently(self):
        for tool in ("Read", "Grep", "Glob", "Bash", "WebFetch", "Task"):
            p = payload(self.OTHER, self.root, tool_name=tool,
                        tool_input={"file_path": "src/app.py"})
            decision, notes = self.decide(p)
            self.assertAllowed(decision)
            self.assertEqual(notes, [],
                             "%s must pass through with no stderr noise" % tool)

    def test_every_write_tool_is_gated(self):
        cases = {
            "Edit": {"file_path": "src/app.py"},
            "Write": {"file_path": "src/app.py", "content": "x"},
            "MultiEdit": {"file_path": "src/app.py", "edits": [{"old_string": "a"}]},
            "NotebookEdit": {"notebook_path": "src/nb.ipynb", "new_source": "x"},
        }
        for tool, ti in cases.items():
            p = payload(self.OTHER, self.root, tool_name=tool, tool_input=ti)
            decision, notes = self.decide(p)
            self.assertIsNotNone(decision, "%s was not gated (%r)" % (tool, notes))

    def test_nested_per_edit_paths_are_extracted(self):
        """A MultiEdit-shaped payload whose per-edit entries carry their own
        file_path must not be reduced to the top-level path alone: the fence
        would then be bypassed by putting the fenced file in edits[1]."""
        p = payload(self.OTHER, self.root, tool_name="MultiEdit",
                    tool_input={"file_path": "README.md",
                                "edits": [{"file_path": "src/app.py",
                                           "old_string": "a", "new_string": "b"}]})
        decision, _n = self.decide(p)
        self.assertIsNotNone(decision)

    def test_extract_targets_is_bounded(self):
        """A hostile payload must not make the hook recurse or scan forever:
        it runs in front of EVERY edit."""
        deep = {"file_path": "a.py"}
        node = deep
        for _ in range(200):
            node = {"nested": node}
        got = fh.extract_targets(node)
        self.assertEqual(got, [], "depth must be bounded")
        wide = {"edits": [{"file_path": "f%d.py" % i} for i in range(500)]}
        self.assertLessEqual(len(fh.extract_targets(wide)), 64)


# ---------------------------------------------------------------------------
# Codex apply_patch: the file write that arrives dressed as Bash (L06).
# ---------------------------------------------------------------------------

#: Captured from a real Codex 0.146 session on 2026-08-05 (docs/RUNTIMES.md):
#: under the Codex CLI every file write reaches hooks as tool_name Bash whose
#: tool_input.command holds an apply_patch heredoc. This is the measured
#: command, verbatim. The tests below are written from THIS capture.
CAPTURED_APPLY_PATCH_COMMAND = (
    "apply_patch <<'PATCH'\n"
    "*** Begin Patch\n"
    "*** Add File: probe_written_by_codex.txt\n"
    "+hello from apply_patch\n"
    "*** End Patch\n"
    "PATCH")


def apply_patch_command(*body_lines):
    """A well formed apply_patch heredoc around the given envelope body."""
    return ("apply_patch <<'PATCH'\n*** Begin Patch\n"
            + "\n".join(body_lines)
            + "\n*** End Patch\nPATCH")


class CodexApplyPatch(FenceHookBase):
    """The one-writer fence under the Codex CLI, where PreToolUse is the
    WHOLE decision: apply_patch fires PreToolUse and never PostToolUse
    (measured twice, recorded in docs/RUNTIMES.md), so nothing here may lean
    on a second look after the write.

    Calibrated both ways, like the Edit path above: the captured payload is
    denied when its path is fenced to another owner and allowed when it is
    not; an envelope whose directives cannot be read is refused rather than
    shrugged at; and a plain shell command passes exactly as silently as it
    did when Bash was not matched at all, because this branch runs in front
    of EVERY Bash call once the matcher widens."""

    def bash(self, session_id, command):
        return payload(session_id, self.root, tool_name="Bash",
                       tool_input={"command": command},
                       tool_use_id="call_fake_1")

    # -- the captured payload, both ways ---------------------------------

    def test_calibrated_captured_payload_denied_when_fenced_to_another_owner(self):
        mine = self.label(self.VICTIM)
        rec = self.claim("probe", ["probe_written_by_codex.txt"], mine)
        decision, _n = self.decide(
            self.bash(self.OTHER, CAPTURED_APPLY_PATCH_COMMAND))
        reason = self.assertDenied(decision)
        # Same actionability bar as the Edit deny: the path, the record, the
        # owner, and the exact takeover command.
        self.assertIn("probe_written_by_codex.txt", reason)
        self.assertIn(rec.lifecycle_uuid, reason)
        self.assertIn(mine, reason)
        self.assertIn("--adopt-from-live-session", reason)

    def test_calibrated_captured_payload_allowed_when_unfenced(self):
        # A live fence exists, just not over the patch's target: the allow
        # must be a real pass, not a fail-open in disguise.
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        decision, notes = self.decide(
            self.bash(self.OTHER, CAPTURED_APPLY_PATCH_COMMAND))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], "an unfenced apply_patch must pass "
                                    "silently: %r" % (notes,))

    def test_calibrated_owner_may_run_the_captured_patch(self):
        mine = self.label(self.VICTIM)
        self.claim("probe", ["probe_written_by_codex.txt"], mine)
        decision, notes = self.decide(
            self.bash(self.VICTIM, CAPTURED_APPLY_PATCH_COMMAND))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], notes)

    # -- every directive form is a checked write --------------------------

    def test_multi_file_patch_denies_naming_the_second_file(self):
        mine = self.label(self.VICTIM)
        self.claim("second", ["src/other.py"], mine)
        cmd = apply_patch_command(
            "*** Update File: README.md",
            "@@",
            "-old",
            "+new",
            "*** Update File: src/other.py",
            "@@",
            "-old",
            "+new")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        reason = self.assertDenied(decision)
        self.assertIn("src/other.py", reason)
        self.assertNotIn("README.md", reason,
                         "the deny must name the FENCED file, not the first")

    def test_update_file_directive_is_checked(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = apply_patch_command("*** Update File: src/app.py", "@@",
                                  "-# app.py", "+# changed")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    def test_delete_file_directive_is_checked(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = apply_patch_command("*** Delete File: src/app.py")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    def test_move_to_target_is_checked(self):
        """A rename's Move to TARGET is also a written path: a fence that
        checked only the source would be bypassed by renaming onto the
        fenced file."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = apply_patch_command("*** Update File: src/free.py",
                                  "*** Move to: src/app.py")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    # -- what must NOT match ----------------------------------------------

    def test_echo_apply_patch_is_not_matched(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        decision, notes = self.decide(self.bash(self.OTHER, "echo apply_patch"))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], "a plain command must stay silent")

    def test_marker_inside_a_quoted_argument_is_not_an_envelope(self):
        """DOCUMENTED CHOICE: an envelope is recognized only when the marker
        OPENS a line. apply_patch is itself a line-based parser, so a marker
        buried mid-line (here, a grep pattern) can never reach a real write,
        and denying it would fence the founder off from searching their own
        notes. Allowed, silently."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        decision, notes = self.decide(
            self.bash(self.OTHER, 'grep "*** Begin Patch" notes.md'))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], notes)

    def test_a_heredoc_that_is_not_apply_patch_is_not_matched(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = "cat <<'EOF' > notes.md\nplain notes, nothing patch shaped\nEOF"
        decision, notes = self.decide(self.bash(self.OTHER, cmd))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], notes)

    def test_plain_bash_is_silent_even_with_no_session_id(self):
        """The fast path out must run BEFORE identity: a plain shell command
        is not the fence's business, and it must not start costing a token
        derivation or a stderr line per command once the matcher widens."""
        p = self.bash(self.OTHER, "ls -la")
        del p["session_id"]
        decision, notes = self.decide(p)
        self.assertAllowed(decision)
        self.assertEqual(notes, [], notes)

    # -- malformed envelopes: parseable part is authoritative -------------

    def test_begin_without_end_and_no_directives_is_denied_as_unreadable(self):
        """An envelope with no readable file directive is a write whose
        targets are unknowable. Refused, not failed open: a fence that
        shrugs at an unparseable write is the silent no-op again."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = "apply_patch <<'PATCH'\n*** Begin Patch\nPATCH"
        decision, notes = self.decide(self.bash(self.OTHER, cmd))
        reason = self.assertDenied(decision)
        self.assertIn("cannot certify", reason)
        self.assertTrue(any("apply_patch envelope" in n for n in notes),
                        "the unreadable refusal must be loud on stderr: %r"
                        % (notes,))

    def test_begin_without_end_with_a_readable_directive_checks_it(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = ("apply_patch <<'PATCH'\n*** Begin Patch\n"
               "*** Update File: src/app.py\n+x\nPATCH")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    def test_empty_directive_path_alone_is_denied_as_unreadable(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = apply_patch_command("*** Add File: ")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("cannot certify", self.assertDenied(decision))

    def test_directive_outside_the_envelope_is_still_checked(self):
        """A directive that escaped the Begin/End pair still names a file the
        patch intends to touch: the parseable part is authoritative."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = ("apply_patch <<'PATCH'\n*** Begin Patch\n*** End Patch\n"
               "*** Update File: src/app.py\nPATCH")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    # -- the wire, and the capture fed verbatim ---------------------------

    def test_apply_patch_deny_uses_the_documented_wire_shape(self):
        self.claim("probe", ["probe_written_by_codex.txt"],
                   self.label(self.VICTIM))
        r = self.run_hook(self.bash(self.OTHER, CAPTURED_APPLY_PATCH_COMMAND))
        self.assertEqual(r.returncode, 0,
                         "deny is exit 0 with JSON on stdout, never exit 2")
        obj = json.loads(r.stdout)
        hso = obj["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("probe_written_by_codex.txt",
                      hso["permissionDecisionReason"])

    def test_captured_payload_verbatim_shape_without_session_id(self):
        """The capture carries ONLY tool_name, tool_input and tool_use_id:
        no session_id, no cwd. Fed verbatim, the hook treats it exactly as it
        treats an Edit with no session id, because the SAME code path decides
        both: advisory fails open LOUDLY, enforced refuses. Recorded so the
        wiring decision (what Codex sends beside tool_input) is made on
        evidence, not assumption."""
        self.claim("probe", ["probe_written_by_codex.txt"],
                   self.label(self.VICTIM))
        verbatim = {
            "tool_name": "Bash",
            "tool_input": {"command": CAPTURED_APPLY_PATCH_COMMAND},
            "tool_use_id": "call_fake_1",
        }
        r = self.run_hook(verbatim, env={"BM_FENCE_MODE": ""})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "", "advisory must fail open on a payload "
                                       "with no session id")
        self.assertIn("no verifiable identity", r.stderr)
        r2 = self.run_hook(verbatim, env={"BM_FENCE_MODE": "enforced"})
        self.assertEqual(r2.returncode, 0)
        obj = json.loads(r2.stdout)
        self.assertEqual(
            obj["hookSpecificOutput"]["permissionDecision"], "deny")

    # -- hostile characters, and the parser itself ------------------------

    def test_hostile_directive_path_is_parsed_and_fenced(self):
        # Accented and CJK characters, as escapes so this file stays ASCII.
        name = "src/caf\u00e9_\u65e5\u672c.py"
        with io.open(os.path.join(self.root, name), "w",
                     encoding="utf-8") as f:
            f.write("ORIG\n")
        self.claim("uni", [name], self.label(self.VICTIM))
        cmd = apply_patch_command("*** Update File: " + name, "+x")
        decision, notes = self.decide(self.bash(self.OTHER, cmd))
        self.assertIsNotNone(
            decision, "a non-ASCII directive path crossed the fence "
                      "unchecked (notes: %r)" % (notes,))

    def test_extract_patch_targets_reads_all_four_directive_forms(self):
        cmd = apply_patch_command(
            "*** Add File: a.txt",
            "*** Update File: b/c.py",
            "*** Move to: b/d.py",
            "*** Delete File: e.txt",
            "*** Update File: b/c.py")
        got, envelope_seen = fh.extract_patch_targets(cmd)
        self.assertTrue(envelope_seen)
        # In input order, de-duplicated, move TARGET included.
        self.assertEqual(got, ["a.txt", "b/c.py", "b/d.py", "e.txt"])

    def test_extract_patch_targets_sees_no_envelope_in_a_quoted_marker(self):
        got, envelope_seen = fh.extract_patch_targets(
            'grep "*** Begin Patch" notes.md')
        self.assertFalse(envelope_seen)
        self.assertEqual(got, [])


# ---------------------------------------------------------------------------
# Codex apply_patch, refute round 2 (L06): the matcher must know which
# program runs and where the heredoc body is, not just scan text.
# ---------------------------------------------------------------------------

class CodexApplyPatchRefuteRound2(FenceHookBase):
    """An adversarial refuter confirmed three FALSE-denies, all one defect:
    the round-1 matcher was a pure textual line scan. It stripped leading
    whitespace before testing for a directive (so an apply_patch hunk CONTEXT
    line, which begins with a space, was harvested as a phantom directive),
    and it entered the apply_patch branch on the mere SUBSTRING of the
    envelope marker (so any heredoc to any command, or any command whose text
    quoted the grammar, was treated as an apply_patch write). In THIS project,
    whose docs and tests are saturated with the grammar, those false denies
    are the common case, not a corner. Each test below pairs the false-deny
    reproduction with the behavior it must now have, and every genuine fenced
    apply_patch write must still DENY."""

    def bash(self, session_id, command):
        return payload(session_id, self.root, tool_name="Bash",
                       tool_input={"command": command},
                       tool_use_id="call_fake_1")

    # -- FD-1: a hunk CONTEXT line is content, never a directive ----------

    FD1 = ("apply_patch <<'PATCH'\n"
           "*** Begin Patch\n"
           "*** Update File: notes.md\n"
           "@@\n"
           " *** Update File: src/secret.py\n"
           "-plain notes\n"
           "+z\n"
           "*** End Patch\nPATCH")

    def test_fd1_context_line_quoting_a_directive_is_not_a_write(self):
        """The only real target is notes.md (unfenced). The line
        ' *** Update File: src/secret.py' has a LEADING SPACE: it is an
        apply_patch context line, i.e. CONTENT written into notes.md, and
        apply_patch never touches src/secret.py. The round-1 hook stripped
        the space and fenced src/secret.py. It must now ALLOW."""
        self.claim("secret", ["src/secret.py"], self.label(self.VICTIM))
        decision, notes = self.decide(self.bash(self.OTHER, self.FD1))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], "an unfenced apply_patch must pass "
                                    "silently: %r" % (notes,))

    def test_fd1_real_column_zero_directive_to_fenced_file_still_denies(self):
        """The other half: a genuine directive at column 0 to the fenced file
        must still DENY, so the FD-1 fix narrows nothing it should not."""
        self.claim("secret", ["src/secret.py"], self.label(self.VICTIM))
        cmd = apply_patch_command("*** Update File: src/secret.py", "@@",
                                  "-a", "+b")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/secret.py", self.assertDenied(decision))

    def test_fd1_added_and_removed_lines_are_not_directives(self):
        """The '+' and '-' prefixes are added / removed hunk lines, also
        never directives. Round 1 happened to survive these because it did
        not strip '+'/'-'; asserted here so the fix keeps surviving them."""
        self.claim("secret", ["src/secret.py"], self.label(self.VICTIM))
        cmd = apply_patch_command(
            "*** Update File: notes.md", "@@",
            "+*** Add File: src/secret.py",
            "-*** Update File: src/secret.py")
        decision, notes = self.decide(self.bash(self.OTHER, cmd))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], notes)

    # -- FD-2 and FD-3: only apply_patch's OWN heredoc is a patch ----------

    FD2A = ("cat > howto.md <<'EOF'\n"
            "*** Begin Patch\n"
            "*** Update File: src/app.py\n"
            "*** End Patch\nEOF")

    def test_fd2a_cat_heredoc_documenting_the_grammar_is_not_a_write(self):
        """This writes howto.md with cat and never invokes apply_patch. The
        round-1 hook entered the patch branch on the marker substring alone
        and fenced src/app.py. It must now ALLOW."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        decision, notes = self.decide(self.bash(self.OTHER, self.FD2A))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], notes)

    FD2B = ("cat > howto.md <<'EOF'\n"
            "To patch, run apply_patch like this:\n"
            "*** Begin Patch\n"
            "*** Update File: src/app.py\n"
            "*** End Patch\nEOF")

    def test_fd2b_non_apply_patch_heredoc_whose_body_names_apply_patch(self):
        """Harder than FD2A: the body literally contains the token
        apply_patch, so a fast substring gate on 'apply_patch' would still
        wrongly fire. The heredoc is OWNED by cat, so it is not a patch. Must
        ALLOW, which proves the owner check, not just the marker fast path."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        decision, notes = self.decide(self.bash(self.OTHER, self.FD2B))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], notes)

    FD3 = ("git commit -F - <<'EOF'\n"
           "Document apply_patch grammar\n"
           "\n"
           "*** Begin Patch\n"
           "*** Update File: src/app.py\n"
           "*** End Patch\nEOF")

    def test_fd3_git_commit_heredoc_writes_no_file_and_is_allowed(self):
        """A commit message that documents the grammar writes no file at all.
        The heredoc is owned by git, not apply_patch. Must ALLOW."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        decision, notes = self.decide(self.bash(self.OTHER, self.FD3))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], notes)

    # -- the floor from section 3: real apply_patch heredocs still DENY ---

    def test_apply_patch_heredoc_still_denies_the_fenced_write(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = apply_patch_command("*** Update File: src/app.py", "@@", "-a", "+b")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    def test_dash_heredoc_with_tab_indented_directives_still_denies(self):
        """A '<<-' heredoc: the shell strips leading TABS from the body
        before apply_patch sees it, so a tab-indented directive is a real
        column-0 directive to apply_patch. Must still DENY. This is why the
        fix must strip tabs for '<<-' specifically, not strip all whitespace
        for everything."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = ("apply_patch <<-'PATCH'\n"
               "\t*** Begin Patch\n"
               "\t*** Update File: src/app.py\n"
               "\t*** End Patch\n"
               "\tPATCH")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    def test_env_assignment_prefix_before_apply_patch_still_denies(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = ("FOO=bar apply_patch <<'PATCH'\n"
               "*** Begin Patch\n*** Update File: src/app.py\n"
               "*** End Patch\nPATCH")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    def test_unquoted_and_double_quoted_delimiters_still_deny(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        for op in ("<<PATCH", '<<"PATCH"'):
            cmd = ("apply_patch %s\n*** Begin Patch\n"
                   "*** Update File: src/app.py\n*** End Patch\nPATCH" % op)
            decision, _n = self.decide(self.bash(self.OTHER, cmd))
            self.assertIn("src/app.py", self.assertDenied(decision),
                          "delimiter %r must still DENY" % op)

    def test_apply_patch_owned_heredoc_in_a_pipeline_still_denies(self):
        """foo | apply_patch <<PATCH: the heredoc feeds apply_patch even
        though a pipe precedes it. The owner is the segment nearest the '<<'.
        Must DENY."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = ("true | apply_patch <<'PATCH'\n*** Begin Patch\n"
               "*** Update File: src/app.py\n*** End Patch\nPATCH")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    # -- H3 from the same review: a foreign BROTHERMODE_ROOT must not -----
    # -- redirect this hook to the wrong store ----------------------------

    def test_foreign_env_root_does_not_disarm_the_fence(self):
        """H3 (cross-family review 2026-08-08, finding 2): an inherited
        BROTHERMODE_ROOT naming a tree unrelated to the write's own project
        used to win root resolution outright, so the hook consulted a
        foreign store, found no claims, and allowed the fenced write. With
        the env answer scoped to trees that contain the hook's cwd, the
        walk finds the real project and the fence stays live. Must DENY."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        foreign = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, foreign, ignore_errors=True)
        os.environ["BROTHERMODE_ROOT"] = foreign
        p = payload(self.OTHER, self.root, tool_name="Edit",
                    tool_input={"file_path":
                                os.path.join(self.root, "src", "app.py")})
        decision, _n = self.decide(p)
        self.assertIn("src/app.py", self.assertDenied(decision))

    # -- H2 from the 2026-08-08 cross-family review: the two parser -------
    # -- shapes that bypassed this fence, now pinned shut -----------------

    def test_command_builtin_prefix_before_apply_patch_still_denies(self):
        """`command apply_patch <<'PATCH'`: the shell's command builtin runs
        apply_patch, so apply_patch is the program receiving the heredoc.
        The old parser returned 'command' as the program word, matched no
        gated command, and allowed the write (H2, first shape). Must DENY."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = ("command apply_patch <<'PATCH'\n*** Begin Patch\n"
               "*** Update File: src/app.py\n*** End Patch\nPATCH")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    def test_command_builtin_with_flag_before_apply_patch_still_denies(self):
        """`command -p apply_patch`: same builtin with an option token in
        front of the program word. Must DENY for the same reason."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = ("command -p apply_patch <<'PATCH'\n*** Begin Patch\n"
               "*** Update File: src/app.py\n*** End Patch\nPATCH")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    def test_hyphenated_quoted_delimiter_still_denies(self):
        """<<'PATCH-END': a hyphen is a legal heredoc delimiter character.
        The old scanner stopped the delimiter at the hyphen, read the quote
        as unbalanced, and dropped the whole operator, so the apply_patch
        write sailed through unread (H2, second shape). Must DENY."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = ("apply_patch <<'PATCH-END'\n*** Begin Patch\n"
               "*** Update File: src/app.py\n*** End Patch\nPATCH-END")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("src/app.py", self.assertDenied(decision))

    def test_hyphenated_unquoted_and_tab_stripping_delimiters_still_deny(self):
        """The unquoted and '<<-' spellings of a hyphenated delimiter: the
        delimiter still ends at the hyphen boundary correctly and the body
        is still read. Must DENY in both spellings."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        for op, terminator in (("<<PATCH-END", "PATCH-END"),
                               ("<<-'PATCH-END'", "PATCH-END")):
            cmd = ("apply_patch %s\n*** Begin Patch\n"
                   "*** Update File: src/app.py\n*** End Patch\n%s"
                   % (op, terminator))
            decision, _n = self.decide(self.bash(self.OTHER, cmd))
            self.assertIn("src/app.py", self.assertDenied(decision),
                          "delimiter %r must still DENY" % op)

    def test_unreadable_apply_patch_heredoc_still_denies(self):
        """An apply_patch heredoc with an envelope marker but no readable
        directive is still refused: targets unknowable, and this hook does
        not certify a write it cannot read."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = "apply_patch <<'PATCH'\n*** Begin Patch\nPATCH"
        decision, notes = self.decide(self.bash(self.OTHER, cmd))
        self.assertIn("cannot certify", self.assertDenied(decision))
        self.assertTrue(any("apply_patch envelope" in n for n in notes), notes)

    # -- SUS-1: the named residual, recorded not hidden -------------------

    def test_sus1_printf_piped_to_apply_patch_is_a_recorded_residual(self):
        """SUS-1 from the refute report. The '\\n' inside printf are backslash
        escapes, not real newlines, so the whole command is one physical line
        with no heredoc: there is no heredoc body to read. This is a
        hand-crafted evasion, not what codex-cli emits (Codex emits the
        heredoc form, which IS gated), and it lives inside the arbitrary-shell
        gap docs/HOOKS.md already declares open. Recorded here as an explicit
        ALLOW so the residual is on record, not silently swallowed. If the
        arbitrary-shell gap is ever closed, this test is the tripwire that
        says so."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        cmd = ("printf '*** Begin Patch\\n*** Update File: src/app.py\\n@@\\n"
               "-# app.py\\n+x\\n*** End Patch\\n' | apply_patch")
        decision, _n = self.decide(self.bash(self.OTHER, cmd))
        self.assertAllowed(decision)

    def test_owner_may_run_a_real_apply_patch_heredoc(self):
        owner = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], owner)
        cmd = apply_patch_command("*** Update File: src/app.py", "@@", "-a", "+b")
        decision, notes = self.decide(self.bash(self.VICTIM, cmd))
        self.assertAllowed(decision)
        self.assertEqual(notes, [], notes)


# ---------------------------------------------------------------------------
# Strict mode (opt in): claim before edit.
# ---------------------------------------------------------------------------

class StrictMode(FenceHookBase):

    def test_strict_mode_denies_an_unclaimed_path(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        p = payload(self.VICTIM, self.root, tool_input={"file_path": "src/other.py"})
        with mock.patch.dict(os.environ, {"BM_FENCE_STRICT": "1"}):
            decision, _n = self.decide(p)
        reason = self.assertDenied(decision)
        self.assertIn("strict mode", reason)
        self.assertIn("claim", reason)

    def test_strict_mode_still_allows_the_owner(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        with mock.patch.dict(os.environ, {"BM_FENCE_STRICT": "1"}):
            decision, _n = self.decide(payload(self.VICTIM, self.root))
        self.assertAllowed(decision)

    def test_strict_mode_cannot_override_the_fail_open(self):
        """Strict mode is a tightening, never a way to turn a broken store
        into a refusal: the fail-open rule outranks it."""
        with mock.patch.dict(os.environ, {"BM_FENCE_STRICT": "1"}):
            decision, notes = self.decide(payload(self.VICTIM, self.root))
        self.assertAllowed(decision)
        self.assertTrue(any("no active claims" in n for n in notes), notes)

    def test_strict_mode_is_off_by_default(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        p = payload(self.VICTIM, self.root, tool_input={"file_path": "src/other.py"})
        decision, _n = self.decide(p)
        self.assertAllowed(decision)


# ---------------------------------------------------------------------------
# The wire protocol: stdout is the decision channel and nothing else.
# ---------------------------------------------------------------------------

class WireProtocol(FenceHookBase):

    def test_deny_shape_matches_the_documented_contract(self):
        """Field for field against the PreToolUse decision-control block in
        https://code.claude.com/docs/en/hooks, read 2026-07-27. A hook that
        emits a nearly-right object is silently ignored, which is a fence
        that reports success it did not earn."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        r = self.run_hook(payload(self.OTHER, self.root))
        self.assertEqual(r.returncode, 0,
                         "deny is exit 0 with JSON on stdout, never exit 2")
        obj = json.loads(r.stdout)
        self.assertEqual(list(obj.keys()), ["hookSpecificOutput"])
        hso = obj["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIsInstance(hso["permissionDecisionReason"], str)
        self.assertTrue(hso["permissionDecisionReason"].strip())
        self.assertEqual(sorted(hso.keys()),
                         ["hookEventName", "permissionDecision",
                          "permissionDecisionReason"])

    def test_allow_emits_nothing_on_stdout(self):
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        r = self.run_hook(payload(self.VICTIM, self.root))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_stdout_is_never_polluted_by_diagnostics(self):
        r = self.run_hook(payload(self.VICTIM, self.root))
        self.assertEqual(r.stdout, "")
        self.assertIn("FAILING OPEN", r.stderr)

    def test_empty_and_invalid_stdin_fail_open(self):
        for raw in ("", "   ", "not json", "{", "[1,2,3"):
            r = subprocess.run([sys.executable, HOOK_PATH], input=raw, text=True,
                               capture_output=True, cwd=self.root,
                               env=_clean_env())
            self.assertEqual(r.returncode, 0, raw)
            self.assertEqual(r.stdout, "", raw)
            self.assertIn("FAILING OPEN", r.stderr, raw)

    def test_empty_and_invalid_stdin_fail_CLOSED_when_enforced(self):
        """The twin of the test above, and the reason it exists.

        FIXED 2026-08-10. C-01 recorded that enforced mode denies all nine
        failure conditions "because every one of them funnels through
        _FailOpen or the blanket catch", and the register names a malformed
        payload as one of the nine. It did not funnel through either. Both
        handlers live inside decide(), and cmd_hook returned an allow for an
        unparseable payload BEFORE decide() was ever called, so the single
        condition an attacker most directly controls, the bytes on stdin, was
        the one enforced mode could not refuse.

        Why the whole C-01 class missed it: every test there calls
        self.decide() directly, which is downstream of the defect. This one
        runs the real PROCESS, because the process is what a runtime invokes.
        A test that enters below the entry point cannot see a bug in the
        entry point.

        Reproduced before the fix:
            printf 'not json' | BM_FENCE_MODE=enforced python3 tools/bm_fence_hook.py
        printed FAILING OPEN and wrote nothing to stdout, exactly as the
        default mode did.
        """
        env = _clean_env()
        env["BM_FENCE_MODE"] = "enforced"
        for raw in ("", "   ", "not json", "{", "[1,2,3"):
            r = subprocess.run([sys.executable, HOOK_PATH], input=raw, text=True,
                               capture_output=True, cwd=self.root, env=env)
            self.assertEqual(r.returncode, 0, raw)
            self.assertTrue(
                r.stdout.strip(),
                "enforced mode wrote no decision for %r, so the write would "
                "have been allowed through an unreadable payload" % (raw,))
            decision = json.loads(r.stdout)
            out = decision["hookSpecificOutput"]
            self.assertEqual(out["hookEventName"], "PreToolUse", raw)
            self.assertEqual(out["permissionDecision"], "deny", raw)
            self.assertIn("enforced mode", out["permissionDecisionReason"], raw)
            self.assertIn("FAILING CLOSED", r.stderr, raw)
            # The model-visible reason stays literal: nothing has been
            # verified at this point, so it must name no path.
            self.assertNotIn(self.root, out["permissionDecisionReason"], raw)

    def test_session_label_subcommand_reads_every_documented_source(self):
        want = self.label(self.VICTIM)
        r = subprocess.run([sys.executable, HOOK_PATH, "session-label",
                            "--session-id", self.VICTIM],
                           capture_output=True, text=True, cwd=self.root,
                           env=_clean_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), want)
        r = subprocess.run([sys.executable, HOOK_PATH, "session-label"],
                           capture_output=True, text=True, cwd=self.root,
                           env=_clean_env({"BM_FENCE_SESSION_ID": self.VICTIM}))
        self.assertEqual(r.stdout.strip(), want)
        r = subprocess.run([sys.executable, HOOK_PATH, "session-label"],
                           input=json.dumps({"session_id": self.VICTIM}),
                           text=True, capture_output=True, cwd=self.root,
                           env=_clean_env())
        self.assertEqual(r.stdout.strip(), want)

    def test_session_label_refuses_rather_than_inventing_an_identity(self):
        r = subprocess.run([sys.executable, HOOK_PATH, "session-label"],
                           input="", text=True, capture_output=True,
                           cwd=self.root, env=_clean_env())
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("no session id", r.stderr)

    def test_unknown_subcommand_refuses(self):
        r = subprocess.run([sys.executable, HOOK_PATH, "wat"], input="{}",
                           text=True, capture_output=True, cwd=self.root,
                           env=_clean_env())
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown command", r.stderr)


# ---------------------------------------------------------------------------
# Query verb (C3, the real M11): a command-line answer to "would the fence
# allow this write", pinned to run the SAME decide() the PreToolUse hook
# runs. All four claims below are built with bs.Store / self.claim (the
# store's own API), never by hand-writing store files.
# ---------------------------------------------------------------------------

class QueryVerb(FenceHookBase):

    def run_query(self, path, session_id, env=None):
        return subprocess.run(
            [sys.executable, HOOK_PATH, "query", path,
             "--session-id", session_id],
            capture_output=True, text=True, cwd=self.root,
            env=_clean_env(env))

    def test_a_unclaimed_path_default_mode_is_allowed(self):
        """No claim covers the path at all: the real hook allows it (the
        default-mode common case), and so must the query verb."""
        real_decision, _n = self.decide(
            payload(self.OTHER, self.root,
                    tool_input={"file_path": "src/other.py"}))
        self.assertIsNone(real_decision, "pre-check: the real hook must "
                          "allow this too, or the fixture is wrong")
        r = self.run_query("src/other.py", self.OTHER)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("ALLOW"), r.stdout)

    def test_b_path_claimed_by_another_session_is_refused(self):
        """VICTIM claims src/app.py; OTHER asks whether OTHER may write it.
        The real hook denies (CalibratedOwnership.test_calibrated_2 above);
        the query verb must report the same DENY, not a different verdict
        reached by its own logic."""
        mine = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], mine)
        real_decision, _n = self.decide(payload(self.OTHER, self.root))
        self.assertIsNotNone(real_decision, "pre-check: the real hook must "
                             "deny this too, or the fixture is wrong")
        r = self.run_query("src/app.py", self.OTHER)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertTrue(r.stdout.startswith("DENY"), r.stdout)
        self.assertIn(mine, r.stdout)

    def test_c_path_claimed_by_the_asking_session_is_allowed(self):
        """VICTIM claims src/app.py under its OWN label, then VICTIM asks
        whether VICTIM may write it. Ownership, not mere absence of a
        claim, is what must allow this one."""
        mine = self.label(self.VICTIM)
        self.claim("api", ["src/app.py"], mine)
        real_decision, _n = self.decide(payload(self.VICTIM, self.root))
        self.assertIsNone(real_decision, "pre-check: the real hook must "
                          "allow the owner too, or the fixture is wrong")
        r = self.run_query("src/app.py", self.VICTIM)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("ALLOW"), r.stdout)

    def test_d_unreadable_fence_state_fails_open_with_reason_printed(self):
        """The store is deleted mid-session (same fixture as
        CalibratedFailOpen.test_calibrated_4): default mode fails OPEN, and
        the reason must be printed, not swallowed, same as the real hook's
        stderr contract."""
        self.claim("api", ["src/app.py"], self.label(self.VICTIM))
        for suffix in ("", "-wal", "-shm"):
            p = bs.store_path(self.root) + suffix
            if os.path.exists(p):
                os.remove(p)
        real_decision, real_notes = self.decide(payload(self.OTHER, self.root))
        self.assertIsNone(real_decision, "pre-check: the real hook must "
                          "fail open too, or the fixture is wrong")
        self.assertIn("FAILING OPEN", " ".join(real_notes))
        r = self.run_query("src/app.py", self.OTHER)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("ALLOW"), r.stdout)
        self.assertIn("FAILING OPEN", r.stderr)
        self.assertIn("no store at", r.stderr)


# ---------------------------------------------------------------------------
# Cost: this runs before EVERY edit.
# ---------------------------------------------------------------------------

class Cost(FenceHookBase):
    """FIXED 2026-08-10. This class used to hold one test,
    test_decision_is_cheap_with_a_realistic_fence, which ran decide() fifty
    times and asserted the mean stayed under 50 ms of real wall clock. That
    asserts a fact about the MACHINE, not about the hook: on a laptop running
    several sessions the scheduler alone can push a correct decide() past 50
    ms, and the test goes red while nothing is wrong. The same defect class
    was fixed in tools/test_bm.py's redaction tests (2026-07-31 and
    2026-08-04) and in the doctor forwarding test (b3227ed, 2026-08-08).

    The cheapness that actually matters here is not "fewer than 50 ms", which
    nobody chose and no requirement names. It is that a decision costs the
    SAME no matter how big the fence is, because this gate runs in front of
    every edit the founder makes and the store grows all day. That is a
    deterministic operation count, so it is counted rather than timed: no
    stopwatch, no load sensitivity, no flake. Measured 2026-08-10 with 1, 20
    and 80 active records: one ReadOnlyStore open and one SQL statement,
    identical at all three sizes.

    test_calibrated_a_per_record_store_open_is_caught below is the proof that
    this counting instrument reaches the defect it exists to catch."""

    #: Every count a healthy decision is allowed to spend, whatever the fence
    #: holds. Both are 1 because active_claims() opens the store once and
    #: reads every fenced path in a single SELECT; the per-target work after
    #: that is pure string comparison in bs.paths_overlap.
    EXPECTED = {"store_opens": 1, "sql_statements": 1}

    def _cost_of_one_decision(self, n_records, sabotage=False):
        """Count the store work ONE decide() spends against n active records.

        Instruments the store module the HOOK loaded, which is a different
        module object from this file's `bs`: bm_fence_hook._load_store_module
        execs tools/bm_store.py itself and caches its own copy, so patching
        `bs` here would count nothing. Restored in a finally block, and
        test_cost_instrumentation_is_reverted_afterwards below fails by name
        if that restore ever breaks."""
        mine = self.label(self.VICTIM)
        with bs.Store(self.root, create=False) as store:
            for i in range(n_records):
                store.claim("rec%d" % i, "ephemeral",
                            files=["area%d/*.py" % i], session_id=mine)
        p = payload(self.OTHER, self.root,
                    tool_input={"file_path": "src/app.py"})
        hookbs = fh._load_store_module()
        # One warm decision first: the first call materializes this session's
        # fence token, which is a one-time cost and not part of the steady
        # state this measures.
        self.decide(p)
        counts = {"store_opens": 0, "sql_statements": 0}
        real_exec = hookbs._exec
        real_store = hookbs.ReadOnlyStore
        real_overlap = hookbs.paths_overlap

        def counting_exec(store, sql, *a, **kw):
            counts["sql_statements"] += 1
            return real_exec(store, sql, *a, **kw)

        def counting_store(*a, **kw):
            counts["store_opens"] += 1
            return real_store(*a, **kw)

        def per_record_store_open(rel, path):
            # The reinjected defect: a store touched once per fenced record,
            # inside the loop decide() runs over every active claim.
            opened = counting_store(self.root)
            try:
                return real_overlap(rel, path)
            finally:
                opened.close()

        hookbs._exec = counting_exec
        hookbs.ReadOnlyStore = counting_store
        if sabotage:
            hookbs.paths_overlap = per_record_store_open
        try:
            self.decide(p)
        finally:
            hookbs._exec = real_exec
            hookbs.ReadOnlyStore = real_store
            hookbs.paths_overlap = real_overlap
        return counts

    def test_a_decision_costs_the_same_however_large_the_fence_is(self):
        # The property, stated as the thing that must not happen: adding
        # records to the fence must not add store work to a decision. Twenty
        # records is the realistic fence the old timing test used; one record
        # is the floor, and the counts must be the SAME number, not merely
        # both small.
        one = self._cost_of_one_decision(1)
        self.tearDown()
        self.setUp()
        twenty = self._cost_of_one_decision(20)
        self.assertEqual(one, self.EXPECTED,
                         "a decision against 1 active record spent %r, "
                         "expected %r" % (one, self.EXPECTED))
        self.assertEqual(twenty, one,
                         "a decision against 20 active records spent %r "
                         "where 1 record spent %r; decide() runs in front of "
                         "every edit, so its cost must not grow with the "
                         "fence" % (twenty, one))

    def test_calibrated_a_per_record_store_open_is_caught(self):
        # CALIBRATION for the test above, and the part that is usually
        # skipped: a counter nobody has seen move measures nothing. Reinject
        # the defect the count exists to catch (a store operation per fenced
        # record, the classic way a gate like this turns from constant into
        # linear) and the count must move, at 20 records more than at 1.
        #
        # Measured 2026-08-10: healthy gave 1 store open at 1, 20 and 80
        # records; reinjected gave 2 opens at 1 record and 21 at 20 records.
        # If this ever stops reproducing, the assertion above is no longer
        # attached to anything and is passing for free.
        one = self._cost_of_one_decision(1, sabotage=True)
        self.tearDown()
        self.setUp()
        twenty = self._cost_of_one_decision(20, sabotage=True)
        self.assertNotEqual(
            twenty, one,
            "REINJECTION CHECK: a store open per fenced record must make the "
            "cost of a decision grow with the fence. 1 record spent %r and "
            "20 spent %r; equal counts mean the instrument above cannot see "
            "this defect class at all" % (one, twenty))
        self.assertGreater(
            twenty["store_opens"], one["store_opens"] + 10,
            "REINJECTION CHECK: 20 records must cost far more store opens "
            "than 1 under the reinjected defect; got %r against %r"
            % (twenty, one))

    def test_cost_instrumentation_is_reverted_afterwards(self):
        # The two tests above swap three real symbols on the store module the
        # hook loaded, and restore them in finally blocks. If a restore ever
        # broke, every later fence test in this process would run against a
        # counting wrapper (or worse, the sabotage) and could pass or fail for
        # reasons that have nothing to do with the code under test. Same guard
        # tools/test_bm.py keeps beside its redaction reinjection.
        hookbs = fh._load_store_module()
        for name in ("_exec", "ReadOnlyStore", "paths_overlap"):
            fn = getattr(hookbs, name)
            self.assertNotIn(
                "counting", getattr(fn, "__name__", ""),
                "bm_store.%s is still the test's instrumented wrapper" % name)
            self.assertNotIn(
                "per_record", getattr(fn, "__name__", ""),
                "bm_store.%s is still the reinjected defect" % name)


# ---------------------------------------------------------------------------
# Structural rules this repo enforces on itself.
# ---------------------------------------------------------------------------

class Structural(unittest.TestCase):

    FILES = (HOOK_PATH, os.path.abspath(__file__), DOCS_PATH)

    def test_no_em_or_en_dashes_anywhere(self):
        # Built from code points, never written literally: a test that
        # contained the characters it forbids would fail on itself.
        forbidden = {"em dash": chr(0x2014), "en dash": chr(0x2013)}
        for path in self.FILES:
            if not os.path.exists(path):
                continue
            text = io.open(path, encoding="utf-8").read()
            for i, line in enumerate(text.splitlines(), 1):
                for label, ch in forbidden.items():
                    self.assertNotIn(ch, line, "%s at %s:%d" % (label, path, i))

    def test_stdout_has_exactly_one_writer(self):
        """The decision channel discipline, enforced structurally rather than
        by memory: a bare print() added later would silently corrupt the hook
        protocol by putting non-JSON on stdout."""
        lines = io.open(HOOK_PATH, encoding="utf-8").read().splitlines()
        pat = re.compile(r"\bprint\(|sys\.stdout\.write")
        hits = [i for i, l in enumerate(lines, 1)
                if not l.lstrip().startswith("#") and pat.search(l)]
        self.assertEqual(
            len(hits), 1,
            "expected exactly one stdout writer (inside _out); found %d at "
            "lines %s" % (len(hits), hits))

    def test_docs_hooks_md_exists_and_states_the_residual_limit(self):
        self.assertTrue(os.path.exists(DOCS_PATH),
                        "docs/HOOKS.md must document installation")
        text = io.open(DOCS_PATH, encoding="utf-8").read()
        for fragment in ("PreToolUse", "permissionDecision", "settings.json",
                         "determined local attacker", "bm_store.py"):
            self.assertIn(fragment, text,
                          "docs/HOOKS.md must mention %r" % fragment)
        lower = text.lower()
        # The two claims a reader must not have to infer: the fail-open policy,
        # and the honest residual limit on identity.
        self.assertIn("fail open", lower)
        self.assertIn("any process running as this user can read the token",
                      lower)

    def test_the_hook_never_opens_the_store_writably(self):
        """A gate that runs in front of every edit must have no authority to
        change what it is reading. Constructing bs.Store() would give this
        file the quarantine path, which can MOVE the founder's database on a
        transient disk error; ReadOnlyStore has no route to that code at all.
        Enforced structurally so a later edit cannot reintroduce it quietly."""
        text = io.open(HOOK_PATH, encoding="utf-8").read()
        lines = [l for l in text.splitlines()
                 if not l.lstrip().startswith("#")]
        body = "\n".join(lines)
        self.assertIn("ReadOnlyStore", body)
        self.assertNotIn(".Store(", body,
                         "the fence hook must never construct a writable Store")
        for writer in ("init_project(", "write_state_view(", "_atomic_write_text("):
            self.assertNotIn(writer, body,
                             "the fence hook must not call %s" % writer)


class _LeakCheckingResult(unittest.TextTestResult):
    """Same mechanical stop tools/test_bm_store.py uses: a store opened and
    never closed passes on POSIX and fails on Windows, where an open handle
    blocks the directory removal. The hook opens a ReadOnlyStore on every
    single decision, so this is exactly the file that must not leak."""

    def startTest(self, test):
        bs._TRACK_UNCLOSED = True
        bs._UNCLOSED.clear()
        super(_LeakCheckingResult, self).startTest(test)

    def stopTest(self, test):
        still_open = list(bs._UNCLOSED)
        if still_open:
            paths = sorted(set(getattr(s, "path", "<unknown>") for s in still_open))
            for s in still_open:
                try:
                    s.close()
                except Exception:  # sbe: allow-silent best-effort close of a leaked store right before the leak is reported to the test result as a failure
                    pass
            bs._UNCLOSED.clear()
            self.addFailure(test, (AssertionError, AssertionError(
                "%d store(s) were opened by this test and never closed "
                "(Windows-only failure class). Path(s): %s"
                % (len(still_open), ", ".join(paths))), None))
        super(_LeakCheckingResult, self).stopTest(test)


# ---------------------------------------------------------------------------
# The battery fence (incident 2026-08-17): a live gate lock over this checkout
# refuses tracked-file writes for every session, reports what it cannot read,
# and in advisory mode never lets an unestablishable state block.
# ---------------------------------------------------------------------------

_taspec = importlib.util.spec_from_file_location(
    "bm_test_all", os.path.join(HERE, "test_all.py"))
ta = importlib.util.module_from_spec(_taspec)
_taspec.loader.exec_module(ta)
sys.modules.setdefault("bm_test_all", ta)


class BatteryFenceBase(FenceHookBase):
    """FenceHookBase plus a real git repository (the battery fence judges
    trackedness through git, so the fixture must be able to answer) and a
    gate lock released even when a test fails mid-way. The store deliberately
    holds ZERO claims: the battery refusal must not depend on any fence being
    declared, and every deny asserted below proves that at the same time."""

    def setUp(self):
        FenceHookBase.setUp(self)
        self.lock = None
        for k in ("BM_FENCE_MODE", "BM_FENCE_BATTERY", "CLAUDE_SESSION_ID"):
            os.environ.pop(k, None)
        for args in (["init", "-q", "."],
                     ["config", "user.email", "e@e"],
                     ["config", "user.name", "T"],
                     ["add", "src/app.py"],
                     ["commit", "-qm", "one"]):
            r = subprocess.run(["git", "-C", self.root] + args,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True,
                               env={k: v for k, v in os.environ.items()
                                    if not k.startswith("GIT_")})
            self.assertEqual(r.returncode, 0,
                             "git %s failed in the fixture: %s"
                             % (args, r.stderr))

    def tearDown(self):
        if self.lock:
            ta.release_gate_lock(self.lock, quiet=True)
        os.environ.pop(ta.LOCK_ENV, None)
        FenceHookBase.tearDown(self)

    def tools_dir(self):
        return os.path.join(self.root, "tools")

    def take_lock(self, owner="test_all pid taken-by-test"):
        self.lock = ta.acquire_gate_lock(owner=owner, quiet=True,
                                         tools_dir=self.tools_dir())
        # acquire exports LOCK_ENV for child suites; this process is a test,
        # not a suite runner, so scrub it rather than leak it to other tests.
        os.environ.pop(ta.LOCK_ENV, None)
        return self.lock

    def crashed_lock(self):
        """A lock left by a run that died without releasing: acquired in a
        child that exits through os._exit, so the file survives and its
        recorded pid is provably gone."""
        code = (
            "import importlib.util, os, sys\n"
            "spec = importlib.util.spec_from_file_location('bm_test_all', %r)\n"
            "ta = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(ta)\n"
            "ta.acquire_gate_lock(owner='crashed-gate', quiet=True,\n"
            "                     tools_dir=%r)\n"
            "os._exit(0)\n"
            % (os.path.join(HERE, "test_all.py"), self.tools_dir()))
        r = subprocess.run([sys.executable, "-c", code],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, env=_clean_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        path = ta.lock_path(self.tools_dir())
        self.assertTrue(os.path.exists(path))
        return path

    def edit_payload(self, rel, session):
        return payload(session, self.root, tool_name="Edit",
                       tool_input={"file_path": os.path.join(self.root, rel),
                                   "old_string": "a", "new_string": "b"})


class BatteryFenceRefuses(BatteryFenceBase):

    def test_a_tracked_write_during_a_live_gate_is_denied(self):
        self.take_lock()
        decision, notes = self.decide(
            self.edit_payload("src/app.py", self.OTHER))
        reason = self.assertDenied(decision)
        self.assertIn("battery fence", reason)
        self.assertIn("src/app.py", reason)
        self.assertIn("BM_FENCE_BATTERY", reason)

    def test_the_starting_session_is_not_exempt(self):
        os.environ["BM_FENCE_SESSION_ID"] = self.VICTIM
        self.take_lock(owner="test_all pid 1 sha deadbeef session %s"
                       % self.VICTIM)
        decision, _notes = self.decide(
            self.edit_payload("src/app.py", self.VICTIM))
        reason = self.assertDenied(decision)
        self.assertIn("battery fence", reason)
        self.assertIn("EVERY session", reason)

    def test_an_untracked_write_during_a_live_gate_is_allowed(self):
        self.take_lock()
        decision, _notes = self.decide(
            self.edit_payload("src/other.py", self.OTHER))
        self.assertAllowed(decision)

    def test_the_deny_holds_with_zero_claims_in_the_store(self):
        # The store FenceHookBase builds holds no claims, so without the
        # battery check this payload would fail OPEN through
        # no-active-claims. The deny therefore proves the battery check runs
        # BEFORE the claims load.
        self.take_lock()
        decision, _notes = self.decide(
            self.edit_payload("src/app.py", self.OTHER))
        self.assertDenied(decision)


class BatteryFenceHonesty(BatteryFenceBase):

    def test_a_stale_lock_is_reported_and_ignored(self):
        self.crashed_lock()
        decision, notes = self.decide(
            self.edit_payload("src/app.py", self.OTHER))
        self.assertAllowed(decision)
        self.assertTrue(any("provably gone" in n for n in notes), notes)

    def test_an_unreadable_lock_is_reported_never_silently_ignored(self):
        path = ta.lock_path(self.tools_dir())
        os.mkdir(path)
        try:
            decision, notes = self.decide(
                self.edit_payload("src/app.py", self.OTHER))
            self.assertAllowed(decision)
            self.assertTrue(
                any("could not be read" in n for n in notes), notes)
        finally:
            os.rmdir(path)

    def test_enforced_mode_refuses_what_it_cannot_read(self):
        path = ta.lock_path(self.tools_dir())
        os.mkdir(path)
        os.environ["BM_FENCE_MODE"] = "enforced"
        try:
            decision, _notes = self.decide(
                self.edit_payload("src/app.py", self.OTHER))
            reason = self.assertDenied(decision)
            self.assertIn("enforced mode", reason)
        finally:
            os.rmdir(path)

    def test_the_kill_switch_allows_and_says_so(self):
        self.take_lock()
        os.environ["BM_FENCE_BATTERY"] = "off"
        decision, notes = self.decide(
            self.edit_payload("src/app.py", self.OTHER))
        self.assertAllowed(decision)
        self.assertTrue(any("BM_FENCE_BATTERY" in n for n in notes), notes)


class BatteryTokenRecords(BatteryFenceBase):

    def test_lock_state_reads_what_acquire_wrote(self):
        self.take_lock(owner="test_all pid 1 sha deadbeef session sess-x")
        state, holder = ta.lock_state(ta.lock_path(self.tools_dir()))
        self.assertEqual(state, "live")
        self.assertEqual(int(holder.split()[0]), os.getpid())
        self.assertIn("sha deadbeef", holder)
        self.assertIn("session sess-x", holder)

    def test_battery_session_reads_the_documented_env_order(self):
        os.environ["CLAUDE_SESSION_ID"] = "claude-abc"
        self.assertEqual(ta._battery_session(), "claude-abc")
        os.environ["BM_FENCE_SESSION_ID"] = "fence-def"
        self.assertEqual(ta._battery_session(), "fence-def")
        os.environ.pop("BM_FENCE_SESSION_ID", None)
        os.environ.pop("CLAUDE_SESSION_ID", None)
        self.assertEqual(ta._battery_session(), "unrecorded")

    def test_a_crashed_locks_slot_is_reclaimed_by_the_next_run(self):
        self.crashed_lock()
        self.lock = ta.acquire_gate_lock(owner="next-gate", quiet=True,
                                         timeout=5,
                                         tools_dir=self.tools_dir())
        os.environ.pop(ta.LOCK_ENV, None)
        state, holder = ta.lock_state(self.lock)
        self.assertEqual(state, "live")
        self.assertEqual(int(holder.split()[0]), os.getpid())


if __name__ == "__main__":
    unittest.main(
        verbosity=2,
        testRunner=unittest.TextTestRunner(verbosity=2,
                                           resultclass=_LeakCheckingResult))
