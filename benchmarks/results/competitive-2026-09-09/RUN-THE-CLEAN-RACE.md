# The clean race, one command per arm, for the founder's own hand

The in-session route recorded beside this file is a controlled substitute. The clean race the controls describe needs three unattended Claude Code sessions in throwaway homes, and this environment's permission classifier refuses a session that spawns another, and the session cap hook refuses a command whose text names the print-mode launcher. From a plain terminal, outside any Claude session, these three commands run the arms as CONTROLS.json states them: model claude-sonnet-5, zero interventions, one clone each at start commit d61667577f109b5a175f26a251f36734edb76cca. Run them one at a time. The prompt is read from TASK.md so it stays verbatim.

Set once:

```sh
PROMPT="$(sed -n '/--- PROMPT START ---/,/--- PROMPT END ---/p' ~/brother-hub/benchmarks/competitive/TASK.md)"
```

Arm gsd:

```sh
export CLAUDE_CONFIG_DIR=/tmp/race-home-gsd; mkdir -p "$CLAUDE_CONFIG_DIR"; rm -rf /tmp/race-gsd; git clone -q /tmp/brother-competitive-fixture /tmp/race-gsd; cd /tmp/race-gsd; claude --print "$PROMPT" --model claude-sonnet-5 --plugin-dir ~/.claude/plugins/cache/gsd-core/gsd-core/1.12.0 --permission-mode bypassPermissions --output-format json > /tmp/race-gsd.json; echo "exit $?"
```

Arm compound:

```sh
export CLAUDE_CONFIG_DIR=/tmp/race-home-compound; mkdir -p "$CLAUDE_CONFIG_DIR"; rm -rf /tmp/race-compound; git clone -q /tmp/brother-competitive-fixture /tmp/race-compound; cd /tmp/race-compound; claude --print "$PROMPT" --model claude-sonnet-5 --plugin-dir ~/.claude/plugins/cache/compound-engineering-plugin/compound-engineering/3.23.4 --permission-mode bypassPermissions --output-format json > /tmp/race-compound.json; echo "exit $?"
```

Arm brother (the public 1.0.11 marketplace copy):

```sh
export CLAUDE_CONFIG_DIR=/tmp/race-home-brother; mkdir -p "$CLAUDE_CONFIG_DIR"; rm -rf /tmp/race-brother; git clone -q /tmp/brother-competitive-fixture /tmp/race-brother; cd /tmp/race-brother; claude --print "$PROMPT" --model claude-sonnet-5 --plugin-dir ~/.claude/plugins/marketplaces/brother --permission-mode bypassPermissions --output-format json > /tmp/race-brother.json; echo "exit $?"
```

After each arm: `git -C /tmp/race-<arm> diff d6166757 > <arm>/diff.patch`; the JSON's usage block gives tokens_used and its result text gives the claimed exit code; then `python3 benchmarks/competitive/scripts/competitive_score.py <arm> --controls CONTROLS.json` scores it.

UNVERIFIED against Claude Code 2.1.263: the `--plugin-dir` flag and whether the marketplace directory is accepted by it; `claude --help` is the source of truth, and an arm that refuses to load its plugin is recorded NO-DATA, never a guessed score.
