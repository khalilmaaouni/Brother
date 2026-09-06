#!/usr/bin/env python3
"""bm_vault_events: the interchange boundary's replay contract (VB6-08).

WHY THIS EXISTS. The connector debate (docs/plan/CONNECTOR-DATA-MODEL-DEBATE-2026-08-30.md,
Part 3) locked three decisions: payload-free events, a language-neutral interchange, Iceberg
as the primary export with star/graph as projections. This module is the REPLAY CONTRACT
half of that lock: what an event looks like, and the fold rule any consumer must reproduce
to arrive at the same final state this module computes, regardless of the order the events
were delivered, replayed, or re-delivered in.

OWNERSHIP CONTRACT, verbatim: the vault owns the boundary through this exported stream and
the tables generated from it; the consumer owns ingestion after; a consumer that re-derives
payloads from events cannot exist because events are payload-free.

ENCRYPTION CLAUSE (VB8-03), applying to everything exported across this boundary: exported
tables carry Iceberg spec v3 table encryption through a catalog-integrated KMS; a direct
Parquet export (no catalog) uses Parquet modular encryption instead. Either way the KMS
backing it must be FIPS 140-3 validated. Customer-managed keys -- Tri-Secret Secure, Unity
Catalog CMK, preview status as of 2026-08-30 -- are the enterprise expectation for who holds
the key. The vault side of this boundary never hand-rolls column or file encryption; it
hands the exported bytes to the table format and catalog and lets THEM enforce it.

PAYLOAD SHAPE (VB3-15), the second half of "payload-free is a schema property": an
unknown field name is refused outright (below), and every field that IS declared is
also checked for SHAPE, not just presence -- a value longer than MAX_FIELD_LEN
characters, containing a newline, or reading as more than MAX_FIELD_WORDS space-
separated words is refused as payload-shaped, whatever field it rode in on. This is a
MECHANICAL rule, not a content-aware scan (a scan for "looks like a secret" would just
be re-implementing the payload it is trying to keep out): an id, a hash, a kind name
or an ISO date/time never needs to be long or wordy, and a note body, a quoted line or
a free-text summary almost always is. It will not catch a payload smuggled into a
short field (a truncated snippet under the bound), and it is not meant to -- the
REAL guarantee is structural: there is no field on this record FOR content to live in;
this check only makes a producer's attempt to abuse an existing field fail loudly
instead of quietly roundtripping.

REPLAY DISCIPLINE, CHECKPOINTS AND SNAPSHOTS (VB3-15). `make_checkpoint(events)` bundles
an already-validated, already-folded event set plus a self-integrity hash
(checkpoint_hash, stdlib sha256 over the rest of the record) so `replay_from_checkpoint`
never has to re-read or re-validate those events again: it only loads and validates
whatever NEW paths are handed to it, drops any event whose event_key the checkpoint
already includes (a legitimate re-delivery, not an error -- the same idempotency-key
promise `_dedup` already enforces), and folds the checkpoint's own events together with
whatever is genuinely new (fold() still needs the full set to resolve a correction that
targets something already checkpointed). A checkpoint whose own hash does not match a
fresh recompute is refused outright, never trusted on its word. `make_snapshot(state)` /
`load_snapshot(snapshot)` do the same self-integrity check for a serialized fold()
result: a snapshot carries a sha256 over its own content and refuses to load when that
hash mismatches. Call this INTEGRITY-HASHED, never "signed" beyond that: stdlib hashlib
only, no keypair, nothing that proves WHO produced it, only that it has not silently
changed since it was made.

THE EVENT RECORD. One JSON object per line (JSONL), six fields; any other key is rejected
as malformed -- payload-free is a schema property enforced at validation, not a convention
a producer could quietly break:
  event_key   idempotency key: entity id plus field plus effective time, or the note id
              for note events. Identifies THIS event, not a mutable "current" slot.
  kind        "upsert" | "correct" | "tombstone" | "merged_into" | "unmerged"
  ref         the note or entity id this event is about. PAYLOAD-FREE: never content,
              only ids and hashes -- there is no field on this record a payload could live
              in, which is what makes payload-free a schema property instead of a promise.
  corrects    the event_key of the event this one corrects. Required and non-empty for
              kind="correct"; forbidden (must be absent or null) for every other kind.
  occurred_at ISO 8601 string: when the fact became true.
  recorded_at ISO 8601 string: when this event was written to the log.

IDENTITY EVENTS (VB3-17), the two kinds a golden-record merge and its reversal are
recorded as -- never a rewrite of a note, never a mutation of a prior event, only new
events appended to the log, exactly like every other kind here:
  merged_into  `ref` is the entity merged away, plus three more required non-empty
               string fields: `into` (the survivor entity), `rule_version` (which
               survivorship rule decided it) and `effective` (ISO date the merge takes
               effect). `corrects` is forbidden, same as every non-correct kind.
  unmerged     `ref` is the entity being restored, plus `into` (the survivor it is
               being separated from) and `effective` (ISO date the separation takes
               effect), both required non-empty strings. `rule_version` is optional
               here (a reversal does not itself need a new rule) but if present must be
               a non-empty string. `corrects` is forbidden.
`into`, `rule_version` and `effective` are validated exactly as strictly as `corrects`
is: required and non-empty where the kind demands them, refused outright (never
silently ignored) on every kind that does not carry them. This record is deliberately
NOT resolved into "current identity" by fold() below -- fold's job is the note-upsert
replay contract; a consumer that wants the current survivor for an id replays these two
kinds itself (tools/bm_vault_identity.py is that consumer, VB3-17).

REPLAY SEMANTICS (`replay FILE...`), the fold rule:
  1. DUPLICATES. Two events sharing an event_key must be byte-identical on kind, ref,
     corrects and occurred_at (recorded_at may legitimately differ across two writes of
     the same logical event); such a pair collapses to one and applies once. Two events
     sharing an event_key that disagree on any of those fields is a malformed stream --
     an idempotency key is a promise that the key names ONE event, and a caller that
     breaks that promise gets NO-DATA, never a silent pick of one side.
  2. ORDERING is never read from arrival position. The fold builds its answer from the
     full set of parsed events, keyed by content, so any permutation of the same set (any
     file order, any line order, split across any number of files) produces the same
     final state. There is no "apply in stream order" step for this to depend on.
  3. CORRECTIONS chain. A "correct" event's `corrects` must name an event_key already
     present in the stream and sharing its `ref` (unknown or mismatched target: malformed
     stream). Corrections may themselves be corrected (a chain). Whichever event in the
     chain has the latest (occurred_at, recorded_at) tuple wins -- last-correction-wins,
     ties broken by event_key for full determinism. This is arrival-order independent: a
     correction with a later occurred_at wins over one that was recorded first, and a
     correction that shows up earlier in the file loses to one with a later occurred_at
     that appears after it. A correction chain that never reaches a live upsert -- a cycle
     among corrections (A corrects B, B corrects A), or a chain that bottoms out on
     something other than an upsert -- is a malformed stream: CYCLE or ORPHAN-CHAIN,
     naming every event_key in the broken chain, never a silent drop from fold output.
  4. TOMBSTONES end a ref. If any tombstone event names a given `ref`, that ref carries
     no live state at all -- not its original upsert, not any correction of it -- no
     matter where in occurred_at/recorded_at order the tombstone falls relative to them.
     This is the erasure-versus-replay lock: re-emitting (or replaying an export that
     still contains) the tombstoned ref's original upsert event after the tombstone does
     NOT resurrect it, because the fold never asks "did the tombstone arrive after this
     event" -- it asks "does this ref have a tombstone anywhere in the set," which a late
     duplicate of an already-tombstoned upsert answers yes to exactly the same as before.

Exit codes: 0 the stream folded cleanly (0 or more live/tombstoned refs is still clean).
2 NO-DATA: a file could not be read, or a line failed validation -- the offending file and
line are named, never guessed at. Python 3.9 floor, standard library only.
"""
import hashlib
import json
import os
import sys

