#!/usr/bin/env python3
"""Pure functions for correction learning: normalization, hashing, atomicity,
scope rules, tokenizing and ranking.

WHY THIS FILE IS SEPARATE FROM bm_store.py
  bm_store.py is the ONLY writer of the database and its job is transactions.
  Everything here is a pure function: same input, same output, no connection, no
  clock, no filesystem. Keeping them apart means the interesting decisions (is
  this rule atomic? does this scope match? which rule ranks first?) can be tested
  exhaustively without a database, and the store stays readable as a store.

WHY THIS FILE IS SEPARATE FROM bm_learn.py
  bm_learn.py is the founder's command line. A CLI that also owns the semantics
  ends up with its rules only reachable by spawning a process.

RETRIEVAL MODE, STATED PLAINLY: this module implements DETERMINISTIC LEXICAL
matching, and that path is complete on its own. Since post-audit LOOP P7 there
is a SECOND, OPTIONAL mode: when the store carries a healthy SQLite FTS5 index
and the founder has opted in, bm_store.py hands this module a map of BM25
scores and ranking gains one extra named component. Everything here stays pure:
this file never opens a database, never probes for FTS5, and never decides
which mode is in force. It only knows how to rank with a BM25 map and how to
rank without one, which is why the fallback is the same code path rather than a
second implementation that drifts.

Python 3.9, standard library only. No network. No subprocess. Writes no files.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import hashlib
import re
import unicodedata

SCOPE_TYPES = ("global", "project", "domain", "artifact", "relationship", "tool")

# Ordered MOST specific first. Retrieval uses the index as the primary sort key,
# so this tuple IS the precedence rule rather than a comment describing one.
SCOPE_SPECIFICITY = ("relationship", "artifact", "tool", "domain", "project", "global")

RULE_STATES = ("approved", "confirmed", "settled", "contradicted",
               "deprecated", "superseded", "forgotten")

# States eligible for automatic injection. Deliberately excludes contradicted
# (an unresolved conflict must not speak), deprecated, superseded and forgotten.
INJECTABLE_STATES = ("settled", "confirmed", "approved")

CANDIDATE_STATUSES = ("pending", "under_review", "approved", "merged", "split",
                      "rejected", "expired")

# The mode name every honest result carries. RETRIEVAL_MODE is the one that
# always works and is therefore the default and the fallback; FTS5_MODE is only
# ever reported when a real index answered the query.
RETRIEVAL_MODE = "lexical"
FTS5_MODE = "fts5"
RETRIEVAL_MODES = (RETRIEVAL_MODE, FTS5_MODE)

_LEGAL_STATE_MOVES = {
    "approved": ("confirmed", "contradicted", "deprecated", "superseded", "forgotten"),
    "confirmed": ("settled", "contradicted", "deprecated", "superseded", "forgotten"),
    "settled": ("contradicted", "deprecated", "superseded", "forgotten"),
    "contradicted": ("approved", "confirmed", "deprecated", "superseded", "forgotten"),
    "deprecated": ("approved", "forgotten"),
    "superseded": ("forgotten",),
    "forgotten": (),
}

_WS = re.compile(r"\s+")


def normalize_text(text):
    """Collapse whitespace and normalize Unicode WITHOUT changing meaning.

    NFC, not NFKD: NFKD would fold typographic characters into ASCII lookalikes
    and quietly rewrite what the founder actually typed. Case is preserved for
    display; callers that want case-insensitive comparison lower the RESULT, so
    the stored text and the comparison key never diverge."""
    if not text:
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()


def content_hash(*parts):
    """Stable fingerprint over normalized, lowercased parts.

    Used to spot an EXACT repeat, never as an identity: two candidates with the
    same hash in different scopes are two different pieces of evidence, which is
    why the schema carries no unique index on this column."""
    joined = " ".join(normalize_text(p).lower() for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# A unit separator, which normalize_text collapses out of any real text, so no
# field value can contain it and no two different field splits can hash the
# same. content_hash joins on a space and LOWERCASES, which is right for
# spotting a repeated correction and wrong here: "never push" and "NEVER PUSH"
# are the same evidence but not the same rule text, and a founder shown one of
# them must not be held to have approved the other.
_FINGERPRINT_SEP = "\x1f"


def approval_fingerprint(parts):
    """What the founder was actually shown, as one stable hash.

    This is the difference between an approval receipt meaning "the founder said
    yes" and meaning "the founder said yes TO THIS". Every field that changes
    what the rule would DO goes in; bookkeeping stays out. Order matters and
    belongs to the caller, so a change in either the values or their number
    produces a different hash and the receipt stops matching.

    Pure: no store, no clock, no randomness. Given the same parts it returns the
    same hash in the minting path and in the approval path, which is the only
    reason comparing the two proves anything."""
    joined = _FINGERPRINT_SEP.join(normalize_text(p or "") for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# A compound correction carries more than one instruction. Splitting it is the
# founder's call, never the parser's, so these only ever RAISE A FLAG.
_COMPOUND_PATTERNS = (
    (re.compile(r"\band always\b", re.I), "contains 'and always', which usually joins two rules"),
    (re.compile(r"\band never\b", re.I), "contains 'and never', which usually joins two rules"),
    (re.compile(r";"), "contains a semicolon, which usually separates two instructions"),
    (re.compile(r"^\s*[-*]\s+.*\n\s*[-*]\s+", re.M), "contains a bulleted list"),
)


def atomicity_problems(action_text):
    """Reasons this action looks like MORE THAN ONE rule. Empty list means it
    looks atomic.

    Advisory at capture and blocking at approval, because a compound rule can
    never be graded: when the outcome is bad you cannot tell which half was
    wrong. The founder can override at approval with a stated reason, and that
    override is recorded as evidence rather than swallowed as a silent bypass."""
    text = normalize_text(action_text)
    if not text:
        return ["action is empty"]
    return [why for pattern, why in _COMPOUND_PATTERNS if pattern.search(text)]


def validate_scope(scope_type, scope_key):
    """Return an error string, or None when the scope is usable.

    Every scope except global REQUIRES a key. A project-scoped rule with no key
    would match everything, which is the contamination that invariant L5 exists
    to prevent, and it would do it while looking correctly scoped."""
    if scope_type not in SCOPE_TYPES:
        return "unknown scope type %r (known: %s)" % (scope_type, ", ".join(SCOPE_TYPES))
    if scope_type != "global" and not normalize_text(scope_key):
        return "scope type %r requires a scope key; only 'global' may omit one" % scope_type
    # LOOP 12. scope_key was the one displayed field that never met
    # safe_display: it was concatenated raw into the rule line, into the why
    # block of the injected RELEVANT FOUNDER RULES section, and into conflict
    # output. An ESC sequence in a scope key cleared the founder's screen and
    # repainted the neighbouring rule lines in the exact block a session is fed.
    # Refused at the door, because a scope key names a project, a tool or a
    # person and has no business carrying control codes.
    if _CONTROL.search(scope_key or ""):
        return ("scope key contains a control character; a scope key names a "
                "project, domain, artifact, relationship or tool and must be "
                "plain text")
    return None


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)

# Deliberately tiny and English-only. A large stop list starts discarding real
# signal in other languages, and this founder works in French as well.
_STOP = frozenset(("the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
                   "is", "are", "be", "it", "this", "that", "with", "when"))


def tokenize(text):
    """Lowercased word tokens, Unicode-aware so accented French text tokenizes
    the same way English does."""
    return [t for t in (m.group(0).lower() for m in _TOKEN.finditer(text or ""))
            if t and t not in _STOP]


def lexical_overlap(query, *fields):
    """Fraction of query tokens present in the rule's text, 0.0 to 1.0.

    Plain overlap rather than a tuned score on purpose: an unexplainable number
    is exactly what invariant L9 forbids. This is the only relevance signal that
    exists today; see the module docstring on FTS5."""
    q = set(tokenize(query))
    if not q:
        return 0.0
    haystack = set()
    for f in fields:
        haystack.update(tokenize(f))
    return len(q & haystack) / float(len(q))


def scope_matches(rule_scope_type, rule_scope_key, context):
    """Is a rule ELIGIBLE for this task context?

    `context` maps scope type to key, e.g. {"project": "SampleApp",
    "artifact": "executive-update"}. Global always matches. Every other scope
    matches only when the context supplies that exact key, compared on the
    normalized lowercase form so "Executive-Update" and "executive-update" are
    the same artifact. A context that does not mention a scope type at all can
    never match a rule of that type: absence is not a wildcard."""
    if rule_scope_type == "global":
        return True
    supplied = (context or {}).get(rule_scope_type)
    if not supplied:
        return False
    return normalize_text(supplied).lower() == normalize_text(rule_scope_key).lower()


def fts_match_query(text):
    """A SAFE FTS5 MATCH expression for this task text, or "" when there is
    nothing to search for.

    Every token is emitted as a QUOTED STRING, joined with OR. That is the
    whole defence against query-syntax attacks and against accidents: FTS5
    treats `NEAR/2`, `*`, `-`, `^`, `AND`, and `column :` as operators in a
    bare query, and a founder task that happens to contain one of them would
    either change the search or raise a syntax error deep inside the store.
    Inside double quotes FTS5 reads a phrase, not an operator, so the worst a
    hostile query can do is fail to match anything.

    Tokens come from tokenize(), the SAME tokenizer the lexical path uses, so
    the two modes are asking about the same words and a comparison between them
    means something. Embedded double quotes are doubled, which is FTS5's own
    escape, so no token can close its quote early."""
    tokens = tokenize(text)
    if not tokens:
        return ""
    return " OR ".join('"%s"' % t.replace('"', '""') for t in tokens)


def bm25_component(raw):
    """Turn SQLite's bm25() output into a component where HIGHER IS BETTER.

    bm25() returns a value that is zero or negative, and MORE negative means a
    better match, which is the opposite of every other number in this ranking
    and exactly the kind of inversion that produces a silently reversed sort.
    Flipping the sign once, here, in a named function with a test on it, is the
    only place that convention has to be remembered. A missing or unusable
    score is 0.0, which is what a rule with no FTS hit scores, so an absent
    score and a worthless score sort identically instead of raising."""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if val != val:  # NaN, which would make the sort order undefined
        return 0.0
    return -val if val < 0 else 0.0


def rank_key(rule, query, context=None, fts_scores=None):
    """Sort key for retrieval. Lower sorts FIRST.

    Lexicographic over NAMED components rather than one blended score, so every
    result can explain its own position (invariant L9). Order:
      1. scope specificity, most specific first;
      2. rule state, settled before confirmed before approved;
      3. FTS5 BM25 score, higher first (always 0.0 in lexical mode);
      4. exact lexical overlap, higher first;
      5. rule_uuid, purely so the order is stable and reproducible.

    Gate delivery is not a component here on purpose: it is structural, applied
    by split_gates() after ranking, so a gate never has to out-rank anything to
    be delivered. See retrieve_learning_rules in bm_store.py.

    `fts_scores` maps rule_uuid to a raw bm25() value. None means lexical mode,
    and then component 3 is 0.0 for every rule, so the key degenerates to
    EXACTLY the order this function produced before FTS5 existed. That is the
    fallback guarantee in one line of code rather than in a second function."""
    spec = SCOPE_SPECIFICITY.index(rule["scope_type"]) \
        if rule["scope_type"] in SCOPE_SPECIFICITY else len(SCOPE_SPECIFICITY)
    state = INJECTABLE_STATES.index(rule["state"]) \
        if rule["state"] in INJECTABLE_STATES else len(INJECTABLE_STATES)
    bm25 = bm25_component((fts_scores or {}).get(rule.get("rule_uuid", "")))
    relevance = lexical_overlap(query, rule.get("trigger_text", ""),
                                rule.get("action_text", ""),
                                rule.get("because_text", ""),
                                rule.get("domain", ""),
                                rule.get("scope_key", ""))
    return (spec, state, -bm25, -relevance, rule.get("rule_uuid", ""))


def explain_rank(rule, query, context=None, fts_scores=None, mode=None):
    """The human-readable reason a rule was selected, built from the SAME
    values rank_key sorts on. Derived from one source so the explanation cannot
    drift from the ordering it claims to describe.

    `mode` is passed in by the store, which is the only layer that knows which
    mode actually answered. It is not guessed from fts_scores being non-empty,
    because a rule with no FTS hit still has to report the mode the run used."""
    matched = sorted(set(tokenize(query)) &
                     set(tokenize("%s %s" % (rule.get("trigger_text", ""),
                                             rule.get("action_text", "")))))
    return {
        "scope": safe_scope(rule["scope_type"], rule.get("scope_key", "")),
        "state": rule["state"],
        "mode": mode or RETRIEVAL_MODE,
        "matched_terms": matched,
        # Kept under its original name and with its original meaning (trigger
        # plus action overlap), because it is written into stored diagnostics
        # and read back by older rows. The FTS component is reported ALONGSIDE
        # it rather than replacing it.
        "relevance": round(lexical_overlap(query, rule.get("trigger_text", ""),
                                           rule.get("action_text", "")), 3),
        "bm25": round(bm25_component((fts_scores or {}).get(
            rule.get("rule_uuid", ""))), 6),
        "lexical_bonus": round(lexical_overlap(
            query, rule.get("trigger_text", ""), rule.get("action_text", ""),
            rule.get("because_text", ""), rule.get("domain", ""),
            rule.get("scope_key", "")), 3),
    }


def state_transition_error(current, target):
    """Error string for an illegal rule state move, or None when it is legal."""
    if current not in _LEGAL_STATE_MOVES:
        return "unknown current state %r" % (current,)
    if target not in RULE_STATES:
        return "unknown target state %r (known: %s)" % (target, ", ".join(RULE_STATES))
    if target not in _LEGAL_STATE_MOVES[current]:
        legal = _LEGAL_STATE_MOVES[current]
        return "cannot move a rule from %r to %r (legal from %r: %s)" % (
            current, target, current, ", ".join(legal) if legal else "nothing")
    return None


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def safe_display(text, limit=200):
    """One-line, control-character-free, length-capped text for CLI and injected
    output.

    Control characters are stripped rather than escaped because the risk is
    structural: a newline or a carriage return inside a rule's action can forge
    what looks like an entire additional rule block in injected context or in a
    list of results. Stripping removes the capability; escaping only makes it
    visible."""
    flat = _WS.sub(" ", _CONTROL.sub("", text or "")).strip()
    if len(flat) <= limit:
        return flat
    return flat[:limit - 3].rstrip() + "..."


# The dash characters the project's copy rule forbids (I7 of
# docs/superpowers/specs/2026-07-30-documentation-and-gate-packs-design.md: no em
# dashes and no en dashes anywhere, INCLUDING generated output), mapped to what a
# rendered page shows instead.
#
# WHY A TABLE RATHER THAN DISCIPLINE. Text from OUTSIDE this repository reaches a
# generated page on every run: a note body is typed by a reviewer, and a quoted
# source line comes from whatever file the note was anchored to. Reproduced
# against a real store and a real project before this existed: a note body
# holding an en dash and an anchored source line holding an em dash both landed
# verbatim in Documentation/30-decisions/INDEX.md,
# Documentation/40-handover/HANDOVER.md and
# Documentation/20-technical/CODE-MAP.md, and the suite stayed green because its
# fixtures were dash free.
#
# WHY A HYPHEN. It is the ASCII stand-in, it keeps the sentence readable, and it
# changes nothing about what the text says. The look-alikes (figure dash,
# horizontal bar, the two long dashes, the vertical and small compatibility
# forms, the fullwidth hyphen) are mapped too, because they read as an em or en
# dash to a human and a rule a reader can see broken is broken.
_DASH_LOOKALIKES = {0x2012: "-", 0x2013: "-", 0x2014: "-", 0x2015: "-",
                    0x2E3A: "-", 0x2E3B: "-", 0xFE31: "-", 0xFE32: "-",
                    0xFE58: "-", 0xFF0D: "-"}


def plain_dashes(text):
    """`text` with every em dash, en dash and dash look-alike replaced by a plain
    ASCII hyphen. Pure.

    DISPLAY ONLY. Nothing here rewrites what the store holds: a note keeps the
    body its author typed and a file keeps its own bytes. This is what a
    generated document or a CLI line shows for them."""
    return (text or "").translate(_DASH_LOOKALIKES)


def safe_scope(scope_type, scope_key, limit=80):
    """The displayable "type:key" scope string, control characters removed.

    validate_scope now refuses a control character in a scope key, so nothing
    new can carry one. This is the containment for rows ALREADY in a store,
    which is the half a validation-only fix would have missed: the escape was
    live on the model-facing retrieval surface, and old rows keep printing."""
    if scope_type == "global":
        return "global"
    return "%s:%s" % (safe_display(scope_type, 40), safe_display(scope_key, limit))


def storage_key(text):
    """A scope key as it must be STORED: control characters removed, whitespace
    collapsed, and NOT length capped. Pure.

    safe_display exists for screens and caps at 200 characters, which is right
    for a line a human reads and wrong for a value something later looks a rule
    up by. validate_scope imposes no length limit on a scope key, so a legal
    250-character project key ran through safe_display came back with an
    ellipsis on the end, no longer matched the rule it named, and the retrieval
    miss it should have exposed disappeared without a word (FIX ROUND P6,
    reproduced on the real CLI). A stored lookup key is either the key or it is
    a different key; there is no shortened version of it that still works."""
    return _WS.sub(" ", _CONTROL.sub("", text or "")).strip()


# The most task terms a retrieval run will store. Generous for real prompts
# (this is post-stopword, deduplicated), and a task that exceeds it stores no
# terms at all rather than a truncated set, because a truncated set re-ranks
# differently from the task that actually ran and would produce confident
# misattribution instead of a refusal.
MAX_QUERY_TERMS = 200


def query_terms(text, max_terms=MAX_QUERY_TERMS):
    """The task's SEARCH TERMS, sorted, deduplicated, order destroyed. Pure.

    What this is for: re-ranking a past retrieval needs whatever the ranker
    read, and the ranker reads exactly `set(tokenize(query))` (lexical_overlap
    is the only query-derived signal in rank_key). So the set is sufficient,
    and the prompt itself is not needed. Word order, punctuation, repetition,
    stopwords and every non-token character are dropped here and never reach
    the store, which is how a retrieval stays measurable without the ledger
    accumulating a verbatim copy of what the founder typed (LOOP P6 acceptance
    criterion: no raw task prompt is stored by default).

    Returns "" when the task has more than `max_terms` distinct terms. Empty
    means the classifier reports `no_task_text` and refuses to decide, which is
    the honest answer for a task whose terms were not kept in full."""
    terms = sorted(set(tokenize(text)))
    if len(terms) > max_terms:
        return ""
    return " ".join(terms)


def task_fingerprint(text):
    """A short, stable, NON-REVERSIBLE handle for a task.

    Applications need to recognise "this same kind of task came back" without
    the store accumulating a copy of every prompt the founder ever typed. A
    hash prefix does that; the full text stays where it already was."""
    return content_hash(text)[:16]


# ---------------------------------------------------------------------------
# Loop 4: correction candidate DETECTION.
#
# What this closes, measured rather than assumed (docs/NOT-FINALIZED.md item
# 17): the shipped filter was one English regex with a hard 400-character cap,
# and of five real founder messages it captured two. A French correction and a
# long correction were dropped SILENTLY. The founder works in French, so an
# English-only filter is not a small gap, it is most of his corrections.
#
# Two rules govern everything below.
#   1. A phrase pack produces CANDIDATES, never rules. A false positive costs
#      one line in a review queue. A false negative costs a correction that the
#      system will never see again, so the packs lean towards capture.
#   2. Length never drops a message. A long correction is EXCERPTED with the
#      omission stated in the row, so a reviewer always knows something was
#      cut. Silence is the defect; truncation with a marker is not.
# ---------------------------------------------------------------------------

# Bounded excerpt budget for a captured correction. Larger than the old 400 so
# a real paragraph survives intact, small enough that the inbox never becomes a
# copy of the founder's transcript.
CORRECTION_EXCERPT_LIMIT = 1200

# (family, label, pattern). The family matters: an interrogative message that
# only trips "negation" is a question, while one that trips "instruction" is a
# standing order phrased as a question. See suppression_reasons.
_PACK_EN = (
    # The shipped regex, preserved verbatim as ONE feature rather than replaced,
    # so nothing that was captured before stops being captured now.
    ("negation", "legacy-en-regex", re.compile(
        r"\b(no[,.]? (that|this|the)|not what i|wrong|you did not|you didn'?t|not even"
        r"|i said|stop doing|never do|always use|from now on|instead of|do better)\b",
        re.I)),
    ("replacement", "asked-for-x-not-y", re.compile(
        r"\bi (?:asked|wanted|said)\b[^.!?]{0,120}\bnot\b", re.I)),
    ("instruction", "memory-instruction", re.compile(
        r"\b(remember (?:that|to)?|from now on|going forward|never (?:again )?"
        r"(?:do|use|write|say)|always (?:do|use|write|say)|please stop)\b", re.I)),
    ("negation", "not-what-i-meant", re.compile(
        r"\b(that'?s not (?:it|right|what)|not (?:like )?that|you missed|you ignored)\b",
        re.I)),
)

_PACK_FR = (
    ("negation", "fr-negation", re.compile(
        r"(ce n'?est pas (?:ce que|ça|cela|bon)|c'?est faux|tu n'?as pas"
        r"|ce n'?est pas ce que je|pas du tout ça)", re.I)),
    ("replacement", "fr-asked-for-x-not-y", re.compile(
        r"(j'?ai (?:demandé|dit)|je t'?ai (?:demandé|dit))[^.!?]{0,120}\bpas\b", re.I)),
    ("instruction", "fr-memory-instruction", re.compile(
        r"(désormais|dorénavant|à partir de maintenant|arrête de|arrete de"
        r"|ne (?:fais|mets|écris|ecris|utilise) (?:plus |jamais)"
        r"|(?:utilise|fais|mets|écris|ecris) toujours"
        r"|il faut toujours|rappelle-toi|souviens-toi|au lieu de)", re.I)),
)

# Japanese has no word boundaries, so every pattern here is a plain substring
# and \b is deliberately absent. Starter phrases only, each one backed by a
# test, per the plan's language section.
_PACK_JA = (
    ("negation", "ja-negation", re.compile(
        r"(違います|違う|そうじゃない|そうではない|間違って(?:います|いる)|ではありません)")),
    ("instruction", "ja-memory-instruction", re.compile(
        r"(今後は|これからは|次からは|必ず|覚えておいて|やめてください|しないでください)")),
)

PHRASE_PACKS = (("en", _PACK_EN), ("fr", _PACK_FR), ("ja", _PACK_JA))

# Structural markers that the message is QUOTED law rather than a correction.
# Deliberately structural, not topical: "from now on, follow SKILL.md" is a real
# founder correction that mentions a document, and a topical filter would eat it.
_QUOTE_LINE = re.compile(r"^\s*>")
_QUOTE_ATTRIBUTION = re.compile(
    r"^\s*(?:the\s+)?(?:skill|law|rule|constitution|spec|SKILL\.md|CLAUDE\.md)\s+"
    r"(?:says|states|reads)\b", re.I)
_FENCE = re.compile(r"```")

_BRAINSTORM = re.compile(
    r"^\s*(what if|maybe we|should we|could we|i wonder|how about"
    r"|et si |peut-?[eê]tre |on pourrait )", re.I)
_BUSINESS_CHANGE = re.compile(
    r"(we (?:have )?(?:decided|changed our mind)|new plan is|the client changed"
    r"|on a chang[ée] d'avis|nouveau plan)", re.I)


def quotation_reasons(text):
    """Reasons this message is QUOTED text rather than the founder correcting.

    Structural only. A subagent pasting the constitution back into a message
    trips these; the founder typing a correction that happens to name a
    document does not."""
    raw = text or ""
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    reasons = []
    if lines and all(_QUOTE_LINE.match(ln) for ln in lines):
        reasons.append("every line is a blockquote")
    if _QUOTE_ATTRIBUTION.match(raw):
        reasons.append("the message opens by attributing the text to a document")
    if len(_FENCE.findall(raw)) >= 2:
        inside = sum(len(part) for part in raw.split("```")[1::2])
        if inside >= 0.6 * len(raw):
            reasons.append("the message is mostly a fenced code block")
    return reasons


def suppression_reasons(text, families):
    """Reasons a phrase hit should NOT become a candidate.

    `families` are the signal families that matched. The interrogative rule is
    the load-bearing one: "why didn't you use the desktop app?" trips the
    negation family and is a question, not a standing preference. The same
    sentence carrying an instruction family ("from now on use the desktop app,
    ok?") is a correction wearing a question mark, and is kept."""
    norm = normalize_text(text)
    reasons = []
    if norm.endswith("?") and "instruction" not in families and "replacement" not in families:
        reasons.append("reads as a question, not a standing instruction")
    if _BRAINSTORM.search(norm):
        reasons.append("reads as brainstorming, not a correction")
    if _BUSINESS_CHANGE.search(norm):
        reasons.append("reads as a changed decision, not a permanent preference")
    return reasons


def bounded_excerpt(text, limit=CORRECTION_EXCERPT_LIMIT):
    """(excerpt, truncated). Long text is CUT WITH A MARKER, never dropped.

    Head and tail are both kept because a correction usually states the problem
    at the start and the instruction at the end, and keeping only the head was
    how the old cap turned a 4,000 character correction into nothing at all."""
    norm = normalize_text(text)
    if len(norm) <= limit:
        return norm, False
    marker = " [... %d characters omitted ...] "
    head = (limit * 2) // 3
    tail = limit - head - len(marker % len(norm))
    if tail < 0:
        tail = 0
    omitted = len(norm) - head - tail
    cut = norm[:head] + (marker % omitted)
    if tail:
        cut += norm[len(norm) - tail:]
    return cut, True


def detect_correction(text, limit=CORRECTION_EXCERPT_LIMIT):
    """None, or a description of why this founder message looks like a
    correction.

    Unicode-safe and language-agnostic at the boundary: nothing here assumes
    ASCII, and every pack is optional. Explicit capture (bm_learn.py capture)
    covers a language no pack knows yet, so a missing pack is a recall gap to
    close and never a wall the founder cannot get past."""
    norm = normalize_text(text)
    if not norm:
        return None
    hits = []
    for lang, pack in PHRASE_PACKS:
        for family, label, pattern in pack:
            if pattern.search(norm):
                hits.append((lang, family, label))
    if not hits:
        return None
    if quotation_reasons(text):
        return None
    families = set(h[1] for h in hits)
    if suppression_reasons(norm, families):
        return None
    excerpt, truncated = bounded_excerpt(norm, limit)
    langs = []
    for lang, _family, _label in hits:
        if lang not in langs:
            langs.append(lang)
    return {
        "lang": langs[0],
        "languages": langs,
        "signals": ["%s:%s" % (lang, label) for lang, _family, label in hits],
        "families": sorted(families),
        "excerpt": excerpt,
        "truncated": truncated,
        "original_length": len(norm),
    }


def inbox_identity(session_id, text):
    """The identity of ONE captured correction inbox row.

    Backfill must be safe to run any number of times, so identity has to be a
    property of the row itself and not of when it was imported. Session plus
    normalized text is exactly what the vault file already deduplicates on, so
    the store and the inbox agree on what "the same row" means."""
    return content_hash("correction-inbox", session_id or "", text or "")


def text_echo_key(text):
    """The key that answers "have I seen these WORDS before, from anywhere".

    Separate from inbox_identity on purpose: identity is session scoped, an
    echo is not. Case and whitespace are folded (content_hash normalizes and
    lowercases), because the founder restating a correction with sentence
    capitalization is the same correction and the reviewer needs to be told so.
    A raw SQL equality on the stored text was the defect this replaces: one
    capital letter defeated it and the reviewer got no signal at all."""
    return content_hash("correction-echo", text or "")


# Why a rejected candidate was not a rule. Descriptive categories over the
# founder's OWN stated reason, never an inference about what he meant: an
# unmatched reason is "other" rather than being forced into a bucket. The
# categories exist so the capture channels can be tuned against real review
# cost; they are counts, not an accuracy measurement.
_FP_CATEGORIES = (
    ("not-a-correction", re.compile(
        r"(?i)\bnot a (?:correction|rule|preference)\b|\bjust (?:a )?(?:question|asking)\b"
        r"|\bbrainstorm|\bthinking out loud\b|\bdiscussion\b")),
    ("one-off", re.compile(
        r"(?i)\bone[- ]off\b|\bjust (?:this|that) once\b|\bonly (?:this|that) time\b"
        r"|\bthis case only\b|\btemporar")),
    ("duplicate", re.compile(r"(?i)\bduplicate\b|\balready (?:have|a rule|captured|covered)\b"
                             r"|\bsame as\b|\bredundant\b")),
    ("wrong-scope", re.compile(r"(?i)\bwrong (?:scope|project|repo)\b|\bother project\b"
                               r"|\bnot global\b|\bproject specific\b")),
    ("superseded", re.compile(r"(?i)\bchanged my mind\b|\bno longer\b|\bsupersed|\boutdated\b"
                              r"|\bout of date\b")),
    ("noise", re.compile(r"(?i)\bnoise\b|\bfalse positive\b|\bgarbage\b|\bmisdetect")),
)

FALSE_POSITIVE_CATEGORIES = tuple(name for name, _pat in _FP_CATEGORIES) + ("other",)


def false_positive_category(reason):
    """Bucket a rejection reason. Always returns one of FALSE_POSITIVE_CATEGORIES.

    First match wins and the order is fixed, so the same reason always lands in
    the same bucket. An empty or unrecognized reason is "other", which is the
    honest answer when the founder's words do not say which kind it was."""
    norm = normalize_text(reason)
    if not norm:
        return "other"
    for name, pat in _FP_CATEGORIES:
        if pat.search(norm):
            return name
    return "other"


# ---------------------------------------------------------------------------
# Loop 6: duplicates, contradictions and supersession.
#
# WHAT THESE FUNCTIONS MAY AND MAY NOT CLAIM
#   Everything here is deterministic and lexical, the same honesty the retrieval
#   ranker holds itself to. There is no embedding, no similarity model, and no
#   attempt to understand what a rule means. So:
#     * a DUPLICATE is detected when two rules say nearly the same words in the
#       same scope, which is the case that actually floods a review queue;
#     * a CONTRADICTION is detected only when two rules in the same scope give
#       OPPOSITE polarity instructions about the same subject ("always push
#       through the desktop app" against "never push through the desktop app").
#       That is the shape a founder's own reversal takes, and it is the one
#       shape a lexical comparison can be trusted on. It is judged on how much
#       of the SHORTER action the longer one also names, so padding one side
#       with an ordinary founder clause does not hide the reversal, and the
#       trigger wording does not have to match for it to count.
#   "Use tabs" against "use spaces" is a real contradiction and this code will
#   NOT find it. That gap is why `link a contradicts b` exists as a founder
#   command: the founder can always declare a conflict the detector cannot see,
#   and a declared conflict counts exactly as much as a detected one everywhere
#   downstream. Nothing here ever RESOLVES a conflict; resolution is a founder
#   act, and these functions only ever describe.
# ---------------------------------------------------------------------------

RELATIONS = ("duplicate_of", "contradicts", "supersedes",
             "derived_from", "supports", "applies_to")

# Polarity markers, English and French. A rule whose action carries one of these
# forbids something; a rule with none of them requires something. Deliberately
# small: a longer list starts flipping the polarity of ordinary prose, and a
# wrongly flipped polarity manufactures a contradiction that blocks an approval.
_NEGATORS = frozenset((
    "never", "not", "no", "dont", "don", "avoid", "stop", "without", "refuse",
    "jamais", "pas", "aucun", "sans", "arrete", "cesse",
))

# The mirror of _NEGATORS: words that assert an instruction rather than negate
# it. They carry no subject of their own, so they are removed alongside the
# negators when two actions are compared. Without this, "always push through the
# desktop app" kept the token "always" while "never push through the desktop
# app" lost "never", the two subjects came out different sizes, and the very
# comparison _content_tokens exists to make was skewed by the polarity words it
# was supposed to cancel out.
_AFFIRMERS = frozenset(("always", "toujours"))

_POLARITY_MARKERS = _NEGATORS | _AFFIRMERS

# Phrases that INTENSIFY an instruction while containing a negation word.
# "always run the tests, no exceptions" is the founder agreeing with himself
# more loudly, not forbidding anything, and reading "no" there flipped the rule
# to 'forbid' and manufactured a contradiction against a rule that plainly
# agreed with it. Stripped before polarity is read, never after.
_INTENSIFIERS = re.compile(
    r"\b(?:no|without)\s+(?:exceptions?|fail)\b"
    r"|\bno\s+matter\s+what\b"
    r"|\bsans\s+(?:exceptions?|faute)\b"
    r"|\baucune\s+exception\b", re.I)

ACTION_RELATIONS = ("identical", "near_duplicate", "incompatible",
                    "narrowing", "not_comparable")

# Named thresholds rather than numbers buried in a condition, because invariant
# L9 says every decision has to be able to explain itself, and "0.85" inside an
# if statement explains nothing.
NEAR_DUPLICATE_FLOOR = 0.85
INCOMPATIBLE_FLOOR = 0.5
NARROWING_FLOOR = 0.5
TRIGGER_OVERLAP_FLOOR = 0.3

# A reversal is judged by how much of the SHORTER action the longer one also
# names, not by symmetric overlap. Symmetric overlap punishes length: padding
# "never push through the desktop app" with an ordinary founder clause dropped
# the score under INCOMPATIBLE_FLOOR and the reversal stopped being seen at all,
# which is the silent accumulation the Loop 6 done gate forbids.
SUBJECT_CONTAINMENT_FLOOR = 0.8

# A one-word subject can be contained in anything, so a single shared token
# would start flagging "never commit" against "always commit the lock file after
# every build", which is a narrowing and not a reversal.
MIN_SUBJECT_TOKENS = 2

CONFLICT_VERDICTS = ("duplicate", "contradiction", "narrowing", "unrelated")


def _without_intensifiers(text):
    """Text with intensifier phrases removed. See _INTENSIFIERS."""
    return _INTENSIFIERS.sub(" ", text or "")


def polarity(text):
    """'forbid' or 'require'. See _NEGATORS on why the marker list is small."""
    toks = set(tokenize(_without_intensifiers(text)))
    return "forbid" if toks & _NEGATORS else "require"


def _content_tokens(text):
    """Tokens with polarity markers removed, so "always use X" and "never use X"
    compare as the SAME subject with opposite polarity rather than as two
    different subjects."""
    return set(tokenize(_without_intensifiers(text))) - _POLARITY_MARKERS


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def subject_containment(a, b):
    """How much of the SHORTER action's subject the longer one also names.

    0.0 to 1.0, and deliberately asymmetric in what it divides by: a founder who
    restates one side of a reversal at greater length is still talking about the
    same thing, and a symmetric score reads that extra length as a different
    subject."""
    ta = _content_tokens(a)
    tb = _content_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(min(len(ta), len(tb)))


def direct_reversal(a, b):
    """Are these two actions the SAME instruction with opposite polarity?

    This is the one shape a lexical comparison can be trusted on, and it is the
    shape the docs promise is caught: "always push through the desktop app"
    against "never push through the desktop app", however either side is padded
    and however differently the two triggers were phrased."""
    if polarity(a) == polarity(b):
        return False
    ta = _content_tokens(a)
    tb = _content_tokens(b)
    if min(len(ta), len(tb)) < MIN_SUBJECT_TOKENS:
        return False
    return subject_containment(a, b) >= SUBJECT_CONTAINMENT_FLOOR


def scope_relation(a_type, a_key, b_type, b_key):
    """How A's scope relates to B's: same, broader, narrower or disjoint.

    Containment between two NON-global scopes is not inferred. This store holds
    no map saying artifact 'executive-update' lives inside project 'SampleApp', so
    claiming one narrows the other would be a guess, and a guess here decides
    whether an approval is blocked. Unknown containment reports 'disjoint',
    which coexists rather than blocks."""
    ak = normalize_text(a_key).lower()
    bk = normalize_text(b_key).lower()
    if a_type == b_type:
        if a_type == "global":
            return "same"
        return "same" if ak == bk else "disjoint"
    if a_type == "global":
        return "broader"
    if b_type == "global":
        return "narrower"
    return "disjoint"


def action_relation(a, b):
    """One of ACTION_RELATIONS, describing two rule actions against each other."""
    na = normalize_text(a).lower()
    nb = normalize_text(b).lower()
    if not na or not nb:
        return "not_comparable"
    if na == nb:
        return "identical"
    ta = _content_tokens(a)
    tb = _content_tokens(b)
    if not ta or not tb:
        return "not_comparable"
    overlap = _jaccard(ta, tb)
    if polarity(a) != polarity(b):
        if overlap >= INCOMPATIBLE_FLOOR or direct_reversal(a, b):
            return "incompatible"
        return "not_comparable"
    if overlap >= NEAR_DUPLICATE_FLOOR:
        return "near_duplicate"
    if (ta < tb or tb < ta) and overlap >= NARROWING_FLOOR:
        return "narrowing"
    return "not_comparable"


def conflict_verdict(a, b):
    """Compare two rule dicts and say, with its working shown, what they are to
    each other.

    Only rules in the SAME scope can reach 'contradiction'. A narrower rule
    beside a broader one is how a founder legitimately says "in general do X,
    but here do Y", and blocking that would make the scope system useless. It
    reports 'narrowing' instead, which is visible and never blocks.

    The trigger-overlap floor is a tie breaker, NOT a veto. Triggers are free
    text and the founder routinely phrases the same situation two ways
    ("pushing to github" and "publishing commits upstream"). When the two
    actions are a direct reversal of each other, that disagreement is real
    whatever words the triggers used, so the floor is bypassed and said to be
    bypassed in the reasons."""
    scope = scope_relation(a["scope_type"], a.get("scope_key", ""),
                           b["scope_type"], b.get("scope_key", ""))
    trig = _jaccard(set(tokenize(a.get("trigger_text", ""))),
                    set(tokenize(b.get("trigger_text", ""))))
    act = action_relation(a.get("action_text", ""), b.get("action_text", ""))
    reversal = direct_reversal(a.get("action_text", ""), b.get("action_text", ""))
    trigger_note = ""
    verdict = "unrelated"
    if scope == "same":
        if act in ("identical", "near_duplicate") and trig >= TRIGGER_OVERLAP_FLOOR:
            verdict = "duplicate"
        elif act == "incompatible" and (trig >= TRIGGER_OVERLAP_FLOOR or reversal):
            verdict = "contradiction"
            if trig < TRIGGER_OVERLAP_FLOOR:
                trigger_note = (" (below the floor and not required: the actions "
                                "are a direct reversal)")
        elif act == "narrowing":
            verdict = "narrowing"
    elif scope in ("broader", "narrower"):
        if act in ("identical", "near_duplicate", "incompatible", "narrowing"):
            verdict = "narrowing"
    return {
        "verdict": verdict,
        "scope_relation": scope,
        "trigger_overlap": round(trig, 3),
        "action_relation": act,
        "direct_reversal": reversal,
        "subject_containment": round(subject_containment(
            a.get("action_text", ""), b.get("action_text", "")), 3),
        "reasons": ["scope %s" % scope,
                    "trigger overlap %.2f%s" % (trig, trigger_note),
                    "actions %s" % act],
    }


def _supersedes_adjacency(edges):
    adj = {}
    for pair in edges:
        adj.setdefault(pair[0], set()).add(pair[1])
    return adj


def supersession_cycle(edges, new_from, new_to):
    """Would adding "new_from supersedes new_to" close a loop?

    A supersession cycle means every rule in it claims to replace another one
    that eventually replaces it, so no version of that instruction is current.
    The state machine alone does not prevent this: an already superseded rule
    can still be named as the SUCCESSOR of a third rule, which is exactly how a
    three-rule loop forms."""
    if new_from == new_to:
        return True
    adj = _supersedes_adjacency(edges)
    adj.setdefault(new_from, set()).add(new_to)
    seen = set()
    stack = [new_to]
    while stack:
        node = stack.pop()
        if node == new_from:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adj.get(node, ()))
    return False


