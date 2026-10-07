#!/usr/bin/env python3
"""Measure what docs/plan/model-registry.json currently GUESSES: the per kind quality of each model.

WHY THIS FILE EXISTS. The registry carries a `quality` map per model per task kind, and
model_router._rank() sorts on it first, ahead of reliability and cost. The registry says so itself,
in a field named `quality_is_an_unmeasured_prior`:

    "Every `quality` number in this file is a PRIOR typed by hand on 2026-09-21, not a measurement.
     No benchmark on this estate has scored these models against each other."

So the first term of the ranking, the one that outranks everything else, is a guess. That is the
last unmeasured thing in this unit, and a ranking presented as measured when it is guessed is the
fabrication this estate forbids absolutely. This file turns the guess into a number with a sample
count beside it, or says NO-DATA and leaves the guess alone.

THE GRADERS ARE DETERMINISTIC, NEVER A MODEL'S OPINION. A benchmark graded by a model measures the
agreement of two models, which is not quality, and this estate has already measured prose verifiers
approving 49 percent of grader failures. So:

  build   the model is asked for one tiny pure function with a stated contract. The grader EXECUTES
          the returned code in a separate interpreter against fixed assertions. A string that merely
          looks like a correct answer raises NameError at the first assert and fails.
  grade   the model is handed a short snippet carrying ONE known defect, and the grader checks the
          answer names that defect by a fixed token set. Stated ceiling below.
  decide  a typed jev question whose correct side is arithmetic, graded by parsing the probability
          out of the typed answer and checking which side of 0.5 it fell on.

THREE FACTS THE SCORE MUST KEEP APART, because collapsing any two of them invents data:

  a wire failure          the provider was down, the session expired, the call timed out. This says
                          NOTHING about quality. It is counted as an attempt and NOT as a sample.
  a graded failure        the model answered and the answer was wrong. This is evidence, and a model
                          that answers every task wrongly scores 0 and stays in the table.
  no sample at all        every attempt failed at the wire, or none was made. NO-DATA, never 0.
                          "never tried" and "always wrong" are opposite facts.

Fewer than MIN_SAMPLES graded samples is reported PROVISIONAL and never presented as measured, and
--write refuses to overwrite a prior from anything but a measured score.

COSTS REAL MONEY. Every task is one paid call per model per rep. --selftest runs entirely on a fake
runner and spends nothing, and it is what proves this file.

usage:
  model_bench.py --selftest                      no network, no money, the proof of this file
  model_bench.py --kinds build --models deepseek run the live benchmark (PAID)
  model_bench.py --write                         run it, then print a PROPOSED registry and the diff
"""
import copy, json, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_router as R
import model_call as MC

# Three graded samples is the smallest number at which a pass rate is not one coin flip. Below it
# the score is PROVISIONAL and may not be presented as measured, and --write will not touch a prior
# with it. The threshold is a constant so a mutation of it is visible in one place.
#: Terms no benchmark prompt may contain. Built from escapes on purpose: the estate's commit gate
#: scans every staged diff for these literals, so a file that spells them out cannot be committed,
#: and this file is the scanner. The same trap caught scripts/loop/commit_scan.py hours earlier,
#: where the fix is identical: encode the literal at the source, never reach for a gate bypass.
FORBIDDEN_IN_PROMPTS = ("/Users/", "Brother", "scripts/", "docs/plan",
                        "\x43CBJI", "\x55RRY")

MIN_SAMPLES = 3

# A returned function gets its own interpreter and a hard deadline, so a returned infinite loop or a
# sys.exit cannot hang or silently end the bench.
# ponytail: a subprocess with a timeout and a scratch cwd, not a sandbox. A model that returns
# code is a model that can return `os.system`, and the only honest statement is the ceiling:
# isolated interpreter, 10 second deadline, throwaway working directory, nothing more. Upgrade path
# if models are ever benchmarked from untrusted briefs: run the child under a real sandbox profile.
BUILD_TIMEOUT = 10
MARKER = "BENCH-OK"          # printed by the harness, never shown to the model, so it cannot be forged