REQUIRED_FIELDS = ("event_key", "kind", "ref", "occurred_at", "recorded_at")
#: into/rule_version/effective are the merged_into/unmerged payload fields (VB3-17),
#: validated exactly as strictly as corrects below: required where a kind demands them,
#: refused outright on any kind that does not.
ALLOWED_FIELDS = frozenset(REQUIRED_FIELDS) | {"corrects", "into", "rule_version", "effective"}
KINDS = ("upsert", "correct", "tombstone", "merged_into", "unmerged")
#: PAYLOAD SHAPE (VB3-15): a tight length bound plus a naive word-count heuristic, see
#: the module docstring's own "PAYLOAD SHAPE" section for why this is mechanical rather
#: than content-aware, and what it does and does not catch.
MAX_FIELD_LEN = 200
MAX_FIELD_WORDS = 12


class FoldError(Exception):
    """A malformed stream: the caller reports NO-DATA and exits 2."""


def _payload_shape_violation(value):
    """None when value is clean; otherwise the reason it reads as payload-shaped
    rather than a structural identifier. See the module docstring's PAYLOAD SHAPE
    section: mechanical (length, newline, word count), never a content-aware scan."""
    if len(value) > MAX_FIELD_LEN:
        return "is longer than %d characters" % MAX_FIELD_LEN
    if "\n" in value:
        return "contains a newline"
    if len(value.split()) > MAX_FIELD_WORDS:
        return "reads as prose (more than %d words)" % MAX_FIELD_WORDS
    return None


