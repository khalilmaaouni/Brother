#!/usr/bin/env python3
"""OUT1.n integrator cases beside the frozen ConsumerAdmission contract (test_workflows.py, 9 cases on the pure rule).

The frozen class tests consumer_capability_visible on records. These cases pin what it cannot reach: the door's own
entry points (the --capability command line, --route recommendation, --available) consult one admission view, a
hidden capability starts nothing and answers with the same truthful limitation, a stale, foreign-host, foreign-revision
or tiered-out record hides it again, an unreadable ADMISSION.json hides everything, and every door capability is
listed by some group in the shipped docs/plan/ADMISSION.json, so nothing is hidden with no owner.

Hermetic: temp registries only, no HOME read, no network, no model.

Run: python3 -B scripts/test_consumer_admission.py
"""
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import door  # noqa: E402
import reach_check  # noqa: E402

REV, HOST = "rev-a", "host-a"
GOOD = dict(implemented=True, packaged=True, supported_hosts=[HOST], candidate=REV, host=HOST,
            installed_verdict="PASS", evidence_refs=["installed-proof"], benefit_score=8.0, ease_score=8.0,
            score_evidence_refs=["independent-assessment"], evidence_current=True, blocking_defects=[],
            quality_verdict="PASS")
CAP = {"id": "build-outcome", "verb": "start", "command": "scripts/door.py", "marker": "def recommend",
       "ask": "build me a login screen", "paraphrases": ["add a dark mode toggle", "implement the export feature"]}
HELP = {"id": "how-it-works", "verb": "help", "command": "scripts/door.py", "marker": "def recommend",
        "ask": "explain how this works", "paraphrases": ["help me understand this", "orient me please"]}


def inventory(gid, routed=None, unit="OUT1.x"):
    return {"id": gid, "routed": routed, "unit": unit, "disposition": "consumer", "user_benefit": "x"}


class Fixture(object):
    """A temp registry + ADMISSION.json, and the environment that names this candidate and host."""
    def __init__(self, case, entries, caps=(CAP, HELP), inv=None, raw=None):
        self.dir = tempfile.mkdtemp(prefix="admission-")
        case.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))
        self.registry = os.path.join(self.dir, "REACH.json")
        with open(self.registry, "w") as fh:
            json.dump({"capabilities": list(caps), "inventory": inv or [inventory("g1", "build-outcome")]}, fh)
        if raw is not None or entries is not None:
            with open(os.path.join(self.dir, door.ADMISSION_FILE), "w") as fh:
                fh.write(raw if raw is not None else json.dumps({"entries": entries}))
        env = mock.patch.dict(os.environ, {"BROTHER_CANDIDATE": REV, "BROTHER_HOST": HOST})
        reg = mock.patch.object(door, "REACH_REGISTRY", self.registry)
        env.start(), reg.start()
        case.addCleanup(env.stop), case.addCleanup(reg.stop)


def entry(**changes):
    e = {"tier": "AMPLIFIER", "owner": "OUT1.x", "capabilities": ["vault.recall", "vault.status"],
         "admission": dict(GOOD)}
    e.update(changes)
    return e


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = door.main(argv)
    return rc, out.getvalue()


