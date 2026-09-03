# Plugin interoperability

This page names the minimum a Claude Code plugin owes the rest of somebody's
setup: it must not claim a name another plugin already uses, must not write
where it was not asked to write, must not read what belongs to someone else's
session, and must keep working when the pieces around it are absent. It does
not describe a compatibility framework, because none was built for this: LT-502
is documentation and tests over guarantees the codebase already keeps, not new
machinery.

Every claim below carries one of two labels, stated on its own line so neither
reads as the other:

- **PROVEN BY TEST**, naming the exact test in
  `tools/test_sbe_interop.py` that fails if the claim stops being true.
- **DOCUMENTED CONTRACT, no mechanical check.** Either the claim depends on
  Claude Code's own runtime behavior (which this repository cannot drive in a
  test), or on a human following a documented step (SETUP.md's manual paste),
  and no test here pretends otherwise.

Run the fixtures yourself:

```
python3 tools/test_sbe_interop.py
```

---

## 1. Every skill is namespaced

BrotherSBE ships fourteen skills. Nine are invoked by name (`skills/adopt`,
`handover`, `help`, `learn`, `next`, `review`, `start`, `status`, `verify`).
Five fire on the situation instead, so a person who has started nothing still
receives guidance in the session they were already in (`kickoff`, `design`,
`work`, `prove-this-change`, `spec-and-data-prep`). `kickoff`, `design` and
`work` moved into this group in the six-verb command surface redesign, since
`start` already routes into them and a person no longer has to learn a
separate command to reach them.
Claude Code's own plugin harness prefixes every one of them with the plugin
name declared in `.claude-plugin/plugin.json` (`"name": "brothersbe"`), so the
real invocation is always `brothersbe:<skill>`, never the bare skill name.
That prefixing is the platform's job, not this repository's code, which is
exactly why the second half of this guarantee exists: every skill's own
`description` frontmatter states its fully namespaced form in plain text
("Invoke as /brothersbe:review."), so a person reading the skill, not only the
harness's own UI, sees the correct spelling. `agents/*.md` (seven read-only
reviewer subagents, tools `Read`, `Grep`, `Glob`, `Bash` only, plus one
`implementation-worker` agent that additionally carries `Edit` and `Write`
and writes to a user's files inside its declared scope; BrotherSBE's own
skills dispatch all eight, none is invoked directly by a user) are namespaced
the same way by the same harness mechanism and are not separately tested
here, because nothing in this repository claims a bare agent name as a
user-facing command.

Status: PROVEN BY TEST, `TestSkillsNamespaced::test_every_skill_declares_its_namespaced_invocation`.
The harness's own prefixing behavior (that `brothersbe:review` and not `review`
is what actually resolves) is DOCUMENTED CONTRACT, no mechanical check: this
repository has no way to drive Claude Code's plugin loader in a unit test.

## 2. No generic command is claimed outside the namespace

Nowhere in this repository does a skill description, a doc, or a README claim
a bare, un-prefixed slash command (`review`, `work`, `status`, or any of the
other nine skill names, each without a leading slash here on purpose, so this
very sentence does not itself read as the claim it is warning against) as
something a user types without the `brothersbe:` prefix. A plugin that
claimed the review skill's command outright, with a leading slash and no
namespace, would collide with any other plugin that also ships a review
skill; BrotherSBE never does, on purpose.

Status: PROVEN BY TEST, `TestNoBareCommandClaims::test_no_generic_slash_command_claimed_in_docs_or_skills`.

## 3. No global user settings are overwritten

