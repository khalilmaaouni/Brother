#!/usr/bin/env python3
"""Score each Jev use case in a catalogue out of 10. Owner bar 2026-09-20: keep only 9 or 10, and TOKEN SAVING plus
SPEED rank highest, so those two carry 5 of the 10 points.
usage (repo root): usecase_score.py <catalogue.md ...> [--json out.json] [--keep 9]      usecase_score.py --selftest
A use case is one '### <title>' section carrying the ten fields the catalogue shape defines. Every point is decided by
reading the entry, never by asking a model. FOUR REFUSALS score the entry 0 whatever else it says, because each one is a
property the estate forbids outright: Jev as the decider, a state string carrying rows or table data, an invented figure,
or a blocking call on a hot path. An entry that cannot be parsed scores 0 and says why: unreadable is never a pass."""
import json, re, sys

FIELDS = ("Decision", "Surface", "Deterministic first pass", "Jev question", "Inputs",
          "Fail direction", "Cascade use", "Ground truth for calibration", "What it saves", "Why not a bigger model")
# The four refusals, each a regex over the WHOLE entry. A hit means 0, and the reason is named.
REFUSALS = (
    ("jev decides", r"jev\s+(?:is\s+the\s+|becomes\s+the\s+|as\s+the\s+)?(?:decider|decides\s+whether|final\s+say|authorit)"
                    r"|(?:overrid|replac)\w*\s+the\s+deterministic"),
    ("sends data rows", r"\b(?:raw\s+rows|row\s+contents|sample\s+rows|full\s+table|table\s+contents|the\s+dataframe"
                        r"|column\s+values|cell\s+values|record\s+contents)\b"),
    ("invented figure", r"(?:saves|reduces|cuts|faster\s+by|cheaper\s+by|improv\w+\s+by)\D{0,24}\d+(?:\.\d+)?\s*(?:%|percent|x\b|times\b)"),
    ("blocking hot path", r"blocks?\s+(?:the\s+)?(?:tool\s+call|query|pipeline|request|until\s+jev)"
                          r"|synchronous(?:ly)?\s+(?:in|on)\s+(?:the\s+)?(?:pretooluse|hot\s+path|query\s+path)"),
)

def entries(text):
    """[(title, body)] for each '### ' section, in order. A document with no such heading yields nothing."""
    parts = re.split(r"^###[ \t]+(.+?)[ \t]*$", text, flags=re.M)
    return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]

def field(body, name):
    """The text of one '- **Name**:' field, up to the next field or the end. '' when absent."""
    m = re.search(r"^[-*]\s*\*\*%s\*\*\s*:\s*(.*?)(?=^[-*]\s*\*\*|\Z)" % re.escape(name), body, re.M | re.S)
    return m.group(1).strip() if m else ""

def typed_shape_ok(text):
    """True only for a shape the REAL call path accepts. Verified in the tree 2026-09-20:
    model_capability_profile.JEV_ALLOWED_QUESTION_TYPES is frozenset({"noul"}) and
    JEV_QUARANTINED_QUESTION_TYPES is {"score", "choice"}, refused by validate_jev_question_type with a hard
    raise, not a warning. So a choice or score entry cannot run at all, however well formed it looks, and an
    earlier version of this scorer wrongly passed both."""
    t = re.search(r'"type"\s*:\s*"([a-z]+)"', text)
    if not t: return False
    if not re.search(r'"instructions"\s*:\s*"[^"]{8,}', text): return False
    if t.group(1) != "noul": return False             # score and choice are quarantined in code
    return re.search(r'"criteria"\s*:', text) is None  # a noul carries no criteria

