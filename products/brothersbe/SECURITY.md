# Security

## Reporting a vulnerability

Open a GitHub issue describing the problem and how to reproduce it. If the issue
would expose someone's data by being public, say so in one line without the
details and ask for a private channel first.

## What this software does with your data

Everything that runs automatically inside a session, meaning anything a
hook, a gate, or an `sbe` subcommand reaches on its own without you naming
a network-reaching command by name, makes no network calls. That property
is scoped: it covers `tools/`, `src/brothersbe/`, `hooks/`, `scripts/`,
`bin/sbe` and `install.sh`, the exact surface the audit grep below walks
and `tools/test_sbe.py` recomputes on every run, and it holds everywhere in
that surface except the paths named one by one in "Network exceptions,
exact path only" further down. Every one of them fires only because a
person ran its own triggering command by name, for the one job that is its
entire reason to exist; none is reached by a gate, a hook, or any command
outside that list. The one link between two of them,
`scripts/local-gates.sh` invoking `src/brothersbe/bbstatus.py` on a
Bitbucket origin, is one named exception calling another, not an outside
gate or hook reaching in. Read the list below for the exact paths, what
each one sends, and whether it reads or writes; the two sentences you can
rely on are: nothing outside that list reaches a network address, and
nothing inside it does so until a person runs it.

`sbe pr verify` calls a pull-request API only when you ask for it and only
when a credential is present. Which API depends on your own origin remote,
never on a flag you have to remember: a GitHub remote uses the GitHub API
with `GITHUB_TOKEN`, `GH_TOKEN` or a working `gh auth token`; a Bitbucket
remote uses the Bitbucket Cloud API with `BITBUCKET_TOKEN`, or
`BITBUCKET_USERNAME` with `BITBUCKET_APP_PASSWORD`. With no credential it
makes ZERO network attempts and reports NO-DATA naming the remedy. Documented
in `docs/KNOWN-LIMITS.md`. Its Bitbucket sibling client,
`src/brothersbe/bbprverify.py`, is permitted on exactly the same terms.

`sbe protections verify` calls `gh api` the same way: only when you ask for
it, only with `gh` on PATH and a credential discoverable the same way `sbe
pr verify`'s is. It is read-only by construction, not only by practice: the
one function that leaves the machine, in `src/brothersbe/protections.py`,
refuses any HTTP method other than GET before it builds the request. With
no `gh` on PATH or no credential, it makes ZERO requests and reports
NO-DATA once per fact it could not check, never a pass.

`install.sh` runs once, before any session starts, and is not a tool a
session invokes: it calls `git ls-remote --tags` to check whether a release
tag is published, and on the clone fallback calls `git -C ... pull
--ff-only` or `git clone`, then hands off to `claude plugin marketplace
add` and `claude plugin install`.

`tools/sbe_bb_estate_check.sh` is the one tool in `tools/` itself that talks
to a network: it calls the Bitbucket REST API to finish an estate's
Pipelines check, on purpose, because that call is its entire reason to
exist. It runs only when a person invokes it directly; no gate, hook or
default path calls it. With `BITBUCKET_USER` unset or its keychain
credential absent, it makes ZERO network attempts and exits naming what to
set, before any `curl` call is reached.

`scripts/branch-inventory.sh` and `scripts/local-gates.sh` are operator
scripts: a person runs each from the command line, or from a
`workflow_dispatch` CI job someone triggered by hand (nothing in this
repository fires on push), never from a gate, a hook, or each other.
`branch-inventory.sh` calls `git ls-remote --heads origin` to list what
exists on the remote right now, a read that sends nothing but the request
itself. `local-gates.sh` calls `git fetch --quiet origin main` to refresh
the trust anchor its own battery reads from (a read; a failed fetch is not
fatal, the run continues on whatever `origin/main` already held locally and
says so), then, once the battery has reached a verdict, reports it outward:
on a GitHub origin, `gh api -X POST repos/$REPO/statuses/$SHA` WRITES the
pass or fail state and a one-line description to your own repository's
commit-status API; on a Bitbucket origin, the write is
`src/brothersbe/bbstatus.py`, the same report translated to Bitbucket's
build-status resource, sending one POST to one endpoint. Neither can turn a
failing battery into a passing one: the verdict posted is the verdict the
battery already reached before either write is attempted, a failed POST is
reported as a failure to report and never folded into the battery's own
exit code, and neither runs at all without its own credential.

