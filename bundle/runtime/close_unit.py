#!/usr/bin/env python3
"""Close a unit: run its own done_check AFTER the last edit, and mark it DONE only if that command passed.

usage (launch worktree root): python3 scripts/close_unit.py [UNIT ...] [--dry]
With no unit named, every unit whose sub units are all landed but whose state is not DONE.
Exit 0 when at least one unit closed, 1 when none did, 2 when the plan could not be read.

WHY. Whole units DONE is the number the owner watches, and it moved once in a night while 60 sub units landed,
because closing was a manual step that kept being deferred. It is mechanical: the plan already names each unit's
done_check, and the rule is already written down. What it must never become is a rubber stamp.

THE THREE GUARDS, each one a thing that has gone wrong on this estate:
  1. EVERY sub unit must be landed per the plan's own evidence. A unit is not done because its tests pass; it is
     done when its work is in and its tests pass.
  2. The done_check must EXIT ZERO, read from the process, never from text in its output. A gate here once printed
     FAIL and exited 0 and eleven tests passed over it.
  3. The check's own last lines are quoted verbatim into the evidence. A DONE with no quoted output is a claim,
     and this estate excludes claims from every count."""
import argparse
import datetime
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

# This program is the reason provenance exists: it sets DONE, stamps state_at and appends a closure paragraph,
# unattended, on every loop pass, and recorded no author at all. A HARD import on purpose: provenance.py ships
# beside this file, so a missing one means a broken checkout, and closing units silently unattributed is exactly
# the state that was measured on 2026-09-21. The SOFT failure path is inside journal(), which returns False on a
# bad disk rather than stopping a closure, because a provenance gap must never block the work it describes.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import provenance  # noqa: E402  (sibling module, scripts/loop/provenance.py)
import plan_store  # noqa: E402  (the one plan writer, and the one landed test: plan_store.sub_landed)
import plan_path as _PP  # noqa: E402  (the one plan selector: BROTHER_WBS or the default, an invalid value refuses)
import proof_accept  # noqa: E402  (the one revert rule, proof_accept.REVERTS, read by landing_refusal)

PLAN = _PP.selected()



# The done_check string comes from the PLAN FILE, and spec intake writes plan fields from model drafts, so it is
# model authored data and not trusted text. Running it through a shell would execute whatever a draft contained.
# This screen allows exactly the shape every real done_check on this estate has: one or more python3 invocations
# of repo relative paths or modules, chained with &&. Anything else is refused and the unit is simply not closed.
from plan_lint import screen_done_check  # noqa: E402  (one screen for the closer, the lint and the pool)



def write_atomic(path, text):
    """Write through a temporary file and rename onto the target. A truncating open() on the plan destroys it if
    anything interrupts the write, and an unattended loop at 04:00 has nobody to notice. os.replace is atomic on
    the same filesystem, so a reader sees either the old plan or the new one, never a half written one."""
    if not isinstance(path, str) or not path:
        raise ValueError("write_atomic: path must be a non empty str, not %s" % type(path).__name__)
    if not isinstance(text, str):
        raise ValueError("write_atomic: text must be str, not %s" % type(text).__name__)
    if os.path.isdir(path):
        raise ValueError("write_atomic: refusing to write %r: it is a directory" % path)
    parent = os.path.dirname(path) or "."
    if not os.path.isdir(parent):
        raise ValueError("write_atomic: refusing to write %r: its directory does not exist" % path)
    tmp = path + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def landed_subs(plan):
    """Landed sub unit ids, judged only from the plan's own evidence.

    The plan is model authored data, so a value that is not the shape this function reads is REFUSED
    with ValueError, never answered with an empty set (which is the answer a plan that really has no
    landings gives, so a caller could not tell the two apart) and never crashed into with a raw
    AttributeError or TypeError. Refusing keeps every unit open, which is the fail closed direction."""
    if not isinstance(plan, dict):
        raise ValueError("landed_subs: plan must be a dict, got %s" % type(plan).__name__)
    units = plan.get("units")
    if not isinstance(units, list):
        raise ValueError("landed_subs: plan['units'] must be a list, got %s" % type(units).__name__)
    out = set()
    for unit in units:
        if not isinstance(unit, dict):
            raise ValueError("landed_subs: every unit must be a dict, got %s" % type(unit).__name__)
        ev = unit.get("evidence")
        if ev is None:
            ev = ""
        if not isinstance(ev, str):
            raise ValueError("landed_subs: unit['evidence'] must be a str, got %s" % type(ev).__name__)
        subs = unit.get("sub_units")
        if subs is None:
            subs = []
        if not isinstance(subs, list):
            raise ValueError("landed_subs: unit['sub_units'] must be a list, got %s" % type(subs).__name__)
        for sub in subs:
            if not isinstance(sub, str):
                raise ValueError("landed_subs: every sub unit id must be a str, got %s" % type(sub).__name__)
            if plan_store.sub_landed(sub, ev):
                out.add(sub)
    return out


