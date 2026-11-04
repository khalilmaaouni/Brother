#!/usr/bin/env python3
"""BrotherSBE plan: dossier to task graph, deterministically.

Spec of record: docs/specs/2026-07-30-sbe-plan-derivation.md. The fixtures in
tools/test_sbe_plan.py and this file both read from that document, and a
disagreement between them is a defect in one of the three, never a negotiation.

Two surfaces, one truth:

  sbe_plan.py <dossier-dir> [--write] [--json] [--strict] [--cwd <repo>]
      Default: validate design/<change>/08-plan.json against the dossier with
      the PLAN_CHECKS registry below. --write derives the plan from the dossier
      first, writes 08-plan.json, then validates what was written with the same
      rules. An empty derivation writes NOTHING and exits 1, because the
      essentials law says an empty plan must never return success.
  sbe_plan.py <check-name> <dossier-dir>
      One check by name, advisory: the verdict line is the result and the exit
      code stays 0, exactly as sbe_gate.py answers the honesty meta-test. The
      full run keeps the src/brothersbe/cli.py convention instead (0 ok, 1 a
      control failed, 2 usage) because `bin/sbe plan` is a control surface.

Honesty law inherited from the chassis: absent evidence is NO-DATA, never PASS.
Derivation reads the dossier's own structures and nothing else; the planner
never invents a task, and a gap the dossier never answers (a migration with no
rollback row, a contract change with no compatibility claim) is a refusal that
names the gap, not a blank to fill in.
"""
import hashlib
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import Check, run_guarded, answered, evidence_problem, one_line, say
# Reused, not reimplemented (loop 1 brief): sbe_design already owns the section
# and digest parsing idioms this tool needs, so the same definitions serve both.
from sbe_design import _sections, _section_body, _sha256_of
# The tri-state changes_contract vocabulary (no/additive/breaking, plus the
# legacy yes/no forms) is owned by sbe_intake.py's read_answers, the SAME
# reader sbe_design.py's check_artifacts already reuses rather than
# re-parsing. This file used to read the field through a local `_yes()` that
# collapsed "additive" into the same False as "no", so a dossier recording
# additive lost that word: plan_compatibility's NO-DATA line said "intake
# answers.changes_contract is no" about an intake that said additive. Reusing
# read_answers means this file and sbe_design.py cannot learn the vocabulary
# two different ways.
from sbe_intake import read_answers, CONTRACT

PLAN_FILE = "08-plan.json"
INTAKE = "00-intake.json"
ADR = "03-adr.md"
DATA_MODEL = "05-data-model.md"
VERIFICATION = "07-verification.md"
PLAN_SCHEMA_VERSION = "1.0"

#: A backticked token is a repo path when it has no whitespace and either
#: carries a slash or ends in a known code extension (spec, Derivation rules).
_CODE_EXTS = (".py", ".sql", ".yaml", ".yml", ".json", ".md", ".ts", ".js",
              ".go", ".java", ".rb", ".kt", ".sh", ".swift", ".rs", ".proto",
              ".tf", ".toml", ".cfg", ".ini")

#: The migration-shaped path rule. The reference detectors live in
#: src/brothersbe/impact.py (the "db-migration" entry); the pattern is mirrored
#: here rather than imported because tools/ stays importable with nothing but
#: tools/ on the path, and a drift between the two is a finding for the
#: convergence loop, not a silent re-derivation.
_MIGRATION_PATH_RE = re.compile(r"(^|/)(migrations?|alembic|flyway|liquibase|db/migrate)/",
                                re.IGNORECASE)

_TASK_ID_RE = re.compile(r"T\d+\Z")
_HTML_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
_DECISION_PAT = r"^#+\s*decision\b"
_CONSEQUENCES_PAT = r"^#+\s*consequence"
_PHYSICAL_PAT = r"^#+\s*physical\b"
_COMPAT_WORDS = ("compat", "contract", "consumer")


def _text_answer(value):
    """The answered string this field records, or None. Strings only: a number
    or a list where prose belongs records no prose."""
    v = answered(value)
    if isinstance(v, str):
        return v
    return None


def _strip_html_comments(text):
    """Markdown with its HTML comments removed, so a section whose whole body is
    commented out reads as the empty section it renders as."""
    return _HTML_COMMENT_RE.sub(" ", text)


def _read_text(root, name):
    """{"text": str or None, "problem": str, "absent": bool} for one artifact.

    Absence is an absence (the caller's NO-DATA); a file that exists and cannot
    be opened or decoded is a broken claim and comes back named.
    """
    path = os.path.join(root, name)
    problem = evidence_problem(path)
    if problem:
        return {"text": None, "problem": "%s is %s" % (name, problem), "absent": False}
    if not os.path.lexists(path):
        return {"text": None, "problem": "", "absent": True}
    try:
        with io.open(path, encoding="utf-8") as fh:
            body = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        return {"text": None, "absent": False,
                "problem": "%s exists but cannot be read as text (%s)" % (name, type(e).__name__)}
    return {"text": body, "problem": "", "absent": False}


def _read_json(root, name):
    """{"data": dict or None, "problem": str, "absent": bool} for one JSON file."""
    raw = _read_text(root, name)
    if raw["problem"] or raw["absent"]:
        return {"data": None, "problem": raw["problem"], "absent": raw["absent"]}
    try:
        obj = json.loads(raw["text"])
    except ValueError as e:
        return {"data": None, "absent": False,
                "problem": "%s is not readable as JSON (%s)" % (name, type(e).__name__)}
    if not isinstance(obj, dict):
        return {"data": None, "absent": False,
                "problem": "%s holds top-level JSON of type %s, not an object"
                           % (name, type(obj).__name__)}
    return {"data": obj, "problem": "", "absent": False}


