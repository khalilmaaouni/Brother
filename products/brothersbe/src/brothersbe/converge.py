"""Does the code between two commits still match the approved design.

Spec of record: docs/specs/2026-07-30-sbe-converge.md. Five dimensions
(SCOPE, CONTRACTS, DATA, ARCHITECTURE, VERIFICATION), each grounded in a
deterministic fact: a file, an operation, an entity, a receipt, a sha. No
LLM anywhere. There is no force flag and none may be added: the only path
from divergence to PASS is amending the dossier, regenerating the plan, and
regenerating the evidence, which the fixtures walk end to end.

The report written to <dossier>/09-convergence.json carries no timestamps,
so two runs over the same tree are byte-identical.
"""
import io
import json
import os
import re
import shlex

from ._toolspath import mount
mount()
# The dossier-path extraction converge compares scope against is the SAME
# parser `sbe plan` derives with (tools/sbe_plan.py): two parsers would drift
# into two definitions of "a path the dossier names".
import sbe_plan  # noqa: E402

from . import evidence as evidence_mod
from . import impact as impact_mod
from .tasks import _git


class ConvergeUnavailable(Exception):
    """A range that does not resolve. Convergence over a guessed range is not
    a comparison, it is a hope."""


VERDICT_RANK = {"FAIL": 3, "REVIEW-REQUIRED": 2, "NO-DATA": 1, "PASS": 0}

_DROP_RE = re.compile(r"\bdrop\s+(table|column)\s+(?:if\s+exists\s+)?([A-Za-z_][A-Za-z0-9_]*)",
                      re.IGNORECASE)

_CONTRACT_KINDS = ("openapi", "asyncapi", "protobuf", "avro", "graphql",
                   "event-schema", "dbt-model", "orm-model")
_MIGRATION_KINDS = ("db-migration", "sql-ddl", "destructive-migration")

_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

#: A small named source-text set (spec: SCOPE). A changed, unowned,
#: undossiered file whose path matches no impact detector AND whose extension
#: is outside this set is not source this tool can call "unplanned but
#: potentially legitimate": it is a DISTINCT category, unmeasured, because
#: "nobody reviewed it" and "this tool cannot even read what kind of file it
#: is" are different sentences and only the detector/extension check tells
#: them apart.
_SOURCE_EXTENSIONS = (".py", ".sql", ".md", ".sh", ".js", ".ts", ".go", ".rb",
                     ".java", ".kt", ".json", ".yaml", ".yml", ".toml", ".cfg",
                     ".ini", ".txt")


def _is_source_shaped(rel):
    """True when an impact detector recognizes this path, or its extension is
    in the tracked source-text set. False means SCOPE cannot even name what
    kind of file this is (a checksum, a binary, an opaque artifact) and it
    belongs in the unmeasured category, not the unplanned one."""
    if _detector_kinds(rel):
        return True
    return os.path.splitext(rel)[1].lower() in _SOURCE_EXTENSIONS


def _resolve(cwd, ref):
    code, out, err = _git(["rev-parse", "--verify", "%s^{commit}" % ref], cwd)
    if code != 0:
        raise ConvergeUnavailable("%r does not resolve to a commit here (%s); pass a real "
                                  "sha, because convergence over a guessed range is a hope, "
                                  "not a comparison" % (ref, err.strip()))
    return out.strip()


def _show(cwd, sha, rel):
    """File content at a commit, or None when absent there."""
    code, out, _err = _git(["show", "%s:%s" % (sha, rel)], cwd)
    return out if code == 0 else None


def _detector_kinds(rel, content=None):
    """Detector ids whose PATH matches, honoring each detector's CONTENT
    pattern when it declares one. External proof, estate B: dropping the
    content half made every .sql (and every .py, via the destructive
    detector's path pattern) migration-shaped, so a SELECT-only dbt model
    FAILed a dossier for lacking a data model it never needed. A detector
    that declares a content pattern speaks only when the content matches;
    with no content to inspect (an unreadable or absent side), the detector
    stays silent rather than guessing."""
    kinds = []
    for det_id, _why, _sets, path_pat, content_pat in impact_mod.DETECTORS:
        if not re.search(path_pat, rel):
            continue
        if content_pat is not None:
            if content is None or not re.search(content_pat, content, re.IGNORECASE):
                continue
        kinds.append(det_id)
    return kinds


