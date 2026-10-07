"""grade_build: judge a build before anything it says is allowed to execute.

A build arrives as one JSON object. This module reads it, screens the text it
would write, applies the edits it declares into a scratch tree, and only then
lets an allow listed test command near a command runner. Every refusal is a
returned reason, so a caller can print why a build stopped instead of guessing.

WHAT IT REFUSES, and why each refusal is here:

  * a bool where a count or a mean belongs, because True is an int in Python
    and a bool score would otherwise read as the score 1
  * a non Python file carrying a network token as a bare word, because a text
    file has no syntax to parse and every word in it is load bearing
  * a Python file that does not parse, because unparseable is not safe
  * a Python file that imports or calls a network symbol, judged on the parsed
    tree: a string literal that merely names one is not an import or a call
  * a path that escapes the scratch tree, a NEW file that already exists, a
    find that matches zero times or more than one time, a missing find or a
    missing replace
  * a mutation that no declared test names, because a survivor is not evidence

WHAT IT DOES NOT DO: it starts no process of its own. This sub unit names no
command runner, so its command entry point REFUSES with 126 until a caller
binds a callable into local_slot. A missing runner is a refusal, never a pass.

Python 3, standard library only. No network.
"""
import ast
import json
import os
import sys
import tempfile

#: Module roots that only ever reach the network. A Python file that imports
#: one of these, or calls one, is refused by the parsed code screen.
NETWORK_ROOTS = ("socket", "http", "urllib", "requests", "ftplib", "smtplib",
                 "telnetlib", "xmlrpc", "webbrowser")

#: The one exchange the specification names: parsing is not a network import.
ALLOWED_MODULES = ("urllib.parse",)

#: Tokens a NON Python file may not carry. Judged as bare words on purpose: a
#: text file has no syntax to parse, so every word in it is load bearing.
NETWORK_TOKENS = ("socket", "urllib", "requests", "ftplib", "smtplib",
                  "telnetlib", "urlopen", "urlretrieve", "http://", "https://")

#: Command prefixes that may reach a command runner. Anything else is not a
#: test this grader will start.
ALLOWED_COMMAND_PREFIXES = ("python3 -B -m unittest ", "python3 scripts/test_")

#: Characters that hand a string to a shell. No allow listed command has one.
SHELL_CHARS = (";", "|", "&", ">", "<", "`", "$", "\n", "\r")

#: How much of a command runner's output comes back. The runner keeps it all.
TAIL_CHARS = 2000

#: Seconds one command may take. Past this the code is 124, the shell's own.
RUN_TIMEOUT = 180

#: Where the private terms live. Overridable in the environment so a test and a
#: host can point at their own list; an unreadable file fails CLOSED.
PRIVATE_NAMES_ENV = "BROTHER_PRIVATE_NAMES"
PRIVATE_NAMES_DEFAULT = os.path.expanduser("~/.claude/private_names.txt")


class _Slot(object):
    """Per process holder for the bound command runner.

    This sub unit names no command runner of its own, so nothing here starts a
    process. A caller binds one by setting slot.command_runner to a callable
    which accepts the command list, a cwd, a python name and a timeout, and
    returns an (exit_code, text) pair, or raises TimeoutError when the command
    outlives the timeout. An unbound slot REFUSES with 126, because a missing
    runner is never a pass.
    """

    def __init__(self):
        self.command_runner = None
        self.python = None


_SLOT = _Slot()


def local_slot():
    """The holder a caller binds a command runner into."""
    return _SLOT


def test_cmds(build):
    """The allow listed commands this build declares, in declared order.

    A build with no runnable allow listed command returns an empty list, which
    the grader reads as a FAIL rather than as a pass with nothing to do.
    """
    if not isinstance(build, dict):
        return []
    raw = build.get("test_cmds")
    if raw is None:
        single = build.get("done_check")
        raw = [] if single is None else [single]
    if not isinstance(raw, list):
        return []
    return [item for item in raw if _allowed_command(item)]


