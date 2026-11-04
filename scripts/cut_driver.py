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
import codecs
import datetime
import glob
import io
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
    """(exit code, text). stream=True inherits stdin and stderr and copies stdout to ours as each chunk arrives, a
    partial line included, while keeping it (CR15: the cut's [y/N] question ends without a newline and is typed
    here; a line relay held it back at the 1.1.4 cut). The kept text is UTF-8 with errors replaced, newlines
    translated as text mode translates them. Never raises: 127 for a missing binary."""
    try:
        if not stream:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            # a value is read from stdout alone when the command succeeded: stderr noise (a sandbox's xcrun cache
            # warning, 2026-10-12) must never become part of a parsed sha or count; a failure keeps both for its message
            out = proc.stdout or ""
            return proc.returncode, out if proc.returncode == 0 else out + (proc.stderr or "")
        kept = []
        # incremental: a character, or a \r\n, split across two reads decodes whole
        decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder("utf-8")("replace"), translate=True)
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, bufsize=0)
        try:
            while True:
                chunk = os.read(proc.stdout.fileno(), 65536)   # what is there now; never waits for a newline
                piece = decoder.decode(chunk, final=not chunk)
                if piece:
                    sys.stdout.write(piece)
                    sys.stdout.flush()
                    kept.append(piece)
                if not chunk:
                    break
        finally:
            proc.stdout.close()
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


def new_state(version, candidate, tree, release_branch, now, inputs=None):
    """A fresh driver state. `inputs` (CUT1.b) is the identity it is started under, by default this checkout's for
    `candidate` (cut_rehearsal.inputs_identity); a phase entry that records none of its own is judged by it."""
    if inputs is None:
        inputs = CR.inputs_identity(ROOT, candidate, version)
    return {"schema": SCHEMA, "version": version, "candidate_sha": candidate, "candidate_tree": tree,
            "release_branch": release_branch, "started_at": float(now), "phases": {}, "tip": "", "pin": "",
            "worktree": "", "inputs": inputs, "attempts": []}


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


def automatic_mode(candidate, previous):
    """CUT2.a: {mode, reason}, the cold, resume or follow choice the owner never types. `previous` is the validated
    state of an earlier run ({candidate, lock_held}) or None; lock_held is the live lock readback. A matching run
    whose lock is held is FOLLOWED, a matching one whose lock is free is RESUMED, anything else starts COLD. A lock
    readback that is neither True nor False is treated as held (the _lock_alive rule): nothing is started."""
    if not isinstance(previous, dict):
        return {"mode": "COLD", "reason": "no earlier run for this candidate"}
    before = previous.get("candidate")
    if not isinstance(candidate, str) or not SHA_RE.fullmatch(candidate) or before != candidate:
        return {"mode": "COLD", "reason": "the earlier run is for candidate %r, not %r; its proof is not reused"
                % (before, candidate)}
    if previous.get("lock_held") is False:
        return {"mode": "RESUME", "reason": "the earlier run for this candidate stopped; it is picked up"}
    return {"mode": "FOLLOW", "reason": "a run for this candidate is already going; it is followed, not repeated"}


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
    rc, text = runner([sys.executable, "-B", "scripts/close_ceremony_check.py", "--require-current"], root)
    log = _save_log(version, "freeze", text)
    if rc == 0:
        return "PASS", "clean at %s, ceremony log %s" % (head[:12], log)
    return ("FAIL" if rc == 1 else "NO-DATA"), "close_ceremony_check exit %d: %s (log %s)" % (rc, _last_line(text), log)


#: CUT2.c: the generators whose output the candidate carries, each run bare to write and with --check to prove.
GENERATORS = ("scripts/bundle_runtime.py", "scripts/system_doc.py")


def _step(name, answer):
    """(verdict, reason) from a preparation operation's answer, a verdict or (verdict, reason)."""
    reason = ""
    if isinstance(answer, (tuple, list)) and len(answer) == 2 and isinstance(answer[1], str):
        answer, reason = answer
    if answer not in ("PASS", "FAIL", "NO-DATA"):
        return "NO-DATA", "%s answered %r, not PASS, FAIL or NO-DATA" % (name, answer)
    return answer, "%s: %s" % (name, reason or answer)


def prepare_release(operations):
    """CUT2.c: {verdict, identity?, reason?}. Runs operations generate, validate and freeze in that order, each at
    most once: a generate or validate that is not PASS stops before the freeze and its verdict is the answer. An
    operation that raises, a missing or uncallable one, and a freeze answering anything but a 40 hex identity are
    NO-DATA with the reason, never PASS. PASS carries identity, exactly what the freeze returned. Never raises."""
    try:
        ops = [(name, operations[name]) for name in ("generate", "validate", "freeze")]
    except Exception:  # noqa: BLE001  # sbe: allow-silent a malformed table is answered NO-DATA below
        ops = []
    if not ops or not all(callable(op) for _name, op in ops):
        return {"verdict": "NO-DATA", "reason": "preparation needs callable generate, validate and freeze; "
                                                "nothing ran"}
    for name, op in ops:
        try:
            answer = op()
        except Exception as exc:  # noqa: BLE001  # sbe: allow-silent an operation that raises is NO-DATA, named
            return {"verdict": "NO-DATA", "reason": "%s raised %s: %s" % (name, type(exc).__name__, exc)}
        if name == "freeze":
            if isinstance(answer, str) and SHA_RE.fullmatch(answer):
                return {"verdict": "PASS", "identity": answer}
            return {"verdict": "NO-DATA", "reason": "freeze named no 40 hex identity: %r" % (answer,)}
        verdict, reason = _step(name, answer)
        if verdict != "PASS":
            return {"verdict": verdict, "reason": reason}
    return {"verdict": "NO-DATA", "reason": "the freeze never ran"}


