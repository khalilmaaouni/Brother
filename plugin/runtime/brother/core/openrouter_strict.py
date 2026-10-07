"""Brother Core: structural fallback-detection wrapper (unit OR-2).

Replaces a voluntary "check the [usage] model= line if you have time"
discipline, which a caller under deadline pressure will skip, with a
wrapper that fails closed unless the bridge's own real usage line
names the requested model. Also enforces the timeout and max-tokens
floors this session's own real, self-inflicted timeouts named: a
--timeout below 300 seconds and a --max near a model's effort floor
both caused real failures tonight.
"""

import math
import re
from dataclasses import dataclass
from typing import Optional
import subprocess

MIN_TIMEOUT_SECONDS = 300

# Line-anchored: a usage tag quoted mid-sentence is never the bridge's own line.
_USAGE_LINE_RE = re.compile(r"^\[usage\][^\n]*\bmodel=(\S+)", re.MULTILINE)

# D2 (board unit D2, audit issue F34): the WHOLE winning [usage] line, so
# token counts and the model are always read off the SAME line, never mixed
# across two different [usage] lines in one stderr blob.
_USAGE_FULL_LINE_RE = re.compile(r"^\[usage\][^\n]*$", re.MULTILINE)
_TOKEN_PAIR_RE = re.compile(
    r"\bprompt=(?P<prompt>\S+)\s+completion=(?P<completion>\S+)"
    r"|\binput=(?P<input>\S+)\s+output=(?P<output>\S+)")
# The bridge's own billed line (scripts/loop/or_ask.py billed_line()); only
# known=yes is a real, measured figure, known=no is a floor and stays NO-DATA.
_BILLED_LINE_RE = re.compile(
    r"^\[billed\] usd=([0-9.]+) attempts=\d+ known=(yes|no)", re.MULTILINE)


class FallbackDetected(Exception):
    """Raised when the bridge's real usage line names a different
    model than requested, and allow_fallback was not set; and raised
    when the bridge exited 0 with no usage line at all, because no
    line proves who answered (L5a-7, row X8: a missing proof blocks
    instead of passing silently). A nonzero exit with no usage line
    is a NO-DATA refusal and is handed back unraised."""


class TimeoutTooLow(Exception):
    """Raised when a caller asks for a timeout below the floor that
    caused real, self-inflicted timeouts this session."""


class MaxTokensTooLow(Exception):
    """Raised when max_tokens is below a model's documented floor."""


def _require_real_number(value, name):
    """A real, finite, non bool number, or a ValueError. A bool IS an int
    in Python, so it is refused rather than silently read as 0 or 1; a
    NaN is refused because every comparison against a NaN is False, which
    would silently clear a floor instead of blocking the call. A None, a
    str, bytes or a container is refused the same way, never a raw
    TypeError from a comparison."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "%s must be a real number, got %s" % (name, type(value).__name__))
    if not math.isfinite(value):
        raise ValueError("%s must be finite, got %r" % (name, value))
    return value


def enforce_floors(timeout_seconds, max_tokens, min_max_tokens):
    """Raises before any call is made, never after, so a caller cannot
    accidentally reproduce tonight's own self-inflicted timeouts.

    Every argument is refused unless it is a real, finite number (no
    None, no bool, no NaN): a NaN compares False against every floor, so
    an unchecked NaN would silently clear both floors instead of
    blocking the call, and a None reached a real TypeError."""
    _require_real_number(timeout_seconds, "timeout_seconds")
    _require_real_number(max_tokens, "max_tokens")
    _require_real_number(min_max_tokens, "min_max_tokens")
    if timeout_seconds < MIN_TIMEOUT_SECONDS:
        raise TimeoutTooLow(
            "timeout_seconds=%s is below the %s second floor; a lower "
            "value caused real self-inflicted timeouts this session"
            % (timeout_seconds, MIN_TIMEOUT_SECONDS)
        )
    if max_tokens < min_max_tokens:
        raise MaxTokensTooLow(
            "max_tokens=%s is below this model's own %s floor"
            % (max_tokens, min_max_tokens)
        )


def _json_safe_number(value):
    """A JSON native, nonnegative, finite float, or None. Refuses NaN, inf,
    a bool (a bool IS an int in Python and must never be read as a count)
    and a negative value rather than passing any of them through, so a
    caller that only reads None can never mistake an unmeasured value for
    a real zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


# A token count is a COUNT, never a float. Only a plain digit string (no
# sign, no decimal point, no exponent, no "nan"/"inf" spelling) is exact
# integer parsing: this pattern is refused before int()/float() ever sees
# it, so a value neither loses precision above 2**53 nor gets a fractional
# string ("1.9") silently truncated into a fabricated integer.
_EXACT_DIGITS_RE = re.compile(r"^\d+$")


