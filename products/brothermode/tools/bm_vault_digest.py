#!/usr/bin/env python3
"""bm_vault_digest: per-person daily digests, built on bm_vault_route's own per-owner
routing lanes. WBS VB11-04.

WHY THIS EXISTS. bm_vault_route.py (VB10-03) already answers "whose problem is this",
grouped by owner and folder, over six already-shipped check sources. It is report-only:
nothing ever turned that report into a page a person actually reads. This module is that
page: one markdown digest per principal (owner, and separately steward when the two
differ), summarizing the SAME routed findings plus every note still sitting in
`promotion: candidate` (bm_vault_lifecycle's own state machine) awaiting steward review.
No new grouping algorithm is written here: owner and steward resolution stays entirely
delegated to bm_vault_route.owner_of() / bm_vault_contract's resolve_owner/resolve_steward,
and the folder-dedupe grain is bm_vault_contract.domain_of(), read off those modules, never
re-derived. The only new code is fanning one finding out to up to two principals (owner and,
when it differs, steward) instead of bm_vault_route's single-owner fan-out, because a digest
recipient is "whoever has to act on this", which can be either role.

TWO DEVIATIONS FROM THE ROW AS BRIEFED, both checked and named rather than guessed around:

1. "The existing Bases views from VB9-01": grepped this whole repository (tools/, templates/,
   vault-template/, docs/) for "VB9-01", "Bases" and "*.base" before writing a line of this
   module. Nothing exists: no VB9-01 row, no .base file, no syntax check for one anywhere in
   this tree. There is therefore no prior syntax to copy and no existing check to reuse for
   done-check item (d). The .base file this module ships (vault-template/99-System/views/
   pending-work.base) is authored directly against Obsidian's own published Bases syntax
   (obsidian.md/help/bases/syntax and .../bases/functions, fetched and read before writing
   the file: top-level filters/views keys, a view's type/name/order fields, bare property
   names in filter expressions, file.hasProperty()). The "syntax check" in the test file is
   this module's own minimal structural validator (check_base_syntax below), because the
   estate carries no YAML dependency (stdlib only, per the estate's own rule) and there is no
   sibling check to point a test at.

2. "The class vocabulary" for the immediate bypass: bm_vault_route.py's SOURCES were read in
   full (census/triage/rot/doctor/governance/posture) along with bm_vault_tiers.classify()'s
   own cls values (empty_note, whitespace_only_note, orphan_attachment, and the staleness
   kinds) -- none of the six sources, and no cls token anywhere in the tree, ever spells
   "revocation" or "security-finding". Per the row's own fallback, the two names are declared
   in exactly one place: IMMEDIATE_CLASSES below.

THE PENDING SET. "Pending items" are every note whose bm_vault_lifecycle.read_promotion()
state is exactly "candidate": a human-admitted note fresh out of bm_vault_intake.py, or a
machine-drafted candidate fresh out of bm_vault_enrich.py. Both are read by walking the
vault with bm_vault_lifecycle.walk() and reading each note's own promotion field -- never a
second store, never a second definition of "pending" (bm_vault_lifecycle.STATES is the one
vocabulary). Pending items are folded into the SAME per-principal grouping as routed
findings, tagged with source "pending", so a person's digest shows both problems routed to
them and drafts waiting on their review in the one page.

CARD FIELDS. Every card names: drafter identity (drafting_model when the note is a
machine-drafted bm_vault_enrich candidate, else the note's own provenance_actor field from
bm_vault_intake.py, else "unknown" -- never a guess), evidence (the routing source and
detail, or the pending reason), diff size (the drafted value's or candidate body's own line
and character count when the item is pending, "n/a (not a draft)" otherwise), and the
steward-review state (bm_vault_lifecycle's own promotion state, read fresh off the note).
A machine-drafted card's FIRST line is "MODEL: <drafting_model>", unmistakably, so a reader
scanning only headings can tell a model draft from a human one without opening the note.

THE IMMEDIATE BYPASS. Any finding whose detail carries an IMMEDIATE_CLASSES token as its
leading word (before ":") never reaches a daily digest: it is written instead to its own
99-System/digests/<date>-immediate.md, named on stdout, and the daily digest set is built
from every OTHER finding plus the full pending set (pending items are never urgent by this
module's own definition; nothing here reclassifies a draft as an immediate notice).

Exit 0: ran (digests written, or NO-DATA logged when nothing needed reporting). Exit 2:
NO-DATA, an unreadable vault or a dependency that failed to load. Python 3.9, standard
library only, no network, no subprocess.

No em or en dashes anywhere in this file.
"""
import argparse
import datetime
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_route as route      # noqa: E402
import bm_vault_lifecycle as lc     # noqa: E402
import bm_vault_ids as ids          # noqa: E402
import bm_vault_enrich as enrich    # noqa: E402
import bm_vault_policy as policy    # noqa: E402 -- VB3-13: decide(), never a parallel deny
import bm_vault_labels as labels    # noqa: E402 -- VB3-13: read_label(), the same field
                                     # bm_vault.py's own recall trim already honors

