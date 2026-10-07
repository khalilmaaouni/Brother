#!/usr/bin/env python3
"""RL3.b: classify_kind, entry_from_note and supersede on a temporary vault.

Every note is written to a temporary vault as a frontmatter file and read back
from disk, so the entries under test are the ones a real vault walk produces.
No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(HERE, "..", "..", "..", "scripts"))
import bm_context_authority as CA  # noqa: E402
import bm_vault_principals  # noqa: E402
import bm_vault_retention  # noqa: E402
import context_capsule  # noqa: E402

TODAY = date(2026, 10, 2)
SCOPE = {"account": "acct-1", "project": "/work/proj-a"}


def note(body="a line", **front):
    lines = ["---"]
    for key, value in front.items():
        lines.append("%s: %s" % (key, value))
    lines.append("---")
    lines.append(body)
    return "\n".join(lines) + "\n"


class Vault(object):
    """A temporary vault: write notes, read them back into entries."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="rl3b-")

    def write(self, rel, text):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return rel

    def entry(self, rel, text):
        self.write(rel, text)
        with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
            return CA.entry_from_note(rel, fh.read(), TODAY)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def pref(source, subject="editor", observed="2026-09-01",
         authority="casual", corrects=None, kind="explicit_preference"):
    return {"snippet": "s", "source": source, "authority": authority,
            "kind": kind, "scope": dict(SCOPE), "observed_at": observed,
            "revoked": False, "authoritative": False, "subject": subject,
            "corrects": corrects}


class Base(unittest.TestCase):
    def setUp(self):
        self.vault = Vault()
        self.addCleanup(self.vault.close)


class ClassifyKind(Base):
    def test_mirrors_context_capsule_vocabulary(self):
        self.assertEqual(CA.KINDS, context_capsule.KINDS)
        self.assertEqual(CA.ENTRY_KEYS, context_capsule.ENTRY_KEYS)

    def test_each_declared_kind(self):
        for kind in CA.KINDS:
            self.assertEqual(CA.classify_kind(note(kind=kind)), kind)
        self.assertEqual(CA.classify_kind(note(kind='"explicit_preference"')),
                         "explicit_preference")

    def test_absent_kind_is_observation(self):
        self.assertEqual(CA.classify_kind(note(subject="x")), "observation")
        self.assertEqual(CA.classify_kind("no frontmatter at all"),
                         "observation")

    def test_prose_preference_without_key_is_observation(self):
        text = note("I prefer tabs. I always prefer short answers. "
                    "Preference: dark mode.", subject="editor")
        self.assertEqual(CA.classify_kind(text), "observation")
        entry, _ = self.vault.entry("prose.md", text)
        self.assertEqual(entry["kind"], "observation")

    def test_unknown_kind_raises_naming_it(self):
        with self.assertRaises(ValueError) as ctx:
            CA.classify_kind(note(kind="preference"))
        self.assertIn("preference", str(ctx.exception))
        with self.assertRaises(ValueError):
            CA.classify_kind(note(kind=""))

    def test_hostile_input_refused(self):
        for bad in (None, 3, True, float("nan"), ["kind: observation"],
                    {"kind": "observation"}, b"---\nkind: observation\n---"):
            with self.assertRaises(ValueError):
                CA.classify_kind(bad)


