#!/usr/bin/env python3
"""CD1 (docs/plan/specs/CD1.md): one command per release that drives the cut
from freeze to after, prints one PHASE line per phase, stops at the first line
that is not PASS or REUSED, and resumes after a stop.

It replaces the two hand written 1.1.2 wrappers (run_rehearsal_112.sh and
cut_from_rehearsal_112.sh). The owner's y stays his: this tool never passes
--yes, never writes or reads an answer file, and starts cut.py only on a
terminal. It never pushes and never opens a pull request.

Footprint: one state file and one lock file per version and candidate, plus
one log per phase run, all in cut_rehearsal.evidence_dir(); no temp files.

Python 3.9, standard library only. No em or en dashes.
"""
import argparse
import calendar
import datetime
import glob
import json
import math
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
if os.path.join(HERE, "loop") not in sys.path:
    sys.path.append(os.path.join(HERE, "loop"))
import cut_rehearsal as CR  # noqa: E402
try:
    import power_rule  # noqa: E402  the one power rule, shared with the loop launcher and cut_ready
except ImportError:   # a missing power rule refuses the power check, it never stands in as AC
    power_rule = None

SCHEMA = "cv1.driver.1"
PHASES = ("ready", "freeze", "preflight", "rehearsal", "adopt", "cut", "after")
#: FR0.f: the four re-runnable, side effect free phases a sleep inside the window corrupts.
SLEEP_SENSITIVE = ("ready", "freeze", "preflight", "rehearsal")
LID_CMD = ["ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"]
LID_RE = re.compile(r'"AppleClamshellState"\s*=\s*(Yes|No)\b')
SLEEP_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) ([+-]\d{4}) Sleep\s+Entering Sleep state")
PASSING = ("PASS", "REUSED")
EXIT_FOR = {"PASS": 0, "REUSED": 0, "FAIL": 1, "NO-DATA": 2}
MAX_STATE_AGE_HOURS = 24.0
WAIT_POLL_S = 5
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SHA_RE = re.compile(r"[0-9a-f]{40}")
REHEARSAL_RE = re.compile(r"^REHEARSAL (\S+) \S+ \S+ exit (\d+) record (\S+)\s*$", re.M)


# ---------------------------------------------------------------- the runner

def real_runner(cmd, cwd, stream=False):
    """(exit code, text). stream=True inherits stdin and stderr and copies stdout to ours line by line while keeping
    it (the cut's question is typed here). Never raises: 127 for a missing binary."""
    try:
        if not stream:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            # a value is read from stdout alone when the command succeeded: stderr noise (a sandbox's xcrun cache
            # warning, 2026-10-12) must never become part of a parsed sha or count; a failure keeps both for its message
            out = proc.stdout or ""
            return proc.returncode, out if proc.returncode == 0 else out + (proc.stderr or "")
        kept = []
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, text=True, bufsize=1)
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            kept.append(line)
        return proc.wait(), "".join(kept)
    except OSError as exc:
        return 127, "%s" % exc


def network_runner(cmd, cwd, stream=False):
    """real_runner for a network read (CF1.b): bounded at NETWORK_TIMEOUT_S, killed with its process group, under the low
    speed limit and no terminal prompt. Same (exit code, text) shape; 124 on the bound, 127 for a missing binary."""
    import cut_preflight as P
    env = dict(os.environ)
    env.update(P.network_bounds())
    proc = P._run(cmd, cwd, env=env, timeout=P.NETWORK_TIMEOUT_S)
    out = proc.stdout or ""
    return proc.returncode, out if proc.returncode == 0 else out + (proc.stderr or "")


def _git(root, args, runner):
    """(stdout stripped, '') on exit 0, else ('', the failure in words)."""
    rc, text = runner(["git"] + list(args), root)
    if rc != 0:
        return "", "git %s exited %d: %s" % (" ".join(args[:3]), rc, text.strip()[:200])
    return text.strip(), ""


