# Calibrated decisions: when to use Jev, when not, at what trust and cost

A typed answer can still be wrong. In Brother, use Jev only for the work assigned to it, at a confidence threshold supported by the project's measured outcomes.

## What Jev is (and is not)

Jev is TypeSafe's decision model. Give it a state and questions with defined answer types; it returns decisions and probabilities. The vendor documentation describes text input, including strings, JSON objects and arrays. Jev does not write code, replies or explanations. It is not an agent: your software keeps control of actions and side effects.

Brother's decision record specifies OpenRouter's decisions endpoint. The chat endpoint refuses Jev, and a chat bridge can silently substitute another model. Brother's decision path must never accept that substitution as a Jev answer. The vendor digest documents the native API but does not establish the OpenRouter request contract, so do not treat the examples below as complete OpenRouter requests.

## The three question types

These small question objects are excerpted from the request examples in DOCS-DIGEST.md. Put a named question under `questions` and supply the text to judge in `state`.

Use `noul` for a yes/no question. Its answer is the probability of yes, with no separate `confidence` field.

```json
{
  "type": "noul",
  "instructions": "Does this convey urgency?",
  "criteria": {
    "true": "Explicitly time-sensitive",
    "false": "No urgency expressed"
  }
}
```

Use `choice` for options with no ordering. It returns the highest-probability option, the probability distribution and a confidence value.

```json
{
  "type": "choice",
  "instructions": "Which team should handle this?",
  "criteria": {
    "billing": "Payments, invoicing, refunds",
    "technical": "Bugs, outages, integrations",
    "sales": "Pricing, upgrades, new accounts"
  }
}
```

Use `score` for an ordered scale. Its answer is a probability-weighted position that can fall between levels, accompanied by a legend, probabilities and confidence. It is not a way to obtain exact arithmetic.

```json
{
  "type": "score",
  "instructions": "How frustrated is the customer?",
  "criteria": ["Calm", "Frustrated", "Very angry"]
}
```

These examples explain the interface. They do not add approved Brother use cases.

## When to use it

Follow the assignments in DECISION.md. The measured reasons below come from EVAL-README.md and the score files.

**Screen rules for unknown inputs being treated as safe (task B).** Ask whether missing, unknown, undeclared or unreadable input falls through as passing or permitted. Jev's accuracy was 90% (n=20), with balanced accuracy of 88% (n=20), for both single and batched questions. It is a candidate for autonomous use only: the confidence threshold is derived from the calibration ledger for that question family and risk class (a band whose 95 percent lower bound meets the target over the minimum sample), not fixed in advance.

**Triage release gate lines in a batch (task D).** Classify lines as blocking, warning or information. Batched Jev accuracy was 100% (n=14), compared with 79% (n=14) for single questions. This is the other candidate for autonomous use, under the same ledger-derived threshold. Classifying a line does not give Jev organizational release authority.

**Rank mutations, never skip them (task A).** A prediction that tests will not catch a mutation raises its priority. A prediction that tests will catch it never removes it. Jev's accuracy was 88% (n=24), but balanced accuracy was 75% (n=24), and the decision record identifies missed survivors as the reason to keep every mutation in the work.

**Keep Muse as the default adversarial reviewer.** Muse had the best balanced accuracy on mutation triage: 92% (n=24), with accuracy of 96% (n=24). Below an authorized Jev confidence band, escalate to Muse first and to Opus for the gates named in the plan. A favorable Jev result does not replace that review assignment.

**Do not predict routing from a specification (task C).** No judge beat the always-majority baseline of 68% (n=22). Jev scored 55% (n=22), its repeat 64% (n=22), Muse 64% (n=22), and DeepSeek 38% (n=13 answered). Verify the work after it is built.

Batch questions about a shared state into a call. The gate-line result above supports that assignment, and the cost figures below show the measured cost difference. Record the question type and framing: the vendor reports different probabilities for equivalent `noul` and `choice` questions. Never pool their calibration records or transfer a threshold between them.

