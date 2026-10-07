#!/usr/bin/env python3
"""The named roles of a run, and the check that a user's choices for them can actually be honoured.

WHY (owner, 2026-09-22): "We have to have set roles inside the loop with names otherwise the user wont know how to
set it". Until then the roles lived in three environment variables, a tuple inside probe_wave.py and the head of
whoever started the run. A user could pin a worker the fan out cannot carry, or name a checker the privacy rule
refuses, and learn it from a runner log an hour later. docs/plan/loop-roles.json names every role; this file answers,
BEFORE a run, which choices hold.

usage (repo root): loop_roles.py show                                   the roles, what each does, how it is set
                   loop_roles.py check worker=deepseek checker=opus finisher=fable documenter=luna ...
                   loop_roles.py --selftest
check prints one line per role and exits 0 only when every role is settled: chosen or defaulted, known to the
registry, able to do the role's kind, and for a role INSIDE the run allowed to receive the role's content (the router
enforces that at the wire anyway; here it is said before money is spent). A role that must be chosen and was not is
a refusal. An unknown role name is a refusal. An unreadable roles file or registry is NO-DATA, exit 3, never ready.
For a role OUTSIDE the run the content rule is a NOTE, not a refusal: that model is called by the owner's own
procedure and not through this router, and the privacy ruling there is his.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # THIS file's own directory: the copies installed beside it
ORDER = {"public": 0, "internal": 1, "private": 2}
BRIDGE_ONLY = ()   # 2026-09-30: the adversary seats read BROTHER_ADVERSARY_MODEL (probe_wave.adversary_models); every inside role may be on any transport the fan out speaks


def roles_path():
    env = os.environ.get("BROTHER_LOOP_ROLES")
    if env:
        return env
    for p in (os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "plan", "loop-roles.json"),
              os.path.expanduser("~/.claude/loop-roles.json"),
              # A TRACKED FIXTURE, LAST, exactly as model_router does for its registry: an export copy of the tree ships
              # no docs/plan, and a tool that cannot find its own data there reads as broken to every detector.
              os.path.join(os.path.dirname(HERE), "fixtures", "loop-roles-fixture.json")):
        if os.path.isfile(p):
            return p
    return ""


def load_roles(path=None):
    """The roles mapping, or None when it cannot be read or is not the expected shape."""
    try:
        with open(path or roles_path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    roles = d.get("roles") if isinstance(d, dict) else None
    need = ("does", "when", "kind", "content", "must_be_chosen")
    if not isinstance(roles, dict) or not roles or any(not isinstance(r, dict) or any(k not in r for k in need) for r in roles.values()):
        return None
    return roles


def judge(role, spec, choice, reg):
    """(verdict, line) for one role. verdict is OK, NOTE or REFUSED. One reason per branch, first one wins."""
    name = choice or spec.get("default")
    if not name:
        # AN OPTIONAL ROLE WITH NO DEFAULT IS NOT RUN (fix 7, 2026-09-22): the checker lost its default after 0 LAND in
        # 37 rulings, and "not run" is a NOTE, not a refusal; only a role that must be chosen refuses here.
        if spec.get("must_be_chosen"): return "REFUSED", "%s: must be chosen by the user and nothing was given" % role
        return "NOTE", "%s: not run (no default and nothing chosen); name a model to run it" % role
    how = "chosen" if choice else "default"
    m = reg.get(name)
    if m is None:
        return "REFUSED", "%s: %r is not in the model registry" % (role, name)
    if spec["kind"] not in m["kinds"]:
        return "REFUSED", "%s: %s cannot do %s work" % (role, name, spec["kind"])
    # THE ALLOWLIST AND THE ROLES ARE CHECKED AGAINST EACH OTHER (attack 3, 2026-09-30): under BROTHER_TRANSPORTS=claude
    # a role that resolves to a bridge model (the worker's and adversary's deepseek defaults) is REFUSED at intake, so a
    # READY record never describes a run whose every build would be refused at the wire.
    try:
        import model_router
        allowed = model_router.transports_allowed()
    except Exception as exc:   # sbe: allow-silent the refusal names why; an unreadable allowlist settles no role
        return "REFUSED", "%s: the transport allowlist could not be read (%s: %s)" % (role, type(exc).__name__, str(exc)[:100])
    if allowed is not None and m["transport"] not in allowed:
        return "REFUSED", "%s: %s (%s) is on the %s transport, outside BROTHER_TRANSPORTS=%s; name a model on an allowed transport" % (role, name, how, m["transport"], model_router.transports_text(allowed))
    inside = spec["when"] == "inside"
    if inside and role in BRIDGE_ONLY and m["transport"] != "bridge":
        return "REFUSED", "%s: %s is on the %s transport and the parallel fan out carries bridge models only" % (role, name, m["transport"])
    short = ORDER.get(m["privacy"], -1) < ORDER.get(spec["content"], 99)
    if short and inside:
        return "REFUSED", "%s: %s may receive %s content at most and this role's brief is %s" % (role, name, m["privacy"], spec["content"])
    if short:
        return "NOTE", "%s: %s (%s). It may receive %s content at most and this role reads %s content: outside the run that ruling is the owner's, not the router's" % (role, name, how, m["privacy"], spec["content"])
    return "OK", "%s: %s (%s)" % (role, name, how)


def stage_ok(name, row):
    """FX-11.4 (REQ-FX11-14): may this registry row seat? (ok, why).

    A row with no stage field reads seated: docs/plan/model-registry.json carries no stage field at this commit
    (FX-31, MDL-03, adds it with the rule "absent reads seated"). Only `seated` passes. A retired row is one whose
    stage with zero yield was removed; a shadow row advises and never blocks; any other value is an unknown and
    BLOCKS, never the safe case."""
    if not isinstance(name, str) or not isinstance(row, dict):
        raise ValueError("stage_ok wants a model name and its registry row")
    stage = row.get("stage", "seated")
    if not isinstance(stage, str):
        raise ValueError("%s has an unknown stage %r" % (name, stage))
    if stage == "seated":
        return True, ""
    if stage in ("retired", "shadow"):
        return False, "%s is %s and never seats" % (name, stage)
    return False, "%s has an unknown stage %r" % (name, stage)


FABLE_ORDER = "the owner's order of 2026-09-29, Do not use Fable for the loop"


def _chain_members(spec, role):
    """The role's written chain, or [] when it has none; a chain that is not a list is a refusal, never ignored."""
    members = spec.get("chain")
    if members is None:
        return []
    if not isinstance(members, list):
        raise ValueError("%s has a chain that is not a list of names" % role)
    return members


