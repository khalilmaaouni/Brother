#!/usr/bin/env python3
"""The mixed arms of a build round: which model writes each of the round's builds.
usage as a library:  worker_mix.picks(n, base, known, env=None) -> list of n model names
usage as a check:    python3 -B worker_mix.py --selftest
Owner 2026-09-22 23:1x (row 18): eight identical DeepSeek builds per round sample one arm eight times. BROTHER_WORKER_MIX names
the arms as "model:count,..." (default deepseek:5,sonnet:1); a model the registry does not know is dropped, and the
base picks (the registry's own order) fill whatever the mix leaves. An unreadable mix falls back to the base picks; with no
base and no known mix model the round has no model and the caller refuses, never a guessed name."""
import json, os, sys
import json, math, random, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unit_ledger  # noqa: E402  (last_rows: the one dedupe of the ledger, one row per build)
import model_router  # noqa: E402  (the registry is the one source of arms and the default mix, FX-31.6)


def views():
    """The registry's derived tables (model_router.derive_model_views), READ WHEN A CALLER ASKS AND NEVER AT IMPORT
    (review of 2026-10-04: computed at load, a registry the router refused made this module fail to import and took
    unit_runner down with it). Raises model_router.Refused; each caller below turns that into its own NO-DATA."""
    return model_router.derive_model_views(model_router.registry())


def default_mix():
    """"model:count,..." over the registry rows whose `arm` carries a `mix`, in row order (today deepseek:5,sonnet:1; muse retired 2026-10-05).
    Derived, never typed here, since FX-31.6. Raises model_router.Refused."""
    return views()["default_mix"]


def arm_keys():
    """((substring of a ledger row's model, arm), ...) in registry row order, from each arm row's `arm.keys` (FX-31.6).
    Raises model_router.Refused."""
    return views()["arm_of"]


def __getattr__(name):
    """DEFAULT_MIX and ARM_OF stay readable as module attributes for their callers, resolved on the read (PEP 562)."""
    if name == "DEFAULT_MIX":
        return default_mix()
    if name == "ARM_OF":
        return arm_keys()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def parse_mix(text):
    """Strict worker mix parser for H4.b.

    "model:count,..." returns [(model, count)] in first appearance order with
    repeated models summed and said on stdout. A non string, empty text, a
    token without exactly one colon, a non integer count, or a count <= 0
    raises ValueError naming the offending token.
    """
    if isinstance(text, bool) or not isinstance(text, str) or text == "":
        raise ValueError("worker_mix: malformed token %r" % (text,))
    out = []
    seen = {}
    for raw in text.split(","):
        tok = raw.strip()
        parts = tok.split(":")
        if len(parts) != 2:
            raise ValueError("worker_mix: malformed token %r" % (raw,))
        name = parts[0].strip().lower()
        count = parts[1].strip()
        if not name or not count or not count.isdigit():
            raise ValueError("worker_mix: malformed token %r" % (raw,))
        c = int(count)
        if c <= 0:
            raise ValueError("worker_mix: malformed token %r" % (raw,))
        if name in seen:
            seen[name] += c
            print("worker_mix: repeated model %s summed to %d" % (name, seen[name]))
        else:
            seen[name] = c
            out.append((name, c))
    return [(name, seen[name]) for name, _ in out]


def parse(spec):
    """Strict worker mix parser. Raises ValueError on malformed input."""
    return parse_mix(spec)


LEDGER = os.path.expanduser("~/.claude/evidence/unit-ledger.jsonl")
_ABSENT = object()   # "the caller set nothing", distinct from a caller setting None


def arm_of(actual_model):
    """The arm a ledger row's actual model belongs to, or None when it names no arm, or when the registry that names
    the arms refuses (NO-DATA: no arm is known, so no row is credited to one)."""
    if not isinstance(actual_model, str):
        return None
    m = actual_model.lower()
    try:
        keys = arm_keys()
    except model_router.Refused:   # sbe: allow-silent a reader that never rewrites: with no registry no arm is known, so the row is credited to none and arm_stats gives the bandit nothing to shift on (the configured mix stands)
        return None
    return next((arm for key, arm in keys if key in m), None)