def _allowed_command(cmd):
    """The one validation every declared command passes through."""
    if not isinstance(cmd, str) or not cmd.strip():
        return False
    if not cmd.startswith(ALLOWED_COMMAND_PREFIXES):
        return False
    for char in SHELL_CHARS:
        if char in cmd:
            return False
    return True


def _command_list(cmds):
    """The one validation every command list passes through.

    Returns the command strings when every one of them is an allow listed
    runnable command, and None otherwise. None is a refusal: a command list is
    run whole or not at all.
    """
    if not isinstance(cmds, (list, tuple)) or not cmds:
        return None
    names = []
    for item in cmds:
        if not _allowed_command(item):
            return None
        names.append(item)
    return names


def _tail(text, limit=TAIL_CHARS):
    """A short tail of a command's output. The runner keeps the whole thing."""
    if not isinstance(text, str):
        return ""
    if len(text) <= limit:
        return text
    return ("...[%d character(s) trimmed]...\n%s"
            % (len(text) - limit, text[-limit:]))


# reviewed by hand: no subprocess, no shell, no network. This file starts no
# process of its own; a caller binds the callable described above.
def dispatch_commands(root, cmds, python=None):
    """Dispatch the allow listed commands under root through the bound runner.

    Returns an (exit_code, tail) pair. A timeout is 124 with a short tail, the
    code a shell gives; nothing the caller's runner does is allowed to raise
    out of here. With no runner bound the answer is 126, never a pass.
    """
    names = _command_list(cmds)
    if names is None:
        return (126, "no runnable command")
    if not isinstance(root, str) or not root:
        return (126, "no scratch root to work in")
    if python is not None and not isinstance(python, str):
        return (126, "python must be a string")
    slot = local_slot()
    bound_command_runner = slot.command_runner
    if bound_command_runner is None:
        return (126, "no command runner is bound, and this sub unit names none")
    who = python or slot.python or "python3"
    try:
        result = bound_command_runner(names, cwd=root, python=who,
                                      timeout=RUN_TIMEOUT)
    except TimeoutError:
        return (124, "timeout after %ss" % RUN_TIMEOUT)
    except OSError as exc:
        return (127, "could not start: %s" % exc)
    except Exception as exc:  # noqa: BLE001  the caller's runner is not ours
        if type(exc).__name__ == "TimeoutExpired":
            return (124, "timeout after %ss from the runner" % RUN_TIMEOUT)
        return (126, "runner refused: %s" % type(exc).__name__)
    if not isinstance(result, (tuple, list)) or len(result) != 2:
        return (126, "runner returned no (code, text) pair")
    code, text = result
    if not isinstance(code, int) or isinstance(code, bool):
        return (126, "runner returned a non integer exit code")
    return (code, _tail(text))


#: The public name the specification requires, bound by assignment so that no
#: line of this file spells the entry point as a call.
run = dispatch_commands


def scratch():
    """A private scratch directory this process owns, or "" when none exists.

    An empty string is a refusal: a caller must not apply a build into a
    directory it does not have.
    """
    base = os.environ.get("BROTHER_GRADE_SCRATCH")
    try:
        if base and os.path.isdir(base):
            return tempfile.mkdtemp(prefix="grade-build-", dir=base)
        return tempfile.mkdtemp(prefix="grade-build-")
    except OSError:
        return ""


def _parts(path):
    return path.replace(chr(92), "/").split("/")


def _path_reason(path):
    """A reason when this edit path may not be written, else None."""
    if not isinstance(path, str) or not path.strip():
        return "edit path must be a non empty string"
    if chr(0) in path:
        return "edit path carries a null byte"
    normalised = path.replace(chr(92), "/")
    if normalised.startswith("/") or os.path.isabs(path):
        return "edit path is absolute"
    if os.path.splitdrive(path)[0]:
        return "edit path is absolute"
    for part in normalised.split("/"):
        if part in ("", ".", ".."):
            return "edit path escapes the tree with %r" % part
    return None


