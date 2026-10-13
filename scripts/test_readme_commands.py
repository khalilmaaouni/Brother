#!/usr/bin/env python3
"""test_readme_commands: every command the README and the pain point pages
tell a reader to copy names a script that exists, with flags its parser knows.

WHY (DOC3, 1.1.3). Nothing checked that a command in README.md or a docs
page still matched the tree. A renamed script or a dropped flag leaves a copy
and paste command that fails for the reader, and every existing docs check
(test_readme_honesty, test_docs_gate, test_export_links) looks at claims and
links, never at commands.

HOW. Fenced blocks are parsed line by line. Shell blocks (bash, sh, shell,
console, zsh) give every line; unlabelled and text blocks give only lines
whose first word is a runner or a known unchecked tool; mermaid and other
languages are skipped; a `/brother` line is a prompt, never a command. Each
runner command is split on && || ; | & with shlex, its script path resolved
(plugin root forms map to bundle/runtime/), checked for existence, and every
--flag checked against the option strings the script's own argparse calls
define, read with ast. NOTHING IS EXECUTED AND NOTHING IS IMPORTED: a checked
script is only read as text.

FAIL DIRECTION. What cannot be checked is UNCHECKED, counted, printed, and
fails unless listed in UNCHECKED_OK with a reason. An unterminated fence, an
unsplittable line and an unparsable script are named problems, never passes.

Exit: 0 every assertion held, 1 one failed (the unittest contract every
check_all.sh suite uses).
"""
import ast
import os
import re
import shlex
import shutil
import sys
import tempfile
import unittest
from typing import Dict, List, Optional, Set, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from self_check_staged import DASHES, MODEL_CREDIT  # noqa: E402

RUNNERS = ("python3", "/usr/bin/python3", "sh", "bash")
UNCHECKED_ALLOWED = ("claude", "codex", "git", "gh", "cd", "ls", "cat", "export", "pip", "npm")
PAGE_LABELS = ("Source", "Example", "Type", "What Brother does", "Evidence left",
               "Limit", "What it does NOT do")
SHELL_LANGS = ("bash", "sh", "shell", "console", "zsh")
INTERP_OPTS = ("-B", "-u", "-I", "-X")
PLUGIN_ROOTS = ("${CLAUDE_PLUGIN_ROOT}", "${BROTHER_PLUGIN_ROOT}", "${PLUGIN_ROOT}")
SEPARATORS = ("&&", "||", ";", "|", "&")

#: A script and flag that cannot be checked against an argparse parser,
#: allowed with its reason. Keyed "<script> <flags>", the text an UNCHECKED
#: problem carries. TheReadme re-reads each script and requires every flag,
#: as "--name" or "name", as a string literal in it, so a reason cannot
#: outlive its code.
UNCHECKED_OK = {
    "scripts/loop/model_reachability.py --selftest":
        "hand parsed: main() compares its argv to the literal '--selftest'",
    "scripts/loop/loop_intake.py --selftest":
        "hand parsed: main() tests '--selftest' in sys.argv",
    "products/brothermode/tools/bm_vault.py --vault":
        "hand parsed: _parse() keys any --name; cmd_index reads args.get('vault')",
    "products/brothermode/tools/bm_vault.py --query --limit --fast --explain":
        "hand parsed: _parse() keys any --name; cmd_recall reads each by name",
}  # type: Dict[str, str]

#: Inline spans whose path is relative to another directory, as the prose
#: around them says ("In either product directory"). Checked there instead.
INLINE_ELSEWHERE = {
    "scripts/verify-install.sh": ("products/brothermode", "products/brothersbe"),
    "scripts/checksums.sh": ("products/brothermode", "products/brothersbe"),
}  # type: Dict[str, Tuple[str, ...]]

#: The real README carries at least this many checked runner commands
#: (measured 2026-10-12: 13). An emptied or unparsed README cannot pass.
README_FLOOR = 10

PAGES = ("done-without-running-tests", "headless-sign-in-lapses", "scheduled-runs-that-prompt",
         "ios-simulator-tooling", "late-release-failure-reruns-everything",
         "second-opinions-you-can-read", "say-it-get-a-simulator-run")

