#!/usr/bin/env python3
"""The unit done checks the loop can EXECUTE, for the units whose plan field was a sentence.

Owner law 2026-09-21: a done check is a COMMAND, never a sentence, its subject is the DELIVERABLE, never a fixture,
and it reaches three verdicts: red on the real tree, green on the real thing, NO-DATA on unreadable input. Measured
2026-09-22: six units in scope (L1b, L3, L3b, L5a, C0, R4) carried a done_check that scripts/close_unit.py's screen
refuses (prose, or a command with an `env` head and a dollar sign), so they could never close however much landed.

Each unit's check is one row in CHECKS: a real path, and one of three shapes. `unittest`: the deliverable's own
suite must run more than zero tests and pass. `points`: a report's `Point N name: <value>` lines must all be numbers
at or above a bar (NO-DATA on a report with no point lines). `outcome`: a recorded end to end run (JSON, the last
line of tests/e2e/antigravity/run_e2e.py) whose `outcome` is `loaded` and which names every hook event the spec
requires. The thin scripts/donecheck_<unit>.py wrappers call run(unit); close_unit's screen accepts them.
Exit 0 green, 1 red, 3 NO-DATA. Run: python3 scripts/donecheck_units.py <unit> | --selftest
"""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
CHECKS = {
    "L1b": {"shape": "outcome", "path": "docs/plan/evidence/L1b-antigravity-e2e.jsonl",
            "events": ["PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop"],
            "why": "the spec's acceptance is a REAL host run: scripts/e2e_antigravity.sh --real --log <this path> writes one JSON line per event with the host's verbatim version, the package hashes and the raw bytes beside the log; this check re-verifies all of it with tests/e2e/antigravity/verify_log.py and refuses a sandbox run or a bare summary (the old docs/plan/evidence/L1b-antigravity-e2e.json held only {outcome, events_fired}, no version, hashes or raw bytes)"},
    "L3": {"shape": "unittest", "path": "products/brothermode/vault_ui/test_read_surface.py", "also": "products/brothermode/vault_ui/test_write_boundary_l33.py",
           "why": "the vault human layer's landed modules and their suites beside them; L3.4 and L3.5 add theirs to this row when they land"},
    "L3b": {"shape": "unittest", "path": "products/brothermode/tools/tests/test_bm_vault_web_ui.py",
            "class": "TestL3bT16Acceptance", "why": "T-16: a real request to a running bm_vault_serve.py from the new UI"},
    "L5a": {"shape": "scored", "path": "docs/architecture/L5A-SECURITY-REVIEW.md",
            "record": "docs/architecture/l5a6_run_of_record.json", "bar": 8.5,
            "mutations_spec": "docs/plan/specs/L5a.md", "mutations_dir": "docs/architecture/l5a-mutations",
            "why": "L5a-11: the four scores are RECOMPUTED from the run of record by the L5a-6 scorer, the report's Point lines, "
                   "header sha and closing Blocking line must agree with them, and every point must be PASS at or above 8.5; "
                   "a hand edited Point line is RED, a record that cannot be read or scored is NO-DATA"},
    "C0": {"shape": "unittest", "path": "scripts/test_c0_fast_cut.py", "why": "the fast cut's own suite: fast path and full chain reach the same verdict"},
    "R4": {"shape": "unittest", "path": "scripts/test_jev_checks.py", "also": "scripts/test_jev_seam.py", "hermetic": True,
           "why": "no test reads or spends real machine state: both suites under an empty HOME"},
}


def check_unittest(spec, run):
    """(code, line). More than zero tests must run; the `class` key narrows to one class; `hermetic` runs under an empty HOME."""
    paths = [spec["path"]] + ([spec["also"]] if spec.get("also") else [])
    for p in paths:
        if not os.path.isfile(os.path.join(ROOT, p)): return 3, "NO-DATA: %s is not in the tree" % p
    total = 0
    for p in paths:
        target = p[:-3].replace("/", ".") + (("." + spec["class"]) if spec.get("class") else "")
        if spec.get("class"):
            src = open(os.path.join(ROOT, p), encoding="utf-8").read()
            if not re.search(r"^class %s\b" % re.escape(spec["class"]), src, re.M): return 1, "RED: %s has no class %s yet" % (p, spec["class"])
        rc, out = run([sys.executable, "-B", "-m", "unittest", target], spec.get("hermetic", False))
        m = re.search(r"^Ran (\d+) tests?", out, re.M)
        if not m: return 3, "NO-DATA: %s printed no 'Ran N tests' line" % p
        if int(m.group(1)) == 0: return 1, "RED: %s ran zero tests" % p
        if rc != 0: return 1, "RED: %s exit %d" % (p, rc)
        total += int(m.group(1))
    return 0, "GREEN: %d test(s) passed in %s" % (total, ", ".join(paths))


