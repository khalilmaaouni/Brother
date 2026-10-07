"""The bridge adapter (FX-31.2): the OpenRouter bridge's command line, verdict and cost behind one object.

  argv   model_call's bridge command line, unchanged: this interpreter, the bridge program, the registry name as
         --model (the bridge resolves its own aliases), --effort at the owner's xhigh floor, --max at the model's
         ceiling, --timeout as the whole call's wall, then `--` and the prompt. The `--` is the prompt boundary: a
         prompt that starts with a dash ("--json", "--settle=gen-x", "-h") is the user's words, never a bridge flag.
  judge  the contract's one judge (adapters.judge): a non zero exit, an EMPTY stdout at exit zero and a provider
         refusal all fail, never an answer; the verdict carries the call's measured cost.
  cost   the bridge's own [billed] line on stderr: known=yes is OpenRouter's charge (a measured zero stays 0.0),
         known=no is a floor and so NOT_MEASURED, no line is NOT_MEASURED.

THE BRIDGE PROGRAM IS THE CALLER'S TO NAME. scripts/test_bridge_spawners.py keeps every runtime file that names the
bridge behind the router's transport allowlist, and model_call (its BRIDGE, gated at assert_may_send) is that file, so
it hands the path in. An adapter given no program refuses; it never guesses one. Nothing here starts a process.
"""
import os
import re
import sys
from collections.abc import Mapping

import adapters as A

#: model_call.BRIDGE_MAX, the fan out's MODEL_MAX_TOKENS: the model's full ceiling, billed only for what is generated.
BRIDGE_MAX = {"deepseek": 384000, "muse": 943718, "jev": 32000}
DEFAULT_MAX = 64000
#: The owner's floor for the bridge (2026-09-21), and the highest effort the bridge's own --effort accepts: "max",
#: which model_call.bridge_effort lets through, would only make the bridge exit 2 after the call was reserved.
EFFORT_FLOOR = "xhigh"
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")
BILLED = re.compile(r"\[billed\] usd=(\S+) attempts=([0-9]+) known=(yes|no)")
USD = re.compile(r"[0-9]+\.[0-9]+")


class BridgeAdapter(object):
    """The bridge transport's argv, judge and cost.

    program  the bridge script's absolute path, handed in by the gated caller (model_call.BRIDGE).
    env      where BROTHER_BRIDGE_EFFORT is read, at call time (default os.environ), so a refusal is live.
    """

    transport = "bridge"

    def __init__(self, program=None, env=None):
        if env is not None and not isinstance(env, Mapping):
            raise ValueError("env must be a mapping or None, not %s" % type(env).__name__)
        self.program, self.env = program, env

    def argv(self, model, prompt, timeout, row):
        """(argv, stdin, None), the shape model_call._argv returns. Refused before any process starts for a row of
        another transport, a model that is not a plain name, an empty prompt or one holding a NUL, a timeout that is
        not a whole number of seconds of at least 1 (a bool is not one), an effort other than the floor, or no
        program."""
        if not isinstance(row, dict) or row.get("transport") != self.transport:
            raise A.Refused("the bridge adapter builds only a bridge row's command line, not %.120r" % (row,))
        if not isinstance(model, str) or not MODEL_NAME.fullmatch(model):
            raise A.Refused("model must be a plain registry name, not %.120r" % (model,))
        if not isinstance(prompt, str) or not prompt.strip() or "\0" in prompt:
            raise A.Refused("the prompt must be non-empty text without a NUL byte")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise A.Refused("timeout must be a whole number of seconds, at least 1, not %.60r" % (timeout,))
        program = self.program
        if not isinstance(program, str) or "\0" in program or not os.path.isabs(program) or not os.path.isfile(program):
            raise A.Refused("no bridge program: the gated caller names it by absolute path and this adapter never "
                            "guesses one (%.120r)" % (program,))
        if not sys.executable:
            raise A.Refused("no interpreter to run the bridge with")
        argv = [sys.executable, program, "--model", model, "--effort", self._effort(),
                "--max", str(BRIDGE_MAX.get(model, DEFAULT_MAX)), "--timeout", str(timeout)]
        return argv + ["--", prompt], "", None

    def _effort(self):
        env = os.environ if self.env is None else self.env
        raw = env.get("BROTHER_BRIDGE_EFFORT") or EFFORT_FLOOR
        effort = raw.strip().lower() if isinstance(raw, str) else None
        if effort != EFFORT_FLOOR:
            raise A.Refused("BROTHER_BRIDGE_EFFORT=%.40r: the bridge runs at %s, its floor and the highest effort it "
                            "accepts" % (raw, EFFORT_FLOOR))
        return effort

    def judge(self, model, row, result):
        """The contract's AdapterResult for one finished bridge run, with cost_usd from its [billed] line. A row of
        another transport, a result that is not a mapping and a returncode that is not an int (False is not exit 0)
        are refused, so no other transport's output is ever judged by the bridge's rules."""
        if not (isinstance(row, dict) and row.get("transport") == self.transport):
            raise ValueError("the bridge adapter judges only a bridge row's result, not %.120r" % (row,))
        if not isinstance(result, dict):
            raise ValueError("a transport result must be a mapping, not %s" % type(result).__name__)
        code = result.get("returncode")
        if isinstance(code, bool) or not isinstance(code, int):
            raise ValueError("a bridge result's returncode must be an int, not %.40r" % (code,))
        verdict = A.judge(self, model, row, result)
        verdict.cost_usd = self.cost(result)
        return verdict

    def cost(self, result, usage=None):
        """OpenRouter's own charge from the bridge's one [billed] line on stderr, or NOT_MEASURED. The answer on
        stdout is never read for it. Two lines, a line that does not parse, or a charge with no attempt behind it
        cannot all be true: each is refused, never read as a cost."""
        if not isinstance(result, dict):
            raise ValueError("a transport result must be a mapping, not %s" % type(result).__name__)
        err = result.get("stderr")
        if err is not None and not isinstance(err, str):
            raise ValueError("a bridge result's stderr must be text or None, not %s" % type(err).__name__)
        lines = [line.strip() for line in (err or "").splitlines() if line.strip().startswith("[billed]")]
        if not lines:
            return A.normalize_cost(None, usage)
        if len(lines) > 1:
            raise ValueError("%d [billed] lines in one bridge result; one call prints one" % len(lines))
        billed = BILLED.fullmatch(lines[0])
        if billed is None:
            raise ValueError("the [billed] line does not parse: %.120r" % lines[0])
        if billed.group(3) == "no":
            return A.normalize_cost(None, usage)   # a floor, never the charge
        if not USD.fullmatch(billed.group(1)):
            raise ValueError("a known charge must be a plain amount, not %.40r" % billed.group(1))
        usd = float(billed.group(1))
        if usd > 0 and int(billed.group(2)) == 0:
            raise ValueError("a charge of %s USD with no attempt behind it" % billed.group(1))
        return A.normalize_cost(usd, usage)
