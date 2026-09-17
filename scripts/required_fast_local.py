#!/usr/bin/env python3
"""An honest required_fast check mark for a hub PR, from a local run.

WHY THIS EXISTS. hub (khalilmaaouni/brother-hub) used its 2,000 free GitHub
Actions minutes for September 2026, so no cloud check runs on hub PRs until
October 1; the plan also has no rulesets or branch protection (403 "Upgrade
to GitHub Pro"), so nothing can be REQUIRED. The founder's local gate of
record is `sh scripts/required_fast.sh`. This script runs that gate against
the PR's real head commit in a disposable detached worktree, runs it again
(cached) against the base branch tip to know which reds are inherited, and
either prints the verdict or posts it as a commit status GitHub itself would
otherwise produce -- an informational signal, never a merge gate.

Mirrors products/brothersbe/src/brothersbe/bbstatus.py: a bounded, injected-
transport write client that reports a verdict somebody else reached and
never turns a red into a green.

    python3 scripts/required_fast_local.py --pr 751 [--repo OWNER/REPO] [--post]

Without --post: prints the would-be status and exits 0 (success), 1
(failure) or 2 (no-data/error). With --post: also POSTs a commit status via
`gh api`, after re-checking the PR head has not moved; a failed post exits 2.

Python 3.9 floor, standard library only (plus the `gh` and `git` binaries).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cut as _cut  # noqa: E402  reuse FAST_SUMMARY_RE, the one place the
                     # shape required_fast.sh prints is already spelled out

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_NODATA = 2

DEFAULT_REPO = "khalilmaaouni/brother-hub"
CONTEXT = "required-fast (local)"
EVIDENCE_DIR = os.path.expanduser("~/.claude/evidence")

COUNT_RE = re.compile(r"^pass (\d+) +fail (\d+) +no-data (\d+)$")
FAILED_NAMES_RE = re.compile(r"^FAILED:(.*)$")


def _run(cmd, cwd=None, runner=None, **kw):
    """One subprocess call, never raising on a missing binary: a
    CompletedProcess-shaped failure (code 127) so every caller reads one
    shape, same contract as scripts/cut.py's _run."""
    runner = runner or subprocess.run
    try:
        return runner(cmd, cwd=cwd, capture_output=True, text=True, **kw)
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", "%s" % exc)


def _text(proc):
    return ((proc.stdout or "") + (proc.stderr or "")).rstrip()


def pr_head_and_base(repo, pr, runner=None):
    """(head_sha, base_branch, err). err is a NO-DATA sentence, never a
    guess -- both values come straight from `gh pr view`'s own JSON."""
    proc = _run(["gh", "pr", "view", str(pr), "--repo", repo, "--json",
                "headRefOid,baseRefName"], runner=runner)
    if proc.returncode != 0:
        return None, None, ("gh pr view #%s exited %d: %s"
                            % (pr, proc.returncode, _text(proc)))
    try:
        data = json.loads(proc.stdout or "")
        head_sha = data["headRefOid"]
        base_branch = data["baseRefName"]
    except (ValueError, KeyError, TypeError) as exc:
        return None, None, ("gh pr view #%s printed no headRefOid/"
                            "baseRefName JSON (%s)" % (pr, exc))
    if not head_sha or not base_branch:
        return None, None, ("gh pr view #%s returned an empty headRefOid "
                            "or baseRefName" % pr)
    return head_sha, base_branch, ""


def worktree_at(root, url, ref, label, runner=None):
    """(path, sha, err): fetch `ref` (a branch name or a commit sha) fresh
    from `url`, create a DETACHED worktree at exactly the fetched commit,
    and verify it landed clean. `sha` is always read back from git, never
    assumed to equal `ref`. On any failure path is None and whatever was
    created on disk is already cleaned up."""
    fetch = _run(["git", "fetch", "--quiet", url, ref], cwd=root, runner=runner)
    if fetch.returncode != 0:
        return None, None, ("git fetch %s %s exited %d: %s"
                            % (url, ref, fetch.returncode, _text(fetch)))
    rp = _run(["git", "rev-parse", "FETCH_HEAD"], cwd=root, runner=runner)
    sha = (rp.stdout or "").strip()
    if rp.returncode != 0 or not sha:
        return None, None, ("git rev-parse FETCH_HEAD exited %d: %s"
                            % (rp.returncode, _text(rp)))
    base_dir = tempfile.mkdtemp(prefix="required-fast-local-%s-" % label)
    path = os.path.join(base_dir, "wt")
    add = _run(["git", "worktree", "add", "--quiet", "--detach", path, sha],
              cwd=root, runner=runner)
    if add.returncode != 0:
        shutil.rmtree(base_dir, ignore_errors=True)
        return None, sha, ("git worktree add exited %d: %s"
                           % (add.returncode, _text(add)))
    head = _run(["git", "rev-parse", "HEAD"], cwd=path, runner=runner)
    status = _run(["git", "status", "--porcelain"], cwd=path, runner=runner)
    if (head.stdout or "").strip() != sha or (status.stdout or "").strip():
        _remove_worktree(root, path, runner)
        return None, sha, ("worktree did not verify clean at %s (HEAD=%r, "
                           "status=%r)" % (sha, (head.stdout or "").strip(),
                                          (status.stdout or "")[:200]))
    return path, sha, ""


