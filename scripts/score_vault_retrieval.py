#!/usr/bin/env python3
"""score_vault_retrieval: retrieval quality for the Brother Vault, measured through
the real point of need call, never a re-implementation of it.

WHAT IT RUNS. For every query in the corpus, exactly the subprocess
products/brothermode/tools/vault_recall_hook.py makes today:

    [sys.executable, TOOL, "check", "--paths", <basename>,
     "--context", <the edited file's repo-relative path>, "--limit", <limit>]

with stderr discarded, the same way the hook discards it. The context is NOT
re-derived here: _context_path is imported from the hook itself, so a benchmark
that claims to make the caller's call cannot drift from it. VR4 adds --project: the
corpus row names the project, and the row is what is sent, because every fixture
path is deliberately in no git repository and the hook's own git-root route can
therefore name nothing for it. project_of stays as the fallback for a corpus that
names no project of its own.

AN OPTION THE TOOL DOES NOT LIST IS OMITTED, never an error. The options are read
off the tool under test (its own --help text), because an older bm_vault.py is a
valid arm of a before and after comparison and a scorer that crashed on one could
not measure the change. Which options were sent is printed and written into the
result, and the exact argv is recorded on every per-query row, so the call is read
off the run rather than assumed from this paragraph.

WHAT IT SCORES. The corpus names, per query, the ONE note that is the recorded
lesson for that file (primary_path) and every note that merely carries the same
anchor (expected_paths). Ranks are read off the tool's own printed hit blocks:

  raw_rank      position among every block the tool printed
  served_rank   position among blocks that were NOT withheld

Scoring uses served_rank, because a withheld note never reaches the model. The
two are reported side by side so a change that only moves notes into the withheld
pile is visible as such.

ISOLATION. The vault is copied into a temporary directory and indexed there, with
BROTHER_CONFIG_DIR, HOME and BM_FRESHNESS_STATE all pointed at that directory, so
this tool never reads or writes the machine's real index at
~/.claude/bm_vault_index.sqlite3, the real vault, or the real freshness state.
BM_FRESHNESS_ROOTS points at the fixture's own stub source tree: an anchored note
whose citation resolves nowhere is WITHHELD as stale, which would make the whole
fixture unservable and score a filesystem accident instead of retrieval.

EXIT 0 ALWAYS. A corpus with fewer than 30 queries, a missing vault, or a missing
tool prints NO-DATA and writes an aggregate of NULLS, never zeros: a population
that cannot be measured must not compose into a score, and zeros compose.

Python 3.9, standard library only, no network. No em or en dashes anywhere.

Usage:
    python3 scripts/score_vault_retrieval.py --out /tmp/vr0-k5.json
    python3 scripts/score_vault_retrieval.py --limit 2 --k 2 --out /tmp/vr0-l2.json
"""
import argparse
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

DEFAULT_VAULT = os.path.join(REPO, "benchmarks", "fixtures", "vault-retrieval", "vault")
DEFAULT_CORPUS = os.path.join(REPO, "benchmarks", "retrieval", "vault-retrieval-v1.json")
DEFAULT_TOOL = os.path.join(REPO, "products", "brothermode", "tools", "bm_vault.py")
DEFAULT_HOOK = os.path.join(REPO, "products", "brothermode", "tools", "vault_recall_hook.py")
#: The options this scorer will send when the tool under test lists them, in the
#: order the hook sends them. Anything not listed by the tool is left out.
OPTIONAL_FLAGS = ("--context", "--project")

MIN_QUERIES = 30
RECALL_POINTS = (1, 2, 5)
NDCG_AT = 5
TIMEOUT_S = 120
#: Every fixture note is stamped with this mtime before indexing, so a checkout time
#: cannot move a score. bm_vault_decay scales a fused score by note age, and a corpus
#: whose ages depend on when git wrote the files is not deterministic.
FIXED_MTIME = 1735689600.0      # 2025-01-01T00:00:00Z
WITHHELD_MARKER = "\x00BM-VAULT-WITHHELD\x00"
CONTENT_FALLBACK_PREFIX = "possible pattern match (content, not filename):"


