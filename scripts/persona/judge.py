#!/usr/bin/env python3
"""The headless Claude CLI judges each persona transcript AS the persona (it
replaced the OpenRouter Muse bridge, row SR-2). Reads results.jsonl and
transcripts/, writes judgments.jsonl (one per scenario not yet judged).
Archetype content only; the transcripts are of fixture repositories. The
private-term scrub list lives outside this repository and is never a literal
here, per the estate's private-terms-in-public rule.
Exit 0 when every result has a judgment, 1 if any judge call failed, 2
NO-DATA when there is nothing to judge or the term list cannot be read.
"""
import argparse
import json
import os
import re
import subprocess
import sys

D = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EVIDENCE_DIR = os.environ.get(
    "PERSONA_EVIDENCE_DIR",
    os.path.expanduser("~/.claude/evidence/persona-dogfood-2026-09-07"),
)
JUDGE_MODEL = "claude-cli:sonnet"
JUDGE_ARGV = ["claude", "-p", "--output-format", "json", "--model", "sonnet", "--fallback-model", "haiku"]


def private_terms(path=None):
    """Read the scrub list from $PERSONA_PRIVATE_TERMS, else
    ~/.brothersbe-private-names (resolved here, at call time, never as a
    default argument, so an env var set after import is honored). One term
    per line; blank lines and '#' comments are ignored. Raises OSError when
    the file is missing or unreadable, so callers fail closed.
    """
    p = path if path is not None else os.environ.get(
        "PERSONA_PRIVATE_TERMS", os.path.expanduser("~/.brothersbe-private-names"))
    with open(p) as fh:
        lines = fh.read().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")], p


def scrub(text, terms):
    """Replace every private term with [client]. A term of five characters
    or fewer is matched exactly (case sensitive); a longer one is matched
    case insensitively, per the estate's push-gate matching rule."""
    for term in terms:
        if not term:
            continue
        if len(term) <= 5:
            text = text.replace(term, "[client]")
        else:
            text = re.sub(re.escape(term), "[client]", text, flags=re.IGNORECASE)
    return text


def build_argparser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence-dir", default=DEFAULT_EVIDENCE_DIR,
                     help="where transcripts/, results.jsonl and judgments.jsonl live (default: $PERSONA_EVIDENCE_DIR or the 2026-09-07 evidence dir)")
    return ap


