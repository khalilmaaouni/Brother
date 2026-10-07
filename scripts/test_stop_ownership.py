"""stop_loop.sh stops only what the loop owns (finding 1b, reproduced by REVIEW-STOP-OWNERSHIP-2026-09-26).

Its pattern named `claude -p --model`, `codex exec -m`, `bin/or_ask.py` and `core.or_fanout` outright, so a stop on this
machine could signal any session's model CLI, and the orchestrator's own OpenRouter calls. The rule now lives in one
helper, scripts/loop/loop_procs.py, evaluated on ONE process snapshot (pid, parent, process group, command):
  - a loop-only tool (driver, runner, grade, probe, check, repair and finisher tools) is selected by its name, and only
    when it belongs to a run of this loop: its program in $HOME/.claude/bin, or its environment naming a run under the
    runs root (scripts/test_stop_run_identity.py covers that half; every fixture here lives in the fixture home's bin);
  - a generic model process is selected only when its parent chain, or its process group, leads to a live loop process;
  - anything else, an unknown owner included, is never selected, so it is never signalled;
  - a parent pid now held by an unrelated command (pid reuse) proves nothing, because the snapshot shows that command.
Run from the repository root: python3 -B scripts/test_stop_ownership.py
"""
import os, signal, subprocess, sys, tempfile, time, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP = os.path.join(ROOT, "scripts", "loop")
STOP = os.path.join(LOOP, "stop_loop.sh")
sys.path.insert(0, LOOP)
import loop_procs as LP  # noqa: E402

HOME = "/Users/you"   # the placeholder home shipped files may name (scripts/test_export_public.py)
RUNNER = HOME + "/.claude/bin/unit_runner.py H3 H3.d"
FANOUT = "python3 -m plugin.runtime.brother.core.or_fanout jobs.json --workers 3"
ORASK = "python3 /Users/x/tools/bin/or_ask.py --model deepseek"
CLAUDE = "/usr/local/bin/claude -p --model sonnet"
CODEX = "/usr/local/bin/codex exec -m gpt"
RUN_VARS = ("BROTHER_RUN_DIR", "BROTHER_RUNS_ROOT", "STOP_LOOP_ONLY")   # the driver exports the first two to all it starts


def rows(*r):
    return [dict(pid=a, ppid=b, pgid=c, command=d) for a, b, c, d in r]


def fixture_env(home, **extra):
    """The environment a fixture stop or count runs under: HOME naming the fixture, no run marker inherited from a driver."""
    env = {k: v for k, v in os.environ.items() if k not in RUN_VARS}
    env["HOME"] = home
    env.update(extra)
    return env


class Rule(unittest.TestCase):
    def owned(self, snap, only=""):
        with mock.patch.dict(os.environ, fixture_env(HOME), clear=True):
            return set(LP.owned(snap, LP.RUNNERS_RX, only=only, self_pid=1))

    def test_unrelated_model_clis_are_never_selected(self):
        snap = rows((610001, 1, 610001, CLAUDE + " --cwd /tmp/other"), (610002, 1, 610002, CODEX + " -C /tmp/other"), (610003, 1, 610003, ORASK))
        self.assertEqual(self.owned(snap), set())

    def test_a_loop_tool_is_selected_by_name(self):
        self.assertEqual(self.owned(rows((500, 1, 500, "python3 " + RUNNER))), {500})

    def test_a_model_cli_under_a_runner_is_selected(self):
        snap = rows((500, 1, 500, "python3 " + RUNNER), (501, 500, 501, FANOUT), (502, 501, 501, ORASK), (503, 500, 500, CLAUDE))
        self.assertEqual(self.owned(snap), {500, 501, 502, 503})

    def test_an_orphan_in_a_loop_process_group_is_selected(self):
        """the runner's own child exited; the model CLI was reparented to 1 but keeps the runner's process group"""
        snap = rows((500, 1, 500, "python3 " + RUNNER), (504, 1, 500, CLAUDE))
        self.assertEqual(self.owned(snap), {500, 504})

    def test_a_reused_parent_pid_proves_nothing(self):
        """pid 500 once was a runner; now it is an editor. Its child model CLI is not the loop's."""
        snap = rows((500, 1, 500, "/usr/bin/vim notes.md"), (505, 500, 505, CLAUDE))
        self.assertEqual(self.owned(snap), set())

    def test_a_shell_that_names_a_tool_is_not_the_tool(self):
        snap = rows((600, 1, 600, "/bin/bash -c python3 " + RUNNER))
        self.assertEqual(self.owned(snap), set())

    def test_only_narrows_the_selection(self):
        snap = rows((500, 1, 500, "python3 %s/.claude/bin/unit_runner.py A A.1" % HOME), (510, 1, 510, "python3 %s/.claude/bin/unit_runner.py B B.1" % HOME))
        self.assertEqual(self.owned(snap, only="unit_runner.py A "), {500})


