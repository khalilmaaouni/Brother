#!/usr/bin/env python3
"""Proof that every model a run will call answers through the program the run will use, BEFORE the run may start.

WHY (2026-09-30). The intake said READY because the pinned `claude` existed and was executable. It was 2.1.251, which
does not know claude-opus-5-5: every call exited `[claude-code:unrecognized_model]`, 78 attempts were spent and 7 sub
units parked EXHAUSTED in 6 minutes. An executable file is not evidence that a model answers through it. The only
evidence is a call: one minimal real request per distinct invocation, through model_call.call_one, the same boundary
and the same flags every production call uses (model_call.build_invocation builds both).

A PROOF IS ONE OF THREE THINGS:
  OK       the program answered "12" to "7 plus 5" as the requested model, with no error and no substitution
  FAIL     the call ran and was rejected (the program does not know the model, an error result, a wrong answer)
  NO-DATA  no evidence: a timeout, an unreadable version, a spent proof allowance. NEVER a pass, never a start.
A FAIL or a NO-DATA prints NO START naming the role, the model, the program and its version, the cause and the remedy.
Reachability cannot be waived.

BOUNDED: each probe reserves a conservative cost bound against an explicit intake allowance (PER_CALL_USD against
BROTHER_PROOF_ALLOWANCE_USD, default ALLOWANCE_USD) and time against TOTAL_S; a version query waits VERSION_TIMEOUT_S.
A probe that cannot fit its transport's own floor (the bridge refuses calls under 300 s) is NO-DATA, never weakened.

CACHED: a success is reused for TTL_S, keyed by (transport, program path, program version, model id) and bound to the
program's fingerprint, the invocation profile, this adapter code, the endpoint and the account. Any of them changing
is a new question. Probes of one key are serialised by a lock, so two intakes never pay twice.

A success also closes the configuration breaker for its (transport, model), with its own proof time, so a stale
answer can never close a newer fault (breaker.close_config).

usage:  model_reachability.py prove <model> [<model> ...]    one proof each, printed; exit 0 only when every one is OK
        model_reachability.py --selftest
"""
import fcntl, hashlib, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import breaker as BR  # noqa: E402
import model_call as MC  # noqa: E402
import model_router as R  # noqa: E402

TTL_S = 900.0
VERSION_TIMEOUT_S = 5.0
TOTAL_S = 600.0
LOCK_WAIT_S = TOTAL_S
ALLOWANCE_USD = 1.00
#: A conservative ceiling per proof call, never the expected cost: the trimmed Claude child answered 7+5 for 0.0086 USD
#: on 2026-09-23; an Opus or Codex answer is bounded well under these.
PER_CALL_USD = {"claude": 0.10, "codex": 0.10, "bridge": 0.05}
PROBE_TIMEOUT_S = {"claude": 120.0, "codex": 180.0, "bridge": 300.0}
FLOOR_S = {"bridge": 300.0}   # the dispatcher refuses a bridge call with less
QUESTION = "What is 7 plus 5? Reply with only the number."
JEV_QUESTION = json.dumps({"state": {"fact": "7 plus 5 is 12"},
                           "questions": {"ok": {"type": "noul", "instructions": "Answer true if 7 plus 5 is 12."}}})
ANSWER_RE = re.compile(r"\s*12\s*\.?\s*")
SEMVER_RE = re.compile(r"(\d+(?:\.\d+)+)")


def record_path(env=None):
    """The resolved program record: rewritten ONLY when the resolved set changes, so its mtime is a fact for
    runner_pool.fact_time. brother_paths.program_record_path is the reader's copy; a test holds the three equal."""
    e = os.environ if env is None else env
    return e.get("BROTHER_PROGRAM_RECORD") or os.path.join(e.get("HOME") or os.path.expanduser("~"), ".claude", "evidence", "loop-programs.json")


def cache_path(env=None):
    e = os.environ if env is None else env
    return e.get("BROTHER_PROOF_CACHE") or os.path.expanduser("~/.claude/evidence/loop-intake/proofs.json")


