# BrotherMode roadmap

Status: CURRENT as of 2026-08-04. Product authority since 2026-08-11: the
product direction record this project keeps outside the published tree. No
item enters active development without passing its entry test (section 13.1
there), and it supersedes conflicting product-scope guidance in this page.

This page says how far every claim this project makes has actually been
checked, what is being worked toward next, and what is deliberately not on the
list at all. It carries no dates, because a date on a roadmap written by one
person is a promise about a calendar rather than a statement about evidence.

The register behind it is [`capabilities.status.json`](../capabilities.status.json),
which answers a narrower question: is a thing offered, in four states. This
page answers the other half, how far each claim has been proven, in six proof
states. Section 3 is GENERATED from that register, so the two cannot say
different things about the same capability.

## 1. The six proof states

A rung says who checked a claim and where, not how much anyone wants it to be
true.

1. **Planned.** Named here, with no evidence in the tree behind it yet. A
   reader should treat it as an intention.
2. **Implemented.** Built, with something in the tree describing it, and not
   measured. The code exists; the claim about what it achieves does not have a
   number behind it.
3. **Verified locally.** The evidence names a file or a test in this tree, and
   no continuous integration job covers it yet. It passes on the machine it was
   written on.
4. **Verified in CI.** The evidence names a job in the repository's own
   workflow definitions, so the check runs somewhere
   other than the author's own machine, on a schedule nobody controls by hand.
5. **Verified externally.** Checked by someone outside this project, or on a
   machine this project does not own.
6. **Certified.** The register marks it certified: a named evidence file in
   this tree plus a test or a job that goes red when the claim stops being
   true.

Two honest notes about that list, because the ordering invites a reading it
does not support.

Certified sits at the top as the strongest claim this project can make about
its own tree, and it does not include an outside opinion. Rung 5 is about who
did the checking; rung 6 is about how strong the proof inside the tree is.
Nothing is at rung 5 today, and section 3 prints that empty rung rather than
hiding it. What filling it would take is written down in this project's own closure
register, items X-01 to X-06: a second runtime with credits, participants who did not build this, a
benchmark corpus, measured dogfood, ecosystem thresholds, and fault injection.
All six are open, and none of them close by writing code.

The rungs are also not a queue every item walks in order. A capability can be
certified against this tree while no outside party has ever run it, which is
exactly the position most of section 3 is in.

## 2. What it takes to move up a rung

No item on this page reaches certified through a documentation-only change.
Editing this page moves nothing at all: section 3 is rendered from the
register, and a state written into the register has to name evidence that
resolves to a file in the tree before `tools/test_bm_docs.py` will pass. A
promotion is therefore always at least three things in one change: the work,
the file that proves it, and the register line pointing at that file.

The two checks that hold this, both runnable from a clone:

- `bm-docs roadmap-status --check` (from a clone, the same subcommand on
  `tools/bm_docs.py`) refuses a page whose generated block is not what the
  register renders today.
- `bm-docs verify-docs` runs that check, the same check for the README block,
  the identity manifest agreement, and a link check across the current pages,
  and reports one line per lane.

What those checks cannot do is judge whether the named evidence is the RIGHT
evidence. A test that proves the wrong thing still resolves to a file. That
judgement stays with the person reading the review, which is why every row in
section 3 carries its evidence sentence in full rather than a checkmark.

## 3. Where every capability stands today

The block below is GENERATED from
[`capabilities.status.json`](../capabilities.status.json) by
`bm-docs roadmap-status --write` (from a clone, the same subcommand on
`tools/bm_docs.py`). The mapping from the register's four states to these six
proof states is code, printed inside the block so a reader never has to take it
on trust. To change what this says, change the register and rerun the command.

<!-- BEGIN GENERATED ROADMAP STATUS -->
<!-- Generated from capabilities.status.json by `bm-docs roadmap-status --write` (the packaged console script; from a clone, tools/bm_docs.py). Edit the register, not this block. -->

