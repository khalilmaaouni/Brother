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

SCHEMA = "cv1.rehearsal.1"
MAX_AGE_HOURS = 24.0
FUTURE_SLACK_S = 300
DEFAULT_TIMEOUT_S = 10800
VERDICTS = ("READY", "NOT-READY", "NO-DATA")
EXIT_FOR = {"READY": 0, "NOT-READY": 1, "NO-DATA": 2}

# products/brothermode/tools holds bm_store.py, which cut.py loads by path for the fence: a changed fence module
# after a READY record is a changed cut.
SCRIPT_DIRS = ("scripts", "products/brothermode/scripts", "products/brothersbe/scripts",
               "products/brothermode/tools", "products/brothersbe/tools")

VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SHA_RE = re.compile(r"[0-9a-f]{40}")
RECORD_FIELDS = ("schema", "version", "candidate_sha", "candidate_tree", "exit_code", "verdict", "log_path",
                 "log_sha256", "cut_py_sha256", "script_trees", "seconds", "finished_at")


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


def record_rehearsal(version, candidate, tree, exit_code, verdict, log_path, seconds, cut_py_sha256, directory="",
                     now=None, script_trees=None, note=""):
    """Write the record atomically (temp file then os.replace) and return its path. Raises ValueError on a
    candidate that is not 40 hex or a verdict outside READY, NOT-READY, NO-DATA, and on any other malformed field.
    A READY record needs a readable log and a tree hash for every SCRIPT_DIRS entry."""
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
              "finished_at": _iso(stamp)}
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


def _ready_line(output, version):
    return re.search(r"^READY for %s(?![\w.])" % re.escape(version), output, re.MULTILINE) is not None


def run_rehearsal(root, version, candidate="", directory="", timeout_s=DEFAULT_TIMEOUT_S, runner=None, clock=None):
    """(verdict, exit_code, record_path). Never raises on a child failure: a worktree that cannot be made, a child that
    times out or a log that cannot be written is NO-DATA with a record that says why, or a RehearsalRefused when even
    the record cannot be written or another rehearsal is running. A malformed argument raises ValueError."""
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
    if code == 0 and _ready_line(output, version):
        verdict = "READY"
    elif code == 1:
        verdict = "NOT-READY"
    else:
        verdict = "NO-DATA"
        note = note or ("the child exited %s" % code if code != 0
                        else "the child exited 0 without the line 'READY for %s'" % version)
    finished = clock()
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise RehearsalRefused("the evidence folder %s cannot be made: %s" % (directory, exc))
    log_path = os.path.join(directory, "rehearsal-%s-%s-%d.log" % (version, sha[:12], int(finished)))
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(output)
    except OSError as exc:
        verdict, log_path = "NO-DATA", ""
        note = (note + "; " if note else "") + "the log could not be written: %s" % exc
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
    try:
        path = record_rehearsal(version, sha, tree, code, verdict, log_path, max(finished - started, 0.0), cut_sha,
                                directory, now=finished, script_trees=trees, note=note)
    except (OSError, ValueError) as exc:
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
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            seen = data.get("schema") if isinstance(data, dict) else None
            return [], "the rehearsal record %s has schema %r, not %s" % (name, seen, SCHEMA)
        bad = [f for f in RECORD_FIELDS if f not in data]
        stamp = _parse_iso(data.get("finished_at"))
        if (bad or data["version"] != version or not _is_str(data["candidate_sha"])
                or not SHA_RE.fullmatch(data["candidate_sha"]) or data["verdict"] not in VERDICTS
                or not _is_str(data["verdict"]) or stamp is None or not isinstance(data["script_trees"], dict)
                or not _is_str(data["log_path"]) or not _is_str(data["log_sha256"])
                or not _is_str(data["cut_py_sha256"])):
            return [], "the rehearsal record %s is malformed (%s)" % (name, ("missing " + ", ".join(bad)) if bad
                                                                       else "a field has the wrong type or value")
        data["_epoch"] = stamp
        data["_name"] = name
        records.append(data)
    return records, ""


def covering_record(root, version, resume_sha="", directory="", runner=None, now=None, allowed_paths=()):
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
    return os.path.join(directory, where), ""


def rehearsal_refusal(root, version, resume_sha="", directory="", runner=None, now=None, allowed_paths=()):
    """'' when covering_record finds a record, else its reason. `allowed_paths` is passed through to covering_record
    and on to resume_chain_problem; an empty tuple on a resume means every path refuses."""
    _path, reason = covering_record(root, version, resume_sha, directory, runner, now, allowed_paths)
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
    args = ap.parse_args(argv)
    root = root or ROOT
    if not VERSION_RE.fullmatch(args.version):
        print("NO-DATA: --version %r is not X.Y.Z" % args.version)
        return 2
    if args.verify:
        import cut as C
        reason = C.rehearsal_refusal(root, args.version, runner=runner)
        print(reason or "COVERED")
        return 0 if not reason else 1
    try:
        verdict, code, path = run_rehearsal(root, args.version, args.candidate, timeout_s=args.timeout,
                                            runner=runner)
    except (RehearsalRefused, ValueError) as exc:
        print("REFUSED: %s" % exc)
        return 2
    sha12 = os.path.basename(path).split("-")[2]
    print("REHEARSAL %s %s %s exit %d record %s" % (verdict, args.version, sha12, code, path))
    return EXIT_FOR[verdict]


if __name__ == "__main__":
    sys.exit(main())
