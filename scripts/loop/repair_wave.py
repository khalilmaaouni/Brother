#!/usr/bin/env python3
"""Send every DIRTY lane of a probe wave back to workers, then grade and probe the results. One command, no human in the round.
usage (repo root): repair_wave.py <build wave dir> <probe wave dir> <new wave dir> [workers per lane=3]
exit: 0 every stage completed, or nothing was dirty; 1 the fan-out, grading or probing failed or the fan-out passed its
deadline (the table is partial); 3 refused before any job (the model registry is unreadable or empty, the code root is
refused, the worker count is not a whole number of at least 1, or dirty work has no usable worker arm); 5 held (a pause or the owner's HOLD: nothing is bought). A fan-out past its
deadline (BROTHER_REPAIR_FANOUT_TIMEOUT_S, default 480) is killed with its whole process group and reaped.
For each lane whose <probe wave>/logs/<lane>.done reads DIRTY: brief = the lane's ORIGINAL prompt (from the build wave's jobs file)
+ the real findings (a returned refusal value is not a finding) + the previous passing build. Briefs with a private term or over
the dispatcher size are withheld. Dispatch has a short call timeout, no retry and an overall deadline: never wait for a straggler.
Then grade_lanes_par.sh and probe_wave.py run on the new wave; the verdict table lands in <new wave dir>.table"""
import glob, json, os, re, signal, subprocess, sys, time
# THE HOLD REACHES THIS ROUTE (review item 1, 2026-09-26): checked before any model seam loads; the selftest still runs.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loop_hold as _LH  # noqa: E402
if "--selftest" not in sys.argv: _LH.gate(where="repair_wave")
BIN = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BIN)
import probe_build as P
import worker_mix as WM, ev_gate as EV, repair_advisor as RA
GENERATED = ("bundle/", "SYSTEM.md", "scripts/check_all.sh", "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json")


def touch_paths(build_path):
    """The files a build patches (edits and tests), generated paths dropped; None when the build is unreadable."""
    try:
        with open(build_path, encoding="utf-8") as fh: b = json.load(fh)
        paths = {e["path"] for k in ("edits", "tests") for e in (b.get(k) or []) if isinstance(e, dict) and isinstance(e.get("path"), str)}
    except (OSError, ValueError, AttributeError): return None
    return {q for q in paths if not any(q == g or q.startswith(g) for g in GENERATED)} or None


def defer_conflicts(lanes):
    """FILE TOUCH EDGES IN THE REPAIR WAVE (row 17, 2026-09-22): lanes are [(lane, touch set)] in order; the first lane
    to touch a file keeps it, a later lane sharing one is deferred to the next wave with the file named, and a lane whose
    set is unknown is deferred (unknown reads as touching everything). Returns (kept lanes, [(lane, why)])."""
    busy, kept, deferred = {}, [], []
    for lane, ts in lanes:
        if ts is None: deferred.append((lane, "touch set unknown")); continue
        hit = sorted(q for q in ts if q in busy)
        if hit: deferred.append((lane, "shares %s with %s" % (hit[0], busy[hit[0]]))); continue
        busy.update({q: lane for q in ts}); kept.append(lane)
    return kept, deferred


def arms(per, known):
    """The models for one lane's `per` workers: the mix shifted by the ledger's record (fix 4). DeepSeek fills the seats
    only when the registry actually knows it; with nothing known no seat is filled, so nothing is bought blind
    (review item 2, 2026-09-26: an empty registry used to buy the default model for every seat)."""
    # worker_mix.picks fills its base arms round robin without asking `known` (finding 28, its own unit): filtered here.
    got = [m for m in (WM.picks(per, ["deepseek"], known, stats=WM.arm_stats()) or []) if m in known]
    return got or (["deepseek"] * per if "deepseek" in known else [])


# F21 (MECHANISM-AUDIT item 21, 2026-09-26): the flat per job estimate this file still reserves at
# dispatch (see the jobs.append call below) is F4b's concern, held for the dispatcher's own M4.3/M4.4
# estimator. This constant is F21's OWN cold start fallback for the EV GATE's round cost only, used
# when a model has no measured history yet; it is not wired into the reservation itself.
_FALLBACK_JOB_COST = 0.02


