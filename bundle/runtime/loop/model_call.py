#!/usr/bin/env python3
"""Call the best available model for a task, and fall over to the next when the wire fails.

model_router.py decides WHICH models are eligible and in what order. This file is the half that
actually talks to them. Each transport's command line and verdict live in its adapter under
scripts/loop/adapters (FX-31.5), behind one contract, because the three have nothing in common:

  bridge   a python CLI, prompt on argv, answer on stdout, usage line on stderr
  claude   `claude -p --model <id>`, prompt on STDIN
  codex    `codex exec -m <id> -C <trusted dir>`, prompt on argv, and it BLOCKS on stdin unless
           stdin is closed, and refuses outright unless -C names a directory it trusts

Both codex requirements were measured on 2026-09-21, each costing a failed probe: run from /tmp it
exits 1 with "Not inside a trusted directory", and with the prompt already on argv it still prints
"Reading additional input from stdin" and hangs.

THE FAILOVER RULE, which is the whole reason this is not a one line subprocess call. Fail over on
INFRASTRUCTURE and never on an ANSWER:

  fail over   a non zero exit, a timeout, an EMPTY body at exit zero, an unparseable answer where
              the caller asked for JSON, a transport that is not installed
  return      a model's valid negative result, a grade of FAIL, a low probability, or a reasoned
              refusal to do the work

The second list is the important one. Retrying a task across providers until one of them says yes
is not resilience, it is how a pipeline manufactures a false green, and this estate has measured
that failure in four other places this month.

AN EMPTY ANSWER AT EXIT ZERO IS A FAILURE. `claude -p` with an expired session printed an
authentication error and exited 1, but the piped check read exit 0 from the pipe rather than from
the command. Three exit codes were misread that way in one evening, so nothing here pipes.

usage:
  model_call.py --kind build --sensitivity public --prompt "..."
  model_call.py --selftest
"""
import tempfile, json, os, re, subprocess, sys, time

# A SELFTEST BUILDS ITS OWN WORLD (FX-31's close, 2026-10-05 22:31): the closer ran this file's --selftest inside a proof
# phase, where the suite environment keeps the proof keys and drops the run directory, so proof_ledger refused every fake
# call before its fake runner ran and the unit read CLOSE-RED on a tree where the same command passed by hand. Twelve
# names flipped the verdict, each alone: the three proof keys, the run directory, the program record, the token switch,
# both efforts, the transports list and the three root overrides. So when this file RUNS AS the selftest, no BROTHER_
# name of the caller's survives. It is done HERE, above every import of the loop's own modules, because the code root,
# the pool and the scratch are resolved while those load; selftest() then names the few it needs. A caller that IMPORTS
# this module is untouched. Proven at the entry point, one name per case: scripts/test_model_call_selftest_world.py.
if __name__ == "__main__" and "--selftest" in sys.argv:
    for _name in [n for n in os.environ if n.startswith("BROTHER_")]:
        del os.environ[_name]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_router as R

BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "or_ask.py")
CLAUDE = None   # an override (tests); None resolves the installed CLI at call time through R.claude_bin()
# The Codex binary is resolved when a Codex call is made (R.codex_bin, through the code root), never at import: a
# proof phase with no code root must reach this module's own refusal, not crash while it loads.
import claude_ledger as CL  # noqa: E402
import breaker as BR  # noqa: E402  the one CONFIG classifier and the mandatory configuration breaker (2026-09-30)
import loop_switches as SW  # noqa: E402
# THE ADAPTER BOUNDARY (FX-31.5): every transport's command line and verdict come from its adapter, in the contract's
# one status vocabulary (adapters.STATUS_OK, STATUS_FAILED, STATUS_PROVIDER_REFUSED). This module resolves the program
# and the trusted root and hands them in; it keeps no transport branch of its own.
import adapters as A  # noqa: E402
from adapters.bridge import BridgeAdapter  # noqa: E402
from adapters.claude import ClaudeAdapter  # noqa: E402
from adapters.codex import CodexAdapter  # noqa: E402
LOOP_TOKEN_SWITCH = "BROTHER_LOOP_TOKEN"
def _codex_root():
    """Delegates to model_router.repo_root. ONE implementation, because two copies of the same path
    logic in two files is how they drift, and this exact logic already drifted once."""
    return R.repo_root()


# THE PLUGIN RUNTIME COMES FROM THE CODE ROOT (U3, B5-08): in a proof phase that is the frozen candidate the launcher
# names, never the launch worktree the loop lands into. A proof phase with no code root imports nothing and refuses
# every call below, the same direction as a missing pool.
ADMISSION_WHY = "the shared dispatch pool (the plugin runtime) is not shipped here, so no call runs outside it"
try:
    ROOT = R.code_root()
except R.Refused as exc:
    ROOT, ADMISSION_WHY = None, str(exc)
if ROOT:
    sys.path.insert(0, ROOT)
# THE POOL IS OPTIONAL TO LOAD, NEVER TO CALL (finding 32, 2026-09-26): the public export ships scripts/ and not plugin/, and
# importing this at load made the module unusable there. Where the pool is absent every call is refused in call_one:
# the shared admission pool is what bounds concurrent paid calls, so there is no safe call without it.
try:
    if not ROOT:
        raise ImportError(ADMISSION_WHY)
    from plugin.runtime.brother.core import dispatch_semaphore as admission
except ImportError:
    admission = None


class SpawnFailed(OSError):
    """subprocess.Popen itself raised: no child exists, so nothing was sent and nothing can have been billed. The only
    failure that records a Claude call at a known zero (outcome not_started); any error after the spawn is unknown."""


class Attempt(object):
    """One try against one model. Kept as an object because the CALLER needs the whole history:
    which models were tried, why each was abandoned, and what finally answered. A failover that
    reports only its winner hides the cost it spent getting there."""

    def __init__(self, model, ok, answer, detail, seconds, failure=None, status=None):
        self.model, self.ok, self.answer, self.detail, self.seconds = model, ok, answer, detail, seconds
        # failure is None or "CONFIG": the program or endpoint does not know this model. A CONFIG attempt is not the
        # model's failure and not a round's: callers hold on it (CONFIG_WAIT) and never count it against quality.
        self.failure = failure
        # status is the wire vocabulary of breaker.STATUSES. An unlabelled result is never healthy: an unlabelled
        # failure reads MALFORMED, so no caller can mistake "we did not classify it" for an answer.
        if status is not None and (not isinstance(status, str) or status not in BR.STATUSES):
            raise ValueError("status must be None or a member of breaker.STATUSES")
        self.status = status if status is not None else ("ANSWERED" if ok else "MALFORMED")
        self.raw = None   # the transport's {returncode, stdout, stderr} when a program ran, else None

    def __repr__(self):
        return "<%s %s %s>" % (self.model, "ok" if self.ok else "FAILED", self.detail[:50])


# THE BRIDGE RUNS AT xhigh AND max MOVES WITH IT (owner law 2026-09-21; measured again 2026-09-22: the finisher's first
# real brief, spec plus build plus real files, sat 15 minutes at "--effort low --max 8000" and timed out). Reasoning and
# answer share one budget, so a low ceiling on a large brief produces an empty answer or a timeout, never a cheaper
# answer. The ceiling bills only what is generated, so the model's full ceiling costs nothing when unused.
# DEEPSEEK IS NEVER BELOW xhigh (owner order 2026-09-22, his words: "Deepseek should always be on xhigh or higher"). The
# environment may raise the bridge effort, never lower it under xhigh: a lower value is refused here, not corrected silently.
EFFORT_RANK = {"minimal": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5}
def bridge_effort(env=None):
    v = ((os.environ if env is None else env).get("BROTHER_BRIDGE_EFFORT") or "xhigh").strip().lower()
    if EFFORT_RANK.get(v, -1) < EFFORT_RANK["xhigh"]:
        raise R.Refused("BROTHER_BRIDGE_EFFORT=%r is below xhigh; the owner's floor for the bridge is xhigh" % v)
    return v
