"""The registry that says WHICH command a check is, so a receipt can prove the
right command ran and not merely that a command ran.

The defect this exists for, in one sentence: `sbe evidence run` proves a process
started, was timed and exited, so `-- true` mints a clean, sealed, commit-bound
receipt, and the only thing naming which obligation it answered was `--kind`, a
word the caller typed beside it.

    The command ran.
    The command was the registered check required for this change.

The first sentence was provable and the second was not, and a control that can
only prove the first is a control that clears an obligation for `true`.

`.sbe/checks.yml` moves the command out of the caller's hands. A registered
check names its executable, its exact argument vector, its working directory,
the files it is evidence for, and the runner files whose bytes ARE the check.
`sbe evidence run --check <id>` resolves all of that from the registry and
accepts no substitution: there is no argument on that path that changes what
runs. The receipt then records the check id, its kind, the SHA256 of its
specification, the runner hashes and the covered hashes, and `sbe evidence
verify` recomputes every one of them against the registry as it stands NOW. A
registry edit, a renamed runner, a changed argument vector or a modified runner
script therefore invalidates every receipt minted before it, which is the whole
point: those receipts describe a check that no longer exists.

WHAT THIS STILL CANNOT PROVE, stated here rather than in a footnote. This module
binds a receipt to a registered COMMAND. It does not understand what that
command does. A registry entry that points `migration-rehearsal` at a script
which prints nothing and exits zero is a false registry, and the defence against
that is the registry being part of the protected control plane (`.sbe/checks.yml`
is named in `.sbe/policy.yml`'s control-plane rule, so changing it owes
control-plane evidence and a protected approval) rather than anything this file
can compute.

THE REGISTRY IS THIS REPOSITORY'S, NOT A TEMPLATE'S. Only checks whose runner
files actually exist here are registered. A check this repository owes and
cannot yet run is listed under `unregistered:` WITH ITS REASON instead of being
pointed at a script that does not exist: an entry nobody can run would report a
broken command where the truth is that the check has not been built, and the
policy engine reporting MISSING for it is the honest outcome.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, and the YAML subset parser is this repository's own
(`brothersbe.program`), not a dependency.
"""
import hashlib
import io
import json
import os
import subprocess

from .program import ProgramParseError, load_yaml_file

SCHEMA_VERSION = "1.0"

REGISTRY_REL = os.path.join(".sbe", "checks.yml")

#: The obligation vocabulary a REGISTERED check may declare. Deliberately wider
#: than `evidence.CHECK_KIND_NAMES` (design, gate, score), which is the legacy
#: caller-declared vocabulary: a registry kind names what the check IS
#: (a migration rehearsal, a reconciliation of numbers, a command that ran),
#: and the two lists are not the same question. A kind outside this tuple is a
#: registry PARSE ERROR, never a kind that quietly matches nothing.
CHECK_KINDS = ("migration", "ran", "numbers", "design", "gate", "score",
               "security", "contract")

#: The ONLY environment variables a registered check's process inherits.
#: Everything else is dropped before the command starts, because a check whose
#: result depends on a variable the caller exported is a check the caller can
#: steer without touching the registry, and the receipt would record an argument
#: vector that no longer describes what ran.
#:
#: PYTHONPATH is deliberately ABSENT and this is the reason: it changes which
#: modules a python runner imports, which is command substitution by another
#: name. SBE_CI_RUN_ID is present because the CI environment, not the agent, is
#: what mints it, and dropping it would make every CI receipt read as local.
ENV_ALLOWLIST = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "TMPDIR", "TEMP", "TMP", "USER", "LOGNAME", "SHELL",
    "SYSTEMROOT", "COMSPEC", "PATHEXT", "USERPROFILE",
    "SBE_CI_RUN_ID", "CI", "GITHUB_RUN_ID", "GITHUB_SHA", "GITHUB_REPOSITORY",
    # Bitbucket's equivalents, on the same footing as GitHub's rather than
    # absent. Everything not on this list is stripped before a registered
    # check's subprocess starts, so with only the GITHUB_* names here a real
    # Bitbucket Pipelines run had its own build identifiers removed and minted
    # receipts indistinguishable from somebody's laptop: exactly the failure
    # the comment above this tuple was written to prevent, live on the other
    # host. SBE_CI_RUN_ID remains the host-neutral one that decides the trust
    # level; these three are what a check script reads to know where it ran.
    "BITBUCKET_BUILD_NUMBER", "BITBUCKET_COMMIT", "BITBUCKET_REPO_SLUG",
    "BITBUCKET_BRANCH", "BITBUCKET_WORKSPACE", "BITBUCKET_PR_ID",
)