def _load_plan(root):
    """{"plan": dict or None, "tasks": list, "problem": str, "nodata": str}.

    One loader so every check refuses the same broken shapes the same way: an
    absent plan is NO-DATA naming the --write remedy, a plan that exists and
    cannot be read (or whose tasks are not a list of objects each carrying a
    usable T-numbered id) is a broken claim, FAIL. The id rule is load-bearing:
    every FAIL sentence downstream names tasks by id, so a task no sentence
    could name is refused here, once.
    """
    got = _read_json(root, PLAN_FILE)
    if got["absent"]:
        return {"plan": None, "tasks": [],
                "problem": "",
                "nodata": "no %s recorded here; `sbe plan <dossier> --write` derives one "
                          "from the dossier" % PLAN_FILE}
    if got["problem"]:
        return {"plan": None, "tasks": [], "problem": got["problem"], "nodata": ""}
    plan = got["data"]
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        return {"plan": plan, "tasks": [], "nodata": "",
                "problem": "%s records no tasks list (got %s); a plan without one is a "
                           "broken claim, not an absence"
                           % (PLAN_FILE, type(tasks).__name__)}
    seen = set()
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            return {"plan": plan, "tasks": [], "nodata": "",
                    "problem": "task %d in %s is %s, not a task object"
                               % (i + 1, PLAN_FILE, type(task).__name__)}
        tid = _text_answer(task.get("id"))
        if tid is None or not _TASK_ID_RE.match(tid):
            return {"plan": plan, "tasks": [], "nodata": "",
                    "problem": "task %d in %s records no usable id (%r); ids are T-numbered "
                               "(T01, T02, ...) and every finding downstream names tasks by id"
                               % (i + 1, PLAN_FILE, task.get("id"))}
        if tid in seen:
            return {"plan": plan, "tasks": [], "nodata": "",
                    "problem": "task id %s appears more than once in %s, so nothing that "
                               "names it names one task" % (tid, PLAN_FILE)}
        seen.add(tid)
    return {"plan": plan, "tasks": tasks, "problem": "", "nodata": ""}


def _slug(title):
    """The heading's citation slug: lower case, runs of non-alphanumerics as one
    dash, exactly what `03-adr.md#decision` resolves against."""
    return re.sub(r"[^a-z0-9]+", "-", title.strip().lower()).strip("-")


def _section_answer(text, heading_pattern):
    """The answered body text under the matching heading, or None.

    HTML comments are stripped first, so a section whose body was commented out
    is the empty section it renders as, and a heading with a vacuous body (TODO)
    names nothing.
    """
    if text is None:
        return None
    body = _section_body(_strip_html_comments(text), heading_pattern)
    if body is None:
        return None
    return _text_answer(" ".join(line.strip() for line in body if line.strip()))


def _resolving_sections(text):
    """{slug: answered body or None} for every heading in a markdown artifact."""
    out = {}
    for title, body in _sections(_strip_html_comments(text or "")):
        s = _slug(title)
        if s and s not in out:
            out[s] = _text_answer(" ".join(line.strip() for line in body if line.strip()))
        elif s and out.get(s) is None:
            out[s] = _text_answer(" ".join(line.strip() for line in body if line.strip()))
    return out


def _rows_of(text):
    """The 07-verification table's data rows, in order, as dicts.

    Mirrors the table idiom in sbe_design (_table_alternatives): pipe rows,
    separator rows skipped by character set, rows with an empty first cell
    skipped, cells stripped. Row numbering downstream is the 1-based index into
    what this returns.
    """
    rows = []
    for line in _strip_html_comments(text or "").splitlines():
        r = line.strip()
        if not r.startswith("|"):
            continue
        if not set(r) - set("|-: "):
            continue
        # A backslash-escaped pipe is markdown's own way to put a literal |
        # inside a cell (external proof, estate A: a grep pattern with an
        # escaped pipe lost its command half into a phantom cell, silently).
        # Sentinel-swap before the split, restore after.
        sentinel = "\x00PIPE\x00"
        cells = [c.strip().replace(sentinel, "|")
                 for c in r.strip("|").replace("\\|", sentinel).split("|")]
        if not cells or not cells[0]:
            continue
        rows.append({"claim": cells[0],
                     "check": cells[1] if len(cells) > 1 else "",
                     "when": cells[2] if len(cells) > 2 else ""})
    if rows:
        rows = rows[1:]  # the first surviving pipe row is the header
    return rows


def _backticked_paths(text):
    """Repo paths named in backticks: no whitespace, a slash or a code
    extension. Order preserved, duplicates dropped."""
    out = []
    for tok in re.findall(r"`([^`]+)`", text or ""):
        tok = tok.strip()
        if not tok or any(ch.isspace() for ch in tok):
            continue
        if "/" in tok or tok.lower().endswith(_CODE_EXTS):
            if tok not in out:
                out.append(tok)
    return out


def _commands_of(cell):
    """Command-shaped backticked tokens in a 07 check cell: backticked, with
    embedded whitespace."""
    out = []
    for tok in re.findall(r"`([^`]+)`", cell or ""):
        tok = tok.strip()
        if tok and any(ch.isspace() for ch in tok):
            out.append(tok)
    return out


def _pathish_words(command):
    """The path-shaped words inside a command string."""
    out = []
    for word in (command or "").split():
        if "/" in word or word.lower().endswith(_CODE_EXTS):
            out.append(word)
    return out


def _migrationish(path):
    return _MIGRATION_PATH_RE.search((path or "").replace(os.sep, "/").lower()) is not None


def _contract_value(answers):
    """"no", "additive", or "breaking" (the P7 tri-state vocabulary, legacy
    y/n forms included), or None if unreadable or absent.

    Delegates to sbe_intake.read_answers, the one place this vocabulary is
    defined, instead of re-parsing it here (replaces the old `_yes()`, which
    collapsed "additive" into the same False as "no" and lost the word
    before either call site below could quote it). A one-key dict is passed
    on purpose: read_answers walks every QUESTIONS key and this call site
    only owns changes_contract, so the other four keys are left unanswered
    and their problems discarded; only the CONTRACT entry of the returned
    values is ever read.
    """
    if not isinstance(answers, dict):
        return None
    values, _problems = read_answers({CONTRACT: answers.get(CONTRACT)})
    return values.get(CONTRACT)


def _first_sentence(text):
    t = " ".join((text or "").split())
    m = re.search(r"^.*?\.(?=\s|$)", t)
    if m:
        return m.group(0)
    return t


