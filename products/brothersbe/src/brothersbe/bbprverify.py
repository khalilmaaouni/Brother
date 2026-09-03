"""`sbe pr verify` for a Bitbucket Cloud-hosted repository: live evidence for
one pull request, bound to its head commit.

Sibling of src/brothersbe/prverify.py, the GitHub implementation. This file
mirrors its structure, verdict vocabulary, docstring register, and security
posture as closely as the Bitbucket Cloud REST API allows; every place the
two APIs genuinely differ is called out at the point it happens, never left
implicit. tools/test_sbe_bbprverify.py pins this module's calling
convention the same way tools/test_sbe_prverify.py pins prverify.py's.

ENDPOINT (one, not several). Unlike GitHub, Bitbucket embeds the full
participants list on the pull request resource itself, so there is no
separate reviews call:

  GET https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}
  Docs: https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/#api-repositories-workspace-repo-slug-pullrequests-pull-request-id-get
  Schema verified against Bitbucket's own OpenAPI v3 document (component
  schemas "pullrequest", "pullrequest_endpoint", "participant", "account",
  "user"), fetched 2026-08-17 from
  https://developer.atlassian.com/cloud/bitbucket/swagger.v3.json -- not
  taken from memory, per this task's own instruction not to guess at an API
  that changes.

SCHEMA FACTS this file leans on, read directly out of that document:
  - the head commit hash lives at source.commit.hash (source is a
    pullrequest_endpoint object; GitHub's equivalent is head.sha).
  - participants is an array of {user: account, role: "PARTICIPANT" or
    "REVIEWER", approved: bool, state: "approved"/"changes_requested"/null,
    participated_on: an ISO8601 timestamp}. Critically, a participant
    carries NO commit reference anywhere -- no field analogous to GitHub's
    review.commit_id, which is exactly what prverify.py's staleness check
    binds an approval to a head with. There is nothing here to bind
    participated_on to a commit, and pullrequest.updated_on is not a
    substitute: it moves on comments and other activity too, not only on a
    push. That is why APPROVAL IS CURRENT below is NO-DATA whenever an
    approval exists -- inventing an answer from a timestamp would be a
    guess, not a certified verdict, and this project's law is that absent
    evidence is NO-DATA, never a pass.
  - "account" (the base object every user is at least) guarantees `uuid`
    and `display_name`. `account_id` and `nickname` only appear on the
    richer "user" subtype. No email field appears anywhere in either
    schema, confirming the brief's expectation that Bitbucket commonly
    omits it. `uuid` is therefore the identity this file compares on for
    INDEPENDENT APPROVAL, falling back to `account_id`, then `nickname`;
    `display_name` is deliberately excluded from identity comparison
    (unlike the prose it is fine for) because two accounts can share one,
    and a certificate of independence must never rest on a comparison that
    could be one person twice.
  - pull request state is one of OPEN/MERGED/DECLINED/SUPERSEDED, not
    GitHub's open/closed; only OPEN is a live pull request here.

AUTHENTICATION. Atlassian's own guidance
(https://support.atlassian.com/bitbucket-cloud/docs/app-passwords/, checked
2026-08-17) states API tokens "are the long term replacement for App
passwords". BITBUCKET_TOKEN (an API token, sent as a Bearer credential) is
tried first for that reason; BITBUCKET_USERNAME plus BITBUCKET_APP_PASSWORD
(sent as HTTP Basic) is the fallback for teams still on the deprecated path.
Unlike GitHub's `gh auth token`, there is no single official Bitbucket CLI
this project can shell out to, so there is no third discovery source.

Laws this file carries, unchanged from prverify.py wherever nothing about
Bitbucket makes the law inapplicable:

- Read-only client. The only network code path is `_real_fetch`, sends GET
  and nothing else, holds the credential in memory only, and runs under a
  hard timeout. `fetch` is a PARAMETER of `evaluate`, so every fixture
  injects a canned one and never opens a socket.
- The credential is never persisted, never printed, never written into the
  report; the report records only which discovery source supplied it, by
  name. Every detail string is scrubbed against both the raw secret and the
  constructed Authorization header value before the report is returned.
- A missing credential is NO-DATA with a remedy and a nonzero exit, never a
  clean verdict either way. With no credential, ZERO network attempts are
  made.
- A repository whose origin remote is not bitbucket.org, or has no origin
  remote at all, is the same kind of absence: NO-DATA naming the host that
  was actually found, checked before the first network call, against the
  real transport only. This file was told to reuse prverify.py's
  `remote_host` rather than write a second one; `_remote_host` below is a
  same-logic stand-in used only because this worktree's checkout of
  prverify.py predates that function (see `_remote_host`'s own docstring
  for the exact reason and the cleanup this leaves behind).
- Every HTTP failure maps to a verdict from this file's own table. A
  traceback reaching the user is a defect. UNVERIFIABLE is the fourth
  report-level verdict, same as prverify.py, for the same reason: these
  controls live here, not in a Check registry.
- The pull request resource is fetched FIRST to learn the head commit hash
  every control is evaluated against, and LAST to detect a push mid-run. A
  race is UNVERIFIABLE with re-run prose.

ONE DELIBERATE DIVERGENCE from prverify.py's verdict table: a 404 on the
pull request resource is NO-DATA here, not FAIL. GitHub's 404 for pull
requests reliably means "does not exist" (a token lacking access gets 401
or 403 instead). Bitbucket Cloud is documented to return 404 -- not 403 --
for a resource this credential lacks permission to see, specifically so the
response does not confirm the resource's existence to an unauthorized
caller. From here those two cases are indistinguishable, so asserting FAIL
("no such pull request") would sometimes be asserting a false certainty;
NO-DATA is the honest reading.

Python floor is 3.9, standard library only.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# NOT importing prverify here: this worktree's checkout of prverify.py does
# not yet carry `remote_host` (see `_remote_host` below for why, and for the
# instruction this deviates from). Nothing else in this module needs
# prverify at all.

API_ROOT = "https://api.bitbucket.org/2.0"
TIMEOUT_SECONDS = 10

#: workspace/repo_slug, each side [A-Za-z0-9_.-]+ and nothing else -- the
#: same shape prverify.py's REPO_SHAPE_RE enforces for owner/name, because
#: Bitbucket's own slug rules are a subset of GitHub's. Reimplemented here
#: rather than imported: this file was told to reuse `remote_host`
#: specifically, and a ten-line regex is cheaper to own locally than to
#: reach into prverify.py's private names for.
REPO_SHAPE_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TRAVERSAL_SEGMENTS = (".", "..")


def valid_repo_shape(repo):
    """True when `repo` is exactly workspace/repo_slug in the shape above,
    and neither side is a bare "." or "..". Checked before credential
    discovery and before any fetch."""
    if not repo or not REPO_SHAPE_RE.match(repo):
        return False
    workspace, slug = repo.split("/", 1)
    return workspace not in _TRAVERSAL_SEGMENTS and slug not in _TRAVERSAL_SEGMENTS


#: The one host this command has evidence for.
BITBUCKET_HOST = "bitbucket.org"


def _remote_host(cwd):
    """(host, detail), delegated to prverify.remote_host, which is the single
    implementation of this for either host.

    It was briefly duplicated. The builder that wrote this module was isolated
    onto a worktree whose checkout predated that helper, and it wrote a
    stand-in and flagged the staleness rather than merging diverged history on
    its own, which was the right call from where it stood. In a current tree
    there is no reason for two copies of git-generic host detection, and two
    copies drifting apart is precisely the failure this file exists to stop
    repeating between the GitHub and Bitbucket sides.

    Imported inside the function rather than at module scope on purpose: the
    zero-network scan reads this file's imports, and the two modules already
    reference each other's report vocabulary; a local import keeps the
    dependency one-directional at load time."""
    from . import prverify as _prverify
    return _prverify.remote_host(cwd)


def _parse_remote_host(url):
    """The lowercase hostname in a git remote URL, or None. See
    `_remote_host`'s docstring for why this exists alongside
    prverify.py's identical `_parse_remote_host` rather than importing it."""
    url = (url or "").strip()
    if not url:
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url):
        try:
            host = urllib.parse.urlsplit(url).hostname
        except ValueError:
            return None  # sbe: allow-silent this function's whole contract is "the
            # hostname, or None", and a URL urlsplit refuses genuinely has no
            # hostname to return; every caller already treats None as "no host
            # could be read" and says so rather than assuming a host
        return host.lower() if host else None
    match = re.match(r"^(?:[^@/]+@)?([^:/]+):", url)
    return match.group(1).lower() if match else None

