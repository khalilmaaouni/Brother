# The U1 plus U2 reference: what the controller consumes from the contract

Status: CURRENT as of 2026-08-05.

LOAD WHEN: building, extending, or debugging anything in `tools/bm_controller.py`,
or reasoning about how the durable Full-Auto controller (U2) depends on the
signed autonomy contract layer (U1). The plain-language account of each
layer lives in `docs/AUTONOMY.md` and `docs/FULL-AUTO.md`; this page is the
narrower technical map between them.

No em or en dashes anywhere in this page.

## The seven U1 surfaces U2 consumes

U1 (`tools/bm_autonomy.py`, `tools/bm_store.py` schema 14) is the signed
contract, the gate, the breaker, the kill switch, and the record. U2 adds no
enforcement of its own; it reads and writes exactly these seven surfaces,
never anything else of U1's:

1. **`gate_check(project_id, action_class, path=None, surface=None)`**,
   read on every path-bearing action before a unit is claimed and
   dispatched (`_claim_and_dispatch` in `tools/bm_controller.py`). Its
   eight-step check order is normative: no contract at all, a non-live
   contract, a floor id, an ungranted risk class, a path outside scope, a
   surface not allowed, a spend hard-stop, and only then `ALLOWED`. The
   `revision` it returns is captured as `contract_revision` on the
   dispatch row and is the staleness protocol's own anchor (surface 7,
   below).
2. **`latest_contract(project_id, raw=True)`**, read at the top of every
   `step()` call to confirm the contract is still `live` before anything
   else happens, and again at the moment a result is judged (the
   staleness re-read).
3. **`spend_totals(project_id)`**, read every `step()` call: `ok` starts
   new units, `soft-stop` finishes only what is already in flight and
   starts nothing new, `hard-stop` drains the whole run, `no-data` starts
   units and says so.
4. **`record_spend(project_id, tokens, minutes, note, session_id, actor)`**,
   called once a worker's result carries a cost, guarded behind a fresh
   live-contract read so a revoke landing between dispatch and this call
   never crashes spend bookkeeping (the actual REJECTION of a stale
   result happens at surface 7, not here).
5. **`record_checkpoint(project_id, controller_id, kind, note, session_id,
   actor)`**, the controller's own liveness beacon (`kind="heartbeat"`
   before a wave, `kind="unit-green"` on acceptance, `kind="stopped"` on
   drain, `kind="deliverable-ready"` at final delivery) and the same
   surface `bm-autonomy checkpoint` writes through by hand.
6. **`record_interruption(project_id, condition, question, session_id,
   actor)`**, raised when the circuit breaker escalates (a unit failed
   twice) or when a unit is permanently ungrantable under the live
   contract; `condition` must be one of the four in `AUTONOMY_CONDITIONS`,
   never a free-form string.
7. **`queue_human_step(project_id, floor, lane, what, click_path, blocks,
   session_id, actor)`** and **`list_human_steps(project_id,
   resolved=False)`**, the founder-only queue. Only the named lane is
   marked `BLOCKED`; every other lane keeps running. This is the
   mechanism behind the design's own invariant that a human-blocked lane
   never stalls an independent one.

U2 also REUSES two U1 primitives verbatim, no new mechanism: `claim()` and
`transition()`, the fence store every parallel-fenced unit and the
controller's own run-level identity are ordinary holders of.

## gate-check is called on every path-bearing action

Every unit the controller is about to claim and dispatch is
`gate_check`ed first, scoped to that unit's OWN `risk_class` and the first
path in its `write_scope`:

```
verdict = store.gate_check(project_id, unit["risk_class"], path=write_scope[0])
```

A `REFUSED-STATE` or `REFUSED-BREAKER` verdict is a transient, whole-run
condition (the contract moved, or the breaker tripped): the controller
starts draining the entire run. A `REFUSED-CLASS`, `REFUSED-SCOPE`, or
`REFUSED-FLOOR` verdict is a genuine authorisation gap for THAT unit alone:
it is routed through the same circuit breaker a dispatch rejection uses,
so an ungrantable unit escalates after its retry ceiling rather than being
re-selected forever with no visible progress and no founder step.

`gate_check`'s own JSON output carries the `revision` it judged against.
That number is what the staleness protocol below compares against later,
never re-derived any other way.

## The staleness protocol: the revision U2 captures, and where it is re-read

A contract can move (pause, revoke, amend) at any moment, including between
the instant a unit is dispatched and the instant its result is judged. The
`contract_revision` captured from `gate_check` at dispatch time is stored on
the dispatch row; when a result comes back, `latest_contract`'s CURRENT
revision is re-read and compared. If it no longer matches, the result is
NOT accepted, whatever the done-check says: the unit is re-queued rather
than treated as authorised under a contract that no longer holds. This is
the exact TOCTOU window U1's own design leaves for U2 to close, and closing
it here, at the one place a result is about to be accepted, is the whole
of how it is closed.

## What U2 does not read from U1

`list_assumptions` and `answer_interruption` are U1 surfaces the controller
never calls: assumptions are a founder or model's own reversible record,
and answering an interruption is a founder action taken through
`bm-autonomy interrupt`/`human-steps --resolve`, never something the
controller resolves on its own behalf.
