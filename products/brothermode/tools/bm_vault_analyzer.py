#!/usr/bin/env python3
"""bm_vault_analyzer: the Japanese-first analyzer seam. WBS VB2-03.

THE ROW. The first target estate is Japanese-first, and the 2026-08-30
external adversarial review converges with steering rows F07 to F09 on one
gap: bm_vault.py's lexical signal tokenizes with
``re.findall(r"[A-Za-z0-9_.]{3,}", text)`` (see that module's ``_fts_query``),
which matches nothing outside ASCII. A pure-Japanese query never reaches the
FTS5 index at all today. This module is the seam: normalization, script
folding, and segmentation for CJK text, consulted by bm_vault.py's ``_search``
only when the query itself carries a CJK character, so a pure-ASCII query
takes the exact path it always has (proven byte-identical in
test_bm_vault_analyzer.py).

RESEARCH OF RECORD. docs/plan/research/JAPAN-DATA-STRUCTURES-RESEARCH-2026-08-30.md
(Brother estate, read 2026-08-30 for this row). Two recipes from it, followed
here:

  Section 7 (text normalization for matching): "NFKC also rewrites circled
  numerals and Roman-numeral glyphs, so a blind pass alters meaningful
  characters: scope it, check it." ``normalize()`` below never runs NFKC over
  the whole string; it runs NFKC only over runs of characters already known
  to be safe width variants (the full-width ASCII block and the half-width
  katakana block), so a circled numeral or a kanji character is never
  touched by this function, on purpose, character-range by character-range.

  Section 7 (legal-form tokens): "Legal-form tokens appear as full spelling,
  (kabu) abbreviation, or the single circled glyph, prefix or suffix: strip
  before fuzzy match." ``strip_legal_forms()`` removes the common spellings
  (kabushiki-gaisha, yugen-gaisha, godo-gaisha and their parenthesized and
  circled-glyph abbreviations) so a company name matches regardless of which
  form a query or a note happened to use.

THE HONEST CEILING. This estate is standard library only (see CONTRIBUTING.md
and bm_vault.py's own module docstring): no MeCab, no SudachiPy, no
fugashi, no morphological analyzer of any kind. Real compound decomposition
needs a dictionary-backed morphological engine trained on real corpora; this
module cannot do that and does not claim to. What it does instead, and all
it claims: CHARACTER-CLASS N-GRAM SEGMENTATION over contiguous CJK runs
(sliding character bigrams, the same technique general-purpose CJK search
engines fall back to without a tokenizer), promoted by a DICTIONARY-
LONGEST-MATCH pass first, so a known term (a user's own vocabulary, or a
company name filed in the company dictionary) segments as itself rather than
being sliced into bigrams. UPGRADE PATH: swap ``segment()`` for a call into
SudachiPy or MeCab-python3 when this estate is allowed a compiled dependency;
every caller of ``analyze()`` keeps working unchanged, since the seam is the
token list, not the algorithm that produced it.

FUNCTIONS.
  normalize(text)       Scoped width folding. See "scoped, never blind" above.
  strip_legal_forms(t)  Remove common legal-form spellings, anywhere they
                         appear (ponytail: not position-restricted to prefix
                         or suffix, which is the common real placement; a
                         legal-form string appearing mid-name, which is rare,
                         would also be stripped).
  kana_alias(text)      Fold katakana to hiragana, a MATCH-LANE transform
                         (never for display). analyze() below also generates
                         the reverse fold (hiragana to katakana) internally,
                         via a private table, so a hiragana query token and
                         a katakana note token match EITHER way round; this
                         function itself stays the one named, one-direction
                         call the row's own wording describes. The
                         prolonged-sound mark (U+30FC) has no hiragana
                         counterpart and is left as is.
  segment(text, ...)    Dictionary-longest-match then character-bigram
                         fallback over every contiguous CJK run in text.
  load_dictionary(path) One JSON file (a list of terms, or an object with a
                         "terms" list) to a term list. Missing, unreadable,
                         or malformed is [] : NO-DATA, never a crash.
  load_dictionaries(v)  (user_terms, company_terms) for vault directory v,
                         reading v/99-System/dictionaries/{user,company}-
                         dictionary.json. v falsy or absent files degrade to
                         ([], []).
  has_cjk(text)         True if text carries a Hiragana, Katakana (full or
                         half width), or CJK ideograph character.
  needs_analysis(text)  has_cjk(text) OR text carries a full-width-ASCII or
                         ideographic-space character worth folding.
                         bm_vault.py gates the whole seam on THIS wider
                         check (never has_cjk alone) so a query typed
                         entirely in full-width digits and letters still
                         reaches normalize(), while a genuinely pure-ASCII
                         query still never pays for any of this.
  analyze(text, vault_dir=None)
                         The CJK-side match tokens for text: normalized,
                         legal-form-stripped, segmented, each token's
                         kana-alias fold added when it differs, deduplicated
                         in first-seen order. The ASCII side of a mixed query
                         is NOT reproduced here; bm_vault.py's existing
                         ``_fts_query`` already tokenizes it and keeps doing
                         so unchanged (see that module's docstring point A).

NOT FOLDED, on purpose, by normalize(): kanji of any kind, hiragana or
katakana already in full-width form, circled numerals and ideographs
(U+2460 block, U+3220 block), Roman-numeral glyphs (U+2160 block), and CJK
compatibility ideographs used for legal-form abbreviations (U+3231 etc,
handled instead by strip_legal_forms, a literal-string operation rather than
a Unicode-normalization one).

Python 3.9, standard library only, no network, no subprocess.

No em or en dashes anywhere in this file.
"""
import json
import os
import re
import unicodedata

