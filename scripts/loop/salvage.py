#!/usr/bin/env python3
"""Nothing the loop paid for is lost: find every build that passed the grader and never landed, and say why.

WHY (measured 2026-09-22). A night of 40 passes landed nothing, and 15 builds sat on disk that were grader PASS and
probe CLEAN, for five sub units, stopped only by a checker that vetoed 17 of 17. Nothing listed them, so the money
read as wasted until a human went looking, and nothing could bring them back except hand editing a STATUS file.

usage (repo root): salvage.py list                 every unlanded grader PASS build: probes, checker, APPLIES or STALE, duplicates
                   salvage.py promote              per unlanded sub unit with no live runner and no READY build, mark its NEWEST
                                                   build that is probe CLEAN and still APPLIES as READY (old STATUS kept beside it)
                   salvage.py --selftest
promote decides NOTHING about quality: land_batch's gates (status, council, spec score, both Pythons, fuzz, battery)
still judge the build, exactly as for a build a runner marked READY. It only undoes a parking that the evidence on
disk contradicts. In checker GATE mode a build whose verdict file says FIX or NO-DATA is left alone: the gate's word
stands while it is a gate. Fail direction: anything unreadable (plan, lane log, probe verdict, build json) is
skipped and named as NO-DATA, never promoted. Exit 0 always for list and promote: a reporter must not end a pass.
Roots are overridable for tests: SALVAGE_RUNS, SALVAGE_PLAN.
"""
import fcntl, glob, json, os, re, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # THIS file's own directory: the copies installed beside it
from runner_pool import admissible
import plan_store  # noqa: E402  (the one landed test every loop reader shares: plan_store.sub_landed)

PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"


def runs_dir():
    return os.environ.get("SALVAGE_RUNS") or os.path.expanduser("~/.claude/evidence/unit-runs")


def landed_subs(plan):
    out = set()
    for u in plan.get("units") or []:
        ev = u.get("evidence") or ""
        for s in u.get("sub_units") or []:
            sid = s["id"] if isinstance(s, dict) else s
            if plan_store.sub_landed(sid, ev):
                out.add(sid)
    return out


def all_subs(plan, scope=None):
    """Every sub unit id of the plan; with a scope regex (BROTHER_SCOPE, the pool's own knob) only units it matches."""
    rx = re.compile(scope) if scope else None
    return {(s["id"] if isinstance(s, dict) else s) for u in plan.get("units") or []
            if rx is None or rx.search(str(u.get("id") or "")) for s in u.get("sub_units") or []}


def candidates(runs, subs):
    """[{sub, build, run, round_at, probes, checker}] for every round whose lane log names a grader PASS build."""
    out = []
    for lane in glob.glob(os.path.join(runs, "*-*", "round*", "lane.log")):
        rd = os.path.dirname(lane); run = os.path.dirname(rd)
        sub = re.sub(r"-\d{6}$", "", os.path.basename(run))
        if sub not in subs:
            continue
        try:
            with open(lane, encoding="utf-8", errors="replace") as f:
                m = re.search(r"PASS (\S+)", f.read())
        except OSError:   # sbe: allow-silent an unreadable lane log means this round proved nothing; it is not a candidate
            continue
        if not m:
            continue
        build = os.path.join(rd, "out", m.group(1) + "-build.json")
        if not os.path.isfile(build):
            continue
        try:
            with open(os.path.join(rd, "probes", "logs", sub + ".done"), encoding="utf-8") as f:
                probes = f.read().strip() or "NO-DATA"
        except FileNotFoundError:
            # NOT-RUN only when no adversary log exists either (review 2026-10-04): probe_wave writes each <sub>-<m>.log,
            # CRASH lines included, BEFORE .done, so a wave killed mid way leaves findings and no .done. That is not
            # "never ran"; it stays NO-DATA and is left, as the runner refuses a CRASH finding.
            probes = "NO-DATA" if glob.glob(os.path.join(rd, "probes", "logs", glob.escape(sub) + "-*.log")) else "NOT-RUN"
        except OSError:
            probes = "NO-DATA"
        try:
            with open(build + ".check.json", encoding="utf-8") as f:
                checker = str((json.load(f) or {}).get("verdict") or "NO-DATA")
        except (OSError, ValueError, AttributeError):
            checker = "none"
        out.append({"sub": sub, "build": build, "run": run, "at": os.path.getmtime(lane), "probes": probes, "checker": checker})
    return sorted(out, key=lambda c: (c["sub"], -c["at"]))