class EntryFromNote(Base):
    def test_full_note_is_a_tagged_entry_context_capsule_admits(self):
        entry, findings = self.vault.entry("Prefs/editor.md", note(
            "Use tabs.", kind="explicit_preference",
            authority="source_of_record", verified_at="2026-09-30",
            account="acct-1", project="/work/proj-a", subject="editor"))
        self.assertEqual(findings, [])
        for key in context_capsule.ENTRY_KEYS:
            self.assertIn(key, entry)
        self.assertEqual(entry["source"], "Prefs/editor.md")
        self.assertEqual(entry["snippet"], "Use tabs.")
        self.assertEqual(entry["authority"], "source_of_record")
        self.assertEqual(entry["kind"], "explicit_preference")
        self.assertEqual(entry["observed_at"], "2026-09-30")
        self.assertEqual(entry["scope"], SCOPE)
        self.assertIs(entry["revoked"], False)
        self.assertIs(entry["authoritative"], False)
        self.assertEqual(entry["subject"], "editor")
        kept, withheld = context_capsule.admit_context([entry], SCOPE, TODAY)
        self.assertEqual((len(kept), withheld), (1, []))

    def test_inference_claiming_record_is_demoted(self):
        entry, findings = self.vault.entry("Infer/guess.md", note(
            "They probably like spaces.", kind="inferred_hypothesis",
            authority="source_of_record", verified_at="2026-09-30",
            subject="editor"))
        self.assertEqual(entry["kind"], "inferred_hypothesis")
        self.assertEqual(entry["authority"], "casual")
        self.assertTrue(any(CA.INFERENCE_DEMOTED in f for f in findings),
                        findings)

    def test_preference_keeps_record_authority(self):
        entry, findings = self.vault.entry("p.md", note(
            kind="explicit_preference", authority="source_of_record"))
        self.assertEqual(entry["authority"], "source_of_record")
        self.assertEqual(findings, [])

    def test_unknown_authority_is_a_finding_and_falls_to_casual(self):
        entry, findings = self.vault.entry("p.md", note(
            kind="explicit_preference", authority="supreme"))
        self.assertEqual(entry["authority"], "casual")
        self.assertEqual(len(findings), 1)
        self.assertIn("supreme", findings[0])

    def test_missing_or_sentinel_date_is_none_and_withheld_as_stale(self):
        for front in ({}, {"verified_at": "no-derivable-date"},
                      {"verified_at": "yesterday"}):
            entry, findings = self.vault.entry("d.md", note(
                account="acct-1", project="/work/proj-a", **front))
            self.assertIsNone(entry["observed_at"])
            _, withheld = context_capsule.admit_context([entry], SCOPE, TODAY)
            self.assertEqual(withheld, [{"source": "d.md", "reason": "stale"}])
        self.assertEqual(len(findings), 1)  # the unparseable one says so

    def test_missing_scope_key_leaves_scope_none_withheld_cross_scope(self):
        for front in ({"account": "acct-1"}, {"project": "/work/proj-a"}, {}):
            entry, _ = self.vault.entry("s.md", note(
                verified_at="2026-10-01", **front))
            self.assertIsNone(entry["scope"])
            _, withheld = context_capsule.admit_context([entry], SCOPE, TODAY)
            self.assertEqual(withheld[0]["reason"], "cross-scope")

    def test_frontmatter_without_kind_is_observation(self):
        entry, _ = self.vault.entry("o.md", note(subject="editor",
                                                 authority="derived"))
        self.assertEqual(entry["kind"], "observation")
        self.assertEqual(entry["authority"], "derived")

    def test_unknown_kind_blocks(self):
        with self.assertRaises(ValueError):
            self.vault.entry("k.md", note(kind="hunch"))

    def test_hostile_arguments_refused(self):
        good = note(kind="observation")
        for args in ((None, good, TODAY), ("", good, TODAY),
                     (["a.md"], good, TODAY), ("a.md", None, TODAY),
                     ("a.md", ["x"], TODAY), ("a.md", good, "2026-10-02"),
                     ("a.md", good, datetime(2026, 10, 2)),
                     ("a.md", good, None), ("a.md", good, float("nan"))):
            with self.assertRaises(ValueError):
                CA.entry_from_note(*args)


