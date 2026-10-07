#!/usr/bin/env python3
"""one_plugin_readiness.py: the submission readiness bars of the one Brother plugin (OP1.f, docs/plan/specs/OP1.md
sections 5.5 and 10, requirements R19 to R22).

Prints one JSON row per bar, {"id", "verdict", "detail"}, with verdict one of PASS, FAIL, NO-DATA, and exits 0 only
when the row ids are exactly the fixed bar set (BARS, or STATIC_BARS under --static) and every row is PASS; 1 when any
row is FAIL; 2 otherwise (a NO-DATA row, an empty report, a missing bar, an unknown id, a repeated id). A bar is
reported, never omitted: an omitted bar reads 2, never 0.

usage (repository root):
  python3 scripts/one_plugin_readiness.py [--static] [--json] [--corpus PATH] [--record PATH] [--root DIR]

--static runs the six bars that need no host binary; the full run adds the seven host bars, and a host binary that is
not on PATH reads NO-DATA, never PASS (hard rule H1). Every static bar calls the function the earlier sub unit owns
(bundle_runtime.manifest_problems, gen_door_table.check_table and retired_namespace_hits, version_source.run_check,
donecheck_u8.catalog_problems), so a bar cannot drift from its control. catalogs-one-plugin reads NO-DATA, awaiting
the 1.1.0 cut, while retire_catalogs.pending is non empty and catalog_problems lists nothing beyond the known pre cut
set (the retiring names beside brother in the Claude and Cursor catalogs, the two product catalogs, the stale
shipped_plugins); any other problem is FAIL; the end state is PASS (R20, decision 9). The triggering record is self
declared and unsigned (decision 7): its row's detail says so, and accepting it as the submission proof is the owner's
act (H3). Unknown, corrupt or unreadable input is a FAIL or a NO-DATA, never a clean read (H2). The control functions
are this checkout's; --root names the tree they are applied to.
"""
import argparse
import contextlib
import glob
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

PASS, FAIL, NO_DATA = "PASS", "FAIL", "NO-DATA"
STATIC_BARS = ("one-manifest-dependency-free", "hooks-guarded", "namespace-clean", "versions-agree",
               "catalogs-one-plugin", "skill-descriptions")
HOST_BARS = ("plugin-validate", "clean-home-install-claude", "clean-home-install-codex",
             "clean-home-install-cursor", "clean-home-install-antigravity", "skill-triggering",
             "hooks-once-transcript")
BARS = STATIC_BARS + HOST_BARS

CORPUS_REL = os.path.join("docs", "plan", "one-plugin-trigger-corpus.json")
RECORD_REL = os.path.join("docs", "plan", "evidence", "one-plugin-triggering.jsonl")
HP1_EVIDENCE_REL = os.path.join("docs", "plan", "evidence", "HP1-host-live.jsonl")
ANTIGRAVITY_RESOLVER_REL = os.path.join("tests", "e2e", "antigravity", "run_e2e.py")
UNSIGNED = "owner record, unsigned"
MIN_ASKS = 21
RETIRED_PLUGINS = ("brothermode", "brothersbe", "brotherds")
PROVENANCE = ("host", "claude_version", "ts")

_DESCRIPTION = re.compile(r"^description:[ \t]*(.*)$", re.M)


def _row(bar, verdict, detail):
    return {"id": bar, "verdict": verdict, "detail": detail}


def _lines(problems, limit=6):
    """The first problems joined for a detail field, the rest counted so none is hidden."""
    shown = "; ".join(str(p) for p in problems[:limit])
    if len(problems) > limit:
        shown += "; and %d more" % (len(problems) - limit)
    return shown


def _read_json(path):
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _which(name, env):
    return shutil.which(name, path=env.get("PATH", ""))


def manifest_row(root):
    """one-manifest-dependency-free: bundle_runtime.manifest_problems over bundle/, the one reader (R8)."""
    bar = "one-manifest-dependency-free"
    try:
        import bundle_runtime
        problems = bundle_runtime.manifest_problems(os.path.join(root, "bundle"))
    except Exception as exc:
        return _row(bar, NO_DATA, "manifest_problems did not run: %s" % exc)
    if problems:
        return _row(bar, FAIL, _lines(problems))
    return _row(bar, PASS, "every host manifest under bundle/ is readable and declares no dependencies")


