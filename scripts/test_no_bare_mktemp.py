"""No script under scripts/ runs a bare mktemp: every call names its template, under TMPDIR.

On macOS a bare mktemp, mktemp -d, or mktemp -t prefix ignores TMPDIR and lands in the host's per-user temp folder
(measured 2026-10-11: with TMPDIR pointed at a fresh folder, all four forms still returned /var/folders/.../T/). The
hermetic gate's sandbox (scripts/hermetic_test_check.py) denies that folder, so a bare call fails there with "mkdtemp
failed ... Operation not permitted". It reached the gate once through the fake gh in test_export_public.py
(b9f63f627), and a sweep on 2026-10-11 found seven more command sites. A call counts as templated only when one of
its arguments carries the XXX run that mktemp fills in, as in: mktemp -d "${TMPDIR:-/tmp}/<name>.XXXXXX".

Comment lines are prose and are not read. A call built as a Python argv list is not seen by this scan; none exists in
scripts/ today. An unreadable file is an offender, never a clean file.
"""
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
# A mktemp started by command substitution, $( or a backtick, and its arguments up to the first shell operator.
CALL = re.compile(r"(?:\$\(|`)\s*mktemp\b([^)`|;&<>\n]*)")


def bare_calls(text):
    """(line number, stripped line) for each mktemp call on a non-comment line whose arguments carry no template."""
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for match in CALL.finditer(line):
            if "XXX" not in match.group(1):
                out.append((number, line.strip()))
    return out


def tree_offenders(root=None):
    """Every bare call in a .sh or .py file under root (scripts/ when None), as 'path:line: text'."""
    root = root or HERE
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith((".sh", ".py")):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                out.append("%s: unreadable, so not known to be clean: %s" % (os.path.relpath(path, root), exc))
                continue
            for number, line in bare_calls(text):
                out.append("%s:%d: %s" % (os.path.relpath(path, root), number, line))
    return out


# Fixture text is split at "$" "(" and "`" "mktemp" so this file's own source never carries a call the scan reads.
BARE = {
    "dash d": "d=$" "(mktemp -d)",
    "no argument": 'f="$' '(mktemp)"',
    "backtick": "d=`" "mktemp -d`",
    "dash t prefix": "f=$" "(mktemp -t probe)",
    "redirect then or-echo": 'f="$' '(mktemp 2>/dev/null || echo /tmp/x-$$)"',
    "an X that is no template": "f=$" "(mktemp -t Xprobe)",
    "the XXX belongs to the or-echo, not to mktemp": 'f="$' '(mktemp 2>/dev/null || echo /tmp/x.XXXXXX)"',
}
TEMPLATED = {
    "dash d": 'd=$' '(mktemp -d "${TMPDIR:-/tmp}/x.XXXXXX")',
    "redirect then or-echo": 'f="$' '(mktemp "${TMPDIR:-/tmp}/x.XXXXXX" 2>/dev/null || echo y)"',
}


class BareCalls(unittest.TestCase):
    def test_each_bare_form_is_read_as_bare(self):
        for name, line in BARE.items():
            with self.subTest(name):
                self.assertEqual(bare_calls(line), [(1, line)])

    def test_a_call_naming_its_template_is_not_bare(self):
        for name, line in TEMPLATED.items():
            with self.subTest(name):
                self.assertEqual(bare_calls(line), [])

    def test_a_comment_line_is_prose_never_a_call(self):
        self.assertEqual(bare_calls("    # measured with HOME=$" "(mktemp -d) on that day"), [])


class Tree(unittest.TestCase):
    def test_no_script_under_scripts_runs_a_bare_mktemp(self):
        self.assertEqual(tree_offenders(), [])

    def test_an_unreadable_file_is_an_offender_never_a_clean_file(self):
        root = tempfile.mkdtemp(prefix="no-bare-mktemp-")
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "x.sh"), "w", encoding="utf-8") as fh:
            fh.write("echo clean\n")
        with mock.patch("builtins.open", side_effect=OSError("denied")):
            found = tree_offenders(root)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith("x.sh: unreadable"), found)


if __name__ == "__main__":
    unittest.main()
