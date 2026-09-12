"""Independent grader for mdm_validate.py (orchestrator's own reference implementation)."""
import json, random, subprocess, sys

sys.path.insert(0, "products/brotherds")
bad = []
try:
    import mdm_validate as V
except Exception as e:
    print("CHECK FAIL: import: %r" % e); print("1 CHECK FAILURE(S)"); sys.exit(1)


def eq(a, b, msg):
    if a != b:
        bad.append("%s: got %r expected %r" % (msg, a, b))


def ref_corp_check(base12):
    s = sum(int(d) * (1 if n % 2 == 1 else 2) for n, d in enumerate(reversed(base12), start=1))
    return str(9 - (s % 9))


def ref_gtin_check(body):
    s = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return str((10 - s % 10) % 10)


rng = random.Random(20260912)
# corporate number: known public example plus 40 generated
r = V.corporate_number_check("7000012050002")
eq((r["valid"], r["reason"], r["normalized"]), (True, "ok", "7000012050002"), "known corporate number")
for _ in range(40):
    base = "".join(rng.choice("0123456789") for _ in range(12))
    good = ref_corp_check(base) + base
    wrong = str((int(good[0]) + 1 + rng.randrange(8)) % 10) + base
    eq(V.corporate_number_check(good)["valid"], True, "generated corp %s" % good)
    eq(V.corporate_number_check(wrong)["reason"], "check digit mismatch", "corrupted corp %s" % wrong)
eq(V.corporate_number_check("７０００－０１２０５０００２")["valid"], True, "full-width with hyphens")
eq(V.corporate_number_check("700001205000")["reason"], "not 13 digits", "12 digits")
eq(V.corporate_number_check("")["reason"], "empty", "empty")
eq(V.corporate_number_check(None)["reason"], "empty", "None")
# invoice number
eq(V.invoice_number_check("T7000012050002")["valid"], True, "invoice ok")
eq(V.invoice_number_check("t 7000012050002")["normalized"], "T7000012050002", "invoice lower t and space")
eq(V.invoice_number_check("7000012050002")["reason"], "not T plus 13 digits", "invoice without T")
eq(V.invoice_number_check("T8000012050002")["reason"], "check digit mismatch", "invoice bad check")
eq(V.invoice_number_check("T7000012050002").get("check_digit_rule"), "corporate_number", "invoice rule named")
# GTIN
for L in (8, 12, 13, 14):
    for _ in range(10):
        body = "".join(rng.choice("0123456789") for _ in range(L - 1))
        g = body + ref_gtin_check(body)
        r = V.gtin_check(g)
        eq((r["valid"], r["kind"]), (True, "GTIN-%d" % L), "gtin %s" % g)
        badg = body + str((int(g[-1]) + 1) % 10)
        eq(V.gtin_check(badg)["reason"], "check digit mismatch", "gtin bad %s" % badg)
jan = "490123456789"
jan = jan + ref_gtin_check(jan)
eq(V.gtin_check(jan)["jan_prefix"], True, "jan prefix 49")
other = "123456789012"
other = other + ref_gtin_check(other)
eq(V.gtin_check(other)["jan_prefix"], False, "non-jan GTIN-13")
eq(V.gtin_check("12345")["reason"], "not a GTIN length", "gtin length")
# postal
eq(V.postal_code_check("〒100-0013")["normalized"], "100-0013", "postal mark")
eq(V.postal_code_check("１００００１３")["normalized"], "100-0013", "postal full-width")
eq(V.postal_code_check("100-001")["reason"], "not 7 digits", "postal short")
# phone
for raw, norm, kind in [("03-1234-5678", "0312345678", "landline"),
                        ("090-1234-5678", "09012345678", "mobile"),
                        ("+81 90-1234-5678", "09012345678", "mobile"),
                        ("050-1234-5678", "05012345678", "ip"),
                        ("0120-123-456", "0120123456", "free_dial"),
                        ("0800-123-4567", "08001234567", "free_dial"),
                        ("0570-123-456", "0570123456", "navi_dial"),
                        ("０６（１２３４）５６７８", "0612345678", "landline")]:
    r = V.phone_check(raw)
    eq((r["valid"], r["normalized"], r["kind"]), (True, norm, kind), "phone %s" % raw)
