#!/usr/bin/env python3
"""One DuckDB view over ~/.claude/evidence/unit-runs: every worker build as one row (unit, sub unit, unit class, run,
round, model, grade, refusal reason, probes, cost), and the four rollups the loop's decisions read: pass rate by model,
by unit class, by round, and the refusal reason histogram. Until 2026-09-22 every one of these was a grep over logs.
usage (repo root):  unit_ledger.py [--runs DIR] [--ledger FILE] [--plan FILE] [--out FILE.jsonl] [--db FILE.duckdb] [--since HOURS]
                    unit_ledger.py --selftest
The rows are written to --out (JSONL, stdlib only, works under every Python); the view and the rollups need duckdb, and a
Python without it prints NO-DATA and exits 3 after writing the rows (never a pass, never an empty table read as zero).
Cost: the dispatcher ledger's RECONCILE row whose holder is the build id and whose time falls inside the round's window
(jobs.json written to results.json written); a build with no such row carries null, never 0.
Model time (FX-10, 2026-09-29): model_seconds is the dispatcher ledger's RESERVE to its first close (RECONCILE, ABANDONED
or RELEASE) for the build's first reservation inside [jobs.json, results.json]; the RESERVE is written after the slot
wait, so it is the model's clock, never the queue's (seconds, the fan out's wall clock, includes the queue). model_clock
says which: span, none (no reservation: a codex or claude build, or a slot wait that ran out), open (no close yet) or
corrupt (a time that is missing, not a number, or not after its reserve); only span carries model_seconds."""
import argparse, fnmatch, json, math, os, re, sys, tempfile, time
RUNS = os.path.expanduser("~/.claude/evidence/unit-runs")
LEDGER = os.path.expanduser("~/.claude/brother-or-dispatch-state/openrouter-ledger.jsonl")
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
ROLLUPS = [("by model", "actual_model"), ("by unit class", "unit_class"), ("by round", "round"), ("by refusal reason", "refusal")]


def _mtime(p):
    try: return os.path.getmtime(p)
    except OSError: return 0.0


def _json(p):
    try:
        with open(p, encoding="utf-8") as fh: return json.load(fh)
    except (OSError, ValueError): return None


def unit_classes(plan):
    """unit id -> 'runner' (the plan names command runners for it), 'docs' (it owns only .md paths) or 'code'."""
    out = {}
    for u in (plan or {}).get("units", []) or []:
        if not isinstance(u, dict) or not u.get("id"): continue
        owns = [p for p in (u.get("owns") or []) if isinstance(p, str)]
        out[u["id"]] = "runner" if u.get("command_runners") else "docs" if owns and all(p.endswith(".md") for p in owns) else "code"
    return out


def grade_of(text):
    """(PASS|FAIL|NO-DATA, refusal reason or '') from a grader transcript: the LAST PASS or FAIL line decides, as
    grade_lane.sh reads it (grader lane F4, 2026-09-27: the first line let a PASS stand over a later FAIL), and a last
    verdict line that is not exactly PASS is NO-DATA, never a PASS."""
    # [ \t]*, never \s*: \s* crossed the newline, so "PASS" followed by "exit=0" read as the line "PASS exit=0"
    # exit=3 is the grader's own NO-DATA (no slot, no sandbox, a leg that never ran): never a FAIL (review 2026-10-03)
    if re.findall(r"^exit=(\d+)[ \t]*$", text or "", flags=re.M)[-1:] == ["3"]: return "NO-DATA", ""
    found = list(re.finditer(r"^(PASS|FAIL)\b:?[ \t]*(.*)$", text or "", flags=re.M))
    if not found: return "NO-DATA", ""
    m = found[-1]
    if m.group(1) == "PASS" and m.group(0).strip() != "PASS": return "NO-DATA", ""
    # digits become N so "1 patch problems" and "3 patch problems" are one reason class
    return m.group(1), (re.sub(r"\d+", "N", re.split(r"[:;(]", m.group(2))[0].strip())[:60] if m.group(1) == "FAIL" else "")


def fail_class(refusal):
    """The class of one grader refusal, from grade_of's short reason: CONTRACT (refused before any test ran), PROOF
    (tests prove nothing), else SUITE, the class diagnosed rather than fixed blind. ONE reader: the pool's admission
    and the pass pulse's loss ranking both call this (2026-09-27)."""
    s = refusal if isinstance(refusal, str) else ""
    if any(k in s for k in ("safety screen", "preflight", "patch problem", "find occurs")): return "CONTRACT"
    if "without the code" in s or "mutation" in s: return "PROOF"
    return "SUITE"


def grade_passed(path):
    """True only when the grade file at `path` passes by the rule grade_lane.sh reads (grader lane F4, 2026-09-27):
    its LAST line is exactly exit=0 and its LAST line starting PASS or FAIL is exactly PASS. Read bytes as bash sees
    them: no newline translation, and a NUL fails because grep then answers "Binary file ... matches". Unreadable or
    not UTF-8 is not a pass (stricter than bash, never looser). scripts/test_grade_ledger_and_probe_readers.py runs both readers
    over one table."""
    try:
        with open(path, encoding="utf-8", newline="") as fh: text = fh.read()
    except (OSError, UnicodeDecodeError):
        return False
    if "\0" in text: return False
    lines = (text[:-1] if text.endswith("\n") else text).split("\n")
    verdicts = [l for l in lines if l.startswith(("PASS", "FAIL"))]
    return lines[-1] == "exit=0" and bool(verdicts) and verdicts[-1] == "PASS"


def _merged(reader, ledger_path):
    """reader over the shared ledger plus every run's own ledger when ledger_path is the default (2026-09-27: each run
    spends in its own money root); an explicit ledger (a test, a replay) is read alone. Mappings are merged, counts summed."""
    paths = [ledger_path]
    if ledger_path == LEDGER:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import proof_ledger
        paths += proof_ledger.run_ledger_paths()
    got = [reader(p) for p in paths]
    merged = []
    for i in range(len(got[0])):
        if isinstance(got[0][i], dict):
            m = {}
            for g in got:
                for k, v in g[i].items(): m.setdefault(k, []).extend(v)
            merged.append(m)
        else:
            merged.append(sum(g[i] for g in got))
    return tuple(merged)


def costs(ledger_path):
    return _merged(_costs_one, ledger_path)


def costs_counted(ledger_path):
    return _merged(_costs_counted_one, ledger_path)


def _costs_one(ledger_path):
    """(holder id -> [(at, actual_cost)] from RECONCILE rows, holder id -> [(at, estimated_cost)]
    from ABANDONED rows), both joined to their RESERVE's holder; unreadable rows are skipped.

    ABANDONED liability (R3, independent review of RS1, 2026-09-26): a dead holder's estimate,
    unknown truth, kept in its OWN mapping so it is never mistaken for a measured RECONCILE cost
    and never silently dropped just because a later, unrelated payment exists for the same
    holder."""
    holder, estimate, out, owed = {}, {}, {}, {}
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                try: r = json.loads(line)
                except ValueError: continue
                if not isinstance(r, dict): continue
                if r.get("type") == "RESERVE" and isinstance(r.get("reservation_id"), str):
                    holder[r["reservation_id"]] = r.get("holder_id")
                    estimate[r["reservation_id"]] = r.get("estimated_cost")
                elif r.get("type") == "RECONCILE" and isinstance(r.get("actual_cost"), (int, float)):
                    h = holder.get(r.get("reservation_id"))
                    if h: out.setdefault(h, []).append((float(r.get("at") or 0), float(r["actual_cost"])))
                elif r.get("type") == "ABANDONED":
                    rid = r.get("reservation_id")
                    h, est = holder.get(rid), estimate.get(rid)
                    if h and isinstance(est, (int, float)) and not isinstance(est, bool):
                        owed.setdefault(h, []).append((float(r.get("at") or 0), float(est)))
    except (OSError, TypeError, ValueError): pass
    return out, owed


