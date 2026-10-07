#!/usr/bin/env python3
"""D-8 (U12, objections 5, 6, 7): the proof pair launcher's refusals and its handoff, driven with a stub driver.

The real proof_pair.sh runs from a scratch bin (scripts/loop copied, the candidate staged from this working tree by
deploy_stamped.stage_candidate, the fixture of test_proof_pair_rehearsal.py) whose loop_until.sh is a stub: it records
its argv and environment per phase, creates the run directory the launcher named with a plain mkdir, and ends the way
the case asks (a DEADLINE receipt, an UNFUNDED one, exit 1, or a refusal). Everything else is the real launcher: its
preflight, the ledger baseline, the freeze under E, the pair record, the results, the handoff and acceptance.

One condition per case:
  refusals before RB (exit 2, no pair directory, the driver never runs): BROTHER_CHECKER exported only by
  launch-env.sh (objection 5); a pair record that does not cover the pair (objection 6); a non pair record; the
  rehearsal window knob without --rehearsal; --rehearsal without its window; LOOP-HOLD.txt; LOOP-PAUSE.txt; BOUNDED_ABANDON_COUNTS on with a stale
  price catalog.
  after RB (exit 3, no RC): the RB launch refused (exit 2, the result records no receipt); RB ended UNFUNDED;
  RB exited 1 with a DEADLINE receipt; a HOLD appeared during RB (left in place); the pair record stopped covering RC.
  the handoff (RC runs): RC's environment equals RB's except the volatile names, each deadline is a minute boundary
  at least window + 180 s after its launch, pair.json names both directories and the frozen manifest's digest, and
  acceptance's verdict is kept in the pair directory.
Run: python3 -B scripts/test_proof_pair_handoff.py
"""
import datetime
import hashlib
import importlib.util
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "loop"))
import freeze_manifest  # noqa: E402

_spec = importlib.util.spec_from_file_location("pair_fixture", os.path.join(HERE, "test_proof_pair_rehearsal.py"))
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)

STUB = r'''#!/bin/bash
# stub driver: records argv and environment, then ends as $HOME/stub-<phase> says
P="${BROTHER_PROOF_PHASE:-none}"
python3 -B -c 'import json, os, sys; json.dump(dict(os.environ), open(sys.argv[1], "w"))' "$HOME/driver-env.$P.json"
printf '%s\n' "$@" > "$HOME/driver-argv.$P"
date +%s > "$HOME/driver-started.$P"
MODE=$(cat "$HOME/stub-$P" 2>/dev/null || echo deadline)
[ "$MODE" = refuse ] && { echo "REFUSED TO START: stub"; exit 2; }
mkdir "$BROTHER_PROOF_RUN_DIR" || exit 2
mkdir "$BROTHER_PROOF_RUN_DIR/proof" "$BROTHER_PROOF_RUN_DIR/receipt"
echo '{}' > "$BROTHER_PROOF_RUN_DIR/proof/evidence.json"
STATE=DEADLINE; [ "$MODE" = unfunded ] && STATE=UNFUNDED
printf '{"run_id": "%s", "end_state": "%s"}\n' "$(basename "$BROTHER_PROOF_RUN_DIR")" "$STATE" > "$BROTHER_PROOF_RUN_DIR/receipt/receipt.json"
[ "$MODE" = hold ] && echo "owner: stop after this run" > "$HOME/.claude/evidence/LOOP-HOLD.txt"
[ "$MODE" = shrink ] && python3 -B -c 'import json, sys; p = sys.argv[1]; r = json.load(open(p)); r["pair_until"] = sys.argv[2]; json.dump(r, open(p, "w"))' \
  "$HOME/.claude/evidence/loop-intake/CURRENT.json" "$(python3 -c 'import datetime; print((datetime.datetime.now().astimezone() + datetime.timedelta(hours=1)).isoformat())')"
[ "$MODE" = exit1 ] && exit 1
exit 0
'''