def supersession_cycles(edges):
    """Every rule uuid that sits on a supersedes cycle, sorted.

    The integrity checker needs to find loops that ALREADY exist, for instance
    in a store written by an older build that had no cycle guard, which the
    pre-insert check above cannot tell it."""
    adj = _supersedes_adjacency(edges)
    on_cycle = set()
    for start in list(adj):
        seen = set()
        stack = list(adj.get(start, ()))
        while stack:
            node = stack.pop()
            if node == start:
                on_cycle.add(start)
                break
            if node in seen:
                continue
            seen.add(node)
            stack.extend(adj.get(node, ()))
    return sorted(on_cycle)


# ---------------------------------------------------------------------------
# Loop 7: the application lifecycle, in pure form.
#
# Storing rules answers "what did the founder say". It does not answer "was the
# right rule surfaced, and was it followed". Those are the questions an
# application row exists for, and the three functions below are the only places
# that DECIDE anything about them. They take facts and return a judgement, with
# no clock, no database and no file, so the judgement can be tested directly
# and cannot quietly depend on when it ran.
#
# The rule that governs all of it: a classification is refused when the
# evidence is missing. "not_decidable" is a first-class answer here, not a
# failure of the classifier, because a confident wrong label is worse than an
# honest gap on data the founder will act on.
# ---------------------------------------------------------------------------