def context_deriver(hook):
    """(fn, note): the hook's OWN _context_path, imported by path.

    Never re-implemented here. The whole claim of this benchmark is that it makes
    the call the point of need hook makes, and a second copy of that derivation
    would be a second opinion about what the hook sends: the day the hook changed
    from a bare basename (62f49ded) to a basename plus a context, a copy here
    would have kept scoring the old call and reported it as the new one.

    fn is None when the hook cannot be imported, and the run then sends no
    --context and says so in its own output, rather than guessing one."""
    if not os.path.isfile(hook):
        return None, "no vault_recall_hook.py at %r, so no --context is sent" % hook
    try:
        spec = importlib.util.spec_from_file_location("vault_recall_hook_for_context", hook)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._context_path, ""
    except Exception as e:                      # an unimportable hook is NO-DATA about
        return None, "vault_recall_hook.py did not import (%s)" % e   # the context, never a crash


def tool_options(tool):
    """The subset of OPTIONAL_FLAGS the tool under test names in its own help text.

    Both help surfaces are read because bm_vault.py has two: `check --help` prints
    a usage line (its argv parser is hand rolled and accepts any --flag silently,
    so probing by sending the flag would pass on every tool ever written), and the
    bare `--help` prints the module docstring, which is where the check command's
    options are documented. A flag named in neither is not sent."""
    text = ""
    for argv in (["check", "--help"], ["--help"]):
        try:
            proc = subprocess.run([sys.executable, tool] + argv, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired):  # sbe: allow-silent a failed help probe is tolerated because the mandatory check invocation still follows
            continue
        text += proc.stdout.decode("utf-8", "replace")
        text += proc.stderr.decode("utf-8", "replace")
    return tuple(flag for flag in OPTIONAL_FLAGS if flag in text)


