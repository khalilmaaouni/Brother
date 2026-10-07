#!/usr/bin/env python3
"""The one writer of unit records in the plan file: lock, re-read, replace only the named units, rename into place.
usage: imported by land_batch.py and close_unit.py;   python3 scripts/loop/plan_store.py --selftest

WHY (2026-09-22 review, and the night before it): the plan JSON is read by every runner, the pool, the digest, the
closer and the landing tool, and two of them REWROTE IT WHOLE from a copy read minutes earlier. Last writer wins,
with no detection: close_unit set BL to DONE and a landing two minutes later wrote it back to PARTIAL from its own
stale copy, which cost five failed closes. A whole file read-modify-write is that protocol by construction.
This module writes by UNIT: it takes an exclusive lock on <plan>.lock, re-reads the file under the lock, replaces
only the unit records the caller names, and renames a temp file into place. Two writers touching different units
both survive; two writers touching the same unit serialise, and the second one's record wins with the first one
already on disk to be re-read. FAIL DIRECTION: an unreadable plan or a unit id the file does not contain raises,
and nothing is written, because a write that cannot find its target is a write to the wrong file."""
import contextlib, copy, fcntl, json, os, re, sys, tempfile

# THE ONE LANDED TEST (finding 2, 2026-09-27). On e7e17784c, 19 call sites in 17 files under scripts/ each carried
# their own copy of r"<sub>[^.]{0,80}?(landed|LANDED|DONE)" to read the plan, and that pattern matched A.1 inside "A.10 landed" and counted "A.1 not landed: gate refused" as a landing, so an
# unlanded sub unit left READY and its unit became closable. The id must stand as a WHOLE token (no word character,
# dot, or hyphen joins it on the left; none of a word character, a hyphen, or a dot followed by one on the right),
# then no full stop within 80 characters before the verb, and no negation or refusal word in between.
_LANDED_VERB = r"\b(?:landed|LANDED|DONE)\b"
_NOT_LANDED = re.compile(r"\b(?:not|never|no|cannot|refus\w*|fail\w*|\w+n't)\b", re.I)


def sub_landed(sub, evidence):
    """True when `evidence` records `sub` as landed: any one clean occurrence counts, a negated one never does.
    Non text raises ValueError: unreadable evidence is not "not landed", it is unknown, and the caller says so.
    Out of scope, named: a negation AFTER the verb ("A.1 landed? no") and a later revert are not read."""
    if not isinstance(sub, str) or not sub or not isinstance(evidence, str):
        raise ValueError("sub_landed needs a sub unit id and evidence text, got %s and %s" % (type(sub).__name__, type(evidence).__name__))
    found = re.finditer(r"(?<![\w.-])" + re.escape(sub) + r"(?![\w-]|\.\w)([^.]{0,80}?)" + _LANDED_VERB, evidence)
    return any(not _NOT_LANDED.search(m.group(1)) for m in found)


# THE UNIT STATES THAT MEAN "NOT IN THIS RELEASE" (2026-09-29). The plan's state words are DONE, PARTIAL, SPECIFIED,
# OPEN, BLOCKED and DEFERRED (scripts/gen_launch_board.py counts exactly these) plus RETIRED (scripts/gen_release_plan.py).
# DEFERRED is the owner moving a unit to a later release; RETIRED is a unit withdrawn. Neither admits a worker however
# well its spec scores: the pool's ORDER listed DEFERRED units (RL3, RL4, L5f) under BROTHER_SCOPE=. because every
# reader tested only `state != "DONE"`. Every loop reader asks release_refusal, never its own list.
NOT_IN_RELEASE = ("DEFERRED", "RETIRED")


def release_refusal(state):
    """'' unless `state` is a NOT_IN_RELEASE word, else why the unit takes no work. DONE, and a missing or corrupt
    state, stay each caller's own case (runner_pool.admissible refuses both)."""
    if state in NOT_IN_RELEASE:
        return "unit state %s: not in this release" % state
    return ""


