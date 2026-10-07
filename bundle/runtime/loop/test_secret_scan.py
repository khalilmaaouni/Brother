"""The one table both gates read, held to what it must refuse.

Every secret shaped fixture here is assembled from parts at run time, never as
one contiguous literal, so this file does not itself carry the shapes it tests
for. Nothing here reads the home directory, the network, or another machine's
state: the fixtures are built, not found.

The module under test is imported by its bare name from this file's own folder,
scripts/loop/, where the one secret_scan.py lives (2026-09-30: FX-07.1 had placed it
flat under scripts/ and FX-07.2 wrote a second copy here; they are one module now).
"""
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import secret_scan as S


def _mk(*parts):
    """Join the parts of a fixture. Nothing here is ever one literal."""
    return "".join(parts)


def _sk_key(tail):
    return _mk("s", "k", "-", tail)


def _aws_key_id(tail):
    return _mk("AK", "IA", tail)


def _aws_session_id(tail):
    return _mk("AS", "IA", tail)


def _github_classic(tail):
    return _mk("g", "hp_", tail)


def _github_fine(tail):
    return _mk("git", "hub_pat_", tail)


def _slack(tail):
    return _mk("x", "ox", "b", "-", tail)


def _private_key_header():
    return _mk("BEG", "IN ", "RSA ", "PRIVATE KEY")


def _fenced_private_key_header():
    return _mk("-" * 5, _private_key_header(), "-" * 5)


def _password_assignment():
    return _mk("pass", "word", "=", "AAAA")


def _passwd_assignment():
    return _mk("pass", "wd", "=", "AAAA")


def _bearer_token():
    return _mk("bea", "rer ", "A" * 16)


def _api_key_assignment():
    return _mk("ap", "i_key", "=", "AAAAAAAA")


def _secret_key_assignment():
    return _mk("sec", "ret_key", "=", "AAAAAAAA")


def _aws_secret_assignment():
    return _mk("aws", "_secret_", "access_", "key", " = ", "AAAAAAAA")


def _public_example():
    return _mk("AK", "IA", "IOS", "FODNN7", "EXAM", "PLE")


#: One isolated fixture per union family, so a family that stops matching is
#: caught by name.
_UNION_FIXTURES = (
    ("openai-key", _sk_key("A" * 20)),
    ("aws-key-id", _aws_key_id("A" * 8)),
    ("aws-secret-assignment", _aws_secret_assignment()),
    ("github-classic-token", _github_classic("A" * 8)),
    ("github-fine-grained-token", _github_fine("A" * 8)),
    ("slack-token", _slack("A" * 10)),
    ("aws-session-id", _aws_session_id("A" * 8)),
    ("private-key-header", _private_key_header()),
    ("password-assignment", _password_assignment()),
    ("passwd-assignment", _passwd_assignment()),
    ("bearer-token", _bearer_token()),
    ("api-key-assignment", _api_key_assignment()),
    ("secret-key-assignment", _secret_key_assignment()),
)

#: One fixture per strict family. Every one of these must also match the union.
_STRICT_FIXTURES = (
    ("openai-key", _sk_key("A" * 20)),
    ("aws-key-id", _aws_key_id("A" * 16)),
    ("github-classic-token", _github_classic("A" * 36)),
    ("private-key-header", _private_key_header()),
)

#: Every distinct value shape either gate refused this month, rebuilt from the
#: family and the match length recorded for it. Nothing is read from the home
#: directory: the shapes are rebuilt here at run time.
_CORPUS_FIXTURES = (
    ("openai-key", _sk_key("A" * 20)),
    ("openai-key", _sk_key("A" * 22)),
    ("slack-token", _slack("A" * 21)),
    ("slack-token", _slack("A" * 29)),
    ("password-assignment", _mk("pass", "word", " = ", "ABCD")),
    ("bearer-token", _bearer_token()),
    ("api-key-assignment", _mk("ap", "i_key", " = ", "AAAAAAAA")),
    ("github-classic-token", _github_classic("A" * 12)),
    ("github-classic-token", _github_classic("A" * 36)),
    ("private-key-header", _fenced_private_key_header()),
    ("private-key-header", _private_key_header()),
    ("aws-key-id", _public_example()),
)


