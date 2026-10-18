#!/usr/bin/env python3
"""C8.a (1.1.6): a push whose tree differs from a recorded whole-gate PASS tree ONLY in docs/plan/specs/ or
docs/plan/seeds/ markdown reuses that record; every other delta re-gates.

HOW. The fixture of scripts/test_pre_push_whole_gate.py (a throwaway repository, its pre-push ref line fed to
pre_push_gate.main(), every other check replaced by one that finds nothing) judges the rule at the entry point the
installed hook runs. Tree A carries a loop change and a PASS record written by gate_ledger.record_whole_gate, the
writer required_fast.sh calls; tree B is A plus the delta under test. Each case changes one condition. Path forms
git itself never emits (absolute, dot dot) are asked of gate_ledger.docs_only_delta directly. The last class is the
guard that keeps the allowlist honest: no gate row (scripts/required_fast.sh and every script it names) reads either
allowlisted prefix, and that guard is shown to fire on a fixture that does. Everything lives under one temporary
directory; no network.

Run: python3 -B scripts/test_whole_gate_docs_delta.py
"""
import ast
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate_ledger  # noqa: E402
import pre_push_gate as G  # noqa: E402
from test_pre_push_whole_gate import _Fixture, git  # noqa: E402

ROOT = os.path.dirname(HERE)
SPEC = "docs/plan/" + "specs/C8X.md"          # split so the guard below never reads this file as a gate row read
SEED = "docs/plan/" + "seeds/1.1.6/NOTE.md"


class _Delta(_Fixture):
    def recorded(self):
        """Tree A: a loop change, and the whole gate's PASS record for exactly that tree."""
        self.commit("scripts/loop/tool.py", "x = 2\n")
        self.record_for_head()
        return git(self.repo, "rev-parse", "HEAD^{tree}")

    def change(self, files, delete=()):
        for rel, text in files.items():
            self.write(rel, text)
        for rel in delete:
            os.unlink(os.path.join(self.repo, rel))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "delta")


class AllowlistedDeltaReusesTheRecord(_Delta):
    def test_an_allowlisted_delta_reuses_the_record(self):
        tree_a = self.recorded()
        self.change({SPEC: "one spec\n"})
        code, out = self.push()
        self.assertEqual(code, G.EXIT_OK, out)
        self.assertIn("whole-gate PASS on record for tree " + tree_a, out)
        self.assertIn("docs only delta", out)

    def test_many_spec_and_seed_files_and_a_deletion_reuse_the_record(self):
        self.write(SEED, "seed\n")
        self.recorded()
        files = {"docs/plan/specs/S%02d.md" % i: "s\n" for i in range(8)}
        files.update({"docs/plan/seeds/1.1.6/N%02d.md" % i: "n\n" for i in range(4)})
        self.change(files, delete=(SEED,))
        code, out = self.push()
        self.assertEqual(code, G.EXIT_OK, out)
        self.assertIn("13 file(s)", out)

    def test_an_exact_match_answers_as_before(self):
        tree_a = self.recorded()
        code, out = self.push()
        self.assertEqual(code, G.EXIT_OK, out)
        self.assertIn("whole-gate PASS on record for tree " + tree_a, out)
        self.assertNotIn("docs only delta", out)

    def test_a_second_docs_push_on_the_same_record_is_judged_on_its_own_delta(self):
        self.recorded()
        self.change({SPEC: "one\n"})
        self.assertEqual(self.push()[0], G.EXIT_OK)
        self.change({"scripts/loop/tool.py": "x = 3\n"})       # the same actor, the same record, a new delta
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)