def _member_ok(role, spec, name, reg):
    """Raise ValueError when this role may not seat this model. One place, so every caller refuses alike."""
    row = reg.get(name)
    if not isinstance(row, dict):
        raise ValueError("role_chain: %s names %r, which is not in the model registry" % (role, name))
    kind = spec.get("kind")
    if kind and kind not in row.get("kinds", ()):
        raise ValueError("role_chain: %s names %s, which cannot do %s work" % (role, name, kind))
    ok, reason = stage_ok(name, row)
    if not ok:
        raise ValueError("role_chain: %s" % reason)
    return True


def _breaker_on(env=None):
    """FX-11.4: is the breaker switch on? breaker.py (FX-11.1) is imported INSIDE this function, never at module
    level, so this file, the intake and scripts/test_attacker_role_limit.py still import where no breaker file is
    present. A missing breaker module cannot decide the switch, so the environment value itself decides."""
    env = os.environ if env is None else env
    value = str((env or {}).get("BROTHER_BREAKER", "") or "").strip().lower()
    try:
        import breaker
        return breaker.mode(env) == "on"
    except ImportError:
        return value == "on"


def role_chain(role, choice=None, roles=None, reg=None, env=None):
    """FX-11.4 (REQ-FX11-13): the ordered model names for a role: the first member, then its own named chain.

    first = choice, else the role's setting read from env, else its default. The chain members follow, each once; a
    member equal to the first is skipped. With BROTHER_BREAKER off this returns [first] ONLY, exactly today's
    behaviour: one model per role, no fallback.

    Raises ValueError naming the member for an unknown model, a wrong kind, a retired or shadow stage, an unknown
    stage value, and for Fable written into a chain. The FIRST member is the owner's own choice and is never refused
    for being Fable."""
    if not isinstance(role, str):
        raise ValueError("role_chain wants a role name")
    if choice is not None and not isinstance(choice, str):
        raise ValueError("role_chain wants a model name or None")
    if env is not None and not isinstance(env, dict):
        raise ValueError("role_chain wants an environment mapping or None")
    if roles is None:
        roles = load_roles()
    if not isinstance(roles, dict):
        raise ValueError("role_chain: the roles file cannot be read")
    spec = roles.get(role)
    if not isinstance(spec, dict):
        raise ValueError("role_chain: %r is not a role of the loop" % role)
    if reg is None:
        import model_router
        reg = model_router.registry()
    if not isinstance(reg, dict):
        raise ValueError("role_chain: the model registry cannot be read")
    env = os.environ if env is None else env

    setting = spec.get("setting")
    picked = env.get(setting) if setting else None
    first = choice or picked or spec.get("default")
    if first is None:
        if spec.get("must_be_chosen"):
            raise ValueError("role_chain: %s must be chosen and nothing was given" % role)
        return []
    _member_ok(role, spec, first, reg)

    if not _breaker_on(env):
        return [first]

    out = [first]
    for member in _chain_members(spec, role):
        if not isinstance(member, str):
            raise ValueError("role_chain: %s has a chain member %r that is not a name" % (role, member))
        if member == first:
            continue
        if "fable" in member.lower():
            raise ValueError("role_chain: %s names %s in its chain and %s" % (role, member, FABLE_ORDER))
        _member_ok(role, spec, member, reg)
        if member not in out:
            out.append(member)
    return out


