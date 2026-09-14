#!/usr/bin/env python3
"""WBS-40.03 Normalization Trace: derived-matching-key audit trail.

Generic Japanese-text normalization tool, usable for any entity type
(customer, product, supplier, ...), never entity-specific. "Never mutate raw
truth": the raw value is recorded once, as the trace's own first step, and
every transform after it is a NEW step appended to the chain -- nothing ever
overwrites that first entry.

For each derived matching key this records the full chain:

    raw -> transform 1 -> transform 2 -> ... -> matching key

Each transform step records: transform name, version, before/after (or a
sha256 hash when the value is marked sensitive), whether the step was lossy,
why, and the locale profile it ran under.

Two of the seven required transforms (itaiji / variant-kanji unification,
and small-tsu-ke ヶ/ケ/个 unification) have NO verified, documented mapping
table available right now. Per the brief for WBS-40.03: inventing one would
be actively harmful in a real MDM matching pipeline (a wrong kanji-variant
merge is a false match). Both are real, callable, testable seams that return
the value UNCHANGED with lossy=False and an honest reason, ready for a real
mapping table later -- never a silent no-op with no explanation.

Future address parsing enters through the same Transform seam (register a
new Transform and add it to a chain); this module does not build one.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata

# Katakana-Hiragana Prolonged Sound Mark (chōonpu), the correct target
# codepoint for a Japanese long vowel: https://en.wikipedia.org/wiki/Ch%C5%8Donpu
CHOON = "ー"

# Dash-family codepoints documented as visual lookalikes / common mis-entry
# substitutes for the chōonpu in Japanese text (OCR, IME slips, different
# source systems keying the same sound mark differently). The half-width
# compatibility form U+FF70 is deliberately NOT listed here: NFKC already
# folds it to U+30FC (verified: unicodedata.normalize("NFKC", "ｰ") ==
# "ー"), so by the time this transform runs in the default chain (after
# NFKC) it has nothing left to do for that one.
CHOON_LOOKALIKES = (
    "‐",  # HYPHEN
    "‑",  # NON-BREAKING HYPHEN
    "‒",  # FIGURE DASH
    "\u2013",  # EN DASH
    "\u2014",  # EM DASH
    "―",  # HORIZONTAL BAR
    "−",  # MINUS SIGN
    "－",  # FULLWIDTH HYPHEN-MINUS
    "-",       # ASCII HYPHEN-MINUS (only touched when it directly follows kana, below)
)

# A kana mora is required immediately before a real chōonpu (it prolongs the
# preceding vowel), so restricting the swap to "dash-like char right after a
# kana character" is a real orthographic constraint, not a guess: it is what
# keeps this transform from mangling an ordinary hyphen or dash used
# elsewhere in the string.
_KANA_BEFORE_RE = re.compile(
    r"[぀-ゟ゠-ヿｦ-ﾝ]"  # hiragana, katakana, halfwidth katakana
)

# Common Japanese corporate legal-form strings, both the full form and the
# parenthesized short form, as documented in Japan's Companies Act entity
# types (kabushiki-gaisha, gomei/goshi/godo-gaisha, yugen-gaisha) and common
# registrar abbreviation tables. Both prefix (mae-kabu, e.g. "株式会社テスト")
# and suffix (ato-kabu, e.g. "テスト株式会社") placement are real, common
# Japanese naming conventions, so both are stripped. Ordered longest-first
# so a full form is preferred over an abbreviation when both could match.
CORPORATE_FORMS = tuple(sorted(
    [
        "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
        "一般社団法人", "公益社団法人", "一般財団法人", "公益財団法人",
        "特定非営利活動法人", "社会福祉法人", "医療法人", "学校法人", "宗教法人",
        "NPO法人",
        "(株)", "（株）", "(有)", "（有）", "(同)", "（同）", "(資)", "（資）", "(名)", "（名）",
    ],
    key=len,
    reverse=True,
))

# Separators a phone number is commonly keyed with across source systems.
# Full-width digit/paren-to-half-width conversion is NFKC's job (already
# covered by chain order); this transform only strips separators.
PHONE_SEPARATORS = ("-", "‐", "\u2013", "\u2014", "(", ")", "（", "）", " ", "　")


def _represent(value, sensitive):
    """Plain value, or a safe sha256 hash when the value is sensitive."""
    if not sensitive:
        return value
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class Transform:
    """name + version + apply(value) -> (new_value, lossy, reason)."""

    name = "unnamed"
    version = "0"

    def apply(self, value):
        raise NotImplementedError


class NFKCTransform(Transform):
    name = "nfkc"
    version = "1.0"

    def apply(self, value):
        new_value = unicodedata.normalize("NFKC", value)
        if new_value == value:
            return new_value, False, "already NFKC-normalized, no change"
        return (
            new_value,
            True,
            "NFKC compatibility normalization (stdlib unicodedata): folds "
            "full-width/half-width and compatibility-ideograph variants; "
            "a compatibility decomposition, so it is not reversible",
        )


class SpaceTransform(Transform):
    name = "space"
    version = "1.0"

    def apply(self, value):
        # U+3000 IDEOGRAPHIC SPACE behaves as whitespace here even before
        # NFKC has run (NFKC alone would already fold it to U+0020).
        collapsed = re.sub(r"[\s　]+", " ", value).strip()
        if collapsed == value:
            return collapsed, False, "no whitespace variants to collapse"
        return (
            collapsed,
            True,
            "collapsed whitespace runs (including full-width U+3000) to a "
            "single half-width space and trimmed the ends; exact original "
            "spacing is not recoverable",
        )


class LongVowelTransform(Transform):
    name = "long_vowel"
    version = "1.0"

    def apply(self, value):
        out = []
        changed = False
        for i, ch in enumerate(value):
            if ch in CHOON_LOOKALIKES and i > 0 and _KANA_BEFORE_RE.match(value[i - 1]):
                out.append(CHOON)
                changed = True
            else:
                out.append(ch)
        new_value = "".join(out)
        if not changed:
            return new_value, False, "no dash-like chōonpu substitute found after kana"
        return (
            new_value,
            True,
            "normalized a dash-like character (hyphen/dash/minus family) "
            "immediately following a kana character to U+30FC (chōonpu); "
            "the original character choice is not recoverable",
        )


class ItaijiTransform(Transform):
    """Honest seam: no verified variant-kanji (itaiji) mapping table exists.
    A guessed table would risk false merges in a real MDM pipeline, so this
    passes the value through unchanged rather than inventing one."""

    name = "itaiji"
    version = "0-seam"

    def apply(self, value):
        return value, False, "no mapping table configured yet"


class SmallKeTransform(Transform):
    """Honest seam: no verified ヶ/ケ/个 unification table exists yet.
    Same reasoning as ItaijiTransform: pass through unchanged rather than
    guess which of ヶ/ケ/个/ヵ/カ a given source intended."""

    name = "small_ke"
    version = "0-seam"

    def apply(self, value):
        return value, False, "no mapping table configured yet"


class CorporateFormTransform(Transform):
    name = "corporate_form"
    version = "1.0"

    def apply(self, value):
        stripped = value
        matched = []
        for form in CORPORATE_FORMS:
            if stripped.startswith(form):
                stripped = stripped[len(form):].strip()
                matched.append(("leading", form))
                break
        for form in CORPORATE_FORMS:
            if stripped.endswith(form):
                stripped = stripped[: len(stripped) - len(form)].strip()
                matched.append(("trailing", form))
                break
        if not matched:
            return stripped, False, "no known corporate form found"
        parts = ", ".join("%s '%s'" % (pos, form) for pos, form in matched)
        return (
            stripped,
            True,
            "stripped corporate form(s): %s; the legal-form information is "
            "not recoverable from the result" % parts,
        )


class PhoneTransform(Transform):
    name = "phone"
    version = "1.0"

    def apply(self, value):
        new_value = value
        for sep in PHONE_SEPARATORS:
            new_value = new_value.replace(sep, "")
        if new_value == value:
            return new_value, False, "no phone separators found"
        return (
            new_value,
            True,
            "stripped common phone separators (hyphens, parentheses, "
            "spaces); original grouping is not recoverable",
        )


TRANSFORMS = {
    t.name: t
    for t in (
        NFKCTransform(),
        SpaceTransform(),
        LongVowelTransform(),
        ItaijiTransform(),
        SmallKeTransform(),
        CorporateFormTransform(),
        PhoneTransform(),
    )
}

DEFAULT_CHAIN = (
    "nfkc", "space", "long_vowel", "itaiji", "small_ke", "corporate_form", "phone",
)


def default_chain():
    return [TRANSFORMS[name] for name in DEFAULT_CHAIN]


def trace(raw_value, transforms, locale_profile, sensitive=False):
    """Run raw_value through a NAMED chain of transforms, in order.

    Returns the full trace record. The raw value is recorded once, as step
    0, and is never overwritten by a later step -- every transform appends a
    new step instead of editing a prior one.
    """
    steps = [{
        "step": 0,
        "transform": "raw",
        "version": None,
        "before": None,
        "after": _represent(raw_value, sensitive),
        "lossy": False,
        "reason": "raw input, preserved unchanged as the trace's first entry",
        "locale_profile": locale_profile,
    }]

    current = raw_value
    for i, t in enumerate(transforms, start=1):
        new_value, lossy, reason = t.apply(current)
        steps.append({
            "step": i,
            "transform": t.name,
            "version": t.version,
            "before": _represent(current, sensitive),
            "after": _represent(new_value, sensitive),
            "lossy": lossy,
            "reason": reason,
            "locale_profile": locale_profile,
        })
        current = new_value

    return {
        "raw_value": _represent(raw_value, sensitive),
        "sensitive": sensitive,
        "locale_profile": locale_profile,
        "steps": steps,
        # The real, usable value: matching logic needs the actual string,
        # even when the audit trail above is hashed for a sensitive field.
        "matching_key": current,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("value", help="the raw value to trace")
    parser.add_argument("--locale-profile", default="ja-JP")
    parser.add_argument(
        "--transforms", default=",".join(DEFAULT_CHAIN),
        help="comma-separated transform names to run, in order (default: %(default)s); "
             "available: " + ", ".join(sorted(TRANSFORMS)),
    )
    parser.add_argument(
        "--sensitive", action="store_true",
        help="hash before/after/raw values in the trace instead of showing plaintext",
    )
    args = parser.parse_args(argv)

    names = [n for n in args.transforms.split(",") if n]
    unknown = [n for n in names if n not in TRANSFORMS]
    if unknown:
        print("NO-DATA: unknown transform(s): %s" % ", ".join(unknown))
        return 2
    chosen = [TRANSFORMS[n] for n in names]

    record = trace(args.value, chosen, args.locale_profile, sensitive=args.sensitive)
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