class RegistryUnreadable(Exception):
    """The check registry could not be read, or does not validate.

    Raised rather than returning an empty registry, because an empty registry
    and an unreadable one produce the same lookup failure and only one of them
    means this repository registered no checks.
    """


# ---------------------------------------------------------------------------
# The registry file
# ---------------------------------------------------------------------------

def default_registry_path(cwd):
    return os.path.join(cwd, REGISTRY_REL)


def _require_str(value, where):
    if not isinstance(value, str) or not value.strip():
        raise RegistryUnreadable("%s must be a non-empty string, got %r" % (where, value))
    return value.strip()


def _require_list_of_str(value, where, allow_empty=True):
    if value is None:
        value = []
    if not isinstance(value, list):
        raise RegistryUnreadable("%s must be a list, got %r" % (where, value))
    out = [_require_str(v, "%s[%d]" % (where, i)) for i, v in enumerate(value)]
    if not out and not allow_empty:
        raise RegistryUnreadable("%s must name at least one entry" % where)
    return out


def _require_bool(value, where):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no"):
        return value.strip().lower() in ("true", "yes")
    raise RegistryUnreadable("%s must be true or false, got %r" % (where, value))


def _normalize_check(check_id, raw):
    """One validated check specification, as the dict everything else hashes.

    Key order does not matter (the hash is computed over a canonically encoded
    payload), but the SHAPE does: every field a receipt binds to is required
    here, so a registry entry cannot omit its `covers` and leave a receipt
    proving coverage of nothing.
    """
    if not isinstance(raw, dict):
        raise RegistryUnreadable("checks.%s must be a mapping, got %r" % (check_id, raw))
    kind = _require_str(raw.get("kind"), "checks.%s.kind" % check_id)
    if kind not in CHECK_KINDS:
        raise RegistryUnreadable(
            "checks.%s.kind is %r and this build knows no such kind (it knows %s). An "
            "unrecognized kind is refused rather than ignored, because ignoring it would leave "
            "a registered check that satisfies nothing and reads as if it should"
            % (check_id, kind, ", ".join(CHECK_KINDS)))
    command = raw.get("command")
    if not isinstance(command, dict):
        raise RegistryUnreadable("checks.%s.command must be a mapping, got %r"
                                 % (check_id, command))
    executable = _require_str(command.get("executable"),
                              "checks.%s.command.executable" % check_id)
    arguments = _require_list_of_str(command.get("arguments"),
                                     "checks.%s.command.arguments" % check_id)
    run_cwd = command.get("cwd")
    run_cwd = "." if run_cwd is None else _require_str(run_cwd,
                                                       "checks.%s.command.cwd" % check_id)
    covers = _require_list_of_str(raw.get("covers"), "checks.%s.covers" % check_id,
                                  allow_empty=False)
    runner_files = _require_list_of_str(raw.get("runnerFiles"),
                                        "checks.%s.runnerFiles" % check_id)
    protected = _require_bool(raw.get("protectedEvidence", True),
                              "checks.%s.protectedEvidence" % check_id)
    return {
        "id": check_id,
        "kind": kind,
        "command": {"executable": executable, "arguments": arguments, "cwd": run_cwd},
        "covers": covers,
        "runnerFiles": runner_files,
        "protectedEvidence": protected,
        "why": raw.get("why") if isinstance(raw.get("why"), str) else None,
    }