def score_entry(title, body):
    """(score out of 10, [missing reasons], [refusal reasons]). Refusals force 0."""
    refused = [why for why, pat in REFUSALS if re.search(pat, body, re.I)]
    pts, missing = [], []
    def award(ok, reason):
        pts.append(1 if ok else 0)
        if not ok: missing.append(reason)
    saves, inputs, det = field(body, "What it saves"), field(body, "Inputs"), field(body, "Deterministic first pass")
    fail, casc, truth = field(body, "Fail direction"), field(body, "Cascade use"), field(body, "Ground truth for calibration")
    jq, why = field(body, "Jev question"), field(body, "Why not a bigger model")
    # SAVING, 3 of 10
    award(bool(re.search(r"\b(not\s+sent|never\s+sent|instead\s+of\s+sending|skip\w*|avoid\w*|without\s+(?:running|sending)|short\s+state)", saves)),
          "SAVING: no concrete mechanism naming what is not sent or not run")
    award(bool(re.search(r"(typed|one\s+probability|short\s+state|state\s+string|few\s+hundred|kilobyte|vs\.?\s+the\s+whole|whole\s+context|full\s+context|expensive\s+scan|full\s+scan|reviewer)", saves + " " + why)),
          "SAVING: the saving is asserted, not structural (name the thing sent or run instead)")
    award("UNMEASURED" in saves or bool(re.search(r"measured\s+(?:at|on)\b", saves)),
          "SAVING: neither UNMEASURED nor a named measurement, so the figure is unsourced")
    # SPEED, 2 of 10
    award(bool(re.search(r"(after\s+the\s+(?:fact|decision)|side\s+effect|fire\s+and\s+forget|non\s*blocking|out\s+of\s+band|asynchronous|opt\s*in|background)", body)),
          "SPEED: does not state that it stays off the blocking path")
    award(bool(re.search(r"(faster\s+than|slower\s+path|replaces\s+a|instead\s+of\s+waiting|round\s+trip|minutes|seconds)", saves + " " + why + " " + field(body, "Surface"))),
          "SPEED: does not say what slower thing it replaces")
    # CONTRACT, 5 of 10
    award(bool(det) and "NEEDS-DETERMINISTIC-PASS-FIRST" not in det.upper(),
          "no deterministic first pass, so there is no answer for Jev to second guess")
    award(typed_shape_ok(jq), "the Jev question is not a shape jev_decide.py accepts (type, instructions, criteria)")
    award(bool(re.search(r"(safe|refus\w+|block\w*|escalat\w+|hold|keep\s+the|falls?\s+back|treats?\s+it\s+as)", fail)) and len(fail) > 20,
          "fail direction does not name the safe side")
    award(all(w in casc.upper() for w in ("ACT", "ESCALATE", "NO-DATA")),
          "cascade does not state all three of ACT, ESCALATE and NO-DATA")
    award(len(truth) > 20, "no ground truth, so the calibration ledger can never score it")
    absent = [f for f in FIELDS if not field(body, f)]
    if absent: missing.append("fields absent: " + ", ".join(absent))
    return (0 if refused else sum(pts)), missing, refused

def parse_inputs(argv):
    """Input paths only: a flag and its value are never mistaken for a file."""
    out, skip = [], False
    for a in argv:
        if skip: skip = False; continue
        if a in ("--json", "--keep"): skip = True; continue
        if not a.startswith("--"): out.append(a)
    return out

def selftest():
    """Answer the question even when a case RAISES. Measured 2026-09-22: eleven selftests in this
    directory exited 1 with a bare traceback and no verdict, so a pipeline reading the exit code and
    a human reading the text described the same run differently. Cases are built EAGERLY, so one
    raising expression takes the whole run with it; this wrapper is what turns that into a readable
    refusal. It does not make a broken module pass: it still returns non zero."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    good = """- **Decision**: Is this OPTIMIZE worth running now?
