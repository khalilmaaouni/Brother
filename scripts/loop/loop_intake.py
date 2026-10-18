#!/usr/bin/env python3
"""The intake of a run: everything a run needs is checked and written down BEFORE anything is spent.

WHY (owner, 2026-09-22): "We need an intake process for the loop preparation", and "It should give hints or guidance
to the user otherwise they wont know in advance the first time". The night before, a run was started from a hand
edited env file: a new judge was switched on as a gate with no calibration, the money cap and the deadline disagreed
between two files, no end to end proof was run, and the first sign of trouble reached a human five hours later.

usage (repo root):
  loop_intake.py guide                      plain words: each role, what it does, WHICH MODELS QUALIFY RIGHT NOW, an example
  loop_intake.py prepare finisher=<model> documenter=<model> [role=model ...] --deadline HH:MM --budget-usd N [--scope REGEX] [--plan docs/plan/X.json] [--words "<his answer verbatim>"] [--pair]
  (--pair: ONE record for the proof pair RB then RC; the deadline is the pair's end, it must cover both runs, the
   budget is per run and the grant carries two of them)
  (every value may be his words: "deepseek at xhigh", "6pm", "80USD"; the tool prints a READ line for each reading it made)
                         [--accept key,key --accepted-by "who, in their words"]     continue past ADVISABLE conditions, on the record
  loop_intake.py status                     the current intake record, its age, and whether a driver may start on it
  loop_intake.py status --running           a running driver's budget and deadline, read every pass; the start rules never gate it
  loop_intake.py --selftest
prepare runs every check, prints one line each (OK, REFUSED with a HINT, or NO-DATA), writes the record to
~/.claude/evidence/loop-intake/CURRENT.json and the launch settings beside it, and exits 0 only when the verdict is
READY. TWO CLASSES OF CONDITION (owner, 2026-09-22: "check all conditions are met before starting the loop ... or
alert the user and ask if he wants to continue if they are not all met, some can be optional"). REQUIRED conditions
can never be waived: a run that ignores one repeats 2026-09-22 or breaks the machine. ADVISABLE conditions alert the
user with what is risked and the exact words to continue; a waiver must name who accepted it and is kept in the
record. A canary that RAN AND FAILED is required; a canary that does not exist yet is advisable. READY means: roles settled, deadline in the future, budget within the money headroom, nothing of the loop
alive, lease free, executed tools equal the versioned ones. The owner's HOLD is reported on its own line and makes the
verdict HELD: an intake can be prepared under a hold, a driver cannot start under one. NO-DATA on any check is never
READY. The plan lint is one LINT line (FX-13.5): in report mode, the default, it is information and never changes the
verdict; with BROTHER_RUNFLOW_LINT=block a lint that is not clean is ADVISABLE, waived with --accept lint.
Roots are overridable for tests: INTAKE_EVIDENCE, and every outside check is injected through `probes`.
"""
import datetime, json, os, re, shlex, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # THIS file's own directory: the copies installed beside it
import loop_roles  # noqa: E402
import model_router  # noqa: E402  (the registry is the one source of the spoken model names, FX-31.6)
import proof_ledger  # noqa: E402  the strict parser: a repeated budget member refuses, never a larger budget
MAX_AGE_H = 6.0            # a record older than this no longer describes the machine it was checked on
# THE PAIR RECORD (U1, objection 6): one record covers RB and RC, and what it must cover, not its age, decides whether a
# run may start on it. RB needs its own window and RC's, each with its 180 s launch allowance, plus 900 s of slack; RC
# needs its window, the allowance and 300 s. The window is always the production 8 h here: a rehearsal's short window
# (proof_pair.sh --rehearsal) never lowers what a pair record must cover.
PAIR_WINDOW_S, PAIR_LAUNCH_S = 28800, 180
PAIR_COVER_S = {"RB": 2 * (PAIR_WINDOW_S + PAIR_LAUNCH_S) + 900, "RC": PAIR_WINDOW_S + PAIR_LAUNCH_S + 300}


def ev():
    return os.environ.get("INTAKE_EVIDENCE") or os.path.expanduser("~/.claude/evidence")


# THE GATE CACHE (LA4.a, 2026-10-12). A relaunch on unchanged tools re-ran the parity test, the canary and the plan lint
# (1:29 at 14:37:49). A deterministic gate now stores ONLY an OK, keyed by a sha256 over the bytes it reads; a hit
# answers "<detail> (cached, key xxxxxxxx)" without running it. Every doubt runs the gate: a changed byte, a corrupt,
# future dated or 24 h old cache, a key that cannot be computed. A FAIL, a NO-DATA or a FINDINGS is never stored. The
# proofs, the tree, the hold and the live checks are never cached. A hit never refreshes `at`, so the real gate runs at
# least daily (the bound on whatever ambient state a key does not name).
GATE_CACHE_SCHEMA = "loop-gate-cache-v1"
GATE_CACHE_MAX_AGE_S = 86400
_SKIP_DIRS = ("__pycache__", ".git")


def gate_cache_path(env=None):
    env = os.environ if env is None else env
    return os.path.join(env.get("INTAKE_EVIDENCE") or os.path.expanduser("~/.claude/evidence"), "loop-intake", "gate-cache.json")


def _sha_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _raise(exc):
    raise exc


def _file_pairs(root, recursive=True, suffixes=None):
    """[(relative path, sha256)] of the regular files under root (a symlink is followed), sorted; None when the root or
    any file cannot be read. __pycache__ and .git are skipped."""
    out = []
    try:
        if recursive:
            walked = []
            for d, dirs, files in os.walk(root, onerror=_raise):
                dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
                walked += [os.path.join(d, f) for f in files if f not in _SKIP_DIRS]
        else:
            walked = [os.path.join(root, f) for f in os.listdir(root)]
        for p in walked:
            if suffixes and not p.endswith(suffixes):
                continue
            if os.path.isfile(p):
                out.append((os.path.relpath(p, root), _sha_file(p)))
    except OSError:  # sbe: allow-silent a key that cannot be computed is None, and None means the real gate runs (LA4: any doubt runs the gate)
        return None
    return sorted(out)


def _digest(parts):
    import hashlib
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


def tree_digest(root, names_only_top=False):
    """sha256 over the sorted (relative path, file sha256) of root (only its top level files with names_only_top);
    None when any file cannot be read."""
    pairs = _file_pairs(root, recursive=not names_only_top)
    return None if pairs is None else _digest(pairs)


def stat_signature(path):
    """"missing" for a missing file, "<size>:<mtime_ns>" otherwise, None for any other error (an outside input too big
    or too live to hash, the ledger, is keyed by its signature)."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError, TypeError):
        return None
    return "%d:%d" % (st.st_size, st.st_mtime_ns)


def parity_key(repo, bin_dir, python_version=None):
    """The parity test's inputs: scripts/loop (recursive), the top level scripts/*.py and *.sh, every file directly in
    the bin directory (the deploy stamp included) and the interpreter. None when any of them cannot be read."""
    parts = [_file_pairs(os.path.join(repo, "scripts", "loop")),
             _file_pairs(os.path.join(repo, "scripts"), recursive=False, suffixes=(".py", ".sh")),
             _file_pairs(bin_dir, recursive=False)]
    if any(p is None for p in parts):
        return None
    return _digest(["parity", parts, python_version or sys.version, sys.executable])


def lint_key(repo, plan_rel, scope, mode, ledger=None, roles=None, council=None, council_dir=None):
    """The plan lint's inputs. Beyond the spec's list (plan, scope, mode, scripts/loop, docs/plan/specs, roles, the
    ledger and council signatures) the WHOLE working tree is keyed by content: the lint asks whether paths named by
    the plan exist (NEW-EXISTS, TEST-FOUND) and reads call site files anywhere, so a narrower key could serve a wrong
    OK. ponytail: about a second over a 150 MB tree on a miss; name the read paths if that ever matters."""
    import plan_lint
    ledger = ledger or os.path.expanduser(plan_lint.LEDGER_PATH)
    council = council or os.path.expanduser(plan_lint.COUNCIL_PATH)
    council_dir = council_dir or os.path.expanduser(plan_lint.COUNCIL_DIR)
    roles = roles or os.path.join(repo, "docs", "plan", "loop-roles.json")
    plan = plan_rel if os.path.isabs(plan_rel) else os.path.join(repo, plan_rel)
    try:
        files = [_sha_file(plan), _sha_file(roles) if os.path.exists(roles) else "missing"]
    except OSError:  # sbe: allow-silent an unreadable plan or roles file gives no key, so the plan lint runs for real; nothing is skipped
        return None
    tree = _file_pairs(repo)
    council_tree = "missing" if not os.path.exists(council_dir) else _file_pairs(council_dir)
    sigs = [stat_signature(ledger), stat_signature(council)]
    if tree is None or council_tree is None or None in sigs:
        return None
    return _digest(["lint", files, scope, mode, tree, council_tree, sigs, sys.version])


def _read_cache(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != GATE_CACHE_SCHEMA or not isinstance(doc.get("gates"), dict):
        return None
    return doc


def gate_cache_get(gate, key, now=None, path=None):
    """The stored detail on an exact, fresh hit; None on anything else (a damaged file included)."""
    doc = _read_cache(path or gate_cache_path())
    row = doc["gates"].get(gate) if doc else None
    if not isinstance(row, dict) or not isinstance(key, str) or row.get("key") != key or not isinstance(row.get("detail"), str):
        return None
    at, now = row.get("at"), time.time() if now is None else now
    if isinstance(at, bool) or not isinstance(at, (int, float)) or at > now or now - at > GATE_CACHE_MAX_AGE_S:
        return None
    return row["detail"]


def gate_cache_put(gate, key, detail, now=None, path=None):
    """"" when stored, else why not. Read modify write under an fcntl lock (the probes run in threads, LA2.a), written
    through a temp file and os.replace, so a reader never sees half a document."""
    import fcntl
    path = path or gate_cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".lock", "a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            doc = _read_cache(path) or {"schema": GATE_CACHE_SCHEMA, "gates": {}}
            doc["gates"][gate] = {"key": key, "detail": detail, "at": time.time() if now is None else now}
            fd, tmp = tempfile.mkstemp(prefix=".gate-cache-", dir=os.path.dirname(path))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh, sort_keys=True)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
    except OSError as exc:
        return "the gate cache %s cannot be written (%s)" % (path, type(exc).__name__)
    return ""


def cached_gate(gate, key, run, path=None):
    """(state, detail): a fresh exact hit without calling run; otherwise run(), stored only when it answered OK.
    A None key runs the gate and stores nothing."""
    if key is None:
        return run()
    hit = gate_cache_get(gate, key, path=path)
    if hit is not None:
        return "OK", "%s (cached, key %s)" % (hit, key[:8])
    state, detail = run()
    if state == "OK":
        gate_cache_put(gate, key, detail, path=path)   # a store that fails costs a later rerun, never the verdict
    return state, detail


def _key_or_none(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:   # sbe: allow-silent an uncomputable key runs the real gate, which is the safe direction
        return None


def qualifying(role, spec, reg):
    """Model names that loop_roles would NOT refuse for this role, BEST FIRST by the registry's own quality figure for
    the role's kind of work, so the first name is a sensible suggestion and not an accident of file order."""
    ok = [n for n in reg if loop_roles.judge(role, spec, n, reg)[0] != "REFUSED"]
    return sorted(ok, key=lambda n: (-(reg[n].get("quality") or {}).get(spec["kind"], 0), n))


def guide(roles, reg):
    out = ["HOW TO PREPARE A RUN. Six roles. Two MUST be chosen by you (finisher, documenter); the rest have defaults.", ""]
    for role, s in roles.items():
        ok = qualifying(role, s, reg)
        out += ["%s  (%s)" % (role.upper(), s["when"].replace("_", " ")), "  what it does: " + s["does"],
                "  models that qualify right now: " + (", ".join(ok) or "NONE, which would block a run"),
                "  default: %s%s" % (s.get("default") or "none, YOU choose", "" if not s.get("limits") else "   | limit: " + s["limits"]), ""]
    must = [r for r, s in roles.items() if s.get("must_be_chosen")]
    out += ["EXAMPLE:  loop_intake.py prepare %s --deadline 18:00 --budget-usd 10" % " ".join("%s=%s" % (r, (qualifying(r, roles[r], reg) or ["<model>"])[0]) for r in must),
            "Nothing starts from an intake. It only writes the record; the driver starts on the owner's order and refuses without a fresh READY record."]
    return out


def stub_probes():
    """For the selftest's entry point runs ONLY (INTAKE_PROBES=stub): every fact is NO-DATA, so a record made through
    this stub can never read READY and no driver can ever start on it. It exists because the real probes run the end
    to end canary, about a minute each, and the entry point cases need argument parsing, not the machine."""
    nd = lambda what: (lambda: ("NO-DATA", "%s not probed: INTAKE_PROBES=stub" % what))
    return {"alive": nd("alive"), "lease": nd("lease"), "parity": nd("parity"), "switch": nd("switch"), "canary": nd("canary"), "tree": nd("tree"), "digest": nd("digest"), "done": nd("done"), "salvage": nd("salvage"), "pool": nd("pool"), "lint": nd("lint"),
            "reach": lambda plan: None,
            "headroom": lambda: None, "hold": lambda: False, "now": datetime.datetime.now}


def parse_headroom(text):
    """The burn guard's headroom figure, SIGNED, or None when no figure is printed. A spent out day prints a negative
    headroom ("headroom -148.16" after a grant expired, 2026-09-23 14:1x); the old [0-9.]+ pattern could not read the sign,
    so a real number read as NO-DATA and refused every intake, although the figure is only information here."""
    m = re.search(r"headroom (-?[0-9]+(?:\.[0-9]+)?)", text or "")
    return float(m.group(1)) if m else None


def pass_tool(name, args, ok_codes, read=None):
    """A probe that runs one pass tool's read only verb from beside this file: OK on a documented exit code with no
    traceback, REFUSED otherwise with the tool's last line, NO-DATA when the tool is missing or cannot run.
    read: when given, the finished run is answered by read(r) instead (the plan lint, FX-13.5); missing and could
    not run stay NO-DATA."""
    def probe():
        tool = os.path.join(HERE, name)
        if not os.path.isfile(tool): return "NO-DATA", "%s is not beside this copy" % name
        try:
            r = subprocess.run([sys.executable, "-B", tool] + list(args), capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError):
            return "NO-DATA", "%s could not run" % name
        if read is not None:
            return read(r)
        if r.returncode not in ok_codes or "Traceback" in r.stderr:
            return "REFUSED", "%s %s exits %d: %s" % (name, " ".join(args), r.returncode, ((r.stderr.strip() or r.stdout.strip()).splitlines() or ["no output"])[-1][:140])
        return "OK", "%s %s answers (exit %d, %d lines)" % (name, " ".join(args), r.returncode, len(r.stdout.splitlines()))
    return probe


import plan_path as _PP  # noqa: E402  the one plan selector (BROTHER_WBS or the default; an invalid value refuses)
LINT_PLAN = _PP.selected()   # the plan the pool reads, relative to the launch tree the pass tools run in; prepare --plan moves it
LINT_SUMMARY = re.compile(r"LINT SUMMARY blocking ([0-9]+) advisory ([0-9]+) no-data ([0-9]+) \|")


def plan_unit_ids(path):
    """([unit ids], {sub unit ids}) of the plan file at `path`. ValueError naming the reason when the file cannot be read,
    is not JSON, has no non empty `units` list, or holds a unit without a text id. Never a guess, never an empty default."""
    try:
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
    except OSError as exc:
        raise ValueError("%s cannot be read (%s)" % (path, type(exc).__name__))
    except ValueError as exc:
        raise ValueError("%s is not JSON (%s)" % (path, str(exc)[:80]))
    units = plan.get("units") if isinstance(plan, dict) else None
    if not isinstance(units, list) or not units:
        raise ValueError("%s has no non empty units list" % path)
    ids = [u.get("id") for u in units if isinstance(u, dict) and isinstance(u.get("id"), str) and u.get("id")]
    if len(ids) != len(units):
        raise ValueError("%s holds a unit without a text id" % path)
    subs = {s for u in units for s in (u.get("sub_units") or []) if isinstance(s, str)}
    return ids, subs


def plan_collisions(selected_ids, default_ids, default_subs, scope):
    """THE COLLISION RULE (2026-10-10). The ids the scope admits among `selected_ids` (plan_lint's own test, re.search on
    the unit id; no scope admits every id) that the default plan ALSO holds, as a unit id or as the prefix of one of its
    sub unit ids (M1 and U8 live in both plans today). The spec scores, the council, the unit ledger and the run folders
    key on these ids, so a second plan reusing one would read and write the first plan's records. Sorted, possibly []."""
    try:
        rx = re.compile(scope) if scope else None
    except re.error:   # sbe: allow-silent a scope that is not a regular expression is refused by its own check; here it admits every id, the wider refusal
        rx = None
    admitted = [i for i in selected_ids if rx is None or rx.search(i)]
    default_ids = set(default_ids)
    return sorted(set(i for i in admitted if i in default_ids or any(s.startswith(i + ".") for s in default_subs)))


def lint_answer(code, out, err):
    """(state, detail) from one finished `plan_lint.py --report` run (FX-13.5): OK on exit 0 with blocking 0, FINDINGS on
    exit 0 with blocking above 0, both with the LINT SUMMARY line; NO-DATA with the last line otherwise (exit 3, a
    traceback, no summary, a summary whose no-data count contradicts its exit 0, an answer that is not text)."""
    if isinstance(code, bool) or not isinstance(code, int) or not isinstance(out, str) or not isinstance(err, str):
        return "NO-DATA", "plan_lint.py answered in a shape this intake cannot read"
    said = (err if "Traceback" in err else out).strip().splitlines() or err.strip().splitlines()
    last = said[-1].strip()[:400] if said else "plan_lint.py printed nothing (exit %d)" % code
    m = LINT_SUMMARY.match(last)
    if code != 0 or "Traceback" in err or not m or int(m.group(3)) != 0:
        return "NO-DATA", last
    return ("FINDINGS" if int(m.group(1)) > 0 else "OK"), last


