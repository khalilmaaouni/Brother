#!/usr/bin/env python3
"""tools/bm_plan.py: F2, the plan as a versioned, approvable artifact, and
the read side of R7, the four clause gate a controller run checks before
it may dispatch (the write side, the refusal itself, lives in
tools/bm_controller.py's ControllerEngine, injected plan_gate parameter,
and StorePlanGate below).

WHAT THIS IS
  A plan for one project is a single JSON record, not a paragraph that
  scrolled past in chat and was never looked at again. It carries the
  options that were considered, which one is recommended, what will run,
  what will explicitly NOT run, the blast radius, the reversibility, and
  the evidence promised, plus one companion field, unknown_fields, that
  names which of those the author does not yet know rather than leaving
  it silently blank. Every distinct CONTENT of a plan has exactly one
  VERSION: a 12 lowercase hex character fingerprint of its content
  fields, the same fingerprint shape tools/bm_store.py's views table
  already uses for a published page. Two plans with byte identical
  content always fingerprint the same; changing any content field, or
  adding or removing an unknown_fields entry, changes it.

WHY THIS EXISTS. The founder scored Brother 0 out of 5 because intake
  never showed him options with diagrams, though the rule was written
  twice and marked buildable. The rule lived as ADVICE TO A MODEL, and
  advice with nothing that can fail when it is skipped degrades to
  optional under time pressure, every time. This file is the general fix
  for that failure mode: a plan is no longer a sentence a model chose to
  write, it is a record a controller run can be MADE to check for,
  structurally, before it is allowed to act.

BORROWED FROM, and the adaptation. Kimi Code (Moonshot) ships a
  constrained Plan mode whose plan is a persisted artifact submitted for
  approval before the agent may act on it. Kimi's approval approves A
  PLAN. This file's adaptation is the line that matters: approval is
  bound to a specific plan VERSION (the content fingerprint above), and
  tools/bm_controller.py's gate refuses to dispatch unless the approved
  version and the plan's CURRENT version are the same string. A plan
  that mutated after approval is a plan whose approval no longer covers
  what would actually run, so drift between approval and execution is a
  REFUSAL, never a silent proceed on a plan that merely looks familiar.

THE CEILING, stated once and plainly rather than left for a later reader
  to over-read a green result: this file enforces that an APPROVED plan
  VERSION MATCHES what is about to execute. It does not and cannot judge
  whether the plan was a GOOD plan: whether the options considered were
  the right options, whether the recommendation was sound, or whether a
  founder actually read the plan before approving it. Those stay human
  judgements this file has no way to see, and no test in
  tools/test_bm_plan.py claims otherwise.

WHERE A PLAN LIVES. Plain JSON on disk, one file per project, at
  <root>/.brothermode/plans/<project_id>.json, written atomically (a
  temp file plus os.replace) so a crash mid write never leaves a half
  written plan for the next reader. This file issues NO SQL and imports
  no sqlite3: it has nothing to do with tools/bm_store.py's schema and
  cannot be mistaken for a schema migration. ".brothermode" names the
  same hidden directory tools/bm_store.py's STORE_DIRNAME already uses
  for this project's store; it is named again here rather than imported,
  because this file takes zero dependency on tools/bm_store.py by
  design (its core, PlanStore and the functions above it, is unit
  tested in tools/test_bm_plan.py with nothing but tempfile). The CLI
  section below is the one place that reuses tools/bm_store.py, and only
  for its root resolution helper, loaded the same by-path technique
  tools/bm_controller.py's own _load uses, so a plan lives at the SAME
  project root every other bm_*.py command resolves rather than this
  file inventing a second notion of "which project".

Python 3.9, standard library only. No network.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import datetime
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

#: The seven content fields F2 requires a plan to carry. Every one must
#: be present and truthy, OR be named in the plan's own unknown_fields
#: with a non-empty reason it is unknown (clauses (a) and (b) of R7).
#: Order here is the order a refusal message lists them in.
REQUIRED_FIELDS = (
    "options", "recommended", "will_run", "will_not_run",
    "blast_radius", "reversibility", "evidence_promised",
)

#: Every top level key `PlanStore.record` accepts: the seven content
#: fields plus the one field that marks some of them explicitly unknown.
#: Anything else is refused by name, the same "unknown field" shape
#: tools/bm_store.py's record_view and record_insight already use for
#: exactly this class of typo.
CONTENT_FIELDS = REQUIRED_FIELDS + ("unknown_fields",)


class PlanError(Exception):
    """Every refusal this file raises. `reason` is a kebab case code, the
    same convention tools/bm_store.py's OwnershipRefused uses, so a
    renderer can key a founder facing block off it the same way."""

    def __init__(self, reason, message):
        self.reason = reason
        super(PlanError, self).__init__(message)

    def __str__(self):
        return self.args[0] if self.args else ""


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _canonical_content(plan):
    """The bytes plan_version fingerprints: exactly the content fields,
    nothing else. Deliberately excludes every approval bookkeeping field
    (version, approved, approved_version, approved_by, approved_at,
    updated_at, recorded_by): approving a plan WRITES those fields, and
    if they fed the fingerprint too, approving a plan would change the
    very version it just approved, so clause (d) could never hold the
    instant after a successful approve().

    'redirects' (F1, mid-stream steering) is the one exception, and
    deliberately so: a redirect IS a content change, the whole point of
    routing it through a plan version bump rather than a side channel
    (see PlanStore.record_redirect below), so it must move plan_version
    or a redirect would silently fail to invalidate the stale approval
    clause (d) exists to catch. Absent, it defaults to [], same value a
    plan that has never been redirected always had, so this is neutral
    for every plan recorded before F1 existed."""
    content = dict((field, plan.get(field)) for field in REQUIRED_FIELDS)
    content["unknown_fields"] = plan.get("unknown_fields") or {}
    content["redirects"] = plan.get("redirects") or []
    return json.dumps(content, sort_keys=True, default=str)


def plan_version(plan):
    """The plan's content fingerprint: 12 lowercase hex characters of the
    sha256 over its canonical content (_canonical_content above). This is
    the VERSION clause (d) of R7 compares an approval against."""
    digest = hashlib.sha256(_canonical_content(plan).encode("utf-8"))
    return digest.hexdigest()[:12]


def validate_plan(plan):
    """R7 clauses (a) and (b): the option schema is complete, and every
    field that is not is named in unknown_fields with why. Raises
    PlanError and leaves the caller's object untouched; returns True
    when both hold.

    A present but falsy field (empty string, empty list, empty dict)
    counts as missing, not satisfied: an empty will_not_run is a claim
    that nothing was excluded, and that claim must be WRITTEN, never
    defaulted from an absent key."""
    if not isinstance(plan, dict):
        raise PlanError(
            "bad-plan",
            "a plan must be a dict of (%s), got %r"
            % (", ".join(CONTENT_FIELDS), type(plan).__name__))
    unknown_fields = plan.get("unknown_fields") or {}
    if not isinstance(unknown_fields, dict):
        raise PlanError(
            "bad-unknown-fields",
            "unknown_fields must be a dict mapping a field name to the "
            "reason it is unknown, got %r" % (type(unknown_fields).__name__,))
    bad_keys = sorted(k for k in unknown_fields if k not in REQUIRED_FIELDS)
    if bad_keys:
        raise PlanError(
            "bad-unknown-fields",
            "unknown_fields names field(s) outside the plan schema: %s "
            "(the schema is: %s)"
            % (", ".join(bad_keys), ", ".join(REQUIRED_FIELDS)))
    missing = []
    for field in REQUIRED_FIELDS:
        if field in unknown_fields:
            reason = unknown_fields[field]
            if not isinstance(reason, str) or not reason.strip():
                raise PlanError(
                    "bad-unknown-fields",
                    "unknown_fields[%r] must name WHY it is unknown; an "
                    "empty reason is the same omission this field exists "
                    "to forbid" % (field,))
            continue
        value = plan.get(field)
        if value is None or value == "" or value == [] or value == {}:
            missing.append(field)
    if missing:
        raise PlanError(
            "incomplete-plan",
            "plan is missing required field(s): %s. Fill them in, or mark "
            "each one explicitly unknown in unknown_fields with the "
            "reason; never omit one silently." % (", ".join(missing),))
    return True


def check_plan_gate(plan):
    """R7's whole four clause invariant, judged over ONE plan record as
    PlanStore.read returns it (or None, when no plan was ever recorded).
    Raises PlanError naming exactly which clause failed; returns True
    only when all four hold:

      (a) and (b), via validate_plan: the option schema is complete, and
          every field that is not is explicitly marked unknown.
      (c) the user approved the identified scope: judged by the SAME
          `approved` flag clause (d) reads below. Approving a plan
          record IS approving the scope it identifies; this file
          introduces no second, separate scope object a founder would
          have to approve twice.
      (d) the approved plan version matches the version about to
          execute: plan_version(plan) is compared against the version
          that was actually approved, not against any other number."""
    if plan is None:
        raise PlanError(
            "no-plan",
            "no plan has been recorded for this project. Record one "
            "(bm_plan.py record) and have it approved before a "
            "controller run may dispatch.")
    validate_plan(plan)
    if not plan.get("approved"):
        raise PlanError(
            "plan-not-approved",
            "this plan has not been approved. Approving a plausible plan "
            "is not approving whatever eventually runs: approve THIS "
            "plan's current version explicitly (bm_plan.py approve).")
    current = plan_version(plan)
    approved_version = plan.get("approved_version")
    if approved_version != current:
        raise PlanError(
            "plan-drifted",
            "the approved plan version (%s) does not match this plan's "
            "current version (%s). The plan changed after it was "
            "approved, so that approval no longer covers what would run. "
            "Approve the new version, or restore the content that was "
            "approved." % (approved_version, current))
    return True


class PlanStore(object):
    """The durable home for one project's plan: one JSON file per
    project, one version live at a time. An earlier version is not kept
    around by this file; git history over the plan file (it is plain
    JSON, meant to be committed) is the record of what changed, the same
    way it already is for every other founder authored file in this
    repository."""

    #: Named, not imported, from tools/bm_store.py's own STORE_DIRNAME;
    #: see this module's own docstring for why this file takes no import
    #: dependency on that one.
    _DIRNAME = os.path.join(".brothermode", "plans")

    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.dir = os.path.join(self.root, self._DIRNAME)

    def _path(self, project_id):
        if (not isinstance(project_id, str) or not project_id
                or "/" in project_id or "\\" in project_id
                or project_id in (".", "..")):
            raise PlanError(
                "bad-project-id",
                "project_id %r is not a safe plan file name" % (project_id,))
        return os.path.join(self.dir, project_id + ".json")

    def read(self, project_id):
        """This project's current plan record, or None when it has never
        had one. None is a real answer a caller (check_plan_gate above)
        reads as its own refusal, not a gap this method papers over."""
        path = self._path(project_id)
        if not os.path.exists(path):
            return None
        with open(path, "r") as fh:
            return json.load(fh)

    def _write(self, project_id, record):
        os.makedirs(self.dir, exist_ok=True)
        path = self._path(project_id)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".plan-",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(record, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return record

    def record(self, project_id, content, actor=""):
        """Write project_id's plan content (F2). `content` is a dict of
        ONLY CONTENT_FIELDS entries; an unrecognised key refuses
        'unknown-field'. Validated with validate_plan BEFORE anything is
        written, so a malformed or incomplete record leaves the prior
        plan, if any, untouched on disk.

        Approval bookkeeping (approved, approved_version, approved_by,
        approved_at) is carried forward from the prior record UNCHANGED,
        never reset here: recording new content does not itself revoke
        an old approval. What makes an old approval stop mattering is
        check_plan_gate's own version comparison, which this call's
        content edit will now fail if the content actually changed
        (clause (d)). A caller that wants a clean slate calls `approve`
        again after `record`; that is not an implicit side effect of
        `record`."""
        if not isinstance(content, dict):
            raise PlanError(
                "bad-plan",
                "plan content must be a dict, got %r"
                % (type(content).__name__,))
        unknown = sorted(k for k in content if k not in CONTENT_FIELDS)
        if unknown:
            raise PlanError(
                "unknown-field",
                "unknown plan field(s) %s (allowed: %s)"
                % (", ".join(unknown), ", ".join(CONTENT_FIELDS)))
        validate_plan(content)
        prior = self.read(project_id) or {}
        record = dict((field, content.get(field)) for field in
                      REQUIRED_FIELDS)
        record["unknown_fields"] = content.get("unknown_fields") or {}
        # Carried forward UNCHANGED, same as the approval bookkeeping
        # below: redirect history is provenance of what happened to this
        # project's plan over time, not content the author of a new
        # revision is asked to restate, and re-recording content must
        # never silently erase it.
        record["redirects"] = prior.get("redirects") or []
        record["version"] = plan_version(record)
        record["approved"] = prior.get("approved", False)
        record["approved_version"] = prior.get("approved_version")
        record["approved_by"] = prior.get("approved_by", "")
        record["approved_at"] = prior.get("approved_at")
        record["recorded_by"] = actor or ""
        record["updated_at"] = _now_iso()
        return self._write(project_id, record)

    def approve(self, project_id, approved_by):
        """Approve project_id's CURRENT plan at its CURRENT version
        (clause (c), and the version half of clause (d)). Refuses
        'no-plan' if nothing was ever recorded, and re-validates
        (clauses (a) and (b)) before approving: an incomplete plan
        cannot be approved, so an approval can never outrun the
        completeness check that is supposed to gate it."""
        plan = self.read(project_id)
        if plan is None:
            raise PlanError(
                "no-plan",
                "project %r has no plan to approve; record one first"
                % (project_id,))
        validate_plan(plan)
        if not isinstance(approved_by, str) or not approved_by.strip():
            raise PlanError(
                "bad-approver", "approved_by must name who approved this "
                "plan")
        plan["approved"] = True
        plan["approved_version"] = plan_version(plan)
        plan["approved_by"] = approved_by
        plan["approved_at"] = _now_iso()
        plan["updated_at"] = _now_iso()
        return self._write(project_id, plan)

    def record_redirect(self, project_id, note, actor=""):
        """F1 (mid-stream steering): bump project_id's plan to a new
        content-fingerprint VERSION carrying a redirect note, leaving
        approval bookkeeping (approved, approved_version, approved_by,
        approved_at) untouched. This is the file's own BORROWED FROM
        section made concrete: Kimi Code's Ctrl-S treats a mid-stream
        correction as a message; Brother routes it into the plan as a
        VERSION BUMP instead, so it becomes recorded provenance with a
        before and an after, not a turn nobody can reconstruct later.

        Because 'redirects' participates in plan_version's fingerprint
        (_canonical_content above), the returned version always differs
        from the version before this call, and clause (d) of R7
        (check_plan_gate) will correctly refuse to dispatch on the now
        stale approval until someone approves the redirected plan again.
        That refusal is deliberate, not a bug this method should paper
        over: an approval given before the redirect did not cover it.

        Raises PlanError('no-plan') if this project has never had a plan
        recorded; a redirect has nothing to bump. Raises
        PlanError('bad-redirect') for an empty or non-string note: a
        redirect with no note is exactly the lost-turn failure mode this
        method exists to prevent."""
        if not isinstance(note, str) or not note.strip():
            raise PlanError(
                "bad-redirect", "a redirect must carry a non-empty note")
        current = self.read(project_id)
        if current is None:
            raise PlanError(
                "no-plan",
                "project %r has no plan to redirect; record one first"
                % (project_id,))
        redirects = list(current.get("redirects") or [])
        redirects.append({
            "note": note,
            "at": _now_iso(),
            "actor": actor or "",
            "from_version": current.get("version"),
        })
        record = dict(current)
        record["redirects"] = redirects
        record["version"] = plan_version(record)
        record["updated_at"] = _now_iso()
        return self._write(project_id, record)

    def check(self, project_id):
        """R7's whole invariant over this project's current plan. See
        check_plan_gate's own docstring for the four clauses; this is a
        thin read then check wrapper so a caller holding only a
        PlanStore need not import check_plan_gate separately."""
        return check_plan_gate(self.read(project_id))


# ---------------------------------------------------------------------------
# CLI: record, approve, show, check.
# ---------------------------------------------------------------------------

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2

_bs_module = None


def _bs():
    """tools/bm_store.py, loaded by path the same technique
    tools/bm_controller.py's own _load uses, cached after the first call.
    Used ONLY for its root resolution helper (require_root); this file
    never opens a Store or touches bm_store.py's schema."""
    global _bs_module
    if _bs_module is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bm_store", os.path.join(HERE, "bm_store.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _bs_module = mod
    return _bs_module


