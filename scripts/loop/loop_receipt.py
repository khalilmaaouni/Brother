#!/usr/bin/env python3
"""Writes brother.loop's own receipt (AGENTS.md "The receipt contract", audit issue F23).

MEASURED 2026-09-26: 0 of 15 loop run directories under ~/.claude/evidence/loop-runs held
receipt/receipt.json. They held journal.jsonl, gates.tsv and dream/, but never the one file the
contract names: "a run that changes anything owes a receipt ... every run that reaches the end
writes receipt/receipt.json inside its own run directory". This is the writer scripts/loop/
loop_until.sh's raise() calls once, at every terminal state, right after it writes its own
"RUN END" line.

THE CONTRACT'S OWN WORDS GOVERN THE SHAPE HERE. A field this tool cannot answer is a string
starting "NO-DATA: <why>", never a missing key and never a silent zero: a truly unspent budget and
an unreadable spend are opposite facts, and the AGENTS.md law that a stopped project has already
paid for elsewhere ("Zero landings and an unreadable landing ledger are opposite facts") is the
same rule applied to money, a log digest and a git revision.

WRITTEN ATOMICALLY: a temp file inside receipt/ then os.replace, so a reader never sees a half
written receipt. If the receipt directory cannot be made or the rename fails (a full disk, a
read-only run directory), write_receipt returns the reason instead of raising: the caller decides
how loud to be, and loop_until.sh's raise() prints it on the log and in the alarm rather than
letting the run end silently unaccounted for.

PER FILE CHECKS (2026-09-30): receipt["scope"]["changed"] names every file each landing committed with the exact checks that
ran before that commit and their exit codes, in receipt_door.per_file_checks' own keys, and receipt["evidence"] carries the
checks in full; a landing whose row records no checks or no files reads no-data, never verified.

LANDINGS ARE READ FROM THE LANDING RECORD, NEVER FROM GIT (U10, Codex check-in 3 #3): git history
in the window also holds closure commits and commits fast forwarded in from elsewhere (a mock with one
landing, one closure and one foreign commit counted 3). land_batch appends one loop-landing-v1 row
per landing commit to proof/landings.jsonl and retains the observed remote history beside it; proof-end
records their digests and the count is rederived from those bytes by proof_accept.surviving_landings.

CLAUDE SPEND IS TALLIED AFTER THE FINAL PASS (U9, B5-19): in a proof phase from the pair Claude ledger's
bytes retained at proof-end, by this run's tag, a call still pending at the end being NO-DATA; outside
one from the live ledger over the window, at write time. The driver's own figure is never used.

THE END SEQUENCE (DESIGN-FINAL section 1, S6 to S10): proof-ending (the ending marker, under both ledger
locks), proof-settle (waits for this run's open calls, names leftovers), the runners are stopped,
clock, proof-end (the end snapshots and the end marker), write.

usage: loop_receipt.py write --run-dir DIR --pid N --start ISO --end ISO --state STATE
                              --reason TEXT --deadline HHMM --budget USD
                              [--spent-before N] [--spent-after N]
                              --log-path PATH [--cwd DIR]
       prints the written path on success, exit 0.
       prints "RECEIPT FAILED: <why>" on failure to write, exit 1.
       loop_receipt.py proof-ending --run-dir DIR --state STATE      exit 0, 2 when not recorded
       loop_receipt.py proof-settle --run-dir DIR --max-seconds N    SETTLED exit 0, leftovers exit 2

Test: python3 scripts/loop/test_loop_receipt.py
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

# The loop's own copies of proof_ledger, proof_accept, freeze_manifest and grade_build, never a
# same-named file elsewhere on the path (scripts/ carries differing copies): the deploy parity
# check refuses an import that does not say which copy it means (F47).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _num(text):
    """A float from a string the caller says is a number, or None for empty/unreadable. The
    caller, not this helper, decides why a None is NO-DATA."""
    if text is None or text == "":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def openrouter_spend(spent_before, spent_after):
    """(value, note). note is None exactly when value is a real number."""
    b, a = _num(spent_before), _num(spent_after)
    if b is None or a is None:
        return None, "NO-DATA: the OpenRouter spend before and after this run was not both readable at the end"
    return round(a - b, 4), None


def log_sha256(path):
    if not path or not os.path.isfile(path):
        return None, "NO-DATA: the driver log %s could not be read" % path
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError as exc:
        return None, "NO-DATA: the driver log could not be read (%s: %s)" % (type(exc).__name__, exc)
    return h.hexdigest(), None


def _git(cwd, args, timeout):
    try:
        return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "NO-DATA: git %s failed in %s (%s: %s)" % (" ".join(args), cwd, type(exc).__name__, exc)


def runtime_revision(cwd):
    r = _git(cwd, ["rev-parse", "HEAD"], timeout=10)
    if isinstance(r, tuple):
        return r
    if r.returncode != 0 or not r.stdout.strip():
        return None, "NO-DATA: git rev-parse HEAD exited %d in %s" % (r.returncode, cwd)
    return r.stdout.strip(), None


def _no_data(note):
    return note if note.startswith("NO-DATA") else "NO-DATA: calls still pending at the end: " + note


def receipt_claude(a, evidence, proof):
    """The receipt's Claude figure, never the driver's (U9). A proof run: the retained end bytes by run tag. Otherwise:
    the live ledger over [start, end], read now, after the final pass."""
    if proof:
        try:
            usd, note = claude_end_spend(a.run_dir, evidence if isinstance(evidence, dict) else {}, a.end)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return "NO-DATA: the Claude end snapshot could not be rederived (%s: %s)" % (type(exc).__name__, exc)
        return usd if note is None else note
    import claude_ledger
    t = claude_ledger.tally(claude_ledger.ledger_path(), since=a.start, until=a.end)
    note = claude_ledger.note(t)
    return t["usd"] if not note else _no_data(note)


def receipt_landings(run_dir, evidence, start, end):
    """The receipt's landing count from the retained landing record (U10); any landing of unknown survival is NO-DATA."""
    try:
        n, unknown, detail = landings_end(run_dir, evidence if isinstance(evidence, dict) else {}, start, end)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return "NO-DATA: the landing record could not be rederived (%s: %s)" % (type(exc).__name__, exc)
    if unknown:
        return "NO-DATA: %d landing(s) of unknown survival, no observed remote or no retained history: %s" % (
            unknown, ", ".join(c[:12] for c in detail["unknown"]))
    return n


# THE PER FILE SHAPE (2026-09-30, AGENTS.md "The receipt contract": every changed file with the exact check command, its exit
# code and where its output lives). The keys are the ones scripts/receipt_door.per_file_checks writes, so
# receipt_door.require_per_file_checks(receipt["scope"]["changed"]) reads a loop receipt the way it reads any other.
LANDING_SCHEMA = "loop-landing-v1"
NODATA = "NO-DATA"
ROW_ERRORS = (OSError, ValueError, KeyError, TypeError, RecursionError)   # receipt_landings' tuple, plus a line nested too deep to parse


