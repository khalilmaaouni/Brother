#!/usr/bin/env python3
"""OpenRouter ground truth: the key's own cumulative usage in USD, as OpenRouter bills it (killed and straggler calls
included). usage: or_meter.py LABEL  -> appends {"at", "label", "usage", "limit"} to ab/or-meter.jsonl and prints it.
The key is read from the keychain at call time (account pinned), sent only to openrouter.ai, never printed or stored.
Any failure prints NO-DATA and exits 3: a missing reading is never a zero."""
import datetime, json, os, subprocess, sys, urllib.request
AB = os.path.dirname(os.path.abspath(__file__))
label = sys.argv[1] if len(sys.argv) > 1 else "reading"
sys.path.insert(0, os.path.dirname(AB)); import or_balance   # noqa: E402  the ONE reading of the allowlist for OpenRouter metadata
_skip = or_balance.not_used()
if _skip:
    print("NO-DATA or_meter %s: %s" % (label, _skip)); sys.exit(3)
try:
    key = subprocess.run(["security", "find-generic-password", "-s", "openrouter", "-a", os.environ.get("USER", ""), "-w"],
                         capture_output=True, text=True, timeout=20).stdout.strip()
    if not key: raise RuntimeError("no key in the keychain")
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + key})
    d = json.load(urllib.request.urlopen(req, timeout=30)).get("data") or {}
    row = {"at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "label": label, "usage": d.get("usage"), "limit": d.get("limit"),
           "usage_daily": d.get("usage_daily")}
    if not isinstance(row["usage"], (int, float)): raise RuntimeError("no usage figure in the answer")
except Exception as exc:
    print("NO-DATA or_meter %s: %s" % (label, type(exc).__name__ + ": " + str(exc)[:120])); sys.exit(3)
finally:
    key = None
with open(os.path.join(AB, "or-meter.jsonl"), "a") as f: f.write(json.dumps(row) + "\n")
print(json.dumps(row))
