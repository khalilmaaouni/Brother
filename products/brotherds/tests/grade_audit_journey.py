"""Grader for tests/test_audit_journey.py: it passes, it drives the CLI, and it leaves the real HOME alone."""
import hashlib, os, pathlib, re, subprocess, sys

bad = []
T = "products/brotherds/tests/test_audit_journey.py"
if not os.path.exists(T):
    print("CHECK FAIL: missing %s" % T); print("1 CHECK FAILURE(S)"); sys.exit(1)
def indexes():
    home = pathlib.Path.home()
    configs = {home / ".claude", home / ".codex"}
    configs.update(pathlib.Path(os.environ[key]).expanduser() for key in
                   ("BROTHER_CONFIG_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME") if os.environ.get(key))
    states = {}
    for config in configs:
        for suffix in ("", "-wal", "-shm"):
            path = config / ("bm_vault_index.sqlite3" + suffix)
            states[str(path)] = ((path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                                 if path.exists() else None)
    return states

before = indexes()
p = subprocess.run([sys.executable, T], capture_output=True, text=True, timeout=900)
out = p.stdout + p.stderr
if p.returncode != 0 or "JOURNEY PASS (31 checks)" not in p.stdout:
    bad.append("journey did not pass: exit %d; tail: %s" % (p.returncode, out[-1500:]))
if len(re.findall(r"^ok \d+ ", p.stdout, re.M)) != 31:
    bad.append("expected thirty-one 'ok N' lines")
after = indexes()
if before != after:
    bad.append("the real vault index changed during the journey (HOME not isolated)")
src = open(T, encoding="utf-8").read()
if any("import " + name in src for name in ("mdm_audit", "mdm_eval", "pack_mdm", "bds")):
    bad.append("the journey must drive the command line, not import the tool's modules")
if "HOME" not in src or "TemporaryDirectory" not in src:
    bad.append("the journey must run with a temporary HOME")
if src.count("mdm-audit") < 3:
    bad.append("the journey must call the mdm-audit verb at least three times")
if "\u2014" in src or "\u2013" in src:
    bad.append("dash character in source")
if bad:
    for x in bad[:20]:
        print("CHECK FAIL:", x)
    print("%d CHECK FAILURE(S)" % len(bad)); sys.exit(1)
print("CHECK PASS")