Six proof states, mapped from the four states in `capabilities.status.json`, updated 2026-08-10: certified means the register marks it certified, meaning a named evidence file in this tree plus a test or a job that goes red when the claim stops being true; verified externally means checked by someone outside this project, or on a machine this project does not own; verified in CI means the evidence names a continuous integration job, so the check runs somewhere other than the author's own machine; verified locally means the evidence names a file or a test in this tree, and no continuous integration job covers it yet; implemented means built, with something in the tree describing it, and not measured; planned means named, with no evidence in the tree behind it yet.

The mapping is code rather than judgement. Certified stays certified. A beta row becomes verified in CI when its evidence names a job under `.github/workflows`, and verified locally otherwise. An experimental row becomes implemented when its evidence points at a file in this tree, and planned when it points at nothing. An unsupported row is not a rung at all and is listed as a non-goal below.

**Certified**, the register marks it certified, meaning a named evidence file in this tree plus a test or a job that goes red when the claim stops being true.

| Capability | Proof state | What proves it |
|---|---|---|
| Durable local store that survives a crash and can be recovered | certified | tools/bm_store.py holds the state and tools/test_bm_store.py exercises recovery; the store job in .github/workflows/tests.yml runs that suite on Linux, macOS and Windows. |
| Current pages are held to the facts read out of the tree | certified | tools/test_bm_docs.py refuses a current page carrying a stale count, a stale version, or a dated record that declares no status; docs/ba/QA-GATES.md states the gates. |
| Guided beginner flow on Claude Code | certified | skills/brotherme/SKILL.md drives the flow, commands/brotherme-start.md is its entry point, and docs/QUICKSTART.md is the install path a beginner follows. |
| Continuous integration on macOS and Linux | certified | .github/workflows/tests.yml runs the suite job on ubuntu-latest and macos-latest at Python 3.9 and 3.x, with fail-fast disabled so one platform cannot erase another's result. |
| Session telemetry recorded only after the user consents | certified | scripts/setup.py writes the consent record, tools/bm_telemetry.py is the only writer, and tools/test_bm_consent.py refuses a write without consent. |
| Two-command plugin install through Claude Code's own plugin manager | certified | scripts/release-smoke-install.sh proves the whole path on every release inside a throwaway configuration: marketplace add, install, installed version matched against VERSION, every hook group registered, uninstall leaving settings clean; first PASSED run 2026-08-07 on the release candidate tree, with a live sandboxed end to end run the same night. claude plugin validate passes both manifests. SURFACE LIMIT, founder-reproduced 2026-08-06: the desktop app cannot run /plugin itself; the two commands run once in a terminal and the app consumes the installed plugin. The plugin line tracks the repository's default branch by the plugin system's design; the tagged clone remains the immutable option and docs/RELEASE.md states both. |

**Verified externally**, checked by someone outside this project, or on a machine this project does not own.

Nothing stands at this rung today.

**Verified in CI**, the evidence names a continuous integration job, so the check runs somewhere other than the author's own machine.

