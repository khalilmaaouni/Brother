"""Derive what a change actually touches from its diff, and reconcile that with
what the human said at intake.

The defect this exists for, in one sentence: the tier is computed from five
answers and nothing has ever read the code, so a change that rewrites an API
contract can be classified T0 by answering "no" five times, and every gate
downstream then agrees that this change owes no evidence.

THE ONE RULE THAT MAKES THIS SAFE: this module does not implement a second tier
ladder. It converts detector hits into the SAME five intake answers the human
gives, and hands them to `sbe_intake.compute_tier`. One rule, one table, two
inputs. A tier computed here and a tier computed from a person's answers cannot
drift apart, because there is only one place where a tier is decided.

THE PROPOSED TIER IS A FLOOR, NEVER A CEILING. Two of the five intake answers
cannot be derived from a diff at all:

  - `consumers`: how many downstream things break if this is wrong. Nothing in a
    diff knows that without an ownership map the repository does not have. It is
    assumed "none", which is the value that can only make the proposal LOWER,
    and it is reported under `unmeasured` rather than quietly assumed.
  - `crosses_boundary` is inferred only where the diff shows infrastructure,
    deployment or event-contract files. A service call added inside existing code
    is not visible to a path-based detector.

So: this tool may say a change is bigger than the human claimed. It may never
say a change is smaller, and a PASS from it means "nothing in the diff
contradicts the declared tier", which is a much narrower statement than "the
declared tier is right".
"""
import io
import json
import os
import re
import subprocess

from ._toolspath import mount
mount()
from sbe_intake import compute_tier, UnreadableIntake  # noqa: E402

#: Detectors. Each is (id, why it matters, the intake answer it sets, path
#: pattern, optional content pattern). Path patterns are matched against the
#: forward-slash path, case-folded. Content patterns, where present, are matched
#: against the ADDED lines of the diff only, so deleting a line that mentions a
#: payment does not classify a change as touching money.
#:
#: `sets` is the intake answer this hit forces. Only these three are derivable:
#:   sensitive     -> touches_sensitive = True        (drives T3)
#:   irreversible  -> reversible_under_hour = False   (drives T3)
#:   contract      -> changes_contract = True         (drives T2)
#:   boundary      -> crosses_boundary = True         (drives T1)
DETECTORS = [
    ("openapi", "an HTTP API contract others build against", "contract",
     r"(openapi|swagger)[^/]*\.(ya?ml|json)$", None),
    ("asyncapi", "an asynchronous API contract", "contract",
     r"asyncapi[^/]*\.(ya?ml|json)$", None),
    ("protobuf", "a wire format shared across services", "contract", r"\.proto$", None),
    ("avro", "a serialization schema consumers decode with", "contract", r"\.avsc$", None),
    ("graphql", "a published query surface", "contract", r"\.(graphql|gql)$", None),
    ("event-schema", "an event contract consumers subscribe to", "contract",
     r"(^|/)(events?|schemas?)/.*\.(json|ya?ml|avsc)$", None),
    ("db-migration", "a schema change other code and queries depend on", "contract",
     r"(^|/)(migrations?|alembic|flyway|liquibase|db/migrate)/", None),
    ("sql-ddl", "data definition language, which changes a shared shape", "contract",
     r"\.sql$", r"\b(create|alter|drop)\s+(table|view|schema|index|type)\b"),
    ("dbt-model", "a warehouse model other models and reports select from", "contract",
     r"(^|/)models/.*\.(sql|ya?ml)$", None),
    ("orm-model", "the code definition of a persisted shape", "contract",
     r"(^|/)(models?|entities|schema)\.(py|rb|ts|js|go|java|kt)$", None),

    ("destructive-migration", "drops or truncates, which is not reversible in an hour",
     "irreversible", r"\.(sql|py|rb)$",
     r"\b(drop\s+(table|column|schema)|truncate\s+table|delete\s+from\s+\w+\s*;)"),

    ("payment-path", "money movement", "sensitive",
     r"(payment|billing|invoice|refund|settlement|payout|charge|pricing|rebate|discount)", None),
    ("partner-path", "a partner-facing surface", "sensitive",
     r"(partner|vendor|supplier|merchant)s?[_/-]", None),
    ("pii-path", "personal data", "sensitive",
     r"(^|/)[^/]*(pii|gdpr|personal[_-]?data|customer[_-]?data)[^/]*", None),
    ("pii-field", "a personal data field added in code or schema", "sensitive", r"\.(sql|py|rb|ts|js|go|java|kt|ya?ml|json)$",
     r"\b(ssn|social_security|passport_no|national_id|date_of_birth|dob|home_address|"
     r"phone_number|email_address|credit_card|card_number|iban|bank_account)\b"),
    ("auth-path", "who may reach what", "sensitive",
     r"(^|/)[^/]*(auth|authz|authentication|authorization|permission|rbac|oauth|session)[^/]*"
     r"\.(py|rb|ts|js|go|java|kt)$", None),
    ("production-config", "production state", "sensitive",
     r"(^|/)(prod|production)[^/]*/|\.(tfvars)$|(^|/)(terraform|k8s|kubernetes|helm|deploy"
     r"|deployment)s?/", None),
    ("secret-material", "credentials or key material", "sensitive",
     r"(^|/)[^/]*(secret|credential|keystore|\.pem|\.p12)[^/]*", None),

    ("infrastructure", "infrastructure that other systems run on", "boundary",
     r"\.(tf|tfvars)$|(^|/)(k8s|kubernetes|helm|charts?)/|(^|/)docker-compose[^/]*\.ya?ml$",
     None),
    ("ci-pipeline", "the pipeline other engineers' merges run through", "boundary",
     r"(^|/)\.github/workflows/|(^|/)\.gitlab-ci\.ya?ml$|(^|/)Jenkinsfile$", None),
    ("queue-config", "an asynchronous boundary between systems", "boundary",
     r"(kafka|rabbitmq|sqs|pubsub|kinesis)", None),
]