def closable(plan, landed):
    """Units whose every sub unit is landed, that are not DONE, and that HAVE a done_check to run.
    A unit with sub units but no done_check is never closed here: there would be nothing to prove it.

    The plan and the landed collection are model authored data, so a value that is not the shape this
    function reads is REFUSED with ValueError, never answered with an empty list (the answer a plan with
    nothing closable gives, so a caller could not tell the two apart) and never crashed into. Refusing
    keeps every unit open, which is the fail closed direction."""
    if not isinstance(plan, dict):
        raise ValueError("closable: plan must be a dict, got %s" % type(plan).__name__)
    if not isinstance(landed, (set, frozenset, list, tuple, dict)):
        raise ValueError("closable: landed must be a set, frozenset, list, tuple or dict, got %s" % type(landed).__name__)
    units = plan.get("units")
    if not isinstance(units, list):
        raise ValueError("closable: plan['units'] must be a list, got %s" % type(units).__name__)
    out = []
    for unit in units:
        if not isinstance(unit, dict):
            raise ValueError("closable: every unit must be a dict, got %s" % type(unit).__name__)
        subs = unit.get("sub_units")
        if subs is None:
            subs = []
        if not isinstance(subs, list):
            raise ValueError("closable: unit['sub_units'] must be a list, got %s" % type(subs).__name__)
        # RETIRED is the owner withdrawing the unit (plan_store.NOT_IN_RELEASE): closing it would overwrite that
        # ruling with DONE whenever its check passed, and screening its check every pass only repeats a refusal
        # nobody will act on (FX-49, 2026-10-03). DEFERRED stays closable: its work may land and pass.
        if not subs or unit.get("state") in ("DONE", "RETIRED"):
            continue
        done_check = unit.get("done_check")
        if not isinstance(done_check, str) or not done_check.strip():
            continue
        for s in subs:
            if not isinstance(s, str):
                raise ValueError("closable: every sub unit id must be a str, got %s" % type(s).__name__)
        uid = unit.get("id")
        if not isinstance(uid, str):
            raise ValueError("closable: unit['id'] must be a str, got %s" % type(uid).__name__)
        if all(s in landed for s in subs):
            out.append(uid)
    return out


def legacy(unit):
    """True for a unit with no landing_events key at all: every unit landed before LOOP1.d (2026-10-31). P2b (owner,
    2026-10-31, "P2b, integrator's"): such a unit closes on its prose as before, and the closer prints
    `LEGACY <unit>: no landing events` whenever it does. KNOWN GAP: a reverted legacy landing still reads landed (RC07
    stays open for legacy units only). Every landing since writes the key (land_batch.landing_records), so no new unit is
    legacy; flip: once the open units are backfilled with events, a follow up removes this path."""
    return "landing_events" not in unit


def facts_refusal(unit):
    """'' when, for every sub unit, the prose reader (plan_store.sub_landed) and the landing events
    (plan_store.landing_facts) both say landed, else why the unit stays open. Pure: read again under the plan lock.
    LOOP1.d: during the migration both readers are compared, and a disagreement is printed and never closes work. A
    legacy unit (no landing_events key) is the one exception: its prose decides (see legacy)."""
    if legacy(unit):
        return ""
    events = unit.get("landing_events")
    for sub in unit.get("sub_units") or []:
        prose = plan_store.sub_landed(sub, unit.get("evidence") or "")
        got = plan_store.landing_facts([] if events is None else events, unit.get("id"), sub)
        if prose != got["landed"]:
            return "DISAGREE %s: prose=%s events=%s" % (sub, "landed" if prose else "not landed",
                                                         "landed" if got["landed"] else "not landed (%s)" % got["reason"])
        if not prose:
            return "%s is not landed (%s)" % (sub, got["reason"])
        if got["evidence_id"] not in plan_store.evidence_ids(sub, unit.get("evidence") or ""):
            return "DISAGREE %s: its landing event's evidence_id %s hashes no line of the unit's evidence" % (
                sub, got["evidence_id"][:12])
    return ""


def landing_key(unit):
    """What git confirmed for a unit: per sub unit, the current landing's (base, evidence_id). The locked write closes
    only when the unit re-read under the lock still has exactly this key: a re-land or a revert appended after the git
    check names a landing git never confirmed (adversarial review, 2026-10-31)."""
    events = unit.get("landing_events") or []
    return tuple((s, plan_store.landing_facts(events, unit.get("id"), s)["commit"],
                  plan_store.landing_facts(events, unit.get("id"), s)["evidence_id"]) for s in unit.get("sub_units") or [])