def _command_tokens(command):
    """The command as the shell would split it, or None when it does not
    parse. The receipt records ARGV (quotes already consumed by the shell);
    the plan records the RAW markdown text (quotes included). Comparing a
    rejoined argv to raw text can never match a quoted argument, which is
    how two estates' freshly-made receipts were refused as absent. Both
    sides canonicalize through shlex before comparison.

    POSIX mode ON EVERY PLATFORM, deliberately (Windows audit, 2026-08-18).
    The cost is real and is written down rather than left to be discovered: a
    backslash is an escape here, so a verify command naming a bare
    `C:\\Users\\x` splits to `C:Usersx`, matches nothing, and a freshly minted
    receipt reads as absent. Quoting the path or spelling it with forward
    slashes both survive intact, and docs/WINDOWS-CHECK.md tells a Windows
    tester to do exactly that. `posix=False` on Windows was rejected: it keeps
    the quote characters a recorded argv never carries, so it would break the
    quoted spelling that works today, and it would make one plan and one
    receipt compare by different rules depending on the machine reading them.
    Failure stays a no-match, never a false match; what a no-match then MEANS
    belongs to the caller, and the two callers differ: `sbe work finish`
    reports it as NO-DATA, while convergence records a required receipt it
    cannot match as FAIL (a statement that it ran is not evidence; see the
    verification section below). Pinned by `VerifyCommandWindowsPaths` in
    tools/test_sbe_windows_sim.py."""
    try:
        return shlex.split(command)
    except ValueError:  # sbe: allow-silent this module reads and reports and never rewrites a record, and a ValueError here carries no fact beyond "this string is not shell-splittable" (an unbalanced quote); the ONE caller tests for None and falls back to the raw-text comparison this function was added to SUPPLEMENT, so no receipt is dropped, nothing is counted as matched, and the unparseable command is still compared the way it was before this function existed
        return None


def _dossier_text(dossier_dir):
    chunks = []
    for name in sorted(os.listdir(dossier_dir)):
        if name.endswith((".md", ".json")):
            try:
                chunks.append(io.open(os.path.join(dossier_dir, name),
                                      encoding="utf-8", errors="ignore").read())
            except OSError as exc:
                raise ConvergeUnavailable(
                    "dossier artifact %s exists but cannot be read (%s); refusing to "
                    "judge documentation coverage over a dossier this tool could not "
                    "fully read" % (name, exc))
    return "\n".join(chunks)


def _plan_text(plan):
    parts = []
    for task in plan.get("tasks", []) if plan else []:
        parts.append(str(task.get("title", "")))
        parts.extend(str(a) for a in task.get("acceptance", []))
    return "\n".join(parts)


def _openapi_ops(text):
    """{"method path": json-subtree} or None when the text is not JSON."""
    try:
        doc = json.loads(text)
    except ValueError:
        return None  # sbe: allow-silent  (not silent: the caller names the file as
        #                unmeasured and the dimension cannot PASS while it stands)
    ops = {}
    paths = doc.get("paths") if isinstance(doc, dict) else None
    if not isinstance(paths, dict):
        return ops
    for path, methods in sorted(paths.items()):
        if not isinstance(methods, dict):
            continue
        for method, body in sorted(methods.items()):
            if method.lower() in _HTTP_METHODS:
                ops["%s %s" % (method.lower(), path)] = body
    return ops


def _data_model_names(dossier_dir):
    """Entity and attribute names documented in 05-data-model.md, lowercased."""
    path = os.path.join(dossier_dir, "05-data-model.md")
    if not os.path.isfile(path):
        return None
    body = io.open(path, encoding="utf-8", errors="ignore").read()
    names = set()
    in_attrs = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_attrs = stripped.lower().startswith("## attribute roles")
            continue
        if stripped.startswith("- ") and ":" in stripped:
            names.add(stripped[2:].split(":", 1)[0].strip().lower())
        if in_attrs and stripped.startswith("|") and not set(stripped) <= set("|- :"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) >= 2 and cells[0].lower() not in ("attribute",):
                names.add(cells[0].lower())
                names.add(cells[1].lower())
    return names


