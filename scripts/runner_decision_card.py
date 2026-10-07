#!/usr/bin/env python3
"""ACC7.b: the runner decision card, written for the owner's money decision.

The card is generated from the ACC7.a figures (scripts/heavy_wait_report.py)
every time, never cached: the verdict and figures first, then exactly one
recommendation the verdict allows. On HOLD it says keep option A and do not
buy. On FLIP it recommends option C only within a ceiling the owner named, and
refuses to render at all without one. On NO-DATA it recommends nothing and says
what data is missing. No price is ever invented: the only dollar figures that
may appear are the ceiling and the hourly rate the owner supplied, and the card
checks itself for any other before it is returned.

  python3 scripts/runner_decision_card.py            writes docs/plan/ACC7-RUNNER-DECISION.md
  python3 scripts/runner_decision_card.py --check    regenerates in memory and compares

Exit codes: 0 HOLD (and, with --check, the committed card equals the regeneration
apart from its generated time line); 1 FLIP (the unit stays open until a runner
is wired and measured); 2 NO-DATA; 3 with --check when the committed card is
missing or stale.
"""
import argparse
import os
import re
import sys
import time
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import heavy_wait_report as hwr  # noqa: E402

ROOT = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(ROOT, "docs", "plan", "ACC7-RUNNER-DECISION.md")
GENERATED_PREFIX = "Generated: "
HOLD_LINE = "Recommendation: keep option A, do not buy."
STAT_KEYS = ("n", "median_s", "p90_s", "over_bar", "unqueued", "first_at")
NUMBER = r"\d[\d,]*(?:\.\d+)?"
PRICE_WORDS = (r"\$", r"USD", r"per month", r"/month", r"per hour", r"/hour")
PRICE_RE = re.compile(r"(?:%s)\s*(%s)|(%s)\s*(?:%s)"
                      % ("|".join(PRICE_WORDS), NUMBER, NUMBER, "|".join(PRICE_WORDS)), re.IGNORECASE)


def runner_options():
    # type: () -> List[Dict[str, str]]
    return [
        {"name": "A", "what": "keep the laptop with the existing heavy_slot queue",
         "cost_formula": "nothing: the machine is already owned", "footprint": "one slot directory, no new process",
         "limit": "heavy work waits for a slot; the wait is what waits.jsonl measures"},
        {"name": "B", "what": "a public repository lane for checks over the exported tree only",
         "cost_formula": "Linux minutes are free on a public repository", "footprint": "a workflow file in the public export",
         "limit": "exported tree only: never private source, by the public boundary rule"},
        {"name": "C", "what": "one owner paid cloud VM running the same heavy_slot client",
         "cost_formula": "hours used times the owner's quoted hourly rate", "footprint": "one VM, one slot directory on it",
         "limit": "spent only within a monthly ceiling the owner names; never bought by a session"},
    ]


def _dollars(value):
    return ("%.2f" % value).rstrip("0").rstrip(".")


def card_errors(card, allowed_dollars):
    # type: (str, List[str]) -> List[str]
    errors = []
    if not any(re.search(r"\b%s\b" % re.escape(v), card) for v in hwr.VERDICTS):
        errors.append("the card carries no verdict word")
    for match in PRICE_RE.finditer(card):
        number = match.group(1) or match.group(2)
        if number.replace(",", "") not in allowed_dollars:
            errors.append("price %r is not an owner supplied figure" % match.group(0))
    return errors


def _missing(verdict, stats):
    if stats.get("n", 0) < hwr.MIN_SAMPLES:
        return "%d usable samples, %d needed" % (stats.get("n", 0), hwr.MIN_SAMPLES)
    return "the wait file was unreadable or over %g of its lines were unusable" % hwr.MAX_BAD_SHARE