class Budget(object):
    """The intake's explicit proof allowance: money bound and wall time. reserve() is called before every probe."""

    def __init__(self, usd=None, seconds=TOTAL_S, clock=time.time, env=None):
        e = os.environ if env is None else env
        if usd is None:
            try:
                usd = float(e.get("BROTHER_PROOF_ALLOWANCE_USD") or ALLOWANCE_USD)
            except ValueError:
                usd = 0.0   # an unreadable allowance allows nothing: every probe is NO-DATA, named
        self.usd, self.left_usd, self.clock = float(usd), float(usd), clock
        self.deadline = clock() + float(seconds)
        self.calls = 0

    def remaining_s(self):
        return max(0.0, self.deadline - self.clock())

    def reserve(self, transport):
        """'' and the bound reserved, or why the allowance cannot take one more probe."""
        bound = PER_CALL_USD.get(transport)
        if bound is None:
            return "no cost bound is known for transport %r" % transport
        if bound > self.left_usd + 1e-9:
            return "the intake's proof allowance is spent (%.2f of %.2f USD reserved over %d probe(s))" % (
                self.usd - self.left_usd, self.usd, self.calls)
        if self.remaining_s() < FLOOR_S.get(transport, 1.0):
            return "the intake's proof time is spent (%.0f s left, the %s transport needs %.0f s)" % (
                self.remaining_s(), transport, FLOOR_S.get(transport, 1.0))
        self.left_usd -= bound
        self.calls += 1
        return ""


