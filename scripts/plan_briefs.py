#!/usr/bin/env python3
"""PG1.e: one brief per sub unit from the writer batch file, dispatched at full width, the report filed by script.

    python3 scripts/plan_briefs.py --assignments docs/plan/specs/<V>/WRITER-ASSIGNMENTS.json --out docs/plan/specs/<V>/briefs/
                                   [--root DIR] [--preamble FILE]
    python3 scripts/plan_briefs.py --file-report <SUB> --assignments FILE [--root DIR]  < writer-final-message.txt

The first form writes docs/plan/specs/<V>/briefs/<SUB>.md for every batch and prints the wave table. The writer
self scores with scripts/loop/spec_score.py --no-claim (PG1.d) and stops at exit 0 or after three rounds. The second
form files the one fenced REPORT block of a writer's last message to the batch's report path. Reader of the batch
file scripts/plan_gen.py (PG1.b) writes.

FAIL DIRECTION: a batch whose sub unit has no section in its spec refuses every brief (nothing is written); a width
that cannot be read is a refusal, never unlimited; a report with no block, two blocks, an unclosed block or an empty
block is never filed; a report is never overwritten with other bytes. Exit 0 done, 1 refused, 2 NO-DATA (no
assignments file, no batch to dispatch). Python 3.9, standard library only. Tests: scripts/test_plan_briefs.py.
"""
import argparse
import json
import math
import os
import re
import sys
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plan_seed  # noqa: E402

REPORT_FENCE = "REPORT"
WIDTH_ENV = "BROTHER_WRITER_WIDTH"
DEFAULT_WIDTH = 4  # the wave of four that did not trip the account limit on 2026-10-14; the limit itself is UNVERIFIED
LIMIT_STATUS = "UNVERIFIED"  # replace by hand with 'measured <source>' once a run records a refusal or a reading
WAVE_SECONDS = 190  # one writer on one sub unit, measured 2026-10-14 (PG1 spec, W2)
CHUNK_BYTES = 10 * 1024  # a Write call over about 10 KB was refused as OVERSIZED on 2026-10-14
MAX_REPORT_BYTES = 200000
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_FENCE = re.compile(r"^ {0,3}```\s*(\S*)\s*$")


def width(batches: int, env: Optional[dict] = None) -> int:
    """min(batches, BROTHER_WRITER_WIDTH) where the env value is a positive int; unset means 4 (the wave of four
    that did not trip the account limit on 2026-10-14, the limit itself UNVERIFIED); anything else raises ValueError."""
    if isinstance(batches, bool) or not isinstance(batches, int) or batches < 0:
        raise ValueError("the batch count must be a non negative integer, got %r" % (batches,))
    raw = (os.environ if env is None else env).get(WIDTH_ENV)
    if raw is None:
        cap = DEFAULT_WIDTH
    elif isinstance(raw, str) and re.fullmatch(r"[0-9]{1,6}", raw) and int(raw) > 0:
        cap = int(raw)
    else:
        raise ValueError("%s=%r is not a positive integer" % (WIDTH_ENV, raw))
    return min(batches, cap)


