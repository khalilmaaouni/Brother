Status: CURRENT.

# The Windows check

This is the written protocol that replaced the Windows CI leg when it was
disarmed on cost grounds. Four places in this repository referred to this file
for a day and a half before it existed, which is how three Windows defects
reached a user: the honest paragraph explaining where the coverage went was
pointing at nothing.

## Who this is for, and the one rule

You are running BrotherSBE on Windows and reporting back to somebody who has
no Windows machine and cannot watch over your shoulder.

**The rule: you are never asked to describe a problem in your own words.**
Every step below produces output you paste verbatim. If a step's output looks
wrong to you, paste it anyway and say "this looks wrong". Deciding what it
means is our job, not yours. A step that fails is a successful run of this
protocol, not a failure of it.

If you only have ten minutes, run step 1 and stop. It is worth more than the
rest combined.

---

## Step 1: the environment report

```bash
python3 bin/sbe doctor
```

Paste the whole output, including the summary line.

This one command answers what used to take a conversation. It reports the git
version against the 2.34 floor (below that, git cannot verify an SSH signature
at all, so the approval gate can never pass, whatever else is configured),
whether git has any trust anchor to verify an approval against, whether
`ssh-keygen` resolves, whether `gh` exists, and the platform every other line
must be read against.

If `python3` is not found, that is itself the finding, and it is a common one:
a python.org Windows installer ships `python.exe` and a `py` launcher but no
`python3.exe`. Try `python bin/sbe doctor` and `py bin/sbe doctor`, and tell us
which of the three worked. Do not "fix" your PATH before reporting: the fact
that the plain command failed is the result we need, because every hook this
plugin installs invokes `python3`.

For a machine-readable copy, which is easier to paste into an issue:

```bash
python3 bin/sbe doctor --json
```

## Step 2: which shell you are in

Run **each** block below in the shell it names, and paste all three. There is
one block per shell rather than one command for all three because they do not
share a language, which this file got wrong until 2026-08-18: the single
`bash` line that used to sit here is a **parse error** in Windows PowerShell
(`||` is a pipeline chain operator that arrived in PowerShell 7.0, and Windows
ships 5.1; see
https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_pipeline_chain_operators), and in `cmd.exe` the `;` is not a separator at all, so the whole
line echoed and nothing ran. Two of the three shells this step names could
never execute the step. If a tester reported that and we read it as a Windows
defect, it was ours.

**Git Bash:**

```bash
echo "shell: git bash"; echo "SHELL=$SHELL"; uname -s; git --version
command -v python3 || command -v python || echo "NO python3 AND NO python on PATH"
```

**PowerShell** (works on 5.1 and on 7; do not install 7 for us, 5.1 is what we
most want to hear about because it is what Windows ships):

```powershell
"shell: PowerShell $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition))"
git --version
Get-Command python3, python, py -ErrorAction SilentlyContinue | Select-Object Name, Source
```

**cmd.exe:**

```bat
echo shell: cmd.exe
ver
git --version
where python3
where python
where py
```

This matters more than it looks. `sh` is not on the Windows PATH at all, and
`bash` on the Windows PATH is `C:\WINDOWS\system32\bash.exe`, which is WSL, a
different filesystem where the plugin's own paths do not resolve. Knowing which
shell your tools came from decides how we read every other result.

The PowerShell block prints its own version on purpose. 5.1 and 7 differ in
ways that change what a protocol step even means: the chain operators above,
`where` (an alias for `Where-Object` in PowerShell, so it is **not** the
`where.exe` that cmd gives you), and the default encoding. `sbe doctor` now
reports the same distinction from its side by reading the parent process, so
step 1 and step 2 should agree; if they disagree, that disagreement is itself a
finding worth pasting.

## Step 3: the hooks, on a real repository

Open a session in a repository with real history, not an empty test folder.
Sizes are the entire point of this step: the defect that prompted this
protocol only appeared past a few thousand files.

```bash
python3 tools/sbe_autosave.py precompact
```

Then paste the last few lines of the autosave log:

```bash
tail -5 "$BROTHERSBE_VAULT/99-System/telemetry/autosave.log"
```

What we are looking for is one of three sentences, and all three are useful:

- `saved <sha> (precompact) ... minus N of M scanned file(s)`. It worked. Tell
  us roughly how long it took and how many files the repository has
  (`git ls-files | wc -l`).