def fake_ps(d, table):
    """A ps that answers the two shapes the stop script asks for, from a fixed table: no real process is listed."""
    p = os.path.join(d, "ps")
    with open(p, "w") as f:
        f.write("#!/usr/bin/env python3\nimport sys\nT=%r\na=' '.join(sys.argv[1:])\n" % (table,)
                + "if '-p' in sys.argv:\n    pid=int(sys.argv[sys.argv.index('-p')+1]); r=[t for t in T if t[0]==pid]\n"
                + "    print(r[0][3] if r else ''); sys.exit(0 if r else 1)\n"
                + "for t in T: print('%d %d %d %s' % t)\n")
    os.chmod(p, 0o755)


class Script(unittest.TestCase):
    def test_dry_run_leaves_unrelated_model_clis_out(self):
        d = tempfile.mkdtemp(prefix="stop-own-")
        os.makedirs(os.path.join(d, ".claude", "evidence"))
        runner = "python3 %s/.claude/bin/unit_runner.py H3 H3.d" % d   # the fixture home's own bin: a runner of this loop
        fake_ps(d, [(610001, 1, 610001, CLAUDE + " --cwd /tmp/unrelated-project"), (610002, 1, 610002, CODEX + " -C /tmp/unrelated-project"),
                    (620001, 1, 620001, runner), (620002, 620001, 620001, CLAUDE)])
        env = fixture_env(d, PATH=d + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["bash", STOP, "--dry"], env=env, capture_output=True, text=True, timeout=60)
        self.assertNotIn("610001", r.stdout); self.assertNotIn("610002", r.stdout)
        self.assertIn("620001", r.stdout); self.assertIn("620002", r.stdout)


class RealProcesses(unittest.TestCase):
    def test_owned_children_are_stopped_and_an_unrelated_cli_survives(self):
        d = tempfile.mkdtemp(prefix="stop-real-")
        home = os.path.join(d, "home")
        os.makedirs(os.path.join(home, ".claude", "bin")); os.makedirs(os.path.join(home, ".claude", "evidence"))
        cli = os.path.join(d, "claude")
        with open(cli, "w") as f:
            f.write("#!/usr/bin/env python3\nimport time\ntime.sleep(120)\n")
        os.chmod(cli, 0o755)
        runner = os.path.join(home, ".claude", "bin", "unit_runner.py")   # the fixture home's own bin: a runner of this loop
        with open(runner, "w") as f:
            f.write("import subprocess, sys, time\nsubprocess.Popen([sys.executable, %r, '-p', '--model', 'owned'])\ntime.sleep(120)\n" % cli)
        r_proc = subprocess.Popen([sys.executable, runner], start_new_session=True)
        other = subprocess.Popen([sys.executable, cli, "-p", "--model", "unrelated"], start_new_session=True)
        try:
            time.sleep(1.5)
            env = fixture_env(home, STOP_LOOP_ONLY=d)
            r = subprocess.run(["bash", STOP, "--runners-only"], env=env, capture_output=True, text=True, timeout=90)
            time.sleep(0.5)
            self.assertIsNotNone(r_proc.poll(), "the owned runner survived: " + r.stdout[-300:])
            self.assertIsNone(other.poll(), "an unrelated model CLI was signalled: " + r.stdout[-300:])
            left = subprocess.run(["pgrep", "-f", cli + " -p --model owned"], capture_output=True, text=True)
            self.assertEqual(left.stdout.strip(), "", "the runner's model CLI child survived the stop")
        finally:
            for p in (r_proc, other):
                try: os.killpg(p.pid, signal.SIGKILL)
                except OSError: pass
                p.wait()
            subprocess.run(["pkill", "-9", "-f", cli], capture_output=True)   # this fixture's own processes only


if __name__ == "__main__":
    unittest.main(verbosity=1)