def section_of(spec_text: str, sub: str) -> Optional[str]:
    """The text of the '### <sub> ...' section of a unit spec, up to the next '## ' or '### ' heading; None when the
    spec has no such heading."""
    lines = spec_text.split("\n")
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^### " + re.escape(sub) + r"(\s|$)", line):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^#{2,3} ", lines[j]):
            end = j
            break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def brief_text(batch: dict, preamble: str, spec_text: str) -> str:
    """The brief for one batch: the preamble, the sub unit's section (headings and done check frozen), the cached
    reads pasted, the rule 'self score with spec_score.py --no-claim <unit> after every edit and stop at exit 0 or
    after three rounds', the 10 KB chunk rule, the no HH:MM rule, and the REPORT block contract. A batch that is
    not a record, names an id that is not plain, or names a sub unit with no section in the spec raises ValueError."""
    if not isinstance(batch, dict) or not isinstance(batch.get("id"), str) or not ID_RE.match(batch["id"]):
        raise ValueError("a batch without a plain id: %r" % (batch.get("id") if isinstance(batch, dict) else batch,))
    subs, units = batch.get("sub_units"), batch.get("units")
    if not (isinstance(subs, list) and subs and all(isinstance(s, str) for s in subs)
            and isinstance(units, list) and units and all(isinstance(u, str) for u in units)):
        raise ValueError("batch %s names no sub units or no units" % batch["id"])
    if not isinstance(preamble, str) or not preamble.strip() or not isinstance(spec_text, str):
        raise ValueError("batch %s: the preamble or the spec text is missing" % batch["id"])
    sections, missing = [], []
    for sub in subs:
        sec = section_of(spec_text, sub)
        (sections if sec else missing).append(sec or sub)
    if missing:
        raise ValueError("batch %s: no section in the spec for sub unit %s" % (batch["id"], ", ".join(missing)))
    cached = batch.get("cached_reads") or []
    reads = []
    for r in cached:
        if isinstance(r, dict):
            reads.append("%s%s\n%s" % (r.get("path", "?"), " line %s" % r["line"] if "line" in r else "", r.get("text", "NO-DATA")))
    score = "python3 scripts/loop/spec_score.py --no-claim " + " ".join(units)
    report_path = str(batch.get("report") or "REPORT-%s.md" % batch["id"])
    out = [preamble.rstrip(), "",
           "## Your batch: %s (%s)" % (batch["id"], batch.get("name") or ", ".join(subs)), "",
           "Sub units: %s. Write ONLY: %s." % (", ".join(subs), ", ".join(str(w) for w in batch.get("writes") or [])), "",
           "## Your sections, frozen (keep every heading and every Done check line exactly; fill every WRITER: line)", "",
           "\n".join(sections), ""]
    if reads:
        out += ["## Cached reads (quoted when the plan was generated; read nothing else first)", "", "\n\n".join(reads), ""]
    out += ["## Rules that bind you",
            "- SELF SCORE: after every edit run `%s`. Exit 0 means every scored sub unit is at 9 or above: stop. Exit 1 prints the "
            "misses by name: fix them and run it again. Stop after three rounds even when it still exits 1. It records no claim, "
            "so it is safe to run; this line overrides any preamble line that forbids spec_score." % score,
            "- CHUNKS: no single Write call over %d bytes (a larger call is refused as OVERSIZED); write the file in chunks of at most "
            "%d bytes and append the rest with Edit." % (CHUNK_BYTES, CHUNK_BYTES),
            "- CLOCK: never write a clock time as hours, a colon and minutes (a hook refuses it); write 15h20 style times.",
            "- ASCII only, no em dash and no en dash, no credit to any model or tool.", "",
            "## Your last message (the REPORT contract)",
            "End with exactly ONE fenced block opened by a line holding three backticks and the word %s, and closed by a line of "
            "three backticks. Its text is filed as %s by script. At most 40 lines. The first line is `DONE: <score per sub unit>` "
            "when the last self score exited 0, else `HELD: <the misses by name>`. Then: sub units written, anything in the plan "
            "wrong or impossible in the code with file:line, any owner decision needed. No second block anywhere in the message." % (
                REPORT_FENCE, report_path), ""]
    return "\n".join(out)


def waves(batches: List[dict], w: int) -> List[List[dict]]:
    """The batches in wave order, w per wave, the longest units first inside a wave (critical path first)."""
    if isinstance(w, bool) or not isinstance(w, int) or w < 1:
        raise ValueError("a wave width must be a positive integer, got %r" % (w,))
    ordered = sorted(batches, key=lambda b: -len(b.get("sub_units") or []))  # stable: ties keep file order
    return [ordered[i:i + w] for i in range(0, len(ordered), w)]


