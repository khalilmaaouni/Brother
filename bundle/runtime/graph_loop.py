#!/usr/bin/env python3
"""The graph loop: which nodes may run RIGHT NOW, and which may run TOGETHER.

FOUNDER DIRECTION 2026-08-29: make graph loops with multi-agent capability a core
principle, codified rather than described, borrowed from the best available and
adapted to this estate's reality.

WHAT ALREADY EXISTED, and what was actually being used. The ready-set standard
(docs/plan/READY-SET-STANDARD-2026-08-28.md) defines the graph, the ready set, a
pull rule and five folded-in practices from Temporal, Airflow, Dagster and SQS.
Of those, this estate has been using TWO: the dependency graph and a ready set.
Measured honestly on 2026-08-29, it was NOT using downstream-weight pull order
(nodes were picked by intuition), NOT the lane cap (four agents ran against a
stated cap of two), NOT event nodes, NOT BLOCKED-BY naming, and NOT quarantine.
BLOCKED-BY naming and the lane cap landed with the first version of this file;
event nodes landed 2026-08-29 when the vault graph produced three real ones.

AND THE GAP THAT MATTERED MOST WAS NOT IN THE STANDARD AT ALL. The graph knows
what must happen BEFORE what. It says nothing about what may happen BESIDE what.
So on 2026-08-29 two independent efforts fixed the same defect within an hour,
one agent wrote a file in nobody's declared scope, three sessions spent an hour
establishing who owned 83 lines, and about 500 lines were deleted. Every one of
those is a CONCURRENCY failure, and a dependency edge cannot express it.

THE BORROW, and it is from further afield than the standard's four sources.

A dependency edge is a HAPPENS-BEFORE constraint. What was missing is a
HAPPENS-BESIDE constraint, and databases solved that decades ago: two
transactions may interleave freely only when their write sets are disjoint, and
must serialize when they overlap. That is conflict serializability, and an agent
holding `ownedPaths` is a transaction holding a write set.

THE ADAPTATION, since a copy is not a steal. A database aborts and retries a
conflicting transaction, which is cheap because a transaction is cheap. An agent
is not: a wasted agent run costs minutes and real money, and an aborted one can
leave a half-written tree. So this NEVER aborts. It refuses admission BEFORE
dispatch, which turns an expensive rollback into a free scheduling decision. The
whole point is that the conflict is discovered while it is still hypothetical.

Also adapted: Dagster's downstream-weight prioritisation, so among nodes that MAY
run, the one unblocking the most work goes first; and Temporal's worker
concurrency limit, except the cap here is DERIVED from the machine rather than
configured, because the founder's named failure was streams dying when CPU, RAM
or disk ran short.

WHAT THIS DOES NOT DO. It reasons about DECLARED paths. Two nodes that collide
through an undeclared write are invisible to it, which is precisely why W1's
write-time attribution ledger is its companion and not an alternative. It also
cannot tell that two differently-worded nodes are the same work; that is reported
as a warning for a human or Fable to read, never as an automatic refusal, and it
says NO-DATA rather than "clear" when it cannot tell.

Exit 0 a plan was produced. Exit 2 NO-DATA, the roadmap could not be read.
Python 3.9 floor, standard library only.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from typing import Mapping, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import resource_gate  # noqa: E402  (sibling module, scripts/resource_gate.py)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROADMAP = os.path.join(ROOT, 'docs', 'plan', 'READINESS-ROADMAP-2026-08-29.json')

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from plugin.runtime.brother.core.dream_policy import rank_ready_units, SchedulingError
except ImportError:
    rank_ready_units = None
    SchedulingError = None

# Resource floors. The disk numbers are this estate's own standing law: under 15
# GiB clean up before builds, under 8 refuse. Measured 2026-08-29 at 8.9 GiB,
# so the cleanup band is where this machine actually lives.
DISK_REFUSE_GIB = 8
DISK_CLEANUP_GIB = 15
CORES_RESERVED = 2          # never take the last two cores

#: This estate's OWN standing cap, which is stricter than anything the hardware
#: implies: 3 agents that build, 6 read-only. Derived capacity may exceed it and
#: must never override it. Added after a run on a freshly cleared disk proposed
#: six concurrent builders, which the hardware allows and the estate's law does
#: not. A resource check that quietly outvotes a written rule is worse than no
#: resource check, because it looks principled while removing a control.
ESTATE_BUILDER_CAP = 3

#: A node whose owner is the founder is never pulled by a session. The ready-set
#: standard has said so since 2026-08-28 and this scheduler did not know it: on a
#: cleared disk it proposed dispatching R15, which is AWAITING FOUNDER and is a
#: supply-chain decision only he can take.
FOUNDER_OWNERS = ('FOUNDER', 'founder')

#: A node with NO declared write set is not safe, it is UNKNOWN, and those are
#: different states that this scheduler collapsed in its first two drafts. A
#: read-only node (owns == []) genuinely conflicts with nobody. A node that never
#: declared its paths (owns is None) conflicts with EVERYTHING, because nothing
#: proves otherwise. Treating the second as the first is exactly the undeclared
#: write that destroyed about 500 lines in this estate on 2026-08-29: the tool
#: could not attribute a change, so it guessed, and the guess was destructive.
#: Found when a cleared disk raised capacity and the scheduler batched two nodes
#: whose write sets nobody had declared.

#: ORCH-02 (routing metadata survives the whole spine, 2026-09-18): the
#: orchestrator-task-v1.json fields nodes() used to rebuild from a fixed
#: twelve-key dictionary that held none of them. Kept as its own literal
#: here rather than imported from work_record.py, because this module
#: builds nodes from roadmap rows (docs/plan/READINESS-ROADMAP-*.json) as
#: often as from a Work document, and a roadmap row is not required to
#: carry any of them; importing a work_record-owned list would wire a
#: dependency this module does not otherwise have for a list this short.
ROUTING_METADATA_FIELDS = (
    "task_class", "worker_profile", "review_profile", "risk_class",
    "evidence_obligation", "max_outer_attempts", "max_repair_attempts",
    "leaf_worker_only",
)


#: D1.2: schema name for ready-set fingerprints. A fingerprint is always
#: 64 lowercase hex sha256 over canonical JSON. Missing, corrupt or hostile
#: keys are treated as the restrictive empty list or 0, never guessed.
PLAN_FINGERPRINT_SCHEMA = "plan-v1"


def _plan_doc_ok(doc):
    """True only for a mapping whose rows/features are sequences of mappings.

    This is the one validation every scheduler entry point routes through.
    A wrong type, a generator where a list belongs, bytes that are not a
    document, or a mapping whose own get raises is refused here rather than
    crashing later in nodes().
    """
    if not isinstance(doc, dict):
        return False
    try:
        for key in ("rows", "features"):
            value = doc.get(key)
            if value is None:
                continue
            if not isinstance(value, (list, tuple)):
                return False
            for item in value:
                if not isinstance(item, dict):
                    return False
    except Exception:  # noqa: BLE001  (deny a hostile mapping, never crash)
        return False
    return True


def _plan_no_data(reason):
    """The module's own refusal value for plan()."""
    return {
        "batch": [],
        "deferred": [],
        "blocked": [],
        "in_flight": [],
        "weight": {},
        "capacity": 0,
        "notes": [reason],
        "unknown_deps": [],
    }