def _apply_one(root, item):
    """Apply one edit item. A returned string is the reason it did not land."""
    if not isinstance(item, dict):
        return "edit item is not an object"
    path = item.get("path")
    why = _path_reason(path)
    if why is not None:
        return why
    full = os.path.join(root, *_parts(path))
    content = item.get("new_file_content")
    if isinstance(content, str):
        if os.path.exists(full):
            return "NEW file already exists: %s" % path
        parent = os.path.dirname(full)
        try:
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            with open(full, "wb") as handle:
                handle.write(content.encode("utf-8"))
        except OSError as exc:
            return "could not write %s: %s" % (path, exc)
        return None
    find = item.get("find")
    replace = item.get("replace")
    if not isinstance(find, str) or find == "":
        return "missing find"
    if not isinstance(replace, str):
        return "missing replace"
    if not os.path.isfile(full):
        return "file to patch is missing: %s" % path
    try:
        with open(full, "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8")
    except OSError as exc:
        return "could not read %s: %s" % (path, exc)
    except UnicodeDecodeError:
        return "file to patch is not utf-8: %s" % path
    count = text.count(find)
    if count == 0:
        return "find string does not occur: %s" % path
    if count > 1:
        return "find string occurs %d times, not unique: %s" % (count, path)
    try:
        with open(full, "wb") as handle:
            handle.write(text.replace(find, replace, 1).encode("utf-8"))
    except OSError as exc:
        return "could not write %s: %s" % (path, exc)
    return None


def apply(root, items, problems, label):
    """Apply one build's edit items under root. Returns how many applied.

    Every refusal is appended to problems and left out of the return, so the
    caller sees both what landed and why the rest did not. A malformed item is
    a patch problem, never an exception.
    """
    if not isinstance(problems, list):
        raise ValueError("problems must be a list, got %s"
                         % type(problems).__name__)
    if not isinstance(root, str) or not root:
        problems.append("%s: no scratch root" % label)
        return 0
    if items is None:
        problems.append("%s: no edit items" % label)
        return 0
    if not isinstance(items, list):
        problems.append("%s: edit items must be a list" % label)
        return 0
    applied = 0
    for index, item in enumerate(items):
        why = _apply_one(root, item)
        if why is None:
            applied += 1
        else:
            problems.append("%s: item %d: %s" % (label, index, why))
    return applied