def extract_report(message: str) -> str:
    """The text inside the one fenced REPORT block of a writer's final message; no block, two blocks, a block never
    closed, an empty block or an oversized one raises ValueError."""
    if not isinstance(message, str):
        raise ValueError("the message is %s, not text" % type(message).__name__)
    lines, opens, inside, body, closed = message.split("\n"), 0, False, [], False
    for line in lines:
        m = _FENCE.match(line)
        if not inside:
            if m and m.group(1) == REPORT_FENCE:
                opens += 1
                inside = opens == 1
        elif m and m.group(1) == "":
            inside, closed = False, True
        else:
            if m and m.group(1) == REPORT_FENCE:
                opens += 1  # a second REPORT opener inside the block is a second block
            body.append(line)
    if opens == 0:
        raise ValueError("the message holds no fenced %s block" % REPORT_FENCE)
    if opens > 1:
        raise ValueError("the message holds %d fenced %s blocks, exactly one is filed" % (opens, REPORT_FENCE))
    if not closed:
        raise ValueError("the %s block is opened and never closed" % REPORT_FENCE)
    text = "\n".join(body).strip("\n")
    if not text.strip():
        raise ValueError("the %s block is empty" % REPORT_FENCE)
    if len(text.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("the %s block is over %d bytes" % (REPORT_FENCE, MAX_REPORT_BYTES))
    return text + "\n"


def file_report(message: str, path: str) -> str:
    """Writes extract_report(message) to path (never overwrites a different report: same bytes pass, other bytes
    refuse) and returns the path."""
    text = extract_report(message)
    data = text.encode("utf-8")
    tmp = "%s.tmp-%d" % (path, os.getpid())
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        try:
            os.link(tmp, path)  # fails when the report exists: the first filing is never replaced
        except FileExistsError:
            with open(path, "rb") as f:
                if f.read() != data:
                    raise ValueError("%s holds a different report: not overwritten" % path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def _load(path: str) -> Dict[str, object]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    batches = data.get("batches") if isinstance(data, dict) else None
    if not isinstance(batches, list) or not all(isinstance(b, dict) for b in batches):
        raise ValueError("%s holds no list of batches" % path)
    return data


def _spec_for(batch: dict, root: str) -> str:
    sec = batch.get("section")
    rels = [sec["file"]] if isinstance(sec, dict) and isinstance(sec.get("file"), str) else \
        ["docs/plan/specs/%s.md" % u for u in (batch.get("units") or []) if isinstance(u, str)]
    if not rels:
        raise ValueError("batch %s names no spec file" % batch.get("id"))
    parts = []
    for rel in rels:
        full = plan_seed._safe_path(root, rel)
        if full is None:
            raise ValueError("batch %s: spec path %r leaves the root" % (batch.get("id"), rel))
        with open(full, encoding="utf-8") as f:
            parts.append(f.read())
    return "\n".join(parts)


def main(argv: Optional[List[str]] = None, stdin=None) -> int:
    ap = argparse.ArgumentParser(description="PG1.e: writer briefs and report filing")
    ap.add_argument("--assignments", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--preamble", default=None)
    ap.add_argument("--file-report", default=None, metavar="SUB")
    try:
        args = ap.parse_args(argv)
        args.root = os.path.abspath(args.root)  # a relative root broke the lint overlay (2026-10-14)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    try:
        data = _load(args.assignments)
    except OSError as exc:
        print("NO-DATA: the assignments file %s cannot be read (%s)" % (args.assignments, type(exc).__name__))
        return 2
    except ValueError as exc:
        print("REFUSED: %s" % exc)
        return 1
    batches = data["batches"]
    try:
        if args.file_report is not None:
            hit = [b for b in batches if b.get("id") == args.file_report]
            if len(hit) != 1 or not isinstance(hit[0].get("report"), str):
                raise ValueError("no single batch with id %r and a report path in %s" % (args.file_report, args.assignments))
            target = plan_seed._safe_path(args.root, hit[0]["report"])
            if target is None:
                raise ValueError("report path %r leaves the root" % hit[0]["report"])
            print("REPORT FILED: %s" % file_report((stdin or sys.stdin).read(), target))
            return 0
        if not batches:
            print("WAVES: 0 (nothing to dispatch)")
            return 2
        if args.out is None:
            raise ValueError("--out DIR is required to write briefs")
        pre = data.get("preamble") if isinstance(data.get("preamble"), str) else None
        if args.preamble:
            with open(args.preamble, encoding="utf-8") as f:
                pre = f.read()
        elif pre and plan_seed._safe_path(args.root, pre) and os.path.isfile(plan_seed._safe_path(args.root, pre)):
            with open(plan_seed._safe_path(args.root, pre), encoding="utf-8") as f:
                pre = f.read()
        pre = pre or (data.get("rule") if isinstance(data.get("rule"), str) else "")
        ids = [b.get("id") for b in batches]
        if len(set(map(str, ids))) != len(ids):
            raise ValueError("two batches share an id")
        briefs = {b["id"] if isinstance(b.get("id"), str) else "?": brief_text(b, pre, _spec_for(b, args.root)) for b in batches}
        w = width(len(batches))
    except OSError as exc:
        print("NO-DATA: %s" % exc)
        return 2
    except ValueError as exc:
        print("REFUSED: %s" % exc)
        return 1
    os.makedirs(args.out, exist_ok=True)
    for bid, text in briefs.items():
        with open(os.path.join(args.out, bid + ".md"), "w", encoding="utf-8") as f:
            f.write(text)
    table = waves(batches, w)
    print("WAVES: %d width %d (limit %s)" % (len(table), w, LIMIT_STATUS))
    for i, wave in enumerate(table, 1):
        print("WAVE %d: %s (about %d s per wave, W2, 2026-10-14)" % (i, ", ".join(b["id"] for b in wave), WAVE_SECONDS))
    print("ARITHMETIC: ceil(%d / %d) = %d waves x about %d s = about %d s" % (
        len(batches), w, len(table), WAVE_SECONDS, len(table) * WAVE_SECONDS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
