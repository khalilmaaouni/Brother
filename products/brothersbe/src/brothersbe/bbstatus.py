#!/usr/bin/env python3
"""Post a build status to Bitbucket Cloud, the counterpart of the `gh api`
call the local gate runner makes on GitHub.

WHY THIS MODULE EXISTS RATHER THAN A CURL IN THE RUNNER. scripts/local-gates.sh
reports its verdict to the host the code lives on. On GitHub it shells out to
`gh`, an authenticated CLI the operator installed. Bitbucket has no equivalent
CLI, and this repository's zero-network property bans curl, wget and nc from
every shipped shell script, permitting a network client ONLY in a Python module
allow-listed by exact path and documented in SECURITY.md. That control refused
the shell version of this on 2026-08-17 and was right to; this file is the
shape it was asking for.

WHAT IT IS AND IS NOT
  It is a WRITE client, and the only one in this tree: it sends exactly one
  POST, to exactly one endpoint, carrying a build key, a state and a
  description. Its read-only siblings (prverify.py, bbprverify.py) refuse
  anything but GET on purpose; this one refuses anything but that single POST
  shape for the same reason, so the surface stays something a reader can hold
  in their head.

  It is NOT a verdict producer. It reports a verdict somebody else reached.
  Nothing it does can turn a failing battery into a passing one, and a failure
  to report is never a failure of the run: the caller keeps the battery's own
  exit status regardless of what happens here.

THE LAWS IT CARRIES, the same ones its read-only siblings carry:
  - No credential means ZERO network attempts and a NO-DATA sentence naming
    the remedy. Absence of a credential is never a silent skip.
  - The credential is never printed, never persisted, never written into a
    receipt. Only the discovery source is named, and every message is scrubbed
    of the secret before it is returned.
  - A refusal says what it could not do, in a sentence a person can act on.

Endpoint (Bitbucket Cloud REST 2.0, the commit build status resource):
  POST /2.0/repositories/{workspace}/{repo_slug}/commit/{sha}/statuses/build
  https://developer.atlassian.com/cloud/bitbucket/rest/api-group-commit-statuses/

Bitbucket's state vocabulary is its own and is NOT GitHub's: INPROGRESS,
SUCCESSFUL, FAILED, STOPPED. Sending GitHub's words here is rejected by the
API, so the translation happens in one place, `STATE_MAP` below.

Python floor 3.9, standard library only.
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API_ROOT = "https://api.bitbucket.org/2.0"
TIMEOUT_SECONDS = 10

#: GitHub's status vocabulary on the left, Bitbucket's on the right. The
#: runner speaks GitHub's because that is the reference implementation; this
#: is the only place the two are reconciled.
STATE_MAP = {
    "success": "SUCCESSFUL",
    "failure": "FAILED",
    "error": "FAILED",
    "pending": "INPROGRESS",
}
DEFAULT_STATE = "STOPPED"

REMEDY = ("no Bitbucket credential was found: export BITBUCKET_TOKEN (an Atlassian "
          "API token, sent as Bearer) or export both BITBUCKET_USERNAME and "
          "BITBUCKET_APP_PASSWORD (sent as HTTP Basic), then re-run")


def _credential():
    """(header, secret, source, detail). Identical discovery order to
    bbprverify.py, deliberately: one estate, one answer to "which credential
    does Bitbucket use here", so an operator who configured one command has
    configured both. `secret` is the single raw value to scrub from output
    (never the username, which is not sensitive)."""
    token = os.environ.get("BITBUCKET_TOKEN")
    if token:
        return ("Bearer %s" % token, token, "BITBUCKET_TOKEN",
                "the BITBUCKET_TOKEN environment variable is set")
    app_password = os.environ.get("BITBUCKET_APP_PASSWORD")
    username = os.environ.get("BITBUCKET_USERNAME")
    if app_password and username:
        basic = base64.b64encode(("%s:%s" % (username, app_password))
                                 .encode("utf-8")).decode("ascii")
        return ("Basic %s" % basic, app_password,
                "BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD",
                "the BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD environment "
                "variables are set")
    return None, None, None, REMEDY


def _current_head():
    """`git rev-parse HEAD` in the current directory, or None when git
    cannot answer (not a checkout, git missing, any subprocess failure).
    None is a real answer: a machine that cannot resolve HEAD gets the
    staleness check skipped rather than a crash, exactly as `_stale_sentence`
    below treats it."""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _stale_sentence(sha, current_head):
    """The refusal sentence when `sha` (the head this status is ABOUT) is not
    `current_head` (the head this machine is actually ON right now), or None
    when they agree. A caller with no way to resolve `current_head` passes
    None, which this treats as "cannot check" rather than "mismatch": a
    guard that refuses everything it cannot verify is not a stronger guard,
    it is a different, wider outage. Pure and dependency-free on purpose, so
    a test exercises the exact refusal wording without a real git checkout
    or any credential.
    """
    if not sha or not current_head:
        return None
    if sha == current_head:
        return None
    return "STALE: status head %s is not the current head %s; not posted" % (sha, current_head)


def _scrub(text, secret):
    """The secret never reaches a caller's log, whatever the API echoed back."""
    if not secret or not text:
        return text
    return text.replace(secret, "<redacted>")


