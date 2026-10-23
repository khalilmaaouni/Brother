"""LC6: the run lands one unit with its receipt and branch, and a rejected
review pushes nothing (A11, A12).

One TestCase class per sub unit, so the landing facts, the receipt's own
landing keys, the push choke point and the unit's gate wiring each carry
their own proof. This file runs alone and also runs whole.
"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BROTHER_RUN = os.path.join(HERE, "brother_run.py")
RECEIPT_DOOR = os.path.join(HERE, "receipt_door.py")
GATE_FILE = os.path.join(HERE, "test_receipt_door.py")
import receipt_door  # noqa: E402
import brother_run  # noqa: E402

NODATA = "NO-DATA"


def _verified(uid):
    return {"id": uid, "state": "verified"}


class TheLandingFactsAreReadNeverGuessed(unittest.TestCase):
    """LC6.a: receipt_door.landing_facts and landing_lines."""

    def test_one_landed_unit_reads_its_own_id_and_count(self):
        facts = receipt_door.landing_facts(
            [_verified("U1")], branch="main", revision="abc123")
        self.assertEqual(sorted(facts), ["branch", "missing", "revision",
                                         "unit", "units"])
        self.assertEqual(facts["units"], "1")
        self.assertEqual(facts["unit"], "U1")
        self.assertEqual(facts["branch"], "main")
        self.assertEqual(facts["revision"], "abc123")
        self.assertEqual(facts["missing"], "")
        for value in facts.values():
            self.assertIsInstance(value, str)
        self.assertEqual(receipt_door.landing_facts([]),
                         {"units": "0", "unit": NODATA, "branch": NODATA,
                          "revision": NODATA,
                          "missing": "unit, branch, revision"})

    def test_a_refused_or_unmarked_unit_never_lands(self):
        facts = receipt_door.landing_facts([
            {"id": "R", "state": "refused", "reason": "x"},
            {"id": "N", "state": "no-data"},
            {"id": "Z", "state": "something-a-newer-module-wrote"},
            {"id": "Q"}, {}, _verified("V")])
        self.assertEqual((facts["units"], facts["unit"]), ("1", "V"))
        none = receipt_door.landing_facts([{"id": "R", "state": "refused"}])
        self.assertEqual((none["units"], none["unit"]), ("0", NODATA))

    def test_two_landed_units_are_named_in_receipt_order(self):
        facts = receipt_door.landing_facts([
            _verified("B"), {"id": "R", "state": "refused"}, _verified("A")])
        self.assertEqual((facts["units"], facts["unit"]), ("2", "B, A"))
        # id keys: uid, then unit, then id; a receipt naming none reads NO-DATA
        facts = receipt_door.landing_facts([
            {"uid": "U", "id": "I", "state": "verified"},
            {"unit": " ", "id": "J", "state": "verified"},
            {"state": "verified"}])
        self.assertEqual(facts["unit"], "U, J, " + NODATA)
        # one verified, one refused: the count is the landed count, not len()
        facts = receipt_door.landing_facts(
            [_verified("A"), {"id": "R", "state": "refused"}])
        self.assertEqual((facts["units"], facts["unit"]), ("1", "A"))
        # idempotent: the same list twice reads equal facts
        rs = [_verified("A")]
        self.assertEqual(receipt_door.landing_facts(rs, "b", "r"),
                         receipt_door.landing_facts(rs, "b", "r"))

    def test_the_same_id_listed_twice_lands_once(self):
        facts = receipt_door.landing_facts(
            [_verified("A"), _verified("A"), _verified("B")])
        self.assertEqual((facts["units"], facts["unit"]), ("2", "A, B"))

    def test_a_missing_branch_or_revision_reads_no_data(self):
        facts = receipt_door.landing_facts([_verified("U")])
        self.assertEqual(facts["missing"], "branch, revision")
        facts = receipt_door.landing_facts([_verified("U")], branch="   ",
                                           revision=" r1 ")
        self.assertEqual(facts["branch"], NODATA)
        self.assertEqual(facts["revision"], "r1")
        self.assertEqual(facts["missing"], "branch")
        facts = receipt_door.landing_facts([], branch="b", revision="r")
        self.assertEqual(facts["missing"], "unit")

    def test_a_hostile_argument_is_refused_by_name(self):
        for bad in (None, "text", {"a": 1}, 3):
            with self.assertRaises(ValueError) as ctx:
                receipt_door.landing_facts(bad)
            self.assertIn(type(bad).__name__, str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            receipt_door.landing_facts([_verified("U"), "x"])
        self.assertIn("str", str(ctx.exception))
        self.assertEqual(receipt_door.landing_facts(())["units"], "0")
        for bad in (True, 1, 1.5, ["b"], {"b": 1}):
            for key in ("branch", "revision"):
                with self.assertRaises(ValueError) as ctx:
                    receipt_door.landing_facts([], **{key: bad})
                self.assertIn(key, str(ctx.exception))
                self.assertIn(type(bad).__name__, str(ctx.exception))
        with self.assertRaises(ValueError):
            receipt_door.landing_lines(["not", "a", "dict"])

    def test_the_lines_keep_no_data_and_carry_no_newline(self):
        lines = receipt_door.landing_lines(receipt_door.landing_facts(
            [_verified("U")], branch="main", revision="abc"))
        self.assertEqual(lines, ["the run landed 1 unit(s)",
                                 "the landed unit: U",
                                 "the branch it landed on: main",
                                 "the revision it landed at: abc"])
        lines = receipt_door.landing_lines(
            receipt_door.landing_facts([_verified("U")]))
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[4], "not recorded: branch, revision")
        lines = receipt_door.landing_lines(
            {"units": "", "unit": None, "branch": "  ", "revision": 7})
        self.assertEqual(lines[0], "the run landed %s unit(s)" % NODATA)
        for line in lines:
            self.assertTrue(line)
        self.assertEqual(lines[2], "the branch it landed on: " + NODATA)
        # an id carrying a line break never splits a line
        lines = receipt_door.landing_lines(receipt_door.landing_facts(
            [_verified("A\r\nB\nC")], branch="x\ny", revision="r"))
        for line in lines:
            self.assertNotIn("\n", line)
            self.assertNotIn("\r", line)


def _git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                          text=True, check=True).stdout.strip()


def _make_repo(tmp, branch="lc6-branch"):
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    _git(repo, "init", "-q")
    _git(repo, "checkout", "-q", "-b", branch)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.test",
         "commit", "-q", "--allow-empty", "-m", "first")
    return repo


class _Result(object):
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


class TheRunsReceiptNamesTheUnitItsBranchAndItsRevision(unittest.TestCase):
    """LC6.b: brother_run._run_branch, _landing_keys and _write_receipt."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lc6b-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.run_dir = os.path.join(self.tmp, "run")
        os.makedirs(self.run_dir)
        self.repo = _make_repo(self.tmp)

    def _put(self, name, doc):
        with open(os.path.join(self.run_dir, name), "w",
                  encoding="utf-8") as fh:
            if isinstance(doc, str):
                fh.write(doc)
            else:
                json.dump(doc, fh)

    def _write(self, receipts=None, body=None):
        """_write_receipt over this run directory, receipt_record stubbed to
        a fixed body so the keys the file carries are exactly known."""
        body = {"scope": {}, "old_key": 1} if body is None else body
        with mock.patch.object(brother_run.receipt_door, "receipt_record",
                               return_value=dict(body)):
            path, why = brother_run._write_receipt(
                self.run_dir,
                [_verified("U1")] if receipts is None else receipts,
                "the report")
        self.assertEqual(why, "")
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_a_real_run_leaves_a_receipt_naming_the_one_unit_it_landed(self):
        self._put("target.json", {"cwd": self.repo})
        self._put("capsule.json", {"canonical_revision": "rev-1"})
        got = self._write()
        self.assertEqual(got["landing"], {
            "units": "1", "unit": "U1", "branch": "lc6-branch",
            "revision": "rev-1", "missing": ""})
        self.assertEqual(got["landing_lines"][1], "the landed unit: U1")

    def test_the_receipt_names_the_branch_the_run_ran_on(self):
        self.assertEqual(brother_run._run_branch(self.repo), "lc6-branch")
        self.assertEqual(brother_run._run_branch(self.repo, runner=lambda a, **k:
                         _Result("topic\n")), "topic")
        self.assertEqual(brother_run._run_branch(os.fspath(self.repo)),
                         "lc6-branch")
        self.assertEqual(brother_run._run_branch(None), NODATA)
        calls = []

        def runner(argv, **kw):
            calls.append((argv, kw))
            return _Result("main\n")
        self.assertEqual(brother_run._run_branch(self.repo, runner), "main")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], ["git", "rev-parse", "--abbrev-ref",
                                       "HEAD"])
        self.assertEqual(calls[0][1]["cwd"], self.repo)
        self.assertIs(calls[0][1]["capture_output"], True)
        self.assertIs(calls[0][1]["text"], True)

    def test_the_receipt_names_the_revision_the_run_landed_at(self):
        self._put("target.json", {"cwd": self.repo})
        self._put("capsule.json", {"canonical_revision": "abc123"})
        self.assertEqual(self._write()["landing"]["revision"], "abc123")
        self._put("capsule.json", {"canonical_revision": 12})
        landing = self._write()["landing"]
        self.assertEqual(landing["revision"], NODATA)
        self.assertEqual(landing["missing"], "revision")

    def test_a_run_directory_with_no_target_file_reads_no_data(self):
        got = self._write()
        self.assertEqual(got["landing"]["branch"], NODATA)
        self.assertEqual(got["landing"]["unit"], "U1")
        self.assertIn("branch", got["landing"]["missing"])
        # a torn target record, and a cwd that is a number or a list
        self._put("target.json", "{not json")
        self.assertEqual(self._write()["landing"]["branch"], NODATA)
        for bad in (7, ["x"], True, "", {"a": 1}):
            self._put("target.json", {"cwd": bad})
            self.assertEqual(self._write()["landing"]["branch"], NODATA, bad)

    def test_a_failed_or_emptied_git_reads_no_data_never_a_guess(self):
        for result in (_Result("main\n", 128), _Result("", 0),
                       _Result("HEAD\n", 0), _Result("  \n", 0), object()):
            self.assertEqual(brother_run._run_branch(
                self.repo, runner=lambda a, **k: result), NODATA, result)

        def boom(argv, **kw):
            raise OSError("git is not installed")
        self.assertEqual(brother_run._run_branch(self.repo, runner=boom),
                         NODATA)

        def stalled(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 30)
        self.assertEqual(brother_run._run_branch(self.repo, runner=stalled),
                         NODATA)
        self.assertEqual(brother_run._run_branch(
            os.path.join(self.tmp, "no-such-dir")), NODATA)
        _git(self.repo, "checkout", "-q", "--detach")
        self.assertEqual(brother_run._run_branch(self.repo), NODATA)

    def test_a_hostile_cwd_is_refused_by_name(self):
        for bad in (True, 3, ["x"], {"a": 1}, 1.5):
            with self.assertRaises(ValueError) as ctx:
                brother_run._run_branch(bad)
            self.assertIn(type(bad).__name__, str(ctx.exception))

    def test_the_receipt_carries_the_landing_lines_beside_the_facts(self):
        self._put("target.json", {"cwd": self.repo})
        got = self._write()
        self.assertEqual(got["landing_lines"],
                         brother_run.receipt_door.landing_lines(got["landing"]))
        self.assertGreaterEqual(len(got["landing_lines"]), 4)
        self.assertEqual(got["landing_lines"][2],
                         "the branch it landed on: lc6-branch")

    def test_a_target_or_capsule_reader_answering_a_wrong_type_reads_no_data(self):
        # _landing_keys re-checks what its two readers hand back, so a reader
        # that changes shape later cannot trip _run_branch's own refusal
        for bad in (5, ["x"], {"cwd": 5}, {"cwd": None}, True, {}):
            with mock.patch.object(brother_run, "_read_run_target",
                                   return_value=bad):
                self.assertEqual(brother_run._landing_keys(
                    self.run_dir, [])["landing"]["branch"], NODATA, bad)
        with mock.patch.object(brother_run, "_read_run_target",
                               return_value={"cwd": self.repo}):
            self.assertEqual(brother_run._landing_keys(
                self.run_dir, [])["landing"]["branch"], "lc6-branch")
        for bad in (12, ["r"], {"a": 1}, True):
            with mock.patch.object(brother_run, "_checkpoint_canonical_revision",
                                   return_value=bad):
                self.assertEqual(brother_run._landing_keys(
                    self.run_dir, [])["landing"]["revision"], NODATA, bad)

    def test_the_receipt_keeps_every_key_it_wrote_before(self):
        got = self._write(body={"scope": {"changed": []}, "old_key": 1})
        self.assertEqual(got["old_key"], 1)
        self.assertEqual(got["scope"], {"changed": []})
        self.assertEqual(got["report"], "the report")
        self.assertIn("host_capability", got)
        self.assertEqual(set(got), {"scope", "old_key", "report",
                                    "host_capability", "landing",
                                    "landing_lines"})
        # a second write over the same run directory accumulates nothing
        self.assertEqual(self._write(body={"scope": {"changed": []},
                                           "old_key": 1}), got)

    def test_the_receipt_is_written_when_the_landing_facts_cannot_be_read(self):
        # a real run directory: the real receipt_record reads its own Work
        # document, the real keys stay, and no repository exists at all
        self._put("W-w1.json", {"outcome": "o", "work_id": "w1", "rows": [
            {"id": "A", "objective": "x", "done_check": "true",
             "owns": ["m.py"], "status": "DONE", "check_passed_before": False,
             "files_changed_by_unit": []}]})
        path, why = brother_run._write_receipt(self.run_dir, [], "r")
        self.assertEqual(why, "", why)
        with open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        for key in ("scope", "intent", "evidence", "unproven", "continuity",
                    "report", "host_capability"):
            self.assertIn(key, got)
        self.assertEqual(got["landing"]["units"], "0")
        self.assertEqual(got["landing"]["branch"], NODATA)


