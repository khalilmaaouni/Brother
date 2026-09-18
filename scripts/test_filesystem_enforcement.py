#!/usr/bin/env python3
"""Regression suite for scripts/filesystem_enforcement.py (DOM-40.02).

Every attack named in the unit's brief gets its own test, built with real
symlinks, real hard links, and real nested directories rather than mocks:
this estate has a recorded lesson (a-mocked-tool-call-proves-the-handling-
not-what-the-tool-matches) that a mocked filesystem call proves the code
path was reached, never that the real tool matches what the test assumed.
Each test asserts both the verdict AND the named rule, per the brief.

Several attack tests place the escape INSIDE an otherwise-permitted write
root (the hard link, the git submodule, the git worktree) rather than
outside every root: that is deliberate. If the veto for that attack were
deleted, plain root containment would still ALLOW the path, so the test
would go from red to green on the exact mutation that matters, instead
of passing either way. That is the mutation-proof property, applied per
attack, not just at the one manual mutation run at the end of this file's
docstring's sibling (see the DONE-CHECK report for that run's own output).
"""
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import filesystem_enforcement as fe  # noqa: E402


class FilesystemEnforcementTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fe-test-")
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.read_root = os.path.join(base, "read_root")
        self.write_root = os.path.join(base, "write_root")
        self.immutable_root = os.path.join(base, "immutable_root")
        self.temp_root = os.path.join(base, "temp_root")
        self.outside = os.path.join(base, "outside")
        for d in (self.read_root, self.write_root, self.immutable_root,
                  self.temp_root, self.outside):
            os.makedirs(d)
        self.roots = {
            "read": [self.read_root],
            "write": [self.write_root],
            "immutable": [self.immutable_root],
            "temporary": [self.temp_root],
        }

    # -- baseline allows, so a broken check that refuses everything cannot
    #    pass this suite by accident -------------------------------------

    def test_allow_write_existing_file_in_write_root(self):
        p = os.path.join(self.write_root, "existing.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("x")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.ALLOW)
        self.assertEqual(v.rule, "write-root")

    def test_allow_write_new_file_not_yet_created(self):
        p = os.path.join(self.write_root, "not-yet-created.txt")
        self.assertFalse(os.path.exists(p))
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.ALLOW)
        self.assertEqual(v.rule, "write-root")

    def test_allow_read_in_read_root(self):
        p = os.path.join(self.read_root, "doc.md")
        v = fe.decide(p, self.roots, action="read")
        self.assertEqual(v.decision, fe.ALLOW)
        self.assertEqual(v.rule, "read-root")

    def test_refuse_write_in_read_root(self):
        p = os.path.join(self.read_root, "doc.md")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "read-only-root")

    def test_allow_read_of_write_root_file(self):
        p = os.path.join(self.write_root, "code.py")
        v = fe.decide(p, self.roots, action="read")
        self.assertEqual(v.decision, fe.ALLOW)
        self.assertEqual(v.rule, "write-root")

    def test_allow_read_of_immutable_root(self):
        p = os.path.join(self.immutable_root, "frozen.json")
        v = fe.decide(p, self.roots, action="read")
        self.assertEqual(v.decision, fe.ALLOW)
        self.assertEqual(v.rule, "immutable-root")

    def test_refuse_write_in_immutable_root(self):
        p = os.path.join(self.immutable_root, "frozen.json")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "immutable-root")

    def test_allow_write_and_read_in_temporary_root(self):
        p = os.path.join(self.temp_root, "scratch.tmp")
        for action in ("write", "read"):
            v = fe.decide(p, self.roots, action=action)
            self.assertEqual(v.decision, fe.ALLOW, msg=action)
            self.assertEqual(v.rule, "temporary-root", msg=action)

    def test_allow_plain_hidden_dotfile_inside_write_root(self):
        p = os.path.join(self.write_root, ".env-example")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("FOO=bar\n")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.ALLOW)
        self.assertEqual(v.rule, "write-root")

    # -- the named attacks -------------------------------------------------

    def test_attack_parent_directory_traversal(self):
        p = os.path.join(self.write_root, "..", "..", "etc", "passwd")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "outside-declared-roots")

    def test_attack_symlink_out_of_write_root(self):
        link = os.path.join(self.write_root, "escape")
        os.symlink(self.outside, link)
        p = os.path.join(link, "payload.txt")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "outside-declared-roots")
        self.assertIn("symlink", v.reason)

    def test_attack_hard_link_inside_write_root(self):
        # The escape lives entirely INSIDE the write root by name: plain
        # containment alone would allow it. Only the nlink veto refuses it.
        origin = os.path.join(self.outside, "secret.txt")
        with open(origin, "w", encoding="utf-8") as fh:
            fh.write("s")
        linked = os.path.join(self.write_root, "looks-local.txt")
        os.link(origin, linked)
        v = fe.decide(linked, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "hard-link")
        # Reading the same hard-linked path is not vetoed: only the write
        # side of this attack is dangerous (see module docstring).
        v_read = fe.decide(linked, self.roots, action="read")
        self.assertEqual(v_read.decision, fe.ALLOW)
        self.assertEqual(v_read.rule, "write-root")

    def test_attack_git_submodule_inside_write_root(self):
        sub = os.path.join(self.write_root, "vendor-sub")
        os.makedirs(sub)
        with open(os.path.join(sub, ".git"), "w", encoding="utf-8") as fh:
            fh.write("gitdir: ../.git/modules/vendor-sub\n")
        p = os.path.join(sub, "module.py")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "git-submodule")

    def test_attack_git_worktree_inside_write_root(self):
        wt = os.path.join(self.write_root, "alt-worktree")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w", encoding="utf-8") as fh:
            fh.write("gitdir: /somewhere/main/.git/worktrees/alt\n")
        p = os.path.join(wt, "file.py")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "git-worktree")

    def test_attack_path_assembled_from_parts(self):
        # Built from separate string parts at runtime, never one literal,
        # so a check that string-matched a hardcoded attack pattern would
        # not catch this the way resolving the real path does.
        parts = [self.write_root]
        parts.append("..")
        parts.append("..")
        parts.append("etc")
        parts.append("shadow")
        assembled = os.sep.join(parts)
        v = fe.decide(assembled, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "outside-declared-roots")

    def test_attack_system_temp_directory_not_declared(self):
        rogue = os.path.join(tempfile.gettempdir(),
                              "fe-rogue-%d" % os.getpid())
        v = fe.decide(rogue, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "outside-declared-roots")

    def test_attack_hidden_dotfile_symlink_escape(self):
        link = os.path.join(self.write_root, ".secret")
        os.symlink(self.outside, link)
        p = os.path.join(link, "creds.txt")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "outside-declared-roots")

    def test_attack_symlinked_parent_directory_escapes(self):
        linkdir = os.path.join(self.write_root, "linked-parent")
        os.symlink(self.outside, linkdir)
        p = os.path.join(linkdir, "child.txt")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "outside-declared-roots")

    def test_symlinked_parent_directory_staying_inside_is_allowed(self):
        real_child = os.path.join(self.write_root, "real-child")
        os.makedirs(real_child)
        linkdir = os.path.join(self.write_root, "linked-but-internal")
        os.symlink(real_child, linkdir)
        p = os.path.join(linkdir, "child.txt")
        v = fe.decide(p, self.roots, action="write")
        self.assertEqual(v.decision, fe.ALLOW)
        self.assertEqual(v.rule, "write-root")

    # -- contingencies named in the module docstring ------------------------

    def test_empty_roots_declaration_refuses_everything(self):
        p = os.path.join(self.write_root, "anything.txt")
        v = fe.decide(p, {}, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "empty-roots-declaration")
        v_none = fe.decide(p, None, action="write")
        self.assertEqual(v_none.decision, fe.REFUSE)
        self.assertEqual(v_none.rule, "empty-roots-declaration")

    def test_declared_root_that_does_not_exist(self):
        roots = {"write": [os.path.join(self._tmp.name, "never-created")]}
        p = os.path.join(self._tmp.name, "never-created", "file.txt")
        v = fe.decide(p, roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "no-usable-roots")

    def test_relative_path_is_refused(self):
        v = fe.decide("scripts/file.py", self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertEqual(v.rule, "relative-path")

    def test_unresolvable_path_with_embedded_null_byte(self):
        bad = self.write_root + "/bad\x00name"
        v = fe.decide(bad, self.roots, action="write")
        self.assertEqual(v.decision, fe.REFUSE)
        self.assertIn(v.rule, ("unresolvable", "internal-error"))

    def test_write_root_inside_read_only_root(self):
        base = os.path.join(self._tmp.name, "nested-a")
        outer_read = os.path.join(base, "repo")
        inner_write = os.path.join(outer_read, "scripts")
        os.makedirs(inner_write)
        roots = {"read": [outer_read], "write": [inner_write]}

        outer_only = os.path.join(outer_read, "README.md")
        v_write_outer = fe.decide(outer_only, roots, action="write")
        self.assertEqual(v_write_outer.decision, fe.REFUSE)
        self.assertEqual(v_write_outer.rule, "read-only-root")
        v_read_outer = fe.decide(outer_only, roots, action="read")
        self.assertEqual(v_read_outer.decision, fe.ALLOW)
        self.assertEqual(v_read_outer.rule, "read-root")

        inner_file = os.path.join(inner_write, "tool.py")
        v_write_inner = fe.decide(inner_file, roots, action="write")
        self.assertEqual(v_write_inner.decision, fe.ALLOW)
        self.assertEqual(v_write_inner.rule, "write-root")

    def test_immutable_root_nested_inside_write_root(self):
        base = os.path.join(self._tmp.name, "nested-b")
        outer_write = os.path.join(base, "repo")
        inner_immutable = os.path.join(outer_write, "vendor")
        os.makedirs(inner_immutable)
        roots = {"write": [outer_write], "immutable": [inner_immutable]}

        outer_file = os.path.join(outer_write, "app.py")
        v_write_outer = fe.decide(outer_file, roots, action="write")
        self.assertEqual(v_write_outer.decision, fe.ALLOW)
        self.assertEqual(v_write_outer.rule, "write-root")

        inner_file = os.path.join(inner_immutable, "lib.py")
        v_write_inner = fe.decide(inner_file, roots, action="write")
        self.assertEqual(v_write_inner.decision, fe.REFUSE)
        self.assertEqual(v_write_inner.rule, "immutable-root")
        v_read_inner = fe.decide(inner_file, roots, action="read")
        self.assertEqual(v_read_inner.decision, fe.ALLOW)
        self.assertEqual(v_read_inner.rule, "immutable-root")


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fe-cli-test-")
        self.addCleanup(self._tmp.cleanup)
        self.write_root = os.path.join(self._tmp.name, "write_root")
        os.makedirs(self.write_root)
        self.roots_json = os.path.join(self._tmp.name, "roots.json")
        with open(self.roots_json, "w", encoding="utf-8") as fh:
            json.dump({"write": [self.write_root]}, fh)

    def test_cli_exit_0_on_allow(self):
        p = os.path.join(self.write_root, "ok.txt")
        rc = fe.main(["--path", p, "--roots-json", self.roots_json,
                     "--action", "write"])
        self.assertEqual(rc, 0)

    def test_cli_exit_1_on_refuse(self):
        p = os.path.join(self._tmp.name, "outside.txt")
        rc = fe.main(["--path", p, "--roots-json", self.roots_json,
                     "--action", "write"])
        self.assertEqual(rc, 1)

    def test_cli_exit_2_when_roots_file_missing(self):
        missing = os.path.join(self._tmp.name, "does-not-exist.json")
        p = os.path.join(self.write_root, "ok.txt")
        rc = fe.main(["--path", p, "--roots-json", missing,
                     "--action", "write"])
        self.assertEqual(rc, 2)

    def test_cli_exit_2_when_roots_file_not_json_object(self):
        bad_json = os.path.join(self._tmp.name, "bad.json")
        with open(bad_json, "w", encoding="utf-8") as fh:
            fh.write("[1, 2, 3]")
        p = os.path.join(self.write_root, "ok.txt")
        rc = fe.main(["--path", p, "--roots-json", bad_json,
                     "--action", "write"])
        self.assertEqual(rc, 2)


class LastResortHandlerTest(unittest.TestCase):
    """The catch-all that exists to fail closed was UNTESTED: flipping it
    from REFUSE to ALLOW left all 29 other cases green, which is the exact
    shape this estate keeps paying for (a control nothing drives). Added by
    the orchestrator during verification, 2026-09-18."""

    def test_an_unexpected_error_while_deciding_refuses(self):
        class Exploding(object):
            """A path-like object whose resolution raises. A str subclass
            will not do: os.path treats it as an ordinary string and never
            calls __fspath__, so the first version of this case passed for
            the wrong reason."""

            def __fspath__(self):
                raise RuntimeError("boom from inside the path object")

        verdict = fe.decide(Exploding(), {"write": ["/tmp"]})
        self.assertEqual(verdict.decision, fe.REFUSE, verdict)
        # NOT asserted: which rule refused. The module fails closed EARLIER
        # (relative-path), so this input never reaches the catch-all. Stated
        # rather than papered over: the last-resort handler at the bottom of
        # decide() is still UNCOVERED, and flipping it to ALLOW leaves this
        # suite green. It is a net behind refusals that do fire, so the
        # behaviour is right; the gap is in the evidence, not the control.


if __name__ == "__main__":
    unittest.main()
