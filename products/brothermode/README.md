Product authority: [PRODUCT-DIRECTION.md](PRODUCT-DIRECTION.md) (founder
direction, 2026-08-11) defines what BrotherMode is, who it serves, and what it
will not build. It supersedes conflicting product-scope guidance elsewhere.

This page is for one person already running Claude Code who wants a plan
agreed before anything is touched, one writer per file, and a check quoted
before anything is called done, paid for with the cost of installing hooks and
state files. If what you want is a no-setup editor, a bounded background task
run from GitHub Issues, or several people coordinating on the same project
with roles, approvals, and merge control, look elsewhere: see
[docs/ECOSYSTEM.md](docs/ECOSYSTEM.md) for what fits each of those situations
better.

BrotherMode is built for one person doing serious work, not for coordinating
several people on the same project at once. That is a deliberate choice, not
an oversight: it scales down to one person doing the work of several roles,
and adding shared state, accounts, and multi-user merge control would cost the
single-writer discipline that the plan-then-verify guarantees above depend on.
Where a genuine multi-person need exists, a team coordination tool is the
honest answer, not this one, and the sibling product is that answer:
BrotherMode governs one person's session, BrotherSBE governs one change's
passage between people. A team runs both, one session each plus the sibling
between them, and
[docs/WORKING-WITH-BROTHERSBE.md](docs/WORKING-WITH-BROTHERSBE.md) is the
worked guide to using them together, including what team use does and does
not cover today.

Everything it shows you is a file you own. The page of where a project stands
is written as one self contained HTML file with nothing fetched from anywhere:
open it by double clicking, mail it, or paste it into your own wiki, with no
account and no network. Produce it whenever you want with

    python3 tools/bm_view.py render --project-id <your-project> --out page.html --actor-name "<you>"

A real page measures about 22 kilobytes, so it travels anywhere a document
travels. Nothing is hosted unless you choose to host it.

The handover procedure is published as a plain document you can run with
nothing installed: [docs/HANDOVER-BY-HAND.md](docs/HANDOVER-BY-HAND.md).
Using it costs you nothing and tells us nothing, and it is worth having
even if you never install anything here.

---

# BrotherMode

**A verified delivery layer for AI agents.**

The reliability layer for serious Claude Code work.

From intent to verified delivery.

Resume the work. Control the execution. Prove the result.

Describe the outcome. BrotherMode keeps the decisions, the ownership of every
file, and the evidence intact from the first sentence to the delivery packet.

The long-term vision is an autonomous product team. That is stated here as the
direction, not as the claim: what is proven today is the memory, the
guardrails and the proof, each with its evidence in the register below. The
founder chose a narrower headline on 2026-08-07, over keeping the broader one,
precisely because three independent reviews said the broader one ran ahead of
the evidence. The four lines above are the 2026-08-11 wording of that same
narrower claim, and they are copy rather than a new capability: every
capability they gesture at already has its own row and its own evidence in the
register below, and none of them was added to say this.

## Install

