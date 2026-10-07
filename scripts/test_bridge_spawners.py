"""The outside bridge (~/.claude/bin/or_ask.py, OpenRouter) is named in exactly the runtime files below, each behind
the model router's transport allowlist (BROTHER_TRANSPORTS, owner 2026-09-30: a Claude only run) or a dispatcher
those gated files feed. A new file that names the bridge path in a string literal is a new way for a call to leave the
machine around the gate, and this goes red naming it until the file is gated and listed here with its reason.

Read with the standard library `ast` only: no module here is imported, so no loop_hold gate or side effect runs."""
import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ROOTS = ("scripts", os.path.join("plugin", "runtime", "brother", "core"))
BRIDGE = "or_ask.py"

# relative path: why naming the bridge there is gated, or is not a model call
ALLOWED = {
    "plugin/runtime/brother/core/openrouter_dispatch.py": "the dispatcher: every job reaches it through or_fanout.run_job, gated at _assert_privacy (assert_may_send)",
    "plugin/runtime/brother/core/or_dispatch_cli.py": "BRIDGE_PATH definition read by or_fanout, gated as above",
    "scripts/bridge_default_model.py": "reads the bridge's default model name from its source; never runs it",
    "scripts/capability_probe.py": "capability listing of the bridge's own flags; a probe, off every pass",
    "scripts/jev_decide.py": "the one Jev entry: decide() asks bridge_refused() (the router's allowlist) before any bridge",
    "scripts/jev_eval.py": "evaluation tool over jev_decide records; off every pass",
    "scripts/loop/abc/model_conformance.py": "conformance bench: its direct Jev call asks assert_may_send first",
    "scripts/loop/model_call.py": "BRIDGE definition; call_one asks assert_may_send at the wire",
    "scripts/loop/grade_build.py": "names the bridge only in the protected path list a build may not edit; not a model call",
    "scripts/loop/tool_stamp.py": "stamps the deployed tools by path; never runs the bridge",
}


def files_naming_the_bridge(root=ROOT):
    out = {}
    for base in ROOTS:
        for dp, _dn, fn in os.walk(os.path.join(root, base)):
            if "fixtures" in dp.split(os.sep):
                continue
            for f in fn:
                if not f.endswith(".py") or f.startswith("test_"):
                    continue
                p = os.path.join(dp, f)
                with open(p, encoding="utf-8") as fh:
                    src = fh.read()
                if BRIDGE not in src:
                    continue
                try:
                    tree = ast.parse(src)
                except SyntaxError:
                    continue
                n = sum(1 for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and BRIDGE in node.value and len(node.value) < 200)
                if n:
                    out[os.path.relpath(p, root).replace(os.sep, "/")] = n
    return out