| Capability | Proof state | What proves it |
|---|---|---|
| Windows | verified in CI | Only the store job in .github/workflows/tests.yml runs on windows-latest; the suite and gate jobs run on Linux and macOS only. docs/KNOWN-LIMITS.md records that the installer refuses Windows and that WSL works. There is no native Windows install lifecycle. |
| The signed authorisation an autonomous session has to work inside | verified in CI | tools/bm_autonomy.py is the command line, tools/test_bm_autonomy.py is its suite, and the store job in .github/workflows/tests.yml runs that suite on Linux, macOS and Windows; docs/AUTONOMY.md is the page. It stays beta because docs/KNOWN-LIMITS.md records open items against this layer and no use outside this project is recorded. |
| The durable controller that carries a signed outcome to a checked deliverable | verified in CI | tools/bm_controller.py is the engine and its command line, tools/test_bm_controller.py is its suite including an end to end run that is killed and resumed (its transcript is docs/program/absolute-lead/evidence/L03/E4-endtoend.json), the store job in .github/workflows/tests.yml runs that suite on Linux, macOS and Windows, and docs/FULL-AUTO.md is the page. Not experimental, because experimental here means not measured and this is measured. It stays beta because docs/KNOWN-LIMITS.md carries its own list of what the controller does not yet do, and no pilot outside this project exists. |
| A half hour catch-up that arrives on its own, on by default after setup | verified in CI | hooks/hooks.json wires it to the Stop hook as a due check rather than a background process, tools/test_bm_consent.py drives every hook wired command against a fresh home directory and fails if any of them writes before consent (the suite job in .github/workflows/tests.yml runs that suite), and SECURITY.md discloses that it ships on by default, what it writes when a catch-up is due, and that it writes nothing when it is not. It stays beta because it cannot fire inside a turn that never ends and because its activity ceiling is a chosen constant, both recorded in docs/KNOWN-LIMITS.md. |

**Verified locally**, the evidence names a file or a test in this tree, and no continuous integration job covers it yet.