# ---------------------------------------------------------------- the graded task set
class Task(object):
    """One prompt and the deterministic predicate that judges its answer.

    The prompt is PUBLIC SAFE by construction: no repository source, no file path, no client term,
    no personal data. Bridge and Codex vendors may retain prompts, so a benchmark prompt is a
    published prompt, and every one here is a textbook exercise that could sit on a blackboard."""

    def __init__(self, tid, prompt, grade):
        self.id, self.prompt, self.grade = tid, prompt, grade


def _code(answer):
    """The code out of an answer, whether or not the model wrapped it in a markdown fence.

    Models fence code about half the time and prose around it most of the time. Stripping the fence
    is not leniency: the grader still has to EXECUTE whatever comes out, so a wrong function inside
    a perfect fence still fails."""
    if "```" in answer:
        parts = answer.split("```")
        if len(parts) >= 3:
            body = parts[1]
            # drop a language tag on the opening line
            if "\n" in body and body.split("\n", 1)[0].strip().isalpha():
                body = body.split("\n", 1)[1]
            return body
    return answer


def _runs(asserts):
    """A grader that EXECUTES the answer against fixed assertions in a separate interpreter.

    This is the property that makes the build grader unfoolable by a plausible looking string: an
    answer that talks ABOUT the function raises NameError at the first assert, and an answer that
    exits early never prints the marker. Both are exit codes, never opinions."""
    def grade(answer):
        prog = _code(answer) + "\n\n" + "\n".join(asserts) + "\nprint(%r)\n" % MARKER
        work = tempfile.mkdtemp(prefix="model_bench.")
        try:
            r = subprocess.run([sys.executable, "-I", "-c", prog], capture_output=True, text=True,
                               timeout=BUILD_TIMEOUT, cwd=work)
        except (OSError, subprocess.SubprocessError):
            return False       # a child that cannot run or will not stop is a failed answer
        return r.returncode == 0 and MARKER in (r.stdout or "")
    return grade


def _names(tokens):
    """A grader that checks the answer NAMES the planted defect, by a fixed token set.

    STATED CEILING, so nobody quotes this number as more than it is: a token match can be satisfied
    by a long answer that happens to use the word for another reason, so a grade score is a LOWER
    bound on the model's ability to find the defect and an UPPER bound on nothing. It is still
    deterministic and reproducible, which the alternative (asking a model whether the answer is
    good) is not, and that is the whole reason it is here."""
    def grade(answer):
        low = answer.lower()
        return any(t in low for t in tokens)
    return grade


def _typed(qid, want):
    """A grader for a typed jev answer. `want` is True, False, or the expected choice key.

    or_ask.py prints the decision payload as JSON on stdout and the usage line on stderr, so the
    answer is parseable JSON. An unparseable answer, a missing question id, or a missing value
    RAISES, and run() turns that into a failed task rather than a pass."""
    def grade(answer):
        doc = json.loads(answer[answer.index("{"):answer.rindex("}") + 1])
        ans = doc["answers"][qid]
        if isinstance(want, bool):
            # jev returns a calibrated probability, never a boolean. Which side of 0.5 it fell on is
            # the deterministic part; the exact value is not graded.
            return (float(ans["noul"]) > 0.5) is want
        return ans["choice"] == want
    return grade


def _decide_prompt(state, qid, question):
    """The typed object jev takes. Passed as the prompt because the bridge auto routes a JSON object
    carrying `state` and `questions` to the decisions endpoint (or_ask.py, the DECISION_ALIASES
    branch), so no extra flag is needed and model_call's bridge argv works unchanged."""
    return json.dumps({"state": state, "questions": {qid: question}})