def applies(build, preflight):
    """'' when the build still applies to the tree in the current directory, else the first reason it does not."""
    try:
        with open(build, encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, ValueError) as exc:
        return "NO-DATA build json unreadable (%s)" % type(exc).__name__
    if not isinstance(b, dict):
        return "NO-DATA build json is not a record"
    try:
        return preflight(b) or ""
    except Exception as exc:   # a preflight that raises has not said yes
        return "NO-DATA preflight raised %s" % type(exc).__name__


def newest_status(runs, sub):
    """(first STATUS word, RUNNING, DEAD or UNREADABLE, run folder) of the newest run of this sub unit; (None, None) when there is none."""
    best = None
    for d in glob.glob(os.path.join(runs, sub + "-*")):
        if re.match(r"^" + re.escape(sub) + r"-\d{6}$", os.path.basename(d)) and os.path.isdir(d):
            t = os.path.getmtime(d)
            if best is None or t > best[0]:
                best = (t, d)
    if best is None:
        return None, None
    try:
        with open(os.path.join(best[1], "STATUS"), encoding="utf-8") as f:
            return (f.read().split() or ["EMPTY"])[0], best[1]
    except FileNotFoundError:   # sbe: allow-silent only an ABSENT STATUS falls through to the PID check below
        pass
    except OSError:
        # A STATUS that exists but cannot be read is UNKNOWN, never DEAD (2026-09-30), the same rule as status_bytes.
        return "UNREADABLE", best[1]
    # NO STATUS IS NOT PROOF OF A RUNNER. A run killed by a stop leaves a folder with a PID and no STATUS for ever
    # (measured 2026-09-22: five such folders after one stop). Ask the PID: alive means RUNNING, anything else is DEAD.
    try:
        with open(os.path.join(best[1], "PID"), encoding="utf-8") as f:
            os.kill(int(f.read().strip()), 0)
        return "RUNNING", best[1]
    except (OSError, ValueError):
        return "DEAD", best[1]