def _remove_worktree(root, path, runner=None):
    """Always safe to call: removes git's own record then the directory,
    ignoring errors on the belt-and-suspenders rmtree."""
    if not path:
        return
    _run(["git", "worktree", "remove", "--force", path], cwd=root, runner=runner)
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def parse_fast_output(stdout):
    """(counts, failed_names, why). counts is (pass, fail, nodata) or None
    when required_fast.sh printed no summary line at all -- NO-DATA, never
    an inferred success. failed_names is sorted, empty when none failed."""
    lines = _cut.FAST_SUMMARY_RE.findall(stdout or "")
    counts = None
    failed = []
    for line in lines:
        m = COUNT_RE.match(line)
        if m:
            counts = tuple(int(x) for x in m.groups())
            continue
        m = FAILED_NAMES_RE.match(line)
        if m:
            failed.extend(m.group(1).split())
    if counts is None:
        return None, [], "no 'pass N   fail N   no-data N' summary line printed"
    return counts, sorted(failed), ""


def run_gate(path, log_path, runner=None):
    """Runs `sh scripts/required_fast.sh` inside the worktree at `path`,
    saves the full output to `log_path` (best-effort), and returns
    (counts, failed_names, why)."""
    proc = _run(["sh", os.path.join("scripts", "required_fast.sh")],
               cwd=path, runner=runner)
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(_text(proc) + "\n")
    except OSError:
        pass
    return parse_fast_output(proc.stdout or "")


def _cache_path(evidence_dir, base_sha):
    return os.path.join(evidence_dir, "required-fast-local-base-%s.json" % base_sha)


