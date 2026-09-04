"""What the undeclared-write refusal must keep true.

Two properties pull against each other here, and both are load bearing. It must
actually REFUSE an undeclared write, because that is the whole feature. And it
must FAIL OPEN on every unexpected condition, because a hook that blocks the
machine when its own parser is confused gets deleted within the hour and then
enforces nothing at all.

Most of this file is the second property. The first is one test.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lifecycle_hooks as L  # noqa: E402

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


def owns(task, path):
    """The real matcher's semantics: prefix for directories, exact for files."""
    for owned in task.get("ownedPaths", []):
        owned = owned.rstrip("/")
        if path == owned or path.startswith(owned + "/"):
            return True
    return False


ROOT = tempfile.mkdtemp()


def write_call(rel):
    return {"tool_name": "Write",
            "tool_input": {"file_path": os.path.join(ROOT, rel)}}


class ItRefusesAnUndeclaredWrite(unittest.TestCase):
    """The feature."""

    def test_a_path_no_open_task_declares_is_REFUSED(self):
        code, msg = L.decide(write_call("a.py"), ROOT,
                             [{"id": "t1", "ownedPaths": ["b.py"]}], owns)
        self.assertEqual(code, L.REFUSE)
        self.assertIn("a.py", msg)

    def test_the_refusal_explains_the_consequence_not_just_the_rule(self):
        """A refusal a person cannot act on gets worked around. This one names
        why the declaration matters: the scheduler compares declared write sets
        to decide what may run beside what."""
        msg = L.decide(write_call("a.py"), ROOT,
                       [{"id": "t1", "ownedPaths": ["b.py"]}], owns)[1]
        self.assertIn("scheduler", msg)
        self.assertIn("sbe task open", msg)

    def test_a_declared_path_is_allowed(self):
        self.assertEqual(
            L.decide(write_call("a.py"), ROOT,
                     [{"id": "t1", "ownedPaths": ["a.py"]}], owns)[0], L.ALLOW)

    def test_a_directory_declaration_covers_a_file_under_it(self):
        self.assertEqual(
            L.decide(write_call("pkg/x.py"), ROOT,
                     [{"id": "t", "ownedPaths": ["pkg/"]}], owns)[0], L.ALLOW)

    def test_any_one_open_task_declaring_it_is_enough(self):
        self.assertEqual(
            L.decide(write_call("a.py"), ROOT,
                     [{"id": "t1", "ownedPaths": ["b.py"]},
                      {"id": "t2", "ownedPaths": ["a.py"]}], owns)[0], L.ALLOW)


class ItFailsOpenOnEverythingItCannotUnderstand(unittest.TestCase):
    """The property that keeps it installed."""

    def test_an_unreadable_registry_ALLOWS_and_says_it_checked_nothing(self):
        """Silence here would be indistinguishable from a clean pass, which is
        how a control dies quietly."""
        code, msg = L.decide(write_call("a.py"), ROOT, None, owns)
        self.assertEqual(code, L.ALLOW)
        self.assertIn("NO-DATA", msg)
        self.assertIn("not a pass", msg)

    def test_a_tool_that_does_not_write_is_ignored(self):
        for tool in ("Read", "Bash", "Grep", "SomeToolAddedNextYear"):
            self.assertEqual(
                L.decide({"tool_name": tool, "tool_input": {"file_path": "x"}},
                         ROOT, [], owns)[0], L.ALLOW, tool)

    def test_a_payload_with_no_path_is_ignored(self):
        self.assertEqual(L.decide({"tool_name": "Write", "tool_input": {}},
                                  ROOT, [], owns)[0], L.ALLOW)

    def test_a_write_OUTSIDE_the_repository_is_not_this_hook_s_business(self):
        """A declaration is repository relative, so a temp file or a home file
        has nothing to be compared against."""
        self.assertEqual(
            L.decide({"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts"}},
                     ROOT, [], owns)[0], L.ALLOW)

    def test_an_empty_payload_is_ignored(self):
        self.assertEqual(L.decide({}, ROOT, [], owns)[0], L.ALLOW)
        self.assertEqual(L.decide(None, ROOT, [], owns)[0], L.ALLOW)

    def test_malformed_stdin_allows_rather_than_blocking_the_machine(self):
        import io
        real = sys.stdin
        try:
            sys.stdin = io.StringIO("this is not json")
            self.assertEqual(L.main([]), L.ALLOW)
        finally:
            sys.stdin = real

    def test_a_json_scalar_instead_of_an_object_allows(self):
        import io
        real = sys.stdin
        try:
            sys.stdin = io.StringIO('"a bare string"')
            self.assertEqual(L.main([]), L.ALLOW)
        finally:
            sys.stdin = real


class TheNotebookToolWritesToo(unittest.TestCase):
    def test_a_notebook_edit_is_judged_like_any_other_write(self):
        call = {"tool_name": "NotebookEdit",
                "tool_input": {"notebook_path": os.path.join(ROOT, "n.ipynb")}}
        self.assertEqual(L.decide(call, ROOT, [{"id": "t", "ownedPaths": ["x"]}],
                                  owns)[0], L.REFUSE)


class ThePrecedenceOrderIsDeclaredOnce(unittest.TestCase):
    """The trap this module exists not to deepen: instructions, rules, skills,
    hooks and memories are five overlapping policy systems, and an estate that
    adds enforcement without saying which layer wins ends up with rules nobody
    can reason about."""

    def test_the_order_is_stated_and_has_five_layers(self):
        self.assertEqual(len(L.ORDER), 5)

    def test_safety_and_founder_gates_come_first(self):
        """Nothing below may permit what these refuse, and no hook may grant an
        exception to them."""
        self.assertEqual(L.ORDER[0], "safety-and-founder-gates")

    def test_mechanical_hooks_outrank_written_rules(self):
        """Which is the whole point of building this at the tool layer: a rule
        that only exists as prose is advice."""
        self.assertLess(L.ORDER.index("mechanical-hooks"),
                        L.ORDER.index("written-rules"))

    def test_recalled_memory_is_last_and_is_never_authority(self):
        self.assertEqual(L.ORDER[-1], "recalled-memory")


class TheExitCodesAreTheContract(unittest.TestCase):
    def test_refuse_is_two_because_that_is_what_the_host_reads(self):
        """Asserted rather than assumed: the host blocks a tool call on exit 2
        specifically, and a hook returning 1 would fail silently as an error."""
        self.assertEqual(L.REFUSE, 2)
        self.assertEqual(L.ALLOW, 0)


if __name__ == "__main__":
    unittest.main()
