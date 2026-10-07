"""L1b.1 sandbox provisioning tests. Standard library only."""

import importlib.util
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SANDBOX = os.path.join(HERE, "sandbox.sh")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SANDBOX_ID_PREFIX = "brother-e2e-antigravity."


def _run_sandbox(args, env=None):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        ["sh", SANDBOX] + list(args),
        capture_output=True,
        text=True,
        env=merged,
    )


def _check_traversal(value, label):
    for part in value.split("/"):
        if part in (".", ".."):
            raise ValueError("%s contains a path traversal component: %r" % (label, part))


def _refuse_source_shape(value):
    if not isinstance(value, str):
        raise ValueError("source_plugin must be a str")
    if value == "":
        raise ValueError("source_plugin must not be empty")
    if "\x00" in value:
        raise ValueError("source_plugin contains NUL")
    if value == "/":
        raise ValueError("source_plugin must not be the filesystem root")
    _check_traversal(value, "source_plugin")
    for part in value.split("/"):
        if part.startswith(SANDBOX_ID_PREFIX):
            raise ValueError("source_plugin looks like a sandbox id, not a source plugin: %r" % part)
    return value


def _refuse_cleanup_shape(value):
    if not isinstance(value, str):
        raise ValueError("path must be a str")
    if value == "":
        raise ValueError("path must not be empty")
    if "\x00" in value:
        raise ValueError("path contains NUL")
    if value == "/":
        raise ValueError("path must not be the filesystem root")
    _check_traversal(value, "path")
    return value


def _refuse_target_env(value):
    if value == "":
        return value
    _check_traversal(value, "BROTHER_E2E_SANDBOX_TARGET")
    return value


def sandbox_create(source_plugin, allow_repo=False):
    _refuse_source_shape(source_plugin)
    if not isinstance(allow_repo, bool):
        raise ValueError("allow_repo must be a bool")
    target_env = os.environ.get("BROTHER_E2E_SANDBOX_TARGET")
    if target_env is not None:
        _refuse_target_env(target_env)
    args = ["create", source_plugin]
    env = {}
    if allow_repo:
        env["BROTHER_E2E_ALLOW_REPO"] = "1"
        args.append("--allow-repo")
    result = _run_sandbox(args, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            "sandbox_create failed: %s"
            % (result.stderr.strip() or result.stdout.strip() or "exit %d" % result.returncode)
        )
    path = result.stdout.strip()
    if not path:
        raise RuntimeError("sandbox_create produced no path")
    if not os.path.isabs(path):
        raise RuntimeError("sandbox_create produced a non absolute path: %r" % path)
    return path


def sandbox_cleanup(path):
    if not isinstance(path, str):
        raise ValueError("path must be a str")
    _refuse_cleanup_shape(path)
    result = _run_sandbox(["cleanup", path])
    if result.returncode != 0:
        raise RuntimeError(
            "sandbox_cleanup failed: %s"
            % (result.stderr.strip() or result.stdout.strip() or "exit %d" % result.returncode)
        )


def _write_source_plugin(root):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "plugin.json"), "w", encoding="utf-8") as handle:
        handle.write('{"name": "sandbox-fixture"}')
    with open(os.path.join(root, "mcp_config.json"), "w", encoding="utf-8") as handle:
        handle.write('{"mcpServers": {}}')
    os.makedirs(os.path.join(root, "skills", "using-brother"), exist_ok=True)
    with open(os.path.join(root, "skills", "using-brother", "SKILL.md"), "w", encoding="utf-8") as handle:
        handle.write("# skill\n")
    os.makedirs(os.path.join(root, "rules"), exist_ok=True)
    with open(os.path.join(root, "rules", "rule.md"), "w", encoding="utf-8") as handle:
        handle.write("# rule\n")


class _Env(object):
    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


