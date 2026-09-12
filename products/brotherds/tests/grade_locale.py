"""Independent grader for mdm_normalize.py and pack_mdm_locale.py (orchestrator's cases)."""
import subprocess, sys

sys.path.insert(0, "products/brotherds")
bad = []
try:
    import mdm_normalize as N
    import pack_mdm_locale as P
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


class F(object):
    def __init__(self, g, v, d):
        self.gate, self.verdict, self.detail = g, v, d


class R(object):
    fns = []
    def register(self, ct, fn):
        assert ct == "MASTER_DATA"; self.fns.append(fn)


r = R(); P.register(r, F)


def run(c):
    out = {}
    for fn in r.fns:
        try:
            for f in fn(c, F):
                out[f.gate] = f
        except Exception as e:
            bad.append("gate crashed: %r" % e)
    return out.get("M22.normalization")


def want(c, v, words=(), label=""):
    f = run(c)
    if f is None:
        bad.append("%s: no M22 line" % label); return
    if f.verdict != v:
        bad.append("%s: %s want %s (%s)" % (label, f.verdict, v, f.detail))
    for w in words:
        if w.lower() not in f.detail.lower():
            bad.append("%s: detail lacks %r: %s" % (label, w, f.detail))


ALL = ["nfkc", "space", "long_vowel", "itaiji", "small_ke", "corporate"]
want({"master_data": {}}, "NO-DATA", label="absent")
want({"master_data": {"normalization": {"locale": "ja", "steps": ALL}}}, "PASS", label="full")
want({"master_data": {"normalization": {"locale": "ja", "steps": ["nfkc", "space"]}}}, "FAIL", ("itaiji", "small_ke"), "missing")
want({"master_data": {"normalization": {"locale": "tlh", "steps": ALL}}}, "FAIL", ("tlh",), "locale")
want({"master_data": {"normalization": {"locale": "ja", "steps": ALL,
      "probes": {"company": [["株式会社ｱｲｳ", "(株)アイウ"]], "phone": [["03-1234-5678", "+81 3 1234 5678"]]}}}},
     "PASS", (), "probes ok")
_pd = (run({"master_data": {"normalization": {"locale": "ja", "steps": ALL,
      "probes": {"company": [["株式会社ｱｲｳ", "(株)アイウ"]], "phone": [["03-1234-5678", "+81 3 1234 5678"]]}}}}) or F("", "", "")).detail
if "1/1" not in _pd and "2" not in _pd:
    bad.append("probe totals missing from the PASS detail: %s" % _pd)
want({"master_data": {"normalization": {"locale": "ja", "steps": ALL,
      "probes": {"company": [["アイウ", "カキク"]]}}}}, "FAIL", ("company",), "probe fails")
want({"master_data": {"normalization": {"locale": "ja", "steps": "nfkc"}}}, "FAIL", ("steps",), "malformed steps")
want({"master_data": {"normalization": {"locale": "ja", "steps": ALL, "probes": {"company": [["only one"]]}}}},
     "FAIL", label="malformed probe")
if P.__dict__.get("register") and run({"claim_type": "MASTER_DATA"}) is not None:
    bad.append("no master_data must be silent")
for f in ("products/brotherds/mdm_normalize.py", "products/brotherds/pack_mdm_locale.py"):
    c = subprocess.run([sys.executable, f] + (["--selftest"] if "normalize" in f else []), capture_output=True, text=True)
    if c.returncode != 0 or "SELFTEST PASS" not in c.stdout:
        bad.append("%s selftest exit %d %r" % (f, c.returncode, (c.stdout + c.stderr)[-300:]))
    t = open(f, encoding="utf-8").read()
    if "\u2014" in t or "\u2013" in t:
        bad.append("dash in " + f)
c = subprocess.run([sys.executable, "products/brotherds/mdm_normalize.py", "key", "animal", "ja", "x"], capture_output=True, text=True)
if c.returncode != 2 or not c.stdout.startswith("NO-DATA"):
    bad.append("CLI bad kind must print NO-DATA exit 2: %d %r" % (c.returncode, c.stdout[:80]))
for z in bad:
    print("CHECK FAIL:", z)
print("CHECK PASS" if not bad else "%d CHECK FAILURE(S)" % len(bad))
sys.exit(1 if bad else 0)
