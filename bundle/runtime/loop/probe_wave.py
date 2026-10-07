#!/usr/bin/env python3
"""For every lane of a wave whose grader verdict is PASS: have two adversaries write executable probes, run them, print one row per lane.
usage (repo root): probe_wave.py <wave dir> <probe wave dir>
Row: lane, passing variant, probes run, CRASH, WRONG-ACCEPT?, fenced (touches scripts/), verdict CLEAN / DIRTY / NO-DATA.
CLEAN needs at least one adversary whose probes RAN (more than zero) and zero CRASH and zero WRONG-ACCEPT? across all that ran.
Briefs carrying a private term are withheld, never sent. Findings for DIRTY lanes are written to <probe wave>/findings/<lane>.txt for the repair brief."""
import glob, hashlib, json, os, re, shutil, subprocess, sys, time
# THE HOLD REACHES THIS ROUTE (review item 1, 2026-09-26): a pause or the owner's HOLD stops it before any model seam
# loads or anything is written; loop_hold.py is the one reader. Its own selftest still runs while held.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loop_hold as _LH  # noqa: E402
if "--selftest" not in sys.argv: _LH.gate(where="probe_wave")
BIN = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, BIN)


# PROBES ARE A PROPERTY OF THE SPEC, NOT OF THE ROUND (unit E2, owner approved 2026-09-22 03:07). Measured that night: the
# adversaries wrote a NEW probe set every round, so a build that passed round 1's probes met different ones in round 2;
# 8 of 11 runs held a grader passing build and still exhausted all five rounds, at about 590 s of adversary time each.
# The probe set is written ONCE per sub unit, keyed on the spec the brief carries and on the brief template itself, and
# RE-EXECUTED every later round against the new build. A repaired spec changes the key and earns fresh probes.
# FAIL DIRECTION: an entry that cannot be read is a miss; a cached probe that dies against the new build (no counts
# line) is DROPPED from the cache and the next round dispatches fresh, so a stale probe can never hold a sub unit.
def probe_cache_dir(lane, spec_path, brief_tool=os.path.join(BIN, "probe_brief.py")):
    """~/.claude/evidence/probe-cache/<lane>/<key>/ or '' when the key cannot be formed. BROTHER_PROBE_CACHE overrides the root."""
    h = hashlib.sha256()
    for path in (spec_path, brief_tool):
        try:
            with open(path, "rb") as f: h.update(f.read())
        except OSError:
            return ""
    root = os.environ.get("BROTHER_PROBE_CACHE") or os.path.expanduser("~/.claude/evidence/probe-cache")
    return os.path.join(root, lane, h.hexdigest()[:16])


def probe_cache_take(cdir, label, out):
    """Copy the stored probe for this adversary label to `out`. True on a hit; an unreadable or empty entry is a miss."""
    src = os.path.join(cdir, label + ".json") if cdir else ""
    try:
        if not src or os.path.getsize(src) == 0: return False
        json.load(open(src, encoding="utf-8")); shutil.copyfile(src, out); return True
    except (OSError, ValueError):
        return False


def probe_cache_put(cdir, label, probe):
    """Store a probe that RAN (its log carried a counts line). Temp file then replace; an existing entry is kept."""
    if not cdir: return
    dst = os.path.join(cdir, label + ".json")
    if os.path.exists(dst): return
    try:
        os.makedirs(cdir, exist_ok=True); tmp = dst + ".tmp-%d" % os.getpid(); shutil.copyfile(probe, tmp); os.replace(tmp, dst)
    except OSError as exc:
        print("%-10s PROBES cache not written (%s): the next round dispatches fresh" % (label, type(exc).__name__))   # said, and the cost is one dispatch


def probe_cache_drop(cdir, label):
    try: os.remove(os.path.join(cdir, label + ".json"))
    except OSError: pass


