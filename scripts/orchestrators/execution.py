#!/usr/bin/env python3
"""ORCH-06 concrete sibling: the execution-role orchestrator adapter.

Exists only to prove base.OrchestratorAdapter's contract is genuinely
implementable by something that could invoke a real headless model, and to
encode, as constants and comments rather than folklore, three invocation
facts this machine already paid to learn:

  1. THE BINARY IS CALLED BY ABSOLUTE PATH. The `codex` found on PATH on
     this machine is an older shim that rejects the model with an HTTP 400
     before it reads anything the caller sent: that is a client-version
     fault, not a spent attempt, and it must never be classified as one of
     this module's real failure classes. CODEX_BIN_DEFAULT names the
     app-bundled binary this estate has already verified working
     (scripts/model_worker.py, scripts/codex_smoke.py, scripts/
     codex_battery.py all default to the same path); CODEX_BIN_ENV lets a
     caller override it for a machine where the bundle lives elsewhere.

  2. PREPARED FILES, NEVER A HISTORY WALK. This adapter's only source of
     context is the capsule handed to invoke() (scripts/context_capsule.py's
     build_capsule() output: relevant_files, dependency_outputs,
     relevant_decisions, all already read and truncated by the caller). It
     never runs `git log`, `git grep`, or any other repository walk itself.
     A brief that let a model run git commands freely burned 412,796 tokens
     for no usable result; this adapter's argv never even grants the model
     a shell wide enough to try.

  3. macOS HAS NO `timeout` COMMAND. A wrapper that shells out to `timeout
     N cmd...` exits 127 immediately on this machine, which would silently
     turn every invocation into "tool_unavailable" before the model was
     ever asked anything. The deadline is enforced in Python instead, via
     the `timeout=` keyword on the injected runner (subprocess.run by
     default), exactly as scripts/model_worker.py's own C3 adapters do.

WORKSPACE-WRITE, NEVER DANGER-FULL-ACCESS. This adapter always passes
`-s workspace-write`; base.assert_safe_invocation() is called on every
argv this module builds, before it is ever handed to the runner, so a
future edit that widened the sandbox flag or added a merge/push token
would fail loudly here rather than shipping quietly.

Python 3.9 floor, standard library only, no network beyond the subprocess
call itself.
"""

import os
import subprocess

from orchestrators import base

#: The app-bundled Codex binary this estate has already verified working
#: headless (see the module docstring, point 1). Never the bare "codex"
#: name: that resolves through PATH to the older, broken shim.
CODEX_BIN_DEFAULT = "/Applications/ChatGPT.app/Contents/Resources/codex"

#: Override seam for a machine where the bundle lives elsewhere. Read only
#: at call time (codex_bin()), never cached at import time.
CODEX_BIN_ENV = "BROTHER_CODEX_BIN"

#: The only sandbox mode this adapter ever requests. Never
#: "danger-full-access": that flag drops sandboxing entirely, which is
#: exactly what base.assert_safe_invocation() exists to refuse.
SANDBOX_MODE = "workspace-write"


def codex_bin(env=None):
    """The absolute Codex binary path this call should use: CODEX_BIN_ENV
    when set, else CODEX_BIN_DEFAULT. Never the bare "codex" name."""
    env = os.environ if env is None else env
    return env.get(CODEX_BIN_ENV) or CODEX_BIN_DEFAULT