def landing_refusal(unit, plan_path=None):
    """'' when facts_refusal agrees AND git confirms every sub unit's landing in this history, else why the unit stays
    open. Per sub unit, from its current landing event (P1, ruled 2026-10-31: `commit` is the landing's base):
    the base is an ancestor of HEAD; EXACTLY ONE commit after the base added the event's evidence_id to the plan (the
    integrator's tightening, 2026-10-31: none is an event no commit carries, two or more is a re-land after a revert,
    and both are NO-DATA); and no later commit says `This reverts commit <it>` (proof_accept.REVERTS, the one revert
    rule), or names a merge that brought it in. FAIL DIRECTION: git that cannot answer is NO-DATA and keeps the unit
    open, never a pass. KNOWN LIMIT: a revert by hand, with no such message, is not read."""
    why = facts_refusal(unit)
    if why:
        return why
    if legacy(unit):
        return ""   # nothing structured to confirm: the prose decides (P2b, a named gap)
    plan_path = plan_path or PLAN

    class Unanswered(Exception):
        pass

    def git(*argv, ok=(0,)):
        """The process for `git argv` when it exited with one of `ok`; anything else is one NO-DATA, raised."""
        try:
            r = subprocess.run(["git"] + list(argv), capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Unanswered("git %s: %s" % (argv[0], exc))
        if r.returncode not in ok:
            raise Unanswered("git %s exited %d" % (argv[0], r.returncode))
        return r
    try:
        for sub in unit.get("sub_units") or []:
            why = _git_refusal(sub, unit, plan_path, git)
            if why:
                return why
    except Unanswered as exc:
        return "NO-DATA: %s, so no landing of %s is confirmed in this history" % (exc, unit.get("id"))
    return ""


def _git_refusal(sub, unit, plan_path, git):
    """landing_refusal's git reading for one sub unit: '' when confirmed, else why not. `git` raises when git cannot
    answer, so every failure to read the history is one NO-DATA in the caller."""
    got = plan_store.landing_facts(unit.get("landing_events") or [], unit.get("id"), sub)
    base, eid = got["commit"], got["evidence_id"]
    # exit 1 only for an object this repository does not hold (the ^{commit} form exits 128 for that too)
    if git("cat-file", "-e", base, ok=(0, 1)).returncode == 1:
        return "%s: its landing base %s is not in this repository, so not an ancestor of HEAD" % (sub, base[:12])
    if git("merge-base", "--is-ancestor", base, "HEAD", ok=(0, 1)).returncode == 1:
        return "%s: its landing base %s is not an ancestor of HEAD" % (sub, base[:12])
    # every commit that changed how often the evidence_id appears, with its diff: it ADDED the event when its added lines
    # carry the id more often than its removed lines (a one line JSON plan rewrites the whole line, so both sides carry it
    # and only the difference tells an addition from a removal)
    log = git("log", "--no-color", "--no-ext-diff", "--format=%x00%H", "-p", "-S" + eid, "%s..HEAD" % base, "--",
              plan_path).stdout
    added, needle = [], eid.encode("ascii")
    for chunk in log.split(b"\0")[1:]:
        sha, _, diff = chunk.partition(b"\n")
        lines = diff.splitlines()
        plus = sum(l.count(needle) for l in lines if l.startswith(b"+") and not l.startswith(b"+++"))
        minus = sum(l.count(needle) for l in lines if l.startswith(b"-") and not l.startswith(b"---"))
        if plus > minus:
            added.append(sha.decode("ascii", "replace").strip())
    if len(added) != 1:
        return ("NO-DATA %s: %d commits after its base %s added its landing event to %s, where exactly one must "
                "(none: no commit carries it; two or more: a re-land after a revert)" % (sub, len(added), base[:12], plan_path))
    landing = added[0]
    body = git("log", "--format=%B", "%s..HEAD" % landing).stdout
    for named in sorted({m.decode("ascii").lower() for m in proof_accept.REVERTS.findall(body)}):
        if landing.startswith(named):
            return "%s: its landing commit %s was reverted later in this history" % (sub, landing[:12])
        # A REVERTED MERGE (adversarial review, 2026-10-31): `git revert -m 1 <merge>` names the merge that brought the
        # landing in, never the landing; that merge reaches the landing and its first parent does not. A revert by hand,
        # with no such message, is beyond what history can tell and is named as a limit, not read.
        found = git("rev-parse", "--verify", "-q", named + "^{commit}", ok=(0, 1))
        if found.returncode:
            continue
        merge = found.stdout.decode("ascii", "replace").strip()
        parents = git("rev-list", "--parents", "-n", "1", merge).stdout.decode("ascii", "replace").split()[1:]
        if (len(parents) > 1 and git("merge-base", "--is-ancestor", landing, merge, ok=(0, 1)).returncode == 0
                and git("merge-base", "--is-ancestor", landing, parents[0], ok=(0, 1)).returncode == 1):
            return "%s: the merge %s that brought in its landing commit %s was reverted later in this history" % (
                sub, merge[:12], landing[:12])
    return ""


def verdict(returncode, output):
    """(ok, quoted). ok ONLY when the process exited zero: the exit code decides, never the printed words.
    quoted is the check's own result lines, which is what goes into the evidence."""
    # "selftest: N cases, OK" and "PASS: ..." are this estate's own result shapes (most done checks are --selftest);
    # quoting only the unittest shapes left "no result line" on every unit closed by a selftest (six rows, 2026-09-24)
    # FX-15.6. A NO-DATA reason is the one thing the owner can act on when the closer refuses with exit 2, so it
    # must reach the log even when the check printed result lines before or after it and the four line window
    # slid past. Keeping only the NO-DATA lines, when any are present, is additive: the exit code still decides
    # ok, only the quoted reason widens.
    lines = result_lines(output)
    nodata = [l for l in lines if l.startswith("NO-DATA:")]
    # A ZERO EXIT THAT PRINTED NO-DATA MEASURED NOTHING (LOOP1.c review, 2026-11-01): the line was counted as a result line,
    # so the unit closed having proved nothing. Not ok, the reason quoted; the unit stays open (NO-DATA is never a pass).
    # The verdict is a step's LAST result line (review round 2): a green suite whose fixtures print NO-DATA lines before
    # its own OK measured something, and refusing it would hold five recorded closures (CF1 among them) open forever.
    # Only the "NO-DATA:" verdict shape counts (round 3): a journey's "NO-DATA  <row>" is a data row, and a walk is judged
    # by its exit code over its --expect rows, so an unexpected trailing row must not hold the unit open.
    if returncode == 0:
        for step in (output or "").split(STEP_BREAK):
            last = result_lines(step)[-1:]
            if last and last[0].startswith("NO-DATA:"):
                return False, " ".join(nodata[-4:] or last)[:400]
    if nodata:
        lines = nodata
    # A FAILED check whose output carries none of the shapes above still said why: its last non empty line is the
    # reason (U8's "NOT DONE    4 of 5 catalog(s)...", a traceback's exception line). Quoting nothing there left the
    # loop report reading "exited 1:" and nothing else (2026-10-03). A pass keeps "no result line": unchanged.
    if not lines and returncode != 0:
        lines = [l.strip() for l in (output or "").splitlines() if l.strip() and l.strip() != STEP_BREAK][-1:]
    # A SKIP IS NOT A PASS (A1, I12, 2026-10-19): CY1.a closed on "OK (skipped=11)" of 15 and C12 on "OK (skipped=1)", because
    # the OK word was read and skipped=N never was. A zero exit with N above 0 is PARTIAL: not ok, the skipping tests named
    # first so the reason survives the 400 character cut.
    n, names = skipped(output)
    if returncode == 0 and n:
        named = ", ".join(names) if names else "not named in the output (run the done check with -v to name them)"
        return False, ("PARTIAL: %d test(s) skipped: %s. %s" % (n, named, " ".join(lines[-2:])))[:400]
    # A SHELL EXIT IS NOT A PHASE VERDICT (LOOP1.c, 2026-11-01): the exit code still decides a FAIL, but a zero exit that
    # printed no result line at all is a claim, not a proof, and this estate excludes claims from every count (guard 3
    # above; six units closed on "printed: no result line" on 2026-10-29). FAIL DIRECTION: the unit stays open, and the
    # refusal names the missing line so its check can be made to print one. EACH STEP carries its own (review, 2026-11-01):
    # run_done_check marks where one step's output ends, and the result lines were once counted across all of them, so a
    # last step that printed nothing closed on an earlier step's OK. Plain output with no mark is one step.
    if returncode == 0:
        for i, step in enumerate((output or "").split(STEP_BREAK), 1):
            if not result_lines(step):
                return False, ("NO-VERDICT: step %d of the done_check exited 0 but printed no result line, so the exit "
                               "alone is not a verdict" % i)
    return returncode == 0, " ".join(lines[-4:])[:400]


#: written by run_done_check between the steps of a chained done check, so verdict() can read each step's own output
STEP_BREAK = "=== done_check step break ==="
#: a line shape the closer reads as a result: unittest's, the estate's own selftest/PASS/FAIL/GREEN/RED, a journey row
RESULT_PREFIXES = ("Ran ", "OK", "FAILED", "ERROR", "selftest:", "PASS:", "FAIL:", "NO-DATA:", "GREEN:", "RED:",
                   #: a journey walk's row: the verdict word, spaces, the row id (2026-10-29: two units
                   #: closed on walks with "no result line" quoted, which is a DONE with no evidence)
                   "PASS  ", "FAIL  ", "NO-DATA  ")


def result_lines(output):
    """The result lines of `output`, stripped, in order."""
    return [l.strip() for l in (output or "").splitlines()
            if l.startswith(RESULT_PREFIXES) or l.strip() in ("OK", "PASS", "FAIL")]


#: unittest's summary line, OK or FAILED, carrying a skipped=N count
SKIPPED_RE = re.compile(r"^(?:OK|FAILED) \(.*\bskipped=(\d+)")
#: a verbose (-v) skip line: "test_x (mod.Class) ... skipped 'why'" (3.9) or "test_x (mod.Class.test_x) ..." (3.11+);
#: a test with a docstring prints its id on the line before, and the docstring line carries the "... skipped"
SKIP_LINE_RE = re.compile(r"^(?P<head>.*?) \.\.\. skipped\b")
TEST_ID_RE = re.compile(r"^(\w+) \(([\w.]+)\)")


def skip_records(output):
    """[{"test", "reason"}] for every skipped test a verbose (-v) run names, in order, one per test. The reason is the text
    unittest printed after "skipped", quotes stripped ('' when it printed none). Without -v unittest names nothing."""
    lines = (output or "").splitlines()
    out, seen = [], set()
    for i, line in enumerate(lines):
        m = SKIP_LINE_RE.match(line)
        if not m:
            continue
        t = TEST_ID_RE.match(m.group("head")) or (TEST_ID_RE.match(lines[i - 1]) if i else None)
        if not t:
            continue
        test = t.group(2) if t.group(2).endswith("." + t.group(1)) else "%s.%s" % (t.group(2), t.group(1))
        if test in seen:
            continue
        seen.add(test)
        reason = line[m.end():].strip()
        if len(reason) >= 2 and reason[0] == reason[-1] and reason[0] in "'\"":
            reason = reason[1:-1]
        out.append({"test": test, "reason": reason})
    return out


def skipped(output):
    """(N, names): the skipped tests summed over every unittest summary in output (a chained done check prints one per
    step), and the tests a verbose run names. Without -v unittest names none, so names may be shorter than N."""
    n = sum(int(m.group(1)) for m in (SKIPPED_RE.match(l.strip()) for l in (output or "").splitlines()) if m)
    return n, [r["test"] for r in skip_records(output)]


#: STAB1.f. A skip is a question only a person can answer (why does this test not run here?), so the closer asks it ONCE:
#: one record per distinct ask in this append only file, and a second pass over the same skip finds the record and writes
#: nothing. Before, the same skip printed a red line on every pass, forever, and nobody was asked anything.
def evidence_dir():
    """Where the closer keeps its records: BROTHER_EVIDENCE (the variable pass_digest reads), else ~/.claude/evidence."""
    return os.path.expanduser(os.environ.get("BROTHER_EVIDENCE") or "~/.claude/evidence")


def ask_once(row, path=None):
    """'asked' when this call wrote the NEEDS_INPUT record, 'already' when an identical ask (same `key`) is on file, else
    'unrecorded: <why>'. Read and appended under one exclusive lock, so two closers racing write one record. FAIL
    DIRECTION: a log that cannot be read or written is 'unrecorded', and the caller still prints NEEDS_INPUT and closes
    nothing; the ask is never lost by pretending it was recorded. A torn or foreign line is skipped, never trusted."""
    import fcntl
    path = path or os.path.join(evidence_dir(), "brother-needs-input.jsonl")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # errors="replace": one non UTF-8 byte in the log (a torn write, a foreign editor) is a bad line to skip, never a crash
        with open(path, "a+", encoding="utf-8", errors="replace") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.seek(0)
            for line in fh:
                try:
                    if json.loads(line).get("key") == row["key"]:
                        return "already"
                except (ValueError, AttributeError):
                    continue   # a torn or foreign line is not an ask on file
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return "asked"
    except (OSError, ValueError) as exc:
        return "unrecorded: %s" % exc


def ask_key(*parts):
    """A stable id for one ask: the same unit, the same skipped tests and reasons, the same run give the same key."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()[:16]


SHOWN_REASON = 120   # characters of one skip reason on the stdout line; the record keeps the whole reason


def shown(text, cap=SHOWN_REASON):
    """`text` safe to print inside a closer verdict line: control characters (a newline included) become spaces, the cap is
    kept, and the token `DONE:` that merge_window.sh and loop_pass.sh read as a closure is defused. A skip reason is text a
    test author wrote, so it is never trusted as the closer's own words."""
    text = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(text))).strip()
    return re.sub(r"DONE:", "DONE-", text)[:cap]


