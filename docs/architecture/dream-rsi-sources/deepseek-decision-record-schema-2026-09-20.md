## 1. Decision record event schema

The existing event envelope is unchanged. A decision record is an event whose `type` is `"decision"`. The decision-specific data lives in `payload`.

### Envelope fields

| Field | Type | Notes |
|---|---|---|
| `at` | string | ISO 8601 timestamp |
| `event_id` | string | Unique event id |
| `parent_ids` | array of string | Parent event ids |
| `payload` | object | Decision payload below |
| `run_id` | string | Run id |
| `session_id` | string | Session id |
| `type` | string | Must be `"decision"` |
| `unit_id` | string or null | Work unit id if applicable |

### Decision payload fields

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | e.g. `"decision.v1"` |
| `decision_kind` | string | `"model_budget"`, `"claim_unit"`, `"gate_order"`, etc. |
| `policy_id` | string | Policy identity |
| `policy_version` | string | Policy version |
| `policy_hash` | string | Content hash of policy code |
| `policy_kind` | string | `"fixed"`, `"learned"`, `"human"`, `"deterministic"` |
| `observed` | object | Prefix-only snapshot available at decision time |
| `options` | array of Option | All available options |
| `chosen_option_id` | string | Option taken |
| `chosen_action` | object | Copy of chosen option action |
| `cost` | Cost | Actual cost incurred by the decision |
| `outcome` | Outcome or null | Inline outcome if already known |
| `outcome_event_id` | string or null | Later `decision_outcome` event id |
| `safety_floor` | SafetyFloor | Safety constraints |
| `replay_key` | string or null | Stable key for replay matching |

### Option

| Field | Type | Notes |
|---|---|---|
| `option_id` | string | Unique within decision |
| `action` | object | Typed action payload |
| `cost_estimate` | Cost or null | Estimated cost |
| `allowed` | boolean | Allowed by safety floor |
| `locked` | boolean | Locked by safety floor |

### Cost

| Field | Type | Notes |
|---|---|---|
| `wall_ms` | number | Wall clock milliseconds |
| `tokens` | number | Token count |
| `usd` | number | USD cost |
| `probes` | number | Probe count |

### Outcome

| Field | Type | Notes |
|---|---|---|
| `status` | string | `"pass"`, `"fail"`, `"no_data"` |
| `value` | number or null | Numeric score if pass |
| `cost` | Cost | Actual cost of executed action |
| `details` | object | Diagnostics |

### SafetyFloor

| Field | Type | Notes |
|---|---|---|
| `floor_hash` | string | Hash of frozen safety floor |
| `locked` | boolean | Floor is locked |
| `human_reserved` | boolean | Human owner decision |
| `allowed_by_floor` | boolean | Chosen option passes floor |
| `locked_checks` | array of string | Checks that must not be loosened |

### Later outcome event

A later event attaches the outcome:

| Field | Type | Notes |
|---|---|---|
| `type` | string | `"decision_outcome"` |
| `payload.decision_event_id` | string | Decision event id |
| `payload.status` | string | `"pass"`, `"fail"`, `"no_data"` |
| `payload.value` | number or null | Numeric score if pass |
| `payload.cost` | Cost | Actual action cost |
| `payload.details` | object | Diagnostics |
| `payload.graded_at` | string | ISO 8601 timestamp |

### Example 1: choosing model and budget for a job

```json
{
  "at": "2026-01-15T10:00:00Z",
  "event_id": "evt_dec_model_001",
  "parent_ids": ["evt_job_123"],
  "payload": {
    "schema_version": "decision.v1",
    "decision_kind": "model_budget",
    "policy_id": "pol_model_chooser",
    "policy_version": "v3",
    "policy_hash": "sha256:abc123",
    "policy_kind": "learned",
    "observed": {
      "job_id": "job_123",
      "unit_id": "unit_456",
      "task_tags": ["refactor", "python"],
      "remaining_usd": 4.20,
      "remaining_tokens": 120000,
      "prior_failures": 0
    },
    "options": [
      {
        "option_id": "opt_gpt4o_8k",
        "action": {"model": "gpt-4o", "max_tokens": 8000},
        "cost_estimate": {"wall_ms": 0, "tokens": 8000, "usd": 0.12, "probes": 0},
        "allowed": true,
        "locked": false
      },
      {
        "option_id": "opt_gpt4o_mini_4k",
        "action": {"model": "gpt-4o-mini", "max_tokens": 4000},
        "cost_estimate": {"wall_ms": 0, "tokens": 4000, "usd": 0.02, "probes": 0},
        "allowed": true,
        "locked": false
      }
    ],
    "chosen_option_id": "opt_gpt4o_8k",
    "chosen_action": {"model": "gpt-4o", "max_tokens": 8000},
    "cost": {"wall_ms": 0, "tokens": 1200, "usd": 0.03, "probes": 0},
    "outcome": null,
    "outcome_event_id": null,
    "safety_floor": {
      "floor_hash": "sha256:floor789",
      "locked": true,
      "human_reserved": false,
      "allowed_by_floor": true,
      "locked_checks": []
    },
    "replay_key": "model_budget:job_123"
  },
  "run_id": "run_001",
  "session_id": "sess_001",
  "type": "decision",
  "unit_id": "unit_456"
}
```

