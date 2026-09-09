# Adopting an existing repository

LOAD WHEN: the user is standing in a repository that already holds code but no project yet, or once memory exists and vault routing words need somewhere to point.

(Extracted verbatim from brothermode/start's SKILL.md, moved here so the common kickoff path does not pay for a branch it does not take; see WBS-TODAY.md Intake V2 byte floor.)

Answer in the language the user wrote in, per references/honesty.md.

## A repository that already exists: adopt first, one command, at most one question

If the user is standing in a repository that already holds code but no project yet, the first step is not the guided kickoff above: it is `python3 "${CLAUDE_PLUGIN_ROOT}/tools/bm_project.py" adopt --ask "<their words>"` (same install-path rule as below). ONE command. It reads what the repository already says (its name, its branch, its remote, the test suites it documents, the evidence already on disk) and writes one typed project record, so nothing is asked that the repository could have answered. It asks AT MOST ONE question, and only when the repository never says how it is checked: "How do you check this repository is healthy? Name one command." Answer it in the same breath with `--answer success_checks="<their command>"`. Read its exit code rather than its prose: 0 means the record is complete, 3 means that one question is still open, 2 means nothing was written. Never ask the user for a fact this command can read, and never invent one: a fact the repository does not carry is recorded as NO-DATA, in the open, rather than guessed. A team that already tracks the work in a ticketing system passes `--ticket jira:PAY-142` (SYSTEM:ID) to bind the CR id to the record and turn `audit.required` on (or `--no-audit` to keep it off), and `--branch NAME` to bind an existing or about-to-exist branch, with provenance `git` when the branch is already there and `ask` when the plan still has to create it.

Later, once memory exists to ask about, the words "vault doctor", "vault census" and "vault recall <query>" all route to that same command (`bm_vault_cli.py doctor` / `census` / `recall`), never a separate command of their own; see references/memory.md for the routing.