def _answered_list(value):
    """The answered string entries of a list field, order kept. A non-list is
    an empty result; the caller says what that absence means."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        v = _text_answer(item)
        if v is not None:
            out.append(v)
    return out


def _reachable(adj, start, goal):
    """True when goal is reachable from start over dependsOn edges."""
    seen = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(adj.get(node, ()))
    return False


def _find_cycle(adj):
    """One dependency cycle as a list of ids (first repeated at the end), or []."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in adj}
    parent = {}
    for root_node in sorted(adj):
        if color[root_node] != WHITE:
            continue
        stack = [(root_node, iter(sorted(adj[root_node])))]
        color[root_node] = GREY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in color:
                    continue
                if color[nxt] == GREY:
                    cycle = [nxt, node]
                    cur = node
                    while cur != nxt:
                        cur = parent[cur]
                        cycle.append(cur)
                    cycle.reverse()
                    return cycle
                if color[nxt] == WHITE:
                    color[nxt] = GREY
                    parent[nxt] = node
                    stack.append((nxt, iter(sorted(adj[nxt]))))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
    return []


# ---------------------------------------------------------------------------
# Validation checks (the PLAN_CHECKS registry; always run, both modes)
# ---------------------------------------------------------------------------

def plan_nonempty(root):
    loaded = _load_plan(root)
    if loaded["problem"]:
        return "FAIL", loaded["problem"]
    if loaded["nodata"]:
        return "NO-DATA", loaded["nodata"]
    if not loaded["tasks"]:
        return "FAIL", ("%s records zero tasks. An empty plan should have been NO-DATA and "
                        "never written, so a written one is a broken claim" % PLAN_FILE)
    return "PASS", ("plan records %d task(s), each with a usable T-numbered id"
                    % len(loaded["tasks"]))


def plan_citations(root):
    loaded = _load_plan(root)
    if loaded["problem"]:
        return "FAIL", loaded["problem"]
    if loaded["nodata"]:
        return "NO-DATA", loaded["nodata"]
    tasks = loaded["tasks"]
    if not tasks:
        return "NO-DATA", "plan records zero tasks, so there is no citation to resolve"
    section_cache = {}
    rows_cache = {}
    cited = 0
    for task in tasks:
        tid = task["id"]
        sources = _answered_list(task.get("dossierSources"))
        if not sources:
            return "FAIL", ("task %s cites no dossier source, which is the planner-"
                            "inventing-work case: every task exists because the dossier "
                            "said so, or it does not exist" % tid)
        for src in sources:
            fname, _sep, frag = src.partition("#")
            fname = fname.strip()
            frag = frag.strip()
            if not fname or fname.startswith(("/", "..")) or ".." in fname.split("/"):
                return "FAIL", ("task %s cites %r, which does not name a file inside the "
                                "dossier" % (tid, src))
            artifact = _read_text(root, fname)
            if artifact["problem"]:
                return "FAIL", ("task %s cites %r but %s" % (tid, src, artifact["problem"]))
            if artifact["absent"]:
                return "FAIL", ("task %s cites %r but %s does not exist in the dossier"
                                % (tid, src, fname))
            if not frag:
                if _text_answer(_strip_html_comments(artifact["text"])) is None:
                    return "FAIL", ("task %s cites %r but %s records nothing"
                                    % (tid, src, fname))
                cited += 1
                continue
            row_match = re.match(r"row-(\d+)\Z", frag)
            if row_match:
                if fname not in rows_cache:
                    rows_cache[fname] = _rows_of(artifact["text"])
                n = int(row_match.group(1))
                if n < 1 or n > len(rows_cache[fname]):
                    return "FAIL", ("task %s cites %r but %s has %d data row(s), so row-%d "
                                    "does not resolve"
                                    % (tid, src, fname, len(rows_cache[fname]), n))
                cited += 1
                continue
            if fname not in section_cache:
                section_cache[fname] = _resolving_sections(artifact["text"])
            body = section_cache[fname].get(frag.lower())
            if frag.lower() not in section_cache[fname]:
                return "FAIL", ("task %s cites %r, and no heading in %s resolves the "
                                "section %r" % (tid, src, fname, frag))
            if body is None:
                return "FAIL", ("task %s cites %r but that section of %s records nothing; "
                                "citing an empty section is citing nothing" % (tid, src, fname))
            cited += 1
    return "PASS", ("%d task(s) cite %d dossier source(s) and every one resolves to "
                    "recorded content" % (len(tasks), cited))


def plan_ownership(root):
    loaded = _load_plan(root)
    if loaded["problem"]:
        return "FAIL", loaded["problem"]
    if loaded["nodata"]:
        return "NO-DATA", loaded["nodata"]
    tasks = loaded["tasks"]
    if not tasks:
        return "NO-DATA", "plan records zero tasks, so there is no ownership to examine"
    adj = {}
    owns_by_id = {}
    for task in tasks:
        tid = task["id"]
        role = _text_answer(task.get("role"))
        if role not in ("writer", "reviewer"):
            return "FAIL", ("task %s records role %r; a task is a writer or a reviewer, and "
                            "a role nobody can read makes ownership unenforceable"
                            % (tid, task.get("role")))
        owns_field = task.get("owns")
        owns = _answered_list(owns_field)
        if isinstance(owns_field, list) and len(owns) != len(owns_field):
            return "FAIL", ("task %s lists an owned path that records nothing; a blank "
                            "claim fences nothing" % tid)
        if role == "writer" and not owns:
            return "FAIL", ("writer task %s owns no path, so nothing fences what it will "
                            "write" % tid)
        if role == "reviewer" and owns:
            return "FAIL", ("reviewer task %s owns %s; reviewer tasks own nothing, always"
                            % (tid, ", ".join(owns)))
        owns_by_id[tid] = owns
        adj[tid] = _answered_list(task.get("dependsOn"))
    ids = list(owns_by_id)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            shared = sorted(set(owns_by_id[a]) & set(owns_by_id[b]))
            if not shared:
                continue
            if not (_reachable(adj, a, b) or _reachable(adj, b, a)):
                return "FAIL", ("path %s is owned by both %s and %s and the dependency "
                                "graph does not order them; two parallel writers on one "
                                "path is the collision the plan exists to prevent"
                                % (shared[0], a, b))
    return "PASS", ("%d task(s): every writer owns at least one recorded path, reviewers "
                    "own nothing, and every owned-path overlap is dependency-ordered"
                    % len(tasks))


def plan_acceptance(root):
    loaded = _load_plan(root)
    if loaded["problem"]:
        return "FAIL", loaded["problem"]
    if loaded["nodata"]:
        return "NO-DATA", loaded["nodata"]
    tasks = loaded["tasks"]
    if not tasks:
        return "NO-DATA", "plan records zero tasks, so there is no acceptance to examine"
    total = 0
    for task in tasks:
        crit = _answered_list(task.get("acceptance"))
        if not crit:
            return "FAIL", ("task %s carries no acceptance criterion, so nothing states "
                            "when it is done" % task["id"])
        total += len(crit)
    return "PASS", ("%d task(s) carry %d recorded acceptance criterion(s)"
                    % (len(tasks), total))