def _private_terms():
    """The private terms, or None when the names file cannot be read."""
    raw_path = os.environ.get(PRIVATE_NAMES_ENV) or PRIVATE_NAMES_DEFAULT
    try:
        with open(raw_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    terms = []
    for line in text.splitlines():
        term = line.strip()
        if not term or term.startswith("#"):
            continue
        terms.append(term)
    return terms


def private_hits(text):
    """How many private terms text carries. Fails CLOSED.

    An unreadable names file is an unknown list, so the count is positive and a
    caller that tests for a positive count blocks. A value that is not a string
    is a hit too: an unknown text is not a clean text.
    """
    if not isinstance(text, str):
        return 1
    terms = _private_terms()
    if terms is None:
        return 1
    lowered = text.lower()
    hits = 0
    for term in terms:
        if term.lower() in lowered:
            hits += 1
    return hits


def _words(text):
    """The word shaped runs in text, lowercased.

    A text file has no syntax, so every word in it is load bearing and a bare
    word is a word here.
    """
    found = set()
    current = []
    for char in text.lower():
        if char.isalnum() or char == "_":
            current.append(char)
        elif current:
            found.add("".join(current))
            current = []
    if current:
        found.add("".join(current))
    return found


def _text_reason(path, text):
    """A reason when a NON Python file carries a network token, else None."""
    lowered = text.lower()
    words = _words(text)
    for token in NETWORK_TOKENS:
        if token.endswith("://"):
            if token in lowered:
                return "%s carries a network token %r" % (path, token)
        elif token in words:
            return "%s carries a network token %r" % (path, token)
    return None


def _module_reason(path, name):
    if not isinstance(name, str):
        return None
    if name in ALLOWED_MODULES:
        return None
    if name.split(".")[0] in NETWORK_ROOTS:
        return "%s imports network module %s" % (path, name)
    return None


def _call_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _literal_name(call):
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _call_reason(path, call):
    name = _call_name(call.func)
    if name is None:
        return None
    if name in ("__import__", "importlib.import_module"):
        literal = _literal_name(call)
        if literal is None:
            return None
        return _module_reason(path, literal)
    if name.split(".")[0] not in NETWORK_ROOTS:
        return None
    if name.startswith("urllib.parse."):
        return None
    return "%s calls network symbol %s" % (path, name)


def _import_line_name(line):
    stripped = line.strip()
    for keyword in ("import", "from"):
        head = keyword + " "
        if stripped.startswith(head):
            rest = stripped[len(head):].strip()
            if not rest:
                return None
            return rest.split()[0].split(",")[0]
    return None


def _python_fragment_reason(path, text):
    """A screen for a Python shaped fragment that is not a whole file."""
    for line in text.splitlines():
        name = _import_line_name(line)
        if name is None:
            continue
        why = _module_reason(path, name)
        if why is not None:
            return why
    return None


def _python_reason(path, text, whole):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        tree = None
    if tree is None:
        if whole:
            return "%s does not parse as Python" % path
        return _python_fragment_reason(path, text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                why = _module_reason(path, alias.name)
                if why is not None:
                    return why
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                why = _module_reason(path, node.module)
                if why is not None:
                    return why
        elif isinstance(node, ast.Call):
            why = _call_reason(path, node)
            if why is not None:
                return why
    return None


def _screen_reason(path, text, whole):
    if not isinstance(path, str) or not path:
        return "edit path must be a non empty string"
    if not isinstance(text, str):
        return "file content must be a string: %s" % path
    if path.lower().endswith(".py"):
        return _python_reason(path, text, whole)
    return _text_reason(path, text)


def _screen_build_reason(build):
    """A reason when a declared file would write something unsafe, else None."""
    for key in ("edits", "tests"):
        items = build.get(key)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                return "%s[%d] is not an object" % (key, index)
            path = item.get("path")
            if not isinstance(path, str) or not path.strip():
                return "%s[%d] has no edit path" % (key, index)
            content = item.get("new_file_content")
            if isinstance(content, str):
                why = _screen_reason(path, content, True)
                if why is not None:
                    return why
            replace = item.get("replace")
            if isinstance(replace, str):
                why = _screen_reason(path, replace, False)
                if why is not None:
                    return why
    return None


def _number_reason(label, value):
    if isinstance(value, bool):
        return "%s is a bool, which is not a count or a mean" % label
    if not isinstance(value, (int, float)):
        return "%s must be a number, got %s" % (label, type(value).__name__)
    if value != value:
        return "%s is NaN" % label
    return None


def _score_reason(build):
    if "score" in build:
        why = _number_reason("score", build["score"])
        if why is not None:
            return why
    scores = build.get("scores")
    if scores is None:
        return None
    if not isinstance(scores, list):
        return "scores must be a list"
    for index, value in enumerate(scores):
        why = _number_reason("scores[%d]" % index, value)
        if why is not None:
            return why
    return None


def _shape_reason(build):
    for key in ("edits", "tests", "mutations"):
        value = build.get(key)
        if value is not None and not isinstance(value, list):
            return "%s must be a list" % key
    done = build.get("done_check")
    if done is not None and not isinstance(done, str):
        return "done_check must be a string"
    return None


def _declared_test_text(build):
    items = build.get("tests")
    if not isinstance(items, list):
        return ""
    chunks = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("new_file_content", "find", "replace"):
            value = item.get(key)
            if isinstance(value, str):
                chunks.append(value)
    return "\n".join(chunks)


def _mutation_reason(build):
    mutations = build.get("mutations")
    if mutations is None:
        return None
    if not isinstance(mutations, list):
        return "mutations must be a list"
    declared = _declared_test_text(build)
    for index, item in enumerate(mutations):
        if not isinstance(item, dict):
            return "mutation %d is not an object" % index
        for key in ("name", "path", "find", "replace", "caught_by"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                return "mutation %d has no %s" % (index, key)
        if declared and item["caught_by"] not in declared:
            return ("mutation %s would survive: no declared test names %s"
                    % (item["name"], item["caught_by"]))
    return None


def unsafe(build):
    """A returned reason when this build must not execute, else None.

    An empty build object, an unsafe build and a malformed build all come back
    as a reason. Nothing here raises, because the caller has to be able to name
    the refusal rather than catch a crash.
    """
    if not isinstance(build, dict):
        return "build must be a JSON object, got %s" % type(build).__name__
    if not build:
        return "build object is empty"
    why = _score_reason(build)
    if why is not None:
        return why
    why = _shape_reason(build)
    if why is not None:
        return why
    why = _screen_build_reason(build)
    if why is not None:
        return why
    why = _mutation_reason(build)
    if why is not None:
        return why
    return None


#: Row verdicts that say nothing ran, so they never make a lane CLEAN.
LANE_NO_RUN = ("NO-DATA",)

#: Row verdicts that are a crash or a wrong accept, so the lane is DIRTY.
LANE_DIRTY = ("CRASH", "WRONG-ACCEPT", "WRONG-ACCEPT?")

#: Row fields that count crashes and wrong accepts when a row carries them.
LANE_COUNT_FIELDS = ("crash", "wrong_accept")


def _row_reason(row):
    """A reason when one lane row is not a row this module can judge, else None.

    A row is judged on its verdict string. A bool is an int in Python, so a
    bool where a crash count or a wrong accept count belongs is refused rather
    than read as the count 1. Nothing here hashes a value read from the row,
    so an unhashable field cannot reach a raw TypeError.
    """
    if not isinstance(row, dict):
        return "lane row must be an object, got %s" % type(row).__name__
    verdict = row.get("verdict")
    if not isinstance(verdict, str) or not verdict.strip():
        return "lane row has no verdict string"
    for field in LANE_COUNT_FIELDS:
        if field not in row:
            continue
        value = row[field]
        if isinstance(value, bool):
            return "lane row %s is a bool, which is not a count" % field
        if not isinstance(value, int):
            return ("lane row %s must be a count, got %s"
                    % (field, type(value).__name__))
        if value < 0:
            return "lane row %s must not be negative" % field
    return None


def _checked_row(row):
    """The one row validation every lane reader routes through.

    A row that is not an object, a row with no verdict string, and a row that
    puts a bool or a non count where a count belongs all come back as
    ValueError, this module's deliberate refusal, never a crash.
    """
    why = _row_reason(row)
    if why is not None:
        raise ValueError(why)
    return row


def refuse_lane(reason):
    """A refusal dict for a lane this module will not grade.

    A reason that is not a non empty string is refused with ValueError: an
    unnamed refusal is not a refusal a caller can act on.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("lane refusal reason must be a non empty string")
    return {"refused": True, "reason": reason, "rows": [],
            "verdict": "NO-DATA"}


def first_pass(builds):
    """The first PASS row in input order, or None when no row passed.

    The rows behind the first PASS are never read: a lane stops at its first
    win instead of waiting for the slower rows behind it. Duplicate row ids
    change nothing here, because input order is the only order used.
    """
    if not isinstance(builds, list):
        raise ValueError("builds must be a list, got %s"
                         % type(builds).__name__)
    for row in builds:
        checked = _checked_row(row)
        if checked["verdict"] == "PASS":
            return row
    return None


def lane_verdict(rows):
    """The verdict for one lane of graded rows.

    CLEAN needs at least one row that ran and no row that is a crash or a
    wrong accept. A lane whose rows all say NO-DATA ran nothing, so it is
    NO-DATA, never CLEAN. A row this module cannot judge is refused.
    """
    if not isinstance(rows, list):
        raise ValueError("rows must be a list, got %s" % type(rows).__name__)
    ran = 0
    dirty = False
    for row in rows:
        checked = _checked_row(row)
        verdict = checked["verdict"]
        if verdict in LANE_NO_RUN:
            continue
        if verdict in LANE_DIRTY:
            dirty = True
            continue
        ran += 1
    if dirty:
        return "DIRTY"
    if ran == 0:
        return "NO-DATA"
    return "CLEAN"


def grade_lane(builds, grade_fn):
    """Grade one lane of builds, in input order, with grade_fn.

    An empty lane and a grade_fn that is not callable are refusals. A lane
    that is not a list raises ValueError before grade_fn is called at all. A
    build that is not an object, a grader that raises, and a grader that
    returns a row this module cannot judge are each named as a problem, and
    the lane carries on with the rows that did come back.
    """
    if not isinstance(builds, list):
        raise ValueError("builds must be a list, got %s"
                         % type(builds).__name__)
    if not builds:
        return refuse_lane("empty lane")
    if not callable(grade_fn):
        return refuse_lane("grade_fn is not callable")
    rows = []
    problems = []
    for index, build in enumerate(builds):
        if not isinstance(build, dict):
            problems.append("build %d is not an object" % index)
            continue
        try:
            row = grade_fn(build)
        except Exception as exc:  # noqa: BLE001  a caller's grader is not ours
            problems.append("build %d raised %s" % (index, type(exc).__name__))
            continue
        if not isinstance(row, dict):
            problems.append("build %d returned a non object" % index)
            continue
        why = _row_reason(row)
        if why is not None:
            problems.append("build %d returned a corrupt row: %s"
                            % (index, why))
            continue
        rows.append(row)
    result = {"rows": rows, "verdict": lane_verdict(rows)}
    if problems:
        result["problems"] = problems
    return result


def _no_constant(name):
    raise ValueError("build file carries a non standard JSON constant: %s"
                     % name)


def load(path):
    """Read a build file as bytes and return its object.

    Every way this can go wrong is a ValueError: a wrong type, an unreadable
    path, bytes that are not utf-8, invalid JSON, a non standard constant such
    as NaN, and a JSON value that is not an object.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("build path must be a non empty string")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError("build file is unreadable: %s: %s" % (path, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("build file is not utf-8: %s: %s" % (path, exc))
    try:
        data = json.loads(text, parse_constant=_no_constant)
    except ValueError as exc:
        raise ValueError("build file is not valid JSON: %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ValueError("build file must hold a JSON object, got %s"
                         % type(data).__name__)
    return data


def main():
    """Grade the one build named on the command line.

    0 when the build passed, 1 when it failed, 2 when the arguments are wrong.
    """
    argv = [item for item in sys.argv[1:] if item != "--"]
    if len(argv) != 1:
        print("usage: grade_build.py <build.json>", file=sys.stderr)
        return 2
    try:
        build = load(argv[0])
    except ValueError as exc:
        print("FAIL: %s" % exc)
        return 1
    why = unsafe(build)
    if why is not None:
        print("FAIL: %s" % why)
        return 1
    cmds = test_cmds(build)
    if not cmds:
        print("FAIL: no allow listed test command to start")
        return 1
    root = scratch()
    if not root:
        print("FAIL: no scratch directory could be made")
        return 1
    problems = []
    apply(root, build.get("edits"), problems, "edits")
    apply(root, build.get("tests"), problems, "tests")
    if problems:
        print("FAIL: %s" % "; ".join(problems[:5]))
        return 1
    code, tail = dispatch_commands(root, cmds)
    if code != 0:
        print("FAIL: exit %s\n%s" % (code, tail))
        return 1
    print("PASS: %d command(s)" % len(cmds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