| Capability | Proof state | What proves it |
|---|---|---|
| Single writer per file for supported write tools, refused by a hook; other writes detected, not contained | verified locally | Conflicting writes are refused for the Claude Code write tools (Edit, Write, MultiEdit, NotebookEdit) and readable apply_patch envelopes on the Bash leg, wired by hooks/hooks.json and proven by tools/test_bm_fence_hook.py. Other shell and external writes are detected where possible by tools/bm_bash_audit.py but are NOT contained. Hooks are cooperative enforcement: no container or operating system sandbox is provided. MEASURED 2026-08-07 on OpenAI Codex CLI 0.146.0: the fence does NOT fire in the codex exec path. A live run overwrote a file another session had claimed, twice, and a marker probe proved the PreToolUse hook never executed, with config syntax, project trust and hook-trust bypass all ruled out (docs/mistakes/M19-the-codex-fence-does-not-fire-in-exec-mode.md). Under Codex, BrotherMode is an instruction file plus a working command line, not an enforcement layer. Downgraded to beta: proven in this tree with tools/test_bm_fence_hook.py, but not proven on an installed plugin because the install writes no INSTALLED-FROM stamp and the detector scripts/doctor.py check_install_identity can only return SKIP. Since 2026-08-17 the same hook also refuses a write to any git-tracked file while a live tools/test_all.py gate lock covers the checkout (the battery fence), for every session including the one that started the gate; a lock it cannot read is reported and, in advisory mode, never blocks. The battery classes in tools/test_bm_fence_hook.py prove it in this tree, under the same beta caveat as the rest of this row. ENFORCED MODE DRIVEN 2026-09-02 for the first time, by ../../scripts/fence_enforced_drill.py: 20 conditions, each fed to the hook twice, advisory against enforced, 20 PASS and 0 NO-DATA. Fifteen conditions that fail OPEN with the mode unset fail CLOSED with BM_FENCE_MODE=enforced, and a properly claimed write still ALLOWS under enforced, so enforcement is selective rather than blanket. The drill was itself driven backwards against a mutant copy of the hook whose enforced_mode() returns False, which produced 15 FAIL at exit 1. ONE GAP FOUND AND NOT FIXED, recorded here rather than left in a transcript: a payload whose tool_name is not a string leaves decide() at the not-a-write-tool branch BEFORE any mode check, so enforced mode cannot refuse it. That is one of the five shapes of malformed payload this row's C-01 note claims enforcement covers, so the claim is one shape wider than the code until that branch moves. |
| A record of what was decided and why, and the short catch-up built from it | verified locally | tools/bm_lead.py records and renders them over two append only tables in tools/bm_store.py, tools/test_bm_lead.py is its suite, and docs/program/absolute-lead/DESIGN-insight-ledger-and-handback.md states what a record has to carry before it may be written. It stays beta because the record holds the coordinator's judgement rather than a measurement, because no continuous integration run covers it yet, and because docs/KNOWN-LIMITS.md names what it does not do. |
| Handing a decision and the work under it back to the person who owns it | verified locally | Offered on every key decision and enforced by a refusal in tools/bm_store.py rather than by a rendering convention: a key decision that offers no handback cannot be recorded at all. tools/bm_lead.py performs the handback and writes the page a developer picks the work up from, and tools/test_bm_lead.py drives both, including the refusal of a second handback on one decision. It stays beta because no continuous integration run covers it yet and no handback outside this project is recorded. |
| Handover pages an analyst or a project lead can take a project over from | verified locally | tools/bm_lead.py generates them from rows and tools/test_bm_lead.py checks both directions: every row the store says belongs on a page reaches that page, and every claim on a page resolves back to the row it came from. It stays beta because no continuous integration run covers it yet and because nobody outside this project has read a pack, which is the empty rung docs/ROADMAP.md section 1 describes. |
| One page showing where a project stands, generated from that project's own records | verified locally | PROVEN: tools/bm_view.py writes the page as one self contained file from the records, through the collectors in tools/bm_lead.py rather than a second reading of its own, tools/bm_visual.py draws it, and tools/test_bm_view.py and tools/test_bm_visual.py check the structure instead of the pixels (a drawn node for each row, a label matching the row it came from, no address outside the file, one file, exactly one recommended next action). NOT PROVEN, and it is a separate half: publishing that file as a private page needs a paid plan, a signed in session and four further conditions listed in docs/KNOWN-LIMITS.md, so what the product promises is the file on disk and the published page is an addition that can be unavailable. OPEN: nobody outside this project has opened either one. |
| A scripted first fifteen minutes with three commands and something to look at at each step | verified locally | PROVEN: commands/brotherme-start.md carries the opening block that writes nothing before consent and the first page after it, commands/brotherme-help.md asks one question instead of listing every command, and tools/test_bm_view.py drives the path from an empty folder and fails if a fourth command is offered before the first piece of work completes, if anything is written before consent, or if a section with no rows renders blank instead of the short note tools/bm_view.py holds for it. OPEN, and this is the whole gap: fifteen minutes is a target, no first run by a person who has never used this has been measured, and the checks are structural rather than behavioural. |
| Four levels of alert where exactly one interrupts, computed from the records rather than stored | verified locally | PROVEN: tools/bm_visual.py computes the levels as one function over rows with no table behind them, so a condition that clears takes its alert with it and nothing has to be dismissed, and tools/test_bm_visual.py holds the four anti noise rules to that (at most one interrupting alert on screen, at most two levels in any one message, no promotion by age, one interrupt per cause per catch-up window). NOT PROVEN: that the ladder keeps a reader engaged, which is a claim about a person rather than about code. OPEN: hooks/hooks.json runs the check when a session stops, so it cannot fire inside a turn that never ends, which is the limit docs/KNOWN-LIMITS.md already records for the half hour catch-up. |
| The offer to take a decision and the work under it back, on screen whether or not a decision is open | verified locally | PROVEN: tools/bm_view.py renders the standing panel on every page, its wording comes byte for byte from tools/bm_lead.py rather than being retyped, tools/test_bm_view.py fails a page that drops it and fails a drawn decision whose last branch is not the handback, and tools/bm_store.py already refuses to record a key decision that offers no handback at all. NOT PROVEN, and by design: nothing on the page can act on the project, the control copies a prompt the reader pastes back into the session, and docs/KNOWN-LIMITS.md states that as a limit rather than dressing it up. OPEN: no handback by anyone outside this project is recorded. |
| What the hooks cost per action, measured on a stated machine, with the parts nobody measured named as unmeasured | verified locally | MEASURED: tools/bm_hookbench.py reads which programs fire at which event from hooks/hooks.json, feeds each one the payload shape docs/HOOKS.md documents, and times it against a store built for the run inside a temporary directory with HOME and every BrotherMode variable pinned there. It reports, per user action, the cost of each program AND the cost of the whole chain (the four Stop programs share one budget, so those are two different numbers), each as a median with its spread over a stated number of repetitions, plus the machine and interpreter the run was taken on, and the exit codes and fail open lines the run actually produced. docs/PERFORMANCE.md is generated from that run and carries the record it was rendered from; tools/test_bm_hookbench.py re-renders the page from that record and fails on one differing byte, so a number cannot be typed onto the page by hand, and it also refuses a sandbox whose fence fails open rather than reporting the cost of a hook that checked nothing. NOT MEASURED, named on the page rather than estimated: the fork, the exec and the interpreter bootstrap every hook process pays before any code of this project runs, which is why every published total is a LOWER BOUND (the tool runs the programs in process because tools/test_bm.py bans import subprocess in shipping modules and its allowlist does not name this one); the SessionStart hook, which is a shell script; store lock contention frequency in real use; hook failure frequency in the field; and the share of real sessions hitting a warning or a false refusal, which needs telemetry this project deliberately does not collect. OPEN: the numbers are one machine, one operating system and one Python version, and nothing here says what they are on anyone else's. |

