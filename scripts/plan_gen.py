#!/usr/bin/env python3
"""PG1.b: one command builds the next release plan from the closing ceremony's seed.

    python3 scripts/plan_gen.py --seed docs/plan/seeds/1.1.5/SEED.json --version 1.1.5 [--dry] [--root DIR]

Writes docs/plan/BROTHER-<V>-WBS.json (schema brother-release-wbs/1), one stub spec per unit under
docs/plan/specs/<ID>.md, the pack skeleton under docs/plan/specs/<V>/pack/ and
docs/plan/specs/<V>/WRITER-ASSIGNMENTS.json with one batch per sub unit. Reader of scripts/plan_seed.py (PG1.a).
The pack headings come from the newest docs/plan/specs/<N>/pack_v*.py (read with ast, never imported or edited);
without one the command prints NO-DATA and writes nothing.

FAIL DIRECTION: every refusal happens before the first write. An id already tracked, a lint BLOCKING or NO-DATA
finding on the plan about to be written, an existing target, a dash or clock text in any rendered file, a seed of
another schema or a bad version: exit 1. Git unreadable, the template absent, the seed file unreadable: exit 2
(NO-DATA, never an empty taken set). Python 3.9, standard library only. Tests: scripts/test_plan_gen.py.
"""
import argparse
import ast
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import plan_seed  # noqa: E402

WBS_SCHEMA = "brother-release-wbs/1"
ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
CLOCK_RE = re.compile(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)")
DASHES = (chr(0x2013), chr(0x2014))
CACHE_LINES = 40  # lines pasted per cached read
LINE_CAP = 400  # characters pasted per cached line
EPIC = "E1"
EPIC_DIR = "E1_PLAN"
_TEST_PATH = re.compile(r"scripts/(?:loop/)?test_\w+\.py\Z")


def _plan_lint():
    """plan_lint, imported on first use (it lives in scripts/loop and pulls the grader in)."""
    sys.path.insert(0, os.path.join(HERE, "loop"))
    import plan_lint
    return plan_lint