def _json_safe_token_count(value):
    """An exact, nonnegative integer token count, or None (CODEX-REVIEW
    int1 finding R5, 2026-09-26): the old path ran every count through
    float() then int(), which turned "1.9" into a fabricated 1 and lost
    precision on an exact integer above 2**53 ("9007199254740993" read
    back as 9007199254740992). Missing, fractional, non-numeric,
    negative, a bool, "nan" or "inf" all stay None here, never rounded
    or truncated into an invented count."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str) or not _EXACT_DIGITS_RE.match(value):
        return None
    return int(value)  # a plain digit string: Python's int() never loses precision


def parse_usage_envelope(stderr_text):
    """A plain, JSON safe dict {"model", "prompt_tokens", "completion_tokens",
    "cost_usd"} read from the bridge's own real stderr, or None when no
    [usage] line is present at all, the same NO-DATA case parse_usage_model
    already returns None for. The model and both token counts come from the
    SAME winning [usage] line (the last one, forgery proofed exactly like
    parse_usage_model); cost comes from the bridge's own separate [billed]
    line, only when it reports known=yes. Every numeric field is a JSON
    native, nonnegative, finite number or None, never a synthesized 0.

    Root cause of board unit D2 / audit issue F34 (four reverted landings:
    590af7c4e, and reverts 2ee79d532, ec8c178ef, 7a13e424b, 78f713791): the
    earlier fix put a whole UsageObservation OBJECT into run_strict's own
    return value, and that object reached or_fanout's JSON results file and
    crashed json.dump ("UsageObservation is not JSON serializable"). This
    function returns a plain dict instead, and run_strict's own return
    contract (the second value is the model id string or None) never
    changes: no existing caller of run_strict changes type because of it.
    """
    if stderr_text is None:
        return None
    if not isinstance(stderr_text, str):
        raise ValueError(
            "stderr_text must be a string or None, got %s" % type(stderr_text).__name__)
    lines = _USAGE_FULL_LINE_RE.findall(stderr_text)
    if not lines:
        return None
    line = lines[-1]  # the bridge prints its usage line last

    model_match = re.search(r"\bmodel=(\S+)", line)

    prompt_tokens = completion_tokens = None
    tokens_match = _TOKEN_PAIR_RE.search(line)
    if tokens_match:
        prompt = tokens_match.group("prompt")
        if prompt is None:
            prompt = tokens_match.group("input")
        completion = tokens_match.group("completion")
        if completion is None:
            completion = tokens_match.group("output")
        prompt_tokens = _json_safe_token_count(prompt)
        completion_tokens = _json_safe_token_count(completion)

    cost_usd = None
    billed_matches = _BILLED_LINE_RE.findall(stderr_text)
    if billed_matches:
        amount, known = billed_matches[-1]
        if known == "yes":
            cost_usd = _json_safe_number(amount)

    return {
        "model": model_match.group(1) if model_match else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost_usd,
    }


def parse_usage_model(output_text):
    """The model name from a real [usage] line (either the
    'input=...output=...model=X' or 'prompt=...completion=...model=X'
    shape the bridge actually prints), or None if no usage line is
    present at all (a NO-DATA refusal with nothing to compare). A value
    that is neither a string nor None is refused: a wrong type is never
    folded into the no-usage answer, which is the safe direction for a
    caller that only reads None. A thinned wrapper over
    parse_usage_envelope (D2, REQ-PARSE-4): one regex source for the
    model, never two that can drift apart."""
    if output_text is None:
        return None
    if not isinstance(output_text, str):
        raise ValueError(
            "output_text must be a string or None, got %s" % type(output_text).__name__)
    observation = parse_usage_envelope(output_text)
    return observation["model"] if observation else None


def run_strict(bridge_argv, requested_model, timeout_seconds,
                allow_fallback=False, runner=None, env=None):
    """Run the bridge for real (bridge_argv, a real subprocess command
    list) and raise FallbackDetected unless its own [usage] model=
    line names requested_model, or allow_fallback=True is passed
    explicitly.

    runner, if given, replaces the real subprocess call for a
    hermetic test (the same seam this repo already uses elsewhere: a
    real subprocess call by default, a fake for tests that must not
    depend on network access or a live API key).

    env, if given, is the bridge's whole environment (None inherits
    this process's): how dispatch hands a proof phase call its
    reservation id.
    """
    run = runner or (lambda argv, timeout: subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, env=env
    ))
    if runner is not None and not callable(runner):
        raise ValueError(
            "runner must be callable or None, got %s" % type(runner).__name__)
    # the local name is bridge_runner, never a bare dynamic run(): a
    # command runner call whose argv is not a literal list is refused
    # L5a-7, row X9: the argv type is checked before it is used. A str
    # would be spread into characters; bytes or None would reach the
    # runner as something no process can be built from.
    if not isinstance(bridge_argv, (list, tuple)) or not bridge_argv \
            or not all(isinstance(item, str) for item in bridge_argv):
        raise ValueError(
            "bridge_argv must be a non-empty list or tuple of str, got %s"
            % type(bridge_argv).__name__)
    bridge_runner = run
    try:
        result = bridge_runner(bridge_argv, timeout_seconds)
    except Exception as exc:
        # only the TYPE is named: an exception's text can carry a token
        raise ValueError(
            "the bridge runner raised %s; refusing to continue with an "
            "unmeasured call" % type(exc).__name__)
    # stderr only: the bridge prints its usage line there and nowhere
    # else, while stdout is the model's answer, which can quote or forge
    # a usage tag (seen 2026-09-20: a review of this very file did).
    actual_model = parse_usage_model(result.stderr)

    if actual_model is None:
        # L5a-7, row X8: an exit of 0 with no usage line is an answer
        # nobody proved; it blocks unless a library caller overrides.
        # A nonzero exit is a NO-DATA refusal the caller settles.
        if result.returncode == 0 and allow_fallback is not True:
            raise FallbackDetected(
                "requested model %r but the bridge exited 0 with no "
                "[usage] line on stderr, so nothing proves who answered; "
                "pass allow_fallback=True to accept an unproven answer "
                "explicitly" % (requested_model,))
        return result, None  # a NO-DATA refusal; nothing to compare

    if actual_model != requested_model and not allow_fallback:
        raise FallbackDetected(
            "requested model %r but the real usage line names %r; "
            "pass allow_fallback=True to accept a fallback explicitly "
            "rather than silently trusting the --model flag"
            % (requested_model, actual_model)
        )

    return result, actual_model


_PROVENANCE_BRIDGE_STDERR_USAGE = "bridge.stderr.usage"


@dataclass(frozen=True)
class UsageObservation:
    """D2.1: one immutable reading of the bridge's own real [usage] line.
    The model and both token counts come from the SAME winning [usage]
    line (the last anchored one, exactly like parse_usage_envelope); cost
    comes from the bridge's own separate [billed] line only when that
    line reports known=yes. Every numeric field is None (NO-DATA) on
    missing, negative, non finite or unrecognised input: never a
    synthesized 0. envelope_kind is "input_output" for an input= plus
    output= pair, "prompt_completion" for a prompt= plus completion=
    pair, otherwise "unknown". raw_line is the verbatim winning line.
    provenance is always _PROVENANCE_BRIDGE_STDERR_USAGE in 1.1.0."""

    model: Optional[str]
    cost_usd: Optional[float]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    envelope_kind: str
    raw_line: str
    provenance: str


def _usage_envelope_kind(raw_line):
    """The envelope shape of one verbatim [usage] line: "input_output"
    for an input= plus output= pair, "prompt_completion" for a prompt=
    plus completion= pair, otherwise "unknown"."""
    if re.search(r"\binput=\S+", raw_line) and re.search(r"\boutput=\S+", raw_line):
        return "input_output"
    if re.search(r"\bprompt=\S+", raw_line) and re.search(r"\bcompletion=\S+", raw_line):
        return "prompt_completion"
    return "unknown"


def parse_usage_observation(stderr_text):
    """D2.1: the typed, frozen UsageObservation view of the same winning
    [usage] line parse_usage_envelope already reads as a plain dict.
    Additive: parse_usage_envelope still returns its dict and
    parse_usage_model still returns a string or None, so no existing
    caller changes type. Returns None when there is no [usage] line at
    all (the same NO-DATA answer as parse_usage_model). A value that is
    neither a string nor None is refused with ValueError, never folded
    into the no-usage answer and never a raw TypeError."""
    if stderr_text is None:
        return None
    if not isinstance(stderr_text, str):
        raise ValueError(
            "parse_usage_observation: stderr_text must be a string or None, got %s"
            % type(stderr_text).__name__)
    lines = _USAGE_FULL_LINE_RE.findall(stderr_text)
    if not lines:
        return None
    raw_line = lines[-1]
    envelope = parse_usage_envelope(stderr_text)
    if envelope is None:
        return None
    return UsageObservation(
        model=envelope["model"],
        cost_usd=envelope["cost_usd"],
        input_tokens=envelope["prompt_tokens"],
        output_tokens=envelope["completion_tokens"],
        envelope_kind=_usage_envelope_kind(raw_line),
        raw_line=raw_line,
        provenance=_PROVENANCE_BRIDGE_STDERR_USAGE,
    )