class Dispatch(unittest.TestCase):
    def call(self, argv):
        """door.main on `argv`; any real runner reached is recorded and answers COMPLETE."""
        reached = []
        done = lambda *a, **k: (reached.append(a[0]) or {"capability": a[0], "state": "COMPLETE"})
        with mock.patch.object(door, "run_vault_capability", done):
            rc, out = run_main(argv)
        return rc, out, reached

    def test_admitted_capability_reaches_its_runner(self):
        Fixture(self, {"g1": entry()})
        rc, out, reached = self.call(["--capability", "vault.recall"])
        self.assertEqual((rc, reached), (0, ["vault.recall"]))

    def test_hidden_capability_starts_nothing_and_says_so(self):
        Fixture(self, {"g1": entry(admission=None)})
        rc, out, reached = self.call(["--capability", "vault.recall"])
        self.assertEqual((rc, reached), (2, []))
        got = json.loads(out)
        self.assertEqual((got["state"], got["verdict"], got["output"]), ("BLOCKED", "NO-DATA", door.ADMISSION_LIMIT))
        self.assertIn("help", got["next_step"])

    def test_old_route_to_an_unlisted_or_unknown_capability_is_hidden(self):
        Fixture(self, {"g1": entry()})
        for cap in ("vault.promote", "vault.nothing", "data.check", ""):
            rc, out, reached = self.call(["--capability", cap])
            self.assertEqual((rc, reached), (2, []), cap)
            self.assertNotIn("known:", out)

    def test_revoked_proof_hides_again_and_a_repair_shows_again(self):
        fx = Fixture(self, {"g1": entry()})
        self.assertEqual(self.call(["--capability", "vault.recall"])[0], 0)
        stale = dict(GOOD, evidence_current=False)
        json.dump({"entries": {"g1": entry(admission=stale)}}, open(os.path.join(fx.dir, door.ADMISSION_FILE), "w"))
        self.assertEqual(self.call(["--capability", "vault.recall"])[2], [])
        json.dump({"entries": {"g1": entry()}}, open(os.path.join(fx.dir, door.ADMISSION_FILE), "w"))
        self.assertEqual(self.call(["--capability", "vault.recall"])[2], ["vault.recall"])

    def test_each_wrong_identity_hides(self):
        for name, env in (("revision", {"BROTHER_CANDIDATE": "rev-old"}), ("host", {"BROTHER_HOST": "host-b"})):
            Fixture(self, {"g1": entry()})
            with mock.patch.dict(os.environ, env):
                self.assertEqual(self.call(["--capability", "vault.recall"])[2], [], name)

    def test_unidentified_candidate_or_host_hides(self):
        Fixture(self, {"g1": entry()})
        for key in ("BROTHER_CANDIDATE", "BROTHER_HOST"):
            with mock.patch.dict(os.environ):
                os.environ.pop(key)
                os.environ.pop("CLAUDE_PLUGIN_ROOT", None), os.environ.pop("BROTHER_PLUGIN_ROOT", None)
                with mock.patch.object(door, "current_candidate", lambda: None):
                    self.assertEqual(self.call(["--capability", "vault.recall"])[2], [], key)

    def test_internal_tier_is_hidden_whatever_the_record(self):
        for tier in ("PARK", "MAINTAINER"):
            Fixture(self, {"g1": entry(tier=tier)})
            self.assertEqual(self.call(["--capability", "vault.recall"])[2], [], tier)

    def test_missing_corrupt_or_wrongly_shaped_admission_file_hides_everything(self):
        for kind, kw in (("missing", dict(entries=None)), ("corrupt", dict(entries=None, raw="{not json")),
                         ("no entries key", dict(entries=None, raw="{}")),
                         ("entries a list", dict(entries=None, raw='{"entries": []}'))):
            Fixture(self, **kw)
            self.assertEqual(self.call(["--capability", "vault.recall"])[2], [], kind)

    def test_glob_pattern_admits_its_prefix_only(self):
        Fixture(self, {"g1": entry(capabilities=["vault.*"])})
        self.assertEqual(self.call(["--capability", "vault.status"])[2], ["vault.status"])
        self.assertEqual(self.call(["--capability", "data.check"])[2], [])

    def test_a_bare_star_admits_nothing(self):
        Fixture(self, {"g1": entry(capabilities=["*", "vault*"])})
        self.assertEqual(self.call(["--capability", "vault.recall"])[2], [])

    def test_capability_patterns_of_a_malformed_entry_hide(self):
        Fixture(self, {"g1": entry(capabilities="vault.*")})
        self.assertEqual(self.call(["--capability", "vault.recall"])[2], [])