Outside those, BrotherSBE has no analytics, no account, and no remote
server: nothing here reaches a network address other than the pull-request
and branch-protection host you are actually on (GitHub or Bitbucket,
through `sbe pr verify` and `sbe protections verify`), the Bitbucket estate
you point the estate check at, your own origin remote's branches and commit
status through the two operator scripts above, and the git remotes
`install.sh` talks to before a session exists.

**2026-08-05 amendment (gate LP-0301, decision recorded in
[docs/adr/2026-08-05-gui-server-amendment.md](docs/adr/2026-08-05-gui-server-amendment.md)):**
the promise above is "no remote server", not "no server at all". A loopback-only
GUI workspace is authorized: a future module, `src/brothersbe/gui/server.py`,
may bind `127.0.0.1` and nothing else, never `0.0.0.0`, never a remote host, and
never an outbound call of its own. That module does not exist in this tree yet;
this amendment only reserves its name in the audit surface below, and in the
zero-network scan's allowlist, so a later lane can build it without reopening
this security boundary. No GUI code ships in this change. Nothing else gains
network capability by this amendment: every other file, including every other
file a future `src/brothersbe/gui/` directory holds, stays bound to the same
zero-network rule as before, and the scan below enforces that directly rather
than trusting the directory name.

Everything it writes goes to your vault folder, which you choose with
`BROTHERSBE_VAULT` (default `~/BrotherSBEVault`). You can verify both claims
yourself; the tools are standard-library Python and shell: 72,395 lines measured
2026-08-21 by `wc -l tools/*.py tools/*.sh`, a figure stated here rather
than left for you to discover, and a test in `tools/test_sbe.py` fails if it
drifts more than 15 percent, so the auditability claim degrades loudly
instead of quietly. This is a wide net for a person to read with judgment,
not a proof of completeness:

```bash
grep -rnE "urllib|requests|socket|http|curl|wget|subprocess" tools/ src/ hooks/ scripts/ bin/
```

What to expect from that grep, so it is usable rather than reassuring: it
does not know a git remote operation or a `gh api` call by name, so it
cannot see those on its own, and every other hit is one of three benign
shapes: `subprocess` running local `git`, the words "socket" or "http"
inside a refusal message or comment, or a fake credential inside a
redaction TEST FIXTURE (`tools/test_sbe.py` carries a literal
`curl ... Bearer ...` string precisely to prove such strings get masked).
The mechanical check that actually stands behind this document is
`tools/test_sbe.py`'s own `TestAuditableSurface`, and it is stricter than
this grep along two axes: it parses Python imports by AST rather than
matching text, and, in every scanned shell file, it looks for `git
ls-remote`, `git fetch`, `git push`, `git clone`, `git pull` and `gh api`
as real invocations, not merely as words that happen to appear on a line (a
dry-run message that PRINTS the sentence "git clone" to describe what would
happen is not a match; the shell scan blanks quoted text before it looks,
so prose describing a command is not mistaken for running it). Its Python
half adds one more shape past a bare `import`: a `curl`, `wget` or `nc`
subprocess invocation, or a `gh api` one, caught whether that argument list
is written inline or built into a local variable first, the shape
`src/brothersbe/protections.py` itself uses. A hit outside the paths named
in "Network exceptions, exact path only" below, in either half of the
scan, is a violation of this document; report it.

The property itself is drift-tested: `tools/test_sbe.py` parses every tool
under `tools/`, `src/brothersbe/` (including every file a future
`src/brothersbe/gui/` directory holds, walked recursively, not skipped as a
directory), `hooks/`, `scripts/`, `bin/sbe`, and `install.sh`. It fails if
any of them, other than the Python paths named below, each skipped by its
exact path, imports `urllib`, `requests`, `socket` or `http`, or builds a
`curl`, `wget`, `nc` or `gh api` subprocess call; and it fails if any shell
tool, other than the shell paths named below, each skipped by its exact
path, invokes `curl`, `wget`, `nc`, a `git` remote operation, or `gh api`.
Both allowlists are checked, not merely trusted: every shell path must
still exist on disk (a Python path reserved for code that does not exist
yet, `src/brothersbe/gui/server.py`, is allowed to be absent), and every
path in either allowlist must appear as its own line in "Network
exceptions, exact path only" below, in that exact structured shape. A path
that merely appears somewhere else in this document -- an unrelated
mention, a checksum manifest entry, a changelog line -- does not count:
closing that loophole (an allowlist entry "documented" only by an
incidental substring match elsewhere in this file) is the whole reason that
section exists as a separate, structured list rather than prose.

