#!/usr/bin/env python3
"""Arm C: ONE continuous Claude Code session (owner 2026-09-23 14:4x: no per turn restart, no early cut off), fed follow up
messages the way a developer types them into the same window: per sub unit, implement, then verify on both Pythons and
fix, then commit. Normal Claude Code context (user settings, CLAUDE.md, plugins, skills), no MCP servers. Every output event is appended to arm-c-stream.jsonl,
every turn is timed in c-turns.tsv. Runs until the queue is done; stop_arm_c.sh ends it at the slot end.
usage: c_driver.py <ab dir> <tree> <session id>"""
import json, os, subprocess, sys, time
ab, tree, sid = sys.argv[1:4]
queue = [l.rstrip("\n").split("\t") for l in open(os.path.join(ab, "c-queue.tsv")) if l.strip()]
stream = open(os.path.join(ab, "arm-c-stream.jsonl"), "a", buffering=1)
turns = open(os.path.join(ab, "c-turns.tsv"), "a", buffering=1)
tools = ("Agent,Task,Read,Edit,Write,Grep,Glob,Bash(python3:*),Bash(/usr/bin/python3:*),Bash(git add:*),Bash(git commit:*),"
         "Bash(git diff:*),Bash(git status:*),Bash(git log:*),Bash(date),Bash(ls:*)")
p = subprocess.Popen([os.path.expanduser("~/.local/bin/claude"), "-p", "--session-id", sid, "--model", "claude-opus-5-5",
                      "--effort", "high", "--strict-mcp-config",   # NORMAL Claude Code context (owner 14:5x), MCP off (owner 14:3x)
                       "--permission-mode", "acceptEdits",
                      "--allowedTools", tools, "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"],
                     cwd=tree, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(os.path.join(ab, "arm-c-stderr.log"), "a"),
                     text=True, bufsize=1)

def say(sub, name, text):
    t0 = time.strftime("%H:%M:%S")
    p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"); p.stdin.flush()
    result = None
    for line in p.stdout:                      # one turn ends at its result event
        stream.write(line)
        try: ev = json.loads(line)
        except ValueError: continue
        if ev.get("type") == "result": result = ev; break
    if result is None:
        turns.write("%s\t%s\t%s\t%s\tSESSION ENDED\n" % (sub, name, t0, time.strftime("%H:%M:%S"))); sys.exit(0)
    turns.write("%s\t%s\t%s\t%s\t%s\n" % (sub, name, t0, time.strftime("%H:%M:%S"), result.get("subtype", "?")))

for unit, sub, spec, chk in queue:
    chk = chk or "the done check command written in its section"
    say(sub, "implement", "Implement sub unit %s of unit %s. Its specification is the section \"%s\" in %s. Read that section and "
        "the files it names, implement exactly what it asks, and write or update the tests it names. Do not commit yet." % (sub, unit, sub, spec))
    say(sub, "verify", "Run the done check for %s (%s) with python3 and again with /usr/bin/python3. If either fails, fix the code "
        "and rerun until both pass. Then tell me the last line of each run." % (sub, chk))
    say(sub, "commit", "Commit only the files you changed for %s, with the message \"ABC-C %s: <one line on what it delivers>\". "
        "Do not push." % (sub, sub))
turns.write("QUEUE DONE\n"); p.stdin.close(); p.wait()
