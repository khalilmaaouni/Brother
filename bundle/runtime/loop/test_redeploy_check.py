#!/usr/bin/env python3
"""scripts/loop/redeploy.sh, tested at its entry point (M40.a, 2026-10-16).

WHY. On 10-14 every deploy and launch refusal arrived alone, after the loop was stopped, and nine hand written
redeploy scripts piled up. redeploy.sh --check asks every refusal while the loop runs and prints ALL of them; the
default mode refuses before any drain when the check fails; --plan-only refuses a tool or a deployed config by name.

HOW. The script is copied into a throwaway tools folder beside the REAL power_rule.py, loop_canary.py and
tool_stamp.py (cheap, pure) and STUBS for the expensive or state changing ones: stop_loop.sh (records every call),
loop_intake.py (seat_gate, a paid call in life), deploy_stamped.py (stage_candidate; CONFIGS and STAMP copied from the
real module at test time, so the stub can never hold a different list). The plan and loop worktrees are a real clone
and a real git worktree over a bare upstream. HOME, the scratch root and the evidence folder are temp folders; the
deploy and the launcher are replaced through REDEPLOY_DEPLOY and REDEPLOY_START.

ONE FIXTURE PER GUARD: each FAIL below is reached by a fixture only that check can refuse, and the all PASS fixture
proves every other check answers PASS on the same tree.

Python 3.9 and 3.13, standard library only, no network.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import deploy_stamped  # noqa: E402  the real CONFIGS and STAMP, handed to the stub

DATED = "2099-01-01 %02d:%02d" % (9, 0)   # a far deadline: on battery the power rule refuses it

STUB_STOP = '''#!/bin/bash
echo "$*" >> "$TEST_LOG/stop-calls.txt"
case "$1" in
  --dry) echo "stub live: ${TEST_LIVE_RC:-1}"; exit "${TEST_LIVE_RC:-1}";;
  --drain) echo "DRAINED: stub"; exit "${TEST_DRAIN_RC:-0}";;
  --resume) echo "RESUMED: stub"; exit 0;;
  *) echo "STOPPED: stub"; exit "${TEST_STOP_RC:-0}";;
esac
'''

STUB_INTAKE = '''import json, os
def seat_gate(proofs, probe=None, native=None):
    with open(os.path.join(os.environ["TEST_LOG"], "seat.json"), "w") as fh:
        json.dump({"scratch": os.environ.get("BROTHER_SCRATCH"), "program": proofs[0]["program"], "cap_usd": proofs[0].get("cap_usd")}, fh)
    p = dict(proofs[0])
    if os.environ.get("TEST_SEAT") == "raise":
        raise OSError("the seat could not be prepared")
    if os.environ.get("TEST_SEAT") == "silent":
        os._exit(0)
    if os.environ.get("TEST_SEAT") == "native-off":
        return [p]
    if os.environ.get("TEST_SEAT") == "fail":
        p.update(status="FAIL", cause="the plain call answered but a sandboxed native seat did not (401)", remedy="refresh the login")
    else:
        p.update(cause=p["cause"] + "; a sandboxed native seat answered")
    return [p]
'''

STUB_DEPLOY = '''import os
CONFIGS = %r
STAMP = %r
def stage_candidate(source_dir, dest):
    with open(os.path.join(os.environ["TEST_LOG"], "stage.txt"), "w") as fh:
        fh.write(source_dir + "\\n" + dest)
    if os.environ.get("TEST_STAGE") == "fail":
        raise RuntimeError("dynamic loader requires a resolved import trace: x.py")
    os.makedirs(dest)
    return ["plugin/runtime/x.py"]
''' % (deploy_stamped.CONFIGS, deploy_stamped.STAMP)

STUB_PARITY = '''import os, sys
log = os.environ["TEST_LOG"]
target = os.environ.get("BROTHER_DEPLOY_TARGET", "")
with open(os.path.join(log, "parity.json"), "w") as fh:
    import json
    json.dump({"target": target, "files": sorted(os.listdir(target)) if os.path.isdir(target) else None}, fh)
if os.environ.get("TEST_PARITY") == "fail":
    print("FAIL drift    tool.py                      the executed copy differs from the versioned one")
    sys.exit(1)
print("PASS: stub parity")
'''

STUB_DOCTOR = '''import json, os, sys
NAMES = ["security-policy", "code-owners", "evidence-obligation", "obligation-wired", "signed-receipts",
         "signing-identities", "pinned-ci", "ci-permissions", "os-sandbox", "egress-policy",
         "delegated-identity", "central-audit-store", "fail-closed-mode"]
mode = os.environ.get("TEST_DOCTOR", "pass")
if mode == "refuse":
    print("not certified", file=sys.stderr)
    sys.exit(2)
controls = [{"control": name, "status": "PRESENT", "decided_by": "stub doctor"} for name in NAMES]
if mode == "no_data":
    controls[0]["status"] = "NO-DATA"
elif mode == "duplicate":
    controls[1]["control"] = controls[0]["control"]
elif mode == "unknown":
    controls[-1]["control"] = "not-a-real-control"
elif mode == "blocked":
    controls[0]["status"] = "BLOCKED"
elif mode == "short":
    controls = controls[:12]
print(json.dumps({"controls": controls, "counts": {"present": len(controls)}}))
'''


def _write(path, body, executable=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(body)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class RedeployFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="test-redeploy-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.log = os.path.join(self.tmp, "log")
        self.evd = os.path.join(self.tmp, "evidence")
        for d in (self.home, self.log, self.evd, os.path.join(self.home, ".claude", "bin")):
            os.makedirs(d)
        _write(os.path.join(self.home, ".claude", "bin", "installed.py"), "# the deployed bin\n")
        self.tools = os.path.join(self.tmp, "tools")
        os.makedirs(self.tools)
        for name in ("redeploy.sh", "power_rule.py", "loop_canary.py", "tool_stamp.py"):
            shutil.copy2(os.path.join(HERE, name), os.path.join(self.tools, name))
        self.script = os.path.join(self.tools, "redeploy.sh")
        _write(os.path.join(self.tmp, "enterprise_doctor.py"), STUB_DOCTOR)
        _write(os.path.join(self.tools, "stop_loop.sh"), STUB_STOP, executable=True)
        _write(os.path.join(self.tools, "loop_intake.py"), STUB_INTAKE)
        _write(os.path.join(self.tools, "deploy_stamped.py"), STUB_DEPLOY)
        self.fakebin = os.path.join(self.tmp, "fakebin")
        _write(self.fakebin, "#!/bin/sh\nexit 0\n", executable=True)
        # The doctor reads `uname -s`; a host that is not Darwin reads NO-DATA. The suite pins the certified host in
        # its own fake bin so it never depends on the machine it runs on, and one test overrides PATH with Linux.
        self.ubin = os.path.join(self.tmp, "ubin")
        _write(os.path.join(self.ubin, "uname"), "#!/bin/sh\necho Darwin\n", executable=True)
        self.build = os.path.join(self.tmp, "build.json")
        _write(self.build, "{}\n")
        # the repository: a bare upstream, the plan worktree a clone of it, the loop worktree a git worktree of that clone
        self.bare = os.path.join(self.tmp, "upstream.git")
        seed = os.path.join(self.tmp, "seed")
        self.git(self.tmp, "init", "-q", "--bare", self.bare)
        self.git(self.tmp, "init", "-q", seed)
        self.git(seed, "checkout", "-q", "-b", "plan/x")
        for rel, body in (("docs/plan/notes.md", "notes\n"), ("scripts/loop/tool.py", "print('tool')\n"),
                          ("plugin/runtime/x.py", "X = 1\n"), ("scripts/test_loop_tool_parity.py", STUB_PARITY),
                          ("scripts/test_bl_owns_complete.py", "print('owns ok')\n"),
                          ("docs/plan/loop-roles.json", "{}\n")):
            _write(os.path.join(seed, rel), body)
        self.git(seed, "add", "-A")
        self.git(seed, "commit", "-q", "-m", "seed")
        self.base = self.out(seed, "rev-parse", "HEAD")
        self.write_canary(seed, base=self.base, build=self.build)
        self.git(seed, "add", "-A")
        self.git(seed, "commit", "-q", "-m", "canary spec")
        self.git(seed, "branch", "loop/x")
        self.git(seed, "remote", "add", "hub", self.bare)
        self.git(seed, "push", "-q", "hub", "plan/x", "loop/x")
        self.pw = os.path.join(self.tmp, "plan-wt")
        self.lw = os.path.join(self.tmp, "loop-wt")
        self.git(self.tmp, "clone", "-q", "-o", "hub", "-b", "plan/x", self.bare, self.pw)
        self.git(self.pw, "branch", "-q", "--track", "loop/x", "hub/loop/x")
        self.git(self.pw, "worktree", "add", "-q", self.lw, "loop/x")
        # the loop line landed work the plan does not hold yet, the usual state before a redeploy
        self.commit(self.lw, "scripts/loop/tool.py", "print('landed')\n", "landed", push="loop/x")

    # git helpers ---------------------------------------------------------------------------------------------------
    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "BROTHER_", "START_RUN_", "TEST_"))}
        env.update(HOME=self.home, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.path.join(self.tmp, "gitconfig"),
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@example.invalid", START_RUN_EVIDENCE=self.evd, TEST_LOG=self.log,
                   BROTHER_CLAUDE_BIN=self.fakebin, BROTHER_CODEX_BIN=self.fakebin, BROTHER_POWER_SOURCE="AC Power",
                   PATH=self.ubin + os.pathsep + os.environ.get("PATH", ""))
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        return env

    def git(self, cwd, *args):
        subprocess.run(["git"] + list(args), cwd=cwd, env=self.env(), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def out(self, cwd, *args):
        return subprocess.run(["git"] + list(args), cwd=cwd, env=self.env(), check=True, stdout=subprocess.PIPE,
                              universal_newlines=True).stdout.strip()

    def commit(self, cwd, rel, body, msg, push=None):
        _write(os.path.join(cwd, rel), body)
        self.git(cwd, "add", "-A")
        self.git(cwd, "commit", "-q", "-m", msg)
        if push:
            self.git(cwd, "push", "-q", "hub", push)

    def write_canary(self, root, **over):
        spec = {"sub": "D1.a", "base_commit": over.get("base"), "build": over.get("build")}
        _write(os.path.join(root, "docs", "plan", "loop-canary.json"), json.dumps(spec) + "\n")

    def upstream(self, branch):
        return self.out(self.bare, "rev-parse", branch)

    # running the script --------------------------------------------------------------------------------------------
    def run_redeploy(self, *more, mode="--check", **extra):
        args = ["bash", self.script] + ([mode] if mode else []) + [
            "--plan-wt", self.pw, "--loop-wt", self.lw, "--plan-branch", "plan/x", "--loop-branch", "loop/x"] + list(more)
        p = subprocess.run(args, cwd=self.tmp, env=self.env(**extra), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=180)
        return p.returncode, p.stdout

    def check(self, *more, cap="5", **extra) -> tuple:
        capped = ["--probe-cap-usd", cap] if cap is not None else []
        return self.run_redeploy("--deadline", DATED, *(capped + list(more)), **extra)

    def stop_calls(self):
        try:
            with open(os.path.join(self.log, "stop-calls.txt")) as fh:
                return fh.read().splitlines()
        except OSError:
            return []

    def rows(self, out):
        """{check name: (state, detail)} from the verdict lines."""
        got = {}
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0] in ("PASS", "FAIL", "NO-DATA"):
                got[parts[1]] = (parts[0], parts[2])
        return got

    def scratch_left(self):
        root = os.path.join(self.home, ".claude", "brother-scratch")
        return [n for n in os.listdir(root) if n.startswith("redeploy-")] if os.path.isdir(root) else []


CHECKS = ["power", "controls", "live", "plan", "loop", "merge", "pins", "seat", "stage", "parity", "canary", "doctor"]


class CheckMode(RedeployFixture):

    def assertOnlyDry(self):
        """--check stops nothing: the stop tool is asked only its read only --dry question."""
        self.assertEqual(set(self.stop_calls()), {"--dry"}, self.stop_calls())

    def test_all_pass_writes_nothing_outside_its_scratch(self):
        # both lines moved, so the merge makes a tree the repository does not hold: its objects must land in the scratch
        self.commit(self.pw, "docs/plan/specs/M1.md", "spec\n", "a spec", push="plan/x")
        heads = {b: self.upstream(b) for b in ("plan/x", "loop/x")}
        local = (self.out(self.pw, "rev-parse", "HEAD"), self.out(self.lw, "rev-parse", "HEAD"))
        objects = self.out(self.pw, "count-objects")
        rc, out = self.check()
        self.assertEqual(rc, 0, out)
        rows = self.rows(out)
        self.assertEqual([n for n in CHECKS if rows.get(n, ("",))[0] == "PASS"], CHECKS, out)
        self.assertIn("CHECK PASS: 12 PASS, 0 FAIL, 0 NO-DATA", out)
        self.assertOnlyDry()
        # nothing pushed, nothing merged, no object written into the repository, the scratch removed
        self.assertEqual({b: self.upstream(b) for b in heads}, heads)
        self.assertEqual((self.out(self.pw, "rev-parse", "HEAD"), self.out(self.lw, "rev-parse", "HEAD")), local)
        self.assertEqual(self.out(self.pw, "count-objects"), objects)
        self.assertEqual(self.scratch_left(), [])
        # the seat probe's folder and the parity target were inside the scratch, the target built the deploy's way
        with open(os.path.join(self.log, "seat.json")) as fh:
            seat = json.load(fh)
        with open(os.path.join(self.log, "parity.json")) as fh:
            parity = json.load(fh)
        scratch_root = os.path.join(self.home, ".claude", "brother-scratch", "redeploy-")
        self.assertTrue(seat["scratch"].startswith(scratch_root), seat)
        self.assertEqual(seat["program"], self.fakebin)
        self.assertTrue(parity["target"].startswith(scratch_root), parity)
        self.assertEqual(parity["files"], ["candidate", "installed.py", "tool.py"])

    def test_two_failures_are_both_printed_and_every_later_check_still_runs(self):
        rc, out = self.check(BROTHER_POWER_SOURCE="Battery Power", TEST_SEAT="fail")
        self.assertEqual(rc, 1, out)
        rows = self.rows(out)
        self.assertEqual(rows["power"][0], "FAIL", out)
        self.assertTrue(rows["power"][1].startswith("plug in the charger first: the machine draws from battery"), out)
        self.assertEqual(rows["seat"][0], "FAIL", out)
        self.assertIn("remedy: refresh the login", rows["seat"][1])
        self.assertEqual([n for n in CHECKS if n in rows], CHECKS, out)   # the first FAIL stopped nothing after it
        self.assertEqual(rows["parity"][0], "PASS", out)
        self.assertIn("CHECK FAIL: 10 PASS, 2 FAIL, 0 NO-DATA", out)
        self.assertOnlyDry()
        self.assertEqual(self.scratch_left(), [])

    def test_a_check_that_cannot_answer_is_no_data_exit_2_and_a_fail_outranks_it(self):
        rc, out = self.check(TEST_LIVE_RC="2")
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.rows(out)["live"][0], "NO-DATA", out)
        self.assertIn("CHECK NO-DATA: 11 PASS, 0 FAIL, 1 NO-DATA", out)
        rc, out = self.check(TEST_LIVE_RC="2", TEST_PARITY="fail")
        self.assertEqual(rc, 1, out)
        self.assertIn("CHECK FAIL: 10 PASS, 1 FAIL, 1 NO-DATA", out)

    def test_a_check_that_raises_or_prints_nothing_is_no_data_never_pass(self):
        for mode, why in (("raise", "the seat gate could not run (OSError"), ("silent", "the check printed no verdict")):
            with self.subTest(mode=mode):
                rc, out = self.check(TEST_SEAT=mode)
                self.assertEqual(rc, 2, out)
                self.assertEqual(self.rows(out)["seat"][0], "NO-DATA", out)
                self.assertIn(why, self.rows(out)["seat"][1])

    def test_no_native_seat_is_said_plainly(self):
        rc, out = self.check(TEST_SEAT="native-off")
        self.assertEqual(rc, 0, out)
        self.assertIn("no native seat will run", self.rows(out)["seat"][1])

    def test_no_loop_running_passes_too(self):
        rc, out = self.check(TEST_LIVE_RC="0")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.rows(out)["live"], ("PASS", "no loop is running"))

    # one fixture per guard: only that check can refuse it --------------------------------------------------------
    def only_fail(self, name, why, **extra):
        rc, out = self.check(**extra)
        self.assertEqual(rc, 1, out)
        rows = self.rows(out)
        self.assertEqual(sorted(n for n, (s, _) in rows.items() if s != "PASS"), [name], out)
        self.assertIn(why, rows[name][1])
        self.assertOnlyDry()
        return out

    def test_a_far_deadline_on_battery_fails_power(self):
        self.only_fail("power", "plug in the charger first", BROTHER_POWER_SOURCE="Battery Power")

    def test_a_hold_fails_controls(self):
        _write(os.path.join(self.evd, "LOOP-HOLD.txt"), "held by the owner\n")
        self.only_fail("controls", "a HOLD is in force (held by the owner)")

    def test_a_pause_fails_controls(self):
        _write(os.path.join(self.evd, "LOOP-PAUSE.txt"), "paused by hand\n")
        self.only_fail("controls", "a PAUSE is in force (paused by hand)")

    def test_a_dirty_plan_worktree_fails_plan(self):
        _write(os.path.join(self.pw, "docs", "plan", "notes.md"), "edited, not committed\n")
        self.only_fail("plan", "1 changed path(s) in %s" % os.path.realpath(self.pw))

    def test_a_loop_line_behind_its_upstream_fails_loop(self):
        other = os.path.join(self.tmp, "other")
        self.git(self.tmp, "clone", "-q", "-o", "hub", "-b", "loop/x", self.bare, other)
        self.commit(other, "docs/plan/elsewhere.md", "x\n", "pushed from elsewhere", push="loop/x")
        self.only_fail("loop", "hub/loop/x has commits")

    def test_a_branch_mismatch_fails_plan(self):
        self.git(self.pw, "checkout", "-q", "-b", "side")
        self.only_fail("plan", "is on 'side', not plan/x")

    def test_a_conflict_fails_merge(self):
        self.commit(self.pw, "scripts/loop/tool.py", "print('plan side')\n", "plan edit", push="plan/x")
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        rows = self.rows(out)
        self.assertEqual(rows["merge"][0], "FAIL", out)
        self.assertIn("scripts/loop/tool.py", rows["merge"][1])
        for n in ("stage", "parity", "canary"):   # nothing to stage without a merged tree: unknown, never PASS
            self.assertEqual(rows[n][0], "NO-DATA", out)
        self.assertOnlyDry()

    def test_a_pin_that_is_not_a_program_fails_pins(self):
        out = self.only_fail("pins", "BROTHER_CODEX_BIN resolves to", BROTHER_CODEX_BIN=os.path.join(self.tmp, "absent"))
        self.assertIn("set BROTHER_CODEX_BIN=", out)

    def test_a_seat_that_does_not_answer_fails_seat(self):
        self.only_fail("seat", "a sandboxed native seat did not", TEST_SEAT="fail")

    def test_a_candidate_the_deploy_refuses_fails_stage(self):
        self.only_fail("stage", "dynamic loader requires a resolved import trace", TEST_STAGE="fail")

    def test_a_parity_refusal_fails_parity(self):
        self.only_fail("parity", "FAIL drift    tool.py", TEST_PARITY="fail")

    def test_a_missing_fixture_build_fails_canary(self):
        os.remove(self.build)
        self.only_fail("canary", "build file missing: D1.a")

    def test_an_unknown_fixture_base_commit_fails_canary(self):
        self.write_canary(self.pw, base="0" * 40, build=self.build)
        self.git(self.pw, "commit", "-q", "-am", "canary at an unknown commit")
        self.git(self.pw, "push", "-q", "hub", "plan/x")
        self.only_fail("canary", "base commit not in")

    def test_an_unreadable_canary_spec_fails_canary(self):
        _write(os.path.join(self.pw, "docs", "plan", "loop-canary.json"), "{not json\n")
        self.git(self.pw, "commit", "-q", "-am", "a corrupt canary spec")
        self.git(self.pw, "push", "-q", "hub", "plan/x")
        self.only_fail("canary", "no readable canary spec")

    def test_a_live_probe_without_a_cap_reads_no_data(self) -> None:
        rc, out = self.check(cap=None)
        state, detail = self.rows(out)["seat"]
        self.assertEqual(state, "NO-DATA", out)
        self.assertIn("no cap", detail)
        self.assertFalse(os.path.exists(os.path.join(self.log, "seat.json")), "the seat gate ran with no cap: " + out)
        self.assertIn("CHECK NO-DATA: 11 PASS, 0 FAIL, 1 NO-DATA", out)

    def test_a_live_probe_with_a_positive_cap_passes_the_cap(self) -> None:
        rc, out = self.check(cap="5")
        self.assertEqual(self.rows(out)["seat"][0], "PASS", out)
        with open(os.path.join(self.log, "seat.json")) as fh:
            self.assertEqual(json.load(fh)["cap_usd"], "5", out)

    def test_a_live_probe_with_a_non_positive_cap_is_refused_before_any_check(self) -> None:
        for cap in ("0", "0.0", "-5"):
            with self.subTest(cap=cap):
                rc, out = self.check(cap=cap)
                self.assertEqual(rc, 2, out)
                self.assertIn("REDEPLOY REFUSED [args]: --probe-cap-usd must be", out)
                self.assertEqual(self.rows(out), {}, out)

    def test_a_live_probe_with_a_non_numeric_cap_is_refused_before_any_check(self) -> None:
        for cap in ("banana", "1.2.3", ".5", "5.", "1e3"):
            with self.subTest(cap=cap):
                rc, out = self.check(cap=cap)
                self.assertEqual(rc, 2, out)
                self.assertIn("REDEPLOY REFUSED [args]: --probe-cap-usd must be a positive number", out)
                self.assertEqual(self.rows(out), {}, out)

    def test_bad_arguments_refuse_before_anything(self):
        for more, why in (([], "--deadline HH:MM"), (["--deadline", DATED, "--surprise"], "unknown argument --surprise"),
                          (["--deadline", "--check"], "--deadline needs a value")):
            with self.subTest(more=more):
                rc, out = self.run_redeploy(*more)
                self.assertEqual(rc, 2, out)
                self.assertIn("REDEPLOY REFUSED [args]: " + why, out)
        self.assertEqual(self.stop_calls(), [])

    # LC3.a: the doctor check, one verdict per capability, on the certified host only.
    def _doctor_row(self, out):
        return self.rows(out)["doctor"]

    def test_a_doctor_that_gives_thirteen_named_verdicts_and_no_no_data_passes(self) -> None:
        rc, out = self.check(TEST_DOCTOR="pass")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._doctor_row(out), ("PASS", "13 capabilities, no BLOCKED, no NO-DATA"), out)

    def test_a_doctor_with_a_no_data_control_reads_no_data(self) -> None:
        rc, out = self.check(TEST_DOCTOR="no_data")
        self.assertEqual(rc, 2, out)
        state, detail = self._doctor_row(out)
        self.assertEqual(state, "NO-DATA", out)
        self.assertIn("1 control(s) read NO-DATA", detail)

    def test_a_doctor_with_a_duplicate_control_name_fails(self) -> None:
        rc, out = self.check(TEST_DOCTOR="duplicate")
        self.assertEqual(rc, 1, out)
        state, detail = self._doctor_row(out)
        self.assertEqual(state, "FAIL", out)
        self.assertIn("duplicate control names", detail)

    def test_a_doctor_with_an_unknown_control_name_fails(self) -> None:
        rc, out = self.check(TEST_DOCTOR="unknown")
        self.assertEqual(rc, 1, out)
        state, detail = self._doctor_row(out)
        self.assertEqual(state, "FAIL", out)
        self.assertIn("control names not in the expected set", detail)

    def test_a_doctor_with_a_blocked_control_fails(self) -> None:
        rc, out = self.check(TEST_DOCTOR="blocked")
        self.assertEqual(rc, 1, out)
        state, detail = self._doctor_row(out)
        self.assertEqual(state, "FAIL", out)
        self.assertIn("reads a status outside PRESENT, ABSENT, NO-DATA", detail)

    def test_a_doctor_with_fewer_than_thirteen_verdicts_fails(self) -> None:
        rc, out = self.check(TEST_DOCTOR="short")
        self.assertEqual(rc, 1, out)
        state, detail = self._doctor_row(out)
        self.assertEqual(state, "FAIL", out)
        self.assertIn("gave 12 verdicts, expected 13", detail)

    def test_an_unsupported_host_reads_no_data_not_certified(self) -> None:
        linuxbin = os.path.join(self.tmp, "linuxbin")
        _write(os.path.join(linuxbin, "uname"), "#!/bin/sh\necho Linux\n", executable=True)
        rc, out = self.check(PATH=linuxbin + os.pathsep + os.environ.get("PATH", ""))
        self.assertEqual(rc, 2, out)
        state, detail = self._doctor_row(out)
        self.assertEqual(state, "NO-DATA", out)
        self.assertIn("not certified", detail)
        self.assertIn("Linux", detail)

    def test_a_doctor_that_refuses_for_another_reason_fails(self) -> None:
        rc, out = self.check(TEST_DOCTOR="refuse")
        self.assertEqual(rc, 1, out)
        state, detail = self._doctor_row(out)
        self.assertEqual(state, "FAIL", out)
        self.assertIn("the doctor refused (exit 2)", detail)
        self.assertIn("not certified", detail)


STUB_DEPLOY_SH = '''#!/bin/bash
echo "deploy ran in $(pwd)" >> "$TEST_LOG/deploy.txt"
mkdir -p "$HOME/.claude/bin"
printf '{"revision": "%s"}\\n' "${TEST_STAMP_REV:-$(git rev-parse HEAD)}" > "$HOME/.claude/bin/.deploy-stamp.json"
echo "PASS: stub deploy"
'''

STUB_START_SH = '''#!/bin/bash
echo "$*" > "$TEST_LOG/start-argv.txt"
echo "STARTED: stub"
'''


class DefaultMode(RedeployFixture):

    def launch(self, **extra):
        deploy = os.path.join(self.tmp, "stub_deploy.sh")
        start = os.path.join(self.tmp, "stub_start.sh")
        _write(deploy, STUB_DEPLOY_SH, executable=True)
        _write(start, STUB_START_SH, executable=True)
        return self.run_redeploy("--deadline", DATED, "--plan", "docs/plan/P.json", "--scope", "^(A1)$",
                                 "--budget-usd", "5", "--probe-cap-usd", "5", "--reason", "redeploy test", mode=None,
                                 REDEPLOY_DEPLOY=deploy, REDEPLOY_START=start, **extra)

    def test_a_failed_check_refuses_before_any_drain(self):
        heads = {b: self.upstream(b) for b in ("plan/x", "loop/x")}
        rc, out = self.launch(BROTHER_POWER_SOURCE="Battery Power")
        self.assertEqual(rc, 2, out)
        self.assertIn("REDEPLOY REFUSED [check]: the check above found 1 FAIL and 0 NO-DATA", out)
        self.assertIn("plug in the charger first", out)
        self.assertEqual(self.stop_calls(), ["--dry"])   # never --drain, never a stop
        self.assertEqual({b: self.upstream(b) for b in heads}, heads)
        self.assertFalse(os.path.exists(os.path.join(self.log, "deploy.txt")))

    def test_a_no_data_check_refuses_too(self):
        rc, out = self.launch(TEST_LIVE_RC="5")
        self.assertEqual(rc, 2, out)
        self.assertIn("REDEPLOY REFUSED [check]: the check above found 0 FAIL and 1 NO-DATA", out)
        self.assertEqual(self.stop_calls(), ["--dry"])

    def test_the_whole_ceremony_in_order_with_the_stamp_read_back(self):
        rc, out = self.launch()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.stop_calls(), ["--dry", "--drain 3600 --reason redeploy test", "--reason redeploy test", "--resume"])
        plan, loop = self.upstream("plan/x"), self.upstream("loop/x")
        self.assertEqual(plan, loop, out)   # the loop line was fast forwarded to the plan and both were pushed
        self.assertEqual(self.out(self.bare, "log", "-1", "--format=%s", plan), "merge loop/x before redeploy")
        self.assertIn("OK   stamp  the deployed tools are %s, the loop line's HEAD" % loop, out)
        with open(os.path.join(self.log, "start-argv.txt")) as fh:
            argv = fh.read().split()
        self.assertEqual(argv[:2], ["--plan", "docs/plan/P.json"])
        self.assertIn("^(A1)$", argv)
        self.assertTrue(out.rstrip().endswith("REDEPLOY DONE: %s deployed and launched" % loop), out)

    def test_a_stamp_that_names_another_revision_refuses_before_the_launch(self):
        rc, out = self.launch(TEST_STAMP_REV="f" * 40)
        self.assertEqual(rc, 2, out)
        self.assertIn("REDEPLOY REFUSED [stamp]: the deployed stamp names %s" % ("f" * 40), out)
        self.assertFalse(os.path.exists(os.path.join(self.log, "start-argv.txt")))

    def test_an_incomplete_drain_resumes_and_stops_nothing(self):
        rc, out = self.launch(TEST_DRAIN_RC="1")
        self.assertEqual(rc, 2, out)
        self.assertIn("REDEPLOY REFUSED [drain]", out)
        self.assertEqual(self.stop_calls(), ["--dry", "--drain 3600 --reason redeploy test", "--resume"])


STUB_START_ARGV = '''#!/bin/bash
printf '%s\\n' "$@" > "$TEST_LOG/start-argv.txt"
echo "STARTED: stub"
'''

# M40.c: the launcher's role flags, copied from start_run.sh's own option list
ROLE_FLAGS = ("--worker", "--checker", "--finisher", "--documenter", "--adversary", "--gap", "--lift-hold")


class RoleFlags(RedeployFixture):
    """M40.c (2026-10-16): the launcher's role flags and --lift-hold pass through the one ceremony to start_run.sh."""

    BASE_ARGV = ["--plan", "docs/plan/P.json", "--deadline", DATED, "--budget-usd", "5", "--scope", "^(A1)$"]

    def launch(self, *roles, **extra):
        deploy = os.path.join(self.tmp, "stub_deploy.sh")
        start = os.path.join(self.tmp, "stub_start_argv.sh")
        _write(deploy, STUB_DEPLOY_SH, executable=True)
        _write(start, STUB_START_ARGV, executable=True)
        return self.run_redeploy("--deadline", DATED, "--plan", "docs/plan/P.json", "--scope", "^(A1)$",
                                 "--budget-usd", "5", "--probe-cap-usd", "5", *roles, mode=None, REDEPLOY_DEPLOY=deploy, REDEPLOY_START=start,
                                 **extra)

    def start_argv(self):
        with open(os.path.join(self.log, "start-argv.txt")) as fh:
            return fh.read().split("\n")[:-1]

    def test_every_given_role_flag_reaches_the_launcher_verbatim_and_in_order(self):
        roles = ["--lift-hold", "the owner said go at 03h40, roles on Claude only", "--worker", "opus55",
                 "--gap", "300", "--adversary", "sonnet", "--documenter", "luna", "--finisher", "opus55",
                 "--checker", " sonnet "]
        rc, out = self.launch(*roles)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.start_argv(), self.BASE_ARGV + roles, out)
        self.assertEqual(sorted(roles[0::2]), sorted(ROLE_FLAGS))

    def test_an_omitted_role_flag_never_reaches_the_launcher(self):
        rc, out = self.launch()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.start_argv(), self.BASE_ARGV, out)
        rc, out = self.launch("--checker", "sonnet")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.start_argv(), self.BASE_ARGV + ["--checker", "sonnet"], out)

    def test_a_role_flag_without_a_value_is_refused_before_any_drain(self):
        for flag in ROLE_FLAGS:
            for value in ("", "   ", "--dry"):
                with self.subTest(flag=flag, value=value):
                    rc, out = self.launch(flag, value)
                    self.assertEqual(rc, 2, out)
                    self.assertIn("REDEPLOY REFUSED [args]: %s needs a value, not '%s'" % (flag, value), out)
                    self.assertEqual(self.stop_calls(), [], out)   # not even the check's --dry: nothing was asked
                    self.assertFalse(os.path.exists(os.path.join(self.log, "start-argv.txt")))
        rc, out = self.launch("--worker")
        self.assertEqual(rc, 2, out)
        self.assertIn("REDEPLOY REFUSED [args]: --worker needs a value", out)
        self.assertEqual(self.stop_calls(), [], out)

    def test_check_takes_the_role_flags_stops_nothing_and_names_them(self):
        rc, out = self.run_redeploy("--deadline", DATED, "--probe-cap-usd", "5", "--worker", "opus55", "--lift-hold", "the owner said go",
                                    "--gap", "300", REDEPLOY_START=os.path.join(self.tmp, "absent_start.sh"))
        self.assertEqual(rc, 0, out)
        self.assertIn("ROLES   a launch would pass to start_run.sh: --worker 'opus55' --lift-hold 'the owner said go' "
                      "--gap '300'\n", out)
        self.assertIn("CHECK PASS: 12 PASS, 0 FAIL, 0 NO-DATA", out)   # the roles line is no verdict
        self.assertEqual(set(self.stop_calls()), {"--dry"}, self.stop_calls())

    def test_check_without_role_flags_names_the_launchers_own_defaults(self):
        rc, out = self.check()
        self.assertEqual(rc, 0, out)
        self.assertIn("ROLES   none given: a launch uses start_run.sh's own defaults\n", out)


