"""What scripts/coe_outside_gate.py must keep true.

Every forbidden term used below is FAKE and made up for this test file, none
of it is a real client or team term from this estate's private-terms lists.
That is deliberate: a scanner's own test fixtures have leaked real terms
into this estate before (see scripts/test_private_terms_scan.py's docstring
for the incident), and the point of this module is to be the fix for a
different incident, not a second copy of that one.
"""
import ast
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coe_outside_gate as G  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__)
    )

# A fake category and a fake short term chosen specifically to reproduce the
# shape of tonight's real false positive (a short forbidden term sitting
# inside an everyday English word) without using the real term. "vex" is
# hidden inside the ordinary word "convex".
FAKE_TERMS = {"fake-client-terms": ["Vex"]}


class Recorder(object):
    """A zero-argument callable that remembers whether it ran, standing in
    for "the outside model call" in every guard() test."""

    def __init__(self, return_value="outside-result"):
        self.ran = False
        self.return_value = return_value

    def __call__(self):
        self.ran = True
        return self.return_value


class CheckCleanContent(unittest.TestCase):
    def test_true_positive_standalone_term_caught(self):
        result = G.check("please do not Vex the cat", terms=FAKE_TERMS)
        self.assertFalse(result.allowed)
        self.assertEqual(result.hit_count, 1)
        self.assertEqual(result.matched_categories, ["fake-client-terms"])

    def test_true_positive_lowercase_term_caught(self):
        result = G.check("please do not vex the cat", terms=FAKE_TERMS)
        self.assertFalse(result.allowed)
        self.assertEqual(result.hit_count, 1)

    def test_false_positive_measured_tonight_not_caught(self):
        # The exact shape measured tonight: a forbidden term sitting inside
        # an ordinary word is NOT a hit, because the character before the
        # embedded term ("n" in "convex") is itself a letter.
        result = G.check("the lens has a convex shape", terms=FAKE_TERMS)
        self.assertTrue(result.allowed)
        self.assertEqual(result.hit_count, 0)
        self.assertEqual(result.matched_categories, [])

    def test_clean_content_allowed(self):
        result = G.check("nothing forbidden lives in this sentence",
                          terms=FAKE_TERMS)
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason, "content is clean")