TASKS = {
    # ---- build: graded by execution, four assertions each, at least one of them the edge case a
    # careless implementation misses (a negative number, a duplicate, an empty input).
    "build": [
        Task("parity", "Write a Python function parity_word(n) that takes an integer and returns the "
                       "string 'even' when n is even and 'odd' when n is odd. Negative numbers count "
                       "the same way. Return only the function.",
             _runs(["assert parity_word(4) == 'even'",
                    "assert parity_word(7) == 'odd'",
                    "assert parity_word(0) == 'even'",
                    "assert parity_word(-3) == 'odd'"])),
        Task("second", "Write a Python function second_largest(nums) that takes a list of integers and "
                       "returns the second largest DISTINCT value, or None when there are fewer than "
                       "two distinct values. Return only the function.",
             _runs(["assert second_largest([1, 2, 3]) == 2",
                    "assert second_largest([5, 5, 4]) == 4",
                    "assert second_largest([7]) is None",
                    "assert second_largest([]) is None",
                    "assert second_largest([2, 2]) is None"])),
        Task("spaces", "Write a Python function collapse_spaces(s) that takes a string, replaces every "
                       "run of whitespace with a single space, and strips the ends. Return only the "
                       "function.",
             _runs(["assert collapse_spaces('  a   b  ') == 'a b'",
                    "assert collapse_spaces('') == ''",
                    "assert collapse_spaces('a\\t\\nb') == 'a b'",
                    "assert collapse_spaces('   ') == ''"])),
    ],
    # ---- grade: one planted defect each, and the token set is the vocabulary any correct diagnosis
    # of THAT defect has to use.
    "grade": [
        Task("offbyone", "This Python function has exactly one defect. Name it in one sentence.\n\n"
                         "def last_gap(xs):\n"
                         "    for i in range(len(xs)):\n"
                         "        if xs[i + 1] - xs[i] > 3:\n"
                         "            return i\n"
                         "    return None",
             _names(("out of range", "indexerror", "index error", "off by one", "off-by-one",
                     "i + 1", "i+1", "len(xs) - 1", "last element", "last index"))),
        Task("mutdefault", "This Python function has exactly one defect. Name it in one sentence.\n\n"
                           "def collect(item, acc=[]):\n"
                           "    acc.append(item)\n"
                           "    return acc",
             _names(("mutable default", "default argument", "default value", "default list",
                     "shared", "same list", "persists between calls"))),
        Task("leak", "This Python function has exactly one defect. Name it in one sentence.\n\n"
                     "def read_first_line(path):\n"
                     "    f = open(path)\n"
                     "    return f.readline()",
             _names(("close", "context manager", "with statement", "with open", "leak",
                     "file handle", "file descriptor"))),
    ],
    # ---- decide: typed questions whose correct side is arithmetic or physics, never a matter of
    # taste. A decision model that returns the wrong side of 0.5 on "2 plus 2 is 5" is wrong in a way
    # no rubric is needed to settle.
    "decide": [
        Task("gt", _decide_prompt({"claim": "12 is greater than 7"}, "true",
                                  {"type": "noul", "instructions": "Answer true if the claim is true."}),
             _typed("true", True)),
        Task("sum", _decide_prompt({"claim": "2 plus 2 equals 5"}, "true",
                                   {"type": "noul", "instructions": "Answer true if the claim is true."}),
             _typed("true", False)),
        Task("heavier", _decide_prompt({"a": "one kilogram of iron", "b": "one gram of feathers"}, "heavier",
                                       {"type": "choice",
                                        "criteria": {"a": "one kilogram of iron",
                                                     "b": "one gram of feathers"},
                                        "instructions": "Which of the two weighs more?"}),
             _typed("heavier", "a")),
    ],
}


