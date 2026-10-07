---
audit_version: L2-1
commit_sha: 1649d16b7172e9eb92345069859a34e18d99c72b
generated_at_utc: 2026-09-21T05:00:47Z
non_test_py_count: 126
test_py_count: 130
domain_count: 8
grep_positive_control: plugin/runtime/brother
---

# One-system wiring audit: is plugin/runtime/brother/ actually triggerable?

GENERATED FILE. Do not hand edit: run `python3 -B scripts/gen_wiring_audit.py`, which rewrites it from
the tree. `--check` re-renders and exits 1 when this file has drifted from the tree.

Every row below starts from a path walked on disk, so no row can name a file that does not exist.
Imports are found by PARSING each file with ast, never by grepping text, so a module name inside a
string or a comment is not counted as a caller. Every G record below is a command this generator ran
through /bin/sh, with the exit code it really returned and a sha256 taken over the bytes it really
wrote to stdout.

## Headline

126 non-test modules sit in the eight audited domains. WIRED (something outside the package imports or
invokes it) 4. ORPHAN (nothing outside the package reaches it) 122. The package is proven by its own
130 test modules and, for 122 of its modules, by nothing else.

## Scope, counts and the domain vocabulary

`non_test_py_count` and `test_py_count` count the modules inside the eight audited domains (`core`,
`mode`, `assurance`, `data`, `data/adapters`, `vault`, `mobile`, `hosts`), which is the vocabulary the
L2 envelope fixes and the only vocabulary the findings table can express. The package as a whole holds
129 non-test modules (G-17). The difference is 3 module(s) whose directory is outside that vocabulary,
listed in full by G-03 and named here rather than filed under a neighbouring domain they do not sit in:

- `plugin/runtime/brother/__init__.py`: ORPHAN, no caller found outside the package
- `plugin/runtime/brother/rtm/__init__.py`: ORPHAN, no caller found outside the package
- `plugin/runtime/brother/rtm/voice_pipeline.py`: ORPHAN, no caller found outside the package, but named as a python3 -m command in plugin/skills/voice-miner/SKILL.md line 47

20 file(s) in this repository could not be parsed as Python and so contributed no import edge. Every
one is a deliberately broken fixture under `benchmarks/fixtures/`, and all of them are still covered by
the text searches G-02 and G-05 to G-08, which read bytes and do not need a file to parse. No row is
therefore NO_DATA on their account.

## Entry points

| surface | manifest_path | manifest_version | claude_marketplace_registered | claude_marketplace_line | cursor_marketplace_registered | cursor_marketplace_line | mutation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bundle | - | NO-DATA | true | 46 | true | 13 | delete the bundle entry from .claude-plugin/marketplace.json; registered flips false |
| products/brothermode | - | NO-DATA | true | 16 | true | 23 | delete the products/brothermode entry from .claude-plugin/marketplace.json; registered flips false |
| products/brothersbe | - | NO-DATA | true | 31 | true | 33 | delete the products/brothersbe entry from .claude-plugin/marketplace.json; registered flips false |
| plugin | - | NO-DATA | false | 0 | false | 0 | add a plugin entry to .claude-plugin/marketplace.json; registered flips true |

Registration booleans and line numbers are read out of the pasted G-12 grep. No per surface manifest
file was shown to this unit, so `manifest_path` is `-` and `manifest_version` is `NO-DATA` for every
surface. `plugin/marketplace.json` is proposed, not present: G-11 shows it missing, and it is never
cited here as existing.

## Findings

