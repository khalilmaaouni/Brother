#!/usr/bin/env python3
"""FX-07.3: the push gate reads the one shared secret table, and refuses a
hostile argument instead of crashing on it.

scripts/pre_push_gate.py used to carry its own copy of what a credential looks
like. It now reads scripts/loop/secret_scan.py, the table the commit gate also
reads, over two scopes: the UNION over the ADDED text of the outgoing range,
and the STRICT table over the whole patch log, so a context line or a removed
line is still refused exactly as before. A refusal names the matched families
and prints no matched value.

The same file is the boundary a hostile caller reaches first, so its entry
points are pinned here too: a checkout that is not a path, or ref lines that
are not text (None is allowed there and means git handed the hook no ref
lines), comes back as NO-DATA; an argv the gate cannot use, or a flag it does
not know, is refused with the gate's own error. Never a raw AttributeError or
TypeError, and never a clean scan.

The git call is replaced by a scripted stand in, so these tests need no
repository and no child process. Every secret shaped fixture is assembled at
run time from parts, and nothing here prints a value.
"""
import contextlib
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pre_push_gate as G  # noqa: E402


#: Every hostile value the entry points are handed: a bool where text belongs,
#: a number, NaN, bytes, a list, a mapping, a set, a generator, an opaque
#: object. None is NOT hostile for the ref lines: it is git handing the hook
#: no ref lines at all.
HOSTILE_VALUES = (True, False, 7, 1.5, float("nan"), b"x", bytearray(b"x"),
                  ["refs"], {"a": 1}, {1, 2}, ("x" for _ in ()), object())

#: Hostile checkout paths, and the hostile argv shapes handed to main().
HOSTILE_PATHS = (None, 7, True, float("nan"), b"x", ["x"], object())
HOSTILE_ARGV = (True, 7, 1.5, float("nan"), b"x", bytearray(b"x"), "check",
                ["--json", 7], {"json": 1}, {1, 2}, object())