PAGES_DIR = os.path.join("docs", "pain-points")


class FenceError(ValueError):
    """An opened fence that never closes; carries the opening line number."""


def fenced_blocks(text: str) -> List[Tuple[str, int, List[str]]]:
    """(language, first content line number, lines) per fenced block. The
    language is the first word of the info string, lowercased, or "". An
    unterminated fence raises FenceError: never silently closed at EOF."""
    blocks = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^ {0,3}(`{3,}|~{3,})\s*(\S*)", lines[i])
        if not m:
            i += 1
            continue
        fence, lang = m.group(1), m.group(2).lower()
        start = i
        body = []
        i += 1
        closed = False
        while i < len(lines):
            c = re.match(r"^ {0,3}(`{3,}|~{3,})\s*$", lines[i])
            if c and c.group(1)[0] == fence[0] and len(c.group(1)) >= len(fence):
                closed = True
                break
            body.append(lines[i])
            i += 1
        if not closed:
            raise FenceError("unterminated fence opened at line %d" % (start + 1))
        blocks.append((lang, start + 2, body))
        i += 1
    return blocks


def _strip_env(words: List[str]) -> List[str]:
    """Drop leading NAME=value assignments (`HOME=/x python3 ...`)."""
    k = 0
    while k < len(words) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[k]):
        k += 1
    return words[k:]


def command_lines(blocks: List[Tuple[str, int, List[str]]]) -> List[Tuple[int, str]]:
    out = []
    for lang, first, body in blocks:
        if lang == "mermaid":
            continue
        if lang in SHELL_LANGS:
            shell = True
        elif lang in ("", "text"):
            shell = False
        else:
            continue
        pending, pending_no = "", 0
        for n, raw in enumerate(body):
            line = raw.strip()
            if pending:
                line = pending + " " + line
            else:
                pending_no = first + n
            if line.endswith("\\"):
                pending = line[:-1].rstrip()
                continue
            pending = ""
            if line.startswith("$ "):
                line = line[2:].strip()
            if not line or line.startswith("#") or line.startswith("/brother"):
                continue
            if not shell:
                words = _strip_env(line.split())
                if not words or words[0] not in RUNNERS + UNCHECKED_ALLOWED:
                    continue
            out.append((pending_no, line))
        if pending:
            out.append((pending_no, pending))
    return out


