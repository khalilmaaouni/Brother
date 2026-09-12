"""Independent grader for mdm_normalize.py and pack_mdm_locale.py (orchestrator's cases)."""
import subprocess, sys

sys.path.insert(0, "products/brotherds")
bad = []
try:
    import mdm_normalize as N
except Exception as e:
    print("CHECK FAIL: import: %r" % e); print("1 CHECK FAILURE(S)"); sys.exit(1)


def eq(a, b, msg):
    if a != b:
        bad.append("%s: %r != %r" % (msg, a, b))


def ck(s, loc="ja"):
    return N.company_key(s, loc)


eq(ck("株式会社ｱｲｳ商事"), ck("(株)アイウ商事"), "kabushiki prefix vs (kabu), half-width kana")
eq(ck("ＡＢＣ　ホールディングス㈱"), ck("ABCホールディングス株式会社"), "full-width, ideographic space, circled mark")
eq(N.person_key("山﨑 花子", "ja"), N.person_key("山崎花子", "ja"), "itaiji saki")
eq(N.person_key("齋藤", "ja"), N.person_key("斎藤", "ja"), "itaiji sai")
eq(N.person_key("渡邊", "ja"), N.person_key("渡辺", "ja"), "itaiji be")
eq(ck("コ－ヒ－工房"), ck("コーヒー工房"), "long vowel full-width hyphen")
eq(ck("コｰヒｰ工房"), ck("コーヒー工房"), "long vowel half-width mark")
eq(ck("霞ヶ関商店"), ck("霞ケ関商店"), "small ke")
eq(N.phone_key("０３－１２３４－５６７８"), "0312345678", "phone full-width")
eq(N.phone_key("+81 3 1234 5678"), "0312345678", "phone +81")
eq(N.normalize_long_vowel("03-1234"), "03-1234", "hyphen in digits untouched")
eq(ck("Müller GmbH", "latin"), ck("MULLER gmbh", "latin"), "latin fold and GmbH")
eq(ck("Acme Co., Ltd.", "ja"), ck("ACME", "ja"), "romanised Co., Ltd.")
if ck("アイウ商事") == ck("アイオ商事"):
    bad.append("different companies must not collide")
pr = N.probe_pairs([["株式会社ｱｲｳ", "(株)アイウ"], ["アイウ", "カキク"]], "company", "ja")
eq((pr["n"], pr["equal"], len(pr["unequal"])), (2, 1, 1), "probe_pairs counts")
for fn, msg in ((lambda: N.probe_pairs([], "animal", "ja"), "kind"), (lambda: N.probe_pairs([], "company", "xx"), "locale")):
    try:
        fn(); bad.append("probe_pairs unknown %s must raise" % msg)
    except ValueError:
        pass


# R3 regressions
for fn, lab in ((lambda: N.company_key(123, "ja"), "company_key"), (lambda: N.phone_key(12345), "phone_key"),
                (lambda: N.person_key(None, "ja"), "person_key")):
    try:
        fn(); bad.append("%s on a non-string must raise ValueError" % lab)
    except ValueError:
        pass
    except Exception as ex:
        bad.append("%s on a non-string raised %s, want ValueError" % (lab, type(ex).__name__))
c = subprocess.run([sys.executable, "products/brotherds/mdm_normalize.py", "--selftest"], capture_output=True, text=True)
if c.returncode != 0 or "SELFTEST PASS" not in c.stdout:
    bad.append("selftest exit %d %r" % (c.returncode, (c.stdout + c.stderr)[-300:]))
c = subprocess.run([sys.executable, "products/brotherds/mdm_normalize.py", "key", "animal", "ja", "x"], capture_output=True, text=True)
if c.returncode != 2 or not c.stdout.startswith("NO-DATA"):
    bad.append("CLI bad kind must print NO-DATA exit 2: %d %r" % (c.returncode, c.stdout[:80]))
if "\u2014" in open("products/brotherds/mdm_normalize.py", encoding="utf-8").read():
    bad.append("dash in file")
for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