#: Extensions this tool has no detector for and does not pretend to read. Files
#: whose extension is not in the union of what the detectors match land in
#: `unmeasured`, because "no detector fired" and "nothing to detect" are
#: different sentences and only one of them is honest about coverage.
QUIET_EXTENSIONS = (".md", ".txt", ".rst", ".adoc", ".gitignore", ".editorconfig",
                    ".license", ".cfg", ".ini", ".toml", ".lock")


#: Detector ids counted as "this diff touches money", for consequence
#: assessment. Kept to the one detector DETECTORS itself labels for money
#: rather than re-describing the shape of a payment path a second time.
MONEY_DETECTORS = ("payment-path",)

#: Thresholds a diff's total changed-line count is measured against.
#: ponytail: two constants, not a tuned model; raise the ceiling with a real
#: distribution of diff sizes if this ever misjudges a real change.
SMALL_DIFF_LINES = 20
LARGE_DIFF_LINES = 200

#: A changed path this tool reads as "an existing test suite touched by this
#: diff", naming the change as at least partly self-proving. Path-based, the
#: same idiom DETECTORS already uses.
_TEST_PATH = re.compile(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_[^/]+\.\w+$|_test\.\w+$",
                        re.I)


class DiffUnavailable(Exception):
    """The diff could not be resolved, so nothing was inspected.

    Raised rather than returning an empty change set, because an empty change
    set and an unreadable one produce the same JSON and only one of them means
    the change is small.
    """


def _git(args, cwd):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


def resolve_range(cwd, base=None, head="HEAD"):
    """(base, head) or raise. Never guesses silently.

    Order: an explicit base wins. Otherwise the merge base with the default
    branch, tried in the order a repository is likely to name it. Otherwise the
    parent commit. A repository with a single commit and no explicit base has no
    range at all, and that is reported rather than turned into "everything".
    """
    code, _out, err = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    if code != 0:
        raise DiffUnavailable("not a git repository: %s" % err.strip())
    if base:
        code, _o, err = _git(["rev-parse", "--verify", base], cwd)
        if code != 0:
            raise DiffUnavailable("base %r does not resolve in this repository" % base)
        return base, head
    for candidate in ("origin/HEAD", "origin/main", "main", "origin/master", "master"):
        code, out, _e = _git(["merge-base", candidate, head], cwd)
        if code == 0 and out.strip():
            return out.strip(), head
    code, out, _e = _git(["rev-parse", "%s^" % head], cwd)
    if code == 0 and out.strip():
        return out.strip(), head
    raise DiffUnavailable(
        "no diff range: no default branch to merge-base against and %s has no parent. "
        "Pass --base explicitly. Nothing was inspected." % head)


