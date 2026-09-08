"""land_queue: the hub PR lander as a product script, not five loose shell files.

WHY (row M6, docs/plan/READINESS-ROADMAP-2026-08-29.json). On 2026-09-06 21:24
JST a switch restarted the serial consumer twice, seven gates ran at once, the
pre-push gate's own 60 second test timeouts painted gates red under that load,
and a PR was refused for load rather than for a real defect. The loose scripts
(~/.claude/evidence/serial-queue.sh, land_one.sh, regate.sh, merge_if_green.sh,
parallel-land.sh) have no pid lock (nothing stopped a second consumer), no
concurrency ceiling (parallel-land.sh's MAXJ was a manual override, not a
default), and no timeout class (a load timeout and a real gate failure printed
the same way). This file is those three controls around the SAME gate and
merge commands, unchanged.

HOW TO REPLACE THE LOOSE SCRIPTS WITHOUT CHANGING WHAT LANDS. Nothing about
regate.sh or merge_if_green.sh changes today; only what drives them does.

  1. Same queue file: ~/.claude/evidence/land-queue.txt, one PR number per
     line, unchanged format.
  2. Same log file: ~/.claude/evidence/serial-land.log gets the same
     "merge <n>: ..." and "LAND-<n>-END" lines it already has; a session
     reading that log does not need to know which driver wrote a given line.
  3. Point --gate and --merge at the existing scripts, with "{n}" standing in
     for the PR number (this file substitutes it, so do not redirect their
     output yourself; land_queue.py captures it and writes
     ~/.claude/evidence/gate-regate<n>.log itself, the same path
     merge_if_green.sh already reads):

       python3 scripts/land_queue.py run \\
         --queue ~/.claude/evidence/land-queue.txt \\
         --log   ~/.claude/evidence/serial-land.log \\
         --gate  "bash ~/.claude/evidence/regate.sh {n}" \\
         --merge "bash ~/.claude/evidence/merge_if_green.sh {n}" \\
         --concurrency 2

  4. Retire serial-queue.sh, land_one.sh and parallel-land.sh once this has
     run clean for a few PRs; regate.sh and merge_if_green.sh keep doing the
     gate-detail and merge-decision work they already do, "until those move
     too" per the row.

THE THREE CONTROLS.
  - A pid lock file (default <queue>.lock) refuses a second `run` while a
    live one holds it; a lock naming a dead pid is reclaimed and the reclaim
    is written to --log before work starts.
  - `--concurrency N` bounds how many gate commands run at once with a
    semaphore, not a batch: a slot frees the moment one gate finishes, so the
    next queued number starts immediately rather than waiting for a whole
    batch of N to land together.
  - A gate whose captured output carries "TimeoutExpired" or "timed out" and
    no "required_fast exit 0" line is a load timeout, not a verdict on the
    change: it is re-gated ALONE, after every other gate in this run has
    finished, before its result can be used to refuse anything.

Python 3, standard library only. No network calls of its own; --gate and
--merge are opaque shell commands this file merely runs and logs.

P3a EXTENSION (night 2026-09-07, docs/plan/runs/night-2026-09-07/design-P3.md
and codex-findings-P3.md). The legacy --gate/--merge templates above did not
read their own gate's verdict: a red gate still ran the --merge template,
which decided refusal itself by re-grepping the log land_queue had already
written. That is now closed for BOTH the legacy CLI path (classify_gate below
gates every merge-template invocation) and a new structured, exact-SHA,
authority-checked landing path (decide_land, ForgeAdapter, run_governed,
resume) that fixtures a full governed merge against a local bare remote,
never a real forge, per steering law 9 (no live autonomous merge into Brother
main tonight). merge_queue.py stays pure and untouched; this file is still
the only one that acts.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import autonomy_dial  # noqa: E402  the approval axis; merge_or_release is a structural A3 flag
try:
    from fable_authority import MERGE_ACTION  # noqa: E402  F1: one constant, not two spellings
except ImportError:
    MERGE_ACTION = "merge"  # fable_authority.py not present in this worktree yet

TIMEOUT_MARKERS = ("TimeoutExpired",)
TIMEOUT_SUBSTRING_CI = "timed out"
REQUIRED_FAST_GREEN = "required_fast exit 0"

# Structured gate verdict (P3a). Distinct words, never collapsed (law 2).
GATE_PASS, GATE_FAIL, GATE_NO_DATA, GATE_TIMEOUT_RED = (
    "PASS", "FAIL", "NO-DATA", "TIMEOUT-RED",
)

# Structured land decision (P3a). Also distinct, also never collapsed.
LAND_PASS = "PASS"
LAND_FAIL = "FAIL"
LAND_NO_DATA = "NO-DATA"
LAND_STALE = "STALE"
LAND_BASE_MOVED = "BASE-MOVED"
LAND_READY_FOR_HUMAN = "READY-FOR-HUMAN"
LAND_ALREADY_LANDED = "ALREADY-LANDED"
LAND_MERGED_UNVERIFIED = "MERGED-UNVERIFIED"


# ---------------------------------------------------------------- the lock --

def read_lock(lock_path):
    try:
        with open(lock_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_lock(lock_path, data):
    tmp = lock_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, lock_path)


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else: still alive
    except OSError:
        return False
    return True


def acquire_lock(lock_path):
    """Returns (ok, note). note is a reclaim message when a dead lock was
    replaced, empty otherwise. ok is False when a live pid already holds it."""
    held = read_lock(lock_path)
    note = ""
    if held:
        other = held.get("pid")
        if other != os.getpid() and pid_alive(other):
            return False, "land_queue: REFUSED to start, pid %s already holds %s" % (other, lock_path)
        if other and not pid_alive(other):
            note = "land_queue: reclaimed a dead lock (pid %s) at %s" % (other, lock_path)
    write_lock(lock_path, {"pid": os.getpid(), "since": time.time(), "inflight": []})
    return True, note


def release_lock(lock_path, expected_pid):
    held = read_lock(lock_path)
    if held and held.get("pid") == expected_pid:
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


# --------------------------------------------------------------- the queue --

def _peek_one(queue_path):
    """Head line's first token, WITHOUT removing it (F10: pop_and_queue
    needs to know the id before it may write anything, but must not
    remove the line from the queue until the land record naming that id
    is safely on disk). Returns None when the queue is empty."""
    if not os.path.exists(queue_path):
        return None
    with open(queue_path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                return line.split()[0]
    return None


def _remove_line(queue_path, n):
    """Remove exactly the first line whose first token is `n`, atomically
    (same tmp+rename pattern as write_lock/write_land_record)."""
    if not os.path.exists(queue_path):
        return
    with open(queue_path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines):
        if line.strip() and line.split()[0] == n:
            rest = lines[:i] + lines[i + 1:]
            tmp = queue_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(rest)
            os.replace(tmp, queue_path)
            return


def pop_one(queue_path):
    """Head line, rewrite atomically: the same contract serial-queue.sh
    keeps. Returns the first token of the first non-blank line, or None."""
    n = _peek_one(queue_path)
    if n is None:
        return None
    _remove_line(queue_path, n)
    return n


def queue_length(queue_path):
    if not os.path.exists(queue_path):
        return 0
    with open(queue_path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


# ------------------------------------------------------------- gate/merge --

def run_cmd(template, n):
    """Run a shell command template with {n} substituted for the PR number,
    capturing combined stdout+stderr. Returns (exit_code, text)."""
    cmd = template.format(n=n)
    proc = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode, proc.stdout or ""


def is_timeout_red(text):
    has_timeout = any(m in text for m in TIMEOUT_MARKERS) or TIMEOUT_SUBSTRING_CI in text.lower()
    return has_timeout and REQUIRED_FAST_GREEN not in text


def append_log(log_path, text):
    if not text.endswith("\n"):
        text += "\n"
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(text)


def write_gate_log(gate_dir, n, text):
    path = os.path.join(gate_dir, "gate-regate%s.log" % n)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def classify_gate(exit_code, text):
    """One gate run, read structurally instead of grepped ad hoc by whatever
    consumes the log (codex-findings-P3 #1). TIMEOUT-RED beats everything:
    it means "not a verdict yet", exactly the class run()'s existing lone
    re-gate loop already carves out before classification ever sees it, so
    a TIMEOUT-RED that classify_gate itself returns is one that SURVIVED
    that lone re-gate, a real refusal now, not more grace. Empty captured
    output is NO-DATA, never a silent pass (law 2): "an empty gate log is
    indistinguishable from a clean pass at the Brother layer today" is
    exactly the hole reproduction 2 in codex-findings-P3 names. Everything
    else is judged on the SAME marker merge_if_green.sh has always grepped
    for (REQUIRED_FAST_GREEN), so this is a structured reading of the
    existing contract, not a new one; exit_code is accepted for interface
    symmetry with run_cmd's own return shape but the marker is
    authoritative, matching the estate's existing grep-based verdict."""
    text = text or ""
    if is_timeout_red(text):
        return GATE_TIMEOUT_RED
    if exit_code != 0:
        # F5: a nonzero exit is a refusal regardless of what the captured
        # text happens to contain (a green marker echoed inside a quoted
        # refusal, or printed before the process was killed). exit_code
        # was already accepted for interface symmetry; it is now read.
        return GATE_FAIL
    if not text.strip():
        return GATE_NO_DATA
    if REQUIRED_FAST_GREEN in text:
        return GATE_PASS
    return GATE_FAIL


def decide_land(candidate, gate_verdict, head_now, base_now, authority, method=None):
    """The governed land decision (design-P3.md section 3, steering 9.3).
    Pure function, no I/O: `candidate` is a dict carrying at least
    `gated_head_sha`, `gated_base_sha` and `merged` (bool, for idempotent
    resume); `gate_verdict` is one of classify_gate's four words;
    `head_now`/`base_now` are freshly re-read immediately before merge
    (never reused from the gate-time read, steering 9.5); `authority` is a
    live scoped grant dict or None/falsy for "no grant".

    Order, each one closing off the next class of false-green (steering
    law 2): already-landed short-circuits everything so a resumed run
    never merges twice; then the gate verdict itself (FAIL and NO-DATA
    never merge, TIMEOUT-RED that reached here already survived its lone
    re-gate and is treated as FAIL, not given a second chance); then
    freshness (a gated SHA is not the SHA about to be merged); then base
    movement (the old gate proved nothing about the new base, steering
    9.6); only then authority, because a stale or failed candidate must
    never even reach the question of who is allowed to land it."""
    if candidate.get("merged"):
        return LAND_ALREADY_LANDED
    if gate_verdict == GATE_FAIL:
        return LAND_FAIL
    if gate_verdict in (GATE_NO_DATA, GATE_TIMEOUT_RED):
        return LAND_NO_DATA
    # gate_verdict == GATE_PASS from here.
    if (not candidate.get("gated_head_sha") or not head_now
            or not candidate.get("gated_base_sha") or not base_now):
        # F2: an unread revision (None, on any git failure) must never
        # compare equal to itself and read as fresh (None == None).
        return LAND_NO_DATA
    if head_now != candidate.get("gated_head_sha"):
        return LAND_STALE
    if base_now != candidate.get("gated_base_sha"):
        return LAND_BASE_MOVED
    if not authority:
        return LAND_READY_FOR_HUMAN
    if method is not None and authority.get("merge_method") != method:
        # F3/steering 9.7: a grant scoped to one merge method never
        # authorizes another.
        return LAND_READY_FOR_HUMAN
    risk_class = candidate.get("risk_class")
    if risk_class is not None:
        ceiling = authority.get("risk_ceiling")
        if (ceiling not in autonomy_dial.ORDER or risk_class not in autonomy_dial.ORDER
                or autonomy_dial.ORDER.index(ceiling) < autonomy_dial.ORDER.index(risk_class)):
            # F3/steering 9.7: a grant whose risk_ceiling sits below the
            # candidate's own class never authorizes it either.
            return LAND_READY_FOR_HUMAN
    return LAND_PASS


# ------------------------------------------------------------- land record --

def _slug(text):
    """Filesystem-safe id, the same substitution accept_delivery.slugify
    uses (scripts/accept_delivery.py), mirrored rather than imported so
    this module keeps its own dependency set small and stdlib-only."""
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())


