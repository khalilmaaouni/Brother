#!/usr/bin/env python3
"""Try every repaired spec draft of a unit against the deterministic scorer and keep the best one that is ACCEPTABLE.
usage (repo root): spec_accept.py <drafts dir> [unit ...]      drafts are <unit>-<model>.md
A draft is ACCEPTABLE only if ALL hold (any unreadable input rejects it):
  - every sub unit id of the plan still has its own heading section;
  - the section of every sub unit ALREADY LANDED is word for word what is in the tree (a repair never rewrites history);
  - no sub unit scores LOWER than it does today, and every sub unit not yet landed scores 9 or more;
  - for every sub unit not yet landed the scorer's PATHS TRUE and CALLS TRUE points are met: what the spec says about the
    real tree was checked against the real tree, so a repaired spec cannot be fiction with signatures;
  - it carries no private term and no long dash.
The best acceptable draft (highest minimum score, then highest total) is left IN PLACE in the working tree for the commit;
otherwise the original file is restored byte for byte. Prints one line per draft with the reason. Exit 0, 1 or 2: see the end."""
import glob, json, os, re, subprocess, sys
BIN = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BIN)
import grade_build as G
import plan_store  # noqa: E402  (the one landed test: plan_store.sub_landed, finding 2 of 2026-09-27)
# FLOOR, founder order 2026-09-21: "Finish these minimum at 8 if 9 is impossible". 9 stays the TARGET and
# the default, so an ordinary run is unchanged; --floor 8 is a deliberate SECOND pass, used only after a repair
# wave has tried for 9 and failed, and it says so on every section it accepts below 9 so the debt is visible
# rather than quietly banked. A floor above 9 or below 1 is refused: this option exists to lower a bar by a
# named amount under a named order, never to move it anywhere at all.
FLOOR = 9
if "--floor" in sys.argv:
    _i = sys.argv.index("--floor")
    FLOOR = int(sys.argv[_i + 1])
    if not 1 <= FLOOR <= 10:
        print("a floor must be between 1 and 10; refusing %r" % FLOOR); sys.exit(2)
    del sys.argv[_i:_i + 2]
if len(sys.argv) < 2:
    print("usage: spec_accept.py <drafts dir> [unit ...] | --selftest"); sys.exit(2)
drafts_dir = os.path.abspath(os.path.expanduser(sys.argv[1])); only = sys.argv[2:]
if FLOOR < 9:
    print("FLOOR   %d of 10, below the standing bar of 9: a second pass under the owner's 2026-09-21 order, "
          "for sections a repair wave could not lift to 9" % FLOOR)
plan = json.load(open("docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", encoding="utf-8"))
tmp_json = os.path.expanduser("~/.claude/evidence/spec-accept-scores.json")


def score(unit):
    r = subprocess.run([sys.executable, os.path.join(BIN, "spec_score.py"), unit, "--json", tmp_json], capture_output=True, text=True)
    if r.returncode:
        print("spec_score exit %d for %s: unscored, which the floor refuses" % (r.returncode, unit))
        return {}
    try:
        return json.load(open(tmp_json))
    except (OSError, ValueError):
        return {}


def section(text, sub):
    """The sub unit's section up to the next heading of its own level or higher: a `####` block inside a `###` section
    belongs to it (review 2026-09-23: the old boundary stopped at any heading, so a nested rewrite slipped past the splice)."""
    h = re.search(r"^(#{2,4}) %s\b" % re.escape(sub), text, flags=re.M)
    if not h: return None
    m = re.search(r"^#{2,4} %s\b.*?(?=^#{2,%d} |\Z)" % (re.escape(sub), len(h.group(1))), text, flags=re.M | re.S)
    return m.group(0).strip() if m else None


def splice_landed(text, original, landed):
    """A REPAIR NEVER REWRITES HISTORY, BY CONSTRUCTION (2026-09-23 00:0x): four of four L1b and C0 drafts were refused for
    rewriting a landed section although their open sections were the repair asked for. Each landed section in the draft is
    replaced by the tree's own text before the draft is judged, so the drafter cannot rewrite history whatever it returns.
    A landed section the draft dropped, or one the tree does not carry, is left for the missing-section refusal."""
    for sub in landed:
        h = re.search(r"^(#{2,4}) %s\b" % re.escape(sub), text, flags=re.M)
        mine = re.search(r"^#{2,4} %s\b.*?(?=^#{2,%d} |\Z)" % (re.escape(sub), len(h.group(1))), text, flags=re.M | re.S) if h else None
        theirs = section(original, sub)
        if mine and theirs is not None:
            text = text[:mine.start()] + theirs + "\n\n" + text[mine.end():].lstrip("\n")
    return text