def round_cost_for_arms(models):
    """The round's total expected cost, priced per model (F21): each arm's own measured cost from
    the ledger (ev_gate.model_round_cost, RS1's actual_costs_for_model) when the ledger has one,
    the flat cold start fallback otherwise. A corrupt or unreadable ledger raises ev_gate.CostUnreadable,
    which the caller turns into a refused round, never the fallback. None only when there are no arms at all, since the
    caller is about to refuse the round for lack of a usable arm anyway and no cost estimate is
    needed for a round that will not be dispatched."""
    if not models:
        return None
    return sum((EV.model_round_cost(m) or _FALLBACK_JOB_COST) for m in models)


def grade_passed(text):
    """True only when a grade file (grade_one.sh: the grader's lines, then exit=N) ends in exit=0 AND its last line starting
    PASS or FAIL is exactly PASS: the grade command's exit code and its last verdict, the rule grade_lane.sh keeps (lane
    F4). RR lane C, 2026-09-27: any line starting PASS used to count here, so a worker's own printed PASS above the real
    FAIL of a grade that exited 1 made that build the lane's "passing" repair base."""
    lines = text.splitlines() if isinstance(text, str) else []
    verdicts = [l for l in lines if l.startswith(("PASS", "FAIL"))]
    return bool(lines) and lines[-1] == "exit=0" and bool(verdicts) and verdicts[-1] == "PASS"


def _drain_fanout(proc, deadline):
    """Kill a timed-out fan-out's whole process group. os.killpg is the fast path; when it is refused
    for any reason but the group's absence, read the process table and signal every member of the
    group TERM then KILL. Any failure other than the member's own absence, a member still present
    after the KILL pass, or an unreadable (or timed out) process table is reported as NO-DATA, so the
    caller fails the wave instead of reporting a drain it did not make.
    RR lane C, 2026-09-27, two defects this closes. (1) EVERY WAIT ENDS BY `deadline` seconds from entry: with the group
    signal denied, the table unreadable and proc.kill() refused, proc.wait() blocked for as long as the fan-out lived;
    a fan-out not reaped in time is NO-DATA. (2) THE KILL IS RE-VERIFIED: the members are read AGAIN after the TERM and
    only pids a fresh table still shows in the fan-out's group are SIGKILLed, since a pid TERMed and then reused by an
    unrelated process was SIGKILLed from the stale list; a fresh read that fails stops the drain as NO-DATA before any
    KILL. NAMED LIMIT: a pid can still be reused between that fresh read and the kill itself (no pidfd here)."""
    _end = time.monotonic() + max(0.0, float(deadline))
    _left = lambda: max(0.0, _end - time.monotonic())

    def _reaped():
        """None once the fan-out is reaped inside the drain's deadline, else NO-DATA, said."""
        try:
            proc.wait(timeout=_left())
            return None
        except subprocess.TimeoutExpired:
            print("DRAIN: the fan-out was not reaped inside the drain's deadline of %s s; the fan-out's group kill is NO-DATA" % deadline)
            return "NO-DATA"
    def _ps_members():
        _ps = subprocess.run(["ps", "-axo", "pid=,pgid="], capture_output=True, text=True,
                             timeout=float(os.environ.get("BROTHER_REPAIR_PS_TIMEOUT_S", "10")))
        if _ps.returncode != 0:
            raise OSError("ps exited %s" % _ps.returncode)
        _pids = []
        for _line in _ps.stdout.splitlines():
            _parts = _line.split()
            if not _parts:
                continue
            # `pid=,pgid=` prints no header, so any other shape is anomalous: skipping it could hide a live member
            # and read the group as empty. Raise instead; the caller reports the drain as NO-DATA.
            if len(_parts) != 2 or not (_parts[0].isdigit() and _parts[1].isdigit()):
                raise OSError("unreadable process table line %r" % _line[:80])
            _pid = int(_parts[0]); _pgid = int(_parts[1])
            if _pgid == proc.pid:
                _pids.append(_pid)
        return _pids
    try:
        os.killpg(proc.pid, signal.SIGKILL)
        return _reaped()
    except ProcessLookupError:
        return _reaped()
    except OSError as _exc:
        print("DRAIN: the fan-out's group could not be signalled (%s); reading the process table" % type(_exc).__name__)
        try:
            _pids = _ps_members()
        except subprocess.TimeoutExpired as _exc2:
            print("DRAIN: the process table read timed out (%s); the fan-out's group kill is NO-DATA" % _exc2)
            try: proc.kill()
            except OSError: pass
            try: proc.wait(timeout=_left())
            except Exception: pass
            return "NO-DATA"
        except Exception as _exc2:
            print("DRAIN: the process table could not be read (%s); the fan-out's group kill is NO-DATA" % type(_exc2).__name__)
            try: proc.kill()
            except OSError: pass
            try: proc.wait(timeout=_left())
            except Exception: pass
            return "NO-DATA"
        if not _pids:
            return _reaped()
        for _sig in (signal.SIGTERM, signal.SIGKILL):
            if _sig == signal.SIGKILL:
                try:
                    _pids = _ps_members()
                except Exception as _exc5:
                    print("DRAIN: the process table could not be re-read before the KILL (%s); the fan-out's group kill is NO-DATA" % type(_exc5).__name__)
                    return "NO-DATA"
            for _pid in _pids:
                try: os.kill(_pid, _sig)
                except ProcessLookupError: pass
                except OSError as _exc3:
                    print("DRAIN: process %d could not be signalled (%s); the fan-out's group kill is NO-DATA" % (_pid, type(_exc3).__name__))
                    return "NO-DATA"
            if _sig == signal.SIGTERM:
                time.sleep(0.5)
        time.sleep(0.3)
        try: proc.wait(timeout=_left())
        except Exception: pass   # sbe: allow-silent the re-read below decides: a leader still alive is a survivor
        try:
            _survived = _ps_members()
        except Exception as _exc4:
            print("DRAIN: the process table could not be re-read (%s); the fan-out's group kill is NO-DATA" % type(_exc4).__name__)
            return "NO-DATA"
        if _survived:
            print("DRAIN: %d process(es) of the fan-out's group survived the KILL pass; the fan-out's group kill is NO-DATA" % len(_survived))
            return "NO-DATA"
        return None


def selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="rw-"); b = os.path.join(d, "b.json")
    with open(b, "w") as fh: json.dump({"edits": [{"path": "scripts/a.py"}, {"path": "bundle/x.py"}], "tests": [{"path": "scripts/test_a.py"}]}, fh)
    with open(os.path.join(d, "junk.json"), "w") as fh: fh.write("{")
    kept, deferred = defer_conflicts([("L1", {"scripts/a.py"}), ("L2", {"scripts/a.py", "scripts/b.py"}), ("L3", {"scripts/c.py"}), ("L4", None)])
    cases = [("touch paths read edits and tests, drop generated paths", touch_paths(b) == {"scripts/a.py", "scripts/test_a.py"}),
             ("an unreadable build has no touch set", touch_paths(os.path.join(d, "junk.json")) is None and touch_paths(os.path.join(d, "none.json")) is None),
             ("the first lane keeps a shared file, the later one is deferred with the file named", kept == ["L1", "L3"] and deferred[0] == ("L2", "shares scripts/a.py with L1")),
             ("an unknown touch set is deferred, never treated as touching nothing", ("L4", "touch set unknown") in deferred),
             ("arms fill no seat when nothing is known, fall back to deepseek only when it is known, and never exceed the worker count", arms(3, set()) == [] and arms(3, {"deepseek"}) == ["deepseek"] * 3 and len(arms(8, {"deepseek", "muse"})) == 8)]
    # F21: round_cost_for_arms prices each arm from ev_gate's real per-model lookup, monkeypatched
    # here (a plain module attribute swap, restored in finally) since it is the actual reference
    # repair_wave.py calls, never a hand reimplementation of it.
    _orig_cost = EV.model_round_cost
    EV.model_round_cost = lambda m: {"known-model": 0.5}.get(m)
    try:
        cases.append(("round cost sums each arm's own measured cost, falling back per unknown arm",
                      abs(round_cost_for_arms(["known-model", "unknown-model", "known-model"])
                          - (0.5 + _FALLBACK_JOB_COST + 0.5)) < 1e-9))
        cases.append(("no arms means no round cost to compute: the caller refuses for lack of an arm instead",
                      round_cost_for_arms([]) is None))
    finally:
        EV.model_round_cost = _orig_cost
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if "--selftest" in sys.argv: sys.exit(selftest())
bw, pw, nw = [os.path.abspath(os.path.expanduser(a)) for a in sys.argv[1:4]]
_jc = subprocess.run([sys.executable, os.path.join(BIN, "judge_calibrate.py")], capture_output=True, text=True)   # the repair loop reads the same calibration
print((_jc.stdout.strip().splitlines() or ["CALIB   NO-DATA: calibrator printed nothing"])[-1][:160])
# WORKERS PER LANE (owner 2026-09-23 00:0x, "as many lanes and writers as needed to accelerate the repair"): eight, the measured
# plateau of the runner's own rounds, unless the caller says otherwise. Lanes are unbounded; the machine stage is bounded by the slot pool.
# THREE, AS THE USAGE LINE SAYS (review item 4, 2026-09-26): the default had drifted to eight while the header said three.
try:
    per = int(sys.argv[4]) if len(sys.argv) > 4 else int(os.environ.get("BROTHER_WORKERS_PER_ROUND", "3"))