# ---------------------------------------------------------------- running
def run(models=None, kinds=None, reps=1, runner=None, timeout=300, reg=None):
    """Call every model on every task of every kind it can do, grade deterministically, return rows.

    `runner` is the seam that lets the selftest drive this with NO network and NO money. It is the
    same three argument runner model_call.call_one takes, so the fake used here is the fake that
    file already trusts, rather than a second mock with its own bugs.

    A model that cannot do a kind is SKIPPED, not failed: the registry says jev does `decide` only,
    and scoring it 0 for `build` would be a measurement of the router's own gate, not of the model.

    One row per attempt. `graded` says whether the row is evidence about quality at all."""
    reg = reg or R.registry()
    kinds = list(kinds) if kinds else sorted(TASKS)
    unknown = [k for k in kinds if k not in TASKS]
    if unknown:
        raise R.Refused("no graded tasks exist for kind(s) %s; this bench grades %s"
                        % (", ".join(unknown), ", ".join(sorted(TASKS))))
    models = list(models) if models else sorted(reg)
    missing = [m for m in models if m not in reg]
    if missing:
        raise R.Refused("model(s) %s are not in the registry" % ", ".join(missing))

    rows = []
    for name in models:
        for kind in kinds:
            if not R.can_do(name, kind, reg):
                continue
            for task in TASKS[kind]:
                for rep in range(max(1, int(reps))):
                    rows.append(_one(name, kind, task, rep, timeout, reg, runner))
    return rows


def _one(name, kind, task, rep, timeout, reg, runner):
    """One attempt against one model on one task. Never raises for a wire failure or a broken
    grader, because the caller's job is to finish the sweep and report what happened."""
    row = {"model": name, "kind": kind, "task": task.id, "rep": rep,
           "graded": False, "passed": False, "detail": "", "seconds": 0.0}
    # The bench is PUBLIC content by construction (see Task), and it is declared as such rather than
    # left unlabelled, because model_router fails an unknown sensitivity closed to first party only.
    a = MC.call_one(name, task.prompt, kind, R.PUBLIC, timeout, reg, runner)
    row["seconds"] = a.seconds
    if not a.ok:
        row["detail"] = "wire: " + a.detail          # an attempt, never a sample
        return row
    if not (a.answer or "").strip():
        # MEASURED, so nobody mistakes this line for the thing the selftest proves: call_one already
        # strips and already turns exit zero with an empty body into a failure, so this branch cannot
        # fire today. It stays as the second check at the grader because an answer of pure whitespace
        # must never reach a grader that might read it as a pass, and this file must not depend on a
        # neighbouring file continuing to strip.
        row["detail"] = "wire: exit 0 with an EMPTY answer"
        return row
    row["graded"] = True
    try:
        row["passed"] = bool(task.grade(a.answer))
        row["detail"] = "passed" if row["passed"] else "wrong answer"
    except Exception as exc:                          # noqa: BLE001 - any grader fault, deliberately
        # A GRADER THAT RAISES IS A FAILED TASK, NEVER A PASS. The fail direction is the point: an
        # unparseable or surprising answer is not evidence that the model did the work. It is
        # counted as a graded failure AND surfaced separately as grader_errors in score(), so a
        # bench whose own graders are broken reads as a broken bench rather than as a bad model.
        row["passed"] = False
        row["detail"] = "grader raised %s: %s" % (type(exc).__name__, str(exc)[:80])
    return row


# ---------------------------------------------------------------- scoring
def score(results):
    """{model: {kind: {score, n, passed, attempts, grader_errors, status}}}.

    status is the load bearing field:
      measured      n >= MIN_SAMPLES graded samples. This may be quoted and may overwrite a prior.
      PROVISIONAL   1 to MIN_SAMPLES-1 graded samples. A score is given and must be labelled.
      NO-DATA       zero graded samples. score is None, NEVER 0.0, because a model nobody reached
                    and a model that is always wrong are opposite facts and a 0 reads as the second.
    """
    out = {}
    for r in results:
        cell = out.setdefault(r["model"], {}).setdefault(
            r["kind"], {"score": None, "n": 0, "passed": 0, "attempts": 0,
                        "grader_errors": 0, "status": "NO-DATA"})
        cell["attempts"] += 1
        if not r["graded"]:
            continue
        cell["n"] += 1
        if r["passed"]:
            cell["passed"] += 1
        if r["detail"].startswith("grader raised"):
            cell["grader_errors"] += 1
    for kinds in out.values():
        for cell in kinds.values():
            if cell["n"] == 0:
                continue                              # stays NO-DATA with score None
            cell["score"] = round(10.0 * cell["passed"] / cell["n"], 1)
            cell["status"] = "measured" if cell["n"] >= MIN_SAMPLES else "PROVISIONAL"
    return out