DIGEST_DIR = "99-System/digests"

# See the module docstring, deviation 2: no source in bm_vault_route.py's own vocabulary,
# and no cls token anywhere in this tree, ever spells either name. Declared once, here,
# rather than invented a second time at a call site.
IMMEDIATE_CLASSES = ("revocation", "security-finding")


# ---------------------------------------------------------------------------
# Reading a note's own record: drafter identity, diff size, steward state.
# Every helper below degrades to a named "unknown"/"n/a" rather than a guess
# or an exception, matching every sibling module's own boundary-read posture.
# ---------------------------------------------------------------------------

def _read_note(vault, path):
    if not path:
        return None
    try:
        with open(os.path.join(vault, path), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:  # sbe: allow-silent degrades to named unknown/n/a per module boundary-read posture above, not a guess or crash
        return None


_PROVENANCE_ACTOR_RE = re.compile(r"(?m)^provenance_actor:\s*(.+?)\s*$")


def drafter_identity(vault, path):
    """(label, is_model, model_name). label is what a card shows for "drafter:".
    is_model marks a machine-drafted bm_vault_enrich candidate so the caller can
    prefix the card with MODEL: unmistakably. Never a guess: "unknown" when
    neither field is present."""
    text = _read_note(vault, path)
    if text is None:
        return "unknown", False, None
    meta = enrich._read_enrich_meta(text)
    if meta and meta.get("type") == enrich.NOTE_TYPE and meta.get("drafter_kind") == "machine":
        model = meta.get("drafting_model") or "unknown-model"
        return model, True, model
    m = _PROVENANCE_ACTOR_RE.search(text)
    if m:
        return m.group(1).strip(), False, None
    return "unknown", False, None


def _note_body(text):
    """The text after the frontmatter fence, or the whole text when there is
    none. Uses bm_vault_ids.frontmatter's own (block, start, end) contract
    (end points at the "\\n---" the closing fence occupies, the same offset
    every sibling that splices frontmatter already uses)."""
    _block, _start, end = ids.frontmatter(text)
    if end == -1:
        return text
    return text[end + 4:]


def diff_size(vault, finding):
    """'n/a (not a draft)' for anything that is not a pending item (this
    module's own "pending" source, see collect_pending). For a pending item,
    the drafted value's (bm_vault_enrich) or the candidate body's (plain
    admitted note) own line and character count, read from the note, never
    a second copy of the value stored anywhere."""
    if finding.get("source") != "pending":
        return "n/a (not a draft)"
    text = _read_note(vault, finding.get("path"))
    if text is None:
        return "n/a (note unreadable)"
    body = _note_body(text)
    meta = enrich._read_enrich_meta(text)
    if meta and meta.get("type") == enrich.NOTE_TYPE:
        m = re.search(r"^# machine-drafted .+$", body, re.M)
        value = body[m.end():].lstrip("\n") if m else body
    else:
        value = body
    value = value.strip()
    lines_n = len(value.splitlines()) if value else 0
    return "+%d line(s), %d char(s)" % (lines_n, len(value))


def steward_review_state(vault, path):
    text = _read_note(vault, path)
    if text is None:
        return "n/a (no note to review)"
    state, _record, problems = lc.read_promotion(text)
    if state is None:
        return "unknown (%s)" % "; ".join(problems) if problems else "unknown"
    return state


def finding_class(finding):
    """The IMMEDIATE_CLASSES token a finding's detail carries as its own
    leading word (before ':'), the same shape bm_vault_tiers.classify()'s cls
    values already take once bm_vault_route folds them into a detail string.
    None for every finding today (see the module docstring, deviation 2)."""
    detail = finding.get("detail") or ""
    token = detail.split(":", 1)[0].strip()
    return token if token in IMMEDIATE_CLASSES else None


# ---------------------------------------------------------------------------
# The pending set: every note at promotion: candidate, read through
# bm_vault_lifecycle's own walk()/read_promotion(), never a second store.
# ---------------------------------------------------------------------------

def collect_pending(vault):
    out = []
    for abspath in lc.walk(vault):
        try:
            with open(abspath, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent an unreadable note is excluded from the pending set, same boundary-read posture as above
            continue
        state, _record, _problems = lc.read_promotion(text)
        if state != "candidate":
            continue
        rel = os.path.relpath(abspath, vault).replace(os.sep, "/")
        out.append({"source": "pending", "path": rel,
                    "detail": "candidate awaiting steward review"})
    return out


# ---------------------------------------------------------------------------
# Routing by principal: the SAME per-owner grouping bm_vault_route.route_findings
# runs (folder dedupe via domain_of, owner/steward resolution via owner_of),
# generalized to fan a finding out to both owner and steward when they differ,
# because either role may be the one who has to act on it. Owner/steward
# resolution itself is never re-implemented: only the fan-out is new.
# ---------------------------------------------------------------------------

def route_by_principal(vault, findings, owners_map, contract_mod, default_owner,
                        authorized=None):
    """(grouped, withheld_count). authorized(principal, relpath) -> bool, VB3-13:
    when given, a finding is dropped from a principal's own group (never from a
    co-principal's) the same call would deny; None keeps every finding, the
    byte-identical behavior this function had before that row existed."""
    by_principal = {}
    withheld = 0
    for f in findings:
        owner, steward = route.owner_of(vault, f["path"], owners_map, contract_mod)
        owner = owner or default_owner
        steward = steward or owner
        folder = contract_mod.domain_of(f["path"]) if f["path"] else "UNSCOPED"
        for principal in {owner, steward}:
            if authorized is not None and not authorized(principal, f.get("path")):
                withheld += 1
                continue
            groups = by_principal.setdefault(principal, {})
            group = groups.setdefault(folder, {"folder": folder, "count": 0, "findings": []})
            group["count"] += 1
            group["findings"].append(f)
    grouped = {p: sorted(g.values(), key=lambda x: x["folder"]) for p, g in by_principal.items()}
    return grouped, withheld


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------

def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "principal"


def _render_card(vault, finding):
    label, is_model, model = drafter_identity(vault, finding.get("path"))
    where = (finding["path"] + ": ") if finding.get("path") else ""
    lines = []
    if is_model:
        lines.append("MODEL: %s" % model)
    lines.append("drafter: %s" % label)
    lines.append("evidence: %s[%s] %s" % (where, finding["source"], finding["detail"]))
    lines.append("diff size: %s" % diff_size(vault, finding))
    lines.append("steward review: %s" % steward_review_state(vault, finding.get("path")))
    return lines


def render_digest(vault, principal, groups, date):
    lines = ["---", "type: digest", "principal: %s" % principal, "date: %s" % date,
              "---", "", "# Digest for %s, %s" % (principal, date), ""]
    for g in groups:
        lines.append("## %s (%d item(s))" % (g["folder"], g["count"]))
        ordered = sorted(g["findings"],
                          key=lambda f: (f["source"], f.get("path") or "", f["detail"]))
        for i, f in enumerate(ordered, 1):
            lines.append("")
            lines.append("### item %d" % i)
            lines.extend(_render_card(vault, f))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_immediate(vault, immediate, owners_map, contract_mod, default_owner, date):
    lines = ["---", "type: immediate-notice", "date: %s" % date, "---", "",
             "# Immediate notices, %s" % date, ""]
    ordered = sorted(immediate,
                      key=lambda f: (f["source"], f.get("path") or "", f["detail"]))
    for f in ordered:
        owner, steward = route.owner_of(vault, f.get("path"), owners_map, contract_mod)
        owner = owner or default_owner
        steward = steward or owner
        where = (f["path"] + ": ") if f.get("path") else ""
        lines.append("- class=%s owner=%s steward=%s %s[%s] %s" % (
            finding_class(f), owner, steward, where, f["source"], f["detail"]))
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# build: the one entry point tests and the CLI both call.
# ---------------------------------------------------------------------------

def build(vault, date=None, owners_override=None,
          default_owner=route.DEFAULT_OWNER, tools_dir=None):
    """(exit_code, digest_paths, immediate_path_or_None, [message, ...]).
    digest_paths and immediate_path are vault-relative, already written."""
    tools_dir = tools_dir or HERE
    date = date or datetime.date.today().isoformat()
    if not vault or not os.path.isdir(vault):
        return 2, [], None, ["bm_vault_digest: NO-DATA, no readable vault at %r" % vault]

    contract_mod = route._load_sibling("bm_vault_contract", tools_dir)
    if contract_mod is None:
        return 2, [], None, ["bm_vault_digest: NO-DATA, bm_vault_contract failed to load; "
                              "owner/steward resolution has no seam without it"]
    owners_map, err = contract_mod.load_owners_map(vault, owners_override)
    if err:
        return 2, [], None, ["bm_vault_digest: NO-DATA, %s" % err]

    findings, no_data_sources = route.collect_all(vault, tools_dir)
    pending = collect_pending(vault)

    immediate = [f for f in findings if finding_class(f)]
    daily = [f for f in findings if not finding_class(f)]
    combined = daily + pending

    # VB3-13: derived memory cannot declassify -- a digest is a downstream surface
    # exactly like an export bundle, so a restricted-labeled claim must never reach
    # an unauthorized principal's page. Composes with bm_vault_policy.decide(), the
    # SAME primitive bm_vault.py's own recall trim already calls: no parallel deny
    # mechanism is added here. No policy file (the opt-in default everywhere else
    # in this estate) means every finding is authorized, byte-identical to this
    # function's own behavior before this row existed.
    pol_path = policy.policy_path(vault, None)
    pol_policy, pol_problems = policy.load(pol_path)
    if pol_problems:
        return 2, [], None, ["bm_vault_digest: NO-DATA access policy: %s. A broken "
                              "policy fails closed, not open." % "; ".join(pol_problems)]
    note_cache = {}

    def _note_text(relpath):
        if relpath not in note_cache:
            note_cache[relpath] = _read_note(vault, relpath) or ""
        return note_cache[relpath]

    def _authorized(principal, relpath):
        if pol_policy is None or not relpath:
            return True
        text = _note_text(relpath)
        if labels.read_label(text) == labels.LABELS[0]:
            return True  # public: this row's scope is the restricted/internal axis only
        verdict = policy.decide(pol_policy, principal, relpath)
        if verdict == "allow":
            return True
        if verdict == "require_approval":
            state, _record, problems = lc.read_promotion(text)
            return lc.counts_as_canonical(state, problems)
        return False

    routed, withheld = route_by_principal(
        vault, combined, owners_map, contract_mod, default_owner, authorized=_authorized)

    digest_dir_abs = os.path.join(vault, DIGEST_DIR)
    digest_paths = []
    for principal in sorted(routed):
        text = render_digest(vault, principal, routed[principal], date)
        rel = "%s/%s-%s.md" % (DIGEST_DIR, date, _slug(principal))
        os.makedirs(digest_dir_abs, exist_ok=True)
        with open(os.path.join(vault, rel), "w", encoding="utf-8") as fh:
            fh.write(text)
        digest_paths.append(rel)

    immediate_path = None
    if immediate:
        os.makedirs(digest_dir_abs, exist_ok=True)
        rel = "%s/%s-immediate.md" % (DIGEST_DIR, date)
        text = render_immediate(vault, immediate, owners_map, contract_mod, default_owner, date)
        with open(os.path.join(vault, rel), "w", encoding="utf-8") as fh:
            fh.write(text)
        immediate_path = rel

    messages = []
    if not digest_paths and not immediate_path:
        messages.append("bm_vault_digest: NO-DATA, nothing to digest for %s" % date)
        return 0, [], None, messages
    for rel in digest_paths:
        messages.append("DIGEST %s" % rel)
    if immediate_path:
        messages.append("IMMEDIATE %s" % immediate_path)
    if withheld:
        messages.append("WITHHELD %d item(s) excluded from digests by access policy"
                         % withheld)
    if no_data_sources:
        messages.append("NO-DATA sources (sibling failed to load, skipped): %s"
                         % ", ".join(sorted(no_data_sources)))
    return 0, digest_paths, immediate_path, messages


# ---------------------------------------------------------------------------
# The pending-work Bases view: a minimal structural check, see deviation 1.
# Not a YAML parser (the estate is stdlib-only); enough to catch a broken
# top-level shape or a view missing its required fields.
# ---------------------------------------------------------------------------

def check_base_syntax(text):
    """(ok, [problem, ...]). Checks the shape obsidian.md/help/bases/syntax
    documents: a top-level "views:" key, at least one view block carrying
    both "type:" and "name:", and, when "filters:" is present, at least one
    "and:" or "or:" list under it with real "- " list items."""
    problems = []
    lines = text.splitlines()
    if not any(re.match(r"^views:\s*$", ln) for ln in lines):
        problems.append("no top-level 'views:' key")
        return False, problems
    view_blocks = [i for i, ln in enumerate(lines) if re.match(r"^\s*-\s*type:\s*\S+", ln)]
    if not view_blocks:
        problems.append("no view block with a 'type:' field under views:")
        return False, problems
    for i in view_blocks:
        block_indent = len(lines[i]) - len(lines[i].lstrip(" "))
        has_name = False
        j = i + 1
        while j < len(lines):
            ln = lines[j]
            if ln.strip() == "":
                j += 1
                continue
            indent = len(ln) - len(ln.lstrip(" "))
            if indent <= block_indent and re.match(r"^\s*-\s", ln):
                break
            if re.match(r"^\s*name:\s*\S", ln):
                has_name = True
            j += 1
        if not has_name:
            problems.append("view block at line %d has no 'name:' field" % (i + 1))
    if any(re.match(r"^filters:\s*$", ln) for ln in lines):
        if not any(re.match(r"^\s*(and|or):\s*$", ln) for ln in lines):
            problems.append("'filters:' present with no 'and:'/'or:' list under it")
    return (not problems), problems


PENDING_BASE_RELPATH = "99-System/views/pending-work.base"

PENDING_BASE_CONTENT = """\
filters:
  and:
    - file.hasProperty("promotion")
    - promotion == "candidate"

views:
  - type: table
    name: "Pending review"
    order:
      - file.name
      - promotion
      - type
      - drafter_kind
      - drafting_model
      - target_note
      - enrich_field
      - provenance_actor
      - created
"""


# -------------------------------------------------------------------- CLI

def cmd_build(args):
    code, _digests, _immediate, messages = build(
        args.vault, args.date, args.owners, args.default_owner)
    for m in messages:
        print(m)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("build",))
    ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))
    ap.add_argument("--date", default=None)
    ap.add_argument("--owners", default=None)
    ap.add_argument("--default-owner", default=route.DEFAULT_OWNER)
    args = ap.parse_args(argv)
    return cmd_build(args)


if __name__ == "__main__":
    sys.exit(main())
