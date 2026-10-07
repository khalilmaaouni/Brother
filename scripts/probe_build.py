"""probe_build: turn one probe run into one fixed outcome word, then judge the run.

THE FAILURE THIS EXISTS FOR. The last part of the gate that touches something a
model wrote is the probe run, and its outcome used to live in prose. Prose is
read differently by two people at two hours, so a probe that died on a type it
was handed could be described as handled, and a probe that quietly returned None
could be described as done. This module gives each of those one fixed word, so
no reader has to decide.

WHAT IT REFUSES, and why each refusal is here:

  * a raw interpreter exception, or a TypeError shape that means the probe's
    input handling is broken, is CRASH
  * a deliberate error type, an argparse exit 2, and a returned refusal value
    such as None, False, a quarantine record or a no decision record are
    REFUSED, never a pass and never a wrong accept
  * a probe body carrying a private term is refused before any of it is read as
    a transcript, and a term list that cannot be read refuses the same way
    rather than running with the screen switched off
  * a fire label call whose label or message is not a literal is NO-DATA,
    because a probe nothing is known about must not be counted as a probe that
    ran

NOTHING BUT THE STANDARD LIBRARY IS IMPORTED, and this file has no module level
side effect at all. That is deliberate: the runner is executed inside a
containment sandbox, and a module level import of a sibling file, or a module
level mutation of sys.path, makes the whole runner fail to load there. The
private terms list is read here from the same environment variable and the same
default path the build grader reads, so one list governs both halves of the gate
and an unreadable list blocks both.

HOW IT READS A TRANSCRIPT. probe_text is the captured record of one probe run,
written as Python so this module can parse it. Each probe announces itself with
a fire label call, fire(label, message), where message is the literal text that
probe reported; classify() turns that text into one outcome word. This module
never runs the transcript: a probe body is read and never handed to a dynamic
call.

Python 3, standard library only. No network.
"""
import ast
import json
import os
import sys

#: The outcome words. A caller compares against these, so they are constants.
CRASH = "CRASH"
REFUSED = "REFUSED"
NO_DATA = "NO-DATA"
WRONG_ACCEPT = "WRONG-ACCEPT"
PASS = "PASS"

#: The name a probe body calls to say which label it fired, and the arity that
#: call must have to be a probe this module can judge: a label and a message.
FIRE_LABEL_CALL = "fire"
FIRE_LABEL_ARGS = 2

#: The exit codes run_probe, main and _main return.
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NO_DATA = 2

#: The one line printed when the arguments are missing or the wrong shape.
USAGE = "usage: probe_build.py <build.json> <probe.py> <root>"

#: The private terms list. Read here from the same variable and the same
#: default path the build grader reads.
PRIVATE_NAMES_ENV = "BROTHER_PRIVATE_NAMES"

#: Words in a probe's own report that say it refused on purpose. Read before
#: the crash words, because a deliberate refusal that names an exception type is
#: still a refusal.
DELIBERATE_MARKERS = ("refused", "refusal", "quarantine", "no decision",
                      "no-decision", "no data", "no-data", "nodata",
                      "deliberate")

#: Reports that are a refusal value rather than a sentence about one.
REFUSAL_VALUES = ("none", "false", "refused", "quarantine", "no-decision",
                  "no data", "no-data", "nodata")

#: Raw interpreter exception names. A probe that died on one of these broke,
#: whatever else its report says.
RAW_CRASH_TYPES = ("typeerror", "attributeerror", "keyerror", "indexerror",
                   "nameerror", "unboundlocalerror", "zerodivisionerror",
                   "recursionerror", "memoryerror", "syntaxerror",
                   "importerror", "modulenotfounderror", "indentationerror",
                   "stopiteration", "runtimeerror", "assertionerror")


