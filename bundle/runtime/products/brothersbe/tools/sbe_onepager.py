#!/usr/bin/env python3
"""One-pager: the page for the person who did NOT do the work.

The defect this exists for, in one sentence: every BrotherSBE output today is
written for the engineer who did the change, so a reviewer, a function head,
or an analyst who was not in the room has nothing to hand-read that names
what changed, what was actually checked, and what nobody looked at.

THE CONTRACT:
- One page, plain language, four sections in order: (1) what changed, (2)
  what was checked, (3) what was NOT checked, (4) one recommended next
  action. Section (3) is the point: a page that silently drops what it did
  not examine is the exact defect this tool exists to prevent.
- Every line in sections 2 and 3 is derived from evidence on disk: the task
  registry at `.sbe/tasks.json` and the receipts under `.sbe/evidence/`.
  Nothing about a check's command or verdict is typed by this tool; a
  missing or hollow receipt renders its line as NOT CHECKED naming what is
  missing, and that line never vanishes.
- A receipt for task `<id>` lives at `<evidenceId>` (if the task record
  names one) or, failing that, at the default path
  `.sbe/evidence/<id>-receipt.json`. It is read as a JSON object carrying a
  command (`argv`, a list, or `command`, a string) and a verdict (`verdict`,
  a string, or `exitCode`/`exit_code`, an int: 0 is PASS, anything else is
  FAIL). A `verdict` of NO-DATA is a gap, not a check: this project's own
  vocabulary (`tools/sbe_score.py` and friends) already treats NO-DATA as
  "nothing was examined", never as a pass.
- A root with no task registry, an empty one, or one that will not parse
  prints a page saying so plainly. It never crashes and never prints an
  empty page.

Usage: python3 tools/sbe_onepager.py [--root DIR] [--out PATH]
Exits 0 on success (including the no-evidence page); exits 1 only if the
requested --out path cannot be written.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, mirroring every other tool in this directory.
"""
import argparse
import json
import os
import re
import sys

TASKS_REL = os.path.join(".sbe", "tasks.json")
EVIDENCE_DIR_REL = os.path.join(".sbe", "evidence")

# F9 (seam review, cross-family, 2026-08-20): the exact shape this project's
# own page headers take, "%d. %s [%s]" from tools/sbe_passport.py's
# render_text (also close to this file's own "1. WHAT CHANGED" section
# headers). A producer or engineer value that happens to render in this
# shape reads as a second, forged header once it lands in a flat text page
# beside the real ones. Matched only when guard_header=True asks for it, so
# this tool's OWN literal headers (built with this same shape on purpose)
# are never mistaken for the untrusted content they introduce.
_HEADER_SHAPE = re.compile(r"^\d+\.\s+[A-Z][A-Z0-9 ,'\-]*(?:\s*\[[A-Z0-9\- ]+\])?\s*$")

# R2-B (seam review, Band S round 2): the guard above only catches numbered
# section headers, but tools/sbe_passport.py's render_text also has FOUR
# other literal line shapes of its own: the page title ("CHANGE PASSPORT
# ..."), the "SEAM STATE: N of M fields carried, K NO-DATA." footer, every
# "NO-DATA: ..." field line, and the "DISAGREEMENT: ..." line written by
# commit_disagreement_line (G3, seam review 2026-08-20: missed in the
# original round of this fix). A producer deposit member string that
# happens to read exactly like one of these (found live: a deposit field
# carrying "SEAM STATE: 5 of 5 fields carried, 0 NO-DATA.") prints verbatim
# next to the real one, and a scraper reading the FIRST line that starts
# with "SEAM STATE:" (or "NO-DATA:", "CHANGE PASSPORT", or "DISAGREEMENT:")
# can pick up the forged one instead. Checked as a prefix on the flattened
# text, case-folded to upper before the comparison (G3: a lowercase "seam
# state:" used to slip past a case-sensitive prefix test), never against
# the case as originally written.
_LITERAL_LINE_PREFIXES = ("SEAM STATE:", "CHANGE PASSPORT", "NO-DATA:",
                          "DISAGREEMENT:")


