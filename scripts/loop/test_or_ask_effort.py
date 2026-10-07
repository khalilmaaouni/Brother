"""Every OpenRouter call runs at reasoning effort xhigh (owner order 2026-09-26: "For all openrouter calls set them to
xhigh for all models").

Before: the bridge sent no effort unless a caller named one, so the provider chose; model_router's liveness probe asked
at "--effort low --max 1200"; and the bridge's xhigh ceiling floor was 6000 tokens, which its own comment marked as an
unconfirmed estimate (reasoning and answer share one budget, so a small ceiling reads as an empty answer). Now the bridge
defaults to xhigh, lifts any lower request to xhigh and says so, floors the xhigh ceiling at 32000 (billed only for tokens
generated), and the probe asks at xhigh. Jev's typed decisions endpoint takes no effort and is untouched.
No network: the request is captured by a fake urlopen, the probe's command by a fake subprocess.run.
Run: python3 -B scripts/loop/test_or_ask_effort.py
"""
import contextlib, importlib.util, io, json, os, subprocess, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load_bridge():
    spec = importlib.util.spec_from_file_location("or_ask_effort_under_test", os.path.join(HERE, "or_ask.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.read_key = lambda account=None: "\x73k-or-v1-test-not-a-real-key"
    return mod


def ask(mod, argv):
    seen = []

    def fake(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        seen.append(body)
        data = json.dumps({"model": body["model"], "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                           "choices": [{"message": {"content": "4"}, "finish_reason": "stop"}]}).encode("utf-8")

        class Resp(object):
            def read(self_inner):
                return data
        return contextlib.nullcontext(Resp())
    real = mod.urllib.request.urlopen
    mod.urllib.request.urlopen = fake
    out, err, old = io.StringIO(), io.StringIO(), sys.argv
    sys.argv = ["or_ask.py"] + argv + ["two", "plus", "two"]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main()
    finally:
        sys.argv = old
        mod.urllib.request.urlopen = real
    return code, seen, err.getvalue()


class BridgeEffort(unittest.TestCase):
    def test_no_effort_named_means_xhigh(self):
        code, seen, _ = ask(load_bridge(), ["--model", "deepseek"])
        self.assertEqual(code, 0)
        self.assertEqual(seen[0].get("reasoning"), {"effort": "xhigh"})

    def test_a_lower_effort_is_lifted_to_xhigh_and_said(self):
        code, seen, err = ask(load_bridge(), ["--model", "deepseek", "--effort", "low"])
        self.assertEqual(seen[0].get("reasoning"), {"effort": "xhigh"})
        self.assertIn("xhigh", err)

    def test_the_xhigh_ceiling_is_at_least_32000(self):
        code, seen, _ = ask(load_bridge(), ["--model", "deepseek", "--max", "1200"])
        self.assertGreaterEqual(seen[0]["max_tokens"], 32000)


class ProbeEffort(unittest.TestCase):
    def test_the_liveness_probe_asks_at_xhigh(self):
        """model_router.probe delegates to model_reachability.prove (2026-09-30), which sends the production invocation:
        the bridge argv it runs carries --effort xhigh, read from the call the fake runner receives. No real call."""
        import tempfile, shutil
        import model_reachability as MR
        d = tempfile.mkdtemp(prefix="probe-effort-"); self.addCleanup(shutil.rmtree, d, True)
        keep = {k: os.environ.get(k) for k in ("BROTHER_PROOF_CACHE", "BROTHER_OR_STATE_ROOT", "BROTHER_BRIDGE_CALLS_LEDGER")}
        os.environ.update(BROTHER_PROOF_CACHE=os.path.join(d, "p.json"), BROTHER_OR_STATE_ROOT=os.path.join(d, "s"),
                          BROTHER_BRIDGE_CALLS_LEDGER=os.path.join(d, "b.jsonl"))
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in keep.items()])
        seen = []
        MR.prove("deepseek", runner=lambda argv, stdin, t: seen.append(argv) or {"returncode": 0, "stdout": "12\n", "stderr": ""})
        cmd = seen[0]
        self.assertIn("--effort", cmd)
        self.assertEqual(cmd[cmd.index("--effort") + 1], "xhigh", cmd)


if __name__ == "__main__":
    unittest.main(verbosity=1)