class Surfaces(unittest.TestCase):
    def test_available_lists_only_what_is_admitted(self):
        Fixture(self, {"g1": entry(), "g2": entry(admission=None, capabilities=["data.check"])},
                inv=[inventory("g1", "build-outcome"), inventory("g2")])
        rc, out = run_main(["--available"])
        got = json.loads(out)
        self.assertEqual((rc, got["capabilities"], got["routes"]), (0, ["vault.recall", "vault.status"],
                                                                   ["build-outcome"]))
        self.assertEqual((got["candidate"], got["host"]), (REV, HOST))

    def test_route_is_delivered_only_for_an_admitted_route(self):
        Fixture(self, {"g1": entry()})
        rc, out = run_main(["--route", "build me a login screen"])
        self.assertEqual(rc, 0)
        Fixture(self, {"g1": entry(admission=dict(GOOD, ease_score=7.9))})
        rc, out = run_main(["--route", "build me a login screen"])
        self.assertEqual(rc, 2)
        self.assertIn(door.ADMISSION_LIMIT, out)

    def test_hidden_route_names_no_hidden_capability(self):
        pending = {"id": "secret-thing", "disposition": "pending", "unit": "OUT1.z", "routed": None,
                   "user_benefit": "Reconcile the secret warehouse ledger", "operations": ["warehouse ledger"]}
        fx = Fixture(self, {"g1": entry(admission=None)}, inv=[inventory("g1", "build-outcome"), pending])
        got = door.recommend("reconcile the secret warehouse ledger", admission=door.consumer_admission())
        self.assertFalse(got["delivered"])
        self.assertNotIn("secret", json.dumps(got))
        self.assertNotIn("OUT1.z", json.dumps(got))
        # the library reading (no view given) keeps the old pending-unit sentence, so only the door's view hides
        self.assertTrue(door.recommend("reconcile the secret warehouse ledger", registry=fx.registry)["route"] is None)

    def test_help_route_starts_nothing_so_needs_no_admission(self):
        Fixture(self, {"g1": entry(admission=None)})
        got = door.recommend("explain how this works", admission=door.consumer_admission())
        self.assertEqual((got["route"], got["verb"]), ("how-it-works", "help"))
        self.assertIn("explain what it does", got["action"])

    def test_help_lists_only_the_admitted_asks(self):
        hidden = {"id": "cut-check", "verb": "review", "command": "scripts/door.py", "marker": "def recommend",
                  "ask": "is the release ready to cut", "paraphrases": ["run the loop overnight"]}
        Fixture(self, {"g1": entry(), "g2": entry(admission=None)}, caps=(CAP, HELP, hidden),
                inv=[inventory("g1", "build-outcome"), inventory("g2", "cut-check")])
        got = door.recommend("explain how this works", admission=door.consumer_admission())
        self.assertIn("Available now: build me a login screen.", got["action"])
        self.assertNotIn("ready to cut", got["action"])
        self.assertNotIn("overnight", got["action"])

    def test_a_help_goal_that_best_matches_a_hidden_route_does_not_name_it(self):
        Fixture(self, {"g1": entry(admission=None)})
        goal = "help me build a login screen"
        self.assertEqual(door.route(goal), "help")  # the premise: the verb is help while the best match is the hidden ask
        self.assertEqual(door.recommend(goal, registry=door._default_registry())["route"], "build-outcome")
        got = door.recommend(goal, admission=door.consumer_admission())
        self.assertIsNone(got["route"])
        self.assertNotIn("build-outcome", json.dumps(got))

    def test_help_does_not_list_an_admitted_help_route_as_something_to_ask(self):
        Fixture(self, {"g1": entry(), "g2": entry(admission=dict(GOOD), capabilities=["data.check"])},
                inv=[inventory("g1", "build-outcome"), inventory("g2", "how-it-works")])
        view = door.consumer_admission()
        self.assertIn("how-it-works", view["routes"])  # the premise: a help route IS admitted
        got = door.recommend("explain how this works", admission=view)
        self.assertIn("Available now: build me a login screen.", got["action"])
        self.assertNotIn("explain how this works.", got["action"])

    def test_every_hidden_group_is_queued_with_cause_owner_and_next_verification(self):
        Fixture(self, {"g1": entry(tier="PARK", admission=dict(GOOD, benefit_score=None))},
                inv=[inventory("g1", "build-outcome", "OUT1.q"), inventory("g2", None, "OUT1.r")])
        queue = {q["id"]: q for q in door.consumer_admission()["queue"]}
        self.assertEqual(sorted(queue), ["g1", "g2"])
        self.assertIn("tier PARK is internal only", queue["g1"]["cause"])
        self.assertIn("benefit_score", queue["g1"]["cause"])
        self.assertEqual(queue["g2"]["cause"], "no admission record")
        self.assertEqual((queue["g1"]["owner"], queue["g2"]["owner"]), ("OUT1.x", "OUT1.r"))
        self.assertTrue(all(q["next_verification"] for q in queue.values()))

    def test_unreadable_registry_admits_nothing(self):
        fx = Fixture(self, {"g1": entry()})
        os.remove(fx.registry)
        self.assertEqual(door.consumer_admission()["capabilities"], [])


def core_entry(**changes):
    rec = {"basis": "journey", "journey_rows": ["J2/x"], "implemented": True, "packaged": True,
           "supported_hosts": [HOST], "blocking_defects": [], "quality_verdict": "PASS"}
    rec.update(changes)
    return entry(tier="CORE", admission=rec)


def walk(directory, name="J2", **changes):
    """Write the walk record JOURNEY-<name>.json the way scripts/journey/run_journey.py does, for this machine.
    `changes` override fields of the record itself (including its own "journey" id, which may differ from `name`)."""
    doc = {"journey": name, "clean_home": True, "identity": "abc", "candidate_identity": "abc",
           "host": door._os_host(), "rows": {"x": "PASS"}}
    doc.update(changes)
    with open(os.path.join(directory, "JOURNEY-%s.json" % name), "w") as fh:
        json.dump(doc, fh)


