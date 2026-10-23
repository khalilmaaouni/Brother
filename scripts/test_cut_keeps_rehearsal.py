#!/usr/bin/env python3
"""CS12 (docs/plan/specs/1.1.7/CS12.md): the rehearsal keeps its commits, so the commit the owner approves is the
commit that was proved.

CS12.a: the rehearsal leaves refs/brother/rehearsed/<version>-<candidate12> at its bumped tip, the record carries
bumped_sha and bumped_tree, and the cut driver's adopt phase reads the field (the log parse is the fallback for an
older record only).

HOW. Throwaway git repositories and a scripted runner: git runs for real (through cut._run), the rehearsal's child
cut.py is replaced by two real commits made in the real rehearsal worktree. Nothing touches the real repository,
the real evidence folder or the network.

Python 3.9, standard library only. No em or en dashes.
"""
import contextlib
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut as C  # noqa: E402
import cut_driver as D  # noqa: E402
import cut_rehearsal as CR  # noqa: E402

try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n" % os.path.basename(__file__))

V = "9.9.9"
EMAIL = "cut@example.test"
BUMP = "%s: the version bump and the regenerated manifests" % V
NOTE = "%s: the export manifest that describes the tree and the note it ships" % V
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": EMAIL, "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": EMAIL, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}



def _recent_stamp():
    """An origin stamp one hour old, so a test about a carried row's REUSE never ages past MAX_AGE_HOURS on a later day
    (two cases fixed 2026-10-21 had stamped a literal date and went stale 24 hours after they were written)."""
    import datetime as _dt
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H" + ":%M" + ":%SZ")

class Done(object):
    """A process result, the shape subprocess.run returns."""

    def __init__(self, args, returncode, stdout="", stderr=""):
        self.args, self.returncode, self.stdout, self.stderr = args, returncode, stdout, stderr


class Fixture(unittest.TestCase):
    """A throwaway repository holding every SCRIPT_DIRS entry and scripts/cut.py, an evidence folder and a scratch
    root, with the cut evidence seam (BROTHER_CUT_TEST) on, under a clean environment."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="cs12-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.repo = os.path.join(self.base, "repo")
        self.ev = os.path.join(self.base, "evidence")
        self.bs = os.path.join(self.base, "scratch")
        home = os.path.join(self.base, "home")
        for d in (self.repo, home, self.bs):
            os.makedirs(d)
        env = dict((k, v) for k, v in os.environ.items()
                   if not k.startswith(("GIT_", "BROTHER_")) and k != "XDG_CONFIG_HOME")
        env.update(GIT_ENV, HOME=home, BROTHER_SCRATCH=self.bs, BROTHER_CUT_TEST="1", BROTHER_CUT_EVIDENCE_DIR=self.ev)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", EMAIL)
        files = {"scripts/cut.py": "cut\n", "bundle/a.json": "{}\n"}
        for rel in CR.SCRIPT_DIRS:
            files[rel + "/m.py"] = "m\n"
        for rel, text in files.items():
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "candidate")
        self.cand = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")
        self.n = 0

    def git(self, *args, **kw):
        cwd = kw.get("cwd", self.repo)
        proc = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, (args, proc.stdout, proc.stderr))
        return proc.stdout.strip()

    def ref(self, name):
        proc = subprocess.run(["git", "rev-parse", "--verify", "--quiet", name], cwd=self.repo,
                              capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def cut_commits(self, *subjects):
        """Commits on top of the candidate kept off main by a ref; returns the tip."""
        self.git("checkout", "-q", "--detach")
        for i, subject in enumerate(subjects):
            with open(os.path.join(self.repo, "c%d-%d.txt" % (self.n, i)), "w") as fh:
                fh.write(subject)
            self.git("add", "-A")
            self.git("commit", "-q", "-m", subject)
        tip = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/rehearsal-test/tip%d" % self.n, tip)
        self.n += 1
        self.git("checkout", "-q", "main")
        return tip

    def log(self, text="x\n"):
        os.makedirs(self.ev, exist_ok=True)
        self.n += 1
        path = os.path.join(self.ev, "log-%d.txt" % self.n)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def record(self, tip=None, verdict="READY", bumped=True, lines="tip", **over):
        """A record through the real writer. `tip` is the rehearsed tip; `bumped` carries it as the field; `lines`
        'tip' writes the two cut lines naming it into the log, or the text given."""
        if lines == "tip":
            lines = ("cut_v1.0.0.sh | %s %s\ncut_v1.0.0.sh | %s %s\n" % (tip[:9], BUMP, tip[:9], NOTE)) if tip else "x\n"
        log = self.log(lines)
        kw = dict(directory=self.ev, script_trees=dict((d, "a" * 40) for d in CR.SCRIPT_DIRS))
        if bumped and tip:
            kw.update(bumped_sha=tip, bumped_tree=self.git("rev-parse", tip + "^{tree}"))
        kw.update(over)
        return CR.record_rehearsal(V, self.cand, self.tree, 0 if verdict == "READY" else 1, verdict, log, 1.0,
                                   "0" * 64, **kw)

    def rewrite(self, path, **fields):
        """Rewrite fields of a record file in place; a value of KeyError removes the key."""
        with open(path) as fh:
            data = json.load(fh)
        for k, v in fields.items():
            if v is KeyError:
                data.pop(k, None)
            else:
                data[k] = v
        with open(path, "w") as fh:
            json.dump(data, fh)
        return path

    @staticmethod
    def load(path):
        with open(path) as fh:
            return json.load(fh)

    def log_of(self, record_path):
        with open(record_path) as fh:
            return json.load(fh)["log_path"]


class Runner(object):
    """The driver's runner: git is real, the covering-record probe answers OK, anything else is unhandled."""

    def __init__(self, cover="OK\n"):
        self.cover, self.calls = cover, []

    def __call__(self, cmd, cwd, stream=False):
        self.calls.append(list(cmd))
        if "_covering_record" in " ".join(cmd):
            return 0, self.cover
        if cmd[0] == "git":
            return D.real_runner(cmd, cwd)
        return 127, "unhandled %s" % " ".join(cmd)


class RehearsalRig(object):
    """A runner for run_rehearsal: git runs for real, worktree add and remove included. The rehearsal's child cut.py is
    replaced the way the real one behaves (scripts/cut.py check_mode): it runs its chain in a NESTED worktree of its own
    under the temp folder, commits the cut's two commits there, removes it, and prints the REHEARSED-TIP line only when
    it ran; the outer rehearsal worktree never moves. `tip_line` is None (print the true line), "" (print none) or the
    text to print instead; `subjects` are the two commit subjects; `fail` names git subcommands that fail."""

    def __init__(self, code=0, out="READY for %s\n" % V, fail=(), tip_line=None, subjects=(BUMP, NOTE)):
        self.code, self.out, self.fail, self.tip_line, self.subjects = code, out, set(fail), tip_line, subjects
        self.calls, self.tip, self.nested = [], "", ""

    def __call__(self, cmd, **kw):
        cmd = list(cmd)
        cwd = kw.get("cwd")
        self.calls.append((cmd, cwd))
        if cmd[0] != "git":
            nested = tempfile.mkdtemp(prefix="cut-rehearsal-%s-" % V)
            self.nested = nested
            assert C._run(["git", "worktree", "add", "--detach", nested, "HEAD"], cwd).returncode == 0
            for subject in self.subjects:
                with open(os.path.join(nested, "bump.txt"), "a") as fh:
                    fh.write(subject + "\n")
                C._run(["git", "add", "-A"], nested)
                proc = C._run(["git", "commit", "-q", "-m", subject], nested)
                assert proc.returncode == 0, proc.stderr
            self.tip = C._run(["git", "rev-parse", "HEAD"], nested).stdout.strip()
            tree = C._run(["git", "rev-parse", "HEAD^{tree}"], nested).stdout.strip()
            C._run(["git", "worktree", "remove", "--force", nested], cwd)
            line = "%s%s %s\n" % (CR.TIP_PREFIX, self.tip, tree) if self.tip_line is None else self.tip_line
            return Done(cmd, self.code, self.out + line, "")
        if cmd[1] in self.fail:
            return Done(cmd, 128, "", "fatal: scripted failure")
        return C._run(cmd, cwd, timeout=kw.get("timeout"))