def plan_graph(root):
    loaded = _load_plan(root)
    if loaded["problem"]:
        return "FAIL", loaded["problem"]
    if loaded["nodata"]:
        return "NO-DATA", loaded["nodata"]
    tasks = loaded["tasks"]
    if not tasks:
        return "NO-DATA", "plan records zero tasks, so there is no graph to examine"
    ids = set(t["id"] for t in tasks)
    adj = {t["id"]: [] for t in tasks}
    edges = 0
    for task in tasks:
        deps_field = task.get("dependsOn")
        deps = _answered_list(deps_field)
        if isinstance(deps_field, list) and len(deps) != len(deps_field):
            return "FAIL", ("task %s lists a dependency that records nothing; an edge to "
                            "nothing orders nothing" % task["id"])
        for dep in deps:
            if dep not in ids:
                return "FAIL", ("task %s dependsOn %r, which is no task id in this plan"
                                % (task["id"], dep))
            adj[task["id"]].append(dep)
            edges += 1
    if edges == 0:
        return "NO-DATA", ("plan records zero dependency edges, so there is no ordering "
                           "to examine")
    cycle = _find_cycle(adj)
    if cycle:
        return "FAIL", ("dependency cycle: %s; a cycle orders nothing and no task in it "
                        "can start" % " -> ".join(cycle))
    return "PASS", ("%d dependency edge(s) all resolve to task ids and the graph is "
                    "acyclic" % edges)


def plan_compatibility(root):
    loaded = _load_plan(root)
    if loaded["problem"]:
        return "FAIL", loaded["problem"]
    intake = _read_json(root, INTAKE)
    if intake["problem"]:
        return "FAIL", intake["problem"]
    if intake["absent"]:
        return "NO-DATA", ("no %s here, so whether this change alters a contract is "
                           "unrecorded and the compatibility rule cannot be applied" % INTAKE)
    answers = intake["data"].get("answers")
    declared = _contract_value(answers)
    if declared is None:
        return "NO-DATA", ("%s records no readable answers.changes_contract, so the "
                           "compatibility rule cannot be applied" % INTAKE)
    if declared == "no":
        return "NO-DATA", ("intake answers.changes_contract is no; no contract change is "
                           "declared, so there is nothing for this check to examine")
    # DECISION (2026-08-15, defect 3 repair): an ADDITIVE contract change is
    # still a contract change, so it owes a compatibility task too, the same
    # mechanism (a task naming compat/contract/consumer) that a BREAKING
    # change already owes; the message below just says which kind, so the
    # obligation is never misquoted as "no" again. REJECTED: leaving
    # "additive" at NO-DATA (the old behaviour) demands nothing of an
    # additive change's dossier, which is under-verification in the unsafe
    # direction the same way defect 2's tier-skip was: a contract change
    # that is deliberately declared lighter than breaking still owes a
    # record of who stays compatible, not silence.
    if loaded["nodata"]:
        return "NO-DATA", loaded["nodata"]
    hits = []
    for task in loaded["tasks"]:
        blob = " ".join([_text_answer(task.get("title")) or ""]
                        + _answered_list(task.get("acceptance"))).lower()
        if any(word in blob for word in _COMPAT_WORDS):
            hits.append(task["id"])
    if not hits:
        return "FAIL", ("intake declares a %s contract change and no task's title or "
                        "acceptance mentions compatibility, contract, or consumer. An API "
                        "change whose dossier never claims compatibility is a missing "
                        "requirement; the planner never invents the task" % declared)
    return "PASS", ("intake declares a %s contract change and task(s) %s answer it by "
                    "naming compatibility, contract, or consumer" % (declared, ", ".join(hits)))


def plan_migration(root):
    loaded = _load_plan(root)
    if loaded["problem"]:
        return "FAIL", loaded["problem"]
    dm = _read_text(root, DATA_MODEL)
    if dm["problem"]:
        return "FAIL", dm["problem"]
    physical = _section_answer(dm["text"], _PHYSICAL_PAT) if not dm["absent"] else None
    # Only MIGRATION-SHAPED paths declare a migration (external proof, estate
    # A: a Physical section that backticked a source file in prose explicitly
    # saying "no migration needed" was forced to invent reverse and
    # reconciliation rows for a migration that does not exist; the derivation
    # side already gated on _migrationish and this validation side did not).
    paths = [pth for pth in _backticked_paths(physical or "") if _migrationish(pth)]
    if not paths:
        return "NO-DATA", ("no %s Physical section naming a migration-shaped path, so no "
                           "migration is declared and there is no triplet to demand"
                           % DATA_MODEL)
    if loaded["nodata"]:
        return "NO-DATA", loaded["nodata"]
    tasks = loaded["tasks"]
    if not tasks:
        return "FAIL", ("%s declares a migration (%s) and the plan records zero tasks, so "
                        "no forward, reverse or reconciliation task exists"
                        % (DATA_MODEL, ", ".join(paths)))
    forward = None
    for task in tasks:
        if set(paths) & set(_answered_list(task.get("owns"))):
            forward = task
            break
    if forward is None:
        return "FAIL", ("%s declares a migration and no task owns %s, so the forward "
                        "migration has no owner" % (DATA_MODEL, ", ".join(paths)))
    if _text_answer(forward.get("role")) != "writer":
        return "FAIL", ("the forward migration task %s is not a writer, so nobody is "
                        "declared to write %s" % (forward["id"], ", ".join(paths)))
    reverse = None
    reconciliation = None
    for task in tasks:
        if task is forward:
            continue
        blob = " ".join(_answered_list(task.get("acceptance"))).lower()
        if reverse is None and ("reverse" in blob or "rollback" in blob):
            reverse = task
        elif reconciliation is None and "reconcil" in blob:
            reconciliation = task
    if reverse is None:
        return "FAIL", ("the plan has no reverse or rollback task for the migration "
                        "(%s); a migration nobody states how to undo is a missing "
                        "requirement" % ", ".join(paths))
    if _text_answer(reverse.get("role")) != "writer":
        return "FAIL", "the reverse task %s is not a writer" % reverse["id"]
    if forward["id"] not in _answered_list(reverse.get("dependsOn")):
        return "FAIL", ("the reverse task %s does not depend on the forward migration "
                        "task %s, so nothing orders undo after do"
                        % (reverse["id"], forward["id"]))
    if reconciliation is None:
        return "FAIL", ("the plan has no reconciliation task for the migration (%s); a "
                        "migration nobody states how to reconcile is a missing "
                        "requirement" % ", ".join(paths))
    if _text_answer(reconciliation.get("role")) != "writer":
        return "FAIL", "the reconciliation task %s is not a writer" % reconciliation["id"]
    recon_deps = _answered_list(reconciliation.get("dependsOn"))
    if forward["id"] not in recon_deps or reverse["id"] not in recon_deps:
        return "FAIL", ("the reconciliation task %s must depend on both the forward task "
                        "%s and the reverse task %s, and does not"
                        % (reconciliation["id"], forward["id"], reverse["id"]))
    return "PASS", ("migration triplet present for %s: forward %s, reverse %s, "
                    "reconciliation %s, all writers, dependency-ordered"
                    % (", ".join(paths), forward["id"], reverse["id"], reconciliation["id"]))