- `SKIPPED (precompact): content scan passed its Ns deadline after X of Y
  candidate files`. It ran out of time and said so. **This is the sentence we
  most want to see if it is slow**, because it means the control refused
  honestly instead of dying in silence. Paste it with the file count.
- Nothing at all, or the command hangs. Say so plainly. Silence is a finding,
  and it is the one that cost us most.

## Step 3b: how to spell a path inside a verify command

If you write a **verify command** into a plan or a task (the command a receipt
is later matched against), spell any Windows path one of these two ways:

```bash
python3 tools/sbe_checks.py "C:\Users\you\project"   # quoted, preferred
python3 tools/sbe_checks.py C:/Users/you/project     # forward slashes
```

Not this:

```bash
python3 tools/sbe_checks.py C:\Users\you\project     # bare backslashes
```

The reason, stated plainly because you should not have to guess at it: the
declared command text and the receipt's recorded arguments are compared after
both are split the way a POSIX shell would split them (Python's `shlex` in its
default mode, in `brothersbe/converge.py` and `brothersbe/work.py`). A
backslash is an escape character to that splitter, so the bare spelling above
is read as `C:Usersyouproject`, it matches nothing, and a receipt you just
minted is reported as absent. Quoting or using a forward slash both survive
the split exactly.

This is a known limitation, not a bug we are hiding: splitting per platform
instead would make the same plan and the same receipt compare by different
rules on different machines, which is worse for evidence than a documented
spelling rule. The failure is always a no-match, never a false match. What
the no-match then reads as depends on which command hit it: `sbe work
finish` reports NO-DATA, while `sbe converge` counts a required receipt it
cannot match as FAIL, because there a statement that the check ran is not
evidence. The split behavior is pinned by `VerifyCommandWindowsPaths` in
`tools/test_sbe_windows_sim.py`.

If you hit this, it is still worth reporting: paste the verify command and the
NO-DATA line. Seeing it happen to a real person is what would move this from a
documented limit to a fix.

## Step 4: the guided run and the red team

Run both tracks in `TEST-PROTOCOL.md` and report as that file asks. Track B is
the valuable one for this platform: it is where a control that refuses on macOS
and silently does nothing on Windows would show itself.

## Step 5: the suites

```bash
python3 tools/test_sbe_windows_sim.py
python3 tools/test_sbe_hooks.py
python3 tools/test_sbe.py
```

Paste the last five lines of each. The first suite simulates Windows conditions
and should pass everywhere; if it fails on real Windows, the simulation is
wrong and that is a genuinely valuable result for us.

`tools/test_sbe.py` runs green on macOS and Linux as of 2026-08-18 (121 tests,
5 skipped for platform-specific tooling absent on this box). Paste what you get
in full; on real Windows any red here is a finding, not noise.

## Step 5b: the install path, which only a POSIX shell can run

`install.sh`, `scripts/test-install-artifact.sh` and
`scripts/test-upgrade-rollback.sh` are POSIX sh. `cmd.exe` and PowerShell
cannot run them; Git Bash (which arrives with Git for Windows, already required
above) can. This path has NOT been run on a real Windows machine, so it is
UNVERIFIED there. Closing that gap is exactly what this step asks for:

```bash
sh scripts/test-install-artifact.sh
sh scripts/test-upgrade-rollback.sh
```

Paste the last five lines of each, from Git Bash, and say which shell you used.

---

## What we cannot ask a simulation to do, which is why you are here

`tools/test_sbe_windows_sim.py` recreates the conditions that make Windows
different (separator handling, encoding, newline rewriting, hook shape) on
whatever machine runs it, so those classes are caught on every change without
waiting for you. What it cannot reach is anything that depends on the Windows
kernel rather than on values Python reports: a file that cannot be deleted
because a handle is open, a path length limit, an antivirus scanner holding a
file, a real console code page, and the true cost of starting a process.

That last one is exactly what broke: process creation costs roughly sixteen
times more on Windows than on Linux, and no simulation on our side would have
shown it. Those are the findings only you can produce, and they are why this
protocol asks for timings and file counts rather than opinions.

## Reporting

One message, in this shape:

    Environment:   <paste step 1 in full>
    Shells:        <paste step 2, labelled per shell>
    Autosave:      <paste step 3, with the repo's file count>
    Tracks:        <as TEST-PROTOCOL.md asks>
    Suites:        <last five lines of each>
    Anything that looked wrong: <one line each, no detail needed>

Raw output beats a summary every time. If in doubt, paste more.
