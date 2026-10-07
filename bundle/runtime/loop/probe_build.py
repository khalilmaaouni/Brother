#!/usr/bin/env python3
"""Run an adversary's executable hostile probe against one worker build, in a scratch copy.
usage (repo root): probe_build.py <build.json> <probe.py or probe.json with key "probe_script">

The adversary (a reviewer model) PROPOSES probes as code; this runner EXECUTES them, so a claim like
"refuses an unhashable id" is observed, never believed. The probe body calls fire(label, callable).
One line per probe:  CRASH (a raw interpreter exception reached the caller: always a defect),
REFUSED (a deliberate error: ValueError, TypeError, or an exception class the repository's own code defines),
RETURNED (read by hand against the spec: may be a wrong ACCEPT).
Exit 1 when any probe is a CRASH or the probe itself cannot run; 0 otherwise. NO probes run is exit 2, never a pass.
The probe child runs under grade_build.sandboxed(); no usable sandbox is NO-DATA (exit 2), never an unsandboxed run,
unless BROTHER_SANDBOX=off is set, which the output then says.
"""
import ast, json, os, re, secrets, shutil, subprocess, sys, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade_build as G

# THE RECORD CHANNEL (audit finding 6, 2026-09-27). Results used to be ordinary stdout lines starting PROBE, so a
# script that printed one forged line (or a build that printed one) produced CLEAN. Records now travel on a pipe
# only this harness writes, each carrying a nonce the parent hands over on stdin and the harness keeps in a
# closure; stdout is never read for results. The END record is written after the script's last line, so a run that
# stopped early (os._exit(0) inside a probe) is an unfinished run, not a short clean one.
# ponytail: in process, a body that introspects fire.__closure__ can still read the nonce; the admission screen
# refuses every _bp_ name and any rebinding of fire, and a stronger channel needs one process per probe.
HARNESS = r'''
import os as _bp_os, sys as _bp_sys
_bp_sys.path.insert(0, ".")
def _bp_open():
    nonce = _bp_sys.stdin.readline().strip()
    fd = int(_bp_os.environ.pop("BROTHER_PROBE_FD"))
    _bp_os.set_inheritable(fd, False)
    here = _bp_os.path.realpath(".") + _bp_os.sep
    def put(*fields):
        _bp_os.write(fd, ("\t".join((nonce,) + tuple(str(f).replace("\t", " ").replace("\r", " ").replace("\n", " ") for f in fields)) + "\n").encode("utf-8", "replace"))
    def own(t):
        # the repository's own code defines this exception class: the module's own deliberate error.
        # Anything unreadable is "0", which classify() reads as CRASH, never as a refusal.
        try:
            m = None if t.__module__ in ("__main__", "builtins") else _bp_sys.modules.get(t.__module__)
            f = getattr(m, "__file__", None)
            return "1" if isinstance(f, str) and f and _bp_os.path.realpath(f).startswith(here) else "0"
        except Exception:
            return "0"
    # WHERE IT WAS RAISED (verifier finding, 2026-09-29). "1" only when the innermost traceback frame is the probe
    # script's own code (this file's code AND this module's globals): an arity TypeError there is the ADVERSARY's
    # call, refused before the callee got a frame. Raised in any other frame, it is the build calling its own helper
    # wrong, a real defect. Anything unreadable is "0", which classify() reads as CRASH, never as excused.
    # ponytail: a Python level wrapper in the build (a decorator taking *args) moves the adversary's bad call into a
    # build frame, so it reads CRASH: the repair direction, never CLEAN. Reading the callee signature would fix it.
    me = _bp_sys._getframe()
    code_file, probe_globals = me.f_code.co_filename, me.f_globals
    def at_call(e):
        try:
            tb = e.__traceback__
            while tb.tb_next is not None:
                tb = tb.tb_next
            f = tb.tb_frame
            return "1" if f.f_code.co_filename == code_file and f.f_globals is probe_globals else "0"
        except Exception:
            return "0"
    def fire(label, fn):
        try:
            out = ("RETURNED", "", "0", "0", repr(fn())[:200])
        except BaseException as e:  # SystemExit and KeyboardInterrupt included: a probe must never end the run
            try:
                msg = str(e)[:160]
            except BaseException:
                msg = "<unprintable>"
            out = (type(e).__name__, type(e).__module__, own(type(e)), at_call(e), msg)
        try:
            lab = str(label)[:70]
        except BaseException:
            lab = "<unprintable label>"
        put("PROBE", lab, *out)
    return fire, (lambda: put("END"))
fire, _bp_end = _bp_open()
del _bp_open
'''
TRAILER = "\n_bp_end()\n"


