#!/usr/bin/env python3
"""jev_ticket_triage: J1.b of the Jev use cases, ticket triage.

An incoming ticket (a help desk message, a call center wrap up note, an
issue created in a tracker such as Jira or Asana) is given exactly one
destination: a named queue the operator declared, or HUMAN_QUEUE. This
module never writes to the tracker. It returns a decision record

    {"queue": str, "by": str, "scores": dict or None, "consulted": bool}

that an adapter may apply.

THE RULES THIS MODULE OBEYS.

  H1  Jev never approves. A declared rule decides outright and is never
      overridden by any model answer. When no rule matches, the fixed
      threshold check in triage_verdict() alone turns a model's scores
      into a queue: the model supplies numbers, never the verdict.
  H2  Unknown means stricter. A missing score set, an empty score set, a
      tie at the top, a top score under the bar, a top queue the operator
      never declared, an unusable answer, or a model that raised, all
      resolve to HUMAN_QUEUE, never to a named queue.
  H3  Nothing private leaves the machine. In shadow and assist modes the
      ticket text passes scripts/coe_outside_gate.py FIRST. A refusal
      there means nothing is sent and the ticket goes to HUMAN_QUEUE.
  H4  The seam ships OFF. mode "off" consults nothing.

THE THREE MODES (triage()'s `mode` argument).

  off     no model call at all; no rule match means HUMAN_QUEUE.
  shadow  the model is asked and its answer is recorded in "scores", but
          the returned "queue" is ALWAYS HUMAN_QUEUE. A recorded second
          opinion never decides.
  assist  the model is asked and triage_verdict() turns its scores into
          the returned queue under the fixed CONFIDENT_AT bar.

Any other mode string is refused with ValueError: an unrecognised mode is
a configuration typo, never guessed at.

HOSTILE INPUT. Every public function refuses a wrong type, None, NaN, a
bool, an unhashable name, or a malformed mapping with this module's own
ValueError (or, where the specification says so, HUMAN_QUEUE). A raw
interpreter TypeError never escapes: a mapping whose own .items() raises,
or an entry that is not a pair, is refused with a ValueError instead.
The one validation every score reader routes through is _safe_items().

THE "by" FIELD names what decided, one of: "rule" (a declared rule),
"off" (mode off, nothing consulted), "shadow" (a shadow answer recorded,
the human queue kept), "model" (assist scores cleared the bar),
"human-review" (assist ran but the threshold check refused the scores),
"gate-refused" (the content gate refused, nothing was sent), "no-data"
(the model was never usable).

Python 3.9 floor, standard library only, no network in this module.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coe_outside_gate as _outside_gate  # noqa: E402  (sibling module, reused not copied)

#: The destination every uncertain case falls back to. A ticket is always
#: routed to a named queue or to this one, never to none (R3).
HUMAN_QUEUE = "human-review"

#: The fixed bar triage_verdict() uses when a caller passes no override: a
#: top score strictly below this goes to HUMAN_QUEUE.
CONFIDENT_AT = 0.8

#: The only mode strings this module recognises. Anything else is a config
#: typo and is refused, never treated as off by accident (H2).
_MODES = ("off", "shadow", "assist")


def _require_str(value, label):
    """A non-empty str, or ValueError. A wrong type is never coerced.

    A str SUBCLASS passes (an isinstance check is the right width), but a
    name that is not a str at all is refused before any hash is ever
    taken: a list name can never reach a set/dict membership test, so no
    raw "unhashable type" TypeError can escape from a score name.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("%s must be a non-empty str" % label)
    return value


