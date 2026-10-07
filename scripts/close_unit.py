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
import json
import os
import re
import shlex
import subprocess
import sys

# This program is the reason provenance exists: it sets DONE, stamps state_at and appends a closure paragraph,
# unattended, on every loop pass, and recorded no author at all. A HARD import on purpose: provenance.py ships
# beside this file, so a missing one means a broken checkout, and closing units silently unattributed is exactly
# the state that was measured on 2026-09-21. The SOFT failure path is inside journal(), which returns False on a
# bad disk rather than stopping a closure, because a provenance gap must never block the work it describes.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import provenance  # noqa: E402  (sibling module, scripts/loop/provenance.py)
import plan_store  # noqa: E402  (the one plan writer, and the one landed test: plan_store.sub_landed)

PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"



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


def verdict(returncode, output):
    """(ok, quoted). ok ONLY when the process exited zero: the exit code decides, never the printed words.
    quoted is the check's own result lines, which is what goes into the evidence."""
    # "selftest: N cases, OK" and "PASS: ..." are this estate's own result shapes (most done checks are --selftest);
    # quoting only the unittest shapes left "no result line" on every unit closed by a selftest (six rows, 2026-09-24)
    # FX-15.6. A NO-DATA reason is the one thing the owner can act on when the closer refuses with exit 2, so it
    # must reach the log even when the check printed result lines before or after it and the four line window
    # slid past. Keeping only the NO-DATA lines, when any are present, is additive: the exit code still decides
    # ok, only the quoted reason widens.
    lines = [l.strip() for l in (output or "").splitlines()
             if l.startswith(("Ran ", "OK", "FAILED", "ERROR", "selftest:", "PASS:", "FAIL:", "NO-DATA:", "GREEN:", "RED:")) or l.strip() in ("OK", "PASS", "FAIL")]
    nodata = [l for l in lines if l.startswith("NO-DATA:")]
    if nodata:
        lines = nodata
    # A FAILED check whose output carries none of the shapes above still said why: its last non empty line is the
    # reason (U8's "NOT DONE    4 of 5 catalog(s)...", a traceback's exception line). Quoting nothing there left the
    # loop report reading "exited 1:" and nothing else (2026-10-03). A pass keeps "no result line": unchanged.
    if not lines and returncode != 0:
        lines = [l.strip() for l in (output or "").splitlines() if l.strip()][-1:]
    return returncode == 0, " ".join(lines[-4:])[:400]


def evidence_line(unit_id, stamp, command, quoted):
    return (" UNIT DONE %s: every sub unit landed, and the unit done_check was re-run after the last edit. "
            "`%s` printed: %s." % (stamp, command, quoted or "no result line"))


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
        for argv in steps:
            try:
                r = land_batch.boxed(list(argv), log, 2400, env, cwd=copy)
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
    wanted = args.units or closable(plan, landed_subs(plan))
    if not wanted:
        print("nothing to close: no unit has every sub unit landed with a done_check waiting")
        return 1
    env = done_check_env(os.environ)
    closed = []
    for uid in wanted:
        unit = next((u for u in plan["units"] if u["id"] == uid), None)
        if unit is None:
            print("%-5s not in the plan" % uid)
            continue
        if uid not in closable(plan, landed_subs(plan)):
            print("%-5s not closable: a sub unit is unlanded, it is already DONE or RETIRED, or it has no done_check" % uid)
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
        print("%-5s running its done_check: %s" % (uid, cmd[:90]))
        if args.dry:
            continue
        code, out = run_done_check(steps, env)
        ok, quoted = verdict(code, out)
        r = type("R", (), {"returncode": code})
        if not ok:
            print("%-5s NOT CLOSED: the done_check exited %d: %s" % (uid, r.returncode, quoted[:120]))
            continue
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M %Z") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        # THE REASON, not only the mechanism: the done_check verbatim and the exit code the PROCESS really
        # returned, so a reader can re-run the exact command that justified this DONE instead of trusting it.
        because = "%s exit %d" % (cmd, r.returncode)
        closed.append((uid, unit.get("state"), evidence_line(uid, stamp, cmd, quoted), because, quoted))
    # BY UNIT, UNDER A LOCK, FROM THE FRESH RECORD (finding 1, 2026-09-27). The plan copy above was read before a
    # done_check that can run for forty minutes; evidence built from it erased a receipt another writer committed
    # meanwhile. Each close is now a function applied to the unit RE-READ under the lock, and a unit that is already
    # DONE there, or no longer has every sub unit landed, is left exactly as the other writer left it.
    done, gone = [], []

    def close(uid, line, because):
        def apply(unit):
            if unit.get("state") == "DONE" or uid not in closable({"units": [unit]}, landed_subs({"units": [unit]})):
                gone.append(uid)
                return unit
            unit["state"] = "DONE"
            unit["state_at"] = datetime.datetime.now().strftime("%Y-%m-%d")
            unit["evidence"] = (unit.get("evidence") or "").rstrip() + line
            done.append(uid)
            return provenance.apply(unit, "close_unit.py", because=because)
        return apply
    if closed:
        plan_store.update_units(PLAN, {uid: close(uid, line, because) for uid, _, line, because, _ in closed})
    for uid, was, _, because, quoted in closed:
        if uid in gone:
            print("%-5s NOT CLOSED: re-read under the lock, it is already DONE or a sub unit is no longer landed" % uid)
            continue
        # The board is current state and the next writer overwrites it, so the TRANSITION also goes to the
        # append only journal, after the write it describes. Its verdict is deliberately not checked: journal()
        # never raises, and a False (a full disk, a read only home) must never stop a unit closing.
        provenance.journal(dict(provenance.stamp("close_unit.py", because=because),
                                unit=uid, was=was, now="DONE", plan=PLAN, quoted=quoted))
        print("%-5s DONE: %s" % (uid, quoted[:120]))
    print("CLOSED  %d unit(s): %s" % (len(done), ", ".join(done) or "none"))
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