def norm_reason(reason):
    """A skip reason with its changing parts collapsed (hex ids and numbers: a pid, a port, a timestamp), so a reason that
    differs per run only in those is the same ask and is not asked again every pass."""
    return re.sub(r"\b[0-9a-f]{7,}\b|\d+", "#", str(reason))


def needs_input_for_skips(uid, out, path=None):
    """The NEEDS_INPUT line for a unit whose done check exited 0 with skipped tests, writing the record once. '' when the
    output holds no skip count (the caller keeps its own refusal)."""
    n, _ = skipped(out)
    if not n:
        return ""
    recs = skip_records(out)
    what = ", ".join("%s (%s)" % (r["test"], shown(r["reason"]) or "no reason given") for r in recs) or \
        "not named (the done check printed no per test lines)"
    # the ask is the unit and its tests with normalized reasons; an unnamed skip falls back to the count
    ident = sorted((r["test"], norm_reason(r["reason"])) for r in recs) or n
    row = {"at": time.time(), "kind": "skip", "unit": uid, "skipped": n, "tests": recs,
           "key": ask_key("skip", uid, ident)}
    got = ask_once(row, path)
    note = {"asked": "", "already": " [already asked, waiting on the owner]"}.get(got, " [%s]" % got)
    # "PARTIAL: N test(s) skipped" is the words verdict() and merge_window.sh already use for this state
    return "%-5s NEEDS_INPUT: PARTIAL: %d test(s) skipped: %s%s" % (uid, n, what[:600], note)


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def newest_runs(runs):
    """{sub: run directory} of each sub unit's NEWEST run folder under `runs` (<sub>-HHMMSS), newest by the folder's
    modified time, the name only breaking a tie (the folder name wraps at midnight: diag_brief._dir_time). {} when absent."""
    try:
        names = os.listdir(runs)
    except FileNotFoundError:
        return {}
    last = {}
    for name in sorted(names, key=lambda n: (_mtime(os.path.join(runs, n)), n)):
        m = re.fullmatch(r"(.+)-\d{6}", name)
        if m and os.path.isdir(os.path.join(runs, name)):
            last[m.group(1)] = os.path.join(runs, name)
    return last


