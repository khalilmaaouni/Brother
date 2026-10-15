#!/usr/bin/env python3
"""PG1.f: tests for scripts/plan_pipeline.py, the one command from the seed to a building loop.

Most cases inject a fake runner, so no child process starts and nothing paid can: the launch is only ever an argv
the fake records. The end to end cases run the real plan_gen.py and plan_briefs.py children in a temporary git
repository and fake only scripts/loop/start_run.sh and the writer command. No case starts a real loop, a real writer
or a model call, and no case needs ps, gh, the network or the real docs/plan. HOME is an empty temporary directory for
the whole run. No em or en dashes and no clock text are typed in this file: such strings are built in code.
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
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import plan_pipeline as PP  # noqa: E402
import plan_seed as PS  # noqa: E402

VERSION = "9.9.10"
WBS = "docs/plan/BROTHER-%s-WBS.json" % VERSION
SPEC_DIR = "docs/plan/specs/%s" % VERSION
HEADS = ["## %d. Heading %d" % (i, i) for i in range(1, 12)]
FILLS = ["WRITER: fill %d" % i for i in range(1, 12)]
BRIEFS_OUT = ("WAVES: 3 width 2 (limit UNVERIFIED)\nWAVE 1: A, B (about 190 s per wave, W2, 2026-10-14)\n"
              "ARITHMETIC: ceil(5 / 2) = 3 waves x about 190 s = about 570 s\n")


def grant(**kw):
    g = {"model": "m words", "budget": "80", "deadline": "18" + ":" + "30"}
    g.update(kw)
    return g


def seed_of(units=(), version=VERSION, **kw):
    s = {"schema": PS.SEED_SCHEMA, "version": version, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "grant": grant(), "sources": [], "units": list(units), "findings": [], "failures": {}}
    s.update(kw)
    return s


def unit(uid):
    low = uid.lower()
    return {"id": uid, "title": "title " + uid, "objective": "objective " + uid, "state": "DEFERRED",
            "owns": ["scripts/%s.py" % low, "scripts/test_%s.py" % low], "after": [], "source": "src", "reads": [],
            "remains": "everything", "spec": "docs/plan/specs/%s.md" % uid}


class Fake:
    """A runner that records every call and answers from a script keyed by the kind of command."""

    def __init__(self, script=None):
        self.calls = []
        self.script = script or {}
        self.lock = threading.Lock()

    @staticmethod
    def kind(argv):
        joined = " ".join(argv)
        for k in ("plan_gen.py", "plan_briefs.py", "start_run.sh"):
            if k in joined:
                return k
        return "writer"

    def __call__(self, argv, cwd, env):
        k = self.kind(argv)
        with self.lock:
            self.calls.append((k, list(argv), cwd, env))
        answer = self.script.get(k)
        if callable(answer):
            return answer(argv, cwd, env)
        if answer is not None:
            return answer
        return (0, BRIEFS_OUT) if k == "plan_briefs.py" else (0, "ok")

    def kinds(self):
        return [c[0] for c in self.calls]


class Fix:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg1f_test_")
        self._home = os.environ.get("HOME")
        self._width = os.environ.pop(PP.plan_briefs.WIDTH_ENV, None)
        os.environ["HOME"] = os.path.join(self.tmp, "emptyhome")  # an empty HOME: nothing may lean on this laptop
        os.makedirs(os.environ["HOME"])
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.root, SPEC_DIR))
        self.seed_path = os.path.join(self.tmp, "seed.json")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home
        if self._width is not None:
            os.environ[PP.plan_briefs.WIDTH_ENV] = self._width
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put_seed(self, seed=None, **kw):
        with open(self.seed_path, "w", encoding="utf-8") as f:
            json.dump(seed if seed is not None else seed_of(**kw), f)

    def put_batches(self, sizes):
        batches = [{"id": "B%d" % i, "units": ["U"], "sub_units": ["U.%d" % j for j in range(n)]} for i, n in enumerate(sizes)]
        with open(os.path.join(self.root, SPEC_DIR, "WRITER-ASSIGNMENTS.json"), "w", encoding="utf-8") as f:
            json.dump({"batches": batches}, f)

    def go(self, runner, launch=False, writer_cmd=None, version=VERSION):
        return PP.pipeline(self.seed_path, version, launch, runner, writer_cmd, self.root)

    def record(self):
        with open(os.path.join(self.root, SPEC_DIR, "PIPELINE.json"), encoding="utf-8") as f:
            return json.load(f)

    def main(self, argv, runner):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = PP.main(argv + ["--root", self.root], runner=runner)
        return code, out.getvalue()


class TestStages(Fix, unittest.TestCase):
    def test_stages_run_in_order_and_stop_at_the_first_refusal(self):
        self.put_seed()
        ok = Fake()
        rec = self.go(ok, launch=True)
        self.assertEqual(ok.kinds(), ["plan_gen.py", "plan_briefs.py", "start_run.sh"])
        self.assertEqual([s["stage"] for s in rec["stages"]], ["seed", "generate", "briefs", "launch"])
        self.assertEqual(rec["exit"], 0)
        for broken, ran in (("plan_gen.py", ["plan_gen.py"]), ("plan_briefs.py", ["plan_gen.py", "plan_briefs.py"])):
            fake = Fake({broken: (1, "REFUSED: because")})
            rec = self.go(fake, launch=True, writer_cmd="w {brief}")
            self.assertEqual(fake.kinds(), ran, "nothing after the refusal may run, the launch above all")
            self.assertEqual(rec["stages"][-1]["exit"], 1)
            self.assertEqual(rec["exit"], 1)
            self.assertNotIn("start_run.sh", fake.kinds())

    def test_the_stages_names_are_the_contract(self):
        self.assertEqual(PP.STAGES, ("seed", "generate", "briefs", "launch"))

    def test_the_first_nonzero_exit_is_the_pipeline_exit(self):
        self.put_seed()
        self.assertEqual(self.go(Fake({"plan_gen.py": (2, "NO-DATA")}))["exit"], 2)
        self.assertEqual(self.go(Fake({"plan_gen.py": (7, "odd")}))["exit"], 7)
        code, out = self.main(["--seed", self.seed_path, "--version", VERSION], Fake({"plan_gen.py": (7, "odd")}))
        self.assertEqual(code, 1)  # the command only ever answers 0, 1 or 2
        self.assertIn("PIPELINE STOPPED at generate", out)

    def test_a_launch_the_loop_refuses_is_recorded_with_its_words(self):
        self.put_seed()
        fake = Fake({"start_run.sh": (1, "START REFUSED [intake]: the budget exceeds the headroom")})
        rec = self.go(fake, launch=True)
        self.assertEqual(rec["stages"][-1]["stage"], "launch")
        self.assertEqual(rec["exit"], 1)
        self.assertIn("START REFUSED [intake]", rec["stages"][-1]["output_tail"])
        self.assertIn("START REFUSED", self.record()["stages"][-1]["output_tail"])
        code, out = self.main(["--seed", self.seed_path, "--version", VERSION, "--launch"], fake)
        self.assertEqual(code, 1)
        self.assertIn("START REFUSED [intake]", out)

    def test_each_child_runs_from_the_root_with_the_sibling_scripts(self):
        self.put_seed()
        fake = Fake()
        self.go(fake, launch=True)
        gen, briefs, launch = fake.calls
        self.assertEqual(gen[1][2], os.path.join(HERE, "plan_gen.py"))
        self.assertEqual(briefs[1][2], os.path.join(HERE, "plan_briefs.py"))
        self.assertEqual({c[2] for c in fake.calls}, {self.root})
        self.assertEqual(gen[1][3:], ["--seed", self.seed_path, "--version", VERSION, "--root", self.root])
        self.assertIn(os.path.join(self.root, SPEC_DIR, "briefs"), briefs[1])


class TestLaunch(Fix, unittest.TestCase):
    def test_the_launch_env_carries_the_grant_and_the_floor(self):
        env = PP.grant_env(seed_of(grant=grant(budget="20M tokens and 100USD", deadline="6pm")))
        self.assertEqual(env["BROTHER_SPEC_FLOOR"], "9")
        self.assertEqual(env["BROTHER_WBS"], WBS)
        self.assertEqual((env["--deadline"], env["--budget-usd"]), ("6pm", "20M tokens and 100USD"))  # verbatim, the loop judges
        self.put_seed()
        fake = Fake()
        self.go(fake, launch=True)
        kind, argv, cwd, child_env = fake.calls[-1]
        self.assertEqual(kind, "start_run.sh")
        self.assertEqual(argv, ["bash", os.path.join(self.root, "scripts", "loop", "start_run.sh"), "--plan", WBS,
                                "--deadline", grant()["deadline"], "--budget-usd", "80"])
        self.assertEqual(child_env, {"BROTHER_SPEC_FLOOR": "9", "BROTHER_WBS": WBS})
        self.assertEqual(cwd, self.root)

    def test_a_grant_reading_no_data_refuses_the_launch(self):
        for key in ("budget", "deadline"):
            for bad in (PS.NO_DATA, "NO-DATA: the owner gave none", "", "   ", None, 7, ["80"]):
                with self.assertRaisesRegex(ValueError, key, msg="%s %r" % (key, bad)):
                    PP.grant_env(seed_of(grant=grant(**{key: bad})))
            g = grant()
            del g[key]
            with self.assertRaisesRegex(ValueError, key):
                PP.grant_env(seed_of(grant=g))
            self.put_seed(grant=grant(**{key: PS.NO_DATA}))
            fake = Fake()
            rec = self.go(fake, launch=True)
            self.assertEqual(fake.calls, [], "the refusal comes before the generator writes anything")
            self.assertEqual([s["stage"] for s in rec["stages"]], ["seed"])
            self.assertEqual(rec["exit"], 1)
            self.assertIn(key, rec["stages"][0]["output_tail"])
        for bad in (None, "text", [], {}):
            with self.assertRaises(ValueError):
                PP.grant_env(seed_of(grant=bad))
        with self.assertRaises(ValueError):
            PP.grant_env(seed_of(version="1.1"))
        with self.assertRaises(ValueError):
            PP.grant_env("not a seed")

    def test_without_launch_the_loop_is_not_started(self):
        self.put_seed()
        fake = Fake()
        code, out = self.main(["--seed", self.seed_path, "--version", VERSION], fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.kinds(), ["plan_gen.py", "plan_briefs.py"])
        self.assertIn("LAUNCH (not started, add --launch): BROTHER_SPEC_FLOOR=9 BROTHER_WBS=%s bash " % WBS, out)
        self.assertIn("start_run.sh --plan %s --deadline %s --budget-usd 80" % (WBS, grant()["deadline"]), out)
        self.assertEqual([s["stage"] for s in self.record()["stages"]], ["seed", "generate", "briefs"])

    def test_without_launch_a_no_data_grant_still_generates_and_says_the_launch_would_refuse(self):
        self.put_seed(grant=grant(budget=PS.NO_DATA))
        fake = Fake()
        rec = self.go(fake)
        self.assertEqual(fake.kinds(), ["plan_gen.py", "plan_briefs.py"])
        self.assertEqual(rec["exit"], 0)
        self.assertIn("NO-DATA: the launch would be refused", rec["launch_line"])

    def test_a_hostile_grant_word_is_carried_as_one_argument_never_a_shell(self):
        word = "80; touch /tmp/never && echo $(id)"
        self.put_seed(grant=grant(budget=word))
        fake = Fake()
        self.go(fake, launch=True)
        argv = fake.calls[-1][1]
        self.assertEqual(argv[argv.index("--budget-usd") + 1], word)
        self.assertEqual(argv[0], "bash")


class TestRecord(Fix, unittest.TestCase):
    def test_the_pipeline_record_carries_every_stage_and_the_width(self):
        self.put_seed()
        before = time.time()
        rec = self.go(Fake(), launch=True)
        on_disk = self.record()
        self.assertEqual(on_disk["schema"], "brother-plan-pipeline/1")
        self.assertEqual([s["stage"] for s in on_disk["stages"]], ["seed", "generate", "briefs", "launch"])
        self.assertEqual((on_disk["width"], on_disk["arithmetic"]), (2, "ARITHMETIC: ceil(5 / 2) = 3 waves x about 190 s = about 570 s"))
        with open(self.seed_path, encoding="utf-8") as f:
            self.assertEqual(on_disk["seed_generated_at"], json.load(f)["generated_at"])
        last = before - 1
        for s in on_disk["stages"]:
            self.assertLessEqual(last, s["started"])
            self.assertLessEqual(s["started"], s["finished"])
            self.assertAlmostEqual(s["seconds"], s["finished"] - s["started"], places=2)
            last = s["started"]
        self.assertEqual(on_disk["exit"], rec["exit"])

    def test_a_record_is_written_even_when_a_stage_refuses(self):
        self.put_seed()
        self.go(Fake({"plan_gen.py": (1, "REFUSED")}))
        rec = self.record()
        self.assertEqual([s["stage"] for s in rec["stages"]], ["seed", "generate"])
        self.assertEqual((rec["width"], rec["arithmetic"]), ("NO-DATA", "NO-DATA"))
        self.assertEqual(rec["exit"], 1)

    def test_a_corrupt_record_is_moved_aside_never_read_and_a_good_one_is_kept(self):
        self.put_seed()
        path = os.path.join(self.root, SPEC_DIR, "PIPELINE.json")
        for held, tag in (("{not json", "corrupt"), ("[1, 2]", "corrupt"), (json.dumps({"schema": "other/1"}), "corrupt"),
                          (json.dumps({"schema": PP.SCHEMA, "stages": []}), "prev")):
            with open(path, "w", encoding="utf-8") as f:
                f.write(held)
            self.go(Fake())
            names = os.listdir(os.path.join(self.root, SPEC_DIR))
            moved = [n for n in names if n.startswith("PIPELINE.json.%s-" % tag)]
            self.assertEqual(len(moved), 1, (held, names))
            with open(os.path.join(self.root, SPEC_DIR, moved[0]), encoding="utf-8") as f:
                self.assertEqual(f.read(), held)
            os.remove(os.path.join(self.root, SPEC_DIR, moved[0]))
            self.assertEqual(self.record()["schema"], PP.SCHEMA)

    def test_every_child_output_goes_to_the_log(self):
        self.put_seed()
        self.go(Fake({"plan_gen.py": (0, "GENERATED-MARK")}))
        with open(os.path.join(self.root, SPEC_DIR, "PIPELINE.log"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("== generate exit 0", text)
        self.assertIn("GENERATED-MARK", text)
        self.assertIn("== briefs exit 0", text)

    def test_a_log_that_cannot_be_written_is_recorded_not_fatal(self):
        rec = PP.run_stage("x", ["a"], lambda a, c, e: (0, "out"), os.path.join(self.tmp, "no", "such", "dir", "log"))
        self.assertEqual((rec["exit"], rec["log_error"]), (0, "FileNotFoundError"))


class TestSeedStage(Fix, unittest.TestCase):
    def test_a_seed_of_another_schema_version_or_shape_refuses_before_any_child(self):
        cases = [seed_of(schema="brother-plan-seed/0"), seed_of(version="9.9.11"), dict(seed_of(), units="x"), "not a record", []]
        for seed in cases:
            self.put_seed(seed)
            fake = Fake()
            rec = self.go(fake)
            self.assertEqual((rec["exit"], fake.calls), (1, []), repr(seed)[:60])
        with open(self.seed_path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(self.go(Fake())["exit"], 1)

    def test_a_missing_seed_or_a_directory_is_no_data(self):
        self.assertEqual(self.go(Fake())["exit"], 2)
        os.makedirs(self.seed_path)
        fake = Fake()
        self.assertEqual(self.go(fake)["exit"], 2)
        self.assertEqual(fake.calls, [])

    def test_a_bad_version_refuses_and_writes_no_record(self):
        self.put_seed()
        before = sorted(os.listdir(self.tmp))
        for bad in ("1.1", "v1.1.5", "1.1.5.1", "1.1.5-rc1", "../x", "", None):
            fake = Fake()
            rec = self.go(fake, version=bad)
            self.assertEqual((rec["exit"], fake.calls), (1, []), repr(bad))
        self.assertEqual(sorted(os.listdir(self.tmp)), before)
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "docs", "plan", "specs"))), [VERSION])

    def test_a_seed_that_carries_the_same_bad_version_is_refused_too(self):
        for bad in ("1.1", "v1.1.5", "1.1.5-rc1", "../x"):
            self.put_seed(seed_of(version=bad))
            fake = Fake()
            rec = self.go(fake, version=bad)
            self.assertEqual((rec["exit"], fake.calls), (1, []), repr(bad))
            self.assertIn("not N.N.N", rec["stages"][0]["output_tail"])

    def test_a_stale_seed_still_runs_but_the_record_says_its_age(self):
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 86400))
        self.put_seed(generated_at=old)
        rec = self.go(Fake())
        self.assertEqual(rec["exit"], 0)
        self.assertGreater(rec["seed_age_s"], PP.STALE_SEED_S)
        self.put_seed(generated_at="yesterday-ish")
        self.assertEqual(self.go(Fake())["seed_age_s"], "NO-DATA")

    def test_a_writer_command_without_the_brief_word_or_unparsable_refuses_first(self):
        self.put_seed()
        for cmd in ("run-writer", "run-writer 'unclosed {brief}"):
            fake = Fake()
            rec = self.go(fake, writer_cmd=cmd)
            self.assertEqual((rec["exit"], fake.calls), (1, []), cmd)
            self.assertIn("writer command", rec["stages"][0]["output_tail"])


class TestWriters(Fix, unittest.TestCase):
    def test_the_writer_command_runs_once_per_first_wave_brief(self):
        self.put_seed()
        self.put_batches([1, 3, 1, 2, 1, 1])  # B1 is the longest, then B3, then B0 and B2 (file order)
        fake = Fake()
        rec = self.go(fake, writer_cmd="run-writer --in {brief} --note 'a b'")
        writes = [c for c in fake.calls if c[0] == "writer"]
        briefs_dir = os.path.join(self.root, SPEC_DIR, "briefs")
        self.assertEqual(len(writes), 4, "width 4: one writer per brief of the first wave, none for the later waves")
        self.assertEqual(sorted(c[1][2] for c in writes), sorted(os.path.join(briefs_dir, "%s.md" % b) for b in ("B1", "B3", "B0", "B2")))
        self.assertEqual(writes[0][1][:2] + writes[0][1][3:], ["run-writer", "--in", "--note", "a b"])
        self.assertEqual(sorted(w["stage"] for w in rec["writers"]), ["writer:B0", "writer:B1", "writer:B2", "writer:B3"])
        self.assertEqual(self.record()["writers"], rec["writers"])

    def test_the_writer_width_follows_the_env_cap(self):
        self.put_seed()
        self.put_batches([1] * 6)
        os.environ[PP.plan_briefs.WIDTH_ENV] = "2"
        try:
            fake = Fake()
            self.go(fake, writer_cmd="w {brief}")
        finally:
            os.environ.pop(PP.plan_briefs.WIDTH_ENV, None)
        self.assertEqual(len([c for c in fake.calls if c[0] == "writer"]), 2)

    def test_the_launch_happens_while_the_first_wave_is_still_writing(self):
        self.put_seed()
        self.put_batches([1, 1])
        launched = threading.Event()
        seen = {}

        def writer(argv, cwd, env):
            seen[argv[-1]] = launched.wait(10)  # a writer that only finishes once the loop has been launched
            return (0, "wrote")

        def launch(argv, cwd, env):
            launched.set()
            return (0, "STARTED")

        rec = self.go(Fake({"writer": writer, "start_run.sh": launch}), launch=True, writer_cmd="w {brief}")
        self.assertEqual(list(seen.values()), [True, True], "no barrier: the launch must not wait for the writers")
        self.assertEqual([w["exit"] for w in rec["writers"]], [0, 0])

    def test_no_writer_command_starts_no_writer_and_a_failed_writer_does_not_fail_the_pipeline(self):
        self.put_seed()
        self.put_batches([1])
        fake = Fake()
        self.go(fake)
        self.assertNotIn("writer", fake.kinds())
        rec = self.go(Fake({"writer": (1, "HELD")}), writer_cmd="w {brief}")
        self.assertEqual((rec["exit"], rec["writers"][0]["exit"]), (0, 1))  # a held writer is the pool's to refuse

    def test_a_refused_briefs_stage_starts_no_writer(self):
        self.put_seed()
        self.put_batches([1])
        fake = Fake({"plan_briefs.py": (1, "REFUSED")})
        self.go(fake, writer_cmd="w {brief}")
        self.assertNotIn("writer", fake.kinds())


class TestRunStage(Fix, unittest.TestCase):
    def test_a_child_that_cannot_start_is_exit_127(self):
        def cannot(argv, cwd, env):
            raise FileNotFoundError("no such program")

        rec = PP.run_stage("x", ["nope"], cannot, os.devnull)
        self.assertEqual(rec["exit"], 127)
        self.assertIn("FileNotFoundError", rec["error"])
        for garbage in (("0", "out"), (None, "out"), (True, "out"), (0, None), 0, "ok"):
            rec = PP.run_stage("x", ["a"], lambda a, c, e, g=garbage: g, os.devnull)
            self.assertEqual(rec["exit"], 127, repr(garbage))
        real = PP.run_stage("x", [os.path.join(self.tmp, "does-not-exist")], PP._default_runner, os.devnull)
        self.assertEqual(real["exit"], 127)
        self.put_seed()
        rec = self.go(cannot, launch=True)
        self.assertEqual((rec["exit"], rec["stages"][-1]["stage"]), (127, "generate"))
        self.assertEqual(self.main(["--seed", self.seed_path, "--version", VERSION], cannot)[0], 1)

    def test_a_child_over_the_cap_is_exit_124_not_a_pass(self):
        old, PP.TIMEOUT_S = PP.TIMEOUT_S, 0.3
        try:
            rec = PP.run_stage("x", [sys.executable, "-c", "import time; time.sleep(30)"], PP._default_runner, os.devnull)
        finally:
            PP.TIMEOUT_S = old
        self.assertEqual(rec["exit"], 124)
        self.assertIn("timed out", rec["output_tail"])

    def test_the_default_runner_returns_the_exit_code_and_both_streams(self):
        code, out = PP._default_runner([sys.executable, "-c", "import sys; print('a'); print('b', file=sys.stderr); sys.exit(3)"], None, None)
        self.assertEqual((code, sorted(out.split())), (3, ["a", "b"]))
        code, out = PP._default_runner([sys.executable, "-c", "import os; print(os.environ['PG1F_X'])"], self.tmp, {"PG1F_X": "v"})
        self.assertEqual((code, out.split()[0]), (0, "v"))


def init_repo(root):
    """A git repository the real plan_gen.py can run in: a tracked template generator."""
    os.makedirs(os.path.join(root, "docs", "plan", "specs", "9.9.9"), exist_ok=True)
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    with open(os.path.join(root, "docs", "plan", "specs", "9.9.9", "pack_v999.py"), "w", encoding="utf-8") as f:
        f.write("HEADINGS = %r\nFILL = %r\n" % (HEADS, FILLS))
    for args in (("init", "-q"), ("add", "-A")):
        subprocess.run(("git", "-C", root) + args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class TestEndToEnd(Fix, unittest.TestCase):
    """The real generator and brief children; only the launch and the writers are faked."""

    def setUp(self):
        super().setUp()
        init_repo(self.root)
        os.environ["GIT_CEILING_DIRECTORIES"] = self.tmp

    def tearDown(self):
        os.environ.pop("GIT_CEILING_DIRECTORIES", None)
        super().tearDown()

    def runner(self, launches):
        def run(argv, cwd, env):
            if "start_run.sh" in " ".join(argv):
                launches.append((argv, env))
                return (0, "STARTED")
            return PP._default_runner(argv, cwd, env)
        return run

    def seed_with(self, ids):
        plan = {"schema": "brother-release-wbs/1", "as_of": "2026-10-14 (fixture)", "units": [unit(i) for i in ids]}
        seed = PS.build_seed(VERSION, plan, None, None, [], grant(), time.time(), self.root, from_plan=None)
        for u in seed["units"]:  # fresh planner rows, not carried ones
            for key in ("carried", "sub_units", "worker", "spec", "done_check", "checker", "from_plan"):
                u.pop(key, None)
        self.put_seed(seed)

    def test_the_whole_chain_generates_briefs_and_launches_once(self):
        self.seed_with(["AA1", "BB2"])
        launches = []
        rec = self.go(self.runner(launches), launch=True)
        self.assertEqual(rec["exit"], 0, json.dumps(rec["stages"], indent=1))
        self.assertTrue(os.path.isfile(os.path.join(self.root, WBS)))
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, SPEC_DIR, "briefs"))), ["AA1.a.md", "BB2.a.md"])
        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0][1], {"BROTHER_SPEC_FLOOR": "9", "BROTHER_WBS": WBS})
        self.assertEqual((rec["width"], rec["arithmetic"].split(" = ")[0]), (2, "ARITHMETIC: ceil(2 / 2)"))
        self.assertEqual([s["stage"] for s in rec["stages"]], list(PP.STAGES))

    def test_an_empty_seed_stops_at_the_generator_and_records_it(self):
        self.seed_with([])
        launches = []
        rec = self.go(self.runner(launches), launch=True)
        self.assertEqual(rec["exit"], 1)
        self.assertEqual(rec["stages"][-1]["stage"], "generate")
        self.assertEqual(launches, [])
        self.assertIn("no unit", rec["stages"][-1]["output_tail"])

    def test_a_second_run_stops_at_generate_and_keeps_the_first_record(self):
        self.seed_with(["AA1"])
        launches = []
        self.assertEqual(self.go(self.runner(launches), launch=True)["exit"], 0)
        again = self.go(self.runner(launches), launch=True)
        self.assertEqual((again["exit"], again["stages"][-1]["stage"], len(launches)), (1, "generate", 1))
        self.assertIn("exists already", again["stages"][-1]["output_tail"])
        prev = [n for n in os.listdir(os.path.join(self.root, SPEC_DIR)) if n.startswith("PIPELINE.json.prev-")]
        self.assertEqual(len(prev), 1)

    def test_the_command_prints_the_stages_and_the_closing_line(self):
        self.seed_with(["AA1"])
        launches = []
        code, out = self.main(["--seed", self.seed_path, "--version", VERSION, "--launch"], self.runner(launches))
        self.assertEqual(code, 0, out)
        for line in ("STAGE seed exit 0", "STAGE generate exit 0", "STAGE briefs exit 0", "STAGE launch exit 0"):
            self.assertIn(line, out)
        self.assertIn("PIPELINE: stages 4 width 1 ARITHMETIC: ceil(1 / 1) = 1 waves", out)

    def test_nothing_is_launched_without_the_flag_even_with_real_children(self):
        self.seed_with(["AA1"])
        launches = []
        code, out = self.main(["--seed", self.seed_path, "--version", VERSION], self.runner(launches))
        self.assertEqual((code, launches), (0, []))
        self.assertIn("LAUNCH (not started, add --launch)", out)


class TestNoDashes(unittest.TestCase):
    def test_the_source_and_this_test_hold_no_dash_or_clock_text(self):
        clock = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)")
        for name in ("plan_pipeline.py", "test_plan_pipeline.py"):
            with open(os.path.join(HERE, name), encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn(chr(0x2013), text, name)
            self.assertNotIn(chr(0x2014), text, name)
            self.assertIsNone(clock.search(text), name)


class TestNothingToWrite(unittest.TestCase):
    """2026-10-14: a plan of carried units only has no batch to write, and the pipeline goes on to the launch."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._td.name, "WRITER-ASSIGNMENTS.json")

    def tearDown(self):
        self._td.cleanup()

    def write(self, batches):
        with open(self.path, "w") as fh:
            json.dump({"batches": batches}, fh)

    def test_all_carried_and_zero_batches_is_nothing_to_write(self):
        self.write([])
        self.assertTrue(PP._nothing_to_write({"units": [{"id": "A1", "carried": True}]}, self.path))

    def test_any_fresh_unit_or_any_batch_or_an_unreadable_file_still_goes_through_briefs(self):
        self.write([])
        self.assertFalse(PP._nothing_to_write({"units": [{"id": "A1", "carried": True}, {"id": "B1"}]}, self.path))
        self.assertFalse(PP._nothing_to_write({"units": []}, self.path))
        self.write([{"id": "A1.a"}])
        self.assertFalse(PP._nothing_to_write({"units": [{"id": "A1", "carried": True}]}, self.path))
        with open(self.path, "w") as fh:
            fh.write("{torn")
        self.assertFalse(PP._nothing_to_write({"units": [{"id": "A1", "carried": True}]}, self.path))


if __name__ == "__main__":
    unittest.main()