except ValueError:
    per = 0
# A WAVE THAT CANNOT DISPATCH IS NOT A CLEAN WAVE (review 2026-09-26): zero or negative workers built no job and printed
# "nothing to repair" with exit 0 while dirty work waited.
if per < 1:
    print("REFUSED: workers per lane must be a whole number of at least 1 (got %r); no job is written" % (sys.argv[4] if len(sys.argv) > 4 else os.environ.get("BROTHER_WORKERS_PER_ROUND"))); sys.exit(3)
# AN UNREADABLE REGISTRY REFUSES THE WAVE (review item 2): it used to become an empty set and every seat bought the default.
try:
    import model_router as _MR; KNOWN = set(_MR.seatable_names())   # the stage gate: a shadow or retired row never fills a seat
except Exception as _exc:
    print("REFUSED: the model registry is unreadable (%s: %s); no job is written and nothing is bought" % (type(_exc).__name__, str(_exc)[:120])); sys.exit(3)
if not KNOWN:
    print("REFUSED: the model registry is empty; no job is written and nothing is bought"); sys.exit(3)
# THE FAN OUT RUNS THE FROZEN CANDIDATE (U3, B5-08): `-m` resolves the module from the cwd, which was the landing tree.
# Asked once, before any job exists, so a refused code root (a proof phase with none set) buys nothing.
try:
    CODE_ROOT = _MR.code_root()
except Exception as _exc:
    print("REFUSED: the code root is refused (%s: %s); no job is written and nothing is bought" % (type(_exc).__name__, str(_exc)[:120])); sys.exit(3)
for d in ("prompts", "out"):
    os.makedirs(os.path.join(nw, d), exist_ok=True)
prompt_of = {}
for jf in glob.glob(os.path.join(bw, "jobs*.json")):
    for j in json.load(open(jf, encoding="utf-8")):
        prompt_of.setdefault(re.sub(r"-r\d+$", "", j["id"]), j["prompt_file"])