def prepared_freeze(root, version, runner=None, candidate="", release_branch="", hub_remote="hub", directory="",
                    now=None):
    """CUT2.c: (verdict, detail), the freeze phase through prepare_release. generate, on a clean tree only, runs
    each of GENERATORS with --check (cheap freshness) and regenerates through the bare generator only the stale ones,
    so a fresh tree is never rewritten (bundle_runtime rewrites a provisional -dirty stamp on every bare run).
    validate refuses, naming them, any paths the generators changed (they need committing and proving first: the
    candidate is never frozen with a tree other than the proven one), then runs each --check again. freeze is
    freeze_phase, whose own verdict and detail are the answer when it is not PASS."""
    runner = runner or real_runner
    seen = {}
    texts = []

    def porcelain():
        rc, text = runner(["git", "status", "--porcelain"], root)
        if rc != 0:
            return None, "git status exited %d" % rc
        return [ln for ln in text.splitlines() if ln.strip()], ""

    def run(script, extra):
        rc, text = runner([sys.executable, "-B", script] + extra, root)
        texts.append(text)
        return rc, "%s exit %d: %s" % (" ".join([script] + extra), rc, _last_line(text))

    def generate():
        paths, why = porcelain()
        if paths is None:
            return "NO-DATA", why
        if paths:
            return "FAIL", "the tree is not clean (%d paths); nothing was generated" % len(paths)
        for script in GENERATORS:
            if run(script, ["--check"])[0] != 0:   # stale or unreadable: the normal generator writes it
                rc, why = run(script, [])
                if rc != 0:
                    return "FAIL", why
        return "PASS", ""

    def validate():
        paths, why = porcelain()
        if paths is None:
            return "NO-DATA", why
        if paths:
            return "FAIL", ("the generators changed %d paths (%s); commit them and prove that tree before the cut"
                            % (len(paths), ", ".join(p[3:] for p in paths[:10])))
        for script in GENERATORS:
            rc, why = run(script, ["--check"])
            if rc != 0:
                return "FAIL", why
        return "PASS", ""

    def freeze():
        seen["freeze"] = freeze_phase(root, version, runner, candidate, release_branch, hub_remote, directory, now)
        if seen["freeze"][0] != "PASS":
            return ""
        head = _git(root, ["rev-parse", "HEAD"], runner)[0]
        if candidate and SHA_RE.fullmatch(head) and head != candidate:   # HEAD moved after freeze_phase checked it
            seen["freeze"] = ("FAIL", "the frozen HEAD %s is not the candidate %s; nothing is frozen"
                              % (head[:12], candidate[:12]))
            return ""
        return head

    got = prepare_release({"generate": generate, "validate": validate, "freeze": freeze})
    log = _save_log(version, "prepare", "\n".join(texts)) if texts else ""
    if "freeze" in seen and seen["freeze"][0] != "PASS":
        return seen["freeze"]
    if got["verdict"] == "PASS":
        return "PASS", "%s; prepared %s, frozen %s (log %s)" % (
            seen["freeze"][1], " and ".join(GENERATORS), got["identity"][:12], log)
    return got["verdict"], "preparation %s (log %s)" % (got["reason"], log)


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


def _record_json(record_path):
    """(dict, '') for the JSON object at `record_path`, else (None, reason naming the path). Never raises."""
    try:
        with open(record_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError) as exc:
        return None, "the rehearsal record %s is corrupt or unreadable (%s)" % (record_path, exc)
    if not isinstance(data, dict):
        return None, "the rehearsal record %s is not an object" % record_path
    return data, ""


def record_bumped_sha(record_path):
    """(bumped_sha, bumped_tree, why) from a rehearsal record (CS12.a). A record of the bumped schema must carry
    both as 40 hex: it never falls back to the log. An older record may lack them, which reads as ('', '', '');
    one that carries a malformed value is refused. Any other schema, a non-object, a torn or missing file: why."""
    data, why = _record_json(record_path)
    if data is None:
        return "", "", why
    schema = data.get("schema")
    if schema not in CR.SCHEMAS_READ:
        return "", "", "the rehearsal record %s has schema %r, not one of %s" % (
            record_path, schema, ", ".join(CR.SCHEMAS_READ))
    bumped_sha, bumped_tree = data.get("bumped_sha", ""), data.get("bumped_tree", "")
    if not isinstance(bumped_sha, str) or not isinstance(bumped_tree, str):
        return "", "", "the rehearsal record %s carries a bumped_sha or bumped_tree that is not text" % record_path
    if schema == CR.SCHEMA_BUMPED and not (SHA_RE.fullmatch(bumped_sha) and SHA_RE.fullmatch(bumped_tree)):
        return "", "", "the record %s is schema %s and must carry bumped_sha and bumped_tree" % (record_path, schema)
    if schema != CR.SCHEMA_BUMPED and (bumped_sha or bumped_tree) and not (
            SHA_RE.fullmatch(bumped_sha) and SHA_RE.fullmatch(bumped_tree)):
        return "", "", "the rehearsal record %s carries a bumped_sha or bumped_tree that is not 40 hex" % record_path
    return bumped_sha, bumped_tree, ""