def needfact_asks(plan, only=None, ev=None, path=None):
    """Lines for every NEEDFACT sub unit (newest run EXHAUSTED or WITHHELD, not landed) whose newest run was already sent to
    the diagnostician (its DIAGNOSED marker) and still stands: the machine has asked what it can, so a person is asked, ONCE
    per run, with the diagnosis note's path. A newer run is a new ask. `only`: unit ids to look at (None: every open unit)."""
    ev = ev or evidence_dir()
    newest = newest_runs(os.path.join(ev, "unit-runs"))
    lines = []
    units = plan.get("units")
    for unit in units if isinstance(units, list) else []:   # a plan that is not the shape this reads asks nobody (the closer's own readers refuse it)
        if not isinstance(unit, dict) or not isinstance(unit.get("id"), str):
            continue
        uid = unit["id"]
        subs, evidence = unit.get("sub_units"), unit.get("evidence") or ""
        if (only and uid not in only) or unit.get("state") in ("DONE", "RETIRED") or not isinstance(subs, list) or not isinstance(evidence, str):
            continue
        for sub in subs:
            run = newest.get(sub) if isinstance(sub, str) else None
            if not run or plan_store.sub_landed(sub, evidence):
                continue
            try:
                with open(os.path.join(run, "STATUS"), encoding="utf-8", errors="replace") as fh:
                    status = (fh.read().split() or ["RUNNING"])[0]
            except OSError:
                status = "RUNNING"   # no STATUS yet: still running, not stuck
            if status not in ("EXHAUSTED", "WITHHELD") or not os.path.isfile(os.path.join(run, "DIAGNOSED")):
                continue
            note = os.path.join(ev, "diag-notes", uid + ".txt")
            row = {"at": time.time(), "kind": "needfact", "unit": uid, "sub": sub, "run": os.path.basename(run), "note": note,
                   "note_written": os.path.isfile(note), "key": ask_key("needfact", uid, sub, os.path.basename(run))}
            got = ask_once(row, path)
            if got == "already":
                continue
            # every name here comes from a plan or a folder, so none is printed raw (shown: no controls, no DONE: token)
            lines.append("%-5s NEEDS_INPUT: %s needs a fact the diagnostician could not prove (run %s, %s); diagnosis note %s%s%s"
                         % (shown(uid, 60), shown(sub, 80), shown(os.path.basename(run), 80), shown(status, 40), shown(note, 300),
                            "" if row["note_written"] else " (not written)", "" if got == "asked" else " [%s]" % got))
    return lines


def runs_unittest_main(path):
    """True when the script at `path` (relative to the tree) hands its command line to unittest.main and reads no command
    line of its own, so a trailing -v reaches unittest and raises the verbosity. False for anything unreadable or that
    parses arguments itself (argparse, sys.argv): it runs exactly as the plan wrote it."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(2000000)
    except OSError:
        return False
    return "unittest.main(" in text and "argparse" not in text and "sys.argv" not in text


def verbose_step(argv):
    """argv made verbose, so a skipped test is named with its reason (STAB1.f). Two shapes only, every other step (a selftest,
    a gate) may reject an unknown flag and runs exactly as the plan wrote it:
      python3 -m unittest <modules>      -> -v right after unittest
      python3 -m unittest discover ...   -> -v right after discover (a global -v there exits 2: unrecognized arguments)
      python3 <script>.py                -> a trailing -v, only when the script runs unittest.main and takes no arguments of its own"""
    argv = list(argv)
    if any(a in ("-v", "--verbose") for a in argv):
        return argv
    for i in range(len(argv) - 1):
        if argv[i] == "-m" and argv[i + 1] == "unittest":
            at = i + 3 if argv[i + 2:i + 3] == ["discover"] else i + 2
            return argv[:at] + ["-v"] + argv[at:]
    if "-m" not in argv and argv[-1].endswith(".py") and not argv[-1].startswith("-") and runs_unittest_main(argv[-1]):
        return argv + ["-v"]
    return argv


def evidence_line(unit_id, stamp, command, quoted, rev=None):
    """The closure paragraph. `rev` is the full HEAD sha the check ran over; the line names its first 12 characters."""
    at = " at %s" % rev[:12] if rev else ""
    return (" UNIT DONE %s: every sub unit landed, and the unit done_check was re-run after the last edit%s. "
            "`%s` printed: %s." % (stamp, at, command, quoted or "no result line"))


def head_revision():
    """The full sha of HEAD, the revision the next done check runs over (run_done_check copies HEAD plus the tree's
    uncommitted paths). None when git cannot answer: the caller closes nothing, NO-DATA is never a pass. KNOWN LIMIT: a
    by hand run over uncommitted edits is named by its HEAD; the lander's tree is clean by its own refusal."""
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", out) else None


