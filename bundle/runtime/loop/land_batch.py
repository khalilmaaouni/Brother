#!/usr/bin/env python3
"""The WHOLE landing for a batch of READY builds, one command, about 8 lines out. Details go to a log on disk.
usage (repo root of the launch worktree): land_batch.py [--dry] <build.json ...>      land_batch.py --selftest
                                          land_batch.py --push   (the driver's reconcile: commit a closure's plan change and push
                                          a branch left ahead of hub, both through the frozen checks with hooks off)
Per build: gate (STATUS says READY for exactly this file, never READY-UNPROBED; council not DO NOT BUILD; spec 9 or more; every
unreadable input HOLDS) -> land_apply.py (both Pythons, fuzz) -> a RED or crashing build is reverted by name and dropped.
Then once: register new suites in check_all.sh, battery registration, system_doc and bundle_runtime (regenerate on 1) in a
disposable copy under the sandbox, the registered neighbour tests there too, plan evidence written from the run's own numbers,
every changed path attributed to a step or REFUSED, commit_scan gate, the pre-commit check and the push gate run by this command
from a frozen copy of the base commit (git runs no hook), templated commit, push to hub only, parity, shared git config
unchanged. Exit 0 only when parity holds."""
import datetime, fcntl, hashlib, json, os, re, shutil, signal, stat, subprocess, sys, tempfile, time
BIN = os.path.dirname(os.path.abspath(__file__)); EV = os.path.expanduser("~/.claude/evidence")
REMOTE = "hub"   # fixture default for the pure helpers; main() replaces it from upstream()
BRANCH = "refactor/brother-unified-1.1"   # fixture default, one statement so a module level string constant reader (test_land_batch_callsites) finds it
LAND_REMOTE = os.environ.get("BROTHER_LAND_REMOTE", "hub")   # the one remote a landing may push to (docstring: "push to hub only")


def remote_allowed(remote):
    """"" when the upstream remote is the landing remote, else the refusal. Found 2026-09-24 by the security review:
    the push went to whatever upstream the branch carried, so a clone whose upstream is the public origin would have
    landed there; only that clone's configuration stood in the way."""
    if remote == LAND_REMOTE: return ""
    return ("REFUSED: the upstream remote %r is not the landing remote %r (BROTHER_LAND_REMOTE); nothing is fetched, "
            "compared or pushed anywhere else" % (remote, LAND_REMOTE))
def expected_upstream_refusal(remote, branch, env=None):
    """"" unless BROTHER_EXPECTED_UPSTREAM is set and differs from <remote>/<branch>, else the refusal. Astra review of the
    practice proof design (2026-10-05): the landing checks the branch name, but --close and --push take whatever
    upstream the checkout carries, so a practice run whose tree tracked the release line would push its closures there.
    The pair pins the upstream it launched with; every push mode is refused, fail closed, when the tree now differs.
    Unset keeps today's behaviour."""
    env = os.environ if env is None else env
    want = (env.get("BROTHER_EXPECTED_UPSTREAM") or "").strip()
    if not want or "%s/%s" % (remote, branch) == want:
        return ""
    return ("REFUSED: the upstream %s/%s is not the expected %s (BROTHER_EXPECTED_UPSTREAM); nothing is landed, closed "
            "or pushed" % (remote, branch, want))
def upstream():
    """(remote, branch) from the checkout's configured upstream, or None. A literal remote and branch were true on one
    laptop and false in every other clone; nothing pushes or compares without a real upstream (owner, 2026-09-22)."""
    try:
        r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    up = r.stdout.strip()
    return tuple(up.split("/", 1)) if r.returncode == 0 and "/" in up else None
def code_root(frozen=None):
    """The frozen code the loop runs (model_router.code_root, U3, B5-08): close_unit and the plugin gate modules come from
    here, never from the landing tree this command commits into. Raises model_router.Refused in a proof phase with no
    BROTHER_CODE_ROOT; every caller turns that into a refusal of its own step, never a fall back to the cwd.
    INSIDE THE LANDER THE CODE ROOT IS NEVER THE LANDING TREE (review 15 finding 1, 2026-10-03): model_router.code_root
    falls back to the checkout outside a proof, and the checkout here is the tree a build just wrote into, so the gate
    policy and the closer were imported from it unsandboxed on every live landing. With BROTHER_CODE_ROOT (or a proof
    phase) the router's answer stands; otherwise the frozen copy of the base commit is the code root; with neither the
    step refuses. A landing tree answer refuses whatever route produced it."""
    sys.path.insert(0, BIN)
    import model_router
    if os.environ.get("BROTHER_CODE_ROOT") or os.environ.get("BROTHER_PROOF_PHASE"):
        root = model_router.code_root()
    elif frozen:
        root = frozen
    else:
        raise model_router.Refused("inside the lander the code root is never the landing tree: BROTHER_CODE_ROOT is not set and no frozen copy was given")
    if os.path.realpath(root) == os.path.realpath(os.getcwd()):
        raise model_router.Refused("inside the lander the code root is never the landing tree: %s is the tree being landed into" % root)
    return root
PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"; CHECK_ALL = "scripts/check_all.sh"
LOOP_UNIT = "BL"   # the unit whose owns names every brother.loop component (scripts/test_bl_owns_complete.py)
ANCHOR = "# LAST, on purpose"
#: A test path the lander may write into check_all.sh, a shell script run unsandboxed by people (ninth review 2026-10-02,
#: reproduced: a build-created scripts/test_$(cmd).py was pasted verbatim and ran at the next battery). Plain names only.
SAFE_TEST_PATH = re.compile(r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*/)*test_[A-Za-z0-9_]+\.py")

def build_name(sub, variant=0):
    """The one file name a build carries, <sub>-r<N>-build.json. sub_of reads exactly this shape back and nothing wider.
    finish_run wrote its repairs as <sub>-finisher-build.json, a name sub_of refuses, so every repair was held at the
    gate however good it was (finding B8, 2026-09-27); a writer names a build here instead of spelling the pattern."""
    return "%s-r%d-build.json" % (sub, variant)

def landing_records(lines, seen=None, owns=(), owner=LOOP_UNIT):
    """{unit id: [evidence line, ...]} to plan_store records: each a callable that appends this landing's lines, in
    order, to the unit plan_store re-reads under its lock, so evidence another writer added since this landing read
    the plan survives. A stale copy of the unit would erase it, which plan_store refuses with ValueError.
    seen, a dict, receives {unit id: (evidence re-read, evidence written)}: what plan_store.undo_appended needs to take
    back exactly this landing's lines on a refusal (X1 finding 3, 2026-09-27).
    owns: the new loop components this landing created (loop_components). Each one the owner unit does not name yet is
    appended to its owns in the same locked write, and seen[owner] carries them as a third item so a refusal takes them
    back too (FLOW-1.1.0 queue item 5, 2026-09-30: four landings in one night left a new scripts/loop file unowned and
    scripts/test_bl_owns_complete.py red on the run line). An owns that is not a list is left alone; the check reports it."""
    def append(uid, add, new):
        def record(fresh):
            before = fresh.get("evidence")
            ev = before or ""
            for line in add:
                ev = ev.rstrip() + line
            out = dict(fresh, evidence=ev) if add else dict(fresh)
            have = fresh.get("owns")
            added = [p for p in dict.fromkeys(new) if p not in have] if isinstance(have, list) else []
            if added: out["owns"] = have + added
            if seen is not None and (add or added):
                seen[uid] = (before, ev if add else before) + ((added,) if added else ())
            return out
        return record
    ids = {uid: list(add) for uid, add in lines.items()}
    if owns: ids.setdefault(owner, [])
    return {uid: append(uid, add, list(owns) if uid == owner else []) for uid, add in ids.items()}

def loop_components(paths):
    """The paths that make up brother.loop by scripts/test_bl_owns_complete.py's own rule (its components()): a .py or
    .sh directly in scripts/loop, scripts/test_loop*.py, scripts/test_unit_runner*.py. The check keeps its own copy of
    the rule on purpose: an audit that imported the control's rule would share its bugs. Order kept, duplicates dropped."""
    def one(p):
        d, name = os.path.split(p)
        return ((d == "scripts/loop" and name.endswith((".py", ".sh")))
                or (d == "scripts" and name.endswith(".py") and name.startswith(("test_loop", "test_unit_runner"))))
    return [p for p in dict.fromkeys(paths) if one(p)]

def sub_of(path):
    m = re.match(r"(.+)-r\d+-build\.json$", os.path.basename(path))
    return m.group(1) if m else None

def unit_of(sub, plan):
    for u in plan.get("units", []):
        if sub in (u.get("sub_units") or []): return u
    return None

def spec_gate(sub, plan, root=".", runner=None):
    """'' when the sub unit's OWN spec done check is green on both Pythons in the landing tree, else why the build drops.
    A/B/C test 2026-09-23: D14.5 landed on its builder's own tests while its spec's check did not exist. Unknown BLOCKS:
    no unit, no spec file, or no runnable check in the section drops the build; the pool readmits it when the spec changes."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # THIS directory's spec_check, never another copy
    import spec_check as SC
    u = unit_of(sub, plan)
    if not u or not u.get("spec"): return "spec gate: no unit or spec file names %s" % sub
    try:
        with open(os.path.join(root, u["spec"]), encoding="utf-8") as fh: text = fh.read()
    except OSError as exc:
        return "spec gate: spec %s unreadable (%s)" % (u["spec"], type(exc).__name__)
    cmd = SC.done_check(text, sub)
    if not cmd: return "spec gate: the section names no runnable done check for %s" % sub
    ok, detail = SC.run_both(cmd, root, runner=runner)
    return "" if ok else "spec done check red: %s | %s" % (detail, cmd[:90])


def score_hold(sc):
    """'' when sc is a usable spec score, else why it holds. A bool is refused BY TYPE, in its own sentence.
    Measured 2026-09-21: deleting the isinstance(sc, bool) test changed nothing any check could see, because
    True < 9 holds anyway, so the property was not merely untested, it was unexpressed. A refusal that is
    indistinguishable from another refusal cannot be asserted."""
    if isinstance(sc, bool) or not isinstance(sc, int): return "spec score %r is not a whole number" % (sc,)
    # ONE FLOOR WITH THE POOL (2026-09-25): runner_pool admits at BROTHER_SPEC_FLOOR (default 8, the owner's floor of
    # 2026-09-21) while this line refused under 9, so a build of an 8 was paid for and then thrown away at landing.
    floor = int(os.environ.get("BROTHER_SPEC_FLOOR", "8")) if os.environ.get("BROTHER_SPEC_FLOOR", "8").isdigit() else 9
    if sc < floor: return "spec %s of 10, needs %d" % (sc, floor)
    return ""

def gate(path, status_text, council, scores, plan):
    """'' when the build may land, else the reason it is held. Unknown or unreadable always holds."""
    sub = sub_of(path)
    if not sub: return "file name is not <sub>-rN-build.json"
    u = unit_of(sub, plan)
    if u is None: return "sub unit %s is in no plan unit" % sub
    w = (status_text or "").split()
    if len(w) < 2 or w[0] != "READY" or os.path.realpath(w[1]) != os.path.realpath(path):
        return "STATUS is not READY for this file (%s)" % (w[0] if w else "missing")
    if not isinstance(council, dict) or not isinstance(scores, dict): return "council or score file unreadable"
    if council.get(u["id"], {}).get("state") == "DO-NOT-BUILD": return "council holds %s: DO NOT BUILD" % u["id"]
    hold = score_hold(scores.get(sub, {}).get("score"))
    if hold: return hold
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import plan_store   # THIS directory's copy: the one landed test
    if plan_store.sub_landed(sub, u.get("evidence") or ""): return "already landed per plan evidence"
    return ""

def unit_closed(u):
    """True when the unit's evidence names EVERY one of its sub units as landed, read by plan_store.sub_landed, the one
    landed test (lane G, 2026-09-27): the hand written regex this replaced read A.1 inside "A.10 landed" and counted
    "A.1 not landed" as a landing, so main() listed an unfinished unit as closed and asked close_unit to close it."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import plan_store
    return all(plan_store.sub_landed(s, u.get("evidence") or "") for s in u["sub_units"])

def evidence_line(sub, date, suites, crashes, head):
    """Must satisfy the one landed test, plan_store.sub_landed: the whole sub id, then no full stop within 80 characters, then 'landed'."""
    return " %s landed %s: %s; green on Python 3.13 and 3.9; landing fuzz crashes %d; base %s." % (sub, date, ", ".join(suites) or "no suite", crashes, head)

def register(text, tests):
    """check_all.sh text with one run_check per unregistered new test, inserted before the closing comparison. None if no anchor."""
    lines = []
    for t in tests:
        if not SAFE_TEST_PATH.fullmatch(t): continue   # never pasted into a shell script; main() refuses the landing first
        if t in text or t[:-3].replace("/", ".") in text: continue
        slug = re.sub(r"[^a-z0-9]+", "-", os.path.basename(t)[5:-3].lower()).strip("-") + "-self"
        lines.append('run_check "%s" python3 %s' % (slug, t if t.startswith("scripts/") else "-B -m unittest " + t[:-3].replace("/", ".")))
    if not lines: return text
    i = text.find(ANCHOR)
    return None if i < 0 else text[:i] + "\n".join(lines) + "\n" + text[i:]

def unattributed(changed, attributed):
    return sorted(p for p in changed if p not in attributed)


def snapshot(paths, root=None):
    """{path: (bytes, mode) for a regular file, ("link", target) for a symlink, None for a path that does not exist}, as
    each stands now: the tree before a build applies, so a dropped build can be put back byte for byte (finding B1).
    Read with lstat, so a link is recorded as a link and never read through (eleventh review 2026-10-02: a landed file
    replaced by a symlink read back as its target's bytes and the rollback reported success). root: the tree the relative
    paths are read under (default the current directory); the keys stay the relative paths either way."""
    out = {}
    for p in paths:
        full = os.path.join(root, p) if root else p
        try:
            st = os.lstat(full)
        except FileNotFoundError:
            out[p] = None
            continue
        if stat.S_ISLNK(st.st_mode):
            out[p] = ("link", os.readlink(full))
        elif stat.S_ISDIR(st.st_mode):
            out[p] = ("dir", None)   # never equal to a file's entry: a directory where a file was is a mismatch, not a crash
        else:
            with open(full, "rb") as fh:
                out[p] = (fh.read(), st.st_mode & 0o7777)
    return out


def rollback(snap, changed, log, plan_path=PLAN):
    """Put the tree back exactly as `snap` saw it: a path dirty only since the snapshot goes back to HEAD by name
    (revert), a path an earlier build of this batch had already edited goes back to that build's bytes. Returns '' when
    the tree now reads exactly as the snapshot, else what is left.
    WHY (finding B1, 2026-09-27): the drop reverted changed_paths() minus the paths already attributed, so a rejected
    build's edit to a file the first build also edited stayed in the tree and was pushed under the first build's record.
    THE CHECK IS THE READ BACK, not the writes: every restore may fail on its own, so the verdict is the tree re-read.
    NEVER THROUGH A LINK (eleventh review 2026-10-02): whatever sits at the path is removed first and the file made
    afresh with O_NOFOLLOW, so bytes never land where a planted symlink points. NEVER THE PLAN: other writers append to
    it under its lock while a batch runs; a blanket checkout erased their evidence (X1 finding 3). At a drop this landing
    has appended nothing yet, so the plan is left exactly as it stands; only revert_landing takes a landing's lines back."""
    revert(sorted(p for p in changed if p not in snap and p != plan_path), log)
    cause = {}
    for p, v in sorted(snap.items()):
        if p == plan_path: continue
        try:
            if os.path.lexists(p): os.remove(p)
            if v is None: continue
            if v[0] == "link":
                os.symlink(v[1], p)
            else:
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(v[0])
                os.chmod(p, v[1])
        except OSError as exc:   # the read back below decides; the cause is named there
            cause[p] = type(exc).__name__
    now, left = snapshot([p for p in snap if p != plan_path]), changed_paths() - {plan_path}
    wrong = sorted(p for p in snap if p != plan_path and now.get(p) != snap[p]) + sorted(left - set(snap))
    return "" if not wrong else "%d path(s) not back as they were: %s" % (
        len(wrong), ", ".join(p + (" (%s)" % cause[p] if p in cause else "") for p in wrong[:3]))


def commit_text(landed, facts, checks):
    """The landing commit message. It CLAIMS only checks this landing ran before the commit, each with the exit code it
    returned: `checks` is the list of (label, exit code) main() appends to as each one runs. WHY (finding B10,
    2026-09-27): the fixed template said fast discover and the hermetic check exited 0 after discovery had left the
    gates, and the hermetic check runs after the commit, so no commit can report it."""
    lines = ["%s land: READY builds through the landing checks below" % " and ".join(landed), "",
             "Checks this landing ran before this commit, with their exit codes:"]
    for label, rc in checks:
        extra = facts.get(label[len("land_apply "):]) if label.startswith("land_apply ") else None
        lines.append("%s exit %d%s" % (label, rc, ": %s; landing fuzz crashes %d" % (", ".join(extra[0]) or "no suite", extra[1]) if extra else ""))
    lines += ["", "The push gate (the pre-push hook's checks, run from a frozen copy of the base commit) and the push run after this commit; their exit codes are in the land log.",
              "Landed by land_batch.py; full run log kept outside the repository."]
    return "\n".join(lines) + "\n"

# EVERY function below was a decision taken inline in main(). An adversarial sweep on 2026-09-21 ran 18
# mutations against this file and ELEVEN survived at "selftest: 17 cases, OK", because the selftest covered
# four pure helpers and never main(), where the landing happens. They are extracted here, facts in and
# decision out, so each one can be asserted by scripts/test_land_batch_loop_guard.py. Nothing else moved.

def preflight(branch, dirty, head, hub_head, plan):
    """'' when the landing may start, else the refusal. One condition per line and no combined test, so a
    fixture trips exactly one of them: a fixture that trips two guards proves neither."""
    if branch != BRANCH: return "on %r, not %s" % (branch, BRANCH)
    if dirty: return "tree not clean (%d paths); land from a clean tree so every change is attributable" % len(dirty)
    if head != hub_head: return "local %s is not hub %s before landing" % (head, hub_head)
    if plan is None: return "plan unreadable"
    return ""

def landing_verdict(returncode, stdout, status_text, sub, plan, gate=None, stderr=""):
    """after_build, then THE SPEC'S OWN CHECK: a build whose suites passed still drops, quarantined with the reason, when
    its spec done check is red or cannot be found (spec_gate). The one decision main() takes for every build."""
    verdict, payload, crashes, note = after_build(returncode, stdout, status_text, stderr)
    if verdict == "land":
        why = (gate or spec_gate)(sub, plan)
        if why:
            return "drop", why, crashes, "QUARANTINE dropped at landing, %s | previous: %s\n" % (why, (status_text or "").strip() or "none")
    return verdict, payload, crashes, note


FUZZ_SUMMARY_RE = re.compile(r"^FUZZ {4}new modules \d+ \| crashes (\d+) \| ", re.M)


