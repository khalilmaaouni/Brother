#!/usr/bin/env python3
"""LX1.a1 (docs/plan/specs/LX1.md): the Linux canary record, its job judgement, the minutes stop and the covering
lookup.

HOW. Records are written into a temp evidence folder; the clock is passed in; the billing call is a scripted gh
callable. Nothing reaches the network or the real ~/.claude.

Python 3.9, standard library only. No network. No em or en dashes.
"""
import base64
import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import linux_canary as L  # noqa: E402

TREE = "a" * 40
OTHER = "b" * 40
SHA = "c" * 40
V = "9.9.9"
NOW = 1760000000.0
HOSTILE = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())


def record(tree=TREE, jobs=None, verdict="PASS", finished=NOW - 600, version=V):
    jobs = dict(L.EXPECTED_JOBS) if jobs is None else jobs
    return {"schema": L.SCHEMA, "version": version, "tree": tree, "pushed_sha": SHA, "branch": "canary/x",
            "run_id": 123, "run_url": "https://example.invalid/run/123", "jobs": jobs, "verdict": verdict,
            "detail": "", "minutes_before": 10.0, "started_at": L._iso(finished - 530),
            "finished_at": L._iso(finished), "seconds": 530.0}


def billing(minutes, repo="brother-hub"):
    return json.dumps({"usageItems": [
        {"product": "actions", "unitType": "Minutes", "repositoryName": repo, "grossAmount": minutes * L.LINUX_PER_MIN},
        {"product": "actions", "unitType": "Minutes", "repositoryName": "Brother", "grossAmount": 99.0},
        {"product": "git_lfs", "unitType": "GigabyteHours", "repositoryName": repo, "grossAmount": 50.0}]})


def scripted_gh(usage, login=(0, "someone\n", ""), calls=None):
    def gh(argv):
        if calls is not None:
            calls.append(list(argv))
        if argv == ["api", "user", "--jq", ".login"]:
            return login
        return usage
    return gh


