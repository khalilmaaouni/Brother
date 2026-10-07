#!/usr/bin/env python3
"""OpenRouter's own answer to "how much money can still be spent": the account balance (GET /api/v1/credits:
total_credits minus total_usage) and this key's own cap (GET /api/v1/key: limit_remaining, null when it has none).
The smaller of the two is what the provider will still serve (owner, 2026-09-27: "check available budget from
Openrouter too").

usage: or_balance.py [--root DIR]      prints one PROVIDER line; exit 0 read, 3 NO-DATA

A reading is cached in <root>/provider-balance.json for TTL seconds, so a pass that asks several times costs one
request. The key is read from the keychain at call time (account pinned to the user), sent only to openrouter.ai,
never printed or stored. BROTHER_OR_BALANCE_FILE names a JSON file holding the two answers' fields instead of the
network (a sandbox or a test). Any failure is NO-DATA with its reason: never a zero, never a stop by itself."""
import json, math, os, subprocess, sys, time, urllib.request

TTL = 60
CACHE = "provider-balance.json"
URLS = {"credits": "https://openrouter.ai/api/v1/credits", "key": "https://openrouter.ai/api/v1/key"}


def _amount(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("%s is not a finite amount: %r" % (name, value))
    return float(value)


def available(credits, key):
    """(available, account, key_remaining) from the two answers' data objects. key_remaining None means the key has
    no cap of its own. Raises ValueError on anything that is not a finite, non negative amount."""
    total, used = _amount(credits.get("total_credits"), "total_credits"), _amount(credits.get("total_usage"), "total_usage")
    account = total - used
    remaining = key.get("limit_remaining")
    remaining = None if remaining is None else _amount(remaining, "limit_remaining")
    return (account if remaining is None else min(account, remaining)), account, remaining


def _keychain_key():
    r = subprocess.run(["security", "find-generic-password", "-s", "openrouter", "-a", os.environ.get("USER", ""), "-w"],
                       capture_output=True, text=True, timeout=20)
    key = (r.stdout or "").strip()
    if r.returncode != 0 or not key:
        raise RuntimeError("no OpenRouter key in the keychain")
    return key


def _network():
    key = _keychain_key()
    try:
        out = {}
        for name, url in URLS.items():
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=15) as resp:
                out[name] = (json.load(resp) or {}).get("data") or {}
        return out
    finally:
        key = None


