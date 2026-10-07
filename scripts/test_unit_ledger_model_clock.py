#!/usr/bin/env python3
"""FX-10.1: the unit ledger records the model's own time, reserve to close in the dispatcher ledger, never the fan out's
wall clock, which starts before the dispatcher slot wait (pace note: a median 358 s by that clock against 144 s of model).

Entry points: unit_ledger.append_run, the function grade_lane.sh calls after every grade, over a real run folder, with a
REAL dispatcher ledger written through openrouter_ledger.reserve and reconcile in a scratch state root; and
worker_mix.patience reading the rows it wrote. One condition per fixture. Nothing calls a model.
Run from the repository root: python3 -B scripts/test_unit_ledger_model_clock.py
"""
import json, os, shutil, sys, tempfile, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))
sys.path.insert(0, REPO)
import unit_ledger  # noqa: E402
import worker_mix  # noqa: E402
from plugin.runtime.brother.core import openrouter_ledger as OL  # noqa: E402

SCRATCH = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")


class Fixture(unittest.TestCase):
    def setUp(self):
        os.makedirs(SCRATCH, exist_ok=True)
        self.d = tempfile.mkdtemp(prefix="model-clock-", dir=SCRATCH)
        self.addCleanup(shutil.rmtree, self.d, True)
        # a proof run's policy lives in BROTHER_PROOF_* variables: none of them may leak into a scratch ledger
        env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_")}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start(); self.addCleanup(patcher.stop)
        self.state = os.path.join(self.d, "state"); os.makedirs(self.state)
        self.ledger = os.path.join(self.state, "openrouter-ledger.jsonl")
        self.plan = os.path.join(self.d, "plan.json")
        with open(self.plan, "w") as fh: json.dump({"units": []}, fh)
        self.out = os.path.join(self.d, "unit-ledger.jsonl")
        self.t0 = time.time() - 5000

    def round_dir(self, results, name="M1.1-000001", rn=0, t0=None, t_res=None):
        """One round: jobs.json at t0, results.json at t_res (default t0 + 600)."""
        t0 = self.t0 if t0 is None else t0
        rd = os.path.join(self.d, "runs", name, "round%d" % rn); os.makedirs(os.path.join(rd, "grades"), exist_ok=True)
        with open(os.path.join(rd, "jobs.json"), "w") as fh: json.dump([{"id": r["id"]} for r in results], fh)
        os.utime(os.path.join(rd, "jobs.json"), (t0, t0))
        with open(os.path.join(rd, "results.json"), "w") as fh: json.dump(results, fh)
        t_res = t0 + 600 if t_res is None else t_res
        os.utime(os.path.join(rd, "results.json"), (t_res, t_res))
        with open(os.path.join(self.d, "runs", name, "STATUS"), "w") as fh: fh.write("READY x\n")
        return os.path.join(self.d, "runs", name)

    def paid(self, holder, at, closed_at=None, close="reconcile"):
        rid = OL.reserve(self.state, 100, 0.02, holder, now=at)
        if closed_at is not None:
            if close == "reconcile": OL.reconcile(self.state, rid, 0.01, now=closed_at)
            else: OL.abandon(self.state, rid, "holder gone", now=closed_at)
        return rid

    def raw(self, row):
        with open(self.ledger, "a") as fh: fh.write((row if isinstance(row, str) else json.dumps(row)) + "\n")

    def rows(self, run):
        self.assertEqual(unit_ledger.append_run(run, self.out, self.ledger, self.plan), 0)
        return {r["build"]: r for r in unit_ledger.last_rows(self.out)}


def ok(bid, seconds=358.0, model="deepseek"):
    return {"id": bid, "model": model, "ok": True, "actual_model": model + "/v", "seconds": seconds, "bytes": 10}