def spans(ledger_path):
    """THE MODEL'S OWN CLOCK (FX-10): (holder id -> [(reserve_at, close_at, close_type)], rows skipped), one entry per
    RESERVE, closed by its FIRST RECONCILE, ABANDONED or RELEASE row (close_type None while it is open). Merged over the
    run ledgers exactly like costs(); a time is kept as written and judged by model_clock(), never repaired here."""
    return _merged(_spans_one, ledger_path)


def _spans_one(ledger_path):
    if not isinstance(ledger_path, str) or not os.path.isfile(ledger_path):
        return {}, 0
    reserved, closed, skipped = {}, {}, 0
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if not isinstance(r, dict):
                    skipped += 1
                    continue
                rid = r.get("reservation_id")
                if r.get("type") == "RESERVE":
                    hid = r.get("holder_id")
                    if not isinstance(rid, str) or not rid or not isinstance(hid, str) or not hid:
                        skipped += 1
                        continue
                    reserved.setdefault(rid, (hid, r.get("at")))
                elif r.get("type") in ("RECONCILE", "ABANDONED", "RELEASE") and isinstance(rid, str):
                    closed.setdefault(rid, (r.get("at"), r["type"]))   # the first close wins, as the ledger itself rules
    except (OSError, ValueError):
        skipped += 1
    out = {}
    for rid, (hid, at) in reserved.items():
        close_at, close_type = closed.get(rid, (None, None))
        out.setdefault(hid, []).append((at, close_at, close_type))
    return out, skipped