def _last_line(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _save_log(version, phase, text):
    """Write a child's whole output to its own log beside the state; '' when it cannot be written."""
    directory = CR.evidence_dir()
    path = os.path.join(directory, "driver-%s-%s-%d.log" % (version, phase, int(time.time())))
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        return ""
    return path


# ---------------------------------------------------------------- the state

def state_path(version, candidate, directory=""):
    return os.path.join(directory or CR.evidence_dir(), "driver-%s-%s.json" % (version, candidate[:12]))


def new_state(version, candidate, tree, release_branch, now):
    return {"schema": SCHEMA, "version": version, "candidate_sha": candidate, "candidate_tree": tree,
            "release_branch": release_branch, "started_at": float(now), "phases": {}, "tip": "", "pin": "",
            "worktree": ""}


def _read_json(path):
    """(dict, '') or (None, reason). Never raises."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "%s is corrupt or unreadable (%s)" % (path, exc)
    if not isinstance(data, dict) or not isinstance(data.get("phases"), dict):
        return None, "%s is not a driver state" % path
    return data, ""


def load_state(path, candidate, tree, now):
    """(state, '') only for a state of this schema, candidate and tree started under MAX_STATE_AGE_HOURS ago;
    (None, reason) otherwise. A corrupt file is moved aside to <name>.corrupt-<epoch>, never deleted."""
    if not os.path.exists(path):
        return None, "no state at %s" % path
    data, why = _read_json(path)
    if data is None:
        try:
            os.replace(path, "%s.corrupt-%d" % (path, int(now)))
        except OSError:
            pass
        return None, "start over: " + why
    if data.get("schema") != SCHEMA:
        return None, "start over: the state schema %r is not %s" % (data.get("schema"), SCHEMA)
    if data.get("candidate_sha") != candidate:
        return None, "start over: the state is for candidate %r, not %s" % (data.get("candidate_sha"), candidate)
    if data.get("candidate_tree") != tree:
        return None, "start over: the state is for tree %r, not %s" % (data.get("candidate_tree"), tree)
    started = data.get("started_at")
    if isinstance(started, bool) or not isinstance(started, (int, float)) \
            or not -CR.FUTURE_SLACK_S <= now - started <= MAX_STATE_AGE_HOURS * 3600:
        return None, "start over: the state started at %r, not within %g hours" % (started, MAX_STATE_AGE_HOURS)
    return data, ""


def save_state(path, state):
    """Atomic: temp file, fsync, os.replace."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def phase_line(name, verdict, seconds, detail):
    return "PHASE %s %s %ds %s" % (name, verdict, int(round(seconds)), detail)


def _verdict(state, name):
    entry = state.get("phases", {}).get(name)
    return entry.get("verdict", "") if isinstance(entry, dict) else ""


def next_phase(state):
    for name in PHASES:
        if _verdict(state, name) not in PASSING:
            return name
    return None


def _take_lock(path):
    """O_EXCL lock file holding our pid (the cut_rehearsal._take_lock pattern); '' or the refusal."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for _attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            alive, why = CR._lock_alive(path)
            if alive:
                return "another driver holds %s (%s); one driver at a time" % (path, why)
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write("%d\n" % os.getpid())
        return ""
    return "the driver lock %s could not be taken" % path


# ---------------------------------------------------------------- the phases

def _ready(root, version, runner, directory, now):
    """(path, verdict, reason, tree): the cut ready record for exactly HEAD^{tree} (cut_rehearsal.ready_record);
    tree is '' when it cannot be read."""
    tree, why = _git(root, ["rev-parse", "HEAD^{tree}"], runner)
    if why or not SHA_RE.fullmatch(tree):
        return "", "NO-DATA", why or "the tree of HEAD cannot be read: %r" % tree[:40], ""
    path, verdict, reason = CR.ready_record(version, tree, directory, now)
    return path, verdict, reason, tree


def ready_phase(root, version, runner=None, directory="", now=None):
    """(verdict, detail): REUSED when a PASS cut ready record for HEAD^{tree} exists; else run scripts/cut_ready.py
    and judge by the record read AFTER the run, never by the exit code alone."""
    runner = runner or real_runner
    path, verdict, reason, tree = _ready(root, version, runner, directory, now)
    if verdict == "PASS":
        return "REUSED", "cut-ready record %s" % path
    if not tree:
        return "NO-DATA", reason
    rc, text = runner([sys.executable, "-B", "scripts/cut_ready.py", "--version", version], root)
    log = _save_log(version, "ready", text)
    path, verdict, reason, _tree = _ready(root, version, runner, directory, now)
    if rc == 0 and verdict == "PASS":
        return "PASS", "cut-ready record %s (log %s)" % (path, log)
    if rc == 1:
        return "FAIL", "cut_ready exit 1: %s (log %s)" % (_last_line(text), log)
    if rc == 0:
        return "NO-DATA", ("cut_ready exited 0 and wrote no PASS record for this tree: %s (log %s)" % (reason, log))
    return "NO-DATA", "cut_ready exit %d: %s (log %s)" % (rc, _last_line(text), log)


def freeze_phase(root, version, runner=None, candidate="", release_branch="", hub_remote="hub", directory="",
                 now=None):
    """(verdict, detail): a clean tree, HEAD the candidate, a PASS cut ready record for exactly its tree, the hub's
    release branch, the closing ceremony 0."""
    runner = runner or real_runner
    rc, text = runner(["git", "status", "--porcelain"], root)
    if rc != 0:
        return "NO-DATA", "git status exited %d" % rc
    if text.strip():
        return "FAIL", "the tree is not clean (%d paths)" % len(text.strip().splitlines())
    head, why = _git(root, ["rev-parse", "HEAD"], runner)
    if why:
        return "NO-DATA", why
    if candidate and head != candidate:
        return "FAIL", "HEAD %s is not the candidate %s" % (head[:12], candidate[:12])
    _path, verdict, reason, tree = _ready(root, version, runner, directory, now)
    if verdict != "PASS":
        return ("FAIL" if verdict == "FAIL" else "NO-DATA"), (
            "no cut-ready PASS record for tree %s younger than 6 h: run python3 scripts/cut_ready.py --version %s (%s)"
            % (tree[:12] or "unreadable", version, reason))
    ls_remote = network_runner if runner is real_runner else runner
    rc, text = ls_remote(["git", "ls-remote", hub_remote, "refs/heads/%s" % release_branch], root)
    remote = text.split()[0] if rc == 0 and text.split() else ""
    if not SHA_RE.fullmatch(remote):
        return "NO-DATA", "%s has no readable %s (git ls-remote exit %d)" % (hub_remote, release_branch, rc)
    if remote != head:
        return "FAIL", "HEAD %s is not %s/%s %s" % (head[:12], hub_remote, release_branch, remote[:12])
    rc, text = runner([sys.executable, "-B", "scripts/close_ceremony_check.py"], root)
    log = _save_log(version, "freeze", text)
    if rc == 0:
        return "PASS", "clean at %s, ceremony log %s" % (head[:12], log)
    return ("FAIL" if rc == 1 else "NO-DATA"), "close_ceremony_check exit %d: %s (log %s)" % (rc, _last_line(text), log)


def _write_preflight_record(root, version, runner, verdict, log, seconds):
    """'' + ' record <path>' text, or the reason. FR1.a: one cv1.preflight.1 record for this candidate, so cut.py
    --check can leave the export rows this run already asked. Never raises; the phase verdict is not its to change."""
    head, why = _git(root, ["rev-parse", "HEAD"], runner)
    tree, why2 = _git(root, ["rev-parse", "HEAD^{tree}"], runner)
    if why or why2:
        return " (record not written: %s)" % (why or why2)
    export_id, id_note = "", ""
    if verdict == "PASS":
        rc, text = runner([sys.executable, "-B", "scripts/cut_preflight.py", "--version", version,
                           "--export-tree-id"], root)
        found = CR.EXPORT_TREE_RE.findall(text)
        if rc == 0 and len(found) == 1:
            export_id = found[0]
        else:
            id_note = " (export tree id not read, so this record is never reused: exit %d)" % rc
    try:
        path = CR.record_preflight(version, head, tree, export_id, verdict, log, seconds)
    except (OSError, ValueError) as exc:
        return " (record not written: %s)" % exc
    return " record %s%s" % (path, id_note)


def preflight_phase(root, version, runner=None):
    runner = runner or real_runner
    began = time.time()
    rc, text = runner([sys.executable, "-B", "scripts/cut_preflight.py", "--version", version], root)
    log = _save_log(version, "preflight", text)
    first = [ln.strip() for ln in text.splitlines() if ln.startswith(("REFUSED", "NO-DATA"))]
    said = first[0] if first else _last_line(text)
    verdict = {0: "PASS", 1: "FAIL"}.get(rc, "NO-DATA")
    note = _write_preflight_record(root, version, runner, verdict, log, max(0.0, time.time() - began))
    return verdict, "%s (log %s)%s" % (said, log, note)


def rehearsal_phase(root, version, candidate, timeout_s, runner=None):
    """(verdict, detail, record path): REUSED when a READY record already covers HEAD, else a fresh rehearsal."""
    runner = runner or real_runner
    path, _why = CR.covering_record(root, version)
    if path:
        return "REUSED", "record %s" % path, path
    rc, text = runner([sys.executable, "-B", "scripts/cut_rehearsal.py", "--version", version,
                       "--candidate", candidate, "--timeout", str(timeout_s)], root)
    log = _save_log(version, "rehearsal", text)
    found = REHEARSAL_RE.findall(text)
    if not found:
        return "NO-DATA", "no REHEARSAL line, exit %d (log %s)" % (rc, log), ""
    said, code, record = found[-1]
    verdict = {"READY": "PASS", "NOT-READY": "FAIL"}.get(said, "NO-DATA")
    if verdict == "PASS" and (rc != 0 or code != "0"):
        verdict = "NO-DATA"
    return verdict, "%s exit %d record %s (log %s)" % (said, rc, record, log), record


# ---------------------------------------------------------------- adopt and cut (CD1.b1)

# the two subjects cut_v1.0.0.sh commits (its BUMP_MSG and NOTE_MSG), as the chain log prints them
TIP_LINE = (r"cut_v1\.0\.0\.sh \| ([0-9a-f]{9}) %s: the (?:version bump and the regenerated manifests"
            r"|export manifest that describes the tree and the note it ships)")
CUT_SUBJECTS = ("%s: the version bump and the regenerated manifests",
                "%s: the export manifest that describes the tree and the note it ships")
EXIT_INCOMPLETE = 3   # cut.py's own EXIT_INCOMPLETE
COVER_CODE = ('import sys; sys.path.insert(0, "scripts"); import cut; r = cut._covering_record(".", sys.argv[1], '
              'sys.argv[2]); print("OK" if r[0] and not r[1] else "REFUSED: %r" % (r,))')


def pin_name(version, candidate):
    return "rehearsed/%s-%s" % (version, candidate[:9])


def tip_from_log(log_text, version):
    """(9 hex of the LAST cut commit line in the rehearsal log, '') or ('', reason)."""
    found = re.findall(TIP_LINE % re.escape(version), log_text or "")
    if not found:
        return "", "the rehearsal log names no cut commit for %s" % version
    return found[-1], ""


def adopt_problem(root, candidate, tip, version, runner=None):
    """'' when tip is one or two cut commits on top of candidate, else the reason."""
    runner = runner or real_runner
    rc, text = runner(["git", "merge-base", "--is-ancestor", candidate, tip], root)
    if rc == 1:
        return "%s does not descend from the candidate %s" % (tip[:12], candidate[:12])
    if rc != 0:
        return "the ancestry of %s cannot be read (exit %d)" % (tip[:12], rc)
    count, why = _git(root, ["rev-list", "--count", "%s..%s" % (candidate, tip)], runner)
    if why:
        return why
    if count not in ("1", "2"):
        return "%s..%s is %s commits, not the cut's one or two" % (candidate[:12], tip[:12], count)
    subjects, why = _git(root, ["log", "--format=%s", "%s..%s" % (candidate, tip)], runner)
    if why:
        return why
    allowed = [s % version for s in CUT_SUBJECTS]
    foreign = [s for s in subjects.splitlines() if s not in allowed]
    if foreign or len(subjects.splitlines()) != int(count):
        return "a commit after the candidate is not the cut's own: %r" % (foreign[:1] or subjects,)
    return ""


def adopt_phase(root, version, candidate, record_path, runner=None):
    """(verdict, detail, tip, worktree): pin the rehearsed tip and make a detached worktree at it. The release
    branch of the main checkout is never touched here."""
    runner = runner or real_runner
    record, why = CR.load_record(record_path)
    if record is None:
        return "NO-DATA", why, "", ""
    if record.get("verdict") != "READY" or record.get("candidate_sha") != candidate:
        return "FAIL", "the record %s says %s for %s, not READY for %s" % (
            record_path, record.get("verdict"), str(record.get("candidate_sha"))[:12], candidate[:12]), "", ""
    try:
        with open(record["log_path"], "r", encoding="utf-8", errors="replace") as fh:
            log_text = fh.read()
    except OSError as exc:
        return "NO-DATA", "the rehearsal log is unreadable: %s" % exc, "", ""
    tip9, why = tip_from_log(log_text, version)
    if why:
        return "FAIL", why, "", ""
    tip, why = _git(root, ["rev-parse", "--verify", "--quiet", "%s^{commit}" % tip9], runner)
    if why or not SHA_RE.fullmatch(tip):
        return "FAIL", "%s is not a commit in this repository" % tip9, "", ""
    why = adopt_problem(root, candidate, tip, version, runner)
    if why:
        return "FAIL", why, "", ""
    pin = pin_name(version, candidate)
    had_pin, _ = _git(root, ["rev-parse", "--verify", "--quiet", "refs/heads/%s^{commit}" % pin], runner)
    if had_pin and had_pin != tip:
        return "FAIL", "the pin %s exists at %s, not %s; it is never moved" % (pin, had_pin[:12], tip[:12]), "", ""
    if not had_pin:
        _out, why = _git(root, ["branch", pin, tip], runner)
        if why:
            return "FAIL", "could not pin %s: %s" % (tip[:12], why), "", ""
    worktree = os.path.join(CR.scratch_root(), "cut-wt", "%s-%s" % (version, tip[:12]))
    had_tree = os.path.exists(worktree)
    if had_tree:
        head, why = _git(worktree, ["rev-parse", "HEAD"], runner)
        rc, dirty = runner(["git", "status", "--porcelain"], worktree)
        if why or head != tip or rc != 0 or dirty.strip():
            return "FAIL", ("the worktree %s is not clean at %s; the driver never removes it, the owner decides"
                            % (worktree, tip[:12])), tip, worktree
    else:
        _out, why = _git(root, ["worktree", "add", "--detach", worktree, tip], runner)
        if why:
            return "FAIL", "no detached worktree at %s: %s" % (tip[:12], why), tip, worktree
    rc, text = runner([sys.executable, "-B", "-c", COVER_CODE, version, tip], worktree)
    if rc != 0 or _last_line(text) != "OK":
        return "FAIL", "the covering record does not accept %s: %s" % (tip[:12], _last_line(text)), tip, worktree
    verdict = "REUSED" if had_pin and had_tree else "PASS"
    return verdict, "pin %s at %s, worktree %s" % (pin, tip[:12], worktree), tip, worktree


def cut_command(worktree, version, tip, body):
    return [sys.executable, "-u", os.path.join(worktree, "scripts", "cut.py"), "--version", version,
            "--resume", tip, "--release-notes-file", body]


def cut_phase(worktree, version, tip, body, runner=None, tty=None):
    """(verdict, detail). Only on a terminal: the owner types the y into cut.py itself."""
    runner = runner or real_runner
    if tty is None:
        tty = sys.stdin.isatty()
    if tty is not True:
        return "NO-DATA", "the cut asks its question on a terminal; start the driver in the owner's terminal"
    rc, text = runner(cut_command(worktree, version, tip, body), worktree, stream=True)
    log = _save_log(version, "cut", text)
    if rc == 0:
        if not re.search(r"^DONE: cut %s pushed" % re.escape(version), text, re.M):
            return "FAIL", "the cut exited 0 without pushing (declined?): %s (log %s)" % (_last_line(text), log)
        ls_remote = network_runner if runner is real_runner else runner
        rc2, tags = ls_remote(["git", "ls-remote", "origin", "refs/tags/v%s" % version], worktree)
        if rc2 != 0 or len([ln for ln in tags.splitlines() if ln.strip()]) != 1:
            return "FAIL", "the cut said pushed but v%s is not one public tag (log %s)" % (version, log)
        return "PASS", "v%s public (log %s)" % (version, log)
    if rc == EXIT_INCOMPLETE:
        return "FAIL", "cut incomplete (exit 3), finish with the guarded command it printed: %s (log %s)" % (
            _last_line(text), log)
    return "FAIL", "cut exited %d: %s (log %s)" % (rc, _last_line(text), log)


# ---------------------------------------------------------------- after (CD1.b2)

REPO = "khalilmaaouni/Brother"
DISPATCH_GRACE_S = 180      # a run created after the dispatch must appear within this
INSTALL_TIMEOUT_S = 1200    # the virgin-install workflow's own timeout
LIST_POLL_S = 10
VIEW_POLL_S = 30
SUMMARY_RE = re.compile(r"fails=(\d+) nodata=(\d+)")


def verify_command(version, body):
    return ["sh", "scripts/post_publish_verify.sh", version, body]


def verify_result(output, code):
    """PASS only for exit 0, a last line exactly fails=0 nodata=0 and no FAIL or NO-DATA line."""
    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    if not lines:
        return "NO-DATA", "the verifier printed nothing (exit %d)" % code
    m = SUMMARY_RE.fullmatch(lines[-1])
    if not m:
        return "NO-DATA", "the verifier printed no summary (exit %d): %s" % (code, lines[-1])
    if m.group(1) != "0":
        return "FAIL", lines[-1]
    if m.group(2) != "0":
        return "NO-DATA", lines[-1]
    if code != 0 or any(ln.startswith(("FAIL", "NO-DATA")) for ln in lines):
        return "FAIL", "the verifier summary %s disagrees with its exit %d or its lines" % (lines[-1], code)
    return "PASS", lines[-1]


def _epoch(stamp):
    try:
        return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def _json(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def install_proof(version, runner=None, sleep=None, clock=None):
    """(verdict, detail): dispatch virgin-install at the tag; PASS only for a success conclusion of a run created
    at or after the dispatch."""
    runner = runner or real_runner
    sleep = sleep or time.sleep
    clock = clock or time.time
    started = clock()
    rc, text = runner(["gh", "workflow", "run", "virgin-install.yml", "-R", REPO, "--ref", "v%s" % version], None)
    if rc != 0:
        return "NO-DATA", "the virgin-install dispatch was refused (exit %d): %s" % (rc, _last_line(text))
    run_id = None
    while run_id is None:
        rc, text = runner(["gh", "run", "list", "--workflow", "virgin-install.yml", "-R", REPO, "--json",
                           "databaseId,headSha,status,conclusion,createdAt", "--limit", "5"], None)
        for item in (_json(text) if rc == 0 else None) or []:
            created = _epoch(item.get("createdAt")) if isinstance(item, dict) else None
            if created is not None and created >= int(started):
                run_id = item.get("databaseId")
                break
        if run_id is None:
            if clock() - started >= DISPATCH_GRACE_S:
                return "NO-DATA", "no virgin-install run created after the dispatch within %ds" % DISPATCH_GRACE_S
            sleep(LIST_POLL_S)
    found = clock()
    while True:
        rc, text = runner(["gh", "run", "view", str(run_id), "-R", REPO, "--json", "status,conclusion"], None)
        data = _json(text) if rc == 0 else None
        if isinstance(data, dict) and data.get("status") == "completed":
            said = data.get("conclusion")
            return ("PASS" if said == "success" else "FAIL"), "virgin-install run %s: %s" % (run_id, said)
        if clock() - found >= INSTALL_TIMEOUT_S:
            return "NO-DATA", "virgin-install run %s did not finish within %ds" % (run_id, INSTALL_TIMEOUT_S)
        sleep(VIEW_POLL_S)


def fast_forward(root, release_branch, pin, runner=None):
    """(verdict, detail): the only move of the release branch, by fast forward to the pin, in a clean checkout
    on that branch. REUSED when it already equals the pin."""
    runner = runner or real_runner
    target, why = _git(root, ["rev-parse", "--verify", "--quiet", "refs/heads/%s^{commit}" % pin], runner)
    if why or not SHA_RE.fullmatch(target):
        return "FAIL", "the pin %r is not a branch here" % pin
    current, why = _git(root, ["branch", "--show-current"], runner)
    if why or current != release_branch:
        return "FAIL", "the main checkout is on %r, not %s" % (current, release_branch)
    rc, text = runner(["git", "status", "--porcelain"], root)
    if rc != 0:
        return "NO-DATA", "git status exited %d" % rc
    if text.strip():
        return "FAIL", "the main checkout is not clean"
    head, why = _git(root, ["rev-parse", "HEAD"], runner)
    if why:
        return "NO-DATA", why
    if head == target:
        return "REUSED", "%s already at the pin %s" % (release_branch, target[:12])
    rc, _t = runner(["git", "merge-base", "--is-ancestor", release_branch, target], root)
    if rc != 0:
        return "FAIL", "%s is not an ancestor of the pin %s (exit %d)" % (release_branch, target[:12], rc)
    _out, why = _git(root, ["merge", "--ff-only", target], runner)
    after, _ = _git(root, ["rev-parse", "HEAD"], runner)
    if why or after != target:
        return "FAIL", "the fast forward to %s did not land: %s" % (target[:12], why)
    return "PASS", "%s fast forwarded to the pin %s" % (release_branch, target[:12])


def after_phase(root, version, body, release_branch, pin, runner=None, sleep=None, clock=None, steps=None):
    """(verdict, detail): the public verifier, then the install proof, then and only then the fast forward. A step
    already PASS in `steps` (the state's record of a partial after) is not repeated; `steps` is updated in place.
    Pushes nothing and opens no pull request: the next commands are printed for the session."""
    runner = runner or real_runner
    steps = {} if steps is None else steps
    if steps.get("verify") != "PASS":
        rc, text = runner(verify_command(version, body), root)
        log = _save_log(version, "verify", text)
        verdict, detail = verify_result(text, rc)
        steps["verify"] = verdict
        if verdict != "PASS":
            return verdict, "post publish verifier: %s (log %s)" % (detail, log)
    if steps.get("install") != "PASS":
        verdict, detail = install_proof(version, runner, sleep, clock)
        steps["install"] = verdict
        if verdict != "PASS":
            return verdict, "install proof: " + detail
    verdict, detail = fast_forward(root, release_branch, pin, runner)
    steps["fast_forward"] = verdict
    if verdict not in PASSING:
        return verdict, detail
    return verdict, ("%s; next for the session (this driver pushes nothing): the secret, dash and private terms "
                     "scans over the range, then git push hub %s and the hub pull request" % (detail, release_branch))


# ---------------------------------------------------------------- the branch invariant (CR1.b)

def branch_invariant(root, release_branch, candidate, after_passed, runner=None):
    """'' when the release branch is still at the candidate (candidate..branch empty) or the after phase has
    passed (its fast forward is the only move); otherwise the violation in one sentence: the count and the first
    three subjects. The 1.1.2 cut stopped at the push with the branch already fast forwarded onto its own two
    commits. A branch or candidate that does not resolve, or a count that does not parse, is a violation too:
    unknown blocks. Read only."""
    if after_passed:
        return ""
    runner = runner or real_runner
    branch, why = _git(root, ["rev-parse", "--verify", "--quiet", "refs/heads/%s^{commit}" % release_branch], runner)
    if not release_branch or why or not SHA_RE.fullmatch(branch):
        return "the release branch %r does not resolve" % (release_branch,)
    cand, why = _git(root, ["rev-parse", "--verify", "--quiet", "%s^{commit}" % candidate], runner)
    if not candidate or why or not SHA_RE.fullmatch(cand):
        return "the candidate %r is not a commit" % (candidate,)
    span = "%s..%s" % (cand, branch)
    count, why = _git(root, ["rev-list", "--count", span], runner)
    if why or not count.isdigit():
        return "git rev-list --count %s..%s gave no count (%s)" % (cand[:12], release_branch, why or count[:40])
    if count == "0":
        return ""
    subjects, _why = _git(root, ["log", "--format=%h %s", "-3", span], runner)
    return "%s is %s commit(s) ahead of the candidate %s: %s" % (
        release_branch, count, cand[:12], "; ".join(subjects.splitlines()) or "subjects unreadable")


# ---------------------------------------------------------------- the run

def _resolve(root, candidate, version, directory, runner):
    """(sha, tree, why). No candidate given: a finished state of this version whose tip is HEAD names its own."""
    sha, why = _git(root, ["rev-parse", "--verify", "%s^{commit}" % (candidate or "HEAD")], runner)
    if why or not SHA_RE.fullmatch(sha):
        return "", "", "the candidate %r does not resolve: %s" % (candidate or "HEAD", why)
    if not candidate:
        for path in glob.glob(os.path.join(directory or CR.evidence_dir(), "driver-%s-*.json" % version)):
            data, _ = _read_json(path)
            if data and data.get("tip") == sha and SHA_RE.fullmatch(str(data.get("candidate_sha", ""))):
                sha = data["candidate_sha"]
                break
    tree, why = _git(root, ["rev-parse", "%s^{tree}" % sha], runner)
    if why or not SHA_RE.fullmatch(tree):
        return "", "", "the tree of %s cannot be read: %s" % (sha[:12], why)
    return sha, tree, ""


def _default_power_source():
    if power_rule is None:
        return "unreadable"
    try:
        got = power_rule.power_source()
    except Exception:
        return "unreadable"
    return got if isinstance(got, str) else "unreadable"


def _default_lid():
    if power_rule is None:
        return "unreadable"
    text = power_rule.command_stdout(LID_CMD)
    if not isinstance(text, str):
        return "unreadable"
    match = LID_RE.search(text)
    if match is None:
        return "unreadable"
    return "closed" if match.group(1).lower() == "yes" else "open"


def _power_pair(power):
    """(source, lid) from the injected reader or from this host; None when it cannot be read."""
    if power is None:
        return _default_power_source(), _default_lid()
    if not callable(power):
        return None
    try:
        got = power()
    except Exception:
        return None
    if isinstance(got, (tuple, list)) and len(got) == 2 and all(isinstance(x, str) for x in got):
        return got[0], got[1]
    return None


def power_refusal(power=None):
    """'' when a cut may start, else the NO-DATA refusal sentence.

    Only AC with an open lid is known to last. Battery, a source this host
    cannot read and a closed lid all refuse; a host that is not macOS reports
    no-data and does not block.
    """
    pair = _power_pair(power)
    if pair is None:
        return "NO-DATA: the power reading is unreadable and only AC is known to last: plug in the charger"
    source, lid = pair
    if source == "no-data":
        return ""
    if source == "battery":
        return "NO-DATA: the machine draws from battery and a low battery sleep ends the run: plug in the charger"
    if source != "ac":
        return ("NO-DATA: the power source is unreadable (%s) and only AC is known to last: plug in the charger"
                % source)
    if lid == "closed":
        return "NO-DATA: the lid is closed and a clamshell sleep ends the run: open the lid"
    return ""


def _first_sleep_in(text, start, end):
    """The first pmset Sleep time in [start, end], or None. Both edges are inclusive."""
    if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start):
        return None
    if isinstance(end, bool) or not isinstance(end, (int, float)) or not math.isfinite(end):
        return None
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        match = SLEEP_LINE_RE.match(line)
        if match is None:
            continue
        try:
            when = datetime.datetime.strptime(
                "%s %s %s" % (match.group(1), match.group(2), match.group(3)),
                "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            continue
        try:
            stamp = when.timestamp()
        except (OSError, OverflowError, ValueError):
            continue
        if start <= stamp <= end:
            return stamp
    return None


def _default_sleeps(start, end):
    """The sleep note from this host's own pmset log; '' on a host that is not macOS."""
    if sys.platform != "darwin" or power_rule is None:
        return ""
    text = power_rule.command_stdout(["pmset", "-g", "log"])
    when = _first_sleep_in(text, start, end)
    if when is None:
        return ""
    return "a sleep at %d fell inside this phase" % int(when)


def _any_note(start, end, sleeps=None):
    """'' or a sentence naming the first sleep inside the window; never raises."""
    if sleeps is None:
        return _default_sleeps(start, end)
    if not callable(sleeps):
        return ""
    try:
        got = sleeps(start, end)
    except Exception:
        return ""
    return got if isinstance(got, str) else ""


def sleep_note(name, start, end, sleeps=None):
    """'' unless the phase is in SLEEP_SENSITIVE and the log holds a Sleep inside the window."""
    if not isinstance(name, str) or name not in SLEEP_SENSITIVE:
        return ""
    return _any_note(start, end, sleeps)


def _run_phase(name, state, root, version, body, runner, tty, hub_remote, timeout_s, directory="", now=None):
    """(verdict, detail, extra fields for the state entry)."""
    if name == "ready":
        v, d = ready_phase(root, version, runner, directory, now)
        return v, d, {}
    if name == "freeze":
        v, d = freeze_phase(root, version, runner, state["candidate_sha"], state["release_branch"], hub_remote,
                            directory, now)
        return v, d, {}
    if name == "preflight":
        v, d = preflight_phase(root, version, runner)
        return v, d, {}
    if name == "rehearsal":
        v, d, record = rehearsal_phase(root, version, state["candidate_sha"], timeout_s, runner)
        return v, d, {"record": record}
    if name == "adopt":
        record = state["phases"].get("rehearsal", {}).get("record", "")
        v, d, tip, worktree = adopt_phase(root, version, state["candidate_sha"], record, runner)
        state["tip"], state["worktree"] = tip, worktree
        state["pin"] = pin_name(version, state["candidate_sha"]) if v in PASSING else state.get("pin", "")
        return v, d, {"record": record}
    if name == "cut":
        if not SHA_RE.fullmatch(state.get("tip", "")) or not os.path.isdir(state.get("worktree", "")):
            return "NO-DATA", "no adopted tip and worktree in the state; rerun the adopt", {}
        v, d = cut_phase(state["worktree"], version, state["tip"], body, runner, tty)
        return v, d, {}
    prev = state["phases"].get("after")
    steps = dict(prev.get("steps", {})) if isinstance(prev, dict) and isinstance(prev.get("steps"), dict) else {}
    v, d = after_phase(root, version, body, state["release_branch"], state["pin"], runner, steps=steps)
    return v, d, {"steps": steps}


def run(root, version, candidate, body, release_branch, runner=None, clock=None, tty=None, out=None,
        hub_remote="hub", timeout_s=CR.DEFAULT_TIMEOUT_S, directory="", require_state=False,
        power=None, sleeps=None):
    runner = runner or real_runner
    clock = clock or time.time
    out = out or sys.stdout

    def say(line):
        out.write(line + "\n")
        out.flush()

    if not VERSION_RE.fullmatch(version or ""):
        say("NO-DATA: --version %r is not X.Y.Z" % (version,))
        return 2
    sha, tree, why = _resolve(root, candidate, version, directory, runner)
    if why:
        say("NO-DATA: " + why)
        return 2
    path = state_path(version, sha, directory)
    lock = path[:-len(".json")] + ".lock"
    why = _take_lock(lock)
    if why:
        say("NO-DATA: " + why)
        return 2
    try:
        existed = os.path.exists(path)
        state, why = load_state(path, sha, tree, clock())
        if state is None:
            if require_state:
                say("NO-DATA: nothing to resume: " + why)
                return 2
            if existed:
                say(why)
            if not release_branch:
                release_branch, why = _git(root, ["branch", "--show-current"], runner)
                if why or not release_branch:
                    say("NO-DATA: no release branch named and HEAD is on none")
                    return 2
            state = new_state(version, sha, tree, release_branch, clock())
        if next_phase(state) is None:
            say("already complete: all %d phases PASS or REUSED for %s %s" % (len(PHASES), version, sha[:12]))
            return 0
        def moved(name):
            """CR1.b: the branch invariant before `name`; True (state saved, line said) when it is violated."""
            why = branch_invariant(root, state["release_branch"], state["candidate_sha"],
                                   _verdict(state, "after") in PASSING, runner)
            if not why:
                return False
            detail = "the release branch moved: " + why
            state["phases"][name] = {"verdict": "NO-DATA", "started": clock(), "seconds": 0, "detail": detail,
                                     "record": "", "head_after": ""}
            save_state(path, state)
            say(phase_line(name, "NO-DATA", 0, detail))
            return True

        began = power_refusal(power)
        if began:
            say(began)
            return 2

        for name in PHASES[PHASES.index(next_phase(state)):]:
            if _verdict(state, name) in PASSING:
                continue
            if moved(name):
                return 2
            lost = power_refusal(power)
            if lost:
                state["phases"][name] = {"verdict": "NO-DATA", "started": clock(), "seconds": 0, "detail": lost,
                                         "record": "", "head_after": ""}
                save_state(path, state)
                say(phase_line(name, "NO-DATA", 0, lost))
                return 2
            started = clock()
            verdict, detail, extra = _run_phase(name, state, root, version, body, runner, tty, hub_remote,
                                                timeout_s, directory)
            head, _ = _git(root, ["rev-parse", "HEAD"], runner)
            ended = clock()
            note = _any_note(started, ended, sleeps)
            if note and name in SLEEP_SENSITIVE:
                detail = "%s; the phase said %s (%s); rerun it" % (note, verdict, detail)
                verdict = "NO-DATA"
            elif note:
                detail = "%s; %s" % (detail, note)
            entry = {"verdict": verdict, "started": started, "seconds": ended - started, "detail": detail,
                     "record": "", "head_after": head}
            entry.update(extra)
            state["phases"][name] = entry
            save_state(path, state)
            say(phase_line(name, verdict, entry["seconds"], detail))
            if name == "cut" and moved("after"):     # whatever the cut exited with
                return 2
            if verdict not in PASSING:
                return EXIT_FOR.get(verdict, 2)
        return 0
    finally:
        CR._release_lock(lock)


# ---------------------------------------------------------------- wait

def _newest_state(version, directory=""):
    paths = glob.glob(os.path.join(directory or CR.evidence_dir(), "driver-%s-*.json" % version))
    return max(paths, key=os.path.getmtime) if paths else ""


def _deadline(until, now):
    """The next local occurrence of HH:MM after `now`; None for no deadline; raises ValueError for a bad value."""
    if not until:
        return None
    m = re.fullmatch(r"([01]?[0-9]|2[0-3]):([0-5][0-9])", until)
    if not m:
        raise ValueError("--until %r is not HH:MM" % until)
    local = time.localtime(now)
    target = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, int(m.group(1)), int(m.group(2)), 0,
                          0, 0, -1))
    return target if target > now else target + 86400


