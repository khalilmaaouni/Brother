"""The adapter contract for the loop's model transports (FX-31.1).

One small interface per transport, so result judgment and cost normalization have a single home
and no caller keeps a private copy of either. This sub unit ships the contract itself:

  adapter_for     refuses an unknown or unhashable transport BEFORE any process could start
  judge           reports a finished transport result in one shared status vocabulary
  normalize_cost  keeps a missing cost as NOT_MEASURED and a reported, priced zero as 0.0

The module is deliberately inert. It imports nothing from the loop, starts no process and names
no executable: command construction belongs to the transport runners, so nothing here can reach a
binary. An adapter built here refuses to invent a command line rather than guessing one.
"""
import json
import math
from typing import Optional, Protocol

NOT_MEASURED = None          # the cost sentinel: an absent cost is UNKNOWN, never 0.0
STATUS_OK = "OK"
STATUS_FAILED = "FAILED"
STATUS_PROVIDER_REFUSED = "PROVIDER_REFUSED"

#: Every transport this contract carries. A transport outside this tuple is refused, never guessed.
TRANSPORTS = ("bridge", "claude", "codex")

#: A provider refusal is a status of its own, never a transport success and never a plain failure.
PROVIDER_REFUSAL_MARKERS = ("provider refused", "provider_refused", "provider refusal")


class Refused(Exception):
    """A gate said no. It carries the reason, because a refusal without one gets worked around."""


class AdapterResult(object):
    """One judged transport result, in the shared vocabulary.

    ok is the fail direction a caller acts on. status is the vocabulary: STATUS_OK, STATUS_FAILED
    or STATUS_PROVIDER_REFUSED. cost_usd is NOT_MEASURED when the transport reported no cost, and a
    float (0.0 included) when it reported one.
    """

    __slots__ = ("ok", "answer", "detail", "status", "cost_usd")

    def __init__(self, ok, answer, detail, status, cost_usd=NOT_MEASURED):
        self.ok = bool(ok)
        self.answer = answer
        self.detail = detail
        self.status = status
        self.cost_usd = cost_usd

    def __repr__(self):
        return "<AdapterResult %s %s %s>" % (
            self.status, "ok" if self.ok else "failed", str(self.detail)[:60])


class Adapter(Protocol):
    """What every transport owes its callers.

    argv   the command line, its stdin and its safety settings, from the registry row.
    judge  the AdapterResult for one finished transport result.
    cost   the measured cost, or NOT_MEASURED when the transport reported none.
    """

    transport: str

    def argv(self, model: str, prompt: str, timeout: int, row: dict) -> tuple: ...
    def judge(self, model: str, row: dict, result: dict) -> object: ...
    def cost(self, result: dict, usage: Optional[dict] = None) -> Optional[float]: ...


def normalize_cost(value, usage=None):
    """NOT_MEASURED or a float. A missing cost stays UNKNOWN and is never rendered as zero.

    A reported cost of 0 is a MEASUREMENT (a free or fully cached call) and is returned as 0.0, so
    the two states stay distinguishable all the way to the ledger. Hostile input is refused with
    this module's own error rather than coerced or silently accepted: a bool is not a number here,
    a string is not parsed, and a non finite value is not a cost.
    """
    if usage is not None and not isinstance(usage, dict):
        raise ValueError("usage must be a mapping or None, not %s" % type(usage).__name__)
    if value is None:
        return NOT_MEASURED
    if isinstance(value, bool):
        raise ValueError("a cost must be a number, not a bool")
    if not isinstance(value, (int, float)):
        raise ValueError("a cost must be a number, not %s" % type(value).__name__)
    try:
        amount = float(value)
    except (OverflowError, ValueError, TypeError) as exc:
        raise ValueError("a cost must be a usable number: %s" % exc)
    if math.isnan(amount) or math.isinf(amount):
        raise ValueError("a cost must be a finite number, not %r" % (value,))
    return amount