def verified_rehearsal_log(record_path):
    """(log text, '') only when the record names a log that exists and still hashes to its log_sha256, else
    ('', reason). Every adopted record passes through here: the log is the evidence the record stands on."""
    data, why = _record_json(record_path)
    if data is None:
        return "", why
    log_path, log_sha256 = data.get("log_path"), data.get("log_sha256")
    if not isinstance(log_path, str) or not log_path or not isinstance(log_sha256, str) or not log_sha256:
        return "", "the rehearsal record %s names no log or no log hash" % record_path
    try:
        actual = CR._sha256_file(log_path)
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        return "", "the rehearsal log is unreadable: %s" % exc
    if actual != log_sha256:
        return "", "the rehearsal log %s was altered after the record %s was written" % (log_path, record_path)
    return text, ""


def adopt_phase(root, version, candidate, record_path, runner=None):
    """(verdict, detail, tip, worktree): pin the rehearsed tip and make a detached worktree at it. The release
    branch of the main checkout is never touched here. CS12.a: a record that names its bumped_sha is adopted only
    when refs/brother/rehearsed/<version>-<candidate12> points at exactly that commit; a record without it (an
    older schema) reads the tip from its log. Either way the log must still hash to the record's log_sha256."""
    runner = runner or real_runner
    record, why = CR.load_record(record_path)
    if record is None:
        return "NO-DATA", why, "", ""
    if record.get("verdict") != "READY" or record.get("candidate_sha") != candidate:
        return "FAIL", "the record %s says %s for %s, not READY for %s" % (
            record_path, record.get("verdict"), str(record.get("candidate_sha"))[:12], candidate[:12]), "", ""
    bumped_sha, _bumped_tree, why = record_bumped_sha(record_path)
    if why:
        return "FAIL", why, "", ""
    log_text, why = verified_rehearsal_log(record_path)
    if why:
        return ("NO-DATA" if "unreadable" in why else "FAIL"), why, "", ""
    if bumped_sha:
        ref = CR.rehearsed_ref_name(version, candidate)
        actual, _why = _git(root, ["rev-parse", "--verify", "--quiet", "%s^{commit}" % ref], runner)
        if actual != bumped_sha:
            return "FAIL", "the record names bumped_sha %s but %s points at %s" % (
                bumped_sha, ref, actual or "nothing"), "", ""
        tip = bumped_sha
    else:
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
    made_pin = False
    had_pin, _ = _git(root, ["rev-parse", "--verify", "--quiet", "refs/heads/%s^{commit}" % pin], runner)
    if had_pin and had_pin != tip:
        if not bumped_sha:
            return "FAIL", "the pin %s exists at %s, not %s; it is never moved" % (pin, had_pin[:12], tip[:12]), "", ""
        # a second rehearsal of the same candidate moved the ref to a new tip, and the record and the ref agree on
        # it: the pin follows them, else a rerun could never adopt the commit that was proved last
        _out, why = _git(root, ["branch", "-f", pin, tip], runner)
        if why:
            return "FAIL", "could not move the pin %s from %s to %s: %s" % (pin, had_pin[:12], tip[:12], why), "", ""
        had_pin = tip   # moved, so this run made the pin: PASS below, never REUSED
        made_pin = True
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
    verdict = "REUSED" if had_pin and had_tree and not made_pin else "PASS"
    return verdict, "pin %s at %s, worktree %s" % (pin, tip[:12], worktree), tip, worktree


# ---------------------------------------------------------------- X01, the Linux half (RDM1.b)

#: RDM1.b (2026-10-29), the owner's cut gate AC2 of 2026-10-23. The dispatch only virgin-install workflow walks
#: J1 and J2 on the exported candidate and its RUN LOG is the record (the workflow's own note says so). The
#: spec fixes no other source, so this reads that log: the newest finished run whose log names the candidate's
#: identity, bound by the `identity candidate=<sha> installed=<sha>` line scripts/journey/run_journey.py prints.
#: This reads a run; it dispatches none (the cost law: the owner or the session dispatches it in the public repo).
UBUNTU_JOURNEYS = ("J1", "J2")
LOG_PREFIX_RE = re.compile(r"^.*?\d{4}-\d{2}-\d{2}T[0-9:.]+Z ")
LOG_SECTION_RE = re.compile(r"^=== (J\d+) ===\s*$")
LOG_ROW_RE = re.compile(r"^(PASS|FAIL|NO-DATA)\s+\S")
LOG_IDENTITY_RE = re.compile(r"^identity candidate=(\S+) installed=(\S+)\s*$")
LOG_EXIT_RE = re.compile(r"^(J\d+) exit (\d+)\s*$")