eq(V.phone_check("12345")["reason"], "no leading zero", "phone no zero")
eq(V.phone_check("0312345")["reason"], "unexpected length", "phone short")
# review round R4b, findings reproduced by running (2026-09-12)
eq(V.corporate_number_check("8700110005901")["valid"], True, "official NTA worked example 8700110005901")
eq(V.corporate_number_check("9000000000000")["valid"], True, "remainder 0 gives check digit 9 (NTA formula)")
eq(V.phone_check("0901234567")["reason"], "unexpected length", "10-digit 090 number is not a landline")
eq(V.phone_check("0501234567")["reason"], "unexpected length", "10-digit 050 number is not a landline")
eq(V.phone_check("+81-03-1234-5678")["normalized"], "0312345678", "+81 followed by a domestic 0")
eq(V.phone_check("+81 (0)3 1234 5678")["normalized"], "0312345678", "+81 (0) form")
eq(V.corporate_number_check(7000012050002)["valid"], True, "an integer from a spreadsheet export is read, not empty")
eq(V.corporate_number_check(70000.5)["reason"], "not 13 digits", "a float is invalid, not empty")
eq(V.corporate_number_check("70000\u201312050002")["valid"], True, "en dash stripped")
eq(V.corporate_number_check("70000\u201412050002")["valid"], True, "em dash stripped")
inv = V.invoice_number_check("T7000012050002")
if "assumption" not in inv or "sole proprietor" not in str(inv.get("assumption", "")).lower():
    bad.append("invoice result must carry an 'assumption' field naming the sole proprietor limit")
col2 = V.validate_column([123, "7000012050002", 7000012050002], "corporate_number")
eq((col2["empty"], col2["valid"], col2["invalid"]), (0, 2, 1), "column with integers")
# column
col = V.validate_column(["7000012050002", "7000012050002", "", "123", "8000012050002", "7000012050002"], "corporate_number")
eq((col["n"], col["empty"], col["valid"], col["invalid"], col["duplicates"]), (6, 1, 3, 2, 2), "column counts")
eq(col["invalid_examples"], ["123", "8000012050002"], "column invalid examples")
eq(col["reasons"], {"not 13 digits": 1, "check digit mismatch": 1}, "column reasons")
try:
    V.validate_column([], "nope")
    bad.append("validate_column accepted an unknown kind")
except ValueError as exc:
    refused = str(exc)  # expected: the refusal is the behaviour under test
# CLI
p = subprocess.run([sys.executable, "products/brotherds/mdm_validate.py", "phone", "03-1234-5678"], capture_output=True, text=True)
try:
    eq(json.loads(p.stdout.strip().splitlines()[0])["kind"], "landline", "cli phone")
except Exception as e:
    bad.append("cli output not JSON: %r %r" % (e, p.stdout[:200]))
p = subprocess.run([sys.executable, "products/brotherds/mdm_validate.py", "bogus", "x"], capture_output=True, text=True)
eq(p.returncode, 2, "cli unknown kind exit")
p = subprocess.run([sys.executable, "products/brotherds/mdm_validate.py", "--selftest"], capture_output=True, text=True)
eq((p.returncode, "SELFTEST PASS" in p.stdout), (0, True), "selftest")
src = open("products/brotherds/mdm_validate.py", encoding="utf-8").read()
if "\u2014" in src or "\u2013" in src:
    bad.append("dash character in source")
if bad:
    for b in bad[:40]:
        print("CHECK FAIL:", b)
    print("%d CHECK FAILURE(S)" % len(bad)); sys.exit(1)
print("CHECK PASS")