def changed_files(cwd, base, head):
    code, out, err = _git(["diff", "--name-only", "%s..%s" % (base, head)], cwd)
    if code != 0:
        raise DiffUnavailable("git diff failed: %s" % err.strip())
    return [f for f in out.split("\n") if f.strip()]


def stale_ignoring_record_commits(cwd, bound, head, exempt_rel):
    """True when a record bound to commit `bound` no longer speaks for the
    tree at `head` -- False only when `bound` and `head` are the same
    commit, or every commit strictly between them (`bound`..`head`, walked
    with `git rev-list`) touches nothing outside `exempt_rel`
    (repo-root-relative POSIX paths: a record's own file, and its lock
    sidecar where it has one).

    WHY THIS EXISTS. `sbe handover prepare` and `sbe evidence run` each bind
    a record to the commit checked out when they ran, then the record itself
    has to be committed for it to reach a second clone at all -- and that
    commit becomes the new HEAD, one past the commit the record just named.
    Compared by exact equality alone, that stales the record over the exact
    commit that carries it, which defeats sharing it in the first place (see
    `docs/adr/2026-08-12-handover-across-two-machines.md`). A commit whose
    ENTIRE diff is the record (and its lock) is provably not "the code
    moved"; it is the record announcing itself. A commit that bundles the
    record with anything else still stales it exactly as it always did --
    this narrows one specific case, it does not loosen the rule the ADR's
    own "property that must survive" names.

    `exempt_rel` empty reproduces the historical, unmodified behavior: any
    commit in range stales the binding. An unwalkable range (a shallow
    clone, `bound` unreachable from `head`, a rewritten history) is reported
    stale, the same conservative default the old exact-equality check
    already had for everything except the exact-match case -- this only
    rules a false positive OUT when it can positively prove the range is
    record-only, it never rules one IN by assumption.
    """
    if bound == head:
        return False
    if not bound or not head:
        return True
    code, out, _err = _git(["rev-list", "%s..%s" % (bound, head)], cwd)
    if code != 0:
        return True
    commits = [c for c in out.splitlines() if c.strip()]
    if not commits:
        # bound..head resolved but is empty: bound is not an ancestor of
        # head (a rewritten or diverged history). bound != head was already
        # established above, so there is real drift here with no range to
        # inspect; the conservative default applies.
        return True
    for commit in commits:
        code, out, _err = _git(["diff", "--name-only", "%s^" % commit, commit], cwd)
        if code != 0:
            # a root commit has no parent to diff against; its own full tree
            # is what it introduced.
            code, out, _err = _git(["show", "--name-only", "--pretty=format:", commit], cwd)
            if code != 0:
                return True
        touched = [p.strip() for p in out.splitlines() if p.strip()]
        if any(p not in exempt_rel for p in touched):
            return True
    return False


def added_lines(cwd, base, head, path):
    """Only the ADDED lines for one file. Deleting a line that mentions a
    payment is not a change that touches money, and a content detector reading
    the whole diff would classify it as one."""
    code, out, _err = _git(["diff", "-U0", "%s..%s" % (base, head), "--", path], cwd)
    if code != 0:
        return ""
    return "\n".join(l[1:] for l in out.split("\n")
                     if l.startswith("+") and not l.startswith("+++"))


def detect(cwd, base, head, files=None):
    """(hits, unmeasured). One hit per (detector, file) pair, each naming why."""
    files = changed_files(cwd, base, head) if files is None else files
    hits, unmeasured = [], []
    for path in files:
        low = path.lower()
        matched_any = False
        for det_id, why, sets, path_pat, content_pat in DETECTORS:
            if not re.search(path_pat, low):
                continue
            if content_pat:
                body = added_lines(cwd, base, head, path).lower()
                if not re.search(content_pat, body):
                    continue
            matched_any = True
            hits.append({"detector": det_id, "file": path, "why": why, "sets": sets})
        if not matched_any:
            ext = os.path.splitext(low)[1]
            if ext not in QUIET_EXTENSIONS:
                unmeasured.append({"file": path,
                                   "reason": "no detector covers %s files; this tool did not "
                                             "read it and is not reporting it as clean"
                                             % (ext or "extensionless")})
    return hits, unmeasured


