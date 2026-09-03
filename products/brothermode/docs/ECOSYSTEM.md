Status: CURRENT.

# BrotherMode and the coding-agent ecosystem

## Why this page exists

This page exists so a reader can choose correctly, and correctly sometimes
means choosing something other than BrotherMode. If you read this and decide
Cursor, GitHub Copilot, Cline, Claude Code on its own, OpenAI Codex CLI, or
superpowers fits your situation better, this page has done its job. We would
rather you land on the right tool by way of this page than land on
BrotherMode by way of a page that hid the tradeoffs.

It is also fair to say plainly: this page is written so it gets found in
search by people comparing these tools. That is not in tension with the
first paragraph. A comparison page only gets cited, and only gets found,
when the people who read it can trust what it says, including the parts
that are not flattering to us.

Every factual claim below comes from a single research pass completed on
2026-08-11, itself built from 49 primary sources. Where a claim could not be
confirmed against a primary source, or where a fetch disagreed with another
fetch, that is stated in the same sentence, not hidden in a footnote.

## Which one fits your situation

If you want to open an app, sign in, and start editing with no terminal and
no setup, Cursor fits that best. If your whole team already lives in GitHub
Issues and wants a bounded task worked in the background with no local
machine involved at all, GitHub Copilot's coding agent fits that best. If
you want the model choice open across 30 or more providers and a fully
free, auditable, open-source codebase, Cline fits that best. If you want a
plan-then-execute terminal loop that is Apache-2.0 end to end and scriptable
into CI, OpenAI Codex CLI fits that best. BrotherMode fits the situation
where you are already running Claude Code and want a governance layer on
top of it, a plan, a file list, a single writer per file, and a verifying
command quoted before anything is called done, and you are willing to pay
the setup cost of hooks and state files to get that. BrotherMode is not
named for the situations above because, on the evidence gathered here, it
is not the best answer to them.

## 1. Claude Code