### Network exceptions, exact path only

This is the one place every network-capable path in this repository is
named together. Each line below is what `tools/test_sbe.py` reads to
decide whether an allowlisted path counts as documented; a path that is
not on its own line here, in exactly this shape, is not a documented
exception no matter what else this file says about it.

- `src/brothersbe/prverify.py` -- read. `sbe pr verify`'s GitHub API client.
- `src/brothersbe/bbprverify.py` -- read. Its Bitbucket Cloud sibling.
- `src/brothersbe/bbstatus.py` -- WRITE. Posts a gate verdict to Bitbucket's build-status API; called by `scripts/local-gates.sh` below.
- `src/brothersbe/protections.py` -- read. `sbe protections verify`'s `gh api` client, GET only.
- `src/brothersbe/gui/server.py` -- reserved, not yet built (2026-08-05 amendment above); loopback-only when it exists.
- `install.sh` -- read. `git ls-remote`, then `git clone` or `git -C ... pull --ff-only`; runs once, before any session exists.
- `tools/sbe_bb_estate_check.sh` -- read. Calls the Bitbucket REST API to finish an estate's Pipelines check.
- `scripts/branch-inventory.sh` -- read. `git ls-remote --heads origin`, an operator script.
- `scripts/local-gates.sh` -- read (`git fetch origin main`) and WRITE (`gh api -X POST` on GitHub; delegates to `bbstatus.py` above on Bitbucket).

## Capture is off by default, per category

A default installation captures no transcript text and no correction excerpt.
Nothing is read out of a session transcript until a category that needs it is
switched on, and each category is switched on separately:

| Category | Switch | What turning it on stores |
|---|---|---|
| `metrics` | `BROTHERSBE_TELEMETRY_METRICS=1` | the per-session row in `outcomes.jsonl` |
| `transcript` | `BROTHERSBE_TELEMETRY_TRANSCRIPT=1` | transcript text in the resume brief |
| `corrections` | `BROTHERSBE_TELEMETRY_CORRECTIONS=1` | excerpts of your own messages |

With a category off, the tool says so on the line where it would have reported a
capture, naming the switch. `transcript` off (the default) means no resume brief
is written at all, matching `metrics` and `corrections`: the `precompact-brief`
code path that would have written it names the switch once on stderr instead, so
an absent file is never mistaken for a quiet session. Flip decision (founder,
2026-07-29): the resume brief used to be written either way, real content on,
a `[REDACTED]` placeholder off, which made it the one category still writing a
file by default.

`metrics` is opt-in as well, even though it stores no message text: the row
carries the basename of the working directory, and a directory basename can be a
client's name.

**The organization override.** Set `BROTHERSBE_TELEMETRY_DISABLE=1`, or put
`capture = off` in `/etc/brothersbe/telemetry-policy.conf` (override the path
with `BROTHERSBE_TELEMETRY_POLICY`). Either one forces every category off and no
local switch can turn one back on. The file is the half a user's own shell
cannot unset, and on a managed machine it lives where an ordinary user cannot
write. A policy file that exists and cannot be read, or that carries a directive
this version does not recognize, fails closed: capture is off and the reason
names the file and the line. Its limit, stated plainly: this is a policy control
on a cooperating machine, not an enforcement boundary. Anyone who can edit that
file, or run a patched copy of the script, is past it.

## Seeing, exporting and deleting what is stored

```bash
python3 tools/sbe_telemetry.py data-show          # every file, its records, its mode
python3 tools/sbe_telemetry.py data-export --out bundle.json   # owner-only copy
python3 tools/sbe_telemetry.py data-purge         # names what would go
python3 tools/sbe_telemetry.py data-purge --yes   # deletes it, then re-checks the disk
```

All three read one inventory, so a file `data-show` lists is a file
`data-export` copies and `data-purge` removes. `data-purge` re-checks the
filesystem after each removal and reports anything that survived, rather than
reporting success from its own intention. `purge-corrections` still exists and
still does only the corrections file:

```bash
python3 tools/sbe_telemetry.py purge-corrections        # shows what is there
python3 tools/sbe_telemetry.py purge-corrections --yes  # deletes it
```

`data-show` reports this vault only. A backup, a mirror or a sync client may
hold copies of any of it, and nothing here can see those.

