#!/usr/bin/env python3
"""Regression tests for `sbe_score._is_live_fence`, the fence-liveness rule the
PreToolUse hook (`sbe_fence_hook.is_live_fence`) loads by path so the two
consumers can never spell the rule two different ways.

Run: python3 tools/test_sbe_liveness_rule.py

THE DEFECT THIS FILE PINS. The old rule called a bullet line a live fence when
it started with "- " or "* ", contained the substring "agent" case-insensitively
anywhere in the line, and carried no LANDED or ADOPTED marker. Measured end to
end, that rule got both directions wrong:

  MISSED a real fence that names its writer as "SOLE WRITER" and never spells
  the word "agent" at all, so a foreign session's write into its declared
  scope was ALLOWED through the hook. A missed fence is an unprotected file,
  the dangerous direction.

  FALSE-POSITIVED on ordinary prose that merely contains the substring
  "agent": a closure note reporting a fence was already closed, and an aside
  mentioning "a three-agent fleet". Both read as live and produced a fence
  hygiene warning on every write; six such lines in one real registry meant
  six warnings per write, the volume at which a reader stops reading them.

THE FIX. Liveness now keys on FENCE GRAMMAR: a live fence is a markdown bullet
that DECLARES ownership through one of STATE.template.md's own markers (a
`files:` scope, an `owner-session:` field, the `agent: <id>` FIELD form, or the
`sole writer` phrase), not on whether the word "agent" shows up anywhere. See
`sbe_score._is_live_fence` for the rule itself.

BOTH CONSUMERS MUST AGREE. `sbe_fence_hook.is_live_fence` loads
`sbe_score._is_live_fence` by path rather than holding a private copy (see
`sbe_fence_hook.live_fence_rule`'s own docstring), so the two are the SAME
function at runtime. `TestBothConsumersAgree` below still asserts the verdicts
match across a shared corpus, because "the two are supposed to be the same
function" is exactly the kind of claim that should be proven, not assumed: a
future edit could import a stale copy, or add a second call site that types
the rule again, and this test is what would catch it.
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
SCORE_PATH = os.path.join(HERE, "sbe_score.py")
HOOK_PATH = os.path.join(HERE, "sbe_fence_hook.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules[name] = mod
    return mod


score = _load("sbe_score_for_liveness_test", SCORE_PATH)
fh = _load("sbe_fence_hook_for_liveness_test", HOOK_PATH)


def write(path, text):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# The shared corpus. Every line the task's measured defect named, plus the
# regression guards the task asked for by name. `expected` is what a fence
# grammar reader should say, independent of either module's own code.
# ---------------------------------------------------------------------------

#: Case 1 from the defect report, verbatim: a real fence with an explicit
#: `files:` scope and a `SOLE WRITER` declaration that never spells "agent",
#: which the old substring rule MISSED (measured end to end: a foreign
#: session's write into src/app.py was ALLOWED).
DEFECT_1_MISSED_FENCE = (
    "- finish-and-ship (main session, Fable 5, SOLE WRITER of tracked files): "
    "the whole working tree. Tier T3. TTL 24h. files: src/app.py |"
)

#: Case 2, verbatim: a closure note reporting a fence was already closed. The
#: old rule read it live because it contains "agent" as a bare word; it
#: declares nothing (no `files:`, no `owner-session:`, no `agent:` field, no
#: "sole writer" phrase) so the new rule reads it correctly as prose.
DEFECT_2_CLOSURE_NOTE = (
    "- docs-refresh agent fence closed clean (only its two files, suite green)."
)

#: Case 3, verbatim: an incidental aside mentioning a fleet size. The old rule
#: read it live off the "agent" inside "three-agent"; the new rule requires a
#: colon-terminated `agent:` field, which this line never has.
DEFECT_3_INCIDENTAL_MENTION = (
    "- Three upheld Majors fixed by a three-agent fleet in 13272f2 (rehearsal"
)

#: A genuine fence written exactly as STATE.template.md's grammar prescribes:
#: `agent: <id> (sole writer, session <id>) | ...`. Regression guard: the new,
#: narrower-for-prose rule must not have become narrower for a real fence too.
TEMPLATE_FENCE = (
    "- agent: doc-writer (sole writer, session abcd1234) | tier T1 | "
    "TTL 2026-12-31 |"
)

#: A fence declared entirely through `owner-session:`, with no `agent:` field
#: and no "sole writer" phrase at all: the marker the task named by name.
OWNER_SESSION_FENCE = (
    "- rotation-9 (abc, v2) owner-session: sess-999, objective: rotate keys |"
)

#: A template-form fence closed with LANDED. LANDED must still close it.
LANDED_LINE = (
    "- agent: orchestrator (sole writer, session abc123) | tier T1 | "
    "TTL 2026-01-15 | files: src/parser.py | LANDED 2026-01-15, "
    "evidence: tests passed"
)

#: A template-form fence closed with ADOPTED. ADOPTED must still close it.
ADOPTED_LINE = (
    "- agent: doc-writer (sole writer, session xyz) | tier T1 | "
    "files: docs/SETUP.md | ADOPTED 2026-07-28"
)

#: An ordinary bullet that declares nothing at all: no field, no phrase, and
#: (unlike the three defect lines) not even the word "agent".
PLAIN_PROSE_BULLET = "- Remember to update the README before the next release."

#: Carries both "agent" and "files:" as words, but never starts a bullet, so
#: it is not a fence line under any reading of the grammar.
NON_BULLET_LINE = (
    "This paragraph mentions an agent and files: casually but starts no "
    "bullet at all."
)

CORPUS = (
    ("defect_1_missed_fence", DEFECT_1_MISSED_FENCE, True),
    ("defect_2_closure_note", DEFECT_2_CLOSURE_NOTE, False),
    ("defect_3_incidental_mention", DEFECT_3_INCIDENTAL_MENTION, False),
    ("template_fence", TEMPLATE_FENCE, True),
    ("owner_session_fence", OWNER_SESSION_FENCE, True),
    ("landed_line", LANDED_LINE, False),
    ("adopted_line", ADOPTED_LINE, False),
    ("plain_prose_bullet", PLAIN_PROSE_BULLET, False),
    ("non_bullet_line", NON_BULLET_LINE, False),
)


class TestLivenessRuleOnTheCorpus(unittest.TestCase):
    """`sbe_score._is_live_fence` returns the expected verdict on every line
    in CORPUS, one assertion per line so a failure names which one broke."""

    def test_defect_1_missed_fence_is_now_live(self):
        self.assertTrue(
            score._is_live_fence(DEFECT_1_MISSED_FENCE),
            "a real fence declared with `files:` and SOLE WRITER, never "
            "spelling the word agent, must be read as live")

    def test_defect_2_closure_note_is_not_live(self):
        self.assertFalse(
            score._is_live_fence(DEFECT_2_CLOSURE_NOTE),
            "a closure note reporting a fence was already closed must not "
            "read as a new live fence merely for containing the word agent")

    def test_defect_3_incidental_mention_is_not_live(self):
        self.assertFalse(
            score._is_live_fence(DEFECT_3_INCIDENTAL_MENTION),
            "an aside mentioning a three-agent fleet must not read as live "
            "off the substring agent inside three-agent")

    def test_template_form_fence_is_still_live(self):
        self.assertTrue(
            score._is_live_fence(TEMPLATE_FENCE),
            "a genuine STATE.template.md-shaped fence must still be live")

    def test_owner_session_field_alone_declares_a_live_fence(self):
        self.assertTrue(
            score._is_live_fence(OWNER_SESSION_FENCE),
            "an owner-session: field is one of the declared markers on its own")

    def test_landed_still_closes_a_fence(self):
        self.assertFalse(score._is_live_fence(LANDED_LINE))

    def test_adopted_still_closes_a_fence(self):
        self.assertFalse(score._is_live_fence(ADOPTED_LINE))

    def test_a_plain_prose_bullet_is_not_live(self):
        self.assertFalse(score._is_live_fence(PLAIN_PROSE_BULLET))

    def test_a_non_bullet_line_is_not_live(self):
        self.assertFalse(score._is_live_fence(NON_BULLET_LINE))


class TestBothConsumersAgree(unittest.TestCase):
    """`sbe_fence_hook.is_live_fence` loads `sbe_score._is_live_fence` by path
    (see `sbe_fence_hook.live_fence_rule`) so the two are the same function at
    runtime; this proves it over the shared corpus rather than assuming it."""

    def test_the_hook_and_the_scorer_agree_on_every_corpus_line(self):
        mismatches = []
        for name, line, expected in CORPUS:
            score_verdict = score._is_live_fence(line)
            hook_verdict = fh.is_live_fence(line)
            if score_verdict != hook_verdict or score_verdict != expected:
                mismatches.append(
                    "%s: score=%r hook=%r expected=%r"
                    % (name, score_verdict, hook_verdict, expected))
        self.assertEqual(
            mismatches, [],
            "the hook and the scorer disagreed, or one disagreed with the "
            "expected fence-grammar verdict: %s" % "; ".join(mismatches))


# ---------------------------------------------------------------------------
# End to end through the real hook: a subprocess, JSON on stdin, exactly as
# Claude Code invokes it. Mirrors tools/test_sbe_fence_hook.py's own
# TestWireProtocol.run_hook, kept local so this file stays runnable standalone.
# ---------------------------------------------------------------------------

class HookCase(unittest.TestCase):
    ENV_KEYS = ("BROTHERSBE_REGISTRIES", "BROTHERSBE_FENCE_SESSION",
                "BROTHERSBE_FENCE_HOOK_OFF", "CLAUDE_CONFIG_DIR")

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "src"))
        os.makedirs(os.path.join(self.root, "docs"))
        write(os.path.join(self.root, "src", "app.py"), "app\n")
        write(os.path.join(self.root, "docs", "SETUP.md"), "setup\n")
        self.registry = os.path.join(self.root, "STATE.md")
        # Hermetic: companion detection reads CLAUDE_CONFIG_DIR off the real
        # filesystem otherwise, and this whole file's verdicts would then
        # depend on whether the machine running it happens to have a
        # companion fence hook installed.
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.root, "claude-config")

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def run_hook(self, target, session="intruder-session-9999"):
        payload = {"tool_name": "Write",
                   "tool_input": {"file_path": target, "content": "x"},
                   "session_id": session,
                   "cwd": self.root,
                   "project_dir": self.root}
        env = dict(os.environ)
        env.pop("BROTHERSBE_REGISTRIES", None)
        return subprocess.run(
            [sys.executable, HOOK_PATH], input=json.dumps(payload),
            capture_output=True, text=True, timeout=60, env=env, cwd=self.root)


class TestFinishAndShipFenceIsEnforced(HookCase):
    """RED before this fix: the finish-and-ship line never spells the word
    "agent", so the old substring rule did not read it as a live fence at
    all, `fence_files` was never even asked about it, and a foreign session's
    Write into src/app.py was ALLOWED. GREEN after: the `files:` scope and the
    SOLE WRITER declaration are enough to make it live, and the write into the
    declared file is DENIED."""

    def test_a_foreign_session_editing_the_declared_file_is_denied(self):
        write(self.registry, "# STATE\n\n## Fence registry\n\n"
                             + DEFECT_1_MISSED_FENCE + "\n\n## Decisions\n")
        r = self.run_hook(os.path.join(self.root, "src", "app.py"))
        self.assertEqual(r.returncode, 0)
        obj = json.loads(r.stdout)
        out = obj["hookSpecificOutput"]
        self.assertEqual(
            out["permissionDecision"], "deny",
            "a foreign session's write into the finish-and-ship fence's own "
            "declared file must be refused; stderr: %s" % r.stderr)
        self.assertIn("src/app.py", out["permissionDecisionReason"])


class TestClosureNoteCreatesNoPhantomFence(HookCase):
    """RED before this fix: the closure note reads as live off the bare word
    "agent", so it becomes a phantom fence with no readable `files:` scope,
    producing a "did NOT enforce it" warning on stderr for every write in the
    project, real conflict or not. GREEN after: the line declares nothing, so
    it is not a fence at all and produces neither a warning nor an
    enforcement."""

    def test_no_phantom_fence_warning_and_the_unrelated_write_is_allowed(self):
        write(self.registry, "# STATE\n\n## Fence registry\n\n"
                             + DEFECT_2_CLOSURE_NOTE + "\n\n## Decisions\n")
        r = self.run_hook(os.path.join(self.root, "docs", "SETUP.md"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "",
                         "an allow writes nothing to stdout; the closure "
                         "note must not have produced a deny")
        self.assertNotIn(
            "no readable `files:` scope", r.stderr,
            "the closure note must not be read as a live fence with an "
            "unreadable scope; it must not be read as a fence at all")


class TestIncidentalMentionCreatesNoPhantomFence(HookCase):
    """RED before this fix: "three-agent" contains the substring "agent", so
    this aside also became a phantom fence and warned on every write. GREEN
    after: no colon-terminated field, no declaration, no fence."""

    def test_no_phantom_fence_warning_and_the_unrelated_write_is_allowed(self):
        write(self.registry, "# STATE\n\n## Fence registry\n\n"
                             + DEFECT_3_INCIDENTAL_MENTION + "\n\n## Decisions\n")
        r = self.run_hook(os.path.join(self.root, "docs", "SETUP.md"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertNotIn("no readable `files:` scope", r.stderr)


class TestTemplateFormFenceStillEnforced(HookCase):
    """Regression guard: the rule must be no narrower for a real,
    template-shaped fence than it was before this fix."""

    def test_a_foreign_session_is_still_denied(self):
        write(self.registry, "# STATE\n\n## Fence registry\n\n"
                             + TEMPLATE_FENCE.replace(
                                 "TTL 2026-12-31 |",
                                 "TTL 2026-12-31 | files: docs/SETUP.md |")
                             + "\n\n## Decisions\n")
        r = self.run_hook(os.path.join(self.root, "docs", "SETUP.md"))
        self.assertEqual(r.returncode, 0)
        obj = json.loads(r.stdout)
        self.assertEqual(obj["hookSpecificOutput"]["permissionDecision"], "deny")


class TestLandedAndAdoptedStillClose(HookCase):

    def _fence(self, files="docs/SETUP.md"):
        return ("- agent: doc-writer (sole writer, session owner-1) | tier T1 | "
                "TTL 2026-12-31 | files: %s |" % files)

    def test_landed_closes_the_fence(self):
        write(self.registry, "# STATE\n\n## Fence registry\n\n"
                             + self._fence()
                             + " LANDED 2026-07-28, evidence: tests passed\n"
                             + "\n## Decisions\n")
        r = self.run_hook(os.path.join(self.root, "docs", "SETUP.md"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "", "a LANDED fence must not deny; stderr: %s" % r.stderr)

    def test_adopted_closes_the_fence(self):
        write(self.registry, "# STATE\n\n## Fence registry\n\n"
                             + self._fence()
                             + " ADOPTED 2026-07-28\n"
                             + "\n## Decisions\n")
        r = self.run_hook(os.path.join(self.root, "docs", "SETUP.md"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "", "an ADOPTED fence must not deny; stderr: %s" % r.stderr)


class TestScorerFenceHygieneOverTheCorpus(unittest.TestCase):
    """The scorer's own fence-hygiene check, run over a registry containing
    the whole corpus, still reports sensibly: it counts only the lines that
    actually declare ownership, and does not fold the prose lines in."""

    def test_check_fence_hygiene_counts_only_the_declaring_lines(self):
        with tempfile.TemporaryDirectory() as d:
            registry = os.path.join(d, "STATE.md")
            body = "# STATE\n\n## Fence registry\n\n" + "\n".join(
                line for _, line, _ in CORPUS) + "\n\n## Decisions\n"
            write(registry, body)
            # check_fence_hygiene(ctx) reads ctx.registries / ctx.registry_denials,
            # which score.Ctx() serves off these two module globals (normally
            # populated from BROTHERSBE_REGISTRIES at import time). Setting them
            # directly exercises the real Ctx/check pairing without re-importing
            # the module or shelling out to the CLI.
            old_registries, old_denials = score.REGISTRIES, score.REGISTRY_DENIALS
            score.REGISTRIES, score.REGISTRY_DENIALS = [registry], []
            try:
                verdict, detail = score.check_fence_hygiene(score.Ctx())
            finally:
                score.REGISTRIES, score.REGISTRY_DENIALS = old_registries, old_denials
            # Exactly the lines expected live (defect_1, template_fence,
            # owner_session_fence) declare ownership; none is older than 2
            # days (the registry was just written), so the verdict is PASS.
            live_count = sum(1 for _, _, expected in CORPUS if expected)
            self.assertEqual(verdict, "PASS", detail)
            self.assertIn("%d fence line(s)" % live_count, detail,
                         "check_fence_hygiene must count exactly the "
                         "declaring lines, not the prose ones: %s" % detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