def wait(version, phase, until="", directory="", sleep=None, clock=None, out=None):
    """Block until `phase` has a verdict in the newest state of `version`; its exit code is that verdict's. Reads
    only: it never writes the state."""
    sleep = sleep or time.sleep
    clock = clock or time.time
    out = out or sys.stdout
    if phase not in PHASES:
        out.write("NO-DATA: unknown phase %r; the phases are %s\n" % (phase, ", ".join(PHASES)))
        return 2
    try:
        deadline = _deadline(until, clock())
    except ValueError as exc:
        out.write("NO-DATA: %s\n" % exc)
        return 2
    while True:
        path = _newest_state(version, directory)
        data, _ = _read_json(path) if path else (None, "")
        entry = (data or {}).get("phases", {}).get(phase)
        if isinstance(entry, dict) and entry.get("verdict"):
            verdict = entry["verdict"]
            out.write(phase_line(phase, verdict, entry.get("seconds", 0) or 0, entry.get("detail", "")) + "\n")
            return EXIT_FOR.get(verdict, 2)
        if deadline is not None and clock() >= deadline:
            out.write("NO-DATA: no %s verdict for %s by %s (%s)\n"
                      % (phase, version, until, path or "no state file"))
            return 2
        sleep(WAIT_POLL_S)


