#!/usr/bin/env python3
"""The tree gate: one ledger, keyed by the tree the merge base will have.

WHY THIS EXISTS. The merge tools lived outside the repository, in a home
folder, and their ledger lookup was two awk lines inside a shell script
(R-MG-1, H4). The tree gate now has ONE definition and ONE owner:
`scripts/merge_gate.py`. The shell tools call it and carry no copy of the
rule.

WHAT IT PROVES. `verify_tree` is `verify_newest`: the NEWEST row for the
exact tree decides, through every rule of `verify_row` (schema, mac, host,
age, log hash, summary, the gate script at the base, the changed checks). A bare
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
import unicodedata

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
USAGE = ("usage: merge_gate.py verify [--base <name>] <clone> <tree> | refuse-env [<caller names>] | "
         "ledger-path <clone> | base-ref <remote> <name> | gate <clone> <worktree> <tree> <pr> <head> <base> | "
         "review-check <clone> <pr> <head> <base commit> | merge-record <clone> <pr> <head> <tree> <base> <actor> | "
         "review-record <clone> <pr> <head> <line> <reviewer> <PASS|FAIL> | review-show <clone> <pr> <head>")


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
# decides (D2), so an honest newer row cannot hide them. checks_changed is computed against the base the row's writer
# was given, which is not always the base being merged into (MERGE2.a review, 2026-11-01: a row gated on the dev line
# carries checks_changed=[] for every check the dev line edited, so the dev line's merge back into main read PASS). So
# verify_row also recomputes the changed checks against the ref merged into, and either list refuses.
# D6: such a change is merged by hand. Every other refusal is evidence that is
# missing, stale, foreign or unreadable, and reads NO-DATA (a fresh gate may still answer it). Both block every caller.
FAIL_CODES = ("FAIL", "CHECKS-EDITED", "GATE-EDITED")


def verify_tree(clone, tree, env=None, runner=None, now=None, base="main"):
    """("PASS", why), ("FAIL", why) or ("NO-DATA", why) for the exact tree: THE one verdict every caller reads (the
    `verify` command line, merge_precompute's skip check and wait_ready). It is verify_newest, never a second rule: the
    newest row for the tree decides through verify_row (D2), so schema, mac, host, age, log, the gate script at the
    base (GATE-EDITED, read at base_ref(base)) and the changed checks (CHECKS-EDITED, D6) all apply. A missing runner
    is NO-DATA, never a pass; a base not on ALLOWED_BASES is refused."""
    clone = _as_str(clone, "clone")
    tree = _as_str(tree, "tree")
    if not TREE_RE.match(tree):
        raise MergeGateError("tree must be a 40 character hex tree id, not %r"
                             % tree[:64])
    ref = base_ref(base)
    try:
        ok, why = verify_newest(clone, tree, now, env, runner, ref)
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
# LO1.a (2026-10-14): the owns completeness check, read FIRST on every merge. On 1.1.3 a builder branch merged by
# the session added a scripts/loop file no unit owned, and the cut refused it 2.6 hours in (rehearsal run 7). The
# gate's own loop-owns-complete row still runs later; this is the same check read before 500 s are spent.
OWNS_SCRIPT = "scripts/test_bl_owns_complete.py"
# MERGE2.a (owner ruling 2026-11-01, his words "Yes, allow it"): the bases a merge may target, fixed HERE, in the
# tool's own source. A base is chosen by an explicit argument (merge_verified.sh --base), never by an inherited
# environment variable: MERGE_BASE_BRANCH stays in FORBIDDEN_NAMES. A base not on this list is refused by name.
ALLOWED_BASES = ("main", "plan/1.1.9-line-2026-10-25")
REMOTE_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def base_ref(base, remote="hub"):
    """refs/remotes/<remote>/<base> for a base on ALLOWED_BASES, or a refusal naming it. Never the short name: a
    fetched tag refs/tags/hub/main outranks refs/remotes/hub/main when git resolves hub/main."""
    base = _as_str(base, "base")
    remote = _as_str(remote, "remote")
    if base not in ALLOWED_BASES:
        raise MergeGateError("base %r is not a named merge base; the allowed bases are %s"
                             % (base[:80], ", ".join(ALLOWED_BASES)))
    if not REMOTE_RE.match(remote):
        raise MergeGateError("remote %r is not a plain remote name" % remote[:80])
    return "refs/remotes/%s/%s" % (remote, base)


MAIN_REF = base_ref("main")
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
    check_owns(runner, worktree, child_env)
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


def owns_verdict(code, out):
    """("PASS", line) only for exit 0 with a line starting PASS: and no line starting SKIPPED; ("FAIL", text) for
    exit 1, quoting up to three FAIL lines; ("NO-DATA", why) for everything else. A check that did not look never
    reads as a pass."""
    if isinstance(code, bool) or not isinstance(code, int) or not isinstance(out, str):
        return ("NO-DATA", "the owns check returned no exit code or no text")
    lines = [l.strip() for l in out.replace("\r", "\n").splitlines()]
    if code == 1:
        fails = [l for l in lines if l.startswith("FAIL")]
        more = len(fails) - 3
        text = "; ".join(l[:200] for l in fails[:3]) or "exit 1 with no FAIL line"
        return ("FAIL", text + (" (and %d more)" % more if more > 0 else ""))
    if code != 0:
        return ("NO-DATA", "the owns check exited %d" % code)
    if any(l.startswith("SKIPPED") for l in lines):
        return ("NO-DATA", "the owns check skipped: it did not look, so it is not a pass")
    passed = [l for l in lines if l.startswith("PASS:")]
    if not passed:
        return ("NO-DATA", "the owns check exited 0 with no PASS: line")
    return ("PASS", passed[0][:300])


def check_owns(runner, worktree, child_env):
    """Run the owns completeness check in `worktree`; the PASS line, or MergeNoData naming why the merge is refused."""
    code, out, err = _call(runner, ["python3", "-B", OWNS_SCRIPT], worktree, child_env)
    verdict, text = owns_verdict(code, out)
    if verdict == "PASS":
        return text
    if verdict == "FAIL":
        raise MergeNoData("OWNS-FAIL: a scripts/loop component is not owned by unit BL (%s); the gate did not run "
                          "and no row is written" % text)
    raise MergeNoData("OWNS-NO-DATA: %s%s; the gate did not run and no row is written"
                      % (text, (": " + err.strip()[:200]) if err.strip() else ""))


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
        return False, "GATE-EDITED: the change edits scripts/required_fast.sh against %s; merge it by hand" % ref
    if row["checks_changed"]:
        return False, "CHECKS-EDITED: %s; merge it by hand" % " ".join(row["checks_changed"])
    # the row's own list was computed against ITS base; the merge goes into `ref`, so the closure is read again there
    changed = _changed_checks(clone, ref, row["tree"], runner)
    if changed:
        return False, "CHECKS-EDITED: %s against %s; merge it by hand" % (" ".join(changed), ref)
    return True, "PASS: pass %d fail 0 no-data 0" % counts["pass"]


def verify_newest(clone, tree, now=None, env=None, runner=None, ref=MAIN_REF):
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
    ok, why = verify_row(clone, mine[-1], stamp, env, runner, ref)
    if ok and any(isinstance(r.get("rc"), int) and r.get("rc") != 0 for r in mine[:-1]):
        why += " (flaky: earlier FAIL)"
    return ok, why


# ---------------------------------------------------------------------------------------------------------------------
# MERGE1.a (2026-11-01): a merge needs a recorded review of the EXACT head. Measured: one pull request was refused by the
# host's classifier although its review was green, and another was merged before its review found a HIGH, so the merge
# decision lived in no record at all. Two files beside the whole gate record, in the git common directory every worktree
# shares: <common>/brother-review-records.jsonl (read here, written by the reviewer) and
# <common>/brother-merge-records.jsonl (appended here, after the merge call succeeded). review_check READS ONLY; every
# unreadable, torn, missing or unproven input refuses (MergeGateError = FAIL, MergeNoData = NO-DATA), never a pass.
# ---------------------------------------------------------------------------------------------------------------------
REVIEW_FILE = "brother-review-records.jsonl"
MERGE_RECORDS_FILE = "brother-merge-records.jsonl"
PR_RE = re.compile(r"\A[0-9]+\Z")
HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")


def _records_dir(clone):
    """(gate_ledger module, whole gate ledger path, the common git directory): the same directory the whole gate record
    lives in, from the one function the writer and the push gate use."""
    try:
        import gate_ledger
    except ImportError as exc:
        raise MergeNoData("gate_ledger.py cannot be imported (%s), so no review can be checked" % exc)
    whole, why = gate_ledger.whole_gate_ledger(clone)
    if whole is None:
        raise MergeNoData(why)
    return gate_ledger, whole, os.path.dirname(whole)


def _read_jsonl(path, what):
    """Every line of a JSON lines file as a dict. A file that cannot be read, is not utf-8, or holds one torn or
    non-object line is NO-DATA as a whole: no line of it is trusted, and none is skipped."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, ValueError) as exc:
        raise MergeNoData("%s %s cannot be read (%s)" % (what, path, exc))
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise MergeNoData("%s %s line %d is not JSON, so no line of it is trusted" % (what, path, number))
        if not isinstance(row, dict):
            raise MergeNoData("%s %s line %d is not a JSON object" % (what, path, number))
        rows.append(row)
    return rows


# ROUND 1 (2026-11-01, independent review): a review record is no longer a free-text claim.
#   * ONE WRITER: append_review_record (merge_gate.py review-record). It derives the builders from git, refuses a
#     reviewer among them, takes the whole gate row for the tree from the ledger itself, and chains the row to the one
#     before it. review_check never trusts a self declared builder list: the builders are DERIVED again at check time
#     (authors and committers of base..head) and the record's own list can only ADD names.
#   * STRICT_REVIEW_RECORDS: the two rules that turn a hand written row into a refusal (a zero code_hash gate row; a
#     review row that is unchained or breaks the chain) are written and tested, and are switched on by this constant
#     once the harness writes its valid case through the writer. Until then the chain is a RATCHET: once any row carries
#     prev_hash, every row from there on must chain, so a hand appended row after a writer row is NO-DATA.
#   * THE CHAIN IS AN AUDIT, NOT A CONTROL (owner ruling D8, 2026-11-01). It is keyless tamper EVIDENCE: it shows an edit,
#     a splice or a hand appended row to whoever reads the file, and a keyless chain can be rewritten whole by someone who
#     can write the git common directory on this machine, who recomputes every hash. The CONTROL is the owner's typed
#     count, and merge_verified.sh shows him each PR's review row (review_show: reviewer, verdict, head, time, chain
#     position) above that prompt. No key is built here; a signed git note is a later upgrade that waits on the owner.
STRICT_REVIEW_RECORDS = True
GENESIS_HASH = "0" * 64
REVIEWER_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}\Z")
BUILDER_SOURCE = "builders are the commit authors and committers of base..head; the claim store names no builders for a pull request"