def status_bytes(run):
    """This run's STATUS exactly as it is on disk, or None when there is none. A STATUS that exists but cannot be read
    raises OSError: unknown never reads as absent, or an unreadable quarantine would be promoted over (2026-09-27)."""
    try:
        with open(os.path.join(run, "STATUS"), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None   # sbe: allow-silent None means no STATUS file at all; an unreadable one raises and every caller refuses


def refused_at_landing(run, status=None, holds=True):
    """True when this run's STATUS says the LANDING refused its build (at the gates, or after the commit). Salvage must
    never bring such a build back: land_batch quarantines it so it is not offered again, and a salvage that re-promoted
    it would have the two tools undo each other on every pass. A quarantine written by the CHECKER is not this.
    status: the STATUS bytes the caller already read, so the decision and promote's compare rest on ONE read; None
    reads the file (for an absent STATUS that is a second read, and anything it finds promote's compare then refuses)."""
    text = (status_bytes(run) if status is None else status) or b""
    # both spellings the landing writes: "refused at the gates" / "refused after the commit" and "dropped at landing"
    # (measured 2026-09-22 09:58: a build dropped for a fuzz crash read as PROMOTE and would have looped every pass)
    # WITHHELD is a hold as well (2026-09-25: salvage re-promoted a withheld D2.1 build that had replaced the live
    # dispatcher's subprocess with a stub, and the landing put the break back twice)
    # holds=False asks only whether the LANDING refused it: the runner's own WITHHELD parks (a grade that judged nothing, a
    # refused brief) are no refusal, and reading them as one blocked every older build behind a park (review 2026-10-04)
    # a hold a PERSON wrote covers the whole sub unit either way (D2.1, 2026-09-25)
    return text.decode("utf-8").startswith(("QUARANTINE refused", "QUARANTINE dropped at landing", "WITHHELD by the orchestrator")
                                           + (("WITHHELD",) if holds else ()))


def plan_view(cands, preflight, mode, runs):
    """Annotate every candidate with what would happen to it: PROMOTE, or the reason it is left alone. Each candidate
    keeps the STATUS bytes its decision read (status_seen): promote writes only while STATUS still holds exactly those."""
    seen = set()
    for c in cands:
        try:
            c["status_seen"] = status_bytes(c["run"])
        except OSError as exc:   # no status_seen, so promote refuses this candidate even if a caller overrides the action
            c["applies"], c["action"] = "NO-DATA", "leave: STATUS unreadable (%s)" % str(exc)[:80]
            continue
        why = applies(c["build"], preflight)
        c["applies"] = "APPLIES" if not why else "STALE: " + why[:110]
        word, newest = newest_status(runs, c["sub"])
        try:   # an older build of a sub unit whose NEWEST run the landing refused waits for a new fact (review 2026-10-04)
            newest_refused = bool(newest) and newest != c["run"] and refused_at_landing(newest, holds=False)
        except OSError:   # an unreadable newest STATUS is unknown: never promote under it
            newest_refused = True
        # A PROBE THAT NEVER RAN DOES NOT BLOCK (owner 2026-09-27, the runner's rule since then: a build that passed the
        # grader whose probe gave no answer goes to landing, and the landing gates decide). Salvage still demanded CLEAN,
        # so grader PASS builds sat unpromoted (2026-10-04: PR1.a twice, HP1.d, D2.6). DIRTY, or a probe log that exists
        # and cannot be read, is still left.
        if c["probes"] not in ("CLEAN", "NOT-RUN"):
            c["action"] = "leave: probes %s" % c["probes"]
        elif why:
            c["action"] = "leave: does not apply to this tree"
        elif refused_at_landing(c["run"], c["status_seen"]):
            c["action"] = "leave: the landing gates refused this run's build"
        elif newest_refused:
            c["action"] = "leave: the newest run of %s was refused at landing; older builds wait for a new fact" % c["sub"]
        elif mode == "gate" and c["checker"] in ("FIX", "NO-DATA"):
            c["action"] = "leave: checker is a gate and said %s" % c["checker"]
        elif c["sub"] in seen:
            c["action"] = "leave: duplicate, a newer clean build of %s exists" % c["sub"]
        elif word in ("READY", "RUNNING", "UNREADABLE"):
            c["action"] = "leave: newest run of %s is %s" % (c["sub"], word); seen.add(c["sub"])
        else:
            c["action"] = "PROMOTE"; seen.add(c["sub"])
    return cands


def promote(c, now=None):
    """Mark c's build READY in its run's STATUS. Returns '' when written, else why it was left alone.

    A LANDING'S WORD ALWAYS WINS (audit 2026-09-27, reproduced on hub main e7e17784c): the candidate was chosen from a
    STATUS read at selection, a landing then wrote QUARANTINE, and this overwrote it with READY, so the refused build
    was offered again. So the write happens only under the runs root's exclusive lock, <runs>/.status.lock, and only
    while STATUS holds exactly the bytes selection read and no other run of the sub unit is now READY or RUNNING. A
    candidate carrying no snapshot is refused: unknown never reads as unchanged. A refusal touches nothing in the run
    folder, since a new entry there moves its mtime and makes it read as the newest run. The lock closes the last gap
    (compare, then write) only against writers that take it too."""
    if "status_seen" not in c:
        return "no STATUS snapshot from selection; only a plan_view candidate is promoted"
    run = c["run"]; st = os.path.join(run, "STATUS"); runs = os.path.dirname(run)
    with open(os.path.join(runs, ".status.lock"), "a") as lock:   # outside every run folder, so no run's mtime moves
        fcntl.flock(lock, fcntl.LOCK_EX)   # released when the file closes   # ponytail: one lock for every run; per run locks if writers contend
        try:
            now_seen = status_bytes(run)
        except OSError as exc:
            return "STATUS unreadable now (%s); nothing written" % str(exc)[:80]
        if now_seen != c["status_seen"]:
            return "STATUS changed since selection, now %r; the newer word stands" % (now_seen or b"(none)")[:80]
        word, newest = newest_status(runs, c["sub"])
        if word == "UNREADABLE" or (word in ("READY", "RUNNING") and os.path.basename(newest) != os.path.basename(run)):
            return "newest run of %s is now %s (%s)" % (c["sub"], word, os.path.basename(newest))
        if os.path.isfile(st) and not os.path.exists(st + ".before-salvage"):
            shutil.copy2(st, st + ".before-salvage")
        tmp = st + ".tmp-%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("READY %s (salvaged %s: grader PASS, probes %s, still applies; checker said %s; the landing gates decide)\n"
                    % (c["build"], time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), c["probes"], c["checker"]))
        os.replace(tmp, st)   # creating an entry moves the run folder's own mtime, and readers take the NEWEST run of a sub unit: this one now speaks for it
    return ""