def after_build(returncode, stdout, status_text, stderr=""):
    """What a finished land_apply.py means: ("land", suites, crashes, None) or ("drop", why, crashes, status).
    A DROP ALWAYS carries a QUARANTINE status. A STATUS left READY is offered again every pass (measured
    2026-09-21: two builds offered three passes running) and the loop livelocks on it. The first word stays
    QUARANTINE because scripts/loop/unit_runner.py previous_reason() reads exactly that word.
    The previous text is taken as an ARGUMENT, not re-read here: the old inline version read the file back
    after opening it "w", so it had already been truncated and every quarantine recorded "previous: ".
    The crash count is read from the lander's own FUZZ summary line only, the last one, never from worker text; the
    verdict only from a line that STARTS with it (FX-08.3, 2026-09-28: an exception message reading "crashes 0" made
    the first unanchored match, and a build with a crash landed)."""
    counted = FUZZ_SUMMARY_RE.findall(stdout)
    crashes = int(counted[-1]) if counted else 99   # sentinel: keeps the drop fail closed, never printed as a count
    if returncode or crashes or not re.search(r"^VERDICT SUITES GREEN", stdout, re.M):
        # THE REASON IS READ FROM WHERE LAND_APPLY WROTE IT (2026-09-27 15:06): its refusals go to stderr, so a stdout only
        # reader briefed the next runner "fuzz crashes 99: no output" for a patch whose anchor was not unique.
        last = (stdout.strip().splitlines() or (stderr or "").strip().splitlines() or ["no output"])[-1][:110]
        why = "exit %d, %s: %s" % (returncode, "fuzz crashes %d" % crashes if counted else "fuzz not reached", last)
        # THE CRASH LINES TRAVEL WITH THE REASON (2026-09-24 04:46 and 05:00: D11.e was dropped twice on "fuzz crashes 7"
        # and the next runner, briefed from this note, rebuilt the same crash, because the note carried the count and
        # the verdict line but not one call that crashed); the next brief needs the calls, not the number
        crash_lines = [l.strip()[:100] for l in stdout.splitlines() if l.strip().startswith("CRASH")][:6]
        if crash_lines: why += " | " + "; ".join(crash_lines)
        return ("drop", why, crashes, "QUARANTINE dropped at landing, %s | previous: %s\n" % (why, (status_text or "").strip() or "none"))
    suites = ["%s %s" % (m[0].strip(), m[1]) for m in re.findall(r"^py3\s+(\S+)\s+exit=0 (?:Ran )?(\d+ tests?)", stdout, re.M)]
    return ("land", suites, crashes, None)

#: (name, check argv, regenerate argv or None, the output set the generator may write: exact paths and directory prefixes
#: ending in /, compared case folded). A regenerate step that writes anything else is a red gate and the batch is refused
#: before take_generated (review 14 finding 1, 2026-10-02, reproduced: a build declared docs/x.txt, its system_doc stub also
#: wrote scripts/loop/secret_scan.py and scripts/private_terms_scan.py in the copy, dest() let both through and both landed:
#: the modules the NEXT landing's frozen checks import). The bundle set is what scripts/bundle_runtime.py writes (generate,
#: generate_loop, codex_skills.generate, generate_hooks, generate_mcp, generate_antigravity; read 2026-10-02).
GATES = (("registration", ["scripts/test_battery_registration.py"], None, ()),
         ("system_doc", ["scripts/system_doc.py", "--check"], ["scripts/system_doc.py"], ("SYSTEM.md",)),
         ("bundle", ["scripts/bundle_runtime.py", "--check"], ["scripts/bundle_runtime.py"],
          ("bundle/runtime/", "bundle/codex-skills/", "bundle/hooks/hooks.json", "bundle/mcp.json", "bundle/.antigravity-plugin/scripts/")))


def undeclared_outputs(paths, outputs):
    """The paths a gate's generator wrote (generated_in's keys, relative) that its declared output set does not cover: an
    exact path, or anything under a prefix ending in /. Case folded, this volume is case insensitive. A path that is not a
    plain string is undeclared, never a crash."""
    exact = {o.casefold() for o in outputs if not o.endswith("/")}; dirs = tuple(o.casefold() for o in outputs if o.endswith("/"))
    out = []
    for p in sorted(paths, key=str):
        q = os.path.normpath(p).casefold() if isinstance(p, str) and p else ""
        if not q or (q not in exact and not q.startswith(dirs)): out.append(p)
    return out
# U1 of the final plan (2026-09-24): the landing runs gates scoped to the change. The whole plugin runtime discovery (52 test
# files, every product's selftest) is NOT a landing gate any more: on 2026-09-24 it refused both evening landings on a
# BrotherDS vault selftest that passes by hand while the builds were good, and on 2026-09-24 14:5x it quarantined a good
# D3.7 build the same way. The registered tests of every module a landing edits still run through the fifth gate
# (neighbour_tests, both Pythons); the full discovery stays in the battery (scripts/check_all.sh).
LANDING_GATE_NAMES = tuple(g[0] for g in GATES)


#: THE GATE POLICY RUNS IN A BOX (review 17 finding 1, 2026-10-03, executed with a marker): gate_order imported the D13
#: policy IN PROCESS from the frozen copy, and the frozen copy holds every earlier landed build's writes, so a build that
#: wrote plugin/__init__.py (or any package __init__ on that import path) ran inside the lander, unconfined. A deny list of
#: such paths kept growing a new escape per review. The source is executing tree code outside the sandbox, so the policy
#: now runs as this child, isolated (-I: no script dir, no user site, no PYTHON* variables; the code root is appended
#: AFTER the standard library) and boxed (no network, writes only to its own temp directory). It prints one JSON line;
#: the lander keeps the gate SET and accepts only a reordering of it.
GATE_ORDER_CHILD = "exec(%r)" % r"""import json, os, sys, tempfile
root, names, logs = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
sys.path.append(root)
from plugin.runtime.brother.core import dream_gate, dream_gate_policy as P
tmp, translated = tempfile.mkdtemp(prefix="gate-history-"), []
for i, lg in enumerate(logs):
    rows = dream_gate.read_gates(lg) or []
    lines = ["%s exit %d %s %ds recorded" % (dream_gate.gate_verdict(int(r["exit_code"])), int(r["exit_code"]), r["check_name"], max(0, int(r["duration_ms"])) // 1000) for r in rows]
    if lines:
        with open(os.path.join(tmp, "log-%d.txt" % i), "w", encoding="utf-8") as fh: fh.write("\n".join(lines) + "\n")
        translated.append(os.path.join(tmp, "log-%d.txt" % i))
if not translated:
    print(json.dumps({"order": None})); sys.exit(0)
cur = ["landing." + n for n in names]
print(json.dumps({"order": P.select_order(cur, P.aggregate_history(translated), {c: "landing" for c in cur})}))
"""


def gate_order(gates=GATES, runs_root=None, frozen=None, log=None):
    """The landing's gates in the order the board's D13 policy proposes from the recorded gate history (gates.tsv of the
    newest run directories, written by record_gate): the gate that fails most often and costs least runs first, so a red
    landing is known in seconds rather than minutes. The fixed order stands whenever the policy has nothing to read or
    refuses (NoDataError, a corrupt log), and the reason is printed once; the set of gates never changes, only their order.
    D13 wiring, 2026-09-24. The policy is code_root(frozen)'s, run by GATE_ORDER_CHILD under boxed(), never imported here
    (review 17 finding 1): no frozen copy and no BROTHER_CODE_ROOT is the fixed order, named; a child that exits nonzero,
    prints no JSON, or names a different set is the fixed order, named, never a decision."""
    import io
    names = [g[0] for g in gates]; by = {g[0]: g for g in gates}
    root = runs_root or os.path.expanduser("~/.claude/evidence/loop-runs")
    logs = sorted((os.path.join(root, d, "gates.tsv") for d in (os.listdir(root) if os.path.isdir(root) else []) if os.path.isfile(os.path.join(root, d, "gates.tsv"))))[-10:]
    if not logs: return list(gates), "fixed order: no gate history yet"
    try:
        code = code_root(frozen)
    except Exception as exc:   # sbe: allow-silent a refused code root is the fixed order, named; it never decides a landing
        return list(gates), "fixed order: the policy refused (%s)" % str(exc)[:100]
    out_dir = tempfile.mkdtemp(prefix="gate-order-")
    try:
        r = boxed([sys.executable, "-I", "-B", "-c", GATE_ORDER_CHILD, code, json.dumps(names), json.dumps(logs)],
                  log if log is not None else io.StringIO(), 120, None, cwd=out_dir)
        try:
            got = json.loads((r.stdout or "").strip().splitlines()[-1]) if r.returncode == 0 else None
        except (ValueError, IndexError):
            got = None
        if not isinstance(got, dict) or "order" not in got:
            return list(gates), "fixed order: the policy refused (NO-DATA: the boxed policy exited %d with no answer: %s)" % (
                r.returncode, ((r.stderr or "").strip().splitlines() or ["no output"])[-1][:80])
        if got["order"] is None: return list(gates), "fixed order: the gate history holds no rows"
        ordered = got["order"]
        if not isinstance(ordered, list) or not all(isinstance(c, str) and c.startswith("landing.") for c in ordered) \
                or sorted(c[len("landing."):] for c in ordered) != sorted(names):
            return list(gates), "fixed order: the policy returned a different set"
        out = [by[c[len("landing."):]] for c in ordered]
        return out, "D13 order from %d gate log(s): %s" % (len(logs), ", ".join(g[0] for g in out))
    finally:
        shutil.rmtree(out_dir, True)


def record_gate(run_dir, name, returncode, ms):
    """D4 wiring (2026-09-24): every gate the landing runs is appended to <run_dir>/gates.tsv in dream_gate's own line
    shape ("name TAB exit TAB ms", format_gate_line), when a run directory is declared. Recording never takes a landing
    down: a failure is one stderr line. The line is written HERE, with dream_gate's own field rules, and no module is
    imported for it (review 17 finding 1: the writer was imported in process from the frozen copy, whose package
    __init__ files are a build's writes)."""
    if not run_dir: return False
    try:
        if not isinstance(name, str) or not name or set(name) & set("\t\n\r"):
            raise ValueError("check_name must be a non-empty str without TAB or newline")
        rc, dur = int(returncode), int(ms)
        if not 0 <= rc <= 255 or dur < 0: raise ValueError("exit_code must be in 0..255 and duration_ms >= 0, got %r, %r" % (rc, dur))
        with open(os.path.join(run_dir, "gates.tsv"), "a", encoding="utf-8", newline="") as fh:
            fh.write("%s\t%d\t%d\n" % (name, rc, dur))
        return True
    except Exception as exc:   # sbe: allow-silent recording is a side effect of the landing, never its verdict; the reason is printed
        sys.stderr.write("land_batch: gate not recorded (%s)\n" % str(exc)[:160]); return False


def usd_text(sub, runs_root=None, ledger_path=None, claude_path=None):
    '''One short field for the LANDED line: usd N.NN or usd NO-DATA.'''
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import unit_ledger
    except ImportError:
        return 'usd NO-DATA (unit_ledger not importable)'
    runs_root = runs_root if runs_root is not None else unit_ledger.RUNS
    usd, skipped = unit_ledger.blended_usd_detail(sub, runs_root, ledger_path if ledger_path is not None else unit_ledger.LEDGER)
    # THE NATIVE CLAUDE PART, BY IDENTITY (2026-10-04): the provider ledger alone read NO-DATA for every native build
    if claude_path is None:
        import claude_ledger
        claude_path = claude_ledger.ledger_path()
    nat, unknown, calls, untagged = unit_ledger.native_usd(sub, runs_root, claude_path)
    if unknown:
        out = 'usd NO-DATA (%d Claude call(s) of %s have no cost%s)' % (unknown, sub, '; %.2f known' % ((usd or 0) + (nat or 0)) if (usd or nat) else '')
    elif usd is None and nat is None:
        out = 'usd NO-DATA'
    elif nat is None:
        out = 'usd %.2f' % usd
    else:
        out = 'usd %.2f (claude %.2f over %d call(s) under its runner, provider %s)' % ((usd or 0) + nat, nat, calls, 'NO-DATA' if usd is None else '%.2f' % usd)
    if untagged and calls:
        out += ' (%d earlier run(s) before the run tag: their Claude spend is not in this figure)' % untagged
    if skipped > 0:
        out += ' (%d corrupt row(s) skipped)' % skipped
    return out


def landed_line(landed, closed, logp, usd_by_sub):
    '''The one LANDED line, with each sub unit blended USD between the units field and the log path.'''
    if not isinstance(landed, list) or not landed or not all(isinstance(s, str) for s in landed):
        return 'REFUSED: landed_line got no sub unit names'
    def _usd_for(s):
        if not isinstance(usd_by_sub, dict):
            return 'usd NO-DATA'
        v = usd_by_sub.get(s, 'usd NO-DATA')
        return v if isinstance(v, str) and v else 'usd NO-DATA'
    usd_field = ', '.join('%s %s' % (s, _usd_for(s)) for s in landed)
    return 'LANDED  %s | units with every sub unit landed (run the unit done-check): %s | %s | log %s' % (', '.join(landed), closed or 'none', usd_field, logp)


def write_status(path, text):
    """Write text to path atomically: path + ".tmp" then os.replace; a reader sees the old text or the new one, never a torn one. Only os and builtins are used, so this body can be lifted by a test. Unknown input REFUSES: a path that is not a non-empty str, or a text that is not a str, raises ValueError naming it; an OSError from the write, the flush, the fsync or the replace is re-raised as OSError naming the path and the cause, and the stale .tmp is left for the next write to overwrite."""
    if not isinstance(path, str) or not path:
        raise ValueError("write_status path is not a non-empty str: %r" % (path,))
    if not isinstance(text, str):
        raise ValueError("write_status text is not a str: %r" % (text,))
    tmp = path + ".tmp"
    # ONE LOCK WITH salvage.promote (2026-09-27): promote compares STATUS with what its selection read and then writes,
    # both under <runs>/.status.lock; a landing's QUARANTINE or LANDED written between the two was overwritten with READY.
    runs = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    try:
        with open(os.path.join(runs, ".status.lock"), "a") as lock:   # outside every run folder, so no run's mtime moves
            fcntl.flock(lock, fcntl.LOCK_EX)   # released when the file closes
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
    except OSError as exc:
        raise OSError("write_status failed for %r: %s" % (path, exc)) from exc