def probe_ran(log_text):
    """Did this adversary's probe execute at all: probe_build prints its counts line only when the child lived."""
    return bool(re.search(r"^PROBES\s+\d+ run: ", log_text, re.M)) and "probe-exit=0" in log_text
ADVERSARIES = ("deepseek", "deepseek-b")                              # two independent lanes; the label is the output file name, never the model
def second_adversary(mix=None):
    """Muse as the SECOND adversary (owner 2026-09-20) whenever the worker mix names muse or names nothing; DeepSeek when
    the day's mix is DeepSeek only (owner 2026-09-24: run 3 sent 3 Muse probe jobs, got 0 valid answers, two fallbacks
    refused and one bridge exit 44, so every READY build read NO-DATA and was rebuilt). Read only from the mix string."""
    mix = os.environ.get("BROTHER_WORKER_MIX", "") if mix is None else mix
    return "deepseek" if mix.strip() and "muse" not in mix else "muse"
def adversary_models(env=None):
    """{label: model} for the two seats. BROTHER_ADVERSARY_MODEL (the roles file's adversary setting, 2026-09-30) names
    ONE model for both seats, so a Claude only run (BROTHER_TRANSPORTS=claude) probes with a claude model; unset or
    blank keeps today's seats: DeepSeek first, Muse or DeepSeek second by the worker mix. Not a model choice: the
    registry gate at the wire still decides whether the name may be sent."""
    one = (os.environ if env is None else env).get("BROTHER_ADVERSARY_MODEL", "")
    one = one.strip() if isinstance(one, str) else ""
    if one and one != "deepseek":
        return {"deepseek": one, "deepseek-b": one}
    return {"deepseek": "deepseek", "deepseek-b": second_adversary()}   # "deepseek" (the roles file default) is today's pair
ADVERSARY_MODEL = adversary_models()     # owner 2026-09-20: keep Muse, as the SECOND adversary only; DeepSeek stays first
def lane_verdict(logs):
    """(probes run, CRASH, WRONG-ACCEPT?, verdict) for one lane, from each adversary's log TEXT.

    A FUNCTION on purpose. This decision used to be inline in the loop at the bottom of this script, where the
    only thing a test could assert about it was its SOURCE TEXT, and a source-text assertion is exactly the
    check that was defeated on 2026-09-21 by an edit one line upstream of the thing it claimed to guard.

    THE CHILD'S EXIT CODE DECIDES WHETHER ITS DENOMINATOR MAY BE READ. Measured 2026-09-22 on this very pair
    of files: a probe child that fired one harmless case and then raised exited 1, probe_build printed its
    counts line BEFORE reporting the death, and this reader took "1 run: 0 CRASH, 0 WRONG" and called the lane
    CLEAN. One case out of an unknown number is not a clean bill. probe_build now withholds the counts line
    entirely when the child died, and this reader ALSO checks the exit code, so neither file alone can restore
    the defect.

    FAIL DIRECTION, asymmetric on purpose: an unfinished run may not contribute to `ran`, which is what CLEAN
    is made of, but any CRASH or WRONG-ACCEPT? it DID print is a real observation and still counts as dirt.
    An unknown never buys a pass; evidence of dirt is never discarded.

    THREE STATES, decided in this order so no unknown can outrank evidence:
      DIRTY   at least one CRASH or WRONG-ACCEPT? was observed, whatever else happened;
      CLEAN   at least one adversary FINISHED (probe-exit=0) with a denominator and nothing came back wrong;
      NO-DATA nobody finished. Not a pass, and not a rejection either.
    Zero probes, all probes crashed, every child timed out and a missing log all land on NO-DATA, never CLEAN.

    AN ABORTED RUN'S PRINTED FINDINGS ARE READ LINE BY LINE (audit finding 8, 2026-09-27). probe_build withholds the
    counts line when the child died, and this reader skipped every log with no counts line, so a CRASH printed
    before the death vanished and a second, clean adversary made the lane CLEAN. The finding lines are probe_build's
    own output (the child's stdout is never echoed), so each is counted; max() keeps a finished run from counting
    its findings twice."""
    ran = crash = accept = 0
    for text in logs:
        hit = re.search(r"^PROBES\s+(\d+) run: (\d+) CRASH, (\d+) WRONG", text, re.M)
        crash += max(int(hit.group(2)) if hit else 0, len(re.findall(r"^CRASH\s", text, re.M)))
        accept += max(int(hit.group(3)) if hit else 0, len(re.findall(r"^WRONG-ACCEPT\?\s", text, re.M)))
        if hit and re.search(r"^probe-exit=0$", text, re.M):
            ran += int(hit.group(1))
    return ran, crash, accept, ("DIRTY" if crash + accept else ("CLEAN" if ran else "NO-DATA"))


