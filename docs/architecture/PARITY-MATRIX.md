# Parity matrix, Brother 1.1.0 unification

Per the brief's section 64: every capability ends in MIGRATED, INTENTIONALLY RETIRED,
or NO-DATA/BLOCKED before any legacy path is deleted. This file starts with the one
row produced tonight from a verified U0 finding; it grows as each WBS unit lands.

| Capability | Legacy owner | Unified owner | Deciding test | Status |
| --- | --- | --- | --- | --- |
| brother_paths.py path/log helpers (`one_line()` / `say()` control character and newline defenses) | scripts/, bundle/runtime/, bundle/runtime/hooks/brothermode/tools/, bundle/runtime/hooks/brothersbe/tools/, products/brothermode/tools/ (five byte identical copies, md5 960d0dba...) | brother.core; canonical source products/brothersbe/tools/brother_paths.py (pending absorption) | md5 all six copies: five must equal 960d0dba...; products/brothersbe/tools/brother_paths.py must be the 303 line superset with one_line()/say(); hook path load test covers control character/newline log injection in env paths | NO-DATA/BLOCKED |
| bm_store.py ownership-fencing core (claim/transition/checkpoint/decide, exception vocabulary, root resolution, path safety) | products/brothermode/tools/bm_store.py | plugin/runtime/brother/mode/store.py, identity.py | test_store.py, test_fencing.py, test_versioning.py, test_takeover.py, test_handovers.py, test_corruption.py, test_containment.py, test_crossprocess.py, test_facade_integrity.py (42 tests) | MIGRATED |
| Store class's full method surface (learning, sentinel, autonomy, controller, ledger, views, capability-receipts, reality-records, ~160 public methods total) | products/brothermode/tools/bm_store.py's Store class | plugin/runtime/brother/mode (`Store` re-exported as the literal same class object) | test_store_class_parity.py: object identity (`facade Store is bm_store.Store`), not merely equal behavior | MIGRATED |
| 13 thin-CLI wrapper tools (autonomy, lead, project, view, continue, cursor, controller, docs_export, docs, handover, packs, learn, runtimes) | products/brothermode/tools/bm_*.py, each with a module-level COMMANDS dict | plugin/runtime/brother/mode/*_facade.py, one per tool | 13 *_facade test files, each asserting the real COMMANDS key set read from source | MIGRATED |
| 2 worktree tools beyond the thin-CLI set (bm_autosave.py, bm_reconcile_worktrees.py) | products/brothermode/tools/bm_autosave.py, bm_reconcile_worktrees.py | plugin/runtime/brother/mode/autosave_facade.py, reconcile_worktrees_facade.py | test_autosave_facade.py, test_reconcile_worktrees_facade.py (real linked worktree in a temp dir) | MIGRATED |
| Safe Unwatched Time (bm_controller.py's `--unattended` 8-condition preflight) | products/brothermode/tools/bm_controller.py's `unattended_preflight` | plugin/runtime/brother/mode/controller_facade.py (start/step COMMANDS) plus new sut.py (segmentation journal, a genuinely new capability, not a migration) | test_store_class_parity.py (object identity for unattended_preflight); test_sut.py (8 tests, crash/resume vs real intervention) | MIGRATED |
| sbe_checks.py vocabulary (Check, KINDS, VACUOUS_VALUES, answered, answered_as, stated) | products/brothersbe/tools/sbe_checks.py | plugin/runtime/brother/assurance/checks_facade.py | test_checks_facade.py (6 tests), test_registry_parity.py | MIGRATED |
| Engineering evidence lifecycle sensitivity stage (a test that cannot fail is downgraded) | new capability, no legacy owner (built directly against unit E's objective) | plugin/runtime/brother/assurance/evidence_lifecycle.py | test_evidence_lifecycle.py (6 tests, real temp git repo, real dead-seam and discriminating test cases) | MIGRATED |
| bds.py claim-checker vocabulary (PASS/FAIL/NODATA/ORIGINS, main, selftest/receipt subcommands) | products/brotherds/bds.py | plugin/runtime/brother/data/bds_facade.py | test_bds_facade.py (3 tests), test_bds_cli_delegation.py (3 tests, a real selftest run through the facade) | MIGRATED |
| Real-system data adapters (Snowflake, Databricks, dbt, local DuckDB reference) | new capability, no legacy owner (products/brotherds has no doctor/maturity/evidence-envelope concept per U5's own corrected finding) | plugin/runtime/brother/data/adapters/ | test_adapters.py (13 tests, independent credential-scan markers not reused from the redaction code's own pattern list) | MIGRATED |
| bm_vault_lint.py frontmatter schema contract (required fields per note type, id/date formats, vocabularies loaded by path) | products/brothermode/tools/bm_vault_lint.py, bm_vault_authority.py, bm_vault_lifecycle.py, bm_vault_ids.py, bm_vault_temporal.py | plugin/runtime/brother/vault/vault_lint_facade.py, vault_facade.py | test_vault_facades.py (6 tests, synthetic vault fixtures only, never the founder's real Kay Vault) | MIGRATED |
| vault_recall_hook.py untrusted-data framing (wrap_untrusted, per-line instruction flagging) | products/brothermode/tools/vault_recall_hook.py | plugin/runtime/brother/vault/recall_hook_facade.py | test_recall_untrusted_framing.py (5 tests, an adversarial fixture impersonating already-verified evidence) | MIGRATED |
| Native mobile safety-gating capability (silent-permissive-default detection) | new capability, no legacy owner (converged read-only from a real production iOS app's own documented incident, generalized, no app identity on this public surface) | plugin/runtime/brother/mobile/silent_permissive_default.py | test_silent_permissive_default.py (4 tests, the exact real incident shape plus a negative case) | MIGRATED |
| Android tooling groundwork (honest NO-DATA vs false PASS) | new capability, no legacy owner | plugin/runtime/brother/mobile/android.py | test_android.py (6 tests, plus a real unmocked run on this machine confirming NO-DATA) | MIGRATED |
| Cross-domain capability composition (one run_id, one receipt across N domains) | new capability, no legacy owner | plugin/runtime/brother/core/registry.py | test_registry.py (14 tests: 6 reconstructed composition scenarios, 5 regressions seeded from a real Muse adversarial review) | MIGRATED |
| Canonical branch authority (main/master/trunk, detached HEAD, no/stale remote, override, nested worktree) | new capability, no legacy owner (built directly against the U0 divergence incident) | plugin/runtime/brother/core/branch_authority.py | test_branch_authority.py (9 tests, all 8 named edge cases against real git repos plus the exact U0 incident class) | MIGRATED |
| Leases and collision identity (one writer per protected root) | new capability, no legacy owner | plugin/runtime/brother/core/leases.py | test_leases.py (14 tests, a real 5-process OS-level race) | MIGRATED |
| Context provenance manifest (no full prompt ever persisted) | new capability, no legacy owner | plugin/runtime/brother/core/context.py | test_context.py (10 tests, a real sensitive-marker redaction proof) | MIGRATED |
| Evidence-subordinate closeout (a prose-only domain cannot back a PASS) | new capability, no legacy owner | plugin/runtime/brother/core/receipt.py | test_receipt_acceptance.py (extended, 55 tests total, a real seeded false-green found and closed via Muse review) | MIGRATED |

| BrotherSBE gate runner (sbe_gate.py, 2400+ lines) | products/brothersbe/tools/sbe_gate.py | plugin/runtime/brother/assurance/gate_facade.py | test_engine_facades.py (--help exits 0 via the real sys.argv path, since gate.py has no argv parameter) | MIGRATED (smoke-level: loader parity proven, full internal behavior out of scope for a facade) |
| BrotherSBE testkit, authority hook, fence hook, telemetry, evals (sbe_testkit.py, sbe_authority_hook.py, sbe_fence_hook.py, sbe_telemetry.py, evals/run_evals.py, 9000+ lines) | products/brothersbe/tools/ and evals/ | plugin/runtime/brother/assurance/{testkit,authority_hook,fence_hook,telemetry,run_evals}_facade.py | test_engine_facades.py (6 tests: all mains callable, real file resolution, --help/no-op exit codes) | MIGRATED (smoke-level, same scope note as the gate row) |
| BrotherDS transitive siblings (vault_bridge.py, forecast_score.py, packs.py) | products/brotherds/{vault_bridge,forecast_score,packs}.py, imported by bds.py as bare sibling modules | Already reachable through the existing bds_facade.py: no new facade file needed | test_bds_transitive_siblings.py (4 tests, object identity against a direct sibling import) | MIGRATED |
| BrotherDS's 5 real mdm_*.py CLI tools (mdm_normalize, mdm_jan_lookup, mdm_eval, mdm_derive, mdm_audit) | products/brotherds/mdm_*.py, each with a real `def main` | plugin/runtime/brother/data/mdm_*_facade.py | test_mdm_facades.py (5 tests, each tool's own real `--selftest` flag run through its facade) | MIGRATED |
| BrotherDS's 2 further transitive siblings (mdm_validate.py, audit_provenance.py) | Pure library modules, no CLI of their own, imported by mdm_audit.py as bare siblings | Already reachable through mdm_audit_facade.py: no new facade file needed | test_mdm_facades.py (3 more tests, object identity against a direct sibling import) | MIGRATED |
| BrotherDS's 11 pack_*.py modules (pack_experiment, pack_detection, pack_mdm, pack_mdm_science, pack_mdm_decision, pack_mdm_calibration, pack_mdm_locale, pack_mdm_audit, pack_mdm_coverage, pack_mdm_identity, pack_pipeline) | Pure library modules, no CLI of their own; bds.py's own `_PACK_MODULE_NAMES` loop imports all 11 unconditionally at module load time | Already reachable through the existing bds_facade.py: no new facade file needed | test_bds_pack_modules.py (2 tests, all 11 confirmed present, object identity for one sample) | MIGRATED |
| BrotherSBE's real primary CLI, the "sbe" command (36 real commands: status, verify, work, gate, decide, score, policy, task, review, and 27 more) | products/brothersbe/src/brothersbe/cli.py (a proper package, relative imports, already described by its own __init__.py as "a facade over those tools rather than a reimplementation of them") | plugin/runtime/brother/assurance/brothersbe_cli_facade.py (package-style loading: src/ on sys.path, real `import brothersbe.cli`, never exec'd as an isolated file) | test_brothersbe_cli_facade.py (6 tests: all 36 commands confirmed present, --version and no-args handled, and 6 sampled commands with genuinely different lazy-imported internals: status, verify, score, decide, policy, work, run for real without error) | MIGRATED (the entry point itself; see scope note below on its ~30 lazily-imported internals) |

BROTHERDS IS NOW FULLY ACCOUNTED FOR: all 23 top-level products/brotherds/*.py
files are either facaded or proven transitively reachable (bds.py itself;
vault_bridge/forecast_score/packs; all 11 pack_*.py modules; the 5 real
mdm_*.py CLI tools; mdm_validate.py and audit_provenance.py). lessons.py is
the one remaining top-level file, and it has no `def main` of its own and is
imported only by products/brotherds/tests/grade_lessons.py (test-grading
infrastructure, not product capability), so it is correctly out of scope,
the same as products/brotherds/examples/ and products/brotherds/tests/.

STILL NO-DATA/BLOCKED, not yet in this matrix's MIGRATED column: the
brother_paths.py row above (unresolved from U0), and the ~65
bm_vault_*.py siblings beyond bm_vault.py/bm_vault_lint.py in
products/brothermode/tools/ (a Brother Mode subsystem, not brotherds).

The brothersbe CLI facade closes the single most important remaining
gap (the real, most-invoked entry point end users and other tools
actually call), but its ~30 lazily-imported internal tool modules
(each of the other 30 of 36 commands' own sbe_*.py implementation)
are reached correctly ONLY WHEN their owning command actually runs,
by construction of the facade always resolving to the real file on
disk, never a copy: this is a structural guarantee, not a per-module
test, and is named here as exactly that rather than overclaimed as
individually proven.

U8's own done_check (marketplace plugin count of 1, `claude plugin
validate` passing) is NOT attempted from this matrix state: brotherds
is now fully closed and brothersbe's real entry point is facaded, but
the ~65 bm_vault_* siblings remain a real, named, unclosed gap. This
matrix update is deliberately scoped to recording real evidence, not
to clearing U8 for its own irreversible-ish marketplace change while
that gap stands.

Draft credit: Deepseek (deepseek/deepseek-v4.1-flash, xhigh), given the verified finding
above as its full input; row spot checked against the tree before being copied here
(see docs/architecture/inventory/PROVENANCE.md addendum for the exact commands run).
This 2026-09-19 update (18 additional rows) was written directly by Fable, not
Deepseek, consolidating real evidence already collected and verified earlier
in the same session for units U3 through U7.

## L5e.2 claim spot-check

Every MIGRATED and COVERED row above is spot-checked against the real tree
by scripts/test_l5e_2_parity_matrix_claims.py. That check extracts the
backticked tokens from each such row and verifies that every token names a
real path or module in the tree and, when it names a symbol, that the symbol
is defined somewhere under the repository root. A row set with zero
extractable tokens is a block rather than a pass, never the safe case.

| Capability | Legacy owner | Unified owner | Deciding test | Status |
| --- | --- | --- | --- | --- |
| PARITY-MATRIX claim spot-check, self-check of `extract_parity_claims` and `verify_claim` | docs/architecture/PARITY-MATRIX.md (claim source) | scripts/test_l5e_2_parity_matrix_claims.py | test_parity_claims_exist | COVERED |