class TheRecordAndItsJudgement(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lx1-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def put(self, rec):
        return L.write_record(rec, self.dir)

    # REQ-LX1-01: judge_jobs, one case per shape
    def test_exact_shape_is_pass(self):
        self.assertEqual(L.judge_jobs({"required-fast-canary": "success", "required-fast": "skipped"})[0], "PASS")

    def test_all_skipped_is_no_data(self):
        self.assertEqual(L.judge_jobs({"required-fast-canary": "skipped", "required-fast": "skipped"})[0], "NO-DATA")

    def test_missing_job_is_no_data(self):
        self.assertEqual(L.judge_jobs({"required-fast-canary": "success"})[0], "NO-DATA")

    def test_required_fast_not_skipped_is_no_data(self):
        self.assertEqual(L.judge_jobs({"required-fast-canary": "success", "required-fast": "success"})[0], "NO-DATA")

    def test_failed_canary_is_fail(self):
        for c in ("failure", "cancelled", "timed_out"):
            self.assertEqual(L.judge_jobs({"required-fast-canary": c, "required-fast": "skipped"})[0], "FAIL", c)

    def test_empty_extra_or_unusual_is_no_data(self):
        for jobs in ({}, dict(L.EXPECTED_JOBS, other="success"),
                     {"required-fast-canary": "weird", "required-fast": "skipped"},
                     {"required-fast-canary": 1, "required-fast": "skipped"}):
            self.assertEqual(L.judge_jobs(jobs)[0], "NO-DATA", jobs)

    # REQ-LX1-02, REQ-LX1-03: covering
    def test_one_pass_record_covers(self):
        path = self.put(record())
        self.assertEqual(L.covering(TREE, self.dir, NOW), (path, ""))
        self.assertTrue(os.path.basename(path).startswith("linux-canary-%s-%s-" % (V, TREE[:12])))

    def test_empty_folder_has_no_record(self):
        path, why = L.covering(TREE, self.dir, NOW)
        self.assertEqual(path, "")
        self.assertIn("no canary record", why)

    def test_another_tree_never_covers(self):
        self.put(record(tree=OTHER))
        path, why = L.covering(TREE, self.dir, NOW)
        self.assertEqual(path, "")
        self.assertTrue(why)

    def test_newer_record_for_another_tree_does_not_decide(self):
        path = self.put(record(finished=NOW - 7200))
        self.put(record(tree=OTHER, jobs={}, verdict="NO-DATA", finished=NOW - 600))
        self.assertEqual(L.covering(TREE, self.dir, NOW), (path, ""))

    def test_stale_record_never_covers(self):
        self.put(record(finished=NOW - 25 * 3600))
        path, why = L.covering(TREE, self.dir, NOW)
        self.assertEqual(path, "")
        self.assertIn("stale", why)

    def test_future_record_never_covers(self):
        self.put(record(finished=NOW + 301))
        path, why = L.covering(TREE, self.dir, NOW)
        self.assertEqual(path, "")
        self.assertIn("future", why)

    def test_corrupt_record_never_covers_and_is_counted(self):
        with open(os.path.join(self.dir, "linux-canary-%s-%s-1.json" % (V, TREE[:12])), "w") as fh:
            fh.write("{not json")
        path, why = L.covering(TREE, self.dir, NOW)
        self.assertEqual(path, "")
        self.assertIn("1 corrupt", why)

    def test_corrupt_newer_record_for_the_tree_refuses(self):
        self.put(record())
        with open(os.path.join(self.dir, "linux-canary-%s-%s-%d.json" % (V, TREE[:12], int(NOW))), "w") as fh:
            fh.write("{not json")
        self.assertEqual(L.covering(TREE, self.dir, NOW)[0], "")

    def test_fail_and_no_data_records_never_cover(self):
        fail = {"required-fast-canary": "failure", "required-fast": "skipped"}
        self.put(record(jobs=fail, verdict="FAIL"))
        self.assertEqual(L.covering(TREE, self.dir, NOW)[0], "")
        other = tempfile.mkdtemp(prefix="lx1-")
        self.addCleanup(shutil.rmtree, other, True)
        L.write_record(record(jobs={}, verdict="NO-DATA"), other)
        self.assertEqual(L.covering(TREE, other, NOW)[0], "")

    def test_tampered_jobs_never_cover(self):
        skipped = {"required-fast-canary": "skipped", "required-fast": "skipped"}
        path = self.put(record(jobs=skipped, verdict="PASS"))
        self.assertEqual(L.covering(TREE, self.dir, NOW)[0], "")
        ok, why = L.record_covers(path, TREE, NOW)
        self.assertFalse(ok)
        self.assertIn("jobs judge NO-DATA", why)

    def test_pass_then_newer_fail_is_not_covered(self):
        self.put(record(finished=NOW - 7200))
        self.put(record(jobs={"required-fast-canary": "failure", "required-fast": "skipped"}, verdict="FAIL",
                        finished=NOW - 600))
        path, why = L.covering(TREE, self.dir, NOW)
        self.assertEqual(path, "")
        self.assertIn("FAIL", why)

    def test_newest_of_two_passes_is_returned(self):
        self.put(record(finished=NOW - 7200))
        newer = self.put(record(finished=NOW - 600))
        self.assertEqual(L.covering(TREE, self.dir, NOW), (newer, ""))

    def test_version_filter(self):
        self.put(record(version="1.1.3"))
        self.assertEqual(L.covering(TREE, self.dir, NOW, "1.1.4")[0], "")
        self.assertTrue(L.covering(TREE, self.dir, NOW, "1.1.3")[0])

    def test_tree_not_40_hex_is_a_reason(self):
        self.put(record())
        for bad in ("A" * 40, TREE[:39], "", "z" * 40):
            path, why = L.covering(bad, self.dir, NOW)
            self.assertEqual(path, "")
            self.assertIn("40 hex", why)

    def test_temp_file_of_a_killed_writer_is_ignored(self):
        with open(os.path.join(self.dir, "linux-canary.tmp-1-abc"), "w") as fh:
            json.dump(record(), fh)
        self.assertEqual(L.covering(TREE, self.dir, NOW)[0], "")
        path = self.put(record())
        self.assertEqual(L.covering(TREE, self.dir, NOW), (path, ""))

    def test_second_writer_same_second_gets_its_own_name(self):
        first = self.put(record())
        second = self.put(record())
        self.assertNotEqual(first, second)
        self.assertEqual(sorted(n for n in os.listdir(self.dir)), sorted([os.path.basename(first),
                                                                           os.path.basename(second)]))

    def test_write_record_refuses_malformed(self):
        for bad in (dict(record(), tree="nothex"), dict(record(), verdict="MAYBE"), dict(record(), schema="x"),
                    {k: v for k, v in record().items() if k != "jobs"}, dict(record(), seconds=float("nan"))):
            with self.assertRaises(ValueError):
                L.write_record(bad, self.dir)
        self.assertEqual(os.listdir(self.dir), [])

    # REQ-LX1-22: record_covers, one file against one tree
    def test_record_covers_match_other_tree_tampered(self):
        path = self.put(record())
        self.assertEqual(L.record_covers(path, TREE, NOW), (True, ""))
        ok, why = L.record_covers(path, OTHER, NOW)
        self.assertFalse(ok)
        self.assertIn("for tree", why)
        with open(path) as fh:
            data = json.load(fh)
        data["jobs"] = {"required-fast-canary": "success"}
        with open(path, "w") as fh:
            json.dump(data, fh)
        self.assertFalse(L.record_covers(path, TREE, NOW)[0])
        self.assertFalse(L.record_covers(os.path.join(self.dir, "missing.json"), TREE, NOW)[0])

    def test_record_covers_age_edges(self):
        self.assertTrue(L.record_covers(self.put(record(finished=NOW - 23 * 3600)), TREE, NOW)[0])
        self.assertTrue(L.record_covers(self.put(record(finished=NOW + 299)), TREE, NOW)[0])
        self.assertFalse(L.record_covers(self.put(record(finished=NOW - 25 * 3600)), TREE, NOW)[0])

    # REQ-LX1-04: private_minutes
    def test_minutes_below_the_stop_is_a_number(self):
        calls = []
        got, why = L.private_minutes(scripted_gh((0, billing(1799), ""), calls=calls), datetime.date(2026, 10, 12))
        self.assertEqual((round(got, 3), why), (1799.0, ""))
        self.assertEqual(calls[1], ["api", "/users/someone/settings/billing/usage?year=2026&month=10"])

    def test_minutes_at_the_stop_is_none(self):
        got, why = L.private_minutes(scripted_gh((0, billing(1800), "")), datetime.date(2026, 10, 12))
        self.assertIsNone(got)
        self.assertIn("1800", why)

    def test_minutes_above_the_stop_is_none(self):
        got, why = L.private_minutes(scripted_gh((0, billing(1850), "")), datetime.date(2026, 10, 12))
        self.assertIsNone(got)
        self.assertTrue(why)

    def test_minutes_without_user_scope_is_none(self):
        no_scope = (1, "", "HTTP 404: Not Found (needs the user scope)")
        got, why = L.private_minutes(scripted_gh(no_scope), datetime.date(2026, 10, 12))
        self.assertIsNone(got)
        self.assertIn("NO-DATA", why)
        got, why = L.private_minutes(scripted_gh((0, billing(1), ""), login=(4, "", "auth")),
                                     datetime.date(2026, 10, 12))
        self.assertIsNone(got)

    def test_minutes_bad_json_or_missing_items_is_none(self):
        for out in ("{not json", "{}", '{"usageItems": "x"}', "[]",
                    '{"usageItems": [{"product": "actions", "unitType": "Minutes", "grossAmount": "1"}]}'):
            got, why = L.private_minutes(scripted_gh((0, out, "")), datetime.date(2026, 10, 12))
            self.assertIsNone(got, out)
            self.assertIn("NO-DATA", why)

    # REQ-LX1-05: hub_slug
    def test_hub_slug_reads_both_forms_and_nothing_else(self):
        for url in ("https://github.com/owner/brother-hub.git", "https://github.com/owner/brother-hub",
                    "git@github.com:owner/brother-hub.git", "git@github.com:owner/brother-hub"):
            self.assertEqual(L.hub_slug(url), ("owner/brother-hub", ""), url)
        for url in ("https://gitlab.com/owner/brother-hub.git", "ssh://git@github.com/owner/repo.git",
                    "https://github.com/owner", "/local/path", "https://github.com/owner/repo/extra"):
            slug, why = L.hub_slug(url)
            self.assertEqual(slug, "", url)
            self.assertTrue(why)

    # main
    def test_main_covering(self):
        path = self.put(record(finished=L.time.time() - 60))
        env = {"BROTHER_CUT_EVIDENCE_DIR": self.dir, "BROTHER_CUT_TEST": "1"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(L.main(["--covering", TREE]), 0)
            self.assertEqual(out.getvalue().strip(), path)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(L.main(["--covering", OTHER, "--version", V]), 1)
            self.assertIn("no canary record", out.getvalue())
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # the hostile input contract: every public function, the same value in every positional parameter
    def test_hostile_inputs_return_or_refuse(self):
        allowed = (ValueError, LookupError, SystemExit)
        funcs = {"hub_slug": 1, "private_minutes": 2, "judge_jobs": 1, "write_record": 2, "record_covers": 3,
                 "covering": 4, "evidence_dir": 0, "main": 1, "hub_ready": 2, "canary_branch": 2,
                 "find_run": 6, "wait_run": 5, "run_canary": 2}
        err = io.StringIO()
        for name, arity in funcs.items():
            fn = L.__dict__[name]
            for value in HOSTILE:
                try:
                    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                        got = fn(*([value] * arity))
                except allowed:
                    continue
                if name in ("record_covers",):
                    self.assertFalse(got[0], (name, value))
                if name in ("covering", "hub_slug"):
                    self.assertEqual(got[0], "", (name, value))
                if name == "private_minutes":
                    self.assertIsNone(got[0], (name, value))
                if name in ("hub_ready",):
                    self.assertTrue(got, (name, value))
                if name in ("find_run",):
                    self.assertIsNone(got[0], (name, value))
                if name in ("wait_run",):
                    self.assertEqual(got[0], {}, (name, value))
                if name in ("run_canary",):
                    self.assertEqual(got[0], "NO-DATA", (name, value))
                if name == "judge_jobs":
                    self.assertEqual(got[0], "NO-DATA", (name, value))
        with self.assertRaises(SystemExit):
            L.main(0)


SLUG = "owner/brother-hub"
URL = "https://github.com/owner/brother-hub.git"
WORKFLOW_TEXT = "on:\n  workflow_dispatch:\njobs:\n  required-fast-canary:\n    runs-on: ubuntu-latest\n"
RUN_ID = 77


class FakeGh(object):
    """Answers the dispatching run's gh calls by shape and records each argv. `views` is the sequence of run view
    replies, the last one repeating; `runs` the run list reply."""

    def __init__(self, minutes=10.0, enabled="true", workflow=WORKFLOW_TEXT, runs=None, views=None,
                 dispatch=(0, "", "")):
        self.calls = []
        self.minutes, self.enabled, self.workflow, self.dispatch = minutes, enabled, workflow, dispatch
        self.runs = runs
        self.views = list(views) if views is not None else [
            {"status": "in_progress", "conclusion": "", "jobs": []},
            {"status": "in_progress", "conclusion": "", "jobs": []},
            {"status": "completed", "conclusion": "success",
             "jobs": [{"name": "required-fast-canary", "conclusion": "success"},
                      {"name": "required-fast", "conclusion": "skipped"}]}]

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:1] == ["api"] and argv[1].endswith("/actions/permissions"):
            return 0, self.enabled + "\n", ""
        if argv[:1] == ["api"] and "/contents/.github/workflows/" in argv[1]:
            return 0, base64.b64encode(self.workflow.encode()).decode() + "\n", ""
        if argv == ["api", "user", "--jq", ".login"]:
            return 0, "someone\n", ""
        if argv[:1] == ["api"] and "/settings/billing/usage" in argv[1]:
            return 0, billing(self.minutes), ""
        if argv[:2] == ["workflow", "run"]:
            return self.dispatch
        if argv[:2] == ["run", "list"]:
            runs = self.runs if self.runs is not None else [
                {"databaseId": RUN_ID, "headSha": SHA, "status": "queued", "conclusion": "",
                 "event": "workflow_dispatch", "createdAt": "2026-10-12T00:00:00Z"}]
            return 0, json.dumps(runs), ""
        if argv[:2] == ["run", "view"]:
            view = self.views.pop(0) if len(self.views) > 1 else self.views[0]
            if isinstance(view, BaseException):
                raise view
            return 0, json.dumps(view), ""
        return 1, "", "unexpected gh call %r" % (argv,)

    def named(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


class FakeGit(object):
    """Answers the dispatching run's git calls and records (argv, cwd). `tip` is what ls-remote prints."""

    def __init__(self, tip=SHA, push=(0, "", ""), url=URL):
        self.calls = []
        self.tip, self.push, self.url = tip, push, url

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if argv == ["remote", "get-url", "hub"]:
            return 0, self.url + "\n", ""
        if argv == ["rev-parse", "HEAD^{tree}"]:
            return 0, TREE + "\n", ""
        if argv == ["rev-parse", "HEAD"]:
            return 0, SHA + "\n", ""
        if argv[:1] == ["ls-remote"]:
            return 0, ("%s\t%s\n" % (self.tip, argv[-1])) if self.tip else "", ""
        if argv[:1] == ["push"]:
            return self.push if "--delete" not in argv else (0, "", "")
        return 1, "", "unexpected git call %r" % (argv,)

    def pushes(self):
        return [c for c in self.calls if c[:1] == ["push"] and "--delete" not in c]

    def deletes(self):
        return [c for c in self.calls if c[:1] == ["push"] and "--delete" in c]


class Clock(object):
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class TheDispatchingRun(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lx1-ev-")
        self.scratch = tempfile.mkdtemp(prefix="lx1-scratch-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(shutil.rmtree, self.scratch, True)
        old = os.environ.get("BROTHER_SCRATCH")
        os.environ["BROTHER_SCRATCH"] = self.scratch
        self.addCleanup(lambda: os.environ.pop("BROTHER_SCRATCH", None) if old is None
                        else os.environ.__setitem__("BROTHER_SCRATCH", old))
        self.built = []

    def build(self, dest):
        self.built.append(dest)
        with open(os.path.join(dest, "marker"), "w") as fh:
            fh.write("x")

    def run_it(self, gh=None, git=None):
        self.gh, self.git, self.clock = gh or FakeGh(), git or FakeGit(), Clock()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            got = L.run_canary("/fake/root", V, gh=self.gh, git=self.git, build=self.build, directory=self.dir,
                               sleep=self.clock.sleep, clock=self.clock)
        self.out = out.getvalue()
        return got

    def record_of(self, path):
        with open(path) as fh:
            return json.load(fh)

    # the happy path: one push without force, one dispatch, three polls, a PASS record
    def test_happy_path_writes_a_pass_record(self):
        verdict, detail, path = self.run_it()
        self.assertEqual(verdict, "PASS", detail)
        self.assertEqual(len(self.git.pushes()), 1)
        self.assertEqual(len(self.gh.named("workflow", "run")), 1)
        self.assertEqual(len(self.gh.named("run", "view")), 3)
        rec = self.record_of(path)
        self.assertEqual((rec["verdict"], rec["tree"], rec["pushed_sha"], rec["run_id"]), ("PASS", TREE, SHA, RUN_ID))
        self.assertEqual(rec["jobs"], L.EXPECTED_JOBS)
        self.assertEqual(rec["branch"], L.canary_branch(TREE, SHA))
        self.assertEqual(L.covering(TREE, self.dir)[0], path)
        self.assertEqual(self.built and os.path.exists(self.built[0]), False, "the build folder is removed")

    # REQ-LX1-06
    def test_the_push_is_never_forced_and_the_branch_is_unique_per_commit(self):
        self.run_it()
        push = self.git.pushes()[0]
        for bad in ("--force", "-f", "--force-with-lease"):
            self.assertNotIn(bad, push)
        self.assertFalse(any(a.startswith("+") or a.startswith("--force") for a in push), push)
        self.assertEqual(push, ["push", URL, "HEAD:refs/heads/canary/%s-%s" % (TREE[:12], SHA[:7])])
        self.assertEqual(L.canary_branch(TREE, SHA), "canary/%s-%s" % (TREE[:12], SHA[:7]))
        self.assertNotEqual(L.canary_branch(TREE, SHA), L.canary_branch(TREE, "d" * 40))
        self.assertEqual(self.gh.named("workflow", "run")[0],
                         ["workflow", "run", L.WORKFLOW, "-R", SLUG, "--ref", L.canary_branch(TREE, SHA)])

    # REQ-LX1-07
    def test_find_run_refuses_another_head_sha(self):
        clock = Clock()
        gh = FakeGh(runs=[{"databaseId": 5, "headSha": "d" * 40, "status": "queued", "conclusion": "",
                           "event": "workflow_dispatch", "createdAt": "x"}])
        got, why = L.find_run(gh, SLUG, "canary/b", SHA, clock.sleep, clock)
        self.assertIsNone(got)
        self.assertIn("120", why)

    def test_find_run_takes_the_exact_sha_and_dispatch_event(self):
        clock = Clock()
        gh = FakeGh(runs=[{"databaseId": 4, "headSha": SHA, "status": "queued", "conclusion": "",
                           "event": "push", "createdAt": "x"},
                          {"databaseId": 5, "headSha": SHA, "status": "queued", "conclusion": "",
                           "event": "workflow_dispatch", "createdAt": "x"},
                          {"databaseId": 6, "headSha": SHA, "status": "queued", "conclusion": "",
                           "event": "workflow_dispatch", "createdAt": "x"}])
        self.assertEqual(L.find_run(gh, SLUG, "canary/b", SHA, clock.sleep, clock), (5, ""))

    def test_find_run_that_never_appears_is_no_data(self):
        clock = Clock()
        gh = FakeGh(runs=[])
        got, why = L.find_run(gh, SLUG, "canary/b", SHA, clock.sleep, clock)
        self.assertIsNone(got)
        self.assertGreaterEqual(clock.t - 1000.0, 120.0)
        self.assertLess(clock.t - 1000.0, 200.0)

    def test_find_run_corrupt_or_empty_is_no_data(self):
        for reply in ((0, "", ""), (0, "{not json", ""), (1, "", "boom"), (0, '{"a": 1}', "")):
            clock = Clock()
            got, why = L.find_run(lambda argv, r=reply: r, SLUG, "canary/b", SHA, clock.sleep, clock)
            self.assertIsNone(got, reply)
            self.assertTrue(why)

    def test_a_run_with_another_head_sha_is_a_no_data_record(self):
        gh = FakeGh(runs=[{"databaseId": 5, "headSha": "d" * 40, "status": "queued", "conclusion": "",
                           "event": "workflow_dispatch", "createdAt": "x"}])
        verdict, detail, path = self.run_it(gh=gh)
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(self.record_of(path)["verdict"], "NO-DATA")
        self.assertEqual(len(self.git.deletes()), 1)

    # REQ-LX1-08
    def test_wait_run_past_the_timeout_is_no_data(self):
        clock = Clock()
        gh = FakeGh(views=[{"status": "in_progress", "conclusion": "", "jobs": []}])
        jobs, why = L.wait_run(gh, SLUG, RUN_ID, clock.sleep, clock)
        self.assertEqual(jobs, {})
        self.assertIn("1320", why)
        self.assertGreaterEqual(clock.t - 1000.0, 1320.0)

    def test_an_unconcluded_run_is_a_no_data_record_never_pass(self):
        gh = FakeGh(views=[{"status": "in_progress", "conclusion": "",
                            "jobs": [{"name": "required-fast-canary", "conclusion": "success"},
                                     {"name": "required-fast", "conclusion": "skipped"}]}])
        verdict, detail, path = self.run_it(gh=gh)
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(L.covering(TREE, self.dir)[0], "")

    def test_a_late_completion_after_a_long_sleep_is_read(self):
        clock = Clock()
        views = [{"status": "in_progress", "conclusion": "", "jobs": []},
                 {"status": "completed", "conclusion": "success",
                  "jobs": [{"name": "required-fast-canary", "conclusion": "success"},
                           {"name": "required-fast", "conclusion": "skipped"}]}]
        gh = FakeGh(views=views)
        jobs, why = L.wait_run(gh, SLUG, RUN_ID, lambda s: clock.sleep(5000), clock)
        self.assertEqual((jobs, why), (L.EXPECTED_JOBS, ""))

    def test_wait_run_corrupt_reply_or_duplicate_jobs_is_no_data(self):
        for view in ("{not json", json.dumps({"status": "completed", "jobs": "x"}),
                     json.dumps({"status": "completed", "jobs": [{"name": "a", "conclusion": "success"},
                                                                 {"name": "a", "conclusion": "failure"}]})):
            clock = Clock()
            jobs, why = L.wait_run(lambda argv, v=view: (0, v, ""), SLUG, RUN_ID, clock.sleep, clock)
            self.assertEqual(jobs, {}, view)
            self.assertTrue(why)

    def test_a_failed_canary_job_is_a_fail_record(self):
        gh = FakeGh(views=[{"status": "completed", "conclusion": "failure",
                            "jobs": [{"name": "required-fast-canary", "conclusion": "failure"},
                                     {"name": "required-fast", "conclusion": "skipped"}]}])
        verdict, detail, path = self.run_it(gh=gh)
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(self.record_of(path)["verdict"], "FAIL")

    # REQ-LX1-09
    def test_at_the_minutes_stop_nothing_is_pushed_or_dispatched(self):
        verdict, detail, path = self.run_it(gh=FakeGh(minutes=1850))
        self.assertEqual((verdict, path), ("NO-DATA", ""))
        self.assertIn("1800", detail)
        self.assertEqual(self.git.pushes(), [])
        self.assertEqual(self.gh.named("workflow", "run"), [])
        self.assertEqual(os.listdir(self.dir), [])

    # REQ-LX1-10
    def test_the_branch_is_deleted_when_its_tip_is_still_the_pushed_sha(self):
        self.run_it()
        self.assertEqual(self.git.deletes(), [["push", URL, "--delete", "refs/heads/" + L.canary_branch(TREE, SHA)]])

    def test_a_branch_whose_tip_moved_is_left_and_named(self):
        verdict, detail, path = self.run_it(git=FakeGit(tip="d" * 40))
        self.assertEqual(self.git.deletes(), [])
        self.assertIn("left", detail)
        self.assertEqual(verdict, "PASS")

    def test_a_dispatch_refused_after_the_push_deletes_the_branch(self):
        verdict, detail, path = self.run_it(gh=FakeGh(dispatch=(1, "", "HTTP 422")))
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(len(self.git.deletes()), 1)
        self.assertEqual(self.record_of(path)["verdict"], "NO-DATA")

    def test_a_failed_push_is_no_data(self):
        verdict, detail, path = self.run_it(git=FakeGit(push=(128, "", "unreachable"), tip=""))
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual(self.gh.named("workflow", "run"), [])
        self.assertEqual(self.git.deletes(), [])

    # REQ-LX1-11
    def test_an_already_covered_tree_dispatches_nothing(self):
        path = L.write_record(record(finished=L.time.time() - 60), self.dir)
        verdict, detail, got = self.run_it()
        self.assertEqual((verdict, got), ("PASS", path))
        self.assertIn("REUSED linux-canary %s %s" % (TREE[:12], path), self.out)
        self.assertEqual(self.git.pushes(), [])
        self.assertEqual(self.gh.named("workflow", "run"), [])
        self.assertEqual(self.gh.named("api", "user"), [])

    # REQ-LX1-12
    def test_hub_ready_names_the_fix(self):
        self.assertEqual(L.hub_ready(FakeGh(), SLUG), "")
        why = L.hub_ready(FakeGh(enabled="false"), SLUG)
        self.assertIn("Actions", why)
        why = L.hub_ready(FakeGh(workflow="on:\n  pull_request:\njobs:\n  required-fast-canary:\n"), SLUG)
        self.assertIn("workflow_dispatch", why)
        why = L.hub_ready(FakeGh(workflow="on:\n  workflow_dispatch:\njobs:\n  required-fast:\n"), SLUG)
        self.assertIn("required-fast-canary", why)
        self.assertTrue(L.hub_ready(lambda argv: (0, "", ""), SLUG))

    def test_a_hub_not_ready_pushes_nothing(self):
        verdict, detail, path = self.run_it(gh=FakeGh(enabled="false"))
        self.assertEqual((verdict, path), ("NO-DATA", ""))
        self.assertEqual(self.git.pushes(), [])

    def test_a_hub_remote_that_is_not_github_pushes_nothing(self):
        verdict, detail, path = self.run_it(git=FakeGit(url="/local/path"))
        self.assertEqual((verdict, path), ("NO-DATA", ""))
        self.assertEqual(self.git.pushes(), [])

    def test_a_build_that_raises_is_no_data(self):
        def boom(dest):
            raise RuntimeError("no allowlist")
        self.build = boom
        verdict, detail, path = self.run_it()
        self.assertEqual((verdict, path), ("NO-DATA", ""))
        self.assertIn("no allowlist", detail)

    # the interrupt: cleanup runs, a NO-DATA record `interrupted`
    def test_an_interrupt_during_the_wait_cleans_up_and_records(self):
        gh = FakeGh(views=[KeyboardInterrupt()])
        verdict, detail, path = self.run_it(gh=gh)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("interrupted", detail)
        self.assertEqual(len(self.git.deletes()), 1)
        self.assertEqual(self.record_of(path)["verdict"], "NO-DATA")

    def test_main_dispatch_mode_exit_codes(self):
        for verdict, code in (("PASS", 0), ("FAIL", 1), ("NO-DATA", 2)):
            out = io.StringIO()
            with mock.patch.object(L, "run_canary", return_value=(verdict, "d", "/x.json")), \
                    contextlib.redirect_stdout(out):
                self.assertEqual(L.main(["--version", V, "--root", "/r"]), code)
            self.assertEqual(out.getvalue().splitlines()[-1], "CANARY %s /x.json d" % verdict)
        out = io.StringIO()
        with mock.patch.object(L, "run_canary", return_value=("NO-DATA", "d", "")), contextlib.redirect_stdout(out):
            L.main(["--version", V])
        self.assertEqual(out.getvalue().splitlines()[-1], "CANARY NO-DATA - d")


RUNBOOK = os.path.join(os.path.dirname(HERE), "docs", "plan", "specs", "RUNBOOK-1.1.3.md")


def canary_section_problem(runbook_text):
    """'' when the runbook's "## LX1 Linux canary" section names --covering, the 1,800 stop and the hub branch name
    shape; else the first thing missing."""
    lines = runbook_text.splitlines()
    starts = [i for i, l in enumerate(lines) if l.startswith("## ") and "Linux canary" in l]
    if not starts:
        return "no ## section titled Linux canary"
    body = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    text = "\n".join(body)
    for need, what in (("--covering", "the --covering read"), ("1,800", "the 1,800 minute stop"),
                       ("canary/<tree12>-<sha7>", "the hub branch name shape")):
        if need not in text:
            return "the Linux canary section does not name %s (%s)" % (what, need)
    return ""


class TheRunbookSection(unittest.TestCase):
    def test_the_problem_finder_goes_red(self):
        good = "## LX1 Linux canary\n--covering, the 1,800 stop, canary/<tree12>-<sha7>\n## next\n1,800\n"
        self.assertEqual(canary_section_problem(good), "")
        self.assertIn("1,800", canary_section_problem(good.replace("the 1,800 stop", "a stop")))
        self.assertTrue(canary_section_problem("## Something else\n"))

    @unittest.skipUnless(os.path.exists(RUNBOOK), "docs/plan/specs/RUNBOOK-1.1.3.md is not shipped here")
    def test_the_runbook_has_the_canary_section(self):
        with open(RUNBOOK, encoding="utf-8") as fh:
            self.assertEqual(canary_section_problem(fh.read()), "")


if __name__ == "__main__":
    unittest.main()