def plan_calculation(root):
    loaded = _load_plan(root)
    if loaded["problem"]:
        return "FAIL", loaded["problem"]
    if loaded["nodata"]:
        return "NO-DATA", loaded["nodata"]
    tasks = loaded["tasks"]
    if not tasks:
        return "NO-DATA", ("plan records zero tasks, so there is no decision-bearing "
                           "criterion to examine")
    bearing = 0
    for task in tasks:
        # Rule 5 is about 07-verification rows; the plan's own citation is the
        # anchor, so a task that does not cite a row (the ADR task, a migration
        # task whose acceptance merely names a numbered file) is out of scope.
        if not any(re.match(r"07-verification\.md#row-\d+\Z", s)
                   for s in _answered_list(task.get("dossierSources"))):
            continue
        for claim in _answered_list(task.get("acceptance")):
            if not any(ch.isdigit() for ch in claim):
                continue
            bearing += 1
            commands = []
            for cmd in _answered_list(task.get("verificationCommands")):
                if cmd not in commands:
                    commands.append(cmd)
            second_row = False
            for other in tasks:
                if other is task:
                    continue
                if claim in _answered_list(other.get("acceptance")):
                    second_row = True
                    break
            if len(commands) < 2 and not second_row:
                return "FAIL", ("task %s carries the decision-bearing criterion %r with "
                                "%d check command(s) and no second task derives the same "
                                "claim; a figure that reaches a decision needs an "
                                "independent second derivation"
                                % (task["id"], claim, len(commands)))
    if bearing == 0:
        return "NO-DATA", ("no acceptance criterion carries a digit, so nothing here is "
                           "decision-bearing and there is no derivation to demand")
    return "PASS", ("%d decision-bearing criterion(s), each with two commands or a "
                    "second task deriving the same claim" % bearing)


def plan_freshness(root):
    loaded = _load_plan(root)
    if loaded["problem"]:
        return "FAIL", loaded["problem"]
    if loaded["nodata"]:
        return "NO-DATA", loaded["nodata"]
    if not loaded["tasks"]:
        return "NO-DATA", ("plan records zero tasks; whether an empty plan is stale is "
                           "the nonempty check's finding, not a freshness one")
    plan = loaded["plan"]
    digests = plan.get("dossierDigests")
    if not isinstance(digests, dict) or not digests:
        return "NO-DATA", ("plan records no dossierDigests, so staleness against the "
                           "dossier on disk cannot be measured")
    matched = 0
    for fname in sorted(digests):
        name = _text_answer(fname)
        if name is None or name.startswith(("/", "..")) or ".." in name.split("/"):
            return "FAIL", ("dossierDigests names %r, which is not a file inside the "
                            "dossier" % fname)
        want = _text_answer(digests[fname])
        if want is None:
            return "FAIL", ("dossierDigests records no digest for %s, so its freshness "
                            "was asserted and never measured" % name)
        path = os.path.join(root, name)
        problem = evidence_problem(path)
        if problem:
            return "FAIL", "%s is %s, so its recorded digest proves nothing" % (name, problem)
        if not os.path.lexists(path):
            return "FAIL", ("%s carries a digest in the plan and does not exist on disk; "
                            "the dossier changed after planning and the plan is stale" % name)
        have = _sha256_of(path)
        if have is None:
            return "FAIL", "%s exists but could not be read for hashing" % name
        if have != want.lower():
            return "FAIL", ("%s changed after planning (sha256 mismatch); the plan is "
                            "stale and must be re-derived" % name)
        matched += 1
    intake = _read_json(root, INTAKE)
    if intake["problem"]:
        return "FAIL", intake["problem"]
    binding = intake["data"].get("binding") if not intake["absent"] else None
    head = _text_answer(binding.get("head")) if isinstance(binding, dict) else None
    base = _text_answer(plan.get("baseCommit"))
    if head is not None:
        if base is None:
            return "FAIL", ("intake binding.head records %s and the plan records no "
                            "baseCommit; a bound dossier planned against nothing is "
                            "stale by construction" % head[:12])
        if base != head:
            return "FAIL", ("plan baseCommit %s does not match intake binding.head %s; "
                            "the dossier was re-bound after planning"
                            % (base[:12], head[:12]))
        return "PASS", ("%d dossier file(s) match their recorded digests and baseCommit "
                        "matches intake binding.head" % matched)
    return "NO-DATA", ("%d dossier file(s) match their recorded digests, but no intake "
                       "binding.head exists to corroborate baseCommit, so the commit "
                       "half of freshness is unmeasured" % matched)


_FX_CIT_ADR = "## Decision\nWe cache widget lookups in `src/widgets/cache.py`.\n"
_FX_MIG_DM = ("## Physical\nThe migration adds `migrations/0001_widget_cache.sql` for "
              "the cache table.\n")
_FX_HEAD = "0123456789abcdef0123456789abcdef01234567"
_FX_ADR_SHA = hashlib.sha256(_FX_CIT_ADR.encode("utf-8")).hexdigest()

