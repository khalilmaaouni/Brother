"""coe_outside_gate: the privacy scan an outside council seat cannot skip.

WHY THIS EXISTS. A council seat marked "outside" (COE-SUBSYSTEM-WBS.md,
section 2) runs on a model outside this vendor. Sending content to it is
equivalent to publishing that content. The rule that only permitted content
may leave the machine has, until this module, lived in a human's memory and
been run by hand: tonight it was run by hand four times, and the first time,
AFTER the call it was meant to gate had already been launched. This estate
already has a name for that shape of failure, written down before tonight:
"a scan that prints a count and proceeds is not a gate" (memory note
a-scan-that-prints-a-count-and-proceeds-is-not-a-gate). This module is the
fix: the scan and the call are wired together in one function, in one
process, in an order the caller cannot invert.

THE TWO ENTRY POINTS.

  check(content, *, terms=None) -> GateResult
      Scans `content` and reports the verdict. Never calls anything outside
      itself, never raises for a bad verdict: an unscannable or forbidden
      input is a GateResult with allowed=False, not an exception. The only
      reason to call this directly is to inspect a verdict without also
      wiring up the call it would gate.

  guard(content, call, *, terms=None)
      THE FUNCTION CALLERS ACTUALLY USE. Runs check() first, in this
      process, and invokes the zero-argument callable `call` only when the
      result allows it. There is no way to obtain `call`'s result from this
      function without check() having already run and passed, because
      `call` is not invoked anywhere except the one line after that check:
      the caller never gets to decide, after the fact, whether the scan
      "counted." If check() refuses, guard() raises GateRefused and `call`
      is never touched. Compare the alternative this module refuses to be:
      a caller runs a scan, reads a count off to the side, and calls the
      outside model regardless because the scan and the call are two
      separate steps someone can (and, measured tonight, did) reorder.

RULE 4 IS THE ONE THAT MATTERS MOST HERE: UNREADABLE MEANS REFUSE, NEVER
ALLOW. If the term configuration is missing, unreadable, malformed, or
present but empty, this module treats that exactly like a match: it refuses.
An empty or absent term list must never be read as "nothing is forbidden",
because the two are indistinguishable to a caller that only checks
`allowed`, and the failure mode of misreading one as the other is a leak,
not an inconvenience. Every OTHER failure path in this module costs a false
refusal (an outside seat that could safely have run does not). This one
failure path, read backwards, costs a leak, so it is the one direction this
module never gives ground on.

WHAT THIS MODULE IS HONEST ABOUT NOT BEING (RULE 5). This is a term
matcher. It catches content that contains one of the configured forbidden
terms, spelled out, in a form the matcher recognises. It does not
understand meaning, and it cannot recognise private content that happens to
use none of the configured terms: a paraphrase, a translation, a renamed
entity, a screenshot description, or a fact stated without the term that
would have flagged it, all pass this gate untouched. It is a backstop
underneath a human judgement about what is nominated as "outside" content in
the first place, never a replacement for that judgement. Claiming otherwise
would be worse than admitting the gap, because a gate believed to be
airtight is a gate nobody keeps looking past.

THE FALSE POSITIVE THIS MODULE EXISTS TO AVOID (RULE 2). A plain
case-insensitive substring match on a short forbidden term hits ordinary
English words that happen to contain it: measured on a real corpus tonight,
44 hits, 7 of them the everyday word "convex", because a 4-letter forbidden
term sits inside it as a substring. This module's match therefore refuses a
hit whose PRECEDING character is a letter (case-insensitive): "convex" is
rejected because the letter before the embedded term is itself a letter,
while the term standing alone, at either edge of the content, or glued to a
letter or digit AFTER it, still matches, because gluing a suffix onto a
forbidden term does not make it stop being that term. Only the preceding
side is guarded; see `_count_hits` for the one comparison this makes.

RULE 3: THE SCANNER MUST NOT PRINT WHAT IT FORBIDS. This estate has already
recorded an incident where a reviewer explaining a leak scanner wrote the
forbidden terms into a code comment (memory note
the-scanner-must-contain-what-it-forbids). This module carries zero forbidden
terms in its own source, in any test fixture, or in any log line: every
term lives outside this file, in the JSON configuration at DEFAULT_TERMS_PATH
(a category name mapped to a list of terms) or supplied directly by a caller
that already holds it. GateResult.matched_categories names only the
CATEGORY a term belonged to (a label the configuration author chose, such
as "client-terms"), never the term itself, and GateResult.reason and
GateRefused's message are built exclusively from category names and counts.

SIZE CAP. Content over MAX_CONTENT_BYTES is refused unread rather than
scanned: this gate sits synchronously in front of an outside call a
scheduler is waiting on, and a caller that hands it an unbounded payload
should get a fast, honest refusal instead of a gate that quietly becomes
the slow part of the pipeline. See MAX_CONTENT_BYTES for the number and
`check` for where it is enforced, before any decoding or scanning happens.

CONTINGENCY.

  Which direction this fails: every ambiguous case (missing config, empty
  config, malformed config, undecodable bytes, oversized payload, content
  that is neither str nor bytes) refuses. The module has exactly one
  allowed verdict shape (allowed=True, hit_count=0, reason="content is
  clean", matched_categories=[]) and every other combination refuses. A
  false refusal costs a delayed or rerouted call; a false allow costs a
  leak. Given a choice between the two failure directions, this module
  always takes the cheaper one.

  What a caller rolls back: nothing, by construction. guard() invokes
  `call` only after check() has already passed, so a GateRefused means the
  outside call never happened and there is no partial side effect anywhere
  to undo.

  What a caller does when it refuses, and it is NOT "retry": the match is
  deterministic, so calling guard() again with the same content and the
  same terms reproduces the same refusal. A caller that gets GateRefused
  must do one of: (a) remove or generalise the flagged material and check
  again with the changed content, (b) route the work to a seat that is not
  marked outside instead of this one, or (c) escalate to a human to judge
  whether the flagged material is actually private in this context. Looping
  on the same input is the one thing this module's determinism guarantees
  will not work.

Python 3.9 floor, standard library only, no network.
"""
import json
import os