class TheOneTable(unittest.TestCase):
    """What the one table must match, must not match, and must refuse."""

    def test_union_holds_every_old_alternative(self):
        self.assertEqual(13, len(S.FAMILIES))
        self.assertEqual(13, len(set(name for name, _ in S.FAMILIES)))
        for name, value in _UNION_FIXTURES:
            with self.subTest(family=name):
                self.assertIn(name, S.families(value))
                self.assertGreaterEqual(S.count(value), 1)
        self.assertEqual(4, len(S.STRICT_FAMILIES))
        for name, value in _STRICT_FIXTURES:
            with self.subTest(family=name):
                self.assertTrue(any(p.search(value) for p in S.STRICT))
                self.assertGreaterEqual(S.count(value), 1)

    def test_strict_is_a_subset_of_union(self):
        for name, value in _STRICT_FIXTURES:
            with self.subTest(family=name):
                self.assertTrue(any(p.search(value) for p in S.STRICT))
                self.assertGreaterEqual(S.count(value), 1)

    def test_each_family_isolated(self):
        for name, value in _UNION_FIXTURES:
            with self.subTest(family=name):
                self.assertEqual([name], S.families(value))
                self.assertEqual(1, S.count(value))

    def test_many_values_all_named_none_printed(self):
        text = "\n".join(value for _, value in _UNION_FIXTURES)
        names = S.families(text)
        self.assertEqual(sorted(name for name, _ in _UNION_FIXTURES), names)
        self.assertGreaterEqual(S.count(text), 13)
        shown = repr(names)
        for name, value in _UNION_FIXTURES:
            with self.subTest(family=name):
                self.assertNotIn(value, shown)

    def test_empty_text_counts_zero(self):
        self.assertEqual(0, S.count(""))
        self.assertEqual([], S.families(""))

    def test_unknown_shape_is_clean(self):
        text = "the quick brown fox jumps over the lazy dog"
        self.assertEqual(0, S.count(text))
        self.assertEqual([], S.families(text))

    def test_non_string_input_refused(self):
        hostile = (None, 0, True, -1, float("nan"), b"x", [], ["x"], {},
                   {"a": 1}, (), {1, 2}, object())
        public = (("count", S.count), ("families", S.families),
                  ("added_text", S.added_text),
                  ("strip_public_examples", S.strip_public_examples))
        for label, func in public:
            for value in hostile:
                with self.subTest(function=label):
                    with self.assertRaises(S.ScanInputError) as caught:
                        func(value)
                    self.assertIsInstance(caught.exception, ValueError)
                    self.assertIsInstance(caught.exception, TypeError)

    def test_near_misses_clean(self):
        near = ("task-id", "docs/or-ask-provenance-2026-09-19", "password",
                _sk_key("A" * 15), _aws_key_id("A" * 7))
        for value in near:
            with self.subTest():
                self.assertEqual(0, S.count(value))
                self.assertEqual([], S.families(value))

    def test_public_example_rule_is_pinned(self):
        example = _public_example()
        self.assertEqual(1, S.count(example))
        stripped = S.strip_public_examples(example)
        self.assertEqual("", stripped)
        self.assertEqual(0, S.count(stripped))
        for pattern in S.STRICT:
            self.assertIsNone(pattern.search(stripped))
        other = _aws_key_id("A" * 16)
        self.assertNotEqual(example, other)
        self.assertGreaterEqual(S.count(other), 1)
        self.assertTrue(any(p.search(other) for p in S.STRICT))

    def test_log_message_after_a_hunk_is_scanned(self):
        value = _password_assignment()
        diff = "\n".join([
            "diff --git a/f b/f",
            "index 0000000..1111111 100644",
            "--- a/f",
            "+++ b/f",
            "@@ -1,1 +1,1 @@",
            "-old line",
            "+new line",
            "commit 0123456789abcdef",
            "    " + value,
        ])
        got = S.added_text(diff)
        self.assertIn(value, got)
        self.assertGreaterEqual(S.count(got), 1)

    def test_truncated_diff_still_scanned(self):
        value = _password_assignment()
        cut = "\n".join([
            "diff --git a/f b/f",
            "index 0000000..1111111 100644",
            "--- a/f",
            "+++ b/f",
            "@@ -1,1 +1,1 @@",
            "-old line",
            "+" + value,
        ])
        got = S.added_text(cut)
        self.assertIn(value, got)
        self.assertGreaterEqual(S.count(got), 1)
        stray = "\n".join([
            "diff --git a/f b/f",
            "index 0000000..1111111 100644",
            "--- a/f",
            "+++ b/f",
            "@@ -1,1 +1,1 @@",
            "-old line",
            "+new line",
            value,
        ])
        got_stray = S.added_text(stray)
        self.assertIn(value, got_stray)

    def test_concurrent_scans_agree(self):
        text = "\n".join(value for _, value in _UNION_FIXTURES)
        expected = (S.count(text), S.families(text))
        results = []
        lock = threading.Lock()

        def _scan():
            got = (S.count(text), S.families(text))
            with lock:
                results.append(got)

        threads = [threading.Thread(target=_scan) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(4, len(results))
        for got in results:
            self.assertEqual(expected, got)

    def test_sources_do_not_trip_the_scanner(self):
        for name in ("secret_scan.py", "test_secret_scan.py"):
            path = os.path.join(HERE, name)
            with self.subTest(source=name):
                with open(path, "r", encoding="utf-8") as handle:
                    self.assertEqual(0, S.count(handle.read()))
        gate = os.path.join(HERE, "commit_scan.py")
        with self.subTest(source="commit_scan.py"):
            with open(gate, "r", encoding="utf-8") as handle:
                self.assertEqual(0, S.count(handle.read()))
        hook = os.path.join(os.path.dirname(HERE), "pre_push_gate.py")
        with self.subTest(source="pre_push_gate.py"):
            with open(hook, "r", encoding="utf-8") as handle:
                body = S.strip_public_examples(handle.read())
            self.assertEqual(0, S.count(body))

    def test_refused_this_month_corpus(self):
        for name, value in _CORPUS_FIXTURES:
            with self.subTest(family=name):
                self.assertGreaterEqual(S.count(value), 1)
                self.assertIn(name, S.families(value))

    def test_registered_in_a_battery(self):
        path = os.path.join(os.path.dirname(HERE), "check_all.sh")
        with open(path, "r", encoding="utf-8") as handle:
            body = handle.read()
        row = ('run_check "secret-scan-self" '
               "python3 -B scripts/loop/test_secret_scan.py")
        self.assertEqual(1, body.count(row))


class BothGatesAtTheirEntryPoints(unittest.TestCase):
    """Both gates must reach the same verdict for the same value.

    The verdict lives in secret_scan.gate_refuses, the one place a caller
    routes through. These tests hold the verdicts each gate must reach at
    its entry point: for every corpus fixture either gate refused this
    month, and for the carve outs each gate already had (the documented
    public example at the commit gate, and context or removed lines at the
    push gate, which reads the narrower STRICT table).
    """

    def test_every_corpus_fixture_refused_at_commit_entry(self):
        for name, value in _CORPUS_FIXTURES:
            if value == _public_example():
                continue
            with self.subTest(family=name, length=len(value)):
                self.assertTrue(S.gate_refuses(value, "commit"))

    def test_every_corpus_fixture_refused_at_push_entry(self):
        for name, value in _CORPUS_FIXTURES:
            if value == _public_example():
                continue
            with self.subTest(family=name, length=len(value)):
                self.assertTrue(S.gate_refuses(value, "push-added"))

    def test_public_example_commit_refuses(self):
        self.assertTrue(S.gate_refuses(_public_example(), "commit"))

    def test_public_example_push_passes(self):
        stripped = S.strip_public_examples(_public_example())
        self.assertFalse(S.gate_refuses(stripped, "push-added"))
        self.assertFalse(S.gate_refuses(stripped, "push-any"))

    def test_union_value_in_context_passes_push(self):
        self.assertFalse(S.gate_refuses(_password_assignment(), "push-any"))

    def test_strict_value_in_context_blocks_push(self):
        self.assertTrue(S.gate_refuses(_sk_key("A" * 20), "push-any"))

    def test_clean_change_passes_both(self):
        text = "the quick brown fox jumps over the lazy dog"
        for stage in S.GATE_STAGES:
            with self.subTest(stage=stage):
                self.assertFalse(S.gate_refuses(text, stage))

    def test_hostile_input_refused(self):
        hostile = (None, 0, True, -1, float("nan"), b"x", [], ["x"], {},
                   {"a": 1}, (), {1, 2}, object())
        for value in hostile:
            for stage in ("commit", "push-added", "push-any"):
                with self.subTest(kind=type(value).__name__, stage=stage):
                    with self.assertRaises(S.ScanInputError):
                        S.gate_refuses(value, stage)

    def test_stage_that_is_not_text_refused(self):
        for stage in (None, 0, True, b"", [], {}):
            with self.subTest(stage=repr(stage)):
                with self.assertRaises(S.ScanInputError):
                    S.gate_refuses("clean text", stage)

    def test_unknown_stage_name_refused(self):
        for stage in ("", "committy", "push", "push-added ", "commit\n"):
            with self.subTest(stage=repr(stage)):
                with self.assertRaises(S.ScanInputError):
                    S.gate_refuses("clean text", stage)


if __name__ == "__main__":
    unittest.main()