jobs = []; busy = {}; no_arm = []
for done in sorted(glob.glob(os.path.join(pw, "logs", "*.done"))):
    lane = os.path.basename(done)[:-5]
    if open(done).read().strip() != "DIRTY" or lane not in prompt_of:
        continue
    passing = None
    for g in sorted(glob.glob(os.path.join(bw, "grades", lane + "-r*.txt"))):
        if grade_passed(open(g, encoding="utf-8").read()):
            passing = os.path.join(bw, "out", os.path.basename(g)[:-4] + "-build.json"); break
    if not passing:
        continue
    finds = []
    for log in glob.glob(os.path.join(pw, "logs", lane + "-*.log")):
        for l in open(log, encoding="utf-8").read().splitlines():
            m = re.match(r"^(CRASH|WRONG-ACCEPT\?)\s+(\S+)\s+(\S+)\s?(.*)$", l)
            if not m:
                continue
            if m.group(1) == "CRASH":
                if P.classify(m.group(3), "", m.group(4)) == "CRASH":
                    finds.append(l[:230])
            elif not P.REFUSAL_VALUE.search(re.sub(r"^RETURNED\s+", "", (m.group(3) + " " + m.group(4)).strip())):
                finds.append(l[:230])
    finds = sorted(set(finds))
    if not finds:
        print("%-10s no real finding after reclassifying returned refusals: treat as CLEAN, land it" % lane); continue
    # THE EXPECTED VALUE GATE (fix 5): a lane whose record says another round is not worth its cost goes to the advisor or to
    # spec repair, not to eight more workers. History is the lane's own across every run, from the unit ledger.
    # ARMS ARE DRAWN ONCE, HERE (F21, 2026-09-26): worker_mix.picks() samples from an unseeded
    # random.Random() on every call, so pricing the round from a SECOND, separate arms() call could
    # price a different model mix than the one this round actually dispatches. The one draw made
    # here is reused below for the real dispatch; every model's round used to be priced alike from
    # one flat BROTHER_ROUND_COST default, never from the ledger's own per-model measured history.
    _arms = arms(per, KNOWN)
    try:
        _cost = round_cost_for_arms(_arms)
    except EV.CostUnreadable as _e:
        print("%-10s SKIP EV: the round is refused, its cost is unknown: %s" % (lane, _e)); continue
    _hr, _hp = EV.history(lane); _go, _ev = EV.should_continue(_hr, _hp, round_cost=_cost)
    if not _go:
        print("%-10s SKIP EV: %s" % (lane, _ev)); continue
    _ts = touch_paths(passing)
    if _ts is not None and any(q in busy for q in _ts):
        _q = sorted(q for q in _ts if q in busy)[0]; print("%-10s DEFERRED touch: shares %s with %s" % (lane, _q, busy[_q])); continue
    if _ts is None:
        print("%-10s DEFERRED touch: the passing build's touch set is unreadable" % lane); continue
    busy.update({q: lane for q in _ts})
    # THE ADVISOR HEADS THE BRIEF (fix 2): three targeted edits from the findings, before the findings themselves.
    _advice = RA.advise("\n".join(finds), open(passing, encoding="utf-8").read()[:20000], open(prompt_of[lane], encoding="utf-8").read()[:8000]) if RA.enabled(lane) else None
    if _advice: print("%-10s ADVISOR %s" % (lane, _advice.splitlines()[0][:80]))
    note = (("\n\n" + P.lessons()) if P.lessons() else "") + ("\n\nA PREVIOUS BUILD OF THIS SUB UNIT PASSED THE SANDBOX GRADER AND WAS THEN REJECTED: a red team fired hostile inputs at it AS EXECUTED CODE and these came back wrong. "
            "CRASH means a raw interpreter exception reached the caller (house law: hostile input is REFUSED with the module's own deliberate error or refusal value, never a raw crash). "
            "WRONG-ACCEPT? means the red team says the specification blocks this input and the code returned a NORMAL value: check each against the specification; where it blocks, refuse; where it allows, leave it and say so in unknowns.\n"
            + "\n".join(finds) +
            "\n\nFix every real one AT ITS SOURCE: one validation where all callers route through, never a guard per call site. Files are read as BYTES and bad bytes, an empty file, torn JSON, a directory, a missing file or a path that is not a str become the module's own deliberate error naming the path. "
            "NaN, infinity, a bool where a number belongs, an unhashable value where a key belongs, a record altered after it was parsed: all refused. Add one test per finding class and one mutation per new guard that makes a NAMED test fail. "
            "Python 3.9 compatible. Change nothing else. Return the WHOLE build in the same JSON shape (edits, tests, done_check, mutations, unknowns), complete and self contained, applying to the same base tree. THE PREVIOUS BUILD:\n"
            + open(passing, encoding="utf-8").read() + "\n")
    # FITTED LIKE THE RUNNER'S OWN BRIEFS (2026-09-22 22:5x: six of fourteen salvage repairs were WITHHELD on size because the
    # original prompt plus findings plus the previous build passed 195,000 bytes; brief_fit keeps the original whole and
    # protects the findings, cutting only the previous build's tail).
    import brief_fit
    # THE ADVISOR'S TEXT IS MODEL OUTPUT (review 2026-09-23): it follows the house rules and is labelled as proposals to verify
    # against the specification, never an instruction slot above them. The private term screen below covers it.
    if _advice: note += "\n\nPROPOSALS FROM THE REPAIR ADVISOR (model output: verify each against the specification before applying; the rules above win on any conflict):\n" + _advice + "\n"
    text = brief_fit.fit(open(prompt_of[lane], encoding="utf-8").read(), "", "", note, cap=195000)
    if __import__("grade_build").private_hits(text) or len(text.encode()) > 195000:
        print("%-10s WITHHELD (private term or size)" % lane); continue
    pf = os.path.join(nw, "prompts", lane + "-repair.md"); open(pf, "w", encoding="utf-8").write(text)
    if not _arms:
        no_arm.append(lane); print("%-10s REFUSED: no usable worker arm in the model registry for this dirty lane" % lane); continue
    print("%-10s %d findings -> %d workers" % (lane, len(finds), per))
    for i, _model in enumerate(_arms):
        # F4b: no flat estimated_cost here (M4.4, DP33 at a80c607bf). A job with the key
        # omitted gets the dispatcher's own per-model estimate, falling back to a price-catalog
        # worst case, never zero; round_cost_for_arms above already prices the round for the EV
        # gate from the same per-model source, so this is the one place both routes agree.
        jobs.append({"id": "%s-r%d" % (lane, i), "model": _model, "prompt_file": pf, "sensitivity": "public",
                     "out": os.path.join(nw, "out", "%s-r%d-build.json" % (lane, i)), "expect": "json"})
