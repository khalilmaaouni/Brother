# Security

## Supported versions

| Version | Status |
|---|---|
| 3.2.x | Supported. Security fixes land here. |
| Everything earlier | Best effort only. No security backports are promised. |

"Best effort" is meant literally rather than as a softener: an older line gets
a fix if the fix happens to apply cleanly and somebody has reason to do it, and
otherwise the answer is to upgrade. A single maintainer cannot honestly promise
more than that, and promising it anyway is how a support commitment becomes
something a reader relies on and does not get.

## Reporting a vulnerability

**If the problem would expose anyone's data by being described in public, do
not open a public issue.** Use GitHub's private vulnerability reporting on this
repository (the Security tab, "Report a vulnerability"), which opens a private
thread visible only to the maintainer.

If private reporting is not available to you for any reason, open a public
issue that says only that you have a security report and gives no details, and
ask for a private channel. A one-line placeholder discloses nothing; a full
reproduction in a public issue discloses everything, to everyone, immediately
and permanently.

For anything that could NOT expose data by being public (a hardening
suggestion, a missing check, a documentation error about the data flow), a
normal public issue is the right place and is genuinely welcome.

**FOUNDER ACTION, not done by this release and not doable in code.** Enabling
private vulnerability reporting is a setting in this repository's own GitHub
configuration, under Settings, Advanced Security. No commit can turn it on. It
is listed here as an outstanding action rather than described as if it were
already true, because a security page that names a reporting channel which does
not actually exist is worse than one that names none: it sends somebody who
found a real problem into a dead end and leaves them assuming they were heard.
Until that switch is on, the fallback paragraph above is the live path.

**NOT DONE, stated because it gates who should use this.** No external security
review has been carried out. The threat model below is the maintainer's own
account of what this software defends and what it does not, written in good
faith and never independently audited. Everything in it is checkable against
the code, and none of it has been checked by anyone without an interest in the
answer. Weigh it accordingly, particularly if you are deciding whether a team
rather than an individual should adopt this.

## What this software does with your data

BrotherMode's hooks and tools make no network calls when running on their own.
It has no analytics, no account, and no server. CORRECTED 2026-08-01: the
sentence above used to say flatly "makes no network calls", and that stopped
being fully precise the day /brotherme-update shipped: that command, and only
that command, runs `git ls-remote` against the public repository to compare
your installed version with the newest release tag, only when you invoke it,
sending nothing but the standard git query. Nothing that runs automatically
(hooks, gates, the store, the docs engine) reaches the network, ever. Most of what it writes goes to your vault folder, which you choose with
`BROTHERMODE_VAULT` (default `~/BrotherModeVault`). The work registry is the
exception: it writes inside your project directory, not the vault, so you can
find it there too:

CORRECTED 2026-07-27 (external audit finding 17). The list below previously named
`threads/registry.json`, `threads/REGISTRY.md`, `threads/.registry.lock` and
`threads/.mode.lock`. Phase 3 deleted `bm_registry.py` and rewrote thread storage
on top of the sqlite store, so no shipped tool writes any of those four files any
more. Verified by grep across `tools/*.py`. Stale security documentation is an
operational risk in its own right, because a reviewer audits the data flow the
document describes rather than the one the code has.

What the code actually writes inside your project today:

- `.brothermode/store.sqlite3` and its `-wal` / `-shm` sidecars: the raw store,
  holding objectives, decisions, digests and directives BEFORE redaction. This is
  the sensitive artefact. It is excluded from git and, as of the audit
  remediation, the tools refuse to run when it is already tracked.
- `threads/thread-mode.json` under your project root.
- Each thread gets `threads/<name>-<id>/STATE.md`, `inbox.md`, `outbox.md` and
  `digest.md`. Everything written there is redacted at the write.
- There are no lock files. Ordering comes from single sqlite transactions, and a
  test forbids importing `fcntl` anywhere in the toolchain.
- `STATE.md` is regenerated from the store on every mutating command (a generated
  view, never hand-edited truth). One honest exception, still open at the time of
  writing: handover delivery APPENDS a block to `STATE.md` outside the store
  transaction (audit finding 12). That is being moved into the store so the view
  is generated rather than appended to. Until it lands, treat `STATE.md` as
  "regenerated, plus one appender", not as purely generated. Each
  thread also gets its own `threads/<name>-<id>/digest.md`, a view of that
  thread's recorded handover. There is no `absorb` command in either CLI
  (confirmed by running `--help` on both, 2026-07-26: `bm_store.py`'s
  commands are adopt, checkpoint, claim, complete, dashboard, decide, dump,
  init, park, resume, verify; `bm_threads.py`'s are adopt, checkpoint,
  complete, dashboard, decide, off, on, park, recommend, resume, send,
  start). An earlier draft of this document named a command that was never
  shipped.
