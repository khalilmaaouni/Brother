# The adapter contract, and how a provider is onboarded

Brother has ONE provider-neutral core (`scripts/brother_run.py` and the modules
`scripts/bundle_runtime.py` mirrors into the installed plugin) and THIN adapters
that translate a provider's shape into the core's seams. Nothing in the core
branches on a provider name; `scripts/test_provider_adapter.py` asserts it.

## The capability table

`python3 scripts/provider_adapter.py describe --provider <claude|codex|cortex|all>`
prints, per adapter, one answer for each capability below. An answer is either a
concrete value or a `Refusal(reason)`. A refusal is explicit, never an exception
and never silence, and every consumer turns it into NO-DATA.

| Capability | What the adapter answers |
| :-- | :-- |
| invocation | the binary and the argv shape that runs one task turn |
| worker_call | how the core's model worker seam is reached (`MODEL_WORKER_CMD`, `DOOR_MODEL_CMD`) |
| hook_events, tool_events | the event names the host fires, and the ones it clamps or lacks |
| sandbox_grants | how a write root is granted (Codex: `sandbox_workspace_write.writable_roots`) |
| auth_discovery | where a signed-in state is detected, never its content |
| capability_discovery | the command that lists what the host supports |
| paths | the config home and the plugin cache layout, via `scripts/brother_paths.py` |
| install, upgrade, rollback, uninstall | the verbs of `scripts/brother_install.py`, or a refusal |
| resume | how an interrupted run continues (`brother_run.py --continue`) |

## The conformance suite, unchanged across adapters

`python3 scripts/adapter_conformance.py --provider <p> [--offline] [--evidence-dir DIR]`
runs the SAME twelve steps for every adapter and writes whole outputs plus
`<evidence-dir>/<p>/summary.txt`:

1. install; 2. same task (the mathlib toy through the core with deterministic
seams); 3. baseline red (`check_passed_before` false per file); 4. real changed
files; 5. receipt at a DURABLE path with per-file command, exit code and output;
6. exit semantics (0 on green, nonzero on a still-red check); 7. resume after a
kill; 8. install again (idempotent); 9. upgrade; 10. rollback; 11. uninstall;
12. second uninstall: the tool itself reports `NO-DATA: nothing of Brother's is
installed` at exit 0, and the STEP passes on exactly that observation.

FAIL conditions: a receipt under `/tmp`, `/private/tmp` or the process temp dir;
`check_passed_before` true; an unchanged file; a green run that exits nonzero.
NO-DATA conditions: a refused capability, `--offline` for the lifecycle verbs, a
missing `brother_install.py`, an unmeasured provider. Verdict per provider: FAIL
on any FAIL, else NO-DATA on any NO-DATA, else PASS. Exit 1, 2, 0 respectively.

Measured 2026-09-06 on this machine: `claude --offline` pass 6, fail 0, no-data 6
(the lifecycle verbs need the network); `cortex` no-data 12. The lifecycle verbs
install from the public repository at real tags (Codex accepts `--ref` only on a
git marketplace source): install and install-again at the previous public tag,
upgrade from it to the current one, so `--ref`/`--from-ref` name them explicitly
when the checkout's own tags cannot. Measured 2026-09-07, `codex` online at
v1.0.8 to v1.0.9: see the handback that dated this paragraph.

## Provider onboarding

A new provider answers six things before it ships: (1) the invocation argv;
(2) the worker seam; (3) its hook and tool event names, with every clamp named;
(4) its sandbox grant; (5) where a signed-in state is detected; (6) its config
home and plugin cache layout. Then:

- add ONE class in `scripts/provider_adapter.py` following `CodexAdapter`;
- add ONE line to the `ADAPTERS` registry;
- prove it with `python3 scripts/adapter_conformance.py --provider <new>`.

Until every one of the six is measured, the adapter refuses every capability and
ships as NO-DATA, exactly like `CortexAdapter` does today. A refusal is honest; a
guessed PASS is the defect this contract exists to prevent. Setting
`BROTHER_CORTEX_BIN` only lets the Cortex adapter report a version string; it does
not lift a single refusal.