def _real_post(url, header, payload):
    """The only code path that leaves this machine. (status, error).

    Two values rather than one: a bare status would make "the network never
    answered" indistinguishable from a status code, and those are different
    facts that need different sentences."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    if header:
        request.add_header("Authorization", header)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.getcode(), None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError) as exc:
        return None, "the request did not complete (%s)" % exc


def post_status(workspace_repo, sha, state, description="", key="local-gates",
                post=_real_post, current_head=None):
    """(verdict, sentence). verdict is "POSTED" or "NO-DATA", never "PASS":
    this module reports a verdict, it does not reach one, and a word that
    looks like a gate verdict here would be read as one.

    `post` is injected so tests never touch the network.

    `current_head`, when given, is the head this machine is actually on
    right now; a `sha` that does not match it is refused before the
    credential lookup and before any network attempt (`_stale_sentence`
    names the exact sentence). Default None SKIPS this check rather than
    computing `_current_head()` itself: a direct `post_status` call is
    exactly the shape this module's own test suite uses with hand-picked
    shas that were never meant to be "the real HEAD", and a check that ran
    unconditionally would refuse every one of them. `main()` below is the
    one caller that resolves and passes a real `current_head`, because that
    is the actual "posting for real" choke point this guard exists for.
    """
    if not workspace_repo or "/" not in workspace_repo:
        return "NO-DATA", ("no Bitbucket repository was named as workspace/repo_slug, "
                           "so nothing was posted")
    if not sha:
        return "NO-DATA", "no commit sha was named, so nothing was posted"

    stale = _stale_sentence(sha, current_head)
    if stale:
        # Checked BEFORE the credential lookup and before any network
        # attempt: a status about a commit that is no longer HEAD is refused
        # on its own facts, never masked by (or mistaken for) a credential
        # problem.
        return "NO-DATA", stale

    header, secret, source, detail = _credential()
    if not header:
        # ZERO network attempts on this path. The check is before the URL is
        # even built, so an absent credential cannot become a request.
        return "NO-DATA", detail

    bb_state = STATE_MAP.get((state or "").lower(), DEFAULT_STATE)
    url = "%s/repositories/%s/commit/%s/statuses/build" % (API_ROOT, workspace_repo, sha)
    payload = {"key": key, "state": bb_state, "name": key,
               "description": (description or "")[:250]}

    status, error = post(url, header, payload)
    if status is None:
        return "NO-DATA", _scrub("nothing was posted: %s" % error, secret)
    if status in (200, 201):
        return "POSTED", ("build status %s posted for %s on Bitbucket (credential from %s)"
                          % (bb_state, sha[:12], source))
    if status in (401, 403):
        return "NO-DATA", ("nothing was posted: Bitbucket refused the credential from %s "
                           "with HTTP %d; the token needs repository write access to post "
                           "a build status" % (source, status))
    if status == 404:
        # 404 here is ambiguous in the same way it is for the read client:
        # the repository may not exist, or this credential may not see it.
        return "NO-DATA", ("nothing was posted: Bitbucket returned 404 for %s at %s, which "
                           "means either the repository does not exist or this credential "
                           "cannot see it, and those are different facts"
                           % (workspace_repo, sha[:12]))
    return "NO-DATA", ("nothing was posted: Bitbucket answered HTTP %d" % status)


def main(argv=None):
    """usage: bbstatus.py <workspace/repo_slug> <sha> <state> [description]

    Exit 0 when the status was posted, 1 when it was not. The CALLER keeps its
    own verdict either way: this exit code says whether the report landed, and
    never whether the thing being reported passed.
    """
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 3:
        sys.stderr.write(main.__doc__.strip().splitlines()[0] + "\n")
        return 2
    # The one place `current_head` is resolved for real: this is the actual
    # "posting for real" choke point (what scripts/local-gates.sh invokes),
    # so this is where the STALE guard is live rather than skipped.
    verdict, sentence = post_status(argv[0], argv[1], argv[2],
                                    argv[3] if len(argv) > 3 else "",
                                    current_head=_current_head())
    print("%-8s %s" % (verdict, sentence))
    return 0 if verdict == "POSTED" else 1


if __name__ == "__main__":
    sys.exit(main())
