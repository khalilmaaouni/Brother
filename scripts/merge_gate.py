#!/usr/bin/env python3
"""The tree gate: one ledger, keyed by the tree main will have.

WHY THIS EXISTS. The merge tools lived outside the repository, in a home
folder, and their ledger lookup was two awk lines inside a shell script
(R-MG-1, H4). The tree gate now has ONE definition and ONE owner:
`scripts/merge_gate.py`. The shell tools call it and carry no copy of the
rule.

WHAT IT PROVES. `verify_tree` is `verify_newest`: the NEWEST row for the
exact tree decides, through every rule of `verify_row` (schema, mac, host,
age, log hash, summary, the gate script at main, the changed checks). A bare
tree and rc row, which anything could write, never passes. A missing ledger,
a tree with no row, and a torn ledger are NO-DATA: never a pass and never a
skip (H2).

STDLIB ONLY, Python 3.9 floor. This module runs no process of its own: the
`git merge-tree` call takes a git runner the caller injects, and the shell
tools pass the tree id in. A caller that injects nothing is REFUSED, never
waved through. The `verify` command line borrows the runner of the declared
command runner merge_precompute.py; when that cannot be imported it is NO-DATA.
"""
from __future__ import annotations

import datetime
import fcntl
import hashlib
import hmac
import json
import math
import os
import pwd
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import tmp_sandbox as _tmp_sandbox
except ImportError:  # a missing list REFUSES; it never approves a steered env
    _tmp_sandbox = None