def deployed_switch(env, bin_dir, jev_path=None):
    """(state, detail): with BROTHER_TRANSPORTS set, the DEPLOYED model router (loop_pass.sh runs the copies in bin_dir,
    never the repository's) must carry the transport allowlist, else the switch is silently ignored and a Claude only
    run would still spawn the bridge (attack finding, 2026-09-30). Unset is OK: nothing was asked. Unreadable refuses."""
    import model_router
    try:
        allowed = model_router.transports_allowed(env)
    except model_router.Refused as exc:
        return "REFUSED", "BROTHER_TRANSPORTS is not a transport allowlist: %s" % exc
    if allowed is None:
        return "OK", "no transport allowlist requested (BROTHER_TRANSPORTS unset)"
    raw = model_router.transports_text(allowed)
    path = os.path.join(bin_dir, "model_router.py")
    try:
        with open(path, encoding="utf-8") as fh: src = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return "REFUSED", "BROTHER_TRANSPORTS=%s is set but the deployed model router %s cannot be read (%s): deploy first" % (raw, path, type(exc).__name__)
    if "def transports_allowed" not in src:
        return "REFUSED", "BROTHER_TRANSPORTS=%s is set but the deployed model router %s has no transport allowlist: deploy first, or every pass ignores the switch" % (raw, path)
    jev = os.path.join(os.path.dirname(HERE), "jev_decide.py") if jev_path is None else jev_path   # run from the tree, not the bin
    try:
        with open(jev, encoding="utf-8") as fh: jsrc = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return "REFUSED", "BROTHER_TRANSPORTS=%s is set but the tree's Jev entry %s cannot be read (%s)" % (raw, jev, type(exc).__name__)
    if "def bridge_refused" not in jsrc:
        return "REFUSED", "BROTHER_TRANSPORTS=%s is set but the tree's Jev entry %s has no bridge gate: every Jev call would still leave the machine" % (raw, jev)
    return "OK", "BROTHER_TRANSPORTS=%s: the deployed model router %s and the tree's Jev entry carry the allowlist" % (raw, path)


def launcher_alive(env, now=None):
    """LA3.b: the launcher's own live check, when it read nothing alive (exit exactly 0) at most LIVE_FRESH_S ago, as
    the alive probe's answer; None in every other case, and the probe runs stop_loop.sh --dry as before."""
    if env.get("START_RUN_LIVE_RC") != "0":
        return None
    age = _age(env.get("START_RUN_LIVE_AT", ""), now)
    if age is None or age > LIVE_FRESH_S:
        return None
    return "OK", "nothing of the loop is alive (the launcher checked %d s ago)" % int(age)


def real_probes():
    """The outside facts, each as (state, detail) with state OK, REFUSED or NO-DATA. Injected whole in tests."""
    if os.environ.get("INTAKE_PROBES") == "stub":
        return stub_probes()
    def run(cmd, timeout=120):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            return None
    def alive():
        said = launcher_alive(os.environ)
        if said is not None:
            return said
        r = run(["bash", os.path.join(HERE, "stop_loop.sh"), "--dry"])
        return ("NO-DATA", "stop_loop.sh --dry could not run") if r is None else ("OK", "nothing of the loop is alive") if r.returncode == 0 else ("REFUSED", (r.stdout.strip().splitlines() or ["something is alive"])[0][:120])
    def lease():
        r = run(["bash", os.path.expanduser("~/.claude/bin/loop_guard.sh"), "check"])
        return ("NO-DATA", "loop_guard.sh could not run") if r is None else ("OK", "lease free") if r.returncode == 0 else ("REFUSED", (r.stdout.strip() or "lease held")[:120])
    def parity():
        if not os.path.isfile(os.path.join(os.path.dirname(HERE), "test_loop_tool_parity.py")):
            return "NO-DATA", "the parity test is not beside this copy: run prepare from the repository, scripts/loop/loop_intake.py"
        def go():
            r = run([sys.executable, "-B", os.path.join(os.path.dirname(HERE), "test_loop_tool_parity.py")], 300)
            return ("NO-DATA", "the parity test could not run") if r is None else ("OK", "executed tools equal the versioned ones") if r.returncode == 0 else ("REFUSED", "the executed tools differ from the repository: deploy first")
        bin_dir = os.path.abspath(os.path.expanduser(os.environ.get("BROTHER_DEPLOY_TARGET", "~/.claude/bin")))   # the parity test's own BIN
        return cached_gate("parity", _key_or_none(parity_key, os.path.dirname(os.path.dirname(HERE)), bin_dir), go)   # the repository root
    def headroom():
        r = run([sys.executable, os.path.join(HERE, "burn_guard.py"), "--stop-hour", "10"])
        return parse_headroom(r.stdout) if r is not None else None
    # THE CANARY IS NOT BUILT YET, and that is said as NO-DATA rather than skipped: an intake that cannot prove the
    # whole path end to end must never read READY. When scripts/loop/loop_canary.py exists this probe runs it.
    def canary():
        tool = os.path.join(HERE, "loop_canary.py")
        if not os.path.isfile(tool):
            return "NO-DATA", "no end to end canary exists yet, so nothing proves the whole path before a start"
        def go():
            r = run([sys.executable, "-B", tool], 1800)
            return ("NO-DATA", "the canary could not run") if r is None else ("OK", "a known good build went end to end") if r.returncode == 0 else ("REFUSED", (r.stdout.strip().splitlines() or ["the canary failed"])[-1][:140])
        def key():
            import loop_canary as LC
            spec = LC.load_spec()
            return LC.canary_key(spec, os.path.dirname(os.path.dirname(HERE))) if spec is not None else None
        return cached_gate("canary", _key_or_none(key), go)
    # THE TREE THE DRIVER WILL RUN IN, checked with the SAME predicate loop_pass.sh applies on its first pass (a
    # dirty tree or unpushed commits exit 44 BLOCKED and ring the alarm). Measured 2026-09-22 13:16: an intake read
    # READY, two uncommitted files were written into the launch worktree after it, and the driver refused at pass 1.
    # An intake that does not look at the tree certifies a run the first pass will refuse.
    def tree():
        sha = os.environ.get("START_RUN_UPSTREAM_SHA", "")
        at = os.environ.get("START_RUN_FETCHED_AT", "")
        return tree_state(os.path.dirname(os.path.dirname(HERE)), fetched_sha=sha if _SHA40.fullmatch(sha) else None,
                          fetched_at=at if _EPOCH.fullmatch(at) else None)
    # THE PASS DIGEST IS RUN FOR REAL. It is the first tool every pass needs; on 2026-09-22 14:10 it crashed on a
    # formatting error and the driver was BLOCKED at pass 1 after an intake that had read READY.
    # Every tool the pass calls has a read only verb; each is run here, from the launch worktree, and must answer with
    # one of its documented exit codes and no traceback. The digest was the one that crashed; the other three would
    # have been found the same way, one pass later.
    digest = pass_tool("pass_digest.py", ["--no-fetch"], (0,))
    done = pass_tool("loop_done.py", ["--scope", "."], (0, 1))
    salvage = pass_tool("salvage.py", ["list"], (0,))
    pool = pass_tool("runner_pool.py", ["--dry"], (0,))
    # THE PLAN LINT (FX-13.5), its CLI in a child process so a lint crash is a NO-DATA line, never an intake traceback.
    # The scope is the pool's own: BROTHER_SCOPE reaches the child through the environment it inherits (plan_lint reads
    # it when no --scope is given), never as a computed argument; unset, the whole plan (a default never narrows the work).
    # LA4.a: cached by its inputs, read where the lint reads them (root "." is the launch cwd); only an OK is stored.
    lint_run = pass_tool("plan_lint.py", ["--report", LINT_PLAN], (0,), read=lambda r: lint_answer(r.returncode, r.stdout, r.stderr))
    def lint_key_now():
        import plan_lint
        repo = os.path.dirname(os.path.dirname(HERE))
        if not os.path.samefile(os.getcwd(), repo):   # samefile, not realpath: this disk ignores case
            return None   # the lint reads ".", which is not the tree this copy keys: run it
        return lint_key(repo, LINT_PLAN, os.environ.get("BROTHER_SCOPE") or ".", plan_lint.lint_mode(os.environ)[0], roles=plan_lint._roles_path())
    lint = lambda: cached_gate("lint", _key_or_none(lint_key_now), lint_run)
    switch = lambda: deployed_switch(os.environ, os.path.abspath(os.path.expanduser(os.environ.get("BROTHER_DEPLOY_TARGET", "~/.claude/bin"))))
    # REACHABILITY (2026-09-30): one real minimal call per model the run will call, through the program it will use.
    def reach(plan):
        import model_reachability as MR
        prev = read_current()
        epoch = prev.get("epoch") if isinstance(prev, dict) else None
        since = float(epoch) if isinstance(epoch, (int, float)) and not isinstance(epoch, bool) else 0.0
        fresh = fresh_transports(prev, breaker_events(since))   # SEAT1.c: a seat that failed last time is proved live
        return seat_gate(MR.resolve_and_prove(plan, fresh=fresh))   # the newest proven program per transport, the owner pin first; then a seat
    return {"alive": alive, "lease": lease, "parity": parity, "switch": switch, "headroom": headroom, "canary": canary, "tree": tree, "digest": digest, "done": done, "salvage": salvage, "pool": pool, "lint": lint,
            "reach": reach, "hold": lambda: os.path.exists(os.path.join(ev(), "LOOP-HOLD.txt")), "now": datetime.datetime.now}


#: The transports whose cached proofs a relaunch may refuse (SEAT1.c). Claude is out of scope: its seat is probed live
#: every intake (seat_gate) and has its own breaker, and a forced Claude proof costs a paid call each launch.
NON_CLAUDE = ("codex", "bridge")
_TRIP_EVENTS = ("trip", "reopen", "config_trip")


def fresh_transports(prev, events):
    """The transports whose cached proofs are NOT trusted at this launch. Every doubt widens the set:
    1. no previous record (missing, unreadable, corrupt, not a mapping): every NON_CLAUDE transport;
    2. a previous record that is not READY: the transports in its seat_failures, or all of them when that key is
       missing or not a list (a 1.1.2 record, or a corrupt one);
    3. an event log that cannot be read (events is None): all of them;
    4. a trip, reopen or config_trip event newer than the previous record: that event's transport."""
    every = set(NON_CLAUDE)
    if not isinstance(prev, dict):
        return every
    out = set()
    if prev.get("verdict") != "READY":
        failed = prev.get("seat_failures")
        if not isinstance(failed, list):
            return every
        out |= {t for t in failed if t in NON_CLAUDE}
    if events is None:
        return every
    epoch = prev.get("epoch")
    since = float(epoch) if isinstance(epoch, (int, float)) and not isinstance(epoch, bool) else 0.0
    for row in events:
        if not isinstance(row, dict) or row.get("event") not in _TRIP_EVENTS:
            continue
        t, key = row.get("t"), row.get("key")
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t <= since:
            continue   # older than the previous record; a trip with no readable time counts
        if not isinstance(key, str):
            return every   # a trip on an unknown key: which seat failed cannot be said
        parts = key.split(":")
        transport = (parts[1] if len(parts) > 1 else "") if parts[0] == "config" else parts[0]
        if transport in NON_CLAUDE:
            out.add(transport)
    return out


def breaker_events(since, path=None):
    """The breaker event rows newer than `since`; [] when the log does not exist (a fresh machine), None when it cannot
    be read or a row cannot be parsed (an unknown never reads as "no trip")."""
    if path is None:
        try:
            import breaker as BR
            path = os.path.join(BR.state_root(), "breaker-events.jsonl")
        except (ImportError, ValueError, OSError):
            return None
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    rows = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError:  # sbe: allow-silent a corrupt cache row makes the whole cache unreadable, which serves nothing and runs every gate
            return None
        if not isinstance(row, dict):
            return None
        t = row.get("t")
        if not isinstance(t, (int, float)) or isinstance(t, bool) or t > since:
            rows.append(row)   # a row with no readable time is kept: the reader decides, widening
    return rows


def seat_gate(proofs, probe=None, native=None, codex_probe=None):
    """THE SEAT IS PROVEN, NOT ONLY THE PROGRAM (owner ruling A, 2026-10-05). The reach proof runs the program OUTSIDE
    the native sandbox, so it read OK while every seat of proof pair RB answered 401 (a seat may read the login but not
    refresh it) and the run ended UNPRODUCTIVE in 4.5 minutes. When native seats will run and a Claude proof reads OK,
    one real minimal call goes through a seat (native_worker.seat_probe, the seat's own command line, the proven
    program); a seat that does not answer turns every OK Claude proof into FAIL with no binding, so the run never
    starts, and the HINT names the refresh (brother-login first on the loop token lane, whose login the seat ran on).
    BOTH HOSTS (1.1.6 S2): the Codex proofs then go through codex_seat. probe(program=), native and codex_probe stand
    in for tests. Returns the proofs."""
    return codex_seat(_claude_seat(proofs, probe, native), codex_probe)


def _claude_seat(proofs, probe, native):
    """seat_gate's Claude half, unchanged since 2026-10-05 except the seat field and the token lane's remedy."""
    claude = [i for i, p in enumerate(proofs or []) if isinstance(p, dict) and p.get("transport") == "claude" and p.get("status") == "OK"]
    if not claude:
        return proofs
    import loop_switches as SW
    import native_worker as NW
    if not (SW.native_on() if native is None else native):
        return proofs   # no seat will run: the plain proof is the whole truth
    state, why = (probe or NW.seat_probe)(program=proofs[claude[0]]["program"])
    remedy = NW.refresh_hint()
    if SW.on("BROTHER_LOOP_TOKEN"):
        remedy = "run brother-login (the seats run on the login it saved), then prepare again; else " + remedy
    out = list(proofs)
    for i in claude:
        out[i] = (dict(out[i], cause="%s; a sandboxed native seat answered" % out[i].get("cause", "")) if state == "OK" else
                  dict(out[i], status="FAIL", binding=None, cached=False, remedy=remedy, seat="claude",
                       cause="the plain call answered but a sandboxed native seat did not (%s)" % why))
    return out


def codex_seat(proofs, probe=None):
    """A SIGNED OUT CODEX STOPS THE ARM (1.1.6 S2). seat_gate kept only Claude proofs, so a Codex whose login file was
    gone read OK and every codex seat of the night failed. When a Codex proof reads OK, probe() (default
    CodexAdapter.login_probe, which names the auth file and copies nothing) answers for its login; anything but OK, a
    probe that raises included, turns every OK Codex proof into FAIL with no binding and the remedy "codex login"."""
    codex = [i for i, p in enumerate(proofs or []) if isinstance(p, dict) and p.get("transport") == "codex" and p.get("status") == "OK"]
    if not codex:
        return proofs
    try:
        if probe is None:
            from adapters import codex as CX   # an install without the adapters reads FAIL below, never OK
            probe = CX.login_probe
        state, why = probe()
    except Exception as exc:   # sbe: allow-silent an unreadable seat state refuses the arm, named below
        state, why = "FAIL", "the codex login could not be probed (%s)" % type(exc).__name__
    if state == "OK":
        return proofs
    out = list(proofs)
    for i in codex:
        out[i] = dict(out[i], status="FAIL", binding=None, cached=False, seat="codex",
                      remedy="codex login, then prepare again", cause="the program answered but its login does not (%s)" % why)
    return out


def seat_lines(proofs):
    """One "SEAT     <transport> signed out: <remedy>" line per transport whose seat gate failed, in proof order. The
    launcher (start_run.sh) reads these lines to refuse the arm with one notify row per transport."""
    seen, lines = set(), []
    for p in proofs or []:
        if isinstance(p, dict) and p.get("status") == "FAIL" and p.get("seat") and p["seat"] not in seen:
            seen.add(p["seat"])
            lines.append("SEAT     %s signed out: %s" % (p["seat"], p.get("remedy") or "sign in again"))
    return lines


NOTIFY_FILE = "notify-request.jsonl"


def notify_once(run_dir, key, step, now):
    """Append {key, step, at} to <run_dir>/notify-request.jsonl only when no row there carries `key`; True when it
    wrote. A line that does not parse is skipped (never read as the key), a file that cannot be read is read as having
    no row (a second relay beats a silent one), and a write that fails raises OSError for the caller to name."""
    path = os.path.join(run_dir, NOTIFY_FILE)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue   # sbe: allow-silent a line that does not parse is never read as the key; a second relay beats none
                if isinstance(row, dict) and row.get("key") == key:
                    return False
    except FileNotFoundError:
        pass   # sbe: allow-silent no file yet is no row yet
    except OSError:
        pass   # sbe: allow-silent unreadable: the append below either writes or raises
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": key, "step": step, "at": now}) + "\n")
    return True


LAUNCH_RECORD_MAX = 1 << 20   # a launch record bigger than this is skipped as corrupt


def _launch_records(records_dir):
    """([(path, record)] newest first by mtime, [skip notes]) for the launch-*.json records in records_dir; a folder
    that is absent answers ([], []); one that cannot be listed answers ([], [why])."""
    try:
        names = [n for n in os.listdir(records_dir) if n.startswith("launch-") and n.endswith(".json")]
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], ["the launch records cannot be read (%s)" % type(exc).__name__]
    paths = []
    for n in names:
        p = os.path.join(records_dir, n)
        try:
            paths.append((os.stat(p).st_mtime, n, p))
        except OSError:
            continue   # sbe: allow-silent a record that vanished between the listing and the stat is no earlier launch
    out, notes = [], []
    for _m, n, p in sorted(paths, reverse=True):
        try:
            if os.path.getsize(p) > LAUNCH_RECORD_MAX:
                notes.append("%s is over 1 MiB, skipped" % n)
                continue
            with open(p, encoding="utf-8") as fh:
                rec = json.load(fh)
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except (OSError, ValueError):
            notes.append("%s does not parse, skipped" % n)
            continue
        out.append((p, rec))
    return out, notes