class CheckNeverPrintsWhatItForbids(unittest.TestCase):
    def test_reason_excludes_matched_text_and_term_list(self):
        result = G.check("please do not Vex the cat", terms=FAKE_TERMS)
        self.assertNotIn("Vex", result.reason)
        self.assertNotIn("vex", result.reason.lower())
        self.assertIn("fake-client-terms", result.reason)

    def test_gate_refused_message_excludes_matched_text(self):
        recorder = Recorder()
        with self.assertRaises(G.GateRefused) as ctx:
            G.guard("please do not Vex the cat", recorder, terms=FAKE_TERMS)
        self.assertFalse(recorder.ran)
        self.assertNotIn("vex", str(ctx.exception).lower())
        self.assertIn("fake-client-terms", str(ctx.exception))

    def test_module_defines_no_term_holding_constant(self):
        # It is not possible to enumerate every real private term here
        # (reproducing the list would itself violate the rule this module
        # exists to serve), so this checks the one structural fact that
        # would catch a future regression instead: the module's only
        # top-level assignments are the path constant and the size cap,
        # never a literal term or list of terms sitting in the source.
        module_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "coe_outside_gate.py"
        )
        with open(module_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        top_level_names = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        top_level_names.add(target.id)
        self.assertEqual(
            top_level_names, {"DEFAULT_TERMS_PATH", "MAX_CONTENT_BYTES"}
        )


class CheckUnreadableMeansRefuse(unittest.TestCase):
    def test_missing_config_file_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = os.path.join(tmp, "does-not-exist.json")
            original = G.DEFAULT_TERMS_PATH
            G.DEFAULT_TERMS_PATH = missing_path
            try:
                result = G.check("anything at all")
            finally:
                G.DEFAULT_TERMS_PATH = original
            self.assertFalse(result.allowed)
            self.assertEqual(result.matched_categories, [])

    def test_malformed_config_file_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad.json")
            with open(bad_path, "w", encoding="utf-8") as handle:
                handle.write("{not valid json")
            self.assertIsNone(G.load_terms(bad_path))

    def test_wrong_shape_config_refuses(self):
        # A list, not a {category: [term, ...]} object.
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad_shape.json")
            with open(bad_path, "w", encoding="utf-8") as handle:
                json.dump(["Vex"], handle)
            self.assertIsNone(G.load_terms(bad_path))

    def test_empty_terms_dict_refuses(self):
        result = G.check("anything at all", terms={})
        self.assertFalse(result.allowed)

    def test_category_with_no_terms_refuses(self):
        result = G.check("anything at all", terms={"fake": []})
        self.assertFalse(result.allowed)

    def test_none_passed_explicitly_is_same_as_missing(self):
        result = G.check("anything at all", terms=None)
        # Whatever the machine's real DEFAULT_TERMS_PATH holds, this call
        # must not raise, and must return a GateResult either way.
        self.assertIsInstance(result, G.GateResult)


class GuardGatesTheCall(unittest.TestCase):
    """Rule 1: the scan runs before the call, in this process, and gates
    it. This is the test that would go red under the mutation the worker
    contract requires (guard() calling `call()` before check())."""

    def test_call_never_runs_when_scan_refuses(self):
        recorder = Recorder()
        with self.assertRaises(G.GateRefused):
            G.guard("please do not Vex the cat", recorder, terms=FAKE_TERMS)
        self.assertFalse(recorder.ran, "call() ran even though the scan refused")

    def test_call_never_runs_when_terms_are_unreadable(self):
        recorder = Recorder()
        with self.assertRaises(G.GateRefused):
            G.guard("anything at all", recorder, terms={})
        self.assertFalse(recorder.ran)

    def test_call_runs_exactly_once_when_allowed(self):
        recorder = Recorder(return_value=42)
        outcome = G.guard("clean content", recorder, terms=FAKE_TERMS)
        self.assertTrue(recorder.ran)
        self.assertEqual(outcome, 42)

    def test_refusal_carries_the_result(self):
        try:
            G.guard("please do not Vex the cat", Recorder(), terms=FAKE_TERMS)
            self.fail("expected GateRefused")
        except G.GateRefused as exc:
            self.assertIsInstance(exc.result, G.GateResult)
            self.assertFalse(exc.result.allowed)


class EdgeList(unittest.TestCase):
    def test_empty_content_allowed(self):
        result = G.check("", terms=FAKE_TERMS)
        self.assertTrue(result.allowed)
        self.assertEqual(result.hit_count, 0)

    def test_whitespace_only_content_allowed(self):
        result = G.check("   \n\t  ", terms=FAKE_TERMS)
        self.assertTrue(result.allowed)

    def test_term_at_very_start_of_content_caught(self):
        result = G.check("Vex is not allowed here", terms=FAKE_TERMS)
        self.assertFalse(result.allowed)

    def test_term_at_very_end_of_content_caught(self):
        result = G.check("this content ends with Vex", terms=FAKE_TERMS)
        self.assertFalse(result.allowed)

    def test_term_embedded_on_both_sides_not_caught(self):
        # "convexity": a letter immediately before ("n") AND letters after
        # ("ity"). Only the preceding side is guarded, so this is refused
        # for the same reason "convex" is, independent of what follows.
        result = G.check("study the convexity of the curve", terms=FAKE_TERMS)
        self.assertTrue(result.allowed)

    def test_term_with_letter_suffix_still_caught(self):
        # A forbidden term glued to a letter AFTER it still counts: only
        # the preceding side is guarded (module docstring, RULE 2 section).
        result = G.check("Vexation is the noun form", terms=FAKE_TERMS)
        self.assertFalse(result.allowed)

    def test_bytes_content_decoded_and_scanned(self):
        result = G.check("do not Vex the cat".encode("utf-8"), terms=FAKE_TERMS)
        self.assertFalse(result.allowed)

    def test_bytes_content_clean_allowed(self):
        result = G.check("nothing here".encode("utf-8"), terms=FAKE_TERMS)
        self.assertTrue(result.allowed)

    def test_bytes_content_invalid_utf8_refuses(self):
        result = G.check(b"\xff\xfe\x00\x01", terms=FAKE_TERMS)
        self.assertFalse(result.allowed)

    def test_non_str_non_bytes_content_refuses(self):
        result = G.check(12345, terms=FAKE_TERMS)
        self.assertFalse(result.allowed)

    def test_oversized_payload_refused(self):
        original_cap = G.MAX_CONTENT_BYTES
        G.MAX_CONTENT_BYTES = 16
        try:
            result = G.check("this content is longer than sixteen bytes",
                              terms=FAKE_TERMS)
        finally:
            G.MAX_CONTENT_BYTES = original_cap
        self.assertFalse(result.allowed)
        self.assertEqual(result.matched_categories, [])

    def test_oversized_bytes_payload_refused_before_decode_attempted(self):
        original_cap = G.MAX_CONTENT_BYTES
        G.MAX_CONTENT_BYTES = 4
        try:
            # Invalid utf-8 tail that would raise on decode if decoding
            # were attempted; the size refusal must win first.
            result = G.check(b"abcdef\xff", terms=FAKE_TERMS)
        finally:
            G.MAX_CONTENT_BYTES = original_cap
        self.assertFalse(result.allowed)


class NormalizeTermsIsDefensive(unittest.TestCase):
    def test_terms_argument_is_not_mutated(self):
        original = copy.deepcopy(FAKE_TERMS)
        G.check("do not Vex the cat", terms=FAKE_TERMS)
        self.assertEqual(FAKE_TERMS, original)

    def test_non_dict_terms_argument_refuses(self):
        result = G.check("anything", terms=["Vex"])
        self.assertFalse(result.allowed)

    def test_non_list_category_value_refuses(self):
        result = G.check("anything", terms={"fake": "Vex"})
        self.assertFalse(result.allowed)

    def test_non_string_term_refuses(self):
        result = G.check("anything", terms={"fake": [123]})
        self.assertFalse(result.allowed)


class ModuleIsHonestAboutWhatItIs(unittest.TestCase):
    def test_docstring_states_the_boundary(self):
        doc = G.__doc__ or ""
        self.assertIn("backstop", doc.lower())
        self.assertIn("cannot recognise private content", doc.lower())


if __name__ == "__main__":
    unittest.main()