class Supersede(Base):
    def test_empty(self):
        self.assertEqual(CA.supersede([]), ([], []))

    def test_exactly_one_preference(self):
        e = pref("a.md")
        self.assertEqual(CA.supersede([e]), ([e], []))

    def test_correction_retires_older_one_active_per_corrected_subject(self):
        old, _ = self.vault.entry("Prefs/old.md", note(
            "tabs", kind="explicit_preference", subject="editor",
            verified_at="2026-09-01", authority="source_of_record"))
        fix, _ = self.vault.entry("Prefs/fix.md", note(
            "spaces", kind="explicit_preference", subject="editor",
            verified_at="2026-09-20", corrects="Prefs/old.md"))
        active, retired = CA.supersede([old, fix])
        self.assertEqual([e["source"] for e in active
                          if e["subject"] == "editor"], ["Prefs/fix.md"])
        self.assertEqual(retired, [{"source": "Prefs/old.md",
                                    "retired_by": "Prefs/fix.md",
                                    "reason": "corrected"}])

    def test_correction_wins_whatever_the_dates_and_authority(self):
        target = pref("t.md", observed="2026-09-30",
                      authority="source_of_record")
        fix = pref("c.md", observed="2026-08-01", corrects="t.md")
        active, retired = CA.supersede([target, fix])
        self.assertEqual([e["source"] for e in active], ["c.md"])
        self.assertEqual(retired[0]["retired_by"], "c.md")

    def test_inference_never_retires_a_preference(self):
        p, _ = self.vault.entry("Prefs/p.md", note(
            kind="explicit_preference", subject="editor",
            verified_at="2026-09-01"))
        guess, _ = self.vault.entry("Infer/g.md", note(
            kind="inferred_hypothesis", subject="editor",
            authority="source_of_record", verified_at="2026-09-30",
            corrects="Prefs/p.md"))
        active, retired = CA.supersede([p, guess])
        self.assertEqual(retired, [])
        self.assertEqual(sorted(e["source"] for e in active),
                         ["Infer/g.md", "Prefs/p.md"])

    def test_observation_and_inference_never_retired(self):
        obs = pref("o.md", observed="2026-01-01", kind="observation")
        inf = pref("i.md", observed="2026-01-01", kind="inferred_hypothesis")
        newer = pref("p.md", observed="2026-09-30",
                     authority="source_of_record", corrects="o.md")
        active, retired = CA.supersede([obs, inf, newer])
        self.assertEqual(retired, [])
        self.assertEqual(len(active), 3)

    def test_newer_with_authority_not_below_retires_older(self):
        old = pref("a.md", observed="2026-09-01", authority="derived")
        new = pref("b.md", observed="2026-09-02", authority="derived")
        active, retired = CA.supersede([old, new])
        self.assertEqual([e["source"] for e in active], ["b.md"])
        self.assertEqual(retired, [{"source": "a.md", "retired_by": "b.md",
                                    "reason": "newer"}])

    def test_newer_with_lower_authority_does_not_retire(self):
        old = pref("a.md", observed="2026-09-01", authority="source_of_record")
        new = pref("b.md", observed="2026-09-02", authority="casual")
        self.assertEqual(CA.supersede([old, new])[1], [])

    def test_equal_pair_is_unresolved_on_both_sides(self):
        a = pref("a.md")
        b = pref("b.md")
        active, retired = CA.supersede([a, b])
        self.assertEqual(active, [])
        self.assertEqual(retired, [
            {"source": "a.md", "retired_by": "b.md", "reason": "unresolved"},
            {"source": "b.md", "retired_by": "a.md", "reason": "unresolved"}])

    def test_different_subjects_never_compete(self):
        entries = [pref("a.md", subject="editor"),
                   pref("b.md", subject="shell"),
                   pref("c.md", subject="tone", observed="2026-09-03"),
                   pref("d.md", subject="tone", observed="2026-09-04"),
                   pref("e.md", subject=None)]
        active, retired = CA.supersede(entries)
        self.assertEqual([e["source"] for e in active],
                         ["e.md", "a.md", "b.md", "d.md"])
        self.assertEqual(retired, [{"source": "c.md", "retired_by": "d.md",
                                    "reason": "newer"}])

    def test_dangling_corrects_retires_nothing_and_is_a_finding(self):
        a = pref("a.md", observed="2026-09-01")
        b = pref("b.md", observed="2026-09-01", authority="derived",
                 corrects="gone.md")
        active, retired = CA.supersede([a, b])
        self.assertEqual(retired, [])
        findings = CA.dangling_corrections([a, b])
        self.assertEqual(len(findings), 1)
        self.assertIn("gone.md", findings[0])
        self.assertEqual(CA.dangling_corrections(
            [a, pref("c.md", corrects="a.md")]), [])

    def test_stale_older_correction_and_newer_plain_target(self):
        fix = pref("c.md", observed="2026-08-01", corrects="t.md")
        target = pref("t.md", observed="2026-09-30")
        active, retired = CA.supersede([target, fix])
        self.assertEqual([e["source"] for e in active], ["c.md"])
        self.assertEqual(retired, [{"source": "t.md", "retired_by": "c.md",
                                    "reason": "corrected"}])

    def test_same_principal_corrects_own_preference_without_climb(self):
        mine = pref("me/1.md", observed="2026-09-01", authority="derived")
        again = pref("me/2.md", observed="2026-09-02", authority="casual",
                     corrects="me/1.md")
        active, retired = CA.supersede([mine, again])
        self.assertEqual([e["source"] for e in active], ["me/2.md"])
        self.assertEqual(retired[0]["reason"], "corrected")

    def test_idempotent_and_never_retired_twice(self):
        old = pref("a.md", observed="2026-09-01")
        fix = pref("b.md", observed="2026-09-02", corrects="a.md")
        third = pref("c.md", observed="2026-09-03", corrects="a.md")
        entries = [third, old, fix, old]
        first = CA.supersede(entries)
        self.assertEqual(first, CA.supersede(list(reversed(entries))))
        self.assertEqual([r["source"] for r in first[1]].count("a.md"), 1)
        again = CA.supersede(first[0])
        self.assertEqual(again[0], first[0])

    def test_pure_does_not_mutate_input(self):
        entries = [pref("a.md"), pref("b.md", corrects="a.md")]
        snapshot = [dict(e) for e in entries]
        CA.supersede(entries)
        self.assertEqual(entries, snapshot)

    def test_hostile_entries_refused(self):
        bad_lists = ("not a list", None, {"source": "a.md"}, [None], [3],
                     [pref("a.md", kind="hunch")],
                     [pref("a.md", authority="supreme")],
                     [pref("a.md", authority=["casual"])],
                     [pref(None)], [pref(True)], [pref("")],
                     [pref("a.md", subject=1)], [pref("a.md", corrects=True)],
                     [pref("a.md", observed=float("nan"))],
                     [pref("a.md", observed="soon")],
                     [pref("a.md"), pref("a.md", subject="other")])
        for bad in bad_lists:
            with self.assertRaises(ValueError):
                CA.supersede(bad)


