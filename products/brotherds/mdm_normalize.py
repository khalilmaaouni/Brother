"""Deterministic match-key normalizers for master data.

This module is part of an open-source data quality tool.
It uses only the Python 3.9 standard library.
"""

import re
import sys
import unicodedata

REQUIRED_STEPS = {
    "ja": ["nfkc", "space", "long_vowel", "itaiji", "small_ke", "corporate"],
    "latin": ["nfkc", "space", "fold", "corporate"],
}

def _require_str(value, func_name):
    if not isinstance(value, str):
        raise ValueError(func_name + ": text must be str, got " + type(value).__name__)

def nfkc(s):
    _require_str(s, "nfkc")
    return unicodedata.normalize("NFKC", s)

def normalize_space(s):
    _require_str(s, "normalize_space")
    return " ".join(s.split())

def is_katakana(ch):
    code = ord(ch)
    return (
        (0x30A1 <= code <= 0x30FA)
        or code == 0x30FC
        or (0x30FD <= code <= 0x30FE)
        or (0x31F0 <= code <= 0x31FF)
        or (0xFF66 <= code <= 0xFF9D)
    )

_LONG_VOWEL_VARIANTS = {
    "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015",
    "\u2212", "\uFF0D", "\u002D", "\uFF70",
}

def normalize_long_vowel(s):
    _require_str(s, "normalize_long_vowel")
    chars = list(s)
    for i, ch in enumerate(chars):
        if ch in _LONG_VOWEL_VARIANTS:
            if i > 0 and is_katakana(chars[i - 1]):
                chars[i] = "\u30FC"
    return "".join(chars)

_ITAIJI_MAP = {
    0x9AD9: 0x9AD8,
    0xFA11: 0x5D0E,
    0x9F4B: 0x658E,
    0x9F4A: 0x658E,
    0x908A: 0x8FBA,
    0x9089: 0x8FBA,
    0x6FA4: 0x6CA2,
    0x5EE3: 0x5E83,
    0x570B: 0x56FD,
}

def normalize_itaiji(s):
    _require_str(s, "normalize_itaiji")
    return s.translate(_ITAIJI_MAP)

def is_kanji(ch):
    code = ord(ch)
    return (
        (0x4E00 <= code <= 0x9FFF)
        or (0x3400 <= code <= 0x4DBF)
        or (0xF900 <= code <= 0xFAFF)
        or (0x20000 <= code <= 0x2A6DF)
        or (0x2A700 <= code <= 0x2B73F)
        or (0x2B740 <= code <= 0x2B81F)
        or (0x2B820 <= code <= 0x2CEAF)
        or (0x2F800 <= code <= 0x2FA1F)
    )

def normalize_small_ke(s):
    _require_str(s, "normalize_small_ke")
    chars = list(s)
    for i, ch in enumerate(chars):
        if ch in ("\u30F6", "\u30B1", "\u30F5"):
            if i > 0 and i + 1 < len(chars):
                if is_kanji(chars[i - 1]) and is_kanji(chars[i + 1]):
                    chars[i] = "\u30B1"
    return "".join(chars)

def _strip_literal_forms(t, forms, case_sensitive=True, separate_word=False, allow_punct=False):
    flags = 0 if case_sensitive else re.IGNORECASE
    changed = True
    while changed:
        changed = False
        for form in forms:
            pat = re.escape(form)
            if separate_word:
                if allow_punct:
                    end_pat = r"(?<!\w)" + pat + r"\.?[.,]*\s*$"
                    start_pat = r"^\s*" + pat + r"\.?[.,]*(?!\w)"
                else:
                    end_pat = r"(?<!\w)" + pat + r"\s*$"
                    start_pat = r"^\s*" + pat + r"(?!\w)"
            else:
                if allow_punct:
                    end_pat = pat + r"\.?[.,]*\s*$"
                    start_pat = r"^\s*" + pat + r"\.?[.,]*"
                else:
                    end_pat = pat + r"\s*$"
                    start_pat = r"^\s*" + pat
            m = re.search(end_pat, t, flags)
            if m:
                t = t[:m.start()].strip()
                changed = True
                break
            m = re.search(start_pat, t, flags)
            if m:
                t = t[m.end():].strip()
                changed = True
                break
    return t