def main():
    if "--selftest" in sys.argv:
        return selftest()
    verb = (sys.argv[1:] or ["list"])[0]
    try:
        with open(os.environ.get("SALVAGE_PLAN") or PLAN, encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, ValueError) as exc:
        print("SALVAGE NO-DATA: the plan cannot be read (%s); nothing listed, nothing promoted" % type(exc).__name__); return 0
    try:
        import grade_build, check_wave
        preflight, mode = grade_build.preflight, check_wave.checker_mode()
    except Exception as exc:
        print("SALVAGE NO-DATA: the preflight or the checker mode cannot be loaded (%s)" % type(exc).__name__); return 0
    runs = runs_dir()
    cands = plan_view(candidates(runs, all_subs(plan, os.environ.get("BROTHER_SCOPE")) - landed_subs(plan)), preflight, mode, runs)
    if verb == "promote":
        done = []
        for c in [c for c in cands if c["action"] == "PROMOTE"]:
            refusal = admissible(plan, c["sub"])
            if refusal:
                print("SALVAGE refused %s: %s" % (c["sub"], refusal))
                continue
            try:
                why = promote(c)
            except OSError as exc:
                print("SALVAGE NO-DATA: could not write STATUS for %s (%s)" % (c["sub"], type(exc).__name__)); continue
            if why:
                print("SALVAGE refused %s: %s" % (c["sub"], why)); continue
            done.append(c["sub"])
        print("SALVAGE promoted %d: %s" % (len(done), ", ".join(done) or "none"))
        return 0
    print("UNLANDED BUILDS THAT PASSED THE GRADER: %d (checker mode %s)" % (len(cands), mode))
    tally = {}
    for c in cands:
        k = c["action"].split(":")[0] if c["action"] == "PROMOTE" else c["action"].split(",")[0][:40]; tally[k] = tally.get(k, 0) + 1
    print("  " + " | ".join("%s %d" % (k, v) for k, v in sorted(tally.items(), key=lambda kv: -kv[1])))
    for c in cands:
        print("  %-8s probes %-7s checker %-7s %-9s %s | %s" % (c["sub"], c["probes"], c["checker"], c["applies"][:9], c["action"], os.path.relpath(c["build"], runs)))
    return 0


def selftest():
    # A CASE THAT RAISES STILL ANSWERS THE QUESTION: a verdict line and exit 1, never a bare traceback, because the
    # pipeline reads the code and the human reads the line, and the two must say the same thing.
    try: return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1