DISPOSITIONS = ("followed", "ignored", "not_relevant", "unknown")

APPLICATION_OUTCOMES = ("pending", "accepted", "rework", "escaped_defect",
                        "corrected_again", "not_decidable")

# Outcomes that mean the work had to be done again or reached the founder
# broken. Following a rule into one of these is what makes a rule suspect.
NEGATIVE_OUTCOMES = ("rework", "escaped_defect", "corrected_again")

# retrieval_limit_miss is SEPARATE from retrieval_miss, and the split is the
# point (post-audit LOOP P6). Both mean the rule never reached the acting
# model, but they have different fixes and the founder cannot choose between
# them if they arrive as one number. A relevance miss says the ranking or the
# scope was wrong. A limit miss says the ranking was right and the caller's
# result limit was too small, which is a one-word configuration fix, and
# burying it inside the same count is how a limit set to 1 stays invisible
# forever while the retrieval quality looks bad.
FAILURE_CLASSES = ("retrieval_miss", "retrieval_limit_miss",
                   "compliance_failure", "bad_rule",
                   "scope_error", "not_decidable")

# How much of a past retrieval can be reconstructed from what was stored.
# Named rather than free-form because the whole loop turns on refusing to
# treat a guess as a fact.
RETRIEVAL_EVIDENCE = ("complete", "legacy", "no_task_text")


