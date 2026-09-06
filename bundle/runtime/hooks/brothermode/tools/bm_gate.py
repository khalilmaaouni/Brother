#!/usr/bin/env python3
"""bm_gate: the admission gate for heavy work and for shared delivery channels.

WHY THIS EXISTS, in the words of the night that produced it (2026-08-27, one estate, three
lanes). Two failures happened in the same hours and they look unrelated until you try to fix
either one properly.

  COLLISION. Two lanes edited the same files from different worktrees, then each uploaded to
  TestFlight. The second upload buried the first, and the founder judged the night on a build
  that held half of it. The fence store could have refused the file overlap, and now does
  (GATE 8), but nothing at all governed the DELIVERY CHANNEL, because a channel is not a file.

  OVERLOAD. Three lanes ran builds, simulators and test suites at once. Free disk reached
  311 MB, swap reached 14 GB, load average passed 300. Suites died in ways that looked like
  code defects and were not. Nothing anywhere asked, before starting, whether the machine
  could carry one more.

Both are the same missing primitive: ADMISSION. Ask before you start, be told GO or WAIT, and
be given a number of seconds rather than a wall. That is all this tool does.

WHAT IT DELIBERATELY DOES NOT DO. It does not kill processes, suspend them, or manage cgroups:
pausing is COOPERATIVE, because a lane knows its own safe points and this tool does not. It
does not predict the future beyond the machine's own moving averages. It does not replace the
fence store, which remains the one owner of who owns what; this reads that store rather than
keeping a second opinion.

THE MARGIN IS ON PURPOSE. Every threshold sits well below the point where the machine actually
fell over, and the release threshold is lower than the admit threshold (hysteresis), so a lane
is never admitted, refused, admitted, refused as the average wobbles by a hair.

  ask       one lane asks to start one unit of heavy work; prints GO <token> or WAIT <seconds>
  check     a lane already running asks whether to keep going or yield at its next safe point
  done      release the lease
  status    what is live, what the machine reads right now
  forecast  would this set of paths collide with in flight work, WITHOUT claiming anything

Exit codes: 0 GO or clean, 75 WAIT (EX_TEMPFAIL, the conventional "try again later"), 2 bad
input. Python 3.9, standard library only, no subprocess, no network.
"""
import json
import os
import shutil
import sys
import time
import uuid

# ---------------------------------------------------------------- classes and capacities
# Capacity is per MACHINE, not per lane, because the machine is what ran out. The numbers come
# from what this Mac actually survived, with a margin: two concurrent Xcode builds were fine,
# three plus a suite were not.
CLASS_CAPACITY = {
    "build": 2,     # xcodebuild build
    "suite": 1,     # a full test run: it owns a simulator and most of the CPU
    "sim": 2,       # a booted simulator doing screenshot or UI work
    "upload": 1,    # TestFlight and any other shared delivery channel
    "gui": 1,       # one driver of a visible app at a time
}
HEAVY = ("build", "suite", "sim")

# ---------------------------------------------------------------- machine thresholds
# load per core. Tonight's failure sat near 30. Admit below 3.0, and once refused do not admit
# again until it falls under 2.0, so the gate cannot flap.
def _env_float(name, default):
    """Every threshold is tunable from the environment. Two reasons, both real: this machine is
    not every machine, and a test must be able to pin the numbers or it is really testing
    whatever else happened to be running at the time."""
    try:
        return float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return default


LOAD_ADMIT = _env_float("BM_GATE_LOAD_ADMIT", 3.0)
LOAD_RESUME = _env_float("BM_GATE_LOAD_RESUME", 2.0)
# free disk. The failure sat at 0.3 GB. Heavy work needs room for DerivedData and a simulator.
DISK_ADMIT_HEAVY_GB = _env_float("BM_GATE_DISK_HEAVY_GB", 8.0)
DISK_ADMIT_ANY_GB = _env_float("BM_GATE_DISK_ANY_GB", 3.0)
# a lease nobody released is not allowed to hold the machine for ever: this is the watchdog
# half, and it is passive by design (no process is signalled, the record simply stops counting).
LEASE_TTL_SEC = 45 * 60
RETRY_MIN_SEC, RETRY_MAX_SEC = 30, 300


def _state_path(root):
    return os.path.join(root, ".brothermode", "gate.json")


def resolve_root(start=None):
    """Walk up for a .brothermode directory, then a .git, then fall back to cwd. Mirrors the
    store's own order so both tools agree on which estate they are in."""
    cur = os.path.abspath(start or os.getcwd())
    while True:
        for marker in (".brothermode", ".git"):
            if os.path.exists(os.path.join(cur, marker)):
                return cur
        nxt = os.path.dirname(cur)
        if nxt == cur:
            return os.path.abspath(start or os.getcwd())
        cur = nxt


