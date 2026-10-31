#!/usr/bin/env python3
"""CY1.b: a real cut takes its approval as one line from the owner's phone
code, bound to HEAD, the version and the pinned key id (docs/plan/specs/CY1.b.md).

The owner approves from the Claude app or a browser: the session asks him for
the six digit code, `brother-approve verify VERSION SHA CODE` turns it into
one line `cy1.approval.1 VERSION SHA STEP KEYID HMAC`, and `cut.py --approval
LINE` accepts that line only when `brother-approve check` (the line on stdin)
exits 0, the version is the cut's, the sha is HEAD, the step is under 5
minutes old, and the key id and the verifier file's sha256 are the ones pinned
in hub/main's docs/plan/APPROVER-KEY.json, the tree's copy equal to it
(CY1.c). The terminal y path is unchanged.

HOW. Every case drives the REAL entry points: cut.main (through the CV1.e
seam harness of scripts/test_cv1_cut_rehearsed.py, a scripted runner that
never reaches the network and records the dry push seam
`export_public.py --push` as one more label), cut.run_chain,
cut.approval_refusal, cut.approver_pin, cut.parse_pin and cut_driver.cut_phase. The
verifier is a fake executable written into a throwaway folder: it accepts the
lines listed in its own file, each ONCE (the real verifier's replay rule), and
refuses anything else. hub/main's copy of the pin is the scripted answer to
`git show hub/main:docs/plan/APPROVER-KEY.json`.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import cut_driver as D  # noqa: E402
import cut_rehearsal as CR  # noqa: E402
import test_cv1_cut_rehearsed as T  # noqa: E402

RV = T.RV
HEAD = T.SEAM_HEAD
KEY = "0123456789abcdef"
HMAC = "f" * 64
SHOW = "git show hub/main:docs/plan/APPROVER-KEY.json"
STEP = None   # the current 30 s step, frozen by Base.setUp so one case sees one line


def line(version=RV, sha=HEAD, step=None, key=KEY, hmac=HMAC,
         schema="cy1.approval.1"):
    return " ".join([schema, version, sha, step or STEP, key, hmac])


FAKE_VERIFIER = r'''#!%s
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
given = sys.stdin.read()
with open(os.path.join(here, "calls"), "a") as fh:
    fh.write(repr((sys.argv[1:], given)) + "\n")
if sys.argv[1:] != ["check"]:
    sys.exit(2)
with open(os.path.join(here, "accept")) as fh:
    accept = [l.rstrip("\n") for l in fh if l.strip()]
used_path = os.path.join(here, "used")
used = []
if os.path.exists(used_path):
    with open(used_path) as fh:
        used = [l.rstrip("\n") for l in fh]
if given not in accept or given in used:
    print("REFUSED")
    sys.exit(1)
with open(used_path, "a") as fh:
    fh.write(given + "\n")
print("OK")
'''


class Base(unittest.TestCase):
    """The CV1.e harness (scripted runner, fake fence store, throwaway
    evidence folder), borrowed by reference so none of its own tests run
    here, plus a fake verifier and a pinned key id file."""

    _cover = T.CV1eAnswerSeam._cover
    ask = T.CV1eAnswerSeam.ask
    main = T.CV1eAnswerSeam.main

    def setUp(self):
        global STEP
        STEP = str(int(time.time()) // C.APPROVAL_STEP_S)
        T.CV1eAnswerSeam.setUp(self)
        self.vdir = os.path.join(self.scratch, "verifier")
        os.makedirs(self.vdir)
        self.bin = os.path.join(self.vdir, "brother-approve")
        with open(self.bin, "w") as fh:
            fh.write(FAKE_VERIFIER % sys.executable)
        os.chmod(self.bin, os.stat(self.bin).st_mode | stat.S_IXUSR)
        self.accept([])
        self.key_path = os.path.join(self.root, "docs", "plan",
                                     "APPROVER-KEY.json")
        os.makedirs(os.path.dirname(self.key_path))
        self.hub = None
        self.pin(KEY)
        for patcher in (mock.patch.object(C, "APPROVE_BIN", self.bin),
                        mock.patch.object(D.CR, "evidence_dir",
                                          return_value=self.ev)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def accept(self, lines):
        with open(os.path.join(self.vdir, "accept"), "w") as fh:
            fh.write("".join(l + "\n" for l in lines))

    def runner(self):
        """The CV1.e seam runner, answering `git show` with hub/main's pin
        (exit 128 when hub/main has none)."""
        runner = T.SeamRunner()
        runner.answers[SHOW] = (0, self.hub) if self.hub is not None \
            else (128, "")
        return runner

    def refusal(self, l, version=RV, head=HEAD, check=lambda l: 0, **kw):
        return C.approval_refusal(l, version, head, self.root, self.runner(),
                                  check=check, **kw)

    def calls(self):
        path = os.path.join(self.vdir, "calls")
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return [l for l in fh.read().splitlines() if l]

    def pin(self, key, sha=None, hub=True):
        """The pin in the tree, and the same bytes on hub/main when hub."""
        with open(self.bin, "rb") as fh:
            sha = sha or C.hashlib.sha256(fh.read()).hexdigest()
        text = json.dumps({"key_id": key, "verifier_sha256": sha})
        with open(self.key_path, "w") as fh:
            fh.write(text)
        if hub:
            self.hub = text

    def refused(self, text):
        return isinstance(text, str) and text.startswith("NO-DATA")


class CY1b1AppApproval(Base):

    def test_no_line_no_terminal_is_refused(self):
        runner = self.runner()
        code, out = self.main([], runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("reads its one answer from a terminal", out)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.asked, [])
        # an empty line, and a line of blanks, are no line
        for empty in ("", "   "):
            runner = self.runner()
            code, out = self.main(["--approval", empty], runner, tty=False)
            self.assertEqual(code, C.EXIT_NODATA, out)
            self.assertNotIn("export_public.py --push", runner.calls)
            self.assertNotIn("bm_store claim", runner.calls)
            self.assertTrue(self.refused(self.refusal(empty)))
        self.assertEqual(self.calls(), [])

    def test_a_fabricated_line_is_refused(self):
        # well formed and bound to HEAD, the version and the pin, but the
        # verifier never issued it: refused, nothing claimed, nothing pushed
        runner = self.runner()
        code, out = self.main(["--approval", line()], runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("refused by the verifier", out)
        self.assertNotIn("export_public.py --push", runner.calls)
        self.assertNotIn("bm_store claim", runner.calls)
        self.assertEqual(self.calls(), [repr((["check"], line()))])
        # only an int 0 from the check is a yes; any other answer refuses
        for answer in (1, 2, -9, None, False, "0", 0.0, "", [0]):
            self.assertTrue(self.refused(self.refusal(
                line(), check=lambda l, a=answer: a)), answer)

        def boom(_line):
            raise OSError("no verifier")
        self.assertTrue(self.refused(self.refusal(line(), check=boom)))
        # a verifier that is missing, or does not answer in time, refuses
        with mock.patch.object(C, "APPROVE_BIN",
                               os.path.join(self.vdir, "absent")):
            self.assertTrue(self.refused(self.refusal(line(), check=None)))
        with mock.patch.object(C.subprocess, "run",
                               side_effect=C.subprocess.TimeoutExpired("x", 30)):
            self.assertTrue(self.refused(self.refusal(line(), check=None)))
        # the verifier is the account's own, whatever HOME says: a session
        # that points HOME at a folder holding a verifier of its own is not
        # asked
        import pwd
        home = pwd.getpwuid(os.getuid()).pw_dir
        fake_home = os.path.join(self.scratch, "home")
        os.makedirs(fake_home)
        env = dict(os.environ, HOME=fake_home)
        out = C.subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; sys.path.insert(0, %r); import cut; "
             "print(cut.APPROVE_BIN)" % HERE],
            env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        self.assertEqual(out.stdout.strip().splitlines()[-1], os.path.join(
            home, ".claude", "bin", "brother-approve"))

    def test_a_valid_line_is_used_once(self):
        self.accept([line()])
        runner = self.runner()
        code, out = self.main(["--approval", line()], runner, tty=False)
        self.assertEqual(code, C.EXIT_OK, out)
        self.assertEqual(runner.count("export_public.py --push"), 1, out)
        self.assertEqual(runner.count(SHOW), 1, "the pin is read from hub/main")
        self.assertEqual(self.asked, [], "the app line replaces the question")
        self.assertNotIn(HMAC, out, "the line itself is never printed")
        # the same line again: the verifier's replay rule refuses it
        runner = self.runner()
        code, out = self.main(["--approval", line()], runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("refused by the verifier", out)
        self.assertEqual(runner.count("export_public.py --push"), 0)
        # the driver passes the line through and needs no terminal with it
        seen = []

        def drive(cmd, cwd, stream=False):
            seen.append(list(cmd))
            if "ls-remote" in cmd:
                return 0, "%s\trefs/tags/v%s\n" % ("1" * 40, RV)
            return 0, "DONE: cut %s pushed: a, b\n" % RV

        verdict, detail = D.cut_phase(self.scratch, RV, HEAD, "/b.md", drive,
                                      tty=False, approval=line())
        self.assertEqual(verdict, "PASS", detail)
        self.assertEqual(seen[0], D.cut_command(self.scratch, RV, HEAD,
                                                "/b.md", line()))
        self.assertEqual(seen[0][-2:], ["--approval", line()])
        # without the line, the driver still wants the owner's terminal
        seen[:] = []
        verdict, _ = D.cut_phase(self.scratch, RV, HEAD, "/b.md", drive,
                                 tty=False)
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(seen, [])
        self.assertEqual(D.cut_command("/wt", RV, HEAD, "/b.md"),
                         D.cut_command("/wt", RV, HEAD, "/b.md", ""))
        # the phase dispatcher hands the line to the cut phase
        seen[:] = []
        # RDM1.b: the dispatcher first reads the Linux half of X01, so the worktree carries a manifest and
        # the runner answers gh with a finished run whose J1 and J2 name that manifest, every row PASS.
        manifest = b'{"fixture": "the candidate manifest"}\n'
        ident = hashlib.sha256(manifest).hexdigest()
        stamp = "-".join(["2026", "10", "29"]) + "T" + ":".join(["10", "00", "00"]) + ".1Z"
        gh_log = "".join("prove\tJourney\t%s %s\n" % (stamp, ln) for j in ("J1", "J2") for ln in (
            "=== %s ===" % j, "PASS     A.row   atom   ok", "identity candidate=%s installed=%s" % (ident, ident),
            "%s exit 0" % j))
        os.makedirs(os.path.join(self.scratch, "bundle", "runtime"))
        with open(os.path.join(self.scratch, "bundle", "runtime", "RUNTIME-" + "MANIFEST.json"), "wb") as fh:
            fh.write(manifest)

        def gated(cmd, cwd, stream=False):
            if "databaseId,status,createdAt" in " ".join(cmd):
                return 0, json.dumps([{"databaseId": 7, "status": "completed", "createdAt": "t"}])
            if cmd[1:4] == ["run", "view", "7"]:
                return 0, gh_log
            return drive(cmd, cwd, stream)

        state = {"tip": HEAD, "worktree": self.scratch}
        verdict, _, _ = D._run_phase("cut", state, self.root, RV, "/b.md",
                                     gated, False, "hub", 1,
                                     approval=line())
        self.assertEqual(verdict, "PASS")
        self.assertEqual(seen[0][-2:], ["--approval", line()])
        # run hands the line to every phase it dispatches, and never writes
        # it into the state file
        phases = []

        def phase(*a, **k):
            phases.append(k.get("approval"))
            return "FAIL", "stopped by the test", {}

        with mock.patch.object(D, "_resolve",
                               return_value=(HEAD, "b" * 40, "")), \
                mock.patch.object(D, "power_refusal", return_value=""), \
                mock.patch.object(D, "branch_invariant", return_value=""), \
                mock.patch.object(D, "_run_phase", side_effect=phase):
            D.run(self.root, RV, HEAD, "/b.md", "main",
                  runner=lambda c, w, stream=False: (0, HEAD + "\n"),
                  out=io.StringIO(), directory=self.ev, approval=line())
        self.assertEqual(phases, [line()])
        for name in os.listdir(self.ev):
            if name.startswith("driver-") and name.endswith(".json"):
                with open(os.path.join(self.ev, name)) as fh:
                    self.assertNotIn(HMAC, fh.read(), name)
        # the driver's own argument reaches the cut phase
        got = []
        with mock.patch.object(D, "run", side_effect=lambda *a, **k:
                               got.append(k.get("approval")) or 0):
            self.assertEqual(D.main(["run", "--version", RV, "--body", "/b",
                                     "--approval", line()]), 0)
        self.assertEqual(got, [line()])

    def test_binding_mismatches_are_refused(self):
        good = self.refusal(line())
        self.assertEqual(good, "")
        for bad, why in (
                (line(sha=T.MOVED_HEAD), "another sha than HEAD"),
                (line(version="1.0.9"), "another version"),
                (line(key="fedcba9876543210"), "another key id"),
                (line(schema="cy1.approval.2"), "an unknown schema"),
                (line(sha=HEAD + "a"), "a 41 character sha"),
                (line(sha=HEAD.upper()), "an upper case sha"),
                (line(hmac="z" * 64), "a non hex hmac"),
                (line(step="-1"), "a negative step"),
                (" ".join(line().split()[:5]), "five fields"),
                (line() + " extra", "seven fields"),
                (line() + "\n", "a trailing newline"),
                (line() + "\x00", "a NUL"),
                ("-" + line(), "a leading dash"),
                (line() + "\n" + line(), "two lines")):
            self.assertTrue(self.refused(self.refusal(bad)), why)
        # a HEAD that cannot be read is no HEAD
        for head in ("", None, "a" * 39, T.MOVED_HEAD + "\n"):
            self.assertTrue(self.refused(self.refusal(line(), head=head)), head)
        # through main: a line for another commit than HEAD never pushes
        self.accept([line(sha=T.MOVED_HEAD)])
        runner = self.runner()
        code, out = self.main(["--approval", line(sha=T.MOVED_HEAD)], runner,
                              tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertNotIn("export_public.py --push", runner.calls)
        # through run_chain: the chain moved HEAD after the line was checked,
        # so the commit the line approved is not the one that would be pushed
        runner = self.runner()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code, evidence, note = C.run_chain(
                self.root, RV, (None, None, "a test"), ["scripts/"], None,
                False, self.ask, runner, approved_sha=T.MOVED_HEAD)
        self.assertEqual(code, C.EXIT_REFUSED, out.getvalue())
        self.assertIn("approval", note)
        self.assertNotIn("export_public.py --push", runner.calls)
        self.assertEqual(self.asked, [])
        # the same chain with the matching sha pushes once and never asks
        runner = self.runner()
        with contextlib.redirect_stdout(io.StringIO()):
            code, evidence, note = C.run_chain(
                self.root, RV, (None, None, "a test"), ["scripts/"], None,
                False, self.ask, runner, approved_sha=HEAD)
        self.assertEqual(runner.count("export_public.py --push"), 1, note)
        self.assertEqual(self.asked, [])

    def test_unpinned_key_is_refused(self):
        sha = C._sha256_file(self.bin)
        self.assertEqual(C.parse_pin(self.hub), (KEY, sha))
        self.assertEqual(C.approver_pin(self.root, self.runner()),
                         (KEY, sha, ""))
        for name, body in (("not-enrolled", '{"key_id": "NOT-ENROLLED"}'),
                           ("corrupt", '{"key_id": '),
                           ("a list", '["%s"]' % KEY),
                           ("not text", '{"key_id": 7}'),
                           ("empty", '{"key_id": ""}'),
                           ("not hex", '{"key_id": "zzzzzzzzzzzzzzzz"}'),
                           ("no key", '{}'),
                           ("no verifier", '{"key_id": "%s"}' % KEY),
                           ("short verifier", '{"key_id": "%s", '
                            '"verifier_sha256": "%s"}' % (KEY, sha[:63]))):
            self.assertFalse(all(C.parse_pin(body)), name)
            with open(self.key_path, "w") as fh:
                fh.write(body)
            self.hub = body
            for key in (KEY, "NOT-ENROLLED", ""):
                self.assertTrue(self.refused(self.refusal(line(key=key))),
                                (name, key))
        # through main: the line is never checked and nothing is pushed
        self.accept([line()])
        runner = self.runner()
        code, out = self.main(["--approval", line()], runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("APPROVER-KEY.json", out)
        self.assertNotIn("export_public.py --push", runner.calls)
        self.assertEqual(self.calls(), [])

    def test_the_shipped_pin_is_the_placeholder_or_an_enrolled_pin(self):
        # the file this tree ships is one of two states and nothing else: the placeholder,
        # which the cut refuses, or the owner's enrolled pin (exactly key_id and
        # verifier_sha256), which the cut accepts. A failure names the state, never the
        # pinned values. docs/plan is not part of the public export, so the export tree has
        # nothing to read: a named skip there (NO-DATA for this one fact), never a pass and
        # never an error.
        shipped = os.path.join(ROOT, "docs", "plan", "APPROVER-KEY.json")
        if not os.path.exists(shipped):
            self.skipTest("docs/plan/APPROVER-KEY.json is not shipped in this tree (export tree)")
        with open(shipped, encoding="utf-8") as fh:
            self.hub = fh.read()
        try:
            data = json.loads(self.hub)
        except ValueError:
            data = None
        # the pin as the cut reads it, hub/main carrying the same bytes as this tree
        pin = C.approver_pin(ROOT, self.runner())
        if data == {"key_id": "NOT-ENROLLED"}:
            self.assertTrue(pin[:2] == ("", "") and self.refused(pin[2]),
                            "the cut accepts the NOT-ENROLLED placeholder")
            return
        self.assertTrue(isinstance(data, dict)
                        and sorted(data) == ["key_id", "verifier_sha256"],
                        "the shipped pin is neither the placeholder nor exactly "
                        "key_id and verifier_sha256 (values not printed)")
        self.assertTrue(pin == (data["key_id"], data["verifier_sha256"], ""),
                        "the cut refuses the shipped enrolled pin (values not printed)")

    def test_terminal_path_unchanged(self):
        runner = self.runner()
        code, out = self.main([], runner, tty=True)
        self.assertEqual(code, C.EXIT_OK, out)
        self.assertEqual(len(self.asked), 1)
        self.assertTrue(self.asked[0].rstrip().endswith("[y/N]"))
        self.assertEqual(runner.count("export_public.py --push"), 1)
        self.answer = "n"
        runner = self.runner()
        code, out = self.main([], runner, tty=True)
        self.assertIn("declined, nothing pushed", out)
        self.assertEqual(runner.count("export_public.py --push"), 0)
        self.assertEqual(self.calls(), [])
        # the line never combines with a dry run, --yes or a second line
        self.accept([line()])
        for argv in (["--check", "--approval", line()],
                     ["--shadow", "--approval", line()],
                     ["--yes", "--approval", line()],
                     ["--approval", line(), "--approval", line()],
                     ["--answer-file", "/x", "--approval", line()]):
            runner = self.runner()
            code, out = self.main(argv, runner, tty=True)
            self.assertEqual(code, C.EXIT_NODATA, (argv, out))
            self.assertNotIn("export_public.py --push", runner.calls, argv)
            self.assertNotIn("bm_store claim", runner.calls, argv)
        self.assertEqual(self.calls(), [])
        # an argv dash value is a parse error, never a check
        runner = self.runner()
        code, out = self.main(["--approval", "-x"], runner, tty=False)
        self.assertNotEqual(code, C.EXIT_OK)
        self.assertEqual(runner.calls, [])

    def test_codex_mirror_fresh(self):
        import codex_skills as CS
        ok, problems = CS.check()
        self.assertTrue(ok, problems)
        for rel in (("bundle", "skills"), ("bundle", "codex-skills")):
            path = os.path.join(ROOT, *(rel + ("using-brother", "SKILL.md")))
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for part in ("six digit code", "brother-approve verify",
                         "--approval", "full sha"):
                self.assertTrue(part in text, (path, part))


class CY1cHardening(Base):
    """CY1.c: the findings of the security review, each at the real entry
    point (cut.main, cut.run_chain, cut.approval_refusal), each with its
    control."""

    def test_f3_the_app_question_names_the_version_and_the_commit(self):
        runner = self.runner()
        code, out = self.main(["--resume", HEAD], runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("Approve cut v%s at commit %s?" % (RV, HEAD), out)
        self.assertIn("six digit code", out)
        self.assertEqual(runner.calls, [], "asking runs nothing")
        # without the commit it says what it needs, never a question
        code, out = self.main([], self.runner(), tty=False)
        self.assertNotIn("Approve cut", out)
        self.assertIn("needs --version X.Y.Z and --resume", out)
        # at a terminal the terminal question is asked, no app question
        code, out = self.main([], self.runner(), tty=True)
        self.assertNotIn("APP QUESTION", out)

    def test_f3_a_line_older_than_five_minutes_is_refused(self):
        start = int(STEP) * C.APPROVAL_STEP_S
        self.assertEqual(self.refusal(line(), now=start + 300), "", "the control")
        for now, why in ((start + 301, "older than 5 minutes"),
                         (start - 1, "a step not yet begun")):
            text = self.refusal(line(), now=now)
            self.assertTrue(self.refused(text), why)
        self.assertIn("older than 5 minutes", self.refusal(line(), now=start + 301))
        # through main: an eleven minute old line never reaches the verifier
        old = line(step=str(int(STEP) - 22))
        self.accept([old])
        runner = self.runner()
        code, out = self.main(["--approval", old], runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("older than 5 minutes", out)
        self.assertEqual(self.calls(), [])
        self.assertEqual(runner.count("export_public.py --push"), 0)

    def test_f3_the_receipt_states_the_residual(self):
        self.accept([line()])
        code, out = self.main(["--approval", line()], self.runner(), tty=False)
        self.assertEqual(code, C.EXIT_OK, out)
        done = [l for l in out.splitlines() if l.startswith("DONE: ")]
        self.assertEqual(len(done), 1, out)
        self.assertIn("relayed 30 second single use code", done[0])
        # the terminal path's receipt carries no app residual
        code, out = self.main([], self.runner(), tty=True)
        self.assertNotIn("single use code", out)

    def test_f4_the_line_travels_on_stdin_and_no_refusal_carries_it(self):
        self.assertEqual(self.refusal(line(), check=None), "NO-DATA: the "
                         "approval line was refused by the verifier (exit 1): "
                         "never issued, already used, or for another commit; "
                         "ask the owner for a new code")
        self.assertEqual(self.calls(), [repr((["check"], line()))],
                         "argv is the command alone; the line is stdin")

        def leaky(l):
            raise ValueError("the verifier choked on %s" % l)
        text = self.refusal(line(), check=leaky)
        self.assertTrue(self.refused(text))
        self.assertIn("ValueError", text)
        self.assertNotIn(HMAC, text)
        self.assertNotIn(line(), text)
        self.assertNotIn(HMAC, self.refusal(line(), check=lambda l: l))
        # a timeout names the type only, and counts the line as spent
        with mock.patch.object(C.subprocess, "run", side_effect=C.subprocess.
                               TimeoutExpired([self.bin, "check", line()], 30)):
            text = self.refusal(line(), check=None)
        self.assertTrue(self.refused(text))
        self.assertIn("TimeoutExpired", text)
        self.assertIn("counts as spent", text)
        self.assertNotIn(HMAC, text)
        # through main: a verifier that hangs past the deadline pushes nothing
        # and the printed refusal never carries the line
        with open(self.bin, "a") as fh:
            fh.write("import time\ntime.sleep(30)\n")
        self.pin(KEY)
        self.accept([line()])
        runner = self.runner()
        with mock.patch.object(C, "APPROVE_TIMEOUT_S", 1):
            code, out = self.main(["--approval", line()], runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("counts as spent", out)
        self.assertNotIn(HMAC, out)
        self.assertEqual(runner.count("export_public.py --push"), 0)

    def test_f5_a_replaced_verifier_is_refused(self):
        self.accept([line()])
        self.assertEqual(self.refusal(line(), check=None), "", "the control")
        with open(self.bin, "a") as fh:   # the same user rewrites the file
            fh.write("# replaced\n")
        self.accept([line(step=str(int(STEP) - 1))])
        text = self.refusal(line(step=str(int(STEP) - 1)), check=None)
        self.assertTrue(self.refused(text))
        self.assertIn("is not the build pinned on hub/main", text)
        self.assertEqual(len(self.calls()), 1, "the replaced verifier never ran")
        # through main as well, and a verifier that is gone is no identity
        runner = self.runner()
        code, out = self.main(["--approval", line(step=str(int(STEP) - 1))],
                              runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("is not the build pinned", out)
        self.assertEqual(runner.count("export_public.py --push"), 0)
        with mock.patch.object(C, "APPROVE_BIN", os.path.join(self.vdir, "x")):
            self.assertIn("unreadable", self.refusal(line(), check=lambda l: 0))

    def test_f6_the_pin_is_read_from_hub_main_only(self):
        self.accept([line()])
        # hub/main has no pin yet: NO-DATA naming the owner's one step, even
        # though the tree holds a well formed pin for the line's own key
        self.hub = None
        text = self.refusal(line())
        self.assertTrue(self.refused(text))
        self.assertIn("hub/main carries no docs/plan/APPROVER-KEY.json", text)
        self.assertIn("merges the pull request", text)
        runner = self.runner()
        code, out = self.main(["--approval", line()], runner, tty=False)
        self.assertEqual(code, C.EXIT_NODATA, out)
        self.assertIn("merges the pull request", out)
        self.assertIn(SHOW, runner.calls, "the pin is asked of hub/main")
        self.assertEqual(self.calls(), [])
        # the session edits the tree's pin to its own key: refused by name
        self.pin(KEY)
        self.pin("fedcba9876543210", hub=False)
        for l in (line(), line(key="fedcba9876543210")):
            text = self.refusal(l)
            self.assertTrue(self.refused(text), l)
            self.assertIn("docs/plan/APPROVER-KEY.json in this tree differs "
                          "from hub/main's", text)
        os.remove(self.key_path)
        self.assertIn("differs from hub/main's", self.refusal(line()))
        os.makedirs(self.key_path)
        self.assertIn("differs from hub/main's", self.refusal(line()))
        os.rmdir(self.key_path)
        self.assertEqual(self.calls(), [], "the verifier never ran")
        # the control: the tree's copy equal to hub/main's approves
        self.pin(KEY)
        self.assertEqual(self.refusal(line()), "")
        code, out = self.main(["--approval", line()], self.runner(), tty=False)
        self.assertEqual(code, C.EXIT_OK, out)


def setUpModule():
    T.setUpModule()


def tearDownModule():
    pass


if __name__ == "__main__":
    unittest.main()
