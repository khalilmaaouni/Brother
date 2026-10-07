# L5c test integrity audit

This is the controlled evidence document for unit L5c. Human written text
lives outside the two markers below and is never overwritten by the tool.
The generated region between the markers is produced by
`scripts/l5c_audit.render_doc` from the run ledger and must equal
`scripts/l5c_audit.render_region(ledger)` byte for byte; `verify_evidence`
refuses any document where it does not.

The verifier enforces every L5c.5 requirement, and each one is checked
separately so one drifted byte cannot hide behind another:

* L5C5-REQ-1: each generated-region marker occurs exactly once.
* L5C5-REQ-2: every locked hash still matches the tree now.
* L5C5-REQ-3: every recorded `old` anchor still resolves exactly once.
* L5C5-REQ-4: every `evidence_quote` is a substring of its recorded tail
  and at most 300 characters long.
* L5C5-REQ-5: the meta battery holds nine rows, every one KILLED and
  attributed.
* L5C5-REQ-6: the gate is true with all five condition booleans present.

A missing ledger, a missing or duplicated marker, a drifted hash, a moved
anchor, a mismatched region, an oversized or absent quote, an incomplete
meta battery or a false gate is reported by name and blocks. None of them
is ever read as a pass, because a fix whose check cannot fail is not a fix.

`render_doc` regenerates the region below from the ledger, so this
document cannot pass by being written once: any change to the ledger
without a re-render is a REGION problem.

## Result of run 3 (2026-09-30)

score = 10.0/10 on the sampled denominator: 10 of 10 pre-registered
mutants killed and attributed (vault 4, dispatch 3, hook 3; 4 of the
kills are state backed), and the meta battery M0 to M8 on
`scripts/l5c_audit.py` itself is 9 of 9 killed and attributed. Each row's
red line is quoted in the region below. The gate is the integer bar
(killed * 20 >= 17 * total), not the rounded score.

Limits, stated rather than hidden: this is a sample of 10 drawn by the
sorted rule from 44 vault facade suites (61 tests), 20 dispatch tests and
32 hook tests, not a census. The four vault samples are object identity
tests of near identical facades, so they probe one property four times.
The vault suites locate their source module through the repository's
`.git`; the probe's scratch copy has none, so the run points each facade
at its source through the facade's own `BROTHER_VAULT_<NAME>_PATH`
variable. The earlier probe over 9 modules (51 of 79 killed before its
fixes, every former survivor killed after) is recorded in the unit's
evidence in the plan, not here.

