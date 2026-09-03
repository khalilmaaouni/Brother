"""`sbe pr verify`: live GitHub evidence for one pull request, bound to its head.

Spec of record: docs/specs/2026-07-30-sbe-pr-verify.md. The fixtures in
tools/test_sbe_prverify.py drive this module directly through `evaluate` and
`render`; the calling convention they pin (the report dict shape, the
three-value fetch contract, the control names) is authoritative here.

Laws this file carries:

- Read-only client. The only network code path is `_real_fetch`, it sends GET
  and nothing else, pins Accept to application/vnd.github+json, holds the
  Authorization header in memory only, and runs under a hard timeout. The
  fetch function is a PARAMETER of `evaluate`, so every fixture injects a
  canned one and never opens a socket.
- The token is never persisted, never printed, never written into the report;
  the report records only which discovery source supplied it, by name. As a
  belt on top of that design, every detail string is scrubbed against the
  token value before the report is returned (the same posture as
  src/brothersbe/evidence.py redact_argv, applied to the one secret this
  command can hold).
- Missing credentials are NO-DATA with a remedy and a nonzero exit, never a
  clean verdict in either direction: absence of evidence is not evidence of
  absence. With no token, ZERO network attempts are made.
- A repository whose origin remote is not github.com, or has no origin remote
  at all, is the same kind of absence: NO-DATA naming the host that was
  actually found (or the fact that none was), never a network attempt against
  api.github.com and never read as a pass or a blocking failure. Checked
  before the first network call, against the real transport only, so no
  fixture-injected fetch is affected.
- Every HTTP failure maps to a verdict from the spec's table; a traceback
  reaching the user is a defect.
- UNVERIFIABLE is a fourth report-level verdict for this command only. These
  controls live here, not in a Check registry, so the check-registry law of
  three is untouched; the rendered report states that in its own header.
- A later commit invalidates: the pull request resource is fetched FIRST to
  learn the head sha every control is evaluated against, and LAST to detect a
  force-push mid-run. A race is UNVERIFIABLE with re-run prose.

Python floor is 3.9, standard library only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.github.com"
TIMEOUT_SECONDS = 10
GOOD_CONCLUSIONS = ("success", "neutral", "skipped")

#: owner/name, each side [A-Za-z0-9_.-]+ and NOTHING else: no extra slashes,
#: no stray "/" inside either side (a stray "/" breaks the match, since the
#: pattern is anchored end to end). The character class allows periods
#: though, so it matches "../repo" and "owner/.." just as readily as
#: "octocat.github.io/demo" -- a side that is exactly "." or ".." is a
#: path-traversal token, not a name, and valid_repo_shape rejects that case
#: explicitly below rather than leaving it to this regex.
REPO_SHAPE_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

#: A repo segment equal to exactly one of these is a path-traversal token,
#: never a real owner or repository name.
_TRAVERSAL_SEGMENTS = (".", "..")


def valid_repo_shape(repo):
    """True when `repo` is exactly owner/name in the shape above, and
    neither side is a bare "." or "..". Called before token discovery and
    before any fetch, so a malformed --repo is refused as a usage error and
    never turns into a URL."""
    if not repo or not REPO_SHAPE_RE.match(repo):
        return False
    owner, name = repo.split("/", 1)
    return owner not in _TRAVERSAL_SEGMENTS and name not in _TRAVERSAL_SEGMENTS


#: A commit sha exactly as GitHub returns it: lowercase hex, plausible
#: length. `first_sha` is API-sourced (the pull request's head.sha), not
#: user-supplied like --repo, but it still reaches a URL path segment (the
#: check-runs fetch below) -- so it is checked at that one point rather than
#: trusted as already safe just because it came from a JSON body.
SHA_SHAPE_RE = re.compile(r"[0-9a-f]{7,40}")


def valid_sha_shape(sha):
    """True when `sha` is a plausible commit sha: lowercase hex, 7 to 40
    characters, nothing else."""
    return bool(sha) and bool(SHA_SHAPE_RE.fullmatch(sha))

#: The one-line remedy the spec requires next to every credential-shaped
#: NO-DATA. Worded without the other two verdict names on purpose: a
#: credentials-absent report must not carry them anywhere, not even as prose.
REMEDY = ("no token was found in GITHUB_TOKEN or GH_TOKEN and gh auth token "
          "supplied none; export GITHUB_TOKEN or run gh auth login, then re-run")

#: Controls whose evidence comes from the GitHub API. Their NO-DATA is what
#: drags FINAL to NO-DATA; a locally caused NO-DATA (an unpinned PR HEAD, an
#: empty evidence store) does not.
NETWORK_CONTROLS = ("PR EXISTS", "AUTHOR KNOWN", "INDEPENDENT APPROVAL",
                    "BOT APPROVAL", "REVIEW THREADS", "CODEOWNERS",
                    "REQUIRED CHECKS")

DEFAULT_POLICY = {"requireHumanApproval": True}


def _scrub(text, token):
    """Replace the token value anywhere it appears in a string. The token
    should never reach a detail string in the first place; this is the last
    line of defense, not the design."""
    if token and text and token in str(text):
        return str(text).replace(token, "[REDACTED:token]")
    return text


def discover_token():
    """(token, source, detail): GITHUB_TOKEN, then GH_TOKEN, then gh auth
    token. Three values, never two. A failure of the third is absence, never
    an error: gh missing, not installed, and not logged in are the same
    finding from here."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token, "GITHUB_TOKEN", "the GITHUB_TOKEN environment variable is set"
    token = os.environ.get("GH_TOKEN")
    if token:
        return token, "GH_TOKEN", "the GH_TOKEN environment variable is set"
    try:
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, None, "gh was not runnable here (%s)" % exc
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip(), "gh auth token", "gh auth token returned one"
    return None, None, "gh auth token returned nothing (exit %d)" % proc.returncode


