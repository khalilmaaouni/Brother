#!/usr/bin/env python3
"""cut_rehearsal: the cut rehearsal command whose verdict the real cut reads (CV1.b).

`python3 scripts/cut_rehearsal.py --version 1.1.0 [--candidate SHA]` makes a detached worktree of the candidate commit
under the scratch root, runs `scripts/cut.py --check --version V` inside it, keeps the full output as a log, removes only
the worktree it made and writes one record into the cut evidence folder. cut.py's `cut_gates` then reads that record
through `rehearsal_refusal` before it claims the fence: a real cut runs only on a commit whose rehearsal said READY.

Every unknown, corrupt or missing input refuses or is NO-DATA, never READY. This module never imports `cut` at module
level (cut imports this one); commands run through cut's own `_run`, imported lazily, so there is one process seam.

Python 3.9, standard library only. No network.
"""
import argparse
import calendar
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SCHEMA = "cv1.rehearsal.2"
# CS12.a: a record that names the rehearsed tip (bumped_sha, bumped_tree) is written under SCHEMA_BUMPED; a record
# without them (a worktree whose HEAD could not be read) keeps SCHEMA, the shape every older reader and test knows
# (named .2b, not .3: test_cut_rehearsal.py keeps .3 as its example of a schema no reader knows)
SCHEMA_BUMPED = "cv1.rehearsal.2b"
# RC1.a: a .1 record (no rows) still reads, as rows == []; writing is SCHEMA or SCHEMA_BUMPED
SCHEMAS_READ = ("cv1.rehearsal.1", "cv1.rehearsal.2", "cv1.rehearsal.2b")
MAX_AGE_HOURS = 24.0
FUTURE_SLACK_S = 300
DEFAULT_TIMEOUT_S = 10800
VERDICTS = ("READY", "NOT-READY", "NO-DATA")
EXIT_FOR = {"READY": 0, "NOT-READY": 1, "NO-DATA": 2, "LOCAL-PASS": 0}

# FR1.g (C8): the local half of a rehearsal (no export rows, no Linux canary) writes its own kind of record under its
# own prefix. Nothing that decides a cut reads it: _load_records globs rehearsal-<version>-*.json, which
# local-rehearsal-<version>-*.json does not match, and its schema is not in SCHEMAS_READ. Only row_sources reads it,
# for the rows a full check may reuse.
LOCAL_SCHEMA = "cv1.local.1"
LOCAL_VERDICTS = ("LOCAL-PASS", "NOT-READY", "NO-DATA")
LOCAL_PREFIX = "local-rehearsal-"

# products/brothermode/tools holds bm_store.py, which cut.py loads by path for the fence: a changed fence module
# after a READY record is a changed cut.
SCRIPT_DIRS = ("scripts", "products/brothermode/scripts", "products/brothersbe/scripts",
               "products/brothermode/tools", "products/brothersbe/tools")

VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SHA_RE = re.compile(r"[0-9a-f]{40}")
RECORD_FIELDS = ("schema", "version", "candidate_sha", "candidate_tree", "exit_code", "verdict", "log_path",
                 "log_sha256", "cut_py_sha256", "script_trees", "seconds", "finished_at")

# RC1.a: one row is one expensive check the rehearsed chain ran, keyed by the git tree id of the bytes it measured.
# The child prints each as ROW_PREFIX + JSON; the parent keeps a row only while its log hashes to log_sha256.
ROW_PREFIX = "CUTROW "
ROW_NAMES = ("required-fast-hub", "export-real-home", "virgin-gate")
ROW_HOMES = ("real", "empty")
ROW_VERDICTS = ("PASS", "FAIL", "NO-DATA")
ROW_FIELDS = ("row", "argv", "key", "home", "verdict", "seconds", "log_path", "log_sha256")
# CS12.c: a row a rehearsal REUSED is carried into its record with the record it came from and that record's stamp,
# so the next reader ages the row by when it was measured, never by when it was carried
CARRIED_FIELDS = ("origin_record", "origin_stamp")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
EXPORT_TREE_RE = re.compile(r"^EXPORT-TREE: ([0-9a-f]{40})$", re.M)   # the one line cut_preflight and export_public print

# CUT1.b: evidence is keyed by everything that decides its verdict: the candidate (a commit, or a row's export tree
# id), the checker (the cut's own judging scripts as on disk), the policy (what must pass), the interpreter and the
# environment. evidence_key is the ONE key function: the driver's phase entries and the rehearsal rows carry it.
INPUT_FIELDS = ("candidate", "checker", "policy", "interpreter", "environment")
CHECKER_FILES = ("scripts/cut.py", "scripts/cut_driver.py", "scripts/cut_ready.py", "scripts/cut_preflight.py",
                 "scripts/cut_rehearsal.py", "scripts/export_public.py")
POLICY_FILES = ("scripts/required_fast.sh", "docs/plan/APPROVER-KEY.json", "docs/plan/BROTHER-%s-WBS.json")
ENV_VARS = ("HOME", "BROTHER_CUT_TEST", "BROTHER_CUT_EVIDENCE_DIR", "BROTHER_SCRATCH")
ROW_INPUTS_FIELD = "inputs_key"   # optional on a row: a row written before CUT1 has none and keeps its tree key rule


def evidence_key(inputs):
    """The sha256 of the named inputs, or None when they are unknown: not a dict, no candidate, a field outside
    INPUT_FIELDS, or a value that is not non empty text. None is never a key, so it never matches anything."""
    if not isinstance(inputs, dict) or not inputs.get("candidate") or set(inputs) - set(INPUT_FIELDS):
        return None
    if not all(_is_str(v) and v for v in inputs.values()):
        return None
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest()


def input_changes(recorded, current):
    """The names of the inputs that differ between two identities (present on one side only counts)."""
    a = recorded if isinstance(recorded, dict) else {}
    b = current if isinstance(current, dict) else {}
    return [f for f in INPUT_FIELDS if a.get(f) != b.get(f)]


def _files_digest(root, rels):
    """sha256 over each file's path and bytes; a missing or unreadable file contributes its name and 'absent'."""
    h = hashlib.sha256()
    for rel in rels:
        h.update(rel.encode("utf-8") + b"\0")
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                h.update(hashlib.sha256(fh.read()).hexdigest().encode("ascii"))
        except OSError:
            h.update(b"absent")
    return h.hexdigest()


def environment_identity(env=None):
    """The host and the variables that redirect where a cut reads or writes. PATH and the like are left out on
    purpose: a terminal and an app session differ there and judge the same tree the same way."""
    import platform
    env = os.environ if env is None else env
    return json.dumps({"host": "%s %s" % (platform.system(), platform.machine()),
                       "vars": dict((k, env.get(k, "")) for k in ENV_VARS)}, sort_keys=True)


def interpreter_identity():
    return "%s %s" % (sys.executable, _interpreter())


def inputs_identity(root, candidate, version, env=None):
    """The driver's inputs for one candidate commit, every field filled, so a 40 hex candidate always has a key."""
    return {"candidate": candidate,
            "checker": _files_digest(root, CHECKER_FILES),
            "policy": _files_digest(root, [p % version if "%s" in p else p for p in POLICY_FILES]),
            "interpreter": interpreter_identity(), "environment": environment_identity(env)}


def row_inputs_key(export_tree_id, env=None):
    """The key a rehearsal row carries: its export tree id is its candidate (a cut's bump commits move the commit,
    not the export), with the interpreter and the environment beside it."""
    return evidence_key({"candidate": export_tree_id, "interpreter": interpreter_identity(),
                         "environment": environment_identity(env)})


class RehearsalRefused(RuntimeError):
    """A rehearsal that cannot run or cannot be recorded; main prints it and exits 2."""


def evidence_dir():
    """~/.claude/evidence/cut. BROTHER_CUT_EVIDENCE_DIR is honoured ONLY when BROTHER_CUT_TEST=1 is also set (the
    fixture seam); cut_gates refuses a real cut outright when either variable is present, so the real cut always
    reads the fixed location."""
    over = os.environ.get("BROTHER_CUT_EVIDENCE_DIR", "")
    if over and os.environ.get("BROTHER_CUT_TEST") == "1":
        return os.path.abspath(over)
    return os.path.join(os.path.expanduser("~"), ".claude", "evidence", "cut")


def scratch_root():
    """BROTHER_SCRATCH when set and non empty, else ~/.claude/brother-scratch. Never the system temp folder."""
    over = os.environ.get("BROTHER_SCRATCH", "")
    if over:
        return os.path.abspath(over)
    return os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")


def _is_str(value):
    return isinstance(value, str)


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _parse_iso(text):
    """Epoch seconds of a `%Y-%m-%dT%H:%M:%SZ` stamp, or None."""
    if not _is_str(text):
        return None
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        return None


GIT_TIMEOUT_S = 300   # every git call is bounded (review 2026-10-04: a git call hung on a held index lock kept the lock alive)