REMEDY = ("no Bitbucket credential was found: export BITBUCKET_TOKEN (an Atlassian API "
          "token, sent as Bearer) or export both BITBUCKET_USERNAME and "
          "BITBUCKET_APP_PASSWORD (sent as HTTP Basic), then re-run")

#: Controls whose evidence comes from the Bitbucket API. Their NO-DATA is
#: what drags FINAL to NO-DATA; PR HEAD is deliberately excluded, the same
#: way prverify.py excludes it: an unpinned head is a locally caused
#: NO-DATA, not an absence of API evidence.
NETWORK_CONTROLS = ("PR EXISTS", "AUTHOR KNOWN", "INDEPENDENT APPROVAL",
                    "APPROVAL IS CURRENT")


def _scrub(text, secret):
    """Replace `secret` anywhere it appears in a string. Last line of
    defense, not the design: the credential should never reach a detail
    string in the first place."""
    if secret and text and secret in str(text):
        return str(text).replace(secret, "[REDACTED:credential]")
    return text


def discover_credential():
    """(header, secret, source, detail). `header` is the ready-to-send
    Authorization value (already carries its scheme, Bearer or Basic).
    `secret` is the one raw value to scrub out of every detail string --
    the bare API token, or the app password alone (never the username,
    which is not sensitive). `source` names which discovery path supplied
    it; `detail` is always a sentence safe to print either way."""
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
    return (None, None, None,
            "no BITBUCKET_TOKEN was found, and BITBUCKET_USERNAME/BITBUCKET_APP_PASSWORD "
            "were not both set")


