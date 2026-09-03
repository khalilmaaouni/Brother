Status: CURRENT.

# Using BrotherMode with Bitbucket

BrotherMode's engine never talks to GitHub. The store, the fences, the
handover ceremony, the gates and every verification command speak plain git,
so a project whose remote lives on Bitbucket Cloud gets the same enforcement
a GitHub project gets. This page states exactly what that means, what was
executed to prove it, and what remains unproven. Every claim below names its
source; a claim nobody has executed is labeled UNVERIFIED rather than left
looking checked.

## What already works, with the proving command

The release-truth checks and remote verification use `git ls-remote`, which
is host agnostic. Executed against a public Bitbucket repository on
2026-08-15 from this machine:

    GIT_TERMINAL_PROMPT=0 git ls-remote https://bitbucket.org/tutorials/tutorials.git.bitbucket.org.git HEAD
    59d3c2c4c3e2682f0800f1fb85d45a776f4f19dd  HEAD

Nothing in `tools/` or `hooks/` shells out to `gh` or calls a GitHub API.
The only GitHub references in executable code are documentation strings and
the issue link `scripts/doctor.py` prints. The gate, the fences, the store
and the baton ceremony run identically whatever host the remote lives on.

## Remote formats

Bitbucket Cloud accepts both standard git transports:

- https: `https://bitbucket.org/<workspace>/<repository>.git`
- ssh: `git@bitbucket.org:<workspace>/<repository>.git`

The https form was exercised anonymously against a public repository this
session (command above). The ssh form follows Bitbucket's own documentation
and standard git behavior; it was not exercised here because it needs a key
registered to an account, and is labeled UNVERIFIED for that reason.

## Installing BrotherMode from a Bitbucket mirror