def _root():
    mod = _bs()
    try:
        root, _source = mod.require_root()
    except mod.OwnershipRefused as exc:
        raise PlanError(exc.reason, str(exc))
    return root


def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _parse(argv, known, wants_value=()):
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                raise PlanError("bad-flag", "unrecognized flag --%s" % (name,))
            if name in wants_value:
                i += 1
                if i >= len(argv):
                    raise PlanError("bad-flag", "--%s needs a value" % (name,))
                kv[name] = argv[i]
            else:
                kv[name] = True
        else:
            positional.append(tok)
        i += 1
    return positional, kv


def _print_json(obj):
    _out(json.dumps(obj, indent=2, sort_keys=True))


def cmd_record(argv):
    _pos, kv = _parse(
        argv, ("project", "file", "actor-name", "json"),
        wants_value=("project", "file", "actor-name"))
    project_id = kv.get("project")
    file_path = kv.get("file")
    if not project_id or not file_path:
        _err("usage: record --project ID --file PATH [--actor-name NAME] "
             "[--json]")
        return EXIT_USAGE
    if file_path == "-":
        content = json.load(sys.stdin)
    else:
        with open(file_path, "r") as fh:
            content = json.load(fh)
    store = PlanStore(_root())
    record = store.record(project_id, content, actor=kv.get("actor-name", ""))
    if kv.get("json"):
        _print_json(record)
    else:
        _out("plan recorded for %s: version %s" % (project_id,
                                                    record["version"]))
    return EXIT_OK


