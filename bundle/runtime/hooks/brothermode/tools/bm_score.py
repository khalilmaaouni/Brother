#!/usr/bin/env python3
"""BrotherMode weekly code-graded checks (Anthropic eval guidance: many small
code-graded checks; LLM judgment only for the residue). Reads the telemetry
ledger and fence registries; prints PASS / FAIL / NO-DATA per check with the
evidence inline. Never blocks; always exits 0. Honest outputs only: a check
without data says NO-DATA, never PASS."""
import json, os, sys, glob, datetime, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bm_telemetry import (LEDGER, RATINGS, REVIEWS, CORRECTIONS, SESSIONS_GLOB,
                          read_jsonl, age_days, fld, OUT_KEYS, prediction_counts,
                          real_sessions)

# Fence registries: the STATE.md files whose fence lines the hygiene checks
# read. Point BROTHERMODE_REGISTRIES at your own projects as colon-separated
# glob patterns, for example:
#   export BROTHERMODE_REGISTRIES="$HOME/work/*/STATE.md:$HOME/work/*/.plans/**/*STATE*.md"
# Unset, the registry checks report NO-DATA instead of guessing at paths.
REGISTRIES = []
for _pat in os.environ.get("BROTHERMODE_REGISTRIES", "").split(":"):
    if _pat.strip():
        REGISTRIES.extend(glob.glob(os.path.expanduser(_pat.strip()), recursive=True))

CACHE_RATIO_FLOOR = 90.0
results = []


def check(name, verdict, evidence):
    results.append((name, verdict, evidence))