#: Outside every path this module writes to, and not committed with real
#: terms in it: see the module docstring's RULE 3 section. A category name
#: maps to a list of forbidden terms, e.g. {"client-terms": ["..."]}. This
#: constant is a PATH, never a term, so naming it here does not violate the
#: rule it exists to serve.
DEFAULT_TERMS_PATH = os.path.expanduser("~/.claude/coe-outside-gate-terms.json")

#: A synchronous gate that scans slowly is a gate that becomes the pipeline's
#: bottleneck. Content over this size is refused unread rather than scanned;
#: see the module docstring's SIZE CAP section for why 5 MB in particular:
#: comfortably larger than any prompt or transcript this council scores, and
#: small enough that even a pathological configuration (many categories,
#: many terms) scans it well inside a caller's patience.
MAX_CONTENT_BYTES = 5 * 1024 * 1024


class GateResult(object):
    """One scan's verdict. Immutable by convention: construct a new one
    rather than mutating an existing result.

    allowed             bool. False means: do not make the call.
    hit_count           int. Total matched occurrences, summed across every
                         term in every category. Zero exactly when allowed
                         is True for a scanned (not refused-for-other-
                         reasons) input.
    reason              str. Human-readable, built only from category names
                         and counts, never from the matched text or the
                         term list (RULE 3).
    matched_categories  list of str. Category NAMES that matched, de-
                         duplicated, in the order first matched. Empty when
                         allowed is True. Never the matched text.
    """

    __slots__ = ("allowed", "hit_count", "reason", "matched_categories")

    def __init__(self, allowed, hit_count, reason, matched_categories):
        self.allowed = allowed
        self.hit_count = hit_count
        self.reason = reason
        self.matched_categories = list(matched_categories)

    def __repr__(self):
        return (
            "GateResult(allowed=%r, hit_count=%r, reason=%r, "
            "matched_categories=%r)"
            % (self.allowed, self.hit_count, self.reason,
               self.matched_categories)
        )

    def __eq__(self, other):
        if not isinstance(other, GateResult):
            return NotImplemented
        return (
            self.allowed == other.allowed
            and self.hit_count == other.hit_count
            and self.reason == other.reason
            and self.matched_categories == other.matched_categories
        )


class GateRefused(Exception):
    """Raised by guard() when check() refuses. Carries the GateResult that
    caused the refusal as `.result`; str(exc) is exactly `.result.reason`,
    which is built only from category names and counts (RULE 3), never the
    matched text or the term list.
    """

    def __init__(self, result):
        self.result = result
        super(GateRefused, self).__init__(result.reason)


def _normalize_terms(raw):
    """`raw` to a {category: [term, ...]} dict, or None if it is not one.

    Fails closed on any shape problem rather than salvaging part of it: a
    configuration that is half-valid is exactly the kind of ambiguity RULE 4
    exists to resolve toward refusal, not toward "use what parsed."
    """
    if not isinstance(raw, dict):
        return None
    normalized = {}
    for category, terms in raw.items():
        if not isinstance(category, str) or not category.strip():
            return None
        if not isinstance(terms, list) or not terms:
            return None
        cleaned = []
        for term in terms:
            if not isinstance(term, str) or not term:
                return None
            cleaned.append(term)
        normalized[category] = cleaned
    return normalized