| finding_id | domain | file_path | symbol_line | symbol | capability | status | caller_path | caller_line | grep_import | grep_string | mutation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F-001 | assurance | plugin/runtime/brother/assurance/__init__.py | 1 | <module> | Brother Assurance: gates, readiness, verdict compositors | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.assurance to any file outside the package; this row must flip to WIRED |
| F-002 | assurance | plugin/runtime/brother/assurance/authority_hook_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.assurance.authority_hook_facade to any file outside the package; this |
| F-003 | assurance | plugin/runtime/brother/assurance/brothersbe_cli_facade.py | 1 | <module> | Facade over brothersbe's own real CLI package (src/brothersbe/cli.py | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.assurance.brothersbe_cli_facade to any file outside the package; this |
| F-004 | assurance | plugin/runtime/brother/assurance/checks_facade.py | 1 | <module> | Runtime facade for Brother's absorbed SBE checks module (U4, Group 0 | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.assurance.checks_facade to any file outside the package; this row must |
| F-005 | assurance | plugin/runtime/brother/assurance/evidence_lifecycle.py | 1 | <module> | Brother Assurance: engineering evidence lifecycle. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.assurance.evidence_lifecycle to any file outside the package; this row |
| F-006 | assurance | plugin/runtime/brother/assurance/fence_hook_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.assurance.fence_hook_facade to any file outside the package; this row |
| F-007 | assurance | plugin/runtime/brother/assurance/gate_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.assurance.gate_facade to any file outside the package; this row must f |
| F-008 | assurance | plugin/runtime/brother/assurance/run_evals_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.assurance.run_evals_facade to any file outside the package; this row m |
| F-009 | assurance | plugin/runtime/brother/assurance/telemetry_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.assurance.telemetry_facade to any file outside the package; this row m |
| F-010 | assurance | plugin/runtime/brother/assurance/testkit_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.assurance.testkit_facade to any file outside the package; this row mus |
| F-011 | core | plugin/runtime/brother/core/__init__.py | 1 | <module> | Brother Core: run identity, evidence envelope, receipt, acceptance, | WIRED | bundle/runtime/dream_bridge.py | 150 | G-01 | G-02 | delete the reference at bundle/runtime/dream_bridge.py line 150; this row must flip to ORPHAN |
| F-012 | core | plugin/runtime/brother/core/acceptance.py | 1 | <module> | Brother Core acceptance module. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.acceptance to any file outside the package; this row must flip to |
| F-013 | core | plugin/runtime/brother/core/branch_authority.py | 1 | <module> | Brother Core branch authority. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.branch_authority to any file outside the package; this row must f |
| F-014 | core | plugin/runtime/brother/core/context.py | 1 | <module> | Brother Core context manifest. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.context to any file outside the package; this row must flip to WI |
| F-015 | core | plugin/runtime/brother/core/dispatch_semaphore.py | 1 | <module> | Brother Core: machine-local dispatch semaphore and claim-before- | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dispatch_semaphore to any file outside the package; this row must |
| F-016 | core | plugin/runtime/brother/core/dream_calls.py | 1 | <module> | Capability call recording and coverage for the dream RSI layer (unit | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_calls to any file outside the package; this row must flip t |
| F-017 | core | plugin/runtime/brother/core/dream_gate.py | 1 | <module> | Gate ledger primitives for the dream RSI layer (unit D4.a). | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_gate to any file outside the package; this row must flip to |
| F-018 | core | plugin/runtime/brother/core/dream_gate_policy.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_gate_policy to any file outside the package; this row must |
| F-019 | core | plugin/runtime/brother/core/dream_grade.py | 1 | <module> | D9.a contracts, parsing and error type for dream grade. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_grade to any file outside the package; this row must flip t |
| F-020 | core | plugin/runtime/brother/core/dream_grade_d9d.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_grade_d9d to any file outside the package; this row must fl |
| F-021 | core | plugin/runtime/brother/core/dream_policy.py | 1 | <module> | (no module docstring) | WIRED | bundle/runtime/dream_bridge.py | 150 | G-01 | G-02 | delete the reference at bundle/runtime/dream_bridge.py line 150; this row must flip to ORPHAN |
| F-022 | core | plugin/runtime/brother/core/dream_policy_d15a.py | 1 | <module> | D15-A pure scheduling ranking and bounded batch width. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_policy_d15a to any file outside the package; this row must |
| F-023 | core | plugin/runtime/brother/core/dream_promote.py | 1 | <module> | Append only promotion store for D12.A. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_promote to any file outside the package; this row must flip |
| F-024 | core | plugin/runtime/brother/core/dream_propose.py | 1 | <module> | dream_propose: sanitized intake, request builder, budget charge (uni | WIRED | bundle/runtime/dream_bridge.py | 155 | G-01 | G-02 | delete the reference at bundle/runtime/dream_bridge.py line 155; this row must flip to ORPHAN |
| F-025 | core | plugin/runtime/brother/core/dream_record.py | 1 | <module> | dream_record: record a control decision and, later, its outcome (uni | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_record to any file outside the package; this row must flip |
| F-026 | core | plugin/runtime/brother/core/dream_replay.py | 1 | <module> | dream_replay: offline replay of recorded control decisions (unit D6+ | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_replay to any file outside the package; this row must flip |
| F-027 | core | plugin/runtime/brother/core/dream_report.py | 1 | <module> | dream_report: summarize the dream decision journal of one run (unit | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_report to any file outside the package; this row must flip |
| F-028 | core | plugin/runtime/brother/core/dream_world.py | 1 | <module> | dream_world: pure deterministic constructor for historical worlds (u | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.dream_world to any file outside the package; this row must flip t |
| F-029 | core | plugin/runtime/brother/core/evidence.py | 1 | <module> | Evidence envelope for Brother Core. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.evidence to any file outside the package; this row must flip to W |
| F-030 | core | plugin/runtime/brother/core/leases.py | 1 | <module> | Brother Core leases and collision identity. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.leases to any file outside the package; this row must flip to WIR |
| F-031 | core | plugin/runtime/brother/core/model_capability_profile.py | 1 | <module> | Brother Core: capability canary and quarantine gate (unit OR-3). | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.model_capability_profile to any file outside the package; this ro |
| F-032 | core | plugin/runtime/brother/core/openrouter_dispatch.py | 1 | <module> | Brother Core: the real integration point tying OR-1 through OR-4 | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.openrouter_dispatch to any file outside the package; this row mus |
| F-033 | core | plugin/runtime/brother/core/openrouter_ledger.py | 1 | <module> | Brother Core: OpenRouter reserve/reconcile budget ledger (unit OR-1) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.openrouter_ledger to any file outside the package; this row must |
| F-034 | core | plugin/runtime/brother/core/openrouter_prices.py | 1 | <module> | OpenRouter model pricing catalog helpers. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.openrouter_prices to any file outside the package; this row must |
| F-035 | core | plugin/runtime/brother/core/openrouter_strict.py | 1 | <module> | Brother Core: structural fallback-detection wrapper (unit OR-2). | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.openrouter_strict to any file outside the package; this row must |
| F-036 | core | plugin/runtime/brother/core/or_dispatch_cli.py | 1 | <module> | Real CLI wiring for openrouter_dispatch.py: the command this session | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.or_dispatch_cli to any file outside the package; this row must fl |
| F-037 | core | plugin/runtime/brother/core/or_fanout.py | 1 | <module> | or_fanout: run many gated OpenRouter jobs at once, one results file. | WIRED | bundle/runtime/dream_bridge.py | 160 | G-01 | G-02 | delete the reference at bundle/runtime/dream_bridge.py line 160; this row must flip to ORPHAN |
| F-038 | core | plugin/runtime/brother/core/product.py | 1 | <module> | Brother Core product identity information. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.product to any file outside the package; this row must flip to WI |
| F-039 | core | plugin/runtime/brother/core/receipt.py | 1 | <module> | Brother Core receipt module. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.receipt to any file outside the package; this row must flip to WI |
| F-040 | core | plugin/runtime/brother/core/registry.py | 1 | <module> | Brother Core: capability registry and cross-domain composition (unit | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.registry to any file outside the package; this row must flip to W |
| F-041 | core | plugin/runtime/brother/core/repair_trajectory.py | 1 | <module> | D14.1: the repair trajectory contract and strict failure parsing. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.repair_trajectory to any file outside the package; this row must |
| F-042 | core | plugin/runtime/brother/core/run.py | 1 | <module> | Run identity and outcome contract for Brother Core. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.run to any file outside the package; this row must flip to WIRED |
| F-043 | core | plugin/runtime/brother/core/state.py | 1 | <module> | Brother Core state root path resolution and layout management. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.core.state to any file outside the package; this row must flip to WIRE |
| F-044 | data | plugin/runtime/brother/data/__init__.py | 1 | <module> | Brother Data: schemas, records, warehouse bindings | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data to any file outside the package; this row must flip to WIRED |
| F-045 | data/adapters | plugin/runtime/brother/data/adapters/__init__.py | 1 | <module> | Brother Data real-system adapters: Snowflake, Databricks, dbt, and | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.adapters to any file outside the package; this row must flip to W |
| F-046 | data/adapters | plugin/runtime/brother/data/adapters/base.py | 1 | <module> | Brother Data: shared base for real-system adapters (Snowflake, | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.adapters.base to any file outside the package; this row must flip |
| F-047 | data/adapters | plugin/runtime/brother/data/adapters/databricks_adapter.py | 1 | <module> | BrotherDS Databricks adapter | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.adapters.databricks_adapter to any file outside the package; this |
| F-048 | data/adapters | plugin/runtime/brother/data/adapters/dbt_adapter.py | 1 | <module> | BrotherDS dbt adapter | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.adapters.dbt_adapter to any file outside the package; this row mu |
| F-049 | data/adapters | plugin/runtime/brother/data/adapters/duckdb_adapter.py | 1 | <module> | BrotherDS DuckDB adapter: the local, embeddable, no-credentials | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.adapters.duckdb_adapter to any file outside the package; this row |
| F-050 | data/adapters | plugin/runtime/brother/data/adapters/snowflake_adapter.py | 1 | <module> | BrotherDS Snowflake adapter | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.adapters.snowflake_adapter to any file outside the package; this |
| F-051 | data | plugin/runtime/brother/data/bds_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.bds_facade to any file outside the package; this row must flip to |
| F-052 | data | plugin/runtime/brother/data/mdm_audit_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.mdm_audit_facade to any file outside the package; this row must f |
| F-053 | data | plugin/runtime/brother/data/mdm_derive_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.mdm_derive_facade to any file outside the package; this row must |
| F-054 | data | plugin/runtime/brother/data/mdm_eval_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.mdm_eval_facade to any file outside the package; this row must fl |
| F-055 | data | plugin/runtime/brother/data/mdm_jan_lookup_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.mdm_jan_lookup_facade to any file outside the package; this row m |
| F-056 | data | plugin/runtime/brother/data/mdm_normalize_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.data.mdm_normalize_facade to any file outside the package; this row mu |
| F-057 | hosts | plugin/runtime/brother/hosts/__init__.py | 1 | <module> | Brother Hosts: GitHub and Bitbucket adapters, CI/CD bridges, parity | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.hosts to any file outside the package; this row must flip to WIRED |
| F-058 | mobile | plugin/runtime/brother/mobile/__init__.py | 1 | <module> | Brother Mobile: converged native workflow/evidence capability. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mobile to any file outside the package; this row must flip to WIRED |
| F-059 | mobile | plugin/runtime/brother/mobile/android.py | 1 | <module> | Brother Mobile: Android parity groundwork (unit J). | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mobile.android to any file outside the package; this row must flip to |
| F-060 | mobile | plugin/runtime/brother/mobile/silent_permissive_default.py | 1 | <module> | Brother Mobile capability: detect a silent-permissive-default gate. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mobile.silent_permissive_default to any file outside the package; this |
| F-061 | mode | plugin/runtime/brother/mode/__init__.py | 1 | <module> | Brother Mode facade: the ownership contract of bm_store.py, re-expor | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode to any file outside the package; this row must flip to WIRED |
| F-062 | mode | plugin/runtime/brother/mode/autonomy_facade.py | 1 | <module> | Autonomy contract facade for Brother U3. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.autonomy_facade to any file outside the package; this row must fl |
| F-063 | mode | plugin/runtime/brother/mode/autosave_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.autosave_facade to any file outside the package; this row must fl |
| F-064 | mode | plugin/runtime/brother/mode/continue_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.continue_facade to any file outside the package; this row must fl |
| F-065 | mode | plugin/runtime/brother/mode/controller_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.controller_facade to any file outside the package; this row must |
| F-066 | mode | plugin/runtime/brother/mode/cursor_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.cursor_facade to any file outside the package; this row must flip |
| F-067 | mode | plugin/runtime/brother/mode/docs_export_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.docs_export_facade to any file outside the package; this row must |
| F-068 | mode | plugin/runtime/brother/mode/docs_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.docs_facade to any file outside the package; this row must flip t |
| F-069 | mode | plugin/runtime/brother/mode/handover_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.handover_facade to any file outside the package; this row must fl |
| F-070 | mode | plugin/runtime/brother/mode/identity.py | 1 | <module> | Core to Mode identity bridge, per U3-BMSTORE-MIGRATION-PLAN.md secti | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.identity to any file outside the package; this row must flip to W |
| F-071 | mode | plugin/runtime/brother/mode/lead_facade.py | 1 | <module> | Runtime facade for the bm_lead CLI. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.lead_facade to any file outside the package; this row must flip t |
| F-072 | mode | plugin/runtime/brother/mode/learn_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.learn_facade to any file outside the package; this row must flip |
| F-073 | mode | plugin/runtime/brother/mode/packs_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.packs_facade to any file outside the package; this row must flip |
| F-074 | mode | plugin/runtime/brother/mode/project_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.project_facade to any file outside the package; this row must fli |
| F-075 | mode | plugin/runtime/brother/mode/reconcile_worktrees_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.reconcile_worktrees_facade to any file outside the package; this |
| F-076 | mode | plugin/runtime/brother/mode/runtimes_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.runtimes_facade to any file outside the package; this row must fl |
| F-077 | mode | plugin/runtime/brother/mode/store.py | 1 | <module> | Runtime resolver for bm_store.py, per docs/architecture/U3-BMSTORE-M | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.store to any file outside the package; this row must flip to WIRE |
| F-078 | mode | plugin/runtime/brother/mode/sut.py | 1 | <module> | Brother Mode: Safe Unwatched Time. | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.sut to any file outside the package; this row must flip to WIRED |
| F-079 | mode | plugin/runtime/brother/mode/view_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.mode.view_facade to any file outside the package; this row must flip t |
| F-080 | vault | plugin/runtime/brother/vault/__init__.py | 1 | <module> | Brother Vault: unification of the memory/lesson subsystem | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault to any file outside the package; this row must flip to WIRED |
| F-081 | vault | plugin/runtime/brother/vault/assertions_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.assertions_facade to any file outside the package; this row must |
| F-082 | vault | plugin/runtime/brother/vault/attribute_provenance_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.attribute_provenance_facade to any file outside the package; thi |
| F-083 | vault | plugin/runtime/brother/vault/attributes_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.attributes_facade to any file outside the package; this row must |
| F-084 | vault | plugin/runtime/brother/vault/census_ext_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.census_ext_facade to any file outside the package; this row must |
| F-085 | vault | plugin/runtime/brother/vault/cite_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.cite_facade to any file outside the package; this row must flip |
| F-086 | vault | plugin/runtime/brother/vault/cli_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.cli_facade to any file outside the package; this row must flip t |
| F-087 | vault | plugin/runtime/brother/vault/closure_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.closure_facade to any file outside the package; this row must fl |
| F-088 | vault | plugin/runtime/brother/vault/compose_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.compose_facade to any file outside the package; this row must fl |
| F-089 | vault | plugin/runtime/brother/vault/contract_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.contract_facade to any file outside the package; this row must f |
| F-090 | vault | plugin/runtime/brother/vault/crosswalk_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.crosswalk_facade to any file outside the package; this row must |
| F-091 | vault | plugin/runtime/brother/vault/curate_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.curate_facade to any file outside the package; this row must fli |
| F-092 | vault | plugin/runtime/brother/vault/digest_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.digest_facade to any file outside the package; this row must fli |
| F-093 | vault | plugin/runtime/brother/vault/distill_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.distill_facade to any file outside the package; this row must fl |
| F-094 | vault | plugin/runtime/brother/vault/enrich_gate_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.enrich_gate_facade to any file outside the package; this row mus |
| F-095 | vault | plugin/runtime/brother/vault/entity_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.entity_facade to any file outside the package; this row must fli |
| F-096 | vault | plugin/runtime/brother/vault/events_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.events_facade to any file outside the package; this row must fli |
| F-097 | vault | plugin/runtime/brother/vault/exchange_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.exchange_facade to any file outside the package; this row must f |
| F-098 | vault | plugin/runtime/brother/vault/export_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.export_facade to any file outside the package; this row must fli |
| F-099 | vault | plugin/runtime/brother/vault/graph_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.graph_facade to any file outside the package; this row must flip |
| F-100 | vault | plugin/runtime/brother/vault/heat_temporal_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.heat_temporal_facade to any file outside the package; this row m |
| F-101 | vault | plugin/runtime/brother/vault/hierarchy_req_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.hierarchy_req_facade to any file outside the package; this row m |
| F-102 | vault | plugin/runtime/brother/vault/identity_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.identity_facade to any file outside the package; this row must f |
| F-103 | vault | plugin/runtime/brother/vault/intake_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.intake_facade to any file outside the package; this row must fli |
| F-104 | vault | plugin/runtime/brother/vault/interchange_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.interchange_facade to any file outside the package; this row mus |
| F-105 | vault | plugin/runtime/brother/vault/jbench_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.jbench_facade to any file outside the package; this row must fli |
| F-106 | vault | plugin/runtime/brother/vault/labels_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.labels_facade to any file outside the package; this row must fli |
| F-107 | vault | plugin/runtime/brother/vault/ledger_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.ledger_facade to any file outside the package; this row must fli |
| F-108 | vault | plugin/runtime/brother/vault/lineage_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.lineage_facade to any file outside the package; this row must fl |
| F-109 | vault | plugin/runtime/brother/vault/lock_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.lock_facade to any file outside the package; this row must flip |
| F-110 | vault | plugin/runtime/brother/vault/notify_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.notify_facade to any file outside the package; this row must fli |
| F-111 | vault | plugin/runtime/brother/vault/pack_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.pack_facade to any file outside the package; this row must flip |
| F-112 | vault | plugin/runtime/brother/vault/pane_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.pane_facade to any file outside the package; this row must flip |
| F-113 | vault | plugin/runtime/brother/vault/plugins_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.plugins_facade to any file outside the package; this row must fl |
| F-114 | vault | plugin/runtime/brother/vault/posture_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.posture_facade to any file outside the package; this row must fl |
| F-115 | vault | plugin/runtime/brother/vault/promote_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.promote_facade to any file outside the package; this row must fl |
| F-116 | vault | plugin/runtime/brother/vault/promotions_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.promotions_facade to any file outside the package; this row must |
| F-117 | vault | plugin/runtime/brother/vault/realdata_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.realdata_facade to any file outside the package; this row must f |
| F-118 | vault | plugin/runtime/brother/vault/recall_hook_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.recall_hook_facade to any file outside the package; this row mus |
| F-119 | vault | plugin/runtime/brother/vault/retention_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.retention_facade to any file outside the package; this row must |
| F-120 | vault | plugin/runtime/brother/vault/retier_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.retier_facade to any file outside the package; this row must fli |
| F-121 | vault | plugin/runtime/brother/vault/route_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.route_facade to any file outside the package; this row must flip |
| F-122 | vault | plugin/runtime/brother/vault/shapes_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.shapes_facade to any file outside the package; this row must fli |
| F-123 | vault | plugin/runtime/brother/vault/survivorship_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.survivorship_facade to any file outside the package; this row mu |
| F-124 | vault | plugin/runtime/brother/vault/triage_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.triage_facade to any file outside the package; this row must fli |
| F-125 | vault | plugin/runtime/brother/vault/vault_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.vault_facade to any file outside the package; this row must flip |
| F-126 | vault | plugin/runtime/brother/vault/vault_lint_facade.py | 1 | <module> | (no module docstring) | ORPHAN | - | 0 | G-01 | G-02 | add an import of plugin.runtime.brother.vault.vault_lint_facade to any file outside the package; this row must |

`symbol_line` is 1 and `symbol` is `<module>` because this audit asks a module level question: does
anything outside the package reach this file at all. `capability` is the module's own first docstring
sentence, read from the file, never a description written here.

## Documented but not called

Modules named as a `python3 -m` command line inside a `.md` or `.json` outside the package, read out
of the pasted G-05 search. A published command line is not a caller and does not change a status, but
an ORPHAN module with a documented command is the ORPHAN most likely to be a real wiring gap:

- `plugin.runtime.brother.core.or_dispatch_cli`: named in docs/plan/specs/L2.md line 30
- `plugin.runtime.brother.rtm.voice_pipeline`: named in plugin/skills/voice-miner/SKILL.md line 47

## Reproducibility

Every command below was run from the repository root through /bin/sh by
`scripts/gen_wiring_audit.py`, with stderr discarded, exactly as the `sha256_stdout` helper in
`scripts/test_L2_spec.py` discards it. `sha256:` is the SHA-256 of that command's stdout bytes only.
An interactive shell can carry a `grep` shell function that forwards to a different grep binary, which
strips the leading `./` and honours ignore files; a hash re-taken in such a shell will not match. Use
`sh -c` to reproduce one. The positive control `plugin/runtime/brother` hits in exactly one pasted output (G-03),
which is what makes an empty result in any other record a real absence rather than a dead pattern.

### G-01 import syntax search, outside the package

command: grep -rn --exclude-dir=.git --include='*.py' -e 'from plugin[.]runtime[.]brother' -e 'import plugin[.]runtime[.]brother' . | grep -v '^[.]/plugin/runtime/brother/'
exit_code: 0
sha256: 87caac3d27c0cca2c0cb3ab78cea6c6f15d280872888249a261b912878b83cc1
output_pasted: true
output:
```
./bundle/runtime/dream_bridge.py:150:    from plugin.runtime.brother.core import dream_policy
./bundle/runtime/dream_bridge.py:155:    from plugin.runtime.brother.core import dream_propose
./bundle/runtime/dream_bridge.py:160:    from plugin.runtime.brother.core import or_fanout
./bundle/runtime/dream_bridge.py:557:        from plugin.runtime.brother.core import dream_propose
./scripts/dream_authorize.py:22:from plugin.runtime.brother.core import dream_policy as _dp
./scripts/dream_bridge.py:150:    from plugin.runtime.brother.core import dream_policy
./scripts/dream_bridge.py:155:    from plugin.runtime.brother.core import dream_propose
./scripts/dream_bridge.py:160:    from plugin.runtime.brother.core import or_fanout
./scripts/dream_bridge.py:557:        from plugin.runtime.brother.core import dream_propose
```

### G-02 package string search over every audited extension, outside the package

command: grep -rl --exclude-dir=.git --include='*.py' --include='*.sh' --include='*.json' --include='*.md' --include='*.yml' --include='*.yaml' -e 'plugin[.]runtime[.]brother' -e 'plugin/runtime/brother' . | grep -v '^[.]/plugin/runtime/brother/'
exit_code: 0
sha256: a04e70a93b549c2463fa4d6f1b7798418e48e14df89635caab12330e42d3266d
output_pasted: true
output:
```
./editions/personal/2026-09-19-mobile-capability-sourcing.md
./plugin/skills/voice-miner/SKILL.md
./bundle/runtime/dream_bridge.py
./docs/plan/specs/L10c.md
./docs/plan/specs/D12.md
./docs/plan/specs/D9.md
./docs/plan/specs/D13.md
./docs/plan/specs/L5b.md
./docs/plan/specs/M4.md
./docs/plan/specs/D2.md
./docs/plan/specs/D6.md
./docs/plan/specs/L0.md
./docs/plan/specs/D7.md
./docs/plan/specs/L5c.md
./docs/plan/specs/M5.md
./docs/plan/specs/D3.md
./docs/plan/specs/L5.md
./docs/plan/specs/D4.md
./docs/plan/specs/L2.md
./docs/plan/specs/D0.md
./docs/plan/specs/L5a.md
./docs/plan/specs/D1.md
./docs/plan/specs/D14.md
./docs/plan/specs/D10.md
./docs/plan/specs/D11.md
./docs/plan/specs/D15.md
./docs/plan/JEV-USE-CASE-CATALOGUE.md
./docs/plan/DELIVERY-FRAMEWORK.md
./docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json
./docs/plan/UNIFY-1.1.0-STATUS.json
./docs/plan/UNIFY-1.1.0-WBS.json
./docs/architecture/U3-BMSTORE-MIGRATION-PLAN.md
./docs/architecture/L5A-SECURITY-REVIEW.md
./docs/architecture/DREAM-RSI-SELF-LEARNING-ANALYSIS.md
./docs/architecture/inventory/products.json
./docs/architecture/ONE-SYSTEM-WIRING-AUDIT.md
./docs/architecture/PARITY-MATRIX.md
./docs/architecture/ANTIGRAVITY-ADAPTER-SPEC.md
./scripts/check_all.sh
./scripts/probe_round.py
./scripts/test_build_brief_corrections.py
./scripts/gen_wiring_audit.py
./scripts/test_dream_bridge.py
./scripts/dream_authorize.py
./scripts/bundle_runtime.py
./scripts/dream_bridge.py
./scripts/test_close_unit.py
./scripts/diag_round.py
./scripts/plugin_runtime_fast_discover.py
./scripts/required_fast.sh
./scripts/test_L2_spec.py
./scripts/loop/repair_wave.py
./scripts/loop/probe_wave.py
./scripts/loop/build_brief.py
./scripts/loop/spec_wave.py
./scripts/loop/spec_council.py
./scripts/loop/unit_runner.py
./SYSTEM.md
```

### G-03 positive control, and the modules outside the audited domain vocabulary

command: find plugin/runtime/brother/__init__.py plugin/runtime/brother/rtm -name '*.py' -not -name 'test_*' | sort
exit_code: 0
sha256: fcde501e4ee94780926674182c3588d4f6493697e9ee49570aff4055842ea406
output_pasted: true
output:
```
plugin/runtime/brother/__init__.py
plugin/runtime/brother/rtm/__init__.py
plugin/runtime/brother/rtm/voice_pipeline.py
```

### G-04 dynamic importlib search

command: grep -rn --exclude-dir=.git --include='*.py' 'importlib' . | grep 'plugin[.]runtime[.]brother'
exit_code: 1
sha256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
output_pasted: true
output:
```
(no output)
```

### G-05 python3 -m command line string search

command: grep -rn --exclude-dir=.git --include='*.py' --include='*.sh' --include='*.json' --include='*.md' --include='*.yml' --include='*.yaml' 'python3 -m plugin[.]runtime[.]brother' . | grep -v '^[.]/plugin/runtime/brother/' | grep -v 'ONE-SYSTEM-WIRING-AUDIT'
exit_code: 0
sha256: 42b2034bc4006a1a7aba4f9771f7dd71a20f6c06cd163670a49753b363872d29
output_pasted: true
output:
```
./plugin/skills/voice-miner/SKILL.md:47:   python3 -m plugin.runtime.brother.rtm.voice_pipeline --dir "/absolute/working-folder" --handle "your-handle"
./plugin/skills/voice-miner/SKILL.md:59:   python3 -m plugin.runtime.brother.rtm.voice_pipeline --dir "/absolute/working-folder" --handle "your-handle" --no-fetch
./docs/plan/specs/L2.md:30:| C-09 | Single import-syntax grep suffices for ORPHAN | Insufficient. The owned doc must account for `if __name__ == "__main__":` and intended `python3 -m plugin.runtime.brother.core.or_dispatch_cli` strings, which `^from plugin\.runtime\.brother` misses. Spec requires dual search plus trigger taxonomy. | `scripts/plugin_runtime_fast_discover.py` scope note |
./docs/plan/JEV-USE-CASE-CATALOGUE.md:12:cat <payload>.json | python3 -m plugin.runtime.brother.core.or_dispatch_cli \
./scripts/loop/spec_wave.py:334:    print("RUN     python3 -m plugin.runtime.brother.core.or_fanout %s/jobs.json --workers %d --retries 1 "
```

### G-06 shell wrapper search

command: grep -rn --exclude-dir=.git --include='*.sh' 'plugin[.]runtime[.]brother' . | grep -v '^[.]/plugin/runtime/brother/'
exit_code: 0
sha256: e85fd838424de842e66596fb057a629fc264bacaea20dfa587a2718d3701bea7
output_pasted: true
output:
```
./scripts/check_all.sh:1893:run_check "dream-coverage-self" python3 -B -m unittest plugin.runtime.brother.core.test_dream_coverage
./scripts/check_all.sh:1896:run_check "or-fanout-m51-self" python3 -B -m unittest plugin.runtime.brother.core.test_or_fanout_m51
./scripts/check_all.sh:1900:run_check "dream-execution-self" python3 -B -m unittest plugin.runtime.brother.core.test_dream_execution
./scripts/check_all.sh:1901:run_check "dream-world-d62-self" python3 -B -m unittest plugin.runtime.brother.core.test_dream_world_d62
./scripts/check_all.sh:1904:run_check "dream-world-d63-self" python3 -B -m unittest plugin.runtime.brother.core.test_dream_world_d63
./scripts/check_all.sh:1914:run_check "dream-world-d64-self" python3 -B -m unittest plugin.runtime.brother.core.test_dream_world_d64
./scripts/check_all.sh:1922:run_check "dream-world-d65-self" python3 -B -m unittest plugin.runtime.brother.core.test_dream_world_d65
./scripts/check_all.sh:1928:run_check "or-fanout-l5a1-self" python3 -B -m unittest plugin.runtime.brother.core.test_or_fanout_l5a1
./scripts/check_all.sh:1929:run_check "repair-trajectory-d141-self" python3 -B -m unittest plugin.runtime.brother.core.test_repair_trajectory_d141
./scripts/check_all.sh:1933:run_check "dream-bridge-self" python3 -B -m unittest plugin.runtime.brother.core.test_dream_bridge
./scripts/check_all.sh:1934:run_check "dream-policy-d15a-self" python3 -B -m unittest plugin.runtime.brother.core.test_dream_policy_d15a
./scripts/check_all.sh:1935:run_check "dream-grade-d9d-self" python3 -B -m unittest plugin.runtime.brother.core.test_dream_grade_d9d
```

### G-07 manifest, workflow and data file reference search

command: grep -rl --exclude-dir=.git --include='*.json' --include='*.yml' --include='*.yaml' -e 'plugin[.]runtime[.]brother' -e 'plugin/runtime/brother' . | grep -v '^[.]/plugin/runtime/brother/'
exit_code: 0
sha256: edcc95ab937660adb54e5148ead979ab40481fdef17bcb7fa96086609e820afc
output_pasted: true
output:
```
./docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json
./docs/plan/UNIFY-1.1.0-STATUS.json
./docs/plan/UNIFY-1.1.0-WBS.json
./docs/architecture/inventory/products.json
```

### G-08 subprocess argument search

command: grep -rn --exclude-dir=.git --include='*.py' 'subprocess' . | grep 'plugin[.]runtime[.]brother' | grep -v '^[.]/plugin/runtime/brother/'
exit_code: 0
sha256: 7d3c55ac570d21620f0c62be742546688e8dd28d4bc2cff67c2d29600dc2f25b
output_pasted: true
output:
```
./scripts/probe_round.py:167:    subprocess.run([sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jf,
./scripts/loop/repair_wave.py:64:proc = subprocess.Popen([sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jf, "--workers", "95", "--timeout", "420", "--retries", "0",
./scripts/loop/probe_wave.py:44:    proc = subprocess.Popen([sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jf, "--workers", "80", "--timeout", "600", "--retries", "0",
./scripts/loop/spec_council.py:48:        subprocess.Popen([sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jf, "--workers", "99", "--timeout", "600", "--retries", "0",
./scripts/loop/unit_runner.py:93:    proc = subprocess.Popen([sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jf, "--workers", "99", "--timeout", "420", "--retries", "0",
```

### G-09 HEAVY basenames the fast gate skips

command: grep -n 'HEAVY' scripts/plugin_runtime_fast_discover.py
exit_code: 0
sha256: c221b249d75fe9614d513ef34777ec1e976f5eafb38986fdc325d49eed1ea131
output_pasted: true
output:
```
18:HEAVY = {"test_openrouter_ledger.py", "test_leases.py", "test_dispatch_semaphore.py"}
24:        if os.path.basename(path) in HEAVY:
```

### G-10 gate registration of the package test run

command: grep -n 'plugin-runtime-tests-fast' scripts/required_fast.sh
exit_code: 0
sha256: da15163edd39d0fc80207fe31de351d33be6cabfe532e9201ef45911bdebc6cb
output_pasted: true
output:
```
200:run_check "plugin-runtime-tests-fast" python3 scripts/plugin_runtime_fast_discover.py
```

### G-11 marketplace files present or absent

command: ls .claude-plugin/marketplace.json .cursor-plugin/marketplace.json plugin/marketplace.json
exit_code: 1
sha256: 4a45743319c453b6d069f7237b8c167c4a0333d169bc71c80a567fadb8e20540
output_pasted: true
output:
```
.claude-plugin/marketplace.json
.cursor-plugin/marketplace.json
```

### G-12 marketplace surface registration lines

command: grep -n -e '"path": ' -e '"source": ' .claude-plugin/marketplace.json .cursor-plugin/marketplace.json
exit_code: 0
sha256: d79bfc95e00a4ce63b283a9e265965b7b5fb65a84c2cca261f00a40733c66148
output_pasted: true
output:
```
.claude-plugin/marketplace.json:13:      "source": {
.claude-plugin/marketplace.json:14:        "source": "git-subdir",
.claude-plugin/marketplace.json:16:        "path": "products/brothermode",
.claude-plugin/marketplace.json:28:      "source": {
.claude-plugin/marketplace.json:29:        "source": "git-subdir",
.claude-plugin/marketplace.json:31:        "path": "products/brothersbe",
.claude-plugin/marketplace.json:43:      "source": {
.claude-plugin/marketplace.json:44:        "source": "git-subdir",
.claude-plugin/marketplace.json:46:        "path": "bundle",
.claude-plugin/marketplace.json:58:      "source": {
.claude-plugin/marketplace.json:59:        "source": "git-subdir",
.claude-plugin/marketplace.json:61:        "path": "products/brotherds",
.cursor-plugin/marketplace.json:13:      "source": "bundle",
.cursor-plugin/marketplace.json:23:      "source": "products/brothermode",
.cursor-plugin/marketplace.json:33:      "source": "products/brothersbe",
```

### G-13 parity matrix present

command: ls docs/architecture/PARITY-MATRIX.md
exit_code: 0
sha256: af12f745bbfbb273b6e585b892fb358ac05047b1edac19628d1b208d7a750397
output_pasted: true
output:
```
docs/architecture/PARITY-MATRIX.md
```

### G-14 shared codes_file path in the gate wrapper

command: grep -n 'codes_file=' scripts/required_fast.sh
exit_code: 0
sha256: 6d3fa89673462738c256f1038c5b981a9b7a47075dc64b475caadad76512ff8b
output_pasted: true
output:
```
32:codes_file="${TMPDIR:-/tmp}/required-fast-codes-$worktree_key.txt"
```

### G-15 exit 1 conflation in the discovery script

command: grep -n 'if not mods' scripts/plugin_runtime_fast_discover.py
exit_code: 0
sha256: 19cb4c4899d4b82e5ade7d09d9e14a1c6319576a95d17e644fb994dd8ce1b828
output_pasted: true
output:
```
27:    if not mods:
```

### G-16 BROTHER_JEV_STATE_DIR preset handling

command: grep -n 'BROTHER_JEV_STATE_DIR' scripts/required_fast.sh
exit_code: 0
sha256: 90f9384029930cc9ef2a53e2993508dc7af4ab950854d96243dde9abee3e6abd
output_pasted: true
output:
```
69:if [ -z "${BROTHER_JEV_STATE_DIR:-}" ]; then
70:  BROTHER_JEV_STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/required-fast-jev.XXXXXX")" || exit 2
71:  export BROTHER_JEV_STATE_DIR
```

### G-17 whole package non-test module census

command: find plugin/runtime/brother -name '*.py' -not -name 'test_*' -not -path '*__pycache__*' | wc -l
exit_code: 0
sha256: 020536c5b9b3d5da60fca0b3dbd5dc7ddd8026c1240bbfd6b6591cc37a69e6b9
output_pasted: true
output:
```
     129
```

### G-18 workflow directory listing

command: ls .github/workflows
exit_code: 0
sha256: 2f7a29945eeb1f44b9816c0d464c30a52e12a156a6067071eec53eb77abc740f
output_pasted: true
output:
```
linux-smoke.yml
required-fast.yml
virgin-install.yml
```

## Gate limits noted, not fixed

- HEAVY basenames: `scripts/plugin_runtime_fast_discover.py` line 18 skips `test_openrouter_ledger.py`, `test_leases.py`, `test_dispatch_semaphore.py` by basename, so a fast gate PASS does not cover them; last full battery covering all three: NO-DATA. G-09. fix deferred, audit only
- shared codes_file: `scripts/required_fast.sh` line 32 writes every check code to a predictable path under TMPDIR keyed only by directory name and pid, and nothing counts the codes before they are read. G-14. fix deferred, audit only
- exit `1` conflation: `scripts/plugin_runtime_fast_discover.py` line 27 returns 1 when discovery finds no module, which the wrapper cannot tell apart from a real test failure, so an empty run reads as FAIL rather than NO-DATA. G-15. fix deferred, audit only
- BROTHER_JEV_STATE_DIR preset: `scripts/required_fast.sh` line 69 keeps a caller supplied value instead of its own mktemp directory, so a caller can opt the run out of state isolation. G-16. fix deferred, audit only

The package's own test run is registered in the gate at `scripts/required_fast.sh` line 200 (G-10).

## Status semantics

1. ORPHAN means no caller outside plugin/runtime/brother/ was found by the pasted grep.
2. NO_DATA must never be rendered as ORPHAN.
3. ORPHAN is a wiring observation, not a defect.

## Staleness

This audit is valid only at the `commit_sha` recorded in the front matter above. Any change to an
input listed below invalidates it: re-run all G records by regenerating this file, which refreshes
`commit_sha` and `generated_at_utc` together with every hash, and only then cite it again.

- any change to the `plugin/runtime/brother/` glob
- any change to `scripts/required_fast.sh`
- any change to `scripts/plugin_runtime_fast_discover.py`
- any change to `.claude-plugin/marketplace.json` (NEW)
- any change to `.cursor-plugin/marketplace.json` (NEW)
- any change to `plugin/marketplace.json` (NEW)

A stale audit is NO-DATA: it blocks, and it is never rendered as WIRED or ORPHAN.

## Out of scope

- U6: packaging unit, no rewiring here.
- U7: registration unit, no rewiring here.
- U8: marketplace retirement unit, no rewiring here.
- PARITY-MATRIX: parity labels only (G-13 shows the file present), no rewiring here.
- out-of-repo bridge path: a non-repo note only, never a caller, never proof, never a blocker.
- `scripts/audit_one_system_wiring.sh` NEW: follow-on automation, not built here.
- every gate limit above: fix deferred, and no code file changes in this unit beyond
  `scripts/gen_wiring_audit.py`, the generator that writes this document.