class TestCS12A(Fixture):

    # --- write_rehearsed_ref ------------------------------------------------------------------------------------

    def test_write_rehearsed_ref_writes_the_ref_at_the_sha(self):
        tip = self.cut_commits(BUMP, NOTE)
        self.assertEqual(CR.write_rehearsed_ref(self.repo, V, self.cand, tip), (True, ""))
        ref = "refs/brother/rehearsed/%s-%s" % (V, self.cand[:12])
        self.assertEqual(CR.rehearsed_ref_name(V, self.cand), ref)
        self.assertEqual(self.ref(ref), tip)
        # a second write moves the ref (the newest rehearsal wins) and a write of the same sha is no error
        other = self.cut_commits(BUMP)
        self.assertEqual(CR.write_rehearsed_ref(self.repo, V, self.cand, other), (True, ""))
        self.assertEqual(self.ref(ref), other)
        self.assertEqual(CR.write_rehearsed_ref(self.repo, V, self.cand, other), (True, ""))

    def test_write_rehearsed_ref_refuses_a_non_sha(self):
        ref = "refs/brother/rehearsed/%s-%s" % (V, self.cand[:12])
        for sha in ("", "abc", "g" * 40, "A" * 40, None, 7, ["a" * 40], self.cand + "0"):
            with self.subTest(sha=sha):
                ok, why = CR.write_rehearsed_ref(self.repo, V, self.cand, sha)
                self.assertFalse(ok)
                self.assertIn("not 40 hex", why)
        for version in ("1.1", "", None, "1.1.7.1", "v1.1.7"):
            with self.subTest(version=version):
                self.assertFalse(CR.write_rehearsed_ref(self.repo, version, self.cand, self.cand)[0])
        for candidate in ("", "abc", None):
            with self.subTest(candidate=candidate):
                self.assertFalse(CR.write_rehearsed_ref(self.repo, V, candidate, self.cand)[0])
        self.assertEqual(self.ref(ref), "")

    def test_write_rehearsed_ref_reports_a_git_failure(self):
        ok, why = CR.write_rehearsed_ref(self.repo, V, self.cand, "1" * 40)   # a sha that is no object
        self.assertFalse(ok)
        self.assertIn("git update-ref refs/brother/rehearsed/", why)

    # --- record_rehearsal ---------------------------------------------------------------------------------------

    def test_record_rehearsal_carries_the_two_fields(self):
        tip = self.cut_commits(BUMP, NOTE)
        path = self.record(tip)
        with open(path) as fh:
            data = json.load(fh)
        self.assertEqual((data["schema"], data["bumped_sha"]), (CR.SCHEMA_BUMPED, tip))
        self.assertEqual(data["bumped_tree"], self.git("rev-parse", tip + "^{tree}"))
        self.assertIn(CR.SCHEMA_BUMPED, CR.SCHEMAS_READ)
        self.assertEqual(CR.load_record(path)[1], "")
        # without the fields the record keeps the shape every older reader knows
        with open(self.record(tip, bumped=False)) as fh:
            old = json.load(fh)
        self.assertEqual(old["schema"], CR.SCHEMA)
        self.assertNotIn("bumped_sha", old)

    def test_record_rehearsal_refuses_a_malformed_bumped_pair(self):
        tip = self.cut_commits(BUMP)
        tree = self.git("rev-parse", tip + "^{tree}")
        for sha, tr in (("abc", tree), (tip, "abc"), (tip, ""), ("", tree), (7, tree), (tip, ["x"]), (tip.upper(), tree)):
            with self.subTest(sha=sha, tree=tr):
                with self.assertRaises(ValueError):
                    self.record(tip, bumped_sha=sha, bumped_tree=tr)
        self.assertEqual([n for n in os.listdir(self.ev) if n.startswith("rehearsal-")], [],
                         "a refused record leaves no file")

    # --- record_bumped_sha --------------------------------------------------------------------------------------

    def test_record_bumped_sha_reads_the_fields(self):
        tip = self.cut_commits(BUMP, NOTE)
        path = self.record(tip)
        self.assertEqual(D.record_bumped_sha(path), (tip, self.git("rev-parse", tip + "^{tree}"), ""))

    def test_record_bumped_sha_refuses_new_schema_without_field(self):
        tip = self.cut_commits(BUMP, NOTE)
        for fields in ({"bumped_sha": KeyError}, {"bumped_tree": KeyError}, {"bumped_sha": ""}, {"bumped_sha": "abc"},
                       {"bumped_tree": "zz"}):
            with self.subTest(fields=sorted(fields)):
                path = self.rewrite(self.record(tip), **fields)
                sha, tree, why = D.record_bumped_sha(path)
                self.assertEqual((sha, tree), ("", ""))
                self.assertIn("must carry bumped_sha and bumped_tree", why)
                self.assertIn(path, why)

    def test_record_bumped_sha_falls_back_only_for_old_schema(self):
        tip = self.cut_commits(BUMP, NOTE)
        for schema in ("cv1.rehearsal.1", CR.SCHEMA):
            with self.subTest(schema=schema):
                path = self.rewrite(self.record(tip, bumped=False), schema=schema)
                self.assertEqual(D.record_bumped_sha(path), ("", "", ""))
        # an old record that does carry a malformed value is refused, not read as absent
        path = self.rewrite(self.record(tip, bumped=False), bumped_sha="abc", bumped_tree="def")
        self.assertIn("not 40 hex", D.record_bumped_sha(path)[2])
        path = self.rewrite(self.record(tip, bumped=False), bumped_sha=5)
        self.assertIn("not text", D.record_bumped_sha(path)[2])

    def test_record_bumped_sha_refuses_an_unreadable_record(self):
        tip = self.cut_commits(BUMP, NOTE)
        good = self.record(tip)
        torn = os.path.join(self.ev, "torn.json")
        with open(good) as fh, open(torn, "w") as out:
            out.write(fh.read()[:40])
        listy = os.path.join(self.ev, "list.json")
        with open(listy, "w") as fh:
            fh.write("[]")
        unknown = self.rewrite(self.record(tip), schema="cv1.rehearsal.0")
        for path in (torn, listy, os.path.join(self.ev, "missing.json"), self.ev, unknown):
            with self.subTest(path=os.path.basename(path)):
                sha, tree, why = D.record_bumped_sha(path)
                self.assertEqual((sha, tree), ("", ""))
                self.assertIn(path, why)

    # --- adopt_phase --------------------------------------------------------------------------------------------

    def adopt(self, path, runner=None):
        return D.adopt_phase(self.repo, V, self.cand, path, runner or Runner())

    def test_adopt_uses_the_field_before_the_log(self):
        tip = self.cut_commits(BUMP, NOTE)
        decoy = self.cut_commits(BUMP, NOTE)
        # the log names the decoy tip, the field names the real one, and the ref agrees with the field
        path = self.record(tip, lines="cut_v1.0.0.sh | %s %s\n" % (decoy[:9], NOTE))
        CR.write_rehearsed_ref(self.repo, V, self.cand, tip)
        with mock.patch.object(D, "tip_from_log", side_effect=AssertionError("the log must not be parsed")):
            verdict, detail, got, wt = self.adopt(path)
        self.assertEqual((verdict, got), ("PASS", tip), detail)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=wt), tip)

    def test_adopt_refuses_a_ref_that_does_not_point_at_the_named_sha(self):
        tip = self.cut_commits(BUMP, NOTE)
        other = self.cut_commits(BUMP, NOTE)
        path = self.record(tip)
        ref = CR.rehearsed_ref_name(V, self.cand)
        verdict, detail, got, wt = self.adopt(path)   # no ref at all
        self.assertEqual((verdict, got, wt), ("FAIL", "", ""))
        self.assertIn("the record names bumped_sha %s but %s points at nothing" % (tip, ref), detail)
        CR.write_rehearsed_ref(self.repo, V, self.cand, other)
        verdict, detail, _t, _w = self.adopt(path)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("the record names bumped_sha %s but %s points at %s" % (tip, ref, other), detail)
        self.assertEqual(self.ref("refs/heads/" + D.pin_name(V, self.cand)), "", "a refused adopt pins nothing")

    def test_adopt_falls_back_to_the_log_when_the_field_is_absent(self):
        tip = self.cut_commits(BUMP, NOTE)
        path = self.record(tip, bumped=False)
        verdict, detail, got, _w = self.adopt(path)
        self.assertEqual((verdict, got), ("PASS", tip), detail)

    def test_adopt_refuses_a_fallback_log_that_does_not_hash(self):
        tip = self.cut_commits(BUMP, NOTE)
        path = self.record(tip, bumped=False)
        with open(self.log_of(path), "a") as fh:
            fh.write("altered after the record\n")
        verdict, detail, got, _w = self.adopt(path)
        self.assertEqual((verdict, got), ("FAIL", ""))
        self.assertIn("was altered after the record", detail)
        os.remove(self.log_of(path))
        verdict, detail, _t, _w = self.adopt(path)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("unreadable", detail)

    def test_adopt_refuses_a_new_schema_record_with_a_broken_log(self):
        # council blocker (security): the field path is also gated on the log the record names
        tip = self.cut_commits(BUMP, NOTE)
        path = self.record(tip)
        CR.write_rehearsed_ref(self.repo, V, self.cand, tip)
        self.assertEqual(self.adopt(path)[0], "PASS")
        with open(self.log_of(path), "a") as fh:
            fh.write("altered\n")
        verdict, detail, got, _w = self.adopt(path)
        self.assertEqual((verdict, got), ("FAIL", ""))
        self.assertIn("was altered", detail)
        os.remove(self.log_of(path))
        self.assertEqual(self.adopt(path)[0], "NO-DATA")

    def test_adopt_refuses_a_new_schema_record_without_its_field(self):
        tip = self.cut_commits(BUMP, NOTE)
        path = self.rewrite(self.record(tip), bumped_sha=KeyError)
        CR.write_rehearsed_ref(self.repo, V, self.cand, tip)
        verdict, detail, got, _w = self.adopt(path)   # the record reader already calls it malformed
        self.assertEqual((verdict, got), ("NO-DATA", ""))
        self.assertIn("bumped_sha and bumped_tree", detail)

    def test_adopt_refuses_an_older_record_that_carries_a_malformed_tip(self):
        tip = self.cut_commits(BUMP, NOTE)
        path = self.rewrite(self.record(tip, bumped=False), bumped_sha="abc", bumped_tree="def")
        verdict, detail, got, _w = self.adopt(path)
        self.assertEqual((verdict, got), ("FAIL", ""))
        self.assertIn("not 40 hex", detail)

    def test_adopt_moves_the_pin_when_a_second_rehearsal_moved_the_ref(self):
        # council blocker (adversary): the second rehearsal's tip must be adoptable although the first one's pin exists
        first = self.cut_commits(BUMP, NOTE)
        second = self.cut_commits(BUMP, NOTE)
        pin = "refs/heads/" + D.pin_name(V, self.cand)
        CR.write_rehearsed_ref(self.repo, V, self.cand, first)
        verdict, detail, got, _w = self.adopt(self.record(first))
        self.assertEqual((verdict, got, self.ref(pin)), ("PASS", first, first), detail)
        CR.write_rehearsed_ref(self.repo, V, self.cand, second)
        verdict, detail, got, wt = self.adopt(self.record(second))
        self.assertEqual((verdict, got, self.ref(pin)), ("PASS", second, second), detail)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=wt), second)
        again = self.adopt(self.record(second))
        self.assertEqual(again[0], "REUSED", again[1])

    def test_adopt_keeps_a_log_parsed_pin_unmoved(self):
        first = self.cut_commits(BUMP, NOTE)
        second = self.cut_commits(BUMP, NOTE)
        self.assertEqual(self.adopt(self.record(first, bumped=False))[0], "PASS")
        verdict, detail, _t, _w = self.adopt(self.record(second, bumped=False))
        self.assertEqual(verdict, "FAIL")
        self.assertIn("never moved", detail)

    def test_adopt_returns_the_four_tuple_contract(self):
        tip = self.cut_commits(BUMP, NOTE)
        CR.write_rehearsed_ref(self.repo, V, self.cand, tip)
        results = [self.adopt(self.record(tip)), self.adopt(self.record(tip, verdict="NOT-READY")),
                   self.adopt(os.path.join(self.ev, "missing.json")), self.adopt(self.record(tip, bumped=False))]
        for result in results:
            self.assertEqual(len(result), 4, result)
            self.assertIn(result[0], ("PASS", "REUSED", "FAIL", "NO-DATA"))
            self.assertTrue(all(isinstance(x, str) for x in result), result)
        self.assertEqual([r[0] for r in results], ["PASS", "FAIL", "NO-DATA", "REUSED"])

    # --- the rehearsal writes the ref (the entry point, run_rehearsal) ------------------------------------------

    def rehearse(self, rig):
        return CR.run_rehearsal(self.repo, V, runner=rig)

    def test_a_ready_rehearsal_leaves_the_ref_at_the_bumped_tip_and_the_record_names_it(self):
        ref = CR.rehearsed_ref_name(V, self.cand)
        rig = RehearsalRig()
        verdict, code, path = self.rehearse(rig)
        self.assertEqual((verdict, code), ("READY", 0))
        with open(path) as fh:
            data = json.load(fh)
        tip = self.ref(ref)
        self.assertRegex(tip, "^[0-9a-f]{40}$")
        self.assertNotEqual(tip, self.cand)
        self.assertEqual((data["schema"], data["bumped_sha"]), (CR.SCHEMA_BUMPED, tip))
        self.assertEqual(data["bumped_tree"], self.git("rev-parse", tip + "^{tree}"))
        self.assertEqual(self.git("log", "-2", "--format=%s", tip).splitlines(), [NOTE, BUMP])
        removes = [c for c in rig.calls if c[0][:3] == ["git", "worktree", "remove"]]
        self.assertEqual(len(removes), 1, "the worktree is removed")
        self.assertFalse(os.path.exists(removes[0][0][-1]))
        # and the driver adopts exactly that commit with no log parse
        with mock.patch.object(D, "tip_from_log", side_effect=AssertionError("no log parse")):
            verdict, detail, got, _w = self.adopt(path)
        self.assertEqual((verdict, got), ("PASS", tip), detail)

    def test_a_failed_second_rehearsal_does_not_move_the_ref(self):
        ref = CR.rehearsed_ref_name(V, self.cand)
        self.assertEqual(self.rehearse(RehearsalRig())[0], "READY")
        first = self.ref(ref)
        for rig, want in ((RehearsalRig(code=1, out="NOT READY for %s\n" % V), "NOT-READY"),
                          (RehearsalRig(code=0, out="no verdict line\n"), "NO-DATA")):
            verdict, _code, path = self.rehearse(rig)
            self.assertEqual(verdict, want)
            self.assertEqual(self.ref(ref), first, "a %s rehearsal moved the ref" % want)
            with open(path) as fh:
                data = json.load(fh)
            self.assertEqual(data["verdict"], want)
            self.assertNotIn("bumped_sha", data, "a rehearsal that is not READY names no tip to approve")

    def test_a_second_ready_rehearsal_moves_the_ref_to_its_own_tip(self):
        ref = CR.rehearsed_ref_name(V, self.cand)
        _v, _c, first_path = self.rehearse(RehearsalRig())
        first = self.ref(ref)
        time.sleep(1.1)   # the commits and the record name carry the second
        _v, _c, second_path = self.rehearse(RehearsalRig())
        second = self.ref(ref)
        with open(second_path) as fh:
            self.assertEqual(json.load(fh)["bumped_sha"], second)
        self.assertNotEqual(first, second)
        self.assertEqual(self.adopt(second_path)[0], "PASS")
        verdict, detail, _t, _w = self.adopt(first_path)   # the older record's field no longer matches the ref
        self.assertEqual(verdict, "FAIL")
        self.assertIn("points at %s" % second, detail)

    def test_a_rehearsal_whose_ref_cannot_be_written_is_not_ready(self):
        ref = CR.rehearsed_ref_name(V, self.cand)
        verdict, _code, path = self.rehearse(RehearsalRig(fail=("update-ref",)))
        self.assertEqual(verdict, "NOT-READY")
        with open(path) as fh:
            data = json.load(fh)
        self.assertEqual(data["verdict"], "NOT-READY")
        self.assertIn("git update-ref", data["note"])
        self.assertEqual(self.ref(ref), "")

    def test_the_tip_is_the_nested_chains_not_the_rehearsal_worktrees(self):
        # reviewer B1: the real child commits in a nested worktree and removes it; the outer HEAD stays the candidate
        ref = CR.rehearsed_ref_name(V, self.cand)
        rig = RehearsalRig()
        verdict, _code, path = self.rehearse(rig)
        data = self.load(path)
        self.assertEqual(verdict, "READY")
        self.assertNotEqual(rig.tip, self.cand)
        self.assertEqual((data["schema"], data["bumped_sha"], self.ref(ref)), (CR.SCHEMA_BUMPED, rig.tip, rig.tip))
        self.assertFalse(os.path.exists(rig.nested))
        self.assertEqual(self.git("rev-list", "--count", "%s..%s" % (self.cand, rig.tip)), "2")
        # the driver adopts that commit, and the real cut's gate accepts it as the resume
        verdict, detail, got, _w = self.adopt(path)
        self.assertEqual((verdict, got), ("PASS", rig.tip), detail)
        self.git("checkout", "-q", "--detach", rig.tip)
        with mock.patch.object(CR, "running_cut_py_sha256", return_value=data["cut_py_sha256"]):
            got, why = CR.covering_record(self.repo, V, resume_sha=rig.tip, directory=self.ev, require_tip=rig.tip,
                                          allowed_paths=("bump.txt",))
        self.assertEqual((os.path.basename(got), why), (os.path.basename(path), ""))

    def test_a_child_that_prints_no_tip_line_leaves_the_older_record_and_no_ref(self):
        ref = CR.rehearsed_ref_name(V, self.cand)
        verdict, _code, path = self.rehearse(RehearsalRig(tip_line=""))
        data = self.load(path)
        self.assertEqual(self.ref(ref), "")
        self.assertIn("printed no REHEARSED-TIP: line", data["note"])
        self.assertNotIn("bumped_sha", data)
        self.assertEqual(data["schema"], CR.SCHEMA)
        self.assertEqual(verdict, "READY", "the older shape: the driver then reads the verified log")

    def test_a_tip_line_that_is_not_the_cuts_own_commits_leaves_no_ref(self):
        ref = CR.rehearsed_ref_name(V, self.cand)
        other = self.cut_commits("a hand commit")
        other_tree = self.git("rev-parse", other + "^{tree}")
        three = self.cut_commits(BUMP, NOTE, NOTE)
        cases = {
            "the candidate itself (zero commits)": ("%s%s %s\n" % (CR.TIP_PREFIX, self.cand, self.tree),
                                                    "0 commits past the candidate"),
            "a foreign subject": ("%s%s %s\n" % (CR.TIP_PREFIX, other, other_tree), "is not the cut's own"),
            "three commits": ("%s%s %s\n" % (CR.TIP_PREFIX, three, self.git("rev-parse", three + "^{tree}")),
                              "3 commits past the candidate"),
            "an unknown object": ("%s%s %s\n" % (CR.TIP_PREFIX, "1" * 40, "2" * 40), "is not on top of the candidate"),
            "a malformed line": ("%snot-a-sha\n" % CR.TIP_PREFIX, "printed no REHEARSED-TIP: line"),
        }
        for name, (line, why) in cases.items():
            with self.subTest(case=name):
                verdict, _code, path = self.rehearse(RehearsalRig(tip_line=line))
                data = self.load(path)
                self.assertEqual(self.ref(ref), "")
                self.assertNotIn("bumped_sha", data)
                self.assertIn(why, data["note"])
        # the right commits with the wrong tree named
        rig = RehearsalRig(tip_line="")
        self.rehearse(rig)
        wrong = "%s%s %s\n" % (CR.TIP_PREFIX, rig.tip, "3" * 40)
        _v, _c, path = self.rehearse(RehearsalRig(tip_line=wrong))
        self.assertIn("not the", self.load(path)["note"])
        self.assertEqual(self.ref(ref), "")

    def test_the_last_tip_line_wins(self):
        ref = CR.rehearsed_ref_name(V, self.cand)
        rig = RehearsalRig(tip_line="%s%s %s\n" % (CR.TIP_PREFIX, "1" * 40, "2" * 40))
        _v, _c, path = self.rehearse(rig)
        self.assertEqual(self.ref(ref), "")
        rig = RehearsalRig(out="READY for %s\n%s%s %s\n" % (V, CR.TIP_PREFIX, "1" * 40, "2" * 40))
        _v, _c, path = self.rehearse(rig)   # the bad line comes first, the child's true one is last
        self.assertEqual(self.load(path)["bumped_sha"], rig.tip)

    def test_a_record_that_cannot_be_written_puts_the_ref_back(self):
        ref = CR.rehearsed_ref_name(V, self.cand)
        self.assertEqual(self.rehearse(RehearsalRig())[0], "READY")
        first = self.ref(ref)
        with mock.patch.object(CR, "record_rehearsal", side_effect=ValueError("scripted")):
            with self.assertRaises(CR.RehearsalRefused):
                self.rehearse(RehearsalRig())
        self.assertEqual(self.ref(ref), first)
        self.git("update-ref", "-d", ref)
        with mock.patch.object(CR, "record_rehearsal", side_effect=ValueError("scripted")):
            with self.assertRaises(CR.RehearsalRefused):
                self.rehearse(RehearsalRig())
        self.assertEqual(self.ref(ref), "", "no record, so no ref")

    def test_a_local_rehearsal_writes_no_ref(self):
        ref = CR.rehearsed_ref_name(V, self.cand)
        verdict, _code, _path = CR.run_rehearsal(self.repo, V, runner=RehearsalRig(out="LOCAL-READY for %s\n" % V),
                                                 local=True)
        self.assertEqual(verdict, "LOCAL-PASS")
        self.assertEqual(self.ref(ref), "")