class ExecutionAdapter(base.OrchestratorAdapter):
    """Drives the app-bundled Codex binary headless, one bounded call at a
    time, through base.OrchestratorAdapter's shared retry, deadline and
    classification machinery. Never grants write access to the canonical
    repository and never invokes a merge (see build_argv, checked by
    base.assert_safe_invocation on every call)."""

    name = "execution"

    def __init__(self, requested_model=None, codex_bin_path=None,
                 runner=None, env=None):
        super(ExecutionAdapter, self).__init__(requested_model=requested_model)
        self._codex_bin = codex_bin_path or codex_bin(env)
        #: Injected so tests never spawn a real process; a real caller
        #: leaves this as subprocess.run.
        self._runner = runner or subprocess.run

    def build_argv(self, workdir):
        """The argv this adapter would run against `workdir` (a directory
        already holding the capsule's prepared files, never this
        repository's own working tree). A pure builder: it never itself
        spawns anything, so a test can inspect the exact list this
        adapter constructs with no process spawned.

        --skip-git-repo-check: workdir is a prepared scratch directory,
        not expected to be a git repository at all, and this flag is what
        keeps Codex from refusing to run there; -C names it explicitly
        rather than relying on the runner's own cwd, so the argv itself is
        a complete, self-describing record of where this call ran.
        """
        argv = [
            self._codex_bin, "exec", "--json", "--skip-git-repo-check",
            "-s", SANDBOX_MODE, "-C", workdir,
        ]
        base.assert_safe_invocation(argv)
        return argv

    def _run_attempt(self, capsule, *, budget_s, attempt_number, prior_note):
        workdir = capsule.get("workdir") or os.getcwd()
        prompt = _build_prompt(capsule, attempt_number, prior_note)
        argv = self.build_argv(workdir)
        try:
            completed = self._runner(
                argv, input=prompt, capture_output=True, text=True,
                timeout=budget_s)
        except subprocess.TimeoutExpired:
            # The deadline enforced in Python (module docstring, point 3),
            # never a shelled-out `timeout` command.
            return {"raw": "", "timed_out": True, "model_reported": None,
                    "invocation": argv}
        except OSError as exc:
            # The binary could not even be launched (missing, not
            # executable, ...): concrete evidence, not a guess, so this
            # is reported as tool_unavailable rather than left for the
            # generic empty-answer path to misclassify as "the model said
            # nothing".
            return {"raw": "", "timed_out": False, "model_reported": None,
                    "invocation": argv, "failure_class": "tool_unavailable",
                    "os_error": str(exc)}
        return {
            "raw": completed.stdout or "",
            "timed_out": False,
            "model_reported": _reported_model(completed.stdout or ""),
            "invocation": argv,
        }

    def health(self):
        """UNAVAILABLE unless the configured binary actually exists on
        disk. This adapter has no long-running process or heartbeat file
        to poll (each call is a fresh, bounded subprocess), so the binary
        existing is the only thing it can measure; it is deliberately
        never read as HEALTHY, only as "not provably UNAVAILABLE for the
        one reason this method can check"."""
        if os.path.isfile(self._codex_bin):
            return "STARTING"
        return "UNAVAILABLE"


def _build_prompt(capsule, attempt_number, prior_note):
    """The prompt text for one attempt, built only from what the capsule
    already carries (module docstring, point 2): never a repository walk,
    never a chat transcript. attempt_number and prior_note are folded in
    so a retry's prompt is provably different from the first attempt's,
    which is part of what base.check_retry_allowed's signature comparison
    relies on for the "never identically" rule when a concrete adapter's
    own budget alone would not have differed enough to notice."""
    lines = [
        "unit_id: %s" % capsule.get("unit_id", ""),
        "objective: %s" % capsule.get("objective", ""),
        "contract_fragment: %s" % capsule.get("contract_fragment", ""),
        "attempt: %d" % attempt_number,
    ]
    if prior_note:
        lines.append("prior_note: %s" % prior_note)
    lines.append(
        "Answer with exactly one JSON object matching "
        "docs/schema/orchestrator-event-v1.json. No prose, no markdown "
        "fences, nothing before or after the object.")
    return "\n".join(lines)


def _reported_model(stdout):
    """The model identity a `codex exec --json` JSONL stream reports for
    this turn, or None when no line in it says. Never raises on
    unparsable input: a stdout that is not JSONL at all is exactly the
    "prose" case base._classify already turns into a failure, and this
    function's job is limited to extracting an identity when one is
    genuinely present, not to judging the stream's validity."""
    import json
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            model = event.get("model") or event.get("model_reported")
            if isinstance(model, str) and model:
                return model
    return None