def _hook_commands(path):
    """Every command string of one hooks.json. Raises ValueError on a file that is unreadable, not JSON or not the
    hooks shape (an event map of matcher groups, each carrying a hooks list of command entries)."""
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("%s: %s" % (path, exc))
    events = doc.get("hooks") if isinstance(doc, dict) else None
    if not isinstance(events, dict):
        raise ValueError("%s: no hooks object" % path)
    commands = []
    for event, groups in events.items():
        if not isinstance(groups, list):
            raise ValueError("%s: %s is not a list of matcher groups" % (path, event))
        for group in groups:
            hooks = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(hooks, list):
                raise ValueError("%s: a %s group carries no hooks list" % (path, event))
            for hook in hooks:
                command = hook.get("command") if isinstance(hook, dict) else None
                if not isinstance(command, str) or not command:
                    raise ValueError("%s: a %s hook carries no command string" % (path, event))
                commands.append(command)
    return commands


def hooks_row(root):
    """hooks-guarded: every command of bundle/hooks/hooks.json runs through the guard, and their count equals the
    count the products register (23 of 23 on 2026-10-06), so a dropped or an unwrapped hook is a FAIL. A products
    file that cannot be read leaves the expected count unknown, NO-DATA; a bundle file that cannot be read is FAIL."""
    bar = "hooks-guarded"
    try:
        import bundle_runtime as B
        product_total = 0
        for product in B.HOOK_PRODUCTS:
            product_total += len(_hook_commands(
                os.path.join(root, "products", product, "hooks", B.PRODUCT_HOOKS_JSON_NAME)))
        guarded = re.compile(r'^python3 "\$\{CLAUDE_PLUGIN_ROOT\}/runtime/hooks/'
                             + re.escape(B.HOOK_GUARD_NAME) + r'" ')
        bundle_path = os.path.join(root, "bundle", "hooks", B.HOOKS_JSON_NAME)
    except Exception as exc:
        return _row(bar, NO_DATA, "the product hook count could not be established: %s" % exc)
    if product_total == 0:
        return _row(bar, NO_DATA, "the products register no hook command, so there is nothing to guard")
    try:
        commands = _hook_commands(bundle_path)
    except ValueError as exc:
        return _row(bar, FAIL, "the bundle hooks file is not readable as hooks: %s" % exc)
    wrapped = [c for c in commands if guarded.match(c)]
    detail = "%d of %d bundle hook commands run through %s; the products register %d" % (
        len(wrapped), len(commands), B.HOOK_GUARD_NAME, product_total)
    if len(wrapped) == len(commands) == product_total:
        return _row(bar, PASS, detail)
    return _row(bar, FAIL, detail)


def namespace_row(root):
    """namespace-clean: gen_door_table.check_table (the door, its generated table, every /brother:<name> mention)
    plus retired_namespace_hits over every folder a user can read (R13, R14, R15)."""
    bar = "namespace-clean"
    try:
        import gen_door_table as G
    except Exception as exc:
        return _row(bar, NO_DATA, "gen_door_table did not import: %s" % exc)
    problems = list(G.check_table(root))
    try:
        hits = G.retired_namespace_hits(root, G.USER_SCOPES)
    except ValueError as exc:
        hits = []
        problems.append(str(exc))
    problems.extend("%s:%d speaks a retired namespace" % (rel, number) for rel, number, _ in hits)
    if problems:
        return _row(bar, FAIL, _lines(problems))
    return _row(bar, PASS, "the door table is current and no retired namespace is readable under bundle/")