def reboot_note(records_dir, boot_now):
    """'' when the newest launch record's boot field equals boot_now, else the note: "the last run <stamp> was killed
    by a reboot at <boot_now>" when they differ, "boot not recorded" when the record has none (never a reboot), or why
    it cannot be told (the boot now unreadable, the records unreadable). Records that do not parse are skipped, named."""
    recs, notes = _launch_records(records_dir)
    note = ""
    if recs:
        rec = recs[0][1]
        stamp, boot = str(rec.get("stamp") or "(no stamp)")[:80], rec.get("boot")
        if not isinstance(boot_now, str) or not boot_now.strip():
            note = "the boot time cannot be read now, so whether the last run %s was killed by a reboot cannot be told" % stamp
        elif not isinstance(boot, str) or not boot.strip():
            note = "the last run %s has its boot not recorded, so a reboot since it cannot be told" % stamp
        elif boot != boot_now:
            note = "the last run %s was killed by a reboot at %s" % (stamp, boot_now[:200])
    return "; ".join(notes + ([note] if note else []))


def mark_not_delivered(records_dir, boot_now):
    """The newest launch record, named killed by a reboot, gets delivered=false and killed_by_reboot=boot_now, written
    through a temporary file; the path, or "" when there was nothing to mark."""
    recs, _notes = _launch_records(records_dir)
    if not recs or "killed by a reboot" not in reboot_note(records_dir, boot_now):
        return ""
    path, rec = recs[0]
    rec.update(delivered=False, killed_by_reboot=boot_now[:200])
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1)
    os.replace(tmp, path)
    return path


FETCH_FRESH_S = 120   # LA3.b: a launcher fetch younger than this, of the sha the upstream still names, is not repeated
LIVE_FRESH_S = 30     # LA3.b: a launcher live check younger than this, that read nothing alive, is not repeated
_SHA40 = re.compile(r"[0-9a-f]{40}")
_EPOCH = re.compile(r"[0-9]+(\.[0-9]+)?")


def _age(at, now):
    """Seconds since `at`, or None when `at` is not a plain epoch number or lies in the future (stale-unknown)."""
    if not isinstance(at, (int, float)) and not (isinstance(at, str) and _EPOCH.fullmatch(at)):
        return None
    age = (time.time() if now is None else now) - float(at)
    return age if age >= 0 else None


def tree_state(repo, fetched_sha=None, fetched_at=None, now=None, run=None):
    """(state, detail) for the tree a driver would run in: OK only when it is clean AND equal to its upstream.
    THE UPSTREAM IS READ FROM GIT, NEVER WRITTEN AS A LITERAL (owner, 2026-09-22: fix it at the root, for anyone who
    clones this repository). A literal remote and branch name is true on one laptop and false in every other clone.
    No upstream, or git unable to answer, is NO-DATA: an unknown tree never reads as a clean one.
    ONE FETCH (LA3.b, 2026-10-12): start_run.sh has just fetched and passes the upstream sha it read and when. The
    fetch here is skipped only when that sha is a full sha, still what git names for the upstream, and at most
    FETCH_FRESH_S old; any doubt (no sha, an odd one, a moved upstream, a stale or unreadable time) fetches as before."""
    def _run(cmd, timeout):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            return None
    run = run or _run
    if not os.path.isdir(os.path.join(repo, ".git")) and not os.path.isfile(os.path.join(repo, ".git")):
        return "NO-DATA", "no git checkout at %s: run prepare from the launch worktree" % repo
    up = run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], 60)
    if up is None or up.returncode != 0 or "/" not in up.stdout.strip():
        return "NO-DATA", "this branch has no upstream, so nothing says where a landing pushes: git branch --set-upstream-to <remote>/<branch>"
    up = up.stdout.strip()
    fresh = False
    age = _age(fetched_at, now)
    if isinstance(fetched_sha, str) and _SHA40.fullmatch(fetched_sha) and age is not None and age <= FETCH_FRESH_S:
        head = run(["git", "-C", repo, "rev-parse", up], 60)
        fresh = head is not None and head.returncode == 0 and head.stdout.strip() == fetched_sha
    if not fresh:
        run(["git", "-C", repo, "fetch", "-q", up.split("/", 1)[0]], 120)
    st = run(["git", "-C", repo, "status", "--porcelain"], 60)
    ah = run(["git", "-C", repo, "rev-list", "--count", up + "..HEAD"], 60)
    if st is None or ah is None or st.returncode != 0 or ah.returncode != 0 or not ah.stdout.strip().isdigit():
        return "NO-DATA", "git could not report the launch tree, so whether the first pass would be BLOCKED is unknown"
    dirty = len([l for l in st.stdout.splitlines() if l.strip()]); ahead = int(ah.stdout.strip())
    if dirty or ahead:
        return "REFUSED", "launch tree has %d changed path(s) and %d unpushed commit(s): the first pass exits 44 BLOCKED on this" % (dirty, ahead)
    return "OK", "launch tree clean and equal to " + up


# THE OWNER ANSWERS IN HIS OWN WORDS, NOT IN FLAGS (owner, 2026-09-22: "people will type comments and pick other; it should
# be handled by the LLM and the harness underneath it; think of the whole intake"). The session relays each answer verbatim;
# these readers turn words into the one value the check needs, deterministically, and prepare prints a READ line for every
# answer it had to interpret so the owner sees what was understood. Nothing here guesses: a reading that is not certain
# returns None and the check that needs it refuses with the example to type.
def spoken_names():
    """{phrase: registry name} from each row's `spoken` list (FX-31.6): the registry row is where a spoken name is added,
    never this file. READ WHEN A WORD IS READ, NEVER AT IMPORT (review of 2026-10-04): a registry the router refuses
    must stop one reading with a named refusal, not this module's import. Raises model_router.Refused."""
    return model_router.derive_model_views(model_router.registry())["spoken"]


def __getattr__(name):
    """MODEL_ALIASES stays readable as a module attribute for its callers, resolved on the read (PEP 562)."""
    if name == "MODEL_ALIASES":
        return spoken_names()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


NO_CHOICE = ("none", "no", "skip", "nobody", "nothing", "-", "n/a", "na", "null")


def read_model(text):
    """A model name from the owner's words: case, spaces, a parenthetical like (Codex), an effort suffix like 'at xhigh'
    and the known aliases are absorbed. 'none' and its kin mean NO choice (the role stays unchosen, which the checks then
    ask about). Anything else is returned as typed so the role check can refuse it with the list that qualifies."""
    if text is None: return None
    t = re.sub(r"\s+", " ", str(text)).strip().lower()
    t = re.sub(r"\([^)]*\)", "", t).strip()                                   # astra (codex) -> astra
    t = re.sub(r"\b(at|on|effort)\s+(minimal|low|medium|high|xhigh|max)\b", "", t).strip()   # deepseek at xhigh -> deepseek
    t = re.sub(r"\b(minimal|low|medium|high|xhigh|max)\b$", "", t).strip()
    t = t.rstrip(" .,;:!")
    if t in NO_CHOICE or not t: return ""
    try:
        spoken = spoken_names()
    except model_router.Refused:   # sbe: allow-silent a reader that never rewrites: None is its not certain answer, and main() derives these views before it reads a word and prepares nothing when they refuse (INTAKE NO-DATA)
        return None
    t = spoken.get(t, t)
    return t.split(" ")[0] if " " in t and t.split(" ")[0] in spoken.values() else t


def read_budget(text):
    """USD from the owner's words: '80', '80.5', '$80', '80USD', '80 usd', 'USD 80', '80,5' (a decimal comma), '1,000'.
    Words like 'eighty', nan, inf, empty or two numbers return None."""
    if text is None: return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text) if text == text and text not in (float("inf"), float("-inf")) else None
    t = str(text).strip().lower()
    t = re.sub(r"(usd|us\$|dollars?|bucks|\$)", " ", t)
    t = re.sub(r"\b(budget|max|cap|up to|at most|spend|of)\b", " ", t).strip()
    m = re.fullmatch(r"([0-9][0-9.,]*)", t.replace(" ", ""))
    if not m: return None
    n = m.group(1)
    if "," in n and "." not in n:
        n = n.replace(",", "") if re.fullmatch(r"[0-9]{1,3}(,[0-9]{3})+", n) else n.replace(",", ".")
    else:
        n = n.replace(",", "")
    try:
        return float(n)          # the pattern above admits digits, dots and commas only, so nan and inf cannot reach here
    except ValueError:   # sbe: allow-silent an unreadable figure is None and the intake refuses it by name (INTAKE REFUSED: budget unreadable)
        return None


def read_deadline(text, now):
    """HH:MM today from the owner's words: '18:00', '18:00 JST', '6pm', '6 pm', '6:30pm', '18h', '18h30', '1800', '18.00',
    'at 18:00', 'until 6pm', 'in 4 hours', '+90m', '4h from now'. A day other than today ('tomorrow', a date) returns
    None: the driver runs until a time TODAY and says so."""
    if text is None: return None
    t = str(text).strip().lower()
    t = re.sub(r"\b(jst|utc|gmt|local|today|at|until|till|by|deadline|stop|o'clock)\b", " ", t).strip()
    t = re.sub(r"\s+", " ", t)
    # ANOTHER DAY IS ALLOWED (owner 2026-09-23 00:0x, "Extend the grant to tomorrow 24h"): a deadline past midnight comes back
    # dated, "YYYY-MM-DD HH:MM", and every reader goes through deadline_dt(). Weekday names still refuse (which one is a guess).
    if re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t): return None
    dated = lambda then: then.strftime("%H:%M") if then.date() == now.date() else then.strftime("%Y-%m-%d %H:%M")
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) (\d{1,2}):(\d{2})", t)
    if m:
        try: return dated(datetime.datetime.strptime("%s %02d:%s" % (m.group(1), int(m.group(2)), m.group(3)), "%Y-%m-%d %H:%M"))
        except ValueError: return None
    m = re.fullmatch(r"tomorrow(?: (.+))?", t)
    if m:
        hhmm = read_deadline(m.group(1), now) if m.group(1) else None
        if m.group(1) and (hhmm is None or "-" in hhmm): return None
        h, mm = (int(hhmm[:2]), int(hhmm[3:])) if hhmm else (now.hour, now.minute)
        return dated((now + datetime.timedelta(days=1)).replace(hour=h, minute=mm))
    m = re.fullmatch(r"(?:in |\+)?\s*(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)(?: from now)?", t)
    if m and (t.startswith(("in ", "+")) or t.endswith("from now")):     # '18h' is a time of day; 'in 18h' is a duration
        amount = float(m.group(1)); minutes = amount * 60 if m.group(2).startswith("h") else amount
        return dated(now + datetime.timedelta(minutes=minutes))
    m = re.fullmatch(r"(\d{1,2})(?:[:h.](\d{2})?)?\s*(am|pm)?", t) or re.fullmatch(r"(\d{2})(\d{2})()", t)
    if not m: return None
    h, mm, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3) or ""
    if ap == "pm" and h < 12: h += 12
    if ap == "am" and h == 12: h = 0
    if not (0 <= h <= 23 and 0 <= mm <= 59): return None
    return "%02d:%02d" % (h, mm)


MAX_RUN_H = 48   # the longest one run's grant may live (review 2026-09-23: a 2099 deadline wrote a 73 year ceiling)