COLD = "a real cut from 1.1.7 on needs --resume <bumped_sha>, or --cold in the owner's own words"


class TestCS12B(Fixture):

    # --- cold_refusal -------------------------------------------------------------------------------------------

    def test_cold_refusal_refuses_a_cold_cut_at_117(self):
        self.assertEqual(C.COLD_REFUSAL, COLD)
        for version in ("1.1.7", "1.1.8", "1.2.0", "2.0.0", "9.9.9"):
            with self.subTest(version=version):
                self.assertEqual(C.cold_refusal(version, "", False), COLD)
                self.assertEqual(C.cold_refusal(version, "", None), COLD)
                self.assertEqual(C.cold_refusal(version, "", 0), COLD)

    def test_cold_refusal_allows_a_resume(self):
        self.assertIsNone(C.cold_refusal("1.1.7", "a" * 40, False))
        self.assertIsNone(C.cold_refusal("1.1.7", self.cand, None))

    def test_cold_refusal_refuses_a_blank_or_junk_resume(self):
        # council blocker (adversary, assurance, security): a space or a word is no resume
        for resume in (" ", "\n", "x", "abc", "a" * 39, "a" * 41, "A" * 40, " " + "a" * 40, "a" * 40 + "\n", None, 7,
                       ["a" * 40]):
            with self.subTest(resume=resume):
                self.assertEqual(C.cold_refusal("1.1.7", resume, False), COLD)

    def test_cold_refusal_allows_the_cold_flag(self):
        for cold in (True, 1, "yes"):
            with self.subTest(cold=cold):
                self.assertIsNone(C.cold_refusal("1.1.7", "", cold))
        self.assertIsNone(C.cold_refusal("9.9.9", " ", True))

    def test_cold_refusal_allows_pre_117(self):
        for version in ("1.1.6", "1.0.0", "1.0.99", "0.9.9", "0.0.0"):
            with self.subTest(version=version):
                self.assertIsNone(C.cold_refusal(version, "", False))

    def test_cold_refusal_reads_version_as_numbers_not_strings(self):
        # "1.1.10" < "1.1.7" is True as text; as numbers 1.1.10 is the later cut
        for version in ("1.1.10", "1.10.0", "10.0.0", "1.1.100"):
            with self.subTest(version=version):
                self.assertEqual(C.cold_refusal(version, "", False), COLD)
        self.assertIsNone(C.cold_refusal("1.1.6", "", False))
        self.assertIsNone(C.cold_refusal("1.0.10", "", False))

    def test_cold_refusal_refuses_a_version_that_is_not_xyz_even_when_cold(self):
        for version in ("", "1.1", "1.1.7.1", "v1.1.7", " 1.1.7", "1.1.7\n", None, 117, ["1", "1", "7"], "1.1.x"):
            with self.subTest(version=version):
                for cold in (False, True):
                    why = C.cold_refusal(version, "a" * 40, cold)
                    self.assertEqual(why, "the version %r is not X.Y.Z" % (version,))

    # --- cut_mode and main --------------------------------------------------------------------------------------

    def run_cut_mode(self, version="1.1.7", gates="STOP: scripted gate", **kw):
        out = io.StringIO()
        runner = mock.Mock(side_effect=AssertionError("no command runs before the gate"))
        bm = mock.MagicMock()
        with mock.patch.object(C, "cut_gates", return_value=gates) as g, \
                mock.patch.object(C, "claim_fence", side_effect=AssertionError("no fence claimed")) as claim, \
                contextlib.redirect_stdout(out):
            code = C.cut_mode(self.repo, version, bm, ["scripts/"], None, True, None, runner, **kw)
        return code, out.getvalue(), g, claim, runner

    def test_cut_mode_refuses_a_cold_cut_before_the_fence(self):
        code, out, gates, claim, runner = self.run_cut_mode(cold=False)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertEqual(out.strip(), COLD)
        gates.assert_not_called()
        claim.assert_not_called()
        runner.assert_not_called()

    def test_cut_mode_lets_a_resume_through_to_the_gates(self):
        code, out, gates, _claim, _r = self.run_cut_mode(cold=False, resume_sha="b" * 40)
        self.assertEqual(code, C.EXIT_REFUSED)
        self.assertEqual(out.strip(), "STOP: scripted gate")
        gates.assert_called_once()

    def test_cut_mode_prints_the_cold_line_when_named(self):
        code, out, gates, _claim, _r = self.run_cut_mode(cold=True)
        self.assertEqual(out.splitlines(), ["[cut] --cold named by the owner", "STOP: scripted gate"])
        gates.assert_called_once()
        code, out, _g, _c, _r = self.run_cut_mode(version="1.1.6", cold=True)
        self.assertEqual(out.splitlines()[0], "[cut] --cold named by the owner")

    def test_cut_mode_without_the_word_is_not_gated(self):
        # a library caller that passes no cold at all is not asking for the gate (main always asks)
        code, out, gates, _claim, _r = self.run_cut_mode()
        self.assertEqual(out.strip(), "STOP: scripted gate")
        gates.assert_called_once()

    def run_main(self, *argv, injected=False, **patches):
        """main as the command line runs it (no runner), or with a runner injected, the tests' seam."""
        out = io.StringIO()
        notes = ["--no-release-page", "scripted"]
        stack = contextlib.ExitStack()
        with stack:
            stack.enter_context(mock.patch.object(C, "_stdin_is_tty", return_value=True))
            stack.enter_context(mock.patch.object(C, "_load_bm_store", return_value=(None, None, "absent")))
            stack.enter_context(mock.patch.object(C, "release_paths", return_value=["scripts/"]))
            stack.enter_context(contextlib.redirect_stdout(out))
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(C, name, **value))
            runner = mock.Mock(side_effect=AssertionError("no run")) if injected else None
            code = C.main(list(argv) + notes, root=self.repo, runner=runner)
        return code, out.getvalue()

    def test_main_refuses_a_cold_real_cut_before_any_gate(self):
        for argv in (["--version", "1.1.7"], ["--version", "1.1.7", "--yes"] and ["--version", "1.1.9"]):
            with self.subTest(argv=argv):
                code, out = self.run_main(*argv, cut_gates={"side_effect": AssertionError("gate reached")})
                self.assertEqual(code, C.EXIT_REFUSED)
                self.assertIn(COLD, out)

    def test_main_with_an_injected_runner_is_not_gated(self):
        # the seam every existing suite drives main through: it never reaches the command line
        seen = []
        code, out = self.run_main("--version", "1.1.7", injected=True,
                                  cut_mode={"side_effect": lambda *a, **k: seen.append(k) or C.EXIT_OK})
        self.assertEqual(code, C.EXIT_OK)
        self.assertIsNone(seen[0]["cold"])
        self.assertNotIn(COLD, out)

    def test_main_passes_cold_and_resume_to_cut_mode(self):
        seen = []

        def spy(*args, **kw):
            seen.append(kw)
            return C.EXIT_OK
        resume = "c" * 40
        for argv, want_cold in ((["--version", "1.1.7", "--cold"], True), (["--version", "1.1.7", "--resume", resume], False),
                                (["--version", "1.1.6"], False)):
            with self.subTest(argv=argv):
                code, _out = self.run_main(*argv, cut_mode={"side_effect": spy})
                self.assertEqual(code, C.EXIT_OK)
                self.assertIs(seen[-1]["cold"], want_cold)
                self.assertEqual(seen[-1]["resume_sha"], resume if "--resume" in argv else None)

    def test_main_check_ignores_cold(self):
        seen = []
        code, out = self.run_main("--check", "--version", "1.1.7", "--cold",
                                  check_mode={"side_effect": lambda *a, **k: seen.append(1) or C.EXIT_OK},
                                  cut_mode={"side_effect": AssertionError("cut_mode is not a check")})
        self.assertEqual((code, seen), (C.EXIT_OK, [1]))

    def test_the_cold_flag_is_a_declared_argument(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = C.main(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("--cold", out.getvalue())
        self.assertIn("refused from 1.1.7 on without it", " ".join(out.getvalue().split()))

    # --- the resume must be the proved commit (cut_gates, one source: covering_record) --------------------------

    def proven(self, **over):
        """A record naming the rehearsed tip, HEAD checked out at the tip; returns (tip, record path)."""
        tip = self.cut_commits(BUMP, NOTE)
        trees, why = CR._script_trees(self.repo, self.cand)
        self.assertEqual(why, "")
        kw = dict(script_trees=trees, now=time.time())
        kw.update(over)
        log = self.log("READY for %s\n" % V)
        path = CR.record_rehearsal(V, self.cand, self.tree, 0, "READY", log, 1.0, CR.running_cut_py_sha256(),
                                   directory=self.ev, bumped_sha=tip, bumped_tree=self.git("rev-parse", tip + "^{tree}"),
                                   **kw)
        self.git("checkout", "-q", "--detach", tip)
        return tip, path

    def cover(self, resume, require_tip=""):
        allowed = self.git("diff", "--name-only", "%s..HEAD" % self.cand).split()
        return CR.covering_record(self.repo, V, resume_sha=resume, directory=self.ev, allowed_paths=allowed,
                                  require_tip=require_tip)

    def test_covering_record_covers_the_proved_tip_only_when_asked_for_it(self):
        tip, path = self.proven()
        self.assertEqual(self.cover(tip, ""), (path, ""))
        CR.write_rehearsed_ref(self.repo, V, self.cand, tip)
        self.assertEqual(self.cover(tip, tip), (path, ""))
        other = "e" * 40
        got, why = self.cover(tip, other)
        self.assertEqual(got, "")
        self.assertIn("is not the tip %s that rehearsal" % tip[:12], why)

    def test_covering_record_refuses_when_the_ref_is_missing_or_moved(self):
        tip, path = self.proven()
        got, why = self.cover(tip, tip)   # no ref written
        self.assertEqual(got, "")
        self.assertIn("points at nothing", why)
        elsewhere = self.cut_commits(BUMP, NOTE)
        CR.write_rehearsed_ref(self.repo, V, self.cand, elsewhere)
        got, why = self.cover(tip, tip)
        self.assertEqual(got, "")
        self.assertIn("points at %s" % elsewhere[:12], why)

    def test_covering_record_leaves_an_older_record_alone(self):
        tip = self.cut_commits(BUMP, NOTE)
        trees, _why = CR._script_trees(self.repo, self.cand)
        log = self.log("READY for %s\n" % V)
        path = CR.record_rehearsal(V, self.cand, self.tree, 0, "READY", log, 1.0, CR.running_cut_py_sha256(),
                                   directory=self.ev, script_trees=trees)
        self.git("checkout", "-q", "--detach", tip)
        self.assertEqual(self.cover(tip, tip), (path, ""))

    def test_covering_record_refuses_a_record_of_the_new_schema_without_its_tip(self):
        tip, path = self.proven()
        self.rewrite(path, bumped_sha=KeyError)
        got, why = self.cover(tip, tip)
        self.assertEqual(got, "")
        self.assertIn("malformed", why)
        self.assertIn("bumped_sha and bumped_tree", why)

    def test_cut_gates_asks_the_resume_to_be_the_proved_commit(self):
        tip, _path = self.proven()
        CR.write_rehearsed_ref(self.repo, V, self.cand, tip)
        allowed = tuple(self.git("diff", "--name-only", "%s..HEAD" % self.cand).split())
        with mock.patch.object(C, "version_skip_refusal", return_value=""), \
                mock.patch.object(C, "_cut_own_paths", return_value=allowed):
            self.assertEqual(C.cut_gates(self.repo, V, resume_sha=tip, env={}), "")
            moved = self.cut_commits(BUMP, NOTE)
            CR.write_rehearsed_ref(self.repo, V, self.cand, moved)
            why = C.cut_gates(self.repo, V, resume_sha=tip, env={})
        self.assertIn("points at %s" % moved[:12], why)


KEY = "d" * 40
HOMES = {"required-fast-hub": "real", "export-real-home": "real", "virgin-gate": "empty"}


class RowFixture(Fixture):
    """Rows with real logs, and the chain's REUSED lines for them."""

    def row(self, name="export-real-home", key=KEY, verdict="PASS", text=None):
        log = self.log(text or "the log of %s %d\n" % (name, self.n))
        with open(log, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        return {"row": name, "argv": ["x"], "key": key, "home": HOMES[name], "verdict": verdict, "seconds": 1.0,
                "log_path": log, "log_sha256": digest}

    def all_rows(self):
        return [self.row(name) for name in HOMES]

    def first_record(self, rows=None, hours_ago=2.0, **over):
        """A READY record of `hours_ago` ago holding the rows; returns (path, rows)."""
        rows = self.all_rows() if rows is None else rows
        kw = dict(rows=rows, now=time.time() - hours_ago * 3600)
        kw.update(over)
        return self.record(**kw), rows

    @staticmethod
    def reused_lines(rows, names=None):
        return "".join("REUSED %s %s %s\n" % (r["row"], r["key"][:12], r["log_path"])
                       for r in rows if names is None or r["row"] in names)


class TestCS12C(RowFixture):

    def test_carry_reused_rows_adds_origin_stamp_and_record(self):
        rows = self.all_rows()
        before = json.dumps(rows, sort_keys=True)
        got = CR.carry_reused_rows(rows, "/ev/record-1.json", "2026-10-20T01:00:00Z")
        self.assertEqual(len(got), 3)
        for row in got:
            self.assertEqual((row["origin_record"], row["origin_stamp"]), ("/ev/record-1.json", "2026-10-20T01:00:00Z"))
            self.assertEqual(CR.validate_row(row), "")
        self.assertEqual(json.dumps(rows, sort_keys=True), before, "the rows given are copied, never changed")
        self.assertEqual(CR.carry_reused_rows([], "a", "b"), [])

    def test_carry_reused_rows_keeps_the_first_origin_of_a_row_that_has_one(self):
        # council blocker (adversary, assurance): a second carry never resets the age
        row = dict(self.row(), origin_record="/ev/first.json", origin_stamp="2026-10-19T00:00:00Z")
        got = CR.carry_reused_rows([row], "/ev/second.json", "2026-10-20T00:00:00Z")
        self.assertEqual((got[0]["origin_record"], got[0]["origin_stamp"]), ("/ev/first.json", "2026-10-19T00:00:00Z"))
        # an origin that is only half there is replaced whole, never mixed
        half = dict(self.row(), origin_stamp="2026-10-19T00:00:00Z")
        got = CR.carry_reused_rows([half], "/ev/second.json", "2026-10-20T00:00:00Z")
        self.assertEqual((got[0]["origin_record"], got[0]["origin_stamp"]), ("/ev/second.json", "2026-10-20T00:00:00Z"))

    def test_carry_reused_rows_keeps_the_last_of_two_rows_with_one_name_and_home(self):
        first, second = self.row(), self.row()
        got = CR.carry_reused_rows([first, second], "/ev/r.json", "2026-10-20T00:00:00Z")
        self.assertEqual([r["log_path"] for r in got], [second["log_path"]])

    def test_carry_reused_rows_refuses_a_malformed_row(self):
        good = self.row()
        broken = [dict((k, v) for k, v in good.items() if k != "key"), dict(good, key="abc"), dict(good, verdict="MAYBE"),
                  dict(good, surprise=1), "a row", ["a"], None, dict(good, home="elsewhere")]
        for bad in broken:
            with self.subTest(bad=str(bad)[:40]):
                with self.assertRaises(ValueError):
                    CR.carry_reused_rows([good, bad], "/ev/r.json", "2026-10-20T00:00:00Z")
        for rows in ("rows", None, {"a": 1}, b"x", 7):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    CR.carry_reused_rows(rows, "/ev/r.json", "2026-10-20T00:00:00Z")

    def test_carry_reused_rows_refuses_an_empty_origin_stamp(self):
        for origin_record, origin_stamp in (("/ev/r.json", ""), ("", "2026-10-20T00:00:00Z"), ("/ev/r.json", None),
                                            (None, "x"), ("/ev/r.json", 5), (["a"], "x")):
            with self.subTest(record=origin_record, stamp=origin_stamp):
                with self.assertRaises(ValueError):
                    CR.carry_reused_rows([self.row()], origin_record, origin_stamp)

    def test_validate_row_allows_the_two_origin_fields(self):
        row = dict(self.row(), origin_record="/ev/r.json", origin_stamp="2026-10-20T00:00:00Z")
        self.assertEqual(CR.validate_row(row), "")
        self.assertEqual(CR.validate_row(self.row()), "")
        self.assertEqual(CR.CARRIED_FIELDS, ("origin_record", "origin_stamp"))
        only_one = dict(self.row(), origin_stamp="2026-10-20T00:00:00Z")
        self.assertEqual(CR.validate_row(only_one), "", "pairing is carry_reused_rows' and record_rehearsal's rule")

    def test_validate_row_refuses_an_unknown_extra_key_and_a_bad_origin(self):
        good = self.row()
        self.assertIn("fields", CR.validate_row(dict(good, origin_extra="x")))
        self.assertIn("fields", CR.validate_row(dict(good, origin_record="/ev/r.json", surprise=1)))
        self.assertIn("fields", CR.validate_row(dict((k, v) for k, v in good.items() if k != "row")))
        for field in CR.CARRIED_FIELDS:
            for bad in ("", None, 5, ["x"], True):
                with self.subTest(field=field, bad=bad):
                    self.assertIn("not a non empty string", CR.validate_row(dict(good, **{field: bad})))

    def test_a_chain_row_never_carries_an_origin(self):
        row = dict(self.row(), origin_record="/ev/r.json", origin_stamp="2026-10-20T00:00:00Z")
        rows, problems = CR.parse_rows(CR.row_line(self.row()) + "\n" + CR.row_line(row) + "\n")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(problems), 1)
        self.assertIn("never carries an origin", problems[0])

    def test_record_rehearsal_keeps_carried_rows(self):
        fresh = self.row("virgin-gate")
        carried = CR.carry_reused_rows([self.row("export-real-home")], "/ev/r.json", "2026-10-20T00:00:00Z")
        path = self.record(rows=[fresh], carried=carried)
        with open(path) as fh:
            data = json.load(fh)
        self.assertEqual([r["row"] for r in data["rows"]], ["virgin-gate", "export-real-home"])
        self.assertNotIn("origin_stamp", data["rows"][0])
        self.assertEqual(data["rows"][1]["origin_record"], "/ev/r.json")
        # empty everywhere is the shape the base wrote
        with open(self.record()) as fh:
            self.assertEqual(json.load(fh)["rows"], [])

    def test_record_rehearsal_reads_a_carried_row_back(self):
        carried = CR.carry_reused_rows([self.row("export-real-home")], "/ev/r.json", _recent_stamp())
        path = self.record(carried=carried)
        record, why = CR.load_record(path)
        self.assertEqual(why, "")
        self.assertEqual(record["rows"], carried)
        row, why = CR.reusable_row(record, "export-real-home", KEY, "real")
        self.assertEqual((row, why), (carried[0], ""))

    def test_record_rehearsal_refuses_a_carried_row_it_cannot_trust(self):
        good = self.row()
        for carried in ([good], [dict(good, origin_record="/ev/r.json")], [dict(good, origin_stamp="2026-10-20T00:00:00Z")],
                        [dict(good, origin_record="", origin_stamp="x")], ["row"], "rows", None, {"a": 1},
                        [dict(good, key="abc", origin_record="/ev/r.json", origin_stamp="x")]):
            with self.subTest(carried=str(carried)[:50]):
                with self.assertRaises(ValueError):
                    self.record(carried=carried)
        self.assertEqual([n for n in os.listdir(self.ev) if n.startswith("rehearsal-")], [])

    def test_a_carried_row_wins_over_a_fresh_one_for_the_same_name(self):
        fresh = self.row("export-real-home")
        carried = CR.carry_reused_rows([self.row("export-real-home")], "/ev/r.json", _recent_stamp())
        record, _why = CR.load_record(self.record(rows=[fresh], carried=carried))
        row, why = CR.reusable_row(record, "export-real-home", KEY, "real")
        self.assertEqual(row["log_path"], carried[0]["log_path"], why)

    # --- the rehearsal carries what it reused (the entry point, run_rehearsal) ----------------------------------

    def rehearse_reusing(self, rows, names=None, extra=""):
        out = "READY for %s\n%s%s" % (V, self.reused_lines(rows, names), extra)
        return CR.run_rehearsal(self.repo, V, runner=RehearsalRig(out=out))

    def test_a_second_rehearsal_carries_every_reused_row(self):
        first, rows = self.first_record()
        verdict, _code, second = self.rehearse_reusing(rows)
        self.assertEqual(verdict, "READY")
        record, why = CR.load_record(second)
        self.assertEqual(why, "")
        first_stamp = CR.load_record(first)[0]["finished_at"]
        self.assertEqual(sorted(r["row"] for r in record["rows"]), sorted(HOMES))
        for row in record["rows"]:
            self.assertEqual((row["origin_record"], row["origin_stamp"]), (first, first_stamp))
        # a fake --resume cut over the second record runs zero rows
        for name, home in HOMES.items():
            got, why = C._reusable(record, name, KEY, home)
            self.assertIsNotNone(got, "%s: %s" % (name, why))
        boom = {name: mock.Mock(side_effect=AssertionError("row %s ran" % name)) for name in C.EXPORT_ROWS}
        with mock.patch.object(C, "export_tree", return_value=(KEY, "")), contextlib.redirect_stdout(io.StringIO()):
            ok, lines = C.reused_or_run_export_rows(self.repo, V, record, second, checks=boom)
        self.assertTrue(ok, lines)
        self.assertTrue(all("reused" in line for line in lines), lines)

    def test_a_third_rehearsal_keeps_the_first_origin_so_age_is_never_laundered(self):
        first, rows = self.first_record(hours_ago=3.0)
        first_stamp = CR.load_record(first)[0]["finished_at"]
        time.sleep(1.1)
        _v, _c, second = self.rehearse_reusing(rows)
        time.sleep(1.1)
        _v, _c, third = self.rehearse_reusing(rows)
        for path in (second, third):
            rows_there = CR.load_record(path)[0]["rows"]
            self.assertEqual(len(rows_there), 3, path)
            for row in rows_there:
                self.assertEqual((row["origin_record"], row["origin_stamp"]), (first, first_stamp), path)
        # and when the original record is gone, a carried copy still names the first origin
        os.remove(first)
        time.sleep(1.1)
        _v, _c, fourth = self.rehearse_reusing(rows)
        rows_there = CR.load_record(fourth)[0]["rows"]
        self.assertEqual(len(rows_there), 3)
        for row in rows_there:
            self.assertEqual((row["origin_record"], row["origin_stamp"]), (first, first_stamp))

    def test_a_rehearsal_that_reused_some_rows_carries_those_and_keeps_the_fresh_ones(self):
        first, rows = self.first_record()
        fresh = self.row("virgin-gate", key="e" * 40)
        out = "READY for %s\n%s%s" % (V, self.reused_lines(rows, ("export-real-home",)), CR.row_line(fresh) + "\n")
        _v, _c, path = CR.run_rehearsal(self.repo, V, runner=RehearsalRig(out=out))
        record = CR.load_record(path)[0]
        by_name = dict((r["row"], r) for r in record["rows"])
        self.assertEqual(sorted(by_name), ["export-real-home", "virgin-gate"])
        self.assertEqual(by_name["export-real-home"]["origin_record"], first)
        self.assertNotIn("origin_record", by_name["virgin-gate"])

    def test_a_reused_row_no_record_holds_or_whose_log_changed_is_not_carried_and_the_note_says_so(self):
        first, rows = self.first_record()
        ghost = "REUSED export-real-home %s /nowhere/row.log\n" % KEY[:12]
        _v, _c, path = self.rehearse_reusing(rows, names=("virgin-gate",), extra=ghost)
        record = self.load(path)
        self.assertEqual([r["row"] for r in record["rows"]], ["virgin-gate"])
        self.assertIn("held by no record", record["note"])
        with open(rows[0]["log_path"], "a") as fh:
            fh.write("altered\n")
        _v, _c, path = self.rehearse_reusing(rows, names=(rows[0]["row"],))
        record = self.load(path)
        self.assertEqual(record["rows"], [])
        self.assertIn("does not hash", record["note"])

    def test_a_rehearsal_that_reused_nothing_carries_nothing(self):
        _first, _rows = self.first_record()
        _v, _c, path = CR.run_rehearsal(self.repo, V, runner=RehearsalRig())
        self.assertEqual(self.load(path)["rows"], [])

    def test_a_local_rehearsal_carries_nothing(self):
        _first, rows = self.first_record()
        out = "LOCAL-READY for %s\n%s" % (V, self.reused_lines(rows))
        _v, _c, path = CR.run_rehearsal(self.repo, V, runner=RehearsalRig(out=out), local=True)
        self.assertEqual(self.load(path)["rows"], [])


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class TestCS12D(RowFixture):

    def carried(self, hours_old, name="export-real-home", record="/ev/origin.json"):
        return CR.carry_reused_rows([self.row(name)], record, iso(time.time() - hours_old * 3600))

    def fresh_record_with(self, carried, rows=()):
        """A READY record finished just now whose rows are `rows` then the `carried` ones."""
        path = self.record(rows=list(rows), carried=carried)
        return CR.load_record(path)[0], path

    # --- row_origin_stamp ---------------------------------------------------------------------------------------

    def test_row_origin_stamp_reads_a_carried_rows_stamp(self):
        row = self.carried(3)[0]
        self.assertEqual(CR.row_origin_stamp(row), (row["origin_stamp"], ""))

    def test_row_origin_stamp_is_none_on_a_fresh_row(self):
        self.assertEqual(CR.row_origin_stamp(self.row()), (None, ""))

    def test_row_origin_stamp_refuses_an_empty_carried_stamp(self):
        row = dict(self.row(), origin_record="/ev/o.json")
        self.assertEqual(CR.row_origin_stamp(row), (None, "the carried row has no origin_stamp"))
        stamp, why = CR.row_origin_stamp(dict(row, origin_stamp=""))
        self.assertIsNone(stamp)
        self.assertIn("unreadable origin_stamp", why)

    def test_row_origin_stamp_refuses_a_non_string_carried_stamp(self):
        for bad in (5, 5.5, True, None, ["2026-10-20T00:00:00Z"], {"a": 1}):
            with self.subTest(bad=bad):
                stamp, why = CR.row_origin_stamp(dict(self.row(), origin_record="/o", origin_stamp=bad))
                self.assertIsNone(stamp)
                self.assertIn("unreadable origin_stamp", why)

    def test_row_origin_stamp_refuses_an_unreadable_carried_stamp(self):
        for bad in ("yesterday", "2026-13-45T99:99:99Z", "  ", "2026-10-20"):
            with self.subTest(bad=bad):
                stamp, why = CR.row_origin_stamp(dict(self.row(), origin_record="/o", origin_stamp=bad))
                self.assertIsNone(stamp)
                self.assertEqual(why, "the carried row has an unreadable origin_stamp: %r" % bad)

    def test_row_origin_stamp_never_raises(self):
        for bad in (None, "row", 5, ["a"]):
            stamp, why = CR.row_origin_stamp(bad)
            self.assertEqual((stamp, why), (None, "a row is not an object"))
        self.assertEqual(CR.row_origin_stamp({}), (None, ""))

    # --- reusable_row -------------------------------------------------------------------------------------------

    def test_reusable_row_reads_age_from_origin_stamp(self):
        record, _p = self.fresh_record_with(self.carried(5))
        row, why = CR.reusable_row(record, "export-real-home", KEY, "real")
        self.assertEqual(why, "")
        self.assertIsNotNone(row)
        # the same carried row, asked a day later than its origin plus the limit, is stale though the record is fresh
        row, why = CR.reusable_row(record, "export-real-home", KEY, "real", now=time.time() + 20 * 3600)
        self.assertIsNone(row)
        self.assertIn("stale", why)

    def test_a_fresh_record_with_an_old_carried_row_reruns(self):
        # the base returned the row here: a fresh finished_at made a 25 hour old measurement look new
        carried = self.carried(25, record="/ev/origin-25h.json")
        record, _p = self.fresh_record_with(carried)
        row, why = CR.reusable_row(record, "export-real-home", KEY, "real")
        self.assertIsNone(row)
        self.assertEqual(why, "the carried export-real-home row is stale: origin /ev/origin-25h.json finished %s, "
                              "older than 24 hours" % carried[0]["origin_stamp"])

    def test_a_mixed_record_reuses_the_fresh_row_and_reruns_the_old_one(self):
        carried = self.carried(25, "export-real-home") + self.carried(2, "virgin-gate")
        record, _p = self.fresh_record_with(carried)
        self.assertIsNone(CR.reusable_row(record, "export-real-home", KEY, "real")[0])
        self.assertIsNotNone(CR.reusable_row(record, "virgin-gate", KEY, "empty")[0])

    def test_the_limit_is_exact_and_a_future_origin_refuses(self):
        now = time.time()
        record, _p = self.fresh_record_with(CR.carry_reused_rows([self.row()], "/o", iso(now - 24 * 3600 - 5)))
        self.assertIsNone(CR.reusable_row(record, "export-real-home", KEY, "real", now=now)[0])
        record, _p = self.fresh_record_with(CR.carry_reused_rows([self.row()], "/o", iso(now - 24 * 3600 + 60)))
        self.assertIsNotNone(CR.reusable_row(record, "export-real-home", KEY, "real", now=now)[0])
        record, _p = self.fresh_record_with(CR.carry_reused_rows([self.row()], "/o", iso(now + 2 * 3600)))
        row, why = CR.reusable_row(record, "export-real-home", KEY, "real", now=now)
        self.assertIsNone(row)
        self.assertIn("from the future", why)

    def test_reusable_row_refuses_an_empty_origin_stamp(self):
        # never replaced by the record's own fresh stamp
        row = dict(self.row(), origin_record="/ev/o.json")
        record = {"verdict": "READY", "finished_at": iso(time.time()), "rows": [row]}
        got, why = CR.reusable_row(record, "export-real-home", KEY, "real")
        self.assertIsNone(got)
        self.assertEqual(why, "the carried row has no origin_stamp")
        for bad in ("", 5, "later"):
            record["rows"] = [dict(row, origin_stamp=bad)]
            got, why = CR.reusable_row(record, "export-real-home", KEY, "real")
            self.assertIsNone(got, bad)
            self.assertTrue(why)

    def test_reusable_row_still_checks_the_log_hash(self):
        carried = self.carried(1)
        record, _p = self.fresh_record_with(carried)
        with open(carried[0]["log_path"], "a") as fh:
            fh.write("edited\n")
        got, why = CR.reusable_row(record, "export-real-home", KEY, "real")
        self.assertIsNone(got)
        self.assertIn("altered after it was recorded", why)

    def test_a_fresh_row_is_still_judged_by_the_record_stamp(self):
        old = CR.load_record(self.record(rows=[self.row()], now=time.time() - 30 * 3600))[0]
        got, why = CR.reusable_row(old, "export-real-home", KEY, "real")
        self.assertIsNone(got)
        self.assertIn("the record is stale", why)
        # a record with an old stamp and a carried row: a fresh row of the same record still reads the record stamp
        mixed = CR.load_record(self.record(rows=[self.row("virgin-gate")], carried=self.carried(1),
                                           now=time.time() - 30 * 3600))[0]
        got, why = CR.reusable_row(mixed, "virgin-gate", KEY, "empty")
        self.assertIsNone(got)
        self.assertIn("the record is stale", why)
        self.assertIsNotNone(CR.reusable_row(mixed, "export-real-home", KEY, "real", now=time.time())[0])

    def test_a_stale_record_without_carried_rows_keeps_its_old_first_reason(self):
        old = CR.load_record(self.record(rows=[self.row()], now=time.time() - 30 * 3600))[0]
        for key, home in ((KEY[:-1] + "b", "real"), (KEY, "empty")):
            got, why = CR.reusable_row(old, "export-real-home", key, home)
            self.assertIsNone(got)
            self.assertIn("the record is stale", why)

    def test_reusable_check_row_ages_a_carried_row_by_its_origin(self):
        # the rehearsal's own reader (--check reuse) gets the same rule, else the age is laundered one step earlier
        self.record(rows=[], carried=self.carried(25, record="/ev/origin-25h.json"))
        got, why = CR.reusable_check_row(V, "export-real-home", KEY, "real", self.ev)
        self.assertIsNone(got)
        self.assertIn("the carried export-real-home row is stale: origin /ev/origin-25h.json", why)
        shutil.rmtree(self.ev)
        self.record(rows=[], carried=self.carried(2))
        self.assertEqual(CR.reusable_check_row(V, "export-real-home", KEY, "real", self.ev)[1], "")

    def test_reusable_check_row_refuses_an_invalid_origin_stamp(self):
        path = self.record(rows=[], carried=self.carried(1))
        self.rewrite(path, rows=[dict(r, origin_stamp="yesterday") for r in self.load(path)["rows"]])
        got, why = CR.reusable_check_row(V, "export-real-home", KEY, "real", self.ev)
        self.assertIsNone(got)
        self.assertIn("unreadable origin_stamp", why)

    # --- export_public.reused_required_fast (it reads the row through reusable_row) -----------------------------

    def test_reused_required_fast_reads_age_from_origin_stamp(self):
        import export_public as EP
        _r, path = self.fresh_record_with(self.carried(25, record="/ev/origin-25h.json"))
        ok, why = EP.reused_required_fast(path, KEY)
        self.assertFalse(ok)
        self.assertIn("the carried export-real-home row is stale: origin /ev/origin-25h.json", why)
        _r, path = self.fresh_record_with(self.carried(2))
        ok, line = EP.reused_required_fast(path, KEY)
        self.assertTrue(ok, line)
        self.assertTrue(line.startswith("REUSED export-real-home %s " % KEY[:12]))

    def test_reused_required_fast_refuses_an_invalid_origin_stamp(self):
        import export_public as EP
        carried = self.carried(1)
        path = self.record(carried=carried)
        self.rewrite(path, rows=[dict(r, origin_stamp="yesterday") for r in self.load(path)["rows"]])
        ok, why = EP.reused_required_fast(path, KEY)
        self.assertFalse(ok)
        self.assertTrue(why)

    # --- the second rehearsal's record, end to end -----------------------------------------------------------

    def test_age_survives_a_chain_of_rehearsals(self):
        # a 25 hour old original, reused by a rehearsal that ran 'fresh enough' by the old rule, never turns young
        first, rows = self.first_record(hours_ago=23.0)
        first_stamp = self.load(first)["finished_at"]
        _v, _c, second = CR.run_rehearsal(self.repo, V, runner=RehearsalRig(
            out="READY for %s\n%s" % (V, self.reused_lines(rows))))
        record = CR.load_record(second)[0]
        # two hours later the original is 25 hours old although the second record is two hours old
        later = time.time() + 2 * 3600
        for name, home in HOMES.items():
            got, why = CR.reusable_row(record, name, KEY, home, now=later)
            self.assertIsNone(got, name)
            self.assertIn("origin %s finished %s" % (first, first_stamp), why)



class TestCS12Fix(Fixture):
    """Reviewer B1: scripts/cut.py check_mode is the one place that knows the nested chain worktree's tip."""

    def run_check(self, exit_rule="", local=False):
        made = {}

        def chain(rehearsal, version, runner, **kw):
            for subject in (BUMP, NOTE):
                with open(os.path.join(rehearsal, "bump.txt"), "a") as fh:
                    fh.write(subject + "\n")
                self.git("add", "-A", cwd=rehearsal)
                self.git("commit", "-q", "-m", subject, cwd=rehearsal)
            made["tip"] = self.git("rev-parse", "HEAD", cwd=rehearsal)
            made["tree"] = self.git("rev-parse", "HEAD^{tree}", cwd=rehearsal)
            return [], []
        out = io.StringIO()
        with mock.patch.object(C, "readiness", return_value=[]), \
                mock.patch.object(C, "reusable_preflight", return_value=("", "none")), \
                mock.patch.object(C, "chain", side_effect=chain), \
                mock.patch.object(C, "rehearsed_exit_refusal", return_value=exit_rule), \
                contextlib.redirect_stdout(out):
            code = C.check_mode(self.repo, V, (None, None, "absent"), ["scripts/"], None, local=local)
        return code, out.getvalue(), made

    def test_check_mode_prints_the_nested_chains_tip_after_the_exit_rule(self):
        code, out, made = self.run_check()
        lines = [ln for ln in out.splitlines() if ln.startswith(CR.TIP_PREFIX)]
        self.assertEqual(lines, ["%s%s %s" % (CR.TIP_PREFIX, made["tip"], made["tree"])], out)
        self.assertNotEqual(made["tip"], self.cand)
        self.assertLess(out.index("exit rule: the commits the chain made"), out.index(CR.TIP_PREFIX))
        self.assertEqual(code, C.EXIT_OK, out)

    def test_check_mode_prints_no_tip_when_the_exit_rule_refuses(self):
        code, out, _made = self.run_check(exit_rule="a commit is not the cut's own")
        self.assertNotIn(CR.TIP_PREFIX, out)
        self.assertEqual(code, C.EXIT_REFUSED)

    def test_check_mode_prints_no_tip_for_a_local_check(self):
        _code, out, _made = self.run_check(local=True)
        self.assertNotIn(CR.TIP_PREFIX, out)

    def test_the_line_check_mode_prints_is_the_line_the_rehearsal_reads(self):
        _code, out, made = self.run_check()
        tip, tree, why = CR._tip_from_output(self.repo, V, self.cand, out)
        self.assertEqual((tip, tree, why), (made["tip"], made["tree"], ""))

    def test_rehearsed_tip_reads_head_and_tree_or_nothing(self):
        self.assertEqual(C.rehearsed_tip(self.repo), (self.cand, self.tree))
        self.assertEqual(C.rehearsed_tip(os.path.join(self.base, "no-such-dir")), ("", ""))
        self.assertEqual(C.rehearsed_tip(self.base), ("", ""))
        failing = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "a" * 40 + "\n", "")
        self.assertEqual(C.rehearsed_tip(self.repo, failing), ("", ""))
        worked = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "b" * 40 + "\n", "")
        self.assertEqual(C.rehearsed_tip(self.repo, worked), ("b" * 40, "b" * 40))


if __name__ == "__main__":
    unittest.main()
