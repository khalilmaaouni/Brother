"""Calibration for scripts/model_worker.py.

No network, no real claude: MODEL_WORKER_CMD points every case at a tiny
stub script written to a tempdir, mirroring bm_worker_spawn's own injected-
runner style but at the process boundary rather than the function boundary,
since model_worker's contract is a whole subprocess, not a function.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "model_worker.py")
sys.path.insert(0, HERE)


def write_stub(tmpdir, body):
    path = os.path.join(tmpdir, "stub_model.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def run_worker(stdin_text, cwd, env_extra=None):
    env = dict(os.environ)
    env.pop("MODEL_WORKER_CMD", None)
    env.pop("MODEL_WORKER_TIMEOUT_S", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, WORKER], input=stdin_text, cwd=cwd,
                          env=env, capture_output=True, text=True, timeout=60)


def git_init(cwd):
    for args in (["init", "-q"], ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                       text=True, timeout=30, check=True)


class ModelWorkerSuccess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mw-")
        git_init(self.tmp)
        self.stub = write_stub(self.tmp, """
            import sys
            sys.stdin.read()
            with open("created.txt", "w") as fh:
                fh.write("made it\\n")
            print("did the thing")
        """)

    def test_creates_file_and_reports_artifact(self):
        brief = json.dumps({"id": "U1", "objective": "make a file",
                            "writes": ["created.txt"]})
        proc = run_worker(brief, self.tmp,
                          {"MODEL_WORKER_CMD": "%s %s" % (sys.executable, self.stub)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("worker_claim", out)
        self.assertTrue(out["worker_claim"])
        self.assertIn("created.txt", out["artifacts"])
        self.assertEqual(out["cost"], {})


class ModelWorkerNonzeroExit(unittest.TestCase):
    def test_nonzero_model_exit_is_unavailable(self):
        tmp = tempfile.mkdtemp(prefix="mw-")
        stub = write_stub(tmp, """
            import sys
            sys.stdin.read()
            sys.exit(7)
        """)
        brief = json.dumps({"id": "U2", "objective": "fail"})
        proc = run_worker(brief, tmp,
                          {"MODEL_WORKER_CMD": "%s %s" % (sys.executable, stub)})
        self.assertNotEqual(proc.returncode, 0)
        # stdout is not required to be JSON on the unavailable path; the
        # contract only promises one clean JSON object on success.
        self.assertEqual(proc.stdout.strip(), "")


class ModelWorkerGarbageStdin(unittest.TestCase):
    def test_garbage_stdin_exits_nonzero_with_readable_stderr(self):
        tmp = tempfile.mkdtemp(prefix="mw-")
        proc = run_worker("not json at all {{{", tmp)
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(proc.stderr.strip())


class ModelWorkerDoneCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mw-")
        self.stub = write_stub(self.tmp, """
            import sys
            sys.stdin.read()
            print("ok")
        """)
        self.env = {"MODEL_WORKER_CMD": "%s %s" % (sys.executable, self.stub)}

    def test_passing_done_check_reports_exit_code_0(self):
        brief = json.dumps({"id": "U3", "objective": "x", "done_check": "true"})
        proc = run_worker(brief, self.tmp, self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("exit code: 0", out["worker_claim"])

    def test_failing_done_check_reports_the_code_but_worker_still_exits_0(self):
        brief = json.dumps({"id": "U4", "objective": "x", "done_check": "false"})
        proc = run_worker(brief, self.tmp, self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("exit code: 1", out["worker_claim"])

    def test_missing_done_check_is_named_as_such(self):
        brief = json.dumps({"id": "U5", "objective": "x"})
        proc = run_worker(brief, self.tmp, self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("no done_check", out["worker_claim"])


class ModelWorkerDefensiveFields(unittest.TestCase):
    def test_missing_fields_never_crash_the_prompt_builder(self):
        import model_worker as mw  # noqa: PLC0415 (path set at module import time)
        prompt = mw.build_prompt({})
        self.assertIn("Unit id:", prompt)
        self.assertIn("Objective:", prompt)


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    unittest.main()