# PROBES ARE OFF UNLESS ASKED FOR (plan E step 2c, 2026-10-01): every launcher (the runner, probe_round, repair_wave) reaches
# generation through this file, so the switch is honoured here, once. Nothing is generated, run or written: a launcher
# that reads a verdict file finds none, which reads NO-DATA, never CLEAN.
import loop_switches  # noqa: E402
if "--selftest" not in sys.argv and not loop_switches.probes_on():
    print("PROBES OFF: BROTHER_PROBES is not on (plan E step 2c); nothing was generated or run")
    sys.exit(0)
wave, pw = os.path.abspath(os.path.expanduser(sys.argv[1])), os.path.abspath(os.path.expanduser(sys.argv[2]))
# THE FAN OUT RUNS THE FROZEN CANDIDATE (U3, B5-08): `-m` resolves the module from the cwd, which was the landing tree.
# Asked before anything is written, so a refused code root (a proof phase with none set) sends nothing: exit 3.
import model_router as _MR   # BIN, this file's own directory, is on sys.path
import unit_ledger as _UL    # the grade reader grade_lane.sh's rule is written once in (grade_passed)
try:
    CODE_ROOT = _MR.code_root()
except _MR.Refused as _exc:
    print("REFUSED: the code root is refused (%s); no probe job is written and nothing is sent" % str(_exc)[:160]); sys.exit(3)
for d in ("prompts", "out", "findings", "logs"):
    os.makedirs(os.path.join(pw, d), exist_ok=True)
plan = json.load(open("docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", encoding="utf-8"))
spec_of = {sub: u["spec"] for u in plan["units"] for sub in (u.get("sub_units") or []) if u.get("spec")}
names = [l.strip().lower() for l in open(os.path.expanduser("~/.brothersbe-private-names"), encoding="utf-8") if l.strip() and not l.startswith("#")]
lanes = {}
only = re.compile(sys.argv[3]) if len(sys.argv) > 3 else None  # optional lane filter: the council's work in progress limit, not every pass at once
for g in sorted(glob.glob(os.path.join(wave, "grades", "*.txt"))):
    variant = os.path.basename(g)[:-4]; lane = re.sub(r"-r\d+$", "", variant)
    if only and not only.match(lane):
        continue
    # THE GRADER'S RULE, NOT ANY PASS LINE (2026-09-27): any line starting PASS bought probes, so a worker's own PASS
    # above the grader's FAIL, a PASS at exit 1 and a line reading PASSED all did. grade_lane.sh's rule, one reader.
    if lane not in lanes and _UL.grade_passed(g):
        lanes[lane] = variant
# FX-11.6 (REQ-FX11-21): the adversary seats are decided ONCE, here, through the roles file. With BROTHER_BREAKER on and
# BROTHER_ADVERSARY_MODEL set, loop_roles.adversary_models judges the choice; a refused choice adds no probe job, so
# every lane reads NO-DATA by the rule above, never a silent default. Otherwise it answers the plain pair, and today's
# seats (ADVERSARY_MODEL) stand exactly as they were. ADVERSARIES and ADVERSARY_MODEL keep their literal lines.
import loop_roles  # noqa: E402  this directory's copy (BIN is on sys.path)
try:
    _second = second_adversary()
    _seats = loop_roles.adversary_models(_second, dict(os.environ))
    ADVERSARY_SEATS = _seats if _seats != {"deepseek": "deepseek", "deepseek-b": _second} else dict(ADVERSARY_MODEL)