class EachExcludedClassRegates(_Delta):
    EXCLUDED = (
        "docs/plan/BATTERY-EXPECTATIONS.json", "docs/plan/KEY-COMPONENTS.json", "docs/plan/loop-roles.json",
        "SYSTEM.md", "README.md", "docs/plan/specs/C8X.json", "docs/plan/notes.md", "docs/plan/specs/C8X.MD",
        "docs/plan/specsX/C8X.md", "docs/plan/specs/a\nb.md", "scripts/loop/tool.py",
    )

    def test_each_excluded_class_regates(self):
        self.recorded()
        commit_a = git(self.repo, "rev-parse", "HEAD")
        for rel in self.EXCLUDED:
            with self.subTest(rel=rel):
                git(self.repo, "reset", "-q", "--hard", commit_a)     # each delta is A plus this one file alone
                self.change({rel: "edit %r\n" % rel})
                code, out = self.push()
                self.assertEqual(code, G.EXIT_BLOCKED, (rel, out))

    def test_a_mixed_delta_of_one_spec_and_one_script_regates(self):
        self.recorded()
        self.change({SPEC: "spec\n", "scripts/other.py": "y = 1\n"})
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("scripts/other.py", out)

    def test_a_symlink_under_specs_regates(self):
        self.recorded()
        link = os.path.join(self.repo, "docs", "plan", "specs", "LINK.md")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink("../../../scripts/loop/tool.py", link)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "link")
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)

    def test_path_forms_git_never_emits_are_refused(self):
        ok = ["docs/plan/specs/C8.md", "docs/plan/seeds/1.1.6/REGISTER.md"]
        self.assertTrue(gate_ledger.docs_only_delta(ok))
        for bad in ([], None, "docs/plan/specs/C8.md", ["/docs/plan/specs/C8.md"], ["docs/plan/specs/../../x.md"],
                    ["docs/plan/specs/./C8.md"], ["docs/plan/specs//C8.md"], ["docs/plan/specs/C8.md", 7],
                    ["docs/plan/specs/a\x00b.md"], ["docs/plan/specs/a\nb.md"]):
            with self.subTest(bad=bad):
                self.assertFalse(gate_ledger.docs_only_delta(bad))


class TamperedRecordRefuses(_Delta):
    def test_a_tampered_record_refuses(self):
        tree_a = self.recorded()
        self.record_for_head()        # a second line, so the first is sealed by its prev_hash (the last never is)
        self.change({SPEC: "spec\n"})
        path = self.shared()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace('"duration_s":', '"duration_s":1', 1))   # one byte of line 1: the chain breaks
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)
        self.assertIn("corrupt", out)
        tree_b = git(self.repo, "rev-parse", "HEAD^{tree}")
        ok, why = gate_ledger.whole_gate_pass_for_delta(path, tree_b, tree_a, [SPEC])
        self.assertFalse(ok, why)

    def test_a_record_for_a_tree_git_cannot_read_refuses(self):
        self.commit("scripts/loop/tool.py", "x = 2\n")
        gate_ledger.append_ledger(self.shared(), {
            "schema": 1, "cut_id": gate_ledger.NO_CUT, "cut_version": "UNKNOWN", "gate_name": gate_ledger.WHOLE_GATE,
            "gate_argv": ["sh", "scripts/required_fast.sh"], "status": "PASS", "exit_code": 0,
            "started_unix": 1.0, "ended_unix": 2.0, "duration_s": 1.0, "worktree_key": "UNKNOWN",
            "tree_sha": "e" * 40, "inputs_hash": None, "code_hash": "a" * 64,
            "tool_versions": {"python3": "Python 3"}, "verdict_source": "full", "cache_key": None,
            "deadline_unix": None})
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)

    def test_an_unreadable_record_refuses(self):
        self.recorded()
        self.change({SPEC: "spec\n"})
        os.chmod(self.shared(), 0)
        self.addCleanup(os.chmod, self.shared(), 0o600)
        code, out = self.push()
        self.assertEqual(code, G.EXIT_BLOCKED, out)


class CorruptDiffRefuses(_Delta):
    """A diff git printed that does not parse as raw NUL separated entries refuses, even for an allowlisted path."""

    def test_a_diff_that_does_not_parse_refuses(self):
        self.recorded()
        self.change({SPEC: "spec\n"})
        real = gate_ledger._git_out
        for text in (":100644 100644 %s %s M\0%s\0:100644 100644 %s %s M\0" % ("a" * 40, "b" * 40, SPEC, "a" * 40,
                                                                                "b" * 40),
                     "100644 100644 x y M\0%s\0" % SPEC,           # no leading colon
                     ":100644 100644 x M\0%s\0" % SPEC,            # four fields, not five
                     UnicodeDecodeError("utf-8", b"\xff", 0, 1, "a name that does not decode")):
            with self.subTest(text=str(text)[:30]):
                def torn(repo, *args, text=text):
                    if args[:1] != ("diff",):
                        return real(repo, *args)
                    if isinstance(text, Exception):
                        raise text
                    return text
                with mock.patch.object(gate_ledger, "_git_out", torn):
                    code, out = self.push()
                self.assertEqual(code, G.EXIT_BLOCKED, out)
                self.assertIn("could not be read as regular files", out)


