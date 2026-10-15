#!/usr/bin/env python3
"""PG1.a: tests for scripts/plan_seed.py and the ceremony's --emit-seed action.

Every case builds its own fixture tree (plan, specs, decisions, vault index, loop runs, cut states) in a
temporary directory and drives the real entry points: plan_seed functions and handover_ceremony.main with
--emit-seed. Nothing here reads the real vault, ~/.claude/evidence or docs/handover, and no case needs ps, gh or
a real HOME (HOME is an empty temporary directory for the whole run). The one case that reads the real plan is
skipped on a tree without docs/plan/specs. No em or en dashes.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import handover_ceremony as HC  # noqa: E402
import plan_seed as PS  # noqa: E402

NOW = 1791990000.0  # a fixed clock for generated_at
OLD = 1700000000.0  # 2023, older than any plan date used here


def unit(uid, state="OPEN", **kw):
    u = {"id": uid, "title": "title " + uid, "objective": "objective " + uid, "state": state,
         "owns": ["scripts/%s.py" % uid], "after": [], "source": "src " + uid, "reads": [],
         "remains": "everything", "spec": "docs/plan/specs/%s.md" % uid}
    u.update(kw)
    return u


def plan_of(*units, **kw):
    p = {"schema": "brother-release-wbs/1", "as_of": "2026-10-14 (fixture)", "units": list(units)}
    p.update(kw)
    return p


class World:
    """One fixture tree: a root with docs/plan, a source file for reads, loop runs, cut states."""

    def __init__(self, tmp, plan=None, decisions=None):
        self.tmp = tmp
        self.root = os.path.join(tmp, "repo")
        self.runs = os.path.join(tmp, "runs")
        self.cut = os.path.join(tmp, "cut")
        for d in (os.path.join(self.root, "docs", "plan", "specs", "9.9.9"), os.path.join(self.root, "scripts"),
                  self.runs, self.cut, os.path.join(self.root, "docs", "plan", "seeds", "9.9.10")):
            os.makedirs(d)
        self.plan_rel = "docs/plan/BROTHER-9.9.9-WBS.json"
        self.write(self.plan_rel, json.dumps(plan if plan is not None else plan_of(unit("AA1"))))
        if decisions is not None:
            self.write("docs/plan/specs/9.9.9/PLAN-DECISIONS.md", decisions)
        self.write("scripts/code.py", "\n".join("line %d" % i for i in range(1, 101)) + "\n")
        self.seed = os.path.join(self.root, "docs", "plan", "seeds", "9.9.10", "SEED.json")

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def run(self, extra=None, seed=True, plan_env=None):
        """handover_ceremony.main with --emit-seed against this world; returns (exit code, stdout)."""
        argv = (["--emit-seed", self.seed] if seed else []) + [
            "--root", self.root, "--runs", self.runs, "--cut", self.cut] + (extra or [])
        return run_main(argv, plan_env or self.plan_rel)

    def loaded(self):
        with open(self.seed, encoding="utf-8") as f:
            return json.load(f)

    def run_dir(self, name, status, age=0.0):
        d = os.path.join(self.runs, name)
        os.makedirs(d)
        path = os.path.join(d, "STATUS")
        with open(path, "w", encoding="utf-8") as f:
            f.write(status + "\n")
        os.utime(path, (NOW - age, NOW - age))
        return path

    def cut_state(self, name, phases, mtime=None):
        path = os.path.join(self.cut, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"phases": phases}, f)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path


def run_main(argv, plan_env):
    out = io.StringIO()
    old = os.environ.get("BROTHER_WBS")
    os.environ["BROTHER_WBS"] = plan_env
    try:
        with contextlib.redirect_stdout(out):
            code = HC.main(argv)
    finally:
        if old is None:
            os.environ.pop("BROTHER_WBS", None)
        else:
            os.environ["BROTHER_WBS"] = old
    return code, out.getvalue()


class SeedCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._home = os.environ.get("HOME")
        os.environ["HOME"] = os.path.join(self.tmp, "emptyhome")  # an empty HOME: nothing may lean on this laptop
        os.makedirs(os.environ["HOME"])

    def tearDown(self):
        if self._home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home
        self._tmp.cleanup()


class TestParkedUnits(SeedCase):
    def test_parked_and_deferred_units_seed_the_next_plan(self):
        plan = plan_of(unit("DN1", "DONE"), unit("DF1", "DEFERRED", owns=["a.py", "b.py"], after=["DN1"]),
                       unit("PK1", "PARKED"), unit("OP1", "OPEN"), unit("OP2", "OPEN", remains="nothing"))
        rows = PS.parked_units(plan)
        self.assertEqual([r["id"] for r in rows], ["DF1", "PK1", "OP1"])
        df = rows[0]
        self.assertEqual((df["owns"], df["after"], df["source"], df["title"]),
                         (["a.py", "b.py"], ["DN1"], "src DF1", "title DF1"))
        self.assertEqual(df["reason"], "state DEFERRED")
        self.assertEqual(rows[2]["reason"], "state OPEN, remains everything")
        for r in rows:
            for key in ("id", "title", "objective", "owns", "after", "source", "reads", "spec_text", "reason"):
                self.assertIn(key, r)

    def test_a_carried_row_keeps_its_landing_evidence(self):
        u = unit("OP1", "OPEN")
        u["evidence"] = "OP1.a landed 2026-10-14: green."
        row = PS.parked_units(plan_of(u))[0]
        self.assertEqual(row["evidence"], "OP1.a landed 2026-10-14: green.")
        self.assertEqual(PS.parked_units(plan_of(unit("OP2", "OPEN")))[0]["evidence"], "")

    def test_exactly_one_and_zero_parked_units(self):
        self.assertEqual(len(PS.parked_units(plan_of(unit("DF1", "DEFERRED")))), 1)
        self.assertEqual(PS.parked_units(plan_of(unit("DN1", "DONE"))), [])
        self.assertEqual(PS.parked_units(plan_of()), [])

    def test_an_unknown_state_is_carried_not_dropped(self):
        rows = PS.parked_units(plan_of(unit("ZZ1", "SOMETHING")))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reason"], "unknown state SOMETHING")
        self.assertEqual(len(PS.parked_units(plan_of(unit("ZZ2", None)))), 1)

    def test_spec_text_is_carried_only_for_a_spec_that_scored_nine(self):
        w = World(self.tmp)
        w.write("docs/plan/specs/AA1.md", "SPEC TEXT")
        scored = PS.parked_units(plan_of(unit("AA1", "DEFERRED", spec_score=9)), w.root)[0]
        low = PS.parked_units(plan_of(unit("AA1", "DEFERRED", spec_score=8)), w.root)[0]
        none = PS.parked_units(plan_of(unit("AA1", "DEFERRED")), w.root)[0]
        gone = PS.parked_units(plan_of(unit("AA9", "DEFERRED", spec_score=9)), w.root)[0]
        self.assertEqual(scored["spec_text"], "SPEC TEXT")
        self.assertEqual((low["spec_text"], none["spec_text"], gone["spec_text"]), (None, None, None))

    def test_a_malformed_plan_is_not_a_record(self):
        for bad in (None, [], "text", {}, {"units": "x"}, {"units": [5]}, {"units": [{"state": "OPEN"}]},
                    {"units": [dict(unit("A"), owns="scripts/a.py")]}, {"units": [dict(unit("A"), owns=None)]},
                    {"units": [dict(unit("A"), after="x")]}, {"units": [dict(unit(" "))]}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                PS.parked_units(bad)

    def test_every_row_names_its_origin(self):
        for r in PS.parked_units(plan_of(unit("DF1", "DEFERRED"))):
            self.assertEqual(r["origin"], "plan:DF1")


class TestCarriedRows(SeedCase):
    """The seam fix: a parked row is a CARRIED unit, so it keeps its id, its sub units, its worker and its spec."""

    FULL = dict(sub_units=["DF1.a", "DF1.b"], worker="a named worker", done_check="python3 -B scripts/test_df1.py",
                checker="the checker", spec="docs/plan/specs/DF1.md")

    def test_a_parked_row_is_a_carried_row_with_its_sub_units_worker_spec_and_done_check(self):
        row = PS.parked_units(plan_of(unit("DF1", "DEFERRED", **self.FULL)), None, "docs/plan/BROTHER-9.9.9-WBS.json")[0]
        self.assertIs(row["carried"], True)
        self.assertEqual((row["sub_units"], row["worker"], row["spec"], row["done_check"], row["checker"]),
                         (["DF1.a", "DF1.b"], "a named worker", "docs/plan/specs/DF1.md", "python3 -B scripts/test_df1.py", "the checker"))
        self.assertEqual(row["from_plan"], "docs/plan/BROTHER-9.9.9-WBS.json")

    def test_a_row_given_no_closing_plan_reads_no_data_for_it(self):
        for given in (None, "", "  ", 7):
            self.assertEqual(PS.parked_units(plan_of(unit("DF1", "DEFERRED")), None, given)[0]["from_plan"], PS.NO_DATA, repr(given))
        row = PS.parked_units(plan_of(unit("DF1", "DEFERRED")))[0]
        self.assertEqual((row["sub_units"], row["worker"], row["done_check"]), ([], "", ""))

    def test_malformed_sub_units_are_not_a_record(self):
        for bad in ("DF1.a", [1], [["DF1.a"]], {"a": 1}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                PS.parked_units(plan_of(unit("DF1", "DEFERRED", sub_units=bad)))

    def test_a_proposed_row_is_never_carried(self):
        seed = PS.build_seed("9.9.10", plan_of(unit("DF1", "DEFERRED")), "# Parked\n- one\n", None, [], {}, NOW, None, "docs/plan/x.json")
        carried = [u["id"] for u in seed["units"] if u.get("carried")]
        self.assertEqual(carried, ["DF1"])
        self.assertTrue(all("carried" not in u for u in seed["units"] if u["id"].startswith("PROPOSED-")))

    def test_build_seed_carries_the_given_closing_plan_and_the_selector_when_none_is_given(self):
        plan = plan_of(unit("DF1", "DEFERRED"))
        given = PS.build_seed("9.9.10", plan, None, None, [], {}, NOW, None, "docs/plan/given.json")
        self.assertEqual(given["units"][0]["from_plan"], "docs/plan/given.json")
        self.assertEqual(PS.build_seed("9.9.10", plan, None, None, [], {}, NOW)["units"][0]["from_plan"], PS.NO_DATA)  # no root: no guess
        old = os.environ.get("BROTHER_WBS")
        try:
            os.environ["BROTHER_WBS"] = "docs/plan/selected.json"
            self.assertEqual(PS.build_seed("9.9.10", plan, None, None, [], {}, NOW, self.tmp)["units"][0]["from_plan"],
                             "docs/plan/selected.json")
            os.environ["BROTHER_WBS"] = "/an/absolute/path.json"  # the selector refuses: NO-DATA, plan_gen then refuses
            self.assertEqual(PS.build_seed("9.9.10", plan, None, None, [], {}, NOW, self.tmp)["units"][0]["from_plan"], PS.NO_DATA)
        finally:
            if old is None:
                os.environ.pop("BROTHER_WBS", None)
            else:
                os.environ["BROTHER_WBS"] = old

    def test_the_ceremony_seed_carries_the_closing_plan_path(self):
        w = World(self.tmp, plan_of(unit("DF1", "DEFERRED", **self.FULL)))
        self.assertEqual(w.run()[0], 0)
        row = [u for u in w.loaded()["units"] if u["id"] == "DF1"][0]
        self.assertEqual((row["carried"], row["from_plan"], row["sub_units"]), (True, w.plan_rel, ["DF1.a", "DF1.b"]))


class TestCachedReads(SeedCase):
    def test_reads_are_cached_with_their_sha_or_read_no_data(self):
        w = World(self.tmp)
        rows = PS.cached_reads(["scripts/code.py main (line 10)"], w.root)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        want = "\n".join("line %d" % i for i in range(10, 50))
        self.assertEqual((r["path"], r["line"], r["text"]), ("scripts/code.py", 10, want))
        self.assertEqual(r["sha256"], hashlib.sha256(want.encode("utf-8")).hexdigest())
        short = PS.cached_reads(["scripts/code.py f (line 99)"], w.root)[0]
        self.assertEqual(short["text"], "line 99\nline 100\n")  # fewer than 40 lines left, the file's end

    def test_several_marks_and_parts_make_several_rows(self):
        w = World(self.tmp)
        rows = PS.cached_reads(["scripts/code.py a (line 3), b (line 60); scripts/code.py lines 70 to 72"], w.root)
        self.assertEqual([r["line"] for r in rows], [3, 60, 70])

    def test_a_missing_file_a_late_line_and_a_missing_line_are_no_data_by_name(self):
        w = World(self.tmp)
        rows = PS.cached_reads(["scripts/none.py f (line 1)", "scripts/code.py f (line 5000)",
                                "scripts/code.py header rules 1 to 3"], w.root)
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertTrue(r["text"].startswith("NO-DATA"), r)
            self.assertNotIn("sha256", r)
        self.assertEqual(PS.cached_reads(["scripts/code.py f (line 0)"], w.root)[0]["text"][:7], "NO-DATA")

    def test_a_path_outside_the_root_is_never_read(self):
        w = World(self.tmp)
        secret = os.path.join(self.tmp, "secret.txt")
        with open(secret, "w") as f:
            f.write("TOP SECRET\n")
        for entry in ("../secret.txt f (line 1)", "../../etc/passwd f (line 1)", secret + " f (line 1)"):
            r = PS.cached_reads([entry], w.root)[0]
            self.assertTrue(r["text"].startswith("NO-DATA"), r)
            self.assertNotIn("SECRET", json.dumps(r))
        os.symlink(secret, os.path.join(w.root, "scripts", "link.py"))
        self.assertTrue(PS.cached_reads(["scripts/link.py f (line 1)"], w.root)[0]["text"].startswith("NO-DATA"))

    def test_a_huge_line_is_cut_and_marked(self):
        w = World(self.tmp)
        w.write("scripts/big.js", "x" * 1000000 + "\nnext\n")
        r = PS.cached_reads(["scripts/big.js f (line 1)"], w.root)[0]
        self.assertTrue(r["truncated"])
        self.assertLess(len(r["text"]), 2000)

    def test_a_directory_or_a_binary_file_or_a_non_text_entry_is_no_data(self):
        w = World(self.tmp)
        with open(os.path.join(w.root, "scripts", "bin.dat"), "wb") as f:
            f.write(b"\xff\xfe\x00\x80\x81")
        rows = PS.cached_reads(["scripts f (line 1)", "scripts/bin.dat f (line 1)", 5, ""], w.root)
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertTrue(r["text"].startswith("NO-DATA"), r)
        with self.assertRaises(ValueError):
            PS.cached_reads("scripts/code.py f (line 1)", w.root)

    def test_a_parked_unit_carries_its_cached_reads_when_a_root_is_given(self):
        w = World(self.tmp)
        plan = plan_of(unit("DF1", "DEFERRED", reads=["scripts/code.py main (line 2)"]))
        self.assertEqual(PS.parked_units(plan, w.root)[0]["reads_cache"][0]["line"], 2)
        self.assertEqual(PS.parked_units(plan)[0]["reads_cache"], [])


DECISIONS = """# Plan decisions