def version_of(program, timeout=VERSION_TIMEOUT_S, run=None):
    """The program's own version (`<program> --version`), or None: unreadable is NO-DATA, never a guess."""
    run = run or subprocess.run
    try:
        r = run([program, "--version"], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    m = SEMVER_RE.search((r.stdout or "") + " " + (r.stderr or ""))
    return m.group(1) if m else None


def version_key(v):
    """Semantic order: 2.1.284 above 2.1.251 above 2.1.99, never the text order."""
    return tuple(int(x) for x in SEMVER_RE.search(v).group(1).split(".")) if isinstance(v, str) and SEMVER_RE.search(v) else ()


def fingerprint(path):
    """brother_paths.program_fingerprint: the same function the program record is read back with."""
    try:
        return R._paths().program_fingerprint(path)
    except (R.Refused, ImportError, OSError):
        try:   # the same format, for a tree whose brother_paths cannot be reached (no code root)
            st = os.stat(path)
        except (OSError, TypeError, ValueError):
            return None
        return "%s|%d|%d|%d" % (os.path.realpath(path), st.st_size, st.st_mtime_ns, st.st_ino)


def code_rev():
    """This adapter's code: the files that build and judge the call. A change to any of them is a new question."""
    h = hashlib.sha256()
    for name in ("model_call.py", "model_router.py", "breaker.py", "model_reachability.py"):
        try:
            with open(os.path.join(HERE, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"missing:" + name.encode())
    return h.hexdigest()[:16]


def _program_version(transport, program, run=None):
    if transport == "bridge":
        fp = fingerprint(program)
        return ("or_ask-" + hashlib.sha256(fp.encode()).hexdigest()[:12]) if fp else None
    return version_of(program, run=run)


def _binding(inv, version, reg_row):
    t = inv.transport
    account = "default"
    if t == "bridge":
        try:
            account = BR.account_of("bridge", list(inv.argv))
        except ValueError:
            account = "unknown"
    fp = fingerprint(inv.program)
    if t == "bridge":
        fp = "%s+%s" % (fingerprint(sys.executable), fp)
    return {"transport": t, "program": os.path.realpath(inv.program) if inv.program else inv.program, "version": version,
            "model": inv.name, "model_id": inv.model_id, "fingerprint": fp, "profile": inv.profile, "code": code_rev(),
            "endpoint": {"claude": "anthropic", "codex": "openai", "bridge": "openrouter"}.get(t, t), "account": account}


def cache_key(b):
    return "|".join(str(b.get(k)) for k in ("transport", "program", "version", "model_id"))


BOUND_FIELDS = ("fingerprint", "profile", "code", "endpoint", "account")


class _Locked(object):
    def __init__(self, path):
        self.path, self.fd = path + ".lock", None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.time() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() >= deadline:
                    os.close(self.fd); self.fd = None
                    raise OSError("the proof cache lock is held")
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
        return False


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}   # an unreadable cache is an empty cache: it only ever costs a probe, never grants one


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def cache_get(b, now, path=None):
    """The cached proof for this binding, or None. Every bound field must equal, and it must be younger than TTL_S."""
    hit = _read_json(path or cache_path()).get(cache_key(b))
    if not isinstance(hit, dict):
        return None
    if any(hit.get(f) != b.get(f) for f in BOUND_FIELDS):
        return None
    at = hit.get("proved_at")
    if not isinstance(at, (int, float)) or isinstance(at, bool) or not (0 <= now - at < TTL_S):
        return None
    return hit


def cache_put(b, proved_at, path=None):
    path = path or cache_path()
    data = _read_json(path)
    data[cache_key(b)] = dict(b, proved_at=proved_at)
    _write_json(path, data)


def remedy(transport, program, cause, pinned=False):
    if pinned:
        return ("the owner pin names a program that does not answer this model: upgrade that program, or unset the pin "
                "so the newest proven one is chosen, then prepare again")
    if transport == "claude":
        return "update Claude Code (the desktop app or `claude update`) or choose a model this program knows, then prepare again"
    if transport == "codex":
        return "update the Codex CLI or choose a model it knows, then prepare again"
    return "check the model id in docs/plan/model-registry.json and the bridge's key, then prepare again"


def _substituted(inv, attempt):
    """'' when the answering model is the requested one, else what answered. Claude's JSON result names the models
    that ran in modelUsage; the bridge prints `[usage] ... model=<id>`. A transport that names none: None (no evidence)."""
    raw = attempt.raw or {}
    if inv.transport == "claude":
        doc = MC._claude_doc(raw.get("stdout") or "")
        used = (doc or {}).get("modelUsage")
        if not isinstance(used, dict) or not used:
            return None
        return "" if any(k == inv.model_id or k.startswith(inv.model_id + "-") or k.startswith(inv.model_id + "[")
                         for k in used) else ", ".join(sorted(used))
    if inv.transport == "bridge":
        m = MC.USAGE_RE.search(raw.get("stderr") or "")
        if not m:
            return None
        return "" if m.group(3) == inv.model_id or m.group(3).startswith(inv.model_id) else m.group(3)
    return None


def _proof(status, inv, version, cause="", role="", cached=False, proved_at=None, binding=None, pinned=False):
    return {"status": status, "role": role, "model": inv.name if inv else None, "model_id": inv.model_id if inv else None,
            "transport": inv.transport if inv else None, "program": inv.program if inv else None, "version": version,
            "cause": cause, "remedy": "" if status == "OK" else remedy(inv.transport if inv else "", inv.program if inv else "", cause, pinned),
            "cached": cached, "proved_at": proved_at, "binding": binding, "pinned": pinned}


def prove(name, reg=None, program=None, budget=None, runner=None, role="", now=time.time, version_run=None, pinned=False):
    """One reachability proof of `name` through `program` (None: the program production resolves). Never raises for
    a failed call: the proof says FAIL or NO-DATA with its cause."""
    reg = reg or R.registry()
    budget = budget or Budget()
    m = reg.get(name)
    if not isinstance(m, dict):
        return _proof("NO-DATA", None, None, "model %r is not in the registry" % name, role)
    jev = name == "jev"
    question = JEV_QUESTION if jev else QUESTION
    kinds = m.get("kinds") or {"build"}
    kind = "decide" if jev else ("build" if "build" in kinds else sorted(kinds)[0])
    try:   # THE WIRE GATE FIRST: a transport the run refuses is never spawned, not even for its version
        R.assert_may_send(name, R.PUBLIC, kind, reg)
    except R.Refused as exc:
        return _proof("NO-DATA", None, None, "not probed: %s" % exc, role)
    try:
        inv = MC.build_invocation(name, question, int(PROBE_TIMEOUT_S.get(m.get("transport"), 120)), reg, program=program)
    except (R.Refused, OSError, KeyError) as exc:
        return _proof("NO-DATA", None, None, "the invocation could not be built for %s: %s" % (name, exc), role)
    version = _program_version(inv.transport, inv.program, run=version_run)
    if version is None:
        return _proof("NO-DATA", inv, None, "the version of %s could not be read in %.0f s" % (inv.program, VERSION_TIMEOUT_S),
                      role, pinned=pinned)
    b = _binding(inv, version, m)
    t0 = now()
    try:
        with _Locked(cache_path()):
            hit = cache_get(b, t0)
            # A CACHED PROOF NEVER ANSWERS WHILE THE CONFIGURATION BREAKER IS OPEN (attack R1 F1): a production call
            # tripped it after the cached proof, so the cache is stale evidence. Only a FRESH proof can close it.
            if hit and not BR.config_open(BR.config_key(inv.transport, inv.model_id)):
                return _proof("OK", inv, version, "cached proof from %s" % time.strftime("%H:%M:%S", time.localtime(hit["proved_at"])),
                              role, cached=True, proved_at=hit["proved_at"], binding=b, pinned=pinned)
            why = budget.reserve(inv.transport)
            if why:
                return _proof("NO-DATA", inv, version, why, role, pinned=pinned)
            timeout = min(PROBE_TIMEOUT_S.get(inv.transport, 120.0), budget.remaining_s())
            a = MC.call_one(name, question, kind, R.PUBLIC, timeout=int(max(1, timeout)), reg=reg, runner=runner,
                            program=program, proof=True)
            proved_at = now()
            if a.failure == "CONFIG":
                return _proof("FAIL", inv, version, a.detail[:240], role, pinned=pinned)
            if not a.ok:
                state = "NO-DATA" if ("timed out" in a.detail or "admission refused" in a.detail) else "FAIL"
                return _proof(state, inv, version, a.detail[:240], role, pinned=pinned)
            if jev:
                good = '"ok"' in a.answer or "ok=" in a.answer
            else:
                good = bool(ANSWER_RE.fullmatch(a.answer or ""))
            if not good:
                return _proof("FAIL", inv, version, "answered, but not the proof's answer: %r" % (a.answer or "")[:80], role, pinned=pinned)
            sub = _substituted(inv, a)
            if sub:
                return _proof("FAIL", inv, version, "another model answered: %s" % sub, role, pinned=pinned)
            cache_put(b, proved_at)
    except OSError as exc:
        return _proof("NO-DATA", inv, version, "the proof cache could not be used: %s" % exc, role, pinned=pinned)
    key = BR.config_key(inv.transport, inv.model_id)
    try:
        closed, why = BR.close_config(key, proved_at)
    except ValueError as exc:
        closed, why = False, str(exc)
    held = BR.config_open(key)
    if not closed or held:   # OK only when the breaker is closed now: a proof that could not close it is not a pass
        return _proof("NO-DATA", inv, version, "answered, but the configuration breaker stays open: %s" % (held or why)[:200],
                      role, pinned=pinned)
    return _proof("OK", inv, version, "answered 12 as %s%s" % (inv.model_id, "" if sub == "" else " (the answering model is not named by this transport)"),
                  role, proved_at=proved_at, binding=b, pinned=pinned)


def line(p):
    """One printed line per proof: OK, or NO START with role, model, program, version, cause and remedy."""
    if p["status"] == "OK":
        return "OK       reach role=%s model=%s program=%s version=%s: %s%s" % (
            p["role"] or "-", p["model"], p["program"], p["version"], p["cause"], " (cached)" if p["cached"] else "")
    return "%-8s NO START role=%s model=%s program=%s version=%s cause=%s  HINT: %s" % (
        "REFUSED" if p["status"] == "FAIL" else "NO-DATA", p["role"] or "-", p["model"], p["program"], p["version"] or "unknown",
        p["cause"], p["remedy"])


def require_proof(plan, reg=None, budget=None, runner=None, version_run=None, programs=None, pins=None):
    """[proof] for a role plan [(role, model)], one proof per distinct (model, program), in order. programs maps a
    transport to the program to prove (None: the resolved one); pins names the transports whose program the owner
    pinned. Nothing here waives anything: the caller treats every non OK proof as NOT READY."""
    reg = reg or R.registry()
    budget = budget or Budget()
    programs, pins = programs or {}, pins or set()
    out, seen = [], {}
    for role, name in plan:
        t = (reg.get(name) or {}).get("transport")
        key = (name, programs.get(t))
        if key in seen:
            p = dict(seen[key], role=role, cached=True)
        else:
            p = prove(name, reg, program=programs.get(t), budget=budget, runner=runner, role=role,
                      version_run=version_run, pinned=t in pins)
            seen[key] = p
        out.append(p)
    return out


PIN_OF = {"claude": "BROTHER_CLAUDE_BIN", "codex": "BROTHER_CODEX_BIN"}


def candidates_of(transport, env=None):
    paths = R._paths()
    return paths.claude_candidates(env) if transport == "claude" else paths.codex_candidates(env)


def resolve_program(transport, names, reg=None, budget=None, runner=None, version_run=None, candidates=None, env=None):
    """(selected program or None, [proof per name], [(candidate, version, why rejected)]) for one transport.

    THE OWNER PIN FIRST, AND ONLY IT: a pinned program is the one proved; if it fails, the answer is None and NO START
    names the pin. It is never overridden by a program that happens to work. Without a pin: every candidate
    (brother_paths.claude_candidates or codex_candidates) says its version; the readable ones are tried NEWEST FIRST,
    semantically, and the first that answers every model is selected. A candidate whose version cannot be read is
    rejected as NO-DATA, never selected on its name or its place."""
    e = os.environ if env is None else env
    reg = reg or R.registry()
    budget = budget or Budget()
    pin = e.get(PIN_OF.get(transport, ""), "") if transport in PIN_OF else ""
    if pin:
        proofs = [prove(n, reg, program=pin, budget=budget, runner=runner, version_run=version_run, pinned=True) for n in names]
        if all(p["status"] == "OK" for p in proofs):
            return pin, proofs, []
        bad = next(p for p in proofs if p["status"] != "OK")
        return None, proofs, [(pin, bad.get("version"), "the owner pin %s=%s: %s" % (PIN_OF[transport], pin, bad["cause"]))]
    if candidates is None:
        try:
            candidates = candidates_of(transport, e)
        except (R.Refused, ImportError, OSError) as exc:
            return None, [_proof("NO-DATA", None, None, "the %s candidates could not be listed: %s" % (transport, exc))
                          for _ in names], []
    versions = [(c, version_of(c, run=version_run)) for c in candidates]
    rejected = [(c, None, "its version could not be read (NO-DATA)") for c, v in versions if not v]
    readable = sorted([cv for cv in versions if cv[1]], key=lambda cv: version_key(cv[1]), reverse=True)
    last = None
    for c, v in readable:
        proofs = [prove(n, reg, program=c, budget=budget, runner=runner, version_run=version_run) for n in names]
        if all(p["status"] == "OK" for p in proofs):
            for p in proofs:
                p["selected_by"] = "newest proven"
            return c, proofs, rejected
        bad = next(p for p in proofs if p["status"] != "OK")
        rejected.append((c, v, bad["cause"]))
        last = proofs
        if "allowance is spent" in bad["cause"] or "proof time is spent" in bad["cause"]:
            break   # every later candidate would be NO-DATA too; say so once
    if last is None:
        last = [_proof("NO-DATA", None, None, "no installed %s program could say its version (%d candidate(s))" % (transport, len(candidates)))
                for _ in names]
    return None, last, rejected


def resolve_and_prove(plan, reg=None, budget=None, runner=None, version_run=None, candidates=None, env=None):
    """[proof per (role, model) in plan]: Claude and Codex models through the resolved program of their transport
    (resolve_program), bridge models directly. Each Claude or Codex proof carries its transport's rejected candidates."""
    reg = reg or R.registry()
    budget = budget or Budget()
    by_t = {}
    for _role, name in plan:
        t = (reg.get(name) or {}).get("transport")
        if t in PIN_OF and name not in by_t.setdefault(t, []):
            by_t[t].append(name)
    resolved = {}
    for t, names in by_t.items():
        sel, proofs, rejected = resolve_program(t, names, reg, budget, runner, version_run,
                                                (candidates or {}).get(t) if candidates else None, env)
        for n, p in zip(names, proofs):
            resolved[n] = dict(p, rejected=[list(r) for r in rejected], selected=sel)
    out, bridge = [], {}
    for role, name in plan:
        if name in resolved:
            out.append(dict(resolved[name], role=role))
        else:
            if name not in bridge:
                bridge[name] = prove(name, reg, budget=budget, runner=runner, role=role, version_run=version_run)
            out.append(dict(bridge[name], role=role))
    return out


def write_record(selected, path=None, proven=None):
    """Write the resolved programs {transport: {path, version, fingerprint, selected_by}} ONLY when they differ from the
    record on disk, so the file's mtime is the time the resolved program last changed. Returns True when written."""
    path = path or record_path()
    old = _read_json(path)
    ident = lambda progs: {t: {k: (r or {}).get(k) for k in ("path", "version", "fingerprint")}
                           for t, r in (progs or {}).items()} if isinstance(progs, dict) else progs
    proven = sorted(set(proven or []))
    if ident(old.get("programs")) == ident(selected) and sorted(old.get("proven") or []) == proven:
        return False   # the same programs and the same proof set: the mtime, a fact for runner_pool, must not move
    _write_json(path, {"programs": selected, "proven": proven, "changed_at": time.time(), "note": "written by model_reachability.py; "
                       "its mtime is a fact for runner_pool: a parked sub unit is re-seated when the resolved program changes"})
    return True


def proven_models(env=None):
    """None when no proof regime applies (no BROTHER_PROGRAM_RECORD in the environment); else the set of
    'transport|model id' the intake proved. A record that is named but unreadable proves nothing: an empty set."""
    e = os.environ if env is None else env
    if not e.get("BROTHER_PROGRAM_RECORD"):
        return None
    rec = _read_json(e["BROTHER_PROGRAM_RECORD"])
    return {"|".join(k.split("|")[i] for i in (0, 3)) for k in (rec.get("proven") or []) if isinstance(k, str) and k.count("|") == 3}


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if a[:1] == ["--selftest"]:
        return _selftest()
    if a[:1] != ["prove"] or len(a) < 2:
        print(__doc__.split("usage:")[1].strip())
        return 2
    proofs = require_proof([("cli", n) for n in a[1:]])
    for p in proofs:
        print(line(p))
    return 0 if all(p["status"] == "OK" for p in proofs) else 1


def _selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="reach-selftest-")
    saved = {k: os.environ.get(k) for k in ("BROTHER_PROOF_CACHE", "BROTHER_OR_STATE_ROOT", "BROTHER_CLAUDE_CALLS_LEDGER")}
    os.environ.update(BROTHER_PROOF_CACHE=os.path.join(d, "proofs.json"), BROTHER_OR_STATE_ROOT=os.path.join(d, "state"),
                      BROTHER_CLAUDE_CALLS_LEDGER=os.path.join(d, "calls.jsonl"))
    try:
        b = Budget(usd=0.15)
        cases = [("semantic order puts 2.1.284 above 2.1.99", version_key("2.1.284") > version_key("2.1.99")),
                 ("a budget of 0.15 takes one claude probe and refuses the second", b.reserve("claude") == "" and b.reserve("claude") != ""),
                 ("an unknown transport has no bound and is refused", Budget(usd=5).reserve("pigeon") != ""),
                 ("an unreadable version is None", version_of("/nonexistent/program") is None)]
    finally:
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