def _validate(rec, where):
    if not isinstance(rec, dict):
        raise FoldError("%s: not a JSON object" % where)
    extra = sorted(set(rec) - ALLOWED_FIELDS)
    if extra:
        raise FoldError("%s: unknown field %r -- payload-free events carry only %s"
                         % (where, extra[0], sorted(ALLOWED_FIELDS)))
    for field, value in rec.items():
        if isinstance(value, str):
            problem = _payload_shape_violation(value)
            if problem:
                raise FoldError(
                    "%s: field %r %s -- payload-free events carry only ids, hashes, "
                    "field names and tombstones, never note body text"
                    % (where, field, problem))
    for field in REQUIRED_FIELDS:
        value = rec.get(field)
        if not isinstance(value, str) or not value:
            raise FoldError("%s: missing or empty required field %r" % (where, field))
    if rec["kind"] not in KINDS:
        raise FoldError("%s: unknown kind %r (must be one of %s)"
                         % (where, rec["kind"], ", ".join(KINDS)))
    corrects = rec.get("corrects")
    if rec["kind"] == "correct":
        if not isinstance(corrects, str) or not corrects:
            raise FoldError("%s: kind=correct requires a non-empty 'corrects'" % where)
    elif corrects is not None:
        raise FoldError("%s: 'corrects' is only valid for kind=correct" % where)

    into = rec.get("into")
    rule_version = rec.get("rule_version")
    effective = rec.get("effective")
    if rec["kind"] == "merged_into":
        for name, value in (("into", into), ("rule_version", rule_version),
                             ("effective", effective)):
            if not isinstance(value, str) or not value:
                raise FoldError("%s: kind=merged_into requires a non-empty %r" % (where, name))
    elif rec["kind"] == "unmerged":
        for name, value in (("into", into), ("effective", effective)):
            if not isinstance(value, str) or not value:
                raise FoldError("%s: kind=unmerged requires a non-empty %r" % (where, name))
        if rule_version is not None and not (isinstance(rule_version, str) and rule_version):
            raise FoldError("%s: 'rule_version' must be a non-empty string when present"
                            % where)
    else:
        for name, value in (("into", into), ("rule_version", rule_version),
                             ("effective", effective)):
            if value is not None:
                raise FoldError("%s: %r is only valid for kind=merged_into/unmerged"
                                % (where, name))
    return rec


def parse_lines(lines_with_source):
    """lines_with_source: iterable of (source_label, line_text). Returns the list of
    validated event dicts, or raises FoldError naming the exact source and line."""
    events = []
    for source, line in lines_with_source:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError as e:
            raise FoldError("%s: invalid JSON (%s)" % (source, e))
        events.append(_validate(rec, source))
    return events


def load_events(paths):
    """Reads every path as JSONL. Raises FoldError (bad JSON/schema) or OSError (unreadable
    file) -- callers translate both into the same NO-DATA exit."""
    def _gen():
        for path in paths:
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    yield ("%s:%d" % (path, lineno)), line
    return parse_lines(_gen())


def _dedup(events):
    """event_key -> event, collapsing byte-identical duplicates. Raises FoldError when two
    events share a key but disagree on kind/ref/corrects/occurred_at -- an idempotency key
    is a promise the key names one event, and a caller that breaks it does not get a
    silent pick of either side."""
    by_key = {}
    for e in events:
        k = e["event_key"]
        fingerprint = (e["kind"], e["ref"], e.get("corrects"), e["occurred_at"])
        if k in by_key:
            prior = by_key[k]
            prior_fp = (prior["kind"], prior["ref"], prior.get("corrects"), prior["occurred_at"])
            if fingerprint != prior_fp:
                raise FoldError("event_key %r used for two different events" % k)
            continue
        by_key[k] = e
    return by_key