def render_card(verdict, stats, ceiling_usd, rate_usd_per_hour=None):
    # type: (str, Dict[str, float], Optional[float], Optional[float]) -> str
    if verdict not in hwr.VERDICTS:
        raise ValueError("verdict must be one of %s, not %r" % (", ".join(hwr.VERDICTS), verdict))
    missing_keys = [k for k in STAT_KEYS if k not in stats]
    if missing_keys:
        verdict = "NO-DATA"
    if verdict == "FLIP":
        if ceiling_usd is None:
            raise ValueError("a money decision without a ceiling is refused: pass ceiling_usd")
        if ceiling_usd <= 0:
            raise ValueError("ceiling_usd must be over 0")
    allowed = []
    if verdict == "FLIP":
        allowed.append(_dollars(ceiling_usd))
        if rate_usd_per_hour is not None:
            allowed.append(_dollars(rate_usd_per_hour))
    lines = ["# ACC7 runner decision card", "",
             "Verdict: %s" % verdict,
             "Flip condition: median heavy_slot wait strictly over %g s" % hwr.MEDIAN_BAR_S]
    if missing_keys:
        lines.append("Figures: NO-DATA, the statistics lack %s" % ", ".join(missing_keys))
    else:
        lines += ["Median wait: %.1f s over %d admissions since %s" % (stats["median_s"], stats["n"], stats["first_at"] or "NO-DATA"),
                  "Tail: p90 %.1f s, %d admissions over %g s, %d unqueued"
                  % (stats["p90_s"], stats["over_bar"], hwr.MEDIAN_BAR_S, stats["unqueued"])]
    lines += ["", "## Options", ""]
    for opt in runner_options():
        lines.append("- Option %s: %s. Cost: %s. Footprint: %s. Limit: %s."
                     % (opt["name"], opt["what"], opt["cost_formula"], opt["footprint"], opt["limit"]))
    lines += ["", "## Recommendation", ""]
    if verdict == "HOLD":
        lines += [HOLD_LINE, 'Owner act, one sentence: "keep the laptop".']
    elif verdict == "FLIP":
        ceiling = _dollars(ceiling_usd)
        rate = ("$%s per hour" % _dollars(rate_usd_per_hour) if rate_usd_per_hour is not None
                else "the hourly rate, owner supplied, not held by this script")
        lines += ["Recommendation: buy option C only within $%s per month." % ceiling,
                  "Cost formula: hours used times %s, capped at $%s per month." % (rate, ceiling),
                  'Owner act, one sentence: "buy option C at $%s per month".' % ceiling]
    else:
        lines += ["No recommendation: the data does not answer yet.",
                  "Missing: %s." % _missing(verdict, stats)]
    card = "\n".join(lines) + "\n"
    errors = card_errors(card, allowed)
    if errors:
        raise ValueError("; ".join(errors))
    return card


def _short(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home != "~" and path.startswith(home + os.sep) else path


def provenance(path, total, bad, now=None):
    return "%s%s\nSource: %s, %d lines, %d unusable\n" % (
        GENERATED_PREFIX, time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)), _short(path), total, bad)


def strip_generated(text):
    return "\n".join(l for l in text.splitlines() if not l.startswith(GENERATED_PREFIX))


def build(path, ceiling_usd, rate_usd_per_hour):
    verdict, stats, bad, total, _readable, _note = hwr.report(path)
    card = render_card(verdict, stats, ceiling_usd, rate_usd_per_hour)
    return verdict, card + "\n" + provenance(path, total, bad)


def write_whole(out, text):
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, out)   # a crash before this line leaves the old card in place


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--path", default=None, help="the wait file (default: <slot dir>/waits.jsonl)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="the card file")
    ap.add_argument("--ceiling", type=float, default=None, help="owner supplied monthly ceiling in USD")
    ap.add_argument("--rate", type=float, default=None, help="owner supplied hourly rate in USD")
    ap.add_argument("--check", action="store_true", help="regenerate in memory and compare with --out")
    args = ap.parse_args(argv)
    path = args.path or hwr.default_path()
    try:
        verdict, text = build(path, args.ceiling, args.rate)
    except ValueError as exc:
        print("REFUSED: %s" % exc)
        return 1
    code = {"HOLD": 0, "FLIP": 1, "NO-DATA": 2}[verdict]
    if args.check:
        if not os.path.isfile(args.out):
            print("%s: card %s is missing; generate it with: python3 scripts/runner_decision_card.py" % (verdict, args.out))
            return 3
        with open(args.out, encoding="utf-8") as fh:
            committed = fh.read()
        if strip_generated(committed) != strip_generated(text):
            print("%s: card %s is stale against %s; regenerate it with: python3 scripts/runner_decision_card.py"
                  % (verdict, args.out, path))
            return 3
        print("%s: card %s matches the regeneration from %s" % (verdict, args.out, path))
        return code
    write_whole(args.out, text)
    print("%s: wrote %s" % (verdict, args.out))
    return code


if __name__ == "__main__":
    sys.exit(main())
