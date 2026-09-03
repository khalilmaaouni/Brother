# Computer control and founder gates

LOAD WHEN: the machine (Xcode, simulators, browsers, GUI apps) is about to be driven directly, or a founder-gated action (credentials, releases, destructive operations) is in view.

(Extracted verbatim from SKILL.md section 11; see SKILL.md for the full law.)

## 11. Computer control and founder gates
Drive the machine end to end: Xcode and simulators (build, test, record), GitHub
Desktop for pushes (or your team's push flow), your IDE, browsers (in-app browser by
default; real Chrome or Edge only when logged-in sessions are needed), Finder, any
app via computer use, web research with opened-and-read sources.
GUI control is a SINGLETON: the machine has one keyboard, one pointer, one screen,
so exactly one agent (normally the orchestrator) drives the GUI at any moment;
subagents needing machine control get the CLI equivalent whenever one exists
(xcodebuild and simctl over clicking Xcode, git plumbing over clicking, scripts over
UI), and GUI-only flows (GitHub Desktop, App Store Connect pages, native-app-only
tasks) are serialized through the single driver with a screenshot-verify step after
every consequential click.
VENUE SELECTION: pick the environment, not just the tool. a second IDE agent when its separate
quota or IDE context genuinely helps (a second independent repo worked in
parallel, or this harness near its session limits); Edge or Chrome for flows needing
the founder's logged-in sessions; the dedicated MCPs (Xcode, video vision, docs)
over generic shells when they exist.
TOOL DISCOVERY AND CREATION: when a capability is missing, do not hand-roll around
it; first search what exists (the MCP registry, plugin marketplaces, installed
skills), propose promising finds to the founder before installing (curation
decisions are theirs; the declined-by-choice list is respected), and when nothing
fits, BUILD the tool (a script, a skill via skill-creator, a hook) and register it
so the capability compounds instead of being re-improvised. TOOL EXPERTISE COMPOUNDS OR
IT IS RE-DISCOVERED: the tool register is consulted BEFORE a tool is used and appended
AFTER a use that was verified, never after merely reading documentation about it. Every
recipe carries the date and version it was verified against, and a recipe older than 90
days is stale rather than trusted, because version-sensitive facts typed from memory are
the most reliable way to waste a session. Gotchas are recorded only when they cost a
real failure. HARD GATES that stay
with the founder, stated plainly the moment they are hit:
- Credentials, sign-ins, payments, and the Apple developer account login: never
  typed, never automated. Operating an already-authenticated app is fine for
  reversible actions; anything that submits, publishes, releases, or spends gets
  founder confirmation first.
- Project file surgery where founder work lives (signing, targets), App Store and
  TestFlight submissions, entitlement grants: founder-gated, with a prepared
  click-path so their step takes five minutes.
- Destructive operations: print exactly what will be affected, confirm explicitly,
  every time.