TREE_RE = re.compile(r"\A[0-9a-f]{40}\Z")
GATE_DIR = "merge-gate"
LEDGER_NAME = "ledger.jsonl"
KEY_NAME = "key"
SEAM_ENV = "MERGE_GATE_TEST"
LEDGER_ENV = "MERGE_GATE_LEDGER"
FORBIDDEN_PREFIXES = ("MERGE_GATE_", "MERGE_ALL_")
# MERGE_CLONE, MERGE_REPO, MERGE_REMOTE and MERGE_BASE_BRANCH re-point the clone, the repository, the remote or the
# base a real run verifies and merges against (review round 17, 2026-10-04: a clone it controls carries its own ledger
# and key, so a forged PASS would merge): honoured by a dry run only.
# and (review round 19) the names that re-point gh at another host or identity, or git at another configuration
# PR_PARK_NOW pins the park check's clock for its hermetic suite (2026-10-10, unit U0c); a real run must read the clock.
FORBIDDEN_NAMES = ("PR_PARK_REPO", "PR_PARK_NOW", "MERGE_CLONE", "MERGE_REPO", "MERGE_REMOTE", "MERGE_BASE_BRANCH",
                   "GH_HOST", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                   "GH_CONFIG_DIR", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                   "GIT_EXEC_PATH", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_SSH_VARIANT")
USAGE = ("usage: merge_gate.py verify <clone> <tree> | refuse-env [<caller names>] | "
         "ledger-path <clone> | gate <clone> <worktree> <tree> <pr> <head> <base>")


class MergeGateError(ValueError):
    """Every deliberate refusal of this module, so a caller catches one name."""


class MergeConflict(MergeGateError):
    """merge-tree reported a conflict, or the git runner itself failed."""


class MergeNoData(MergeGateError):
    """Input this module cannot read: never a pass and never a skip."""


def _as_str(value, what):
    """A non empty string, or a refusal. A bool is never a string here."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise MergeGateError("%s must be a string, not %s"
                             % (what, type(value).__name__))
    if not value.strip():
        raise MergeGateError("%s must not be empty" % what)
    return value


def _as_env(env):
    """The environment mapping this module will read, or a refusal. A str, a
    bytes, a bool and a number are not mappings; a name that is not a string
    is refused before anything reads it."""
    if env is None:
        return dict(os.environ)
    if isinstance(env, (bool, int, float, bytes, bytearray, str, list, tuple,
                        set, frozenset)):
        raise MergeGateError("env must be a mapping of names, not %s"
                             % type(env).__name__)
    try:
        names = list(env.keys())
    except AttributeError:
        raise MergeGateError("env must be a mapping of names, not %s"
                             % type(env).__name__)
    except Exception as exc:  # a block, never a crash and never a pass
        raise MergeGateError("env keys cannot be read (%s)"
                             % type(exc).__name__)
    for name in names:
        if not isinstance(name, str):
            raise MergeGateError("every env key must be a string, not %s"
                                 % type(name).__name__)
    return {name: env[name] for name in names}


def _read_text(path):
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise MergeNoData("%s cannot be read (%s)" % (path, exc))
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise MergeNoData("%s is not utf-8" % path)


def _resolve(path, base):
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base, path))


def git_common_dir(clone):
    """The clone's git common directory, read from disk and never from a
    process. `<clone>/.git` is a directory for an ordinary clone and a file
    naming a gitdir for a linked worktree, and a gitdir may name a shared
    commondir. A name this module cannot read is NO-DATA, never a guess."""
    clone = _as_str(clone, "clone")
    dotgit = os.path.join(clone, ".git")
    if os.path.isdir(dotgit):
        gitdir = dotgit
    elif os.path.isfile(dotgit):
        text = _read_text(dotgit)
        if not text.startswith("gitdir:"):
            raise MergeNoData("clone %s: .git names no gitdir" % clone)
        gitdir = _resolve(text[len("gitdir:"):].strip(), clone)
    elif os.path.isfile(os.path.join(clone, "HEAD")):
        gitdir = clone
    else:
        raise MergeNoData("clone %s: no .git directory, .git file or bare HEAD"
                          % clone)
    commondir = os.path.join(gitdir, "commondir")
    if os.path.isfile(commondir):
        gitdir = _resolve(_read_text(commondir), gitdir)
    if not os.path.isdir(gitdir):
        raise MergeNoData("clone %s: the git common dir %s is not a directory"
                          % (clone, gitdir))
    return gitdir


def ledger_path(clone, env=None):
    """`<git common dir>/merge-gate/ledger.jsonl` for the clone: untracked by
    construction, shared by every worktree, never counted as a dirty tree.
    `MERGE_GATE_LEDGER` is honoured only while `MERGE_GATE_TEST=1` is also
    set, the fixture seam, so a caller can never point the verifier at a
    ledger and key it controls (R-MG-8)."""
    clone = _as_str(clone, "clone")
    environment = _as_env(env)
    steered = environment.get(LEDGER_ENV)
    if steered is not None and not isinstance(steered, str):
        raise MergeGateError("%s must be a string, not %s"
                             % (LEDGER_ENV, type(steered).__name__))
    if steered and environment.get(SEAM_ENV) == "1":
        return steered
    return os.path.join(git_common_dir(clone), GATE_DIR, LEDGER_NAME)


def _git_location_names():
    """`tmp_sandbox.GIT_LOCATION_VARS`, imported and never written a second
    time. None means the list could not be read, and a real run then REFUSES:
    a missing validator never approves."""
    if _tmp_sandbox is None:
        return None
    names = _tmp_sandbox.GIT_LOCATION_VARS
    if isinstance(names, str) or not isinstance(names, (tuple, list)):
        return None
    return tuple(names)


def refuse_steered_env(env, dry_run):
    """The reason a REAL run refuses to start with, or None. Any
    `MERGE_GATE_*` or `MERGE_ALL_*` name, `PR_PARK_REPO`, or any name in
    `tmp_sandbox.GIT_LOCATION_VARS` would steer the gate: the git names would
    redirect the ledger path or its objects, and the park name the close
    target. A dry run writes nothing, so it is never refused for this."""
    environment = _as_env(env)
    if not isinstance(dry_run, bool):
        raise MergeGateError("dry_run must be True or False, not %s"
                             % type(dry_run).__name__)
    if dry_run:
        return None
    names = _git_location_names()
    if names is None:
        return ("the git location variable list could not be read, so a real "
                "run cannot prove the environment is clean")
    for name in sorted(environment):
        if (name in FORBIDDEN_NAMES or name.startswith(FORBIDDEN_PREFIXES)
                or name in names):
            return ("%s is set, so the guarded process would steer the gate; "
                    "unset it and run again" % name)
    return None


def merged_tree(clone, base, head, runner=None):
    """The tree id `git merge-tree --write-tree base head` prints, through a
    runner the caller injects. A conflict, a git failure and a missing runner
    are all refusals; none of them is a tree id."""
    clone = _as_str(clone, "clone")
    base = _as_str(base, "base")
    head = _as_str(head, "head")
    if runner is not None and not callable(runner):
        raise MergeGateError("runner must be a callable git runner or None, "
                             "not %s" % type(runner).__name__)
    if runner is None:
        raise MergeNoData("no git runner was injected, so the merged tree "
                          "cannot be computed; the shell tool computes it "
                          "and passes the tree id in")
    argv = ["git", "merge-tree", "--write-tree", base, head]
    try:
        proc = runner(argv, cwd=clone)
    except OSError as exc:
        raise MergeNoData("the git runner could not be started: %s" % exc)
    code = proc.returncode
    if isinstance(code, bool) or not isinstance(code, int):
        raise MergeGateError("the git runner returned no exit code")
    out = proc.stdout
    if not isinstance(out, str):
        raise MergeGateError("the git runner returned no text stdout")
    lines = out.strip().splitlines()
    if code != 0:
        first = lines[0][:200] if lines else ""
        raise MergeConflict("git merge-tree exited %d: %s" % (code, first))
    if not lines:
        raise MergeConflict("git merge-tree printed no tree id")
    tree = lines[0].strip()
    if not TREE_RE.match(tree):
        raise MergeConflict("git merge-tree printed no tree id: %r" % tree[:80])
    return tree


# The codes of verify_row that judge the change itself: FAIL, CHECKS-EDITED and GATE-EDITED. The newest row for a tree
# decides (D2), so an honest newer row cannot hide them; but checks_changed is computed against the base the row's
# writer was given, and verify_row does not recompare that base with main, so a newer row written by the same user with
# a chosen base CAN turn CHECKS-EDITED into a pass. That is inside D0 (the mac guards accident, partial writes and hand
# typed rows, never a same-user adversary; security review 2026-10-05), and no shipped caller chooses such a base.
# D6: such a change is merged by hand. Every other refusal is evidence that is
# missing, stale, foreign or unreadable, and reads NO-DATA (a fresh gate may still answer it). Both block every caller.
FAIL_CODES = ("FAIL", "CHECKS-EDITED", "GATE-EDITED")


def verify_tree(clone, tree, env=None, runner=None, now=None):
    """("PASS", why), ("FAIL", why) or ("NO-DATA", why) for the exact tree: THE one verdict every caller reads (the
    `verify` command line, merge_precompute's skip check and wait_ready). It is verify_newest, never a second rule: the
    newest row for the tree decides through verify_row (D2), so schema, mac, host, age, log, the gate script at main
    (GATE-EDITED) and the changed checks (CHECKS-EDITED, D6) all apply. A missing runner is NO-DATA, never a pass."""
    clone = _as_str(clone, "clone")
    tree = _as_str(tree, "tree")
    if not TREE_RE.match(tree):
        raise MergeGateError("tree must be a 40 character hex tree id, not %r"
                             % tree[:64])
    try:
        ok, why = verify_newest(clone, tree, now, env, runner)
    except MergeNoData as exc:
        return "NO-DATA", str(exc)
    if ok:
        return "PASS", why
    if why.split(":", 1)[0] in FAIL_CODES:
        return "FAIL", why
    return "NO-DATA", why


def _canonical(row):
    body = {}
    for name in sorted(row):
        if name != "mac":
            body[name] = row[name]
    try:
        return json.dumps(body, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MergeGateError("the row cannot be signed (%s)" % exc)


def _key_path(clone):
    return os.path.join(git_common_dir(clone), GATE_DIR, KEY_NAME)


def _key(clone, create):
    path = _key_path(clone)
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        raw = b""
    except OSError as exc:
        raise MergeNoData("the gate key at %s cannot be read (%s)"
                          % (path, exc))
    if len(raw) >= 16:
        return raw
    if not create:
        return None
    if raw:
        raise MergeNoData("the gate key at %s is shorter than 16 bytes" % path)
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise MergeNoData("the gate key directory %s cannot be made (%s)"
                          % (directory, exc))
    if not os.path.isdir(directory):
        raise MergeNoData("the gate key directory %s is not a directory"
                          % directory)
    fresh = os.urandom(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        handle = os.open(path, flags, 0o600)
    except FileExistsError:
        return _key(clone, False)
    except OSError as exc:
        raise MergeNoData("the gate key at %s cannot be made (%s)"
                          % (path, exc))
    try:
        os.write(handle, fresh)
    finally:
        os.close(handle)
    return fresh


def sign_row(clone, row):
    """A copy of `row` with a `mac` over every other field. The key is a 0600
    file under the clone's git common dir, so a hand typed row is refused."""
    clone = _as_str(clone, "clone")
    if not isinstance(row, dict) or isinstance(row, (bool, bytes, str)):
        raise MergeGateError("row must be a mapping, not %s"
                             % type(row).__name__)
    for name in row:
        if not isinstance(name, str):
            raise MergeGateError("every row key must be a string, not %s"
                                 % type(name).__name__)
    key = _key(clone, True)
    if key is None:
        raise MergeNoData("no gate key is available for %s" % clone)
    body = _canonical(row)
    signed = {}
    for name in row:
        if name != "mac":
            signed[name] = row[name]
    signed["mac"] = hmac.new(key, body, hashlib.sha256).hexdigest()
    return signed


def mac_ok(clone, row):
    """True only when the row carries a valid mac over its other fields."""
    clone = _as_str(clone, "clone")
    if not isinstance(row, dict) or isinstance(row, (bool, bytes, str)):
        return False
    mac = row.get("mac")
    if not isinstance(mac, str) or len(mac) != 64:
        return False
    key = _key(clone, False)
    if key is None:
        return False
    try:
        body = _canonical(row)
    except MergeGateError:
        return False
    want = hmac.new(key, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, mac)


# ---------------------------------------------------------------------------------------------------------------------
# MG1.b: the ledger row, its writer and its verifier.
#
# NO PROCESS OF ITS OWN. Every git call and the gate child run through a RUNNER the caller injects:
# runner(argv, cwd=..., env=...) returns an object with returncode, stdout and stderr as text. A caller that injects
# none is REFUSED (MergeNoData), never waved through; this file is not a declared command runner, so it imports no
# subprocess.
# ---------------------------------------------------------------------------------------------------------------------
SCHEMA = 1
GATE_SCRIPT = "scripts/required_fast.sh"
MAIN_REF = "refs/remotes/hub/main"   # never the short name: a fetched tag refs/tags/hub/main outranks it
LOGS_DIR = "logs"
# BROTHER_CLAUDE_BIN (C10, 2026-10-10): the owner's pin for the Claude Code CLI the plugin-manifest row runs, read by
# brother_paths.claude_for_check (an absolute path to an executable file, else that row reads NO-DATA). It crosses on
# its own, so PATH stays the system's; it grants nothing HOME does not already decide (the proven program record and
# the package directories both live under HOME).
CHILD_ENV_ALLOW = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "TERM", "USER", "SHELL", "BROTHER_CLAUDE_BIN")
MAX_AGE_ENV = "MERGE_GATE_MAX_AGE_H"
TRUSTED_ENV = "MERGE_GATE_TRUSTED_HOSTS"
DEFAULT_MAX_AGE_H = 48.0
FUTURE_SLACK_S = 300.0
HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")
COUNT_LINE_RE = re.compile(r"^pass (\d+) +fail (\d+) +no-data (\d+)$", re.MULTILINE)
FAILED_LINE_RE = re.compile(r"^FAILED:(.*)$", re.MULTILINE)
NODATA_LINE_RE = re.compile(r"^NO-DATA:(.*?)\s+\(not a pass", re.MULTILINE)
MODULE_RE = re.compile(r"\A[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\Z")
TOKEN_RE = re.compile(r"[^\s'\"=;&|()<>$`]+")
GLOB_CALL_RE = re.compile(r"glob\s*\(\s*['\"]([^'\"]+)['\"]")
ROW_KEYS = ("schema", "tree", "rc", "pr", "head", "base", "by", "pid", "started", "finished", "failed",
            "nodata_names", "gate_sha256", "checks_changed", "log", "log_sha256", "mac")


class LedgerCorrupt(MergeGateError):
    """A torn or non-object ledger line: every merge BLOCKS, no line is skipped."""


def _as_runner(runner):
    if runner is None:
        raise MergeNoData("no command runner was injected, so git and the gate cannot run; "
                          "a missing runner refuses and never approves")
    if not callable(runner):
        raise MergeGateError("runner must be callable, not %s" % type(runner).__name__)
    return runner


def _call(runner, argv, cwd, env=None):
    """(returncode, stdout, stderr) from the injected runner, or a refusal."""
    try:
        proc = runner(argv, cwd=cwd, env=env)
    except OSError as exc:
        raise MergeNoData("%s could not be started: %s" % (argv[0], exc))
    code = proc.returncode
    if isinstance(code, bool) or not isinstance(code, int):
        raise MergeGateError("the runner returned no exit code")
    out, err = proc.stdout, proc.stderr
    if out is None:
        out = ""
    if err is None:
        err = ""
    if not isinstance(out, str) or not isinstance(err, str):
        raise MergeGateError("the runner returned no text output")
    return code, out, err


def _git_text(runner, clone, argv):
    code, out, err = _call(runner, ["git"] + argv, clone)
    if code != 0:
        raise MergeNoData("git %s exited %d: %s" % (" ".join(argv[:2]), code, (err or out).strip()[:200]))
    return out


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _str_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _glob_regex(pattern):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _module_paths(tokens, files):
    """Each dotted module name (a `-m` module or a unittest argument) turned into the file it names."""
    found = set()
    for token in tokens:
        if MODULE_RE.match(token) and token.replace(".", "/") + ".py" in files:
            found.add(token.replace(".", "/") + ".py")
    return found


def _cd_relative(tokens, files, any_kind):
    """Files named relative to a `cd <dir>` earlier on the same line, as in `sh -c 'cd <dir> && sh x.sh'`."""
    found = set()
    here = None
    for index, token in enumerate(tokens):
        if token == "cd" and index + 1 < len(tokens):
            here = tokens[index + 1]
            continue
        if here is None:
            continue
        joined = os.path.normpath(os.path.join(here, token))
        if joined in files and (any_kind or joined.endswith((".py", ".sh"))):
            found.add(joined)
    return found


def gate_closure(clone, ref=MAIN_REF, runner=None):
    """Every path of the tree at `ref` that scripts/required_fast.sh executes, in any directory: each path token on a
    run_check line, each -m or unittest module turned into its file, each script the file runs outside a run_check
    line, the names relative to a `cd` inside a shell wrapper, and the files every discovery script's own glob
    literal matches. Read from the file itself at `ref`, never from a hand kept list."""
    clone = _as_str(clone, "clone")
    ref = _as_str(ref, "ref")
    runner = _as_runner(runner)
    text = _git_text(runner, clone, ["show", "%s:%s" % (ref, GATE_SCRIPT)])
    files = set(line for line in _git_text(runner, clone, ["ls-tree", "-r", "--name-only", ref]).splitlines() if line)
    found = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        any_kind = stripped.startswith("run_check")
        tokens = TOKEN_RE.findall(stripped)
        for token in tokens:
            if token in files and (any_kind or token.endswith((".py", ".sh"))):
                found.add(token)
        found |= _module_paths(tokens, files)
        found |= _cd_relative(tokens, files, any_kind)
    for path in sorted(p for p in found if p.endswith(".py")):
        source = _git_text(runner, clone, ["show", "%s:%s" % (ref, path)])
        for pattern in GLOB_CALL_RE.findall(source):
            regex = _glob_regex(pattern)
            found |= set(name for name in files if regex.match(name))
    found.discard(GATE_SCRIPT)
    return sorted(found)


def _changed_checks(clone, base, tree, runner):
    closure = set(gate_closure(clone, base, runner))
    diff = _git_text(runner, clone, ["diff", "--name-only", base, tree])
    return sorted(closure & set(line for line in diff.splitlines() if line))


def _iso(stamp):
    return datetime.datetime.fromtimestamp(stamp).astimezone().isoformat(timespec="seconds")


def _host():
    return os.uname().nodename


def _user():
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.getuid())