def retrieval_evidence(retrieval_uuid, task_excerpt):
    """What can be reconstructed about ONE recorded retrieval. Pure.

    Returns (kind, reason). 'complete' means a retrieval-run row exists, so
    the task context, the requested limit and the counts are stored facts.
    'legacy' means the application rows predate the run table: they are real
    evidence that those rules WERE surfaced, and no evidence at all about
    what else was retrievable, because the scope context of the day is gone.
    Reconstructing it from the scope_match values that happen to have been
    recorded is exactly the invented history this build refuses: a task whose
    only recorded rule was global would report an empty context, and every
    project rule it actually missed would be invisible."""
    if not task_excerpt:
        # Checked FIRST, and before the run. A run row proves the context and
        # the limit were kept; it does not conjure text to re-rank, and a task
        # with no text cannot be re-ranked at all.
        return ("no_task_text",
                "no task text was kept for this task, so what else was "
                "retrievable cannot be reconstructed")
    if retrieval_uuid:
        return ("complete", "the retrieval run was recorded, so the task "
                            "context and the requested limit are stored facts")
    return ("legacy",
            "this application predates the retrieval-run record, so the scope "
            "context and the result limit used at the time were never stored; "
            "what else was retrievable is incomplete evidence, not zero misses")

# Rule types where ignoring the rule is a decision the founder is entitled to
# see a reason for. A skipped communication PREFERENCE is a small thing; a
# skipped safety procedure or a skipped decision right is not, and neither is
# anything the founder marked severity 'gate'.
SUBSTANTIAL_RULE_TYPES = ("safety", "procedure", "decision_right")