except ValueError as _exc:
    ADVERSARY_SEATS = {}
    print("ADVERSARY  %s; no probe job is added and every lane reads NO-DATA" % str(_exc)[:300])
jobs = []
for lane, variant in lanes.items():
    spec = spec_of.get(lane); build = os.path.join(wave, "out", variant + "-build.json")
    if not spec or not os.path.isfile(spec) or os.path.isfile(os.path.join(pw, "logs", lane + ".done")):
        continue
    brief = os.path.join(pw, "prompts", lane + "-probe.md")
    subprocess.run([sys.executable, os.path.join(BIN, "probe_brief.py"), spec, lane, build, brief], check=True, capture_output=True, timeout=300)
    text = open(brief, encoding="utf-8").read(); low = text.lower()
    if __import__("grade_build").private_hits(text) or len(text.encode()) > 195000:
        os.remove(brief); print("%-10s WITHHELD (private term or size)" % lane); continue
    cdir = probe_cache_dir(lane, spec); hits = 0
    for m in ADVERSARIES:  # owner order 2026-09-20: DeepSeek only, no Muse. Two lanes, both DeepSeek; the LABEL varies, the model does not
        if m not in ADVERSARY_SEATS:
            continue   # FX-11.6: a refused adversary choice adds no job and takes no cached probe; the lane reads NO-DATA
        out = os.path.join(pw, "out", "%s-probe-%s.json" % (lane, m))
        if not os.path.isfile(out) and probe_cache_take(cdir, m, out):
            hits += 1
        if not os.path.isfile(out):
            # sensitivity is EARNED by this file's own private-term screen above, never assumed:
            # or_fanout refuses an unlabelled job, because a caller that forgot to label its
            # content is exactly the caller whose content must not leave this machine.
            jobs.append({"id": "%s-probe-%s" % (lane, m), "model": ADVERSARY_SEATS[m],
                         "prompt_file": brief, "out": out, "estimated_cost": 0.02,
                         "expect": "json", "sensitivity": "public"})
    print("%-10s PROBES %s" % (lane, "cached %d of %d (key %s)" % (hits, len(ADVERSARIES), os.path.basename(cdir)) if hits else "fresh"))
if jobs:
    jf = os.path.join(pw, "jobs-%d.json" % len(glob.glob(os.path.join(pw, "jobs-*.json"))))
    json.dump(jobs, open(jf, "w"), indent=1)
    # never wait for the slowest adversary: a 15 minute straggler once held three whole chains idle for half an hour (2026-09-20).
    # Short per call timeout, no retry, and a hard cap on the whole wait; a lane is judged on whichever adversary answered.
    # PROBE_TIMEOUT is a knob, not a constant: 600 s was set for an empty machine, and under load both adversaries
    # timed out on two lanes in one night (2026-09-22). A timeout is set for the load it will actually run under.
    per_call = int(os.environ.get("PROBE_TIMEOUT", "600")) if str(os.environ.get("PROBE_TIMEOUT", "600")).isdigit() else 600
    proc = subprocess.Popen([sys.executable, "-m", "plugin.runtime.brother.core.or_fanout", jf, "--workers", "80", "--timeout", str(per_call), "--retries", "0",
                             "--results", jf.replace(".json", ".results.json")], stdout=open(jf.replace(".json", ".log"), "w"), stderr=subprocess.STDOUT,
                            cwd=CODE_ROOT)
    # WAIT FOR AN ANSWER PER LANE, NOT FOR EVERY CALL (A/B/C 2026-09-23: the probe stage was a median 605 s of a 15 minute
    # round; 24 of 56 calls stalled to 600 s while every success finished within 408 s). wave_wait ends the wave once each
    # lane has an answer and PROBE_ENOUGH seconds passed; a lane with none still holds it to the cap.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import wave_wait   # THIS directory's copy
    _outs = {}
    for _j in jobs: _outs.setdefault(_j["id"].rsplit("-probe-", 1)[0], []).append(_j["out"])
    _enough = int(os.environ.get("PROBE_ENOUGH", "300")) if str(os.environ.get("PROBE_ENOUGH", "300")).isdigit() else 300
    _how, _secs = wave_wait.wait(proc, _outs, per_call + 60, _enough)
    if _how != "done":
        proc.kill(); proc.wait(timeout=5)
        print("STRAGGLERS         cut loose after %d s (%s); lanes are judged on the adversaries that answered" % (_secs, "every lane had an answer" if _how == "enough" else "hard cap"))
