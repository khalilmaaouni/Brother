#!/usr/bin/env python3
"""Drive scripts/cut.py backwards with a scripted runner, never the live
chain: every subprocess the orchestrator would spawn is answered by a
fixture keyed on the command, and the test reads the ORDER and the fence
bookkeeping (claim, park, complete) that the founder's complaint of
2026-09-17 was about. The chain scripts themselves (required_fast.sh,
cut_v1.0.0.sh, refresh_cut.py, release_invariant.py, reproduce_export.py,
export_public.py) each have their own suites; this one only proves that
cut.py calls them in the documented order, stops at the first red, never
claims in --check, and releases the fence on every exit.

Exit contract, same shape as this estate's other suites: 0 all assertions
pass, 1 an assertion failed.

Python 3, stdlib only. No network. No em or en dashes anywhere in this file.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cut as C  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

VERSION = "9.9.9"
BM_PATH = "/fake/bm_store.py"
UUID = "a" * 32

CLAIM_OK = ("claimed 'release-cut-%s' as lifecycle %s (version 1, session "
            "cli-x)\n" % (VERSION, UUID))
FAST_OK = ("Brother: required-fast, the pre-merge contract\n\n"
           "PASS    exit 0   version-truth          1s  ok\n\n"
           "pass 30   fail 0   no-data 2\n"
           "NO-DATA: plugin-manifest closing-ceremony-real  (not a pass, "
           "and not a failure)\n")
FAST_RED = ("Brother: required-fast, the pre-merge contract\n\n"
            "pass 29   fail 1   no-data 2\nFAILED: integrate\n")


LIVE_B = "b" * 32
LIVE_D = "d" * 32


def _dump(active_paths, second_paths=None, version=1):
    """A bm_store dump carrying one ACTIVE record fencing `active_paths`
    and one PARKED record fencing scripts/, which must never count; with
    `second_paths`, a second ACTIVE record (store version 7) as well.
    `version` is the record version the real dump exports (park needs it
    back as --version); None drops the key, the way an older store would."""
    records = [
        {"lifecycle_uuid": LIVE_B, "name": "lane-live", "version": version,
         "state": "active", "session_id": "[WITHHELD]",
         "objective": "[WITHHELD]"},
        {"lifecycle_uuid": "c" * 32, "name": "lane-parked", "version": 3,
         "state": "parked", "session_id": "[WITHHELD]",
         "objective": "[WITHHELD]"},
    ]
    claims = [{"lifecycle_uuid": LIVE_B, "path": p} for p in active_paths]
    claims.append({"lifecycle_uuid": "c" * 32, "path": "scripts/"})
    if second_paths:
        records.append({"lifecycle_uuid": LIVE_D, "name": "lane-two",
                        "version": 7, "state": "active",
                        "session_id": "[WITHHELD]",
                        "objective": "[WITHHELD]"})
        claims.extend({"lifecycle_uuid": LIVE_D, "path": p}
                      for p in second_paths)
    for rec in records:
        if rec["version"] is None:
            del rec["version"]
    return json.dumps({"records": records, "claims": claims})


class FakeBmStore(object):
    """The one function cut.py calls on the loaded module."""

    @staticmethod
    def paths_overlap(a, b):
        a = a.rstrip("/")
        b = b.rstrip("/")
        return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _key(cmd):
    """One label per command shape, the way cut.py builds them."""
    if cmd[0] == "git":
        return "git " + " ".join(cmd[1:3])
    if cmd[0] == "sh":
        return os.path.basename(cmd[1])
    if cmd[1] == BM_PATH:
        return "bm_store " + cmd[2]
    return os.path.basename(cmd[1])


class Runner(object):
    """Answers each command from `answers` (label -> (code, stdout)),
    default exit 0 with empty output, and records every label in order."""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.calls = []
        self.cmds = []
        self.cwds = []

    def __call__(self, cmd, **kw):
        label = _key(cmd)
        self.calls.append(label)
        self.cmds.append(list(cmd))
        self.cwds.append(kw.get("cwd"))
        code, out = self.answers.get(label, (0, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")


class ParkingRunner(Runner):
    """Runner where a park really parks: every later dump exports that
    record as parked, the way the real store would, so the scan after the
    cut's own claim sees what the force-conflicts pass left behind."""

    def __init__(self, answers=None):
        Runner.__init__(self, answers)
        self.parked = set()

    def __call__(self, cmd, **kw):
        label = _key(cmd)
        if label == "bm_store dump" and self.parked:
            code, out = self.answers[label]
            data = json.loads(out)
            for rec in data["records"]:
                if rec["lifecycle_uuid"] in self.parked:
                    rec["state"] = "parked"
            self.answers[label] = (code, json.dumps(data))
        proc = Runner.__call__(self, cmd, **kw)
        if label == "bm_store park" and proc.returncode == 0:
            self.parked.add(cmd[3])
        return proc


def _stall(dead_uuids):
    return json.dumps({"findings": [
        {"kind": "stale-fence", "lifecycle_uuid": u, "name": "lane",
         "severity": "high", "message": "has a dead owner", "actions": []}
        for u in dead_uuids]})


GREEN = {
    "next_cut.py": (0, "next cut weekday: Friday\nnext cut version: %s\n"
                       % VERSION),
    "bm_store dump": (0, _dump(["docs/plan/x.md"])),
    "bm_store claim": (0, CLAIM_OK),
    "bm_store park": (0, "parked: ok\n"),
    "bm_store complete": (0, "complete: ok\n"),
    "bm_store adopt": (0, "adopted: 'lane' (lifecycle x) is now adopted at "
                          "version 9\n"),
    "git status --porcelain": (0, ""),
    "git branch --show-current": (0, "main\n"),
    "git rev-parse --short": (0, "abc1234\n"),
    "required_fast.sh": (0, FAST_OK),
    "cut_v1.0.0.sh": (0, "== STOP. Review the output above. ==\n"),
    "refresh_cut.py": (0, "CLEAR: the manifest in this tree describes "
                          "this tree\n"),
    "release_invariant.py": (0, "release-invariant: identity chain "
                                "holds\n"),
    "export_public.py": (0, "export: pushed and tagged v%s\n" % VERSION),
    "reproduce_export.py": (0, "reproduce-export: PASS\n"),
}