def answers_from_hits(hits):
    """The five intake answers, as the diff supports them.

    Two are floors rather than measurements, and say so in `unmeasured` upstream:
    `consumers` is assumed "none" and `crosses_boundary` is only ever inferred
    from infrastructure-shaped files. Both assumptions can only LOWER the
    proposed tier, which is the safe direction for a tool whose whole promise is
    that it never silently lowers anything.
    """
    sets = set(h["sets"] for h in hits)
    return {
        "changes_contract": "y" if "contract" in sets else "n",
        "crosses_boundary": "y" if "boundary" in sets else "n",
        "reversible_under_hour": "n" if "irreversible" in sets else "y",
        "touches_sensitive": "y" if "sensitive" in sets else "n",
        "consumers": "none",
    }


def _diff_total_lines(cwd, base, head):
    """Added-plus-removed line count over the whole range, via `git diff
    --numstat`. A file numstat cannot count (binary) contributes 0 rather
    than raising: this is a size estimate for proof-burden, not a
    byte-exact accounting, and `git diff` itself already covers the
    DiffUnavailable cases this module raises elsewhere."""
    code, out, _err = _git(["diff", "--numstat", "%s..%s" % (base, head)], cwd)
    if code != 0:
        return 0
    total = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        for n in parts[0], parts[1]:
            if n.isdigit():
                total += int(n)
    return total


def assess_consequence(cwd, base, head, files, hits):
    """Proof burden driven by CONSEQUENCE, never by answer shape: risk asks
    what breaks if this is wrong, not how the diff is shaped.

    A tiny SQL change that also touches money raises the burden regardless
    of its size (small blast radius, high stakes). A large refactor that
    also touches an existing test suite lowers it (already partly proven).
    Neither condition alone moves anything; this only ever adjusts a
    `standard` burden, and never touches `proposedTier`, which stays the
    tier ladder's job alone.
    """
    total_lines = _diff_total_lines(cwd, base, head)
    money = any(h["sets"] == "sensitive" and h["detector"] in MONEY_DETECTORS for h in hits)
    tests_touched = any(_TEST_PATH.search(f) for f in files)
    if money and total_lines <= SMALL_DIFF_LINES:
        burden, reason = "raised", (
            "a small diff (%d line(s) changed) touches a money path; size is not the "
            "measure here, consequence is" % total_lines)
    elif total_lines >= LARGE_DIFF_LINES and tests_touched:
        burden, reason = "lowered", (
            "a large diff (%d line(s) changed) also touches an existing test suite, which "
            "is partly self-proving" % total_lines)
    else:
        burden, reason = "standard", "neither the raise nor the lower condition is met"
    return {"diffLines": total_lines, "moneyTouched": money, "testsTouched": tests_touched,
            "proofBurden": burden, "reason": reason}


def read_intent(path):
    """The value_hypothesis recorded in the same intake file `read_intake`
    reads, or None if there is none to read.

    Reads independently of `read_intake` rather than threading a second
    return through it, so a caller that only wants the tier keeps the exact
    call it already has. A missing file, a missing `intent` block, and a
    blank value_hypothesis are all the same honest None here; a file that
    exists but does not parse is also None -- `read_intake`, called on the
    very same path by every caller of this module, is what turns that
    failure into FAIL. This function only ever adds a field, never a
    verdict, so it does not repeat that error a second time.
    """
    if not path or not os.path.exists(path):
        return None
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError:
        # sbe: allow-silent a malformed intake at this same path is already
        # reported as FAIL by read_intake; this function only ever adds a
        # field and must not invent a second error message for one file.
        return None
    intent = data.get("intent") if isinstance(data, dict) else None
    if not isinstance(intent, dict):
        return None
    value = intent.get("value_hypothesis")
    return value.strip() if isinstance(value, str) and value.strip() else None


def read_intake(path):
    """(tier, answers, problem). A malformed intake is a problem, never a zero."""
    if not path:
        return None, None, ("no intake was read: no dossier was named on the "
                            "command line, so there is no intake path to read")
    if not os.path.exists(path):
        return None, None, "no intake file at %s" % path
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        return None, None, "intake at %s does not parse: %s" % (path, exc)
    answers = data.get("answers", data)
    try:
        return compute_tier(answers), answers, None
    except UnreadableIntake as exc:
        return None, answers, "intake at %s cannot be read: %s" % (path, exc)