def taken_ids(root: str) -> set:
    """Every <ID> with a tracked docs/plan/specs/<ID>.md (git ls-files) plus every unit id in every tracked
    docs/plan/BROTHER-*-WBS.json; git unreadable raises RuntimeError (NO-DATA, never an empty set)."""
    try:
        done = subprocess.run(["git", "-C", root, "ls-files", "-z", "--", "docs/plan"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("NO-DATA: git cannot run in %s (%s)" % (root, type(exc).__name__))
    if done.returncode != 0:
        raise RuntimeError("NO-DATA: git ls-files exited %d in %s: %s" % (
            done.returncode, root, done.stderr.decode("utf-8", "replace").strip()[:200]))
    ids = set()
    for rel in done.stdout.decode("utf-8", "replace").split("\0"):
        spec = re.fullmatch(r"docs/plan/specs/([^/]+)\.md", rel)
        if spec:
            ids.add(spec.group(1))
        elif re.fullmatch(r"docs/plan/BROTHER-[^/]*-WBS\.json", rel):
            try:
                with open(os.path.join(root, rel), encoding="utf-8") as f:
                    units = json.load(f)["units"]
                ids.update(u["id"] for u in units if isinstance(u, dict) and isinstance(u.get("id"), str))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise RuntimeError("NO-DATA: tracked plan %s is unreadable (%s)" % (rel, type(exc).__name__))
    return ids


def _plan_unit_ids(root: str, rel: object, cache: dict) -> set:
    """The unit ids of the plan file `rel` (relative to root, inside it), or an empty set when it is unsafe,
    unreadable or not a plan: an unreadable closing plan grants no exemption."""
    if not isinstance(rel, str):
        return set()
    if rel not in cache:
        full = plan_seed._safe_path(root, rel)
        ids = set()
        try:
            with open(full, encoding="utf-8") as f:
                ids = {u["id"] for u in json.load(f)["units"] if isinstance(u, dict) and isinstance(u.get("id"), str)}
        except (OSError, TypeError, ValueError, KeyError, UnicodeDecodeError):
            ids = set()
        cache[rel] = ids
    return cache[rel]


def collisions(units: List[dict], taken: set, root: Optional[str] = None) -> List[str]:
    """The unit ids of `units` that are in `taken`, sorted; a unit without a text id is a collision named '<no id>'.
    A CARRIED row (carried is exactly True) keeps its tracked id and is not a collision when `root` is given and
    its id is a unit of the plan its from_plan names, read from root; no root, an unsafe or unreadable from_plan
    or an id missing from that plan leave the clash standing."""
    out, cache = set(), {}
    for u in units:
        uid = u.get("id") if isinstance(u, dict) else None
        if not isinstance(uid, str) or not uid:
            out.add("<no id>")
        elif uid in taken:
            if root is not None and u.get("carried") is True and uid in _plan_unit_ids(root, u.get("from_plan"), cache):
                continue
            out.add(uid)
    return sorted(out)


def find_template(root: str) -> str:
    """The newest docs/plan/specs/<N.N.N>/pack_v*.py under root, else RuntimeError (NO-DATA)."""
    hits = []
    for path in glob.glob(os.path.join(root, "docs", "plan", "specs", "*", "pack_v*.py")):
        name = os.path.basename(os.path.dirname(path))
        if plan_seed.VERSION_RE.match(name):
            hits.append((tuple(int(n) for n in name.split(".")), path))
    if not hits:
        raise RuntimeError("NO-DATA: no docs/plan/specs/<N.N.N>/pack_v*.py template under %s" % root)
    return max(hits)[1]


def load_template(path: str) -> Tuple[List[str], List[str]]:
    """(HEADINGS, FILL) read from the template generator's module level assignments with ast, never executed."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError) as exc:
        raise RuntimeError("NO-DATA: template %s is unreadable (%s)" % (path, type(exc).__name__))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("HEADINGS", "FILL"):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    head, fill = found.get("HEADINGS"), found.get("FILL")
    if not (isinstance(head, list) and head and isinstance(fill, list) and len(fill) == len(head)
            and all(isinstance(s, str) for s in head + fill)):
        raise RuntimeError("NO-DATA: template %s has no HEADINGS and FILL lists of equal length" % path)
    return head, fill


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].rstrip("-")


def _grant(seed: dict, key: str) -> str:
    """The owner's words for `key`, or NO-DATA. A clock time in them is written hours, the letter h, minutes (the
    house form), never with a colon: the rendered plan refuses clock text, and start_run.sh needs its deadline with
    a colon, so a launchable grant must not make the generator refuse (the seed keeps the words exactly as given)."""
    g = seed.get("grant")
    v = g.get(key) if isinstance(g, dict) else None
    if not (isinstance(v, str) and v.strip()):
        return plan_seed.NO_DATA
    return CLOCK_RE.sub(lambda m: m.group(0).replace(":", "h"), v)


def _check_header(seed: dict, version: str) -> List[dict]:
    """The seed's units after the cheap checks (version, schema, the seed written for this release), else ValueError."""
    if not isinstance(version, str) or not plan_seed.VERSION_RE.match(version):
        raise ValueError("version %r is not N.N.N" % (version,))
    if not isinstance(seed, dict) or seed.get("schema") != plan_seed.SEED_SCHEMA:
        raise ValueError("the seed is not a %s record" % plan_seed.SEED_SCHEMA)
    if seed.get("version") != version:
        raise ValueError("the seed is for release %r, not %s" % (seed.get("version"), version))
    units = seed.get("units")
    if not isinstance(units, list):
        raise ValueError("the seed units are not a list")
    return units


def _check_seed(seed: dict, version: str) -> List[dict]:
    """The seed units, or ValueError naming every row defect at once."""
    units = _check_header(seed, version)
    bad, seen = [], set()
    for u in units:
        uid = u.get("id") if isinstance(u, dict) else None
        if not isinstance(uid, str) or not ID_RE.match(uid):
            bad.append("unit id %r is not a plain id" % (uid,))
            continue
        if uid in seen:
            bad.append("unit id %s appears twice" % uid)
        seen.add(uid)
        owns = u.get("owns")
        if not isinstance(owns, list) or not owns or not all(isinstance(o, str) and o.strip() for o in owns):
            bad.append("unit %s has no owns (a unit with no owns cannot be linted): the planner fills it in the seed" % uid)
        for key in ("after", "reads"):
            if u.get(key) is not None and not (isinstance(u[key], list) and all(isinstance(s, str) for s in u[key])):
                bad.append("unit %s: %s is not a list of text" % (uid, key))
        subs = u.get("sub_units")
        if u.get("carried") is True:
            if not (isinstance(subs, list) and subs and all(isinstance(x, str) and re.fullmatch(re.escape(uid) + r"\.[A-Za-z0-9]+", x) for x in subs)):
                bad.append("carried unit %s: sub_units must be ids %s.<name>, got %r" % (uid, uid, subs))
            for key in ("spec", "done_check", "worker"):
                if not (isinstance(u.get(key), str) and u[key].strip()):
                    bad.append("carried unit %s: %s is missing" % (uid, key))
        elif subs is not None and not (isinstance(subs, list) and all(isinstance(x, dict) for x in subs)):
            bad.append("unit %s: sub_units are not rows (ids belong to a carried unit)" % uid)
        if u.get("reads_cache") is not None and not (isinstance(u["reads_cache"], list)
                                                       and all(isinstance(r, dict) for r in u["reads_cache"])):
            bad.append("unit %s: reads_cache is not a list of rows" % uid)
    if bad:
        raise ValueError("; ".join(bad))
    return units


def _subs(u: dict) -> List[dict]:
    """The unit's sub units: its own `sub_units` rows (id, title, files, done_check) or one `<ID>.a` over all owns.
    A sub unit with no test file among its files and no done_check of its own is a ValueError."""
    uid, rows = u["id"], u.get("sub_units") or [{}]
    out = []
    for i, row in enumerate(rows):
        sid = row.get("id", "%s.%s" % (uid, "abcdefghijklmnopqrstuvwxyz"[i % 26]))
        if not isinstance(sid, str) or not re.fullmatch(re.escape(uid) + r"\.[A-Za-z0-9]+", sid):
            raise ValueError("sub unit id %r of %s is not %s.<name>" % (sid, uid, uid))
        files = row.get("files") or u["owns"]
        check = row.get("done_check") or next(
            ("python3 -B " + f for f in files if _TEST_PATH.match(os.path.normpath(f))), None)
        if not check:
            raise ValueError("sub unit %s names no done check and owns no scripts/test_*.py file" % sid)
        out.append({"id": sid, "title": str(row.get("title") or u.get("title") or sid), "files": list(files), "done_check": check})
    return out


def _cache_block(rows: List[dict]) -> List[str]:
    out = []
    for r in rows:
        head = "%s%s%s" % (r.get("path", "?"), " line %s" % r["line"] if "line" in r else "",
                           " sha256 %s" % r["sha256"][:12] if r.get("sha256") else "")
        lines = str(r.get("text", "NO-DATA: the cached read has no text")).split("\n")
        cut = len(lines) > CACHE_LINES or any(len(x) > LINE_CAP for x in lines) or bool(r.get("truncated"))
        out += ["    " + head + (" (truncated)" if cut else "")]
        out += ["    " + x[:LINE_CAP] for x in lines[:CACHE_LINES]]
        out += [""]
    return out


def _spec_text(u: dict, subs: List[dict], worker: str, version: str, now: str, root: str) -> str:
    """The stub spec of one unit: the 1.1.4 skeleton, one section per sub unit, headings and done checks fixed."""
    uid, cache, reads = u["id"], u.get("reads_cache") or [], u.get("reads") or []
    check = " && ".join(dict.fromkeys(s["done_check"] for s in subs))
    out = ["# %s: %s" % (uid, u.get("title") or uid), "",
           "SKELETON written %s by scripts/plan_gen.py from the seed of release %s (origin %s); a spec writer fills it to "
           "9 of 10 per docs/plan/specs/LOOP-LANDING-STANDARD.md. Source: %s." % (now, version, u.get("origin", "NO-DATA"), u.get("source", "")), "",
           "## 1. Measured baseline and what is missing", "",
           "WRITER: quote the current code with file and line numbers you read (start from the cached reads below), name the exact gap, sweep the callers of every function you will change and report the count.", "",
           "Objective (from the seed; the spec makes it buildable, it does not widen it): %s" % (u.get("objective") or ""), "",
           "Reads: " + "; ".join(reads), ""]
    if cache:
        out += ["Cached reads (quoted from the tree when the seed was written; re-read a region whose sha256 no longer matches):", ""]
        out += _cache_block(cache)
    out += ["## 2. Sub-units", ""]
    for s in subs:
        labelled = ", ".join("`%s` (%s)" % (p, "existing" if os.path.exists(os.path.join(root, p)) else "NEW") for p in s["files"])
        out += ["### %s %s" % (s["id"], s["title"]), "", "Files: %s." % labelled, "", "Built by: %s." % worker, "",
                "WRITER: a python block of typed signatures (NEW or changed); behavior in prose; Red before green naming the new test case that fails on the base and why; a requirements table (ID, requirement, control point and check).", "",
                "Done check: `%s`" % s["done_check"], "", "#### Edges and failures", "",
                "WRITER: empty, exactly one, many, unknown value, corrupt or truncated input, a concurrent second actor, stale, already done, partially done, the same actor as last time; the fail direction of every new branch; at least one mutation per guard.", "",
                "Checklist (one key per line):", "", "```",
                "S1 own test file: yes", "S2 done check own only: yes", "S3 no shared file: WRITER names the siblings in flight",
                "S4 mirrored file: WRITER", "S5 red case named: WRITER", "S6 base commit: WRITER quotes the lines",
                "S7 hostile inputs: WRITER lists them", "S8 mutation per guard: WRITER lists them", "S9 size: under about 150 lines",
                "S10 landing route: WRITER", "S11 fail direction: WRITER names it per branch", "S12 outside content: none", "```", ""]
    out += ["## 3. Parent close and evidence", "",
            "Both interpreters after the last edit: `%s` and the same with `/usr/bin/python3 -B`. The mutation sweep's survivor count per sub unit goes in the evidence beside the green." % check, ""]
    return "\n".join(out)


def _pack_text(s: dict, uid: str, spec_path: str, worker: str, heads: List[str], fills: List[str]) -> str:
    head = ["# %s: %s" % (s["id"], s["title"]), "", "> **Loop unit:** `%s` (spec %s)  " % (uid, spec_path),
            "> **State:** SKELETON / NOT IMPLEMENTED / TESTS NOT RUN  ", "> **Built by:** %s  " % worker,
            "> **done_check:** `%s`  " % s["done_check"], ""]
    return "\n".join(head + [x for h, f in zip(heads, fills) for x in (h, "", f, "")])


def _record(u: dict, version: str, now: str, worker: str, sub_ids: List[str], check: str, spec_path: str,
            checker: str, reason: str) -> dict:
    """One WBS unit record in the 1.1.4 key order."""
    why = reason.replace("state ", "").split(",")[0]
    return {
        "id": u["id"], "family": "%s %s: planned from the seed" % (version, EPIC), "epic": EPIC, "wave": 1, "lane": "loop-lane",
        "plan_start": now, "plan_end": now, "size": plan_seed.NO_DATA, "size_hours": [], "owner_hand": False,
        "title": str(u.get("title") or u["id"]), "objective": str(u.get("objective") or ""),
        "user_outcome": plan_seed.NO_DATA + ": the planner states the outcome", "state": why if why in ("DEFERRED", "PARKED") else "OPEN",
        "owns": list(u["owns"]), "sub_units": sub_ids, "worker": worker, "checker": checker,
        "reads": list(u.get("reads") or []), "done_check": check, "after": list(u.get("after") or []),
        # depends_on carries after: runner_pool holds a unit only on depends_on (the 1.1.3 lesson 4 defect, again in 1.1.4).
        "depends_on": list(u.get("after") or []),
        "source": str(u.get("source") or u.get("origin") or ""), "remains": "everything", "spec": spec_path}


def render_plan(seed: dict, version: str, now: str, root: Optional[str] = None,
                template: Optional[Tuple[List[str], List[str]]] = None) -> Dict[str, object]:
    """{wbs, specs: {path: text}, pack: {path: text}, assignments, wbs_path, assignments_path} in the exact shapes of
    the 1.1.4 files (schema brother-release-wbs/1, the skeleton headings, the template's pack headings, one batch
    per sub unit), every NEW path labelled NEW (absent under `root`), every reads quote from the seed's cache pasted
    under the unit's Reads line. A seed of another schema or a version that is not N.N.N raises ValueError; the
    template absent raises RuntimeError (NO-DATA)."""
    units = _check_seed(seed, version)
    root = ROOT if root is None else root
    heads, fills = template if template is not None else load_template(find_template(root))
    pl = _plan_lint()
    specs, pack, records, subunits, batches, last_sub = {}, {}, [], [], [], {}
    for u in units:
        last_sub[u["id"]] = u["sub_units"][-1] if u.get("carried") is True else _subs(u)[-1]["id"]
    for u in units:
        uid = u["id"]
        if u.get("carried") is True:  # keeps its tracked id and its existing spec: a WBS row only, no spec, pack or batch
            spec_path = u["spec"]
            full = plan_seed._safe_path(root, spec_path)
            if full is None or not os.path.isfile(full):
                raise ValueError("carried unit %s: its spec %r is not an existing file inside the root" % (uid, spec_path))
            records.append(_record(u, version, now, u["worker"], list(u["sub_units"]), u["done_check"], spec_path,
                                   str(u.get("checker") or u["done_check"]), str(u.get("reason", ""))))
            if u.get("evidence"):   # a sub unit landed in the closing plan stays landed (plan_store.sub_landed)
                records[-1]["evidence"] = str(u["evidence"])
            continue
        subs = _subs(u)
        worker = u.get("worker") or ("Brother.Loop (loop seats)" if not any(pl.protected_reason(p) for p in u["owns"])
                                     else "the session, direct (an owned path is protected or generated)")
        spec_path, reads, cache = "docs/plan/specs/%s.md" % uid, list(u.get("reads") or []), u.get("reads_cache") or []
        check = " && ".join(dict.fromkeys(s["done_check"] for s in subs))
        records.append(_record(u, version, now, worker, [s["id"] for s in subs], check, spec_path, check, str(u.get("reason", ""))))
        specs[spec_path] = _spec_text(u, subs, worker, version, now, root)
        for i, s in enumerate(subs):
            ppath = "docs/plan/specs/%s/pack/epics/%s/%s_%s.md" % (version, EPIC_DIR, s["id"], _slug(s["title"]))
            deps = ([subs[i - 1]["id"]] if i else []) + [last_sub[a] for a in (u.get("after") or []) if a in last_sub]
            pack[ppath] = _pack_text(s, uid, spec_path, worker, heads, fills)
            subunits.append({"id": s["id"], "wbs_id": uid, "title": s["title"], "path": os.path.relpath(ppath, "docs/plan/specs/%s/pack" % version),
                             "state": "SKELETON", "deps": deps, "done_check": s["done_check"], "owns": list(s["files"]), "worker": worker})
            batches.append({"id": s["id"], "name": s["title"], "units": [uid], "sub_units": [s["id"]],
                            "section": {"file": spec_path, "sub": s["id"]}, "writes": [ppath],
                            "reads": reads + [p for p in s["files"] if os.path.exists(os.path.join(root, p))],
                            "cached_reads": cache, "brief": "docs/plan/specs/%s/briefs/%s.md" % (version, s["id"]),
                            "report": "docs/plan/specs/%s/REPORT-%s.md" % (version, s["id"]), "done_check": s["done_check"]})
    wbs = {
        "schema": WBS_SCHEMA, "initiative": "Brother %s: plan generated from the closing ceremony seed" % version,
        "eyebrow": "Brother %s WBS, generated %s by scripts/plan_gen.py" % (version, now), "as_of": now,
        "version_of_plan": "v1, %s: generated from the seed written %s" % (now, str(seed.get("generated_at", plan_seed.NO_DATA))[:10]),
        "north_star": plan_seed.NO_DATA + ": the planner states the goal in the owner's words",
        "the_journey": plan_seed.NO_DATA + ": the planner writes the journey",
        "epics": [{"id": EPIC, "title": "Release %s units" % version, "outcome": plan_seed.NO_DATA + ": the planner states the outcome",
                   "units": [u["id"] for u in units]}],
        "sources": ["%s: %s %s" % (s.get("name"), s.get("status"), s.get("why", "")) for s in seed.get("sources", []) if isinstance(s, dict)],
        "glance": {"NOW": "plan generated; the specs are skeletons", "WAITING ON KHALIL": "the grant lines marked NO-DATA", "NEXT": "writers fill the specs to 9"},
        "cut_band": [now, now], "calendar": {"start": now, "end": plan_seed.NO_DATA, "basis": "generated; the planner sets the window"},
        "waves": [{"id": 1, "title": "W1 release %s units" % version, "window": plan_seed.NO_DATA}],
        "budget": {"tokens": _grant(seed, "budget"), "deadline": _grant(seed, "deadline"), "model": _grant(seed, "model")},
        "units": records, "deferred": [], "decisions_waiting": [], "risks": []}
    pack["docs/plan/specs/%s/pack/manifest.json" % version] = json.dumps(
        {"kind": "descriptive_spec_pack_not_executable_loop_wbs", "name": "Brother %s plan pack" % version, "as_of": now,
         "wbs": "docs/plan/BROTHER-%s-WBS.json" % version, "quality_threshold": 9, "night_order": [u["id"] for u in units],
         "subunits": subunits}, indent=1, ensure_ascii=False) + "\n"
    assignments = {"as_of": now, "model": _grant(seed, "model"),
                   "rule": "write ONLY the files your batch lists plus your own sub unit section of the unit spec; the cached reads are quoted so you read nothing else first; replace every WRITER: line; self score with spec_score.py --no-claim <unit> and stop at 9 or after three rounds; ASCII only, no em or en dashes, no clock text",
                   "batches": batches}
    return {"wbs": wbs, "specs": specs, "pack": pack, "assignments": assignments,
            "wbs_path": "docs/plan/BROTHER-%s-WBS.json" % version,
            "assignments_path": "docs/plan/specs/%s/WRITER-ASSIGNMENTS.json" % version}


def _files(rendered: Dict[str, object]) -> Dict[str, str]:
    """Every file the rendering writes: relative path to text."""
    out = {rendered["wbs_path"]: json.dumps(rendered["wbs"], indent=1, ensure_ascii=False) + "\n",
           rendered["assignments_path"]: json.dumps(rendered["assignments"], indent=1, ensure_ascii=False) + "\n"}
    out.update(rendered["specs"])
    out.update(rendered["pack"])
    return out


def _overlay(src: str, dst: str, chain: List[str]) -> None:
    """dst mirrors src by symlinks, except the directories named by `chain`, which are real so files can be added."""
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        if chain and name == chain[0]:
            _overlay(os.path.join(src, name), os.path.join(dst, name), chain[1:])
        else:
            os.symlink(os.path.join(src, name), os.path.join(dst, name))


def lint_before_write(rendered: Dict[str, object], root: str) -> List[str]:
    """Writes the WBS and the stub specs into a temporary overlay of root (symlinks, so NEW and existing checks read
    the real tree; docs/plan is not copied), runs plan_lint.lint_plan there and returns the BLOCKING and NO-DATA
    finding lines; the overlay is removed whatever happens."""
    pl = _plan_lint()
    files = _files(rendered)
    keep = {p: files[p] for p in [rendered["wbs_path"]] + list(rendered["specs"])}
    with tempfile.TemporaryDirectory(prefix="pg1b_lint_") as tmp:
        _overlay(root, tmp, ["docs", "plan", "specs"])
        os.makedirs(os.path.join(tmp, "docs", "plan", "specs"), exist_ok=True)
        for rel, text in keep.items():
            with open(os.path.join(tmp, rel), "w", encoding="utf-8") as f:
                f.write(text)
        report = pl.lint_plan(os.path.join(tmp, rendered["wbs_path"]), root=tmp, env={})
    if report.get("exit") == 3:
        return ["LINT NO-DATA: the rendered plan cannot be read (%s)" % report.get("reason")]
    lines = [pl._line(f) for f in report["findings"] if f["severity"] in ("BLOCKING", "NO-DATA")]
    # The lint reads only OPEN sub unit sections (plan_lint._open_sub_ids): a sub unit a carried unit already landed
    # (plan_store.sub_landed on its evidence) is skipped by design, so it is not owed a section read.
    import plan_store
    wanted = sum(1 for u in rendered["wbs"]["units"] if u["state"] != "DONE" for s in u["sub_units"]
                 if not plan_store.sub_landed(s, u.get("evidence") or ""))
    if report["read"]["units"] == 0:
        lines.append("LINT BLOCKING - -: the rendered plan has no unit")
    elif report["read"]["sections"] != wanted:
        lines.append("LINT NO-DATA - -: the lint read %d of %d sub unit sections" % (report["read"]["sections"], wanted))
    return lines


def write_plan(rendered: Dict[str, object], root: str) -> List[str]:
    """Writes every file of `rendered` under root, refusing (ValueError, nothing written) when any target exists or
    leaves root, or holds a dash or clock text; returns the written paths."""
    files = _files(rendered)
    base = os.path.realpath(root)
    bad = []
    for rel, text in files.items():
        if not os.path.realpath(os.path.join(root, rel)).startswith(base + os.sep):
            bad.append("%s leaves the root" % rel)
        elif os.path.lexists(os.path.join(root, rel)):
            bad.append("%s exists already (delete it by name to regenerate)" % rel)
        if any(d in text for d in DASHES):
            bad.append("%s holds an em or en dash" % rel)
        if CLOCK_RE.search(text):
            bad.append("%s holds clock text" % rel)
    if bad:
        raise ValueError("; ".join(bad))
    done = []
    for rel, text in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        try:
            with open(full, "x", encoding="utf-8") as f:
                f.write(text)
        except FileExistsError:  # a second generator won the race after the check above
            raise ValueError("%s appeared while writing (a second run?); written before it: %s" % (rel, ", ".join(done) or "none"))
        done.append(rel)
    return done


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="PG1.b: build the next release plan from the closing ceremony seed")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--template", default=None)
    ap.add_argument("--dry", action="store_true", help="render and lint, write nothing")
    try:
        args = ap.parse_args(argv)
        args.root = os.path.abspath(args.root)  # a relative root broke the lint overlay (2026-10-14)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    t0 = time.monotonic()
    try:
        with open(args.seed, encoding="utf-8") as f:
            seed = json.load(f)
    except OSError as exc:
        print("NO-DATA: the seed %s cannot be read (%s)" % (args.seed, type(exc).__name__))
        return 2
    except ValueError as exc:
        print("REFUSED: the seed %s is not JSON (%s)" % (args.seed, exc))
        return 1
    try:
        units = _check_header(seed, args.version)
        clash = collisions(units, taken_ids(args.root), args.root)  # the id clash is named even when a row is also unfinished
        if clash:
            print("REFUSED: unit ids already tracked under docs/plan: %s" % ", ".join(clash))
            return 1
        _check_seed(seed, args.version)
        template = load_template(args.template or find_template(args.root))
        rendered = render_plan(seed, args.version, time.strftime("%Y-%m-%d"), root=args.root, template=template)
        found = lint_before_write(rendered, args.root)
        if found:
            print("REFUSED: the plan about to be written does not lint, nothing written:")
            print("\n".join(found))
            return 1
        written = [] if args.dry else write_plan(rendered, args.root)
    except RuntimeError as exc:
        print(str(exc) if str(exc).startswith("NO-DATA") else "NO-DATA: %s" % exc)
        return 2
    except ValueError as exc:
        print("REFUSED: %s" % exc)
        return 1
    for rel in written:
        print("WROTE %s" % rel)
    subs = sum(len(u["sub_units"]) for u in rendered["wbs"]["units"])
    print("PLAN: units %d sub units %d batches %d seconds %.2f%s" % (
        len(units), subs, len(rendered["assignments"]["batches"]), time.monotonic() - t0,
        " (dry, nothing written)" if args.dry else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