def _main(argv, runner, answer="y", root="/fake/root"):
    """cut.main with the bm_store loader and the prompt both faked; the
    loader cache is cleared so each test decides what loads."""
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return answer

    del C._BM_STORE_CACHE[:]
    with mock.patch.object(C, "_load_bm_store",
                           return_value=(FakeBmStore, BM_PATH, None)), \
         mock.patch.object(C, "release_paths",
                           return_value=["scripts/", "bundle/"]):
        code = C.main(argv, root=root, runner=runner, ask=ask)
    del C._BM_STORE_CACHE[:]
    return code, asked


class TheVersionComesFromNextCut(unittest.TestCase):
    def test_the_next_cut_line_is_parsed_and_used(self):
        r = Runner(GREEN)
        code, _ = _main(["--check"], r)
        self.assertEqual(r.calls[0], "next_cut.py")
        refresh = [c for c in r.cmds if _key(c) == "refresh_cut.py"][0]
        self.assertIn(VERSION, refresh)
        self.assertEqual(code, C.EXIT_OK)

    def test_an_explicit_version_skips_next_cut(self):
        r = Runner(GREEN)
        _main(["--check", "--version", "1.2.3"], r)
        self.assertNotIn("next_cut.py", r.calls)

    def test_next_cut_no_data_is_no_data_here(self):
        r = Runner(dict(GREEN, **{"next_cut.py": (3, "NO-DATA: no weekday\n")}))
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_NODATA)


class CheckModeClaimsNothingAndWritesNothing(unittest.TestCase):
    def test_a_green_tree_reads_ready_without_a_claim_or_a_prompt(self):
        r = Runner(GREEN)
        code, asked = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertEqual(asked, [])
        for never in ("bm_store claim", "bm_store park", "bm_store complete",
                      "export_public.py", "reproduce_export.py"):
            self.assertNotIn(never, r.calls)
        for read in ("bm_store dump", "git status --porcelain",
                     "cut_v1.0.0.sh", "required_fast.sh", "refresh_cut.py",
                     "release_invariant.py"):
            self.assertIn(read, r.calls)
        refresh = [c for c in r.cmds if _key(c) == "refresh_cut.py"][0]
        self.assertIn("--check", refresh)

    def test_check_rehearses_the_writes_outside_this_checkout(self):
        """2026-09-17: --check runs the whole chain, bump included, but in
        a throwaway worktree it removes afterwards; nothing that writes may
        run with this checkout as its cwd."""
        r = Runner(GREEN)
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        add = [c for c in r.cmds if c[:3] == ["git", "worktree", "add"]]
        rm = [c for c in r.cmds if c[:3] == ["git", "worktree", "remove"]]
        self.assertEqual(len(add), 1, r.cmds)
        self.assertEqual(len(rm), 1, r.cmds)
        rehearsal = add[0][4]
        self.assertEqual(rm[0][-1], rehearsal)
        for label in ("cut_v1.0.0.sh", "required_fast.sh", "refresh_cut.py",
                      "release_invariant.py"):
            i = r.calls.index(label)
            self.assertEqual(r.cwds[i], rehearsal, (label, r.cwds[i]))
        self.assertLess(r.calls.index("cut_v1.0.0.sh"),
                        r.calls.index("required_fast.sh"))

    def test_a_failed_rehearsal_reads_not_ready_and_still_cleans_up(self):
        r = Runner(dict(GREEN, **{"release_invariant.py": (1, "FAIL\n")}))
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("git worktree add", r.calls)
        self.assertTrue(any(c[:3] == ["git", "worktree", "remove"]
                            for c in r.cmds), r.cmds)

    def test_a_dirty_tree_is_reported_and_reads_not_ready(self):
        r = Runner(dict(GREEN, **{"git status --porcelain":
                                  (0, " M README.md\n")}))
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("bm_store claim", r.calls)

    def test_a_red_gate_reads_not_ready(self):
        r = Runner(dict(GREEN, **{"required_fast.sh": (1, FAST_RED)}))
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_REFUSED)

    def test_a_conflict_still_reads_every_other_step_in_check_mode(self):
        # A readiness report carries everything to clear at once; only a
        # real cut stops at the first block.
        r = Runner(dict(GREEN, **{"bm_store dump":
                                  (0, _dump(["scripts/foo.py"])),
                                  "git status --porcelain":
                                  (0, " M README.md\n")}))
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        for read in ("git status --porcelain", "required_fast.sh",
                     "refresh_cut.py", "release_invariant.py"):
            self.assertIn(read, r.calls)

    def test_a_missing_bm_store_still_reports_the_rest(self):
        r = Runner(GREEN)
        del C._BM_STORE_CACHE[:]
        with mock.patch.object(C, "_load_bm_store",
                               return_value=(None, None, "absent")), \
             mock.patch.object(C, "release_paths",
                               return_value=["scripts/"]):
            code = C.main(["--check"], root="/fake", runner=r, ask=None)
        del C._BM_STORE_CACHE[:]
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertNotIn("bm_store dump", r.calls)
        self.assertIn("required_fast.sh", r.calls)