def _norm_name(value):
    """NFKC, no Unicode Cf (zero width joiner and kin), blanks collapsed, case folded: two spellings of one id compare equal."""
    text = unicodedata.normalize("NFKC", value)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return " ".join(text.split()).casefold()


def _reviewer_problem(reviewer, declared, derived):
    """Why `reviewer` may not review, or None. The id must be plain ASCII once normalised (a Cyrillic lookalike of a
    builder's letter is no letter of ours), and must match none of the derived builders and none of the declared ones."""
    if not isinstance(reviewer, str) or not _norm_name(reviewer):
        return "the review record names no reviewer"
    name = _norm_name(reviewer)
    if not REVIEWER_RE.match(name):
        return "reviewer %r is not a plain ASCII id (letters, digits, . _ @ + -)" % reviewer[:60]
    if not isinstance(declared, list) or not all(isinstance(b, str) and _norm_name(b) for b in declared):
        return "the record's builders are not a list of non empty names"
    if name in {_norm_name(b) for b in list(derived) + list(declared)}:
        return "reviewer %r is one of the builders" % reviewer
    return None


def derive_builders(runner, clone, base_commit, head):
    """Every author and committer (name and email) of the commits base_commit..head, from git: the builder set a
    reviewer must be outside of. No commit in the range reads as NO-DATA (nothing to merge, nothing to review)."""
    out = _git_text(runner, clone, ["log", "--no-show-signature", "--format=%an%n%ae%n%cn%n%ce",
                                    "%s..%s" % (base_commit, head)])
    names = sorted({line.strip() for line in out.splitlines() if line.strip()})
    if not names:
        raise MergeNoData("no commit in %s..%s, so no builders can be derived" % (base_commit[:12], head[:12]))
    return names