def selftest():
    orig = "# U\n### U.1 a\nold one\n### U.2 b\nold two\n"
    draft = "# U\n### U.1 a\nREWRITTEN\n### U.2 b\nnew two\n"
    out = splice_landed(draft, orig, ["U.1"])
    nested_o = "# U\n### U.1 a\nold\n#### detail\nOLD DETAIL\n### U.2 b\ntwo\n"; nested_d = "# U\n### U.1 a\nold\n#### detail\nNEW DETAIL\n### U.2 b\ntwo\n"
    cases = [("a landed section comes back word for word from the tree", section(out, "U.1") == section(orig, "U.1")),
             ("a nested block belongs to its section: a rewrite inside it is restored from the tree", "OLD DETAIL" in splice_landed(nested_d, nested_o, ["U.1"]) and "NEW DETAIL" not in splice_landed(nested_d, nested_o, ["U.1"])),
             ("an open section keeps the draft's text", section(out, "U.2") == "### U.2 b\nnew two"),
             ("nothing landed changes nothing", splice_landed(draft, orig, []) == draft),
             ("a landed section the draft dropped stays missing for the next refusal", section(splice_landed("# U\n### U.2 b\nx\n", orig, ["U.1"]), "U.1") is None),
             ("the path rule the acceptance applies: a section with a backticked file passes it, one without does not",
              bool(re.search(r"`[A-Za-z0-9_./-]+\.(?:py|sh|json|md|jsonl)`", "### U.2 b\n`scripts/x.py` existing\n")) and not re.search(r"`[A-Za-z0-9_./-]+\.(?:py|sh|json|md|jsonl)`", "### U.2 b\nno file here\n")),
             ("a landed section the tree lacks leaves the draft alone", splice_landed(draft, "# U\n### U.2 b\nold two\n", ["U.1"]) == draft)]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if "--selftest" in sys.argv: sys.exit(selftest())


if not os.path.isdir(drafts_dir):
    print("NO-DATA: the drafts folder %s does not exist, so no draft was judged" % drafts_dir); sys.exit(2)
accepted, rejected, judged = [], 0, set()
for u in plan["units"]:
    files = sorted(glob.glob(os.path.join(drafts_dir, u["id"] + "-*.md")))
    if not files or (only and u["id"] not in only) or not u.get("spec") or not u.get("sub_units"):
        continue
    judged.add(u["id"])
    path = u["spec"]; original = open(path, encoding="utf-8").read()
    ev = u.get("evidence") or ""
    landed = [s for s in u["sub_units"] if plan_store.sub_landed(s, ev)]
    before = score(u["id"]); best = None
    for f in files:
        text = open(f, encoding="utf-8").read()
        text = re.sub(r"^```(?:markdown|md)?\n|\n```\s*$", "", text.strip()) + "\n"
        text = splice_landed(text, original, landed)
        why = None
        if G.private_hits(text) or re.search("[\u2014\u2013]", text):
            why = "private term or long dash"
        elif any(section(text, s) is None for s in u["sub_units"]):
            why = "a sub unit section is missing: %s" % [s for s in u["sub_units"] if section(text, s) is None][:3]
        elif any(not re.search(r"`[A-Za-z0-9_./-]+\.(?:py|sh|json|md|jsonl)`", section(text, s) or "") for s in u["sub_units"] if s not in landed):
            why = "an open section names no file in backticks: %s (the pool's touch rule holds such a sub unit as unknown)" % [s for s in u["sub_units"] if s not in landed and not re.search(r"`[A-Za-z0-9_./-]+\.(?:py|sh|json|md|jsonl)`", section(text, s) or "")][:3]
        elif any(section(text, s) != section(original, s) for s in landed):
            why = "rewrites a LANDED section: %s" % [s for s in landed if section(text, s) != section(original, s)][:3]
        if why is None:
            open(path, "w", encoding="utf-8").write(text)
            after = score(u["id"])
            open(path, "w", encoding="utf-8").write(original)
            todo = [s for s in u["sub_units"] if s not in landed]
            lower = [s for s in u["sub_units"] if after.get(s, {}).get("score", 0) < before.get(s, {}).get("score", 0)]
            low = [s for s in todo if after.get(s, {}).get("score", 0) < FLOOR]
            untrue = [s for s in todo if any(m.startswith(("PATHS TRUE", "CALLS TRUE")) for m in after.get(s, {}).get("missing", []))]
            if lower:
                why = "scores LOWER on %s" % lower[:3]
            elif low:
                why = "still under %d: " % FLOOR + ", ".join("%s %s/10 (%s)" % (s, after.get(s, {}).get("score"), "; ".join(after.get(s, {}).get("missing", []))[:80]) for s in low[:2])
            elif untrue:
                why = "says something untrue about the tree in %s" % untrue[:3]
            else:
                key = (min(after[s]["score"] for s in todo) if todo else 10, sum(after[s]["score"] for s in u["sub_units"]))
                if best is None or key > best[0]:
                    best = (key, f, text)
                why = "ACCEPTABLE, minimum %d of 10" % key[0]
        print("%-5s %-28s %s" % (u["id"], os.path.basename(f), why))
        rejected += not why.startswith("ACCEPTABLE")
    if best:
        open(path, "w", encoding="utf-8").write(best[2]); accepted.append(u["id"])
        print("%-5s KEPT %s -> %s" % (u["id"], os.path.basename(best[1]), path))
print("\naccepted %d unit spec(s): %s" % (len(accepted), accepted))
missing = [x for x in only if x not in judged] if only else ([] if judged else ["any unit"])
if missing:
    print("NO-DATA: no judgeable draft in %s for %s" % (drafts_dir, ", ".join(missing)))
if rejected:
    print("REFUSED %d draft(s): see the lines above" % rejected)
# THE EXIT SAYS WHAT HAPPENED (Codex audit F10, 2026-09-27: a missing folder and a batch of refused drafts both exited 0):
# 0 every draft judged was ACCEPTABLE; 1 at least one draft was refused; 2 NO-DATA, no draft of a unit asked for was judged.
sys.exit(1 if rejected else 2 if missing else 0)
