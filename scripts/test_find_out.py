"""What find_out.py must keep true.

Four sources, each provably searchable and each provably NO-DATA when its
store is missing, plus the one sentence attempt_ledger.py's refusal now
carries: the exact command to run instead of a chore.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import find_out as F  # noqa: E402
import pattern_note as P  # noqa: E402
import attempt_ledger as A  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def vault():
    """A temp vault with two failure notes, an index, and LEARNED.md."""
    d = tempfile.mkdtemp()
    _write(os.path.join(d, "40-Failures", "a-suite-went-red-on-a-clean-branch.md"), """---
type: failure
description: "A suite went red and nobody could tell whether the branch caused it"
---

# A suite went red and nobody could tell whether the branch caused it

The suite went red. Nobody could tell whether the branch caused it or the
suite was already broken before anyone touched it.
""")
    _write(os.path.join(d, "40-Failures", "a-tooltip-was-never-drawn.md"), """---
type: failure
description: "A tooltip was built six times and never actually rendered"
---

# A tooltip was built six times and never actually rendered

Six rounds of visual polish went into a cue that was never drawn at all.
""")
    _write(os.path.join(d, "40-Failures", "Failures-Index.md"), """# Failures Index

- [[a-suite-went-red-on-a-clean-branch]] 2026-09-01. The suite went red.
- [[a-tooltip-was-never-drawn]] 2026-08-17. A tooltip never rendered.
""")
    _write(os.path.join(d, "LEARNED.md"), """# LEARNED

## Laws

    LESSON: a suite that goes red on an unchanged branch is not your fault
    RULE:   run the suite on unchanged main before blaming your branch
    BECAUSE: three sessions blamed themselves for a base that was already red

    LESSON: a tooltip that is never drawn cannot be made more visible
    RULE:   check whether the element renders at all before tuning its style
    BECAUSE: three builds tuned visibility on an element that was never drawn
""")
    os.makedirs(os.path.join(d, P.FOLDER), exist_ok=True)
    return d


def pattern_store():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, P.FOLDER))
    P.write("Judge a branch against unchanged main",
            "The suite is red and I cannot tell whether my branch caused it",
            "run the suite on unchanged main first", "seen three times", vault=d)
    return d


def memory_file():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "MEMORY.md")
    _write(path, """# Memory index
- [A suite result is bound to the tree it ran on](a-gate-result-outlives-the-tree-it-ran-on.md) - a rebase can change a gate's own code, not only its input.
- [A watchdog dies with its session](a-watchdog-dies-with-its-session.md) - CronCreate and Monitor end silently with their session.
""")
    return path


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = F.main(argv)
    return code, out.getvalue()


class VaultFailuresAreFindableByTheProblem(unittest.TestCase):
    def test_a_shared_wording_query_ranks_the_matching_note_first(self):
        v = vault()
        hits = F.vault_failures(
            F._words("the suite is red and I cannot tell if it is my branch"), v)
        self.assertTrue(hits)
        self.assertIn("a-suite-went-red-on-a-clean-branch", hits[0][1])

    def test_a_missing_vault_folder_is_None_not_empty(self):
        self.assertIsNone(F.vault_failures(["anything"], "/no/such/vault"))


class LearnedBlocksAreFindableByTheProblem(unittest.TestCase):
    def test_a_shared_wording_query_finds_the_right_block(self):
        v = vault()
        hits = F.vault_learned(F._words("the suite is red and my branch"), v)
        self.assertTrue(hits)
        self.assertIn("suite that goes red", hits[0][2])

    def test_a_missing_LEARNED_file_is_None(self):
        self.assertIsNone(F.vault_learned(["anything"], "/no/such/vault"))


class PatternsAreFoundByPatternNoteItself(unittest.TestCase):
    def test_the_pattern_is_found_by_its_problem(self):
        d = pattern_store()
        hits = F.patterns("the suite is red and I cannot tell whether my branch caused it", d)
        self.assertTrue(hits)
        self.assertIn("judge-a-branch-against-unchanged-main", hits[0][1])

    def test_a_missing_patterns_folder_is_None(self):
        self.assertIsNone(F.patterns("anything", "/no/such/vault"))


class MemoryIndexIsFindableByTheProblem(unittest.TestCase):
    def test_a_shared_wording_query_finds_the_matching_line(self):
        m = memory_file()
        hits = F.memory_index(F._words("the tree it ran a gate result"), m)
        self.assertTrue(hits)
        self.assertIn("A suite result is bound to the tree it ran on", hits[0][2])

    def test_a_missing_memory_file_is_None(self):
        self.assertIsNone(F.memory_index(["anything"], "/no/such/file.md"))


class TheFourSourceRun(unittest.TestCase):
    def test_a_missing_vault_prints_NO_DATA_while_others_still_answer(self):
        pd = pattern_store()
        m = memory_file()
        code, out = run_main([
            "the suite is red and I cannot tell whether my branch caused it",
            "--vault", "/no/such/vault",
            "--patterns", pd,
            "--memory", m,
        ])
        self.assertEqual(code, 0)
        self.assertIn("NO-DATA: vault failures not found at", out)
        self.assertIn("NO-DATA: vault learned not found at", out)
        self.assertNotIn("NO-DATA: patterns", out)
        self.assertNotIn("NO-DATA: memory index", out)

    def test_all_sources_missing_prints_four_NO_DATA_lines_and_exits_2(self):
        code, out = run_main([
            "anything at all",
            "--vault", "/no/such/vault",
            "--patterns", "/no/such/patterns",
            "--memory", "/no/such/memory.md",
        ])
        self.assertEqual(code, 2)
        self.assertEqual(out.count("NO-DATA:"), 4)
        self.assertIn("0 of 4 source(s) answered.", out)

    def test_a_real_run_across_all_four_sources_answers_and_exits_0(self):
        v = vault()
        _write(os.path.join(v, P.FOLDER, "seed.md"), "seed\n")  # keep folder non-empty, harmless
        P.write("Judge a branch against unchanged main",
                "The suite is red and I cannot tell whether my branch caused it",
                "run the suite on unchanged main first", "seen three times", vault=v)
        m = memory_file()
        code, out = run_main([
            "the suite is red and I cannot tell whether my branch caused it",
            "--vault", v, "--patterns", v, "--memory", m,
        ])
        self.assertEqual(code, 0)
        self.assertIn("4 of 4 source(s) answered.", out)


class TheRefusalNamesTheExactCommand(unittest.TestCase):
    """The 'go and find out' branch used to be prose. Driven in a temp root:
    three failing records at the two-strike limit produce a refusal that
    names the exact command, with the class's problem substituted."""

    def test_the_refusal_text_contains_the_find_out_command_with_the_problem(self):
        d = tempfile.mkdtemp()
        store = os.path.join(d, "attempts.jsonl")
        problem = "the room cue is invisible"
        klass = "light on the painting"
        A.record(problem, klass, "failed", store=store)
        A.record(problem, klass, "failed", store=store)
        A.record(problem, klass, "failed", store=store)
        rows = A.read(store)
        verdict, reason = A.check(rows, problem, klass)
        self.assertEqual(verdict, A.REFUSE)
        self.assertIn("python3 scripts/find_out.py %r" % problem, reason)


if __name__ == "__main__":
    unittest.main()