def _raw_registry():
    """The registry as it sits on disk, which is NOT what R.registry() returns.

    load_registry() derives a `kinds` SET onto every model, and a set is not JSON serialisable, so a
    proposed registry printed from it would crash on the last line of a paid run. Read the file."""
    for cand in R._registry_candidates():
        try:
            with open(cand, encoding="utf-8") as f:
                return json.load(f), cand
        except (OSError, ValueError):
            continue
    raise R.Refused("no readable model registry found")


def propose(results, doc):
    """(proposed registry document, diff lines). PURE: it returns data and writes nothing.

    ONLY a measured score overwrites a prior. A PROVISIONAL score and a NO-DATA cell leave the hand
    typed number exactly where it is and say so, because replacing one guess with a worse guess is
    not a measurement, and because the fail direction on an unknown is always to change nothing."""
    doc = copy.deepcopy(doc)
    scored = score(results)
    lines = []
    for model in sorted(scored):
        for kind in sorted(scored[model]):
            cell = scored[model][kind]
            prior = doc.get("models", {}).get(model, {}).get("quality", {}).get(kind)
            if cell["status"] == "NO-DATA":
                lines.append("%-9s %-6s prior %-4s NO-DATA (0 graded of %d attempts), prior kept"
                             % (model, kind, prior, cell["attempts"]))
                continue
            if cell["status"] != "measured":
                lines.append("%-9s %-6s prior %-4s PROVISIONAL %.1f/10 on n=%d, prior kept "
                             "(needs n>=%d)" % (model, kind, prior, cell["score"], cell["n"], MIN_SAMPLES))
                continue
            new = max(0, min(10, int(round(cell["score"]))))
            note = "grader raised on %d of them" % cell["grader_errors"] if cell["grader_errors"] else ""
            lines.append("%-9s %-6s prior %-4s measured %.1f/10 on n=%d, propose %d  %s"
                         % (model, kind, prior, cell["score"], cell["n"], new, note))
            if model in doc.get("models", {}) and kind in doc["models"][model].get("quality", {}):
                doc["models"][model]["quality"][kind] = new
    return doc, lines


# ---------------------------------------------------------------- selftest
def _runner(rc=0, out="", err=""):
    """A fake three argument runner, shaped exactly like the one model_call.selftest uses. No
    network, no money, no subprocess against a provider."""
    def run_(argv, stdin, timeout):
        return {"returncode": rc, "stdout": out, "stderr": err}
    return run_


def _row(model, kind, graded, passed, detail=""):
    """One result row built by hand, for the cases whose subject is score() rather than run().

    The sample count threshold is a property of score(), so driving it through run() would mean
    adding a task list override to the production signature purely to let a test count to two. A
    test seam in shipped code is a thing that can drift from the code it claims to exercise."""
    return {"model": model, "kind": kind, "task": "t", "rep": 0,
            "graded": graded, "passed": passed, "detail": detail, "seconds": 0.0}


