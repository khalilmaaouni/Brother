#!/usr/bin/env python3
"""linux_canary: the Linux canary run on the export tree, its record, the minutes stop and the covering lookup (LX1.a).

A manual dispatch of `required-fast.yml` on the private hub runs exactly one job, `required-fast-canary`, and skips
`required-fast`. That shape, and only that shape, is PASS. A record binds one run to one export tree id; `covering`
answers whether a PASS record exists for exactly this tree, recent and re-judged from its own jobs.

`python3 scripts/linux_canary.py --covering TREE [--version V]` prints the covering record path and exits 0, or
prints the reason and exits 1. `python3 scripts/linux_canary.py --version V [--root R]` is the dispatching mode
(LX1.a2): it builds the export tree, reuses a covering PASS record at zero minutes, stops at 1,800 private minutes,
pushes the tree to the hub branch canary/<tree12>-<sha7> (never forced), dispatches required-fast.yml there, waits,
deletes the branch while its tip is still the pushed commit, records the outcome and prints one last line
`CANARY <verdict> <record path or -> <detail>`; exit 0 PASS, 1 FAIL, 2 NO-DATA. SIGTERM or SIGINT runs the same
cleanup and records NO-DATA `interrupted`.

Every unknown, corrupt or missing input is a reason or NO-DATA, never PASS. Python 3.9, standard library only. No
network in a test: every gh and git call goes through an injected callable.
"""
import argparse
import base64
import calendar
import datetime
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from typing import Callable, Dict, List, Optional, Tuple

SCHEMA: str = "cv1.canary.1"
HUB_REMOTE: str = "hub"
WORKFLOW: str = "required-fast.yml"
EXPECTED_JOBS: Dict[str, str] = {"required-fast-canary": "success", "required-fast": "skipped"}
FREE_MINUTES: int = 2000
MINUTES_STOP: int = 1800
LINUX_PER_MIN: float = 0.006
MAX_AGE_HOURS: float = 24.0
FUTURE_SLACK_S = 300
# repositories whose Actions minutes are free (public); every other repository counts against the private quota
PUBLIC_REPOS = ("Brother",)

Gh = Callable[[List[str]], Tuple[int, str, str]]            # (return code, stdout, stderr), the shape github_cost_guard.gh has

VERDICTS = ("PASS", "FAIL", "NO-DATA")
CANARY_FAILED = ("failure", "cancelled", "timed_out")
RECORD_FIELDS = ("schema", "version", "tree", "pushed_sha", "branch", "run_id", "run_url", "jobs", "verdict", "detail",
                 "minutes_before", "started_at", "finished_at", "seconds")