def versions_row(root):
    """versions-agree: version_source.run_check, which reads every host manifest that host_manifests discovers and
    every umbrella carrier against the one source version (R12); its 0, 1, 2 are PASS, FAIL, NO-DATA."""
    bar = "versions-agree"
    out = io.StringIO()
    try:
        import version_source
        with contextlib.redirect_stdout(out):
            code = version_source.run_check(Path(root))
    except Exception as exc:
        return _row(bar, NO_DATA, "version_source.run_check did not run: %s" % exc)
    said = [line for line in out.getvalue().splitlines() if not line.startswith("PASS")]
    if code == 0:
        return _row(bar, PASS, "every host manifest and umbrella carrier agrees with the source version")
    if code == 2:
        return _row(bar, NO_DATA, _lines(said) or "run_check exited 2")
    return _row(bar, FAIL, _lines(said) or "run_check exited %d" % code)


def _known_pre_cut(root, problem, pending, D, R):
    """True when `problem` is a line catalog_problems is expected to print before the 1.1.0 cut (R20): a product
    catalog still present, the Claude or Cursor catalog still publishing the retiring names beside brother, or
    bundle/MANIFEST.json still listing them, each only while retire_catalogs.pending names the change. Anything
    else (a sixth catalog, a wrong ref, an unreadable file, a foreign name) is not pre cut."""
    allowed = set(RETIRED_PLUGINS) | {D.KEEP}
    for rel in pending:
        if rel in R.DELETE and problem.startswith("extra catalog %s:" % rel):
            return True
        if rel in R.CATALOGS and problem.startswith("%s publishes " % rel):
            try:
                names = D.plugins(os.path.join(root, rel))
            except (OSError, ValueError, TypeError):
                return False
            return D.KEEP in names and set(names) <= allowed
    if pending and problem.startswith("%s shipped_plugins is " % D.MANIFEST_REL):
        try:
            shipped = _read_json(os.path.join(root, D.MANIFEST_REL)).get("shipped_plugins")
        except (OSError, ValueError, AttributeError):
            return False
        return isinstance(shipped, list) and D.KEEP in shipped and set(shipped) <= allowed
    return False


def catalogs_row(root):
    """catalogs-one-plugin: donecheck_u8.catalog_problems, the ONE reader of the end state (decision 9). [] is
    PASS. Before the cut the known pre cut set reads NO-DATA, awaiting the 1.1.0 cut; any other problem is FAIL."""
    bar = "catalogs-one-plugin"
    try:
        import donecheck_u8 as D
        import retire_catalogs as R
        problems = D.catalog_problems(root)
    except Exception as exc:
        return _row(bar, NO_DATA, "catalog_problems did not run: %s" % exc)
    if not problems:
        return _row(bar, PASS, "one plugin, brother, in every catalog and in bundle/MANIFEST.json")
    try:
        pending = R.pending(root)
    except Exception as exc:
        return _row(bar, FAIL, "retire_catalogs.pending could not read the catalogs (%s); %s"
                    % (exc, _lines(problems)))
    unexpected = [p for p in problems if not _known_pre_cut(root, p, pending, D, R)]
    if unexpected:
        return _row(bar, FAIL, _lines(unexpected))
    return _row(bar, NO_DATA, "awaiting the 1.1.0 cut: %d catalog change(s) pending, %d known pre cut problem(s)"
                % (len(pending), len(problems)))