- **Surface**: per table, nightly, fires after the fact as a side effect, replaces a reviewer's minutes.
- **Deterministic first pass**: a manifest scan counting files under 16 MB.
- **Jev question**: {"type": "noul", "instructions": "The small file count and the last compaction age say compaction is due."}
- **Inputs**: file count, median file size, days since last OPTIMIZE.
- **Fail direction**: unsure keeps the deterministic answer, which is to skip, the safe side.
- **Cascade use**: ACT logs agreement, ESCALATE writes a line for the engineer, NO-DATA changes nothing.
- **Ground truth for calibration**: the next run's scan time, recorded after the job.
- **What it saves**: a short state string is sent instead of the whole manifest, so an expensive scan is skipped. UNMEASURED.
- **Why not a bigger model**: one probability beats paragraphs, and it is faster than a round trip with full context.
"""
    mk = lambda repl: good if not repl else good.replace(*repl)
    cases = [
        ("a complete entry scores 10", score_entry("t", good)[0] == 10),
        ("a missing deterministic pass loses a point", score_entry("t", mk(("a manifest scan counting files under 16 MB.", "NEEDS-DETERMINISTIC-PASS-FIRST, none exists.")))[0] == 9),
        ("a choice question is quarantined in code, so it loses the point", score_entry("t", mk(('{"type": "noul", "instructions": "The small', '{"type": "choice", "criteria": {"a": "x"}, "instructions": "The small')))[0] == 9),
        ("a score question is quarantined too", score_entry("t", mk(('{"type": "noul", "instructions": "The small', '{"type": "score", "criteria": ["a", "b"], "instructions": "The small')))[0] == 9),
        ("an unknown type loses the point", score_entry("t", mk(('"type": "noul"', '"type": "rating"')))[0] == 9),
        ("a noul carrying criteria is refused the point", score_entry("t", mk(('"type": "noul", "instructions"', '"type": "noul", "criteria": ["x"], "instructions"')))[0] == 9),
        ("an unsourced saving loses a point", score_entry("t", mk(("is skipped. UNMEASURED.", "is skipped. It saves a lot.")))[0] == 9),
        ("a named measurement keeps the point", score_entry("t", mk(("is skipped. UNMEASURED.", "is skipped, measured on the 2026-09-20 run.")))[0] == 10),
        ("an invented percentage scores 0", score_entry("t", mk(("UNMEASURED.", "saves 40 percent of tokens.")))[0] == 0),
        ("jev as decider scores 0", score_entry("t", good + "\nJev is the decider here.")[0] == 0),
        ("sending rows scores 0", score_entry("t", mk(("file count, median file size", "the raw rows of the table, file count")))[0] == 0),
        ("a blocking hot path scores 0", score_entry("t", good + "\nIt blocks the tool call until Jev answers.")[0] == 0),
        ("a refusal names its reason", score_entry("t", good + "\nJev is the decider here.")[2] == ["jev decides"]),
        ("two missing cascade words lose the point", score_entry("t", mk(("ACT logs agreement, ESCALATE writes a line for the engineer, NO-DATA changes nothing.", "ACT logs agreement.")))[0] == 9),
        ("no ground truth loses a point", score_entry("t", mk(("the next run's scan time, recorded after the job.", "none.")))[0] == 9),
        ("an empty document yields no entries", entries("nothing here") == []),
        ("two entries are split", len(entries("### a\nx\n### b\ny\n")) == 2),
        ("a flag value is not treated as an input file", parse_inputs(["a.md", "--json", "out.json", "--keep", "9", "b.md"]) == ["a.md", "b.md"]),
        ("an unparsable entry scores low, never high", score_entry("t", "- **Decision**: something")[0] <= 2),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0

def main():
    if "--selftest" in sys.argv: return selftest()
    args = parse_inputs(sys.argv[1:])
    if not args: print(__doc__); return 2
    keep = int(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else 9
    out = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    rows, kept, refused_n = {}, 0, 0
    for path in args:
        try:
            with open(path, encoding="utf-8") as f: text = f.read()
        except OSError as exc:
            print("NO-DATA  %s unreadable: %s" % (path, exc)); continue
        es = entries(text)
        if not es: print("NO-DATA  %s has no '### ' entry" % path); continue
        for title, body in es:
            sc, missing, refused = score_entry(title, body)
            rows[title] = {"score": sc, "file": path, "missing": missing, "refused": refused}
            kept += sc >= keep; refused_n += bool(refused)
            mark = "KEEP" if sc >= keep else "DROP"
            print("%-4s %2d/10  %-58s %s" % (mark, sc, title[:58],
                  ("REFUSED: " + ", ".join(refused)) if refused else "; ".join(missing)[:90]))
    print("\n%d entr(y/ies) scored | %d at %d or more | %d refused outright" % (len(rows), kept, keep, refused_n))
    if out:
        with open(out, "w", encoding="utf-8") as f: json.dump(rows, f, indent=1)
        print("wrote " + out)
    return 0 if rows else 1
if __name__ == "__main__": sys.exit(main())