def done_check_env(environ):
    """The environment a unit's done check runs under: the caller's, without git location variables and without the run's
    own choices (loop_switches.RUN_KNOBS: transport, model pins, scope, deadlines). 2026-10-02: a Claude only run's
    BROTHER_TRANSPORTS=claude made FX-11's done check (its selftests route a bridge model) refuse itself, so a unit with
    every sub unit landed could not close; every closer (loop_pass.sh each pass, land_batch's closing pass) routes here."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
    import loop_switches
    return {k: v for k, v in loop_switches.drop_run_knobs(dict(environ)).items() if not k.startswith("GIT_")}


def run_done_check(steps, env):
    """(exit code, output) of a unit's done check: every step run UNDER THE SANDBOX IN A DISPOSABLE COPY of the tree, never
    bare in the tree (review 15 finding 2, 2026-10-03: the closer ran each step with plain subprocess.run, cwd the landing
    tree, so a done check that is model drafted plan data ran unconfined over just landed code, every pass). The copy is
    land_batch.make_copy (a detached worktree of HEAD plus the tree's uncommitted paths, dropped in finally); the sandbox
    and the environment are the landing suites' own (grade_build.sandboxed, loop_switches.suite_env: a throwaway HOME and
    TMPDIR, no run knobs). && semantics: the first failure stops the chain and decides the verdict. No copy or no sandbox
    on this host is NO-DATA (exit 2, nothing ran), never a bare run and never a pass."""
    # land_batch alone: its boxed() is the sandbox, the throwaway HOME and the suite environment in one place, and its name
    # is unique (a staged candidate resolves a bare `import grade_build` to scripts/grade_build.py, an older module of the
    # same name, so that import is never made here)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
    import io
    import land_batch
    try:
        copy = land_batch.make_copy()
    except land_batch.TreeUnreadable as exc:
        return 2, "NO-DATA: no disposable copy of the tree for the done check (%s); nothing ran" % exc
    log = io.StringIO()
    code, out = 0, ""
    try:
        for i, argv in enumerate(steps):
            if i:
                out += "\n" + STEP_BREAK + "\n"   # even after a silent step, so its silence is its own segment
            try:
                r = land_batch.boxed(verbose_step(argv), log, 2400, env, cwd=copy)
            except (OSError, subprocess.SubprocessError) as exc:
                return 1, out + "%s could not run: %s" % (argv[0], exc)
            if r.returncode == 125:   # boxed's own answer for a host that cannot confine the run: nothing ran
                return 2, out + "NO-DATA: the done check cannot run confined on this host (%s); nothing ran" % (r.stderr or "").strip()
            out += (r.stdout or "") + (r.stderr or "")
            code = r.returncode
            if code:
                break
    finally:
        land_batch.drop_copy(copy)
    return code, out


def preflight_refusal(base):
    """'' when scripts/builder_preflight.sh passed over the lane from base, else why DONE is refused. ONE PROOF FOR DONE
    (A1, I10 and I13): a builder lane closes through this program, so the five lints it could have run before handing the
    work over run here first, bare in the lane (they read its git range and the deployed stamp), and the done check then
    runs boxed. A preflight that could not judge (exit 2) refuses too: NO-DATA is never a pass."""
    if not isinstance(base, str) or not re.fullmatch(r"[A-Za-z0-9_./^~][A-Za-z0-9_./^~-]{0,99}", base):
        return "the --lane-base %r is not a revision this tool will read" % (base,)
    # THE BASE'S PREFLIGHT, NEVER THE LANE'S (security review, 2026-10-19): the lane's copy is code the subject controls,
    # so it is read from the base commit with git show; its first row then refuses a lane that touches any program it runs.
    import tempfile
    shown = subprocess.run(["git", "show", "%s:scripts/builder_preflight.sh" % base], capture_output=True, text=True)
    if shown.returncode != 0 or not shown.stdout.strip():
        return ("the builder preflight refused (exit 2): NO-DATA: the base %s carries no scripts/builder_preflight.sh "
                "to judge the lane with" % base)
    box = tempfile.mkdtemp(prefix="lane-preflight-")
    try:
        script = os.path.join(box, "builder_preflight.sh")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(shown.stdout)
        r = subprocess.run(["sh", script, "--base", base], capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as exc:
        return "the builder preflight could not run (%s)" % exc
    finally:
        import shutil
        shutil.rmtree(box, ignore_errors=True)
    said = [l for l in ((r.stdout or "") + (r.stderr or "")).splitlines() if l.strip()]
    print("\n".join(said))
    if r.returncode == 0:
        return ""
    return "the builder preflight refused (exit %d): %s" % (r.returncode, (said or ["no output"])[-1][:200])


def harness_drift(unit):
    """'' when every frozen harness the unit pins (harness_sha256: {path: sha256}) still has its pinned digest, else
    the reason the unit must not close. Readiness finding c (2026-10-29): the pins were read by nothing at close, so a
    harness edited to pass went green silently, which defeats the whole red first rule. A pin that is not a map is
    unreadable and refuses; a pinned file that is missing refuses; NO-DATA is never a pass."""
    import hashlib
    pins = unit.get("harness_sha256") if isinstance(unit, dict) else None
    if pins is None:
        return ""
    if not isinstance(pins, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in pins.items()):
        return "its harness_sha256 pin is unreadable (not a map of path to digest)"
    for path, want in sorted(pins.items()):
        try:
            with open(path, "rb") as fh:
                got = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            return "its frozen harness %s is missing, so the pin cannot be checked" % path
        if got != want:
            return "its frozen harness %s has drifted from its pin (HARN1): the harness, not the code, was changed" % path
    return ""


def unlisted_spec_subs(unit):
    """Sub unit ids the unit's own spec defines (`## <id>.N` headings) that its plan row neither lists in sub_units
    nor names anywhere else (a removal is recorded in the row's text, as L0's evidence records L0.3 and L0.4).
    2026-10-03: ACC2.5 (R-DIET-7) was spec'd, never registered, and ACC2 closed DONE without it. A spec that cannot be
    read answers [] here; the plan lint's own SPEC-FILE rule owns that case."""
    spec, uid = unit.get("spec"), unit.get("id")
    if not isinstance(spec, str) or not spec or not isinstance(uid, str):
        return []
    try:
        with open(spec, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return []
    # the WHOLE id, hyphens included (2026-10-04: "#### RECON.dirty-worktree" was read as RECON.dirty, so a fully listed
    # unit was refused for sub units nobody defined)
    heads = re.findall(r"^#{2,4} (%s[.-][A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)(?=[\s:),]|\.(?:\s|$)|$)" % re.escape(uid), text, flags=re.M)
    listed = set(unit.get("sub_units") or [])
    # a removal counts only when a sentence naming the id SAYS it was removed; `remains` lists what is still owed, so a
    # mention there is an open obligation, never a record (ACC2's remains: "append ACC2.5 ... to sub_units")
    told = " ".join(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                    for k, v in unit.items() if k != "remains")
    gone = re.compile(r"\b(removed|superseded|dropped|retired|withdrawn)\b", re.I)

    def recorded(h):
        for m in re.finditer(r"(?<![\w.-])%s(?![\w-]|\.\w)" % re.escape(h), told):
            end = told.find(". ", m.end())
            if gone.search(told[m.start(): end if end != -1 else len(told)]):
                return True
        return False
    return sorted({h for h in heads if h not in listed and not recorded(h)})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("units", nargs="*")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--needfact", action="store_true",
                    help="only ask the owner about NEEDFACT sub units the diagnostician already saw (once per run), "
                         "closing nothing: the lander's closing pass runs this every pass (STAB1.f)")
    ap.add_argument("--lane-base", default=None, metavar="REV",
                    help="a builder lane's base: scripts/builder_preflight.sh runs its five lints over REV..the tree "
                         "before any done check, and a refusal keeps every named unit open (A1, I13)")
    if argv is None:
        raw_argv = sys.argv[1:]
    elif isinstance(argv, (list, tuple)) and all(isinstance(a, str) for a in argv):
        raw_argv = list(argv)
    else:
        raise ValueError("main: argv must be None or a list/tuple of str, got %s" % type(argv).__name__)
    try:
        args = ap.parse_args(raw_argv)
    except SystemExit as exc:
        # argparse stops the process on --help and on a bad argument by raising SystemExit. Its code is RETURNED
        # here, so a caller that calls main() directly never meets a raw SystemExit, and the process exit code
        # is unchanged because the entry point is sys.exit(main()).
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 2)
    try:
        with open(PLAN, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, ValueError) as exc:
        print("NO-DATA: the plan is unreadable (%s)" % exc)
        return 2
    # STAB1.f: a NEEDFACT sub unit the diagnostician already saw escalates to the owner once per run (a dry run asks nobody).
    # --needfact is the sweep alone: land_batch.closing_pass names only fully landed units and lists with --dry, so a sweep
    # that ran only inside a closing run never fired on the loop's path.
    if not args.dry:
        try:
            for line in needfact_asks(plan, set(args.units) or None):
                print(line)
        except OSError as exc:
            print("NO-DATA: the NEEDFACT sweep could not read the run folders (%s)" % exc)
    if args.needfact:
        return 0
    wanted = args.units or closable(plan, landed_subs(plan))
    if not wanted:
        print("nothing to close: no unit has every sub unit landed with a done_check waiting")
        return 1
    env = done_check_env(os.environ)
    if args.lane_base is not None and not args.dry:
        refused = preflight_refusal(args.lane_base)
        if refused:
            for uid in wanted:
                print("%-5s NOT CLOSED: %s" % (uid, refused))
            print("CLOSED  0 unit(s): none")
            return 1
    closed = []
    for uid in wanted:
        unit = next((u for u in plan["units"] if u["id"] == uid), None)
        if unit is None:
            print("%-5s not in the plan" % uid)
            continue
        if uid not in closable(plan, landed_subs(plan)):
            print("%-5s not closable: a sub unit is unlanded, it is already DONE or RETIRED, or it has no done_check" % uid)
            continue
        refused = facts_refusal(unit)   # pure and cheap: a disagreement never runs the done check
        if refused:
            print("%-5s NOT CLOSED: %s" % (uid, refused))
            continue
        drift = harness_drift(unit)
        if drift:
            print("%-5s NOT CLOSED: %s" % (uid, drift))
            continue
        forgot = unlisted_spec_subs(unit)
        if forgot:
            print("%-5s NOT CLOSED: its spec defines sub unit(s) the plan neither lists nor records as removed: %s"
                  % (uid, ", ".join(forgot)))
            continue
        cmd = unit["done_check"].strip()
        steps = screen_done_check(cmd)
        if steps is None:
            print("%-5s NOT CLOSED: its done_check is not a shape this tool will execute: %s" % (uid, cmd[:90]))
            continue
        # flushed BEFORE the check starts: a closer killed at its timeout keeps this line, which is how land_batch tells a
        # check that started from one that waited in the queue (LOOP1.e)
        print("%-5s running its done_check: %s" % (uid, cmd[:90]), flush=True)
        if args.dry:
            continue
        rev = head_revision()   # before the check starts: the revision it proves is the one it ran over
        code, out = run_done_check(steps, env)
        ok, quoted = verdict(code, out)
        r = type("R", (), {"returncode": code})
        if not ok:
            # STAB1.f: a green run that skipped tests is a question for the owner, asked once, never a red line every pass
            asked = needs_input_for_skips(uid, out) if code == 0 and quoted.startswith("PARTIAL:") else ""
            if asked:
                print(asked)
                continue
            print("%-5s NOT CLOSED: the done_check exited %d: %s" % (uid, r.returncode, quoted[:120]))
            continue
        if rev is None:   # after the check, so a tree the check itself could not copy keeps its own NO-DATA answer above
            print("%-5s NOT CLOSED: NO-DATA: git could not name HEAD, so the revision the done_check proves is unknown" % uid)
            continue
        stamp =datetime.datetime.now().strftime("%Y-%m-%d %H:%M %Z") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        # THE REASON, not only the mechanism: the done_check verbatim and the exit code the PROCESS really
        # returned, so a reader can re-run the exact command that justified this DONE instead of trusting it.
        because = "%s exit %d" % (cmd, r.returncode)
        closed.append((uid, unit.get("state"), evidence_line(uid, stamp, cmd, quoted, rev), because, quoted, cmd, rev))
    # GIT CONFIRMS EVERY LANDING AFTER THE LAST DONE CHECK, from the plan as it is now, immediately before the locked
    # write: each check can run for forty minutes and the history can move meanwhile (a revert can land), so the answer
    # read here, not one read before some other unit's check, is what a DONE rests on (adversarial review, 2026-10-31).
    # The lock then closes a unit only while its landing facts still equal the ones confirmed here (landing_key).
    confirmed, legacies = {}, set()
    if closed:
        try:
            now_plan = plan_store.load(PLAN)
        except (OSError, ValueError) as exc:
            print("NO-DATA: the plan could not be read again after the done checks (%s); nothing closed" % exc)
            return 2
        kept = []
        for row in closed:
            unit = next((u for u in now_plan.get("units", []) if isinstance(u, dict) and u.get("id") == row[0]), None)
            refused = "it is no longer in the plan" if unit is None else landing_refusal(unit)
            if refused:
                print("%-5s NOT CLOSED: after its done_check passed, %s" % (row[0], refused))
                continue
            confirmed[row[0]] = landing_key(unit)
            if legacy(unit):
                legacies.add(row[0])
            kept.append(row)
        closed = kept
    # BY UNIT, UNDER A LOCK, FROM THE FRESH RECORD (finding 1, 2026-09-27). The plan copy above was read before a
    # done_check that can run for forty minutes; evidence built from it erased a receipt another writer committed
    # meanwhile. Each close is now a function applied to the unit RE-READ under the lock, and a unit that is already
    # DONE there, or no longer has every sub unit landed, is left exactly as the other writer left it.
    # THE done_check FIELD IS THE CHECK (reproduced 2026-10-30): a unit whose done_check changed while the old one ran
    # is left open too. The command that passed is not the command the plan names now, so a DONE written over it
    # would rest on a check nobody can re-run from the record; `moved` names those units so the log says why.
    done, gone, moved = [], [], []

    def close(uid, line, because, ran, rev):
        def apply(unit):
            if (unit.get("state") == "DONE" or uid not in closable({"units": [unit]}, landed_subs({"units": [unit]}))
                    or landing_key(unit) != confirmed.get(uid) or legacy(unit) != (uid in legacies)):
                gone.append(uid)
                return unit
            now = unit.get("done_check")
            if not isinstance(now, str) or now.strip() != ran:
                moved.append(uid)
                return unit
            if harness_drift(unit):   # re-read under the lock: a harness edited while the check ran is a drift too
                moved.append(uid)
                return unit
            unit["state"] = "DONE"
            unit["state_at"] = datetime.datetime.now().strftime("%Y-%m-%d")
            unit["evidence"] = (unit.get("evidence") or "").rstrip() + line
            if not legacy(unit):   # a legacy unit stays legacy: its key is absent, and adding it would change how it reads (P2b)
                events = unit["landing_events"]
                top = max([e["sequence"] for e in events if isinstance(e, dict) and type(e.get("sequence")) is int] or [0])
                events.extend(plan_store.landing_event(uid, sub, rev, line, top + 1 + i, kind="closed")
                              for i, sub in enumerate(unit.get("sub_units") or []))
            done.append(uid)
            return provenance.apply(unit, "close_unit.py", because=because)
        return apply
    if closed:
        plan_store.update_units(PLAN, {uid: close(uid, line, because, ran, rev) for uid, _, line, because, _, ran, rev in closed})
    for uid, was, _, because, quoted, _ran, _rev in closed:
        if uid in gone:
            print("%-5s NOT CLOSED: re-read under the lock, it is already DONE or a sub unit is no longer landed" % uid)
            continue
        if uid in moved:
            print("%-5s NOT CLOSED: its done_check changed while the check ran, so the check that passed is not the "
                  "check the plan names now; run the closer again" % uid)
            continue
        # The board is current state and the next writer overwrites it, so the TRANSITION also goes to the
        # append only journal, after the write it describes. Its verdict is deliberately not checked: journal()
        # never raises, and a False (a full disk, a read only home) must never stop a unit closing.
        provenance.journal(dict(provenance.stamp("close_unit.py", because=because),
                                unit=uid, was=was, now="DONE", plan=PLAN, quoted=quoted))
        if uid in legacies:   # P2b's condition: every legacy close says so, a named gap and never a silent one
            print("LEGACY %s: no landing events" % uid)
        print("%-5s DONE: %s" % (uid, quoted[:120]))
    print("CLOSED  %d unit(s): %s" % (len(done), ", ".join(done) or "none"))
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