class LiveFencesOverlappingTheReleasePathsRefuse(unittest.TestCase):
    def test_an_active_claim_on_scripts_refuses_before_any_write(self):
        r = Runner(dict(GREEN, **{"bm_store dump":
                                  (0, _dump(["scripts/foo.py"]))}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("cut_v1.0.0.sh", r.calls)
        self.assertNotIn("required_fast.sh", r.calls)
        # The cut's own fence was claimed, so it is parked, never left.
        self.assertIn("bm_store claim", r.calls)
        self.assertIn("bm_store park", r.calls)
        self.assertNotIn("bm_store complete", r.calls)

    def test_a_parked_claim_on_scripts_does_not_count(self):
        r = Runner(GREEN)  # only the parked record fences scripts/
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)

    def test_force_conflicts_proceeds_and_lands_in_the_evidence(self):
        r = ParkingRunner(dict(GREEN, **{"bm_store dump":
                                         (0, _dump(["scripts/foo.py"])),
                                         "bm_stall.py": (1, _stall([LIVE_B]))}))
        code, _ = _main(["--yes", "--force-conflicts", "lane is dead"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        complete = [c for c in r.cmds if _key(c) == "bm_store complete"][0]
        self.assertIn("lane is dead", " ".join(complete))

    def test_without_the_flag_nothing_but_the_cuts_own_fence_is_parked(self):
        # Regression: the precedence fix must never park another session's
        # claim unless the operator passed --force-conflicts REASON.
        r = Runner(dict(GREEN, **{"bm_store dump":
                                  (0, _dump(["scripts/foo.py"],
                                            ["bundle/x"]))}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        parks = [c for c in r.cmds if _key(c) == "bm_store park"]
        self.assertEqual([p[3] for p in parks], [UUID], r.calls)
        self.assertLess(r.calls.index("bm_store claim"),
                        r.calls.index("bm_store dump"))
        self.assertNotIn("required_fast.sh", r.calls)

    def test_check_with_the_flag_parks_nothing(self):
        r = Runner(dict(GREEN, **{"bm_store dump":
                                  (0, _dump(["scripts/foo.py"]))}))
        code, _ = _main(["--check", "--force-conflicts", "lane is dead"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertNotIn("bm_store park", r.calls)
        self.assertNotIn("bm_store claim", r.calls)


class ForceConflictsParksTheOverlapsBeforeTheClaim(unittest.TestCase):
    TWO = {"bm_store dump": (0, _dump(["scripts/foo.py"], ["bundle/x"]))}

    def test_each_conflict_is_adopted_then_parked_with_uuid_version_reason(self):
        """2026-09-17: a real store refuses a non-owner's park of an ACTIVE
        record, so each dead-owner record is adopted at its dump version,
        then parked at the version the adopt printed."""
        r = ParkingRunner(dict(GREEN, **dict(self.TWO, **{
            "bm_stall.py": (1, _stall([LIVE_B, LIVE_D]))})))
        code, _ = _main(["--yes", "--force-conflicts", "lane is dead"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        adopts = [c for c in r.cmds if _key(c) == "bm_store adopt"]
        self.assertEqual([a[3] for a in adopts], [LIVE_B, LIVE_D], r.calls)
        for a, ver in zip(adopts, ("1", "7")):
            self.assertEqual(a[a.index("--version") + 1], ver)
            self.assertIn("--adopt-from-live-session", a)
        parks = [c for c in r.cmds if _key(c) == "bm_store park"]
        self.assertEqual([p[3] for p in parks], [LIVE_B, LIVE_D], r.calls)
        for p in parks:
            self.assertEqual(p[2], "park")
            self.assertEqual(p[p.index("--version") + 1], "9")
            self.assertEqual(p[p.index("--note") + 1],
                             "lane is dead, superseded by release-cut-%s"
                             % VERSION)
            self.assertRegex(p[p.index("--session") + 1], r"^cli-[0-9a-f]{32}$")
        # Liveness read first, then adopt before park, both before the
        # cut's own claim; the claim is then completed, never parked.
        self.assertLess(r.calls.index("bm_stall.py"),
                        r.calls.index("bm_store adopt"))
        self.assertLess(r.calls.index("bm_store adopt"),
                        r.calls.index("bm_store park"))
        self.assertLess(r.calls.index("bm_store park"),
                        r.calls.index("bm_store claim"))
        self.assertEqual(r.calls.count("bm_store park"), 2)
        self.assertIn("bm_store complete", r.calls)

    def test_a_live_owner_refuses_before_any_adopt_or_park(self):
        """Only bm_stall's DEAD verdicts are eligible: one live owner among
        the conflicts refuses the whole pass with nothing written."""
        r = ParkingRunner(dict(GREEN, **dict(self.TWO, **{
            "bm_stall.py": (1, _stall([LIVE_B]))})))
        code, _ = _main(["--yes", "--force-conflicts", "lane is dead"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        for never in ("bm_store adopt", "bm_store park", "bm_store claim"):
            self.assertNotIn(never, r.calls)

    def test_an_unreadable_liveness_sweep_refuses_before_any_write(self):
        r = ParkingRunner(dict(GREEN, **dict(self.TWO, **{
            "bm_stall.py": (2, "NO-DATA: store unreadable\n")})))
        code, _ = _main(["--yes", "--force-conflicts", "lane is dead"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        for never in ("bm_store adopt", "bm_store park", "bm_store claim"):
            self.assertNotIn(never, r.calls)

    def test_a_failed_park_stops_at_once_and_claims_nothing(self):
        r = ParkingRunner(dict(GREEN, **dict(self.TWO, **{
            "bm_stall.py": (1, _stall([LIVE_B, LIVE_D])),
            "bm_store park": (2, "stale version\n")})))
        code, _ = _main(["--yes", "--force-conflicts", "lane is dead"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        parks = [c for c in r.cmds if _key(c) == "bm_store park"]
        self.assertEqual([p[3] for p in parks], [LIVE_B], r.calls)
        self.assertNotIn("bm_store claim", r.calls)
        self.assertNotIn("bm_store complete", r.calls)
        self.assertNotIn("required_fast.sh", r.calls)
        self.assertNotIn("git status --porcelain", r.calls)

    def test_a_record_without_a_version_cannot_be_parked_so_nothing_is(self):
        r = ParkingRunner(dict(GREEN, **{"bm_store dump":
                                         (0, _dump(["scripts/foo.py"],
                                                   version=None))}))
        code, _ = _main(["--yes", "--force-conflicts", "lane is dead"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("bm_store park", r.calls)
        self.assertNotIn("bm_store claim", r.calls)

    def test_an_unreadable_scan_claims_nothing(self):
        r = ParkingRunner(dict(GREEN, **{"bm_store dump": (1, "locked\n")}))
        code, _ = _main(["--yes", "--force-conflicts", "lane is dead"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("bm_store park", r.calls)
        self.assertNotIn("bm_store claim", r.calls)

    def test_a_fence_claimed_after_the_park_pass_refuses(self):
        # Plain Runner: its park parks nothing, so the scan after the claim
        # still sees a live overlap, exactly a claim landing in between.
        r = Runner(dict(GREEN, **{"bm_store dump":
                                  (0, _dump(["scripts/foo.py"]))}))
        code, _ = _main(["--yes", "--force-conflicts", "lane is dead"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("required_fast.sh", r.calls)
        self.assertNotIn("bm_store complete", r.calls)

    def test_force_conflicts_without_a_reason_is_no_data(self):
        r = Runner(GREEN)
        code, _ = _main(["--yes", "--force-conflicts", " "], r)
        self.assertEqual(code, C.EXIT_NODATA)
        self.assertEqual(r.calls, [])

    def test_the_scan_is_read_only_dump_never_dashboard(self):
        r = Runner(GREEN)
        _main(["--check"], r)
        dump = [c for c in r.cmds if c[1] == BM_PATH]
        self.assertTrue(dump)
        self.assertTrue(all(c[2] == "dump" for c in dump), dump)


class TheFenceIsReleasedOnEveryExit(unittest.TestCase):
    def _park_note(self, r):
        parks = [c for c in r.cmds if _key(c) == "bm_store park"]
        self.assertEqual(len(parks), 1, r.calls)
        self.assertIn("--version", parks[0])
        self.assertEqual(parks[0][parks[0].index("--version") + 1], "1")
        return parks[0][parks[0].index("--note") + 1]

    def test_a_dirty_tree_parks_with_the_reason(self):
        r = Runner(dict(GREEN, **{"git status --porcelain":
                                  (0, "?? scratch.txt\n")}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("required_fast.sh", r.calls)
        self.assertIn("uncommitted", self._park_note(r))

    def test_a_red_required_fast_parks_and_never_reaches_the_push(self):
        """required_fast.sh runs on the bumped tree, after cut_v1.0.0.sh's
        local commits and before the push; red there stops everything
        irreversible."""
        r = Runner(dict(GREEN, **{"required_fast.sh": (1, FAST_RED)}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("cut_v1.0.0.sh", r.calls)
        self.assertNotIn("export_public.py", r.calls)
        self.assertNotIn("refresh_cut.py", r.calls)
        self.assertIn("required_fast", self._park_note(r))

    def test_a_red_cut_script_parks_and_never_reaches_the_push(self):
        r = Runner(dict(GREEN, **{"cut_v1.0.0.sh": (1, "boom\n")}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("export_public.py", r.calls)
        self.assertIn("cut_v1.0.0.sh", self._park_note(r))

    def test_a_decline_at_the_prompt_parks_and_exits_0(self):
        r = Runner(GREEN)
        code, asked = _main([], r, answer="n")
        self.assertEqual(code, C.EXIT_OK)
        self.assertEqual(asked, ["Approve cut and push tag v%s? [y/N] "
                                 % VERSION])
        self.assertNotIn("export_public.py", r.calls)
        self.assertIn("declined by operator before push", self._park_note(r))

    def test_a_failed_push_parks_and_exits_1(self):
        r = Runner(dict(GREEN, **{"export_public.py": (1, "REFUSED\n")}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("reproduce_export.py", r.calls)
        self.assertIn("export_public", self._park_note(r))

    def test_a_failed_reproduction_after_the_push_parks_and_exits_1(self):
        r = Runner(dict(GREEN, **{"reproduce_export.py": (1, "FAIL\n")}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("pushed tag", self._park_note(r))
        self.assertNotIn("bm_store complete", r.calls)

    def test_a_keyboard_interrupt_mid_chain_parks(self):
        r = Runner(GREEN)

        def interrupted(*a, **k):
            raise KeyboardInterrupt()

        with mock.patch.object(C, "required_fast", interrupted):
            code, _ = _main(["--yes"], r)
        self.assertEqual(code, 130)
        self.assertIn("interrupted", self._park_note(r))

    def test_a_crash_mid_chain_still_parks_then_raises(self):
        r = Runner(GREEN)

        def crash(*a, **k):
            raise RuntimeError("synthetic")

        with mock.patch.object(C, "required_fast", crash):
            with self.assertRaises(RuntimeError):
                _main(["--yes"], r)
        self.assertIn("crash", self._park_note(r))

    def test_a_failed_park_is_shouted_but_keeps_the_original_code(self):
        r = Runner(dict(GREEN, **{"required_fast.sh": (1, FAST_RED),
                                  "bm_store park": (2, "stale identity\n")}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("bm_store park", r.calls)

    def test_a_refused_claim_exits_1_and_parks_nothing(self):
        r = Runner(dict(GREEN, **{"bm_store claim":
                                  (2, "bm_store: refused (overlap)\n")}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("bm_store park", r.calls)
        self.assertNotIn("required_fast.sh", r.calls)

    def test_no_bm_store_refuses_a_real_cut_as_no_data(self):
        r = Runner(GREEN)
        del C._BM_STORE_CACHE[:]
        with mock.patch.object(C, "_load_bm_store",
                               return_value=(None, None, "absent")):
            code = C.main(["--yes"], root="/fake", runner=r, ask=None)
        del C._BM_STORE_CACHE[:]
        self.assertEqual(code, C.EXIT_NODATA)
        self.assertNotIn("required_fast.sh", r.calls)


class TheGreenPathRunsTheChainInOrderAndCompletes(unittest.TestCase):
    def test_order_claim_gates_cut_push_reproduce_complete(self):
        r = Runner(GREEN)
        code, asked = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertEqual(asked, [])
        wanted = ["bm_store claim", "bm_store dump", "git status --porcelain",
                  "cut_v1.0.0.sh", "required_fast.sh", "refresh_cut.py",
                  "release_invariant.py", "export_public.py",
                  "reproduce_export.py", "bm_store complete"]
        seen = [c for c in r.calls if c in wanted]
        self.assertEqual(seen, wanted, r.calls)
        self.assertNotIn("bm_store park", r.calls)

    def test_the_claim_names_the_version_and_fences_the_release_paths(self):
        r = Runner(GREEN)
        _main(["--yes"], r)
        claim = [c for c in r.cmds if _key(c) == "bm_store claim"][0]
        self.assertEqual(claim[3], "release-cut-%s" % VERSION)
        self.assertIn("--lifetime", claim)
        self.assertIn("ephemeral", claim)
        files = claim[claim.index("--files") + 1:]
        self.assertEqual(files, ["scripts/", "bundle/"])
        # 2026-09-17: one fresh run identity on every store change, in
        # bm_store's own cli-<uuid4 hex> shape; the claim and its release
        # must carry the same one or the release is refused "not-owner".
        sess = claim[claim.index("--session") + 1]
        self.assertRegex(sess, r"^cli-[0-9a-f]{32}$")
        complete = [c for c in r.cmds if _key(c) == "bm_store complete"][0]
        self.assertEqual(complete[complete.index("--session") + 1], sess)

    def test_each_run_mints_a_new_identity(self):
        seen = []
        for _ in range(2):
            r = Runner(GREEN)
            _main(["--yes"], r)
            claim = [c for c in r.cmds if _key(c) == "bm_store claim"][0]
            seen.append(claim[claim.index("--session") + 1])
        self.assertNotEqual(seen[0], seen[1])

    def test_the_push_command_is_the_documented_one(self):
        r = Runner(GREEN)
        _main(["--yes"], r)
        push = [c for c in r.cmds if _key(c) == "export_public.py"][0]
        self.assertEqual(push[2:], ["--push", "--tag", "v%s" % VERSION,
                                    "--prove-required-fast"])
        self.assertNotIn("--require-signed", push)

    def test_complete_carries_the_version_and_names_what_passed(self):
        r = Runner(GREEN)
        _main(["--yes"], r)
        complete = [c for c in r.cmds if _key(c) == "bm_store complete"][0]
        self.assertEqual(complete[3], UUID)
        self.assertEqual(complete[complete.index("--version") + 1], "1")
        evidence = complete[complete.index("--evidence") + 1]
        for name in ("required_fast", "cut_v1.0.0.sh", "export_public",
                     "reproduce_export"):
            self.assertIn(name, evidence)


class TheReleasePathsAreReadFromTheTree(unittest.TestCase):
    def test_products_with_manifests_are_fenced_and_others_are_not(self):
        import tempfile
        root = tempfile.mkdtemp(prefix="cut-test-")
        os.makedirs(os.path.join(root, "products", "alpha", ".claude-plugin"))
        with open(os.path.join(root, "products", "alpha", "CHECKSUMS.sha256"),
                  "w", encoding="utf-8") as fh:
            fh.write("x\n")
        os.makedirs(os.path.join(root, "products", "beta"))
        paths = C.release_paths(root)
        self.assertEqual(paths[:4], ["scripts/", "bundle/", "docs/releases/",
                                     ".claude-plugin/"])
        self.assertIn("products/alpha/CHECKSUMS.sha256", paths)
        self.assertIn("products/alpha/.claude-plugin/", paths)
        self.assertFalse(any("beta" in p for p in paths), paths)


class TheApproveQuestionWorksWithoutAKeyboard(unittest.TestCase):
    """A Claude Code shell has no keyboard: the founder answers in the
    question UI and the session writes his answer to --answer-file."""

    def _tmp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return os.path.join(d, "answer")

    def test_eof_at_the_prompt_declines_and_never_pushes(self):
        r = Runner(GREEN)

        def no_keyboard(prompt):
            raise EOFError()

        del C._BM_STORE_CACHE[:]
        with mock.patch.object(C, "_load_bm_store",
                               return_value=(FakeBmStore, BM_PATH, None)), \
             mock.patch.object(C, "release_paths",
                               return_value=["scripts/", "bundle/"]):
            code = C.main([], root="/fake/root", runner=r, ask=no_keyboard)
        self.assertEqual(code, C.EXIT_OK)
        self.assertNotIn("export_public.py", r.calls)

    def test_a_yes_written_to_the_file_is_read(self):
        path = self._tmp()
        ticks = []

        def sleep(s):
            ticks.append(s)
            with open(path, "w") as f:
                f.write("y\n")

        ask = C.file_asker(path, timeout_s=60, poll_s=1,
                           clock=lambda: len(ticks), sleep=sleep)
        self.assertEqual(ask("Approve? [y/N] "), "y")

    def test_no_answer_before_the_timeout_declines(self):
        path = self._tmp()
        ticks = []
        ask = C.file_asker(path, timeout_s=3, poll_s=1,
                           clock=lambda: len(ticks), sleep=ticks.append)
        self.assertEqual(ask("Approve? [y/N] "), "")
        self.assertEqual(len(ticks), 3)

    def test_a_stale_answer_file_refuses_before_any_claim(self):
        path = self._tmp()
        with open(path, "w") as f:
            f.write("y\n")
        r = Runner(GREEN)
        code, _ = _main(["--answer-file", path], r)
        self.assertEqual(code, C.EXIT_NODATA)
        self.assertNotIn("bm_store claim", r.calls)

    def test_yes_and_answer_file_together_is_no_data(self):
        r = Runner(GREEN)
        code, _ = _main(["--yes", "--answer-file", self._tmp()], r)
        self.assertEqual(code, C.EXIT_NODATA)
        self.assertNotIn("bm_store claim", r.calls)

    def test_a_no_in_the_file_declines_through_the_real_cut(self):
        path = self._tmp()
        r = Runner(GREEN)

        def founder_says_no(prompt):
            with open(path, "w") as f:
                f.write("n\n")
            return C.file_asker(path, timeout_s=5, poll_s=1,
                                sleep=lambda s: None)(prompt)

        del C._BM_STORE_CACHE[:]
        with mock.patch.object(C, "_load_bm_store",
                               return_value=(FakeBmStore, BM_PATH, None)), \
             mock.patch.object(C, "release_paths",
                               return_value=["scripts/", "bundle/"]):
            code = C.main(["--answer-file", path], root="/fake/root",
                          runner=r, ask=founder_says_no)
        self.assertEqual(code, C.EXIT_OK)
        self.assertNotIn("export_public.py", r.calls)
        self.assertIn("bm_store park", r.calls)

class TheStoreIsReadFromTheCheckoutThatOwnsIt(unittest.TestCase):
    """2026-09-17: run from ~/Brother-wt/cut-1019 the fence scan read
    NO-DATA, because bm_store walks up from its cwd and the store lives in
    the main checkout. Every bm_store call now runs from the parent of the
    common git dir."""

    def test_bm_store_runs_from_the_main_checkout(self):
        r = Runner(dict(GREEN, **{
            "git rev-parse --path-format=absolute":
                (0, "/main/checkout/.git\n")}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        bm = [i for i, c in enumerate(r.calls) if c.startswith("bm_store")]
        self.assertTrue(bm, r.calls)
        for i in bm:
            self.assertEqual(r.cwds[i], "/main/checkout", r.calls[i])

    def test_an_unreadable_common_dir_falls_back_to_root(self):
        r = Runner(dict(GREEN, **{
            "git rev-parse --path-format=absolute": (128, "")}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        i = r.calls.index("bm_store claim")
        self.assertEqual(r.cwds[i], "/fake/root")


class TheChainShowsProgressWhileItRuns(unittest.TestCase):
    """2026-09-17: the 1.0.19 rehearsal printed nothing for more than 50
    minutes, because each step was captured whole. A real (not faked)
    process tree proves a step's first line is shown before the step ends,
    through a Python grandchild that would block-buffer into a pipe, and
    that the step's own exit code survives."""

    def test_progress_appears_before_the_step_finishes(self):
        seen = []

        def emit(line):
            seen.append((time.monotonic(), line))

        child = ("import time; print('phase one started'); "
                 "time.sleep(2); print('phase one done'); raise SystemExit(3)")
        cmd = ["sh", "-c", '"%s" -c "%s"' % (sys.executable, child)]
        proc = C._stream("demo", cmd, os.getcwd(), emit=emit)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("phase one started", proc.stdout)
        self.assertIn("phase one done", proc.stdout)
        first = next(t for t, l in seen if "phase one started" in l)
        end = next(t for t, l in seen if "end demo: exit 3" in l)
        self.assertGreater(end - first, 1.5, seen)
        self.assertTrue(seen[0][1].startswith("[cut "), seen)
        self.assertIn("start demo", seen[0][1])

    def test_ansi_codes_are_stripped_from_the_live_lines(self):
        seen = []
        cmd = ["sh", "-c", "printf '\\033[31mred\\033[0m\\n'"]
        proc = C._stream("ansi", cmd, os.getcwd(), emit=seen.append)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("  ansi | red", seen, seen)

    def test_every_chain_step_runs_live_never_captured_silently(self):
        """The recurrence guard: a step added to the chain later through a
        captured-whole call would bring the silent hour back, so every
        long step of a real cut must reach _stream."""
        labels = []
        real = C._stream

        def spy(label, cmd, root, runner=None, emit=None, clock=None):
            labels.append(label)
            return real(label, cmd, root, runner, emit=lambda l: None)

        r = Runner(GREEN)
        with mock.patch.object(C, "_stream", spy):
            code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        for want in ("cut_v1.0.0.sh", "required_fast.sh",
                     "refresh_cut --check", "release_invariant",
                     "export_public --push", "reproduce_export"):
            self.assertIn(want, labels, labels)

    def test_a_missing_binary_is_exit_127_not_a_crash(self):
        seen = []
        proc = C._stream("gone", ["/nonexistent/binary-for-cut-test"],
                         os.getcwd(), emit=seen.append)
        self.assertEqual(proc.returncode, 127)
        self.assertTrue(any("exit 127" in l for l in seen), seen)


class PrecedenceAgainstARealTemporaryStore(unittest.TestCase):
    """2026-09-17: the park-only precedence path passed every fake-store test
    and was refused by the real store on the 1.0.19 cut ("not-owner"). These
    cases drive the REAL tools/bm_store.py and tools/bm_stall.py against an
    isolated store in a fresh temporary git directory, never the estate's
    own store. A far-future --now makes the owner's heartbeat DEAD for
    bm_stall; the present makes it LIVE."""

    FUTURE = "2030-01-01T00:00:00Z"

    def setUp(self):
        self.repo_root = os.path.dirname(os.path.dirname(
            os.path.abspath(C.__file__)))
        self.bm = C._load_bm_store(self.repo_root)
        if self.bm[0] is None:
            self.skipTest("bm_store.py not loadable: %s" % self.bm[2])
        self.bm_path = self.bm[1]
        self.tmp = tempfile.mkdtemp(prefix="cut-realstore-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = dict(os.environ)
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        os.makedirs(os.path.join(self.tmp, "scripts"))
        for cmd in (["init"],
                    ["claim", "dead-lane", "--session", "owner-session-a",
                     "--lifetime", "ephemeral", "--objective", "old work",
                     "--files", "scripts/old.py"]):
            p = subprocess.run([sys.executable, self.bm_path] + cmd,
                               cwd=self.tmp, capture_output=True, text=True,
                               env=env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def _record(self):
        p = subprocess.run([sys.executable, self.bm_path, "dump"],
                           cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        rec = [r for r in data["records"] if r["name"] == "dead-lane"][0]
        moves = [x.get("to_state") for x in data.get("transitions", [])
                 if x.get("lifecycle_uuid") == rec["lifecycle_uuid"]]
        return rec, moves

    def _conflicts(self):
        conflicts, why = C.live_conflicts(self.tmp, self.bm[0], self.bm_path,
                                          ["scripts/"])
        self.assertIsNotNone(conflicts, why)
        self.assertEqual([c["name"] for c in conflicts], ["dead-lane"])
        return conflicts

    def test_authorized_precedence_adopts_a_dead_owner_then_parks(self):
        conflicts = self._conflicts()
        dead, why = C.dead_owner_uuids(self.tmp, self.bm_path, now=self.FUTURE)
        self.assertIsNotNone(dead, why)
        self.assertIn(conflicts[0]["lifecycle_uuid"], dead)
        ok, lines = C.park_conflicts(self.tmp, self.bm_path, conflicts,
                                     "9.9.9", "founder chose precedence",
                                     dead=dead)
        self.assertTrue(ok, lines)
        rec, moves = self._record()
        self.assertEqual(rec["state"], "parked", lines)
        self.assertLess(moves.index("adopted"), moves.index("parked"), moves)
        self.assertTrue(any(l.startswith("adopted ") for l in lines), lines)
        # And the release fence can now be claimed over the same path.
        uuid, _, claim_lines = C.claim_fence(self.tmp, self.bm_path, "9.9.9",
                                             ["scripts/"])
        self.assertIsNotNone(uuid, claim_lines)

    def test_a_live_owner_stays_refused_and_untouched(self):
        conflicts = self._conflicts()
        dead, why = C.dead_owner_uuids(self.tmp, self.bm_path)
        self.assertIsNotNone(dead, why)
        self.assertNotIn(conflicts[0]["lifecycle_uuid"], dead)
        ok, lines = C.park_conflicts(self.tmp, self.bm_path, conflicts,
                                     "9.9.9", "founder chose precedence",
                                     dead=dead)
        self.assertFalse(ok)
        self.assertIn("not judged DEAD", "\n".join(lines))
        rec, moves = self._record()
        self.assertEqual(rec["state"], "active")
        self.assertNotIn("adopted", moves)

    def test_a_version_race_is_refused_and_leaves_the_record_active(self):
        conflicts = self._conflicts()
        dead, _ = C.dead_owner_uuids(self.tmp, self.bm_path, now=self.FUTURE)
        stale = [dict(conflicts[0], version=conflicts[0]["version"] + 4)]
        ok, lines = C.park_conflicts(self.tmp, self.bm_path, stale, "9.9.9",
                                     "founder chose precedence", dead=dead)
        self.assertFalse(ok)
        self.assertIn("ADOPT FAILED", "\n".join(lines))
        rec, moves = self._record()
        self.assertEqual(rec["state"], "active")
        self.assertEqual(rec["version"], conflicts[0]["version"])
        self.assertNotIn("adopted", moves)

    def test_without_authorization_the_cut_adopts_and_parks_nothing(self):
        paths = ["scripts/"]
        code = C.cut_mode(self.tmp, "9.9.9", self.bm, paths, None, True,
                          None)
        self.assertEqual(code, C.EXIT_REFUSED)
        rec, moves = self._record()
        self.assertEqual(rec["state"], "active")
        self.assertNotIn("adopted", moves)
        self.assertNotIn("parked", moves)


class ARealStoreCutLifecycle(unittest.TestCase):
    """2026-09-17, 1.0.19 attempt 3: after adopting and parking two dead
    owners, the cut refused its OWN fence as "claimed meanwhile" and could
    not release it ("not-owner"), because every bm_store process minted its
    own id. The whole cut_mode lifecycle runs here against a REAL store in a
    committed, clean temporary git repo, with every session variable
    removed from the environment; only the six expensive release stages are
    stubbed. The cut's run identity must carry claim through release."""

    EXPENSIVE = ("cut_v1.0.0.sh", "required_fast.sh", "refresh_cut.py",
                 "release_invariant.py", "export_public.py",
                 "reproduce_export.py")
    PATHS = ["scripts/", "docs/releases/"]
    SESSION_VARS = ("BM_FENCE_SESSION_ID", "CLAUDE_SESSION_ID",
                    "CLAUDE_CODE_SESSION_ID")

    def setUp(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(C.__file__)))
        self.bm = C._load_bm_store(repo_root)
        if self.bm[0] is None:
            self.skipTest("bm_store.py not loadable: %s" % self.bm[2])
        self.bm_path = self.bm[1]
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for var in self.SESSION_VARS:
            os.environ.pop(var, None)
        self.tmp = tempfile.mkdtemp(prefix="cut-lifecycle-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        git = lambda *a: subprocess.run(["git"] + list(a), cwd=self.tmp,
                                        check=True, capture_output=True)
        git("init", "-q")
        os.makedirs(os.path.join(self.tmp, "scripts"))
        with open(os.path.join(self.tmp, "scripts", "keep.txt"), "w") as fh:
            fh.write("x\n")
        with open(os.path.join(self.tmp, ".gitignore"), "w") as fh:
            fh.write(".brothermode/\nSTATE.md*\n")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        self.store("init")
        self.stubs = {
            "cut_v1.0.0.sh": (0, "== STOP. ==\n"),
            "required_fast.sh": (0, FAST_OK),
            "refresh_cut.py": (0, "CLEAR\n"),
            "release_invariant.py": (0, "release-invariant: holds\n"),
            "export_public.py": (0, "pushed\n"),
            "reproduce_export.py": (0, "reproduce-export: PASS\n"),
        }

    def store(self, *args):
        p = subprocess.run([sys.executable, self.bm_path] + list(args),
                           cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def records(self):
        return {r["name"]: r for r in json.loads(self.store("dump"))["records"]}

    def runner(self, cmd, **kw):
        name = os.path.basename(cmd[1] if cmd[0] in ("sh", sys.executable)
                                and len(cmd) > 1 else cmd[0])
        if name in self.EXPENSIVE:
            code, out = self.stubs[name]
            return subprocess.CompletedProcess(cmd, code, out, "")
        return subprocess.run(cmd, **kw)

    def cut(self, force=None, answer="n"):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = C.cut_mode(self.tmp, "9.9.9", self.bm, self.PATHS, force,
                              False, lambda prompt: answer, self.runner)
        return code, buf.getvalue()

    def test_a_decline_excludes_the_own_claim_and_releases_the_fence(self):
        code, out = self.cut(answer="n")
        self.assertEqual(code, C.EXIT_OK, out)
        self.assertIn("declined, nothing pushed", out)
        self.assertNotIn("claimed meanwhile", out, out)
        self.assertNotIn("FENCE NOT RELEASED", out, out)
        self.assertEqual(self.records()["release-cut-9.9.9"]["state"], "parked")

    def test_a_resumed_decline_skips_the_cut_script_and_releases(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.tmp,
                              capture_output=True, text=True).stdout.strip()
        calls = []
        real_runner = self.runner

        def recording(cmd, **kw):
            calls.append(os.path.basename(cmd[1]) if len(cmd) > 1 else cmd[0])
            return real_runner(cmd, **kw)

        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = C.cut_mode(self.tmp, "9.9.9", self.bm, self.PATHS, None,
                              False, lambda prompt: "n", recording,
                              resume_sha=head)
        out = buf.getvalue()
        self.assertEqual(code, C.EXIT_OK, out)
        self.assertIn("resume from the checked commit %s" % head, out)
        self.assertNotIn("cut_v1.0.0.sh", calls)
        self.assertIn("required_fast.sh", calls)
        self.assertIn("declined, nothing pushed", out)
        self.assertEqual(self.records()["release-cut-9.9.9"]["state"], "parked")

    def test_a_failed_gate_releases_the_fence(self):
        self.stubs["required_fast.sh"] = (1, FAST_RED)
        code, out = self.cut()
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertNotIn("FENCE NOT RELEASED", out, out)
        self.assertEqual(self.records()["release-cut-9.9.9"]["state"], "parked")

    def test_a_foreign_live_claim_still_refuses_the_cut(self):
        self.store("claim", "foreign-lane", "--session", "cli-" + "f" * 32,
                   "--lifetime", "ephemeral", "--objective", "someone else",
                   "--files", "scripts/keep.txt")
        code, out = self.cut()
        self.assertEqual(code, C.EXIT_REFUSED, out)
        recs = self.records()
        self.assertEqual(recs["foreign-lane"]["state"], "active")
        cut_rec = recs.get("release-cut-9.9.9")
        self.assertTrue(cut_rec is None or cut_rec["state"] != "active", out)

    def test_a_foreign_live_claim_is_not_taken_even_when_forced(self):
        self.store("claim", "foreign-lane", "--session", "cli-" + "f" * 32,
                   "--lifetime", "ephemeral", "--objective", "someone else",
                   "--files", "scripts/keep.txt")
        code, out = self.cut(force="founder chose precedence")
        self.assertEqual(code, C.EXIT_REFUSED, out)
        self.assertIn("not judged DEAD", out)
        self.assertEqual(self.records()["foreign-lane"]["state"], "active")


class ResumeFromTheCheckedCommit(unittest.TestCase):
    """2026-09-17: the 1.0.19 cut passed every gate, then its answer window
    expired and it declined with nothing pushed. Re-running would repeat the
    66 minute cut_v1.0.0.sh; --resume <sha> skips only that script, only when
    HEAD is exactly the checked commit, and keeps every gate."""

    SHA = "c56e19c308f74ffd57a13575bfb05a71f41c10c7"

    def test_matching_head_skips_only_the_cut_script(self):
        r = Runner(dict(GREEN, **{"git rev-parse HEAD": (0, self.SHA + "\n")}))
        code, _ = _main(["--yes", "--resume", self.SHA], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertNotIn("cut_v1.0.0.sh", r.calls)
        for gate in ("required_fast.sh", "refresh_cut.py",
                     "release_invariant.py", "export_public.py",
                     "reproduce_export.py", "bm_store complete"):
            self.assertIn(gate, r.calls)
        self.assertLess(r.calls.index("required_fast.sh"),
                        r.calls.index("export_public.py"))

    def test_a_moved_head_refuses_before_any_gate_and_parks(self):
        r = Runner(dict(GREEN, **{"git rev-parse HEAD": (0, "f" * 40 + "\n")}))
        code, _ = _main(["--yes", "--resume", self.SHA], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        for never in ("cut_v1.0.0.sh", "required_fast.sh", "export_public.py"):
            self.assertNotIn(never, r.calls)
        parks = [c for c in r.cmds if _key(c) == "bm_store park"]
        self.assertEqual(len(parks), 1, r.calls)
        self.assertIn("not the checked commit", parks[0][parks[0].index("--note") + 1])

    def test_a_short_sha_or_check_mode_is_no_data(self):
        for argv in (["--resume", "c56e19c30"],
                     ["--check", "--resume", self.SHA]):
            r = Runner(GREEN)
            code, _ = _main(argv, r)
            self.assertEqual(code, C.EXIT_NODATA, argv)
            self.assertNotIn("bm_store claim", r.calls)


class TheHelpPrints(unittest.TestCase):
    def test_help_exits_0_without_a_traceback(self):
        proc = subprocess.run([sys.executable, C.__file__, "--help"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--force-conflicts", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