def read_machine():
    """One reading of the machine, stdlib only. Swap is deliberately NOT read: it needs a
    subprocess on macOS, and load average already moves when the machine starts thrashing."""
    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        load1 = load5 = load15 = 0.0
    cpus = os.cpu_count() or 1
    try:
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024.0 ** 3)
    except OSError:
        free_gb = float("inf")
    return {"load1": load1, "load5": load5, "load15": load15, "cpus": cpus,
            "load_per_cpu": load1 / cpus, "free_disk_gb": free_gb}


def _load(root):
    path = _state_path(root)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (IOError, OSError, ValueError):
        data = {"leases": []}
    now = time.time()
    fresh, expired = [], []
    for l in data.get("leases", []):
        (expired if now - l.get("at", 0) > LEASE_TTL_SEC else fresh).append(l)
    data["leases"] = fresh
    return data, expired


def _save(root, data):
    path = _state_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)          # atomic: a torn read can never be observed


def _store_claims(root):
    """Live file ownership, read from the fence store rather than duplicated here. Returns
    {path: record_name}. Absent or unreadable store is NO-DATA, never an implied 'clear': the
    caller is told, because a collision check that silently saw nothing is worse than none."""
    db = os.path.join(root, ".brothermode", "store.sqlite3")
    if not os.path.exists(db):
        return None
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2.0)
        try:
            rows = con.execute(
                "SELECT c.path, r.name FROM claims c JOIN records r "
                "ON r.lifecycle_uuid = c.lifecycle_uuid WHERE r.state='active'").fetchall()
        finally:
            con.close()
        return {p: n for p, n in rows}
    except Exception:  # sbe: allow-silent documented above: absent/unreadable store returns None as an explicit NO-DATA the caller must handle, never an implied clear
        return None


def _overlap(paths, owned):
    """A proposed path collides with an owned one when either contains the other as a directory
    prefix, or they are equal. Same shape the fence store uses, kept simple on purpose."""
    hits = []
    for p in paths:
        p = p.strip().rstrip("/")
        if not p:
            continue
        for q, name in (owned or {}).items():
            q = q.rstrip("/")
            if p == q or p.startswith(q + "/") or q.startswith(p + "/"):
                hits.append((p, q, name))
    return hits


def _retry_after(m, live_count, capacity):
    """A number, not a spin. Heavier pressure means a longer wait, bounded at both ends."""
    over_load = max(0.0, m["load_per_cpu"] - LOAD_RESUME)
    over_queue = max(0, live_count - capacity + 1)
    secs = RETRY_MIN_SEC + 45 * over_load + 30 * over_queue
    return int(max(RETRY_MIN_SEC, min(RETRY_MAX_SEC, secs)))


def _pressure_reason(m, klass, waiting_already):
    """None means the machine is willing. Hysteresis: a lane that is ALREADY waiting must see
    the calmer RESUME threshold before it is let in, so the gate cannot flap."""
    limit = LOAD_RESUME if waiting_already else LOAD_ADMIT
    if m["load_per_cpu"] > limit:
        return ("load average %.1f over %d cores is %.1f per core, above the %.1f mark"
                % (m["load1"], m["cpus"], m["load_per_cpu"], limit))
    if m["free_disk_gb"] < DISK_ADMIT_ANY_GB:
        return "only %.1f GB free on disk, below the %.1f GB floor" % (
            m["free_disk_gb"], DISK_ADMIT_ANY_GB)
    if klass in HEAVY and m["free_disk_gb"] < DISK_ADMIT_HEAVY_GB:
        return "only %.1f GB free on disk, and %s work needs %.1f GB of room" % (
            m["free_disk_gb"], klass, DISK_ADMIT_HEAVY_GB)
    return None