def main(argv=None):
    args = build_argparser().parse_args(argv)
    terms_path = os.environ.get("PERSONA_PRIVATE_TERMS") or os.path.expanduser("~/.brothersbe-private-names")
    try:
        terms, terms_path = private_terms()
    except OSError:
        print("NO-DATA: private terms file not readable at %s; refusing to send any transcript to the judge" % terms_path)
        return 2
    evidence_dir = args.evidence_dir
    T = os.path.join(evidence_dir, "transcripts")
    scen_data = json.load(open(os.path.join(D, "scenarios.json")))
    personas = {p["id"]: p for p in scen_data["personas"]}
    scen = {s["id"]: s for s in scen_data["scenarios"]}
    res_path = os.path.join(T, "results.jsonl")
    out_path = os.path.join(evidence_dir, "judgments.jsonl")
    if not os.path.exists(res_path):
        print("NO-DATA: no results.jsonl yet")
        return 2
    done = set()
    if os.path.exists(out_path):
        for line in open(out_path):
            try:
                j = json.loads(line)
                done.add((j["scenario"], j.get("round", 1)))
            except (OSError, ValueError, TypeError):  # sbe: allow-silent an unreadable or malformed past judgment cannot prove completion, so it is not added to done
                continue
    todo = []
    for line in open(res_path):
        try:
            r = json.loads(line)
        except (OSError, ValueError, TypeError):  # sbe: allow-silent a malformed result line has no scenario to judge, so the remaining transcript is still processed
            continue
        if (r.get("scenario"), r.get("round", 1)) not in done:
            todo.append(r)
    if not todo:
        print("nothing new to judge (%d judged)" % len(done))
        return 0
    fails = 0
    for r in todo:
        sid = r["scenario"]
        pid = r.get("persona", sid[:2])
        rnd = r.get("round", 1)
        tpath = os.path.join(T, "%s-%s.md" % (pid, sid)) if rnd == 1 else os.path.join(T, "%s-%s-r%s.md" % (pid, sid, rnd))
        transcript = open(tpath).read()[:24000] if os.path.exists(tpath) else "(no transcript file)"
        transcript = scrub(transcript, terms)
        brief = ("You ARE this person, reviewing a transcript of your own session with the Brother plugin for Claude Code:\n%s\n\nThe scenario you were running:\n%s\n\nThe actor's self report:\n%s\n\nThe transcript:\n%s\n\n"
                 "Answer as this person, honestly, in JSON only: {\"scenario\":\"%s\",\"verdict\":\"PASS|FAIL|NO-DATA\",\"trust_after\":1-5,\"would_use_tomorrow\":true|false,"
                 "\"what_i_wanted\":\"one sentence\",\"what_i_got\":\"one sentence\",\"worst_moment\":\"quote the exact line from the transcript that hurt most, or none\","
                 "\"root_cause_guess\":\"one sentence, name the surface if you can\",\"one_fix\":\"the single change that would have made me trust it\",\"rubric_agreement\":\"agree|disagree with the actor's verdict and why, one sentence\"}. No prose outside the JSON."
                 % (json.dumps(personas.get(pid, {}), ensure_ascii=False), json.dumps(scen.get(sid, {}), ensure_ascii=False), json.dumps(r, ensure_ascii=False), transcript, sid))
        try:
            p = subprocess.run(JUDGE_ARGV, input=brief, capture_output=True, text=True, timeout=300)
            if p.returncode != 0:
                # An account limit exits 1 with the reason in the envelope's
                # result; anything else leaves it on stderr.
                try:
                    why = str(json.loads(p.stdout).get("result") or "")
                except (ValueError, TypeError, AttributeError):
                    why = ""
                raise ValueError("claude CLI exited %s: %r"
                                 % (p.returncode, (why or p.stderr or "").strip()[:200]))
            envelope = json.loads(p.stdout)
            if envelope.get("is_error"):
                raise ValueError("claude CLI is_error true: %r" % str(envelope.get("result", ""))[:200])
            result_text = envelope.get("result") or ""
            if not result_text:
                raise ValueError("claude CLI returned an empty result field")
            s, e = result_text.find("{"), result_text.rfind("}")
            j = json.loads(result_text[s:e + 1])
            # A headless child that loads this machine's hooks once answered
            # {"ok": true, "reason": ...} here: JSON, but no judgment.
            if not isinstance(j, dict) or j.get("verdict") not in ("PASS", "FAIL", "NO-DATA"):
                shape = sorted(j)[:8] if isinstance(j, dict) else type(j).__name__
                raise ValueError("the judge's answer carried no PASS, FAIL or NO-DATA "
                                 "verdict (got %s), so it is not a judgment" % (shape,))
            j["scenario"] = sid
            j["persona"] = pid
            j["round"] = rnd
            j["judge_model"] = JUDGE_MODEL
        except subprocess.TimeoutExpired:
            fails += 1
            j = {"scenario": sid, "persona": pid, "round": rnd, "verdict": "NO-DATA", "judge_error": "claude CLI timed out after 300s", "judge_model": JUDGE_MODEL}
        except OSError as ex:
            fails += 1
            j = {"scenario": sid, "persona": pid, "round": rnd, "verdict": "NO-DATA", "judge_error": "claude CLI not runnable: %s" % ex, "judge_model": JUDGE_MODEL}
        except Exception as ex:
            fails += 1
            j = {"scenario": sid, "persona": pid, "round": rnd, "verdict": "NO-DATA", "judge_error": "%s" % ex, "judge_model": JUDGE_MODEL}
        with open(out_path, "a") as f:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")
        print(sid, j.get("verdict"), "trust", j.get("trust_after"), "|", str(j.get("one_fix", ""))[:90])
    print("judged %d, failed %d" % (len(todo), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