class TestSandboxProvisioning(unittest.TestCase):
    def test_create_outside_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            source = os.path.join(tmp, "source_plugin")
            os.makedirs(repo)
            _write_source_plugin(source)
            with _Env(
                BROTHER_E2E_REPO_ROOT=repo,
                BROTHER_E2E_SANDBOX_TARGET=None,
                BROTHER_E2E_ALLOW_REPO=None,
            ):
                path = sandbox_create(source)
                self.assertTrue(os.path.isabs(path))
                self.assertFalse(path == repo or path.startswith(repo + os.sep))
                self.assertTrue(os.path.isdir(os.path.join(path, "plugin")))
                self.assertTrue(os.path.isfile(os.path.join(path, ".brother_e2e_sandbox")))
                sandbox_cleanup(path)
                self.assertFalse(os.path.exists(path))

    def test_refuses_repo_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            source = os.path.join(tmp, "source_plugin")
            os.makedirs(repo)
            _write_source_plugin(source)
            target = os.path.join(repo, "sandbox")
            with _Env(
                BROTHER_E2E_REPO_ROOT=repo,
                BROTHER_E2E_SANDBOX_TARGET=target,
                BROTHER_E2E_ALLOW_REPO=None,
            ):
                with self.assertRaises(RuntimeError):
                    sandbox_create(source, allow_repo=False)

    def test_create_path_dotdot_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            source = os.path.join(tmp, "source_plugin")
            os.makedirs(repo)
            os.makedirs(os.path.join(repo, "sub"))
            _write_source_plugin(source)
            target = os.path.join(repo, "sandbox")
            repo_with_dotdot = os.path.join(repo, "sub", "..")
            with _Env(
                BROTHER_E2E_REPO_ROOT=repo_with_dotdot,
                BROTHER_E2E_SANDBOX_TARGET=target,
                BROTHER_E2E_ALLOW_REPO=None,
            ):
                with self.assertRaises(RuntimeError):
                    sandbox_create(source)
                self.assertFalse(os.path.isdir(os.path.join(repo, "sandbox")))

    def test_source_plugin_path_shaped_id_refused(self):
        cases = [
            SANDBOX_ID_PREFIX + "CZwrW6",
            "/tmp/" + SANDBOX_ID_PREFIX + "abc123",
            ".",
            "..",
            "/",
            "foo/../bar",
            "foo/./bar",
        ]
        for bad in cases:
            with self.assertRaises(ValueError, msg="expected refusal for %r" % bad):
                sandbox_create(bad)

    def test_missing_source_plugin_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing_plugin")
            with self.assertRaises(RuntimeError):
                sandbox_create(missing)

    def test_non_directory_source_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_file = os.path.join(tmp, "plugin.json")
            with open(source_file, "w", encoding="utf-8") as handle:
                handle.write("{}")
            with self.assertRaises(RuntimeError):
                sandbox_create(source_file)

    def test_cleanup_outside_scratch_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = os.path.join(tmp, "outside")
            os.makedirs(outside)
            with self.assertRaises(RuntimeError):
                sandbox_cleanup(outside)
            self.assertTrue(os.path.isdir(outside))

    def test_hostile_input_refused(self):
        bad_values = [None, 123, 1.5, float("nan"), [], {}, b"bytes", b"\xff\xfe"]
        for bad in bad_values:
            with self.assertRaises(ValueError):
                sandbox_create(bad)
            with self.assertRaises(ValueError):
                sandbox_cleanup(bad)
        with self.assertRaises(ValueError):
            sandbox_create("/tmp/does-not-matter", allow_repo="yes")
        with self.assertRaises(ValueError):
            sandbox_create("/tmp/does-not-matter", allow_repo=1)

    def test_client_parity_tables_unchanged(self):
        parity_path = os.path.join(REPO_ROOT, "scripts", "client_parity.py")
        spec = importlib.util.spec_from_file_location("client_parity_l1b1_probe", parity_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        pairs_before = dict(module.PAIRS)
        anti_before = dict(module.ANTIGRAVITY_PAIRS)
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "source_plugin")
            _write_source_plugin(source)
            with _Env(
                BROTHER_E2E_REPO_ROOT=tmp,
                BROTHER_E2E_SANDBOX_TARGET=None,
                BROTHER_E2E_ALLOW_REPO=None,
            ):
                path = sandbox_create(source)
                try:
                    self.assertEqual(module.PAIRS, pairs_before)
                    self.assertEqual(module.ANTIGRAVITY_PAIRS, anti_before)
                finally:
                    sandbox_cleanup(path)