def _real_fetch(method, url, token):
    """The only code path that touches the network. (status, body, error):
    status is the HTTP code or None when the network was unreachable, body
    is parsed JSON or None, error is a short string set only when status is
    None. `token` here is the ready Authorization header value."""
    if method != "GET":
        return None, None, "refused: this client is read-only and sends GET only"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/json")
    if token:
        request.add_header("Authorization", token)
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


def _identity(account):
    """(kind, value) from the first of uuid, account_id, nickname that is
    present on `account`, or None when none of the three are. `account` may
    be a full "user" object or the bare "account" schema; either way,
    `display_name` is never used here -- it is not guaranteed unique, and
    an independence certificate must never rest on a comparison that could
    be one person twice."""
    account = account or {}
    for key in ("uuid", "account_id", "nickname"):
        value = account.get(key)
        if value:
            return (key, value)
    return None


def _label(account):
    """A human-readable name for prose only, never for identity comparison."""
    account = account or {}
    return (account.get("display_name") or account.get("nickname")
           or account.get("uuid") or "(unnamed account)")


def evaluate(repo, number, token, fetch=None, cwd=None, head=None, token_source=None,
            secret=None):
    """The report dict tools/test_sbe_bbprverify.py pins. `fetch=None` means
    the real network transport; fixtures always inject their own. `token` is
    the ready Authorization header value (see discover_credential); `secret`
    is the raw value to scrub."""
    if fetch is None:
        fetch = _real_fetch
    controls = []

    def control(name, verdict, detail):
        controls.append({"name": name, "verdict": verdict, "detail": detail})

    report = {
        "tool": "sbe pr verify (bitbucket)",
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

    def _no_network(message):
        """The shared NO-DATA shortcut: every network control and PR HEAD
        all read NO-DATA with the same one-line reason, and FINAL follows
        them. Used both when no credential was found and when the origin
        remote is not Bitbucket; the two reasons are never combined into
        one sentence."""
        for name in NETWORK_CONTROLS:
            control(name, "NO-DATA", message)
        controls.insert(1, {"name": "PR HEAD", "verdict": "NO-DATA",
                            "detail": "the head commit hash was never fetched; " + message})
        report["final"] = "NO-DATA"
        report["finalDetail"] = message
        for c in controls:
            c["detail"] = _scrub(_scrub(c["detail"], secret), token)
        report["finalDetail"] = _scrub(_scrub(report["finalDetail"], secret), token)

    if not token:
        _no_network(REMEDY)
        return report

    # Host check runs only once a credential exists, and only against the
    # real transport: a fixture always injects its own fetch, and no
    # fixture cwd is answerable for a live origin remote.
    if fetch is _real_fetch:
        host, host_detail = _remote_host(cwd or os.getcwd())
        if host != BITBUCKET_HOST:
            _no_network(
                "%s; a real verdict needs a Bitbucket-hosted origin remote, because this "
                "command's only evidence source is the Bitbucket Cloud API" % host_detail)
            return report

    pr_url = "%s/repositories/%s/pullrequests/%d" % (API_ROOT, repo, number)

    # First fetch: the head commit hash everything below is evaluated
    # against, plus the author and the embedded participants list.
    status, body, err = fetch("GET", pr_url, token)
    first_sha = None
    author = None
    participants = []
    downstream = None
    if status == 200 and isinstance(body, dict):
        source = body.get("source") or {}
        commit = source.get("commit") or {}
        first_sha = commit.get("hash")
        author = body.get("author") or {}
        participants = [p for p in (body.get("participants") or []) if isinstance(p, dict)]
        state = body.get("state")
        if not first_sha:
            control("PR EXISTS", "UNVERIFIABLE",
                    "the pull request resolved but carried no head commit hash")
            downstream = ("UNVERIFIABLE", "the head commit hash never resolved, so this "
                                          "control had nothing to evaluate against")
        elif state == "OPEN":
            control("PR EXISTS", "PASS",
                    "pull request %d in %s resolved and is OPEN" % (number, repo))
        else:
            control("PR EXISTS", "FAIL",
                    "pull request %d in %s resolved but its state is %r, not OPEN"
                    % (number, repo, state))
    elif status == 404:
        # Deliberate divergence from prverify.py: see the module docstring.
        # Bitbucket returns 404 both for a pull request that does not exist
        # and for one this credential lacks permission to see, so a 404
        # here is never read as a certified "does not exist".
        control("PR EXISTS", "NO-DATA",
                "no pull request %d resolved in %s (HTTP 404); Bitbucket Cloud returns 404 "
                "both when a pull request does not exist and when this credential lacks "
                "permission to see it, so this cannot be read as a certified absence"
                % (number, repo))
        downstream = ("NO-DATA", "the pull request did not resolve (HTTP 404, ambiguous "
                                 "between not-found and no-permission on Bitbucket), so "
                                 "this control examined nothing")
    elif status in (401, 403):
        control("PR EXISTS", "UNVERIFIABLE",
                "the credential lacks permission to read pull request %d in %s (HTTP %d)"
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

    # PR HEAD: the assessed commit against the fetched head. No --cwd local
    # comparison here (unlike prverify.py): none of this file's five
    # controls need it, and duplicating that git plumbing for a control
    # nothing exercises would be scope prverify.py's own brief did not ask
    # for. --head still lets a caller pin an expected commit.
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
    else:
        control("PR HEAD", "NO-DATA",
                "no expected commit was pinned (--head), so there is nothing to compare "
                "the head %s against" % first_sha)

    # AUTHOR KNOWN.
    if first_sha is None:
        control("AUTHOR KNOWN", downstream[0], downstream[1])
    else:
        author_id = _identity(author)
        if author_id is not None:
            control("AUTHOR KNOWN", "PASS",
                    "the author resolves to %s (%s %s)"
                    % (_label(author), author_id[0], author_id[1]))
        else:
            control("AUTHOR KNOWN", "FAIL",
                    "the author does not resolve to a live account (no uuid, account_id, "
                    "or nickname present)")

    # INDEPENDENT APPROVAL and APPROVAL IS CURRENT both read the same
    # embedded participants list; `approvals` is computed once and reused.
    if first_sha is None:
        control("INDEPENDENT APPROVAL", downstream[0], downstream[1])
        control("APPROVAL IS CURRENT", downstream[0], downstream[1])
    else:
        author_id = _identity(author)
        approvals = [p for p in participants if p.get("approved") is True]

        valid, self_reasons, unresolvable = [], [], []
        for p in approvals:
            user = p.get("user") or {}
            approver_id = _identity(user)
            label = _label(user)
            if approver_id is None or author_id is None:
                unresolvable.append(
                    "the approval by %s could not be compared to the author with a "
                    "stable identifier (uuid/account_id/nickname absent on one side)"
                    % label)
            elif approver_id == author_id:
                self_reasons.append(
                    "the approval by %s is the pull request author approving their own "
                    "change" % label)
            else:
                valid.append(label)

        if valid:
            control("INDEPENDENT APPROVAL", "PASS",
                    "live approval by %s, independent of author %s"
                    % (", ".join(valid), _label(author)))
        elif unresolvable:
            control("INDEPENDENT APPROVAL", "NO-DATA",
                    "; ".join(unresolvable) + "; independence cannot be certified without "
                    "a stable identifier, so this is not read as a pass")
        elif self_reasons:
            control("INDEPENDENT APPROVAL", "FAIL", "; ".join(self_reasons))
        else:
            control("INDEPENDENT APPROVAL", "FAIL",
                    "no live approval exists on this pull request")

        if not approvals:
            control("APPROVAL IS CURRENT", "NO-DATA",
                    "no approval exists on this pull request to evaluate for staleness")
        else:
            control("APPROVAL IS CURRENT", "NO-DATA",
                    "Bitbucket's participant object records only participated_on (a "
                    "timestamp) and carries no commit reference the approval was bound "
                    "to, unlike GitHub's review.commit_id; this API has no way to "
                    "certify whether the approval(s) by %s predate the current head %s"
                    % (", ".join(_label(p.get("user") or {}) for p in approvals), first_sha))

    # Last fetch: the head again, to catch a force-push mid-run.
    race_detail = None
    last_sha = first_sha
    if first_sha is not None:
        f_status, f_body, f_err = fetch("GET", pr_url, token)
        f_sha = None
        if f_status == 200 and isinstance(f_body, dict):
            f_source = f_body.get("source") or {}
            f_sha = (f_source.get("commit") or {}).get("hash")
        if f_sha:
            last_sha = f_sha
            if last_sha != first_sha:
                race_detail = ("the head moved from %s to %s while this run was "
                               "evaluating; every control above was evaluated "
                               "against the first, so nothing above binds to the "
                               "current head: re-run" % (first_sha, last_sha))
        else:
            race_detail = ("the head could not be re-confirmed after evaluation "
                           "(HTTP %s%s), so a push during this run cannot be "
                           "ruled out: re-run"
                           % (f_status, ", " + f_err if f_err else ""))
    report["headSha"] = last_sha

    verdicts = [c["verdict"] for c in controls]
    network_verdicts = [c["verdict"] for c in controls if c["name"] in NETWORK_CONTROLS]
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

    # Last line of defense: the credential value never reaches an output
    # byte, scrubbed as both the raw secret and the constructed header.
    for c in controls:
        c["detail"] = _scrub(_scrub(c["detail"], secret), token)
    report["finalDetail"] = _scrub(_scrub(report["finalDetail"], secret), token)
    return report


def render(report):
    """The human-readable form: header bound to repository, number and head
    sha, one line per control, FINAL last."""
    lines = []
    lines.append("sbe pr verify (bitbucket): %s #%s" % (report["repository"], report["number"]))
    lines.append("head commit: %s" % (report["headSha"]
                                      or "unresolved (the pull request was never fetched)"))
    lines.append("generated at: %s (wall clock; approvals are never cached, every run "
                 "re-fetches)" % report.get("generatedAt"))
    lines.append("credential source: %s" % (report.get("tokenSource") or "none found"))
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
        prog="sbe pr verify (bitbucket)",
        description="Verify one pull request against live Bitbucket Cloud evidence, bound "
                    "to its head commit. Read-only: every request is a GET.")
    parser.add_argument("number", type=int, help="the pull request number")
    parser.add_argument("--repo", required=True,
                        help="the repository, as workspace/repo_slug")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--head", default=None,
                        help="pin the expected head commit instead of merely reporting it")
    parser.add_argument("--cwd", default=None,
                        help="a local clone: its origin remote must resolve to "
                             "bitbucket.org for a real (non-fixture) run to proceed")
    return parser