def _seam(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {"credits": d, "key": d}


def not_used(env=None):
    """Why OpenRouter's balance is NOT READ in this run, or ''. Under BROTHER_TRANSPORTS without the bridge transport
    (owner 2026-09-30: a Claude only run) no OpenRouter call is made, so the keychain key never leaves the machine and
    the provider's balance can neither fund nor defund a lane (burn_guard.funding reads PROVIDER-LOW only from an OK
    reading). The ONE definition of the allowlist is model_router.transports_allowed, beside this file; the setting
    present but unreadable also means not used."""
    env_map = os.environ if env is None else env
    raw = env_map.get("BROTHER_TRANSPORTS")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import model_router
        allowed = model_router.transports_allowed(env_map)
    except Exception as exc:   # sbe: allow-silent the reason is the reading's why; nothing is asked of the provider
        return "BROTHER_TRANSPORTS=%s is set but the allowlist could not be read (%s): OpenRouter not asked" % (raw.strip(), type(exc).__name__)
    if allowed is not None and "bridge" not in allowed:
        return "BROTHER_TRANSPORTS=%s allows no bridge transport: OpenRouter is not used in this run" % raw.strip()
    return ""


def read(root, now=None, fetch=None):
    """{"status": "OK", "available", "account", "key_remaining", "at"} or {"status": "NO-DATA", "why"}, or
    {"status": "NOT-USED", "why"} when the run's transport allowlist leaves OpenRouter out (nothing is fetched, no cache
    is read or written). A cached reading younger than TTL is reused; a failed read is never cached."""
    skip = not_used()
    if skip:
        return {"status": "NOT-USED", "why": skip}
    now = time.time() if now is None else now
    cache = os.path.join(root, CACHE)
    try:
        with open(cache, encoding="utf-8") as f:
            got = json.load(f)
        if got.get("status") == "OK" and 0 <= now - float(got["at"]) < TTL:
            return got
    except (OSError, ValueError, KeyError, TypeError):
        pass   # sbe: allow-silent no usable cache simply means ask the provider
    try:
        seam = os.environ.get("BROTHER_OR_BALANCE_FILE")
        answers = (fetch or (lambda: _seam(seam) if seam else _network()))()
        avail, account, remaining = available(answers["credits"], answers["key"])
    except Exception as exc:   # sbe: allow-silent every failure is a named NO-DATA reading, never a number
        return {"status": "NO-DATA", "why": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    got = {"status": "OK", "available": avail, "account": account, "key_remaining": remaining, "at": now}
    try:
        os.makedirs(root, exist_ok=True)
        tmp = cache + ".tmp-%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(got, f)
        os.replace(tmp, cache)
    except OSError:
        pass   # sbe: allow-silent the cache only saves a request; the reading itself stands
    return got


def line(reading):
    if reading.get("status") == "NOT-USED":
        return "PROVIDER NOT USED: %s; the run's own budget governs and no OpenRouter balance funds or defunds a lane" % reading.get("why")
    if reading.get("status") != "OK":
        return "PROVIDER NO-DATA: OpenRouter's balance could not be read (%s); the run's own budget still governs" % reading.get("why")
    rem = reading.get("key_remaining")
    return "PROVIDER OpenRouter available %.2f USD (account %.2f, key cap %s)" % (
        reading["available"], reading["account"], "none" if rem is None else "%.2f left" % rem)


def selftest():
    """The transport allowlist cases, with a fetch that records calls and a warm cache: nothing reaches the provider."""
    import tempfile
    root = tempfile.mkdtemp(prefix="or-balance-self-")
    calls = []
    fetch = lambda: calls.append(1) or {"credits": {"total_credits": 5, "total_usage": 1}, "key": {"limit_remaining": None}}
    saved = os.environ.get("BROTHER_TRANSPORTS"); os.environ.pop("BROTHER_TRANSPORTS", None)
    try:
        warm = read(root, 100.0, fetch=fetch)                       # writes the cache
        os.environ["BROTHER_TRANSPORTS"] = "claude"
        under = read(root, 101.0, fetch=fetch)                      # the cache is warm and younger than TTL: still NOT-USED
        os.environ["BROTHER_TRANSPORTS"] = "claude,-bridge"
        junk = read(root, 101.0, fetch=fetch)
        os.environ["BROTHER_TRANSPORTS"] = "bridge,claude"
        again = read(root, 101.0, fetch=fetch)
        cases = [("without the setting the fetch is reached and the reading is OK", warm.get("status") == "OK" and calls == [1]),
                 ("under an allowlist without the bridge a WARM cache is not read: NOT-USED, no fetch", under.get("status") == "NOT-USED" and calls == [1] and "allows no bridge" in under["why"]),
                 ("a malformed allowlist is NOT-USED naming the reason, no fetch", junk.get("status") == "NOT-USED" and "could not be read" in junk["why"] and calls == [1]),
                 ("an allowlist naming the bridge reads as before (the warm cache answers)", again.get("status") == "OK"),
                 ("line() names NOT USED", "NOT USED" in line(under))]
    finally:
        if saved is not None: os.environ["BROTHER_TRANSPORTS"] = saved
        else: os.environ.pop("BROTHER_TRANSPORTS", None)
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        sys.exit(selftest())
    _known = {"--root"}
    _extra = [x for i, x in enumerate(args) if x.startswith("-") and x not in _known] + [x for i, x in enumerate(args) if not x.startswith("-") and (i == 0 or args[i - 1] != "--root")]
    if _extra:   # an unknown argument refuses BEFORE any provider call: --selftest used to be a live fetch (attack 3, 2026-09-30)
        print("REFUSED: unknown argument(s) %s; usage: or_balance.py [--root DIR] | --selftest" % _extra); sys.exit(2)
    root = args[args.index("--root") + 1] if "--root" in args else (
        os.environ.get("BROTHER_OR_STATE_ROOT") or os.path.expanduser("~/.claude/brother-or-dispatch-state"))
    r = read(root)
    print(line(r))
    sys.exit(0 if r.get("status") == "OK" else 3)