def _strip_regex_forms(t, patterns, flags=0):
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            end_re = re.compile(r"(?<!\w)(" + pattern + r")\.?[.,]*\s*$", flags)
            start_re = re.compile(r"^\s*(" + pattern + r")\.?[.,]*(?!\w)", flags)
            m = end_re.search(t)
            if m:
                t = t[:m.start()].strip()
                changed = True
                break
            m = start_re.search(t)
            if m:
                t = t[m.end():].strip()
                changed = True
                break
    return t

def strip_corporate(s, locale):
    _require_str(s, "strip_corporate")
    if locale not in ("ja", "latin"):
        raise ValueError("unknown locale: " + str(locale))
    t = s.strip()
    if locale == "ja":
        exact_forms = [
            "\u682A\u5F0F\u4F1A\u793E",
            "\u6709\u9650\u4F1A\u793E",
            "\u5408\u540C\u4F1A\u793E",
            "\u5408\u8CC7\u4F1A\u793E",
            "\u5408\u540D\u4F1A\u793E",
            "\u30AB\u30D6\u30B7\u30AD\u30AC\u30A4\u30B7\u30E3",
            "(\u682A)",
            "(\u6709)",
            "(\u540C)",
            "\uFF08\u682A\uFF09",
            "\uFF08\u6709\uFF09",
            "\uFF08\u540C\uFF09",
            "\u3231",
            "\u3232",
            "\u3239",
        ]
        t = _strip_literal_forms(t, exact_forms, case_sensitive=True, separate_word=False, allow_punct=False)
        roman_patterns = [
            r"K\.\s*K\.",
            r"KK",
            r"Co\.\s*,\s*Ltd\.?",
            r"Co\.\s*,\s*Ltd",
            r"Ltd\.?",
            r"Inc\.?",
        ]
        t = _strip_regex_forms(t, roman_patterns, flags=re.IGNORECASE)
        return t
    else:
        forms = [
            "GmbH", "AG", "SA", "SAS", "SARL", "S.p.A.", "S.p.A", "SpA", "BV", "B.V.", "NV",
            "Ltd", "Limited", "LLC", "Inc", "Incorporated", "Corp", "Corporation", "PLC", "Co",
        ]
        forms.sort(key=len, reverse=True)
        t = _strip_literal_forms(t, forms, case_sensitive=False, separate_word=True, allow_punct=True)
        return t

def fold_latin(s):
    _require_str(s, "fold_latin")
    t = unicodedata.normalize("NFKD", s)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return t.casefold()

_COMPANY_REMOVE = set(" .,\u30FB\uFF65()\uFF08\uFF09")

def company_key(s, locale):
    _require_str(s, "company_key")
    if locale not in ("ja", "latin"):
        raise ValueError("unknown locale: " + str(locale))
    t = nfkc(s)
    t = normalize_space(t)
    if locale == "ja":
        t = normalize_long_vowel(t)
        t = normalize_itaiji(t)
        t = normalize_small_ke(t)
    t = strip_corporate(t, locale)
    t = fold_latin(t)
    t = "".join(ch for ch in t if not ch.isspace() and ch not in _COMPANY_REMOVE)
    return t

_PERSON_REMOVE = set(" \u30FB\uFF65")

def person_key(s, locale):
    _require_str(s, "person_key")
    if locale not in ("ja", "latin"):
        raise ValueError("unknown locale: " + str(locale))
    t = nfkc(s)
    t = normalize_space(t)
    if locale == "ja":
        t = normalize_itaiji(t)
        t = normalize_long_vowel(t)
    t = fold_latin(t)
    t = "".join(ch for ch in t if not ch.isspace() and ch not in _PERSON_REMOVE)
    return t

def phone_key(s):
    _require_str(s, "phone_key")
    t = nfkc(s)
    stripped = t.lstrip()
    if stripped.startswith("+81"):
        t = "0" + stripped[3:]
    return "".join(ch for ch in t if ch in "0123456789")

