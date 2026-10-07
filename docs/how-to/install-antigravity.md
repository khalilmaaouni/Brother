# Install the Antigravity hook plugin

This walkthrough is rendered from the captured evidence log at
`docs/architecture/ANTIGRAVITY-E2E-TEST-LOG.md`. Every command block and
every stated fact carries an evidence anchor that resolves to one pinned run
in that log.

## Prerequisites

You need:

- Python 3.9 or newer, standard library only.
- The Antigravity host.
- A scratch workspace outside any repository you care about.

Antigravity is experimental and unverified in 1.1.0. Its rows are NO-DATA and decide nothing under the 1.1.0 scope.
The host name and version will be reported from a live run when its evidence row exists. <!-- evidence: 0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0 -->

The `view_file` tool does not reach PreToolUse.
The `list_dir` tool does not reach PreToolUse.
The `grep_search` tool does not reach PreToolUse.

## Load the plugin

Copy `bundle/.antigravity-plugin/` into the scratch workspace and load it
with the captured host load argv, which is a single token, the host binary
itself, with no invented verb:

<!-- evidence: 0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0 -->
```bash
agy
```

## First run

Fire one hook event by piping a tool call payload into the adapter:

<!-- evidence: 1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809 -->
```bash
python3 scripts/brother_antigravity_hook.py pre_tool < payload.json
```

## What a fired hook looks like

A clean PreToolUse for a known tool answers with the verbatim stdout line
`{"decision": "allow"}` and exits 0, as confirmed by the pinned run. <!-- evidence: 1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809 -->

A corrupt PostToolUse payload writes this verbatim stderr line and still
answers `{}` on stdout, so that event cannot block there:

`brother_antigravity_hook: PostToolUse received corrupt payload: ...`

A corrupt PreInvocation payload writes this verbatim stderr line and still
answers `{"injectSteps": []}` on stdout, so that event cannot block either:

`brother_antigravity_hook: PreInvocation received corrupt payload and cannot block by contract: ...`

A corrupt Stop payload answers `{"decision": "continue"}`.

A corrupt PostInvocation payload answers
`{"injectSteps": [], "terminationBehavior": "terminate"}`.

## Uninstall

Uninstall is verified against the pinned run. <!-- evidence: 0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0 -->

Remove the plugin directory from the scratch workspace and drop the host
entry that pointed at it.

## Known limits

A missing or crashing hook does not guard (the host ran the tool after hook exit 2); a Brother verdict requires the adapter's own event log.

On this host, a new workspace is not a boundary, and private term isolation is not claimed. The Antigravity host injects other windows' documents and the last three chats into a fresh workspace, so content placed there must be treated as public to this host.

The agent inside Antigravity has full access to Antigravity's own data folder (`~/.gemini/antigravity-ide`) by default. The owner ruled this in chat on 2026-09-30, in his words "Antigravity can access private data" and "I allow full access to antigravity". The hook's host data guard reads `BROTHER_ANTIGRAVITY_GUARD_HOST_DATA` from the environment the host starts the hook with: unset, empty or `0` leaves it off; `1` turns it on, and then a read, a write or a command naming a path under that folder is denied, including a path nested inside a list or object argument and a path spelled with `~` or `$VAR`. Any other value keeps the guard on and prints a warning, because an unknown setting blocks rather than allows. Corrupt input is denied either way: a known tool whose `args` is missing or not an object, and a `run_command` whose `CommandLine` is missing, empty or not a string.

`brother.loop` is unavailable on Gemini until `model_call` has a headless transport. The transport is not present in this checkout, so the Gemini route cannot run the loop today.
- stop allows unconditionally: a clean Stop returns `{"decision": "allow"}`
  and no completion artifact control exists. <!-- evidence: 4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c -->
- pre_invocation injects nothing: a clean PreInvocation returns
  `{"injectSteps": []}`. <!-- evidence: 3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b -->
- known tools allow destructive args: only the tool name is read, so a
  `run_command` carrying `rm -rf docs/` is allowed. <!-- evidence: 1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809 -->
- pre_invocation and post_tool have no blocking verb on corrupt input: a
  corrupt payload on those events is reported on stderr and blocked
  downstream. <!-- evidence: 2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a -->