class PushedTreeIsNeverAnOption(_Delta):
    def test_a_pushed_tree_that_is_not_40_hex_is_never_handed_to_git(self):
        self.recorded()
        out = os.path.join(self.scratch, "written-by-git-diff")
        ok, why = gate_ledger.whole_gate_pass_in_repo(self.repo, "--output=" + out)
        self.assertFalse(ok, why)
        self.assertFalse(os.path.exists(out), "a pushed tree value reached git diff as an option")


# --- REQ-C8A-04: the allowlist is safe only while no gate row reads it. ---
PREFIXES = ("plan/specs", "plan/seeds")
NAMED = re.compile(r"(?:scripts|products|bundle|tests)/[A-Za-z0-9_./-]+\.(?:py|sh)")


def _docstring_ids(tree):
    found = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant):
            found.add(id(body[0].value))
    return found


def _allowlist_ids(tree):
    """The one place the prefixes may be written: gate_ledger's DOCS_DELTA_PREFIXES assignment."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "DOCS_DELTA_PREFIXES" for t in node.targets):
            found.update(id(n) for n in ast.walk(node.value))
    return found


def reads_of_allowlist(path):
    """(line, text) of every place a gate file names an allowlisted prefix in code, not in a comment or a docstring.
    ponytail: a literal grep; a path built from pieces other than 'specs' or 'seeds' would pass it."""
    with open(path, encoding="utf-8") as f:
        source = f.read()
    hits = []
    if path.endswith(".py"):
        tree = ast.parse(source)
        skip = _docstring_ids(tree) | _allowlist_ids(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                if any(p in node.value for p in PREFIXES) or node.value.strip("/") in ("specs", "seeds"):
                    hits.append((node.lineno, node.value[:80]))
    else:
        for n, line in enumerate(source.splitlines(), 1):
            if not line.lstrip().startswith("#") and any(p in line for p in PREFIXES):
                hits.append((n, line.strip()[:80]))
    return sorted(hits)


def gate_files(root):
    """scripts/required_fast.sh and every script a non comment line of it names, that exists in root."""
    rf = os.path.join(root, "scripts", "required_fast.sh")
    with open(rf, encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if not l.lstrip().startswith("#")]
    named = sorted({m for l in lines for m in NAMED.findall(l)})
    return [rf] + [os.path.join(root, p) for p in named if os.path.isfile(os.path.join(root, p))]


class NoGateRowReadsTheAllowlist(unittest.TestCase):
    def test_no_gate_row_reads_the_allowlist(self):
        files = gate_files(ROOT)
        self.assertGreater(len(files), 30, "the guard scanned too few gate files to mean anything")
        self.assertIn(os.path.join(ROOT, "scripts", "gate_ledger.py"), files)
        hits = {os.path.relpath(f, ROOT): reads_of_allowlist(f) for f in files}
        self.assertEqual({k: v for k, v in hits.items() if v}, {})

    def test_the_guard_fires_on_a_gate_file_that_reads_the_allowlist(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "scripts"))
            reader = os.path.join(d, "scripts", "reads_specs.py")
            with open(reader, "w") as f:
                f.write('"""Cites docs/plan/' 'specs/C8.md in a docstring, which is fine."""\n'
                        '# and docs/plan/' 'seeds in a comment\n'
                        'DOC = open("docs/plan/' 'specs/C8.md").read()\n'
                        'PARTS = ("docs", "plan", "seeds")\n')
            with open(os.path.join(d, "scripts", "required_fast.sh"), "w") as f:
                f.write('# scripts/ignored_in_comment.py\nrun_check "r" python3 scripts/reads_specs.py\n'
                        'ls docs/plan/' 'specs\n')
            files = gate_files(d)
            self.assertEqual([os.path.basename(p) for p in files], ["required_fast.sh", "reads_specs.py"])
            self.assertEqual([n for n, _ in reads_of_allowlist(reader)], [3, 4])
            self.assertEqual([n for n, _ in reads_of_allowlist(files[0])], [3])


if __name__ == "__main__":
    unittest.main()
