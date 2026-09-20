"""What scripts/jev_registry.py must keep true.

The property this file exists to assert: the real data/jev-registry.json
lints clean today (a regression that breaks that contract must fail here,
not surface later as a caller silently skipping every entry); a planted
lint violation is actually found, not swallowed; callable() refuses
MUST_NOT, must_stay_on_machine, unknown ids, AND every entry with any
lint finding at all, without ever reaching for the network or a
subprocess, proven by monkeypatching both to explode; the unknown-option
match reads option names case-insensitively and never an option's
description; duplicate ids make load() refuse the whole file rather than
silently keeping one; id lookup is exact (no case-folding, no trimming);
both_orders() flips a choice question's option order and leaves score and
noul alone; and a malformed registry file is refused by the CLI with exit
code 2, never a partial read.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jev_registry as R  # noqa: E402

REAL_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "jev-registry.json"
)


def _entry(entry_id="J900", role="second_opinion", wave="W1",
           privacy="public_or_own_text", qtype="choice",
           options=("a", "b", "unknown"), instructions="pick one"):
    return {
        "id": entry_id,
        "role": role,
        "wave": wave,
        "privacy": privacy,
        "question": {"type": qtype, "instructions": instructions, "options": list(options)},
    }


class LoadRealRegistry(unittest.TestCase):
    def test_real_registry_lints_clean(self):
        registry = R.load(REAL_REGISTRY_PATH)
        findings = R.lint(registry)
        self.assertEqual(
            findings, [],
            "real data/jev-registry.json has lint findings, report them "
            "rather than weakening lint:\n" + "\n".join(str(f) for f in findings),
        )

    def test_real_registry_has_117_entries(self):
        registry = R.load(REAL_REGISTRY_PATH)
        self.assertEqual(len(registry), 117)


class Lint(unittest.TestCase):
    def test_choice_with_no_unknown_option_is_found(self):
        registry = [_entry(entry_id="J901", options=("a", "b"))]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J901"]
        self.assertIn("missing-unknown-option", rules)

    def test_score_with_no_unknown_option_is_found(self):
        registry = [_entry(entry_id="J902", qtype="score", options=("1", "2", "3"))]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J902"]
        self.assertIn("missing-unknown-option", rules)

    def test_noul_is_never_checked_for_unknown_option(self):
        registry = [_entry(entry_id="J903", qtype="noul", options=("true", "false"))]
        findings = R.lint(registry)
        self.assertEqual([f for f in findings if f.entry_id == "J903"], [])

    def test_must_not_choice_with_no_unknown_option_is_not_flagged(self):
        registry = [_entry(entry_id="J904", role="MUST_NOT", options=("a", "b"))]
        findings = R.lint(registry)
        self.assertEqual([f for f in findings if f.entry_id == "J904"], [])

    def test_bad_question_type_for_non_must_not_entry_is_found(self):
        registry = [_entry(entry_id="J905", qtype="essay", options=())]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J905"]
        self.assertIn("bad-question-type", rules)

    def test_duplicate_id_is_found(self):
        registry = [_entry(entry_id="J906"), _entry(entry_id="J906")]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J906"]
        self.assertIn("duplicate-id", rules)

    def test_non_must_not_entry_with_no_wave_is_found(self):
        registry = [_entry(entry_id="J907", wave=None)]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J907"]
        self.assertIn("missing-wave", rules)

    def test_must_not_entry_with_no_wave_is_not_flagged(self):
        registry = [_entry(entry_id="J908", role="MUST_NOT", wave=None)]
        findings = R.lint(registry)
        self.assertEqual([f for f in findings if f.entry_id == "J908"], [])

    def test_must_stay_on_machine_entry_with_wrong_role_is_found(self):
        registry = [_entry(entry_id="J909", role="second_opinion",
                            privacy="must_stay_on_machine")]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J909"]
        self.assertIn("machine-only-not-must-not", rules)

    def test_must_stay_on_machine_entry_with_must_not_role_is_clean(self):
        registry = [_entry(entry_id="J910", role="MUST_NOT",
                            privacy="must_stay_on_machine", wave=None)]
        findings = R.lint(registry)
        self.assertEqual([f for f in findings if f.entry_id == "J910"], [])

    def test_clean_registry_has_no_findings(self):
        registry = [_entry(entry_id="J911")]
        self.assertEqual(R.lint(registry), [])

    def test_privacy_typo_is_found(self):
        # M2: a misspelled privacy value must never silently read as
        # callable, so lint flags anything outside the allowed set.
        registry = [_entry(entry_id="J912", privacy="publc_or_own_text")]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J912"]
        self.assertIn("bad-privacy-value", rules)

    def test_missing_privacy_field_is_found(self):
        entry = _entry(entry_id="J913")
        del entry["privacy"]
        findings = R.lint([entry])
        rules = [f.rule for f in findings if f.entry_id == "J913"]
        self.assertIn("missing-privacy", rules)


class Get(unittest.TestCase):
    def test_finds_entry(self):
        registry = [_entry(entry_id="J920")]
        self.assertEqual(R.get(registry, "J920")["id"], "J920")

    def test_unknown_id_raises(self):
        registry = [_entry(entry_id="J920")]
        with self.assertRaises(R.RegistryError):
            R.get(registry, "does-not-exist")


class ExplodingNetwork(object):
    """Any call to this raises, so a test using it proves the code under
    test never reached the network or a subprocess."""

    def __call__(self, *args, **kwargs):
        raise AssertionError("network/subprocess call reached during callable()")


class Callable(unittest.TestCase):
    def setUp(self):
        # Patch both a network entry point and a subprocess entry point so
        # any accidental reach for either fails the test loudly instead of
        # quietly succeeding against the real network.
        self._patches = [
            mock.patch.object(subprocess, "run", ExplodingNetwork()),
            mock.patch.object(urllib.request, "urlopen", ExplodingNetwork()),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_refuses_must_not_without_network_call(self):
        registry = [_entry(entry_id="J930", role="MUST_NOT")]
        ok, reason = R.callable(registry, "J930")
        self.assertFalse(ok)
        self.assertIn("MUST_NOT", reason)

    def test_refuses_must_stay_on_machine_without_network_call(self):
        registry = [_entry(entry_id="J931", role="second_opinion",
                            privacy="must_stay_on_machine")]
        ok, reason = R.callable(registry, "J931")
        self.assertFalse(ok)
        self.assertIn("must stay on machine", reason)

    def test_refuses_unknown_id_without_network_call(self):
        registry = [_entry(entry_id="J932")]
        ok, reason = R.callable(registry, "no-such-id")
        self.assertFalse(ok)
        self.assertIn("unknown", reason.lower())

    def test_accepts_callable_choice_entry(self):
        registry = [_entry(entry_id="J933", options=("a", "b", "unknown"))]
        ok, question = R.callable(registry, "J933")
        self.assertTrue(ok)
        self.assertEqual(question["type"], "choice")
        self.assertEqual(question["criteria"], {"a": "a", "b": "b", "unknown": "unknown"})

    def test_accepts_callable_score_entry(self):
        registry = [_entry(entry_id="J934", qtype="score", options=("1", "2", "unknown"))]
        ok, question = R.callable(registry, "J934")
        self.assertTrue(ok)
        self.assertEqual(question["type"], "score")
        self.assertEqual(question["criteria"], ["1", "2", "unknown"])

    def test_accepts_callable_noul_entry(self):
        registry = [_entry(entry_id="J935", qtype="noul", options=("true", "false"),
                            instructions="is it done")]
        ok, question = R.callable(registry, "J935")
        self.assertTrue(ok)
        self.assertEqual(question["type"], "noul")
        self.assertEqual(question["criteria"], {"true": "is it done", "false": "is it done"})

    def test_real_registry_second_opinion_entry_is_callable(self):
        registry = R.load(REAL_REGISTRY_PATH)
        second_opinion = next(e for e in registry if e["role"] == "second_opinion")
        ok, question = R.callable(registry, second_opinion["id"])
        self.assertTrue(ok)
        self.assertEqual(question["type"], second_opinion["question"]["type"])

    def test_real_registry_must_not_entries_all_refused(self):
        registry = R.load(REAL_REGISTRY_PATH)
        must_not_ids = [e["id"] for e in registry if e["role"] == "MUST_NOT"]
        self.assertTrue(must_not_ids)
        for entry_id in must_not_ids:
            ok, _reason = R.callable(registry, entry_id)
            self.assertFalse(ok, "MUST_NOT entry %r was callable" % (entry_id,))


class BothOrders(unittest.TestCase):
    def test_choice_is_reversed(self):
        question = {"type": "choice", "instructions": "x",
                    "criteria": {"a": "a", "b": "b", "c": "c"}}
        original = copy.deepcopy(question)
        results = R.both_orders(question)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], original)
        self.assertEqual(list(results[1]["criteria"].keys()), ["c", "b", "a"])
        # original untouched
        self.assertEqual(question, original)

    def test_score_is_left_alone(self):
        question = {"type": "score", "instructions": "x", "criteria": ["1", "2", "3"]}
        results = R.both_orders(question)
        self.assertEqual(results, [question])

    def test_noul_is_left_alone(self):
        question = {"type": "noul", "instructions": "x",
                    "criteria": {"true": "x", "false": "x"}}
        results = R.both_orders(question)
        self.assertEqual(results, [question])


class LintOptionNameMatching(unittest.TestCase):
    """Requirement 2: the unknown-option match is case-insensitive, reads
    option NAMES (dict keys for a {name: description} option), and is
    never fooled by a description that happens to mention the word."""

    def test_dict_option_named_unknown_passes_case_insensitively(self):
        registry = [_entry(entry_id="J952", options=({"Unknown": "abstain, cannot tell"},
                                                       "a", "b"))]
        self.assertEqual(R.lint(registry), [])

    def test_description_only_mention_of_unknown_still_flagged(self):
        registry = [_entry(entry_id="J953", options=({"a": "the unknown fallback value"},
                                                       "b", "c"))]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J953"]
        self.assertIn("missing-unknown-option", rules)

    def test_bare_string_all_caps_unknown_passes(self):
        registry = [_entry(entry_id="J954", options=("a", "b", "UNKNOWN"))]
        self.assertEqual(R.lint(registry), [])


class LintOptionCountAndDuplicates(unittest.TestCase):
    """Requirement 5: empty/single-option choice questions and duplicate
    option names are lint findings, so callable() refuses them too."""

    def test_empty_options_on_choice_is_found(self):
        registry = [_entry(entry_id="J955", options=())]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J955"]
        self.assertIn("too-few-options", rules)

    def test_single_option_on_choice_is_found(self):
        registry = [_entry(entry_id="J956", options=("only-one",))]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J956"]
        self.assertIn("too-few-options", rules)

    def test_must_not_choice_with_empty_options_is_not_flagged(self):
        registry = [_entry(entry_id="J957", role="MUST_NOT", options=(), wave=None)]
        findings = R.lint(registry)
        self.assertEqual([f for f in findings if f.entry_id == "J957"], [])

    def test_duplicate_option_names_on_choice_is_found(self):
        registry = [_entry(entry_id="J958", options=("a", "a", "unknown"))]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J958"]
        self.assertIn("duplicate-option-names", rules)

    def test_duplicate_option_names_case_insensitive_on_score(self):
        registry = [_entry(entry_id="J959", qtype="score",
                            options=("Low", "low", "unknown"))]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J959"]
        self.assertIn("duplicate-option-names", rules)

    def test_two_distinct_options_no_unknown_needed_check_still_applies(self):
        # sanity: two distinct, unique-named options with an unknown one
        # is clean (no false positive from the new checks).
        registry = [_entry(entry_id="J960a", options=("a", "b", "unknown"))]
        self.assertEqual(R.lint(registry), [])


class LintRefusesAdviseActForACallSiteThatNeverReadsTheAnswer(unittest.TestCase):
    """Item 7 (Muse G1 point 5, A0.8 round 6, hardened in the BLOCK
    review of round 6): a registry entry marked "call_site_reads_answer"
    PRESENT but anything other than the literal bool True (an explicit
    False, or a non-bool typo such as the string "false" or 0)
    configured to advise or act, in the given seams_config, is a lint
    finding -- an operator cannot flip such a mode and believe it is now
    enforcement, and a one-character data typo cannot make this check
    silently pass either. No seams_config at all, or a mode of
    shadow/off, is unaffected: shadow never reads the answer by design
    (every mode behaves that way), so it is never the problem this rule
    exists to catch. The field being MISSING entirely is unaffected too
    -- see test_an_entry_with_no_call_site_reads_answer_field_is_never_flagged
    below, which is the regression guard for that scope boundary."""

    def _entry(self, entry_id="J961", reads=False):
        e = _entry(entry_id=entry_id)
        e["call_site_reads_answer"] = reads
        return e

    def test_advise_on_a_non_reading_entry_is_a_finding(self):
        registry = [self._entry()]
        findings = R.lint(registry, seams_config={"modes": {"J961": "advise"}})
        rules = [f.rule for f in findings if f.entry_id == "J961"]
        self.assertIn("advise-act-never-read", rules)

    def test_act_on_a_non_reading_entry_is_a_finding(self):
        registry = [self._entry()]
        findings = R.lint(registry, seams_config={"modes": {"J961": "act"}})
        rules = [f.rule for f in findings if f.entry_id == "J961"]
        self.assertIn("advise-act-never-read", rules)

    def test_shadow_on_a_non_reading_entry_is_not_a_finding(self):
        registry = [self._entry()]
        findings = R.lint(registry, seams_config={"modes": {"J961": "shadow"}})
        self.assertEqual([f for f in findings if f.entry_id == "J961"], [])

    def test_off_or_unconfigured_is_not_a_finding(self):
        registry = [self._entry()]
        self.assertEqual(
            [f for f in R.lint(registry, seams_config={"modes": {"J961": "off"}})
             if f.entry_id == "J961"], [])
        self.assertEqual(
            [f for f in R.lint(registry, seams_config={"modes": {}}) if f.entry_id == "J961"], [])

    def test_no_seams_config_at_all_is_not_a_finding(self):
        # lint() must still work with no opinion about any seams file --
        # this is the shape every existing caller (including the CLI on
        # a registry with no sibling jev-seams.json) already relies on.
        registry = [self._entry()]
        self.assertEqual(R.lint(registry), [])
        self.assertEqual(R.lint(registry, seams_config=None), [])

    def test_an_entry_that_does_read_the_answer_is_never_flagged(self):
        registry = [self._entry(entry_id="J962", reads=True)]
        findings = R.lint(registry, seams_config={"modes": {"J962": "act"}})
        self.assertEqual([f for f in findings if f.entry_id == "J962"], [])

    def test_string_false_on_act_is_a_finding_not_a_silent_pass(self):
        # BLOCK review, round 6: a string "false" is not the bool False,
        # so the old "is False" comparison missed it entirely. "is not
        # True" catches it.
        registry = [self._entry(entry_id="J964", reads="false")]
        findings = R.lint(registry, seams_config={"modes": {"J964": "act"}})
        rules = [f.rule for f in findings if f.entry_id == "J964"]
        self.assertIn("advise-act-never-read", rules)

    def test_numeric_zero_on_act_is_a_finding_not_a_silent_pass(self):
        # 0 == False in Python, but 0 is not False, and it is certainly
        # not the bool True this field needs to mean "reads".
        registry = [self._entry(entry_id="J965", reads=0)]
        findings = R.lint(registry, seams_config={"modes": {"J965": "act"}})
        rules = [f.rule for f in findings if f.entry_id == "J965"]
        self.assertIn("advise-act-never-read", rules)

    def test_an_entry_with_no_call_site_reads_answer_field_is_never_flagged(self):
        # missing the field entirely (every entry except the eight G1
        # ones) is not the same as False: only an EXPLICIT false triggers
        # this rule.
        registry = [_entry(entry_id="J963")]
        findings = R.lint(registry, seams_config={"modes": {"J963": "act"}})
        self.assertEqual([f for f in findings if f.entry_id == "J963"], [])

    def test_malformed_modes_mapping_is_read_as_every_entry_off(self):
        registry = [self._entry()]
        self.assertEqual(R.lint(registry, seams_config={"modes": "not-a-dict"}), [])
        self.assertEqual(R.lint(registry, seams_config={}), [])

    def test_cli_reads_the_sibling_seams_file_and_fails_on_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = os.path.join(tmp, "reg.json")
            with open(reg_path, "w", encoding="utf-8") as fh:
                json.dump([self._entry()], fh)
            seams_path = os.path.join(tmp, "jev-seams.json")
            with open(seams_path, "w", encoding="utf-8") as fh:
                json.dump({"modes": {"J961": "act"}}, fh)
            self.assertEqual(R.main(["lint", reg_path]), R.EXIT_FINDINGS)

    def test_cli_with_no_sibling_seams_file_still_exits_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = os.path.join(tmp, "reg.json")
            with open(reg_path, "w", encoding="utf-8") as fh:
                json.dump([self._entry()], fh)
            # no jev-seams.json written alongside it
            self.assertEqual(R.main(["lint", reg_path]), R.EXIT_CLEAN)

    def test_cli_with_a_shadow_only_sibling_seams_file_exits_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg_path = os.path.join(tmp, "reg.json")
            with open(reg_path, "w", encoding="utf-8") as fh:
                json.dump([self._entry()], fh)
            seams_path = os.path.join(tmp, "jev-seams.json")
            with open(seams_path, "w", encoding="utf-8") as fh:
                json.dump({"modes": {"J961": "shadow"}}, fh)
            self.assertEqual(R.main(["lint", reg_path]), R.EXIT_CLEAN)


class CallSiteReadsAnswerNotBoolIsItsOwnDistinctFinding(unittest.TestCase):
    """BLOCK review, round 6: a "call_site_reads_answer" field that is
    PRESENT but not a Python bool is a data typo, and must be named for
    exactly that (a type error) via its own "call-site-reads-answer-
    not-bool" finding from _entry_findings() -- unconditional, no
    seams_config needed, fires regardless of mode, distinct from
    "advise-act-never-read" so the two problems (a call site not proven
    to read vs. a garbage value in this field) are never conflated. A
    present bool (True or False either), or the field missing entirely,
    is never this finding."""

    def _entry(self, entry_id="J970", value="OMIT"):
        e = _entry(entry_id=entry_id)
        if value != "OMIT":
            e["call_site_reads_answer"] = value
        return e

    def test_string_false_is_flagged_with_no_seams_config_at_all(self):
        registry = [self._entry(value="false")]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J970"]
        self.assertIn("call-site-reads-answer-not-bool", rules)

    def test_numeric_zero_is_flagged(self):
        registry = [self._entry(entry_id="J971", value=0)]
        findings = R.lint(registry)
        rules = [f.rule for f in findings if f.entry_id == "J971"]
        self.assertIn("call-site-reads-answer-not-bool", rules)

    def test_bool_true_is_never_flagged(self):
        registry = [self._entry(entry_id="J972", value=True)]
        findings = R.lint(registry)
        self.assertEqual(
            [f for f in findings if f.entry_id == "J972" and f.rule == "call-site-reads-answer-not-bool"],
            [])

    def test_bool_false_is_never_flagged_as_a_type_error(self):
        # False is a valid bool: it is a legitimate "does not read"
        # declaration, not a typo. It can still trigger
        # advise-act-never-read separately (covered elsewhere); it must
        # never also get this type-error finding.
        registry = [self._entry(entry_id="J973", value=False)]
        findings = R.lint(registry)
        self.assertEqual(
            [f for f in findings if f.entry_id == "J973" and f.rule == "call-site-reads-answer-not-bool"],
            [])

    def test_missing_field_is_never_flagged_as_a_type_error(self):
        registry = [self._entry(entry_id="J974")]
        findings = R.lint(registry)
        self.assertEqual(
            [f for f in findings if f.entry_id == "J974" and f.rule == "call-site-reads-answer-not-bool"],
            [])

    def test_a_type_error_entry_is_never_callable_in_any_mode(self):
        # _entry_findings() is the single source of truth both lint()
        # and callable() share (see its own docstring): a dirty entry
        # (any finding at all) is refused by callable() too, in EVERY
        # mode, not only advise/act -- this is what makes a type-error
        # entry safe even in shadow, where the advise-act-never-read
        # cross-check would have nothing to say.
        registry = [self._entry(value="false")]
        ok, reason = R.callable(registry, "J970")
        self.assertFalse(ok)
        self.assertIn("call-site-reads-answer-not-bool", reason)


class CallSiteReadsAnswerFullMatrix(unittest.TestCase):
    """The exact matrix the BLOCK review asked for, in one place: bool
    True, bool False, string "false", numeric 0, and a missing field,
    each against lint()'s two independent checks (advise-act-never-read
    when configured to act, and the unconditional type check), so the
    correct outcome for every case is visible side by side rather than
    scattered across two classes."""

    def _entry(self, entry_id, value="OMIT"):
        e = _entry(entry_id=entry_id)
        if value != "OMIT":
            e["call_site_reads_answer"] = value
        return e

    def _rules(self, entry_id, value):
        registry = [self._entry(entry_id, value)]
        findings = R.lint(registry, seams_config={"modes": {entry_id: "act"}})
        return sorted(f.rule for f in findings)

    def test_bool_true_is_clean(self):
        self.assertEqual(self._rules("M1", True), [])

    def test_bool_false_is_advise_act_never_read_only(self):
        self.assertEqual(self._rules("M2", False), ["advise-act-never-read"])

    def test_string_false_is_both_findings(self):
        self.assertEqual(
            self._rules("M3", "false"),
            ["advise-act-never-read", "call-site-reads-answer-not-bool"])

    def test_numeric_zero_is_both_findings(self):
        self.assertEqual(
            self._rules("M4", 0),
            ["advise-act-never-read", "call-site-reads-answer-not-bool"])

    def test_missing_field_is_clean(self):
        # The one case that stays exactly as it always was: see
        # test_no_call_site_reads_answer_field_is_still_never_downgraded
        # in test_jev_seam.py for the runtime half of this same fact.
        self.assertEqual(self._rules("M5", "OMIT"), [])


class LoadDuplicateIds(unittest.TestCase):
    """Requirement 3: a duplicate id in a FILE makes load() refuse the
    whole file, never silently keeping the first or the last entry."""

    def test_duplicate_ids_in_file_make_load_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dup.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([_entry(entry_id="J961"), _entry(entry_id="J961")], fh)
            with self.assertRaises(R.RegistryError):
                R.load(path)

    def test_duplicate_ids_in_file_exit_2_via_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dup.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([_entry(entry_id="J962"), _entry(entry_id="J962")], fh)
            self.assertEqual(R.main(["lint", path]), R.EXIT_UNREADABLE)


class ExactIdMatching(unittest.TestCase):
    """Requirement 4: ids are matched exactly, never case-folded or
    trimmed."""

    def test_get_refuses_trailing_space(self):
        registry = [_entry(entry_id="J025")]
        with self.assertRaises(R.RegistryError):
            R.get(registry, "J025 ")

    def test_get_refuses_wrong_case(self):
        registry = [_entry(entry_id="J025")]
        with self.assertRaises(R.RegistryError):
            R.get(registry, "j025")

    def test_callable_refuses_trailing_space_and_wrong_case(self):
        registry = [_entry(entry_id="J025")]
        ok, _reason = R.callable(registry, "J025 ")
        self.assertFalse(ok)
        ok, _reason = R.callable(registry, "j025")
        self.assertFalse(ok)


class CallableRefusesEveryLintErrorFixture(unittest.TestCase):
    """Requirement 1: callable() must refuse (False, reason naming the
    finding) any entry with ANY lint finding, not only MUST_NOT/
    must_stay_on_machine/unknown ids, with the network/subprocess
    exploders still armed so no fixture can sneak a real call through."""

    def setUp(self):
        self._patches = [
            mock.patch.object(subprocess, "run", ExplodingNetwork()),
            mock.patch.object(urllib.request, "urlopen", ExplodingNetwork()),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _assert_refused_and_names_finding(self, registry, entry_id):
        ok, reason = R.callable(registry, entry_id)
        self.assertFalse(ok, "callable(%r) should have been refused" % (entry_id,))
        findings = R.lint(registry)
        expected_rules = {f.rule for f in findings if f.entry_id == entry_id}
        self.assertTrue(expected_rules, "fixture %r produced no lint findings" % (entry_id,))
        for rule in expected_rules:
            self.assertIn(rule, reason, "reason %r does not name rule %r" % (reason, rule))

    def test_missing_unknown_option(self):
        registry = [_entry(entry_id="J970", options=("a", "b"))]
        self._assert_refused_and_names_finding(registry, "J970")

    def test_bad_question_type(self):
        registry = [_entry(entry_id="J971", qtype="essay", options=())]
        self._assert_refused_and_names_finding(registry, "J971")

    def test_duplicate_id(self):
        registry = [_entry(entry_id="J972"), _entry(entry_id="J972")]
        self._assert_refused_and_names_finding(registry, "J972")

    def test_missing_wave(self):
        registry = [_entry(entry_id="J973", wave=None)]
        self._assert_refused_and_names_finding(registry, "J973")

    def test_too_few_options_empty(self):
        registry = [_entry(entry_id="J974", options=())]
        self._assert_refused_and_names_finding(registry, "J974")

    def test_too_few_options_single(self):
        registry = [_entry(entry_id="J975", options=("only-one",))]
        self._assert_refused_and_names_finding(registry, "J975")

    def test_duplicate_option_names(self):
        registry = [_entry(entry_id="J976", options=("a", "a", "unknown"))]
        self._assert_refused_and_names_finding(registry, "J976")

    def test_bad_privacy_value(self):
        registry = [_entry(entry_id="J979", privacy="publc_or_own_text")]
        self._assert_refused_and_names_finding(registry, "J979")

    def test_missing_privacy_field(self):
        entry = _entry(entry_id="J980")
        del entry["privacy"]
        self._assert_refused_and_names_finding([entry], "J980")

    def test_machine_only_not_must_not(self):
        # Caught by callable()'s dedicated must_stay_on_machine check
        # before the lint gate is even reached; still must be False.
        registry = [_entry(entry_id="J977", privacy="must_stay_on_machine")]
        ok, reason = R.callable(registry, "J977")
        self.assertFalse(ok)
        self.assertIn("must stay on machine", reason)


class CallableUsesDictShapedOptions(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(subprocess, "run", ExplodingNetwork()),
            mock.patch.object(urllib.request, "urlopen", ExplodingNetwork()),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_dict_option_description_used_in_criteria(self):
        registry = [_entry(entry_id="J978",
                            options=({"Unknown": "abstain, cannot tell"}, "a", "b"))]
        ok, question = R.callable(registry, "J978")
        self.assertTrue(ok)
        self.assertEqual(question["criteria"]["Unknown"], "abstain, cannot tell")
        self.assertEqual(question["criteria"]["a"], "a")


class MalformedFile(unittest.TestCase):
    def test_invalid_json_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assertEqual(R.main(["lint", path]), R.EXIT_UNREADABLE)
            with self.assertRaises(R.RegistryError):
                R.load(path)

    def test_json_object_instead_of_array_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"not": "a list"}, fh)
            self.assertEqual(R.main(["lint", path]), R.EXIT_UNREADABLE)

    def test_entry_missing_id_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([{"question": {"type": "noul", "instructions": "x"}}], fh)
            self.assertEqual(R.main(["lint", path]), R.EXIT_UNREADABLE)

    def test_missing_file_exits_2(self):
        self.assertEqual(R.main(["lint", "/no/such/path/jev-registry.json"]),
                          R.EXIT_UNREADABLE)


class CliExitCodes(unittest.TestCase):
    def test_clean_registry_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "reg.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([_entry()], fh)
            self.assertEqual(R.main(["lint", path]), R.EXIT_CLEAN)

    def test_dirty_registry_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "reg.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([_entry(options=("a", "b"))], fh)
            self.assertEqual(R.main(["lint", path]), R.EXIT_FINDINGS)

    def test_real_registry_exits_0(self):
        self.assertEqual(R.main(["lint", REAL_REGISTRY_PATH]), R.EXIT_CLEAN)


if __name__ == "__main__":
    unittest.main()
