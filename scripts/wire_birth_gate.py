#!/usr/bin/env python3
"""wire_birth_gate: refuses to let a NEW part land unwired into the battery.

WHY THIS EXISTS. SYSTEM.md (scripts/system_doc.py) counts 271 parts today and
lists 71 of them as "NO-DATA, nothing in the battery runs it". Of those 71,
48 already have a test file on disk that nothing ever calls, and 23 have no
test at all. Someone will eventually close those 71. That is symptom work:
nothing in this estate stops the 72nd from landing tomorrow, which is why
there are 71 in the first place. This module is the thing that stops the
72nd. It is a BIRTH gate, not a health gate: it never touches the 71 that
already exist unwired, and it never blocks anyone from fixing them.

THE ONE QUESTION IT ANSWERS. For each path being added, is it a NEW part
(one that did not exist at HEAD), and if so, is it registered in the battery
or does it carry an explicit, valid exemption. That is the whole gate.

REGISTRATION IS NEVER RE-DECIDED HERE. scripts/system_doc.py already reads
scripts/check_all.sh and decides, by regex over each `run_check` line, which
module names are proven. This module calls system_doc.battery_checks() and
system_doc.parts() for that answer rather than reading check_all.sh a second
time with a second pattern: two readers of the same file drift the moment
one of them is edited and the other is not, and this estate has already paid
for that lesson once in gen_readiness_board.py's --help/argv confusion. The
only place this module reasons on its own is "is this path new" (a git
question system_doc does not ask) and "is this exemption valid" (a question
system_doc has no vocabulary for at all).

WHAT COUNTS AS A PART. A `.py` file directly inside the same directory as
the battery script (ordinarily scripts/), excluding any `test_*.py` file,
which is never a part (rule 5: a test proves a part, it is not one). A path
outside that directory is not this gate's remit and is silently allowed: it
carries no registration concept, matching system_doc, which only ever walks
that one directory.

NESTED PARTS ARE A KNOWN BLIND SPOT, ON PURPOSE. system_doc.parts() lists
files directly inside its scripts directory with os.listdir(), never
recursively, and its two module-name regexes (one matching "scripts/NAME.py"
or "scripts.NAME.py", the other matching "scripts.NAME") cannot match a
slash or a dot inside the captured name either, so a file under a
subdirectory of scripts/ can never be proven
registered by the existing detector, no matter what check_all.sh says. Since
NO-DATA is never a pass, a new part in a subdirectory is always treated as
unwired here (never falsely matched against a same-named top-level part; see
_registered_modules and the nested branch of check_paths). Its only ways
forward are the same two given to every unwired birth: move it to be a
direct child of scripts/ and register it there, or declare it Tier C.

TIER, for the exemptions file. There is no existing Tier vocabulary in this
estate to import (checked: not in orchestrator_invariants.py, not in
check_all.sh); this gate defines the three letters itself, scoped to what an
exemption may claim. Tier C is a part that cannot influence a decision (a
generator, a reporter): the only tier this gate will exempt. Tier A and
Tier B name parts that DO influence a decision, so a claimed exemption at
either tier is refused outright, on the theory that those are exactly the
parts a birth gate exists to keep honest.

THE EXEMPTIONS FILE is a JSON object, module name to `{"tier": "...",
"reason": "..."}`. A missing file is read as no exemptions at all (the safe
direction: nothing is falsely excused). A file that exists but is not valid
JSON, or whose top level is not an object, is MALFORMED and check_paths()
raises BirthGateError rather than guessing "empty": allowing on a read error
is how a gate quietly stops existing. An exemption entry that parses fine
but claims Tier A/B, or carries an empty or missing reason, is not malformed,
it is simply not honored: the part it names still needs registering or a
real exemption, and the refusal message says so.

WHO IS TOLD, AND HOW TO GET PAST THIS LEGITIMATELY. Every refusal is printed
to stderr by main() and appended, one JSON line per refusal, to a durable log
(default docs/plan/BIRTH-GATE-REFUSALS.log, overridable with --log), because
this estate has already shipped a gate once that refused eight commands and
wrote the fact nowhere, so nobody could say whether it had ever helped or
only ever cost time. `python3 scripts/wire_birth_gate.py --count-refusals`
reads that log back. An author gets past a real refusal in exactly two ways:
add a `run_check` line in scripts/check_all.sh naming the new part, or add a
Tier C entry with a non-empty reason to the exemptions file. There is no
third way, and no flag on this module skips the check; only NOT calling it
skips it, which is a decision for whoever wires it into the pre-commit hook,
not for this module to make quietly.

CONTINGENCY. An unreadable battery or a malformed exemptions file both
REFUSE, never allow: check_paths() raises BirthGateError, main() prints the
reason, logs it, and exits 2. A found (not exceptional) unwired birth is not
an error, it is the gate's ordinary product: check_paths() returns a
GateResult with allowed=False, main() logs and prints result.reason, and
exits 1. Anything git cannot answer (no git on PATH, the path is not inside
any git repository) also raises BirthGateError rather than guessing "must be
new" or "must be old". allowed=True with exit 0 is the only "not refused"
outcome.

Exit codes, running as `python3 scripts/wire_birth_gate.py PATH...`: 0
allowed, 1 refused (a real unwired birth), 2 the gate itself could not
decide (unreadable battery, malformed exemptions, git failure).

Python 3.9 floor, standard library only, no network.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import List

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import system_doc

ROOT = system_doc.ROOT
DEFAULT_BATTERY = system_doc.BATTERY
DEFAULT_EXEMPTIONS = os.path.join(ROOT, "docs", "plan", "BIRTH-GATE-EXEMPTIONS.json")
DEFAULT_LOG = os.path.join(ROOT, "docs", "plan", "BIRTH-GATE-REFUSALS.log")

# The only tier an exemption may claim. Tier A and Tier B name parts that
# influence a decision, which is exactly what this gate exists to keep wired.
EXEMPT_TIER = "C"
KNOWN_TIERS = ("A", "B", "C")


class BirthGateError(Exception):
    """Raised when the gate cannot safely decide and must refuse rather than guess.

    Covers: a battery it cannot read, an exemptions file that is not valid
    JSON or not a JSON object, and any question git itself cannot answer
    (no git on PATH, or a path outside any git repository). Never raised for
    an ordinary found-and-refused birth; that is GateResult(allowed=False,
    ...), not an exception.
    """


@dataclass
class GateResult:
    """The verdict for one check_paths() call.

    allowed: True only when nothing in `paths` is an unregistered,
        unexempted new part.
    unwired: module names that are new parts with no registration and no
        valid exemption. Non-empty exactly when allowed is False.
    registered: module names that are new parts and are proven registered
        in the battery.
    exempt: module names that are new parts covered by a valid Tier C
        exemption.
    reason: human-readable explanation. Names every unwired part and both
        ways forward when allowed is False; a short confirming sentence
        otherwise.
    """
    allowed: bool
    unwired: List[str] = field(default_factory=list)
    registered: List[str] = field(default_factory=list)
    exempt: List[str] = field(default_factory=list)
    reason: str = ""


def _run_git(args, cwd):
    """One git invocation. Raises BirthGateError if git itself cannot run.

    A non-zero return code is NOT raised here: callers use it as a normal
    boolean answer (git cat-file -e, git rev-parse --verify both use exit
    code as their whole interface). Only "git could not even be started" is
    exceptional.
    """
    try:
        return subprocess.run(
            ["git"] + list(args), cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except OSError as exc:
        raise BirthGateError("git could not be run to check history: %s" % exc)


def _git_toplevel(start_dir):
    """The repository root above start_dir, or BirthGateError if there is none."""
    proc = _run_git(["rev-parse", "--show-toplevel"], start_dir)
    if proc.returncode != 0:
        raise BirthGateError(
            "%s is not inside a git repository, so this gate cannot tell a "
            "new part from an existing one: %s" % (start_dir, proc.stderr.strip()))
    return proc.stdout.strip()


def _is_birth(abs_path):
    """True if abs_path did not exist at HEAD (a birth); False if it did.

    Judged against HEAD only, never the working tree or the index: a part
    added and edited again before commit is still a birth, and a part
    re-saved with identical content is still a modification. A repository
    with no commits yet has no HEAD to compare against, so every path in it
    is necessarily a birth; that is a real, testable state, not an error.

    Resolved to a real path first: `git rev-parse --show-toplevel` prints the
    symlink-resolved form (on macOS, /tmp is itself a symlink to /private/tmp),
    so comparing an unresolved abs_path against it can compute a relpath that
    walks entirely outside the repository and makes every path look new.
    """
    abs_path = os.path.realpath(abs_path)
    root = os.path.realpath(_git_toplevel(os.path.dirname(abs_path)))
    rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
    head = _run_git(["rev-parse", "--verify", "-q", "HEAD"], root)
    if head.returncode != 0:
        return True
    existed = _run_git(["cat-file", "-e", "HEAD:%s" % rel], root)
    return existed.returncode != 0


def _registered_modules(battery_path):
    """Module names system_doc's own rule already proves registered.

    Returns None if the battery could not be read at all (system_doc's
    NO-DATA case for a missing file), so a caller can tell "we checked and
    nothing is registered" (empty set) apart from "we could not check"
    (None). Reuses system_doc.battery_checks() (already parameterised by
    path) and system_doc.parts() (already parameterised by scripts_dir) for
    the actual direct-mention and transitive-credit computation; the only
    thing system_doc.parts() cannot take as an argument is which battery
    file to read (it always calls its own no-arg battery_checks(), a known
    shape in that module), so this function points system_doc.BATTERY at
    the requested file for the duration of the call and restores it
    afterwards. That is pointing the existing rule at a different file, not
    writing a second rule.
    """
    checks = system_doc.battery_checks(battery_path)
    if checks is None:
        return None
    scripts_dir = os.path.dirname(battery_path)
    saved_battery = system_doc.BATTERY
    try:
        system_doc.BATTERY = battery_path
        rows = system_doc.parts(scripts_dir)
    finally:
        system_doc.BATTERY = saved_battery
    return {row["module"] for row in rows if row["proven_by"]}


def _load_exemptions(path):
    """The exemptions object, or {} if the file is simply absent.

    Absence is the safe direction and is not an error. Anything else wrong
    with the file (unreadable, not JSON, not a JSON object) IS an error:
    BirthGateError, never an empty dict, because an empty dict here would
    read exactly like "nobody asked for any exemptions" when the truth is
    "the exemptions mechanism is broken and nobody can tell what it says".
    """
    if path is None or not os.path.isfile(path):
        return {}
    try:
        with io.open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise BirthGateError("exemptions file %s could not be read: %s" % (path, exc))
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise BirthGateError("exemptions file %s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise BirthGateError(
            "exemptions file %s must be a JSON object of module name to "
            "exemption, found a %s" % (path, type(data).__name__))
    return data


def _validate_exemption(entry):
    """(ok, message). ok True means this exemption is honored as written.

    A structurally odd entry (not an object) or a tier outside A/B/C is
    treated the same as any other invalid claim, refused with a message,
    never as a reason to abort the whole file: one bad exemption entry
    should not make every other valid one stop working.
    """
    if not isinstance(entry, dict):
        return False, "its exemption entry is not an object"
    tier = entry.get("tier")
    reason = entry.get("reason")
    if tier not in KNOWN_TIERS:
        return False, "its exemption declares tier %r, not one of %s" % (tier, KNOWN_TIERS)
    if tier != EXEMPT_TIER:
        return False, (
            "its exemption claims Tier %s; only Tier %s (a generator or "
            "reporter that cannot influence a decision) may be exempted" %
            (tier, EXEMPT_TIER))
    if not isinstance(reason, str) or not reason.strip():
        return False, "its exemption has an empty or missing reason"
    return True, "exempt (Tier %s): %s" % (tier, reason.strip())


def _forward_paths_note(module_name, nested):
    """The two ways forward, named for the author, plus the nested caveat."""
    nested_note = ""
    if nested:
        nested_note = (
            " It lives under a subdirectory of scripts/, and this estate's "
            "registration check (system_doc.py) only recognises a direct "
            "scripts/%s.py invocation, so a nested part can never be proven "
            "registered here; move it to be a direct child of scripts/ if "
            "you want it registered." % module_name)
    return (
        "register it with a run_check line in scripts/check_all.sh naming "
        "it, or declare it Tier C in the exemptions file with a non-empty "
        "reason.%s" % nested_note)


def check_paths(paths, *, battery_path=None, exemptions_path=None):
    """The gate. See module docstring for the whole rule.

    paths: an iterable of file paths (any of them may not exist under
        scripts/ at all; those are silently out of scope, see the module
        docstring's WHAT COUNTS AS A PART).
    battery_path: defaults to this estate's real scripts/check_all.sh.
    exemptions_path: defaults to this estate's real exemptions file; None
        (explicitly) or a missing file both mean "no exemptions".

    Raises BirthGateError when the gate cannot safely decide (see
    CONTINGENCY in the module docstring). Otherwise always returns a
    GateResult, including for the ordinary refusal case.
    """
    battery_path = DEFAULT_BATTERY if battery_path is None else battery_path
    scripts_dir_abs = os.path.abspath(os.path.dirname(battery_path))

    unwired, registered, exempt, reasons = [], [], [], []
    registered_modules = None  # loaded lazily: only a birth ever needs it
    exemptions = None  # loaded lazily: only an unregistered birth ever needs it

    for raw_path in paths:
        abs_path = os.path.abspath(raw_path)
        if not abs_path.endswith(".py"):
            continue  # not a part: system_doc only ever considers .py files
        file_dir = os.path.dirname(abs_path)
        if file_dir != scripts_dir_abs and not file_dir.startswith(scripts_dir_abs + os.sep):
            continue  # outside this gate's remit, same directory system_doc walks
        basename = os.path.basename(abs_path)
        if basename.startswith("test_"):
            continue  # rule 5: a test proves a part, it is never one itself
        module_name = basename[:-3]
        nested = file_dir != scripts_dir_abs

        if not _is_birth(abs_path):
            continue  # rule 4: this gate is about births, not edits

        if nested:
            is_registered = False
        else:
            if registered_modules is None:
                registered_modules = _registered_modules(battery_path)
                if registered_modules is None:
                    raise BirthGateError(
                        "the battery %s could not be read, so nothing can be "
                        "proven registered; every new part is refused until "
                        "this is fixed" % battery_path)
            is_registered = module_name in registered_modules

        if is_registered:
            registered.append(module_name)
            continue

        if exemptions is None:
            exemptions = _load_exemptions(exemptions_path)
        entry = exemptions.get(module_name)
        exemption_note = ""
        if entry is not None:
            ok, msg = _validate_exemption(entry)
            if ok:
                exempt.append(module_name)
                continue
            exemption_note = " An exemption was found but %s." % msg

        unwired.append(module_name)
        reasons.append(
            "%s is a new part with no battery registration and no valid "
            "exemption.%s To land it: %s" %
            (module_name, exemption_note, _forward_paths_note(module_name, nested)))

    allowed = not unwired
    reason = " ".join(reasons) if reasons else (
        "every path is either not a new scripts/ part, or is registered, "
        "exempt, or an edit to an existing part")
    return GateResult(allowed=allowed, unwired=unwired, registered=registered,
                       exempt=exempt, reason=reason)


def _log_refusal(log_path, paths, reason):
    """Append one JSON line. A logging failure is printed, never swallowed,
    and never turned into the refusal itself failing: the refusal already
    happened by the time this runs."""
    entry = {"time": time.time(), "paths": list(paths), "reason": reason}
    try:
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with io.open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as exc:
        print("could not write refusal log %s: %s" % (log_path, exc), file=sys.stderr)


def _count_refusals(log_path):
    """How many refusals are on record. 0 for a log that does not exist yet,
    never an error: a gate nobody has tripped yet is not a broken gate."""
    if not os.path.isfile(log_path):
        return 0
    count = 0
    with io.open(log_path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                count += 1
    return count


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", help="paths being committed")
    ap.add_argument("--battery", default=DEFAULT_BATTERY)
    ap.add_argument("--exemptions", default=DEFAULT_EXEMPTIONS)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--count-refusals", action="store_true",
                     help="print the number of logged refusals and exit")
    args = ap.parse_args(argv)

    if args.count_refusals:
        print(_count_refusals(args.log))
        return 0

    try:
        result = check_paths(args.paths, battery_path=args.battery,
                              exemptions_path=args.exemptions)
    except BirthGateError as exc:
        _log_refusal(args.log, args.paths, str(exc))
        print("BIRTH GATE COULD NOT DECIDE, REFUSED: %s" % exc, file=sys.stderr)
        return 2

    if not result.allowed:
        _log_refusal(args.log, args.paths, result.reason)
        print("BIRTH GATE REFUSED: %s" % result.reason, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