### Example 2: choosing which work unit to claim next

```json
{
  "at": "2026-01-15T10:01:00Z",
  "event_id": "evt_dec_claim_001",
  "parent_ids": ["evt_queue_001"],
  "payload": {
    "schema_version": "decision.v1",
    "decision_kind": "claim_unit",
    "policy_id": "pol_claim",
    "policy_version": "v7",
    "policy_hash": "sha256:def456",
    "policy_kind": "learned",
    "observed": {
      "ready_units": ["unit_101", "unit_102", "unit_103"],
      "depends_on": {"unit_102": ["unit_101"], "unit_103": []},
      "priorities": {"unit_101": 5, "unit_102": 3, "unit_103": 4},
      "spend_remaining_usd": 2.50
    },
    "options": [
      {
        "option_id": "opt_unit_101",
        "action": {"claim_unit_id": "unit_101"},
        "cost_estimate": {"wall_ms": 0, "tokens": 0, "usd": 0.0, "probes": 0},
        "allowed": true,
        "locked": false
      },
      {
        "option_id": "opt_unit_103",
        "action": {"claim_unit_id": "unit_103"},
        "cost_estimate": {"wall_ms": 0, "tokens": 0, "usd": 0.0, "probes": 0},
        "allowed": true,
        "locked": false
      }
    ],
    "chosen_option_id": "opt_unit_101",
    "chosen_action": {"claim_unit_id": "unit_101"},
    "cost": {"wall_ms": 12, "tokens": 0, "usd": 0.0, "probes": 0},
    "outcome": null,
    "outcome_event_id": null,
    "safety_floor": {
      "floor_hash": "sha256:floor789",
      "locked": true,
      "human_reserved": false,
      "allowed_by_floor": true,
      "locked_checks": []
    },
    "replay_key": "claim_unit:run_001"
  },
  "run_id": "run_001",
  "session_id": "sess_001",
  "type": "decision",
  "unit_id": null
}
```

### Example 3: choosing the order of gate checks

```json
{
  "at": "2026-01-15T10:02:00Z",
  "event_id": "evt_dec_gate_001",
  "parent_ids": ["evt_pr_789"],
  "payload": {
    "schema_version": "decision.v1",
    "decision_kind": "gate_order",
    "policy_id": "pol_gate_order",
    "policy_version": "v2",
    "policy_hash": "sha256:ghi789",
    "policy_kind": "deterministic",
    "observed": {
      "pr_id": "pr_789",
      "changed_files": ["src/a.py", "src/b.py"],
      "risk_tags": ["privacy", "api"],
      "gate_registry": ["lint", "unit", "privacy", "integration"],
      "previous_failures": {"lint": 0, "unit": 1, "privacy": 0, "integration": 0}
    },
    "options": [
      {
        "option_id": "opt_order_lint_unit_privacy_integration",
        "action": {"order": ["lint", "unit", "privacy", "integration"]},
        "cost_estimate": {"wall_ms": 0, "tokens": 0, "usd": 0.0, "probes": 0},
        "allowed": true,
        "locked": false
      },
      {
        "option_id": "opt_order_privacy_lint_unit_integration",
        "action": {"order": ["privacy", "lint", "unit", "integration"]},
        "cost_estimate": {"wall_ms": 0, "tokens": 0, "usd": 0.0, "probes": 0},
        "allowed": true,
        "locked": false
      }
    ],
    "chosen_option_id": "opt_order_privacy_lint_unit_integration",
    "chosen_action": {"order": ["privacy", "lint", "unit", "integration"]},
    "cost": {"wall_ms": 3, "tokens": 0, "usd": 0.0, "probes": 0},
    "outcome": null,
    "outcome_event_id": null,
    "safety_floor": {
      "floor_hash": "sha256:floor789",
      "locked": true,
      "human_reserved": false,
      "allowed_by_floor": true,
      "locked_checks": ["privacy"]
    },
    "replay_key": "gate_order:pr_789"
  },
  "run_id": "run_001",
  "session_id": "sess_001",
  "type": "decision",
  "unit_id": "unit_456"
}
```

