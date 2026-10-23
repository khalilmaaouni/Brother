"""The Claude adapter (FX-31.3): the headless first party call's command line, verdict and cost behind one object.

  argv   model_call's claude command line, unchanged: the program, -p, the row's id as --model, CLAUDE_TRIM (no
         inherited settings, slash commands, MCP servers or dynamic prompt sections), CLAUDE_IO (the JSON result record
         and no session persistence), --tools "" (no tool, ever) and --effort at the model's floor or above. The prompt
         goes on STDIN, never on argv, so a prompt that starts with a dash is never read as a flag.
  judge  the contract's one judge (adapters.judge) over the JSON result record, read with the ledger's own parser, which
         refuses a repeated member. A record whose stop_reason is "refusal" is PROVIDER_REFUSED, never an answer; an
         error record, an empty result, a non zero exit and output that is not the record all fail. The verdict carries
         the call's measured cost.
  cost   the record's own total_cost_usd, whatever the exit (a failed call can still have been billed): a priced zero
         stays 0.0; an absent, invalid or unreadable figure, or no record at all, is NOT_MEASURED, never 0.0.

THE PROGRAM IS THE CALLER'S TO NAME. model_call resolves the installed CLI (its CLAUDE override, else
model_router.claude_bin) and hands the path in. An adapter given no runnable program refuses; it never guesses one.
Nothing here starts a process.
"""
import os
from collections.abc import Mapping

import adapters as A
import claude_ledger as CL
from adapters.bridge import MODEL_NAME

#: model_call.CLAUDE_TRIM. NO INHERITED PREFIX (measured 2026-09-23): the default child loaded about 235 thousand tokens of
#: settings, hooks, skill lists and MCP tool definitions, 0.99 USD before any work; trimmed, the same answer cost 0.0086.
CLAUDE_TRIM = ("--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config",
               "--exclude-dynamic-system-prompt-sections")
#: model_call.CLAUDE_IO: the JSON result record carries the answer, usage and cost, so no transcript is written per call.
CLAUDE_IO = ("--output-format", "json", "--no-session-persistence")
#: NO TOOLS, EVER (measured 2026-09-22: a headless checker edited two tracked files). "" is the CLI's spelling for none.
NO_TOOLS = ("--tools", "")
#: model_call.CLAUDE_EFFORTS and FLOOR_BY_MODEL: the levels in order, and each model family's floor (owner 2026-09-22 and
#: 2026-09-23). A model no family names runs at DEFAULT_FLOOR.
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
FLOOR_BY_MODEL = {"opus": "medium", "sonnet": "medium", "fable": "high", "haiku": "medium"}
DEFAULT_FLOOR = "medium"
NOT_THE_RECORD = "the first party output is not the JSON result record it was asked for"
REFUSED_WHY = "the provider refused this call: the result record's stop_reason is refusal"


def _stdout(result):
    out = result.get("stdout")
    if out is not None and not isinstance(out, str):
        raise ValueError("a Claude result's stdout must be text or None, not %s" % type(out).__name__)
    return out


def _record(out):
    """The JSON result record `claude -p --output-format json` printed, or None for anything else: text that is not
    JSON, a document that is not an object, a repeated member (a cost of 9 then 0) or nesting past the parser's limit."""
    try:
        doc = CL.proof_ledger.loads(out or "")
    except (ValueError, RecursionError):
        return None  # sbe: allow-silent None is the refusal: judge fails the call and cost reads NOT_MEASURED
    return doc if isinstance(doc, dict) else None