def _is_ancestor(runner, clone, older, newer):
    """True/False from git merge-base --is-ancestor (exit 0/1); any other exit is NO-DATA."""
    code, _out, err = _call(runner, ["git", "merge-base", "--is-ancestor", older, newer], clone)
    if code in (0, 1):
        return code == 0
    raise MergeNoData("git merge-base --is-ancestor %s %s exited %d: %s" % (older[:12], newer[:12], code, err.strip()[:160]))


def _check_review_chain(rows, path, line_hash, strict):
    """The review file chains like the gate ledger: each row's prev_hash is the hash of the row before it (the first:
    GENESIS). strict: every row must carry it. Otherwise a ratchet: from the first row that carries prev_hash on, all
    must chain. A broken chain is NO-DATA, never a skipped row."""
    first = next((i for i, row in enumerate(rows) if "prev_hash" in row), None)
    if first is None:
        if strict and rows:
            raise MergeNoData("the review record file %s has rows with no prev_hash; strict mode reads an unchained file as NO-DATA" % path)
        return
    start = 0 if strict else first
    for j in range(start, len(rows)):
        want = GENESIS_HASH if j == 0 else line_hash(rows[j - 1])
        if rows[j].get("prev_hash") != want:
            raise MergeNoData("the review record file %s does not chain at line %d" % (path, j + 1))