class Handoff(unittest.TestCase):
    def setUp(self):
        if not F.PS_RUNS:   # the export sandbox denies ps, which every launch here reads: NO-DATA by name, never an error
            self.skipTest(F.NO_PS)
        self.s = F.Scratch(deploy=False)
        F.w(os.path.join(self.s.bin, "loop_until.sh"), STUB, 0o755)

    def tearDown(self):
        self.s.remove()

    def launch(self, **kw):
        return self.s.launch(rehearsal=False, timeout=600, **kw)

    def mode(self, phase, mode):
        F.w(os.path.join(self.s.home, "stub-" + phase), mode)

    def ran(self, phase):
        return os.path.exists(os.path.join(self.s.home, "driver-argv." + phase))

    def pairs(self):
        return [d for d in os.listdir(self.s.runs) if d.startswith("pair-")] if os.path.isdir(self.s.runs) else []

    def refused_before_rb(self, code, lines, words):
        out = "\n".join(lines)
        self.assertEqual(code, 2, out)
        self.assertTrue(any(l.startswith("PAIR REFUSED") for l in lines), out)
        self.assertIn(words, out)
        self.assertFalse(self.ran("RB"), out)
        self.assertEqual(self.pairs(), [], out)

    # ---- refusals before RB
    def test_a_checker_exported_only_by_the_launch_settings_refuses(self):
        self.s.write_launch_env("export BROTHER_SCOPE='x'\nexport BROTHER_CHECKER=strong\n")
        self.refused_before_rb(*self.launch()[:2], words="BROTHER_CHECKER")

    def test_a_pair_record_that_does_not_cover_the_pair_refuses(self):
        self.s.write_intake({"pair_until": (datetime.datetime.now().astimezone() + datetime.timedelta(hours=10)).isoformat()})
        self.refused_before_rb(*self.launch()[:2], words="cover")

    def test_a_non_pair_record_refuses(self):
        self.s.write_intake({"pair": None, "pair_until": None})
        self.refused_before_rb(*self.launch()[:2], words="pair")

    def test_the_rehearsal_knob_without_rehearsal_refuses(self):
        self.s.write_launch_env("export BROTHER_SCOPE='x'\nexport BROTHER_PROOF_MIN_WINDOW_S=60\n")
        self.refused_before_rb(*self.launch()[:2], words="BROTHER_PROOF_MIN_WINDOW_S")

    def test_rehearsal_without_its_window_refuses(self):
        self.refused_before_rb(*self.s.launch(rehearsal=True, timeout=600, BROTHER_PROOF_MIN_WINDOW_S="")[:2],
                               words="BROTHER_PROOF_MIN_WINDOW_S")

    def test_a_hold_refuses_and_is_left_in_place(self):
        hold = os.path.join(self.s.evidence, "LOOP-HOLD.txt")
        F.w(hold, "owner: not tonight\n")
        self.refused_before_rb(*self.launch()[:2], words="not tonight")
        self.assertEqual(F.slurp(hold), "owner: not tonight\n")

    def test_a_pause_refuses_and_is_left_in_place(self):
        pause = os.path.join(self.s.evidence, "LOOP-PAUSE.txt")
        F.w(pause, "owner: going out\n")
        self.refused_before_rb(*self.launch()[:2], words="going out")
        self.assertEqual(F.slurp(pause), "owner: going out\n")

    def test_bounded_abandons_on_with_a_stale_catalog_refuses(self):
        p = os.path.join(self.s.bin, "proof_ledger.py")
        src = F.slurp(p)
        # VALUE AGNOSTIC: the shipped switch may read either way (the owner turned it on, Q1); exactly one of the two
        # lines must be there, and this case needs it ON, since only then is the catalog's age checked at all.
        on, off = "\nBOUNDED_ABANDON_COUNTS = True\n", "\nBOUNDED_ABANDON_COUNTS = False\n"
        self.assertEqual((src.count(on) + src.count(off), src.count(on) * src.count(off)), (1, 0), "the switch line")
        F.w(p, src.replace(off, on))
        catalog = os.path.join(self.s.state, "openrouter-models.json")
        F.w(catalog, json.dumps({"data": []}) + "\n")
        old = time.time() - 6.5 * 86400
        os.utime(catalog, (old, old))
        self.refused_before_rb(*self.launch()[:2], words="catalog")

    # ---- after RB: no RC
    def stopped_after_rb(self, code, lines):
        out = "\n".join(lines)
        self.assertEqual(code, 3, out)
        self.assertTrue(any(l.startswith("PAIR STOPPED after RB") for l in lines), out)
        self.assertFalse(self.ran("RC"), out)
        pair = self.s.pair_dir(lines)
        self.assertFalse(os.path.exists(os.path.join(pair, "RC.result.json")), out)
        return pair

    def test_a_refused_rb_launch_is_recorded_without_a_receipt(self):
        self.mode("RB", "refuse")
        pair = self.stopped_after_rb(*self.launch()[:2])
        result = self.s.read(pair, "RB.result.json")
        self.assertEqual((result["exit_code"], result["receipt_sha256"]), (2, None))

    def test_rb_ending_unfunded_starts_no_rc(self):
        self.mode("RB", "unfunded")
        pair = self.stopped_after_rb(*self.launch()[:2])
        self.assertEqual(self.s.read(pair, "RB.result.json")["exit_code"], 0)

    def test_rb_exit_1_with_a_deadline_receipt_starts_no_rc(self):
        self.mode("RB", "exit1")
        pair = self.stopped_after_rb(*self.launch()[:2])
        self.assertEqual(self.s.read(pair, "RB.result.json")["exit_code"], 1)

    def test_a_hold_that_appears_during_rb_starts_no_rc_and_stays(self):
        self.mode("RB", "hold")
        self.stopped_after_rb(*self.launch()[:2])
        self.assertTrue(os.path.isfile(os.path.join(self.s.evidence, "LOOP-HOLD.txt")))

    def test_a_record_that_stops_covering_rc_starts_no_rc(self):
        self.mode("RB", "shrink")
        self.stopped_after_rb(*self.launch()[:2])

    # ---- the handoff
    def test_rc_runs_on_rb_s_environment_except_the_volatile_names(self):
        t0 = time.time()
        code, lines, _ = self.launch(PAIR_CALLER_ONLY="the caller's own variable")
        out = "\n".join(lines)
        self.assertTrue(self.ran("RB") and self.ran("RC"), out)
        env = {p: json.loads(F.slurp(os.path.join(self.s.home, "driver-env.%s.json" % p))) for p in ("RB", "RC")}
        differ = {k for k in set(env["RB"]) | set(env["RC"]) if env["RB"].get(k) != env["RC"].get(k)}
        self.assertEqual(differ, {"BROTHER_PROOF_PHASE", "BROTHER_PROOF_RUN_DIR"}, out)
        self.assertTrue(differ <= freeze_manifest.VOLATILE_ENV)
        self.assertNotIn("BROTHER_CHECKER", env["RB"])
        self.assertNotIn("PAIR_CALLER_ONLY", env["RB"])          # E starts from env -i: nothing of the caller leaks in
        self.assertEqual((env["RB"]["PYTHONDONTWRITEBYTECODE"], env["RB"]["PYTHONNOUSERSITE"]), ("1", "1"))
        self.assertTrue(os.path.isabs(env["RB"]["BROTHER_CLAUDE_CALLS_LEDGER"]))
        self.assertEqual(env["RB"]["BROTHER_CODE_ROOT"], os.path.join(self.s.bin, "candidate"))
        for phase in ("RB", "RC"):
            argv = F.slurp(os.path.join(self.s.home, "driver-argv." + phase)).splitlines()
            started = int(F.slurp(os.path.join(self.s.home, "driver-started." + phase)))
            deadline = time.mktime(time.strptime(argv[0], "%Y-%m-%d %H:%M"))
            self.assertEqual(argv[1], F.GAP)
            self.assertEqual(int(deadline) % 60, 0)
            self.assertGreaterEqual(deadline, started + 8 * 3600 + 180 - 2)
            self.assertLess(deadline, started + 8 * 3600 + 180 + 62)
        pair = self.s.pair_dir(lines)
        rec = self.s.read(pair, "pair.json")
        manifest = F.slurp(os.path.join(pair, "freeze.json"), "rb")
        self.assertEqual(rec["manifest_sha256"], hashlib.sha256(manifest).hexdigest())
        self.assertEqual((os.path.basename(rec["rb_dir"]), os.path.basename(rec["rc_dir"])),
                         ("run-RB-" + os.path.basename(pair)[len("pair-"):], "run-RC-" + os.path.basename(pair)[len("pair-"):]))
        self.assertEqual(env["RB"]["BROTHER_FREEZE_MANIFEST"], os.path.join(pair, "freeze.json"))
        self.assertTrue(os.path.isfile(os.path.join(pair, "verdict.json")), out)
        self.assertTrue(any(l.startswith("PAIR VERDICT") for l in lines), out)
        self.assertIn(code, (1, 2))                      # a stub pair is never proof
        self.assertLess(time.time() - t0, 300)

    # ---- the model executables the owner pins
    def test_executables_pinned_in_the_launch_settings_reach_the_freeze_and_both_runs(self):
        """BROTHER_CLAUDE_BIN and BROTHER_CODEX_BIN set in launch-env.sh are in E, so the freeze records exactly those
        files and both runs call them: the executables the proof runs are the ones it froze."""
        code, lines, _ = self.launch()
        out = "\n".join(lines)
        self.assertTrue(self.ran("RB") and self.ran("RC"), out)
        pinned = {"BROTHER_CLAUDE_BIN": self.s.cli["claude"], "BROTHER_CODEX_BIN": self.s.cli["codex"]}
        for phase in ("RB", "RC"):
            env = json.loads(F.slurp(os.path.join(self.s.home, "driver-env.%s.json" % phase)))
            self.assertEqual({k: env.get(k) for k in pinned}, pinned, phase)
        manifest = json.loads(F.slurp(os.path.join(self.s.pair_dir(lines), "freeze.json")))
        self.assertEqual(manifest["model_executables"], self.s.cli, out)

    def test_a_pin_only_in_the_callers_shell_never_reaches_the_pair(self):
        """E starts from env -i and the launch settings: a pin exported only by the caller is not the pair's, so in this
        scratch HOME (no Claude CLI) the freeze refuses by name and RB never runs."""
        self.s.write_launch_env("export BROTHER_SCOPE='%s'\n" % F.proof_accept.SCOPE, pins=False)   # the default, unpinned
        # THE PREMISE MADE TRUE (2026-10-04): the router's unpinned resolution scans PATH since 2026-09-30, so on a machine
        # with a Claude CLI on PATH the freeze found one and RB ran. "No Claude CLI" is this case's premise, so the caller's
        # PATH drops every directory holding an executable claude; the fixture's shim stays first.
        clean = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                                if d and not os.access(os.path.join(d, "claude"), os.X_OK))
        code, lines, _ = self.launch(BROTHER_CLAUDE_BIN=self.s.cli["claude"], BROTHER_CODEX_BIN=self.s.cli["codex"],
                                     PATH=self.s.shim + os.pathsep + clean)
        out = "\n".join(lines)
        self.assertEqual(code, 2, out)
        self.assertIn("model executable claude cannot be frozen", out)
        self.assertFalse(self.ran("RB"), out)


if __name__ == "__main__":
    unittest.main()