## When never to use it

Never assign Jev code generation, arithmetic, counting or date calculations, subtle test-coverage judgment alone, or the role of sole security gate. Do not use it to waive a mutation or predict whether a specification will land cleanly.

The vendor documents unreliable counting and date comparisons, difficulty with indirection and irrelevant context, and susceptibility to injected instructions in the supplied state. Keep exact calculations in code. A schema-valid answer does not establish that a security boundary held.

## Trust the measured band

For `choice` and `score`, the vendor describes confidence as a summary of how concentrated the answer distribution is. It is not a guarantee about an individual answer. In this evaluation, Jev's `noul` confidence is `max(p, 1-p)`, where `p` is the probability of yes. Muse and DeepSeek instead wrote confidence into generated JSON; those values are not established as an equivalent scale.

Brother's thresholds come from this project's own calibration ledger, not from the vendor. Today no band qualifies for autonomous use: seeded with this evaluation, the best band's 95 percent lower bound is 0.6756 (8 correct of 8), below every risk class's target, so every decision escalates until recorded outcomes lift it. Record a decision and its confidence, join it to the later verified outcome, and use the resulting evidence for that question family and risk class. Below the ledger's minimum sample, autonomous use is NO-DATA. The supplied files do not state a numeric minimum or a target precision for every risk class.

The tables copy the score files exactly. Coverage is the share of answered items meeting the confidence gate. Gated precision here means the scorer's `acc@gate`, correctness among those items, not a separate class-specific precision measurement. `Task n` is the answered sample; `gated n` is the smaller sample used for gated precision. Where the scores do not print that smaller count, it is marked unreported rather than inferred.

At confidence at least 0.90, from benchmarks/jev_eval/SCORES-gate-0.9.txt:

| Task and mode | Coverage | Gated precision |
|---|---|---|
| A, single | 54% (task n=24) | 92% (gated n=13) |
| B, single | 35% (task n=20) | 100% (gated n=7) |
| B, batched | 20% (task n=20) | 100% (task n=20; gated n unreported) |
| C, single | 5% (task n=22) | 0% (gated n=1) |
| D, single | 64% (task n=14) | 100% (gated n=9) |
| D, batched | 79% (task n=14) | 100% (task n=14; gated n unreported) |

At confidence at least 0.80, from benchmarks/jev_eval/SCORES-gate-0.8.txt:

| Task and mode | Coverage | Gated precision |
|---|---|---|
| A, single | 75% (task n=24) | 89% (task n=24; gated n unreported) |
| B, single | 60% (task n=20) | 100% (task n=20; gated n unreported) |
| B, batched | 60% (task n=20) | 100% (task n=20; gated n unreported) |
| C, single | 9% (task n=22) | 0% (task n=22; gated n unreported) |
| D, single | 71% (task n=14) | 100% (task n=14; gated n unreported) |
| D, batched | 86% (task n=14) | 100% (task n=14; gated n unreported) |

The lower gate shows a coverage tradeoff, not permission to lower Brother's threshold. Task C's confident error also prevents treating low coverage as proof that Jev reliably recognizes its own mistakes. These cells do not establish calibration for new tasks or inputs.

## Cost per success

Use cost per correct decision, not cost per call. EVAL-README.md defines `$/success` as the row's total spend divided by correct answers, and `$/gated-succ` as that total spend divided by correct answers meeting the confidence gate. The latter includes spend on answers outside the gate.

These are measured USD figures from benchmarks/jev_eval/SCORES-gate-0.9.txt. The comparator columns are costs per correct answer overall.

| Task, answered sample | Jev single per success | Jev single per gated success at 0.90 | Muse per success | DeepSeek per success |
|---|---|---|---|---|
| A, n=24 each | $0.000022 | $0.000038 | $0.000253 | $0.000710 |
| B, n=20 each | $0.000018 | $0.000047 | $0.000437 | $0.001397 |
| D, n=14 each | $0.000022 | $0.000027 | $0.000177 | $0.000839 |

