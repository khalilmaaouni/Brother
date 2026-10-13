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
import builtins
import hashlib
import io
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

# Item (2026-09-19): --release-notes-file is now scanned before the fence
# is claimed, against a private-terms list. Every test using
# --release-notes-file needs BOTH a real, readable file and a terms list
# that never depends on this machine's actual ~/.brothersbe-private-names
# (that path is the production default and must stay untouched by tests).
_NOTES_TEST_DIR = tempfile.mkdtemp(prefix="cut-test-notes-")
CLEAN_NOTES_FILE = os.path.join(_NOTES_TEST_DIR, "body.md")
with open(CLEAN_NOTES_FILE, "w", encoding="utf-8") as _fh:
    _fh.write("# Release notes\n\nEverything in this file is safe to "
             "publish.\n")
EMPTY_TERMS_FILE = os.path.join(_NOTES_TEST_DIR, "empty-terms.txt")
with open(EMPTY_TERMS_FILE, "w", encoding="utf-8") as _fh:
    _fh.write("# no real terms here; only so a test never reads the real "
             "~/.brothersbe-private-names\n")

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
    if cmd[0] == "gh":
        return "gh " + " ".join(cmd[1:3])
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


#: A fixed 40-hex commit for GREEN's own export_public.py fixture output
#: (opus re-review round 2, 2026-09-19): real cut.py code never invents
#: this value, it only ever parses it back out of a TAGGED: line
#: (_local_tagged_commit), so GREEN carries a real one by default, the
#: same shape export_public.py itself prints on a genuine tagged push.
GREEN_TAGGED_COMMIT = "c" * 40
GREEN_EXPORT_PUBLIC_OUT = (
    "PUSHED: one commit appended to origin release/%s\n"
    "MERGED: https://github.com/khalilmaaouni/Brother/pull/1 into main\n"
    "TAGGED: v%s points at the merged tip (local commit %s) on main\n"
    % (VERSION, VERSION, GREEN_TAGGED_COMMIT))

#: CV1.e: the question names the full HEAD the chain left, so GREEN answers
#: a 40 hex HEAD; a runner without one is refused before the question.
GREEN_HEAD = "e" * 40

GREEN = {
    "next_cut.py": (0, "next cut weekday: Friday\nnext cut version: %s\n"
                       % VERSION),
    "git rev-parse HEAD": (0, GREEN_HEAD + "\n"),
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
    "donecheck_u8.py": (0, "DONE        exactly one plugin, named brother\n"),
    "retire_catalogs.py": (0, "retire_catalogs: already in end state\n"),
    "export_public.py": (0, GREEN_EXPORT_PUBLIC_OUT),
    "reproduce_export.py": (0, "reproduce-export: PASS\n"),
    "gh release create": (0, "https://github.com/khalilmaaouni/Brother/"
                             "releases/tag/v%s\n" % VERSION),
    "bm_store checkpoint": (0, "checkpoint 1 recorded for %s (version 2)\n"
                               % UUID),
}


#: RC1.b1: the export rows build a real export tree, which a fake root
#: cannot, so setUpModule answers them for every scripted chain in this
#: module (twelve call paths reach chain: _main, _main_true_default_terms,
#: seven direct C.main calls and three C.cut_mode calls);
#: TheExportRowsRunOnce drives the real function, kept here.
STUB_EXPORT_ROWS = (True, ["export rows: stubbed"])
REAL_EXPORT_ROWS = C.export_rows