#: Full-width ASCII block (U+FF01-FF5E), the ideographic space (U+3000), and
#: the half-width katakana block (U+FF61-FF9F, including the half-width
#: voiced/semi-voiced marks, which NFKC composes correctly with the
#: preceding kana when the whole run is normalized together). Anything
#: outside these ranges never reaches unicodedata.normalize from this
#: module: that is the "scoped, never blind" contract above.
_SCOPED_WIDTH_RE = re.compile(r"[！-ﾟ　]+")

#: Hiragana, katakana (full width, includes the prolonged-sound mark and the
#: katakana middle dot), and CJK ideographs (common + compatibility). A
#: contiguous run in this class is "Japanese running text" for segmentation
#: purposes; ASCII, punctuation, and half-width kana are run boundaries.
_CJK_RUN_RE = re.compile(r"[ぁ-ヿ㐀-鿿豈-﫿]+")

#: Same character classes as _CJK_RUN_RE plus half-width katakana, used only
#: to decide IF a string is worth analyzing at all (has_cjk). Half-width
#: kana is included here (a query can arrive before normalize() runs) but
#: not in _CJK_RUN_RE, because segment() always receives already-normalized
#: (hence already width-folded) text.
_CJK_DETECT_RE = re.compile(r"[ぁ-ヿ㐀-鿿豈-﫿｡-ﾟ]")

#: has_cjk's class PLUS the full-width-ASCII range and the ideographic space
#: (_SCOPED_WIDTH_RE's own class, expressed here with explicit \u escapes so
#: no compatibility-ideograph literal risks silent NFC/NFKC folding in transit).
#: A query built entirely of full-width digits and letters (a company code
#: typed full-width, say) carries no Hiragana/Katakana/kanji at all, so
#: has_cjk alone would miss it and the width-variant class of the benchmark
#: would never reach normalize(). needs_analysis() is the wider gate
#: bm_vault.py actually calls.
_ANALYSIS_GATE_RE = re.compile(
    u"[\u3041-\u30FF\u3400-\u9FFF\uF900-\uFAFF\uFF01-\uFF9F\u3000]")

#: A width-folded query can turn back into plain ASCII (full-width
#: "B-014" typed full-width normalizes to plain "B-014"), which has no
#: CJK run for segment() to find at all. analyze() also runs this ASCII
#: extraction over the NORMALIZED text (never the raw one) so a
#: width-variant query still produces a token the LIKE scan can use. Same
#: shape as bm_vault.py's own _fts_query pattern, duplicated deliberately
#: rather than imported: that function also drops stopwords and is tuned
#: for English prose, neither of which applies here.
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}")

#: Katakana (U+30A1-U+30F6) to hiragana (U+3041-U+3096), a fixed offset of
#: 0x60 shared by the whole block. Codepoints outside this map (the
#: prolonged-sound mark U+30FC, the middle dot U+30FB, small katakana with
#: no simple hiragana counterpart past U+30F6) pass through translate()
#: unchanged, which is str.translate's own documented behavior for a
#: codepoint absent from the table.
_KATA_TO_HIRA = {cp: cp - 0x60 for cp in range(0x30A1, 0x30F7)}