# THE PROOF SUPPLY (owner ruling 2026-10-04, "A: Widen to held 1.1.0 units"): of a unit widened into the proof scope,
# ONLY the sub units the owner named are loop supply; every other sub unit of that unit stays with its manual lane
# (D2.6 keeps the full REQ-FAN-2 obligation, OP1.c is owner refused). The pool builds a unit's FIRST unlanded sub unit,
# so without this list it would have built D2.6 before D2.7. ONE definition, read by runner_pool (the hold and
# admissible), loop_done (ready builds and eligible work), pass_digest (what the lander may be offered) and
# proof_accept (what a proof's landings may contain). A unit not listed here is untouched. MG1.e and MG1.f joined the
# same evening (decision proof-supply-mg1.json): MG1.a to MG1.d are landed, and no 1.1.1 sub unit was loop buildable.
# D2.7 LEFT THE SUPPLY 2026-10-05 (owner ruling, option A of proofs-unlandable.json): its spec mutates
# plugin/runtime/brother/core/openrouter_dispatch.py, a path the landing trusts and a build never writes, so every
# build was ADAPTER REFUSED (unit-runs/D2.7-141303). D2.7 is a REVIEWED HAND-ROUTE item from then on: a person writes
# and reviews it outside the loop, recorded here, the one definition. D2 KEEPS ITS KEY WITH AN EMPTY SUPPLY: removing the
# key would make D2 an untouched unit and hand D2.6 (manual lane) and D2.7 back to the pool; an empty tuple holds every D2
# sub unit, so D2 stays in the launch scope as one more unfinished unit with nothing eligible, like D2.6 before.
# MG1.f LEFT THE SUPPLY 2026-10-06: the loop landed it on 2026-10-05 (2e9082e56), the cut condition review of
# 2026-10-06 refused it (docs/plan/PROOF-ACCEPTANCE.md), it was removed from the tree and it moves to 1.1.1 with that
# review's findings. Left in this list, a pool reading a plan where MG1.f is no longer landed would build the refused
# sub unit again from the same specification. MG1.e stays: it landed, and its review let it stand with known limits.
SUPPLY = {"D2": (), "FX-31": ("FX-31.8",), "PR1": ("PR1.c", "PR1.d", "PR1.e", "PR1.f"), "OP1": ("OP1.d",), "MG1": ("MG1.e",)}


def supply_unit(sub):
    """The SUPPLY unit `sub` belongs to by id (D2.7 is D2's, FX-31.8 is FX-31's; D20.1 is nobody's), else None."""
    if not isinstance(sub, str) or not sub:
        raise ValueError("supply_unit needs a sub unit id, got %r" % (sub,))
    return next((u for u in SUPPLY if sub.startswith(u + ".")), None)


def supply_hold(sub):
    """'' unless `sub` belongs to a SUPPLY unit and is not one of its named sub units, else why it takes no worker."""
    unit = supply_unit(sub)
    if unit is None or sub in SUPPLY[unit]:
        return ""
    return "held: %s is not proof supply; the owner named only %s of %s for the loop (2026-10-04)" % (sub, ", ".join(SUPPLY[unit]) or "none", unit)


def load(path):
    with open(path, encoding="utf-8") as f: return json.load(f)


def _evidence_kept(k, old, new):
    """Evidence is append only. A write whose evidence does not extend what is on disk was built from a stale read
    and would erase a concurrent writer's committed receipt (finding 1, 2026-09-27): refuse it, nothing written."""
    old = "" if old is None else old
    new = "" if new is None else new
    if not isinstance(old, str) or not isinstance(new, str):
        raise ValueError("unit %s: evidence is not text on disk or in the record; nothing written" % k)
    if not new.startswith(old.rstrip()):
        raise ValueError("unit %s: this evidence was built from a stale read and would erase evidence committed since; "
                         "pass a callable record so the change is applied to the unit re-read under the lock" % k)