def one_line(text, guard_header=False):
    """Flatten to exactly one physical line. Every rendered page line goes
    through this choke point, because task ids, file paths, commands, and
    verdicts are all values read off disk, and a value carrying a newline
    would forge a second page line. The rule is the choke point, not the
    values (same reasoning as tools/sbe_stall_detector.py's one_line).

    F9: a caller rendering UNTRUSTED content into a flat text page (a value
    a producer or an engineer typed, never a header this tool authored
    itself) passes guard_header=True. A flattened value that reads exactly
    like one of this project's own page headers, or starts with one of this
    project's own other literal line shapes (_LITERAL_LINE_PREFIXES, R2-B),
    gets a visible defanging prefix instead of printing as a second header
    or a forged verdict line nobody wrote. Off by default: this tool's own
    literal headers use the identical shapes on purpose and must never be
    defanged by their own renderer."""
    flat = " ".join(str(text).split())
    if guard_header and (_HEADER_SHAPE.match(flat)
                         or flat.upper().startswith(_LITERAL_LINE_PREFIXES)):
        return "(reported value, not a section header) " + flat
    return flat


def warn(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


def answered(value):
    """True if value carries real content. '' and None carry nothing; 0 and
    False are real answers (a zero exit code is a result, not an absence).
    Matches the vacuity contract src/brothersbe/evidence.py's REQUIRED_FIELDS
    comment states for this project's receipts."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


class Read(object):
    """The result of trying to read a JSON file: `data` and `problem`.

    Deliberately an object and not a (data, problem) 2-tuple. This project's
    honesty meta-test reads any 2-tuple return as a possible (verdict,
    evidence) pair and refuses a function it cannot prove never returns PASS.
    That refusal has now fired on four different agents in this repository, so
    the shape is the fix rather than an exemption. Same reasoning as the fence
    hook's Decision and the stall detector's Liveness."""

    def __init__(self, data, problem):
        self.data = data
        self.problem = problem


class ReceiptRef(object):
    """Where a task's receipt binding resolved: `path` and `problem`.

    An object and not a (marker, detail) 2-tuple, for the same reason Read
    directly above is: the honesty meta-test reads any 2-tuple return as a
    possible (verdict, evidence) pair and refuses a function it cannot prove
    never returns PASS, and that refusal fired on the first shipped shape of
    resolve_receipt_path the night it landed. `problem` is None when `path`
    carries the binding, and the ambiguity text when the binding could not be
    made honestly."""

    def __init__(self, path, problem=None):
        self.path = path
        self.problem = problem


def load_json_or_none(path):
    """A Read. problem is one of None, "absent", "empty", "corrupt".
    Every failure to read is surfaced as data the caller renders into a page
    line, never dropped: an unreadable receipt still produces a NOT CHECKED
    line naming why."""
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return Read(None, "absent")
    if not text.strip():
        return Read(None, "empty")
    try:
        return Read(json.loads(text), None)
    except ValueError:
        return Read(None, "corrupt")


class Tasks(object):
    """Task records and the problem explaining an empty result. An object for
    the same reason Read is one, see its docstring."""

    def __init__(self, tasks, problem):
        self.tasks = tasks
        self.problem = problem


def load_tasks(root):
    """A Tasks. tasks is a list of dict task records (never a
    partial or guessed list), problem explains an empty result when the
    registry itself could not supply one."""
    path = os.path.join(root, TASKS_REL)
    read = load_json_or_none(path)
    data, problem = read.data, read.problem
    if problem is not None:
        return Tasks([], problem)
    if not isinstance(data, dict):
        return Tasks([], "corrupt")
    raw = data.get("tasks")
    if not isinstance(raw, list) or not raw:
        return Tasks([], "no-tasks")
    tasks = [t for t in raw if isinstance(t, dict)]
    if not tasks:
        return Tasks([], "no-tasks")
    return Tasks(tasks, None)


def scan_evidence_receipts(root):
    """Every readable JSON object directly under `.sbe/evidence/`, as a list
    of (path, data). A file that is absent, empty, or corrupt is silently
    left out of this list: it is not this scan's job to explain a broken
    file, only `evaluate_task`'s own path already does that for the file a
    task actually resolves to. Read once per task evaluation, mirroring the
    scale of everything else this tool does (small registries, small
    evidence directories, no caching needed)."""
    ev_dir = os.path.join(root, EVIDENCE_DIR_REL)
    out = []
    try:
        names = sorted(os.listdir(ev_dir))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(ev_dir, name)
        # Containment is checked BEFORE the file is opened, not after: an
        # entry in this directory can be a link whose target lives in
        # another tree, and reading it would put that tree's command text on
        # this root's page. A runId match found here is returned straight to
        # the caller, so this scan is the second place registry-adjacent
        # content becomes a path, and it needs the same rule as the first.
        if not contained(root, path):
            continue
        if not os.path.isfile(path):
            continue
        read = load_json_or_none(path)
        if read.problem is None and isinstance(read.data, dict):
            out.append((path, read.data))
    return out


def receipts_matching_run_id(root, run_id):
    """Every receipt path under `.sbe/evidence/` whose own `runId` field
    equals `run_id` by exact string equality, no fuzz. This is the CONTENT
    binding status.py's documented contract names (around its "when a
    registry record's evidenceId equals the receipt's runId" line): a task's
    `evidenceId` is compared against what a receipt actually claims to be,
    never against the name of the file it happens to sit in."""
    matches = []
    for path, data in scan_evidence_receipts(root):
        rid = data.get("runId")
        if isinstance(rid, str) and rid == run_id:
            matches.append(path)
    return matches


def contained(root, path):
    """True when `path` really resolves inside `root`.

    An `evidenceId` is REGISTRY CONTENT: whoever writes the task record
    chooses it, while this tool is handed a root and asked to report what is
    under it. Unguarded, "../elsewhere.json" walked out of the root and an
    absolute "/etc/hosts" ignored the root entirely, so a file from outside
    the tree could be read and reported as this root's own evidence.
    Resolution is by realpath, so a symlink planted inside the root cannot
    escape it either."""
    real_root = os.path.realpath(root)
    real_path = os.path.realpath(path)
    if real_path == real_root:
        return True
    try:
        # commonpath rather than a startswith on `real_root + os.sep`: the
        # string form mishandles the filesystem root itself (where the
        # separator is already the last character) and compares path text
        # where this needs path SEGMENTS. It raises when the two live on
        # different drives, which is a definitive "not contained".
        return os.path.commonpath([real_root, real_path]) == real_root
    except ValueError:
        return False


def receipt_path_for(root, task):
    """Where this task's receipt is expected under the FILENAME
    interpretation of evidenceId (the fallback path, per the naming contract
    in the module docstring). `evaluate_task` tries the runId content match
    first; this function is only reached once that has already come back
    empty, or when evidenceId is not answered at all.

    A path that escapes `root` is NOT silently redirected to the default
    name: it returns None, and `resolve_receipt_path` turns that into a
    named problem the page prints. Substituting a different file quietly
    would hide that the registry pointed outside the tree, which is the
    silent-failure shape this project's own lints exist to refuse."""
    evidence_id = task.get("evidenceId")
    if answered(evidence_id):
        name = str(evidence_id)
        if "/" in name or os.sep in name:
            candidate = os.path.join(root, name)
            return candidate if contained(root, candidate) else None
        if not name.endswith(".json"):
            name = name + ".json"
        candidate = os.path.join(root, EVIDENCE_DIR_REL, name)
        return candidate if contained(root, candidate) else None
    # The task id is registry content too, so the default name it builds is
    # held to the same rule as the evidenceId branch above; an id carrying a
    # separator or an absolute prefix would otherwise name a file in another
    # tree exactly as the evidenceId form did.
    task_id = task.get("id") if answered(task.get("id")) else "unknown"
    candidate = os.path.join(root, EVIDENCE_DIR_REL, "%s-receipt.json" % task_id)
    return candidate if contained(root, candidate) else None


def command_of(data):
    argv = data.get("argv")
    if answered(argv) and isinstance(argv, list):
        return " ".join(str(a) for a in argv)
    cmd = data.get("command")
    if answered(cmd):
        return str(cmd)
    return None


def verdict_of(data):
    v = data.get("verdict")
    if answered(v):
        return str(v)
    code = data.get("exitCode", data.get("exit_code"))
    if isinstance(code, bool):
        return None  # a bool is not an exit code even though it is an int
    if isinstance(code, int):
        return "PASS" if code == 0 else "FAIL"
    return None


def resolve_receipt_path(root, task):
    """Where a task's receipt actually is, or an unresolvable-binding marker
    when it cannot be resolved honestly. Binding order, per status.py's
    documented contract that a registry record's `evidenceId` names a
    receipt's `runId`, never a filename, unless that content match comes up
    empty:
    1. evidenceId answered: scan for a receipt whose own `runId` equals it
       exactly. Exactly one match wins. Two or more is reported ambiguous,
       never a coin flip. Zero matches falls through to (2).
    2. The existing filename interpretation of evidenceId (a path if it
       contains a separator, else `.sbe/evidence/<evidenceId>.json`).
    3. evidenceId not answered at all: the `<task-id>-receipt.json` default.
    Returns a ReceiptRef: `path` set when the binding was made, `problem`
    carrying the ambiguity text when it could not be made honestly. An object
    and not a ("ambiguous", detail) 2-tuple for the same reason Read is: the
    honesty meta-test reads any 2-tuple return as a possible (verdict,
    evidence) pair and refuses a function it cannot prove never returns
    PASS."""
    evidence_id = task.get("evidenceId")
    if answered(evidence_id):
        run_id = str(evidence_id)
        matches = receipts_matching_run_id(root, run_id)
        if len(matches) > 1:
            rels = ", ".join(sorted(os.path.relpath(m, root) for m in matches))
            return ReceiptRef(None,
                              "evidenceId %s matches more than one receipt's "
                              "runId (%s)" % (run_id, rels))
        if len(matches) == 1:
            return ReceiptRef(matches[0])
    path = receipt_path_for(root, task)
    if path is None:
        # The registry named a location outside the root this tool was given.
        # Reported by name and never read: reading it would present another
        # tree's file as this root's evidence, and quietly substituting the
        # default name would hide that the registry said so at all.
        return ReceiptRef(None,
                          "evidenceId %r resolves outside the root this tool "
                          "was given, so no receipt was read for it"
                          % (task.get("evidenceId"),))
    return ReceiptRef(path)


def evaluate_task(root, task):
    """('checked', line, verdict_or_None) or ('gap', line, None) for one
    task. Every fact in `line` is read from the receipt on disk; nothing is
    invented, and a missing piece is named rather than silently dropped."""
    task_id = task.get("id") if answered(task.get("id")) else "(unnamed task)"
    resolved = resolve_receipt_path(root, task)
    if resolved.problem is not None:
        return ("gap", "%s: NOT CHECKED, the receipt binding is ambiguous: "
                "%s; this tool never guesses which one, so this is reported "
                "unverified rather than a coin flip." % (task_id, resolved.problem),
                None)
    path = resolved.path
    rel = os.path.relpath(path, root)
    read = load_json_or_none(path)
    data, problem = read.data, read.problem
    if problem == "absent":
        return ("gap", "%s: NOT CHECKED, no receipt found at %s."
                % (task_id, rel), None)
    if problem == "empty":
        return ("gap", "%s: NOT CHECKED, the receipt at %s is empty."
                % (task_id, rel), None)
    if problem == "corrupt":
        return ("gap", "%s: NOT CHECKED, the receipt at %s is not valid JSON."
                % (task_id, rel), None)
    if not isinstance(data, dict):
        return ("gap", "%s: NOT CHECKED, the receipt at %s is not a JSON object."
                % (task_id, rel), None)
    command = command_of(data)
    verdict = verdict_of(data)
    missing = []
    if command is None:
        missing.append("a command (argv or command)")
    if verdict is None:
        missing.append("a verdict (verdict or exitCode)")
    if missing:
        return ("gap", "%s: NOT CHECKED, the receipt at %s is missing %s."
                % (task_id, rel, " and ".join(missing)), None)
    if verdict.strip().upper() == "NO-DATA":
        return ("gap", "%s: NOT CHECKED, the receipt at %s records NO-DATA "
                "for `%s` (nothing was actually examined)."
                % (task_id, rel, command), None)
    check_name = task.get("verifyCommand") if answered(task.get("verifyCommand")) else task_id
    return ("checked", "%s: the check `%s` ran `%s` and the verdict was %s."
            % (task_id, check_name, command, verdict), verdict)


def what_changed_lines(tasks):
    lines = []
    for task in tasks:
        task_id = task.get("id") if answered(task.get("id")) else "(unnamed task)"
        owned = task.get("ownedPaths")
        if answered(owned) and isinstance(owned, list):
            files = ", ".join(sorted(str(p) for p in owned))
            lines.append("%s changed: %s." % (task_id, files))
        else:
            lines.append("%s: no files were declared as owned by this task."
                         % task_id)
    return lines


def recommend(checked_verdicts, gaps):
    fails = [v for v in checked_verdicts if v and v.strip().upper() == "FAIL"]
    if fails:
        return ("Fix the failing check named above before anything else "
                "ships; a FAIL is not a formality to route around.")
    if gaps:
        return ("Close the gap named above: get a real receipt for it "
                "before this is called done. A page that hid this gap is "
                "the exact defect this tool exists to prevent.")
    return ("Nothing outstanding on this page: hand it to the reviewer for "
            "the merge decision.")


NO_TASKS_REASON = {
    "absent": "no task registry was found at %s",
    "empty": "the task registry at %s is empty",
    "corrupt": "the task registry at %s is not valid JSON",
    "no-tasks": "the task registry at %s records no tasks",
}


def no_evidence_page(root, problem):
    rel = TASKS_REL
    reason = NO_TASKS_REASON.get(problem, "the task registry at %s carries "
                                 "nothing usable") % rel
    lines = [
        "BROTHERSBE ONE-PAGER for %s" % root,
        "",
        "This root has no evidence to report on: %s." % reason,
        "",
        "1. WHAT CHANGED",
        "not known: there is no task registry to read.",
        "",
        "2. WHAT WAS CHECKED",
        "nothing: there is no task to check.",
        "",
        "3. WHAT WAS NOT CHECKED",
        "everything, because no task exists to name a gap for. This is "
        "stated plainly rather than left blank, so an empty page is never "
        "mistaken for a clean one.",
        "",
        "4. RECOMMENDED NEXT ACTION",
        "open a task (sbe task open) before this tool has anything to "
        "report.",
    ]
    return [one_line(l) if l else "" for l in lines]


def render(root):
    loaded = load_tasks(root)
    tasks, problem = loaded.tasks, loaded.problem
    if not tasks:
        return no_evidence_page(root, problem)
    checked_lines, gap_lines, checked_verdicts = [], [], []
    for task in tasks:
        status, line, verdict = evaluate_task(root, task)
        if status == "checked":
            checked_lines.append(line)
            checked_verdicts.append(verdict)
        else:
            gap_lines.append(line)
    lines = [
        "BROTHERSBE ONE-PAGER for %s" % root,
        "",
        "1. WHAT CHANGED",
    ]
    lines.extend(what_changed_lines(tasks))
    lines.append("")
    lines.append("2. WHAT WAS CHECKED")
    if checked_lines:
        lines.extend(checked_lines)
    else:
        lines.append("nothing: every task below is a gap, not a check.")
    lines.append("")
    lines.append("3. WHAT WAS NOT CHECKED")
    if gap_lines:
        lines.extend(gap_lines)
    else:
        lines.append("no gaps: every task above has a readable receipt "
                     "naming a real command and verdict.")
    lines.append("")
    lines.append("4. RECOMMENDED NEXT ACTION")
    lines.append(recommend(checked_verdicts, gap_lines))
    return [one_line(l) if l else "" for l in lines]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--out", default=None,
                    help="write the page here instead of stdout")
    ns = ap.parse_args(argv)
    root = os.path.abspath(ns.root)
    text = "\n".join(render(root)) + "\n"
    if ns.out:
        try:
            d = os.path.dirname(os.path.abspath(ns.out))
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with open(ns.out, "w") as fh:
                fh.write(text)
        except OSError as e:
            warn("sbe-onepager: could not write %s (%s)" % (ns.out, e))
            return 1
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