## Data dictionary

Every field that can be stored, with the switch that has to be on for it to
exist. Everything below lives under `$BROTHERSBE_VAULT/99-System/telemetry/`.

`outcomes.jsonl` (one JSON object per recorded session, category `metrics`):

| Field | What it holds |
|---|---|
| `schema` | ledger schema version, currently 2 |
| `ts` | when the row was written, ISO 8601 UTC |
| `session_id` | the harness's session id |
| `project` | the BASENAME of the working directory, which can be a client name |
| `end_reason` | the reason the harness gave for the session ending |
| `gen_ai.usage.output_tokens` | output tokens summed over the session's messages |
| `gen_ai.usage.input_tokens` | input tokens summed over the session's messages |
| `sub_out_tokens` | output tokens summed over subagent transcripts |
| `cache_write`, `cache_read` | cache creation and cache read input tokens |
| `api_msgs`, `human_msgs` | count of assistant messages, count of operator messages |
| `tool_calls`, `agent_spawns`, `workflow_calls` | counts of tool uses by kind |
| `subagent_files` | how many subagent transcript files were read |
| `models` | the model names the session used |
| `duration_h` | first to last message timestamp, in hours, idle included |
| `token_basis` | always `as-flushed`, because the transcript can lag the last turn |

No message text, no prompt, no file content, and no file path appears in a
metrics row.

`corrections.jsonl` (category `corrections`, owner-only 0600):

| Field | What it holds |
|---|---|
| `ts` | when the excerpt was written |
| `session_id` | the session it came from |
| `project` | the basename of the working directory |
| `text` | up to 400 characters **of your own message**, secret-redacted |
| `redactions` | how many substrings were masked, present only when some were |

At most five excerpts per session, only from operator messages the correction
pattern matched, only from the main transcript.

`last-resume-<project>-<hash>.md` (category `transcript`, owner-only 0600): the
last operator message (600 characters), up to four recent assistant text blocks
(300 characters each), up to ten recent tool descriptors (a command line
truncated to 100 characters, or a file path), and the last write-ahead intent
line. Every one of those is secret-redacted before it is written.

Written by explicit commands rather than by capture: `ratings.jsonl` (the score,
task and note you typed), `reviews.jsonl` (a timestamp and note), and
`intent-<project>-<hash>.log` (the intent lines you typed, one per line).
`installed-skill-version-brothersbe` holds one git sha (namespaced so a
sibling skill sharing the vault cannot overwrite it). `autosave.log` and
`autosave-exclusions.log` hold snapshot events and excluded PATHS with reasons,
never excluded content.

Secret-shaped substrings (API keys, tokens, `password=`, private keys,
national-ID and card shapes) are redacted before anything above is written.
Redaction is best-effort pattern matching, not a guarantee. Keep the vault out
of version control (the shipped `memory-template/.gitignore` excludes it).

To disable capture entirely without touching the switches, remove the
`SessionEnd` hook. You lose the automatic capture half of the learning loop;
everything else keeps working.

## The autosave makes no network call either