# The one severity that outranks the caller's result limit. Named here, in the
# pure module, because three separate places have to agree on what a gate is:
# the relevance floor that exempts it, the retrieval split that refuses to let
# a limit cut it, and the CLI that has to say so out loud.
GATE_SEVERITY = "gate"


def is_gate(rule):
    """True when this rule row is a founder gate.

    A mapping, not an object: rows arrive as sqlite3.Row and as plain dicts
    depending on the caller, and both must answer the same way. A row with no
    severity at all is NOT a gate, because inventing one out of missing data
    would be this tool escalating a rule the founder never escalated."""
    try:
        return rule["severity"] == GATE_SEVERITY
    except (KeyError, IndexError, TypeError):
        return False


def split_gates(rules):
    """Partition rules into (gates, soft), preserving the incoming order.

    The order matters: the caller has already ranked, and this must not
    re-rank. It only decides which side of the limit each rule falls on."""
    gates, soft = [], []
    for r in rules:
        (gates if is_gate(r) else soft).append(r)
    return gates, soft


# -----------------------------------------------------------------------
# LOOP 3: mandatory gates delivered whole, without flooding the prompt.
#
# retrieve_learning_rules (bm_store.py) already guarantees every applicable
# gate is RETURNED. It says nothing about how much text that costs: a corpus
# of twenty gates is twenty full trigger/action/because blocks, and a model
# handed twenty mandatory rules at once tends to act on none of them. Two
# layers fix the two failure directions, and neither is allowed to weaken
# the other:
#
#   Layer A (gate_manifest, below): every applicable gate, ALWAYS, in a
#   compact form bounded per entry so the total grows predictably rather
#   than exploding with the corpus. This is the "never hidden" guarantee,
#   moved off full text and onto a form small enough to actually read.
#
#   Layer B (gate_expansion_reason, below): full gate text only when the
#   query hits the gate's trigger, the gate's scope is narrow and matched,
#   the query's wording reaches the gate's own action, or the caller asks
#   for that gate by its short id. bm_store.py additionally caps how many
#   gates this can expand per call (GATE_EXPANSION_CAP), because "bounded
#   per gate" alone does not bound the SUM once a corpus is large enough.
# -----------------------------------------------------------------------

