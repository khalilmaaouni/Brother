# Brother hooks under Codex: what maps, what does not, and which gate then reads NO-DATA

Ship gate 3 of the Codex workstream (board row C3). One row per lifecycle hook
event the two products register, the Brother control that event carries, the
Codex equivalent, and where a hard gate must report NO-DATA rather than a pass.

Everything below was measured on this machine on 2026-09-04 against the
app-bundled Codex CLI, `/Applications/ChatGPT.app/Contents/Resources/codex`,
`codex-cli 0.153.0-alpha.5`. The PATH `codex` (0.146.0 from npm) was not used
for any of it. Nothing here is a projection from a release note.

## The one fact that decides every row

Codex has hooks. Codex plugins do not.

    $ /Applications/ChatGPT.app/Contents/Resources/codex features list
    hooks                                    stable             true
    plugin_hooks                             removed            false
    plugins                                  stable             true

(run with `CODEX_HOME` pointed at a throwaway directory, so the founder's own
`~/.codex` was read-only for this measurement and nothing was written into it.)

Measured 2026-09-05, with the brothermode plugin installed beside brother
(commit 07179111, because the loop engine ships in `products/brothermode`):
Codex's `hooks/list` app-server method DOES return that plugin's own
`hooks/hooks.json` entries, marked `"source": "plugin"`, `"trustStatus":
"untrusted"`, alongside a warning that Codex is clamping its 30 second
`SessionEnd` timeout to 3 seconds. Those entries never fire, because nothing
ever trusts them, and `scripts/codex_hooks_install.py` reports them as a
NO-DATA note beside its own PASS rather than failing on a file it never
wrote.

The canonical plugin validator agrees, and refuses a manifest that declares
hooks at all. Scaffolded with Codex's own generator, one `hooks` key added,
then validated with the installed canonical validator:

    $ python3 ~/.codex/skills/.system/plugin-creator/scripts/create_basic_plugin.py \
        hookprobe --path <tmp> --with-hooks --with-skills
    $ uv run --with pyyaml python3 \
        ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py <tmp>/hookprobe
    Plugin validation failed:
    - plugin.json field `hooks` is not accepted by plugin validation
    exit 1

Note also what the scaffold itself does with `--with-hooks`: it creates the
directory and writes NO `hooks` key into `plugin.json`. The generator and the
validator are consistent with the feature flag.

So the shape of the answer for every event is the same, and it is not "Codex
lacks this event":

  - As a HOOK EVENT NAME, Codex supports every event Brother uses. Its
    `hooks.json` wire schema carries `PreToolUse`, `PermissionRequest`,
    `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`,
    `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop` and
    `Interrupt`.
  - As a THING A CODEX PLUGIN INSTALL CAN DELIVER, none of them arrive.
    Installing the Brother Codex plugin installs skills and commands; it
    installs no hooks, because Codex removed the mechanism that would have
    carried them.

## The route that does work, and what now drives it

Codex reads a USER-SCOPE hooks file out of its own home, and everything below
was driven live against the app-bundled binary on 2026-09-04. Row C3 turns
that route from "a user could do this by hand" into something Brother ships:
`scripts/codex_hooks_install.py` writes the file, and then asks Codex to read
it back rather than asserting that it worked.

    $ python3 scripts/codex_hooks_install.py \
        --codex-home <throwaway> --trust --cwd <throwaway-repo>
    codex_hooks_install: wrote <throwaway>/hooks.json: 18 command(s) across
      PostToolUse, PreCompact, PreToolUse, SessionEnd, SessionStart, Stop
    codex_hooks_install: codex hooks/list reports 18 hook(s) from <throwaway>/hooks.json
    codex_hooks_install: PASS: codex reports all 18 hook(s) trusted and enabled
    exit 0

Eighteen commands is every hook registration the two products ship, and the
second and third lines are Codex's own reading of its own configuration, taken
through the `hooks/list` method of `codex app-server`.

### The file format, measured rather than inferred