def _clock_time(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def model_clock(entries, t0, t_res):
    """(model_seconds, model_clock, reservations) of one build from its spans() entries: the FIRST reservation whose
    reserve time lies inside [t0, t_res] (the build call itself; a self check repair reusing the id after results.json,
    and an earlier round or run reusing it before jobs.json, are never joined). A reservation whose time cannot be placed
    types the build corrupt when nothing else is in the window; a close that is missing is open; a span that is not a
    finite number above zero is corrupt. Only span carries a number, so no unknown can ever read as fast."""
    placed = sorted((e for e in entries if _clock_time(e[0]) and t0 <= e[0] <= t_res), key=lambda e: e[0])
    if not placed:
        return None, ("corrupt" if any(not _clock_time(e[0]) for e in entries) else "none"), 0
    at, close_at, close_type = placed[0]
    if close_type is None:
        return None, "open", len(placed)
    if not _clock_time(close_at) or not close_at - at > 0:
        return None, "corrupt", len(placed)
    return round(close_at - at, 1), "span", len(placed)


def _finite(v):
    '''True only for a real, finite, non negative number; a bool is not a cost.'''
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0


def _costs_counted_one(ledger_path):
    '''The strict cost reader: holder id -> [(at, cost)], holder id -> [(at, estimated_cost)] for a
    dead holder's ABANDONED liability (R3, independent review of RS1, 2026-09-26: unknown truth,
    still real, never hidden behind a measured payment for the same holder), and the number of
    corrupt rows skipped.'''
    if not isinstance(ledger_path, str) or not os.path.isfile(ledger_path):
        return {}, {}, 0
    holder, estimate, out, owed, skipped = {}, {}, {}, {}, 0
    try:
        with open(ledger_path, encoding='utf-8') as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if not isinstance(r, dict):
                    skipped += 1
                    continue
                if r.get('type') == 'RESERVE':
                    rid, hid = r.get('reservation_id'), r.get('holder_id')
                    if not isinstance(rid, str) or not rid or not isinstance(hid, str) or not hid:
                        skipped += 1
                        continue
                    holder[rid] = hid
                    estimate[rid] = r.get('estimated_cost')
                elif r.get('type') == 'RECONCILE':
                    rid = r.get('reservation_id')
                    if not isinstance(rid, str):
                        skipped += 1
                        continue
                    if not _finite(r.get('actual_cost')):
                        skipped += 1
                        continue
                    if 'at' in r:
                        at = r.get('at')
                        if not _finite(at):
                            skipped += 1
                            continue
                    else:
                        at = 0.0
                    h = holder.get(rid)
                    if h:
                        out.setdefault(h, []).append((float(at), float(r['actual_cost'])))
                elif r.get('type') == 'ABANDONED':
                    rid = r.get('reservation_id')
                    if not isinstance(rid, str):
                        skipped += 1
                        continue
                    if 'at' in r:
                        at = r.get('at')
                        if not _finite(at):
                            skipped += 1
                            continue
                    else:
                        at = 0.0
                    h, est = holder.get(rid), estimate.get(rid)
                    if h and _finite(est):
                        owed.setdefault(h, []).append((float(at), float(est)))
    except (OSError, ValueError):
        skipped += 1
    return out, owed, skipped


def blended_usd_detail(sub, runs_root, ledger_path=LEDGER):
    '''The sub unit blended USD over its run folders, and the count of corrupt rows or folders skipped.'''
    if not isinstance(sub, str) or not sub:
        return None, 0
    if not isinstance(runs_root, str) or not os.path.isdir(runs_root):
        return None, 0
    try:
        names = os.listdir(runs_root)
    except OSError:
        return None, 0
    cost, owed, skipped = costs_counted(ledger_path)
    held = spans(ledger_path)[0]
    total, seen = 0.0, False
    pattern = re.escape(sub) + r'-\d{6}'
    for name in names:
        if not isinstance(name, str):
            continue
        if not re.fullmatch(pattern, name):
            continue
        full = os.path.join(runs_root, name)
        if not os.path.isdir(full):
            continue
        try:
            rs = run_rows(full, {}, {}, cost, owed, held)
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            skipped += 1
            continue
        for r in rs:
            if isinstance(r, dict) and r.get('cost_usd') is not None:
                try:
                    total += float(r['cost_usd'])
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                seen = True
    return (total if seen else None), skipped


def native_usd(sub, runs_root, claude_path):
    """(usd or None, unknown, calls, untagged): the Claude spend of this sub unit's run folders, joined BY IDENTITY (2026-10-04):
    each folder's RUN-TAG holds the unit_run tag (<folder>@<claim epoch>) claude_ledger.start wrote on every call its
    runner caused (the native session, its retries, helper calls in the runner's own process tree), and finish copied
    onto the terminal row, so nothing is attributed by a time window and an untagged historical row is never counted.
    Limit, stated: a call made outside the runner's process tree (a finisher repair, the diag lane started by the
    pass) carries no tag and is not in this figure; the run level tally still counts it. untagged counts this sub
    unit's run folders with no RUN-TAG (runs from before 2026-10-04): their Claude spend is not in usd, and the caller
    says so rather than printing a total that silently leaves them out (review round 3). unknown is the count of this sub unit's calls with no
    usable cost (uncosted, still in flight, or a ledger that could not be read): any of them makes the figure NO-DATA
    for the caller, never a smaller number. usd is None when no call is tagged to this sub unit at all. The provider
    ledger (blended_usd_detail) and this one hold disjoint calls, so adding the two never counts a call twice."""
    if not isinstance(sub, str) or not sub:
        return None, 0, 0, 0   # no sub unit id names no runner: nothing to attribute, and the caller prints plain NO-DATA
    if not isinstance(runs_root, str) or not isinstance(claude_path, str):
        return None, 1, 0, 0
    try:
        names = [n for n in os.listdir(runs_root) if isinstance(n, str) and re.fullmatch(re.escape(sub) + r'-\d{6}', n)]
    except OSError:
        return None, 1, 0, 0
    if not os.path.exists(claude_path):
        return None, 0, 0, 0   # no Claude ledger: no Claude call was ever registered here
    import claude_ledger
    usd, unknown, calls, untagged = 0.0, 0, 0, 0
    for name in sorted(names):
        # the exact tag the runner wrote beside its folder: a folder with none (a run from before 2026-10-04) has no
        # tagged call, and a reused folder name never inherits a pruned run's rows (review 2026-10-04)
        try:
            with open(os.path.join(runs_root, name, "RUN-TAG"), encoding="utf-8") as fh:
                tag = fh.read().strip()
        except FileNotFoundError:
            untagged += 1
            continue
        except OSError:
            return None, 1, calls, untagged
        if not claude_ledger.UNIT_RUN.fullmatch(tag) or not tag.startswith(name + "@"):
            return None, 1, calls, untagged   # a tag that is not this folder's own is unknown, never counted
        t = claude_ledger.tally(claude_path, unit_run=tag)
        # a fault anywhere in the ledger (a duplicate or orphan row, an unreadable line) means no row of it can be
        # counted exactly once, so the whole figure is unknown, never a partial sum (claude_ledger.tally's own rule)
        if t.get("error") or t.get("usd") is None or any(t.get(k) for k in ("unreadable_rows", "duplicate_starts", "duplicate_done", "orphan_done")):
            return None, 1, calls, untagged
        # uncosted already counts a START closed by an unusable cost; invalid_done only adds terminals with no START,
        # which orphan_done refuses above, so adding it would count one call twice (measured: 2 for 1)
        calls += t["calls"]; usd += t["usd"]; unknown += t["uncosted"] + t["inflight"]
    return (usd if calls else None), unknown, calls, untagged


def blended_usd(sub, runs_root, ledger_path=LEDGER):
    '''The sub unit blended USD, or None when no row carries a cost.'''
    return blended_usd_detail(sub, runs_root, ledger_path)[0]


def probe_verdicts(round_dir):
    """variant -> verdict from probes.table (its last column); {} when the round has none."""
    out = {}
    try:
        with open(os.path.join(round_dir, "probes.table"), encoding="utf-8") as fh: lines = fh.read().splitlines()[1:]
    except OSError: return out
    for l in lines:
        f = l.split()
        if len(f) >= 3: out[f[1]] = f[-1]
    return out


def _context(ledger_path, plan_path):
    plan = _json(plan_path) or {}
    by_sub = {s: u["id"] for u in plan.get("units", []) or [] if isinstance(u, dict) for s in (u.get("sub_units") or [])}
    cost, owed = costs(ledger_path)
    return by_sub, unit_classes(plan), cost, owed, spans(ledger_path)[0]


def run_dirs(root):
    """Every run folder under root as "<path>/", which is what glob("*-*/") returned, except that an unreadable root
    RAISES (finding 8, 2026-09-27: glob swallowed a permission error, so a digest over a folder holding a READY build
    printed zero of everything and exited 0). A root that does not exist yet holds no runs: []."""
    try:
        with os.scandir(root) as it: entries = list(it)
    except FileNotFoundError:
        return []
    return [e.path + "/" for e in entries if "-" in e.name and not e.name.startswith(".") and e.is_dir()]


def rows(runs_dir=RUNS, ledger_path=LEDGER, plan_path=PLAN, since_hours=None):
    """Rows of every run folder, oldest folder first by MODIFICATION TIME, never by name: the name carries HHMMSS
    only, which wraps at midnight and repeats on a reused name. An unreadable runs folder raises OSError."""
    by_sub, classes, cost, owed, held = _context(ledger_path, plan_path); floor = time.time() - since_hours * 3600 if since_hours else 0.0
    out = []
    for run in sorted(run_dirs(runs_dir), key=lambda p: (_mtime(p), p)):
        run = run.rstrip("/")
        if _mtime(run) >= floor: out += run_rows(run, by_sub, classes, cost, owed, held)
    return out


def run_rows(run, by_sub, classes, cost, liability=None, spans=None):
    """The rows of ONE run folder (<sub>-HHMMSS): one per build per round. liability (R3, 2026-09-26)
    is holder id -> [(at, estimated_cost)] for a dead holder's ABANDONED reservation: kept as its
    own abandoned_cost_usd figure on the row, beside cost_usd, never merged into it and never
    hidden by it. Optional and defaulting to none found, so a caller that predates this stays
    correct (it just shows no abandoned figure, never an invented one). spans (FX-10) is spans()'s mapping, optional in
    the same way: without it every row says model_clock none, never an invented model time."""
    liability = liability or {}
    spans = spans or {}
    out = []
    if True:
        run = run.rstrip("/"); run_at = _mtime(run)
        sub = re.sub(r"-\d{6}$", "", os.path.basename(run)); unit = by_sub.get(sub, sub.split(".")[0].split("-")[0])
        status = ""
        try:
            with open(os.path.join(run, "STATUS"), encoding="utf-8") as fh: status = (fh.read().split() or [""])[0]
        except FileNotFoundError: status = "RUNNING"      # no STATUS yet is a live run; an unreadable one raises
        # listdir, never glob: an unreadable run folder raises rather than reading as a run with no rounds (finding 8's class)
        try: names = os.listdir(run)
        except FileNotFoundError: names = []
        rounds = sorted((os.path.join(run, n) for n in names if fnmatch.fnmatch(n, "round[0-9]*")), key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
        # A REPAIR WAVE FOLDER (review 2026-09-23) has out/, grades/ and results.json at its top and no round dirs: it is one round.
        wave = not rounds and os.path.isfile(os.path.join(run, "results.json"))
        if wave: rounds = [run]
        for i, rd in enumerate(rounds):
            rn = 0 if wave else int(re.sub(r"\D", "", os.path.basename(rd)) or 0)
            results = _json(os.path.join(rd, "results.json")); jobs = _json(os.path.join(rd, "jobs.json"))
            if not isinstance(results, list): continue
            est = {j.get("id"): j.get("estimated_cost") for j in (jobs or []) if isinstance(j, dict)}
            # THE WINDOW ENDS AT THE BUILD'S LAST RESULTS FILE, AND BEFORE THE NEXT ROUND (X3 finding 3, 2026-09-27). It
            # ended 120 s after results.json, but self_check repairs a build under the SAME id and writes its own
            # results-selfcheck.json, often minutes later, so the repair's payment fell outside and the row and the
            # blended cost understated paid work (2.0 for 5.0 paid). A build that file lists ends its window there. The
            # next round reuses the build ids, so a window never reaches past the next round's jobs.json (a payment
            # counted in two rows is the same understatement's mirror). Out of scope, named: a fan out killed between a
            # job's payment and its results row, more than 120 s before the latest results file.
            t0, t_res = _mtime(os.path.join(rd, "jobs.json")), _mtime(os.path.join(rd, "results.json"))
            repair = _json(os.path.join(rd, "results-selfcheck.json"))
            repaired = {j.get("id") for j in repair if isinstance(j, dict)} if isinstance(repair, list) else set()
            t_rep = _mtime(os.path.join(rd, "results-selfcheck.json"))
            nxt = _mtime(os.path.join(rounds[i + 1], "jobs.json")) if i + 1 < len(rounds) else 0.0
            nxt = nxt or float("inf")
            probes = probe_verdicts(rd)
            for r in results:
                if not isinstance(r, dict) or not r.get("id"): continue
                bid = r["id"]
                if wave: sub = re.sub(r"-r\d+$", "", bid); unit = by_sub.get(sub, sub.split(".")[0].split("-")[0])
                gpath = os.path.join(rd, "grades", bid + ".txt")
                try:
                    with open(gpath, encoding="utf-8") as fh: grade, refusal = grade_of(fh.read())
                except (OSError, UnicodeDecodeError): grade, refusal = "NO-DATA", ""
                # ONE PASS RULE (X3 finding 4, 2026-09-27): grade_of reads the verdict line and ignores the exit record,
                # so a transcript the grader refused (PASS, then exit=1) became a ledger PASS and a worker reward while
                # grade_lane.sh and probe_wave refused it. A PASS stands only where grade_passed, grade_lane.sh's rule,
                # says so; any other PASS is NO-DATA (the grader's answer is unknown, never a FAIL charged to the worker).
                if grade == "PASS" and not grade_passed(gpath): grade = "NO-DATA"
                t1 = max(t_res, t_rep if bid in repaired else 0.0) + 120
                inside = lambda at: t0 <= at <= t1 and at < nxt   # one window for the payments and the liability
                paid = [c for at, c in cost.get(bid, []) if inside(at)]
                owed = [c for at, c in liability.get(bid, []) if inside(at)]
                m_s, m_clock, m_n = model_clock(spans.get(bid, []), t0, t_res)
                out.append({"unit": unit, "sub": sub, "unit_class": classes.get(unit, "code"), "run": os.path.basename(run), "run_at": run_at,
                            "status": status, "rounds": len(rounds), "round": rn, "build": bid, "model": r.get("model"),
                            # AN UNKNOWN ANSWERER STAYS UNKNOWN (R3, review 2026-09-26): "or r.get('model')" used to
                            # replace a missing actual_model with the REQUESTED model, inventing an answering identity
                            # nobody observed. A failed fan-out row with no actual_model is unknown, never the ask.
                            "actual_model": r.get("actual_model"),
                            "ok": bool(r.get("ok")), "seconds": r.get("seconds"), "bytes": r.get("bytes"), "grade": grade, "refusal": refusal,
                            # THE MODEL'S OWN CLOCK (FX-10): seconds above is the fan out's wall clock, slot wait included
                            "model_seconds": m_s, "model_clock": m_clock, "reservations": m_n,
                            # WHY THE CALL FAILED, as the fan out recorded it (2026-09-27): the worker mix charges an empty answer
                            # to the arm asked, and must not charge it for the run's own refusals (budget, drain, stop)
                            "call_error": str(r.get("error") or "")[:120] if not r.get("ok") else "",
                            # EVERY PAYMENT, never the last one (finding 3, 2026-09-27): a self check repair reuses the build
                            # id, so one build can carry two reconciled payments inside its round window
                            "probes": probes.get(bid, "NO-DATA"), "estimated_cost": est.get(bid), "cost_usd": sum(paid) if paid else None,
                            "abandoned_cost_usd": sum(owed) if owed else None, "at": time.time()})
    return out


def append_run(run_dir, out_path, ledger_path=LEDGER, plan_path=PLAN):
    """ONE APPEND AT THE SOURCE (fix 8, 2026-09-22): called by grade_lane.sh after every grade, so the ledger grows as
    the loop runs and no reader needs a rebuild. Rows already present for this run (same run, round, build) are not
    written twice; a folder that is not a unit run (<sub>-HHMMSS under the runs dir) is refused with NO-DATA."""
    run_dir = os.path.abspath(run_dir.rstrip("/"))
    if not os.path.isdir(run_dir) or not (re.search(r"-\d{6}$", os.path.basename(run_dir)) or os.path.isfile(os.path.join(run_dir, "results.json"))):
        print("NO-DATA: %s is not a unit run or wave folder; nothing appended" % run_dir); return 3
    by_sub, classes, cost, owed, held = _context(ledger_path, plan_path)
    try: new = run_rows(run_dir, by_sub, classes, cost, owed, held)
    except OSError as exc: print("NO-DATA: %s cannot be read (%s); nothing appended" % (run_dir, exc)); return 3
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    import fcntl
    # ONE WRITER AT A TIME, AND ANY CHANGE SUPERSEDES THE ROW BEFORE IT. The file stays append only; every reader takes
    # the LAST row per (run, round, build) through last_rows(). A row is appended when anything but its own write
    # stamp differs from the last one on file (finding 4, 2026-09-27: only a grade leaving NO-DATA refreshed it, so a
    # later status and a later ABANDONED liability never reached the ledger), and the rows reach the disk BEFORE the
    # lock is released (finding 7: they sat in the buffer past the unlock, a second writer read an empty ledger and
    # appended the same build again).
    sig = lambda r: {k: v for k, v in r.items() if k != "at"}
    with open(out_path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX); fh.seek(0); have = {}
        for line in fh:
            try: r = json.loads(line)
            except ValueError: continue
            if isinstance(r, dict) and r.get("run") == os.path.basename(run_dir): have[(r.get("round"), r.get("build"))] = sig(r)
        add = [r for r in new if have.get((r["round"], r["build"])) != json.loads(json.dumps(sig(r), sort_keys=True))]
        fh.seek(0, 2)
        for r in add: fh.write(json.dumps(r, sort_keys=True) + "\n")
        fh.flush(); os.fsync(fh.fileno())
        fcntl.flock(fh, fcntl.LOCK_UN)
    print("APPEND %d new row(s) of %d for %s -> %s" % (len(add), len(new), os.path.basename(run_dir), out_path)); return 0


def last_rows(path):
    """The ledger's rows, dict rows only, the LAST one per (run, round, build) winning: the file is append only and a
    later row supersedes the one before it. THE ONE DEDUPE every reader of this ledger routes through (worker_mix
    too, finding 7, 2026-09-27: two physical rows of one build counted as two rewards). A row with no build id has
    no identity to collapse on and is kept as it is. Unreadable file, or a path that is not text: []."""
    if not isinstance(path, str):
        return []
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                try: r = json.loads(line)
                except ValueError: continue
                if isinstance(r, dict): out[(r.get("run"), r.get("round"), r.get("build")) if r.get("build") is not None else i] = r
    except OSError: return []
    return list(out.values())


def write_rows(rs, out_path):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for r in rs: fh.write(json.dumps(r, sort_keys=True) + "\n")


def rollups(out_path, db_path):
    """Create or refresh the view in db_path over out_path and return {title: [(key, builds, passes, pass_rate, cost)]}.
    Raises ImportError when duckdb is absent: the caller says NO-DATA."""
    import duckdb  # noqa: the whole point of the tool; absent means NO-DATA, decided by the caller
    con = duckdb.connect(db_path)
    # a CREATE VIEW cannot take a prepared parameter (duckdb binder), so the path is quoted by hand
    con.execute("CREATE OR REPLACE VIEW unit_runs AS SELECT * FROM read_json_auto('%s', format='newline_delimited')" % os.path.abspath(out_path).replace("'", "''"))
    out = {}
    for title, col in ROLLUPS:
        where = " WHERE grade = 'FAIL' AND refusal <> ''" if col == "refusal" else ""
        out[title] = con.execute("SELECT CAST(%s AS VARCHAR) k, count(*) n, sum(CASE WHEN grade='PASS' THEN 1 ELSE 0 END) p, "
                                 "round(100.0 * sum(CASE WHEN grade='PASS' THEN 1 ELSE 0 END) / count(*), 1) rate, round(coalesce(sum(TRY_CAST(cost_usd AS DOUBLE)), 0), 2) usd "
                                 "FROM (SELECT * FROM unit_runs QUALIFY row_number() OVER (PARTITION BY run, round, build ORDER BY coalesce(\"at\", 0) DESC) = 1)%s GROUP BY 1 ORDER BY n DESC, k" % (col, where)).fetchall()
    con.close(); return out


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--runs", default=RUNS); ap.add_argument("--ledger", default=LEDGER); ap.add_argument("--plan", default=PLAN)
    ap.add_argument("--out", default=os.path.expanduser("~/.claude/evidence/unit-ledger.jsonl")); ap.add_argument("--db", default=os.path.expanduser("~/.claude/evidence/unit-ledger.duckdb"))
    ap.add_argument("--since", type=float, default=None, help="hours; rows from runs newer than this only"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--append-run", default=None, help="one unit run folder: append its rows to --out (grade_lane.sh calls this)")
    ap.add_argument("--rebuild", action="store_true", help="rewrite --out from every run folder on disk (the default only reads the record: rotated run folders would be lost)")
    a = ap.parse_args(argv)
    if a.selftest: return selftest()
    if a.append_run: return append_run(a.append_run, a.out, a.ledger, a.plan)
    if a.since is not None: a.out = re.sub(r"\.jsonl$", "", a.out) + ".since.jsonl"   # never truncate the append grown record with a window
    if a.rebuild or a.since is not None or not os.path.isfile(a.out):
        # an unreadable runs folder is NO-DATA, and the append grown record is never truncated to its empty reading
        try: rs = rows(a.runs, a.ledger, a.plan, a.since)
        except OSError as exc: print("NO-DATA: the runs folder %s cannot be read (%s); nothing written" % (a.runs, exc)); return 3
        write_rows(rs, a.out)
    else:
        rs = last_rows(a.out)
    print("ROWS %d build(s) over %d run(s) -> %s" % (len(rs), len({r["run"] for r in rs}), a.out))
    if not rs: print("NO-DATA: no build under %s" % a.runs); return 3
    try: tables = rollups(a.out, a.db)
    except ImportError: print("NO-DATA: duckdb is not importable under %s; the rows are written, the view is not (/usr/bin/python3 carries duckdb)" % sys.executable); return 3
    print("VIEW unit_runs in %s" % a.db)
    for title, table in tables.items():
        print("\n%-24s %6s %6s %6s %8s" % (title.upper(), "builds", "pass", "rate%", "usd"))
        for k, n, p, rate, usd in table[:12]: print("%-24s %6d %6d %6s %8.2f" % ((k or "")[:24], n, p, rate, usd))
    return 0


def selftest():
    """A case that RAISES has not reported a verdict (the estate's selftest law, measured 2026-09-22): anything that
    escapes is printed as a FAILED verdict naming the exception, and the exit code still says 1."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1


def _selftest_body():
    d = tempfile.mkdtemp(prefix="unit-ledger-"); runs = os.path.join(d, "runs"); t = time.time()
    def build(sub, folder, rn, bid, model, grade_text, probe=None, ok=True):
        rd = os.path.join(runs, "%s-%s" % (sub, folder), "round%d" % rn); os.makedirs(os.path.join(rd, "grades"), exist_ok=True)
        t0 = t - 1000 + 300 * rn                              # each round has its own window, as real rounds do
        with open(os.path.join(rd, "jobs.json"), "w") as fh: json.dump([{"id": bid, "model": model, "estimated_cost": 0.02}], fh)
        os.utime(os.path.join(rd, "jobs.json"), (t0, t0))
        with open(os.path.join(rd, "results.json"), "w") as fh: json.dump([{"id": bid, "model": model, "ok": ok, "actual_model": model + "/v", "seconds": 5.0, "bytes": 10}], fh)
        os.utime(os.path.join(rd, "results.json"), (t0 + 50, t0 + 50))
        if grade_text is not None:
            with open(os.path.join(rd, "grades", bid + ".txt"), "w") as fh: fh.write(grade_text)
        if probe:
            with open(os.path.join(rd, "probes.table"), "w") as fh: fh.write("lane variant probes CRASH ACCEPT? fenced verdict\n%s %s 4 0 0 True %s\n" % (sub, bid, probe))
        with open(os.path.join(runs, "%s-%s" % (sub, folder), "STATUS"), "w") as fh: fh.write("READY x\n")
    build("D1.1", "000001", 0, "D1.1-r0", "deepseek", "APPLY 1 edits\nFAIL: suite not green with the code\nexit=1\n")
    build("D1.1", "000001", 1, "D1.1-r0", "deepseek", "APPLY 1 edits\nPASS\nexit=0\n", probe="CLEAN")   # the shape grade_one.sh writes
    build("D2.1", "000002", 0, "D2.1-r0", "muse", "FAIL safety screen: imports subprocess; read it by hand\n")
    build("D2.1", "000002", 1, "D2.1-r1", "muse", None)
    build("D3.1", "000003", 0, "D3.1-r0", "muse", "APPLY 1 edits\nRED-WITHOUT-CODE yes\n")          # killed before its verdict line
    build("D3.1", "000003", 1, "D3.1-r0", "muse", "FAIL: 3 patch problems\n")
    plan = os.path.join(d, "plan.json"); ledger = os.path.join(d, "ledger.jsonl")
    with open(plan, "w") as fh: json.dump({"units": [{"id": "D1", "sub_units": ["D1.1"], "command_runners": ["scripts/x.py"]}, {"id": "D2", "sub_units": ["D2.1"], "owns": ["docs/a.md"]}]}, fh)
    with open(ledger, "w") as fh:
        fh.write(json.dumps({"type": "RESERVE", "reservation_id": "D1.1-r0-a", "holder_id": "D1.1-r0", "at": t - 990}) + "\n")
        fh.write(json.dumps({"type": "RECONCILE", "reservation_id": "D1.1-r0-a", "actual_cost": 0.03, "at": t - 980}) + "\n")
        fh.write(json.dumps({"type": "RESERVE", "reservation_id": "D1.1-r0-old", "holder_id": "D1.1-r0", "at": t - 90000}) + "\n")
        fh.write(json.dumps({"type": "RECONCILE", "reservation_id": "D1.1-r0-old", "actual_cost": 9.99, "at": t - 89000}) + "\n")
        fh.write("not json\n")
    rs = rows(runs, ledger, plan); by = {(r["sub"], r["round"]): r for r in rs}
    cases = [("one row per build across rounds", len(rs) == 6),
             ("a grade transcript with no PASS or FAIL line is NO-DATA, never PASS", by[("D3.1", 0)]["grade"] == "NO-DATA"),
             ("digits in a reason are normalised so patch problem counts are one class", by[("D3.1", 1)]["refusal"] == "N patch problems"),
             ("a transcript with one verdict line is graded by it", by[("D1.1", 0)]["grade"] == "FAIL" and by[("D1.1", 1)]["grade"] == "PASS"),
             # THE LAST VERDICT LINE DECIDES (grader lane F4, 2026-09-27), as grade_lane.sh reads it: a PASS followed by a
             # later FAIL is a FAIL, a FAIL retried to a PASS is a PASS, and a last line that is not exactly PASS is no PASS.
             ("a later FAIL overrides an earlier PASS", grade_of("PASS\nre-run after the probe\nFAIL: gate refused\nexit=1\n") == ("FAIL", "gate refused")),
             ("a later PASS overrides an earlier FAIL", grade_of("FAIL: 2 patch problems\nrepaired\nPASS\nexit=0\n") == ("PASS", "")),
             ("a last verdict line that is not exactly PASS is NO-DATA, never PASS", grade_of("FAIL: x\nPASS with warnings\n") == ("NO-DATA", "")),
             ("the refusal reason is the FAIL line's head, never the PASS row's", by[("D1.1", 0)]["refusal"] == "suite not green with the code" and by[("D1.1", 1)]["refusal"] == ""),
             ("a reason with detail is cut at its first colon", by[("D2.1", 0)]["refusal"] == "safety screen"),
             ("a build with no grade file is NO-DATA, never FAIL or PASS", by[("D2.1", 1)]["grade"] == "NO-DATA"),
             ("unit class from the plan: runner and docs", by[("D1.1", 0)]["unit_class"] == "runner" and by[("D2.1", 0)]["unit_class"] == "docs"),
             ("cost joins the RECONCILE inside the round window only, never the old one", by[("D1.1", 0)]["cost_usd"] == 0.03 and by[("D1.1", 1)]["cost_usd"] is None),
             ("probe verdict read from the table, NO-DATA without one", by[("D1.1", 1)]["probes"] == "CLEAN" and by[("D1.1", 0)]["probes"] == "NO-DATA"),
             ("the round count and status travel with every row", by[("D2.1", 1)]["rounds"] == 2 and by[("D2.1", 1)]["status"] == "READY"),
             ("an empty runs dir yields no rows", rows(os.path.join(d, "none"), ledger, plan) == []),
             ("an unreadable plan still yields rows, class code", rows(runs, ledger, os.path.join(d, "no-plan.json"))[0]["unit_class"] == "code")]
    out = os.path.join(d, "rows.jsonl"); write_rows(rs, out)
    ap_out = os.path.join(d, "append.jsonl"); r1 = os.path.join(runs, "D1.1-000001")
    cases += [("append_run writes a run's rows once, and a second call adds nothing", append_run(r1, ap_out, ledger, plan) == 0 and append_run(r1, ap_out, ledger, plan) == 0
               and sum(1 for _ in open(ap_out)) == 2),
              ("a build appended before its grade is superseded once graded, and last_rows reads the graded row", (lambda: (
                  append_run(os.path.join(runs, "D2.1-000002"), ap_out, ledger, plan), open(os.path.join(runs, "D2.1-000002", "round1", "grades", "D2.1-r1.txt"), "w").write("PASS\nexit=0\n"),
                  append_run(os.path.join(runs, "D2.1-000002"), ap_out, ledger, plan)))() and [r["grade"] for r in last_rows(ap_out) if r["build"] == "D2.1-r1" and r["round"] == 1] == ["PASS"]),
              ("a wave folder (results.json at its top, no round dirs) is one round zero, its sub unit read from the build id", (lambda w: (build("W", "x", 0, "L9.9-r0", "muse", "PASS\n"), os.rename(os.path.join(runs, "W-x", "round0"), w), None)[2] or
                  append_run(w, ap_out, ledger, plan) == 0 and [r["sub"] for r in last_rows(ap_out) if r["build"] == "L9.9-r0"] == ["L9.9"])(os.path.join(runs, "or-wave9"))),
              ("a bare scalar line in the dispatcher ledger is skipped", (lambda: (open(ledger, "a").write("123\n"), costs(ledger)[0])[1])().get("D1.1-r0") is not None),
              ("append_run refuses a folder that is not a unit run with NO-DATA and writes nothing", (lambda n0: append_run(d, ap_out, ledger, plan) == 3 and sum(1 for _ in open(ap_out)) == n0)(sum(1 for _ in open(ap_out)))),
              ("rows() over the runs dir equals the union of run_rows()", len(rows(runs, ledger, plan)) == len(rs) + 1)]   # plus the wave folder planted above
    # R3 (independent review of RS1, 2026-09-26): an unknown answering model must never be
    # replaced with the requested one, and an abandoned liability must stay visible beside a
    # measured payment for the SAME holder, never hidden behind it.
    wave3 = os.path.join(runs, "U1.1-999999"); os.makedirs(wave3)
    with open(os.path.join(wave3, "jobs.json"), "w") as fh:
        json.dump([{"id": "U1.1-r0", "estimated_cost": 9.5}], fh)
    with open(os.path.join(wave3, "results.json"), "w") as fh:
        json.dump([{"id": "U1.1-r0", "model": "requested-only", "actual_model": None, "ok": False, "error": "BudgetExceeded: over the cap"},
                   {"id": "U1.1-r1", "model": "requested-only", "actual_model": "m/v", "ok": True, "error": "a stale note"}], fh)
    t0w = _mtime(os.path.join(wave3, "jobs.json"))
    lp2 = os.path.join(d, "r3-ledger.jsonl")
    with open(lp2, "w") as fh:
        fh.write(json.dumps({"type": "RESERVE", "reservation_id": "r3-a", "holder_id": "U1.1-r0", "estimated_cost": 9.5, "at": t0w}) + "\n")
        fh.write(json.dumps({"type": "ABANDONED", "reservation_id": "r3-a", "at": t0w + 1}) + "\n")
        fh.write(json.dumps({"type": "RESERVE", "reservation_id": "r3-b", "holder_id": "U1.1-r0", "estimated_cost": 0.2, "at": t0w}) + "\n")
        fh.write(json.dumps({"type": "RECONCILE", "reservation_id": "r3-b", "actual_cost": 0.2, "at": t0w + 2}) + "\n")
    cost3, owed3 = costs(lp2)
    cases.append(("costs() keeps a measured payment", cost3.get("U1.1-r0") == [(t0w + 2, 0.2)]))
    cases.append(("costs() surfaces abandoned liability beside it, for the same holder", owed3.get("U1.1-r0") == [(t0w + 1, 9.5)]))
    cost3c, owed3c, skip3c = costs_counted(lp2)
    cases.append(("costs_counted() agrees with costs() on abandoned liability", owed3c.get("U1.1-r0") == [(t0w + 1, 9.5)] and skip3c == 0))
    r3rows = run_rows(wave3, {}, {}, cost3, owed3)
    cases.append(("an unknown answerer stays unknown, never the requested model", r3rows[0]["actual_model"] is None))
    cases.append(("a failed call's row carries why it failed; an answered call's row carries nothing",
                  r3rows[0]["call_error"] == "BudgetExceeded: over the cap" and r3rows[1]["call_error"] == ""))
    cases.append(("a measured payment and an abandoned liability are both visible on the same row",
                  r3rows[0]["cost_usd"] == 0.2 and r3rows[0]["abandoned_cost_usd"] == 9.5))
    cases.append(("run_rows without a liability argument still works and shows no abandoned figure",
                  run_rows(wave3, {}, {}, cost3)[0]["abandoned_cost_usd"] is None))
    r3known = run_rows(wave3, {}, {}, {}, {})
    cases.append(("a build with no known payment at all keeps cost_usd null, never invented",
                  r3known[0]["cost_usd"] is None and r3known[0]["abandoned_cost_usd"] is None))
    cases.append(("a plan unit that is not a record is skipped, never raised", unit_classes({"units": ["junk", {"id": "X"}]}) == {"X": "code"}))
    # FINDING 3, 2026-09-27: a self check repair reuses the build id, so one build carries two payments inside its
    # round window. The row kept the last one only, so the row and the blended cost understated paid work.
    two = os.path.join(runs, "P1.1-000004"); build("P1.1", "000004", 0, "P1.1-r0", "deepseek", "PASS\n")
    t0p = _mtime(os.path.join(two, "round0", "jobs.json")); lp3 = os.path.join(d, "two-payments.jsonl")
    with open(lp3, "w") as fh:
        for rid, paid, at in (("orig", 2, t0p + 1), ("selfcheck", 3, t0p + 10)):
            fh.write(json.dumps({"type": "RESERVE", "reservation_id": rid, "holder_id": "P1.1-r0", "at": at}) + "\n")
            fh.write(json.dumps({"type": "RECONCILE", "reservation_id": rid, "actual_cost": paid, "at": at + 1}) + "\n")
    cases.append(("two payments for one build are summed on its row and in its blended cost, never the last one only",
                  run_rows(two, {}, {}, costs(lp3)[0])[0]["cost_usd"] == 5 and blended_usd("P1.1", runs, lp3) == 5))
    # X3 FINDING 3, 2026-09-27: the window ended 120 s after results.json, and a self check repair (same build id, its
    # own results-selfcheck.json) finishing minutes later was left out: 2.0 for 5.0 paid. One fixture per guard.
    def paid_rows(path, rows_):
        with open(path, "w") as fh:
            for rid, holder, paid, at in rows_:
                fh.write(json.dumps({"type": "RESERVE", "reservation_id": rid, "holder_id": holder, "at": at - 1}) + "\n")
                fh.write(json.dumps({"type": "RECONCILE", "reservation_id": rid, "actual_cost": paid, "at": at}) + "\n")
        return path
    def repair_file(rd, ids, when):
        with open(os.path.join(rd, "results-selfcheck.json"), "w") as fh: json.dump([{"id": i, "ok": True} for i in ids], fh)
        os.utime(os.path.join(rd, "results-selfcheck.json"), (when, when))
    slow = os.path.join(runs, "S1.1-000008"); build("S1.1", "000008", 0, "S1.1-r0", "deepseek", "PASS\nexit=0\n")
    t08 = _mtime(os.path.join(slow, "round0", "jobs.json")); repair_file(os.path.join(slow, "round0"), ["S1.1-r0"], t08 + 350)
    lp8 = paid_rows(os.path.join(d, "slow-repair.jsonl"), [("orig", "S1.1-r0", 2, t08 + 40), ("repair", "S1.1-r0", 3, t08 + 350)])
    cases.append(("a self check repair finishing 300 s after the round's results is still counted on the row and in the blended cost",
                  run_rows(slow, {}, {}, costs(lp8)[0])[0]["cost_usd"] == 5 and blended_usd("S1.1", runs, lp8) == 5))
    other = os.path.join(runs, "S2.1-000009"); build("S2.1", "000009", 0, "S2.1-r0", "deepseek", "PASS\nexit=0\n")
    t09 = _mtime(os.path.join(other, "round0", "jobs.json")); repair_file(os.path.join(other, "round0"), ["S2.1-r7"], t09 + 350)
    lp9 = paid_rows(os.path.join(d, "not-repaired.jsonl"), [("orig", "S2.1-r0", 2, t09 + 40), ("later", "S2.1-r0", 3, t09 + 350)])
    cases.append(("a build the repair file does not list keeps its own window: a later payment under its id is not its cost",
                  run_rows(other, {}, {}, costs(lp9)[0])[0]["cost_usd"] == 2))
    nxt = os.path.join(runs, "S3.1-000010"); build("S3.1", "000010", 0, "S3.1-r0", "deepseek", "FAIL: x\nexit=1\n")
    build("S3.1", "000010", 1, "S3.1-r0", "deepseek", "PASS\nexit=0\n"); t10 = _mtime(os.path.join(nxt, "round0", "jobs.json"))
    os.utime(os.path.join(nxt, "round1", "jobs.json"), (t10 + 110, t10 + 110))   # the next round starts 60 s after this round's results
    lp10 = paid_rows(os.path.join(d, "next-round.jsonl"), [("r0", "S3.1-r0", 2, t10 + 40), ("r1", "S3.1-r0", 3, t10 + 120)])
    by10 = {r["round"]: r["cost_usd"] for r in run_rows(nxt, {}, {}, costs(lp10)[0])}
    cases.append(("the next round's payment under the same build id is counted in that round only, never twice",
                  by10 == {0: 2, 1: 3} and blended_usd("S3.1", runs, lp10) == 5))
    # X3 FINDING 4, 2026-09-27: ingestion read grade_of, which ignores the exit record, so a transcript the grader refused
    # (PASS, then exit=1) became a ledger PASS and a worker reward. At the entry point grade_lane.sh calls.
    refused = os.path.join(runs, "G1.1-000011"); build("G1.1", "000011", 0, "G1.1-r0", "deepseek", "PASS\nexit=1\n")
    ap11 = os.path.join(d, "refused-append.jsonl")
    cases.append(("a PASS the grader refused with exit=1 is never a ledger PASS",
                  append_run(refused, ap11, ledger, plan) == 0 and [r["grade"] for r in last_rows(ap11)] == ["NO-DATA"]))
    # FX-10: the model's own clock, reserve to first close, for the build's own reservation only. One fixture each.
    clk = os.path.join(runs, "M1.1-000012"); build("M1.1", "000012", 0, "M1.1-r0", "deepseek", "PASS\nexit=0\n")
    t12 = _mtime(os.path.join(clk, "round0", "jobs.json")); lp12 = os.path.join(d, "clock.jsonl")
    def clock_of(rows_):
        with open(lp12, "w") as fh:
            for r_ in rows_: fh.write(json.dumps(r_) + "\n")
        r_ = run_rows(clk, {}, {}, {}, None, spans(lp12)[0])[0]
        return r_["model_seconds"], r_["model_clock"], r_["reservations"]
    rsv = lambda at, rid="k": {"type": "RESERVE", "reservation_id": rid, "holder_id": "M1.1-r0", "at": at}
    rec = lambda at, rid="k": {"type": "RECONCILE", "reservation_id": rid, "actual_cost": 0.01, "at": at}
    cases += [("model clock: the span is reserve to close, never the fan out's seconds", clock_of([rsv(t12 + 5), rec(t12 + 35)]) == (30.0, "span", 1)),
              ("model clock: an open reservation is open, never a sample", clock_of([rsv(t12 + 5)]) == (None, "open", 1)),
              ("model clock: a close before its reserve is corrupt, never a sample", clock_of([rsv(t12 + 5), rec(t12 + 1)]) == (None, "corrupt", 1)),
              ("model clock: an earlier round's reservation under a reused id is not joined", clock_of([rsv(t12 - 500), rec(t12 - 400)]) == (None, "none", 0))]
    # FINDING 4, 2026-09-27: append_run refreshed a row only when its grade left NO-DATA, so a later status and a
    # later ABANDONED liability never reached the ledger the loop reads.
    late = os.path.join(runs, "Q1.1-000005"); build("Q1.1", "000005", 0, "Q1.1-r0", "deepseek", "PASS\n")
    t0q = _mtime(os.path.join(late, "round0", "jobs.json")); lp4 = os.path.join(d, "late.jsonl"); ap4 = os.path.join(d, "late-append.jsonl")
    with open(lp4, "w") as fh: fh.write(json.dumps({"type": "RESERVE", "reservation_id": "q", "holder_id": "Q1.1-r0", "estimated_cost": 9, "at": t0q + 1}) + "\n")
    with open(os.path.join(late, "STATUS"), "w") as fh: fh.write("RUNNING\n")
    append_run(late, ap4, lp4, plan)
    with open(os.path.join(late, "STATUS"), "w") as fh: fh.write("LANDED /b/Q1.1.json c0ffee\n")
    with open(lp4, "a") as fh: fh.write(json.dumps({"type": "ABANDONED", "reservation_id": "q", "at": t0q + 20}) + "\n")
    append_run(late, ap4, lp4, plan)
    got4 = [r for r in last_rows(ap4) if r["build"] == "Q1.1-r0"]
    cases.append(("a later status and a later ABANDONED liability reach an already graded ledger row",
                  len(got4) == 1 and got4[0]["status"] == "LANDED" and got4[0]["abandoned_cost_usd"] == 9))
    cases.append(("an unchanged run appends nothing on a second call", (lambda n: append_run(late, ap4, lp4, plan) == 0
                  and sum(1 for _ in open(ap4)) == n)(sum(1 for _ in open(ap4)))))
    # FINDING 7, 2026-09-27: the appender unlocked BEFORE its buffered rows reached the file, so a second writer
    # read an empty ledger and appended the same build again. A child appender pauses right after its unlock.
    import subprocess
    race = os.path.join(runs, "R1.1-000006"); build("R1.1", "000006", 0, "R1.1-r0", "deepseek", "PASS\n")
    ap7, sig = os.path.join(d, "race-append.jsonl"), os.path.join(d, "unlocked")
    child = ("import fcntl, os, sys, time\nsys.path.insert(0, sys.argv[1])\nimport unit_ledger\nreal = fcntl.flock\n"
             "def pause(fd, op):\n    real(fd, op)\n    if op == fcntl.LOCK_UN:\n        open(sys.argv[6], 'w').close(); end = time.monotonic() + 30\n"
             "        while not os.path.exists(sys.argv[6] + '.go'):\n            assert time.monotonic() < end\n            time.sleep(0.02)\n"
             "fcntl.flock = pause\nunit_ledger.append_run(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])\n")
    proc = subprocess.Popen([sys.executable, "-B", "-c", child, os.path.dirname(os.path.abspath(__file__)), race, ap7, ledger, plan, sig],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.monotonic() + 30
        while not os.path.exists(sig) and proc.poll() is None and time.monotonic() < end: time.sleep(0.02)
        at_unlock = sum(1 for _ in open(ap7)) if os.path.exists(ap7) else -1
        append_run(race, ap7, ledger, plan)
    finally:
        open(sig + ".go", "w").close()
        try: child_rc = proc.wait(timeout=60)
        except subprocess.TimeoutExpired: proc.kill(); child_rc = proc.wait()
    cases.append(("an appender flushes before it unlocks, so a concurrent writer never appends the same build twice",
                  child_rc == 0 and at_unlock == 1 and sum(1 for _ in open(ap7)) == 1))
    # (b) of 2026-09-27: run folders are ordered by modification time, never by name. HHMMSS wraps at midnight, so the
    # name puts a run made at 00:00:01 before one made at 23:59:59 the evening before.
    order = os.path.join(d, "order"); os.makedirs(order)
    for name, when in (("O1.1-235959", t - 7200), ("O1.1-000001", t - 60)):
        os.makedirs(os.path.join(order, name, "round0", "grades"))
        with open(os.path.join(order, name, "round0", "results.json"), "w") as fh: json.dump([{"id": name + "-b"}], fh)
        os.utime(os.path.join(order, name), (when, when))
    cases.append(("rows() orders run folders by modification time, never by the HHMMSS in their name",
                  [r["run"] for r in rows(order, ledger, plan)] == ["O1.1-235959", "O1.1-000001"]))
    # FINDING 8's class in this module: glob swallowed an unreadable folder. A rebuild over an unreadable runs folder
    # must say NO-DATA and leave the append grown record as it was, never truncate it to its empty reading; an
    # unreadable run folder appends nothing and says NO-DATA, never "APPEND 0 of 0".
    locked, kept = os.path.join(d, "locked-runs"), os.path.join(d, "kept.jsonl")
    os.makedirs(os.path.join(locked, "K1.1-000007")); write_rows([{"run": "K1.1-000007", "round": 0, "build": "K1.1-r0"}], kept)
    before_kept = open(kept).read()
    os.chmod(locked, 0)
    try: rebuild_rc = main(["--runs", locked, "--ledger", ledger, "--plan", plan, "--out", kept, "--db", os.path.join(d, "k.duckdb"), "--rebuild"])
    finally: os.chmod(locked, 0o700)
    cases.append(("a rebuild over an unreadable runs folder is NO-DATA and never truncates the record", rebuild_rc == 3 and open(kept).read() == before_kept))
    # two guards, one fixture each: a STATUS that exists and cannot be read, then a folder that can be entered (its
    # STATUS opens) but not listed (its rounds cannot be read)
    status7 = os.path.join(race, "STATUS"); os.chmod(status7, 0)
    try: status_rc = append_run(race, os.path.join(d, "locked-status.jsonl"), ledger, plan)
    finally: os.chmod(status7, 0o600)
    cases.append(("an unreadable STATUS appends nothing and says NO-DATA, never RUNNING", status_rc == 3 and not os.path.exists(os.path.join(d, "locked-status.jsonl"))))
    os.chmod(race, 0o100)
    try: list_rc = append_run(race, os.path.join(d, "locked-append.jsonl"), ledger, plan)
    finally: os.chmod(race, 0o700)
    cases.append(("a run folder whose rounds cannot be listed appends nothing and says NO-DATA", list_rc == 3 and not os.path.exists(os.path.join(d, "locked-append.jsonl"))))
    try:
        tb = rollups(out, os.path.join(d, "t.duckdb"))
        m = {k: (n, p) for k, n, p, rate, usd in tb["by model"]}
        cases += [("view: pass rate by model counts builds and passes", m.get("deepseek/v") == (2, 1) and m.get("muse/v") == (4, 0)),
                  ("view: refusal histogram lists only FAIL rows with a reason", sorted(k for k, *_ in tb["by refusal reason"]) == ["N patch problems", "safety screen", "suite not green with the code"]),
                  ("view: cost sums what was reconciled", [usd for k, n, p, rate, usd in tb["by unit class"] if k == "runner"] == [0.03])]
        # cost_usd arrives as JSON shaped or text in some rows (Claude builds carry no per build cost; arm A 2026-09-23 rows
        # made duckdb infer JSON and sum() raised a BinderException): the rollup casts, never crashes, and sums the numbers
        with open(out, "a") as fh:
            fh.write(json.dumps({"run": "X-1", "round": 0, "build": "X-r9", "unit": "X", "model": "muse/v", "grade": "FAIL", "refusal": "", "cost_usd": {"note": "none"}, "at": 1.0}) + "\n")
            fh.write(json.dumps({"run": "X-1", "round": 0, "build": "X-r8", "unit": "X", "model": "muse/v", "grade": "FAIL", "refusal": "", "cost_usd": "0.02", "at": 1.0}) + "\n")
        try:
            tb2 = rollups(out, os.path.join(d, "t2.duckdb")); ok2 = abs(sum(usd for k, n, p, rate, usd in tb2["by model"]) - 0.05) < 1e-6
        except Exception as exc:   # sbe: allow-silent the exception IS the case under test, named in the case title
            ok2 = False; print("rollup raised on mixed cost_usd: %s" % exc)
        cases.append(("view: a JSON shaped or text cost_usd never crashes the rollup and numbers still sum", ok2))
    except ImportError:
        print("selftest: duckdb not importable under %s: the view cases are NO-DATA here (run under /usr/bin/python3 for them)" % sys.executable)
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__": sys.exit(main())