class Core(unittest.TestCase):
    """CORE is admitted on journey rows READ from the walk records, one fixture per guard; nothing else relaxes."""
    def admitted(self, record=True, **changes):
        fx = Fixture(self, {"g1": core_entry(**changes)})
        if record is not None:
            walk(fx.dir, **(record if isinstance(record, dict) else {}))
        return "vault.recall" in door.consumer_admission()["capabilities"]

    def test_core_with_passing_walk_rows_is_admitted_without_scores_or_revision(self):
        self.assertTrue(self.admitted())
        self.assertTrue(self.admitted(candidate="another"))

    def test_each_core_entry_guard_hides(self):
        for kw in ({"basis": "scores"}, {"journey_rows": []}, {"journey_rows": "J2/x"}, {"implemented": False},
                   {"packaged": 1}, {"blocking_defects": ["security"]}, {"quality_verdict": "FAIL"},
                   {"supported_hosts": []}, {"supported_hosts": ["host-b"]}):
            self.assertFalse(self.admitted(**kw), kw)

    def test_each_walk_record_guard_hides(self):
        for label, record in (("no record file", None), ("another journey", {"journey": "J9"}),
                              ("not a clean home", {"clean_home": False}),
                              ("clean_home is not a bool", {"clean_home": "yes"}),
                              ("not the candidate's own tree", {"identity": "abc", "candidate_identity": "def"}),
                              ("no identity", {"identity": "", "candidate_identity": ""}),
                              ("another machine", {"host": "no-such-os"}),
                              ("row FAIL", {"rows": {"x": "FAIL"}}), ("row NO-DATA", {"rows": {"x": "NO-DATA"}}),
                              ("row absent", {"rows": {"y": "PASS"}}), ("rows not a mapping", {"rows": ["x"]}),
                              ("row passes as a bool", {"rows": {"x": True}})):
            self.assertFalse(self.admitted(record), label)

    def test_an_unreadable_record_hides(self):
        fx = Fixture(self, {"g1": core_entry()})
        with open(os.path.join(fx.dir, "JOURNEY-J2.json"), "w") as fh:
            fh.write("{not json")
        self.assertNotIn("vault.recall", door.consumer_admission()["capabilities"])

    def test_every_named_row_must_pass_not_just_one(self):
        self.assertFalse(self.admitted({"rows": {"x": "PASS", "y": "NO-DATA"}}, journey_rows=["J2/x", "J2/y"]))
        self.assertTrue(self.admitted({"rows": {"x": "PASS", "y": "PASS"}}, journey_rows=["J2/x", "J2/y"]))

    def test_a_malformed_row_reference_hides_even_when_a_record_would_satisfy_it(self):
        for ref in ("J2", "J2/", "/x", "../J2/x", "J2.json/x"):
            self.assertFalse(self.admitted(journey_rows=[ref]), ref)
        fx = Fixture(self, {"g1": core_entry(journey_rows=["a b/x"])})  # a record that would pass if the id were accepted
        walk(fx.dir, "a b", journey="a b")
        self.assertNotIn("vault.recall", door.consumer_admission()["capabilities"])
        fx = Fixture(self, {"g1": core_entry(journey_rows=["J2/"])})  # an empty row name that the record "passes"
        walk(fx.dir, rows={"": "PASS"})
        self.assertNotIn("vault.recall", door.consumer_admission()["capabilities"])

    def test_core_fails_closed_on_an_unidentified_host_and_on_a_wrong_host(self):
        fx = Fixture(self, {"g1": core_entry()})
        walk(fx.dir)
        with mock.patch.dict(os.environ):
            os.environ.pop("BROTHER_HOST")
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            os.environ.pop("BROTHER_PLUGIN_ROOT", None)
            self.assertNotIn("vault.recall", door.consumer_admission(host=None)["capabilities"])
        with mock.patch.dict(os.environ, {"BROTHER_HOST": "host-b"}):
            self.assertNotIn("vault.recall", door.consumer_admission()["capabilities"])
        self.assertIn("vault.recall", door.consumer_admission()["capabilities"])

    def test_the_core_tier_name_does_not_relax_a_non_core_group(self):
        Fixture(self, {"g1": entry(admission=dict(core_entry().get("admission"), benefit_score=None))})
        self.assertNotIn("vault.recall", door.consumer_admission()["capabilities"])

    def test_the_host_comes_from_the_plugin_root_or_from_where_the_door_is_installed(self):
        self.assertEqual("claude", door.current_host({}, "/h/.claude/plugins/cache/brother/brother/1/runtime"))
        self.assertEqual("codex", door.current_host({}, "/h/.codex/plugins/cache/brother/runtime"))
        self.assertIsNone(door.current_host({}, "/h/Brother/scripts"))
        self.assertEqual("x", door.current_host({"BROTHER_HOST": "x"}, "/h/.claude/plugins/a"))

    def test_a_no_data_sentence_in_the_environment_is_not_a_candidate_either(self):
        with tempfile.TemporaryDirectory() as here, mock.patch.dict(os.environ):
            with open(os.path.join(here, door.HOST_MANIFEST), "w") as fh:
                json.dump({"source_revision": "d9d48279b"}, fh)
            os.environ["BROTHER_CANDIDATE"] = "NO-DATA: git could not name the source revision"
            self.assertEqual(door.current_candidate(here), "d9d48279b")  # falls through to the installed stamp
            os.environ["BROTHER_CANDIDATE"] = "rev-from-the-operator"
            self.assertEqual(door.current_candidate(here), "rev-from-the-operator")

    def test_a_client_whose_config_folder_is_not_named_dot_claude_is_still_the_claude_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            here = os.path.join(tmp, "config", "plugins", "cache", "brother-candidate", "brother", "1.2.0", "runtime")
            os.makedirs(here)
            self.assertEqual("claude", door.current_host({"CLAUDE_CONFIG_DIR": os.path.join(tmp, "config")}, here))
            self.assertIsNone(door.current_host({}, here))  # no variable, no .claude in the path: unidentified
            self.assertIsNone(door.current_host({"CLAUDE_CONFIG_DIR": os.path.join(tmp, "elsewhere")}, here))
            self.assertIsNone(door.current_host({"CLAUDE_CONFIG_DIR": os.path.join(tmp, "conf")}, here))  # a prefix of a name is not the folder

    def test_a_no_data_runtime_stamp_is_not_a_candidate(self):
        for stamp, want in (("NO-DATA: git could not name the source revision", None), ("", None), ("d9d48279b", "d9d48279b"),
                            ("0123456789abcdef0123456789abcdef01234567", "0123456789abcdef0123456789abcdef01234567")):
            with tempfile.TemporaryDirectory() as here, mock.patch.dict(os.environ):
                os.environ.pop("BROTHER_CANDIDATE", None)
                with open(os.path.join(here, door.HOST_MANIFEST), "w") as fh:
                    json.dump({"source_revision": stamp}, fh)
                self.assertEqual(door.current_candidate(here), want, stamp)