## 5. Parked, and why

- RD2 is parked, P2 in the matrix.
- LP14.3 freeze roots: parked, no incident.

## 6. The stop rule

- not a finding

## 7. Files this plan could not verify

- scripts/x.py was not readable
  - nested bullet also counts
"""


class TestFindings(SeedCase):
    def test_open_findings_read_only_the_parked_and_could_not_verify_headings(self):
        rows = PS.open_findings(DECISIONS)
        self.assertEqual([r["text"][:6] for r in rows], ["RD2 is", "LP14.3", "script", "nested"])
        self.assertEqual({r["heading"] for r in rows}, {"5. Parked, and why", "7. Files this plan could not verify"})
        self.assertEqual(PS.open_findings("# PARKED\n- upper case heading\n")[0]["text"], "upper case heading")

    def test_empty_and_non_text_decisions(self):
        self.assertEqual(PS.open_findings(""), [])
        self.assertEqual(PS.open_findings("# Parked\n"), [])
        for bad in (None, 5, [], b"x"):
            with self.assertRaises(ValueError):
                PS.open_findings(bad)

    def test_a_bullet_cut_at_the_end_of_the_file_is_marked_truncated(self):
        rows = PS.open_findings("# Parked\n- whole\n- cut in")
        self.assertNotIn("truncated", rows[0])
        self.assertTrue(rows[1]["truncated"])
        self.assertNotIn("truncated", PS.open_findings("# Parked\n- whole\n- also whole\n")[1])

    def test_a_ten_thousand_character_bullet_is_capped(self):
        rows = PS.open_findings("# Parked\n- " + "w" * 10000 + "\n")
        self.assertLess(len(rows[0]["text"]), 700)

    def test_findings_and_evidence_become_proposed_rows(self):
        w = World(self.tmp)
        w.run_dir("U1-1", "WITHHELD brief refused", age=5)
        w.run_dir("U2-1", "LANDED x", age=5)
        w.run_dir("U3-1", "EXHAUSTED after 3 rounds", age=5)
        w.cut_state("driver-9.9.9-aaaa.json", {"freeze": {"started": 1, "verdict": "PASS"},
                                               "rehearsal": {"started": 2, "verdict": "FAIL", "detail": "NOT-READY"}})
        index = ("- [[slug-new]] 2026-10-15. AREA: a. SYMPTOM: s. FIX: f.\n"
                 "- [[slug-old]] 2026-01-01. AREA: b. SYMPTOM: old.\n")
        seed = PS.build_seed("9.9.10", plan_of(unit("DF1", "DEFERRED")), DECISIONS, index,
                             PS.evidence_findings(w.runs, w.cut), {}, NOW)
        ids = [u["id"] for u in seed["units"]]
        self.assertEqual(ids[0], "DF1")
        self.assertEqual(ids[1:], ["PROPOSED-%02d" % i for i in range(1, 9)])
        origins = [u["origin"] for u in seed["units"]]
        self.assertEqual([o.split(":")[0] for o in origins],
                         ["plan"] + ["finding"] * 4 + ["failure"] + ["evidence"] * 3)
        self.assertTrue(all(u["origin"] for u in seed["units"]))
        self.assertEqual([r["slug"] for r in seed["failures"]["rows"]], ["slug-new"])
        self.assertEqual(len(seed["findings"]), 4 + 3)

    def test_the_failure_index_is_cut_at_the_plan_date_and_reads_both_link_shapes(self):
        index = ("- [[a]] 2026-10-14. AREA: x. SYMPTOM: s.\n- [b](b.md) 2026-10-20. AREA: y.\n"
                 "- [[c]] 2026-10-13. AREA: z.\n" + "- [[big]] 2026-11-01. " + "w" * 10000 + "\n")
        rows = PS.failure_rows(index, "2026-10-14")
        self.assertEqual([r["slug"] for r in rows], ["a", "b", "big"])
        self.assertLess(len(rows[2]["text"]), 700)
        with self.assertRaises(ValueError):
            PS.failure_rows(None, "2026-10-14")

    def test_an_absent_failure_index_reads_no_data_not_empty(self):
        seed = PS.build_seed("9.9.10", plan_of(), DECISIONS, None, [], {}, NOW)
        src = {s["name"]: s for s in seed["sources"]}
        self.assertEqual(src["failures_index"]["status"], "NO-DATA")
        self.assertTrue(src["failures_index"]["why"])
        self.assertEqual(seed["failures"]["status"], "NO-DATA")
        # a present index with no failure since the plan is an honest PRESENT with zero rows
        seed = PS.build_seed("9.9.10", plan_of(), DECISIONS, "- [[o]] 2026-01-01. AREA: x.\n", [], {}, NOW)
        self.assertEqual(seed["failures"]["status"], "PRESENT")
        self.assertEqual(seed["failures"]["rows"], [])
        # an index with no plan date to cut it at cannot be trusted either
        seed = PS.build_seed("9.9.10", {"units": [], "as_of": "unknown"}, DECISIONS,
                             "- [[o]] 2026-10-15. AREA: x.\n", [], {}, NOW)
        self.assertEqual(seed["failures"]["status"], "NO-DATA")

    def test_an_absent_decisions_file_reads_no_data(self):
        seed = PS.build_seed("9.9.10", plan_of(), None, "", [], {}, NOW)
        self.assertEqual({s["name"]: s["status"] for s in seed["sources"]}["decisions"], "NO-DATA")


class TestEvidence(SeedCase):
    def test_only_withheld_exhausted_and_failed_runs_are_findings(self):
        w = World(self.tmp)
        paths = [w.run_dir("A", "WITHHELD x"), w.run_dir("B", "EXHAUSTED after 5 rounds"), w.run_dir("C", "LANDED z"),
                 w.run_dir("D", "PAUSED q"), w.run_dir("E", "UNFUNDED r")]
        os.makedirs(os.path.join(w.runs, "F"))  # a run with no STATUS file yet: not a finding
        rows = PS.evidence_findings(w.runs, w.cut)
        self.assertEqual([(r["kind"], r["status"], r["file"]) for r in rows],
                         [("loop-run", "WITHHELD", paths[0]), ("loop-run", "EXHAUSTED", paths[1])])

    def test_a_cut_state_counts_only_when_its_newest_phase_failed(self):
        w = World(self.tmp)
        w.cut_state("driver-1-a.json", {"freeze": {"started": 5, "verdict": "FAIL", "detail": "old"},
                                        "preflight": {"started": 9, "verdict": "PASS"}})  # a later PASS: recovered
        failing = w.cut_state("driver-1-b.json", {"freeze": {"started": 5, "verdict": "PASS"},
                                                  "preflight": {"started": 9, "verdict": "FAIL", "detail": "NOT-READY"}})
        w.cut_state("driver-1-c.lock", {"phases": {}})
        rows = PS.evidence_findings(w.runs, w.cut)
        self.assertEqual([(r["kind"], r["file"]) for r in rows], [("cut-state", failing)])
        self.assertIn("NOT-READY", rows[0]["text"])

    def test_old_evidence_before_since_is_left_out(self):
        w = World(self.tmp)
        w.run_dir("OLD", "WITHHELD old", age=NOW - OLD)
        w.run_dir("NEW", "WITHHELD new", age=1)
        w.cut_state("driver-1-old.json", {"p": {"started": 1, "verdict": "FAIL"}}, mtime=OLD)
        self.assertEqual(len(PS.evidence_findings(w.runs, w.cut)), 3)  # since 0: all
        rows = PS.evidence_findings(w.runs, w.cut, since=NOW - 100)
        self.assertEqual([os.path.basename(os.path.dirname(r["file"])) for r in rows], ["NEW"])

    def test_an_unreadable_directory_or_state_is_one_no_data_row_never_empty(self):
        w = World(self.tmp)
        rows = PS.evidence_findings(os.path.join(self.tmp, "nope"), w.cut)
        self.assertEqual([(r["kind"], r["status"]) for r in rows], [("loop-run", "NO-DATA")])
        rows = PS.evidence_findings(w.runs, os.path.join(self.tmp, "nope"))
        self.assertEqual([(r["kind"], r["status"]) for r in rows], [("cut-state", "NO-DATA")])
        for bad in ("not json", '{"phases": 5}', '{"phases": {"x": {"verdict": "FAIL"}}}', "[]"):
            with open(os.path.join(w.cut, "driver-bad.json"), "w") as f:
                f.write(bad)
            rows = PS.evidence_findings(w.runs, w.cut)
            self.assertEqual([r["status"] for r in rows], ["NO-DATA"], bad)

    def test_a_no_data_evidence_row_marks_the_source_and_makes_no_unit(self):
        row = {"kind": "loop-run", "file": "/x", "status": "NO-DATA", "text": "runs directory unreadable"}
        seed = PS.build_seed("9.9.10", plan_of(), "", "", [row], {}, NOW)
        self.assertEqual({s["name"]: s["status"] for s in seed["sources"]}["evidence"], "NO-DATA")
        self.assertEqual(seed["units"], [])
        self.assertEqual(seed["findings"], [row])

    def test_malformed_evidence_is_refused(self):
        for bad in ("x", [5], [{"kind": "a"}], [{"kind": "a", "file": 1, "status": "b", "text": "c"}]):
            with self.assertRaises(ValueError):
                PS.build_seed("9.9.10", plan_of(), "", "", bad, {}, NOW)


class TestBuildAndWrite(SeedCase):
    def test_the_seed_carries_every_schema_key(self):
        grant = {"model": "m", "budget": "b", "deadline": "d"}
        seed = PS.build_seed("9.9.10", plan_of(), "", "", [], grant, NOW)
        self.assertEqual(list(seed), ["schema", "version", "generated_at", "grant", "sources", "units", "findings",
                                      "failures"])
        self.assertEqual((seed["schema"], seed["version"]), ("brother-plan-seed/1", "9.9.10"))
        self.assertRegex(seed["generated_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_an_empty_seed_is_honest_and_every_source_is_present(self):
        grant = {"model": "m", "budget": "b", "deadline": "d"}
        seed = PS.build_seed("9.9.10", plan_of(), "", "", [], grant, NOW)
        self.assertEqual(seed["units"], [])
        self.assertEqual({s["status"] for s in seed["sources"]}, {"PRESENT"})

    def test_a_corrupt_plan_or_version_refuses(self):
        for bad in ("1.1", "v1.1.4", "1.1.4.1", "1.1.4-rc1", "", " 1.1.4", "1.1.4\n", None, 114):
            with self.assertRaises(ValueError, msg=repr(bad)):
                PS.build_seed(bad, plan_of(), "", "", [], {}, NOW)
        for bad in (None, [], "x", {"units": None}):
            with self.assertRaises(ValueError):
                PS.build_seed("1.1.5", bad, "", "", [], {}, NOW)
        w = World(self.tmp)
        w.write(w.plan_rel, "{not json")
        code, out = w.run()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)
        self.assertFalse(os.path.exists(w.seed))
        w.write(w.plan_rel, json.dumps(["a list"]))
        self.assertEqual(w.run()[0], 2)
        w.write("docs/plan/NOVERSION-WBS.json", json.dumps(plan_of()))
        self.assertEqual(w.run(plan_env="docs/plan/NOVERSION-WBS.json")[0], 2)
        self.assertEqual(w.run(plan_env="../escape.json")[0], 2)
        self.assertFalse(os.path.exists(w.seed))

    def test_the_grant_is_verbatim_or_no_data(self):
        grant = os.path.join(self.tmp, "BASELINE.md")
        with open(grant, "w", encoding="utf-8") as f:
            f.write('# prose line, ignored\nbase_sha | abc\nowner | model | "use opus, nothing above high"\n'
                    'owner | budget | "3 million tokens"\nowner | deadline | NO-DATA\n'
                    'row | x | y | PASS | exit 0 | PASS\n')
        self.assertEqual(PS.read_grant(grant), {"model": "use opus, nothing above high",
                                                "budget": "3 million tokens", "deadline": "NO-DATA"})
        for text in ('owner | model | "unbalanced\n', 'owner | model | unquoted words\n',
                     'owner | model | "a"\nowner | model | "b"\n', 'owner | model | "\n', 'owner | model |\n', "",
                     'owner | colour | "red"\n'):
            with open(grant, "w", encoding="utf-8") as f:
                f.write(text)
            self.assertEqual(PS.read_grant(grant)["model"], "NO-DATA", repr(text))
        with open(grant, "w", encoding="utf-8") as f:
            f.write('owner | model | "x"\nowner | model | "x"\n')
        self.assertEqual(PS.read_grant(grant)["model"], "x")  # a repeat that agrees is not a conflict
        with self.assertRaises(OSError):
            PS.read_grant(os.path.join(self.tmp, "none.md"))
        self.assertEqual(PS._grant(None), {"model": "NO-DATA", "budget": "NO-DATA", "deadline": "NO-DATA"})
        self.assertEqual(PS._grant({"model": 5, "budget": "  ", "deadline": "NO-DATA"}),
                         {"model": "NO-DATA", "budget": "NO-DATA", "deadline": "NO-DATA"})

    def test_the_grant_reaches_the_seed_through_the_ceremony(self):
        w = World(self.tmp)
        grant = os.path.join(self.tmp, "BASELINE.md")
        with open(grant, "w", encoding="utf-8") as f:
            f.write('owner | model | "words as spoken"\n')
        self.assertEqual(w.run(["--grant", grant])[0], 0)
        seed = w.loaded()
        self.assertEqual(seed["grant"], {"model": "words as spoken", "budget": "NO-DATA", "deadline": "NO-DATA"})
        self.assertEqual({s["name"]: s["status"] for s in seed["sources"]}["grant"], "NO-DATA")
        self.assertEqual(w.run(["--grant", os.path.join(self.tmp, "missing.md")])[0], 2)

    def test_a_seed_path_holding_another_schema_is_not_overwritten(self):
        w = World(self.tmp)
        for held in ('{"schema": "other/2"}', "not json at all", "[]", '{"no": "schema"}'):
            with open(w.seed, "w") as f:
                f.write(held)
            code, out = w.run()
            self.assertEqual(code, 1, held)
            self.assertIn("REFUSED", out)
            with open(w.seed) as f:
                self.assertEqual(f.read(), held)
        os.remove(w.seed)
        self.assertEqual(w.run()[0], 0)
        self.assertEqual(w.run()[0], 0)  # its own schema may be rewritten
        self.assertEqual(sorted(os.listdir(os.path.dirname(w.seed))), ["SEED.json"])  # no temporary name left

    def test_a_directory_path_a_missing_parent_and_a_bad_seed_record_refuse(self):
        seed = PS.build_seed("9.9.10", plan_of(), "", "", [], {}, NOW)
        with self.assertRaises((OSError, ValueError)):
            PS.write_seed(self.tmp, seed)
        with self.assertRaises(OSError):
            PS.write_seed(os.path.join(self.tmp, "no", "such", "dir", "SEED.json"), seed)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "no")))
        for bad in ({}, None, [], {"schema": "x"}):
            with self.assertRaises(ValueError):
                PS.write_seed(os.path.join(self.tmp, "S.json"), bad)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "S.json")))

    def test_a_kill_before_the_rename_leaves_no_seed_and_no_temporary_file(self):
        seed = PS.build_seed("9.9.10", plan_of(), "", "", [], {}, NOW)
        target = os.path.join(self.tmp, "out", "SEED.json")
        os.makedirs(os.path.dirname(target))
        real = os.replace

        def killed(a, b):
            raise OSError("killed")
        os.replace = killed
        try:
            with self.assertRaises(OSError):
                PS.write_seed(target, seed)
        finally:
            os.replace = real
        self.assertEqual(os.listdir(os.path.dirname(target)), [])

    def test_two_ceremonies_on_one_path_leave_one_whole_file(self):
        seed = PS.build_seed("9.9.10", plan_of(unit("A", "DEFERRED")), "", "", [], {}, NOW)
        target = os.path.join(self.tmp, "out", "SEED.json")
        os.makedirs(os.path.dirname(target))
        errors = []

        def go():
            try:
                for _ in range(20):
                    PS.write_seed(target, seed)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=go) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        with open(target) as f:
            self.assertEqual(json.load(f)["schema"], PS.SEED_SCHEMA)
        self.assertEqual(os.listdir(os.path.dirname(target)), ["SEED.json"])

    def test_the_same_inputs_give_the_same_bytes_but_the_clock(self):
        args = ("9.9.10", plan_of(unit("A", "DEFERRED")), DECISIONS, "", [], {})
        a = json.dumps(PS.build_seed(*args, now=NOW), indent=1)
        b = json.dumps(PS.build_seed(*args, now=NOW + 60), indent=1)
        self.assertNotEqual(a, b)
        self.assertEqual(a.splitlines()[:3] + a.splitlines()[4:], b.splitlines()[:3] + b.splitlines()[4:])


class TestCeremonyAction(SeedCase):
    def test_the_ceremony_action_writes_a_seed_for_the_next_version(self):
        plan = plan_of(unit("DN1", "DONE"), unit("DF1", "DEFERRED", reads=["scripts/code.py f (line 7)"]),
                       unit("OP1", "OPEN"))
        w = World(self.tmp, plan=plan, decisions=DECISIONS)
        code, out = w.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), "SEED: %s units 6 sources 3/5" % w.seed)
        seed = w.loaded()
        self.assertEqual((seed["schema"], seed["version"]), ("brother-plan-seed/1", "9.9.10"))
        self.assertEqual([u["id"] for u in seed["units"]][:2], ["DF1", "OP1"])
        self.assertEqual(seed["units"][0]["reads_cache"][0]["text"].split("\n")[0], "line 7")
        srcs = {s["name"]: s["status"] for s in seed["sources"]}
        self.assertEqual(srcs, {"plan": "PRESENT", "decisions": "PRESENT", "failures_index": "NO-DATA",
                                "evidence": "PRESENT", "grant": "NO-DATA"})

    def test_the_failure_index_file_reaches_the_seed(self):
        w = World(self.tmp)
        index = os.path.join(self.tmp, "Failures-Index.md")
        with open(index, "w") as f:
            f.write("- [[f1]] 2026-10-20. AREA: x. SYMPTOM: late.\n- [[f0]] 2026-01-01. AREA: y.\n")
        self.assertEqual(w.run(["--failures-index", index])[0], 0)
        self.assertEqual([r["slug"] for r in w.loaded()["failures"]["rows"]], ["f1"])
        self.assertEqual(w.run(["--failures-index", os.path.join(self.tmp, "gone.md")])[0], 2)

    def test_a_missing_decisions_file_and_missing_evidence_dirs_read_no_data_not_failure(self):
        w = World(self.tmp)
        shutil.rmtree(w.runs)
        code, out = w.run()
        self.assertEqual(code, 0, out)
        srcs = {s["name"]: s["status"] for s in w.loaded()["sources"]}
        self.assertEqual((srcs["decisions"], srcs["evidence"]), ("NO-DATA", "NO-DATA"))

    def test_the_four_existing_actions_keep_their_exit_codes(self):
        plan_env = "docs/plan/x.json"
        code, out = run_main([], plan_env)
        self.assertEqual(code, 2)
        self.assertIn("nothing to do", out)
        self.assertEqual(run_main(["--emit-patterns"], plan_env)[0], 2)
        lessons = os.path.join(self.tmp, "lessons.json")
        self.assertEqual(run_main(["--emit-vault", os.path.join(self.tmp, "v0"), "--lesson-file", lessons],
                                  plan_env)[0], 2)  # lesson file absent
        with open(lessons, "w") as f:
            json.dump([{"name": "a finding", "type": "finding", "description": "d", "what_happened": "w"}], f)
        code, out = run_main(["--emit-vault", os.path.join(self.tmp, "v1"), "--lesson-file", lessons], plan_env)
        self.assertEqual(code, 0, out)
        with open(lessons, "w") as f:
            json.dump([{"name": "a failure", "type": "failure", "symptom": "boom is loud", "what_happened": "w",
                        "applies_to": ["scripts/a.py"]}], f)
        code, out = run_main(["--emit-vault", os.path.join(self.tmp, "v2"), "--lesson-file", lessons], plan_env)
        self.assertEqual(code, 1, out)  # FLAGGED: no Failures-Index in the vault directory
        self.assertNotIn("SEED:", out)

    def test_the_seed_action_combines_with_another_by_the_worst_code(self):
        w = World(self.tmp)
        code, out = w.run(["--emit-patterns"])  # patterns without a vault is exit 2, the seed still writes
        self.assertEqual(code, 2)
        self.assertTrue(os.path.exists(w.seed))
        self.assertIn("SEED:", out)

    def test_the_seed_action_alone_does_not_trip_nothing_to_do(self):
        w = World(self.tmp)
        self.assertEqual(w.run()[0], 0)
        self.assertEqual(run_main(["--root", w.root], w.plan_rel)[0], 2)  # no action named: still nothing to do

    @unittest.skipUnless(os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs")), "docs/plan/specs is hub only")
    def test_the_real_plan_seeds_a_next_version_in_a_scratch_directory(self):
        wbs = "docs/plan/BROTHER-1.1.4-WBS.json"
        if not os.path.isfile(os.path.join(ROOT, wbs)):
            self.skipTest("the 1.1.4 plan is not in this tree")
        out_dir = os.path.join(self.tmp, "out")
        os.makedirs(out_dir)
        seed_path = os.path.join(out_dir, "SEED.json")
        code, out = run_main(["--emit-seed", seed_path, "--root", ROOT, "--runs", os.path.join(self.tmp, "r"),
                              "--cut", os.path.join(self.tmp, "c")], wbs)
        self.assertEqual(code, 0, out)
        with open(seed_path) as f:
            seed = json.load(f)
        self.assertEqual(seed["version"], "1.1.5")
        self.assertTrue(all(u["origin"] for u in seed["units"]))
        self.assertGreater(len(seed["units"]), 0)


if __name__ == "__main__":
    unittest.main()
