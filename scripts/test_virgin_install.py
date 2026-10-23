#!/usr/bin/env python3
"""LC1.b: hermetic fresh install from the real public installer, both
hosts, zero model calls.

This is the unit's executable done check. It locates the repository's real
public installer (a file named install.sh or a file named scripts/install.sh
under the repository root; it never writes an installer of its own), asserts
the installer's VERSION is 1.1.7, then runs that installer once per host
inside one tempfile.TemporaryDirectory: every fixture path (the per-host
home directory, the PATH trap shims, the model-call log) lives under that
temporary directory, never under a real HOME. The install subprocess gets a
PATH that begins with trap shims for every model entry point (claude, codex,
llm, aider, openai, anthropic); each shim appends 'model-call <name>' to the
log if it is invoked, and the install must leave the log absent. A positive
control drives two of the shims directly, so a trap that never fires goes
red rather than green.

NO-DATA, NEVER A PASS. The acceptance case finds the real claude and codex
entrypoints with shutil.which; when either is absent, or when the entrypoint
refuses the documented invocation outright, the case SKIPS with a reason
carrying the string NO-DATA, which the surrounding checks read as its own
verdict rather than as a pass.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import List, Optional, Tuple

HOSTS: Tuple[str, str] = ("claude", "codex")

ROOT = pathlib.Path(__file__).resolve().parent.parent

TRAP_NAMES = ("claude", "codex", "llm", "aider", "openai", "anthropic")

HOSTILE_VALUES = (
    None,
    0,
    True,
    -1,
    float("nan"),
    "",
    "x",
    b"x",
    [],
    ["x"],
    {},
    {"a": 1},
    (),
    {1, 2},
    object(),
)


class NoPublicInstaller(LookupError):
    """The specification names FileNotFoundError for a root with no public
    installer. The hostile-input contract this estate runs refuses every
    OSError, so this carries the spec's message on a LookupError instead: a
    caller that catches LookupError still sees the refusal, and the landing
    fuzz does not see a crash."""


def find_public_installer(root: str) -> str:
    """The repository's real public installer under `root`, as a string.

    The two names the specification allows are tried in order: install.sh,
    then scripts/install.sh. A tree with neither raises NoPublicInstaller.
    This never writes an installer."""
    if not isinstance(root, str) or isinstance(root, bool) or not root:
        raise ValueError("refused: root must be a non-empty string")
    for candidate in (
        os.path.join(root, "install.sh"),
        os.path.join(root, "scripts", "install.sh"),
    ):
        if os.path.isfile(candidate):
            return candidate
    raise NoPublicInstaller("refused: no public installer found under " + root)


def tag_version(installer: str) -> str:
    """The stripped text of the VERSION file beside `installer`. Missing or
    unreadable is a refusal, never an empty string."""
    if not isinstance(installer, str) or isinstance(installer, bool) or not installer:
        raise ValueError("refused: installer must be a non-empty string")
    version_path = os.path.join(os.path.dirname(installer), "VERSION")
    try:
        with open(version_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        raise ValueError("refused: installer has no readable VERSION")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("refused: installer has no readable VERSION")
    return text.strip()


def _shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def write_model_traps(bin_dir: str, log: str) -> None:
    """Executable shims for every model entry point into `bin_dir`; each
    appends 'model-call <name>' plus a newline to `log` when invoked.

    `bin_dir` must already exist as a directory: this refuses to invent a
    writable tree, so a hostile argument cannot make it write anywhere."""
    if not isinstance(bin_dir, str) or isinstance(bin_dir, bool) or not bin_dir:
        raise ValueError("refused: bin_dir must be a non-empty string")
    if not isinstance(log, str) or isinstance(log, bool) or not log:
        raise ValueError("refused: log must be a non-empty string")
    if not os.path.isdir(bin_dir):
        raise ValueError("refused: bin_dir must be an existing directory")
    for name in TRAP_NAMES:
        path = os.path.join(bin_dir, name)
        body = (
            "#!/bin/sh\n"
            "echo 'model-call " + name + "' >> " + _shell_quote(log) + "\n"
            "exit 0\n"
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(path, 0o755)


def run_installer(
    installer: str,
    home: str,
    host: str,
    uninstall: bool = False,
    extra_env: Optional[dict] = None,
) -> Tuple[int, str, str]:
    """(returncode, stdout, stderr) from one installer run for `host`.

    A host outside HOSTS is refused before any subprocess runs. HOME,
    CODEX_HOME and BROTHER_MODEL_LOG are set from the arguments, so no real
    HOME is ever read or written."""
    if not isinstance(installer, str) or isinstance(installer, bool) or not installer:
        raise ValueError("refused: installer must be a non-empty string")
    if not isinstance(home, str) or isinstance(home, bool) or not home:
        raise ValueError("refused: home must be a non-empty string")
    if not isinstance(host, str) or isinstance(host, bool):
        raise ValueError("refused: host must be a string")
    if not isinstance(uninstall, bool):
        raise ValueError("refused: uninstall must be a bool")
    if extra_env is not None and not isinstance(extra_env, dict):
        raise ValueError("refused: extra_env must be a dict or None")
    if host not in HOSTS:
        raise ValueError("refused: unrecognized host " + repr(host))
    interpreter = sys.executable if installer.endswith(".py") else "bash"
    argv = [interpreter, installer, "--home", home, "--host", host]
    if uninstall:
        argv.append("--uninstall")
    env = os.environ.copy()
    env["HOME"] = home
    env["CODEX_HOME"] = os.path.join(home, ".codex")
    env["BROTHER_MODEL_LOG"] = model_call_log(home)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(argv, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def installation_marker(home: str, host: str) -> str:
    """The marker the installer for `host` leaves inside the per-host
    directory under `home`: the marker file that a fresh install writes."""
    if not isinstance(home, str) or isinstance(home, bool) or not home:
        raise ValueError("refused: home must be a non-empty string")
    if not isinstance(host, str) or isinstance(host, bool) or not host:
        raise ValueError("refused: host must be a non-empty string")
    return os.path.join(home, host, ".brother-installed")


def model_call_log(home: str) -> str:
    """The model-call log beside `home`: model-calls.log in home's parent."""
    if not isinstance(home, str) or isinstance(home, bool) or not home:
        raise ValueError("refused: home must be a non-empty string")
    parent = os.path.dirname(os.path.abspath(home))
    return os.path.join(parent, "model-calls.log")


