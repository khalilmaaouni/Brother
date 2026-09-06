#!/usr/bin/env python3
"""provider_adapter: ONE provider-neutral core, THIN adapters.

WHY THIS EXISTS. Brother already runs Claude Code and Codex through the same
provider-neutral seams (DOOR_MODEL_CMD, MODEL_WORKER_CMD, scripts/door.py,
scripts/model_worker.py, scripts/brother_paths.py), but the FACTS that
differ between the two hosts (which binary, which hook events, what a
sandbox grant looks like, how auth is discovered) were scattered across
scripts/codex_smoke.py, scripts/codex_hooks_install.py, docs/codex/*.md and
the README, each a separate place a third host would have to be added to by
hand. This module is the one table: a fixed list of CAPABILITIES, and one
small adapter class per provider that answers each capability with either a
plain JSON-serializable fact or an explicit Refusal, never an exception and
never silence.

THE RULE THIS FILE ENFORCES ON ITSELF: the shared code (the CAPABILITIES
list, Refusal, the registry, describe(), main()) never branches on a
provider's name. `python3 scripts/provider_adapter.py describe --provider X`
looks X up in ADAPTERS and calls the same thirteen methods on whatever
adapter comes back; every fact that differs by provider lives inside that
provider's own class body. Proven by grep, not by promise: a search for the
three quoted provider names in this file must hit only inside a class body
(ClaudeAdapter, CodexAdapter, CortexAdapter) or the ADAPTERS registry line
itself (see scripts/test_provider_adapter.py, TheCoreNeverBranchesOnAName).

CORTEX is a provider this estate has NEVER run: no binary has been invoked,
no protocol has been read, nothing here is measured. Its adapter therefore
answers Refusal("cortex adapter: untested, no binary or protocol measured
on this machine") for all thirteen capabilities, always. Setting
BROTHER_CORTEX_BIN to an existing executable makes it also report what
`<bin> --version` prints, as a diagnostic aside; that print is NEVER allowed
to turn any capability's answer into anything but NO-DATA, because an
environment variable naming a binary is not a measured protocol.

Python 3.9, standard library only. No network. Every subprocess call has an
explicit failure path: a missing binary, a timeout, or a nonzero exit is
read into the return value, never raised past this module.

No em or en dashes anywhere in this file or its output.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import brother_paths  # noqa: E402
import codex_smoke  # noqa: E402

#: The fixed capability table. Every adapter answers each of these by name
#: (a same-named zero-argument method), so the shared describe() loop below
#: needs to know nothing about any one provider.
CAPABILITIES = (
    "invocation", "worker_call", "hook_events", "tool_events",
    "sandbox_grants", "auth_discovery", "capability_discovery",
    "paths", "install", "upgrade", "rollback", "uninstall", "resume",
)

#: The lifecycle commands the README and scripts/test_readme_honesty.py
#: already pin for the Claude client (DUAL_CLIENT_COMMANDS there); copied
#: here rather than imported, since a test module is not a stable API.
_CLAUDE_INSTALL_CMD = ("claude plugin marketplace add khalilmaaouni/Brother "
                      "&& claude plugin install brother@brother")
_CLAUDE_UPGRADE_CMD = "claude plugin update brother@brother"
_CLAUDE_UNINSTALL_CMD = "claude plugin uninstall brother@brother"

#: The Codex lifecycle commands, same source (test_readme_honesty.py
#: DUAL_CLIENT_COMMANDS) and scripts/release_closeout.py's own --ref usage.
_CODEX_INSTALL_CMDS = (
    "codex plugin marketplace add https://github.com/khalilmaaouni/Brother",
    "codex plugin add brother@brother --json")
_CODEX_UPGRADE_CMD = ("codex plugin marketplace remove brother && codex "
                     "plugin marketplace add "
                     "https://github.com/khalilmaaouni/Brother --ref <tag>")
_CODEX_UNINSTALL_CMDS = ("codex plugin remove brother@brother",
                        "codex plugin marketplace remove brother")


class Refusal(object):
    """An explicit "this adapter does not support, or has not measured,
    this capability" value. Every capability method on every adapter
    returns either a plain JSON-serializable dict or one of these; nothing
    here is ever an exception or a bare None standing in for "unsupported"."""

    def __init__(self, reason):
        self.reason = str(reason)

    def to_dict(self):
        return {"refusal": self.reason}

    def __eq__(self, other):
        return isinstance(other, Refusal) and other.reason == self.reason

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "Refusal(%r)" % (self.reason,)


def _run(argv, timeout=20, cwd=None, env=None):
    """(proc, "") or (None, why). Never raises: a missing binary, a bad
    timeout, or an unreadable environment is reported, not thrown."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "%s failed to run: %s" % (" ".join(argv), exc)
    return proc, ""