`tools/sbe_autosave.py` runs on the PreCompact hook (right before Claude Code
compacts context, which is what happens when you run low on tokens). It snapshots
your entire working tree, including untracked files, into a private git ref
`refs/brothersbe/autosave/<worktree-id>` (one ref per worktree, so two worktrees
of one repository cannot overwrite each other's snapshots), using a throwaway
index so your real branch, index,
and working tree are never touched. It runs git **locally only and never pushes**,
so the zero-network property above still holds with autosave enabled. Recover a
snapshot with:

```bash
python3 tools/sbe_autosave.py recover
```

An optional continuous mode (`sbe_autosave.py tick`, off unless you set
`BROTHERSBE_AUTOSAVE`) also snapshots every N tool calls, for a crash that is not
a compaction. To disable autosave entirely, remove the PreCompact hook.

### What the autosave will not put in a git object

A snapshot is a permanent git object, so every candidate file's CONTENT is read
BEFORE `git add` runs, which is the moment a blob would be created. A file is
kept out when its content matches a secret shape, when it is larger than
`BROTHERSBE_AUTOSAVE_MAX_BYTES` (1 MiB by default, so it was never scanned),
when it is binary (this scanner cannot read one for secret shapes), or when its
name is one of the secret-shaped names. Every exclusion is written to
`99-System/telemetry/autosave-exclusions.log` with its reason, as a path and a
reason only, never the matched content. `python3 tools/sbe_autosave.py recover` points at that
record, because what a snapshot does NOT hold matters at recovery time.

Two statements that belong together. A file name pattern was never a control
over secrets: a credential lives in a normally named source file at least as
often as in a file called `.env`, and this project shipped a version whose
comment claimed otherwise. And the content scan that replaced that claim is
pattern matching too, so a secret in a shape it does not know still enters the
snapshot. An excluded file is left out of the snapshot entirely, so an unsaved
edit to it is preserved nowhere.

In a repository you declare production (`BROTHERSBE_REPO_CLASS=production`, or a
`.brothersbe-production` file at the top of the checkout), autosave is opt-in: it
snapshots nothing until `BROTHERSBE_AUTOSAVE_PRODUCTION=1` is set, and the skip
line names both the marker it read and the switch that would enable it.

`docs/THREAT_MODEL.md` covers this and fourteen other threats, including the
ones nothing here stops.

## The update check makes no network call

`tools/sbe_telemetry.py check-update` runs at session start and tells you when your
installed copy differs from an already-fetched origin, when it has gone stale, and
once when the law itself changed under you. It does this by reading git ref files
directly. It never runs `git`, never opens a socket, and never contacts a server, so
the zero-network property above still holds with the check enabled. The cost of that
choice: it can only see an update that something else already fetched, which is why
it also warns when your copy is simply old.

To disable it, remove the `check-update` line from `tools/sbe_sessionstart.py`.

## A publishable-history hit can be accepted, on record

`test_no_private_name_reaches_publishable_history` in `tools/test_sbe.py`
reads every object reachable from `HEAD` and from the remote-tracking refs,
because a name deleted from the tracked tree can still sit in a blob or a
commit message no edit reaches. When a hit was already public before anyone
noticed, no edit can clean it either: the object is on the remote, and a
history rewrite is its own recorded failure class, not a free fix.

For exactly that situation the project can record an ACCEPTANCE instead of
pretending the object is gone. A tracked file at the repository root,
`.sbe-private-history-acceptance.json`, carries the decision date, the exact
decision wording, who accepted it, the flip condition that reopens the
question, and one entry per accepted object: its object id, its path, the
remote ref(s) that reach it, and a `reason`. The test reads this file. An
object id listed there with a non-empty reason is reported as **WAIVED**,
named by object id, printed to the test output; it never counts as a PASS
and the run says so in words. An object id with no matching entry, or an
entry whose reason is blank, still FAILS by id. A record that is missing,
unreadable or malformed waives nothing: every hit fails exactly as it did
before the record existed.

A WAIVED result is an exposure on record, not a cleanup. Its flip condition:
any external citation of either accepted term reopens the question, and the
remediation at that point is a clean extraction into a fresh repository, the
same fix any other leaked private term in history requires.

## Scope note

This project governs how a Claude Code session behaves. It does not change what
Claude Code itself transmits to Anthropic or your chosen cloud provider. For
that, see Anthropic's own documentation on Claude Code data usage, and choose
your plan accordingly: commercial terms (Team, Enterprise, API, cloud providers)
differ materially from consumer plans.

## Verifying what you installed

The repository ships a SHA256 manifest (`CHECKSUMS.sha256`, generated by
`scripts/checksums.sh`) and a checker for it:

```bash
cd ~/.claude/skills/brothersbe && scripts/verify-install.sh
```

One expectation to set before you run it: any file YOU created inside the
install is reported as EXTRA and fails the check, including the demo dossier
the README walkthrough creates (`design/my-project`). That is the checker
doing its job, not an intrusion: it cannot tell your scratch file from a
planted one, so it names both. Delete what you created (`rm -rf
design/my-project`) and re-run, and keep real work outside this clone.

It verifies both directions: every file the manifest names matches on disk,
and every file on disk appears in the manifest, so a planted extra file fails
rather than riding along unexamined. What a PASSED does NOT prove: that the
manifest itself is authentic. Take the manifest from the release you trust
(the tag's git history), not from the same channel as the code you are
checking. [docs/RELEASE.md](docs/RELEASE.md) is the cut and pin runbook, and
it states plainly which of its steps have never been executed.

Commits are unsigned. If your organization pins to commits rather than tags,
record the hash yourself:

```bash
git -C ~/.claude/skills/brothersbe rev-parse HEAD
```
