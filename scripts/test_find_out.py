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


def _load_products_copy():
    """The shipped copy under products/brothermode/tools, loaded by path, so
    --file is proven on both copies of find_out.py, not only this one."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "products", "brothermode", "tools", "find_out.py")
    spec = importlib.util.spec_from_file_location("find_out_products", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FileLookupReadsAnchorsOnly(unittest.TestCase):
    """2026-10-04: find_out.py --file answers from 40-Failures frontmatter
    applies_to anchors only, one line per lesson. One fixture per guard."""

    MODULES = (F, _load_products_copy())

    def setUp(self):
        self.vault = tempfile.mkdtemp()
        folder = os.path.join(self.vault, "40-Failures")
        _write(os.path.join(folder, "anchored.md"),
               '---\ntype: failure\nsymptom: "it broke"\n'
               'applies_to: [scripts/loop/proof_accept.py, scripts/x.py]\n---\n\nbody\n')
        # Names the file in its BODY only: a full text search would find it,
        # an anchors-only lookup must not.
        _write(os.path.join(folder, "body-only.md"),
               '---\ntype: failure\n---\n\nscripts/loop/proof_accept.py broke\n')
        _write(os.path.join(folder, "other.md"),
               '---\ntype: failure\napplies_to: [scripts/other.py]\n---\n')
        # No frontmatter at all: an applies_to line in the text is not one.
        _write(os.path.join(folder, "nofm.md"),
               'intro\napplies_to: [scripts/loop/proof_accept.py]\n---\n')
        _write(os.path.join(folder, "waived.md"),
               '---\ntype: failure\napplies_to: []\n---\n')

    def run_main(self, mod, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = mod.main(argv)
        return code, buf.getvalue()

    def test_one_line_per_anchored_lesson_and_body_mentions_ignored(self):
        for mod in self.MODULES:
            code, out = self.run_main(mod, ["--file", "scripts/loop/proof_accept.py",
                                            "--vault", self.vault])
            self.assertEqual(code, 0)
            self.assertEqual(out.strip().splitlines(), [
                "[[anchored]]  applies_to: scripts/loop/proof_accept.py, "
                "scripts/x.py  symptom: it broke"])

    def test_a_bare_file_name_matches_the_anchor_path_suffix(self):
        for mod in self.MODULES:
            code, out = self.run_main(mod, ["--file", "proof_accept.py", "--vault", self.vault])
            self.assertEqual(code, 0)
            self.assertIn("[[anchored]]", out)

    def test_a_partial_name_is_not_a_match(self):
        for mod in self.MODULES:
            code, out = self.run_main(mod, ["--file", "accept.py", "--vault", self.vault])
            self.assertEqual(code, 1)
            self.assertIn("no lesson", out)

    def test_an_unanchored_file_exits_1(self):
        for mod in self.MODULES:
            code, _ = self.run_main(mod, ["--file", "scripts/none.py", "--vault", self.vault])
            self.assertEqual(code, 1)

    def test_a_missing_folder_is_NO_DATA_exit_2(self):
        for mod in self.MODULES:
            code, out = self.run_main(mod, ["--file", "x.py", "--vault",
                                            os.path.join(self.vault, "nope")])
            self.assertEqual(code, 2)
            self.assertIn("NO-DATA", out)

    def test_a_longer_given_path_matches_the_anchor(self):
        for mod in self.MODULES:
            code, out = self.run_main(mod, ["--file", "/r/scripts/loop/proof_accept.py",
                                            "--vault", self.vault])
            self.assertEqual(code, 0)
            self.assertIn("[[anchored]]", out)

    def test_names_file_rules(self):
        for mod in self.MODULES:
            nf = mod._names_file
            self.assertTrue(nf("./scripts/x.py", "scripts/x.py"))
            self.assertTrue(nf("scripts/x.py", "./scripts/x.py"))
            self.assertFalse(nf("python3 scripts/x.py", "scripts/x.py"))
            self.assertFalse(nf("python3 /r/scripts/x.py", "scripts/x.py"))
            self.assertFalse(nf("x.py", "scripts/ax.py"))
            self.assertFalse(nf("scripts/", ""))

    def test_the_body_is_never_read(self):
        folder = os.path.join(self.vault, "40-Failures")
        for mod in self.MODULES:
            self.assertNotIn("body", mod._frontmatter_only(os.path.join(folder, "anchored.md")))


if __name__ == "__main__":
    unittest.main()