def deadline_dt(text, now):
    """The record's deadline ('HH:MM' today or 'YYYY-MM-DD HH:MM') as a datetime on now's clock, or None when unreadable."""
    m = re.fullmatch(r"(?:(\d{4}-\d{2}-\d{2}) )?([01]\d|2[0-3]):([0-5]\d)", str(text or ""))
    if not m: return None
    try:
        day = datetime.datetime.strptime(m.group(1), "%Y-%m-%d").date() if m.group(1) else now.date()
        return now.replace(year=day.year, month=day.month, day=day.day, hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
    except ValueError: return None


PIN_NAMES = ("BROTHER_CLAUDE_BIN", "BROTHER_CODEX_BIN")   # the programs the proof pair runs; the owner pins them, the intake carries the pin


def launch_pins(env):
    """(pins, problems) from a mapping. A pin is SET and non-empty; it must be an absolute path to an existing, executable
    regular file with no single quote (launch-env.sh single quotes it). A bad one is one problem naming the variable and
    why; an unset or empty one is neither a pin nor a problem."""
    pins, problems = {}, []
    for name in PIN_NAMES:
        val = env.get(name)
        if val is None or val == "": continue
        if not isinstance(val, str): why = "is not text"
        elif "'" in val: why = "contains a single quote"
        elif not os.path.isabs(val): why = "is not an absolute path"
        elif not os.path.isfile(val): why = "is not an existing regular file"
        elif not os.access(val, os.X_OK): why = "is not executable"
        else: pins[name] = val; continue
        problems.append("%s %r %s" % (name, val if isinstance(val, str) else type(val).__name__, why))
    return pins, problems


def role_plan(roles, reg, env=None):
    """[(role, model)] for every model a run will call: each chosen or default role (the orchestrator is this session
    and not a loop call), every arm of the worker mix, and the two side roles, filtered to the run's transports. The same
    choices dispatch makes: worker_mix.parse and the side roles' own settings."""
    e = os.environ if env is None else env
    import worker_mix as WM, model_router as R   # the intake reads the mix; the call boundary never imports it
    try:
        allowed = R.transports_allowed(e)
    except R.Refused:
        allowed = None   # the intake's own allowlist check refuses it; proving every transport here is the safe side
    plan = []
    def add(role, name):
        if name and name != "off" and name in reg and (allowed is None or reg[name].get("transport") in allowed):
            if (role, name) not in plan:
                plan.append((role, name))
    for role, name in (roles or {}).items():
        if role != "orchestrator":
            add(role, name)
    try:
        mix_text = e.get("BROTHER_WORKER_MIX", WM._ABSENT)   # absent reads the registry default; present is the caller's, as given
        arms = WM.parse(WM.default_mix() if mix_text is WM._ABSENT else mix_text)
    except (ValueError, AttributeError, R.Refused):   # an unreadable mix, or a registry that refuses, names no arm (NO-DATA)
        arms = []
    for name, _count in arms or []:
        add("worker-mix", name)
    add("build-plan", e.get("BROTHER_BUILD_PLAN_MODEL", "sonnet"))
    add("repair-advisor", e.get("BROTHER_REPAIR_ADVISOR_MODEL", "sonnet"))
    return plan


AFTER_RUN = ("after_run", "after_final_check")   # a role that acts only after the loop has exited cannot stop a run from working


def _clock_read(clock):
    """One reading, or None when the clock raises or answers anything but a finite number (never a bool)."""
    try:
        t = clock()
    except Exception:   # sbe: allow-silent a clock that cannot be read is a NO-DATA timing row, never a verdict (LA1)
        return None
    if isinstance(t, bool) or not isinstance(t, (int, float)) or t != t or t in (float("inf"), float("-inf")):
        return None
    return float(t)


def timed(name, fn, sink, clock=time.monotonic):
    """THE STEP TIMER (LA1.a, reporting only): call fn() and append (name, seconds or None, raised) to sink. An exception
    from fn propagates unchanged after its row is recorded; a clock that cannot be read records None. Nothing here
    reads or changes a state, so the verdict is exactly what it is without the timer."""
    t0 = _clock_read(clock)
    raised = True
    try:
        out = fn()
        raised = False
        return out
    finally:
        t1 = _clock_read(clock)
        sink.append((name, None if t0 is None or t1 is None else max(0.0, t1 - t0), raised))


def time_lines(sink):
    """One `TIME <step> <seconds>` line per row, `TIME NO-DATA <step>: ...` for a clock that could not be read. Every
    line starts with TIME, so the launcher's refusal reader (^(REFUSED|NO-DATA)) can never match one. Never raises."""
    out = []
    for row in sink:
        try:
            name, sec = str(row[0]), row[1]
            out.append("TIME NO-DATA %s: the clock could not be read" % name if sec is None else "TIME %s %.1f" % (name, float(sec)))
        except Exception:   # sbe: allow-silent a row of an unknown shape is a NO-DATA line, the timer never breaks an intake
            out.append("TIME NO-DATA: a timing row could not be read")
    return out


#: LA2.a: the intake's independent read only probes run together, at most PROBE_WORKERS at once, and are READ (lines,
#: states, TIME rows) in PROBE_ORDER, never in completion order. Every real probe is a subprocess with its own timeout.
PROBE_WORKERS = 8
PROBE_ORDER = ("headroom", "alive", "lease", "parity", "switch", "tree", "digest", "done", "salvage", "pool", "lint", "canary", "reach", "hold")
_TIME_NAME = {"lint": "plan_lint"}   # the plan lint keeps ONE line naming it (FX-13.5)
_CANARY_AFTER = ("tree", "digest")   # the canary's `git worktree add` never overlaps their git status / fetch


def _raised(name, exc):
    """A probe that raised, as the value its existing NO-DATA branch in prepare reads (never the safe case)."""
    if name in ("headroom", "reach"):
        return None   # "could not be read" / "no proof was run": NO-DATA
    if name == "hold":
        return True   # an unreadable hold reads as HELD: nothing starts
    return "NO-DATA", "probe %s raised %s: %s" % (name, type(exc).__name__, str(exc)[:120])


def run_probes(probes, reach_plan, sink, workers=PROBE_WORKERS, clock=time.monotonic):
    """{name: result} for every probe in PROBE_ORDER the dictionary holds (reach only with a plan, lint only when
    callable), run in a pool of `workers`; the canary is submitted only after tree and digest have finished. Each call
    is timed (LA1.a) and its rows reach `sink` in PROBE_ORDER after the join. A probe that raises is _raised(name)."""
    import concurrent.futures as cf
    names = [n for n in PROBE_ORDER if n in probes and (n != "reach" or reach_plan) and (n != "lint" or callable(probes[n]))]
    rows = {n: [] for n in names}
    def task(name):
        fn = probes[name]
        try:
            return timed(_TIME_NAME.get(name, name), (lambda: fn(reach_plan)) if name == "reach" else fn, rows[name], clock)
        except Exception as exc:   # sbe: allow-silent a raising probe is a NO-DATA result its verdict branch reads, never a traceback
            return _raised(name, exc)
    with cf.ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futs = {n: pool.submit(task, n) for n in names if n != "canary"}
        if "canary" in names:
            cf.wait([futs[n] for n in _CANARY_AFTER if n in futs])
            futs["canary"] = pool.submit(task, "canary")
    for n in names:
        sink.extend(rows[n])
    return {n: futs[n].result() for n in names}


def prepare(choices, deadline, budget, scope, roles, reg, probes, accept=(), accepted_by="", pair=False, env=None, plan=None, tree_root=None, clock=time.monotonic):
    """(record, lines). Every check appends exactly one line; the verdict is derived from the states, nowhere else.
    pair=True makes the record the proof pair's: its deadline is the pair's end and must cover RB and RC.
    plan: the --plan value, the plan file this run reads and writes (through plan_path, the one selector); None keeps
    the selected plan (BROTHER_WBS or the default). tree_root: the launch tree the plan is read in (default: the
    checkout this file lives in)."""
    lines, states, waived = [], [], []
    timings = []   # LA1.a: one (step, seconds, raised) row per probe call, in call order; printed before the verdict
    def add(state, text, hint=""):
        states.append(state); lines.append("%-8s %s%s" % (state, text, ("  HINT: " + hint) if hint and state != "OK" else ""))
    def advise(key, text, risk):
        """An ADVISABLE condition that is not met: the user is asked, or has accepted it BY NAME and with a name."""
        if key in accept and accepted_by.strip():
            waived.append(key); states.append("OK"); lines.append("WAIVED   %s  (accepted by: %s)" % (text, accepted_by.strip()[:120]))
        else:
            states.append("ASK"); lines.append("ASK      %s  RISK: %s  TO CONTINUE ANYWAY: add --accept %s --accepted-by \"<who, in their words>\"" % (text, risk, key))
    code, role_lines = loop_roles.check(choices, roles, reg)
    role_bad = False
    for l in role_lines[:-1]:
        role = l.split()[1].rstrip(":") if len(l.split()) > 1 else ""
        refused = l.startswith("REFUSED")
        if refused and role in roles and roles[role]["when"] in AFTER_RUN and not choices.get(role):
            advise(role, "no %s was chosen" % role, "a run works without one, but %s" % ("whatever does not land waits for a human" if role == "finisher" else "nothing writes the documentation afterwards"))
            lines[-1] += "  HINT: models that qualify for %s: %s" % (role, ", ".join(qualifying(role, roles[role], reg)) or "none")
            continue
        hint = "models that qualify for %s: %s" % (role, ", ".join(qualifying(role, roles[role], reg)) or "none") if refused and role in roles else ""
        lines.append(l + (("  HINT: " + hint) if hint else "")); role_bad = role_bad or refused
    states.append("REFUSED" if role_bad else "OK")
    now = probes["now"]()
    dl = deadline_dt(deadline, now)
    if dl is None:
        add("REFUSED", "deadline %r is not HH:MM or YYYY-MM-DD HH:MM" % deadline, "give the stop time, for example --deadline 18:00 or --deadline 'tomorrow 18:00'")
    elif dl <= now:
        add("REFUSED", "deadline %s is not in the future (now %s)" % (deadline, now.strftime("%Y-%m-%d %H:%M")), "the driver runs until a time after now")
    elif dl > now + datetime.timedelta(hours=MAX_RUN_H):
        add("REFUSED", "deadline %s is more than %d h away: a run's money must not outlive the run" % (deadline, MAX_RUN_H), "name a deadline within %d hours" % MAX_RUN_H)
    elif pair and dl < now + datetime.timedelta(seconds=PAIR_COVER_S["RB"]):
        add("REFUSED", "deadline %s does not cover the proof pair: RB and RC need %.2f h from now" % (deadline, PAIR_COVER_S["RB"] / 3600.0),
            "name the pair's end at least %.1f h ahead" % (PAIR_COVER_S["RB"] / 3600.0))
    else:
        add("OK", "deadline %s%s" % (deadline, " today" if "-" not in str(deadline) else ""))
    # LA2.a: every independent probe runs here, together (run_probes); each is READ below at its old position, so the
    # lines, the states and the verdict are the serial ones. The role plan is pure, so reach can start with the rest.
    role_models = {r: choices.get(r) or roles[r].get("default") for r in roles}
    reach_plan = role_plan(role_models, reg, os.environ if env is None else env)
    results = run_probes(probes, reach_plan, timings, workers=PROBE_WORKERS, clock=clock)
    room = results["headroom"]
    if budget is None or budget <= 0:
        add("REFUSED", "budget %r is not a positive number of USD" % budget, "say what this run may spend, for example --budget-usd 10")
    elif room is None:
        add("NO-DATA", "the money headroom could not be read, so the budget cannot be checked")
    else:
        # ONE BUDGET PER RUN (owner 2026-09-22): the run's grant IS the ceiling, written by this intake from this figure, so
        # the old grant's headroom is information, never a refusal (it refused an 80 USD run over a 79.62 leftover at 19:10).
        add("OK", "budget %.2f USD for this run (the previous grant's headroom was %.2f USD; this intake writes the run's own grant)" % (budget, room))
    if scope:
        try:
            re.compile(scope); add("OK", "scope %s" % scope)
        except re.error:
            add("REFUSED", "scope %r is not a regular expression" % scope, "leave --scope out to use the pool's default")
    # THE PLAN (2026-10-10): the file every pass tool reads and writes, named once, through plan_path (the one selector).
    # --plan is REFUSED when the selector refuses it (absolute, climbing, outside docs/plan/, not .json), when the launch
    # tree lacks the file or it has no non empty units list, and, when it is not the default plan, when any unit the
    # scope admits carries an id the default plan also holds (plan_collisions): those records are shared by id. An
    # unreadable default plan refuses too (nothing can prove the ids are distinct). FAIL DIRECTION: every refusal here
    # is REQUIRED (never waived) and the record still names the plan it was asked for, so the reader sees what was tried.
    tree_root = tree_root or os.path.dirname(os.path.dirname(HERE))
    plan_sel, plan_refused = plan, ""
    try:
        if plan is None:
            plan_sel = _PP.selected(os.environ if env is None else env)
        else:
            plan_sel = _PP.validate(plan)
            sel_ids, _sel_subs = plan_unit_ids(os.path.join(tree_root, plan_sel))
            if plan_sel != _PP.DEFAULT:
                try:
                    def_ids, def_subs = plan_unit_ids(os.path.join(tree_root, _PP.DEFAULT))
                except ValueError as exc:
                    raise ValueError("the default plan cannot be read, so nothing proves the ids are distinct: %s" % exc)
                shared = plan_collisions(sel_ids, def_ids, def_subs, scope if scope else None)
                if shared:
                    raise ValueError("unit id(s) %s exist in both %s and the default plan %s; the score, council, ledger and run folder "
                                     "records key on these ids, so they are refused by name" % (", ".join(shared), plan_sel, _PP.DEFAULT))
            add("OK", "plan %s (%d unit(s))" % (plan_sel, len(sel_ids)))
    except ValueError as exc:
        plan_refused = str(exc)
        add("REFUSED", "plan %s: %s" % (plan_sel, exc), "name a .json plan under docs/plan/, relative to the launch tree, whose unit ids the default plan does not hold")
    lines.append("PLAN     %s" % plan_sel)
    # THE ALLOWLIST IS PARSED ONCE, BY THE ROUTER (attack 3, 2026-09-30): a malformed value is REFUSED here, never
    # sanitized into a valid token; the record and the launch settings carry the parser's own canonical text.
    import model_router
    _transports = None
    try:
        _allowed = model_router.transports_allowed(os.environ if env is None else env)
        if _allowed is not None:
            _transports = model_router.transports_text(_allowed); add("OK", "transports %s (BROTHER_TRANSPORTS)" % _transports)
    except model_router.Refused as exc:
        add("REFUSED", "BROTHER_TRANSPORTS is not a transport allowlist: %s" % exc, "name transports only, comma separated: claude, or claude,codex, or leave it unset")
    for name in ("alive", "lease", "parity", "switch", "tree", "digest", "done", "salvage", "pool"):
        state, detail = results[name]; add(state, detail, {"alive": "bash scripts/loop/stop_loop.sh --drain (runners leave at their checkpoints, nothing is killed), then bash scripts/loop/stop_loop.sh once it says DRAINED", "lease": "another driver holds it: stop that one first", "parity": "run the deploy tool, it never starts a driver",
                                                          "switch": "deploy the repository's model router (bash scripts/loop/deploy_stamped.sh deploy), then prepare again; a Claude only run never starts on a bin that ignores the switch",
                                                          "tree": "commit and push through the gate, or move the files out of the launch worktree; then prepare again",
                                                          "digest": "fix pass_digest.py first: a pass cannot run without it", "done": "fix loop_done.py first: the pass cannot tell FINISHED from WORKING without it",
                                                          "salvage": "fix salvage.py first: the pass promotes unlanded builds through it", "pool": "fix runner_pool.py first: the pass starts runners through it"}[name])
    # THE PLAN LINT (FX-13.5). Report mode, the default, prints its answer and never touches the states, so the verdict
    # is exactly what it was without it. Block mode asks on anything but OK, waivable with --accept lint like the
    # canary. An unknown BROTHER_RUNFLOW_LINT, or a lint module that cannot be read, is block with a NO-DATA note.
    try:
        import plan_lint
        mode, note = plan_lint.lint_mode(env)
    except ImportError:
        mode, note = "block", "NO-DATA: plan_lint.py cannot be imported beside this copy"
    except ValueError as exc:
        mode, note = "block", "NO-DATA: %s" % exc
    if "lint" not in probes:
        state, detail = "NO-DATA", "no lint probe given"
    elif not callable(probes["lint"]):
        state, detail = "NO-DATA", "the lint probe given is not callable"
    else:
        answer = results["lint"]   # timed as plan_lint, not lint: the plan lint keeps ONE line naming it (FX-13.5, test_plan_lint)
        if isinstance(answer, tuple) and len(answer) == 2 and isinstance(answer[0], str) and answer[0] in ("OK", "FINDINGS", "NO-DATA") and isinstance(answer[1], str):
            state, detail = answer
        else:
            state, detail = "NO-DATA", "the lint probe answered a %s, not (OK, FINDINGS or NO-DATA, summary)" % type(answer).__name__
    said = "lint %s: %s%s" % (state, detail, "  (%s)" % note if note else "")
    if mode == "report":
        lines.append("LINT     NO-DATA: no lint probe given" if "lint" not in probes else "LINT     %s: %s (report mode: information, the verdict is unchanged)" % (state, detail))
    elif state == "OK":
        add("OK", said)
    else:
        advise("lint", said, "units with blocking findings will be skipped by the pool; the report names each")
    state, detail = results["canary"]
    if state == "NO-DATA":   # it could not be RUN, which is different from running and failing
        advise("canary", "canary: " + detail, "nothing proves the whole path end to end before money is spent; on 2026-09-22 that cost a night")
    else:
        add(state, "canary: " + detail, "a canary that ran and failed is never waived: fix the stage it names")
    pins, pin_problems = launch_pins(os.environ if env is None else env)
    for name, path in pins.items(): add("OK", "pin %s=%s: the pair runs exactly this program" % (name, path))
    for prob in pin_problems: add("REFUSED", "a pin that does not name an executable: " + prob, "set it to the absolute path of an executable program, or unset it, then prepare again")
    # AN EXECUTABLE IS NOT A PROOF (2026-09-30): the pinned claude existed and was executable, and it did not know the
    # model; 78 calls and 7 sub units were spent before anyone saw it. Every model the run will call answers, through the
    # program the run will use, or the run does not start. REQUIRED: a FAIL or a NO-DATA here can never be waived.
    import model_reachability as MR
    plan = reach_plan
    proofs = results.get("reach")   # absent when no reach probe was given or the plan is empty, as before
    if not plan:
        add("NO-DATA", "reachability: the role plan names no model the registry knows, so nothing proves a call can be answered")
    elif not isinstance(proofs, list) or len(proofs) != len(plan):
        add("NO-DATA", "reachability: no proof was run for %s, so nothing shows these models answer through their programs"
            % ", ".join("%s=%s" % rm for rm in plan), "prepare again from the repository, where the reachability proof runs")
    else:
        for pr in proofs:
            states.append("OK" if pr.get("status") == "OK" else "REFUSED" if pr.get("status") == "FAIL" else "NO-DATA")
            lines.append(MR.line(pr))
        lines.extend(seat_lines(proofs))   # 1.1.6 S2: the launcher refuses the arm on these, one notify row each
        _said = set()
        for pr in proofs:   # every candidate passed over, once, with its reason: the record of why this program was chosen
            for cand in pr.get("rejected") or []:
                if tuple(cand) not in _said:
                    _said.add(tuple(cand)); lines.append("PASSED   over %s %s: %s" % (cand[0], cand[1] or "(version unknown)", str(cand[2])[:200]))
    reach = list({MR.cache_key(pr["binding"]): pr["binding"] for pr in (proofs or [])
                  if isinstance(pr, dict) and pr.get("status") == "OK" and pr.get("binding")}.values())   # one per distinct proof
    programs = {}
    for pr in (proofs or []):
        b = pr.get("binding") if isinstance(pr, dict) and pr.get("status") == "OK" else None
        if b:   # the bridge too (attack R1 F2): its or_ask.py is the program a bridge proof is bound to
            programs[b["transport"]] = {"path": b["program"], "version": b["version"], "fingerprint": b["fingerprint"],
                                        "selected_by": "pin" if pr.get("pinned") else pr.get("selected_by") or "resolved",
                                        "rejected": pr.get("rejected") or []}
    held = results["hold"]
    lines.append("%-8s the owner's HOLD is %s" % ("HELD" if held else "OK", "IN FORCE: no driver may start until he orders it" if held else "absent"))
    if accept and not accepted_by.strip():
        states.append("REFUSED"); lines.append("REFUSED  --accept was given without --accepted-by: a waiver nobody signed is not a waiver")
    required_bad = any(s not in ("OK", "ASK") for s in states)
    verdict = "NOT READY" if required_bad else "NEEDS YOUR DECISION" if "ASK" in states else ("HELD" if held else "READY")
    lines.extend(time_lines(timings))   # before the verdict: the launcher reads the LAST INTAKE line, unchanged
    lines.append("INTAKE %s" % verdict)
    rec = {"verdict": verdict, "at": now.isoformat(timespec="seconds"), "epoch": time.time(), "deadline": deadline, "budget_usd": budget, "scope": scope,
           "plan": plan_sel,   # the plan file this run reads and writes (plan_path): the run's plan identity, on disk
           "plan_refused": plan_refused,   # "" or why the plan above was refused: launch_env exports nothing for a refused plan
           "transports": _transports,   # the canonical allowlist text the driver runs under, or None
           "roles": {r: choices.get(r) or roles[r].get("default") for r in roles}, "waived": waived, "accepted_by": accepted_by.strip() if waived else "", "pins": pins, "lines": lines,
           "reach": reach, "programs": programs, "proven": sorted(MR.cache_key(b) for b in reach),   # the proven bindings startable() revalidates, and the programs the run uses
           # the non Claude transports whose proof was not OK: the next launch proves them live (SEAT1.c)
           "seat_failures": sorted({pr.get("transport") for pr in (proofs or []) if isinstance(pr, dict)
                                    and pr.get("status") != "OK" and pr.get("transport") in NON_CLAUDE}),
           "timings": {row[0]: row[1] for row in timings}}   # LA1.a: step to seconds, None where the clock failed
    if pair and dl is not None:
        rec.update(pair=True, pair_until=dl.astimezone().isoformat(timespec="seconds"))
    return rec, lines


def earned_mode(role, model, authority=None):
    """gate only when the judge calibrator has GRANTED gate to this role and model, else shadow (owner order
    2026-09-23: a judge earns authority by measurement). Until 2026-09-30 the intake wrote shadow whatever the
    calibration said, so a checker measured at precision 0.956 and recall 1.0 over 45 known rulings, granted gate,
    ran as shadow: it spent Claude on every ruling and decided nothing. An unreadable calibration is shadow."""
    try:
        if authority is None:
            import judge_calibrate as JC
            authority = JC.authority
        return "gate" if authority("%s:%s" % (role, model)) == "gate" else "off"
    except Exception:   # sbe: allow-silent an unreadable calibration is off: no unproven judge runs (plan E, 2026-10-01)
        return "off"


def launch_env(rec, roles, authority=None):
    """The settings a driver needs, from the record alone. Only roles that HAVE a setting are exported."""
    out = ["# written by loop_intake.py at %s; verdict %s. Do not edit by hand: prepare a new intake." % (rec["at"], rec["verdict"])]
    for role, spec in roles.items():
        if spec.get("setting") and rec["roles"].get(role):
            out.append("export %s=%s" % (spec["setting"], rec["roles"][role]))
        if spec.get("mode_setting") and rec["roles"].get(role):
            out.append("export %s=%s" % (spec["mode_setting"], earned_mode(role, rec["roles"][role], authority)))
    if rec.get("scope"):
        out.append("export BROTHER_SCOPE='%s'" % rec["scope"].replace("'", ""))
    if rec.get("plan_refused"):
        # a plan the intake REFUSED (collision, unreadable, not a plan path) is never exported: the record says why
        out.append("# BROTHER_WBS NOT exported: the intake refused the plan: %s" % str(rec["plan_refused"]).replace("\n", " ")[:200])
    elif rec.get("plan"):
        # THE PLAN THE RUN READS AND WRITES (2026-10-10): the selector's validated path, shell quoted, so every pass tool the
        # driver starts (they all read plan_path.selected) opens the plan this intake was prepared for and no other
        try:
            out.append("export BROTHER_WBS=%s" % shlex.quote(_PP.validate(str(rec["plan"]))))
        except ValueError as exc:   # never a substitution: a record whose plan the selector refuses exports nothing, and says so
            out.append("# BROTHER_WBS NOT exported: %s" % str(exc).replace("\n", " ")[:200])
    if rec.get("transports"):
        import model_router
        try:   # the parser again, never a substitution: a record that does not parse exports nothing
            out.append("export BROTHER_TRANSPORTS=%s" % model_router.transports_text(model_router.transports_allowed({"BROTHER_TRANSPORTS": str(rec["transports"])})))
        except (model_router.Refused, TypeError):
            out.append("# BROTHER_TRANSPORTS NOT exported: the record's value %r is not a transport allowlist" % (rec["transports"],))
    for name, path in (rec.get("pins") or {}).items():
        out.append("export %s='%s'" % (name, path))   # quoted: the real Claude path has a space
    if rec.get("programs"):
        # THE RESOLUTION RECORD, never a pin: the driver's calls read the proven program from it (model_router.claude_bin)
        import model_reachability as MR
        out.append("export BROTHER_PROGRAM_RECORD='%s'" % MR.record_path().replace("'", ""))
    return "\n".join(out) + "\n"


def transports_match(rec, env=None):
    """'' when the live BROTHER_TRANSPORTS means the same allowlist the record was prepared under, else why not. Unset on
    one side and set on the other is a difference (a record prepared for claude must not start a run that spawns the
    bridge); an unreadable live value is a difference too."""
    import model_router
    want = rec.get("transports") if isinstance(rec, dict) else None
    try:
        live = model_router.transports_allowed(os.environ if env is None else env)
        live = None if live is None else model_router.transports_text(live)
    except model_router.Refused as exc:
        return "the live BROTHER_TRANSPORTS is not a transport allowlist: %s" % exc
    if (want or None) != (live or None):
        return "the record was prepared under transports %s and the driver's environment says %s: prepare again under the same BROTHER_TRANSPORTS" % (want or "unset", live or "unset")
    return ""


def reach_why(rec, resolve=None, fp=None):
    """'' when every proven binding in the record still describes the machine, else why not. A record with no bindings
    was prepared before reachability was proven, and never starts. A program whose fingerprint moved since its proof, or
    a program production would no longer resolve, is a new question: prepare again. Reads files only, never calls."""
    import model_reachability as MR
    fp = fp or MR.fingerprint
    reach = rec.get("reach") if isinstance(rec, dict) else None
    if not isinstance(reach, list) or not reach:   # an EMPTY proof proves nothing: zero bindings used to fall through to ""
        return "the record carries no reachability proof (prepared before 2026-09-30, or by hand): prepare again"
    for b in reach:
        if not isinstance(b, dict):
            return "the record's reachability proof is unreadable: prepare again"
        want = b.get("fingerprint")
        now_fp = fp(b.get("program")) if b.get("transport") != "bridge" else want
        if not want or now_fp != want:
            return "the program %s changed since it proved %s (was %s): prepare again" % (b.get("program"), b.get("model"), b.get("version"))
    import breaker as BR
    for b in reach:   # A PROVEN BINDING WHOSE BREAKER IS OPEN IS NOT READY (attack R1 F1)
        try:
            held = BR.config_open(BR.config_key(b.get("transport"), b.get("model_id")))
        except ValueError as exc:
            held = "its configuration breaker cannot be named (%s)" % exc
        if held:
            return "the configuration breaker holds %s (%s): prepare again, whose fresh proof closes it" % (b.get("model"), held)
    progs = {t: r for t, r in (rec.get("programs") or {}).items() if t != "bridge"}
    for t, row in progs.items():
        try:
            got = (resolve or _resolved_program)(t)
        except Exception as exc:   # sbe: allow-silent an unresolvable program is a refusal, named below
            return "the %s program cannot be resolved now (%s): prepare again" % (t, exc)
        if os.path.realpath(got or "") != os.path.realpath(row.get("path") or ""):
            return "production would now run %s for %s, not the proven %s: prepare again" % (got, t, row.get("path"))
    return ""


def _resolved_program(transport):
    import model_router
    return model_router.claude_bin() if transport == "claude" else model_router.codex_bin() if transport == "codex" else None


def startable(rec, now=None, max_age_h=MAX_AGE_H, phase=None):
    """'' when a driver may start on this record, else why not: the start rules below, then the reachability bindings
    (reach_why). Unknown, stale, not READY or unproven all refuse."""
    return _startable_base(rec, now, max_age_h, phase) or reach_why(rec)


def _startable_base(rec, now=None, max_age_h=MAX_AGE_H, phase=None):
    """'' when a driver may start on this record, else why not. Unknown, stale or not READY all refuse. A pair record
    is judged by what it still covers for this phase (RC by RC's run, anything else by RB's pair), never by its age; a
    proof phase (RB, RC) never starts on a record that is not a pair record."""
    if not isinstance(rec, dict): return "there is no readable intake record"
    if rec.get("verdict") != "READY": return "the intake verdict is %s, not READY" % rec.get("verdict")
    now = time.time() if now is None else now
    if rec.get("pair") is True:
        try:
            until = datetime.datetime.fromisoformat(str(rec.get("pair_until")))
        except ValueError:
            until = None
        if until is None or until.tzinfo is None:
            return "the pair record's pair_until %r is not a time with its offset" % (rec.get("pair_until"),)
        need = PAIR_COVER_S["RC" if phase == "RC" else "RB"]
        if now + need > until.timestamp():
            return ("the pair record covers until %s, and %s needs %.2f h from now: prepare a new pair record"
                    % (rec.get("pair_until"), "RC" if phase == "RC" else "RB and RC", need / 3600.0))
        return ""
    if phase in ("RB", "RC"):
        return "a proof phase starts only on a pair intake record (prepare --pair), and this one is not"
    age = (now - rec["epoch"]) / 3600.0 if isinstance(rec.get("epoch"), (int, float)) else None
    if age is None: return "the intake record carries no readable time"
    if age < 0 or age > max_age_h: return "the intake record is %.1f h old (limit %.1f): prepare a new one" % (age, max_age_h)
    return ""


def running_figures(rec, phase=None):
    """('', budget, deadline) for a driver ALREADY RUNNING, else (why, None, None). startable() decides whether a run may
    START; its coverage and age rules never gate reading the figures of a run under way. Measured 2026-09-27 (audit E1):
    seven hours into a pair the coverage check failed, status printed no BUDGET_USD line, and the driver kept spending
    against its 10.00 USD while the owner had cut the budget to 0.50. A record that is not READY, a non pair record in a
    proof phase, or a budget that is not a positive number is unreadable, and the driver holds on it."""
    if not isinstance(rec, dict): return "there is no readable intake record", None, None
    if rec.get("verdict") != "READY": return "the intake verdict is %s, not READY" % rec.get("verdict"), None, None
    if phase in ("RB", "RC") and rec.get("pair") is not True:
        return "a proof phase reads only a pair intake record, and this one is not", None, None
    b = rec.get("budget_usd")
    if isinstance(b, bool) or not isinstance(b, (int, float)) or not 0 < b < float("inf"):
        return "the intake record's budget %r is not a positive number" % (b,), None, None
    dl = rec.get("deadline")
    return "", float(b), dl if isinstance(dl, str) and dl.strip() else None


def committed_of(text):
    """What burn_guard's headroom already subtracts from its ceiling (measured spend plus every unresolved or reserved
    liability), read as ceiling minus headroom from its one MONEY line; None when the line carries no such pair.
    THE GRANT'S BASIS, NOT THE MEASURED SPEND (2026-09-27): the intake granted spend 268.99 + 100 while the burn guard
    also charged 104.86 USD of abandoned holds, so a READY run read headroom -4.86 and stopped UNFUNDED at pass 0."""
    m = re.search(r"MONEY\s+spent [^|]*? of ([0-9]+(?:\.[0-9]+)?) USD \| headroom (-?[0-9]+(?:\.[0-9]+)?)", text or "")
    return round(float(m.group(1)) - float(m.group(2)), 2) if m else None


def spend_now(probes=None):
    """burn_guard's committed figure (see committed_of), or None."""
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "burn_guard.py")], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return committed_of(r.stdout)