class Installed(unittest.TestCase):
    """The door run from a copy of bundle/ alone, isolated, with no checkout and no host variable. The copy sits under
    a .claude/plugins cache path, the way a client installs it, so the host is identified by where the door lives."""
    @classmethod
    def setUpClass(cls):
        import shutil
        cls.dir = tempfile.mkdtemp(prefix="admission-bundle-")
        cls.bundle = os.path.join(cls.dir, ".claude", "plugins", "cache", "brother", "brother", "9.9.9")
        shutil.copytree(os.path.join(ROOT, "bundle"), cls.bundle)
        cls.data = os.path.join(cls.bundle, "runtime", "data")
        cls.records = {}
        for jid in ("J1", "J2", "J4"):
            path = os.path.join(cls.data, "JOURNEY-%s.json" % jid)
            with open(path) as fh:
                doc = json.load(fh)
            doc["host"] = door._os_host()  # shipped records were walked on macos; this copy runs wherever the test does
            with open(path, "w") as fh:
                json.dump(doc, fh)
            cls.records[jid] = doc

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.dir, ignore_errors=True)

    def door(self, *args, **kw):
        import subprocess
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER_", "CLAUDE_"))}
        got = subprocess.run([sys.executable, "-I", "-B", os.path.join(kw.get("bundle") or self.bundle, "runtime", "door.py")]
                             + list(args), cwd=self.dir, env=clean, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, timeout=60)
        return got.returncode, got.stdout

    def test_iphone_words_reach_an_ordinary_route_while_the_mobile_steps_stay_unlisted(self):
        """OUT1.n review M1: these two phrasings are routed (rc 0) and still the door does not list a mobile step, so the
        mobile skill must gate on --available, never on a route being named."""
        got = json.loads(self.door("--available")[1])
        self.assertFalse([c for c in got["capabilities"] if c.startswith("mobile.")], got["capabilities"])
        for goal, route in (("build a SwiftUI screen for my iPhone app", "build-outcome"),
                            ("fix the bug in my iOS app", "app-correction")):
            rc, out = self.door("--route", goal)
            self.assertEqual(rc, 0, (goal, out))
            self.assertIn("route: " + route, out)

    def test_the_same_bundle_outside_a_client_cache_admits_nothing(self):
        import shutil
        loose = os.path.join(self.dir, "loose")
        shutil.copytree(self.bundle, loose)
        rc, out = self.door("--available", bundle=loose)
        got = json.loads(out)
        self.assertEqual((rc, got["capabilities"], got["routes"], got["host"]), (0, [], [], None))
        rc, out = self.door("--route", "build me a login screen", bundle=loose)
        self.assertEqual(rc, 2)

    def passing_core(self):
        """The CORE groups whose every named row is PASS in the walk records this bundle ships."""
        with open(os.path.join(self.data, "ADMISSION.json")) as fh:
            entries = json.load(fh)["entries"]
        return {g: e for g, e in entries.items() if e["tier"] == "CORE" and all(
            self.records[r.split("/")[0]]["rows"].get(r.split("/")[1]) == "PASS" for r in e["admission"]["journey_rows"])}

    def test_what_the_bundle_admits_is_what_its_shipped_walk_records_pass(self):
        want = self.passing_core()
        rc, out = self.door("--available")
        got = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(got["host"], "claude")
        self.assertEqual(got["capabilities"], sorted(c for e in want.values() for c in e["capabilities"]))
        self.assertTrue(want)  # the execution path is admitted from its recorded PASS rows
        self.assertEqual("execution-outcome" in want, "build-outcome" in got["routes"])
        self.assertEqual("status-acceptance" in want, "where-things-stand" in got["routes"])
        self.assertEqual("host-health" in want, "host.*" in got["capabilities"])

    def test_core_goals_route_from_the_bundle(self):
        for goal, route in (("where do things stand", "where-things-stand"), ("build me a login screen", "build-outcome")):
            rc, out = self.door("--route", goal)
            self.assertEqual(rc, 0, out)
            self.assertIn("route: " + route, out)

    def test_an_unadmitted_goal_is_refused_with_the_limitation(self):
        rc, out = self.door("--route", "check this number before the board sees it")
        self.assertEqual(rc, 2)
        self.assertNotIn("route: check-a-number", out)

    def test_available_lists_the_core_routes_and_help_says_so(self):
        rc, out = self.door("--available")
        got = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertIn("build-outcome", got["routes"])
        self.assertEqual("host.*" in got["capabilities"], "host-health" in self.passing_core())
        rc, out = self.door("--route", "help")
        self.assertIn("Available now: build me a login screen", out)

    def test_a_hidden_capability_is_refused_from_the_bundle(self):
        rc, out = self.door("--capability", "vault.recall")
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out)["output"], door.ADMISSION_LIMIT)