def call_chain(role, choice=None, env=None):
    """FX-11.5: (role_chain(...), "") or ([], "roles: <why>") when the roles file refuses the chain.

    The one shared answer for the four call sites (finisher, checker, repair advisor, planner): a refused or empty chain
    makes no call and says why, never a crash on a roles file edit. An empty list is never a pass. Raises ValueError only
    for a caller defect: role not a non-empty string, choice not None or a non-empty string, env not None or a dict.
    The process environment is handed on as a plain dict copy: the breaker mode reader refuses os.environ itself."""
    if not isinstance(role, str) or not role.strip():
        raise ValueError("call_chain wants a role name")
    if choice is not None and (not isinstance(choice, str) or not choice.strip()):
        raise ValueError("call_chain wants a model name or None")
    if env is not None and not isinstance(env, dict):
        raise ValueError("call_chain wants an environment mapping or None")
    try:
        chain = role_chain(role, choice=choice, env=dict(os.environ) if env is None else env)
    except (ValueError, OSError) as exc:
        return [], "roles: %s" % str(exc)[:300]
    if not chain:
        return [], "roles: %s names no model and nothing was chosen" % role
    return chain, ""


# FX-11.6 (REQ-FX11-20, REQ-FX11-21): the planner, the repair advisor and the adversary take their models from this file.
# unit_runner.py and probe_wave.py run their whole body at import and cannot be imported by a test, so the two seat
# decisions live here and each script makes one call.
ADVERSARY_SETTING = "BROTHER_ADVERSARY_MODEL"
SIDE_SEAT_ROLES = (("plan", "planner"), ("repair", "repair_advisor"))
ROLE_SHAPE_ERRORS = (ValueError, OSError, KeyError, TypeError, AttributeError)   # a hand edited roles file or registry row of the wrong shape refuses, never crashes a runner


def _env_copy(env, who):
    """A plain dict copy of env (the process environment when None), or ValueError: every key and value a string."""
    if env is None:
        return dict(os.environ)
    if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
        raise ValueError("%s wants an environment mapping of strings or None" % who)
    return dict(env)


