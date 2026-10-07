#!/usr/bin/env python3
"""Claude token use in a time window, from every Claude Code transcript on this machine (subagent files included).
usage: claude_window.py START END [--only DIR_SUBSTRING] [--session ID] [--exclude ID,ID] [--calls]
  START/END 'YYYY-MM-DD HH:MM[:SS]' local. --only keeps project folders whose name contains the substring (loop children
  run in model-call-* folders; arm C in its worktree's folder). --session keeps one session (the orchestrator's own).
  --calls reconciles against ~/.claude/evidence/claude-calls.jsonl (one row per loop Claude call, written BEFORE the
  call): calls with no usage record (killed stragglers, crashes) are COUNTED and their input tokens ESTIMATED from their
  prompt size at the measured chars-per-token ratio of the calls that did record. Estimates are labelled, never merged.
Tokens are the record; USD is applied later from a checked price table. Unreadable files are counted, never skipped."""
import datetime, glob, json, os, sys

def when(s):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try: return datetime.datetime.strptime(s, f).astimezone()
        except ValueError: pass
    raise SystemExit("bad time %r" % s)

def opt(argv, k):
    return argv[argv.index(k) + 1] if k in argv else None

def main(argv):
    start, end = when(argv[1]), when(argv[2])
    only, sess = opt(argv, "--only"), opt(argv, "--session")
    excl = set((opt(argv, "--exclude") or "").split(",")) - {""}
    tot, seen, sessions, unreadable, sess_model = {}, set(), {}, 0, {}
    for f in glob.glob(os.path.expanduser("~/.claude/projects/**/*.jsonl"), recursive=True):
        proj = os.path.relpath(f, os.path.expanduser("~/.claude/projects")).split(os.sep)[0]
        if only and only not in proj: continue
        try:
            if datetime.datetime.fromtimestamp(os.path.getmtime(f)).astimezone() < start: continue
            fh = open(f, encoding="utf-8")
        except OSError:
            unreadable += 1; continue
        with fh:
            for line in fh:
                if '"usage"' not in line: continue
                try: d = json.loads(line)
                except ValueError: continue
                m = d.get("message") or {}; u, ts, sid = m.get("usage"), d.get("timestamp"), d.get("sessionId")
                if not u or not ts or sid in excl or (sess and sid != sess): continue
                t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if not (start <= t < end): continue
                key = (m.get("id") or d.get("uuid"), sid)
                if key in seen: continue
                seen.add(key); sessions[sid] = proj; sess_model.setdefault(sid, m.get("model") or "unknown")
                cc = u.get("cache_creation") or {}
                w1h, w5m = cc.get("ephemeral_1h_input_tokens"), cc.get("ephemeral_5m_input_tokens")
                cw = u.get("cache_creation_input_tokens") or 0
                if w1h is None and w5m is None: w1h, w5m = 0, cw     # no split recorded: the 5 minute rate, the lower bound
                r = tot.setdefault(m.get("model") or "unknown", {"input": 0, "cache_write_1h": 0, "cache_write_5m": 0, "cache_read": 0, "output": 0, "messages": 0})
                r["input"] += u.get("input_tokens") or 0; r["cache_write_1h"] += w1h or 0; r["cache_write_5m"] += w5m or 0
                r["cache_read"] += u.get("cache_read_input_tokens") or 0; r["output"] += u.get("output_tokens") or 0; r["messages"] += 1
    out = {"start": argv[1], "end": argv[2], "only": only, "session": sess, "sessions": len(sessions), "unreadable": unreadable, "by_model": tot}
    if "--calls" in argv:
        rows = []
        try:
            for line in open(os.path.expanduser("~/.claude/evidence/claude-calls.jsonl"), encoding="utf-8"):
                try: c = json.loads(line)
                except ValueError: continue
                t = datetime.datetime.strptime(c["at"], "%Y-%m-%dT%H:%M:%S").astimezone()
                if start <= t < end: rows.append(c)
        except OSError:
            out["calls"] = "NO-DATA: claude-calls.jsonl unreadable"
        else:
            known = set(json.load(open(os.path.expanduser("~/.claude/model-registry.json"))).get("models", {}).get(k, {}).get("id") for k in json.load(open(os.path.expanduser("~/.claude/model-registry.json"))).get("models", {}))
            real = [c for c in rows if c.get("model") in known]      # selftest rows name fake ids (claude-x): excluded, counted
            calc = {}
            for mdl in sorted({c["model"] for c in real}):
                # calls are START rows; a done row is the same call's cost, never a second call (claude_ledger.py)
                n_log = sum(1 for c in real if c["model"] == mdl and c.get("event") != "done"); n_rec = sum(1 for s, mm in sess_model.items() if mm == mdl)
                v = tot.get(mdl); miss = max(0, n_log - n_rec)
                per = {k: round(v[k] / n_rec) for k in ("input", "cache_write_1h", "cache_write_5m", "cache_read", "output")} if (v and n_rec) else None
                calc[mdl] = {"logged_calls": n_log, "sessions_with_usage": n_rec, "calls_without_usage": miss,
                             "ESTIMATE_per_missing_call_mean_of_recorded": per if miss else None}
            out["calls"] = {"excluded_non_registry_rows": len(rows) - len(real), "by_model": calc}
    print(json.dumps(out, indent=1))

if __name__ == "__main__":
    sys.exit(main(sys.argv))