def update_units(path, records, lock_path=None, fields=None):
    """records: {unit_id: unit dict, or a callable}. A CALLABLE receives a copy of the unit RE-READ UNDER THE LOCK and
    returns the new record: the only safe way to derive a value from the current one, such as appending evidence
    after a check that ran for minutes (finding 1, 2026-09-27). With `fields` (an iterable of keys) only those keys
    are merged into the record on disk, re-read under the lock, so a landing writing `evidence` and a closer writing
    `state` from a stale copy of the SAME unit both survive (executed attack 2026-09-22: a stale close erased a
    landing's evidence). Without `fields` the whole record is replaced. Evidence may only grow: a record whose
    evidence does not extend the evidence on disk raises ValueError. Returns the ids written. Raises KeyError for an
    id the plan lacks."""
    if not isinstance(records, dict) or not records: raise ValueError("records must be a non empty {id: unit} dict")
    with _locked(path, lock_path):
        plan = load(path)                      # re-read UNDER the lock: never the caller's stale copy
        by_id = {u.get("id"): i for i, u in enumerate(plan.get("units", []))}
        missing = [k for k in records if k not in by_id]
        if missing: raise KeyError("plan has no unit(s) %s; nothing written" % missing)
        for k, rec in records.items():
            if callable(rec): rec = rec(copy.deepcopy(plan["units"][by_id[k]]))
            if not isinstance(rec, dict) or rec.get("id") != k: raise ValueError("record for %s must be a unit dict with that id" % k)
            old = plan["units"][by_id[k]]
            new = dict(old, **{f: rec[f] for f in fields if f in rec}) if fields else rec
            if new.get("evidence") != old.get("evidence"): _evidence_kept(k, old.get("evidence"), new.get("evidence"))
            plan["units"][by_id[k]] = new
        _replace(path, _body(plan))
    return sorted(records)


def undo_appended(path, appended, lock_path=None, committed=None):
    """Take back exactly what ONE writer appended to unit evidence, and nothing else, under the plan lock. appended is
    {unit id: (evidence that writer re-read, evidence it wrote)}, as land_batch.landing_records records it. A unit whose
    evidence still starts with what was written gets back what was re-read, followed by whatever other writers appended
    since; no other unit and no other field is touched. A unit whose evidence no longer starts with that write, or that
    the plan no longer holds, is left exactly as it is and returned. Returns the ids NOT restored; nothing is written
    when none is. committed, the plan's committed bytes: when the undone plan holds exactly their data, those bytes are
    written back rather than this module's serialisation, so a refusal with no other writer leaves the plan as
    committed, byte for byte, and the tree clean. WHY (X1 finding 3, 2026-09-27): a refused landing restored the whole
    plan with an unlocked git checkout, erasing evidence another writer had appended under this lock meanwhile.
    A third item, when present, lists the owns entries that writer added (landing_records, queue item 5 2026-09-30):
    each one still in the unit's owns is removed in this same pass, so the byte for byte restore above still holds; an
    owns entry another writer added is kept. A record whose evidence part is unchanged (after == before) added owns only,
    and only its owns come back."""
    if not isinstance(appended, dict): raise ValueError("appended must be a {id: (before, after[, owns added])} dict")
    missed, changed = [], False
    with _locked(path, lock_path):
        plan = load(path)
        by_id = {u.get("id"): u for u in plan.get("units", []) if isinstance(u, dict)}
        for k, rec in sorted(appended.items()):
            before, after, *more = rec
            added = more[0] if more else ()
            unit = by_id.get(k)
            if not (added and after == before and unit is not None):
                cur = unit.get("evidence") if unit is not None else None
                if not isinstance(after, str) or not isinstance(cur, str) or not cur.startswith(after):
                    missed.append(k); continue
                rest = cur[len(after):]
                unit["evidence"] = (before or "") + rest if rest else before
                changed = True
            owns = unit.get("owns")
            for p in added:
                if isinstance(owns, list) and p in owns:
                    owns.remove(p); changed = True
        if changed: _replace(path, committed if _holds(committed, plan) else _body(plan))
    return missed


def _holds(raw, plan):
    """True when the bytes `raw` parse to exactly `plan`; anything unreadable is False."""
    try:
        return isinstance(raw, bytes) and json.loads(raw.decode("utf-8")) == plan
    except ValueError:   # sbe: allow-silent unreadable committed bytes are not the plan, the caller writes its own serialisation
        return False