def _read_cache(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "counts" not in data:
        return None
    return data


def _write_cache(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


def base_result(root, url, base_branch, evidence_dir, runner=None):
    """(base_sha, counts, failed_names, err). Runs required_fast.sh once
    per base commit and caches it under
    ~/.claude/evidence/required-fast-local-base-<sha>.json, so a later PR
    check against the same base tip reads the cache instead of re-running
    the heavy battery."""
    fetch = _run(["git", "fetch", "--quiet", url, base_branch], cwd=root, runner=runner)
    if fetch.returncode != 0:
        return None, None, [], ("git fetch %s %s exited %d: %s"
                                % (url, base_branch, fetch.returncode, _text(fetch)))
    rp = _run(["git", "rev-parse", "FETCH_HEAD"], cwd=root, runner=runner)
    base_sha = (rp.stdout or "").strip()
    if rp.returncode != 0 or not base_sha:
        return None, None, [], ("git rev-parse FETCH_HEAD exited %d: %s"
                                % (rp.returncode, _text(rp)))

    cache_path = _cache_path(evidence_dir, base_sha)
    cached = _read_cache(cache_path)
    if cached is not None:
        return base_sha, tuple(cached["counts"]), cached.get("failed", []), ""

    path, sha, err = worktree_at(root, url, base_sha, "base", runner)
    if err:
        return base_sha, None, [], "base worktree: %s" % err
    try:
        log_path = os.path.join(evidence_dir, "required-fast-local-base-%s.log" % base_sha)
        counts, failed, why = run_gate(path, log_path, runner)
    finally:
        _remove_worktree(root, path, runner)
    if counts is None:
        return base_sha, None, [], "base run at %s: %s" % (base_sha, why)
    _write_cache(cache_path, {"counts": list(counts), "failed": failed})
    return base_sha, counts, failed, ""


def evaluate(root, repo, pr, evidence_dir=None, runner=None):
    """Runs the whole check and returns a dict: state (success/failure/
    error), sha (PR head, or None if it could not even be resolved),
    description, context, lines (human-readable trail, print these)."""
    evidence_dir = evidence_dir or EVIDENCE_DIR
    url = "https://github.com/%s.git" % repo
    lines = []

    head_sha, base_branch, err = pr_head_and_base(repo, pr, runner)
    if err:
        return {"state": "error", "sha": None, "context": CONTEXT,
                "description": "", "lines": lines + ["NO-DATA: %s" % err]}
    lines.append("PR #%s head %s base %s" % (pr, head_sha, base_branch))

    base_sha, base_counts, base_failed, err = base_result(
        root, url, base_branch, evidence_dir, runner)
    if err or base_counts is None:
        return {"state": "error", "sha": head_sha, "context": CONTEXT,
                "description": "", "lines": lines + ["NO-DATA: %s" % err]}
    lines.append("base %s: pass %d   fail %d   no-data %d   FAILED:%s"
                 % (base_sha, base_counts[0], base_counts[1], base_counts[2],
                    (" " + " ".join(base_failed)) if base_failed else " (none)"))

    pr_path, pr_sha, err = worktree_at(root, url, head_sha, "pr", runner)
    if err:
        return {"state": "error", "sha": head_sha, "context": CONTEXT,
                "description": "", "lines": lines + ["NO-DATA: %s" % err]}
    try:
        log_path = os.path.join(evidence_dir, "required-fast-local-pr-%s-%s.log"
                                % (pr, head_sha))
        counts, failed, why = run_gate(pr_path, log_path, runner)
    finally:
        _remove_worktree(root, pr_path, runner)
    lines.append("full gate log: %s" % log_path)
    if counts is None:
        return {"state": "error", "sha": head_sha, "context": CONTEXT,
                "description": "", "lines": lines + ["NO-DATA: PR run: %s" % why]}
    lines.append("PR %s: pass %d   fail %d   no-data %d   FAILED:%s"
                 % (head_sha, counts[0], counts[1], counts[2],
                    (" " + " ".join(failed)) if failed else " (none)"))

    new_failures = sorted(set(failed) - set(base_failed))
    inherited = sorted(set(failed) & set(base_failed))
    if (counts[1] > 0 and not failed) or (base_counts[1] > 0 and not base_failed):
        # A fail count with no FAILED: names cannot be split into inherited
        # and new, so it can never read as success.
        return {"state": "error", "sha": head_sha, "context": CONTEXT,
                "description": "", "lines": lines + [
                    "NO-DATA: a run reported failures without naming them "
                    "(PR fail %d, base fail %d)" % (counts[1], base_counts[1])]}
    if new_failures:
        state = "failure"
    elif counts[2] > 0:
        state = "error"
    else:
        state = "success"

    description = ("local required_fast: pass %d fail %d (inherited %d, new %d) at %s"
                   % (counts[0], counts[1], len(inherited), len(new_failures),
                      head_sha[:7]))[:140]
    if new_failures:
        lines.append("new failures (not on base): %s" % " ".join(new_failures))

    return {"state": state, "sha": head_sha, "context": CONTEXT,
           "description": description, "lines": lines}


def post_status(repo, sha, state, description, context=CONTEXT, runner=None):
    """(ok, text). The only write in this file: one `gh api` POST to the
    commit-status endpoint, through the same injectable `runner` every read
    above used, so a test never touches the network."""
    cmd = ["gh", "api", "-X", "POST", "repos/%s/statuses/%s" % (repo, sha),
          "-f", "state=%s" % state, "-f", "context=%s" % context,
          "-f", "description=%s" % description]
    proc = _run(cmd, runner=runner)
    if proc.returncode != 0:
        return False, ("gh api statuses POST exited %d: %s"
                       % (proc.returncode, _text(proc)))
    return True, "posted %s for %s (context %s)" % (state, sha[:12], context)


_EXIT_FOR_STATE = {"success": EXIT_OK, "failure": EXIT_FAILURE, "error": EXIT_NODATA}


def main(argv=None, runner=None, evidence_dir=None):
    """`evidence_dir` is test-only: a script invocation always uses the
    real ~/.claude/evidence (evaluate()'s own default)."""
    argv = list(argv if argv is not None else sys.argv[1:])
    parser = argparse.ArgumentParser(
        description="Give a hub PR an honest required_fast check mark from "
                    "a local run (hub's cloud Actions minutes are exhausted "
                    "and it has no branch protection to require one).")
    parser.add_argument("--pr", type=int, required=True, help="PR number")
    parser.add_argument("--repo", default=DEFAULT_REPO,
                        help="owner/repo (default: %s)" % DEFAULT_REPO)
    parser.add_argument("--post", action="store_true",
                        help="POST the result as a commit status via gh api")
    args = parser.parse_args(argv)

    result = evaluate(ROOT, args.repo, args.pr, evidence_dir=evidence_dir, runner=runner)
    for line in result["lines"]:
        print(line)

    if result["sha"] is None:
        print("NO-DATA: could not resolve PR #%s at all" % args.pr)
        return EXIT_NODATA

    print("%-8s %s: %s" % (result["state"], result["context"], result["description"]))

    if not args.post:
        return _EXIT_FOR_STATE[result["state"]]

    fresh_sha, _base, err = pr_head_and_base(args.repo, args.pr, runner)
    if err:
        print("NOT POSTED: could not re-read PR head before posting: %s" % err)
        return EXIT_NODATA
    if fresh_sha != result["sha"]:
        print("NOT POSTED: PR head moved from %s to %s since the run; "
              "re-run to check the new head" % (result["sha"], fresh_sha))
        return EXIT_NODATA

    ok, text = post_status(args.repo, result["sha"], result["state"],
                           result["description"], result["context"], runner)
    print(text)
    if not ok:
        return EXIT_NODATA
    return _EXIT_FOR_STATE[result["state"]]


if __name__ == "__main__":
    sys.exit(main())