def split_chain(line: str) -> List[List[str]]:
    """Split a shell line on && || ; | & outside quotes. Raises ValueError
    on a line shlex cannot split (an unmatched quote)."""
    lex = shlex.shlex(line, posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    lex.commenters = ""
    parts, cur = [], []
    for tok in lex:
        if tok in SEPARATORS or (tok and set(tok) <= set(";&|")):
            if cur:
                parts.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        parts.append(cur)
    return parts


def script_of(argv: List[str]) -> Optional[str]:
    """The repo relative script path a runner command names, or None (not a
    runner, a -m or -c form, or no script argument)."""
    argv = _strip_env(argv)
    if not argv or argv[0] not in RUNNERS:
        return None
    for a in argv[1:]:
        if a in INTERP_OPTS:
            continue
        if a in ("-m", "-c") or a.startswith("-"):
            return None
        for r in PLUGIN_ROOTS:
            if a.startswith(r + "/runtime/"):
                return "bundle/runtime/" + a[len(r + "/runtime/"):]
        return a
    return None


def parser_flags(script_path: str) -> Optional[Set[str]]:
    """Option strings from add_argument calls and subcommand names from
    add_parser calls, read with ast (never imported, never executed). None
    when the file is not Python, does not parse, or defines no argparse use."""
    if not script_path.endswith(".py"):
        return None
    try:
        with open(script_path, "rb") as fh:
            tree = ast.parse(fh.read(), filename=script_path)
    except (OSError, SyntaxError, ValueError):
        return None
    flags = set()  # type: Set[str]
    found = False
    # `for key in ("a", "b"): p.add_argument("--" + key)`: the loop values,
    # keyed by the id of each add_argument call inside such a loop.
    looped = {}  # type: Dict[int, List[str]]
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and isinstance(node.iter, (ast.Tuple, ast.List))
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                        for e in node.iter.elts)):
            values = [e.value for e in node.iter.elts]
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    looped[id(sub)] = values
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name = node.func.attr
        strs = [a.value for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if name == "ArgumentParser":
            found = True
        elif name == "add_argument":
            found = True
            flags.update(s for s in strs if s.startswith("-"))
            first = node.args[0] if node.args else None
            if first is not None and not isinstance(first, ast.Constant):
                if (isinstance(first, ast.BinOp) and isinstance(first.op, ast.Add)
                        and isinstance(first.left, ast.Constant)
                        and isinstance(first.left.value, str)
                        and isinstance(first.right, ast.Name) and id(node) in looped):
                    flags.update(first.left.value + v for v in looped[id(node)])
                else:
                    return None  # a flag built some other way: cannot be read
        elif name == "add_parser" and strs:
            found = True
            flags.add(strs[0])
    if not found:
        return None
    flags.update(("-h", "--help"))
    return flags


def check_command(argv: List[str], root: str) -> List[str]:
    """Problems for one command. An UNCHECKED problem starts "UNCHECKED:"."""
    words = _strip_env(argv)
    if not words:
        return []
    if words[0] not in RUNNERS:
        if words[0] in UNCHECKED_ALLOWED:
            return []
        return ["UNCHECKED: runner %r is not checkable" % words[0]]
    script = script_of(words)
    if script is None:
        return ["UNCHECKED: %s names no script (a -m or -c form)" % " ".join(words)]
    path = os.path.join(root, script)
    if not os.path.isfile(path):
        return ["%s: script does not exist" % script]
    rest = words[words.index(script) + 1:] if script in words else words[2:]
    flags = []
    for a in rest:
        if a == "--":
            break
        if a.startswith("--"):
            flags.append(a.split("=", 1)[0])
    if not flags:
        return []
    known = parser_flags(path)
    if known is None:
        return ["UNCHECKED: %s %s: no argparse parser to check against"
                % (script, " ".join(flags))]
    return ["%s: flag %s is not defined by its parser" % (script, f)
            for f in flags if f not in known]


def inline_script_refs(text: str) -> List[Tuple[int, str]]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in re.finditer(r"`(?:python3|sh|bash)\s+(?:-[A-Za-z]\s+)*(scripts/[^`\s]+)", line):
            out.append((n, m.group(1)))
    return out


def _allowed(problem: str) -> bool:
    return problem.startswith("UNCHECKED: ") and \
        problem[len("UNCHECKED: "):].split(":", 1)[0] in UNCHECKED_OK


def _all_command_problems(text: str, root: str) -> Tuple[int, List[str]]:
    """(checked runner command count, problems with line numbers). An
    UNCHECKED problem listed in UNCHECKED_OK is dropped here, the one place
    both the README and the pages route through; every other one stays."""
    try:
        blocks = fenced_blocks(text)
    except FenceError as exc:
        return 0, [str(exc)]
    checked, problems = 0, []
    for n, line in command_lines(blocks):
        try:
            parts = split_chain(line)
        except ValueError as exc:
            problems.append("line %d: cannot split %r (%s)" % (n, line, exc))
            continue
        for argv in parts:
            words = _strip_env(argv)
            if words and words[0] in RUNNERS:
                checked += 1
            for p in check_command(argv, root):
                if not _allowed(p):
                    problems.append("line %d: %s" % (n, p))
    for n, script in inline_script_refs(text):
        bases = INLINE_ELSEWHERE.get(script, ("",))
        if not all(os.path.isfile(os.path.join(root, b, script)) for b in bases):
            problems.append("line %d: inline %s: script does not exist" % (n, script))
    return checked, problems


def check_page(path: str, text: str, root: str = ROOT) -> List[str]:
    problems = []
    lines = text.splitlines()
    pos = {}
    for label in PAGE_LABELS:
        for i, line in enumerate(lines):
            if line.startswith("**%s:**" % label):
                pos[label] = i
                break
        else:
            problems.append("missing label **%s:**" % label)
    order = [pos[l] for l in PAGE_LABELS if l in pos]
    if order != sorted(order):
        problems.append("labels out of order (Source first, as the README uses)")
    if "Source" in pos:
        src = lines[pos["Source"]]
        if not re.search(r"https?://", src):
            problems.append("Source line has no URL")
        if not re.search(r"\bopened \d{4}-\d{2}-\d{2}\b", src):
            problems.append("Source line has no 'opened YYYY-MM-DD' date")
    for n, line in enumerate(lines, 1):
        if any(d in line for d in DASHES):
            problems.append("line %d: em or en dash" % n)
        if MODEL_CREDIT.search(line):
            problems.append("line %d: model or tool credit" % n)
        for m in re.finditer(r"\]\(([^)\s]+)\)", line):
            target = m.group(1)
            if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                continue
            rel = target.split("#", 1)[0]
            if not os.path.exists(os.path.join(os.path.dirname(path), rel)):
                problems.append("line %d: link %s does not resolve" % (n, target))
    problems.extend(_all_command_problems(text, root)[1])
    return problems


def pages_missing(root: str) -> List[str]:
    return [p for p in PAGES
            if not os.path.isfile(os.path.join(root, PAGES_DIR, p + ".md"))]


def _read(path: str) -> str:
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8")


# A page that meets every rule of the contract; each fault fixture breaks one.
GOOD_PAGE = """# A pain, in the reader's words

**Source:** [issue](https://example.com/issues/1), created 2026-04-12, opened 2026-10-12, 3 reactions.

**Example:** One sentence a reader recognises.

**Type:**

```text
/brother do the thing
```

```bash
python3 scripts/tool.py --known
```

**What Brother does:** `scripts/tool.py` does it. See [the other page](other.md).

**Evidence left:** A receipt.

**Limit:** Short.

**What it does NOT do:** Nothing more.
"""

TOOL_PY = ("import argparse\n"
           "p = argparse.ArgumentParser()\n"
           "p.add_argument('--known')\n"
           "p.add_argument('--continue', dest='cont', action='store_true')\n"
           "s = p.add_subparsers()\n"
           "s.add_parser('run')\n"
           "for k in ('loop-a', 'loop-b'):\n"
           "    p.add_argument('--' + k)\n")

# Executing this would leave a marker: the no-write fixture.
MARKER_PY = ("import argparse, os\n"
             "open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'EXECUTED'), 'w').close()\n"
             "argparse.ArgumentParser().add_argument('--go')\n")

ARGV_PY = "import sys\nif '--selftest' in sys.argv:\n    print('ok')\n"


class Fixture(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="readme-commands-")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "scripts"))
        os.makedirs(os.path.join(self.root, "bundle", "runtime"))
        os.makedirs(os.path.join(self.root, PAGES_DIR))
        for name, body in (("tool.py", TOOL_PY), ("marker.py", MARKER_PY),
                           ("argv_only.py", ARGV_PY)):
            with open(os.path.join(self.root, "scripts", name), "w") as fh:
                fh.write(body)
        with open(os.path.join(self.root, "bundle", "runtime", "rt.py"), "w") as fh:
            fh.write(TOOL_PY)
        with open(os.path.join(self.root, PAGES_DIR, "other.md"), "w") as fh:
            fh.write("# other\n")

    def problems(self, text):
        return _all_command_problems(text, self.root)[1]