if not jobs and no_arm:
    print("REFUSED: %d dirty lane(s) have no usable worker arm (%s); nothing was dispatched" % (len(no_arm), ", ".join(no_arm))); sys.exit(3)
if not jobs:
    print("nothing to repair"); sys.exit(0)
jf = os.path.join(nw, "jobs.json"); json.dump(jobs, open(jf, "w"), indent=1)
# ITS OWN SESSION, DRAINED AS A GROUP (review item 5, 2026-09-26): kill() reached the fan-out only, and its bridge calls
# outlived it. The deadline is the operator's knob; the whole group is killed and the fan-out reaped.
_deadline = int(os.environ.get("BROTHER_REPAIR_FANOUT_TIMEOUT_S", "480"))
proc = subprocess.Popen([sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jf, "--workers", "95", "--timeout", "420", "--retries", "0",
                         "--results", os.path.join(nw, "results.json")], stdout=open(os.path.join(nw, "fanout.log"), "w"), stderr=subprocess.STDOUT,
                        start_new_session=True, cwd=CODE_ROOT)
failed = []   # A FAILED STAGE IS A NONZERO EXIT (review item 6), the fan-out included (review 2026-09-26)
try:
    _frc = proc.wait(timeout=_deadline)
    if _frc != 0:
        failed.append("fan-out exit %s" % _frc); print("FAN-OUT exit %s: some builds may be missing; the wave is not clean" % _frc)
    # FINDING m (s6 adversary, 2026-09-26): the fan-out runs in its own session; a paid bridge call it did not wait
    # for outlives a clean leader exit and left the wave reporting clean. Probe its group (never pid 0 or 1: os.killpg would signal the wave's own).
    if isinstance(proc.pid, int) and proc.pid > 1:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:  # sbe: allow-silent no process is left in the fan-out's group: the proven-empty case, nothing to drain
            pass
        except OSError:
            # the group cannot be proven empty (e.g. PermissionError): drain it; only NO-DATA fails the wave
            _drain = _drain_fanout(proc, _deadline)
            if _drain == "NO-DATA":
                failed.append("fan-out group drain NO-DATA")
        else:
            _drain = _drain_fanout(proc, _deadline)
            failed.append("fan-out exited %s but left members of its session running" % _frc)
            print("DRAIN: the fan-out exited %s but left members of its session running; drained, the wave is not clean" % _frc)
            if _drain == "NO-DATA":
                failed.append("fan-out group drain NO-DATA")
except subprocess.TimeoutExpired:
    failed.append("fan-out past its deadline of %d s" % _deadline)
    _drain = _drain_fanout(proc, _deadline)
    if _drain: failed.append("fan-out group drain %s" % _drain); print("DRAIN: the fan-out's group kill is %s" % _drain)
    print("STRAGGLERS cut loose after %d s, the fan-out's whole process group with them" % _deadline)
rc = subprocess.run([os.path.join(BIN, "grade_lanes_par.sh"), nw, os.environ.get("LOCAL_SLOTS", "5")], stdout=open(os.path.join(nw, "lanes.log"), "w"), stderr=subprocess.STDOUT).returncode
if rc:
    failed.append("grading exit %d" % rc)
    print("GRADING exit %d: lanes.log names the failure; the table below is partial" % rc)
with open(nw + ".table", "w") as out:
    rc = subprocess.run([sys.executable, os.path.join(BIN, "probe_wave.py"), nw, nw + "-probes"], stdout=out, stderr=subprocess.STDOUT).returncode
if rc:
    failed.append("probing exit %d" % rc)
    print("PROBES exit %d: %s.table is partial" % (rc, nw))
print(open(nw + ".table").read())
if failed:
    print("REPAIR WAVE FAILED: %s; the table above is partial" % ", ".join(failed)); sys.exit(1)