class ClaudeAdapter(object):
    """The claude transport's argv, judge and cost.

    program  the Claude CLI's absolute path, handed in by the caller (model_call's CLAUDE or model_router.claude_bin).
    env      where BROTHER_CLAUDE_EFFORT is read, at call time (default os.environ), so a refusal is live.
    """

    transport = "claude"

    def __init__(self, program=None, env=None):
        if env is not None and not isinstance(env, Mapping):
            raise ValueError("env must be a mapping or None, not %s" % type(env).__name__)
        self.program, self.env = program, env

    def argv(self, model, prompt, timeout, row):
        """(argv, stdin, None), the shape model_call._argv returns, with the prompt as stdin. Refused before any process
        starts for a row of another transport, a row id or model that is not a plain name, an empty prompt, a timeout
        that is not a whole number of seconds of at least 1 (a bool is not one), an effort level nobody can read, or a
        program that is not an executable file named by absolute path."""
        if not isinstance(row, dict) or row.get("transport") != self.transport:
            raise A.Refused("the Claude adapter builds only a claude row's command line, not %.120r" % (row,))
        model_id = row.get("id")
        if not isinstance(model_id, str) or not MODEL_NAME.fullmatch(model_id):
            raise A.Refused("a claude row's id must be a plain model id, not %.120r" % (model_id,))
        if not isinstance(model, str) or not MODEL_NAME.fullmatch(model):
            raise A.Refused("model must be a plain registry name, not %.120r" % (model,))
        if not isinstance(prompt, str) or not prompt.strip():
            raise A.Refused("the prompt must be non-empty text")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise A.Refused("timeout must be a whole number of seconds, at least 1, not %.60r" % (timeout,))
        program = self.program
        if not isinstance(program, str) or "\0" in program or not os.path.isabs(program) \
                or not os.path.isfile(program) or not os.access(program, os.X_OK):
            raise A.Refused("no Claude program: the caller names an executable by absolute path and this adapter "
                            "never guesses one (%.120r)" % (program,))
        argv = [program, "-p", "--model", model_id] + list(CLAUDE_TRIM) + list(CLAUDE_IO) + list(NO_TOOLS)
        return argv + ["--effort", self._effort(model_id)], prompt, None

    def _effort(self, model_id):
        """The model's floor, lifted by BROTHER_CLAUDE_EFFORT when that names a higher level; never below the floor. An
        unset or blank value is the floor; a value that names no level is refused, never run at a guess."""
        floor = next((f for k, f in FLOOR_BY_MODEL.items() if k in model_id.lower()), DEFAULT_FLOOR)
        env = os.environ if self.env is None else self.env
        raw = env.get("BROTHER_CLAUDE_EFFORT")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return floor
        level = raw.strip().lower() if isinstance(raw, str) else None
        if level not in CLAUDE_EFFORTS:
            raise A.Refused("BROTHER_CLAUDE_EFFORT=%.40r names no effort level (%s)" % (raw, ", ".join(CLAUDE_EFFORTS)))
        return level if CLAUDE_EFFORTS.index(level) > CLAUDE_EFFORTS.index(floor) else floor

    def judge(self, model, row, result):
        """The contract's AdapterResult for one finished claude run, with cost_usd from its record. A row of another
        transport, a result that is not a mapping, a returncode that is not an int (False is not exit 0) and output
        that is not text are refused, so no other transport's output is ever judged by the claude rules."""
        if not (isinstance(row, dict) and row.get("transport") == self.transport):
            raise ValueError("the Claude adapter judges only a claude row's result, not %.120r" % (row,))
        if not isinstance(result, dict):
            raise ValueError("a transport result must be a mapping, not %s" % type(result).__name__)
        code = result.get("returncode")
        if isinstance(code, bool) or not isinstance(code, int):
            raise ValueError("a Claude result's returncode must be an int, not %.40r" % (code,))
        doc = _record(_stdout(result))
        try:
            verdict = A.judge(self, model, row, result)
        except RecursionError:   # the contract's parser gives up on nesting past its limit: that is not the record
            verdict = A.AdapterResult(False, "", NOT_THE_RECORD, A.STATUS_FAILED)
        if verdict.status != A.STATUS_PROVIDER_REFUSED and doc is not None and not doc.get("is_error") \
                and doc.get("stop_reason") == "refusal":
            verdict = A.AdapterResult(False, "", REFUSED_WHY, A.STATUS_PROVIDER_REFUSED)
        elif verdict.ok and doc is None:
            verdict = A.AdapterResult(False, "", NOT_THE_RECORD + " (a repeated member)", A.STATUS_FAILED)
        verdict.cost_usd = self.cost(result)
        return verdict

    def cost(self, result, usage=None):
        """The record's total_cost_usd as a float, or NOT_MEASURED. A result that is not a mapping, output that is not
        text and usage that is not a mapping are refused; the figure itself is never coerced into a cost."""
        if not isinstance(result, dict):
            raise ValueError("a transport result must be a mapping, not %s" % type(result).__name__)
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("usage must be a mapping or None, not %s" % type(usage).__name__)
        doc = _record(_stdout(result))
        if doc is None:
            return A.NOT_MEASURED   # no readable record: malformed output is not a measurement
        value = doc.get("total_cost_usd")
        if value is None:
            return A.NOT_MEASURED   # the record reported no cost: UNKNOWN, never 0.0
        if not CL.valid_cost(value):
            return A.NOT_MEASURED   # a bool, text, negative or non finite figure is not spend
        return A.normalize_cost(value, usage)