RUN_REFUSALS = ("BudgetExceeded", "DrainRefused", "Stopped")   # the run refused the call; the model was never asked
CONFIG_WAIT = "CONFIG_WAIT"   # the program does not know the model (2026-09-30): never the arm's failure, never a reward


def timed_out(call_error):
    """True when a call ran out of time (the fan out's deadline, a subprocess timeout, or the bridge's own wall). TIME IS
    PATIENCE'S, NOT THE BANDIT'S (2026-09-27): such a call teaches patience that the model needs longer, and is never
    charged as the model's failure, since a queue or an admission wait of ours can cause it too."""
    e = call_error if isinstance(call_error, str) else ""
    return e.startswith(("STALLED_AFTER_DEADLINE", "TimeoutExpired")) or ("no answer from" in e and "within" in e)


def arm_stats(ledger_path=LEDGER, unit_class=None, since_s=7 * 86400, now=None):
    """{arm: [passes, fails]} over graded builds in the ledger's last since_s seconds, optionally one unit class.
    Unreadable ledger or junk rows: {} (the mix then stays the configured one, never a guess). ONE ROW PER BUILD
    (finding 7, 2026-09-27): a concurrent append left one build on disk twice and it counted as two rewards.
    AN EMPTY ANSWER IS A FAIL OF THE ARM THAT WAS ASKED (2026-09-27): it has no answering model, so it was invisible here and
    an arm that never answers kept its prior share forever (muse 206 of 266 calls empty, sonnet 439 of 508, still seated in
    82 rounds). A call the RUN refused (budget, drain, stop: 560 of the failed calls on disk) is not the arm's failure and
    is left out, and so is a row that does not say why its call failed (rows written before call_error existed): an
    unknown cause is not evidence against the model (Codex, 2026-09-27)."""
    out = {}; floor = (time.time() if now is None else now) - since_s
    try:
        for r in unit_ledger.last_rows(ledger_path):
            if (r.get("run_at") or 0) < floor: continue
            if unit_class and r.get("unit_class") != unit_class: continue
            if r.get("grade") in ("PASS", "FAIL"):
                arm = arm_of(r.get("actual_model")); won = r["grade"] == "PASS"
            elif (r.get("ok") is False and r.get("status") not in ("UNFUNDED", "DRAINED") and isinstance(r.get("call_error"), str)
                  and r["call_error"] and not r["call_error"].startswith(RUN_REFUSALS + (CONFIG_WAIT,)) and not timed_out(r["call_error"])):
                arm = arm_of(r.get("model")); won = False
            else:
                continue
            if arm: out.setdefault(arm, [0, 0])[0 if won else 1] += 1
    except TypeError: return {}
    return out


# ROUND PATIENCE PER MODEL FAMILY (owner 2026-09-23: "It should adapt to each type of model"). The runner cut every round
# at 150 s once two builds were in, and hard at 480 s, numbers tuned for DeepSeek (median about 2 min). Sonnet builds took
# 293 to 391 s in the A/B/C test, so they were paid for and then killed: 21 of 21 NO-DATA in arm B. A round now waits
# for its SLOWEST family's measured 75th percentile; a family with fewer than PATIENCE_MIN samples uses its transport's
# default. Unknown transport or unreadable ledger: the slowest default, never the fastest (fewer kills, never more).
# ON THE MODEL'S OWN CLOCK (FX-10, BROTHER_ONE_DEADLINE=on): a bridge sample is unit_ledger's model_seconds, the dispatcher's
# reserve to settle span, which starts after the slot wait; seconds (the fan out's wall clock) counted the queue, so a busy
# queue raised patience instead of clearing the queue (pace note: a median 358 s by that clock, 144 s of model). A bridge
# row with no span was queue, not model, and is not a sample; a codex or claude row has no dispatcher queue and keeps its
# seconds. Below PATIENCE_MIN samples a model uses its registry row's optional measured.p75_s, else its transport default.
# Switch off (the default): today's clock and arithmetic, unchanged.
TRANSPORT_PATIENCE = {"bridge": 150, "codex": 300, "claude": 420}
PATIENCE_MIN, PATIENCE_FLOOR, PATIENCE_CEILING = 5, 150, 900
PATIENCE_RECENT = 200   # the newest samples per model: a provider's speed moves by the hour (2026-09-27: 7 days said p75 170 s
                        # while the last 200 calls said 346 s, so the deadline stayed 420 s and 10 of 13 calls died on it)