def principal_rec(status):
    return {"kind": "human", "status": status, "added_at": "2026-09-01",
            "added_by": "owner", "recorded_at": "2026-09-01",
            "recorded_by": "owner"}


def scoped(source, **over):
    e = context_capsule.tag_entry("s", source, scope=dict(SCOPE),
                                  observed_at=TODAY.isoformat())
    e.update(over)
    return e


class Revocation(Base):
    """RL3.c: revoked_sources, apply_revocation, then admit_context."""

    def registry(self, principals=None, raw=None):
        path = os.path.join(self.vault.root, bm_vault_principals.REGISTRY_RELPATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(raw if raw is not None
                     else json.dumps({"principals": principals}))
        return path

    def request(self, path, entries, deleted=frozenset()):
        """One request: the registry is read fresh, then admit_context."""
        revoked, problem = CA.revoked_sources(path)
        marked_entries, marked = CA.apply_revocation(entries, revoked,
                                                     set(deleted), problem)
        kept, withheld = context_capsule.admit_context(marked_entries, SCOPE,
                                                       TODAY)
        return [e["source"] for e in kept], withheld, marked

    def test_one_revoked_one_active(self):
        path = self.registry({"mallory": principal_rec("revoked"),
                              "alice": principal_rec("active")})
        self.assertEqual(CA.revoked_sources(path), ({"mallory"}, None))
        entries = [scoped("mallory:Prefs/a.md"), scoped("alice:Prefs/b.md"),
                   scoped("Prefs/c.md", by="Mallory"), scoped("Prefs/d.md")]
        kept, withheld, marked = self.request(path, entries)
        self.assertEqual(kept, ["alice:Prefs/b.md", "Prefs/d.md"])
        self.assertEqual(withheld, [
            {"source": "mallory:Prefs/a.md", "reason": "revoked"},
            {"source": "Prefs/c.md", "reason": "revoked"}])
        self.assertEqual([m["reason"] for m in marked],
                         [CA.REASON_REVOKED, CA.REASON_REVOKED])

    def test_empty_registry_revokes_nothing(self):
        path = self.registry({})
        self.assertEqual(CA.revoked_sources(path), (set(), None))
        kept, withheld, marked = self.request(path, [scoped("alice:a.md")])
        self.assertEqual((kept, withheld, marked), (["alice:a.md"], [], []))

    def test_many_revoked(self):
        path = self.registry(dict(("p%d" % i, principal_rec("revoked"))
                                  for i in range(5)))
        revoked, problem = CA.revoked_sources(path)
        self.assertIsNone(problem)
        self.assertEqual(revoked, set("p%d" % i for i in range(5)))

    def test_tamper_suspect_active_reads_revoked(self):
        rec = principal_rec("active")
        del rec["recorded_by"]
        path = self.registry({"eve": rec})
        self.assertEqual(CA.revoked_sources(path), ({"eve"}, None))

    def test_corrupt_registry_withholds_every_principal_sourced_entry(self):
        path = self.registry(raw="{not json")
        revoked, problem = CA.revoked_sources(path)
        self.assertEqual(revoked, set())
        self.assertTrue(problem.startswith("NO-DATA: "), problem)
        entries = [scoped("alice:Prefs/b.md"), scoped("Prefs/c.md", by="bob"),
                   scoped("Prefs/d.md")]
        kept, withheld, marked = self.request(path, entries)
        self.assertEqual(kept, ["Prefs/d.md"])
        self.assertEqual([w["reason"] for w in withheld], ["revoked", "revoked"])
        self.assertEqual([m["reason"] for m in marked],
                         [CA.REASON_REGISTRY, CA.REASON_REGISTRY])

    def test_unreadable_registry_shapes_are_no_data(self):
        for raw in ("[]", json.dumps({"principals": []}),
                    json.dumps({"principals": {"a": "revoked"}}),
                    json.dumps({"principals": {"a": principal_rec("gone")}})):
            revoked, problem = CA.revoked_sources(self.registry(raw=raw))
            self.assertEqual(revoked, set())
            self.assertIsNotNone(problem, raw)
        missing = os.path.join(self.vault.root, "absent.json")
        self.assertEqual(CA.revoked_sources(missing)[0], set())
        self.assertIsNotNone(CA.revoked_sources(missing)[1])
        for bad in (None, "", 3, True, ["p.json"], float("nan")):
            revoked, problem = CA.revoked_sources(bad)
            self.assertEqual(revoked, set())
            self.assertTrue(problem.startswith("NO-DATA"))

    def test_revocation_takes_effect_on_next_request_and_reactivation(self):
        path = self.registry({"alice": principal_rec("active")})
        entries = [scoped("alice:Prefs/a.md")]
        self.assertEqual(self.request(path, entries)[0], ["alice:Prefs/a.md"])
        self.registry({"alice": principal_rec("revoked")})
        self.assertEqual(self.request(path, entries)[0], [])
        self.registry({"alice": principal_rec("active")})
        self.assertEqual(self.request(path, entries)[0], ["alice:Prefs/a.md"])

    def test_same_name_stays_revoked_under_another_spelling(self):
        path = self.registry({"alice": principal_rec("revoked")})
        for name in ("Alice", " alice ", "ALICE"):
            kept, _, _ = self.request(path, [scoped("Prefs/a.md", by=name)])
            self.assertEqual(kept, [])

    def test_deleted_path_is_withheld_and_unknown_deleted_path_marks_nothing(self):
        path = self.registry({})
        entries = [scoped("Prefs/a.md"), scoped("alice:Prefs/b.md")]
        kept, withheld, marked = self.request(
            path, entries, deleted={"Prefs/a.md", "Prefs/b.md"})
        self.assertEqual(kept, [])
        self.assertEqual([m["reason"] for m in marked],
                         [CA.REASON_DELETED, CA.REASON_DELETED])
        kept, withheld, marked = self.request(path, entries,
                                              deleted={"Gone/x.md"})
        self.assertEqual((len(kept), withheld, marked), (2, [], []))

    def test_apply_revocation_does_not_filter_or_mutate_input(self):
        entries = [scoped("mallory:a.md"), "not an entry"]
        snapshot = [dict(entries[0]), entries[1]]
        out, marked = CA.apply_revocation(entries, {"mallory"}, set(), None)
        self.assertEqual(len(out), 2)
        self.assertIs(out[0]["revoked"], True)
        self.assertEqual(out[1], "not an entry")
        self.assertEqual(entries, snapshot)
        self.assertEqual(marked, [{"source": "mallory:a.md",
                                   "reason": CA.REASON_REVOKED}])

    def test_unreadable_principal_is_marked(self):
        out, marked = CA.apply_revocation([scoped("a.md", by=["alice"])],
                                          set(), set(), None)
        self.assertIs(out[0]["revoked"], True)
        self.assertEqual(marked[0]["reason"], CA.REASON_UNREADABLE_BY)

    def test_hostile_arguments_refused(self):
        good = [scoped("a.md")]
        for args in (("x", set(), set(), None), (None, set(), set(), None),
                     (good, ["alice"], set(), None), (good, "alice", set(), None),
                     (good, {1}, set(), None), (good, None, set(), None),
                     (good, set(), ["a.md"], None), (good, set(), {None}, None),
                     (good, set(), set(), True), (good, set(), set(), 3),
                     (good, set(), set(), float("nan"))):
            with self.assertRaises(ValueError):
                CA.apply_revocation(*args)


class StubCursor(object):
    def fetchone(self):
        return None


class StubIndex(object):
    """A retrieval index connection holding no rows: the note is not indexed."""

    def __init__(self):
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        return StubCursor()


class ForgetPlans(Base):
    def test_one_plan_per_path_in_order_with_no_data_for_a_raising_path(self):
        self.vault.write("Notes/a.md", note("alpha"))
        self.vault.write("Notes/b.md", note("beta"))
        sib = bm_vault_retention._forget_siblings()
        con = StubIndex()
        plans = CA.forget_plans(self.vault.root, ["Notes/b.md", "Gone/x.md",
                                                  "Notes/a.md"], sib, con)
        self.assertEqual(len(plans), 3)
        self.assertEqual(plans[0]["note"]["rel_path"], "Notes/b.md")
        self.assertEqual(plans[2]["note"]["rel_path"], "Notes/a.md")
        self.assertEqual(plans[1]["rel_path"], "Gone/x.md")
        self.assertEqual(plans[1]["status"], "NO-DATA")
        self.assertIn("FileNotFoundError", plans[1]["reason"])
        index = [c for c in plans[0]["classes"] if c["name"] == "index"][0]
        self.assertEqual(index["count"], 0)  # honest zero, not indexed
        self.assertTrue(os.path.isfile(os.path.join(self.vault.root,
                                                    "Notes/a.md")))
        self.assertTrue(all(q.lstrip().upper().startswith("SELECT")
                            for q in con.queries))

    def test_path_leaving_the_vault_or_unreadable_is_no_data(self):
        sib = bm_vault_retention._forget_siblings()
        plans = CA.forget_plans(self.vault.root, ["../x.md", "/etc/hosts",
                                                  None, 3, ""], sib, StubIndex())
        self.assertEqual([p["status"] for p in plans], ["NO-DATA"] * 5)

    def test_empty_list_is_empty(self):
        self.assertEqual(CA.forget_plans(self.vault.root, [], {}, StubIndex()),
                         [])

    def test_hostile_arguments_refused(self):
        con = StubIndex()
        for args in ((self.vault.root, "Notes/a.md", {}, con),
                     (self.vault.root, None, {}, con),
                     (None, [], {}, con), ("", [], {}, con),
                     (self.vault.root, [], None, con),
                     (self.vault.root, [], [], con),
                     (self.vault.root, [], {}, None)):
            with self.assertRaises(ValueError):
                CA.forget_plans(*args)


if __name__ == "__main__":
    unittest.main()
