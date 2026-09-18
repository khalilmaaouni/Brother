"""What scripts/bridge_content_gate.py must keep true.

Every forbidden term used below is FAKE (ACMEWIDGET, ACMECORP), invented for
this test file. This estate has a recorded incident of a scanner's test
fixtures leaking a real client or team term (see coe_outside_gate's test
file for the same discipline); this file carries none of the real ones.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_content_gate as G  # noqa: E402


def write_terms(path, mapping):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(mapping, handle)


class DecideDestinationClassification(unittest.TestCase):
    def test_local_destination_allows_without_scanning(self):
        # No terms_path given at all: if this scanned, it would refuse on a
        # missing list. It must not even try, because "claude" is local.
        verdict = G.decide("anything at all", "claude")
        self.assertEqual(verdict.verdict, G.ALLOW)
        self.assertIn("never leaves this machine", verdict.reason)

    def test_local_destination_is_case_insensitive(self):
        verdict = G.decide("x", "CLAUDE")
        self.assertEqual(verdict.verdict, G.ALLOW)

    def test_unknown_destination_refuses(self):
        verdict = G.decide("clean text", "some-new-lane-nobody-registered")
        self.assertEqual(verdict.verdict, G.REFUSE)
        self.assertIn("not a recognised lane", verdict.reason)

    def test_non_string_destination_refuses(self):
        verdict = G.decide("clean text", None)
        self.assertEqual(verdict.verdict, G.REFUSE)


class DecideTermsListLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        for name in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, name))
        os.rmdir(self.tmp)

    def test_missing_list_refuses_never_allow(self):
        missing = os.path.join(self.tmp, "does-not-exist.json")
        verdict = G.decide("clean text", "muse", terms_path=missing)
        self.assertEqual(verdict.verdict, G.REFUSE)
        self.assertIn("missing", verdict.reason)

    def test_empty_list_file_refuses(self):
        path = os.path.join(self.tmp, "terms.json")
        write_terms(path, {})
        verdict = G.decide("clean text", "muse", terms_path=path)
        self.assertEqual(verdict.verdict, G.REFUSE)

    def test_malformed_list_file_refuses(self):
        path = os.path.join(self.tmp, "terms.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("not json{{{")
        verdict = G.decide("clean text", "muse", terms_path=path)
        self.assertEqual(verdict.verdict, G.REFUSE)

    def test_default_terms_path_used_when_none_given(self):
        path = os.path.join(self.tmp, "terms.json")
        write_terms(path, {"vendor-fixture": ["ACMEWIDGET"]})
        original = G._coe.DEFAULT_TERMS_PATH
        G._coe.DEFAULT_TERMS_PATH = path
        try:
            allowed = G.decide("clean text", "muse")
            refused = G.decide("this has ACMEWIDGET in it", "muse")
        finally:
            G._coe.DEFAULT_TERMS_PATH = original
        self.assertEqual(allowed.verdict, G.ALLOW)
        self.assertEqual(refused.verdict, G.REFUSE)


class DecideScanning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.terms_path = os.path.join(self.tmp, "terms.json")
        write_terms(self.terms_path, {"vendor-fixture": ["ACMEWIDGET"]})

    def tearDown(self):
        for name in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, name))
        os.rmdir(self.tmp)

    def decide(self, payload):
        return G.decide(payload, "muse", terms_path=self.terms_path)

    def test_empty_payload_allows(self):
        verdict = self.decide("")
        self.assertEqual(verdict.verdict, G.ALLOW)

    def test_clean_payload_allows(self):
        verdict = self.decide("a perfectly ordinary sentence")
        self.assertEqual(verdict.verdict, G.ALLOW)

    def test_matching_payload_refuses(self):
        verdict = self.decide("please send ACMEWIDGET details")
        self.assertEqual(verdict.verdict, G.REFUSE)

    def test_refusal_reason_never_echoes_the_matched_term(self):
        verdict = self.decide("please send ACMEWIDGET details")
        self.assertNotIn("ACMEWIDGET", verdict.reason)
        self.assertNotIn("acmewidget", verdict.reason.lower())

    def test_match_is_case_insensitive(self):
        verdict = self.decide("please send acmewidget details")
        self.assertEqual(verdict.verdict, G.REFUSE)

    def test_reason_names_category_and_shape_and_position(self):
        verdict = self.decide("x ACMEWIDGET y")
        self.assertIn("vendor-fixture", verdict.reason)
        self.assertIn("10 chars", verdict.reason)
        self.assertIn("line 1", verdict.reason)

    def test_term_embedded_in_a_longer_word_is_not_a_false_positive(self):
        # Guard rule: a hit whose preceding character is a letter does not
        # count (mirrors coe_outside_gate's "convex" example).
        verdict = self.decide("PREACMEWIDGET is an unrelated word")
        self.assertEqual(verdict.verdict, G.ALLOW)

    def test_term_split_across_a_line_break_still_refuses(self):
        verdict = self.decide("ACME\nWIDGET appears here")
        self.assertEqual(verdict.verdict, G.REFUSE)
        self.assertIn("position unresolvable", verdict.reason)

    def test_bytes_payload_is_decoded_and_scanned(self):
        verdict = self.decide(b"please send ACMEWIDGET details")
        self.assertEqual(verdict.verdict, G.REFUSE)

    def test_bytes_payload_clean_allows(self):
        verdict = self.decide(b"nothing forbidden here")
        self.assertEqual(verdict.verdict, G.ALLOW)

    def test_invalid_utf8_bytes_refuse(self):
        verdict = self.decide(b"\xff\xfe\x00\x01")
        self.assertEqual(verdict.verdict, G.REFUSE)

    def test_non_text_non_bytes_payload_refuses(self):
        verdict = self.decide(12345)
        self.assertEqual(verdict.verdict, G.REFUSE)

    def test_oversized_payload_refuses(self):
        oversized = "a" * (G._coe.MAX_CONTENT_BYTES + 1)
        verdict = self.decide(oversized)
        self.assertEqual(verdict.verdict, G.REFUSE)
        self.assertIn("exceeds", verdict.reason)


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.terms_path = os.path.join(self.tmp, "terms.json")
        write_terms(self.terms_path, {"vendor-fixture": ["ACMEWIDGET"]})

    def tearDown(self):
        for name in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, name))
        os.rmdir(self.tmp)

    def test_guard_returns_calls_value_on_allow(self):
        result = G.guard("clean text", "muse", lambda: "outside-answer",
                          terms_path=self.terms_path)
        self.assertEqual(result, "outside-answer")

    def test_guard_never_calls_call_on_refusal(self):
        def boom():
            raise AssertionError("call must not run when the gate refuses")

        with self.assertRaises(G.GateRefused) as ctx:
            G.guard("has ACMEWIDGET in it", "muse", boom,
                    terms_path=self.terms_path)
        self.assertEqual(ctx.exception.verdict.verdict, G.REFUSE)

    def test_guard_raises_on_unknown_destination_without_calling_call(self):
        def boom():
            raise AssertionError("call must not run on an unknown lane")

        with self.assertRaises(G.GateRefused):
            G.guard("anything", "unregistered-lane", boom)

    def test_guard_local_destination_needs_no_terms_path(self):
        result = G.guard("anything", "claude", lambda: 42)
        self.assertEqual(result, 42)


if __name__ == "__main__":
    unittest.main()