SHA_RE = re.compile(r"[0-9a-f]{40}")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
LOGIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
NAME_RE = re.compile(r"linux-canary-([0-9]+\.[0-9]+\.[0-9]+)-([0-9a-f]{12})-([0-9]+)\.json")
HTTPS_RE = re.compile(r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?")
SSH_RE = re.compile(r"git@github\.com:([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?")


def evidence_dir():
    """The cut evidence folder, by the same rule as cut_rehearsal.evidence_dir (scripts/cut_rehearsal.py line 53):
    BROTHER_CUT_EVIDENCE_DIR only when BROTHER_CUT_TEST=1 is also set, else ~/.claude/evidence/cut. Kept here
    because cut_rehearsal imports a process module this module must not reach."""
    over = os.environ.get("BROTHER_CUT_EVIDENCE_DIR", "")
    if over and os.environ.get("BROTHER_CUT_TEST") == "1":
        return os.path.abspath(over)
    return os.path.join(os.path.expanduser("~"), ".claude", "evidence", "cut")


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


def hub_slug(remote_url: str) -> Tuple[str, str]:
    """("OWNER/REPO", '') for https://github.com/OWNER/REPO(.git) or git@github.com:OWNER/REPO(.git); ('', reason)
    for anything else."""
    if not _is_str(remote_url):
        return "", "the remote url is not a string"
    text = remote_url.strip()
    for rx in (HTTPS_RE, SSH_RE):
        m = rx.fullmatch(text)
        if m and m.group(2) not in (".", ".."):
            return "%s/%s" % (m.group(1), m.group(2)), ""
    return "", "the remote url %r is not a github.com https or ssh url" % (text[:200],)


def _gh_call(gh, argv):
    """(stdout, '') or ('', reason) for one gh call; a reply of the wrong shape is a reason."""
    reply = gh(argv)
    if not isinstance(reply, tuple) or len(reply) != 3:
        return "", "gh %s returned %r, not (rc, stdout, stderr)" % (" ".join(argv), type(reply).__name__)
    rc, out, err = reply
    if not isinstance(rc, int) or isinstance(rc, bool) or not _is_str(out) or not _is_str(err):
        return "", "gh %s returned a reply of the wrong types" % " ".join(argv)
    if rc != 0:
        return "", "gh %s exited %d: %s" % (" ".join(argv), rc, err.strip()[:300])
    return out, ""


def private_minutes(gh: Gh, today: Optional[datetime.date] = None) -> Tuple[Optional[float], str]:
    """(private Linux equivalent minutes this month, '') or (None, reason). The sum is the cost guard's: usageItems with
    product actions, unit Minutes, a repository outside PUBLIC_REPOS, grossAmount over LINUX_PER_MIN. At or above
    MINUTES_STOP the answer is (None, reason): no number to dispatch on."""
    if not callable(gh):
        return None, "NO-DATA: no gh to ask"
    if today is None:
        today = datetime.datetime.now(datetime.timezone.utc).date()
    if isinstance(today, datetime.datetime) or not isinstance(today, datetime.date):
        return None, "NO-DATA: the month %r is not a date" % (today,)
    out, why = _gh_call(gh, ["api", "user", "--jq", ".login"])
    if why:
        return None, "NO-DATA: " + why
    login = out.strip()
    if not LOGIN_RE.fullmatch(login):
        return None, "NO-DATA: gh api user named no login (%r)" % (login[:60],)
    path = "/users/%s/settings/billing/usage?year=%d&month=%d" % (login, today.year, today.month)
    out, why = _gh_call(gh, ["api", path])
    if why:
        # a token without the user scope is refused here: the guard reads that as NO-DATA, so does this
        return None, "NO-DATA: " + why
    try:
        data = json.loads(out)
    except ValueError as exc:
        return None, "NO-DATA: the billing usage is not JSON (%s)" % exc
    if not isinstance(data, dict) or not isinstance(data.get("usageItems"), list):
        return None, "NO-DATA: the billing usage has no usageItems list"
    gross = 0.0
    for item in data["usageItems"]:
        if not isinstance(item, dict):
            return None, "NO-DATA: a usage item is not an object"
        product, unit, repo = item.get("product"), item.get("unitType"), item.get("repositoryName")
        if not _is_str(product) or not _is_str(unit):
            return None, "NO-DATA: a usage item has no product or unit"
        if product.lower() != "actions" or unit.lower() != "minutes":
            continue
        if _is_str(repo) and repo in PUBLIC_REPOS:
            continue
        amount = item.get("grossAmount")
        if not _is_num(amount) or amount < 0:
            return None, "NO-DATA: a usage item has grossAmount %r" % (amount,)
        gross += float(amount)
    minutes = round(gross / LINUX_PER_MIN, 6)   # 1794 minutes is 10.764 dollars, which divides back to 1793.99...
    if not math.isfinite(minutes):
        return None, "NO-DATA: the minutes figure is not finite"
    if minutes >= MINUTES_STOP:
        return None, "STOP: %.0f of %d free private minutes used this month, at or above the %d stop" % (
            minutes, FREE_MINUTES, MINUTES_STOP)
    return minutes, ""


def judge_jobs(jobs: Dict[str, str]) -> Tuple[str, str]:
    """(PASS | FAIL | NO-DATA, detail) for a run's job name to conclusion map. PASS only for EXPECTED_JOBS exactly."""
    if not isinstance(jobs, dict) or not jobs:
        return "NO-DATA", "no jobs to judge"
    if not all(_is_str(k) and _is_str(v) for k, v in jobs.items()):
        return "NO-DATA", "a job name or conclusion is not a string"
    if all(v == "skipped" for v in jobs.values()):
        return "NO-DATA", ("every job was skipped: github.event.repository.private read empty in the dispatch, so "
                           "the canary premise is unproven")
    if jobs == EXPECTED_JOBS:
        return "PASS", "required-fast-canary success, required-fast skipped"
    canary = jobs.get("required-fast-canary")
    if canary in CANARY_FAILED:
        return "FAIL", "required-fast-canary concluded %s" % canary
    return "NO-DATA", "the job shape %s is not the canary shape %s" % (sorted(jobs.items()),
                                                                       sorted(EXPECTED_JOBS.items()))


def _record_problem(data):
    """'' when `data` is a well formed record, else the problem in words."""
    if not isinstance(data, dict):
        return "the record is not an object"
    if data.get("schema") != SCHEMA:
        return "schema %r, not %s" % (data.get("schema") if _is_str(data.get("schema")) else None, SCHEMA)
    bad = [f for f in RECORD_FIELDS if f not in data]
    if bad:
        return "missing " + ", ".join(bad)
    if not _is_str(data["version"]) or not VERSION_RE.fullmatch(data["version"]):
        return "version is not X.Y.Z"
    if not _is_str(data["tree"]) or not SHA_RE.fullmatch(data["tree"]):
        return "tree is not 40 hex"
    if not _is_str(data["pushed_sha"]) or not SHA_RE.fullmatch(data["pushed_sha"]):
        return "pushed_sha is not 40 hex"
    for field in ("branch", "run_url", "detail"):
        if not _is_str(data[field]):
            return "%s is not a string" % field
    if not (_is_str(data["run_id"]) or (isinstance(data["run_id"], int) and not isinstance(data["run_id"], bool))):
        return "run_id is not an id"
    if not isinstance(data["jobs"], dict) or not all(_is_str(k) and _is_str(v) for k, v in data["jobs"].items()):
        return "jobs is not a name to conclusion map"
    if not _is_str(data["verdict"]) or data["verdict"] not in VERDICTS:
        return "verdict is not one of %s" % ", ".join(VERDICTS)
    if data["minutes_before"] is not None and not _is_num(data["minutes_before"]):
        return "minutes_before is not a number"
    if not _is_num(data["seconds"]) or data["seconds"] < 0:
        return "seconds is not a finite number"
    if _parse_iso(data["started_at"]) is None or _parse_iso(data["finished_at"]) is None:
        return "started_at or finished_at is not a UTC stamp"
    return ""


def write_record(record: Dict[str, object], directory: str = "") -> str:
    """Write the record atomically (temp file then a link that never replaces another writer's record) as
    linux-canary-<version>-<tree12>-<epoch>.json and return its path. Raises ValueError on a malformed record."""
    if not isinstance(record, dict):
        raise ValueError("the record is not a dict")
    if not _is_str(directory):
        raise ValueError("the directory is not a path")
    data = dict(record)
    data.setdefault("schema", SCHEMA)
    problem = _record_problem(data)
    if problem:
        raise ValueError("refusing to write a malformed canary record: %s" % problem)
    directory = directory or evidence_dir()
    os.makedirs(directory, exist_ok=True)
    epoch = int(_parse_iso(data["finished_at"]))
    tmp = os.path.join(directory, "linux-canary.tmp-%d-%s" % (os.getpid(), uuid.uuid4().hex[:8]))
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    try:
        for _ in range(1000):
            path = os.path.join(directory, "linux-canary-%s-%s-%d.json" % (data["version"], data["tree"][:12], epoch))
            try:
                os.link(tmp, path)
            except FileExistsError:
                epoch += 1
                continue
            return path
        raise ValueError("no free record name for %s after 1000 tries" % data["tree"][:12])
    finally:
        os.unlink(tmp)


def _load(path):
    """(record dict, '') or (None, reason) for one file."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "corrupt or unreadable (%s)" % exc
    problem = _record_problem(data)
    if problem:
        return None, "malformed (%s)" % problem
    return data, ""


def record_covers(path: str, tree: str, now: Optional[float] = None) -> Tuple[bool, str]:
    """(True, '') when the record file at `path` covers the export tree `tree` now, else (False, reason)."""
    if not _is_str(path) or not path:
        return False, "the record path is not a path"
    if not _is_str(tree) or not SHA_RE.fullmatch(tree):
        return False, "the tree %r is not 40 hex" % (tree if _is_str(tree) else type(tree).__name__,)
    if now is not None and not _is_num(now):
        return False, "the clock %r is not a finite number" % (now,)
    clock_now = time.time() if now is None else float(now)
    data, why = _load(path)
    name = os.path.basename(path)
    if why:
        return False, "the canary record %s is %s" % (name, why)
    if data["tree"] != tree:
        return False, "the canary record %s is for tree %s, not %s" % (name, data["tree"][:12], tree[:12])
    if data["verdict"] != "PASS":
        return False, "the canary record %s says %s, not PASS" % (name, data["verdict"])
    judged, detail = judge_jobs(data["jobs"])
    if judged != "PASS":
        return False, "the canary record %s says PASS but its jobs judge %s (%s)" % (name, judged, detail)
    age = clock_now - _parse_iso(data["finished_at"])
    if age < -FUTURE_SLACK_S:
        return False, "the canary record %s is stamped from the future (%s)" % (name, data["finished_at"])
    if age > MAX_AGE_HOURS * 3600:
        return False, "the canary record %s is stale: finished %s, older than %g hours" % (
            name, data["finished_at"], MAX_AGE_HOURS)
    return True, ""


def covering(tree: str, directory: str = "", now: Optional[float] = None, version: str = "") -> Tuple[str, str]:
    """(record path, '') for the newest record for exactly this tree when record_covers accepts it; ('', reason)
    otherwise. Only linux-canary-*.json names are read; a corrupt one never grants and is counted."""
    if not _is_str(tree) or not SHA_RE.fullmatch(tree):
        return "", "the tree %r is not 40 hex" % (tree if _is_str(tree) else type(tree).__name__,)
    if not _is_str(directory):
        return "", "the evidence folder is not a path"
    if not _is_str(version) or (version and not VERSION_RE.fullmatch(version)):
        return "", "the version %r is not X.Y.Z" % (version if _is_str(version) else type(version).__name__,)
    if now is not None and not _is_num(now):
        return "", "the clock %r is not a finite number" % (now,)
    directory = directory or evidence_dir()
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        return "", "no canary record: the evidence folder %s cannot be read (%s)" % (directory, exc)
    corrupt = []
    candidates = []
    others = 0
    for name in names:
        m = NAME_RE.fullmatch(name)
        if not m:
            continue
        path = os.path.join(directory, name)
        data, why = _load(path)
        if why:
            corrupt.append((name, m))
            continue
        if data["tree"] != tree or (version and data["version"] != version):
            others += 1
            continue
        candidates.append((_parse_iso(data["finished_at"]), int(m.group(3)), name))
    counted = " (%d corrupt or unreadable record%s ignored)" % (len(corrupt), "" if len(corrupt) == 1 else "s") \
        if corrupt else ""
    if not candidates:
        return "", "no canary record for tree %s%s (%d record%s for other trees or versions)%s" % (
            tree[:12], " version " + version if version else "", others, "" if others == 1 else "s", counted)
    newest = max(candidates)
    for name, m in corrupt:
        # a corrupt file named for this tree and no older than the newest readable one could be the newest: refuse
        if m.group(2) == tree[:12] and (not version or m.group(1) == version) and int(m.group(3)) >= newest[1]:
            return "", "the newest canary record for tree %s may be %s, which is corrupt or unreadable%s" % (
                tree[:12], name, counted)
    path = os.path.join(directory, newest[2])
    ok, why = record_covers(path, tree, now)
    if not ok:
        return "", why + counted
    return path, ""


# ---------------------------------------------------------------------------------------------------------------
# LX1.a2: the dispatching run. Build the export tree, push it to a canary branch of the hub (never forced), dispatch
# required-fast.yml on it, find the run by the pushed sha, wait for it, judge its jobs, delete the branch when its
# tip is still ours, and record every outcome after the push.

Git = Callable[[List[str], str], Tuple[int, str, str]]        # (argv after `git`, cwd) -> (rc, stdout, stderr)

FIND_POLL_S = 10.0
CANARY_BRANCH_PREFIX = "canary/"
RUN_LIST_FIELDS = "databaseId,headSha,status,conclusion,event,createdAt"


class Interrupted(Exception):
    """SIGTERM, raised in the main thread by main's handler so the cleanup below runs exactly as on SIGINT."""


def scratch_root():
    """By the same rule as cut_rehearsal.scratch_root: BROTHER_SCRATCH when set, else ~/.claude/brother-scratch."""
    over = os.environ.get("BROTHER_SCRATCH", "")
    if over:
        return os.path.abspath(over)
    return os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")


def _real_gh(argv):
    try:
        proc = subprocess.run(["gh"] + list(argv), capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        return 124, "", "gh timed out: %s" % exc
    except OSError as exc:
        return 127, "", "gh could not start: %s" % exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _real_git(argv, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        proc = subprocess.run(["git"] + list(argv), cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        return 124, "", "git timed out: %s" % exc
    except OSError as exc:
        return 127, "", "git could not start: %s" % exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _git_call(git, argv, cwd):
    """(stdout, '') or ('', reason), the same shape rule as _gh_call."""
    reply = git(argv, cwd)
    if not isinstance(reply, tuple) or len(reply) != 3:
        return "", "git %s returned no (rc, stdout, stderr)" % " ".join(argv)
    rc, out, err = reply
    if not isinstance(rc, int) or isinstance(rc, bool) or not _is_str(out) or not _is_str(err):
        return "", "git %s returned a reply of the wrong types" % " ".join(argv)
    if rc != 0:
        return "", "git %s exited %d: %s" % (" ".join(argv), rc, err.strip()[:300])
    return out, ""


def hub_ready(gh: Gh, slug: str) -> str:
    """'' when the hub can run the canary: Actions enabled, and required-fast.yml on hub main carries both
    workflow_dispatch and the required-fast-canary job (GitHub dispatches only a workflow on the default branch).
    Otherwise the reason, naming the fix."""
    if not callable(gh) or not _is_str(slug) or not slug:
        return "NO-DATA: no gh or no hub slug to ask"
    out, why = _gh_call(gh, ["api", "repos/%s/actions/permissions" % slug, "--jq", ".enabled"])
    if why:
        return "NO-DATA: the hub's Actions setting cannot be read (%s)" % why
    if out.strip() != "true":
        return ("NO-DATA: GitHub Actions is disabled on %s (read %r); fix: enable Actions in the hub's Settings, "
                "Actions, General" % (slug, out.strip()[:40]))
    out, why = _gh_call(gh, ["api", "repos/%s/contents/.github/workflows/%s?ref=main" % (slug, WORKFLOW),
                             "--jq", ".content"])
    if why:
        return "NO-DATA: %s cannot be read from %s main (%s); fix: merge it to hub main" % (WORKFLOW, slug, why)
    try:
        text = base64.b64decode(out.strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        return "NO-DATA: %s on %s main does not decode (%s)" % (WORKFLOW, slug, exc)
    for need in ("workflow_dispatch", "required-fast-canary"):
        if need not in text:
            return ("NO-DATA: %s on %s main has no %s; fix: merge the canary workflow (FX5.a) to hub main"
                    % (WORKFLOW, slug, need))
    return ""


def canary_branch(tree: str, pushed_sha: str) -> str:
    """canary/<tree12>-<sha7>: unique per commit, so a rerun never collides with an earlier branch."""
    if not (_is_str(tree) and SHA_RE.fullmatch(tree) and _is_str(pushed_sha) and SHA_RE.fullmatch(pushed_sha)):
        raise ValueError("canary_branch needs two 40 hex ids")
    return "%s%s-%s" % (CANARY_BRANCH_PREFIX, tree[:12], pushed_sha[:7])


def find_run(gh: Gh, slug: str, branch: str, pushed_sha: str, sleep: Callable[[float], None],
             clock: Callable[[], float], grace_s: float = 120.0) -> Tuple[Optional[int], str]:
    """(run id, '') of the first listed run with headSha == pushed_sha and event workflow_dispatch, polled until
    `grace_s` has passed; (None, reason) after that, or at once on an empty, failed or corrupt gh reply."""
    if not (callable(gh) and callable(sleep) and callable(clock)) or not all(
            _is_str(v) and v for v in (slug, branch, pushed_sha)) or not _is_num(grace_s):
        return None, "NO-DATA: find_run needs gh, a slug, a branch, a sha, a sleep and a clock"
    argv = ["run", "list", "--workflow", WORKFLOW, "-R", slug, "--branch", branch, "--json", RUN_LIST_FIELDS,
            "--limit", "5"]
    start = clock()
    while True:
        out, why = _gh_call(gh, argv)
        if why:
            return None, "NO-DATA: " + why
        if not out.strip():
            return None, "NO-DATA: gh run list printed nothing"
        try:
            runs = json.loads(out)
        except ValueError as exc:
            return None, "NO-DATA: gh run list is not JSON (%s)" % exc
        if not isinstance(runs, list):
            return None, "NO-DATA: gh run list is not a list"
        for run in runs:
            if isinstance(run, dict) and run.get("headSha") == pushed_sha \
                    and run.get("event") == "workflow_dispatch":
                rid = run.get("databaseId")
                if isinstance(rid, int) and not isinstance(rid, bool) and rid > 0:
                    return rid, ""
        if clock() - start >= grace_s:
            return None, ("NO-DATA: no workflow_dispatch run of %s for %s appeared within %g s"
                          % (WORKFLOW, pushed_sha[:12], grace_s))
        sleep(FIND_POLL_S)


def wait_run(gh: Gh, slug: str, run_id: int, sleep: Callable[[float], None], clock: Callable[[], float],
             timeout_s: float = 1320.0, poll_s: float = 30.0) -> Tuple[Dict[str, str], str]:
    """({job name: conclusion}, '') once the run's status is completed; ({}, reason) when it has not concluded
    within `timeout_s` or a reply is unreadable. The status is read before the deadline, so a long sleep of the
    machine reads as a late completion, never as a pass without a conclusion."""
    if not (callable(gh) and callable(sleep) and callable(clock)) or not (_is_str(slug) and slug) \
            or not (isinstance(run_id, int) and not isinstance(run_id, bool)) \
            or not _is_num(timeout_s) or not _is_num(poll_s):
        return {}, "NO-DATA: wait_run needs gh, a slug, a run id, a sleep, a clock and finite times"
    argv = ["run", "view", str(run_id), "-R", slug, "--json", "status,conclusion,jobs"]
    start = clock()
    while True:
        out, why = _gh_call(gh, argv)
        if why:
            return {}, "NO-DATA: " + why
        try:
            view = json.loads(out)
        except ValueError as exc:
            return {}, "NO-DATA: gh run view is not JSON (%s)" % exc
        if not isinstance(view, dict):
            return {}, "NO-DATA: gh run view is not an object"
        if view.get("status") == "completed":
            jobs = view.get("jobs")
            if not isinstance(jobs, list):
                return {}, "NO-DATA: the completed run lists no jobs"
            found = {}
            for job in jobs:
                if not isinstance(job, dict) or not _is_str(job.get("name")) or not _is_str(job.get("conclusion")):
                    return {}, "NO-DATA: a job of run %s has no name or conclusion" % run_id
                if job["name"] in found:
                    return {}, "NO-DATA: run %s lists the job %s twice" % (run_id, job["name"])
                found[job["name"]] = job["conclusion"]
            return found, ""
        if clock() - start >= timeout_s:
            return {}, "NO-DATA: run %s did not conclude within %g s (status %r)" % (
                run_id, timeout_s, view.get("status"))
        sleep(poll_s)


def _push_argv(url, branch):
    argv = ["push", url, "HEAD:refs/heads/%s" % branch]
    # never forced: a forced push could rewrite a branch someone else holds
    if any(a in ("-f", "--force") or a.startswith("--force") or a.startswith("+") for a in argv[1:]):
        raise ValueError("refusing a forced push: %r" % (argv,))
    return argv


def _cleanup(git, url, branch, pushed_sha, cwd):
    """Delete the hub branch only while its tip is still `pushed_sha`; '' or what was left and why."""
    out, why = _git_call(git, ["ls-remote", url, "refs/heads/%s" % branch], cwd)
    if why:
        return "the hub branch %s was left: its tip cannot be read (%s)" % (branch, why)
    tips = [line.split()[0] for line in out.splitlines() if line.strip()]
    if not tips:
        return ""
    if tips != [pushed_sha]:
        return "the hub branch %s was left: its tip is %s, not the pushed %s" % (branch, tips[0][:12],
                                                                                 pushed_sha[:12])
    _, why = _git_call(git, ["push", url, "--delete", "refs/heads/%s" % branch], cwd)
    if why:
        return "the hub branch %s was left: the delete failed (%s)" % (branch, why)
    return ""


def run_canary(root: str, version: str, gh: Optional[Gh] = None, git: Optional[Git] = None,
               build: Optional[Callable[[str], None]] = None, directory: str = "",
               sleep: Optional[Callable[[float], None]] = None,
               clock: Optional[Callable[[], float]] = None) -> Tuple[str, str, str]:
    """(verdict, detail, record path). Every failure is NO-DATA, never an exception; nothing is recorded before
    the push (the detail says so), and every outcome after it is recorded."""
    if not _is_str(root) or not root or not _is_str(version) or not VERSION_RE.fullmatch(version):
        return "NO-DATA", "run_canary needs a root and an X.Y.Z version", ""
    gh, git = gh or _real_gh, git or _real_git
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    out, why = _git_call(git, ["remote", "get-url", HUB_REMOTE], root)
    if why:
        return "NO-DATA", "nothing recorded, nothing pushed: no %s remote (%s)" % (HUB_REMOTE, why), ""
    url = out.strip()
    slug, why = hub_slug(url)
    if why:
        return "NO-DATA", "nothing recorded, nothing pushed: " + why, ""
    why = hub_ready(gh, slug)
    if why:
        return "NO-DATA", "nothing recorded, nothing pushed: " + why, ""
    if build is None:
        import cut_preflight
        build = cut_preflight.export_tree_builder(root, cache=True)
    dest = os.path.join(scratch_root(), "canary-build", "%d-%d" % (int(time.time()), os.getpid()))
    try:
        os.makedirs(dest)
        try:
            build(dest)
        except Exception as exc:  # sbe: allow-silent returned as the reason, never swallowed
            return "NO-DATA", "nothing recorded, nothing pushed: the export tree could not be built: %s" % exc, ""
        tree, why = _git_call(git, ["rev-parse", "HEAD^{tree}"], dest)
        sha, why2 = _git_call(git, ["rev-parse", "HEAD"], dest)
        tree, sha = tree.strip(), sha.strip()
        if why or why2 or not SHA_RE.fullmatch(tree) or not SHA_RE.fullmatch(sha):
            return "NO-DATA", "nothing recorded, nothing pushed: the built tree has no readable commit (%s)" % (
                why or why2 or "not 40 hex"), ""
        path, _ = covering(tree, directory, version=version)
        if path:
            print("REUSED linux-canary %s %s" % (tree[:12], path))
            return "PASS", "reused: a PASS record already covers tree %s" % tree[:12], path
        minutes, why = private_minutes(gh)
        if minutes is None:
            return "NO-DATA", "nothing recorded, nothing pushed: " + why, ""
        return _dispatch(git, gh, url, slug, dest, tree, sha, version, minutes, directory, sleep, clock)
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def _dispatch(git, gh, url, slug, dest, tree, sha, version, minutes, directory, sleep, clock):
    branch = canary_branch(tree, sha)
    started = time.time()
    rec = {"schema": SCHEMA, "version": version, "tree": tree, "pushed_sha": sha, "branch": branch, "run_id": "",
           "run_url": "", "jobs": {}, "verdict": "NO-DATA", "detail": "", "minutes_before": minutes,
           "started_at": _iso(started)}
    pushed = False
    try:
        pushed = True   # set before the push: an interrupted push may still have landed, the cleanup reads the tip
        _, why = _git_call(git, _push_argv(url, branch), dest)
        if why:
            rec["detail"] = "the push to %s failed: %s" % (branch, why)
        else:
            _, why = _gh_call(gh, ["workflow", "run", WORKFLOW, "-R", slug, "--ref", branch])
            if why:
                rec["detail"] = "the dispatch was refused: " + why
            else:
                run_id, why = find_run(gh, slug, branch, sha, sleep, clock)
                if why:
                    rec["detail"] = why
                else:
                    rec["run_id"] = run_id
                    rec["run_url"] = "https://github.com/%s/actions/runs/%d" % (slug, run_id)
                    jobs, why = wait_run(gh, slug, run_id, sleep, clock)
                    rec["jobs"] = jobs
                    if why:
                        rec["detail"] = why
                    else:
                        rec["verdict"], rec["detail"] = judge_jobs(jobs)
    except (KeyboardInterrupt, Interrupted):
        rec["verdict"], rec["detail"] = "NO-DATA", "interrupted: " + (rec["detail"] or "a signal stopped the run")
    finally:
        left = _cleanup(git, url, branch, sha, dest) if pushed else ""
    if left:
        rec["detail"] = (rec["detail"] + "; " if rec["detail"] else "") + left
    finished = time.time()
    rec.update(finished_at=_iso(finished), seconds=round(max(0.0, finished - started), 3))
    try:
        path = write_record(rec, directory)
    except (OSError, ValueError) as exc:
        return "NO-DATA", "the canary record could not be written (%s): %s" % (exc, rec["detail"]), ""
    return rec["verdict"], rec["detail"], path


def _on_sigterm(signum, frame):
    raise Interrupted("signal %d" % signum)


def main(argv=None):
    if argv is not None and not (isinstance(argv, list) and all(isinstance(a, str) for a in argv)):
        raise SystemExit(2)
    ap = argparse.ArgumentParser(description="Run the Linux canary on the export tree, or say whether a record "
                                             "covers one.")
    ap.add_argument("--covering", metavar="TREE", help="the 40 hex export tree id: print the covering record")
    ap.add_argument("--version", default="", help="X.Y.Z: the release (required to dispatch; a filter with --covering)")
    ap.add_argument("--root", default="", help="the checkout to build the export tree from (default: this one)")
    args = ap.parse_args(argv)
    if args.covering is not None:
        path, why = covering(args.covering, version=args.version)
        if path:
            print(path)
            return 0
        print("NOT COVERED: " + why)
        return 1
    if not args.version:
        ap.error("--version is required to dispatch (or give --covering TREE)")
    root = args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    old = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        try:
            verdict, detail, path = run_canary(root, args.version)
        except (KeyboardInterrupt, Interrupted):
            # a signal outside the dispatch: nothing was pushed, so nothing to clean up
            verdict, detail, path = "NO-DATA", "interrupted before the push, nothing pushed", ""
    finally:
        signal.signal(signal.SIGTERM, old)
    print("CANARY %s %s %s" % (verdict, path or "-", " ".join(detail.split())))
    return {"PASS": 0, "FAIL": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