def read_landing_lines(run_dir):
    """(lines, note): the non empty lines of proof/landings.jsonl, UNPARSED, so landing_receipts parses each inside its own
    guard and one corrupt line costs that line alone. note is None, or NO-DATA when the file exists and cannot be read. No
    file means no landings and no note: the count in receipt["landings"] already says what a missing record means."""
    path = os.path.join(run_dir, "proof", "landings.jsonl")
    if not os.path.lexists(path):
        return [], None
    try:
        with open(path, encoding="utf-8") as fh:
            return [line for line in fh.read().splitlines() if line.strip()], None
    except (OSError, ValueError) as exc:
        return [], "NO-DATA: the landing record could not be read for per file checks (%s: %s)" % (type(exc).__name__, exc)


def _landing_row(row):
    """(row, unit): a landing row as a dict and its key <sub>@<commit first 12> (subs joined by +), so the same sub landing
    twice stays two entries. Raises ValueError on anything that is not a loop-landing-v1 row."""
    if isinstance(row, str):
        row = json.loads(row)
    if not isinstance(row, dict) or row.get("schema") != LANDING_SCHEMA:
        raise ValueError("not a %s row" % LANDING_SCHEMA)
    commit, subs = row.get("commit"), row.get("subs")
    if not isinstance(commit, str) or not commit:
        raise ValueError("the row names no commit")
    if not isinstance(subs, list) or not all(isinstance(x, str) and x for x in subs):
        raise ValueError("the row's subs are not a list of names")
    return row, "%s@%s" % ("+".join(subs) or "NO-SUB", commit[:12])


def _landing_check(row):
    """(check_command, exit_code, state, reason) for one landing. The checks it ran before its commit, joined by ' && ' in the
    order they ran, and the first non zero exit code (0 when all were zero), which is what that command line would return.
    A row that records no checks, or a check with no command or no integer exit code, is NO-DATA with no command and no exit
    code, never verified: an unknown check is not a pass. Checks that ran and exited 0 are still "no-data", never "verified":
    the loop measures no check_passed_before (a landing has no untouched-tree run), and receipt_door only calls a check
    verified once it was seen to fail before the work, so this follows the engine's rule and the reason says why."""
    checks = row.get("checks")
    if not isinstance(checks, list) or not checks:
        return "", None, "no-data", "NO-DATA: this landing's row records no checks"
    commands, code = [], 0
    for c in checks:
        cmd = c.get("command") if isinstance(c, dict) else None
        rc = c.get("exit_code") if isinstance(c, dict) else None
        if not isinstance(cmd, str) or not cmd.strip() or isinstance(rc, bool) or not isinstance(rc, int):
            return "", None, "no-data", "NO-DATA: a check on this landing has no command or no integer exit code"
        commands.append(cmd.strip())
        code = code or rc
    if code:
        return " && ".join(commands), code, "no-data", "a check on this landing exited %d" % code
    return (" && ".join(commands), 0, "no-data",
            "every check ran before the landing commit and exited 0, but check_passed_before is NO-DATA (a loop landing has no "
            "untouched tree run), so, as receipt_door rules, a check never seen to fail is not verified")


def landing_receipts(rows):
    """The receipt's scope.changed: one per file entry per landing, in receipt_door.per_file_checks' own keys (file, unit,
    check_command, exit_code, output_location, check_passed_before, state, reason). rows are landing rows as dicts or as the
    raw lines read_landing_lines returns. A landing that recorded no files gives ONE no-data entry naming its unit, with no
    check command so require_per_file_checks refuses it; an unreadable or corrupt row becomes one NO-DATA string. Never raises."""
    out = []
    for i, raw in enumerate(rows):
        try:
            row, unit = _landing_row(raw)
            command, code, state, reason = _landing_check(row)
            where = row.get("log") if isinstance(row.get("log"), str) and row.get("log") else NODATA
            files = row.get("files")
            entry = {"unit": unit, "check_command": command, "exit_code": code, "output_location": where,
                     "check_passed_before": NODATA, "state": state, "reason": reason}
            if not isinstance(files, list) or not files or not all(isinstance(f, str) and f for f in files):
                out.append(dict(entry, file="NO-DATA: no files recorded for %s" % unit, check_command="", exit_code=None,
                                state="no-data", reason="NO-DATA: this landing's row records no files"))
            else:
                out.extend(dict(entry, file=f) for f in files)
        except ROW_ERRORS as exc:
            out.append("NO-DATA: landing row %d cannot be read (%s: %s)" % (i + 1, type(exc).__name__, str(exc)[:80]))
    return out


def landing_evidence(rows):
    """The receipt's evidence: one item per landing, naming its commit, verdict, every check with its command and exit code,
    the build's declared done_check per sub, and where the full output lives. A row that cannot be read is one NO-DATA string."""
    out = []
    for i, raw in enumerate(rows):
        try:
            row, unit = _landing_row(raw)
            checks, done = row.get("checks"), row.get("done_check")
            out.append({"unit": unit, "commit": row["commit"], "verdict": row.get("verdict"),
                        "checks": checks if isinstance(checks, list) and checks else "NO-DATA: this landing's row records no checks",
                        "done_check": done if isinstance(done, dict) else "NO-DATA: the build done_check was not recorded",
                        "output_location": row.get("log") if isinstance(row.get("log"), str) and row.get("log") else NODATA})
            if isinstance(row.get("parity"), str) and row["parity"]:
                out[-1]["parity"] = row["parity"]   # the UNVERIFIED row's own note: why no remote was observed
        except ROW_ERRORS as exc:
            out.append("NO-DATA: landing row %d cannot be read (%s: %s)" % (i + 1, type(exc).__name__, str(exc)[:80]))
    return out