BRIDGE_EFFORT = "xhigh"   # the floor; the argv reads bridge_effort() at call time so the refusal is live, not import time
BRIDGE_MAX = {"deepseek": 384000, "muse": 943718, "jev": 32000}   # the fan out's own table, MODEL_MAX_TOKENS


CLAUDE_TRIM = ["--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config", "--exclude-dynamic-system-prompt-sections"]
# NO TRANSCRIPT PER CALL (owner law 2026-09-24, resource footprint): the call's own JSON result carries its usage and
# cost, so nothing is written under ~/.claude/projects; the ledger row written at the end holds what a transcript held.
CLAUDE_IO = ["--output-format", "json", "--no-session-persistence"]


def claude_effort(env=None, model_id=""):
    """The effort a Claude child runs at, by the ONE rule, the Claude adapter's (FX-31.5 review gap 2): the model's
    floor (adapters.claude.FLOOR_BY_MODEL), lifted by BROTHER_CLAUDE_EFFORT when that names a higher level, never below
    the floor. A value that names no level is REFUSED (R.Refused), for a one shot call and a native session alike: it
    is never read as the floor, because an arm that silently runs at the floor is the A/B run that compared nothing."""
    try:
        return ClaudeAdapter(env=env)._effort(str(model_id))
    except A.Refused as exc:
        raise R.Refused(str(exc))


import collections, hashlib   # noqa: E402
#: ONE IMMUTABLE DESCRIPTION OF A CALL (2026-09-30). The intake's reachability proof and every production call are built
#: by the same function from the same registry row, so a proof can never exercise flags production does not send.
#: profile is the digest of everything but the prompt: two calls with equal profiles differ only in what they ask.
Invocation = collections.namedtuple("Invocation", "name model_id transport program argv stdin profile")
PROMPT_MARK = "\x00PROMPT\x00"


def build_invocation(name, prompt, timeout, reg, program=None):
    """The Invocation for one model. program overrides the resolved program (the reachability proof of a candidate);
    None is the program every production call resolves. Raises Refused for an unknown transport."""
    argv, stdin, _ = _argv(name, prompt, timeout, reg, program=program)
    m = reg[name]
    shape = [PROMPT_MARK if a == prompt else a for a in argv] + ["stdin=" + ("prompt" if stdin == prompt else repr(stdin))]
    digest = hashlib.sha256(json.dumps(shape).encode("utf-8")).hexdigest()[:16]
    return Invocation(name, m.get("id") or name, m["transport"], argv[0] if m["transport"] != "bridge" else argv[1],
                      tuple(argv), stdin, digest)


def _adapter(transport, program=None, root=None):
    """The transport's adapter (FX-31.5). The contract's adapter_for refuses an unknown or unhashable transport by
    name BEFORE any program is resolved; program and root matter only to argv, so a judge needs neither."""
    kind = A.adapter_for(transport).transport
    if kind == "codex":
        return CodexAdapter(program=program, root=root)
    return (BridgeAdapter if kind == "bridge" else ClaudeAdapter)(program=program)


def _argv(name, prompt, timeout, reg, program=None):
    """The command line for one model, from its adapter. Raises R.Refused, naming the model, for an unknown transport
    or a command line the adapter will not build (no program, no trusted root, an empty prompt, an effort nobody can
    read): nothing is guessed, because a guessed transport sends content somewhere nobody intended. The bridge always
    runs the gated BRIDGE (test_bridge_spawners keeps that path behind the allowlist); program overrides the resolved
    Claude or Codex program (a candidate's reachability proof)."""
    m = reg[name]
    resolve = {"bridge": lambda: (BRIDGE, None),
               "claude": lambda: (program or CLAUDE or R.claude_bin(), None),
               "codex": lambda: (program or R.codex_bin(), _codex_root())}
    try:
        kind = _adapter(m["transport"]).transport   # the contract's refusal for an unknown transport, before any program is resolved
        binary, root = resolve[kind]()
        return _adapter(kind, binary, root).argv(name, prompt, timeout, m)
    except A.Refused as exc:
        raise R.Refused("model %r: %s" % (name, exc))


def _refused_run(why):
    """A runner whose spawn FAILS: the adapter refused the command line, so no child can exist and nothing was billed.
    call_one runs it in place of the transport, so the refusal takes the spawn failure path every caller already
    handles (a Claude call still writes its not_started row at a known zero) and the chain moves on, never a raise."""
    def run(argv, stdin, timeout):
        raise SpawnFailed(0, why)
    return run


USAGE_RE = re.compile(r"\[usage\] (?:prompt|input)=(\d+|None) (?:completion|output)=(\d+|None) model=(\S+)")


def _claude_doc(out):
    """The result record `claude -p --output-format json` printed, or None."""
    try:
        d = CL.proof_ledger.loads(out or "")   # a repeated member (a cost of 9 then 0) refuses: the cost is unknown
    except ValueError:
        return None   # sbe: allow-silent None is the refusal: _claude_usage records the cost unknown and _claude_result fails the call
    return d if isinstance(d, dict) else None


def _claude_usage(out):
    """The terminal row's cost and usage fields from the result record. The cost is recorded whatever the exit code
    (a failed call can still have been billed); no record means cost null, which is unknown, never zero."""
    d = _claude_doc(out)
    if d is None:
        return {"cost_usd": None}
    u = d.get("usage") or {}
    cc = u.get("cache_creation") or {}
    return {"cost_usd": d.get("total_cost_usd"), "input": u.get("input_tokens"), "output": u.get("output_tokens"),
            "cache_read": u.get("cache_read_input_tokens"), "cache_write_1h": cc.get("ephemeral_1h_input_tokens"),
            "cache_write_5m": cc.get("ephemeral_5m_input_tokens"), "is_error": bool(d.get("is_error"))}


def _claude_result(out):
    """(answer text, "") from `claude -p --output-format json`, or ("", why). An error result, an unparseable
    document or an empty result is a failure, never an answer."""
    d = _claude_doc(out)
    if d is None:
        return "", "claude output is not the JSON result it was asked for"
    if d.get("is_error"): return "", "claude returned an error result: %s" % str(d.get("result"))[:90]
    ans = d.get("result")
    if not isinstance(ans, str) or not ans.strip(): return "", "claude returned an empty result"
    return ans, ""


def _claude_run(path, started, argv, stdin, timeout, runner):
    """Run one registered Claude call (its START row, from claude_ledger.start, is `started`) and write its one
    terminal row in a finally (U7): a known zero only when the spawn itself failed, the parsed cost for any exit code,
    and null (unknown) for a timeout or an error after the spawn. A terminal row that cannot be written is said on
    stderr; its START stays pending until it expires, then reads NO-DATA."""
    call_id = started["call"]
    done = {"event": "done", "model": started.get("model"), "call": call_id, "cost_usd": None, "outcome": "exit-unknown"}
    done.update((k, started[k]) for k in ("run", "attempt") if k in started)
    t0 = time.time()
    try:
        # THE TOKEN LANE IS THE ONLY LANE ONCE THE SWITCH IS ON (review 2026-10-03): a headless Claude call that is not
        # a native seat (argv[0] sandbox-exec) would run on this machine's own login with the Keychain open; refused
        # as a spawn failure (nothing sent, nothing billed) naming the two ways out.
        if SW.on(LOOP_TOKEN_SWITCH) and (not argv or os.path.basename(argv[0]) != "sandbox-exec"):
            raise SpawnFailed(0, "BROTHER_LOOP_TOKEN is on: only native seats run on the loop login "
                                 "(set BROTHER_CLAUDE_NATIVE=on, or turn BROTHER_LOOP_TOKEN off)")
        r = (runner or _run)(argv, stdin, timeout)
        done.update(_claude_usage(r.get("stdout")), outcome="exit %s" % r.get("returncode"))
        return r
    except SpawnFailed:
        done.update(cost_usd=0, outcome="not_started")
        raise
    except subprocess.TimeoutExpired:
        done["outcome"] = "timeout"
        raise
    finally:
        done["seconds"] = round(time.time() - t0, 1)
        try:
            CL.finish(path, done)
        except OSError as exc:
            sys.stderr.write("model_call: the terminal row of Claude call %s could not be written (%s); its START stays "
                             "pending until it expires, then reads NO-DATA\n" % (call_id, exc))