def side_seat_models(env=None, roles=None, reg=None):
    """FX-11.6 (REQ-FX11-20): ({"plan": name or None, "repair": name or None}, why or None).

    Under BROTHER_BREAKER off both seats are None and why is None: unit_runner keeps today's advice line. Under on each
    seat is the first member of the planner and repair_advisor role chains (the owner's setting, else the default).
    When the roles file or registry cannot settle either role, both seats are None and why names it: no half seated
    pair. Raises ValueError only for a caller defect (env, roles or reg of the wrong type)."""
    env = _env_copy(env, "side_seat_models")
    if roles is not None and not isinstance(roles, dict):
        raise ValueError("side_seat_models wants a roles mapping or None")
    if reg is not None and not isinstance(reg, dict):
        raise ValueError("side_seat_models wants a registry mapping or None")
    if not _breaker_on(env):
        return {"plan": None, "repair": None}, None
    if roles is None:
        roles = load_roles()
    if roles is None:
        return {"plan": None, "repair": None}, "the roles file cannot be read or is not the expected shape"
    seats = {"plan": None, "repair": None}
    for side, role in SIDE_SEAT_ROLES:
        try:
            chain = role_chain(role, roles=roles, reg=reg, env=env)
        except ROLE_SHAPE_ERRORS as exc:
            return {"plan": None, "repair": None}, str(exc)[:300]
        if not chain:
            return {"plan": None, "repair": None}, "%s names no model and nothing was chosen" % role
        seats[side] = chain[0]
    return seats, None


def adversary_models(second, env=None, roles=None, reg=None):
    """FX-11.6 (REQ-FX11-21): {label: model} for the two adversary seats.

    Under BROTHER_BREAKER off, or with BROTHER_ADVERSARY_MODEL unset or blank, exactly {"deepseek": "deepseek",
    "deepseek-b": second}. Otherwise the chosen model is judged as the adversary role; a REFUSED choice, an unreadable
    roles file or registry, or a refused chain member raises ValueError("NO-DATA adversary refused: ..."), never a
    silent default. The second seat is the adversary chain's second name, else `second`. Raises ValueError for a caller
    defect too (second not a non empty name, env, roles or reg of the wrong type)."""
    if not isinstance(second, str) or not second.strip():
        raise ValueError("adversary_models wants the second seat's model name")
    env = _env_copy(env, "adversary_models")
    if roles is not None and not isinstance(roles, dict):
        raise ValueError("adversary_models wants a roles mapping or None")
    if reg is not None and not isinstance(reg, dict):
        raise ValueError("adversary_models wants a registry mapping or None")
    adv_on = _breaker_on(env)
    chosen = env.get(ADVERSARY_SETTING, "").strip()
    if not adv_on or not chosen:
        return {"deepseek": "deepseek", "deepseek-b": second}
    if roles is None:
        roles = load_roles()
    spec = roles.get("adversary") if isinstance(roles, dict) else None
    if not isinstance(spec, dict) or any(not isinstance(spec.get(k), str) for k in ("kind", "when", "content")):
        raise ValueError("NO-DATA adversary refused: the roles file names no readable adversary role")
    if reg is None:
        import model_router
        try:
            reg = model_router.registry()
        except (OSError, ValueError) as exc:
            raise ValueError("NO-DATA adversary refused: the model registry cannot be read (%s)" % type(exc).__name__)
    if not isinstance(reg, dict):
        raise ValueError("NO-DATA adversary refused: the model registry is not a mapping")
    row = reg.get(chosen)
    if row is not None and (not isinstance(row, dict) or not isinstance(row.get("transport"), str) or not isinstance(row.get("privacy"), str)
                            or not isinstance(row.get("kinds"), (list, tuple, set, frozenset))):
        raise ValueError("NO-DATA adversary refused: the registry row of %s is not readable" % chosen)
    verdict, line = judge("adversary", spec, chosen, reg)
    if verdict == "REFUSED":
        raise ValueError("NO-DATA adversary refused: %s" % line)
    try:
        chain = role_chain("adversary", choice=chosen, roles=roles, reg=reg, env=env)
    except ROLE_SHAPE_ERRORS as exc:
        raise ValueError("NO-DATA adversary refused: %s" % str(exc)[:300])
    member = chain[1] if len(chain) > 1 else second
    return {"deepseek": chosen, "deepseek-b": member}