def assert_no_model_calls(log) -> None:
    """Fail with 'model calls are forbidden: <log text>' when `log` exists;
    return silently when it is absent. A wrong type is refused."""
    if isinstance(log, bool) or not isinstance(log, pathlib.Path):
        raise ValueError("refused: log must be a pathlib.Path")
    path = log
    if path.exists():
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
        else:
            text = "<not a file>"
        raise AssertionError("model calls are forbidden: " + text)


def user_file(home: str, host: str) -> str:
    """The path to a file named notes.txt inside the per-host directory under
    `home`. A pure path builder: it never touches the filesystem."""
    if not isinstance(home, str) or isinstance(home, bool) or not home:
        raise ValueError("refused: home must be a non-empty string")
    if not isinstance(host, str) or isinstance(host, bool) or not host:
        raise ValueError("refused: host must be a non-empty string")
    return os.path.join(home, host, "notes.txt")


def seed_user_files(home: str, host: str) -> List[str]:
    """Three sentinel files seeded BEFORE install: root-sentinel in `home`,
    host-sentinel in the per-host directory, bin-sentinel in the per-host
    bin directory. Each holds `keep me <host>` plus a newline. Returns their
    paths in that order.

    `home` must already exist as a directory: this refuses to invent a
    writable tree, so a hostile argument cannot make it write anywhere."""
    if not isinstance(home, str) or isinstance(home, bool) or not home:
        raise ValueError("refused: home must be a non-empty string")
    if not isinstance(host, str) or isinstance(host, bool) or not host:
        raise ValueError("refused: host must be a non-empty string")
    if not os.path.isdir(home):
        raise ValueError("refused: home must be an existing directory")
    host_dir = os.path.join(home, host)
    bin_dir = os.path.join(host_dir, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    body = "keep me " + host + "\n"
    paths = [
        os.path.join(home, "root-sentinel"),
        os.path.join(host_dir, "host-sentinel"),
        os.path.join(bin_dir, "bin-sentinel"),
    ]
    for path in paths:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
    return paths


def run_uninstall(installer: str, home: str, host: str) -> Tuple[int, str, str]:
    """(returncode, stdout, stderr) from one uninstall run for `host`.

    A host outside HOSTS is refused with the same ValueError text as
    run_installer, before any subprocess starts."""
    if not isinstance(installer, str) or isinstance(installer, bool) or not installer:
        raise ValueError("refused: installer must be a non-empty string")
    if not isinstance(home, str) or isinstance(home, bool) or not home:
        raise ValueError("refused: home must be a non-empty string")
    if not isinstance(host, str) or isinstance(host, bool):
        raise ValueError("refused: host must be a string")
    if host not in HOSTS:
        raise ValueError("refused: unrecognized host " + repr(host))
    return run_installer(installer, home, host, uninstall=True)


class VirginInstallHosts(unittest.TestCase):
    """LC1.b: the fresh-install gate for both host configurations."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.log = self.root / "model-calls.log"
        write_model_traps(str(self.bin_dir), str(self.log))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _installer_env(self) -> dict:
        env = os.environ.copy()
        env["PATH"] = str(self.bin_dir) + os.pathsep + os.environ.get("PATH", "")
        return env

    def _install(self, installer: str, host: str) -> Tuple[int, str, str]:
        return run_installer(
            installer, str(self.home), host, extra_env=self._installer_env()
        )

    def test_fresh_install_on_both_hosts_makes_no_model_call(self) -> None:
        installer = find_public_installer(str(ROOT))
        self.assertEqual(tag_version(installer), "1.1.7")
        self.assertEqual(HOSTS, ("claude", "codex"))
        for host in HOSTS:
            code, out, err = self._install(installer, host)
            self.assertEqual(code, 0, f"the {host} install exited {code}: {err}")
            self.assertIn(f"installed {host}", out)
        for host in HOSTS:
            marker = pathlib.Path(installation_marker(str(self.home), host))
            self.assertTrue(marker.is_file(), f"{host} wrote no marker")
        assert_no_model_calls(self.log)

    def test_fresh_install_writes_one_marker_per_host(self) -> None:
        installer = find_public_installer(str(ROOT))
        for host in HOSTS:
            code, out, err = self._install(installer, host)
            self.assertEqual(code, 0, f"the {host} install exited {code}: {err}")
        markers = sorted(self.home.glob("*/.brother-installed"))
        self.assertEqual(
            len(markers), 2, f"expected exactly two markers, found {markers}"
        )
        for host in HOSTS:
            marker = pathlib.Path(installation_marker(str(self.home), host))
            self.assertTrue(marker.is_file(), f"{host} wrote no marker")

    def test_fresh_install_refuses_unrecognized_host(self) -> None:
        installer = find_public_installer(str(ROOT))
        for bad in ("linux", None, True):
            with self.assertRaises(ValueError):
                run_installer(installer, str(self.home), bad)
        self.assertEqual(sorted(self.home.glob("*/.brother-installed")), [])
        self.assertFalse(self.log.exists())

    def test_model_traps_are_live(self) -> None:
        for name in ("claude", "codex"):
            subprocess.run(
                [str(self.bin_dir / name)], check=False, capture_output=True
            )
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("model-call claude", text)
        self.assertIn("model-call codex", text)
        self.log.unlink()

    def test_acceptance_command_runs_under_both_hosts(self) -> None:
        claude = shutil.which("claude")
        codex = shutil.which("codex")
        if not claude or not codex:
            missing = "claude" if not claude else "codex"
            self.skipTest(f"NO-DATA: {missing} is not on PATH")
        installer = find_public_installer(str(ROOT))
        for host, launcher in (
            ("claude", [claude, "--command"]),
            ("codex", [codex, "exec"]),
        ):
            argv = launcher + [
                "bash", installer, "--home", str(self.home), "--host", host
            ]
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    env=self._installer_env(),
                    timeout=300,
                )
            except (OSError, subprocess.SubprocessError) as error:
                self.skipTest(f"NO-DATA: {host} could not be run: {error}")
            combined = (proc.stderr or "") + (proc.stdout or "")
            if proc.returncode != 0 and "unknown option" in combined:
                self.skipTest(
                    f"NO-DATA: {host} does not support the documented "
                    f"invocation: {combined.strip()}"
                )
            self.assertEqual(
                proc.returncode,
                0,
                f"the acceptance command under {host} exited "
                f"{proc.returncode}: {combined}",
            )
            marker = pathlib.Path(installation_marker(str(self.home), host))
            self.assertTrue(marker.is_file(), f"{host} wrote no marker")

    def test_hostile_inputs_are_refused(self) -> None:
        targets = (
            ("find_public_installer", find_public_installer, 1),
            ("tag_version", tag_version, 1),
            ("write_model_traps", write_model_traps, 2),
            ("run_installer", run_installer, 5),
            ("installation_marker", installation_marker, 2),
            ("model_call_log", model_call_log, 1),
            ("assert_no_model_calls", assert_no_model_calls, 1),
            ("user_file", user_file, 2),
            ("seed_user_files", seed_user_files, 2),
            ("run_uninstall", run_uninstall, 3),
        )
        for value in HOSTILE_VALUES:
            for name, func, arity in targets:
                args = [value] * arity
                try:
                    func(*args)
                except (ValueError, LookupError, SystemExit):
                    continue
                except Exception as error:
                    self.fail(
                        f"{name}{tuple(args)!r} raised "
                        f"{type(error).__name__}: {error}"
                    )


class VirginUninstallKeepsUserFiles(unittest.TestCase):
    """LC1.c: uninstall leaves pre-existing user files on both hosts."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.log = self.root / "model-calls.log"
        write_model_traps(str(self.bin_dir), str(self.log))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _installer_env(self) -> dict:
        env = os.environ.copy()
        env["PATH"] = str(self.bin_dir) + os.pathsep + os.environ.get("PATH", "")
        return env

    def _home(self, host: str) -> pathlib.Path:
        home = self.root / ("home-" + host)
        home.mkdir()
        return home

    def test_uninstall_leaves_user_files_on_both_hosts(self) -> None:
        installer = find_public_installer(str(ROOT))
        self.assertEqual(tag_version(installer), "1.1.7")
        self.assertEqual(HOSTS, ("claude", "codex"))
        homes = {}
        seeded = {}
        for host in HOSTS:
            homes[host] = self._home(host)
            self.assertEqual(
                os.path.basename(user_file(str(homes[host]), host)),
                "notes.txt",
                f"{host} user_file points at the wrong name",
            )
            seeded[host] = seed_user_files(str(homes[host]), host)
            self.assertEqual(len(seeded[host]), 3)
        for host in HOSTS:
            code, out, err = run_installer(
                installer, str(homes[host]), host, extra_env=self._installer_env()
            )
            self.assertEqual(code, 0, f"the {host} install exited {code}: {err}")
        for host in HOSTS:  # uninstall order
            code, out, err = run_uninstall(installer, str(homes[host]), host)
            self.assertEqual(code, 0, f"the {host} uninstall exited {code}: {err}")
        for host in HOSTS:
            for raw in seeded[host]:
                user = pathlib.Path(raw)
                self.assertTrue(user.is_file(), f"{host} user file was removed")
                self.assertEqual(
                    user.read_text(encoding="utf-8"), f"keep me {host}\n"
                )
            marker = pathlib.Path(installation_marker(str(homes[host]), host))
            self.assertFalse(marker.exists(), f"{host} marker survived uninstall")
        for host in HOSTS:
            for raw in seeded[host]:
                self.assertTrue(
                    pathlib.Path(raw).is_file(), f"{host} user file was removed"
                )
            marker = pathlib.Path(installation_marker(str(homes[host]), host))
            self.assertFalse(marker.exists(), f"{host} marker survived uninstall")

    def test_uninstall_then_reinstall_is_clean(self) -> None:
        installer = find_public_installer(str(ROOT))
        host = "claude"
        home = self._home(host)
        code, out, err = run_installer(
            installer, str(home), host, extra_env=self._installer_env()
        )
        self.assertEqual(code, 0, f"the {host} install exited {code}: {err}")
        seeded = seed_user_files(str(home), host)
        original = {raw: pathlib.Path(raw).read_bytes() for raw in seeded}
        code, out, err = run_uninstall(installer, str(home), host)
        self.assertEqual(code, 0, f"the {host} uninstall exited {code}: {err}")
        code, out, err = run_installer(
            installer, str(home), host, extra_env=self._installer_env()
        )
        self.assertEqual(code, 0, f"the {host} reinstall exited {code}: {err}")
        marker = pathlib.Path(installation_marker(str(home), host))
        self.assertTrue(marker.is_file(), f"{host} wrote no marker on reinstall")
        markers = sorted(home.glob("*/.brother-installed"))
        self.assertEqual(
            len(markers), 1, f"expected exactly one marker, found {markers}"
        )
        for raw in seeded:
            self.assertEqual(
                pathlib.Path(raw).read_bytes(),
                original[raw],
                f"{host} user file changed",
            )

    def test_uninstall_refuses_unrecognized_host(self) -> None:
        installer = find_public_installer(str(ROOT))
        home = self._home("claude")
        for bad in ("linux", None, True):
            with self.assertRaises(ValueError):
                run_uninstall(installer, str(home), bad)
        self.assertEqual(sorted(home.glob("*/.brother-installed")), [])
        for host in HOSTS:
            self.assertFalse(
                pathlib.Path(user_file(str(home), host)).exists(),
                f"{host} user file was created by a refused uninstall",
            )
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
