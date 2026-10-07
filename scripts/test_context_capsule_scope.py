"""RL3.a: source, authority, scope, freshness and revocation on context.

Proves context_capsule's ENTRY_KEYS, context_scope(), tag_entry(),
admit_context() and build_capsule()'s scope keyword. The last class re-runs
the fifteen inputs of test_context_capsule.py with scope=None and pins the
capsule_hash each produced BEFORE this slice existed, so the None path is
shown byte identical, not merely similar.
"""
import copy
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import context_capsule as C  # noqa: E402

TODAY = date(2026, 10, 2)
SCOPE = {"account": "acct-1", "project": "/work/proj-a"}


def entry(**over):
    """A tagged entry that passes every test, with fields overridden."""
    base = C.tag_entry("a lesson", "note-a", scope=dict(SCOPE),
                       observed_at=TODAY.isoformat())
    base.update(over)
    return base


def write(cwd, rel, body):
    full = os.path.join(cwd, rel)
    os.makedirs(os.path.dirname(full) or cwd, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(body)


class TagEntry(unittest.TestCase):
    def test_every_entry_key_present_and_never_authoritative(self):
        e = C.tag_entry("s", "src", authority="source_of_record",
                        kind="explicit_preference", scope=SCOPE,
                        observed_at="2026-10-01")
        self.assertEqual(set(e), set(C.ENTRY_KEYS))
        self.assertIs(e["authoritative"], False)
        self.assertEqual(e["authority"], "source_of_record")
        self.assertEqual(e["scope"], SCOPE)
        self.assertIs(e["revoked"], False)

    def test_defaults(self):
        e = C.tag_entry("s", "src")
        self.assertEqual((e["authority"], e["kind"], e["scope"],
                          e["observed_at"], e["revoked"]),
                         ("casual", "observation", None, None, False))

    def test_authority_levels_mirror_vault_authority(self):
        self.assertEqual(C.AUTHORITY_LEVELS,
                         ("casual", "derived", "source_of_record"))

    def test_unknown_authority_raises_naming_value(self):
        with self.assertRaises(ValueError) as cm:
            C.tag_entry("s", "src", authority="gospel")
        self.assertIn("gospel", str(cm.exception))

    def test_unknown_kind_raises_naming_value(self):
        with self.assertRaises(ValueError) as cm:
            C.tag_entry("s", "src", kind="rumour")
        self.assertIn("rumour", str(cm.exception))

    def test_snippet_is_not_truncated_here(self):
        self.assertEqual(len(C.tag_entry("x" * 10000, "src")["snippet"]),
                         10000)

    def test_hostile_inputs_are_refused(self):
        bad = [dict(snippet=None), dict(snippet=["a"]), dict(source=3),
               dict(authority=["casual"]), dict(authority=None),
               dict(kind={"observation": 1}), dict(kind=True),
               dict(scope="acct-1"), dict(scope=["a"]),
               dict(observed_at=20261001), dict(observed_at=float("nan")),
               dict(revoked="yes"), dict(revoked=1), dict(revoked=None)]
        for over in bad:
            kw = dict(snippet="s", source="src")
            kw.update(over)
            with self.subTest(over=over):
                with self.assertRaises(ValueError):
                    C.tag_entry(**kw)


class ContextScope(unittest.TestCase):
    def test_empty_account_stays_empty_and_project_is_cwd_realpath(self):
        cwd = tempfile.mkdtemp()
        self.assertEqual(C.context_scope({"id": "u1"}, cwd=cwd),
                         {"account": "", "project": os.path.realpath(cwd)})

    def test_node_values_win(self):
        self.assertEqual(
            C.context_scope({"account": "a", "project": "p"}, cwd="/x"),
            {"account": "a", "project": "p"})

    def test_hostile_node_is_refused(self):
        for node in (None, "u1", ["account"], {"account": 5},
                     {"project": ["p"]}, {"account": True}):
            with self.subTest(node=node):
                with self.assertRaises(ValueError):
                    C.context_scope(node, cwd="/x")


class AdmitContext(unittest.TestCase):
    def admit(self, entries, **kw):
        return C.admit_context(entries, dict(SCOPE), TODAY, **kw)

    def assertWithheld(self, e, reason, **kw):
        kept, withheld = self.admit([e], **kw)
        self.assertEqual(kept, [])
        self.assertEqual(withheld, [{"source": e.get("source", "")
                                     if isinstance(e, dict) else "",
                                     "reason": reason}])

    def test_one_per_reason_and_one_that_passes(self):
        good = entry(source="ok")
        entries = [entry(source="m", authoritative=True),
                   entry(source="r", revoked=True),
                   entry(source="x", scope={"account": "acct-1",
                                            "project": "/work/proj-b"}),
                   entry(source="s", observed_at="2020-01-01"),
                   good]
        kept, withheld = self.admit(entries)
        self.assertEqual(kept, [good])
        self.assertEqual(withheld, [
            {"source": "m", "reason": "malformed"},
            {"source": "r", "reason": "revoked"},
            {"source": "x", "reason": "cross-scope"},
            {"source": "s", "reason": "stale"}])

    def test_empty(self):
        self.assertEqual(self.admit([]), ([], []))

    def test_exactly_one(self):
        e = entry()
        self.assertEqual(self.admit([e]), ([e], []))

    def test_many_order_preserved(self):
        es = [entry(source="n%d" % i) for i in range(6)]
        es[2]["revoked"] = True
        kept, withheld = self.admit(es)
        self.assertEqual([e["source"] for e in kept],
                         ["n0", "n1", "n3", "n4", "n5"])
        self.assertEqual(withheld, [{"source": "n2", "reason": "revoked"}])

    def test_extra_key_is_kept(self):
        e = entry(extra="fine")
        self.assertEqual(self.admit([e]), ([e], []))

    def test_each_missing_key_is_malformed(self):
        for key in C.ENTRY_KEYS:
            e = entry()
            del e[key]
            with self.subTest(missing=key):
                kept, withheld = self.admit([e])
                self.assertEqual(kept, [])
                self.assertEqual(withheld[0]["reason"], "malformed")

    def test_authoritative_must_be_exactly_false(self):
        for value in (True, 0, None, "False", []):
            with self.subTest(value=value):
                self.assertWithheld(entry(authoritative=value), "malformed")

    def test_malformed_beats_revoked_beats_cross_scope_beats_stale(self):
        self.assertWithheld(entry(authoritative=True, revoked=True),
                            "malformed")
        self.assertWithheld(entry(revoked=True, scope=None,
                                  observed_at="bad"), "revoked")
        self.assertWithheld(entry(scope=None, observed_at="bad"),
                            "cross-scope")

    def test_other_project_is_cross_scope(self):
        self.assertWithheld(entry(scope={"account": "acct-1",
                                         "project": "/work/proj-b"}),
                            "cross-scope")

    def test_other_account_is_cross_scope(self):
        self.assertWithheld(entry(scope={"account": "acct-2",
                                         "project": "/work/proj-a"}),
                            "cross-scope")

    def test_scopeless_entry_is_cross_scope_not_a_wildcard(self):
        self.assertWithheld(entry(scope=None), "cross-scope")
        self.assertWithheld(entry(scope={}), "cross-scope")
        self.assertWithheld(entry(scope={"project": "/work/proj-a"}),
                            "cross-scope")

    def test_empty_account_matches_only_empty_account(self):
        req = {"account": "", "project": "/p"}
        empty = entry(scope={"account": "", "project": "/p"})
        other = entry(scope={"account": "acct-1", "project": "/p"})
        kept, withheld = C.admit_context([empty, other], req, TODAY)
        self.assertEqual(kept, [empty])
        self.assertEqual(withheld[0]["reason"], "cross-scope")
        kept, _ = self.admit([entry(scope={"account": "",
                                           "project": "/work/proj-a"})])
        self.assertEqual(kept, [])

    def test_unparsable_observed_at_is_stale(self):
        for value in ("yesterday", "", None, 20261001, 2026.1, "2026-13-40",
                      "2026-10-02Tnoon", "10/02/2026", ["2026-10-02"]):
            with self.subTest(value=value):
                self.assertWithheld(entry(observed_at=value), "stale")

    def test_horizon_default_and_caller_supplied(self):
        edge = (TODAY - timedelta(days=C.DEFAULT_HORIZON_DAYS)).isoformat()
        past = (TODAY - timedelta(days=C.DEFAULT_HORIZON_DAYS + 1)).isoformat()
        self.assertEqual(len(self.admit([entry(observed_at=edge)])[0]), 1)
        self.assertWithheld(entry(observed_at=past), "stale")
        self.assertEqual(len(self.admit([entry(observed_at=past)],
                                        horizon_days=365)[0]), 1)

    def test_horizon_zero_keeps_only_today(self):
        today = entry(observed_at=TODAY.isoformat())
        today_dt = entry(observed_at=TODAY.isoformat() + "T08:30:00")
        kept, _ = self.admit([today, today_dt], horizon_days=0)
        self.assertEqual(kept, [today, today_dt])
        self.assertWithheld(entry(observed_at=(TODAY - timedelta(days=1))
                                  .isoformat()), "stale", horizon_days=0)

    def test_future_observation_is_stale(self):
        self.assertWithheld(entry(observed_at="2026-10-03"), "stale")

    def test_non_dict_entry_is_malformed(self):
        for e in ("a lesson", None, 3, ["snippet"]):
            with self.subTest(e=e):
                kept, withheld = self.admit([e])
                self.assertEqual(kept, [])
                self.assertEqual(withheld, [{"source": "",
                                             "reason": "malformed"}])

    def test_corrupt_field_types_are_malformed(self):
        for over in (dict(revoked="no"), dict(revoked=0),
                     dict(authority="gospel"), dict(kind="rumour"),
                     dict(snippet=None), dict(source=5),
                     dict(authority=["casual"])):
            with self.subTest(over=over):
                kept, withheld = self.admit([entry(**over)])
                self.assertEqual(kept, [])
                self.assertEqual(withheld[0]["reason"], "malformed")

    def test_hostile_request_is_refused(self):
        bad = [dict(entries="abc"), dict(entries=None), dict(entries=(1,)),
               dict(scope=None), dict(scope="acct-1"),
               dict(scope={"account": "a"}),
               dict(scope={"account": None, "project": "/p"}),
               dict(today="2026-10-02"), dict(today=None),
               dict(today=datetime(2026, 10, 2)),
               dict(horizon_days=True), dict(horizon_days=float("nan")),
               dict(horizon_days="90"), dict(horizon_days=-1),
               dict(horizon_days=None)]
        for over in bad:
            kw = dict(entries=[entry()], scope=dict(SCOPE), today=TODAY,
                      horizon_days=90)
            kw.update(over)
            with self.subTest(over=over):
                with self.assertRaises(ValueError):
                    C.admit_context(**kw)

    def test_pure_and_nothing_cached(self):
        es = [entry(source="a"), entry(source="b", revoked=True)]
        before = copy.deepcopy(es)
        first = self.admit(es)
        self.assertEqual(es, before)
        self.assertEqual(self.admit(es), first)

    def test_same_principal_revoked_since_last_time_is_withheld(self):
        """The acceptance line: admitted on the last request, the same
        principal on the same project revoked since, withheld on this one."""
        e = entry(source="pref")
        self.assertEqual(self.admit([e]), ([e], []))
        e["revoked"] = True
        self.assertEqual(self.admit([e]),
                         ([], [{"source": "pref", "reason": "revoked"}]))


class BuildCapsuleScoped(unittest.TestCase):
    def build(self, snippets, **kw):
        cwd = tempfile.mkdtemp()
        return C.build_capsule({"id": "u1", "write_scope": []}, cwd=cwd,
                               vault_snippets=snippets, scope=dict(SCOPE),
                               **kw)

    def fresh(self, **over):
        return entry(observed_at=date.today().isoformat(), **over)

    def test_kept_and_withheld_are_both_visible(self):
        good = self.fresh(source="good")
        cap = self.build([good, self.fresh(source="gone", revoked=True)])
        self.assertEqual(cap["vault_context"], [good])
        self.assertEqual(cap["vault_withheld"],
                         [{"source": "gone", "reason": "revoked"}])

    def test_untagged_snippets_are_tagged_with_defaults_then_withheld(self):
        cap = self.build(["plain", {"snippet": "b", "source": "note-x"}])
        self.assertEqual(cap["vault_context"], [])
        self.assertEqual(cap["vault_withheld"],
                         [{"source": "vault", "reason": "cross-scope"},
                          {"source": "note-x", "reason": "cross-scope"}])

    def test_hostile_vault_items_are_withheld_not_crashed(self):
        cap = self.build([3, None, ["x"], {"snippet": 7, "source": "n"},
                          self.fresh(snippet=None)])
        self.assertEqual(cap["vault_context"], [])
        self.assertEqual([w["reason"] for w in cap["vault_withheld"]],
                         ["malformed"] * 5)

    def test_hostile_scope_or_snippets_container_is_refused(self):
        node = {"id": "u1", "write_scope": []}
        for kw in (dict(scope="acct-1"), dict(scope=["a"]),
                   dict(scope={"account": "a"}),
                   dict(scope=dict(SCOPE), vault_snippets="abc")):
            with self.subTest(kw=kw):
                with self.assertRaises(ValueError):
                    C.build_capsule(node, cwd=tempfile.mkdtemp(), **kw)

    def test_kept_snippet_is_truncated_by_the_byte_cap(self):
        cap = self.build([self.fresh(snippet="z" * 500)], max_file_bytes=50)
        self.assertIn("truncated", cap["vault_context"][0]["snippet"])
        self.assertLess(len(cap["vault_context"][0]["snippet"]), 100)

    def test_withholding_changes_the_hash(self):
        good = self.fresh(source="good")
        h1 = self.build([good])["capsule_hash"]
        h2 = self.build([dict(good, revoked=True)])["capsule_hash"]
        self.assertNotEqual(h1, h2)


# The fifteen inputs of test_context_capsule.py, one builder per test there,
# in that file's order. Each returns (node, kwargs) after writing its files.
def _c1(cwd):
    return ({"id": "u1", "name": "do the thing", "done_check": "true",
             "owns": ["a.py"]}, {})


def _c2(cwd):
    return {"id": "u2"}, {}


def _c3(cwd):
    return {"id": "u1", "write_scope": []}, {}


def _c4(cwd):
    write(cwd, "declared.py", "x = 1\n")
    write(cwd, "undeclared.py", "y = 2\n")
    write(cwd, "pkg/nested.py", "z = 3\n")
    return {"id": "u1", "write_scope": ["declared.py"]}, {}


def _c5(cwd):
    write(cwd, "pkg/inner.py", "z = 1\n")
    return {"id": "u1", "write_scope": ["pkg"]}, {}


def _c6(cwd):
    return {"id": "u1", "write_scope": ["nope.py"]}, {}


def _c7(cwd):
    write(cwd, "big.py", "A" * 10000)
    return {"id": "u1", "write_scope": ["big.py"]}, {"max_file_bytes": 100}


def _c8(cwd):
    for i in range(20):
        write(cwd, "f%d.py" % i, "x" * 500)
    return ({"id": "u1", "write_scope": ["f%d.py" % i for i in range(20)]},
            {"vault_snippets": [{"snippet": "y" * 500, "source": "vault"}
                                for _ in range(20)],
             "max_file_bytes": 1000, "max_total_bytes": 4000})


def _c9(cwd):
    write(cwd, "only.py", "z" * 5000)
    return ({"id": "u1", "write_scope": ["only.py"]},
            {"max_file_bytes": 5000, "max_total_bytes": 1})


def _c10(cwd):
    write(cwd, "a.py", "same content\n")
    return {"id": "u1", "write_scope": ["a.py"], "done_check": "true"}, {}


def _c11(cwd):
    write(cwd, "a.py", "version one\n")
    return {"id": "u1", "write_scope": ["a.py"]}, {}


def _c12(cwd):
    return ({"id": "u1", "write_scope": []},
            {"dependency_outputs": {"upstream": "ok"}})


def _c13(cwd):
    write(cwd, "a.py", "fresh\n")
    return {"id": "u1", "write_scope": ["a.py"]}, {}


def _c14(cwd):
    return ({"id": "u1", "write_scope": []},
            {"vault_snippets": ["a lesson", {"snippet": "b",
                                             "source": "note-x"}]})


def _c15(cwd):
    return ({"id": "u1", "write_scope": []},
            {"decisions": [{"decision": "use path A", "reason": "cheaper",
                            "cost_if_wrong": "one revert",
                            "unrelated_field": "drop me"}]})


#: capsule_hash of each input under build_capsule as it stood before RL3.a,
#: identical under Python 3.9 and 3.13.
PINNED_HASHES = {
    "_c1": "81e71e6b73b5f7e6b745bd7a4641bf8f92fb9ae97c6cfdcd61b357dab21bf02c",
    "_c2": "dfd599d0e9de9a5122bbb448148c756c2300334d1814b163798bb0134b74dea3",
    "_c3": "1f1b190321ac4fa59899399b3e21f02068b1bed2b430bba5b45c7ade90db9523",
    "_c4": "8ad07e6a820bf7af33b1342e3e69901edac3cd3a066ea270737fdf234601975b",
    "_c5": "e07735930c1680be37677f4eebecf81a04148a1ce6bf9d24686c273e25f61b12",
    "_c6": "fa06b3c7b7e36f304c2ed4fdfa6154e34d26ba1a81a025ca293eaeee23cb90d6",
    "_c7": "88c5bba526461cd37d0f0fe44ae355efd1f57f08e6c8ff6f454220f001a74b4c",
    "_c8": "7f90b65dfda91238a9de0aed90983394eaa20ec7bce0f948c77b2b8e856fbc13",
    "_c9": "426351e58945b637a353867a94931665e2050c0347878cff13596ffc03af0e61",
    "_c10": "a3413499c28fdd10fdf873e38b1a5a919ccc181f243fba7eb0dc544bc6b1b46f",
    "_c11": "a1510a3eb40b244e7d0a72464c137a1b120fd10ed8e5944119978417109e4441",
    "_c12": "bfea66cb4fe884b6caa75416284a25d88f7ea7d795bc7bf0b3e2ca176e72f25e",
    "_c13": "79bef691439b3cefd67c12116ade8d89d0ba77b3c36470b86372dc088ab81697",
    "_c14": "4a3ba65a5ee3d2ff190876e9364ad25954907636ffdcf1e1e1e40b4455d534b8",
    "_c15": "12b4dbe53091b31b224fa32995a2443e72da162f6b6a14299c44e219a39ea39e",
}


class ScopeNoneIsByteIdentical(unittest.TestCase):
    def test_existing_inputs_hash_as_before_with_scope_none(self):
        builders = (_c1, _c2, _c3, _c4, _c5, _c6, _c7, _c8, _c9, _c10, _c11,
                    _c12, _c13, _c14, _c15)
        self.assertEqual(len(builders), 15)
        for build in builders:
            with self.subTest(case=build.__name__):
                cwd = tempfile.mkdtemp()
                node, kw = build(cwd)
                plain = C.build_capsule(dict(node), cwd=cwd, **kw)
                scoped = C.build_capsule(dict(node), cwd=cwd, scope=None, **kw)
                self.assertEqual(scoped, plain)
                self.assertNotIn("vault_withheld", scoped)
                self.assertEqual(scoped["capsule_hash"],
                                 PINNED_HASHES[build.__name__])


class HostObservation(unittest.TestCase):
    """RL3.c: host_observation reads only the marker variables client reads."""

    def test_empty_env_is_no_data_and_not_observed(self):
        self.assertEqual(C.host_observation({}),
                         {"host": "NO-DATA", "observed": False})

    def test_named_client_is_observed(self):
        for host in ("claude", "codex", "cursor"):
            self.assertEqual(C.host_observation({"BROTHER_CLIENT": host}),
                             {"host": host, "observed": True})
        self.assertEqual(C.host_observation({"CODEX_SESSION_ID": "s1"}),
                         {"host": "codex", "observed": True})

    def test_unrecognised_client_value_is_no_data(self):
        self.assertEqual(C.host_observation({"BROTHER_CLIENT": "vim"}),
                         {"host": "NO-DATA", "observed": False})

    def test_missing_brother_paths_reads_no_data(self):
        saved = sys.modules.get("brother_paths")
        sys.modules["brother_paths"] = None  # import now raises ImportError
        try:
            obs = C.host_observation({"BROTHER_CLIENT": "claude"})
        finally:
            if saved is None:
                del sys.modules["brother_paths"]
            else:
                sys.modules["brother_paths"] = saved
        self.assertEqual(obs, {"host": "NO-DATA", "observed": False})

    def test_hostile_env_refused(self):
        for bad in ("BROTHER_CLIENT=claude", ["claude"], True, 3,
                    float("nan"), ("claude",)):
            with self.assertRaises(ValueError):
                C.host_observation(bad)


if __name__ == "__main__":
    unittest.main()