def project_of(file_path):
    """The git root's basename for the edited file, which is what VR4's --project
    resolves to when the caller does not name one.

    Empty for a path in no repository, and an empty project is OMITTED from the
    argv rather than sent as a guess. HOME is refused as a root for the same reason
    the hook refuses it: a dotfiles checkout makes ~ a git root, and "everything
    under my home directory" is not a project."""
    parent = os.path.dirname(os.path.abspath(file_path))
    if not os.path.isdir(parent):
        return ""
    try:
        proc = subprocess.run(["git", "-C", parent, "rev-parse", "--show-toplevel"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    root = proc.stdout.decode("utf-8", "replace").strip()
    if not root:
        return ""
    try:
        if os.path.realpath(root) == os.path.realpath(os.path.expanduser("~")):
            return ""
    except Exception:
        return ""
    return os.path.basename(root)


def check_argv(tool, query_sent, context, project, limit, options):
    """The exact argv for one query, in the order the hook writes it. A flag whose
    value is empty, or whose name the tool did not list, is left out."""
    argv = [sys.executable, tool, "check", "--paths", query_sent]
    if context and "--context" in options:
        argv += ["--context", context]
    if project and "--project" in options:
        argv += ["--project", project]
    return argv + ["--limit", str(limit)]


def load_corpus(path):
    """(doc, None) or (None, reason). Never raises: an unreadable corpus is a
    NO-DATA answer at the caller, not a traceback."""
    if not path or not os.path.isfile(path):
        return None, "no corpus file at %r" % path
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        return None, "%r is not readable JSON (%s)" % (path, e)
    if not isinstance(doc, dict) or not isinstance(doc.get("queries"), list):
        return None, "%r carries no queries list" % path
    return doc, None


def _sources_dir(vault):
    """The fixture's stub source tree, a sibling of the vault, or the vault itself
    when there is none (a real vault's anchors resolve against a real repo)."""
    sib = os.path.join(os.path.dirname(os.path.abspath(vault)), "sources")
    return sib if os.path.isdir(sib) else os.path.abspath(vault)


def _env(work, vault, sources):
    env = dict(os.environ)
    env.update({
        "HOME": os.path.join(work, "home"),
        "BROTHER_CONFIG_DIR": os.path.join(work, "cfg"),
        "BM_VAULT_ROOT": vault,
        "BM_FRESHNESS_STATE": os.path.join(work, "cfg", "freshness.sqlite3"),
        "BM_FRESHNESS_ROOTS": sources,
    })
    # A stray seam left in the caller's environment would silently change what the
    # tool serves, and a benchmark that inherits one is measuring the seam.
    for key in list(env):
        if key.startswith("BM_VAULT_DISABLE_"):
            del env[key]
    return env


def _stage(vault, work):
    """The vault copied under `work`, every note stamped with FIXED_MTIME. Copying
    rather than indexing in place keeps this tool free of any write into the
    repository, which is the same posture bm_vault_jbench.py takes."""
    staged = os.path.join(work, "vault")
    shutil.copytree(vault, staged)
    for dirpath, _dirs, files in os.walk(staged):
        for fn in files:
            os.utime(os.path.join(dirpath, fn), (FIXED_MTIME, FIXED_MTIME))
    return staged


def parse_hits(text, vault_abs):
    """[{path, withheld, matched_on, header}] in printed order, plus the count the
    tool said it did not show.

    A hit block opens on a line indented exactly two spaces (bm_vault.py's
    _print_hits prints every title that way, withheld or served) and its detail
    lines are indented four. The note path is recognised by its prefix, not by a
    filename pattern: a real vault path can contain a space, which is the parser
    defect RR3's dry run hit. Blocks after the content fallback banner answer a
    different question (this file's content, not its name) and are not counted."""
    hits, current, truncated = [], None, 0
    for line in text.splitlines():
        if line.startswith(CONTENT_FALLBACK_PREFIX):
            break
        if line.startswith("Vault: ") and " more lesson" in line:
            head = line[len("Vault: "):].split(" ", 1)[0]
            try:
                truncated = int(head)
            except ValueError:
                truncated = 0
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith("    "):
            if current is None:
                continue
            if WITHHELD_MARKER in line:
                current["withheld"] = True
            elif stripped.startswith("matched on: "):
                current["matched_on"] = [p.strip() for p in
                                         stripped[len("matched on: "):].split(",")]
            elif stripped.startswith(vault_abs + os.sep):
                current["path"] = stripped
            continue
        if line.startswith("  "):
            current = {"header": stripped, "withheld": stripped.startswith("WITHHELD"),
                       "path": None, "matched_on": []}
            hits.append(current)
    return [h for h in hits if h["path"]], truncated


def _rank_of(blocks, wanted, served_only):
    for i, block in enumerate(b for b in blocks if not (served_only and b["withheld"])):
        if block["path"] in wanted:
            return i + 1
    return None


def _ndcg(served, primary, expected, cut):
    gains = []
    for block in served[:cut]:
        if block["path"] == primary:
            gains.append(2.0)
        elif block["path"] in expected:
            gains.append(1.0)
        else:
            gains.append(0.0)
    dcg = sum(g / math.log2(r + 2) for r, g in enumerate(gains))
    ideal = ([2.0] + [1.0] * (max(len(expected), 1) - 1))[:NDCG_AT]
    idcg = sum(g / math.log2(r + 2) for r, g in enumerate(ideal))
    return (dcg / idcg) if idcg else None


def _null_aggregate():
    agg = {"n": 0}
    for point in RECALL_POINTS:
        agg["recall@%d" % point] = None
    agg["MRR"] = None
    agg["nDCG@%d" % NDCG_AT] = None
    return agg


def _aggregate(rows, horizon):
    agg = {"n": len(rows)}
    if not rows:
        for point in RECALL_POINTS:
            agg["recall@%d" % point] = None
        agg["MRR"] = None
        agg["nDCG@%d" % NDCG_AT] = None
        return agg
    for point in RECALL_POINTS:
        if point > horizon:
            agg["recall@%d" % point] = None       # never observed, so never a zero
            continue
        hit = sum(1 for r in rows
                  if r["served_rank"] is not None and r["served_rank"] <= point)
        agg["recall@%d" % point] = round(hit / float(len(rows)), 4)
    agg["MRR"] = round(sum(r["rr"] for r in rows) / float(len(rows)), 4)
    ndcgs = [r["ndcg"] for r in rows if r["ndcg"] is not None]
    agg["nDCG@%d" % NDCG_AT] = round(sum(ndcgs) / float(len(ndcgs)), 4) if ndcgs else None
    return agg


def _grouped(rows, key, horizon):
    out = {}
    for row in rows:
        out.setdefault(row[key], []).append(row)
    return dict((k, _aggregate(v, horizon)) for k, v in sorted(out.items()))


def run(vault, corpus_path, tool, k, limit, out_path, hook=None):
    hook = DEFAULT_HOOK if hook is None else hook
    doc, why = load_corpus(corpus_path)
    problems = []
    if doc is None:
        problems.append(why)
    if not os.path.isdir(vault):
        problems.append("no vault directory at %r" % vault)
    if not os.path.isfile(tool):
        problems.append("no bm_vault.py at %r" % tool)
    if doc is not None and len(doc["queries"]) < MIN_QUERIES:
        problems.append("corpus holds %d queries, below the floor of %d"
                        % (len(doc["queries"]), MIN_QUERIES))
    if problems:
        for problem in problems:
            print("NO-DATA: %s" % problem)
        print("NO-DATA: nothing measured, aggregates written as null (never zero)")
        _write(out_path, {
            "schema": 1, "status": "NO-DATA", "reasons": problems,
            "vault": vault, "corpus": corpus_path, "tool": tool, "hook": hook,
            "k": k, "limit": limit,
            "per_query": [], "aggregate": _null_aggregate(),
            "by_band": {}, "by_lang": {},
        })
        return 0

    horizon = min(k, limit)
    derive, context_note = context_deriver(hook)
    options = tool_options(tool)
    if context_note:
        print("NO-DATA: %s" % context_note)
    for flag in OPTIONAL_FLAGS:
        if flag not in options:
            print("NO-DATA: the tool under test does not list %s, so it is not sent "
                  "(an older tool is a valid arm of a comparison)" % flag)
    work = tempfile.mkdtemp(prefix="vault-retrieval-")
    try:
        os.makedirs(os.path.join(work, "home"))
        os.makedirs(os.path.join(work, "cfg"))
        staged = _stage(vault, work)
        env = _env(work, staged, _sources_dir(vault))
        idx = subprocess.run([sys.executable, tool, "index"], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env, timeout=TIMEOUT_S)
        if idx.returncode != 0:
            print("NO-DATA: indexing the fixture failed (exit %d)" % idx.returncode)
            print(idx.stderr.decode("utf-8", "replace").strip()[:2000])
            _write(out_path, {
                "schema": 1, "status": "NO-DATA",
                "reasons": ["index exit %d" % idx.returncode],
                "vault": vault, "corpus": corpus_path, "tool": tool,
                "k": k, "limit": limit,
                "per_query": [], "aggregate": _null_aggregate(),
                "by_band": {}, "by_lang": {},
            })
            return 0

        rows = []
        for q in doc["queries"]:
            primary = os.path.join(staged, q["primary_path"])
            expected = set(os.path.join(staged, p) for p in q.get("expected_paths", []))
            expected.add(primary)
            file_path = (q.get("hook_tool_input") or {}).get("file_path") or ""
            context = derive(file_path) if (derive and file_path) else ""
            # The corpus row's project WINS over the git-root route. The fixture
            # paths (/repo/alpha-app/...) sit in no git repository, so project_of
            # can only answer "" for them; the row's `project` is the value the
            # hook WOULD resolve for a real checkout of that project, so sending it
            # is what makes this run the call the point of need hook makes. A corpus
            # that names no project still falls back to the git root, and an empty
            # value is omitted from the argv rather than sent as a guess.
            project = q.get("project") or (project_of(file_path) if file_path else "")
            argv = check_argv(tool, q["query_sent"], context, project, limit, options)
            proc = subprocess.run(
                argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                timeout=TIMEOUT_S)
            text = proc.stdout.decode("utf-8", "replace")
            blocks, truncated = parse_hits(text, staged)
            served = [b for b in blocks if not b["withheld"]]
            raw_rank = _rank_of(blocks, {primary}, served_only=False)
            served_rank = _rank_of(blocks, {primary}, served_only=True)
            first_expected = _rank_of(blocks, expected, served_only=True)
            rr = (1.0 / served_rank) if (served_rank and served_rank <= horizon) else 0.0
            rows.append({
                "qid": q["qid"], "query_sent": q["query_sent"], "band": q["band"],
                "lang": q["lang"], "folder": q["folder"], "kind": q.get("kind"),
                "anchor_note_count": q.get("anchor_note_count"),
                "primary_path": q["primary_path"],
                "file_path": file_path, "context": context, "project": project,
                "argv": argv[2:],
                "raw_rank": raw_rank, "served_rank": served_rank,
                "first_expected_rank": first_expected,
                "hits": len(blocks), "served": len(served),
                "withheld": len(blocks) - len(served), "truncated": truncated,
                "matched_on": sorted(set(m for b in served for m in b["matched_on"])),
                "rr": rr,
                "ndcg": _ndcg(served, primary, expected, min(NDCG_AT, horizon)),
            })
    finally:
        shutil.rmtree(work, ignore_errors=True)

    result = {
        "schema": 1, "status": "OK",
        "vault": vault, "corpus": corpus_path, "tool": tool, "hook": hook,
        "tool_options": list(options),
        "context_source": context_note or "%s _context_path" % os.path.basename(hook),
        "argv_note": ("per-query argv omits the interpreter and the tool path, which are "
                      "the same on every row and named once above as \"tool\""),
        "corpus_label": doc.get("corpus"), "vault_fingerprint": doc.get("vault_fingerprint"),
        "k": k, "limit": limit, "scored_horizon": horizon,
        "per_query": rows,
        "aggregate": _aggregate(rows, horizon),
        "by_band": _grouped(rows, "band", horizon),
        "by_lang": _grouped(rows, "lang", horizon),
    }
    _write(out_path, result)
    _report(result)
    return 0


def _fmt(agg):
    parts = ["n=%d" % agg["n"]]
    for point in RECALL_POINTS:
        value = agg["recall@%d" % point]
        parts.append("recall@%d=%s" % (point, "null" if value is None else "%.4f" % value))
    parts.append("MRR=%s" % ("null" if agg["MRR"] is None else "%.4f" % agg["MRR"]))
    key = "nDCG@%d" % NDCG_AT
    parts.append("%s=%s" % (key, "null" if agg[key] is None else "%.4f" % agg[key]))
    return "  ".join(parts)


def _report(result):
    print("corpus %s  vault %s  k=%d limit=%d"
          % (result.get("corpus_label"), os.path.basename(result["vault"].rstrip(os.sep)),
             result["k"], result["limit"]))
    print("options sent %s  context from %s"
          % (" ".join(result.get("tool_options") or ["none"]),
             result.get("context_source")))
    print("ALL    %s" % _fmt(result["aggregate"]))
    for band, agg in result["by_band"].items():
        print("band %-8s %s" % (band, _fmt(agg)))
    for lang, agg in result["by_lang"].items():
        print("lang %-8s %s" % (lang, _fmt(agg)))


def _write(path, payload):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score Brother Vault retrieval quality.")
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--tool", default=DEFAULT_TOOL)
    ap.add_argument("--hook", default=DEFAULT_HOOK,
                    help="the point of need hook whose _context_path is imported")
    ap.add_argument("--k", type=int, default=5, help="scoring horizon (default 5)")
    ap.add_argument("--limit", type=int, default=None,
                    help="the --limit passed to bm_vault.py check (default: --k). "
                         "Pass 2 for the production number the hook actually gets.")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    limit = args.k if args.limit is None else args.limit
    return run(args.vault, args.corpus, args.tool, args.k, limit, args.out, args.hook)


if __name__ == "__main__":
    sys.exit(main())
