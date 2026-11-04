#!/usr/bin/env python3
"""OUT1.m integrator cases beside the frozen ContentAccess contract (test_workflows.py, the pack harness).

The frozen class drives invoke_capability with a fake handler only. These cases pin what it cannot reach: the two
content rows (analyze, collect) run the EXISTING authored-content pipeline (door.CONTENT_SOURCE, the rtm
voice_pipeline.py) on a synthetic archive outside every repository and read its own exit code, summary line and
dataset back; a missing,
empty, corrupt, duplicate-only or non UTF-8 archive is NO-DATA or a refusal, never a success with zero counts; a summary
the dataset contradicts is a FAIL; analyze never fetches and collect fetches only behind approve; no post text enters a
reply; the route is hidden from a consumer until admitted; the capability list the door can run, the admission group,
the reach check and the shipped runtime all name the same two ids; and the real command line entry point completes the
route from the checkout AND from the installed bundle layout.

The real pipeline runs here (no network: analyze passes --no-fetch, and collect is only ever driven by a scripted fake).
Every archive is a temp folder holding synthetic pages, never a person's content.

Run: python3 -B scripts/test_content_door_ops.py
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUNDLE = os.path.join(ROOT, "bundle", "runtime")
sys.path.insert(0, HERE)
import door  # noqa: E402
import reach_check  # noqa: E402

IDS = {"content.analyze", "content.collect"}
AUTHORITY = {"content.analyze": "write", "content.collect": "approve"}
HANDLE = "some-author"
POST, OTHER = "7047585770068434944", "7250486972413046784"
BODY = "First synthetic line.\\n\\nSecond synthetic line."
PAGE = ("""<html><a href="https://www.linkedin.com/posts/%s_some-title-activity-%s-abcd">x</a>
<script type="application/ld+json">{"@type":"SocialMediaPosting","datePublished":"2023-03-31T01:02:03Z",
"articleBody":\"""" + BODY + """","interactionStatistic":[
{"interactionType":"http://schema.org/LikeAction","userInteractionCount":529},
{"interactionType":"https://schema.org/CommentAction","userInteractionCount":189}]}</script>""" + " " * 6000)
ADMIT = {"candidate": "c", "host": "h", "capabilities": ["content.*"], "routes": set(), "queue": []}
HIDE = {"candidate": "c", "host": "h", "capabilities": [], "routes": set(), "queue": []}
SUMMARY = "ids %d | OK %d | NO-DATA %d | fetched %d | waiting %d"


class _Archive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="content-door-")
        self.archive = os.path.join(self.tmp, "post archive")  # a space in the path is an ordinary path
        os.makedirs(os.path.join(self.archive, "raw"))
        self.calls = []
        self.reply = (0, "")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, text):
        with open(os.path.join(self.archive, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def page(self, activity, handle=HANDLE):
        with open(os.path.join(self.archive, "raw", activity + ".html"), "w", encoding="utf-8") as fh:
            fh.write(PAGE % (handle, activity))

    def fake_run(self, argv, timeout, env=None):
        self.calls.append((argv, timeout))
        return self.reply

    def ask(self, capability, allow=None, fake=False, archive=None, handle=HANDLE):
        typed = {"archive": self.archive if archive is None else archive, "handle": handle}
        run = self.fake_run if fake else None
        invoke = lambda h, i: door.content_invoke(h, i, run=run)  # noqa: E731
        return door.run_content_capability(capability, None, typed, allow, invoke=invoke)

    def written(self):
        return [n for n in ("dataset.jsonl", "REPORT.md") if os.path.exists(os.path.join(self.archive, n))]

    def cli(self, args, view, here=HERE):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BROTHER", "BM_"))}
        env["HOME"] = self.tmp
        entry = ("import sys; sys.path.insert(0, %r); import door; "
                 "door.consumer_admission = lambda *a, **k: %r; sys.exit(door.main(%r))" % (here, view, args))
        return subprocess.run([sys.executable, "-B", "-c", entry], env=env, cwd=self.tmp, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=300)


class TheListIsOneList(unittest.TestCase):
    """Omit a supported capability from discovery, or ship it without its tool, and one of these goes red."""

    def test_the_door_runs_exactly_the_two_and_each_authority_is_as_designed(self):
        self.assertEqual(IDS, {c["id"] for c in door.CONTENT_CAPABILITIES})
        for row in door.CONTENT_CAPABILITIES:
            self.assertEqual([AUTHORITY[row["id"]]], row["required_authority"], row["id"])
            self.assertTrue(row["handler"].startswith("content_existing_"), row)
            self.assertEqual(["archive", "handle"], row["prerequisites"], row)

    def test_the_two_are_in_the_admission_group_and_in_the_reach_check(self):
        with open(os.path.join(ROOT, "docs", "plan", "ADMISSION.json"), encoding="utf-8") as fh:
            group = json.load(fh)["entries"]["authored-content"]
        self.assertEqual(IDS, set(group["capabilities"]))
        self.assertEqual("AMPLIFIER", group["tier"], "a PARK group could never be admitted; this one waits on proof")
        self.assertTrue(IDS <= set(reach_check.door_capability_ids()))

    def test_an_unknown_content_id_names_the_known_ones_including_every_content_id(self):
        got = door.run_content_capability("content.nothing")
        self.assertEqual("BLOCKED", got["state"])
        for cap in IDS:
            self.assertIn(cap, got["output"])
        self.assertNotIn("limit", got)

    def test_the_pipeline_ships_flat_beside_the_installed_door_byte_for_byte(self):
        source = os.path.join(ROOT, *door.CONTENT_SOURCE.split("/"))
        shipped = os.path.join(BUNDLE, door.CONTENT_TOOL)
        self.assertTrue(os.path.isfile(shipped), "bundle_runtime.py does not ship the pipeline beside the door")
        with open(source, "rb") as a, open(shipped, "rb") as b:
            self.assertEqual(a.read(), b.read(), "the shipped pipeline is not the source")
        self.assertEqual(source, door.content_tool(HERE))
        self.assertEqual(shipped, door.content_tool(BUNDLE))
        self.assertIsNone(door.content_tool(tempfile.gettempdir()))

    def test_the_reach_registry_routes_authored_content_through_the_door(self):
        with open(door.REACH_REGISTRY, encoding="utf-8") as fh:
            data = json.load(fh)
        routes = {c["id"]: c for c in data["capabilities"]}
        row = next(r for r in data["inventory"] if r["id"] == "authored-content")
        self.assertEqual("def run_content_capability", routes[row["routed"]]["marker"])
        self.assertEqual("scripts/door.py", routes[row["routed"]]["command"])
        self.assertTrue(row["shipped"] is True)
        self.assertEqual("OUT1.m", row["unit"])
        self.assertTrue(set(re.findall(r"\bcontent\.[a-z_]+", row["reason"])) <= IDS)
        self.assertEqual("pending", row["disposition"], "no installed end to end run exists, so it is not consumer")
        for text in [routes[row["routed"]]["ask"]] + routes[row["routed"]]["paraphrases"]:
            self.assertNotIn("voice", text.lower())
            self.assertNotIn("speech", text.lower())

    def test_the_route_is_hidden_from_a_consumer_until_an_admission_record_earns_it(self):
        view = door.consumer_admission(candidate="0" * 40, host="claude")
        queued = {q["id"]: q for q in view["queue"]}
        self.assertIn("authored-content", queued)
        self.assertIn("no admission record", queued["authored-content"]["cause"])
        self.assertFalse(door.capability_admitted("content.analyze", view))
        goal = "review which of my own posts performed well, from my archive"
        hidden = door.recommend(goal, admission=view)
        self.assertEqual((False, None, door.ADMISSION_LIMIT), (hidden["delivered"], hidden["route"], hidden["action"]))
        library = door.recommend(goal)  # the registry read as a library: the route exists and is reachable
        self.assertEqual((True, "authored-content-report", "review"),
                         (library["delivered"], library["route"], library["verb"]))

    def test_the_help_and_the_limit_say_what_this_is_not(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            door.main(["--help"])
        text = " ".join(out.getvalue().split())
        self.assertIn("content.analyze", text)
        self.assertIn("neither is speech recognition", text)
        self.assertIn("outside every repository", text)
        self.assertIn("not speech recognition", door.CONTENT_LIMIT)
        self.assertIn("nothing was published", door.CONTENT_LIMIT)


class TheRealPipeline(_Archive):
    """The real downstream tool over a synthetic archive, driven through the door."""

    def test_analyze_completes_with_counts_read_back_from_the_dataset_and_no_post_text(self):
        self.write("ids.txt", "%s\n%s\n%s\n" % (POST, POST, OTHER))
        self.page(POST)
        self.page(OTHER, handle="another-person")
        got = self.ask("content.analyze", ["write"])
        self.assertEqual(("COMPLETE", "PASS"), (got["state"], got["verdict"]), got)
        self.assertIn("2 post id(s) known, 2 page(s) parsed, 0 NO-DATA page(s)", got["output"])
        self.assertIn("1 own post(s) with text and metrics", got["output"])
        self.assertEqual(door.CONTENT_LIMIT, got["limit"])
        self.assertIn(os.path.join(self.archive, "REPORT.md"), got["evidence_refs"])
        self.assertIn(os.path.join(self.archive, "dataset.jsonl"), got["evidence_refs"])
        self.assertTrue(any(ref.endswith("exit 0") for ref in got["evidence_refs"]), got["evidence_refs"])
        self.assertNotIn("synthetic line", json.dumps(got), "a post's text reached the reply")
        self.assertEqual(["dataset.jsonl", "REPORT.md"], self.written())
        with open(os.path.join(self.archive, "REPORT.md"), encoding="utf-8") as fh:
            self.assertIn("own posts with text and metrics: 1", fh.read())

    def test_the_command_line_entry_point_completes_from_the_checkout_and_from_the_installed_layout(self):
        self.write("ids.txt", POST + "\n")
        self.page(POST)
        args = ["--capability", "content.analyze", "--input", "archive=" + self.archive, "--input", "handle=" + HANDLE,
                "--allow", "write"]
        for here in (HERE, BUNDLE):
            got = self.cli(args, ADMIT, here)
            self.assertEqual(0, got.returncode, (here, got.stdout, got.stderr))
            reply = json.loads(got.stdout)
            self.assertEqual("COMPLETE", reply["state"], (here, reply))
            self.assertIn("1 own post(s)", reply["output"])
            self.assertIn(os.path.join(here, door.CONTENT_TOOL) if here == BUNDLE else door.CONTENT_SOURCE,
                          " ".join(reply["evidence_refs"]))
            self.assertNotIn("synthetic line", got.stdout)
        hidden = self.cli(args, HIDE, BUNDLE)
        self.assertEqual(2, hidden.returncode, hidden.stdout + hidden.stderr)
        self.assertEqual(door.ADMISSION_LIMIT, json.loads(hidden.stdout)["output"])

    def test_without_write_nothing_runs_and_the_archive_is_untouched(self):
        self.write("ids.txt", POST + "\n")
        self.page(POST)
        got = self.ask("content.analyze")
        self.assertEqual(("REFUSED", ["write"]), (got["state"], got["missing_authority"]), got)
        self.assertEqual([], self.written())

    def test_an_archive_with_no_source_is_no_data_and_nothing_is_written(self):
        got = self.ask("content.analyze", ["write"])
        self.assertEqual(("NO-DATA", "NO-DATA"), (got["state"], got["verdict"]), got)
        self.assertIn("no archive source", got["output"])
        self.assertIn("nothing was started", got["output"])
        self.assertEqual([], self.written())
        self.assertEqual([], [r for r in got["evidence_refs"] if not r.endswith("exit 2")])

    def test_sources_that_name_no_id_and_a_source_that_is_not_utf8_are_no_data(self):
        self.write("ids.txt", "not an id\n")
        got = self.ask("content.analyze", ["write"])
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("names no post id", got["output"])
        with open(os.path.join(self.archive, "ids.txt"), "wb") as fh:
            fh.write(b"\xff\xfe" + POST.encode() + b"\n")
        got = self.ask("content.analyze", ["write"])
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("could not be read", got["output"])
        self.assertIn("ids.txt unreadable", got["output"])
        self.assertEqual([], self.written())

    def test_an_archive_inside_a_repository_is_refused_by_the_pipeline_itself(self):
        os.makedirs(os.path.join(self.tmp, ".git"))
        self.write("ids.txt", POST + "\n")
        self.page(POST)
        got = self.ask("content.analyze", ["write"])
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("inside the repository", got["output"])
        self.assertEqual([], self.written())

    def test_a_bad_handle_is_the_pipelines_own_refusal(self):
        self.write("ids.txt", POST + "\n")
        got = self.ask("content.analyze", ["write"], handle="x")
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("not a plain profile handle", got["output"])
        self.assertEqual([], self.written())

    def test_an_id_named_by_every_source_and_every_line_counts_once(self):
        self.write("ids.txt", "%s\n%s\n%s\n" % (POST, POST, POST))
        self.write("slugs.txt", "%s_a-title-activity-%s-abcd\n" % (HANDLE, POST))
        self.page(POST)
        got = self.ask("content.analyze", ["write"])
        self.assertEqual("PASS", got["verdict"], got)
        self.assertIn("1 post id(s) known, 1 page(s) parsed", got["output"])
        with open(os.path.join(self.archive, "dataset.jsonl"), encoding="utf-8") as fh:
            self.assertEqual(1, len([ln for ln in fh if ln.strip()]))

    def test_pages_that_hold_no_own_post_are_no_data_never_a_pass_with_zero(self):
        self.write("ids.txt", OTHER + "\n")
        self.page(OTHER, handle="another-person")
        got = self.ask("content.analyze", ["write"])
        self.assertEqual(("NO-DATA", "NO-DATA"), (got["state"], got["verdict"]), got)
        self.assertIn("0 own post(s)", got["output"])
        self.assertIn("Nothing can be said about what performed well", got["output"])
        self.write("ids.txt", POST + "\n")  # an id with no page on disk: kept as NO-DATA, nothing invented
        got = self.ask("content.analyze", ["write"])
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("1 post id(s) known, 0 page(s) parsed, 1 NO-DATA page(s)", got["output"])


class HostileArchive(_Archive):
    """An archive whose outputs are links aimed outside it: every write refuses, through the real command line entry
    point, and the link's target stays byte identical."""

    def setUp(self):
        super().setUp()
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.outside)
        self.write("ids.txt", POST + "\n")
        self.page(POST)
        self.args = ["--capability", "content.analyze", "--input", "archive=" + self.archive,
                     "--input", "handle=" + HANDLE, "--allow", "write"]

    def aim(self, rel, text="precious\n"):
        """A symlink at archive/<rel>: raw/ is aimed at the (empty) outside folder, a file at a target file there."""
        link = os.path.join(self.archive, rel)
        if rel == "raw":
            shutil.rmtree(link)
            os.symlink(self.outside, link)
            return self.outside
        target = os.path.join(self.outside, rel.replace("/", "_") + ".target")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.symlink(target, link)
        return target

    def refused(self, needle):
        got = self.cli(self.args, ADMIT, BUNDLE)
        self.assertEqual(2, got.returncode, got.stdout + got.stderr)
        reply = json.loads(got.stdout)
        self.assertEqual(("NO-DATA", "NO-DATA"), (reply["state"], reply["verdict"]), reply)
        self.assertIn(needle, reply["output"])
        return reply

    def test_a_symlinked_dataset_refuses_and_its_target_is_untouched(self):
        target = self.aim("dataset.jsonl")
        self.refused("dataset.jsonl is a symlink, so it is not written")
        self.assertEqual("precious\n", open(target, encoding="utf-8").read())
        self.assertFalse(os.path.exists(os.path.join(self.archive, "REPORT.md")))

    def test_a_symlinked_report_refuses_and_its_target_is_untouched(self):
        target = self.aim("REPORT.md")
        self.refused("REPORT.md is a symlink, so it is not written")
        self.assertEqual("precious\n", open(target, encoding="utf-8").read())
        self.assertFalse(os.path.exists(os.path.join(self.archive, "dataset.jsonl")))

    def test_a_symlinked_raw_folder_refuses_and_nothing_lands_outside(self):
        self.aim("raw")
        self.refused("raw is a symlink")
        self.assertEqual([], os.listdir(self.outside))
        self.assertEqual([], self.written())

    def test_a_symlinked_page_is_not_read_and_the_run_says_so(self):
        os.unlink(os.path.join(self.archive, "raw", POST + ".html"))
        target = self.aim("raw/" + POST + ".html", PAGE % (HANDLE, POST))
        got = self.cli(self.args, ADMIT, BUNDLE)
        self.assertEqual(2, got.returncode, got.stdout + got.stderr)  # NO-DATA: the only page was not read
        reply = json.loads(got.stdout)
        self.assertEqual("NO-DATA", reply["verdict"], reply)
        self.assertIn("1 NO-DATA page(s)", reply["output"])
        self.assertEqual(PAGE % (HANDLE, POST), open(target, encoding="utf-8").read())
        with open(os.path.join(self.archive, "dataset.jsonl"), encoding="utf-8") as fh:
            self.assertIn("symlink", json.loads(fh.readline())["reason"])

    def test_a_symlinked_page_in_a_deep_archive_folder_still_says_symlink(self):
        """The reason is cut at a fixed length; a page named by its full path in a deep folder lost the cause."""
        deep = os.path.join(self.tmp, "d" * 150, "post archive")
        os.makedirs(os.path.dirname(deep))
        shutil.move(self.archive, deep)
        self.archive = deep
        self.args[3] = "archive=" + deep
        self.test_a_symlinked_page_is_not_read_and_the_run_says_so()