DEADLINE_CAP_S = 900       # the one deadline never exceeds this (FX-10 acceptance; PATIENCE FINDING names a family it hurts)
DEADLINE_CAP_RANGE = (300, 3600)


def deadline_cap(env=None):
    """The cap on the one deadline: DEADLINE_CAP_S, or BROTHER_DEADLINE_CAP_S when set (owner 2026-10-02: "Give more time
    maybe? Quality over speed on Claude?"; Claude builds measured p50 504 s, max 943 s against the 900 s cap). An unset
    value is the default; a set value outside DEADLINE_CAP_RANGE or not an integer is refused, never read as the default,
    so a typo can never quietly run the night on 900 s."""
    raw = (os.environ if env is None else env).get("BROTHER_DEADLINE_CAP_S")
    if raw is None or str(raw).strip() == "":
        return DEADLINE_CAP_S
    try:
        v = int(str(raw).strip())
    except ValueError:
        raise ValueError("worker_mix: BROTHER_DEADLINE_CAP_S must be an integer, got %r" % raw)
    if not DEADLINE_CAP_RANGE[0] <= v <= DEADLINE_CAP_RANGE[1]:
        raise ValueError("worker_mix: BROTHER_DEADLINE_CAP_S %d is outside %s" % (v, DEADLINE_CAP_RANGE))
    return v
DISPATCH_FLOOR_S = 300     # openrouter_strict.MIN_TIMEOUT_SECONDS: a bridge call under it is refused TimeoutTooLow, unpaid
FINDING_RATIO = 1.5        # p90 above this times p75: the deadline multiplier is short for that family


def one_deadline(env=None):
    """BROTHER_ONE_DEADLINE (FX-10): "on" puts patience on the model's own clock and every runner bound on deadlines();
    "off", unset or empty keeps today's. Any other value says ONE-DEADLINE NO-DATA and keeps today's, never a guess.
    or_fanout.one_deadline reads the same rule (that package must not import this one); a test pins both."""
    v = (os.environ if env is None else env).get("BROTHER_ONE_DEADLINE", "")
    if v == "on":
        return True
    if v not in ("off", "", None):
        print("ONE-DEADLINE NO-DATA: %r is not on or off; today's deadlines stand" % (v,), flush=True)
    return False