def _chain_verdicts(role, spec, reg):
    """One line per written chain member, judged by the same rules as the first plus the stage rule."""
    out = []
    try:
        members = _chain_members(spec, role)
    except ValueError as exc:
        return ["REFUSED  %s" % exc]
    for member in members:
        if not isinstance(member, str):
            out.append("REFUSED  %s: the chain member %r is not a name" % (role, member)); continue
        if "fable" in member.lower():
            out.append("REFUSED  %s chain: %s, and %s" % (role, member, FABLE_ORDER)); continue
        row = reg.get(member)
        if not isinstance(row, dict):
            out.append("REFUSED  %s chain: %r is not in the model registry" % (role, member)); continue
        ok, reason = stage_ok(member, row)
        if not ok:
            out.append("REFUSED  %s chain: %s" % (role, reason)); continue
        verdict, line = judge(role, spec, member, reg)
        out.append("%-8s chain %s" % (verdict, line))
    return out


def check_role_receipt(role, receipt, registry):
    """FX-31.8 (R-FX-31-6): True only when the receipt holds a complete, current entry for this role.

    The entry (receipt["roles"][role]) must carry every field, both conformance cases C11 and C18 at PASS, a model the
    registry still has, and three SHA-256 values equal to the ones measured now: the adapter file, the executable at
    the recorded path and the registry row. A stale, incomplete or malformed receipt, a missing adapter_conformance
    module and a file that cannot be read are all False, never True."""
    if not isinstance(role, str) or not isinstance(receipt, dict) or not isinstance(registry, dict):
        return False
    try:
        import adapter_conformance as AC
    except ImportError:
        return False   # a missing authority cannot approve
    roles = receipt.get("roles")
    entry = roles.get(role) if isinstance(roles, dict) else None
    if receipt.get("schema") != AC.SCHEMA or not isinstance(entry, dict) or any(k not in entry for k in AC.ENTRY_KEYS):
        return False
    if any(entry[c] != AC.PASS for c in AC.CASES):
        return False
    row = registry.get(entry["model"]) if isinstance(entry["model"], str) else None
    if not isinstance(row, dict) or row.get("transport") != entry["transport"]:
        return False
    try:
        return AC.receipt_matches(entry, AC.adapter_sha256(entry["transport"]), AC.file_sha256(entry["binary_path"]), AC.row_sha256(row))
    except ValueError:
        return False


def check(choices, roles, reg, receipt=None):
    """(exit code, lines). 0 only when no role is REFUSED and no unknown role was named.

    FX-31.8: with a receipt given, every role that resolves to a model must also hold a current receipt entry for that
    very model (check_role_receipt); one that does not is REFUSED. receipt=None keeps today's behaviour."""
    lines, bad = [], 0
    for extra in sorted(set(choices) - set(roles)):
        lines.append("REFUSED  %s: not a role of the loop; the roles are %s" % (extra, ", ".join(roles))); bad += 1
    for role, spec in roles.items():
        verdict, line = judge(role, spec, choices.get(role), reg)
        name = choices.get(role) or spec.get("default")
        if verdict != "REFUSED" and receipt is not None and name:
            held = receipt.get("roles") if isinstance(receipt, dict) else None
            entry = held.get(role) if isinstance(held, dict) else None
            if not (isinstance(entry, dict) and entry.get("model") == name and check_role_receipt(role, receipt, reg)):
                verdict, line = "REFUSED", "%s: no current conformance receipt for %s (stale, incomplete or malformed)" % (role, name)
        lines.append("%-8s %s" % (verdict, line)); bad += verdict == "REFUSED"
        for chain_line in _chain_verdicts(role, spec, reg):
            lines.append(chain_line); bad += chain_line.startswith("REFUSED")
    lines.append("ROLES %s: %d role(s), %d refused" % ("SETTLED" if not bad else "NOT SETTLED", len(roles), bad))
    return (1 if bad else 0), lines