def _whole_gate_rows(gate_ledger, whole, tree):
    """[(id, row)] of the whole gate ledger's WHOLE_GATE rows for `tree`, oldest first; the chain is verified first."""
    if not os.path.lexists(whole):
        raise MergeGateError("no whole gate record: %s does not exist" % whole)
    if not gate_ledger.verify_chain(whole):
        raise MergeNoData("the whole gate ledger %s is corrupt or does not chain, so no record in it is trusted" % whole)
    return [(gate_ledger._line_hash(row), row) for row in _read_jsonl(whole, "the whole gate ledger")
            if row.get("gate_name") == gate_ledger.WHOLE_GATE and row.get("tree_sha") == tree]


def _real_code_hash(row):
    value = row.get("code_hash")
    return isinstance(value, str) and bool(HEX64_RE.match(value)) and value != GENESIS_HASH


def review_check(clone, pr, head, base_commit, runner=None, strict=None):
    """The sentence naming the review when the NEWEST review record for pull request `pr` at exactly `head` is a PASS
    by a reviewer outside its DERIVED builders, for the tree `head` has now, with a whole gate PASS record of that tree
    that is also the newest whole gate row for it, and a release line that `head` and the base both carry. Otherwise a
    refusal. Newest decides: a later FAIL beats an earlier PASS. A record for the same pull request at ANOTHER head is
    not a review of this one. strict (default STRICT_REVIEW_RECORDS) adds: a real (non zero) code_hash on the gate row
    and a review file chained end to end."""
    clone = _as_str(clone, "clone")
    pr = _as_str(pr, "pr")
    head = _as_str(head, "head")
    base_commit = _as_str(base_commit, "base commit")
    strict = STRICT_REVIEW_RECORDS if strict is None else strict
    if not PR_RE.match(pr):
        raise MergeGateError("the pull request number must be digits, not %r" % pr[:40])
    if not TREE_RE.match(head) or not TREE_RE.match(base_commit):
        raise MergeGateError("head and base commit must be 40 character hex ids")
    runner = _as_runner(runner)
    gate_ledger, whole, common = _records_dir(clone)
    path = os.path.join(common, REVIEW_FILE)
    if not os.path.lexists(path):
        raise MergeGateError("no review record: %s does not exist" % path)
    number = int(pr)
    rows = _read_jsonl(path, "the review record file")
    _check_review_chain(rows, path, gate_ledger._line_hash, strict)
    mine = [r for r in rows if _is_int(r.get("pr")) and r["pr"] == number and r.get("head") == head]
    if not mine:
        raise MergeGateError("no review record for pull request #%d at head %s" % (number, head))
    rec = mine[-1]
    if not _is_int(rec.get("schema")) or rec["schema"] != 1:
        raise MergeGateError("the review record's schema is not 1")
    if rec.get("verdict") != "PASS":
        raise MergeGateError("the review's verdict is %r, not PASS" % (rec.get("verdict"),))
    reviewer = rec.get("reviewer")
    problem = _reviewer_problem(reviewer, rec.get("builders"), derive_builders(runner, clone, base_commit, head))
    if problem:
        raise MergeGateError(problem)
    tree = _git_text(runner, clone, ["rev-parse", "--verify", "--quiet", head + "^{tree}"]).strip()
    if not TREE_RE.match(tree):
        raise MergeNoData("git gave no tree id for %s" % head)
    if rec.get("tree") != tree:
        raise MergeGateError("the review was of tree %s, the head's tree is %s" % (rec.get("tree"), tree))
    gate_id = rec.get("whole_gate")
    if not isinstance(gate_id, str) or not HEX64_RE.match(gate_id):
        raise MergeGateError("the review record's whole_gate is not a 64 character hex id")
    if not os.path.lexists(whole):
        raise MergeGateError("no whole gate record: %s does not exist" % whole)
    if not gate_ledger.verify_chain(whole):
        raise MergeNoData("the whole gate ledger %s is corrupt or does not chain, so no record in it is trusted" % whole)
    found = None
    for row in _read_jsonl(whole, "the whole gate ledger"):
        if gate_ledger._line_hash(row) == gate_id:
            found = row
            break
    if found is None:
        raise MergeGateError("whole gate record %s is not in %s" % (gate_id, whole))
    if found.get("gate_name") != gate_ledger.WHOLE_GATE or found.get("status") != "PASS":
        raise MergeGateError("whole gate record %s is %s %s, not a PASS of %s"
                             % (gate_id[:12], found.get("gate_name"), found.get("status"), gate_ledger.WHOLE_GATE))
    if found.get("tree_sha") != tree:
        raise MergeGateError("whole gate record %s is for tree %s, not %s" % (gate_id[:12], found.get("tree_sha"), tree))
    newest_id, newest = _whole_gate_rows(gate_ledger, whole, tree)[-1]
    if newest.get("status") != "PASS":
        raise MergeGateError("the newest whole gate record for tree %s is %s %s, so the earlier PASS no longer stands"
                             % (tree[:12], newest_id[:12], newest.get("status")))
    if strict and not _real_code_hash(found):
        raise MergeGateError("whole gate record %s carries no real code_hash" % gate_id[:12])
    line = rec.get("line")
    if not isinstance(line, str) or not TREE_RE.match(line):
        raise MergeGateError("the review record's line is not a 40 character hex commit")
    if len(_git_text(runner, clone, ["rev-list", "--parents", "-n", "1", line]).split()) < 2:
        raise MergeGateError("the line %s is a root commit, which every history carries and so proves nothing" % line[:12])
    if not _is_ancestor(runner, clone, line, head):
        raise MergeGateError("the line %s the review was made against is not an ancestor of head %s" % (line[:12], head[:12]))
    if not _is_ancestor(runner, clone, line, base_commit):
        raise MergeGateError("the line %s the review was made against is not an ancestor of the base %s" % (line[:12], base_commit[:12]))
    fork = _git_text(runner, clone, ["merge-base", base_commit, head]).strip()
    if not TREE_RE.match(fork) or not _is_ancestor(runner, clone, fork, line):
        raise MergeGateError("the line %s is older than the point head %s forked from the base (%s)" % (line[:12], head[:12], fork[:12]))
    return "PASS: reviewed by %s at head %s, tree %s, whole gate %s, line %s (%s)" % (
        reviewer, head[:12], tree[:12], gate_id[:12], line[:12], BUILDER_SOURCE)