def mark_landed(status_paths, builds, head, now=None):
    """After the push reached hub: every landed build's STATUS is rewritten as LANDED <build> at <time> commit <sha>, so
    the pool and the salvage never offer it as READY again (2026-09-24: U8.1 and R3.3 sat READY on disk for a day after
    their landing and were held at the gate as 'already landed per plan evidence'). Returns the paths written."""
    written = []
    status_paths = [p for p in status_paths] if isinstance(status_paths, (list, tuple)) else []
    builds = [b for b in builds] if isinstance(builds, (list, tuple)) else []
    for st, b in zip(status_paths, builds):
        try:
            write_status(st, "LANDED %s at %s commit %s\n" % (b, (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S"), head))
            written.append(st)
        except (OSError, ValueError) as exc:   # sbe: allow-silent the landing already happened; a STATUS that cannot be rewritten is named, not fatal
            print("        note: STATUS not rewritten for %s (%s)" % (b, exc))
    return written


LANDING_SCHEMA = "loop-landing-v1"


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_history(proof, commit, horizon, log_bytes):
    """THE ONE WRITER of proof/landing-history/<commit>.log: log_bytes (`git log --format=%H%x00%B <commit>..<horizon>`)
    followed by the range line proof_accept.history_end reads back, written to a temp file in proof/, synced, then
    renamed into place. A write that fails at any point leaves the previous history byte for byte and raises OSError; a
    history is never half rewritten (X1 finding 1, 2026-09-27: an in place rewrite cut short kept the newest entry and
    lost an older revert, and the landing counted). NEVER CREATES proof/."""
    hist = os.path.join(proof, "landing-history")
    try:
        sys.path.insert(0, BIN)
        import proof_accept
        data = log_bytes + proof_accept.history_end(commit, horizon)
    except (ImportError, AttributeError, TypeError) as exc:   # no range line means no history: the reader says UNKNOWN
        raise OSError("the history format could not be read (%s)" % type(exc).__name__)
    if not os.path.isdir(proof): raise OSError("no proof directory")
    os.makedirs(hist, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".landing-history-", dir=proof)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, os.path.join(hist, commit + ".log"))
    except BaseException:
        try: os.unlink(tmp)
        except OSError as exc: print("HISTORY temp %s not removed (%s)" % (tmp, exc))
        raise


def refresh_histories(run_dir, remote_sha, git_log):
    """Q3 judges survival at the run's LAST landing fetch (Codex check-in 5, finding 2): each earlier landing whose
    remote was observed gets its retained history rewritten as git_log(<earlier>, <remote_sha>), the range up to the
    remote this landing just observed, so a revert landed in between is seen. git_log returns bytes or None; None (or
    a file that cannot be written) leaves that history as it was, stopping short of this fetch, and the reader
    (proof_accept.reaches_horizon) then reads that landing's survival UNKNOWN, never surviving (finding B2, 2026-09-27).
    NEVER CREATES proof/. Returns the number of histories rewritten; every one it could not rewrite is NAMED on one
    HISTORY line, never dropped in silence (the silent-failure lint, 2026-09-27)."""
    proof = os.path.join(run_dir, "proof") if isinstance(run_dir, str) and run_dir else ""
    hist = os.path.join(proof, "landing-history")
    if not remote_sha or not os.path.isdir(hist):
        return 0
    done, missed = 0, []
    try:
        with open(os.path.join(proof, "landings.jsonl"), encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    except (OSError, ValueError) as exc:
        rows, missed = [], ["the landing record (%s)" % type(exc).__name__]
    for row in rows:
        commit = row.get("commit") if isinstance(row, dict) else None
        if not isinstance(commit, str) or not os.path.isfile(os.path.join(hist, commit + ".log")):
            continue
        data = git_log(commit, remote_sha)
        try:
            if data is None:
                raise OSError("its range could not be read")
            write_history(proof, commit, remote_sha, data)
            done += 1
        except OSError as exc:
            missed.append("%s (%s)" % (commit[:12], exc))
    if missed:
        print("HISTORY %d not refreshed to %s, each reads UNKNOWN survival: %s" % (len(missed), str(remote_sha)[:12], "; ".join(missed[:3])))
    return done


def record_landing(run_dir, commit, subs, builds, verdict, remote_ref, remote_sha, fetched_at, log_bytes=None,
                   files=None, checks=None, done_check=None, log=None, parity=None):
    """U10 (B5-02): one loop-landing-v1 row per landing commit this run made, appended to <run dir>/proof/landings.jsonl,
    so a proof counts landings from what the lander did, never from git history (which also holds closure commits and
    commits fast forwarded in from elsewhere). commit is the LANDING commit, read right after it was made. remote_sha and
    fetched_at are the remote ref this run observed at its LAST fetch (the closure's, when a closure ran and its fetch
    succeeded, X1 finding 2), None when the landing fetch failed (UNVERIFIED).
    log_bytes, when the remote was observed, are `git log --format=%H%x00%B <commit>..<remote_sha>`, written to
    proof/landing-history/<commit>.log for the survival rule (a revert in that range removes the landing).
    THE PER FILE FACTS (2026-09-30, AGENTS.md "The receipt contract"): files (every path in the landing commit),
    checks ([{label, command, exit_code}], every check that ran before the commit with the exit code it returned,
    landing_facts builds them), done_check ({sub: the build json's declared done_check or None}) and log (where the
    full output lives) are stored only when given, so a caller that names none writes the row it always wrote.
    parity, given on the UNVERIFIED path, is the NO-DATA note naming why no remote was observed (the push exit and
    the fetch exit), so the row says why it carries no remote_sha instead of merely lacking one.
    loop_receipt.landing_receipts turns them into the engine's per-file entries.
    NEVER CREATES proof/: outside a proof run there is nowhere to write and nothing is written. Returns the line to print,
    "" when there is no proof directory. FAIL DIRECTION: a row that cannot be appended loses this landing from the run's
    count (cost per landing rises, never falls) and says so; history that cannot be written reads NO-DATA downstream."""
    proof = os.path.join(run_dir, "proof") if isinstance(run_dir, str) and run_dir else ""
    if not proof or not os.path.isdir(proof): return ""
    if not commit: return "RECORD  NOT WRITTEN: the landing commit could not be read, so this run's count is missing it"
    notes = []
    if log_bytes is not None:
        try:
            write_history(proof, commit, remote_sha, log_bytes)
        except OSError as exc:
            notes.append("history not written (%s)" % exc)
    row = {"schema": LANDING_SCHEMA, "commit": commit, "subs": list(subs), "builds": list(builds), "verdict": verdict,
           "remote_ref": remote_ref, "remote_sha": remote_sha, "fetched_at": fetched_at, "at": utc_now()}
    row.update({k: v for k, v in (("files", files), ("checks", checks), ("done_check", done_check), ("log", log), ("parity", parity)) if v is not None})
    try:
        fd = os.open(os.path.join(proof, "landings.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(row, sort_keys=True, default=str) + "\n").encode("utf-8")); os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        return "RECORD  NOT WRITTEN for commit %s (%s): this run's count is missing it" % (commit[:12], exc)
    return "RECORD  commit %s %s in proof/landings.jsonl%s" % (commit[:12], verdict, "; " + "; ".join(notes) if notes else "")


def note_check(checks, cmds, label, argv, rc):
    """One check that ran: (label, exit code) into checks, what commit_text prints, and the exact command into cmds at the
    same index, what the landing record keeps. Every place main() runs a check goes through here, so the two lists never drift."""
    checks.append((label, rc)); cmds.append(" ".join(argv))


def build_declared(path):
    """The set of paths a build json declares in its edits and tests, read the way build_done_check reads it; an
    unreadable build declares nothing, so everything it wrote is undeclared (fails closed)."""
    try:
        with open(path, encoding="utf-8") as fh: text = re.sub(r"^```[a-z]*\n|\n```$", "", fh.read().strip())
        b = json.loads(text[text.find("{"):text.rfind("}") + 1])
        return {it["path"] for it in (b.get("edits") or []) + (b.get("tests") or [])
                if isinstance(it, dict) and isinstance(it.get("path"), str)}
    except (OSError, ValueError, AttributeError, TypeError): return set()


def build_done_check(path):
    """The done_check a build json declares, or None when the file cannot be read or names none: NO-DATA downstream, never a
    guess. Reads the way land_apply.load does (a fenced block is accepted)."""
    try:
        with open(path, encoding="utf-8") as fh: text = re.sub(r"^```[a-z]*\n|\n```$", "", fh.read().strip())
        value = json.loads(text[text.find("{"):text.rfind("}") + 1]).get("done_check")
    except (OSError, ValueError, AttributeError): return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def landing_facts(files, checks, cmds, done_checks, log):
    """The keyword arguments record_landing takes for one landing: files is the list the commit holds, checks the
    [{label, command, exit_code}] rows (checks and cmds are the aligned lists note_check fills), done_checks {sub: command
    or None} and log the land log path."""
    return {"files": list(files), "checks": [{"label": l, "command": c, "exit_code": rc} for (l, rc), c in zip(checks, cmds)],
            "done_check": dict(done_checks), "log": log}


def refusal_reason(red, stray):
    """'' when the batch may be committed. Each blocks ON ITS OWN: a stray path is an edit this run cannot
    attribute, and committing it signs someone else's work into this landing, whatever the gates said."""
    if red: return "gates RED (%s)" % ", ".join(red)
    if stray: return "stray path(s): %s" % ", ".join(stray[:3])
    return ""

def gate_quarantine(landed, status_paths, why, prev):
    """The QUARANTINE text for a build refused at the gates, or None when blame is ambiguous. ONE build in the
    batch means the blame is unambiguous, so it is quarantined: left READY it is retried every pass forever.
    With more than one build nothing can be blamed fairly, so they are only reverted and bisect_plan retries
    them singly."""
    if len(landed) != 1 or not status_paths: return None
    return "QUARANTINE refused at the gates alone, %s | previous: %s\n" % (why, prev)

def bisect_plan(landed, builds, no_bisect):
    """The retries for a refused multi build batch: a list of argument lists, ONE build each, never the batch
    again. Measured 2026-09-21: one poisoned build in a batch of five reverted all five, and the identical
    five were offered again every pass, forever. Retrying alone lands the innocent ones and drives the guilty
    one into the single build quarantine above, which already knows how to blame."""
    if no_bisect or len(landed) <= 1: return []
    return [[b] for b in builds]

def blame_paths(landed, status_paths, no_bisect=None):
    """The STATUS files a refusal after the commit may quarantine: every one for a single build, whose blame is
    unambiguous, and none for a batch, which is retried build by build instead (retry_alone)."""
    no_bisect = ("--no-bisect" in sys.argv) if no_bisect is None else no_bisect
    return list(status_paths) if len(landed) <= 1 or no_bisect else []


def retry_alone(landed, builds, no_bisect=None, run=None):
    """Retry each build of a refused batch ALONE, from every refusal point: the gates, the commit scan, the commit,
    the hermetic check and the push. Measured 2026-09-28: a batch refused at the commit scan (one build's tests carried
    secret-shaped fixtures) was neither retried nor quarantined, so the same batch came back every pass, the pool
    was never refilled, and run B ended UNPRODUCTIVE at 05:22; refusals after the commit quarantined every build of a
    batch, five innocent ones in one night. Alone, the innocent builds land and the guilty one reaches the single
    build quarantine. 0 when at least one landed, else 1; 1 when there is nothing to retry."""
    no_bisect = ("--no-bisect" in sys.argv) if no_bisect is None else no_bisect
    run = run or (lambda group: subprocess.run([sys.executable, __file__, "--no-bisect"] + group).returncode)
    retries = bisect_plan(landed, builds, no_bisect)
    if not retries:
        return 1
    ok = 0
    for group in retries:
        print("BISECT  retrying %s alone" % ", ".join(sub_of(g) or g for g in group))
        if run(group) == 0: ok += 1
    print("BISECT  %d of %d build(s) landed alone; the rest are quarantined or still READY" % (ok, len(retries)))
    return 0 if ok else 1


STEP_DETAIL = {"fetch": "nothing landed", "commit scan": "staged, not committed", "pre-commit check": "staged, not committed",
               "commit": "nothing landed", "hermetic check": "the commit is kept locally, NOT pushed",
               "push gate": "the commit is kept locally, NOT pushed"}

def step_refusal(step, rc):
    """'' when the step passed, else the refusal naming what is no longer true. Every outside exit code is
    load bearing: a result that is read and not acted on is an audit, not a gate. Measured 2026-09-21: the
    commit_scan exit code and the hermetic exit code could each be ignored with the selftest still green."""
    if not rc: return ""
    return "%s exit %d; %s" % (step, rc, STEP_DETAIL.get(step, "refused"))

def exit_code(parity_ok, push_rc, cfg_same):
    """0 only when the push returned 0 AND hub matches local AND the shared git config is untouched. Three
    separate facts, so a fixture trips exactly one. A landing that claims 0 without parity is the whole
    failure this command exists to prevent."""
    if push_rc: return 1
    if not parity_ok: return 1
    if not cfg_same: return 1
    return 0

def neighbour_tests(paths, exists, listdir, registry_text):
    """The registered tests named after each module a build edited: scripts/<m>.py and scripts/loop/<m>.py map to
    scripts/test_<m>.py and scripts/test_<m>_*.py; plugin/runtime/brother/core/<m>.py maps to its sibling test_<m>*.py.
    Only tests the battery names (registry_text is check_all.sh) count: a brand new test is the build's own and
    land_apply already ran it. Generated trees (bundle/, docs/) map to nothing. Returns (test path, dotted or None)
    pairs, deduplicated, in path order. THE FIFTH GATE (2026-09-24): the four gates are not the battery; five act
    mode seam tests and two other registered checks were red on the launch tree for a day and landed through."""
    out = []
    for p in sorted(set(paths)):
        if not p.endswith(".py") or p.startswith(("bundle/", "docs/")): continue
        d, n = os.path.split(p); m = n[:-3]
        if d in ("scripts", "scripts/loop"): td, dotted = "scripts", False
        elif d == "plugin/runtime/brother/core": td, dotted = d, True
        else: continue
        try: names = sorted(listdir(td))
        except OSError: continue
        for t in names:
            if t == "test_%s.py" % m or (t.startswith("test_%s_" % m) and t.endswith(".py")):
                tp = os.path.join(td, t); dn = tp[:-3].replace("/", ".")
                # a whole token, never a substring: test_or_fanout sat inside the registered test_or_fanout_m51 and was run as a
                # neighbour although deliberately unregistered (Codex review 2026-09-25)
                reg = lambda x: re.search(r"(?<![\w.])" + re.escape(x) + r"(?![\w])", registry_text) is not None
                if exists(tp) and (reg(tp) or reg(dn)) and tp not in [o[0] for o in out]:
                    out.append((tp, dn if dotted else None))
    return out


def neighbour_verdict(red_with_build, red_on_base):
    """REQ-H-BASE: a neighbour test red with the build is the build's fault only when it is green on the base.
    RED only when red with the build and green on the base; INHERITED when red on both (held, never
    quarantined); NO-DATA when the base run could not be read (held, never landed, never quarantined).
    Every branch is its own return line, so each mutation target occurs once."""
    if not isinstance(red_with_build, bool):
        return "NO-DATA"
    if red_with_build is False:
        return "GREEN"
    if red_on_base is None:
        return "NO-DATA"
    if not isinstance(red_on_base, bool):
        return "NO-DATA"
    if red_on_base is False:
        return "RED"
    if red_on_base is True:
        return "INHERITED"
    return "NO-DATA"


def base_tree(runner, env, tmp_root=None, rev="HEAD"):
    """rev's files (HEAD by default; the hub head for the driver's reconcile push) in a fresh temp dir, with
    GIT_INDEX_FILE and GIT_WORK_TREE set in a COPY of env so the computed paths travel through env and never
    through argv. Both argv lists are string constants but for the revision name. A runner that is not callable,
    a rev that is not a plain revision name, or a tmp_root that cannot be made a directory under, is NO-DATA
    (None) and creates nothing. A non-int or non-zero return code, or OSError/SubprocessError/ValueError,
    returns None and drops the dir."""
    if not callable(runner):
        return None
    if not isinstance(rev, str) or not rev or rev.startswith("-") or "\0" in rev:
        return None
    try:
        path = tempfile.mkdtemp(prefix="land-base-", dir=tmp_root)
    except (OSError, ValueError):
        return None
    genv = dict(env) if isinstance(env, dict) else dict(os.environ)
    genv["GIT_INDEX_FILE"] = os.path.join(path, "index")
    genv["GIT_WORK_TREE"] = path
    try:
        r1 = runner(["git", "read-tree", rev], genv)
        r2 = runner(["git", "checkout-index", "-a", "-f"], genv)
    except (OSError, subprocess.SubprocessError, ValueError):
        drop_base_tree(path)
        return None
    if isinstance(r1.returncode, bool) or not isinstance(r1.returncode, int) or r1.returncode != 0:
        drop_base_tree(path)
        return None
    if isinstance(r2.returncode, bool) or not isinstance(r2.returncode, int) or r2.returncode != 0:
        drop_base_tree(path)
        return None
    return path


def tree_digest(root, subdirs=("scripts", "docs/plan")):
    """sha256 over every entry under root's subdirs (relative path, mode and bytes, or a link's target), sorted: what a
    frozen copy's checks can import or read. None when root cannot be walked or read, which a caller reads as changed,
    never as unchanged (review 14 finding 5: the frozen copy doubles as the red neighbour's base tree, and a base rerun
    runs sandboxed with that tree as its root, so what it wrote there is measured before the copy's checks run again)."""
    h = hashlib.sha256()
    try:
        for sub in subdirs:
            for d, dirs, files in os.walk(os.path.join(root, sub)):
                dirs.sort()
                for name in sorted(files + [x for x in dirs if os.path.islink(os.path.join(d, x))]):
                    p = os.path.join(d, name)
                    h.update(os.path.relpath(p, root).encode("utf-8", "surrogateescape") + b"\0")
                    if os.path.islink(p):
                        h.update(b"link " + os.readlink(p).encode("utf-8", "surrogateescape"))
                    else:
                        h.update(("%o " % stat.S_IMODE(os.lstat(p).st_mode)).encode("ascii"))
                        with open(p, "rb") as fh:
                            h.update(fh.read())
                    h.update(b"\0")
    except (OSError, ValueError):
        return None
    return h.hexdigest()


def drop_base_tree(path):
    """Remove a base tree this module made. Refuses (returns False, deletes nothing) anything that is not a
    real directory whose basename starts with land-base-: deleting an unrecognised target is the destructive
    case. os.walk topdown=False, os.remove and os.rmdir only, never shutil."""
    if not isinstance(path, str):
        return False
    if not os.path.basename(path).startswith("land-base-"):
        return False
    if not os.path.isdir(path):
        return False
    try:
        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                os.remove(os.path.join(root, f))
            for d in dirs:   # os.walk lists a link to a directory here, and rmdir refuses a link (ENOTDIR): unlink it
                q = os.path.join(root, d)
                os.remove(q) if os.path.islink(q) else os.rmdir(q)
        os.rmdir(path)
    except OSError:
        return False
    return True


def base_run_red(tree, tp, dotted, py, runner, exists=os.path.isfile):
    """True when the neighbour test is red in the base tree, False when it is green, None when the run could
    not be read. tp is relative to the tree; the tree travels through os.chdir, never through argv. An absent
    test file in the base tree is NO-DATA, because a neighbour the base never had is not the build's fault.
    A runner or an exists that is not callable is NO-DATA too, never a crash."""
    if not isinstance(tree, str):
        return None
    if not isinstance(tp, str):
        return None
    if not isinstance(py, str):
        return None
    if not callable(runner):
        return None
    if not callable(exists):
        return None
    if not exists(os.path.join(tree, tp)):
        return None
    old = os.getcwd()
    try:
        os.chdir(tree)
        r = runner([py, "-B", "-m", "unittest", dotted] if dotted else [py, "-B", tp])
        rc = r.returncode
    except (OSError, subprocess.SubprocessError, ValueError):   # timeout, cannot start, or bad value is NO-DATA
        return None
    finally:
        os.chdir(old)
    if isinstance(rc, bool) or not isinstance(rc, int):
        return None
    return rc != 0


def gates_text(red, held):
    """The GATES line's first field. Byte for byte the old text when nothing is held, so a landing with no held
    neighbour prints what it always printed. With held alone it says HELD and never the word green. A red or
    held that is not a list of strings is refused as NO-DATA, never read as the safe green."""
    if not isinstance(red, (list, tuple)) or not isinstance(held, (list, tuple)):
        return "NO-DATA"
    if not all(isinstance(x, str) for x in red) or not all(isinstance(x, str) for x in held):
        return "NO-DATA"
    if not held:
        if not red:
            return "green"
        return "RED: " + ", ".join(red)
    if not red:
        return "HELD: " + ", ".join(held)
    return "RED: " + ", ".join(red) + " | HELD: " + ", ".join(held)


def held_reason(held):
    """The sentence that names every held neighbour and says the build is neither landed nor quarantined.
    "" when nothing is held. A held that is not a list of (name, word) pairs is refused as NO-DATA, never read
    as the empty safe case."""
    if not isinstance(held, (list, tuple)):
        return "NO-DATA (held neighbours are not a list of name and word pairs)"
    if not held:
        return ""
    parts = []
    for item in held:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return "NO-DATA (held neighbour is not a name and a word)"
        name, word = item
        if not isinstance(name, str) or not isinstance(word, str):
            return "NO-DATA (held neighbour is not a name and a word)"
        if word == "INHERITED":
            parts.append("%s INHERITED (red on the base too)" % name)
        elif word == "NO-DATA":
            parts.append("%s NO-DATA (base run unreadable)" % name)
        else:
            parts.append("%s %s" % (name, word))
    return ", ".join(parts) + "; the build is neither landed nor quarantined"


def push_verdict(push_rc, fetch_rc, local, remote, local_is_ancestor):
    """What the push actually did, as one word, so the caller never reads a network failure as "not landed".
    LANDED: the remote accepted and matches local. UNVERIFIED: the remote accepted (exit 0) but the parity fetch
    failed, so parity is NO-DATA; the commit stays, nothing is reset, the next pass verifies. FAST-FORWARD: the remote
    accepted and holds local as an ancestor (another landing reached it too); local catches up, nothing is unwound.
    NOT-LANDED: the push was refused. DIVERGED: the remote answered and holds a different history.
    Measured 2026-09-24 (backend review): a landed push followed by one failed fetch printed "did not reach hub",
    reset local and quarantined a landed build, then every later landing was refused "local is not hub"."""
    if push_rc: return "NOT-LANDED"
    if fetch_rc: return "UNVERIFIED"
    if local == remote: return "LANDED"
    if local_is_ancestor: return "FAST-FORWARD"
    return "DIVERGED"


def behind_action(head, hub_head, head_is_ancestor):
    """equal: land. fast-forward: local is strictly behind hub (an earlier landing reached hub while its parity read
    failed, or another clone landed), so local catches up with --ff-only and the landing proceeds. diverged: refuse."""
    if head == hub_head: return "equal"
    if head_is_ancestor: return "fast-forward"
    return "diverged"


def refuse_landing(why, paths, status_paths, landed, log, run=None, prev_reader=None, appended=None):
    """A refused commit scan or a failed commit must leave the tree as it found it: unstage the applied paths, revert
    them by name, quarantine the build with the reason. Measured 2026-09-24 (backend review): the refusal returned
    with staged paths in the tree, STATUS still READY, and the loop exited BLOCKED on "tree not clean" for a human.
    The plan is never checked out: only this landing's own lines leave it (revert_landing, X1 finding 3)."""
    run = run or sh; paths = sorted(paths)
    if paths: run(["git", "reset", "-q", "--"] + paths, log)
    t, c, plan_note = revert_landing(paths, log, appended)
    held = 0
    for st in status_paths[:1]:
        try:
            prev = (prev_reader or (lambda q: open(q, encoding="utf-8").read().strip()))(st)
            note = gate_quarantine(landed, status_paths, why, prev)
            if note:
                write_status(st, note)
                held += 1
        except (OSError, ValueError) as exc:
            print("note: could not quarantine %s (%s)" % (st, exc))
    return "REFUSED: %s; unstaged and reverted %d tracked and removed %d created path(s), %d build(s) quarantined, %s" % (
        why, t, c, held, plan_note[2:] if plan_note else "tree left clean")


def unwind_refusal(parent, started_from, dirty):
    """'' when a landing commit that cannot be pushed may be unwound to where this landing started, else why a
    human must look instead. One condition per line. The reset below is a destructive verb, so it runs ONLY when the
    commit being removed is provably the one this run just made: its parent is the hub head this run started from,
    and the tree holds nothing else. Anything unknown REFUSES, because the safe side of an unknown is to leave the
    tree alone and say so."""
    if not started_from: return "the hub head this landing started from is unknown"
    if not parent: return "HEAD's parent could not be read"
    if parent != started_from: return "HEAD's parent %s is not %s, the hub head this landing started from" % (parent, started_from)
    if dirty: return "tree not clean (%d paths), so a reset would discard work this landing did not make" % len(dirty)
    return ""


def unwind(started_from, why, status_paths, log, rev, run=None, changed=None):
    """After a landing commit that cannot reach hub: put the branch back on hub, KEEP the commit under a ref, and
    quarantine its builds. Returns the one line to print.

    WHY (measured 2026-09-21). A landing committed, its hermetic check then failed, and the tool printed "the commit
    is kept locally, NOT pushed" and returned. From that moment local was ahead of hub, so preflight refused EVERY
    later landing ("384a6aeed is not hub 2a4e6bdda before landing", 15 times in a row) until a human noticed. One
    bad build ended the night. A failed push leaves exactly the same state. Nothing is lost by unwinding: the
    commit stays reachable as refs/brother/unlanded/<sha>, the build json is on disk, and the STATUS says why."""
    run = run or sh; changed = changed or changed_paths
    sha = rev("HEAD"); refusal = unwind_refusal(rev("HEAD~1"), started_from, changed())
    if refusal:
        return "UNWIND REFUSED: %s. Commit %s stays local and every later landing will refuse until a human looks" % (refusal, sha)
    run(["git", "update-ref", "refs/brother/unlanded/%s" % sha, "HEAD"], log)
    if run(["git", "reset", "--hard", started_from], log).returncode != 0 or rev("HEAD") != started_from:
        return "UNWIND FAILED: the reset to %s did not take; commit %s is kept as refs/brother/unlanded/%s and a human must look" % (started_from, sha, sha)
    held = 0
    for st in status_paths:
        try:
            with open(st, encoding="utf-8") as fh: prev = fh.read().strip()
            write_status(st, "QUARANTINE refused after the commit, %s; the commit is kept as refs/brother/unlanded/%s | previous: %s\n" % (why, sha, prev))
            held += 1
        except (OSError, ValueError) as exc:
            print("        note: could not quarantine %s (%s), it may be offered again" % (st, exc))
    return "UNWOUND: branch back on hub %s, commit %s kept as refs/brother/unlanded/%s, %d build(s) quarantined, so later landings are not blocked" % (started_from, sha, sha, held)


def env_line(env=None):
    """The environment a gate ran under, for the land log: TMPDIR, HOME, PWD and every BROTHER* value in full (paths
    and switches, never secrets), the other variables by NAME only. 2026-09-24: a discover gate refused the day's only
    READY build on a selftest capture read as credential shaped, the same test passed in three sandboxes, and nothing
    in the log said what environment the gate had run under."""
    e = os.environ if env is None else env
    shown = sorted(k for k in e if k in ("TMPDIR", "HOME", "PWD") or k.startswith("BROTHER"))
    rest = sorted(k for k in e if k not in shown)
    return "env: " + " ".join("%s=%s" % (k, e.get(k, "")) for k in shown) + " | other names: " + ",".join(rest) + "\n"


def boxed(argv, log, timeout, env, cwd=None, root=None, seed=()):
    """sh() under the grader's sandbox with a throwaway HOME, rooted at cwd (default the CURRENT directory: the base tree
    base_run_red changes into) and run there. EIGHTH REVIEW 2026-10-02, reproduced: grading lets a build start processes
    because grading is contained, and then the landing imported the build's module in its neighbour tests with no
    sandbox, the network open and the real HOME. The neighbour run and its base comparison now run under the same
    profile and environment as land_apply's suites, so a red caused by the sandbox reads the same on both trees and
    never quarantines a good build. A host without the sandbox fails the step (exit 125), never runs it bare.
    ELEVENTH REVIEW 2026-10-02: the root is a disposable copy (make_copy), never the landing tree, so what a build's code
    writes there (an ignored .pyc, a symlink, a file rewritten by a child it left behind) dies with the copy.
    root: the one directory the child may write besides its HOME, when that is not cwd (review 17: a child that must READ
    the landing tree runs with cwd the tree and root a temp directory of its own, so it writes nothing there). seed: HOME
    relative files copied from the real HOME into the throwaway one (the private terms list the pre-commit check reads; the
    profile hides the real one, and the box keeps the copy off the network and out of every file outside its own temp)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import grade_build as _G, loop_switches as _LS
    home = tempfile.mkdtemp(prefix="land-neighbour-home-"); os.makedirs(os.path.join(home, "tmp"), exist_ok=True)
    root = root or cwd or os.getcwd()
    try:
        for rel in seed:
            src = os.path.join(os.path.expanduser("~"), rel)
            if os.path.isfile(src) and not os.path.islink(src): shutil.copyfile(src, os.path.join(home, rel))
        try:
            cmd = _G.sandboxed(list(argv), root, home)
        except _G.SandboxRefused as e:
            log.write("\n$ %s\n%sexit=125\nSANDBOX REFUSED: %s\n" % (" ".join(argv), env_line(env), e)); log.flush()
            return subprocess.CompletedProcess(list(argv), 125, "", "SANDBOX REFUSED: %s" % e)
        return sh(cmd, log, timeout, _LS.suite_env(env if env is not None else os.environ, home), cwd=cwd)
    finally:
        shutil.rmtree(home, ignore_errors=True)


#: (B) EVERY PYTHON LAUNCH OF TREE CODE IS ISOLATED (review 17 finding 3, executed: a build wrote scripts/json.py and the
#: frozen close_unit.py, self_check_staged.py and pre_push_gate.py imported it, because a script's own directory is the
#: FIRST sys.path entry, ahead of the standard library). `-I` drops the script directory, the user site and every PYTHON*
#: variable on 3.9 and 3.13 alike (3.9 has no -P); the script's directory is then appended AFTER the standard library,
#: so a tree module never shadows a stdlib one.
#: THE LIBRARY WINS EVERY NAME IT HAS, AND ITS OWN IMPORTS NEVER REACH THE TREE (found by scripts/test_lander_box_tripwire.py,
#: 2026-10-03): appending is not enough. The frozen scripts put their directory back at the FRONT (hermetic_test_check,
#: cut_preflight: sys.path.insert(0, HERE)), and the stdlib probes for modules this platform lacks (subprocess tries
#: _winapi, then msvcrt), a miss falling through to the script's directory. No name list can be complete, so the finder
#: below orders every search: the launch path (stdlib and site) first, whatever the script did to sys.path; and an import
#: made BY a module of the launch path searches only the launch path. The tree supplies only names the library lacks, and
#: only to the tree's own modules. Every launcher is passed as exec(<one line repr>), so the land log keeps one line per
#: command (the receipt contract reads the log line by line).
ISOLATE = "exec(%r)" % r"""import importlib.machinery as M, os, runpy, sys
s = sys.argv[1]; sys.argv = sys.argv[1:]
base = [os.path.realpath(p) for p in sys.path if p]
def under(p):
    p = os.path.realpath(p or os.getcwd())
    return any(p == a or p.startswith(a + os.sep) for a in base)
class Library(object):
    @classmethod
    def find_spec(cls, name, path=None, target=None):
        f = sys._getframe(1)
        while f is not None and str(f.f_globals.get("__name__", "")).startswith(("importlib", "_frozen_importlib")):
            f = f.f_back
        who = f.f_globals.get("__file__") if f is not None else None
        paths = sys.path if path is None else path
        lib = [p for p in paths if under(p)]
        return M.PathFinder.find_spec(name, lib if not who or under(who) else lib + [p for p in paths if not under(p)], target)
sys.meta_path = [Library if f is M.PathFinder else f for f in sys.meta_path]
sys.path.append(os.path.dirname(os.path.abspath(s)))
runpy.run_path(s, run_name="__main__")
"""


def isolated(script, args=(), py=None):
    """[py, -I, -B, -c, ISOLATE, script, *args]: script run as __main__ with its own directory after the stdlib."""
    return [py or sys.executable, "-I", "-B", "-c", ISOLATE, script] + list(args)


#: THE CLOSER IS PINNED TO THE DEPLOYED BIN (review 17 finding 2, executed: a build wrote scripts/loop/plan_store/__init__.py,
#: a package directory beats the protected plan_store.py of the same name, and the frozen close_unit.py ran it). close_unit
#: cannot run boxed: its done checks are boxed already and a sandbox cannot nest, and it commits the plan through git. So
#: the split: the frozen close_unit.py (a protected file, its bytes never a build's) runs isolated, and EVERY module it
#: imports comes from the standard library, the site directories, or this lander's own bin, the deployed copy; an import
#: that resolves under the code root or the landing tree is not found at all (a ModuleNotFoundError: CLOSE-RED, never a
#: fall back). The plan write is plan_store's from the bin, the done checks land_batch.boxed's from the bin.
CLOSER = "exec(%r)" % r"""import importlib.machinery as M, os, runpy, sys
b, s = os.path.realpath(sys.argv[1]), sys.argv[3]
deny = [os.path.realpath(sys.argv[2]), os.path.realpath(os.getcwd())]
base = [os.path.realpath(p) for p in sys.path if p]
sys.argv = sys.argv[3:]
def ok(p):
    p = os.path.realpath(p or os.getcwd())
    if p == b: return True
    if any(p == d or p.startswith(d + os.sep) for d in deny): return False
    return any(p == a or p.startswith(a + os.sep) for a in base)
class Pinned(object):
    @classmethod
    def find_spec(cls, name, path=None, target=None):
        return M.PathFinder.find_spec(name, [p for p in (sys.path if path is None else path) if ok(p)], target)
sys.meta_path = [Pinned if f is M.PathFinder else f for f in sys.meta_path]
sys.path.append(b)
runpy.run_path(s, run_name="__main__")
"""


def closer_argv(code, args=(), py=None):
    """The frozen close_unit.py under CLOSER: its imports from the stdlib and BIN only, never code or the landing tree."""
    return [py or sys.executable, "-I", "-B", "-c", CLOSER, BIN, code, os.path.join(code, "scripts", "close_unit.py")] + list(args)


def kill_group(pid):
    """SIGKILL the whole process group a child was started as the leader of (start_new_session=True), so a child it
    left behind dies with it: the group outlives its leader while any member lives, and a dead group answers
    ProcessLookupError, which is the clean case."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def descendant_groups(pid):
    """The process groups of every live descendant of pid, read from one ps listing, never this process's own group. A
    child that ran a step through run_group itself leads a NESTED group, and killing the outer group first SIGKILLs the
    parent before its own finally can kill the inner one (2026-10-03: ACC2's done check ran on for half an hour, orphaned,
    after its closer was killed at the 1800 s timeout). So the groups are collected while the tree is still whole.
    ponytail: a descendant started between this read and the kill escapes; a second read after the kill would catch it.
    An unreadable listing returns nothing: the outer kill still runs, as it did before this existed."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):   # sbe: allow-silent no listing kills only the outer group, the old behaviour
        return set()
    kids, pgid = {}, {}
    for line in out.splitlines():
        f = line.split()
        if len(f) == 3 and all(x.isdigit() for x in f):
            kids.setdefault(int(f[1]), []).append(int(f[0])); pgid[int(f[0])] = int(f[2])
    seen, todo = set(), list(kids.get(pid, []))
    while todo:
        c = todo.pop()
        if c not in seen: seen.add(c); todo.extend(kids.get(c, []))
    return {pgid[c] for c in seen if c in pgid} - {os.getpgrp(), pid}


def run_group(cmd, timeout, env, cwd=None, stdin=None):
    """subprocess.run(capture_output=True, text=True) with the child the LEADER OF ITS OWN PROCESS GROUP, and the whole
    group killed when it returns or outlives its timeout (exit 124, the reason appended to stderr), never an exception.
    ELEVENTH REVIEW 2026-10-02, reproduced: a suite started a detached child that outlived subprocess.run and rewrote a
    landed file inside the window before git add. The sandbox is what keeps such a child out of the landing tree (it
    runs in a copy); the kill is what keeps it from running at all once its parent has answered.
    stdin: text fed to the child (the push gate reads git's ref lines there); None leaves the child with this process's."""
    # output goes to FILES, never pipes (twelfth review 2026-10-02, reproduced: a detached grandchild outside the group
    # held the pipe open and communicate() after the kill waited 8 s, for ever with a background server); stdin comes
    # from a file too, so no write to a child can block either
    # decoded leniently (thirteenth review 2026-10-02): a child's non-UTF-8 bytes raised UnicodeDecodeError here, and in a
    # tree gate that would have crashed the lander with the tree dirty
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as fo, \
            tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as fe, \
            tempfile.TemporaryFile("w+", encoding="utf-8") as fi:
        if stdin is not None:
            fi.write(stdin); fi.flush(); fi.seek(0)
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=fi if stdin is not None else None, stdout=fo, stderr=fe,
                             text=True, start_new_session=True)
        rc, note = None, ""
        try:
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                nested = descendant_groups(p.pid)   # read before the outer kill reparents them
                kill_group(p.pid)
                for g in nested: kill_group(g)
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:  # sbe: allow-silent the group was SIGKILLed above; rc is set to 124 below whatever this wait says
                    pass   # it is killed and nothing it holds can block this read; its code reads 124 below
                rc, note = 124, "NO-DATA: timed out after %ss\n" % timeout
        finally:
            kill_group(p.pid)
        fo.seek(0); fe.seek(0)
        return subprocess.CompletedProcess(cmd, rc, fo.read(), fe.read() + note)


def sh(cmd, log, timeout=2400, env=None, cwd=None, stdin=None):
    # A step that outlives its timeout is a FAILED step (exit 124, the reason in its stderr), never an exception: the
    # uncaught TimeoutExpired of one slow unit done check (close_unit ACC2, 1800 s behind the heavy slot, 2026-09-30)
    # crashed the whole landing after its push, the driver read exit 1 as a refused landing, and the loop stopped.
    # Every one of this module's callers already fails closed on a nonzero returncode.
    # NO RUN KNOB REACHES A CHILD BY DEFAULT (2026-10-02 12:54): 11 callers pass no env, the push among them, so the
    # pre-push hook's hermetic suites ran under the run's BROTHER_TRANSPORTS=claude and refused RL5.a's landing on a
    # test that is green without it. A missing env is os.environ minus loop_switches.RUN_KNOBS (GIT_* kept for git).
    if env is None:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import loop_switches as _LS
        env = _LS.drop_run_knobs(dict(os.environ))
    r = run_group(cmd, timeout, env, cwd, stdin)
    log.write("\n$ %s\n%sexit=%d\n%s%s" % (" ".join(cmd), env_line(env), r.returncode, r.stdout, r.stderr)); log.flush()
    return r

#: git as the lander runs it against a tree a build's code has touched (tenth review 2026-10-02): no fsmonitor program, no
#: hooks, names never quoted
LAND_GIT = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.quotePath=false"]


class TreeUnreadable(RuntimeError):
    """git could not read the tree. An unknown tree never reads as clean (eleventh review 2026-10-02: a failed git status
    returned an empty set, which every caller took as a clean tree)."""


def changed_paths(cwd=None):
    """Every changed or untracked path, read NUL separated (tenth review 2026-10-02, reproduced: a non-ASCII name came back
    quoted and escaped, so revert could not find it and the dirty tree refused every later landing). Raises TreeUnreadable
    when git exits non-zero: the caller turns it into REFUSED, never into an empty, clean looking set. cwd: the tree read
    (default the current directory, the landing tree; the disposable copy when a gate's generator ran there)."""
    r = subprocess.run(LAND_GIT + ["status", "--porcelain=v1", "-z", "-uall"], capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise TreeUnreadable("git status exit %d: %s" % (r.returncode, (r.stderr.strip().splitlines() or ["no output"])[-1][:160]))
    out = r.stdout.split("\0")
    paths, i = set(), 0
    while i < len(out):
        e = out[i]; i += 1
        if len(e) < 4:
            continue
        paths.add(e[3:])
        if "R" in e[:2] or "C" in e[:2]:
            i += 1   # -z puts the original name of a rename or copy in the next entry
    return paths

COPY_PREFIX = "land-copy-"


def make_copy(paths=None):
    """A disposable copy of the landing tree (the current directory) for a build's code to run in: a detached git
    worktree of HEAD, plus every path the tree holds uncommitted (paths, default changed_paths()) synced in as it stands,
    so the copy reads exactly as the landing tree does, outside it. Returns the copy's root, under a fresh land-copy-*
    folder in TMPDIR; the caller drops it with drop_copy in a finally. Raises TreeUnreadable when git refuses, so nothing
    runs in the landing tree instead (eleventh review 2026-10-02: suites and neighbour tests ran in the landing tree
    under a sandbox rooted there, and what they wrote outside git's sight landed or was loaded by later gates)."""
    parent = tempfile.mkdtemp(prefix=COPY_PREFIX); root = os.path.join(parent, "tree")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    r = subprocess.run(LAND_GIT + ["worktree", "add", "-q", "--detach", root, "HEAD"], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        shutil.rmtree(parent, ignore_errors=True)
        raise TreeUnreadable("git worktree add exit %d: %s" % (r.returncode, (r.stderr.strip().splitlines() or ["no output"])[-1][:160]))
    try:
        sync_copy(root, changed_paths() if paths is None else paths)
    except (OSError, TreeUnreadable) as exc:
        drop_copy(root)
        raise TreeUnreadable("the copy could not be synced: %s" % exc)
    return root


def sync_copy(root, paths):
    """Every path in paths, as the current tree holds it now, written into root: a symlink as a symlink (never read
    through), a file byte for byte with its mode, a missing path removed. Whatever sat at the path in root goes first."""
    for p in sorted(paths):
        dst = os.path.join(root, p)
        if os.path.lexists(dst):
            if os.path.isdir(dst) and not os.path.islink(dst): shutil.rmtree(dst)
            else: os.remove(dst)
        if os.path.islink(p):
            os.makedirs(os.path.dirname(dst), exist_ok=True); os.symlink(os.readlink(p), dst)
        elif os.path.isfile(p):
            os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(p, dst)


def drop_copy(root):
    """Remove a copy make_copy made, and only that: a tree folder under a land-copy-* folder. git forgets the worktree
    (remove --force, the copy is dirty by design), the folder goes, stale registrations are pruned. Returns True when
    the path is gone; anything else is refused untouched (deleting an unrecognised target is the destructive case)."""
    if not isinstance(root, str) or os.path.basename(root) != "tree" or not os.path.basename(os.path.dirname(root)).startswith(COPY_PREFIX):
        return False
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(LAND_GIT + ["worktree", "remove", "--force", root], capture_output=True, env=env)  # sbe: allow-silent the verdict is the lexists check this function returns
    shutil.rmtree(os.path.dirname(root), ignore_errors=True)
    pr = subprocess.run(LAND_GIT + ["worktree", "prune"], capture_output=True, text=True, env=env)
    if pr.returncode != 0:   # the folder may be gone while git still registers it: said, never silent (review 2026-10-03, B2)
        sys.stderr.write("drop_copy: git worktree prune exit %d, a stale registration may remain: %s\n" % (pr.returncode, (pr.stderr or "").strip()[:200]))
    return not os.path.lexists(root)


def generated_in(copy, before):
    """{relative path: snapshot entry} for every path a gate's generator changed in the copy: new, rewritten (bytes or
    mode), replaced by a link or removed since `before`, the snapshot (root=copy) of the copy's changed paths taken before
    the generator ran. Raises TreeUnreadable when the copy cannot be read."""
    now = snapshot(sorted(changed_paths(copy) | set(before)), root=copy)
    # a path git lists only now was clean before, so it changed whatever it reads as, a removal (None) included: an
    # absent key is never read as the None a removed file snapshots to (hostile re-read 2026-10-02)
    missing = object()
    return {p: v for p, v in now.items() if before.get(p, missing) != v}


def take_generated(copy, changed, root="."):
    """What a gate's generator wrote in the copy, written into the landing tree (root) path by path, and nothing else.
    D13 (2026-10-02): the generators run in the copy under the sandbox because a build may have edited a module they
    import, so what comes back out is a build's output and is screened like a build's: only a path grade_build.dest lets
    a build write (inside the tree, never a protected file, never through a link), only a regular file's bytes and mode
    or a removal, written afresh with O_NOFOLLOW the way rollback writes. Returns (taken, refused): taken the paths now
    holding the generator's output, refused {path: why} for everything else, which the caller reads as a red gate."""
    sys.path.insert(0, BIN); import grade_build as _G
    taken, refused = [], {}
    for p, v in sorted(changed.items()):
        full, why = _G.dest(root, p)
        if why:
            refused[p] = why; continue
        if v is not None and v[0] in ("link", "dir"):
            refused[p] = "the generator left a link or a directory at %s" % p; continue
        try:
            if os.path.lexists(full):
                if os.path.isdir(full) and not os.path.islink(full):
                    refused[p] = "a directory stands at %s" % p; continue
                os.remove(full)
            if v is not None:
                os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
                fd = os.open(full, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(v[0])
                os.chmod(full, v[1])
        except OSError as exc:
            refused[p] = type(exc).__name__; continue
        taken.append(p)
    return taken, refused


#: THE HOOKS' CHECKS, RUN BY THE LANDER FROM A FROZEN COPY (D13, docs/decisions/native-run-2026-10-02.json): the installed
#: pre-commit hook runs the tree's scripts/self_check_staged.py and the pre-push hook the tree's scripts/pre_push_gate.py.
#: Both are protected, but they import unprotected scripts a build may have just edited (private_terms_scan, edition_guard,
#: cut_preflight, export_public among 22 measured on 2026-10-02), so the build's code ran unsandboxed at the commit and the
#: push. The lander runs the same two checks itself, the same way the hooks do (cwd the landing tree, git's own ref line on
#: stdin), from base_tree: the base commit's files, reviewed and landed before this batch, reading the landing tree as their
#: data; git is then told to run no hook (LAND_GIT). A frozen copy that lacks a check refuses the step (NO_SCRIPT), never
#: runs the tree's copy instead.
PRECOMMIT_CHECK = os.path.join("scripts", "self_check_staged.py")
PUSH_GATE = os.path.join("scripts", "pre_push_gate.py")
TERMS_FILE = ".brothersbe-private-names"   # HOME relative: the private terms list self_check_staged reads (private_terms_scan)
NO_SCRIPT = 125
FROZEN = []   # the frozen copies main() made, dropped by run_main whatever happens


def frozen_argv(frozen, rel):
    """isolated(<frozen>/<rel>) when the frozen copy holds the script as a regular file (never a link), else None."""
    if not isinstance(frozen, str) or not frozen:
        return None
    p = os.path.join(frozen, rel)
    if os.path.islink(p) or not os.path.isfile(p):
        return None
    return isolated(p)


def no_script(frozen, rel):
    """The refusal a missing frozen check answers with: (argv it would have run, a completed process at NO_SCRIPT)."""
    argv = isolated(os.path.join(str(frozen), rel))
    return argv, subprocess.CompletedProcess(argv, NO_SCRIPT, "", "NO-DATA: the frozen copy holds no %s, so the check did not run; that is not a pass\n" % rel)


def precommit_check(frozen, log):
    """(argv, result) of the frozen scripts/self_check_staged.py over the staged diff of the current directory, exactly as
    the installed pre-commit hook runs it (no flag: the script reads `git diff --cached` where it is run). Exit 2 is the
    script's own NO-DATA (its term list unreadable), refused like any other nonzero code.
    BOXED AND ISOLATED (review 17 finding 3): cwd the landing tree, which it only reads; it writes only its own temp
    directory, with no network, and its HOME holds a copy of the private terms list it scans with (seed). A host that
    cannot box answers 125 and the commit is refused, never run bare."""
    argv = frozen_argv(frozen, PRECOMMIT_CHECK)
    if argv is None:
        return no_script(frozen, PRECOMMIT_CHECK)
    out = tempfile.mkdtemp(prefix="precommit-box-")
    try:
        return argv, boxed(argv, log, 600, None, cwd=os.getcwd(), root=out, seed=(TERMS_FILE,))
    finally:
        shutil.rmtree(out, True)


def ref_lines(remote, branch, rev_=None):
    """git's pre-push stdin for `git push <remote> HEAD:<branch>`: one line, local ref (HEAD, the refspec's source as git
    names it), local sha, remote ref, remote sha. None when HEAD cannot be read (the gate would read a zero local sha as a
    deletion and scan nothing). A remote sha this checkout cannot read is 40 zeros, which the gate reads as a ref the
    remote lacks and scans everything not on any remote: a wider range, never a narrower one."""
    rev_ = rev_ or (lambda r: subprocess.run(["git", "rev-parse", "--verify", "--quiet", r + "^{commit}"], capture_output=True, text=True).stdout.strip())
    local = rev_("HEAD")
    if not local:
        return None
    return "HEAD %s refs/heads/%s %s\n" % (local, branch, rev_("%s/%s" % (remote, branch)) or "0" * 40)


def push_gate(frozen, remote, branch, log, sh_=None, rev_=None, url=None):
    """(argv, result) of the frozen scripts/pre_push_gate.py over the current directory, as scripts/pre_push_hook.sh runs it
    before `git push <remote> HEAD:<branch>`: --cwd this tree, the remote's name and push URL, git's ref line on stdin. A
    frozen copy without it, or a HEAD this checkout cannot read, answers NO_SCRIPT and runs nothing."""
    argv = frozen_argv(frozen, PUSH_GATE)
    lines = ref_lines(remote, branch, rev_)
    if argv is None or lines is None:
        return no_script(frozen, PUSH_GATE)
    if url is None:
        got = subprocess.run(["git", "remote", "get-url", "--push", remote], capture_output=True, text=True)
        url = got.stdout.strip() if got.returncode == 0 else ""
    argv += ["--cwd", os.getcwd(), "--remote-name", remote, "--remote-url", url]
    return argv, (sh_ or sh)(argv, log, 2400, None, None, lines)


def shared_config_digest():
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True).stdout.strip()
    h = hashlib.sha256()
    for f in (os.path.join(common, "config"), os.path.join(common, "config.worktree")):
        h.update(open(f, "rb").read() if os.path.isfile(f) else b"<absent>")
    return h.hexdigest()[:12]

def revert(paths, log):
    """Undo exactly the paths this run applied, by name. Nothing is lost: each change came from a build JSON that
    still sits on disk, so the work is re-appliable. Leaving them instead halts an unattended loop on a dirty tree
    it is not allowed to clean (seen 2026-09-21: three passes in a row named CLEAN-TREE and stopped)."""
    tracked, created = [], []
    for p in sorted(paths):
        if subprocess.run(["git", "ls-files", "--error-unmatch", p], capture_output=True).returncode == 0:
            tracked.append(p)
        elif os.path.isfile(p):
            created.append(p)
    if tracked: sh(["git", "checkout", "--"] + tracked, log)
    for p in created:
        try: os.remove(p)
        except OSError: pass
    return len(tracked), len(created)


def revert_landing(paths, log, appended, plan_path=PLAN):
    """Undo a refused landing that has appended its evidence: every applied path by name, EXCEPT the plan. Other writers
    append to the plan under its lock while the gates run, and a blanket checkout of it erased their evidence and left a
    clean tree that hid the loss (X1 finding 3, 2026-09-27). The plan gets back only this landing's own lines, taken back
    under the same lock by plan_store.undo_appended (appended: {unit id: (evidence re-read, evidence written[, owns
    added])}, filled by landing_records), and so are the owns entries it added. Returns (tracked, created, note): note is '' when the plan reads as committed, else what it still
    holds and why. FAIL DIRECTION: lines that cannot be taken back stay, named; nothing is committed either way."""
    paths = set(paths)
    t, c = revert(paths - {plan_path}, log)
    if plan_path not in paths:
        return t, c, ""
    try:
        sys.path.insert(0, BIN)
        import plan_store
        shown = subprocess.run(["git", "show", "HEAD:" + plan_path], capture_output=True)
        missed = plan_store.undo_appended(plan_path, appended or {}, committed=shown.stdout if shown.returncode == 0 else None)
    except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return t, c, "; the plan keeps this landing's evidence (%s: %s), a human must take it out" % (type(exc).__name__, str(exc)[:80])
    if missed:
        return t, c, "; the plan keeps this landing's evidence for %s, rewritten since it was appended, a human must take it out" % ", ".join(missed)
    if subprocess.run(["git", "diff", "--quiet", "--", plan_path], capture_output=True).returncode != 0:
        return t, c, "; the plan keeps what other writers added under its lock meanwhile (or a build's own edit to it), never checked out over it"
    return t, c, ""


def load_json(p):
    try: return json.load(open(os.path.expanduser(p), encoding="utf-8"))
    except (OSError, ValueError): return None

def selftest():
    """Answer the question even when a case RAISES. Measured 2026-09-22: eleven selftests in this
    directory exited 1 with a bare traceback and no verdict, so a pipeline reading the exit code and
    a human reading the text described the same run differently. Cases are built EAGERLY, so one
    raising expression takes the whole run with it; this wrapper is what turns that into a readable
    refusal. It does not make a broken module pass: it still returns non zero."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    global boxed
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import plan_store; _landed = plan_store.sub_landed
    plan = {"units": [{"id": "D4", "sub_units": ["D4.c", "D4.d"], "evidence": "D4.d landed 2026-09-20"}, {"id": "D12", "sub_units": ["D12.A"], "evidence": ""}]}
    p = "/x/D4.c-1/round2/out/D4.c-r1-build.json"; ok = {"D4": {"state": "FIX-FIRST"}, "D12": {"state": "DO-NOT-BUILD"}}; s9 = {"D4.c": {"score": 9}, "D12.A": {"score": 10}, "D4.d": {"score": 10}}
    class _R:
        def __init__(self, rc, out=""): self.returncode, self.stdout, self.stderr = rc, out, ""
    _calls = []
    def _sh(argv, log, *a):
        _calls.append(list(argv))
        if any(str(x).endswith("close_unit.py") for x in argv) and argv[-1] == "--dry": return _R(1, "CLOSED  0 unit(s): none\n")
        if any(str(x).endswith("close_unit.py") for x in argv): return _R(1 if argv[-1] == "RED" or argv[-1].endswith("close_unit.py") else 0, "DONE %s: done check passed\n" % argv[-1])
        return _R(0)
    import tempfile
    def _no_root(fn):
        # the live run's shape: no BROTHER_CODE_ROOT and no proof phase, whatever this selftest was started under
        saved = {k: os.environ.pop(k) for k in ("BROTHER_CODE_ROOT", "BROTHER_PROOF_PHASE") if k in os.environ}
        try: return fn()
        finally: os.environ.update(saved)
    _frozen = tempfile.mkdtemp(prefix="land-base-selftest-")   # a frozen copy holding both hook checks, so the closure may commit and push
    for _rel in (PRECOMMIT_CHECK, PUSH_GATE):
        os.makedirs(os.path.dirname(os.path.join(_frozen, _rel)), exist_ok=True); open(os.path.join(_frozen, _rel), "w").write("import sys\nsys.exit(0)\n")
    _git = lambda word: [c for c in _calls if c[:1] == ["git"] and word in c]
    # the closure's push gate reads HEAD where it runs (ref_lines), so these cases run inside the selftest's own clone below,
    # never in whatever directory the selftest was started from (a scratch copy with no checkout is a legitimate one)
    _d = tempfile.mkdtemp(prefix="upstream-"); _g = lambda *a: subprocess.run(["git"] + list(a), capture_output=True, text=True, timeout=60)
    _bare = os.path.join(_d, "r.git"); _clone = os.path.join(_d, "c"); _g("init", "-q", "--bare", "-b", "main", _bare); _g("clone", "-q", _bare, _clone)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")): _g("-C", _clone, "config", k, v)
    _g("-C", _clone, "commit", "-q", "--allow-empty", "-m", "a"); _g("-C", _clone, "push", "-q", "-u", "origin", "main")
    # the pre-commit check is boxed (review 17): recorded here like _sh, so the selftest needs no sandbox
    _cwd = os.getcwd(); _boxed = boxed
    boxed = lambda argv, log, *a, **k: (_calls.append(["BOXED"] + list(argv)), _R(0))[1]
    try:
        os.chdir(_clone); _lines = closing_pass(["D5", "RED"], _sh, None, "hub", "b", frozen=_frozen)
    finally:
        os.chdir(_cwd); boxed = _boxed
    cases = [("a green done check closes the unit, commits the plan and pushes, both with hooks off and each after its frozen check; a red one prints CLOSE-RED and commits nothing",
              _lines[0].startswith("CLOSED  D5") and _lines[1].startswith("CLOSE-RED RED") and len(_lines) == 2 and len(_git("commit")) == 1 and len(_git("push")) == 1
              and all(c[:len(LAND_GIT)] == LAND_GIT for c in _git("commit") + _git("push"))
              and sum(1 for c in _calls if c[:1] == ["BOXED"] and c[-1] == os.path.join(_frozen, PRECOMMIT_CHECK) and "-I" in c) == 1
              and sum(1 for c in _calls if os.path.join(_frozen, PUSH_GATE) in c and "--cwd" in c and "-I" in c) == 1
              and any(c[-1] == "--dry" and CLOSER in c and "-I" in c for c in _calls)   # the sweep lists, it never reruns (2026-10-03)
              and not any(c[-1].endswith("close_unit.py") for c in _calls)),
             ("no unit to close does nothing", closing_pass([], _sh, None, "hub", "b") == [] and closing_pass(None, _sh, None, "hub", "b") == []),
             ("a closure with no frozen copy and no BROTHER_CODE_ROOT is CLOSE-RED at the code root (review 15: never the landing tree) and runs nothing",
              _no_root(lambda: closing_pass(["D5"], _sh, None, "hub", "b", frozen=None))[0].startswith("CLOSE-RED D5: the code root is refused (inside the lander the code root is never the landing tree")
              and len(_git("commit")) == 1),
             ("precommit_check and push_gate refuse a frozen copy without the script at NO_SCRIPT, a nonzero code, running nothing",
              NO_SCRIPT != 0 and precommit_check("/nonexistent", None)[1].returncode == NO_SCRIPT and push_gate("/nonexistent", "hub", "b", None, _sh)[1].returncode == NO_SCRIPT
              and not any("/nonexistent" in " ".join(c) for c in _calls)),
             ("ref_lines is git's own pre-push line, zeros for a remote this checkout cannot read, None when HEAD cannot be read",
              ref_lines("hub", "b", lambda r: {"HEAD": "a" * 40}.get(r, "")) == "HEAD %s refs/heads/b %s\n" % ("a" * 40, "0" * 40)
              and ref_lines("hub", "b", lambda r: "") is None),

        ("undeclared_outputs keeps what a gate declared (exact path or prefix, case folded) and names the rest, a non string included",
         undeclared_outputs(["SYSTEM.md", "Bundle/Runtime/x.py", "scripts/x.py", None], ("system.md", "bundle/runtime/")) == [None, "scripts/x.py"]
         and undeclared_outputs(["bundle/runtime.py"], ("bundle/runtime/",)) == ["bundle/runtime.py"] and undeclared_outputs([], ()) == []),
        ("ready lands", gate(p, "READY %s (round 2)" % p, ok, s9, plan) == ""),
        ("unprobed held", gate(p, "READY-UNPROBED %s" % p, ok, s9, plan) != ""),
        ("other file held", gate(p, "READY /x/other-r0-build.json", ok, s9, plan) != ""),
        ("missing status held", gate(p, "", ok, s9, plan) != ""),
        ("council hold", "DO NOT BUILD" in gate("/x/D12.A-r0-build.json", "READY /x/D12.A-r0-build.json", ok, s9, plan)),
        ("unreadable council held", gate(p, "READY " + p, None, s9, plan) != ""),
        ("score 7 held, under the floor the pool admits at", gate(p, "READY " + p, ok, {"D4.c": {"score": 7}}, plan) != ""),
        ("score 8 lands: the landing floor is the pool's floor (8)", gate(p, "READY " + p, ok, {"D4.c": {"score": 8}}, plan) == ""),
        ("bool score held", gate(p, "READY " + p, ok, {"D4.c": {"score": True}}, plan) != ""),
        ("unscored held", gate(p, "READY " + p, ok, {}, plan) != ""),
        ("already landed held", "already" in gate("/x/D4.d-r0-build.json", "READY /x/D4.d-r0-build.json", ok, s9, plan)),
        ("unknown sub held", gate("/x/Z9.z-r0-build.json", "READY /x/Z9.z-r0-build.json", ok, s9, plan) != ""),
        ("evidence reads as landed by the one landed test, plan_store.sub_landed", _landed("D4.c", evidence_line("D4.c", "2026-09-20", ["scripts/test_a.py 9 OK"], 0, "abc"))),
        ("register inserts before anchor", register("a\n" + ANCHOR + "\n", ["scripts/test_new_x.py"]) == 'a\nrun_check "new-x-self" python3 scripts/test_new_x.py\n' + ANCHOR + "\n"),
        ("register is idempotent", register("python3 scripts/test_new_x.py\n" + ANCHOR, ["scripts/test_new_x.py"]) == "python3 scripts/test_new_x.py\n" + ANCHOR),
        ("register without anchor refuses", register("a\n", ["scripts/test_new_x.py"]) is None),
        # the second word IS the build path here, so ONLY the READY check can refuse it: without that check the
        # quarantine would be ignored and the loop would livelock re-applying a build that always drops
        ("a quarantined status is refused on the next pass", gate("/x/D4.c-1/round2/out/D4.c-r1-build.json",
            "QUARANTINE /x/D4.c-1/round2/out/D4.c-r1-build.json dropped at landing, fuzz crashes 6",
            {"D4": {"state": "FIX-FIRST"}}, {"D4.c": {"score": 10}},
            {"units": [{"id": "D4", "sub_units": ["D4.c"], "evidence": ""}]}) != ""),
        ("stray path is named", unattributed({"a.py", "junk.tmp"}, {"a.py"}) == ["junk.tmp"]),
        ("a name build_name writes is the name sub_of reads back, and nothing wider is read",
         sub_of("/x/" + build_name("D4.c", 3)) == "D4.c" and sub_of("/x/D4.c-finisher-build.json") is None),
    ]
    try:
        os.chdir(_clone); _with = upstream(); _g("-C", _clone, "branch", "--unset-upstream"); _without = upstream()
    finally:
        os.chdir(_cwd)
    cases.append(("upstream() reads (remote, branch) from the checkout, and None with no upstream", _with == ("origin", "main") and _without is None))
    # the land log names the gate's environment: values for TMPDIR, HOME, PWD and BROTHER*, names only for the rest
    _el = env_line({"TMPDIR": "/t", "HOME": "/h", "BROTHERDS_VAULT": "/v", "OPENROUTER_API_KEY": "sk-secret-value-here", "PATH": "/bin"})
    cases.append(("env_line shows TMPDIR, HOME and BROTHER* values and never another value", "TMPDIR=/t" in _el and "BROTHERDS_VAULT=/v" in _el and "sk-secret" not in _el and "OPENROUTER_API_KEY" in _el))
    import io as _io
    _buf = _io.StringIO(); sh([sys.executable, "-c", "print(1)"], _buf, env=dict(os.environ, TMPDIR="/t"))
    cases.append(("sh writes the env line under the command it ran", "env: " in _buf.getvalue() and "TMPDIR=/t" in _buf.getvalue()))
    _usd_dir = tempfile.mkdtemp(prefix='usd-empty-')
    _usd_missing = os.path.join(_usd_dir, 'missing.jsonl')
    cases.append(('usd_text: an empty temp runs dir is NO-DATA and never 0.00', usd_text('D4.c', _usd_dir, _usd_missing, _usd_missing) == 'usd NO-DATA' and '0.00' not in usd_text('D4.c', _usd_dir, _usd_missing, _usd_missing)))
    cases.append(('usd_text: hostile sub types are NO-DATA', all(usd_text(s, _usd_dir, _usd_missing, _usd_missing) == 'usd NO-DATA' for s in (None, 5, ''))))
    # RETRY ALONE FROM EVERY REFUSAL POINT (2026-09-28): a batch is retried build by build and never quarantined whole
    _seen = []
    _rc = retry_alone(['A.1', 'B.1'], ['/x/A.1-r0-build.json', '/x/B.1-r0-build.json'], no_bisect=False,
                      run=lambda g: (_seen.append(g), 0 if 'B.1' in g[0] else 1)[1])
    cases.append(('retry_alone: a refused batch of two is retried one build at a time, and one landing is success',
                  _rc == 0 and _seen == [['/x/A.1-r0-build.json'], ['/x/B.1-r0-build.json']]))
    cases.append(('retry_alone: a single build is not retried (its blame is unambiguous)',
                  retry_alone(['A.1'], ['/x/A.1-r0-build.json'], no_bisect=False, run=lambda g: 0) == 1))
    cases.append(('retry_alone: a retry never retries again (--no-bisect)',
                  retry_alone(['A.1', 'B.1'], ['/x/a', '/x/b'], no_bisect=True, run=lambda g: 0) == 1))
    cases.append(('retry_alone: every retry refused is a refusal',
                  retry_alone(['A.1', 'B.1'], ['/x/a', '/x/b'], no_bisect=False, run=lambda g: 1) == 1))
    cases.append(('blame_paths: a batch quarantines nobody after the commit, one build is blamed alone',
                  blame_paths(['A.1', 'B.1'], ['s1', 's2'], no_bisect=False) == []
                  and blame_paths(['A.1'], ['s1'], no_bisect=False) == ['s1']
                  and blame_paths(['A.1', 'B.1'], ['s1', 's2'], no_bisect=True) == ['s1', 's2']))
    _ll = landed_line(['D4.c', 'D4.d'], 'D4', '/tmp/log', {'D4.c': 'usd 0.03', 'D4.d': 'usd NO-DATA'})
    cases.append(('landed_line: per-sub usd sits after units and before log, old head intact', _ll.startswith('LANDED  D4.c, D4.d | units with every sub unit landed (run the unit done-check): D4 | ') and 'D4.c usd 0.03' in _ll and 'D4.d usd NO-DATA' in _ll and _ll.endswith('| log /tmp/log')))
    cases.append(('landed_line: an empty landed list is refused', landed_line([], 'D4', '/tmp/log', {}) == 'REFUSED: landed_line got no sub unit names'))
    cases.append(('mark_landed refuses a None or wrong type argument list by returning no paths', mark_landed(None, [], 'abc') == [] and mark_landed([], None, 'abc') == [] and mark_landed(5, [1], 'abc') == [] and mark_landed([1], 5, 'abc') == []))
    cases.append(("H1.b neighbour_verdict: red with the build and green on the base is RED; red on both is INHERITED; a base that cannot be read is NO-DATA; a None base or a bool-as-int base is never RED",
                  neighbour_verdict(True, False) == "RED" and neighbour_verdict(True, True) == "INHERITED"
                  and neighbour_verdict(True, None) == "NO-DATA" and neighbour_verdict(True, 0) == "NO-DATA"
                  and neighbour_verdict(True, 1) == "NO-DATA" and neighbour_verdict(True, "x") == "NO-DATA"
                  and neighbour_verdict(None, False) == "NO-DATA" and neighbour_verdict(False, None) != "RED"))
    cases.append(("H1.b gates_text refuses a red or held that is not a list of strings and never reads them as green",
                  gates_text([], []) == "green" and gates_text(None, []) == "NO-DATA" and gates_text([], None) == "NO-DATA"
                  and gates_text([], 5) == "NO-DATA" and gates_text([1], []) == "NO-DATA"))
    cases.append(("H1.b held_reason refuses hostile input and never reads it as the empty safe case",
                  held_reason([]) == "" and held_reason(None).startswith("NO-DATA") and held_reason([1, 2, 3]).startswith("NO-DATA")
                  and held_reason([("a", "INHERITED"), ("b", "NO-DATA")]).endswith("neither landed nor quarantined")))
    bad = [n for n, good in cases if not good]
    import io
    _log = io.StringIO()
    try:
        _slow = sh([sys.executable, "-c", "import time; time.sleep(5)"], _log, timeout=1)
        _timed = (_slow.returncode == 124 and "timed out after 1s" in _slow.stderr and "exit=124" in _log.getvalue())
    except Exception:
        _timed = False
    cases.append(("sh turns a step that outlives its timeout into exit 124 with the reason, never an exception", _timed))
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0

NOT_CLOSED_RE = re.compile(r"^(\S+)\s+NOT CLOSED: (.*)$")


#: The failure ledger record_close_red appends to (failure_ledger.LEDGER, the estate ledger the next brief's lessons read).
FAILURE_LEDGER = os.path.expanduser("~/.claude/evidence/brother-failures.jsonl")
LEDGER_CLASS = re.compile(r"[A-Za-z0-9_.:-]{1,80}")


def record_close_red(uid, why, log, frozen=None, ledger=None):
    """File one refused closure in the failure ledger under the close stage. Never raises and never changes the pass:
    a ledger that cannot record is said in the pass output's log, the closure stays red either way.
    SPLIT, review 17 (2026-10-03): the ledger script is tree code (code_root(frozen), never the landing tree, review 15),
    so it runs isolated under boxed(), recording into a ledger file inside its own temp directory; that is the one thing
    it decides, the CLASS its taxonomy names. This trusted lander reads that one row back, keeps only a class of the
    ledger's own shape, and appends the row itself, with its own time, detail and unit. Anything else records nothing."""
    detail = ("%s NOT CLOSED: %s" % (uid, why))[:400]
    try:
        script = os.path.join(code_root(frozen), "scripts", "failure_ledger.py")
        box = tempfile.mkdtemp(prefix="close-red-")
        try:
            row = os.path.join(box, "ledger.jsonl")
            r = boxed(isolated(script, ["record", "auto:close", detail, "--unit", uid, "--sub", uid, "--path", row]), log, 60, None, cwd=box)
            with open(row, encoding="utf-8") as fh:
                rows = [l for l in fh.read().splitlines() if l.strip()]
            cls = json.loads(rows[0]).get("class") if r.returncode == 0 and len(rows) == 1 else None
        finally:
            shutil.rmtree(box, True)
        if not isinstance(cls, str) or not LEDGER_CLASS.fullmatch(cls):
            return False
        path = ledger or FAILURE_LEDGER
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), "class": cls, "detail": detail, "unit": uid, "sub": uid}) + "\n")
        return True
    except Exception:   # sbe: allow-silent the refusal is already printed as CLOSE-RED; a bookkeeping failure never blocks a landing
        return False


#: ONE CLOSURE ATTEMPT PER UNIT, DONE CHECK AND TREE (owner 2026-10-03 23:0x). A done check that did not close a unit is
#: recorded here with the digests of its check and of the tree it ran on, and closing_pass does not run it again until one of
#: them changes; a check that timed out CLOSE_TIMEOUT_CAP times on the same check text waits for a person whatever the tree.
CLOSE_ATTEMPTS = os.path.expanduser("~/.claude/evidence/close-attempts.jsonl")
CLOSE_TIMEOUT_CAP = 2
RUNNING_RE = re.compile(r"^(\S+)\s+running its done_check: ")
CHECK_RAN_RE = re.compile(r"^the done_check exited \d+: ?(.*)$", re.S)
#: close_unit's own answers when its done check never started (close_unit.run_done_check): matched by prefix, since the
#: quote is cut at 120 characters and can lose the trailing "nothing ran"
NOTHING_RAN = ("NO-DATA: no disposable copy", "NO-DATA: the done check cannot run confined")


def check_ran(rc, why):
    """True when a done check really ran and answered (review 2026-10-03, findings 2 and round 2 finding 2): a timeout, or
    close_unit's 'the done_check exited N' unless its answer is one of close_unit's own nothing-ran answers. A check's own
    NO-DATA (ACC2's "a run printed no summary line", after both gate runs) did run and is recorded. A screen refusal, a
    missing copy or sandbox, an interpreter that could not start, or a traceback ran no check and records nothing.
    ponytail: a timeout counts toward the cap even if the closer hung before the check began; its "running" line is in a
    buffer the SIGKILL discards, so the output cannot tell the two apart."""
    m = CHECK_RAN_RE.match(why)
    return rc == 124 or bool(m and not m.group(1).startswith(NOTHING_RAN) and not re.match(r"\S+ could not run: ", m.group(1)))


def close_identity(uid, plan_path):
    """(check digest, tree digest) for a unit's closure in the current directory, or None when either cannot be read or the
    tree is dirty. The tree is ALL of HEAD, the plan file included (review 2026-10-03 finding 1: donecheck_L5.py reads only
    the plan, so a sibling's closure is a changed input). A dirty tree has no identity, because the closer's copy carries
    the uncommitted paths HEAD does not name. None is never a match: the check runs, as it did before this record existed."""
    try:
        with open(plan_path, encoding="utf-8") as fh:
            check = next(u for u in json.load(fh)["units"] if u.get("id") == uid)["done_check"].strip()
        if changed_paths(): return None
        r = subprocess.run(LAND_GIT + ["rev-parse", "HEAD^{tree}"], capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip(): return None
        return hashlib.sha256(check.encode()).hexdigest()[:16], r.stdout.strip()[:16]
    except Exception:   # sbe: allow-silent an unreadable plan or tree has no identity, and no identity runs the check
        return None


def prior_attempt(uid, ident, path=None):
    """Why this unit's done check is NOT run again, or None. A row that cannot be parsed is skipped, never a match."""
    rows = []
    try:
        with open(path or CLOSE_ATTEMPTS, encoding="utf-8") as fh:
            for l in fh:
                try: rows.append(json.loads(l))
                except ValueError: continue
    except FileNotFoundError:   # sbe: allow-silent no record yet: nothing was attempted, and the check runs
        return None
    except OSError as exc:   # unreadable is not absent: the check still runs (the safe direction), and the guard's absence is said
        print("CLOSE-ATTEMPTS NO-DATA: %s unreadable (%s); the one attempt guard is off for %s this pass" % (path or CLOSE_ATTEMPTS, exc, uid))
        return None
    mine = [r for r in rows if isinstance(r, dict) and r.get("unit") == uid and r.get("check") == ident[0]]
    same = [r for r in mine if r.get("tree") == ident[1]]
    if same:
        r = same[-1]
        return "its done check already ran on this tree and check at %s (exit %s); not rerun until the tree or the check changes" % (
            time.strftime("%H:%M", time.localtime(r.get("at") or 0)), r.get("rc"))
    timeouts = [r for r in mine if r.get("rc") == 124]
    if len(timeouts) >= CLOSE_TIMEOUT_CAP:
        return ("its done check timed out %d times (cap %d) and waits for a person: with the loop stopped, run python3 scripts/close_unit.py "
                "%s then ~/.claude/bin/land_batch.py --push, or delete its rows from %s" % (len(timeouts), CLOSE_TIMEOUT_CAP, uid, path or CLOSE_ATTEMPTS))
    return None


def record_attempt(uid, ident, rc, why, path=None):
    """One row per closure that did not close. No identity records nothing (the next pass runs the check again)."""
    if not ident: return False
    try:
        path = path or CLOSE_ATTEMPTS
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": time.time(), "unit": uid, "check": ident[0], "tree": ident[1], "rc": rc, "why": why[:160]}) + "\n")
        return True
    except OSError:   # sbe: allow-silent the refusal is already printed as CLOSE-RED; a lost row costs one rerun, never a closure
        return False


def closing_pass(closed, sh_, log, remote, branch, plan_path="docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json", frozen=None, rev_=None):
    """THE CLOSING PASS (owner 2026-09-23 06:0x): a unit whose last sub unit just landed is closed NOW by its own done check
    through scripts/close_unit.py, and the closure is committed and pushed by this same writer. A red done check prints
    CLOSE-RED with the check's last line and the unit stays open; a refused scan restores the plan file; a refused push gate
    or push UNWINDS the closure commit the way a refused landing is unwound (kept under refs/brother/unlanded, the branch
    back on its parent), so the lander never leaves local ahead of hub (review 14 finding 3, 2026-10-02: a closure commit
    left local made the driver's reconcile push run the tree's installed hooks over just landed code). Closing was a manual
    step that kept being deferred (one closure in a night against sixty landings). Returns the lines to print.
    frozen: the base commit's copy the pre-commit check and the push gate run from (D13); the closure's commit and push run
    the hooks' checks the way the landing's do, and git runs no hook. No frozen copy closes nothing and says so.
    rev_: short sha reader for the unwind (default git rev-parse --short in the current directory)."""
    out = []
    rev_ = rev_ or (lambda r: subprocess.run(["git", "rev-parse", "--short", r], capture_output=True, text=True).stdout.strip())
    # EVERY ELIGIBLE UNIT, not only this landing's (defect seen 07:0x: D5 and U8 had every sub unit landed before the pass was
    # deployed and stayed open). close_unit.py with no name closes every unit whose sub units are all landed and not DONE.
    if not closed: return out
    named = [u for u in dict.fromkeys(closed) if u != "--all-eligible"]
    # close_unit runs from the frozen code root and the scan from this file's own bin (U3, B5-08): both used to run the
    # landing tree's copies, relative to the cwd. A refused code root closes nothing and says so. Outside a proof the
    # code root is the frozen copy of the base commit (review 15 finding 2): the landing tree's close_unit.py, and the
    # plan_store, provenance and plan_lint it imports, are a build's writes until the next landing reviews them.
    try:
        code = code_root(frozen)
    except Exception as exc:   # model_router.Refused, or no model_router beside this file: nothing runs
        return ["CLOSE-RED %s: the code root is refused (%s); nothing was closed" % (closed[0], str(exc)[:120])]
    said_red = set()

    def red(uid, why):
        if uid in said_red: return   # one CLOSE-RED and one ledger row per unit per pass, however many closer runs said it
        said_red.add(uid); out.append("CLOSE-RED %s: %s" % (uid, why[:160])); record_close_red(uid, why, log, frozen)
    # EVERY ELIGIBLE UNIT, ONCE (owner 2026-10-03): the sweep LISTS the eligible units with --dry, which runs no done check,
    # and each one joins the queue after the named ones. It used to be a second closer run with no name, which ran every
    # unit this pass had just named again: ACC2's check timed out at 1800 s and was started again at once, unchanged.
    d = sh_(closer_argv(code, ["--dry"]), log, 600)
    dry = ((d.stdout or "") + (d.stderr or "")).strip().splitlines()
    queue = list(named)
    # A SCOPED RUN'S SWEEP OBEYS ITS SCOPE (owner 2026-10-04): a ^(CV1|MG1|PR1)$ run spent its first pass on ACC2's 30
    # minute check. Units the pass NAMED are closed as before; an eligible unit the run's scope does not admit (the same
    # re.match of BROTHER_SCOPE the pool and the lander use) is left to an unscoped closer (an unscoped run, or
    # close_unit.py by hand) and named once; its refusal reason (a CLOSE-RED and ledger row) then comes only from that
    # unscoped closer. No BROTHER_SCOPE: release wide, as before. A scope that is not a regular
    # expression sweeps nothing and says so.
    src = os.environ.get("BROTHER_SCOPE") or ""
    try:
        admits = re.compile(src).match if src else (lambda uid: True)
    except re.error:
        admits = None
        out.append("CLOSE-RED --all-eligible: BROTHER_SCOPE %r is not a regular expression; the sweep closed nothing" % src[:80])
    left = []
    for l in dry:
        m, run_ = NOT_CLOSED_RE.match(l), RUNNING_RE.match(l)
        uid = (m or run_).group(1) if (m or run_) else None
        if uid and uid not in named and (admits is None or not admits(uid)):
            if uid not in left: left.append(uid)
            continue
        if m and m.group(1) not in named: red(m.group(1), m.group(2))
        if run_ and run_.group(1) not in queue: queue.append(run_.group(1))
    if left and admits is not None:
        out.append("CLOSE-SCOPE %d eligible unit(s) outside this run's scope %s, left to an unscoped closer: %s" % (len(left), src[:60], ", ".join(left[:12])))
    if d.returncode not in (0, 1) or not any(l.startswith("CLOSED ") or l.startswith("nothing to close") for l in dry):
        out.append("CLOSE-RED --all-eligible: the sweep could not list the eligible units (exit %d: %s)" % (d.returncode, (dry or ["no output"])[-1][:120]))
    for u in queue:
        ident = close_identity(u, plan_path)
        held = prior_attempt(u, ident) if ident else None
        if held:
            out.append("CLOSE-SKIP %s: %s" % (u, held)); continue
        # the run's own routing never reaches the done check: close_unit.done_check_env drops it for every closer (2026-10-02)
        # PINNED (review 17 finding 2): the closer's every import from the stdlib and this bin, never the code root's tree
        r = sh_(closer_argv(code, [u]), log, 1800)
        said = ((r.stdout or "") + (r.stderr or "")).strip().splitlines() or ["no output"]
        # THE REASON IS THE PRODUCT, NOT THE SUMMARY (owner 2026-10-01, "this should always be captured by brother"): the
        # last line is close_unit's own "CLOSED 0 unit(s): none", so quoting it dropped every unit's NOT CLOSED reason, and
        # the --all-eligible path dropped them with a bare continue: 22 silent rows in one night. Each refusal is quoted
        # per unit and recorded in the failure ledger, where the next brief's lessons read it.
        reds = [(m.group(1), m.group(2)) for m in (NOT_CLOSED_RE.match(l) for l in said) if m]
        if r.returncode != 0:
            if not any(uid == u for uid, _ in reds): reds.append((u, next((l for l in reversed(said) if not l.startswith("CLOSED ")), said[-1])))
            for uid, why in reds: red(uid, why)
            why_u = next(why for uid, why in reds if uid == u)
            if check_ran(r.returncode, why_u): record_attempt(u, ident, r.returncode, why_u)
            continue
        last = said[-1][:160]
        sh_(["git", "add", plan_path], log)
        if sh_([sys.executable, "-B", os.path.join(BIN, "commit_scan.py")], log).returncode != 0:
            sh_(["git", "reset", "-q", "HEAD", plan_path], log); sh_(["git", "checkout", "--", plan_path], log); out.append("CLOSE-RED %s: the commit scan refused the closure" % u); continue
        pc = precommit_check(frozen, log)[1]
        if pc.returncode != 0:
            sh_(["git", "reset", "-q", "HEAD", plan_path], log); sh_(["git", "checkout", "--", plan_path], log); out.append("CLOSE-RED %s: the pre-commit check refused the closure (exit %d)" % (u, pc.returncode)); continue
        started_from = rev_("HEAD")   # the closure commit's parent: where the unwind puts the branch back
        c = sh_(LAND_GIT + ["commit", "-q", "-m", "%s closed: its done check passed after the last landing (close_unit.py)\n\n%s" % (u, last)], log)
        p = c
        if c.returncode == 0:
            p = push_gate(frozen, remote, branch, log, sh_)[1]
            if p.returncode == 0: p = sh_(LAND_GIT + ["push", remote, "HEAD:" + branch], log, 3000)
        if c.returncode == 0 and p.returncode == 0:
            out.append("CLOSED  %s: %s" % (u, last)); continue
        why = "commit exit %d, push gate or push exit %d" % (c.returncode, p.returncode)
        if c.returncode == 0:
            # the closure is this writer's own commit: unwound like a refused landing (no build to quarantine), so the unit
            # stays open in the committed plan and the next closer tries again; local is never left ahead of hub
            why += "; " + unwind(started_from, "closure " + why, [], log, rev_, run=sh_)
        out.append("CLOSE-RED %s: %s" % (u, why)); record_close_red(u, why, log, frozen)
    return out


def reconcile_close(log, logp, env, rev):
    """THE DRIVER'S CLOSER, through the lander (review 15 finding 2, 2026-10-03): loop_pass.sh ran the LANDING TREE's
    scripts/close_unit.py every pass (CODE_ROOT is the launch worktree outside a proof), which imports the tree's plan_store,
    provenance and plan_lint and ran every done check bare, cwd the tree: just landed code, unsandboxed, each pass. This
    mode (land_batch.py --close) closes every eligible unit the way a landing's closing pass does: the closer from a frozen
    copy of HUB's head, each done check under the sandbox in a disposable copy (close_unit.py's own discipline), the closure
    committed and pushed by closing_pass with the frozen checks and no hook. Prints every CLOSED and CLOSE-RED line. Exit 0
    when a unit closed, 1 when none did (nothing eligible, or every closure red and said so), 2 when no frozen copy could be
    made (NO-DATA, nothing ran)."""
    why = step_refusal("fetch", sh(["git", "fetch", "-q", REMOTE], log, 300).returncode)
    if why: print("NO-DATA: %s; nothing was closed, see %s" % (why, logp)); return 2
    frozen = base_tree(lambda argv, genv: sh(argv, log, 600, genv), env, rev=REMOTE + "/" + BRANCH)
    if frozen is None:
        print("NO-DATA: no frozen copy of hub %s could be made for the closer; nothing was closed, see %s" % (rev(REMOTE + "/" + BRANCH), logp)); return 2
    FROZEN.append(frozen)
    lines = closing_pass(["--all-eligible"], sh, log, REMOTE, BRANCH, frozen=frozen)
    for line in lines: print(line)
    return 0 if any(line.startswith("CLOSED ") for line in lines) else 1


def reconcile_push(log, logp, env, rev):
    """THE DRIVER'S PUSH, through the lander (review 14 finding 3, 2026-10-02): loop_pass.sh used plain `git commit` and
    `git push` for a closure it made and for a branch left ahead of hub, and the installed hooks then exec the TREE's
    scripts/self_check_staged.py and scripts/pre_push_hook.sh: just landed code, run unsandboxed. This mode (land_batch.py
    --push) does what the landing does for its own commit and push: a frozen copy of HUB's head (the last reviewed and
    landed tree, never a commit this checkout is about to push), the pre-commit check from it when the plan alone is dirty,
    the push gate from it over git's own ref line, LAND_GIT for the commit and the push (no hook), parity read after.
    Prints one BLOCK line on a gate refusal (the driver reads it as deterministic, never a transport retry) and REFUSED on
    anything it will not touch: a tree dirty beyond the plan, a frozen copy that cannot be made. Exit 0 only on parity."""
    why = step_refusal("fetch", sh(["git", "fetch", "-q", REMOTE], log, 300).returncode)
    if why: print("REFUSED: %s, see %s" % (why, logp)); return 1
    dirty = changed_paths(); hub_head = rev(REMOTE + "/" + BRANCH)
    if dirty and dirty != {PLAN}:
        print("REFUSED: %d path(s) are dirty besides the plan (%s); the lander commits only a closure's plan change, see %s"
              % (len(dirty - {PLAN}), sorted(dirty - {PLAN})[:3], logp)); return 1
    frozen = base_tree(lambda argv, genv: sh(argv, log, 600, genv), env, rev=REMOTE + "/" + BRANCH)
    if frozen is None:
        print("REFUSED: no frozen copy of hub %s could be made for the push checks; nothing committed or pushed, see %s" % (hub_head, logp)); return 1
    FROZEN.append(frozen)
    started_from = None   # set when this call commits: the one commit it may unwind
    if dirty:
        sh(["git", "add", "--", PLAN], log)
        pc_argv, r = precommit_check(frozen, log)
        why = step_refusal("pre-commit check", r.returncode)
        if why:
            sh(["git", "reset", "-q", "HEAD", "--", PLAN], log)
            said = ((r.stderr or "") + (r.stdout or "")).strip().splitlines() or ["no output"]
            print("BLOCK %s: %s (the plan change is left as it was, see %s)" % (why, said[-1][:160], logp)); return 1
        started_from = rev("HEAD")
        c = sh(LAND_GIT + ["commit", "-q", "-m", "close: unit done checks passed on this pass\n\ncommitted by the lander for the driver's closer; checks from a frozen copy of hub %s, hooks off" % hub_head], log)
        why = step_refusal("commit", c.returncode)
        if why: print("REFUSED: %s, see %s" % (why, logp)); return 1

    def undo(why):
        # the lander's own closure commit never stays local (the next closer redoes the closure); a commit somebody else
        # left ahead of hub is not this call's to remove, and stays for a human
        return (" | " + unwind(started_from, why, [], log, rev)) if started_from else ""
    ahead = subprocess.run(["git", "rev-list", "--count", "%s/%s..HEAD" % (REMOTE, BRANCH)], capture_output=True, text=True).stdout.strip()
    if ahead == "0":
        print("PUSH    nothing to push: local %s is hub %s | PARITY OK" % (rev("HEAD"), hub_head)); return 0
    pg_argv, r = push_gate(frozen, REMOTE, BRANCH, log)
    why = step_refusal("push gate", r.returncode)
    if why:
        bad = [l for l in (r.stdout + r.stderr).splitlines() if "FAILS" in l or l.startswith(("REFUSED", "BLOCK", "NO-DATA", "pre-push:"))]
        print("BLOCK %s on the range hub %s..%s: %s%s" % (why, hub_head, rev("HEAD"), (bad or ["see " + logp])[0][:160], undo(why))); return 1
    r = sh(LAND_GIT + ["push", REMOTE, "HEAD:" + BRANCH], log, 3000)
    frc = sh(["git", "fetch", "-q", REMOTE], log, 300).returncode
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", REMOTE + "/" + BRANCH], capture_output=True).returncode == 0
    verdict = push_verdict(r.returncode, frc, rev("HEAD"), rev(REMOTE + "/" + BRANCH), anc)
    said = (r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or [""])[-1][:160]
    landed = verdict in ("LANDED", "FAST-FORWARD")
    print("PUSH    exit %d | %s | local %s hub %s | PARITY %s%s%s" % (r.returncode, verdict, rev("HEAD"), rev(REMOTE + "/" + BRANCH), "OK" if landed else "BROKEN",
                                                                       (" | " + said) if r.returncode else "", "" if landed or verdict == "UNVERIFIED" else undo("push exit %d, %s" % (r.returncode, verdict))))
    return 0 if landed else 1


def run_boxed(args):
    """land_batch.py --boxed <script> [arg ...]: the script, isolated and under boxed(), cwd the current directory (which
    it may read and never write: its write root is a temp directory of its own), stdout and stderr passed through, its
    exit code returned. (C) loop_pass.sh runs the landing tree's worktree_sentry.py this way outside a proof, where no
    frozen code root is named: the tree is a build's writes, so its code runs in a box or not at all. A host that cannot
    box answers 125, which the pass reads as an unreadable fingerprint and refuses to land on."""
    import io
    if not args or not args[0].endswith(".py"):
        print("usage: land_batch.py --boxed <script.py> [arg ...]"); return 2
    out = tempfile.mkdtemp(prefix="boxed-run-")
    try:
        r = boxed(isolated(os.path.abspath(args[0]), args[1:]), io.StringIO(), 600, None, cwd=os.getcwd(), root=out)
    finally:
        shutil.rmtree(out, True)
    sys.stdout.write(r.stdout or ""); sys.stderr.write(r.stderr or "")
    return r.returncode


def main():
    if "--selftest" in sys.argv: return selftest()
    if sys.argv[1:2] == ["--boxed"]: return run_boxed(sys.argv[2:])
    # THE LOOP'S HOLD BINDS THE LANDING ITSELF (D13, 2026-10-03): only the driver read LOOP-PAUSE.txt and LOOP-HOLD.txt
    # between passes, so a manual or in-flight land_batch.py landed, closed or pushed while the loop was held. The one
    # reader, loop_hold.gate, from THIS bin (the deployed copy, never the frozen copy or the landing tree), asks before any
    # git, plan or build read: held, or a control that cannot be read, prints HELD and exits 5 with nothing touched.
    sys.path.insert(0, BIN); import loop_hold
    loop_hold.gate(EV, where="land_batch")
    global REMOTE, BRANCH
    up = upstream()
    if not up:
        print("REFUSED: this branch has no upstream, so there is nowhere to land: git branch --set-upstream-to <remote>/<branch>"); return 2
    REMOTE, BRANCH = up
    if remote_allowed(REMOTE): print(remote_allowed(REMOTE)); return 2
    if expected_upstream_refusal(REMOTE, BRANCH): print(expected_upstream_refusal(REMOTE, BRANCH)); return 2   # every push mode below
    dry = "--dry" in sys.argv; push_only = "--push" in sys.argv; close_only = "--close" in sys.argv
    builds = [os.path.abspath(a) for a in sys.argv[1:] if not a.startswith("--")]
    if not builds and not push_only and not close_only: print(__doc__); return 2
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S"); logp = os.path.join(EV, "land-batch", stamp + ".log")
    os.makedirs(os.path.dirname(logp), exist_ok=True); log = open(logp, "w")
    # NO RUN KNOB REACHES A CHECK (2026-10-02 12:47): the static gates, the neighbour suites, their base rerun and the
    # hermetic check all ran with this env, which kept the run's own routing; under a Claude only run (BROTHER_TRANSPORTS
    # =claude) every Jev call is skipped, so test_mobile_hybrid_action_router went red on the build AND the base and RL5.a
    # was held as INHERITED. land_apply's suites already dropped them (suite_env); this is the same one definition.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import loop_switches as _LS
    env = {k: v for k, v in _LS.drop_run_knobs(dict(os.environ)).items() if not k.startswith("GIT_")}
    rev = lambda r: subprocess.run(["git", "rev-parse", "--short", r], capture_output=True, text=True).stdout.strip()
    if push_only: return reconcile_push(log, logp, env, rev)   # the driver's commit and push of a closure or a branch left ahead
    if close_only: return reconcile_close(log, logp, env, rev)   # the driver's closer, from a frozen copy of hub's head (review 15)
    why = step_refusal("fetch", sh(["git", "fetch", "-q", REMOTE], log, 300).returncode)
    if why: print("REFUSED: %s, see %s" % (why, logp)); return 1
    branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    head = rev("HEAD"); plan = load_json(PLAN)
    hub_head = rev(REMOTE + "/" + BRANCH)
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", REMOTE + "/" + BRANCH], capture_output=True).returncode == 0
    if behind_action(head, hub_head, anc) == "fast-forward" and not changed_paths():
        if sh(["git", "merge", "--ff-only", REMOTE + "/" + BRANCH], log, 300).returncode == 0:
            print("CATCH-UP local %s was behind hub %s, fast forwarded" % (head, hub_head)); head = rev("HEAD")
    why = preflight(branch, changed_paths(), head, hub_head, plan)
    if why: print("REFUSED: " + why); return 1
    cfg0 = shared_config_digest()
    # THE FROZEN COPY (D13): HEAD's files, read now while HEAD is the base commit and the tree is clean, so the pre-commit
    # check and the push gate run reviewed code over whatever this batch lands. No copy, no landing: nothing falls back
    # to the tree's scripts.
    frozen = None if dry else base_tree(lambda argv, genv: sh(argv, log, 600, genv), env)
    if not dry and frozen is None:
        print("REFUSED: no frozen copy of the base commit could be made for the landing checks; nothing landed, see %s" % logp); return 1
    if frozen: FROZEN.append(frozen)
    frozen_at = rev("HEAD")   # the commit the frozen copy holds: the red neighbour base comparison reuses it while HEAD is still that commit
    council, scores = load_json(EV + "/spec-council.json"), load_json(EV + "/spec-scores.json")
    landed, attributed, new_tests, facts, checks = [], set(), [], {}, []
    status_paths, landed_builds, cmds, done_checks = [], [], [], {}
    graded = {}   # {attributed path: snapshot entry} as the lander (or a gate's generator, or this file) wrote it: what git add may take
    for b in builds:
        st = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(b))), "STATUS")
        status_text = open(st, encoding="utf-8").read() if os.path.isfile(st) else ""
        why = gate(b, status_text, council, scores, plan)   # the checker is gone (owner decision 2026-10-02)
        if why:
            print("HELD    %-10s %s" % (sub_of(b) or os.path.basename(b), why))
            continue
        if dry: print("WOULD   %-10s land" % sub_of(b)); continue
        before = snapshot(changed_paths())   # the tree as the earlier builds of this batch left it
        apply_argv = [sys.executable, os.path.join(BIN, "land_apply.py"), b]
        r = sh(apply_argv, log, 3000, env)   # land_apply, never land_build: see its docstring
        verdict, payload, crashes, note = landing_verdict(r.returncode, r.stdout, status_text, sub_of(b), plan, stderr=r.stderr)
        mine = changed_paths() - attributed
        declared = build_declared(b)
        undeclared = sorted(p for p in mine if p not in declared)
        if undeclared and verdict != "drop":
            # tenth review 2026-10-02, reproduced: a build's test wrote the push hook, the battery and a workflow inside
            # the sandbox's ROOT, and every changed path was committed as the build's; only what it declared may land
            verdict, payload = "drop", "wrote %d path(s) it never declared: %s" % (len(undeclared), undeclared[:3])
            note = "DROPPED at landing: " + payload
        if verdict == "drop":
            print("DROPPED %-10s %s" % (sub_of(b), payload))
            # A dropped build must never be offered again, or every later pass re-applies and re-reverts it and the
            # loop livelocks on it (seen 2026-09-21: two builds offered three passes running). The reason is kept in
            # the STATUS so the next runner knows what to fix rather than rediscovering it.
            try:
                write_status(st, note)
            except (OSError, ValueError) as exc:
                print("        note: could not quarantine %s (%s), it may be offered again" % (st, exc))
            left = rollback(before, changed_paths(), log)
            if left:   # a rejected build's bytes never land: the whole batch is refused rather than committed over them
                t, c = revert((set(before) | changed_paths()) - {PLAN}, log)
                print("REFUSED: the dropped build %s could not be rolled back (%s); nothing committed, reverted %d tracked and "
                      "removed %d created path(s); the other builds stay READY | log %s" % (sub_of(b), left, t, c, logp))
                return 1
            continue
        note_check(checks, cmds, "land_apply " + sub_of(b), apply_argv, r.returncode); done_checks[sub_of(b)] = build_done_check(b)
        attributed |= mine; landed.append(sub_of(b)); facts[sub_of(b)] = (payload, crashes); status_paths.append(st); landed_builds.append(b)
        # the lander's bytes, the only ones a landed path may carry at git add: every declared path this build wrote,
        # including one an earlier build of the batch already landed (twelfth review 2026-10-02, reproduced: the second
        # build's bytes never entered graded, and the batch was refused on every pass)
        graded.update(snapshot(sorted(mine | (declared & changed_paths()))))
        new_tests += [p for p in mine if os.path.basename(p).startswith("test_") and p.endswith(".py") and subprocess.run(["git", "ls-files", "--error-unmatch", p], capture_output=True).returncode != 0]
    if dry or not landed: print("nothing landed%s | log %s" % (" (dry)" if dry else "", logp)); return 0 if dry else 1
    unsafe = [p for p in new_tests if not SAFE_TEST_PATH.fullmatch(p)]
    if unsafe:
        t, c = revert(attributed & changed_paths(), log)
        print("REFUSED: new test path(s) %s are not plain names and cannot be registered in %s; reverted %d and removed %d path(s)"
              % (unsafe[:3], CHECK_ALL, t, c)); return 1
    text = open(CHECK_ALL, encoding="utf-8").read(); new_text = register(text, sorted(new_tests))
    if new_text is None:
        t, c = revert(attributed & changed_paths(), log)
        print("REFUSED: %s has no anchor line; reverted %d and removed %d path(s)" % (CHECK_ALL, t, c)); return 1
    if new_text != text: open(CHECK_ALL, "w", encoding="utf-8").write(new_text)
    attributed.add(CHECK_ALL); graded.update(snapshot([CHECK_ALL])); red = []
    # THE GATE RUNS BESIDE FIVE GRADERS (2026-09-24 01:49: every landing went RED on discover because one selftest's vault
    # capture subprocess hit its 10 s budget under load, alone and in batch): the vault doors get a wide budget here, so a
    # loaded machine never reads as a broken product
    env = dict(env or os.environ); env.setdefault("BROTHERDS_CAPTURE_TIMEOUT_S", "90"); env.setdefault("BROTHERDS_RECALL_TIMEOUT_S", "30")
    ordered, why_order = gate_order(frozen=frozen, log=log); print("ORDER   " + why_order)
    # THE GATES RUN IN THE COPY, UNDER THE SANDBOX (D13, 2026-10-02): the gate scripts are protected, but bundle_runtime
    # imports codex_skills and a build may have just edited it, so a gate ran the build's code unsandboxed in the landing
    # tree. They now run in the disposable copy holding the batch's applied state, confined like a neighbour; what a
    # generator writes there comes back out through take_generated, screened like a build's own edit.
    try:
        copy = make_copy()
    except TreeUnreadable as exc:
        t, c = revert(attributed - {PLAN}, log)
        print("REFUSED: no disposable copy for the landing gates (%s); nothing committed, reverted %d tracked and "
              "removed %d created path(s) | log %s" % (exc, t, c, logp)); return 1
    held = []
    reds = []   # (test path, dotted, python, name): red WITH the build, judged against the base once the copy is gone
    try:
        for name, cmd, regen, outputs in ordered:
            t0 = time.time(); argv = [sys.executable, "-B"] + cmd; r = boxed(argv, log, 2400, env, cwd=copy); note_check(checks, cmds, name, list(r.args), r.returncode)
            if r.returncode and regen:
                before = snapshot(sorted(changed_paths(copy)), root=copy)
                regen_argv = [sys.executable, "-B"] + regen
                rr = boxed(regen_argv, log, 2400, env, cwd=copy); note_check(checks, cmds, name + " regenerate", list(rr.args), rr.returncode)
                # THE GENERATOR'S OUTPUT SET IS DECLARED (review 14 finding 1): a build's code may run inside the generator,
                # so a path outside the gate's declared outputs is a red gate and nothing of it leaves the copy, whatever
                # dest() would say of the path alone
                generated = generated_in(copy, before); stray = undeclared_outputs(generated, outputs)
                if stray:
                    print("GATE    %s regenerate wrote %d path(s) outside its declared output set, which the landing never takes: %s" % (name, len(stray), "; ".join(str(p) for p in stray[:3])))
                    record_gate(os.environ.get("BROTHER_RUN_DIR", ""), "landing." + name, 1, (time.time() - t0) * 1000); red.append(name); continue
                # only what the generator itself wrote in the copy, taken into the landing tree through the one screen
                # every build write passes (regenerated bytes are attributed to the gate, so the check before git add
                # knows them as such)
                taken, refused = take_generated(copy, generated)
                attributed |= set(taken); graded.update(snapshot(taken))
                if refused:
                    print("GATE    %s regenerate wrote %d path(s) the landing never takes: %s" % (name, len(refused), "; ".join("%s (%s)" % kv for kv in sorted(refused.items())[:3])))
                    record_gate(os.environ.get("BROTHER_RUN_DIR", ""), "landing." + name, 1, (time.time() - t0) * 1000); red.append(name); continue
                r = boxed(argv, log, 2400, env, cwd=copy); note_check(checks, cmds, name, list(r.args), r.returncode)
            record_gate(os.environ.get("BROTHER_RUN_DIR", ""), "landing." + name, r.returncode, (time.time() - t0) * 1000)
            if r.returncode: red.append(name)
        # THE FIFTH GATE: the registered tests named after every module this landing edited, on both Pythons.
        try:
            with open(CHECK_ALL, encoding="utf-8") as fh: _reg = fh.read()
        except OSError: _reg = ""
        nb = neighbour_tests(attributed, os.path.isfile, os.listdir, _reg)
        # IN A COPY, NEVER THE LANDING TREE (eleventh review 2026-10-02): a neighbour imports the build's code, so it runs
        # in the disposable copy holding the batch's applied state; the copy goes before the base tree is made (one at a time).
        for tp, dotted in nb:
            for py in (sys.executable, "/usr/bin/python3"):
                t0 = time.time(); nb_argv = [py, "-B", "-m", "unittest", dotted] if dotted else [py, "-B", tp]; r = boxed(nb_argv, log, 1200, env, cwd=copy)
                note_check(checks, cmds, "neighbour %s on %s" % (tp, "py3.9" if py != sys.executable else "py3"), list(r.args), r.returncode)   # what RAN, sandbox wrapper included (receipt contract)
                record_gate(os.environ.get("BROTHER_RUN_DIR", ""), "landing.neighbour." + os.path.basename(tp)[:-3], r.returncode, (time.time() - t0) * 1000)
                if r.returncode:
                    reds.append((tp, dotted, py, "neighbour %s on %s" % (tp, "py3.9" if py != sys.executable else "py3")))
                    break
    finally:
        drop_copy(copy)

    def runner(argv, genv=None):
        return sh(argv, log, 1200, genv if genv is not None else env)
    print("NEIGHBOURS %d registered test(s) of the edited modules ran on both Pythons%s" % (len(nb), "" if nb else " (none named)"))
    if reds:
        # THE FROZEN COPY IS THE BASE (review 14 finding 5, footprint): it holds the base commit's files already, so a red
        # neighbour is judged against it while HEAD is still that commit; a third checkout is made only when it is not
        reuse = frozen is not None and rev("HEAD") == frozen_at
        base_root = frozen if reuse else base_tree(runner, env)
        sealed = tree_digest(frozen) if reuse else None
        for tp, dotted, py, nb_name in reds:
            if base_root is None:
                word = "NO-DATA"
            else:
                word = neighbour_verdict(True, base_run_red(base_root, tp, dotted, py, lambda argv, genv=None: boxed(argv, log, 1200, env)))
            if word == "RED":
                red.append(nb_name)
            else:
                held.append((nb_name, word))
        if base_root is not None and not reuse:
            drop_base_tree(base_root)
        if reuse and (sealed is None or tree_digest(frozen) != sealed):
            # the base rerun is confined to the tree it runs in, and that tree is the frozen copy: a write there would be
            # read by the commit and push checks that run from it next, so the landing stops here
            t, c = revert(attributed & changed_paths(), log)
            print("REFUSED: a base rerun changed the frozen copy the commit and push checks run from; nothing committed, "
                  "reverted %d tracked and removed %d created path(s); STATUS left unchanged | log %s" % (t, c, logp)); return 1
    date = datetime.datetime.now().strftime("%Y-%m-%d"); closed = []; lines = {}
    for sub in landed:
        u = unit_of(sub, plan); line = evidence_line(sub, date, facts[sub][0], facts[sub][1], head)
        u["evidence"] = (u.get("evidence") or "").rstrip() + line; lines.setdefault(u["id"], []).append(line)
        if unit_closed(u): closed.append(u["id"])
    # BY UNIT, UNDER A LOCK (review 2026-09-22): the plan copy in `plan` was read minutes ago; only the units this
    # landing touched are written, and each line is appended to the unit RE-READ under the lock, so evidence a closer
    # or another landing wrote meanwhile is kept (plan_store refuses a record that would erase it).
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import plan_store
    appended = {}   # {unit id: (evidence re-read, evidence written[, owns added])}: what a refusal takes back, and nothing else
    # EVERY NEW LOOP FILE IS OWNED IN THE SAME WRITE (queue item 5): a file this landing created in brother.loop's shape
    # goes into unit BL's owns under the same lock as the evidence, and leaves with it on any refusal below.
    owned = [p for p in loop_components(sorted(attributed & changed_paths()))
             if subprocess.run(["git", "ls-files", "--error-unmatch", p], capture_output=True).returncode != 0]
    if owned and not any(u.get("id") == LOOP_UNIT for u in plan.get("units", [])):
        print("OWNS    unit %s is not in the plan: %d new loop file(s) left unowned %s" % (LOOP_UNIT, len(owned), owned[:3])); owned = []
    plan_store.update_units(PLAN, landing_records(lines, appended, owns=owned), fields=("evidence", "owns")); attributed.add(PLAN)
    if len(appended.get(LOOP_UNIT, ())) > 2:
        print("OWNS    unit %s now names %s" % (LOOP_UNIT, ", ".join(appended[LOOP_UNIT][2])))
    stray = unattributed(changed_paths(), attributed)
    print("GATES   %s | new suites registered %d | stray paths %d %s" % (gates_text(red, [h[0] for h in held]), len(new_tests), len(stray), stray[:3] if stray else ""))
    if held and not red:
        t, c, plan_note = revert_landing(attributed & changed_paths(), log, appended)
        print("REFUSED: nothing committed, %s; reverted %d tracked and removed %d created path(s)%s; STATUS left unchanged | log %s" % (held_reason(held), t, c, plan_note, logp))
        return 1
    why = refusal_reason(red, stray)
    if why:
        t, c, plan_note = revert_landing(attributed & changed_paths(), log, appended)
        if status_paths:
            try:
                with open(status_paths[0], encoding="utf-8") as fh: prev = fh.read().strip()
                note = gate_quarantine(landed, status_paths, why, prev)
                if note:
                    write_status(status_paths[0], note)
                    print("QUARANTINED %s: it failed the gates on its own, so it is not offered again" % landed[0])
            except (OSError, ValueError) as exc:
                print("note: could not quarantine %s (%s)" % (status_paths[0], exc))
        print("REFUSED: nothing committed, %s; reverted %d tracked and removed %d created path(s)%s; every build "
              "json is still on disk%s | log %s"
              % (why, t, c, plan_note, "" if len(landed) == 1 else "; bisecting: each build is retried alone", logp))
        # BISECT HERE, never "the caller retries them singly". That sentence sat in this file as a comment while
        # NO caller implemented it: loop_pass.sh reads the exit code, prints "landing refused" and stops. So one
        # poisoned build in a batch of five reverted all five, and the identical five were offered again every
        # pass, forever. Measured 2026-09-21: five READY builds (D14.3, L1b.3, L3.3, L3b-01, L5a-2) could not
        # land at all while discover was green on the clean tree, which is exactly the "the loop cannot close
        # anything" complaint. An obligation written in prose and handed to nobody is not a design.
        # Retrying each build ALONE lands the innocent ones and drives the guilty one into the single-build
        # quarantine path above, which already knows how to blame unambiguously.
        return retry_alone(landed, builds)
    landed_files = sorted(attributed & changed_paths())   # read BEFORE the commit: after it git status is empty and this reads nothing
    # IMMEDIATELY BEFORE git add (eleventh review 2026-10-02): every landed path must still be the regular file with the
    # bytes the lander (or a gate's generator, or this file) wrote; the plan is excepted, other writers append to it
    # under its lock by design. A link, a rewrite or a mode change since then refuses the batch, whoever did it.
    now = snapshot(landed_files)
    moved = sorted(p for p in landed_files if p != PLAN and now[p] != graded.get(p))
    if moved:
        t, c, plan_note = revert_landing(attributed & changed_paths(), log, appended)
        print("REFUSED: %d landed path(s) changed after the lander wrote them (%s); nothing committed, reverted %d tracked and "
              "removed %d created path(s)%s; STATUS left unchanged | log %s" % (len(moved), moved[:3], t, c, plan_note, logp)); return 1
    sh(["git", "add", "--"] + landed_files, log)
    scan_argv = [sys.executable, os.path.join(BIN, "commit_scan.py")]
    r = sh(scan_argv, log); print("SCAN    " + (r.stdout.strip().splitlines() or ["no output"])[-1])
    note_check(checks, cmds, "commit_scan", scan_argv, r.returncode)
    why = step_refusal("commit scan", r.returncode)
    if why: print(refuse_landing(why, attributed & changed_paths(), status_paths, landed, log, appended=appended)); return retry_alone(landed, builds)
    # THE PRE-COMMIT HOOK'S CHECK, run by this command from the frozen copy (D13); the commit below runs no hook
    pc_argv, r = precommit_check(frozen, log); note_check(checks, cmds, "pre-commit check", list(r.args), r.returncode)   # what RAN, sandbox wrapper included (receipt contract)
    why = step_refusal("pre-commit check", r.returncode)
    if why:
        said = ((r.stderr or "") + (r.stdout or "")).strip().splitlines() or ["no output"]
        print(refuse_landing("%s: %s" % (why, said[-1][:160]), attributed & changed_paths(), status_paths, landed, log, appended=appended)); return retry_alone(landed, builds)
    msg = commit_text(landed, facts, checks)
    why = step_refusal("commit", sh(LAND_GIT + ["commit", "-q", "-m", msg], log).returncode)
    if why: print(refuse_landing("%s, see %s" % (why, logp), attributed & changed_paths(), status_paths, landed, log, appended=appended)); return retry_alone(landed, builds)
    # THE LANDING COMMIT, read here and nowhere later (U10): `head` is its PARENT, and after a fast forward or a closure
    # HEAD is somebody else's commit. Both mark_landed calls and the landing record name this one.
    landing = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    facts_row = landing_facts(landed_files, checks, cmds, done_checks, logp)   # what the landing ran, named per file in the run's receipt
    # THE PRE-PUSH HOOK'S GATE (its hermetic check over the pushed range included), AFTER the commit, run by this command from
    # the frozen copy with git's own ref line (D13): the export tree is built from git, so a test file that is new and
    # uncommitted is "not shipped" there and passes vacuously (found 2026-09-20 when the pre-push refused what this step had
    # passed). The push below runs no hook.
    pg_argv, r = push_gate(frozen, REMOTE, BRANCH, log)
    why = step_refusal("push gate", r.returncode)
    if why:
        bad = [l for l in (r.stdout + r.stderr).splitlines() if "FAILS" in l or l.startswith(("REFUSED", "BLOCK", "NO-DATA", "pre-push:"))]
        print("REFUSED: %s on the committed range; commit %s: %s" % (why, rev("HEAD"), (bad or ["see log"])[0][:160]))
        print(unwind(head, why, blame_paths(landed, status_paths), log, rev)); return retry_alone(landed, builds)
    gate_said = "pre-push: clear" if "pre-push: clear" in r.stdout + r.stderr else "pre-push verdict not seen"
    r = sh(LAND_GIT + ["push", REMOTE, "HEAD:" + BRANCH], log, 3000)
    frc = sh(["git", "fetch", "-q", REMOTE], log, 300).returncode
    fetched_at = utc_now() if frc == 0 else None
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", REMOTE + "/" + BRANCH], capture_output=True).returncode == 0
    verdict = push_verdict(r.returncode, frc, rev("HEAD"), rev(REMOTE + "/" + BRANCH), anc)
    if verdict == "FAST-FORWARD" and sh(["git", "merge", "--ff-only", REMOTE + "/" + BRANCH], log, 300).returncode == 0:
        print("CATCH-UP hub holds this landing and more; local fast forwarded to %s" % rev("HEAD"))
    same = verdict in ("LANDED", "FAST-FORWARD")
    print("PUSH    exit %d | %s | local %s hub %s | PARITY %s" % (r.returncode, gate_said, rev("HEAD"), rev("hub/" + BRANCH), "OK" if same else "BROKEN"))
    cfg1 = shared_config_digest(); print("CONFIG  shared git config %s" % ("unchanged " + cfg1 if cfg1 == cfg0 else "CHANGED %s -> %s: a suite wrote to the live repository" % (cfg0, cfg1)))
    if verdict == "UNVERIFIED":
        # The remote accepted (push exit 0); only the parity read failed. Parity is NO-DATA, never "not landed":
        # the commit stays, nothing is reset, the next pass fetches and verifies (or fast forwards).
        parity = "NO-DATA: push exit %d but the parity fetch failed (exit %d); no remote observed, verified on the next pass" % (r.returncode, frc)
        print("PARITY  %s, see %s" % (parity, logp))
        line = record_landing(os.environ.get("BROTHER_RUN_DIR", ""), landing, landed, landed_builds, verdict, REMOTE + "/" + BRANCH, None, None, parity=parity, **facts_row)
        if line: print(line)
        mark_landed(status_paths, landed_builds, rev(landing) if landing else "NO-DATA"); return exit_code(False, 0, cfg1 == cfg0)
    if verdict in ("NOT-LANDED", "DIVERGED"):
        # NOT LANDED IS NEVER PRINTED AS LANDED. This line used to print LANDED whatever the push did, and a commit
        # that never reached hub then blocked every later landing on parity.
        print("REFUSED: the push did not reach hub (exit %d, %s); nothing landed, see %s" % (r.returncode, verdict, logp))
        print(unwind(head, "push exit %d, %s" % (r.returncode, verdict), blame_paths(landed, status_paths), log, rev)); return retry_alone(landed, builds)
    full_sha = lambda: subprocess.run(["git", "rev-parse", REMOTE + "/" + BRANCH], capture_output=True, text=True).stdout.strip() or None
    remote_sha = full_sha()
    print(landed_line(landed, closed, logp, {s: usd_text(s) for s in landed}))
    mark_landed(status_paths, landed_builds, rev(landing) if landing else "NO-DATA")
    closing = closing_pass(closed, sh, log, REMOTE, BRANCH, frozen=frozen)
    for line in closing: print(line)
    if closing:
        # PARITY IS READ AFTER THE LAST PUSH (finding B4, 2026-09-27): the closure commits and pushes after the parity
        # above, and a refused closure push returned that earlier 0 with local ahead of hub. A failed read is NO-DATA.
        frc = sh(["git", "fetch", "-q", REMOTE], log, 300).returncode
        same = same and frc == 0 and rev("HEAD") == rev(REMOTE + "/" + BRANCH)
        print("PARITY  after the closure: %s | local %s hub %s" % ("OK" if same else "BROKEN" if frc == 0 else "NO-DATA (fetch exit %d)" % frc, rev("HEAD"), rev(REMOTE + "/" + BRANCH)))
        if frc == 0:
            remote_sha, fetched_at = full_sha(), utc_now()
    # THE LAST FETCH IS WHAT THE RECORD CARRIES (X1 finding 2, 2026-09-27): the record and every history were written
    # before the closure's fetch, so a revert that fetch saw made this exit nonzero while the receipt still counted the
    # landing surviving. They are written here, at the last observation this run made; a failed closure fetch observed
    # nothing, so the landing fetch stands, and its fetched_at names it.
    history = subprocess.run(["git", "log", "--format=%H%x00%B", "%s..%s" % (landing, remote_sha)], capture_output=True) if remote_sha else None
    def _range_log(earlier, sha):
        got = subprocess.run(["git", "log", "--format=%H%x00%B", "%s..%s" % (earlier, sha)], capture_output=True)
        return got.stdout if got.returncode == 0 else None
    refresh_histories(os.environ.get("BROTHER_RUN_DIR", ""), remote_sha, _range_log)
    line = record_landing(os.environ.get("BROTHER_RUN_DIR", ""), landing, landed, landed_builds, verdict, REMOTE + "/" + BRANCH,
                          remote_sha, fetched_at, history.stdout if history is not None and history.returncode == 0 else None, **facts_row)
    if line: print(line)
    return exit_code(same, r.returncode, cfg1 == cfg0)


def run_main():
    """main(), with an unreadable tree (changed_paths, make_copy) a printed REFUSED and exit 1, never a traceback and
    never a landing judged on a tree git could not read."""
    try:
        return main()
    except TreeUnreadable as exc:
        print("REFUSED: the tree could not be read (%s); nothing landed and no build was judged" % exc); return 1
    finally:
        for p in FROZEN:   # the frozen copy lives for one landing, whatever that landing did
            if not drop_base_tree(p):   # said, never silent: 70 copies leaked unseen and stopped RB on disk (2026-10-05)
                sys.stderr.write("drop_base_tree: the frozen copy %s was not removed\n" % p)
if __name__ == "__main__": sys.exit(run_main())