def _diagnose_unreached_corrections(by_key, unreached_keys):
    """unreached_keys: correction event_keys that resolve_chain never visited from any
    upsert -- they would otherwise vanish from fold output with no error. Names the whole
    connected component (via `corrects` links, undirected) as CYCLE when it bottoms out on
    nothing but corrections (a loop), or ORPHAN-CHAIN when it bottoms out on a real event
    that just isn't an upsert (e.g. a correction targeting a tombstone)."""
    adjacency = {}
    for e in by_key.values():
        if e["kind"] != "correct":
            continue
        a, b = e["event_key"], e["corrects"]
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    component = set()
    frontier = [unreached_keys[0]]
    while frontier:
        nxt = []
        for k in frontier:
            if k in component:
                continue
            component.add(k)
            nxt.extend(nb for nb in adjacency.get(k, ()) if nb not in component)
        frontier = nxt

    named = sorted(component)
    if any(by_key[k]["kind"] != "correct" for k in component):
        raise FoldError("ORPHAN-CHAIN: correction chain never reaches a live upsert -- "
                         "event_keys %s" % named)
    raise FoldError("CYCLE: correction chain never reaches a live upsert -- "
                     "event_keys %s form a cycle" % named)


def fold(events):
    """Pure and order-independent: fold(events) == fold(any permutation of events). Returns
    {"live": [...], "tombstoned": [...]}, both sorted for a deterministic report. See the
    module docstring for the four rules this implements."""
    by_key = _dedup(events)

    tombstoned_refs = {}
    for e in by_key.values():
        if e["kind"] != "tombstone":
            continue
        cur = tombstoned_refs.get(e["ref"])
        order = (e["occurred_at"], e["recorded_at"], e["event_key"])
        if cur is None or order < (cur["occurred_at"], cur["recorded_at"], cur["event_key"]):
            tombstoned_refs[e["ref"]] = e

    corrections_targeting = {}
    for e in by_key.values():
        if e["kind"] != "correct":
            continue
        target = by_key.get(e["corrects"])
        if target is None:
            raise FoldError("correction %r corrects unknown event_key %r"
                             % (e["event_key"], e["corrects"]))
        if target["ref"] != e["ref"]:
            raise FoldError("correction %r has ref %r but corrects %r whose ref is %r"
                             % (e["event_key"], e["ref"], e["corrects"], target["ref"]))
        corrections_targeting.setdefault(e["corrects"], []).append(e)

    reached_from_upsert = set()

    def resolve_chain(root_key):
        chain = [by_key[root_key]]
        seen = {root_key}
        frontier = [root_key]
        while frontier:
            nxt = []
            for k in frontier:
                for corr in corrections_targeting.get(k, ()):
                    ck = corr["event_key"]
                    if ck in seen:
                        continue
                    seen.add(ck)
                    chain.append(corr)
                    nxt.append(ck)
            frontier = nxt
        reached_from_upsert.update(seen)
        return max(chain, key=lambda ev: (ev["occurred_at"], ev["recorded_at"], ev["event_key"]))

    live = []
    for e in by_key.values():
        if e["kind"] != "upsert":
            continue
        # Always resolve the chain, even for a tombstoned ref, so its corrections are
        # marked reached (legitimately erased) instead of misdiagnosed as orphaned below.
        winner = resolve_chain(e["event_key"])
        if e["ref"] in tombstoned_refs:
            continue   # erasure beats replay: a tombstoned ref never resurrects
        live.append({
            "ref": e["ref"],
            "root_event_key": e["event_key"],
            "winning_event_key": winner["event_key"],
            "winning_kind": winner["kind"],
            "occurred_at": winner["occurred_at"],
            "recorded_at": winner["recorded_at"],
        })

    unreached = sorted(
        e["event_key"] for e in by_key.values()
        if e["kind"] == "correct" and e["event_key"] not in reached_from_upsert
    )
    if unreached:
        _diagnose_unreached_corrections(by_key, unreached)

    tombstoned = [
        {"ref": ref, "tombstone_event_key": ev["event_key"],
         "occurred_at": ev["occurred_at"], "recorded_at": ev["recorded_at"]}
        for ref, ev in tombstoned_refs.items()
    ]
    return {
        "live": sorted(live, key=lambda r: (r["ref"], r["root_event_key"])),
        "tombstoned": sorted(tombstoned, key=lambda r: r["ref"]),
    }