class ClaudeAdapter(object):
    """The Claude Code adapter: the `claude` CLI on PATH, permission modes
    for its sandbox, the plugin marketplace for its lifecycle."""

    name = "claude"

    #: Copied from scripts/model_worker.py's own CLAUDE_ARGV rather than
    #: retyped, so a change there cannot drift silently from what this file
    #: reports.
    HEADLESS_ARGV = ["claude", "-p", "--output-format", "json",
                    "--permission-mode", "acceptEdits"]

    def __init__(self, bin=None, env=None):
        self.env = os.environ if env is None else env
        self.bin = bin or shutil.which("claude")

    def _client_env(self):
        merged = dict(self.env)
        merged["BROTHER_CLIENT"] = "claude"
        return merged

    def _version(self):
        if not self.bin:
            return None, "no `claude` binary found on PATH"
        proc, err = _run([self.bin, "--version"])
        if proc is None:
            return None, err
        if proc.returncode != 0:
            return None, "claude --version exited %d: %s" % (
                proc.returncode, (proc.stderr or proc.stdout).strip())
        return (proc.stdout or proc.stderr).strip(), ""

    def invocation(self):
        if not self.bin:
            return Refusal("no `claude` binary found on PATH")
        return {"binary": self.bin, "found_on": "PATH",
                "headless_argv": self.HEADLESS_ARGV,
                "source": "scripts/model_worker.py CLAUDE_ARGV"}

    def worker_call(self):
        return {"env_vars": ["MODEL_WORKER_CMD", "DOOR_MODEL_CMD"],
                "default_argv": self.HEADLESS_ARGV,
                "source": "scripts/model_worker.py, scripts/door.py "
                         "default_model_cmd"}

    def hook_events(self):
        return {"events": ["SessionStart", "SessionEnd", "Stop",
                          "PreCompact"]}

    def tool_events(self):
        return {"events": ["PreToolUse", "PostToolUse"]}

    def sandbox_grants(self):
        return {"mechanism": "permission modes",
                "modes": ["default", "acceptEdits", "plan",
                        "bypassPermissions"],
                "note": "a permission mode gates the whole session; there "
                       "is no per-root writable-roots grant like Codex's "
                       "sandbox_workspace_write"}

    def auth_discovery(self):
        version, err = self._version()
        if version is None:
            return Refusal(err)
        return {"claude_version": version,
                "credential_presence": "NO-DATA: not probed"}

    def capability_discovery(self):
        version, err = self._version()
        if version is None:
            return Refusal(err)
        return {"claude_version": version}

    def paths(self):
        config_dir = brother_paths.config_dir(self._client_env())
        return {"config_dir": config_dir,
                "plugin_cache": os.path.join(config_dir, "plugins", "cache",
                                            "brother", "brother")}

    def install(self):
        return {"command": _CLAUDE_INSTALL_CMD}

    def upgrade(self):
        return {"command": _CLAUDE_UPGRADE_CMD}

    def rollback(self):
        return Refusal("no documented single rollback command for the "
                       "Claude adapter; not measured")

    def uninstall(self):
        return {"command": _CLAUDE_UNINSTALL_CMD}

    def resume(self):
        return {"command": "python3 scripts/brother_run.py --continue",
                "source": "scripts/brother_run.py argparse: --resume, "
                         "--continue (provider neutral)"}


