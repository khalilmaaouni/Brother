---
name: auto
description: Begin or resume the Full-Auto controller, which sequences a signed outcome to a checked deliverable
argument-hint: [nothing needed once a contract is signed]
disable-model-invocation: true
---

Outcome to produce: the durable Full-Auto controller (`docs/FULL-AUTO.md`) either begins a fresh run or resumes an existing one, and the user sees plainly what it just did and what happens next.

Enter the Full-Auto flow. This is a different, more advanced layer than the guided start (`/brothermode:start`): it requires a signed authorisation (the autonomy contract, `docs/AUTONOMY.md`) before any run can begin, and it drives real work through a resumable loop rather than a single conversation.

FIRST, check for a live contract by running `python3 tools/bm_autonomy.py show --project <id>`. If none exists, the user has not authorised autonomous work yet: explain in plain language what signing means (the outcome, how done is defined, which folders and actions are allowed, an optional token and minute budget, and that credential entry, payments, account sign-in, permanent deletion, and publishing can never be granted, ever), gather the answers, and run `python3 tools/bm_autonomy.py sign` yourself with the gathered details. Never invent an outcome or a done definition the user did not actually state.

ONCE a live contract exists, run `python3 tools/bm_controller.py start --project <id> --outcome "<the same outcome>" --done-definition "<the same done definition>" --controller-id <a stable id for this session> --actor-name <the user's name>`. Read its output rather than answering from memory of this conversation about what state the run is in:

- If it reports a fresh run in state `NEW`, the unit graph does not exist yet. Building that graph (breaking the outcome into small, dependency-ordered units with a done-check command each) is this session's own judgement, never something the controller invents; do that work, then hand the graph to `python3 tools/bm_controller.py plan --units-file <file> --controller-id <id> --actor-name <name>`.
- If it reports a dispatched unit and a printed brief, that unit is now this session's own work to do. Do it, then call `python3 tools/bm_controller.py record-result --dispatch-id <id from status --json> --worker-claim "<what was done>" --artifact <path> --controller-id <id> --actor-name <name>` to hand the result back. Never call `record-result` for work that was not actually done.
- If it reports `DELIVERABLE_READY`, name exactly what the report says remains founder-gated (open human steps, failed units) and tell the user plainly that only they can accept it, with `/brothermode:auto-status` for the full picture and a manual `bm-controller complete` call once they are satisfied.
- If it reports `WAITING_HUMAN` or `PAUSED`, say so in plain language and name the one thing that would unblock it.

Run this same command again, with the same `--controller-id`, to continue driving the loop forward; it always resumes from where it left off and never repeats completed work.

v3 note: this skill is the internal, hidden replacement for the legacy `/brotherme-auto` command (the founder's 2026-08-07 night rename decision, refutation ruling B5: "the full-auto trio... become INTERNAL hidden skills, preserved behaviour, hidden from the public slash surface. No retirement"). It stays user-invocable by explicit name and keeps `disable-model-invocation: true` rather than `user-invocable: false`: starting real autonomous work is exactly the class of side-effecting, timing-controlled action plan section 3.3 asks to gate from model auto-invocation, and Claude Code's frontmatter offers no field that hides a skill from the `/` menu while leaving it directly typeable by the founder (confirmed against the official skills reference this session: `user-invocable: false` removes the founder's own ability to invoke it, not only the menu listing), so preserving founder direct control was chosen over literal menu-hiding for this skill and the rest of the safety-critical trio (`stop`, plus `handback` and `handover-pack` in the founder-mode family). It does not call `tools/brothermode_cli.py`: none of `bm_autonomy.py` or `bm_controller.py`'s verbs are among the ten the boundary owns (freeze answer 4), so this flow stays a documented internal-adapter exception to the boundary gate, per ruling H4 ("the 17 console scripts stay as internal adapters this run").