Claude Code's plugin system accepts full git URLs, not only GitHub
shorthand. Per the marketplace reference at
https://code.claude.com/docs/en/plugin-marketplaces, `claude plugin
marketplace add` accepts local paths, GitHub `owner/repo` shorthand, and
full git URLs including Bitbucket https and ssh URLs. So against a mirror:

```bash
claude plugin marketplace add https://bitbucket.org/<workspace>/BrotherModeUp.git
```

Two honest caveats, both from the same documentation page:

- Ref pinning. The docs state git-based marketplace sources support a
  branch or tag ref, but the exact `@ref` command syntax is only shown in
  examples for GitHub shorthand. Whether `<url>@v3.3.1` parses is
  UNVERIFIED until somebody runs it.
- Private mirrors. Background marketplace auto-updates disable credential
  helpers for https, so a private Bitbucket mirror may fail to auto-update.
  Keys held in ssh-agent work. The docs page carries the workarounds.

The tagged git clone install, the most proven path on GitHub, translates
directly:

```bash
git clone --branch v3.3.1 --depth 1 https://bitbucket.org/<workspace>/BrotherModeUp.git ~/.claude/skills/brothermode
```

UNVERIFIED end to end: no public Bitbucket mirror of this repository exists
yet, so neither command above has ever been executed against one. Creating
the mirror is an account-holding step the founder runs; the moment one
exists, executing these two commands and recording their output closes this
label.

## Reporting a gate verdict when the origin is not the canonical GitHub repo

`scripts/local-gates.sh` runs the real battery locally and then REPORTS the
verdict as a commit status. Until 2026-08-17 the reporting arm was `gh api`
against a hardcoded GitHub repository, so a checkout whose origin lives on
Bitbucket Cloud got a reporting refusal on top of a battery that had just
passed. Two ways to avoid that, and the first needs nothing from you:

1. Host routing, automatic. The runner reads the ORIGIN REMOTE, classifies
   it with `python3 tools/bm_bbstatus.py classify <url>` (github, bitbucket
   or unknown), and posts through the matching arm. On Bitbucket it POSTs a
   build status through the REST API; on an unrecognized host it posts
   nothing and says so. The GitHub arm keeps its nonzero return when a POST
   fails, and since 2026-08-26 it reads the SAME remote for its target:
   `python3 tools/bm_bbstatus.py slug <url>` gives it `owner/repo`, and an
   origin URL that does not split into two path segments is a refusal that
   names the URL, never a guessed repository.
2. `scripts/local-gates.sh --no-post`. Runs the gate, prints the verdict,
   writes and signs the receipt, sends nothing anywhere. Use it when you
   want the verdict locally and no report at all. It is NO LONGER the
   workaround a GitHub FORK needs: the target is parsed from the fork's own
   origin, so a fork reports to the fork.

A reporting failure on the Bitbucket arm NEVER changes the gate's exit
status, and that difference from the GitHub arm is deliberate. On GitHub the
nonzero return is long-standing behavior in this repository and sessions
read it. On Bitbucket there is no such habit to preserve, and turning a
green battery red because a credential was absent would teach the adopter
team to distrust the gate.

Credentials come from the environment, never from a file in the repository,
and the names are identical to the sibling repository's so one team can hold
one set:

- `BITBUCKET_TOKEN`, sent as a Bearer token, or
- `BITBUCKET_USERNAME` plus `BITBUCKET_APP_PASSWORD`, sent as HTTP Basic.
  The second variable's NAME is historical: Atlassian removed classic app
  passwords on 2026-07-28 (see the authentication note below), so what it
  carries today is a scoped API token. The name is kept because both
  siblings use it and a rename would break the parity for no gain.

What is proven, and what is not, stated separately:

| Claim | Status | Evidence |
| --- | --- | --- |
| Routing picks the right arm from an origin URL, both transports, and refuses to guess on an unknown host | VERIFIED | `python3 tools/test_bm_bbstatus.py`, quoted in the run record below |
| A missing credential, an unreadable URL, or a refusing API is REPORTED and never changes the gate's exit | VERIFIED | same suite: the no-credential, bad-URL, HTTP 401 and dead-network cases each assert the message and that no request was even attempted |
| The GitHub arm still behaves exactly as before | VERIFIED | same suite reads `scripts/local-gates.sh` itself and pins the historical command and its refusal |
| A real POST reaching api.bitbucket.org and appearing on a commit | UNVERIFIED | no session in this estate holds a Bitbucket API credential, and no mirror exists to post against. Closing this needs both, and it is one command once they exist. |

## Windows, and a defect that had been silent since this arm shipped

UNVERIFIED, said first because it governs everything below: **no Windows
machine has run any of this.** `docs/WINDOWS-CHECK.md` is the protocol for
closing that, and its step 5 exercises exactly the commands this section
describes, without needing `bash` or a credential. Until somebody returns that
form, every claim here is about code that was read and reproduced, not about
a machine that ran it.

**What the Bitbucket leg needs to work on Windows.** `tools/bm_bbstatus.py` is
the whole reporting arm and it is standard library Python: no subprocess, no
shell, no filesystem paths, no POSIX assumption. That part is portable by
construction. What is NOT portable is the caller: `scripts/local-gates.sh` is
bash, so on a Windows box without git-bash the runner cannot start, and the
tool has to be invoked directly. There is no Windows-native gate runner and
this page does not pretend otherwise.

**THE DEFECT, found 2026-08-26 and fixed the same day.** Python opens stdout
as a text stream with `newline=None`, which on Windows rewrites every `\n` as
`\r\n`. The runner captures this tool's stdout with `$(...)`, and command
substitution strips trailing NEWLINES but not a carriage return. So on
Windows:

```
HOST_KIND="$(python3 tools/bm_bbstatus.py classify "$ORIGIN_URL")"   # github\r
case "$HOST_KIND" in
  github)    ...   # never matched
  bitbucket) ...   # never matched
  *)         echo "host not recognized from the origin remote" ;;
