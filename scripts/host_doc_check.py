#!/usr/bin/env python3
"""host_doc_check.py: the accuracy check for the two host install pages (HP1.c, spec docs/plan/specs/HP1.md, sections
5.4 and 10).

WHY THIS EXISTS. docs/how-to/install-codex.md and docs/how-to/install-antigravity.md teach a stranger to install
Brother on Codex and on Antigravity. On 2026-10-03 both were stale in ways a Markdown linter never sees: the Codex page
named a default binary that no longer exists and called v1.0.19 the current release, and the Antigravity page named
host `agy` at 1.0.0, carried evidence anchors that resolve to nothing and never said that the read tools bypass
PreToolUse. check_pages reads each page back against the tree and the live evidence rows and returns one refusal per
contradiction, naming the page, the line and the fact. An empty list is a pass; a NO-DATA line is a refusal too.

WHAT IS CHECKED:
- Every fenced shell command resolves: the script is in the tree, and its subcommand and flags exist in its argparser.
- Only the argv lists in SAFE_SHAPES run (the brother_install.py `status` and codex_hooks_install.py `--check` forms,
  with or without `--json`), matched exactly, through the caller's runner, against a throwaway home, and must exit 0.
- A Codex binary path the page names on a line about a default equals brother_paths.codex_bin({}).
- A host version the page names equals the version in the newest evidence row for that page's host, or the page names
  no number. A host name equals the row's host_name (or the basename of its host_bin_realpath).
- A release tag (`v1.2.3` anywhere, or a bare number on a line saying "current release") equals the umbrella version
  version_source.read_source returns.
- An anchor `<!-- evidence: <sha256> -->` equals the raw_out_sha256 of an evidence row for the page's host (the anchor
  rule of docs/architecture/ANTIGRAVITY-E2E-TEST-LOG.md, section L1b.6).
- The Antigravity page states, for each of BYPASS_TOOLS, on one line naming the tool, PreToolUse and "not" or "never",
  that the tool does not reach PreToolUse; each tool so listed is a string in scripts/brother_antigravity_hook.py and
  does not match the PreToolUse matcher of bundle/.antigravity-plugin/hooks.json.

STARTS NO PROCESS. The build screen refuses subprocess in this file, and running a page's script in this process would
start the Codex binary that script drives. So a safe listed command runs only through `runner(argv, cwd, env)`, which
returns the exit code; without a runner every safe listed command is a NO-DATA refusal, never a pass (the same
contract as tests/e2e/antigravity/doc_lint.py run_stranger_harness). For the same reason doc_assurance.check_commands is
not reused: that module imports subprocess and asks each script's --help in a child. The argparser is read from the
script's source with ast instead, so no line of the script runs.

Python 3.9 and 3.13, standard library only.
"""
import ast
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import brother_paths  # noqa: E402  the one Codex binary rule, codex_bin
import version_source  # noqa: E402  the one umbrella version reader, read_source

CODEX_PAGE = "docs/how-to/install-codex.md"
ANTIGRAVITY_PAGE = "docs/how-to/install-antigravity.md"
#: Each page and the host its facts are judged against.
PAGES = ((CODEX_PAGE, "codex"), (ANTIGRAVITY_PAGE, "antigravity"))
#: Hosts whose page must name the host binary at all (the Antigravity page names it as the rows do).
MUST_NAME_HOST = ("antigravity",)
HOOK_SCRIPT = "scripts/brother_antigravity_hook.py"
HOOKS_JSON = "bundle/.antigravity-plugin/hooks.json"
#: The read tools the Antigravity host never sends to PreToolUse, each stated on the Antigravity page.
BYPASS_TOOLS = ("view_file", "list_dir", "grep_search")
#: The binary names a fenced command may start a host with. A fenced command starting one names that host.
HOST_BINARIES = frozenset(("antigravity", "agy", "antigravity-ide", "codex", "claude"))

#: THE SAFE LIST, exact argv shapes. A page cannot add a command by wording it differently: any other order, flag,
#: prefix or redirection is not on the list and does not run. `$HOME` and `$TARGET_REPO` are bound to the throwaway home.
_STATUS = ("python3", "scripts/brother_install.py", "status", "--codex-home", "$HOME/.codex")
_CHECK = ("python3", "scripts/codex_hooks_install.py", "--check", "--codex-home", "$HOME/.codex")
SAFE_SHAPES = frozenset((
    _STATUS, _STATUS + ("--json",), _STATUS + ("--allow-default-home",), _STATUS + ("--allow-default-home", "--json"),
    _CHECK, _CHECK + ("--allow-default-home",), _CHECK + ("--cwd", "$TARGET_REPO"),
    _CHECK + ("--allow-default-home", "--cwd", "$TARGET_REPO"),
))