def _find_receipt(evidence_dir, command, head):
    """{"match": path or None, "wrong_head": [(path, headCommit)], "unreadable": [names]}.
    The house matching rule from sbe work: recorded argv joins to the command.
    Receipts are examined directly (load plus seal) rather than through
    evidence.verify, because verify binds to the CURRENT head of the tree and
    converge assesses an explicit --head that may lawfully differ."""
    out = {"match": None, "wrong_head": [], "unreadable": [], "broken": []}
    if not os.path.isdir(evidence_dir):
        return out
    for name in sorted(os.listdir(evidence_dir)):
        path = os.path.join(evidence_dir, name)
        if not name.endswith(".json") or not os.path.isfile(path):
            continue
        try:
            receipt = evidence_mod.load(path)
        except evidence_mod.ReceiptUnreadable as exc:
            out["unreadable"].append("%s: %s" % (name, exc))
            continue
        argv = [str(a) for a in (receipt.get("argv") or [])]
        wanted = _command_tokens(command)
        if wanted is None:
            if " ".join(argv) != command:
                continue
        elif argv != wanted:
            continue
        if receipt.get("runId") != evidence_mod.compute_seal(receipt):
            out["broken"].append("%s: runId does not match the seal over its own "
                                 "run facts" % name)
            continue
        if receipt.get("headCommit") != head:
            out["wrong_head"].append((name, str(receipt.get("headCommit"))))
            continue
        if receipt.get("exitCode") != 0:
            out["broken"].append("%s: recorded exit %s, not 0" % (name, receipt.get("exitCode")))
            continue
        if out["match"] is None:
            out["match"] = name
    return out


def _dim(name, verdict, findings):
    return {"name": name, "verdict": verdict, "findings": findings}


