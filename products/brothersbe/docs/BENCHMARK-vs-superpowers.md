# BrotherSBE against Superpowers: a measured benchmark

Measured 2026-08-10. BrotherSBE at HEAD da05487, version 1.0.0-rc.38. Superpowers at the installed copy of v6.1.1, plus the GitHub API for figures the local copy cannot show (the installed copy is a shallow clone with one commit, so its history is not readable here).

Every number below came from a command run in this session. Nothing is from memory. Where a figure has one source only, it says so.

**Re-checked 2026-08-20, local only, no network:** the installed Superpowers
copy is still v6.1.1 (`~/.claude/plugins/cache/superpowers-marketplace/superpowers/6.1.1/package.json`),
unchanged from the header above. BrotherSBE's own HEAD and version have moved
since the measurement: `VERSION` now reads 3.2.1 and `git rev-list --count HEAD`
now reads 724, both past the da05487 / 1.0.0-rc.38 point this benchmark was
measured against. The benchmark was not re-run; every figure in this file
stays dated to 2026-08-10 at da05487, a measurement rather than a rolling
number.

## Read this first

These two are not the same kind of thing, and a straight score would mislead you.

Superpowers is a general development methodology: brainstorm, write a spec, write a plan, then drive it through test-driven subagent execution. It carries no domain knowledge and no executable checks. It works by putting well-written instructions in front of the model at the right moment.

BrotherSBE is a backend and data engineering specialist that ships executable enforcement: gates that exit non-zero, an eval bed, and eight reviewer subagents. It knows what a migration rehearsal is. Superpowers does not, and does not try to.

So the fair comparison runs on the axes they share (how a stranger installs it, how much it costs to keep in context, whether anyone else uses it), and stops pretending on the axes they do not.

## The measured facts

| Measure | BrotherSBE | Superpowers | Command |
|---|---|---|---|
| Skills | 12 | 14 | `ls skills` |
| Skill markdown files | 12 | 36 | `find skills -name "*.md"` |
| Skill markdown lines | 1,135 | 7,231 | `xargs wc -l` |
| Subagents shipped | 8 | 0 | `ls agents` |
| Python tools | 65 | 0 | `ls tools/*.py` |
| Python lines (tools + src) | 69,697 | 0 | `find ... -name "*.py" \| xargs wc -l` |
| Test files | 46 unit suites, 4 eval scripts | 37 (shell, js, ts) | `find -name "test_*.py"` / `find tests -type f` |
| CI workflows | 3 | present upstream, not in installed copy | `ls .github/workflows` |
| Hook events used | 5 (SessionStart, SessionEnd, PreCompact, PreToolUse, Stop) | 1 (SessionStart) | `hooks/hooks.json` |
| Entry file size | SKILL.md 17,232 bytes | using-superpowers 3,063 bytes | `wc -c` |
| Harnesses supported | 1 (Claude Code) | 10 (Claude Code, Antigravity, Codex App, Codex CLI, Cursor, Factory Droid, Copilot CLI, Kimi Code, OpenCode, Pi) | README |
| Repo size | 6,217 KB | 4,435 KB | GitHub API |
| Commits | 509 | not readable locally | `git rev-list --count` |
| Created | 2026-07-28 | 2025-10-09 | GitHub API |
| Stars | 0 | 270,008 | GitHub API, single source |
| Forks | 0 | 24,139 | GitHub API, single source |
| Contributors | 1 | 30 | GitHub API |
| Releases | 1 | 11 | GitHub API |
| Open issues | 0 | 328 | GitHub API |

The star and fork counts came from one source, the GitHub API, read today. They are not cross-checked against a second source.

## Scored, with the reasoning attached

Ten is best. The score is mine; the evidence beside it is measured.