#: The registry is the contract, exactly as sbe_gate.GATES and sbe_design.CHECKS:
#: each check declares the evidence it opens, its empty verdict, and a worked
#: example that SHOULD pass, and evals/test_no_data_class.py discovers this dict
#: and holds every entry to the no-PASS-over-nothing law. The fixtures are
#: minimal on purpose: every field in them is a field the PASS sentence asserts
#: over, so the honesty test can hollow any one of them and demand the PASS
#: goes away.
PLAN_CHECKS = {
    "nonempty": Check(
        plan_nonempty, reads=(PLAN_FILE,), kind="json", severity="gate", item_key="tasks",
        empty_expect="FAIL",
        empty_note="a written plan that records zero tasks claims derivable work exists "
                   "and names none; the empty case should have been NO-DATA and never "
                   "written, so an empty recorded plan is a broken claim, not an absence",
        full_fixture={"files": {PLAN_FILE: {"tasks": [{"id": "T01"}]}}}),
    "citations": Check(
        plan_citations, reads=(PLAN_FILE, ADR), kind="json", severity="gate",
        item_key="tasks",
        full_fixture={"files": {
            PLAN_FILE: {"tasks": [{"id": "T01", "dossierSources": ["03-adr.md#decision"]}]},
            ADR: _FX_CIT_ADR}}),
    "ownership": Check(
        plan_ownership, reads=(PLAN_FILE,), kind="json", severity="gate", item_key="tasks",
        full_fixture={"files": {PLAN_FILE: {"tasks": [
            {"id": "T01", "role": "writer", "owns": ["src/widgets/cache.py"]},
            {"id": "T02", "role": "reviewer", "owns": []}]}}}),
    "acceptance": Check(
        plan_acceptance, reads=(PLAN_FILE,), kind="json", severity="gate", item_key="tasks",
        full_fixture={"files": {PLAN_FILE: {"tasks": [
            {"id": "T01", "acceptance": ["The cache responds within its latency budget"]}]}}}),
    "graph": Check(
        plan_graph, reads=(PLAN_FILE,), kind="json", severity="gate", item_key="tasks",
        full_fixture={"files": {PLAN_FILE: {"tasks": [
            {"id": "T01", "dependsOn": []},
            {"id": "T02", "dependsOn": ["T01"]}]}}}),
    "compatibility": Check(
        plan_compatibility, reads=(PLAN_FILE, INTAKE), kind="json", severity="gate",
        item_key="tasks",
        full_fixture={"files": {
            PLAN_FILE: {"tasks": [
                {"id": "T01",
                 "title": "Keep the widget contract compatible for every consumer"}]},
            INTAKE: {"answers": {"changes_contract": "y"}}}}),
    "migration": Check(
        plan_migration, reads=(PLAN_FILE, DATA_MODEL), kind="json", severity="gate",
        item_key="tasks",
        full_fixture={"files": {
            PLAN_FILE: {"tasks": [
                {"id": "T01", "role": "writer",
                 "owns": ["migrations/0001_widget_cache.sql"]},
                {"id": "T02", "role": "writer", "dependsOn": ["T01"],
                 "acceptance": ["The migration rollback restores prior state"]},
                {"id": "T03", "role": "writer", "dependsOn": ["T01", "T02"],
                 "acceptance": ["Reconciliation confirms row counts match"]}]},
            DATA_MODEL: _FX_MIG_DM}}),
    "calculation": Check(
        plan_calculation, reads=(PLAN_FILE,), kind="json", severity="gate", item_key="tasks",
        full_fixture={"files": {PLAN_FILE: {"tasks": [
            {"id": "T01", "acceptance": ["Cache hit rate stays above 95 percent"],
             "dossierSources": ["07-verification.md#row-1"],
             "verificationCommands": ["pytest tests/test_cache_hitrate.py",
                                      "python3 tools/report_hitrate.py"]}]}}}),
    "freshness": Check(
        plan_freshness, reads=(PLAN_FILE, INTAKE), kind="json", severity="gate",
        item_key="tasks",
        full_fixture={"files": {
            PLAN_FILE: {"baseCommit": _FX_HEAD,
                        "dossierDigests": {ADR: _FX_ADR_SHA},
                        "tasks": [{"id": "T01"}]},
            INTAKE: {"binding": {"head": _FX_HEAD}},
            ADR: _FX_CIT_ADR}}),
}


# ---------------------------------------------------------------------------
# Derivation (--write)
# ---------------------------------------------------------------------------