class _ScriptedResult(object):
    """The fields the gate reads from one git call's result."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""
        self.args = []


class _ScriptedGit(object):
    """A scripted stand in for the gate's own git call: the patch log call
    answers with the fixture, every other call answers empty and successful,
    so _scan_range runs end to end with no repository and no child process."""

    def __init__(self, patch_log=""):
        self.patch_log = patch_log
        self.calls = []

    def __call__(self, args, cwd=None, runner=None):
        self.calls.append(list(args))
        if args and args[0] == "log":
            return _ScriptedResult(self.patch_log)
        return _ScriptedResult("")


def _no_imported_roots(cwd, runner=None):
    """The imported history roots the gate asks for: none in a fixture."""
    return []


def _hunk_log(added, context=None):
    """A patch log fixture: one commit header, one file, one hunk whose added
    lines are `added` (a string or a list of strings) and whose optional
    context line is `context`."""
    if isinstance(added, str):
        added = [added]
    lines = [
        "commit 0123456789abcdef0123456789abcdef01234567",
        "Author: Fixture <fixture@example.invalid>",
        "Date: Mon, 28 Sep 2026 00:00:00 +0900",
        "",
        "    fixture commit",
        "",
        "diff --git a/fixture.txt b/fixture.txt",
        "index 1111111..2222222 100644",
        "--- a/fixture.txt",
        "+++ b/fixture.txt",
        "@@ -1,2 +1,3 @@",
    ]
    if context is not None:
        lines.append(" " + context)
    for line in added:
        lines.append("+" + line)
    return "\n".join(lines) + "\n"


class UnionReadsAddedTextOnly(unittest.TestCase):
    """The push scan reads the one shared table, over two scopes.

    R-FX-07-9: strip_public_examples() runs before any match, so the
    documented public example stays accepted.
    R-FX-07-10: a STRICT shape on a context or removed line still blocks.
    R-FX-07-11: a UNION only shape on ADDED text blocks even when no STRICT
    pattern matches.
    R-FX-07-12: a refusal names the matched families and prints no value.
    """

    def setUp(self):
        self._real = (G._git, G._imported_roots)
        self.addCleanup(self._restore)

    def _restore(self):
        G._git, G._imported_roots = self._real

    def _run(self, patch_log):
        G._git = _ScriptedGit(patch_log)
        G._imported_roots = _no_imported_roots
        return G._scan_range(["aaaa1111..bbbb2222"], "fixture range", G.ROOT)

    def _blocks(self, findings):
        return [f for f in findings if f[0] == G.BLOCK]

    def _detail(self, findings):
        blocks = self._blocks(findings)
        self.assertEqual(1, len(blocks),
                         "expected exactly one BLOCK, got %r" % (findings,))
        return blocks[0][2]

    def test_secret_shapes_is_the_shared_strict_table(self):
        self.assertIs(G.S.STRICT, G.SECRET_SHAPES)
        self.assertIs(G.S.KNOWN_PUBLIC_EXAMPLE_VALUES,
                      G.KNOWN_PUBLIC_EXAMPLE_VALUES)
        self.assertIs(G.S.strip_public_examples, G.strip_public_examples)
        self.assertEqual(4, len(G.SECRET_SHAPES))

    def test_loading_the_module_does_not_shadow_scripts(self):
        loop_dir = os.path.abspath(os.path.join(HERE, "loop"))
        self.assertNotEqual(loop_dir, os.path.abspath(sys.path[0]))
        self.assertEqual(os.path.join(loop_dir, "secret_scan.py"),
                         os.path.abspath(G.S.__file__))

    def test_fence_free_key_header_blocks(self):
        header = "BEGIN " + "RSA " + "PRIVATE KEY"
        detail = self._detail(self._run(_hunk_log(header)))
        self.assertIn("private-key-header", detail)
        self.assertNotIn(header, detail)

    def test_union_value_added_blocks(self):
        value = "hunter2" + "xyzzy"
        assignment = "pass" + "word = " + value
        detail = self._detail(self._run(_hunk_log(assignment)))
        self.assertIn("password-assignment", detail)
        self.assertIn("secret shape famil", detail)
        self.assertNotIn(value, detail)
        self.assertNotIn(assignment, detail)

    def test_union_value_in_context_is_not_refused(self):
        value = "hunter2" + "xyzzy"
        assignment = "pass" + "word = " + value
        findings = self._run(_hunk_log("an ordinary added line", assignment))
        self.assertEqual([], self._blocks(findings), findings)
        self.assertTrue(any(f[0] == G.OK for f in findings), findings)

    def test_strict_value_in_context_still_blocks(self):
        key_id = "AKIA" + "Q" * 16
        detail = self._detail(self._run(
            _hunk_log("an ordinary added line", "key id " + key_id)))
        self.assertIn("aws-key-id", detail)
        self.assertNotIn(key_id, detail)

    def test_added_then_removed_in_range_still_blocks(self):
        value = "hunter2" + "xyzzy"
        assignment = "pass" + "word = " + value
        log = _hunk_log(assignment) + (
            "diff --git a/fixture.txt b/fixture.txt\n"
            "index 3333333..4444444 100644\n"
            "--- a/fixture.txt\n"
            "+++ b/fixture.txt\n"
            "@@ -1,2 +1,1 @@\n"
            "-" + assignment + "\n")
        detail = self._detail(self._run(log))
        self.assertIn("password-assignment", detail)
        self.assertNotIn(value, detail)

    def test_union_value_in_a_commit_message_blocks(self):
        value = "s3cr3t" + "value1"
        assignment = "api" + "_key = " + value
        log = _hunk_log("an ordinary added line") + (
            "commit 89abcdef0123456789abcdef0123456789abcdef\n"
            "Author: Fixture <fixture@example.invalid>\n"
            "Date: Mon, 28 Sep 2026 00:00:01 +0900\n"
            "\n"
            "    " + assignment + "\n")
        detail = self._detail(self._run(log))
        self.assertIn("api-key-assignment", detail)
        self.assertNotIn(value, detail)

    def test_public_example_is_not_refused_on_added_text(self):
        example = G.KNOWN_PUBLIC_EXAMPLE_VALUES[0]
        findings = self._run(_hunk_log("key id " + example))
        self.assertEqual([], self._blocks(findings), findings)

    def test_another_value_of_the_example_shape_blocks(self):
        key_id = "AKIA" + "Q" * 16
        detail = self._detail(self._run(_hunk_log("key id " + key_id)))
        self.assertIn("aws-key-id", detail)
        self.assertNotIn(key_id, detail)

    def test_many_families_are_named_and_no_value_is_printed(self):
        password_value = "hunter2" + "xyzzy"
        api_value = "s3cr3t" + "value1"
        bearer_value = "abcdefgh" + "ijklmnop"
        added = [
            "pass" + "word = " + password_value,
            "api" + "_key = " + api_value,
            "bear" + "er " + bearer_value,
        ]
        detail = self._detail(self._run(_hunk_log(added)))
        for name in ("password-assignment", "api-key-assignment",
                     "bearer-token"):
            self.assertIn(name, detail)
        for value in (password_value, api_value, bearer_value):
            self.assertNotIn(value, detail)


class HostileInputIsRefused(unittest.TestCase):
    """A checkout that is not a path, or ref lines that are not text, is
    refused with NO-DATA. An argv the gate cannot use, or a flag it does not
    know, is refused with the gate's own error. Never a raw interpreter
    exception, and never a clean answer."""

    def test_the_refusal_error_is_a_value_error(self):
        self.assertTrue(issubclass(G.HostileInputError, ValueError))

    def test_entry_points_refuse_hostile_ref_lines(self):
        checks = (("check_correctness", G.check_correctness),
                  ("check_handback", G.check_handback),
                  ("check_collision", G.check_collision),
                  ("check_hermetic_tests", G.check_hermetic_tests))
        for label, check in checks:
            for bad in HOSTILE_VALUES:
                findings = check(G.ROOT, None, bad)
                self.assertEqual(G.NODATA, findings[0][0],
                                 "%s with %r" % (label, bad))
                self.assertEqual([], [f for f in findings if f[0] == G.OK],
                                 "%s with %r" % (label, bad))

    def test_check_hermetic_tests_refuses_hostile_ref_lines(self):
        for bad in HOSTILE_VALUES:
            findings = G.check_hermetic_tests(G.ROOT, None, bad)
            self.assertEqual(G.NODATA, findings[0][0], repr(bad))

    def test_entry_points_refuse_a_hostile_checkout(self):
        checks = (("check_correctness", G.check_correctness),
                  ("check_handback", G.check_handback),
                  ("check_collision", G.check_collision),
                  ("check_hermetic_tests", G.check_hermetic_tests))
        for label, check in checks:
            for bad in HOSTILE_PATHS:
                findings = check(bad, None, None)
                self.assertEqual(G.NODATA, findings[0][0],
                                 "%s with %r" % (label, bad))
                self.assertEqual([], [f for f in findings if f[0] == G.OK],
                                 "%s with %r" % (label, bad))

    def test_check_docs_current_refuses_a_hostile_checkout(self):
        for bad in HOSTILE_PATHS:
            findings = G.check_docs_current(bad)
            self.assertEqual(G.NODATA, findings[0][0], repr(bad))
            self.assertEqual([], [f for f in findings if f[0] == G.OK])

    def test_gate_refuses_a_hostile_checkout_and_ref_lines(self):
        for bad in HOSTILE_PATHS:
            findings = G.gate(cwd=bad)
            self.assertEqual(G.NODATA, findings[0][0], repr(bad))
            self.assertEqual([], [f for f in findings if f[0] == G.OK])
        for bad in HOSTILE_VALUES:
            findings = G.gate(cwd=G.ROOT, stdin_text=bad)
            self.assertEqual(G.NODATA, findings[0][0], repr(bad))
            self.assertEqual([], [f for f in findings if f[0] == G.OK])

    def test_main_refuses_a_hostile_argv_with_the_gates_own_error(self):
        sink = io.StringIO()
        for bad in HOSTILE_ARGV:
            with contextlib.redirect_stdout(sink), \
                    contextlib.redirect_stderr(sink):
                with self.assertRaises(G.HostileInputError):
                    G.main(bad)

    def test_main_refuses_an_unknown_flag_with_the_gates_own_error(self):
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), \
                contextlib.redirect_stderr(sink):
            with self.assertRaises(G.HostileInputError):
                G.main(["--no-such-flag"])

    def test_main_still_answers_a_help_request(self):
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), \
                contextlib.redirect_stderr(sink):
            self.assertEqual(G.EXIT_OK, G.main(["--help"]))


if __name__ == "__main__":
    unittest.main()