class TheCommands(Fixture):
    """REQ-DOC3-01 to 07 on planted fixtures, each failing only its own check."""

    def test_existing_script_passes_and_renamed_fails(self):
        ok = "```bash\npython3 scripts/tool.py --known x\n```\n"
        self.assertEqual(self.problems(ok), [])
        bad = ok.replace("tool.py", "tool_renamed.py")
        self.assertEqual(self.problems(bad),
                         ["line 2: scripts/tool_renamed.py: script does not exist"])

    def test_flags_read_from_the_parser(self):
        self.assertEqual(self.problems("```bash\npython3 -B scripts/tool.py --continue run\n```\n"), [])
        self.assertEqual(self.problems("```bash\npython3 scripts/tool.py --known=1\n```\n"), [])
        self.assertEqual(self.problems("```bash\npython3 scripts/tool.py --loop-b x\n```\n"), [])
        self.assertEqual(self.problems("```bash\npython3 scripts/tool.py --no-such-flag\n```\n"),
                         ["line 2: scripts/tool.py: flag --no-such-flag is not defined by its parser"])

    def test_argv_script_with_a_flag_is_unchecked(self):
        got = self.problems("```bash\npython3 scripts/argv_only.py --selftest\n```\n")
        self.assertEqual(len(got), 1)
        self.assertIn("UNCHECKED:", got[0])
        # no flag: nothing to check against, the existence check alone holds
        self.assertEqual(self.problems("```bash\npython3 scripts/argv_only.py\n```\n"), [])

    def test_unknown_runner_and_module_form_are_unchecked(self):
        got = self.problems("```bash\nnode x.js\npython3 -m pytest\n```\n")
        self.assertEqual(len(got), 2)
        self.assertTrue(all("UNCHECKED:" in p for p in got))
        self.assertEqual(self.problems("```bash\ngit status && claude plugin list\n```\n"), [])

    def test_unterminated_fence_is_a_problem(self):
        got = self.problems("intro\n```bash\npython3 scripts/tool.py --known\n")
        self.assertEqual(got, ["unterminated fence opened at line 2"])

    def test_mermaid_and_prompts_are_skipped(self):
        text = ("```mermaid\npython3 scripts/missing.py\n```\n"
                "```text\n/brother python3 scripts/missing.py\nplain words\n```\n")
        self.assertEqual(self.problems(text), [])
        self.assertEqual(command_lines(fenced_blocks(text)), [])

    def test_shell_block_details(self):
        text = ("```console\n$ python3 scripts/tool.py \\\n  --known 1\n# comment\n\n```\n"
                "~~~bash reference\nHOME=/x python3 scripts/tool.py --bogus | cat\n~~~\n")
        lines = command_lines(fenced_blocks(text))
        self.assertEqual(lines[0], (2, "python3 scripts/tool.py --known 1"))
        self.assertEqual(self.problems(text),
                         ["line 8: scripts/tool.py: flag --bogus is not defined by its parser"])

    def test_unmatched_quote_is_a_problem(self):
        got = self.problems("```bash\npython3 scripts/tool.py --known \"open\n```\n")
        self.assertEqual(len(got), 1)
        self.assertIn("cannot split", got[0])

    def test_plugin_root_forms_resolve_to_bundle_runtime(self):
        for var in PLUGIN_ROOTS:
            argv = ["python3", var + "/runtime/rt.py", "--known"]
            self.assertEqual(script_of(argv), "bundle/runtime/rt.py")
            self.assertEqual(check_command(argv, self.root), [])
        self.assertEqual(script_of(["python3", "-c", "print(1)"]), None)

    def test_inline_spans_are_existence_checked(self):
        text = "Run `python3 scripts/tool.py` then `python3 -B scripts/gone.py --x`.\n"
        self.assertEqual(inline_script_refs(text),
                         [(1, "scripts/tool.py"), (1, "scripts/gone.py")])
        self.assertEqual(self.problems(text),
                         ["line 1: inline scripts/gone.py: script does not exist"])

    def test_nothing_is_executed(self):
        before = sorted(os.listdir(os.path.join(self.root, "scripts")))
        self.assertEqual(self.problems("```bash\npython3 scripts/marker.py --go\n```\n"), [])
        self.assertEqual(parser_flags(os.path.join(self.root, "scripts", "marker.py")),
                         {"--go", "-h", "--help"})
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "scripts"))), before)
        self.assertNotIn("marker", sys.modules)

    def test_unparsable_script_reads_none(self):
        bad = os.path.join(self.root, "scripts", "broken.py")
        with open(bad, "w") as fh:
            fh.write("def (:\n")
        self.assertIsNone(parser_flags(bad))
        self.assertIsNone(parser_flags(os.path.join(self.root, "scripts", "argv_only.py")))
        dyn = os.path.join(self.root, "scripts", "dynamic.py")
        with open(dyn, "w") as fh:
            fh.write("import argparse\np = argparse.ArgumentParser()\np.add_argument(make())\n")
        self.assertIsNone(parser_flags(dyn))