def check_points(spec, read):
    text = read(spec["path"])
    if text is None: return 3, "NO-DATA: %s cannot be read" % spec["path"]
    pts = re.findall(r"^- Point (\d+) [^:]+: *(\S+)", text, re.M)
    if not pts: return 3, "NO-DATA: no Point lines in %s" % spec["path"]
    bad = [n for n, v in pts if not re.match(r"^[0-9.]+$", v) or float(v) < spec["bar"]]
    return (1, "RED: point(s) %s not at %.1f in %s" % (", ".join(bad), spec["bar"], spec["path"])) if bad else (0, "GREEN: %d point(s) at or above %.1f" % (len(pts), spec["bar"]))


_POINT_NAMES = {1: "Secrets", 2: "Exec", 3: "Writes", 4: "Deps"}
_EVIDENCE_KEYS = ("command", "sha", "exit_code", "excerpt", "excerpt_hash", "point",
                  "hit_count", "mutation_id", "mutation_artifact", "audited_count")
_FINDING_KEYS = ("id", "control", "target_file", "target_function", "status", "point", "limitation")


def collector_excerpt(output):
    """The L5a-6 collector's truncation rule, the one place it is written: the first 50 lines, a trailing newline kept
    when the output had one and fits, then at most 4096 bytes of utf-8."""
    lines = output.splitlines()
    excerpt = "\n".join(lines[:50])
    if lines and output.endswith("\n") and len(lines) <= 50:
        excerpt += "\n"
    return excerpt.encode("utf-8")[:4096].decode("utf-8", "ignore")