class ModelClock(Fixture):
    def test_span_is_reserve_to_close(self):
        run = self.round_dir([ok("M1.1-r0")]); self.paid("M1.1-r0", self.t0 + 214, self.t0 + 358)
        r = self.rows(run)["M1.1-r0"]
        self.assertEqual((r["model_seconds"], r["model_clock"], r["reservations"], r["seconds"]), (144.0, "span", 1, 358.0))

    def test_spans_empty_ledger(self):
        self.assertEqual(unit_ledger.spans(os.path.join(self.state, "absent.jsonl")), ({}, 0))
        open(self.ledger, "w").close()
        self.assertEqual(unit_ledger.spans(self.ledger), ({}, 0))
        run = self.round_dir([])
        self.assertEqual(unit_ledger.run_rows(run, {}, {}, {}, spans={}), [])

    def test_no_reservation_is_none(self):
        run = self.round_dir([ok("M1.1-r0", model="sonnet")])
        r = self.rows(run)["M1.1-r0"]
        self.assertEqual((r["model_seconds"], r["model_clock"], r["reservations"]), (None, "none", 0))

    def test_open_reservation_is_not_a_sample(self):
        run = self.round_dir([ok("M1.1-r0")]); self.paid("M1.1-r0", self.t0 + 10)
        r = self.rows(run)["M1.1-r0"]
        self.assertEqual((r["model_seconds"], r["model_clock"]), (None, "open"))

    def test_abandoned_close_is_a_span(self):
        run = self.round_dir([ok("M1.1-r0")]); self.paid("M1.1-r0", self.t0 + 10, self.t0 + 70, close="abandon")
        self.assertEqual(self.rows(run)["M1.1-r0"]["model_seconds"], 60.0)

    def test_corrupt_span_is_never_a_sample(self):
        for name, close_at in (("C1.1-000001", "before"), ("C2.1-000002", "same"), ("C3.1-000003", "nan"), ("C4.1-000004", "text")):
            with self.subTest(close=close_at):
                bid = name.split("-")[0] + "-r0"
                run = self.round_dir([ok(bid)], name=name)
                rid = "%s-rid" % bid
                self.raw({"type": "RESERVE", "reservation_id": rid, "holder_id": bid, "estimated_cost": 0.02, "at": self.t0 + 100})
                at = {"before": self.t0 + 50, "same": self.t0 + 100, "nan": float("nan"), "text": "soon"}[close_at]
                self.raw('{"type": "RECONCILE", "reservation_id": "%s", "actual_cost": 0.01, "at": %s}' % (rid, json.dumps(at) if close_at != "nan" else "NaN"))
                r = self.rows(run)[bid]
                self.assertEqual((r["model_seconds"], r["model_clock"]), (None, "corrupt"))

    def test_reserve_without_a_time_is_corrupt_and_junk_is_counted(self):
        run = self.round_dir([ok("M1.1-r0")])
        self.raw({"type": "RESERVE", "reservation_id": "x", "holder_id": "M1.1-r0", "estimated_cost": 0.02})
        self.raw("not json"); self.raw("[1]")
        self.assertEqual(unit_ledger.spans(self.ledger)[1], 2)
        self.assertEqual(self.rows(run)["M1.1-r0"]["model_clock"], "corrupt")

    def test_first_close_wins(self):
        run = self.round_dir([ok("M1.1-r0")])
        self.raw({"type": "RESERVE", "reservation_id": "x", "holder_id": "M1.1-r0", "at": self.t0 + 100})
        self.raw({"type": "RECONCILE", "reservation_id": "x", "actual_cost": 0.01, "at": self.t0 + 250})
        self.raw({"type": "RECONCILE", "reservation_id": "x", "actual_cost": 0.01, "at": self.t0 + 500})
        self.assertEqual(self.rows(run)["M1.1-r0"]["model_seconds"], 150.0)

    def test_reused_build_id_joins_only_its_own_round(self):
        # an earlier round (or run) of the same sub paid under the same id, before this round's jobs.json
        self.paid("M1.1-r0", self.t0 - 900, self.t0 - 800)
        run = self.round_dir([ok("M1.1-r0")])
        r = self.rows(run)["M1.1-r0"]
        self.assertEqual((r["model_seconds"], r["model_clock"], r["reservations"]), (None, "none", 0))

    def test_selfcheck_repair_span_is_not_the_build_span(self):
        # the build call made no reservation (its slot wait ran out); the self check repair reused its id after results.json
        run = self.round_dir([ok("M1.1-r0")]); self.paid("M1.1-r0", self.t0 + 700, self.t0 + 760)
        r = self.rows(run)["M1.1-r0"]
        self.assertEqual((r["model_seconds"], r["model_clock"], r["reservations"]), (None, "none", 0))

    def test_retried_job_counts_reservations(self):
        run = self.round_dir([ok("M1.1-r0")])
        self.paid("M1.1-r0", self.t0 + 300, self.t0 + 400); self.paid("M1.1-r0", self.t0 + 20, self.t0 + 200)
        r = self.rows(run)["M1.1-r0"]
        self.assertEqual((r["model_seconds"], r["reservations"]), (180.0, 2))

    def test_queue_time_does_not_raise_patience(self):
        # five bridge builds that queued: the fan out's clock says 700 s, the model ran 200 s of each
        res = [ok("M1.1-r%d" % i, seconds=700.0) for i in range(5)]
        run = self.round_dir(res, t_res=self.t0 + 800)
        for i in range(5): self.paid("M1.1-r%d" % i, self.t0 + 400 + i, self.t0 + 600 + i)
        self.rows(run)
        on, off = {"BROTHER_ONE_DEADLINE": "on"}, {"BROTHER_ONE_DEADLINE": "off"}
        now = time.time()
        self.assertEqual(worker_mix.patience(["deepseek"], {"deepseek": "bridge"}, self.out, now=now, env=on), 200)
        self.assertEqual(worker_mix.patience(["deepseek"], {"deepseek": "bridge"}, self.out, now=now, env=off), 700)

    def test_blended_cost_callers_still_read_rows(self):
        run = self.round_dir([ok("M1.1-r0")]); self.paid("M1.1-r0", self.t0 + 10, self.t0 + 70)
        self.assertEqual(unit_ledger.blended_usd("M1.1", os.path.dirname(run), self.ledger), 0.01)
        self.assertEqual(unit_ledger.run_rows(run, {}, {}, {})[0]["model_clock"], "none")   # no spans argument: none found


if __name__ == "__main__":
    unittest.main()