class CodexAdapter(object):
    """The Codex adapter: the app-bundled binary, sandbox_workspace_write
    grants, USER-scope hooks (scripts/codex_hooks_install.py), the plugin
    marketplace for its lifecycle."""

    name = "codex"

    def __init__(self, bin=None, env=None):
        self.env = os.environ if env is None else env
        default = codex_smoke.DEFAULT_CODEX
        if bin:
            self.bin = bin
        elif os.path.isfile(default) and os.access(default, os.X_OK):
            self.bin = default
        else:
            self.bin = shutil.which("codex")

    def _client_env(self):
        merged = dict(self.env)
        merged["BROTHER_CLIENT"] = "codex"
        return merged

    def invocation(self):
        if not self.bin:
            return Refusal("no codex binary found: checked %s and PATH"
                          % codex_smoke.DEFAULT_CODEX)
        return {"binary": self.bin,
                "argv_shape": codex_smoke.documented_argv(
                    self.bin, "<toy-workspace>"),
                "source": "scripts/codex_smoke.py documented_argv, "
                         "sandbox_flags"}

    def worker_call(self):
        return {"env_vars": ["MODEL_WORKER_CMD", "DOOR_MODEL_CMD"],
                "note": "the same provider-neutral seams as Claude "
                       "(scripts/model_worker.py, scripts/door.py); a "
                       "nested `codex exec` cannot start inside a Codex "
                       "sandbox at all (scripts/codex_smoke.py "
                       "NETWORK_GRANT comment), so under Codex these seams "
                       "must be set rather than left at their defaults"}

    def hook_events(self):
        return {"events": ["SessionStart", "SessionEnd", "Stop",
                          "PreCompact"],
                "note": "SessionEnd's timeout is clamped to 3s by Codex "
                       "(measured 2026-09-04, docs/codex/HOOKS-MAPPING.md); "
                       "the others carry whatever timeoutSec "
                       "scripts/codex_hooks_install.py writes"}

    def tool_events(self):
        return {"events": ["PreToolUse", "PostToolUse"]}

    def sandbox_grants(self):
        return {"mechanism": "sandbox_workspace_write.writable_roots",
                "sandbox_mode": codex_smoke.SANDBOX_MODE,
                "example_grant": codex_smoke.GIT_GRANT % "<workspace>",
                "source": "scripts/codex_smoke.py sandbox_flags, GIT_GRANT"}

    def auth_discovery(self):
        codex_home = brother_paths.config_dir(self._client_env())
        auth_path = os.path.join(codex_home, "auth.json")
        try:
            present = os.path.exists(auth_path)
        except OSError as exc:
            return Refusal("could not check %s: %s" % (auth_path, exc))
        return {"codex_home": codex_home, "auth_json_present": present,
                "note": "presence only, never content"}

    def capability_discovery(self):
        if not self.bin:
            return Refusal("no codex binary found: checked %s and PATH"
                          % codex_smoke.DEFAULT_CODEX)
        proc, err = _run([self.bin, "features", "list"])
        if proc is None:
            return Refusal(err)
        if proc.returncode != 0:
            return Refusal("codex features list exited %d: %s" % (
                proc.returncode, (proc.stderr or proc.stdout).strip()))
        return {"features_list": (proc.stdout or "").strip()}

    def paths(self):
        config_dir = brother_paths.config_dir(self._client_env())
        return {"config_dir": config_dir,
                "plugin_cache": os.path.join(config_dir, "plugins", "cache",
                                            "brother", "brother")}

    def install(self):
        return {"commands": list(_CODEX_INSTALL_CMDS)}

    def upgrade(self):
        return {"command": _CODEX_UPGRADE_CMD}

    def rollback(self):
        return {"command": _CODEX_UPGRADE_CMD.replace("<tag>",
                                                       "<older-tag>"),
                "note": "reuses the upgrade shape's --ref pin pointed "
                       "backward; not a separate command, not measured as "
                       "a distinct rollback path"}

    def uninstall(self):
        return {"commands": list(_CODEX_UNINSTALL_CMDS)}

    def resume(self):
        return {"command": "python3 scripts/brother_run.py --continue",
                "note": "provider neutral: scripts/brother_run.py does not "
                       "branch on client"}