# Per-gate manifest entry is bounded, not the whole manifest: total size then
# grows linearly and predictably with the gate count instead of being capped
# at a number that would have to hide a gate to hold. safe_display leaves
# room for the "..." it appends on truncation.
GATE_MANIFEST_ACTION_CHARS = 100

# How many gates a single retrieval may hand back as FULL text. Independent
# of --limit (which only ever governed soft rules, see retrieve_learning_rules)
# and independent of the corpus size: a store with 200 gates still expands at
# most this many per call. Every gate that does not make the cut is still in
# the compact manifest and can be pulled in full by id, so nothing is hidden,
# only deferred.
GATE_EXPANSION_CAP = 5


def gate_short_id(rule):
    """Stable short id for a gate, for the compact manifest and for
    `--expand`.

    Derived from rule_uuid, which is minted once at approval and never
    reassigned (bm_store.approve_learning_candidate) and never reused (a
    superseded or forgotten rule keeps its own uuid forever; see
    change_learning_rule_state). It is therefore stable across every
    operation that can happen to the CORPUS: approving another gate,
    editing this gate's text (a version bump, not a new uuid), superseding
    or forgetting a different rule. A counter that renumbered on each
    retrieval would not have that property; the same gate could read "G3"
    today and "G2" tomorrow because an unrelated gate ahead of it in
    creation order was forgotten. rule_uuid never moves, so this never
    does either. The "G" prefix plus 8 hex characters mirrors the prefix
    length already used for display everywhere else in this codebase (see
    `_rule_line` and every `rule_uuid[:8]` in bm_learn.py), so a founder
    already reading rule ids elsewhere recognizes the shape."""
    return "G" + (rule.get("rule_uuid") or "")[:8]


def gate_manifest(gates):
    """The Layer A compact manifest over `gates` (already-filtered gate rows,
    any order). Pure: given the same rows it returns the same text and hash
    every time, which is what "deterministic" and "manifest stability" mean
    for this function specifically.

    Deliberately NOT a function of the query or the ranking: every applicable
    gate belongs in this manifest regardless of relevance, so sorting by
    rule_uuid (not rank_key) is what makes the same gate corpus produce a
    byte-identical manifest whether the query was "colour of the orb" or the
    gate's own trigger text. Ranking still governs Layer B (which gates get
    expanded) and the order of `results`; it has no say here.

    Returns a dict: count, hash (over the complete gate SET: every gate's
    short id, uuid, current version and full action text, so adding,
    removing or editing any one gate changes it), entries (one per gate,
    bounded action text), and text (the rendered block)."""
    ordered = sorted(gates, key=lambda r: r.get("rule_uuid") or "")
    entries = []
    fingerprint_parts = []
    for r in ordered:
        gid = gate_short_id(r)
        action = plain_dashes(safe_display(r.get("action_text", ""),
                                           GATE_MANIFEST_ACTION_CHARS))
        entries.append({"id": gid, "rule_uuid": r.get("rule_uuid", ""),
                        "action": action})
        fingerprint_parts.extend([gid, r.get("rule_uuid", ""),
                                  str(r.get("current_version", "")),
                                  r.get("action_text", "") or ""])
    digest = approval_fingerprint(fingerprint_parts)
    lines = ["MANDATORY FOUNDER GATES - %d ACTIVE" % len(entries), ""]
    for e in entries:
        lines.append("%s  %s" % (e["id"], e["action"]))
    lines.append("")
    lines.append("Full gate text available by id (--expand <id>).")
    lines.append("Manifest hash: %s" % digest)
    return {"count": len(entries), "hash": digest, "entries": entries,
            "text": "\n".join(lines)}