def load_terms(path):
    """The {category: [term, ...]} configuration at `path`.

    Returns None when the file is absent, unreadable, not valid JSON, or
    valid JSON in the wrong shape. Returns {} when the file parses to an
    object with no categories at all. The caller (`check`) treats both as
    "unreadable or empty" and refuses either way (RULE 4); they are kept
    distinct here only so a future caller that wants to tell "never
    configured" apart from "configured empty" can.
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return None
    normalized = _normalize_terms(raw)
    if normalized is None:
        return None
    return normalized


def _count_hits(text_lower, term_lower):
    """Occurrences of `term_lower` in `text_lower` whose PRECEDING
    character is not a letter (or which start at position 0).

    This is the whole fix for RULE 2. A plain `str.find` loop would count
    "Vex" inside "convex" as a hit; the `idx == 0 or not
    text_lower[idx - 1].isalpha()` guard below is the one line that refuses
    it, because the character immediately before that match is the letter
    "h". A term glued to a letter or digit AFTER it (a suffix attached to
    the forbidden term) still counts: only the preceding side is guarded,
    deliberately, per the module docstring.
    """
    hits = 0
    start = 0
    while True:
        idx = text_lower.find(term_lower, start)
        if idx == -1:
            break
        if idx == 0 or not text_lower[idx - 1].isalpha():
            hits += 1
        start = idx + 1
    return hits


def _refuse(reason):
    return GateResult(False, 0, reason, [])


def check(content, *, terms=None):
    """Scan `content` against a {category: [term, ...]} mapping and return
    a GateResult. Never raises; an unscannable or forbidden input comes
    back as a refusing GateResult, never an exception (guard() is where a
    refusal becomes an exception, for the caller that wants one).

    `terms`, when given, is used as-is instead of reading DEFAULT_TERMS_PATH,
    and is validated exactly as a loaded file would be. When omitted (or
    None), the terms are loaded from DEFAULT_TERMS_PATH. Either way, a
    missing, malformed, or empty term set refuses (RULE 4): see
    `_normalize_terms` and `load_terms`.
    """
    if terms is None:
        loaded = load_terms(DEFAULT_TERMS_PATH)
    else:
        loaded = _normalize_terms(terms)

    if not loaded:
        return _refuse(
            "term configuration is missing, unreadable, malformed, or "
            "empty; refusing rather than treating that as nothing being "
            "forbidden"
        )

    if isinstance(content, bytes):
        if len(content) > MAX_CONTENT_BYTES:
            return _refuse(
                "content exceeds %d bytes; refusing rather than scanning "
                "an unbounded payload synchronously" % MAX_CONTENT_BYTES
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return _refuse(
                "content bytes are not valid utf-8; refusing rather than "
                "scanning content that cannot be decoded safely"
            )
    elif isinstance(content, str):
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            return _refuse(
                "content exceeds %d bytes; refusing rather than scanning "
                "an unbounded payload synchronously" % MAX_CONTENT_BYTES
            )
        text = content
    else:
        return _refuse(
            "content is neither str nor bytes; refusing rather than "
            "guessing how to scan it"
        )

    text_lower = text.lower()
    hit_count = 0
    matched_categories = []
    for category, category_terms in loaded.items():
        for term in category_terms:
            hits = _count_hits(text_lower, term.lower())
            if hits:
                hit_count += hits
                if category not in matched_categories:
                    matched_categories.append(category)

    if hit_count == 0:
        return GateResult(True, 0, "content is clean", [])

    return GateResult(
        False,
        hit_count,
        "content matched %d forbidden term occurrence(s) in categor%s: %s"
        % (
            hit_count,
            "y" if len(matched_categories) == 1 else "ies",
            ", ".join(matched_categories),
        ),
        matched_categories,
    )


def guard(content, call, *, terms=None):
    """Scan `content`, then invoke the zero-argument callable `call` only
    if the scan allows it, and return `call`'s return value.

    Raises GateRefused (carrying the refusing GateResult as `.result`)
    without ever invoking `call` when the scan refuses. This is the
    function callers use instead of calling `check` and the outside call
    separately: there is no code path in this module, and therefore no way
    for a caller using this function, to invoke `call` before `check` has
    already passed in the same process. See CONTINGENCY in the module
    docstring for what a caller does with a GateRefused.
    """
    result = check(content, terms=terms)
    if not result.allowed:
        raise GateRefused(result)
    return call()