def build_receipt(a):
    receipt = {
        "run_id": os.path.basename(a.run_dir.rstrip("/")),
        "driver_pid": a.pid,
        "start": a.start,
        "end": a.end,
        "end_state": a.state,
        "end_reason": a.reason,
        "deadline": a.deadline,
        "driver_log_path": a.log_path,
    }
    start_record = {}
    try:
        start_record = proof_read(os.path.join(a.run_dir, 'proof', 'start.json'))
        receipt['proof_attempt_id'] = start_record['attempt_id']
    except (OSError, ValueError, TypeError, KeyError) as exc:
        receipt['proof_attempt_id'] = 'NO-DATA: start identity unavailable: ' + str(exc)
    budget = _num(a.budget)
    receipt["budget_usd"] = budget if budget is not None else "NO-DATA: the budget figure %r was not numeric" % a.budget

    spend, note = openrouter_spend(a.spent_before, a.spent_after)
    receipt["openrouter_spend_usd"] = spend if note is None else note
    proof = start_record.get('phase') in ('RB', 'RC') or bool(os.environ.get('BROTHER_PROOF_PHASE'))
    try:
        observed = proof_read(os.path.join(a.run_dir, 'proof', 'evidence.json'))
    except (OSError, ValueError, TypeError) as exc:
        observed = 'NO-DATA: end evidence unavailable: %s' % exc
    if proof:
        try:
            if not isinstance(observed, dict):
                raise ValueError(observed)
            accounting = proof_end_accounting(a.run_dir, observed)
            if (observed['end'] != a.end or observed['run_id'] != receipt['run_id']
                    or observed['attempt_id'] != start_record['attempt_id']
                    or accounting['schema'] != 'loop-proof-accounting-v1'
                    or accounting['run_id'] != receipt['run_id']
                    or accounting['attempt_id'] != start_record['attempt_id']
                    or accounting['baseline_sha256'] != start_record['ledger_baseline_sha256']):
                raise ValueError('accounting end identity mismatch')
            import proof_ledger
            cost = proof_ledger.amount(accounting['measured_spend_usd'])
            unknown = proof_ledger.amount(accounting['run_unknown_cost_calls'])
            if unknown != 0:
                raise ValueError('this run has unresolved provider cost')
            receipt['openrouter_spend_usd'] = cost
            receipt['proof_baseline_sha256'] = accounting['baseline_sha256']
        except (OSError, ValueError, TypeError, KeyError) as exc:
            receipt['openrouter_spend_usd'] = 'NO-DATA: proof accounting: ' + str(exc)

    receipt["claude_spend_usd"] = receipt_claude(a, observed, proof)
    receipt["landings"] = receipt_landings(a.run_dir, observed, a.start, a.end)
    lines, lnote = read_landing_lines(a.run_dir)
    changed, evidence = landing_receipts(lines), landing_evidence(lines)
    receipt["scope"] = {"changed": changed + ([lnote] if lnote else [])}
    receipt["evidence"] = evidence + ([lnote] if lnote else [])

    digest, dnote = log_sha256(a.log_path)
    receipt["driver_log_sha256"] = digest if dnote is None else dnote

    rev, rnote = runtime_revision(a.cwd)
    receipt["runtime_revision"] = rev if rnote is None else rnote
    return receipt


def write_receipt(receipt, run_dir):
    """(path, None) on success, (None, reason) on failure. Never raises: an unwritable run
    directory is a fact to report, not a crash."""
    d = os.path.join(run_dir, "receipt")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as exc:
        return None, "the receipt directory %s could not be created (%s: %s)" % (d, type(exc).__name__, exc)
    path = os.path.join(d, "receipt.json")
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".receipt-", suffix=".tmp", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:  # sbe: allow-silent best effort cleanup of the failed write's own temp file; the real failure is already captured and returned two lines below
                pass
        return None, "the receipt could not be written to %s (%s: %s)" % (path, type(exc).__name__, exc)
    return path, None


# Included in loop_receipt.py by the allocated proof-recording unit.
def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def proof_json(path, value):
    from pathlib import Path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.proof-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(value, fh, indent=2, sort_keys=True, allow_nan=False)
            fh.write('\n'); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def proof_read(path):
    with open(path, encoding='utf-8') as fh:
        from proof_accept import unique_object
        value = json.load(fh, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError('proof record is not an object')
    return value


def freeze_boundary(manifest, run_id, phase):
    from pathlib import Path
    row = dict(schema='loop-freeze-check-v1', run_id=run_id, phase=phase,
               observed_at=utc_now(), verdict='NO-DATA', checked_files=0,
               manifest_sha256='NO-DATA: manifest not configured')
    try:
        if not manifest:
            raise ValueError('BROTHER_FREEZE_MANIFEST is not set')
        import freeze_manifest
        raw = Path(manifest).read_bytes()
        value = json.loads(raw)
        code, findings = freeze_manifest.verify(value)
        if os.environ.get('BROTHER_PROOF_PHASE') in ('RB', 'RC'):
            # U3 item 6: the code a proof run executes must be inside what this manifest froze
            runtime, root = value.get('runtime'), os.environ.get('BROTHER_CODE_ROOT', '')
            real = os.path.realpath(runtime) if isinstance(runtime, str) and runtime else None
            if not (real and root and os.path.commonpath([real, os.path.realpath(root)]) == real):
                code = 1
                findings = list(findings) + ['FAIL code root outside frozen runtime: %r not under %r' % (root, runtime)]
        row.update(verdict=('PASS','FAIL','NO-DATA')[code], findings=findings,
                   checked_files=len(value.get('files',{})),
                   manifest_sha256=hashlib.sha256(raw).hexdigest(), observed_at=utc_now())
    except (OSError, ValueError, TypeError, ImportError) as exc:
        row['findings'] = ['NO-DATA: ' + str(exc)]
    return row


def sandbox_observation(run_dir, attempt_id=None):
    """Exercise the same wrapper as grading, with scratch-only effects.

    One allowed write must succeed. An outside write and loopback bind must
    be denied by permission, not merely fail for an unrelated reason.
    """
    from pathlib import Path
    import shutil
    if sys.platform != 'darwin' or not shutil.which('sandbox-exec'):
        return 'NO-DATA: sandbox-exec unavailable'
    try:
        import grade_build
        proof = Path(run_dir) / 'proof'
        proof.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='sandbox-observe-', dir=str(proof)) as scratch:
            root = Path(scratch) / 'allowed'; root.mkdir()
            outside = Path(scratch) / 'outside'
            code = '''import errno,json,pathlib,socket,sys
root=pathlib.Path(sys.argv[1]);outside=pathlib.Path(sys.argv[2])
(root/'control').write_text('control')
blocked=0
try: outside.write_text('escape')
except OSError as exc:
 if exc.errno in (errno.EPERM,errno.EACCES):blocked+=1
sock=socket.socket()
try: sock.bind(('127.0.0.1',0))
except OSError as exc:
 if exc.errno in (errno.EPERM,errno.EACCES):blocked+=1
finally:sock.close()
print(json.dumps({'control':True,'escape_attempts':2,'blocked_attempts':blocked}))
'''
            command = grade_build.sandboxed([sys.executable,'-B','-c',code,str(root),str(outside)],str(root),str(root))
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
            observed = json.loads(result.stdout) if result.returncode == 0 else {}
            record = dict(schema='loop-sandbox-observation-v1', run_id=Path(run_dir).name,
                          attempt_id=attempt_id or 'NO-DATA: no proof attempt supplied',
                          observed_at=utc_now(), command=command, exit_code=result.returncode,
                          stdout=result.stdout, stderr=result.stderr, observation=observed)
            artifact = proof / 'sandbox.json'
            proof_json(artifact, record)
            return dict(enforced=bool(result.returncode == 0 and observed.get('control') is True
                                     and observed.get('blocked_attempts') == 2 and not outside.exists()),
                        escape_attempts=2, blocked_attempts=observed.get('blocked_attempts',0),
                        evidence_path=str(artifact), evidence_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest())
    except (OSError, ValueError, TypeError, ImportError, subprocess.SubprocessError) as exc:
        return 'NO-DATA: sandbox observation unavailable: ' + str(exc)