Two lines, inside Claude Code. Full detail, the clone alternative and the
uninstall path are in [Install and preflight](#install-and-preflight) further
down.

```bash
claude plugin marketplace add khalilmaaouni/BrotherModeUp@v3.4.2
claude plugin install brothermode@brothermode-marketplace
```

## Who this is for

**A technical solo founder, a senior solo builder, or a maintainer using AI
agents on serious multi-session work.** In plain terms: you are accountable
for the final result, you already use Claude Code or another serious coding
agent, your work spans many files and more than one sitting, you sometimes run
more than one agent at once, and you have already been burned by context loss,
stale evidence, rework, or two workers editing the same file. You know Git and
a terminal, you would rather have dependable delivery than the fastest first
draft, and you want the records to live on your own disk where you can read
them.

The job it exists to do, in the founder's own words: let me delegate serious
work to AI without repeatedly explaining the project, losing decisions,
colliding with another worker, or accepting a false claim of completion.

**Who it is NOT for**, stated plainly because disqualifying the wrong user is
a product strength rather than a marketing failure: anyone who wants an
instant website or app with no code; anyone making a one-line reversible edit;
anyone unwilling to use Git or a terminal; a team wanting shared accounts,
role-based access, approvals and enterprise project management; a buyer
shopping for the best model, editor or cloud coding runtime; anyone wanting
GitHub-native background delegation with no local environment; anyone
expecting full operating-system containment; anyone expecting autonomous
production deployment with no human gate; and a team that only needs a
planning methodology or a test-driven workflow.

## Which runtimes this actually works on

**Claude Code is the one verified runtime.** Everything this page promises was
measured there.

BrotherMode also ships instruction files for Generic AGENTS.md, OpenAI Codex
CLI, GitHub Copilot, Google Antigravity, Qwen Code, iFlow CLI, Cursor and
Gemini CLI. Read [docs/RUNTIMES.md](docs/RUNTIMES.md) before relying on any of
them, and take that table's own words rather than a stronger summary of them.
Two things it says that matter more than the rest: every non-Claude answer in
its BrotherMode-hooks column is UNVERIFIED except one, and UNVERIFIED there
means do not wire it, because a fence that fails open while looking installed
is worse than no fence at all. The one measured exception is OpenAI Codex CLI,
and what was measured is a NO rather than a yes.

BrotherMode is a Claude Code plugin: a written set of working rules plus a
small toolchain of Python and shell scripts. You install it once, and from then
on a session behaves like a colleague rather than an assistant waiting for
instructions. It agrees the shape of the work with you before touching
anything, keeps one writer per file across the write tools it can see so two
parallel efforts cannot silently overwrite
each other, refuses the word done until a check has run after the last change,
and writes the decisions and the progress to plain files so a crash or a
restart does not lose the thread.

Receipts is the word to hold this page to. Every claim below names the file or
the command behind it, the capability table further down is generated from a
register in this repository rather than typed here, and `tools/test_bm_docs.py`
fails when a page and the tree disagree.

## The public surface: six names, and what each one really does today

The documented surface is six names. It used to be seven. Two of the six are
named but not yet shipped, and they are marked as such rather than listed as
peers, because a name that does nothing teaches you that this page cannot be
trusted.

| Name | Status today |
|---|---|
| `start` | Works |
| `status` | Works |
| `deliver` | Works |
| `doctor` | Works |
| `verify` | A NAME FOR A FLOW, NOT AN ENTRY POINT. Settled by ruling on 2026-08-11: verify stays a routing name over the review and deliver flow that already exists, with no folder of its own, so the canonical skill count stays at nine and ruling B5 of [docs/decisions/V3-FREEZE-2026-08-07.md](docs/decisions/V3-FREEZE-2026-08-07.md) is untouched. Ask for a review and you reach exactly the flow this name describes. There is no `/brothermode:verify` command and there will not be one. |
| `toolkit` | NOT BUILT. It arrives with the Toolkit release. No stub ships, no placeholder folder, no entry point that fails quietly. |

**Nothing was removed or deprecated.** `next`, `review`, `view` and `help`
keep working exactly as they do today, along with `auto`, `auto-status`,
`brief`, `decisions`, `handback`, `handover-pack`, `stop` and `update`. They
are advanced internal surface now, which means they are off the public list
and nothing else. `review` is the engine `verify` routes to. The fifteen files
under `commands/` are unchanged and still work; each now carries a one-line
notice saying its documented status changed and its behaviour did not.
Consolidating them physically is a later tranche.

The documented surface shrank from seven names to six. The working surface
shrank by nothing.

## One minute with it

Real commands, described rather than transcribed. There is no screenshot on
this page standing in for a session you have not had yet, and the wording your
session uses will be its own. Type these into Claude Code once it is installed.

1. `/brothermode:start I run a bakery and want a one-page site: our story,
   photos, opening hours, and cake pre-orders.`
   It sizes up the goal and asks only the questions whose answers change the
   scope, one decision at a time, recommended option first. It ends with one
   written project brief (`CANVAS.md`, generated from the project's own
   records) and a first decision to make, with a time and cost range given as
   a range rather than a promise.
2. `/brothermode:status` prints where the project stands, in plain language,
   read out of those records rather than out of the conversation.
3. `/brothermode:next` recommends the single next step, and says why that one.
4. Work happens. A file one session has CLAIMED is refused to a second session
   by a hook, not by a reminder in a prompt. Stated exactly, because the
   difference is the whole guarantee: the refusal covers claimed paths written
   through the editing tools. By default an UNCLAIMED path is allowed, and a
   write made by a shell command crosses a fence unrefused and is caught only
   afterwards by the audit. Set `BM_FENCE_MODE=enforced` and
   `BM_FENCE_STRICT=1` to require a claim before any project edit.
   `docs/KNOWN-LIMITS.md` states what the hook misses.
5. `/brothermode:review` checks the work against the written definition of done
   and reports what passes and what does not, bad news first.
6. `/brothermode:deliver` writes the delivery packet: what was built, every check
   that ran after the last change, and what was left out on purpose. If a check
   is missing or failing it says so instead of delivering.

Nothing in that sequence asks you to read a log. `/brothermode:help` explains any
of it again in plain language.

## What you get out of it

Three outcomes, each one grounded in a capability the register below marks
certified, with its own honest gap named beside it.

**A guided project that ends in a packet you can hand to someone.** The guided
flow (`skills/brotherme/SKILL.md`, entered by `commands/brotherme-start.md`)
takes you from a sentence to a delivery packet built from the project's own
records by `python3 tools/bm_project.py deliver`. The packet's own exit rule,
in `project-template/DELIVERY-PACKET.md`, forbids the words ready to deliver
unless every acceptance check has evidence produced after the final edit. The
gap: the packet proves what was checked, not that the checks were the right
ones. That judgement stays yours.

**A killed session you can pick up instead of restarting.** Decisions, open
work and next intent live in a durable local store (`tools/bm_store.py`), and
right before Claude Code compacts its context, a hook snapshots your whole
working tree, untracked files included, into a private local git reference
(`tools/bm_autosave.py`, never pushed, `recover` prints how to restore it).
`tools/test_bm_store.py` exercises the recovery path, and the store job in
`.github/workflows/tests.yml` runs that suite on three operating systems. The
gap: a snapshot is a rescue, not a backup service. It lives in your own `.git`
directory and goes when that goes.

**A scorecard built from your own corrections rather than from self-report.**
A hook, not the model, records what each session actually cost in tokens, tool
calls and time (`tools/bm_telemetry.py`, recorded only after you consent, which
`tools/test_bm_consent.py` enforces). Corrections you make can be captured,
approved into a rule by a human-confirmed receipt, retrieved with the reason
shown, and graded on whether the correction was applied before the same
situation came round again
(`tools/bm_learn.py`, walked through in `docs/CORRECTION-LEARNING.md`). The
gap, and it is the largest one on this page: this has been used for real but
never MEASURED on a real project. There is no counted project, no recorded
rework rate and no comparison against working without it
(`docs/KNOWN-LIMITS.md`, "Used for real, but never MEASURED on a real
project"; `docs/NOT-FINALIZED.md` item 1).

## What is certified, and what is not

The section below is GENERATED. It is rendered from `capabilities.status.json`
by `python3 tools/bm_docs.py capability-status --write`, and `tools/test_bm_docs.py`
fails if the block here and the register disagree, so a claim cannot quietly
drift out of the page it was written on. To change what this says, change the
register and re-run the command.

<!-- BEGIN GENERATED CAPABILITY STATUS -->
<!-- Generated from capabilities.status.json by `bm-docs capability-status --write` (the packaged console script; from a clone, tools/bm_docs.py). Edit the register, not this block. -->

Four states and no others, read out of `capabilities.status.json`, updated 2026-08-10: certified means proven in this tree today by the evidence named; beta means real, with a named gap; experimental means built or planned, not measured; unsupported means not offered, and no plan makes it offered.

**Certified**, proven in this tree today by the evidence named.

| Capability | What proves it, or why it is not offered |
|---|---|
| Durable local store that survives a crash and can be recovered | tools/bm_store.py holds the state and tools/test_bm_store.py exercises recovery; the store job in .github/workflows/tests.yml runs that suite on Linux, macOS and Windows. |
| Current pages are held to the facts read out of the tree | tools/test_bm_docs.py refuses a current page carrying a stale count, a stale version, or a dated record that declares no status; docs/ba/QA-GATES.md states the gates. |
| Guided beginner flow on Claude Code | skills/brotherme/SKILL.md drives the flow, commands/brotherme-start.md is its entry point, and docs/QUICKSTART.md is the install path a beginner follows. |
| Continuous integration on macOS and Linux | .github/workflows/tests.yml runs the suite job on ubuntu-latest and macos-latest at Python 3.9 and 3.x, with fail-fast disabled so one platform cannot erase another's result. |
| Session telemetry recorded only after the user consents | scripts/setup.py writes the consent record, tools/bm_telemetry.py is the only writer, and tools/test_bm_consent.py refuses a write without consent. |
| Two-command plugin install through Claude Code's own plugin manager | scripts/release-smoke-install.sh proves the whole path on every release inside a throwaway configuration: marketplace add, install, installed version matched against VERSION, every hook group registered, uninstall leaving settings clean; first PASSED run 2026-08-07 on the release candidate tree, with a live sandboxed end to end run the same night. claude plugin validate passes both manifests. SURFACE LIMIT, founder-reproduced 2026-08-06: the desktop app cannot run /plugin itself; the two commands run once in a terminal and the app consumes the installed plugin. The plugin line tracks the repository's default branch by the plugin system's design; the tagged clone remains the immutable option and docs/RELEASE.md states both. |

**Beta**, real, with a named gap.

| Capability | What proves it, or why it is not offered |
|---|---|
| Single writer per file for supported write tools, refused by a hook; other writes detected, not contained | Conflicting writes are refused for the Claude Code write tools (Edit, Write, MultiEdit, NotebookEdit) and readable apply_patch envelopes on the Bash leg, wired by hooks/hooks.json and proven by tools/test_bm_fence_hook.py. Other shell and external writes are detected where possible by tools/bm_bash_audit.py but are NOT contained. Hooks are cooperative enforcement: no container or operating system sandbox is provided. MEASURED 2026-08-07 on OpenAI Codex CLI 0.146.0: the fence does NOT fire in the codex exec path. A live run overwrote a file another session had claimed, twice, and a marker probe proved the PreToolUse hook never executed, with config syntax, project trust and hook-trust bypass all ruled out (docs/mistakes/M19-the-codex-fence-does-not-fire-in-exec-mode.md). Under Codex, BrotherMode is an instruction file plus a working command line, not an enforcement layer. Downgraded to beta: proven in this tree with tools/test_bm_fence_hook.py, but not proven on an installed plugin because the install writes no INSTALLED-FROM stamp and the detector scripts/doctor.py check_install_identity can only return SKIP. Since 2026-08-17 the same hook also refuses a write to any git-tracked file while a live tools/test_all.py gate lock covers the checkout (the battery fence), for every session including the one that started the gate; a lock it cannot read is reported and, in advisory mode, never blocks. The battery classes in tools/test_bm_fence_hook.py prove it in this tree, under the same beta caveat as the rest of this row. ENFORCED MODE DRIVEN 2026-09-02 for the first time, by ../../scripts/fence_enforced_drill.py: 20 conditions, each fed to the hook twice, advisory against enforced, 20 PASS and 0 NO-DATA. Fifteen conditions that fail OPEN with the mode unset fail CLOSED with BM_FENCE_MODE=enforced, and a properly claimed write still ALLOWS under enforced, so enforcement is selective rather than blanket. The drill was itself driven backwards against a mutant copy of the hook whose enforced_mode() returns False, which produced 15 FAIL at exit 1. ONE GAP FOUND AND NOT FIXED, recorded here rather than left in a transcript: a payload whose tool_name is not a string leaves decide() at the not-a-write-tool branch BEFORE any mode check, so enforced mode cannot refuse it. That is one of the five shapes of malformed payload this row's C-01 note claims enforcement covers, so the claim is one shape wider than the code until that branch moves. |
| Windows | Only the store job in .github/workflows/tests.yml runs on windows-latest; the suite and gate jobs run on Linux and macOS only. docs/KNOWN-LIMITS.md records that the installer refuses Windows and that WSL works. There is no native Windows install lifecycle. |
| The signed authorisation an autonomous session has to work inside | tools/bm_autonomy.py is the command line, tools/test_bm_autonomy.py is its suite, and the store job in .github/workflows/tests.yml runs that suite on Linux, macOS and Windows; docs/AUTONOMY.md is the page. It stays beta because docs/KNOWN-LIMITS.md records open items against this layer and no use outside this project is recorded. |
| The durable controller that carries a signed outcome to a checked deliverable | tools/bm_controller.py is the engine and its command line, tools/test_bm_controller.py is its suite including an end to end run that is killed and resumed (its transcript is docs/program/absolute-lead/evidence/L03/E4-endtoend.json), the store job in .github/workflows/tests.yml runs that suite on Linux, macOS and Windows, and docs/FULL-AUTO.md is the page. Not experimental, because experimental here means not measured and this is measured. It stays beta because docs/KNOWN-LIMITS.md carries its own list of what the controller does not yet do, and no pilot outside this project exists. |
| A record of what was decided and why, and the short catch-up built from it | tools/bm_lead.py records and renders them over two append only tables in tools/bm_store.py, tools/test_bm_lead.py is its suite, and docs/program/absolute-lead/DESIGN-insight-ledger-and-handback.md states what a record has to carry before it may be written. It stays beta because the record holds the coordinator's judgement rather than a measurement, because no continuous integration run covers it yet, and because docs/KNOWN-LIMITS.md names what it does not do. |
| Handing a decision and the work under it back to the person who owns it | Offered on every key decision and enforced by a refusal in tools/bm_store.py rather than by a rendering convention: a key decision that offers no handback cannot be recorded at all. tools/bm_lead.py performs the handback and writes the page a developer picks the work up from, and tools/test_bm_lead.py drives both, including the refusal of a second handback on one decision. It stays beta because no continuous integration run covers it yet and no handback outside this project is recorded. |
| A half hour catch-up that arrives on its own, on by default after setup | hooks/hooks.json wires it to the Stop hook as a due check rather than a background process, tools/test_bm_consent.py drives every hook wired command against a fresh home directory and fails if any of them writes before consent (the suite job in .github/workflows/tests.yml runs that suite), and SECURITY.md discloses that it ships on by default, what it writes when a catch-up is due, and that it writes nothing when it is not. It stays beta because it cannot fire inside a turn that never ends and because its activity ceiling is a chosen constant, both recorded in docs/KNOWN-LIMITS.md. |
| Handover pages an analyst or a project lead can take a project over from | tools/bm_lead.py generates them from rows and tools/test_bm_lead.py checks both directions: every row the store says belongs on a page reaches that page, and every claim on a page resolves back to the row it came from. It stays beta because no continuous integration run covers it yet and because nobody outside this project has read a pack, which is the empty rung docs/ROADMAP.md section 1 describes. |
| One page showing where a project stands, generated from that project's own records | PROVEN: tools/bm_view.py writes the page as one self contained file from the records, through the collectors in tools/bm_lead.py rather than a second reading of its own, tools/bm_visual.py draws it, and tools/test_bm_view.py and tools/test_bm_visual.py check the structure instead of the pixels (a drawn node for each row, a label matching the row it came from, no address outside the file, one file, exactly one recommended next action). NOT PROVEN, and it is a separate half: publishing that file as a private page needs a paid plan, a signed in session and four further conditions listed in docs/KNOWN-LIMITS.md, so what the product promises is the file on disk and the published page is an addition that can be unavailable. OPEN: nobody outside this project has opened either one. |
| A scripted first fifteen minutes with three commands and something to look at at each step | PROVEN: commands/brotherme-start.md carries the opening block that writes nothing before consent and the first page after it, commands/brotherme-help.md asks one question instead of listing every command, and tools/test_bm_view.py drives the path from an empty folder and fails if a fourth command is offered before the first piece of work completes, if anything is written before consent, or if a section with no rows renders blank instead of the short note tools/bm_view.py holds for it. OPEN, and this is the whole gap: fifteen minutes is a target, no first run by a person who has never used this has been measured, and the checks are structural rather than behavioural. |
| Four levels of alert where exactly one interrupts, computed from the records rather than stored | PROVEN: tools/bm_visual.py computes the levels as one function over rows with no table behind them, so a condition that clears takes its alert with it and nothing has to be dismissed, and tools/test_bm_visual.py holds the four anti noise rules to that (at most one interrupting alert on screen, at most two levels in any one message, no promotion by age, one interrupt per cause per catch-up window). NOT PROVEN: that the ladder keeps a reader engaged, which is a claim about a person rather than about code. OPEN: hooks/hooks.json runs the check when a session stops, so it cannot fire inside a turn that never ends, which is the limit docs/KNOWN-LIMITS.md already records for the half hour catch-up. |
| The offer to take a decision and the work under it back, on screen whether or not a decision is open | PROVEN: tools/bm_view.py renders the standing panel on every page, its wording comes byte for byte from tools/bm_lead.py rather than being retyped, tools/test_bm_view.py fails a page that drops it and fails a drawn decision whose last branch is not the handback, and tools/bm_store.py already refuses to record a key decision that offers no handback at all. NOT PROVEN, and by design: nothing on the page can act on the project, the control copies a prompt the reader pastes back into the session, and docs/KNOWN-LIMITS.md states that as a limit rather than dressing it up. OPEN: no handback by anyone outside this project is recorded. |
| What the hooks cost per action, measured on a stated machine, with the parts nobody measured named as unmeasured | MEASURED: tools/bm_hookbench.py reads which programs fire at which event from hooks/hooks.json, feeds each one the payload shape docs/HOOKS.md documents, and times it against a store built for the run inside a temporary directory with HOME and every BrotherMode variable pinned there. It reports, per user action, the cost of each program AND the cost of the whole chain (the four Stop programs share one budget, so those are two different numbers), each as a median with its spread over a stated number of repetitions, plus the machine and interpreter the run was taken on, and the exit codes and fail open lines the run actually produced. docs/PERFORMANCE.md is generated from that run and carries the record it was rendered from; tools/test_bm_hookbench.py re-renders the page from that record and fails on one differing byte, so a number cannot be typed onto the page by hand, and it also refuses a sandbox whose fence fails open rather than reporting the cost of a hook that checked nothing. NOT MEASURED, named on the page rather than estimated: the fork, the exec and the interpreter bootstrap every hook process pays before any code of this project runs, which is why every published total is a LOWER BOUND (the tool runs the programs in process because tools/test_bm.py bans import subprocess in shipping modules and its allowlist does not name this one); the SessionStart hook, which is a shell script; store lock contention frequency in real use; hook failure frequency in the field; and the share of real sessions hitting a warning or a false refusal, which needs telemetry this project deliberately does not collect. OPEN: the numbers are one machine, one operating system and one Python version, and nothing here says what they are on anyone else's. |

**Experimental**, built or planned, not measured.

| Capability | What proves it, or why it is not offered |
|---|---|
| Delivering a web build through the same guided flow | not measured |
| Deployment previews attached to a delivery | not measured |
| Benchmark harness comparing runs | docs/BENCHMARK.md describes the method and docs/BENCHMARK-V1-V2-RC2.md records a dated run. No run against the current tree is recorded, so no current number is claimed. |
| Cursor compatibility mode: independent install, manage, uninstall, and a local Fable-to-Cursor harness | scripts/install_cursor.py and scripts/uninstall_cursor.py own the Cursor lifecycle; tools/bm_cursor.py is the manage and mailbox harness CLI; tools/bm_cursor_hook.py adapts Cursor hook payloads to the Claude fence contract; tools/test_bm_cursor.py covers adapter translation, install ownership, and dispatch-claim-record-adopt. docs/CURSOR-COMPAT.md states the honest limit: fence enforcement under Cursor is ADVISORY until a live Agent canary is recorded. No live Cursor Agent or Cloud Agent canary is in this tree yet. |

**Unsupported**, not offered, and no plan makes it offered.

| Capability | What proves it, or why it is not offered |
|---|---|
| Publishing to production on its own | Not offered. Cutting and publishing a release is a founder-gated sequence of steps in docs/RELEASE.md, and the suite skips the release checks until a human has cut the tag. |
| Spending money on the user's behalf | Not offered. SECURITY.md states there is no account and no server, and that the only outbound call is a version check the user invokes by hand. |
| Legal or security certification of any kind | not measured |
| A guaranteed native mobile result | not measured |
| Replacing a human specialist review | not measured |
| Multi-user or enterprise project management | Not offered. README.md states there is no shared server, no account system and no multi-user coordination layer, and that running this as a control plane for several people is not what it is for. |
| Changing its own safety rules | not measured |

<!-- END GENERATED CAPABILITY STATUS -->

## How it works: guide, execute, verify, land, record

Five steps, and each one names the machinery that holds it up rather than the
intention behind it.

**Guide.** The session maps what is already true (git status, what else is
mid-flight) before writing anything, sizes the work, and brings decisions to
you one at a time with a recommended option first and the tradeoff in plain
words. `references/kickoff.md` is the written procedure; `SKILL.md` is the law
it belongs to.

**Execute.** Work is claimed before it is written, and the claim is enforced:
`tools/bm_fence_hook.py` runs on the PreToolUse hook and REFUSES a write to a
file another live session owns, whether it arrives as an Edit tool call or,
since L06, as a Bash apply_patch envelope naming a fenced path. It is the one
hook that can say no.
`tools/test_bm_fence_hook.py` holds that behaviour in place, and
`scripts/doctor.py` proves the wired fence is live on your machine by
simulating a blocked foreign write in a throwaway project.

**Verify.** Done means a check that ran after the last change and passed.
Evidence gathered before the final edit proves nothing about what is being
delivered, which is why `commands/brotherme-review.md` reports what passes and
what does not instead of summarizing.

**Land.** The delivery packet is generated from the records, never filled in
by hand, so what it claims and what the store holds cannot differ.

**Record.** Session cost, decisions and corrections are written down
mechanically: a model reporting on its own performance is not evidence. Your
memory lives in a vault folder on your own disk, outside this repository.

## Install and preflight

Two ways in. The plugin way is two lines and no folders; the pinned clone is
the path that has been proven end to end the most times. The full,
copy-pasteable, ten-minute walkthrough with the expected output of every
command is [`docs/QUICKSTART.md`](docs/QUICKSTART.md). With no interactive
Claude Code session to type `/plugin` commands into, for example a script
installing this unattended, use the pinned clone below rather than the plugin
way.

Requirements: Claude Code (CLI or desktop app) with skills enabled, Python 3.9
or newer using the standard library only with nothing to install, and git.

**The plugin way (two lines, inside Claude Code).** This repository is its own
plugin marketplace: add it once, install from it, and the six hooks register
on the next start. Upgrading later is one `/plugin` update from the same
source; uninstalling removes the plugin and leaves your project data and
vault untouched.

```bash
claude plugin marketplace add khalilmaaouni/BrotherModeUp@v3.4.2
claude plugin install brothermode@brothermode-marketplace
```

Already running v2? Uninstall it first (`claude plugin uninstall brotherme`). The plugin identity changed at v3.0.0, so the old and new
ids are different plugins to Claude Code and installing both leaves two
hook chains wired at once.

The `@v3.3.1` pins the marketplace add itself to the released tag rather
than the repository's moving default branch. Anthropic's plugin
marketplace format resolves an `owner/repo@ref` source to that exact
branch or tag on every add and every later update, per the CLI reference
for `claude plugin marketplace add` at
https://code.claude.com/docs/en/plugin-marketplaces. `docs/RELEASE.md`
step 2b makes re-pinning this line to the newly cut tag an explicit
release step, and `tools/test_bm_docs.py` fails this page if the pin ever
disagrees with `install_target_tag` (`python3 tools/bm_project_facts.py
--field install_target_tag`).

Those are plain shell commands: paste them into any terminal once. Inside
the terminal client, the interactive `/plugin marketplace add ...` and
`/plugin install ...` forms do the same thing.

The word "install" covers three different things here, and vendor
documentation draws the line between them differently than an earlier
version of this page did:

- **Adding this repository as a marketplace, the first time.** Vendor
  documentation shows this done from `/plugin marketplace add` or the
  `claude plugin marketplace add` shell command above, both run from a
  terminal-backed Claude Code surface (see "Add marketplaces" at
  https://code.claude.com/docs/en/discover-plugins); it does not document
  a desktop-app GUI path for registering a brand new marketplace source.
  That is the one step this page asks you to run in a terminal, once.
- **Installing a plugin from a marketplace already configured, inside the
  desktop app, no terminal needed.** Once the marketplace above has been
  added, the desktop app's own **+** button next to the prompt box, then
  **Plugins**, then **Add plugin**, opens a plugin browser over your
  configured marketplaces, official and third party alike, and installs
  from there without a terminal (see "Install plugins" at
  https://code.claude.com/docs/en/desktop). This project's own proof,
  `scripts/release-smoke-install.sh`, exercises the terminal path only;
  the desktop browser is a vendor-documented path this project has not
  separately verified.
- **Installing through the terminal client.** The two lines above, or the
  interactive `/plugin` panel, which vendor documentation describes as
  opening in the terminal CLI, not the desktop app (see
  https://code.claude.com/docs/en/discover-plugins). Founder-reproduced
  2026-08-06 against the desktop app directly: the `/plugin` slash form
  itself does not run there, which is the narrow, still-true fact behind
  the older wording on this page; it never meant the desktop app cannot
  install plugins at all.

Proven, not promised: `scripts/release-smoke-install.sh` runs the terminal
path end to end inside a throwaway configuration on every release:
marketplace add, install, the installed version matched against `VERSION`,
all hook groups registered, then a clean uninstall that leaves settings
untouched. A release whose smoke run does not print PASSED does not ship
(docs/RELEASE.md names the step).

**Updating.** Type `/brothermode:update` and it walks you through it, or run the
two lines it wraps yourself: `/plugin marketplace update brothermode-marketplace`,
then `/plugin update brothermode`. Updating never touches your projects or your
records.

**The pinned clone (the most-proven path).** This clones an immutable, tagged
release, not a moving branch, because code that runs automatically on every
future session should come from a fixed, checkable snapshot; a moving branch
feeding auto-run code was the weakest link the original external audit named.

```bash
git clone --branch v3.4.2 --depth 1 https://github.com/khalilmaaouni/BrotherModeUp.git ~/.claude/skills/brothermode
```

That tag is not typed by hand: it is generated from the same release fact every
other page reads (`python3 tools/bm_project_facts.py --field
install_target_tag`), the last tag actually cut and known to resolve, and
`tools/test_bm_docs.py` fails if this page ever disagrees with it. The tree on
the default branch carries a development identity of its own, which is not the
public install target; `docs/RELEASE.md` explains why the two can differ on
purpose, and `python3 tools/bm_project_facts.py --field version` prints what
this checkout claims. Do not run both paths at once on one machine: the plugin
wires the same six hooks the clone's installer wires, so a machine carrying
both runs every hook twice (docs/KNOWN-LIMITS.md records this; pick one).

What that tag carries, read out of the tag rather than remembered: both
surfaces at once. `git ls-tree --name-only v3.0.0 skills/` lists all
seventeen `skills/<name>/` directories, the nine this page describes among
them (`start`, `status`, `next`, `review`, `deliver`, `view`, `help`,
`doctor`, `update`), and `git show v3.0.0:skills/start/SKILL.md` prints that
skill's own front matter at the tag. `git show
v3.0.0:.claude-plugin/plugin.json` prints the post-rename plugin id
`brothermode`, the same dated fact as the v2 uninstall note in the plugin
way above. The flat `commands/brotherme-*.md` files ship at that tag too,
kept on purpose so a v2 install or a v2 habit still resolves during the
migration window: `git ls-tree --name-only v3.0.0 commands/` lists them, and
`git show v3.0.0:commands/brotherme-start.md` opens with a LEGACY v2
COMPATIBILITY SHIM banner naming `/brothermode:start` as its replacement.
An earlier version of this paragraph said the tag carried only the old flat
surface and the single `skills/brotherme/SKILL.md` conductor; that was
wrong, and the `git ls-tree` line above is the command that disproves it.
The engine underneath (`tools/bm_*.py`, `scripts/install.py`,
`scripts/doctor.py`) is the same at the tag and on the default branch. If
you want the moving tree rather than the fixed snapshot, use the development
clone below instead.

**Bitbucket.** The engine speaks plain git and never calls a host API, so a
project whose remote lives on Bitbucket Cloud gets the same fences, gates and
ceremony as one on GitHub; that is a standing rule (the two-host law in this
repository's CLAUDE.md), with GitHub as the canonical home and the target
stated in PRODUCT-DIRECTION.md: individual contributors and 2 to 15 person
technical teams using GitHub or Bitbucket. `docs/BITBUCKET.md` carries the
full picture: install from a mirror (Claude Code's marketplace command
accepts full git URLs), the pull request flow through Bitbucket's REST API
now that app passwords are gone, `bitbucket-pipelines.yml` beside
`.github/workflows/` honest about its Linux-only runners, and an explicit
list of what has not yet been executed against a live mirror, labeled
UNVERIFIED on the page rather than implied. An Azure Repos page exists in
docs/ as a dormant record of executed checks; it is not a target.

Working on BrotherMode's own code, rather than just using it? Use the separate
development command instead, which tracks the moving `main` branch on purpose
and installs into its own directory so the two can never be confused:

```bash
# Development branch (changes over time)
git clone --branch main https://github.com/khalilmaaouni/BrotherModeUp.git ~/.claude/skills/brothermode-dev
```

**Preflight.** Then follow `docs/QUICKSTART.md` (or the longer reference,
`docs/SETUP.md`) to run the gate, wire the hooks (one installer command does
it), and point a vault folder somewhere on your disk. `python3
scripts/doctor.py` runs ten environment checks with plain-language remediation,
including whether this install's pinned public tag has since been moved on
the public origin: [`docs/PUBLIC-RELEASE-CHECK-2026-08-18.md`](docs/PUBLIC-RELEASE-CHECK-2026-08-18.md).
Invoke with `/brothermode` at the start of a sizable task.

Which release this is, and how many hooks get wired, are not typed by hand on
this page. Both come out of the tree:

```bash
python3 tools/bm_project_facts.py
```

It prints the current version and release tag, the storage schema version, the
hook events the installer writes (`SessionStart`, `SessionEnd`, `Stop`,
`PreCompact`, `PreToolUse`, which is the fence that can refuse a write, and
`PostToolUse`, which reports a shell write that crossed a fence), the suite
files the gate runs, and the Python floor. What it deliberately does not print
is a test count, for the reason given under "Evidence" below.

### Uninstall

Two different things get removed: the skill itself, and whatever it wrote
inside each project you used it in. Doing only the first leaves real files,
including the one file `SECURITY.md` calls sensitive, behind.

**The skill.** Unwire the hooks first, while the files are still there: the
installer's counterpart removes only the entries it wrote, and leaves your own
hooks and your vault alone.

```bash
python3 ~/.claude/skills/brothermode/scripts/uninstall.py
rm -rf ~/.claude/skills/brothermode
```

By hand instead, remove from `~/.claude/settings.json` every entry whose command
names this installation's own `tools/bm_*` files, across every hook event this
project wires (`python3 tools/bm_project_facts.py --field hook_events` lists
them; `docs/SETUP.md` explains what each one does).

**Per project.** Measured 2026-07-26 by actually installing, using, and then
removing this skill in a scratch project: for every project where you ran
it, it leaves behind

- `.brothermode/store.sqlite3` (plus `-wal` and `-shm` sidecar files while a
  session is open). This is the file `SECURITY.md` calls the raw sensitive
  artifact: your objectives, decisions, and directives as you typed them,
  before redaction.
- `threads/`, including `threads/thread-mode.json` and a
  `threads/<name>-<id>/` folder (`STATE.md`, `inbox.md`, `outbox.md`,
  `digest.md`) for every thread you ever started, completed or not.
- `STATE.md` at your project root, plus one `STATE.md.bak-<timestamp>` file
  for every time it was regenerated (it is backed up before every rewrite,
  by design, so these accumulate).
- Local git refs under `refs/brothermode/autosave/...`, written by the
  PreCompact hook. These live inside `.git` and are not touched by deleting
  any of the files above.
- Three lines in that project's `.git/info/exclude` (`.brothermode/`,
  `threads/`, `STATE.md`), added by `bm_store.py init` so none of the above
  is committed by accident. Harmless to leave, but they are this project's
  lines, not git's own.

To remove all of it, run this from the project root (run `git status` first
if you want to see what is there before it goes; none of this touches your
own tracked files):

```bash
git for-each-ref --format='%(refname)' refs/brothermode | \
  while read -r ref; do git update-ref -d "$ref"; done
rm -rf .brothermode threads STATE.md STATE.md.bak-*
grep -vxE '\.brothermode/|threads/|STATE\.md' .git/info/exclude \
  > .git/info/exclude.tmp && mv .git/info/exclude.tmp .git/info/exclude
```

Verified 2026-07-26 in a scratch project: after those three commands plus
deleting the skill folder, `git status` reports a clean working tree with no
BrotherMode trace, and `git for-each-ref` shows no `refs/brothermode/*`
entries left.

Your vault (default `~/BrotherModeVault`) is a separate, ordinary folder: none
of the above touches it, and it is yours to keep or delete on its own, per
project or entirely.

## Evidence: gates, receipts, and the docs suite

**The gate.** To check that the tools do what they claim mechanically (secret
redaction, owner-only file permissions on POSIX, no silent overwrite between
two writers), run it yourself rather than trusting this page:

```bash
python3 tools/test_all.py
```

Expect it to end `ALL GREEN` and exit 0. It runs every suite serially, in its
own process each, and is the command this project actually gates on. It takes
several minutes; that is the real cost of the isolation, not a hang. Individual
suites still run on their own (`python3 tools/test_bm_store.py` and so on) when
you are working on one of them; a single suite passing is not the gate.

**No test count is quoted on this page, on purpose.** Counts move with every
test that lands, and a reader who sees a mismatch cannot tell a stale page from
a broken install, which is exactly backwards. If you want the suite list rather
than the count, `python3 tools/bm_project_facts.py` prints it from
`tools/test_all.py` itself. Exact counts, tied to the date and the commit they
were true of, live in `CHANGELOG.md` and in the dated evidence files under
`docs/`.

**Receipts.** Approving a correction into a rule is one-time and
receipt-gated: no part of this system, automatic capture included, can approve
or promote its own candidate. The receipt proves an answer was supplied for
this exact proposed rule and has not already been used; it does not
cryptographically prove which human supplied the answer.

**The docs suite.** `tools/test_bm_docs.py` is why this page can be trusted
about itself. It refuses an active page that pins a test count, that claims a
version other than the one `VERSION` holds, that states a hook count the
installer does not wire, that clones the skill directory off a moving branch,
that carries a capability block disagreeing with the register, or that uses a
name the identity contract (`docs/brand/IDENTITY-CONTRACT.md`) does not allow
there. Where this project stands as a whole is in `CHANGELOG.md`, and the
current program baseline is
[`docs/program/BASELINE-AFTER-HANDOVER-2026-08-04.md`](docs/program/BASELINE-AFTER-HANDOVER-2026-08-04.md).

## Safety, privacy, and cost

**Your data stays on your disk.** There is no account, no server and no
subscription. The project claims it makes no network calls; do not take that on
faith, it is checkable in under a minute:

```bash
cd ~/.claude/skills/brothermode
grep -rnE "^[[:space:]]*(import|from)[[:space:]]+(urllib|http|socket|requests|ftplib|smtplib|telnetlib|xmlrpc)\b" tools/*.py
```

Expected: no output. That is the check that matters, because a network call
needs an import, and `tools/test_bm.py` enforces exactly this ban on every
shipping module in `tools/` so it cannot regress quietly.

The broader keyword sweep is worth running too, as long as you read its output
rather than expecting silence:

```bash
grep -rnE "urllib|requests|socket|http|curl|wget" tools/*.py tools/*.sh | grep -v "^tools/test_"
```

Expect a handful of hits, and expect every one of them to be a URL written down
rather than a URL fetched: vendor documentation links in comments and in the
runtime registry's source table, which records where each runtime fact was read
and on what date. Read the hits by KIND, not by count: what would matter is an
import of `urllib` or `requests`, or a `socket`, `curl` or `wget` invocation,
and the sweep shows none. Counting them here instead was a mistake, corrected
on 2026-08-02: this paragraph claimed two lines while the sweep returned
fifteen, which is the worst place in the document to be wrong, because it sits
in the section inviting you to distrust us and check. The `test_` files are
excluded because they deliberately contain these words in fixture data and in
the test that enforces the ban above. The one thing that shells out at all is
the autosave mechanism, and it only ever calls local `git`, never a network
command; `grep -rn subprocess tools/*.py tools/*.sh | grep -v test_` shows
exactly where.

**Telemetry is consent-gated.** Nothing is recorded until you say yes:
`scripts/setup.py` writes the consent record, `tools/bm_telemetry.py` is the
only writer, and `tools/test_bm_consent.py` refuses a write without consent.
What it records is your own session cost, and it stays on your disk with
everything else.

**The risky moments stay yours.** Sign-ins, payments, publishing and deletions
are prepared and handed back rather than performed; credentials are never
typed. Cutting and publishing a release is a founder-gated sequence in
`docs/RELEASE.md`, and the suite skips the release checks until a human has cut
the tag. `SECURITY.md` states the data model, including which file holds your
raw text before redaction.

**Cost.** The money you spend is Claude Code's own token cost, and the
telemetry hook is what tells you what a session actually spent, per session,
instead of leaving you to guess at the end of the month.

## Where other tools are better

Fair is more useful than flattering, so: this is not the right tool for every
job, and two categories beat it outright today. What is said about a peer below
is a desk assessment, read from that project's own public material, because no
file in this tree can carry evidence about a codebase this project does not
own. What is said about BrotherMode traces to a file named beside it. The
longer comparison, peer by peer and dimension by dimension, is
[`docs/market/CATEGORY.md`](docs/market/CATEGORY.md).

**Hosted agent platforms** that run in a browser and deploy for you get a
working URL with far less setup than this. There is no BrotherMode hosting, no
deploy button and no preview environment; the register above marks deployment
previews and the web delivery lane experimental, meaning not measured. If your
goal is a live site tonight and you do not care where the work is recorded,
start there.

**Coding runtimes such as Cline** execute across more tool surfaces today than
this project gates. BrotherMode's gates are machinery on Claude Code and
advisory instruction files everywhere else, where they fail open rather than
lock you out; `docs/RUNTIMES.md` carries the capability table saying which
runtime has hook points and where the hooks are verified.

No score, ranking or benchmark percentage appears here, in either direction:
the shared run that would earn one has not happened, and the register above
says so by marking the benchmark harness experimental.

What this trades those things for is the record: one writer per file refused by
a hook for the write tools it can see (and detected, not contained, beyond
them), a check that has to run after the last change before anything is called
done, and a written trail you can read afterwards. That trade is worth it when
the work matters more than the demo, and it is a bad trade when it does not.

Also not here, and not planned: a distributed lock service, multi-machine
coordination, or an organization-wide governance layer. Those serve a different
kind of user than the one this project is built for, and adding them would cost
the simplicity that makes this useful for one person. It is built to scale down
to one person doing the work of several roles, not up to a team: there is no
shared server, no account system and no multi-user coordination layer. Handing
a project to a teammate occasionally is supported (`bm_telemetry.py handoff`);
running this as a control plane for several people at once is not what it is
for.

## Under the hood, and what is not proven yet

Read the limits before the features. Two registers hold them, and both are
believed over this page when they disagree with it:
[`docs/KNOWN-LIMITS.md`](docs/KNOWN-LIMITS.md), which is what is not proven,
and [`docs/NOT-FINALIZED.md`](docs/NOT-FINALIZED.md), the numbered defect
register with its status words defined at the top. They are not restated here,
because a second copy of a limits list is a copy that goes stale.

The mechanics, if you want them: [`docs/HOW-IT-WORKS.md`](docs/HOW-IT-WORKS.md)
explains the tools that run today, [`docs/HOOKS.md`](docs/HOOKS.md) explains
what each hook receives and what the fence can refuse,
[`docs/CONTINUITY.md`](docs/CONTINUITY.md) states what a session owes the next
one when it ends with work still open, and
`docs/BrotherMode-Design-Document.pdf` is the whitepaper: philosophy, code,
data flow and cost.

### The booklet: the long-form explanation of this project

[`docs/book/brothermode-solo-builder-booklet.html`](docs/book/brothermode-solo-builder-booklet.html)
is the official long-form explanation of what this is and whether it is worth
your overhead. It is one self-contained file: no script, no external font, no
network request, so it opens by double clicking it and keeps working offline.

It is written for a solo founder or individual contributor deciding whether to
adopt this, and it is ordered pain first. What goes wrong when you hand serious
work to Claude, what relieves that immediately, what stops it coming back, and
only then the machinery. Five acts, nineteen diagrams, and a language toggle at
the top: the whole booklet exists in English and Japanese, prose and diagram
labels alike.

Three blocks in it are real captured output rather than mock-ups: the
eight-field status, the section headings of a generated project page, and the
node labels of the three drawings that page produced, taken by running a
project through `tools/brothermode_cli.py` against a throwaway repository and
pasted exactly as printed.

What it deliberately does not carry: any productivity number, because none has
been measured; any test count, because counts move and a reader seeing a
mismatch cannot tell a stale page from a broken install; and any ranking
against another product, because the shared run that would earn one has not
happened. It states its own limits in the same voice as this page, including
that nobody outside this project has read it.

`docs/book/brothermode-for-dummies.html` is a different book for a different
reader, for someone who has never heard the word hook.
[`docs/book/README.md`](docs/book/README.md) explains which is which, and why
each remaining HTML file in this repository is still here.

### What is in the box

| File | What it does |
|---|---|
| `SKILL.md` | The law: numbered sections covering classification, delegation, fences, budgets, research, honesty, memory, scoring |
| `DIGEST.md` | A short compression of the law, injected at every session start so the rules survive context loss |
| `RUBRIC.md` | A template for the metrics a weekly review scores against |
| `STATE.template.md` | The running state file format: fences, decisions, the never-forget list |
| `tools/bm_telemetry.py` | The mechanical half of the learning loop: session telemetry, corrections capture, scorecard, nags |
| `tools/bm_score.py` | Code-graded weekly checks, so an LLM judge only scores what code cannot decide |
| `tools/bm_sessionstart.py` | Session-start hook: injects the digest, overdue-review nags, and a recovery pointer after a compaction. Python since 2026-08-17, so it runs on a machine with no POSIX shell |
| `tools/bm_hookchain.py` | The hook chain driver: reads the payload once and hands it to each program in a named chain (Stop, PreCompact). Replaces the `sh -c` pipelines those two events used to run, which no Windows machine without Git Bash could execute |
| `tools/bm_bbstatus.py` | The gate runner's reporting arm: routes a local gate verdict on the origin remote and posts a Bitbucket build status. The only module that makes a network write, never wired into a hook, documented in `SECURITY.md` |
| `scripts/bm_ci_context.py` | Captures which revision a CI run is actually testing (source, destination, tested tree) and refuses a pull request run that cannot say, before the gate runs |
| `tools/bm_autosave.py` | On the PreCompact hook, snapshots your whole working tree (untracked files included) to a private local git reference. Never pushes. `recover` restores it |
| `tools/bm_threads.py` | Thread mode (opt-in): one persistent thread per key feature, plus a dashboard. Reversible mid-project |
| `tools/bm_fence_hook.py` | The PreToolUse fence: the one hook that can REFUSE a write to a file another live session owns. Explained in `docs/HOOKS.md` |
| `tools/bm_store.py`, `tools/test_bm_store.py` | The V2 storage engine and its tests, wired into the tools above since Phase 3 (2026-07-26) |
| `tools/bm_project.py` | The project surface the guided commands drive: start, status, next, review, deliver, and the canonical objects behind them |
| `tools/bm_learn.py` | The founder-facing correction-learning CLI: capture, approve, retrieve, grade. No direct database access, no automatic approval |
| `tools/bm_packs.py` | Gate deep-dive packs: on demand, writes one markdown document per decision under `Documentation/30-decisions/`, with the code quoted live from disk, the callers and tests found by search, the rollback, and the review slots. A citation that no longer resolves fails the build rather than quoting stale code |
| `tools/bm_docs.py` | The documentation engine: writes the numbered `Documentation/` folder from what is recorded, and renders the capability block on this page from `capabilities.status.json` |
| `tools/bm_learning.py` | Pure helper functions the CLI and store share: normalization, hashing, ranking. No database, clock, or file access |
| `tools/bm_project_facts.py` | Prints the facts documentation is allowed to state (version, release tag, schema version, hook events, suite list, Python floor), read out of the tree rather than typed into a page |
| `tools/bm_runtimes.py` | Generates the instruction file that wires BrotherMode into another AI coding runtime (Codex CLI, GitHub Copilot, Google Antigravity, Qwen Code, iFlow CLI, or a generic AGENTS.md). Each generated file carries the vendor URL its convention was read from and the date it was read |
| `tools/test_bm.py`, `tools/test_bm_autosave.py`, `tools/test_bm_fence_hook.py`, `tools/test_install.py`, `tools/test_bm_runtimes.py`, `tools/test_bm_docs.py` | The regression suites: the running tools, the autosave and its recovery, the fence hook, the installer, the runtime adapters, and the documentation facts. Standard library only |
| `tools/test_all.py` | Runs every suite serially, one process each, with one exit code. The actual gate; read this before running any single suite by hand |
| `tools/WEEKLY-REVIEW.md` | The weekly self-review procedure |
| `scripts/install.py`, `scripts/uninstall.py` | Wire and unwire the hooks in `~/.claude/settings.json`, backing it up first, touching no hook entry they did not write |
| `scripts/doctor.py` | Ten environment checks with plain-language remediation (table in docs/SETUP.md); the deepest proves the wired fence is LIVE by simulating a blocked foreign write and an allowed owner write in a throwaway project |
| `docs/QUICKSTART.md` | The literal ten-minute path, with expected output at every step |
| `docs/engineering/` | The engineering onboarding pack for a development team: a task-oriented README, one runnable first-project tutorial, the workflow map, the command reference, and the per-stage verification page. Ships as a standalone zip in the same folder |
| `docs/SETUP.md` | The fuller installation and hooks reference |
| `docs/HOOKS.md` | What each hook receives, what the fence can refuse, and the exact contract it implements |
| `docs/RELEASE.md` | The release discipline: tags, checksums, and the steps a machine must refuse to take on its own |
| `docs/HOW-IT-WORKS.md` | The mechanics of the tools that run today, explained exactly |
| `docs/RUNTIMES.md`, `docs/runtimes/` | Running BrotherMode in other AI coding runtimes: the capability table and the generated adapter files. Both regenerated by `tools/bm_runtimes.py` |
| `docs/CORRECTION-LEARNING.md` | The correction-learning system in plain language, with real command output and honest limits |
| `docs/brand/IDENTITY-CONTRACT.md`, `product.identity.json`, `capabilities.status.json` | The names this project uses, and the register every claim on a page has to agree with |
| `docs/KNOWN-LIMITS.md` | What is not proven yet. Read this before the rest |
| `docs/NOT-FINALIZED.md` | The numbered defect and limits register, status words defined at the top |
| `docs/BrotherMode-Design-Document.pdf` | The whitepaper: philosophy, the code, data flow and cost |
| `CHANGELOG.md` | What changed release to release, and the known limits of each addition |
| `vault-template/` | A ready-made memory vault folder: copy it and start working |

## License

MIT. Use it, fork it, rewrite the law to fit how you work. `RUBRIC.md` ships
as a template on purpose: measure your own baselines before freezing it.

Created by Khalil Maaouni.

## Part of Brother

This product is one capability area of Brother, the umbrella that carries the
shared chain, the verdict tuple and the evidence law across all three.

Read COORDINATION.md in https://github.com/khalilmaaouni/Brother before adding
a command, an agent, a hook, or a state vocabulary: the merge enforces surface
caps and a single state vocabulary, and work that fails them will be deleted.
