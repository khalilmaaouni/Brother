"""`sbe protections verify`: is the repository itself protecting the control plane.

Spec of record: BrotherSBE_RELEASE_BLOCKERS_FOR_FABLE.md item 5 (5.1 CODEOWNERS,
5.2 the ruleset for the default branch, 5.3 this verifier). The fixtures in
tools/test_sbe_protections.py drive this module directly through `evaluate` and
`render`; the calling convention they pin (the report dict shape, the
three-value fetch contract, the control names) is authoritative here.

The point of the command, in one sentence: a green workflow is not a merge gate
unless the repository requires it, and nothing inside this repository can prove
that the repository requires it. So this command asks GitHub, and when it cannot
ask, it says so instead of guessing.

Laws this file carries, mirrored from its sibling src/brothersbe/prverify.py:

- Read-only client. The only code path that leaves the machine is `_gh_fetch`,
  it shells out to `gh api` with no method flag (gh sends GET when none is
  given), and it refuses any method other than the literal "GET" before it
  builds an argument vector. The fetch function is a PARAMETER of `evaluate`,
  so every fixture injects a canned one and no test ever opens a socket.
- THE NO-NETWORK PATH, and it is the honest one. When no token is discoverable
  or `gh` is not on PATH, this command makes ZERO requests and reports NO-DATA
  once per network fact it could not check, each with the same remedy. NO-DATA
  is never a pass. It is also not, by itself, proof of a broken repository: the
  BrotherSBE release checklist is what treats it as BLOCKING, and RELEASE_NOTE
  below says exactly that in the tool's own output so the operator reads it
  next to the verdict rather than in a document somewhere else.
- The CODEOWNERS leg needs no network at all and therefore always runs. A
  missing or uncovering CODEOWNERS file is a FAIL, not a NO-DATA: the file is
  local, this command read the local tree, and absence that was actually
  looked for is evidence.
- The token is never persisted, never printed, never written into the report;
  the report records only which discovery source supplied it, by name, and
  every detail string is scrubbed against the token value before the report is
  returned.
- UNVERIFIABLE is a fourth report-level verdict, exactly as in prverify: these
  controls live here, not in a Check registry, so the check-registry law of
  three is untouched. The rendered report states that in its own header.
- A repository whose origin remote is not github.com, or has no origin
  remote at all, gets the same NO-DATA treatment as no credentials: named by
  host (or by the absence of one), never a `gh api` attempt, never a pass and
  never a blocking failure. Checked with `prverify.remote_host`, before the
  first request, against the real transport only.

HONEST LIMIT ON THE FIELD NAMES. The endpoint shapes below (the branch rules
list, the ruleset detail and their parameter keys) are pinned by the canned
fixtures in tools/test_sbe_protections.py and were NOT confirmed against a live
GitHub response while this file was written, because this engine has zero
network egress. That is why every parameter this file reads is read
defensively: a key that is absent from the response is NO-DATA ("present but
unreadable"), never a PASS and never silently a FAIL. If a live run reports
NO-DATA on a fact the repository really does configure, the field name here is
what to fix, and the fixture is what pins the fix.

Python floor is 3.9, standard library only.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import prverify as prverify_mod

#: The unreserved set of RFC 3986. `_encode_segment` below percent-encodes
#: everything else, which is what `urllib.parse.quote(text, safe="")` does.
#: It is written out here rather than imported because tools/test_sbe.py's
#: zero-network AST law bans a urllib import outside one exact allow-listed
#: path, and this module genuinely opens no socket: it shells out to `gh`.
#: Importing urllib for one string function would have made the module look
#: like a second HTTP client to that scan, and the scan is right to ask.
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _encode_segment(text):
    """Percent-encode `text` so it is exactly one URL path segment.

    A git ref may legitimately contain "/", and a "/" left raw would introduce
    an extra path segment into the endpoint this module asks `gh` for. Every
    byte outside the unreserved set is encoded, "/" included."""
    out = []
    for byte in text.encode("utf-8"):
        char = chr(byte)
        out.append(char if char in _UNRESERVED else "%%%02X" % byte)
    return "".join(out)

#: How long a single `gh api` invocation may take before it counts as no answer.
TIMEOUT_SECONDS = 20

#: The control plane surface item 5.1 requires an owner for. Compared against
#: the CODEOWNERS file as exact pattern strings: a file that owns
#: "/tools/" instead of "/tools/sbe_gate.py" is not the declaration the spec
#: asked for, and this command will not read one as the other.
CONTROL_PLANE_PATHS = (
    "/.github/workflows/",
    "/.github/actions/sbe-consumer/",
    "/hooks/",
    "/.claude-plugin/",
    "/.sbe/policy.yml",
    "/.sbe/checks.yml",
    "/src/brothersbe/evidence.py",
    "/src/brothersbe/policy.py",
    "/tools/sbe_*hook*.py",
    "/tools/sbe_*guard*.py",
    "/tools/sbe_gate.py",
)

#: The three locations GitHub reads a CODEOWNERS file from, in its own order.
CODEOWNERS_LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")

#: The two facts that need no network. They always run, and their NO-DATA (a
#: path that is not a readable directory) is a local failure to look, never a
#: verdict about the repository on GitHub.
LOCAL_CONTROLS = ("CODEOWNERS FILE", "CODEOWNERS COVERAGE")

#: The seven ruleset facts of item 5.3, in the order that document lists them.
NETWORK_CONTROLS = ("RULESET EXISTS", "REQUIRED CHECKS", "CODEOWNERS REVIEW",
                    "STALE REVIEWS DISMISSED", "LAST PUSH APPROVAL",
                    "FORCE PUSH DISABLED", "BYPASS ACTORS")

#: The one-line remedy printed beside every credentials-absent NO-DATA.
REMEDY = ("no GitHub access was found: no token in GITHUB_TOKEN or GH_TOKEN, and "
          "gh auth token supplied none, or the gh executable is not on PATH; "
          "install gh and run gh auth login, or export GITHUB_TOKEN, then re-run")

#: Printed on every run, in every mode, next to the verdict. The house law is
#: that NO-DATA is never a pass; the release checklist is what makes it block,
#: and an operator reading this report has to be told that here.
RELEASE_NOTE = (
    "NO-DATA in this report means this run could not look, never that the "
    "repository is fine. The BrotherSBE release checklist treats every NO-DATA "
    "line here as BLOCKING and stores this output as the ruleset verification "
    "evidence. This command exits nonzero for any final verdict other than PASS."
)

#: The sentence this report prints about itself when the CODEOWNERS leg passes,
#: because a covered path is not an independent reviewer.
COVERAGE_IS_NOT_INDEPENDENCE = (
    "coverage is not independence: an owner who is also the pull request author "
    "cannot satisfy a second-party review, and this command does not claim they can"
)

#: `gh` writes the HTTP status into its own error text; this is how a failed
#: invocation is turned back into a status code rather than a mystery.
_GH_STATUS_RE = re.compile(r"\(HTTP (\d{3})\)")

#: A ruleset id as it appears on a rule. Digits only: it is composed into a URL
#: path segment below, so it is checked at that point rather than trusted for
#: having arrived inside a JSON body.
_RULESET_ID_RE = re.compile(r"[0-9]{1,19}")

#: A branch segment equal to exactly one of these is a path-traversal token.
_TRAVERSAL_SEGMENTS = (".", "..")


def valid_branch_shape(branch):
    """True when `branch` can be percent-encoded into one URL path segment.

    Checked before token discovery and before any request, so a malformed
    --branch is a usage error and never turns into an argument vector."""
    if not branch or branch.startswith("-"):
        return False
    if branch in _TRAVERSAL_SEGMENTS or branch.strip() != branch:
        return False
    return not any(ch in branch for ch in ("\n", "\r", "\t", "\x00", " "))


def _scrub(text, token):
    """Replace the token value anywhere it appears in a string.

    The same last line of defense as `_scrub` in src/brothersbe/prverify.py,
    written out here rather than imported because that name is module-private
    there. The token should never reach a detail string in the first place."""
    if token and text and token in str(text):
        return str(text).replace(token, "[REDACTED:token]")
    return text


def gh_transport_problem():
    """A sentence naming why `gh api` cannot be used here, or None when it can.

    One value, never a pair: this is not a verdict source."""
    if shutil.which("gh") is None:
        return "the gh executable is not on PATH, so gh api cannot be invoked"
    return None


def _gh_fetch(method, path, token):
    """The only code path that leaves this machine. (status, body, error).

    Three values, never two: a two-value return would read as a possible
    (verdict, evidence) pair to evals/test_no_data_class.py, and this is not a
    verdict source. `status` is the HTTP status code, or None when there was no
    answer at all; `body` is the parsed JSON, or None; `error` is a short
    sentence set only when `status` is None.

    `gh api <path>` sends GET when no method is given. No `-X`, `--method`,
    `-f` or `--input` is ever constructed here, which is the read-only law the
    source-level fixture in tools/test_sbe_protections.py enforces.
    """
    if method != "GET":
        return None, None, "refused: this client is read-only and sends GET only"
    argv = ["gh", "api", "-H", "Accept: application/vnd.github+json", path]
    env = dict(os.environ)
    if token:
        # gh reads GH_TOKEN before its own stored credentials. Passing the
        # discovered token explicitly keeps one discovery order (the sibling's)
        # rather than two, and the value stays in this child environment only.
        env["GH_TOKEN"] = token
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, None, _scrub("gh api was not runnable here (%s)" % exc, token)
    if proc.returncode != 0:
        found = _GH_STATUS_RE.search(proc.stderr or "")
        if found:
            return int(found.group(1)), None, None
        first = (proc.stderr or "").strip().splitlines()
        return None, None, _scrub("gh api exited %d without an HTTP status: %s"
                                  % (proc.returncode,
                                     first[0] if first else "no error text"), token)
    try:
        body = json.loads(proc.stdout)
    except ValueError:
        return None, None, "gh api exited 0 but its output did not parse as JSON"  # sbe: allow-silent nothing is dropped; the failure travels back as this function's third element, the reason string, which every caller renders as a NO-DATA verdict carrying that exact sentence
    return 200, body, None


# ---------------------------------------------------------------------------
# The local leg: CODEOWNERS, no network
# ---------------------------------------------------------------------------

def parse_codeowners(text):
    """(entries, unparsed): entries is [(pattern, [owners])] in file order.

    `unparsed` names lines this parser did not turn into an entry (a section
    header, an owner-less pattern), so the report can say what it ignored
    instead of silently reading a shorter file than the one on disk."""
    entries, unparsed = [], []
    for number, raw in enumerate(text.splitlines(), start=1):
        # GitHub treats '#' as start-of-comment ANYWHERE on the line, not
        # only in column one. Found by a hostile refuter of this parser: a
        # line reading '/hooks/ @owner # temporarily disabled' used to be
        # read as real coverage, so a path a human had commented out still
        # counted as owned. Cut the comment first, then decide emptiness.
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            unparsed.append("line %d is a section header this parser does not "
                            "interpret: %s" % (number, line))
            continue
        parts = line.split()
        if len(parts) < 2:
            unparsed.append("line %d names a pattern with no owner: %s" % (number, line))
            continue
        entries.append((parts[0], parts[1:]))
    return entries, unparsed


def _codeowners_controls(root, control, foreign_host_detail=None):
    """Append CODEOWNERS FILE and CODEOWNERS COVERAGE. Returns nothing.

    `foreign_host_detail` is the host sentence when the origin remote is not
    GitHub, and it turns the ABSENT case from FAIL into NO-DATA. CODEOWNERS is
    a GitHub feature: the three locations searched are the three GitHub reads,
    and a repository hosted elsewhere is not failing a control by lacking a
    file its host would never consult. Reported live on a real Bitbucket
    remote on 2026-08-17, where the run told a Bitbucket team that item 5.1
    required them to create `.github/CODEOWNERS`, which their host ignores:
    an instruction that cannot be satisfied where it was given.

    A file that EXISTS is still read and still judged on every host, because
    then it is evidence somebody chose to put there, and a malformed one is a
    real finding wherever it lives.
    """
    if not root:
        for name in LOCAL_CONTROLS:
            control(name, "NO-DATA",
                    "no local repository was named (--cwd), so the CODEOWNERS leg "
                    "opened no file; this is a failure to look, not a finding")
        return
    if not os.path.isdir(root):
        for name in LOCAL_CONTROLS:
            control(name, "NO-DATA",
                    "%s is not a directory, so the CODEOWNERS leg opened no file" % root)
        return
    found = None
    for rel in CODEOWNERS_LOCATIONS:
        candidate = os.path.join(root, *rel.split("/"))
        if os.path.isfile(candidate):
            found = (rel, candidate)
            break
    if found is None:
        if foreign_host_detail:
            message = ("no CODEOWNERS file exists at any of the locations GitHub reads "
                       "(%s) under %s, and %s: CODEOWNERS is a GitHub feature, so its "
                       "absence on this host is not a finding this repository could act "
                       "on. Item 5.1 of the release blockers is about GitHub-hosted "
                       "repositories; the equivalent control on this host is its own "
                       "branch restriction and default reviewer settings, which this "
                       "command cannot read"
                       % (", ".join(CODEOWNERS_LOCATIONS), root, foreign_host_detail))
            control("CODEOWNERS FILE", "NO-DATA", message)
            control("CODEOWNERS COVERAGE", "NO-DATA",
                    "there is no CODEOWNERS file to cover anything, and none is expected "
                    "here: " + message)
            return
        message = ("no CODEOWNERS file exists at any of the locations GitHub reads "
                   "(%s) under %s; item 5.1 of the release blockers requires one"
                   % (", ".join(CODEOWNERS_LOCATIONS), root))
        control("CODEOWNERS FILE", "FAIL", message)
        control("CODEOWNERS COVERAGE", "FAIL",
                "there is no CODEOWNERS file to cover anything: " + message)
        return
    rel, path = found
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        message = "%s exists but could not be read (%s)" % (path, exc)
        control("CODEOWNERS FILE", "NO-DATA", message)
        control("CODEOWNERS COVERAGE", "NO-DATA",
                "the file could not be read, so nothing was compared: " + message)
        return
    entries, unparsed = parse_codeowners(text)
    tail = ("; ignored: " + "; ".join(unparsed)) if unparsed else ""
    if not entries:
        control("CODEOWNERS FILE", "FAIL",
                "%s exists but declares no pattern with an owner%s" % (rel, tail))
        control("CODEOWNERS COVERAGE", "FAIL",
                "%s declares no owned pattern, so no control plane path is "
                "covered%s" % (rel, tail))
        return
    control("CODEOWNERS FILE", "PASS",
            "%s declares %d owned pattern(s)%s" % (rel, len(entries), tail))
    owned = {}
    for pattern, owners in entries:
        owned.setdefault(pattern, []).extend(owners)
    missing = [want for want in CONTROL_PLANE_PATHS if not owned.get(want)]
    if missing:
        control("CODEOWNERS COVERAGE", "FAIL",
                "%s covers %d of the %d control plane paths item 5.1 names; "
                "uncovered: %s" % (rel, len(CONTROL_PLANE_PATHS) - len(missing),
                                   len(CONTROL_PLANE_PATHS), ", ".join(missing)))
        return
    people = sorted({owner for _p, owners in entries for owner in owners})
    control("CODEOWNERS COVERAGE", "PASS",
            "%s covers all %d control plane paths item 5.1 names, owned by %s; %s"
            % (rel, len(CONTROL_PLANE_PATHS), ", ".join(people),
               COVERAGE_IS_NOT_INDEPENDENCE))


# ---------------------------------------------------------------------------
# The network leg: the seven ruleset facts of item 5.3
# ---------------------------------------------------------------------------

def _boolean_rule_fact(name, parameters, key, requirement, control):
    """One boolean parameter of the pull_request rule, read defensively."""
    if key not in parameters:
        control(name, "NO-DATA",
                "a pull_request rule applies, but its parameters carried no %r key, "
                "so whether %s could not be read from this response" % (key, requirement))
    elif parameters.get(key) is True:
        control(name, "PASS", "the pull_request rule sets %s to true, so %s"
                % (key, requirement))
    else:
        control(name, "FAIL", "the pull_request rule sets %s to %r, so %s is NOT "
                              "required" % (key, parameters.get(key), requirement))


def _bypass_actors(repo, rules, token, fetch, strict, allowed, control):
    """BYPASS ACTORS, read from every ruleset that contributed a rule."""
    ids = []
    for rule in rules:
        rid = rule.get("ruleset_id")
        if rid is None:
            continue
        text = str(rid)
        if _RULESET_ID_RE.fullmatch(text) and text not in ids:
            ids.append(text)
    if not ids:
        control("BYPASS ACTORS", "NO-DATA",
                "no rule that applies to this branch named a numeric ruleset_id, so "
                "the ruleset detail that carries bypass_actors was never requested")
        return
    actors, unreadable = [], []
    for rid in ids:
        url = "/repos/%s/rulesets/%s" % (repo, rid)
        status, body, err = fetch("GET", url, token)
        if status == 200 and isinstance(body, dict):
            if "bypass_actors" not in body:
                unreadable.append("ruleset %s answered 200 with no bypass_actors key" % rid)
                continue
            for actor in body.get("bypass_actors") or []:
                if not isinstance(actor, dict):
                    unreadable.append("ruleset %s listed a bypass actor that is not an "
                                      "object" % rid)
                    continue
                actors.append("%s:%s(%s)" % (actor.get("actor_type") or "unknown-type",
                                             actor.get("actor_id"),
                                             actor.get("bypass_mode") or "unknown-mode"))
        elif status is None:
            unreadable.append("ruleset %s did not answer: %s"
                              % (rid, err or "no reason recorded"))
        else:
            unreadable.append("ruleset %s answered HTTP %s with an unusable body"
                              % (rid, status))
    if unreadable:
        control("BYPASS ACTORS", "NO-DATA",
                "the bypass actor list was not fully readable, so it is not reported "
                "as empty: %s" % "; ".join(unreadable))
        return
    if not actors:
        control("BYPASS ACTORS", "PASS",
                "no ruleset applying to this branch grants a bypass actor (%d ruleset(s) "
                "read)" % len(ids))
        return
    undeclared = [a for a in actors if a not in (allowed or ())]
    if strict:
        control("BYPASS ACTORS", "FAIL",
                "--strict requires no bypass actor at all (item 5.2.10, no ordinary "
                "administrator bypass), and %d are configured: %s"
                % (len(actors), ", ".join(actors)))
    elif undeclared:
        control("BYPASS ACTORS", "FAIL",
                "bypass actors are configured and were not declared to this run with "
                "--allow-bypass-actor: %s" % ", ".join(undeclared))
    else:
        control("BYPASS ACTORS", "PASS",
                "every configured bypass actor was explicitly declared to this run: %s"
                % ", ".join(actors))


def evaluate(repo, branch, token, fetch=None, root=None, strict=False,
             allowed_bypass_actors=None, token_source=None):
    """The report dict tools/test_sbe_protections.py pins.

    `fetch=None` means the real `gh api` transport; fixtures always inject
    their own and this function then opens nothing.
    """
    if fetch is None:
        fetch = _gh_fetch
    allowed = tuple(allowed_bypass_actors or ())
    controls = []

    def control(name, verdict, detail):
        controls.append({"name": name, "verdict": verdict, "detail": detail})

    report = {
        "tool": "sbe protections verify",
        "repository": repo,
        "branch": branch,
        "strict": bool(strict),
        "tokenSource": token_source or ("supplied directly" if token else None),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "taxonomy": ("this report uses UNVERIFIABLE as a fourth verdict beside the "
                     "registry's three; these controls live in src/brothersbe, not in "
                     "a Check registry, so the check-registry law of three is untouched"),
        "releaseNote": RELEASE_NOTE,
        "controls": controls,
        "final": None,
        "finalDetail": None,
    }

    # The host is resolved BEFORE the local leg, because the local leg's verdict
    # depends on it: a missing CODEOWNERS file is a FAIL on GitHub and a NO-DATA
    # anywhere else. Same guard as the network leg below, and for the same
    # reason: only against the real transport, since every fixture injects its
    # own fetch and no fixture root is answerable for a live origin remote.
    foreign_host_detail = None
    if fetch is _gh_fetch:
        _host, _host_detail = prverify_mod.remote_host(root or os.getcwd())
        if _host != prverify_mod.GITHUB_HOST:
            foreign_host_detail = _host_detail

    # The local leg first: it needs nothing and it always runs, so a run with no
    # GitHub access still produces real findings rather than seven shrugs.
    _codeowners_controls(root, control, foreign_host_detail)

    transport = gh_transport_problem() if fetch is _gh_fetch else None
    if not token or transport:
        why = REMEDY if not transport else (transport + "; " + REMEDY)
        for name in NETWORK_CONTROLS:
            control(name, "NO-DATA", why)
        report["final"] = _final(controls)
        report["finalDetail"] = why
        return _scrubbed(report, token)

    # Host check runs only once credentials and `gh` itself are both usable
    # (those already have their own NO-DATA above) and only against the real
    # transport: every fixture injects its own fetch (never `_gh_fetch`), and
    # no fixture root is answerable for a live origin remote, so this must
    # never fire under a canned fetch.
    if fetch is _gh_fetch:
        host, host_detail = prverify_mod.remote_host(root or os.getcwd())
        if host != prverify_mod.GITHUB_HOST:
            why = ("%s; a real verdict needs a GitHub-hosted origin remote, because "
                  "this command's only evidence source is the GitHub API" % host_detail)
            for name in NETWORK_CONTROLS:
                control(name, "NO-DATA", why)
            report["final"] = _final(controls)
            report["finalDetail"] = why
            return _scrubbed(report, token)

    rules_url = "/repos/%s/rules/branches/%s" % (repo, _encode_segment(branch))
    status, body, err = fetch("GET", rules_url, token)
    rules = None
    downstream = None
    if status == 200 and isinstance(body, list):
        rules = [r for r in body if isinstance(r, dict)]
        if rules:
            sources = sorted({str(r.get("ruleset_source") or "unnamed source")
                              for r in rules})
            control("RULESET EXISTS", "PASS",
                    "%d rule(s) apply to %s, from %s" % (len(rules), branch,
                                                         ", ".join(sources)))
        else:
            control("RULESET EXISTS", "FAIL",
                    "no ruleset applies to %s in %s: the branch is unprotected"
                    % (branch, repo))
            downstream = ("FAIL", "no ruleset applies to %s, so this protection is "
                                  "not configured at all" % branch)
    elif status in (401, 403):
        control("RULESET EXISTS", "UNVERIFIABLE",
                "the token lacks permission to read branch rules for %s in %s (HTTP %d)"
                % (branch, repo, status))
        downstream = ("UNVERIFIABLE", "the branch rules were not readable (HTTP %d), so "
                                      "this fact was never evaluated" % status)
    elif status == 404:
        control("RULESET EXISTS", "UNVERIFIABLE",
                "no branch rules endpoint resolved for %s in %s (HTTP 404): the "
                "repository or the branch does not exist, or the token cannot see it"
                % (branch, repo))
        downstream = ("UNVERIFIABLE", "the branch rules endpoint did not resolve, so "
                                      "this fact was never evaluated")
    elif status is None:
        control("RULESET EXISTS", "NO-DATA",
                "gh api gave no answer for the branch rules: %s"
                % (err or "no reason recorded"))
        downstream = ("NO-DATA", "gh api gave no answer for the branch rules, so this "
                                 "fact examined nothing")
    else:
        control("RULESET EXISTS", "UNVERIFIABLE",
                "the branch rules endpoint answered HTTP %s with an unusable body" % status)
        downstream = ("UNVERIFIABLE", "the branch rules endpoint answered HTTP %s"
                      % status)

    if rules is None or downstream is not None:
        for name in NETWORK_CONTROLS[1:]:
            control(name, downstream[0], downstream[1])
        report["final"] = _final(controls)
        report["finalDetail"] = downstream[1]
        return _scrubbed(report, token)

    by_type = {}
    for rule in rules:
        kind = rule.get("type")
        if kind:
            by_type.setdefault(kind, []).append(rule)

    # 5.3.2 required checks.
    checks_rules = by_type.get("required_status_checks") or []
    if not checks_rules:
        control("REQUIRED CHECKS", "FAIL",
                "no required_status_checks rule applies to %s, so no status check is "
                "required to merge" % branch)
    else:
        parameters = checks_rules[0].get("parameters")
        if not isinstance(parameters, dict) or "required_status_checks" not in parameters:
            control("REQUIRED CHECKS", "NO-DATA",
                    "a required_status_checks rule applies, but its response carried no "
                    "required_status_checks parameter, so the required contexts could "
                    "not be read")
        else:
            contexts = []
            for entry in parameters.get("required_status_checks") or []:
                if isinstance(entry, dict) and entry.get("context"):
                    contexts.append(entry["context"])
                elif isinstance(entry, str) and entry:
                    contexts.append(entry)
            if not contexts:
                control("REQUIRED CHECKS", "FAIL",
                        "a required_status_checks rule applies to %s but names zero "
                        "contexts, so nothing is actually required" % branch)
            else:
                control("REQUIRED CHECKS", "PASS",
                        "%d status check context(s) are required on %s: %s"
                        % (len(contexts), branch, ", ".join(sorted(contexts))))

    # 5.3.3, 5.3.4, 5.3.5: three booleans of the one pull_request rule.
    pr_rules = by_type.get("pull_request") or []
    facts = (("CODEOWNERS REVIEW", "require_code_owner_review",
              "review from a code owner is required"),
             ("STALE REVIEWS DISMISSED", "dismiss_stale_reviews_on_push",
              "an approval is dismissed when a new commit is pushed"),
             ("LAST PUSH APPROVAL", "require_last_push_approval",
              "the most recent push must itself be approved"))
    if not pr_rules:
        for name, _key, requirement in facts:
            control(name, "FAIL",
                    "no pull_request rule applies to %s, so %s is NOT required"
                    % (branch, requirement))
    else:
        parameters = pr_rules[0].get("parameters")
        if not isinstance(parameters, dict):
            for name, _key, requirement in facts:
                control(name, "NO-DATA",
                        "a pull_request rule applies to %s but carried no parameters "
                        "object, so whether %s could not be read" % (branch, requirement))
        else:
            for name, key, requirement in facts:
                _boolean_rule_fact(name, parameters, key, requirement, control)

    # 5.3.6 force push.
    if by_type.get("non_fast_forward"):
        control("FORCE PUSH DISABLED", "PASS",
                "a non_fast_forward rule applies to %s, so force pushes are blocked"
                % branch)
    else:
        control("FORCE PUSH DISABLED", "FAIL",
                "no non_fast_forward rule applies to %s, so force pushes are allowed"
                % branch)

    # 5.3.7 bypass actors.
    _bypass_actors(repo, rules, token, fetch, strict, allowed, control)

    report["final"] = _final(controls)
    if report["final"] != "PASS":
        report["finalDetail"] = RELEASE_NOTE
    return _scrubbed(report, token)


def _final(controls):
    """The report verdict. FAIL beats UNVERIFIABLE beats NO-DATA beats PASS."""
    verdicts = [c["verdict"] for c in controls]
    if "FAIL" in verdicts:
        return "FAIL"
    if "UNVERIFIABLE" in verdicts:
        return "UNVERIFIABLE"
    if "NO-DATA" in verdicts:
        return "NO-DATA"
    return "PASS"


def _scrubbed(report, token):
    """Last line of defense: the token value never reaches an output byte."""
    for c in report["controls"]:
        c["detail"] = _scrub(c["detail"], token)
    report["finalDetail"] = _scrub(report["finalDetail"], token)
    return report


def render(report):
    """The human-readable form: header, one line per control, FINAL last."""
    lines = []
    lines.append("sbe protections verify: %s branch %s"
                 % (report["repository"], report["branch"]))
    lines.append("mode: %s" % ("strict (no bypass actor is accepted at all)"
                               if report.get("strict") else
                               "default (an explicitly declared bypass actor is accepted)"))
    lines.append("generated at: %s (wall clock; nothing here is cached, every run "
                 "re-asks)" % report.get("generatedAt"))
    lines.append("token source: %s" % (report.get("tokenSource") or "none found"))
    lines.append("verdicts: %s" % report.get("taxonomy"))
    lines.append("")
    for c in report["controls"]:
        lines.append("  %-24s %-13s %s" % (c["name"], c["verdict"], c["detail"]))
    lines.append("")
    tail = "FINAL %s" % report["final"]
    if report.get("finalDetail"):
        tail += "  %s" % report["finalDetail"]
    lines.append(tail)
    lines.append(report.get("releaseNote") or RELEASE_NOTE)
    return "\n".join(lines)


def _parser():
    parser = argparse.ArgumentParser(
        prog="sbe protections verify",
        description="Verify that the repository itself protects the control plane: the "
                    "CODEOWNERS file locally, and the seven branch ruleset facts of "
                    "release blocker item 5.3 through gh api. Read-only.")
    parser.add_argument("--repository", "--repo", dest="repository", required=True,
                        help="the repository, as owner/name")
    parser.add_argument("--branch", default="main",
                        help="the protected branch to verify (default: main)")
    parser.add_argument("--strict", action="store_true",
                        help="refuse any bypass actor at all, per item 5.2.10; without "
                             "it, a bypass actor declared with --allow-bypass-actor is "
                             "accepted. Exit codes are the same in both modes: only a "
                             "PASS exits 0")
    parser.add_argument("--allow-bypass-actor", dest="allow_bypass_actor",
                        action="append", default=[],
                        help="a bypass actor this run accepts, spelled "
                             "actor_type:actor_id(bypass_mode) exactly as this report "
                             "prints it; repeat for more than one")
    parser.add_argument("--cwd", default=".",
                        help="the local clone whose CODEOWNERS file is read (default: "
                             "the current directory); this leg never touches the network")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def main(rest, exit_ok=0, exit_failed=1, exit_usage=2):
    """The `sbe protections` surface. Exit codes come from the caller so this
    module never disagrees with the CLI's documented table. Exit 0 only when
    FINAL reads PASS; every other final verdict, NO-DATA included, exits
    nonzero, because the release checklist treats NO-DATA as blocking."""
    rest = list(rest)
    if rest and rest[0] in ("-h", "--help"):
        _parser().print_help(sys.stdout)
        return exit_ok
    if not rest or rest[0] != "verify":
        sys.stderr.write("sbe protections: the first positional is 'verify' "
                         "(sbe protections verify --repository owner/name "
                         "--branch main); got %s\n"
                         % (repr(rest[0]) if rest else "nothing"))
        return exit_usage
    parser = _parser()
    try:
        args = parser.parse_args(rest[1:])
    except SystemExit as exc:
        # argparse already answered: -h printed the usage that was asked for and
        # raised 0, and help is not an error. Anything nonzero is a refusal.
        return exit_ok if exc.code in (0, None) else exit_usage
    if not prverify_mod.valid_repo_shape(args.repository):
        sys.stderr.write(
            "sbe protections verify: --repository must look like owner/name, where "
            "owner and name each match [A-Za-z0-9_.-]+ and nothing else (no extra "
            "slashes, no path traversal); got %r\n" % args.repository)
        return exit_usage
    if not valid_branch_shape(args.branch):
        sys.stderr.write(
            "sbe protections verify: --branch must be a git ref with no whitespace, no "
            "leading '-', and it may not be '.' or '..'; got %r\n" % args.branch)
        return exit_usage

    token, source, _detail = prverify_mod.discover_token()
    report = evaluate(args.repository, args.branch, token,
                      root=os.path.abspath(args.cwd) if args.cwd else None,
                      strict=args.strict,
                      allowed_bypass_actors=args.allow_bypass_actor,
                      token_source=source)
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render(report) + "\n")
    return exit_ok if report["final"] == "PASS" else exit_failed