SHELL_FENCES = frozenset(("bash", "sh", "shell", "console", "zsh"))
SHELL_OPERATORS = frozenset(("|", "||", "&&", ";", "&", "<", ">", ">>", "<<", "2>", "2>>", "&>", "2>&1"))
PYTHON_RE = re.compile(r"python(?:3(?:\.\d+)?)?")
PYTHON_OPTIONS = frozenset(("-B", "-u", "-I", "-E", "-s", "-S", "-O", "-OO", "-q"))
ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([^`\s]*)")
ANCHOR_RE = re.compile(r"<!--\s*evidence:\s*(.*?)\s*-->")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
HOST_NAME_RE = re.compile(r"\bhost\s+`([^`\s]+)`", re.I)
HOST_LINE_RE = re.compile(r"\b(?:host|antigravity|agy|codex|claude)\b", re.I)
VERSION_RE = re.compile(r"\bversion\s+`?(\d+(?:\.\d+)+)`?", re.I)
TAG_RE = re.compile(r"(?<![\w.])v(\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?)(?![\w-])(?!\.\w)")
BARE_RE = re.compile(r"(?<![\w.])(\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?)(?![\w-])(?!\.\w)")
CURRENT_RE = re.compile(r"\bcurrent\s+release\b", re.I)
DEFAULT_RE = re.compile(r"\bdefault", re.I)
PATH_TOKEN_RE = re.compile(r"(?<![^\s`(\"'])(?:~/|/)[^\s`'\"<>()]*")
NEGATION_RE = re.compile(r"\b(?:not|never)\b", re.I)
TOOL_RE = re.compile(r"(?<![\w-])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![\w-])")
VERSION_NUMBER_RE = re.compile(r"\d+(?:\.\d+)+")
NO_VALUE_ACTIONS = frozenset(("store_true", "store_false", "store_const", "append_const", "count", "help", "version"))
HELP_FLAGS = {"-h": "none", "--help": "none"}
MAX_PAGE_BYTES = 2000000
MAX_SCRIPT_BYTES = 4000000
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
FACT_KEYS = ("default_paths", "host_names", "host_versions", "release_tags", "anchors", "bypass_tools")


def _usable(value):
    """A non-empty path string with no NUL byte. Anything else (None, a number, a bool, a list) is not a path."""
    return isinstance(value, str) and bool(value.strip()) and "\x00" not in value


def _show(value):
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _read_text(path, cap):
    """(text, problem). Never raises: a missing, unreadable, oversized or non UTF-8 file is a problem line."""
    if not os.path.isfile(path):
        return None, "%s is missing" % path
    try:
        with open(path, "rb") as fh:
            data = fh.read(cap + 1)
    except OSError as exc:
        return None, "%s cannot be read: %s" % (path, exc)
    if len(data) > cap:
        return None, "%s is larger than %d bytes" % (path, cap)
    try:
        return data.decode("utf-8"), ""
    except UnicodeDecodeError as exc:
        return None, "%s is not UTF-8: %s" % (path, exc)


def _layout(text):
    """(prose, fences): prose is [(line, text)] outside every fence; each fence is {line, info, body, closed}."""
    prose, fences, current = [], [], None
    for number, line in enumerate(text.splitlines(), 1):
        mark = FENCE_RE.match(line)
        if current is None:
            if mark:
                current = {"line": number, "mark": mark.group(1), "info": mark.group(2).lower(), "body": [],
                           "closed": False}
                fences.append(current)
            else:
                prose.append((number, line))
            continue
        if mark and mark.group(1)[0] == current["mark"][0] and len(mark.group(1)) >= len(current["mark"]) \
                and not mark.group(2):
            current["closed"] = True
            current = None
            continue
        current["body"].append((number, line))
    return prose, fences


def _split(command):
    """(env, argv, problem): the leading NAME=value words, the argv up to the first shell operator, and why the
    command cannot be split ("" when it can)."""
    try:
        words = shlex.split(command, comments=True)
    except ValueError as exc:
        return {}, [], "cannot be split as a shell command: %s" % exc
    env = {}
    while words and ENV_RE.match(words[0]):
        key, _sep, value = words.pop(0).partition("=")
        env[key] = value
    cut = next((i for i, word in enumerate(words) if word in SHELL_OPERATORS), len(words))
    return env, words[:cut], ""


def _record(line, command, error=""):
    env, argv, problem = _split(command) if command else ({}, [], "")
    return {"line": line, "command": command, "argv": argv, "env": env, "error": error or problem}


def fenced_commands(text: str) -> List[Dict[str, object]]:
    """Every command in the page's shell fences (bash, sh, shell, console, zsh), one record per logical command:
    {line, command, argv, env, error}. Continuation lines ending in a backslash are joined, a leading `$ ` prompt and
    comment lines are dropped, NAME=value words before the program go to `env`, and `argv` stops at the first shell
    operator. A fence never closed, or a continuation running past its fence, is a record whose `error` says so."""
    if not isinstance(text, str):
        raise ValueError("fenced_commands needs the page text as a str, got %s" % type(text).__name__)
    out = []
    for fence in _layout(text)[1]:
        if fence["info"] not in SHELL_FENCES:
            continue
        pending, start = [], 0
        for number, raw in fence["body"]:
            line = raw.strip()
            if not pending:
                if not line or line.startswith("#"):
                    continue
                if line.startswith("$ "):
                    line = line[2:].strip()
                start = number
            if line.endswith("\\"):
                pending.append(line[:-1].strip())
                continue
            pending.append(line)
            out.append(_record(start, " ".join(part for part in pending if part)))
            pending = []
        if pending:
            out.append(_record(start, " ".join(part for part in pending if part),
                               "a line continuation runs past the end of its fenced block"))
        if not fence["closed"]:
            out.append(_record(fence["line"], "", "the fenced block opened here is never closed"))
    return out


def _call_name(node):
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _receiver(node):
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id
    return ""


def _strings(node):
    return [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]


def _value_kind(node):
    """"none" for a flag that takes no value, "optional" for one that may, "one" for one that takes a value."""
    keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
    action, nargs = keywords.get("action"), keywords.get("nargs")
    if action is not None and not isinstance(action, ast.Constant):
        return "optional"
    if isinstance(action, ast.Constant) and action.value in NO_VALUE_ACTIONS:
        return "none"
    if isinstance(nargs, ast.Constant) and nargs.value == 0:
        return "none"
    if isinstance(nargs, ast.Constant) and nargs.value in ("?", "*"):
        return "optional"
    return "one"


def _argparse_of(tree):
    """What the script's argparser offers, read from its source: {argparse, subs, sub_required, flags, loose, words}.
    `flags` maps "" (the root parser) or a subcommand to {flag: value kind}; `loose` holds flags whose parser could not
    be told apart, offered everywhere; `words` is every string constant, the modes a script without argparse reads.
    A helper that adds flags to its parameter (brother_install.py's common(p)) gives them to each parser passed in."""
    facts = {"argparse": False, "subs": set(), "sub_required": False, "flags": {}, "loose": {}, "words": set()}
    owners, aliases, params = {}, {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            facts["words"].add(node.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params[node.name] = [arg.arg for arg in node.args.posonlyargs + node.args.args]
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "ArgumentParser":
                facts["argparse"] = True
            elif name == "add_parser":
                facts["subs"].update(_strings(node)[:1])
            elif name == "add_subparsers":
                facts["sub_required"] = facts["sub_required"] or any(
                    kw.arg == "required" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                    for kw in node.keywords)
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Call):
            target, call = node.targets[0].id, node.value
            name = _call_name(call)
            if name == "ArgumentParser":
                owners[target] = ""
            elif name == "add_parser" and _strings(call):
                owners[target] = _strings(call)[0]
            elif name in ("add_argument_group", "add_mutually_exclusive_group") and _receiver(call):
                aliases[target] = _receiver(call)
    for _round in range(8):
        for target, source in aliases.items():
            if source in owners and target not in owners:
                owners[target] = owners[source]
    helper, calls = {}, []

    def visit(node, func):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                name, receiver = _call_name(child), _receiver(child)
                if name == "add_argument":
                    flags = {word: _value_kind(child) for word in _strings(child) if word.startswith("-")}
                    if func and receiver in params.get(func, ()):
                        helper.setdefault((func, receiver), {}).update(flags)
                    elif receiver in owners:
                        facts["flags"].setdefault(owners[receiver], {}).update(flags)
                    else:
                        facts["loose"].update(flags)
                elif isinstance(child.func, ast.Name) and child.func.id in params:
                    names = params[child.func.id]
                    for index, arg in enumerate(child.args[:len(names)]):
                        if isinstance(arg, ast.Name) and arg.id in owners:
                            calls.append((child.func.id, names[index], owners[arg.id]))
            visit(child, func)

    visit(tree, "")
    used = set()
    for func, param, owner in calls:
        if (func, param) in helper:
            facts["flags"].setdefault(owner, {}).update(helper[(func, param)])
            used.add((func, param))
    for key, flags in helper.items():
        if key not in used:
            facts["loose"].update(flags)
    return facts


def _script_facts(path):
    """(facts, problem) for one script file in the tree."""
    text, problem = _read_text(path, MAX_SCRIPT_BYTES)
    if problem:
        return None, problem
    try:
        return _argparse_of(ast.parse(text)), ""
    except (SyntaxError, ValueError, RecursionError) as exc:
        return None, "%s cannot be parsed to read its argparser: %s" % (path, type(exc).__name__)


def _resolve_words(label, facts, words):
    """Refusals for the words after the script: the subcommand must be one the argparser offers, and each flag one the
    parser it reaches offers. A script without argparse offers no flag, and its first word must be a string it reads."""
    out = []
    if not facts["argparse"]:
        mode_seen = False
        for word in words:
            if word.startswith("-") and len(word) > 1:
                out.append("passes %s to %s, which builds no argparser to offer it" % (word, label))
            elif not mode_seen:
                mode_seen = True
                if word not in facts["words"]:
                    out.append("passes mode %s to %s, a word that script never reads" % (word, label))
        return out
    subs, sub, index = facts["subs"], None, 0
    while index < len(words):
        word = words[index]
        if word == "--":
            break
        if word.startswith("-") and len(word) > 1:
            name = word.split("=", 1)[0]
            offered = dict(facts["loose"])
            offered.update(HELP_FLAGS)
            offered.update(facts["flags"].get("" if sub is None else sub, {}))
            if name not in offered:
                out.append("passes %s to %s%s, which its argparser does not offer" % (
                    name, label, "" if sub is None else " " + sub))
            elif "=" not in word and index + 1 < len(words):
                kind = offered[name]
                if kind == "one" or (kind == "optional" and not words[index + 1].startswith("-")):
                    index += 1
        elif subs and sub is None:
            if word not in subs:
                out.append("names subcommand %s, which %s does not offer (it offers %s)" % (
                    word, label, ", ".join(sorted(subs))))
                return out
            sub = word
        index += 1
    if subs and sub is None and facts["sub_required"]:
        out.append("names no subcommand of %s, which requires one of %s" % (label, ", ".join(sorted(subs))))
    return out


def _tree_path(word):
    """The relative tree path a page names, or None when the word is absolute, climbs out or is not literal."""
    if not word or os.path.isabs(word) or "$" in word or "\\" in word or word.startswith("~"):
        return None
    path = word[2:] if word.startswith("./") else word
    if not path or ".." in path.split("/"):
        return None
    return path


def _resolve_python(root, argv):
    index = 1
    while index < len(argv) and argv[index] in PYTHON_OPTIONS:
        index += 1
    if index >= len(argv):
        return ["`%s` starts an interpreter and names no script in the tree" % " ".join(argv)]
    word = argv[index]
    if word == "-m":
        module = argv[index + 1] if index + 1 < len(argv) else ""
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module):
            return ["runs module %s, which is not a module name" % _show(module)]
        base = os.path.join(root, *module.split("."))
        script = next((p for p in (base + ".py", os.path.join(base, "__main__.py")) if os.path.isfile(p)), None)
        if script is None:
            return ["runs module %s, which is not in the tree" % module]
        label, rest = module, argv[index + 2:]
    elif word.startswith("-"):
        return ["passes %s to the interpreter, which runs no script in the tree" % word]
    else:
        rel = _tree_path(word)
        if rel is None:
            return ["runs %s, which is not a path in the tree" % word]
        script = os.path.join(root, rel)
        if not os.path.isfile(script):
            return ["runs %s, which is not in the tree" % rel]
        label, rest = rel, argv[index + 1:]
    facts, problem = _script_facts(script)
    if problem:
        return [problem]
    return _resolve_words(label, facts, rest)


def resolve_command(root: str, command: str) -> List[str]:
    """Refusals for one fenced command (empty when it resolves). A `python3` command must run a script or module that
    is in the tree, with a subcommand and flags its argparser offers; `sh`/`bash` and a direct `scripts/...` call must
    name a script in the tree. A NAME=value prefix is not part of the program. Any other program (git, cd, a host
    binary) is not the tree's to resolve here; the host binary names are checked as page facts."""
    if not _usable(root) or not os.path.isdir(root):
        return ["NO-DATA: root %s is not a checkout directory, so the command cannot be resolved" % _show(root)]
    if not isinstance(command, str):
        return ["the command is a %s, not a string" % type(command).__name__]
    if not command.strip() or "\x00" in command:
        return ["the command is empty or carries a NUL byte"]
    _env, argv, problem = _split(command)
    if problem:
        return [problem]
    if not argv:
        return []
    program = os.path.basename(argv[0])
    if PYTHON_RE.fullmatch(program):
        return _resolve_python(root, argv)
    if program in ("sh", "bash", "zsh") and len(argv) > 1 and not argv[1].startswith("-"):
        rel = _tree_path(argv[1])
        if rel is None or not os.path.isfile(os.path.join(root, rel)):
            return ["runs %s, which is not in the tree" % argv[1]]
        return []
    if argv[0].startswith(("scripts/", "./scripts/")):
        rel = _tree_path(argv[0])
        if rel is None or not os.path.isfile(os.path.join(root, rel)):
            return ["runs %s, which is not in the tree" % argv[0]]
    return []


def _home_problem(home):
    """Why `home` is not a throwaway home ("" when it is): it must be an existing absolute directory that is neither a
    filesystem root nor the user's own home nor a directory holding it."""
    if not _usable(home):
        return "the throwaway home is %s, not a path" % _show(home)
    if not os.path.isabs(home):
        return "the throwaway home %s is not absolute" % home
    real = os.path.realpath(home)
    if not os.path.isdir(real):
        return "the throwaway home %s is not a directory" % home
    if real == os.path.dirname(real):
        return "the throwaway home %s is a filesystem root" % home
    account = os.path.realpath(os.path.expanduser("~"))
    if real == account or account.startswith(real.rstrip(os.sep) + os.sep):
        return "%s is the user's own home or holds it, and a page command runs against a throwaway home only" % home
    return ""


def _expand(word, env):
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: env[m.group(1) or m.group(2)], word)


def run_safe_commands(root: str, home: str, commands: List[Dict[str, object]], runner=None) -> List[str]:
    """Runs the commands whose whole word list is one of SAFE_SHAPES, through `runner(argv, cwd, env)` (an int exit
    code back), with cwd the checkout and env HOME and TARGET_REPO inside the throwaway `home`; refusals back. Every
    other command is never run. A safe listed command with no runner, a runner that raises or returns no int, or an
    exit code other than 0 is a refusal: an exit code nobody saw is NO-DATA, never 0."""
    if not _usable(root) or not os.path.isdir(root):
        return ["NO-DATA: root %s is not a checkout directory, so no page command ran" % _show(root)]
    if not isinstance(commands, list):
        return ["the commands are a %s, not a list of fenced command records" % type(commands).__name__]
    home_problem = _home_problem(home)
    out = []
    for position, item in enumerate(commands, 1):
        line = item.get("line") if isinstance(item, dict) else None
        command = item.get("command") if isinstance(item, dict) else None
        if not isinstance(command, str) or isinstance(line, bool) or not isinstance(line, int) or line < 1:
            out.append("command record %d is not a fenced command record {line, command}" % position)
            continue
        try:
            words = shlex.split(command, comments=True)
        except ValueError:
            words = None
        if words is None or tuple(words) not in SAFE_SHAPES:
            continue
        where = "line %d: `%s`" % (line, command)
        if home_problem:
            out.append("%s is on the safe list but did not run: %s" % (where, home_problem))
            continue
        if not callable(runner):
            out.append("%s NO-DATA: on the safe list, but no command runner was given and this checker starts no "
                       "process, so its exit code is unknown and the page is not proven" % where)
            continue
        target = os.path.join(home, "target-repo")
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            out.append("%s NO-DATA: the throwaway target %s cannot be made: %s" % (where, target, exc))
            continue
        env = {"HOME": home, "TARGET_REPO": target, "PATH": os.environ.get("PATH") or os.defpath}
        argv = [_expand(word, env) for word in words]
        try:
            code = runner(argv, root, dict(env))
        except Exception as exc:  # the caller's runner failing is a refusal for this command, never a raise
            out.append("%s NO-DATA: the command runner raised %s" % (where, type(exc).__name__))
            continue
        if isinstance(code, bool) or not isinstance(code, int):
            out.append("%s NO-DATA: the command runner returned %s, not an exit code" % (where, _show(code)))
            continue
        if code != 0:
            out.append("%s exits %d against a throwaway home, and a page command must exit 0" % (where, code))
    return out


def _codex_paths(line):
    for match in PATH_TOKEN_RE.finditer(line):
        token = match.group(0).rstrip(".,;:!?")
        if os.path.basename(token) == "codex":
            yield token


def _fact_rows(text):
    """[(line, key, value)] in page order, the facts page_facts groups by key."""
    prose, fences = _layout(text)
    rows = []
    for number, line in prose:
        if DEFAULT_RE.search(line):
            rows.extend((number, "default_paths", path) for path in _codex_paths(line))
        for match in HOST_NAME_RE.finditer(line):
            rows.append((number, "host_names", match.group(1)))
        if HOST_LINE_RE.search(line):
            rows.extend((number, "host_versions", m.group(1)) for m in VERSION_RE.finditer(line))
        if "PreToolUse" in line and NEGATION_RE.search(line):
            rows.extend((number, "bypass_tools", tool) for tool in TOOL_RE.findall(ANCHOR_RE.sub(" ", line)))
    for item in fenced_commands(text):
        argv = item["argv"]
        if argv and os.path.basename(argv[0]) in HOST_BINARIES:
            rows.append((item["line"], "host_names", os.path.basename(argv[0])))
    every = list(prose) + [pair for fence in fences for pair in fence["body"]]
    for number, line in every:
        rows.extend((number, "release_tags", "v" + m.group(1)) for m in TAG_RE.finditer(line))
        if CURRENT_RE.search(line):
            rows.extend((number, "release_tags", m.group(1)) for m in BARE_RE.finditer(line))
        rows.extend((number, "anchors", m.group(1)) for m in ANCHOR_RE.finditer(line))
    rows.sort(key=lambda row: row[0])
    return rows


def page_facts(text: str) -> Dict[str, List[str]]:
    """The facts a page states, by key, each value once per place it is stated (a version quoted in a code span and
    again in prose is two values): default_paths (a Codex binary path on a line about a default), host_names (a code
    span after the word host, or a host binary a fenced command starts), host_versions ("version N" on a line naming
    a host), release_tags (v1.2.3 anywhere, a bare number on a "current release" line), anchors (evidence anchor
    bodies as written) and bypass_tools (snake_case tool names on a line naming PreToolUse with "not" or "never")."""
    if not isinstance(text, str):
        raise ValueError("page_facts needs the page text as a str, got %s" % type(text).__name__)
    facts = {key: [] for key in FACT_KEYS}
    for _line, key, value in _fact_rows(text):
        facts[key].append(value)
    return facts


def _load_rows(path):
    """(rows, problem): the evidence rows, or None with a NO-DATA (absent, empty) or CORRUPT problem line."""
    if not os.path.exists(path):
        return None, "NO-DATA: evidence file %s is not recorded yet" % path
    text, problem = _read_text(path, MAX_EVIDENCE_BYTES)
    if problem:
        return None, "CORRUPT: evidence file %s" % problem
    rows = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            return None, "CORRUPT: evidence file %s line %d is not JSON" % (path, number)
        if not isinstance(row, dict):
            return None, "CORRUPT: evidence file %s line %d is not a JSON object" % (path, number)
        rows.append(row)
    if not rows:
        return None, "NO-DATA: evidence file %s holds no row" % path
    return rows, ""


def _evidence(root, evidence):
    if not _usable(evidence):
        return None, "REFUSED: the evidence path is %s, not a path, so no evidence fact can be checked" % _show(evidence)
    return _load_rows(evidence if os.path.isabs(evidence) else os.path.join(root, evidence))


def _host_rows(rows, host):
    return [row for row in rows or [] if row.get("host") == host]


def _row_versions(row):
    version = row.get("host_version")
    if not isinstance(version, str) or not version.strip() or version.strip().lower() == "no_data":
        return []
    return VERSION_NUMBER_RE.findall(version) or [version.strip()]


def _row_host_name(row):
    name = row.get("host_name")
    if isinstance(name, str) and name.strip() and name.strip().lower() != "no_data":
        return name.strip()
    binary = row.get("host_bin_realpath")
    if isinstance(binary, str) and binary.strip():
        return os.path.basename(binary.strip().rstrip("/")) or None
    return None


def _umbrella(root):
    """The umbrella version from version_source.read_source, or None when it cannot be read."""
    try:
        version, _doc = version_source.read_source(Path(root))
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError):
        return None
    return version.strip() if isinstance(version, str) and version.strip() else None


def _default_codex_bin():
    try:
        found = brother_paths.codex_bin({})
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return found if isinstance(found, str) and found else None


def _bare(tag):
    return tag[1:] if tag.startswith("v") else tag


def _pretool_matchers(root):
    """(compiled matchers, problem) for every PreToolUse entry of the shipped Antigravity hooks.json. An entry with no
    matcher matches every tool, as the host reads it."""
    path = os.path.join(root, HOOKS_JSON)
    text, problem = _read_text(path, MAX_PAGE_BYTES)
    if problem:
        return [], "NO-DATA: %s" % problem
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError):
        return [], "NO-DATA: %s is not JSON" % path
    found, stack = [], [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            entries = node.get("PreToolUse")
            if isinstance(entries, list):
                for entry in entries:
                    matcher = entry.get("matcher", "") if isinstance(entry, dict) else None
                    if not isinstance(matcher, str):
                        return [], "NO-DATA: %s carries a PreToolUse matcher that is not a string" % path
                    found.append(matcher)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if not found:
        return [], "NO-DATA: %s wires no PreToolUse hook" % path
    try:
        return [(matcher, re.compile(matcher)) for matcher in found], ""
    except re.error:
        return [], "NO-DATA: %s carries a PreToolUse matcher that is not a regular expression" % path


def _bypass_refusals(root, page, listed):
    """The Antigravity page must state each BYPASS_TOOLS entry, and each tool it lists must be one the shipped hook
    names and the shipped PreToolUse matcher does not match: the statement is checked against the tree, not the page."""
    out = []
    names = set(tool for _line, tool in listed)
    for tool in BYPASS_TOOLS:
        if tool not in names:
            out.append("%s: does not state that `%s` does not reach PreToolUse (one line naming the tool, PreToolUse "
                       "and not or never)" % (page, tool))
    if not listed:
        return out
    source, source_problem = _read_text(os.path.join(root, HOOK_SCRIPT), MAX_SCRIPT_BYTES)
    matchers, matcher_problem = _pretool_matchers(root)
    for line, tool in listed:
        where = "%s line %d" % (page, line)
        if source_problem:
            out.append("%s: NO-DATA: `%s` cannot be checked against the shipped hook: %s" % (where, tool, source_problem))
        elif not re.search(r"[\"']%s[\"']" % re.escape(tool), source):
            out.append("%s: lists `%s` as not reaching PreToolUse, but %s never names it" % (where, tool, HOOK_SCRIPT))
        if matcher_problem:
            out.append("%s: %s, so `%s` cannot be checked against it" % (where, matcher_problem, tool))
            continue
        hit = next((text for text, pattern in matchers if pattern.search(tool)), None)
        if hit is not None:
            out.append("%s: states `%s` does not reach PreToolUse, but the shipped PreToolUse matcher %r in %s "
                       "matches it" % (where, tool, hit, HOOKS_JSON))
    return out


def _fact_refusals(root, page, host, facts, rows, rows_problem):
    out = []
    host_rows = _host_rows(rows, host)
    newest = host_rows[-1] if host_rows else None
    missing = rows_problem or "NO-DATA: no evidence row names host %s" % host
    known = set(row["raw_out_sha256"].lower() for row in host_rows if isinstance(row.get("raw_out_sha256"), str))
    default = _default_codex_bin()
    umbrella = _umbrella(root)
    expected_name = _row_host_name(newest) if newest is not None else None
    want = _row_versions(newest) if newest is not None else []
    named_host, listed = False, []
    for line, key, value in facts:
        where = "%s line %d" % (page, line)
        if key == "default_paths":
            if default is None:
                out.append("%s: NO-DATA: names %s as the default Codex binary, but brother_paths.codex_bin({}) "
                           "returned nothing to compare" % (where, value))
            elif value != default:
                out.append("%s: names %s as the default Codex binary, but brother_paths.codex_bin({}) is %s" % (
                    where, value, default))
        elif key == "host_versions":
            if newest is None:
                out.append("%s: NO-DATA: names %s host version %s, which cannot be checked: %s; name no number "
                           "until the evidence row exists" % (where, host, value, missing))
            elif not want:
                out.append("%s: NO-DATA: names %s host version %s, but the newest %s evidence row pins none" % (
                    where, host, value, host))
            elif value not in want:
                out.append("%s: names %s host version %s, but the newest %s evidence row reads %s" % (
                    where, host, value, host, _show(newest.get("host_version"))))
        elif key == "host_names":
            named_host = True
            if newest is None:
                out.append("%s: NO-DATA: names host %s, which cannot be checked: %s" % (where, value, missing))
            elif expected_name is None:
                out.append("%s: NO-DATA: names host %s, but the newest %s evidence row names no host binary" % (
                    where, value, host))
            elif value != expected_name:
                out.append("%s: names host %s, but the %s evidence rows name %s" % (where, value, host, expected_name))
        elif key == "release_tags":
            if umbrella is None:
                out.append("%s: NO-DATA: names release %s, but version_source.read_source found no umbrella "
                           "version" % (where, value))
            elif _bare(value) != umbrella:
                out.append("%s: names release %s, but the current release is %s (version_source.read_source)" % (
                    where, value, umbrella))
        elif key == "anchors":
            if not SHA256_RE.fullmatch(value.lower()):
                out.append("%s: carries evidence anchor %s, which is not a sha256 and resolves to no row" % (
                    where, _show(value)))
            elif rows is None:
                out.append("%s: NO-DATA: evidence anchor %s cannot be resolved: %s" % (where, value, rows_problem))
            elif value.lower() not in known:
                out.append("%s: evidence anchor %s resolves to no %s row's raw_out_sha256" % (where, value, host))
        elif key == "bypass_tools":
            listed.append((line, value))
    if host in MUST_NAME_HOST and not named_host:
        out.append("%s: names no %s host binary (a code span after the word host, or a fenced command starting it)"
                   % (page, host))
    if host == "antigravity":
        out.extend(_bypass_refusals(root, page, listed))
    return out


def _check_page(root, page, host, rows, rows_problem, runner, home):
    text, problem = _read_text(os.path.join(root, page), MAX_PAGE_BYTES)
    if problem:
        return ["%s: cannot be checked: %s" % (page, problem)]
    out = []
    commands = fenced_commands(text)
    if not commands:
        out.append("%s: carries no fenced command block, and a page with nothing to run is not accurate" % page)
    for item in commands:
        reasons = [item["error"]] if item["error"] else resolve_command(root, item["command"])
        out.extend("%s line %d: %s" % (page, item["line"], reason) for reason in reasons)
    out.extend("%s %s" % (page, reason) for reason in run_safe_commands(root, home, commands, runner=runner))
    out.extend(_fact_refusals(root, page, host, _fact_rows(text), rows, rows_problem))
    return out


def check_pages(root: str, evidence: str, runner=None) -> List[str]:
    """Refusals for docs/how-to/install-codex.md and docs/how-to/install-antigravity.md under `root`, judged against
    the tree and the evidence rows at `evidence` (relative to `root` unless absolute). Empty is a pass. Never raises on
    a bad page: an unreadable page, a missing evidence file, an unreadable umbrella version or a missing runner is a
    NO-DATA line in the list, and a corrupt evidence file is a refusal of its own. Safe listed commands run through
    `runner` (see run_safe_commands) against a throwaway home made here and removed afterwards."""
    if not _usable(root):
        return ["NO-DATA: root is %s, not a checkout path, so no page was checked" % _show(root)]
    if not os.path.isdir(root):
        return ["NO-DATA: root %s is not a directory, so no page was checked" % root]
    rows, rows_problem = _evidence(root, evidence)
    out = [rows_problem] if rows_problem.startswith(("CORRUPT", "REFUSED")) else []
    try:
        home = tempfile.mkdtemp(prefix="hp1c-home-")
    except OSError as exc:
        home = None
        out.append("NO-DATA: no throwaway home could be made: %s" % exc)
    try:
        for page, host in PAGES:
            try:
                out.extend(_check_page(root, page, host, rows, rows_problem, runner, home))
            except (OSError, ValueError, RecursionError) as exc:
                out.append("%s: cannot be checked: %s" % (page, type(exc).__name__))
    finally:
        if home:
            shutil.rmtree(home, ignore_errors=True)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# HP1.d: the owner hand steps page, docs/how-to/host-live-proof.md. Like the rest of this file it starts no process:
# a step the loop can run goes through the caller's `runner(argv, cwd, env)`, which returns (exit code, output text),
# and a runnable step with no runner is NO-DATA, never a pass.

OWNER_PAGE = "docs/how-to/host-live-proof.md"
#: The three hosts the owner page must name, each as (label, word that names it).
OWNER_HOSTS = (("Antigravity", "antigravity"), ("Codex", "codex"), ("Claude Code", "claude"))
STEP_RE = re.compile(r"^#{1,6}\s*Step\s+(\d+)\b[\s:.-]*(.*)$", re.I)
DECLARED_RE = re.compile(r"<!--\s*owner-values:\s*(.*?)\s*-->")
PLACEHOLDER_RE = re.compile(r"<([A-Za-z][\w-]*)>")
SECRET_NAME_RE = re.compile(r"api[_-]?key|token|secret|passw|credential|bearer", re.I)
SECRET_SHAPE_RE = re.compile(r"\bBearer\s+(?![$<])\S{6,}|\bsk-[A-Za-z0-9_-]{8,}|\bgh[pousr]_[A-Za-z0-9]{8,}"
                             r"|\bAKIA[0-9A-Z]{12,}|\bxox[abp]-[A-Za-z0-9-]{8,}")
#: What an expected block must never match: the empty text, a blank line and a run of gibberish.
JUNK_OUTPUTS = ("", "\n", "zq9#x -0 !~")
RUNNABLE_PROOF = "scripts/host_live_proof.py"
RUNNABLE_VERIFY = "scripts/host_live_verify.py"


def _runnable_words(command):
    """The word list of a command the loop may run (the verifier, or the driver's --print-owner-commands), else None."""
    try:
        words = shlex.split(command, comments=True)
    except ValueError:
        return None
    if len(words) < 2 or not PYTHON_RE.fullmatch(os.path.basename(words[0])) or ENV_RE.match(words[0]):
        return None
    if any(word in SHELL_OPERATORS or PLACEHOLDER_RE.search(word) or "$" in word for word in words):
        return None
    rest = list(words[1:])
    while rest and rest[0] in PYTHON_OPTIONS:
        rest.pop(0)
    if not rest:
        return None
    if rest[0] == RUNNABLE_VERIFY or (rest[0] == RUNNABLE_PROOF and "--print-owner-commands" in rest):
        return words
    return None


def _close_line(fence):
    """The line number of the closing fence of `fence` (or one past its last body line when never closed)."""
    return (fence["body"][-1][0] if fence["body"] else fence["line"]) + 1


def steps(text: str) -> List[Dict[str, object]]:
    """The numbered steps of the owner page, in page order. A step opens at a heading `Step N` and holds its one
    fenced command block (any fence not named `expected`) and the `expected` fence that follows it with nothing but
    blank lines between. Each record: {number, title, line, block_line, info, lines, commands, placeholders,
    extra_blocks, runnable, expected, expected_line}; `expected` is None when no expected block follows the command
    block, and `commands` are fenced_commands records (shell fences only)."""
    if not isinstance(text, str):
        raise ValueError("steps needs the page text as a str, got %s" % type(text).__name__)
    prose, fences = _layout(text)
    heads = [(n, int(m.group(1)), m.group(2).strip()) for n, line in prose for m in [STEP_RE.match(line)] if m]
    records = fenced_commands(text)
    out = []
    for index, (line, number, title) in enumerate(heads):
        stop = heads[index + 1][0] if index + 1 < len(heads) else len(text.splitlines()) + 2
        mine = [f for f in fences if line < f["line"] < stop]
        blocks = [f for f in mine if f["info"] != "expected"]
        step = {"number": number, "title": title, "line": line, "block_line": None, "info": "", "lines": [],
                "commands": [], "placeholders": [], "extra_blocks": max(0, len(blocks) - 1), "runnable": False,
                "expected": None, "expected_line": None}
        if blocks:
            block = blocks[0]
            close = _close_line(block)
            step["block_line"], step["info"] = block["line"], block["info"]
            step["lines"] = [raw for _n, raw in block["body"]]
            step["commands"] = [r for r in records if block["line"] < r["line"] <= close] \
                if block["info"] in SHELL_FENCES else []
            names = []
            for raw in step["lines"]:
                names.extend(PLACEHOLDER_RE.findall(raw))
            step["placeholders"] = names
            after = [f for f in mine if f["line"] > close]
            if after and after[0]["info"] == "expected" and block["closed"]:
                gap = [t for n, t in prose if close <= n < after[0]["line"] and t.strip()]
                if not gap:
                    step["expected"] = "\n".join(raw for _n, raw in after[0]["body"]).strip("\n")
                    step["expected_line"] = after[0]["line"]
            cmds = [c for c in step["commands"] if c["command"]]
            step["runnable"] = bool(cmds) and not step["extra_blocks"] and \
                all(not c["error"] and _runnable_words(c["command"]) for c in cmds)
        out.append(step)
    return out


def _step_name(step):
    number = step.get("number") if isinstance(step, dict) else None
    return "step %s" % (number if isinstance(number, int) and not isinstance(number, bool) else "?")


def expected_output_refusals(step: Dict[str, object]) -> List[str]:
    """Refusals for the expected block of one step: none after the command block, an empty one, a regular expression
    for a runnable step that does not compile, and one that matches anything (`.*` alone, or any pattern that matches
    the empty text, a blank line or a run of gibberish)."""
    if not isinstance(step, dict):
        return ["the step is a %s, not a step record" % type(step).__name__]
    name, expected = _step_name(step), step.get("expected")
    if expected is None:
        return ["%s has no expected block right after its command block" % name]
    if not isinstance(expected, str):
        return ["%s: the expected block is a %s, not text" % (name, type(expected).__name__)]
    if not expected.strip():
        return ["%s: the expected block is empty" % name]
    try:
        pattern = re.compile(expected, re.M)
    except (re.error, RecursionError, OverflowError):
        if step.get("runnable") is True:
            return ["%s: the expected block is not a valid regular expression, and this step is run and matched" % name]
        return []
    if any(pattern.search(junk) for junk in JUNK_OUTPUTS):
        return ["%s: the expected block matches anything, so it proves nothing" % name]
    return []


def _secret_refusals(name, lines):
    """Refusals for a command line that asks the human to put a secret in it: a secret shaped string, a secret named
    flag or NAME=value given a literal, or a placeholder named like a secret."""
    out = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        hit = SECRET_SHAPE_RE.search(line) is not None
        for holder in PLACEHOLDER_RE.findall(line):
            hit = hit or SECRET_NAME_RE.search(holder) is not None
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            words = line.split()
        for pos, word in enumerate(words):
            if word.startswith("-") and SECRET_NAME_RE.search(word):
                value = word.partition("=")[2] if "=" in word else (words[pos + 1] if pos + 1 < len(words) else "")
                hit = hit or (bool(value) and not value.startswith("$") and not value.startswith("-"))
            elif ENV_RE.match(word):
                key, _sep, value = word.partition("=")
                hit = hit or (SECRET_NAME_RE.search(key) is not None and bool(value) and not value.startswith("$"))
        if hit:
            out.append("%s asks for a secret in a command line (`%s`); a secret comes from the environment, never "
                       "from the command" % (name, " ".join(line.split())[:60]))
    return out


def _run_refusals(name, step, root, runner):
    """Runs a runnable step's commands through the runner and matches their output against the expected regex."""
    if not callable(runner):
        return ["%s NO-DATA: the step is runnable and its expected block is a pattern, but no command runner was "
                "given and this checker starts no process, so the output is unknown and the step is not proven" % name]
    env = {"PATH": os.environ.get("PATH") or os.defpath, "HOME": os.path.expanduser("~")}
    pieces = []
    for record in step["commands"]:
        if not record["command"]:
            continue
        words = _runnable_words(record["command"])
        try:
            result = runner(list(words), root, dict(env))
        except Exception as exc:  # the caller's runner failing is a refusal for this step, never a raise
            return ["%s NO-DATA: the command runner raised %s" % (name, type(exc).__name__)]
        ok = isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str) \
            and isinstance(result[0], int) and not isinstance(result[0], bool)
        if not ok:
            return ["%s NO-DATA: the command runner returned %s, not (exit code, output)" % (name, _show(result))]
        pieces.append(result[1])
    output = "\n".join(pieces)
    if re.search(step["expected"], output, re.M) is None:
        first = next((ln for ln in output.splitlines() if ln.strip()), "")
        return ["%s: the output of its command does not match the expected block (first line: %s)" % (name, _show(first))]
    return []


def check_owner_steps(root: str, page: str, runner=None) -> List[str]:
    """Refusals for the owner hand steps page `page` (relative to `root` unless absolute). Empty is a pass. Refused:
    a page that omits any of the three hosts, steps out of order or repeated, a step with no expected block or an
    empty or match anything one, a command whose script or flag does not resolve, a `<name>` the page does not
    declare in `<!-- owner-values: a, b -->`, and a command line that asks for a secret. A runnable step (the verifier,
    or the driver's --print-owner-commands) is run through `runner(argv, cwd, env)` -> (exit code, output) and its
    output must match the expected block; with no runner that is a NO-DATA refusal. Never raises on a bad page."""
    if not _usable(root):
        return ["NO-DATA: root is %s, not a checkout path, so the owner steps were not checked" % _show(root)]
    if not os.path.isdir(root):
        return ["NO-DATA: root %s is not a directory, so the owner steps were not checked" % root]
    if not _usable(page):
        return ["REFUSED: the page is %s, not a path" % _show(page)]
    text, problem = _read_text(page if os.path.isabs(page) else os.path.join(root, page), MAX_PAGE_BYTES)
    if problem:
        return ["NO-DATA: owner page " + problem]
    out = []
    lowered = text.lower()
    for label, word in OWNER_HOSTS:
        if re.search(r"\b%s\b" % word, lowered) is None:
            out.append("the page omits the host %s" % label)
    try:
        found = steps(text)
    except (ValueError, RecursionError) as exc:
        return out + ["the page cannot be read as steps: %s" % type(exc).__name__]
    if not found:
        return out + ["the page holds no numbered step (a heading `Step 1`, `Step 2`, ...)"]
    declared = set()
    for body in DECLARED_RE.findall(text):
        declared.update(word for word in re.split(r"[\s,]+", body) if word)
    seen, commands, previous = {}, {}, 0
    for step in found:
        name = _step_name(step)
        number = step["number"]
        if number in seen:
            out.append("%s appears twice (lines %d and %d)" % (name, seen[number], step["line"]))
        else:
            seen[number] = step["line"]
            if number != previous + 1:
                out.append("%s is out of order: it follows step %d" % (name, previous))
        previous = max(previous, number)
        if step["block_line"] is None:
            out.append("%s has no command block" % name)
            continue
        if step["extra_blocks"]:
            out.append("%s holds more than one command block; one numbered step is one block" % name)
        refused = expected_output_refusals(step)
        out.extend(refused)
        for record in step["commands"]:
            if record["error"]:
                out.append("%s: line %d %s" % (name, record["line"], record["error"]))
            elif record["command"]:
                out.extend("%s: line %d: %s" % (name, record["line"], why)
                           for why in resolve_command(root, record["command"]))
        out.extend("%s names <%s>, which the page does not declare in an owner-values list" % (name, holder)
                   for holder in sorted(set(step["placeholders"]) - declared))
        out.extend(_secret_refusals(name, step["lines"]))
        key = "\n".join(line.strip() for line in step["lines"] if line.strip() and not line.strip().startswith("#"))
        if key and key in commands:
            out.append("%s repeats the command block of step %d" % (name, commands[key]))
        elif key:
            commands[key] = number
        if step["runnable"] is True and not refused:
            out.extend(_run_refusals(name, step, root, runner))
    return out