def _admit_without_a_row(timeout, env=None):
    """PROOF ADMISSION FOR A TRANSPORT THAT KEEPS NO LEDGER (money audit 2026-09-27, finding 2): the Codex transport
    took a slot and ran its child after the run's UNFUNDED ending marker and past its deadline. It passes the same
    proof_ledger.admit_locked a Claude START passes, under the Claude ledger lock mark_ending also takes, with the same
    timeout plus the transport's grace, and writes no row: it reports no cost to record. Admits outside a proof phase.
    Raises DrainRefused, or EvidenceError (a ValueError) when the proof records cannot be read, writing nothing."""
    env = os.environ if env is None else env
    P = CL.proof_ledger
    if P.run_identity(env) is None:
        return
    path = CL.ledger_path(env)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with P.ledger_lock(path):
        P.admit_locked(env.get("BROTHER_RUN_DIR", ""), P.amount(timeout) + CL.COMMUNICATE_GRACE_S, __file__, env=env)


def _bridge_via_dispatcher(name, argv, timeout, reg, admit=None):
    """One bridge call through the OpenRouter dispatcher (Codex check-in 2, finding 3). The dispatcher takes the slot,
    reserves the call on its ledger (in a proof phase: admits it, marks it dispatched and hands the bridge
    BROTHER_DISPATCH_RESERVATION, without which or_ask refuses) and records its terminal row. Raises what it raises."""
    from plugin.runtime.brother.core import openrouter_dispatch as D
    result, _model = D.dispatch(argv, reg[name]["id"], holder_id="model-call-%s-%s" % (os.getpid(), name),
                                timeout_seconds=timeout, max_tokens=BRIDGE_MAX.get(name, 64000),
                                state_root=admission.state_root(), model_alias=name, admit=admit,
                                breaker_record=False)   # call_one admits and records this call itself, once
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def _log_bridge(name, reg, kind, prompt, err, ok, secs):
    """EVERY BRIDGE CALL IS A ROW (A/B/C test 2026-09-23: 6.59 of arm B's 9.68 USD of OpenRouter spend was in no ledger,
    because only the fan out reserves on the dispatcher ledger and calls routed here did not). Tokens and the answering
    model come from the bridge's own [usage] line; the calling tool names who spent it. A failed or silent call is a row
    too, with null tokens: the money meter still counts it, and the gap must stay visible."""
    if (reg.get(name) or {}).get("transport") != "bridge": return
    m = USAGE_RE.search(err or "")
    tok = lambda s: int(s) if s and s.isdigit() else None
    try:
        with open(os.path.expanduser(os.environ.get("BROTHER_BRIDGE_CALLS_LEDGER") or "~/.claude/evidence/bridge-calls.jsonl"), "a", encoding="utf-8") as _f:
            _f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": name, "answered_by": m.group(3) if m else None,
                                 "prompt_tokens": tok(m.group(1)) if m else None, "completion_tokens": tok(m.group(2)) if m else None,
                                 "kind": kind, "caller": os.path.basename(sys.argv[0] or "?"), "prompt_chars": len(prompt or ""),
                                 "ok": ok, "seconds": round(secs, 1)}) + "\n")
    except OSError: pass   # sbe: allow-silent a record that cannot be written never stops the call it records


CONFIG_WAIT = "CONFIG_WAIT"   # the word every holder of a configuration fault carries: record errors, STATUS, reports


def config_admit(transport, model_id):
    """'' when the configuration breaker admits a call to this (transport, model), else why not. MANDATORY: it reads no
    mode and no threshold. An unreadable breaker refuses the call (fail closed), named, never admitted on a guess."""
    try:
        state, why = BR.admit([BR.config_key(transport, model_id)], "config-admit-%d" % os.getpid())
    except ValueError as exc:   # BreakerNoData is a ValueError; so is a key that cannot be formed
        return "the configuration breaker cannot be read (%s: %s)" % (type(exc).__name__, exc)
    return (why or "the configuration breaker is open") if state == "OPEN" else ""


def config_outcome(transport, model_id, returncode, stdout, stderr, call_id, prompt=None):
    """True when this finished call says the program does not know the model, and then the configuration breaker for
    (transport, model) is opened by it. Classified over the WHOLE raw text, before any truncation, by the breaker's own
    classifiers, the same ones every other status comes from. A breaker that cannot be written is said on stderr; the
    call is still CONFIG, since the classification does not depend on the write."""
    out, err = stdout or "", stderr or ""
    if transport == "bridge":
        status = BR.classify_bridge(int(returncode), out, err, prompt=prompt)
    else:
        status = BR.classify_cli(transport, int(returncode), out, err, _claude_doc(out) if transport == "claude" else None,
                                 prompt=prompt)
    if status != "CONFIG":
        return False
    try:
        BR.record(BR.config_key(transport, model_id), "CONFIG", call_id, (err + "\n" + out).strip())
    except ValueError as exc:
        sys.stderr.write("model_call: CONFIG for %s:%s could not open its breaker (%s); the call is still CONFIG\n"
                         % (transport, model_id, exc))
    return True


def _breaker_on(env=None):
    """True only when BROTHER_BREAKER reads exactly on. Anything else, an unknown value included, reads off."""
    try:
        return BR.mode(env) == "on"
    except ValueError:
        return False


def _breaker_keys(transport, name, shadow):
    """The pair of keys admit checks for this call: the model key and the account key."""
    account = BR.account_of(transport, [])
    return BR.keys_for(transport, account, name, shadow)


def _capacity_gate(transport, name, call_id, shadow, timeout):
    """Refuse this call before anything is sent when a key it would touch is open.

    Fail closed: a call that cannot be keyed, an unreadable state file and a held lock are all
    REFUSED_BY_GATE naming breaker NO-DATA. Nothing unknown is ever read as closed.
    """
    if transport not in ("bridge", "claude", "codex"):
        return None
    try:
        keys = _breaker_keys(transport, name, shadow)
    except (ValueError, OSError) as exc:
        return Attempt(name, False, "", "breaker NO-DATA: cannot key the call: %s" % exc, 0.0,
                       status="REFUSED_BY_GATE")
    try:
        state, reason = BR.admit(keys, call_id, ttl_s=float(timeout) + 60.0)
    except BR.BreakerNoData as exc:
        return Attempt(name, False, "", "breaker NO-DATA: %s" % exc, 0.0, status="REFUSED_BY_GATE")
    except (ValueError, OSError) as exc:
        return Attempt(name, False, "", "breaker NO-DATA: %s" % exc, 0.0, status="REFUSED_BY_GATE")
    if state == "OPEN":
        return Attempt(name, False, "", reason, 0.0, status="REFUSED_BY_GATE")
    return None


def _record_call(transport, name, status, detail, call_id, shadow):
    """The ONE recording point for a call that was actually sent.

    A status that means nothing was sent, or that the breaker does not count, is never recorded.
    A bookkeeping fault is swallowed on purpose: the call already happened and its answer is worth
    more than its record.
    """
    if not isinstance(status, str) or status not in BR.STATUSES:
        return
    if status != "ANSWERED" and status not in BR.COUNTING:
        return
    try:
        account = BR.account_of(transport, [])
    except (ValueError, OSError):
        return
    try:
        k = BR.key(transport, account, name, status if status in BR.COUNTING else "LIMIT", shadow)
    except (ValueError, OSError):
        return
    try:
        BR.record(k, status, call_id, detail)
    except BR.BreakerNoData:
        return
    except (ValueError, OSError):
        return