class TheScriptedPipeline(_Archive):
    """What the real tool cannot show: the argv shape, the timeouts and a tool that lies or dies."""

    def test_analyze_never_fetches_and_collect_fetches_only_behind_approve(self):
        self.ask("content.analyze", ["write"], fake=True)
        argv, timeout = self.calls[-1]
        self.assertEqual("--no-fetch", argv[-1], argv)
        self.assertEqual(door.CONTENT_TIMEOUT_S, timeout)
        self.assertEqual([os.path.abspath(self.archive), HANDLE],
                         [argv[argv.index("--dir") + 1], argv[argv.index("--handle") + 1]])
        self.assertTrue(argv[2].endswith(door.CONTENT_TOOL), argv)
        refused = self.ask("content.collect", ["write"], fake=True)
        self.assertEqual(("REFUSED", ["approve"]), (refused["state"], refused["missing_authority"]))
        self.assertEqual(1, len(self.calls), "collect ran without approve")
        self.ask("content.collect", ["approve"], fake=True)
        argv, timeout = self.calls[-1]
        self.assertNotIn("--no-fetch", argv)
        self.assertEqual(door.CONTENT_COLLECT_TIMEOUT_S, timeout)

    def test_a_missing_folder_or_a_value_that_starts_with_a_dash_starts_nothing(self):
        for archive, handle in ((os.path.join(self.tmp, "nope"), HANDLE), ("-x", HANDLE), (self.archive, "--no-fetch"),
                                (self.archive, "a b"), (self.archive, "")):
            got = self.ask("content.analyze", ["write"], fake=True, archive=archive, handle=handle)
            self.assertNotEqual("COMPLETE", got["state"], (archive, handle))
            self.assertIn("nothing was run" if handle else "missing handle", got["output"], (archive, handle, got))
        self.assertEqual([], self.calls)

    def test_an_exit_zero_without_the_summary_line_claims_no_count(self):
        self.reply = (0, "all good\n")
        got = self.ask("content.analyze", ["write"], fake=True)
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("without its summary line", got["output"])

    def test_a_summary_the_dataset_contradicts_is_a_fail(self):
        self.reply = (0, SUMMARY % (5, 5, 0, 0, 0))
        got = self.ask("content.analyze", ["write"], fake=True)
        self.assertEqual("FAIL", got["verdict"], got)
        self.assertIn("cannot be read back", got["output"])
        row = json.dumps({"activity": POST, "status": "OK", "own_post": True, "text": "t"}) + "\n"
        self.write("dataset.jsonl", row)
        got = self.ask("content.analyze", ["write"], fake=True)
        self.assertEqual("FAIL", got["verdict"], got)
        self.assertIn("holds 1 and 1", got["output"])
        self.write("dataset.jsonl", "{not json\n")
        self.reply = (0, SUMMARY % (1, 1, 0, 0, 0))
        got = self.ask("content.analyze", ["write"], fake=True)
        self.assertEqual("FAIL", got["verdict"], got)
        self.write("dataset.jsonl", row)
        got = self.ask("content.analyze", ["write"], fake=True)
        self.assertEqual(("COMPLETE", "PASS"), (got["state"], got["verdict"]), got)

    def test_a_tool_that_cannot_start_times_out_or_dies_is_never_a_pass(self):
        self.reply = (None, "did not answer within 600 seconds and was stopped")
        got = self.ask("content.analyze", ["write"], fake=True)
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("did not answer", got["output"])
        self.reply = (137, "")
        got = self.ask("content.analyze", ["write"], fake=True)
        self.assertEqual(("FAILED", "FAIL"), (got["state"], got["verdict"]), got)

    def test_no_pipeline_beside_the_door_is_no_data(self):
        got = door.content_invoke("content_existing_report", {"archive": self.archive, "handle": HANDLE},
                                  run=self.fake_run, here=self.tmp)
        self.assertEqual("NO-DATA", got["verdict"], got)
        self.assertIn("carries no " + door.CONTENT_TOOL, got["output"])
        self.assertEqual([], self.calls)