class Rule(unittest.TestCase):
    """One fixture per guard of admission_gaps, so deleting a guard turns exactly one case red."""
    def hidden(self, **changes):
        return door.admission_gaps(dict(GOOD, **changes), REV, HOST)

    def test_boundaries(self):
        self.assertEqual(self.hidden(), [])
        self.assertEqual(self.hidden(benefit_score=8, ease_score=10), [])
        self.assertTrue(self.hidden(ease_score=7.99))

    def test_a_bool_is_not_a_score(self):
        self.assertTrue(self.hidden(benefit_score=True))

    def test_blocking_defects_must_be_an_empty_list(self):
        for bad in ("security", None, ("security",), 0):
            self.assertTrue(self.hidden(blocking_defects=bad), repr(bad))

    def test_quality_verdict_must_pass_on_its_own(self):
        self.assertTrue(self.hidden(quality_verdict="FAIL"))
        self.assertTrue(self.hidden(quality_verdict=None))

    def test_evidence_current_must_be_true_not_truthy(self):
        self.assertTrue(self.hidden(evidence_current="yes"))

    def test_implemented_and_packaged_must_be_true_not_truthy(self):
        self.assertTrue(self.hidden(implemented=1))
        self.assertTrue(self.hidden(packaged="true"))

    def test_record_host_must_match_even_when_the_host_is_listed(self):
        self.assertTrue(self.hidden(host="host-b", supported_hosts=[HOST, "host-b"]))

    def test_unidentified_or_non_string_identity_hides(self):
        for cand, host in ((None, HOST), ("", HOST), (REV, None), (REV, ""), (1, HOST), (REV, ["host-a"])):
            self.assertTrue(door.admission_gaps(dict(GOOD), cand, host), repr((cand, host)))

    def test_non_dict_record_hides(self):
        for rec in (None, [], "PASS", 8):
            self.assertEqual(door.admission_gaps(rec, REV, HOST), ["no admission record"])

    def test_checker_name_agrees_with_the_door(self):
        for rec in (dict(GOOD), dict(GOOD, ease_score=7.9)):
            self.assertEqual(reach_check.consumer_capability_visible(rec, REV, HOST),
                             door.consumer_capability_visible(rec, REV, HOST))