def main(rest, exit_ok=0, exit_failed=1, exit_usage=2):
    """The Bitbucket `sbe pr verify` surface. Exit codes come from the
    caller so this module never disagrees with the CLI's documented table.
    Exit 0 only when FINAL reads clean; every other final verdict,
    including credentials-absent NO-DATA, exits nonzero."""
    rest = list(rest)
    if rest and rest[0] in ("-h", "--help"):
        _parser().print_help(sys.stdout)
        return exit_ok
    if not rest or rest[0] != "verify":
        sys.stderr.write("sbe pr (bitbucket): the first positional is 'verify' "
                         "(sbe pr verify <number> --repo workspace/repo_slug); got %s\n"
                         % (repr(rest[0]) if rest else "nothing"))
        return exit_usage
    parser = _parser()
    try:
        args = parser.parse_args(rest[1:])
    except SystemExit as exc:
        return exit_ok if exc.code in (0, None) else exit_usage
    if not valid_repo_shape(args.repo):
        sys.stderr.write(
            "sbe pr verify (bitbucket): --repo must look like workspace/repo_slug, where "
            "each side matches [A-Za-z0-9_.-]+ and nothing else (no extra slashes, no path "
            "traversal); got %r\n" % args.repo)
        return exit_usage

    token, secret, source, _detail = discover_credential()
    report = evaluate(args.repo, args.number, token, cwd=args.cwd, head=args.head,
                      token_source=source, secret=secret)
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render(report) + "\n")
    return exit_ok if report["final"] == "PASS" else exit_failed