- The V2 store (`tools/bm_store.py`, arriving module by module) writes
  `.brothermode/store.sqlite3` under your project root. That database holds your
  objectives, decisions, digests, and directives AS YOU TYPED THEM: redaction in
  V2 applies at every exit (generated `STATE.md` views, rendered digests,
  dashboard output), while the database itself is the raw, sensitive artifact.
  The learning tables are the one part scrubbed on the way IN as well: every
  field you type into a correction (trigger, action, reason, domain, scope key,
  approval reference, override reason) has secret-shaped substrings masked
  before it is stored, and the count of what was masked is on the candidate.
  Your verbatim capture text and the evidence excerpts taken from it are
  withheld from `dump` entirely and from every `--json` command unless you pass
  `--show-source`.
  As of the 2026-07-29 privacy loop this is no longer special to the learning
  tables. ONE withholding policy now governs every export (`dump`, its JSON
  output, and the MCP server's responses): founder prose is WITHHELD, not
  merely scrubbed, because the scrubber only removes secret SHAPES and
  ordinary sentences carry none. That covers objectives, evidence, digest
  bodies and next intents, transition notes, decision text and directive text.
  Absolute filesystem paths are masked wherever an export does still print
  text, because `/Users/jane.doe/clients/acme` names a person, an employer and
  a client in one string. Masking stops at a space and at a handful of
  characters that end a path in ordinary prose (quotes, backtick, angle
  brackets, pipe, comma, semicolon, colon, and paired brackets and braces),
  so a path containing one of those is masked only up to it; everything else,
  including every non-ASCII name, is covered.
  What an export still shows is structural: identifiers, states, versions,
  hashes, counts, timestamps, plus the record name, tier and claimed path,
  which stay readable so a dump and the fence tools are still usable, and
  which are scrubbed and path-masked on the way out. A session id is shown
  only when it looks like a generated identifier: `--session` is free text, so
  a session id carrying a path, a key or a sentence is withheld like any other
  founder text. `dump --raw` returns everything, prints a warning on standard
  error saying so, and is the only way to get the founder text back out of an
  export.
  The local views you read yourself (`STATE.md`, `digest.md`, `inbox.md`) are
  NOT exports: they carry your real text, scrubbed at the display boundary,
  because they are the product.
  Treat it like the corrections file below. If the database is ever corrupt it
  is renamed to `store.sqlite3.quarantine-<timestamp>` and never deleted, so a
  quarantine file is exactly as sensitive as the store. `bm_store.py init` adds
  `.brothermode/`, `threads/`, and `STATE.md` to your repo's `.git/info/exclude`
  so none of this reaches version control by accident. File permissions are
  owner-only where the platform supports it (on Windows this is best-effort;
  rely on your user profile's access control).

You can verify both claims yourself; the tools are about 212,800 lines of
standard-library Python and shell (re-measured 2026-08-30 after the MCP
connector catalog landed; the figure of 152,700 from 2026-08-20 drifted past
the 15 percent guard the test enforces, as did the figure of
128,300 from 2026-08-10 before it,
standard-library Python and shell (re-measured 2026-08-31 after the VB3-04
access-policy fail-closed work landed; the figure of 180,100 from 2026-08-30
drifted past the 15 percent guard the test enforces, as did the figure of
152,700 from 2026-08-20 before it,
the figure of 128,300 from 2026-08-10 before that,
and before it the figure of 108,900 from
2026-08-05 drifted the same way, so it is
corrected here rather than restated, which is the fifth such correction and is
exactly the pattern the promise below is about. Worth naming plainly: this
correction was not noticed by a person. The drift test refused the change and
named the two numbers, which is the only reason the figure is right rather than
comfortable). Most of that growth is test code, which is the kind
a reader of a security document should want: of the roughly 14,900 lines the
controller added, about 4,400 are the engine and its command line and the rest
are behavioral tests, including the tests that six adversarial refutation
rounds produced. Those rounds are why the number moved twice in one day: each
one reproduced a defect with a probe, and each reproduction became a permanent
test rather than a note. Eleven shipping tools import subprocess, each for LOCAL execution: bm_autosave.py drives git (never a push, never a remote), bm_controller.py (the Full-Auto controller) runs each unit's deterministic done-check as a local command, bm_continue.py starts the successor session as one detached local `claude -p` process whose output goes to a local log file, bm_passport.py (the change-passport producer) runs exactly one command, `git -C <root> config user.name`, a local config read that answers the accountable-person field from a real source instead of guessing it, bm_fence_hook.py (the battery fence, 2026-08-17) asks git one local question while a test-gate lock is live, `git ls-files --error-unmatch` for whether a write target is tracked, a local index read with no remote and no push, and brothermode_cli.py (the v3 public boundary) dispatches its eleven verbs to the existing local tools, with one stated network exception: its update check runs a single read-only `git ls-remote --tags` against the configured remote, a network READ that writes nothing and pushes nothing. Two more joined them on 2026-08-17 when the session-start hook and the hook chain driver were ported from POSIX shell to Python so they can run on Windows: bm_sessionstart.py and bm_hookchain.py each start SIBLING TOOLS from this same repository as local processes, which is exactly what the `sh` wrapper they replaced did. Worth stating plainly rather than counting quietly: those two held this power before the port as well, and as shell scripts they were invisible to a check that reads Python imports, so the port brings them INSIDE the audited set rather than adding a new capability. Three more belong on this list and were missing from it until 2026-08-23, which is a drift in this document rather than in the control: bm_bench.py and bm_cursor.py were named exceptions in the test's table while this paragraph still said eight, and bm_reconcile.py (startup reconciliation) joined them the same day, asking git two local questions to locate the checkout it classifies, `rev-parse --show-toplevel` and `rev-parse --abbrev-ref`, both local reads with no remote and no push and both carrying explicit failure paths rather than a discarded exit code. Every one of these eleven is a named exception in the no-network test, per file and per module, so no twelfth tool inherits the allowance quietly. The count in this sentence is the number the test's own table holds; it fell behind twice, so it is worth re-reading the table rather than trusting this prose. Three vault tools joined the local-subprocess set on 2026-08-30 and are named in the test's table like the rest: bm_vault_contract.py asks git one local question (`git -C <vault> diff --cached --name-only`) to learn which staged notes the per-class contract must gate; bm_vault_exchange.py orchestrates the `age` binary over local pipes because reimplementing age's crypto in Python would be homemade crypto, which its own docstring refuses; and bm_vault_tiers.py reads the staged diff and runs the sibling catalog checker over this same interpreter. None takes a remote, fetches, pushes, or opens a socket.

FOUR MORE JOINED THE TABLE with the vault memory tooling (2026-08-28/29) and were missing from this paragraph the same way the earlier three were: bm_private_scan.py runs symbolic-ref, rev-list, cat-file, log, for-each-ref and rev-parse against the local object database; bm_vault_catalog.py runs one ls-tree against HEAD; vault_recall_hook.py and bm_vault_distill.py each run this same interpreter against the sibling tools/bm_vault.py (`index` to refresh, `recall --query` to search), a local process start with no remote, no push and no fetch. bm_vault.py itself is the SECOND tool on this list that is not local-only, alongside bm_bench.py below: its dense-retrieval stage can shell out to tools/bm-embed-bge, which downloads a model from HuggingFace on its first run in an environment nobody but a developer who built a .venv-embed by hand would have, and is absent entirely for an installed user, per the packaging suite's REPO_ONLY classification of that shim.

THREE MORE JOINED THE TABLE on 2026-08-29, with the W9 controller train and the freshness half of the consolidation work, and they are recorded here for the same reason the earlier stragglers were: the prose fell behind the table again, which is drift in this document rather than in the control. Each was READ before it was allowed, not added to clear a red gate. bm_freshness.py asks git one local question (`rev-parse --show-toplevel`) and then runs a local `grep -rlF` over a root directory, under a 20 second timeout, to decide whether a cited anchor still resolves; no remote, no push, no fetch. bm_repair.py starts this same interpreter against the sibling tools/bm_vault.py (`recall --query`), exactly the pattern vault_recall_hook.py and bm_vault_distill.py already carry, and it shells rather than importing because that module's search is private. bm_worker_spawn.py starts a CONFIGURED argv as a local process with a sanitised environment, and that one deserves the plain statement rather than the comfortable one: like bm_continue.py, what it spawns is whatever it was configured to spawn, so if that is an AI agent then running it causes network traffic, in the same scoped way bm_bench.py below already admits. The tool itself opens no socket.

All three were missing from FOUR registries at once (this claim, pyproject py-modules, the reviewed write-site inventory, and the command effects registry), which is worth naming as a pattern rather than three separate oversights: a tool can land, pass review, and be invisible to every mechanical check the project owns.

ONE TOOL MAKES A NETWORK WRITE, and it is the only one: tools/bm_bbstatus.py (2026-08-17) posts a single build status to Bitbucket Cloud after a local gate run finishes, so a team whose repository lives on Bitbucket sees the same verdict a GitHub team already saw through `gh`. It is never wired into a hook, never runs on its own, and is reached only when somebody runs scripts/local-gates.sh on a checkout whose origin is Bitbucket. Its other two subcommands, `classify` and `slug`, touch no network at all: both are pure string parsing over a remote URL, and `slug` was added on 2026-08-26 so the GITHUB arm of that runner could stop posting to a hardcoded repository name, which means this tool is now read by both arms while still making exactly one network write and only on the Bitbucket one. Its credential comes from the environment and is never written to a receipt, a log or an error message. Like the others it is a named per-file exception in the no-network test, and additionally in the two-host lint in tools/test_bm_hooks.py, which refuses a host API anywhere else under tools/ or hooks/.

ONE MORE TOOL PERFORMS NETWORK READS ON EXPLICIT INVOCATION, added 2026-08-30 and stated plainly rather than folded in: tools/bm_connectors.py, the MCP connector catalog. Its `check` subcommand runs live reachability probes (`gh auth status` and one `gh api rate_limit` call, `ssh -T git@bitbucket.org` with the pinned key, `az account show` where the az CLI exists), and those probes reach the network on purpose, because a reachability check that touched nothing would be theater. It is never wired into a hook, never runs on its own, reads no credential value (the probes use logins this machine already holds), and its `list` and `print-add` subcommands print catalog facts and wiring the founder runs by hand; `print-add` executes nothing, which its own suite proves by making every subprocess entry point raise. Like the others it is a named per-file exception in the no-network test, per file and per module, so no other tool inherits the allowance. The same `check` also probes a local Azurite storage emulator on 127.0.0.1:10000 (loopback only, never a remote host) with one unauthenticated GET, reading the "Server: Azurite-Blob" header off an ordinary HTTPError to tell an emulator from silence; it never signs a request with the published, non-secret devstoreaccount1 dev key, so it reads no data, real or synthetic, and a silent port is NO-DATA naming `npx azurite`, never a failure. Its `conformance` subcommand, added the same day, spawns tools/bm_mock_mcp.py as a local subprocess and talks to it over stdio only (never a socket, never a port); that traffic is local process I/O, not a network call, and is unaffected by this exception.

ONE MORE TOOL RUNS TWO LOCAL OS COMMANDS, added 2026-08-30: tools/bm_vault_posture.py, the encryption posture census (WBS row VB8-01). It runs `diskutil apfs list` and `df -P <path>` to read the operating system's own answer for whether the volume holding the vault (and each derived store bm_vault.py's tools write) is encrypted at rest. Both are local, read-only OS utilities; neither takes a remote argument, and neither is ever wired into a hook. Like the others it is a named per-file exception in the no-network test, per file and per module, so no other tool inherits the allowance.

THE ANSWER LEDGER, THE ACCESS AUDIT AND THEIR OUTCOMES ARE A DIFFERENT PROTECTION PATTERN FROM `dump`, AND WORTH SAYING SO PLAINLY. bm_vault.py's `recall` appends one JSON line per call to `bm_vault_answers.jsonl` (VB2-05: what was read -- served hit ids, paths, content hashes), bm_vault_audit.py appends a sibling line to `bm_vault_audit.jsonl` (VB7-04: who read it -- principal, served count, withheld count), and bm_vault_ledger.py appends telemetry outcomes to `bm_vault_outcomes.jsonl` (VB6-03). All three sit beside each other under `~/.claude/` and all three carry one identical sensitive field: the free-text `query`, verbatim, because a query can name a person, a project or a fact the asker never meant to log (bm_vault_audit.py's own docstring says this plainly). That is the same class of founder-typed text the withholding policy above (`dump`, its JSON output, the MCP server's responses) protects by redacting it on the way OUT. These three files protect it a different way, because they are not that kind of surface: each is a flat JSONL file a local reader (`bm_vault_ledger.py show/replay/census/join`, `bm_vault_audit.py search`) opens directly off disk, so a redaction gate on the reading TOOL would protect nothing -- `cat ~/.claude/bm_vault_answers.jsonl` reads straight past it. The real control is the file's own permission bit: owner read/write only (0600), created that way from the first `os.open()`, never group- or world-readable. `bm_vault_audit.jsonl` was built with that mode from the start (test_bm_vault_audit.py's `test_append_creates_the_audit_file_mode_0600`, a MAJOR finding from that file's own security review). `bm_vault_answers.jsonl` and `bm_vault_outcomes.jsonl` were not: both were created 0644 (world-readable) from VB2-05 and VB6-03 respectively, holding the identical sensitive field the audit file's review had just flagged, until this was found and backfilled (test_bm_vault_ledger.py's own `test_01a_ledger_file_created_mode_0600` and `test_03a_outcome_file_created_mode_0600`). Read the mode back yourself:

```bash
python3 tools/bm_vault.py recall --query "anything" --fast >/dev/null 2>&1
stat -f '%Lp %N' ~/.claude/bm_vault_answers.jsonl ~/.claude/bm_vault_audit.jsonl
```

bm_vault_posture.py (VB8-01, above) census all three as derived stores whose disk-encryption posture matters precisely because file permissions are only as good as the disk under them; that census does not itself change or check the file mode. Retention for all three is UNDECIDED (bm_vault_audit.py's own docstring), pending the VB7-05/VB7-06 rulings: nothing here rotates or expires a row today.

ONE TOOL OPENS AN ACTUAL SOCKET, added 2026-08-30 with VB2-02, "the vault answers over the wire": tools/bm_vault_serve.py, a dependency-free HTTP server (`http.server`) fronting the existing recall stack. It never reimplements ranking; `POST /recall` invokes `tools/bm_vault.py recall` as a subprocess and serves back exactly what local recall prints. It binds `127.0.0.1` by default and REFUSES to start on any other interface without `--token-file` (a shared secret read from a file, never argv, compared with `secrets.compare_digest`, and an empty token file is refused too); localhost without a token stays reachable and `GET /health` says so out loud. It is never wired into a hook and only runs when a person starts it deliberately. Cross-machine transport (a tunnel or a tailnet) is the founder's own network decision, not this module's to open. Like the others it is a named per-file exception in the no-network test, per file and per module, so no other tool inherits the allowance.

A SECOND TOOL OPENS A SOCKET, added 2026-08-30 with VB11-05, "the approval pane": tools/bm_vault_pane.py extends bm_vault_serve.py's own pattern rather than inventing a second one, and carries the identical bind/token posture (`127.0.0.1` by default, REFUSES a non-loopback bind without `--token-file`, never wired into a hook, started deliberately). It never reimplements a promotion or a curation write: `GET /pending` lists pending promotions and curation candidates by reading the vault and the curation queue file, and `POST /act` runs the real `bm_vault_promotions.py` or `bm_vault_curate.py` command, as a direct function call rather than a subprocess, under the CLICKING PRINCIPAL's own name. Every `POST /act` must echo an HMAC action token minted by that same process for the exact (item, decision) pair a prior `GET /pending` offered, keyed to a random secret generated once at process start and never persisted; a wrong, stale or missing token is refused before anything is read from or written to the vault or the queue. A revoked principal (per `bm_vault_principals.py`'s own registry) is refused with that registry's own refusal text, and every outcome, refused or recorded, lands in the same access audit (`bm_vault_audit.py`) that already records who acted on a recall. Like the others it is a named per-file exception in the no-network test.
ONE MORE TOOL MAKES A DELIBERATE NETWORK SEND, added 2026-08-30 with VB11-06, the notification delivery lane: tools/bm_vault_notify.py, one shared renderer that turns a per-principal digest page (VB11-04) into the same digest's HTML email and Teams Adaptive Card, plus two send adapters. Its `send --channel email` path is the network write: on EXPLICIT invocation only, never from a hook and never automatically, it reads a mailbox credential from the macOS keychain (`security find-generic-password -s brother-mailbox -w`, at call time only, never stored, never echoed, never logged, never in argv) and, only when that credential is present, opens one SMTP connection with `smtplib` to deliver the rendered message. No credential in the keychain is NO-DATA naming `brother-mailbox`, before smtplib is ever touched, while every other channel in the same run keeps working. `subprocess` is named here too, for that one local, no-network `security` call, the same posture bm_passport.py and bm_fence_hook.py already carry for a local keychain or git read. Its `send --channel teams` path opens no socket at all: it is fixture-mode only pending tenant consent (docs/TEAMS-CONSENT-REQUEST.md names the exact Graph scope, `ChannelMessage.Send`, fetched from Microsoft's own live documentation rather than recalled), and writes the card payload and recipient identity to a caller-named `--mock-sink` file instead of calling Graph. Like the others it is a named per-file exception in the no-network test, per file and per module, so no other tool inherits the allowance.

A THIRD TOOL OPENS A SOCKET, added 2026-08-31 with VB5-06, "the warm embedder": tools/bm_embed_warm.py holds the dense-retrieval model (BAAI/bge-small-en-v1.5) resident in memory across calls, because tools/bm-embed-bge's own cold load costs 7-9 seconds on every call. It carries the identical bind/token posture bm_vault_serve.py and bm_vault_pane.py already established (`127.0.0.1` by default, REFUSES a non-loopback bind without `--token-file`, never wired into a hook, started deliberately with `python3 bm_embed_warm.py serve`). `POST /embed` returns only ids and rounded floats, never the input text; `GET /health` reports whether it is warm and how it is authenticated. `tools/bm_vault.py`'s own `_embed_texts` tries this daemon first, over a loopback connection with a tight (~200ms) connect timeout, and on ANY failure (daemon absent, refused, timeout, malformed reply) falls back to the ORIGINAL subprocess path (`_embed_texts_subprocess`) unchanged, silently and without raising: an absent warm process is the expected common case, not an error. The `measure` subcommand times both paths against the real embedder and prints the delta, or a named `NO-DATA` line when no interpreter on the machine can load the model, never a fabricated number. Like the others it is a named per-file exception in the no-network test, per file and per module, so no other tool inherits the allowance.

ONE TOOL IS A NETWORK CLIENT RATHER THAN A SERVER, added 2026-08-31 with VB3-09, "the service contract": tools/vault_client.py, a minimal stdlib-only Python client for bm_vault_serve.py's own `/v1/health` and `/v1/recall` routes, written to be lifted onto a second machine standalone with no checkout and no folklore. It opens no listening socket of its own; every call it makes is an outbound `urllib.request` connection to a base URL the caller names explicitly (127.0.0.1 by default, in its own `--base-url` flag), and a non-2xx response is surfaced as a raised `VaultError` carrying the server's structured error body rather than swallowed or guessed at. Like the others it is a named per-file exception in the no-network test.

The small-toolchain promise still stands: if the
NON-test line count starts climbing like this, the honest move is to withdraw
the claim, not keep restating a larger number.

It went UP by roughly 2,700 lines on 2026-07-27, and that direction deserves
an explanation rather than a quiet edit. The external security audit of that
day found real escapes at the filesystem boundary, and closing them added one
shared containment funnel plus the adversarial tests that prove each escape
stays closed. Most of the growth is tests, which is the kind a reader of a
security document should want. The small-toolchain claim is still a promise
this project owes: if the non-test line count keeps climbing, the honest move
is to withdraw the claim rather than restate it.

This figure was raised three times in one day, then fell once Phase 3 landed
the same day: the V2 store shipped ALONGSIDE the V1 registry and thread tools
it replaced for a while, and Phase 3 deleted `bm_registry.py` (917 lines) and
rewrote `bm_threads.py` on top of the store instead of its own storage,
cutting `tools/test_bm.py` from 124 tests to the 54 it has today. Roughly a
third of `bm_store.py` is still comment, much of it narrating which fix round
changed what, which belongs in git rather than in the source; a pass to
strip that provenance out of the source remains contracted, not done:

```bash
find tools -type f \( -name "*.py" -o -name "*.sh" \) | xargs wc -l
grep -rnE "urllib|requests|socket|http|curl|wget|subprocess" tools/
```

Two files inside the vault deserve attention:

- `99-System/telemetry/outcomes.jsonl` holds per-session counts (tokens, tool
  calls, duration) plus the basename of the working directory. No file contents,
  no prompts.
- `99-System/telemetry/corrections.jsonl` holds short excerpts **of your own
  messages** that look like corrections, so the weekly review can turn them into
  rules. Secret-shaped substrings (API keys, tokens, `password=`, private keys,
  national-ID and card shapes) are redacted before anything is written, and the
  file is created owner-only (0600) on POSIX; on Windows `os.chmod` is
  best-effort and the real control is your user profile. That includes the
  paired fields: the
  previous response excerpt AND the file paths of the tools that ran, because a
  path can carry a secret in a directory name. Redaction is best-effort pattern
  matching, not a guarantee. Treat the file as sensitive, keep it out of version control
  (the shipped `vault-template/.gitignore` excludes it), and purge it whenever
  you like:

```bash
python3 tools/bm_telemetry.py purge-corrections        # shows what is there
python3 tools/bm_telemetry.py purge-corrections --yes  # deletes it
```

To disable correction capture entirely, remove the `SessionEnd` hook. You lose
the automatic capture half of the learning loop; everything else keeps working.

## The outcome benchmark DOES cause network calls, and it is the one exception

Added 2026-08-10, stated plainly rather than tucked into a list. Every other
sentence on this page is about tools that run on their own, through a hook,
without anybody asking. `tools/bm_bench.py` is not one of those. It is run
deliberately by a person, never by a hook, and it invokes an AI coding agent
as a subprocess in order to measure it. That agent makes network calls, so
running the benchmark causes network traffic.

There is no way around that and no reason to want one: measuring whether an
agent delivers an outcome requires running the agent. What matters is that the
claim above is SCOPED rather than absolute, and that this exception is written
where a reader will find it instead of living only in a test allowlist. The
mechanical check in `tools/test_bm.py`
(`test_no_network_claim_is_mechanically_true`) names this file by name, per
module, so a second tool cannot quietly inherit the exception.

What the benchmark does NOT do: it sends nothing of yours anywhere. It copies a
task fixture into a throwaway directory, runs the agent there, applies a hidden
acceptance test after the agent exits, and deletes the directory. Your project,
your store and your vault are never in its working directory.

## The autosave makes no network call either

`tools/bm_autosave.py` runs on the PreCompact hook (right before Claude Code
compacts context, which is what happens when you run low on tokens). It snapshots
your tracked and untracked working-tree files into a private git ref namespaced per
worktree and per session, using a throwaway index so your real branch, index, and
working tree are never touched. Ignored files and uncommitted content inside nested
repositories or submodules are NOT captured, so this is not literally your entire
disk state; it is your working tree as git sees it.

This module is the ONE documented exception to the no-subprocess rule above,
because git is an external binary and there is no way to drive it otherwise. Every
call it makes is local: never push, fetch, pull, clone, or remote, and a test
enforces both halves (the named per-file exception, and the ban on any git command
that reaches a remote). The zero-network property holds with autosave enabled.

Recover a snapshot into a SEPARATE worktree, never over your live files:

```bash
python3 tools/bm_autosave.py recover
```

The previous shell version restored in place, which was measured to delete a
tracked file that had been excluded from the snapshot. That path is gone.

An optional continuous mode (`bm_autosave.py tick`, off unless you set
`BROTHERMODE_AUTOSAVE`) also snapshots every N tool calls, for a crash that is not
a compaction. To disable autosave entirely, remove the PreCompact hook.

## The update check makes no network call

`tools/bm_telemetry.py check-update` runs at session start and tells you when your
installed copy differs from an already-fetched origin, when it has gone stale, and
once when the law itself changed under you. It does this by reading git ref files
directly. It never runs `git`, never opens a socket, and never contacts a server, so
the zero-network property above still holds with the check enabled. The cost of that
choice: it can only see an update that something else already fetched, which is why
it also warns when your copy is simply old.

To disable it, remove the `check-update` line from `tools/bm_sessionstart.py`.

## The page that shows where your project stands, and what publishing it means

Added 2026-08-05 with the live project view, and disclosed here because it is the
one artefact of this product that can leave your machine on purpose.

`bm-view render` writes `PROJECT-VIEW.html` at the top of your project folder,
and `bm-view brief-page` writes a page under `Handover/` for whoever picks up a
decision you took back. Both are ordinary files, written through the same one
write funnel as every other generated view, gated on the same consent record as
everything else: before setup, `bm-view` writes nothing, creates no folder and
publishes nothing. Both are generated from your project's own records, so what
they contain is what those records contain: your outcome in your own words, the
decisions and what was weighed, what has been learned and what would change it,
the checks and their results, spend against your ceilings, the recorded ids
behind each claim, and the file paths inside your project that the work touched.
They are subject to the same redaction rules as every other generated view, and
they carry no tokens, no receipts and no contents of any file outside what a
record holds. Read one before you send it anywhere, exactly as you would the
handover pages.

The page makes no network call. Nothing in it is fetched when it opens: no
fonts, no images, no scripts, no addresses of any kind, so it renders with the
machine offline, and there is no request that could carry anything anywhere.

Publishing is a separate act, and it is Claude's, not this toolchain's. No
command here can publish: publishing happens when Claude takes the file you
already have and puts it at a private address on claude.ai that only you and
anyone you deliberately share it with can open. Two things about that are worth
knowing before you agree to the first one. You are asked once, by Claude's own
permission prompt, and publishing that same page again afterwards does not ask
again, so after your first yes the page updates silently, which is the behaviour
you want and also the reason it is written here rather than left to be
discovered. And publishing can be unavailable to you entirely, for reasons that
have nothing to do with this product: the conditions are listed in
`docs/KNOWN-LIMITS.md`. Either way the file on disk is the primary artefact. It
is what the command promises, it is what is committed and diffed, and the
published copy is a convenience laid on top of it.

## Approval and state-change receipts are secrets

Added 2026-07-31, closing the open half of `docs/NOT-FINALIZED.md` item 21. The
code enforced these rules before this section existed; a reader had no way to
learn them from the security page.

A receipt authorises exactly one rule-changing act: approving a candidate into a
rule, editing an approved rule's injectable text, or a state change (supersede,
deprecate, forget, resolve-conflict, and resolving a critical alert). What the
code enforces:

- **Shown once, at mint time, and never again.** `grant-approval` and
  `grant-state-receipt` print the token; there is deliberately no command that
  reads a token back out of the store.
- **Never stored.** Only `sha256` of the token under a domain prefix is kept, and
  the mint path pops that column out of the record it returns, so a caller
  printing the whole record cannot print it either.
- **Withheld from every ordinary export** by the same name-shape policy that
  withholds every other digest column, so `dump` and the MCP surfaces never
  carry it.
- **Fifteen-minute life**, clamped in code, and single use: consumption is a
  conditional `UPDATE ... WHERE consumed_at IS NULL AND expires_at >= ?` in the
  same transaction as the change it authorises, so two racing spends cannot both
  win.
- **Bound to the exact proposed change.** The fingerprint covers rule text,
  scope, severity and any override in play, so a receipt minted for one proposal
  cannot be spent on another, and receipts of one kind cannot spend as another.

**What a receipt does NOT prove.** It proves an answer was supplied for this
exact proposed change and has not already been used. It does NOT prove which
human supplied it. Anything able to run the CLI as the same operating-system user
can mint one by asking, then spend it. The guarantee is "no rule changed without
a fresh, specific, one-time human answer", not "the founder personally authorised
this". Treat a leaked token as a short-lived capability: spendable by whoever
holds it until used or expired.

## Threat model (D-2, Loop 6 security closure)

Added 2026-08-01. Everything above describes what the code does; this section
states, in one place, what it is defending, from whom, and what it openly
does not defend against. Plain words: an "asset" below just means "a file or
value worth protecting", and a "trust boundary" means "the line past which
this project stops being able to promise anything".

**Assets** (the things worth protecting):

- **The store** (`.brothermode/store.sqlite3`): your objectives, decisions,
  digests, directives, alerts and (in the canonical protocol tables) your
  projects, tasks and forecasts, in the clear. The single most sensitive file
  this project writes.
- **The vault** (`BROTHERMODE_VAULT`, default `~/BrotherModeVault`): session
  telemetry and captured corrections, both described above.
- **The consent config** (`~/.brotherme/config.json`): records that setup
  ran and where your vault lives. Not secret by itself, but every hook
  PROGRAM that writes YOUR CONTENT refuses to write anything until this
  file says you said yes, so it is the switch that gates your data leaving
  a session. Read "program" strictly: one hook line can run more than one
  program (PreCompact runs two), and each one carries its own check. The
  gated set is `bm_sessionstart.py`, `bm_autosave.py`, the Bash audit's
  two phases, all three hook-wired `bm_telemetry.py` commands
  (`outcomes-append`, `precompact-brief`, `stop-warn`), and
  `bm_lead.py watchdog`. A test reads `hooks/hooks.json` and fails if a
  wired command lacks the check, and since 2026-08-05 that test reads
  every module named on a hook line rather than `bm_telemetry.py` alone,
  which is the widening the incident at the end of this entry argues for.
  THE WATCHDOG, added 2026-08-05 and disclosed here because it ships ON BY
  DEFAULT: it is the half-hour catch-up, and it is a due check rather than
  a background process. Nothing schedules it and nothing runs between your
  turns; it runs on the Stop hook, once per model turn, alongside the
  telemetry warning already on that line. Its first statement reads the
  consent record, so before setup has been run it prints nothing and
  writes nothing at all, in the same one-door sense as the programs above.
  After consent it reads a few rows to ask whether a catch-up is due. When
  the answer is no, which is the ordinary case, it writes nothing and
  prints nothing. When the answer is yes it writes exactly one row into
  your own project store, the record of the catch-up you were shown, and
  prints that catch-up. It writes nothing into the vault, nothing outside
  your project, and it makes no network call.
  One narrow exception, named so this claim stays true: the fence hook
  does not check consent, and on first use it mints its own session token
  file (a machine-generated 64-hex value, no founder data) under
  `.brothermode/fence/`, because ownership proof has to exist before the
  hook can refuse anyone. It writes nothing else.
  HISTORY, dated because the claim above was FALSE until 2026-08-02: two
  of those `bm_telemetry.py` commands were ungated. `precompact-brief`
  wrote your last message verbatim into the vault, and `stop-warn` created
  the vault tree, both before anyone had said yes. Found by an independent
  adversarial review, reproduced in a throwaway home directory, fixed, and
  pinned by tests in `tools/test_bm_consent.py`. The sentence was wrong for
  the same reason it was easy to believe: the earlier fix gated the first
  program on a hook line and nobody checked the second.
- **Your Claude Code `settings.json`**: what `scripts/install.py` edits to
  wire the hooks. If an attacker could rewrite it, they could point a hook
  command anywhere.
- **Generated views** (`STATE.md`, `digest.md`, `inbox.md`, `outbox.md`, and
  every document `bm_docs.py` or `bm_project.py` renders): read-only
  reflections of the store, scrubbed at the point they are written, so they
  are lower sensitivity than the store itself but not zero.

**Trust boundaries** (who is on which side of the line):

- **Hooks run as you, the logged-in user, with your full filesystem
  permissions.** They are not sandboxed and do not run as a separate,
  lower-privileged account. Anything you could type at your own terminal, a
  hook you installed could also do.
- **Subagents and parallel Claude Code sessions share the same working
  tree.** BrotherMode's fence is a coordination discipline between
  cooperating sessions, not a permission boundary between a trusted and an
  untrusted one: every session that can reach the project directory can, in
  principle, reach every file in it.
- **The MCP server this project can expose is read-only.** It answers
  queries against the store; it has no path that writes, so a client
  talking to it cannot use it to mutate your project even if it wanted to.

**Attacks this design answers:**

- **A second Edit, Write, MultiEdit or NotebookEdit crossing a fence, and
  since L06 (2026-08-06) a Bash apply_patch envelope naming a fenced path.**
  Blocked, in front of the write, by `tools/bm_fence_hook.py` (a PreToolUse
  hook that can refuse the call before it happens; see docs/HOOKS.md) --
  CORRECTED 2026-08-01 (loop6 refuter finding A8a): that "Blocked" is not
  unconditional. The hook FAILS OPEN (lets the write through unchecked) on
  a missing, empty or corrupt store, or on any internal error, exactly as
  docs/KNOWN-LIMITS.md already states; treat this line as "blocked when the
  store is readable", not as an unqualified guarantee.
- **The same kind of cross-fence write, but through Bash.** The fence hook
  cannot see inside a shell command, so this one cannot be blocked (see
  "The Bash boundary" in docs/HOOKS.md for why gating Bash itself is not on
  the table). It is instead DETECTED, after the fact, by
  `tools/bm_bash_audit.py` (D-1, this loop): a PreToolUse/PostToolUse pair
  that snapshots every fenced path that resolves to a REAL, EXISTING FILE
  at the moment the Bash call starts (a claim on a directory or a
  glob-shaped path is not expanded into the files it would cover, so a new
  file created inside a claimed directory during the call is invisible to
  it; see docs/HOOKS.md's "What it cannot see") before a Bash call and
  re-hashes it after, raising a high-severity alert naming the path when a
  session that does not own the fence changed it. Detection, not
  prevention: the write already happened by the time the alert exists, and
  docs/HOOKS.md and docs/KNOWN-LIMITS.md say so rather than implying
  otherwise. Wired on BOTH install paths since 2026-08-01 (the Claude Code
  plugin manifest and `scripts/install.py`'s clone-install path alike; see
  docs/HOOKS.md's "Installing the Bash audit hook").
  EXTENDED 2026-08-03 (closure item C-02). Two things above were incomplete
  until that date. FIRST, a shell command that destroyed BrotherMode's OWN
  enforcement state produced no alert and no stderr line at all. The measured
  case was `rm -f .brothermode/store.sqlite3`: the store is not itself a
  claimed path, so nothing in the detection pair ever looked at it, and with
  the store gone a write the fence had just refused became an allow. That is
  now DETECTED on the same pair, in both modes and in every BrotherMode
  project. The pre phase records whether the store exists, is non-empty and
  still begins with the SQLite file header, and which session tokens exist;
  the post phase reports every one of those that was lost, on stderr and as a
  high-severity `fence-control-loss` alert row. Growth and ordinary mutation
  are ignored on purpose, because a shell call that runs `bm_store.py` is
  normal work. When the store itself is what went missing the row cannot be
  written, and the hook says exactly that instead of falling silent.
  SECOND, and only if you opt in with `BM_FENCE_MODE=enforced` AND the Bash
  call's cwd resolves to a BrotherMode project, that command is now REFUSED
  before it runs. The project check is load-bearing, not decoration: this
  hook installs at USER-GLOBAL scope
  (`~/.claude/settings.json`), so it runs on every Bash call in every Claude
  Code session on the machine, and an earlier draft that refused before
  resolving a project root would have refused this same command in every
  unrelated, non-BrotherMode directory too. Outside a BrotherMode project the
  refusal check is inert. THE DELIBERATE LIMIT THIS CREATES: when
  `tools/bm_store.py` cannot be imported at all, the project check itself
  cannot run, so nothing is refused, even under enforced mode, anywhere. That
  is a fail-open path inside a fail-closed feature, chosen on purpose,
  because the only alternative is refusing every Bash command in every
  directory on the machine, which is not shippable. Someone who can break
  that import can therefore disable the refusal. Read what the refusal is,
  and is not, before relying on it: it is a literal match, a small list of
  destructive shell forms combined with the literal names `.brothermode` and
  `store.sqlite3`, plus two forms that wipe a whole directory without naming
  anything (`git clean` with `-x`, and `rm -r` aimed at `.` or `*`). It is
  not a shell parser and will not become one here. A name assembled at
  runtime, held in a variable, or sitting inside a script file the hook never
  reads is NOT caught, and neither is any program that deletes the file
  without the name appearing in the command. It also over-refuses on purpose,
  inside a BrotherMode project: a read-only command that merely mentions the
  directory next to a redirection is refused too. What enforced mode adds
  beyond the refusal is the aftermath: if the store does go missing by a
  route the refusal misses, the fence hook in that same mode then DENIES
  rather than allowing (C-01), so the one-command bypass needs both halves to
  fail rather than one.
- **A secret leaking through an export.** `dump`, its JSON output, and the
  MCP server responses all pass through ONE withholding policy
  (`export_column` in `tools/bm_store.py`): founder-typed prose is withheld
  outright by default, secret-shaped substrings are redacted wherever a
  column is allowed to show text at all, and absolute filesystem paths are
  masked. Described in full in "What this software does with your data"
  above.
- **A stale or hand-edited manifest going unnoticed.** `scripts/doctor.py`
  check 9 self-checks the release against `CHECKSUMS.sha256`, so a file
  that was quietly modified after the checksums were cut is reported rather
  than trusted -- CORRECTED 2026-08-01 (loop6 refuter finding A8b): on a
  DIRTY working tree (ordinary uncommitted edits, not a checked-out
  release) this check SKIPs and reports nothing, because a checked-in
  manifest was never generated to describe a tree mid-edit; a SKIP is not a
  PASS, and the whole run still exits 0 on a SKIP unless `--strict` is
  passed, so check 9 catches tampering only against a clean, checked-out
  release, not against your own working copy while you edit it.

**Attacks this design explicitly does NOT answer**, each with the one
sentence that says why it is out of scope rather than merely unmentioned:

- **A malicious process already running with your own user privileges.**
  Nothing in this project can defend against that, because a hook, the
  store, and every file this project owns are themselves just more files
  that process could already read or overwrite; there is no privilege
  boundary between "this tool" and "anything else you are running" to
  defend across.
- **A shell command that writes or deletes files, in the general case.**
  Claude Code hands a `Bash` PreToolUse hook a command STRING, not the set of
  files that command will touch, and nothing inside "Python 3.9, standard
  library only" turns one into the other, so there is no honest way to gate
  shell writes the way Edit and Write are gated. What exists instead is
  stated above: after-the-fact detection for fenced files and for
  BrotherMode's own state, and, in enforced mode and inside a BrotherMode
  project only, a literal-match refusal for the obvious destructive forms
  aimed at that state. Real containment would need an operating-system write
  mediator (a sandbox profile, a container, a FUSE layer). That is out of
  scope for this project, deliberately and not for now, and
  docs/KNOWN-LIMITS.md carries the same statement.
- **A supply-chain compromise of Python or git themselves.** This project
  is standard-library Python plus one documented, local-only use of the
  `git` binary; if either of those two trusted programs were themselves
  compromised, every promise in this document is made by code running
  inside the compromised interpreter or shelling out to the compromised
  binary, so no check this project runs on itself could catch it.

## Scope note

This project governs how a Claude Code session behaves. It does not change what
Claude Code itself transmits to Anthropic or your chosen cloud provider. For
that, see Anthropic's own documentation on Claude Code data usage, and choose
your plan accordingly: commercial terms (Team, Enterprise, API, cloud providers)
differ materially from consumer plans.

## Verifying what you installed

CORRECTED 2026-08-11: this section used to say "this repository is unsigned and
has no releases". The first half is still true and the second half stopped
being true. Annotated tags exist (`git tag --list` shows the 3.x line), and
GitHub releases exist. The sentence is corrected here rather than quietly
edited, because a reader deciding how to pin a version was being told there was
nothing to pin to.

What is accurate today: releases and annotated tags exist, and none of them is
cryptographically signed. A tag proves which commit a release names; it proves
nothing about who created it. If your organization requires pinning, pin to a
tag or a commit and record the hash:

```bash
git -C ~/.claude/skills/brothermode rev-parse HEAD
```