def _break_here(attempt, transport, name, call_id, shadow):
    """Record this call once when the breaker is on, then hand the attempt back unchanged."""
    if _breaker_on() and isinstance(transport, str) and transport in ("bridge", "claude", "codex"):
        _record_call(transport, name, attempt.status, attempt.detail, call_id, shadow)
    return attempt


def walk_action(status, kind, gate_detail=""):
    """The status walk table as ONE pure function: "return", "next" or "other_family".

    An ANSWER is never retried anywhere. A grade, decide or plan prompt that a provider refused is
    returned at once, because shopping the same question across providers is how a pipeline
    manufactures a false green. A build or prose refusal gets exactly one hop to another family. A
    refusal at the gate that names a capacity reason or a breaker NO-DATA hops, because nothing was
    sent; any other gate refusal stops, because there the caller is wrong, not the provider.
    """
    if not isinstance(status, str) or status not in BR.STATUSES:
        raise ValueError("status must be a member of breaker.STATUSES")
    if not isinstance(kind, str):
        raise ValueError("kind must be a string")
    if not isinstance(gate_detail, str):
        raise ValueError("gate_detail must be a string")
    if status == "ANSWERED":
        return "return"
    if status == "REFUSED_BY_GATE":
        if "capacity:" in gate_detail or "breaker NO-DATA" in gate_detail:
            return "next"
        return "return"
    if status == "PROVIDER_REFUSED":
        return "other_family" if kind in ("build", "prose") else "return"
    if status in ("EMPTY", "MALFORMED", "TIMEOUT", "LIMIT", "OVERLOAD", "AUTH", "TRANSPORT_DOWN", "CONFIG"):
        return "next"
    return "return"


def _gated_chain(chain, kind, sensitivity, reg):
    """A caller's own chain, in its own order, through the same privacy and kind gates the router applies."""
    if not isinstance(chain, list):
        raise ValueError("chain must be a list of model names or None")
    order = []
    for n in chain:
        if not isinstance(n, str) or not n:
            raise ValueError("chain members must be non empty strings")
        if n not in reg:
            continue
        try:
            R.assert_may_send(n, sensitivity, kind, reg)
        except R.Refused:
            continue  # sbe: allow-silent a refused member is left out of the chain on purpose; the router's own gate already named the refusal
        order.append(n)
    return order


def _next_other_family(order, reg, start, refused):
    """The next member from start on whose family is in none of the families that refused this prompt."""
    i = start
    while i < len(order):
        fam = BR.family(reg.get(order[i]) or {})
        if fam is not None and fam not in refused:
            return i
        i += 1
    return None


def call_one(name, prompt, kind, sensitivity, timeout=300, reg=None, runner=None, program=None, proof=False,
             expect=None, shadow=False):
    """One attempt against one model, with the wire check first. Never raises for a wire failure:
    it returns an Attempt whose ok is False, because the caller's job is to move on.
    program: the program to run instead of the resolved one (a candidate's reachability proof). proof=True is the
    reachability proof itself: it is the fresh evidence that closes the configuration breaker, so it is the one call the
    breaker does not hold; everything else about it (privacy gate, pool slot, ledger row, flags) is a production call's."""
    if expect is not None and expect != "json":
        raise ValueError("expect must be None or the string 'json'")
    if not isinstance(shadow, bool):
        raise ValueError("shadow must be a bool")
    reg = reg or R.registry()
    R.assert_may_send(name, sensitivity, kind, reg)          # THE SECOND GATE, at the wire
    t0 = time.time()
    # ONE ID FOR BOTH LEDGER ROWS (review 2026-09-26): a done row pairs with its start only through this, so an unrelated
    # completion can never cancel an uncosted call when a reader reconciles concurrent calls.
    call_id = "%d-%d-%s" % (os.getpid(), time.time_ns(), os.urandom(4).hex())
    slot = None
    if admission is None:
        return Attempt(name, False, "", "admission refused: %s" % ADMISSION_WHY, time.time() - t0,
                       status="REFUSED_BY_GATE")
    transport = (reg.get(name) or {}).get("transport")
    model_id = (reg.get(name) or {}).get("id") or name
    _on = _breaker_on()
    if _on and not proof:
        # THE CAPACITY GATE RUNS FIRST, before anything is spent and before the configuration breaker can
        # speak: a provider limit must never be reported as a CONFIG hold, which would blame the program for
        # the wire's answer.
        _gate = _capacity_gate(transport, name, call_id, shadow, timeout)
        if _gate is not None:
            return _gate
    # THE CONFIGURATION BREAKER IS MANDATORY (2026-09-30), whatever BROTHER_BREAKER says: the switch gates only the
    # capacity breaker above. FX-11.2 put this read behind the switch, so with the switch off (the default) a call after
    # a CONFIG trip was transmitted; test_config_dispatch went from OK to 3 failures (2026-10-02).
    _held = config_admit(transport, model_id) if transport in ("bridge", "claude", "codex") and not proof else ""
    if _held:
        return Attempt(name, False, "", "%s: %s" % (CONFIG_WAIT, _held), time.time() - t0, failure="CONFIG",
                       status="CONFIG")
    if transport == "bridge" and runner is None:
        # EVERY BRIDGE CALL IS RESERVED (Codex check-in 2, finding 3; A/B/C test 2026-09-23: 6.59 of 9.68 USD was in no
        # ledger): the dispatcher holds the slot, so none is taken here. An injected runner is a test transport and
        # keeps the path below.
        try:
            argv, _, _ = _argv(name, prompt, timeout, reg, program=program)
        except R.Refused as exc:   # the adapter refused the line: nothing sent, nothing reserved, the chain moves on
            return Attempt(name, False, "", "transport not runnable: %s" % exc, time.time() - t0,
                           status="TRANSPORT_DOWN")
        try:
            r = _bridge_via_dispatcher(name, argv, timeout, reg,
                                       admit=None if proof else (lambda: config_admit(transport, model_id)))   # R1 F6
        except Exception as exc:   # every dispatcher refusal or failure is a failed attempt, named, never an answer
            if type(exc).__name__ == "ConfigHeld":
                return Attempt(name, False, "", "%s: %s" % (CONFIG_WAIT, exc), time.time() - t0, failure="CONFIG",
                               status="CONFIG")
            _log_bridge(name, reg, kind, prompt, "", False, time.time() - t0)
            return Attempt(name, False, "", "dispatch refused or failed: %s: %s" % (type(exc).__name__, str(exc)[:160]),
                           time.time() - t0, status="MALFORMED")
        return _judge(name, reg, kind, prompt, r, time.time() - t0, call_id,
                      expect=expect, shadow=shadow)
    try:
        root = admission.state_root()
        # Every transport shares the bridge's pool. The machine policy, not
        # the number of workers in this caller, decides how many calls fit.
        limit = admission.effective_slots(root, sys.maxsize)
        slot = admission.acquire_slot(root, limit, "model-call-%s-%s" % (os.getpid(), name),
                                      timeout_seconds=timeout)
    except (admission.NoSlotAvailable, OSError, ValueError) as exc:
        return Attempt(name, False, "", "admission refused: %s" % exc, time.time() - t0,
                       status="REFUSED_BY_GATE")
    try:
        # THE FINAL ADMISSION, after the slot wait: a trip another call recorded while this one queued stops it here, before
        # anything is transmitted. A call already transmitted is allowed to finish; a queued one never starts after a trip.
        _held = "" if proof else config_admit(transport, model_id)   # mandatory, never behind the switch
        if _held:
            return Attempt(name, False, "", "%s: %s" % (CONFIG_WAIT, _held), time.time() - t0, failure="CONFIG",
                           status="CONFIG")
        try:
            argv, stdin, _ = _argv(name, prompt, timeout, reg, program=program)
        except R.Refused as exc:   # the adapter refused the line: the spawn failure path below, so nothing is skipped
            argv, stdin, runner = [], "", _refused_run(str(exc))
        if transport == "claude":
            try:
                effort = claude_effort(model_id=reg[name]["id"])
            except R.Refused as exc:   # the effort names no level: nothing to register, nothing sent, the chain moves on
                return Attempt(name, False, "", "transport not runnable: %s" % exc, time.time() - t0,
                               status="TRANSPORT_DOWN")
            # START IS DURABLE BEFORE THE CHILD RUNS (U7, objection 12): a call the ledger did not register never runs
            path = CL.ledger_path()
            try:
                started = CL.start(path, {"model": reg[name]["id"], "effort": effort,
                                          "prompt_chars": len(prompt or ""), "call": call_id}, timeout)
            except (CL.proof_ledger.DrainRefused, ValueError, OSError) as exc:
                return Attempt(name, False, "", "refused before the call: the Claude call ledger did not register it "
                               "(%s: %s)" % (type(exc).__name__, exc), time.time() - t0,
                               status="REFUSED_BY_GATE")
            r = _claude_run(path, started, argv, stdin, timeout, runner)
        else:
            try:
                _admit_without_a_row(timeout)
            except (CL.proof_ledger.DrainRefused, ValueError, OSError) as exc:
                return Attempt(name, False, "", "refused before the call: proof admission (%s: %s)"
                               % (type(exc).__name__, exc), time.time() - t0, status="REFUSED_BY_GATE")
            r = (runner or _run)(argv, stdin, timeout)
    except OSError as exc:
        return Attempt(name, False, "", "transport not runnable: %s" % exc, time.time() - t0,
                       status="TRANSPORT_DOWN")
    except subprocess.TimeoutExpired:
        return Attempt(name, False, "", "timed out after %ds" % timeout, time.time() - t0,
                       status="TIMEOUT")
    finally:
        if slot is not None:
            admission.release_slot(slot)
    return _break_here(_judge(name, reg, kind, prompt, r, time.time() - t0, call_id,
                              expect=expect, shadow=shadow),
                       transport, name, call_id, shadow)