@contextlib.contextmanager
def _locked(path, lock_path=None):
    """The one exclusive lock every plan writer takes. THE LOCK LIVES OUTSIDE THE TREE. A lock beside the plan is an
    untracked file, and the pass's clean tree rule counts it as dirty and refuses to land (measured at first use,
    2026-09-22). Keyed on the plan's absolute path."""
    import hashlib
    lock_path = lock_path or os.path.join(tempfile.gettempdir(), "brother-plan-%s.lock" % hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:16])
    with open(lock_path, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        yield


def _body(plan):
    """The plan as this module writes it. Called BEFORE any file exists, so an unserialisable plan raises with nothing
    written (audit 2026-09-22)."""
    return (json.dumps(plan, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def _replace(path, body):
    """Rename a temp file holding `body` (bytes) into place: a failure leaves the plan as it was and no temp file behind."""
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", dir=os.path.dirname(os.path.abspath(path)) or ".")
    try:
        with os.fdopen(fd, "wb") as f: f.write(body)
        os.replace(tmp, path)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def selftest():
    try: return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1


def _selftest_body():
    import subprocess, textwrap
    d = tempfile.mkdtemp(prefix="plan-store-"); p = os.path.join(d, "plan.json")
    with open(p, "w") as f: json.dump({"units": [{"id": "A", "state": "OPEN", "evidence": ""}, {"id": "B", "state": "OPEN", "evidence": ""}]}, f)
    a = load(p)["units"][0]; a["state"] = "DONE"
    update_units(p, {"A": a})
    # the race that lost BL: a second writer holding a STALE copy of the whole plan updates only B
    stale_b = {"id": "B", "state": "PARTIAL", "evidence": "B.1 landed"}
    update_units(p, {"B": stale_b})
    after = {u["id"]: u for u in load(p)["units"]}
    try: update_units(p, {"Z": {"id": "Z"}}); missing_raises = False
    except KeyError: missing_raises = True
    try: update_units(p, {"A": {"id": "B"}}); mismatch_raises = False
    except ValueError: mismatch_raises = True
    # two processes, different units, at once
    code = textwrap.dedent("""
        import sys, json; sys.path.insert(0, %r); import plan_store as S
        for i in range(40):
            u = [x for x in S.load(%r)["units"] if x["id"] == sys.argv[1]][0]; u["evidence"] += sys.argv[1] + str(i) + " "
            S.update_units(%r, {sys.argv[1]: u})
    """) % (os.path.dirname(os.path.abspath(__file__)), p, p)
    procs = [subprocess.Popen([sys.executable, "-c", code, uid]) for uid in ("A", "B")]
    rcs = [q.wait() for q in procs]
    final = {u["id"]: u for u in load(p)["units"]}
    q = os.path.join(d, "same.json")
    with open(q, "w") as f: json.dump({"units": [{"id": "A", "state": "OPEN", "evidence": ""}]}, f)
    closer_copy = load(q)["units"][0]; lander_copy = load(q)["units"][0]
    lander_copy["evidence"] = "A.1 landed"; update_units(q, {"A": lander_copy}, fields=("evidence",))
    closer_copy["state"] = "DONE"; update_units(q, {"A": closer_copy}, fields=("state",))
    same = load(q)["units"][0]
    before = sorted(os.listdir(d))
    try: update_units(q, {"A": {"id": "A", "evidence": "A.1 landed", "bad": object()}}); unserialisable_raises = False
    except TypeError: unserialisable_raises = True
    # a failure AFTER the temp file exists (the rename itself) must remove it: patch os.replace for one call
    real_replace = os.replace
    def failing_replace(a, b): raise OSError("rename refused")
    os.replace = failing_replace
    try: update_units(q, {"A": {"id": "A", "state": "OPEN", "evidence": "A.1 landed"}}); rename_raised = False
    except OSError: rename_raised = True
    finally: os.replace = real_replace
    # FINDING 1, 2026-09-27: a closer read the unit, ran a long check, then wrote evidence built from that stale copy,
    # erasing evidence another writer committed meanwhile. Two guards, one fixture each.
    r = os.path.join(tempfile.mkdtemp(prefix="plan-store-race-"), "race.json")   # its own folder: the cases above compare d's listing
    with open(r, "w") as f: json.dump({"units": [{"id": "A", "state": "PARTIAL", "evidence": "A.1 landed initial."}]}, f)
    stale = load(r)["units"][0]
    fresh = load(r)["units"][0]; fresh["evidence"] += " A.1 landed concurrent receipt."; update_units(r, {"A": fresh}, fields=("evidence",))
    on_disk = open(r, "rb").read()
    stale["evidence"] += " UNIT DONE."; stale["state"] = "DONE"
    try: update_units(r, {"A": stale}, fields=("evidence", "state")); erasure_raises = False
    except ValueError: erasure_raises = True
    erasure_wrote_nothing = open(r, "rb").read() == on_disk
    update_units(r, {"A": lambda unit: dict(unit, state="DONE", evidence=unit["evidence"] + " UNIT DONE.")})
    rmw = load(r)["units"][0]
    landed = [("A.1", "A.1 landed today."), ("A.1", "x, A.1 DONE"), ("L5a-1", "L5a-1 landed")]
    unlanded = [("A.1", "A.10 landed today."), ("A.1", "XA.1 landed"), ("L5a", "L5a-1 landed"), ("A.1", "A.1.2 landed"),
                ("A.1", "A.1 not landed: gate refused."), ("A.1", "A.1 refused, never landed"), ("A.1", "A.1 unlanded"),
                ("A.1", "A.1 spec. landed"), ("A.1", "A.1 failed to be landed")]
    try: sub_landed("A.1", None); non_text_raises = False
    except ValueError: non_text_raises = True
    try: sub_landed(5, "5 landed"); non_text_id_raises = False
    except ValueError: non_text_id_raises = True
    cases = [("a sub unit of a widened unit the owner did not name is held", supply_hold("D2.6").startswith("held:") and supply_hold("D2.7").startswith("held:") and supply_hold("OP1.c").startswith("held:") and supply_hold("MG1.a").startswith("held:")),
             ("a sub unit that left the supply is held again (MG1.f, refused 2026-10-06)", supply_hold("MG1.f").startswith("held:")),
             ("a named supply sub unit is admitted", supply_hold("PR1.e") == "" and supply_hold("OP1.d") == "" and supply_hold("MG1.e") == ""),
             ("a unit outside the supply list is untouched, prefix siblings included", supply_hold("L5a-10") == "" and supply_hold("D20.1") == "" and supply_unit("D20.1") is None and supply_unit("MG10.e") is None),
             ("a unit update survives a later stale writer of another unit",after["A"]["state"] == "DONE" and after["B"]["state"] == "PARTIAL"),
             ("an unserialisable record raises and leaves no temp file behind", unserialisable_raises and sorted(os.listdir(d)) == before),
             ("a failed rename raises and leaves no temp file behind", rename_raised and sorted(os.listdir(d)) == before),
             ("two stale writers of the SAME unit keep each other's fields", same["state"] == "DONE" and same["evidence"] == "A.1 landed"),
             ("a unit the plan lacks raises and writes nothing", missing_raises and load(p)["units"][0]["state"] == "DONE"),
             ("a record whose id disagrees raises", mismatch_raises),
             ("two concurrent writers of different units both land every write", rcs == [0, 0] and len(re.findall(r"A\d+ ", final["A"]["evidence"])) == 40 and len(re.findall(r"B\d+ ", final["B"]["evidence"])) == 40),
             ("the file is valid JSON after the race", isinstance(load(p), dict)),
             ("a stale evidence value that would erase committed evidence raises and writes nothing", erasure_raises and erasure_wrote_nothing),
             ("a callable record is applied to the unit re-read under the lock, so concurrent evidence survives",
              rmw["state"] == "DONE" and rmw["evidence"] == "A.1 landed initial. A.1 landed concurrent receipt. UNIT DONE."),
             ("sub_landed reads a whole id followed by landed, LANDED or DONE", all(sub_landed(s, e) for s, e in landed)),
             ("sub_landed never reads a longer id, a negation, a refusal or a full stop as landed", not any(sub_landed(s, e) for s, e in unlanded)),
             ("sub_landed refuses evidence that is not text", non_text_raises),
             ("sub_landed refuses a sub unit id that is not text", non_text_id_raises)]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad))); return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else 2)