def parse_log(text):
    """((pass, fail, no_data) or None, failed names, no-data names) from required_fast's own summary lines."""
    counts = None
    for match in COUNT_LINE_RE.finditer(text):
        counts = tuple(int(x) for x in match.groups())
    failed, nodata = [], []
    for match in FAILED_LINE_RE.finditer(text):
        failed.extend(match.group(1).split())
    for match in NODATA_LINE_RE.finditer(text):
        nodata.extend(match.group(1).split())
    return counts, sorted(failed), sorted(nodata)


def _ledger_dir(clone, env):
    return os.path.dirname(ledger_path(clone, env))


def _append_line(path, line):
    """One line, one write, under flock on <path>.lock: two gates never tear a line."""
    data = (line + "\n").encode("utf-8")
    lock = os.open(path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            if os.write(handle, data) != len(data):
                raise MergeNoData("the ledger at %s took a short write" % path)
            os.fsync(handle)
        finally:
            os.close(handle)
    finally:
        os.close(lock)


def record_row(clone, tree, rc, log_path, pr, head, base, env=None, runner=None, started=None, gate_sha256=None):
    """Compute, sign and append ONE row. Counts, failed names, the changed checks, the times, `by` and the log hash are
    all computed here; a caller supplies none of them. `run_gate` is the only caller outside tests."""
    clone = _as_str(clone, "clone")
    tree = _as_str(tree, "tree")
    if not TREE_RE.match(tree):
        raise MergeGateError("tree must be a 40 character hex tree id, not %r" % tree[:64])
    if not _is_int(rc):
        raise MergeGateError("rc must be an int, not %s" % type(rc).__name__)
    log_path = _as_str(log_path, "log_path")
    pr, head, base = _as_str(pr, "pr"), _as_str(head, "head"), _as_str(base, "base")
    runner = _as_runner(runner)
    finished = time.time()
    if started is None:
        started = finished
    if isinstance(started, bool) or not isinstance(started, (int, float)) or not math.isfinite(started):
        raise MergeGateError("started must be a finite number")
    try:
        with open(log_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise MergeNoData("the gate log %s cannot be read (%s)" % (log_path, exc))
    text = raw.decode("utf-8", "replace")
    counts, failed, nodata = parse_log(text)
    if gate_sha256 is None:
        gate_sha256 = _sha256(_git_text(runner, clone, ["show", "%s:%s" % (tree, GATE_SCRIPT)]).encode("utf-8"))
    if not isinstance(gate_sha256, str) or not HEX64_RE.match(gate_sha256):
        raise MergeGateError("gate_sha256 must be a sha256 hex digest")
    changed = _changed_checks(clone, base, tree, runner)
    directory = _ledger_dir(clone, env)
    logs = os.path.join(directory, LOGS_DIR)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.makedirs(logs, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise MergeNoData("the ledger directory %s cannot be made (%s)" % (logs, exc))
    name = "%s-%d-%d.log" % (tree, time.time_ns(), os.getpid())
    copy = os.path.join(logs, name)
    handle = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(handle, raw)
    finally:
        os.close(handle)
    row = {"schema": SCHEMA, "tree": tree, "rc": rc, "pr": pr, "head": head, "base": base,
           "by": "%s@%s" % (_user(), _host()), "pid": os.getpid(), "started": _iso(started),
           "finished": _iso(finished), "failed": failed, "nodata_names": nodata, "gate_sha256": gate_sha256,
           "checks_changed": changed, "log": "%s/%s" % (LOGS_DIR, name), "log_sha256": _sha256(raw)}
    if counts is not None:
        row["counts"] = {"pass": counts[0], "fail": counts[1], "no_data": counts[2]}
    signed = sign_row(clone, row)
    _append_line(ledger_path(clone, env), json.dumps(signed, sort_keys=True, separators=(",", ":")))
    return signed


def run_gate(clone, worktree, tree, pr, head, base, env=None, runner=None):
    """Run `sh scripts/required_fast.sh` in the scratch worktree as a child whose environment is the fixed allowlist,
    keep the whole output, and only then record the row with the exit code the child gave. The only caller of
    record_row; no subcommand lets a caller type an rc."""
    clone = _as_str(clone, "clone")
    worktree = _as_str(worktree, "worktree")
    tree = _as_str(tree, "tree")
    if not TREE_RE.match(tree):
        raise MergeGateError("tree must be a 40 character hex tree id, not %r" % tree[:64])
    runner = _as_runner(runner)
    environment = _as_env(env)
    child_env = {name: value for name, value in environment.items() if name in CHILD_ENV_ALLOW}
    try:
        with open(os.path.join(worktree, GATE_SCRIPT), "rb") as handle:
            gate_sha = _sha256(handle.read())
    except OSError as exc:
        raise MergeNoData("the gate script in %s cannot be read (%s)" % (worktree, exc))
    # H1: the row names the tree main will have, so the gate must run in a worktree holding EXACTLY that tree. A
    # worktree at a PR's own head tests the head alone while the row would claim the merged tree passed (review
    # 2026-10-04); no caller can record a row for a tree it did not test, so this is checked here, not in callers.
    actual = _git_text(runner, worktree, ["rev-parse", "HEAD^{tree}"]).strip()
    if actual != tree:
        raise MergeNoData("the worktree %s holds tree %s, not the tree %s this gate is asked to record; the gate "
                          "did not run and no row is written" % (worktree, actual[:40], tree))
    started = time.time()
    code, out, err = _call(runner, ["sh", GATE_SCRIPT], worktree, child_env)
    scratch = tempfile.mkdtemp(prefix="merge-gate-run-")
    try:
        log = os.path.join(scratch, "gate.log")
        with open(log, "wb") as handle:
            handle.write((out + err).encode("utf-8", "replace"))
        return record_row(clone, tree, code, log, pr, head, base, environment, runner=runner,
                          started=started, gate_sha256=gate_sha)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def read_ledger(path):
    """Every row of the ledger, in append order. A torn or non-object line raises LedgerCorrupt: it never skips."""
    path = _as_str(path, "path")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise MergeNoData("the ledger at %s cannot be read (%s)" % (path, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise LedgerCorrupt("the ledger at %s is not utf-8" % path)
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise LedgerCorrupt("the ledger at %s is torn at line %d" % (path, number))
        if not isinstance(row, dict):
            raise LedgerCorrupt("the ledger at %s carries a non-object row at line %d" % (path, number))
        rows.append(row)
    return rows


def import_legacy_tsv(clone, tsv_path, env=None):
    """Rows marked `legacy: true` from tonight's TSV (tree, rc, when, pr, head, ...). They carry no counts and no mac,
    so verify_row always refuses them; the import only lets a report name the trees to gate again. Any bad line
    raises before anything is written."""
    clone = _as_str(clone, "clone")
    tsv_path = _as_str(tsv_path, "tsv_path")
    text = _read_text(tsv_path)
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 5:
            raise MergeNoData("%s line %d has %d fields, not 5 or more" % (tsv_path, number, len(fields)))
        tree, code, when, pr, head = [f.strip() for f in fields[:5]]
        if not TREE_RE.match(tree) or not re.match(r"\A-?\d+\Z", code) or not pr or not head or not when:
            raise MergeNoData("%s line %d is not a gate row" % (tsv_path, number))
        rows.append({"schema": SCHEMA, "legacy": True, "tree": tree, "rc": int(code), "finished": when,
                     "pr": pr, "head": head})
    path = ledger_path(clone, env)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    for row in rows:
        _append_line(path, json.dumps(row, sort_keys=True, separators=(",", ":")))
    return len(rows)


def _max_age_hours(environment):
    raw = environment.get(MAX_AGE_ENV)
    if raw is None:
        return DEFAULT_MAX_AGE_H
    if not isinstance(raw, str):
        return None
    try:
        hours = float(raw)
    except ValueError:
        return None
    if not math.isfinite(hours) or hours <= 0:
        return None
    return hours


def _schema_problem(row):
    if row.get("schema") != SCHEMA or not _is_int(row.get("schema")):
        return "unknown schema %r" % (row.get("schema"),)
    for key in ROW_KEYS:
        if key not in row:
            return "missing key %s" % key
    if not (isinstance(row["tree"], str) and TREE_RE.match(row["tree"])):
        return "tree is not a tree id"
    for key in ("pr", "head", "base", "by", "started", "finished", "log"):
        if not isinstance(row[key], str) or not row[key]:
            return "%s is not a string" % key
    if "@" not in row["by"]:
        return "by is not user@host"
    for key in ("rc", "pid"):
        if not _is_int(row[key]):
            return "%s is not an int" % key
    for key in ("failed", "nodata_names", "checks_changed"):
        if not _str_list(row[key]):
            return "%s is not a list of strings" % key
    for key in ("gate_sha256", "log_sha256"):
        if not (isinstance(row[key], str) and HEX64_RE.match(row[key])):
            return "%s is not a sha256" % key
    counts = row.get("counts")
    if counts is not None:
        if not isinstance(counts, dict):
            return "counts is not an object"
        for key in ("pass", "fail", "no_data"):
            if not _is_int(counts.get(key)) or counts[key] < 0:
                return "counts.%s is not a count" % key
    return ""


def _finished_epoch(text):
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.timestamp()


def verify_row(clone, row, now, env=None, runner=None, ref=MAIN_REF):
    """(True, "PASS ...") or (False, "<CODE>: why"). Codes: LEGACY, SCHEMA, MAC, FOREIGN, STALE, TREE-ABSENT,
    LOG-MISSING, LOG-HASH, NO-SUMMARY, CONTRADICTION, FAIL, NO-DATA-ROW, GATE-EDITED, CHECKS-EDITED. Nothing here
    accepts a flag that waves a refusal through."""
    clone = _as_str(clone, "clone")
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise MergeGateError("now must be a finite number of seconds")
    environment = _as_env(env)
    runner = _as_runner(runner)
    if not isinstance(row, dict):
        return False, "SCHEMA: the row is %s, not an object" % type(row).__name__
    if row.get("legacy") is True:
        return False, "LEGACY: a row from the old gate has no summary and no mac; gate the tree again"
    problem = _schema_problem(row)
    if problem:
        return False, "SCHEMA: %s" % problem
    key_path = _key_path(clone)
    try:
        key_mode = os.stat(key_path).st_mode
    except OSError:
        return False, "MAC: no gate key at %s" % key_path
    if key_mode & 0o077:
        return False, "MAC: the gate key has group or world permissions"
    if not mac_ok(clone, row):
        return False, "MAC: missing or wrong mac"
    host = row["by"].split("@", 1)[1]
    trusted = [h.strip() for h in str(environment.get(TRUSTED_ENV) or "").split(",") if h.strip()]
    if host != _host() and host not in trusted:
        return False, "FOREIGN: gated on %s, not on this host" % host
    hours = _max_age_hours(environment)
    if hours is None:
        return False, "SCHEMA: %s is not a positive number of hours" % MAX_AGE_ENV
    finished = _finished_epoch(row["finished"])
    if finished is None:
        return False, "SCHEMA: finished is not an ISO time with an offset"
    if finished > now + FUTURE_SLACK_S:
        return False, "SCHEMA: finished is in the future"
    if now - finished > hours * 3600.0:
        return False, "STALE: gated more than %g hours ago" % hours
    code, _out, _err = _call(runner, ["git", "cat-file", "-e", row["tree"] + "^{tree}"], clone)
    if code != 0:
        return False, "TREE-ABSENT: tree %s is not in this clone" % row["tree"]
    directory = _ledger_dir(clone, environment)
    rel = row["log"]
    if os.path.isabs(rel) or ".." in rel.split("/"):
        return False, "SCHEMA: log path leaves the ledger directory"
    try:
        with open(os.path.join(directory, rel), "rb") as handle:
            raw = handle.read()
    except OSError:
        return False, "LOG-MISSING: %s is absent" % rel
    if _sha256(raw) != row["log_sha256"]:
        return False, "LOG-HASH: the log differs from the hash in the row"
    counts = row.get("counts")
    seen, _failed, _nodata = parse_log(raw.decode("utf-8", "replace"))
    if counts is None or seen is None:
        return False, "NO-SUMMARY: no counts or no summary line in the log"
    if (counts["pass"], counts["fail"], counts["no_data"]) != seen:
        return False, "CONTRADICTION: counts differ from the summary line in the log"
    if row["rc"] == 0 and counts["fail"] > 0:
        return False, "CONTRADICTION: rc 0 with fail %d" % counts["fail"]
    if row["rc"] != 0 and counts["fail"] == 0 and not row["failed"]:
        return False, "CONTRADICTION: rc %d with fail 0 and no failed names" % row["rc"]
    if counts["no_data"] > 0:
        return False, "NO-DATA-ROW: no-data %d (%s); a NO-DATA is never a pass" % (
            counts["no_data"], " ".join(row["nodata_names"]) or "no names")
    if row["rc"] != 0:
        return False, "FAIL: the gate exited %d (%s)" % (row["rc"], " ".join(row["failed"]))
    main_gate = _git_text(runner, clone, ["show", "%s:%s" % (ref, GATE_SCRIPT)]).encode("utf-8")
    if _sha256(main_gate) != row["gate_sha256"]:
        return False, "GATE-EDITED: the change edits scripts/required_fast.sh; merge it by hand"
    if row["checks_changed"]:
        return False, "CHECKS-EDITED: %s; merge it by hand" % " ".join(row["checks_changed"])
    return True, "PASS: pass %d fail 0 no-data 0" % counts["pass"]


def verify_newest(clone, tree, now=None, env=None, runner=None):
    """The NEWEST row for the tree decides, through verify_row. An earlier FAIL beside a later PASS is named
    `flaky: earlier FAIL` and does not block. A missing ledger, a corrupt one and a tree with no row refuse."""
    clone = _as_str(clone, "clone")
    tree = _as_str(tree, "tree")
    if not TREE_RE.match(tree):
        raise MergeGateError("tree must be a 40 character hex tree id, not %r" % tree[:64])
    stamp = time.time() if now is None else now
    try:
        rows = read_ledger(ledger_path(clone, env))
    except MergeGateError as exc:
        return False, "NO-DATA: %s" % exc
    mine = [r for r in rows if r.get("tree") == tree]
    if not mine:
        return False, "NO-DATA: no row for tree %s" % tree
    ok, why = verify_row(clone, mine[-1], stamp, env, runner)
    if ok and any(isinstance(r.get("rc"), int) and r.get("rc") != 0 for r in mine[:-1]):
        why += " (flaky: earlier FAIL)"
    return ok, why


def _gate_main(rest, runner):
    if len(rest) != 6:
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        row = run_gate(rest[0], rest[1], rest[2], rest[3], rest[4], rest[5], runner=runner)
    except MergeGateError as exc:
        sys.stderr.write("NO-DATA: %s\n" % exc)
        return 2
    sys.stdout.write("ROW rc %d tree %s\n" % (row["rc"], row["tree"]))
    return 0


def main(argv=None, runner=None):
    # THE PUBLIC ARGV CONTRACT (2026-10-04: dropped at landing for TypeError on main(0), main(True), main(b"x"), ...):
    # None (the process's own arguments) or a list or tuple of str; anything else is a ValueError, the documented
    # refusal, before any argument is read.
    if argv is not None and not (isinstance(argv, (list, tuple)) and all(isinstance(a, str) for a in argv)):
        raise ValueError("main: argv must be None or a list or tuple of str, got %s" % type(argv).__name__)
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(USAGE + "\n")
        return 2
    command, rest = args[0], args[1:]
    if command == "refuse-env":
        # THE CALLER'S NAMES (review 2026-10-06): merge_verified.sh starts itself in a clean environment, so a steering
        # name no longer reaches this process. Every further argument is DATA from that clean start, the blank
        # separated NAMES its caller's environment carried; no value arrives, so each counts as set and is judged by
        # the one rule beside this process's own names. What a real run refused before, it still refuses by name.
        try:
            environment = dict(os.environ)
            for name in " ".join(rest).split():
                environment.setdefault(name, "")
            reason = refuse_steered_env(environment, False)
        except MergeGateError as exc:
            reason = "the environment could not be read: %s" % exc
        if reason:
            sys.stderr.write("NO-DATA: %s\n" % reason)
            return 2
        return 0
    if command == "ledger-path":
        if len(rest) != 1:
            sys.stderr.write(USAGE + "\n")
            return 2
        try:
            sys.stdout.write("%s\n" % ledger_path(rest[0]))
        except MergeGateError as exc:
            sys.stderr.write("NO-DATA: %s\n" % exc)
            return 2
        return 0
    if command == "gate":
        return _gate_main(rest, runner)
    if command == "verify":
        if len(rest) != 2:
            sys.stderr.write(USAGE + "\n")
            return 2
        if runner is None:
            try:   # the declared command runner's own process path; this file imports no subprocess
                from merge_precompute import default_runner as runner
            except Exception as exc:  # a missing runner refuses and never approves
                sys.stderr.write("NO-DATA: no command runner (merge_precompute.py: %s)\n" % exc)
                return 2
        try:
            verdict, why = verify_tree(rest[0], rest[1], runner=runner)
        except MergeGateError as exc:
            sys.stderr.write("NO-DATA: %s\n" % exc)
            return 2
        sys.stdout.write("%s %s\n" % (verdict, why))
        if verdict == "PASS":
            return 0
        if verdict == "FAIL":
            return 1
        return 2
    sys.stderr.write(USAGE + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