def _content_hash(record, hash_key):
    """sha256 over record's own content, the hash_key entry itself excluded -- the same
    self-integrity shape bm_vault_retention.py's forget-execute receipt already uses.
    STDLIB HASHING ONLY: this proves the record has not silently changed, never who
    produced it -- call it integrity-hashed, never signed."""
    body = {k: v for k, v in record.items() if k != hash_key}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def make_checkpoint(events):
    """A checkpoint bundling an already-validated, already-folded event set plus a
    self-integrity hash. See the module docstring's REPLAY DISCIPLINE section for what
    replay_from_checkpoint below does with it, and why bundling the events (not just
    the derived live/tombstoned state) is what keeps a later correction crossing the
    checkpoint boundary resolvable."""
    checkpoint = {
        "included_event_keys": sorted(e["event_key"] for e in events),
        "events": events,
    }
    checkpoint["checkpoint_hash"] = _content_hash(checkpoint, "checkpoint_hash")
    return checkpoint


def verify_checkpoint(checkpoint):
    """Raises FoldError when checkpoint_hash does not match a fresh recompute over the
    rest of the record -- a tampered or corrupt checkpoint is refused, never trusted on
    its own word."""
    expected = _content_hash(checkpoint, "checkpoint_hash")
    got = checkpoint.get("checkpoint_hash")
    if got != expected:
        raise FoldError("checkpoint_hash mismatch: tampered or corrupt checkpoint "
                         "(expected %s, got %s)" % (expected, got))


def replay_from_checkpoint(checkpoint, new_paths):
    """Verifies the checkpoint, then loads and validates ONLY new_paths -- the
    checkpoint's own events are never re-read or re-validated. Any newly-loaded event
    whose event_key the checkpoint already includes is a legitimate re-delivery, not an
    error, and is skipped rather than re-applied (the same idempotency-key promise
    _dedup already enforces on a single fold() call). Returns (state, skipped_count,
    new_count); folds the checkpoint's own events together with the genuinely new ones,
    because fold() needs the whole set to resolve a correction that targets something
    already checkpointed."""
    verify_checkpoint(checkpoint)
    included = set(checkpoint["included_event_keys"])
    new_events = load_events(new_paths) if new_paths else []
    fresh = [e for e in new_events if e["event_key"] not in included]
    skipped = len(new_events) - len(fresh)
    state = fold(checkpoint["events"] + fresh)
    return state, skipped, len(fresh)


def make_snapshot(state):
    """A serialized fold() result (or anything projected from one) carrying a
    self-integrity hash -- see load_snapshot below and the module docstring's REPLAY
    DISCIPLINE section."""
    snapshot = {"state": state}
    snapshot["snapshot_hash"] = _content_hash(snapshot, "snapshot_hash")
    return snapshot


def load_snapshot(snapshot):
    """Returns snapshot["state"] after verifying snapshot_hash matches a fresh
    recompute; raises FoldError on a mismatch rather than loading a tampered or
    corrupt snapshot silently."""
    expected = _content_hash(snapshot, "snapshot_hash")
    got = snapshot.get("snapshot_hash")
    if got != expected:
        raise FoldError("snapshot_hash mismatch: tampered or corrupt snapshot "
                         "(expected %s, got %s)" % (expected, got))
    return snapshot["state"]


def cmd_replay(args):
    paths = args
    if not paths:
        sys.stderr.write("bm_vault_events: replay needs one or more FILE arguments\n")
        return 2
    try:
        events = load_events(paths)
    except FoldError as e:
        print("NO-DATA: %s" % e)
        return 2
    except OSError as e:
        print("NO-DATA: could not read a stream file (%s)" % e)
        return 2
    try:
        state = fold(events)
    except FoldError as e:
        print("NO-DATA: %s" % e)
        return 2

    print("live: %d" % len(state["live"]))
    for r in state["live"]:
        print("  ref=%s event_key=%s winner=%s kind=%s occurred_at=%s recorded_at=%s"
              % (r["ref"], r["root_event_key"], r["winning_event_key"], r["winning_kind"],
                 r["occurred_at"], r["recorded_at"]))
    print("tombstoned: %d" % len(state["tombstoned"]))
    for r in state["tombstoned"]:
        print("  ref=%s tombstone=%s occurred_at=%s recorded_at=%s"
              % (r["ref"], r["tombstone_event_key"], r["occurred_at"], r["recorded_at"]))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[0] != "replay":
        sys.stderr.write("bm_vault_events: unknown command %r; known: replay\n" % argv[0])
        return 2
    return cmd_replay(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