def candidate_manifest_identity(worktree):
    """sha256 of the worktree's bundle runtime manifest, the identity the walker binds a walk by; '' unreadable."""
    import hashlib
    try:
        with open(os.path.join(worktree, "bundle", "runtime", "RUNTIME-MANIFEST.json"), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def parse_ubuntu_log(text, identity):
    """(None, '') when the log is not about `identity`; else (ok, detail): ok True only when J1 and J2 both ran on
    that identity, exited 0 and printed rows that are all PASS. Anything missing or malformed is not ok."""
    sections, current = {}, None
    for raw in (text or "").splitlines():
        line = LOG_PREFIX_RE.sub("", raw).strip()
        m = LOG_SECTION_RE.match(line)
        if m:
            current = sections.setdefault(m.group(1), {"rows": [], "ident": None, "exit": None})
        elif current is not None:
            if LOG_ROW_RE.match(line):
                current["rows"].append(line.split()[0])
            elif LOG_IDENTITY_RE.match(line):
                current["ident"] = LOG_IDENTITY_RE.match(line).groups()
            elif LOG_EXIT_RE.match(line):
                current["exit"] = int(LOG_EXIT_RE.match(line).group(2))
                current = None      # later lines of the log belong to no journey
    if not any(sec["ident"] and sec["ident"][0] == identity for sec in sections.values()):
        return None, ""
    for journey in UBUNTU_JOURNEYS:
        sec = sections.get(journey)
        if sec is None:
            return False, "the log has no %s section" % journey
        if sec["ident"] != (identity, identity):
            return False, "%s walked another tree (%s)" % (journey, sec["ident"])
        if sec["exit"] != 0:
            return False, "%s exit %s" % (journey, sec["exit"])
        if not sec["rows"] or any(v != "PASS" for v in sec["rows"]):
            return False, "%s rows are not all PASS (%s)" % (journey, ",".join(sec["rows"]) or "none")
    return True, "J1 and J2 PASS on ubuntu"


def ubuntu_x01_gate(worktree, runner=None):
    """(verdict, detail): PASS only when the newest finished virgin-install run that walked THIS candidate read
    J1 and J2 PASS on ubuntu. No run for the candidate, an unreadable log or a gh that cannot run is NO-DATA,
    never PASS; a run that walked it and did not pass is FAIL."""
    runner = runner or real_runner
    identity = candidate_manifest_identity(worktree)
    if not identity:
        return "NO-DATA", "the candidate's runtime manifest cannot be read, so it has no identity"
    rc, text = runner(["gh", "run", "list", "--workflow", "virgin-install.yml", "-R", REPO, "--json",
                       "databaseId,status,createdAt", "--limit", "10"], None)
    runs = _json(text) if rc == 0 else None
    if not isinstance(runs, list):
        return "NO-DATA", "the virgin-install runs cannot be listed (exit %d): %s" % (rc, _last_line(text))
    done = [r for r in runs if isinstance(r, dict) and r.get("status") == "completed" and r.get("databaseId")]
    for item in sorted(done, key=lambda r: str(r.get("createdAt")), reverse=True):
        rc, log = runner(["gh", "run", "view", str(item["databaseId"]), "-R", REPO, "--log"], None)
        if rc != 0:
            return "NO-DATA", "run %s log cannot be read (exit %d)" % (item["databaseId"], rc)
        ok, detail = parse_ubuntu_log(log, identity)
        if ok is None:
            continue
        return ("PASS" if ok else "FAIL"), "virgin-install run %s: %s" % (item["databaseId"], detail)
    return "NO-DATA", "no finished virgin-install run walked candidate %s on ubuntu" % identity[:12]


def cut_command(worktree, version, tip, body, approval=""):
    """The cut's argv; `approval` (CY1.b), the owner's app line, is passed on as --approval when given."""
    cmd = [sys.executable, "-u", os.path.join(worktree, "scripts", "cut.py"), "--version", version,
           "--resume", tip, "--release-notes-file", body]
    return cmd + ["--approval", approval] if approval else cmd


def cut_phase(worktree, version, tip, body, runner=None, tty=None, approval=""):
    """(verdict, detail). On a terminal the owner types the y into cut.py itself; with `approval` (CY1.b) the
    owner's app line answers instead, so no terminal is needed, and cut.py checks the line."""
    runner = runner or real_runner
    if tty is None:
        tty = sys.stdin.isatty()
    if tty is not True and not approval:
        return "NO-DATA", ("the cut asks its question on a terminal; start the driver in the owner's terminal, "
                           "or pass the owner's app line with --approval")
    rc, text = runner(cut_command(worktree, version, tip, body, approval), worktree, stream=True)
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


def _run_phase(name, state, root, version, body, runner, tty, hub_remote, timeout_s, directory="", now=None,
               approval=""):
    """(verdict, detail, extra fields for the state entry)."""
    # CUT2.a: the cut publishes `body`, so a missing or empty one stops every phase before it, the rehearsal
    # included; the cut and after phases leave it to cut.py and the verifier, which refuse it themselves.
    try:
        body_ok = name in ("cut", "after") or bool(body) and os.path.isfile(body) and os.path.getsize(body) > 0
    except OSError:
        body_ok = False
    if not body_ok:
        return "NO-DATA", "the release notes file %r is missing or empty; nothing ran" % (body,), {}
    if name == "ready":
        v, d = ready_phase(root, version, runner, directory, now)
        return v, d, {}
    if name == "freeze":
        v, d = prepared_freeze(root, version, runner, state["candidate_sha"], state["release_branch"], hub_remote,
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
        # RDM1.b: nothing is published unless the Linux half of X01 read J1 and J2 PASS on the exported candidate.
        v, d = ubuntu_x01_gate(state["worktree"], runner)
        if v != "PASS":
            return v, "X01 Linux half: " + d, {}
        v, d = cut_phase(state["worktree"], version, state["tip"], body, runner, tty, approval)
        return v, d, {}
    prev = state["phases"].get("after")
    steps = dict(prev.get("steps", {})) if isinstance(prev, dict) and isinstance(prev.get("steps"), dict) else {}
    v, d = after_phase(root, version, body, state["release_branch"], state["pin"], runner, steps=steps)
    return v, d, {"steps": steps}


#: CUT1.b: the one key function, shared with the rehearsal rows (cut_rehearsal.evidence_key).
evidence_key = CR.evidence_key


def reuse_miss(entry, inputs, fallback=None, now=None):
    """'' when a kept phase entry may be REUSED for `inputs`, else why not (CUT1.a, CUT1.b). Only a PASS or REUSED
    entry whose recorded inputs give exactly this evidence key is reused; an entry from before CUT1 carries none and
    is judged by `fallback`, the identity its state was started under. Unknown inputs on either side never hit.
    CUT2.h: the last word is shared_evidence_usable, the one rule readiness, rehearsal and cut share, over the entry
    read as its shared shape (a PASS made when the phase ended, good for MAX_STATE_AGE_HOURS, its identity and key
    verified): a window that does not hold or a key that does not match its inputs is a miss, never a reuse. `now`
    is epoch seconds or a clock to read (only when a window is judged); default the wall clock."""
    if not isinstance(entry, dict):
        return "no earlier attempt"
    if entry.get("verdict") not in PASSING:
        return "the earlier attempt read %s" % (entry.get("verdict") or "nothing")
    key = evidence_key(inputs)
    if key is None:
        return "the inputs are unknown, so nothing is reused"
    recorded = entry["inputs"] if "inputs" in entry else fallback
    if evidence_key(recorded) is None:
        return "the earlier attempt recorded no input identity"
    if evidence_key(recorded) != key:
        return "changed: %s" % ", ".join(CR.input_changes(recorded, inputs))
    started, seconds = entry.get("started"), entry.get("seconds")
    made = started + seconds if CR._is_num(started) and CR._is_num(seconds) else None
    shared = {"schema": 1, "verdict": "PASS", "inputs": recorded, "created_at": made,
              "valid_until": made + MAX_STATE_AGE_HOURS * 3600 if made is not None else None,
              "evidence_id": entry.get("key", key)}
    clock = now() if callable(now) else (time.time() if now is None else now)
    if not shared_evidence_usable(shared, dict(inputs), clock, lambda r: evidence_key(r["inputs"]) == r["evidence_id"]):
        return "the earlier attempt is not usable shared evidence (its window or its key did not verify)"
    return ""


def shared_evidence_usable(record, expected, now, verify):
    """CUT2.b: True only when `record` is one authenticated, fresh PASS made under exactly the `expected` identity,
    so readiness, rehearsal and cut may all stand on it instead of running the check again. `record` is the shared
    shape {schema: 1, verdict, inputs, created_at, valid_until, evidence_id}; `expected` maps every identity name
    (code, checker, policy, interpreter, host, home, export, ...) to an opaque digest; `verify` is the caller's own
    trusted provenance check (it re-hashes the bytes behind evidence_id), never a flag read from the record. Every
    key on either side must be present on the other with the same non empty text, so a new identity field an old
    record lacks is a miss. The window is created_at <= now < valid_until. Anything malformed, and a verify that
    raises or answers anything but True, is False: a doubt runs the full check, it never reuses. Never raises."""
    try:
        if not isinstance(record, dict) or not isinstance(expected, dict) or not expected:
            return False
        if type(record.get("schema")) is not int or record["schema"] != 1 or record.get("verdict") != "PASS":
            return False
        inputs = record.get("inputs")
        if not isinstance(inputs, dict) or set(inputs) != set(expected):
            return False
        if not all(CR._is_str(v) and v and inputs[k] == v for k, v in expected.items()):
            return False
        made, until = record.get("created_at"), record.get("valid_until")
        if not (CR._is_num(made) and CR._is_num(until) and CR._is_num(now)) or not made <= now < until:
            return False
        return verify(record) is True
    except Exception:  # noqa: BLE001  # sbe: allow-silent a proof that cannot be judged is not reused
        return False


#: CUT2.f: the three things one approval binds, and the publication steps publication_next answers.
PUBLICATION_FIELDS = ("candidate", "export", "body")
FINAL_STEP = "DONE"


def _publication_identity(value):
    """The (candidate, export, body) tuple when each is non empty text and the candidate is 40 lowercase hex, else
    None, so a malformed identity never equals a good one."""
    if not isinstance(value, dict):
        return None
    got = tuple(value.get(k) for k in PUBLICATION_FIELDS)
    if not all(CR._is_str(v) and v for v in got) or not SHA_RE.fullmatch(got[0]):
        return None
    return got


def _publication_step(state, now):
    def say(action, reason):
        return {"action": action, "reason": reason}
    if not isinstance(state, dict):
        return say("REFUSE", "the publication state is not a record; nothing is published")
    ready = state.get("ready")
    if ready is not True:
        if ready is None or ready is False:
            return say("PREPARE", "the candidate, export and body are not prepared and checked yet; nothing is "
                                  "asked before they are")
        return say("REFUSE", "ready reads %r, not true or false" % (ready,))
    identity = _publication_identity(state.get("identity"))
    if identity is None:
        return say("REFUSE", "the prepared identity is not a 40 hex candidate with an export and a body")
    if not CR._is_num(now):
        return say("REFUSE", "the clock reading %r is not a number" % (now,))
    remote = state.get("remote_commit")
    if remote is not None:
        if not CR._is_str(remote) or not SHA_RE.fullmatch(remote):
            return say("REFUSE", "the remote read answered %r, not nothing or a 40 lowercase hex commit" % (remote,))
        if remote != identity[0]:
            return say("REFUSE", "the remote tag is foreign: it points at %s, not the candidate %s; it is never "
                                 "moved or adopted" % (remote[:12], identity[0][:12]))
        # the tag is already there at the candidate: an authorized effect that happened is never approved or
        # pushed again, whatever the approval now reads
        if state.get("artifacts_verified") is not True:
            return say("VERIFY", "the tag is on the remote at the candidate: verify the public artifacts, no second "
                                 "push and no new approval")
        if state.get("receipt_written") is not True:
            return say("WRITE_RECEIPT", "the tag and its artifacts read back: write the durable publication receipt")
        return say(FINAL_STEP, "the tag, its read back artifacts and a durable receipt are all in place")
    if state.get("artifacts_verified") is True or state.get("receipt_written") is True:
        return say("REFUSE", "read back artifacts or a receipt are claimed while the remote shows no tag; the state "
                             "contradicts itself, nothing is pushed")
    approval = state.get("approval")
    if approval is None:
        return say("ASK_APPROVAL", "prepared; the owner's one approval for this candidate, export and body")
    if not isinstance(approval, dict):
        return say("REFUSE", "the approval is not a record")
    if _publication_identity(approval.get("identity")) != identity:
        return say("ASK_APPROVAL", "the approval names another candidate, export or body; a new one for this "
                                   "identity, the old one never pushes")
    used, expires = approval.get("used"), approval.get("expires")
    if used is not False:
        # a spent one use approval with no tag on the remote is a replay, or a push whose effect is not
        # reconciled: a fresh approval would authorize a second push over it, so this stops instead of asking
        return say("REFUSE", "the approval is %s: a one use approval is never replayed, and with no tag on the "
                             "remote its effect is unreconciled" % ("already used" if used is True
                                                                    else "of unknown use (%r)" % (used,)))
    if not CR._is_num(expires):
        return say("REFUSE", "the approval's expiry %r is not a number" % (expires,))
    if now >= expires:
        return say("ASK_APPROVAL", "the approval expired; a new one for this identity")
    return say("PUSH", "one unused, unexpired approval of exactly this candidate, export and body, and no tag on "
                       "the remote")


def publication_next(state, now):
    """CUT2.f: {action, reason}, the one next publication step from trusted controller read back. `state` carries
    ready, identity {candidate, export, body}, approval {identity, expires, used} or None, remote_commit (the
    candidate the remote tag is attributable to, a foreign commit, or None), artifacts_verified and
    receipt_written; `now` is epoch seconds. Not ready PREPAREs before anything is asked; a remote tag at the
    candidate VERIFYs, then WRITE_RECEIPT, then the final step, with no second push and no new approval; any other
    remote tag is REFUSEd. With no tag, PUSH needs an approval of exactly this identity, unused, with now < expires:
    none, a changed identity or an expired one ASK_APPROVAL anew; a used one REFUSEs. Anything malformed REFUSEs or
    PREPAREs, never PUSH or the final step. Never raises: an error is REFUSE. Approval is only consulted here;
    cut.py makes, checks and spends it."""
    try:
        return _publication_step(state, now)
    except Exception as exc:  # noqa: BLE001  # sbe: allow-silent a state that cannot be judged publishes nothing
        return {"action": "REFUSE", "reason": "the publication state could not be judged (%s)" % type(exc).__name__}


def affected_checks(graph, changed_paths, known_base=True):
    """CUT2.b: the ids of the checks a change can reach. `graph` maps id -> {inputs: [paths], deps: [ids], always:
    bool}. Selected: every check whose inputs name a changed path, every check downstream of one (transitively), the
    prerequisites of all of those (transitively), and every always check. A changed path no check names, a changed
    list that is not a list of paths, or known_base=False selects the whole graph. A malformed node, a dep naming no
    check, or a cycle raises ValueError.
    SELECTION ONLY: nothing calls this to skip or narrow a real check. Narrowing waits for the shadow full suite
    comparison and the seeded consumer faults that prove the selector equivalent (a later CUT2.b slice)."""
    if not isinstance(graph, dict):
        raise ValueError("the check graph is not a mapping")
    for cid, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("always"), bool):
            raise ValueError("check %r is not a node with a boolean always" % (cid,))
        for field in ("inputs", "deps"):
            if not isinstance(node.get(field), (list, tuple)) or not all(CR._is_str(x) for x in node[field]):
                raise ValueError("check %r has no %s list" % (cid, field))
        missing = [d for d in node["deps"] if d not in graph]
        if missing:
            raise ValueError("check %r depends on unknown %s" % (cid, ", ".join(missing)))
    state = {}

    def visit(cid, trail):
        if state.get(cid) == "done":
            return
        if state.get(cid) == "open":
            raise ValueError("the check graph has a cycle through %s" % " -> ".join(trail + [cid]))
        state[cid] = "open"
        for dep in graph[cid]["deps"]:
            visit(dep, trail + [cid])
        state[cid] = "done"
    for cid in graph:
        visit(cid, [])
    everything = set(graph)
    if not known_base or not isinstance(changed_paths, (list, tuple, set, frozenset)) \
            or not all(CR._is_str(p) for p in changed_paths):
        return everything
    named = set(p for node in graph.values() for p in node["inputs"])
    if any(p not in named for p in changed_paths):
        return everything
    changed = set(changed_paths)
    picked = set(cid for cid, node in graph.items() if changed.intersection(node["inputs"]))
    grew = True
    while grew:   # downstream: a consumer of a picked check is picked
        more = set(cid for cid, node in graph.items() if picked.intersection(node["deps"])) - picked
        picked |= more
        grew = bool(more)
    todo = list(picked)
    while todo:   # prerequisites of everything picked
        for dep in graph[todo.pop()]["deps"]:
            if dep not in picked:
                picked.add(dep)
                todo.append(dep)
    return picked | set(cid for cid, node in graph.items() if node["always"])


def _shape(got):
    """(verdict, detail, extra fields) from a phase runner's answer; a fixture's third item is the tree it saw."""
    if not isinstance(got, (tuple, list)) or len(got) not in (2, 3) or got[0] not in EXIT_FOR \
            or not isinstance(got[1], str):
        return "NO-DATA", "the phase answered %r, not (verdict, detail[, extra])" % (got,), {}
    extra = got[2] if len(got) == 3 else {}
    if isinstance(extra, str):
        extra = {"tree": extra}
    return got[0], got[1], extra if isinstance(extra, dict) else {}


def _walk(state, path, table, inputs, resume, clock, say=None, before=None, after=None, then=None):
    """THE ONE PHASE LOOP (CUT1.a), for the real phases and a fixture's alike: [(phase, (verdict, detail))] in table
    order. A phase whose kept entry passes reuse_miss is REUSED and not run; any other phase runs, and every run
    appends one attempt row (phase, seconds, inputs, result, reuse_miss) to the state, saved before the next phase.
    The walk stops after the first phase that is not PASS or REUSED. Hooks, all optional: before(name) answers a
    (verdict, detail) refusal recorded in place of the run; after(name, verdict, detail, started, ended) answers
    (verdict, detail, extra fields); then(name) True stops the walk after the phase. The second value says whether a
    hook stopped it."""
    key = evidence_key(inputs)
    attempts = state.setdefault("attempts", [])
    results = []
    for name, runner in table.items():
        entry = state["phases"].get(name)
        miss = reuse_miss(entry, inputs, state.get("inputs"), clock) if resume else "a fresh run reuses nothing"
        if not miss:
            results.append((name, ("REUSED", "kept: %s" % entry.get("detail", ""))))
            continue
        refused = before(name) if before else None
        started = clock()
        if refused:
            verdict, detail, extra, ended = refused[0], refused[1], {}, started
        else:
            try:
                got = runner(state)
            except Exception as exc:   # a runner that raises is NO-DATA for its phase, never a pass
                got = ("NO-DATA", "the %s phase raised %s" % (name, type(exc).__name__))
            verdict, detail, extra = _shape(got)
            ended = clock()
            if after:
                verdict, detail, more = after(name, verdict, detail, started, ended)
                extra = dict(extra, **more)
        new = {"verdict": verdict, "started": started, "seconds": ended - started, "detail": detail, "record": "",
               "head_after": "", "inputs": inputs, "key": key}
        new.update(extra)
        state["phases"][name] = new
        attempts.append({"phase": name, "started": started, "seconds": new["seconds"], "inputs": inputs,
                         "key": key, "result": verdict, "reuse_miss": miss})
        save_state(path, state)
        if say:
            say(phase_line(name, verdict, new["seconds"], detail))
        results.append((name, (verdict, "%s (ran: %s)" % (detail, miss))))
        if refused or (then and then(name)):
            return results, True
        if verdict not in PASSING:
            break
    return results, False


def _fixture_state(path, version, now):
    """(state, note): the kept state at `path` for a phase table run, or a fresh one with the reason. A corrupt file
    is moved aside and reuses nothing; an older one than MAX_STATE_AGE_HOURS starts over (its attempt rows kept)."""
    fresh = {"schema": SCHEMA, "version": version, "started_at": float(now), "phases": {}, "attempts": []}
    if not os.path.exists(path):
        return fresh, "no state at %s" % path
    data, why = _read_json(path)
    if data is None:
        try:
            os.replace(path, "%s.corrupt-%d" % (path, int(now)))
        except OSError:
            pass
        return fresh, "start over, nothing reused: " + why
    started = data.get("started_at")
    if data.get("schema") != SCHEMA or isinstance(started, bool) or not isinstance(started, (int, float)) \
            or not -CR.FUTURE_SLACK_S <= now - started <= MAX_STATE_AGE_HOURS * 3600:
        kept = data.get("attempts") if isinstance(data.get("attempts"), list) else []
        return dict(fresh, attempts=kept), "start over: the state is not %s or not within %g hours" % (
            SCHEMA, MAX_STATE_AGE_HOURS)
    return data, ""


def run(version, phases=None, state_dir="", inputs=None, resume=False, *cd1, **kw):
    """CUT1.a: drive `phases`, a table {name: runner(state) -> (verdict, detail[, extra])} in order, under the state
    file in `state_dir`, reusing on `resume` every kept entry whose `inputs` give the same evidence key. Returns
    [(phase, (verdict, detail))]. The lock is the real driver's: a second run meets it and reads NO-DATA naming it.

    The CD1 command line shape, run(root, version, candidate, body, release_branch, ...), is the same entry with the
    real phase table: it is told apart by `phases` not being a table and answers its exit code (drive below)."""
    if not isinstance(phases, dict):
        return drive(version, phases, state_dir, inputs, resume, *cd1, **kw)
    first = next(iter(phases), "ready")
    if not VERSION_RE.fullmatch(version if isinstance(version, str) else "") or not isinstance(state_dir, str) \
            or not state_dir:
        return [(first, ("NO-DATA", "a version X.Y.Z and a state folder are needed, not %r %r" % (version, state_dir)))]
    path = os.path.join(state_dir, "driver-%s.json" % version)
    lock = path[:-len(".json")] + ".lock"
    why = _take_lock(lock)
    if why:
        return [(first, ("NO-DATA", why))]
    try:
        clock = kw.get("clock") or time.time
        state, _note = _fixture_state(path, version, clock())
        return _walk(state, path, phases, inputs, bool(resume), clock)[0]
    finally:
        CR._release_lock(lock)


def real_phases(state, root, version, body, runner, tty, hub_remote, timeout_s, directory, approval):
    """The real phase table over _run_phase, in PHASES order, for _walk."""
    def one(name):
        return lambda st: _run_phase(name, st, root, version, body, runner, tty, hub_remote, timeout_s, directory,
                                     approval=approval)
    return dict((name, one(name)) for name in PHASES)


def drive(root, version, candidate, body, release_branch, runner=None, clock=None, tty=None, out=None,
          hub_remote="hub", timeout_s=CR.DEFAULT_TIMEOUT_S, directory="", require_state=False,
          power=None, sleeps=None, approval=""):
    """The real cut, through the same _walk as run(): its exit code. `approval` (CY1.b) reaches only the cut phase
    and is never written to the state file."""
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
    # CUT2.a: a held lock is keyed by this candidate, so a second ask follows that run; it reads, never writes.
    held, holder = CR._lock_alive(lock) if os.path.exists(lock) else (False, "")
    mode = automatic_mode(sha, {"candidate": sha, "lock_held": held} if held else None)
    if mode["mode"] == "FOLLOW":
        seen = _read_json(path)[0] if os.path.exists(path) else None
        at = next_phase(seen) if seen and seen.get("candidate_sha") == sha else None
        say("FOLLOW: another driver holds %s (%s) for %s %s, at the %s phase; this request starts nothing and its "
            "answer comes from that run" % (lock, holder, version, sha[:12], at or "first"))
        return 2
    why = _take_lock(lock)
    if why:
        say("NO-DATA: " + why)
        return 2
    try:
        inputs = CR.inputs_identity(ROOT, sha, version)
        existed = os.path.exists(path)
        state, why = load_state(path, sha, tree, clock())
        mode = automatic_mode(sha, {"candidate": state["candidate_sha"], "lock_held": False} if state else None)
        if mode["mode"] != "RESUME":
            why, state = (mode["reason"] if state else why), None
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
            state = new_state(version, sha, tree, release_branch, clock(), inputs)
        state["mode"] = mode["mode"]
        if not [n for n in PHASES if reuse_miss(state["phases"].get(n), inputs, state.get("inputs"), clock)]:
            say("already complete: all %d phases PASS or REUSED for %s %s" % (len(PHASES), version, sha[:12]))
            return 0

        def moved(name):
            """CR1.b: the branch invariant before `name`; the refusal when it is violated."""
            why = branch_invariant(root, state["release_branch"], state["candidate_sha"],
                                   _verdict(state, "after") in PASSING, runner)
            return ("NO-DATA", "the release branch moved: " + why) if why else None

        def before(name):
            refused = moved(name)
            if refused:
                return refused
            lost = power_refusal(power)
            return ("NO-DATA", lost) if lost else None

        def after(name, verdict, detail, started, ended):
            head, _ = _git(root, ["rev-parse", "HEAD"], runner)
            note = _any_note(started, ended, sleeps)
            if note and name in SLEEP_SENSITIVE:
                detail = "%s; the phase said %s (%s); rerun it" % (note, verdict, detail)
                verdict = "NO-DATA"
            elif note:
                detail = "%s; %s" % (detail, note)
            return verdict, detail, {"head_after": head}

        def then(name):
            """After the cut, whatever it exited with: a moved branch records NO-DATA on the after phase."""
            if name != "cut":
                return False
            refused = moved("after")
            if not refused:
                return False
            state["phases"]["after"] = {"verdict": "NO-DATA", "started": clock(), "seconds": 0, "detail": refused[1],
                                        "record": "", "head_after": ""}
            save_state(path, state)
            say(phase_line("after", "NO-DATA", 0, refused[1]))
            return True

        began = power_refusal(power)
        if began:
            say(began)
            return 2
        table = real_phases(state, root, version, body, runner, tty, hub_remote, timeout_s, directory, approval)
        results, stopped = _walk(state, path, table, inputs, True, clock, say, before, after, then)
        if stopped:
            return 2
        last = results[-1][1][0] if results else "PASS"
        return EXIT_FOR.get(last, 2) if last not in PASSING else 0
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
                                             "The owner types the y, or gives his app line (--approval); "
                                             "this tool never makes either.", allow_abbrev=False)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("run", "resume"):
        p = sub.add_parser(name, allow_abbrev=False)
        p.add_argument("--version", required=True)
        p.add_argument("--body", required=True, help="the release notes file the cut publishes")
        p.add_argument("--candidate", default="")
        p.add_argument("--release-branch", default="")
        p.add_argument("--hub-remote", default="hub")
        p.add_argument("--timeout", type=int, default=CR.DEFAULT_TIMEOUT_S)
        p.add_argument("--approval", default="", metavar="LINE",
                       help="the owner's app line from brother-approve verify, passed to cut.py --approval")
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
               require_state=args.command == "resume", approval=args.approval)


if __name__ == "__main__":
    sys.exit(main())