esac
```

Both arms missed. **Every Windows run fell through to the unknown arm and
posted nothing**, while printing a reason that blames the operator's origin
remote. The routing this page describes, the entire point of the two-host law
here, had therefore never worked on Windows from the day it shipped on
2026-08-17 until it was fixed. The `slug` command added the same day as the
fix carried the same fault into the post target, where it would have built
`repos/owner/repo\r/statuses/<sha>`.

Verified rather than argued, on both halves. The Python half was reproduced by
installing Windows's own newline policy on a stand-in stream and reading the
bytes back: `tools/test_bm_bbstatus.py`'s `TestWindowsNewlineTranslation`,
four tests, three of which fail against the previous revision of `_out`. The
shell half was confirmed in real bash: a 27 character slug came back 28
characters long, and a `case` on `github\r` reaches the `*)` arm.

`_out` now refuses the translation with `reconfigure(newline="\n")` rather
than writing bytes, so the console's own ENCODING still decides how characters
are formed and only the line ending rewriting is declined. `_err` is left
alone deliberately: it carries prose for a person, where the platform's line
ending is the right one. Only stdout carries a value another program parses.

**The sibling does not share this fault**, checked in its tree rather than
assumed: BrotherSBE routes with a shell-native `case "$ORIGIN_URL" in
*bitbucket.org*` and parses its post target with `sed`, so it captures no
Python stdout on this path at all. Noted while looking, not fixed and not
this repository's to fix: that runner does capture Python stdout elsewhere,
at the battery command extractor, which would take the same `\r` on Windows.
Its runner needs `bash` regardless, so nothing there is reachable on a
Windows box today either way.

## Pushing, and the pull request flow

Pushing is git, so nothing changes: the push policy in CLAUDE.md (direct to
main, secret scan, dash scan, green gate, command verification) applies
verbatim to a Bitbucket remote.

Pull requests differ from GitHub in tooling, not in shape. Bitbucket Cloud
has no equivalent of `gh pr create` from Atlassian: Atlassian's official
CLI (acli, https://developer.atlassian.com/cloud/acli/) is Jira-first as of
2026-08-15 and does not cover Bitbucket pull requests. The working paths:

1. The REST API. One POST creates a pull request, per
   https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/
   (endpoint shape cross-checked against Atlassian community examples):

       curl -X POST \
         -u "<atlassian-account-email>:$BITBUCKET_API_TOKEN" \
         -H "Content-Type: application/json" \
         https://api.bitbucket.org/2.0/repositories/<workspace>/<repo>/pullrequests \
         -d '{"title": "<title>", "source": {"branch": {"name": "<branch>"}}}'

   `title` and `source.branch.name` are the only required fields; the
   destination defaults to the repository's main branch. The token comes
   from the environment, never from a file in the repository, per the
   credential rules in CLAUDE.md.

2. Authentication is an Atlassian API token created at id.atlassian.com.
   App passwords are gone: Atlassian ran a brownout from 2026-06-09 to
   2026-07-27 and removed app password support on 2026-07-28 (verified
   against two independent Atlassian sources during the research pass for
   this page). Documentation elsewhere that still says app password is
   stale.

3. MCP. Atlassian's official Rovo MCP server covers Bitbucket, so a Claude
   Code session can be granted pull request tools directly. Verified
   against the server's own repository and Atlassian's announcement during
   the research pass for this page. Not exercised from this project:
   UNVERIFIED here.

## Continuous integration

`bitbucket-pipelines.yml` at the repository root mirrors the GitHub
workflow's intent: the documented full gate, `BROTHERMODE_SESSION_CAP=99
python3 tools/test_all.py`, on Python 3.9 and the newest Python 3, with a
full-depth clone because the release-truth suite needs tags to resolve.

The gate invocation in that file is not allowed to drift from the one
PROJECT.md documents: `tools/test_bm_docs.py` reads both and compares the
command and the `BROTHERMODE_SESSION_CAP` value, so a change to either side
alone fails the documentation suite by name. The GitHub side has had this
since the CI inventory check landed in `tools/test_all.py`; the Bitbucket
side had nothing until 2026-08-17.

Stated limitation, not hidden: Bitbucket Cloud's hosted runners are Linux
containers only. The macOS and Windows evidence this project publishes
still comes from `.github/workflows/tests.yml`, and a Bitbucket-only
deployment of this repository would be running with Linux evidence alone
unless somebody attaches a self-hosted macOS or Windows runner. Note what
that means for the workflow file today: it fires on `workflow_dispatch`
only, on ubuntu only, by the founder's cost law of 2026-08-16, so neither
host is producing macOS or Windows evidence automatically right now.

UNVERIFIED: the pipelines file parses (checked locally with a YAML parser
on 2026-08-15) but has never executed on Bitbucket Cloud, because no mirror
with Pipelines enabled exists yet. First green run closes this label.

## The 2026-08-18 audit, item by item

An external review produced a Bitbucket remediation plan covering both
siblings. Its claims were checked against THIS repository one at a time
rather than accepted, because a plan written about two products names
things that are true of one, of the other, or of neither. The result, with
the check that decided each:

| Claim | Verdict here | What was done |
| --- | --- | --- |
| The pipeline lives only at `ci/bitbucket-pipelines.yml`, where Bitbucket never looks | FALSE for this repository | There is no `ci/` directory at all and `bitbucket-pipelines.yml` is at the repository root, which is where Bitbucket Cloud discovers it. Nothing to fix. |
| The pipeline never runs on a pull request | TRUE, and fixed | The file defined `default` alone. Bitbucket runs a pull request pipeline only when a `pull-requests:` section matches, so merge-time enforcement did not run at the moment it exists for. A `pull-requests:` section now runs the same documented gate. |
| A run cannot say which revision it tested, so a verdict can describe an unknown tree | TRUE, and fixed | `scripts/bm_ci_context.py` captures the source, destination and tested identities, states whether the tested HEAD matched the source commit rather than assuming it, and REFUSES a pull request run missing any of them. It runs before the gate, so a build with no usable context stops rather than publishing. |
| Clone depth must be full for merge-base work | ALREADY TRUE | `clone: depth: full` was already set. The capture additionally proves the destination commit is present in the clone, because a full-depth setting is a request and its effect on pull request builds is documented by Atlassian as differing from branch builds. |
| Host-specific code inside the shared engine creates false compatibility | TRUE as a risk, now enforced | CLAUDE.md's two-host law existed only as prose. `tools/test_bm_hooks.py` now refuses a host API import, a `gh` invocation, a host API endpoint or a host CI variable read inside `tools/` or `hooks/`, with one named exemption carrying its reason. It matches CALLS, never mentions, so a documentation URL is not an offence. |
| Provider contract, Code Insights, reviewer, approval and task reads, evidence and approval freshness | OUT OF SCOPE HERE | Every one of these is an assurance-kernel concern and this repository holds no assurance kernel: it has no requirements, evidence, risk or readiness model to bind a revision to. They belong to the sibling, BrotherSBE, and building a half version here would create exactly the false compatibility the plan warns about. |
| Real end-to-end certification in a Bitbucket workspace | BLOCKED, not done | Needs a workspace and a least-privilege credential, both explicitly human steps. Neither exists. |

Release label, following the plan's own rule: this repository is
**Bitbucket Cloud compatible, preview**. It is not certified, and nothing
here should be described as full Bitbucket support until a real pull
request has run the flow end to end.

Fork pull requests: Bitbucket restricts pipelines for fork-based pull
requests, and nothing here changes that. For a single shared workspace with
branch-based development, which is the adopter team's shape, it does not
bite. It is named rather than discovered later, and the safe rule stands:
never run untrusted fork code with a privileged credential.

## What full support still needs, in order

1. Founder creates the Bitbucket workspace and mirror, and enables
   Pipelines on it. Account-holding step, nobody else can run it.
2. One session pushes the current release to the mirror, runs the two
   install commands above against it, and quotes their output here.
3. One green Pipelines run on the mirror, its URL quoted here.
4. Optional, when a Bitbucket-hosted team actually asks for it: a
   `--bitbucket` leg for `scripts/release-smoke-install.sh` mirroring the
   `--github` flag. Not built now, because it would test a mirror that does
   not yet exist. Re-checked 2026-08-17 and still correct: that script's
   `--github` arm passes `khalilmaaouni/BrotherModeUp` to `claude plugin
   marketplace add`, so a `--bitbucket` arm would need a real mirror URL to
   pass instead, and a flag that can only ever fail is worse than an absent
   one. It is sequenced after step 1 above, not forgotten.

## What this repository's two-host law already covers

Both siblings now hold the same three properties, which is what the founder
asked for when the target was constrained to GitHub and Bitbucket:

| Property | BrotherMode | BrotherSBE |
| --- | --- | --- |
| Hooks are python3 only, no POSIX shell in any wired command | yes, since 2026-08-17 (`tools/test_bm_hooks.py` refuses any other interpreter) | yes, ported first; this repository followed its shape |
| Gate reporting routes on the origin host | yes, `tools/bm_bbstatus.py` plus the routing case in `scripts/local-gates.sh` | yes |
| Bitbucket credential names | `BITBUCKET_TOKEN`, or `BITBUCKET_USERNAME` plus `BITBUCKET_APP_PASSWORD` | identical, deliberately |

The sibling's own `PARITY.md` carries the other half of this table. It was
NOT updated in the same change, and that is a real gap rather than an
oversight: the sibling repository is not present in the environment this
change was made in, so editing it was impossible here. Whoever next holds a
checkout with both repositories should copy the three rows above into
`PARITY.md` and say so in that change.

Windows, stated plainly: the hooks are now runnable in principle on a
machine with python3 and no POSIX shell. NOTHING here has executed on a real
Windows machine. That is UNVERIFIED until one does, and
`docs/WINDOWS-CHECK.md` is the written protocol for closing it.

## The 2026-08-18 duo spec, shape by shape

BrotherSBE and this repository were asked to hold Bitbucket support to the
same eight shapes and three laws (docs/plans/2026-08-18-bitbucket-duo-spec.md
in BrotherSBE; that file is the spec of record and is not copied here).
Checked against this repository's own tree, one shape at a time:

| # | Shape | Verdict here | Evidence |
| --- | --- | --- | --- |
| 1 | Host detection in one place | ALREADY-PRESENT | `tools/bm_bbstatus.py`'s `_host_of`/`classify` is the only place a git remote URL is parsed for its host. `scripts/local-gates.sh` reads the raw url itself (one line) and hands it to `classify` rather than parsing it a second time; a repository-wide search for a second implementation (`remote get-url`, `remote.origin.url`) found none. |
| 2 | Read client for pull requests | N/A | This repository holds no pull-request-reading capability on either host. A repository-wide search for `api.github.com` and `api.bitbucket.org` matches only `tools/bm_bbstatus.py`, its test, and the two-host lint that polices for exactly this; nothing here reads PR reviews, approvals or participants. |
| 3 | Write client for build status | ALREADY-PRESENT, one gap closed | `tools/bm_bbstatus.py` already carried the state map and both credential shapes, and laws 1 and 3 below were already proven. Law 2 (the credential never reaching a returned message) had no scrub and no test: `_auth_header` now returns `(header, secret)`, a `_scrub` helper was added, and `post_status` scrubs every message it returns. Two new tests feed a URLError whose text contains the token (Bearer shape) and the app password (Basic shape) and assert `<redacted>` stands in its place. |
| 4 | Zero-network allow-list | ALREADY-PRESENT | `tools/test_bm_hooks.py`'s `HOST_API_EXEMPT` names `bm_bbstatus.py` by exact filename with its reason, checked for staleness by `test_every_exempted_file_exists_and_carries_a_reason`; `tools/test_bm.py` separately allow-lists it per file and per module for the `urllib` import (`"bm_bbstatus.py": {"urllib"}`); this file's own line 158 carries the matching sentence. `docs/KNOWN-LIMITS.md` does not name it; that is a documentation completeness gap, not a control gap, since the two enforced allow-lists above are what actually refuses a ninth tool. |
| 5 | Environment allowlist carries both hosts | N/A | This repository has no registered-check-with-subprocess-environment-allowlist architecture for BrotherSBE's `ENV_ALLOWLIST` to extend into. The two mechanisms that touch subprocess environments here serve different purposes: `scripts/bm_ci_context.py`'s `PROVIDERS` dict already reads both hosts' CI identity variables side by side (for revision-identity capture, not trust filtering), and `tools/bm_controller.py`'s `_sanitised_env` is a GIT_-prefix denylist for redirection safety, unrelated to either host's CI variables. Building an allowlist neither mechanism needs would be a capability nobody asked for. |
| 6 | Installer routes on the origin remote | N/A | `scripts/install.py` wires this plugin into the operator's own `~/.claude/settings.json`; it does not write CI files into a target repository the way BrotherSBE's `sbe init` does, so there is no install-time host detection to extend. The nearest relative is documented already, above: two hand-typed paths (GitHub shorthand or a Bitbucket URL) for installing THIS plugin from either host, chosen by the operator, never detected by code. |
| 7 | CI census looks where each host keeps CI | N/A | This repository has no target-repository adoption or readiness census, no `sbe adopt` equivalent. It does not report on any other repository's CI presence; it only carries its own `bitbucket-pipelines.yml` at its own root, which is evidence about this repository, not a tool that inspects others. |
| 8 | Adoption docs carry both hosts | N/A for the specific content named | The reference row asks for branch restrictions, merge checks, minimum approvals, default reviewers and a CODEOWNERS gap, paired against GitHub's equivalents. This repository uses neither host's branch protection or required-reviewer mechanism: the push policy is direct to main on both hosts, gates are enforced locally by `scripts/local-gates.sh`, and no CODEOWNERS file exists anywhere in the tree (checked directly). There is nothing to adopt on either side for that specific content. This file is this repository's cross-host adoption document for what it actually has: remote formats, install paths, the pull request creation flow, and CI setup, all stated for both hosts above. |

The three laws, checked against `tools/bm_bbstatus.py` specifically, since
that is the one module here that carries a Bitbucket credential:

| Law | Verdict | Evidence |
| --- | --- | --- |
| No credential means zero network attempts | PROVEN | `tools/test_bm_bbstatus.py`'s `test_no_credential_names_both_shapes_and_attempts_nothing` asserts `opener.requests == []` against a recording stand-in transport, never the code's own claim. |
| The credential never reaches output | PROVEN (2026-08-18) | `test_a_bearer_token_leaked_into_an_error_comes_back_redacted` and `test_an_app_password_leaked_into_an_error_comes_back_redacted` feed an error whose text CONTAINS the secret and assert it comes back as `<redacted>`. |
| A status client reports a verdict, never reaches one | PROVEN | `post_status` never maps an unrecognized state to anything; it refuses the post outright by name (`test_a_bad_state_is_refused_by_name`), which is at least as strict as BrotherSBE's mapping to a non-optimistic state, and the two never disagree on the one property the law actually asks for: an unrecognized word cannot become SUCCESSFUL either way. Stated as a genuine difference rather than left for a reader to infer, per the duo spec's own rule. The CLI exit code (`main`, 0 when posted, 3 otherwise) reflects whether the report landed, never whether the reported build passed: posting a `failure` state still exits 0 when the POST itself succeeds. |

PARITY BOOKKEEPING, CLOSED 2026-08-26 after being deferred twice. Both the
2026-08-18 Windows and Bitbucket session and the duo-spec session above it
recorded the same gap and the same reason: each was scoped to this
repository's own tree, so neither could write the mirror rows into
BrotherSBE's `PARITY.md`. A gap named twice and closed neither time is the
shape this repository lints other code for, so a session that could reach
both trees closed it: BrotherSBE pull request 64, branch
claude/parity-rows-bitbucket-2026-08-26.

Two rows were added there, host-routed build status posting and the shared
credential names, each checked against BOTH trees rather than copied from
this file's account of itself. That check found something this table had
missed, and it is recorded here because it was a real divergence rather
than a formatting difference: BrotherSBE PARSES its origin remote into
owner/repo and refuses to post when it cannot, while `scripts/local-gates.sh`
here posted to a hardcoded repository name. A GitHub FORK of this repository
therefore filed its verdict against the canonical repository or failed
trying.

CLOSED 2026-08-26, in this repository, and the honest part of this entry is
that it was named twice before anyone closed it. The GitHub arm now derives
`owner/repo` from the origin remote through `bm_bbstatus.py slug` and
refuses when the URL does not split, which is the sibling's behaviour and
the sibling's exit 2. Three tests in `tools/test_bm_bbstatus.py` hold it:
the runner carries no repository literal, the arm checks its target before
it reaches `gh api`, and a fork URL resolves to the fork.

ONE DIFFERENCE REMAINS, deliberately, and it is mechanism rather than
behaviour. The sibling parses in shell with `sed`; this repository asks the
Python tool that already classified the same URL, because that parser is
under test here and a second one written in `sed` would be a second reading
of the same string. They disagree only on a path with THREE or more
segments, which the sibling accepts whole and this repository truncates to
the first two. Neither host serves a repository at such a path, so neither
behaviour is reachable in practice and neither was changed to match.

## Sources

- https://code.claude.com/docs/en/plugin-marketplaces (marketplace source
  forms, ref support, private repo caveats), read 2026-08-15.
- https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/
  (pull request endpoint), read 2026-08-15; endpoint shape cross-checked
  against Atlassian community documentation the same day.
- Atlassian app password removal dates: verified against two independent
  Atlassian sources, 2026-08-15.
- `git ls-remote` proof executed on this machine, 2026-08-15, quoted above.