def _descendant_groups(pid):
    """The process groups of every live descendant of pid, from one ps listing, never this process's own group. Read
    BEFORE the child is killed, while the tree is whole (land_batch.descendant_groups, 2026-10-03). Unreadable: none."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):   # sbe: allow-silent no listing kills only the child's own group
        return set()
    kids, pgid = {}, {}
    for line in out.splitlines():
        f = line.split()
        if len(f) == 3 and all(x.isdigit() for x in f):
            kids.setdefault(int(f[1]), []).append(int(f[0])); pgid[int(f[0])] = int(f[2])
    seen, todo = set(), list(kids.get(pid, []))
    while todo:
        c = todo.pop()
        if c not in seen:
            seen.add(c); todo.extend(kids.get(c, []))
    return {pgid[c] for c in seen if c in pgid} - {os.getpgrp()}


def _run_tree(cmd, cwd, env, timeout):
    """The rehearsed cut's child in its own session (review 2026-10-04): on a timeout the child's group AND every nested
    group are killed, so cut.py --check's suites never run on orphaned while the worktree is removed under them, and the
    output printed before the kill is kept (to files, never pipes a leftover could hold open). 124 timed out, 127 the
    child could not start; never raises."""
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as fo, \
            tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as fe:
        try:
            p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=fo, stderr=fe, stdin=subprocess.DEVNULL, text=True,
                                 start_new_session=True)
        except OSError as exc:
            return subprocess.CompletedProcess(cmd, 127, "", "%s" % exc)
        note = ""
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            for g in {p.pid} | _descendant_groups(p.pid):
                try:
                    os.killpg(g, signal.SIGKILL)
                except OSError:   # sbe: allow-silent a group already gone is the state this kill wants
                    pass
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:   # sbe: allow-silent SIGKILLed above; rc is 124 whatever this wait says
                pass
            rc, note = 124, "\nNO-DATA: timed out after %ss; the child and its descendants were killed\n" % timeout
        finally:
            # a background child the check left in its own group never outlives it either (review round 10, finding 1)
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:   # sbe: allow-silent the group is already gone, which is the state this wants
                pass
        fo.seek(0); fe.seek(0)
        return subprocess.CompletedProcess(cmd, rc, fo.read(), fe.read() + note)


def _sh(cmd, cwd, runner=None, env=None, timeout=None):
    """One command through cut._run (never raises: 127 missing binary, 124 timeout). No timeout given: GIT_TIMEOUT_S."""
    timeout = GIT_TIMEOUT_S if timeout is None else timeout
    import cut as C
    return C._run(cmd, cwd, runner, timeout=timeout, env=env)


def _stdout(proc):
    """The stdout of a process result, '' when absent or not text."""
    try:
        value = proc.stdout
    except AttributeError:
        return ""
    return value if _is_str(value) else ""


def _stderr(proc):
    try:
        value = proc.stderr
    except AttributeError:
        return ""
    return value if _is_str(value) else ""


def _out(proc):
    return _stdout(proc).strip()


def _rc(proc):
    try:
        code = proc.returncode
    except AttributeError:
        return 99
    return code if isinstance(code, int) and not isinstance(code, bool) else 99


def _git(root, args, runner=None):
    """(stdout stripped, '') on exit 0, else ('', the failure in words)."""
    proc = _sh(["git"] + list(args), root, runner)
    if _rc(proc) != 0:
        return "", "git %s exited %s: %s" % (" ".join(args[:3]), _rc(proc), _stderr(proc).strip()[:200])
    return _out(proc), ""


def _sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def running_cut_py_sha256():
    """sha256 of the scripts/cut.py beside this module, the one that is running now."""
    return _sha256_file(os.path.join(HERE, "cut.py"))


def _script_trees(root, ref, runner=None):
    """({dir: tree hash}, '') for every SCRIPT_DIRS entry at `ref`, or ({}, the first failure)."""
    trees = {}
    for rel in SCRIPT_DIRS:
        out, why = _git(root, ["rev-parse", "%s:%s" % (ref, rel)], runner)
        if why or not SHA_RE.fullmatch(out):
            return {}, "the tree of %s at %s cannot be read%s" % (rel, ref[:12], (": " + why) if why else "")
        trees[rel] = out
    return trees, ""


REHEARSED_REF = "refs/brother/rehearsed/%s-%s"
#: CS12: the one line cut.py --check prints, after its exit rule accepted the chain's commits, naming the tip and the tree
#: of the nested worktree the chain ran in (the rehearsal worktree itself never moves, so its HEAD is never the tip)
TIP_PREFIX = "REHEARSED-TIP: "
TIP_LINE_RE = re.compile(r"^%s([0-9a-f]{40}) ([0-9a-f]{40})$" % re.escape(TIP_PREFIX), re.M)
CUT_SUBJECTS = ("%s: the version bump and the regenerated manifests",
                "%s: the export manifest that describes the tree and the note it ships")


def rehearsed_ref_name(version, candidate):
    """The ref a rehearsal leaves at its bumped tip: refs/brother/rehearsed/<version>-<first 12 of the candidate>."""
    return REHEARSED_REF % (version, candidate[:12])


def write_rehearsed_ref(root, version, candidate, sha, runner=None):
    """(True, '') when the rehearsed ref now points at `sha`, else (False, why). CS12.a: the ref keeps the
    rehearsed commits reachable, and the cut driver adopts exactly the commit it names. Never deleted here."""
    if not _is_str(version) or not VERSION_RE.fullmatch(version):
        return False, "version %r is not X.Y.Z" % (version,)
    if not _is_str(candidate) or not SHA_RE.fullmatch(candidate):
        return False, "candidate %r is not 40 hex" % (candidate,)
    if not _is_str(sha) or not SHA_RE.fullmatch(sha):
        return False, "the bumped tip %r is not 40 hex" % (sha,)
    ref = rehearsed_ref_name(version, candidate)
    _out, why = _git(root, ["update-ref", ref, sha], runner=runner)
    if why:
        return False, "git update-ref %s failed: %s" % (ref, why)
    return True, ""


def _tip_from_output(root, version, candidate, output, runner=None):
    """(tip, tree, '') from the REHEARSED-TIP line of the child's output (the last one), accepted only when the tip is
    one or two commits on top of `candidate`, carrying the cut's own subjects, and its tree is the one the line names;
    else ('', '', why). The commits stay in the object store after the child removed its worktree."""
    lines = TIP_LINE_RE.findall(output if _is_str(output) else "")
    if not lines:
        return "", "", "the child printed no %s line, so the rehearsed tip is unknown" % TIP_PREFIX.strip()
    tip, tree = lines[-1]
    if _rc(_sh(["git", "merge-base", "--is-ancestor", candidate, tip], root, runner)) != 0:
        return "", "", "the rehearsed tip %s is not on top of the candidate %s" % (tip[:12], candidate[:12])
    count, why = _git(root, ["rev-list", "--count", "%s..%s" % (candidate, tip)], runner)
    if why or count not in ("1", "2"):
        return "", "", "the rehearsed tip %s is %s commits past the candidate, not the cut's one or two" % (
            tip[:12], count or why)
    subjects, why = _git(root, ["log", "--format=%s", "%s..%s" % (candidate, tip)], runner)
    allowed = [t % version for t in CUT_SUBJECTS]
    if why or not subjects or any(line not in allowed for line in subjects.splitlines()):
        return "", "", "a commit under the rehearsed tip %s is not the cut's own: %s" % (tip[:12], why or subjects[:120])
    actual, why = _git(root, ["rev-parse", tip + "^{tree}"], runner)
    if why or actual != tree:
        return "", "", "the rehearsed tip %s has tree %s, not the %s the child named" % (tip[:12], actual[:12], tree[:12])
    return tip, tree, ""


def validate_row(row):
    """'' when `row` is a whole row (exactly ROW_FIELDS, each of its type), else the first broken rule. Never raises."""
    if not isinstance(row, dict):
        return "a row is not an object"
    extra = set(row) - set(ROW_FIELDS) - set(CARRIED_FIELDS) - {ROW_INPUTS_FIELD}
    if extra or not set(ROW_FIELDS) <= set(row):
        return "a row has the fields %s, not %s" % (", ".join(sorted(str(k) for k in row)), ", ".join(ROW_FIELDS))
    for field in CARRIED_FIELDS:
        if field in row and (not _is_str(row[field]) or not row[field]):
            return "the %s of row %s is not a non empty string" % (field, row.get("row"))
    if ROW_INPUTS_FIELD in row and (not _is_str(row[ROW_INPUTS_FIELD]) or not HEX64_RE.fullmatch(row[ROW_INPUTS_FIELD])):
        return "the %s of row %s is not 64 hex" % (ROW_INPUTS_FIELD, row.get("row"))
    if not _is_str(row["row"]) or row["row"] not in ROW_NAMES:
        return "row %r is not one of %s" % (row["row"], ", ".join(ROW_NAMES))
    if not isinstance(row["argv"], list) or not row["argv"] or not all(_is_str(a) for a in row["argv"]):
        return "the argv of row %s is not a list of strings" % row["row"]
    if not _is_str(row["key"]) or not SHA_RE.fullmatch(row["key"]):
        return "the key of row %s is not a 40 hex tree id" % row["row"]
    if not _is_str(row["home"]) or row["home"] not in ROW_HOMES:
        return "the home of row %s is not one of %s" % (row["row"], ", ".join(ROW_HOMES))
    if not _is_str(row["verdict"]) or row["verdict"] not in ROW_VERDICTS:
        return "the verdict of row %s is not one of %s" % (row["row"], ", ".join(ROW_VERDICTS))
    if not _is_num(row["seconds"]) or row["seconds"] < 0:
        return "the seconds of row %s are not a finite number at least 0" % row["row"]
    if not _is_str(row["log_path"]) or not row["log_path"]:
        return "the log path of row %s is not a path" % row["row"]
    if not _is_str(row["log_sha256"]) or not HEX64_RE.fullmatch(row["log_sha256"]):
        return "the log sha256 of row %s is not 64 hex" % row["row"]
    return ""


def carry_reused_rows(rows, origin_record, origin_stamp):
    """A fresh list of copies of `rows`, each carrying `origin_record` and `origin_stamp` (CS12.c), the last of two rows
    with one (row, home) kept. A row that already carries both keeps them: its first measurement is its origin, not the
    record it passed through last. ValueError on rows that are not a list of whole rows, or an empty or non text
    origin: nothing is dropped silently."""
    if isinstance(rows, (str, bytes, dict)) or not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a list of rows")
    for what, value in (("origin_record", origin_record), ("origin_stamp", origin_stamp)):
        if not _is_str(value) or not value:
            raise ValueError("%s %r is not a non empty string" % (what, value))
    kept = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("a carried row is not an object: %r" % (row,))
        copy = dict(row)
        if not all(_is_str(copy.get(f)) and copy[f] for f in CARRIED_FIELDS):
            copy["origin_record"] = origin_record
            copy["origin_stamp"] = origin_stamp
        why = validate_row(copy)
        if why:
            raise ValueError("a carried row was refused: %s" % why)
        kept.pop((copy["row"], copy["home"]), None)
        kept[(copy["row"], copy["home"])] = copy
    return list(kept.values())


def row_line(row):
    """The one stdout line a rehearsed chain prints for a row."""
    return ROW_PREFIX + json.dumps(row, sort_keys=True)


def parse_rows(output):
    """([valid rows in order], [problems]) from every line starting with ROW_PREFIX. Never raises."""
    if not _is_str(output):
        return [], ["the output is not text"]
    rows, problems = [], []
    for line in output.splitlines():
        if not line.startswith(ROW_PREFIX):
            continue
        try:
            row = json.loads(line[len(ROW_PREFIX):])
        except ValueError as exc:
            problems.append("a CUTROW line is not JSON (%s)" % exc)
            continue
        why = validate_row(row)
        if not why and isinstance(row, dict) and any(f in row for f in CARRIED_FIELDS):
            why = "a row the chain just ran never carries an origin"
        if why:
            problems.append("a CUTROW line was refused: %s" % why)
        else:
            rows.append(row)
    return rows, problems


def record_rehearsal(version, candidate, tree, exit_code, verdict, log_path, seconds, cut_py_sha256, directory="",
                     now=None, script_trees=None, note="", rows=(), bumped_sha="", bumped_tree="", carried=()):
    """Write the record atomically (temp file then os.replace) and return its path. Raises ValueError on a
    candidate that is not 40 hex or a verdict outside READY, NOT-READY, NO-DATA, and on any other malformed field,
    a malformed row included. A READY record needs a readable log and a tree hash for every SCRIPT_DIRS entry.
    CS12.a: `bumped_sha` and `bumped_tree` (the rehearsed worktree's HEAD and its tree) come together, each 40 hex or
    both empty; a record that carries them is written under SCHEMA_BUMPED, one without keeps SCHEMA.
    CS12.c: `carried` are the rows this rehearsal REUSED, each a whole row with its origin_record and origin_stamp
    (carry_reused_rows makes them); they are written after the fresh rows, so for one (row, home) they win."""
    if isinstance(carried, (str, bytes, dict)) or not isinstance(carried, (list, tuple)):
        raise ValueError("carried must be a list of rows")
    for row in carried:
        why = validate_row(row)
        if why:
            raise ValueError(why)
        if not all(row.get(f) for f in CARRIED_FIELDS):
            raise ValueError("a carried row needs its origin_record and origin_stamp")
    for what, value in (("bumped_sha", bumped_sha), ("bumped_tree", bumped_tree)):
        if not _is_str(value) or (value and not SHA_RE.fullmatch(value)):
            raise ValueError("%s %r is not 40 hex or empty" % (what, value))
    if bool(bumped_sha) != bool(bumped_tree):
        raise ValueError("bumped_sha and bumped_tree come together or not at all")
    if isinstance(rows, (str, bytes, dict)) or not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a list of rows")
    for row in rows:
        why = validate_row(row)
        if why:
            raise ValueError(why)
    if not _is_str(version) or not VERSION_RE.fullmatch(version):
        raise ValueError("version %r is not X.Y.Z" % (version,))
    if not _is_str(candidate) or not SHA_RE.fullmatch(candidate):
        raise ValueError("candidate %r is not 40 hex" % (candidate,))
    if not _is_str(tree) or not SHA_RE.fullmatch(tree):
        raise ValueError("candidate tree %r is not 40 hex" % (tree,))
    if verdict not in VERDICTS or not _is_str(verdict):
        raise ValueError("verdict %r is not one of %s" % (verdict, ", ".join(VERDICTS)))
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise ValueError("exit_code %r is not an int" % (exit_code,))
    if not _is_num(seconds) or seconds < 0:
        raise ValueError("seconds %r is not a finite number" % (seconds,))
    if not _is_str(cut_py_sha256) or not re.fullmatch(r"[0-9a-f]{64}", cut_py_sha256):
        raise ValueError("cut_py_sha256 %r is not 64 hex" % (cut_py_sha256,))
    if not _is_str(log_path) or not _is_str(directory) or not _is_str(note):
        raise ValueError("log_path, directory and note must be strings")
    if now is not None and not _is_num(now):
        raise ValueError("now %r is not a finite number" % (now,))
    trees = {} if script_trees is None else script_trees
    if not isinstance(trees, dict) or not all(_is_str(k) and _is_str(v) for k, v in trees.items()):
        raise ValueError("script_trees must map directory names to tree hashes")
    if verdict == "READY" and not all(SHA_RE.fullmatch(trees.get(d, "")) for d in SCRIPT_DIRS):
        raise ValueError("a READY record needs a tree hash for every one of %s" % ", ".join(SCRIPT_DIRS))
    log_sha = ""
    if log_path:
        try:
            log_sha = _sha256_file(log_path)
        except OSError as exc:
            if verdict == "READY":
                raise ValueError("a READY record needs a readable log: %s" % exc)
            log_path = ""
    elif verdict == "READY":
        raise ValueError("a READY record needs a log")
    stamp = time.time() if now is None else float(now)
    directory = directory or evidence_dir()
    os.makedirs(directory, exist_ok=True)
    record = {"schema": SCHEMA, "version": version, "candidate_sha": candidate, "candidate_tree": tree,
              "exit_code": exit_code, "verdict": verdict, "log_path": log_path, "log_sha256": log_sha,
              "cut_py_sha256": cut_py_sha256, "script_trees": trees, "seconds": float(seconds),
              "finished_at": _iso(stamp), "rows": [dict(r) for r in rows] + [dict(r) for r in carried]}
    if bumped_sha:
        record.update({"schema": SCHEMA_BUMPED, "bumped_sha": bumped_sha, "bumped_tree": bumped_tree})
    if note:
        record["note"] = note
    path = os.path.join(directory, "rehearsal-%s-%s-%d.json" % (version, candidate[:12], int(stamp)))
    tmp = "%s.tmp-%d-%s" % (path, os.getpid(), uuid.uuid4().hex[:6])
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def record_local(version, candidate, tree, exit_code, verdict, log_path, seconds, directory="", now=None, rows=(),
                 note=""):
    """Write a LOCAL record atomically under LOCAL_PREFIX and return its path (FR1.g). The fields are
    record_rehearsal's, the schema is LOCAL_SCHEMA and the verdict one of LOCAL_VERDICTS. Raises ValueError on a
    verdict outside LOCAL_VERDICTS and on any other malformed field, a malformed row included. A LOCAL-PASS needs a
    readable log. Nothing that decides a cut reads this file."""
    if isinstance(rows, (str, bytes, dict)) or not isinstance(rows, (list, tuple)):
        raise ValueError("rows must be a list of rows")
    for row in rows:
        why = validate_row(row)
        if why:
            raise ValueError(why)
    if not _is_str(version) or not VERSION_RE.fullmatch(version):
        raise ValueError("version %r is not X.Y.Z" % (version,))
    for what, value in (("candidate", candidate), ("candidate tree", tree)):
        if not _is_str(value) or not SHA_RE.fullmatch(value):
            raise ValueError("%s %r is not 40 hex" % (what, value))
    if not _is_str(verdict) or verdict not in LOCAL_VERDICTS:
        raise ValueError("verdict %r is not one of %s" % (verdict, ", ".join(LOCAL_VERDICTS)))
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise ValueError("exit_code %r is not an int" % (exit_code,))
    if not _is_num(seconds) or seconds < 0:
        raise ValueError("seconds %r is not a finite number" % (seconds,))
    if not _is_str(log_path) or not _is_str(directory) or not _is_str(note):
        raise ValueError("log_path, directory and note must be strings")
    if now is not None and not _is_num(now):
        raise ValueError("now %r is not a finite number" % (now,))
    log_sha = ""
    if log_path:
        try:
            log_sha = _sha256_file(log_path)
        except OSError as exc:
            if verdict == "LOCAL-PASS":
                raise ValueError("a LOCAL-PASS record needs a readable log: %s" % exc)
            log_path = ""
    elif verdict == "LOCAL-PASS":
        raise ValueError("a LOCAL-PASS record needs a log")
    stamp = time.time() if now is None else float(now)
    directory = directory or evidence_dir()
    os.makedirs(directory, exist_ok=True)
    record = {"schema": LOCAL_SCHEMA, "version": version, "candidate_sha": candidate, "candidate_tree": tree,
              "exit_code": exit_code, "verdict": verdict, "log_path": log_path, "log_sha256": log_sha,
              "cut_py_sha256": running_cut_py_sha256(), "script_trees": {}, "seconds": float(seconds),
              "finished_at": _iso(stamp), "rows": [dict(r) for r in rows]}
    if note:
        record["note"] = note
    path = os.path.join(directory, "%s%s-%s-%d.json" % (LOCAL_PREFIX, version, candidate[:12], int(stamp)))
    tmp = "%s.tmp-%d-%s" % (path, os.getpid(), uuid.uuid4().hex[:6])
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def _inside(path, folder):
    """True when `path` is strictly under `folder` once both are resolved."""
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path != folder and path.startswith(folder.rstrip(os.sep) + os.sep)


def _remove_worktree(root, path, scratch, runner=None):
    """Remove the worktree this run made, and only a path inside the scratch root. True when removed."""
    if not _is_str(path) or not path or not _is_str(scratch) or not _inside(path, scratch):
        return False
    proc = _sh(["git", "worktree", "remove", "--force", path], root, runner)
    # a killed check can leave its own nested worktree registered with its folder gone: prune the registrations
    # (review round 10, finding 3); the verdict stays the removal's own exit code
    _sh(["git", "worktree", "prune"], root, runner)
    return _rc(proc) == 0


def _lock_alive(path):
    """(alive, why): whether the lock file holds a live pid. A lock that cannot be read as a pid is alive (refuses)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return True, "its content is not a pid"
    if pid <= 0:
        return True, "its content is not a pid"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, ""
    except PermissionError:
        return True, "pid %d is alive" % pid
    except OSError:
        return True, "pid %d cannot be checked" % pid
    return True, "pid %d is alive" % pid


def _take_lock(scratch):
    """Create the lock with O_EXCL holding our pid; return its path. A live lock refuses, a dead one is replaced."""
    os.makedirs(scratch, exist_ok=True)
    path = os.path.join(scratch, "cut-rehearsal.lock")
    for _attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            alive, why = _lock_alive(path)
            if alive:
                raise RehearsalRefused("another rehearsal holds %s (%s); one rehearsal at a time" % (path, why))
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write("%d\n" % os.getpid())
        return path
    raise RehearsalRefused("the rehearsal lock %s could not be taken" % path)


def _release_lock(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            mine = fh.read().strip() == str(os.getpid())
        if mine:
            os.remove(path)
    except OSError:
        pass


def _ready_line(output, version, word="READY"):
    """True when `output` has a line that begins `<word> for <version>`. A LOCAL-READY line (FR1.g) never matches the
    default word: the pattern is anchored at the line start."""
    return re.search(r"^%s for %s(?![\w.])" % (re.escape(word), re.escape(version)), output, re.MULTILINE) is not None


#: FC1.b: the absolute path, as scripts/night_tick.py spells it; -i -m -s are the assertions the session's wrapper
#: used (idle, disk, system sleep), and -w ties them to the rehearsal's own pid so they end with it, even on SIGKILL.
CAFFEINATE = "/usr/bin/caffeinate"


def awake_command(pid):
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("the pid to hold awake must be a positive int, got %r" % (pid,))
    return [CAFFEINATE, "-i", "-m", "-s", "-w", str(pid)]


def hold_awake(pid, popen=subprocess.Popen, exists=os.path.exists, settle_s=0.2):
    """(process, '') while caffeinate holds the machine awake for `pid`; (None, why) when it is missing, cannot
    start, or exits within settle_s. Never a hold that is not there."""
    cmd = awake_command(pid)
    if not exists(CAFFEINATE):
        return None, "%s does not exist on this host" % CAFFEINATE
    try:
        proc = popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        code = proc.wait(timeout=settle_s)
    except subprocess.TimeoutExpired:
        return proc, ""
    except (OSError, AttributeError) as exc:
        return None, "%s could not start: %s" % (CAFFEINATE, exc)
    return None, "%s exited at once (exit %s)" % (CAFFEINATE, code)


def release_awake(handle):
    """End the hold; never raises and never changes a verdict (-w ends it with the process anyway)."""
    if handle is None:
        return
    try:
        handle.terminate()
        try:
            handle.wait(timeout=5)
        except subprocess.TimeoutExpired:
            handle.kill()
    except OSError:
        pass


def _no_hold(pid):
    return None, ""


def _awake_for(runner, awake):
    """The injected hold when given; else the real one for a real run (no runner) and none for a scripted runner,
    the same rule run_rehearsal already uses for the child, so no test spawns caffeinate by accident."""
    if awake is not None:
        return awake
    return hold_awake if runner is None else _no_hold


def run_rehearsal(root, version, candidate="", directory="", timeout_s=DEFAULT_TIMEOUT_S, runner=None, clock=None,
                  awake=None, local=False):
    """(verdict, exit_code, record_path). Never raises on a child failure: a worktree that cannot be made, a child that
    times out or a log that cannot be written is NO-DATA with a record that says why, or a RehearsalRefused when even
    the record cannot be written or another rehearsal is running. A malformed argument raises ValueError.
    `local` (FR1.g): the child runs `cut.py --check --local` and the verdict is LOCAL-PASS, NOT-READY or NO-DATA,
    recorded by record_local under LOCAL_PREFIX."""
    if not isinstance(local, bool):
        raise ValueError("local must be a bool")
    if not _is_str(root) or not root or not _is_str(version) or not VERSION_RE.fullmatch(version):
        raise ValueError("run_rehearsal needs a root and an X.Y.Z version")
    if not _is_str(candidate) or not _is_str(directory):
        raise ValueError("candidate and directory must be strings")
    if not isinstance(timeout_s, int) or isinstance(timeout_s, bool) or timeout_s <= 0:
        raise ValueError("timeout_s must be a positive int")
    clock = clock or time.time
    sha, why = _git(root, ["rev-parse", "--verify", "%s^{commit}" % (candidate or "HEAD")], runner)
    if why or not SHA_RE.fullmatch(sha):
        raise RehearsalRefused("the candidate %r does not resolve to a commit: %s" % (candidate or "HEAD", why))
    tree, why = _git(root, ["rev-parse", "%s^{tree}" % sha], runner)
    if why or not SHA_RE.fullmatch(tree):
        raise RehearsalRefused("the tree of %s cannot be read: %s" % (sha[:12], why))
    directory = directory or evidence_dir()
    scratch = scratch_root()
    lock = _take_lock(scratch)
    # FC1.b (2026-10-11): the rehearsal holds the machine awake by itself, from its lock to its record. The 1.1.1
    # rehearsal stayed awake only under the session's caffeinate wrapper; a 1.1.0 one lost about 80 minutes to sleep.
    handle, why = _awake_for(runner, awake)(os.getpid())
    if why:
        _release_lock(lock)
        raise RehearsalRefused("the machine cannot be held awake for the rehearsal: %s" % why)
    try:
        return _rehearse_locked(root, version, sha, tree, directory, scratch, lock, timeout_s, runner, clock, local)
    finally:
        release_awake(handle)


REUSED_RE = re.compile(r"^REUSED (%s) ([0-9a-f]{12}) (\S.*?)\s*$" % "|".join(ROW_NAMES), re.M)


def reused_rows(output, version, directory):
    """([rows the chain REUSED, each carrying its origin], [problems]) for the REUSED lines in the chain's output
    (CS12.c). A line is matched to the record holding that row by its name, the first 12 of its key and the log (or
    record) path it names; a row that is already carried keeps its own first origin, any other takes the record it was
    read from and that record's finished_at (so the original and a carried copy give the same origin). A line no record holds, or a row whose log no longer hashes, is a
    problem and no row: the next cut then runs that row, as it always did. Never raises."""
    found = REUSED_RE.findall(output) if _is_str(output) else []
    if not found:
        return [], []
    records, problems = row_sources(version, directory)
    problems = list(problems)
    carried = {}
    for name, key12, where in found:
        hits = []
        for rec in records:
            record_path = os.path.join(directory, rec["_name"])
            for row in rec["rows"]:
                if row["row"] == name and row["key"][:12] == key12 and where in (row["log_path"], record_path):
                    hits.append((rec, row))
        if not hits:
            problems.append("the REUSED %s row (key %s) is held by no record, so it is not carried" % (name, key12))
            continue
        # any holder will do: a carried copy names the first origin and carry_reused_rows keeps it
        rec, row = hits[0]
        try:
            ok = _sha256_file(row["log_path"]) == row["log_sha256"]
        except OSError:
            ok = False
        if not ok:
            problems.append("the REUSED %s row was not carried: its log %s does not hash to its log_sha256"
                            % (name, row["log_path"]))
            continue
        try:
            copy = carry_reused_rows([row], os.path.join(directory, rec["_name"]), rec["finished_at"])[0]
        except ValueError as exc:
            problems.append("the REUSED %s row was not carried: %s" % (name, exc))
            continue
        carried[(copy["row"], copy["home"])] = copy
    return list(carried.values()), problems


def _rehearse_locked(root, version, sha, tree, directory, scratch, lock, timeout_s, runner, clock, local=False):
    """run_rehearsal from the lock to the record, while the machine is held awake."""
    started = clock()
    worktree = ""
    made = False
    note = ""
    output = ""
    code = 99
    try:
        worktree = os.path.join(scratch, "rehearsal-wt", "%s-%s" % (sha[:12], uuid.uuid4().hex[:8]))
        tmpdir = os.path.join(scratch, "rehearsal-tmp")
        os.makedirs(os.path.dirname(worktree), exist_ok=True)
        os.makedirs(tmpdir, exist_ok=True)
        add = _sh(["git", "worktree", "add", "--detach", worktree, sha], root, runner)
        if _rc(add) != 0:
            note = "git worktree add failed (exit %s): %s" % (_rc(add), _stderr(add).strip()[:300])
            output = note + "\n"
        else:
            made = True
            env = dict(os.environ)
            env["TMPDIR"] = tmpdir
            child = [sys.executable, os.path.join(worktree, "scripts", "cut.py"), "--check", "--version", version]
            if local:
                child.append("--local")
            # an injected runner (the tests) keeps cut._run's shape; the real run kills its whole tree on a timeout
            proc = _sh(child, worktree, runner, env=env, timeout=timeout_s) if runner else _run_tree(child, worktree, env, timeout_s)
            code = _rc(proc)
            output = _stdout(proc) + _stderr(proc)
            if code == 124:
                note = "the child timed out after %d s" % timeout_s
    finally:
        if made and not _remove_worktree(root, worktree, scratch, runner):
            note = (note + "; " if note else "") + "the worktree %s could not be removed" % worktree
        _release_lock(lock)
    word, line = ("LOCAL-PASS", "LOCAL-READY") if local else ("READY", "READY")
    if code == 0 and _ready_line(output, version, line):
        verdict = word
    elif code == 1:
        verdict = "NOT-READY"
    else:
        verdict = "NO-DATA"
        note = note or ("the child exited %s" % code if code != 0
                        else "the child exited 0 without the line '%s for %s'" % (line, version))
    finished = clock()
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise RehearsalRefused("the evidence folder %s cannot be made: %s" % (directory, exc))
    log_path = os.path.join(directory, "%s%s-%s-%d.log" % (LOCAL_PREFIX if local else "rehearsal-", version, sha[:12],
                                                           int(finished)))
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(output)
    except OSError as exc:
        verdict, log_path = "NO-DATA", ""
        note = (note + "; " if note else "") + "the log could not be written: %s" % exc
    rows = []
    kept, problems = parse_rows(output)
    for row in kept:
        try:
            ok = _sha256_file(row["log_path"]) == row["log_sha256"]
        except OSError:
            ok = False
        if ok:
            rows.append(row)
        else:
            problems.append("row %s was dropped: its log %s does not hash to its log_sha256" % (row["row"], row["log_path"]))
    if problems:
        note = (note + "; " if note else "") + "; ".join(problems)
    if local:
        # a local record never covers a cut, so it carries no script trees and no cut.py hash to be checked later
        try:
            path = record_local(version, sha, tree, code, verdict, log_path, max(finished - started, 0.0), directory,
                                now=finished, rows=rows, note=note)
        except (OSError, ValueError) as exc:
            raise RehearsalRefused("the local record could not be written: %s" % exc)
        return verdict, code, path
    bumped_sha = bumped_tree = ""
    if verdict == "READY":
        # CS12: the chain ran in a nested worktree of the child's own, so the tip comes from the line the child
        # printed, checked here against the candidate; a missing or bad line leaves the older record and no ref
        bumped_sha, bumped_tree, why = _tip_from_output(root, version, sha, output, runner)
        if why:
            note = (note + "; " if note else "") + why
    carried_rows, problems = reused_rows(output, version, directory)
    if problems:
        note = (note + "; " if note else "") + "; ".join(problems)
    trees, why = _script_trees(root, sha, runner)
    cut_sha = ""
    shown = _sh(["git", "show", "%s:scripts/cut.py" % sha], root, runner)
    cut_text = _stdout(shown)
    if _rc(shown) != 0 or not cut_text:
        why = why or "scripts/cut.py cannot be read at the candidate (git show exit %s)" % _rc(shown)
    else:
        cut_sha = hashlib.sha256(cut_text.encode("utf-8")).hexdigest()
    if why or not cut_sha:
        verdict = "NO-DATA"
        note = (note + "; " if note else "") + why
        cut_sha = cut_sha or "0" * 64
    # CS12.a: the ref moves only for a rehearsal that ends READY, so a failed second rehearsal never takes it away
    # from the tip an earlier READY record names; a ref that cannot be written ends the rehearsal NOT-READY
    ref = rehearsed_ref_name(version, sha)
    prior, moved = "", False
    if bumped_sha and verdict == "READY":
        prior, _why = _git(root, ["rev-parse", "--verify", "--quiet", ref], runner=runner)
        moved, why = write_rehearsed_ref(root, version, sha, bumped_sha, runner)
        if not moved:
            verdict = "NOT-READY"
            note = (note + "; " if note else "") + why
    try:
        path = record_rehearsal(version, sha, tree, code, verdict, log_path, max(finished - started, 0.0), cut_sha,
                                directory, now=finished, script_trees=trees, note=note, rows=rows,
                                bumped_sha=bumped_sha, bumped_tree=bumped_tree, carried=carried_rows)
    except (OSError, ValueError) as exc:
        if moved:   # no record names the new tip, so the ref goes back to where the last record left it
            _git(root, ["update-ref", "-d", ref] if not SHA_RE.fullmatch(prior) else ["update-ref", ref, prior],
                 runner=runner)
        raise RehearsalRefused("the record could not be written: %s" % exc)
    return verdict, code, path


#: The cut's own re-pin, scripts/cut_v1.0.0.sh step 1b: the one PUBLIC_INSTALL_TAG line of the facts module and the
#: pinned install lines of four pages. The facts module sits under SCRIPT_DIRS and all five sit outside the cut's
#: allowed paths, so until 2026-10-07 no real cut could reach its question: the first one stopped after a green
#: chain. Each file is accepted only when its content at HEAD is exactly what that step writes from its content at
#: the rehearsed commit; anything else under these paths still refuses.
REPIN_FACTS = "products/brothermode/tools/bm_project_facts.py"
REPIN_PAGES = tuple("products/brothermode/" + rel
                    for rel in ("README.md", "docs/QUICKSTART.md", "docs/SETUP.md", "docs/RELEASE.md"))
_PIN_RE = re.compile(r'^PUBLIC_INSTALL_TAG = "([^"]+)"$', re.M)
_PAGE_PINS = ("--branch %s ", "`--branch %s`", "currently `%s`")


def repin_expected(path, text, old, new):
    """What cut_v1.0.0.sh step 1b leaves in `path` when it moves the pin from `old` to `new`."""
    if path == REPIN_FACTS:
        found = _PIN_RE.search(text)
        return text if found is None else text[:found.start(1)] + new + text[found.end(1):]
    for pin in _PAGE_PINS:
        text = text.replace(pin % old, pin % new)
    return text


def _show(root, ref, path, runner=None):
    """(the file's text at ref, unstripped, '') or ('', why). GIT_NO_REPLACE_OBJECTS: the object the tree names is the
    one read, so a refs/replace entry cannot stand in for it. Text that does not decode is a why, never a raise."""
    try:
        proc = _sh(["git", "show", "%s:%s" % (ref, path)], root, runner,
                   env=dict(os.environ, GIT_NO_REPLACE_OBJECTS="1"))
    except (UnicodeError, ValueError) as exc:
        return "", "git show %s:%s printed text that does not decode (%s)" % (ref[:12], path, type(exc).__name__)
    if _rc(proc) != 0:
        return "", "git show %s:%s exited %s" % (ref[:12], path, _rc(proc))
    text = getattr(proc, "stdout", "")
    return (text, "") if _is_str(text) else ("", "git show %s:%s printed no text" % (ref[:12], path))


def _blob_id(text):
    """The object id git gives this text as a blob (sha1 object format, the one this repository uses)."""
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def repinned_paths(root, candidate, head, version, runner=None):
    """The re-pin files whose OBJECT at `head` is exactly step 1b's rewrite of their content at `candidate`, to
    v<version>, its file mode unchanged. A file that cannot be read, a pin already at the version, or pages moved
    while the facts line stayed verify nothing: the pages follow the constant, never the other way round."""
    before, why = _show(root, candidate, REPIN_FACTS, runner)
    found = None if why else _PIN_RE.search(before)
    if found is None or not _is_str(version) or not VERSION_RE.fullmatch(version):
        return frozenset()
    old, new = found.group(1), "v" + version
    if old == new:
        return frozenset()
    done = set()
    for path in (REPIN_FACTS,) + REPIN_PAGES:
        then, why = _show(root, candidate, path, runner)
        was_id, why2 = _git(root, ["rev-parse", "%s:%s" % (candidate, path)], runner)
        now_id, why3 = _git(root, ["rev-parse", "%s:%s" % (head, path)], runner)
        if why or why2 or why3 or was_id == now_id:
            continue
        modes = [_git(root, ["ls-tree", ref, "--", path], runner)[0].split(" ", 1)[0] for ref in (candidate, head)]
        # object against object: line endings, a trailing byte or a replaced object cannot pass as "the same text"
        if _blob_id(repin_expected(path, then, old, new)) == now_id and modes[0] and modes[0] == modes[1]:
            done.add(path)
    return frozenset(done) if REPIN_FACTS in done else frozenset()


def only_the_repin(root, candidate, head, version, rel, runner=None):
    """True when at least one file differs under `rel` between the two commits and every one that does is a
    verified re-pin file. An unreadable listing is False."""
    names, why = _git(root, ["diff", "--no-renames", "--name-only", "-z", "%s..%s" % (candidate, head), "--", rel],
                      runner)
    changed = [n for n in names.split("\0") if n]
    return (not why) and bool(changed) and set(changed) <= repinned_paths(root, candidate, head, version, runner)


def _allowed(path, allowed_paths):
    for entry in allowed_paths:
        if entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif path == entry:
            return True
    return False


def resume_chain_problem(root, candidate, head, version, allowed_paths, runner=None):
    """'' when every commit in candidate..head is one of the cut's own two commits (subjects as the spec lists, in
    order, no merge, one author who is also `git config user.email`, no SCRIPT_DIRS tree changed beyond the cut's
    own re-pin (repinned_paths), and every other path
    `git diff --name-only candidate..head` lists inside `allowed_paths`), else the first offender in words. A range
    that cannot be listed is a problem, never ''. An empty `allowed_paths` means every path refuses."""
    if not _is_str(root) or not root:
        return "the repository root is not a path"
    if not _is_str(version) or not VERSION_RE.fullmatch(version):
        return "the cut version %r is not X.Y.Z" % (version,)
    if not _is_str(candidate) or not SHA_RE.fullmatch(candidate) or not _is_str(head) or not SHA_RE.fullmatch(head):
        return "the candidate and HEAD must both be 40 hex commits"
    if isinstance(allowed_paths, (str, bytes)) or not isinstance(allowed_paths, (list, tuple)) \
            or not all(_is_str(p) for p in allowed_paths):
        return "the allowed paths are not a list of strings, so nothing after the rehearsal can be allowed"
    if candidate == head:
        return ""
    if _rc(_sh(["git", "merge-base", "--is-ancestor", candidate, head], root, runner)) != 0:
        return "the rehearsed commit %s is not reachable from HEAD %s" % (candidate[:12], head[:12])
    listed, why = _git(root, ["rev-list", "--first-parent", "%s..%s" % (candidate, head)], runner)
    if why:
        return "the commits after the rehearsal cannot be listed: %s" % why
    commits = [c for c in listed.split() if c]
    if not commits or len(commits) > 2 or not all(SHA_RE.fullmatch(c) for c in commits):
        return ("%d commits sit between the rehearsed commit and HEAD; a cut adds at most two of its own"
                % len(commits)) if commits else "the range after the rehearsal lists no commit"
    merges, why = _git(root, ["rev-list", "--merges", "%s..%s" % (candidate, head)], runner)
    if why or merges:
        return "the range after the rehearsal holds a merge commit or cannot be checked for one"
    commits.reverse()
    mine, why = _git(root, ["config", "user.email"], runner)
    if why or not mine:
        return "git config user.email of the running cut is unset, so the author of the cut's commits is unknown"
    subjects = []
    for sha in commits:
        line, why = _git(root, ["log", "-1", "--format=%ae%x1f%s", sha], runner)
        email, _sep, subject = line.partition("\x1f")
        if why or not _sep:
            return "commit %s cannot be read: %s" % (sha[:12], why or "no author")
        if email != mine:
            return "commit %s is authored by %s, not by the running cut's identity" % (sha[:12], email)
        subjects.append(subject)
    bump = "%s: the version bump and the regenerated manifests" % version
    note = "%s: the export manifest that describes the tree and the note it ships" % version
    if subjects not in ([bump, note], [bump], [note]):
        return "the commits after the rehearsal are not the cut's own two: %s" % " | ".join(subjects)
    then, why = _script_trees(root, candidate, runner)
    now_trees, why2 = _script_trees(root, head, runner)
    if why or why2:
        return why or why2
    for rel in SCRIPT_DIRS:
        if then[rel] != now_trees[rel] and not only_the_repin(root, candidate, head, version, rel, runner):
            return "%s changed after the rehearsal; a cut never edits its own scripts" % rel
    repinned = repinned_paths(root, candidate, head, version, runner)
    # --no-renames: by default git prints a rename as its destination alone, which hid the deletion of a file
    # outside the allowed paths behind a move into them (review 2026-10-07)
    # -z: a file name may hold a line separator, and a listing split on lines would read it as two allowed names
    names, why = _git(root, ["diff", "--no-renames", "--name-only", "-z", "%s..%s" % (candidate, head)], runner)
    if why:
        return "the files changed after the rehearsal cannot be listed: %s" % why
    for name in names.split("\0"):
        if name and name not in repinned and not _allowed(name, allowed_paths):
            return "%s changed after the rehearsal and is not one of the cut's own files" % name
    return ""


def _load_records(directory, version):
    """([record dicts], '') for every rehearsal-<version>-*.json, or ([], the reason). Any corrupt one refuses."""
    if not os.path.isdir(directory):
        return [], "no rehearsal record: the evidence folder %s does not exist" % directory
    prefix = "rehearsal-%s-" % version
    records = []
    for name in sorted(os.listdir(directory)):
        if not (name.startswith(prefix) and name.endswith(".json")):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            return [], "the rehearsal record %s is corrupt or unreadable (%s); remove it and rehearse again" % (name, exc)
        why = _record_problem(data, name)
        if not why and data["version"] != version:
            why = "the rehearsal record %s is malformed (it names version %r)" % (name, data["version"])
        if why:
            return [], why
        data["_name"] = name
        records.append(data)
    return records, ""


def _record_problem(data, name, schemas=SCHEMAS_READ, verdicts=VERDICTS):
    """'' when `data` is a whole record of a schema in `schemas` (default SCHEMAS_READ), else the reason. A whole
    record gains `_epoch`, and a .1 record gains rows == []. `schemas` and `verdicts` widen only for the local record
    reader (FR1.g); every other caller keeps the strict defaults."""
    if not isinstance(data, dict) or not _is_str(data.get("schema")) or data.get("schema") not in schemas:
        seen = data.get("schema") if isinstance(data, dict) else None
        return "the rehearsal record %s has schema %r, not one of %s" % (name, seen, ", ".join(schemas))
    bad = [f for f in RECORD_FIELDS if f not in data]
    stamp = _parse_iso(data.get("finished_at"))
    if (bad or not _is_str(data["version"]) or not _is_str(data["candidate_sha"])
            or not SHA_RE.fullmatch(data["candidate_sha"]) or data["verdict"] not in verdicts
            or not _is_str(data["verdict"]) or stamp is None or not isinstance(data["script_trees"], dict)
            or not _is_str(data["log_path"]) or not _is_str(data["log_sha256"])
            or not _is_str(data["cut_py_sha256"])):
        return "the rehearsal record %s is malformed (%s)" % (name, ("missing " + ", ".join(bad)) if bad
                                                               else "a field has the wrong type or value")
    if data["schema"] == "cv1.rehearsal.1":
        data["rows"] = []
    else:
        rows = data.get("rows")
        if not isinstance(rows, list):
            return "the rehearsal record %s is malformed (its rows are not a list)" % name
        for row in rows:
            why = validate_row(row)
            if why:
                return "the rehearsal record %s is malformed (%s)" % (name, why)
    if data["schema"] == SCHEMA_BUMPED and not all(
            _is_str(data.get(f)) and SHA_RE.fullmatch(data[f]) for f in ("bumped_sha", "bumped_tree")):
        return "the rehearsal record %s is malformed (bumped_sha and bumped_tree are not 40 hex)" % name
    data["_epoch"] = stamp
    return ""


def load_record(path):
    """(record, '') for the rehearsal record at `path`, rows defaulted to [] for a .1 record; (None, reason) for a
    missing, corrupt, truncated or malformed one. The one reader other modules use."""
    if not _is_str(path) or not path:
        return None, "the rehearsal record path %r is not a path" % (path,)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "the rehearsal record %s is corrupt or unreadable (%s)" % (path, exc)
    why = _record_problem(data, path)
    if why:
        return None, why
    return data, ""


def row_origin_stamp(row):
    """(origin_stamp, '') of a carried row, (None, '') of a fresh one (neither origin field), else (None, reason):
    a carried row without its stamp, a stamp that is empty, not text or not a readable ISO time, or a row that is not
    an object. CS12.d: the one reader of the stamp, so no caller can fall back to the record's own time. Never raises."""
    if not isinstance(row, dict):
        return None, "a row is not an object"
    if "origin_stamp" not in row and "origin_record" not in row:
        return None, ""
    if "origin_stamp" not in row:
        return None, "the carried row has no origin_stamp"
    raw = row["origin_stamp"]
    if not _is_str(raw) or not raw or _parse_iso(raw) is None:
        return None, "the carried row has an unreadable origin_stamp: %r" % (raw,)
    return raw, ""


def _carried_age_problem(row_name, last, origin, now_s):
    """'' when the carried row's origin stamp is not from the future and not older than MAX_AGE_HOURS, else why."""
    age = now_s - _parse_iso(origin)
    if age < -FUTURE_SLACK_S:
        return "the carried %s row is stamped from the future (%s)" % (row_name, origin)
    if age > MAX_AGE_HOURS * 3600:
        return "the carried %s row is stale: origin %s finished %s, older than %g hours" % (
            row_name, last.get("origin_record", ""), origin, MAX_AGE_HOURS)
    return ""


def row_inputs_problem(last, key):
    """'' unless `last` carries an inputs key (CUT1.b) that is not the one this interpreter and environment give its
    tree key now: a row measured under another interpreter or another redirected home is not this run's evidence."""
    if ROW_INPUTS_FIELD not in last:
        return ""
    if last[ROW_INPUTS_FIELD] != row_inputs_key(key):
        return "the %s row was measured under another interpreter or environment" % last.get("row")
    return ""


def reusable_row(record, row, key, home, now=None):
    """(row, '') only when `record` is a READY record whose LAST row named `row` with home `home` carries exactly
    `key`, verdict PASS and a log still hashing to its log_sha256, and is not from the future or older than
    MAX_AGE_HOURS. The age is the record's finished_at for a row the record measured and, for a row it only carried
    (CS12.c), the origin_stamp of its first measurement: a fresh record never makes an old row fresh.
    (None, reason) in every other case, so the caller runs the row."""
    if not isinstance(record, dict):
        return None, "no rehearsal record"
    if not _is_str(row) or row not in ROW_NAMES or not _is_str(home) or home not in ROW_HOMES:
        return None, "row %r with home %r is not a known row" % (row, home)
    if not _is_str(key) or not SHA_RE.fullmatch(key):
        return None, "the key %r is not a 40 hex tree id" % (key,)
    if now is not None and not _is_num(now):
        return None, "the clock %r is not a finite number" % (now,)
    if record.get("verdict") != "READY":
        return None, "the record says %s, not READY" % (record.get("verdict"),)
    stamp = _parse_iso(record.get("finished_at"))
    if stamp is None:
        return None, "the record has no readable finished_at"
    now_s = time.time() if now is None else float(now)
    age = now_s - stamp
    own_age = ""   # the record's own time, which judges a row only when the record measured it
    if age < -FUTURE_SLACK_S:
        own_age = "the record is stamped from the future (%s)" % record.get("finished_at")
    elif age > MAX_AGE_HOURS * 3600:
        own_age = "the record is stale: finished %s, older than %g hours" % (record.get("finished_at"), MAX_AGE_HOURS)
    rows = record.get("rows")
    if own_age and not (isinstance(rows, list) and any(
            isinstance(r, dict) and any(f in r for f in CARRIED_FIELDS) for r in rows)):
        return None, own_age
    if not isinstance(rows, list):
        return None, "the record carries no rows"
    last = None
    for r in rows:
        if validate_row(r):
            return None, "the record carries a malformed row: %s" % validate_row(r)
        if r["row"] == row and r["home"] == home:
            last = r
    if last is None:
        return None, "the record has no %s row for home %s" % (row, home)
    if last["key"] != key:
        return None, "the %s row measured key %s, not %s" % (row, last["key"][:12], key[:12])
    if last["verdict"] != "PASS":
        return None, "the %s row said %s, not PASS" % (row, last["verdict"])
    why = row_inputs_problem(last, key)
    if why:
        return None, why
    origin, why = row_origin_stamp(last)
    if why:
        return None, why
    why = _carried_age_problem(row, last, origin, now_s) if origin else own_age
    if why:
        return None, why
    try:
        log_sha = _sha256_file(last["log_path"])
    except OSError as exc:
        return None, "the log of the %s row is missing or unreadable: %s" % (row, exc)
    if log_sha != last["log_sha256"]:
        return None, "the log of the %s row was altered after it was recorded" % row
    return last, ""


# FR1.e (C5): a --check rehearsal may reuse a PASS row from the newest record of this version, READY or not. The strict
# reader above still refuses a whole folder on one corrupt file, because covering_record decides a cut; a row only
# decides whether a check is skipped, so a bad file here costs a reason and nothing else.
CHECK_ROW_VERDICTS = ("READY", "NOT-READY")
LOCAL_ROW_VERDICTS = ("LOCAL-PASS", "NOT-READY")   # FR1.g: the local record verdicts whose rows may be read
_WORSE = {"FAIL": 2, "NO-DATA": 1, "PASS": 0}


def row_sources(version, directory=""):
    """([records whose verdict is in CHECK_ROW_VERDICTS], [reasons]) from every rehearsal-<version>-*.json in the
    evidence folder. A corrupt, foreign, malformed or NO-DATA file contributes a reason only. Never raises."""
    if not _is_str(version) or not VERSION_RE.fullmatch(version):
        return [], ["version %r is not X.Y.Z" % (version,)]
    if not _is_str(directory):
        return [], ["the evidence folder %r is not a path" % (directory,)]
    directory = directory or evidence_dir()
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        return [], ["no rehearsal record: the evidence folder %s is unreadable (%s)" % (directory, exc)]
    # FR1.g: a full record and a local record feed rows alike; each kind is read under its own schema and verdicts
    kinds = (("rehearsal-%s-" % version, SCHEMAS_READ, VERDICTS, CHECK_ROW_VERDICTS),
             ("%s%s-" % (LOCAL_PREFIX, version), (LOCAL_SCHEMA,), LOCAL_VERDICTS, LOCAL_ROW_VERDICTS))
    records, reasons = [], []
    for name in names:
        kind = [k for k in kinds if name.startswith(k[0])]
        if not (kind and name.endswith(".json")):
            continue
        _prefix, schemas, verdicts, keep = kind[0]
        try:
            with open(os.path.join(directory, name), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            reasons.append("the rehearsal record %s is corrupt or unreadable (%s)" % (name, exc))
            continue
        why = _record_problem(data, name, schemas, verdicts)
        if not why and data["version"] != version:
            why = "the rehearsal record %s is malformed (it names version %r)" % (name, data["version"])
        if not why and data["verdict"] not in keep:
            why = "the rehearsal record %s says %s, so none of its rows are read" % (name, data["verdict"])
        if why:
            reasons.append(why)
            continue
        data["_name"] = name
        records.append(data)
    return records, reasons


def reusable_check_row(version, row, key, home, directory="", now=None):
    """(row, '') when the newest record holding a `row` row for `home` at exactly `key` says PASS, is not from the
    future, is not older than MAX_AGE_HOURS and its log still hashes to log_sha256; (None, reason) otherwise, so the
    caller runs the row. Of two records at one instant the worse verdict wins. Never raises."""
    if not _is_str(row) or row not in ROW_NAMES or not _is_str(home) or home not in ROW_HOMES:
        return None, "row %r with home %r is not a known row" % (row, home)
    if not _is_str(key) or not SHA_RE.fullmatch(key):
        return None, "the key %r is not a 40 hex tree id" % (key,)
    if now is not None and not _is_num(now):
        return None, "the clock %r is not a finite number" % (now,)
    records, reasons = row_sources(version, directory)
    best = None
    for rec in records:
        mine = [r for r in rec["rows"] if r["row"] == row and r["home"] == home and r["key"] == key]
        if not mine:
            continue
        rank = (rec["_epoch"], _WORSE[mine[-1]["verdict"]])
        if best is None or rank > best[0]:
            best = (rank, rec, mine[-1])
    if best is None:
        return None, "no record holds a %s row for home %s at key %s%s" % (
            row, home, key[:12], (" (" + reasons[0] + ")") if reasons else "")
    _, rec, last = best
    now_s = time.time() if now is None else float(now)
    origin, why = row_origin_stamp(last)   # CS12.d: a carried row is as old as its first measurement
    if why:
        return None, why
    if origin:
        why = _carried_age_problem(row, last, origin, now_s)
        if why:
            return None, why
    age = now_s - rec["_epoch"]
    if not origin and age < -FUTURE_SLACK_S:
        return None, "the record %s is stamped from the future (%s)" % (rec["_name"], rec["finished_at"])
    if not origin and age > MAX_AGE_HOURS * 3600:
        return None, "the record %s is stale: finished %s, older than %g hours" % (
            rec["_name"], rec["finished_at"], MAX_AGE_HOURS)
    if last["verdict"] != "PASS":
        return None, "the newest %s row said %s, not PASS" % (row, last["verdict"])
    why = row_inputs_problem(last, key)
    if why:
        return None, why
    try:
        log_sha = _sha256_file(last["log_path"])
    except OSError as exc:
        return None, "the log of the %s row is missing or unreadable: %s" % (row, exc)
    if log_sha != last["log_sha256"]:
        return None, "the log of the %s row was altered after it was recorded" % row
    return last, ""


def covering_record(root, version, resume_sha="", directory="", runner=None, now=None, allowed_paths=(), require_tip=""):
    """(record_path, reason): the path of the newest READY record that covers this commit (exact HEAD, or the resume
    rule when `resume_sha` is non empty), with reason ''; ('', the reason in words) otherwise. Unknown, corrupt or
    unreadable input refuses."""
    if not _is_str(root) or not root:
        return "", "REFUSED: the repository root is not a path"
    if not _is_str(version) or not VERSION_RE.fullmatch(version):
        return "", "REFUSED: the cut version %r is not X.Y.Z" % (version,)
    if resume_sha is None:
        resume_sha = ""
    if not _is_str(resume_sha) or (resume_sha and not SHA_RE.fullmatch(resume_sha)):
        return "", "REFUSED: the resume commit %r is not 40 hex" % (resume_sha,)
    if not _is_str(directory):
        return "", "REFUSED: the evidence folder is not a path"
    if isinstance(allowed_paths, (str, bytes)) or not isinstance(allowed_paths, (list, tuple)) \
            or not all(_is_str(p) for p in allowed_paths):
        return "", "REFUSED: the allowed paths are not a list of strings"
    if now is not None and not _is_num(now):
        return "", "REFUSED: the clock %r is not a finite number" % (now,)
    clock_now = time.time() if now is None else float(now)
    directory = directory or evidence_dir()
    head, why = _git(root, ["rev-parse", "HEAD"], runner)
    if why or not SHA_RE.fullmatch(head):
        return "", "REFUSED: HEAD cannot be read: %s" % why
    records, why = _load_records(directory, version)
    if why:
        return "", "REFUSED: " + why
    problems = {}
    eligible = []
    for rec in records:
        cand = rec["candidate_sha"]
        if cand not in problems:
            if cand == head:
                problems[cand] = ""
            elif resume_sha:
                problems[cand] = resume_chain_problem(root, cand, head, version, allowed_paths, runner)
            else:
                problems[cand] = "the rehearsal covered %s, not HEAD %s" % (cand[:12], head[:12])
        if not problems[cand]:
            eligible.append(rec)
    if not eligible:
        reasons = sorted(set(p for p in problems.values() if p))
        return "", ("REFUSED: no rehearsal record covers HEAD %s (%s)"
                    % (head[:12], "; ".join(reasons) if reasons else "no record at all for %s" % version))
    # newest wins; on a tie the worse verdict wins
    newest = max(eligible, key=lambda r: (r["_epoch"], r["verdict"] != "READY"))
    said = newest["verdict"]
    where = newest["_name"]
    if said != "READY":
        return "", "REFUSED: the newest rehearsal for this commit (%s) says %s, not READY" % (where, said)
    age = clock_now - newest["_epoch"]
    if age < -FUTURE_SLACK_S:
        return "", "REFUSED: the rehearsal %s is stamped from the future (%s)" % (where, newest["finished_at"])
    if age > MAX_AGE_HOURS * 3600:
        return "", "REFUSED: the rehearsal %s is stale: finished %s, older than %g hours" % (
            where, newest["finished_at"], MAX_AGE_HOURS)
    try:
        log_sha = _sha256_file(newest["log_path"])
    except OSError as exc:
        return "", "REFUSED: the log of rehearsal %s is missing or unreadable: %s" % (where, exc)
    if log_sha != newest["log_sha256"]:
        return "", "REFUSED: the log of rehearsal %s was altered after the record was written" % where
    try:
        running = running_cut_py_sha256()
    except OSError as exc:
        return "", "REFUSED: the running scripts/cut.py cannot be hashed: %s" % exc
    if running != newest["cut_py_sha256"]:
        return "", "REFUSED: the rehearsal %s ran a different scripts/cut.py than the one running now" % where
    trees, why = _script_trees(root, "HEAD", runner)
    if why:
        return "", "REFUSED: " + why
    rehearsed, why = _script_trees(root, newest["candidate_sha"], runner)
    if why:
        return "", "REFUSED: " + why
    for rel in SCRIPT_DIRS:
        # the record names the rehearsed commit's own tree, and HEAD may differ from that tree only by the cut's re-pin
        if newest["script_trees"].get(rel) != rehearsed[rel] or (
                rehearsed[rel] != trees[rel]
                and not only_the_repin(root, newest["candidate_sha"], head, version, rel, runner)):
            return "", "REFUSED: %s changed since the rehearsal %s, so it covered different code" % (rel, where)
    if require_tip and newest.get("bumped_sha"):
        # CS12.b: a --resume cut ships the commit that was proved, no other: the record names it and the ref still
        # points at it (a second rehearsal moves the ref, and this record then no longer covers a cut). Only the
        # gate before the fence asks: after the chain a cold cut's HEAD holds commits of its own.
        proved = newest["bumped_sha"]
        ref = rehearsed_ref_name(version, newest["candidate_sha"])
        actual, _why = _git(root, ["rev-parse", "--verify", "--quiet", "%s^{commit}" % ref], runner)
        if require_tip != proved:
            return "", "REFUSED: the resume commit %s is not the tip %s that rehearsal %s proved" % (
                require_tip[:12], proved[:12], where)
        if actual != proved:
            return "", "REFUSED: rehearsal %s proved %s but %s points at %s" % (
                where, proved[:12], ref, actual[:12] or "nothing")
    return os.path.join(directory, where), ""


# FR0.b: the cut ready record (scripts/cut_ready.py writes it) is a different file kind from a rehearsal record. It is
# read here, beside covering_record, and never through SCHEMAS_READ, so one can never stand in for the other.
READY_SCHEMA = "cv1.ready.1"
READY_MAX_AGE_S = 6 * 3600
READY_VERDICTS = ("PASS", "FAIL", "NO-DATA")


def _ready_problem(data):
    """'' when `data` is a whole cut ready record, else the reason (the record is then moved aside by the reader)."""
    if not isinstance(data, dict) or data.get("schema") != READY_SCHEMA:
        return "not a %s record" % READY_SCHEMA
    for key in ("version", "candidate_tree", "verdict", "epoch", "rows"):
        if key not in data:
            return "missing %s" % key
    if not _is_str(data["version"]) or not _is_str(data["candidate_tree"]) or data["verdict"] not in READY_VERDICTS \
            or not _is_str(data["verdict"]) or not isinstance(data["rows"], list):
        return "a field has the wrong type or value"
    for row in data["rows"]:
        if not isinstance(row, dict) or not _is_str(row.get("verdict")) or row["verdict"] not in READY_VERDICTS:
            return "a row has no known verdict"
    return ""


def ready_record(version, tree, directory="", now=None):
    """(path, verdict, reason) for the cut ready record that decides `tree`. PASS only for the newest record whose
    body names this version and this tree, whose verdict is the one its rows give, and which is not older than
    READY_MAX_AGE_S. Everything else is FAIL (the newest record says so) or NO-DATA with the reason and path ''.
    A file that is not a whole record is moved aside to <name>.corrupt-<epoch>, never deleted. Never reads a
    rehearsal file. Unknown, corrupt or unreadable input never reads PASS."""
    if not _is_str(version) or not VERSION_RE.fullmatch(version):
        return "", "NO-DATA", "the cut version %r is not X.Y.Z" % (version,)
    if not _is_str(tree) or not SHA_RE.fullmatch(tree):
        return "", "NO-DATA", "the tree %r is not 40 hex" % (tree,)
    if not _is_str(directory):
        return "", "NO-DATA", "the evidence folder is not a path"
    if now is not None and not _is_num(now):
        return "", "NO-DATA", "the clock %r is not a finite number" % (now,)
    clock_now = time.time() if now is None else float(now)
    directory = directory or evidence_dir()
    none = "no cut-ready record for tree %s" % tree[:12]
    if not os.path.isdir(directory):
        return "", "NO-DATA", none
    prefix = "cut-ready-%s-" % version
    kept = []
    for name in sorted(os.listdir(directory)):
        if not (name.startswith(prefix) and name.endswith(".json")):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError:
            continue
        except ValueError:
            data = None
        if _ready_problem(data):
            try:
                os.replace(path, "%s.corrupt-%d" % (path, int(clock_now)))
            except OSError:
                pass
            continue
        epoch = data["epoch"]
        if data["version"] != version or data["candidate_tree"] != tree or not _is_num(epoch) \
                or epoch - clock_now > FUTURE_SLACK_S:
            continue
        kept.append((epoch, data["verdict"] != "PASS", path, data))
    if not kept:
        return "", "NO-DATA", none
    epoch, _worse, path, data = max(kept, key=lambda k: (k[0], k[1]))
    rows = [r["verdict"] for r in data["rows"]]
    computed = "FAIL" if "FAIL" in rows else ("NO-DATA" if "NO-DATA" in rows or not rows else "PASS")
    if computed != data["verdict"]:
        return "", "NO-DATA", "the record %s contradicts its rows (says %s, rows give %s)" % (
            os.path.basename(path), data["verdict"], computed)
    if computed != "PASS":
        return path, computed, "the newest record %s says %s" % (os.path.basename(path), computed)
    if clock_now - epoch > READY_MAX_AGE_S:
        return "", "NO-DATA", "older than 6 h (%s)" % os.path.basename(path)
    return path, "PASS", ""


# FR1.a: the preflight record. cut_driver.preflight_phase writes it, cut.check_mode reads it, so a rehearsal of the
# same candidate does not run the minutes long export rows a second time. Its own file kind (preflight-*.json), read
# only here; a rehearsal record can never stand in for it nor it for one.
PREFLIGHT_SCHEMA = "cv1.preflight.1"
PREFLIGHT_VERDICTS = ("PASS", "FAIL", "NO-DATA")
PREFLIGHT_EXPORT_ROWS = ("export-real-home", "virgin-gate")
PREFLIGHT_ROW_VERDICTS = ("OK", "REFUSED", "NO-DATA")
PREFLIGHT_FIELDS = ("schema", "version", "candidate_sha", "candidate_tree", "export_tree_id", "verdict", "rows",
                    "log_path", "log_sha256", "interpreter", "seconds", "finished_at")
_PREFLIGHT_LINE_RE = re.compile(r"^(OK|REFUSED|NO-DATA) +(\S+)", re.M)   # cut_preflight prints "%-8s %-16s %s"


def _interpreter():
    return "%d.%d.%d" % sys.version_info[:3]


def record_preflight(version, candidate, tree, export_tree_id, verdict, log_path, seconds, directory="", now=None):
    """Write preflight-<version>-<candidate12>-<epoch>.json atomically and return its path. The rows are parsed from
    the log (the `OK|REFUSED|NO-DATA <name>` lines cut_preflight.py prints). Raises ValueError on a version not X.Y.Z,
    a candidate or tree that is not 40 lower case hex, an export tree id that is neither '' nor 40 hex, a verdict
    outside PREFLIGHT_VERDICTS, a log that cannot be read, or a negative or non finite `seconds`."""
    if not _is_str(version) or not VERSION_RE.fullmatch(version):
        raise ValueError("version %r is not X.Y.Z" % (version,))
    for label, value in (("candidate", candidate), ("tree", tree)):
        if not _is_str(value) or not SHA_RE.fullmatch(value):
            raise ValueError("%s %r is not 40 hex" % (label, value))
    if not _is_str(export_tree_id) or (export_tree_id and not SHA_RE.fullmatch(export_tree_id)):
        raise ValueError("export tree id %r is neither empty nor 40 hex" % (export_tree_id,))
    if not _is_str(verdict) or verdict not in PREFLIGHT_VERDICTS:
        raise ValueError("verdict %r is not one of %s" % (verdict, ", ".join(PREFLIGHT_VERDICTS)))
    if not _is_num(seconds) or seconds < 0:
        raise ValueError("seconds %r is not a finite number" % (seconds,))
    if not _is_str(log_path) or not log_path or not _is_str(directory):
        raise ValueError("log_path and directory must be strings and the log a path")
    if now is not None and not _is_num(now):
        raise ValueError("now %r is not a finite number" % (now,))
    try:
        with open(log_path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ValueError("the preflight log cannot be read: %s" % exc)
    rows = [{"verdict": v, "name": n} for v, n in _PREFLIGHT_LINE_RE.findall(raw.decode("utf-8", "replace"))]
    stamp = time.time() if now is None else float(now)
    directory = directory or evidence_dir()
    os.makedirs(directory, exist_ok=True)
    record = {"schema": PREFLIGHT_SCHEMA, "version": version, "candidate_sha": candidate, "candidate_tree": tree,
              "export_tree_id": export_tree_id, "verdict": verdict, "rows": rows, "log_path": log_path,
              "log_sha256": hashlib.sha256(raw).hexdigest(), "interpreter": _interpreter(),
              "seconds": float(seconds), "finished_at": _iso(stamp)}
    path = os.path.join(directory, "preflight-%s-%s-%d.json" % (version, candidate[:12], int(stamp)))
    tmp = "%s.tmp-%d-%s" % (path, os.getpid(), uuid.uuid4().hex[:6])
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def _preflight_problem(data):
    """'' when `data` is a whole cv1.preflight.1 record, else the reason."""
    if not isinstance(data, dict) or data.get("schema") != PREFLIGHT_SCHEMA:
        return "not a %s record" % PREFLIGHT_SCHEMA
    bad = [f for f in PREFLIGHT_FIELDS if f not in data]
    if bad:
        return "missing " + ", ".join(bad)
    if not all(_is_str(data[f]) for f in PREFLIGHT_FIELDS if f not in ("rows", "seconds")):
        return "a field has the wrong type"
    rows = data["rows"]
    if (not SHA_RE.fullmatch(data["candidate_sha"]) or not SHA_RE.fullmatch(data["candidate_tree"])
            or (data["export_tree_id"] and not SHA_RE.fullmatch(data["export_tree_id"]))
            or data["verdict"] not in PREFLIGHT_VERDICTS or not _is_num(data["seconds"]) or data["seconds"] < 0
            or _parse_iso(data["finished_at"]) is None or not isinstance(rows, list)
            or not all(isinstance(r, dict) and _is_str(r.get("name")) and _is_str(r.get("verdict"))
                       and r["verdict"] in PREFLIGHT_ROW_VERDICTS for r in rows)):
        return "a field has the wrong value"
    return ""


def newest_preflight(version, candidate, tree, directory="", now=None):
    """(record, '') for the newest preflight record of exactly this candidate and tree when it may be reused, else
    (None, reason). A reusable record is a PASS with both PREFLIGHT_EXPORT_ROWS OK, a 40 hex export tree id, the
    running interpreter, an age within MAX_AGE_HOURS (and not FUTURE_SLACK_S ahead), and a log still hashing to
    log_sha256. The NEWEST record decides, so a later FAIL is never hidden by an earlier PASS. A file that is not a
    whole record is skipped and left where it is. The record gains `_path`. Never raises."""
    try:
        return _newest_preflight(version, candidate, tree, directory, now)
    except Exception as exc:  # noqa: BLE001 sbe: allow-silent any doubt reads as no record, so the full preflight runs
        return None, "the preflight records could not be read (%s)" % exc


def _newest_preflight(version, candidate, tree, directory, now):
    if not _is_str(version) or not VERSION_RE.fullmatch(version):
        return None, "the cut version %r is not X.Y.Z" % (version,)
    for label, value in (("candidate", candidate), ("tree", tree)):
        if not _is_str(value) or not SHA_RE.fullmatch(value):
            return None, "the %s %r is not 40 hex" % (label, value)
    if not _is_str(directory) or (now is not None and not _is_num(now)):
        return None, "the evidence folder or the clock is not usable"
    clock_now = time.time() if now is None else float(now)
    directory = directory or evidence_dir()
    if not os.path.isdir(directory):
        return None, "no preflight record: the evidence folder %s does not exist" % directory
    prefix = "preflight-%s-" % version
    kept, skipped = [], []
    for name in sorted(os.listdir(directory)):
        if not (name.startswith(prefix) and name.endswith(".json")):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            skipped.append("%s is unreadable (%s)" % (name, exc))
            continue
        why = _preflight_problem(data)
        if why:
            skipped.append("%s is not a whole record (%s)" % (name, why))
        elif data["version"] == version and data["candidate_sha"] == candidate and data["candidate_tree"] == tree:
            data["_path"] = path
            kept.append((_parse_iso(data["finished_at"]), data["verdict"] != "PASS", data))
    if not kept:
        return None, "no preflight record for %s tree %s%s" % (candidate[:12], tree[:12],
                                                               ("; skipped: " + "; ".join(skipped[:3])) if skipped else "")
    stamp, _worse, rec = max(kept, key=lambda k: (k[0], k[1]))
    where = os.path.basename(rec["_path"])
    if rec["verdict"] != "PASS":
        return None, "the newest preflight record %s says %s" % (where, rec["verdict"])
    for want in PREFLIGHT_EXPORT_ROWS:
        said = [r["verdict"] for r in rec["rows"] if r["name"] == want]
        if not said or any(v != "OK" for v in said):
            return None, "the preflight record %s has no OK %s row" % (where, want)
    if not SHA_RE.fullmatch(rec["export_tree_id"]):
        return None, "the preflight record %s names no export tree" % where
    if rec["interpreter"] != _interpreter():
        return None, "the preflight record %s ran on python %s, this is %s" % (where, rec["interpreter"],
                                                                             _interpreter())
    age = clock_now - stamp
    if age > MAX_AGE_HOURS * 3600 or -age > FUTURE_SLACK_S:
        return None, "the preflight record %s is %.1f h old (the bar is %g h)" % (where, age / 3600.0, MAX_AGE_HOURS)
    try:
        log_ok = _sha256_file(rec["log_path"]) == rec["log_sha256"]
    except OSError:
        log_ok = False
    if not log_ok:
        return None, "the log of the preflight record %s is missing or changed" % where
    return rec, ""


def rehearsal_refusal(root, version, resume_sha="", directory="", runner=None, now=None, allowed_paths=(),
                      require_tip=""):
    """'' when covering_record finds a record, else its reason. `allowed_paths` is passed through to covering_record
    and on to resume_chain_problem; an empty tuple on a resume means every path refuses. `require_tip` (CS12.b) is the
    commit a --resume cut was asked to ship; a record that names a bumped_sha covers it only when they are the same."""
    _path, reason = covering_record(root, version, resume_sha, directory, runner, now, allowed_paths, require_tip)
    return reason or ("" if _path else "REFUSED: no rehearsal record covers this commit")


def main(argv=None, root=None, runner=None):
    # THE PUBLIC ARGV CONTRACT (2026-10-04: the landing fuzz dropped this module for a raw TypeError on main(0), main(True),
    # main(b"x"), ...): argv is None (the process's own arguments) or a list or tuple of str, the shape argparse reads. A
    # bare str is refused too, since argparse would read it character by character. Anything else is a ValueError, the
    # documented refusal, never a crash inside argparse.
    if argv is not None and not (isinstance(argv, (list, tuple)) and all(isinstance(a, str) for a in argv)):
        raise ValueError("main: argv must be None or a list or tuple of str, got %s" % type(argv).__name__)
    ap = argparse.ArgumentParser(description="Rehearse the cut in a detached worktree and record the verdict.")
    ap.add_argument("--version", required=True, help="the release X.Y.Z to rehearse")
    ap.add_argument("--candidate", default="", help="the commit to rehearse (default HEAD)")
    ap.add_argument("--verify", action="store_true", help="only say whether a record covers HEAD now")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="seconds the child may run")
    ap.add_argument("--local", action="store_true",
                    help="the local half only (no export rows, no Linux canary): a LOCAL-PASS record that never covers a cut")
    args = ap.parse_args(argv)
    root = root or ROOT
    if not VERSION_RE.fullmatch(args.version):
        print("NO-DATA: --version %r is not X.Y.Z" % args.version)
        return 2
    if args.local and args.verify:
        print("NO-DATA: --local and --verify contradict each other: a local record never covers a cut")
        return 2
    if args.verify:
        import cut as C
        reason = C.rehearsal_refusal(root, args.version, runner=runner)
        print(reason or "COVERED")
        return 0 if not reason else 1
    try:
        verdict, code, path = run_rehearsal(root, args.version, args.candidate, timeout_s=args.timeout,
                                            runner=runner, local=args.local)
    except (RehearsalRefused, ValueError) as exc:
        print("REFUSED: %s" % exc)
        return 2
    named = os.path.basename(path)
    sha12 = (named[len("local-"):] if named.startswith(LOCAL_PREFIX) else named).split("-")[2]
    print("REHEARSAL %s %s %s exit %d record %s" % (verdict, args.version, sha12, code, path))
    return EXIT_FOR[verdict]


if __name__ == "__main__":
    sys.exit(main())