def _text(value, field):
    """The text of a transport result field, or "" when the field is absent.

    A field that is present and is not text is corrupt input: it is refused here, named, rather
    than stringified into a plausible looking answer.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("a transport result's %s must be text or None, not %s"
                         % (field, type(value).__name__))
    return value


def _claude_doc(out):
    """The JSON result record a headless first party call prints, or None for anything else."""
    try:
        doc = json.loads(out or "")
    except ValueError:
        return None  # sbe: allow-silent output that is not JSON is not the record; _claude_answer turns None into a named failure, never an answer
    return doc if isinstance(doc, dict) else None


def _claude_answer(out):
    """(answer, why). An error record, a document that is not the record, or an empty result is a
    failure, never an answer."""
    doc = _claude_doc(out)
    if doc is None:
        return "", "the first party output is not the JSON result record it was asked for"
    if doc.get("is_error"):
        return "", "the call returned an error result: %s" % str(doc.get("result"))[:90]
    answer = doc.get("result")
    if not isinstance(answer, str) or not answer.strip():
        return "", "the call returned an empty result"
    return answer, ""


def judge(adapter, model, row, result):
    """The AdapterResult for one finished transport result.

    A non zero exit, an empty body at exit zero and a first party error record all FAIL, and none
    of them is ever returned as an answer. A provider refusal carries its own status. A result that
    is not a mapping, an adapter carrying no transport, and a model that is not a name are refused
    here by name, so a caller cannot hand this a shape it cannot judge.
    """
    try:
        transport = adapter.transport
    except AttributeError:
        raise ValueError("judge needs an adapter carrying a transport, not %r" % (adapter,))
    if not isinstance(transport, str):
        raise ValueError("an adapter's transport must be a string, not %s" % type(transport).__name__)
    if not isinstance(model, str) or not model:
        raise ValueError("model must be a non-empty string, not %r" % (model,))
    if not isinstance(row, dict):
        raise ValueError("a registry row must be a mapping, not %s" % type(row).__name__)
    if not isinstance(result, dict):
        raise ValueError("a transport result must be a mapping, not %s" % type(result).__name__)
    out = _text(result.get("stdout"), "stdout").strip()
    err = _text(result.get("stderr"), "stderr").strip()
    blob = (out + "\n" + err).lower()
    if any(marker in blob for marker in PROVIDER_REFUSAL_MARKERS):
        return AdapterResult(False, out, "the provider refused this call", STATUS_PROVIDER_REFUSED)
    code = result.get("returncode")
    if code != 0:
        first = (err or out).splitlines()[0][:90] if (err or out) else "no output"
        return AdapterResult(False, out, "exit %s: %s" % (code, first), STATUS_FAILED)
    if transport == "claude":
        out, why = _claude_answer(out)
        if why:
            return AdapterResult(False, "", why, STATUS_FAILED)
    if not out:
        return AdapterResult(False, "", "exit 0 with an EMPTY answer", STATUS_FAILED)
    return AdapterResult(True, out, "answered", STATUS_OK)


class _TransportAdapter(object):
    """Judging and costing are one contract for every transport; only the transport's own
    vocabulary changes. Command construction is NOT here: it belongs to the transport runner, and
    an adapter that cannot build a correct line says so instead of inventing one.
    """

    transport = ""

    def argv(self, model, prompt, timeout, row):
        raise Refused("the %s adapter builds no command line in this build; the transport runner "
                      "owns command construction" % (self.transport or "unnamed"))

    def judge(self, model, row, result):
        return judge(self, model, row, result)

    def cost(self, result, usage=None):
        if not isinstance(result, dict):
            raise ValueError("a transport result must be a mapping, not %s" % type(result).__name__)
        return normalize_cost(result.get("cost_usd"), usage)


class _BridgeAdapter(_TransportAdapter):
    transport = "bridge"


class _ClaudeAdapter(_TransportAdapter):
    """The first party transport reports its result record and its cost on stdout as JSON."""

    transport = "claude"

    def cost(self, result, usage=None):
        if not isinstance(result, dict):
            raise ValueError("a transport result must be a mapping, not %s" % type(result).__name__)
        value = result.get("cost_usd")
        if value is None:
            stdout = result.get("stdout")
            doc = _claude_doc(stdout) if isinstance(stdout, str) else None
            if doc is not None:
                value = doc.get("total_cost_usd")
        return normalize_cost(value, usage)


class _CodexAdapter(_TransportAdapter):
    transport = "codex"


_TRANSPORT_ADAPTERS = {"bridge": _BridgeAdapter, "claude": _ClaudeAdapter, "codex": _CodexAdapter}


def adapter_for(transport):
    """The registered adapter for a transport, or a refusal BEFORE anything could be dispatched.

    An absent, unhashable or unknown transport is refused here by name: a guessed transport would
    send content somewhere nobody intended, which is what this contract exists to prevent. No
    process is started, and no default is substituted.
    """
    if not isinstance(transport, str):
        raise Refused("a transport must be a string, not %s" % type(transport).__name__)
    name = transport.strip().lower()
    adapter = _TRANSPORT_ADAPTERS.get(name)
    if adapter is None:
        raise Refused("unknown transport %r; refusing to guess one, this contract carries %s"
                      % (transport, ", ".join(TRANSPORTS)))
    return adapter()
