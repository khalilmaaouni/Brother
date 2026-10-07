# U8: bm_vault_*.py facade audit

Real audit, 8 parallel lanes of 8 files each, run against
the `brother-unify-1.1` worktree of this repository,
2026-09-20. Method per lane: grep for a real `if __name__ == "__main__":`
CLI entry point, and grep bm_vault.py / bm_vault_cli.py / bm_vault_lint.py
(the only facaded or front-door files) for a REAL Python import or
`importlib` dynamic load-by-path of the module. A `subprocess.call` from
bm_vault_cli.py's VERBS dispatch does NOT count as coverage: a subprocess
gets its own `sys.modules`, so facading the caller does not facade the
callee. This distinction, made independently by three separate lanes,
is the one correction to the bds.py precedent this audit found: bds.py's
free coverage came from real imports, and the same test applies here,
but bm_vault_cli.py's own dispatch pattern is mostly subprocess, not import,
so it buys far less free coverage than bds.py's sibling-import pattern did.

## Tally across all 64 files (complete, 8 of 8 lanes, recounted directly from the per-lane sections below)

- Needs its own facade today: 43 (lane 1: 3, lane 2: 6, lane 3: 5, lane 4:
  8 (all of them, since lane 4's 2 "covered" cases are conditional on
  bm_vault_export.py being facaded first, which itself needs a facade),
  lane 5: 6, lane 6: 6, lane 7: 6, lane 8: 3)
- Covered transitively by a real import from an already-facaded file: 20
  (lane 1: 5, lane 2: 1, lane 3: 3, lane 5: 2, lane 6: 2, lane 7: 2, lane 8: 5)
- Pure library, no facade needed at all: 1 (bm_vault_context.py, lane 2)
- 43 + 20 + 1 = 64, checks out.
- Once bm_vault_export.py is facaded (already counted in the 43), 2 more
  files (events.py, exchange.py) become free, dropping the real remaining
  facade count to 41.

## Lane 1 (bm_vault_analyzer, asof, assertions, attribute_provenance, attributes, audit, authority, catalog)

- bm_vault_analyzer.py: COVERED_TRANSITIVELY_BY bm_vault.py (`_load_bm_vault_analyzer`)
- bm_vault_asof.py: COVERED_TRANSITIVELY_BY bm_vault.py (`_load_enrichment`)
- bm_vault_assertions.py: NEEDS_OWN_FACADE (only reached from unfacaded bm_vault_interchange.py)
- bm_vault_attribute_provenance.py: NEEDS_OWN_FACADE (only reached from unfacaded bm_vault_enrich_gate.py)
- bm_vault_attributes.py: NEEDS_OWN_FACADE (only reached from unfacaded bm_vault_realdata.py)
- bm_vault_audit.py: COVERED_TRANSITIVELY_BY bm_vault.py (`_load_bm_vault_audit`)
- bm_vault_authority.py: COVERED_TRANSITIVELY_BY bm_vault.py (dynamic import) and bm_vault_lint.py (sibling load); also a real bm_vault_cli.py VERBS subcommand
- bm_vault_catalog.py: COVERED via bm_vault_cli.py's cmd_commit() (real subcommand dispatch: this one is treated as covered per lane 1's read; flagged for a second look given the subprocess-vs-import distinction other lanes applied more strictly)

## Lane 2 (bm_vault_census_ext, cite, cli, closure, compose, context, contract, contradiction)

- bm_vault_census_ext.py: NEEDS_OWN_FACADE (real `__main__` CLI)
- bm_vault_cite.py: NEEDS_OWN_FACADE (real `__main__` CLI)
- bm_vault_cli.py: NEEDS_OWN_FACADE (real `__main__` CLI; this is "the vault's one front door")
- bm_vault_closure.py: NEEDS_OWN_FACADE (real `__main__` CLI)
- bm_vault_compose.py: NEEDS_OWN_FACADE (real `__main__` CLI)
- bm_vault_context.py: SUPPORT_FILE_NO_FACADE_NEEDED (no `__main__`, no argparse; pure RequestContext/tenancy library imported only by bm_vault_serve.py)
- bm_vault_contract.py: NEEDS_OWN_FACADE (real `__main__` CLI)
- bm_vault_contradiction.py: COVERED_TRANSITIVELY_BY bm_vault.py (`_load_bm_vault_contradiction`, line 408-420, called line 2610)