def _plan_fingerprint_empty_payload():
    """The restrictive shape: every unreadable part of a plan is this."""
    return {
        "schema": PLAN_FINGERPRINT_SCHEMA,
        "batch": [],
        "deferred": [],
        "blocked": [],
        "unknown_deps": [],
        "capacity": 0,
    }


def _plan_fingerprint_digest(payload):
    """Canonical JSON of one payload, as 64 lowercase hex characters."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _plan_fingerprint_get(mapping, key):
    """Read ONE key without trusting the mapping.

    A hostile mapping can raise anything out of get: an unhashable lookup
    key, a stored key whose __eq__ raises, a get attribute that is not
    callable, or a dict subclass that overrides get itself. Every one of
    those is denied here by returning the missing-key value, so the raw
    interpreter exception never reaches the caller and a value produced by
    a raising path is never accepted. This is the ONE place every plan key
    is read through.
    """
    try:
        if not isinstance(mapping, dict):
            return None
        return mapping.get(key)
    except Exception:  # noqa: BLE001  (deny a hostile mapping, never crash)
        return None


def _plan_fingerprint_list(value):
    """A plan list, copied so a record altered mid-read cannot slip through.

    A wrong type, or a sequence whose own iteration raises, is denied as the
    empty shape: a partial read would look like real data.
    """
    try:
        if not isinstance(value, (list, tuple)):
            return []
        return list(value)
    except Exception:  # noqa: BLE001  (deny a hostile sequence, never crash)
        return []


def _plan_fingerprint_items(value):
    """The string members of one dependency pair; other members are denied."""
    out = []
    try:
        for item in value:
            out.append(item if isinstance(item, str) else "")
    except Exception:  # noqa: BLE001  (deny a hostile sequence, never crash)
        return []
    return out


def _plan_fingerprint_component(value):
    """Reduce one plan component to JSON-safe deterministic primitives.

    A node dict gives its id string; a dependency pair gives [holder id,
    unmet ids]; a bare string is itself. Anything else, including a value
    whose own methods raise, is denied as None rather than crashing and
    rather than being accepted as a node.
    """
    try:
        if isinstance(value, dict):
            node_id = _plan_fingerprint_get(value, "id")
            return node_id if isinstance(node_id, str) else ""
        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                return None
            left, right = value[0], value[1]
            if isinstance(left, dict):
                left = _plan_fingerprint_get(left, "id")
            if not isinstance(left, str):
                left = ""
            if isinstance(right, (list, tuple)):
                right = _plan_fingerprint_items(right)
            elif not isinstance(right, str):
                right = ""
            return [left, right]
        if isinstance(value, str):
            return value
    except Exception:  # noqa: BLE001  (deny a hostile component, never crash)
        return None
    return None


def _plan_fingerprint_capacity(value):
    """Capacity is an int, never a bool, never a float, never NaN."""
    try:
        if isinstance(value, int) and not isinstance(value, bool):
            return int(value)
    except Exception:  # noqa: BLE001  (deny a hostile number, never crash)
        return 0
    return 0


def plan_fingerprint(plan):
    """Return a stable 64-character lowercase hex fingerprint for a plan.

    D1.2 reads exactly the plan() output keys already shown in this file:
    batch, deferred, blocked, unknown_deps and capacity. A missing key is
    the empty list, or 0 for capacity. Nothing here raises and nothing here
    returns an empty string: a plan that cannot be read at all, including a
    mapping whose own get raises, is denied as that same restrictive shape,
    so corrupt input can never pass as a real plan.
    """
    batch = _plan_fingerprint_list(_plan_fingerprint_get(plan, "batch"))
    deferred = _plan_fingerprint_list(_plan_fingerprint_get(plan, "deferred"))
    blocked = _plan_fingerprint_list(_plan_fingerprint_get(plan, "blocked"))
    unknown = _plan_fingerprint_list(_plan_fingerprint_get(plan, "unknown_deps"))
    payload = _plan_fingerprint_empty_payload()
    payload["batch"] = [_plan_fingerprint_component(n) for n in batch]
    payload["deferred"] = [_plan_fingerprint_component(p) for p in deferred]
    payload["blocked"] = [_plan_fingerprint_component(p) for p in blocked]
    payload["unknown_deps"] = [_plan_fingerprint_component(p) for p in unknown]
    payload["capacity"] = _plan_fingerprint_capacity(_plan_fingerprint_get(plan, "capacity"))
    return _plan_fingerprint_digest(payload)


def load(path=None):
    if path is None:
        path = ROADMAP
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def nodes(doc):
    """Rows and features are the same kind of thing to the scheduler: a unit of
    work with dependencies and a write set. Treating them separately is how a
    feature and a row that touch one file get dispatched together."""
    if not _plan_doc_ok(doc):
        return []
    out = []
    for r in doc.get('rows', []) + doc.get('features', []):
        out.append({
            'id': r.get('id'),
            'title': r.get('title') or r.get('name') or '',
            'status': r.get('status'),
            'depends_on': list(r.get('depends_on') or []),
            'owns': list(r.get('owns') or []),
            # Carried through since 2026-08-29: integration verifies a unit's
            # own check ON canonical after the apply, and a node shape that
            # drops the check turns every integration into NO-DATA. Found by
            # running the spine end to end, not by reading.
            'done_check': r.get('done_check') or '',
            'repo': r.get('repo'),
            'hours': r.get('effort_hours') or r.get('estimate_hours') or 0,
            'in_ship': bool(r.get('in_ship_v1')),
            'owner': r.get('owner') or '',
            # None and [] are DIFFERENT. None means nobody declared a scope;
            # [] means the node was declared read-only. Collapsing them is the
            # bug this field exists to keep visible.
            'declared': r.get('owns') is not None,
            # The external verdict this node waits on, per the ready-set
            # standard. A node carrying one is never pulled by a session,
            # because no amount of session effort produces it.
            'event': r.get('event') or None,
        })
        # ORCH-02: carried through only when the source row actually gave
        # one, exactly the None/[] discipline 'declared' above already
        # uses for 'owns'. A row that never named a risk_class gets no
        # risk_class key here, and downstream code's own fallback (never
        # this function's) decides what an absent key means.
        for field in ROUTING_METADATA_FIELDS:
            if field in r:
                out[-1][field] = r[field]
    return out


def blocked_by(node, done):
    """The unmet dependencies BY NAME. The old ready_rows returned only the ready
    ids, so a blocked node was silent about why, and a dependency naming nothing
    was indistinguishable from one that was merely unfinished."""
    unmet = [d for d in node['depends_on'] if d not in done]
    return unmet


def unknown_deps(all_nodes):
    """A dependency naming no node is a broken graph, never a satisfied edge.
    Silently ignoring it makes a node look READY when its real prerequisite was
    deleted or renamed."""
    ids = set(n['id'] for n in all_nodes)
    return [(n['id'], d) for n in all_nodes for d in n['depends_on'] if d not in ids]


def downstream_weight(all_nodes):
    """How many nodes each node unblocks, transitively. Dagster's asset-graph
    prioritisation: among things that may run, run the one that frees the most."""
    children = {}
    for n in all_nodes:
        for d in n['depends_on']:
            children.setdefault(d, set()).add(n['id'])
    weight = {}

    def reach(nid, seen):
        if nid in seen:
            return set()          # a cycle contributes nothing rather than hanging
        seen = seen | {nid}
        out = set()
        for c in children.get(nid, ()):
            out.add(c)
            out |= reach(c, seen)
        return out

    for n in all_nodes:
        weight[n['id']] = len(reach(n['id'], set()))
    return weight


#: WHICH REPOSITORY A PATH BELONGS TO. Until 2026-08-29 this scheduler compared
#: BARE paths, so two nodes owning tools/x.py in two DIFFERENT repositories
#: looked like a collision and were serialised for no reason, while two nodes
#: genuinely sharing a file could look unrelated if one wrote it as a qualified
#: path and the other did not. An outside review named it and this board had
#: already recorded the same defect independently.
#:
#: A path may be written qualified ("BrotherModeUp:tools/x.py") or bare. A bare
#: path belongs to the node's own repo field, and failing that to the umbrella,
#: which is where this board's own paths live. Guessing beyond that would invent
#: a repository, so an unqualified path with no node repo is DEFAULT_REPO and
#: says so rather than matching everything.
DEFAULT_REPO = 'Brother'


def qualify(path, node_repo=None):
    """(repo, path) for one declaration, however it was written."""
    text = str(path or '').strip()
    if ':' in text:
        repo, _, rest = text.partition(':')
        repo, rest = repo.strip(), rest.strip()
        if repo and rest:
            return repo, rest.lstrip('./')
    return (node_repo or DEFAULT_REPO), text.lstrip('./')


def owned_pairs(node):
    """Every (repo, path) this node declares."""
    return [qualify(p, node.get('repo')) for p in node.get('owns') or []]


def conflicts(a, b):
    """Do two nodes' write sets overlap? A path conflicts with an identical path
    and with any path it contains, because owning a directory owns what is in it.
    A node owning NOTHING conflicts with nobody, which is why a read-only node
    can always be added to a batch."""
    for ra, pa in owned_pairs(a):
        for rb, pb in owned_pairs(b):
            # DIFFERENT REPOSITORIES CANNOT COLLIDE. Two files named tools/x.py
            # in two trees are two files, and serialising them wastes a slot for
            # nothing.
            if ra != rb:
                continue
            if pa == pb or pa.startswith(pb.rstrip('/') + '/') or pb.startswith(pa.rstrip('/') + '/'):
                return True
    return False


def machine_capacity():
    """The parallelism the machine can actually support, DERIVED rather than
    configured. Returns (slots, notes). Slots of 0 means refuse everything, and
    the note says why so a refusal is never mysterious."""
    notes = []
    try:
        total, _used, free = shutil.disk_usage(os.path.expanduser('~'))
        free_gib = free / (1024.0 ** 3)
    except OSError as exc:
        return 1, ['NO-DATA: could not read disk (%s), assuming one slot' % exc]
    cores = os.cpu_count() or 2
    slots = max(1, cores - CORES_RESERVED)
    notes.append('%d core(s), reserving %d, so %d slot(s)' % (cores, CORES_RESERVED, slots))
    if free_gib < DISK_REFUSE_GIB:
        notes.append('REFUSE: %.1f GiB free is under the %d GiB floor' % (free_gib, DISK_REFUSE_GIB))
        return 0, notes
    if free_gib < DISK_CLEANUP_GIB:
        slots = 1
        notes.append('CLEANUP BAND: %.1f GiB free is under %d GiB, so parallelism drops to 1 '
                     'rather than refusing outright' % (free_gib, DISK_CLEANUP_GIB))
    else:
        notes.append('%.1f GiB free, above the cleanup band' % free_gib)
    # Live CPU load, from resource_gate (W2 of the readiness roadmap): disk and
    # core COUNT above say what the machine could ever support, never what it
    # is doing right now. resource_gate.read() exists and is tested but sat
    # uninvoked from any real dispatch path (its own docstring records this).
    # Same graduated response as the disk bands: oversubscription drops slots
    # to 1 rather than refusing outright, and an unreadable load1 is treated
    # as oversubscribed too, on resource_gate's own stated reasoning ("assuming
    # healthy is how a scarce machine gets dispatched into").
    reading = resource_gate.read()
    load1, cores_available = reading.get('load1'), reading.get('cores_available')
    if load1 is None or cores_available is None:
        if slots > 1:
            slots = 1
        notes.append('LOAD NO-DATA: load1 or cores_available unreadable (%s); '
                     'parallelism held at 1 rather than assumed healthy'
                     % reading.get('errors'))
    elif load1 > cores_available:
        if slots > 1:
            slots = 1
        notes.append('LOAD BAND: 1-minute load %.2f exceeds %d available core(s), '
                     'so parallelism drops to 1 rather than adding to the '
                     'oversubscription' % (load1, cores_available))
    else:
        notes.append('load1 %.2f within %d available core(s)' % (load1, cores_available))
    return slots, notes


def _plan_impl(doc, slots=None, also_in_flight=None):
    """The dispatch plan: what is ready, what is blocked and by what, and the
    largest batch that may run TOGETHER without two writers on one path.

    `also_in_flight`, row H3: work this caller itself knows is live but that
    carries no IN-FLIGHT row in `doc` (rolling refill re-plans WHILE workers
    hold paths, and nothing writes that status for them). These are folded
    into the existing in_flight list, duplicate ids kept once, so the ONE
    conflict check below (batch + in_flight) is what admits or defers a node.
    No second conflict rule is added: a second opinion about the same
    question is exactly what this must not become."""
    all_nodes = nodes(doc)
    # SUPERSEDED counts as satisfied for dependency edges (its work moved to
    # named successors and the row is kept only so the edges stay honest), and
    # it is never dispatchable: on 2026-08-30 the ready set offered R12, a row
    # whose own text says its hours moved into G1-M3 and G1-M4, because this
    # filter knew only DONE and IN-FLIGHT.
    done = set(n['id'] for n in all_nodes
               if n['status'] in ('DONE', 'SUPERSEDED', 'ADDRESSED'))
    in_flight = [n for n in all_nodes if n['status'] == 'IN-FLIGHT']
    seen_in_flight = set(n['id'] for n in in_flight)
    for n in (also_in_flight or []):
        if n['id'] not in seen_in_flight:
            in_flight.append(n)
            seen_in_flight.add(n['id'])
    weight = downstream_weight(all_nodes)

    # status is needed for the founder gate, so carry it through
    for n in all_nodes:
        pass
    ready, blocked = [], []
    for n in all_nodes:
        if n['status'] in ('DONE', 'IN-FLIGHT', 'SUPERSEDED', 'ADDRESSED'):
            continue
        unmet = blocked_by(n, done)
        if unmet:
            blocked.append((n, unmet))
        else:
            ready.append(n)

    # SHIP MEMBERSHIP FIRST, then downstream weight, then cheapest.
    #
    # The founder's ask was to ship faster without compromising quality, and the
    # first draft of this sort got that wrong in a way worth recording: it
    # proposed a 40 hour node explicitly OUTSIDE the September 6 ship ahead of a
    # 6 hour node inside it, because the big one unblocked two things and the
    # small one unblocked one. Unblocking is the right tiebreak WITHIN a
    # commitment; it is the wrong primary key when a date has been named. A
    # scheduler that optimises purely for graph structure will always drift
    # toward the largest subtree, which is exactly how a deadline is missed by a
    # sequence of individually defensible choices.
    ready.sort(key=lambda n: (not n['in_ship'], -weight.get(n['id'], 0), n['hours'], n['id']))

    cap, notes = machine_capacity()
    if cap > ESTATE_BUILDER_CAP:
        notes.append('capped at %d by this estate\'s own builder limit, which is stricter '
                     'than the %d the hardware allows' % (ESTATE_BUILDER_CAP, cap))
        cap = ESTATE_BUILDER_CAP
    if slots is not None:
        cap = slots
        notes.append('slot count overridden to %d' % slots)

    # Greedy maximal batch: take the highest-priority node whose write set is
    # disjoint from everything already taken AND from everything in flight.
    batch, deferred = [], []
    for n in ready:
        if n['event']:
            # EVENT-WAIT, and it is checked BEFORE the founder gate so a node
            # waiting on a peer's commit is never reported as the founder's to
            # unblock. Naming the wrong owner sends him chasing someone else's
            # work, which is worse than saying nothing.
            deferred.append((n, 'EVENT-WAIT: %s' % n['event']))
            continue
        if n['owner'] in FOUNDER_OWNERS or n['status'] == 'AWAITING FOUNDER':
            deferred.append((n, 'FOUNDER-GATED: rendered in his lane, never pulled by a session'))
            continue
        if not n['declared']:
            deferred.append((n, 'NO DECLARED SCOPE: owns is absent, so nothing can prove this does '
                                'not collide. Declare its paths (owns: [...]), or declare it '
                                'read-only (owns: []), before it may be dispatched'))
            continue
        if len(batch) >= cap:
            deferred.append((n, 'no free slot: capacity is %d' % cap))
            continue
        clash = next((o for o in batch + in_flight if conflicts(n, o)), None)
        if clash:
            deferred.append((n, 'write set overlaps %s' % clash['id']))
            continue
        batch.append(n)
    return {'batch': batch, 'deferred': deferred, 'blocked': blocked,
            'in_flight': in_flight, 'weight': weight, 'capacity': cap,
            'notes': notes, 'unknown_deps': unknown_deps(all_nodes)}


def plan(doc, slots=None, also_in_flight=None):
    """D1.2 wrapper: refuse hostile or corrupt input as NO-DATA.

    The scheduling logic below is unchanged for a well-formed document.
    A document that is not a mapping, or whose rows/features are not
    sequences of mappings, is refused before any scheduling work, and any
    raw interpreter exception raised by a corrupt record is converted to
    the module's own NO-DATA plan rather than reaching the caller.
    """
    if not _plan_doc_ok(doc):
        return _plan_no_data('NO-DATA: plan refused a document that is not a '
                             'mapping with list rows and features')
    try:
        return _plan_impl(doc, slots=slots, also_in_flight=also_in_flight)
    except (TypeError, AttributeError, KeyError, ValueError, IndexError) as exc:
        return _plan_no_data('NO-DATA: plan refused corrupt input: %s' % exc)


def _scheduling_result_to_dict(result):
    """Convert a SchedulingResult-like object to a plain dict."""
    return {
        "status": getattr(result, "status", "NO-DATA"),
        "reason": getattr(result, "reason", ""),
        "chosen_order": tuple(getattr(result, "chosen_order", ())),
        "width": getattr(result, "width", 0),
        "policy_hash": getattr(result, "policy_hash", ""),
        "limits_hash": getattr(result, "limits_hash", ""),
        "constraints_hash": getattr(result, "constraints_hash", ""),
        "refused": tuple(getattr(result, "refused", ())),
    }


def _no_data_scheduling_plan(deferred, blocked, reason):
    """A plan that refuses as NO-DATA, with empty batch."""
    return {
        "batch": [],
        "deferred": list(deferred),
        "blocked": list(blocked),
        "scheduling": {
            "status": "NO-DATA",
            "reason": reason,
            "chosen_order": (),
            "width": 0,
            "policy_hash": "",
            "limits_hash": "",
            "constraints_hash": "",
            "refused": (),
        },
    }


def _blocks_scheduling_plan(deferred, blocked, reason):
    """A plan that refuses as BLOCKS, with empty batch."""
    return {
        "batch": [],
        "deferred": list(deferred),
        "blocked": list(blocked),
        "scheduling": {
            "status": "BLOCKS",
            "reason": reason,
            "chosen_order": (),
            "width": 0,
            "policy_hash": "",
            "limits_hash": "",
            "constraints_hash": "",
            "refused": (),
        },
    }


def plan_with_scheduling(
    ready: Sequence[Mapping[str, object]],
    deferred: Sequence[tuple[Mapping[str, object], str]],
    blocked: Sequence[tuple[Mapping[str, object], tuple[str, ...]]],
    incumbent_order: Sequence[str],
    policy: object,
    limits: object,
    constraints: object,
) -> dict[str, object]:
    """D15-B: integrate ranking and bounded width into a graph plan.

    Calls rank_ready_units from the scheduling policy module. If that module
    is missing, refuses as NO-DATA. If rank_ready_units raises SchedulingError,
    refuses as BLOCKS. On OK, batch is exactly the ready mappings in
    chosen_order[:width] order. deferred and blocked pass through unchanged.
    """
    if rank_ready_units is None:
        return _no_data_scheduling_plan(
            deferred, blocked,
            "scheduling policy module missing; cannot rank ready units",
        )
    try:
        ready_list = list(ready)
    except TypeError:
        return _no_data_scheduling_plan(deferred, blocked, "ready is not iterable")
    for item in ready_list:
        if not isinstance(item, Mapping):
            return _no_data_scheduling_plan(deferred, blocked, "ready item is not a mapping")
        if not isinstance(item.get("id"), str):
            return _no_data_scheduling_plan(deferred, blocked, "ready item missing string id")
    try:
        result = rank_ready_units(ready_list, policy, limits, constraints)
    except SchedulingError as exc:
        code = getattr(exc, "code", str(exc))
        return _blocks_scheduling_plan(deferred, blocked, code)
    except Exception as exc:
        return _no_data_scheduling_plan(deferred, blocked, "scheduling error: %s" % exc)

    scheduling = _scheduling_result_to_dict(result)
    status = scheduling.get("status")
    if status != "OK":
        batch = []
    else:
        chosen = scheduling.get("chosen_order", ())
        width = scheduling.get("width", 0)
        by_id = {}
        for item in ready_list:
            uid = item.get("id")
            if uid not in by_id:
                by_id[uid] = item
        batch = []
        for uid in chosen[:width]:
            if uid in by_id:
                batch.append(by_id[uid])
    return {
        "batch": batch,
        "deferred": list(deferred),
        "blocked": list(blocked),
        "scheduling": scheduling,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--slots', type=int, help='override the derived capacity, for tests')
    ap.add_argument('--roadmap', help='schedule THIS graph instead of the default readiness '
                                      'roadmap. The scheduling logic is stream independent; '
                                      'only the file it reads was ever hardcoded.')
    args = ap.parse_args(argv)
    try:
        doc = load(args.roadmap)
    except (OSError, ValueError) as exc:
        print('graph-loop: NO-DATA, cannot read the roadmap: %s' % exc, file=sys.stderr)
        return 2
    p = plan(doc, args.slots)

    for note in p['notes']:
        print('capacity: %s' % note)
    if p['unknown_deps']:
        for nid, dep in p['unknown_deps']:
            print('BROKEN GRAPH: %s depends on %r which names no node' % (nid, dep), file=sys.stderr)
    print()
    if p['in_flight']:
        print('IN FLIGHT (%d), their paths are held:' % len(p['in_flight']))
        for n in p['in_flight']:
            print('  %-6s %s' % (n['id'], n['title'][:62]))
        print()
    print('DISPATCH NOW (%d of %d slot(s)):' % (len(p['batch']), p['capacity']))
    for n in p['batch']:
        print('  %-6s unblocks %-2d  %sh  %s'
              % (n['id'], p['weight'].get(n['id'], 0), n['hours'], n['title'][:54]))
    if p['deferred']:
        print()
        print('READY BUT DEFERRED (%d):' % len(p['deferred']))
        for n, why in p['deferred']:
            print('  %-6s %s' % (n['id'], why))
    if p['blocked']:
        print()
        print('BLOCKED (%d), each naming what it waits on:' % len(p['blocked']))
        for n, unmet in p['blocked'][:12]:
            print('  %-6s BLOCKED-BY %s' % (n['id'], ', '.join(unmet)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