def append_review_record(clone, pr, head, line, reviewer, verdict, runner=None, now=None):
    """THE ONE WRITER of the review record file. It derives the builders (authors and committers of line..head), refuses
    a reviewer among them or a non ASCII id, reads the head's tree from git, takes the whole gate row for that tree from
    the ledger itself (the newest, which for a PASS must be a PASS with a real code_hash), and appends the row chained to
    the one before it, under a flock. Returns the file path. `line` is the base tip the review was made against."""
    clone = _as_str(clone, "clone")
    pr = _as_str(pr, "pr")
    head = _as_str(head, "head")
    line = _as_str(line, "line")
    reviewer = _as_str(reviewer, "reviewer")
    verdict = _as_str(verdict, "verdict")
    if not PR_RE.match(pr) or not TREE_RE.match(head) or not TREE_RE.match(line):
        raise MergeGateError("a review record needs a numeric pull request and 40 hex head and line")
    if verdict not in ("PASS", "FAIL"):
        raise MergeGateError("the verdict is PASS or FAIL, not %r" % verdict[:20])
    runner = _as_runner(runner)
    if len(_git_text(runner, clone, ["rev-list", "--parents", "-n", "1", line]).split()) < 2:
        raise MergeGateError("the line %s is a root commit, which every history carries and so proves nothing" % line[:12])
    gate_ledger, whole, common = _records_dir(clone)
    builders = derive_builders(runner, clone, line, head)
    problem = _reviewer_problem(reviewer, [], builders)
    if problem:
        raise MergeGateError(problem)
    tree = _git_text(runner, clone, ["rev-parse", "--verify", "--quiet", head + "^{tree}"]).strip()
    if not TREE_RE.match(tree):
        raise MergeNoData("git gave no tree id for %s" % head)
    tree_rows = _whole_gate_rows(gate_ledger, whole, tree) if os.path.lexists(whole) else []
    if verdict == "PASS":
        if not tree_rows:
            raise MergeGateError("no whole gate record for tree %s, so no PASS review can be recorded" % tree)
        newest_id, newest = tree_rows[-1]
        if newest.get("status") != "PASS" or not _real_code_hash(newest):
            raise MergeGateError("the newest whole gate record for tree %s is not a PASS with a real code_hash" % tree)
        gate_id = newest_id
    else:
        gate_id = tree_rows[-1][0] if tree_rows else GENESIS_HASH
    stamp = time.time() if now is None else now
    path = os.path.join(common, REVIEW_FILE)
    lock = os.open(path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rows = _read_jsonl(path, "the review record file") if os.path.lexists(path) else []
        _check_review_chain(rows, path, gate_ledger._line_hash, False)
        row = {"schema": 1, "pr": int(pr), "head": head, "tree": tree, "whole_gate": gate_id, "line": line,
               "reviewer": reviewer, "builders": builders, "verdict": verdict, "reviewed_unix": stamp,
               "prev_hash": gate_ledger._line_hash(rows[-1]) if rows else GENESIS_HASH}
        data = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
        handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            if os.write(handle, data) != len(data):
                raise MergeNoData("the review record file took a short write")
            os.fsync(handle)
        finally:
            os.close(handle)
    finally:
        os.close(lock)
    return path


def review_show(clone, pr, head):
    """ONE display line about the newest review row for pull request `pr` at `head`, for the owner to read above the
    typed count: reviewer, verdict, head, time, the row's place in the file and whether it chains. It decides nothing
    (review_check does); a file it cannot read or a PR with no row is said in the line, never raised."""
    pr = _as_str(pr, "pr")
    head = _as_str(head, "head")
    try:
        _gl, _whole, common = _records_dir(clone)
        path = os.path.join(common, REVIEW_FILE)
        if not os.path.lexists(path):
            return "REVIEW #%s: none on record (%s does not exist)" % (pr, path)
        rows = _read_jsonl(path, "the review record file")
    except MergeGateError as exc:
        return "REVIEW #%s: cannot be read (%s)" % (pr, exc)
    hits = [i for i, row in enumerate(rows) if _is_int(row.get("pr")) and str(row["pr"]) == pr and row.get("head") == head]
    if not hits:
        return "REVIEW #%s: none on record for head %s" % (pr, head)
    row = rows[hits[-1]]
    stamp = row.get("reviewed_unix")
    when = _iso(stamp) if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp) \
        else "no time recorded"
    try:  # "chained" is said only when the whole file verifies, never because a prev_hash field is present
        _check_review_chain(rows, path, _gl._line_hash, True)
        chain = "chained"
    except MergeNoData:
        chain = "chain does NOT verify"
    return "REVIEW #%s: %s by %s at head %s, %s (row %d of %d, %s; the chain is an audit, your typed count is the control)" % (
        pr, row.get("verdict"), row.get("reviewer"), head, when, hits[-1] + 1, len(rows), chain)


