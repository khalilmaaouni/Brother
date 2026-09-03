#!/usr/bin/env python3
"""Tests for the interchange replay contract (VB6-08): tools/bm_vault_events.py.

Load-bearing behavior: a fixture stream (a duplicate event, a competing-correction pair
whose winner must be decided by occurred_at and not by arrival order, a late correction,
and a tombstone) folds to the SAME final state no matter what order the events are handed
in. FIXED_SEEDS drives several deterministic shuffles (no randomness at runtime -- the
seeds are fixed in source, only the test harness shuffles).

Run: python3 tools/test_bm_vault_events.py      (unittest output, exit 0 or 1)
"""
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_events as ev  # noqa: E402

TOOL = os.path.join(HERE, "bm_vault_events.py")
INDEXER_TOOL = os.path.join(HERE, "bm_vault.py")
RETENTION_TOOL = os.path.join(HERE, "bm_vault_retention.py")
FIXED_SEEDS = (1, 2, 3, 4, 5, 42)


def run(argv):
    p = subprocess.run([sys.executable, TOOL] + argv,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


def run_tool(tool, argv, env):
    p = subprocess.run([sys.executable, tool] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.returncode, p.stdout


def event(event_key, kind, ref, occurred_at, recorded_at, corrects=None):
    rec = {"event_key": event_key, "kind": kind, "ref": ref,
           "occurred_at": occurred_at, "recorded_at": recorded_at}
    if corrects is not None:
        rec["corrects"] = corrects
    return rec


# note-1: an original upsert, a duplicate of it, and two competing corrections where the
# one with the LATER occurred_at must win even though it is not the one recorded last.
U1 = event("k-upsert-1", "upsert", "note-1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z")
U1_DUP = event("k-upsert-1", "upsert", "note-1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z")
C_EARLY = event("k-correct-early", "correct", "note-1", "2026-01-03T00:00:00Z",
                "2026-01-03T00:00:01Z", corrects="k-upsert-1")
C_LATE = event("k-correct-late", "correct", "note-1", "2026-01-08T00:00:00Z",
               "2026-01-02T00:00:00Z", corrects="k-upsert-1")   # recorded EARLIER, occurs LATER

# note-2: an upsert plus a tombstone plus a late duplicate re-emission of that same upsert
# arriving after the tombstone -- must not resurrect it.
U2 = event("k-upsert-2", "upsert", "note-2", "2026-02-01T00:00:00Z", "2026-02-01T00:00:01Z")
U2_REEMIT = event("k-upsert-2", "upsert", "note-2", "2026-02-01T00:00:00Z", "2026-02-01T00:00:01Z")
TOMB2 = event("k-tomb-2", "tombstone", "note-2", "2026-02-02T00:00:00Z", "2026-02-02T00:00:01Z")

FIXTURE = [U1, U1_DUP, C_EARLY, C_LATE, U2, U2_REEMIT, TOMB2]

EXPECTED = {
    "live": [{
        "ref": "note-1", "root_event_key": "k-upsert-1",
        "winning_event_key": "k-correct-late", "winning_kind": "correct",
        "occurred_at": "2026-01-08T00:00:00Z", "recorded_at": "2026-01-02T00:00:00Z",
    }],
    "tombstoned": [{
        "ref": "note-2", "tombstone_event_key": "k-tomb-2",
        "occurred_at": "2026-02-02T00:00:00Z", "recorded_at": "2026-02-02T00:00:01Z",
    }],
}


class FoldConvergenceTest(unittest.TestCase):
    """The done-check itself: any order folds to EXPECTED."""

    def test_in_order_matches_expected(self):
        self.assertEqual(EXPECTED, ev.fold(FIXTURE))

    def test_shuffled_permutations_all_converge_to_the_same_state(self):
        for seed in FIXED_SEEDS:
            shuffled = list(FIXTURE)
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(EXPECTED, ev.fold(shuffled),
                             "seed %d diverged from the in-order fold" % seed)

    def test_explicit_adversarial_orders_converge_to_the_same_state(self):
        # MINOR 2: explicit worst-case orderings rather than relying only on random
        # shuffles, so the adversarial cases are named and reproducible in source.
        orders = {
            "tombstone_first": [TOMB2, U2, U2_REEMIT, U1, U1_DUP, C_EARLY, C_LATE],
            "correction_before_its_targets_arrival_position":
                [C_LATE, C_EARLY, U1, U1_DUP, U2, TOMB2, U2_REEMIT],
            "duplicate_after_tombstone": [U1, C_EARLY, C_LATE, U2, TOMB2, U2_REEMIT, U1_DUP],
        }
        for name, ordered in orders.items():
            self.assertEqual(EXPECTED, ev.fold(ordered),
                             "%s diverged from the in-order fold" % name)

    def test_late_correction_wins_by_occurred_at_not_by_recorded_at_order(self):
        # C_EARLY was RECORDED after C_LATE's occurred_at describes, and C_LATE was
        # actually recorded first -- if the fold used recorded_at or arrival order as the
        # primary key instead of occurred_at, C_EARLY would win. It must not.
        state = ev.fold([U1, C_LATE, C_EARLY])
        self.assertEqual("k-correct-late", state["live"][0]["winning_event_key"])

    def test_tombstone_wins_regardless_of_where_it_falls_in_the_list(self):
        state = ev.fold([TOMB2, U2, U2_REEMIT])
        self.assertEqual([], [r for r in state["live"] if r["ref"] == "note-2"])
        self.assertEqual(1, len(state["tombstoned"]))

    def test_driven_backwards_tombstone_removed_resurrects(self):
        """Documents the exact defect the erasure-versus-replay lock guards against: strip
        the tombstoned-ref skip out of fold() (the `if e["ref"] in tombstoned_refs: continue`
        line) and note-2 reappears in `live` even with TOMB2 present in the input -- this
        test's own assertion above (test_tombstone_wins...) would then fail. Not run as
        part of CI; a manual regression check. Purge tools/__pycache__ between swaps so a
        stale .pyc cannot let the reverted source pass on old bytecode."""
        pass


class ValidationTest(unittest.TestCase):
    def test_duplicate_event_key_with_different_content_is_malformed(self):
        conflicting = event("k-upsert-1", "upsert", "note-1",
                            "1999-01-01T00:00:00Z", "1999-01-01T00:00:00Z")
        with self.assertRaises(ev.FoldError):
            ev.fold([U1, conflicting])

    def test_correction_missing_corrects_is_malformed(self):
        bad = {"event_key": "k-bad", "kind": "correct", "ref": "note-1",
               "occurred_at": "2026-01-01T00:00:00Z", "recorded_at": "2026-01-01T00:00:00Z"}
        with self.assertRaises(ev.FoldError):
            ev._validate(bad, "test")

    def test_corrects_on_non_correct_kind_is_malformed(self):
        bad = {"event_key": "k-bad", "kind": "upsert", "ref": "note-1", "corrects": "x",
               "occurred_at": "2026-01-01T00:00:00Z", "recorded_at": "2026-01-01T00:00:00Z"}
        with self.assertRaises(ev.FoldError):
            ev._validate(bad, "test")

    def test_correction_targeting_unknown_event_key_is_malformed(self):
        bad = event("k-correct-x", "correct", "note-1", "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z", corrects="does-not-exist")
        with self.assertRaises(ev.FoldError):
            ev.fold([U1, bad])

    def test_unknown_kind_is_malformed(self):
        bad = {"event_key": "k-bad", "kind": "delete", "ref": "note-1",
               "occurred_at": "2026-01-01T00:00:00Z", "recorded_at": "2026-01-01T00:00:00Z"}
        with self.assertRaises(ev.FoldError):
            ev._validate(bad, "test")

    def test_unknown_field_is_malformed_and_names_the_key(self):
        # MAJOR 1: payload-free must be a schema property, not a convention an extra key
        # can quietly break. Driven backwards: comment out the `if extra:` block in
        # _validate (tools/bm_vault_events.py ~80-83) and this assertion fails because
        # the record round-trips instead of refusing.
        bad = event("k-bad", "upsert", "note-1",
                    "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        bad["payload"] = "this must never ride along"
        with self.assertRaises(ev.FoldError) as cm:
            ev._validate(bad, "test:1")
        self.assertIn("payload", str(cm.exception))
        self.assertIn("test:1", str(cm.exception))


class PayloadShapeTest(unittest.TestCase):
    """VB3-15: a declared field whose VALUE is payload-shaped (too long, a newline, too
    many words) is refused by name, exactly like an undeclared field name already is.
    Driven backwards: comment out the `for field, value in rec.items()` shape-check loop
    in bm_vault_events._validate and every test below fails because the record
    round-trips instead of refusing."""

    def test_ref_holding_note_body_text_is_refused_by_name(self):
        bad = event("k-bad", "upsert", "This is a whole sentence of note body "
                    "content masquerading as a plain identifier field, which is "
                    "exactly what payload-free forbids riding along on ref.",
                    "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        with self.assertRaises(ev.FoldError) as cm:
            ev._validate(bad, "test:1")
        self.assertIn("ref", str(cm.exception))

    def test_field_over_the_length_bound_is_refused(self):
        bad = event("k" * (ev.MAX_FIELD_LEN + 1), "upsert", "note-1",
                    "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        with self.assertRaises(ev.FoldError) as cm:
            ev._validate(bad, "test:1")
        self.assertIn("event_key", str(cm.exception))
        self.assertIn("characters", str(cm.exception))

    def test_field_with_a_newline_is_refused(self):
        bad = event("k-bad", "upsert", "note-1\nsecond line",
                    "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        with self.assertRaises(ev.FoldError) as cm:
            ev._validate(bad, "test:1")
        self.assertIn("newline", str(cm.exception))

    def test_a_short_ordinary_id_is_not_flagged(self):
        ok = event("k-ok", "upsert", "n-1234567890abcdef",
                   "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        ev._validate(ok, "test")  # must not raise

    def test_a_64_char_sha256_hash_is_not_flagged(self):
        # ids and hashes -- the whole point of payload-free -- must stay well clear of
        # both bounds even at their longest ordinary shape.
        ok = event("k-ok", "upsert", "a" * 64,
                   "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        ev._validate(ok, "test")  # must not raise


class CheckpointAndSnapshotTest(unittest.TestCase):
    """VB3-15 replay discipline: a checkpoint skips already-folded events on resume and
    refuses when tampered; a snapshot refuses to load when tampered."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm_checkpoint_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, records):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return path

    def test_checkpoint_carries_a_self_integrity_hash(self):
        checkpoint = ev.make_checkpoint([U1, U2])
        self.assertIn("checkpoint_hash", checkpoint)
        ev.verify_checkpoint(checkpoint)  # must not raise

    def test_tampered_checkpoint_refuses(self):
        checkpoint = ev.make_checkpoint([U1, U2])
        checkpoint["included_event_keys"].append("k-injected")
        with self.assertRaises(ev.FoldError) as cm:
            ev.verify_checkpoint(checkpoint)
        self.assertIn("checkpoint_hash mismatch", str(cm.exception))

    def test_replay_from_checkpoint_skips_already_included_events_on_re_delivery(self):
        checkpoint = ev.make_checkpoint([U1, C_EARLY])
        # b.jsonl re-delivers the exact same two events (a legitimate re-delivery, e.g.
        # a consumer resuming from an export that overlaps the checkpoint) plus nothing
        # new -- both must be skipped, not re-applied.
        redelivered = self._write("b.jsonl", [U1, C_EARLY])
        state, skipped, new_count = ev.replay_from_checkpoint(checkpoint, [redelivered])
        self.assertEqual(2, skipped)
        self.assertEqual(0, new_count)
        self.assertEqual(ev.fold([U1, C_EARLY]), state)

    def test_replay_from_checkpoint_folds_in_a_genuinely_new_tombstone(self):
        checkpoint = ev.make_checkpoint([U2])
        new_file = self._write("c.jsonl", [TOMB2])
        state, skipped, new_count = ev.replay_from_checkpoint(checkpoint, [new_file])
        self.assertEqual(0, skipped)
        self.assertEqual(1, new_count)
        self.assertEqual([], [r for r in state["live"] if r["ref"] == "note-2"])
        self.assertIn("note-2", [r["ref"] for r in state["tombstoned"]])

    def test_replay_from_a_tampered_checkpoint_refuses(self):
        checkpoint = ev.make_checkpoint([U2])
        checkpoint["checkpoint_hash"] = "0" * 64
        with self.assertRaises(ev.FoldError):
            ev.replay_from_checkpoint(checkpoint, [])

    def test_snapshot_carries_a_self_integrity_hash_and_loads_when_clean(self):
        state = ev.fold([U1, TOMB2])
        snapshot = ev.make_snapshot(state)
        self.assertIn("snapshot_hash", snapshot)
        self.assertEqual(state, ev.load_snapshot(snapshot))

    def test_tampered_snapshot_refuses_to_load(self):
        state = ev.fold([U1, TOMB2])
        snapshot = ev.make_snapshot(state)
        snapshot["state"]["tombstoned"] = []  # tamper: quietly un-erase note-2
        with self.assertRaises(ev.FoldError) as cm:
            ev.load_snapshot(snapshot)
        self.assertIn("snapshot_hash mismatch", str(cm.exception))


class ResurrectionProbeTest(unittest.TestCase):
    """The row's own done-check, driven both ways, composed with a REAL physical
    erasure through bm_vault_retention.py's forget-execute (VB3-08) -- not a mock.

    Codex refutation this row exists to close: "a replayable event stream carrying
    note payloads would UNDO the physical erasure shipped tonight the first time a
    projection replays." First test demonstrates exactly that with a payload-carrying
    stream that bypasses the real validator (the shape a pre-VB3-15 or buggy producer
    could have written). Second test replays the identical scenario -- same note, same
    secret, same physical erasure -- under the payload-free protocol and shows every
    projection converges without the erased content.
    """

    SECRET = "Zx7ErasedSecretQm2Marker"
    SUBJECT_ID = "n-3333333333333333"

    @classmethod
    def setUpClass(cls):
        cls.base_tmp = tempfile.mkdtemp(prefix="bm_resurrection_base_")
        cls.base_vault = os.path.join(cls.base_tmp, "vault")
        os.makedirs(cls.base_vault)
        with open(os.path.join(cls.base_vault, "note-doomed.md"), "w",
                 encoding="utf-8") as f:
            f.write("---\nname: note-doomed\ndescription: erased for real by this "
                    "probe\nid: %s\n---\n" % cls.SUBJECT_ID)
            f.write("Body carries the secret: %s\n" % cls.SECRET)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base_tmp, ignore_errors=True)

    def _forget(self, tag, events_records):
        """A fresh copy of the fixture vault, indexed, seeded with events_records
        (written directly to disk -- bypassing ev._validate on purpose when a test
        wants a tainted record), then really forgotten through forget-plan/execute.
        Returns (tmp, vault, events_path). Each test gets its own copy so the two
        scenarios (tainted vs. clean) can never contaminate each other."""
        tmp = tempfile.mkdtemp(prefix="bm_resurrection_%s_" % tag)
        vault = os.path.join(tmp, "vault")
        shutil.copytree(self.base_vault, vault)
        events_dir = os.path.join(vault, ".vault")
        os.makedirs(events_dir, exist_ok=True)
        events_path = os.path.join(events_dir, "events.jsonl")
        with open(events_path, "w", encoding="utf-8") as f:
            for rec in events_records:
                f.write(json.dumps(rec) + "\n")
        env = dict(os.environ)
        env["HOME"] = tmp
        env["BM_VAULT_ROOT"] = vault
        os.makedirs(os.path.join(tmp, ".claude"))
        code, out = run_tool(INDEXER_TOOL, ["index", "--vault", vault], env)
        self.assertEqual(0, code, out)
        self.assertIn("indexed", out)
        plan_out = os.path.join(tmp, "plan.json")
        code, out = run_tool(RETENTION_TOOL,
                             ["forget-plan", "--vault", vault, "--id", self.SUBJECT_ID,
                              "--out", plan_out], env)
        self.assertEqual(0, code, out)
        code, out = run_tool(RETENTION_TOOL,
                             ["forget-execute", "--vault", vault, "--plan", plan_out],
                             env)
        self.assertEqual(0, code, out)
        self.assertIn("applied", out)
        self.assertFalse(os.path.exists(os.path.join(vault, "note-doomed.md")),
                         "sanity: the note file must really be gone before replay")
        return tmp, vault, events_path

    def test_payload_carrying_stream_resurrects_the_erased_secret(self):
        tainted = {"event_key": "k-doomed-1", "kind": "upsert", "ref": self.SUBJECT_ID,
                  "occurred_at": "2026-01-01T00:00:00Z",
                  "recorded_at": "2026-01-01T00:00:00Z",
                  "note_body": "Body carries the secret: %s" % self.SECRET}
        # Sanity: the real validator already refuses this shape; the tainted fixture
        # below is written straight to disk specifically to bypass it, the way a
        # producer that predates (or ignores) VB3-15 could have.
        with self.assertRaises(ev.FoldError):
            ev._validate(dict(tainted), "sanity")

        tmp, vault, events_path = self._forget("payload", [tainted])
        try:
            # A NAIVE projector: no validation, no fold() semantics, just "read the raw
            # line, keep the latest record per ref, surface whatever fields it has" --
            # exactly the shape of consumer the refutation describes. Nothing this
            # estate ships behaves this way; it exists here only to demonstrate why the
            # schema must foreclose the field this projector reads from, structurally.
            def naive_project(path):
                projection = {}
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            rec = json.loads(line)
                            projection[rec["ref"]] = rec
                return projection

            projection = naive_project(events_path)
            self.assertIn(self.SUBJECT_ID, projection)
            self.assertIn(self.SECRET, projection[self.SUBJECT_ID]["note_body"])
            print("RESURRECTION DEMONSTRATED: the physically-erased secret %r "
                 "reappears in a projection replayed from a payload-carrying event "
                 "stream, after a real forget-execute erasure." % self.SECRET)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_payload_free_protocol_denies_the_same_resurrection(self):
        clean = {"event_key": "k-doomed-2", "kind": "upsert", "ref": self.SUBJECT_ID,
                "occurred_at": "2026-01-01T00:00:00Z",
                "recorded_at": "2026-01-01T00:00:00Z"}
        tomb = {"event_key": "k-doomed-2-tomb", "kind": "tombstone",
               "ref": self.SUBJECT_ID, "occurred_at": "2026-01-02T00:00:00Z",
               "recorded_at": "2026-01-02T00:00:00Z"}
        # Every record here is exactly what the shipped writers produce -- unlike the
        # tainted fixture above, both pass the real validator.
        ev._validate(dict(clean), "sanity")
        ev._validate(dict(tomb), "sanity")

        tmp, vault, events_path = self._forget("clean", [clean, tomb])
        try:
            events = ev.load_events([events_path])
            state = ev.fold(events)
            self.assertEqual([], [r for r in state["live"] if r["ref"] == self.SUBJECT_ID])
            self.assertIn(self.SUBJECT_ID, [r["ref"] for r in state["tombstoned"]])

            snapshot = ev.make_snapshot(state)
            checkpoint = ev.make_checkpoint(events)
            with open(events_path, encoding="utf-8") as f:
                raw_stream_text = f.read()
            projections = {
                "fold() state": json.dumps(state),
                "raw validated event stream": raw_stream_text,
                "integrity-hashed snapshot": json.dumps(snapshot),
                "checkpoint bundle": json.dumps(checkpoint),
            }
            for name, projection in projections.items():
                self.assertNotIn(self.SECRET, projection,
                                 "%s must not carry the erased secret" % name)
            print("RESURRECTION DENIED: every projection (%s) converges after replay "
                 "with the erased secret %r absent, under the payload-free protocol."
                 % (", ".join(sorted(projections)), self.SECRET))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TieBreakTest(unittest.TestCase):
    def test_identical_timestamps_break_tie_by_event_key(self):
        # MINOR 1: two distinct corrections to the same upsert share BOTH occurred_at and
        # recorded_at -- only event_key differs. The fold must still pick one winner
        # deterministically (event_key is the final element of the sort tuple), and that
        # winner must not depend on which order the events were handed in.
        u5 = event("k-upsert-5", "upsert", "note-5",
                   "2026-04-01T00:00:00Z", "2026-04-01T00:00:01Z")
        tied_ts = "2026-05-01T00:00:00Z"
        c_a = event("k-correct-aaa", "correct", "note-5", tied_ts, tied_ts,
                    corrects="k-upsert-5")
        c_b = event("k-correct-bbb", "correct", "note-5", tied_ts, tied_ts,
                    corrects="k-upsert-5")
        state1 = ev.fold([u5, c_a, c_b])
        state2 = ev.fold([u5, c_b, c_a])
        self.assertEqual("k-correct-bbb", state1["live"][0]["winning_event_key"])
        self.assertEqual(state1, state2)


class CorrectionChainTest(unittest.TestCase):
    """MAJOR 2: a correction chain that never reaches a live upsert must refuse, not
    silently vanish from fold output. Driven backwards: replace the `if unreached:` block
    in fold() (tools/bm_vault_events.py, after the live loop) with a no-op and both tests
    below fail because the chains fold cleanly to an empty result instead of refusing."""

    def test_two_event_correction_cycle_refuses_naming_both_keys(self):
        a = event("k-cycle-a", "correct", "note-9", "2026-06-01T00:00:00Z",
                  "2026-06-01T00:00:00Z", corrects="k-cycle-b")
        b = event("k-cycle-b", "correct", "note-9", "2026-06-02T00:00:00Z",
                  "2026-06-02T00:00:00Z", corrects="k-cycle-a")
        with self.assertRaises(ev.FoldError) as cm:
            ev.fold([a, b])
        msg = str(cm.exception)
        self.assertIn("CYCLE", msg)
        self.assertIn("k-cycle-a", msg)
        self.assertIn("k-cycle-b", msg)

    def test_orphan_correction_chain_refuses_naming_every_key(self):
        # k-orphan-1 corrects a tombstone, k-orphan-2 corrects k-orphan-1 -- the whole
        # chain never reaches a live upsert.
        tomb = event("k-tomb-9", "tombstone", "note-9",
                     "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z")
        orphan1 = event("k-orphan-1", "correct", "note-9", "2026-06-02T00:00:00Z",
                        "2026-06-02T00:00:00Z", corrects="k-tomb-9")
        orphan2 = event("k-orphan-2", "correct", "note-9", "2026-06-03T00:00:00Z",
                        "2026-06-03T00:00:00Z", corrects="k-orphan-1")
        with self.assertRaises(ev.FoldError) as cm:
            ev.fold([tomb, orphan1, orphan2])
        msg = str(cm.exception)
        self.assertIn("ORPHAN-CHAIN", msg)
        self.assertIn("k-orphan-1", msg)
        self.assertIn("k-orphan-2", msg)


class IdentityKindTest(unittest.TestCase):
    """VB3-17: merged_into and unmerged validated exactly as strictly as corrects,
    and a stream mixing them with ordinary note events still folds cleanly and order
    independently -- these two kinds carry no live/tombstoned state of their own
    (tools/bm_vault_identity.py is the consumer that replays them for identity), so
    fold() must pass them through without raising and without changing the note
    outcome, regardless of where they sit in the stream."""

    def _merged_into(self, ref="entity-a", into="entity-b", rule_version="v1",
                     effective="2026-01-15", event_key="k-merge-1"):
        return {"event_key": event_key, "kind": "merged_into", "ref": ref, "into": into,
                "rule_version": rule_version, "effective": effective,
                "occurred_at": effective, "recorded_at": effective}

    def _unmerged(self, ref="entity-a", into="entity-b", effective="2026-03-01",
                 event_key="k-unmerge-1"):
        return {"event_key": event_key, "kind": "unmerged", "ref": ref, "into": into,
                "effective": effective, "occurred_at": effective, "recorded_at": effective}

    def test_valid_merged_into_and_unmerged_pass_validation(self):
        ev._validate(self._merged_into(), "test")
        ev._validate(self._unmerged(), "test")

    def test_merged_into_missing_into_is_malformed(self):
        bad = self._merged_into()
        del bad["into"]
        with self.assertRaises(ev.FoldError):
            ev._validate(bad, "test")

    def test_merged_into_missing_rule_version_is_malformed(self):
        bad = self._merged_into()
        del bad["rule_version"]
        with self.assertRaises(ev.FoldError):
            ev._validate(bad, "test")

    def test_merged_into_missing_effective_is_malformed(self):
        bad = self._merged_into()
        del bad["effective"]
        with self.assertRaises(ev.FoldError):
            ev._validate(bad, "test")

    def test_merged_into_with_corrects_is_malformed(self):
        bad = self._merged_into()
        bad["corrects"] = "k-upsert-1"
        with self.assertRaises(ev.FoldError):
            ev._validate(bad, "test")

    def test_unmerged_missing_into_is_malformed(self):
        bad = self._unmerged()
        del bad["into"]
        with self.assertRaises(ev.FoldError):
            ev._validate(bad, "test")

    def test_unmerged_missing_effective_is_malformed(self):
        bad = self._unmerged()
        del bad["effective"]
        with self.assertRaises(ev.FoldError):
            ev._validate(bad, "test")

    def test_unmerged_rule_version_is_optional_but_must_be_a_string_when_present(self):
        ok = self._unmerged()
        ok["rule_version"] = "v1"
        ev._validate(ok, "test")  # must not raise
        bad = self._unmerged()
        bad["rule_version"] = ""
        with self.assertRaises(ev.FoldError):
            ev._validate(bad, "test")

    def test_ordinary_kinds_refuse_the_merge_only_fields(self):
        for field, value in (("into", "x"), ("rule_version", "v1"), ("effective", "2026-01-01")):
            bad = event("k-upsert-x", "upsert", "note-x",
                        "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
            bad[field] = value
            with self.assertRaises(ev.FoldError):
                ev._validate(bad, "test")

    def test_stream_mixing_identity_and_note_kinds_folds_cleanly_and_order_independently(self):
        u = event("k-upsert-9", "upsert", "note-9",
                  "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z")
        m = self._merged_into()
        um = self._unmerged()
        expected = ev.fold([u, m, um])
        for seed in FIXED_SEEDS:
            mixed = [u, m, um]
            random.Random(seed).shuffle(mixed)
            self.assertEqual(expected, ev.fold(mixed))
        self.assertEqual(1, len(expected["live"]))
        self.assertEqual("note-9", expected["live"][0]["ref"])
        self.assertEqual([], expected["tombstoned"])


class IdentityStreamSharedValidatorTest(unittest.TestCase):
    """VB3-15 unification note: bm_vault_identity.py's .identity/events.jsonl stream is
    validated through THIS module's own _validate/parse_lines (import, not copy) --
    a payload-bearing merged_into record is refused by the identity stream reader
    exactly as strictly as an ordinary note event would be, proving the wiring is
    live rather than merely imported and unused."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm_identity_shared_")
        import bm_vault_identity as idmod  # sys.path already carries HERE, top of file
        self.idmod = idmod

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_payload_bearing_identity_event_is_refused_by_the_shared_validator(self):
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(vault, ".identity"))
        bad = {"event_key": "k-merge-bad", "kind": "merged_into", "ref": "entity-a",
              "into": "entity-b", "rule_version": "v1", "effective": "2026-01-15",
              "occurred_at": "2026-01-15", "recorded_at": "2026-01-15",
              "note_body": "leaked content riding on an identity event"}
        with open(os.path.join(vault, ".identity", "events.jsonl"), "w",
                 encoding="utf-8") as f:
            f.write(json.dumps(bad) + "\n")
        with self.assertRaises(ev.FoldError) as cm:
            self.idmod.load_identity_events(vault, ev)
        self.assertIn("note_body", str(cm.exception))

    def test_clean_identity_events_load_through_the_shared_validator(self):
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(vault, ".identity"))
        good = {"event_key": "k-merge-ok", "kind": "merged_into", "ref": "entity-a",
               "into": "entity-b", "rule_version": "v1", "effective": "2026-01-15",
               "occurred_at": "2026-01-15", "recorded_at": "2026-01-15"}
        with open(os.path.join(vault, ".identity", "events.jsonl"), "w",
                 encoding="utf-8") as f:
            f.write(json.dumps(good) + "\n")
        events = self.idmod.load_identity_events(vault, ev)
        self.assertEqual(1, len(events))

    def test_absent_identity_store_is_an_empty_history_not_an_error(self):
        # NO-DATA is for a store that exists and is malformed (test above); a vault
        # that has never had a merge is a legitimate empty history, not a finding --
        # matches bm_vault_identity.load_identity_events' own documented contract.
        vault = os.path.join(self.tmp, "vault")
        os.makedirs(vault)
        self.assertEqual([], self.idmod.load_identity_events(vault, ev))


class CliTest(unittest.TestCase):
    """The `replay FILE...` command end to end, over real files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bm_events_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, records):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        return path

    def test_replay_over_two_files_converges_and_exits_zero(self):
        f1 = self._write("a.jsonl", [U1, C_EARLY, TOMB2])
        f2 = self._write("b.jsonl", [C_LATE, U1_DUP, U2, U2_REEMIT])
        code, out = run(["replay", f1, f2])
        self.assertEqual(0, code, out)
        self.assertIn("live: 1", out)
        self.assertIn("winner=k-correct-late", out)
        self.assertIn("tombstoned: 1", out)
        self.assertIn("ref=note-2", out)

    def test_malformed_json_line_is_no_data_naming_the_line(self):
        path = os.path.join(self.tmp, "bad.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(U1) + "\n")
            f.write("{not valid json\n")
        code, out = run(["replay", path])
        self.assertEqual(2, code, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("%s:2" % path, out)

    def test_replay_with_no_files_is_no_data(self):
        code, out = run(["replay"])
        self.assertEqual(2, code, out)

    def test_replay_of_missing_file_is_no_data(self):
        code, out = run(["replay", os.path.join(self.tmp, "nope.jsonl")])
        self.assertEqual(2, code, out)
        self.assertIn("NO-DATA", out)

    def test_replay_with_extra_field_is_no_data_naming_the_key(self):
        rec = dict(U1)
        rec["payload"] = "leaked content"
        path = self._write("extra.jsonl", [rec])
        code, out = run(["replay", path])
        self.assertEqual(2, code, out)
        self.assertIn("NO-DATA", out)
        self.assertIn("payload", out)


if __name__ == "__main__":
    unittest.main()