**Implemented**, built, with something in the tree describing it, and not measured.

| Capability | Proof state | What proves it |
|---|---|---|
| Benchmark harness comparing runs | implemented | docs/BENCHMARK.md describes the method and docs/BENCHMARK-V1-V2-RC2.md records a dated run. No run against the current tree is recorded, so no current number is claimed. |
| Cursor compatibility mode: independent install, manage, uninstall, and a local Fable-to-Cursor harness | implemented | scripts/install_cursor.py and scripts/uninstall_cursor.py own the Cursor lifecycle; tools/bm_cursor.py is the manage and mailbox harness CLI; tools/bm_cursor_hook.py adapts Cursor hook payloads to the Claude fence contract; tools/test_bm_cursor.py covers adapter translation, install ownership, and dispatch-claim-record-adopt. docs/CURSOR-COMPAT.md states the honest limit: fence enforcement under Cursor is ADVISORY until a live Agent canary is recorded. No live Cursor Agent or Cloud Agent canary is in this tree yet. |

**Planned**, named, with no evidence in the tree behind it yet.

| Capability | Proof state | What proves it |
|---|---|---|
| Delivering a web build through the same guided flow | planned | not measured |
| Deployment previews attached to a delivery | planned | not measured |

**Not a goal**, and therefore on no rung: the register's unsupported rows, carried here because a roadmap that lists only what is coming reads as a promise about everything it leaves out.

| Not a goal | Why it is not offered |
|---|---|
| Publishing to production on its own | Not offered. Cutting and publishing a release is a founder-gated sequence of steps in docs/RELEASE.md, and the suite skips the release checks until a human has cut the tag. |
| Spending money on the user's behalf | Not offered. SECURITY.md states there is no account and no server, and that the only outbound call is a version check the user invokes by hand. |
| Legal or security certification of any kind | not measured |
| A guaranteed native mobile result | not measured |
| Replacing a human specialist review | not measured |
| Multi-user or enterprise project management | Not offered. README.md states there is no shared server, no account system and no multi-user coordination layer, and that running this as a control plane for several people is not what it is for. |
| Changing its own safety rules | not measured |

<!-- END GENERATED ROADMAP STATUS -->

A note on the newest wave, added 2026-08-28, kept here rather than in the waves section below, where this page's own rule is that waves name outcomes and never dates. It is listed last because the ordering rule on this page is about unproven claims, and it is the only wave whose cost has already been PAID rather than estimated: on 2026-08-27 a founder-facing build scored 0 of 5 on a defect this estate had root-caused twice in writing, weeks earlier, and neither note was read before shipping. The founder's own words that day: the tool's memory usage to avoid past mistakes is very weak, even when he warns of them. The evidence, with every command to reproduce it, is kept with this project's own feedback records.