# ---------------------------------------------------------------- check (CD1.c)

def complete(state):
    """(every phase in PHASES PASS or REUSED, the phases that are not). Unknown extra phase keys are ignored."""
    missing = [name for name in PHASES if _verdict(state, name) not in PASSING]
    return not missing, missing


def check(version, directory="", out=None):
    """0 only when the newest state of `version` has every phase in PHASES PASS or REUSED; 1 names the first that is not;
    2 for no state or a corrupt one. Reads only; no age limit (a finished cut is evidence)."""
    out = out or sys.stdout
    path = _newest_state(version, directory)
    if not path:
        out.write("NO-DATA: no driver state for %s\n" % version)
        return 2
    data, why = _read_json(path)
    if data is None:
        out.write("NO-DATA: %s\n" % why)
        return 2
    done, missing = complete(data)
    cand = str(data.get("candidate_sha", ""))
    if done:
        out.write("COMPLETE %s candidate %s tip %s: %s\n" % (version, cand, data.get("tip", ""), ", ".join(
            "%s %s" % (n, _verdict(data, n)) for n in PHASES)))
        return 0
    out.write("INCOMPLETE %s candidate %s: %s is %s (%s)\n" % (version, cand, missing[0],
                                                              _verdict(data, missing[0]) or "not run", path))
    return 1