def cmd_approve(argv):
    _pos, kv = _parse(
        argv, ("project", "actor-name", "json"),
        wants_value=("project", "actor-name"))
    project_id = kv.get("project")
    approved_by = kv.get("actor-name")
    if not project_id or not approved_by:
        _err("usage: approve --project ID --actor-name NAME [--json]")
        return EXIT_USAGE
    store = PlanStore(_root())
    record = store.approve(project_id, approved_by)
    if kv.get("json"):
        _print_json(record)
    else:
        _out("plan approved for %s: version %s"
             % (project_id, record["approved_version"]))
    return EXIT_OK


def cmd_show(argv):
    _pos, kv = _parse(argv, ("project", "json"), wants_value=("project",))
    project_id = kv.get("project")
    if not project_id:
        _err("usage: show --project ID [--json]")
        return EXIT_USAGE
    store = PlanStore(_root())
    record = store.read(project_id)
    if record is None:
        _err("no plan recorded for %s" % (project_id,))
        return EXIT_REFUSED
    _print_json(record)
    return EXIT_OK


def cmd_check(argv):
    _pos, kv = _parse(argv, ("project", "json"), wants_value=("project",))
    project_id = kv.get("project")
    if not project_id:
        _err("usage: check --project ID [--json]")
        return EXIT_USAGE
    store = PlanStore(_root())
    try:
        store.check(project_id)
    except PlanError as exc:
        if kv.get("json"):
            _print_json({"ok": False, "reason": exc.reason,
                        "message": str(exc)})
        else:
            _err("REFUSED (%s): %s" % (exc.reason, exc))
        return EXIT_REFUSED
    if kv.get("json"):
        _print_json({"ok": True})
    else:
        _out("ok: plan gate holds")
    return EXIT_OK