def _judge(name, reg, kind, prompt, r, secs, call_id="judge", expect=None, shadow=False):
    """See _judge_body; the finished transport result rides on the Attempt as raw, for a proof that must read which
    model actually answered (the answer text alone cannot say).

    The shape the caller asked for is judged HERE, at the one point every transport passes and before anything is
    recorded, so a body that is not the JSON the caller asked for reads MALFORMED rather than an answer."""
    if expect is not None and expect != "json":
        raise ValueError("expect must be None or the string 'json'")
    if not isinstance(shadow, bool):
        raise ValueError("shadow must be a bool")
    a = _judge_body(name, reg, kind, prompt, r, secs, call_id)
    a.raw = r
    _label(a, name, reg, r, expect)
    return a


def _claude_doc_of(out):
    """The Claude result document, parsed here so no private helper's signature is assumed."""
    try:
        doc = json.loads(out)
    except (ValueError, TypeError):
        return None
    return doc if isinstance(doc, dict) else None


def _wire_status(name, reg, r):
    """The status breaker's own classifier reads for this row, or None when the row says nothing usable."""
    if not isinstance(r, dict):
        return None
    row = reg.get(name) or {}
    transport = row.get("transport")
    rc = r.get("returncode")
    out = r.get("stdout") or ""
    err = r.get("stderr") or ""
    if not isinstance(out, str) or not isinstance(err, str):
        return None
    try:
        if transport == "bridge":
            return BR.classify_bridge(rc, out, err)
        if transport == "claude":
            return BR.classify_cli("claude", rc, out, err, _claude_doc_of(out))
        if transport == "codex":
            return BR.classify_cli("codex", rc, out, err, None)
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return None
    return None


def _label(a, name, reg, r, expect):
    """Give the attempt its wire status, and judge the shape the caller asked for.

    A provider refusal is never an answer, whatever else the transport result carried. An unlabelled
    failure stays MALFORMED, and a classifier that calls a failed body ANSWERED can never turn it
    healthy."""
    status = _wire_status(name, reg, r)
    if status == "PROVIDER_REFUSED":
        a.ok = False
        a.status = "PROVIDER_REFUSED"
        if not a.detail:
            a.detail = "provider refused the prompt"
        return
    if expect == "json" and a.ok:
        try:
            json.loads(a.answer)
        except (ValueError, TypeError):
            a.ok = False
            a.status = "MALFORMED"
            a.detail = "answered but not the JSON the caller asked for"
            return
    if a.ok:
        a.status = "ANSWERED"
        return
    if a.failure == "CONFIG":
        a.status = "CONFIG"
        return
    if status is None or status == "ANSWERED":
        return
    a.status = status


def _judge_body(name, reg, kind, prompt, r, secs, call_id="judge"):
    """The Attempt for a finished transport result, judged by the transport's adapter (FX-31.5): a non zero exit, an
    empty body at exit zero and a Claude error record fail, a provider refusal is its own status and never an answer.
    A failure the program says is about the MODEL ITSELF (unknown, not found, unsupported) is CONFIG, classified first,
    over the raw text, before the truncation below. A result the adapter cannot judge (a returncode that is not an
    int, output that is not text, a row of another transport) is a FAILED attempt naming the reason, never a raise
    out of the chain and never an answer."""
    out = (r.get("stdout") or "").strip()
    err = (r.get("stderr") or "").strip()
    _t = (reg.get(name) or {}).get("transport")
    rc = r.get("returncode")
    if _t in ("bridge", "claude", "codex") and isinstance(rc, int) and not isinstance(rc, bool) and \
            config_outcome(_t, (reg.get(name) or {}).get("id") or name, rc, r.get("stdout") or "", r.get("stderr") or "", call_id,
                           prompt=prompt):
        _log_bridge(name, reg, kind, prompt, err, False, secs)
        _why = next((l for l in BR.config_channels(_t, out, err, _claude_doc(out), prompt).splitlines() if BR.is_config(l)), (err or out)[:200])
        return Attempt(name, False, "", "%s: the program does not know this model: %s" % (CONFIG_WAIT, _why.strip()[:200]),
                       secs, failure="CONFIG")
    try:
        verdict = _adapter(_t).judge(name, reg[name], r)
    except (A.Refused, ValueError, KeyError, TypeError) as exc:
        _log_bridge(name, reg, kind, prompt, err, False, secs)
        return Attempt(name, False, "", "the result could not be judged: %s: %s" % (type(exc).__name__, str(exc)[:160]), secs)
    _log_bridge(name, reg, kind, prompt, err, verdict.ok, secs)
    return Attempt(name, verdict.ok, verdict.answer, verdict.detail, secs,
                   status="PROVIDER_REFUSED" if verdict.status == A.STATUS_PROVIDER_REFUSED else None)


SCRATCH = os.path.expanduser(os.environ.get("BROTHER_MODEL_SCRATCH") or "~/.claude/brother-scratch/model-call")


def _run(argv, stdin, timeout):
    """The child gets its own process group and the GROUP is killed on timeout. Measured 2026-09-22: a subprocess
    timeout kills the child alone, and `claude -p` and `codex exec` both spawn children of their own, so a timed
    out call left them running; that pattern took this machine to load 157 in another tool the same night."""
    # NEVER IN THE CALLER'S TREE. The child runs in a throwaway directory, so nothing it does can land in the launch
    # worktree whatever its transport or flags (same incident as the --tools guard above; two guards, one per route).
    # ONE STABLE SCRATCH, NOT A FOLDER PER CALL (owner 2026-09-24, "optimize everything ... disk space"): mkdtemp per
    # call left 1,685 folders behind, and each distinct cwd made Claude Code file the call's transcript under a NEW
    # project folder (1,643 of them). That churn fed fseventsd to a 39 GB footprint and 32 GB of swap, which filled the
    # disk. One fixed directory outside every repository keeps both properties (never the caller's tree; children have
    # no tools) with zero per call directories; an unusable one falls back to a temp folder REMOVED after the call.
    scratch, temp = SCRATCH, False
    try:
        os.makedirs(scratch, exist_ok=True)
        if not os.access(scratch, os.W_OK | os.X_OK): raise OSError("not writable")
    except OSError:
        scratch, temp = tempfile.mkdtemp(prefix="model-call-"), True
    try:
        return _run_in(argv, stdin, timeout, scratch)
    finally:
        if temp:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)   # sbe: allow-silent a scratch that cannot be removed never fails the call it served