def load_registry(path):
    """The validated registry, or raise. A registry that does not validate is
    not a registry that registers nothing.

    The `why` of every field is in `_normalize_check`; what this adds is the
    file-level shape and the one cross-field rule that matters: an id may not
    appear in both `checks` and `unregistered`, because a check that is both
    registered and declared unbuildable is a registry that answers the same
    question twice.
    """
    try:
        data = load_yaml_file(path)
    except ProgramParseError as exc:
        raise RegistryUnreadable(
            "%s could not be read as the documented YAML subset (%s). A registry this build "
            "cannot parse is refused rather than treated as a repository that registered no "
            "checks: those two are opposite facts" % (path, exc))
    if not isinstance(data, dict):
        raise RegistryUnreadable("%s holds %s at the top level; a registry is a mapping"
                                 % (path, type(data).__name__))
    declared = _require_str(data.get("schemaVersion"), "%s: schemaVersion" % path)
    if declared != SCHEMA_VERSION:
        raise RegistryUnreadable(
            "%s declares schemaVersion %r and this build reads %r; an unknown schema is refused "
            "rather than parsed hopefully" % (path, declared, SCHEMA_VERSION))
    raw_checks = data.get("checks")
    if not isinstance(raw_checks, dict) or not raw_checks:
        raise RegistryUnreadable(
            "%s declares no checks. An empty registry means no receipt can ever name a "
            "registered check, which is refused here rather than discovered one MISSING "
            "requirement at a time" % path)
    checks = {}
    for check_id in sorted(raw_checks):
        checks[check_id] = _normalize_check(check_id, raw_checks[check_id])
    unregistered = []
    raw_unregistered = data.get("unregistered") or []
    if not isinstance(raw_unregistered, list):
        raise RegistryUnreadable("%s: unregistered must be a list, got %r"
                                 % (path, raw_unregistered))
    for i, item in enumerate(raw_unregistered):
        if not isinstance(item, dict):
            raise RegistryUnreadable("%s: unregistered[%d] must be a mapping, got %r"
                                     % (path, i, item))
        ident = _require_str(item.get("id"), "%s: unregistered[%d].id" % (path, i))
        why = _require_str(item.get("why"), "%s: unregistered[%d].why" % (path, i))
        if ident in checks:
            raise RegistryUnreadable(
                "%s: %r is registered under checks AND listed as unregistered; one id cannot be "
                "both a runnable check and a check nobody can run" % (path, ident))
        unregistered.append({"id": ident, "why": why})
    return {"schemaVersion": declared, "path": path, "checks": checks,
            "unregistered": unregistered}