`install.sh`, `bin/sbe`, and every module under `src/brothersbe/` (the code
path a real installation actually runs) contain zero references to
`settings.json` anywhere in their source. Installation writes exactly two
kinds of state: the project-local footprint `sbe init` creates inside the
target repository (`.sbe/`, the dossier root, `.brothersbe/install-receipt.json`,
all inside the project being installed into, per `docs/CLI.md`'s `init` row),
and, on the plugin path, whatever `claude plugin marketplace add` and
`claude plugin install` do on the user's behalf through the Claude Code CLI
itself, which is Anthropic's own tool, not code this repository ships or
controls. Neither ever opens `~/.claude/settings.json`, a project
`.claude/settings.json`, or any other global settings file for a write.

The one place `~/.claude/settings.json` is mentioned at all is
`docs/SETUP.md` step 3 and `docs/HOOKS.md`'s own "Install" section, both of
which show the operator a JSON block to paste **by hand**, for the standalone
(non-plugin) skill installation path. That paste is the human's own edit to
their own file; no script here performs it for them.

Status: PROVEN BY TEST, `TestNoGlobalSettingsWrite::test_install_path_never_references_settings_json`,
for the code path (nothing in the install path can write there because
nothing in it names the file at all). The manual `docs/SETUP.md` paste step is
DOCUMENTED CONTRACT, no mechanical check: it is a human editing their own file
by hand, and no test can observe what a person outside this repository does.

## 4. Hooks are installed only through the approved path

Two hook-registration paths exist, and both are named, not implicit:

1. **The plugin manifest.** `hooks/hooks.json`, loaded automatically by
   Claude Code when `brothersbe@brothersbe` is installed as a plugin. Every
   command it declares is spelled relative to `${CLAUDE_PLUGIN_ROOT}`; none
   of them names an absolute host path, a second install location, or
   `~/.claude/settings.json` directly.
2. **The manual standalone paste**, `docs/SETUP.md` step 3 and
   `docs/HOOKS.md`'s "Install" section, for someone running BrotherSBE as a
   plain cloned skill rather than a registered plugin. This is the same human
   action named in guarantee 3, not a second code path.

There is no third way to get a BrotherSBE hook wired into a session, and the
first path is itself gated: `hooks/**` is one of the nine authority surfaces
`tools/sbe_instruction_surface.py` watches (`CLAUDE.md`, `.claude/**`,
`.mcp.json`, `.claude-plugin/**`, `hooks/**`, `agents/*.md`,
`skills/*/SKILL.md`, `CODEOWNERS`, `.github/workflows/**`; the same list
`docs/THREAT_MODEL.md` and `references/team-execution.md` both name for the
instruction trust model this check enforces, LT-401). An
edit to `hooks/hooks.json` that changes what runs at session start, precompact,
or before a write, and that was not declared up front and bound to an
independent `Approved-by:` or `Reviewed-in:` trailer, FAILs that check by
name. A hook cannot be quietly added or swapped by an ordinary, undeclared
change; the same gate that protects `CLAUDE.md` protects the hook manifest.

Status: PROVEN BY TEST, `TestHooksApprovedPathOnly::test_hooks_manifest_only_references_the_plugin_root`
(every command in `hooks/hooks.json` is `${CLAUDE_PLUGIN_ROOT}`-relative, and
the fixture's walk of this checkout, pruning nested git worktrees, finds no
second hooks manifest) and
`TestHooksApprovedPathOnly::test_an_undeclared_hooks_edit_fails_the_instruction_surface_gate`
plus `test_a_declared_and_reviewed_hooks_edit_passes` (the real
`tools/sbe_instruction_surface.py`, run against a real two-commit fixture
repository, FAILs the undeclared edit and PASSes the declared, reviewed one).

## 5. BrotherSBE coexists with the official GitHub plugin and common MCP tools

BrotherSBE ships zero MCP servers of its own: `.claude-plugin/plugin.json`
declares no `mcpServers` key, and no `.mcp.json` file exists anywhere in this
repository. There is nothing here for a common MCP tool's own server names to
collide with. Its one GitHub integration, `sbe pr verify`
(`src/brothersbe/prverify.py`), reads a token through `gh auth token`, the
same credential the official GitHub plugin and the `gh` CLI already use; it
does not register a competing GitHub App, a competing OAuth flow, or a
competing set of GitHub-shaped skills. `docs/THREAT_MODEL.md` row 14 already
states the same fact from the security side: "this project adds no
integration to compromise."

At the filesystem and namespace level, two plugins installed side by side
never collide by construction: Claude Code stores and loads each plugin under
its own name (from `.claude-plugin/plugin.json`), every BrotherSBE hook
command is `${CLAUDE_PLUGIN_ROOT}`-relative (never a path reaching outside its
own plugin directory), and every BrotherSBE skill resolves as
`brothersbe:<skill>` regardless of what skill names a second plugin happens to
reuse (guarantee 1). A synthetic second plugin built in a temp directory,
alongside a copy of BrotherSBE's own manifest and hooks file, proves this
structurally: neither plugin's declared paths, hook commands, or namespaced
skill names overlap.

What this repository cannot prove in a test: whether Claude Code's own
harness actually merges two plugins' `SessionStart` or `PreToolUse` hooks
correctly at runtime, whether the official GitHub plugin behaves a particular
way, or whether a specific third-party MCP server's tool names happen to
collide with something outside this project's control. Those are the
harness's and the other plugin's own contracts, not this repository's to
assert.

Status: PROVEN BY TEST (the static, filesystem-level half),
`TestCoexistence::test_plugin_manifest_declares_no_mcp_servers_of_its_own` and
`TestCoexistence::test_a_representative_companion_plugin_installs_beside_it_without_collision`.
The runtime-merging half (how Claude Code itself combines two plugins' hooks
and skills at session start) is DOCUMENTED CONTRACT, no mechanical check.

## 6. Absence of a companion plugin degrades to a documented CLI fallback

Quoting the spec of record's own wording for this guarantee (lean plan
section 15, LT-502): "absence of a companion plugin degrades to a documented
CLI fallback." BrotherSBE does not actually depend on any companion plugin to
function at all: `bin/sbe` and every module under `src/brothersbe/` reference
`CLAUDE_PLUGIN_ROOT` nowhere, so the CLI runs identically whether or not the
BrotherSBE plugin itself is registered, and whether or not any other plugin is
present. `docs/CLI.md` is the standing reference for the full command surface;
`bin/sbe doctor`, `bin/sbe status`, `bin/sbe review <dossier>`, and every other
row in that file's table work from a plain clone with no `claude plugin
install` step at all, exactly as `bin/sbe`'s own module docstring says
("No install step, on purpose... Put this directory on your PATH, or call it
by path").

Status: PROVEN BY TEST, `TestCLIFallbackDocumented::test_the_cli_fallback_is_documented_and_the_commands_are_real`
(this page names `bin/sbe` and `docs/CLI.md`, `docs/CLI.md` exists, and a
sample of the commands this page names are real rows in its table). Whether a
person actually reaches this page when a companion plugin is missing is
DOCUMENTED CONTRACT, no mechanical check: nothing here can observe what a
person reads.

## 7. BrotherSBE does not inspect other plugin conversations

Every read this repository's tools perform against a "conversation" is scoped
to the CURRENT session's own transcript, and only when the harness itself
hands over the path: `tools/sbe_telemetry.py` reads `payload.get("transcript_path")`
out of the JSON the PreCompact or SessionEnd hook receives on stdin, never a
path it discovers itself. Nothing in `src/`, `tools/`, or `hooks/` globs,
walks, or opens `~/.claude/projects`, `~/.claude/history`, or any other
location where Claude Code or another plugin stores a session transcript that
was not handed to this one, this way, for this invocation. Every other
directory walk or glob in this codebase (`tools/sbe_checks.py`'s
evidence-pattern matcher, `tools/sbe_design.py` and `tools/sbe_gate.py`'s
dossier walks, `tools/sbe_telemetry.py`'s own vault globs) is rooted at
either the project under
review, or `BROTHERSBE_VAULT` (default `~/BrotherSBEVault`, a folder the
operator chooses, and never `~/.claude`), never at another plugin's storage.

Status: PROVEN BY TEST, `TestNoOtherPluginConversationReads::test_no_python_source_string_names_another_sessions_store`
and `test_no_shell_source_line_names_another_sessions_store` (an AST walk of
every string literal in `src/brothersbe/*.py`, `tools/*.py`, and `hooks/**`,
plus a comment-stripped scan of every shell script in the same tree, for any
of the known Claude Code conversation-storage markers).

## 8. One-writer-per-file defers to the companion when it is present

`docs/adr/2026-08-12-where-the-shared-machinery-lives.md`, ACCEPTED, decided
that L13's one-writer-per-file boundary belongs to the companion product, not
to this one, because a write collision is a property of concurrent sessions
rather than of a change's assurance. Before that decision, both products
shipped their own `PreToolUse` fence hook over the same markdown registry
format (`tools/sbe_fence_hook.py` here, `bm_fence_hook.py` there), and with
both installed, two independently written parsers decided the same write
event without either noticing the other.

**Standalone is unchanged.** With no companion detectable, `tools/sbe_fence_hook.py`
decides exactly as it always has: this document's other guarantees, and
`tools/test_sbe_fence_hook.py`'s `TestGenuineConflictIsRefused` and
`TestFailOpen` classes, are unaffected by this section.

**The signal.** `detect_companion()` in `tools/sbe_fence_hook.py` reads
exactly the files Claude Code itself reads to decide which hooks run this
session: the global `settings.json` and `settings.local.json` under
`CLAUDE_CONFIG_DIR` (default `~/.claude`), the project's own
`.claude/settings.json` and `.claude/settings.local.json`, and, for a
companion installed as a plugin rather than pasted by hand, the enabled
plugin's own cached `hooks/hooks.json`, resolved through the harness's own
`installed_plugins.json`. Those are the same two install paths guarantee 4
above already documents for THIS hook's own installation (a manual paste, or
a plugin manifest auto-loaded because `settings.json` marks it enabled), so
the check mirrors a boundary this project already treats as authoritative
rather than inventing a new one. No other channel exists to ask the
question: `CLAUDE_PLUGIN_ROOT` names only the calling plugin's own root,
never a sibling plugin's, so a settings and plugin-registry read is the only
honest source.

**What it misses, named rather than hidden:** a companion wired through a
project settings file outside the `cwd`/`project_dir` the hook payload
carries; a config directory this process cannot find because
`CLAUDE_CONFIG_DIR` was not propagated into the hook's own environment; a
companion fork that renamed its fence hook script away from
`bm_fence_hook.py`; a plugin installed by a path that never wrote an
`installed_plugins.json` record. Every one of those degrades to the hook
deciding for itself, standalone-style, never to a guess that the companion is
present.

**Deference is never silent.** When the signal is PRESENT, the hook allows
the write and its one stderr note names both facts: that it deferred, and
what it detected (`bm_fence_hook.py`, and where). Claude Code parses stdout
as the decision channel, so, as with every other note this hook prints, the
deferral note goes to stderr and stdout carries nothing.

**"Could not tell" is its own state and is never treated as "present".** A
settings file that exists and will not parse is NO-DATA, not ABSENT and not
PRESENT: the hook keeps enforcing its own fences exactly as it does
standalone, and says on stderr that the signal was inconclusive and why.
Treating an inconclusive read as "the companion must be here" would be the
one way this section could silently drop L13 enforcement; treating it as
"the companion is not here" would be a quieter version of the same mistake,
which is why NO-DATA is answered with the safer of the two decisions
(keep deciding) rather than with a guess in either direction.

Status: PROVEN BY TEST, in `tools/test_sbe_fence_hook.py`'s
`TestCompanionDeference` class rather than in `tools/test_sbe_interop.py`,
because the behavior lives inside the fence hook itself: with no companion
wired, `TestGenuineConflictIsRefused` already covers standalone parity;
`test_with_the_companion_wired_the_hook_defers_and_says_so` and
`test_the_deferral_path_writes_no_refusal` cover deference and its silence
requirement; `test_no_data_when_the_signal_cannot_be_read_keeps_this_hook_deciding`
and `test_no_data_is_reported_not_silent` cover the NO-DATA safety property;
`test_calibration_an_always_present_detector_makes_the_genuine_conflict_case_go_red`
inverts the detector and confirms the genuine-conflict property is actually
load-bearing rather than a test that would pass against a broken detector.
Whether the companion's own tooling reads this hook's deferral note the same
way is DOCUMENTED CONTRACT, no mechanical check: that is the companion's own
behavior, not this repository's to assert.

---

## Doctor: branch taken

`sbe doctor` (`src/brothersbe/cli.py::_doctor_checks`) is a fixed, hand-written
Python list of `(name, result, detail)` tuples, not a discoverable check
registry the way `tools/sbe_checks.py`'s `CHECKS` dicts are. Adding an
interoperability row there means editing `src/brothersbe/cli.py`, which sits
outside LT-502's file boundary (documentation and tests only: `docs/INTEROPERABILITY.md`,
`tools/test_sbe_interop.py`, `docs/KNOWN-LIMITS.md`, `CHANGELOG.md`) and would
reopen code review on the doctor command for a change this task was not
scoped to make. Branch taken: **document the contract instead of extending
doctor.** To check interoperability today, run `python3 tools/test_sbe_interop.py`
directly, or read this page. A future task that owns `src/brothersbe/cli.py`
can decide whether a doctor row is worth the added surface.

## Verify these claims yourself

```bash
# guarantee 1 and 2: every skill's own invocation line, and nothing else
# claiming a bare command
grep -n "Invoke as /brothersbe:" skills/*/SKILL.md

# guarantee 3: the install path never names settings.json
grep -rn "settings.json" install.sh bin/sbe src/brothersbe/*.py

# guarantee 4: every hooks.json command is plugin-root relative
grep -n "CLAUDE_PLUGIN_ROOT" hooks/hooks.json

# guarantee 5: no MCP servers of its own
python3 -c "import json; print('mcpServers' in json.load(open('.claude-plugin/plugin.json')))"

# guarantee 6: the CLI has no plugin-runtime coupling
grep -rn "CLAUDE_PLUGIN_ROOT" bin/sbe src/brothersbe/*.py

# guarantee 7: transcripts are read from the handed-over payload, never
# discovered by walking another session's store
grep -n "transcript_path" tools/sbe_telemetry.py

# guarantee 8: the companion's fence hook basename is the one fact this file
# is allowed to know about it, and detection degrades to the hook deciding
# for itself rather than guessing
grep -n "COMPANION_HOOK_BASENAME\|CompanionSignal.NO_DATA" tools/sbe_fence_hook.py
python3 tools/test_sbe_fence_hook.py TestCompanionDeference
```

None of these should surprise you after reading the sections above; that is
the point of naming them here instead of asking you to trust the prose.