def gate_expansion_reason(rule, query, context=None, expand_ids=None,
                          fts_scores=None):
    """Why (if at all) `rule`, a gate, earns FULL text this retrieval, or
    None when it stays manifest-only. Only ever called on rows that already
    passed `is_gate`; a soft rule has no expansion question to answer,
    because a soft rule was never a candidate for the compact form.

    Checked in the order the plan lists the four triggers:
      1. explicit id request: the caller named this exact gate.
      2. narrow, matched scope: this gate is not a global rule, and it is
         already in the eligible set BECAUSE its scope matched the caller's
         context (see retrieve_learning_rules), so scope narrowness alone
         is the signal once eligibility already required the match.
      3. trigger matched: the query's wording overlaps the gate's OWN
         trigger_text (the situation the founder wrote for this rule),
         either lexically or via the FTS index.
      4. action reached: the query's wording overlaps the gate's OWN
         action_text (the do-clause), which is the strongest signal that
         the thing being attempted is literally the thing the gate is
         about, distinct from merely sharing a topic with the trigger.

    Pure and read-only, like rank_key: it decides, it does not write."""
    expand_ids = expand_ids or ()
    ruuid = rule.get("rule_uuid", "")
    if ruuid in expand_ids or gate_short_id(rule) in expand_ids:
        return "requested-by-id"
    if rule.get("scope_type") != "global":
        return "narrow-scope-matched"
    trigger_hit = lexical_overlap(query, rule.get("trigger_text", ""),
                                  "", "", "") > 0
    if fts_scores is not None and ruuid in fts_scores:
        trigger_hit = True
    if trigger_hit:
        return "trigger-matched"
    if lexical_overlap(query, "", rule.get("action_text", ""), "", "") > 0:
        return "action-reached"
    return None


def disposition_needs_reason(disposition, severity, rule_type):
    """True when recording this disposition requires a stated reason.

    Only 'ignored' can require one. Following a rule needs no defence, and
    'not_relevant' is itself the reason (it says the retrieval was wrong, which
    is graded as a scope error rather than as a compliance failure)."""
    if disposition != "ignored":
        return False
    return severity == "gate" or rule_type in SUBSTANTIAL_RULE_TYPES


def classify_application(disposition, shown_to_model, outcome):
    """Grade ONE recorded application. Returns (class_or_None, reason).

    None means nothing failed, which is a different statement from
    'not_decidable' and is kept separate on purpose: a rule that was followed
    into an accepted outcome is a success, while a rule that was followed with
    no outcome recorded yet is simply not gradeable.

    retrieval_miss is not decidable from a single row (a row that exists is by
    definition not a miss), so it is produced by the store, which can compare
    what was recorded against what was retrievable at the time."""
    shown = bool(shown_to_model)
    if disposition not in DISPOSITIONS:
        return ("not_decidable", "disposition %r is not one this build knows"
                % (disposition,))
    if outcome not in APPLICATION_OUTCOMES:
        return ("not_decidable", "outcome %r is not one this build knows"
                % (outcome,))
    if disposition == "unknown":
        return ("not_decidable",
                "no disposition was recorded at task close, so whether the rule "
                "was followed is unknown rather than negative")
    if disposition == "ignored":
        if not shown:
            return ("not_decidable",
                    "recorded as ignored but never shown to the acting model, so "
                    "there was nothing to comply with")
        return ("compliance_failure",
                "the rule was retrieved, shown and ignored")
    if disposition == "not_relevant":
        return ("scope_error",
                "the rule was retrieved for a task it did not apply to, so its "
                "scope is wider than the situation it belongs to")
    if outcome in NEGATIVE_OUTCOMES:
        return ("bad_rule",
                "the rule was followed and the work still ended in %s" % outcome)
    if outcome == "accepted":
        return (None, "followed, and the outcome was accepted")
    return ("not_decidable",
            "followed, but no outcome has been recorded against it yet")


# The task signals that make retrieval proportionate. Named rather than
# free-form so a caller cannot invent a signal that nothing grades.
RETRIEVAL_SIGNALS = ("communication_artifact", "architecture_decision",
                     "multi_file_change", "risky_operation", "prior_correction")

_SIGNAL_REASONS = {
    "communication_artifact": "the founder will read or reuse the artifact",
    "architecture_decision": "it is an architecture or design decision",
    "multi_file_change": "it changes more than one file",
    "risky_operation": "it is a risky or irreversible operation",
    "prior_correction": "a correction has landed in this area before",
}


def retrieval_advised(signals=None, gate_rules_exist=False):
    """Should this task retrieve founder rules before it starts?

    The proportionality rule, made mechanical: retrieval on a one-line obvious
    edit is ceremony, and ceremony is what makes a founder stop reading. So the
    default is NO, and a named signal turns it on. The single exception is a
    live gate rule, because a safety gate is exactly the thing that must appear
    when the person did not think to ask for it.

    Returns a decision the caller can print. It records NOTHING: deciding not
    to retrieve must never manufacture an application row, or the outcome data
    fills up with tasks that never happened."""
    signals = signals or {}
    unknown = sorted(k for k in signals if k not in RETRIEVAL_SIGNALS)
    fired = [s for s in RETRIEVAL_SIGNALS if signals.get(s)]
    reasons = [_SIGNAL_REASONS[s] for s in fired]
    if fired:
        return {"advised": True, "signals": fired, "reasons": reasons,
                "unknown_signals": unknown}
    if gate_rules_exist:
        return {"advised": True, "signals": [],
                "reasons": ["a founder gate rule is live, and a gate applies "
                            "even to a small task"],
                "unknown_signals": unknown}
    return {"advised": False, "signals": [],
            "reasons": ["no substantial signal fired, so retrieval here would "
                        "be ceremony rather than care"],
            "unknown_signals": unknown}


# ---------------------------------------------------------------------------
# Loop 8: grading a correction that came back.
#
# The whole program's claim is that BrotherMode learns. The only way to tell
# learning from storage growth is to look at what happened AFTER a rule
# existed: work redone, a defect that reached the founder, or the same
# correction said twice. Those three are external evidence. The session being
# graded cannot write any of them about itself in a way that flatters it,
# because each one is anchored to a work record, an artifact, or the founder's
# own words.
#
# These functions decide, and nothing else here does. No clock, no database,
# no file, so the judgement is testable on its own and cannot drift with when
# it ran.
# ---------------------------------------------------------------------------

# What each miss class says about the RULE itself, and therefore which way the
# evidence points.
#
# NOTHING here is 'support', and that is a correction of an earlier reading of
# this table. A rule that was followed and the correction came back anyway is
# evidence AGAINST the rule, and so is one retrieved into work it was then
# judged irrelevant to. Every other class (never retrieved, retrieved and
# skipped, nothing decidable) says something about the machinery around the
# rule and NOTHING about whether the rule works, so it is neutral.
#
# Why this matters far beyond a label: the promotion gate in
# bm_store.change_learning_rule_state lets a rule reach 'confirmed' on one
# supporting evidence row that is not the founder's own approval, and its
# stated meaning is "an independent event showing the rule worked". Calling a
# compliance failure 'support' handed that gate a row saying the rule was
# shown and skipped, so a rule could be promoted on proof it was never once
# followed. The founder re-stating a preference IS support for the rule, and
# that row is written where it belongs, by merge_learning_candidate, off his
# own words rather than off a loop failure.
REPEATED_CORRECTION_POLARITY = {
    "retrieval_miss": "neutral",
    # Same neutrality and the same reason: a rule the result limit cut is not
    # shown to be wrong and is not shown to work either.
    "retrieval_limit_miss": "neutral",
    "compliance_failure": "neutral",
    "bad_rule": "contradict",
    "scope_error": "contradict",
    "not_decidable": "neutral",
}