def _description(front):
    """The description value of a frontmatter block: a one line scalar with its quotes stripped, or the indented
    lines of a block scalar joined; "" when absent or empty."""
    found = _DESCRIPTION.search(front)
    if found is None:
        return ""
    value = found.group(1).strip()
    if value in (">", "|", ">-", "|-", ">+", "|+"):
        lines = []
        for line in front[found.end():].splitlines():
            if line.startswith((" ", "\t")):
                lines.append(line.strip())
            elif line.strip():
                break
        value = " ".join(lines)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def descriptions_row(root):
    """skill-descriptions: every bundle/skills/*/SKILL.md carries frontmatter with a name and a non empty
    description, and no description names a retired command (the OP1.d property, gen_door_table.RETIRED_NAMESPACE)."""
    bar = "skill-descriptions"
    try:
        import gen_door_table as G
    except Exception as exc:
        return _row(bar, NO_DATA, "gen_door_table did not import: %s" % exc)
    paths = sorted(glob.glob(os.path.join(root, "bundle", "skills", "*", "SKILL.md")))
    if not paths:
        return _row(bar, NO_DATA, "no SKILL.md under bundle/skills, nothing to read")
    problems = []
    for path in paths:
        rel = os.path.relpath(path, root)
        try:
            G.read_skill(path)
            with open(path, "rb") as fh:
                text = fh.read().decode("utf-8")
        except (OSError, ValueError) as exc:
            problems.append("%s: %s" % (rel, exc))
            continue
        value = _description(text[:text.find("\n---", 3)])
        if not value:
            problems.append("%s: no description" % rel)
        elif re.search(G.RETIRED_NAMESPACE, value):
            problems.append("%s: the description names a retired command" % rel)
    if problems:
        return _row(bar, FAIL, _lines(problems))
    return _row(bar, PASS, "%d skill descriptions, each present and free of a retired command" % len(paths))


