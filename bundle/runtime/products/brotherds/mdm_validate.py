"""Validators for Japanese master data identifiers: corporate_number checks the 13 digit Japanese corporate number check digit rule; invoice_number checks the qualified invoice issuer registration number as the letter T followed by 13 digits and applies the corporate number check digit rule; gtin checks GTIN-8, GTIN-12, GTIN-13, and GTIN-14 check digits and reports JAN prefixes; postal_code checks seven digit Japanese postal codes with an optional leading postal mark; and phone checks Japanese phone numbers and classifies mobile, IP, free dial, navi dial, and landline numbers. The invoice number check digit applies the corporate number rule to every registration number: this is verified for corporations, and for sole proprietors it is the tool's assumption and is labelled as such."""

import json
import sys
import unicodedata

_REMOVED_CHARS = {
    ord("-"),
    ord("\u2010"),
    ord("\u2011"),
    ord("\u2013"),
    ord("\u2014"),
    ord("\u2015"),
    ord("\u2212"),
    ord("\u30fc"),
    ord("\uff0d"),
}

_EMPTY = object()
_INVALID = object()

_INVOICE_ASSUMPTION = "the corporate number check digit rule is verified for corporations; for sole proprietors it is applied as an assumption"


def _empty(extra=None):
    result = {"normalized": None, "valid": False, "reason": "empty"}
    if extra:
        result.update(extra)
    return result


def _is_ascii_digits(text):
    if text == "":
        return False
    for ch in text:
        if ch < "0" or ch > "9":
            return False
    return True


def _normalize_common(value):
    if value is None:
        return _EMPTY
    if isinstance(value, bool):
        return _INVALID
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return _INVALID
    text = unicodedata.normalize("NFKC", value).strip()
    out = []
    for ch in text:
        if ch.isspace():
            continue
        if ch == "-" or ord(ch) in _REMOVED_CHARS:
            continue
        out.append(ch)
    result = "".join(out)
    if result == "":
        return _EMPTY
    return result


def _corporate_check_digit_ok(digits):
    if len(digits) != 13 or not _is_ascii_digits(digits):
        return False
    base = digits[1:]
    total = 0
    for index, ch in enumerate(reversed(base), start=1):
        weight = 1 if index % 2 == 1 else 2
        total += int(ch) * weight
    expected = 9 - (total % 9)
    return int(digits[0]) == expected


def corporate_number_check(value):
    normalized = _normalize_common(value)
    if normalized is _EMPTY:
        return _empty()
    if normalized is _INVALID:
        return {"normalized": None, "valid": False, "reason": "not 13 digits"}
    if len(normalized) != 13 or not _is_ascii_digits(normalized):
        return {"normalized": normalized, "valid": False, "reason": "not 13 digits"}
    if not _corporate_check_digit_ok(normalized):
        return {"normalized": normalized, "valid": False, "reason": "check digit mismatch"}
    return {"normalized": normalized, "valid": True, "reason": "ok"}


def invoice_number_check(value):
    normalized = _normalize_common(value)
    if normalized is _EMPTY:
        return _empty({
            "check_digit_rule": "corporate_number",
            "assumption": _INVOICE_ASSUMPTION,
        })
    if normalized is _INVALID:
        return {
            "normalized": None,
            "valid": False,
            "reason": "not T plus 13 digits",
            "check_digit_rule": "corporate_number",
            "assumption": _INVOICE_ASSUMPTION,
        }
    normalized = normalized.upper()
    if len(normalized) != 14 or normalized[0] != "T" or not _is_ascii_digits(normalized[1:]):
        return {
            "normalized": normalized,
            "valid": False,
            "reason": "not T plus 13 digits",
            "check_digit_rule": "corporate_number",
            "assumption": _INVOICE_ASSUMPTION,
        }
    digits = normalized[1:]
    if not _corporate_check_digit_ok(digits):
        return {
            "normalized": "T" + digits,
            "valid": False,
            "reason": "check digit mismatch",
            "check_digit_rule": "corporate_number",
            "assumption": _INVOICE_ASSUMPTION,
        }
    return {
        "normalized": "T" + digits,
        "valid": True,
        "reason": "ok",
        "check_digit_rule": "corporate_number",
        "assumption": _INVOICE_ASSUMPTION,
    }