def classify_repeated_correction(applications, links_known):
    """Why did a rule that already existed fail to prevent this correction?

    Returns (class, reason, polarity). `applications` are the recorded
    applications of that rule in the work the correction is about, each a dict
    carrying 'disposition' and 'shown_to_model'. `links_known` says whether
    the correction names a session or a work record at all.

    The ordering is deliberate and is the point of the whole function:
    'followed' outranks everything, because a rule that was obeyed and did not
    work is the only case where the RULE is the problem. Getting that order
    wrong would blame the rule for every failure to retrieve it, and the
    founder would end up editing text that was already correct."""
    if not links_known:
        return ("not_decidable",
                "this correction names neither a session nor a work record, so "
                "what was retrieved when it happened cannot be established",
                REPEATED_CORRECTION_POLARITY["not_decidable"])
    rows = list(applications or [])
    if not rows:
        return ("retrieval_miss",
                "the rule was live when this work ran and no application of it "
                "was recorded, so it never reached the acting model",
                REPEATED_CORRECTION_POLARITY["retrieval_miss"])
    followed = [r for r in rows if r.get("disposition") == "followed"]
    ignored = [r for r in rows if r.get("disposition") == "ignored"
               and bool(r.get("shown_to_model"))]
    irrelevant = [r for r in rows if r.get("disposition") == "not_relevant"]
    if followed:
        return ("bad_rule",
                "the rule was followed in %d recorded application(s) and the "
                "work went wrong anyway, so the rule as written did not "
                "prevent it" % len(followed),
                REPEATED_CORRECTION_POLARITY["bad_rule"])
    if ignored:
        return ("compliance_failure",
                "the rule was shown in %d recorded application(s) and skipped, "
                "and the work it applied to went wrong" % len(ignored),
                REPEATED_CORRECTION_POLARITY["compliance_failure"])
    if irrelevant:
        return ("scope_error",
                "the rule was retrieved for this work and marked not relevant, "
                "yet the failure it covers happened here, so its scope does "
                "not match where it is actually needed",
                REPEATED_CORRECTION_POLARITY["scope_error"])
    return ("not_decidable",
            "%d application(s) exist but none carries a usable disposition, so "
            "whether the rule was followed cannot be read from what was "
            "recorded" % len(rows),
            REPEATED_CORRECTION_POLARITY["not_decidable"])


# Units a review window may be written in. Months and years are deliberately
# absent: they are not fixed numbers of days, and a window that silently means
# something different in February is a reporting bug waiting to happen.
WINDOW_UNITS = (("d", 1.0), ("w", 7.0), ("h", 1.0 / 24.0))


def parse_window_days(text):
    """A window like '30d', '2w' or '48h' into days. Returns (days, error).

    A bare number means days. Anything else returns (None, message) rather
    than a default, because a window silently reinterpreted is a report about
    a different period than the one asked for."""
    raw = normalize_text(text or "")
    if not raw:
        return (None, "no window given")
    body, factor = raw, 1.0
    for suffix, mult in WINDOW_UNITS:
        if raw.lower().endswith(suffix):
            body, factor = raw[:-1], mult
            break
    try:
        value = float(body)
    except ValueError:
        return (None, "cannot read %r as a window; use a number of days or a "
                      "suffix from %s (for example 30d)"
                      % (text, ", ".join(s for s, _ in WINDOW_UNITS)))
    if value <= 0:
        return (None, "a window must be positive, got %r" % (text,))
    return (value * factor, "")


# ---------------------------------------------------------------------------
# Where an anchored line is NOW (phase C of the 2026-07-30 documentation and
# gate-pack spec, section 6: "a note anchored to a line that has since moved is
# reported rather than silently dropped").
#
# Two pure functions and one vocabulary. The caller reads the file, because
# this module opens nothing; everything below is decidable from a list of lines,
# a line number, and the fingerprint the store recorded when the note was
# written.
#
# WHY A FINGERPRINT AT ALL. Without one, a moved line is undetectable: line 5
# of a file that still has 200 lines resolves to whatever is at line 5 now, and
# a reviewer reads a note about code that has moved somewhere else as though it
# were about the code in front of them. Reproduced against a real store before
# this existed: a note anchored at api/pay.py:99 in a six line file was stored,
# listed and rendered with no complaint from any command.
# ---------------------------------------------------------------------------

# Ordered worst-news last, because a report sorts by it and a reader should
# reach the anchors that no longer exist without scrolling.
ANCHOR_STATES = ("resolves", "unverifiable", "moved", "gone", "file-missing")

# The states a reader has to act on. Named here rather than re-derived at each
# render site, so a document, a pack and the CLI cannot disagree about what
# counts as a problem.
ANCHOR_PROBLEM_STATES = ("moved", "gone", "file-missing")

# The state that is NEITHER a problem nor a check: no fingerprint was recorded
# when the note was written, so nothing is known to be wrong AND nothing was
# verified. Named here for the same reason the two sets above are: a report that
# counts one of these as "checked against the files on disk" tells a reader the
# anchor was verified when it provably cannot be, and every note carried over by
# the schema 7 to 8 migration is in this state by design.
ANCHOR_UNCHECKABLE_STATE = "unverifiable"


def anchor_line_digest(text):
    """The fingerprint of one anchored source line, or '' when there is nothing
    worth fingerprinting.

    Leading and trailing whitespace is stripped before hashing, so
    re-indentation is not reported as a move: the line is the same line, and a
    report that cried move on every reformat would be ignored within a week.

    A blank line fingerprints as '', which the resolver reports as
    unverifiable rather than as a match: the digest of an empty string would
    match every other blank line in the file, so the honest answer is that this
    anchor cannot be tracked, said out loud."""
    norm = (text or "").strip()
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def resolve_anchor_line(lines, line, digest):
    """Where the anchored line is now. Pure; the caller does the reading.

    `lines` is the file's lines with no trailing newlines, or None when the file
    could not be read at all. `line` is the 1-based number the note was written
    against. `digest` is what anchor_line_digest returned then.

    Returns a dict carrying state, the line the note claims, the line it is on
    now when that can be established, the text found there, every candidate when
    a file repeats the same line, and one sentence saying what a reader should
    make of it. Never raises, and never returns None: a note whose anchor cannot
    be checked is reported as uncheckable, which is the whole point."""
    claimed = int(line) if line else 0
    out = {"state": "resolves", "line": claimed, "now_line": claimed,
           "text": "", "candidates": [], "why": ""}
    if lines is None:
        out["state"] = "file-missing"
        out["now_line"] = None
        out["why"] = ("the file this note is anchored to could not be read, so "
                      "the note is reported here instead of being rendered "
                      "beside code")
        return out
    total = len(lines)
    in_range = 1 <= claimed <= total
    if in_range:
        out["text"] = lines[claimed - 1]
    if not digest:
        out["state"] = "unverifiable"
        out["why"] = ("no fingerprint was recorded for this anchor (it predates "
                      "the fingerprint, or the anchored line was blank), so a "
                      "move cannot be detected and the line shown is whatever "
                      "is there now")
        if not in_range:
            out["state"] = "gone"
            out["now_line"] = None
            out["why"] = ("line %d is past the end of the file, which now has "
                          "%d line(s), and no fingerprint was recorded to find "
                          "it by" % (claimed, total))
        return out
    if in_range and anchor_line_digest(lines[claimed - 1]) == digest:
        out["why"] = "the anchored line is still line %d" % claimed
        return out
    matches = [i + 1 for i, text in enumerate(lines)
               if anchor_line_digest(text) == digest]
    if not matches:
        out["state"] = "gone"
        out["now_line"] = None
        out["candidates"] = []
        out["why"] = ("the line this note was written about is no longer in the "
                      "file (it was line %d; the file now has %d line(s)). The "
                      "note is kept and reported: nothing deletes it."
                      % (claimed, total))
        return out
    # Nearest first, so a file that repeats one line puts the likeliest new
    # home of the note in front of the reader instead of the first one found.
    matches.sort(key=lambda n: (abs(n - claimed), n))
    out["state"] = "moved"
    out["now_line"] = matches[0]
    out["candidates"] = matches
    out["text"] = lines[matches[0] - 1]
    if len(matches) == 1:
        out["why"] = ("the anchored line moved from line %d to line %d"
                      % (claimed, matches[0]))
    else:
        out["why"] = ("the anchored line moved from line %d; %d line(s) in the "
                      "file match its fingerprint (%s), nearest first"
                      % (claimed, len(matches),
                         ", ".join(str(n) for n in matches[:8])))
    return out