class Shipped(unittest.TestCase):
    """The ADMISSION.json this repository ships."""
    def load(self):
        with open(os.path.join(ROOT, "docs", "plan", door.ADMISSION_FILE)) as fh:
            return json.load(fh)["entries"]

    def test_every_inventory_group_has_an_entry_with_a_tier_and_owner(self):
        with open(door.REACH_REGISTRY) as fh:
            ids = {r["id"] for r in json.load(fh)["inventory"]}
        entries = self.load()
        self.assertEqual(ids, set(entries))
        for gid, e in entries.items():
            self.assertIn(e["tier"], ("CORE", "AMPLIFIER") + door.ADMISSION_HIDDEN_TIERS, gid)
            self.assertTrue(e["owner"], gid)

    def test_every_door_capability_belongs_to_a_group(self):
        listed = [p for e in self.load().values() for p in e["capabilities"]]
        self.assertEqual([c for c in reach_check.door_capability_ids() if not door._door_match(listed, c)], [])

    def test_no_capability_is_in_two_groups(self):
        for cap in reach_check.door_capability_ids():
            owners = [g for g, e in self.load().items() if door._door_match(e["capabilities"], cap)]
            self.assertEqual(len(owners), 1, (cap, owners))

    def test_core_groups_are_admitted_only_on_passing_shipped_walk_rows_and_never_on_an_unidentified_host(self):
        core = {g for g, e in self.load().items() if e["tier"] == "CORE"}
        self.assertEqual(core, {"execution-outcome", "status-acceptance", "host-health"})
        records = {}
        for j in ("J1", "J2", "J4"):
            with open(os.path.join(ROOT, "docs", "plan", "JOURNEY-%s.json" % j)) as fh:
                records[j] = json.load(fh)
        passing = {g for g in core if all(records[r.split("/")[0]]["rows"].get(r.split("/")[1]) == "PASS"
                                          for r in self.load()[g]["admission"]["journey_rows"])}
        with mock.patch.object(door, "_os_host", return_value=records["J1"]["host"]):
            for cand in (REV, "0" * 40, None):
                queued = {q["id"] for q in door.consumer_admission(candidate=cand, host="claude")["queue"]}
                self.assertEqual(core - queued, passing, cand)  # revision does not matter to CORE, the rows do
                self.assertEqual(len(queued), len(self.load()) - len(passing), cand)
            blind = door.consumer_admission(candidate=REV, host=None)
            self.assertTrue(core <= {q["id"] for q in blind["queue"]})
            self.assertEqual(blind["capabilities"], [])
        self.assertTrue(passing)

    def test_the_walk_records_ship_only_the_fields_the_door_reads(self):
        import bundle_runtime
        for jid in ("J1", "J2", "J4"):
            with open(os.path.join(ROOT, "docs", "plan", "JOURNEY-%s.json" % jid), "rb") as fh:
                source = fh.read()
            with open(os.path.join(ROOT, "bundle", "runtime", "data", "JOURNEY-%s.json" % jid), "rb") as fh:
                shipped = fh.read()
            self.assertEqual(bundle_runtime.ship_journey_record(source), shipped, jid)
            self.assertEqual(sorted(json.loads(shipped)), sorted(bundle_runtime.JOURNEY_SHIPPED_KEYS), jid)
            for private in (b"installed_root", b"walked_by", b"/var/folders", b"session-"):
                self.assertNotIn(private, shipped, (jid, private))

    def test_ship_journey_record_drops_the_extras_keeps_what_the_door_reads_and_refuses_the_unreadable(self):
        import bundle_runtime
        raw = json.dumps({"journey": "J2", "clean_home": True, "identity": "a", "candidate_identity": "a",
                          "host": "macos", "rows": {"x": "PASS"}, "installed_root": "/var/folders/zz/T/x",
                          "walked_by": "someone", "stripped_env": ["A"], "reason": "r"}).encode()
        once = bundle_runtime.ship_journey_record(raw)
        self.assertEqual(set(json.loads(once)), set(bundle_runtime.JOURNEY_SHIPPED_KEYS))
        self.assertEqual(once, bundle_runtime.ship_journey_record(once))  # idempotent: the export scrubs a scrubbed file
        for bad in (b"[1]", b"not json", b"\xff"):
            with self.assertRaises(ValueError):
                bundle_runtime.ship_journey_record(bad)

    def test_the_shipped_copy_does_not_depend_on_the_identity_the_source_carries(self):
        # A source record carries the sha256 of the manifest that lists the shipped copy; if the copy carried it too the
        # manifest would depend on a value derived from itself (no fixed point). Two sources that differ only in
        # their identity must therefore ship byte for byte the same, both fields reading the one fixed word.
        import bundle_runtime

        def raw(identity, candidate=None):
            return json.dumps({"journey": "J1", "clean_home": True, "identity": identity,
                               "candidate_identity": identity if candidate is None else candidate,
                               "host": "macos", "rows": {"x": "PASS"}}).encode()

        one = bundle_runtime.ship_journey_record(raw("a" * 64))
        self.assertEqual(one, bundle_runtime.ship_journey_record(raw("b" * 64)))
        shipped = json.loads(one)
        self.assertEqual(shipped["identity"], bundle_runtime.SHIPPED_IDENTITY)
        self.assertEqual(shipped["candidate_identity"], bundle_runtime.SHIPPED_IDENTITY)
        self.assertEqual(one, bundle_runtime.ship_journey_record(one))
        # unknown is refused, never shipped: unequal, empty, non text, or one of the two missing
        for bad in (raw("a" * 64, "b" * 64), raw(""), raw(7),
                    json.dumps({"journey": "J1", "identity": "a" * 64, "rows": {}}).encode(),
                    json.dumps({"journey": "J1", "candidate_identity": "a" * 64, "rows": {}}).encode()):
            with self.assertRaises(ValueError, msg=bad):
                bundle_runtime.ship_journey_record(bad)

    def test_core_journey_rows_name_real_rows_of_the_journey_file(self):
        with open(os.path.join(HERE, "journey", "journeys.json")) as fh:
            journeys = json.load(fh)
        for gid, e in self.load().items():
            if e["tier"] != "CORE":
                continue
            for ref in e["admission"]["journey_rows"]:
                jid, _, row = ref.partition("/")
                self.assertIn(row, [r["id"] for r in journeys[jid]["rows"]], (gid, ref))

    def test_the_bundle_ships_the_same_registry_and_admission_data(self):
        for name in ("REACH.json", "ADMISSION.json"):
            with open(os.path.join(ROOT, "docs", "plan", name), "rb") as a, \
                    open(os.path.join(ROOT, "bundle", "runtime", "data", name), "rb") as b:
                self.assertEqual(a.read(), b.read(), name)

    LOOP_IDS = ("loop.status", "loop.handoff", "loop.start", "loop.pause", "loop.resume", "loop.stop", "loop.close")

    def test_the_real_loop_groups_have_no_admission_record_so_a_consumer_never_reaches_a_loop_id(self):
        """ADM1.a (L-E): the REAL docs/plan/ADMISSION.json and the REAL consumer_admission, not a mocked view. While the
        loop groups carry no record every loop id is hidden; the day one is earned, this test goes red on purpose and
        the admission blockers named for loop.handoff and loop.close must be closed first."""
        self.assertNotIsInstance(door.consumer_admission, mock.Mock)  # a mocked view proves nothing about the file
        real = os.path.join(ROOT, "docs", "plan", door.ADMISSION_FILE)
        registry = os.path.join(ROOT, "docs", "plan", "REACH.json")
        self.assertEqual(os.path.realpath(registry), os.path.realpath(door.REACH_REGISTRY))
        groups = {g: self.load()[g] for g in ("loop-close", "loop-control")}
        self.assertEqual(set(self.LOOP_IDS), {c for e in groups.values() for c in e["capabilities"]})
        for gid, e in groups.items():
            self.assertNotIn("admission", e, gid)
            self.assertIn(e["tier"], ("AMPLIFIER",) + door.ADMISSION_HIDDEN_TIERS, gid)
        for cand in (REV, "0" * 40, None):
            for host in ("claude", "codex", None):
                view = door.consumer_admission(registry=registry, candidate=cand, host=host, admission_path=real)
                self.assertEqual([c for c in self.LOOP_IDS if door.capability_admitted(c, view)], [], (cand, host))
                self.assertTrue({"loop-close", "loop-control"} <= {q["id"] for q in view["queue"]}, (cand, host))
        with mock.patch.dict(os.environ, {"BROTHER_CANDIDATE": REV, "BROTHER_HOST": "claude"}), \
                mock.patch.object(door, "loop_invoke") as invoke:
            for cap in self.LOOP_IDS:
                rc, out = run_main(["--capability", cap, "--input", "reason=x", "--allow", "approve"])
                self.assertEqual((2, "BLOCKED"), (rc, json.loads(out)["state"]), cap)
        invoke.assert_not_called()

    def test_report_fails_when_a_door_capability_has_no_group(self):
        fx = Fixture(self, {"g1": entry()})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = reach_check.consumer_report(fx.registry, REV, HOST)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL assurance.", out.getvalue())

    def test_report_passes_on_the_shipped_file(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = reach_check.consumer_report(door.REACH_REGISTRY)
        self.assertEqual(rc, 0, out.getvalue()[:300])


if __name__ == "__main__":
    unittest.main()