def spec_sha256(spec):
    """The digest of ONE check's specification, canonically encoded.

    Over the fields a receipt binds to and nothing else, so a comment or a `why`
    sentence edited in the registry does not invalidate evidence, while any
    change to what runs, where it runs, what it covers, which files are the
    runner, or whether it must be protected, does. `sort_keys` and a fixed
    separator set, so the same specification digests identically on any machine.
    """
    payload = {
        "id": spec["id"],
        "kind": spec["kind"],
        "command": spec["command"],
        "covers": spec["covers"],
        "runnerFiles": spec["runnerFiles"],
        "protectedEvidence": spec["protectedEvidence"],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def resolve(registry, check_id):
    """The specification for `check_id`, or raise naming what the registry holds.

    An id listed under `unregistered` gets its own sentence, because "this check
    is not built here yet, and here is why" is a different fact from "you typed
    a name nothing knows", and one message for both would send a reader looking
    for a typo that is not there.
    """
    spec = registry["checks"].get(check_id)
    if spec is not None:
        return spec
    for item in registry["unregistered"]:
        if item["id"] == check_id:
            raise RegistryUnreadable(
                "check %r is declared UNREGISTERED in %s: %s. It cannot be run or satisfied "
                "here until it is registered with a runner that exists"
                % (check_id, registry["path"], item["why"]))
    raise RegistryUnreadable(
        "check %r is not registered in %s, which registers %s. A caller-named check that the "
        "registry does not define is refused: the registry is the identity, not the argument"
        % (check_id, registry["path"], ", ".join(sorted(registry["checks"])) or "nothing"))


# ---------------------------------------------------------------------------
# What the registered command is, and what it covers
# ---------------------------------------------------------------------------

def expected_argv(spec):
    """The exact argument vector a registered run executes. One list, built from
    the registry alone: no caller input reaches it, which is the substitution
    this whole module exists to remove."""
    return [spec["command"]["executable"]] + list(spec["command"]["arguments"])


def _sha256_file(path):
    """The digest of a file's bytes, or None when it cannot be read. None is a
    real answer and every caller here says which one it got: a runner that was
    deleted is a different finding from one whose bytes changed."""
    try:
        with io.open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except (IOError, OSError):
        return None


def file_digests(cwd, rel_paths):
    """`[{path, sha256, note}]` for `rel_paths`, in the order given.

    A file that cannot be read records `sha256: null` WITH the note saying so,
    rather than being dropped: a runner file missing from the receipt and a
    runner file that could not be hashed look identical downstream, and only one
    of them is a registry pointing at something that is not there.
    """
    out = []
    for rel in rel_paths:
        full = rel if os.path.isabs(rel) else os.path.join(cwd, rel)
        digest = _sha256_file(full)
        out.append({
            "path": rel,
            "sha256": digest,
            "note": None if digest else "this file could not be read, so its content is not "
                                        "bound to this receipt",
        })
    return out


def executable_record(cwd, spec):
    """What the registered executable IS, as far as this repository can say.

    A repository-relative runner (`./scripts/x.sh`, `tools/y.py`) is hashed, so
    editing it invalidates every receipt made before the edit. A bare name found
    on PATH (`python3`) is NOT hashed and says so: hashing whatever the calling
    machine's PATH resolved would record a digest of the CI runner's interpreter
    that no other machine could reproduce, and a hash nobody can re-derive is a
    verification that always fails for the wrong reason.
    """
    name = spec["command"]["executable"]
    if os.sep in name or name.startswith("./") or name.startswith("../"):
        rel = name[2:] if name.startswith("./") else name
        full = rel if os.path.isabs(rel) else os.path.join(cwd, rel)
        digest = _sha256_file(full)
        return {
            "path": name,
            "resolved": os.path.abspath(full),
            "sha256": digest,
            "note": None if digest else "the registered executable could not be read at this "
                                        "path, so nothing here binds the receipt to its bytes",
        }
    return {
        "path": name,
        "resolved": None,
        "sha256": None,
        "note": "resolved from PATH rather than from this repository, so its bytes are not "
                "hashed: a digest of one machine's interpreter is not reproducible on another",
    }


def _path_matches(pattern, path):
    """Glob matching where `*` stops at a separator and `**` crosses them.

    Delegates to `brothersbe.policy`, which owns this repository's ONE glob
    implementation, so a `covers` glob in the registry and a `paths` glob in the
    policy can never disagree about the same string. Imported inside the
    function on purpose: `policy` imports `evidence`, `evidence` imports this
    module, and a module-level import here would close that ring at load time.
    """
    from .policy import path_matches
    return path_matches(pattern, path)


def covers_match(spec, rel_path):
    """True when `rel_path` is one of the files this registered check covers."""
    for pattern in spec["covers"]:
        if _path_matches(pattern, rel_path):
            return True
    return False


def tracked_files(cwd):
    """Every path git tracks in `cwd`, as repository-relative POSIX strings.

    Null delimited (`-z`), because a path holding a space or a newline is a path
    a line-oriented reader silently splits into two, and half a path matches no
    glob. Raises RegistryUnreadable when git cannot answer, rather than
    returning an empty list: "this repository tracks nothing" and "git did not
    run" are opposite facts and only one of them means a check covers no file.
    """
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=cwd, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    except OSError as exc:
        raise RegistryUnreadable("git ls-files did not run in %s (%s), so the files a "
                                 "registered check covers could not be listed" % (cwd, exc))
    if out.returncode != 0:
        raise RegistryUnreadable(
            "git ls-files exited %d in %s (%s), so the files a registered check covers could "
            "not be listed; an empty coverage list here would be a receipt that proves nothing "
            "about the code" % (out.returncode, cwd,
                                out.stderr.decode("utf-8", "replace").strip() or "no message"))
    raw = out.stdout.decode("utf-8", "replace")
    return [p.replace(os.sep, "/") for p in raw.split("\0") if p]


def covered_paths(cwd, spec):
    """The repository files this registered check is evidence for, sorted.

    Derived from the REGISTRY and the tracked tree, never from a caller's
    `--covers`: a coverage list the caller supplies is the same substitution as
    a command the caller supplies, one field further along.
    """
    return sorted(p for p in tracked_files(cwd) if covers_match(spec, p))


def filtered_environment(source):
    """(the environment a registered check runs in, the names dropped from it).

    Two values and neither is a verdict: the second is the list this run
    REFUSED, recorded in the receipt as a count so a reader can see that the
    process did not inherit the caller's shell. Names, not values, are what this
    returns; nothing here reads or persists what a dropped variable held.
    """
    kept, dropped = {}, []
    for name in sorted(source):
        if name in ENV_ALLOWLIST:
            kept[name] = source[name]
        else:
            dropped.append(name)
    return kept, dropped


def binding_of(cwd, spec, registry_path):
    """Everything a registered run records about WHAT it is about to run.

    Computed before the command starts, so the runner hashes in the receipt are
    the bytes that were about to execute rather than whatever sits there after.
    """
    kept, dropped = filtered_environment(os.environ)
    return {
        "registryPath": os.path.relpath(registry_path, cwd).replace(os.sep, "/"),
        "executable": executable_record(cwd, spec),
        "argv": expected_argv(spec),
        "cwd": spec["command"]["cwd"],
        "runnerFiles": file_digests(cwd, spec["runnerFiles"]),
        "covers": list(spec["covers"]),
        "protectedEvidence": spec["protectedEvidence"],
        "environmentAllowlist": list(ENV_ALLOWLIST),
        "environmentKept": sorted(kept),
        "environmentDropped": len(dropped),
        "environmentNote": "%d variable(s) outside the allowlist were dropped before the "
                           "command started; a check steered by an exported variable is a "
                           "check the caller can change without touching the registry"
                           % len(dropped),
    }, kept


def binding_problems(receipt, cwd, registry_path=None):
    """Every way a receipt's registered-check binding no longer holds, as
    sentences. Empty list means the binding was checked and holds.

    This is the verify-time half of the module and the order is deliberate:
    the registry is read first (a receipt naming a check nobody can look up is
    unverifiable, not merely stale), then the specification digest, then what
    actually ran, then the runner bytes, then coverage. Each failure names the
    receipt field it compared, because "the check does not match" sends an
    operator reading four files to find out which one moved.
    """
    problems = []
    check_id = receipt.get("checkId")
    if not isinstance(check_id, str) or not check_id.strip():
        return problems
    check_id = check_id.strip()
    path = registry_path or default_registry_path(cwd)
    try:
        registry = load_registry(path)
        spec = resolve(registry, check_id)
    except RegistryUnreadable as exc:
        return ["this receipt claims registered check %r and the registry cannot confirm it: %s"
                % (check_id, exc)]

    recorded_spec = receipt.get("checkSpecSha256")
    current_spec = spec_sha256(spec)
    if recorded_spec != current_spec:
        problems.append(
            "the registered specification for %s now digests to %s, not the %s this receipt "
            "recorded: the check was redefined after this evidence was made, so the receipt "
            "describes a check that no longer exists"
            % (check_id, current_spec[:12], str(recorded_spec)[:12]))

    recorded_kind = receipt.get("checkKind")
    if recorded_kind != spec["kind"]:
        problems.append("this receipt records check kind %r and the registry defines %s as %r"
                        % (recorded_kind, check_id, spec["kind"]))

    binding = receipt.get("checkBinding")
    if not isinstance(binding, dict):
        problems.append("this receipt names registered check %s and records no checkBinding "
                        "object, so nothing in it says what actually ran" % check_id)
        return problems

    # Compared against the REDACTED expectation, because that is what the
    # receipt records: `evidence.redact_argv` masks any secret-shaped token
    # before argv is written, so a registered argument in one of those shapes
    # would otherwise make its own receipt unverifiable forever, and the
    # operator would be reading a command-drift message about a command that
    # never drifted. Imported inside the function for the same reason
    # `_path_matches` is: `evidence` imports this module at load time.
    from .evidence import redact_argv
    expected, _redactions, _note = redact_argv(expected_argv(spec))
    if list(receipt.get("argv") or []) != expected:
        problems.append(
            "the recorded command %r is not the registered command %r for %s: a receipt whose "
            "argv drifted from the registry is evidence about a different command"
            % (list(receipt.get("argv") or []), expected, check_id))
    if binding.get("cwd") != spec["command"]["cwd"]:
        problems.append("this receipt ran %s in %r and the registry defines %r"
                        % (check_id, binding.get("cwd"), spec["command"]["cwd"]))

    recorded_runners = binding.get("runnerFiles")
    if not isinstance(recorded_runners, list):
        recorded_runners = []
    recorded_names = [r.get("path") for r in recorded_runners if isinstance(r, dict)]
    if recorded_names != list(spec["runnerFiles"]):
        problems.append(
            "this receipt hashed runner file(s) %s and the registry now names %s for %s: a "
            "renamed runner is a different check"
            % (recorded_names or "none", list(spec["runnerFiles"]) or "none", check_id))
    else:
        for entry in recorded_runners:
            if not isinstance(entry, dict):
                continue
            rel = entry.get("path")
            full = rel if os.path.isabs(rel or "") else os.path.join(cwd, rel or "")
            now = _sha256_file(full)
            if now is None:
                problems.append("runner file %s cannot be read now, so nothing confirms the "
                                "check this receipt describes still exists" % rel)
            elif now != entry.get("sha256"):
                problems.append(
                    "runner file %s now hashes to %s, not the %s this receipt recorded: the "
                    "check was modified after the evidence was made"
                    % (rel, now[:12], str(entry.get("sha256"))[:12]))

    exec_record = binding.get("executable") or {}
    current_exec = executable_record(cwd, spec)
    if current_exec.get("sha256") is not None and exec_record.get("sha256") is not None:
        if current_exec["sha256"] != exec_record["sha256"]:
            problems.append("the registered executable %s now hashes to %s, not the %s this "
                            "receipt recorded"
                            % (current_exec["path"], current_exec["sha256"][:12],
                               str(exec_record.get("sha256"))[:12]))

    covered = receipt.get("coveredFiles") or []
    outside = [c.get("path") for c in covered
               if isinstance(c, dict) and c.get("path")
               and not covers_match(spec, str(c.get("path")).replace(os.sep, "/"))]
    if outside:
        problems.append(
            "this receipt covers %d file(s) the registered check %s does not cover (%s; it "
            "covers %s): a receipt offered for one check while carrying another change's files "
            "is not applicable to it"
            % (len(outside), check_id, ", ".join(str(p) for p in outside[:5]),
               ", ".join(spec["covers"])))
    if not covered:
        problems.append("this receipt names registered check %s and covers no file at all, so "
                        "nothing binds it to the code the check is evidence for" % check_id)
    return problems