#: The inverse of _KATA_TO_HIRA (hiragana U+3041-U+3096 to katakana
#: U+30A1-U+30F6). PRIVATE on purpose: kana_alias() below is the one named,
#: documented direction (VB2-03's own wording is "katakana and hiragana
#: aliasing", and kana_alias's docstring keeps that literal contract).
#: analyze() uses this reverse table too, internally, because bm_vault.py's
#: notes are matched with a raw LIKE substring scan against UNFOLDED note
#: text (see bm_vault.py's _cjk_hits docstring for why): a hiragana query
#: against a katakana note needs the reverse fold exactly as much as a
#: katakana query against a hiragana note needs the forward one, and
#: without both directions the aliasing would only work one way by
#: accident of which script the query happened to be typed in.
_HIRA_TO_KATA = {v: k for k, v in _KATA_TO_HIRA.items()}

#: Common legal-form spellings: full spelling, then the parenthesized and
#: circled-glyph abbreviations, for the three main Japanese company forms
#: (research section 7). Longest strings first so a full spelling is
#: consumed before a shorter one could partially match inside it (none
#: currently nest, but the ordering is cheap insurance).
_LEGAL_FORMS = sorted((
    "株式会社",   # kabushiki-gaisha, full spelling
    "有限会社",   # yugen-gaisha, full spelling
    "合同会社",   # godo-gaisha, full spelling
    "(株)", "（株）", "㈱",     # (kabu) forms + circled glyph
    "(有)", "（有）", "㈲",     # (yuu) forms + circled glyph
    "(同)", "（同）",                 # (dou) forms, no circled glyph minted
), key=len, reverse=True)

#: Where a vault's declared Japanese-analyzer dictionaries live, relative to
#: the vault root. Two files, one per audience (VB2-03 deliverable): a
#: user's own vocabulary, and a company's own product/legal-name vocabulary.
DICT_SUBDIR = os.path.join("99-System", "dictionaries")
USER_DICT_FILENAME = "user-dictionary.json"
COMPANY_DICT_FILENAME = "company-dictionary.json"


def normalize(text):
    """Scoped width folding: NFKC runs only over the full-width-ASCII and
    half-width-katakana ranges (_SCOPED_WIDTH_RE), never over the whole
    string. See the module docstring's "NOT FOLDED" list for what this
    deliberately leaves alone."""
    if not text:
        return ""
    return _SCOPED_WIDTH_RE.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), text)


def strip_legal_forms(text):
    """Remove every known legal-form spelling from text, wherever it
    appears (see the module docstring's ponytail note on prefix/suffix
    scope)."""
    if not text:
        return ""
    out = text
    for token in _LEGAL_FORMS:
        out = out.replace(token, "")
    return out


def kana_alias(text):
    """Fold katakana to hiragana for a MATCH LANE. Never use this for
    display: it is a lossy comparison key, not a rendering."""
    if not text:
        return ""
    return text.translate(_KATA_TO_HIRA)


def has_cjk(text):
    """True when text carries at least one Hiragana, Katakana (full or half
    width), or CJK-ideograph character. bm_vault.py gates its whole CJK
    signal on this, on the RAW query text, before normalize() ever runs."""
    return bool(text) and bool(_CJK_DETECT_RE.search(text))


def needs_analysis(text):
    """True when text carries a CJK script character OR a full-width-ASCII
    / half-width-katakana character worth folding (_ANALYSIS_GATE_RE). This
    is the WIDER gate bm_vault.py actually calls: a query built entirely of
    full-width digits and letters has no CJK script in it at all (has_cjk
    would say False) but still needs normalize() to fold it back to plain
    ASCII before anything can match it."""
    return bool(text) and bool(_ANALYSIS_GATE_RE.search(text))


def segment(text, user_terms=None, company_terms=None):
    """Dictionary-longest-match, then character-bigram fallback, over every
    contiguous CJK run in text (already-normalized text expected; callers
    needing width folding call normalize() first, as analyze() does).

    At each position in a run: try the longest dictionary term (2+
    characters, from user_terms and company_terms combined) that is a
    prefix of the remaining text; take it whole and advance past it.
    Otherwise take a 2-character sliding bigram and advance by ONE
    character (so consecutive bigrams overlap, the standard CJK n-gram
    trick since a real word boundary can fall on either character). A
    single trailing character with no partner emits as its own one-
    character token rather than being dropped.

    ponytail: the dictionary scan is a linear pass over the combined term
    list at every position (O(run_length * dict_size)); fine for a vault-
    sized dictionary, a trie is the upgrade if a dictionary ever grows into
    the thousands of terms."""
    terms = sorted(set((user_terms or []) + (company_terms or [])),
                   key=len, reverse=True)
    long_terms = [t for t in terms if len(t) >= 2]
    tokens = []
    for run in _CJK_RUN_RE.findall(text or ""):
        i, n = 0, len(run)
        while i < n:
            matched = None
            for term in long_terms:
                if run.startswith(term, i):
                    matched = term
                    break
            if matched:
                tokens.append(matched)
                i += len(matched)
            elif n - i >= 2:
                tokens.append(run[i:i + 2])
                i += 1
            else:
                tokens.append(run[i:i + 1])
                i += 1
    return tokens