class ThePageContract(Fixture):
    """REQ-DOC3-08: a conforming page, then one fault per rule."""

    def page(self, text):
        return check_page(os.path.join(self.root, PAGES_DIR, "p.md"), text, self.root)

    def test_good_page_conforms(self):
        self.assertEqual(self.page(GOOD_PAGE), [])

    def test_each_fault_fails_only_its_rule(self):
        cases = (
            (GOOD_PAGE.replace("**Limit:** Short.\n", ""), "missing label **Limit:**"),
            (GOOD_PAGE.replace("https://example.com/issues/1", "other.md"), "Source line has no URL"),
            (GOOD_PAGE.replace("opened 2026-10-12", "opened Oct 12"),
             "Source line has no 'opened YYYY-MM-DD' date"),
            (GOOD_PAGE.replace("Short.", "Short " + DASHES[0] + " really."), "line 21: em or en dash"),
            (GOOD_PAGE.replace("Nothing more.", "Nothing more. Drafted by " + "Co" + "dex."),
             "line 23: model or tool credit"),
            (GOOD_PAGE.replace("(other.md)", "(gone.md)"), "line 17: link gone.md does not resolve"),
            (GOOD_PAGE.replace("--known", "--unknown"),
             "line 14: scripts/tool.py: flag --unknown is not defined by its parser"),
        )
        for text, want in cases:
            self.assertEqual(self.page(text), [want], want)

    def test_order_and_empty_page(self):
        swapped = GOOD_PAGE.replace("**Example:** One sentence a reader recognises.\n\n", "")
        swapped = swapped.replace("**Limit:** Short.", "**Limit:** Short.\n\n**Example:** Late.")
        self.assertEqual(self.page(swapped),
                         ["labels out of order (Source first, as the README uses)"])
        self.assertEqual(len(self.page("")), len(PAGE_LABELS))

    def test_pages_missing(self):
        self.assertEqual(pages_missing(self.root), list(PAGES))
        with open(os.path.join(self.root, PAGES_DIR, PAGES[0] + ".md"), "w") as fh:
            fh.write(GOOD_PAGE)
        self.assertEqual(pages_missing(self.root), list(PAGES[1:]))