def _selftest_body():
    import tempfile
    d = tempfile.mkdtemp(prefix="salvage-"); runs = os.path.join(d, "runs")
    def plant(sub, stamp, rnd, probes, checker=None, status="EXHAUSTED after 5 rounds", age=0.0):
        rd = os.path.join(runs, "%s-%s" % (sub, stamp), "round%d" % rnd); os.makedirs(os.path.join(rd, "out")); os.makedirs(os.path.join(rd, "probes", "logs"))
        b = os.path.join(rd, "out", "%s-r0-build.json" % sub)
        with open(b, "w") as f: json.dump({"sub": sub}, f)
        with open(os.path.join(rd, "lane.log"), "w") as f: f.write("PASS %s-r0\n" % sub)
        if probes is not None:
            with open(os.path.join(rd, "probes", "logs", sub + ".done"), "w") as f: f.write(probes + "\n")
        if checker:
            with open(b + ".check.json", "w") as f: json.dump({"verdict": checker}, f)
        run = os.path.dirname(rd)
        if status is not None:
            with open(os.path.join(run, "STATUS"), "w") as f: f.write(status + "\n")
        t = time.time() - age
        os.utime(os.path.join(rd, "lane.log"), (t, t)); os.utime(run, (t, t))
        return b
    ok = lambda b: ""                                   # a preflight that accepts everything
    stale = lambda b: "edit 1: find text not in target" if b.get("sub") == "S.1" else ""
    a = plant("A.1", "010000", 2, "CLEAN", "FIX", age=600)
    plant("A.1", "020000", 0, "DIRTY", age=300)         # a newer run of A.1 that ended EXHAUSTED with only a dirty build
    plant("B.1", "010000", 1, "DIRTY")
    plant("C.1", "010000", 1, None)                     # no probe verdict at all
    s1 = plant("S.1", "010000", 1, "CLEAN")
    r1 = plant("R.1", "010000", 1, "CLEAN", status=None)     # a runner is on it: no STATUS, and a PID that is alive (ours)
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(r1))), "PID"), "w") as f: f.write("%d\n" % os.getpid())
    x1 = plant("X.1", "010000", 1, "CLEAN", status=None)     # killed mid run: no STATUS, and a PID nobody holds
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(x1))), "PID"), "w") as f: f.write("%d\n" % (2 ** 22 - 7))
    plant("Y.1", "010000", 1, "CLEAN", status=None)          # no STATUS and no PID file at all: dead, not running
    plant("K.1", "010000", 1, "CLEAN", status="READY /x")
    d1 = plant("D.1", "010000", 1, "CLEAN", age=900); d2 = plant("D.1", "010000", 3, "CLEAN", age=100)
    plant("Q.1", "010000", 1, "CLEAN", status="QUARANTINE refused after the commit, hermetic check exit 1; the commit is kept | previous: READY /x")
    plant("G.1", "010000", 1, "CLEAN", status="QUARANTINE refused at the gates alone, gates RED | previous: READY /x")
    plant("W.1", "010000", 1, "CLEAN", status="WITHHELD by the orchestrator: this build broke the live dispatcher")
    plant("F.1", "010000", 1, "CLEAN", status="QUARANTINE dropped at landing, exit 2, fuzz crashes 99: CRASH x | previous: READY /x")
    plant("H.1", "010000", 1, "CLEAN", "FIX", status="QUARANTINE checker opus FIX FIRST: 1. x | previous: READY /x")
    plant("E.1", "010000", 1, "")   # a probe log that exists and is empty
    l1 = plant("L.1", "010000", 1, None)   # an adversary log with a CRASH and no .done: the wave was killed mid way
    with open(os.path.join(os.path.dirname(os.path.dirname(l1)), "probes", "logs", "L.1-m1.log"), "w") as f: f.write("CRASH x\n")
    plant("N.1", "010000", 1, "CLEAN", age=900)   # an older PASS build ...
    plant("N.1", "020000", 1, "CLEAN", status="QUARANTINE refused at the gates alone, gates RED | previous: READY /x", age=100)   # ... under a refused newest
    plant("P.1", "010000", 1, "CLEAN", age=900)   # an older PASS build under a newest run the RUNNER parked (no landing refusal)
    pr = os.path.dirname(os.path.dirname(os.path.dirname(plant("P.1", "020000", 1, "CLEAN", status="WITHHELD round 0: NO-DATA, the grade judged nothing", age=100))))
    os.remove(os.path.join(pr, "round1", "lane.log"))   # the parked run has no grader PASS of its own
    plant("O.1", "010000", 1, "CLEAN", age=900)   # an older PASS build under a newest run a PERSON held
    plant("O.1", "020000", 1, "CLEAN", status="WITHHELD by the orchestrator: this build broke the live dispatcher", age=100)
    subs = {"A.1", "B.1", "C.1", "E.1", "L.1", "N.1", "P.1", "O.1", "S.1", "R.1", "K.1", "D.1", "X.1", "Y.1", "Q.1", "G.1", "H.1", "F.1", "W.1"}
    view = {(c["sub"], os.path.basename(os.path.dirname(os.path.dirname(c["build"])))): c for c in plan_view(candidates(runs, subs), stale, "shadow", runs)}
    act = lambda sub, rnd: view[(sub, rnd)]["action"]
    gate = {c["build"]: c["action"] for c in plan_view(candidates(runs, subs), ok, "gate", runs)}
    cases = [("a clean build that applies is promoted even though a newer run exhausted", act("A.1", "round2") == "PROMOTE"),
             ("a dirty build is left", act("B.1", "round1").startswith("leave: probes DIRTY")),
             ("a build no probe ever ran on is promoted, the landing gates decide (owner 2026-09-27)", act("C.1", "round1") == "PROMOTE"),
             ("a probe log that exists but says nothing is NO-DATA and left", act("E.1", "round1") == "leave: probes NO-DATA"),
             ("adversary logs with no .done are NO-DATA and left, never NOT-RUN", act("L.1", "round1") == "leave: probes NO-DATA"),
             ("an older build under a newest run the RUNNER parked WITHHELD is still promoted", any(c["sub"] == "P.1" and c["action"] == "PROMOTE" for c in view.values())),
             ("an older build under a newest run a person held is left too", all(c["action"] != "PROMOTE" for c in view.values() if c["sub"] == "O.1")),
             ("an older build under a newest run the landing refused is left", any(c["sub"] == "N.1" and "newest run of N.1 was refused" in c["action"] for c in view.values())),
             ("a build that no longer applies is left and says STALE", act("S.1", "round1").startswith("leave: does not apply") and view[("S.1", "round1")]["applies"].startswith("STALE")),
             ("a sub unit with a live runner is left", "RUNNING" in act("R.1", "round1")),
             ("a run killed mid way, PID dead, does not block the promotion", act("X.1", "round1") == "PROMOTE"),
             ("no STATUS and no PID file is dead too, never RUNNING", act("Y.1", "round1") == "PROMOTE"),
             ("a sub unit that already has a READY build is left", "READY" in act("K.1", "round1")),
             ("of two clean builds the newer is promoted and the older is a duplicate", act("D.1", "round3") == "PROMOTE" and "duplicate" in act("D.1", "round1")),
             ("a build the landing refused after its commit is never brought back", "landing gates refused" in act("Q.1", "round1")),
             ("a build the landing DROPPED (red suite or fuzz crash) is never brought back either", "landing gates refused" in act("F.1", "round1")),
             ("a build the landing refused at the gates is never brought back", "landing gates refused" in act("G.1", "round1")),
             ("a WITHHELD run is never brought back (2026-09-25: salvage re-promoted a withheld D2.1 build that broke the dispatcher)", act("W.1", "round1") != "PROMOTE"),
             ("a build only the checker quarantined IS brought back in shadow mode", act("H.1", "round1") == "PROMOTE"),
             ("in gate mode a FIX verdict is respected", gate[a].startswith("leave: checker is a gate")),
             ("a sub unit outside the asked set is never a candidate", candidates(runs, {"B.1"})[0]["sub"] == "B.1" and len(candidates(runs, {"B.1"})) == 1),
             ("a landed sub unit is read from the plan's evidence", landed_subs({"units": [{"sub_units": ["A.1", "A.2"], "evidence": "A.1 landed ok"}]}) == {"A.1"}),
             ("the pool's scope regex limits the sub units considered", all_subs({"units": [{"id": "D3", "sub_units": ["D3.1"]}, {"id": "L9", "sub_units": ["L9.1"]}]}, r"^D\d") == {"D3.1"}),
             ("with no scope every unit is considered", len(all_subs({"units": [{"id": "D3", "sub_units": ["D3.1"]}, {"id": "L9", "sub_units": ["L9.1"]}]})) == 2),
             ("an unreadable build json is NO-DATA, never applies", applies(os.path.join(d, "absent.json"), ok).startswith("NO-DATA")),
             ("a preflight that raises has not said yes", applies(a, lambda b: 1 / 0).startswith("NO-DATA"))]
    promote(view[("A.1", "round2")])
    st = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(a))), "STATUS")).read()
    cases += [("promotion writes READY with exactly this build's path, second word", st.split()[0] == "READY" and st.split()[1] == a),
              ("the old status is kept beside it", open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(a))), "STATUS.before-salvage")).read().startswith("EXHAUSTED")),
              ("the promoted run becomes the newest run of its sub unit", newest_status(runs, "A.1")[0] == "READY")]
    z = plant("Z.1", "010000", 1, "CLEAN"); zst = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(z))), "STATUS")
    zc = plan_view(candidates(runs, {"Z.1"}), ok, "shadow", runs)[0]
    with open(zst, "w") as f: f.write("QUARANTINE refused at the gates alone, gates RED\n")   # the landing, after selection
    cases += [("a quarantine written after selection is never overwritten (audit 2026-09-27)",
               zc["action"] == "PROMOTE" and bool(promote(zc)) and open(zst).read().startswith("QUARANTINE refused"))]
    # THE ENTRY POINT, as the pass runs it: a subprocess in a temp repo root with a plan and no real preflight reachable
    import subprocess
    plan = os.path.join(d, "plan.json")
    with open(plan, "w") as f: json.dump({"units": [{"id": "D", "sub_units": ["D.1"], "evidence": ""}]}, f)
    r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "list"], capture_output=True, text=True, cwd=d, env=dict(os.environ, SALVAGE_RUNS=runs, SALVAGE_PLAN=plan))
    r2 = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "list"], capture_output=True, text=True, cwd=d, env=dict(os.environ, SALVAGE_RUNS=runs, SALVAGE_PLAN=os.path.join(d, "absent.json")))
    cases += [("the entry point lists the unlanded builds of the plan's sub units and exits 0", r.returncode == 0 and "UNLANDED BUILDS THAT PASSED THE GRADER: 2" in r.stdout and "D.1" in r.stdout),
              ("an unreadable plan is NO-DATA, exit 0, nothing listed", r2.returncode == 0 and "SALVAGE NO-DATA" in r2.stdout)]
    bad = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
