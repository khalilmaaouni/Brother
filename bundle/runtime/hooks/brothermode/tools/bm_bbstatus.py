#!/usr/bin/env python3
"""Host-aware gate reporting for scripts/local-gates.sh.

WHY THIS EXISTS (two-host law, founder order 2026-08-16). The local gate
runner posts its verdict as a commit status, and until this tool existed the
posting arm was `gh api` with a hardcoded GitHub repository: correct on the
canonical checkout, and a guaranteed reporting failure, exit 2, for anyone
whose origin lives on Bitbucket Cloud or anywhere else, even though their
battery had just passed. The engine never talks to a host API; the gate
RUNNER may, because reporting a verdict is the runner's work, not the code
under test's. This file is that reporting arm for the non-GitHub half.

WHAT IT PROMISES, and scripts/local-gates.sh holds the other half:

  1. `classify` reads a git remote URL and answers github, bitbucket, or
     unknown, so the runner can route. Classification is pure string
     parsing: no git, no network, exit 0 always.
  2. `slug` reads the same URL and answers `owner/repo`, which is the
     address the runner's GitHub arm posts to. It REFUSES rather than
     guesses: a URL whose path does not hold two segments prints why on
     stderr, prints NOTHING on stdout, and exits 3, so the runner is never
     handed a half-parsed address. Pure string parsing as well, and it
     shares `workspace_repo` with `post` rather than standing up a second
     parser for the GitHub side.
  3. `post` sends one Bitbucket Cloud build status for a commit. A failure
     to post prints WHY and exits nonzero, and the runner's Bitbucket arm
     deliberately does not let that change the gate's own exit status: a
     verdict that could not be REPORTED is still the verdict. The GitHub
     arm keeps exit 2 on a failed POST, because sessions on the canonical
     checkout already treat that exit as load-bearing, and as of 2026-08-26
     it keeps that same exit on a refusal to name the target.
  4. On an unknown host nothing is posted and the runner says so. NO-DATA
     is never silent.

WHAT `slug` CLOSED. The GitHub arm posted to a hardcoded repository name
until 2026-08-26. On the canonical checkout that was invisible; on a FORK it
meant a battery reading one tree filed its green onto another tree's commits,
or failed trying. docs/BITBUCKET.md named that gap twice without closing it
and the sibling's PARITY.md recorded it as the one real divergence between
the two runners, so it is closed here rather than named a third time.
  4. The credential never reaches a returned or printed message. `_scrub`
     replaces it with `<redacted>` in every message `post_status` returns,
     including the HTTPError and URLError branches, which carry text this
     module did not compose and cannot fully predict.

CREDENTIALS, from the environment only, never from a file in this
repository (CLAUDE.md credential rules). Two shapes, tried in this order:

  BITBUCKET_TOKEN                          sent as a Bearer token (a
                                           workspace, project or repository
                                           access token).
  BITBUCKET_USERNAME and
  BITBUCKET_APP_PASSWORD                   sent as HTTP Basic. Atlassian
                                           retired classic app passwords on
                                           2026-07-28 (docs/BITBUCKET.md);
                                           the SAME variable now carries a
                                           scoped API token, and the name is
                                           kept because both siblings in
                                           this estate use these exact
                                           names, which is the parity the
                                           founder asked for.

The urllib import below is a NAMED exception in tools/test_bm.py's
zero-network suite, per file and per module, the same way bm_autosave.py's
subprocess import is: this tool is invoked by scripts/local-gates.sh after
a battery completes, never by a hook, and the one request it makes is the
status POST it exists for. SECURITY.md documents it beside the other named
exceptions.

Python 3.9, standard library only. No em or en dashes anywhere in this
file, its comments, or its output.
"""

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.bitbucket.org/2.0/repositories"
TIMEOUT_SECONDS = 30

#: Gate result to Bitbucket build-status state. Bitbucket's own vocabulary
#: (SUCCESSFUL, FAILED, INPROGRESS, STOPPED); the runner only ever reports a
#: finished battery, so only the first two are reachable from the CLI.
STATE_MAP = {"success": "SUCCESSFUL", "failure": "FAILED"}