class PlanOnly(RedeployFixture):

    def plan_only(self, **extra):
        return self.run_redeploy("--reason", "spec fix", mode="--plan-only", **extra)

    def assertNothingPaused(self):
        self.assertEqual(self.stop_calls(), [])

    def test_a_tool_path_is_refused_by_name_before_any_pause(self):
        self.commit(self.pw, "scripts/loop/other.py", "x = 1\n", "a tool", push="plan/x")
        self.commit(self.pw, "docs/plan/specs/M1.md", "spec\n", "a spec", push="plan/x")
        loop = self.upstream("loop/x")
        rc, out = self.plan_only()
        self.assertEqual(rc, 2, out)
        self.assertIn("REFUSED  scripts/loop/other.py: not a plan, spec or notes document", out)
        self.assertNotIn("REFUSED  docs/plan/specs/M1.md", out)
        self.assertIn("REDEPLOY REFUSED [plan-only]", out)
        self.assertNothingPaused()
        self.assertEqual(self.upstream("loop/x"), loop)

    def test_every_deployed_config_is_refused_by_name(self):
        for name in deploy_stamped.CONFIGS:
            self.commit(self.pw, "docs/plan/" + name, '{"changed": true}\n', "config " + name)
        self.git(self.pw, "push", "-q", "hub", "plan/x")
        rc, out = self.plan_only()
        self.assertEqual(rc, 2, out)
        for name in deploy_stamped.CONFIGS:
            self.assertIn("REFUSED  docs/plan/%s: the deploy ships it" % name, out)
        self.assertNothingPaused()

    def test_a_spec_only_change_merges_and_pushes_without_a_stop(self):
        self.commit(self.pw, "docs/plan/specs/M1.md", "spec\n", "a spec", push="plan/x")
        rc, out = self.plan_only()
        self.assertEqual(rc, 0, out)
        self.assertIn("PLAN-ONLY 1 document path(s): docs/plan/specs/M1.md", out)
        self.assertEqual(self.stop_calls(), ["--drain 3600 --reason spec fix (plan only)", "--resume"])
        plan, loop = self.upstream("plan/x"), self.upstream("loop/x")
        self.assertEqual(plan, loop)
        self.assertEqual(self.out(self.lw, "rev-parse", "HEAD"), loop)
        self.assertTrue(os.path.isfile(os.path.join(self.lw, "docs", "plan", "specs", "M1.md")))
        self.assertIn("never stopped", out)

    def test_nothing_to_merge_is_said_and_pauses_nothing(self):
        self.git(self.pw, "merge", "-q", "--ff-only", "hub/loop/x")
        self.git(self.pw, "push", "-q", "hub", "plan/x")
        rc, out = self.plan_only()
        self.assertEqual(rc, 0, out)
        self.assertIn("PLAN-ONLY nothing to merge", out)
        self.assertNothingPaused()


if __name__ == "__main__":
    unittest.main()