#: The one host this command has evidence for: every fetch it makes is
#: against api.github.com. A remote hosted anywhere else has no GitHub API to
#: ask, so `remote_host` below is how that gets detected before a network
#: call is attempted, never after.
GITHUB_HOST = "github.com"


def _parse_remote_host(url):
    """The lowercase hostname in a git remote URL, or None when none can be
    read. Handles the usual scheme-prefixed forms (https, http, ssh, git) via
    urlsplit, and the scp-like ssh shorthand (user@host:owner/repo.git) that
    urlsplit does not parse, via a small regex. Never raises on a malformed
    or empty url; a url this cannot make sense of is None, not an exception."""
    url = (url or "").strip()
    if not url:
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url):
        try:
            host = urllib.parse.urlsplit(url).hostname
        except ValueError:  # sbe: allow-silent the caller turns this None into a spoken NO-DATA, "the origin remote for <path> did not parse to a recognizable host", so the finding reaches the reader with its path; only the parser's own exception text is dropped, and it says nothing a reader could act on
            return None
        return host.lower() if host else None
    match = re.match(r"^(?:[^@/]+@)?([^:/]+):", url)
    return match.group(1).lower() if match else None


def remote_host(cwd):
    """(host, detail). `host` is the lowercase hostname parsed from the
    origin remote configured at `cwd`, or None when `cwd` is empty, git could
    not be run there, no origin remote is configured, or the configured url
    did not parse to a host. `detail` is always set: a one-sentence account
    of what was found, safe to print in a NO-DATA line either way. Never
    raises; a subprocess that fails to run is a finding, not an exception."""
    if not cwd:
        return None, "no local repository was named, so no origin remote could be read"
    try:
        # `remote get-url` rather than `config --get remote.origin.url`: the
        # latter returns the raw configured value, so a genuinely GitHub-hosted
        # repository using url.<base>.insteadOf (a routine ssh key setup) reads
        # back as an alias host and would report NO-DATA where a real verdict
        # was obtainable. `remote get-url` resolves insteadOf first.
        proc = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "git was not runnable against %s (%s)" % (cwd, exc)
    url = proc.stdout.strip()
    if proc.returncode != 0 or not url:
        # git exits 128 when the path is not a repository at all, and 2 when a
        # repository has no such remote. Saying "no origin remote configured"
        # about a directory that is not a checkout is a false sentence in a
        # NO-DATA line, and a NO-DATA line is read by somebody deciding what to
        # fix next.
        stderr = (proc.stderr or "").strip().lower()
        if proc.returncode == 128 or "not a git repository" in stderr:
            return None, "%s is not a git repository, so it has no remote to read" % cwd
        return None, "%s has no origin remote configured" % cwd
    host = _parse_remote_host(url)
    if not host:
        return None, "the origin remote for %s did not parse to a recognizable host" % cwd
    return host, "the origin remote for %s resolves to host %s" % (cwd, host)


