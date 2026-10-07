#!/usr/bin/env python3
"""Ask an OpenRouter model. Key comes from the login keychain, never from a file,
never from an argument, never printed.

  0x what is the capital of Japan          (the default row)
  0x --model nvidia/nemotron-3-ultra-550b-a55b:free ...   (zero cost)
  echo "prompt" | python3 ~/.claude/bin/or_ask.py [--model ID|muse|deepseek]
      [--effort minimal|low|medium|high|xhigh] [--max N] [--account NAME]

Default model: the registry row "deepseek" (the muse row is retired since
2026-10-05: OpenRouter answers HTTP 404 model_not_found). Intended for public
or already-generalized content only.
FALLBACK_MODELS are tried in order after the requested model returns an HTTP
error, an empty answer, OR no answer at all (a network failure or no reply
within the call's --timeout, default 300, env OR_ASK_TIMEOUT_S). An empty answer
whose finish_reason is "length" means reasoning spent the whole budget, so
that model is retried once with double the budget before moving on: the same
budget reproduces the same empty answer. Every failure is printed on stderr,
an empty answer never exits 0 (with or without --json), and the [usage] line
REPORTS WHICH ID ANSWERED, so a result is never attributed to a model that did
not produce it. Tests: scripts/loop/test_or_ask.py.
--timeout IS THE WHOLE CALL'S WALL TIME (2026-09-27): every caller kills this process at the --timeout it passed, so
each attempt gets only what is left and none starts with under MIN_ATTEMPT_S; per attempt it let one call run up to four
timeouts inside a caller waiting one, and 501 loop calls ended as anonymous STALLED_AFTER_DEADLINE. --only-model tries
the requested model alone: the dispatcher refuses any substitute answer (FallbackDetected), so it passes this flag and
the 140 substitute answers it used to pay for and throw away are never asked for.
Other ids on the same key:
  meta/muse-spark-1.2-contributor  older contributor model, retained for explicit calls.
      Its model page says
      "Your prompts and outputs may be used to improve Meta's products", so it
      takes PUBLIC or already-generalized content only.
  nvidia/nemotron-3-ultra-550b-a55b:free, zero cost, proven live 2026-09-10
      (7919 x 37 = 293003). minimax/minimax-m3:free was retired upstream the
      same day (HTTP 404, "unavailable for free"). A ":free" id is free
      because the provider keeps the traffic, so PUBLIC or
      already-generalized content only.
Exit 44: no key in the keychain (NO-DATA, never a pass, never a block).
"""
import argparse, getpass, http.client, json, math, os, re, signal, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

KEYCHAIN_SERVICE = "openrouter"
DEFAULT_MODEL = "deepseek"   # a registry NAME, resolved by bridge_aliases like every --model (FX-31.7): the id lives in the registry row


class RegistryUnreadable(RuntimeError):
    """The bridge cannot name a model: the registry beside it is missing, unreadable or malformed. Nothing is sent."""