**bm_vault_cli.py's real dispatch/import list** (subprocess unless noted "import"): bm_vault.py, bm_vault_graph.py, bm_vault_retention.py, bm_vault_posture.py, bm_vault_lint.py, bm_vault_contract.py, bm_vault_curate.py, bm_vault_ids.py, bm_vault_authority.py, bm_vault_staleness.py, bm_vault_pack.py, bm_vault_catalog.py (all subprocess); loads by path: bm_vault.py, scripts/bm_commit_msg_hook.py, bm_autosave.py, scripts/bm_vault_precommit_hook.py, bm_vault_tiers.py. None of this is free coverage today since bm_vault_cli.py itself is unfacaded; re-check once it is.

## Lane 3 (bm_vault_crosswalk, curate, decay, digest, distill, enrich, enrich_gate, enrich_index)

- bm_vault_crosswalk.py: NEEDS_OWN_FACADE (real importers are unfacaded bm_vault_realdata.py/bm_vault_shapes.py/bm_vault_attributes.py)
- bm_vault_curate.py: NEEDS_OWN_FACADE (bm_vault_cli.py only subprocess-calls it; real importer is unfacaded bm_vault_pane.py)
- bm_vault_decay.py: COVERED_TRANSITIVELY_BY bm_vault.py (`_load_bm_vault_decay`, loaded by path)
- bm_vault_digest.py: NEEDS_OWN_FACADE (no real importer anywhere)
- bm_vault_distill.py: NEEDS_OWN_FACADE (no real importer anywhere)
- bm_vault_enrich.py: COVERED_TRANSITIVELY_BY bm_vault_enrich_index.py (itself covered by bm_vault.py)
- bm_vault_enrich_gate.py: NEEDS_OWN_FACADE (leaf consumer of enrich.py, nobody imports it)
- bm_vault_enrich_index.py: COVERED_TRANSITIVELY_BY bm_vault.py (`import bm_vault_enrich_index as _eix`, line 1165)

## Lane 4 (bm_vault_entity, events, exchange, export, graph, heat_temporal, hierarchy_req, identity)

First attempt landed in the wrong worktree (agent-a7ba7226179b5b9a9, a
public export tree) and correctly reported the blocker instead of
fabricating a result. Redispatched; the retry hit a second, different
blocker (its own Edit-tool write fence pinned it to its own agent
worktree regardless of `cd`), also correctly reported rather than
fabricated, and pasted its findings as text for this session to merge
directly instead.

- bm_vault_entity.py: NEEDS_OWN_FACADE (real `__main__`, no importer anywhere but its own test)
- bm_vault_events.py: NEEDS_OWN_FACADE for now; becomes COVERED_TRANSITIVELY_BY bm_vault_export.py once export.py itself is facaded (real sibling import, bm_vault_export.py:141)
- bm_vault_exchange.py: NEEDS_OWN_FACADE for now; same conditional as events.py via bm_vault_export.py:142
- bm_vault_export.py: NEEDS_OWN_FACADE (real `__main__`; facading this one retires events.py and exchange.py for free)
- bm_vault_graph.py: NEEDS_OWN_FACADE (bm_vault_cli.py only reaches it via `subprocess.run`, a separate process, no import coverage)
- bm_vault_heat_temporal.py: NEEDS_OWN_FACADE (it loads bm_vault.py itself by path, the wrong direction for coverage; its real importer, vault_recall_hook.py, is itself unfacaded)
- bm_vault_hierarchy_req.py: NEEDS_OWN_FACADE (real importer is unfacaded bm_vault_closure.py, wrong direction)
- bm_vault_identity.py: NEEDS_OWN_FACADE (no importer but its own test; it loads a contract module itself, not the reverse)

Tally: 6 of 8 need their own facade outright; 2 (events, exchange) drop to
covered once export.py is facaded, so all 8 need real attention today, none
free right now. No coverage flows down from the two already-facaded files
into any of these 8; whatever coverage exists here runs sideways to other
unfacaded siblings, unlike the bds.py precedent.

## Lane 5 (bm_vault_ids, intake, interchange, jbench, labels, ledger, lifecycle, lineage)