def proof_liability(path):
    """Conservatively retain all unresolved ledger liabilities, across days.

    No run tags exist on this ledger yet. It cannot justify filtering away
    another reservation. Missing or malformed is never an empty ledger.
    """
    import math
    import proof_ledger
    reserved = {}; terminal = {}
    try:
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = proof_ledger.loads(line); rid = row['reservation_id']; kind = row['type']   # a repeated member refuses
                if not isinstance(rid,str) or not rid:
                    raise ValueError('invalid reservation identity')
                if kind == 'RESERVE':
                    cost = row['estimated_cost']
                    if rid in reserved or isinstance(cost,bool) or not isinstance(cost,(int,float)) or not math.isfinite(cost) or cost < 0:
                        raise ValueError('invalid or duplicate reservation')
                    reserved[rid] = cost
                elif kind in ('RELEASE','RECONCILE','ABANDONED'):
                    if rid not in reserved or rid in terminal:
                        raise ValueError('unknown or duplicate close')
                    if kind == 'RECONCILE':
                        cost = row['actual_cost']
                        if isinstance(cost,bool) or not isinstance(cost,(int,float)) or not math.isfinite(cost) or cost < 0:
                            raise ValueError('invalid measured cost')
                    terminal[rid] = kind
                else:
                    raise ValueError('unknown ledger event')
        pending = [rid for rid in reserved if terminal.get(rid) not in ('RELEASE','RECONCILE')]
        return dict(unknown_cost_calls=len(pending), abandoned_unsettled_calls=sum(terminal.get(rid)=='ABANDONED' for rid in pending),
                    reserved_liability_usd=proof_ledger.total(reserved[rid] for rid in pending), scope='all ledger unresolved liabilities, conservative')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return 'NO-DATA: liability ledger unreadable: ' + str(exc)



def proof_accounting(run_dir, *, raw=None):
    """One pinned baseline and actual run, using the shared ledger parser.

    The end observation retains the pair-wide unknown liability and this
    run's measured spend separately. It is never a difference of unrelated
    global counters and does not depend on mutable caller policy variables.
    """
    from pathlib import Path
    import proof_ledger
    proof = Path(run_dir) / 'proof'
    if os.path.lexists(proof / 'reuse-refused.json'):
        raise ValueError('proof run directory was reused')
    start = proof_read(proof / 'start.json')
    run, attempt = proof_ledger.identity(start)
    if (start.get('schema') != 'loop-proof-start-v1' or run != Path(run_dir).name
            or start.get('phase') not in ('RB', 'RC')):
        raise ValueError('proof accounting start identity mismatch')
    ledger, baseline = start['ledger_path'], start['ledger_baseline']
    if not Path(ledger).is_absolute() or not Path(baseline).is_absolute():
        raise ValueError('proof accounting paths must be absolute')
    analysis = proof_ledger.analyze(ledger, baseline, start['ledger_baseline_sha256'], raw=raw)
    own = analysis['runs'].get(run)
    if own is not None and own['attempt_id'] != attempt:
        raise ValueError('proof accounting attempt mismatch')
    return dict(schema='loop-proof-accounting-v1', run_id=run, attempt_id=attempt,
                baseline_sha256=analysis['baseline_sha256'], ledger_sha256=analysis['ledger_sha256'],
                historic=analysis['historic'], unknown_cost_calls=analysis['unknown_cost_calls'],
                abandoned_unsettled_calls=analysis['abandoned_unsettled_calls'],
                reserved_liability_usd=analysis['reserved_liability_usd'],
                run_unknown_cost_calls=len(own['unknown_reservation_ids']) if own else 0,
                measured_spend_usd=own['measured_spend_usd'] if own else 0)


def _retain(proof, name, raw):
    """Publish raw once as proof/<name> by a hard link, which refuses any existing path (a prior snapshot, a partial
    earlier finish, a dangling link): a retained end is never overwritten. Returns the artifact path."""
    artifact = proof / name
    fd, tmp = tempfile.mkstemp(prefix='.%s-' % name, dir=str(proof))
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(raw); fh.flush(); os.fsync(fh.fileno())
        os.link(tmp, str(artifact))
    finally:
        os.unlink(tmp)
    return artifact


def proof_snapshot(run_dir):
    """Retain the exact end ledger bytes once, read under the ledger writers' lock.

    One read serves the digest and every later derivation, so a concurrent
    append can never split them. The artifact is published by a hard link,
    which refuses any existing path (a prior snapshot, a partial earlier
    finish, a dangling link): a snapshot is never overwritten.
    """
    from pathlib import Path
    import fcntl
    proof = Path(run_dir).resolve() / 'proof'
    start = proof_read(proof / 'start.json')
    ledger = Path(start['ledger_path'])
    with ledger.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        raw = ledger.read_bytes()
    artifact = _retain(proof, 'ledger-end.jsonl', raw)
    return raw, dict(schema='loop-ledger-end-v1', path=str(artifact), sha256=hashlib.sha256(raw).hexdigest(),
                     ledger_path=start['ledger_path'], baseline_path=start['ledger_baseline'],
                     baseline_sha256=start['ledger_baseline_sha256'],
                     run_id=start['run_id'], attempt_id=start['attempt_id'])


def proof_end_accounting(run_dir, evidence):
    """Rederive the recorded end accounting from the retained snapshot bytes.

    Never reads the live ledger, so later RC rows cannot change the RB end.
    Raises ValueError when the snapshot or the summary contradicts the start
    identity and pinned baseline; a missing artifact raises OSError.
    """
    from pathlib import Path
    proof = Path(run_dir).resolve() / 'proof'
    start = proof_read(proof / 'start.json')
    snapshot = evidence.get('ledger_snapshot')
    artifact = proof / 'ledger-end.jsonl'
    expected = dict(schema='loop-ledger-end-v1', path=str(artifact),
                    ledger_path=start['ledger_path'], baseline_path=start['ledger_baseline'],
                    baseline_sha256=start['ledger_baseline_sha256'],
                    run_id=start['run_id'], attempt_id=start['attempt_id'])
    if not isinstance(snapshot, dict) or any(snapshot.get(k) != v for k, v in expected.items()):
        raise ValueError('end ledger snapshot identity mismatch')
    if artifact.is_symlink():
        raise ValueError('end ledger snapshot is not retained in this run')
    raw = artifact.read_bytes()
    if hashlib.sha256(raw).hexdigest() != snapshot.get('sha256'):
        raise ValueError('end ledger snapshot digest mismatch')
    derived = proof_accounting(run_dir, raw=raw)
    if json.dumps(derived, sort_keys=True) != json.dumps(evidence['accounting'], sort_keys=True):
        raise ValueError('end accounting contradicts the retained ledger bytes')
    return derived


# THE RETAINED CLAUDE AND LANDING BYTES (U9, U10). Same contract as the ledger snapshot above: captured once at
# proof-end, every later figure rederived from them. The derivations raise ValueError for a contradiction (bytes that
# changed after the end, a record naming another run), KeyError when nothing was captured or a row cannot be read, and
# OSError when a retained file is gone; acceptance reads the first as FAIL and the other two as NO-DATA.
CLAUDE_END, LANDINGS_END = 'loop-claude-end-v1', 'loop-landings-end-v1'