def main():
    if "--selftest" in sys.argv:
        return selftest()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    verb = args[0] if args else "show"
    roles = load_roles()
    if roles is None:
        print("ROLES NO-DATA: the roles file cannot be read or is not the expected shape (%s)" % (roles_path() or "no candidate found")); return 3
    if verb == "show":
        for role, r in roles.items():
            print("%-13s %-18s set by %-22s default %-9s %s" % (role, r["when"], r.get("setting") or "the intake record", r.get("default") or "(must be chosen)", r["does"][:110]))
        return 0
    try:
        import model_router
        reg = model_router.registry()
    except Exception as exc:
        print("ROLES NO-DATA: the model registry cannot be read (%s)" % type(exc).__name__); return 3
    choices = dict(a.split("=", 1) for a in args[1:] if "=" in a)
    code, lines = check(choices, roles, reg)
    print("\n".join(lines))
    return code


def selftest():
    saved = os.environ.pop("BROTHER_TRANSPORTS", None)   # every case pins its own switch value; the caller's is restored after
    try: return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120])); return 1
    finally:
        if saved is not None: os.environ["BROTHER_TRANSPORTS"] = saved


def _selftest_body():
    import subprocess, tempfile
    reg = {"cheap": {"transport": "bridge", "privacy": "public", "kinds": {"build", "grade"}},
           "strong": {"transport": "claude", "privacy": "private", "kinds": {"build", "grade", "plan", "prose"}},
           "outside": {"transport": "codex", "privacy": "public", "kinds": {"build", "grade", "prose"}}}
    role = lambda **kw: dict({"does": "x", "when": "inside", "kind": "build", "content": "public", "must_be_chosen": False, "default": "cheap"}, **kw)
    j = lambda name, spec, choice: judge(name, spec, choice, reg)
    def _under(env, fn):
        saved = os.environ.get("BROTHER_TRANSPORTS"); os.environ.pop("BROTHER_TRANSPORTS", None); os.environ.update(env)
        try: return fn()
        finally:
            os.environ.pop("BROTHER_TRANSPORTS", None)
            if saved is not None: os.environ["BROTHER_TRANSPORTS"] = saved
    cases = [("a default that fits is OK", j("worker", role(), None) == ("OK", "worker: cheap (default)")),
             ("a choice that fits is OK and says chosen", j("worker", role(), "cheap")[1].endswith("(chosen)")),
             ("an optional role with no default and no choice is a NOTE that it is not run, never a refusal", j("checker", role(default=None), None)[0] == "NOTE" and "not run" in j("checker", role(default=None), None)[1]),
             ("a role that must be chosen and was not is refused", "must be chosen" in j("finisher", role(must_be_chosen=True, default=None, when="after_run"), None)[1]),
             ("a model the registry does not know is refused", "not in the model registry" in j("worker", role(), "ghost")[1]),
             ("a model that cannot do the kind is refused", "cannot do plan work" in j("orchestrator", role(kind="plan", when="outside"), "cheap")[1]),
             ("a pinned worker off the bridge is allowed: the fan out carries every transport it knows", j("worker", role(), "strong")[0] == "OK"),
             ("an adversary off the bridge is allowed: its seats read the adversary setting (2026-09-30)", j("adversary", role(), "strong")[0] == "OK"),
             ("inside the run, content above the model's class is refused", "may receive public content at most" in j("checker", role(kind="grade", content="private"), "cheap")[1] and j("checker", role(kind="grade", content="private"), "cheap")[0] == "REFUSED"),
             ("outside the run the same gap is a NOTE, never a refusal", j("documenter", role(kind="prose", content="private", when="after_final_check", default=None), "outside")[0] == "NOTE"),
             ("a private model on private content is OK", j("checker", role(kind="grade", content="private"), "strong")[0] == "OK"),
             ("under BROTHER_TRANSPORTS=claude the worker's bridge default is REFUSED naming the setting", _under({"BROTHER_TRANSPORTS": "claude"}, lambda: j("worker", role(), None))[0] == "REFUSED" and "BROTHER_TRANSPORTS=claude" in _under({"BROTHER_TRANSPORTS": "claude"}, lambda: j("worker", role(), None))[1]),
             ("under BROTHER_TRANSPORTS=claude a chosen claude model is OK", _under({"BROTHER_TRANSPORTS": "claude"}, lambda: j("worker", role(), "strong"))[0] == "OK"),
             ("under BROTHER_TRANSPORTS=claude the adversary's bridge default is REFUSED", _under({"BROTHER_TRANSPORTS": "claude"}, lambda: j("adversary", role(), None))[0] == "REFUSED"),
             ("a malformed allowlist settles no role", "could not be read" in _under({"BROTHER_TRANSPORTS": "claude,-bridge"}, lambda: j("worker", role(), "strong"))[1]),
             ("an AFTER RUN role on a bridge model is refused under the allowlist too: the finisher spends like any role", _under({"BROTHER_TRANSPORTS": "claude"}, lambda: j("finisher", role(when="after_run", must_be_chosen=True, default=None), "cheap"))[0] == "REFUSED")]
    roles = {"worker": role(), "finisher": role(must_be_chosen=True, default=None, when="after_run", content="private")}
    code, lines = check({"finisher": "strong"}, roles, reg)
    code2, lines2 = check({}, roles, reg)
    code3, lines3 = check({"finisher": "strong", "painter": "cheap"}, roles, reg)
    cases += [("every role settled exits 0 and says SETTLED", code == 0 and lines[-1].startswith("ROLES SETTLED")),
              ("one refusal exits 1 and says NOT SETTLED", code2 == 1 and "NOT SETTLED" in lines2[-1]),
              ("an unknown role name is a refusal, never ignored", code3 == 1 and any("not a role of the loop" in l for l in lines3))]
    d = tempfile.mkdtemp(prefix="loop-roles-")
    bad = os.path.join(d, "bad.json"); open(bad, "w").write('{"roles": {"worker": {"does": "x"}}}')
    cases += [("a roles file missing a field is unreadable, never half used", load_roles(bad) is None),
              ("an absent roles file is unreadable", load_roles(os.path.join(d, "absent.json")) is None)]
    plan_copy = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "plan", "loop-roles.json")
    fixture = os.path.join(os.path.dirname(HERE), "fixtures", "loop-roles-fixture.json")
    if os.path.isfile(plan_copy) and os.path.isfile(fixture):   # on an export tree only the fixture exists: nothing to compare
        cases += [("the tracked fixture is byte identical to the roles file, so the two cannot drift", open(plan_copy, "rb").read() == open(fixture, "rb").read())]
    # THE ENTRY POINT against the REAL roles file and the REAL registry, as the intake will call it
    me = os.path.abspath(__file__)
    ok = subprocess.run([sys.executable, "-B", me, "check", "finisher=fable", "documenter=opus"], capture_output=True, text=True)
    miss = subprocess.run([sys.executable, "-B", me, "check"], capture_output=True, text=True)
    nodata = subprocess.run([sys.executable, "-B", me, "check"], capture_output=True, text=True, env=dict(os.environ, BROTHER_LOOP_ROLES=bad))
    show = subprocess.run([sys.executable, "-B", me, "show"], capture_output=True, text=True)
    cases += [("the real file with both open roles chosen is SETTLED, exit 0", ok.returncode == 0 and "ROLES SETTLED: 8 role(s), 0 refused" in ok.stdout),
              ("the real file with nothing chosen refuses finisher and documenter, exit 1", miss.returncode == 1 and miss.stdout.count("must be chosen") == 2),
              ("an unreadable roles file is NO-DATA, exit 3", nodata.returncode == 3 and "ROLES NO-DATA" in nodata.stdout),
              ("show names all eight roles", show.returncode == 0 and all(r in show.stdout for r in ("worker", "adversary", "checker", "orchestrator", "finisher", "documenter", "planner", "repair_advisor")))]
    failed = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not failed else "FAILED: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