def load_dictionary(path):
    """The term list at path: a bare JSON list of strings, or a JSON object
    carrying a "terms" list (the shape the vault-template's starter files
    use, so a "_comment" key can sit beside "terms" without breaking this
    reader). [] for a missing file, unreadable file, invalid JSON, or a
    JSON value that is neither shape: honest NO-DATA, never a crash."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if isinstance(data, list):
        terms = data
    elif isinstance(data, dict):
        terms = data.get("terms", [])
    else:
        return []
    if not isinstance(terms, list):
        return []
    return [t for t in terms if isinstance(t, str) and t]


def load_dictionaries(vault_dir):
    """(user_terms, company_terms) read from vault_dir/99-System/
    dictionaries/. ([], []) when vault_dir is falsy: an unconfigured vault
    is NO-DATA for dictionaries, never a guessed path."""
    if not vault_dir:
        return [], []
    base = os.path.join(vault_dir, DICT_SUBDIR)
    user_terms = load_dictionary(os.path.join(base, USER_DICT_FILENAME))
    company_terms = load_dictionary(os.path.join(base, COMPANY_DICT_FILENAME))
    return user_terms, company_terms


def analyze(text, vault_dir=None):
    """The CJK-side match tokens for text (see the module docstring). []
    for empty or pure-ASCII text (has_cjk gates this at the bm_vault.py call
    site already; analyze() itself just returns nothing to segment when
    there is no CJK run to find)."""
    if not text:
        return []
    normalized = normalize(text)
    stripped = strip_legal_forms(normalized)
    user_terms, company_terms = load_dictionaries(vault_dir)
    raw_tokens = segment(stripped, user_terms, company_terms)
    # Width folding can turn a full-width query back into plain ASCII (see
    # _ASCII_TOKEN_RE's own docstring): extract those tokens from the
    # NORMALIZED text too, so a width-variant query still produces
    # something the LIKE scan in bm_vault.py's _cjk_hits can use even when
    # segment() found no CJK run at all.
    ascii_tokens = [t.lower() for t in _ASCII_TOKEN_RE.findall(stripped)]
    seen = set()
    out = []
    for tok in raw_tokens:
        # Both fold directions (see _HIRA_TO_KATA's docstring above for why
        # the reverse fold is generated here even though kana_alias() itself
        # stays the one named, one-directional, katakana-to-hiragana call).
        for variant in (tok, kana_alias(tok), tok.translate(_HIRA_TO_KATA)):
            if variant and variant not in seen:
                seen.add(variant)
                out.append(variant)
    for tok in ascii_tokens:
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _demo():
    """Smallest runnable self-check (ponytail: non-trivial branching logic
    here gets one, per the standing rule), exercised in full by
    test_bm_vault_analyzer.py. Runs on import as __main__ only."""
    assert normalize("ＡＢＣ") == "ABC"          # full-width ASCII folds
    assert normalize("①") == "①"                     # circled numeral untouched
    assert normalize("ｶﾞ") == "ガ"                # half-width ga -> full-width ga
    assert kana_alias("カタカナ") == "かたかな"
    assert strip_legal_forms("トヨタ自動車株式会社") \
        == "トヨタ自動車"
    toks = segment("自動車", user_terms=["自動車"])
    assert toks == ["自動車"], toks
    toks_nodict = segment("自動車")
    assert toks_nodict == ["自動", "動車", "車"], toks_nodict
    assert has_cjk("自動車") is True
    assert has_cjk("hello") is False
    assert needs_analysis("hello") is False
    assert needs_analysis("Ｂ－０１４") is True   # full-width, no CJK script at all
    assert "014" in analyze("Ｂ－０１４")          # width-fold survives into a token
    print("bm_vault_analyzer: self-check OK")


if __name__ == "__main__":
    _demo()