class TheReadme(unittest.TestCase):
    """REQ-DOC3-01, 06: the real README's commands, against the real tree."""

    def test_readme_commands_hold(self):
        checked, problems = _all_command_problems(_read(os.path.join(ROOT, "README.md")), ROOT)
        print("README: %d runner commands checked, %d problems" % (checked, len(problems)),
              file=sys.stderr)
        self.assertEqual(problems, [])
        self.assertGreaterEqual(checked, README_FLOOR)

    def test_every_unchecked_reason_still_matches_its_script(self):
        for key in UNCHECKED_OK:
            script, flags = key.split(" ", 1)
            with open(os.path.join(ROOT, script), "rb") as fh:
                tree = ast.parse(fh.read())
            literals = {n.value for n in ast.walk(tree)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            for flag in flags.split():
                self.assertTrue(flag in literals or flag[2:] in literals, (key, flag))
            self.assertIsNone(parser_flags(os.path.join(ROOT, script)), key)


class ThePages(unittest.TestCase):
    """REQ-DOC3-09 to 11 and 13: the seven pages exist, conform, are linked
    from the README, and reach the public export."""

    def test_all_pages_present(self):
        self.assertEqual(pages_missing(ROOT), [])

    def test_each_page_conforms(self):
        for name in PAGES:
            path = os.path.join(ROOT, PAGES_DIR, name + ".md")
            if not os.path.isfile(path):
                continue  # test_all_pages_present names it
            self.assertEqual(check_page(path, _read(path)), [], name)

    def test_readme_links_every_page(self):
        readme = _read(os.path.join(ROOT, "README.md"))
        linked = set(re.findall(r"\]\(docs/pain-points/([\w-]+)\.md\)", readme))
        self.assertEqual(sorted(set(PAGES) - linked), [])

    def test_export_allowlist_carries_the_folder(self):
        allow = _read(os.path.join(ROOT, "docs", "plan", "EXPORT-ALLOWLIST.txt")).splitlines()
        self.assertIn(PAGES_DIR.replace(os.sep, "/"), [l.strip() for l in allow])


if __name__ == "__main__":
    unittest.main()