def _router():
    """model_router from THIS file's own directory (the copy installed beside it), on sys.path for this one import
    only: left in front, scripts/loop would shadow the scripts/ copies of four same named modules for whoever imported
    this file (the dispatcher does, to price attempts). Imported at call time, never at load, so a tree that ships no
    router can still import this file and refuses only when a name must be resolved."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    try:
        import model_router
    finally:
        sys.path.remove(here)
    return model_router


def bridge_aliases():
    """{alias: id} over the registry's bridge rows (each row's name plus its `aliases`), derived and never typed here
    since FX-31.6: the registry row is where a bridge id changes. TypeSafe's Jev (founder id 2026-09-18) is a DECISION
    model that only answers on DECISIONS_ENDPOINT, never chat, so its aliases are reachable only via --decisions.
    FAIL DIRECTION: no router beside this file, or a registry the router refuses, raises RegistryUnreadable naming why;
    a requested alias is never passed through as a guessed vendor id."""
    return _views()["bridge_aliases"]


def _views():
    """The registry's derived views (model_router.derive_model_views), read when a caller asks and never at import.
    FAIL DIRECTION: no router beside this file, or a registry the router refuses, raises RegistryUnreadable naming why."""
    try:
        R = _router()
    except ImportError as exc:
        raise RegistryUnreadable("no model_router.py beside %s, so no bridge alias can be resolved (%s)" % (__file__, exc))
    try:
        return R.derive_model_views(R.registry())
    except R.Refused as exc:
        raise RegistryUnreadable("the model registry refuses, so no bridge alias can be resolved: %s" % exc)


def resolve_model(requested):
    """The wire id for a --model value: a registry name or alias becomes its row's id; a vendor id (provider/name, the
    only shape OpenRouter accepts) passes through as typed. A bare name the registry does not carry REFUSES through
    RegistryUnreadable, the one refusal path main() turns into exit 46, because sending it would put an alias on the
    wire as if it were an id; the default `deepseek` is exactly such a name whenever its row is missing (review of 6069f945b).
    Every send route resolves here, never with a .get that falls back to the name."""
    aliases = bridge_aliases()
    key = requested.lower()
    if key in aliases:
        return aliases[key]
    if "/" in requested:
        return requested
    raise RegistryUnreadable("--model %r is not a vendor id (provider/name) and the model registry carries no bridge row "
                             "or alias by that name, so nothing is sent" % requested)


def __getattr__(name):
    """MODEL_ALIASES stays readable as a module attribute for its callers and tests, resolved on the read (PEP 562)."""
    if name == "MODEL_ALIASES":
        return bridge_aliases()
    if name == "FALLBACK_MODELS":
        return fallback_models()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


#: Wire format from TypeSafe's own docs (docs.typesafe.ai/primitives/*.md,
#: read 2026-09-18): {model, state, questions: {id: {type, instructions,
#: criteria?}}} in, {model, answers: {id: {type, noul | choice, ...}}, usage} out.
DECISIONS_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
DECISION_TYPES = ("noul", "choice", "score")
DECISION_ALIASES = frozenset(("jev", "typesafe"))
#: minimax/minimax-m3:free was dropped 2026-09-10: OpenRouter answered HTTP 404
#: "This model is unavailable for free", so it cost every fallback a round trip.
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def fallback_models():
    """[id, ...] the chat loop tries after the requested model fails: the registry's bridge rows flagged `fallback`,
    in row order (today nemotron, a zero cost row the loop itself never seats). Derived, never typed here, since the
    FX-31.7 follow-up: the id lives in docs/plan/model-registry.json. Raises RegistryUnreadable like bridge_aliases."""
    return _views()["bridge_fallbacks"]
# LOST CALL SETTLEMENT (2026-09-27, for the proof pair: every call's cost known from the provider). OpenRouter returns
# the generation id in the X-Generation-Id header of every call, GET /generation?id= returns its total_cost, and with
# stream: true the headers arrive when the provider starts, and closing the connection stops processing and billing
# for the providers OpenRouter lists as supporting cancellation (openrouter.ai/docs/api-reference/streaming and
# get-a-generation, read 2026-09-27; which provider serves a model is OpenRouter's routing, so the stop is not promised
# for every call, only the record is). So a chat call streams, prints each attempt before it is sent and its generation
# id the moment the headers arrive (both survive a kill), and an attempt whose reply is lost is settled from the
# provider's own record instead of being left unknown. A call is settled only when EVERY attempt it started is.
GENERATION_ENDPOINT = "https://openrouter.ai/api/v1/generation"
GEN_ID = re.compile(r"gen-[A-Za-z0-9_-]{1,120}")
SETTLE_S = 20   # of a call's wall kept back to settle a lost attempt; /generation can lag a few seconds

#: A low --max with --effort set lets hidden reasoning tokens spend the whole
#: budget before any visible content is written, which reads as an empty
#: answer and burns the one-time auto-double for nothing. Measured 2026-09-13:
#: --effort medium --max 450 failed on both muse and deepseek even after the
#: auto-double to 900, and fell through to the wrong (free, unrelated)
#: fallback model both times; --effort low succeeded at --max 1500 (muse) and
#: needed the auto-double to 1800 (deepseek). minimal/low floors below are
#: measured; medium/high/xhigh are a conservative estimate pending the same
#: measurement, not yet confirmed sufficient. Raising --max costs nothing
#: unused: it is a ceiling, billed only for tokens actually generated.
EFFORT_MAX_FLOOR = {
    "minimal": 800, "low": 1500, "medium": 3000, "high": 4000, "xhigh": 32000,
}
#: EVERY CALL RUNS AT xhigh (owner order 2026-09-26: "For all openrouter calls set them to xhigh for all models"). No
#: effort named means xhigh, a lower one is lifted and said. The xhigh ceiling floor is 32000 tokens: the 6000 it
#: replaced was an unconfirmed estimate, and a ceiling is billed only for tokens actually generated.
EFFORT_RANK = {"minimal": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4}
# THE FLOOR IS xhigh UNLESS A RUN NAMES ANOTHER (owner 2026-09-27, his words "A/B effort": the second 8 h stress run
# measures high against xhigh side by side). BROTHER_EFFORT_FLOOR lowers the floor for that run only; an unknown value
# keeps xhigh. Which effort each call asks for stays the caller's (the fan out passes each job's effort).
EFFORT_FLOOR = os.environ.get("BROTHER_EFFORT_FLOOR", "xhigh") if os.environ.get("BROTHER_EFFORT_FLOOR", "xhigh") in EFFORT_RANK else "xhigh"
MIN_ATTEMPT_S = 30   # an attempt with less wall time than this left is not started: it cannot finish inside the call


PROOF_KEYS = ("BROTHER_PROOF_PHASE", "BROTHER_PROOF_BASELINE", "BROTHER_PROOF_BASELINE_SHA256")


def lifted_effort(effort):
    """The effort a call runs at: the owner's floor (xhigh) unless a higher one is named."""
    return effort if effort in EFFORT_RANK and EFFORT_RANK[effort] >= EFFORT_RANK[EFFORT_FLOOR] else EFFORT_FLOOR