# ---------------------------------------------------------------- main

def _parser():
    ap = argparse.ArgumentParser(description="Drive the cut: ready, freeze, preflight, rehearsal, adopt, cut, after. "
                                             "The owner types the y; this tool never passes it.", allow_abbrev=False)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("run", "resume"):
        p = sub.add_parser(name, allow_abbrev=False)
        p.add_argument("--version", required=True)
        p.add_argument("--body", required=True, help="the release notes file the cut publishes")
        p.add_argument("--candidate", default="")
        p.add_argument("--release-branch", default="")
        p.add_argument("--hub-remote", default="hub")
        p.add_argument("--timeout", type=int, default=CR.DEFAULT_TIMEOUT_S)
    p = sub.add_parser("wait", allow_abbrev=False)
    p.add_argument("--version", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--until", default="")
    p = sub.add_parser("check", allow_abbrev=False)
    p.add_argument("--version", required=True)
    return ap


def main(argv=None, root=None, runner=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:      # any other argument, --yes and --answer-file included: nothing runs
        return exc.code if isinstance(exc.code, int) else 2
    if args.command == "wait":
        return wait(args.version, args.phase, args.until)
    if args.command == "check":
        return check(args.version)
    return run(root or ROOT, args.version, args.candidate, os.path.abspath(args.body), args.release_branch,
               runner=runner, hub_remote=args.hub_remote, timeout_s=args.timeout,
               require_state=args.command == "resume")


if __name__ == "__main__":
    sys.exit(main())