def write_grant(budget, deadline_hhmm, spend_at_start, now=None, root=None, runs=1):
    """ONE BUDGET PER RUN (owner 2026-09-22): the grant file both money readers use becomes the run's own figure:
    cap = spend at the run's start + the run's budget (the cumulative basis burn_guard reads), until = the run's
    deadline today. It expires with the run, so no run's money overlaps another's; the next intake rewrites it. A pair
    record (runs=2) grants two run budgets until the pair's end, so the grant cannot expire inside the pair.
    Returns the path written, or None when any input is unreadable (nothing is written on a guess)."""
    try:
        now = now or datetime.datetime.now().astimezone()
        until = deadline_dt(deadline_hhmm, now)
        if budget is None or budget <= 0 or spend_at_start is None or until is None: return None
        root = root or os.environ.get("BROTHER_OR_STATE_ROOT") or os.path.expanduser("~/.claude/brother-or-dispatch-state")
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, "cap-grant.json")
        rec = {"daily_cap": round(float(spend_at_start) + runs * float(budget), 2), "until": until.isoformat(timespec="seconds"),
               "grant_note": "RUN BUDGET written by loop_intake.py at %s: %d x %.2f USD for %s on top of %.2f USD spent or still unresolved at its start; expires with the deadline; the next intake rewrites it"
                             % (now.strftime("%Y-%m-%d %H:%M %Z"), runs, budget, "this run" if runs == 1 else "the proof pair", spend_at_start)}
        tmp = path + ".tmp-%d" % os.getpid()
        # L5a-9b: the grant is owner only from its first byte (the reader refuses a grant open to others), and a name
        # already there is never written through (O_EXCL); a failed write leaves no temp behind
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(rec, f, indent=1)
            os.replace(tmp, path)
        except BaseException:
            try: os.unlink(tmp)
            except OSError: pass   # sbe: allow-silent the temp is gone or never made; the failure below is what is reported
            raise
        return path
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def read_current():
    try:
        with open(os.path.join(ev(), "loop-intake", "CURRENT.json"), encoding="utf-8") as f:
            return proof_ledger.loads(f.read())
    except (OSError, ValueError):
        return None