def _out(text):
    """Write to stdout with NEWLINE TRANSLATION OFF, and the off is the point.

    WINDOWS, found 2026-08-26 by reproducing the platform rather than
    reasoning about it. Python opens sys.stdout as a text stream with
    newline=None, which on Windows turns every "\n" written into "\r\n".
    `slug` prints a repository ADDRESS that scripts/local-gates.sh captures
    with $(...), and command substitution strips trailing NEWLINES but not a
    carriage return, so on Windows the captured target was "owner/repo\r" and
    the arm built `repos/owner/repo\r/statuses/<sha>`. Verified in bash: a 27
    character slug came back 28 characters long.

    reconfigure() rather than a write to sys.stdout.buffer, because the
    console's own ENCODING should keep deciding how bytes are formed; only
    the newline rewriting is refused. A capturing stand-in (unittest's
    StringIO) has no reconfigure and performs no translation either, so the
    plain write below is already exact there.

    The flush is load bearing for the same reason it is in bm_sessionstart.py:
    a caller reading this through a pipe gets nothing until the buffer moves.
    """
    try:
        sys.stdout.reconfigure(newline="\n")
    except (AttributeError, ValueError, OSError):
        pass
    sys.stdout.write(text)
    try:
        sys.stdout.flush()
    except (ValueError, OSError):
        pass


def _err(text):
    """Stderr is left alone DELIBERATELY, not by oversight.

    Everything written here is prose for a person reading a console, where
    the platform's own line ending is the correct one. Only stdout carries a
    value another program parses, so only stdout refuses the translation."""
    sys.stderr.write(text)


def _host_of(url):
    """The lowercased host part of a git remote URL, or an empty string.

    Handles the two transports Bitbucket Cloud and GitHub both accept
    (https://host/path and the scp-like git@host:path), plus explicit
    ssh:// forms. Anything unreadable is simply no host, which classifies
    as unknown: a guess about where a verdict should be posted is worse
    than not posting."""
    if not url:
        return ""
    text = url.strip()
    m = re.match(r"^[a-z][a-z0-9+.-]*://([^/]+)", text, re.IGNORECASE)
    if m:
        hostport = m.group(1)
    else:
        m = re.match(r"^([^/@]+@)?([^:/]+):", text)
        if not m:
            return ""
        hostport = m.group(2)
    host = hostport.rsplit("@", 1)[-1]
    host = host.split(":", 1)[0]
    return host.lower()


def classify(url):
    """github, bitbucket, or unknown, from the remote URL alone."""
    host = _host_of(url)
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    if host == "bitbucket.org" or host.endswith(".bitbucket.org"):
        return "bitbucket"
    return "unknown"


def workspace_repo(url):
    """(workspace, repo) from a git remote URL, or None.

    The path's first two segments, with a trailing .git dropped. A URL
    whose path has fewer than two segments has no repository to post to,
    and that is a stated refusal in post(), never a guessed path.

    NAMED FOR BITBUCKET'S VOCABULARY, used on both hosts: GitHub's
    owner/repo is the same two path segments in the same two URL forms, so
    `slug` reads this one parser rather than adding a GitHub-only copy of
    it. Kept deliberately lenient about EXTRA segments, because changing
    that would change what the Bitbucket POST accepts as well, and neither
    host serves a repository at a three segment path."""
    if not url:
        return None
    text = url.strip()
    m = re.match(r"^[a-z][a-z0-9+.-]*://[^/]+/(.+)$", text, re.IGNORECASE)
    if m:
        path = m.group(1)
    else:
        m = re.match(r"^(?:[^/@]+@)?[^:/]+:(.+)$", text)
        if not m:
            return None
        path = m.group(1)
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    workspace, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not workspace or not repo:
        return None
    return workspace, repo


def _auth_header(env):
    """(header, secret). `header` is the Authorization value, or None with
    the reason printed by the caller. `secret` is the one raw value that
    must never reach a returned message (the bare token, or the app
    password alone; never the username, which is not sensitive).
    BITBUCKET_TOKEN wins when both shapes are set, because a dedicated
    access token is the narrower credential."""
    token = env.get("BITBUCKET_TOKEN", "").strip()
    if token:
        return "Bearer " + token, token
    user = env.get("BITBUCKET_USERNAME", "").strip()
    password = env.get("BITBUCKET_APP_PASSWORD", "").strip()
    if user and password:
        raw = ("%s:%s" % (user, password)).encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii"), password
    return None, None


def _scrub(text, secret):
    """The credential never reaches a caller's output, whatever the
    transport echoed back. Last line of defense, not the design: nothing
    above should ever put the secret into a message in the first place, but
    an HTTPError body or a URLError reason is text this module did not
    compose, so it is scrubbed here rather than trusted."""
    if not secret or not text:
        return text
    return text.replace(secret, "<redacted>")