<!-- L5C-GENERATED-BEGIN -->
L5C-REGION
```json
{
 "conditions": {
  "categories": true,
  "measured": true,
  "rate": true,
  "sampled_at_least_ten": true,
  "state_backed": true
 },
 "evidence": [
  {
   "evidence_quote": "AssertionError: <function <lambda> at 0x10884c040> is not <function main at 0x108864ea0>",
   "id": "L5C3-VAULT-01",
   "output_tail": "F\n======================================================================\nFAIL: test_facade_main_is_the_source_modules_main (plugin.runtime.brother.vault.test_assertions_facade.TestAssertionsFacadeIsTheRealModule.test_facade_main_is_the_source_modules_main)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/plugin/runtime/brother/vault/test_assertions_facade.py\", line 25, in test_facade_main_is_the_source_modules_main\n    self.assertIs(facade_main, source_module.main)\n    ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: <function <lambda> at 0x10884c040> is not <function main at 0x108864ea0>\n\n----------------------------------------------------------------------\nRan 1 test in 0.000s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: <function <lambda> at 0x108304040> is not <function main at 0x10837e7a0>",
   "id": "L5C3-VAULT-02",
   "output_tail": "F\n======================================================================\nFAIL: test_facade_main_is_the_source_modules_main (plugin.runtime.brother.vault.test_attribute_provenance_facade.TestAttributeProvenanceFacadeIsTheRealModule.test_facade_main_is_the_source_modules_main)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/plugin/runtime/brother/vault/test_attribute_provenance_facade.py\", line 25, in test_facade_main_is_the_source_modules_main\n    self.assertIs(facade_main, source_module.main)\n    ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: <function <lambda> at 0x108304040> is not <function main at 0x10837e7a0>\n\n----------------------------------------------------------------------\nRan 1 test in 0.000s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: <function <lambda> at 0x10a713ec0> is not <function main at 0x10a73e8e0>",
   "id": "L5C3-VAULT-03",
   "output_tail": "F\n======================================================================\nFAIL: test_facade_main_is_the_source_modules_main (plugin.runtime.brother.vault.test_attributes_facade.TestAttributesFacadeIsTheRealModule.test_facade_main_is_the_source_modules_main)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/plugin/runtime/brother/vault/test_attributes_facade.py\", line 25, in test_facade_main_is_the_source_modules_main\n    self.assertIs(facade_main, source_module.main)\n    ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: <function <lambda> at 0x10a713ec0> is not <function main at 0x10a73e8e0>\n\n----------------------------------------------------------------------\nRan 1 test in 0.000s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: <function <lambda> at 0x10a4a4220> is not <function main at 0x10a4a6980>",
   "id": "L5C3-VAULT-04",
   "output_tail": "F\n======================================================================\nFAIL: test_facade_main_is_the_source_modules_main (plugin.runtime.brother.vault.test_census_ext_facade.TestCensusExtFacadeIsTheRealModule.test_facade_main_is_the_source_modules_main)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/plugin/runtime/brother/vault/test_census_ext_facade.py\", line 25, in test_facade_main_is_the_source_modules_main\n    self.assertIs(facade_main, source_module.main)\n    ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: <function <lambda> at 0x10a4a4220> is not <function main at 0x10a4a6980>\n\n----------------------------------------------------------------------\nRan 1 test in 0.000s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: BudgetExceeded not raised",
   "id": "L5C3-DISPATCH-01",
   "output_tail": "enrouter_dispatch: cap grant ignored (expired at 2026-09-19T00:00:00+00:00); cap stays 20.0\nopenrouter_dispatch: cap grant ignored (until carries no offset); cap stays 20.0\nopenrouter_dispatch: cap grant ignored ('until'); cap stays 20.0\nopenrouter_dispatch: cap grant ignored (daily_cap is not a finite number); cap stays 20.0\nopenrouter_dispatch: cap grant ignored (daily_cap is not a finite number); cap stays 20.0\nopenrouter_dispatch: cap grant ignored (Expecting property name enclosed in double quotes: line 1 column 3 (char 2)); cap stays 20.0\n................\n======================================================================\nFAIL: test_a_caller_daily_cap_above_the_configured_cap_is_clamped_down (plugin.runtime.brother.core.test_openrouter_dispatch.TestOpenRouterDispatch.test_a_caller_daily_cap_above_the_configured_cap_is_clamped_down)\nA caller passing daily_cap far above what is actually\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/plugin/runtime/brother/core/test_openrouter_dispatch.py\", line 137, in test_a_caller_daily_cap_above_the_configured_cap_is_clamped_down\n    with self.assertRaises(ledger.BudgetExceeded):\n         ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: BudgetExceeded not raised\n\n----------------------------------------------------------------------\nRan 20 tests in 0.018s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: 1000000 not less than or equal to 4 : a caller's own max_slots must never raise the configured ceiling",
   "id": "L5C3-DISPATCH-02",
   "output_tail": "til'); cap stays 20.0\nopenrouter_dispatch: cap grant ignored (daily_cap is not a finite number); cap stays 20.0\nopenrouter_dispatch: cap grant ignored (daily_cap is not a finite number); cap stays 20.0\nopenrouter_dispatch: cap grant ignored (Expecting property name enclosed in double quotes: line 1 column 3 (char 2)); cap stays 20.0\n................\n======================================================================\nFAIL: test_a_caller_max_slots_above_the_configured_max_is_clamped_down (plugin.runtime.brother.core.test_openrouter_dispatch.TestOpenRouterDispatch.test_a_caller_max_slots_above_the_configured_max_is_clamped_down)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/plugin/runtime/brother/core/test_openrouter_dispatch.py\", line 159, in test_a_caller_max_slots_above_the_configured_max_is_clamped_down\n    self.assertLessEqual(\n    ~~~~~~~~~~~~~~~~~~~~^\n        args[1], dispatch.DEFAULT_MAX_SLOTS,\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n        \"a caller's own max_slots must never raise the configured ceiling\")\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: 1000000 not less than or equal to 4 : a caller's own max_slots must never raise the configured ceiling\n\n----------------------------------------------------------------------\nRan 20 tests in 0.017s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: 5.0 != 20.0 : {'daily_cap': 5.0, 'until': '2026-09-21T00:00:00+00:00'}",
   "id": "L5C3-DISPATCH-03",
   "output_tail": "....openrouter_dispatch: cap grant ignored (expired at 2026-09-19T00:00:00+00:00); cap stays 20.0\nopenrouter_dispatch: cap grant ignored (until carries no offset); cap stays 20.0\nopenrouter_dispatch: cap grant ignored ('until'); cap stays 20.0\nopenrouter_dispatch: cap grant ignored (daily_cap is not a finite number); cap stays 20.0\nopenrouter_dispatch: cap grant ignored (daily_cap is not a finite number); cap stays 20.0\nF...............\n======================================================================\nFAIL: test_a_founder_cap_grant_lifts_the_cap_only_while_valid (plugin.runtime.brother.core.test_openrouter_dispatch.TestOpenRouterDispatch.test_a_founder_cap_grant_lifts_the_cap_only_while_valid)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/plugin/runtime/brother/core/test_openrouter_dispatch.py\", line 300, in test_a_founder_cap_grant_lifts_the_cap_only_while_valid\n    self.assertEqual(grant(**bad), 20.0, msg=repr(bad))\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: 5.0 != 20.0 : {'daily_cap': 5.0, 'until': '2026-09-21T00:00:00+00:00'}\n\n----------------------------------------------------------------------\nRan 20 tests in 0.018s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: False is not true : stop -> {'decision': 'deny', 'reason': 'Brother Antigravity Stop blocked on unhandled exception: RuntimeError'}",
   "id": "L5C3-HOOK-01",
   "output_tail": "_hook.py\").read_bytes(),\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n        Path(HOOK_SCRIPT).read_bytes(),\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n        \"Refresh the shipped adapter from scripts/brother_antigravity_hook.py\",\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n    )\n    ^\nAssertionError: b'#!/[15062 chars]n\": \"continue\",\\n                \"reason\": f\"B[506 chars]()\\n' != b'#!/[15062 chars]n\": \"deny\",\\n                \"reason\": f\"Broth[502 chars]()\\n' : Refresh the shipped adapter from scripts/brother_antigravity_hook.py\n\n======================================================================\nFAIL: test_a_crash_answers_in_the_events_own_schema (__main__.TestBrotherAntigravityHook.test_a_crash_answers_in_the_events_own_schema)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_brother_antigravity_hook.py\", line 291, in test_a_crash_answers_in_the_events_own_schema\n    self.assertTrue(check(out), msg=\"%s -> %r\" % (mode, out))\n    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: False is not true : stop -> {'decision': 'deny', 'reason': 'Brother Antigravity Stop blocked on unhandled exception: RuntimeError'}\n\n----------------------------------------------------------------------\nRan 33 tests in 1.244s\n\nFAILED (failures=2)\n"
  },
  {
   "evidence_quote": "AssertionError: 'allow' != 'deny'",
   "id": "L5C3-HOOK-02",
   "output_tail": "           \"reason\": f\"Brot[1387 chars]()\\n' : Refresh the shipped adapter from scripts/brother_antigravity_hook.py\n\n======================================================================\nFAIL: test_a_hook_launched_without_its_event_name_blocks (__main__.TestBrotherAntigravityHook.test_a_hook_launched_without_its_event_name_blocks)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_brother_antigravity_hook.py\", line 267, in test_a_hook_launched_without_its_event_name_blocks\n    self.assertEqual(out.get(\"decision\"), \"deny\")\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: 'allow' != 'deny'\n- allow\n+ deny\n\n\n======================================================================\nFAIL: test_unknown_mode_blocks (__main__.TestBrotherAntigravityHook.test_unknown_mode_blocks)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_brother_antigravity_hook.py\", line 186, in test_unknown_mode_blocks\n    self.assertEqual(out.get(\"decision\"), \"deny\")\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: 'allow' != 'deny'\n- allow\n+ deny\n\n\n----------------------------------------------------------------------\nRan 33 tests in 1.251s\n\nFAILED (failures=3)\n"
  },
  {
   "evidence_quote": "AssertionError: 'exceeds' not found in 'Brother Antigravity Stop blocked: corrupt termination payload (corrupt JSON payload on stdin: Unterminated string starting at: line 1 column 63 (char 62))'",
   "id": "L5C3-HOOK-03",
   "output_tail": "^^^^^^^^^^^^^^^^^^^^^^^^^^^\n        Path(HOOK_SCRIPT).read_bytes(),\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n        \"Refresh the shipped adapter from scripts/brother_antigravity_hook.py\",\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n    )\n    ^\nAssertionError: b'#!/[3236 chars]CHARS:\\n            err = \"stdin payload excee[12332 chars]()\\n' != b'#!/[3236 chars]CHARS * 2:\\n            err = \"stdin payload e[12336 chars]()\\n' : Refresh the shipped adapter from scripts/brother_antigravity_hook.py\n\n======================================================================\nFAIL: test_an_oversized_payload_is_refused_without_reading_it_all (__main__.TestBrotherAntigravityHook.test_an_oversized_payload_is_refused_without_reading_it_all)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_brother_antigravity_hook.py\", line 256, in test_an_oversized_payload_is_refused_without_reading_it_all\n    self.assertIn(\"exceeds\", out.get(\"reason\", \"\"))\n    ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: 'exceeds' not found in 'Brother Antigravity Stop blocked: corrupt termination payload (corrupt JSON payload on stdin: Unterminated string starting at: line 1 column 63 (char 62))'\n\n----------------------------------------------------------------------\nRan 33 tests in 1.220s\n\nFAILED (failures=2)\n"
  },
  {
   "evidence_quote": "AssertionError: True is not false",
   "id": "M0-gate-constant-true",
   "output_tail": "in test_score_10_is_reported_but_the_gate_is_the_integer_comparison\n    self.assertFalse(result[\"gate\"])\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^\nAssertionError: True is not false\n\n======================================================================\nFAIL: test_score_counts_attributed_kills_only (__main__.AttributionTests.test_score_counts_attributed_kills_only)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 1186, in test_score_counts_attributed_kills_only\n    self.assertFalse(result[\"gate\"])\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^\nAssertionError: True is not false\n\n======================================================================\nFAIL: test_score_gate_requires_every_one_of_the_five_conditions (__main__.AttributionTests.test_score_gate_requires_every_one_of_the_five_conditions)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 1149, in test_score_gate_requires_every_one_of_the_five_conditions\n    self.assertFalse(nine_rows[\"gate\"])\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^\nAssertionError: True is not false\n\n----------------------------------------------------------------------\nRan 70 tests in 2.083s\n\nFAILED (failures=4)\n"
  },
  {
   "evidence_quote": "AssertionError: True is not false",
   "id": "M1-measured-dropped",
   "output_tail": "NO-DATA: argv must be a list of strings\nNO-DATA: argv must contain only strings\nTIMEOUT   L5C1-hang\nmutation_probe: scripts/target.py: 0 of 0 valid mutants killed, 0 invalid\n.......F..............................................................\n======================================================================\nFAIL: test_score_gate_requires_every_one_of_the_five_conditions (__main__.AttributionTests.test_score_gate_requires_every_one_of_the_five_conditions)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 1155, in test_score_gate_requires_every_one_of_the_five_conditions\n    self.assertFalse(blocked[\"conditions\"][\"measured\"])\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: True is not false\n\n----------------------------------------------------------------------\nRan 70 tests in 2.110s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: True is not false",
   "id": "M2-categories-dropped",
   "output_tail": "NO-DATA: argv must be a list of strings\nNO-DATA: argv must contain only strings\nTIMEOUT   L5C1-hang\nmutation_probe: scripts/target.py: 0 of 0 valid mutants killed, 0 invalid\n.......F..............................................................\n======================================================================\nFAIL: test_score_gate_requires_every_one_of_the_five_conditions (__main__.AttributionTests.test_score_gate_requires_every_one_of_the_five_conditions)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 1160, in test_score_gate_requires_every_one_of_the_five_conditions\n    self.assertFalse(no_hook[\"conditions\"][\"categories\"])\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: True is not false\n\n----------------------------------------------------------------------\nRan 70 tests in 2.092s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: {'sam[37 chars] True, 'categories': True, 'rate': False, 'state_backed': True} != {'sam[37 chars] True, 'categories': True, 'rate': True, 'state_backed': True}",
   "id": "M3-rate-swapped",
   "output_tail": "ratch/close-L5c/tmp/mutation-probe-5j3kibgd/scripts/test_l5c_audit.py\", line 1185, in test_score_counts_attributed_kills_only\n    self.assertFalse(result[\"conditions\"][\"rate\"])\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: True is not false\n\n======================================================================\nFAIL: test_score_gate_requires_every_one_of_the_five_conditions (__main__.AttributionTests.test_score_gate_requires_every_one_of_the_five_conditions)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 1137, in test_score_gate_requires_every_one_of_the_five_conditions\n    self.assertEqual(good[\"conditions\"], {\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^\n        \"sampled_at_least_ten\": True,\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n    ...<3 lines>...\n        \"state_backed\": True,\n        ^^^^^^^^^^^^^^^^^^^^^\n    })\n    ^^\nAssertionError: {'sam[37 chars] True, 'categories': True, 'rate': False, 'state_backed': True} != {'sam[37 chars] True, 'categories': True, 'rate': True, 'state_backed': True}\n  {'categories': True,\n   'measured': True,\n-  'rate': False,\n?          ^^^^\n\n+  'rate': True,\n?          ^^^\n\n   'sampled_at_least_ten': True,\n   'state_backed': True}\n\n----------------------------------------------------------------------\nRan 70 tests in 2.086s\n\nFAILED (failures=4)\n"
  },
  {
   "evidence_quote": "AssertionError: True is not false",
   "id": "M4-state-backed-dropped",
   "output_tail": "NO-DATA: argv must be a list of strings\nNO-DATA: argv must contain only strings\nTIMEOUT   L5C1-hang\nmutation_probe: scripts/target.py: 0 of 0 valid mutants killed, 0 invalid\n.......F..............................................................\n======================================================================\nFAIL: test_score_gate_requires_every_one_of_the_five_conditions (__main__.AttributionTests.test_score_gate_requires_every_one_of_the_five_conditions)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 1169, in test_score_gate_requires_every_one_of_the_five_conditions\n    self.assertFalse(too_few_state[\"conditions\"][\"state_backed\"])\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: True is not false\n\n----------------------------------------------------------------------\nRan 70 tests in 2.100s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: True is not false",
   "id": "M5-floor-dropped",
   "output_tail": "NO-DATA: argv must be a list of strings\nNO-DATA: argv must contain only strings\nTIMEOUT   L5C1-hang\nmutation_probe: scripts/target.py: 0 of 0 valid mutants killed, 0 invalid\n.......F..............................................................\n======================================================================\nFAIL: test_score_gate_requires_every_one_of_the_five_conditions (__main__.AttributionTests.test_score_gate_requires_every_one_of_the_five_conditions)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 1148, in test_score_gate_requires_every_one_of_the_five_conditions\n    self.assertFalse(nine_rows[\"conditions\"][\"sampled_at_least_ten\"])\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: True is not false\n\n----------------------------------------------------------------------\nRan 70 tests in 2.114s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: 'KILLED' != 'UNATTRIBUTED'",
   "id": "M6-attribution-true",
   "output_tail": "NO-DATA: argv must be a list of strings\nNO-DATA: argv must contain only strings\nTIMEOUT   L5C1-hang\nmutation_probe: scripts/target.py: 0 of 0 valid mutants killed, 0 invalid\nF.....................................................................\n======================================================================\nFAIL: test_attribute_credits_only_the_registered_red (__main__.AttributionTests.test_attribute_credits_only_the_registered_red)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 1238, in test_attribute_credits_only_the_registered_red\n    self.assertEqual(wrong_reason[\"status\"], \"UNATTRIBUTED\")\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: 'KILLED' != 'UNATTRIBUTED'\n- KILLED\n+ UNATTRIBUTED\n\n\n----------------------------------------------------------------------\nRan 70 tests in 2.109s\n\nFAILED (failures=1)\n"
  },
  {
   "evidence_quote": "AssertionError: False is not true : []",
   "id": "M7-verify-true",
   "output_tail": " in problems), problems)\n    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: False is not true : []\n\n======================================================================\nFAIL: test_verify_reports_region_mismatch (__main__.EvidenceTests.test_verify_reports_region_mismatch)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 459, in test_verify_reports_region_mismatch\n    self.assertTrue(any(\"REGION\" in p for p in problems), problems)\n    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: False is not true : []\n\n======================================================================\nFAIL: test_verify_reports_unattributed_meta (__main__.EvidenceTests.test_verify_reports_unattributed_meta)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 493, in test_verify_reports_unattributed_meta\n    self.assertTrue(any(\"META\" in p for p in problems), problems)\n    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: False is not true : []\n\n----------------------------------------------------------------------\nRan 70 tests in 2.133s\n\nFAILED (failures=17)\n"
  },
  {
   "evidence_quote": "AssertionError: 10 != 9",
   "id": "M8-measured-true",
   "output_tail": "ot false : {}\n\n======================================================================\nFAIL: test_mark_measured_promotes_only_a_real_measurement (__main__.AttributionTests.test_mark_measured_promotes_only_a_real_measurement)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 1217, in test_mark_measured_promotes_only_a_real_measurement\n    self.assertFalse(\n    ~~~~~~~~~~~~~~~~^\n        l5c_audit.mark_measured({\"status\": status})[\"measured\"], status)\n        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: True is not false : NO-DATA\n\n======================================================================\nFAIL: test_score_gate_requires_every_one_of_the_five_conditions (__main__.AttributionTests.test_score_gate_requires_every_one_of_the_five_conditions)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"<probe-scratch>/scripts/test_l5c_audit.py\", line 1154, in test_score_gate_requires_every_one_of_the_five_conditions\n    self.assertEqual(blocked[\"measured\"], 9)\n    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^\nAssertionError: 10 != 9\n\n----------------------------------------------------------------------\nRan 70 tests in 2.085s\n\nFAILED (failures=5)\n"
  }
 ],
 "gate": {
  "conditions": {
   "categories": true,
   "measured": true,
   "rate": true,
   "sampled_at_least_ten": true,
   "state_backed": true
  },
  "gate": true
 },
 "generated_at": "2026-09-30T00:24:35+09:00",
 "generated_by": "scripts/l5c_audit.py via its own probe envelope (run_probe, attribute, score, render_doc)",
 "head": "archive of 1805be27eba0e7e93238c5cca099091898876b74 with the L5c close manifest applied",
 "lock": {
  "plugin/runtime/brother/core/openrouter_dispatch.py": "d350842b49899cc700cc3c6f62334fc130fa95dcab32fd3963c27530169a64d1",
  "plugin/runtime/brother/core/test_openrouter_dispatch.py": "32107f95ceebc8e94deb0b92d7e3a66a9be20b5d947b04028fca3ed1cb9dc825",
  "plugin/runtime/brother/vault/assertions_facade.py": "c0dde448bd0cc39812bddf3305e35ab3e67be16886b848b9dce1f3dfa48351fc",
  "plugin/runtime/brother/vault/attribute_provenance_facade.py": "e3c69c5127da7bc0b559cb0f3b2ddcc582a91dc18a3d88d708915a57e032238a",
  "plugin/runtime/brother/vault/attributes_facade.py": "7a4f6e024c338733552a63b87242783263a645e6f10596e13c6c1f72d90d2d4f",
  "plugin/runtime/brother/vault/census_ext_facade.py": "ff140924dc0388708a3223998bce22655878b9c854bf26128c4c9c55770e98fb",
  "plugin/runtime/brother/vault/test_assertions_facade.py": "b2df7a8ea6749aae93ffd6387b93f0bd5e8465efc6d9240b8ba7b101e780ce02",
  "plugin/runtime/brother/vault/test_attribute_provenance_facade.py": "5f550a8c0087ff2fab6eb8ce8d027d541829915ff29253b68e9ae81b56a58528",
  "plugin/runtime/brother/vault/test_attributes_facade.py": "fe56ab5984661ffb29e0cc335e6dc9580ddd0ae71b681842693a9a2573b20712",
  "plugin/runtime/brother/vault/test_census_ext_facade.py": "f7f59ecce4ef735605283e0163a9cc3efd94470754152ff87e0612012bbd1e2f",
  "scripts/brother_antigravity_hook.py": "1be9cae592d076f1e781b76f78f448512483fd0421e6c69ece16bc26d68fdd18",
  "scripts/l5c_audit.py": "ec4cb054bdbbd6b6ff4cc12644ff22367a04a174a4fb7cd9f1bc6e875081adad",
  "scripts/test_brother_antigravity_hook.py": "b34f4bda6f2e6368de33f7bfa0474a211d95d9946feda8301eeb37542af5a90a",
  "scripts/test_l5c_audit.py": "3f2e795cb543a7d9e5eb66efdfd5a7ab296cea0a61fd59490786c1b39b044e98"
 },
 "meta": [
  {
   "attributed": true,
   "category": "meta",
   "detail": "KILLED and attributed: test_score_gate_requires_every_one_of_the_five_conditions and the pre-registered red token both appear in the captured tail",
   "id": "M0-gate-constant-true",
   "measured": true,
   "method": "test_score_gate_requires_every_one_of_the_five_conditions",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/l5c_audit.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "scripts/test_l5c_audit.py"
  },
  {
   "attributed": true,
   "category": "meta",
   "detail": "KILLED and attributed: test_score_gate_requires_every_one_of_the_five_conditions and the pre-registered red token both appear in the captured tail",
   "id": "M1-measured-dropped",
   "measured": true,
   "method": "test_score_gate_requires_every_one_of_the_five_conditions",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/l5c_audit.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "scripts/test_l5c_audit.py"
  },
  {
   "attributed": true,
   "category": "meta",
   "detail": "KILLED and attributed: test_score_gate_requires_every_one_of_the_five_conditions and the pre-registered red token both appear in the captured tail",
   "id": "M2-categories-dropped",
   "measured": true,
   "method": "test_score_gate_requires_every_one_of_the_five_conditions",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/l5c_audit.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "scripts/test_l5c_audit.py"
  },
  {
   "attributed": true,
   "category": "meta",
   "detail": "KILLED and attributed: test_score_gate_requires_every_one_of_the_five_conditions and the pre-registered red token both appear in the captured tail",
   "id": "M3-rate-swapped",
   "measured": true,
   "method": "test_score_gate_requires_every_one_of_the_five_conditions",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/l5c_audit.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "scripts/test_l5c_audit.py"
  },
  {
   "attributed": true,
   "category": "meta",
   "detail": "KILLED and attributed: test_score_gate_requires_every_one_of_the_five_conditions and the pre-registered red token both appear in the captured tail",
   "id": "M4-state-backed-dropped",
   "measured": true,
   "method": "test_score_gate_requires_every_one_of_the_five_conditions",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/l5c_audit.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "scripts/test_l5c_audit.py"
  },
  {
   "attributed": true,
   "category": "meta",
   "detail": "KILLED and attributed: test_score_gate_requires_every_one_of_the_five_conditions and the pre-registered red token both appear in the captured tail",
   "id": "M5-floor-dropped",
   "measured": true,
   "method": "test_score_gate_requires_every_one_of_the_five_conditions",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/l5c_audit.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "scripts/test_l5c_audit.py"
  },
  {
   "attributed": true,
   "category": "meta",
   "detail": "KILLED and attributed: test_attribute_credits_only_the_registered_red and the pre-registered red token both appear in the captured tail",
   "id": "M6-attribution-true",
   "measured": true,
   "method": "test_attribute_credits_only_the_registered_red",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/l5c_audit.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "scripts/test_l5c_audit.py"
  },
  {
   "attributed": true,
   "category": "meta",
   "detail": "KILLED and attributed: test_verify_reports_unattributed_meta and the pre-registered red token both appear in the captured tail",
   "id": "M7-verify-true",
   "measured": true,
   "method": "test_verify_reports_unattributed_meta",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/l5c_audit.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "scripts/test_l5c_audit.py"
  },
  {
   "attributed": true,
   "category": "meta",
   "detail": "KILLED and attributed: test_score_gate_requires_every_one_of_the_five_conditions and the pre-registered red token both appear in the captured tail",
   "id": "M8-measured-true",
   "measured": true,
   "method": "test_score_gate_requires_every_one_of_the_five_conditions",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/l5c_audit.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "scripts/test_l5c_audit.py"
  }
 ],
 "partial": false,
 "patches": [
  {
   "id": "L5C3-VAULT-01",
   "new": "main = lambda *args, **kwargs: _module.main(*args, **kwargs)\n",
   "old": "main = getattr(_module, \"main\")\n",
   "src_path": "plugin/runtime/brother/vault/assertions_facade.py"
  },
  {
   "id": "L5C3-VAULT-02",
   "new": "main = lambda *args, **kwargs: _module.main(*args, **kwargs)\n",
   "old": "main = getattr(_module, \"main\")\n",
   "src_path": "plugin/runtime/brother/vault/attribute_provenance_facade.py"
  },
  {
   "id": "L5C3-VAULT-03",
   "new": "main = lambda *args, **kwargs: _module.main(*args, **kwargs)\n",
   "old": "main = getattr(_module, \"main\")\n",
   "src_path": "plugin/runtime/brother/vault/attributes_facade.py"
  },
  {
   "id": "L5C3-VAULT-04",
   "new": "main = lambda *args, **kwargs: _module.main(*args, **kwargs)\n",
   "old": "main = getattr(_module, \"main\")\n",
   "src_path": "plugin/runtime/brother/vault/census_ext_facade.py"
  },
  {
   "id": "L5C3-DISPATCH-01",
   "new": "        caps.append(policy if daily_cap is None else max(policy, daily_cap))\n",
   "old": "        caps.append(policy if daily_cap is None else min(policy, daily_cap))\n",
   "src_path": "plugin/runtime/brother/core/openrouter_dispatch.py"
  },
  {
   "id": "L5C3-DISPATCH-02",
   "new": "    effective_max_slots = max_slots\n",
   "old": "    effective_max_slots = min(limits.max_slots, max_slots)\n",
   "src_path": "plugin/runtime/brother/core/openrouter_dispatch.py"
  },
  {
   "id": "L5C3-DISPATCH-03",
   "new": "        if cap <= 0:\n            return daily_cap, None\n",
   "old": "        if cap <= daily_cap:\n            return daily_cap, None\n",
   "src_path": "plugin/runtime/brother/core/openrouter_dispatch.py"
  },
  {
   "id": "L5C3-HOOK-01",
   "new": "                \"decision\": \"deny\",\n                \"reason\": f\"Brother Antigravity Stop blocked on unhandled exception",
   "old": "                \"decision\": \"continue\",\n                \"reason\": f\"Brother Antigravity Stop blocked on unhandled exception",
   "src_path": "scripts/brother_antigravity_hook.py"
  },
  {
   "id": "L5C3-HOOK-02",
   "new": "                \"decision\": \"allow\",\n                \"reason\": f\"Brother Antigravity blocked: unknown hook mode",
   "old": "                \"decision\": \"deny\",\n                \"reason\": f\"Brother Antigravity blocked: unknown hook mode",
   "src_path": "scripts/brother_antigravity_hook.py"
  },
  {
   "id": "L5C3-HOOK-03",
   "new": "        if len(raw) > MAX_STDIN_CHARS * 2:\n",
   "old": "        if len(raw) > MAX_STDIN_CHARS:\n",
   "src_path": "scripts/brother_antigravity_hook.py"
  },
  {
   "id": "M0-gate-constant-true",
   "new": "    gate = True\n",
   "old": "    gate = all(conditions.values())\n",
   "src_path": "scripts/l5c_audit.py"
  },
  {
   "id": "M1-measured-dropped",
   "new": "        \"measured\": True,\n",
   "old": "        \"measured\": measured == total,\n",
   "src_path": "scripts/l5c_audit.py"
  },
  {
   "id": "M2-categories-dropped",
   "new": "        \"categories\": True,\n",
   "old": "        \"categories\": len(missing) == 0,\n",
   "src_path": "scripts/l5c_audit.py"
  },
  {
   "id": "M3-rate-swapped",
   "new": "        \"rate\": killed * BAR_A <= BAR_B * total,\n",
   "old": "        \"rate\": killed * BAR_A >= BAR_B * total,\n",
   "src_path": "scripts/l5c_audit.py"
  },
  {
   "id": "M4-state-backed-dropped",
   "new": "        \"state_backed\": True,\n",
   "old": "        \"state_backed\": state_backed_killed >= 2,\n",
   "src_path": "scripts/l5c_audit.py"
  },
  {
   "id": "M5-floor-dropped",
   "new": "        \"sampled_at_least_ten\": True,\n",
   "old": "        \"sampled_at_least_ten\": total >= 10,\n",
   "src_path": "scripts/l5c_audit.py"
  },
  {
   "id": "M6-attribution-true",
   "new": "        if False:\n            absent.append(name)\n",
   "old": "        if value not in tail:\n            absent.append(name)\n",
   "src_path": "scripts/l5c_audit.py"
  },
  {
   "id": "M7-verify-true",
   "new": "    return []\n",
   "old": "    return list(problems)\n",
   "src_path": "scripts/l5c_audit.py"
  },
  {
   "id": "M8-measured-true",
   "new": "    marked[\"measured\"] = True\n",
   "old": "    marked[\"measured\"] = status in MEASURED_STATUSES\n",
   "src_path": "scripts/l5c_audit.py"
  }
 ],
 "per_sample": [
  {
   "attributed": true,
   "category": "vault",
   "detail": "KILLED and attributed: test_facade_main_is_the_source_modules_main and the pre-registered red token both appear in the captured tail",
   "id": "L5C3-VAULT-01",
   "measured": true,
   "method": "test_facade_main_is_the_source_modules_main",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "plugin/runtime/brother/vault/assertions_facade.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "plugin/runtime/brother/vault/test_assertions_facade.py"
  },
  {
   "attributed": true,
   "category": "vault",
   "detail": "KILLED and attributed: test_facade_main_is_the_source_modules_main and the pre-registered red token both appear in the captured tail",
   "id": "L5C3-VAULT-02",
   "measured": true,
   "method": "test_facade_main_is_the_source_modules_main",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "plugin/runtime/brother/vault/attribute_provenance_facade.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "plugin/runtime/brother/vault/test_attribute_provenance_facade.py"
  },
  {
   "attributed": true,
   "category": "vault",
   "detail": "KILLED and attributed: test_facade_main_is_the_source_modules_main and the pre-registered red token both appear in the captured tail",
   "id": "L5C3-VAULT-03",
   "measured": true,
   "method": "test_facade_main_is_the_source_modules_main",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "plugin/runtime/brother/vault/attributes_facade.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "plugin/runtime/brother/vault/test_attributes_facade.py"
  },
  {
   "attributed": true,
   "category": "vault",
   "detail": "KILLED and attributed: test_facade_main_is_the_source_modules_main and the pre-registered red token both appear in the captured tail",
   "id": "L5C3-VAULT-04",
   "measured": true,
   "method": "test_facade_main_is_the_source_modules_main",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "plugin/runtime/brother/vault/census_ext_facade.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "plugin/runtime/brother/vault/test_census_ext_facade.py"
  },
  {
   "attributed": true,
   "category": "dispatch",
   "detail": "KILLED and attributed: test_a_caller_daily_cap_above_the_configured_cap_is_clamped_down and the pre-registered red token both appear in the captured tail",
   "id": "L5C3-DISPATCH-01",
   "measured": true,
   "method": "test_a_caller_daily_cap_above_the_configured_cap_is_clamped_down",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "plugin/runtime/brother/core/openrouter_dispatch.py",
   "state_backed": true,
   "status": "KILLED",
   "test_path": "plugin/runtime/brother/core/test_openrouter_dispatch.py"
  },
  {
   "attributed": true,
   "category": "dispatch",
   "detail": "KILLED and attributed: test_a_caller_max_slots_above_the_configured_max_is_clamped_down and the pre-registered red token both appear in the captured tail",
   "id": "L5C3-DISPATCH-02",
   "measured": true,
   "method": "test_a_caller_max_slots_above_the_configured_max_is_clamped_down",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "plugin/runtime/brother/core/openrouter_dispatch.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "plugin/runtime/brother/core/test_openrouter_dispatch.py"
  },
  {
   "attributed": true,
   "category": "dispatch",
   "detail": "KILLED and attributed: test_a_founder_cap_grant_lifts_the_cap_only_while_valid and the pre-registered red token both appear in the captured tail",
   "id": "L5C3-DISPATCH-03",
   "measured": true,
   "method": "test_a_founder_cap_grant_lifts_the_cap_only_while_valid",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "plugin/runtime/brother/core/openrouter_dispatch.py",
   "state_backed": true,
   "status": "KILLED",
   "test_path": "plugin/runtime/brother/core/test_openrouter_dispatch.py"
  },
  {
   "attributed": true,
   "category": "hook",
   "detail": "KILLED and attributed: test_a_crash_answers_in_the_events_own_schema and the pre-registered red token both appear in the captured tail",
   "id": "L5C3-HOOK-01",
   "measured": true,
   "method": "test_a_crash_answers_in_the_events_own_schema",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/brother_antigravity_hook.py",
   "state_backed": false,
   "status": "KILLED",
   "test_path": "scripts/test_brother_antigravity_hook.py"
  },
  {
   "attributed": true,
   "category": "hook",
   "detail": "KILLED and attributed: test_a_hook_launched_without_its_event_name_blocks and the pre-registered red token both appear in the captured tail",
   "id": "L5C3-HOOK-02",
   "measured": true,
   "method": "test_a_hook_launched_without_its_event_name_blocks",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/brother_antigravity_hook.py",
   "state_backed": true,
   "status": "KILLED",
   "test_path": "scripts/test_brother_antigravity_hook.py"
  },
  {
   "attributed": true,
   "category": "hook",
   "detail": "KILLED and attributed: test_an_oversized_payload_is_refused_without_reading_it_all and the pre-registered red token both appear in the captured tail",
   "id": "L5C3-HOOK-03",
   "measured": true,
   "method": "test_an_oversized_payload_is_refused_without_reading_it_all",
   "probe_exit": 0,
   "probe_verdict": "KILLED",
   "src_path": "scripts/brother_antigravity_hook.py",
   "state_backed": true,
   "status": "KILLED",
   "test_path": "scripts/test_brother_antigravity_hook.py"
  }
 ],
 "probe_calls": [
  {
   "argv": [
    "python3",
    "scripts/mutation_probe.py",
    "--src",
    "plugin/runtime/brother/vault/assertions_facade.py",
    "--test",
    "plugin/runtime/brother/vault/test_assertions_facade.py",
    "--mutants",
    "<run-log>/manifests/mutants-2abe4baef4f609a8.json",
    "--out",
    "<run-log>/probe-out-vault-0.json",
    "--timeout",
    "120",
    "--copy",
    "plugin/runtime/brother/vault/assertions_facade.py",
    "--copy",
    "plugin/runtime/brother/vault/test_assertions_facade.py"
   ],
   "exit": 0,
   "log": "probe-01.log"
  },
  {
   "argv": [
    "python3",
    "scripts/mutation_probe.py",
    "--src",
    "plugin/runtime/brother/vault/attribute_provenance_facade.py",
    "--test",
    "plugin/runtime/brother/vault/test_attribute_provenance_facade.py",
    "--mutants",
    "<run-log>/manifests/mutants-c69025a3c6fd2229.json",
    "--out",
    "<run-log>/probe-out-vault-1.json",
    "--timeout",
    "120",
    "--copy",
    "plugin/runtime/brother/vault/attribute_provenance_facade.py",
    "--copy",
    "plugin/runtime/brother/vault/test_attribute_provenance_facade.py"
   ],
   "exit": 0,
   "log": "probe-02.log"
  },
  {
   "argv": [
    "python3",
    "scripts/mutation_probe.py",
    "--src",
    "plugin/runtime/brother/vault/attributes_facade.py",
    "--test",
    "plugin/runtime/brother/vault/test_attributes_facade.py",
    "--mutants",
    "<run-log>/manifests/mutants-c010960b6b0f625b.json",
    "--out",
    "<run-log>/probe-out-vault-2.json",
    "--timeout",
    "120",
    "--copy",
    "plugin/runtime/brother/vault/attributes_facade.py",
    "--copy",
    "plugin/runtime/brother/vault/test_attributes_facade.py"
   ],
   "exit": 0,
   "log": "probe-03.log"
  },
  {
   "argv": [
    "python3",
    "scripts/mutation_probe.py",
    "--src",
    "plugin/runtime/brother/vault/census_ext_facade.py",
    "--test",
    "plugin/runtime/brother/vault/test_census_ext_facade.py",
    "--mutants",
    "<run-log>/manifests/mutants-b881669180e63cba.json",
    "--out",
    "<run-log>/probe-out-vault-3.json",
    "--timeout",
    "120",
    "--copy",
    "plugin/runtime/brother/vault/census_ext_facade.py",
    "--copy",
    "plugin/runtime/brother/vault/test_census_ext_facade.py"
   ],
   "exit": 0,
   "log": "probe-04.log"
  },
  {
   "argv": [
    "python3",
    "scripts/mutation_probe.py",
    "--src",
    "plugin/runtime/brother/core/openrouter_dispatch.py",
    "--test",
    "plugin/runtime/brother/core/test_openrouter_dispatch.py",
    "--mutants",
    "<run-log>/manifests/mutants-16f35cc5a43b7d7c.json",
    "--out",
    "<run-log>/probe-out-dispatch-4.json",
    "--timeout",
    "120",
    "--copy",
    "plugin/runtime/brother/core/openrouter_dispatch.py",
    "--copy",
    "plugin/runtime/brother/core/test_openrouter_dispatch.py",
    "--copy",
    "scripts/loop"
   ],
   "exit": 0,
   "log": "probe-05.log"
  },
  {
   "argv": [
    "python3",
    "scripts/mutation_probe.py",
    "--src",
    "scripts/brother_antigravity_hook.py",
    "--test",
    "scripts/test_brother_antigravity_hook.py",
    "--mutants",
    "<run-log>/manifests/mutants-2650b59b476b8eac.json",
    "--out",
    "<run-log>/probe-out-hook-5.json",
    "--timeout",
    "120",
    "--copy",
    "scripts/brother_antigravity_hook.py",
    "--copy",
    "scripts/test_brother_antigravity_hook.py",
    "--copy",
    "plugin",
    "--copy",
    "docs/how-to/install-antigravity.md"
   ],
   "exit": 0,
   "log": "probe-06.log"
  },
  {
   "argv": [
    "python3",
    "scripts/mutation_probe.py",
    "--src",
    "scripts/l5c_audit.py",
    "--test",
    "scripts/test_l5c_audit.py",
    "--mutants",
    "<run-log>/manifests/mutants-efea46a3da078aec.json",
    "--out",
    "<run-log>/probe-out-meta-0.json",
    "--timeout",
    "120",
    "--copy",
    "scripts/l5c_audit.py",
    "--copy",
    "scripts/test_l5c_audit.py"
   ],
   "exit": 0,
   "log": "probe-07.log"
  }
 ],
 "probe_timeout_s": 120,
 "redacted": "absolute paths replaced: <probe-scratch> is the probe's own scratch copy, <run-log> the local run log folder, <tree> the audited tree, ~ the home folder",
 "run_index": 3,
 "score_10": 10.0,
 "status": "RUN",
 "totals": {
  "killed_attributed": 10,
  "measured": 10,
  "missing_categories": [],
  "nodata": 0,
  "state_backed_killed": 4,
  "survived": 0,
  "total": 10
 },
 "vault": {
  "root": "plugin/runtime/brother/vault",
  "source_env": "BROTHER_VAULT_BM_VAULT_<NAME>_PATH=products/brothermode/tools/bm_vault_<name>.py (the probe scratch copy has no .git for the facade's own lookup)",
  "token": "_facade"
 }
}
```
<!-- L5C-GENERATED-END -->

## How to reproduce

1. Build the inventory and the pre-registered sample (L5c.1 through L5c.4
   own the selection and the probe envelope).
2. Run the mutation probe over the pre-registered sample.
3. Write the run ledger to `docs/plan/l5c/run-ledger.json`.
4. Render this document from that ledger with
   `scripts/l5c_audit.render_doc`.
5. Verify with `scripts/l5c_audit.verify_evidence`. An empty list is the
   only approval; everything else is a named problem.