print("%-10s %-14s %6s %6s %7s %-7s %s" % ("lane", "variant", "probes", "CRASH", "ACCEPT?", "fenced", "verdict"))
for lane, variant in sorted(lanes.items()):
    build = os.path.join(wave, "out", variant + "-build.json")
    try:
        b = json.load(open(build, encoding="utf-8"))
        fenced = any(str(e.get("path", "")).startswith("scripts/") for e in (b.get("edits") or []) + (b.get("tests") or []))
    except (ValueError, OSError):
        fenced = "?"
    logs = []; finds = []
    for m in ADVERSARIES:  # owner order 2026-09-20: DeepSeek only, no Muse. Two lanes, both DeepSeek; the LABEL varies, the model does not
        probe = os.path.join(pw, "out", "%s-probe-%s.json" % (lane, m))
        if not os.path.isfile(probe):
            continue
        log = os.path.join(pw, "logs", "%s-%s.log" % (lane, m))
        if not os.path.isfile(log):
            r = subprocess.run([sys.executable, os.path.join(BIN, "probe_build.py"), build, probe], capture_output=True, text=True, timeout=300)
            # THE FIRST LINE IS THE REAL WRITE TIME (MECHANISM-AUDIT item 21, 2026-09-26): probe_build.lessons()
            # reads this "# probe-at <epoch>" marker for its 24 hour window instead of the file's own mtime,
            # which a checkout, restore or copy can reset or preserve independent of when the probe actually ran.
            open(log, "w").write("# probe-at %.6f\n" % time.time() + r.stdout + r.stderr + "probe-exit=%d\n" % r.returncode)
        text = open(log, encoding="utf-8").read()
        logs.append(text)
        cdir = probe_cache_dir(lane, spec_of.get(lane, ""))
        if probe_ran(text): probe_cache_put(cdir, m, probe)
        else: probe_cache_drop(cdir, m)   # a probe that dies against this build is not a probe of this build
        finds += [l[:230] for l in text.splitlines() if l.startswith(("CRASH", "WRONG-ACCEPT?"))]
    ran, crash, accept, verdict = lane_verdict(logs)
    if finds:
        open(os.path.join(pw, "findings", lane + ".txt"), "w").write("\n".join(sorted(set(finds))) + "\n")
    done = os.path.join(pw, "logs", lane + ".done")
    # WRITTEN TWICE: a second probe_wave over the same wave directory (two racing council passes) must never
    # let a later unknown erase dirt that was already observed. A later CLEAN after a repair legitimately
    # replaces DIRTY, because the build it judged is the repaired one; a later NO-DATA judged nothing.
    if not (verdict == "NO-DATA" and os.path.isfile(done) and open(done, encoding="utf-8").read().strip() == "DIRTY"):
        open(done, "w").write(verdict + "\n")
    print("%-10s %-14s %6d %6d %7d %-7s %s" % (lane, variant, ran, crash, accept, fenced, verdict))