def attempt_plan(requested_model, max_tokens, effort, only_model=False):
    """Every (model, max_tokens) the chat loop in main() can send, in order: each model of the chain at the ceiling
    (--max raised to the effort floor), then once at twice it after a length-limited empty answer. main() walks this
    very list, and the dispatcher prices it as the call's worst case (objection 11), so the two cannot diverge."""
    model = resolve_model(requested_model)
    ceiling = max(max_tokens, EFFORT_MAX_FLOOR[lifted_effort(effort)])
    chain = [model] + ([] if only_model else [m for m in fallback_models() if m != model])
    return [(m, tokens) for m in chain for tokens in (ceiling, 2 * ceiling)]


def default_account():
    return os.environ.get("USER") or getpass.getuser()


def read_key(account=None):
    try:
        r = _security(account)
    except subprocess.TimeoutExpired:
        # A locked or prompting keychain blocks forever, silently.
        print("NO-DATA: the keychain did not answer within 30s (locked?)", file=sys.stderr)
        return None
    key = r.stdout.strip()
    if r.returncode != 0 or not key.startswith("sk-or-v1-"):
        return None
    return key


def _security(account):
    return subprocess.run(
        # Account pinned, never a service-only lookup: another project's item
        # shares the service name, and a service-only lookup found it (2026-08-22).
        # --account exists so an untested key can be verified side by side
        # without overwriting the live one (2026-08-23).
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
         "-a", account or default_account(), "-w"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )


def post_json(req, seconds, seen=None, attempt=0):
    """POST and decode the reply, all within `seconds` of WALL CLOCK.
    urlopen's own timeout bounds each socket read, not the call, so a reply
    that trickles a byte now and then holds it open for ever: a --timeout 300
    deepseek xhigh call ran about 20 minutes until SIGTERM on 2026-09-18.
    SIGALRM raises TimeoutError, which every caller already treats as no answer.
    The reply carries OpenRouter's own charge, so it is decoded by the loop's one strict parser: a repeated member
    (a cost of 9 then 0) raises ValueError, a lost reply of unknown cost, never a known smaller charge. Imported
    here, before the request, from this file's own directory (the insert names the copy for the parity gate) and
    only for the import: the dispatcher imports this module as scripts.loop.or_ask and never posts, so its
    sys.path is left as it found it."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import proof_ledger
    finally:
        del sys.path[0]
    def expire(signum, frame):
        raise TimeoutError("no complete reply within %ss of wall clock" % seconds)
    old = signal.signal(signal.SIGALRM, expire)
    signal.alarm(seconds)
    try:
        with urllib.request.urlopen(req, timeout=seconds) as resp:
            headers = getattr(resp, "headers", None)
            gid = headers.get("X-Generation-Id") if headers is not None else None
            if seen is not None and isinstance(gid, str) and GEN_ID.fullmatch(gid):
                seen["id"] = gid
                print("[generation] id=%s attempt=%d" % (gid, attempt), file=sys.stderr, flush=True)
            if headers is not None and "text/event-stream" in (headers.get("Content-Type") or ""):
                return sse_payload(resp, proof_ledger.loads)
            return proof_ledger.loads(resp.read().decode("utf-8"))
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def sse_payload(lines, loads=json.loads):
    """A streamed chat completion assembled into the reply shape the bridge reads: model, choices[0].message.content,
    finish_reason, usage, and an error when the provider sent one mid stream. Comment lines (": OPENROUTER PROCESSING")
    are skipped. A stream that ends before [DONE] is a lost reply (IncompleteRead), never a short answer."""
    parts, model, finish, usage, error, gid, done = [], None, None, None, None, None, False
    for raw in lines:
        line = (raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)).strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            break
        chunk = loads(data)
        if not isinstance(chunk, dict):
            raise ValueError("a stream chunk is not an object")
        gid, model = gid or chunk.get("id"), chunk.get("model") or model
        if chunk.get("error") is not None:
            error = chunk["error"]
        choices = chunk.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        if isinstance(delta.get("content"), str):
            parts.append(delta["content"])
        finish = choice.get("finish_reason") or finish
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
    if not done:
        raise http.client.IncompleteRead(("".join(parts)).encode("utf-8"))
    payload = {"id": gid, "model": model, "choices": [{"message": {"content": "".join(parts)}, "finish_reason": finish}]}
    if usage is not None:
        payload["usage"] = usage
    if error is not None:
        payload["error"] = error
    return payload


def settle(key, gid, budget_s):
    """OpenRouter's own charge for one generation, from GET /generation?id=, or None when it cannot be known inside
    budget_s. The record can lag, so a not found or a transient refusal is retried every 2 s; any other refusal ends it.
    The figure must be a finite number >= 0 that is not a bool. None is unknown, never zero."""
    if not isinstance(gid, str) or not GEN_ID.fullmatch(gid):
        return None
    end = time.monotonic() + max(0.0, float(budget_s))
    while True:
        left = end - time.monotonic()
        if left < 1:
            return None
        req = urllib.request.Request(GENERATION_ENDPOINT + "?id=" + urllib.parse.quote(gid, safe=""),
                                     headers={"Authorization": "Bearer " + key}, method="GET")
        try:
            data = post_json(req, max(1, int(left)))
            cost = (data.get("data") or {}).get("total_cost") if isinstance(data, dict) else None
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
                return float(cost)
        except urllib.error.HTTPError as e:
            if e.code not in (404, 429, 500, 502, 503, 524, 529):
                return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            pass
        time.sleep(max(0.0, min(2.0, end - time.monotonic() - 1)))


def die_loudly(signum, frame):
    """A kill must never leave empty files that read as a quiet run."""
    print("NO-DATA: or_ask stopped by %s after %ds; no answer was written"
          % (signal.Signals(signum).name, time.time() - STARTED), file=sys.stderr)
    sys.exit(128 + signum)


STARTED = time.time()


def decisions(a, key, raw):
    """--decisions: one typed decision call. NO fallback to any other model:
    a chat model's text is not a decision, so every failure exits 45 loudly
    instead of quietly answering as someone else (the probe on 2026-09-18
    showed the chat path answering as a free fallback model)."""
    try:
        body = json.loads(raw)
    except ValueError as e:
        print("NO-DATA: --decisions needs a JSON object on stdin (%s)" % e, file=sys.stderr)
        return 45
    questions = body.get("questions") if isinstance(body, dict) else None
    if not isinstance(body, dict) or "state" not in body or not isinstance(questions, dict) or not questions:
        print("NO-DATA: the object needs 'state' and a non-empty 'questions' object",
              file=sys.stderr)
        return 45
    for qid, q in questions.items():
        if (not isinstance(q, dict) or q.get("type") not in DECISION_TYPES
                or not str(q.get("instructions") or "").strip()):
            print("NO-DATA: question %r needs type in %s and non-empty instructions"
                  % (qid, "/".join(DECISION_TYPES)), file=sys.stderr)
            return 45
        if q["type"] in ("choice", "score") and not q.get("criteria"):
            print("NO-DATA: %s question %r needs criteria" % (q["type"], qid), file=sys.stderr)
            return 45
        if not isinstance(q["instructions"], str):
            q["instructions"] = json.dumps(q["instructions"])
        if isinstance(q.get("criteria"), dict):
            q["criteria"] = {k: v if isinstance(v, str) else json.dumps(v)
                             for k, v in q["criteria"].items()}
    model = resolve_model(a.model)
    req = urllib.request.Request(
        DECISIONS_ENDPOINT,
        data=json.dumps({"model": model, "state": body["state"],
                         "questions": questions}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        payload = post_json(req, a.timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500].replace(key, "<KEY>")
        print("HTTP %s from OpenRouter decisions on %s: %s" % (e.code, model, detail),
              file=sys.stderr)
        return 45
    except (urllib.error.URLError, TimeoutError, OSError, ValueError,
            http.client.HTTPException) as e:
        print("NO-DATA: no decision from %s within %ss (%r)" % (model, a.timeout, e),
              file=sys.stderr)
        return 45
    # A DECODED DECISION REPLY IS BILLED LIKE A CHAT ONE (money audit 2026-09-27, finding 3): without this line a
    # successful Jev call settled ABANDONED at an unknown cost, and the burn guard then funded nothing more.
    usd, known = attempt_cost(payload)
    print(billed_line(usd, 1, known), file=sys.stderr)
    answers = payload.get("answers") if isinstance(payload, dict) else None
    missing = [qid for qid in questions if not isinstance(answers, dict) or qid not in answers]
    if missing:
        print("NO-DATA: %s answered no decision for %s: %s"
              % (model, ", ".join(missing), json.dumps(payload)[:300]), file=sys.stderr)
        return 45
    print(json.dumps(payload, indent=2))
    parts = []
    for qid in questions:
        ans = answers[qid]
        for field in ("noul", "choice", "score"):
            if field in ans:
                parts.append("%s=%s" % (qid, ans[field]))
        if "confidence" in ans:
            parts.append("%s.confidence=%s" % (qid, ans["confidence"]))
    print("[decision] model=%s %s" % (payload.get("model") or model, " ".join(parts)),
          file=sys.stderr)
    usage = payload.get("usage") or {}
    print("[usage] input=%s output=%s model=%s"
          % (usage.get("input_tokens"), usage.get("output_tokens"),
             payload.get("model") or model), file=sys.stderr)
    return 0


def build_parser():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help="OpenRouter model id, or the muse/deepseek alias")
    p.add_argument("--effort", default=None,
                   choices=["minimal", "low", "medium", "high", "xhigh"])
    p.add_argument("--max", type=int, default=8000)
    p.add_argument("--system", default=None)
    p.add_argument("--json", action="store_true", help="print the raw response object")
    p.add_argument("--timeout", type=int,
                   default=int(os.environ.get("OR_ASK_TIMEOUT_S", "300")),
                   help="wall-clock seconds for the WHOLE call, every attempt together "
                        "(default 300; a non-streaming reply is silent until it is "
                        "finished, so raise it for long xhigh calls)")
    p.add_argument("--settle", default=None, metavar="GENERATION_ID",
                   help="print the [billed] line for one generation from the provider's own record (GET /generation), "
                        "exit 0 when known, 44 when not; sends no prompt and spends nothing")
    p.add_argument("--only-model", action="store_true",
                   help="try the requested model only, never a substitute (the caller decides substitutes)")
    p.add_argument("--account", default=None,
                   help="keychain account holding the key (default: the current user)")
    p.add_argument("--decisions", action="store_true",
                   help="send a JSON {state, questions} object on stdin to a decision "
                        "model (e.g. --model typesafe); never falls back to another model")
    p.add_argument("prompt", nargs="*",
                   help="the prompt, as plain words; omit it to read stdin instead")
    return p


def transports_refusal(env=None):
    """Why this bridge may not send under the run's transport allowlist (BROTHER_TRANSPORTS, owner 2026-09-30), or ''.
    The ONE parser is model_router.transports_allowed beside this file (the deployed copy sits beside its own
    model_router); the setting present but unreadable refuses too. Checked before the key is read and before any
    request, so the caller gates are defense in depth and this file is the source."""
    env_map = os.environ if env is None else env
    raw = env_map.get("BROTHER_TRANSPORTS")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import model_router
        allowed = model_router.transports_allowed(env_map)
    except Exception as exc:   # sbe: allow-silent the refusal names why; nothing is sent
        return "BROTHER_TRANSPORTS=%s is set but the transport allowlist could not be read (%s: %s)" % (raw.strip(), type(exc).__name__, str(exc)[:100])
    if allowed is not None and "bridge" not in allowed:
        return "BROTHER_TRANSPORTS=%s allows no bridge transport: this bridge sends nothing" % raw.strip()
    return ""


def main():
    """The whole call, with the one refusal every path shares: a registry the bridge cannot read names no model, so
    nothing is sent and the exit is the refusal code (46), never 0 and never a call on the alias as typed."""
    try:
        return _main()
    except RegistryUnreadable as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 46


def _main():
    p = build_parser()
    a = p.parse_args()
    why = transports_refusal()
    if why:
        print("REFUSED: " + why, file=sys.stderr)
        return 46
    if a.timeout < 1:
        p.error("--timeout must be at least 1 second")
    signal.signal(signal.SIGTERM, die_loudly)
    if a.settle is not None:
        # settles a call a caller had to kill; it sends no prompt, so neither the effort floor nor the proof
        # reservation (which govern spending) applies
        key = read_key(a.account)
        if key is None:
            print("NO-DATA: no valid key for service '%s' account '%s'" % (KEYCHAIN_SERVICE, a.account or default_account()), file=sys.stderr)
            return 44
        cost = settle(key, a.settle, a.timeout)
        print(billed_line(cost if cost is not None else 0.0, 1, cost is not None), file=sys.stderr)
        return 0 if cost is not None else 44

    if lifted_effort(a.effort) != a.effort:
        if a.effort is not None:
            print("NOTE: --effort %s is below the owner's floor; the call runs at %s" % (a.effort, EFFORT_FLOOR), file=sys.stderr)
        a.effort = EFFORT_FLOOR
    floor = EFFORT_MAX_FLOOR.get(a.effort) if a.effort else None
    # A PROOF PHASE SPENDS ONLY THROUGH THE DISPATCHER (objection 5, B5-21): a call must carry the reservation the
    # dispatcher registered for it, and the dispatcher's --max is the real ceiling it priced, so a raise refuses
    # instead. Both refuse before the key is read and before any request.
    if any(os.environ.get(key) for key in PROOF_KEYS):
        if not os.environ.get("BROTHER_DISPATCH_RESERVATION"):
            print("NO-DATA: a proof phase call must come through the dispatcher's reservation "
                  "(BROTHER_DISPATCH_RESERVATION is unset); nothing was sent", file=sys.stderr)
            return 44
        if floor and a.max < floor:
            print("NO-DATA: --max %d is below the %d floor for --effort %s, and a proof phase call cannot raise the "
                  "ceiling its dispatcher priced; nothing was sent" % (a.max, floor, a.effort), file=sys.stderr)
            return 44
    if floor and a.max < floor:
        print("NOTE: --max %d is below the measured floor for --effort %s (%d); "
              "raising it. Reasoning can spend the whole budget before writing "
              "any visible content, which reads as an empty answer and wastes "
              "the one-time auto-double; a higher --max costs nothing unless "
              "actually used." % (a.max, a.effort, floor), file=sys.stderr)
        a.max = floor

    key = read_key(a.account)
    if key is None:
        print("NO-DATA: no valid key for service '%s' account '%s'. Place it by hand, then retry."
              % (KEYCHAIN_SERVICE, a.account or default_account()), file=sys.stderr)
        return 44

    prompt = " ".join(a.prompt) if a.prompt else sys.stdin.read()
    if not prompt.strip():
        print("NO-DATA: no prompt. Pass it as words, or pipe it on stdin.", file=sys.stderr)
        return 44

    if decision_call(a):
        # Jev is a typed decision model, not a chat model. Claude sessions often
        # invoke the bridge without remembering the optional flag, so recognize a
        # valid decision object here and send it to the typed endpoint. Plain text
        # must fail loudly instead of falling through to an unrelated chat model.
        if not a.decisions:
            try:
                decision_object = json.loads(prompt)
            except ValueError:
                decision_object = None
            if not (isinstance(decision_object, dict) and "state" in decision_object and "questions" in decision_object):
                print("NO-DATA: Jev is a typed decision model; pass JSON with state and questions, "
                      "or use --decisions. It does not answer chat prompts.", file=sys.stderr)
                return 45
        return decisions(a, key, prompt)

    msgs = []
    if a.system:
        msgs.append({"role": "system", "content": a.system})
    msgs.append({"role": "user", "content": prompt})
    requested_model = resolve_model(a.model)
    plan = attempt_plan(a.model, a.max, a.effort, a.only_model)
    chain = [m for m, _ in plan[::2]]
    wall_end, tried = time.monotonic() + a.timeout, 0
    billed, attempts, billed_known = 0.0, 0, True   # summed over EVERY answered attempt, the empty and drained ones too
    retry_due = False
    for model, max_tokens in plan:
        # a doubled attempt runs only right after its own model spent the whole budget on an empty answer
        if max_tokens != a.max and not retry_due:
            continue
        retry_due = False
        left = wall_end - time.monotonic()
        if tried and left < MIN_ATTEMPT_S:   # the first attempt always runs: the caller chose that budget
            print("NO-DATA: %.0f s of the call's %d s are left, not enough to start %s; not tried"
                  % (max(left, 0), a.timeout, model), file=sys.stderr)
            continue
        wait = max(1, min(a.timeout, int(math.ceil(left))))
        tried += 1
        # EVERY ATTEMPT RETURNS ITS BILLED COST (A/B/C test 2026-09-23: the account meter read 9.21 USD for arm O while
        # the run ledger booked 2.22, because a drained, retried or fallen back attempt is billed by OpenRouter and only the
        # final attempt's usage was ever printed). usage.include asks OpenRouter for the charge itself, per attempt.
        body = {"model": model, "messages": msgs, "max_tokens": max_tokens, "usage": {"include": True}, "stream": True}
        # the stream keeps SETTLE_S of its wall back, so a lost attempt is settled before the caller's own deadline
        stream_wait = wait - SETTLE_S if wait > 2 * SETTLE_S else wait
        seen = {}
        if a.effort:
            body["reasoning"] = {"effort": a.effort}
        req = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + key},
            method="POST",
        )
        print("[attempt] %d model=%s" % (tried, model), file=sys.stderr, flush=True)
        try:
            payload = post_json(req, stream_wait, seen, tried)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500].replace(key, "<KEY>")
            print("HTTP %s from OpenRouter on %s: %s" % (e.code, model, detail), file=sys.stderr)
            # an error returned before the response is committed is a plain JSON refusal, not a generation: unbilled
            print("[attempt] %d unbilled http=%s" % (tried, e.code), file=sys.stderr, flush=True)
            continue
        # http.client.HTTPException covers IncompleteRead, a response dropped
        # mid-body: a network failure like the rest, never a traceback (2026-09-12).
        except (urllib.error.URLError, TimeoutError, OSError, ValueError,
                http.client.HTTPException) as e:
            # A LOST REPLY IS AN UNKNOWN BILL (money audit 2026-09-27, finding 1): the request may have reached the
            # provider and been charged before the reply was lost, so it is an attempt with no figure, never zero,
            # UNLESS the provider's own record of its generation settles it (closing the stream stopped the billing).
            attempts += 1
            lost = settle(key, seen.get("id"), max(1, wall_end - time.monotonic())) if seen.get("id") else None
            if lost is None:
                billed_known = False
            else:
                billed += lost
            print("no answer from %s within %ss (%r); %s; trying the next model"
                  % (model, stream_wait, e, "settled at %.6f USD from the provider's record" % lost if lost is not None
                     else "its cost is unknown"), file=sys.stderr)
            continue
        attempts += 1
        _cost, _known = attempt_cost(payload)
        if not _known and seen.get("id"):   # an answer without its charge: the provider's record settles it
            _settled = settle(key, seen["id"], max(1, wall_end - time.monotonic()))
            if _settled is not None:
                _cost, _known = _settled, True
        billed += _cost
        billed_known = billed_known and _known   # an attempt without a usable figure makes the sum a floor, and it says so
        choices = payload.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        if (content or "").strip():
            break
        finish = choices[0].get("finish_reason") if choices else None
        if finish == "length" and max_tokens == a.max:
            print("NO-DATA: %s spent its whole budget of %s tokens before answering; "
                  "retrying it once with %s" % (model, max_tokens, max_tokens * 2),
                  file=sys.stderr)
            retry_due = True
            continue
        print("NO-DATA: empty answer from %s: %s" % (model, json.dumps(payload)[:300]), file=sys.stderr)
    else:
        print("NO-DATA: no model answered (tried %s)." % ", ".join(chain), file=sys.stderr)
        print(billed_line(billed, attempts, billed_known), file=sys.stderr)   # a failed call is billed too
        return 44

    if a.json:
        print(json.dumps(payload, indent=2))
        return 0
    answered_by = payload.get("model")
    # PROVENANCE (2026-09-19): the model that actually produced `content` is
    # never asserted anywhere inside `content` itself -- a prompt phrased as
    # a direct address to the requested model gets answered in that same
    # voice ("my 8.5", "my 9.0") by whichever model actually responded, and
    # the ONLY place the real responder was ever named was the trailing
    # [usage] line on stderr, easy to miss reading a long real answer
    # (happened twice the same night this was written, caught only by
    # checking that line by hand, never from the content). A caller piping
    # stdout alone, or skimming a long answer, must not be able to mistake
    # a substitute responder's words for the requested model's own.
    if answered_by and answered_by != requested_model:
        print("[PROVENANCE: requested %s, actually answered by %s -- read "
              "this content as %s speaking, not %s]"
              % (requested_model, answered_by, answered_by, requested_model))
    print(content)
    usage = payload.get("usage") or {}
    print("\n[usage] prompt=%s completion=%s model=%s"
          % (usage.get("prompt_tokens"), usage.get("completion_tokens"),
             payload.get("model")), file=sys.stderr)
    print(billed_line(billed, attempts, billed_known), file=sys.stderr)
    return 0


def billed_line(billed, attempts, known):
    """The one line the ledger reads for a call's real charge: OpenRouter's own billed cost summed over every answered
    attempt. known=no means at least one attempt came back without a figure, so the sum is a floor, never the charge.
    A sum that overflowed is not a charge either (money audit 2026-09-27, finding 7)."""
    known = known and math.isfinite(billed)
    return "[billed] usd=%.6f attempts=%d known=%s" % (billed, attempts, "yes" if known else "no")


def attempt_cost(payload):
    """(usd, True) for OpenRouter's own charge on one decoded reply, else (0.0, False). The figure must be a finite
    number >= 0 that is not a bool: a negative charge offset another attempt's real one and the sum read as fully
    measured, a non finite one poisons it (money audit 2026-09-27, finding 7). Unknown is never zero."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    cost = usage.get("cost") if isinstance(usage, dict) else None
    try:
        ok = isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0
    except OverflowError:
        ok = False
    return (float(cost), True) if ok else (0.0, False)


def decision_call(a):
    """True when main() sends this request to the decisions endpoint (--decisions, or the Jev alias), never through the
    chat loop. Such a request carries no max_tokens, so no chat attempt plan bounds its cost (finding 3)."""
    return bool(a.decisions) or a.model.lower() in DECISION_ALIASES


if __name__ == "__main__":
    sys.exit(main())
