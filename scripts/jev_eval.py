#!/usr/bin/env python3
"""JEV-04 of the 1.0.20 orchestration control plane: Jev (TypeSafe) versus
Muse and DeepSeek as judges, moved into the tree from the session's own
evaluation (ground truth measured in session 50a7c16b, 2026-09-18), so the
cost-per-success and calibration claims can be rerun by anyone who has the
bridge, not just replayed from a frozen file.

WHY THIS EXISTS. The 80-item dataset, the runner and the scorer lived only
under ~/.claude/evidence, machine state that no clone of this repository
carries. A number quoted from there is a claim about a session, never a
property of the tree (see the estate's own "the benchmark score is a
property of the corpus" lesson). Freezing the rows this module was scored
against (benchmarks/jev_eval/results-2026-09-18.jsonl) and shipping the
runner and scorer beside them means the claim can be re-measured, not just
re-read.

TWO COMMANDS, ONE PROCESS:
  run     fires every question at every system through the bridge and
          appends one row per attempt to a results file. It moves an
          existing file at that path aside first (never overwrites
          evidence silently), the way the session script did.
  score   reads a results file and prints accuracy, balanced accuracy,
          coverage at a confidence gate, cost per success, cost per gated
          success, calibration bands, and Jev's agreement with its own
          repeat, paraphrase and batched answers: the same numbers the
          session's score.py computed, from the same rows.

THE UNANSWERED-AS-ASKED RULE. A row whose model answered as a model other
than the one asked (a substitute model chain firing) carries pred=None and
an error string naming the substitute, never a guessed answer credited to
the model that was actually asked. score() only computes accuracy, balanced
accuracy and cost-per-success over rows where pred is not None; every row
with pred is None is counted separately as NO-DATA and never folds into
either the numerator or the denominator of a correctness figure. A results
file with zero rows is NO-DATA, never a printed zero: nothing was measured,
so nothing is a score.

BRIDGE RESOLUTION. Jev answers through the bridge's typed decisions mode;
Muse and DeepSeek answer through its chat mode, so run() resolves two
independent bridge commands:
  BROTHER_DECISION_BRIDGE (env)  used for every ask_jev call.
  BROTHER_CHAT_BRIDGE (env)      used for every ask_chat call.
Either one, left unset, defaults to "python3 <home>/.claude/bin/or_ask.py"
when that file exists, and to NO-DATA (the call is refused, every row that
needed it is recorded as NO-DATA) when it does not. A test overrides either
env var to point at a stand-in script and never has to touch the real
bridge or the network.

EVERY BOUNDARY CALL FAILS EXPLICITLY AND THE RUN KEEPS GOING. The session's
own runner let a single subprocess.TimeoutExpired escape a pool worker and
crash the whole run, silently dropping every row still queued behind it.
Here, a bridge subprocess that times out, that cannot even start (OSError),
or that answers with text that is not valid JSON is caught at the call site
and turned into one NO-DATA row naming the cause; it is never allowed to
propagate out of a worker and end the run for every other row still queued.

Standard library only. Python 3.9 floor (matching the rest of this tree).
No em dash, no en dash, anywhere in this file.
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import threading
import time

NODATA = "NO-DATA"
HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_DIR = os.path.join(HERE, "..", "benchmarks", "jev_eval")
DEFAULT_DATASET = os.path.join(BENCH_DIR, "dataset.json")
DEFAULT_OUT = os.path.join(BENCH_DIR, "results.jsonl")
DEFAULT_BRIDGE = os.path.join(os.path.expanduser("~"), ".claude", "bin", "or_ask.py")

SYSTEMS_BASE = ("jev", "jev2", "muse", "deepseek")
# Report order: base four plus the two Jev variants score.py also prints.
SYSTEMS = ("jev", "jev2", "jev_para", "jev_batch", "muse", "deepseek")
CALIBRATION_SYSTEMS = ("jev", "muse", "deepseek")
CALIBRATION_BANDS = ((0.5, 0.7), (0.7, 0.9), (0.9, 1.01))
PARAPHRASE = {
    "A": "Would the described change to the function make any of the listed tests fail?",
    "B": "Could this rule let an unknown, missing or unreadable input through as if it were fine?",
}
# The one model id each chat system must answer as. A response naming any
# other model means a substitute chain fired and is recorded as NO-DATA,
# never credited to the model asked (matches EXPECT in run_eval.py).
EXPECT = {"jev": "typesafe/jev", "muse": "meta/muse-spark-1.3-contributor",
          "deepseek": "deepseek/deepseek-v4.1-flash"}
# Catalog prices per token (openrouter.ai/api/v1/models, read 2026-09-18),
# used only when a chat response carries no usage.cost of its own.
PRICE = {"muse": (0.0000001, 0.0000002), "deepseek": (0.00000015, 0.0000006)}


# ---------------------------------------------------------------------------
# Bridge resolution and the one place a subprocess is ever spawned.
# ---------------------------------------------------------------------------

def _resolve_bridge(env_var, default_bridge=DEFAULT_BRIDGE):
    """The argv prefix for one bridge role, or (None, reason) when nothing
    usable is configured. An explicit env override is trusted as given (a
    test points it at its own stand-in script); the default is checked for
    existence, because a silently missing default answered as though it
    worked is exactly the kind of guess this module refuses to make."""
    override = os.environ.get(env_var)
    if override:
        try:
            argv = shlex.split(override)
        except ValueError as exc:
            return None, "%s: cannot parse %s=%r: %s" % (NODATA, env_var, override, exc)
        if not argv:
            return None, "%s: %s is set but empty" % (NODATA, env_var)
        return argv, None
    if os.path.isfile(default_bridge):
        return ["python3", default_bridge], None
    return None, ("%s: %s is unset and the default bridge %s does not exist"
                  % (NODATA, env_var, default_bridge))


def _call(argv, stdin, timeout):
    """Run one bridge subprocess. Returns (rc, stdout, stderr, secs). A
    subprocess that times out or that cannot even start is never allowed to
    raise past this point: rc is None and stderr names the reason, so every
    caller treats it exactly like a failing exit code. This keeps one slow
    or broken call from ending the whole batch for every other row still
    queued behind it."""
    t0 = time.time()
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr, round(time.time() - t0, 2)
    except subprocess.TimeoutExpired as exc:
        return None, "", "timed out after %ss: %s" % (timeout, exc), round(time.time() - t0, 2)
    except OSError as exc:
        return None, "", "subprocess failed to start: %s" % exc, round(time.time() - t0, 2)


def ask_jev(bridge, state, questions, timeout=120):
    if bridge is None:
        return None, 0.0, "%s: decision bridge not resolved" % NODATA
    rc, out, err, secs = _call(bridge + ["--decisions", "--model", "typesafe", "--timeout", str(timeout)],
                                json.dumps({"state": state, "questions": questions}), timeout)
    if rc != 0:
        return None, secs, (err or "")[-300:]
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return None, secs, "unparseable bridge output: %s" % exc
    model = payload.get("model") or ""
    if not model.startswith(EXPECT["jev"]):
        # Same rule ask_chat already enforces for Muse and DeepSeek: an
        # answer from a model other than the one asked is a substitute
        # firing, never credited to Jev. Muse's own review of this module
        # found this check present on the chat path and absent here,
        # which is exactly the fallback-is-NO-DATA rule the WBS names as
        # this unit's deciding property, so it applies to Jev too.
        return None, secs, "answered as a different model: %s" % model
    return payload, secs, None


def jev_pred(ans):
    if ans.get("type") == "noul":
        p = float(ans["noul"])
        return p >= 0.5, max(p, 1 - p)
    return ans.get("choice"), float(ans.get("confidence", max((ans.get("probabilities") or {0: 0}).values())))


def ask_chat(bridge, system, state, q, timeout=300):
    if bridge is None:
        return None, None, None, 0.0, None, "%s: chat bridge not resolved" % NODATA
    if q["type"] == "noul":
        options = '"yes" (%s) or "no" (%s)' % (q["criteria"]["true"], q["criteria"]["false"])
    else:
        options = " or ".join('"%s" (%s)' % (k, v) for k, v in q["criteria"].items())
    prompt = ("%s\n\nInput:\n%s\n\nAnswer with one JSON object only, no prose: "
              '{"answer": %s, "probability": <your probability, 0 to 1, that your answer is right>}'
              % (q["instructions"], json.dumps(state, indent=1), options))
    rc, out, err, secs = _call(bridge + ["--model", system, "--effort", "high", "--max", "8000",
                                          "--timeout", str(timeout), "--json"], prompt, timeout)
    if rc != 0:
        return None, None, None, secs, None, (err or "")[-300:]
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return None, None, None, secs, None, "unparseable bridge output: %s" % exc
    model = payload.get("model") or ""
    if not model.startswith(EXPECT[system]):
        return None, None, None, secs, None, "answered as a different model: %s" % model
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        return None, None, None, secs, None, "malformed chat payload: %s" % exc
    m = re.search(r"\{[^{}]*\"answer\"[^{}]*\}", content, re.S)
    if not m:
        return None, None, None, secs, None, "unparseable: %s" % content[:120]
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        return None, None, None, secs, None, "unparseable answer object: %s" % exc
    a = str(obj.get("answer", "")).strip().lower()
    pred = {"yes": True, "no": False}.get(a, a) if q["type"] == "noul" else a
    usage = payload.get("usage") or {}
    cost = usage.get("cost")
    if cost is None:
        pi, po = PRICE[system]
        cost = (usage.get("prompt_tokens") or 0) * pi + (usage.get("completion_tokens") or 0) * po
    try:
        probability = float(obj.get("probability", 0.5))
    except (TypeError, ValueError):
        probability = 0.5
    return pred, probability, cost, secs, model, None


# ---------------------------------------------------------------------------
# The 80-item dataset, read once per run().
# ---------------------------------------------------------------------------

def _spec_text(dataset_dir, spec_dir, unit):
    path = os.path.join(dataset_dir, spec_dir, unit + "-spec.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise FileNotFoundError("%s: cannot read spec %s: %s" % (NODATA, path, exc))


def items(dataset, dataset_dir):
    """(task, id, truth, state, question) for every item, Jev-shaped. A
    routing spec that cannot be read raises rather than silently dropping
    the item (unknown input raises, it never becomes a quieter dataset)."""
    A = dataset["A_mutation_triage"]
    for it in A["items"]:
        yield ("A", it["id"], it["caught"],
               {"rule": it["rule"], "tests": it["tests"], "mutation": it["mutation"]},
               {"type": "noul", "instructions": A["instructions"],
                "criteria": {"true": A["true"], "false": A["false"]}})
    B = dataset["B_unknown_reads_safe"]
    for it in B["items"]:
        yield ("B", it["id"], it["unsafe"], {"rule": it["rule"]},
               {"type": "noul", "instructions": B["instructions"],
                "criteria": {"true": B["true"], "false": B["false"]}})
    C = dataset["C_routing"]
    for unit, label in C["labels"].items():
        text = _spec_text(dataset_dir, C["spec_dir"], unit)
        yield ("C", unit, label, {"specification": text},
               {"type": "choice", "instructions": C["instructions"], "criteria": C["criteria"]})
    D = dataset["D_log_triage"]
    for it in D["items"]:
        yield ("D", it["id"], it["label"], {"gate_output_line": it["line"]},
               {"type": "choice", "instructions": D["instructions"], "criteria": D["criteria"]})


# ---------------------------------------------------------------------------
# run(): fire every question at every system, append one row per attempt.
# ---------------------------------------------------------------------------

def _record(out_path, lock, row):
    with lock:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")


def _job(decision_bridge, chat_bridge, out_path, lock, kind, task, iid, truth, state, q,
         decision_timeout=120, chat_timeout=300):
    base = {"task": task, "item": iid, "truth": truth}
    if kind in ("jev", "jev2", "jev_para"):
        q2 = dict(q)
        if kind == "jev_para":
            q2["instructions"] = PARAPHRASE[task]
        payload, secs, err = ask_jev(decision_bridge, state, {"q": q2}, timeout=decision_timeout)
        if payload is None:
            _record(out_path, lock, dict(base, system=kind, pred=None, error=err, secs=secs))
            return
        try:
            pred, conf = jev_pred(payload["answers"]["q"])
        except (KeyError, TypeError, ValueError) as exc:
            _record(out_path, lock, dict(base, system=kind, pred=None,
                    error="malformed decision payload: %s" % exc, secs=secs))
            return
        _record(out_path, lock, dict(base, system=kind, pred=pred, conf=conf, correct=pred == truth,
                cost=(payload.get("usage") or {}).get("cost"), secs=secs, model=payload.get("model")))
        return
    pred, conf, cost, secs, model, err = ask_chat(chat_bridge, kind, state, q, timeout=chat_timeout)
    if err:
        _record(out_path, lock, dict(base, system=kind, pred=None, error=err, secs=secs))
        return
    _record(out_path, lock, dict(base, system=kind, pred=pred, conf=conf, correct=pred == truth, cost=cost,
            secs=secs, model=model))


def _batched(decision_bridge, out_path, lock, dataset, dataset_dir, task, decision_timeout=120):
    """One Jev call carrying every item of a task as its own question."""
    its = [i for i in items(dataset, dataset_dir) if i[0] == task]
    state = dict((i[1], i[3]) for i in its)
    qs = {}
    for _t, iid, _truth, _s, q in its:
        q2 = dict(q)
        q2["instructions"] = "About input %s only: %s" % (iid, q["instructions"])
        qs[iid.replace(".", "_").replace("-", "_")] = q2
    payload, secs, err = ask_jev(decision_bridge, state, qs, timeout=decision_timeout)
    if payload is None:
        # One row per item, same shape a per-item failure would carry.
        # A single row naming only the task, with no truth and no item,
        # is invisible to compute_task's own "truth" in r filter: a
        # batched call that fails would silently vanish from every count
        # instead of appearing as NO-DATA, which is worse than wrong, it
        # looks like fewer failures happened than actually did.
        for _t, iid, truth, _s, _q in its:
            _record(out_path, lock, {"task": task, "item": iid, "truth": truth, "system": "jev_batch",
                    "pred": None, "error": err, "secs": secs})
        return
    n = len(its) or 1
    for _t, iid, truth, _s, _q in its:
        key = iid.replace(".", "_").replace("-", "_")
        try:
            ans = payload["answers"][key]
        except KeyError:
            _record(out_path, lock, {"task": task, "item": iid, "truth": truth, "system": "jev_batch",
                    "pred": None, "error": "batched answer missing key %s" % key})
            continue
        try:
            pred, conf = jev_pred(ans)
        except (TypeError, ValueError) as exc:
            _record(out_path, lock, {"task": task, "item": iid, "truth": truth, "system": "jev_batch",
                    "pred": None, "error": "malformed decision payload: %s" % exc})
            continue
        _record(out_path, lock, {"task": task, "item": iid, "truth": truth, "system": "jev_batch", "pred": pred,
                "conf": conf, "correct": pred == truth,
                "cost": (payload.get("usage") or {}).get("cost", 0) / n,
                "secs": round(secs / n, 3), "model": payload.get("model")})


def run(dataset_path=DEFAULT_DATASET, out_path=DEFAULT_OUT, workers=6, default_bridge=DEFAULT_BRIDGE,
        decision_timeout=120, chat_timeout=300):
    if not os.path.isfile(dataset_path):
        print("%s: %s does not exist" % (NODATA, dataset_path))
        return 2
    try:
        with open(dataset_path, encoding="utf-8") as fh:
            dataset = json.load(fh)
    except json.JSONDecodeError as exc:
        print("%s: %s is not valid JSON: %s" % (NODATA, dataset_path, exc))
        return 2
    dataset_dir = os.path.dirname(os.path.abspath(dataset_path))
    decision_bridge, decision_err = _resolve_bridge("BROTHER_DECISION_BRIDGE", default_bridge)
    chat_bridge, chat_err = _resolve_bridge("BROTHER_CHAT_BRIDGE", default_bridge)
    if decision_bridge is None and chat_bridge is None:
        print(decision_err)
        print(chat_err)
        return 2
    try:
        work_items = list(items(dataset, dataset_dir))
    except (FileNotFoundError, KeyError) as exc:
        print("%s: %s" % (NODATA, exc))
        return 2
    if os.path.exists(out_path):
        moved = "%s.prev-%d" % (out_path, int(time.time()))
        os.rename(out_path, moved)
        print("moved previous results to %s" % moved)
    lock = threading.Lock()
    work = []
    for task, iid, truth, state, q in work_items:
        for kind in SYSTEMS_BASE:
            work.append((kind, task, iid, truth, state, q))
        if task in PARAPHRASE:
            work.append(("jev_para", task, iid, truth, state, q))
    batched_tasks = sorted(set(t for t, _i, _tr, _s, _q in work_items) & {"B", "D"})
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(_job, decision_bridge, chat_bridge, out_path, lock, *w,
                          decision_timeout=decision_timeout, chat_timeout=chat_timeout) for w in work]
        futs += [ex.submit(_batched, decision_bridge, out_path, lock, dataset, dataset_dir, t,
                           decision_timeout=decision_timeout) for t in batched_tasks]
        for f in cf.as_completed(futs):
            f.result()
    n = 0
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as fh:
            n = sum(1 for _ in fh)
    print("done: %d rows in %s" % (n, out_path))
    return 0


# ---------------------------------------------------------------------------
# score(): read a results file, print the same numbers score.py computed.
# ---------------------------------------------------------------------------

def _fmt(x, pct=True):
    if x is None:
        return NODATA
    return "%3.0f%%" % (100 * x) if pct else ("$%.6f" % x)


def _bal_acc(rs):
    by = collections.defaultdict(list)
    for r in rs:
        by[json.dumps(r["truth"])].append(r["correct"])
    return statistics.mean(statistics.mean(v) for v in by.values()) if by else None


def compute_task(rows, task, gate):
    """This task's numbers for every system that has at least one row.
    Returns None fields rather than a synthesized zero wherever a divisor
    would be empty (no ok rows, no gated rows, no successes)."""
    tr = [r for r in rows if r.get("task") == task and "truth" in r]
    truths = collections.Counter(json.dumps(r["truth"]) for r in tr if r.get("system") == "jev")
    n_items = sum(truths.values())
    base_rate = (max(truths.values()) / n_items) if n_items else None
    systems = {}
    for sysn in SYSTEMS:
        rs = [r for r in tr if r.get("system") == sysn]
        if not rs:
            continue
        ok = [r for r in rs if r.get("pred") is not None]
        nodata = len(rs) - len(ok)
        acc = statistics.mean(r["correct"] for r in ok) if ok else None
        gated = [r for r in ok if (r.get("conf") or 0) >= gate]
        coverage = (len(gated) / len(ok)) if ok else None
        acc_at_gate = statistics.mean(r["correct"] for r in gated) if gated else None
        cost = sum(r.get("cost") or 0 for r in rs)
        succ = sum(1 for r in ok if r["correct"])
        gated_succ = sum(1 for r in gated if r["correct"])
        p50 = statistics.median(r.get("secs") or 0 for r in rs) if rs else None
        systems[sysn] = {
            "n": len(ok), "nodata": nodata, "accuracy": acc,
            "balanced_accuracy": _bal_acc(ok), "coverage": coverage,
            "accuracy_at_gate": acc_at_gate,
            "cost_per_success": (cost / succ) if succ else None,
            "cost_per_gated_success": (cost / gated_succ) if gated_succ else None,
            "p50_seconds": p50,
        }
    calibration = {}
    for sysn in CALIBRATION_SYSTEMS:
        ok = [r for r in tr if r.get("system") == sysn and r.get("pred") is not None]
        bands = []
        for lo, hi in CALIBRATION_BANDS:
            b = [r for r in ok if lo <= (r.get("conf") or 0) < hi]
            bands.append({"lo": lo, "hi": hi, "n": len(b),
                          "accuracy": statistics.mean(x["correct"] for x in b) if b else None})
        calibration[sysn] = bands
    agreement = {}
    j1 = {r["item"]: r.get("pred") for r in tr if r.get("system") == "jev" and r.get("pred") is not None}
    for name, othersys in (("repeat", "jev2"), ("paraphrase", "jev_para"), ("batched", "jev_batch")):
        other = {r["item"]: r.get("pred") for r in tr if r.get("system") == othersys and r.get("pred") is not None}
        common = [k for k in j1 if k in other]
        if common:
            agreement[name] = {"agree": sum(j1[k] == other[k] for k in common), "n": len(common)}
    return {"n_items": n_items, "base_rate": base_rate, "systems": systems,
            "calibration": calibration, "agreement": agreement}


def compute(rows, gate=0.9):
    return dict((task, compute_task(rows, task, gate)) for task in "ABCD")


def spend_by_system(rows):
    spent = collections.defaultdict(float)
    for r in rows:
        spent[r.get("system")] += r.get("cost") or 0
    return dict(spent)


def format_report(computed, spend, gate):
    lines = ["gate for autonomous use: confidence >= %.2f" % gate, ""]
    for task in "ABCD":
        c = computed[task]
        lines.append("TASK %s  items=%d  base rate (always the majority answer)=%s"
                      % (task, c["n_items"], _fmt(c["base_rate"])))
        lines.append("  %-9s %5s %6s %7s %6s %9s %13s %13s %6s" % (
            "system", "n", "acc", "bal_acc", "cover", "acc@gate", "$/success", "$/gated-succ", "p50 s"))
        for sysn in SYSTEMS:
            if sysn not in c["systems"]:
                continue
            s = c["systems"][sysn]
            tail = "  NO-DATA rows=%d" % s["nodata"] if s["nodata"] else ""
            lines.append("  %-9s %5s %6s %7s %6s %9s %13s %13s %6.1f%s" % (
                sysn, s["n"], _fmt(s["accuracy"]), _fmt(s["balanced_accuracy"]),
                _fmt(s["coverage"]), _fmt(s["accuracy_at_gate"]),
                _fmt(s["cost_per_success"], pct=False), _fmt(s["cost_per_gated_success"], pct=False),
                s["p50_seconds"] if s["p50_seconds"] is not None else 0.0, tail))
        for sysn in CALIBRATION_SYSTEMS:
            bands = c["calibration"].get(sysn) or []
            cells = " | ".join("%.1f-%.1f: %s of %d" % (
                b["lo"], min(b["hi"], 1.0),
                ("%3.0f%%" % (100 * b["accuracy"])) if b["accuracy"] is not None else "  -", b["n"])
                for b in bands)
            lines.append("  calibration %-8s %s" % (sysn, cells))
        for name in ("repeat", "paraphrase", "batched"):
            a = c["agreement"].get(name)
            if a:
                lines.append("  jev agreement with its %-10s %d of %d" % (name + ":", a["agree"], a["n"]))
        lines.append("")
    lines.append("spend this eval by system: " + ", ".join(
        "%s $%.4f" % (k, v) for k, v in sorted((k, v) for k, v in spend.items() if k is not None)))
    return "\n".join(lines)


def score(results_path, gate=0.9):
    if not os.path.isfile(results_path):
        print("%s: %s does not exist" % (NODATA, results_path))
        return 2
    rows = []
    with open(results_path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print("%s: %s line %d is not valid JSON: %s" % (NODATA, results_path, lineno, exc))
                return 2
    if not rows:
        print("%s: %s has no rows. An empty results file is nothing measured, "
              "never a zero score." % (NODATA, results_path))
        return 3
    computed = compute(rows, gate)
    spend = spend_by_system(rows)
    print(format_report(computed, spend, gate))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description="Jev evaluation harness (JEV-04)")
    sub = parser.add_subparsers(dest="verb", required=True)
    run_parser = sub.add_parser("run", help="fire every question at every system, append rows")
    run_parser.add_argument("--dataset", default=DEFAULT_DATASET)
    run_parser.add_argument("--out", default=DEFAULT_OUT)
    run_parser.add_argument("--workers", type=int, default=6)
    score_parser = sub.add_parser("score", help="score a results file")
    score_parser.add_argument("results_path")
    score_parser.add_argument("gate", type=float, nargs="?", default=0.9)
    args = parser.parse_args(argv)
    if args.verb == "run":
        return run(args.dataset, args.out, args.workers)
    if args.verb == "score":
        return score(args.results_path, args.gate)
    return 2  # pragma: no cover, argparse already refuses an unknown verb


if __name__ == "__main__":
    sys.exit(main())
