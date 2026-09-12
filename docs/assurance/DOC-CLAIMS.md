# Documentation claim register

This register stops public Brother docs from drifting into incompatible current-state stories. `NO-DATA` in the Test column means no executable documentation check is asserted here yet.

| ID | Current claim | Authority | Test/evidence | Public repeaters |
| --- | --- | --- | --- | --- |
| DOC-001 | Bare Brother discovers/resumes unfinished work first; otherwise asks what the user is trying to do. | `bundle/skills/using-brother/SKILL.md` | router/skill test or `NO-DATA` | README, routing ref |
| DOC-002 | Trivial reversible work gets no Brother trust ceremony. | same routing skill | `NO-DATA` | README, routing ref, personas |
| DOC-003 | Substantial trusted changes route to execution provenance; risk-bearing work routes to assurance. | same routing skill | routing test or `NO-DATA` | README, routing ref |
| DOC-004 | PASS/FAIL/NO-DATA are evidence outcomes; NO-DATA is not PASS. | runtime evidence modules / `SYSTEM.md` | receipt/battery tests | README, verdict ref, tutorials |
| DOC-005 | Human acceptance stays separate from evidence. | acceptance/authority runtime | `scripts/test_fable_authority.py` where current | README, human authority |
| DOC-006 | Work units declare objective, done check, writes, and deps. | router details + engine | brother-run/plan tests | work-unit ref, plan how-to |
| DOC-007 | In-session plan files stay outside the target repo. | router details | dirty-tree/engine test or `NO-DATA` | work-unit ref, plan how-to |
| DOC-008 | Outcome-contract v1 persona enum is analyst/lead/developer/NO-DATA, not profession-page names. | `docs/schema/outcome-contract-v1.json` | contract checker | outcome-contract ref |
| DOC-009 | Product-installer and marketplace hook scoping differ; repository opt-out exists in the current scoped-config model. | installer implementations | hook-scope tests | hooks ref, install docs |
| DOC-010 | Codex does not use Claude's Brother slash-command surface. | `using-brother` skill | Codex smoke/battery | README, Codex docs |
| DOC-011 | Vault memory cannot overrule current evidence/human authority. | Vault/router guidance | Vault behavior tests; impact may be NO-DATA | README, Vault docs |
| DOC-012 | `SYSTEM.md` is generated and not hand-edited. | `SYSTEM.md` header/generator | system-doc check | docs home, state/files |
| DOC-013 | Public docs cannot assume a specific runtime-created `docs/decisions/*.json` exists. | intake workflow/public tree | docs link check | outcome/decisions docs |
| DOC-014 | No receipt means no Brother delivery proof; process exit alone is insufficient. | using-brother + runtime | Brother-run receipt tests | README, receipt ref, smoke docs |

## Update rule

The 2026-09-12 adoption-path revision adds the following explicit claims. Source inspection supports the behavioral descriptions; user benefit remains a separate empirical question.

| ID | Current claim | Authority | Test/evidence | Public repeaters |
| --- | --- | --- | --- | --- |
| DOC-015 | Explicit A0 is refused when the capability floor cannot support it; other modes can proceed marked not enforced. | `scripts/brother_run.py`, `scripts/managed_safety.py` | managed-safety and brother-run tests | README, safety boundaries, delegation guide |
| DOC-016 | Vault retrieval can combine lexical, anchor, and link signals; fast mode skips dense retrieval. | `products/brothermode/tools/bm_vault.py` | Vault retrieval tests; reduced repeat errors remain NO-DATA | README, Vault how-to/reference |
| DOC-017 | Resume preserves integrated units and continuation can directly resume a single match. | `scripts/brother_run.py` | brother-run tests | recovery guide |
| DOC-018 | Auditable records do not establish independent certification or reduced human review time. | measurement protocols | Acceptance Time human trial: NO-DATA | README, comparison |

When behavior changes, update authority/test and every public repeater in the same change. Never leave a stale start page for later cleanup.