def claude_snapshot(run_dir):
    """Retain the pair Claude ledger's bytes once, read under its writers' lock (the lock mark_ending also takes). A
    missing ledger is a measured empty one; one that cannot be read raises."""
    from pathlib import Path
    import proof_ledger
    proof = Path(run_dir).resolve() / 'proof'
    start = proof_read(proof / 'start.json')
    ledger = Path(start['claude_ledger_path'])
    if not ledger.is_absolute():
        raise ValueError('the Claude ledger path must be absolute')
    with proof_ledger.ledger_lock(ledger):
        try:
            raw = ledger.read_bytes()
        except FileNotFoundError:
            raw = b''
    artifact = _retain(proof, 'claude-end.jsonl', raw)
    return dict(schema=CLAUDE_END, path=str(artifact), sha256=hashlib.sha256(raw).hexdigest(),
                ledger_path=str(ledger), run_id=start['run_id'], attempt_id=start['attempt_id'])


def claude_end_spend(run_dir, evidence, end):
    """(usd, None), or (None, 'NO-DATA: why') when a call of this run is pending, uncosted, untagged or unreadable at
    the end: the retained Claude end bytes tallied by this run's tag with the end as now (Q2: a call inside its own
    timeout is pending, which settle waits for; one still pending here is NO-DATA)."""
    from pathlib import Path
    import claude_ledger
    proof = Path(run_dir).resolve() / 'proof'
    start = proof_read(proof / 'start.json')
    artifact = proof / 'claude-end.jsonl'
    snap = evidence.get('claude_snapshot')
    if not isinstance(snap, dict):
        raise KeyError('Claude end snapshot not captured: %s' % (snap,))
    expected = dict(schema=CLAUDE_END, path=str(artifact), ledger_path=start.get('claude_ledger_path'),
                    run_id=start['run_id'], attempt_id=start['attempt_id'])
    if any(snap.get(k) != v for k, v in expected.items()):
        raise ValueError('Claude end snapshot identity mismatch')
    if artifact.is_symlink():
        raise ValueError('Claude end snapshot is not retained in this run')
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != snap.get('sha256'):
        raise ValueError('Claude end snapshot digest mismatch')
    t = claude_ledger.tally(str(artifact), run_id=start['run_id'], now=end)
    note = claude_ledger.note(t)
    return (t['usd'], None) if not note else (None, _no_data(note))


def landing_record(run_dir):
    """The end record of this run's landings: the digest of proof/landings.jsonl (absence recorded as absence) and of
    every retained landing history file, so an append or a rewrite after the end reads as changed."""
    from pathlib import Path
    proof = Path(run_dir).resolve() / 'proof'
    path, hist = proof / 'landings.jsonl', proof / 'landing-history'
    logs = sorted(hist.iterdir()) if hist.is_dir() else []
    absent = not os.path.lexists(path)
    return dict(schema=LANDINGS_END, path=str(path), absent=absent,
                sha256=None if absent else hashlib.sha256(path.read_bytes()).hexdigest(),
                history={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in logs})