class TheBridgeIsNamedOnlyWhereItIsGated(unittest.TestCase):
    def test_every_runtime_file_naming_the_bridge_is_listed_with_its_reason(self):
        found = files_naming_the_bridge()
        self.assertTrue(found, "no runtime file names the bridge: the scan is broken, not the tree")
        extra = sorted(set(found) - set(ALLOWED))
        self.assertEqual(extra, [], "new file(s) name the bridge path outside the gated set: %s" % extra)

    def test_every_listed_file_still_names_the_bridge(self):
        stale = sorted(set(ALLOWED) - set(files_naming_the_bridge()))
        self.assertEqual(stale, [], "listed file(s) no longer name the bridge; drop them from ALLOWED: %s" % stale)

    def test_the_scan_catches_a_new_spawner(self):
        import shutil, tempfile
        d = tempfile.mkdtemp(prefix="bridge-spawners-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "scripts", "loop"))
        with open(os.path.join(d, "scripts", "loop", "sneaky.py"), "w") as fh:
            fh.write('import subprocess\nsubprocess.run(["python3", "/x/.claude/bin/or_ask.py"])\n')
        with open(os.path.join(d, "scripts", "loop", "commented.py"), "w") as fh:
            fh.write('# or_ask.py is mentioned in a comment only\nX = 1\n')
        self.assertEqual(files_naming_the_bridge(d), {"scripts/loop/sneaky.py": 1})


class TheConformanceBenchAsksTheGateBeforeItsDirectBridgeCall(unittest.TestCase):
    """scripts/loop/abc/model_conformance.py is the one runtime file that spawns the bridge directly (a bench, off every
    pass); its Jev call must sit inside a try whose first statement asks assert_may_send. Read by ast, never imported."""

    def test_assert_may_send_is_the_first_statement_of_the_try_holding_the_bridge_call(self):
        path = os.path.join(ROOT, "scripts", "loop", "abc", "model_conformance.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and any(BRIDGE in (getattr(c, "value", "") or "") for st in node.body for c in ast.walk(st)
                                                 if isinstance(c, ast.Constant) and isinstance(c.value, str)):
                first = node.body[0]
                self.assertIsInstance(first, ast.Expr)
                call = first.value
                self.assertIsInstance(call, ast.Call)
                self.assertEqual(getattr(call.func, "attr", None), "assert_may_send", ast.dump(first)[:200])
                found = True
        self.assertTrue(found, "no try block holding the bridge call was found in %s" % path)


class TheBridgeAndTheMeterRefuseThemselves(unittest.TestCase):
    """The source: scripts/loop/or_ask.py and scripts/loop/abc/or_meter.py refuse under an allowlist without the bridge,
    before the keychain is read and before any request. Run as processes with a stand-in `security` on PATH that
    writes a marker if the key is ever asked for."""

    def setUp(self):
        import shutil, tempfile
        self.dir = tempfile.mkdtemp(prefix="bridge-refuses-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.mark = os.path.join(self.dir, "keychain-asked")
        with open(os.path.join(self.dir, "security"), "w") as fh:
            fh.write("#!/bin/sh\ntouch '%s'\necho fakekey\n" % self.mark)
        os.chmod(os.path.join(self.dir, "security"), 0o755)
        self.env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
        self.env["PATH"] = self.dir + os.pathsep + self.env.get("PATH", "")

    def run_under(self, argv, transports, stdin=""):
        import subprocess, sys
        env = dict(self.env); env.pop("BROTHER_TRANSPORTS", None)
        if transports is not None:
            env["BROTHER_TRANSPORTS"] = transports
        return subprocess.run([sys.executable, "-B"] + argv, cwd=ROOT, env=env, input=stdin, capture_output=True, text=True, timeout=120)

    def test_or_ask_refuses_with_exit_46_naming_the_setting_and_never_reads_the_key(self):
        r = self.run_under([os.path.join(ROOT, "scripts", "loop", "or_ask.py"), "--model", "deepseek", "--timeout", "5", "--", "7 plus 5"], "claude")
        self.assertEqual(r.returncode, 46, r.stdout + r.stderr)
        self.assertIn("BROTHER_TRANSPORTS=claude", r.stderr)
        self.assertFalse(os.path.exists(self.mark), "the keychain was asked under BROTHER_TRANSPORTS=claude")

    def test_or_ask_refuses_a_malformed_allowlist_too(self):
        r = self.run_under([os.path.join(ROOT, "scripts", "loop", "or_ask.py"), "--model", "deepseek", "--timeout", "5", "--", "7 plus 5"], "claude,-bridge")
        self.assertEqual(r.returncode, 46, r.stdout + r.stderr); self.assertFalse(os.path.exists(self.mark))

    def test_or_meter_refuses_with_exit_3_and_writes_no_row(self):
        meter = os.path.join(ROOT, "scripts", "loop", "abc", "or_meter.py"); rows = os.path.join(ROOT, "scripts", "loop", "abc", "or-meter.jsonl")
        before = os.path.getsize(rows) if os.path.exists(rows) else None
        r = self.run_under([meter, "self-test"], "claude")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr); self.assertIn("NO-DATA or_meter", r.stdout)
        self.assertFalse(os.path.exists(self.mark), "the keychain was asked")
        self.assertEqual(os.path.getsize(rows) if os.path.exists(rows) else None, before)

    def test_or_balance_refuses_an_unknown_argument_before_any_fetch(self):
        r = self.run_under([os.path.join(ROOT, "scripts", "loop", "or_balance.py"), "--self-test"], None)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr); self.assertIn("REFUSED: unknown argument", r.stdout)
        self.assertFalse(os.path.exists(self.mark), "the keychain was asked on an unknown argument")


class TheRunnerBlocksOnARefusedChain(unittest.TestCase):
    """scripts/loop/unit_runner.py bridge_models(): a chain the router refuses (a pin outside BROTHER_TRANSPORTS) is a
    named BLOCKED with exit 2, never a traceback. The function is extracted by ast (the module runs a unit at import)
    and executed against the REAL model_router with status and sys.exit stubbed."""

    def run_bridge_models(self, env):
        import sys as real_sys, types
        from unittest import mock
        path = os.path.join(ROOT, "scripts", "loop", "unit_runner.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "bridge_models")
        module = ast.Module(body=[fn], type_ignores=[]); ast.fix_missing_locations(module)
        loop = os.path.join(ROOT, "scripts", "loop")
        if loop not in real_sys.path:
            real_sys.path.insert(0, loop)
        said = []
        def _exit(code): raise SystemExit(code)
        stub_sys = types.SimpleNamespace(path=[], exit=_exit, modules=real_sys.modules)
        ns = {"os": os, "sys": stub_sys, "BIN": loop, "status": said.append}
        exec(compile(module, path, "exec"), ns)  # noqa: S102
        with mock.patch.dict(os.environ, env, clear=False):
            for k in ("BROTHER_TRANSPORTS", "BROTHER_PIN_MODEL"):
                if k not in env: os.environ.pop(k, None)
            try:
                return ns["bridge_models"](2), said
            except SystemExit as exc:
                return ("exit", exc.code), said

    def test_a_pinned_bridge_model_under_the_allowlist_is_a_named_blocked_exit_2(self):
        got, said = self.run_bridge_models({"BROTHER_TRANSPORTS": "claude", "BROTHER_PIN_MODEL": "deepseek"})
        self.assertEqual(got, ("exit", 2), (got, said))
        self.assertTrue(said and said[0].startswith("BLOCKED the build chain is refused:"), said)
        self.assertIn("BROTHER_TRANSPORTS", said[0])

    def test_without_the_allowlist_the_same_pin_is_served(self):
        got, said = self.run_bridge_models({"BROTHER_PIN_MODEL": "deepseek"})
        self.assertEqual(got, ["deepseek"], (got, said))


if __name__ == "__main__":
    unittest.main()
