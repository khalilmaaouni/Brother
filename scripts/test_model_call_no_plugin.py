"""model_call without the plugin runtime: it loads, and it refuses every call rather than running one outside the pool.

The model-admission merge (2026-09-26) made scripts/loop/model_call.py import plugin.runtime.brother.core at load time.
The public export ships scripts/ and not plugin/, so the module could not even be imported there (the pre-push gate's
hermetic run: "No module named 'plugin'"). The shared admission pool is what bounds concurrent paid calls, so where it is
absent the only safe answer is a refusal: never an unbounded call, never a crash at import.
Each case runs a child Python whose import system refuses every `plugin` module, so the check does not depend on
whether this checkout ships plugin/. Run from the repository root: python3 -B scripts/test_model_call_no_plugin.py
"""
import json, os, subprocess, sys, tempfile, textwrap, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP = os.path.join(ROOT, "scripts", "loop")

CHILD = textwrap.dedent("""
    import importlib.abc, json, os, sys
    class NoPlugin(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name == "plugin" or name.startswith("plugin."):
                raise ModuleNotFoundError("No module named %r (blocked by the test)" % name)
            return None
    sys.meta_path.insert(0, NoPlugin())
    sys.path.insert(0, {loop!r})
    import model_call as MC
    called = []
    reg = {{"k": {{"id": "claude-x", "transport": "claude", "privacy": "private", "kinds": {{"build"}}, "cost": 1, "quality": {{"build": 5}}}}}}
    a = MC.call_one("k", "hello", "build", "private", timeout=30, reg=reg,
                    runner=lambda argv, stdin, timeout: called.append(argv) or {{"returncode": 0, "stdout": "{{}}", "stderr": ""}})
    print(json.dumps({{"ok": a.ok, "why": a.detail, "runner_called": len(called)}}))
""")


def run_child():
    d = tempfile.mkdtemp(prefix="mc-noplugin-")
    p = os.path.join(d, "child.py")
    with open(p, "w") as f:
        f.write(CHILD.format(loop=LOOP))
    env = dict(os.environ, HOME=d, BROTHER_CLAUDE_CALLS_LEDGER=os.path.join(d, "calls.jsonl"), BROTHER_REPO_ROOT=d)
    return subprocess.run([sys.executable, "-B", p], capture_output=True, text=True, timeout=120, env=env)


class NoPluginRuntime(unittest.TestCase):
    def test_model_call_loads_without_the_plugin_runtime(self):
        r = run_child()
        self.assertEqual(r.returncode, 0, r.stderr[-400:])

    def test_a_call_is_refused_and_no_model_command_runs(self):
        r = run_child()
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertFalse(got["ok"])
        self.assertEqual(got["runner_called"], 0, "a call ran outside the shared admission pool")
        self.assertIn("admission", got["why"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
