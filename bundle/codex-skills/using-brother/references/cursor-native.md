# Cursor-native agent personas

Three Cursor-native agents ship inside `bundle/agents/`: `brother-planner`,
`brother-executor`, `brother-reviewer`. Cursor's own plugin agent
frontmatter carries only `name` and `description`; it is a routing hint,
not a permission system. Every hard limit these agents keep is written
into the agent's own body text, not into frontmatter Cursor could enforce
on our behalf.

## Honest limit

A prompt-level denylist and a preflight classifier reduce silent scope
violations and force disclosure; they cannot guarantee prevention the way
a tool-layer block can. Cursor gives plugin agents no permission system of
their own, so treat each agent's hard limits as a strong instruction it is
told to keep, not as proof it is unable to merge, push, tag, or release.
The one thing that reliably enforces this here: no agent is ever wired to
a credential or a remote that could do those things, because merge, push,
tag, and release stay the founder's own click on every Brother surface.

## Reading them

- `brother-planner`: explores read-only, returns goal, files to change,
  ordered steps, verification commands, and what is out of scope. Never
  edits a file or runs a mutating command.
- `brother-executor`: implements only the plan's declared file scope,
  classifies every shell command before running it (read-only, or the
  plan's own test/lint/build, or refused), and stops to report a blocked
  action instead of working around it.
- `brother-reviewer`: reads the diff against the plan it claims to
  implement, flags scope creep and any mutation attempt as Critical,
  never edits and never proposes a fix.

Ways of working map onto Cursor's own IDE Agent modes: Plan mode is the
planner, Agent mode is the executor, Ask mode stays read-only (`status`
and `list`, never a write that claims a result).

## Declaring them

`bundle/.cursor-plugin/plugin.json` lists `"agents": ["./agents/"]`
alongside the existing `skills` and `commands` entries so Cursor's plugin
loader can find them. `scripts/cursor_plugin_install.py validate` checks
that all three files exist, that each carries exactly `name` and
`description` frontmatter, and that no shipped agent or rule names an
outside model vendor.