def append_merge_record(clone, pr, head, tree, base, actor, now=None):
    """Append ONE merge record to <common>/brother-merge-records.jsonl, under the flock every ledger writer here uses.
    Called by merge_verified.sh only after `gh pr merge` succeeded."""
    clone = _as_str(clone, "clone")
    pr = _as_str(pr, "pr")
    head = _as_str(head, "head")
    tree = _as_str(tree, "tree")
    base = _as_str(base, "base")
    actor = _as_str(actor, "actor")
    if not PR_RE.match(pr) or not TREE_RE.match(head) or not TREE_RE.match(tree) or not actor.strip():
        raise MergeGateError("a merge record needs a numeric pull request, 40 hex head and tree, and an actor")
    _gl, _whole, common = _records_dir(clone)
    stamp = time.time() if now is None else now
    row = {"schema": 1, "pr": int(pr), "head": head, "tree": tree, "base": base, "actor": actor,
           "merged_unix": stamp, "merged_at": _iso(stamp)}
    path = os.path.join(common, MERGE_RECORDS_FILE)
    _append_line(path, json.dumps(row, sort_keys=True))
    return path


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
    if command == "base-ref":
        # MERGE2.a: the one place a shell tool turns a base name into the ref it reads, so the allow list is never
        # copied into a shell script
        if len(rest) != 2:
            sys.stderr.write(USAGE + "\n")
            return 2
        try:
            sys.stdout.write("%s\n" % base_ref(rest[1], rest[0]))
        except MergeGateError as exc:
            sys.stderr.write("NO-DATA: %s\n" % exc)
            return 2
        return 0
    if command == "review-check":
        # MERGE1.a: PASS (exit 0), FAIL (1) or NO-DATA (2) for the review record of this pull request at this head
        if len(rest) != 4:
            sys.stderr.write(USAGE + "\n")
            return 2
        try:
            from merge_precompute import default_runner as review_runner
        except Exception as exc:  # a missing runner refuses and never approves
            sys.stderr.write("NO-DATA: no command runner (merge_precompute.py: %s)\n" % exc)
            return 2
        try:
            sys.stdout.write("%s\n" % review_check(rest[0], rest[1], rest[2], rest[3], runner=runner or review_runner))
        except MergeNoData as exc:
            sys.stdout.write("NO-DATA: %s\n" % exc)
            return 2
        except MergeGateError as exc:
            sys.stdout.write("FAIL: %s\n" % exc)
            return 1
        return 0
    if command == "review-show":
        if len(rest) != 3:
            sys.stderr.write(USAGE + "\n")
            return 2
        sys.stdout.write("%s\n" % review_show(*rest))
        return 0
    if command == "review-record":
        # MERGE1.a round 1: the one writer of the review record file
        if len(rest) != 6:
            sys.stderr.write(USAGE + "\n")
            return 2
        try:
            from merge_precompute import default_runner as writer_runner
        except Exception as exc:
            sys.stderr.write("NO-DATA: no command runner (merge_precompute.py: %s)\n" % exc)
            return 2
        try:
            sys.stdout.write("%s\n" % append_review_record(*rest, runner=runner or writer_runner))
        except (MergeGateError, OSError) as exc:
            sys.stderr.write("NO-DATA: %s\n" % exc)
            return 2
        return 0
    if command == "merge-record":
        if len(rest) != 6:
            sys.stderr.write(USAGE + "\n")
            return 2
        try:
            sys.stdout.write("%s\n" % append_merge_record(*rest))
        except (MergeGateError, OSError) as exc:
            sys.stderr.write("NO-DATA: %s\n" % exc)
            return 2
        return 0
    if command == "verify":
        base = "main"
        if rest[:1] == ["--base"]:
            if len(rest) < 2:
                sys.stderr.write(USAGE + "\n")
                return 2
            base, rest = rest[1], rest[2:]
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
            verdict, why = verify_tree(rest[0], rest[1], runner=runner, base=base)
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