def gtin_check(value):
    normalized = _normalize_common(value)
    if normalized is _EMPTY:
        return {
            "normalized": None,
            "valid": False,
            "reason": "empty",
            "kind": None,
            "jan_prefix": False,
        }
    if normalized is _INVALID:
        return {
            "normalized": None,
            "valid": False,
            "reason": "not a GTIN length",
            "kind": None,
            "jan_prefix": False,
        }
    if len(normalized) not in (8, 12, 13, 14) or not _is_ascii_digits(normalized):
        return {
            "normalized": normalized,
            "valid": False,
            "reason": "not a GTIN length",
            "kind": None,
            "jan_prefix": False,
        }
    kind = {8: "GTIN-8", 12: "GTIN-12", 13: "GTIN-13", 14: "GTIN-14"}[len(normalized)]
    jan_prefix = kind == "GTIN-13" and (normalized.startswith("45") or normalized.startswith("49"))
    check = int(normalized[-1])
    body = normalized[:-1]
    total = 0
    for index, ch in enumerate(reversed(body)):
        weight = 3 if index % 2 == 0 else 1
        total += int(ch) * weight
    expected = (10 - (total % 10)) % 10
    if check != expected:
        return {
            "normalized": normalized,
            "valid": False,
            "reason": "check digit mismatch",
            "kind": kind,
            "jan_prefix": jan_prefix,
        }
    return {
        "normalized": normalized,
        "valid": True,
        "reason": "ok",
        "kind": kind,
        "jan_prefix": jan_prefix,
    }


def postal_code_check(value):
    if value is None:
        return _empty()
    if isinstance(value, bool):
        return {"normalized": None, "valid": False, "reason": "not 7 digits"}
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return {"normalized": None, "valid": False, "reason": "not 7 digits"}
    text = unicodedata.normalize("NFKC", value).strip()
    if text.startswith("\u3012"):
        text = text[1:]
    normalized = _normalize_common(text)
    if normalized is _EMPTY:
        return _empty()
    if normalized is _INVALID:
        return {"normalized": None, "valid": False, "reason": "not 7 digits"}
    if len(normalized) != 7 or not _is_ascii_digits(normalized):
        return {"normalized": normalized, "valid": False, "reason": "not 7 digits"}
    return {
        "normalized": normalized[:3] + "-" + normalized[3:],
        "valid": True,
        "reason": "ok",
    }


def phone_check(value):
    if value is None:
        return {"normalized": None, "valid": False, "reason": "empty", "kind": None}
    if isinstance(value, bool):
        return {"normalized": None, "valid": False, "reason": "unexpected length", "kind": None}
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return {"normalized": None, "valid": False, "reason": "unexpected length", "kind": None}
    text = unicodedata.normalize("NFKC", value).strip()
    plus81 = text.startswith("+81")
    digits = []
    for ch in text:
        if "0" <= ch <= "9":
            digits.append(ch)
    digits = "".join(digits)
    if plus81 and digits.startswith("81"):
        remaining = digits[2:]
        if remaining.startswith("0"):
            digits = remaining
        else:
            digits = "0" + remaining
    if digits == "":
        return {"normalized": None, "valid": False, "reason": "empty", "kind": None}
    if not digits.startswith("0"):
        return {"normalized": digits, "valid": False, "reason": "no leading zero", "kind": None}
    kind = None
    if digits.startswith("0800"):
        if len(digits) == 11:
            kind = "free_dial"
    elif digits.startswith(("070", "080", "090", "050")):
        if len(digits) == 11:
            if digits.startswith("050"):
                kind = "ip"
            else:
                kind = "mobile"
    elif digits.startswith("0120"):
        if len(digits) == 10:
            kind = "free_dial"
    elif digits.startswith("0570"):
        if len(digits) == 10:
            kind = "navi_dial"
    else:
        if len(digits) == 10:
            kind = "landline"
    if kind is None:
        return {"normalized": digits, "valid": False, "reason": "unexpected length", "kind": None}
    return {"normalized": digits, "valid": True, "reason": "ok", "kind": kind}