def _private_terms():
    """The terms this machine's own list carries, or None.

    None is not an empty list. A list that could not be read is an unknown
    list, and an unknown list blocks a probe body rather than passing it.
    """
    path = os.environ.get(PRIVATE_NAMES_ENV)
    if not path:
        try:
            home = os.path.expanduser("~")
        except (OSError, RuntimeError, KeyError):
            return None
        path = os.path.join(home, ".claude", "private_names.txt")
    try:
        with open(path, "rb") as handle:
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

    A text that is not a string, and a term list that cannot be read, are both
    a hit: an unknown screen is a blocked screen, never a clean one.
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

    A report is prose, so an exception name in it is matched as a whole word: a
    symbol that merely contains one of them is a different word.
    """
    found = set()
    current = []
    for char in text:
        if char.isalnum() or char == "_":
            current.append(char)
        elif current:
            found.add("".join(current))
            current = []
    if current:
        found.add("".join(current))
    return found


def classify(name, module, message):
    """One outcome word for one probe's captured report.

    R16: a raw interpreter exception, or a TypeError shape that means the
    probe's input handling is broken, is CRASH.
    R17: a deliberate error type, or an argparse exit 2, is REFUSED.
    R18: a returned refusal value such as None, False, a quarantine record or a
    no decision record is REFUSED, never a wrong accept.

    A name, a module or a report that is not a string is CRASH: a report nobody
    can read is broken input handling, never a pass.
    """
    if not isinstance(name, str) or not isinstance(module, str):
        return CRASH
    if not isinstance(message, str):
        return CRASH
    low = message.strip().lower()
    # R17 FIRST, because a deliberate refusal that names an exception type is
    # still a refusal, and reading it as a crash would hide the refusal.
    if ("exit 2" in low or "exited 2" in low or "exit code 2" in low
            or "systemexit: 2" in low or "status 2" in low):
        return REFUSED
    for marker in DELIBERATE_MARKERS:
        if marker in low:
            return REFUSED
    if "valueerror" in low or "systemexit" in low:
        return REFUSED
    normalised = low.replace("_", "-").strip()
    normalised = normalised.strip(".:;,!?")
    parts = normalised.split()
    if normalised in REFUSAL_VALUES or (parts and parts[-1] in REFUSAL_VALUES):
        return REFUSED
    # R16: raw interpreter exceptions and the TypeError shapes.
    if "traceback" in low or "exception" in low:
        return CRASH
    words = _words(low)
    for token in RAW_CRASH_TYPES:
        if token in words:
            return CRASH
    # A probe that took what it was told to refuse is a wrong accept.
    if "wrong accept" in low or "wrong-accept" in normalised:
        return WRONG_ACCEPT
    if "accepted" in low:
        return WRONG_ACCEPT
    return PASS


def _literal_text(node):
    """The literal text of a constant node, or None when it is not a literal.

    The message a probe reported must be written in the transcript, not computed
    while it is read: a value this module cannot read tells it nothing, and
    nothing is never counted as a probe that passed.
    """
    if not isinstance(node, ast.Constant):
        return None
    value = node.value
    if isinstance(value, str):
        return value
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    return None


def _probes(tree):
    """(probes, unknown) read from a parsed transcript.

    probes is a list of (label, message) for every fire label call whose label
    and message are both literals. unknown counts the fire calls this module
    cannot read: a fire call with no readable label or message is not a probe,
    so counting it as one would count a run that never said what it fired.
    """
    probes = []
    unknown = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != FIRE_LABEL_CALL:
            continue
        if len(node.args) != FIRE_LABEL_ARGS:
            unknown += 1
            continue
        label = _literal_text(node.args[0])
        message = _literal_text(node.args[1])
        if label is None or message is None:
            unknown += 1
            continue
        probes.append((label, message))
    return probes, unknown


def run_probe(build, probe_text, root):
    """(exit_code, message) for one probe transcript.

    R19: exit 1 when any probe is a crash or a wrong accept, exit 2 when zero
    probes ran, and exit 1 when the probe script itself dies.
    R20: a probe body without a fire label call is refused as NO-DATA with
    exit 2.
    R21: a probe body with a blocked term is refused with exit 1.
    R18 reaches this far through classify(): a probe that returned None, False
    or a quarantine record is REFUSED, so it is never counted as a probe that
    passed and never read as a wrong accept.
    """
    if not isinstance(build, dict):
        return (EXIT_FAIL, "CRASH: build is not an object, got %s"
                % type(build).__name__)
    if not isinstance(probe_text, str):
        return (EXIT_FAIL, "CRASH: probe text is not a string, got %s"
                % type(probe_text).__name__)
    if not isinstance(root, str) or not root.strip():
        return (EXIT_FAIL, "CRASH: root must be a non empty string")
    if not os.path.isdir(root):
        return (EXIT_FAIL, "REFUSED: root is not a directory to read in")
    if private_hits(probe_text) > 0:
        return (EXIT_FAIL, "REFUSED: the probe body carries a blocked term")
    try:
        tree = ast.parse(probe_text)
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        return (EXIT_FAIL, "CRASH: the probe script itself died: %s"
                % type(exc).__name__)
    probes, unknown = _probes(tree)
    if unknown:
        return (EXIT_NO_DATA, "NO-DATA: %d fire call(s) carry no readable label and message" % unknown)
    if not probes:
        return (EXIT_NO_DATA, "NO-DATA: zero probes ran")
    for index, (label, message) in enumerate(probes):
        outcome = classify(label, "probe", message)
        if outcome == PASS:
            continue
        if outcome == CRASH:
            return (EXIT_FAIL, "CRASH: probe %d (%s) died on its input"
                    % (index, label))
        if outcome == WRONG_ACCEPT:
            return (EXIT_FAIL, "WRONG-ACCEPT: probe %d (%s) accepted a refusal value" % (index, label))
        return (EXIT_FAIL, "%s: probe %d (%s) was refused, so the run did not pass"
                % (outcome, index, label))
    return (EXIT_PASS, "PASS: %d probe(s) ran" % len(probes))


def _load_build(path):
    """The build object read from path. Every failure is a ValueError.

    A wrong type, an unreadable path, a directory, bytes that are not utf-8,
    invalid JSON and a JSON value that is not an object all come back as a
    ValueError, never as a crash and never as an empty build.
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
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("build file is not valid JSON: %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ValueError("build file must hold a JSON object, got %s"
                         % type(data).__name__)
    return data


def main(argv=None):
    """Read the build, the transcript and the root named on the command line.

    R19 through the command line: the transcript's own exit code comes back, and
    a build file or a probe file that cannot be read is exit 1 rather than a
    crash. Missing or wrong shaped arguments are exit 2.
    """
    args = sys.argv[1:] if argv is None else argv
    if not isinstance(args, (list, tuple)) or len(args) != 3:
        print(USAGE, file=sys.stderr)
        return EXIT_NO_DATA
    for item in args:
        if not isinstance(item, str):
            print(USAGE, file=sys.stderr)
            return EXIT_NO_DATA
    build_path, probe_path, root = args
    try:
        build = _load_build(build_path)
    except ValueError as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    try:
        with open(probe_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        print("REFUSED: probe file is unreadable: %s: %s" % (probe_path, exc),
              file=sys.stderr)
        return EXIT_FAIL
    try:
        probe_text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        print("REFUSED: probe file is not utf-8: %s: %s" % (probe_path, exc),
              file=sys.stderr)
        return EXIT_FAIL
    code, message = run_probe(build, probe_text, root)
    stream = sys.stderr if code != EXIT_PASS else sys.stdout
    print(message, file=stream)
    return code


def _main():
    """The entry point this file uses when it is run as a script."""
    return main()


if __name__ == "__main__":
    sys.exit(_main())