## 4. Waves, and the order they are taken in

Waves are named by the outcome that closes them, never by a date, and this page
does not carry the working plan's own loop identifiers: those move as the plan
is replanned, and a page pinned to them goes stale without anything being
wrong. The ordering rule is the only scheduling claim made here: a wave is
taken when it removes the largest unproven claim still standing, and it does
not close until the proof named beside it exists.

| Wave | The outcome that closes it | What would prove it |
|---|---|---|
| One product truth | One name, one register, one story, one roadmap, and one command that checks all of them against the tree | `bm-docs verify-docs` reports PASS on every lane, and the naming, evidence and absolute-claims tests in `tools/test_bm_docs.py` read every current page |
| An install path a stranger can follow twice | The plugin install carries the same evidence the git clone already carries, so it stops being the second-best documented path | The register's plugin row cites a rehearsed install from a clean machine rather than one recorded first install, and leaves beta |
| The whole lifecycle on Windows | Install, hooks, doctor and uninstall exercised on native Windows, not the store suite alone | The Windows row's evidence names jobs beyond the store job in `.github/workflows/tests.yml`, and `docs/KNOWN-LIMITS.md` stops recording a refused installer |
| Measured use, not reported use | Counted projects, recorded failures, and a comparison against working without the tool | Item X-04 in `docs/closure/CLOSURE_REGISTER.md` closes with numbers behind it |
| A comparison someone else can reproduce | A benchmark corpus run under the same conditions for every tool named in `docs/market/CATEGORY.md` | Item X-03 closes, and the benchmark row leaves implemented for a rung with a measurement behind it |
| Verification from outside | People who did not build this install it, use it, and report what broke | Items X-01, X-02 and X-05 close, and rung 5 in section 1 stops being empty |
| A lesson that reaches the moment it is needed | A failure this estate has already written down cannot be repeated without the person being shown it, because retrieval happens when a symptom is described or the file is opened, not once at session start | A defect already recorded in memory is re-introduced in a rehearsal, and the surfacing fires before the change lands; plus the four measured gaps in the founder feedback record on memory are closed: the index no longer truncates, the technical lessons and the correction rules can see each other, no approved rule contradicts a live founder decision, and a symptom query stops ranking a process rule first on one shared word |

The first wave is the one this page belongs to. The rest are ordered by how
much of what this project claims about itself they would let a stranger check
without asking the author.

## 5. Deferrals: what this roadmap does not schedule

These are non-promises, listed so that silence cannot be read as a plan. Each
one is a thing a reader could reasonably expect from a tool that says it
delivers outcomes, and each one is out.

- **Native mobile builds.** A guaranteed native mobile result is unsupported in
  the register, and nothing on this page moves it. The guided flow can help
  with a mobile project the way it helps with any project, and it makes no
  claim about the result running on a device.
- **Creative media generation.** Images, video, audio and design assets are not
  produced by this tool, and this roadmap does not schedule them. It
  orchestrates work and records evidence; generating media is a different
  product with different failure modes, and pretending otherwise would put a
  claim on this page that no file in this tree could support.
- **Office document artifacts.** Spreadsheets, slide decks and word processor
  files are not deliverables here. What this project generates is markdown and
  the records behind it, so a delivery packet stays readable by git and
  diffable in review.
- **Multi-user and enterprise coordination.** Unsupported in the register, and
  a deliberate design limit rather than a gap: there is no shared server, no
  account system and no multi-user coordination layer, as
  [`README.md`](../README.md) states. Handing a project to another person
  occasionally is supported; running this as a control plane for a team is not
  what it is for.

Two of those four have a row in the register today, the mobile one and the
multi-user one, and the other two are stated here alone. That asymmetry is
recorded rather than tidied over: this page is not the register's editor, and
adding a row is a register change with its own review.

Nothing on this list is a judgement about the tools that do those jobs well.
This project's own category survey names peers that are better at several of
them, in their own words rather than this project's.