def post_status(url, sha, state, description, context, link, env,
                opener=None):
    """POST one build status. Returns (ok, message); never raises for the
    failures a runner can meet (no credential, bad URL, HTTP refusal,
    network down), because the caller's contract is to REPORT the failure
    and leave the gate's exit alone."""
    if opener is None:
        opener = urllib.request.urlopen
    mapped = STATE_MAP.get(state)
    if mapped is None:
        return False, ("not posted: state %r is not one of %s"
                       % (state, "/".join(sorted(STATE_MAP))))
    pair = workspace_repo(url)
    if pair is None:
        return False, ("not posted: could not read a workspace/repository "
                       "pair out of the origin URL %r" % (url or ""))
    auth, secret = _auth_header(env)
    if auth is None:
        return False, ("not posted: no Bitbucket credential in the "
                       "environment. Set BITBUCKET_TOKEN, or "
                       "BITBUCKET_USERNAME plus BITBUCKET_APP_PASSWORD. "
                       "The gate verdict above still stands.")
    workspace, repo = pair
    endpoint = "%s/%s/%s/commit/%s/statuses/build" % (
        API_BASE, workspace, repo, sha)
    payload = {
        "key": context,
        "state": mapped,
        "description": description[:255],
        "url": link or ("https://bitbucket.org/%s/%s" % (workspace, repo)),
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=body,
        headers={"Content-Type": "application/json", "Authorization": auth},
        method="POST")
    try:
        response = opener(request, timeout=TIMEOUT_SECONDS)
        code = getattr(response, "status", None) or response.getcode()
    except urllib.error.HTTPError as exc:
        return False, _scrub(
            "not posted: Bitbucket answered HTTP %s for %s"
            % (exc.code, endpoint), secret)
    except (urllib.error.URLError, OSError) as exc:
        return False, _scrub(
            "not posted: could not reach Bitbucket (%s)"
            % getattr(exc, "reason", exc), secret)
    if code not in (200, 201):
        return False, _scrub(
            "not posted: Bitbucket answered HTTP %s for %s"
            % (code, endpoint), secret)
    return True, ("posted %s=%s against %s/%s@%s"
                  % (context, mapped, workspace, repo, sha[:12]))


def _flag(argv, name):
    if name in argv:
        idx = argv.index(name)
        if idx + 1 >= len(argv):
            return None, "%s needs a value" % name
        value = argv[idx + 1]
        del argv[idx:idx + 2]
        return value, None
    return "", None


def cmd_classify(argv):
    if len(argv) != 1:
        _err("bm_bbstatus classify: exactly one argument, the remote URL "
             "(an empty string is allowed and reads as unknown)\n")
        return 2
    _out(classify(argv[0]) + "\n")
    return 0


def cmd_slug(argv):
    """owner/repo on stdout, or a refusal on stderr and exit 3.

    Nothing is printed on stdout when it refuses. The runner captures this
    command's stdout straight into the address it posts to, so a partial
    line here would become a partial repository name there."""
    if len(argv) != 1:
        _err("bm_bbstatus slug: exactly one argument, the remote URL\n")
        return 2
    pair = workspace_repo(argv[0])
    if pair is None:
        _err("bm_bbstatus slug: %r does not split into owner/repo; nothing "
             "to post to\n" % argv[0])
        return 3
    _out("%s/%s\n" % pair)
    return 0


def cmd_post(argv):
    rest = list(argv)
    values = {}
    for name in ("--url", "--sha", "--state", "--description", "--context",
                 "--link"):
        value, problem = _flag(rest, name)
        if problem:
            _err("bm_bbstatus post: %s\n" % problem)
            return 2
        values[name] = value
    if rest:
        _err("bm_bbstatus post: unrecognized argument(s): %s\n"
             % " ".join(rest))
        return 2
    for required in ("--url", "--sha", "--state", "--description"):
        if not values[required]:
            _err("bm_bbstatus post: %s is required\n" % required)
            return 2
    ok, message = post_status(
        values["--url"], values["--sha"], values["--state"],
        values["--description"], values["--context"] or "local-gates",
        values["--link"], os.environ)
    if ok:
        _out(message + "\n")
        return 0
    _err(message + "\n")
    return 3


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        _out(__doc__)
        return 0
    command = argv.pop(0)
    if command == "classify":
        return cmd_classify(argv)
    if command == "slug":
        return cmd_slug(argv)
    if command == "post":
        return cmd_post(argv)
    _err("bm_bbstatus: unknown command %r; expected classify, slug or "
         "post\n" % command)
    return 2


if __name__ == "__main__":
    sys.exit(main())