def cmd_ask(root, args):
    klass = args.get("class") or args.get("_pos")
    lane = args.get("lane", "(unnamed lane)")
    paths = args.get("paths", [])
    if klass not in CLASS_CAPACITY:
        sys.stderr.write("bm_gate: unknown class %r; known: %s\n"
                         % (klass, ", ".join(sorted(CLASS_CAPACITY))))
        return 2
    data, expired = _load(root)
    for e in expired:
        sys.stderr.write("bm_gate: lease %s (%s, %s) expired after %d minutes and no longer "
                         "counts; the lane never released it\n"
                         % (e.get("token", "?"), e.get("class"), e.get("lane"),
                            LEASE_TTL_SEC // 60))
    live = [l for l in data["leases"] if l["class"] == klass]
    capacity = CLASS_CAPACITY[klass]
    m = read_machine()

    if paths:
        owned = _store_claims(root)
        if owned is None:
            sys.stderr.write("bm_gate: NO-DATA on file ownership (no readable fence store); "
                             "path collision was NOT checked\n")
        else:
            hits = _overlap(paths, owned)
            if hits:
                for p, q, name in hits[:6]:
                    print("COLLIDE %s is owned by active fence %r (as %s)" % (p, name, q))
                print("WAIT %d another lane holds these paths; do not start, re-plan the task "
                      "or wait for that fence to close" % RETRY_MAX_SEC)
                return 75

    waiting_already = bool(args.get("waiting"))
    reason = _pressure_reason(m, klass, waiting_already)
    if reason:
        print("WAIT %d machine pressure: %s" % (_retry_after(m, len(live), capacity), reason))
        return 75
    if len(live) >= capacity:
        holders = ", ".join("%s(%s)" % (l["lane"], l["token"][:6]) for l in live)
        print("WAIT %d %s is at capacity %d of %d, held by %s"
              % (_retry_after(m, len(live), capacity), klass, len(live), capacity, holders))
        return 75

    token = uuid.uuid4().hex[:12]
    data["leases"].append({"token": token, "class": klass, "lane": lane,
                           "paths": list(paths), "at": time.time()})
    _save(root, data)
    print("GO %s %s for %s (load %.1f per core, %.1f GB free)"
          % (token, klass, lane, m["load_per_cpu"], m["free_disk_gb"]))
    return 0


def cmd_check(root, args):
    """A lane already running asks, at ITS OWN safe point, whether to keep going. This is the
    whole of 'pause': no signal is sent, because only the lane knows where it can yield."""
    token = args.get("token") or args.get("_pos")
    data, _ = _load(root)
    mine = [l for l in data["leases"] if l["token"] == token]
    m = read_machine()
    if not mine:
        print("PAUSE lease %s is not live (expired or released); re-ask before continuing"
              % token)
        return 75
    if m["load_per_cpu"] > LOAD_ADMIT or m["free_disk_gb"] < DISK_ADMIT_ANY_GB:
        print("PAUSE machine pressure: load %.1f per core, %.1f GB free; yield at your next "
              "safe point and re-ask" % (m["load_per_cpu"], m["free_disk_gb"]))
        return 75
    print("CONTINUE %s (load %.1f per core, %.1f GB free)"
          % (token, m["load_per_cpu"], m["free_disk_gb"]))
    return 0


def cmd_done(root, args):
    token = args.get("token") or args.get("_pos")
    data, _ = _load(root)
    before = len(data["leases"])
    data["leases"] = [l for l in data["leases"] if l["token"] != token]
    _save(root, data)
    if before == len(data["leases"]):
        print("no live lease %s (already released or expired)" % token)
        return 0
    print("released %s" % token)
    return 0


def cmd_status(root, args):
    data, expired = _load(root)
    m = read_machine()
    print("machine: load %.2f / %.2f / %.2f over %d cores (%.2f per core), %.1f GB free"
          % (m["load1"], m["load5"], m["load15"], m["cpus"], m["load_per_cpu"],
             m["free_disk_gb"]))
    verdict = _pressure_reason(m, "suite", False)
    print("admission: %s" % ("WILLING" if verdict is None else "REFUSING, " + verdict))
    if not data["leases"]:
        print("leases: none live")
    for l in data["leases"]:
        print("  %s %-6s %-28s held %d min%s"
              % (l["token"][:8], l["class"], l["lane"], int((time.time() - l["at"]) / 60),
                 (", paths: " + ", ".join(l["paths"][:3])) if l.get("paths") else ""))
    for e in expired:
        print("  EXPIRED %s %s %s (never released)"
              % (e.get("token", "?")[:8], e.get("class"), e.get("lane")))
    return 0


def cmd_forecast(root, args):
    """The planning question, asked BEFORE a task is written into a WBS: would this work meet
    anyone. Claims nothing, changes nothing."""
    paths = args.get("paths", [])
    if not paths:
        sys.stderr.write("bm_gate: forecast needs --paths\n")
        return 2
    owned = _store_claims(root)
    data, _ = _load(root)
    if owned is None:
        print("NO-DATA no readable fence store; collision could not be judged")
        return 2
    hits = _overlap(paths, owned)
    lease_hits = []
    for l in data["leases"]:
        lease_hits += [(p, q, "lease " + l["lane"]) for p, q, _ in _overlap(paths, {
            q: l["lane"] for q in l.get("paths", [])})]
    if not hits and not lease_hits:
        print("CLEAR %d path(s) meet no active fence and no live lease" % len(paths))
        return 0
    for p, q, name in hits + lease_hits:
        print("COLLIDE %s meets %r (owns %s)" % (p, name, q))
    print("Plan this task AFTER those close, or narrow its paths so the two lanes do not meet.")
    return 75


def _parse(argv):
    args, key = {"_pos": None, "paths": []}, None
    for a in argv:
        if a.startswith("--"):
            key = a[2:]
            if key not in args:
                args[key] = True
        elif key == "paths":
            args["paths"].append(a)
        elif key:
            args[key] = a
            key = None
        elif args["_pos"] is None:
            args["_pos"] = a
    return args


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    args = _parse(rest)
    root = resolve_root(args.get("root"))
    fns = {"ask": cmd_ask, "check": cmd_check, "done": cmd_done,
           "status": cmd_status, "forecast": cmd_forecast}
    if cmd not in fns:
        sys.stderr.write("bm_gate: unknown command %r; known: %s\n"
                         % (cmd, ", ".join(sorted(fns))))
        return 2
    return fns[cmd](root, args)


if __name__ == "__main__":
    sys.exit(main())