`<CODEX_HOME>/hooks.json`. The envelope was read off Codex's own parser error
(a document keyed directly by event name is refused with "unknown field
`PreToolUse`, expected `description` or `hooks`"), so the shape is:

    {
      "description": "...",
      "hooks": {
        "PreToolUse": [
          {"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
           "hooks": [{"type": "command", "command": "python3 /abs/tools/x.py",
                      "async": false, "timeoutSec": 10,
                      "statusMessage": "..."}]}
        ]
      }
    }

Three differences from Brother's shipped Claude file, and nothing else:
`timeout` is spelled `timeoutSec`, `async` is written explicitly, and
`${CLAUDE_PLUGIN_ROOT}` must be expanded because a user-scope hooks file is
not inside a plugin. The event names and the `{"hooks": {...}}` envelope are
already identical, which is why the adapter is a translation and not a
rewrite.

### Trust, which is what makes a written hook a running hook

A hook Codex has read is reported `untrusted` and does not run. Trust is
persisted in `<CODEX_HOME>/config.toml`:

    [hooks.state."<sourcePath>:<snake_case_event>:<block>:<hook>"]
    enabled = true
    trusted_hash = "sha256:..."

Driven BOTH WAYS: with the hash Codex itself reported, `hooks/list` reads back
`trustStatus: trusted`; with a hash of zeroes it reads back `modified`, never
trusted. So the trust half cannot be faked by writing the table alone, and
`codex_hooks_install.py --trust` takes the hash from Codex rather than
computing one. `codex --dangerously-bypass-hook-trust` is the per-invocation
alternative, and is what the end-to-end run below used.

### The payload Codex hands a hook, captured from a real turn

Read by wiring a hook that records its own stdin and running a real
`codex exec` turn, not from the binary's string table:

| Event | Fields Codex sends |
| --- | --- |
| `SessionStart` | `session_id`, `cwd`, `hook_event_name`, `model`, `permission_mode`, `source`, `transcript_path` |
| `PreToolUse` | `session_id`, `turn_id`, `cwd`, `hook_event_name`, `model`, `permission_mode`, `tool_name`, `tool_input`, `tool_use_id`, `transcript_path` |
| `PostToolUse` | the PreToolUse set plus `tool_response` |
| `Stop` | `session_id`, `turn_id`, `cwd`, `hook_event_name`, `model`, `permission_mode`, `stop_hook_active`, `last_assistant_message` |
| `SessionEnd` | `session_id`, `cwd`, `hook_event_name`, `reason` |

THE FINDING THAT MATTERS MOST: Codex NORMALISES its own tool names into
Claude's. A model call to Codex's `exec_command` tool reaches the hook as
`"tool_name": "Bash"` with `"tool_input": {"command": "..."}`, byte-identical
to Claude Code's contract. Brother's hooks parse that contract already, so
nothing in them had to change. The refusal channel matches too: the fence
emits `hookSpecificOutput.permissionDecision = "deny"`, and Codex's own wire
types carry `permissionDecision` and `permissionDecisionReason` on
`PreToolUseHookSpecificOutputWire`.

### The write fence, refusing a real Codex turn

The one that matters. A throwaway repository with a BrotherMode store, one
active claim over `claimed.txt` held by another session, and a real
`codex exec` turn driven to attempt an `apply_patch` write to that file. The
model was a local stub provider so the run needed no credentials and no
network; the binary, the hooks, the store and the fence were all real.

    hook: PreToolUse
    ERROR codex_core::tools::router: error=Command blocked by PreToolUse hook:
      BrotherMode fence: claimed.txt is inside the fence of the active record
      cx6-proof (lifecycle d6a31410d47745b4bc802c9bbd0fc4f6, version 1), which
      is owned by session cli-83712dbfa52442f78b551bd201bd3e87. This session is
      bm1-e971edc8c968e9f7c7c67145, so it is not the writer for that path.
    hook: PreToolUse Blocked

`claimed.txt` was unchanged afterwards. The single-writer fence is enforced
under Codex, by Codex, through Codex's own hooks mechanism.

## The mapping

Every row's Brother command is the one in that product's `hooks/hooks.json`.
"Codex equivalent" is the event name Codex's own hook schema uses.

### products/brothermode/hooks/hooks.json

| Event | Matcher | Brother control it carries | Codex event exists | By a Codex plugin install | By codex_hooks_install.py |
| --- | --- | --- | --- | --- | --- |
| SessionStart | (none) | `bm_sessionstart.py`: the baton, the board and the session's opening state | yes, `SessionStart` | NO-DATA | yes |
| SessionStart | (none) | `bm_vault.py refresh`: rebuilds the memory index so recall is current | yes, `SessionStart` | NO-DATA | yes |
| SessionEnd | (none) | `bm_telemetry.py outcomes-append`: the session's outcome record | yes, `SessionEnd` | NO-DATA | yes |
| Stop | (none) | `bm_hookchain.py stop`: end-of-turn chain | yes, `Stop` | NO-DATA | yes |
| PreCompact | (none) | `bm_hookchain.py precompact`: save state before context is compacted | yes, `PreCompact` | NO-DATA | yes |
| PreToolUse | `Edit\|Write\|MultiEdit\|NotebookEdit\|Bash` | `bm_fence_hook.py`: THE SINGLE-WRITER FENCE, the estate's hardest guarantee | yes, `PreToolUse` | NO-DATA | yes |
| PreToolUse | `Edit\|Write\|MultiEdit\|NotebookEdit\|Bash` | `vault_recall_hook.py check`: the recorded lesson shown at the moment of the edit | yes, `PreToolUse` | NO-DATA | yes |
| PreToolUse | `Bash` | `bm_bash_audit.py pre`: the bash write audit's before half | yes, `PreToolUse` | NO-DATA | yes |
| PreToolUse | `Bash` | `bm_session_cap.py`: the concurrent-session cap | yes, `PreToolUse` | NO-DATA | yes |
| PostToolUse | `Bash` | `bm_bash_audit.py post`: the audit's after half | yes, `PostToolUse` | NO-DATA | yes |

### products/brothersbe/hooks/hooks.json

| Event | Matcher | Brother control it carries | Codex event exists | By a Codex plugin install | By codex_hooks_install.py |
| --- | --- | --- | --- | --- | --- |
| SessionStart | (none) | `sbe_sessionstart.py`: the assurance session's opening state | yes, `SessionStart` | NO-DATA | yes |
| SessionEnd | (none) | `sbe_telemetry.py outcomes-append`: the session's outcome record | yes, `SessionEnd` | NO-DATA | yes |
| PreCompact | (none) | `sbe_autosave.py precompact`: autosave before compaction | yes, `PreCompact` | NO-DATA | yes |
| PreCompact | (none) | `sbe_telemetry.py precompact-brief` | yes, `PreCompact` | NO-DATA | yes |
| PreToolUse | `Edit\|Write\|MultiEdit\|NotebookEdit\|CreateDirectory\|Delete\|apply_patch` | `sbe_authority_hook.py`: who is allowed to write this | yes, `PreToolUse` | NO-DATA | yes |
| PreToolUse | (same matcher) | `sbe_fence_hook.py`: the fence, SBE side | yes, `PreToolUse` | NO-DATA | yes |
| PreToolUse | `Bash` | `sbe_bash_write_guard.py`: writes attempted through a shell | yes, `PreToolUse` | NO-DATA | yes |
| Stop | (none) | `sbe_session_reconcile.py`: reconcile claims at end of turn | yes, `Stop` | NO-DATA | yes |

Note the `apply_patch` matcher already present on the SBE side. That is not
Codex support: `products/brothermode/tools/bm_runtimes.py` records the
measurement behind it (Codex CLI 0.146.0, 2026-08-05), that Codex reports every
file write as `tool_name` Bash running `apply_patch`, so a matcher written for
Claude's `Edit`/`Write` tool names does not fire there. Whoever wires these by
hand under Codex must expect the Bash-plus-apply_patch shape, not the Claude
tool names.

## Which hard gate reads NO-DATA, and when it now reads PASS

`hooks-wiring`, the check `sbe doctor` runs, and the only Brother gate whose
verdict is a statement about the hook install itself. On Claude it compares
the shipped `hooks/hooks.json` against the installed plugin copy. Under Codex
there is no installed plugin copy, because that delivery mechanism does not
exist, so that comparison has nothing to compare and a PASS from it would be a
statement about somebody's Claude install wearing this environment's name.

What row C3 adds is the other half. Under Codex the gate now looks for Codex's
OWN hooks file, at the config directory `brother_paths.config_dir()` resolves
(which is `CODEX_HOME` under a Codex client), and the PRESENCE OF THAT FILE
decides the verdict:

  - absent: NO-DATA, naming the path it looked at and the command that would
    wire it. Nothing is wired, and unexamined is never clean.
  - present and carrying every event, matcher and command the shipped file
    declares: PASS. Commands are compared on the part that survives plugin
    root expansion (everything from `tools/` onward), because the Codex file
    holds absolute paths where the shipped file holds `${CLAUDE_PLUGIN_ROOT}`.
  - present and missing one: FAIL, naming the missing command.

Extra hooks in the Codex file are not a failure. That file is shared, and
BrotherMode's own hooks sit in it beside BrotherSBE's.

Implemented in `products/brothersbe/tools/sbe_hooks_wiring.py`
(`hooks_wiring_check`, guarded on `brother_paths.client()` before any other
rung, delegating to `_codex_wiring_verdict`) and driven BOTH WAYS in
`products/brothersbe/tools/test_sbe_doctor_wiring.py`. Four runs over the same
fixture, which is what stops a guard that fires unconditionally in either
direction:

    $ python3 products/brothersbe/tools/test_sbe_doctor_wiring.py \
        TestHooksWiringUnderACodexClient \
        TestHooksWiringUnderACodexClientThatWiredItsOwnHooks
    Ran 6 tests in 2.097s
    OK

The client is simulated with `BROTHER_CLIENT` and the Codex home with
`CODEX_HOME`, both `brother_paths`' own explicit overrides, so no run needs
Codex or Claude installed to mean anything.

## What is still a release blocker after this row

1. NOTHING IS WIRED BY AN INSTALL. `codex_hooks_install.py` is a deliberate
   second step a user or an installer must run, and it refuses the founder's
   own `~/.codex` unless `--allow-default-home` is passed. A Codex user who
   installs the plugin and stops there has skills and commands and no fence.
   Row C5's README block has to say so in the same breath as the install line.
2. TRUST IS A SECOND ACT. A hooks file Codex has read but not trusted does not
   run. `--trust` closes that, and `--dangerously-bypass-hook-trust` closes it
   per invocation, but a wired-and-untrusted home is a real state, and the
   installer reports it as NO-DATA rather than as done.
3. THE END-TO-END PROOF USED A STUB MODEL PROVIDER. The Codex binary, its hook
   machinery, Brother's hooks, the BrotherMode store and the fence were all
   real; the model was a local HTTP stub, because a credentialled Codex
   session in an isolated home was not available to this lane. What is proven
   is that Codex runs Brother's hooks and honours their refusal. What is NOT
   proven is behaviour under a real model's own tool-call shapes beyond
   `exec_command`.
4. ONLY THE HOOKS-WIRING GATE WAS TAUGHT ABOUT CODEX. Every other Brother
   gate that reasons about a Claude plugin install still reasons about a
   Claude plugin install.

## Reproducing every measurement in this document

    /Applications/ChatGPT.app/Contents/Resources/codex --version
    CODEX_HOME=<throwaway> /Applications/ChatGPT.app/Contents/Resources/codex \
        features list | grep -E '^(hooks|plugin_hooks|plugins) '
    python3 ~/.codex/skills/.system/plugin-creator/scripts/create_basic_plugin.py \
        hookprobe --path <throwaway> --with-hooks --with-skills
    uv run --with pyyaml python3 \
        ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
        <throwaway>/hookprobe
    python3 scripts/codex_hooks_install.py --codex-home <throwaway> --trust \
        --cwd <throwaway-repo>
    python3 scripts/codex_hooks_install.py --codex-home <throwaway> --check
    python3 scripts/test_codex_hooks_install.py -v
    python3 products/brothersbe/tools/test_sbe_doctor_wiring.py

`CODEX_HOME` is the isolation variable, named in Codex's own help text
(`codex exec --help`: "--ignore-user-config  Do not load
$CODEX_HOME/config.toml; auth still uses CODEX_HOME"). Every measurement above
was taken with it pointed at a throwaway directory. Proven afterwards: the
founder's `~/.codex` holds no `hooks.json` and no `hooks/` directory, and its
`config.toml` and `AGENTS.md` are byte-identical to their pre-run hashes
(`5c0d37bb0ae67befe198d0ba8f95b3a864e5c8b2` and
`da39a3ee5e6b4b0d3255bfef95601890afd80709`). A whole-directory hash of
`~/.codex` is NOT quoted, and the reason is stated rather than hidden: the
ChatGPT desktop app was running and rewriting its own sqlite databases and
`models_cache.json` throughout, so that hash drifts on its own and would be
evidence of nothing.