def _run_in(argv, stdin, timeout, scratch, env=None, pass_fds=()):
    """env: the child's whole environment, or None to inherit. pass_fds: descriptors this one child inherits (every
    other stays closed). The native worker passes both only on the loop token lane (BROTHER_LOOP_TOKEN=on): the scrubbed
    environment naming the login's descriptor, and that descriptor; never the parent's environment, never the token."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, cwd=scratch, env=env, pass_fds=tuple(pass_fds))
    except OSError as exc:
        # NO CHILD EXISTS (the CLI is missing or not executable): nothing was sent. Only this is a zero cost call.
        raise SpawnFailed(exc.errno, "%s: %s" % (type(exc).__name__, exc))
    try:
        out, err = proc.communicate(stdin, timeout=timeout + CL.COMMUNICATE_GRACE_S)
    except subprocess.TimeoutExpired:
        import signal
        try: os.killpg(proc.pid, signal.SIGKILL)
        except OSError: pass
        proc.communicate()
        raise
    return {"returncode": proc.returncode, "stdout": out, "stderr": err}


def call(prompt, kind="build", sensitivity=R.PUBLIC, pin=None, timeout=300,
         expect=None, reg=None, runner=None, record=True, chain=None, shadow=False):
    """Try the chain best first. Returns (Attempt or None, [every attempt made]).

    expect="json" makes an unparseable answer an INFRASTRUCTURE failure worth failing over, because
    a model that cannot produce the requested shape has not done the task. It does NOT make a
    valid JSON answer carrying a negative verdict a failure."""
    if not isinstance(shadow, bool):
        raise ValueError("shadow must be a bool")
    reg = reg or R.registry()
    if chain is None:
        order = R.chain(kind, sensitivity, pin, registry_arg=reg)
    else:
        order = _gated_chain(chain, kind, sensitivity, reg)
    if not order:
        return None, []
    tried = []
    # UNDER THE PROOF REGIME (attack R1 gap c): a run started from a proven intake calls only models the intake proved.
    # A fallback the chain would reach beyond them is refused NO-DATA, never sent to find out.
    try:
        import model_reachability as _MR
        proven = _MR.proven_models()
    except Exception as exc:   # sbe: allow-silent an unloadable proof reader under a named record proves nothing
        proven = set() if os.environ.get("BROTHER_PROGRAM_RECORD") else None
    index = 0
    hopped = False
    refused_families = []
    while index < len(order):
        name = order[index]
        if proven is not None and "%s|%s" % (reg[name]["transport"], reg[name]["id"]) not in proven:
            tried.append(Attempt(name, False, "", "NO-DATA: %s was not proven at intake; under the proof regime it is "
                                 "never sent" % name, 0.0, failure="UNPROVEN", status="MALFORMED"))
            index += 1
            continue
        a = call_one(name, prompt, kind, sensitivity, timeout, reg, runner, expect=expect, shadow=shadow)
        tried.append(a)
        if record and a.failure != "CONFIG":   # a configuration fault is never the model's reliability figure
            R.record_outcome(reg[name]["id"], kind, a.ok, a.seconds)
        action = walk_action(a.status, kind, a.detail)
        if action == "return":
            return a, tried
        if action == "next":
            index += 1
            continue
        fam = BR.family(reg.get(name) or {})
        if hopped or fam is None:
            a.detail = "provider refused the prompt twice in family %s" % fam
            return a, tried
        refused_families.append(fam)
        nxt = _next_other_family(order, reg, index + 1, refused_families)
        if nxt is None:
            a.detail = "provider refused the prompt twice in family %s" % fam
            return a, tried
        hopped = True
        index = nxt
    return None, tried


def selftest():
    """A selftest that RAISES has not reported a verdict, it has abandoned the question.

    MEASURED 2026-09-21 on this exact file: injecting a raise into one case expression killed
    this function with a traceback at EXIT 1, which is the same exit code an honest failure
    returns. A pipeline reading the code and a human reading the text then describe the same run
    differently, and the human gets a stack trace where a verdict belongs.

    The cases below are built EAGERLY, so one bad expression takes the whole run with it. Turning
    every case into a lambda would fix that too, but it is a large diff whose own risk is a
    transcription error in a case nobody would then notice was wrong. Wrapping the body is four
    lines, carries no such risk, and covers the NEXT case someone adds here without its author
    doing anything: whatever escapes is reported as a FAILED case naming the exception, and the
    exit code still says 1, so the verdict and the code agree in every direction.
    """
    # THE SELFTEST NEVER WRITES THE LIVE CALL LEDGER (2026-09-23 14:3x: its fake claude-x calls landed in
    # claude-calls.jsonl, the file the A/B/C cost meter reconciles against). Its rows go to a scratch file it then reads.
    # ONE ROOT OF ITS OWN, MADE HERE AND REMOVED HERE (review 2026-10-06). The two ledgers were mkstemp files nothing
    # removed, so every run left three files in the temp folder (both ledgers and the Claude ledger's lock), and the
    # child's working folder was SCRATCH: the caller's BROTHER_MODEL_SCRATCH or a folder under HOME, so with HOME
    # unwritable the cwd case read FAILED on a tree that was fine. The ledgers, the breaker state and the scratch now
    # live under one folder, and the verdict depends on neither HOME nor a caller's scratch.
    import shutil, tempfile
    _root = tempfile.mkdtemp(prefix="selftest-model-call-")
    _prev = os.environ.get("BROTHER_CLAUDE_CALLS_LEDGER")
    os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = os.path.join(_root, "claude-calls.jsonl")
    _prev_b = os.environ.get("BROTHER_BRIDGE_CALLS_LEDGER")
    os.environ["BROTHER_BRIDGE_CALLS_LEDGER"] = os.path.join(_root, "bridge-calls.jsonl")
    _prev_s = os.environ.get("BROTHER_OR_STATE_ROOT")   # the configuration breaker's state: never the live one
    os.environ["BROTHER_OR_STATE_ROOT"] = os.path.join(_root, "state")
    os.makedirs(os.environ["BROTHER_OR_STATE_ROOT"])
    _prev_m = os.environ.get("BROTHER_BREAKER")        # the capacity switch too: a selftest must not read the live one
    os.environ["BROTHER_BREAKER"] = "off"
    # THE SELFTEST NEEDS NO INSTALLED CLI (FX-31.5): the adapters refuse a program that is not an executable file, and
    # every runner here is fake, so argv[0] names this interpreter, the way test_config_dispatch names /bin/echo.
    global CLAUDE, SCRATCH
    _prev_c, CLAUDE = CLAUDE, CLAUDE or sys.executable
    _prev_x, SCRATCH = SCRATCH, os.path.join(_root, "scratch")
    try:
        return _selftest_body()
    except Exception as exc:                       # noqa: BLE001 - a verdict beats a traceback
        print("selftest: FAILED before it could finish, %s: %s"
              % (type(exc).__name__, str(exc)[:120]))
        return 1
    finally:
        if _prev is None: os.environ.pop("BROTHER_CLAUDE_CALLS_LEDGER", None)
        else: os.environ["BROTHER_CLAUDE_CALLS_LEDGER"] = _prev
        if _prev_b is None: os.environ.pop("BROTHER_BRIDGE_CALLS_LEDGER", None)
        else: os.environ["BROTHER_BRIDGE_CALLS_LEDGER"] = _prev_b
        if _prev_s is None: os.environ.pop("BROTHER_OR_STATE_ROOT", None)
        else: os.environ["BROTHER_OR_STATE_ROOT"] = _prev_s
        if _prev_m is None: os.environ.pop("BROTHER_BREAKER", None)
        else: os.environ["BROTHER_BREAKER"] = _prev_m
        CLAUDE, SCRATCH = _prev_c, _prev_x
        shutil.rmtree(_root, ignore_errors=True)   # sbe: allow-silent selftest scratch


def _selftest_body():
    fake = {"first": {"id": "x/first", "transport": "bridge", "privacy": R.PUBLIC,
                      "quality": {"build": 9}, "kinds": {"build"}, "cost": 1.0},
            "second": {"id": "x/second", "transport": "bridge", "privacy": R.PUBLIC,
                       "quality": {"build": 5}, "kinds": {"build"}, "cost": 1.0},
            "local": {"id": "x/local", "transport": "claude", "privacy": R.PRIVATE,
                      "quality": {"build": 7}, "kinds": {"build"}, "cost": 9.0}}

    # Direct three argument runners. An earlier version wrapped a two argument script in an
    # adapter, and three cases failed for a reason that had nothing to do with the code under
    # test: the harness, not the subject. A test helper with its own bug is the worst kind,
    # because it reports the subject as broken.
    def only(which, rc=1, out="", err="boom"):
        """Every model FAILS except the one whose name appears in its argv."""
        def run(argv, stdin, timeout):
            hit = any(which in str(x) for x in argv)
            return ({"returncode": 0, "stdout": "fine", "stderr": ""} if hit
                    else {"returncode": rc, "stdout": out, "stderr": err})
        return run

    def always(rc=0, out="", err=""):
        def run(argv, stdin, timeout):
            return {"returncode": rc, "stdout": out, "stderr": err}
        return run

    def raises(exc):
        def run(argv, stdin, timeout):
            raise exc
        return run

    # ORTHOGONAL FIXTURES. THIS IS THE LESSON OF THIS FILE, and it was learned the expensive way.
    #
    # Every failure fixture above returns a non zero exit AND an empty body at the same time. So
    # `if not out:` fires first and MASKS `if r.get("returncode") != 0:`, and deleting the exit code
    # guard entirely left this selftest fully green at ten cases while an authentication error was
    # handed back to the loop as the build answer, with no failover. Measured 2026-09-21 by an
    # adversarial mutation sweep: 39 survivors of 90 mutations across this unit, and this was the
    # worst of them.
    #
    # A fixture that trips TWO guards cannot tell you either one works. Each of these isolates
    # exactly one condition, so removing the guard it targets is the only way to make it pass.
    def only_bad_exit(argv, stdin, timeout):
        """Non zero exit WITH a full body: only the exit code guard can refuse this."""
        return {"returncode": 1, "stdout": "a complete and plausible looking answer", "stderr": ""}

    def only_empty_body(argv, stdin, timeout):
        """Exit ZERO with an empty body: only the empty answer guard can refuse this."""
        return {"returncode": 0, "stdout": "   \n  ", "stderr": ""}

    # A two model registry for the failover cases. The three model fixture also contains a first
    # party model, which is eligible for PUBLIC content too (a model may receive its own class and
    # everything below), so the chain there is three long. An earlier version of these cases
    # asserted a length of two and failed for that reason alone, with the code behaving correctly.
    pair = {k: v for k, v in fake.items() if k in ("first", "second")}

    # first model fails at the wire, second answers
    win, tried = call("p", "build", R.PUBLIC, reg=pair, record=False,
                      runner=only("second"))
    c1 = win is not None and win.model == "second" and len(tried) == 2 and tried[0].ok is False

    # EXIT ZERO WITH AN EMPTY BODY must fail over, not be returned
    win2, tried2 = call("p", "build", R.PUBLIC, reg=pair, record=False,
                        runner=only("second", rc=0, out="", err=""))
    c2 = win2 is not None and win2.model == "second" and "EMPTY" in tried2[0].detail

    # a valid NEGATIVE answer is an OUTCOME and must be returned by the FIRST model
    win3, tried3 = call("p", "build", R.PUBLIC, reg=pair, record=False, expect="json",
                        runner=always(out=json.dumps({"verdict": "FAIL", "why": "it is wrong"})))
    c3 = win3 is not None and win3.model == "first" and len(tried3) == 1

    # expect json, and the answer is prose: that IS a failover
    win4, tried4 = call("p", "build", R.PUBLIC, reg=pair, record=False, expect="json",
                        runner=always(out="I think probably yes"))
    c4 = win4 is None and len(tried4) == 2 and "not the JSON" in tried4[0].detail

    # every model failing returns None and the WHOLE history, never a fabricated answer
    win5, tried5 = call("p", "build", R.PUBLIC, reg=pair, record=False, runner=always(rc=7, err="down"))
    c5 = win5 is None and len(tried5) == 2

    # the wire gate still refuses, even though the router already checked
    try:
        call_one("first", "p", "build", R.PRIVATE, reg=fake, runner=always(rc=7, err="down"))
        c6 = False
    except R.Refused:
        c6 = True

    # private content selects only the local model, and the chain never contains a bridge one
    order = R.chain("build", R.PRIVATE, registry_arg=fake)
    c7 = order == ["local"]
    # and the mirror of it: a PUBLIC task may use the first party model too, which is why the
    # failover cases above deliberately use a two model registry.
    c7 = c7 and "local" in R.chain("build", R.PUBLIC, registry_arg=fake)

    # a codex argv carries BOTH the trusted directory and a closed stdin
    argv, stdin, _ = _argv("c", "hello", 60, {"c": {"id": "gpt-x", "transport": "codex",
                                                    "privacy": R.PUBLIC, "quality": {"build": 1},
                                                    "kinds": {"build"}, "cost": 1.0}}, program=sys.executable)
    c8 = "-C" in argv and _codex_root() in argv and stdin == ""

    # the bridge runs at xhigh with the model's full ceiling, never a low effort or a small max (owner law)
    argv_b, _, _ = _argv("d", "hello", 60, {"d": {"id": "deepseek/x", "transport": "bridge", "privacy": R.PUBLIC, "quality": {"build": 1}, "kinds": {"build"}, "cost": 1.0}})
    c_bridge = argv_b[argv_b.index("--effort") + 1] == BRIDGE_EFFORT and BRIDGE_EFFORT != "low" and int(argv_b[argv_b.index("--max") + 1]) >= 64000
    try: bridge_effort({"BROTHER_BRIDGE_EFFORT": "low"}); c_floor = False
    except R.Refused as exc: c_floor = "below xhigh" in str(exc)
    c_floor = c_floor and bridge_effort({"BROTHER_BRIDGE_EFFORT": "max"}) == "max" and bridge_effort({}) == "xhigh"
    # a claude argv puts the prompt on STDIN, not on argv
    argv2, stdin2, _ = _argv("k", "hello", 60, {"k": {"id": "claude-x", "transport": "claude",
                                                      "privacy": R.PRIVATE, "quality": {"build": 1},
                                                      "kinds": {"build"}, "cost": 1.0}})
    c9 = stdin2 == "hello" and "hello" not in argv2
    # a claude child gets NO tools and runs OUTSIDE the caller's tree
    c9 = c9 and argv2[-4:-2] == ["--tools", ""] and argv2[-2:] == ["--effort", "medium"]
    c9 = c9 and argv2[4:4 + len(CLAUDE_TRIM)] == CLAUDE_TRIM and argv2[4 + len(CLAUDE_TRIM):4 + len(CLAUDE_TRIM) + len(CLAUDE_IO)] == CLAUDE_IO   # no inherited prefix, no transcript
    # the JSON result is the answer and the ledger gets its cost; an error or an empty result is a failure
    _cl = os.environ["BROTHER_CLAUDE_CALLS_LEDGER"]; open(_cl, "w").close()
    _creg = {"c": {"id": "claude-x", "transport": "claude", "privacy": R.PRIVATE, "quality": {"build": 1}, "kinds": {"build"}, "cost": 1.0}}
    _ok = call_one("c", "hi", "build", R.PRIVATE, reg=_creg, runner=lambda a, s, t: {"returncode": 0, "stdout": json.dumps({"result": "12", "total_cost_usd": 0.0086, "usage": {"input_tokens": 2, "output_tokens": 3}}), "stderr": ""})
    _er = call_one("c", "hi", "build", R.PRIVATE, reg=_creg, runner=lambda a, s, t: {"returncode": 0, "stdout": json.dumps({"is_error": True, "result": "overloaded"}), "stderr": ""})
    _bad = call_one("c", "hi", "build", R.PRIVATE, reg=_creg, runner=lambda a, s, t: {"returncode": 0, "stdout": "12", "stderr": ""})
    _done = [json.loads(l) for l in open(_cl) if '"done"' in l]
    c9 = c9 and _ok.ok and _ok.answer == "12" and not _er.ok and not _bad.ok and any(r.get("cost_usd") == 0.0086 for r in _done)
    c9 = c9 and claude_effort({}, "claude-opus-5") == "medium" and claude_effort({}, "claude-sonnet-5") == "medium" and claude_effort({}, "claude-fable-5-1") == "high" and claude_effort({"BROTHER_CLAUDE_EFFORT": "low"}, "claude-sonnet-5") == "medium" and claude_effort({"BROTHER_CLAUDE_EFFORT": "xhigh"}, "claude-opus-5") == "xhigh"
    # ONE EFFORT RULE: a value that names no level is REFUSED, never read as the floor (the adapter's rule, shared with
    # the native session); the refusal names the variable so the operator knows what to fix
    try: claude_effort({"BROTHER_CLAUDE_EFFORT": "bogus"}, "claude-x"); c9 = False
    except R.Refused as exc: c9 = c9 and "BROTHER_CLAUDE_EFFORT" in str(exc)
    _cwd_probe = _run([sys.executable, "-c", "import os; print(os.getcwd())"], "", 30)
    c9 = c9 and _cwd_probe["returncode"] == 0 and _cwd_probe["stdout"].strip() != os.getcwd() and os.path.realpath(_cwd_probe["stdout"].strip()) == os.path.realpath(SCRATCH)
    # NO FOLDER PER CALL: two more calls leave the temp folder's model-call-* count unchanged
    _tmp = tempfile.gettempdir(); _before = len([n for n in os.listdir(_tmp) if n.startswith("model-call-")])
    _run([sys.executable, "-c", "print(1)"], "", 30); _run([sys.executable, "-c", "print(2)"], "", 30)
    c9 = c9 and len([n for n in os.listdir(_tmp) if n.startswith("model-call-")]) == _before

    # an unknown transport refuses rather than guessing
    try:
        _argv("z", "p", 60, {"z": {"id": "x", "transport": "smoke-signal", "privacy": R.PUBLIC,
                                   "quality": {"build": 1}, "kinds": {"build"}, "cost": 1.0}})
        c10 = False
    except R.Refused:
        c10 = True

    # each of these dies if, and only if, the ONE guard it targets is removed
    w_exit, t_exit = call("p", "build", R.PUBLIC, reg=pair, record=False, runner=only_bad_exit)
    c_exit = w_exit is None and len(t_exit) == 2 and all("exit 1" in a.detail for a in t_exit)
    w_body, t_body = call("p", "build", R.PUBLIC, reg=pair, record=False, runner=only_empty_body)
    c_body = w_body is None and len(t_body) == 2 and all("EMPTY" in a.detail for a in t_body)
    w_to, t_to = call("p", "build", R.PUBLIC, reg=pair, record=False,
                      runner=raises(subprocess.TimeoutExpired(cmd="x", timeout=1)))
    c_to = w_to is None and all("timed out" in a.detail for a in t_to)
    w_os, t_os = call("p", "build", R.PUBLIC, reg=pair, record=False, runner=raises(OSError("no such binary")))
    c_os = w_os is None and all("not runnable" in a.detail for a in t_os)

    cases = [("a NON ZERO EXIT with a full body is refused, nothing else can catch it", c_exit),
             ("an EMPTY body at exit zero is refused, nothing else can catch it", c_body),
             ("a TIMEOUT is a failure, never an answer", c_to),
             ("a transport that will not run is a failure, never an answer", c_os),
             ("a wire failure falls over to the next model", c1),
             ("exit 0 with an EMPTY body falls over, never returns", c2),
             ("a valid NEGATIVE answer is returned, never retried elsewhere", c3),
             ("prose where JSON was required falls over", c4),
             ("every model failing returns None and the whole history", c5),
             ("the wire gate refuses private content on a bridge model", c6),
             ("private content selects only first party models", c7),
             ("a codex call carries -C and a closed stdin", c8),
             ("a bridge argv runs at xhigh with the model's full ceiling, never low and 8000", c_bridge),
             ("an effort below xhigh for the bridge is REFUSED, max is allowed, unset is xhigh", c_floor),
             ("a claude call puts the prompt on stdin, not argv", c9),
             ("an unknown transport refuses rather than guessing", c10)]
    # EVERY BRIDGE CALL IS A ROW, proven at the entry point with a fake runner (the ledger is the selftest's scratch file)
    _bl = os.environ["BROTHER_BRIDGE_CALLS_LEDGER"]; open(_bl, "w").close()
    _breg = {"b": {"id": "x/b", "transport": "bridge", "privacy": R.PUBLIC, "quality": {"build": 1}, "kinds": {"build"}, "cost": 1.0},
             "c": {"id": "claude-x", "transport": "claude", "privacy": R.PRIVATE, "quality": {"build": 1}, "kinds": {"build"}, "cost": 1.0}}
    call_one("b", "hi", "build", R.PUBLIC, reg=_breg, runner=lambda a, s, t: {"returncode": 0, "stdout": "12", "stderr": "[usage] prompt=5 completion=3 model=x/b-v2"})
    call_one("b", "hi", "build", R.PUBLIC, reg=_breg, runner=lambda a, s, t: {"returncode": 44, "stdout": "", "stderr": "no key"})
    call_one("c", "hi", "build", R.PRIVATE, reg=_breg, runner=lambda a, s, t: {"returncode": 0, "stdout": "12", "stderr": ""})
    _rows = [json.loads(l) for l in open(_bl)]
    cases += [("a bridge call that answers is a row with its tokens and the model that answered", len(_rows) >= 1 and _rows[0]["prompt_tokens"] == 5 and _rows[0]["completion_tokens"] == 3 and _rows[0]["answered_by"] == "x/b-v2" and _rows[0]["ok"] is True),
              ("a bridge call that fails is a row too, tokens unknown, never dropped", len(_rows) >= 2 and _rows[1]["ok"] is False and _rows[1]["prompt_tokens"] is None),
              ("a claude call is not a bridge row", len(_rows) == 2)]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    def arg(f, d=None):
        return sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d
    prompt = arg("--prompt")
    if not prompt:
        print(__doc__)
        return 2
    try:
        win, tried = call(prompt, arg("--kind", "build"), arg("--sensitivity", R.PUBLIC), arg("--pin"))
    except R.Refused as exc:
        print("REFUSED: %s" % exc)
        return 1
    for a in tried:
        print("  %-9s %-4s %6.1fs  %s" % (a.model, "ok" if a.ok else "FAIL", a.seconds, a.detail[:70]))
    if win is None:
        print("NO-DATA: every eligible model failed. That is not an answer and must not read as one.")
        return 1
    print("\n%s" % win.answer[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