def land_record_path(gate_dir, repo_slug, n):
    return os.path.join(gate_dir, "land-%s-%s.json" % (repo_slug, n))


def read_land_record(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_land_record(path, record):
    """Atomic tmp+rename, the same pattern write_lock already uses above,
    so a reader (including this module's own resume) never observes a
    half-written record."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def new_land_record(repository, n, base_branch, submission=None, gate_cmd=None,
                     merge_method="merge"):
    """The land record's full field list (design-P3.md section 3, steering
    9.3), reusing existing vocabulary rather than inventing a schema: PASS/
    FAIL/NO-DATA/... verdict words, `submission` is exactly merge_queue's
    own {id, branch, owns, check_cmd} shape, `delivery_receipt` is the
    (path, sha256) pair accept_delivery.record already stores."""
    submission = submission or {"id": n}
    return {
        "repository": repository,
        "submission": submission,
        "gated_head_sha": None,
        "gated_base_sha": None,
        "base_branch": base_branch,
        "gate_cmd": gate_cmd,
        "merge_method": merge_method,
        "delivery_receipt": None,
        "write_scope": submission.get("owns") or [],
        "required_checks": submission.get("check_cmd") or ([gate_cmd] if gate_cmd else []),
        "gate_results": [],
        "authority": None,
        "decision": None,
        "decided_by": "structured-adapter",
        "merge_result": {},
        "post_merge": {},
        "state": "QUEUED",
        "merged": False,
    }


def pop_and_queue(queue_path, gate_dir, repo_slug, repository, base_branch,
                   gate_cmd=None, merge_method="merge"):
    """Pop the next queued candidate id and write its land record as
    QUEUED immediately, before anything else happens to it (design-P3.md
    section 3's "written before pop_one returns"; steering 9.11's crash-
    before-gate case: a crash right here still leaves a named, recoverable
    record on disk instead of an id that just vanished from the queue
    file with nothing naming it). Returns (n, record_path), or (None,
    None) when the queue is empty. Idempotent: a record already on disk
    for this id (a resumed id that got re-popped) is read back rather
    than clobbered.

    F10: the record is written BEFORE the id's line is removed from the
    queue file (peek, then write, then remove), not literally before
    pop_one returns since pop_one is not called here at all any more; a
    crash between write and removal leaves BOTH the queue line and the
    QUEUED record on disk, and a re-run finds the same line still queued,
    re-derives the same record path, and reads the existing record back
    (the idempotent read above) rather than clobbering it, so this
    ordering costs nothing on the non-crash path."""
    n = _peek_one(queue_path)
    if n is None:
        return None, None
    path = land_record_path(gate_dir, repo_slug, n)
    record = read_land_record(path) or new_land_record(
        repository, n, base_branch, gate_cmd=gate_cmd, merge_method=merge_method)
    write_land_record(path, record)
    _remove_line(queue_path, n)
    return n, path


# --------------------------------------------------------------- adapters --

def _run_argv(cmd, cwd=None, env=None, timeout=120):
    """subprocess.run wrapper matching export_public.py's own `_run`
    shape (argv list, capture_output, text=True) so both real forge code
    and its test doubles share one calling convention."""
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 1, stdout="",
                                            stderr="TimeoutExpired: %s" % exc)


def _verify_merged(git_fn, gated_head_sha, merged_sha, base_branch, method):
    """Shared by every adapter's verify_merged (codex-findings-P3 #3).
    `git_fn(args)` runs one git subcommand (args WITHOUT the leading
    "git") against whatever repository the adapter owns and returns a
    CompletedProcess-like object.

    For a real "merge" commit, this is integrate.py's _already_integrated
    predicate: the gated head must be the SECOND parent of a commit
    reachable from base, never merely "any parent" (integrate.py's own
    docstring: an unmerged lane's tip IS the fork base, and the base
    becomes the FIRST parent of the next unrelated sibling merge, so
    "any parent" reads True for work nobody actually merged). Reimplemented
    here rather than imported because integrate._already_integrated walks
    the hardcoded ref "HEAD", while a land candidate's base is whatever
    `base_branch` names; the predicate (parts[2:], not parts[1:]) is
    identical, only the walked ref is parameterized.

    For "squash" or "rebase", which never carry the gated SHA as a parent
    at all, compare tree hashes instead (codex-findings-P3 #3 exactly).
    NO-DATA (None), never PASS, when the comparison itself could not even
    be attempted: never PASS for a squash you could not verify."""
    if method == "merge":
        walk = git_fn(["rev-list", "--parents", base_branch])
        if walk.returncode != 0:
            return None, "NO-DATA: could not walk %s (%s)" % (base_branch, (walk.stderr or "").strip())
        for line in (walk.stdout or "").splitlines():
            parts = line.split()
            if gated_head_sha in parts[2:]:
                return True, "second parent of a commit reachable from %s" % base_branch
        return False, "gated head is not the second parent of any commit reachable from %s" % base_branch
    if method in ("squash", "rebase"):
        if not merged_sha:
            return None, "NO-DATA: no merged_sha to compare a tree against yet"
        a = git_fn(["rev-parse", "%s^{tree}" % merged_sha])
        b = git_fn(["rev-parse", "%s^{tree}" % gated_head_sha])
        if a.returncode != 0 or b.returncode != 0:
            return None, "NO-DATA: could not read a tree hash to compare"
        matched = (a.stdout or "").strip() == (b.stdout or "").strip()
        return matched, "tree hash %s" % ("matched" if matched else "did not match")
    return None, "NO-DATA: unknown merge method %r" % method


class ForgeAdapter(object):
    """The narrow interface every land adapter implements (steering 12:
    "do not make land_queue understand every forge"). One real
    implementation (GhAdapter) and one test double (GitFixtureAdapter);
    land_queue calls only these five methods, never a forge-specific
    detail directly."""

    def head_sha(self, ref):
        raise NotImplementedError

    def base_sha(self, base_branch):
        raise NotImplementedError

    def state(self, ref):
        raise NotImplementedError

    def merge(self, gated_head_sha, base_branch, expected_base_sha, method="merge", pr_ref=None):
        """Returns (ok, merged_sha_or_None, reason). Must merge the
        IMMUTABLE gated_head_sha, never a branch name that could have
        moved (codex-findings-P3 #2), and must fail rather than overwrite
        when expected_base_sha no longer matches the real base
        (compare-and-swap, same finding)."""
        raise NotImplementedError

    def verify_merged(self, gated_head_sha, merged_sha, base_branch, method):
        """Returns (True/False/None, reason). None means NO-DATA: the
        check could not be attempted, never treated as PASS."""
        raise NotImplementedError


class GhAdapter(ForgeAdapter):
    """The real forge adapter: gh + git argv lists, exactly as
    export_public.py builds its own (GH_BIN, argv lists, an injectable
    `run`), so no test here executes a real subprocess or touches a real
    pull request (steering law 9: no live autonomous merge into Brother
    main tonight; this class is capability, not tonight's activation).
    `cwd` is a real local clone with `base_branch` fetched, exactly the
    working assumption land_queue.py already runs under today."""

    GH_BIN = "gh"

    def __init__(self, repo, cwd=None, run=None):
        self.repo = repo
        self.cwd = cwd
        self._run = run or _run_argv

    def head_sha(self, ref):
        proc = self._run(["git", "rev-parse", ref], self.cwd)
        return (proc.stdout or "").strip() if proc.returncode == 0 else None

    def base_sha(self, base_branch):
        return self.head_sha(base_branch)

    def state(self, ref):
        proc = self._run([self.GH_BIN, "pr", "view", str(ref), "-R", self.repo,
                           "--json", "state"], self.cwd)
        if proc.returncode != 0:
            return "NO-DATA"
        try:
            return (json.loads(proc.stdout or "{}") or {}).get("state", "NO-DATA")
        except ValueError:
            return "NO-DATA"

    def merge(self, gated_head_sha, base_branch, expected_base_sha, method="merge", pr_ref=None):
        # --match-head-commit merges the exact SHA gh has recorded as the
        # pull request's head; gh refuses the merge outright if the live
        # head has moved since (gh pr merge --help: "Commit SHA that the
        # pull request head must match to allow merge"), which is the
        # immutable-SHA merge codex-findings-P3 #2 asks for on this path.
        if not gated_head_sha:
            # F2: an unread head SHA must refuse, not build an argv list
            # containing None and raise a TypeError deep inside subprocess.
            return False, None, "cannot merge: missing gated_head_sha"
        flag = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}.get(method, "--merge")
        cmd = [self.GH_BIN, "pr", "merge", str(pr_ref if pr_ref is not None else gated_head_sha),
               "-R", self.repo, flag, "--match-head-commit", gated_head_sha]
        proc = self._run(cmd, self.cwd)
        if proc.returncode != 0:
            return False, None, ((proc.stdout or "") + (proc.stderr or "")).strip()
        return True, self.head_sha(base_branch), "gh pr merge exit 0"

    def verify_merged(self, gated_head_sha, merged_sha, base_branch, method):
        def git_fn(args):
            return self._run(["git"] + args, self.cwd)
        return _verify_merged(git_fn, gated_head_sha, merged_sha, base_branch, method)


class GitFixtureAdapter(ForgeAdapter):
    """The test double: a local bare remote standing in for a real forge
    (design-P3.md section 4, steering 9.12), so the landing fixture needs
    no network and no real pull request. `remote_dir` is a bare repo.

    merge() builds the merge/squash/rebase commit with `git commit-tree`
    against `expected_base_sha` (the freshness re-read the caller just
    took), then writes it with `git update-ref refs/heads/<base> <new>
    <expected_base_sha>`: git's own compare-and-swap, which REFUSES the
    write (nonzero exit, ref left untouched) the moment the real ref no
    longer matches expected_base_sha, closing the check-then-merge race
    codex-findings-P3 #2 names. `race_hook`, when set, is called after the
    new commit object is built but immediately before that update-ref, so
    a test can simulate a concurrent push landing in exactly that window."""

    def __init__(self, remote_dir, run=None):
        self.remote_dir = remote_dir
        self._run = run or _run_argv
        self.race_hook = None

    def _git(self, args):
        return self._run(["git", "--git-dir=%s" % self.remote_dir] + args)

    def head_sha(self, ref):
        proc = self._git(["rev-parse", ref])
        return (proc.stdout or "").strip() if proc.returncode == 0 else None

    def base_sha(self, base_branch):
        return self.head_sha(base_branch)

    def state(self, ref):
        return "open" if self.head_sha(ref) else "missing"

    def merge(self, gated_head_sha, base_branch, expected_base_sha, method="merge", pr_ref=None):
        head_tree_proc = self._git(["rev-parse", "%s^{tree}" % gated_head_sha])
        if head_tree_proc.returncode != 0:
            return False, None, "cannot read gated head's tree: %s" % gated_head_sha
        head_tree = head_tree_proc.stdout.strip()
        if method == "merge":
            args = ["commit-tree", head_tree, "-p", expected_base_sha, "-p", gated_head_sha,
                     "-m", "land_queue fixture merge"]
        else:  # squash, rebase: single parent, gated head's own tree verbatim
            args = ["commit-tree", head_tree, "-p", expected_base_sha,
                     "-m", "land_queue fixture %s" % method]
        proc = self._git(args)
        if proc.returncode != 0:
            return False, None, "commit-tree failed: %s" % ((proc.stderr or proc.stdout or "").strip())
        new_sha = proc.stdout.strip()
        if self.race_hook is not None:
            self.race_hook()
        cas = self._git(["update-ref", "refs/heads/%s" % base_branch, new_sha, expected_base_sha])
        if cas.returncode != 0:
            return False, None, "compare-and-swap refused, base moved: %s" % ((cas.stderr or cas.stdout or "").strip())
        return True, new_sha, "merged"

    def verify_merged(self, gated_head_sha, merged_sha, base_branch, method):
        return _verify_merged(self._git, gated_head_sha, merged_sha, base_branch, method)


# --------------------------------------------------------------- authority --

def resolve_authority(repository, base_branch, action=None,
                       authority_provider=None, now=None):
    """The authority half of decide_land's inputs (design-P3.md section 3,
    steering 9.7). autonomy_dial.gate is consulted first as the floor:
    merge_or_release is a structural A3 flag (autonomy_dial.A3_FLAGS), so
    it always returns "refuse_until_approved" regardless of the dial;
    that call is made anyway rather than assumed, so a future policy
    change to autonomy_dial cannot silently widen this seam by omission.
    Only a LIVE scoped delegation lifts that one instance.

    Returns a grant dict (repository/base/action/... , a dict means
    granted) or None (no grant, authority NO-DATA). `authority_provider`,
    when given, REPLACES the lazy `fable_authority.delegation_for` import
    entirely: this is the seam P3a's own tests use so they never depend
    on P3b's fable_authority.py landing in this worktree. Until P3b lands
    that function, `from fable_authority import delegation_for` raises
    ImportError, caught below and treated as authority NO-DATA, exactly
    like no grant at all, never as "allowed" (law 2: never let unknown
    authority become allowed)."""
    action = MERGE_ACTION if action is None else action
    resolve_authority.last_error = None
    floor = autonomy_dial.gate({"merge_or_release": True})
    if floor != "refuse_until_approved":
        # merge_or_release is a structural A3 flag; this branch is
        # unreachable under the current policy and exists only so a
        # future policy change cannot silently grant authority by
        # omission (never invent a permissive default here).
        return None
    if authority_provider is not None:
        try:
            return authority_provider(repository, base_branch, action, now)
        except Exception as exc:  # noqa: BLE001  F7: broad by design
            # Any exception from a provider (or, below, from the real
            # fable_authority import/call) is NO-DATA, never propagated
            # and never silent: the exception's own class name is left on
            # this function so the caller can fold it into the record's
            # authority field instead of losing it.
            resolve_authority.last_error = exc.__class__.__name__
            return None
    try:
        from fable_authority import delegation_for
    except ImportError as exc:
        resolve_authority.last_error = exc.__class__.__name__
        return None
    try:
        return delegation_for(repository, base_branch, action, now=now)
    except Exception as exc:  # noqa: BLE001  F7: broad by design, see above
        resolve_authority.last_error = exc.__class__.__name__
        return None


resolve_authority.last_error = None


# ---------------------------------------------------------- governed land --

def _gate_once(gate_dir, gate_tpl, n, path, record):
    """Run one gate attempt for a structured candidate, classify it, and
    persist the attempt on the record. Returns (verdict, code, text)."""
    code, text = run_cmd(gate_tpl, n)
    write_gate_log(gate_dir, n, text)
    verdict = classify_gate(code, text)
    record.setdefault("gate_results", []).append(
        {"attempt": len(record.get("gate_results", [])) + 1, "exit_code": code, "verdict": verdict})
    write_land_record(path, record)
    return verdict, code, text


def _decide_and_merge(record, path, adapter, repository, base_branch, method,
                       gate_verdict, log_path, authority_provider, submission,
                       n):
    """The shared tail of run_governed's per-candidate body and resume():
    freshness re-read, authority, decide_land, then merge and post-merge
    verification if PASS. Mutates and writes `record`; returns the final
    decision word."""
    ref = submission.get("branch", n)
    head_now = adapter.head_sha(ref)
    base_now = adapter.base_sha(base_branch)
    authority = resolve_authority(repository, base_branch, MERGE_ACTION, authority_provider)
    if authority:
        record["authority"] = dict(authority)
    else:
        reason = "no live delegation"
        err = getattr(resolve_authority, "last_error", None)
        if err:
            # F7: an exception from the authority provider is folded into
            # the record instead of only living on a function attribute.
            reason = "resolve_authority raised %s, treated as NO-DATA" % err
        record["authority"] = {"status": "NO-DATA", "reason": reason}

    candidate = {
        "gated_head_sha": record.get("gated_head_sha"),
        "gated_base_sha": record.get("gated_base_sha"),
        "merged": bool(record.get("merged")),
        "risk_class": record.get("risk_class"),
    }
    if authority and candidate["risk_class"] is None:
        # F3: no candidate anywhere in this estate sets risk_class yet, so
        # the risk_ceiling half of the grant can never be verified. That
        # is recorded as its own NO-DATA (never silently read as
        # verified) rather than blocking the merge_method-checked path
        # below on a field nothing produces.
        record["authority"]["risk_check"] = "NO-DATA: candidate risk class not tracked"
    decision = decide_land(candidate, gate_verdict, head_now, base_now, authority, method=method)
    record["decision"] = decision

    if decision != LAND_PASS:
        record["state"] = decision
        write_land_record(path, record)
        if log_path:
            append_log(log_path, "land %s: %s, not merged" % (n, decision))
        return decision

    ok_m, merged_sha, reason = adapter.merge(
        record.get("gated_head_sha"), base_branch, base_now, method=method,
        pr_ref=submission.get("pr_ref", n))
    record["merge_result"] = {"merge_command_succeeded": ok_m, "merged_sha": merged_sha, "reason": reason}
    if not ok_m:
        record["state"] = "MERGE-FAILED"
        write_land_record(path, record)
        if log_path:
            append_log(log_path, "land %s: merge command failed: %s" % (n, reason))
        return "MERGE-FAILED"

    verified, vreason = adapter.verify_merged(record.get("gated_head_sha"), merged_sha, base_branch, method)
    record["post_merge"] = {"merged_revision_verified": verified, "reason": vreason}
    # F8/steering 9.10: "merge command succeeded" and "merged revision
    # verified" are different facts. merged stays True either way (the
    # merge command really did land something and must never be attempted
    # a second time by resume), but the OUTCOME WORD only ever says PASS
    # when the revision was actually verified; None (could not check) or
    # False (checked and it did not match) both read as MERGED-UNVERIFIED.
    record["merged"] = True
    if verified is True:
        record["state"] = "MERGED"
        write_land_record(path, record)
        if log_path:
            append_log(log_path, "land %s: MERGED %s" % (n, merged_sha))
            append_log(log_path, "LAND-%s-END" % n)
        return LAND_PASS
    record["state"] = LAND_MERGED_UNVERIFIED
    write_land_record(path, record)
    if log_path:
        append_log(log_path, "land %s: MERGED %s but unverified: %s" % (n, merged_sha, vreason))
        append_log(log_path, "LAND-%s-END" % n)
    return LAND_MERGED_UNVERIFIED


def run_governed(queue_path, log_path, gate_tpl, repository, base_branch,
                  merge_method, adapter, gate_dir=None, lock_path=None,
                  authority_provider=None, submissions=None):
    """The structured, exact-SHA, authority-checked landing path (steering
    P3, design-P3.md section 3). One land record per candidate at
    <gate_dir>/land-<repo_slug>-<n>.json. `submissions`, when given, maps
    candidate id -> a merge_queue submission dict ({id, branch, owns,
    check_cmd}); a candidate with no matching submission falls back to
    treating its queue id as both the branch ref and the check target.

    Sequential by design (ponytail: this path's job tonight is a correct
    decision per candidate, not concurrent gating; add a semaphore here
    the same way run() has one if this path ever needs concurrency).
    Shares the SAME pid lock file convention as run() so a legacy run and
    a governed run never race the same queue."""
    lock_path = lock_path or (queue_path + ".lock")
    gate_dir = gate_dir or (os.path.dirname(os.path.abspath(log_path)) or ".")
    repo_slug = _slug(repository)
    submissions = submissions or {}

    ok, note = acquire_lock(lock_path)
    if not ok:
        print(note)
        return 3
    if note:
        append_log(log_path, note)

    outcomes = []
    try:
        while True:
            n, path = pop_and_queue(queue_path, gate_dir, repo_slug, repository,
                                     base_branch, gate_cmd=gate_tpl, merge_method=merge_method)
            if n is None:
                break
            record = read_land_record(path)
            submission = submissions.get(n, {"id": n, "branch": n})
            record["submission"] = submission
            record["write_scope"] = submission.get("owns") or []
            record["required_checks"] = submission.get("check_cmd") or [gate_tpl]
            receipt_path = submission.get("receipt_path")
            if receipt_path:
                try:
                    with open(receipt_path, "rb") as fh:
                        digest = hashlib.sha256(fh.read()).hexdigest()
                    record["delivery_receipt"] = {"path": receipt_path, "sha256": digest}
                except OSError:
                    record["delivery_receipt"] = None
            record["gated_head_sha"] = adapter.head_sha(submission.get("branch", n))
            record["gated_base_sha"] = adapter.base_sha(base_branch)
            record["state"] = "GATED-PENDING"
            write_land_record(path, record)

            verdict, code, text = _gate_once(gate_dir, gate_tpl, n, path, record)
            if verdict == GATE_TIMEOUT_RED:
                append_log(log_path, "TIMEOUT-RED %s: load timeout, not a verdict, re-gating alone" % n)
                verdict, code, text = _gate_once(gate_dir, gate_tpl, n, path, record)

            decision = _decide_and_merge(record, path, adapter, repository, base_branch,
                                          merge_method, verdict, log_path, authority_provider,
                                          submission, n)
            outcomes.append((n, decision))
    finally:
        release_lock(lock_path, os.getpid())
    return outcomes


def resume(gate_dir, repository, base_branch, adapter, authority_provider=None,
           log_path=None):
    """Idempotent recovery over every land record for `repository` that is
    not already terminal (steering 9.11's four crash cases). For each:

      1. Ask the forge directly whether the gated head already landed
         (adapter.verify_merged with method "merge", the second-parent
         predicate), BEFORE trusting anything the record itself claims.
         True means ALREADY-LANDED and the record is closed with no
         second merge attempted, covering "crash immediately after remote
         merge" even when the local record never got to say so.
         # ponytail: this pre-check only covers method "merge"; a squash
         # or rebase resumed after a remote-side crash needs its
         # merged_sha recorded before the crash to be re-verified by
         # tree hash, which a crash between "merge succeeded" and
         # "record the merged_sha" would still lose. Upgrade if a squash/
         # rebase governed run is put into real use.
      2. No gate result recorded at all: NO-DATA, requeue by hand
         (covers "crash before gate" and "crash during gate": the id was
         already popped from the queue file, so nothing else will pick
         it up).
      3. A gate result exists: re-run the full freshness+authority
         decision fresh (never trust the OLD decision word, steering 9.5)
         and merge if PASS (covers "crash after gate before merge").

    Returns [(n, outcome_word), ...]."""
    repo_slug = _slug(repository)
    outcomes = []
    if not os.path.isdir(gate_dir):
        return outcomes
    prefix = "land-%s-" % repo_slug
    for name in sorted(os.listdir(gate_dir)):
        if not (name.startswith(prefix) and name.endswith(".json")):
            continue
        path = os.path.join(gate_dir, name)
        record = read_land_record(path)
        n = name[len(prefix):-len(".json")]
        if not record:
            outcomes.append((n, "NO-DATA: record unreadable"))
            continue
        if record.get("merged"):
            outcomes.append((n, LAND_ALREADY_LANDED))
            continue

        gated_head = record.get("gated_head_sha")
        method = record.get("merge_method", "merge")
        submission = record.get("submission") or {"id": n, "branch": n}

        if gated_head:
            already, reason = adapter.verify_merged(gated_head, None, base_branch, "merge")
            if already:
                record["decision"] = LAND_ALREADY_LANDED
                record["state"] = "MERGED"
                record["merged"] = True
                record["post_merge"] = {"merged_revision_verified": True, "reason": "resume: " + reason}
                write_land_record(path, record)
                outcomes.append((n, LAND_ALREADY_LANDED))
                continue

        gate_results = record.get("gate_results") or []
        if not gated_head or not gate_results:
            record["state"] = "NO-DATA"
            write_land_record(path, record)
            outcomes.append((n, "NO-DATA: never completed a gate, requeue by hand"))
            continue

        last_verdict = gate_results[-1]["verdict"]
        decision = _decide_and_merge(record, path, adapter, repository, base_branch,
                                      method, last_verdict, log_path, authority_provider,
                                      submission, n)
        outcomes.append((n, decision))
    return outcomes


# ------------------------------------------------------------------- run --

def run(queue_path, log_path, gate_tpl, merge_tpl, concurrency=1, lock_path=None):
    lock_path = lock_path or (queue_path + ".lock")
    gate_dir = os.path.dirname(os.path.abspath(log_path)) or "."
    stop_path = lock_path + ".stop"

    ok, note = acquire_lock(lock_path)
    if not ok:
        print(note)
        return 3
    if note:
        append_log(log_path, note)
        print(note)

    io_lock = threading.Lock()
    inflight = set()

    def publish_inflight():
        with io_lock:
            data = read_lock(lock_path) or {"pid": os.getpid()}
            data["inflight"] = sorted(inflight, key=str)
            write_lock(lock_path, data)

    results = {}
    order = []
    sem = threading.Semaphore(max(1, concurrency))
    threads = []

    def worker(n):
        try:
            code, text = run_cmd(gate_tpl, n)
            results[n] = (code, text)
            write_gate_log(gate_dir, n, text)
        finally:
            inflight.discard(n)
            publish_inflight()
            sem.release()

    try:
        while not os.path.exists(stop_path):
            n = pop_one(queue_path)
            if n is None:
                break
            order.append(n)
            sem.acquire()
            inflight.add(n)
            publish_inflight()
            t = threading.Thread(target=worker, args=(n,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        # A load timeout is not a refusal until it fails ALONE: re-gate every
        # TIMEOUT-RED number by itself, after everything else in this run has
        # finished, before its result can decide a merge.
        for n in order:
            code, text = results.get(n, (1, ""))
            if is_timeout_red(text):
                append_log(
                    log_path,
                    "TIMEOUT-RED %s: load timeout, not a verdict, re-gating alone" % n,
                )
                code2, text2 = run_cmd(gate_tpl, n)
                results[n] = (code2, text2)
                write_gate_log(gate_dir, n, text2)

        # Merge in queue order, but ONLY when this run's own gate verdict
        # was PASS (codex-findings-P3 #1): a red or NO-DATA gate never
        # even reaches the --merge template any more, closing the
        # fail-open hole where the template's own grep decided refusal
        # from a log it had no way to know was never green.
        for n in order:
            code, text = results.get(n, (1, ""))
            verdict = classify_gate(code, text)
            if verdict != GATE_PASS:
                append_log(log_path, "land %s: gate verdict %s, merge template not invoked" % (n, verdict))
                append_log(log_path, "LAND-%s-END" % n)
                continue
            _, mtext = run_cmd(merge_tpl, n)
            append_log(log_path, mtext)
            append_log(log_path, "LAND-%s-END" % n)
    finally:
        try:
            if os.path.exists(stop_path):
                os.remove(stop_path)
        except FileNotFoundError:
            pass
        release_lock(lock_path, os.getpid())
    return 0


# ------------------------------------------------------------------- CLI --

def cmd_run(args):
    if args.repo:
        adapter = GhAdapter(args.repo, cwd=os.getcwd())
        outcomes = run_governed(args.queue, args.log, args.gate, args.repo, args.base,
                                 args.merge_method, adapter, lock_path=args.lock)
        if isinstance(outcomes, int):  # lock refusal returns an int code
            return outcomes
        for n, decision in outcomes:
            print("land %s: %s" % (n, decision))
        return 0
    if not args.merge:
        print("land_queue run: --merge is required unless --repo is given (legacy path)")
        return 2
    return run(args.queue, args.log, args.gate, args.merge, args.concurrency, args.lock)


def cmd_resume(args):
    adapter = GhAdapter(args.repo, cwd=os.getcwd())
    outcomes = resume(args.gate_dir, args.repo, args.base, adapter)
    for n, decision in outcomes:
        print("resume %s: %s" % (n, decision))
    return 0


def cmd_status(args):
    lock_path = args.lock or (args.queue + ".lock")
    qlen = queue_length(args.queue)
    held = read_lock(lock_path)
    if not held:
        print("land_queue status: no lock holder, queue length %d" % qlen)
        return 0
    pid = held.get("pid")
    alive = pid_alive(pid)
    inflight = held.get("inflight") or []
    print(
        "land_queue status: pid %s (%s), in-flight %s, queue length %d"
        % (pid, "running" if alive else "dead (stale lock)",
           ",".join(str(x) for x in inflight) or "none", qlen)
    )
    return 0


def cmd_stop(args):
    lock_path = args.lock or (args.queue + ".lock")
    held = read_lock(lock_path)
    if not held or not pid_alive(held.get("pid")):
        print("land_queue stop: no live holder to stop")
        return 1
    with open(lock_path + ".stop", "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    print("land_queue stop: asked pid %s to finish its current gate and exit" % held.get("pid"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="land_queue: one pid-locked consumer for the hub PR landing "
                     "queue, a gate concurrency ceiling, and a timeout class that "
                     "re-gates alone before it can refuse a PR (row M6). --gate and "
                     "--merge are legacy, operator-configured shell templates; pass "
                     "--repo (with --merge-method and --base) for the structured, "
                     "exact-SHA, authority-checked landing path instead of --merge."
    )
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="drain the queue: gate, classify, merge")
    r.add_argument("--queue", required=True)
    r.add_argument("--log", required=True)
    r.add_argument("--gate", required=True, help='shell command template, "{n}" is the PR number')
    r.add_argument("--merge", required=False, default=None,
                    help='LEGACY, operator-configured shell command template, "{n}" is the PR '
                         'number; required unless --repo is given')
    r.add_argument("--concurrency", type=int, default=1)
    r.add_argument("--lock", default=None, help="default: <queue>.lock")
    r.add_argument("--repo", default=None,
                    help="structured path: owner/name on the forge; enables the exact-SHA, "
                         "authority-checked adapter path instead of --merge")
    r.add_argument("--merge-method", dest="merge_method", default="merge",
                    choices=["merge", "squash", "rebase"])
    r.add_argument("--base", default="main", help="base branch name for the structured path")

    p = sub.add_parser("resume", help="idempotent recovery over land records after a crash")
    p.add_argument("--gate-dir", dest="gate_dir", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--base", default="main")

    s = sub.add_parser("status", help="lock holder, in-flight gates, queue length")
    s.add_argument("--queue", required=True)
    s.add_argument("--lock", default=None)

    st = sub.add_parser("stop", help="ask the holder to finish its current gate and exit")
    st.add_argument("--queue", required=True)
    st.add_argument("--lock", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "resume":
        return cmd_resume(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "stop":
        return cmd_stop(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