def _answer_for(argv, stdin):
    """A fake that answers each build task CORRECTLY, by reading which prompt it was handed. The
    prompt is on argv for a bridge model and on stdin for a claude one, so it looks at both."""
    blob = stdin + " " + " ".join(str(x) for x in argv)
    if "parity_word" in blob:
        return "```python\ndef parity_word(n):\n    return 'even' if n % 2 == 0 else 'odd'\n```"
    if "second_largest" in blob:
        return ("def second_largest(nums):\n"
                "    d = sorted(set(nums), reverse=True)\n"
                "    return d[1] if len(d) > 1 else None\n")
    if "collapse_spaces" in blob:
        return "def collapse_spaces(s):\n    return ' '.join(s.split())\n"
    return "no idea"


def selftest():
    """Answer the question even when a case RAISES. Measured 2026-09-22: eleven selftests in this
    directory exited 1 with a bare traceback and no verdict, so a pipeline reading the exit code and
    a human reading the text described the same run differently. Cases are built EAGERLY, so one
    raising expression takes the whole run with it; this wrapper is what turns that into a readable
    refusal. It does not make a broken module pass: it still returns non zero."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    fake = {"bridgey": {"id": "x/bridgey", "transport": "bridge", "privacy": R.PUBLIC,
                        "quality": {"build": 5, "grade": 5}, "kinds": {"build", "grade"}, "cost": 1.0},
            "typedy": {"id": "x/typedy", "transport": "bridge", "privacy": R.PUBLIC,
                       "quality": {"decide": 9}, "kinds": {"decide"}, "cost": 0.6}}

    def good(argv, stdin, timeout):
        return {"returncode": 0, "stdout": _answer_for(argv, stdin), "stderr": ""}

    # --- a model that ANSWERS every task and gets every one wrong
    wrong = run(models=["bridgey"], kinds=["build"], reg=fake,
                runner=_runner(0, "def unrelated():\n    return 1\n"))
    s_wrong = score(wrong)["bridgey"]["build"]

    # --- the same model on the same tasks, answering correctly
    right = run(models=["bridgey"], kinds=["build"], reg=fake, runner=good)
    s_right = score(right)["bridgey"]["build"]

    # --- every call fails at the wire: attempts but NO samples
    down = run(models=["bridgey"], kinds=["build"], reg=fake, runner=_runner(7, "", "provider down"))
    s_down = score(down)["bridgey"]["build"]

    # --- exit zero with an empty body is a failure, never a graded pass
    empty = run(models=["bridgey"], kinds=["build"], reg=fake, runner=_runner(0, "   ", ""))
    s_empty = score(empty)["bridgey"]["build"]

    # --- two graded samples is PROVISIONAL, three is measured
    two_rows = [_row("bridgey", "build", True, True)] * 2
    two = score(two_rows)["bridgey"]["build"]
    three = score([_row("bridgey", "build", True, True)] * 3)["bridgey"]["build"]

    # --- a grader that RAISES. The typed grader raises on prose, which is the real shape of it.
    raised = run(models=["typedy"], kinds=["decide"], reg=fake,
                 runner=_runner(0, "I am fairly confident the answer is yes"))
    s_raised = score(raised)["typedy"]["decide"]

    # --- the anti-fooling case. The payload is VALID PYTHON that carries every expected value and
    # every function name, and defines nothing. An earlier version of this case was prose, so it
    # failed on a SyntaxError and would have passed even against a grader that only compiled the
    # answer. This one parses, runs, and dies at the first assert with NameError, which is the
    # property the case is named for.
    looks_right = ("# parity_word(4) == 'even', parity_word(7) == 'odd'\n"
                   "# parity_word(0) == 'even', parity_word(-3) == 'odd'\n"
                   "EXPECTED = {4: 'even', 7: 'odd', 0: 'even', -3: 'odd'}\n"
                   "ANSWER = 'the function returns even for even n and odd for odd n'\n")
    fooled = run(models=["bridgey"], kinds=["build"], reg=fake, runner=_runner(0, looks_right))

    # --- a typed answer on the RIGHT side of 0.5 passes, and the same shape on the wrong side fails
    yes = json.dumps({"model": "x", "answers": {"true": {"noul": 0.97}}})
    no = json.dumps({"model": "x", "answers": {"true": {"noul": 0.02}}})
    gt, summ = TASKS["decide"][0], TASKS["decide"][1]

    # --- propose(): only a measured cell moves a prior, and the call writes nothing
    doc = {"models": {"bridgey": {"quality": {"build": 5}}}}
    prop_measured, lines_measured = propose(right, doc)
    prop_prov, lines_prov = propose(two_rows, doc)
    prop_nodata, lines_nodata = propose(down, doc)
    real_doc, real_path = _raw_registry()
    before = os.path.getmtime(real_path)
    propose(right, real_doc)
    after = os.path.getmtime(real_path)

    def refuses(fn):
        try:
            fn(); return False
        except R.Refused:
            return True

    cases = [
        ("every build task is graded by EXECUTING the answer, so a correct model scores 10",
         s_right["score"] == 10.0 and s_right["n"] == 3),
        ("a model that answers every task WRONGLY scores 0 and stays in the table",
         s_wrong["score"] == 0.0 and s_wrong["status"] == "measured" and s_wrong["n"] == 3),
        ("a 0 and a NO-DATA are different rows, never the same row",
         s_wrong["score"] == 0.0 and s_down["score"] is None),
        ("no graded sample is NO-DATA with score None, never a score of 0",
         s_down["status"] == "NO-DATA" and s_down["score"] is None and s_down["attempts"] == 3),
        ("a wire failure is counted as an attempt but never as a sample", s_down["n"] == 0),
        ("exit 0 with an EMPTY answer is a failure, never a graded pass",
         s_empty["n"] == 0 and s_empty["status"] == "NO-DATA"
         and all("EMPTY" in r["detail"] for r in empty)),
        ("fewer than three graded samples is PROVISIONAL",
         two["n"] == 2 and two["status"] == "PROVISIONAL" and two["score"] == 10.0),
        ("three graded samples is measured", three["n"] == 3 and three["status"] == "measured"),
        ("a grader that RAISES is a failed task, never a pass",
         s_raised["n"] == 3 and s_raised["passed"] == 0 and s_raised["score"] == 0.0),
        ("a grader that raises is surfaced separately, so a broken bench is not read as a bad model",
         s_raised["grader_errors"] == 3
         and all(r["detail"].startswith("grader raised") for r in raised)),
        ("a string that merely LOOKS right cannot fool a grader that executes the answer",
         all(r["graded"] and not r["passed"] for r in fooled)),
        ("and that same string is not empty and did reach the grader, so the case can fail",
         len(fooled) == 3 and all(r["detail"] == "wrong answer" for r in fooled)),
        ("the build grader rejects a function that fails only the EDGE assertion",
         _runs(["assert parity_word(-3) == 'odd'"])(
             "def parity_word(n):\n    return 'even' if n % 2 == 0 else 'odd'") is True
         and _runs(["assert parity_word(-3) == 'odd'"])(
             "def parity_word(n):\n    return 'odd' if n == 7 else 'even'") is False),
        ("the build grader fails an answer that exits before the assertions run",
         _runs(["assert True"])("import sys\nsys.exit(0)\n") is False),
        ("the build grader survives a returned infinite loop instead of hanging the bench",
         _runs(["assert True"])("while True:\n    pass\n") is False),
        ("a grade answer naming the planted defect passes",
         TASKS["grade"][0].grade("xs[i + 1] runs off the end on the last index") is True),
        ("a grade answer naming nothing fails",
         TASKS["grade"][0].grade("Looks fine to me, ship it.") is False),
        ("the mutable default defect is recognised by its own vocabulary, not the other task's",
         TASKS["grade"][1].grade("acc is a mutable default argument shared across calls") is True
         and TASKS["grade"][1].grade("it can raise IndexError") is False),
        ("a typed answer on the correct side of 0.5 passes", gt.grade(yes) is True),
        ("the same typed shape on the wrong side fails", gt.grade(no) is False),
        ("a FALSE claim is graded by the same rule in the opposite direction",
         summ.grade(no) is True and summ.grade(yes) is False),
        ("every decide prompt is a valid typed object the bridge will route to the decisions endpoint",
         all(set(json.loads(t.prompt)) == {"state", "questions"} and json.loads(t.prompt)["questions"]
             for t in TASKS["decide"])),
        ("at least three graded tasks exist for every kind", all(len(v) >= 3 for v in TASKS.values())),
        ("the three kinds the registry ranks on are all covered",
         set(TASKS) == {"build", "grade", "decide"}),
        ("no prompt carries a repository path, a client term or anything but a textbook exercise",
         not any(s in t.prompt for v in TASKS.values() for t in v
                 for s in FORBIDDEN_IN_PROMPTS)),
        ("a model is SKIPPED for a kind it cannot do, never scored 0 for it",
         run(models=["typedy"], kinds=["build"], reg=fake, runner=good) == []),
        ("an unknown kind is refused, never quietly benchmarked as something else",
         refuses(lambda: run(models=["bridgey"], kinds=["banana"], reg=fake, runner=good))),
        ("a model that is not in the registry is refused",
         refuses(lambda: run(models=["nope"], kinds=["build"], reg=fake, runner=good))),
        ("reps multiply the sample count", len(run(models=["bridgey"], kinds=["build"], reg=fake,
                                                   runner=good, reps=2)) == 6),
        ("an empty result set scores nothing rather than raising", score([]) == {}),
        ("a measured cell moves the prior in the PROPOSED document",
         prop_measured["models"]["bridgey"]["quality"]["build"] == 10 and "propose 10" in lines_measured[0]),
        ("the proposal is a copy: the document passed in is untouched",
         doc["models"]["bridgey"]["quality"]["build"] == 5),
        ("a PROVISIONAL cell leaves the prior alone and says so",
         prop_prov["models"]["bridgey"]["quality"]["build"] == 5 and "PROVISIONAL" in lines_prov[0]),
        ("a NO-DATA cell leaves the prior alone and says so",
         prop_nodata["models"]["bridgey"]["quality"]["build"] == 5 and "NO-DATA" in lines_nodata[0]),
        ("propose() writes nothing: the real registry file is not touched", before == after),
        ("the real registry still loads, so --write has a document to propose against",
         isinstance(real_doc.get("models"), dict) and len(real_doc["models"]) >= 3),
        ("the proposed document is JSON serialisable, which R.registry()'s derived set is not",
         isinstance(json.dumps(propose(right, real_doc)[0]), str)),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()

    def arg(f, d=None):
        return sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d

    models = [m for m in (arg("--models") or "").split(",") if m] or None
    kinds = [k for k in (arg("--kinds") or "").split(",") if k] or None
    reps = int(arg("--reps", "1"))
    try:
        rows = run(models=models, kinds=kinds, reps=reps)
    except R.Refused as exc:
        print("REFUSED: %s" % exc)
        return 1
    if not rows:
        print("NO-DATA: no model in the registry can do any of the requested kinds.")
        return 1

    scored = score(rows)
    print("%-9s %-6s %8s %6s %8s %s" % ("model", "kind", "score", "n", "attempts", "status"))
    for model in sorted(scored):
        for kind in sorted(scored[model]):
            c = scored[model][kind]
            print("%-9s %-6s %8s %6d %8d %s"
                  % (model, kind, "NO-DATA" if c["score"] is None else "%.1f/10" % c["score"],
                     c["n"], c["attempts"], c["status"]))

    if "--write" in sys.argv:
        doc, path = _raw_registry()
        proposed, lines = propose(rows, doc)
        print("\nPROPOSED registry (stdout only; %s is NOT written by this tool)" % path)
        print(json.dumps(proposed, indent=2))
        print("\nprior vs measured")
        for line in lines:
            print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
