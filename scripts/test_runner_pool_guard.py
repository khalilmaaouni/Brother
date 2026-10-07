#!/usr/bin/env python3
"""The pool must not start a worker on an unscored spec, and the budget cap must bind.

WHY THIS ONE. runner_pool.py is the loop's admission controller: it is the only thing deciding
which units get a worker and how many exist at once. Both of its failure directions cost real
money and neither raises anything.

  1. STARTING ON AN UNSCORED SPEC. The owner's rule is that no work begins under the spec floor,
     and the module's own comment says an absent score is not a pass. If an unscored sub unit
     ever starts, a model is paid to build against a specification nobody scored, the build is
     graded, probed and repaired, and the whole lane is waste that looks exactly like work.
  2. THE CAP NOT BINDING. BURN_CAP and WIP are what convert throughput into closures, and the
     module records the measurement behind them. A pool that starts every eligible unit spends
     the night's budget in one pass, and the only symptom is the bill.

Both are pinned here through the process boundary, because the module does its work at import
time and has no importable entry point. The fixture is a plan and a score file built in a temp
directory with HOME pointed at it, so this reads no live repository document and needs nothing
in the real HOME: the absent ~/.claude/bin helpers are exactly the degraded case the module
says it handles, and --dry means no runner is ever launched.

Run: python3 scripts/test_runner_pool_guard.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
import unittest
import contextlib
import importlib.util
import io
from typing import Optional

POOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop", "runner_pool.py")


def unit(uid, subs, evidence=""):
    """A fabricated unit. Its id never names a plan_store.SUPPLY unit (D2, FX-31, PR1, OP1, MG1) unless the case is about the
    owner's proof supply: the pool holds every unnamed sub unit of those units (2026-10-04), so a D2.1 fixture never starts."""
    if not isinstance(uid, str) or not uid:
        raise ValueError("unit id must be a non-empty string")
    if not isinstance(subs, list):
        raise ValueError("sub_units must be a list")
    for _s in subs:
        if not isinstance(_s, str) or not _s:
            raise ValueError("sub unit id must be a non-empty string")
    if not isinstance(evidence, str):
        raise ValueError("evidence must be a string")
    return {"id": uid, "state": "OPEN", "spec": "docs/plan/specs/%s.md" % uid,
            "sub_units": list(subs), "evidence": evidence}