_KINDS = {
    "corporate_number": corporate_number_check,
    "invoice_number": invoice_number_check,
    "gtin": gtin_check,
    "postal_code": postal_code_check,
    "phone": phone_check,
}


def validate_column(values, kind):
    if kind not in _KINDS:
        raise ValueError(f"unknown kind {kind}")
    check = _KINDS[kind]
    values = list(values)
    n = len(values)
    empty = 0
    valid = 0
    invalid = 0
    invalid_examples = []
    duplicates = 0
    seen = set()
    reasons = {}
    for raw in values:
        result = check(raw)
        reason = result["reason"]
        if reason == "empty":
            empty += 1
            continue
        if result["valid"]:
            valid += 1
            normalized = result["normalized"]
            if normalized in seen:
                duplicates += 1
            else:
                seen.add(normalized)
            continue
        invalid += 1
        if len(invalid_examples) < 5:
            invalid_examples.append(str(raw))
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "kind": kind,
        "n": n,
        "empty": empty,
        "valid": valid,
        "invalid": invalid,
        "invalid_examples": invalid_examples,
        "duplicates": duplicates,
        "reasons": reasons,
    }


def _selftest():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    result = corporate_number_check("7000012050002")
    check(result["valid"] and result["normalized"] == "7000012050002" and result["reason"] == "ok", "corporate valid example")
    result = corporate_number_check(" 7000-0120 50002 ")
    check(result["valid"] and result["normalized"] == "7000012050002", "corporate normalization")
    result = corporate_number_check("7000012050003")
    check((not result["valid"]) and result["reason"] == "check digit mismatch", "corporate check digit mismatch")
    result = corporate_number_check("123")
    check((not result["valid"]) and result["reason"] == "not 13 digits", "corporate not 13 digits")
    result = corporate_number_check(None)
    check((not result["valid"]) and result["reason"] == "empty", "corporate non string")
    result = corporate_number_check("---")
    check((not result["valid"]) and result["reason"] == "empty", "corporate empty after normalization")
    # item 6: National Tax Agency worked example
    result = corporate_number_check("8700110005901")
    check(result["valid"] and result["normalized"] == "8700110005901" and result["reason"] == "ok", "corporate NTA worked example")
    # item 3: int conversion
    result = corporate_number_check(7000012050002)
    check(result["valid"] and result["normalized"] == "7000012050002", "corporate int input")
    result = corporate_number_check(1.5)
    check((not result["valid"]) and result["reason"] == "not 13 digits", "corporate float input")
    result = corporate_number_check(True)
    check((not result["valid"]) and result["reason"] == "not 13 digits", "corporate bool input")
    # item 4: dash variants
    result = corporate_number_check("7000\u20130120\u20145000\u20152")
    check(result["valid"] and result["normalized"] == "7000012050002", "corporate unicode dashes")

    result = invoice_number_check("T7000012050002")
    check(result["valid"] and result["normalized"] == "T7000012050002" and result["check_digit_rule"] == "corporate_number", "invoice valid")
    result = invoice_number_check("t7000012050002")
    check(result["valid"] and result["normalized"] == "T7000012050002", "invoice lowercase")
    result = invoice_number_check("T123")
    check((not result["valid"]) and result["reason"] == "not T plus 13 digits", "invoice bad format")
    result = invoice_number_check("T7000012050003")
    check((not result["valid"]) and result["reason"] == "check digit mismatch", "invoice check mismatch")
    result = invoice_number_check(None)
    check((not result["valid"]) and result["reason"] == "empty" and result["check_digit_rule"] == "corporate_number", "invoice non string")
    # item 5: assumption key present on every result
    result = invoice_number_check("T7000012050002")
    check("assumption" in result and result["assumption"] == _INVOICE_ASSUMPTION, "invoice assumption valid")
    result = invoice_number_check("T7000012050003")
    check("assumption" in result and result["assumption"] == _INVOICE_ASSUMPTION, "invoice assumption mismatch")
    result = invoice_number_check("bad")
    check("assumption" in result and result["assumption"] == _INVOICE_ASSUMPTION, "invoice assumption bad format")
    result = invoice_number_check(None)
    check("assumption" in result and result["assumption"] == _INVOICE_ASSUMPTION, "invoice assumption empty")
    # item 3: float
    result = invoice_number_check(1.5)
    check((not result["valid"]) and result["reason"] == "not T plus 13 digits" and "assumption" in result, "invoice float input")

    result = gtin_check("4901234567894")
    check(result["valid"] and result["kind"] == "GTIN-13" and result["jan_prefix"] is True, "gtin jan valid")
    result = gtin_check("12345670")
    check(result["valid"] and result["kind"] == "GTIN-8" and result["jan_prefix"] is False, "gtin 8 valid")
    result = gtin_check("12345")
    check((not result["valid"]) and result["reason"] == "not a GTIN length" and result["kind"] is None, "gtin bad length")
    result = gtin_check("4901234567895")
    check((not result["valid"]) and result["reason"] == "check digit mismatch" and result["kind"] == "GTIN-13" and result["jan_prefix"] is True, "gtin check mismatch")
    result = gtin_check("490123456789A")
    check((not result["valid"]) and result["reason"] == "not a GTIN length", "gtin non digit")
    result = gtin_check(None)
    check((not result["valid"]) and result["reason"] == "empty" and result["kind"] is None and result["jan_prefix"] is False, "gtin non string")
    # item 3: float
    result = gtin_check(4.5)
    check((not result["valid"]) and result["reason"] == "not a GTIN length" and result["reason"] != "empty", "gtin float input")
    # item 3: int
    result = gtin_check(4901234567894)
    check(result["valid"] and result["normalized"] == "4901234567894", "gtin int input")

    result = postal_code_check("1234567")
    check(result["valid"] and result["normalized"] == "123-4567", "postal valid")
    result = postal_code_check("\u3012123-4567")
    check(result["valid"] and result["normalized"] == "123-4567", "postal mark")
    result = postal_code_check("123456")
    check((not result["valid"]) and result["reason"] == "not 7 digits", "postal bad length")
    result = postal_code_check(None)
    check((not result["valid"]) and result["reason"] == "empty", "postal non string")
    # item 3: int, bool, list
    result = postal_code_check(1234567)
    check(result["valid"] and result["normalized"] == "123-4567", "postal int input")
    result = postal_code_check(True)
    check((not result["valid"]) and result["reason"] == "not 7 digits", "postal bool input")
    result = postal_code_check([1, 2, 3])
    check((not result["valid"]) and result["reason"] == "not 7 digits", "postal list input")

    result = phone_check("090-1234-5678")
    check(result["valid"] and result["kind"] == "mobile" and result["normalized"] == "09012345678", "phone mobile")
    result = phone_check("050-1234-5678")
    check(result["valid"] and result["kind"] == "ip", "phone ip")
    result = phone_check("0120-123-456")
    check(result["valid"] and result["kind"] == "free_dial", "phone free dial 0120")
    result = phone_check("0800-123-4567")
    check(result["valid"] and result["kind"] == "free_dial", "phone free dial 0800")
    result = phone_check("0570-123-456")
    check(result["valid"] and result["kind"] == "navi_dial", "phone navi dial")
    result = phone_check("03-1234-5678")
    check(result["valid"] and result["kind"] == "landline", "phone landline")
    result = phone_check("+81-3-1234-5678")
    check(result["valid"] and result["kind"] == "landline" and result["normalized"] == "0312345678", "phone plus 81")
    result = phone_check("1234567890")
    check((not result["valid"]) and result["reason"] == "no leading zero", "phone no leading zero")
    result = phone_check("090123456789")
    check((not result["valid"]) and result["reason"] == "unexpected length", "phone unexpected length")
    result = phone_check(None)
    check((not result["valid"]) and result["reason"] == "empty" and result["kind"] is None, "phone non string")
    # item 1: prefix length rules
    result = phone_check("0901234567")
    check((not result["valid"]) and result["reason"] == "unexpected length" and result["kind"] is None, "phone 10 digit 090")
    result = phone_check("0801234567")
    check((not result["valid"]) and result["reason"] == "unexpected length", "phone 10 digit 080")
    result = phone_check("0701234567")
    check((not result["valid"]) and result["reason"] == "unexpected length", "phone 10 digit 070")
    result = phone_check("0501234567")
    check((not result["valid"]) and result["reason"] == "unexpected length", "phone 10 digit 050")
    result = phone_check("01201234567")
    check((not result["valid"]) and result["reason"] == "unexpected length", "phone 0120 too long")
    result = phone_check("0800123456")
    check((not result["valid"]) and result["reason"] == "unexpected length", "phone 0800 too short")
    result = phone_check("05701234567")
    check((not result["valid"]) and result["reason"] == "unexpected length", "phone 0570 too long")
    # item 2: plus 81 keeping existing zero
    result = phone_check("+81-03-1234-5678")
    check(result["valid"] and result["normalized"] == "0312345678" and result["kind"] == "landline", "phone plus 81 zero")
    result = phone_check("+81 (0)3 1234 5678")
    check(result["valid"] and result["normalized"] == "0312345678" and result["kind"] == "landline", "phone plus 81 paren zero")
    result = phone_check("+81-90-1234-5678")
    check(result["valid"] and result["normalized"] == "09012345678" and result["kind"] == "mobile", "phone plus 81 mobile")
    # item 3: int and float
    result = phone_check(9012345678)
    check((not result["valid"]) and result["reason"] == "no leading zero", "phone int no leading zero")
    result = phone_check(1.5)
    check((not result["valid"]) and result["reason"] == "unexpected length" and result["reason"] != "empty", "phone float input")

    values = ["4901234567894", "4901234567894", "bad", None, "4901234567895"]
    result = validate_column(values, "gtin")
    check(result["kind"] == "gtin" and result["n"] == 5 and result["empty"] == 1 and result["valid"] == 2 and result["invalid"] == 2, "column counts")
    check(result["invalid_examples"] == ["bad", "4901234567895"], "column invalid examples")
    check(result["duplicates"] == 1, "column duplicates")
    check(result["reasons"] == {"not a GTIN length": 1, "check digit mismatch": 1}, "column reasons")
    result = validate_column(["7000012050002", "7000012050002", "7000012050002"], "corporate_number")
    check(result["valid"] == 3 and result["duplicates"] == 2, "column duplicates three")
    # item 3: validate_column counting for int / float / None / empty
    result = validate_column([7000012050002, 1.5, None, ""], "corporate_number")
    check(result["empty"] == 2 and result["valid"] == 1 and result["invalid"] == 1, "column mixed types counts")
    check(result["reasons"].get("not 13 digits") == 1, "column mixed types reasons")
    try:
        validate_column([], "unknown")
        check(False, "column unknown kind raises")
    except ValueError as exc:
        check("unknown" in str(exc), "column unknown kind message")

    if failures:
        for failure in failures:
            print(failure)
        return 1
    print("SELFTEST PASS")
    return 0


def _main(argv):
    if len(argv) >= 2 and argv[1] == "--selftest":
        return _selftest()
    if len(argv) < 2:
        print("NO-DATA: unknown kind ")
        return 2
    kind = argv[1]
    if kind not in _KINDS:
        print(f"NO-DATA: unknown kind {kind}")
        return 2
    check = _KINDS[kind]
    for value in argv[2:]:
        result = check(value)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