def _git_lines(argv):
    """git's stdout lines in ROOT, or None when git refuses (a refusal is NO-DATA, never a pass)."""
    try:
        r = subprocess.run(["git"] + list(argv), cwd=ROOT, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.splitlines()


def _spec_mutations(text):
    """The T ids the spec's section 10 names (`- T1 `...), read from the spec, never a hardcoded copy."""
    section = re.search(r"^## 10\..*?(?=^## 11\.|\Z)", text, re.M | re.S)
    if not section: return None
    ids = re.findall(r"^- (T\d+) `", section.group(0), re.M)
    return ids or None


def _threshold_ten(spec, read, record, replay):
    """Threshold 10 is machine enforced (attack 2026-09-30: deleting EV-015 to EV-023 left the check GREEN): one
    consistent, red mutation row per T the spec names, its artifact under the mutations directory. A probe row that
    recorded NO-DATA is NO-DATA, never red."""
    text = read(spec["mutations_spec"])
    if text is None: return 3, "NO-DATA: %s cannot be read" % spec["mutations_spec"]
    wanted = _spec_mutations(text)
    if not wanted: return 3, "NO-DATA: %s names no T mutations in its section 10" % spec["mutations_spec"]
    consistent = {r["order"]: r["consistent"] for r in replay if isinstance(r, dict) and "order" in r}
    seen, bad = {}, []
    for order, entry in enumerate(record["evidence"], 1):
        if not isinstance(entry, dict) or entry.get("mutation_id") is None: continue
        art = entry.get("mutation_artifact")
        if not isinstance(art, str) or os.path.normpath(os.path.dirname(art)) != os.path.normpath(spec["mutations_dir"]):
            bad.append("%s: artifact outside %s" % (entry.get("id", "?"), spec["mutations_dir"])); continue
        name = os.path.basename(art)[:-5] if art.endswith(".json") else ""
        excerpt = entry.get("excerpt", "")
        if entry.get("exit_code") == 3 or excerpt.startswith("NO-DATA") or "\nNO-DATA" in excerpt:
            return 3, "NO-DATA: probe row %s recorded NO-DATA for %s" % (entry.get("id", "?"), name)
        if entry.get("exit_code") != 0 or not consistent.get(order) or not re.search(r"^MUTATION \S+: RED$", excerpt, re.M):
            bad.append("%s: not a consistent red probe row" % entry.get("id", "?")); continue
        seen[name] = entry.get("id")
    missing = [t for t in wanted if t not in seen]
    if missing or bad:
        return 1, "RED: threshold 10 (mutations %s): missing %s%s" % (", ".join(wanted), ", ".join(missing) or "none",
                                                                     ("; " + "; ".join(bad)) if bad else "")
    return 0, ""


def _record_binds_to_the_tree(spec, record, git, run_cmd):
    """The record must describe THIS tree, not merely itself (attack 2026-09-30: fabricated excerpts with recomputed
    hashes and an all zero sha read GREEN). Three binds: the record's sha is a commit reachable from HEAD; the tree at
    that sha differs from HEAD only in the report and the record (the rule the report states); every evidence command
    re-run from ROOT gives the recorded exit code and excerpt hash under the collector's own truncation rule."""
    sha = record["sha"]
    if git(["cat-file", "-t", sha]) != ["commit"]:
        return 1, "RED: the record's sha %s is not a commit in this repository" % sha
    if git(["merge-base", "--is-ancestor", sha, "HEAD"]) is None:
        return 1, "RED: the record's sha %s is not reachable from HEAD" % sha
    changed = git(["diff", "--name-only", sha, "HEAD"])
    if changed is None:
        return 3, "NO-DATA: git diff %s HEAD refused" % sha
    allowed = {spec["path"], spec["record"]}
    stray = sorted(set(changed) - allowed)
    if stray:
        return 1, "RED: the tree moved since the record's sha in %d file(s) other than the report and the record: %s" % (len(stray), ", ".join(stray[:5]))
    for entry in record["evidence"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
            return 3, "NO-DATA: an evidence row carries no command string"
        code, output = run_cmd(["bash", "-c", entry["command"]], False)
        excerpt = collector_excerpt(output)
        digest = __import__("hashlib").sha256(excerpt.encode("utf-8")).hexdigest()
        if code != entry.get("exit_code") or digest != entry.get("excerpt_hash"):
            return 1, "RED: evidence %s does not reproduce on this tree (exit %s recorded %s, excerpt hash %s)" % (
                entry.get("id", "?"), code, entry.get("exit_code"), "matches" if digest == entry.get("excerpt_hash") else "differs")
    return 0, ""


def check_scored(spec, read, score=None, run_cmd=None, git=None):
    """L5a-11: the verdict comes from the run of record, never from the report's hand editable lines.
    The record is rescored with the L5a-6 scorer; the report must carry exactly one Point line per point that
    agrees with the recomputed score and verdict, a header sha equal to the record's, and a closing Blocking line
    that agrees with the bar. Any disagreement is RED. Unreadable, malformed or unscorable input is NO-DATA."""
    text = read(spec["path"])
    if text is None: return 3, "NO-DATA: %s cannot be read" % spec["path"]
    raw = read(spec["record"])
    if raw is None: return 3, "NO-DATA: %s cannot be read" % spec["record"]
    try: record = json.loads(raw)
    except ValueError as exc: return 3, "NO-DATA: %s is not JSON (%s)" % (spec["record"], exc.__class__.__name__)
    if not isinstance(record, dict) or not isinstance(record.get("evidence"), list) \
            or not isinstance(record.get("findings"), list) or not isinstance(record.get("sha"), str) \
            or not re.match(r"^[0-9a-f]{40}$", record["sha"]):
        return 3, "NO-DATA: %s is not a record with an evidence list, a findings list and a 40 hex sha" % spec["record"]
    if score is None:
        if not any(os.path.realpath(p) == os.path.realpath(ROOT) for p in sys.path if isinstance(p, str)):
            sys.path.insert(0, ROOT)   # the command runs with scripts/ first on sys.path; the scorer is a package under ROOT
        try:
            from plugin.runtime.brother.core.l5a6_score import score_and_bind as score
        except ImportError as exc:
            return 3, "NO-DATA: the L5a-6 scorer cannot be imported (%s)" % exc
    evidence = [{k: e[k] for k in _EVIDENCE_KEYS if k in e} for e in record["evidence"] if isinstance(e, dict)]
    findings = [{k: f[k] for k in _FINDING_KEYS if k in f} for f in record["findings"] if isinstance(f, dict)]
    try:
        scores, _replay = score(evidence, findings)
    except (ValueError, ImportError, TypeError) as exc:
        return 3, "NO-DATA: the scorer refused the record (%s: %s)" % (exc.__class__.__name__, str(exc)[:160])
    by_point = {}
    for entry in scores:
        try: by_point[int(entry["point"])] = (str(entry["verdict"]), float(entry["score"]))
        except (KeyError, TypeError, ValueError): return 3, "NO-DATA: the scorer returned a malformed score"
    if sorted(by_point) != [1, 2, 3, 4]: return 3, "NO-DATA: the scorer returned points %s" % sorted(by_point)
    bad = []
    lines = re.findall(r"^- Point (\d+) ([^:]+): *(.*)$", text, re.M)
    seen = {}
    for number, _name, value in lines:
        seen.setdefault(int(number), []).append(value.split())
    for point in (1, 2, 3, 4):
        values = seen.get(point, [])
        if len(values) != 1:
            bad.append("point %d has %d Point line(s), expected one" % (point, len(values))); continue
        tokens = values[0]
        verdict, value = by_point[point]
        if verdict == "NO-DATA":
            if not tokens or tokens[0] != "NO-DATA": bad.append("point %d reads %r, the record says NO-DATA" % (point, " ".join(tokens)))
        elif len(tokens) < 2 or tokens[0] != "%.1f" % value or tokens[1] != verdict:
            bad.append("point %d reads %r, the record says %.1f %s" % (point, " ".join(tokens), value, verdict))
    for number in sorted(seen):
        if number not in (1, 2, 3, 4): bad.append("a Point %d line that no point owns" % number)
    header = re.findall(r"^- commit_sha: *(\S+)", text, re.M)
    if len(header) != 1 or header[0] != record["sha"]:
        bad.append("header commit_sha %s, the record says %s" % (header[0] if header else "missing", record["sha"]))
    below = [p for p in (1, 2, 3, 4) if by_point[p][0] != "PASS" or by_point[p][1] < spec["bar"]]
    blocking = re.findall(r"^Blocking: *(\S+)", text, re.M)
    want = "true" if below else "false"
    if not blocking or not blocking[-1].rstrip(".,;").lower() == want:
        bad.append("the closing Blocking line reads %r, the record says %s" % (blocking[-1] if blocking else "missing", want))
    if bad: return 1, "RED: the report disagrees with the record: " + "; ".join(bad)
    if below: return 1, "RED: point(s) %s not PASS at %.1f in %s (recomputed from %s)" % (", ".join(str(p) for p in below), spec["bar"], spec["path"], spec["record"])
    code, line = _threshold_ten(spec, read, record, _replay)
    if code: return code, line
    code, line = _record_binds_to_the_tree(spec, record, git or _git_lines, run_cmd or _run)
    if code: return code, line
    return 0, "GREEN: 4 point(s) PASS at or above %.1f, recomputed from %s, agreed by %s, and the record reproduces on this tree (%d evidence command(s) re-run at %s)" % (
        spec["bar"], spec["record"], spec["path"], len(record["evidence"]), record["sha"][:9])


def check_outcome(spec, read, root=ROOT):
    """(code, line) for L1b. The recorded run log is RE-VERIFIED, never trusted: tests/e2e/antigravity/verify_log.py
    re-hashes every raw byte file beside it, checks the five events, the sequence, the pinned host version and the
    corrupt-input rules, and this adds that every entry says environment real. `read` is unused: the raw bytes must be
    hashed from disk. Missing or empty log is NO-DATA; a sandbox run, a summary with no evidence, a missing or altered
    raw file, or no pinned host version is RED."""
    path = os.path.join(root, spec["path"])
    if not os.path.isfile(path): return 3, "NO-DATA: %s is not recorded yet: the real-host run waits on the owner's signed-in Antigravity install, then scripts/e2e_antigravity.sh --real --plugin <installed plugin> --log %s (%s)" % (spec["path"], spec["path"], spec["why"])
    try:
        import importlib.util
        vs = importlib.util.spec_from_file_location("l1b_verify_log", os.path.join(root, "tests", "e2e", "antigravity", "verify_log.py"))
        vl = importlib.util.module_from_spec(vs); vs.loader.exec_module(vl)
        res = vl.verify_log(path)
    except Exception as exc: return 3, "NO-DATA: the log verifier could not run (%s: %s)" % (type(exc).__name__, str(exc)[:120])
    if not res.get("ok"):
        why = str(res.get("reason", ""))
        return (3, "NO-DATA: %s: %s" % (spec["path"], why)) if why.startswith("no_data") else (1, "RED: %s: %s" % (spec["path"], why))
    entries = res["entries"]
    envs = sorted({e.get("environment") for e in entries})
    if envs != ["real"]: return 1, "RED: environment %s, not real: a sandbox run is not host evidence" % ", ".join(map(str, envs))
    fired = {e.get("event_name") for e in entries}
    missing = [e for e in spec["events"] if e not in fired]
    if missing: return 1, "RED: hook event(s) not fired: %s" % ", ".join(missing)
    return 0, "GREEN: real host %s %s, all %d hook events fired, raw bytes and hashes recomputed" % (entries[0]["host_name"], entries[0]["host_version"], len(entries))


def _read(p):
    try:
        with open(os.path.join(ROOT, p), encoding="utf-8") as f: return f.read()
    except OSError: return None


def _run(argv, hermetic):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": __import__("tempfile").mkdtemp(prefix="donecheck-home-")} if hermetic else dict(os.environ)
    r = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, env=env, timeout=1800)
    return r.returncode, r.stdout + r.stderr


def run(unit, run_cmd=_run, read=_read):
    spec = CHECKS.get(unit)
    if spec is None: return 3, "NO-DATA: no done check is defined for %s" % unit
    return {"unittest": lambda: check_unittest(spec, run_cmd), "points": lambda: check_points(spec, read), "outcome": lambda: check_outcome(spec, read),
            "scored": lambda: check_scored(spec, read)}[spec["shape"]]()


def selftest():
    try: return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1


def _l1b_cases():
    """The L1b gate against logs the REAL writer produced (run_e2e.run_sandbox and write_log, a fake host script
    standing in for the host binary), so writer, verifier and gate are proven together, one condition per case."""
    import importlib.util, shutil, stat, tempfile
    tmp = tempfile.mkdtemp(prefix="donecheck-l1b-")
    try:
        host = os.path.join(tmp, "fakehost")
        with open(host, "w") as f: f.write("#!/bin/sh\necho 9.9.9-selftest\n")
        os.chmod(host, os.stat(host).st_mode | stat.S_IXUSR)
        rs = importlib.util.spec_from_file_location("l1b_run_e2e", os.path.join(ROOT, "tests", "e2e", "antigravity", "run_e2e.py"))
        rm = importlib.util.module_from_spec(rs); rs.loader.exec_module(rm)
        log = os.path.join(tmp, "ev", "run.jsonl")
        result = rm.run_sandbox(os.path.join(ROOT, "bundle", ".antigravity-plugin"), host_bin=host, run_dir=log + ".raw")
        rm.write_log(result, log)
        lines = open(log).read().splitlines()
        def variant(name, edit=None, drop=None):
            d = os.path.join(tmp, name); shutil.copytree(os.path.dirname(log), d); p = os.path.join(d, "run.jsonl")
            recs = [json.loads(x) for x in lines]
            for r in recs:
                r["environment"] = "real"
                if edit: edit(r)
            with open(p, "w") as f: f.write("".join(json.dumps(r) + "\n" for r in recs))
            if drop: drop(d, recs)
            return p
        g = lambda p: check_outcome({"path": p, "events": CHECKS["L1b"]["events"], "why": "x"}, None)
        good = variant("good")
        def tamper(d, recs):
            with open(os.path.join(d, recs[0]["raw_out_path"]), "ab") as f: f.write(b" ")
        summary = os.path.join(tmp, "summary.jsonl")
        with open(summary, "w") as f: f.write('{"outcome": "loaded", "events_fired": %s}\n' % json.dumps(CHECKS["L1b"]["events"]))
        empty = os.path.join(tmp, "empty.jsonl"); open(empty, "w").close()
        moved = os.path.join(tmp, "moved"); shutil.copytree(os.path.dirname(good), moved)
        return [("L1b: the writer's own run is GREEN once it says real", result.get("ok") is True and g(good)[0] == 0),
                ("L1b: the same log moved elsewhere still verifies (raw paths are relative)", g(os.path.join(moved, "run.jsonl"))[0] == 0),
                ("L1b: a sandbox run is RED", g(os.path.join(os.path.dirname(log), "run.jsonl"))[0] == 1),
                ("L1b: no pinned host version is RED", g(variant("nover", edit=lambda r: r.update(host_version="no_data")))[0] == 1),
                ("L1b: an altered raw byte file is RED", g(variant("tamper", drop=tamper))[0] == 1),
                ("L1b: a deleted raw byte file is RED", g(variant("gone", drop=lambda d, recs: os.remove(os.path.join(d, recs[2]["raw_in_path"]))))[0] == 1),
                ("L1b: a summary with no version, hashes or raw bytes is RED", g(summary)[0] == 1),
                ("L1b: a log naming the wrong events is RED", g(variant("names", edit=lambda r: r.update(event_name="Stop")))[0] == 1),
                ("L1b: an empty log is NO-DATA", g(empty)[0] == 3),
                ("L1b: an unrecorded run is NO-DATA", g(os.path.join(tmp, "absent.jsonl"))[0] == 3)]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _selftest_body():
    import tempfile
    files = {}
    read = lambda p: files.get(p)
    ok = lambda argv, h: (0, "Ran 4 tests in 0.1s\n\nOK\n")
    u = {"shape": "unittest", "path": "scripts/test_c0_fast_cut.py"}
    cases = [("a unit with no row is NO-DATA", run("ZZ")[0] == 3),
             ("a passing suite that ran tests is GREEN", check_unittest(u, ok)[0] == 0),
             ("a suite that ran zero tests is RED, never green", check_unittest(u, lambda a, h: (0, "Ran 0 tests\n\nOK\n"))[0] == 1),
             ("a failing suite is RED", check_unittest(u, lambda a, h: (1, "Ran 4 tests\n\nFAILED (failures=1)\n"))[0] == 1),
             ("a suite that prints no Ran line is NO-DATA", check_unittest(u, lambda a, h: (0, "nothing\n"))[0] == 3),
             ("a missing suite file is NO-DATA", check_unittest({"shape": "unittest", "path": "scripts/absent_x.py"}, ok)[0] == 3),
             ("the hermetic flag reaches the runner", (lambda seen: (check_unittest(dict(CHECKS["R4"]), lambda a, h: seen.append(h) or (0, "Ran 1 test\n\nOK\n")), all(seen)))([])[1]),
             ("a narrowed class that does not exist yet is RED", check_unittest(dict(CHECKS["L3b"]), ok)[0] == 1 if not re.search(r"^class TestL3bT16Acceptance\b", open(os.path.join(ROOT, CHECKS["L3b"]["path"])).read(), re.M) else True)]
    files["r.md"] = "- Point 1 a: 9\n- Point 2 b: 8.5\n"; files["bad.md"] = "- Point 1 a: 9\n- Point 2 b: NO-DATA\n"; files["low.md"] = "- Point 1 a: 8\n"; files["none.md"] = "no points\n"
    p = lambda f: check_points({"shape": "points", "path": f, "bar": 8.5}, read)
    cases += [("all points at the bar is GREEN", p("r.md")[0] == 0), ("a NO-DATA point is RED", p("bad.md")[0] == 1), ("a point under the bar is RED", p("low.md")[0] == 1),
              ("no point lines is NO-DATA", p("none.md")[0] == 3), ("a missing report is NO-DATA", p("absent.md")[0] == 3)]
    cases += _l1b_cases()
    # THE EVENTS ARE THE HARNESS'S OWN (2026-09-27): this row asked for SessionStart, which the host does not have, while the
    # harness fires Stop, so the unit could never close; one table now, the harness's, and this case fails if they part
    _h = open(os.path.join(ROOT, "tests", "e2e", "antigravity", "run_e2e.py"), encoding="utf-8").read()
    _modes = re.search(r"^_EVENT_MODES = \{(.*?)^\}", _h, re.M | re.S)
    cases += [("L1b asks for exactly the events the harness fires", _modes is not None and set(re.findall(r'"(\w+)":', _modes.group(1))) == set(CHECKS["L1b"]["events"]))]
    home = _run([sys.executable, "-c", "import os; print(os.environ['HOME'])"], True)[1].strip()
    cases += [("a hermetic run sees an EMPTY home, never this machine's", home != os.path.expanduser("~") and os.path.basename(home).startswith("donecheck-home-"))]
    me = os.path.abspath(__file__)
    r = subprocess.run([sys.executable, "-B", me, "L5a"], capture_output=True, text=True)
    word = {0: "GREEN", 1: "RED", 3: "NO-DATA"}.get(r.returncode)
    cases += [("the entry point on the real L5a report prints the verdict word its exit code names", word is not None and r.stdout.startswith(word))]
    # the closing Blocking guard is tested on fixtures the case builds, whatever the live verdict is
    spec = CHECKS["L5a"]
    green_score = lambda e, f: ([{"point": p, "verdict": "PASS", "score": 9.0, "fix": None} for p in (1, 2, 3, 4)], [])
    rec = json.dumps({"sha": "b" * 40, "evidence": [], "findings": []})
    def report(blocking):
        return "- commit_sha: %s\n- Point 1 Secrets: 9.0 PASS\n- Point 2 Exec: 9.0 PASS\n- Point 3 Writes: 9.0 PASS\n- Point 4 Deps: 9.0 PASS\nBlocking: %s\n" % ("b" * 40, blocking)
    ok_git = lambda argv: ["commit"] if argv[0] == "cat-file" else []
    spec_text = "## 10. x\n\n- T1 `test_a`: x\n\n## 11. y\n"
    probe_out = "FAIL: test_a\nMUTATION M1: RED\n"
    probe = lambda: ([{"order": 1, "consistent": True}], [{"id": "EV-1", "command": "probe", "exit_code": 0, "excerpt": probe_out,
                       "excerpt_hash": __import__("hashlib").sha256(probe_out.encode("utf-8")).hexdigest(),
                       "mutation_id": "M1", "mutation_artifact": spec["mutations_dir"] + "/T1.json"}])
    def scorer(e, f):
        return ([{"point": p, "verdict": "PASS", "score": 9.0, "fix": None} for p in (1, 2, 3, 4)], probe()[0])
    rec = json.dumps({"sha": "b" * 40, "evidence": probe()[1], "findings": []})
    files = lambda text: (lambda p: text if p == spec["path"] else spec_text if p == spec["mutations_spec"] else rec)
    fx = lambda text, git=ok_git: check_scored(spec, files(text), score=scorer, run_cmd=lambda a, h: (0, "FAIL: test_a\nMUTATION M1: RED\n"), git=git)
    cases += [("a green fixture whose closing Blocking line agrees is GREEN", fx(report("false"))[0] == 0),
              ("a green fixture whose closing Blocking line says true is RED", fx(report("true"))[0] == 1 and "Blocking" in fx(report("true"))[1]),
              ("a record sha that git says is not reachable from HEAD is RED", fx(report("false"), lambda a: ["commit"] if a[0] == "cat-file" else (None if a[0] == "merge-base" else []))[0] == 1),
              ("a git diff that refuses is NO-DATA, never GREEN", fx(report("false"), lambda a: ["commit"] if a[0] == "cat-file" else (None if a[0] == "diff" else []))[0] == 3),
              ("a record with no mutation row for a spec T is RED", check_scored(spec, lambda p: report("false") if p == spec["path"] else spec_text if p == spec["mutations_spec"] else json.dumps({"sha": "b" * 40, "evidence": [], "findings": []}),
                                                                                  score=lambda e, f: ([{"point": p, "verdict": "PASS", "score": 9.0, "fix": None} for p in (1, 2, 3, 4)], []), run_cmd=lambda a, h: (0, ""), git=ok_git)[0] == 1)]
    for unit in CHECKS:
        w = os.path.join(HERE, "donecheck_%s.py" % unit)
        cases += [("scripts/donecheck_%s.py exists and calls run(%r)" % (unit, unit), os.path.isfile(w) and ("run(%r)" % unit) in open(w).read())]
    bad = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv: return selftest()
    if len(sys.argv) < 2: print(__doc__); return 2
    code, line = run(sys.argv[1]); print(line); return code


if __name__ == "__main__":
    sys.exit(main())