def landings_end(run_dir, evidence, start, end):
    """(surviving, unknown, detail) rederived from the retained landing record and history by the survival rule
    (proof_accept.surviving_landings, Q3 default)."""
    from pathlib import Path
    import proof_accept
    proof = Path(run_dir).resolve() / 'proof'
    rec = evidence.get('landings_record')
    if not isinstance(rec, dict):
        raise KeyError('landing record not captured at the end: %s' % (rec,))
    path, hist = proof / 'landings.jsonl', proof / 'landing-history'
    if rec.get('schema') != LANDINGS_END or rec.get('path') != str(path) or not isinstance(rec.get('history'), dict):
        raise ValueError('landing end record identity mismatch')
    rows = []
    if rec.get('absent') is True:
        if os.path.lexists(path):
            raise ValueError('a landing record appeared after the end')
    else:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != rec.get('sha256'):
            raise ValueError('the landing record changed after the end')
        try:
            rows = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
        except ValueError as exc:
            raise KeyError('a landing row cannot be read: %s' % exc)
    logs = {}
    for name, digest in rec['history'].items():
        raw = (hist / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('landing history %s changed after the end' % name)
        logs[name[:-len('.log')] if name.endswith('.log') else name] = raw
    return proof_accept.surviving_landings(rows, logs, start, end)



def proof_policy(phase):
    """Resolve explicit proof accounting controls without live path defaults."""
    from pathlib import Path
    import proof_ledger
    root = os.environ.get('BROTHER_OR_STATE_ROOT', '')
    baseline = os.environ.get('BROTHER_PROOF_BASELINE', '')
    expected = os.environ.get('BROTHER_PROOF_BASELINE_SHA256', '')
    if (phase not in ('RB', 'RC') or os.environ.get('BROTHER_PROOF_PHASE') != phase
            or not Path(root).is_absolute() or not Path(baseline).is_absolute()):
        raise ValueError('proof phase and absolute accounting controls required')
    ledger = str((Path(root) / 'openrouter-ledger.jsonl').resolve())
    analysis = proof_ledger.analyze(ledger, baseline, expected)
    if analysis['unknown_cost_calls']:
        raise ValueError('new proof liability remains unresolved')
    return dict(ledger_path=ledger, ledger_baseline=baseline,
                ledger_baseline_sha256=expected), analysis


def refuse_reuse(run_dir):
    """Invalidate a reused run directory and refuse it. Always raises.

    The refusal is recorded OUTSIDE the directory first (finding g, D-12): a proof folder that
    cannot be written could never hold its own reuse marker, which left an older consistent
    proof acceptable after a failed relaunch. The in-folder marker is still written when it can be.
    """
    from pathlib import Path
    import proof_launch
    outside = proof_launch.refuse(run_dir, 'run directory reused')
    try:
        proof_json(Path(run_dir) / 'proof' / 'reuse-refused.json', {'reason': 'run directory reused', 'observed_at': utc_now()})
    except OSError:
        if outside is not None:
            raise  # recorded nowhere: the refusal still stands, and says why
    raise ValueError('proof run directory must be fresh')


def _epoch(value):
    """A finite number of seconds, or None."""
    import math
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def proof_start(run_dir, phase, scope, manifest, start=None, deadline_epoch=None):
    from pathlib import Path
    import claude_ledger
    import freeze_manifest
    import proof_launch
    proof = Path(run_dir) / 'proof'
    # Reserve the identity before writing any observation. A failed reuse must
    # never leave an older receipt eligible as proof of the new attempt.
    if proof_launch.recorded(run_dir):
        refuse_reuse(run_dir)
    try:
        proof.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        refuse_reuse(run_dir)
    import uuid
    attempt_id = uuid.uuid4().hex
    policy = {}; accounting_admission = 'NO-DATA: not a proof phase'
    if phase in ('RB', 'RC'):
        try:
            policy, analysis = proof_policy(phase)
            if Path(run_dir).name in analysis['runs']:
                raise ValueError('proof run identity already appears in ledger')
            accounting_admission = 'PASS'
        except (OSError, ValueError, TypeError, KeyError) as exc:
            accounting_admission = 'NO-DATA: proof admission: ' + str(exc)
    boundary = freeze_boundary(manifest, Path(run_dir).name, 'start')
    # The work clock starts only after its boundary check. The verifier and
    # receipt writer use the same timezone-aware UTC formatter.
    record = dict(schema='loop-proof-start-v1', run_id=Path(run_dir).name, attempt_id=attempt_id,
                  phase=phase or 'NO-DATA: no proof phase', scope=scope or 'NO-DATA: no intake scope',
                  start=start or utc_now(), manifest=manifest, freeze={'start':boundary},
                  sandbox=sandbox_observation(run_dir, attempt_id) if phase in ('RB','RC') and boundary['verdict']=='PASS' and accounting_admission=='PASS' else 'NO-DATA: proof sandbox not observed')
    record.update(policy, accounting_admission=accounting_admission,
                  # what admission reads (Codex check-in 2 #1), the per run names the shared freeze leaves out (U1),
                  # and the Claude ledger this run's calls register on, bound here and never re-read from the end's env
                  deadline_epoch=_epoch(deadline_epoch),
                  volatile_env={k: hashlib.sha256(os.environ[k].encode('utf-8')).hexdigest()
                                for k in sorted(freeze_manifest.VOLATILE_ENV) if k in os.environ},
                  claude_ledger_path=os.path.abspath(claude_ledger.ledger_path()))
    if phase in ('RB', 'RC'):
        # The expectation work start and acceptance compare against, outside the run (D-12).
        proof_launch.record(run_dir, record)
    proof_json(proof/'start.json', record)
    # Exclusive creation refuses a restart reusing the same run identity. The start marker opens the intervention
    # history; proof-end closes it with an end marker, and a history without both is NO-DATA (objection 14).
    with (proof/'events.jsonl').open('x', encoding='utf-8') as fh:
        fh.write(json.dumps(dict(kind='start-marker', detail='proof start', observed_at=utc_now())) + '\n')
        fh.flush(); os.fsync(fh.fileno())
    return record


#: The production work window. BROTHER_PROOF_MIN_WINDOW_S shortens it for a rehearsal only: acceptance's duration row
#: ignores the knob, and a frozen manifest carrying it FAILs acceptance (objection 18).
PROOF_WINDOW_S = 8 * 3600


def proof_window():
    raw = os.environ.get('BROTHER_PROOF_MIN_WINDOW_S')
    if raw is None:
        return PROOF_WINDOW_S
    if not re.fullmatch(r'[0-9]+', raw) or int(raw) <= 0:
        raise ValueError('BROTHER_PROOF_MIN_WINDOW_S must be a positive whole number of seconds, got %r' % raw)
    return int(raw)


def proof_deadline(phase, deadline_epoch, now):
    """Admission includes eight work hours (or the rehearsal window) plus both boundary allowances."""
    if phase not in ('RB', 'RC'):
        return
    import math
    from datetime import datetime
    window = proof_window()
    try:
        remaining = float(deadline_epoch) - datetime.fromisoformat(now).timestamp()
    except (TypeError, ValueError):
        remaining = float('nan')
    if not math.isfinite(remaining) or remaining < window + 2 * 60:
        raise ValueError('proof deadline must leave eight hours of work (or the rehearsal window, %d s) plus two '
                         '60-second brackets' % window)


def proof_work_start(run_dir, deadline_epoch):
    from pathlib import Path
    proof = Path(run_dir) / 'proof'
    start = proof_read(proof / 'start.json')
    if start['run_id'] != Path(run_dir).name or start['schema'] != 'loop-proof-start-v1':
        raise ValueError('start identity mismatch')
    if 'work_start' in start:
        raise ValueError('work clock already started')
    import proof_launch
    # The recorded launch, not start.json or the environment, says whether this is a proof run (s4 A3 to A5).
    launch = proof_launch.bound(run_dir, start)
    boundary = freeze_boundary(start.get('manifest', ''), Path(run_dir).name, 'start')
    work = utc_now()
    proof_deadline(start.get('phase'), deadline_epoch, work)
    if start.get('phase') in ('RB', 'RC') and boundary['verdict'] != 'PASS':
        raise ValueError('work start requires a verified frozen boundary')
    if start.get('phase') in ('RB', 'RC') or os.environ.get('BROTHER_PROOF_PHASE'):
        import fcntl
        from proof_accept import sandbox_valid
        if launch is None:
            raise ValueError('proof work start requires the launch expectation recorded outside the run directory')
        if _epoch(deadline_epoch) is None or _epoch(deadline_epoch) != launch.get('deadline_epoch'):
            # admission reads the launched deadline; the window checked here is that same one, never a second one
            raise ValueError('the work start deadline %r differs from the launched deadline_epoch %r'
                             % (deadline_epoch, launch.get('deadline_epoch')))
        ledger = Path(start['ledger_path'])
        if not ledger.is_absolute():
            raise ValueError('proof ledger path must be absolute')
        with ledger.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            policy, analysis = proof_policy(start.get('phase'))
            if any(start.get(key) != value for key, value in policy.items()):
                raise ValueError('proof accounting policy changed during setup')
            if start.get('accounting_admission') != 'PASS':
                raise ValueError('initial proof accounting admission was not verified')
            accounting = proof_accounting(run_dir)
            if any(accounting[key] for key in ('unknown_cost_calls', 'abandoned_unsettled_calls', 'reserved_liability_usd')):
                raise ValueError('new proof liability remains unresolved at admission')
            if not sandbox_valid(run_dir, start.get('sandbox'), start['attempt_id']):
                raise ValueError('verified sandbox observation required at admission')
            work = utc_now()
            proof_deadline(start.get('phase'), deadline_epoch, work)
            proof_launch.admit(run_dir, start['attempt_id'], work)
            start.update(work_start=work, freeze={'start': boundary})
            proof_json(proof / 'start.json', start)
            return work
    start.update(work_start=work, freeze={'start': boundary})
    proof_json(proof / 'start.json', start)
    return work


HISTORY_MARKERS = ('start-marker', 'end-marker')


def proof_event(run_dir, kind, detail, observed_at=None):
    from pathlib import Path
    path=Path(run_dir)/'proof/events.jsonl'
    # Never recreate missing observation history after a partial failure.
    with path.open('r+',encoding='utf-8') as fh:
        fh.seek(0,2); fh.write(json.dumps(dict(kind=kind, detail=detail, observed_at=observed_at or utc_now()))+'\n')
        fh.flush(); os.fsync(fh.fileno())


def proof_finish(run_dir, end):
    from pathlib import Path
    proof=Path(run_dir)/'proof'; reason='NO-DATA: start observation unavailable'
    evidence = proof / 'evidence.json'
    try:
        start=proof_read(proof/'start.json')
        if start['run_id']!=Path(run_dir).name or start['schema']!='loop-proof-start-v1':
            raise ValueError('start identity mismatch')
    except (OSError,ValueError,TypeError,KeyError) as exc:
        start={};reason='NO-DATA: '+str(exc)
    if evidence.exists():
        recorded = proof_read(evidence)
        if (recorded.get('end') != end or recorded.get('run_id') != Path(run_dir).name
                or recorded.get('attempt_id') != start.get('attempt_id')):
            raise ValueError('end boundary identity mismatch')
        # A repeated finish revalidates the retained end, never recaptures it.
        # Only a recorded NO-DATA string skips it; anything else must derive.
        if not isinstance(recorded.get('accounting', ''), str):
            proof_end_accounting(run_dir, recorded)
        return recorded
    try:
        if not start:raise ValueError(reason)
        if os.path.lexists(Path(run_dir)/'proof-events-failed'):
            # the hold writer's second record, in a second directory: an intervention was seen and not appended (U11)
            raise ValueError('an intervention could not be appended to the history during the run (proof-events-failed)')
        proof_event(run_dir, 'end-marker', 'proof end')
        raw=(proof/'events.jsonl').read_bytes();history_sha256=hashlib.sha256(raw).hexdigest()
        events=[json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
        if any(not isinstance(row,dict) or not row.get('kind') or not row.get('observed_at') for row in events):
            raise ValueError('malformed intervention record')
        kinds=[row['kind'] for row in events]
        if len(kinds)<2 or kinds[0]!='start-marker' or kinds[-1]!='end-marker' or set(kinds[1:-1])&set(HISTORY_MARKERS):
            raise ValueError('the history is not bounded by one start marker and one end marker')
        events=events[1:-1];unattended=not events
    except (OSError,ValueError,TypeError) as exc:
        events='NO-DATA: '+str(exc);unattended=events;history_sha256=events
    state=os.environ.get('BROTHER_OR_STATE_ROOT') or os.path.expanduser('~/.claude/brother-or-dispatch-state')
    accounting = 'NO-DATA: not a proof phase'
    liability = 'NO-DATA: liability not observed'
    ledger_snapshot = claude = 'NO-DATA: not a proof phase'
    try:
        landings = landing_record(run_dir)
    except OSError as exc:
        landings = 'NO-DATA: landing record not captured at the end: ' + str(exc)
    if start.get('phase') in ('RB', 'RC') or os.environ.get('BROTHER_PROOF_PHASE'):
        try:
            claude = claude_snapshot(run_dir)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            claude = 'NO-DATA: Claude end snapshot not captured: ' + str(exc)
        try:
            # A failed capture leaves accounting NO-DATA: nothing is derived from a live read.
            raw, ledger_snapshot = proof_snapshot(run_dir)
            accounting = proof_accounting(run_dir, raw=raw)
            liability = {key: accounting[key] for key in ('unknown_cost_calls', 'abandoned_unsettled_calls', 'reserved_liability_usd')}
            liability['scope'] = 'all new liability since the shared proof baseline'
        except (OSError, ValueError, TypeError, KeyError) as exc:
            accounting = liability = 'NO-DATA: proof accounting unavailable: ' + str(exc)
            if isinstance(ledger_snapshot, str):
                ledger_snapshot = 'NO-DATA: end ledger snapshot not captured: ' + str(exc)
    else:
        liability = proof_liability(os.path.join(state, 'openrouter-ledger.jsonl'))
    row=dict(schema='loop-proof-v1',run_id=Path(run_dir).name,phase=start.get('phase',reason),
             attempt_id=start.get('attempt_id',reason),
             scope=start.get('scope',reason),end=end,work_start=start.get('work_start',reason),
             unattended=unattended,interventions=events,history_sha256=history_sha256,
             sandbox=start.get('sandbox',reason), liability=liability, accounting=accounting,
             ledger_snapshot=ledger_snapshot, claude_snapshot=claude, landings_record=landings,
             freeze={'start':start.get('freeze',{}).get('start',{'verdict':'NO-DATA'}),
                     'end':freeze_boundary(start.get('manifest',''),Path(run_dir).name,'end')})
    proof_json(proof/'evidence.json',row)
    return row


def _proof_start_record(run_dir):
    """This run's start record, or None outside a proof phase. Raises when a proof run's record cannot be read."""
    from pathlib import Path
    start = proof_read(Path(run_dir) / 'proof' / 'start.json')
    return start if start.get('phase') in ('RB', 'RC') else None


def proof_ending(run_dir, state):
    """S6: create proof/ending.json through proof_ledger.mark_ending, under the OpenRouter and Claude ledger locks this
    run's calls register under. From then on every registration for this run refuses, whatever the end state. None
    outside a proof phase. FileExistsError when the run already ended: the first record stands."""
    from pathlib import Path
    import proof_ledger
    start = _proof_start_record(run_dir)
    if start is None:
        return None
    return proof_ledger.mark_ending(run_dir, str(Path(start['ledger_path']).parent), start['claude_ledger_path'], state)


def end_claim(run_dir, pid):
    """ONE END PER RUN, claimed before the end writes anything (2026-10-04: run-20261004-010636-49463 ended STOPPED,
    then a second end appended RUN END and ALARM INTERRUPTED to the driver log after the receipt had hashed it, so the
    receipt's log hash no longer matched). An atomic mkdir of <run dir>/end-claim, with the claiming driver pid inside.
    (0, line) this caller owns the end; (3, line) a duplicate: another end owns it (its owner is alive, or a receipt
    exists), and this caller must write nothing to the run's log; (4, line) NO-DATA: the claim's owner is gone and no
    receipt exists, so the run has no trustworthy end. Covers proof and plain runs alike (proof-ending covers proofs only)."""
    d = os.path.join(run_dir, 'end-claim')
    # BUILT, THEN PUBLISHED (review round 15): the owner file is written inside a private folder that is renamed to
    # end-claim in one step, so no reader ever sees a claim without its owner. A rename onto an existing claim fails.
    import tempfile, shutil, subprocess
    tmp = tempfile.mkdtemp(prefix='.end-claim-', dir=run_dir)
    with open(os.path.join(tmp, 'owner'), 'w', encoding='utf-8') as fh:
        fh.write('%d\n' % int(pid))
    import errno
    try:
        os.rename(tmp, d)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST):
            raise   # not a claim held by another end: the caller's end fails and says why (review round 16)
        try:
            with open(os.path.join(d, 'owner'), encoding='utf-8') as fh:
                owner = int(fh.read().strip())
        except (OSError, ValueError):
            owner = None
        # ALIVE MEANS STILL A LOOP DRIVER (review round 15): a dead owner's reused pid must not read as the end's owner
        alive = False   # True: a live loop driver; None: alive, but its command cannot be read here (ps denied)
        if owner:
            try:
                os.kill(owner, 0)
            except OSError:
                alive = False
            else:
                try:
                    cmd = subprocess.run(['ps', '-o', 'command=', '-p', str(owner)], capture_output=True, text=True, timeout=10).stdout
                    alive = ('loop_until.sh' in cmd) if cmd.strip() else None
                except (OSError, subprocess.SubprocessError):
                    alive = None
        if os.path.exists(os.path.join(run_dir, 'receipt', 'receipt.json')) or alive:
            return 3, 'DUPLICATE END: the end of %s is owned by pid %s; this end writes nothing to the run log' % (os.path.basename(run_dir.rstrip('/')), owner)
        if alive is None:
            return 3, ('DUPLICATE END: the end of %s is claimed by pid %s, alive, whose command cannot be read here, so it is '
                       'taken as the owner; this end writes nothing to the run log' % (os.path.basename(run_dir.rstrip('/')), owner))
        return 4, ('NO-DATA: the end of %s was claimed by pid %s, which is gone or no loop driver, and no receipt exists; '
                   'the run has no trustworthy end' % (os.path.basename(run_dir.rstrip('/')), owner))
    return 0, 'END CLAIMED by pid %d' % int(pid)


def pending(run_dir):
    """S7: what this run still has open, read under both ledger locks (the order mark_ending takes them): each
    reservation of this run with no terminal row, and the count of its Claude calls still inside their own timeout."""
    import claude_ledger
    import proof_ledger
    from pathlib import Path
    start = _proof_start_record(run_dir)
    if start is None:
        return []
    run, left = start['run_id'], []
    with proof_ledger.ledger_lock(start['ledger_path']), proof_ledger.ledger_lock(start['claude_ledger_path']):
        raw = Path(start['ledger_path']).read_bytes()
        own = proof_ledger.analyze(start['ledger_path'], start['ledger_baseline'], start['ledger_baseline_sha256'],
                                   raw=raw)['runs'].get(run)
        t = claude_ledger.tally(start['claude_ledger_path'], run_id=run)
    if t['error']:
        raise OSError('the Claude ledger could not be read: %s' % t['error'])
    left += ['reservation %s has no terminal row' % rid for rid in (own or {}).get('inflight_reservation_ids', [])]
    if t['inflight']:
        left.append('%d Claude call(s) still inside their timeout' % t['inflight'])
    return left


def proof_settle(run_dir, max_seconds, poll=1.0):
    """Wait up to max_seconds for pending(run_dir) to empty. Returns the leftovers, [] when settled."""
    import time
    stop = time.monotonic() + max_seconds
    while True:
        left = pending(run_dir)
        remaining = stop - time.monotonic()
        if not left or remaining <= 0:
            return left
        time.sleep(min(poll, remaining))


def proof_cli(argv):
    parser=argparse.ArgumentParser()
    parser.add_argument('verb',choices=PROOF_VERBS)
    parser.add_argument('--run-dir');parser.add_argument('--kind');parser.add_argument('--detail',default='')
    parser.add_argument('--deadline-epoch');parser.add_argument('--end')
    parser.add_argument('--state');parser.add_argument('--max-seconds',type=float);parser.add_argument('--pid',type=int)
    args=parser.parse_args(argv)
    try:
        if args.verb=='clock':print(utc_now());return 0
        if not args.run_dir:raise ValueError('run-dir required')
        if args.verb=='end-claim':
            if not args.pid or args.pid<=0:raise ValueError('--pid required')
            code,line=end_claim(args.run_dir,args.pid);print(line);return code
        if args.verb=='proof-ending':
            try:
                row=proof_ending(args.run_dir,args.state)
            except FileExistsError:
                print('ENDING already recorded; the first record stands');return 0
            print('NOT A PROOF RUN: no ending marker' if row is None else 'ENDING recorded %s' % row['state']);return 0
        if args.verb=='proof-settle':
            if args.max_seconds is None or not args.max_seconds>=0:raise ValueError('--max-seconds must be a non negative number')
            left=proof_settle(args.run_dir,args.max_seconds)
            if left:print('UNSETTLED after %gs: %s' % (args.max_seconds,'; '.join(left)));return 2
            print('SETTLED');return 0
        if args.verb=='proof-event':
            if not args.kind:raise ValueError('event kind required')
            proof_event(args.run_dir,args.kind,args.detail);return 0
        if args.verb=='proof-end':
            if not args.end:raise ValueError('end required')
            proof_finish(args.run_dir,args.end);return 0
        if args.verb=='proof-work-start':
            print(proof_work_start(args.run_dir,args.deadline_epoch));return 0
        # Invalidate an attempted reuse before any admission check can fail.
        from pathlib import Path
        import proof_launch
        if os.path.lexists(Path(args.run_dir) / 'proof') or proof_launch.recorded(args.run_dir):
            refuse_reuse(args.run_dir)
        phase=os.environ.get('BROTHER_PROOF_PHASE','')
        proof_deadline(phase,args.deadline_epoch,utc_now())
        row=proof_start(args.run_dir,phase,os.environ.get('BROTHER_SCOPE',''),os.environ.get('BROTHER_FREEZE_MANIFEST',''),deadline_epoch=args.deadline_epoch)
        print(row['start'])
        if phase and (phase not in ('RB','RC') or row.get('accounting_admission')!='PASS' or row['freeze']['start']['verdict']!='PASS' or not isinstance(row['sandbox'],dict) or row['sandbox'].get('enforced') is not True):
            print('NO-DATA: proof start requires a valid phase, frozen manifest and observed sandbox',file=sys.stderr);return 2
        return 0
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print('NO-DATA: proof recording failed: '+str(exc),file=sys.stderr);return 2


PROOF_VERBS = ('end-claim', 'clock', 'proof-start', 'proof-event', 'proof-work-start', 'proof-ending', 'proof-settle', 'proof-end')


def _parser():
    p = argparse.ArgumentParser(prog="loop_receipt.py write", add_help=False)
    p.add_argument("--run-dir", required=True)
    p.add_argument("--pid", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--deadline", required=True)
    p.add_argument("--budget", required=True)
    p.add_argument("--spent-before", default="")
    p.add_argument("--spent-after", default="")
    # ACCEPTED AND IGNORED (U9): the receipt tallies Claude spend itself after the final pass. The driver still passes
    # these two (loop_until.sh raise()); they go when that caller drops them.
    p.add_argument("--claude-spent", default="", help=argparse.SUPPRESS)
    p.add_argument("--claude-note", default="", help=argparse.SUPPRESS)
    p.add_argument("--log-path", required=True)
    p.add_argument("--cwd", default=".")
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in PROOF_VERBS:
        return proof_cli(argv)
    if not argv or argv[0] != "write":
        print("usage: loop_receipt.py write --run-dir DIR --pid N --start ISO --end ISO --state STATE "
              "--reason TEXT --deadline HHMM --budget USD [--spent-before N] [--spent-after N] "
              "--log-path PATH [--cwd DIR]")
        return 2
    try:
        args = _parser().parse_args(argv[1:])
    except SystemExit:
        return 2
    try:
        proof_finish(args.run_dir, args.end)
    except (OSError, ValueError, TypeError) as exc:
        print("RECEIPT FAILED: proof recording: %s" % exc)
        return 1
    receipt = build_receipt(args)
    path, err = write_receipt(receipt, args.run_dir)
    if err:
        print("RECEIPT FAILED: %s" % err)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