class RunnerPoolAdmission(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="runner-pool-guard-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(self.home, ".claude", "evidence"))
        os.makedirs(os.path.join(self.dir, "docs", "plan", "specs"))
        self.fund(8)

    def fund(self, lanes):
        """The money guard these admission cases need, stated rather than borrowed: an unreadable guard admits nothing
        since 2026-09-27 (it used to admit a floor of 2, which is what these fixtures silently relied on). lanes None
        removes the guard, so the pool's own refusal is what a case reads."""
        b = os.path.join(self.home, ".claude", "bin"); os.makedirs(b, exist_ok=True)
        p = os.path.join(b, "burn_guard.py")
        if lanes is None:
            if os.path.exists(p): os.unlink(p)
            return
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('print("FUNDING OK fixture")\nprint("LANES   %d")\nprint(%d)\n' % (lanes, lanes))

    def test_a_unit_with_an_empty_supply_starts_none_of_its_sub_units(self):
        """owner 2026-10-04: of a widened unit only the named sub units are loop supply (plan_store.SUPPLY); owner
        2026-10-05: D2.7 left it (a reviewed hand-route item), so D2's supply is empty and neither D2.6 nor D2.7 starts."""
        out = self.run_pool([unit("D2", ["D2.6", "D2.7"])], scores={"D2.6": 10, "D2.7": 10})
        self.assertNotRegex(out, r"(?m)^D2\s+D2\.7\s+START", out)
        self.assertNotRegex(out, r"(?m)^D2\s+D2\.6\s+START", out)

    def test_an_unnamed_mg1_sub_unit_is_held_and_mg1_e_starts(self):
        """decision proof-supply-mg1.json (2026-10-04): MG1 joined the supply for MG1.e and MG1.f only (MG1.f left it
        on 2026-10-06, refused by the cut condition review after the loop landed it). MG1.a, first in
        the list and unlanded in this fixture, must be held so the pool starts MG1.e, never MG1.a."""
        out = self.run_pool([unit("MG1", ["MG1.a", "MG1.e"])], scores={"MG1.a": 10, "MG1.e": 10})
        self.assertRegex(out, r"(?m)^MG1\s+MG1\.e\s+START", out)
        self.assertNotRegex(out, r"(?m)^MG1\s+MG1\.a\s+START", out)

    def test_admissible_refuses_a_sub_unit_the_owner_kept_from_the_loop(self):
        """The shared admission path (salvage promote, diag_apply) refuses the same sub units as the pool's own hold."""
        spec = importlib.util.spec_from_file_location("runner_pool_supply_%d" % id(self), POOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.spec("D2", age=7200, subs=["D2.6", "D2.7"])
        plan = {"units": [dict(unit("D2", ["D2.6", "D2.7"]), spec=os.path.join(self.dir, "docs", "plan", "specs", "D2.md"))]}
        self.assertTrue(mod.admissible(plan, "D2.6").startswith("held:"), mod.admissible(plan, "D2.6"))
        self.assertTrue(mod.admissible(plan, "D2.7").startswith("held:"), mod.admissible(plan, "D2.7"))

    def test_an_unreadable_money_guard_admits_no_runner(self):
        self.fund(None)
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9})
        self.assertRegex(out, r"(?m)^BUDGET cap 0 runner", out)
        self.assertNotRegex(out, r"(?m)^D3\s+D3\.1\s+START", out)

    def write_fixtures(self, units, scores=None, autospec=True):
        """The plan file, one old spec section per sub unit and the score store: the three files every pool run in
        this suite reads. run_pool (the process route) and pool_pass (the in process route ACC3.b adds, which must
        also move the module constant WIP_STATUS) both write them here, so both routes read one fixture shape.
        autospec writes an old spec section per sub unit naming one file of its own (the touch set of row 17 needs
        a readable path, and an unreadable one blocks by design) unless the test wrote that unit's spec itself."""
        with open(os.path.join(self.dir, "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"), "w", encoding="utf-8") as fh:
            json.dump({"units": units}, fh)
        if autospec:
            for u in units:
                if not os.path.exists(os.path.join(self.dir, u["spec"])):
                    self.spec(u["id"], age=7200, subs=u["sub_units"])
        path = os.path.join(self.home, ".claude", "evidence", "spec-scores.json")
        if scores is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({k: {"score": v, "missing": ["FILES: no backticked file"]} for k, v in scores.items()}, fh)

    def run_pool(self, units, scores=None, env=None, autospec=True, dry=True):
        """One --dry pass over a fabricated plan. Returns its stdout; a nonzero exit is a failure here.
        The fixture is isolated from the loop's own launch before a case's own env is applied: every BROTHER_ and
        GIT_ variable is cleared, so a pass reads the default scope, the default exploration width and the fixture
        HOME rather than whatever the run that started this suite had set. BROTHER_WIP_GATE is then "off", so no
        existing case reads the real finish-first limits (gh and git, 60 s timeouts each); an ACC3.b case that wants
        them sets its own value through env and goes through pool_pass."""
        self.write_fixtures(units, scores, autospec)
        e = dict(os.environ, HOME=self.home, PYTHONDONTWRITEBYTECODE="1")
        for k in [k for k in list(e) if k.startswith("BROTHER_") or k.startswith("GIT_")]:
            e.pop(k)
        e["BROTHER_WIP_GATE"] = "off"
        e.pop("BROTHER_WIP", None); e.pop("BROTHER_SPEC_FLOOR", None)
        for k in [k for k in e if k.startswith("GIT_")]:
            e.pop(k)
        e.update(env or {})
        r = subprocess.run([sys.executable, "-B", POOL] + (["--dry"] if dry else []), cwd=self.dir, env=e,
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def started(self, out):
        return set(re.findall(r"^\S+\s+(\S+)\s+START$", out, flags=re.M))

    def plant_runner(self, sub, pid):
        """A run folder that says a runner is on it: PID file, no STATUS. What unit_runner writes at its start."""
        d = os.path.join(self.home, ".claude", "evidence", "unit-runs", "%s-000001" % sub)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "PID"), "w", encoding="utf-8") as fh:
            fh.write("%d\n" % pid)

    def plant_exhausted(self, sub, word="EXHAUSTED", age=3600.0):
        """A finished run that parked its sub unit `age` seconds ago: STATUS written, folder mtime set to then."""
        d = os.path.join(self.home, ".claude", "evidence", "unit-runs", "%s-000001" % sub); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as fh: fh.write("%s after 5 rounds\n" % word)
        then = time.time() - age; os.utime(d, (then, then))

    def spec(self, uid, age, subs=None, paths=None):
        """A spec file with one section per sub unit; each names `paths` (default: one file of the sub unit's own)."""
        p = os.path.join(self.dir, "docs", "plan", "specs", "%s.md" % uid)
        subs = list(subs or ["%s.1" % uid])
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# %s\n" % uid)
            for s in subs:
                fh.write("### %s\n%s existing\n" % (s, " ".join("`%s`" % q for q in (paths if paths is not None else ["scripts/%s.py" % s.replace(".", "_")]))))
                fh.write("Done check: `python3 -B scripts/test_%s.py`\n" % s.replace(".", "_"))   # one command the landing gate accepts
        then = time.time() - age; os.utime(p, (then, then))

    def build(self, sub, paths, age=60.0, folder="000002"):
        """A worker build on disk for this sub unit whose edits touch `paths`, `age` seconds old."""
        d = os.path.join(self.home, ".claude", "evidence", "unit-runs", "%s-%s" % (sub, folder), "round0", "out"); os.makedirs(d, exist_ok=True)
        b = os.path.join(d, "%s-r0-build.json" % sub)
        with open(b, "w", encoding="utf-8") as fh: json.dump({"edits": [{"path": q, "find": "x", "replace": "y"} for q in paths], "tests": []}, fh)
        then = time.time() - age; os.utime(b, (then, then))
        with open(os.path.join(os.path.dirname(os.path.dirname(d)), "STATUS"), "w", encoding="utf-8") as fh: fh.write("EXHAUSTED after 5 rounds\n")

    def judge(self, name, age):
        b = os.path.join(self.home, ".claude", "bin"); os.makedirs(b, exist_ok=True)
        with open(os.path.join(b, name), "w", encoding="utf-8") as fh: fh.write("# judge\n")
        then = time.time() - age; os.utime(os.path.join(b, name), (then, then))

    # THE DRAIN OF 2026-09-22: every unit parked as EXHAUSTED, nothing readmitted them, the run ended with money left.
    def test_the_default_scope_admits_every_unit(self):
        """Owner 2026-09-27, a hard lesson: a default never narrows the work. With no BROTHER_SCOPE a unit of any family
        is admitted, and the pass says it admits all open units."""
        out = self.run_pool([unit("Z9", ["Z9.1"])], scores={"Z9.1": 10})
        self.assertIn("Z9.1", self.started(out), out)
        self.assertRegex(out, r"(?m)^SCOPE   \. \| admits 1 of 1 open units$", out)
    def test_an_empty_status_file_never_kills_the_pool(self):
        """H3 (2026-09-24): a full disk once left an empty STATUS; the pool then died with IndexError on every pass.
        It reads NO-DATA, the pass completes, and the sub unit is neither restarted nor counted as parked."""
        d = os.path.join(self.home, ".claude", "evidence", "unit-runs", "D3.1-000001"); os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "STATUS"), "w").close()
        self.spec("D3", age=7200); self.judge("unit_runner.py", age=7200)
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertNotIn("IndexError", out); self.assertNotIn("D3.1", self.started(out))

    def test_an_exhausted_sub_unit_stays_parked_when_nothing_changed_after_its_run(self):
        self.plant_exhausted("D3.1", age=3600); self.spec("D3", age=7200); self.judge("unit_runner.py", age=7200)
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertIn("needs a fact", out); self.assertNotIn("D3.1", self.started(out))

    def test_a_spec_changed_after_the_run_readmits_it(self):
        self.plant_exhausted("D3.1", age=3600); self.spec("D3", age=60)
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertIn("RETRY", out); self.assertIn("D3.1", self.started(out))

    def test_a_spec_under_fixes_changed_after_the_run_readmits_it(self):
        """2026-09-30: an FX unit's spec field names docs/plan/specs/fixes/<id>.md, and fact_time read only the guessed
        specs/<id>.md, so FX-13.1 stayed WITHHELD after its section was repaired. The unit's own spec field is a fact."""
        self.plant_exhausted("FX9.1", word="WITHHELD", age=3600); self.judge("unit_runner.py", age=7200)
        os.makedirs(os.path.join(self.dir, "docs", "plan", "specs", "fixes"), exist_ok=True)
        self.spec("FX9", age=60)
        os.rename(os.path.join(self.dir, "docs", "plan", "specs", "FX9.md"), os.path.join(self.dir, "docs", "plan", "specs", "fixes", "FX9.md"))
        u = unit("FX9", ["FX9.1"]); u["spec"] = "docs/plan/specs/fixes/FX9.md"
        out = self.run_pool([u], scores={"FX9.1": 10})
        self.assertIn("RETRY", out); self.assertIn("FX9.1", self.started(out))

    def test_a_spec_under_fixes_unchanged_since_the_run_stays_parked(self):
        """The same fixture with the spec OLDER than the run: the new path must not readmit on its own."""
        self.plant_exhausted("FX9.1", word="WITHHELD", age=3600); self.judge("unit_runner.py", age=7200)
        os.makedirs(os.path.join(self.dir, "docs", "plan", "specs", "fixes"), exist_ok=True)
        self.spec("FX9", age=7200)
        os.rename(os.path.join(self.dir, "docs", "plan", "specs", "FX9.md"), os.path.join(self.dir, "docs", "plan", "specs", "fixes", "FX9.md"))
        u = unit("FX9", ["FX9.1"]); u["spec"] = "docs/plan/specs/fixes/FX9.md"
        out = self.run_pool([u], scores={"FX9.1": 10})
        self.assertIn("needs a fact", out); self.assertNotIn("FX9.1", self.started(out))

    def test_a_judging_tool_changed_after_the_run_readmits_it(self):
        self.plant_exhausted("D3.1", age=3600); self.spec("D3", age=7200); self.judge("check_wave.py", age=60)
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertIn("D3.1", self.started(out))

    def test_a_withheld_sub_unit_follows_the_same_rule(self):
        self.plant_exhausted("D3.1", word="WITHHELD", age=3600); self.spec("D3", age=60)
        self.assertIn("D3.1", self.started(self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})))

    def test_a_withheld_sub_unit_stays_parked_when_nothing_changed(self):
        self.plant_exhausted("D3.1", word="WITHHELD", age=3600); self.spec("D3", age=7200)
        self.assertNotIn("D3.1", self.started(self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})))

    def test_no_readable_spec_and_no_judge_parks_rather_than_guessing(self):
        self.plant_exhausted("D3.1", age=3600)              # nothing to compare against: fact time is 0.0
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10}, autospec=False)
        self.assertNotIn("D3.1", self.started(out))

    def test_a_changed_brief_shaper_readmits_a_parked_sub_unit(self):
        self.plant_exhausted("D3.1", age=3600); self.spec("D3", age=7200); self.judge("probe_brief.py", age=60)
        self.assertIn("D3.1", self.started(self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})))

    def test_a_changed_brief_builder_readmits_a_parked_sub_unit(self):
        # 2026-09-24 12:5x: D3.7 and D1.8 parked on briefs that showed no module under test; the builder is what changed
        self.plant_exhausted("D3.1", age=3600); self.spec("D3", age=7200); self.judge("build_brief.py", age=60)
        self.assertIn("D3.1", self.started(self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})))

    def test_a_changed_brief_gate_readmits_a_parked_sub_unit(self):
        self.plant_exhausted("D3.1", age=3600); self.spec("D3", age=7200); self.judge("brief_check.py", age=60)
        self.assertIn("D3.1", self.started(self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})))

    def test_a_changed_slicer_readmits_a_parked_sub_unit(self):
        self.plant_exhausted("D3.1", age=3600); self.spec("D3", age=7200); self.judge("slicer.py", age=60)
        self.assertIn("D3.1", self.started(self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})))

    def test_a_tool_that_is_not_a_judge_does_not_readmit(self):
        self.plant_exhausted("D3.1", age=3600); self.spec("D3", age=7200); self.judge("pass_digest.py", age=60)
        self.assertNotIn("D3.1", self.started(self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})))

    def test_a_live_runner_is_seen_from_its_own_folder_never_from_the_process_table(self):
        # R4: the pool reads its own evidence, so a fixture HOME sees exactly the runners planted in it
        self.plant_runner("D3.1", os.getpid())            # this test process: alive
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertIn("1 alive", out)
        self.assertIn("skip: runner alive", out)
        self.assertNotIn("D3.1", self.started(out))

    def test_a_dead_runner_frees_its_unit(self):
        self.plant_runner("D3.1", 2 ** 22 - 7)              # no such process
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertIn("0 alive", out)
        self.assertIn("D3.1", self.started(out))

    def test_an_empty_pid_file_is_a_runner_between_mkdir_and_its_write_so_it_counts_alive(self):
        d = os.path.join(self.home, ".claude", "evidence", "unit-runs", "D3.1-000001"); os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "PID"), "w").close()
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertIn("1 alive", out); self.assertNotIn("D3.1", self.started(out))

    def test_a_six_hour_old_run_with_no_status_is_dead_whatever_its_pid_says(self):
        self.plant_runner("D3.1", 1)                        # pid 1: EPERM, reads as alive by signal alone
        d = os.path.join(self.home, ".claude", "evidence", "unit-runs", "D3.1-000001")
        old = time.time() - 7 * 3600; os.utime(d, (old, old))
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertIn("0 alive", out)

    def test_a_finished_run_with_a_status_is_not_alive_whatever_its_pid_says(self):
        self.plant_runner("D3.1", os.getpid())
        with open(os.path.join(self.home, ".claude", "evidence", "unit-runs", "D3.1-000001", "STATUS"), "w", encoding="utf-8") as fh:
            fh.write("EXHAUSTED after 5 rounds\n")
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 10})
        self.assertIn("0 alive", out)

    def test_an_unscored_spec_never_starts_a_worker(self):
        # the whole spec file is missing from the score store: absent is not a pass
        out = self.run_pool([unit("D3", ["D3.1"])], scores=None)
        self.assertEqual(self.started(out), set(), out)
        self.assertIn("unscored", out)

    def test_a_score_under_the_floor_never_starts_a_worker(self):
        for score in (0, 5, 7):
            with self.subTest(score=score):
                out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": score})
                self.assertEqual(self.started(out), set(), out)
                self.assertIn("SPEC %d/10" % score, out)

    def owned_spec(self, body):
        """A spec whose one section is `body`: these cases need Owns:, Files: and Reads: lines the spec helper does not write."""
        p = os.path.join(self.dir, "docs", "plan", "specs", "D3.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# D3\n### D3.1\n%s\nDone check: `python3 -B scripts/test_d3_new.py`\n" % body)
        then = time.time() - 7200; os.utime(p, (then, then))

    def test_a_sub_unit_that_writes_a_landing_trusted_path_never_starts_a_worker(self):
        # 2026-10-04: CV1.d's native session ran to DONE and the adapter then refused scripts/cut_preflight.py
        for head in ("Owns:", "Files:"):
            for path in ("scripts/cut_preflight.py", "scripts/loop/model_call.py", "scripts/bundle_runtime.py", "docs/plan/specs/X.md"):
                with self.subTest(head=head, path=path):
                    self.owned_spec("%s `scripts/d3_new.py` (NEW), `%s` (existing, one row added)" % (head, path))
                    out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9}, autospec=False)
                    self.assertEqual(self.started(out), set(), out)
                    self.assertRegex(out, r"(?m)^D3\s+D3\.1\s+skip: REVIEWED-ROUTE: path is one the landing trusts", out)

    def test_a_protected_path_the_sub_unit_only_reads_or_mentions_does_not_route_it(self):
        # review 2026-10-04: screening every mentioned path routed 33 of 57 sub units, PR1.f on a prose mention
        for body in ("Owns: `scripts/d3_new.py` (NEW). Reads: `scripts/cut_preflight.py`",
                     "Owns: `scripts/d3_new.py` (NEW)\n\nThe preflight in `scripts/cut_preflight.py` asks it later.",
                     "Files: `scripts/d3_new.py` (NEW); `scripts/cut_preflight.py` (existing, read only)",
                     "No owns line; prose names `scripts/cut_preflight.py` and `scripts/d3_new.py`."):
            with self.subTest(body=body):
                self.owned_spec(body)
                out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9}, autospec=False)
                self.assertEqual(self.started(out), {"D3.1"}, out)

    def _with_build(self, body, paths):
        self.owned_spec(body)
        self.build("D3.1", paths, folder="000003")
        os.remove(os.path.join(self.home, ".claude", "evidence", "unit-runs", "D3.1-000003", "STATUS"))
        return self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9}, autospec=False)

    def test_a_previous_build_routes_the_sub_unit_only_when_the_spec_states_no_write_set(self):
        out = self._with_build("No owns line; prose only.", ["scripts/d3_new.py", "scripts/cut_preflight.py"])
        self.assertRegex(out, r"(?m)^D3\s+D3\.1\s+skip: REVIEWED-ROUTE", out)

    def test_a_stray_build_never_routes_a_sub_unit_whose_spec_states_its_writes(self):
        # review round 2: the adapter already refused the stray write; the next build may not stray
        out = self._with_build("Owns: `scripts/d3_new.py` (NEW)", ["scripts/d3_new.py", "scripts/cut_preflight.py"])
        self.assertNotRegex(out, r"(?m)^D3\s+D3\.1\s+skip: REVIEWED-ROUTE", out)

    def test_reads_inside_an_annotation_keeps_a_written_protected_path(self):
        self.owned_spec("Files: `scripts/cut_preflight.py` (existing, the function that reads the ledger is changed)")
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9}, autospec=False)
        self.assertRegex(out, r"(?m)^D3\s+D3\.1\s+skip: REVIEWED-ROUTE", out)

    def test_a_sub_unit_naming_only_writable_paths_still_starts(self):
        # the refusal above must not be a screen that refuses everything
        self.owned_spec("Owns: `scripts/d3_new.py` (NEW), `scripts/test_d3_new.py` (NEW)")
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9}, autospec=False)
        self.assertEqual(self.started(out), {"D3.1"}, out)

    def test_a_score_at_the_floor_does_start(self):
        # the refusals above must not be a pool that never starts anything
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 8})
        self.assertEqual(self.started(out), {"D3.1"}, out)

    # M2-4 (objection 10): a runner whose every call was refused because the proof run was draining ends DRAINED. That
    # is neither a failure nor a park: the pool re-seats it like UNFUNDED, and while the run drains it starts nothing.
    def test_a_drained_run_is_not_reseated_within_ten_minutes(self):
        self.plant_exhausted("D3.1", word="DRAINED", age=60.0)
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9})
        self.assertEqual(self.started(out), set(), out)
        self.assertIn("under ten minutes", out)

    def test_a_drained_run_is_reseated_after_the_wait(self):
        self.plant_exhausted("D3.1", word="DRAINED", age=3600.0)
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9})
        self.assertEqual(self.started(out), {"D3.1"}, out)

    def test_a_draining_proof_run_starts_no_runner(self):
        run = os.path.join(self.dir, "runs", "run-RB-x"); os.makedirs(os.path.join(run, "proof"))
        with open(os.path.join(run, "proof", "ending.json"), "w", encoding="utf-8") as fh: fh.write("{}\n")
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9},
                            env={"BROTHER_PROOF_PHASE": "RB", "BROTHER_RUN_DIR": run})
        self.assertEqual(self.started(out), set(), out)
        self.assertIn("DRAINING", out)

    # A RUNNER THAT REFUSED AT STARTUP IS NOT A STARTED RUNNER (X2 finding 5, Codex cross lane review, 2026-09-27).
    # unit_runner exits 2 before it makes a run folder when its sub unit's lock cannot be taken, and the pool counted it
    # as started before spawning, so the pass read "started 1" as productive with no runner at all. Live passes (no
    # --dry): the REAL runner, installed in the scratch bin, refused by a lock path that is a directory; and a stand in
    # runner that writes its PID file the way unit_runner does and then idles, killed at cleanup.
    def install_runner(self, stub=None):
        b = os.path.join(self.home, ".claude", "bin"); os.makedirs(b, exist_ok=True)
        if stub is not None:
            with open(os.path.join(b, "unit_runner.py"), "w", encoding="utf-8") as fh: fh.write(stub)
            return
        loop = os.path.dirname(POOL)
        for n in os.listdir(loop):   # the pool's own helpers stay absent: their degraded answers are what these tests read
            if n.endswith(".py") and not n.startswith("test_") and n not in ("judge_calibrate.py", "spec_score.py", "burn_guard.py"):
                shutil.copy2(os.path.join(loop, n), os.path.join(b, n))

    def reap(self):
        for pid_file in [os.path.join(r, n, "PID") for r in [os.path.join(self.home, ".claude", "evidence", "unit-runs")]
                         for n in (os.listdir(r) if os.path.isdir(r) else [])]:
            try:
                with open(pid_file, encoding="utf-8") as fh: os.kill(int(fh.read().strip()), 9)
            except (OSError, ValueError): pass

    def test_a_runner_that_refuses_at_startup_is_reported_never_counted(self):
        self.install_runner()
        os.makedirs(os.path.join(self.home, ".claude", "evidence", "unit-runs", "D3.1.lock"))
        self.addCleanup(self.reap)
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9}, dry=False)
        self.assertIn("started 0 ", out)
        self.assertEqual(self.started(out), set(), out)
        self.assertRegex(out, r"(?m)^D3\s+D3\.1\s+REFUSED AT STARTUP: runner exit 2\b.*REFUSED D3\.1", out)
        self.assertEqual(sorted(os.listdir(os.path.join(self.home, ".claude", "evidence", "unit-runs"))), ["D3.1.lock", "D3.1.log"])

    def test_control_a_runner_that_writes_its_pid_is_counted_as_started(self):
        self.install_runner(stub="import os, sys, time\nd = os.path.expanduser('~/.claude/evidence/unit-runs/%s-000009' % sys.argv[2])\n"
                                 "os.makedirs(d)\nopen(os.path.join(d, 'PID'), 'w').write('%d\\n' % os.getpid())\ntime.sleep(60)\n")
        self.addCleanup(self.reap)
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9}, dry=False)
        self.assertIn("started 1 ", out)
        self.assertEqual(self.started(out), {"D3.1"}, out)

    def test_a_runner_that_writes_its_pid_and_then_dies_was_still_a_start(self):
        # its run folder exists, so the next pass reconciles it; counting it is the truth, not a refusal
        self.install_runner(stub="import os, sys\nd = os.path.expanduser('~/.claude/evidence/unit-runs/%s-000009' % sys.argv[2])\n"
                                 "os.makedirs(d)\nopen(os.path.join(d, 'PID'), 'w').write('%d\\n' % os.getpid())\nsys.exit(1)\n")
        out = self.run_pool([unit("D3", ["D3.1"])], scores={"D3.1": 9}, dry=False)
        self.assertIn("started 1 ", out)

    def test_a_runner_still_alive_without_a_pid_file_at_the_deadline_counts_as_started(self):
        import types
        mod = self.load_pool_in_process()
        alive = types.SimpleNamespace(pid=2 ** 22 + 7, poll=lambda: None)
        self.assertEqual(mod.spawned(alive, "D3.1", runs_dir=self.dir, wait_s=0.2), "")
        gone = types.SimpleNamespace(pid=2 ** 22 + 7, poll=lambda: 2)
        self.assertEqual(mod.spawned(gone, "D3.1", runs_dir=self.dir, wait_s=5), "runner exit 2 before it started")

    def test_only_the_next_unlanded_sub_unit_is_considered(self):
        out = self.run_pool([unit("D3", ["D3.1", "D3.2"], evidence="D3.1 landed")],
                            scores={"D3.1": 9, "D3.2": 9})
        self.assertEqual(self.started(out), {"D3.2"}, out)

    def test_the_budget_cap_bounds_how_many_start_in_one_pass(self):
        # a guard that funds 2 lanes binds at 2 (it was the unreadable guard's floor until 2026-09-27; that now admits 0)
        self.fund(2)
        units = [unit("D%d" % i, ["D%d.1" % i]) for i in (1, 2, 3, 4, 5)]
        out = self.run_pool(units, scores={"D%d.1" % i: 10 for i in (1, 2, 3, 4, 5)})
        self.assertIn("room for 2", out)
        self.assertEqual(len(self.started(out)), 2, out)
        self.assertIn("budget cap reached", out)

    def test_a_smaller_work_in_progress_cap_wins_over_the_burn_cap(self):
        units = [unit("D%d" % i, ["D%d.1" % i]) for i in (1, 2, 3)]
        out = self.run_pool(units, scores={"D%d.1" % i: 10 for i in (1, 2, 3)}, env={"BROTHER_WIP": "1"})
        self.assertIn("room for 1", out)
        self.assertEqual(len(self.started(out)), 1, out)

    # PRIORITY, EXPLORATION, READY AS WORK IN PROGRESS, SUNK HOLD (Codex and Opus, 2026-09-27)
    def ledger(self, rows_by_unit, age=3600.0):
        """Graded ledger rows: {unit: [(grade, refusal), ...]}, each its own build, run_at `age` seconds ago."""
        p = os.path.join(self.home, ".claude", "evidence", "unit-ledger.jsonl"); at = time.time() - age
        with open(p, "w", encoding="utf-8") as fh:
            for uid, rows in rows_by_unit.items():
                for i, (g, why) in enumerate(rows):
                    fh.write(json.dumps({"run": uid + ".1-000009", "round": 0, "build": "%s.1-r%d" % (uid, i), "unit": uid,
                                         "sub": uid + ".1", "grade": g, "refusal": why, "run_at": at}) + "\n")

    def test_the_cheapest_unit_to_finish_starts_first(self):
        # D1 ranks first by id; D2 passes five times as often with the same work left, so it is cheaper to finish
        self.ledger({"D1": [("PASS", "")] + [("FAIL", "suite not green with the code")] * 9,
                     "D5": [("PASS", "")] * 5 + [("FAIL", "suite not green with the code")] * 5})
        out = self.run_pool([unit("D1", ["D1.1"]), unit("D5", ["D5.1"])], scores={"D1.1": 10, "D5.1": 10}, env={"BROTHER_WIP": "1"})
        self.assertEqual(self.started(out), {"D5.1"}, out)

    def test_a_fresh_unit_gets_an_exploration_slot(self):
        self.ledger({"D1": [("PASS", "")] * 5})
        units = [unit("D1", ["D1.1", "D1.2"]), unit("D9", ["D9.1"])]   # two left: not a closer, so only exploration decides
        out = self.run_pool(units, scores={"D1.1": 10, "D9.1": 10}, env={"BROTHER_WIP": "1"})
        self.assertEqual(self.started(out), {"D9.1"}, out)
        out = self.run_pool(units, scores={"D1.1": 10, "D9.1": 10}, env={"BROTHER_WIP": "1", "BROTHER_EXPLORE": "0"})
        self.assertEqual(self.started(out), {"D1.1"}, out)

    def test_a_unit_one_sub_unit_from_closing_goes_before_exploration(self):
        self.ledger({"D1": [("PASS", "")] * 2 + [("FAIL", "suite not green with the code")] * 8})
        units = [unit("D1", ["D1.1", "D1.2"], evidence="D1.1 landed"), unit("D9", ["D9.1"])]
        out = self.run_pool(units, scores={"D1.2": 10, "D9.1": 10}, env={"BROTHER_WIP": "1"})
        self.assertEqual(self.started(out), {"D1.2"}, out)

    def test_an_exploration_slot_skips_a_fresh_unit_its_spec_floor_refuses(self):
        self.ledger({"D1": [("PASS", "")] * 5})
        units = [unit("D1", ["D1.1", "D1.2"]), unit("D8", ["D8.1"]), unit("D9", ["D9.1"])]
        out = self.run_pool(units, scores={"D1.1": 10, "D8.1": 5, "D9.1": 10}, env={"BROTHER_WIP": "1", "BROTHER_EXPLORE": "1"})
        self.assertEqual(self.started(out), {"D9.1"}, out)

    def test_a_ready_build_waiting_to_land_counts_as_work_in_progress(self):
        self.plant_exhausted("D1.1", word="READY")
        out = self.run_pool([unit("D1", ["D1.1"]), unit("D5", ["D5.1"])], scores={"D1.1": 10, "D5.1": 10}, env={"BROTHER_WIP": "1"})
        self.assertIn("room for 0", out)
        self.assertEqual(self.started(out), set(), out)

    def test_a_sunk_unit_buys_no_build_and_names_its_class(self):
        self.ledger({"D1": [("PASS", "")] + [("FAIL", "safety screen")] * 59})
        out = self.run_pool([unit("D1", ["D1.1"])], scores={"D1.1": 10})
        self.assertRegex(out, r"(?m)^D1\s+D1\.1\s+skip: SUNK 60 grades, 1 passes, mostly CONTRACT", out)
        self.assertEqual(self.started(out), set(), out)

    def test_a_sunk_unit_comes_back_when_its_spec_changes(self):
        self.ledger({"D1": [("PASS", "")] + [("FAIL", "safety screen")] * 59}, age=3600.0)
        self.spec("D1", age=60)
        out = self.run_pool([unit("D1", ["D1.1"])], scores={"D1.1": 10})
        self.assertEqual(self.started(out), {"D1.1"}, out)

    def test_a_unit_just_under_the_sunk_line_still_builds(self):
        self.ledger({"D1": [("PASS", "")] * 3 + [("FAIL", "safety screen")] * 57})
        out = self.run_pool([unit("D1", ["D1.1"])], scores={"D1.1": 10})
        self.assertEqual(self.started(out), {"D1.1"}, out)

    def test_a_file_labelled_after_its_path_gives_a_known_touch_set(self):
        # D15-D, 2026-09-27: every path written `p (NEW)` read as no path, so the unit one sub unit from closing sat
        # behind "TOUCH unknown" while the pool ranked it fourth
        p = os.path.join(self.dir, "docs", "plan", "specs", "D1.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# D1\n### D1.1\nFiles: `plugin/core/test_d1.py (NEW)`.\n- `scripts/d1_tool.py (existing)` is read\nDone check: `python3 -B scripts/test_d1.py`\n")
        then = time.time() - 7200; os.utime(p, (then, then))
        out = self.run_pool([unit("D1", ["D1.1"])], scores={"D1.1": 10}, autospec=False)
        self.assertNotIn("TOUCH unknown", out)
        self.assertEqual(self.started(out), {"D1.1"}, out)

    def test_the_loops_status_lock_in_an_old_build_is_no_touch_edge(self):
        mod = self.load_pool_in_process()
        # 2026-10-03: builds made before the adapter dropped it carry `.status.lock`; it is the loop's lock, never a file
        # a sub unit patches, so it never serializes two units
        runs = os.path.join(self.dir, "runs")
        for sub in ("D1.1", "D5.1"):
            out = os.path.join(runs, sub + "-1", "round0", "out"); os.makedirs(out)
            with open(os.path.join(out, sub + "-r0-build.json"), "w", encoding="utf-8") as fh:
                json.dump({"edits": [{"path": ".status.lock", "new_file_content": ""}, {"path": "scripts/%s.py" % sub[:2].lower(), "new_file_content": "X = 1\n"}]}, fh)
        spec = os.path.join(self.dir, "spec.md")
        with open(spec, "w", encoding="utf-8") as fh:
            fh.write("### D1.1\n### D2.1\n")
        a, b = (mod.touch_set(s, spec, runs_dir=runs) for s in ("D1.1", "D5.1"))
        self.assertNotIn(".status.lock", a | b)
        self.assertEqual(mod.touch_conflict(b, {p: "D1.1" for p in a}, []), "")

    # FILE TOUCH EDGES (row 17): two sub units that patch one file never run together; generated paths are no edge.
    def test_two_sub_units_naming_one_file_never_start_in_one_pass(self):
        self.spec("D1", age=7200, paths=["scripts/shared.py", "scripts/d1.py"]); self.spec("D5", age=7200, paths=["scripts/shared.py"])
        out = self.run_pool([unit("D1", ["D1.1"]), unit("D5", ["D5.1"])], scores={"D1.1": 10, "D5.1": 10})
        self.assertEqual(self.started(out), {"D1.1"}, out)
        self.assertIn("TOUCH: shares scripts/shared.py with D1.1", out)

    def test_a_live_runner_holds_every_file_of_its_sub_unit(self):
        self.plant_runner("D1.1", os.getpid()); self.spec("D1", age=7200, paths=["scripts/shared.py"]); self.spec("D5", age=7200, paths=["scripts/shared.py"])
        out = self.run_pool([unit("D1", ["D1.1"]), unit("D5", ["D5.1"])], scores={"D1.1": 10, "D5.1": 10})
        self.assertEqual(self.started(out), set(), out); self.assertIn("TOUCH: shares scripts/shared.py with D1.1", out)

    def test_the_newest_build_adds_the_files_it_edits(self):
        # the spec names nothing shared; the last build of D1.1 edited the file D2.1's spec names
        self.spec("D1", age=7200, paths=["scripts/d1.py"]); self.spec("D5", age=7200, paths=["scripts/d5.py"])
        self.build("D1.1", ["scripts/d5.py"], age=60); self.plant_runner("D1.1", os.getpid())
        out = self.run_pool([unit("D1", ["D1.1"]), unit("D5", ["D5.1"])], scores={"D1.1": 10, "D5.1": 10})
        self.assertEqual(self.started(out), set(), out); self.assertIn("TOUCH: shares scripts/d5.py with D1.1", out)

    def test_an_older_build_does_not_count_only_the_newest(self):
        self.spec("D1", age=7200, paths=["scripts/d1.py"]); self.spec("D5", age=7200, paths=["scripts/d5.py"])
        self.build("D1.1", ["scripts/d5.py"], age=3600, folder="000002"); self.build("D1.1", ["scripts/d1.py"], age=60, folder="000003")
        self.plant_runner("D1.1", os.getpid())
        out = self.run_pool([unit("D1", ["D1.1"]), unit("D5", ["D5.1"])], scores={"D1.1": 10, "D5.1": 10})
        self.assertEqual(self.started(out), {"D5.1"}, out)

    def test_a_generated_path_is_never_an_edge(self):
        self.spec("D1", age=7200, paths=["scripts/d1.py", "bundle/x.py", "SYSTEM.md", "scripts/check_all.sh"])
        self.spec("D5", age=7200, paths=["scripts/d5.py", "bundle/x.py", "SYSTEM.md", "scripts/check_all.sh"])
        out = self.run_pool([unit("D1", ["D1.1"]), unit("D5", ["D5.1"])], scores={"D1.1": 10, "D5.1": 10})
        self.assertEqual(self.started(out), {"D1.1", "D5.1"}, out)

    def test_an_unreadable_touch_set_blocks_rather_than_reading_as_touching_nothing(self):
        self.spec("D1", age=7200, paths=[])                 # a section with no path and no build on disk
        out = self.run_pool([unit("D1", ["D1.1"])], scores={"D1.1": 10})
        self.assertEqual(self.started(out), set(), out); self.assertIn("TOUCH unknown", out)

    def test_a_live_runner_with_an_unreadable_touch_set_refuses_every_start(self):
        self.plant_runner("D1.1", os.getpid()); self.spec("D1", age=7200, paths=[]); self.spec("D5", age=7200, paths=["scripts/d5.py"])
        out = self.run_pool([unit("D1", ["D1.1"]), unit("D5", ["D5.1"])], scores={"D1.1": 10, "D5.1": 10})
        self.assertEqual(self.started(out), set(), out); self.assertIn("live runner D1.1 has an unknown touch set", out)

    def test_a_touch_set_of_only_generated_paths_is_unknown_and_an_alias_is_one_key(self):
        self.spec("D1", age=7200, paths=["bundle/x.py", "SYSTEM.md"]); self.spec("D5", age=7200, paths=["./scripts/d5.py"]); self.spec("D3", age=7200, paths=["scripts/d5.py"])
        out = self.run_pool([unit("D1", ["D1.1"]), unit("D5", ["D5.1"]), unit("D3", ["D3.1"])], scores={"D1.1": 10, "D5.1": 10, "D3.1": 10})
        self.assertIn("TOUCH unknown", out); self.assertNotIn("D1.1", self.started(out))
        self.assertEqual(len(self.started(out) & {"D5.1", "D3.1"}), 1, out)

    def test_a_unit_outside_the_declared_scope_is_not_started(self):
        out = self.run_pool([unit("Z9", ["Z9.1"])], scores={"Z9.1": 10}, env={"BROTHER_SCOPE": "^D"})
        self.assertEqual(self.started(out), set(), out)
        self.assertIn("left out: Z9", out)

    def test_a_deferred_unit_is_never_admitted_and_is_named_as_left_out(self):
        """2026-09-29: RL3, RL4 and L5f (plan state DEFERRED, moved to a later release by the owner) sat in the pool's
        ORDER under BROTHER_SCOPE=. A state meaning not in this release admits nothing, and the pass says why."""
        d = unit("RL3", ["RL3.a"]); d["state"] = "DEFERRED"
        out = self.run_pool([d, unit("D3", ["D3.1"])], scores={"RL3.a": 10, "D3.1": 10}, env={"BROTHER_SCOPE": "."})
        self.assertNotIn("RL3.a", self.started(out), out)
        self.assertIn("D3.1", self.started(out), out)   # control: the same pass admits the open unit
        self.assertRegex(out, r"(?m)^SCOPE .*left out: .*RL3 \(unit state DEFERRED", out)
        self.assertNotRegex(out, r"(?m)^ORDER .*RL3", out)

    def test_a_unit_whose_predecessor_has_not_landed_is_held_and_says_which(self):
        """2026-09-30: the pool ignored depends_on, so RL4 could start before RL3 had landed and work against code
        that did not exist; RL4 and RL5 were parked under S4 by hand. Now the pool holds the dependent itself."""
        a = dict(unit("A", ["A.1"]), depends_on=["B"])
        out = self.run_pool([a, unit("B", ["B.1"])], scores={"A.1": 10, "B.1": 10})
        self.assertNotIn("A.1", self.started(out), out)
        self.assertIn("B.1", self.started(out), out)   # control: the predecessor itself is admitted
        self.assertRegex(out, r"(?m)^A +A\.1 +skip: A\.1 held: unit A depends on B \(OPEN\), not yet landed$", out)

    def test_a_predecessor_with_every_sub_unit_landed_releases_the_dependent(self):
        a = dict(unit("A", ["A.1"]), depends_on=["B"])
        b = unit("B", ["B.1", "B.2"], evidence="B.1 landed at 1; B.2 landed at 2")
        out = self.run_pool([a, b], scores={"A.1": 10})
        self.assertIn("A.1", self.started(out), out)

    def test_a_predecessor_with_one_sub_unit_unlanded_still_holds_the_dependent(self):
        a = dict(unit("A", ["A.1"]), depends_on=["B"])
        b = unit("B", ["B.1", "B.2"], evidence="B.1 landed at 1")
        out = self.run_pool([a, b], scores={"A.1": 10, "B.2": 10})
        self.assertNotIn("A.1", self.started(out), out)

    def test_a_done_predecessor_releases_the_dependent(self):
        a = dict(unit("A", ["A.1"]), depends_on=["B"])
        b = dict(unit("B", ["B.1"]), state="DONE")
        out = self.run_pool([a, b], scores={"A.1": 10})
        self.assertIn("A.1", self.started(out), out)

    def test_a_predecessor_the_plan_does_not_hold_holds_the_dependent(self):
        a = dict(unit("A", ["A.1"]), depends_on=["loop-line PR merged"])
        out = self.run_pool([a], scores={"A.1": 10})
        self.assertNotIn("A.1", self.started(out), out)
        self.assertIn("depends on 'loop-line PR merged', which the plan does not hold", out)

    def test_a_predecessor_with_no_sub_units_and_not_done_holds_the_dependent(self):
        """M15 (attack 2026-09-30): a predecessor with an empty sub unit list is not landed unless its state is DONE."""
        a = dict(unit("A", ["A.1"]), depends_on=["B"])
        out = self.run_pool([a, unit("B", [])], scores={"A.1": 10})
        self.assertNotIn("A.1", self.started(out), out)
        self.assertIn("depends on B (OPEN), not yet landed", out)

    def test_a_retired_predecessor_releases_the_dependent(self):
        a = dict(unit("A", ["A.1"]), depends_on=["B"])
        b = dict(unit("B", ["B.1"]), state="RETIRED")
        out = self.run_pool([a, b], scores={"A.1": 10})
        self.assertIn("A.1", self.started(out), out)

    def test_a_deferred_predecessor_holds_the_dependent_and_names_the_deferral(self):
        a = dict(unit("A", ["A.1"]), depends_on=["B"])
        b = dict(unit("B", ["B.1"]), state="DEFERRED")
        out = self.run_pool([a, b], scores={"A.1": 10})
        self.assertNotIn("A.1", self.started(out), out)
        self.assertIn("depends on B, DEFERRED to a later release", out)

    def test_unit_landed_reads_an_unreadable_sub_unit_as_not_landed(self):
        """M11: sub_landed raises ValueError on a non string id; the helper answers False (held), never True.
        admissible() refuses such a plan before this helper is reached, so the helper is tested directly."""
        mod = self.load_pool_in_process()
        self.assertFalse(mod.unit_landed({"state": "OPEN", "sub_units": [None], "evidence": "x landed"}))
        self.assertFalse(mod.unit_landed({"state": "OPEN", "sub_units": [], "evidence": "x landed"}))
        self.assertTrue(mod.unit_landed({"state": "OPEN", "sub_units": ["B.1"], "evidence": "B.1 landed"}))   # control

    def test_an_unreadable_depends_on_is_no_data_and_admits_nothing(self):
        mod = self.load_pool_in_process()
        self.spec("A", 60)
        a = dict(unit("A", ["A.1"]), spec=os.path.join(self.dir, "docs", "plan", "specs", "A.md"), depends_on="B")
        self.assertIn("NO-DATA unreadable depends_on", mod.admissible({"units": [a]}, "A.1"))
        a["depends_on"] = []
        self.assertEqual(mod.admissible({"units": [a]}, "A.1"), "")   # control: no dependency admits

    def test_admissible_refuses_every_state_that_is_not_this_release(self):
        mod = self.load_pool_in_process()
        for state in ("DEFERRED", "RETIRED"):
            why = mod.admissible({"units": [dict(unit("X", ["X.1"]), state=state)]}, "X.1")
            self.assertIn("not in this release", why)
        self.spec("X", 60)
        ok = dict(unit("X", ["X.1"]), spec=os.path.join(self.dir, "docs", "plan", "specs", "X.md"))
        self.assertEqual(mod.admissible({"units": [ok]}, "X.1"), "")   # control: OPEN admits

    def chained(self, uid):
        """A spec whose one section's done check is a fenced block of TWO commands: the landing gate joins them with
        && and refuses the result, while the scorer loses only its DONE CHECK point (10 to 9, above the floor of 8)."""
        p = os.path.join(self.dir, "docs", "plan", "specs", "%s.md" % uid)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# %s\n### %s.1\n`scripts/%s_1.py` existing\nDone check:\n\n```\npython3 -B scripts/test_a.py\npython3 -B scripts/test_b.py\n```\n" % (uid, uid, uid))
        then = time.time() - 7200; os.utime(p, (then, then))

    def test_a_done_check_the_landing_gate_refuses_is_never_admitted(self):
        """2026-09-29: the pool admitted any section scoring 8 or more, so a 9 whose done check land_batch refuses
        started builds that were all dropped at landing. Admission now asks the gate's own gate_refusal first."""
        self.chained("D1")
        out = self.run_pool([unit("D1", ["D1.1"]), unit("D3", ["D3.1"])], scores={"D1.1": 9, "D3.1": 9})   # D3 gets the autospec
        self.assertNotIn("D1.1", self.started(out), out)
        self.assertRegex(out, r"(?m)^D1 +D1\.1 +skip: DONE CHECK refused by the landing gate: the landing gate refuses 'python3 -B scripts/test_a\.py && python3 -B scripts/test_b\.py'.*; route: spec repair$", out)
        self.assertIn("D3.1", self.started(out), out)   # control: one accepted command is admitted as before

    def test_admission_refuses_a_unit_whose_spec_cannot_be_read(self):
        mod = self.load_pool_in_process()
        why = mod.admissible({"units": [dict(unit("X", ["X.1"]), spec=os.path.join(self.dir, "absent.md"))]}, "X.1")
        self.assertTrue(why.startswith("NO-DATA unit X spec"), why)
        self.assertTrue(mod.admissible({"units": [dict(unit("X", ["X.1"]), spec=None)]}, "X.1").startswith("NO-DATA"))

    # H3.b DEAD RUNNER RECONCILE (REQ-H-DEADPID). Load the module in process so reconcile_dead can be
    # exercised directly; the module executes its pool pass at import, so the plan file must exist first.
    def load_pool_in_process(self):
        with open(os.path.join(self.dir, "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"), "w", encoding="utf-8") as fh:
            json.dump({"units": []}, fh)
        os.makedirs(os.path.join(self.home, ".claude", "evidence"), exist_ok=True)
        saved_home = os.environ.get("HOME")
        saved_loop_env = {k: os.environ.pop(k) for k in [k for k in list(os.environ) if k.startswith("BROTHER_") or k.startswith("GIT_")]}
        saved_cwd = os.getcwd()
        os.environ["HOME"] = self.home
        os.environ["BROTHER_WIP_GATE"] = "off"   # ACC3.b: no existing in process case reads the real finish-first limits
        os.chdir(self.dir)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                name = "runner_pool_h3b_%d" % id(self)
                spec = importlib.util.spec_from_file_location(name, POOL)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.main()
        finally:
            os.chdir(saved_cwd)
            if saved_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved_home
            for _k in [k for k in list(os.environ) if k.startswith("BROTHER_") or k.startswith("GIT_")]:
                os.environ.pop(_k, None)
            os.environ.update(saved_loop_env)
        self._pool_stdout = buf.getvalue()
        return mod

    def test_a_dead_runner_is_reconciled_to_stalled_in_the_pass(self):
        """REQ-H-DEADPID: a run folder whose runner is dead and has no STATUS reads STALLED for the next
        pass. Without reconcile_dead in the pass, the folder keeps reading RUNNING forever."""
        d = os.path.join(self.home, ".claude", "evidence", "unit-runs", "D3.1-000001")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "PID"), "w", encoding="utf-8") as fh:
            fh.write("4194297\n")
        self.load_pool_in_process()
        status = os.path.join(d, "STATUS")
        self.assertTrue(os.path.isfile(status), self._pool_stdout)
        with open(status, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("STALLED dead runner pid 4194297"), text)
        self.assertFalse(os.path.exists(status + ".tmp"))

    def test_reconcile_dead_writes_stalled_only_for_a_dead_pid_with_no_status(self):
        mod = self.load_pool_in_process()
        base = os.path.join(self.dir, "runs")
        dead = os.path.join(base, "d1"); os.makedirs(dead)
        with open(os.path.join(dead, "PID"), "w", encoding="utf-8") as fh:
            fh.write("4194297\n")
        self.assertEqual(mod.reconcile_dead(dead, lambda p: False), "STALLED")
        with open(os.path.join(dead, "STATUS"), encoding="utf-8") as fh:
            self.assertTrue(fh.read().startswith("STALLED dead runner pid 4194297"))
        self.assertFalse(os.path.exists(os.path.join(dead, "STATUS.tmp")))
        live = os.path.join(base, "d2"); os.makedirs(live)
        with open(os.path.join(live, "PID"), "w", encoding="utf-8") as fh:
            fh.write("1\n")
        self.assertEqual(mod.reconcile_dead(live, lambda p: True), "")
        self.assertFalse(os.path.exists(os.path.join(live, "STATUS")))
        done = os.path.join(base, "d3"); os.makedirs(done)
        with open(os.path.join(done, "PID"), "w", encoding="utf-8") as fh:
            fh.write("4194297\n")
        with open(os.path.join(done, "STATUS"), "w", encoding="utf-8") as fh:
            fh.write("READY\n")
        self.assertEqual(mod.reconcile_dead(done, lambda p: False), "")
        with open(os.path.join(done, "STATUS"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "READY\n")
        nopid = os.path.join(base, "d4"); os.makedirs(nopid)
        self.assertEqual(mod.reconcile_dead(nopid, lambda p: False), "")
        self.assertFalse(os.path.exists(os.path.join(nopid, "STATUS")))

    def test_reconcile_dead_refuses_hostile_input(self):
        mod = self.load_pool_in_process()
        for bad in (None, 123, b"", ""):
            with self.subTest(run_dir=bad):
                with self.assertRaises(ValueError):
                    mod.reconcile_dead(bad, lambda p: False)
        for bad in (None, 5, "yes"):
            with self.subTest(pid_alive=bad):
                with self.assertRaises(ValueError):
                    mod.reconcile_dead(self.dir, bad)
        with self.assertRaises(ValueError):
            mod.reconcile_dead(os.path.join(self.dir, "does-not-exist"), lambda p: False)
        f = os.path.join(self.dir, "a_plain_file")
        open(f, "w").close()
        with self.assertRaises(ValueError):
            mod.reconcile_dead(f, lambda p: False)

    def test_reconcile_dead_refuses_corrupt_pid_files(self):
        mod = self.load_pool_in_process()
        base = os.path.join(self.dir, "runs2")
        cases = {"empty": b"", "not_int": b"not-a-pid\n", "zero": b"0\n",
                 "negative": b"-1\n", "non_utf8": b"\xff\xfe", "whitespace": b"  \n"}
        for name, payload in cases.items():
            with self.subTest(name=name):
                d = os.path.join(base, name); os.makedirs(d)
                with open(os.path.join(d, "PID"), "wb") as fh:
                    fh.write(payload)
                with self.assertRaises(ValueError):
                    mod.reconcile_dead(d, lambda p: False)
        d = os.path.join(base, "pid_dir"); os.makedirs(os.path.join(d, "PID"))
        with self.assertRaises(ValueError):
            mod.reconcile_dead(d, lambda p: False)

    def test_pool_helpers_refuse_hostile_input(self):
        mod = self.load_pool_in_process()
        with self.assertRaises(ValueError):
            mod.waits_to_land({"not": "a string"})
        with self.assertRaises(ValueError):
            mod.status_word(None)
        with self.assertRaises(ValueError):
            mod.touch_conflict([["unhashable"]], {}, [])
        with self.assertRaises(ValueError):
            mod.fact_time(None)
        with self.assertRaises(ValueError):
            mod.fact_time("D3", spec_path=5)
        with self.assertRaises(ValueError):
            mod.remaining_of(None)
        with self.assertRaises(ValueError):
            mod.alive_runners(None, {"units": []})
        with self.assertRaises(ValueError):
            mod.alive_runners("r", None)
        with self.assertRaises(ValueError):
            mod.touch_set(None, "spec")

    def test_fact_time_refuses_a_nan_mtime(self):
        mod = self.load_pool_in_process()
        with self.assertRaises(ValueError):
            mod.fact_time("D3", mtime=lambda p: float("nan"))

    def test_fact_time_refuses_an_infinite_mtime(self):
        """A red team finding: mtime returning inf was accepted and became the newest fact time, which read as
        a change AFTER every parked run, readmitting sub units that nothing had actually changed for."""
        mod = self.load_pool_in_process()
        for bad in (float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    mod.fact_time("D3", mtime=lambda p: bad)

    def test_reseated_too_soon_refuses_a_str_run_at(self):
        """A red team finding: a run time that arrived as a str crashed the pool pass with TypeError before any
        status could be read. Hostile input is refused with the module's own ValueError, never a crash."""
        mod = self.load_pool_in_process()
        for bad in ("3600", b"3600", None, True):
            with self.subTest(run_at=bad):
                with self.assertRaises(ValueError):
                    mod.reseated_too_soon("EXHAUSTED", bad, time.time())
        for bad in (float("nan"), float("inf"), float("-inf"), "now"):
            with self.subTest(run_at=bad):
                with self.assertRaises(ValueError):
                    mod.reseated_too_soon("EXHAUSTED", bad, time.time())
        with self.assertRaises(ValueError):
            mod.reseated_too_soon(5, 0.0, time.time())
        self.assertFalse(mod.reseated_too_soon("EXHAUSTED", 0.0, time.time()))

    def test_status_word_reads_corrupt_bytes_as_no_data(self):
        mod = self.load_pool_in_process()
        p = os.path.join(self.dir, "status_bytes")
        with open(p, "wb") as fh:
            fh.write(b"\xff\xfe not utf-8\n")
        self.assertEqual(mod.status_word(p), "NO-DATA")
        d = os.path.join(self.dir, "status_dir"); os.makedirs(d)
        self.assertEqual(mod.status_word(d), "NO-DATA")

    def test_alive_runners_refuses_a_non_list_sub_units_field(self):
        mod = self.load_pool_in_process()
        with self.assertRaises(ValueError):
            mod.alive_runners(self.dir, {"units": [{"id": "D3", "sub_units": (s for s in ["D3.1"])}]})
        with self.assertRaises(ValueError):
            mod.alive_runners(self.dir, {"units": [{"id": None, "sub_units": []}]})
        with self.assertRaises(ValueError):
            mod.alive_runners(self.dir, {"units": "not a list"})

    def test_the_unit_fixture_refuses_corrupt_arguments(self):
        with self.assertRaises(ValueError):
            unit(None, [])
        with self.assertRaises(ValueError):
            unit("D3", "str")
        with self.assertRaises(ValueError):
            unit("D3", [None])


    # ACC3.b: THE FINISH-FIRST LIMITS ARE READ EVERY PASS, AND A NEW UNIT WAITS ONLY WHEN THE OWNER ENFORCES.
    # scripts/loop/wip_status.py itself (ACC3.a) is loaded from a fixture here: the entry cases must move the module
    # constant WIP_STATUS, which the process route cannot do, so they go through pool_pass below.
    def wip_fixture(self, code, rows=None, name="wip_status.py"):
        """A fixture reader at <dir>/fixture/<name> whose measure returns fixed rows and a fixed code. The unread
        case is the caller naming a path this helper never wrote."""
        d = os.path.join(self.dir, "fixture")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        fixed = list(rows if rows is not None else [("open-prs", "PASS", "7 open non-draft, limit 8"),
                                                    ("oldest-idle-pr", "OVER", "#601 idle 371.9 h, limit 24 h")])
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("def measure(loop_ref, main_ref):\n    return %r, %d\n" % (fixed, code))
        return p

    def pool_pass(self, units: list, wip_status: str, env: Optional[dict] = None,
                  scores: Optional[dict] = None) -> str:
        """One --dry pass IN PROCESS, with the pool's limits reader pointed at `wip_status` (a fixture path).

        The entry cases for the finish-first limits need the module constant WIP_STATUS moved, and a process cannot
        move a constant, so this route loads the pool the way load_pool_in_process does (HOME and the working
        directory set before exec_module, restored after), points WIP_STATUS at the fixture, patches sys.argv to the
        pool path plus --dry and os.environ with env. Every BROTHER_ and GIT_ variable is cleared first, so the
        fixture is isolated from the loop's own launch; BROTHER_WIP_GATE stays unset unless the case sets it, so a
        case reads the real default. It calls the module's main() and returns its captured stdout."""
        self.write_fixtures(units, scores)
        saved_environ = dict(os.environ)
        saved_argv = list(sys.argv)
        saved_cwd = os.getcwd()
        buf = io.StringIO()
        try:
            os.environ["HOME"] = self.home
            os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
            for k in [k for k in list(os.environ) if k.startswith("BROTHER_") or k.startswith("GIT_")]:
                os.environ.pop(k, None)
            for k, v in (env or {}).items():
                os.environ[k] = v
            os.chdir(self.dir)
            sys.argv = [POOL, "--dry"]
            with contextlib.redirect_stdout(buf):
                spec = importlib.util.spec_from_file_location("runner_pool_acc3b_%d" % id(self), POOL)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.WIP_STATUS = wip_status
                mod.main()
        finally:
            os.chdir(saved_cwd)
            sys.argv = saved_argv
            for k in [k for k in list(os.environ) if k not in saved_environ]:
                os.environ.pop(k, None)
            os.environ.update(saved_environ)
        return buf.getvalue()

    def test_the_gate_defaults_to_report_and_an_unreadable_value_enforces(self):
        """R-WIP-7: unset and empty read report, the three known values map to themselves, and a value somebody set
        and nobody can read is enforce, never the safe case. A value that is not a string is refused."""
        mod = self.load_pool_in_process()
        self.assertEqual(mod.wip_mode(None), "report")
        self.assertEqual(mod.wip_mode(""), "report")
        self.assertEqual(mod.wip_mode("   "), "report")
        self.assertEqual(mod.wip_mode(" Report "), "report")
        self.assertEqual(mod.wip_mode("report"), "report")
        self.assertEqual(mod.wip_mode("enforce"), "enforce")
        self.assertEqual(mod.wip_mode("off"), "off")
        self.assertEqual(mod.wip_mode("bogus"), "enforce")
        for bad in (0, True, b"report", ["report"], {"mode": "report"}):
            with self.subTest(raw=bad):
                with self.assertRaises(ValueError):
                    mod.wip_mode(bad)

    def test_the_default_gate_reports_over_limits_and_starts_the_same_unit(self):
        """R-WIP-7 entry: with BROTHER_WIP_GATE unset and the fixture reading OVER, D3.1 still starts and the WIP
        line says gate report, so the default measures and holds nothing; the same with "report" set."""
        out = self.pool_pass([unit("D3", ["D3.1"])], self.wip_fixture(code=1), scores={"D3.1": 10})
        self.assertIn("D3.1", self.started(out), out)
        self.assertIn("WIP     OVER gate report", out)
        self.assertNotIn("FINISH-FIRST", out)
        out = self.pool_pass([unit("D3", ["D3.1"])], self.wip_fixture(code=1),
                             env={"BROTHER_WIP_GATE": "report"}, scores={"D3.1": 10})
        self.assertIn("D3.1", self.started(out), out)
        self.assertIn("WIP     OVER gate report", out)

    def test_under_enforce_over_a_limit_a_new_unit_does_not_start(self):
        """R-WIP-4: under enforce with the reading OVER, D3, which has no landed sub unit, no runner and no READY
        build, prints FINISH-FIRST and no START; the WIP line says gate enforce."""
        out = self.pool_pass([unit("D3", ["D3.1"])], self.wip_fixture(code=1),
                             env={"BROTHER_WIP_GATE": "enforce"}, scores={"D3.1": 10})
        self.assertEqual(self.started(out), set(), out)
        self.assertIn("skip: FINISH-FIRST", out)
        self.assertIn("WIP     OVER gate enforce", out)

    def test_under_enforce_a_unit_under_way_keeps_getting_runners(self):
        """R-WIP-4: D4 has one landed sub unit (D4.1 of D4.1 and D4.2), so it is work under way and D4.2 starts
        under enforce while D3 beside it is held."""
        out = self.pool_pass([unit("D3", ["D3.1"]), unit("D4", ["D4.1", "D4.2"], evidence="D4.1 landed")],
                             self.wip_fixture(code=1), env={"BROTHER_WIP_GATE": "enforce"},
                             scores={"D3.1": 10, "D4.2": 10})
        self.assertEqual(self.started(out), {"D4.2"}, out)

    def test_under_enforce_an_unreadable_reading_holds_exactly_as_an_over_one_does(self):
        """R-WIP-5: WIP_STATUS at a path that does not exist is NO-DATA, D3 does not start under enforce, and the
        WIP line names the path."""
        missing = os.path.join(self.dir, "fixture", "absent_wip_status.py")
        out = self.pool_pass([unit("D3", ["D3.1"])], missing, env={"BROTHER_WIP_GATE": "enforce"}, scores={"D3.1": 10})
        self.assertEqual(self.started(out), set(), out)
        self.assertIn("WIP     NO-DATA gate enforce", out)
        self.assertIn(missing, out)

    def test_within_the_limits_the_same_fixture_starts_under_enforce(self):
        """R-WIP-6: the gate is the only difference; the same fixture reading 0 starts D3.1 under enforce."""
        out = self.pool_pass([unit("D3", ["D3.1"])],
                             self.wip_fixture(code=0, rows=[("open-prs", "PASS", "7 open non-draft, limit 8")]),
                             env={"BROTHER_WIP_GATE": "enforce"}, scores={"D3.1": 10})
        self.assertEqual(self.started(out), {"D3.1"}, out)
        self.assertIn("WIP     PASS gate enforce", out)

    def test_a_malformed_reading_is_no_data_and_holds_under_enforce(self):
        """A reader that answers anything but a list of three-string rows and a code in 0, 1, 2 is NO-DATA, never a
        pass this pool did not read: the unit is held under enforce and the WIP line says NO-DATA."""
        d = os.path.join(self.dir, "fixture")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "bad_rows_wip_status.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('def measure(loop_ref, main_ref):\n    return ("not a list of rows", 0)\n')
        out = self.pool_pass([unit("D3", ["D3.1"])], p, env={"BROTHER_WIP_GATE": "enforce"}, scores={"D3.1": 10})
        self.assertEqual(self.started(out), set(), out)
        self.assertIn("WIP     NO-DATA gate enforce", out)
        q = os.path.join(d, "bad_code_wip_status.py")
        with open(q, "w", encoding="utf-8") as fh:
            fh.write('def measure(loop_ref, main_ref):\n    return ([("open-prs", "PASS", "7 open, limit 8")], 3)\n')
        out = self.pool_pass([unit("D3", ["D3.1"])], q, scores={"D3.1": 10})
        self.assertIn("D3.1", self.started(out), out)   # control: under report a NO-DATA reading holds nothing
        self.assertIn("WIP     NO-DATA gate report", out)

    def test_each_pass_leaves_one_dated_reading_the_report_can_read(self):
        """R-WIP-8: one pass under report writes the complete, dated record; read_wip_record reads it back and no
        temporary sibling is left behind."""
        out = self.pool_pass([unit("D3", ["D3.1"])], self.wip_fixture(code=1), scores={"D3.1": 10})
        self.assertIn("WIP     OVER gate report", out)
        record = os.path.join(self.home, ".claude", "evidence", "wip-status.json")
        self.assertTrue(os.path.isfile(record), out)
        self.assertFalse(os.path.exists(record + ".tmp"))
        mod = self.load_pool_in_process()
        self.assertEqual(mod.WIP_RECORD, record)
        got, why = mod.read_wip_record(record)
        self.assertEqual(why, "")
        self.assertEqual(got["code"], 1)
        self.assertEqual(got["mode"], "report")
        self.assertIsInstance(got["at"], float)
        self.assertEqual(got["rows"][1][0], "oldest-idle-pr")

    def test_the_record_reader_refuses_a_missing_corrupt_future_stale_or_malformed_record(self):
        """R-WIP-8: (None, why) with why starting NO-DATA for a missing file, bytes that are not utf-8, text that is
        not JSON, a record more than 60 s in the future, a record older than max_age_s, and every malformed field;
        the good record still reads."""
        mod = self.load_pool_in_process()
        p = os.path.join(self.dir, "record.json")
        now = 1760000000.0
        self.assertTrue(mod.read_wip_record(p, now=now)[1].startswith("NO-DATA"))   # missing
        with open(p, "wb") as fh:
            fh.write(bytes([255, 254]) + b" not utf-8")
        self.assertTrue(mod.read_wip_record(p, now=now)[1].startswith("NO-DATA"))
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertTrue(mod.read_wip_record(p, now=now)[1].startswith("NO-DATA"))
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(["not", "an", "object"], fh)
        self.assertTrue(mod.read_wip_record(p, now=now)[1].startswith("NO-DATA"))
        good = {"at": now, "mode": "report", "code": 1, "rows": [["open-prs", "PASS", "7 open, limit 8"]]}
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(good, fh)
        record, why = mod.read_wip_record(p, now=now)
        self.assertEqual(why, "")
        self.assertEqual(record["code"], 1)
        for bad in (dict(good, at=now + 61.0), dict(good, at=now - 1801.0), dict(good, at="now"),
                    dict(good, at=float("nan")), dict(good, code=7), dict(good, code=True), dict(good, mode=5),
                    dict(good, rows="nope"), dict(good, rows=[["a", "b"]]), dict(good, rows=[[1, 2, 3]])):
            with self.subTest(bad=str(bad)[:40]):
                with open(p, "w", encoding="utf-8") as fh:
                    json.dump(bad, fh)
                self.assertTrue(mod.read_wip_record(p, now=now)[1].startswith("NO-DATA"), bad)

    def test_the_wip_helpers_refuse_hostile_arguments(self):
        """Every ACC3.b helper refuses 0, True, b"x" and None in each position that does not admit None with its
        own ValueError, never a TypeError and never an accept: a hostile argument is a refusal."""
        mod = self.load_pool_in_process()
        u = {"id": "D3", "sub_units": ["D3.1"], "evidence": ""}
        rows = [("open-prs", "PASS", "7 open non-draft, limit 8")]
        self.assertEqual(mod.finish_first_hold(u, 0, "report", set()), "")
        self.assertEqual(mod.finish_first_hold(u, 1, "report", set()), "")
        self.assertEqual(mod.finish_first_hold(u, 1, "off", set()), "")
        self.assertEqual(mod.finish_first_hold(u, 1, "enforce", {"D3"}), "")
        self.assertTrue(mod.finish_first_hold(u, 1, "enforce", set()).startswith("FINISH-FIRST"))
        self.assertTrue(mod.finish_first_hold(u, 2, "enforce", set()).startswith("FINISH-FIRST"))
        self.assertEqual(mod.finish_first_hold({"id": "D3", "sub_units": ["D3.1"], "evidence": "D3.1 landed"},
                                               1, "enforce", set()), "")
        for bad in (0, True, b"x", None, "D3", ["D3"]):
            with self.subTest(u=bad):
                with self.assertRaises(ValueError):
                    mod.finish_first_hold(bad, 1, "enforce", set())
        for bad in (True, b"x", None, 3, "1", 1.5):
            with self.subTest(code=bad):
                with self.assertRaises(ValueError):
                    mod.finish_first_hold(u, bad, "enforce", set())
        for bad in (0, True, b"x", None, "bogus"):
            with self.subTest(mode=bad):
                with self.assertRaises(ValueError):
                    mod.finish_first_hold(u, 1, bad, set())
        for bad in (0, True, b"x", None, [], {"D3": 1}, frozenset(["D3"])):
            with self.subTest(in_progress=bad):
                with self.assertRaises(ValueError):
                    mod.finish_first_hold(u, 1, "enforce", bad)
        for bad in (0, True, b"x", None):
            with self.subTest(loop_ref=bad):
                with self.assertRaises(ValueError):
                    mod.wip_reading(bad, "hub/main")
            with self.subTest(main_ref=bad):
                with self.assertRaises(ValueError):
                    mod.wip_reading("HEAD", bad)
        for bad in (0, True, b"x", None, "rows", {"rows": rows}):
            with self.subTest(rows=bad):
                with self.assertRaises(ValueError):
                    mod.wip_line(bad, 0, "report")
        for bad in (True, b"x", None, 3, "1"):
            with self.subTest(code=bad):
                with self.assertRaises(ValueError):
                    mod.wip_line(rows, bad, "report")
        for bad in (0, True, b"x", None, "bogus"):
            with self.subTest(mode=bad):
                with self.assertRaises(ValueError):
                    mod.wip_line(rows, 0, bad)
        for bad in (0, True, None, b"x", ""):
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    mod.read_wip_record(bad)
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    mod.write_wip_record(rows, 0, "report", path=bad)
        for bad in (True, float("nan"), float("inf"), float("-inf"), "5", b"5"):
            with self.subTest(now=bad):
                with self.assertRaises(ValueError):
                    mod.read_wip_record(os.path.join(self.dir, "record.json"), now=bad)
            with self.subTest(now=bad):
                with self.assertRaises(ValueError):
                    mod.write_wip_record(rows, 0, "report", path=os.path.join(self.dir, "record.json"), now=bad)
        for bad in (True, float("nan"), float("inf"), "1800", None):
            with self.subTest(max_age_s=bad):
                with self.assertRaises(ValueError):
                    mod.read_wip_record(os.path.join(self.dir, "record.json"), max_age_s=bad)

    def test_the_reader_restores_sys_path_and_caches_no_module(self):
        """R-WIP-4 and R-WIP-5: wip_reading inserts the reader's own directory only for the call, restores sys.path
        exactly in a finally, and never puts the module in sys.modules, so no cached copy from another path can
        answer a later pass."""
        mod = self.load_pool_in_process()
        mod.WIP_STATUS = self.wip_fixture(code=1)
        before = list(sys.path)
        rows, code = mod.wip_reading("HEAD", "hub/main")
        self.assertEqual(code, 1)
        self.assertEqual(rows[1][0], "oldest-idle-pr")
        self.assertNotIn("wip_status", sys.modules)
        self.assertEqual(sys.path, before)
        mod.WIP_STATUS = os.path.join(self.dir, "fixture", "absent_wip_status.py")
        reader_rows, code = mod.wip_reading("HEAD", "hub/main")
        self.assertEqual(code, 2)
        self.assertEqual(reader_rows[0][1], "NO-DATA")
        self.assertEqual(sys.path, before)

    def test_the_gate_off_reads_no_limit_and_writes_no_record(self):
        """The wiring's off step: BROTHER_WIP_GATE=off prints the OFF line, reads nothing, writes no record, and the
        unit starts as it did before ACC3.b."""
        out = self.pool_pass([unit("D3", ["D3.1"])], os.path.join(self.dir, "fixture", "absent_wip_status.py"),
                             env={"BROTHER_WIP_GATE": "off"}, scores={"D3.1": 10})
        self.assertIn("WIP     OFF: BROTHER_WIP_GATE=off", out)
        self.assertEqual(self.started(out), {"D3.1"}, out)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "evidence", "wip-status.json")))


if __name__ == "__main__":
    unittest.main()
