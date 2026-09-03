#!/usr/bin/env python3
"""Capture the revision identity a CI run is actually testing, and REFUSE
when a pull request run cannot say what it tested.

WHY THIS EXISTS. A pull request pipeline has more than one meaningful git
identity, and conflating them is how a green verdict ends up describing a
tree nobody reviewed. There are three, and this file keeps them apart:

    source       the commit on the branch the author pushed
    destination  the commit the change is proposed to land on
    tested       what the runner actually had checked out when the gate ran

Bitbucket Cloud prepares pull request builds in its own way and does not
promise that HEAD equals the source commit, so this reads BOTH: the
environment's declaration and git's own answer, and prints them side by
side rather than assuming they agree.

WHAT IT REFUSES, and why refusing is the point. In pull request mode, a run
that cannot name its pull request id, its source commit or its destination
commit has produced a verdict about an unknown tree. That is worse than no
verdict, because it looks like one. This exits nonzero in that case, before
the gate runs, so the pipeline stops rather than publishing vacuous
assurance. Same for history: a shallow clone that cannot resolve the
destination commit cannot compute a merge base, so any check that needs one
would silently compare against something else. Both refusals print HOW TO
FIX rather than only what is wrong.

WHAT IT IS NOT. It is not an assurance engine, and it deliberately performs
no evaluation of any kind: it observes, prints one JSON object, and exits.
The requirement, evidence, risk and readiness semantics this project's
sibling holds are not here and are not reimplemented here.

WHERE IT LIVES, AND WHY NOT IN tools/. CLAUDE.md's two-host law says the
ENGINE speaks plain git only, and never a host API, in tools/ or hooks/.
This file reads host-specific ENVIRONMENT VARIABLES, which no engine module
may do, and it belongs to the CI RUNNER the same way scripts/local-gates.sh
does. tools/test_bm_hooks.py enforces that boundary mechanically.

Python 3.9, standard library only. Runs git as a local subprocess and makes
no network call. Writes nothing: the JSON goes to stdout, and the caller
decides where to keep it.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import json
import os
import subprocess
import sys

#: Provider name to the environment variables it declares. Bitbucket Cloud's
#: names come from its own pipelines documentation; the GitHub row exists so
#: the same command works on both legs of the two-host law rather than being
#: a Bitbucket-only tool wearing a neutral name.
PROVIDERS = {
    "bitbucket": {
        "repo_full_name": "BITBUCKET_REPO_FULL_NAME",
        "workspace": "BITBUCKET_WORKSPACE",
        "repo_slug": "BITBUCKET_REPO_SLUG",
        "source_commit": "BITBUCKET_COMMIT",
        "source_branch": "BITBUCKET_BRANCH",
        "pull_request_id": "BITBUCKET_PR_ID",
        "destination_branch": "BITBUCKET_PR_DESTINATION_BRANCH",
        "destination_commit": "BITBUCKET_PR_DESTINATION_COMMIT",
        "run_id": "BITBUCKET_PIPELINE_UUID",
        "step_id": "BITBUCKET_STEP_UUID",
    },
    "github": {
        "repo_full_name": "GITHUB_REPOSITORY",
        "source_commit": "GITHUB_SHA",
        "source_branch": "GITHUB_HEAD_REF",
        "pull_request_id": "GITHUB_REF_NAME",
        "destination_branch": "GITHUB_BASE_REF",
        "run_id": "GITHUB_RUN_ID",
        "step_id": "GITHUB_JOB",
    },
}

#: In pull request mode these three must be present, or the run cannot say
#: what it tested. Kept short on purpose: every entry here is a refusal, and
#: a refusal nobody can satisfy is a broken pipeline rather than a control.
PR_REQUIRED = ("pull_request_id", "source_commit", "destination_commit")


def _out(text):
    sys.stdout.write(text)


def _err(text):
    sys.stderr.write(text)


def _git_run(args, cwd=None):
    """One local git command, as (ok, stripped stdout). Never raises: git
    being absent or a commit being unresolvable is an OBSERVED fact this
    file reports, not an exception that hides the rest.

    THE TWO HALVES ARE RETURNED SEPARATELY ON PURPOSE, and the first draft
    of this file got it wrong in a way worth recording: it returned stdout
    alone and read empty output as failure. `git cat-file -e <sha>` prints
    NOTHING when the commit exists, which is exactly the success case, so
    the history check reported every present commit as missing and would
    have failed a pipeline that was perfectly healthy. Caught by running it
    against a commit this clone demonstrably had."""
    try:
        result = subprocess.run(
            ["git"] + list(args), cwd=cwd, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    return result.returncode == 0, result.stdout.strip()


def _git(args, cwd=None):
    """The stripped stdout of a successful git command, or None."""
    ok, text = _git_run(args, cwd)
    if not ok:
        return None
    return text or None


def declared(provider, env):
    """What the CI environment SAYS, with absent variables as None rather
    than empty strings: absent and empty read the same to a shell and must
    not read the same here."""
    names = PROVIDERS[provider]
    out = {}
    for field, variable in names.items():
        value = (env.get(variable) or "").strip()
        out[field] = value or None
    return out


def measured(cwd=None):
    """What git ITSELF answers about the checkout, independent of anything
    the environment claims. The two are printed side by side rather than
    reconciled: a difference between them is a fact about how this provider
    prepares a build, and hiding it would be how a receipt ends up naming a
    revision nobody tested."""
    return {
        "tested_commit": _git(["rev-parse", "HEAD"], cwd),
        "tested_tree": _git(["rev-parse", "HEAD^{tree}"], cwd),
        "dirty": bool(_git(["status", "--porcelain"], cwd)),
    }


def history_available(commit, cwd=None):
    """Whether a commit is actually in this clone. A shallow clone is the
    normal case on both hosts, so a check that needs a merge base has to ask
    rather than assume."""
    if not commit:
        return None
    ok, _text = _git_run(["cat-file", "-e", "%s^{commit}" % commit], cwd)
    return ok


def capture(provider, env, cwd=None):
    """The whole observation as one object. Pure: reads the environment and
    runs git, writes nothing, decides nothing."""
    say = declared(provider, env)
    saw = measured(cwd)
    is_pr = bool(say.get("pull_request_id") and say.get("destination_branch"))
    record = {
        "provider": provider,
        "mode": "pull_request" if is_pr else "branch",
        "repository": say.get("repo_full_name"),
        "pullRequestId": say.get("pull_request_id"),
        "sourceBranch": say.get("source_branch"),
        "sourceCommit": say.get("source_commit"),
        "destinationBranch": say.get("destination_branch"),
        "destinationCommit": say.get("destination_commit"),
        "testedCommit": saw["tested_commit"],
        "testedTree": saw["tested_tree"],
        "testedTreeDirty": saw["dirty"],
        "runId": say.get("run_id"),
        "stepId": say.get("step_id"),
    }
    # STATED RATHER THAN ASSUMED: whether the runner's HEAD is the commit the
    # provider named. Bitbucket prepares pull request builds itself, so these
    # legitimately differ, and a receipt that quietly used one for the other
    # would describe a tree nobody reviewed.
    record["testedIsSourceCommit"] = (
        None if not (record["sourceCommit"] and record["testedCommit"])
        else record["sourceCommit"] == record["testedCommit"])
    record["destinationCommitPresentLocally"] = history_available(
        record["destinationCommit"], cwd)
    return record


def problems(record):
    """Every reason this run cannot honestly report a verdict, as plain
    sentences. Empty means nothing was found, never that nothing was
    checked: the mode is in the record beside it."""
    found = []
    if record["mode"] != "pull_request":
        return found
    names = PROVIDERS[record["provider"]]
    for field in PR_REQUIRED:
        key = {"pull_request_id": "pullRequestId",
               "source_commit": "sourceCommit",
               "destination_commit": "destinationCommit"}[field]
        if not record.get(key):
            found.append(
                "%s is empty, so this pull request run cannot say what it "
                "tested. HOW TO FIX: run this step inside a pull request "
                "pipeline, where the provider sets it (%s)."
                % (key, names.get(field, "provider variable")))
    if not record.get("testedCommit"):
        found.append(
            "git could not name HEAD, so nothing here describes a real "
            "checkout. HOW TO FIX: run this step inside the repository "
            "clone, after the provider's own checkout step.")
    if record.get("destinationCommit") and not record.get(
            "destinationCommitPresentLocally"):
        found.append(
            "the destination commit is not in this clone, so a merge base "
            "cannot be computed and any check that needs one would compare "
            "against something else. HOW TO FIX: use a full-history clone "
            "(clone: depth: full) or fetch the missing commit before this "
            "step.")
    return found


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        _out(__doc__)
        return 0
    provider = argv.pop(0)
    if provider not in PROVIDERS:
        _err("bm_ci_context: unknown provider %r; expected one of: %s\n"
             % (provider, ", ".join(sorted(PROVIDERS))))
        return 2
    if argv and argv[0] == "capture":
        argv.pop(0)
    if argv:
        _err("bm_ci_context: unrecognized argument(s): %s\n" % " ".join(argv))
        return 2
    record = capture(provider, os.environ)
    found = problems(record)
    record["problems"] = found
    _out(json.dumps(record, indent=2, sort_keys=True) + "\n")
    if found:
        _err("bm_ci_context: REFUSING to continue. A pull request run that "
             "cannot name what it tested must not produce a verdict:\n")
        for problem in found:
            _err("  - %s\n" % problem)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