def plugin_validate_row(root, env):
    """plugin-validate: `claude plugin validate bundle --strict`, judged on its exit code; no claude is NO-DATA."""
    bar = "plugin-validate"
    claude = _which("claude", env)
    if not claude:
        return _row(bar, NO_DATA, "no claude binary on PATH")
    try:
        proc = subprocess.run([claude, "plugin", "validate", "bundle", "--strict"], cwd=root, env=env,
                              capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return _row(bar, NO_DATA, "claude plugin validate did not run: %s" % exc)
    tail = " / ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
    if proc.returncode == 0:
        return _row(bar, PASS, "claude plugin validate bundle --strict exited 0: %s" % tail)
    return _row(bar, FAIL, "claude plugin validate bundle --strict exited %d: %s" % (proc.returncode, tail))


def _last_line(proc):
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return lines[-1] if lines else ""


def install_claude_row(root, env):
    """clean-home-install-claude: scripts/bundle-install-smoke.sh in its own throwaway CLAUDE_CONFIG_DIR; its exit
    2 (BLOCKED, no client) is NO-DATA, 1 is FAIL, 0 is PASS."""
    bar = "clean-home-install-claude"
    if not _which("claude", env):
        return _row(bar, NO_DATA, "no claude binary on PATH")
    script = os.path.join(root, "scripts", "bundle-install-smoke.sh")
    if not os.path.isfile(script):
        return _row(bar, NO_DATA, "no %s" % script)
    try:
        proc = subprocess.run(["sh", script], cwd=root, env=env, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return _row(bar, NO_DATA, "bundle-install-smoke.sh did not run: %s" % exc)
    if proc.returncode == 0:
        return _row(bar, PASS, "bundle-install-smoke.sh exited 0: %s" % _last_line(proc))
    if proc.returncode == 2:
        return _row(bar, NO_DATA, "bundle-install-smoke.sh exited 2 (BLOCKED): %s" % _last_line(proc))
    return _row(bar, FAIL, "bundle-install-smoke.sh exited %d: %s" % (proc.returncode, _last_line(proc)))


def _suite_row(bar, script_rel, root, env):
    """One unittest suite run as a child: exit 0 with no skipped case is PASS; exit 0 with a skipped case is
    NO-DATA, because a skip is a case that did not run, never a pass; any other exit is FAIL."""
    script = os.path.join(root, script_rel)
    if not os.path.isfile(script):
        return _row(bar, NO_DATA, "no %s" % script)
    try:
        proc = subprocess.run([sys.executable, "-B", script, "-v"], cwd=root, env=env,
                              capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        return _row(bar, NO_DATA, "%s did not run: %s" % (script_rel, exc))
    last = _last_line(proc)
    if proc.returncode == 0 and "skipped=" not in proc.stdout + proc.stderr:
        return _row(bar, PASS, "%s exited 0: %s" % (script_rel, last))
    if proc.returncode == 0:
        return _row(bar, NO_DATA, "%s skipped a case, so it did not prove the host: %s" % (script_rel, last))
    return _row(bar, FAIL, "%s exited %d: %s" % (script_rel, proc.returncode, last))


def install_codex_row(root, env):
    """clean-home-install-codex: the Codex binary by the one rule (brother_paths.codex_bin, never PATH) and `uv`
    for the canonical validator must both exist, else NO-DATA; then scripts/test_codex_package.py."""
    bar = "clean-home-install-codex"
    try:
        import brother_paths
        codex = brother_paths.codex_bin(env)
    except Exception as exc:
        return _row(bar, NO_DATA, "brother_paths.codex_bin did not answer: %s" % exc)
    if not (isinstance(codex, str) and os.path.isfile(codex) and os.access(codex, os.X_OK)):
        return _row(bar, NO_DATA, "no Codex binary at %s" % codex)
    if not _which("uv", env):
        return _row(bar, NO_DATA, "no uv on PATH, so the canonical Codex validator cannot run")
    return _suite_row(bar, os.path.join("scripts", "test_codex_package.py"), root, env)


def install_cursor_row(root, env):
    """clean-home-install-cursor: the cursor-agent binary on PATH, else NO-DATA; then scripts/test_cursor_plugin.py."""
    bar = "clean-home-install-cursor"
    if not _which("cursor-agent", env):
        return _row(bar, NO_DATA, "no cursor-agent binary on PATH")
    return _suite_row(bar, os.path.join("scripts", "test_cursor_plugin.py"), root, env)


def _antigravity_bin(root):
    """(absolute binary, "") from the one Antigravity resolver, resolve_host in tests/e2e/antigravity/run_e2e.py,
    loaded from its file the way host_live_proof.py loads it; (None, why) when it does not load or resolve."""
    path = os.path.join(root, ANTIGRAVITY_RESOLVER_REL)
    try:
        spec = importlib.util.spec_from_file_location("op1f_run_e2e", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        found = mod.resolve_host()[0]
    except Exception as exc:
        return None, "%s" % exc
    return found, ""


def install_antigravity_row(root, env):
    """clean-home-install-antigravity: NO-DATA in 1.1.0, with the reason named. The host binary is resolved by the
    one resolver; the HP1 harness (host_live_proof.py) starts a host session only by the owner's HP1_LIVE=1
    command, never from a script; and Antigravity ships experimental and unverified in 1.1.0 (the OP1 objective),
    its certification being 1.1.1. The row says which of the binary and the HP1 evidence file this tree has."""
    bar = "clean-home-install-antigravity"
    found, why = _antigravity_bin(root)
    if not found:
        return _row(bar, NO_DATA, "no Antigravity host binary: %s" % why)
    present = os.path.isfile(os.path.join(root, HP1_EVIDENCE_REL))
    return _row(bar, NO_DATA, "Antigravity ships experimental and unverified in 1.1.0 (certification in 1.1.1); "
                "binary %s; the HP1 harness runs only by the owner's HP1_LIVE=1 command; evidence %s is %s"
                % (found, HP1_EVIDENCE_REL, "present" if present else "absent"))


def bundle_skills(root):
    """The bundle/skills directory names, the only values an `expect` may take."""
    return {os.path.basename(os.path.dirname(p))
            for p in glob.glob(os.path.join(root, "bundle", "skills", "*", "SKILL.md"))}


def corpus_problems(corpus, skills):
    """Every way the trigger corpus can be wrong (R22): not an object, no asks, an ask that is not an object or
    carries no string ask, a repeated ask, an expect that names no bundle skill, fewer than MIN_ASKS asks, a door
    verb no ask expects, a retired namespace anywhere in it. [] is a valid corpus."""
    if not isinstance(corpus, dict):
        return ["the corpus is not a JSON object"]
    asks = corpus.get("asks")
    if not isinstance(asks, list) or not asks:
        return ["the corpus carries no asks list, or an empty one"]
    problems = []
    seen = set()
    for index, ask in enumerate(asks):
        if not isinstance(ask, dict):
            problems.append("ask %d is not an object" % index)
            continue
        text = ask.get("ask")
        if not isinstance(text, str) or not text.strip():
            problems.append("ask %d carries no string ask" % index)
        elif text in seen:
            problems.append("ask %d repeats an earlier ask" % index)
        else:
            seen.add(text)
        expect = ask.get("expect")
        if not isinstance(expect, str) or expect not in skills:
            problems.append("ask %d expects %r, which is no bundle skill" % (index, expect))
    if len(asks) < MIN_ASKS:
        problems.append("%d asks, at least %d needed" % (len(asks), MIN_ASKS))
    try:
        import gen_door_table as G
    except Exception as exc:
        return problems + ["gen_door_table did not import: %s" % exc]
    expects = [a.get("expect") for a in asks if isinstance(a, dict) and isinstance(a.get("expect"), str)]
    for verb in G.CORE_ORDER:
        if not any(e.endswith("-" + verb) for e in expects):
            problems.append("no ask expects the door verb %s" % verb)
    if re.search(G.RETIRED_NAMESPACE, json.dumps(corpus)):
        problems.append("the corpus names a retired namespace")
    return problems


def _invoked(value):
    """The invoked value with one leading `brother:` removed, or None when it is not a non empty string."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith("brother:"):
        value = value[len("brother:"):]
    return value


def triggering_row(corpus, rows, skills=None):
    """One row for skill-triggering (R21, R22). The corpus is judged first: a bad corpus is FAIL whatever the record
    says. `rows` None means no record: NO-DATA. A record covering fewer asks than the corpus is NO-DATA. A row
    lacking host, claude_version or ts, an invoked value that is missing or names a retired namespace, or ANY ask
    routed to a skill other than its expect (zero misroutes) is FAIL. Full coverage with zero misroutes is PASS, and
    every verdict's detail says the record is an owner record, unsigned (decision 7)."""
    bar = "skill-triggering"
    if skills is None:
        skills = bundle_skills(ROOT)
    problems = corpus_problems(corpus, skills)
    if problems:
        return _row(bar, FAIL, "corpus invalid: " + _lines(problems))
    try:
        import gen_door_table as G
    except Exception as exc:
        return _row(bar, NO_DATA, "gen_door_table did not import: %s" % exc)
    asks = corpus["asks"]
    if rows is None:
        return _row(bar, NO_DATA, "no triggering record; %s" % UNSIGNED)
    if not isinstance(rows, list):
        return _row(bar, FAIL, "the triggering record is not a list of rows; %s" % UNSIGNED)
    by_ask = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("ask"), str):
            by_ask[row["ask"]] = row
    missing = [a["ask"] for a in asks if a["ask"] not in by_ask]
    if missing:
        return _row(bar, NO_DATA, "the record covers %d of %d asks; %s"
                    % (len(asks) - len(missing), len(asks), UNSIGNED))
    misrouted = []
    for ask in asks:
        row = by_ask[ask["ask"]]
        for key in PROVENANCE:
            if not isinstance(row.get(key), str) or not row[key].strip():
                return _row(bar, FAIL, "the row for %r carries no %s; %s" % (ask["ask"], key, UNSIGNED))
        invoked = _invoked(row.get("invoked"))
        if invoked is None:
            return _row(bar, FAIL, "the row for %r carries no invoked skill; %s" % (ask["ask"], UNSIGNED))
        if re.search(G.RETIRED_NAMESPACE, invoked):
            return _row(bar, FAIL, "the row for %r invoked a retired namespace, %s; %s"
                        % (ask["ask"], invoked, UNSIGNED))
        if invoked != ask["expect"]:
            misrouted.append("%r went to %s, expected %s" % (ask["ask"], invoked, ask["expect"]))
    if misrouted:
        return _row(bar, FAIL, "%d of %d asks misrouted (zero allowed): %s; %s"
                    % (len(misrouted), len(asks), _lines(misrouted, 3), UNSIGNED))
    return _row(bar, PASS, "%s; %d asks, each routed to its expected skill, zero misroutes" % (UNSIGNED, len(asks)))


def _read_record(path):
    """The rows of a JSONL record, or None when the file is absent. A present file that cannot be read, or a line
    that is not JSON, raises ValueError: a record that exists and cannot be read is a defect, never NO-DATA."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read().decode("utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("%s: %s" % (path, exc))
    rows = []
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
            raise ValueError("line %d is not JSON: %s" % (number, exc))
    return rows


def triggering_bar(root, corpus_path, record_path):
    """skill-triggering from files: the corpus (or --corpus) and the record (or --record), handed to triggering_row."""
    bar = "skill-triggering"
    try:
        corpus = _read_json(corpus_path)
    except (OSError, ValueError) as exc:
        return _row(bar, FAIL, "corpus invalid: %s cannot be read (%s)" % (corpus_path, exc))
    skills = bundle_skills(root)
    try:
        rows = _read_record(record_path)
    except ValueError as exc:
        problems = corpus_problems(corpus, skills)
        if problems:
            return _row(bar, FAIL, "corpus invalid: " + _lines(problems))
        return _row(bar, FAIL, "the triggering record is present and unreadable: %s; %s" % (exc, UNSIGNED))
    return triggering_row(corpus, rows, skills)


def transcript_row():
    """hooks-once-transcript: NO-DATA until a real session transcript is recorded (spec 5.5, OP1.f)."""
    return _row("hooks-once-transcript", NO_DATA,
                "no session transcript recorded; the hooks once proof is a live session on the owner's machine")


def readiness(root, static_only, record_path, env, corpus_path=None):
    """Every bar row of one run: the six static bars, then the seven host bars unless static_only. Each row is PASS,
    FAIL or NO-DATA; no bar raises on the report's behalf."""
    root = os.path.abspath(os.fspath(root))
    rows = [manifest_row(root), hooks_row(root), namespace_row(root), versions_row(root), catalogs_row(root),
            descriptions_row(root)]
    if static_only:
        return rows
    rows += [plugin_validate_row(root, env), install_claude_row(root, env), install_codex_row(root, env),
             install_cursor_row(root, env), install_antigravity_row(root, env),
             triggering_bar(root, corpus_path or os.path.join(root, CORPUS_REL),
                            record_path or os.path.join(root, RECORD_REL)),
             transcript_row()]
    return rows


def verdict(rows):
    """The exit code of a report: 1 when any row is FAIL; 0 only when the row ids are exactly STATIC_BARS or BARS,
    none repeated, and every verdict is PASS; 2 otherwise. An empty list, a missing bar, an unknown or repeated id,
    a NO-DATA row and a malformed row all read 2, never 0: a bar is reported, never omitted."""
    rows = rows if isinstance(rows, list) else []
    dicts = [r for r in rows if isinstance(r, dict)]
    if any(r.get("verdict") == FAIL for r in dicts):
        return 1
    if len(dicts) != len(rows):
        return 2
    ids = [r.get("id") for r in dicts]
    if len(set(ids)) != len(ids) or set(ids) not in (set(STATIC_BARS), set(BARS)):
        return 2
    if all(r.get("verdict") == PASS for r in dicts):
        return 0
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="the one plugin submission readiness bars, one JSON row each")
    parser.add_argument("--static", action="store_true", help="only the six bars that need no host binary")
    parser.add_argument("--json", action="store_true", help="one JSON array instead of one JSON object per line")
    parser.add_argument("--corpus", default=None, help="the trigger corpus (default %s)" % CORPUS_REL)
    parser.add_argument("--record", default=None, help="the triggering record (default %s)" % RECORD_REL)
    parser.add_argument("--root", default=ROOT, help="the tree to judge (default: this checkout)")
    args = parser.parse_args(argv)
    rows = readiness(args.root, args.static, args.record, dict(os.environ), args.corpus)
    if args.json:
        sys.stdout.write(json.dumps(rows) + "\n")
    else:
        for row in rows:
            sys.stdout.write(json.dumps(row) + "\n")
    return verdict(rows)


if __name__ == "__main__":
    sys.exit(main())