def build_plan(dossier_dir, repo_root):
    """Derive the plan dict from the dossier, or refuse.

    Returns {"plan": dict or None, "problems": [str], "nodata": str,
             "unexecutable": [row numbers], "base_note": str}. Exactly one of
    plan, problems, nodata carries the outcome. Pure function of the dossier
    bytes: same dossier, same plan, no timestamps, no absolute paths.
    """
    out = {"plan": None, "problems": [], "nodata": "", "unexecutable": [], "base_note": ""}
    root = dossier_dir

    intake = _read_json(root, INTAKE)
    if intake["problem"]:
        out["problems"].append(intake["problem"])
    answers = (intake["data"] or {}).get("answers")
    contract = _contract_value(answers)
    binding = (intake["data"] or {}).get("binding")
    head = _text_answer(binding.get("head")) if isinstance(binding, dict) else None

    adr = _read_text(root, ADR)
    if adr["problem"]:
        out["problems"].append(adr["problem"])
    dm = _read_text(root, DATA_MODEL)
    if dm["problem"]:
        out["problems"].append(dm["problem"])
    ver = _read_text(root, VERIFICATION)
    if ver["problem"]:
        out["problems"].append(ver["problem"])
    if out["problems"]:
        return out

    decision = _section_answer(adr["text"], _DECISION_PAT)
    consequences = _section_answer(adr["text"], _CONSEQUENCES_PAT)
    physical = _section_answer(dm["text"], _PHYSICAL_PAT)
    # Only migration-shaped paths declare a migration; prose that backticks a
    # source file while saying "no migration needed" must not be forced to
    # invent reverse and reconciliation rows (external proof, estate A).
    physical_paths = [pth for pth in _backticked_paths(physical or "")
                      if _migrationish(pth)]
    rows = _rows_of(ver["text"]) if ver["text"] is not None else []

    protos = []
    if decision is not None:
        protos.append({
            "kind": "decision",
            "title": _first_sentence(decision),
            "role": "writer",
            "owns": sorted(set(_backticked_paths(decision) + _backticked_paths(consequences or ""))),
            "acceptance": [_first_sentence(decision)],
            "verificationCommands": [],
            "dossierSources": ["03-adr.md#decision"],
        })
    if physical_paths:
        reverse_rows = [(i + 1, r) for i, r in enumerate(rows)
                        if re.search(r"reverse|rollback", r["claim"], re.I)]
        recon_rows = [(i + 1, r) for i, r in enumerate(rows)
                      if re.search(r"reconcil", r["claim"], re.I)]
        if not reverse_rows:
            out["problems"].append(
                "the Physical section of %s names %s and no %s row states how the "
                "migration is reversed or rolled back. A migration whose dossier never "
                "states how it is undone is a missing requirement, not a gap to invent "
                "an answer for" % (DATA_MODEL, ", ".join(physical_paths), VERIFICATION))
        if not recon_rows:
            out["problems"].append(
                "the Physical section of %s names %s and no %s row states how the "
                "migration is reconciled. A migration whose dossier never states its "
                "reconciliation is a missing requirement, not a gap to invent an answer "
                "for" % (DATA_MODEL, ", ".join(physical_paths), VERIFICATION))
        if not out["problems"]:
            protos.append({
                "kind": "forward",
                "title": "Apply the forward migration: %s" % ", ".join(physical_paths),
                "role": "writer",
                "owns": list(physical_paths),
                "acceptance": [_first_sentence(physical)],
                "verificationCommands": [],
                "dossierSources": ["05-data-model.md#physical"],
            })
            protos.append({
                "kind": "reverse",
                "title": "Provide the reverse migration: %s" % ", ".join(physical_paths),
                "role": "writer",
                "owns": list(physical_paths),
                "acceptance": [r["claim"] for _n, r in reverse_rows],
                "verificationCommands": [],
                "dossierSources": (["05-data-model.md#physical"]
                                   + ["07-verification.md#row-%d" % n
                                      for n, _r in reverse_rows]),
            })
            protos.append({
                "kind": "reconciliation",
                "title": "Reconcile the migration: %s" % ", ".join(physical_paths),
                "role": "writer",
                "owns": list(physical_paths),
                "acceptance": [r["claim"] for _n, r in recon_rows],
                "verificationCommands": [],
                "dossierSources": (["05-data-model.md#physical"]
                                   + ["07-verification.md#row-%d" % n
                                      for n, _r in recon_rows]),
            })
    for i, row in enumerate(rows):
        commands = _commands_of(row["check"])
        if not commands:
            out["unexecutable"].append(i + 1)
        protos.append({
            "kind": "row",
            "title": row["claim"],
            "role": "reviewer",
            "owns": [],
            "acceptance": [row["claim"]],
            "verificationCommands": commands,
            "dossierSources": ["07-verification.md#row-%d" % (i + 1)],
        })

    if out["problems"]:
        return out
    if not protos:
        out["nodata"] = ("nothing derivable from this dossier: no ADR decision, no "
                         "physical data model, no verification rows. Nothing is written, "
                         "because an empty plan must never read as success")
        return out

    # DECISION (defect 3 repair, same as plan_compatibility above): additive
    # is included beside breaking, not only breaking, because an additive
    # contract change is still a contract change the dossier owes a
    # compatibility task for. See plan_compatibility's docstring comment for
    # the rejected alternative (leaving additive at NO-DATA).
    if contract in ("additive", "breaking"):
        answered_by = []
        for proto in protos:
            blob = " ".join([proto["title"]] + proto["acceptance"]).lower()
            if any(word in blob for word in _COMPAT_WORDS):
                answered_by.append(proto["kind"])
        if not answered_by:
            out["problems"].append(
                "intake answers.changes_contract is %s and nothing in the dossier "
                "supports a task mentioning compatibility, contract, or consumer. An API "
                "change whose dossier never claims compatibility is a missing "
                "requirement; the planner never invents the task" % contract)

    for i, row in enumerate(rows):
        claim = row["claim"]
        if not any(ch.isdigit() for ch in claim):
            continue
        commands = _commands_of(row["check"])
        second = any(r["claim"] == claim for j, r in enumerate(rows) if j != i)
        if len(sorted(set(commands))) < 2 and not second:
            out["problems"].append(
                "07-verification.md row-%d is decision-bearing (its claim %r carries a "
                "digit) and has %d check command(s) with no second row citing the same "
                "claim; a figure that reaches a decision needs an independent second "
                "derivation, and the dossier does not state one"
                % (i + 1, claim, len(commands)))
    if out["problems"]:
        return out

    rel = os.path.relpath(os.path.abspath(dossier_dir), os.path.abspath(repo_root))
    rel = rel.replace(os.sep, "/")

    tasks = []
    kind_ids = {}
    for n, proto in enumerate(protos, start=1):
        tid = "T%02d" % n
        kind_ids.setdefault(proto["kind"], tid)
        reviewers = set(["evidence"])
        if proto["kind"] == "decision":
            reviewers.add("backend")
        if proto["kind"] in ("forward", "reverse", "reconciliation"):
            reviewers.add("data")
        mentioned = list(proto["owns"])
        for cmd in proto["verificationCommands"]:
            mentioned.extend(_pathish_words(cmd))
        if any(_migrationish(p) for p in mentioned):
            reviewers.add("migration")
        depends = []
        if proto["kind"] == "reverse":
            depends = [kind_ids["forward"]]
        elif proto["kind"] == "reconciliation":
            depends = sorted([kind_ids["forward"], kind_ids["reverse"]])
        tasks.append({
            "id": tid,
            "title": proto["title"],
            "role": proto["role"],
            "dependsOn": depends,
            "owns": proto["owns"],
            "readOnly": ["%s/**" % rel],
            "acceptance": proto["acceptance"],
            "verificationCommands": proto["verificationCommands"],
            "requiredEvidence": ["command-receipt"] if proto["verificationCommands"] else [],
            "requiredReviewers": sorted(reviewers),
            "dossierSources": proto["dossierSources"],
        })

    # Rule 8, overlap serialization: two writer tasks whose owns overlap get an
    # edge from the later id to the earlier, so no two parallel tasks share a
    # path by construction.
    writers = [t for t in tasks if t["role"] == "writer"]
    for i, earlier in enumerate(writers):
        for later in writers[i + 1:]:
            if set(earlier["owns"]) & set(later["owns"]):
                if earlier["id"] not in later["dependsOn"]:
                    later["dependsOn"] = sorted(later["dependsOn"] + [earlier["id"]])

    digests = {}
    for name in (INTAKE, ADR, DATA_MODEL, VERIFICATION):
        path = os.path.join(root, name)
        if not os.path.lexists(path):
            continue
        digest = _sha256_of(path)
        if digest is None:
            out["problems"].append("%s exists but could not be read for hashing, so the "
                                   "plan cannot record what it derived from" % name)
            return out
        digests[name] = digest

    if head is None:
        out["base_note"] = ("baseCommit is null: no intake binding.head is recorded, so "
                            "the base this plan was derived against is unmeasured")

    out["plan"] = {
        "schemaVersion": PLAN_SCHEMA_VERSION,
        "dossier": rel,
        "baseCommit": head,
        "dossierDigests": digests,
        "tasks": tasks,
    }
    return out


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