def _main(argv, runner, answer="y", root="/fake/root", terms_file=None,
          bare=False):
    """cut.main with the bm_store loader and the prompt both faked; the
    loader cache is cleared so each test decides what loads. terms_file
    defaults to a real, empty, throwaway list so a --release-notes-file
    test never reads this machine's actual ~/.brothersbe-private-names."""
    asked = []
    # A real cut with no release notes file is refused (2026-09-20). Every
    # test that is about something else opts out the way an operator
    # would; `bare=True` leaves argv alone for the tests about that rule.
    if not bare and "--check" not in argv \
            and "--release-notes-file" not in argv \
            and "--no-release-page" not in argv:
        argv = list(argv) + ["--no-release-page", "a test about something else"]

    def ask(prompt):
        asked.append(prompt)
        return answer

    del C._BM_STORE_CACHE[:]
    with mock.patch.object(C, "_load_bm_store",
                           return_value=(FakeBmStore, BM_PATH, None)), \
         mock.patch.object(C, "release_paths",
                           return_value=["scripts/", "bundle/"]):
        code = C.main(argv, root=root, runner=runner, ask=ask,
                      terms_file=terms_file or EMPTY_TERMS_FILE)
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

    def test_catalogs_not_in_the_one_plugin_end_state_refuse_the_cut(self):
        """F10 (architecture review 2026-09-30): U8 retires the brothermode,
        brothersbe and brotherds marketplace entries AT the 1.1.0 cut, but
        no cut step read the catalogs, so a cut could ship four plugins
        while the plan said one. The chain now runs scripts/donecheck_u8.py
        on the rehearsed tree, after release_invariant and before anything
        irreversible: a red U8 reads NOT READY under --check and refuses a
        real cut before export_public.py."""
        red = {"donecheck_u8.py": (1, "NOT DONE    3 entr(y/ies) still to "
                                       "retire: brothermode, brothersbe, "
                                       "brotherds\n")}
        r = Runner(dict(GREEN, **red))
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_REFUSED, r.calls)
        self.assertIn("donecheck_u8.py", r.calls)
        self.assertLess(r.calls.index("release_invariant.py"),
                        r.calls.index("donecheck_u8.py"))
        u8 = [c for c in r.cmds if _key(c) == "donecheck_u8.py"][0]
        rehearsal = [c for c in r.cmds if c[:3] == ["git", "worktree", "add"]][0][4]
        # H1: the step hands the check the rehearsed TREE, so the check
        # enumerates every catalog in it; a single catalog path (any of the
        # five) would let the others ship unread.
        self.assertEqual(u8[-1], rehearsal, u8)
        r = Runner(dict(GREEN, **red))
        code, _ = _main([], r)
        self.assertEqual(code, C.EXIT_REFUSED, r.calls)
        self.assertIn("donecheck_u8.py", r.calls)
        self.assertNotIn("export_public.py", r.calls)
        r = Runner(GREEN)
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertIn("donecheck_u8.py", r.calls)

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

    def test_an_expired_declared_exception_reads_not_ready_in_check(self):
        """2026-09-20, the 1.0.21 cut: two exceptions expired the day before
        and the refusal arrived 41 minutes in. The audit is read first."""
        r = Runner(dict(GREEN, **{"battery_exception_audit.py":
                                  (1, "EXPIRED release-note-perturb\n")}))
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertLess(r.calls.index("battery_exception_audit.py"),
                        r.calls.index("cut_v1.0.0.sh"))

    def test_an_expired_exception_stops_a_real_cut_before_any_long_step(self):
        r = Runner(dict(GREEN, **{"battery_exception_audit.py":
                                  (1, "EXPIRED release-note-perturb\n")}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        for never in ("cut_v1.0.0.sh", "required_fast.sh",
                      "export_public.py"):
            self.assertNotIn(never, r.calls)

    def test_an_unreadable_exceptions_file_blocks_like_an_expired_one(self):
        for code_in in (2, 127):
            r = Runner(dict(GREEN, **{"battery_exception_audit.py":
                                      (code_in, "NO-DATA\n")}))
            code, _ = _main(["--yes"], r)
            self.assertEqual(code, C.EXIT_REFUSED, code_in)
            self.assertNotIn("cut_v1.0.0.sh", r.calls)

    def test_a_real_cut_with_no_release_notes_file_is_refused_at_second_one(self):
        """2026-09-20, the 1.0.21 cut: tagged publicly, no Release page, the
        previous version still shown as latest. Nothing is claimed, run or
        asked before the refusal."""
        r = Runner(GREEN)
        code, asked = _main(["--yes"], r, bare=True)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertEqual(r.calls, [], "something ran before the refusal")
        self.assertEqual(asked, [])

    def test_an_empty_reason_is_not_a_reason(self):
        r = Runner(GREEN)
        code, _ = _main(["--yes", "--no-release-page", "  "], r, bare=True)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertEqual(r.calls, [])

    def test_a_stated_reason_lets_the_cut_proceed_and_check_never_needs_one(self):
        r = Runner(GREEN)
        code, _ = _main(["--yes", "--no-release-page", "tag only, page follows by hand"],
                        r, bare=True)
        self.assertEqual(code, C.EXIT_OK)
        self.assertIn("cut_v1.0.0.sh", r.calls)
        code, _ = _main(["--check"], Runner(GREEN), bare=True)
        self.assertEqual(code, C.EXIT_OK)

    def test_a_refusing_preflight_stops_a_real_cut_before_any_long_step(self):
        """2026-09-20: six refusals of the 1.0.21 cut, each behind the 84
        minute chain or on the public runner. The preflight is asked first,
        and NO-DATA (2) or a missing script (127) blocks like a refusal."""
        for code_in in (1, 2, 127):
            r = Runner(dict(GREEN, **{"cut_preflight.py":
                                      (code_in, "cut-preflight: REFUSED\n")}))
            code, _ = _main(["--yes"], r)
            self.assertEqual(code, C.EXIT_REFUSED, code_in)
            for never in ("cut_v1.0.0.sh", "required_fast.sh",
                          "export_public.py"):
                self.assertNotIn(never, r.calls)

    def test_the_preflight_is_told_the_version_and_runs_in_check_too(self):
        r = Runner(dict(GREEN, **{"cut_preflight.py":
                                  (1, "cut-preflight: REFUSED\n")}))
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        # FC1.a: a refused preflight ends --check before the chain
        self.assertNotIn("cut_v1.0.0.sh", r.calls)
        cmd = r.cmds[r.calls.index("cut_preflight.py")]
        self.assertEqual(cmd[cmd.index("--version") + 1], VERSION)
        self.assertNotIn("--no-virgin-gate", cmd)

    def test_a_changed_plugin_with_no_bump_stops_the_cut_after_the_bump(self):
        """2026-09-20: 1.0.21 shipped 24 and 12 changed product files under
        unchanged plugin versions and the updater said "already at the
        latest". The gate reads the BUMPED tree, before the minutes long gate
        and before anything is pushed; NO-DATA (2) and a missing script (127)
        block like a refusal."""
        for code_in in (1, 2, 127):
            r = Runner(dict(GREEN, **{"plugin_bump_gate.py":
                                      (code_in, "REFUSED  plugin-bumps\n")}))
            code, _ = _main(["--yes"], r)
            self.assertEqual(code, C.EXIT_REFUSED, code_in)
            self.assertLess(r.calls.index("cut_v1.0.0.sh"),
                            r.calls.index("plugin_bump_gate.py"))
            for never in ("required_fast.sh", "export_public.py"):
                self.assertNotIn(never, r.calls)
            cmd = r.cmds[r.calls.index("plugin_bump_gate.py")]
            self.assertEqual(cmd[cmd.index("--version") + 1], VERSION)

    def test_the_plugin_bump_gate_is_rehearsed_by_check_in_the_rehearsal_tree(self):
        r = Runner(dict(GREEN, **{"plugin_bump_gate.py":
                                  (1, "REFUSED  plugin-bumps\n")}))
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        i = r.calls.index("plugin_bump_gate.py")
        self.assertEqual(r.cwds[i], r.cwds[r.calls.index("cut_v1.0.0.sh")])
        self.assertTrue(r.cmds[i][1].startswith(r.cwds[i]))

    def test_a_cheaper_refusal_spares_the_minutes_long_preflight(self):
        r = Runner(dict(GREEN, **{"git status --porcelain":
                                  (0, " M README.md\n")}))
        _main(["--yes"], r)
        self.assertNotIn("cut_preflight.py", r.calls)

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


class ParkConflictsRefusalNamesTheStateAndTheCommand(unittest.TestCase):
    """Review item (2026-09-19): a "not judged DEAD" refusal used to leave
    a human reconstructing both bm_stall's own reasoning and the adopt
    command by hand. Direct, isolated call: no CLI, no fence, no store."""

    def test_the_refusal_names_the_adopt_command_and_bm_stalls_own_reading(self):
        conflicts = [{"name": "lane-live", "lifecycle_uuid": LIVE_B,
                     "version": 4}]
        ok, lines = C.park_conflicts(
            "/fake/root", BM_PATH, conflicts, VERSION,
            "founder chose precedence", runner=Runner(GREEN), dead={},
            session="cli-abc")
        self.assertFalse(ok)
        text = "\n".join(lines)
        self.assertIn("not judged DEAD", text)
        self.assertIn("no dead-owner finding", text)
        # The exact command a human would run, reconstructed nowhere else.
        self.assertIn(sys.executable, text)
        self.assertIn(BM_PATH, text)
        self.assertIn("adopt", text)
        self.assertIn(LIVE_B, text)
        self.assertIn("--version 4", text)
        self.assertIn("--adopt-from-live-session", text)
        self.assertIn("--session cli-abc", text)

    def test_a_dead_owner_with_a_bm_stall_message_still_proceeds(self):
        # dead carries the finding's own text now (a dict, not a bare
        # set); membership alone still gates adopt/park.
        conflicts = [{"name": "lane-live", "lifecycle_uuid": LIVE_B,
                     "version": 4}]
        ok, lines = C.park_conflicts(
            "/fake/root", BM_PATH, conflicts, VERSION,
            "founder chose precedence", runner=Runner(GREEN),
            dead={LIVE_B: "stale fence: heartbeat is 99999s old"})
        self.assertTrue(ok, lines)


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
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, _ = _main(["--yes", "--force-conflicts", "lane is dead"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        for never in ("bm_store adopt", "bm_store park", "bm_store claim"):
            self.assertNotIn(never, r.calls)
        # Review item (2026-09-19): a stuck fence prints bm_stall's own
        # reading of the owner's state and the exact adopt command, so a
        # human never reconstructs either by hand.
        text = out.getvalue()
        self.assertIn("no dead-owner finding", text)
        self.assertIn("--adopt-from-live-session", text)
        self.assertIn("bm_store", text)
        self.assertIn("adopt", text)
        self.assertIn(LIVE_B, text)

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


def _expected_final_version(r):
    """R3: every successful 'bm_store checkpoint' (a heartbeat beat) bumps
    the fence's version by exactly 1 (cmd_checkpoint's own contract), and
    release_fence must release against that CURRENT version, never the
    claim's own stale one (CLAIM_OK's fixed 'version 1'). The default
    Runner answers an unmatched command (0, ""), so every beat these
    GREEN-based tests make succeeds; counting them is exact, not a guess."""
    return str(1 + r.calls.count("bm_store checkpoint"))


class TheFenceIsReleasedOnEveryExit(unittest.TestCase):
    def _park_note(self, r):
        parks = [c for c in r.cmds if _key(c) == "bm_store park"]
        self.assertEqual(len(parks), 1, r.calls)
        self.assertIn("--version", parks[0])
        self.assertEqual(parks[0][parks[0].index("--version") + 1],
                         _expected_final_version(r))
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
        self.assertEqual(len(asked), 1, asked)
        # Item 3: the prompt names the release and its body file, not just
        # "push tag" (no --release-notes-file here, so it says none will
        # be published).
        self.assertIn("push tag v%s" % VERSION, asked[0])
        self.assertIn("no GitHub Release will be published", asked[0])
        self.assertNotIn("export_public.py", r.calls)
        self.assertNotIn("gh release create", r.calls)
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
            code = C.main(["--yes", "--no-release-page", "a test about something else"], root="/fake", runner=r,
                          ask=None)
        del C._BM_STORE_CACHE[:]
        self.assertEqual(code, C.EXIT_NODATA)
        self.assertNotIn("required_fast.sh", r.calls)


class TheGreenPathRunsTheChainInOrderAndCompletes(unittest.TestCase):
    def test_order_claim_gates_cut_push_reproduce_complete(self):
        r = Runner(GREEN)
        code, asked = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertEqual(asked, [])
        # CV1.e: the second git status is pre_push_recheck, after the
        # answer and immediately before the push.
        wanted = ["bm_store claim", "bm_store dump", "git status --porcelain",
                  "cut_v1.0.0.sh", "required_fast.sh", "refresh_cut.py",
                  "release_invariant.py", "git status --porcelain",
                  "export_public.py",
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
        # RC1.b2: the covering rehearsal record rides along, for the
        # exporter to check again against the tree it builds; FP1.b: so
        # does the Linux canary record the question was asked on
        self.assertEqual(push[2:], ["--push", "--tag", "v%s" % VERSION,
                                    "--prove-required-fast",
                                    "--rehearsal-record", FAKE_REHEARSAL,
                                    "--canary-record", "/fixture/canary.json"])
        self.assertNotIn("--require-signed", push)

    def test_the_locally_tagged_commit_is_threaded_into_reproduce_export(self):
        # B1/B2 (opus review, 2026-09-19): a second, independently-typed
        # remote read (git ls-remote) right after the push cannot tell a
        # healthy annotated tag from a broken one -- for an annotated tag,
        # `git ls-remote --tags remote refs/tags/<tag>` (the EXACT refspec
        # the earlier code used) returns only the TAG OBJECT's own sha,
        # never the peeled commit, so every real cut compared the wrong
        # value and refused a perfectly healthy tag. Fixed at the source:
        # the ground truth is the commit export_public.py itself resolved
        # --tag to LOCALLY, right after creating it, which it now names in
        # its own TAGGED: line (GREEN's own default fixture, see
        # GREEN_EXPORT_PUBLIC_OUT); cut.py reads that back out of the push
        # step's full captured output and hands it to reproduce_export.py
        # as --expect-commit. No ls-remote call happens here at all.
        r = Runner(GREEN)
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertNotIn("git ls-remote --tags", r.calls,
                         "cut.py must never ask the remote a second, "
                         "independent time for the commit it just pushed")
        repro = [c for c in r.cmds if _key(c) == "reproduce_export.py"][0]
        self.assertIn("--expect-commit", repro)
        self.assertEqual(repro[repro.index("--expect-commit") + 1],
                         GREEN_TAGGED_COMMIT)

    def test_complete_carries_the_version_and_names_what_passed(self):
        r = Runner(GREEN)
        _main(["--yes"], r)
        complete = [c for c in r.cmds if _key(c) == "bm_store complete"][0]
        self.assertEqual(complete[3], UUID)
        self.assertEqual(complete[complete.index("--version") + 1],
                         _expected_final_version(r))
        evidence = complete[complete.index("--evidence") + 1]
        for name in ("required_fast", "cut_v1.0.0.sh", "export_public",
                     "reproduce_export"):
            self.assertIn(name, evidence)


class LocalTaggedCommitTakesTheVersionsOwnLine(unittest.TestCase):
    """2026-09-19: `_local_tagged_commit` used to `.search()` for the
    FIRST TAGGED: line in export_public.py's whole captured output, for
    ANY tag -- a leftover or debug TAGGED: line for a different version
    printed earlier in that output would have been read as THIS cut's
    own tagged commit. Now it matches only lines tagging v<VERSION>, and
    refuses (returns None, the same "unknown" run_chain already treats
    as a refusal) unless there is EXACTLY ONE such line."""

    def _line(self, version, sha):
        return ("TAGGED: v%s points at the merged tip (local commit %s) "
                "on main\n" % (version, sha))

    def test_a_debug_line_for_another_tag_before_the_real_one_is_ignored(self):
        other_sha = "d" * 40
        real_sha = "c" * 40
        text = self._line("9.9.8", other_sha) + self._line(VERSION, real_sha)
        self.assertEqual(C._local_tagged_commit(text, VERSION), real_sha)

    def test_two_lines_for_the_same_version_with_different_shas_is_none(self):
        text = self._line(VERSION, "c" * 40) + self._line(VERSION, "e" * 40)
        self.assertIsNone(C._local_tagged_commit(text, VERSION))

    def test_two_lines_for_the_same_version_with_the_same_sha_is_still_none(self):
        # Chosen deliberately (see _local_tagged_commit's own docstring):
        # a duplicate TAGGED: line for this run's own version is never a
        # shape export_public.py is meant to print, sha-equal or not, so
        # this refuses rather than trusting the repeated value.
        sha = "c" * 40
        text = self._line(VERSION, sha) + self._line(VERSION, sha)
        self.assertIsNone(C._local_tagged_commit(text, VERSION))

    def test_no_line_for_this_version_is_none(self):
        text = self._line("9.9.8", "d" * 40)
        self.assertIsNone(C._local_tagged_commit(text, VERSION))

    def test_the_normal_single_line_still_works(self):
        sha = "c" * 40
        text = self._line(VERSION, sha)
        self.assertEqual(C._local_tagged_commit(text, VERSION), sha)

    def test_a_duplicate_tagged_line_makes_the_cut_refuse(self):
        # Integration proof, not just the unit-level None above: routed
        # through export_public_push and run_chain the same way a real
        # duplicate would be, the cut refuses and never proceeds to
        # reproduce_export or gh release create.
        dup_out = (self._line(VERSION, "c" * 40)
                  + self._line(VERSION, "e" * 40))
        answers = dict(GREEN, **{"export_public.py": (0, dup_out)})
        r = Runner(answers)
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED, r.calls)
        self.assertNotIn("reproduce_export.py", r.calls)
        self.assertNotIn("gh release create", r.calls)
        self.assertNotIn("bm_store complete", r.calls)


class ExportPublicTaggingNothingIsRefused(unittest.TestCase):
    """m1 (opus re-review round 2, 2026-09-19): export_public.py can exit
    0 under --tag while its own output carries no TAGGED: line at all --
    its own "nothing to push" branch, reachable on a re-run after an
    earlier tag push was rejected (the export content already landed on
    that earlier run, so this run finds nothing new to append and never
    reaches tag creation at all). Before this fix cut.py still recorded
    the push as passed and, once reproduce_export's fetch found no
    matching tag, printed "TAG vX IS ALREADY PUBLIC" -- false, since this
    run tagged nothing. Now: no TAGGED line BLOCKS, whether or not some
    OTHER tag happens to already be on the remote (made by an earlier,
    unrelated run, or anyone else) -- that tag was never made by THIS
    run and is never treated as if it were."""

    def _run_no_tagged_line(self, ls_remote_answer):
        answers = dict(GREEN, **{
            "export_public.py": (0, "nothing to push: the export tree "
                                    "already matches main's current tip\n"),
            "git ls-remote --tags": ls_remote_answer,
        })
        r = Runner(answers)
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, _ = _main(["--yes"], r)
        return code, out.getvalue(), r

    def _park_note(self, r):
        parks = [c for c in r.cmds if _key(c) == "bm_store park"]
        self.assertEqual(len(parks), 1, r.calls)
        return parks[0][parks[0].index("--note") + 1]

    def test_no_remote_tag_refuses_and_names_it(self):
        # sub-case: no tag at all sits on the remote (the GREEN default
        # for an unregistered ls-remote label: exit 0, empty stdout).
        code, out, r = self._run_no_tagged_line((0, ""))
        self.assertEqual(code, C.EXIT_REFUSED, r.calls)
        self.assertNotIn("reproduce_export.py", r.calls,
                         "no tag was made this run; nothing to reproduce")
        self.assertNotIn("gh release create", r.calls,
                         "no tag was made this run; nothing to publish")
        self.assertNotIn("bm_store complete", r.calls)
        self.assertNotIn("TAG v%s IS ALREADY PUBLIC" % VERSION, out,
                         "never claim public when this run tagged nothing")
        note = self._park_note(r)
        self.assertIn("no TAGGED: line", note)
        self.assertIn("does not exist on", note)

    def test_a_remote_tag_nobody_in_this_run_made_still_refuses(self):
        # sub-case: a tag genuinely sits on the remote already (an earlier
        # run, or any other actor) -- this run still made no tag of its
        # own, so it must still refuse rather than adopt that tag as its
        # own and publish a Release over content it never verified.
        foreign_sha = "f" * 40
        code, out, r = self._run_no_tagged_line(
            (0, "%s\trefs/tags/v%s\n" % (foreign_sha, VERSION)))
        self.assertEqual(code, C.EXIT_REFUSED, r.calls)
        self.assertNotIn("reproduce_export.py", r.calls)
        self.assertNotIn("gh release create", r.calls)
        self.assertNotIn("bm_store complete", r.calls)
        self.assertNotIn("TAG v%s IS ALREADY PUBLIC" % VERSION, out,
                         "a foreign tag must never be claimed as this "
                         "run's own publication")
        note = self._park_note(r)
        self.assertIn("no TAGGED: line", note)
        self.assertIn("does exist on", note)

    def test_an_unreadable_remote_still_refuses_and_says_so(self):
        # The read-only diagnostic itself can fail (network, timeout);
        # that must never turn a refusal into a guessed pass, and the
        # refusal still names that the check could not be confirmed.
        code, out, r = self._run_no_tagged_line((1, "network unreachable\n"))
        self.assertEqual(code, C.EXIT_REFUSED, r.calls)
        self.assertNotIn("reproduce_export.py", r.calls)
        self.assertNotIn("gh release create", r.calls)
        self.assertNotIn("TAG v%s IS ALREADY PUBLIC" % VERSION, out)
        self.assertIn("could not confirm", self._park_note(r))

    def test_the_refusal_names_the_peel_command_and_the_gh_release_command(self):
        # An operator reading this refusal needs a read-only path back to
        # confirming what actually landed, not just a REFUSED exit code:
        # the exact command to peel the remote tag (if any) to a commit,
        # and the exact gh release create command this cut would have
        # run, built by gh_release_command so it can never drift from the
        # real one (see GhReleaseCommandConstruction).
        code, out, r = self._run_no_tagged_line((0, ""))
        self.assertEqual(code, C.EXIT_REFUSED, r.calls)
        note = self._park_note(r)
        self.assertIn("git ls-remote %s refs/tags/v%s^{}"
                      % (C.EP.DEFAULT_REMOTE, VERSION), note)
        self.assertIn(" ".join(C.gh_release_command(VERSION)), note)


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
            code = C.main(["--no-release-page", "a test about something else"], root="/fake/root", runner=r,
                          ask=no_keyboard)
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
        # CV1.e (owner ruling 2026-10-03, decision 5): a real cut takes no
        # --answer-file, so this argv no longer names one; the decline path
        # stays covered through the scripted file_asker based `ask`, and
        # the refusal of the same argv WITH --answer-file is proven below.
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
            code = C.main(["--no-release-page", "a test about something else"],
                          root="/fake/root",
                          runner=r, ask=founder_says_no)
        self.assertEqual(code, C.EXIT_OK)
        self.assertNotIn("export_public.py", r.calls)
        self.assertIn("bm_store park", r.calls)

    def test_the_same_real_cut_with_an_answer_file_is_refused(self):
        path = self._tmp()
        r = Runner(GREEN)
        asked = []
        del C._BM_STORE_CACHE[:]
        with mock.patch.object(C, "_load_bm_store",
                               return_value=(FakeBmStore, BM_PATH, None)), \
             mock.patch.object(C, "release_paths",
                               return_value=["scripts/", "bundle/"]):
            code = C.main(["--answer-file", path, "--no-release-page", "a test about something else"],
                          root="/fake/root",
                          runner=r, ask=asked.append)
        self.assertEqual(code, C.EXIT_NODATA)
        self.assertEqual(r.calls, [])
        self.assertEqual(asked, [])
        self.assertFalse(os.path.exists(path))


class TheTestsNeverWriteTheRealEvidenceFolder(unittest.TestCase):
    """CV1.e: a refused push writes a stop record that blocks every later
    real cut; setUpModule points the evidence folder at a throwaway one, and
    this proves it, so no scripted push failure here reaches the real one."""

    def test_the_evidence_folder_is_the_module_s_throwaway_one(self):
        real = os.path.join(os.path.expanduser("~"), ".claude", "evidence",
                            "cut")
        self.assertNotEqual(os.path.abspath(C.CR.evidence_dir()),
                            os.path.abspath(real))
        self.assertTrue(C.CR.evidence_dir().startswith(EVIDENCE_TMP),
                        C.CR.evidence_dir())

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

        def spy(label, cmd, root, runner=None, emit=None, clock=None,
               heartbeat=None):
            labels.append(label)
            return real(label, cmd, root, runner, emit=lambda l: None,
                       heartbeat=heartbeat)

        r = Runner(GREEN)
        with mock.patch.object(C, "_stream", spy):
            # --release-notes-file: the gh step (item 3) only runs with a
            # real body file named, and this guard is specifically about
            # every step reaching _stream, gh included.
            code, _ = _main(["--yes", "--release-notes-file", CLEAN_NOTES_FILE], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        for want in ("cut_v1.0.0.sh", "required_fast.sh",
                     "refresh_cut --check", "release_invariant",
                     "export_public --push", "gh release create",
                     "reproduce_export"):
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
                 "release_invariant.py", "donecheck_u8.py",
                 "retire_catalogs.py", "export_public.py", "reproduce_export.py",
                 "battery_exception_audit.py", "cut_preflight.py",
                 "plugin_bump_gate.py")
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
            "donecheck_u8.py": (0, "DONE        exactly one plugin, named brother\n"),
            "retire_catalogs.py": (0, "retire_catalogs: already in end state\n"),
            "export_public.py": (0, "pushed\n"),
            "reproduce_export.py": (0, "reproduce-export: PASS\n"),
            "battery_exception_audit.py": (0, "OK: 0 exception(s)\n"),
            "cut_preflight.py": (0, "cut-preflight: CLEAR\n"),
            "plugin_bump_gate.py": (0, "OK       plugin-bumps\n"),
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


class ClaimFenceCarriesOwnerEvidence(unittest.TestCase):
    """R3: bm_stall.py's parse_owner_tag (products/brothermode/tools/
    bm_stall.py) reads this back out of the fence's own objective."""

    def test_the_objective_carries_this_processs_pid_and_host(self):
        r = Runner(GREEN)
        C.claim_fence("/fake/root", BM_PATH, VERSION, ["scripts/"], r,
                      session="cli-x")
        claim = [c for c in r.cmds if _key(c) == "bm_store claim"][0]
        objective = claim[claim.index("--objective") + 1]
        self.assertIn("[owner pid=%d host=%s]"
                      % (os.getpid(), C._this_host()), objective)

    def test_owner_tag_matches_the_shape_bm_stall_parses(self):
        # Copied from bm_stall.py's OWNER_TAG_RE by hand, deliberately: two
        # independently-typed regexes agreeing is stronger evidence that
        # neither side silently drifted than importing one to check itself.
        self.assertRegex(C.owner_tag(),
                         r"^\[owner pid=(\d+) host=([^\s\]]+)\]$")


class HeartbeatRefreshesTheFence(unittest.TestCase):
    """R3: cut.py's own Heartbeat, the thing that refreshes records.
    updated_at (bm_stall.py's heartbeat evidence) while the chain runs."""

    def _hb(self, r, version=1, interval_s=60, start=1000.0):
        clock = [start]
        hb = C.Heartbeat("/fake/root", BM_PATH, UUID, version, runner=r,
                         interval_s=interval_s, clock=lambda: clock[0])
        return hb, clock

    def test_the_first_call_always_beats(self):
        r = Runner(GREEN)
        hb, _clock = self._hb(r)
        hb.maybe("step-a")
        self.assertIn("bm_store checkpoint", r.calls)
        self.assertEqual(hb.version, 2)

    def test_a_second_call_within_the_interval_does_not_beat_again(self):
        r = Runner(GREEN)
        hb, clock = self._hb(r)
        hb.maybe("step-a")
        before = len(r.calls)
        clock[0] += 10  # well under the 60s interval
        hb.maybe("step-a")
        self.assertEqual(len(r.calls), before, r.calls)
        self.assertEqual(hb.version, 2)

    def test_force_always_beats_regardless_of_the_interval(self):
        r = Runner(GREEN)
        hb, _clock = self._hb(r)
        hb.maybe("step-a", force=True)
        hb.maybe("step-a", force=True)
        self.assertEqual(r.calls.count("bm_store checkpoint"), 2)
        self.assertEqual(hb.version, 3)

    def test_past_the_interval_it_beats_again(self):
        r = Runner(GREEN)
        hb, clock = self._hb(r)
        hb.maybe("step-a")
        clock[0] += 61
        hb.maybe("step-a")
        self.assertEqual(r.calls.count("bm_store checkpoint"), 2)
        self.assertEqual(hb.version, 3)

    def test_the_checkpoint_command_names_the_uuid_version_and_step(self):
        r = Runner(GREEN)
        hb, _clock = self._hb(r, version=5)
        hb.maybe("required_fast.sh", force=True)
        cmd = r.cmds[-1]
        self.assertEqual(cmd[2], "checkpoint")
        self.assertEqual(cmd[3], UUID)
        self.assertEqual(cmd[cmd.index("--version") + 1], "5")
        self.assertIn("required_fast.sh", cmd[cmd.index("--next") + 1])
        self.assertNotIn("--session", cmd)  # cmd_checkpoint takes none

    def test_a_failed_beat_is_reported_and_leaves_the_version_unchanged(self):
        import contextlib
        import io
        r = Runner({"bm_store checkpoint":
                    (1, "checkpoint refused: stale identity\n")})
        hb, _clock = self._hb(r)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hb.maybe("step-a")
        self.assertEqual(hb.version, 1)
        self.assertIn("heartbeat NOT recorded", out.getvalue())

    def test_a_failed_beat_never_raises(self):
        r = Runner({"bm_store checkpoint": (127, "no such file\n")})
        hb, _clock = self._hb(r)
        hb.maybe("step-a")  # must not raise
        self.assertEqual(hb.version, 1)


class HeartbeatFiresDuringARealCut(unittest.TestCase):
    def test_checkpoint_calls_interleave_with_every_chain_step(self):
        r = Runner(GREEN)
        code, _ = _main(["--yes", "--release-notes-file", CLEAN_NOTES_FILE], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        # 7 real steps, start+end forced each (14), plus item 4's one
        # forced beat immediately before the push: at least 15 beats.
        self.assertGreaterEqual(r.calls.count("bm_store checkpoint"), 15,
                                r.calls)
        for label in ("cut_v1.0.0.sh", "required_fast.sh", "refresh_cut.py",
                      "release_invariant.py", "export_public.py",
                      "gh release create", "reproduce_export.py"):
            self.assertIn(label, r.calls)

    def test_release_uses_the_current_version_not_the_stale_claim_version(self):
        r = Runner(GREEN)
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        complete = [c for c in r.cmds if _key(c) == "bm_store complete"][0]
        got = complete[complete.index("--version") + 1]
        self.assertEqual(got, _expected_final_version(r))
        self.assertNotEqual(
            got, "1",
            "release must not use the claim's own stale version once "
            "heartbeats have advanced it")

    def test_check_mode_never_beats_a_fence_it_never_claims(self):
        r = Runner(GREEN)
        code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertNotIn("bm_store checkpoint", r.calls)
        self.assertNotIn("bm_store claim", r.calls)


class PrePushHeartbeatGuardsTheApprovePromptWait(unittest.TestCase):
    """Item 4: nothing heartbeats while `ask` blocks at the approve
    prompt, so a fence that was LIVE when readiness() checked it can go
    stale, be adopted and parked by another --force-conflicts pass, all
    before the founder's "y" is even read. A forced, blocking beat
    immediately before the push catches that and refuses rather than
    pushing on a claim that may no longer be held."""

    def test_a_failed_pre_push_heartbeat_refuses_the_push(self):
        # Every checkpoint call fails: the same shape a real store gives
        # when this fence's version has already moved (someone else's
        # write landed first -- e.g. an adoption while this cut waited).
        r = Runner(dict(GREEN, **{
            "bm_store checkpoint": (1, "checkpoint refused: stale "
                                       "identity\n")}))
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED, r.calls)
        self.assertNotIn("export_public.py", r.calls,
                         "the push must never run once the pre-push "
                         "heartbeat has failed")
        self.assertNotIn("gh release create", r.calls)
        parks = [c for c in r.cmds if _key(c) == "bm_store park"]
        self.assertEqual(len(parks), 1, r.calls)
        note = parks[0][parks[0].index("--note") + 1]
        self.assertIn("heartbeat failed", note)

    def test_a_healthy_pre_push_heartbeat_lets_the_push_proceed(self):
        r = Runner(GREEN)
        code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertIn("export_public.py", r.calls)

    def test_the_pre_push_beat_runs_after_the_answer_never_before(self):
        r = Runner(GREEN)
        code, asked = _main([], r, answer="y")
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertEqual(len(asked), 1, asked)
        # The pre-push beat is forced (always fires); confirm it landed
        # strictly between the readiness/chain beats and the push itself
        # by checking a checkpoint call is the last thing before
        # export_public.py.
        push_i = r.calls.index("export_public.py")
        self.assertEqual(r.calls[push_i - 1], "bm_store checkpoint",
                         r.calls)


class GhRepoSlug(unittest.TestCase):
    """R4: gh release create --repo reads [HOST/]OWNER/REPO
    (gh release create --help, gh 2.96.0, INHERITED FLAGS)."""

    def test_https_url(self):
        self.assertEqual(
            C.gh_repo_slug("https://github.com/khalilmaaouni/Brother"),
            "khalilmaaouni/Brother")

    def test_https_url_with_dot_git_suffix(self):
        self.assertEqual(
            C.gh_repo_slug("https://github.com/khalilmaaouni/Brother.git"),
            "khalilmaaouni/Brother")

    def test_ssh_url(self):
        self.assertEqual(
            C.gh_repo_slug("git@github.com:khalilmaaouni/Brother.git"),
            "khalilmaaouni/Brother")


class GhReleaseCommandConstruction(unittest.TestCase):
    """R4: the exact shape recorded in docs/plan/CUT-RUNBOOK-1.0.13.md and
    the v1.0.1 evidence in docs/plan/READINESS-ROADMAP-2026-08-29.json:
    gh release create v1.0.13 --repo khalilmaaouni/Brother --title
    "Brother 1.0.13" --notes-file docs/releases/1.0.13.md --verify-tag."""

    def test_the_default_command_matches_the_documented_shape(self):
        cmd = C.gh_release_command(VERSION)
        self.assertEqual(cmd, [
            "gh", "release", "create", "v%s" % VERSION,
            "--repo", "khalilmaaouni/Brother",
            "--title", "Brother %s" % VERSION,
            "--notes-file", os.path.join("docs", "releases",
                                         "%s.md" % VERSION),
            "--verify-tag",
        ])

    def test_an_explicit_notes_file_overrides_the_default(self):
        cmd = C.gh_release_command(VERSION, notes_file="/tmp/custom-body.md")
        self.assertIn("/tmp/custom-body.md", cmd)
        self.assertNotIn(os.path.join("docs", "releases",
                                      "%s.md" % VERSION), cmd)

    def test_the_check_mode_preview_is_built_from_the_same_function(self):
        # No independently-typed second spelling: check_mode's printed
        # preview and create_gh_release's real argv both come from
        # gh_release_command, so they can never drift apart.
        r = Runner(GREEN)
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _main(["--check"], r)
        self.assertIn(" ".join(C.gh_release_command(VERSION)), out.getvalue())


class GhReleaseRunsOnlyAfterASuccessfulPush(unittest.TestCase):
    """R4: never before the push answer, never in --check, run only when
    the push succeeded, and a failure is reported, never retried."""

    def test_a_green_cut_with_a_notes_file_creates_the_gh_release_after_the_push(self):
        r = Runner(GREEN)
        code, _ = _main(["--yes", "--release-notes-file", CLEAN_NOTES_FILE], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertIn("gh release create", r.calls)
        self.assertLess(r.calls.index("export_public.py"),
                        r.calls.index("gh release create"))
        gh = [c for c in r.cmds if _key(c) == "gh release create"][0]
        self.assertEqual(gh, C.gh_release_command(VERSION,
                                                   notes_file=CLEAN_NOTES_FILE))

    def test_reproduce_export_runs_before_gh_release_create(self):
        # Order (opus review, 2026-09-19): reproduce_export.py must run
        # BEFORE gh release create, so a tag that fails reproduction never
        # reaches a public GitHub Release.
        r = Runner(GREEN)
        code, _ = _main(["--yes", "--release-notes-file", CLEAN_NOTES_FILE], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertLess(r.calls.index("reproduce_export.py"),
                        r.calls.index("gh release create"), r.calls)

    def test_a_failed_reproduction_never_reaches_gh_release_even_with_a_notes_file(self):
        # The red proof for the order fix: under the OLD order (gh release
        # create ran right after the push, before reproduce_export.py),
        # this test fails, because gh release create ran unconditionally
        # whenever a notes file was given, whatever reproduce_export.py
        # went on to find.
        r = Runner(dict(GREEN, **{"reproduce_export.py": (1, "FAIL\n")}))
        code, _ = _main(["--yes", "--release-notes-file", CLEAN_NOTES_FILE], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("gh release create", r.calls,
                         "a tag that failed reproduction must never "
                         "receive a public GitHub Release")

    def test_a_green_cut_with_no_notes_file_never_runs_gh_at_all(self):
        # Item 3: docs/releases/<v>.md is the generated note, not
        # necessarily the body actually reviewed and published (the
        # 1.0.20 Release body was a separate Codex draft); without an
        # explicit --release-notes-file the gh step never runs, full
        # stop, even though the push itself succeeds.
        r = Runner(GREEN)
        out = self._run_and_capture(["--yes"], r)
        self.assertEqual(self._code, C.EXIT_OK, r.calls)
        self.assertNotIn("gh release create", r.calls)
        self.assertIn("GitHub Release NOT published for v%s" % VERSION, out)
        self.assertIn("no --release-notes-file was given", out)

    def _run_and_capture(self, argv, r, **kw):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self._code, _ = _main(argv, r, **kw)
        return out.getvalue()

    def test_a_failed_push_never_reaches_gh_release(self):
        r = Runner(dict(GREEN, **{"export_public.py": (1, "REFUSED\n")}))
        code, _ = _main(["--yes", "--release-notes-file", CLEAN_NOTES_FILE], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("gh release create", r.calls)

    def test_a_decline_at_the_prompt_never_reaches_gh_release(self):
        r = Runner(GREEN)
        code, _ = _main(["--release-notes-file", CLEAN_NOTES_FILE], r, answer="n")
        self.assertEqual(code, C.EXIT_OK)
        self.assertNotIn("gh release create", r.calls)

    def test_check_mode_never_invokes_gh_only_prints_the_command(self):
        """A call that reaches cut.py's own code but forgets to thread
        `runner` through (e.g. a stray `create_gh_release(root, version)`
        with no `runner=`) never touches the fake Runner `r` at all: _run's
        own default is `runner or subprocess.run`, and _stream's live
        branch (runner is None) calls subprocess.Popen directly. Neither
        path is visible in r.cmds/r.calls, so a regression there would
        pass silently if this test only read those. Both real primitives
        are patched here so ANY process this whole call graph would
        actually spawn is recorded, threaded through `r` or not."""
        import contextlib
        import io

        spawned = []

        def spy_run(cmd, *a, **kw):
            spawned.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 127, "", "spied: not run")

        class SpyPopen(object):
            def __init__(self, cmd, *a, **kw):
                spawned.append(list(cmd))
                raise OSError("spied: not run")

        r = Runner(GREEN)
        out = io.StringIO()
        with mock.patch.object(C.subprocess, "run", spy_run), \
             mock.patch.object(C.subprocess, "Popen", SpyPopen), \
             contextlib.redirect_stdout(out):
            code, _ = _main(["--check"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertNotIn("gh release create", r.calls)
        self.assertFalse(any(c[0] == "gh" for c in r.cmds), r.cmds)
        gh_spawned = [c for c in spawned if c and c[0] == "gh"]
        self.assertEqual(gh_spawned, [],
                         "check_mode spawned a real gh process outside the "
                         "fake Runner: %s" % gh_spawned)
        self.assertIn("gh release create v%s" % VERSION, out.getvalue())
        # No --release-notes-file here (item 3's default): the preview
        # says the release still needs a body, not "never here".
        self.assertIn("no --release-notes-file", out.getvalue())
        self.assertIn("would NOT run", out.getvalue())

    def test_check_mode_preview_with_a_notes_file_says_it_would_run_after_the_push(self):
        r = Runner(GREEN)
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, _ = _main(["--check", "--release-notes-file", CLEAN_NOTES_FILE], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertNotIn("gh release create", r.calls)
        self.assertIn("would run this after a successful push, never here",
                      out.getvalue())
        self.assertIn("body.md", out.getvalue())

    def _incomplete(self, r, argv=None):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, _ = _main((argv or ["--yes", "--release-notes-file", CLEAN_NOTES_FILE]), r)
        return code, out.getvalue()

    def test_a_failed_gh_release_ends_the_cut_incomplete_with_the_command_that_finishes_it(self):
        """FX3.b: the owner reads the exit code and the word DONE; a requested Release that is not there is an
        unfinished cut (exit 3), never DONE with the failure folded into a sentence."""
        r = Runner(dict(GREEN, **{"gh release create": (1, "HTTP 422\n")}))
        code, out = self._incomplete(r)
        self.assertEqual(code, C.EXIT_INCOMPLETE, r.calls)
        self.assertIn(
            "reproduce_export.py", r.calls,
            "a gh release failure must not stop the chain from verifying "
            "the already-public tag")
        lines = out.splitlines()
        self.assertEqual(len([l for l in lines if l.startswith("INCOMPLETE:")]), 1, out)
        self.assertEqual([l for l in lines if l.startswith("DONE:")], [], out)
        self.assertIn("bm_store park", r.calls)
        self.assertNotIn("bm_store complete", r.calls)

    def test_a_changed_notes_file_also_ends_the_cut_incomplete(self):
        tmp = tempfile.mkdtemp(prefix="cut-test-incomplete-")
        self.addCleanup(shutil.rmtree, tmp, True)
        notes = os.path.join(tmp, "body.md")
        with open(notes, "w", encoding="utf-8") as fh:
            fh.write("# Release notes\n\nScanned.\n")
        r = RewritingRunner(GREEN, notes, new_text="# Release notes\n\nRewritten.\n")
        code, out = self._incomplete(r, ["--yes", "--release-notes-file", notes])
        self.assertEqual(code, C.EXIT_INCOMPLETE, r.calls)
        self.assertNotIn("gh release create", r.calls)
        self.assertTrue(any(l.startswith("INCOMPLETE:") for l in out.splitlines()), out)

    def test_the_parked_fence_note_is_the_printed_line(self):
        r = Runner(dict(GREEN, **{"gh release create": (1, "HTTP 422\n")}))
        _code, out = self._incomplete(r)
        line = [l for l in out.splitlines() if l.startswith("INCOMPLETE:")][0]
        park = [c for c in r.cmds if _key(c) == "bm_store park"][0]
        self.assertEqual(park[park.index("--note") + 1], line)

    def test_the_line_names_the_public_tag_the_guarded_command_the_hash_and_the_read(self):
        sha = "ab" * 32
        line = C.release_incomplete_line(VERSION, ["cut_v1.0.0.sh", "export_public"], "/n/body.md", sha,
                                         "HTTP 422")
        self.assertTrue(line.startswith("INCOMPLETE: tag v%s IS ALREADY PUBLIC" % VERSION), line)
        self.assertIn("cut_v1.0.0.sh, export_public", line)
        self.assertIn(" ".join(C.finish_release_command(VERSION, "/n/body.md", sha)), line)
        self.assertIn("--finish-release %s" % sha, line)
        self.assertIn("sha256 %s" % sha[:12], line)
        self.assertIn("gh release view v%s --repo %s" % (VERSION, C.gh_repo_slug(C.EP.DEFAULT_REMOTE)), line)
        # the guarded route only: no bare gh release create that skips the scans and the hash
        self.assertNotIn("gh release create", line)
        self.assertIn("NO-DATA", C.release_incomplete_line(VERSION, [], "/n/body.md", None, "x"))
        folded = C.release_incomplete_line(VERSION, [], "/n/body.md", sha, "first\nsecond")
        self.assertNotIn("\n", folded)
        long = C.release_incomplete_line(VERSION, [], "/n/body.md", sha, "w" * 5000)
        self.assertIn("w" * 200, long)
        self.assertNotIn("w" * 201, long)

    def test_the_incomplete_exit_code_is_its_own(self):
        self.assertNotIn(C.EXIT_INCOMPLETE, (C.EXIT_OK, C.EXIT_REFUSED, C.EXIT_NODATA))

    def test_no_raw_publish_command_is_printed_on_any_release_failure(self):
        r = Runner(dict(GREEN, **{"gh release create": (1, "HTTP 422\n")}))
        _code, out = self._incomplete(r)
        self.assertNotIn("gh release create v%s" % VERSION, out)
        self.assertIn("--finish-release", out)

    def test_a_failed_gh_release_is_never_retried(self):
        r = Runner(dict(GREEN, **{"gh release create": (1, "HTTP 422\n")}))
        _main(["--yes", "--release-notes-file", CLEAN_NOTES_FILE], r)
        self.assertEqual(r.calls.count("gh release create"), 1, r.calls)

    def test_no_notes_file_is_also_folded_into_the_done_evidence_line(self):
        # Item 9's other half: absence, not only failure, must show up in
        # the same place.
        import contextlib
        import io
        r = Runner(GREEN)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, _ = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        done_line = [l for l in out.getvalue().splitlines()
                    if l.startswith("DONE:")][0]
        self.assertIn("GitHub Release NOT published", done_line, done_line)


class RewritingRunner(Runner):
    """A Runner that rewrites (or deletes, when `new_text` is None) `path`
    the moment `trigger` is answered -- simulating the adversarial
    review's scenario: --release-notes-file is scanned once in main(),
    long before the fence is even claimed, and create_gh_release (the one
    place that actually publishes) runs much later, after the build, the
    human approve prompt AND the push. An editor or a docs tool rewriting
    the file in that window must be caught before those unscanned bytes
    reach `gh release create`. Triggering on export_public.py (the push,
    the step immediately before the gh release step in run_chain) proves
    the rewrite happened strictly after main()'s one scan+hash."""

    def __init__(self, answers, path, trigger="export_public.py",
                new_text=None):
        Runner.__init__(self, answers)
        self.path = path
        self.trigger = trigger
        self.new_text = new_text

    def __call__(self, cmd, **kw):
        proc = Runner.__call__(self, cmd, **kw)
        if _key(cmd) == self.trigger:
            if self.new_text is None:
                if os.path.exists(self.path):
                    os.remove(self.path)
            else:
                with open(self.path, "w", encoding="utf-8") as fh:
                    fh.write(self.new_text)
        return proc


class TheNotesFileIsRehashedRightBeforeItIsPublished(unittest.TestCase):
    """Adversarial review (2026-09-19): the notes file is read and scanned
    ONCE, in main(), before the fence is claimed; create_gh_release runs much
    later -- after the build, the approve prompt and the push -- so bytes
    nobody scanned could reach a public GitHub Release. main() now records
    the scanned file's sha256 and create_gh_release re-hashes right before
    it would publish, refusing (fail closed) on any mismatch, a missing
    file, or an unknown hash, always through the SAME already-tested
    'gh release create failed' path: since FX3.b (2026-10-11) the cut ends
    INCOMPLETE (exit 3, the tag is already public and irreversible, the
    fence parked), the gh step is never reached, and the INCOMPLETE line
    names the guarded route that finishes it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cut-test-hash-")
        self.notes = os.path.join(self.tmp, "body.md")
        with open(self.notes, "w", encoding="utf-8") as fh:
            fh.write("# Release notes\n\nNothing private here.\n")

    def _run(self, r, argv=None):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, _ = _main((argv or []) +
                            ["--yes", "--release-notes-file", self.notes], r)
        return code, out.getvalue()

    def test_an_unchanged_file_publishes_normally(self):
        r = RewritingRunner(GREEN, self.notes, new_text=None)  # never fires
        r.trigger = "no such step"  # never touches the file
        code, out = self._run(r)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertIn("gh release create", r.calls)
        self.assertNotIn("REFUSED", out)

    def test_a_file_modified_after_the_push_refuses_to_publish(self):
        r = RewritingRunner(GREEN, self.notes,
                            new_text="# Release notes\n\nRewritten while "
                                     "the founder was still at the "
                                     "prompt.\n")
        code, out = self._run(r)
        self.assertEqual(code, C.EXIT_INCOMPLETE,
                         "the tag is already public and the Release is "
                         "missing: the cut is incomplete, never DONE: %s"
                         % r.calls)
        self.assertNotIn("gh release create", r.calls,
                         "the real gh binary must never run on unscanned "
                         "bytes")
        self.assertIn("changed since it was scanned", out)
        self.assertIn("REFUSED: gh release create NOT run", out)
        # FX3 recovery ruling: the way out is the guarded route, which
        # repeats the scans and the full hash check; never a bare command.
        self.assertIn("--finish-release", out)
        self.assertNotIn("gh release create v%s" % VERSION, out)
        self.assertTrue(any(l.startswith("INCOMPLETE:")
                            for l in out.splitlines()), out)
        self.assertFalse(any(l.startswith("DONE:")
                             for l in out.splitlines()), out)

    def test_a_file_deleted_after_the_push_refuses_to_publish(self):
        r = RewritingRunner(GREEN, self.notes, new_text=None)
        code, out = self._run(r)
        self.assertEqual(code, C.EXIT_INCOMPLETE, r.calls)
        self.assertNotIn("gh release create", r.calls)
        self.assertIn("could not be re-read", out)
        self.assertIn("REFUSED: gh release create NOT run", out)

    def test_an_unknown_expected_hash_refuses_even_with_a_pristine_file(self):
        # Fail closed: a caller of create_gh_release with no recorded
        # scan hash must never publish, even when the file on disk is
        # perfectly clean -- there is nothing here proving it is the SAME
        # file the scan approved.
        ok, lines = C.create_gh_release("/fake/root", VERSION,
                                        runner=Runner(GREEN),
                                        notes_file=self.notes,
                                        expected_sha256=None)
        self.assertFalse(ok, lines)
        self.assertTrue(any("no recorded scan hash" in l for l in lines),
                        lines)

    def test_real_main_default_terms_file_path_also_refuses_a_rewrite(self):
        # Item (real end-to-end, default config): the SAME test as above
        # but driven through the true CLI shape -- no injected terms_file
        # (default_terms_file()'s own call-time HOME resolution, the exact
        # path a founder's own `cut 1.0.x --release-notes-file ...`
        # actually takes), only the runner stubbed at the subprocess
        # layer, the way the gh-release tests above already do.
        home = tempfile.mkdtemp(prefix="cut-test-home-hash-")
        terms_path = os.path.join(home, ".brothersbe-private-names")
        with open(terms_path, "w", encoding="utf-8") as fh:
            fh.write("plantedterm\n")
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            r = RewritingRunner(GREEN, self.notes,
                                new_text="# Release notes\n\nRewritten.\n")
            del C._BM_STORE_CACHE[:]
            import contextlib
            import io
            out = io.StringIO()
            with mock.patch.object(
                    C, "_load_bm_store",
                    return_value=(FakeBmStore, BM_PATH, None)), \
                 mock.patch.object(C, "release_paths",
                                   return_value=["scripts/", "bundle/"]), \
                 contextlib.redirect_stdout(out):
                code = C.main(["--yes", "--release-notes-file", self.notes],
                              root="/fake/root", runner=r, ask=lambda p: "y")
            del C._BM_STORE_CACHE[:]
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
        self.assertEqual(code, C.EXIT_INCOMPLETE, r.calls)
        self.assertNotIn("gh release create", r.calls)
        self.assertIn("changed since it was scanned", out.getvalue())


class TheGuardedRouteFinishesAMissingRelease(unittest.TestCase):
    """FX3 recovery ruling (plan v5): the only printed way to finish a Release a cut left missing is
    cut.py --finish-release, which repeats the notes scans and checks the full sha256 of the reviewed bytes before
    it creates the Release, and runs no other step of the cut."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cut-test-finish-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.notes = os.path.join(self.tmp, "body.md")
        with open(self.notes, "w", encoding="utf-8") as fh:
            fh.write("# Release notes\n\nReviewed.\n")
        with open(self.notes, "rb") as fh:
            self.sha = hashlib.sha256(fh.read()).hexdigest()

    def finish(self, sha, r=None, notes=None, terms_file=None, version=VERSION):
        r = r or Runner(GREEN)
        argv = ["--finish-release", sha, "--release-notes-file", notes or self.notes]
        if version:
            argv += ["--version", version]
        code, _ = _main(argv, r, terms_file=terms_file)
        return code, r

    def test_the_reviewed_bytes_publish_and_nothing_else_of_the_cut_runs(self):
        code, r = self.finish(self.sha)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertEqual(r.calls.count("gh release create"), 1, r.calls)
        gh = [c for c in r.cmds if _key(c) == "gh release create"][0]
        self.assertEqual(gh, C.gh_release_command(VERSION, notes_file=self.notes))
        for never in ("bm_store claim", "cut_v1.0.0.sh", "export_public.py", "next_cut.py"):
            self.assertNotIn(never, r.calls)

    def test_a_different_full_hash_refuses_before_gh(self):
        code, r = self.finish("0" * 64)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("gh release create", r.calls)

    def test_a_hash_that_is_not_sixty_four_hex_refuses_before_gh(self):
        for bad in ("NO-DATA", self.sha[:12], self.sha.upper()):
            with self.subTest(sha=bad):
                code, r = self.finish(bad)
                self.assertEqual(code, C.EXIT_REFUSED)
                self.assertNotIn("gh release create", r.calls)

    def test_the_scans_are_repeated(self):
        terms = os.path.join(self.tmp, "terms.txt")
        with open(terms, "w", encoding="utf-8") as fh:
            fh.write("plantedterm\n")
        with open(self.notes, "w", encoding="utf-8") as fh:
            fh.write("# Release notes\n\nplantedterm\n")
        with open(self.notes, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        code, r = self.finish(sha, terms_file=terms)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("gh release create", r.calls)

    def test_no_notes_file_refuses(self):
        r = Runner(GREEN)
        code, _ = _main(["--finish-release", self.sha, "--version", VERSION], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("gh release create", r.calls)

    def test_no_explicit_version_refuses(self):
        code, r = self.finish(self.sha, version=None)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertNotIn("gh release create", r.calls)

    def test_a_failed_release_stays_incomplete(self):
        code, r = self.finish(self.sha, r=Runner(dict(GREEN, **{"gh release create": (1, "HTTP 422\n")})))
        self.assertEqual(code, C.EXIT_INCOMPLETE)


class MainReadsTheReleaseNotesFileExactlyOnce(unittest.TestCase):
    """Item (2026-09-19): main() used to open --release-notes-file TWICE
    before the fence was ever claimed -- once inside scan_release_notes_
    file (the scan) and, moments later, again inside _hash_file (recording
    "the hash of the file that was just scanned clean") -- with nothing in
    between guaranteeing the two opens saw the same bytes. A rewrite
    landing in that gap (another process, an editor, a docs tool) would
    make the recorded hash describe bytes nobody scanned; because that
    hash is exactly what gets compared against later, a hash of the
    REWRITTEN bytes would still self-match at publish time if nothing
    touched the file again, so the corruption is invisible downstream.
    create_gh_release's own re-hash right before publish is a DELIBERATE
    second, independent read (its docstring says so explicitly) and stays
    untouched; this fix closes the earlier gap, between the scan and the
    hash, that create_gh_release cannot see at all.

    Proven here by patching the reader (builtins.open, filtered to the
    notes path) so the FIRST open of the file behaves normally and, as a
    side effect right after it returns, rewrites the file on disk. A
    caller that opens the path a second time (the old, unfixed shape)
    reads the rewritten bytes into its hash; a caller that opens it only
    once (the fix) can never observe the rewrite at all, so the hash
    handed to create_gh_release always traces back to what was scanned."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cut-test-oneread-")
        self.notes = os.path.join(self.tmp, "body.md")
        self.scanned_text = "# Release notes\n\nNothing private here.\n"
        with open(self.notes, "w", encoding="utf-8") as fh:
            fh.write(self.scanned_text)
        self.rewritten_text = ("# Release notes\n\nRewritten in the gap a "
                               "second open would have raced into.\n")

    def _run_with_rewrite_after_first_open(self):
        real_open = builtins.open
        target = os.path.abspath(self.notes)
        state = {"opens": 0}

        def fake_open(file, mode="r", *a, **kw):
            fh = real_open(file, mode, *a, **kw)
            if not (isinstance(file, str) and os.path.abspath(file) == target):
                return fh
            state["opens"] += 1
            this_open = state["opens"]
            real_read = fh.read

            def read(*ra, **rkw):
                # Read the ORIGINAL bytes first, exactly like a normal
                # read would, THEN rewrite the file on disk -- so a
                # caller that reads only once still gets the bytes it
                # was scanned against, and only a SECOND open (the old,
                # unfixed shape) can ever observe the rewrite.
                data = real_read(*ra, **rkw)
                if this_open == 1:
                    with real_open(target, "w", encoding="utf-8") as wfh:
                        wfh.write(self.rewritten_text)
                return data

            fh.read = read
            return fh

        captured = {}

        def fake_create_gh_release(root, version, runner=None,
                                   heartbeat=None, remote=None,
                                   notes_file=None, expected_sha256=None):
            captured["expected_sha256"] = expected_sha256
            return True, ["gh release create: skipped by test double"]

        r = Runner(GREEN)
        with mock.patch("builtins.open", fake_open), \
             mock.patch.object(C, "create_gh_release",
                              side_effect=fake_create_gh_release):
            code, _ = _main(["--yes", "--release-notes-file", self.notes], r)
        return code, state["opens"], captured.get("expected_sha256")

    def test_a_rewrite_right_after_the_one_read_cannot_reach_the_recorded_hash(self):
        code, opens, expected_sha256 = self._run_with_rewrite_after_first_open()
        self.assertEqual(code, C.EXIT_OK)
        self.assertEqual(
            opens, 1,
            "the notes file must be opened exactly once before the fence "
            "is claimed; %d opens leaves a window for a rewrite to slip "
            "between the scan and the hash" % opens)
        self.assertEqual(
            expected_sha256,
            hashlib.sha256(self.scanned_text.encode("utf-8")).hexdigest(),
            "the hash handed to create_gh_release must describe the bytes "
            "that were actually scanned, never bytes read a second time "
            "after a rewrite in the gap")
        # The rewrite really did land on disk, proving it fired at all.
        with open(self.notes, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), self.rewritten_text)


def _scan_notes_file(path, terms_file=None, cwd=None):
    """Test-local stand-in for the wrapper cut.py used to export as
    scan_release_notes_file (removed 2026-09-19, item 4 of the opus
    review: it had 0 production callers, all 13 were in this file, and
    main() could not be routed through it without reopening the exact
    double-read race main() was already fixed to close -- see main()'s
    own comment). Exercises the SAME two functions main() actually calls,
    _read_file_bytes then _scan_release_notes_bytes, so this suite still
    proves the scan rule set without a third, unused copy in cut.py."""
    abspath = C._release_notes_abspath(path, cwd)
    raw, err = C._read_file_bytes(abspath)
    if raw is None:
        return False, ["REFUSED: --release-notes-file %s could not be "
                       "read: %s" % (abspath, err)]
    return C._scan_release_notes_bytes(raw, abspath, terms_file)


class ReleaseNotesFileIsScannedBeforeTheFenceIsClaimed(unittest.TestCase):
    """Review item (2026-09-19): --release-notes-file goes straight to a
    public GitHub Release, so before ANYTHING else -- before the fence is
    claimed, before even next_cut.py runs -- it is made absolute and
    scanned with the same gates a push runs: private terms, em/en dashes,
    attribution lines. A missing or unreadable terms list refuses, never
    passes. The matched text is never printed, only its line, column and
    length."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cut-test-scan-")
        self.terms = os.path.join(self.tmp, "terms.txt")
        with open(self.terms, "w", encoding="utf-8") as fh:
            fh.write("acme\nacmewidgetcorp\n")

    def _write_notes(self, text):
        path = os.path.join(self.tmp, "notes.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_a_clean_file_passes(self):
        path = self._write_notes("# Release\n\nNothing here is private.\n")
        ok, lines = _scan_notes_file(path, self.terms)
        self.assertTrue(ok, lines)
        self.assertIn("clean", "\n".join(lines))

    def test_a_short_term_matches_case_sensitively_as_a_whole_word(self):
        path = self._write_notes("Shipped by the acme team today.\n")
        ok, lines = _scan_notes_file(path, self.terms)
        self.assertFalse(ok, lines)
        text = "\n".join(lines)
        self.assertIn("line 1", text)
        self.assertIn("private term, length 4", text)
        self.assertNotIn("acme", text.lower(),
                         "the matched term text must never be printed")

    def test_a_short_term_different_case_does_not_match(self):
        # Case-SENSITIVE for terms of 5 characters or fewer (outgoing_scan's
        # own rule): "ACME" is not "acme".
        path = self._write_notes("The ACME team shipped this.\n")
        ok, lines = _scan_notes_file(path, self.terms)
        self.assertTrue(ok, lines)

    def test_a_long_term_matches_case_insensitively(self):
        path = self._write_notes("Built by ACMEWIDGETCORP engineering.\n")
        ok, lines = _scan_notes_file(path, self.terms)
        self.assertFalse(ok, lines)
        self.assertIn("private term, length 14", "\n".join(lines))

    def test_an_em_dash_refuses(self):
        path = self._write_notes("Shipped fast\u2014no delays.\n")
        ok, lines = _scan_notes_file(path, self.terms)
        self.assertFalse(ok, lines)
        self.assertIn("em or en dash", "\n".join(lines))

    def test_an_en_dash_refuses(self):
        path = self._write_notes("Pages 1\u201310 cover it.\n")
        ok, lines = _scan_notes_file(path, self.terms)
        self.assertFalse(ok, lines)
        self.assertIn("em or en dash", "\n".join(lines))

    def test_an_attribution_line_refuses(self):
        path = self._write_notes(
            "Co-" "Authored-By: Claude Sonnet 5 <no" "reply@anthropic.com>\n")
        ok, lines = _scan_notes_file(path, self.terms)
        self.assertFalse(ok, lines)
        text = "\n".join(lines)
        self.assertIn("attribution line", text)
        self.assertNotIn("Claude", text)
        self.assertNotIn("anthropic", text)

    def test_the_generated_with_footer_refuses(self):
        path = self._write_notes(
            "Body text.\n\nGenerated with [Claude " "Code](https://claude.com)\n")
        ok, lines = _scan_notes_file(path, self.terms)
        self.assertFalse(ok, lines)
        self.assertIn("attribution line", "\n".join(lines))

    def test_a_missing_notes_file_refuses(self):
        # B4 (opus review, 2026-09-19): scan_release_notes_file had 0
        # production callers and is gone; main() carries its own copy of
        # this exact refusal (the only one a real CLI call ever reaches),
        # so this now drives main() itself rather than the removed
        # wrapper -- the mutation proof (main()'s branch turned into
        # `if False:` in a scratch copy) is what shows this test actually
        # depends on that branch, not merely on _scan_notes_file's own.
        import contextlib
        import io
        path = os.path.join(self.tmp, "does-not-exist.md")
        r = Runner(GREEN)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, _ = _main(["--check", "--release-notes-file", path], r,
                            terms_file=self.terms)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("REFUSED", out.getvalue())
        self.assertIn("could not be read", out.getvalue())
        self.assertEqual(r.calls, [])

    def test_an_unreadable_notes_file_path_refuses(self):
        # A directory where a file is expected: a portable way to force an
        # OSError on open() without touching filesystem permissions. Also
        # driven through main() itself, see the previous test.
        import contextlib
        import io
        r = Runner(GREEN)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, _ = _main(["--check", "--release-notes-file", self.tmp], r,
                            terms_file=self.terms)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertIn("could not be read", out.getvalue())
        self.assertEqual(r.calls, [])

    def test_a_missing_terms_list_refuses_never_a_pass(self):
        path = self._write_notes("Perfectly clean text.\n")
        ok, lines = _scan_notes_file(
            path, os.path.join(self.tmp, "no-such-terms.txt"))
        self.assertFalse(ok, lines)
        self.assertIn("REFUSED", "\n".join(lines))
        self.assertIn("private-terms list could not be read", "\n".join(lines))

    def test_the_path_resolves_against_the_given_cwd_not_root(self):
        rel = "notes.md"
        with open(os.path.join(self.tmp, rel), "w", encoding="utf-8") as fh:
            fh.write("Clean.\n")
        ok, lines = _scan_notes_file(rel, self.terms, cwd=self.tmp)
        self.assertTrue(ok, lines)
        self.assertIn(self.tmp, lines[0])

    def test_the_refusal_blocks_the_cut_before_any_bm_store_call(self):
        path = self._write_notes("Shipped by the acme team.\n")
        r = Runner(GREEN)
        code, _ = _main(["--yes", "--release-notes-file", path], r,
                        terms_file=self.terms)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertEqual(r.calls, [],
                         "a refused notes file must block before the fence "
                         "is claimed, before even next_cut.py runs")

    def test_check_mode_is_also_refused_by_a_dirty_notes_file(self):
        path = self._write_notes("Shipped by the acme team.\n")
        r = Runner(GREEN)
        code, _ = _main(["--check", "--release-notes-file", path], r,
                        terms_file=self.terms)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertEqual(r.calls, [])

    def test_a_clean_explicit_notes_file_reaches_the_fence_normally(self):
        r = Runner(GREEN)
        code, _ = _main(["--check", "--release-notes-file", CLEAN_NOTES_FILE],
                        r, terms_file=self.terms)
        self.assertEqual(code, C.EXIT_OK, r.calls)
        self.assertIn("bm_store dump", r.calls)


class DefaultTermsFileIsFoundWithNoOverride(unittest.TestCase):
    """The real bug (2026-09-19): cut.py referenced OS.DEFAULT_TERMS_FILE,
    which scripts/outgoing_scan.py has never exported (grepped: its own
    main() hardcodes the literal "~/.brothersbe-private-names" inline as
    an argparse default and expands it right there, no module constant).
    Every earlier test passed terms_file explicitly, so this path -- the
    one the real CLI actually takes with no override -- was never
    exercised, and `python3 scripts/cut.py --check --version 1.0.21
    --release-notes-file <clean file>` crashed AttributeError on its very
    first real run.

    HOME is redirected here (os.path.expanduser's own documented
    mechanism -- the same one outgoing_scan.py's own default relies on)
    to a temp directory holding a real ~/.brothersbe-private-names, never
    this machine's actual one."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cut-test-home-")
        self.terms_path = os.path.join(self.home,
                                       ".brothersbe-private-names")
        with open(self.terms_path, "w", encoding="utf-8") as fh:
            fh.write("plantedterm\n")
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home

    def _main_true_default_terms(self, argv, runner, answer="y"):
        """Like the module's own _main(), except terms_file is left
        genuinely unset: _main()'s own convenience default (terms_file or
        EMPTY_TERMS_FILE) exists so ordinary tests never touch this
        machine's real ~/.brothersbe-private-names, but that same
        substitution would silently mask the exact bug this class exists
        to catch (cut.py never reaching its own no-override default at
        all). Calls C.main() directly with NO terms_file argument, the
        real CLI's own shape."""
        del C._BM_STORE_CACHE[:]
        with mock.patch.object(C, "_load_bm_store",
                               return_value=(FakeBmStore, BM_PATH, None)), \
             mock.patch.object(C, "release_paths",
                               return_value=["scripts/", "bundle/"]):
            code = C.main(argv, root="/fake/root", runner=runner,
                          ask=lambda p: answer)
        del C._BM_STORE_CACHE[:]
        return code

    def test_default_terms_file_resolves_under_the_redirected_home(self):
        # C.default_terms_file() expands at CALL time (never frozen at
        # import time), so it reflects THIS test's redirected HOME.
        self.assertEqual(C.default_terms_file(), self.terms_path)

    def test_a_clean_file_passes_with_no_terms_file_override_at_all(self):
        clean = os.path.join(self.home, "clean.md")
        with open(clean, "w", encoding="utf-8") as fh:
            fh.write("Nothing private here.\n")
        r = Runner(GREEN)
        code = self._main_true_default_terms(
            ["--check", "--release-notes-file", clean], r)
        self.assertEqual(code, C.EXIT_OK, r.calls)

    def test_a_planted_term_refuses_before_any_bm_store_call(self):
        dirty = os.path.join(self.home, "dirty.md")
        with open(dirty, "w", encoding="utf-8") as fh:
            fh.write("This note carries the plantedterm right here.\n")
        r = Runner(GREEN)
        code = self._main_true_default_terms(
            ["--yes", "--release-notes-file", dirty], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertEqual(r.calls, [],
                         "the default terms file must be found and used "
                         "with no override, refusing before the fence is "
                         "ever claimed")

    def test_the_real_cli_subprocess_does_not_crash_with_no_terms_override(self):
        """The exact shape of the orchestrator's real probe: a genuine
        subprocess, genuine argparse defaults, no Runner, no stubbing at
        all. A planted term (rather than the clean file the probe used)
        keeps this fast -- it refuses at the scan step, before the real
        chain (git worktree, required_fast.sh) would ever run -- while
        exercising the identical no-override code path that crashed."""
        dirty = os.path.join(self.home, "dirty-subprocess.md")
        with open(dirty, "w", encoding="utf-8") as fh:
            fh.write("This carries the plantedterm too.\n")
        env = dict(os.environ, HOME=self.home)
        proc = subprocess.run(
            [sys.executable, C.__file__, "--check", "--version", "9.9.9",
             "--release-notes-file", dirty],
            cwd=self.home, capture_output=True, text=True, env=env,
            timeout=60)
        self.assertNotIn("Traceback", proc.stderr, proc.stderr)
        self.assertNotIn("AttributeError", proc.stdout + proc.stderr,
                         proc.stdout + proc.stderr)
        self.assertEqual(proc.returncode, C.EXIT_REFUSED,
                         proc.stdout + proc.stderr)
        self.assertIn("REFUSED", proc.stdout)


class TheHelpPrints(unittest.TestCase):
    def test_help_exits_0_without_a_traceback(self):
        proc = subprocess.run([sys.executable, C.__file__, "--help"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--force-conflicts", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)


class J064GateLineShadowNeverChangesRequiredFast(unittest.TestCase):
    """J064, wave-1 Jev seam ("Gate/CI/PR log-line classification"): a
    shadow-only second opinion on required_fast.sh's own real
    FAST_SUMMARY_RE matches for this run, fired AFTER required_fast() has
    already decided its (ok, lines) return value from the real exit code --
    never before, never read back into either. Tested directly against
    C.required_fast() (this file already imports cut as C at module scope,
    unlike test_battery_verdict.py/test_export_public.py's own subprocess-
    or fresh-import conventions), the same in-process technique
    test_loop_bridge.py and test_sbe_review_route.py use for their own
    wave-1/2 seams."""

    def _green(self):
        return C.required_fast("/fake/root", VERSION, runner=Runner(GREEN))

    def _red(self):
        r = Runner(dict(GREEN, **{"required_fast.sh": (1, FAST_RED)}))
        return C.required_fast("/fake/root", VERSION, runner=r)

    def _skip_unless_seam_reachable(self):
        """(shared) jev_checks importable is not enough to prove the seam
        was REACHED: a missing data/jev-registry.json or
        data/jev-seams.json (never real in a candidate export tree, per
        docs/plan/EXPORT-ALLOWLIST.txt) makes _jev_gate_line_shadow()'s own
        try/except catch the raise before check_gate_log_lines is ever
        called -- production's byte-identical-result property still holds
        (proved by the adversarial/raising/unavailable tests below, which
        never depend on the fake being reached), but the "was it actually
        called" assertion here needs a real, readable registry/config."""
        if C.jev_checks is None:
            self.skipTest("jev_checks unavailable in this environment "
                          "(fail-open by design)")
        try:
            C.jev_seam.load_seams_config()
            C.jev_seam.load_registry()
        except Exception as exc:  # noqa: BLE001
            self.skipTest("jev_seam config/registry unreadable in this "
                          "environment (fail-open by design): %s" % exc)

    def test_a_normal_call_consults_the_seam_with_the_real_lines_pass_case(self):
        """(a) + wiring proof, PASS case: check_gate_log_lines is actually
        called with the real FAST_SUMMARY_RE lines this run saw, and the
        (ok, lines) it produces matches a run where the seam never fired."""
        self._skip_unless_seam_reachable()
        baseline_ok, baseline_lines = self._green()
        real = C.jev_checks.check_gate_log_lines
        seen = {}

        def fake(lines, *a, **k):
            seen["lines"] = list(lines)
            return []
        C.jev_checks.check_gate_log_lines = fake
        try:
            ok, lines = self._green()
        finally:
            C.jev_checks.check_gate_log_lines = real
        self.assertIn("lines", seen, "check_gate_log_lines was never called")
        self.assertTrue(any("pass 30" in l for l in seen["lines"]),
                        "the real summary line was not among those handed "
                        "to the seam: %r" % seen["lines"])
        self.assertEqual(ok, baseline_ok)
        self.assertEqual(lines, baseline_lines)

    def test_a_normal_call_consults_the_seam_with_the_real_lines_fail_case(self):
        """(a) + wiring proof, FAIL case: same as above, on the FAST_RED
        fixture (required_fast.sh exit 1)."""
        self._skip_unless_seam_reachable()
        baseline_ok, baseline_lines = self._red()
        real = C.jev_checks.check_gate_log_lines
        seen = {}

        def fake(lines, *a, **k):
            seen["lines"] = list(lines)
            return []
        C.jev_checks.check_gate_log_lines = fake
        try:
            ok, lines = self._red()
        finally:
            C.jev_checks.check_gate_log_lines = real
        self.assertIn("lines", seen, "check_gate_log_lines was never called")
        self.assertTrue(any("FAILED: integrate" in l for l in seen["lines"]),
                        "the real FAILED: line was not among those handed "
                        "to the seam: %r" % seen["lines"])
        self.assertEqual(ok, baseline_ok)
        self.assertEqual(lines, baseline_lines)

    def test_an_adversarial_seam_answer_never_changes_ok_or_lines(self):
        """(a continued) + MUTATION-PROOF anchor, both PASS and FAIL cases:
        check_gate_log_lines is rigged to answer "needs-human" on every
        line (the registry's own fail_direction word for low confidence) --
        the one shape a bug in required_fast() reading `.answer` back into
        its return value would act on. required_fast()'s own (ok, lines)
        stay byte-identical to a run where the seam never fired at all."""
        if C.jev_checks is None:
            self.skipTest("jev_checks unavailable in this environment "
                          "(fail-open by design)")
        real = C.jev_checks.check_gate_log_lines

        class _AdverseResult(object):
            answer = "needs-human"
            mode = "shadow"
            reason = "adversarial test fixture"

        for baseline_fn in (self._green, self._red):
            baseline_ok, baseline_lines = baseline_fn()
            C.jev_checks.check_gate_log_lines = \
                lambda lines, *a, **k: [_AdverseResult() for _ in lines]
            try:
                ok, lines = baseline_fn()
            finally:
                C.jev_checks.check_gate_log_lines = real
            self.assertEqual(ok, baseline_ok)
            self.assertEqual(lines, baseline_lines)

    def test_a_raising_seam_never_breaks_required_fast(self):
        """(a continued): a check_gate_log_lines that raises never breaks
        required_fast() and never changes its return value."""
        if C.jev_checks is None:
            self.skipTest("jev_checks unavailable in this environment "
                          "(fail-open by design)")
        baseline_ok, baseline_lines = self._green()
        real = C.jev_checks.check_gate_log_lines
        C.jev_checks.check_gate_log_lines = \
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            ok, lines = self._green()
        finally:
            C.jev_checks.check_gate_log_lines = real
        self.assertEqual(ok, baseline_ok)
        self.assertEqual(lines, baseline_lines)

    def test_jev_checks_unavailable_still_returns_the_same_result(self):
        """(b): jev_checks and jev_seam both unavailable (the fail-open
        case an import failure produces in real life) leaves (ok, lines)
        unchanged, both PASS and FAIL cases."""
        real_checks, real_seam = C.jev_checks, C.jev_seam
        for baseline_fn in (self._green, self._red):
            baseline_ok, baseline_lines = baseline_fn()
            C.jev_checks = None
            C.jev_seam = None
            try:
                ok, lines = baseline_fn()
            finally:
                C.jev_checks = real_checks
                C.jev_seam = real_seam
            self.assertEqual(ok, baseline_ok)
            self.assertEqual(lines, baseline_lines)

ROW_KEY = "a" * 40
HUB_TREE = "b" * 40


def _fake_export_build(dest):
    """An export shaped tree for cut_preflight.check_virgin_gate (its marker file only)."""
    os.makedirs(os.path.join(dest, "scripts"), exist_ok=True)
    with open(os.path.join(dest, "scripts", "required_fast.sh"), "w", encoding="utf-8") as fh:
        fh.write("echo pass 1 fail 0 no-data 0\n")


class TheExportRowsRunOnce(unittest.TestCase):
    """RC1.b1 (docs/plan/specs/RC1.md): the chain runs the two export rows once, after cut_v1.0.0.sh and the hub
    required_fast on the tree that ships, side by side on the real path, and records each row (key, home, verdict,
    a readable log) as a CUTROW line; step 4 of cut_v1.0.0.sh no longer runs a second export dry run."""

    def setUp(self):
        import cut_preflight as P
        self.P = P

    def capture(self, fn, *a, **k):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got = fn(*a, **k)
        return got, out.getvalue()

    def fake(self, verdict, name, text="fake check output\n", barrier=None):
        def check(keep):
            if barrier is not None:
                try:
                    barrier.wait()
                except Exception as exc:  # noqa: BLE001  a broken barrier is the series run this test refuses
                    verdict_now = self.P.REFUSED
                    detail = "ran alone: %s" % type(exc).__name__
                else:
                    verdict_now, detail = verdict, "fake"
            else:
                verdict_now, detail = verdict, "fake"
            with open(keep, "w", encoding="utf-8") as fh:
                fh.write(text)
            return verdict_now, name, detail
        return check

    def rows(self, checks, tree=(ROW_KEY, ""), runner=None):
        with mock.patch.object(C, "export_tree", return_value=tree):
            (ok, lines), out = self.capture(REAL_EXPORT_ROWS, "/fake/root", VERSION, runner=runner, checks=checks)
        rows, problems = C.CR.parse_rows(out)
        self.assertEqual(problems, [], out)
        return ok, lines, rows, out

    # REQ-RC1-07
    def test_emit_row_prints_the_cutrow_line_and_writes_its_log(self):
        row, out = self.capture(C.emit_row, "virgin-gate", ["sh", "scripts/required_fast.sh"], ROW_KEY, "empty",
                                "PASS", 2.5, "pass 1 fail 0 no-data 0\n")
        rows, problems = C.CR.parse_rows(out)
        self.assertEqual((rows, problems), ([row], []))
        self.assertEqual(C.CR.validate_row(row), "")
        self.assertTrue(row["log_path"].startswith(EVIDENCE_TMP), row["log_path"])
        self.assertIn("row-virgin-gate-%s-empty-" % ROW_KEY[:12], row["log_path"])
        with open(row["log_path"], encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "pass 1 fail 0 no-data 0\n")
        self.assertEqual(hashlib.sha256(open(row["log_path"], "rb").read()).hexdigest(), row["log_sha256"])

    def test_emit_row_refuses_a_row_it_could_not_reuse(self):
        for key, verdict in (("", "PASS"), (ROW_KEY[:39], "PASS"), (ROW_KEY, "OK")):
            with self.subTest(key=key, verdict=verdict):
                with self.assertRaises(ValueError):
                    self.capture(C.emit_row, "virgin-gate", ["sh"], key, "empty", verdict, 1.0, "x")

    # REQ-RC1-06
    def test_the_two_rows_run_side_by_side_on_the_real_path(self):
        import threading
        barrier = threading.Barrier(2, timeout=10)
        ok, lines, rows, out = self.rows({"export-real-home": self.fake(self.P.OK, "export-real-home",
                                                                        "EXPORT-TREE: %s\n" % ROW_KEY, barrier),
                                          "virgin-gate": self.fake(self.P.OK, "virgin-gate", barrier=barrier)})
        self.assertTrue(ok, out)
        self.assertEqual(sorted((r["row"], r["home"], r["verdict"], r["key"]) for r in rows),
                         [("export-real-home", "real", "PASS", ROW_KEY), ("virgin-gate", "empty", "PASS", ROW_KEY)])

    # REQ-RC1-08
    def test_one_failing_row_fails_and_both_rows_are_still_emitted(self):
        for bad, verdict in ((self.P.REFUSED, "FAIL"), (self.P.NODATA, "NO-DATA")):
            with self.subTest(verdict=verdict):
                ok, lines, rows, out = self.rows({
                    "export-real-home": self.fake(self.P.OK, "export-real-home", "EXPORT-TREE: %s\n" % ROW_KEY),
                    "virgin-gate": self.fake(bad, "virgin-gate")}, runner=Runner({}))
                self.assertFalse(ok, out)
                self.assertEqual([(r["row"], r["verdict"]) for r in rows],
                                 [("export-real-home", "PASS"), ("virgin-gate", verdict)])

    def test_an_unreadable_tree_id_is_no_data_and_runs_no_check(self):
        ran = []
        checks = {"export-real-home": lambda keep: ran.append(1), "virgin-gate": lambda keep: ran.append(2)}
        ok, lines, rows, out = self.rows(checks, tree=("", "the tree id could not be read"))
        self.assertFalse(ok)
        self.assertEqual((ran, rows), ([], []))
        self.assertTrue(any("NO-DATA" in l for l in lines), lines)

    def test_an_export_row_that_measured_another_tree_or_names_none_is_no_data(self):
        for text in ("EXPORT-TREE: %s\n" % ("c" * 40), "CLEAR with no tree line\n"):
            with self.subTest(text=text):
                ok, lines, rows, out = self.rows({
                    "export-real-home": self.fake(self.P.OK, "export-real-home", text),
                    "virgin-gate": self.fake(self.P.OK, "virgin-gate")}, runner=Runner({}))
                self.assertFalse(ok, out)
                self.assertEqual(rows[0]["verdict"], "NO-DATA")

    def test_export_tree_reads_the_one_line_through_the_runner(self):
        for answer, want in (((0, "EXPORT-TREE: %s\n" % ROW_KEY), ROW_KEY), ((2, "NO-DATA: no build\n"), ""),
                             ((0, "garbage\n"), ""), ((0, "EXPORT-TREE: %s\nEXPORT-TREE: %s\n" % (ROW_KEY, HUB_TREE)), "")):
            with self.subTest(answer=answer):
                r = Runner({"cut_preflight.py": answer})
                tree, why = C.export_tree("/fake/root", VERSION, runner=r)
                self.assertEqual(tree, want)
                self.assertEqual(bool(why), not want)
                cmd = r.cmds[0]
                self.assertEqual(cmd[-1], "--export-tree-id")
                self.assertEqual(cmd[cmd.index("--version") + 1], VERSION)

    # REQ-RC1-05, the chain
    def chain(self, answers):
        r = Runner(dict(GREEN, **dict({"git rev-parse HEAD^{tree}": (0, HUB_TREE + "\n"),
                                       "cut_preflight.py": (0, "EXPORT-TREE: %s\n" % ROW_KEY),
                                       "export_public.py": (0, "EXPORT-TREE: %s\nCLEAR\n" % ROW_KEY)}, **answers)))
        with mock.patch.object(self.P, "export_tree_builder", return_value=_fake_export_build), \
                mock.patch.object(self.P, "real_home_refusal", return_value=""), \
                mock.patch.object(C, "export_rows", REAL_EXPORT_ROWS):
            (passed, failed), out = self.capture(C.chain, "/fake/root", VERSION, runner=r)
        rows, problems = C.CR.parse_rows(out)
        return passed, failed, r, rows, out

    def test_the_chain_runs_the_export_rows_once_after_the_cut_script_and_the_hub_gate(self):
        passed, failed, r, rows, out = self.chain({})
        self.assertEqual(failed, [], out)
        self.assertIn("export rows", passed)
        exports = [i for i, c in enumerate(r.calls) if c == "export_public.py"]
        self.assertEqual(len(exports), 1, r.calls)
        cmd = r.cmds[exports[0]]
        for flag in ("--dry-run", "--prove-required-fast", "--tag-time-checks", "--remote"):
            self.assertIn(flag, cmd)
        self.assertNotIn("--push", cmd)
        fasts = [i for i, c in enumerate(r.calls) if c == "required_fast.sh"]
        self.assertEqual(len(fasts), 2, "the hub gate and the virgin gate, once each: %s" % r.calls)
        self.assertLess(r.calls.index("cut_v1.0.0.sh"), fasts[0])
        self.assertLess(fasts[0], exports[0])
        self.assertLess(exports[0], r.calls.index("refresh_cut.py"))
        self.assertEqual([(x["row"], x["key"], x["home"], x["verdict"]) for x in rows],
                         [("required-fast-hub", HUB_TREE, "real", "PASS"),
                          ("export-real-home", ROW_KEY, "real", "PASS"),
                          ("virgin-gate", ROW_KEY, "empty", "PASS")])

    def test_the_chain_fails_when_an_export_row_fails(self):
        for answers in ({"export_public.py": (1, "EXPORT-TREE: %s\nREFUSED: no\n" % ROW_KEY)},
                        {"cut_preflight.py": (2, "NO-DATA: no build\n")}):
            with self.subTest(answers=answers):
                passed, failed, r, rows, out = self.chain(answers)
                self.assertIn("export rows", failed, out)
                self.assertNotIn("refresh_cut.py", r.calls, "stop_early ran on past a failed export row")

    def test_a_failed_hub_gate_runs_no_export_row(self):
        passed, failed, r, rows, out = self.chain({"required_fast.sh": (1, "FAILED: x\n")})
        self.assertNotIn("export_public.py", r.calls)
        self.assertEqual([x["row"] for x in rows], ["required-fast-hub"])
        self.assertEqual(rows[0]["verdict"], "FAIL")

    def test_check_export_real_home_passes_the_flag_and_keeps_where_asked(self):
        import tempfile as T
        keep = os.path.join(T.mkdtemp(prefix="test-cut-keep-"), "export.txt")
        self.addCleanup(shutil.rmtree, os.path.dirname(keep), True)
        r = Runner({"export_public.py": (0, "EXPORT-TREE: %s\n" % ROW_KEY)})
        home = os.path.dirname(keep)
        os.makedirs(os.path.join(home, ".claude"))
        got = self.P.check_export_real_home("/fake/root", "origin", r, home=home, tag_time_checks=True, keep=keep)
        self.assertEqual(got[0], self.P.OK)
        self.assertIn("--tag-time-checks", r.cmds[0])
        with open(keep, encoding="utf-8") as fh:
            self.assertIn("EXPORT-TREE", fh.read())
        r = Runner({})
        self.P.check_export_real_home("/fake/root", "origin", r, home=home)
        self.assertNotIn("--tag-time-checks", r.cmds[0])
        vkeep = os.path.join(home, "virgin.txt")
        got = self.P.check_virgin_gate("/fake/root", Runner({"required_fast.sh": (0, "pass 3 fail 0 no-data 0\n")}),
                                       build=_fake_export_build, keep=vkeep)
        self.assertEqual(got[0], self.P.OK, got)
        with open(vkeep, encoding="utf-8") as fh:
            self.assertIn("pass 3", fh.read())

    def test_export_tree_id_names_the_bytes_and_refuses_what_it_cannot_read(self):
        import tempfile as T
        counter = []

        def build(dest):
            counter.append(1)
            with open(os.path.join(dest, "a.txt"), "w", encoding="utf-8") as fh:
                fh.write("same bytes\n")
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.test", GIT_COMMITTER_NAME="t",
                       GIT_COMMITTER_EMAIL="t@example.test", GIT_COMMITTER_DATE="@%d +0000" % (1000000000 + len(counter)),
                       GIT_AUTHOR_DATE="@%d +0000" % (1000000000 + len(counter)))
            for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "build %d" % len(counter)]):
                subprocess.run(["git", "-c", "commit.gpgsign=false"] + args, cwd=dest, env=env, check=True,
                               capture_output=True)

        one, why = self.P.export_tree_id("/fake/root", build=build)
        two, why2 = self.P.export_tree_id("/fake/root", build=build)
        self.assertEqual((why, why2), ("", ""))
        self.assertRegex(one, r"^[0-9a-f]{40}$")
        self.assertEqual(one, two, "two builds of the same bytes named two trees")

        def boom(dest):
            raise RuntimeError("no export")
        for bad in (boom, lambda dest: None):
            with self.subTest(build=bad):
                tree, why = self.P.export_tree_id("/fake/root", build=bad)
                self.assertEqual(tree, "")
                self.assertTrue(why)

    def test_the_export_tree_id_mode_prints_one_line(self):
        import contextlib
        import io
        for answer, code, want in (((ROW_KEY, ""), 0, "EXPORT-TREE: %s\n" % ROW_KEY),
                                   (("", "no build"), 2, "NO-DATA: no build\n")):
            with self.subTest(answer=answer):
                out = io.StringIO()
                with mock.patch.object(self.P, "export_tree_id", return_value=answer), \
                        mock.patch.object(self.P, "run_all", side_effect=AssertionError("ran the rows")), \
                        contextlib.redirect_stdout(out):
                    got = self.P.main(["--version", VERSION, "--export-tree-id"])
                self.assertEqual((got, out.getvalue()), (code, want))

    # M-RC1-STEP4
    def test_step_4_runs_only_the_invariant_and_the_chain_runs_the_dry_run(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cut_v1.0.0.sh"), encoding="utf-8") as fh:
            text = fh.read()
        body = text[text.index("== 4."):text.index("STOP. Review")]
        self.assertIn("python3 scripts/release_invariant.py", body)
        code = [l for l in body.splitlines() if l.strip() and not l.lstrip().startswith(("#", "echo"))]
        self.assertFalse([l for l in code if "export_public.py" in l], body)
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cut.py"), encoding="utf-8") as fh:
            self.assertIn("export_rows(", fh.read())


#: LX1.b1: setUpModule stubs the canary for every scripted chain in this
#: module (no chain here may run linux_canary.py through its runner); the
#: classes below drive the real functions, kept here.
REAL_START_CANARY = C.start_canary
REAL_AWAIT_CANARY = C.await_canary
CANARY_PASS_OUT = "REUSED linux-canary aaaaaaaaaaaa /r.json\nCANARY PASS /r.json reused\n"


class TheChainDispatchesTheCanary(unittest.TestCase):
    """REQ-LX1-13 to 16: the chain starts the canary after cut_v1.0.0.sh,
    before required_fast, awaits it after the last local row, never on a
    resume, and terminates it on an early stop."""

    def chain(self, answers=None, resume=False, start=None, await_=None):
        r = Runner(dict(GREEN, **(answers or {})))
        seen = {}

        def awaiting(handle, version):
            seen["calls_at_await"] = list(r.calls)
            return (await_ or REAL_AWAIT_CANARY)(handle, version)
        with mock.patch.object(C, "start_canary", start or REAL_START_CANARY), \
                mock.patch.object(C, "await_canary", awaiting), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            passed, failed = C.chain("/fake/root", VERSION, runner=r, resume=resume)
        return passed, failed, r, seen

    def test_the_canary_starts_after_the_cut_script_and_before_required_fast(self):
        passed, failed, r, seen = self.chain({"linux_canary.py": (0, CANARY_PASS_OUT)})
        self.assertEqual(failed, [], r.calls)
        self.assertEqual(r.calls.count("linux_canary.py"), 1, r.calls)
        i = r.calls.index("linux_canary.py")
        self.assertLess(r.calls.index("cut_v1.0.0.sh"), i)
        self.assertLess(i, r.calls.index("plugin_bump_gate.py"))
        self.assertLess(i, r.calls.index("required_fast.sh"))
        cmd = r.cmds[i]
        self.assertEqual(cmd[2:], ["--version", VERSION, "--root", "/fake/root"])
        # awaited after the last local row
        self.assertEqual(seen["calls_at_await"][-1], "retire_catalogs.py")
        self.assertEqual(passed[-1], "linux canary")

    def test_a_failed_or_no_data_canary_fails_the_chain(self):
        for out, code in (("CANARY FAIL /r.json required-fast-canary concluded failure\n", 1),
                          ("CANARY NO-DATA - nothing recorded, nothing pushed\n", 2),
                          ("no verdict line at all\n", 0)):
            passed, failed, r, _ = self.chain({"linux_canary.py": (code, out)})
            self.assertIn("linux canary", failed, out)
            self.assertNotIn("linux canary", passed)

    def test_await_reads_the_last_canary_line(self):
        def handle(code, out):
            return C.CanaryHandle(result=subprocess.CompletedProcess(["x"], code, out, ""))
        ok, lines = REAL_AWAIT_CANARY(handle(0, CANARY_PASS_OUT), VERSION)
        self.assertTrue(ok)
        self.assertIn("CANARY PASS /r.json reused", lines)
        for code, out in ((1, "CANARY FAIL /r.json x\n"), (2, "CANARY NO-DATA - x\n"),
                          (1, "CANARY PASS /r.json x\n"), (0, "CANARY PASS /r.json x\nCANARY NO-DATA - y\n"),
                          (0, ""), (0, "CANARYPASS\n")):
            ok, lines = REAL_AWAIT_CANARY(handle(code, out), VERSION)
            self.assertFalse(ok, (code, out))
            self.assertTrue(lines)
        ok, lines = REAL_AWAIT_CANARY(None, VERSION)
        self.assertFalse(ok)
        self.assertIn("did not start", " ".join(lines))

    def test_a_canary_that_never_started_is_a_failed_row(self):
        passed, failed, r, _ = self.chain(start=lambda root, version, runner=None: None)
        self.assertIn("linux canary", failed)

    def test_no_canary_starts_on_a_resume(self):
        passed, failed, r, seen = self.chain({"linux_canary.py": (0, CANARY_PASS_OUT)}, resume=True)
        self.assertNotIn("linux_canary.py", r.calls)
        self.assertNotIn("linux canary", passed + failed)
        self.assertEqual(seen, {})

    def test_an_early_stop_terminates_the_child(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        self.addCleanup(lambda: child.poll() is None and (child.kill(), child.wait()))
        handle = C.CanaryHandle(proc=child, log="")
        passed, failed, r, seen = self.chain({"plugin_bump_gate.py": (1, "bump refused\n")},
                                             start=lambda root, version, runner=None: handle)
        self.assertEqual(failed, ["plugin_bump_gate"])
        self.assertIsNotNone(child.poll(), "the canary outlived the chain")
        self.assertEqual(seen, {})

    def test_the_real_start_runs_the_child_and_reads_its_log(self):
        root = tempfile.mkdtemp(prefix="lx1-root-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "scripts"))
        with open(os.path.join(root, "scripts", "linux_canary.py"), "w") as fh:
            fh.write("import sys\nprint('working')\nprint('CANARY PASS /r.json ' + ' '.join(sys.argv[1:]))\n")
        with mock.patch.dict(os.environ, {"BROTHER_SCRATCH": os.path.join(root, "scratch")}), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            handle = REAL_START_CANARY(root, VERSION)
            ok, lines = REAL_AWAIT_CANARY(handle, VERSION)
        self.assertTrue(ok, lines)
        self.assertIn("CANARY PASS /r.json --version %s --root %s" % (VERSION, root), lines)
        self.assertTrue(handle.log.startswith(os.path.join(root, "scratch")))


REAL_CANARY_CHECK = C.canary_check
TREE_A, TREE_B = "a" * 40, "b" * 40


class CanaryRunner(Runner):
    """GREEN, plus the export tree id from `trees` (one per call, the last
    repeating) and linux_canary.py --covering answered from `covering`
    (tree -> (code, stdout))."""

    def __init__(self, trees, covering, answers=None):
        Runner.__init__(self, dict(GREEN, **(answers or {})))
        self.trees, self.covering = list(trees), dict(covering)

    def __call__(self, cmd, **kw):
        if "--export-tree-id" in cmd:
            Runner.__call__(self, cmd, **kw)
            tree = self.trees.pop(0) if len(self.trees) > 1 else self.trees[0]
            if isinstance(tree, tuple):
                return subprocess.CompletedProcess(cmd, tree[0], tree[1], "")
            return subprocess.CompletedProcess(cmd, 0, "EXPORT-TREE: %s\n" % tree, "")
        if "--covering" in cmd:
            Runner.__call__(self, cmd, **kw)
            code, out = self.covering.get(cmd[cmd.index("--covering") + 1], (1, "NOT COVERED: no canary record\n"))
            return subprocess.CompletedProcess(cmd, code, out, "")
        return Runner.__call__(self, cmd, **kw)


class TheQuestionNeedsTheCanary(unittest.TestCase):
    """REQ-LX1-17 to 20: no PASS record for HEAD's export tree, no question;
    asked again after the answer, on the tree at that moment."""

    def setUp(self):
        p = mock.patch.object(C, "canary_check", REAL_CANARY_CHECK)
        p.start()
        self.addCleanup(p.stop)

    def test_no_covering_record_means_no_question(self):
        r = CanaryRunner([TREE_A], {})
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code, asked = _main([], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertEqual(asked, [], "the question was asked with no Linux verdict")
        self.assertIn("no Linux canary PASS for the export tree %s" % TREE_A[:12], out.getvalue())
        self.assertNotIn("export_public.py", r.calls)

    def test_a_covering_record_lets_the_question_through(self):
        r = CanaryRunner([TREE_A], {TREE_A: (0, "/ev/linux-canary-x.json\n")})
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            code, asked = _main([], r, answer="n")
        self.assertEqual(len(asked), 1)
        self.assertEqual(code, C.EXIT_OK)

    def test_every_covering_reason_refuses(self):
        for reason in ("NOT COVERED: the canary record x is for tree bbbbbbbbbbbb, not aaaaaaaaaaaa",
                       "NOT COVERED: the canary record x is stale: finished 2026-10-01T00:00:00Z",
                       "NOT COVERED: the canary record x says FAIL, not PASS",
                       "NOT COVERED: the canary record x says NO-DATA, not PASS",
                       "NOT COVERED: the newest canary record for tree aaaaaaaaaaaa may be x, which is corrupt"):
            r = CanaryRunner([TREE_A], {TREE_A: (1, reason + "\n")})
            why = C.canary_refusal("/fake/root", VERSION, r)
            self.assertTrue(why.startswith("no Linux canary PASS for the export tree %s: " % TREE_A[:12]), why)
            self.assertIn(reason, why)
            self.assertEqual(C.canary_check("/fake/root", VERSION, r)[0], "")
        cmd = [c for c in r.cmds if "--covering" in c][0]
        self.assertEqual(cmd[2:], ["--covering", TREE_A, "--version", VERSION])

    def test_a_covering_record_is_returned(self):
        r = CanaryRunner([TREE_A], {TREE_A: (0, "/ev/linux-canary-x.json\n")})
        self.assertEqual(C.canary_check("/fake/root", VERSION, r), ("/ev/linux-canary-x.json", ""))
        self.assertEqual(C.canary_refusal("/fake/root", VERSION, r), "")

    def test_missing_tool_or_empty_output_refuses(self):
        for answer in ((127, ""), (0, ""), (0, "\n")):
            r = CanaryRunner([TREE_A], {TREE_A: answer})
            self.assertTrue(C.canary_refusal("/fake/root", VERSION, r), answer)

    def test_an_unreadable_tree_id_refuses(self):
        for tree in ((1, "cut-preflight: the export tree could not be built\n"), (0, "no id\n")):
            r = CanaryRunner([tree], {TREE_A: (0, "/ev/x.json\n")})
            why = C.canary_refusal("/fake/root", VERSION, r)
            self.assertIn("no Linux canary PASS for the export tree", why)
            self.assertFalse([c for c in r.cmds if "--covering" in c], "covering asked with no tree id")

    def test_the_recheck_reads_the_tree_again_after_the_answer(self):
        r = CanaryRunner([TREE_A, TREE_B], {TREE_A: (0, "/ev/a.json\n")})
        self.assertEqual(C.canary_refusal("/fake/root", VERSION, r), "")
        with mock.patch.object(C, "_covering_record", return_value=(FAKE_REHEARSAL, "")):
            why = C.pre_push_recheck("/fake/root", VERSION, GREEN_HEAD, FAKE_REHEARSAL, runner=r)
        self.assertTrue(why.startswith("REFUSED: no Linux canary PASS for the export tree %s" % TREE_B[:12]), why)
        r = CanaryRunner([TREE_A], {TREE_A: (0, "/ev/a.json\n")})
        with mock.patch.object(C, "_covering_record", return_value=(FAKE_REHEARSAL, "")):
            self.assertEqual(C.pre_push_recheck("/fake/root", VERSION, GREEN_HEAD, FAKE_REHEARSAL, runner=r), "")


_FP1_FAIL = ("FAIL: required-fast failed again on attempt 2 of 2 on pull request 7: "
             "https://github.com/o/r/actions/runs/5/attempts/2; first failure: "
             "https://github.com/o/r/actions/runs/5/job/1 (test_timing (t.C))")
_FP1_RERUN = ("RERUN: attempt 2 of run 5, first failure https://github.com/o/r/actions/runs/5/job/1 "
              "(test_timing (t.C)), justified by /fixture/canary.json PASS on tree aaaaaaaaaaaa")
_FP1_PUBLIC = "public check: attempt 2 of 2, first failure test_timing (t.C) https://github.com/o/r/actions/runs/5/job/1"


class TheStopRecordNamesTheFailure(unittest.TestCase):
    """FP1.b (docs/plan/specs/FP1.md): the stop record's kept characters
    begin with the failure and both attempts; a green push after a rerun
    says attempt 2 of 2 in its DONE evidence; the exporter is handed the
    canary record the question was asked on."""

    PREAMBLE = ["preamble line %02d of the exporter, before any failure" % i for i in range(30)]

    def stop_record(self):
        path = os.path.join(EVIDENCE_TMP, "stop-%s-%s.json" % (VERSION, GREEN_HEAD[:12]))
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_the_kept_characters_begin_with_the_failure(self):
        """REQ-FP1-07, M-FP1-ORDER."""
        out = "\n".join(self.PREAMBLE + [_FP1_RERUN, _FP1_FAIL]) + "\n"
        r = Runner(dict(GREEN, **{"export_public.py": (1, out)}))
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            code, _asked = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        rec = self.stop_record()
        self.assertTrue(rec["detail"].startswith(_FP1_RERUN + "\n" + _FP1_FAIL[:100]), rec["detail"])
        self.assertEqual(rec["kind"], C.STOP_HOST_REFUSED)

    def test_stop_detail_orders_without_dropping(self):
        lines = ["export_public --push: exit 1", "a", "NO-DATA: x", "b", "FAIL: y", "public check: z"]
        self.assertEqual(C.stop_detail(lines).splitlines(),
                         ["NO-DATA: x", "FAIL: y", "public check: z", "export_public --push: exit 1", "a", "b"])
        plain = ["export_public --push: exit 1", "a", "b"]
        self.assertEqual(C.stop_detail(plain), "\n".join(plain))
        self.assertEqual(C.stop_detail([]), "")

    def test_the_classification_still_reads_the_whole_output(self):
        """REQ-FP1-09: the exporter's armed first line still decides the kind
        although the stop record's detail puts its REFUSED: line first."""
        token = "0123456789abcdef"
        out = "\n".join(["%s %s" % (C.EP.REMOTE_WRITE_ARMED, token)] + self.PREAMBLE[:3]
                        + ["REFUSED: the tag-time checks found 1 problem",
                           "%s %s" % (C.EP.REMOTE_WRITE_NONE, token)]) + "\n"
        self.assertEqual(C.classify_push_failure(out), C.STOP_LOCAL_REFUSED)
        r = Runner(dict(GREEN, **{"export_public.py": (1, out)}))
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            code, _asked = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_REFUSED)
        rec = self.stop_record()
        self.assertTrue(rec["detail"].startswith("REFUSED: the tag-time checks"), rec["detail"])
        self.assertEqual(rec["kind"], C.STOP_LOCAL_REFUSED)

    def test_a_green_push_after_a_rerun_says_attempt_two_of_two(self):
        """REQ-FP1-08, M-FP1-SILENT."""
        out = _FP1_RERUN + "\nrequired-fast: PASS on pull request 7\n" + _FP1_PUBLIC + "\n" + GREEN_EXPORT_PUBLIC_OUT
        r = Runner(dict(GREEN, **{"export_public.py": (0, out)}))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            code, _asked = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK, printed.getvalue())
        done = [l for l in printed.getvalue().splitlines() if l.startswith("DONE:")]
        self.assertEqual(len(done), 1, printed.getvalue())
        self.assertIn(_FP1_PUBLIC, done[0])
        self.assertIn(_FP1_RERUN, done[0])

    def test_a_clean_first_pass_says_nothing_of_attempts(self):
        r = Runner(GREEN)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            code, _asked = _main(["--yes"], r)
        self.assertEqual(code, C.EXIT_OK)
        self.assertNotIn("attempt", [l for l in printed.getvalue().splitlines() if l.startswith("DONE:")][0])

    def test_the_canary_record_reaches_the_exporter_only_when_given(self):
        """REQ-FP1-10, M-FP1-FLAG."""
        for given, want in (("/r/linux-canary.json", ["--canary-record", "/r/linux-canary.json"]), ("", None)):
            r = Runner(GREEN)
            C.export_public_push("/fake/root", VERSION, r, canary_record=given)
            cmd = [c for c in r.cmds if _key(c) == "export_public.py"][0]
            if want:
                self.assertEqual(cmd[cmd.index("--canary-record"):cmd.index("--canary-record") + 2], want)
            else:
                self.assertNotIn("--canary-record", cmd)
        r = Runner(GREEN)
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            _main(["--yes"], r)
        cmd = [c for c in r.cmds if _key(c) == "export_public.py"][0]
        self.assertIn("/fixture/canary.json", cmd, "the covering record was not handed to the exporter")


def setUpModule():
    # Finding 31 (loop audit 2026-09-26), same fixture as test_board_status.py:
    # unmocked production calls read the tracked data/jev-seams.json, where
    # J030, J063, J064, J102 and J117 sit in shadow, and jev_decide resolves
    # the machine's real bridge (~/.claude/bin/or_ask.py) at import. A spy on
    # jev_seam.consult counted 152 calls (all J064) reaching a live shadow
    # entry in one run of this module on hub main under an empty HOME. Pin an
    # all-off seams config and a bridge path that does not exist, so only a
    # case that patches its own config and runner calls out.
    import shutil
    from unittest import mock
    import jev_decide
    import jev_seam
    root = tempfile.mkdtemp(prefix="brother-jev-seams-test-")
    unittest.addModuleCleanup(shutil.rmtree, root, ignore_errors=True)
    seams = os.path.join(root, "jev-seams.json")
    with open(seams, "w", encoding="utf-8") as fh:
        json.dump({"modes": {}}, fh)
    no_bridge = os.path.join(root, "no-bridge-in-tests")
    for patcher in (mock.patch.object(jev_seam, "DEFAULT_SEAMS_CONFIG_PATH", seams),
                    mock.patch.object(jev_decide, "DEFAULT_BRIDGE_PATH", no_bridge)):
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)
    # The processes these tests launch re-import jev_seam and read the
    # tracked config afresh, out of reach of the patches above, and every
    # consult, even an off one, writes attempt rows. So the children get
    # BROTHER_JEV_SEAMS_OFF (the tracked file reads as every entry off), a
    # temporary BROTHER_JEV_STATE_DIR (ledger and daily call budget) and a
    # bridge command that cannot launch, quoted because the variable is
    # parsed as a command line; this process gets the same ledger and
    # budget paths, since its constants were fixed at import.
    import shlex
    import jev_checks
    state = tempfile.mkdtemp(prefix="brother-jev-state-test-")
    unittest.addModuleCleanup(shutil.rmtree, state, ignore_errors=True)
    ledger = os.path.join(state, "ledger")
    for patcher in (mock.patch.object(jev_seam, "DEFAULT_LEDGER_DIR", ledger),
                    mock.patch.object(jev_checks, "DEFAULT_LEDGER_DIR", ledger),
                    mock.patch.object(jev_seam, "DEFAULT_BUDGET_PATH",
                                      os.path.join(state, "jev-budget.json")),
                    mock.patch.dict(os.environ, {
                        "BROTHER_JEV_SEAMS_OFF": "1",
                        "BROTHER_JEV_STATE_DIR": state,
                        "BROTHER_DECISION_BRIDGE": shlex.quote(jev_decide.DEFAULT_BRIDGE_PATH)})):
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)
    # CV1.a (2026-10-03): a real cut now passes cut_gates before its fence
    # (an explicit --version is refused until CV1.e, and the tag state is read
    # through git), and CV1.e reads the terminal through _stdin_is_tty. Both
    # are proven in test_cv1_cut_rehearsed.py; the scripted chains here are
    # about the chain, so the gates pass and a person is at the terminal, and
    # an explicit version still never runs next_cut.py.
    # CV1.e also reads the covering rehearsal record before the question
    # and again before the push (proven in test_cv1_cut_rehearsed.py), and a
    # refused push files a stop record: the record lookup answers one fixed
    # path, and the evidence folder is a throwaway one, so no test here ever
    # writes into the real ~/.claude/evidence/cut.
    os.makedirs(EVIDENCE_TMP, exist_ok=True)
    unittest.addModuleCleanup(shutil.rmtree, EVIDENCE_TMP, ignore_errors=True)
    for patcher in (mock.patch.object(C, "cut_gates", return_value=""),
                    mock.patch.object(C, "_stdin_is_tty", return_value=True,
                                      create=True),
                    mock.patch.object(C.CR, "covering_record",
                                      return_value=(FAKE_REHEARSAL, "")),
                    mock.patch.object(C.CR, "evidence_dir",
                                      return_value=EVIDENCE_TMP),
                    mock.patch.object(C, "export_rows",
                                      return_value=STUB_EXPORT_ROWS),
                    mock.patch.object(C, "start_canary", return_value=None),
                    mock.patch.object(C, "canary_check",
                                      return_value=("/fixture/canary.json", "")),
                    mock.patch.object(C, "await_canary",
                                      return_value=(True, ["linux canary: stubbed"]))):
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


#: CV1.e: the throwaway evidence folder and the fixed rehearsal record path
#: setUpModule hands the whole module.
EVIDENCE_TMP = os.path.join(tempfile.gettempdir(),
                            "cut-test-evidence-%d" % os.getpid())
FAKE_REHEARSAL = os.path.join(EVIDENCE_TMP, "rehearsal-test-READY.json")


def tearDownModule():
    # Python 3.9's unittest runs the module cleanups above only when this
    # hook exists (fixed in 3.10); without it the fixture roots leak per run.
    pass

if __name__ == "__main__":
    unittest.main()