Batched Jev (one call for every item of a task, its cost spread evenly across the items) cost $0.000008 per success on B (n=20), and $0.000034 per gated success at 0.90. On D (n=14), it cost $0.000008 per success and $0.000010 per gated success at 0.90. These are task-level measurements, not a price for every future decision.

Batch costs are amortized across the answered items. Accounting also differs: Jev uses the bridge's reported `usage.cost`, which may be absent; Muse and DeepSeek use reported cost when available and otherwise catalog token prices. The supplied figures do not establish the cost of the full escalation and verification workflow. Jev's task C gated cost is NO-DATA because there were no correct gated answers, not because routing was free.

## Privacy before the call

DECISION.md requires every payload to pass the content gate before leaving the machine. Send only generalized content. The evaluation used role-worded prompts without repository source, file paths, product names or company names.

DOCS-DIGEST.md records TypeSafe's statements that customer requests and responses are not used to train Jev, and that customer data is not used for fine-tuning or LoRA adaptation. It describes zero data retention as available to enterprise customers by arrangement.

Non-enterprise retention is unstated. The fetched documentation is also silent on processing and storage regions and on other internal uses of logged traffic, including product improvement apart from training. The digest did not fetch or verify the linked legal agreements. It does not establish that native API data-handling terms apply unchanged through OpenRouter. Keep those gaps visible when deciding what may leave the machine.

## How to read a NO-DATA

NO-DATA means there is no usable result for the stated purpose. If Jev is unreachable, an answer is missing, or another model answers in its place, record NO-DATA and escalate. The evaluation also records bridge startup failures, timeouts and invalid JSON as NO-DATA. Insufficient calibration samples prevent autonomous use even when a model returned an answer.

In the scores, NO-DATA prediction rows are reported separately and excluded from correctness denominators. An empty results file is refused as NO-DATA. A NO-DATA cost cell means there were no successes to divide by; a NO-DATA gated-accuracy cell means there was no answered set at that gate to score. Read the field and cause before interpreting it.

Keep material NO-DATA visible. If the Jev decision path must be rolled back, DECISION.md specifies removing it from the router; nothing else depends on Jev.

## Limits of the evidence

The local evaluation has 80 labelled items, with 14 to 24 per task, from a single frozen run. Labels were established before asking the models, using mutation runs, rule text, landed verification outcomes and release policy. Confidence gates make the samples smaller still. EVAL-README.md says to treat cells with n under 5 as data points, not rates.

Tasks B and D are close to text matching: B restates the rule, and a gate line's prefix often reveals its class. Their results do not establish general reasoning or security reliability. Repeat and paraphrase agreement measure consistency, not correctness. The frozen run also lost some DeepSeek routing attempts before they could write rows, which limits that comparison.

INDEPENDENT-EVIDENCE.md rates the outside evidence as moderate. It records shipped integrations and narrow hands-on tests, alongside failures on reasoning tasks. It found no independent calibration study or long-term production reliability evidence. DOCS-DIGEST.md likewise reports no published vendor calibration curve in the fetched pages. Neither source justifies extending the local scores into a general calibration claim.

## Verify the result

Observe the outcome after the work runs. Join it back to the decision, confidence and framing in the ledger. Use that evidence to review the authorized band; prior results cannot prove behavior on work that has not happened yet.

Evidence: docs/decisions/jev-calibrated-decisions-2026-09-18.md; benchmarks/jev_eval/README.md; benchmarks/jev_eval/SCORES-gate-0.9.txt; benchmarks/jev_eval/SCORES-gate-0.8.txt; benchmarks/jev_eval/research/DOCS-DIGEST.md; benchmarks/jev_eval/research/INDEPENDENT-EVIDENCE.md.