def _real_fetch(method, url, token):
    """The only code path that touches the network. (status, body, error):
    status is the HTTP code or None when the network was unreachable, body is
    parsed JSON or None, error is a short string set only when status is None.
    """
    if method != "GET":
        return None, None, "refused: this client is read-only and sends GET only"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/vnd.github+json")
    if token:
        request.add_header("Authorization", "Bearer %s" % token)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        return exc.code, None, None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return None, None, _scrub("network unreachable: %s" % reason, token)
    except (OSError, ValueError) as exc:
        return None, None, _scrub("network error: %s" % exc, token)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        body = None
    return status, body, None


def _latest_by_reviewer(reviews):
    """login -> the reviewer's latest live-state review, considering only the
    states that carry review weight (APPROVED, CHANGES_REQUESTED, DISMISSED).
    Sorted by submitted_at (ISO-8601 sorts lexicographically), stable, so
    same-timestamp entries keep list order."""
    latest = {}
    usable = [r for r in reviews if isinstance(r, dict)]
    for rev in sorted(usable, key=lambda r: r.get("submitted_at") or ""):
        user = rev.get("user") or {}
        login = user.get("login")
        state = (rev.get("state") or "").upper()
        if not login or state not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            continue
        latest[login] = rev
    return latest


def _local_head(cwd):
    """(sha, problem). The local HEAD of the repository `cwd` names, or a
    sentence about why there is none. Never a traceback."""
    try:
        proc = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "git was not runnable against %s (%s)" % (cwd, exc)
    if proc.returncode != 0:
        return None, "%s is not a git repository with a resolvable HEAD" % cwd
    return proc.stdout.strip(), None