def probe_pairs(pairs, kind, locale):
    if kind not in ("company", "person", "phone"):
        raise ValueError("unknown kind: " + str(kind))
    if locale not in ("ja", "latin"):
        raise ValueError("unknown locale: " + str(locale))
    n = len(pairs)
    equal = 0
    unequal = []
    for pair in pairs:
        a = pair[0]
        b = pair[1]
        if kind == "company":
            ka = company_key(a, locale)
            kb = company_key(b, locale)
        elif kind == "person":
            ka = person_key(a, locale)
            kb = person_key(b, locale)
        else:
            ka = phone_key(a)
            kb = phone_key(b)
        if ka == kb:
            equal += 1
        else:
            if len(unequal) < 10:
                unequal.append([a, b])
    rate = (equal / n) if n else 0.0
    return {"n": n, "equal": equal, "unequal": unequal, "rate": rate}

def _selftest():
    failures = []
    def check(cond, msg):
        if not cond:
            failures.append(msg)

    check(company_key("\u682A\u5F0F\u4F1A\u793E\uFF71\uFF72\uFF73\u5546\u4E8B", "ja") == company_key("(\u682A)\u30A2\u30A4\u30A6\u5546\u4E8B", "ja"), "company ja parentheses")
    check(company_key("\uFF21\uFF22\uFF23\u3000\u30DB\u30FC\u30EB\u30C7\u30A3\u30F3\u30B0\u30B9\u3231", "ja") == company_key("ABC\u30DB\u30FC\u30EB\u30C7\u30A3\u30F3\u30B0\u30B9\u682A\u5F0F\u4F1A\u793E", "ja"), "company ja nfkc")
    check(person_key("\u5C71\uFA11 \u82B1\u5B50", "ja") == person_key("\u5C71\u5D0E\u82B1\u5B50", "ja"), "person itaiji 1")
    check(person_key("\u9F4B\u85E4", "ja") == person_key("\u658E\u85E4", "ja"), "person itaiji 2")
    check(company_key("\u30B3\uFF0D\u30D2\uFF0D\u5DE5\u623F", "ja") == company_key("\u30B3\u30FC\u30D2\u30FC\u5DE5\u623F", "ja"), "company long vowel")
    check(company_key("\u971E\u30F6\u95A2\u5546\u5E97", "ja") == company_key("\u971E\u30B1\u95A2\u5546\u5E97", "ja"), "company small ke")
    check(phone_key("\uFF10\uFF13\uFF0D\uFF11\uFF12\uFF13\uFF14\uFF0D\uFF15\uFF16\uFF17\uFF18") == "0312345678", "phone full width")
    check(phone_key("03-1234-5678") == "0312345678", "phone ascii")
    check(phone_key("+81 3 1234 5678") == "0312345678", "phone plus 81")
    check(company_key("M\u00FCller GmbH", "latin") == company_key("MULLER gmbh", "latin"), "company latin fold")
    check(normalize_long_vowel("03-1234") == "03-1234", "long vowel untouched")

    check(nfkc("\uFF08\u682A\uFF09") == "(\u682A)", "nfkc parentheses")
    check(normalize_space("a\u3000b\n c") == "a b c", "normalize space")
    check(normalize_long_vowel("\u30B3\uFF0D\u30D2\uFF0D") == "\u30B3\u30FC\u30D2\u30FC", "long vowel trailing")
    check(normalize_long_vowel("-\u30B3") == "-\u30B3", "long vowel leading")
    check(normalize_itaiji("\u9AD9") == "\u9AD8", "itaiji takai")
    check(normalize_itaiji("\uFA11") == "\u5D0E", "itaiji saki")
    check(normalize_itaiji("\u9F4B") == "\u658E", "itaiji saito 1")
    check(normalize_itaiji("\u9F4A") == "\u658E", "itaiji saito 2")
    check(normalize_itaiji("\u908A") == "\u8FBA", "itaiji hen 1")
    check(normalize_itaiji("\u9089") == "\u8FBA", "itaiji hen 2")
    check(normalize_itaiji("\u6FA4") == "\u6CA2", "itaiji sawa")
    check(normalize_itaiji("\u5EE3") == "\u5E83", "itaiji hiro")
    check(normalize_itaiji("\u570B") == "\u56FD", "itaiji kuni")
    check(normalize_small_ke("\u971E\u30F6\u95A2") == "\u971E\u30B1\u95A2", "small ke 1")
    check(normalize_small_ke("\u4E09\u30F5\u6708") == "\u4E09\u30B1\u6708", "small ke 2")
    check(strip_corporate("\u682A\u5F0F\u4F1A\u793EABC", "ja") == "ABC", "strip ja start")
    check(strip_corporate("ABC(\u682A)", "ja") == "ABC", "strip ja end")
    check(strip_corporate("M\u00FCller GmbH", "latin") == "M\u00FCller", "strip latin gmbh")
    check(strip_corporate("ABC Ltd.", "latin") == "ABC", "strip latin ltd")
    check(fold_latin("M\u00FCller") == "muller", "fold latin")
    check(person_key("M\u00FCller", "latin") == person_key("MULLER", "latin"), "person latin fold")
    check(company_key("ABC Co., Ltd.", "latin") == company_key("ABC", "latin"), "company latin multi")
    check(company_key("ABC GmbH", "latin") == company_key("ABC", "latin"), "company latin gmbh")
    check(phone_key("+81-3-1234-5678") == "0312345678", "phone plus 81 hyphen")
    check(phone_key("(03) 1234 5678") == "0312345678", "phone parens")
    check(company_key("\u682A\u5F0F\u4F1A\u793EABC", "ja") == company_key("ABC\u682A\u5F0F\u4F1A\u793E", "ja"), "company ja both")
    check(person_key("\u5C71\u7530 \u592A\u90CE", "ja") == "\u5C71\u7530\u592A\u90CE", "person space")
    check(person_key("\u5C71\u7530\u30FB\u592A\u90CE", "ja") == "\u5C71\u7530\u592A\u90CE", "person middle dot")
    check(probe_pairs([["03-1234-5678", "+81 3 1234 5678"]], "phone", "ja")["equal"] == 1, "probe equal")
    check(probe_pairs([["A", "B"]], "company", "latin")["unequal"] == [["A", "B"]], "probe unequal")

    # Fix 1: non-str text arguments raise ValueError naming the function.
    for func, args in [
        (nfkc, (123,)),
        (normalize_space, (123,)),
        (normalize_long_vowel, (123,)),
        (normalize_itaiji, (123,)),
        (normalize_small_ke, (123,)),
        (strip_corporate, (123, "latin")),
        (fold_latin, (123,)),
        (company_key, (123, "ja")),
        (person_key, (123, "ja")),
        (phone_key, (12345,)),
    ]:
        name = func.__name__
        try:
            func(*args)
            check(False, "type check " + name + " did not raise")
        except ValueError as e:
            check(name in str(e), "type check name " + name)
        except Exception as e:
            check(False, "type check " + name + " raised " + type(e).__name__)

    # Fix 2: strip Latin S.p.A without trailing dot.
    check(strip_corporate("ACME S.p.A", "latin") == "ACME", "strip latin S.p.A no dot")
    check(strip_corporate("ACME S.p.A.", "latin") == "ACME", "strip latin S.p.A. dot")
    check(company_key("ACME S.p.A", "latin") == company_key("ACME", "latin"), "company latin S.p.A no dot")

    try:
        probe_pairs([], "bad", "ja")
        check(False, "probe bad kind")
    except ValueError:
        check(True, "probe bad kind")
    try:
        probe_pairs([], "company", "bad")
        check(False, "probe bad locale")
    except ValueError:
        check(True, "probe bad locale")

    if failures:
        for msg in failures:
            print("SELFTEST FAIL: " + msg)
        return 1
    print("SELFTEST PASS")
    return 0

def main():
    args = sys.argv[1:]
    if len(args) == 1 and args[0] == "--selftest":
        return _selftest()
    if len(args) == 4 and args[0] == "key":
        kind = args[1]
        locale = args[2]
        text = args[3]
        if kind not in ("company", "person", "phone"):
            print("NO-DATA: unknown kind: " + str(kind))
            return 2
        if locale not in ("ja", "latin"):
            print("NO-DATA: unknown locale: " + str(locale))
            return 2
        try:
            if kind == "company":
                print(company_key(text, locale))
            elif kind == "person":
                print(person_key(text, locale))
            else:
                print(phone_key(text))
            return 0
        except ValueError as e:
            print("NO-DATA: " + str(e))
            return 2
    print("NO-DATA: bad input")
    return 2

if __name__ == "__main__":
    sys.exit(main())