def cmd_redirect(argv):
    _pos, kv = _parse(
        argv, ("project", "note", "actor-name", "json"),
        wants_value=("project", "note", "actor-name"))
    project_id = kv.get("project")
    note = kv.get("note")
    if not project_id or not note:
        _err("usage: redirect --project ID --note TEXT [--actor-name NAME] "
             "[--json]")
        return EXIT_USAGE
    store = PlanStore(_root())
    record = store.record_redirect(project_id, note,
                                   actor=kv.get("actor-name", ""))
    if kv.get("json"):
        _print_json(record)
    else:
        _out("plan redirected for %s: version %s" % (project_id,
                                                      record["version"]))
    return EXIT_OK


_COMMANDS = {"record": cmd_record, "approve": cmd_approve, "show": cmd_show,
            "check": cmd_check, "redirect": cmd_redirect}


def main(argv):
    if not argv or argv[0] not in _COMMANDS:
        _err("tools/bm_plan.py: F2, the plan as a versioned artifact.")
        _err("commands: %s" % (", ".join(sorted(_COMMANDS))))
        return EXIT_USAGE
    try:
        return _COMMANDS[argv[0]](argv[1:])
    except PlanError as exc:
        _err("REFUSED (%s): %s" % (exc.reason, exc))
        return EXIT_REFUSED
    except (OSError, ValueError) as exc:
        _err("error: %s" % (exc,))
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
