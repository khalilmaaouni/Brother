#!/usr/bin/env python3
"""Generalization proof for JBEQ-MDM decision-rules-addendum.md.

WHY THIS FILE EXISTS. Two blind rounds against the frozen JBEQ-MDM seed left
the rules arm making the same three kinds of mistake (one of which recurred
across both rounds): reading a shared address as a relation, reading weak
match evidence as a refuting fact, and reading an operational split as
erasing a relation. `benchmarks/jbeq/mdm/decision-rules-addendum.md` adds
three general boundary rules (5 to 7) to close those gaps, and this file
proves them against 9 INVENTED cases in
`benchmarks/jbeq/mdm/generalization-cases-2026-09-05.json`, none of which is
in the frozen seed and none of which the addendum names by id.

WHAT THIS DOES NOT DO. It never touches `seed-2026-09-05.json`, never edits
an expected answer, and never reruns the frozen seed. `scripts/jbeq_mdm.py`
is reused exactly as written, pointed at the generalization file with its own
`--seed` flag: the harness needs no case-specific branch to score a case
outside the seed it shipped with.

WHAT "PROVES" MEANS HERE. Three checks: the generalization file is shaped
like a seed the harness can actually read (schema check); `jbeq_mdm.py
prompts` can write a blind prompt per invented case with the same leak
controls as the frozen seed (no expected answer, no rationale, no critical
flag); and `jbeq_mdm.py score` reads a gold answer file built from the
file's own expected values and reports 0 critical failures at exit 0, which
is the mechanical floor a rules-arm run must also clear. A blind model run
against these prompts is a SEPARATE, heavier proof this file does not
attempt; its result, if one exists, is reported beside this file's own
output rather than folded into it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jbeq_mdm  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "jbeq_mdm.py")
GEN_PATH = os.path.join(
    REPO, "benchmarks", "jbeq", "mdm", "generalization-cases-2026-09-05.json")
GEN2_PATH = os.path.join(
    REPO, "benchmarks", "jbeq", "mdm",
    "generalization-cases-2026-09-05-rules-8-to-11.json")
ADDENDUM_PATH = os.path.join(
    REPO, "benchmarks", "jbeq", "mdm", "decision-rules-addendum.md")
SEED_PATH = os.path.join(
    REPO, "benchmarks", "jbeq", "mdm", "seed-2026-09-05.json")

EXPECTED_MIX = {"entity-object": 3, "match-or-no-merge": 3, "hierarchy": 3}
EXPECTED_MIX2 = {"address": 1, "identifier": 3, "match-or-no-merge": 2,
                 "hierarchy": 2}


def load_generalization():
    with open(GEN_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def load_generalization2():
    with open(GEN2_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def load_seed():
    with open(SEED_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def run(*args):
    """Run jbeq_mdm.py and return (exit code, stdout+stderr).

    The exit code is read off the completed process, never after a pipe.
    """
    proc = subprocess.run([sys.executable, SCRIPT] + list(args),
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


class GeneralizationFileShape(unittest.TestCase):
    def setUp(self):
        self.gen = load_generalization()

    def test_nine_invented_cases_in_the_declared_mix(self):
        cases = self.gen["cases"]
        self.assertEqual(len(cases), 9, "3 cases per addendum rule, 3 rules")
        counted = {}
        for case in cases:
            counted[case["track"]] = counted.get(case["track"], 0) + 1
        self.assertEqual(counted, EXPECTED_MIX)
        self.assertEqual(self.gen["mix"], EXPECTED_MIX)

    def test_not_frozen(self):
        self.assertFalse(self.gen["frozen"],
                         "the generalization file must not claim to be frozen")

    def test_case_ids_are_unique_and_outside_the_seed(self):
        gen_ids = [c["id"] for c in self.gen["cases"]]
        self.assertEqual(len(set(gen_ids)), len(gen_ids))
        seed_ids = set(c["id"] for c in load_seed()["cases"])
        overlap = seed_ids.intersection(gen_ids)
        self.assertFalse(overlap, "invented case ids must never reuse a seed id")

    def test_every_expected_answer_is_in_its_allowed_set(self):
        for case in self.gen["cases"]:
            self.assertIn(case["expected"], case["allowed"], case["id"])

    def test_every_critical_case_names_a_declared_critical_class(self):
        classes = set(self.gen["critical_classes"])
        self.assertTrue(classes)
        for case in self.gen["cases"]:
            self.assertTrue(case["critical"], case["id"])
            self.assertIn(case["critical_class"], classes, case["id"])

    def test_every_case_carries_a_japanese_input_question_and_rationale(self):
        for case in self.gen["cases"]:
            for field in ("input", "question", "rationale_ja"):
                self.assertTrue(case[field].strip(), "%s %s" % (case["id"], field))

    def test_no_invented_case_text_reuses_a_seed_case_text(self):
        # A generalization proof that quietly reused the seed's own inputs
        # would not be "outside the frozen seed" as asked; it would just be
        # the same fixture under a new case id.
        seed_text = json.dumps(load_seed()["cases"], ensure_ascii=False)
        for case in self.gen["cases"]:
            self.assertNotIn(case["input"], seed_text, case["id"])


class AddendumDocument(unittest.TestCase):
    def setUp(self):
        with open(ADDENDUM_PATH, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_three_new_boundary_rules_present(self):
        for n in ("5.", "6.", "7."):
            self.assertIn(n, self.text)

    def test_four_more_boundary_rules_present(self):
        for n in ("8.", "9.", "10.", "11."):
            self.assertIn(n, self.text)

    def test_no_dashes(self):
        # Code points, never literal characters: a source file that spells an
        # em or en dash to check for its absence trips the same push-time
        # dash scan this test exists to satisfy.
        for cp in (0x2014, 0x2013):
            self.assertNotIn(chr(cp), self.text, "no em or en dashes in the addendum")

    def test_no_unresolved_conflict_markers(self):
        # C1 (2026-09-06, qa-review-jbeq-2026-09-06.md): a merge once left
        # <<<<<<< / ======= / >>>>>>> in this exact file, with two rules
        # both numbered 13 and neither side resolved, while this suite
        # still read the file and passed. The document is the extractor's
        # and the operator's contract; it must be plain text, not a diff.
        for marker in ("<<<<<<<", "=======", ">>>>>>>"):
            self.assertNotIn(
                marker, self.text,
                "unresolved conflict marker %r in decision-rules-"
                "addendum.md" % marker,
            )

    def test_numbered_top_level_items_are_unique(self):
        # C1: a conflict that leaves two rules both numbered 13 (or drops
        # a number, or duplicates one without a conflict marker at all) is
        # unreadable to the human operator this document is written for.
        import re

        numbers = re.findall(r"^(\d+)\. ", self.text, flags=re.MULTILINE)
        self.assertTrue(numbers, "no numbered top-level item found; the "
                        "addendum's shape changed under this regex")
        seen = set()
        duplicates = set()
        for n in numbers:
            if n in seen:
                duplicates.add(n)
            seen.add(n)
        self.assertEqual(
            duplicates, set(),
            "numbered item(s) appearing more than once in decision-rules-"
            "addendum.md: %s" % sorted(duplicates, key=int),
        )
        # On this base, items 13 and 14 each appear exactly once.
        self.assertIn("13", numbers)
        self.assertIn("14", numbers)
        self.assertEqual(numbers.count("13"), 1)
        self.assertEqual(numbers.count("14"), 1)

    # The founder's own forbidden clause (no rule may name a seed case id,
    # phrase, or answer set) is verified by
    # grep -rn over every changed file as part of this lane's pre-push
    # evidence, not by a unit test: a test that must spell the forbidden
    # ids to check for their absence would itself violate the same grep
    # it exists to satisfy.


class PromptsAndScoring(unittest.TestCase):
    def setUp(self):
        self.gen = load_generalization()
        self.out = tempfile.mkdtemp(prefix="jbeq-gen-prompts-")
        self.answers_dir = tempfile.mkdtemp(prefix="jbeq-gen-answers-")

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)
        shutil.rmtree(self.answers_dir, ignore_errors=True)

    def test_one_blind_prompt_per_invented_case_no_leak(self):
        code, out = run("prompts", self.out, "--seed", GEN_PATH)
        self.assertEqual(code, 0, out)
        names = sorted(os.listdir(self.out))
        self.assertEqual(names, sorted("%s.md" % c["id"] for c in self.gen["cases"]))
        for case in self.gen["cases"]:
            with open(os.path.join(self.out, "%s.md" % case["id"]),
                      encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(case["rationale_ja"], text, case["id"])
            self.assertNotIn("expected", text.lower(), case["id"])
            self.assertNotIn("critical", text.lower(), case["id"])
            self.assertIn(case["input"], text, case["id"])
            self.assertIn(case["question"], text, case["id"])
            self.assertIn(jbeq_mdm.VOCABULARY_HEADING, text, case["id"])

    def test_gold_answers_score_zero_critical_failures(self):
        # The mechanical floor: the harness must be able to read this file at
        # all, and the file's own expected values must be internally
        # consistent (no case contradicts the rule it was written to prove).
        # This is NOT a blind model run; it is the scorer's self-check.
        gold = {c["id"]: c["expected"] for c in self.gen["cases"]}
        answers_path = os.path.join(self.answers_dir, "gold.json")
        with open(answers_path, "w", encoding="utf-8") as fh:
            json.dump(gold, fh, ensure_ascii=False)
        code, out = run("score", answers_path, "--seed", GEN_PATH)
        self.assertEqual(code, 0, out)
        self.assertIn("critical false merges: 0 of 9", out, out)
        self.assertIn("critical wrong: 0 of 9", out, out)
        self.assertIn("JBEQ-MDM SEED: 9 of 9", out, out)

    def test_the_three_rules_actually_discriminate(self):
        # A rule that never fires on its own case is not a rule. Swap each
        # case's answer for the single most tempting wrong label the round 2
        # rationale showed the rules arm reaching for, and confirm the scorer
        # calls it a critical wrong. Never runs against the frozen seed.
        #
        # G6 is the exception since the 2026-09-06 founder ruling
        # (jbeq_mdm.SCORER_VERSION, REFUTED_IDENTITY_CLASS): rule 6's own
        # axis IS KEEP SEPARATE versus REJECT MATCH (decision-rules-
        # addendum.md's own words: "rule 6 sits on a different axis, KEEP
        # SEPARATE versus REJECT MATCH"), and that is exactly the pair the
        # ruling now scores as one equivalence class for a refuted identity.
        # The scorer can no longer catch that swap as wrong; it is asserted
        # separately below as an equivalence hit, not folded into wrong_swap.
        wrong_swap = {
            "G5-01": "LINK AS RELATED",
            "G5-02": "LINK AS RELATED",
            "G5-03": "LINK AS RELATED",
            "G7-01": "KEEP SEPARATE",
            "G7-02": "KEEP SEPARATE",
            "G7-03": "KEEP SEPARATE",
        }
        g6_ids = [c["id"] for c in self.gen["cases"] if c["id"].startswith("G6")]
        ids = [c["id"] for c in self.gen["cases"]]
        self.assertEqual(sorted(list(wrong_swap) + g6_ids), sorted(ids))
        for cid, wrong in wrong_swap.items():
            gold = {c["id"]: c["expected"] for c in self.gen["cases"]}
            gold[cid] = wrong
            answers_path = os.path.join(self.answers_dir, "%s.json" % cid)
            with open(answers_path, "w", encoding="utf-8") as fh:
                json.dump(gold, fh, ensure_ascii=False)
            code, out = run("score", answers_path, "--seed", GEN_PATH)
            self.assertEqual(code, 1, "%s: %s" % (cid, out))
            self.assertIn(cid, out, out)
        for cid in g6_ids:
            gold = {c["id"]: c["expected"] for c in self.gen["cases"]}
            self.assertEqual(gold[cid], "KEEP SEPARATE", cid)
            gold[cid] = "REJECT MATCH"
            answers_path = os.path.join(self.answers_dir, "%s.json" % cid)
            with open(answers_path, "w", encoding="utf-8") as fh:
                json.dump(gold, fh, ensure_ascii=False)
            code, out = run("score", answers_path, "--seed", GEN_PATH)
            self.assertEqual(code, 0, "%s: %s" % (cid, out))
            self.assertIn(
                "equivalence class %s: expected KEEP SEPARATE, engine said "
                "REJECT MATCH" % cid, out, out)

    def test_an_empty_answer_file_is_no_data_never_a_pass(self):
        answers_path = os.path.join(self.answers_dir, "empty.json")
        with open(answers_path, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        code, out = run("score", answers_path, "--seed", GEN_PATH)
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out)


class GeneralizationFileShapeRules8to11(unittest.TestCase):
    """Same shape checks as GeneralizationFileShape, for the sibling file
    that proves boundary rules 8 to 11 (kept separate so neither file's
    fixed case count or mix has to change for the other)."""

    def setUp(self):
        self.gen = load_generalization2()

    def test_eight_invented_cases_in_the_declared_mix(self):
        cases = self.gen["cases"]
        self.assertEqual(len(cases), 8, "2 cases per rule, 4 rules")
        counted = {}
        for case in cases:
            counted[case["track"]] = counted.get(case["track"], 0) + 1
        self.assertEqual(counted, EXPECTED_MIX2)
        self.assertEqual(self.gen["mix"], EXPECTED_MIX2)

    def test_not_frozen(self):
        self.assertFalse(self.gen["frozen"],
                         "the generalization file must not claim to be frozen")

    def test_case_ids_are_unique_and_outside_the_seed(self):
        gen_ids = [c["id"] for c in self.gen["cases"]]
        self.assertEqual(len(set(gen_ids)), len(gen_ids))
        seed_ids = set(c["id"] for c in load_seed()["cases"])
        overlap = seed_ids.intersection(gen_ids)
        self.assertFalse(overlap, "invented case ids must never reuse a seed id")
        gen1_ids = set(c["id"] for c in load_generalization()["cases"])
        self.assertFalse(gen1_ids.intersection(gen_ids),
                         "must never reuse a rules 5 to 7 case id either")

    def test_every_expected_answer_is_in_its_allowed_set(self):
        for case in self.gen["cases"]:
            self.assertIn(case["expected"], case["allowed"], case["id"])

    def test_every_critical_case_names_a_declared_critical_class(self):
        # Unlike the rules 5 to 7 file, not every case here is critical: an
        # ESCALATE-versus-NO-DATA routing miss (rule 8) does not itself cause
        # a false merge, wrong entity, or cross-tenant leak, so those two
        # cases are marked critical: false and carry no critical_class.
        classes = set(self.gen["critical_classes"])
        self.assertTrue(classes)
        for case in self.gen["cases"]:
            if case["critical"]:
                self.assertIn(case["critical_class"], classes, case["id"])
            else:
                self.assertNotIn("critical_class", case, case["id"])

    def test_every_case_carries_a_japanese_input_question_and_rationale(self):
        for case in self.gen["cases"]:
            for field in ("input", "question", "rationale_ja"):
                self.assertTrue(case[field].strip(), "%s %s" % (case["id"], field))

    def test_no_invented_case_text_reuses_a_seed_case_text(self):
        seed_text = json.dumps(load_seed()["cases"], ensure_ascii=False)
        for case in self.gen["cases"]:
            self.assertNotIn(case["input"], seed_text, case["id"])


class PromptsAndScoringRules8to11(unittest.TestCase):
    """Same mechanical proof as PromptsAndScoring, for rules 8 to 11: the
    harness reads the file, writes a leak-free blind prompt per case, and
    the file's own expected values score zero critical failures at exit 0.
    This is the scorer's self-check, never a blind model run."""

    def setUp(self):
        self.gen = load_generalization2()
        self.out = tempfile.mkdtemp(prefix="jbeq-gen2-prompts-")
        self.answers_dir = tempfile.mkdtemp(prefix="jbeq-gen2-answers-")

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)
        shutil.rmtree(self.answers_dir, ignore_errors=True)

    def test_one_blind_prompt_per_invented_case_no_leak(self):
        code, out = run("prompts", self.out, "--seed", GEN2_PATH)
        self.assertEqual(code, 0, out)
        names = sorted(os.listdir(self.out))
        self.assertEqual(names, sorted("%s.md" % c["id"] for c in self.gen["cases"]))
        for case in self.gen["cases"]:
            with open(os.path.join(self.out, "%s.md" % case["id"]),
                      encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(case["rationale_ja"], text, case["id"])
            self.assertNotIn("expected", text.lower(), case["id"])
            self.assertNotIn("critical", text.lower(), case["id"])
            self.assertIn(case["input"], text, case["id"])
            self.assertIn(case["question"], text, case["id"])
            self.assertIn(jbeq_mdm.VOCABULARY_HEADING, text, case["id"])

    def test_gold_answers_score_zero_critical_failures(self):
        gold = {c["id"]: c["expected"] for c in self.gen["cases"]}
        answers_path = os.path.join(self.answers_dir, "gold.json")
        with open(answers_path, "w", encoding="utf-8") as fh:
            json.dump(gold, fh, ensure_ascii=False)
        code, out = run("score", answers_path, "--seed", GEN2_PATH)
        self.assertEqual(code, 0, out)
        n_critical = sum(1 for c in self.gen["cases"] if c["critical"])
        self.assertIn("critical false merges: 0 of %d" % n_critical, out, out)
        self.assertIn("critical wrong: 0 of %d" % n_critical, out, out)
        self.assertIn("JBEQ-MDM SEED: 8 of 8", out, out)

    def test_the_four_rules_actually_discriminate(self):
        # Swap each case's answer for the single most tempting wrong label
        # the round 3 diagnosis named for that mistake, and confirm the
        # scorer calls the critical ones a critical wrong (the two rule 8
        # cases are not critical, so a wrong answer there is still counted
        # wrong but never flips the verdict to NOT READY on its own).
        #
        # G10-02 is the exception since the 2026-09-06 founder ruling
        # (jbeq_mdm.SCORER_VERSION, REFUTED_IDENTITY_CLASS): rule 10 sits on
        # the same KEEP SEPARATE versus REJECT MATCH axis as rule 6
        # (decision-rules-addendum.md: "rule 6 (and rule 10 below) governs"),
        # which the ruling now scores as one equivalence class. Asserted
        # separately below as an equivalence hit, not folded into wrong_swap.
        wrong_swap = {
            "G8-01": "NO-DATA",
            "G8-02": "NO-DATA",
            "G9-01": "SUGGEST MERGE",
            "G9-02": "AUTO-MERGE",
            "G10-01": "LINK AS RELATED",
            "G11-01": "ESCALATE",
            "G11-02": "ESCALATE",
        }
        ids = [c["id"] for c in self.gen["cases"]]
        self.assertEqual(sorted(list(wrong_swap) + ["G10-02"]), sorted(ids))
        critical_ids = set(c["id"] for c in self.gen["cases"] if c["critical"])
        for cid, wrong in wrong_swap.items():
            gold = {c["id"]: c["expected"] for c in self.gen["cases"]}
            gold[cid] = wrong
            answers_path = os.path.join(self.answers_dir, "%s.json" % cid)
            with open(answers_path, "w", encoding="utf-8") as fh:
                json.dump(gold, fh, ensure_ascii=False)
            code, out = run("score", answers_path, "--seed", GEN2_PATH)
            if cid in critical_ids:
                self.assertEqual(code, 1, "%s: %s" % (cid, out))
                self.assertIn(cid, out, out)
            else:
                self.assertEqual(code, 0, "%s: %s" % (cid, out))
                self.assertIn("JBEQ-MDM SEED: 7 of 8", out, out)

        self.assertIn("G10-02", critical_ids)
        gold = {c["id"]: c["expected"] for c in self.gen["cases"]}
        self.assertEqual(gold["G10-02"], "REJECT MATCH")
        gold["G10-02"] = "KEEP SEPARATE"
        answers_path = os.path.join(self.answers_dir, "G10-02.json")
        with open(answers_path, "w", encoding="utf-8") as fh:
            json.dump(gold, fh, ensure_ascii=False)
        code, out = run("score", answers_path, "--seed", GEN2_PATH)
        self.assertEqual(code, 0, out)
        self.assertIn(
            "equivalence class G10-02: expected REJECT MATCH, engine said "
            "KEEP SEPARATE", out, out)

    def test_an_empty_answer_file_is_no_data_never_a_pass(self):
        answers_path = os.path.join(self.answers_dir, "empty.json")
        with open(answers_path, "w", encoding="utf-8") as fh:
            json.dump({}, fh)
        code, out = run("score", answers_path, "--seed", GEN2_PATH)
        self.assertEqual(code, 3, out)
        self.assertIn("NO-DATA", out)


if __name__ == "__main__":
    unittest.main()
