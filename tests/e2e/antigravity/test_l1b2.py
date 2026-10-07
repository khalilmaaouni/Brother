"""Tests for L1b.2 plugin load gate (tests/e2e/antigravity/run_e2e.py)."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_run_e2e():
    path = os.path.join(_HERE, "run_e2e.py")
    if not os.path.isfile(path):
        raise RuntimeError("run_e2e.py not found at %s" % path)
    spec = importlib.util.spec_from_file_location("_l1b2_run_e2e_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_e2e = _load_run_e2e()


def _write(path, content):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        if isinstance(content, bytes):
            handle.write(content)
        else:
            handle.write(content.encode("utf-8"))


def _fake_host(root, stdout="9.9.9-test\n", code=0, marker=None):
    """An executable standing in for the host binary. It prints ``stdout``,
    exits ``code`` and, with ``marker``, records the argv it received, so a
    test can prove the host was RUN rather than assumed."""
    path = os.path.join(root, "fake-host")
    _write(path + ".out", stdout)
    lines = ["#!/bin/sh"]
    if marker:
        lines.append("for a in \"$@\"; do echo \"$a\"; done > '%s'" % marker)
    lines.append("cat '%s'" % (path + ".out"))
    lines.append("exit %d" % code)
    _write(path, "\n".join(lines) + "\n")
    os.chmod(path, 0o755)
    return path


def _make_sandbox(root):
    _write(os.path.join(root, "plugin.json"),
           json.dumps({"name": "brother-antigravity", "version": "0.0.0"}))
    _write(os.path.join(root, "mcp_config.json"),
           json.dumps({"mcpServers": {}}))
    _write(os.path.join(root, "skills", "using-brother", "SKILL.md"),
           "# using brother\n")
    _write(os.path.join(root, "skills", "review", "SKILL.md"),
           "# review\n")
    _write(os.path.join(root, "rules", "core.md"), "# core rule\n")
    return root


class TestPluginLoad(unittest.TestCase):

    def test_host_resolves_or_blocks(self):
        saved = os.environ.get("ANTIGRAVITY_BIN")
        try:
            os.environ["ANTIGRAVITY_BIN"] = os.path.join(
                tempfile.gettempdir(), "does-not-exist-agy-for-L1b2")
            with self.assertRaises(run_e2e.HostUnresolvedError):
                run_e2e.resolve_host()
        finally:
            if saved is None:
                os.environ.pop("ANTIGRAVITY_BIN", None)
            else:
                os.environ["ANTIGRAVITY_BIN"] = saved

    def test_unresolved_host_is_no_data(self):
        saved_bin = os.environ.get("ANTIGRAVITY_BIN")
        saved_path = os.environ.get("PATH")
        try:
            os.environ["ANTIGRAVITY_BIN"] = os.path.join(
                tempfile.gettempdir(), "definitely-not-here-agy-L1b2")
            os.environ["PATH"] = ""
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run_e2e.main([])
            self.assertNotEqual(rc, 0)
            self.assertIn("no_data", buf.getvalue())
        finally:
            if saved_bin is None:
                os.environ.pop("ANTIGRAVITY_BIN", None)
            else:
                os.environ["ANTIGRAVITY_BIN"] = saved_bin
            if saved_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = saved_path

    def test_no_invented_verbs(self):
        saved = os.environ.get("ANTIGRAVITY_BIN")
        try:
            os.environ["ANTIGRAVITY_BIN"] = sys.executable
            host_bin, argv = run_e2e.resolve_host()
        finally:
            if saved is None:
                os.environ.pop("ANTIGRAVITY_BIN", None)
            else:
                os.environ["ANTIGRAVITY_BIN"] = saved
        self.assertEqual(host_bin, sys.executable)
        self.assertEqual(argv, [host_bin])
        joined = " ".join(argv)
        for invented in ("plugin install", "plugin add", "plugin load", "plugin enable"):
            self.assertNotIn(invented, joined)

    def test_resolve_host_rejects_extra_args(self):
        saved = os.environ.get("ANTIGRAVITY_BIN")
        try:
            os.environ["ANTIGRAVITY_BIN"] = sys.executable
            for bad in (None, 123, [], {}):
                with self.assertRaises(run_e2e.HostUnresolvedError):
                    run_e2e.resolve_host(bad)
                with self.assertRaises(run_e2e.HostUnresolvedError):
                    run_e2e.resolve_host(extra=bad)
        finally:
            if saved is None:
                os.environ.pop("ANTIGRAVITY_BIN", None)
            else:
                os.environ["ANTIGRAVITY_BIN"] = saved

    def test_main_rejects_non_iterable_argv(self):
        saved = os.environ.get("ANTIGRAVITY_BIN")
        try:
            os.environ["ANTIGRAVITY_BIN"] = sys.executable
            for bad in (True, False, 123, 1.5, "path", b"path"):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = run_e2e.main(bad)
                self.assertEqual(rc, 2, msg=repr(bad))
                self.assertIn("no_data", buf.getvalue())
        finally:
            if saved is None:
                os.environ.pop("ANTIGRAVITY_BIN", None)
            else:
                os.environ["ANTIGRAVITY_BIN"] = saved

    def test_main_rejects_non_string_argv_items(self):
        saved = os.environ.get("ANTIGRAVITY_BIN")
        try:
            os.environ["ANTIGRAVITY_BIN"] = sys.executable
            for bad in ([1], (None,), ["ok", 2], [b"x"]):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = run_e2e.main(bad)
                self.assertEqual(rc, 2, msg=repr(bad))
                self.assertIn("no_data", buf.getvalue())
        finally:
            if saved is None:
                os.environ.pop("ANTIGRAVITY_BIN", None)
            else:
                os.environ["ANTIGRAVITY_BIN"] = saved

    def test_load_manifest_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sandbox(tmp)
            host = _fake_host(tmp)
            res = run_e2e.load_plugin(host, tmp)
            self.assertEqual(res["manifest"], "ok")
            self.assertTrue(res["ok"], msg=repr(res))
            _write(os.path.join(tmp, "plugin.json"),
                   json.dumps({"nome": "brother-antigravity", "version": "0.0.0"}))
            res2 = run_e2e.load_plugin(host, tmp)
            self.assertEqual(res2["manifest"], "fail")
            self.assertFalse(res2["ok"], msg=repr(res2))

    def test_load_mcp_config_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sandbox(tmp)
            host = _fake_host(tmp)
            res = run_e2e.load_plugin(host, tmp)
            self.assertEqual(res["mcp_config"], "ok")
            p = os.path.join(tmp, "mcp_config.json")
            with open(p, "rb") as fh:
                data = fh.read()
            with open(p, "wb") as fh:
                fh.write(data[:-1])
            res2 = run_e2e.load_plugin(host, tmp)
            self.assertNotEqual(res2["mcp_config"], "ok")
            self.assertFalse(res2["ok"], msg=repr(res2))

    def test_loads_all_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sandbox(tmp)
            host = _fake_host(tmp)
            res = run_e2e.load_plugin(host, tmp)
            self.assertGreaterEqual(res["skills_count"], 2)
            self.assertGreaterEqual(res["rules_count"], 1)
            self.assertTrue(res["ok"], msg=repr(res))
            shutil.rmtree(os.path.join(tmp, "skills", "using-brother"))
            res2 = run_e2e.load_plugin(host, tmp)
            self.assertLess(res2["skills_count"], 2)
            self.assertFalse(res2["ok"], msg=repr(res2))

    def test_load_runs_captured_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sandbox(tmp)
            marker = os.path.join(tmp, "argv.txt")
            host = _fake_host(tmp, marker=marker)
            res = run_e2e.load_plugin(host, tmp)
            self.assertTrue(res["ok"], msg=repr(res))
            self.assertEqual(res["load_argv"], [host, "--version"])
            self.assertEqual(res["load_exit_code"], 0)
            self.assertEqual(res["host_version"], "9.9.9-test")
            with open(marker) as fh:
                self.assertEqual(fh.read(), "--version\n", "the host was not run with load_argv")
            import hashlib
            self.assertEqual(res["load_stdout_sha256"], hashlib.sha256(b"9.9.9-test\n").hexdigest())

    def test_file_inspection_alone_is_not_a_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sandbox(tmp)
            for no_host in ("", None):
                res = run_e2e.load_plugin(no_host, tmp)
                self.assertEqual(res["manifest"], "ok")
                self.assertFalse(res["ok"], msg=repr(res))
                self.assertEqual(res["load_argv"], [])

    def test_ok_requires_host_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sandbox(tmp)
            for printed in ("", "Antigravity IDE\n", "no_data\n"):
                res = run_e2e.load_plugin(_fake_host(tmp, stdout=printed), tmp)
                self.assertEqual(res["host_version"], "no_data", msg=repr(printed))
                self.assertEqual(res["manifest"], "ok")
                self.assertFalse(res["ok"], msg=repr(res))

    def test_host_that_exits_nonzero_is_not_a_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sandbox(tmp)
            res = run_e2e.load_plugin(_fake_host(tmp, code=3), tmp)
            self.assertEqual(res["load_exit_code"], 3)
            self.assertFalse(res["ok"], msg=repr(res))

    def test_host_that_cannot_run_is_not_a_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sandbox(tmp)
            res = run_e2e.load_plugin(os.path.join(tmp, "absent-host"), tmp)
            self.assertEqual(res["load_exit_code"], -1)
            self.assertFalse(res["ok"], msg=repr(res))
            self.assertIn("no_data: host did not run", res["reason"])

    def test_the_installed_cli_name_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = os.path.join(tmp, "antigravity-ide")
            _write(host, "#!/bin/sh\n")
            os.chmod(host, 0o755)
            saved_bin, saved_path = os.environ.pop("ANTIGRAVITY_BIN", None), os.environ.get("PATH")
            os.environ["PATH"] = tmp
            try:
                self.assertEqual(run_e2e.resolve_host(), (host, [host]))
            finally:
                os.environ["PATH"] = saved_path
                if saved_bin is not None:
                    os.environ["ANTIGRAVITY_BIN"] = saved_bin

    def test_hostile_inputs_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_sandbox(tmp)
            for bad_host in (None, 123, [], {}, 0, False):
                res = run_e2e.load_plugin(bad_host, tmp)
                self.assertIsInstance(res, dict)
                self.assertEqual(res["manifest"], "ok")
            for bad_sandbox in (None, 123, [], {}, b"/no/such"):
                res = run_e2e.load_plugin("", bad_sandbox)
                self.assertIsInstance(res, dict)
                self.assertFalse(res["ok"], msg=repr(bad_sandbox))

        with tempfile.TemporaryDirectory() as tmp2:
            os.makedirs(os.path.join(tmp2, "plugin.json"))
            _write(os.path.join(tmp2, "mcp_config.json"),
                   json.dumps({"mcpServers": {}}))
            res = run_e2e.load_plugin("", tmp2)
            self.assertNotEqual(res["manifest"], "ok")
            self.assertFalse(res["ok"])

        with tempfile.TemporaryDirectory() as tmp3:
            _write(os.path.join(tmp3, "plugin.json"), b"\xff\xfe\x00not utf8")
            _write(os.path.join(tmp3, "mcp_config.json"),
                   json.dumps({"mcpServers": {}}))
            res = run_e2e.load_plugin("", tmp3)
            self.assertNotEqual(res["manifest"], "ok")
            self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
