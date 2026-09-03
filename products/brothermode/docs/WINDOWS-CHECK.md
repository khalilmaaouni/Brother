Status: CURRENT.

# Windows check: what to do, in plain English

This page is for the person checking BrotherMode and BrotherSBE on Windows.

You do not need to know anything about this project. You do not write any code.
You run a few commands, look at what appears on screen, and copy the answers
into the form at the bottom. That is the whole job.

It takes about 30 to 45 minutes, most of which is waiting.

---

## Before you start

You need three things on your Windows machine:

1. **Git.** Check by opening PowerShell and typing `git --version`. If you see a
   version number, you have it.
2. **Python 3.9 or newer.** Type `python --version`. If you see 3.9 or higher,
   you have it.
3. **Claude Code.** Type `claude --version`. If you see a version number, you
   have it.

If any of those three is missing, stop and say so in the form. A missing tool is
a real answer, not a failure on your part.

---

## Step 1: get the code

Open PowerShell and run these two commands, one at a time:

```
git clone https://github.com/khalilmaaouni/BrotherModeUp.git
cd BrotherModeUp
```

**What you should see:** lines about downloading, ending without the word
`error`.

**Write down:** did it work, yes or no.

---

## Step 2: run the tests

Run this one command. It takes a long time, often 10 to 45 minutes. That is
normal. Leave it alone and let it finish.

```
python tools/test_all.py
```

**What you should see** at the very end, on the last line:

```
test_all: NNNN tests across NN suites, N skipped, NNNN.Ns wall. ALL GREEN
```

The important words are **ALL GREEN** at the end.

**If you see ALL GREEN:** the test passed. Write down the whole last line.

**If you do NOT see ALL GREEN:** something failed, and that is genuinely useful
information. Scroll up and find any line containing the word `FAIL`. Copy that
line. Do not try to fix anything.

**Write down:** the last line, and any FAIL line you found.

---

## Step 3: check the install

Run this:

```
python scripts/doctor.py
```

**What you should see:** a list of checks, each ending in PASS, FAIL or SKIP.

**Write down:** how many say PASS, how many say FAIL, and the name of anything
that says FAIL.

SKIP is not a problem. It means that check had nothing to look at.

---

## Step 4: try installing it the way a normal user would

Run these two commands:

```
claude plugin marketplace add khalilmaaouni/BrotherModeUp
claude plugin install brothermode@brothermode-marketplace
```

**What you should see:** a line saying `Successfully installed`.

**Write down:** did it install, yes or no, and the exact message if it failed.

---

## Step 5: the Bitbucket reporting arm

This one is short, and it is the step that matters most for a team whose code
lives on Bitbucket rather than GitHub. It needs no `bash`, no credential, and
it sends nothing anywhere.

Run these two:

```
python tools\bm_bbstatus.py classify https://bitbucket.org/example/repo.git
python tools\bm_bbstatus.py slug https://bitbucket.org/example/repo.git
```

**What you should see:** exactly `bitbucket` from the first, and exactly
`example/repo` from the second.

Now the part a person cannot see by looking, which is why it is a separate
command. Windows ends its lines differently from Mac and Linux, and an extra
invisible character on the end of those two answers is enough to make the
reporting silently stop working while still printing something that looks
right. Run this:

```
python -c "import subprocess,sys; print(repr(subprocess.run([sys.executable,'tools/bm_bbstatus.py','slug','https://bitbucket.org/example/repo.git'],stdout=subprocess.PIPE).stdout))"
```

**What you should see:**

```
b'example/repo\n'
```

**What is WRONG**, and is exactly what this step exists to catch:

```
b'example/repo\r\n'
```

The difference is the `\r`. If you see one, that is a real bug and a very
valuable thing to report. Copy the line exactly as it printed.

**Write down:** the two answers, and the `b'...'` line from the third command
copied character for character.

This step is for BrotherMode only. The second product decides the same things
in its shell script rather than by asking Python, so it cannot have this
particular fault.

---

## Step 6: the same four steps for the second product

Repeat steps 1 to 4, replacing the addresses with these:

```
git clone https://github.com/khalilmaaouni/Brothersbe.git
cd Brothersbe
```

For step 2 in this repository, the test command is different. Run:

```
bash scripts/local-gates.sh --no-post
```

If `bash` is not available, say so in the form and skip step 2 for this one.
That is a useful answer by itself.

For step 4, the two install commands are:

```
claude plugin marketplace add khalilmaaouni/Brothersbe
claude plugin install brothersbe@brothersbe
```

---

## The form: copy this and fill it in

Copy everything between the lines, fill in your answers, and send it back.
If something did not work, write what actually happened. **A problem you report
is worth more than a green tick**, because problems on Windows are exactly what
this check exists to find.

```
-----------------------------------------------------------
WINDOWS CHECK

Your name:
Date:
Windows version:
Python version (from `python --version`):
Git version (from `git --version`):
Claude Code version (from `claude --version`):

--- BrotherMode ---
Step 1, clone worked (yes / no):
Step 2, last line of the test output:
Step 2, did it say ALL GREEN (yes / no):
Step 2, any FAIL lines (paste them, or write none):
Step 3, how many PASS:
Step 3, how many FAIL:
Step 3, names of anything that FAILed:
Step 4, plugin installed (yes / no):
Step 4, message if it failed:
Step 5, answer from classify (should be exactly: bitbucket):
Step 5, answer from slug (should be exactly: example/repo):
Step 5, the b'...' line, copied character for character:

--- BrotherSBE ---
Step 1, clone worked (yes / no):
Step 2, did the gates script run at all (yes / no / bash not available):
Step 2, last line of the output:
Step 2, any FAIL lines (paste them, or write none):
Step 3, how many PASS:
Step 3, how many FAIL:
Step 3, names of anything that FAILed:
Step 4, plugin installed (yes / no):
Step 4, message if it failed:

--- Anything else ---
Anything confusing, slow, or broken that the steps above did not ask about:

-----------------------------------------------------------
```

---

## Questions you might have

**What if a command just hangs?** Give it 45 minutes. If nothing has changed
after that, press Ctrl and C together to stop it, and write down which command
hung and roughly how long you waited.

**What if I get a permission error?** Write down the exact message. Do not run
PowerShell as administrator to get around it; that would hide a real problem
that a normal user would also hit.

**Do I need to undo anything afterwards?** Only if you want to. To remove the
plugins:

```
claude plugin uninstall brothermode
claude plugin uninstall brothersbe
```

The downloaded folders can simply be deleted.

**Am I supposed to fix things?** No. Reporting is the whole job.