class CortexAdapter(object):
    """The untested provider. Every capability answers Refusal, always.
    BROTHER_CORTEX_BIN, if it names an existing executable, adds a
    diagnostic probe() report; it never changes a capability's verdict."""

    name = "cortex"

    UNTESTED = ("cortex adapter: untested, no binary or protocol measured "
               "on this machine")

    def __init__(self, bin=None, env=None):
        self.env = os.environ if env is None else env
        self.bin = bin or self.env.get("BROTHER_CORTEX_BIN") or ""

    def _refusal(self):
        return Refusal(self.UNTESTED)

    def invocation(self):
        return self._refusal()

    def worker_call(self):
        return self._refusal()

    def hook_events(self):
        return self._refusal()

    def tool_events(self):
        return self._refusal()

    def sandbox_grants(self):
        return self._refusal()

    def auth_discovery(self):
        return self._refusal()

    def capability_discovery(self):
        return self._refusal()

    def paths(self):
        return self._refusal()

    def install(self):
        return self._refusal()

    def upgrade(self):
        return self._refusal()

    def rollback(self):
        return self._refusal()

    def uninstall(self):
        return self._refusal()

    def resume(self):
        return self._refusal()

    def probe(self):
        """What `<BROTHER_CORTEX_BIN> --version` prints, when that variable
        names an existing executable file. A diagnostic aside only: it
        never turns any of the thirteen capabilities above into anything
        but NO-DATA."""
        if not self.bin:
            return {"state": "NO-DATA",
                    "detail": "BROTHER_CORTEX_BIN not set"}
        if not (os.path.isfile(self.bin) and os.access(self.bin, os.X_OK)):
            return {"state": "NO-DATA",
                    "detail": "BROTHER_CORTEX_BIN=%s is not an executable "
                             "file" % self.bin}
        proc, err = _run([self.bin, "--version"])
        if proc is None:
            return {"state": "NO-DATA", "detail": err}
        return {"state": "PRESENT", "exit_code": proc.returncode,
                "detail": (proc.stdout or proc.stderr).strip()}


#: The registry: the ONE place a provider name maps to its adapter class.
#: A grep for the quoted provider names is expected to hit here and inside
#: the three class bodies above, nowhere else in this file.
ADAPTERS = {"claude": ClaudeAdapter, "codex": CodexAdapter,
           "cortex": CortexAdapter}


def _jsonable(value):
    """A capability's answer, made safe for json.dumps: a Refusal becomes
    {"refusal": reason}, anything else passes through unchanged."""
    if isinstance(value, Refusal):
        return value.to_dict()
    return value


def describe_adapter(adapter):
    """{capability_name: answer, ...} for every name in CAPABILITIES, read
    by calling the same-named method on `adapter`. This loop never asks
    which provider `adapter` is; that is the whole point of the table."""
    return {cap: _jsonable(getattr(adapter, cap)()) for cap in CAPABILITIES}


def describe(provider, bin=None, env=None):
    """The full report for one provider: {"provider": ..., "capabilities":
    {...}}, plus a "diagnostic" key when the adapter offers one (currently
    only CortexAdapter.probe()). Raises ValueError on an unknown provider
    name; callers at the CLI boundary turn that into a printed message and
    a nonzero exit rather than a traceback reaching a user."""
    cls = ADAPTERS.get(provider)
    if cls is None:
        raise ValueError("unknown provider %r; choose one of %s" % (
            provider, ", ".join(sorted(ADAPTERS))))
    adapter = cls(bin=bin, env=env)
    out = {"provider": provider, "capabilities": describe_adapter(adapter)}
    probe = getattr(adapter, "probe", None)
    if callable(probe):
        out["diagnostic"] = probe()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("describe", help="print the capability table for "
                                        "one provider, or all")
    d.add_argument("--provider", required=True,
                  choices=sorted(ADAPTERS) + ["all"])
    d.add_argument("--bin", default=None,
                  help="override the provider binary (the test seam)")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.cmd == "describe":
        if args.provider == "all":
            payload = [describe(name, bin=args.bin)
                      for name in sorted(ADAPTERS)]
        else:
            payload = describe(args.provider, bin=args.bin)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