def admission(body):
    """'' when the probe script may run, else why not (NO-DATA). PARSED, never searched: a script counts only
    when it holds a real call to the harness's fire(), never rebinds fire, and never names the harness's own
    _bp_ internals. A mention in a comment or a string is not a call (audit finding 6)."""
    if not isinstance(body, str):
        return "no probe script"
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return "the probe script does not parse (%s)" % type(exc).__name__
    calls = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "fire":
            calls += 1
        names = ([n.id] if isinstance(n, ast.Name) else [n.attr] if isinstance(n, ast.Attribute) else [n.arg] if isinstance(n, ast.arg)
                 else [n.asname or n.name] if isinstance(n, ast.alias) else list(n.names) if isinstance(n, (ast.Global, ast.Nonlocal))
                 else [n.name] if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else [])
        if any(isinstance(x, str) and x.startswith("_bp_") for x in names):
            return "the probe script names the harness's internals"
        rebinds = (isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load)) or isinstance(
            n, (ast.alias, ast.Global, ast.Nonlocal, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        if rebinds and "fire" in names:
            return "the probe script rebinds fire, the harness it must call"
    return "" if calls else "no fire() call in the probe script"


RAW = {"AttributeError", "KeyError", "IndexError", "RecursionError", "SystemExit", "ZeroDivisionError", "NameError",
       "UnicodeDecodeError", "UnicodeEncodeError", "UnboundLocalError", "AssertionError", "StopIteration", "MemoryError",
       "FileNotFoundError", "IsADirectoryError", "NotADirectoryError", "PermissionError", "JSONDecodeError"}
# "missing N required keyword-only argument" joined 2026-09-29: the same refused CALL as its positional twin, raised
# by Python before the body runs (28 probe log lines on disk, every R4.2 pass read DIRTY on it). Its unexpected
# keyword twin was already here. Deliberately not added: "got multiple values for argument", nothing measured it.
ARITY = re.compile(r"takes \d+ positional argument|missing \d+ required (?:positional|keyword-only) argument|"
                   r"takes no arguments|got an unexpected keyword argument|"
                   r"takes from \d+ to \d+ positional argument")
RAW_TYPEERROR = re.compile(r"unsupported operand|unhashable|object is not|has no len|not subscriptable|not iterable|argument of type|"
                           r"can only concatenate|not supported between|must be str, not|must be real number|takes \d+ positional|"
                           r"missing \d+ required|unexpected keyword|expected str, bytes|NoneType|cannot unpack|not callable")

#: what a refusal looks like when the code RETURNS it instead of raising (measured 2026-09-20: 33 of 38 probes on one build were
#: flagged as wrong accepts while every one had returned (None, QuarantineRecord...) or (False, (reason,), ...)).
#: NARROWED 2026-09-22. The bare integers 1, 2 and 3 and the empty containers {} [] () '' were in this pattern and are
#: gone. They are not refusal WORDS, they are ordinary return values, and nothing in the 2026-09-20 measurement that
#: justified this pattern returned one: every case there was None, False or a quarantine record, all still matched.
#: A returned value can only be judged a refusal against the function's CONTRACT, which this runner does not read, so
#: a value this pattern does not recognise stays RETURNED and is judged only by the adversary's own expect_block list.
#: FAIL DIRECTION: an unrecognised value reads as an ACCEPT, which sends the lane to repair. An ambiguous scalar
#: excused as a refusal would have sent it to READY, which is the direction that ships the defect.
# An EMPTY container returned for hostile input is a refusal delivered as a value (nothing was accepted), while a
# populated one is an accept. Added 2026-09-22: '[]' and '{}' were read as accepts.
REFUSAL_VALUE = re.compile(r"^\(?(None|False)\b|Quarantine|QUARANTIN|Refus|refus|REFUS|no_decision|NO-DATA|no_data|denied|Denied|blocked|BLOCK|REJECT|reject|invalid|INVALID|MISMATCH|admissible=False|ok=False|allowed=False")

CLI_PROBE = re.compile(r"^(cli|main|argv)[_\-]")
EXIT_CODE = re.compile(r"^\s*[1-9]\d*\s*$")


def verdict_of(probe, kind, value, must_block):
    """The one line verdict for a probe that RETURNED: a refusal value is REFUSED; a CLI probe (the adversary names
    them cli_*, main_*, argv_*) that returned a non zero exit code is REFUSED too, since an exit code IS the refusal a
    CLI delivers (2026-09-24: 47 findings in one night read exit 1 and 2 for a missing, empty, binary file or a directory
    as wrong accepts, and no lane was ever approved); any other returned value the adversary said must block is a
    WRONG-ACCEPT?. FAIL DIRECTION: exit 0 and a non CLI probe stay RETURNED and are judged by the must block list."""
    if kind != "RETURNED": return kind
    if REFUSAL_VALUE.search(value): return "REFUSED"
    if CLI_PROBE.match(probe or "") and EXIT_CODE.match(value or ""): return "REFUSED"
    if probe in must_block: return "WRONG-ACCEPT?"
    return "RETURNED"


def classify(name, module, message, own=False, at_call=False):
    """The verdict for one probe outcome. own is True only when the harness found the exception class defined
    in the repository's own code (never the probe script, never builtins or the standard library); at_call is
    True only when the harness found the exception raised in the probe script's own frame (the adversary's call
    site). Callers that re-read a finding line without them get the conservative answer, CRASH."""
    if name == "RETURNED":
        return "RETURNED"
    if name == "SystemExit" and message.strip() == "2":
        return "REFUSED"  # argparse's usage exit: a CLI refusing its arguments, not a crash
    if name == "AttributeError" and re.search(r"^module '[\w.]+' has no attribute", message):
        # THE PROBE NEVER REACHED THE FUNCTION, so it proves nothing about the function, in either direction.
        # This said REFUSED, which is EVIDENCE: it read as "the build correctly refused this input" and fed the
        # clean count. It is NO-DATA. FAIL DIRECTION: NO-DATA counts toward neither CLEAN nor DIRTY, and a run
        # whose probes are all NO-DATA exits 2, never 0.
        return "NO-DATA"
    if name == "TypeError" and at_call is True and ARITY.search(message):
        # THE ADVERSARY CALLED IT WRONG, which is not a defect in the build. This is the same carve out the
        # AttributeError line above already makes, for the same reason, and it was missing for arity.
        #
        # Measured 2026-09-21 over every probe log on disk: 124 of 838 CRASH verdicts, 15 percent, are this.
        # The decisive one is a probe literally named baseline_machine_capacity failing with
        # "machine_capacity() takes 0 positional arguments but 2 were given": a BASELINE probe that crashes
        # cannot be evidence about the build, only about the probe. The adversary meant to pass a hostile
        # VALUE to a function that accepts no arguments at all, so it never tested anything.
        #
        # Narrow on purpose. It excuses only a failure to CALL the function, never a failure to HANDLE what it
        # was given: a wrong type that reaches the body and raises is still a CRASH, because that is the build's
        # job. Two independent diagnosticians reached this same gate from two different stuck lanes.
        #
        # 2026-09-22: re-measured at 124 of 846 CRASH lines in the probe logs on disk, and RECLASSIFIED from
        # REFUSED to NO-DATA for the same reason as the module-attribute line above. A call that never entered
        # the function body is not the function refusing anything. Judging a refusal properly would need the
        # function's CONTRACT (its real signature and its documented rejects), which this runner does not read;
        # that is out of scope here, so this takes the narrower honest option and reports nothing learned.
        #
        # 2026-09-29: ONLY AT THE PROBE'S OWN CALL (at_call). The message alone cannot tell the adversary's bad call
        # from the build's body calling its own helper wrong; that read NO-DATA, and beside one clean refusal the
        # lane went CLEAN. The harness now reports where it was raised; anywhere else, the screen below says CRASH.
        return "NO-DATA"
    if name in RAW or (name == "TypeError" and (RAW_TYPEERROR.search(message) or ARITY.search(message))):
        return "CRASH"
    # ONLY A DELIBERATE ERROR IS A REFUSAL (audit finding 7, 2026-09-27). Everything outside the crash list used
    # to default to REFUSED, so an uncaught OverflowError from int(float("inf")) read as the build refusing
    # on purpose and the lane read CLEAN. The house law is "the module's own deliberate error or refusal value":
    # ValueError, a TypeError that is not a raw shape (screened above), or a class the repository defines.
    # FAIL DIRECTION: any other type is CRASH, which sends the lane to repair, never to READY.
    if name in ("ValueError", "TypeError") or own is True:
        return "REFUSED"
    return "CRASH"


_PROBE_AT = re.compile(r"^# probe-at ([0-9]+(?:\.[0-9]+)?)\s*$")


def _probe_log_at(path):
    """The real epoch time a probe log was WRITTEN, read from its own first line's
    "# probe-at <epoch>" marker (MECHANISM-AUDIT item 21, 2026-09-26): never the file's mtime,
    which a checkout, restore or copy can reset or preserve independent of when the probe
    actually ran. None when the marker is missing or unreadable (an old log written before this
    fix, or a corrupted file): never guessed, and never read as 'now' or 'always in the window'."""
    try:
        with open(path, encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:  # sbe: allow-silent an unreadable log has no provable probe-at time; None is this function's documented refusal and lessons() excludes it from the window, never guesses it in
        return None
    m = _PROBE_AT.match(first)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:  # sbe: allow-silent a marker that is not a number is a corrupted marker; None is the documented refusal and lessons() excludes the log, never guesses its time
        return None


def lessons(runs_dir=os.path.expanduser("~/.claude/evidence/unit-runs"), since_s=24 * 3600, top=3, now=None):
    """CROSS LANE PROBE LESSONS (owner 2026-09-23 05:2x): the top finding classes of every lane's executed probes in the
    last since_s seconds, as one short block for a round 0 brief. Measured the night it was written: 13 clean of 57 probe
    rounds, findings 1860 WRONG-ACCEPT, 470 raw AssertionError, 373 raw TypeError. Returns '' when no log is readable.

    THE WINDOW IS THE LOG'S OWN probe-at MARKER, NEVER ITS FILE MTIME (item 21): a log with no
    readable marker (written before probe_wave.py started stamping one, or corrupted) is
    excluded, never guessed into or out of the window."""
    import collections, glob, time
    floor = (now or time.time()) - since_s; kinds = collections.Counter(); names = collections.Counter(); n = 0
    for f in glob.glob(os.path.join(runs_dir, "*", "round*", "probes", "logs", "*.log")):
        at = _probe_log_at(f)
        if at is None or at < floor: continue
        try:
            with open(f, encoding="utf-8") as fh: lines = fh.read().splitlines()
        except OSError: continue
        for l in lines:
            m = re.match(r"^(CRASH|WRONG-ACCEPT\?)\s+(\S+)\s+(\S+)", l)
            if not m: continue
            n += 1; kinds["accepted a hostile input the specification blocks" if m.group(1).startswith("WRONG") else "raw %s reached the caller" % m.group(3)] += 1
            names[m.group(2)] += 1
    if not n: return ""
    return ("EXECUTED PROBE FINDINGS ACROSS EVERY LANE IN THE LAST %d H (%d findings): %s. Probes that failed most: %s. "
            "Refuse each hostile input with the module's own error value or refusal; a raw exception is a failure, an accepted bad input is a failure.\n"
            % (since_s // 3600, n, "; ".join("%d %s" % (c, k) for k, c in kinds.most_common(top)), ", ".join("%s (%d)" % (k, c) for k, c in names.most_common(top))))


def selftest():
    import tempfile, time
    now = time.time()
    d = tempfile.mkdtemp(prefix="pb-"); lg = os.path.join(d, "D1.1-000001", "round0", "probes", "logs"); os.makedirs(lg)
    with open(os.path.join(lg, "D1.1-a.log"), "w") as fh:
        fh.write("# probe-at %.6f\n" % now)
        fh.write("CRASH none_input TypeError x\nCRASH bool_for_int TypeError y\nWRONG-ACCEPT? empty_list RETURNED 1\nOK fine\n")
    old = os.path.join(d, "D1.2-000002", "round0", "probes", "logs"); os.makedirs(old)
    # THE WINDOW IS THE MARKER, NEVER MTIME (item 21): this log's marker is 90000 s old while its
    # file mtime is fresh (the default, just written), proving the marker decides, not the file.
    with open(os.path.join(old, "D1.2-a.log"), "w") as fh:
        fh.write("# probe-at %.6f\n" % (now - 90000))
        fh.write("CRASH stale AttributeError z\n")
    unmarked = os.path.join(d, "D1.3-000003", "round0", "probes", "logs"); os.makedirs(unmarked)
    with open(os.path.join(unmarked, "D1.3-a.log"), "w") as fh: fh.write("CRASH no_marker AttributeError w\n")
    out = lessons(d, now=now)
    cases = [("findings are counted by class and probe name, the window excludes old logs", "3 findings" in out and "2 raw TypeError" in out and "1 accepted a hostile input" in out and "AttributeError" not in out and "none_input (1)" in out),
             ("no log at all gives an empty block, never a guess", lessons(os.path.join(d, "none")) == ""),
             ("a log with no probe-at marker is excluded, never guessed", "no_marker" not in out)]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


def main():
    with G.local_slot():
        return _main()


def _drain(fd, sink):
    """Read the record channel to its end on a thread, so a full pipe can never stall the child."""
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return
            sink.append(chunk)
    except OSError:  # sbe: allow-silent a read error ends the channel early; the caller then finds no end record and reports the run unfinished, never clean
        return
    finally:
        os.close(fd)


def _records(data, nonce):
    """(records, stray, ended) from the channel's bytes. A record is [label, type, module, own, at_call, text]. A line that
    does not carry this run's nonce, or has the wrong shape, is STRAY: someone other than the harness wrote it."""
    records, stray, ended = [], 0, False
    for raw in data.split(b"\n"):
        if not raw:
            continue
        parts = [re.sub(r"\s", " ", x) for x in raw.decode("utf-8", "replace").split("\t")]
        if parts[0] != nonce:
            stray += 1
        elif parts[1:] == ["END"]:
            ended = True
        elif len(parts) == 8 and parts[1] == "PROBE":
            records.append(parts[2:])
        else:
            stray += 1
    return records, stray, ended


def _main():
    build = G.load(sys.argv[1])
    with open(sys.argv[2], encoding="utf-8") as fh:
        raw = fh.read()
    must_block = set()
    try:
        doc = G.load(sys.argv[2]) if raw.lstrip().startswith(("{", "```")) else None
        body = doc.get("probe_script") if doc else raw
        must_block = {str(x)[:70] for x in (doc.get("expect_block") or [])} if doc else set()
    except ValueError:
        body = raw
    why = admission(body)
    if why:
        print("NO-DATA: %s; nothing was run" % why); return 2
    hit = G.BLOCK.search(body)
    if hit:
        print("FAIL safety screen: probe contains %r" % hit.group(0)); return 1
    root = G.scratch("probe")
    try:
        problems = []
        G.apply(root, build.get("edits"), problems, "edit"); G.apply(root, build.get("tests"), problems, "tests")
        if problems:
            print("FAIL build does not apply: %s" % problems[:2]); return 1
        with open(os.path.join(root, "_hostile_probe.py"), "w", encoding="utf-8") as f:
            f.write(HARNESS + "\n" + body + TRAILER)
        sandbox = os.path.join(root, ".grade-sandbox"); os.makedirs(sandbox, exist_ok=True)
        os.makedirs(os.path.join(sandbox, "tmp"), exist_ok=True)
        # the grader's and the lander's ONE definition (2026-10-03): this copy kept the run's knobs and credential names
        env = G.suite_env(os.environ, sandbox)
        # THE PROBE CHILD RUNS IN THE GRADER'S SANDBOX (audit finding 4, 2026-09-27). It ran straight through Python,
        # so a build module the probe imported wrote outside its scratch tree and the probe still exited 0. Same
        # wrapper, same profile as every graded test. FAIL DIRECTION: a sandbox that is refused or absent is NO-DATA,
        # never an unsandboxed run; only BROTHER_SANDBOX=off runs it bare, and grade_build.sandboxed says so aloud.
        try:
            cmd = G.sandboxed([sys.executable, "-B", "_hostile_probe.py"], root, os.path.join(sandbox, "tmp"))
        except G.SandboxRefused as exc:
            print("NO-DATA: the probe cannot run sandboxed (%s); it is never run unsandboxed" % exc); return 2
        if cmd[:1] != ["sandbox-exec"] and not G._opted_out():
            print("NO-DATA: no sandbox on this host; the probe is never run unsandboxed (BROTHER_SANDBOX=off runs it bare, and says so)"); return 2
        rfd, wfd = os.pipe(); nonce = secrets.token_hex(16); chunks = []
        reader = threading.Thread(target=_drain, args=(rfd, chunks), daemon=True); reader.start()
        env["BROTHER_PROBE_FD"] = str(wfd)
        # A TIMEOUT IS AN ABORTED RUN, NOT AN EMPTY ONE (X3 finding 2, 2026-09-27). It returned here, before the channel
        # was read, so a CRASH the harness had already reported through it vanished and a clean second adversary made
        # the lane CLEAN. The child is dead once run() raises; the channel is drained and every record is published
        # below, and the run ends as unwhole: no counts line, exit 1 when dirt was seen, else 2 (NO-DATA, never CLEAN).
        r = None
        try:
            r = subprocess.run(cmd, cwd=root, env=env, input=nonce + "\n", capture_output=True, text=True, timeout=180, pass_fds=(wfd,))
        except subprocess.TimeoutExpired:
            pass   # sbe: allow-silent r stays None, which the unwhole rule below reports as a timed out, aborted run
        finally:
            os.close(wfd)
        reader.join(10)
        records, stray, ended = _records(b"".join(chunks), nonce)
        # NO-DATA is a first class bucket, never a kind of pass: it is every probe that never reached the target.
        counts = {"CRASH": 0, "REFUSED": 0, "RETURNED": 0, "WRONG-ACCEPT?": 0, "NO-DATA": 0}
        for label, name, module, own, at, value in records:
            kind = verdict_of(label, classify(name, module, value, own == "1", at == "1"), value, must_block)
            counts[kind] += 1
            print("%-8s %-70s %s %s" % (kind, label, name if kind != "RETURNED" else "", value[:120]))
        # A probe that never reached the target function is not a case that ran, so it is not in this total.
        total = sum(v for k, v in counts.items() if k != "NO-DATA")
        dirt = counts["CRASH"] + counts["WRONG-ACCEPT?"]
        G.record_claim("probe", str(sys.argv[1]), "this build is clean, so it is worth landing",
                       (total - dirt) / float(total) if total else None)
        # A WHOLE RUN is exit 0, the end record, nothing stray on the channel, and the channel closed. Anything less is
        # an unfinished run: exit 0 alone was a build calling os._exit(0) in the middle of the probes.
        unwhole = ""
        if r is None:
            unwhole = "the probe timed out after 180 s and was killed"
        elif r.returncode == 0:
            if stray:
                unwhole = "%d line(s) on the record channel this run did not write" % stray
            elif reader.is_alive():
                unwhole = "the record channel stayed open after the child exited"
            elif not ended:
                unwhole = "the script stopped before its last line (no end record)"
        if unwhole:
            print("PROBES-ABORTED  %s after %d line(s); counts withheld, an unfinished run cannot say the build is clean" % (unwhole, total))
            print("FAIL the probe run is not whole: %s" % unwhole)
            return 1 if dirt else 2
        if r.returncode != 0:
            # THE COUNTS LINE IS WITHHELD WHEN THE CHILD DIED, and that withholding is the whole control.
            # Measured 2026-09-22 on this file: a probe that ran one harmless case and then raised printed
            # "PROBES   1 run: 0 CRASH, 0 WRONG-ACCEPT?, 0 REFUSED, 1 RETURNED" and only THEN reported the
            # death. probe_wave reads the counts line and ignored probe-exit, so one executed case out of an
            # unknown number became a CLEAN verdict for the whole lane. A run that did not finish cannot say
            # how many cases it would have fired, so it may not offer a denominator at all.
            # FAIL DIRECTION: dirt already observed before the death is still dirt (exit 1); no dirt observed
            # is an unknown (exit 2), never 0.
            print("PROBES-ABORTED  child exit=%s after %d line(s); counts withheld, an unfinished run cannot say the build is clean" % (r.returncode, total))
            print("FAIL the probe script itself died (exit %s): %s" % (r.returncode, r.stderr.strip().splitlines()[-1][:200] if r.stderr.strip() else "no stderr"))
            return 1 if dirt else 2
        print("PROBES   %d run: %d CRASH, %d WRONG-ACCEPT?, %d REFUSED, %d RETURNED, %d NO-DATA" % (total, counts["CRASH"], counts["WRONG-ACCEPT?"], counts["REFUSED"], counts["RETURNED"], counts["NO-DATA"]))
        if total == 0:
            # Zero CONCLUSIVE probes, whether the file fired nothing at all or fired only calls that never
            # reached the target. Both are unknowns. FAIL DIRECTION: exit 2, which no caller may read as a pass.
            print("NO-DATA: the probe fired %d case(s) and none of them reached the build" % counts["NO-DATA"]); return 2
        return 1 if dirt else 0
    finally:
        if root not in G.PERSISTENT:   # a slot sandbox is reset at its next use, never removed and recloned
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__" and "--selftest" in sys.argv: sys.exit(selftest())
if __name__ == "__main__":
    sys.exit(main())
import sys
import re
import os