- bm_vault_ids.py: COVERED_TRANSITIVELY_BY bm_vault.py (`_load_enrichment`, line 357) and bm_vault_lint.py
- bm_vault_intake.py: NEEDS_OWN_FACADE (real `__main__` CLI, no importer)
- bm_vault_interchange.py: NEEDS_OWN_FACADE (real `__main__` CLI, no importer)
- bm_vault_jbench.py: NEEDS_OWN_FACADE (real `__main__` CLI, no importer)
- bm_vault_labels.py: NEEDS_OWN_FACADE (real `__main__` CLI, no importer)
- bm_vault_ledger.py: NEEDS_OWN_FACADE (real `__main__` CLI, no importer)
- bm_vault_lifecycle.py: COVERED_TRANSITIVELY_BY bm_vault.py (`_load_bm_vault_lifecycle`, line 337) and bm_vault_lint.py
- bm_vault_lineage.py: NEEDS_OWN_FACADE (real `__main__` CLI, no importer)

## Lane 6 (bm_vault_lock, notify, pack, pane, plugins, policy, posture, principals)

- bm_vault_lock.py: NEEDS_OWN_FACADE (real `__main__` CLI)
- bm_vault_notify.py: NEEDS_OWN_FACADE (real `__main__` CLI)
- bm_vault_pack.py: NEEDS_OWN_FACADE (bm_vault_cli.py only subprocess-calls it)
- bm_vault_pane.py: NEEDS_OWN_FACADE (real `__main__` CLI; this is the pending-approval HTTP surface)
- bm_vault_plugins.py: NEEDS_OWN_FACADE (real `__main__` CLI)
- bm_vault_policy.py: COVERED_TRANSITIVELY_BY bm_vault.py (`_load_bm_vault_policy`, line 540/3173, real import)
- bm_vault_posture.py: NEEDS_OWN_FACADE (bm_vault_cli.py only subprocess-calls it)
- bm_vault_principals.py: COVERED_TRANSITIVELY_BY bm_vault.py (`_load_bm_vault_principals`, line 555/3143, real import)

## Lane 7 (bm_vault_promote, promotions, provenance, read_audit, realdata, retention, retier, route)

- bm_vault_promote.py: NEEDS_OWN_FACADE (no importer anywhere)
- bm_vault_promotions.py: NEEDS_OWN_FACADE (no importer anywhere)
- bm_vault_provenance.py: COVERED_TRANSITIVELY_BY bm_vault.py (real `importlib` dynamic load inside recall's annotation path)
- bm_vault_read_audit.py: COVERED_TRANSITIVELY_BY bm_vault.py (same mechanism as provenance.py)
- bm_vault_realdata.py: NEEDS_OWN_FACADE (no importer anywhere)
- bm_vault_retention.py: NEEDS_OWN_FACADE (only reached via bm_vault_cli.py subprocess dispatch, not an import)
- bm_vault_retier.py: NEEDS_OWN_FACADE (no importer anywhere)
- bm_vault_route.py: NEEDS_OWN_FACADE (no importer anywhere)

## Lane 8 (bm_vault_seams, serve, shapes, staleness, survivorship, temporal, tiers, triage)

- bm_vault_seams.py: COVERED_TRANSITIVELY_BY bm_vault.py and/or bm_vault_cli.py
- bm_vault_serve.py: COVERED_TRANSITIVELY_BY bm_vault.py and/or bm_vault_cli.py
- bm_vault_shapes.py: NEEDS_OWN_FACADE (not reachable from bm_vault.py/bm_vault_cli.py/bm_vault_lint.py)
- bm_vault_staleness.py: COVERED_TRANSITIVELY_BY bm_vault.py and/or bm_vault_cli.py
- bm_vault_survivorship.py: NEEDS_OWN_FACADE (imports bm_vault_triage.py, the wrong direction to grant either coverage)
- bm_vault_temporal.py: COVERED_TRANSITIVELY_BY bm_vault.py, bm_vault_cli.py, and bm_vault_lint.py
- bm_vault_tiers.py: COVERED_TRANSITIVELY_BY bm_vault.py and/or bm_vault_cli.py
- bm_vault_triage.py: NEEDS_OWN_FACADE (not reachable from any already-facaded or front-door file)

## Open question for whoever implements the facades

Lane 1 read `bm_vault_catalog.py` as covered via `bm_vault_cli.py`'s
`cmd_commit()` subprocess dispatch; lanes 2, 3, 6, and 7 explicitly excluded
subprocess-dispatched files from "covered" for the same reason (a subprocess
gets its own `sys.modules`, so the caller being facaded does not facade the
callee). This is a real, small inconsistency between lanes worth resolving
before anyone facades bm_vault_catalog.py on the strength of a coverage
claim that may not hold under the stricter, more-consistently-applied rule.
