---
name: stop
description: Stop the Full-Auto controller run right now, draining in-flight work and releasing every held claim
disable-model-invocation: true
---

Outcome to produce: the controller run for this project is stopped, cleanly, with the user told exactly what state it landed in.

Enter the Full-Auto stop flow. This is the kill switch on the RUN itself (distinct from pausing or revoking the underlying autonomy contract, which is `bm-autonomy pause` or `bm-autonomy revoke`, a separate act the user may also want).

Run the mechanical command `python3 tools/bm_controller.py stop --project <id> --controller-id <the same id the run was started or resumed with> --actor-name <the user's name>`; never answer from memory of this conversation about what state the run was in. This command never fails hard: if there is no run at all, or it already reached a terminal state, it says so and does nothing further. Otherwise it drains any in-flight unit (recording whatever result had already arrived, never inventing one), releases every fence it held, and reports the state it moved from and to.

Tell the user plainly what happened and, if any unit was left mid-flight when the stop landed, name it: nothing is lost, but that unit will need a fresh dispatch the next time the run resumes.

Continuity, before you end the session. Stopping the run is not the same as the work being finished: the units the run had left are still owed to someone. If open work remains once the stop has landed, run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py" continue` (the packaged console script is `brothermode continue`; on a clone install run `python3 tools/brothermode_cli.py continue` from the BrotherMode root). It writes the handoff packet from that project's own records, prints the exact command that starts the next session, and starts it, so a stop at three in the morning does not become a program that waits until someone wakes up. Add `--dry-run` to write the packet and print the command without launching anything. If the launch is refused or impossible, say so plainly and name the packet file the next session must read: Silence is the only forbidden outcome.

v3 note: this skill is the internal, hidden replacement for the legacy `/brotherme-stop` command (the founder's 2026-08-07 night rename decision, refutation ruling B5). It stays user-invocable by explicit name with `disable-model-invocation: true`: a kill switch is exactly the action a founder must be able to reach directly and immediately, without depending on Claude choosing to route a natural-language message to the right skill (see skills/auto/SKILL.md's v3 note for the full reasoning on this choice). It does not call `tools/brothermode_cli.py`: `bm_controller.py stop` is not one of the ten verbs the boundary owns, so this stays a documented internal-adapter exception per ruling H4.