PLAN_USAGE = (
    "usage: sbe_plan.py <dossier-dir> [check-name] [--write] [--json] [--strict] "
    "[--cwd <repo>]\n"
    "  reads: 08-plan.json in the dossier directory, plus the dossier artifacts\n"
    "    each check cites.\n"
    "  writes: nothing without --write; with it, derives and writes 08-plan.json\n"
    "    before validating what was written.\n"
    "  checks (pass one name for a single advisory check): %s\n"
    "  flags:\n"
    "    --write           derive the plan from the dossier, then validate it\n"
    "    --json            machine-readable output\n"
    "    --strict          make FAIL block the exit code\n"
    "    --cwd <repo>      the repository the plan is measured against\n"
    "    -h, --help        print this and exit 0"
)


def main():
    argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        # Help is not an error. This used to fall through to the unknown-flag
        # refusal, so an explicit help request printed usage and exited 2.
        for usage_line in (PLAN_USAGE % ", ".join(PLAN_CHECKS)).splitlines():
            say(usage_line)
        return 0
    strict = "--strict" in argv
    write = "--write" in argv
    as_json = "--json" in argv
    cwd = "."
    positional = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--cwd":
            if i + 1 >= len(argv):
                say("sbe_plan: --cwd needs a repository path after it")
                return 2
            cwd = argv[i + 1]
            i += 2
            continue
        if arg in ("--strict", "--write", "--json"):
            i += 1
            continue
        if arg.startswith("-"):
            say("sbe_plan: unknown flag %r (known: --write, --json, --strict, --cwd)" % arg)
            return 2
        positional.append(arg)
        i += 1

    which = list(PLAN_CHECKS)
    selected = False
    root = None
    for arg in positional:
        if arg in PLAN_CHECKS and os.path.isdir(arg):
            say("sbe_plan: %r is both a check name and a directory here; pass ./%s for "
                "the directory" % (arg, arg))
            return 2
        if arg in PLAN_CHECKS:
            which = [arg]
            selected = True
        elif os.path.isdir(arg):
            if root is not None:
                say("sbe_plan: one dossier directory at a time, got %r and %r" % (root, arg))
                return 2
            root = arg
        else:
            say("sbe_plan: %r is neither a check name (%s) nor a directory"
                % (arg, ", ".join(PLAN_CHECKS)))
            return 2
    if root is None:
        say("sbe_plan: a dossier directory is required. Usage: sbe_plan.py <dossier-dir> "
            "[--write] [--json] [--strict] [--cwd <repo>]")
        return 2
    if write and selected:
        say("sbe_plan: --write derives the whole plan; it cannot be combined with a "
            "single check name")
        return 2
    repo = os.path.abspath(cwd)

    print("BROTHERSBE PLAN  (verdicts are PASS, FAIL, NO-DATA; absent evidence is "
          "NO-DATA and never a pass; an empty plan never exits 0)")

    if write:
        derived = build_plan(root, repo)
        if derived["problems"]:
            for problem in derived["problems"]:
                say("  %-14s %-8s %s" % ("derive", "FAIL", one_line(problem)))
            say("sbe_plan: derivation refused, nothing written, exit 1")
            return 1
        if derived["nodata"]:
            say("  %-14s %-8s %s" % ("derive", "NO-DATA", one_line(derived["nodata"])))
            say("sbe_plan: NO-DATA, an empty plan must never return success, exit 1")
            return 1
        plan_path = os.path.join(root, PLAN_FILE)
        try:
            with io.open(plan_path, "w", encoding="utf-8") as fh:
                json.dump(derived["plan"], fh, indent=2, sort_keys=True)
                fh.write("\n")
        except OSError as e:
            say("  %-14s %-8s %s" % ("derive", "FAIL", one_line(
                "%s could not be written (%s)" % (PLAN_FILE, type(e).__name__))))
            return 1
        say("  %-14s %-8s %s" % ("derive", "-", one_line(
            "wrote %s with %d task(s) derived from the dossier"
            % (PLAN_FILE, len(derived["plan"]["tasks"])))))
        say("  %-14s %-8s %s" % ("rows", "-", one_line(
            "%d verification row(s) hold no executable command%s; acceptance without "
            "commands is legal for non-executable criteria"
            % (len(derived["unexecutable"]),
               " (row(s) %s)" % ", ".join(str(n) for n in derived["unexecutable"])
               if derived["unexecutable"] else ""))))
        if derived["base_note"]:
            say("  %-14s %-8s %s" % ("base", "-", one_line(derived["base_note"])))

    fails = 0
    nodata = 0
    results = {}
    for name in which:
        verdict, evidence = run_guarded(name, PLAN_CHECKS[name], root)
        results[name] = {"verdict": verdict, "evidence": evidence}
        if verdict == "FAIL":
            fails += 1
        elif verdict == "NO-DATA":
            nodata += 1
        say("  %-14s %-8s %s [severity: %s]"
            % (name, verdict, one_line(evidence), PLAN_CHECKS[name].severity))
    if as_json:
        say(json.dumps({"checks": results, "fails": fails, "noData": nodata},
                       sort_keys=True))
    say("sbe_plan: %d FAIL, %d NO-DATA, %d PASS across %d check(s)"
        % (fails, nodata, len(which) - fails - nodata, len(which)))
    if selected:
        # Single-check mode is advisory, exactly as sbe_gate.py without
        # --strict: the verdict line above IS the result, and the honesty
        # meta-test reads it rather than the exit code.
        return 0
    if fails:
        return 1
    if strict and nodata:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # sbe: a broken planner must not read as a clean one
        say("sbe_plan: error %r" % (e,))
        sys.exit(1)