def _require_number(value, label):
    """A finite real number, or ValueError. bool is refused (it is not a
    score), and NaN and infinity are refused rather than compared."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be a number" % label)
    if not math.isfinite(value):
        raise ValueError("%s must be a finite number" % label)
    return value


def _require_queue_list(queues):
    """The operator's declared queue list: a non-empty list of non-empty
    str names, or ValueError. Duplicates are allowed and change nothing."""
    if not isinstance(queues, list) or not queues:
        raise ValueError("queues must be a non-empty list")
    for name in queues:
        _require_str(name, "each declared queue")
    return queues


def _safe_items(mapping, label):
    """The (name, value) pairs of `mapping` as a list, or ValueError.

    A hostile or malformed mapping (a dict subclass whose own items()
    raises TypeError, a view that breaks part way through being consumed,
    anything a caller can arrange so that READING the mapping raises the
    interpreter's own TypeError rather than yielding pairs) is refused
    here with this module's own ValueError, so no raw interpreter
    exception ever escapes a public function (H2, and the estate's
    "hostile input is refused, never a crash" rule). This is the one
    validation every score reader routes through: triage_verdict and
    _clean_scores both call it.
    """
    try:
        return list(mapping.items())
    except TypeError as exc:
        raise ValueError("%s must expose readable name/value pairs: %s" % (label, exc))


def _pair(entry, label):
    """A (name, value) pair out of one iteration entry, or ValueError."""
    try:
        name, value = entry
    except (TypeError, ValueError):
        raise ValueError("%s entries must be (name, value) pairs" % label)
    return name, value


def _ticket_parts(ticket):
    """The ticket's subject and body as a list of non-empty str, or
    ValueError. A missing key is simply absent; a present key of the
    wrong type is refused rather than folded away."""
    if not isinstance(ticket, dict):
        raise ValueError("ticket must be a dict")
    parts = []
    for key in ("subject", "body"):
        if key not in ticket:
            continue
        value = ticket[key]
        if not isinstance(value, str):
            raise ValueError("ticket %r must be a str" % key)
        if value:
            parts.append(value)
    return parts


def _ticket_content(ticket):
    """The text this module matches on and would send: subject and body,
    newline joined, original case preserved. This is what the outside
    content gate scans."""
    return "\n".join(_ticket_parts(ticket))


def _clean_scores(raw):
    """A model answer to a {queue: number} dict, or None when the answer is
    missing, empty, malformed, unhashable, or carries a non-finite or
    non-numeric score. None is the caller's signal to fall back to
    HUMAN_QUEUE. Any TypeError raised while reading the mapping is
    converted to None, never allowed to escape."""
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        raw_items = list(raw.items())
    except TypeError:
        return None
    cleaned = {}
    for entry in raw_items:
        try:
            name, value = entry
        except (TypeError, ValueError):
            return None
        if not isinstance(name, str) or not name:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value):
            return None
        cleaned[name] = value
    return cleaned


def rule_route(ticket, rules):
    """The first declared rule whose EVERY keyword appears in the ticket's
    subject or body names the queue. None when no rule matches. Pure, case
    folded, no model.

    A malformed rule, a malformed keyword, a malformed ticket or a rules
    argument that is not a list is refused with ValueError: a rule the
    operator cannot read is never guessed into a route (H2).
    """
    if not isinstance(rules, list):
        raise ValueError("rules must be a list")
    haystack = _ticket_content(ticket).lower()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("each rule must be a dict")
        queue = _require_str(rule.get("queue"), "rule queue")
        keywords = rule.get("keywords")
        if not isinstance(keywords, list) or not keywords:
            raise ValueError("rule keywords must be a non-empty list")
        hit = True
        for keyword in keywords:
            _require_str(keyword, "rule keyword")
            if keyword.lower() not in haystack:
                hit = False
                break
        if hit:
            return queue
    return None


def triage_verdict(scores, queues, confident_at=CONFIDENT_AT):
    """The single declared queue whose score is at least `confident_at`
    AND strictly higher than every other, else HUMAN_QUEUE. Pure.

    HUMAN_QUEUE for: None scores, an empty dict, a tie at the top, a top
    score under the bar, or a top queue the operator never declared (H2,
    R3). Duplicate names in `queues` change nothing.

    A score that is not a finite number, a queue name that is not a
    non-empty str, scores that are not a dict, an empty `queues` and a
    `confident_at` that is not a finite number are all refused with
    ValueError rather than compared. A mapping whose own .items() raises
    TypeError (a hostile dict subclass, an unhashable key surfacing from
    the mapping's own read) is refused with ValueError too, never a raw
    interpreter TypeError.
    """
    _require_queue_list(queues)
    _require_number(confident_at, "confident_at")
    if scores is None:
        return HUMAN_QUEUE
    if not isinstance(scores, dict):
        raise ValueError("scores must be a dict or None")
    if not scores:
        return HUMAN_QUEUE

    raw_items = _safe_items(scores, "scores")

    best_queue = None
    best_score = None
    tied = False
    for entry in raw_items:
        name, value = _pair(entry, "scores")
        _require_str(name, "score queue name")
        _require_number(value, "score for %r" % name)
        if best_score is None or value > best_score:
            best_queue = name
            best_score = value
            tied = False
        elif value == best_score:
            tied = True

    if tied:
        return HUMAN_QUEUE
    if best_score < confident_at:
        return HUMAN_QUEUE
    if best_queue not in queues:
        return HUMAN_QUEUE
    return best_queue


def triage(ticket, queues, rules, ask=None, mode="off"):
    """Route one ticket. Returns {"queue", "by", "scores", "consulted"}.

    A rule match decides and is never overridden (H1). With no rule match:
    mode "off" returns HUMAN_QUEUE with nothing consulted; mode "shadow"
    returns HUMAN_QUEUE with the model's answer recorded in "scores"; mode
    "assist" runs triage_verdict() over the model's scores.

    An empty `queues` raises ValueError. An unrecognised mode, a
    non-callable `ask`, a queue list holding a non-str, and any hostile
    ticket shape are refused with ValueError rather than guessed at.
    """
    _require_queue_list(queues)
    if not isinstance(mode, str) or mode not in _MODES:
        raise ValueError("mode must be one of %r" % (_MODES,))
    if ask is not None and not callable(ask):
        raise ValueError("ask must be a callable or None")

    matched = rule_route(ticket, rules)
    if matched is not None:
        return {"queue": matched, "by": "rule", "scores": None, "consulted": False}

    if mode == "off":
        return {"queue": HUMAN_QUEUE, "by": "off", "scores": None, "consulted": False}

    # H3: shadow and assist both mean a model would see this text, so the
    # outside content gate runs FIRST, on the same process, before the ask
    # callable is ever touched. A refusal here is this seam's own NO-DATA:
    # nothing is sent and the ticket goes to the human queue.
    content = _ticket_content(ticket)
    verdict = _outside_gate.check(content)
    if not verdict.allowed:
        return {"queue": HUMAN_QUEUE, "by": "gate-refused", "scores": None, "consulted": False}

    if ask is None:
        return {"queue": HUMAN_QUEUE, "by": "no-data", "scores": None, "consulted": False}

    try:
        raw = ask(content)
    except Exception:  # noqa: BLE001 -- H2: a model that raised is an unknown.
        return {"queue": HUMAN_QUEUE, "by": "no-data", "scores": None, "consulted": False}

    scores = _clean_scores(raw)
    if scores is None:
        return {"queue": HUMAN_QUEUE, "by": "no-data", "scores": None, "consulted": True}

    if mode == "shadow":
        return {"queue": HUMAN_QUEUE, "by": "shadow", "scores": scores, "consulted": True}

    route = triage_verdict(scores, queues)
    if route == HUMAN_QUEUE:
        return {"queue": HUMAN_QUEUE, "by": "human-review", "scores": scores, "consulted": True}
    return {"queue": route, "by": "model", "scores": scores, "consulted": True}