Claude Code is Anthropic's command-line and IDE coding assistant: you
describe what you want in plain language and it reads your codebase, edits
files, and runs commands on your machine. Quoted from
[code.claude.com/docs/en/overview](https://code.claude.com/docs/en/overview):
"Claude Code is an agentic coding tool that reads your codebase, edits
files, runs commands, and integrates with your development tools."

What it is genuinely best at: the broadest first-party surface of any tool
in this review. One engine runs across terminal, VS Code, JetBrains, a
desktop app, and the web, and ships hooks, skills, subagents, plugins, MCP,
and an Agent SDK from the same codebase, confirmed by two independent
fetches of [code.claude.com/docs/en/overview](https://code.claude.com/docs/en/overview).

Where it beats BrotherMode: Claude Code needs no setup beyond installing
it. No fence hooks, no state files, and it updates itself. BrotherMode's own
comparison page already concedes this: it "does better than BrotherMode at
raw flexibility... it carries none of the governance overhead this project
asks a session to pay on every write" (docs/market/CATEGORY.md, this
repository).

Licence and pricing, snapshot read 2026-08-11: not open source. The public
GitHub repo ships install scripts, docs, and plugins, not the compiled
agent. Its LICENSE.md states "Anthropic PBC. All rights reserved. Use is
subject to Anthropic's Commercial Terms of Service"
([github.com/anthropics/claude-code/blob/main/LICENSE.md](https://github.com/anthropics/claude-code/blob/main/LICENSE.md)).
Pricing, double-sourced from [claude.com/pricing](https://claude.com/pricing):
the Free plan does not include Claude Code; Pro is $17/month billed
annually; Max plans start "from $100/month" for 5x usage, more for 20x;
Team seats are $20 to $25 per seat per month (Standard) or $100 per seat per
month (Premium), all including Claude Code; Enterprise is seat price plus
usage at API rates. This is a snapshot taken on 2026-08-11, not a stable
citation, since no vendor pricing page read for this research carried a
visible last-updated date.

Concrete handoff seam: Claude Code is an MCP client, so an external tool
(Jira, Slack, Figma, a custom server) can be pulled into a session, and it
separately ships a documented GitHub Actions integration
(`claude-code-action`) that runs on `@claude` mentions or a cron schedule,
authenticates from repo secrets, and pushes commits or opens a pull request
on a git branch
([code.claude.com/docs/en/mcp](https://code.claude.com/docs/en/mcp),
[code.claude.com/docs/en/github-actions](https://code.claude.com/docs/en/github-actions)).
This is the exact seam BrotherMode itself already builds on: BrotherMode is
a Claude Code plugin wired into this same hooks and skills mechanism.

## 2. GitHub Copilot (CLI and coding agent)

GitHub Copilot is Microsoft and GitHub's AI coding assistant, available as
in-editor autocomplete and chat, a terminal CLI, and a "coding agent" that
works a GitHub issue in the background and opens a pull request on its own
([docs.github.com/en/copilot/about-github-copilot/what-is-github-copilot](https://docs.github.com/en/copilot/about-github-copilot/what-is-github-copilot),
[docs.github.com/en/copilot/concepts/agents/about-copilot-cli](https://docs.github.com/en/copilot/concepts/agents/about-copilot-cli)).
The older `gh copilot` suggest and explain extension was deprecated on
October 25, 2025 in favor of the current Copilot CLI, per
[github.com/github/gh-copilot](https://github.com/github/gh-copilot),
cross-checked against a GitHub changelog entry dated 2026-01-21.

What it is genuinely best at: handing off well-scoped, bounded background
tasks (bug fixes, dependency bumps, targeted refactors, added test
coverage) asynchronously while a developer works elsewhere. The coding
agent runs in "a GitHub Actions-powered environment" with "its own
ephemeral development environment" and returns a reviewable diff and pull
request rather than an interactive session
([docs.github.com/en/copilot/concepts/agents/coding-agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent)).
Copilot's own docs give the clearest non-goals statement of any tool
reviewed here: "It's important to note that they were not designed for
every possible scenario." The coding agent specifically "can only make
changes in the repository specified when you start a task," "can only work
on one branch at a time," has "a maximum execution time of 59 minutes," and
"only works with repositories hosted on GitHub"
([docs.github.com/en/copilot/concepts/agents/coding-agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent),
[docs.github.com/en/copilot/responsible-use/agents](https://docs.github.com/en/copilot/responsible-use/agents)).

Where it beats BrotherMode: coding agent runs entirely in GitHub's own
cloud, no local machine, no local Claude Code session, and no locally
installed governance layer required at all. For a team that already lives
in GitHub Issues, that is strictly less setup than installing and
configuring BrotherMode on a local Claude Code install.

Licence and pricing, snapshot read 2026-08-11: not open source. The CLI's
public repo ships under a proprietary EULA, the "GitHub Copilot CLI
License," which permits running it but forbids modifying it or creating
derivative works
([github.com/github/copilot-cli/blob/main/LICENSE.md](https://github.com/github/copilot-cli/blob/main/LICENSE.md)).
Pricing, double-sourced from
[github.com/features/copilot/plans](https://github.com/features/copilot/plans):
Free is $0/month with 2,000 completions and 50 chat requests; Pro is
$10/month with unlimited completions; Pro+ is $39/month with access to
premium models including Opus; Max is $100/month for "sustained,
high-volume agent workflows." Coding-agent delegation is gated to Pro+ and
Max. Business and Enterprise per-seat pricing could not be confirmed on
GitHub's own page and is left out here; see "What we could not verify."

Concrete handoff seam: coding agent reads MCP tool configuration set as
JSON in a repository's own GitHub settings panel, and otherwise works
through GitHub's native Issues, branch, and pull request model, so
assigning an issue to Copilot is itself the handoff. Its MCP support is the
most restricted of the six tools reviewed: its own docs state it does "not
currently support resources or prompts provided by the MCP server" (tools
only), and does "not currently support remote MCP servers that leverage
OAuth for authentication and authorization"
([docs.github.com/en/copilot/concepts/agents/coding-agent/mcp-and-coding-agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/mcp-and-coding-agent)).

## 3. Cursor

Cursor is a standalone code editor, a fork of VS Code, with an AI agent
built into it that can read, write, run, and test code across a project.
From [cursor.com](https://cursor.com/): "your coding agent for building
ambitious software."

What it is genuinely best at: a polished, all-in-one IDE experience that
bundles frontier models from multiple vendors (Anthropic, OpenAI, Google)
in one place, plus parallel and background agents, inline diffs, and
checkpoints, all inside one editor a non-CLI person can just open and use
([cursor.com](https://cursor.com/), [cursor.com/docs/agent/overview](https://cursor.com/docs/agent/overview)).
This framing traces only to Cursor's own site and docs; no independently
fetched third-party review confirmed it, so it is single-sourced to the
vendor.

Where it beats BrotherMode: Cursor needs no terminal literacy. Install the
app, sign in, and the agent, the model choice, and the diff review are all
in one GUI. BrotherMode assumes a Claude Code terminal or IDE session plus
its own hook and store setup, a real barrier for someone who does not want
to touch a command line.

Licence and pricing, snapshot read 2026-08-11: Cursor is widely reported as
proprietary, a closed fork of VS Code, but in this research that specific
claim traces only to a third-party blog, not an independently opened
primary source, so it is UNVERIFIED (Cursor's own pages carry no license
statement). Pricing, read from
[cursor.com/pricing](https://cursor.com/pricing): Hobby is free with
limited Agent requests; Pro is $20/month; Pro+ and Ultra add usage
multipliers on top. Two independent fetches in the underlying research
disagreed on the exact Pro+ and Ultra numbers, one read them as multipliers
off the $20 base, the other read flat monthly figures reported elsewhere as
$60 and $200, so treat those two figures as unresolved; see "What we could
not verify." Teams Standard is $40 per user per month, Enterprise is
custom.

Concrete handoff seam: Cursor is an MCP client over stdio, SSE, or
Streamable HTTP, so an external tool or server can be pulled into it, but it
does not expose its own MCP server, so the seam runs one direction only
([cursor.com/docs/context/mcp](https://cursor.com/docs/context/mcp)). Its
own docs are explicit that local checkpoints are not a substitute for
version control: "Checkpoints are stored locally and separate from Git.
Only use them for undoing Agent changes; use Git for permanent version
control" ([cursor.com/docs/agent/overview](https://cursor.com/docs/agent/overview),
single-sourced, no broader non-goals page was found). The durable handoff
surface for anything permanent remains git.

## 4. OpenAI Codex CLI

OpenAI Codex CLI is OpenAI's open-source coding agent that runs in your
terminal, reading and editing a local codebase from natural-language
instructions. From [github.com/openai/codex](https://github.com/openai/codex):
"Codex CLI is a coding agent from OpenAI that runs locally on your
computer."

What it is genuinely best at: a formal plan-then-execute separation, a
read-only plan mode that drafts a step-by-step plan before any file is
touched, a scriptable terminal loop built for CI use ("You work from the
terminal," "You need scripting or CI," "You want a local code review,"
"You want to hand work to the cloud"), and being the only fully
open-source agent among the big-lab tools in this set
([learn.chatgpt.com/docs/codex/cli](https://learn.chatgpt.com/docs/codex/cli)).
No explicit non-goals statement was found in the pages read; that is a
genuine documentation gap, not an unsearched claim.

Where it beats BrotherMode: Codex CLI's plan mode and AGENTS.md convention
are open and forkable. Anyone can read the Apache-2.0 source and adapt it.
BrotherMode's enforcement, by contrast, is shaped to one specific hook
surface on one specific runtime (Claude Code) and, per BrotherMode's own
docs, does not even fire correctly on Codex's own exec path
(docs/mistakes/M19-the-codex-fence-does-not-fire-in-exec-mode.md, this
repository, referenced in README.md).

Licence and pricing, snapshot read 2026-08-11: fully open source, Apache
License 2.0: "Copyright 2025 OpenAI Licensed under the Apache License,
Version 2.0"
([github.com/openai/codex/blob/main/LICENSE](https://github.com/openai/codex/blob/main/LICENSE)).
This is a real, load-bearing difference from Claude Code and GitHub
Copilot, both proprietary. Pricing, double-sourced from
[learn.chatgpt.com/docs/pricing.md](https://learn.chatgpt.com/docs/pricing.md):
Free is $0/month; Go is $8/month; Plus is $20/month; Pro is "from
$100/month" for 5x usage, more for 20x; Business is $20 per user per month
billed annually ($25 per month billed monthly); Enterprise and Edu are
custom. A separate API-key path bills "only for the tokens Codex uses,
based on API pricing," a genuinely usage-based alternative to the
subscription tiers.

Concrete handoff seam: AGENTS.md, a plain-text instructions file Codex
reads automatically, merged across tiers, a global `~/.codex/AGENTS.md`,
then every directory from the git root down to the working directory. A
spec written by any process, human or another tool, becomes Codex's
working brief the moment it is dropped into the tree. Codex is also an MCP
client (`codex mcp add`, or `[mcp_servers.<name>]` in
`~/.codex/config.toml`) over local stdio or remote Streamable HTTP
([learn.chatgpt.com/docs/agent-configuration/agents-md](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
[learn.chatgpt.com/docs/extend/mcp](https://learn.chatgpt.com/docs/extend/mcp)).
A claim that Codex can also act as an MCP server itself (`codex
mcp-server`) appeared only in secondary sources and was not confirmed
against a primary OpenAI doc page, so it is UNVERIFIED.

## 5. Cline

Cline is a free, open-source AI coding agent that runs as an extension
inside VS Code (also available as a CLI and an SDK), reading and editing
files and running terminal commands using a model API key the user
supplies ([cline.bot](https://cline.bot/), [docs.cline.bot](https://docs.cline.bot/)).

What it is genuinely best at: a deliberate "think before you act" workflow
paired with the widest bring-your-own-key model choice of any tool
reviewed here. Its own docs list 30 or more named providers on top of a
core set (Anthropic, OpenAI, Google, AWS Bedrock, Azure, OpenRouter), so a
team is never locked into one vendor's model
([docs.cline.bot/provider-config/other-30-plus-providers](https://docs.cline.bot/provider-config/other-30-plus-providers),
[docs.cline.bot/sdk/model-providers](https://docs.cline.bot/sdk/model-providers)).
Its own docs, cross-checked against an independent third party (DeepWiki),
describe the workflow: "Plan mode is where you and Cline figure out what
you're building and how. In this mode, Cline can read your codebase, run
searches, and discuss strategy, but cannot modify any files or execute
commands" ([docs.cline.bot/features/plan-and-act](https://docs.cline.bot/features/plan-and-act)).

Where it beats BrotherMode: Cline is itself fully open source (Apache
2.0), so anyone can audit, fork, or extend its enforcement logic directly,
and its "explicit approval" gate is a first-class, built-in mechanic rather
than an add-on hook a user has to wire up. Both are real differences from
BrotherMode, which is a layer of rules and hooks sitting on top of a closed
runtime. Worth noting precisely: approval is the default in Cline, but it
also ships an opt-in "Auto Approve" feature letting a user bypass approval
per action type, so it is approval-by-default with a configurable escape
hatch, not an absolute invariant
([docs.cline.bot/features/auto-approve](https://docs.cline.bot/features/auto-approve)).

Licence and pricing, snapshot read 2026-08-11: Apache 2.0, open source,
confirmed independently on both [cline.bot](https://cline.bot/) and
[github.com/cline/cline](https://github.com/cline/cline). Cline itself
carries no subscription fee; cost is entirely the model API key the user
brings. No separate Cline-hosted subscription pricing page was found.

Concrete handoff seam: Cline is an MCP client: "Users can connect Model
Context Protocol servers to access databases, APIs, and cloud
infrastructure, plus use community-built servers or request custom tools."
It also ships as an SDK and a CLI, both explicit programmatic and CI
handoff surfaces beyond the editor extension
([github.com/cline/cline](https://github.com/cline/cline)).

## 6. superpowers and the Claude Skills ecosystem

Two related but distinct things: Anthropic's official Agent Skills
mechanism in Claude Code, and obra/superpowers, the most-used community
skills package built on top of it. Agent Skills is Claude Code's official
way to package a reusable procedure, a markdown instructions file plus
optional scripts, that Claude loads only when it is relevant. superpowers
is a free, community-built library of such skills implementing a specific
development methodology (test-driven development, systematic debugging,
structured code review) on top of that mechanism
([code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills),
[github.com/obra/superpowers](https://github.com/obra/superpowers)).

What it is genuinely best at: Skills' own strength is progressive
disclosure, only a skill's name and one-line description, roughly 100
tokens, sit in context until it is triggered, so a project can carry dozens
of long procedures at near-zero ongoing cost. Quoted from
[platform.claude.com/docs/en/agents-and-tools/agent-skills/overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview):
"Progressive disclosure ensures only relevant content occupies the context
window at any given time." superpowers' own strength is enforcing one
specific, opinionated practice, refusing code written before a failing
test exists, more strictly than a general-purpose tool asks for by default,
and it is the most widely adopted third-party skill pack for Claude Code:
270,311 stars and 24,163 forks, read from the GitHub API on 2026-08-11
(single-sourced, GitHub is the sole authority on its own star count).

Where it beats BrotherMode: superpowers is free, MIT-licensed, and has an
active community of contributors and 270,000-plus stars behind it, a scale
of peer review and usage no single-founder project can match. It also
enforces one practice, test-first development, with a hard refusal of code
before a failing test exists, more strictly than BrotherMode's own broader
law set asks for by default. BrotherMode's own comparison page already
concedes exactly this tradeoff (docs/market/CATEGORY.md, this repository).

Licence and pricing, snapshot read 2026-08-11: the Skills format itself
follows "the Agent Skills open standard," per
[code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills),
an open specification, though the underlying agentskills.io standard page
itself was not independently opened in the underlying research, so that
governance detail is UNVERIFIED. Claude Code, the runtime skills run on, is
proprietary as described in section 1. superpowers is MIT licensed,
confirmed both by GitHub's API (license MIT, actively pushed as of
2026-08-08) and by its own plugin.json declaring `"license": "MIT"`.
Neither carries a separate price; cost is whatever the underlying Claude
Code session already costs.

Concrete handoff seam: a skill folder becomes a full plugin the moment it
carries a `.claude-plugin/plugin.json` file: "it loads as a plugin named
`<name>@skills-dir`, so it can bundle agents, hooks, and MCP servers"
([code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills)).
That is the literal mechanism BrotherMode itself already is: a
SKILL.md-based plugin bundling its own hooks. superpowers is distributed
the same way, through a plugin marketplace (`/plugin marketplace add`,
then `/plugin install`).

## 7. The orchestration layers

The six tools above are coding agents and the skills that ride them. There
is a closer category: multi-agent orchestration and process layers that,
like BrotherMode, sit on top of a coding agent rather than replace it. This
section was researched separately from the six-tool pass above: every
number below was read live on 2026-08-15 from the GitHub API or the
project's own repository pages, and carries that date rather than the
page-level date at the bottom.

- **Ruflo** ([github.com/ruvnet/ruflo](https://github.com/ruvnet/ruflo)),
  formerly claude-flow, renamed around its v3.5 (reported by third-party
  coverage; the rename is visible on the repository itself). 67,852 stars,
  v3.38.9, releasing near daily as of 2026-08-15. Swarm topologies, vector
  memory, a plugin catalog, multi-provider routing, and a cost-tracker
  plugin. Where it beats BrotherMode: sheer feature surface, shipping
  velocity, and reach. What its own release notes disclosed, quoted from
  its releases feed on 2026-08-15: a memory search labeled HNSW had been
  brute-force cosine similarity for an unspecified period, and explicit
  provider configuration was being silently discarded on some execution
  paths, both since fixed. No documented per-file ownership or conflict
  mechanism was found in its README.
- **BMAD-METHOD**
  ([github.com/bmad-code-org/BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD),
  the org renamed from bmadcode): 51,907 stars, v6.11.0. A full-lifecycle
  role framework whose web bundles let non-technical people join planning
  conversations. Its coordination is durable context passed between
  sessions and human-mediated collaboration, deliberately not an enforced
  mechanism.
- **spec-kit** ([github.com/github/spec-kit](https://github.com/github/spec-kit)):
  128,294 stars, v0.16.4, official GitHub backing, executable specs for a
  whole organization. Its README does not document multi-session
  coordination or concurrent-write handling.
- **claude-task-master** and **affaan-m/claude-swarm** appear in
  orchestration comparisons but were stale or dormant when read
  (last releases 2026-03-31 and 2026-02-11 respectively); treating them as
  active competitors would overstate the field. A related caution from the
  same pass: parruda/claude-swarm and its successor repository both
  returned 404 while RubyGems still listed them as live source links, so
  any comparison built from package indexes rather than opened pages would
  have profiled a ghost.
- **metaswarm** ([github.com/dsifry/metaswarm](https://github.com/dsifry/metaswarm)):
  392 stars, v0.12.0, a nine-phase TDD-gated workflow across three coding
  CLIs, with coverage thresholds blocking before a pull request. No cost
  controls documented.

What none of them documented, checked project by project on 2026-08-15: an
enforced one-writer-per-file mechanism (affaan-m/claude-swarm's pessimistic
locks come closest, in a dormant project), a session handover ceremony with
a close check that refuses hollow packs, forecast calibration from recorded
history that refuses to guess below a data floor, or a progress page built
for a non-engineer. Those are BrotherMode's actual differentiators in this
category, and the honest counterweight from the same pass: every project
above beats BrotherMode on adoption and reach, and BrotherMode's recorded
external install count is zero. The comparison in both directions, with the
full table, lives in docs/plan/FINALIZATION-ROADMAP-2026-08-15.md and the
numbers decay like everything else on this page: re-read them before citing
them.

## Using them together

A single piece of work can move through several of these tools, and the
handoff at each stage is a real, named artifact, not a hand-wave.

Start in **OpenAI Codex CLI**'s plan mode, or in **Claude Code** doing what
its own docs describe as planning "the approach" before writing code. Codex
writes its brief to AGENTS.md at the git root, the file Codex itself reads
automatically on every future run
([learn.chatgpt.com/docs/agent-configuration/agents-md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)).
That file is plain text, so it is also readable by a human, by Claude Code,
or by any other tool in this list; it is the seam, not a Codex-only
artifact.

If the team works from GitHub Issues rather than a local plan file, the
same brief becomes an issue body and gets assigned to **GitHub Copilot**
coding agent, which runs its own named stages, "research, plan, and
iterate," in an ephemeral GitHub Actions environment, and returns the
result as a pull request against a branch
([docs.github.com/en/copilot/concepts/agents/coding-agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent)).
A pull request is the universal handoff every tool in this list
understands, so this is where paths reconverge regardless of which agent
did the work.

For interactive implementation with a human present, **Cursor** or
**Cline** pick up the same branch in an editor: Cursor's Agent works
against the checked-out branch with local checkpoints layered on top of
git for undo, explicit that checkpoints are not a git substitute
([cursor.com/docs/agent/overview](https://cursor.com/docs/agent/overview));
Cline's Plan mode discusses the approach before switching to Act mode to
execute it, against the same branch, via its own MCP-connected tools
([docs.cline.bot/features/plan-and-act](https://docs.cline.bot/features/plan-and-act)).

Where **BrotherMode** enters is on top of a **Claude Code** session working
that same branch: it turns the plan into a file list with a single writer
per file, and it will not let the session call the work done until a
verifying command has been run after the last edit and its output quoted.
The handoff into BrotherMode is the same Claude Code plugin and hooks
mechanism that Skills and superpowers already use, `.claude-plugin/plugin.json`
loading hooks against Claude Code's own hook surface
([code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills)).
A team wanting superpowers' test-first discipline instead of, or alongside,
BrotherMode's broader law set installs it through the identical plugin
marketplace mechanism, `/plugin marketplace add` then `/plugin install`
([github.com/obra/superpowers](https://github.com/obra/superpowers)).

Whichever tool did the work, the pull request is where it lands for
review, and git branches and commits are the one substrate every tool in
this comparison writes to and reads from, whatever it calls its own
internal stages on the way there.

## What we could not verify

- Cursor's exact current Pro+ and Ultra prices. Two independent fetches
  disagreed, one read them as multipliers off the $20 base, another read
  flat $60 and $200 figures from secondary sources. This needs a fresh
  direct read of cursor.com/pricing before either specific number is
  published with confidence.
- Cursor's proprietary and closed-source status. Widely believed true, but
  in the underlying research it traces only to a secondary blog, not an
  opened primary Cursor statement.
- Claude Code's and superpowers' non-goals. Neither carries a dedicated
  non-goals or scope-limits page; that is stated here as a documentation
  gap, not filled in from memory.
- Whether OpenAI Codex CLI can act as an MCP server itself (`codex
  mcp-server`), not just a client. This appeared only in secondary
  sources; the primary GitHub doc page was not independently opened.
- The Agent Skills open standard's own governance page (agentskills.io)
  was not independently opened, so the claim that Skills "follows" an
  open, cross-tool standard rests on Anthropic's own docs page alone.
- GitHub Copilot Business and Enterprise exact per-seat pricing was not
  found on GitHub's own plans page in the time available; a secondary
  aggregator's figures were seen but deliberately left out of this page.
- None of the vendor pricing pages read for this page carried a visible
  "last updated" date. Every price above is a snapshot taken on
  2026-08-11, not a dated, stable citation.
- Nothing on this page was hands-on tested. No session was actually run
  against any of the six tools; every claim above is documentation-derived,
  not behaviorally verified.

## Last checked

This page was last checked against primary sources on 2026-08-11. It is
re-checked weekly; if you are reading this much later than that, treat the
prices in particular as stale and verify them yourself before relying on
them.

The line below is the one a check reads, so it stays in this exact shape.
Do not update it without running the pass in docs/ECOSYSTEM-REFRESH.md:
a fresh date over stale content is worse than an honest old one.

Last checked: 2026-08-11.