class TheCommandLineEntryPoint(unittest.TestCase):
    def test_a_content_id_a_consumer_has_not_earned_is_hidden_and_starts_nothing(self):
        with mock.patch.object(door, "consumer_admission", return_value=HIDE), \
                mock.patch.object(door, "content_invoke") as invoke, contextlib.redirect_stdout(io.StringIO()) as out:
            code = door.main(["--capability", "content.analyze", "--input", "archive=/x", "--input", "handle=h",
                              "--allow", "write"])
        self.assertEqual(2, code)
        invoke.assert_not_called()
        self.assertEqual("BLOCKED", json.loads(out.getvalue())["state"])

    def test_an_admitted_content_id_runs_through_the_content_route_with_its_exit_code(self):
        fake = mock.Mock(return_value={"verdict": "PASS", "output": "1 own post(s)", "evidence_refs": ["e"]})
        with mock.patch.object(door, "consumer_admission", return_value=ADMIT), \
                mock.patch.object(door, "content_invoke", fake), contextlib.redirect_stdout(io.StringIO()) as out:
            code = door.main(["--capability", "content.analyze", "--input", "archive=/x", "--input", "handle=h",
                              "--allow", "write"])
        self.assertEqual(0, code)
        fake.assert_called_once()
        reply = json.loads(out.getvalue())
        self.assertEqual(("COMPLETE", door.CONTENT_LIMIT), (reply["state"], reply["limit"]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
