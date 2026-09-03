# Module: telemetry

LOAD WHEN: a session-start nag, a spend line, a scorecard or an outcome rating is being read or written, or the telemetry ledger is being reasoned about.

(The telemetry line of the active-laws digest, and the startup emitters that were
unconditional before the default profile was cut down. The routing table in
`references/modules.md` names when to load it.)

## The line this module owns

Moved verbatim from `references/laws-full-digest.md`, where it still stands:

> Telemetry is hook-written and idempotent (voluntary logging collapses). Team
> learning spreads only through a reviewed PR into LEARNED.md; local telemetry never
> leaves the machine. [the SessionEnd hook WRITES the ledger and decides nothing; the
> checks fed by it are named on their own lines. The rest of this line is human]

## What is switched on

`tools/sbe_sessionstart.py` runs `tools/sbe_telemetry.py startup-nags` only when this
module is enabled. That emitter prints, when it has something to say:

- the weekly review being overdue, or never having run,
- the last 24 hours of output tokens across sessions,
- the telemetry heartbeat having been silent for 48 hours,
- an active day with no vault session log.

Every one of those is a nag about the operator's own record-keeping. None of them is
the safety floor, which is why the default profile does not spend session context on
them, and why switching this module on is the only way to get them back.

## What stays on at every profile

The compaction hint (`tools/sbe_telemetry.py compact-hint`) is NOT part of this
module. It is the recovery pointer a session reads after a compaction or a resume, it
is the most safety-relevant line the hook prints, and it prints first at every
profile, before anything a truncation could reach.

## The surfaces

- `tools/sbe_telemetry.py` (the ledger writer, the hooks, the nags, the data commands).
- The ledger and scorecard files under the vault's `99-System/telemetry/`.
- The checks fed by the ledger in `tools/sbe_score.py`, each of which is NO-DATA with no vault.