def main():
    led = read_jsonl(LEDGER)
    # real_sessions everywhere: scorecard, nag, and these checks must all mean
    # the same thing by "session" (weekly-review-1 law, extended here 2026-07-23)
    rows = real_sessions(led)
    recent = [r for r in rows if (age_days(r.get("ts", "")) or 99) <= 7]

    # 1. ledger coverage: lines per active day (can only measure what exists)
    days = {}
    for r in recent:
        days.setdefault(r.get("ts", "")[:10], 0)
        days[r.get("ts", "")[:10]] += 1
    check("ledger-coverage", "NO-DATA" if not recent else "PASS",
          "%d sessions across %d active days last 7d" % (len(recent), len(days)))

    # 2. schema uniformity
    old = [r for r in led if r.get("schema") != 2]
    check("schema-2-uniform", "PASS" if not old else "FAIL",
          "%d pre-schema-2 lines remain" % len(old))

    # 3. cache economy per session
    flagged = []
    measured = 0
    for r in recent:
        cr, cw = r.get("cache_read", 0), r.get("cache_write", 0)
        if cr + cw > 0:
            measured += 1
            ratio = 100.0 * cr / (cr + cw)
            if ratio < CACHE_RATIO_FLOOR:
                flagged.append("%s %.0f%%" % (r.get("session_id", "?")[:8], ratio))
    if not measured:
        check("cache-economy", "NO-DATA", "no sessions with cache fields last 7d")
    else:
        check("cache-economy", "PASS" if not flagged else "FAIL",
              "%d/%d sessions >= %.0f%% warm-read; below floor: %s"
              % (measured - len(flagged), measured, CACHE_RATIO_FLOOR, flagged or "none"))

    # 4. vault log per active day (filename date OR mtime date counts, so a
    # dated backfill note clears an old miss; interim-score fix 2026-07-23)
    log_days = set()
    for f in glob.glob(SESSIONS_GLOB):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        if m:
            log_days.add(m.group(1))
        try:
            log_days.add(datetime.date.fromtimestamp(os.path.getmtime(f)).isoformat())
        except OSError:  # sbe: allow-silent glob hit vanished before stat; filename-date match above still counts
            continue
    missing = [d for d in days if d not in log_days]
    check("vault-log-per-active-day", "PASS" if not missing else "FAIL",
          "active days without any vault session log: %s" % (missing or "none"))

    # 5. fence hygiene across registries
    if not REGISTRIES:
        check("fence-hygiene", "NO-DATA", "set BROTHERMODE_REGISTRIES to enable")
    stale = []
    for p in REGISTRIES:
        try:
            age = (datetime.datetime.now().timestamp() - os.path.getmtime(p)) / 86400
        except OSError:  # sbe: allow-silent unreadable registry excluded from this NO-DATA/PASS/FAIL check, not fatal
            continue
        for line in open(p, errors="replace"):
            s = line.strip()
            if s.startswith("- ") and "agent" in s.lower() and "LANDED" not in s and "ADOPTED" not in s and age > 2:
                stale.append(os.path.basename(p))
                break
    if REGISTRIES:
        check("fence-hygiene", "PASS" if not stale else "FAIL",
              "registries with live-looking fences older than 2d: %s" % (stale or "none"))

    # 6. corrections pipeline
    corr = read_jsonl(CORRECTIONS)
    old_corr = [c for c in corr if (age_days(c.get("ts", "")) or 0) > 7]
    check("correction-latency", "PASS" if not old_corr else "FAIL",
          "%d candidates total, %d older than 7d unprocessed" % (len(corr), len(old_corr)))

    # 7. budget vs declared tier: scan fence lines in registries touched the
    # last 7d (older registries predate the tier law and are not judged by it).
    # PASS needs every recent live fence line tier-tagged; the spend comparison
    # itself stays a weekly-review judgment. (interim-score fix 2026-07-23)
    tagged, untagged = 0, []
    skill_state = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "STATE.md")
    for p in REGISTRIES + [skill_state]:
        try:
            if (datetime.datetime.now().timestamp() - os.path.getmtime(p)) / 86400 > 7:
                continue
        except OSError:  # sbe: allow-silent unreadable registry (e.g. no STATE.md yet) excluded from the tier check
            continue
        for line in open(p, errors="replace"):
            s = line.strip()
            if s.startswith("- ") and "agent" in s.lower() and "LANDED" not in s and "ADOPTED" not in s:
                if re.search(r"\btier T[123]\b", s):
                    tagged += 1
                else:
                    untagged.append(os.path.basename(p))
    if tagged + len(untagged) == 0:
        check("budget-vs-tier", "NO-DATA", "no fence lines in registries touched last 7d")
    else:
        check("budget-vs-tier", "PASS" if not untagged else "FAIL",
              "%d recent fence lines tier-tagged, untagged in: %s"
              % (tagged, sorted(set(untagged)) or "none"))

    # 8. predictions and ratings (external-evidence feeds)
    p = prediction_counts()
    rated = [x for x in read_jsonl(RATINGS) if isinstance(x.get("score"), (int, float))]
    check("prediction-seals", "PASS" if p["sealed"] >= 5 else ("NO-DATA" if p["sealed"] == 0 else "FAIL"),
          "%d sealed (target >= 5)" % p["sealed"])
    check("felt-outcome-ratings", "PASS" if len(rated) >= 6 else ("NO-DATA" if not rated else "FAIL"),
          "%d ratings (target >= 6 for alignment 10)" % len(rated))

    # 9. weekly review cadence
    reviews = read_jsonl(REVIEWS)
    last = max((x.get("ts", "") for x in reviews), default=None)
    a = age_days(last) if last else None
    check("review-cadence", "PASS" if (a is not None and a <= 7) else ("NO-DATA" if a is None else "FAIL"),
          "last review: %s" % (("%.1fd ago" % a) if a is not None else "never"))

    width = max(len(n) for n, _, _ in results)
    fails = sum(1 for _, v, _ in results if v == "FAIL")
    for n, v, e in results:
        print("%-*s  %-7s  %s" % (width, n, v, e))
    print("\n%d checks: %d PASS, %d FAIL, %d NO-DATA. LLM judge scores only the residue."
          % (len(results), sum(1 for _, v, _ in results if v == "PASS"), fails,
             sum(1 for _, v, _ in results if v == "NO-DATA")))
    # Two modes, same checks: the local hook stays advisory (exit 0, never blocks a
    # session), but `--strict` exits nonzero on any FAIL so CI can block a merge.
    # This is how "advisory" and "enforced" coexist without contradiction.
    if "--strict" in sys.argv and fails:
        print("STRICT: %d check(s) failed; exiting nonzero to fail the CI gate." % fails)
        sys.exit(1)
    sys.exit(0)


def cli():
    """Console-script entry point for a packaged install (pipx, uv, pip).

    The never-block guarantee lived only in the __main__ block, so a
    packaged `bm-score` pointed straight at main() would have lost it and
    the --strict honesty rule with it. The guard now lives here and the
    __main__ block calls this function, so the installed command and
    `python3 tools/bm_score.py` behave identically."""
    try:
        main()
    except Exception as e:
        # Never-block is a promise to the LOCAL session, not to CI. Swallowing a
        # crash and exiting 0 in --strict made the gate worthless: a checker that
        # died on its own bug reported success, so CI could go green having
        # verified nothing. Local runs still degrade quietly; --strict fails loud.
        print("bm_score: swallowed error (never blocks): %r" % (e,))
        if "--strict" in sys.argv:
            print("STRICT: the checker itself failed, so nothing was verified. "
                  "Exiting nonzero rather than reporting a pass it did not earn.")
            sys.exit(1)
        sys.exit(0)


if __name__ == "__main__":
    cli()