def main():
    if "--selftest" in sys.argv:
        return selftest()
    a = sys.argv[1:]; verb = a[0] if a else "guide"
    # A HELP FLAG IS NOT A PREPARE. On 2026-09-22 13:11 `--help` fell through to prepare and overwrote a READY record
    # with NOT READY. Only the three verbs act; anything else prints the usage and writes nothing.
    if verb in ("-h", "--help", "help"):
        verb = "guide"
    if verb == "budget":
        # CHANGE A RUNNING RUN'S BUDGET OR DEADLINE (owner 2026-09-22). Only through here, only on a READY record, and the
        # grant follows: nothing else on the machine writes the run's money. The words are kept beside the figures.
        rec = read_current()
        if not isinstance(rec, dict) or rec.get("verdict") != "READY":
            print("INTAKE NO-DATA: no READY record to change; prepare a run first"); return 2
        opt = lambda n: a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else None
        b = read_budget(opt("--budget-usd")) if opt("--budget-usd") is not None else rec.get("budget_usd")
        dl = read_deadline(opt("--deadline"), datetime.datetime.now()) if opt("--deadline") is not None else rec.get("deadline")
        _now = datetime.datetime.now(); _dt = deadline_dt(dl, _now) if dl else None
        if b is None or b <= 0 or dl is None or _dt is None or _dt <= _now or _dt > _now + datetime.timedelta(hours=MAX_RUN_H):
            print("INTAKE REFUSED: budget %r or deadline %r unreadable, past, or more than %d h away; nothing changed" % (opt("--budget-usd"), opt("--deadline"), MAX_RUN_H)); return 1
        rec["budget_usd"] = b; rec["deadline"] = dl; rec["changed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        rec["owner_words"] = ((rec.get("owner_words") or "") + " | " + str(opt("--words") or ""))[-2000:]
        try:
            d = os.path.join(ev(), "loop-intake"); tmp = os.path.join(d, "CURRENT.json.tmp-%d" % os.getpid())
            with open(tmp, "w", encoding="utf-8") as f: json.dump(rec, f, indent=1)
            os.replace(tmp, os.path.join(d, "CURRENT.json"))
        except OSError as exc:
            print("INTAKE NO-DATA: the record could not be written (%s)" % type(exc).__name__); return 3
        if rec.get("pair") is not True:
            # A PLAIN RUN'S MONEY IS ITS OWN (owner, 2026-09-27): the running driver reads this change at its next pass
            # and rewrites its run's budget record; the shared grant is never touched for it.
            print("INTAKE CHANGED: budget %.2f USD, deadline %s; the running driver rewrites its run's own budget at its next pass" % (b, dl)); return 0
        _sp = rec.get("spend_at_start")
        g = write_grant(b, dl, spend_now() if _sp is None else _sp, runs=2)   # lazy: burn_guard (and the provider) only when the record lacks it
        print("INTAKE CHANGED: budget %.2f USD, deadline %s, grant %s" % (b, dl, g or "NOT WRITTEN")); return 0 if g else 1
    if verb not in ("guide", "status", "prepare", "budget"):
        print("INTAKE NO-DATA: unknown verb %r; the verbs are guide, status, prepare. Nothing was written." % verb[:40]); return 2
    if verb == "status" and "--running" in a:
        # THE RUNNING DRIVER'S READING (audit E1), every pass: the current budget and deadline, never the start rules.
        why, b, dl = running_figures(read_current(), phase=os.environ.get("BROTHER_PROOF_PHASE") or None)
        print("INTAKE FIGURES: %s" % ("the running driver's budget and deadline" if not why else "UNREADABLE: " + why))
        if not why:
            print("BUDGET_USD %.2f" % b)                    # a cut applies at once; the driver records it as an intervention
            if dl: print("DEADLINE %s" % dl)
        return 0 if not why else 1
    if verb == "status":
        rec = read_current(); why = startable(rec, phase=os.environ.get("BROTHER_PROOF_PHASE") or None) or transports_match(rec)
        print("INTAKE STATUS: %s" % ("a driver may start on the current record (%s)" % rec["at"] if not why else "NO START: " + why))
        if not why:
            print("BUDGET_USD %.2f" % float(rec["budget_usd"]))   # the driver reads this line at its start
            if rec.get("deadline"): print("DEADLINE %s" % rec["deadline"])
        return 0 if not why else 1
    roles = loop_roles.load_roles()
    try:
        import model_router
        reg = model_router.registry()
        # THE VIEWS ARE PART OF "CAN BE READ" (2026-10-06). The loader checks five fields per row and the derived views
        # check the rest, so a registry can load while its views refuse (a retired row that kept its mix share). The
        # owner's words are read through those views: read_model then answered None, the None was stored as the role's
        # choice, and loop_roles.judge reads `choice or default`, so a typed model quietly became the role's default.
        model_router.derive_model_views(reg)
    except Exception:
        reg = None
    if roles is None or reg is None:
        print("INTAKE NO-DATA: the roles file or the model registry cannot be read; nothing prepared"); return 3
    if verb == "guide":
        print("\n".join(guide(roles, reg))); return 0
    opt = lambda n: a[a.index(n) + 1] if n in a and a.index(n) + 1 < len(a) else None
    read = []                                                                  # every interpretation, shown before the checks
    budget = read_budget(opt("--budget-usd"))
    if opt("--budget-usd") is not None and budget is not None and str(opt("--budget-usd")).strip() != ("%g" % budget):
        read.append("READ     budget %r understood as %.2f USD" % (opt("--budget-usd"), budget))
    deadline = read_deadline(opt("--deadline"), datetime.datetime.now())
    if opt("--deadline") is not None and deadline is not None and str(opt("--deadline")).strip() != deadline:
        read.append("READ     deadline %r understood as %s today" % (opt("--deadline"), deadline))
    if opt("--deadline") is not None and deadline is None:
        deadline = str(opt("--deadline"))                                       # the check refuses it with the example
    choices = {}
    for x in a[1:]:
        if "=" in x and not x.startswith("--"):
            k, v = x.split("=", 1); m = read_model(v)
            if m == "":
                read.append("READ     %s %r understood as no choice: the check asks about it" % (k.strip().lower(), v)); continue
            if m != v.strip(): read.append("READ     %s %r understood as %s" % (k.strip().lower(), v, m))
            choices[k.strip().lower()] = m
    # THE PLAN (2026-10-10): a valid --plan reaches the probes through the environment, the way every pass tool reads it
    # (plan_path.selected), so the lint, the digest, the pool's dry run and loop_done all read the plan this run is for.
    # An invalid value exports nothing: prepare refuses it by name and the probes keep reading the plan already selected.
    global LINT_PLAN
    plan_opt = opt("--plan")
    if plan_opt is not None:
        try:
            os.environ[_PP.ENV] = _PP.validate(plan_opt); LINT_PLAN = _PP.selected()
        except ValueError:   # sbe: allow-silent prepare() below refuses the same value with its reason, as a REFUSED line and a NOT READY verdict
            pass
    rec, lines = prepare(choices, deadline, budget, opt("--scope"), roles, reg, real_probes(),
                         accept=tuple(x for x in (opt("--accept") or "").split(",") if x), accepted_by=opt("--accepted-by") or "",
                         pair="--pair" in a, plan=plan_opt)
    if opt("--words") is not None:
        rec["owner_words"] = str(opt("--words"))[:2000]                        # his answer verbatim, beside what was made of it
    lines = read + lines; rec["lines"] = lines
    if rec["verdict"] == "READY":
        if rec.get("pair") is True:
            # the proof pair spends from the shared root its launcher freezes, so its two run budgets are a shared grant
            rec["spend_at_start"] = spend_now()
            g = write_grant(rec["budget_usd"], rec["deadline"], rec["spend_at_start"], runs=2)
            lines.append("GRANT    %s" % ("pair budget written: %s" % g if g else "NOT WRITTEN: the money figure or the deadline could not be read"))
        else:
            # A PLAIN RUN'S MONEY IS ITS OWN (owner, 2026-09-27: "Each run has its own budget and does not carry over"):
            # the driver writes <run dir>/money/run-budget.json at its start from this record's budget and deadline.
            lines.append("BUDGET   %s USD of OpenRouter in the run's own ledger, written by the driver at its start; "
                         "nothing carries over from another run" % rec.get("budget_usd"))
            g = True   # nothing shared to write, so nothing that can fail
        # ROUTING ADVICE FROM THE JOURNAL (P4, 2026-09-24): the newest runs' per model validity and cost, as a proposed
        # worker mix. Advice, never a decision: the owner or the start script sets BROTHER_WORKER_MIX. Best effort: an
        # intake never fails on advice it could not compute, it says NO-DATA.
        def _mix():
            try:
                _adv = subprocess.run([sys.executable, "-B", os.path.join(HERE, "mix_advice.py"), "--runs", "3"], capture_output=True, text=True, timeout=180)
                return next((l for l in (_adv.stdout or "").splitlines() if l.startswith(("ADVICE", "NO-DATA"))), "NO-DATA: mix_advice printed no advice line")
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                return "NO-DATA: mix advice could not run (%s)" % type(exc).__name__
        _mix_t = []
        _line = timed("mix_advice", _mix, _mix_t)
        lines.append("MIX      %s" % _line[:220])
        lines.extend(time_lines(_mix_t))   # after the verdict, like MIX itself: information, never read as one
        rec.setdefault("timings", {}).update({row[0]: row[1] for row in _mix_t})
        # THE PROGRAM RECORD (2026-09-30): rewritten only when the proven programs change, so its mtime is the fact that
        # re-seats a sub unit parked by a configuration fault (runner_pool.fact_time).
        try:
            import model_reachability as MR
            lines.append("PROGRAMS %s %s" % ("changed, record written:" if MR.write_record(rec.get("programs") or {}, proven=rec.get("proven")) else "unchanged:", MR.record_path()))
        except (OSError, ValueError) as exc:
            rec["verdict"] = "NOT READY"; lines.append("INTAKE NOT READY: the program record could not be written (%s)" % exc)
        if not g: rec["verdict"] = "NOT READY"; lines.append("INTAKE NOT READY")
    print("\n".join(lines))
    try:
        d = os.path.join(ev(), "loop-intake"); os.makedirs(d, exist_ok=True)
        for name, text in (("CURRENT.json", json.dumps(rec, indent=1)), ("launch-env.sh", launch_env(rec, roles))):
            tmp = os.path.join(d, name + ".tmp-%d" % os.getpid())
            with open(tmp, "w", encoding="utf-8") as f: f.write(text)
            os.replace(tmp, os.path.join(d, name))
    except OSError as exc:
        print("INTAKE NO-DATA: the record could not be written (%s), so no driver can start on it" % type(exc).__name__); return 3
    return 0 if rec["verdict"] == "READY" else 1


NET_ATTEMPTS = []   # every socket or urlopen the selftest would have made; the guard case reads it


def _reach1():
    """The selftest's one-worker reach row; its model_id is read from the registry row when a case asks, never typed
    here and never at import (FX-31.7)."""
    model_id = model_router.derive_model_views(model_router.registry())["bridge_aliases"]["deepseek"]
    return [{"role": "worker", "transport": "bridge", "model": "deepseek", "model_id": model_id, "program": "bridge",
             "fingerprint": "fixture", "version": "fixture"}]


def selftest():
    """Pins its own environment: no caller pin, no caller switch, and NO NETWORK. In this process socket.socket and
    urllib.request.urlopen are replaced by a recorder that refuses; every child process gets BROTHER_OR_BALANCE_FILE
    pointing at a file that does not exist, so or_balance answers NO-DATA without asking the provider."""
    import socket, urllib.request
    saved = {n: os.environ.pop(n) for n in PIN_NAMES + ("BROTHER_TRANSPORTS", "BROTHER_OR_BALANCE_FILE") if n in os.environ}
    os.environ["BROTHER_OR_BALANCE_FILE"] = os.path.join(tempfile.gettempdir(), "intake-selftest-no-such-balance-%d.json" % os.getpid())
    real = (socket.socket, urllib.request.urlopen)
    def _refuse(*a, **k):
        NET_ATTEMPTS.append(repr(a[:2])); raise OSError("the intake selftest never reaches the network")
    socket.socket, urllib.request.urlopen = _refuse, _refuse
    try: return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1
    finally:
        socket.socket, urllib.request.urlopen = real
        os.environ.pop("BROTHER_OR_BALANCE_FILE", None); os.environ.update(saved)


def _selftest_body():
    reg = {"cheap": {"transport": "bridge", "privacy": "public", "kinds": {"build", "grade"}, "quality": {"build": 5, "grade": 5}},
           "judge": {"transport": "bridge", "privacy": "public", "kinds": {"grade"}, "quality": {"grade": 5}},   # cannot build
           "strong": {"transport": "claude", "privacy": "private", "kinds": {"build", "grade", "plan", "prose"}, "quality": {"build": 9, "grade": 9}}}
    base = {"does": "x", "when": "inside", "kind": "build", "content": "public", "must_be_chosen": False, "default": "cheap"}
    roles = {"worker": dict(base, setting="BROTHER_PIN_MODEL"), "checker": dict(base, kind="grade", content="private", default="strong", setting="BROTHER_CHECKER", mode_setting="BROTHER_CHECKER_MODE"),
             "finisher": dict(base, when="after_run", content="private", must_be_chosen=True, default=None)}
    noon = datetime.datetime(2026, 1, 1, 12, 0)
    _ok_reach = lambda plan: [{"status": "OK", "role": r, "model": m, "program": "/p", "version": "1.0", "cause": "fixture", "cached": False, "binding": None} for r, m in plan]
    good = {"reach": _ok_reach, "canary": lambda: ("OK", "went end to end"), "alive": lambda: ("OK", "nothing alive"), "lease": lambda: ("OK", "free"), "parity": lambda: ("OK", "equal"), "switch": lambda: ("OK", "unset"), "tree": lambda: ("OK", "clean"), "digest": lambda: ("OK", "answers"), "done": lambda: ("OK", "answers"), "salvage": lambda: ("OK", "answers"), "pool": lambda: ("OK", "answers"), "lint": lambda: ("OK", "clean"), "headroom": lambda: 50.0, "hold": lambda: False, "now": lambda: noon}
    def run(probes=None, choices=None, deadline="18:00", budget=10.0, scope=None):
        return prepare({"finisher": "strong"} if choices is None else choices, deadline, budget, scope, roles, reg, dict(good, **(probes or {})))
    body = lambda rec: [l for l in rec["lines"] if l.startswith(("REFUSED", "NO-DATA"))]
    only = lambda rec, text: len(body(rec)) == 1 and text in body(rec)[0]      # ONE condition per fixture: everything else is healthy
    cases = [("with everything healthy the verdict is READY", body(run()[0]) == [] and run()[0]["verdict"] == "READY"),
             ("a canary that does not exist yet is ADVISABLE: the user is asked", run(probes={"canary": lambda: ("NO-DATA", "no canary yet")})[0]["verdict"] == "NEEDS YOUR DECISION"),
             ("a canary that RAN AND FAILED is required, and a waiver does not reach it", prepare({"finisher": "strong"}, "18:00", 10.0, None, roles, reg, dict(good, canary=lambda: ("REFUSED", "stopped at the grader")), accept=("canary",), accepted_by="owner")[0]["verdict"] == "NOT READY"),
             ("a role INSIDE the run that is refused is REQUIRED: NOT READY, and no waiver changes it", run(choices={"finisher": "strong", "worker": "judge"})[0]["verdict"] == "NOT READY" and prepare({"finisher": "strong", "worker": "judge"}, "18:00", 10.0, None, roles, reg, good, accept=("worker",), accepted_by="x")[0]["verdict"] == "NOT READY"),
             ("no finisher chosen is ADVISABLE: the user is asked, with the risk and the words to continue", run(choices={})[0]["verdict"] == "NEEDS YOUR DECISION" and any(l.startswith("ASK") and "--accept finisher" in l and "RISK:" in l for l in run(choices={})[0]["lines"])),
             ("accepted by name and signed, it is WAIVED, READY, and the record keeps who", (lambda r: r["verdict"] == "READY" and r["waived"] == ["finisher"] and r["accepted_by"] == "owner: go")(prepare({}, "18:00", 10.0, None, roles, reg, good, accept=("finisher",), accepted_by="owner: go")[0])),
             ("a waiver nobody signed is refused, and nothing is printed as WAIVED", (lambda r: r["verdict"] == "NOT READY" and r["waived"] == [] and not any(l.startswith("WAIVED") for l in r["lines"]))(prepare({}, "18:00", 10.0, None, roles, reg, good, accept=("finisher",), accepted_by=" ")[0])),
             ("an INSIDE role refused through its DEFAULT, nothing chosen, is still required and cannot be waived", prepare({"finisher": "strong"}, "18:00", 10.0, None, dict(roles, worker=dict(roles["worker"], default="judge")), reg, good, accept=("worker",), accepted_by="owner")[0]["verdict"] == "NOT READY"),
             ("accepting the WRONG key waives nothing", prepare({}, "18:00", 10.0, None, roles, reg, good, accept=("canary",), accepted_by="owner")[0]["verdict"] == "NEEDS YOUR DECISION"),
             ("the owner's hold alone makes it HELD, never READY", run(probes={"hold": lambda: True})[0]["verdict"] == "HELD"),
             ("the question about a missing finisher comes WITH the models that qualify", any(l.startswith("ASK") and "HINT: models that qualify for finisher: strong, cheap" in l for l in run(choices={})[0]["lines"])),
             ("a deadline that is not HH:MM is refused with an example", only(run(deadline="6pm")[0], "is not HH:MM") and "HINT" in body(run(deadline="6pm")[0])[0]),
             ("a deadline in the past is refused", only(run(deadline="09:00")[0], "not in the future")),
             ("a missing budget is refused with an example", only(run(budget=None)[0], "not a positive number")),
             ("a budget above the old grant's headroom is NOT refused: the run writes its own grant", run(budget=80.0)[0]["verdict"] == "READY" and any("budget 80.00 USD for this run" in l for l in run(budget=80.0)[0]["lines"])),
             ("unreadable money is NO-DATA, never OK", only(run(probes={"headroom": lambda: None})[0], "could not be read")),
             ("a bad scope is refused", only(run(scope="(")[0], "not a regular expression")),
             ("something alive is refused with the command that stops it", only(run(probes={"alive": lambda: ("REFUSED", "3 alive")})[0], "3 alive") and "stop_loop.sh" in body(run(probes={"alive": lambda: ("REFUSED", "3 alive")})[0])[0]),
             ("a held lease is refused", only(run(probes={"lease": lambda: ("REFUSED", "HELD by pid 1")})[0], "HELD by pid 1")),
             ("tool drift is refused", only(run(probes={"parity": lambda: ("REFUSED", "differ")})[0], "differ")),
             ("a dirty or unpushed launch tree is refused, with the way out", only(run(probes={"tree": lambda: ("REFUSED", "2 changed path(s)")})[0], "2 changed path(s)") and "move the files out" in body(run(probes={"tree": lambda: ("REFUSED", "2 changed path(s)")})[0])[0]),
             ("a tree git cannot report is NO-DATA, never READY", only(run(probes={"tree": lambda: ("NO-DATA", "git could not report")})[0], "git could not report") and run(probes={"tree": lambda: ("NO-DATA", "git could not report")})[0]["verdict"] == "NOT READY"),
             ("a crashing pass digest is refused with its last line", only(run(probes={"digest": lambda: ("REFUSED", "pass_digest.py exits 1: TypeError")})[0], "TypeError")),
             ("a digest that cannot run is NO-DATA, never READY", run(probes={"digest": lambda: ("NO-DATA", "could not run")})[0]["verdict"] == "NOT READY"),
             ("each of the other three pass tools crashing is refused on its own, with the fix named", all(only(run(probes={k: lambda: ("REFUSED", "%s exits 1: Traceback" % k)})[0], "Traceback") and ("fix %s first" % t) in body(run(probes={k: lambda: ("REFUSED", "x")})[0])[0] for k, t in (("done", "loop_done.py"), ("salvage", "salvage.py"), ("pool", "runner_pool.py")))),
             ("the real pass_tool probe: a documented non zero exit is OK, an undocumented one or a traceback is REFUSED, a missing tool is NO-DATA",
              pass_tool("loop_intake.py", ["guide"], (0, 1))()[0] == "OK" and pass_tool("loop_intake.py", ["guide"], (1,))()[0] == "REFUSED" and pass_tool("no-such-tool.py", [], (0,))()[0] == "NO-DATA"),
             ("a dirty tree cannot be waived", prepare({"finisher": "strong"}, "18:00", 10.0, None, roles, reg, dict(good, tree=lambda: ("REFUSED", "dirty")), accept=("tree",), accepted_by="owner")[0]["verdict"] == "NOT READY"),
             ("the owner's hold is reported and is its own line", any("HOLD is IN FORCE" in l for l in run(probes={"hold": lambda: True})[0]["lines"]))]
    ready = {"verdict": "READY", "epoch": 1000.0, "reach": _reach1()}
    cases += [("a fresh READY record is startable", startable(ready, now=1000.0 + 3600) == ""),
              ("transports_match: record and live agree, both unset or both claude", transports_match({"transports": None}, {}) == "" and transports_match({"transports": "claude"}, {"BROTHER_TRANSPORTS": "CLAUDE"}) == ""),
              ("transports_match: a record prepared for claude refuses a driver whose environment is unset", "prepared under transports claude" in transports_match({"transports": "claude"}, {})),
              ("transports_match: a driver under claude refuses a record prepared with none", "unset" in transports_match({"transports": None}, {"BROTHER_TRANSPORTS": "claude"}) and transports_match({"transports": None}, {"BROTHER_TRANSPORTS": "claude"}) != ""),
              ("transports_match: a malformed live value refuses", "not a transport allowlist" in transports_match({"transports": "claude"}, {"BROTHER_TRANSPORTS": "claude,-bridge"})),
              ("a malformed BROTHER_TRANSPORTS makes the intake NOT READY and the record carries none", (lambda r: r["verdict"] == "NOT READY" and r["transports"] is None)(prepare({"finisher": "strong"}, "18:00", 10.0, None, roles, reg, good, env={"BROTHER_TRANSPORTS": "claude,-bridge"})[0])),
              ("the record carries the parser's canonical text, case normalized", prepare({"finisher": "strong"}, "18:00", 10.0, None, roles, reg, good, env={"BROTHER_TRANSPORTS": " Codex,CLAUDE "})[0]["transports"] == "claude,codex"),
              ("launch_env never sanitizes: a record value that does not parse exports nothing", "export BROTHER_TRANSPORTS" not in launch_env({"at": "t", "verdict": "READY", "roles": {}, "transports": "claude,-bridge"}, roles)
               and "export BROTHER_TRANSPORTS" not in launch_env({"at": "t", "verdict": "READY", "roles": {}, "transports": "brid ge"}, roles)),
              ("a record past the age limit is not", "old" in startable(ready, now=1000.0 + 7 * 3600)),
              ("a record from the future is not", "old" in startable(ready, now=900.0 - 3600)),
              ("a HELD record is not startable", "HELD" in startable({"verdict": "HELD", "epoch": 1000.0}, now=1001.0)),
              ("no record is not startable", startable(None) != ""),
              ("a record with no time is not startable", "no readable time" in startable({"verdict": "READY"}, now=1.0))]
    # THE PAIR RECORD (U1, objection 6): one record covers RB and RC; coverage, not age, decides, per phase.
    t0 = 2000000000.0
    pr = lambda hours: {"verdict": "READY", "epoch": t0, "pair": True, "reach": _reach1(),
                        "pair_until": datetime.datetime.fromtimestamp(t0 + hours * 3600, datetime.timezone.utc).isoformat()}
    cases += [("a pair record covering RB's whole pair starts RB", startable(pr(16.4), now=t0, phase="RB") == ""),
              ("a pair record short of RB's pair (2 x (8 h + 180 s) + 900 s) refuses RB, saying so", "cover" in startable(pr(16.3), now=t0, phase="RB")),
              ("a pair record covering RC's run starts RC", startable(pr(8.2), now=t0, phase="RC") == ""),
              ("a pair record short of RC's run (8 h + 180 s + 300 s) refuses RC", "cover" in startable(pr(8.1), now=t0, phase="RC")),
              ("a pair record starts RC however old it is while it covers RC (15 h after the intake)", startable(pr(24.0), now=t0 + 15 * 3600, phase="RC") == ""),
              ("a pair record outside a proof phase is held to RB's coverage", "cover" in startable(pr(16.3), now=t0) and startable(pr(16.4), now=t0) == ""),
              ("a proof phase never starts on a non pair record", "pair" in startable({"verdict": "READY", "epoch": t0}, now=t0 + 60, phase="RB")),
              ("a pair record whose pair_until is not an aware time refuses", "pair_until" in startable(dict(pr(20), pair_until="tomorrow"), now=t0, phase="RB")
               and "pair_until" in startable(dict(pr(20), pair_until="2030-01-01T00:00:00"), now=t0, phase="RB")),
              ("a pair record that is not READY refuses", "HELD" in startable(dict(pr(20), verdict="HELD"), now=t0, phase="RB"))]
    # running_figures (audit E1): what a RUNNING driver reads. One condition per case; the start rules never gate it.
    late = dict(pr(10), budget_usd=0.5, deadline="23:59")                      # covers 10 h: short of RB's start rule
    cases += [("running_figures: a pair record that no longer covers the pair still gives a running RB its budget and deadline",
               startable(late, now=t0, phase="RB") != "" and running_figures(late, phase="RB") == ("", 0.5, "23:59")),
              ("running_figures: a plain record past the start age still gives its figures", running_figures({"verdict": "READY", "epoch": 1.0, "budget_usd": 2, "deadline": "18:00"}) == ("", 2.0, "18:00")),
              ("running_figures: no record is unreadable", running_figures(None)[0] != "" and running_figures(None)[1:] == (None, None)),
              ("running_figures: a record that is not READY is unreadable", "HELD" in running_figures(dict(late, verdict="HELD"), phase="RB")[0]),
              ("running_figures: a proof phase on a non pair record is unreadable, outside a proof it is read",
               "pair" in running_figures({"verdict": "READY", "budget_usd": 1.0}, phase="RC")[0] and running_figures({"verdict": "READY", "budget_usd": 1.0})[0] == ""),
              ("running_figures: a budget that is not a positive number is unreadable (text, zero, negative, bool, missing, infinite, NaN)",
               all(running_figures(dict(late, budget_usd=v), phase="RB")[1] is None and "budget" in running_figures(dict(late, budget_usd=v), phase="RB")[0]
                   for v in ("0.5", 0, -1.0, True, None, float("inf"), float("nan")))),
              ("running_figures: a missing or blank deadline gives the budget and no deadline",
               running_figures(dict(late, deadline=None), phase="RB") == ("", 0.5, None) and running_figures(dict(late, deadline=" "), phase="RB") == ("", 0.5, None))]
    _pair_now = datetime.datetime.now()
    _near = (_pair_now + datetime.timedelta(hours=10)).strftime("%Y-%m-%d %H:%M")
    _far = (_pair_now + datetime.timedelta(hours=20)).strftime("%Y-%m-%d %H:%M")
    _p_far = prepare({"finisher": "strong"}, _far, 10.0, None, roles, reg, dict(good, now=lambda: _pair_now), pair=True)[0]
    _p_near = prepare({"finisher": "strong"}, _near, 10.0, None, roles, reg, dict(good, now=lambda: _pair_now), pair=True)[0]
    cases += [("prepare --pair writes pair: true and an aware pair_until equal to the deadline", _p_far["verdict"] == "READY" and _p_far.get("pair") is True
               and datetime.datetime.fromisoformat(_p_far["pair_until"]).tzinfo is not None
               and datetime.datetime.fromisoformat(_p_far["pair_until"]).strftime("%Y-%m-%d %H:%M") == _far),
              ("prepare --pair refuses a deadline that cannot cover the pair", _p_near["verdict"] == "NOT READY" and any("cover" in l for l in _p_near["lines"] if l.startswith("REFUSED"))),
              ("prepare without --pair writes no pair field", "pair" not in run()[0] and "pair_until" not in run()[0])]
    import tempfile
    _bin = tempfile.mkdtemp(prefix="intake-switch-")
    _old = os.path.join(_bin, "old"); _new = os.path.join(_bin, "new"); os.makedirs(_old); os.makedirs(_new)
    with open(os.path.join(_old, "model_router.py"), "w") as _f: _f.write("def registry(): pass\n")
    with open(os.path.join(_new, "model_router.py"), "w") as _f: _f.write("def transports_allowed(env=None): pass\n")
    _jok = os.path.join(_bin, "jev_ok.py"); _jno = os.path.join(_bin, "jev_no.py")
    with open(_jok, "w") as _f: _f.write("def bridge_refused(env=None): pass\n")
    with open(_jno, "w") as _f: _f.write("def decide(): pass\n")
    _sw = dict(good, switch=lambda: deployed_switch({"BROTHER_TRANSPORTS": "claude"}, _old, _jok))
    cases += [("with BROTHER_TRANSPORTS unset the deployed switch probe is OK whatever the bin holds", deployed_switch({}, _old)[0] == "OK" and deployed_switch({"BROTHER_TRANSPORTS": " "}, os.path.join(_bin, "none"))[0] == "OK"),
              ("BROTHER_TRANSPORTS set and the deployed model router without the allowlist is REFUSED naming deploy", deployed_switch({"BROTHER_TRANSPORTS": "claude"}, _old, _jok) == ("REFUSED", "BROTHER_TRANSPORTS=claude is set but the deployed model router %s has no transport allowlist: deploy first, or every pass ignores the switch" % os.path.join(_old, "model_router.py"))),
              ("BROTHER_TRANSPORTS set and no deployed model router at all is REFUSED", deployed_switch({"BROTHER_TRANSPORTS": "claude"}, os.path.join(_bin, "none"), _jok)[0] == "REFUSED"),
              ("BROTHER_TRANSPORTS set and the deployed model router carrying the allowlist is OK", deployed_switch({"BROTHER_TRANSPORTS": "claude"}, _new, _jok)[0] == "OK"),
              ("BROTHER_TRANSPORTS set and the tree's Jev entry without its bridge gate is REFUSED naming it", deployed_switch({"BROTHER_TRANSPORTS": "claude"}, _new, _jno)[0] == "REFUSED" and "Jev entry" in deployed_switch({"BROTHER_TRANSPORTS": "claude"}, _new, _jno)[1]
               and deployed_switch({"BROTHER_TRANSPORTS": "claude"}, _new, os.path.join(_bin, "absent.py"))[0] == "REFUSED"),
              ("the real tree's Jev entry carries the gate", deployed_switch({"BROTHER_TRANSPORTS": "claude"}, _new)[0] == "OK"),
              ("deployed_switch refuses a malformed value instead of reading a substring", deployed_switch({"BROTHER_TRANSPORTS": "claude,!bridge"}, _new, _jok)[0] == "REFUSED" and "not a transport allowlist" in deployed_switch({"BROTHER_TRANSPORTS": "claude,!bridge"}, _new, _jok)[1]),
              ("a refused deployed switch makes the intake NOT READY, and no waiver reaches it", prepare({"finisher": "strong"}, "18:00", 10.0, None, roles, reg, _sw)[0]["verdict"] == "NOT READY"
               and prepare({"finisher": "strong"}, "18:00", 10.0, None, roles, reg, _sw, accept=("switch",), accepted_by="owner")[0]["verdict"] == "NOT READY"),
              ("the record carries the transports the intake ran under, and the launch settings export it", prepare({"finisher": "strong"}, "18:00", 10.0, None, roles, reg, good, env={"BROTHER_TRANSPORTS": "claude"})[0]["transports"] == "claude"
               and "export BROTHER_TRANSPORTS=claude" in launch_env({"at": "t", "verdict": "READY", "roles": {}, "transports": "claude"}, roles)
               and "BROTHER_TRANSPORTS" not in launch_env({"at": "t", "verdict": "READY", "roles": {}, "transports": None}, roles))]
    env = launch_env({"at": "t", "verdict": "READY", "roles": {"worker": "cheap", "checker": "strong", "finisher": "strong"}, "scope": "^D"}, roles, authority=lambda j: "NO-DATA")
    _rec = {"at": "t", "verdict": "READY", "roles": {"worker": "cheap", "checker": "sonnet", "finisher": "strong"}, "scope": "^D"}
    def _boom(j): raise OSError("calibration unreadable")
    cases += [("a checker the calibrator granted gate runs as gate (owner 2026-09-23: authority earned by measurement)",
               "export BROTHER_CHECKER_MODE=gate" in launch_env(_rec, roles, authority=lambda j: "gate" if j == "checker:sonnet" else "shadow")),
              ("a checker the calibrator did not grant is off", "export BROTHER_CHECKER_MODE=off" in launch_env(_rec, roles, authority=lambda j: "shadow")),
              ("an unreadable calibration turns the checker off, never gate", "export BROTHER_CHECKER_MODE=off" in launch_env(_rec, roles, authority=_boom))]
    cases += [("the launch settings carry each role that has a setting, and an ungranted checker off", "export BROTHER_PIN_MODEL=cheap" in env and "export BROTHER_CHECKER=strong" in env and "export BROTHER_CHECKER_MODE=off" in env and "BROTHER_SCOPE='^D'" in env),
              ("a role with no setting is not exported", "finisher" not in env.replace("# written", "")),
              ("with no pins the launch settings are exactly the role and scope lines", env == launch_env({"at": "t", "verdict": "READY", "roles": {"worker": "cheap", "checker": "strong", "finisher": "strong"}, "scope": "^D", "pins": {}}, roles)
               and "BIN" not in env)]
    import tempfile
    _pd = tempfile.mkdtemp(prefix="loop-intake-pins-")
    try:
        _dir = os.path.join(_pd, "Claude Code"); os.makedirs(_dir)
        _exe = os.path.join(_dir, "claude"); _plain = os.path.join(_dir, "notexec"); _quoted = os.path.join(_dir, "cl'aude")   # real, executable: only the quote guard can refuse it
        for _f, _mode in ((_exe, 0o755), (_plain, 0o644), (_quoted, 0o755)):
            with open(_f, "w", encoding="utf-8") as fh: fh.write("#!/bin/sh\n")
            os.chmod(_f, _mode)
        pin_run = lambda pinenv, **kw: prepare({"finisher": "strong"}, "18:00", 10.0, None, roles, reg, good, env=pinenv, **kw)
        _ok = pin_run({"BROTHER_CLAUDE_BIN": _exe})[0]
        _pinenv = launch_env(_ok, roles)
        _all = dict(accept=("finisher", "canary", "worker", "checker", "tree"), accepted_by="owner")
        _bad = {"missing file": os.path.join(_dir, "no-such-file"), "non executable file": _plain, "relative path": "bin/claude", "quote in path": _quoted, "a directory": _dir}
        _badrec = {k: pin_run({"BROTHER_CODEX_BIN": v}, **_all)[0] for k, v in _bad.items()}
        cases += [("a pin with a space in its path is recorded, printed as OK, and exported single quoted", _ok["verdict"] == "READY" and _ok["pins"] == {"BROTHER_CLAUDE_BIN": _exe}
                   and any(l.startswith("OK") and "BROTHER_CLAUDE_BIN" in l for l in _ok["lines"]) and ("export BROTHER_CLAUDE_BIN='%s'" % _exe) in _pinenv.splitlines()),
                  ("a pin that does not name an executable: a missing file is REQUIRED, NOT READY, naming the variable, whatever is accepted",
                   _badrec["missing file"]["verdict"] == "NOT READY" and any(l.startswith("REFUSED") and "a pin that does not name an executable" in l and "BROTHER_CODEX_BIN" in l and "HINT" in l for l in _badrec["missing file"]["lines"])),
                  ("a pin on a file that is not executable is REQUIRED, NOT READY, even with everything accepted", _badrec["non executable file"]["verdict"] == "NOT READY" and any("not executable" in l for l in _badrec["non executable file"]["lines"])),
                  ("a pin that is a relative path is REQUIRED, NOT READY, even with everything accepted", _badrec["relative path"]["verdict"] == "NOT READY" and any("not an absolute path" in l for l in _badrec["relative path"]["lines"])),
                  ("a pin with a single quote or naming a directory is REQUIRED, NOT READY", all(_badrec[k]["verdict"] == "NOT READY" for k in ("quote in path", "a directory"))),
                  ("a refused pin is not recorded as a pin and is not exported", all(r["pins"] == {} and "BROTHER_CODEX_BIN" not in launch_env(r, roles) for r in _badrec.values())),
                  ("an unset or empty pin changes nothing: READY, no pins, no PIN line, launch settings as without", all(r["verdict"] == "READY" and r["pins"] == {} and not any("pin " in l for l in r["lines"]) and launch_env(r, roles) == launch_env(dict(r, pins={}), roles)
                                                                                                                    for r in (pin_run({})[0], pin_run({"BROTHER_CLAUDE_BIN": "", "BROTHER_CODEX_BIN": ""})[0]))),
                  ("prepare reads os.environ when no environment is injected", (lambda: (os.environ.__setitem__("BROTHER_CODEX_BIN", _exe), run()[0])[1])()["pins"] == {"BROTHER_CODEX_BIN": _exe})]
        os.environ.pop("BROTHER_CODEX_BIN", None)
    finally:
        shutil.rmtree(_pd, ignore_errors=True)
    g = "\n".join(guide(roles, reg))
    cases += [("the guide names every role, the models that qualify for each, and a ready to edit example", all(r.upper() in g for r in roles) and "models that qualify right now: strong\n" in g and "prepare finisher=strong" in g),
              ("the suggestion is the best model for the kind, not the first in the file", qualifying("finisher", roles["finisher"], reg) == ["strong", "cheap"] and qualifying("worker", roles["worker"], reg) == ["strong", "cheap"])]
    import tempfile
    d = tempfile.mkdtemp(prefix="loop-intake-"); me = os.path.abspath(__file__)
    e = dict(os.environ, INTAKE_EVIDENCE=d, INTAKE_PROBES="stub")
    st0 = subprocess.run([sys.executable, "-B", me, "status"], capture_output=True, text=True, env=e)
    gd = subprocess.run([sys.executable, "-B", me, "guide"], capture_output=True, text=True, env=e)
    hp = subprocess.run([sys.executable, "-B", me, "--help"], capture_output=True, text=True, env=e)
    bad = subprocess.run([sys.executable, "-B", me, "prepar", "--deadline", "00:00"], capture_output=True, text=True, env=e)
    rec_path = os.path.join(d, "loop-intake", "CURRENT.json")
    cases += [("the entry point: --help prints the guide, exit 0, and writes NO record", hp.returncode == 0 and "FINISHER" in hp.stdout and not os.path.exists(rec_path)),
              ("the entry point: an unknown verb is NO-DATA, exit 2, and writes NO record", bad.returncode == 2 and "unknown verb" in bad.stdout and not os.path.exists(rec_path))]
    pr = subprocess.run([sys.executable, "-B", me, "prepare", "--deadline", "00:00", "--budget-usd", "1"], capture_output=True, text=True, env=e)
    with open(rec_path, "w", encoding="utf-8") as f:
        json.dump({"verdict": "READY", "epoch": time.time(), "at": "t", "reach": _reach1(), "budget_usd": 12.5}, f)
    st1 = subprocess.run([sys.executable, "-B", me, "status"], capture_output=True, text=True, env=e)
    cases += [("the entry point: status on a READY record prints the budget line the driver reads", st1.returncode == 0 and "BUDGET_USD 12.50" in st1.stdout.splitlines())]
    pr = subprocess.run([sys.executable, "-B", me, "prepare", "--deadline", "00:00", "--budget-usd", "1"], capture_output=True, text=True, env=e)
    cases += [("the entry point: status with no record says NO START, exit 1", st0.returncode == 1 and "NO START" in st0.stdout),
              ("the entry point: guide prints the real roles with qualifying models, exit 0", gd.returncode == 0 and "FINISHER" in gd.stdout and "models that qualify right now" in gd.stdout),
              ("the entry point: a prepare that is refused still writes the record, exit 1, verdict NOT READY", pr.returncode == 1 and "INTAKE NOT READY" in pr.stdout and json.load(open(os.path.join(d, "loop-intake", "CURRENT.json")))["verdict"] == "NOT READY" and os.path.isfile(os.path.join(d, "loop-intake", "launch-env.sh")))]
    fixed = datetime.datetime(2026, 1, 1, 13, 0)
    cases += [("read_budget: plain, decimal, dollar sign, USD suffix and prefix, decimal comma, thousands comma",
               [read_budget(x) for x in ("80", "80.5", "$80", "80USD", "80 usd", "USD 80", "80,5", "1,000", " 12 dollars ")] == [80.0, 80.5, 80.0, 80.0, 80.0, 80.0, 80.5, 1000.0, 12.0]),
              ("read_budget: words, nan, inf, empty, two numbers, a bool, None are None",
               all(read_budget(x) is None for x in ("eighty", "nan", "inf", "", "80 or 90", True, None, "-5x", float("nan"), float("inf"), float("-inf")))),
              ("read_deadline: HH:MM, with a zone, 12 hour, h suffix, four digits, dotted, with filler words",
               [read_deadline(x, fixed) for x in ("18:00", "18:00 JST", "6pm", "6 PM", "6:30pm", "18h", "18h30", "1800", "18.00", "at 18:00", "until 6pm", "12am", "12pm")]
               == ["18:00", "18:00", "18:00", "18:00", "18:30", "18:00", "18:30", "18:00", "18:00", "18:00", "18:00", "00:00", "12:00"]),
              ("read_deadline: relative forms count from now and stay on today", [read_deadline(x, fixed) for x in ("in 4 hours", "+90m", "4h from now", "in 30 min")] == ["17:00", "14:30", "17:00", "13:30"]),
              ("read_deadline: an impossible time, a weekday, words and empty are None", all(read_deadline(x, fixed) is None for x in ("25:00", "18:60", "soon", "", "monday 09:00"))),
              ("read_deadline: another day comes back dated, today's stays HH:MM", read_deadline("tomorrow 07:00", fixed) == (fixed + datetime.timedelta(days=1)).strftime("%Y-%m-%d") + " 07:00"
               and read_deadline("tomorrow", fixed) == (fixed + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M") and read_deadline("2026-01-02 09:00", fixed) == "2026-01-02 09:00"
               and read_deadline("in 20 hours", fixed) == (fixed + datetime.timedelta(hours=20)).strftime("%Y-%m-%d %H:%M") and "-" not in read_deadline("in 2 hours", fixed)),
              ("a deadline more than 48 h away is refused by prepare", run(deadline=(datetime.datetime.now() + datetime.timedelta(hours=60)).strftime("%Y-%m-%d %H:%M"))[0]["verdict"] == "NOT READY"),
              ("deadline_dt: reads both shapes, refuses junk", deadline_dt("18:00", fixed).hour == 18 and deadline_dt("2026-01-02 09:00", fixed).day == 2 and deadline_dt("soon", fixed) is None and deadline_dt("2026-13-01 09:00", fixed) is None),
              ("read_model: case, spaces, a parenthetical, an effort suffix and aliases are absorbed",
               [read_model(x) for x in ("DeepSeek", " deepseek at xhigh ", "astra (Codex)", "Deep Seek", "GPT-6 Astra", "claude opus", "opus.")] == ["deepseek", "deepseek", "astra", "deepseek", "astra", "opus", "opus"]),
              ("read_model: none and its kin mean no choice, unknown names pass through as typed", [read_model(x) for x in ("none", "skip", "N/A", "", "gemini")] == ["", "", "", "", "gemini"])]
    wd = subprocess.run([sys.executable, "-B", me, "prepare", "finisher=DeepSeek at xhigh", "--deadline", "6pm", "--budget-usd", "80USD", "--words", "80USD 18:00, deepseek"], capture_output=True, text=True, env=e)
    with open(rec_path) as f: wrec = json.load(f)
    cases += [("the stub probes can never make a record READY, whatever else is right", wrec["verdict"] == "NOT READY" and sum("not probed" in l for l in wrec["lines"]) == 11 and not any(l.startswith("OK") and "not probed" in l for l in wrec["lines"])),
              ("the entry point: his words are read, each reading is echoed as a READ line, and the record keeps them verbatim",
               "READ     budget '80USD' understood as 80.00 USD" in wd.stdout and "READ     deadline '6pm' understood as 18:00 today" in wd.stdout
               and "READ     finisher 'DeepSeek at xhigh' understood as deepseek" in wd.stdout and wrec.get("owner_words") == "80USD 18:00, deepseek" and wrec["deadline"] == "18:00" and wrec["budget_usd"] == 80.0)]
    _root = os.path.join(d, "or-state"); _fixed = datetime.datetime(2026, 1, 1, 13, 0).astimezone()
    _g = write_grant(80.0, "22:30", 127.77, now=_fixed, root=_root)
    with open(_g) as f: _grant = json.load(f)
    cases += [("write_grant: cap is spend at start plus the run budget, until is the run deadline with an offset, note names both figures",
               _grant["daily_cap"] == 207.77 and _grant["until"].startswith("2026-01-01T22:30:00") and ("+" in _grant["until"][19:] or "-" in _grant["until"][19:]) and "80.00" in _grant["grant_note"]),
              ("write_grant: an unreadable budget, spend or deadline writes nothing", write_grant(None, "22:30", 1.0, root=_root) is None and write_grant(80.0, "22:30", None, root=_root) is None and write_grant(80.0, "tonight", 1.0, root=_root) is None and write_grant(0, "22:30", 1.0, root=_root) is None)]
    _g2 = write_grant(10.0, "22:30", 127.77, now=_fixed, root=os.path.join(d, "or-state-pair"), runs=2)
    with open(_g2) as f: _grant2 = json.load(f)
    cases += [("write_grant for a pair: the cap is spend at start plus two run budgets, until the pair's end", _grant2["daily_cap"] == 147.77 and _grant2["until"].startswith("2026-01-01T22:30:00"))]
    # the entry point in a proof phase: a READY pair record answers the driver's lines, a non pair record refuses
    _pu = (datetime.datetime.now().astimezone() + datetime.timedelta(hours=20)).replace(microsecond=0)
    with open(rec_path, "w", encoding="utf-8") as f:
        json.dump({"verdict": "READY", "epoch": time.time(), "at": "t", "reach": _reach1(), "budget_usd": 3.0, "deadline": _pu.strftime("%Y-%m-%d %H:%M"), "pair": True, "pair_until": _pu.isoformat()}, f)
    _st_rb = subprocess.run([sys.executable, "-B", me, "status"], capture_output=True, text=True, env=dict(e, BROTHER_PROOF_PHASE="RB"))
    with open(rec_path, "w", encoding="utf-8") as f:
        json.dump({"verdict": "READY", "epoch": time.time(), "at": "t", "budget_usd": 3.0, "deadline": "23:59"}, f)
    _st_np = subprocess.run([sys.executable, "-B", me, "status"], capture_output=True, text=True, env=dict(e, BROTHER_PROOF_PHASE="RB"))
    cases += [("the entry point: status in RB on a covering pair record prints the budget and the pair's deadline", _st_rb.returncode == 0
               and "BUDGET_USD 3.00" in _st_rb.stdout.splitlines() and ("DEADLINE " + _pu.strftime("%Y-%m-%d %H:%M")) in _st_rb.stdout.splitlines()),
              ("the entry point: status in RB on a non pair record is NO START, exit 1", _st_np.returncode == 1 and "NO START" in _st_np.stdout)]
    # THE RUNNING DRIVER'S READING (audit E1, 2026-09-27): seven hours into a pair the record covers 10 h, short of RB's
    # 16.35 h start rule, and the owner has cut the budget to 0.50. status still refuses a START; status --running gives
    # the running driver the cut. One condition per fixture: the record's shape.
    _pu10 = (datetime.datetime.now().astimezone() + datetime.timedelta(hours=10)).replace(microsecond=0)
    _late = {"verdict": "READY", "epoch": time.time() - 7 * 3600, "at": "t", "budget_usd": 0.5, "deadline": "23:59", "pair": True, "pair_until": _pu10.isoformat()}
    _rn = {}
    for _k, _rec, _ph in (("late pair", _late, "RB"), ("late pair RC", _late, "RC"), ("old plain", {"verdict": "READY", "epoch": time.time() - 7 * 3600, "at": "t", "budget_usd": 0.5, "deadline": "23:59"}, None),
                          ("garbage budget", dict(_late, budget_usd="ten"), "RB"), ("zero budget", dict(_late, budget_usd=0), "RB"), ("bool budget", dict(_late, budget_usd=True), "RB"),
                          ("not READY", dict(_late, verdict="HELD"), "RB"), ("plain in RB", {"verdict": "READY", "epoch": time.time(), "at": "t", "budget_usd": 0.5, "deadline": "23:59"}, "RB"),
                          ("no deadline", dict(_late, deadline=None), "RB")):
        with open(rec_path, "w", encoding="utf-8") as f:
            json.dump(_rec, f)
        _pe = dict(e, BROTHER_PROOF_PHASE=_ph) if _ph else {k: v for k, v in e.items() if k != "BROTHER_PROOF_PHASE"}
        _st_running = subprocess.run([sys.executable, "-B", me, "status", "--running"], capture_output=True, text=True, env=_pe)
        _st_start = subprocess.run([sys.executable, "-B", me, "status"], capture_output=True, text=True, env=_pe)
        _rn[_k] = (_st_running, _st_start)
    _lines = lambda k: _rn[k][0].stdout.splitlines()
    cases += [("the entry point: seven hours into a pair, status still refuses a START (the start rule stands)", _rn["late pair"][1].returncode == 1 and "NO START" in _rn["late pair"][1].stdout),
              ("the entry point: seven hours into a pair, status --running gives the running driver the cut budget and the deadline",
               _rn["late pair"][0].returncode == 0 and "BUDGET_USD 0.50" in _lines("late pair") and "DEADLINE 23:59" in _lines("late pair")),
              ("the entry point: status --running in RC reads the same record", _rn["late pair RC"][0].returncode == 0 and "BUDGET_USD 0.50" in _lines("late pair RC")),
              ("the entry point: status --running reads a plain record past the start age", _rn["old plain"][0].returncode == 0 and "BUDGET_USD 0.50" in _lines("old plain")),
              ("the entry point: status --running on a budget that is not a positive number is UNREADABLE, exit 1, no budget line",
               all(_rn[k][0].returncode == 1 and "UNREADABLE" in _rn[k][0].stdout and not any(l.startswith("BUDGET_USD") for l in _lines(k)) for k in ("garbage budget", "zero budget", "bool budget"))),
              ("the entry point: status --running on a record that is not READY is UNREADABLE, exit 1", _rn["not READY"][0].returncode == 1 and "HELD" in _rn["not READY"][0].stdout),
              ("the entry point: status --running in a proof phase on a non pair record is UNREADABLE, exit 1", _rn["plain in RB"][0].returncode == 1 and "pair" in _rn["plain in RB"][0].stdout),
              ("the entry point: status --running with no deadline prints the budget and no DEADLINE line",
               _rn["no deadline"][0].returncode == 0 and "BUDGET_USD 0.50" in _lines("no deadline") and not any(l.startswith("DEADLINE") for l in _lines("no deadline")))]
    _far_arg =(datetime.datetime.now() + datetime.timedelta(hours=20)).strftime("%Y-%m-%d %H:%M")
    _pp = subprocess.run([sys.executable, "-B", me, "prepare", "--deadline", _far_arg, "--budget-usd", "1", "--pair"], capture_output=True, text=True, env=e)
    with open(rec_path) as f: _pprec = json.load(f)
    _or = os.path.join(d, "or-state-budget")
    with open(rec_path, "w", encoding="utf-8") as f:
        json.dump({"verdict": "READY", "epoch": time.time(), "at": "t", "budget_usd": 3.0, "deadline": _far_arg, "spend_at_start": 10.0,
                   "pair": True, "pair_until": _pu.isoformat()}, f)
    _bv = subprocess.run([sys.executable, "-B", me, "budget", "--budget-usd", "2"], capture_output=True, text=True, env=dict(e, BROTHER_OR_STATE_ROOT=_or))
    try:
        with open(os.path.join(_or, "cap-grant.json")) as f: _bvcap = json.load(f).get("daily_cap")
    except (OSError, ValueError):
        _bvcap = None
    cases += [("the entry point: prepare --pair writes a pair record (the stub probes keep it NOT READY)", _pprec.get("pair") is True and _pprec["verdict"] == "NOT READY"
               and datetime.datetime.fromisoformat(_pprec["pair_until"]).strftime("%Y-%m-%d %H:%M") == _far_arg and "pair" not in wrec),
              ("the entry point: a budget change on a pair record grants two run budgets", _bv.returncode == 0 and _bvcap == 14.0)]
    with open(rec_path, "w", encoding="utf-8") as f:   # a READY plain record prepared under claude, asked to start by a driver whose environment is unset
        json.dump({"verdict": "READY", "epoch": time.time(), "at": "t", "reach": _reach1(), "budget_usd": 3.0, "deadline": _far_arg, "transports": "claude"}, f)
    _st_other = subprocess.run([sys.executable, "-B", me, "status"], capture_output=True, text=True, env=e)
    _st_same = subprocess.run([sys.executable, "-B", me, "status"], capture_output=True, text=True, env=dict(e, BROTHER_TRANSPORTS="CLAUDE"))
    cases += [("the entry point: status refuses a record prepared under claude when the driver's environment is unset", _st_other.returncode == 1 and "prepared under transports claude" in _st_other.stdout),
              ("the entry point: status starts the same record when the driver's environment says claude (case blind)", _st_same.returncode == 0 and "a driver may start" in _st_same.stdout),
              ("no socket and no urlopen was attempted by this selftest in this process", NET_ATTEMPTS == [])]
    with open(rec_path, "rb") as f: _before = f.read()
    _bz = subprocess.run([sys.executable, "-B", me, "budget", "--budget-usd", "0"], capture_output=True, text=True, env=dict(e, BROTHER_OR_STATE_ROOT=_or))
    with open(rec_path, "rb") as f: _after = f.read()
    cases += [("the entry point: a budget change to nothing is refused, exit 1, and the record is unchanged", _bz.returncode == 1 and "INTAKE REFUSED" in _bz.stdout and _after == _before)]
    # THE REAL PROBE, on a real clone: a bare remote, a clone tracking it, and the three verdicts it must reach.
    g = lambda *a: subprocess.run(["git"] + list(a), capture_output=True, text=True, timeout=60)
    bare = os.path.join(d, "remote.git"); clone = os.path.join(d, "clone"); loose = os.path.join(d, "loose")
    g("init", "-q", "--bare", "-b", "main", bare); g("clone", "-q", bare, clone)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false"), ("tag.forceSignAnnotated", "false")): g("-C", clone, "config", k, v)
    with open(os.path.join(clone, "a.txt"), "w") as f: f.write("a\n")
    g("-C", clone, "add", "a.txt"); g("-C", clone, "commit", "-q", "-m", "a"); g("-C", clone, "push", "-q", "-u", "origin", "main")
    clean = tree_state(clone)
    with open(os.path.join(clone, "b.txt"), "w") as f: f.write("b\n")
    dirty = tree_state(clone)
    g("-C", clone, "add", "b.txt"); g("-C", clone, "commit", "-q", "-m", "b")
    unpushed = tree_state(clone)
    g("-C", clone, "branch", "--unset-upstream")
    orphan = tree_state(clone)
    os.makedirs(loose)
    cases += [("the real probe: a clean clone equal to its upstream is OK and names the upstream it read", clean == ("OK", "launch tree clean and equal to origin/main")),
              ("the real probe: one changed path is REFUSED and counted", dirty[0] == "REFUSED" and "1 changed path(s) and 0 unpushed" in dirty[1]),
              ("the real probe: one unpushed commit is REFUSED and counted", unpushed[0] == "REFUSED" and "0 changed path(s) and 1 unpushed" in unpushed[1]),
              ("the real probe: no upstream is NO-DATA with the command that sets one", orphan[0] == "NO-DATA" and "--set-upstream-to" in orphan[1]),
              ("the real probe: a directory that is not a checkout is NO-DATA", tree_state(loose)[0] == "NO-DATA")]
    cases += [("a negative headroom is read as a number, never NO-DATA", parse_headroom("MONEY   spent 168.16 of 20.00 USD | headroom -148.16 | 16.8 h") == -148.16),
              ("a positive headroom is read", parse_headroom("headroom 79.62 |") == 79.62),
              ("no headroom figure is None, never a guessed zero", parse_headroom("GRANT expired") is None and parse_headroom(None) is None)]
    global HERE
    _here, _fake = HERE, tempfile.mkdtemp(prefix="intake-headroom-")
    with open(os.path.join(_fake, "burn_guard.py"), "w", encoding="utf-8") as _fh:
        _fh.write('print("GRANT   expired")\nprint("MONEY   spent 168.16 of 20.00 USD | headroom -148.16 | 16.8 h")\n')
    try:
        HERE = _fake; _room = real_probes()["headroom"]()
    finally:
        HERE = _here
    cases += [("the real headroom probe reads a spent out day's negative figure through the burn guard it runs", _room == -148.16)]
    # THE GRANT LEAVES EXACTLY THE RUN'S BUDGET AS HEADROOM, abandoned holds included (measured line, 2026-09-27 14:56).
    with open(os.path.join(_fake, "burn_guard.py"), "w", encoding="utf-8") as _fh:
        _fh.write('print("MONEY   spent 268.99 + 104.86 unresolved (abandoned) of 368.99 USD | headroom -4.86 | 8.1 h to 23:00")\n')
    try:
        HERE = _fake; _basis = spend_now()
    finally:
        HERE = _here
    _gr = write_grant(100.0, "22:30", _basis, now=_fixed, root=os.path.join(d, "or-state-liability"))
    try:
        with open(_gr) as f: _lcap = json.load(f).get("daily_cap")
    except (OSError, TypeError, ValueError):
        _lcap = None
    cases += [("the grant's basis counts abandoned holds: spent 268.99 + 104.86 unresolved is 373.85", _basis == 373.85),
              ("after the grant the burn guard's headroom is the run's budget, 100.00", _lcap is not None and round(_lcap - 268.99 - 104.86, 2) == 100.0),
              ("a reserved liability counts the same way", committed_of("MONEY   spent 1.50000000 + 0.25000000 reserved of 10.00000000 USD | headroom 8.25000000") == 1.75),
              ("a MONEY line with no ceiling and headroom is None, never a guessed basis", committed_of("MONEY   spent 5.00") is None and committed_of(None) is None)]
    # THE PLAN LINT (FX-13.5): report mode prints and never changes the verdict; block mode asks and takes a signed waiver.
    _lint = lambda answer, env, **kw: prepare({"finisher": "strong"}, "18:00", 10.0, None, roles, reg, dict(good, lint=lambda: answer), env=env, **kw)[0]
    _block = {"BROTHER_RUNFLOW_LINT": "block"}
    _found = ("FINDINGS", "LINT SUMMARY blocking 2 advisory 0 no-data 0 | units 3 sections 4 | scope . | not run: none")
    cases += [("report mode: lint findings are one LINT line and the verdict stays READY", (lambda r: r["verdict"] == "READY" and ("LINT     FINDINGS: %s (report mode: information, the verdict is unchanged)" % _found[1]) in r["lines"])(_lint(_found, {}))),
              ("report mode: a probe dict with no lint says so and the verdict stays READY", (lambda r: r["verdict"] == "READY" and "LINT     NO-DATA: no lint probe given" in r["lines"])(
                  prepare({"finisher": "strong"}, "18:00", 10.0, None, roles, reg, {k: v for k, v in good.items() if k != "lint"}, env={})[0])),
              ("block mode: lint findings or no data ask, naming --accept lint", all(_lint(a, _block)["verdict"] == "NEEDS YOUR DECISION" and any(l.startswith("ASK") and "--accept lint" in l for l in _lint(a, _block)["lines"])
                                                                                   for a in (_found, ("NO-DATA", "plan_lint.py exits 3")))),
              ("block mode: a signed waiver of lint is READY and kept in the record", (lambda r: r["verdict"] == "READY" and r["waived"] == ["lint"])(_lint(_found, _block, accept=("lint",), accepted_by="owner"))),
              ("block mode: a clean lint is an OK line and READY", (lambda r: r["verdict"] == "READY" and any(l.startswith("OK") and "lint OK: clean" in l for l in r["lines"]))(_lint(("OK", "clean"), _block))),
              ("an unknown BROTHER_RUNFLOW_LINT is block, never off, and the line names it", (lambda r: r["verdict"] == "NEEDS YOUR DECISION" and any("BROTHER_RUNFLOW_LINT=blok" in l for l in r["lines"]))(_lint(_found, {"BROTHER_RUNFLOW_LINT": "blok"}))),
              ("the lint CLI's answer: exit 0 blocking 0 is OK, blocking above 0 FINDINGS, exit 3 or a traceback NO-DATA",
               lint_answer(0, "LINT SUMMARY blocking 0 advisory 1 no-data 0 | x", "")[0] == "OK" and lint_answer(0, _found[1], "") == _found
               and lint_answer(3, "LINT SUMMARY blocking 0 advisory 0 no-data 1 | x", "")[0] == "NO-DATA" and lint_answer(1, "", "Traceback (most recent call last):\nKeyError: 'x'") == ("NO-DATA", "KeyError: 'x'")),
              ("the stub's lint is NO-DATA, so a stub record can never read READY through it", stub_probes()["lint"]() == ("NO-DATA", "lint not probed: INTAKE_PROBES=stub"))]
    failed = [n for n, good_ in cases if not good_]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not failed else "FAILED: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