## 2. Python signatures

### Pure candidate policy

```python
from typing import Any, Mapping, Sequence

def candidate_policy(
    observed: Mapping[str, Any],
    options: Sequence[Mapping[str, Any]],
) -> str:
    """
    Pure, prefix-only policy. Returns an option_id from options.
    Must not mutate inputs, do I/O, or read unrevealed outcomes.
    """
    ...
```

### Replay evaluator

```python
from typing import Any, Callable, Mapping, Sequence

def replay_evaluate(
    policy: Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], str],
    history: Sequence[Mapping[str, Any]],
    *,
    lambda_cost: float = 1.0,
    mu_attempt: float = 0.0,
    nu_parallel: float = 0.0,
    outcome_values: Mapping[str, float] = {"pass": 1.0, "fail": 0.0, "no_data": 0.0},
) -> dict[str, Any]:
    ...
```

### Replay score formula

For each replay decision `d`:

- `s_d` is the outcome status: `"pass"`, `"fail"`, or `"no_data"`.
- `v_d = outcome_values[s_d]`.
- `c_d = decision.cost.usd + outcome.cost.usd` if the outcome exists, otherwise just `decision.cost.usd`.
- `executed_d = 1` if `s_d != "no_data"`, else `0`.

Then:

- `Q = max(v_d)` over all decisions, or `0.0` if empty.
- `C = sum(c_d)`.
- `N = sum(executed_d)`.
- `R = len(decisions)`.
- `P = N / R` if `R > 0`, else `0.0`.
- `score = Q - lambda_cost * C - mu_attempt * N + nu_parallel * P`.

## 3. Coverage refusal rule

At each replay step, let `record` be the current decision record. Let:

```python
allowed = {opt["option_id"] for opt in record["payload"]["options"]}
chosen = policy(record["payload"]["observed"], record["payload"]["options"])
```

If `chosen not in allowed`, raise `CoverageError` with the decision `event_id`, `chosen`, and `sorted(allowed)`. Do not substitute, default, interpolate, or guess. The replay aborts and returns no score.

If `chosen` is in `allowed` but not equal to `record["payload"]["chosen_option_id"]`, the outcome is `no_data` unless the chosen option has its own recorded outcome event. No guessing is allowed.

## 4. 40-line reference implementation

```python
from typing import Any, Callable, Mapping, Sequence

class CoverageError(Exception):
    pass

def replay_evaluate(
    policy: Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], str],
    history: Sequence[Mapping[str, Any]],
    *,
    lambda_cost: float = 1.0,
    mu_attempt: float = 0.0,
    nu_parallel: float = 0.0,
    outcome_values: Mapping[str, float] = {"pass": 1.0, "fail": 0.0, "no_data": 0.0},
) -> dict[str, Any]:
    cost, attempts, best, chosen_ids = 0.0, 0, 0.0, []
    for ev in history:
        p = ev["payload"]
        opts = p["options"]
        allowed = {o["option_id"] for o in opts}
        chosen = policy(p["observed"], opts)
        if chosen not in allowed:
            raise CoverageError(f"{chosen} not in {sorted(allowed)}")
        chosen_ids.append(chosen)
        if chosen == p["chosen_option_id"]:
            out = p.get("outcome") or {}
            status = out.get("status", "no_data")
            action_cost = out.get("cost", {})
        else:
            status, action_cost = "no_data", {}
        best = max(best, outcome_values.get(status, 0.0))
        if status != "no_data":
            attempts += 1
        cost += float(p.get("cost", {}).get("usd", 0.0))
        cost += float(action_cost.get("usd", 0.0))
    rounds = len(chosen_ids)
    parallel = attempts / rounds if rounds else 0.0
    score = best - lambda_cost * cost - mu_attempt * attempts + nu_parallel * parallel
    return {"score": score, "best": best, "cost": cost, "attempts": attempts, "rounds": rounds, "chosen": chosen_ids}
```