def _repo_contains(cwd, sha):
    try:
        proc = subprocess.run(["git", "-C", cwd, "cat-file", "-e", sha + "^{commit}"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _evidence_store(cwd):
    """The evidence store path under `cwd`, or None when there is none. The
    layout is the one src/brothersbe/tasks.py fixes (DEFAULT_EVIDENCE_DIR)."""
    if not cwd:
        return None
    from . import tasks as tasks_mod
    store = tasks_mod.evidence_dir(cwd)
    return store if os.path.isdir(store) else None


def _freshness(store, head_sha):
    """(verdict, detail) for EVIDENCE FRESHNESS over a store that exists."""
    if not head_sha:
        return "NO-DATA", ("an evidence store exists at %s but the head sha was never "
                           "resolved, so nothing can be compared against it" % store)
    from . import evidence as evidence_mod
    receipts, unreadable = [], []
    for name in sorted(os.listdir(store)):
        path = os.path.join(store, name)
        if not name.endswith(".json") or not os.path.isfile(path):
            continue
        try:
            receipts.append((path, evidence_mod.load(path)))
        except evidence_mod.ReceiptUnreadable as exc:
            unreadable.append("%s was not readable as a receipt: %s" % (path, exc))
    if not receipts and not unreadable:
        return "NO-DATA", "the evidence store at %s holds no receipts" % store
    stale = [(path, receipt.get("headCommit")) for path, receipt in receipts
             if receipt.get("headCommit") != head_sha]
    if stale:
        named = "; ".join("%s is bound to %s but the head is %s"
                          % (path, bound or "no commit at all", head_sha)
                          for path, bound in stale)
        return "FAIL", named
    detail = "%d receipt(s) in %s bind to the head %s" % (len(receipts), store, head_sha)
    if unreadable:
        detail += "; unreadable and counted as nothing: " + "; ".join(unreadable)
        return "NO-DATA", detail
    return "PASS", detail


def evaluate(repo, number, token, fetch=None, cwd=None, head=None, policy=None,
             token_source=None):
    """The report dict tools/test_sbe_prverify.py pins. `fetch=None` means the
    real network transport; fixtures always inject their own."""
    policy = dict(DEFAULT_POLICY, **(policy or {}))
    if fetch is None:
        fetch = _real_fetch
    controls = []

    def control(name, verdict, detail):
        controls.append({"name": name, "verdict": verdict, "detail": detail})

    report = {
        "tool": "sbe pr verify",
        "repository": repo,
        "number": number,
        "headSha": None,
        "tokenSource": token_source or ("supplied directly" if token else None),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "taxonomy": ("this report uses UNVERIFIABLE as a fourth verdict beside the "
                     "registry's three; these controls live in src/brothersbe, not in "
                     "a Check registry, so the check-registry law of three is untouched"),
        "controls": controls,
        "final": None,
        "finalDetail": None,
    }

    store = _evidence_store(cwd)

    def _no_network(message):
        """The shared NO-DATA shortcut: every network control, PR HEAD, and
        EVIDENCE FRESHNESS (when a store exists) all read NO-DATA with the
        same one-line reason, and FINAL follows them. Used both when no
        token was found and when the origin remote is not GitHub; the two
        reasons are never combined into one sentence, so the operator is
        told the one thing that is actually true."""
        for name in NETWORK_CONTROLS:
            control(name, "NO-DATA", message)
        controls.insert(1, {"name": "PR HEAD", "verdict": "NO-DATA",
                            "detail": "the head sha was never fetched; " + message})
        if store:
            control("EVIDENCE FRESHNESS", "NO-DATA",
                    "an evidence store exists at %s but there is no head sha to bind "
                    "it to; %s" % (store, message))
        report["final"] = "NO-DATA"
        report["finalDetail"] = message
        for c in controls:
            c["detail"] = _scrub(c["detail"], token)
        report["finalDetail"] = _scrub(report["finalDetail"], token)

    if not token:
        _no_network(REMEDY)
        return report

    # Host check runs only once a token exists (credentials-absent already has
    # its own NO-DATA above) and only against the real transport: a fixture
    # always injects its own fetch, and no fixture cwd is answerable for a
    # live origin remote, so this must never fire under a canned fetch.
    if fetch is _real_fetch:
        host, host_detail = remote_host(cwd or os.getcwd())
        if host != GITHUB_HOST:
            _no_network(
                "%s; a real verdict needs a GitHub-hosted origin remote, because this "
                "command's only evidence source is the GitHub API" % host_detail)
            return report

    pr_url = "%s/repos/%s/pulls/%d" % (API_ROOT, repo, number)

    # First fetch: the head sha everything below is evaluated against.
    status, body, err = fetch("GET", pr_url, token)
    first_sha = None
    author_login = None
    author_type = None
    base_ref = None
    downstream = None  # (verdict, detail) when the PR itself did not resolve
    if status == 200 and isinstance(body, dict):
        first_sha = (body.get("head") or {}).get("sha")
        user = body.get("user") or {}
        author_login = user.get("login")
        author_type = user.get("type")
        base_ref = (body.get("base") or {}).get("ref")
        state = body.get("state")
        if not first_sha:
            control("PR EXISTS", "UNVERIFIABLE",
                    "the pull request resolved but carried no head sha")
            downstream = ("UNVERIFIABLE", "the head sha never resolved, so this "
                                          "control had nothing to evaluate against")
        elif state == "open":
            control("PR EXISTS", "PASS",
                    "pull request %d in %s resolved and is open" % (number, repo))
        else:
            control("PR EXISTS", "FAIL",
                    "pull request %d in %s resolved but its state is %r, not open"
                    % (number, repo, state))
    elif status == 404:
        control("PR EXISTS", "FAIL",
                "no pull request %d resolved in %s (HTTP 404)" % (number, repo))
        downstream = ("UNVERIFIABLE", "the pull request itself did not resolve, so "
                                      "this control had nothing to evaluate")
    elif status in (401, 403):
        control("PR EXISTS", "UNVERIFIABLE",
                "the token lacks permission to read pull request %d in %s (HTTP %d)"
                % (number, repo, status))
        downstream = ("UNVERIFIABLE",
                      "insufficient permission at the pull request itself (HTTP %d)"
                      % status)
    elif status is None:
        control("PR EXISTS", "NO-DATA",
                "the network did not answer: %s" % (err or "no reason recorded"))
        downstream = ("NO-DATA", "the network did not answer, so this control "
                                 "examined nothing")
    else:
        control("PR EXISTS", "UNVERIFIABLE",
                "the pull request endpoint answered HTTP %s with an unusable body"
                % status)
        downstream = ("UNVERIFIABLE", "the pull request endpoint answered HTTP %s"
                      % status)

    # PR HEAD: the assessed commit against the fetched head.
    if first_sha is None:
        verdict, detail = downstream
        control("PR HEAD", verdict, detail)
    elif head:
        if head == first_sha:
            control("PR HEAD", "PASS",
                    "the pinned commit %s equals the pull request head" % head)
        else:
            control("PR HEAD", "FAIL",
                    "the pinned commit is %s but the pull request head is %s"
                    % (head, first_sha))
    elif cwd:
        local, problem = _local_head(cwd)
        if local is None:
            control("PR HEAD", "NO-DATA",
                    "%s; pin an expected commit with --head to evaluate this control"
                    % problem)
        elif local == first_sha:
            control("PR HEAD", "PASS",
                    "the local HEAD %s equals the pull request head" % local)
        elif not _repo_contains(cwd, first_sha):
            control("PR HEAD", "NO-DATA",
                    "the repository at %s does not contain the pull request head %s, "
                    "so the two histories cannot be compared here" % (cwd, first_sha))
        else:
            control("PR HEAD", "FAIL",
                    "the local HEAD is %s but the pull request head is %s"
                    % (local, first_sha))
    else:
        control("PR HEAD", "NO-DATA",
                "no expected commit was pinned (--head) and no local repository was "
                "named (--cwd), so there is nothing to compare the head %s against"
                % first_sha)

    # AUTHOR KNOWN.
    if first_sha is None:
        control("AUTHOR KNOWN", downstream[0], downstream[1])
    elif author_login and author_login.lower() != "ghost":
        control("AUTHOR KNOWN", "PASS",
                "the author resolves to %s (type %s)" % (author_login,
                                                         author_type or "unknown"))
    else:
        control("AUTHOR KNOWN", "FAIL",
                "the author does not resolve to a live account (login %r)"
                % author_login)

    # Reviews feed three controls, plus CODEOWNERS below needs to know
    # whether a live, non-stale, non-self approval exists at all. `live_
    # approvals` is None when the reviews were never readable (permission,
    # network, or a bad body) -- that is the signal CODEOWNERS below uses to
    # tell "no satisfying approval" apart from "could not check".
    live_approvals = None
    valid_approvals = None
    reviews_unavailable = None
    if first_sha is None:
        for name in ("INDEPENDENT APPROVAL", "BOT APPROVAL", "REVIEW THREADS"):
            control(name, downstream[0], downstream[1])
        reviews_unavailable = downstream[1]
    else:
        r_status, r_body, r_err = fetch("GET", pr_url + "/reviews", token)
        if r_status == 200 and isinstance(r_body, list):
            latest = _latest_by_reviewer(r_body)
            approvals = [rev for rev in latest.values()
                         if (rev.get("state") or "").upper() == "APPROVED"]
            live_approvals = approvals
            valid, reasons = [], []
            for rev in approvals:
                login = (rev.get("user") or {}).get("login")
                bound = rev.get("commit_id")
                if login == author_login:
                    reasons.append("the approval by %s is the pull request author "
                                   "approving their own change" % login)
                elif bound and bound != first_sha:
                    reasons.append("the approval by %s is bound to %s but the head "
                                   "is %s; an approval predating the current head "
                                   "is stale" % (login, bound, first_sha))
                else:
                    valid.append(login)
            if valid:
                control("INDEPENDENT APPROVAL", "PASS",
                        "live approval by %s, independent of author %s, bound to "
                        "the head %s" % (", ".join(valid), author_login, first_sha))
            elif reasons:
                control("INDEPENDENT APPROVAL", "FAIL", "; ".join(reasons))
            else:
                control("INDEPENDENT APPROVAL", "FAIL",
                        "no live approval exists on this pull request "
                        "(latest state per reviewer, dismissals excluded)")

            if not approvals:
                control("BOT APPROVAL", "NO-DATA",
                        "no live approvals exist to classify as human or bot")
            else:
                humans = [(rev.get("user") or {}).get("login") for rev in approvals
                          if (rev.get("user") or {}).get("type") != "Bot"]
                bots = [(rev.get("user") or {}).get("login") for rev in approvals
                        if (rev.get("user") or {}).get("type") == "Bot"]
                if humans:
                    control("BOT APPROVAL", "PASS",
                            "at least one live approval is from a human account (%s)"
                            % ", ".join(humans))
                elif policy.get("requireHumanApproval", True):
                    control("BOT APPROVAL", "FAIL",
                            "every live approval is from a Bot account (%s) and "
                            "policy requires a human" % ", ".join(bots))
                else:
                    control("BOT APPROVAL", "PASS",
                            "only Bot approvals exist (%s), and this policy "
                            "explicitly allows that" % ", ".join(bots))

            blockers = [login for login, rev in sorted(latest.items())
                        if (rev.get("state") or "").upper() == "CHANGES_REQUESTED"]
            if blockers:
                control("REVIEW THREADS", "FAIL",
                        "the latest live review by %s is CHANGES_REQUESTED and "
                        "undismissed" % ", ".join(blockers))
            else:
                control("REVIEW THREADS", "PASS",
                        "no reviewer's latest live state is CHANGES_REQUESTED")
            valid_approvals = valid
        elif r_status in (401, 403):
            reviews_unavailable = ("the token lacks permission to read the reviews "
                                   "(HTTP %d)" % r_status)
            for name in ("INDEPENDENT APPROVAL", "BOT APPROVAL", "REVIEW THREADS"):
                control(name, "UNVERIFIABLE", reviews_unavailable)
        elif r_status is None:
            reviews_unavailable = ("the network did not answer for the reviews: %s"
                                   % (r_err or "no reason recorded"))
            for name in ("INDEPENDENT APPROVAL", "BOT APPROVAL", "REVIEW THREADS"):
                control(name, "NO-DATA", reviews_unavailable)
        else:
            reviews_unavailable = ("the reviews endpoint answered HTTP %s with an "
                                   "unusable body" % r_status)
            for name in ("INDEPENDENT APPROVAL", "BOT APPROVAL", "REVIEW THREADS"):
                control(name, "UNVERIFIABLE", reviews_unavailable)

    # Branch protection: the ONE authoritative source for REQUIRED CHECKS and
    # CODEOWNERS (F6+F7). required_status_checks.contexts drives REQUIRED
    # CHECKS; required_pull_request_reviews.require_code_owner_reviews drives
    # CODEOWNERS. Neither is ever inferred from the check-runs scan or a raw
    # CODEOWNERS file alone; a 403/404 on this ONE endpoint makes both
    # UNVERIFIABLE, naming the endpoint, rather than falling back to a guess.
    if first_sha is None:
        control("REQUIRED CHECKS", downstream[0], downstream[1])
        control("CODEOWNERS", downstream[0], downstream[1])
    elif not base_ref:
        msg = ("the pull request resolved but named no base branch, so branch "
              "protection could not be looked up to drive either control")
        control("REQUIRED CHECKS", "UNVERIFIABLE", msg)
        control("CODEOWNERS", "UNVERIFIABLE", msg)
    else:
        # base_ref is API-sourced (the pull request's base.ref) and is about
        # to be composed into a URL path segment; it is urlencoded with
        # safe="" so a "/" inside it (a legitimate character in a git ref)
        # becomes %2F rather than introducing an extra path segment, and per
        # GitHub's own documented branch-protection endpoint contract a
        # slash in the branch name must be percent-encoded to be addressed
        # at all.
        p_url = "%s/repos/%s/branches/%s/protection" % (
            API_ROOT, repo, urllib.parse.quote(base_ref, safe=""))
        p_status, p_body, p_err = fetch("GET", p_url, token)

        # The check-runs scan survives ONLY as ADVISORY prose folded into
        # REQUIRED CHECKS' detail; it never decides a verdict by itself, and
        # `run_by_name` is the one piece of it REQUIRED CHECKS actually uses,
        # to test each required context named by branch protection.
        run_by_name = {}
        if not valid_sha_shape(first_sha):
            # first_sha is API-sourced and is about to be composed straight
            # into a URL path segment; a value that does not look like a
            # commit sha is refused before any request is built, never
            # composed into one.
            k_status, runs = None, None
            advisory = ("ADVISORY (not verdict-determining): the pull request head %r "
                       "does not look like a commit sha (expected 7 to 40 lowercase hex "
                       "characters), so check runs were never requested" % first_sha)
        else:
            k_url = "%s/repos/%s/commits/%s/check-runs" % (API_ROOT, repo, first_sha)
            k_status, k_body, k_err = fetch("GET", k_url, token)
            runs = k_body.get("check_runs") if isinstance(k_body, dict) else None
            if k_status == 200 and isinstance(runs, list):
                for r in runs:
                    if isinstance(r, dict) and r.get("name"):
                        run_by_name[r["name"]] = r
                if runs:
                    advisory = "ADVISORY (not verdict-determining), every check run on %s: %s" % (
                        first_sha, ", ".join(
                            "%s=%s" % (r.get("name") or "(unnamed)",
                                      r.get("conclusion") or r.get("status") or "pending")
                            for r in runs if isinstance(r, dict)))
                else:
                    advisory = "ADVISORY (not verdict-determining): no check runs are reported on %s" % first_sha
            elif k_status in (401, 403):
                advisory = ("ADVISORY (not verdict-determining): the token lacks permission "
                           "to read check runs (HTTP %d)" % k_status)
            elif k_status is None:
                advisory = ("ADVISORY (not verdict-determining): the network did not answer "
                           "for check runs: %s" % (k_err or "no reason recorded"))
            else:
                advisory = ("ADVISORY (not verdict-determining): the check-runs endpoint "
                           "answered HTTP %s" % k_status)

        if p_status in (403, 404):
            msg = ("branch protection for %s is not readable by this token (HTTP %d at "
                  "%s); REQUIRED CHECKS and CODEOWNERS are never guessed from the "
                  "check-runs scan or a raw CODEOWNERS file alone" % (base_ref, p_status, p_url))
            control("REQUIRED CHECKS", "UNVERIFIABLE", msg)
            control("CODEOWNERS", "UNVERIFIABLE", msg)
        elif p_status == 200 and isinstance(p_body, dict):
            required = ((p_body.get("required_status_checks") or {}).get("contexts")) or []
            checkruns_readable = k_status == 200 and isinstance(runs, list)
            if not required:
                control("REQUIRED CHECKS", "PASS",
                        "branch protection for %s requires no status checks (nothing "
                        "required); %s" % (base_ref, advisory))
            elif not checkruns_readable:
                control("REQUIRED CHECKS", "UNVERIFIABLE",
                        "branch protection for %s requires context(s) %s, but their "
                        "status on %s could not be read: %s"
                        % (base_ref, ", ".join(required), first_sha, advisory))
            else:
                missing = [name for name in required if name not in run_by_name]
                failing = [name for name in required if name in run_by_name
                          and (run_by_name[name].get("status") != "completed"
                               or run_by_name[name].get("conclusion") not in GOOD_CONCLUSIONS)]
                if missing or failing:
                    reasons = []
                    if missing:
                        reasons.append("required context(s) missing on %s: %s"
                                      % (first_sha, ", ".join(missing)))
                    if failing:
                        reasons.append("required context(s) not passing on %s: %s"
                                      % (first_sha, ", ".join(failing)))
                    reasons.append(advisory)
                    control("REQUIRED CHECKS", "FAIL", "; ".join(reasons))
                else:
                    control("REQUIRED CHECKS", "PASS",
                            "all %d required context(s) for %s passed on %s; %s"
                            % (len(required), base_ref, first_sha, advisory))

            reviews_policy = p_body.get("required_pull_request_reviews") or {}
            require_code_owner = bool(reviews_policy.get("require_code_owner_reviews"))
            if not require_code_owner:
                control("CODEOWNERS", "NO-DATA",
                        "branch protection for %s does not require code owner reviews; "
                        "no applicable rule found" % base_ref)
            elif live_approvals is None:
                control("CODEOWNERS", "UNVERIFIABLE",
                        "branch protection for %s requires code owner reviews, but the "
                        "reviews needed to check that were not readable: %s"
                        % (base_ref, reviews_unavailable))
            elif valid_approvals:
                control("CODEOWNERS", "PASS",
                        "branch protection for %s requires code owner reviews, and a "
                        "live approval by %s satisfies it"
                        % (base_ref, ", ".join(valid_approvals)))
            else:
                control("CODEOWNERS", "FAIL",
                        "branch protection for %s requires code owner reviews, and no "
                        "live approval satisfies it" % base_ref)
        elif p_status is None:
            msg = ("the network did not answer for branch protection: %s"
                  % (p_err or "no reason recorded"))
            control("REQUIRED CHECKS", "NO-DATA", msg)
            control("CODEOWNERS", "NO-DATA", msg)
        else:
            msg = ("the branch protection endpoint answered HTTP %s with an unusable "
                  "body" % p_status)
            control("REQUIRED CHECKS", "UNVERIFIABLE", msg)
            control("CODEOWNERS", "UNVERIFIABLE", msg)

    # EVIDENCE FRESHNESS, only when the caller named a local evidence store.
    if store:
        verdict, detail = _freshness(store, first_sha)
        control("EVIDENCE FRESHNESS", verdict, detail)

    # Last fetch: the head again, to catch a force-push mid-run.
    race_detail = None
    last_sha = first_sha
    if first_sha is not None:
        f_status, f_body, f_err = fetch("GET", pr_url, token)
        if f_status == 200 and isinstance(f_body, dict) \
                and (f_body.get("head") or {}).get("sha"):
            last_sha = (f_body.get("head") or {}).get("sha")
            if last_sha != first_sha:
                race_detail = ("the head moved from %s to %s while this run was "
                               "evaluating; every control above was evaluated "
                               "against the first, so nothing above binds to the "
                               "current head: re-run" % (first_sha, last_sha))
        else:
            race_detail = ("the head could not be re-confirmed after evaluation "
                           "(HTTP %s%s), so a force-push during this run cannot be "
                           "ruled out: re-run"
                           % (f_status, ", " + f_err if f_err else ""))
    report["headSha"] = last_sha

    verdicts = [c["verdict"] for c in controls]
    network_verdicts = [c["verdict"] for c in controls
                        if c["name"] in NETWORK_CONTROLS]
    if "FAIL" in verdicts:
        final = "FAIL"
    elif "UNVERIFIABLE" in verdicts:
        final = "UNVERIFIABLE"
    elif "NO-DATA" in network_verdicts:
        final = "NO-DATA"
    else:
        final = "PASS"
    if race_detail and final != "FAIL":
        final = "UNVERIFIABLE"
    report["final"] = final
    report["finalDetail"] = race_detail

    # Last line of defense: the token value never reaches an output byte.
    for c in controls:
        c["detail"] = _scrub(c["detail"], token)
    report["finalDetail"] = _scrub(report["finalDetail"], token)
    return report


def render(report):
    """The human-readable form: header bound to repository, number and head
    sha, one line per control, FINAL last."""
    lines = []
    lines.append("sbe pr verify: %s #%s" % (report["repository"], report["number"]))
    lines.append("head sha: %s" % (report["headSha"]
                                   or "unresolved (the pull request was never fetched)"))
    lines.append("generated at: %s (wall clock; approvals are never cached, every run "
                 "re-fetches)" % report.get("generatedAt"))
    lines.append("token source: %s" % (report.get("tokenSource") or "none found"))
    lines.append("verdicts: %s" % report.get("taxonomy"))
    lines.append("")
    for c in report["controls"]:
        lines.append("  %-22s %-13s %s" % (c["name"], c["verdict"], c["detail"]))
    lines.append("")
    tail = "FINAL %s" % report["final"]
    if report.get("finalDetail"):
        tail += "  %s" % report["finalDetail"]
    lines.append(tail)
    return "\n".join(lines)


def _parser():
    parser = argparse.ArgumentParser(
        prog="sbe pr verify",
        description="Verify one pull request against live GitHub evidence, bound to "
                    "its head commit. Read-only: every request is a GET.")
    parser.add_argument("number", type=int, help="the pull request number")
    parser.add_argument("--repo", required=True,
                        help="the repository, as owner/name")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--cwd", default=None,
                        help="a local clone: its HEAD is compared to the pull request "
                             "head, and its evidence store (if any) is checked for "
                             "freshness")
    parser.add_argument("--head", default=None,
                        help="pin the expected head commit instead of reading a local "
                             "clone")
    return parser


def main(rest, exit_ok=0, exit_failed=1, exit_usage=2):
    """The `sbe pr` surface. Exit codes come from the caller so this module
    never disagrees with the CLI's documented table. Exit 0 only when FINAL
    reads clean; every other final verdict, including credentials-absent
    NO-DATA, exits nonzero."""
    rest = list(rest)
    if rest and rest[0] in ("-h", "--help"):
        # Help is not an error: `sbe pr -h` prints the one subcommand's usage
        # and exits 0, rather than being refused as a missing 'verify'.
        _parser().print_help(sys.stdout)
        return exit_ok
    if not rest or rest[0] != "verify":
        sys.stderr.write("sbe pr: the first positional is 'verify' "
                         "(sbe pr verify <number> --repo owner/name); got %s\n"
                         % (repr(rest[0]) if rest else "nothing"))
        return exit_usage
    parser = _parser()
    try:
        args = parser.parse_args(rest[1:])
    except SystemExit as exc:
        # argparse already answered: -h/--help printed the usage the caller
        # asked for and raised 0, and help is not an error. Anything nonzero
        # is a refused flag or argument.
        return exit_ok if exc.code in (0, None) else exit_usage
    if not valid_repo_shape(args.repo):
        sys.stderr.write(
            "sbe pr verify: --repo must look like owner/name, where owner and name "
            "each match [A-Za-z0-9_.-]+ and nothing else (no extra slashes, no path "
            "traversal); got %r\n" % args.repo)
        return exit_usage

    token, source, _detail = discover_token()
    report = evaluate(args.repo, args.number, token, cwd=args.cwd, head=args.head,
                      token_source=source)
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render(report) + "\n")
    return exit_ok if report["final"] == "PASS" else exit_failed