def _tier_index(tier):
    return ("T0", "T1", "T2", "T3").index(tier)


def read_disposition(path, head_sha):
    """A recorded human decision about a disagreement, bound to a commit.

    Bound on purpose: a disposition written against an earlier head says nothing
    about what the diff contains now. A stale one is reported as stale and does
    NOT resolve anything, which is the same rule the evidence work applies to
    every other receipt.
    """
    if not path or not os.path.exists(path):
        return [], None
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        return [], "disposition at %s does not parse: %s" % (path, exc)
    records = data if isinstance(data, list) else data.get("dispositions", [])
    live, stale = [], 0
    for rec in records:
        required = ("detector", "decision", "reason", "who", "head")
        if any(not str(rec.get(k, "")).strip() for k in required):
            continue
        if rec.get("head") != head_sha:
            stale += 1
            continue
        live.append(rec)
    note = None
    if stale:
        note = ("%d disposition(s) were written against a different head commit and resolve "
                "nothing; a decision about an older diff is not a decision about this one"
                % stale)
    return live, note


def report(cwd, base=None, head="HEAD", intake_path=None, disposition_path=None):
    """The whole verdict, as a dict ready to be written as impact-report.json."""
    base_sha, head_ref = resolve_range(cwd, base, head)
    code, head_sha, _e = _git(["rev-parse", head_ref], cwd)
    head_sha = head_sha.strip() if code == 0 else head_ref

    files = changed_files(cwd, base_sha, head_ref)
    hits, unmeasured = detect(cwd, base_sha, head_ref, files)
    derived = answers_from_hits(hits)
    proposed = compute_tier(derived)
    human_tier, _human_answers, intake_problem = read_intake(intake_path)
    value_hypothesis = read_intent(intake_path)
    dispositions, disposition_note = read_disposition(disposition_path, head_sha)
    consequence = assess_consequence(cwd, base_sha, head_ref, files, hits)

    unmeasured = list(unmeasured)
    unmeasured.append({"file": None,
                       "reason": "consumers: how many downstream things break if this is "
                                 "wrong cannot be read from a diff. Assumed 'none', which can "
                                 "only lower the proposal, never raise it."})
    if disposition_note:
        unmeasured.append({"file": None, "reason": disposition_note})
    if value_hypothesis is None and _tier_index(proposed) >= _tier_index("T2"):
        unmeasured.append({"file": None,
                           "reason": "no value_hypothesis was recorded in the intake for a "
                                     "change proposing tier %s" % proposed})

    disagreements = []
    if human_tier and _tier_index(proposed) > _tier_index(human_tier):
        covered = set(d["detector"] for d in dispositions)
        for hit in hits:
            if hit["sets"] in ("contract", "sensitive", "irreversible", "boundary"):
                disagreements.append({
                    "detector": hit["detector"],
                    "file": hit["file"],
                    "why": hit["why"],
                    "disposition": "recorded" if hit["detector"] in covered else "missing",
                })

    unresolved = [d for d in disagreements if d["disposition"] == "missing"]

    if intake_problem and not human_tier:
        verdict = "FAIL" if "does not parse" in intake_problem or "cannot be read" in \
            intake_problem else "NO-DATA"
    elif not files:
        verdict = "NO-DATA"
    elif unresolved:
        verdict = "REVIEW-REQUIRED"
    elif not hits and not [u for u in unmeasured if u["file"]]:
        verdict = "NO-DATA" if not files else "PASS"
    else:
        verdict = "PASS"

    return {
        "schemaVersion": "1.0",
        "scope": "git diff %s..%s over %d changed file(s)" % (base_sha[:12], head_ref,
                                                              len(files)),
        "baseCommit": base_sha,
        "headCommit": head_sha,
        "detected": hits,
        "derivedAnswers": derived,
        "proposedTier": proposed,
        "proposedTierIsA": "floor, never a ceiling: two of the five intake answers cannot be "
                           "derived from a diff and are assumed at their lowest value",
        "humanTier": human_tier,
        "intakeProblem": intake_problem,
        "disagreements": disagreements,
        "unmeasured": unmeasured,
        "verdict": verdict,
        "valueHypothesis": value_hypothesis,
        "consequence": consequence,
    }