ACCEPT = {"screen": "release", "choice": "accept", "by": "a person",
          "at": "2026-10-20"}
HOLD = {"screen": "release", "choice": "hold", "by": "a person",
        "at": "2026-10-20"}


def _conclusion(release_answer):
    return brother_run.delivery_conclusion(
        [{"id": "U", "state": "verified", "exit_code": 0}], [], [], True,
        [], release_answer)


class _Recorder(object):
    """A runner that appends the argv it is handed and answers as told."""

    def __init__(self, returncode=0, raises=None):
        self.calls, self.returncode, self.raises = [], returncode, raises

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        if self.raises is not None:
            raise self.raises
        return _Result("", self.returncode)


class ARejectedReviewPushesNothing(unittest.TestCase):
    """LC6.c: may_push_branch, push_keys, push_branch and the push decision
    delivery_conclusion carries. The review's answer is the shape the run
    reads (loom.read_answer: None or a dict whose choice is one of
    loom.CHOICES, accept or hold), never a bare word."""

    REJECTED = "nothing is pushed: the review rejected the delivery"
    NO_ANSWER = "nothing is pushed: the review gave no answer"
    ACCEPTED = "the review accepted the delivery; the branch may be pushed"

    def test_the_reject_word_never_pushes(self):
        self.assertEqual(brother_run.may_push_branch(HOLD),
                         (False, self.REJECTED))
        self.assertEqual(brother_run.may_push_branch({"screen": "release", "choice": " HOLD "}),
                         (False, self.REJECTED))
        self.assertEqual(brother_run.push_keys(HOLD, "DONE", True),
                         {"push_allowed": False, "push_reason": self.REJECTED})

    def test_only_the_accept_word_opens_the_gate(self):
        self.assertEqual(brother_run.may_push_branch(ACCEPT),
                         (True, self.ACCEPTED))
        self.assertEqual(brother_run.may_push_branch({"screen": "release", "choice": " Accept\n"}),
                         (True, self.ACCEPTED))
        self.assertEqual(brother_run.push_keys(ACCEPT, "DONE", True),
                         {"push_allowed": True, "push_reason": self.ACCEPTED})
        # asking twice returns the same pair
        self.assertEqual(brother_run.may_push_branch(HOLD),
                         brother_run.may_push_branch(HOLD))

    def test_no_answer_at_all_never_pushes(self):
        for answer in (None, {}, {"choice": ""}, {"choice": "  "},
                       {"choice": None}, {"choice": 1}, {"by": "x"}):
            allowed, reason = brother_run.may_push_branch(answer)
            self.assertIs(allowed, False, answer)
            self.assertTrue(reason.startswith("nothing is pushed:"), reason)
        self.assertEqual(brother_run.may_push_branch(None),
                         (False, self.NO_ANSWER))

    def test_a_word_outside_the_release_values_never_pushes(self):
        for word in ("yes", "proceed", "reject", "decision_recorded", "acceptx",
                     "accept hold"):
            self.assertEqual(brother_run.may_push_branch({"screen": "release", "choice": word}),
                             (False, "nothing is pushed: the review answered "
                                     "%s, which is not an acceptance" % word))
        # a word carrying a line break is named on one line only
        allowed, reason = brother_run.may_push_branch(
            {"screen": "release", "choice": "no\nforged: line"})
        self.assertIs(allowed, False)
        self.assertNotIn("\n", reason)

    def test_a_hostile_answer_is_refused_by_name(self):
        for bad in (True, 3, 1.5, ["accept"], "accept", ("accept",)):
            with self.assertRaises(ValueError) as ctx:
                brother_run.may_push_branch(bad)
            self.assertIn(type(bad).__name__, str(ctx.exception))
        # the settled conclusion never raises: a hostile answer is a closed gate
        for bad in (True, 3, ["accept"], "accept"):
            conclusion = _conclusion(bad)
            self.assertIs(conclusion["push_allowed"], False)
            self.assertTrue(conclusion["push_reason"])

    def test_the_conclusion_carries_the_push_decision_it_made(self):
        base_keys = {"state", "units", "counts", "release", "next", "reason"}
        for answer, allowed, release in ((ACCEPT, True, "DECISION_RECORDED"),
                                         (HOLD, False, "DECISION_RECORDED"),
                                         (None, False, "NOT_REQUESTED")):
            conclusion = _conclusion(answer)
            self.assertEqual(set(conclusion), base_keys | {"push_allowed",
                                                           "push_reason"})
            self.assertIs(conclusion["push_allowed"], allowed)
            self.assertEqual(conclusion["push_reason"],
                             brother_run.push_keys(answer, "DONE", True)["push_reason"])
            # every key it returned before keeps its value
            self.assertEqual(conclusion["state"], "DONE")
            self.assertEqual(conclusion["units"], 1)
            self.assertEqual(conclusion["counts"],
                             {"PASS": 1, "FAIL": 0, "NO-DATA": 0})
            self.assertEqual(conclusion["release"], release)
            self.assertEqual(conclusion["next"], "review-receipt")
            self.assertEqual(conclusion["reason"],
                             "1 unit(s) verified and none was refused")
        # the result line and the exit mapping read what they read before
        full = _conclusion(ACCEPT)
        stripped = {k: v for k, v in full.items() if not k.startswith("push_")}
        self.assertEqual(brother_run.format_result_line(full),
                         brother_run.format_result_line(stripped))
        self.assertTrue(brother_run.format_result_line(full).startswith(
            brother_run.RESULT_PREFIX + "DONE | units=1 PASS=1 FAIL=0"))
        self.assertEqual(brother_run.EXIT_FOR_STATE[full["state"]], 0)

    def test_a_renamed_release_value_cannot_open_the_gate(self):
        with mock.patch.object(brother_run.loom, "CHOICES", ("hold",)):
            self.assertEqual(brother_run.may_push_branch(ACCEPT),
                             (False, self.NO_ANSWER))
        with mock.patch.object(brother_run.loom, "CHOICES", ("approve", "hold")):
            self.assertEqual(brother_run.may_push_branch(ACCEPT),
                             (False, self.NO_ANSWER))
        with mock.patch.object(brother_run.loom, "CHOICES",
                               ("accept", "hold", "defer")):
            self.assertEqual(brother_run.may_push_branch(ACCEPT),
                             (True, self.ACCEPTED))
            self.assertEqual(brother_run.may_push_branch({"screen": "release", "choice": "defer"}),
                             (False, "nothing is pushed: the review answered "
                                     "defer, which is not an acceptance"))

    def test_a_rejected_conclusion_refuses_before_any_git_push(self):
        repo = tempfile.mkdtemp(prefix="lc6c-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        for conclusion in (_conclusion(HOLD), _conclusion(None),
                           {}, {"push_allowed": False}, {"push_allowed": None},
                           {"push_allowed": 1}, {"push_allowed": "proceed"},
                           {"push_allowed": "True"}, {"push_allowed": {}},
                           {"push_allowed": [True]}):
            runner = _Recorder()
            got = brother_run.push_branch(conclusion, "feature", cwd=repo,
                                          runner=runner)
            self.assertEqual(runner.calls, [], conclusion)
            self.assertEqual(set(got), {"pushed", "push_command",
                                        "push_reason"})
            self.assertIs(got["pushed"], False)
            self.assertEqual(got["push_command"], NODATA)
            self.assertTrue(got["push_reason"].startswith("nothing is pushed:"))
        got = brother_run.push_branch(_conclusion(HOLD), "feature", cwd=repo,
                                      runner=_Recorder())
        self.assertEqual(got["push_reason"], self.REJECTED)
        got = brother_run.push_branch({}, "feature", cwd=repo,
                                      runner=_Recorder())
        self.assertEqual(got["push_reason"], "nothing is pushed: the settled "
                                             "conclusion did not allow it")
        got = brother_run.push_branch({"push_allowed": 0, "push_reason": ""},
                                      "feature", cwd=repo, runner=_Recorder())
        self.assertEqual(got["push_reason"], "nothing is pushed: the settled "
                                             "conclusion did not allow it")

    def test_an_accepted_conclusion_reaches_the_push_command_through_the_runner(self):
        repo = tempfile.mkdtemp(prefix="lc6c-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        runner = _Recorder()
        got = brother_run.push_branch(_conclusion(ACCEPT), "feature/x",
                                      cwd=repo, runner=runner)
        self.assertEqual(len(runner.calls), 1)
        argv, kw = runner.calls[0]
        self.assertEqual(argv, ["git", "push", "origin", "feature/x"])
        self.assertEqual(kw["cwd"], repo)
        self.assertIs(kw["capture_output"], True)
        self.assertIs(kw["text"], True)
        self.assertEqual(kw["timeout"], brother_run.PUSH_TIMEOUT_SECONDS)
        self.assertEqual(got, {"pushed": True,
                               "push_command": "git push origin feature/x",
                               "push_reason": self.ACCEPTED})
        # a non-zero exit, a runner that raises and a stalled push
        got = brother_run.push_branch(_conclusion(ACCEPT), "b", cwd=repo,
                                      runner=_Recorder(returncode=1))
        self.assertEqual((got["pushed"], got["push_command"],
                          got["push_reason"]),
                         (False, "git push origin b",
                          "the push did not land: git exited 1"))
        got = brother_run.push_branch(_conclusion(ACCEPT), "b", cwd=repo,
                                      runner=_Recorder(raises=OSError("x")))
        self.assertEqual((got["pushed"], got["push_reason"]),
                         (False, "nothing is pushed: the push did not run"))
        got = brother_run.push_branch(
            _conclusion(ACCEPT), "b", cwd=repo,
            runner=_Recorder(raises=subprocess.TimeoutExpired(["git"], 1)))
        self.assertEqual((got["pushed"], got["push_reason"]),
                         (False, "nothing is pushed: the push did not finish "
                                 "in %s seconds" % brother_run.PUSH_TIMEOUT_SECONDS))
        # the real transport: a local bare remote, pushed to for real
        origin = os.path.join(repo, "origin.git")
        _git(repo, "init", "-q", "--bare", origin)
        work = _make_repo(repo, branch="topic")
        _git(work, "remote", "add", "origin", origin)
        got = brother_run.push_branch(_conclusion(ACCEPT), "topic", cwd=work)
        self.assertEqual(got["push_command"], "git push origin topic")
        self.assertIs(got["pushed"], True, got)
        self.assertEqual(_git(origin, "rev-parse", "--verify", "topic"),
                         _git(work, "rev-parse", "HEAD"))

    def test_an_unnamed_or_unsafe_branch_remote_or_directory_never_pushes(self):
        repo = tempfile.mkdtemp(prefix="lc6c-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        accepted = _conclusion(ACCEPT)
        for branch in (None, "", "  ", 5, ["b"], "-f", "--force", ":main",
                       "a:b", "a b", "a\nb", "+main", "../x", "a/../b"):
            runner = _Recorder()
            got = brother_run.push_branch(accepted, branch, cwd=repo,
                                          runner=runner)
            self.assertEqual(runner.calls, [], branch)
            self.assertIs(got["pushed"], False)
            self.assertEqual(got["push_command"], NODATA)
        got = brother_run.push_branch(accepted, "", cwd=repo,
                                      runner=_Recorder())
        self.assertEqual(got["push_reason"],
                         "nothing is pushed: no branch was named")
        for remote in ("-u", "--mirror", "o:x", "", None, "a b"):
            runner = _Recorder()
            got = brother_run.push_branch(accepted, "b", cwd=repo,
                                          runner=runner, remote=remote)
            self.assertEqual(runner.calls, [], remote)
            self.assertIs(got["pushed"], False)
        for cwd in (None, "", 5, ["x"], True):
            runner = _Recorder()
            got = brother_run.push_branch(accepted, "b", cwd=cwd,
                                          runner=runner)
            self.assertEqual(runner.calls, [], cwd)
            self.assertIs(got["pushed"], False)
            self.assertEqual(got["push_reason"], "nothing is pushed: no "
                             "repository directory was named")
        # an os.PathLike directory is fine
        import pathlib
        runner = _Recorder()
        got = brother_run.push_branch(accepted, "b", cwd=pathlib.Path(repo),
                                      runner=runner)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0][1]["cwd"], repo)

    def test_a_hostile_conclusion_is_refused_by_name(self):
        for bad in (None, "True", ["push_allowed"], 1, True, ()):
            runner = _Recorder()
            with self.assertRaises(ValueError) as ctx:
                brother_run.push_branch(bad, "b", cwd=".", runner=runner)
            self.assertIn(type(bad).__name__, str(ctx.exception))
            self.assertEqual(runner.calls, [])

    def _pushes(self, conclusion):
        """push_branch over a settled conclusion with a recording runner:
        (result, argv calls)."""
        repo = tempfile.mkdtemp(prefix="lc6c-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        runner = _Recorder()
        return (brother_run.push_branch(conclusion, "main", cwd=repo,
                                        runner=runner), runner.calls)

    def test_a_run_that_is_not_done_never_pushes_whatever_the_answer(self):
        verified = {"id": "U", "state": "verified", "exit_code": 0}
        cases = {
            "a refused unit": ([dict(verified, state="refused")],
                               [("U", "check failed")], [], True),
            "a FAIL unit with no refusal entry": (
                [dict(verified, state="refused")], [], [], True),
            "a parked piece": ([verified], [], ["P"], True),
            "a PASS beside a NO-DATA": (
                [verified, {"id": "N", "state": "no-data"}], [], [], True),
            "nothing verified": ([{"id": "N", "state": "no-data"}], [], [],
                                 True),
            "no units at all": ([], [], [], True),
        }
        for name, (receipts, refused, parked, available) in cases.items():
            conclusion = brother_run.delivery_conclusion(
                receipts, refused, parked, available, [], ACCEPT)
            self.assertNotEqual(conclusion["state"], "DONE", name)
            self.assertIs(conclusion["push_allowed"], False, name)
            self.assertIn("not DONE", conclusion["push_reason"], name)
            self.assertIn(conclusion["state"], conclusion["push_reason"], name)
            got, calls = self._pushes(conclusion)
            self.assertEqual(calls, [], name)
            self.assertIs(got["pushed"], False, name)
            self.assertEqual(got["push_reason"], conclusion["push_reason"])

    def test_push_keys_closes_on_a_state_it_does_not_know(self):
        for state in (None, "bogus\nforged", 7, "done", "PARTIAL"):
            keys = brother_run.push_keys(ACCEPT, state, True)
            self.assertIs(keys["push_allowed"], False, state)
            self.assertNotIn("\n", keys["push_reason"])
        self.assertEqual(brother_run.push_keys(ACCEPT, "bogus", True)[
            "push_reason"], "nothing is pushed: the run's state is NO-DATA, "
                            "not DONE")
        self.assertIs(brother_run.push_keys(ACCEPT, "DONE", True)[
            "push_allowed"], True)

    def test_a_run_with_no_receipt_never_pushes_whatever_the_answer(self):
        for available in (False, None, 1, "yes"):
            conclusion = brother_run.delivery_conclusion(
                [{"id": "U", "state": "verified", "exit_code": 0}], [], [],
                available, [], ACCEPT)
            self.assertIs(conclusion["push_allowed"], False, available)
            self.assertEqual(conclusion["push_reason"], "nothing is pushed: "
                             "this run left no readable receipt")
            got, calls = self._pushes(conclusion)
            self.assertEqual(calls, [], available)
        # the same run with its receipt and the accepted answer may push
        got, calls = self._pushes(_conclusion(ACCEPT))
        self.assertEqual(len(calls), 1)

    def test_an_answer_on_the_wrong_screen_never_pushes(self):
        for screen in ("acceptance", "intent", "", None, 7, "Release",
                       "release\nforged"):
            answer = dict(ACCEPT, screen=screen)
            allowed, reason = brother_run.may_push_branch(answer)
            self.assertIs(allowed, False, screen)
            self.assertIn("not on the release screen", reason)
            self.assertNotIn("\n", reason)
            conclusion = _conclusion(answer)
            self.assertIs(conclusion["push_allowed"], False, screen)
            got, calls = self._pushes(conclusion)
            self.assertEqual(calls, [], screen)
        no_screen = {"choice": "accept", "by": "a person"}
        self.assertIs(_conclusion(no_screen)["push_allowed"], False)
        self.assertEqual(brother_run.may_push_branch(no_screen),
                         (False, "nothing is pushed: the answer was recorded "
                                 "on no screen, not on the release screen"))
        self.assertEqual(brother_run.may_push_branch(
            dict(ACCEPT, screen="acceptance"))[1],
            "nothing is pushed: the answer was recorded on the acceptance "
            "screen, not on the release screen")

    def test_the_owned_modules_hold_exactly_one_push_site(self):
        import re
        counts = {}
        for path in (BROTHER_RUN, RECEIPT_DOOR):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            counts[os.path.basename(path)] = (
                text.count('["git", "push", remote, branch]'),
                len(re.findall(r"""["']push["']""", text)))
        self.assertEqual(counts, {"brother_run.py": (1, 1),
                                  "receipt_door.py": (0, 0)})


LANDING_CLASSES = ("TheLandingFactsAreReadNeverGuessed",
                   "TheRunsReceiptNamesTheUnitItsBranchAndItsRevision",
                   "ARejectedReviewPushesNothing",
                   "TheUnitsGateRunsEveryLandingClass")
GATE_MAIN_GUARD = 'if __name__ == "__main__":'


def _gate_import_line(name):
    return ("from test_receipt_door_landing import %s  # noqa: E402,F401"
            % name)


def _collected_ids(suite):
    """The case ids of a suite, walked recursively. A suite holding no case
    yields an empty list, so a refusal names a missing class, never crashes."""
    if isinstance(suite, unittest.TestCase):
        return [suite.id()]
    ids = []
    for item in suite:
        ids.extend(_collected_ids(item))
    return ids


def _load_gate_module():
    """The gate file as a module under a private name: its __main__ guard is
    false, so loading it runs nothing."""
    spec = importlib.util.spec_from_file_location("_lc6_gate_probe", GATE_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(os.path.isfile(GATE_FILE),
                     "scripts/test_receipt_door.py is not in this checkout")
class TheUnitsGateRunsEveryLandingClass(unittest.TestCase):
    """LC6.d: the unit row's own command, test_receipt_door.py, runs every
    landing class beside its own legacy ones."""

    def _gate_text(self):
        with open(GATE_FILE, encoding="utf-8") as fh:
            return fh.read()

    def test_the_gate_file_imports_every_landing_class(self):
        text = self._gate_text()
        for name in LANDING_CLASSES:
            line = _gate_import_line(name)
            self.assertEqual(text.splitlines().count(line), 1,
                             "%s must be imported exactly once by %s"
                             % (name, os.path.basename(GATE_FILE)))

    def test_the_gate_imports_sit_above_the_gates_main_guard(self):
        text = self._gate_text()
        self.assertEqual(text.splitlines().count(GATE_MAIN_GUARD), 1)
        guard = text.index("\n" + GATE_MAIN_GUARD + "\n")
        for name in LANDING_CLASSES:
            at = text.find(_gate_import_line(name))
            self.assertGreaterEqual(at, 0, "%s is not imported" % name)
            self.assertLess(at, guard,
                            "%s is imported below the __main__ guard" % name)

    def test_the_gate_collects_every_landing_class(self):
        module = _load_gate_module()
        suite = unittest.defaultTestLoader.loadTestsFromModule(module)
        ids = _collected_ids(suite)
        for name in LANDING_CLASSES:
            self.assertTrue(any(".%s." % name in i for i in ids),
                            "the gate collects no test of %s" % name)

    def test_the_collected_landing_tests_run_and_none_is_skipped(self):
        module = _load_gate_module()
        suite = unittest.defaultTestLoader.loadTestsFromModule(module)
        # D itself is not re-run here: it is the class doing the asking
        mine = unittest.TestSuite(
            case for case in _flatten(suite)
            if any(".%s." % name in case.id() for name in LANDING_CLASSES[:3]))
        result = unittest.TextTestRunner(stream=io.StringIO()).run(mine)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertEqual(result.skipped, [])
        self.assertGreaterEqual(result.testsRun, 29)

    def test_collected_ids_walks_nested_suites_and_empty_ones(self):
        class _T(unittest.TestCase):
            def test_a(self):
                pass

            def test_b(self):
                pass
        inner = unittest.TestSuite([_T("test_a")])
        nested = unittest.TestSuite([unittest.TestSuite([inner]),
                                     _T("test_b")])
        self.assertEqual(len(_collected_ids(nested)), 2)
        self.assertEqual(_collected_ids(unittest.TestSuite()), [])
        self.assertEqual(_collected_ids(unittest.TestSuite(
            [unittest.TestSuite()])), [])


def _flatten(suite):
    if isinstance(suite, unittest.TestCase):
        yield suite
        return
    for item in suite:
        for case in _flatten(item):
            yield case


if __name__ == "__main__":
    unittest.main()