def _positive(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


def _checked(models, transports, since_s, now):
    """The one validation patience() and measured() route through: (models set, transports dict, window floor)."""
    if models is None or isinstance(models, (str, bytes)):
        raise ValueError("worker_mix: models must be an iterable of model names")
    try:
        models_set = set(models)
    except TypeError:
        raise ValueError("worker_mix: models must contain hashable model names")
    if any(not isinstance(m, str) for m in models_set):
        raise ValueError("worker_mix: every model name must be a str")
    if transports is None:
        transports = {}
    if not isinstance(transports, dict):
        raise ValueError("worker_mix: transports must be a dict")
    if isinstance(since_s, bool) or not isinstance(since_s, (int, float)) or since_s != since_s:
        raise ValueError("worker_mix: since_s must be a finite number")
    if now is not None and (isinstance(now, bool) or not isinstance(now, (int, float)) or now != now):
        raise ValueError("worker_mix: now must be a finite number or None")
    return models_set, transports, (time.time() if now is None else now) - since_s


def _samples(ledger_path, floor_t, transports, model_clock):
    """model -> [(run_at, seconds)] of every build in the window that answered or ran out of time. model_clock False:
    today's seconds. True: the span for a row whose model_clock is span, seconds for a model off the bridge (no dispatcher
    queue), and nothing for a bridge row without a span (it was queue, not model)."""
    secs = {}
    try:
        for r in unit_ledger.last_rows(ledger_path):     # one sample per build, never one per physical row (finding 7)
            if not (r.get("ok") or timed_out(r.get("call_error"))) or (r.get("run_at") or 0) < floor_t: continue
            if not model_clock:
                s = r.get("seconds")
            elif r.get("model_clock") == "span":
                s = r.get("model_seconds")
            elif transports.get(r.get("model")) != "bridge":
                s = r.get("seconds")
            else:
                continue
            if isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0:
                secs.setdefault(r.get("model"), []).append((r.get("run_at") or 0, float(s)))
    except TypeError:
        secs = {}
    return secs


def _registry_p(model, registry):
    """(p75_s, p90_s) from the model's registry row's optional measured block, or None. Read, never written. A block that
    is present and unusable says MEASURED NO-DATA and is None: a corrupt number never shortens a deadline."""
    row = registry.get(model) if isinstance(registry, dict) else None
    if not isinstance(row, dict) or "measured" not in row:
        return None
    block = row["measured"]
    p75 = block.get("p75_s") if isinstance(block, dict) else None
    if not _positive(p75):
        print("MEASURED NO-DATA: %s measured.p75_s %r is not a positive number; its transport default stands"
              % (model, p75 if isinstance(block, dict) else block), flush=True)
        return None
    p90 = block.get("p90_s")
    return float(p75), (float(p90) if _positive(p90) else None)


def measured(models, transports, registry=None, ledger_path=LEDGER, since_s=7 * 86400, now=None):
    """Per model {"p75_s", "p90_s", "n", "source"} on the model's own clock (FX-10): source ledger at PATIENCE_MIN
    samples or more (the newest PATIENCE_RECENT), else registry (its measured block), else default (the transport's,
    the slowest for an unknown transport; p90_s None)."""
    models_set, transports, floor_t = _checked(models, transports, since_s, now)
    secs = _samples(ledger_path, floor_t, transports, True)
    worst = max(TRANSPORT_PATIENCE.values())
    out = {}
    for m in sorted(models_set):
        xs = sorted(s for _, s in sorted(secs.get(m, []))[-PATIENCE_RECENT:])
        if len(xs) >= PATIENCE_MIN:
            out[m] = {"p75_s": xs[int(0.75 * (len(xs) - 1))], "p90_s": xs[int(0.9 * (len(xs) - 1))], "n": len(xs), "source": "ledger"}
            continue
        reg = _registry_p(m, registry)
        if reg:
            out[m] = {"p75_s": reg[0], "p90_s": reg[1], "n": len(xs), "source": "registry"}
        else:
            out[m] = {"p75_s": TRANSPORT_PATIENCE.get(transports.get(m), worst), "p90_s": None, "n": len(xs), "source": "default"}
    return out


def patience_of(block):
    """Seconds a round waits: the max of the clamped per model p75 of a measured() block (the floor when it is empty)."""
    return int(min(PATIENCE_CEILING, max([PATIENCE_FLOOR] + [b["p75_s"] for b in block.values()])))


def findings(block):
    """PATIENCE FINDING lines, one per model whose p90 exceeds FINDING_RATIO times its p75: the deadline, a multiple of
    p75 capped at DEADLINE_CAP_S, is short for that family."""
    return ["PATIENCE FINDING %s p90 %d s > %s x p75 %d s: the deadline multiplier is short for this family"
            % (m, b["p90_s"], FINDING_RATIO, b["p75_s"]) for m, b in sorted(block.items())
            if _positive(b.get("p90_s")) and _positive(b.get("p75_s")) and b["p90_s"] > FINDING_RATIO * b["p75_s"]]


def deadlines(pat, knob=420, grace=60, settle=30):
    """THE ONE DEADLINE SOURCE (FX-10): {"call", "round", "wave", "reap"} seconds from one patience. call and round are
    T = min(DEADLINE_CAP_S, max(PATIENCE_FLOOR, knob, 1.5 x pat)); the knob (FANOUT_TIMEOUT_S) is a lower bound. wave, the
    fan out's own deadline, is 2 T + settle, because a job spends up to one T waiting for a dispatcher slot and a second T
    in the call; reap is 2 T + grace, after the wave. An inconsistent set is refused before any call is launched."""
    for name, v in (("pat", pat), ("knob", knob), ("grace", grace), ("settle", settle)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("worker_mix: deadlines %s must be an int, got %r" % (name, v))
    if not PATIENCE_FLOOR <= pat <= PATIENCE_CEILING:
        raise ValueError("worker_mix: deadlines pat %d is outside [%d, %d]" % (pat, PATIENCE_FLOOR, PATIENCE_CEILING))
    if knob <= 0:
        raise ValueError("worker_mix: deadlines knob must be positive, got %d" % knob)
    if settle < 0 or grace <= settle:
        raise ValueError("worker_mix: the reaper must fire after the fan out's own deadline (grace %d, settle %d)" % (grace, settle))
    t = min(deadline_cap(), max(PATIENCE_FLOOR, knob, int(pat * 1.5)))
    return {"call": t, "round": t, "wave": 2 * t + settle, "reap": 2 * t + grace}


def patience(models, transports, ledger_path=LEDGER, since_s=7 * 86400, now=None, *, registry=None, env=None):
    """Seconds a round should wait for its slowest model family: max over models of the measured p75 build time
    (the ledger's last since_s), or the transport default below PATIENCE_MIN samples.
    A CALL THAT RAN OUT OF TIME IS A SAMPLE TOO, at the seconds it was given (2026-09-27): learning from successes only,
    patience could never see that its own deadline was too short, and 122 of 447 calls that day died on it.
    BROTHER_ONE_DEADLINE=on (FX-10, read from env when given): the model's own clock and the registry measured block,
    through measured(); otherwise today's clock, unchanged."""
    if env is not None and not isinstance(env, dict):
        raise ValueError("worker_mix: env must be a dict or None")
    models_set, transports, floor_t = _checked(models, transports, since_s, now)
    if one_deadline(env):
        return patience_of(measured(models_set, transports, registry, ledger_path, since_s, now))
    secs = _samples(ledger_path, floor_t, transports, False)
    worst = max(TRANSPORT_PATIENCE.values())
    out = PATIENCE_FLOOR
    for m in models_set:
        xs = sorted(s for _, s in sorted(secs.get(m, []))[-PATIENCE_RECENT:])
        p = xs[int(0.75 * (len(xs) - 1))] if len(xs) >= PATIENCE_MIN else TRANSPORT_PATIENCE.get((transports or {}).get(m), worst)
        out = max(out, p)
    return int(min(PATIENCE_CEILING, max(PATIENCE_FLOOR, out)))


RECORD_MIN = 8   # rulings before an arm competes on its record instead of keeping its configured share


def allocated(mix, n):
    """Scale configured weights to real seats, preserving diversity when seats permit.

    Truncating an expanded eight-seat mix to three seats bought only its first
    model. Give each arm a seat when possible, then fill the largest deficit
    against its weighted quota. Integer arithmetic keeps large weights exact.
    Ties retain configuration order. The full configured size is unchanged.
    """
    if not mix or n <= 0:
        return []
    total = sum(count for _, count in mix)
    n = min(n, total)
    seats = [0] * len(mix)
    for i in sorted(range(len(mix)), key=lambda i: -mix[i][1])[:n]:
        seats[i] = 1
    for _ in range(n - sum(seats)):
        i = max(range(len(mix)), key=lambda i: n * mix[i][1] - seats[i] * total)
        seats[i] += 1
    return [name for (name, _), count in zip(mix, seats) for _ in range(count)]


def shifted(mix, n, stats, rng):
    """THE BANDIT (fix 4, 2026-09-22): eight identical builds per round were k samples of one arm. Each of the n slots is
    won by the arm with the highest Beta draw, Beta(mix count + passes, 1 + fails): the configured mix is the prior, the
    grader verdicts in the ledger are the reward, and an arm with no record keeps its prior share so it is still explored.
    With no stats at all the configured mix is returned unchanged."""
    if not isinstance(mix, (list, tuple)):
        raise ValueError("worker_mix: shifted mix must be a list, got %r" % (mix,))
    if isinstance(n, bool) or not isinstance(n, int):
        raise ValueError("worker_mix: shifted n must be an int, got %r" % (n,))
    if n < 0:
        raise ValueError("worker_mix: shifted n must not be negative")
    for item in mix:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise ValueError("worker_mix: shifted mix item must be a pair, got %r" % (item,))
        name, count = item
        if not isinstance(name, str) or isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("worker_mix: shifted mix item must be (str, positive int), got %r" % (item,))
    if stats is not None and not isinstance(stats, dict):
        raise ValueError("worker_mix: shifted stats must be a dict or None")
    if rng is not None and not hasattr(rng, "betavariate"):
        raise ValueError("worker_mix: shifted rng must provide betavariate")
    if not stats: return allocated(mix, n)
    # AN ARM WITHOUT A RECORD KEEPS ITS CONFIGURED SHARE: a Thompson draw from a wide prior wins half the slots for an arm
    # nobody has measured (seen in this file's own selftest), which is exploration paid at the worker's price. Its configured
    # count is the exploration budget the owner set; once it has RECORD_MIN rulings it competes on its record.
    recorded = [(name, c) for name, c in mix if sum(stats.get(name, (0, 0))) >= RECORD_MIN]
    recorded_names = {name for name, _ in recorded}
    out = [name for name in allocated(mix, n) if name not in recorded_names]
    if not recorded: return allocated(mix, n)
    while len(out) < n:
        best, draw = None, -1.0
        for name, c in recorded:
            p, f = stats.get(name, (0, 0)); x = rng.betavariate(max(c, 1) + p, 1 + f)
            if x > draw: best, draw = name, x
        out.append(best)
    return out


def picks(n, base, known, env=None, stats=None, rng=None):
    """n model names: the mix first (only models in `known`, shifted by `stats` when given), then the base picks round
    robin. Empty when nothing is known."""
    if isinstance(n, bool):
        return []
    try:
        n = int(n)
    except (TypeError, ValueError):
        return []
    if n <= 0:
        return []
    if env is None:
        env_map = os.environ
    elif isinstance(env, dict):
        env_map = env
    else:
        return []
    if stats is not None and not isinstance(stats, dict):
        return []
    if rng is not None and not hasattr(rng, "betavariate"):
        return []
    if known is None:
        known_set = set()
    else:
        if isinstance(known, (str, bytes)):
            return []
        try:
            known_set = set(known)
        except TypeError:
            return []
        if any(not isinstance(k, str) for k in known_set):
            return []
    # EVERY ARGUMENT IS VALIDATED BEFORE THE PIN ANSWERS (2026-09-30): the pin shortcut returned before `base` was
    # read, so under the loop's own BROTHER_PIN_MODEL a hostile base came back as the pinned model, and FX-10's
    # hostile input tests failed only inside the loop (the unit could not close).
    if base is None:
        base_list = []
    else:
        if isinstance(base, (str, bytes)):
            return []
        try:
            base_list = list(base)
        except TypeError:
            return []
        for b in base_list:
            if not isinstance(b, str):
                return []
    # The caller already checked role eligibility. Mixing may never replace
    # its exclusive pin, including when history or a default mix is present.
    pin = env_map.get("BROTHER_PIN_MODEL")
    if pin:
        if not isinstance(pin, str) or pin not in known_set:
            return []
        return [pin] * n
    spec = env_map.get("BROTHER_WORKER_MIX", _ABSENT)   # absent reads the registry default; present, None included, is the caller's
    try:
        parsed = parse_mix(default_mix() if spec is _ABSENT else spec)
    except model_router.Refused as exc:
        print("worker_mix: the registry refuses (%s), so there is no default mix; the base picks fill the round" % exc)
        parsed = []
    except ValueError:
        parsed = []
    for name, _ in parsed:
        if name not in known_set:   # BY NAME: a mix arm the registry does not seat (unknown, shadow or retired) is said, never silently filled
            print("worker_mix: %s is not a seatable registry model and is dropped from the mix" % name)
    mix = [(name, c) for name, c in parsed if name in known_set]
    try:
        out = shifted(mix, n, stats, rng or random.Random())
    except ValueError:
        return []
    out = out[:n]
    base = [b for b in base_list if b in known_set]   # never an unknown model, even as a fallback (finding 28, 2026-09-26)
    i = 0
    while len(out) < n and base:
        out.append(base[i % len(base)]); i += 1
    return out[:n]


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="wm-") as _d:   # its files never outlive the check (FX-10)
        return _selftest_body(_d)


def _selftest_body(_d):
    known = {"deepseek", "muse", "sonnet", "opus"}
    _lp = os.path.join(_d, "l.jsonl"); _now = 1000000.0
    _ids = views()["bridge_aliases"]; _ds, _mu = _ids["deepseek"], _ids["muse"]   # the ids come from the registry rows, never typed here (FX-31.7)
    with open(_lp, "w") as fh:
        for am, g, at in [(_ds, "PASS", _now)] * 40 + [(_ds, "FAIL", _now)] * 60 + [(_mu, "FAIL", _now)] * 50 + [(_mu, "PASS", _now - 30 * 86400)] * 50:
            fh.write(json.dumps({"actual_model": am, "grade": g, "run_at": at, "unit_class": "code"}) + "\n")
        fh.write(json.dumps({"actual_model": "deepseek/x", "grade": "NO-DATA", "run_at": _now}) + "\n"); fh.write("junk\n")
    _st = arm_stats(_lp, now=_now); _rng = random.Random(7)
    _sh = shifted([("deepseek", 5), ("muse", 2), ("sonnet", 1)], 80, _st, _rng)
    cases = [("arm stats count PASS and FAIL per arm inside the window only, NO-DATA and junk skipped", _st == {"deepseek": [40, 60], "muse": [0, 50]}),
             ("a unit class filter narrows the stats", arm_stats(_lp, unit_class="docs", now=_now) == {}),
             ("an absent ledger gives no stats", arm_stats(os.path.join(_d, "none")) == {}),
             ("the bandit shifts slots toward the arm the grader rewards; an arm with no record keeps exactly its configured share", _sh.count("deepseek") > 60 and _sh.count("sonnet") == 1 and len(_sh) == 80),
             ("an arm whose record is all failures still appears only by chance, never by right", _sh.count("muse") < 10),
             ("with no stats the configured mix comes back unchanged", shifted([("deepseek", 5), ("muse", 2), ("sonnet", 1)], 8, {}, random.Random(1)) == ["deepseek"] * 5 + ["muse"] * 2 + ["sonnet"]),
             ("picks with stats still only names known models and fills from the base", set(picks(8, ["deepseek"], {"deepseek", "muse"}, {}, stats=_st, rng=random.Random(3))) <= {"deepseek", "muse"} and len(picks(8, ["deepseek"], {"deepseek", "muse"}, {}, stats=_st, rng=random.Random(3))) == 8),
("the default mix at eight is 5 deepseek, 1 sonnet, the base filling the rest; muse is retired (2026-10-05) and never seats", default_mix() == "deepseek:5,sonnet:1" and picks(8, ["deepseek"], known, {}) == ["deepseek"] * 5 + ["sonnet"] + ["deepseek"] * 2),
             ("a smaller round preserves diversity within its seat limit", picks(3, ["deepseek"], known, {}) == ["deepseek", "deepseek", "sonnet"] and picks(6, ["deepseek"], known, {}) == ["deepseek"] * 5 + ["sonnet"]),
             ("a larger round fills from the base picks", picks(10, ["deepseek", "muse"], known, {})[8:] == ["deepseek", "muse"]),
             ("an unknown mix model is dropped, the base fills its seats", picks(8, ["deepseek"], {"deepseek", "muse"}, {}) == ["deepseek"] * 8),
             ("an unreadable mix falls back to the base", picks(3, ["deepseek"], known, {"BROTHER_WORKER_MIX": "nonsense"}) == ["deepseek"] * 3),
             ("no known models and no base gives an empty list, never a name", picks(3, [], set(), {"BROTHER_WORKER_MIX": "zzz:3"}) == [] and picks("x", ["deepseek"], known) == [] and picks(0, ["deepseek"], known) == [])]
    _now = 1_800_000_000; _led = open(os.path.join(_d, "patience.jsonl"), "w")
    for _s in (300, 320, 340, 360, 380, 400):   # six Sonnet builds: p75 of 300..400 is 360 (index 3 of 6)
        _led.write(json.dumps({"model": "sonnet", "ok": True, "seconds": _s, "run_at": _now}) + "\n")
    _led.write(json.dumps({"model": "sonnet", "ok": False, "seconds": 5000, "run_at": _now}) + "\n")   # a failed call never sets patience
    _led.write(json.dumps({"model": "deepseek", "ok": True, "seconds": 250, "run_at": _now}) + "\n")    # one sample above the default: too few, default applies
    for _s in (2000,) * 6: _led.write(json.dumps({"model": "fable", "ok": True, "seconds": _s, "run_at": _now}) + "\n")   # above the ceiling
    _led.close(); _T = {"sonnet": "claude", "deepseek": "bridge", "astra": "codex"}
    cases += [("patience is the slowest family's measured p75, failed calls ignored", patience(["deepseek", "sonnet"], _T, _led.name, now=_now) == 360),
              ("a family with too few samples uses its transport default", patience(["deepseek"], _T, _led.name, now=_now) == 150 and patience(["astra"], _T, _led.name, now=_now) == 300),
              ("an unknown transport or unreadable ledger waits the slowest default, never the fastest", patience(["x"], {}, _led.name, now=_now) == 420 and patience(["astra"], _T, "/nonexistent", now=_now) == 300),
              ("patience is clamped to the ceiling", patience(["fable"], _T, _led.name, now=_now) == PATIENCE_CEILING and patience([], _T, _led.name, now=_now) == PATIENCE_FLOOR),
              ("samples outside the window are ignored", patience(["sonnet"], _T, _led.name, since_s=10, now=_now + 3600) == 420)]
    # FX-10: the registry's measured block below PATIENCE_MIN samples, and the one deadline it gives
    _reg = {"sonnet": {"transport": "claude", "measured": {"p75_s": 360, "p90_s": 400}}}
    _on = {"BROTHER_ONE_DEADLINE": "on"}
    _p = patience(["sonnet"], _T, os.path.join(_d, "none.jsonl"), now=_now, registry=_reg, env=_on)
    cases += [("measured p75 360 gives patience 360, call 540 and round 540",
               _p == 360 and deadlines(_p)["call"] == 540 and deadlines(_p)["round"] == 540),
              ("p90 above 1.5 x p75 prints a finding",
               findings({"m": {"p75_s": 200, "p90_s": 350}}) == ["PATIENCE FINDING m p90 350 s > 1.5 x p75 200 s: the deadline multiplier is short for this family"]
               and findings({"m": {"p75_s": 200, "p90_s": 290}}) == [])]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__": sys.exit(selftest() if "--selftest" in sys.argv else 2)