| Dimension | BrotherSBE | Superpowers | Why |
|---|---|---|---|
| Distribution reach | 3 | 10 | One harness against ten. Superpowers also sits in the official Anthropic marketplace and the official Codex marketplace. |
| Adoption proof | 1 | 10 | Zero stars, zero forks, one contributor, thirteen days public. Against 270k stars, 24k forks, 30 contributors, 328 open issues (issue traffic is itself proof people are using it). |
| Mechanical enforcement | 9 | 3 | BrotherSBE's gates run and block: `sbe_gate.py --strict` returns a non-zero exit code, and three CI workflows consume it. Superpowers enforces through prose plus a single session-start hook. Nothing in it computes a verdict. |
| Domain depth (backend, data) | 9 | 4 | Eight domain reviewers (backend, data, migration, security, QA, evidence, architecture) plus decision tables. Superpowers is domain-neutral by design. |
| Onboarding friction | 5 | 9 | Superpowers needs zero commands: skills fire on their own. BrotherSBE asks the user to learn twelve slash commands and answer a five-question tier intake before work starts. |
| Context cost per session | 4 | 8 | Superpowers injects 3 KB and uses one hook. BrotherSBE's SKILL.md alone is 17 KB, plus an injected laws digest, plus five hook events. In this very session the BrotherSBE session-start output was large enough that the harness spilled it to a file. |
| Test infrastructure | 8 | 7 | Near parity, different shapes. BrotherSBE has 46 Python unit suites and an eval bed that mechanically hollows every declared example. Superpowers has 37 test files spread across eleven harness directories, which is the harder integration problem. |
| Maintainability and bus factor | 2 | 8 | 69,697 lines of Python maintained by one person against 7,231 lines of markdown maintained by thirty. |

## The one number that matters most

69,697 lines of Python against zero.

That single figure explains both sides of this comparison. It is why BrotherSBE can refuse a release when the evidence is missing and Superpowers can only advise. It is also why Superpowers runs on ten harnesses and has thirty contributors while BrotherSBE runs on one and has you.

Superpowers made a bet that instructions are enough and portability is worth more than enforcement. BrotherSBE made the opposite bet. Neither bet is wrong. But the second one carries a maintenance cost that a single maintainer eventually cannot pay, and that cost is currently invisible because you have not yet had to keep 65 tools working across a Claude Code breaking change.

## Where BrotherSBE actually leads

1. Verdicts that a machine computes. `sbe_gate.py`, `sbe_design.py`, `sbe_score.py`, `sbe_checks.py` all return exit codes. Superpowers has no equivalent, and cannot fail a build.
2. The NO-DATA discipline, mechanically swept. `evals/test_no_data_class.py` imports every tool and hollows every declared example, requiring that none of them ever return PASS on empty input. This is an honesty property enforced by a test, which is rare in this whole category.
3. Domain reviewers as real subagents with restricted tool sets, not prose personas.
4. Hook coverage across five events, including PreCompact and Stop, where Superpowers uses one.

## Where it loses, in order of how much it costs you

1. **Nobody has used it.** Zero forks means zero independent installs proven. Every usability claim in the repo is currently self-reported. This is the gap that no amount of code closes.
2. **One harness.** Superpowers made itself portable early, and that is most of its reach. BrotherSBE is Claude Code only.
3. **Onboarding asks too much up front.** Twelve commands and a tier intake before the first useful output. Superpowers gets to work on the first message.
4. **Context tax.** 17 KB of SKILL.md plus a digest plus five hooks, on every session, before any work happens.
5. **Bus factor of one against 69k lines.**

## What would close the gap, ranked by return

1. Make one command do useful work with zero prior knowledge. Superpowers' whole onboarding advantage is that the user types nothing. A `/brothersbe:start` that produces a real design artifact from a single sentence, with the tier intake inferred rather than asked, removes the largest friction measured here.
2. Get three outside installs and record what broke. This is already the open founder gate in the project's own record, and it is correctly identified as the blocker.
3. Cut the always-on context. Move more of SKILL.md behind the routing table that `references/` already implements. The lazy core exists; it is not yet lazy enough.
4. Port to a second harness only after the above. Portability multiplies reach, but multiplying zero adoption still gives zero.

## Limits of this benchmark

- Superpowers' local copy is a shallow clone, so its commit count, contributor history and CI configuration were read from the GitHub API or its README, not from its git history.
- Star and fork counts are single-sourced from the GitHub API today.
- No user study was run on either side. Onboarding friction is scored from measured surface (command count, entry file size, whether an intake is required), not from observed users.
- Quality of the skill instructions themselves was not scored on either side. That would need blind human rating, which is exactly the founder-gated work already open.
- Ultrapowers, mattpocock-skills and the other installed plugins were sampled but not scored. They are smaller than both and would not change the ranking.