def evaluate(dossier_dir, cwd, base, head):
    """The full report dict. Raises ConvergeUnavailable on an unresolvable ref."""
    cwd = os.path.abspath(cwd)
    dossier_dir = os.path.abspath(dossier_dir)
    base_sha = _resolve(cwd, base)
    head_sha = _resolve(cwd, head)
    # SERIALIZED RELATIVE PATHS ARE POSIX-SPELLED, ON EVERY PLATFORM (see
    # status.py:302 for the same rule stated at length). rel_dossier is
    # compared below against `changed`, which is git-diff output and always
    # forward-slash-spelled regardless of host; it is also serialized into
    # the report as "dossier" and into finding "evidence" fields. Left as
    # `os.path.relpath` answers, it read `design\chg` on Windows, which never
    # matched a `design/chg/...` git path: every dossier-artifact edit fell
    # through into "unplanned", so SCOPE read REVIEW-REQUIRED forever, on
    # every range, on Windows only. Canonicalize once, here.
    rel_dossier = os.path.relpath(dossier_dir, cwd).replace(os.sep, "/")

    changed = impact_mod.changed_files(cwd, base_sha, head_sha)

    plan_path = os.path.join(dossier_dir, "08-plan.json")
    plan = None
    if os.path.isfile(plan_path):
        try:
            plan = json.loads(io.open(plan_path, encoding="utf-8").read())
        except ValueError:
            plan = None
    owned = set()
    commands = []
    for task in (plan or {}).get("tasks", []):
        for p in task.get("owns", []):
            owned.add(p)
        for c in task.get("verificationCommands", []):
            commands.append((task.get("id", "?"), c, list(task.get("owns", []))))

    dossier_named = set()
    for name in sorted(os.listdir(dossier_dir)):
        if name.endswith(".md"):
            body = io.open(os.path.join(dossier_dir, name),
                           encoding="utf-8", errors="ignore").read()
            for token in sbe_plan._backticked_paths(body):
                dossier_named.add(token)

    doc_text = (_dossier_text(dossier_dir) + "\n" + _plan_text(plan)).lower()

    # SCOPE ------------------------------------------------------------
    scope_findings = []
    in_scope, unplanned, unmeasured = [], [], []
    for rel in changed:
        if rel.startswith(rel_dossier + "/") or rel.startswith(".sbe/"):
            continue  # design artifacts and stores are not implementation scope
        if rel in owned or rel in dossier_named:
            in_scope.append(rel)
        elif _is_source_shaped(rel):
            unplanned.append(rel)
        else:
            unmeasured.append(rel)
    for rel in unplanned:
        scope_findings.append({"verdict": "REVIEW-REQUIRED",
                               "detail": "%s changed but no plan task owns it and no dossier "
                                         "artifact names it: unplanned but potentially "
                                         "legitimate" % rel,
                               "evidence": rel})
    for rel in unmeasured:
        scope_findings.append({"verdict": "PASS",
                               "detail": "%s changed but matches no impact detector and its "
                                         "extension is outside the tracked source-text set: "
                                         "unmeasured, a category distinct from unplanned, "
                                         "never silently counted as clean" % rel,
                               "evidence": rel})
    if not plan and not dossier_named:
        scope_verdict = "NO-DATA"
        scope_findings.append({"verdict": "NO-DATA",
                               "detail": "no plan and no dossier-named paths: nothing to "
                                         "converge scope against",
                               "evidence": rel_dossier})
    elif unplanned:
        scope_verdict = "REVIEW-REQUIRED"
    else:
        scope_verdict = "PASS"
        detail = ("%d changed file(s), every one owned by a plan task or named by "
                 "the dossier" % len(in_scope))
        if unmeasured:
            detail += ("; %d unmeasured file(s) named above, not silently counted "
                      "as clean" % len(unmeasured))
        scope_findings.append({"verdict": "PASS", "detail": detail,
                               "evidence": ", ".join(in_scope) or "no changes"})

    # CONTRACTS --------------------------------------------------------
    contract_findings = []
    contract_files = [r for r in changed
                      if any(k in _CONTRACT_KINDS
                             for k in _detector_kinds(r, _show(cwd, head_sha, r)))]
    unmeasured = []
    for rel in contract_files:
        if not re.search(r"(openapi|swagger)[^/]*\.json$", rel):
            unmeasured.append((rel, "only JSON OpenAPI is diffed today; this kind is unread"))
            continue
        base_ops = _openapi_ops(_show(cwd, base_sha, rel) or "null")
        head_ops = _openapi_ops(_show(cwd, head_sha, rel) or "null")
        if base_ops is None or head_ops is None:
            which = []
            if base_ops is None:
                which.append(base_sha[:12])
            if head_ops is None:
                which.append(head_sha[:12])
            unmeasured.append((rel, "does not parse as JSON at %s" % " and ".join(which)))
            continue
        for op in sorted(set(base_ops) - set(head_ops)):
            if op in doc_text:
                continue
            contract_findings.append({"verdict": "FAIL",
                                      "detail": "operation %s was removed by this range and "
                                                "no dossier artifact or plan task mentions "
                                                "it: a direct contradiction" % op,
                                      "evidence": "%s at %s..%s" % (rel, base_sha[:12],
                                                                    head_sha[:12])})
        for op in sorted(set(head_ops) - set(base_ops)):
            if op in doc_text:
                continue
            contract_findings.append({"verdict": "REVIEW-REQUIRED",
                                      "detail": "operation %s was added by this range and is "
                                                "documented nowhere in the dossier or plan"
                                                % op,
                                      "evidence": rel})
        for op in sorted(set(base_ops) & set(head_ops)):
            if base_ops[op] != head_ops[op] and op not in doc_text:
                contract_findings.append({"verdict": "REVIEW-REQUIRED",
                                          "detail": "operation %s changed shape in this "
                                                    "range, undocumented" % op,
                                          "evidence": rel})
    for rel, why in unmeasured:
        contract_findings.append({"verdict": "REVIEW-REQUIRED",
                                  "detail": "%s is a changed contract-shaped file this tool "
                                            "did not read (%s); unmeasured, and a dimension "
                                            "with an unread contract cannot claim PASS"
                                            % (rel, why),
                                  "evidence": rel})
    if not contract_files:
        contract_verdict = "NO-DATA"
        contract_findings.append({"verdict": "NO-DATA",
                                  "detail": "no contract-shaped file changed in this range",
                                  "evidence": "%s..%s" % (base_sha[:12], head_sha[:12])})
    else:
        contract_verdict = max((f["verdict"] for f in contract_findings),
                               key=lambda v: VERDICT_RANK[v], default="PASS")
        if contract_verdict not in ("FAIL", "REVIEW-REQUIRED"):
            contract_verdict = "PASS"
            contract_findings.append({"verdict": "PASS",
                                      "detail": "every contract change in this range is "
                                                "documented in the dossier or plan",
                                      "evidence": ", ".join(contract_files)})

    # DATA -------------------------------------------------------------
    data_findings = []
    migration_files = [r for r in changed
                       if any(k in _MIGRATION_KINDS
                              for k in _detector_kinds(r, _show(cwd, head_sha, r)))]
    documented = _data_model_names(dossier_dir)
    if migration_files and documented is None:
        data_findings.append({"verdict": "FAIL",
                              "detail": "migration-shaped files changed but the dossier has "
                                        "no 05-data-model.md at all: a data change claiming "
                                        "to need no data model",
                              "evidence": ", ".join(migration_files)})
    for rel in migration_files:
        body = _show(cwd, head_sha, rel)
        if body is None:
            continue  # deleted migration file: nothing at head to scan
        for _kind, name in _DROP_RE.findall(body):
            if documented and name.lower() in documented:
                data_findings.append({"verdict": "FAIL",
                                      "detail": "%s drops %s, which 05-data-model.md still "
                                                "documents: migration and data model "
                                                "contradict each other" % (rel, name),
                                      "evidence": "%s against %s/05-data-model.md"
                                                  % (rel, rel_dossier)})
    if not migration_files:
        data_verdict = "NO-DATA"
        data_findings.append({"verdict": "NO-DATA",
                              "detail": "no migration-shaped file changed in this range",
                              "evidence": "%s..%s" % (base_sha[:12], head_sha[:12])})
    elif any(f["verdict"] == "FAIL" for f in data_findings):
        data_verdict = "FAIL"
    else:
        data_verdict = "PASS"
        data_findings.append({"verdict": "PASS",
                              "detail": "no changed migration drops anything the data model "
                                        "still documents; subtler contradictions are beyond "
                                        "this scan and KNOWN-LIMITS says so",
                              "evidence": ", ".join(migration_files)})

    # ARCHITECTURE -----------------------------------------------------
    arch_findings = [{"verdict": "NO-DATA",
                      "detail": "architecture comparison reads declared component names "
                                "against new top-level directories and nothing deeper "
                                "(technology, dependencies, infrastructure, recovery are "
                                "not read from a diff); this dossier declares no diagram "
                                "components to compare" if not os.path.isfile(
                                    os.path.join(dossier_dir, "06-diagrams.md"))
                                else "architecture comparison is name-level only; no new "
                                     "top-level directory appeared in this range",
                      "evidence": rel_dossier}]
    arch_verdict = "NO-DATA"
    diagrams_path = os.path.join(dossier_dir, "06-diagrams.md")
    if os.path.isfile(diagrams_path):
        code, out, _err = _git(["ls-tree", "--name-only", base_sha], cwd)
        base_top = set(out.split()) if code == 0 else set()
        declared = io.open(diagrams_path, encoding="utf-8", errors="ignore").read().lower()
        new_tops = sorted({r.split("/", 1)[0] for r in changed if "/" in r}
                          - base_top)
        flagged = [t for t in new_tops if t.lower() not in declared]
        for top in flagged:
            arch_findings.append({"verdict": "REVIEW-REQUIRED",
                                  "detail": "new top-level directory %s appeared in this "
                                            "range and matches no declared component in "
                                            "06-diagrams.md" % top,
                                  "evidence": top})
        if flagged:
            arch_verdict = "REVIEW-REQUIRED"

    # VERIFICATION -----------------------------------------------------
    verif_findings = []
    evidence_dir = os.path.join(cwd, ".sbe", "evidence")
    seen_commands = set()
    for task_id, command, owns in commands:
        dedup_key = (command, tuple(sorted(owns)))
        if dedup_key in seen_commands:
            continue
        seen_commands.add(dedup_key)
        found = _find_receipt(evidence_dir, command, head_sha)
        for note in found["unreadable"] + found["broken"]:
            verif_findings.append({"verdict": "FAIL", "detail": note,
                                   "evidence": evidence_dir})
        if found["match"]:
            receipt_note = found["match"]
            receipt_path = os.path.join(evidence_dir, receipt_note)
            covered = (evidence_mod.load(receipt_path).get("coveredFiles") or [])
            covered_paths = set()
            for covered_entry in covered:
                if isinstance(covered_entry, dict):
                    if covered_entry.get("path"):
                        covered_paths.add(covered_entry["path"])
                elif isinstance(covered_entry, str) and covered_entry:
                    covered_paths.add(covered_entry)
            missing_cover = [p for p in owns if p not in covered_paths]
            if owns and missing_cover:
                verif_findings.append({"verdict": "FAIL",
                                       "detail": "the receipt for %s does not cover %s, "
                                                 "which its task owns: evidence about the "
                                                 "wrong files" % (command,
                                                                  ", ".join(missing_cover)),
                                       "evidence": receipt_note})
                continue
            verif_findings.append({"verdict": "PASS",
                                   "detail": "%s has a sealed receipt bound to %s"
                                             % (command, head_sha[:12]),
                                   "evidence": receipt_note})
            continue
        if found["wrong_head"]:
            name, other = found["wrong_head"][0]
            verif_findings.append({"verdict": "FAIL",
                                   "detail": "the receipt for %s binds to %s, not the "
                                             "assessed head %s: stale evidence from another "
                                             "commit" % (command, other[:12], head_sha[:12]),
                                   "evidence": name})
            continue
        verif_findings.append({"verdict": "FAIL",
                               "detail": "task %s requires %s and no receipt for it exists "
                                         "bound to %s; a statement that it ran is not "
                                         "evidence" % (task_id, command, head_sha[:12]),
                               "evidence": evidence_dir})
    if not commands:
        verif_verdict = "NO-DATA"
        verif_findings.append({"verdict": "NO-DATA",
                               "detail": "no plan task carries a verification command",
                               "evidence": plan_path if plan else rel_dossier})
    else:
        verif_verdict = ("FAIL" if any(f["verdict"] == "FAIL" for f in verif_findings)
                         else "PASS")

    # FINAL ------------------------------------------------------------
    dims = [_dim("SCOPE", scope_verdict, scope_findings),
            _dim("CONTRACTS", contract_verdict, contract_findings),
            _dim("DATA", data_verdict, data_findings),
            _dim("ARCHITECTURE", arch_verdict, arch_findings),
            _dim("VERIFICATION", verif_verdict, verif_findings)]
    verdicts = [d["verdict"] for d in dims]
    if "FAIL" in verdicts:
        final = "FAIL"
    elif "REVIEW-REQUIRED" in verdicts:
        final = "REVIEW-REQUIRED"
    elif all(v == "NO-DATA" for v in verdicts):
        final = "NO-DATA"
    else:
        final = "PASS"
    silences = [d["name"] for d in dims if d["verdict"] == "NO-DATA"]

    return {
        "tool": "sbe converge",
        "repository": evidence_mod.repository_identity(cwd),
        "dossier": rel_dossier,
        "base": base_sha,
        "head": head_sha,
        "dimensions": dims,
        "final": final,
        "notExamined": silences,
        "finalRule": "FAIL beats REVIEW-REQUIRED; NO-DATA is final only when every "
                     "dimension was NO-DATA; a PASS lists its silences by name",
    }


def render(report):
    lines = []
    for dim in report["dimensions"]:
        lines.append("%-13s %s" % (dim["name"], dim["verdict"]))
        for f in dim["findings"]:
            lines.append("  %-15s %s" % (f["verdict"], f["detail"]))
    lines.append("")
    lines.append("FINAL %s  (dossier %s, %s..%s)"
                 % (report["final"], report["dossier"],
                    report["base"][:12], report["head"][:12]))
    if report["notExamined"]:
        lines.append("not examined (NO-DATA, named rather than counted clean): %s"
                     % ", ".join(report["notExamined"]))
    return "\n".join(lines)


def write_report(report, dossier_dir):
    path = os.path.join(dossier_dir, "09-convergence.json")
    io.open(path, "w", encoding="utf-8").write(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    return path
