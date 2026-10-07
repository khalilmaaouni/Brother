# L4 measurement task: read one passing check's full output

This is the fixed task behind the ObservationPack before and after measurement in
HARNESS-EFFICIENCY-SOLPI-ASSESSMENT.md (unit L4, REQ-02: same task, same tree, same counter).
The two runs differ in exactly one input: the gate log handed to the before run has the
`[full: PATH]` handle stripped from its PASS lines (the tree before unit L4b.1), the log handed
to the after run is the one scripts/required_fast.sh really printed. Everything else, this text
included, is byte identical between the two runs. The whole session's token usage, as the
harness reports it, is the measurement.

## The task, as given to the session verbatim (the two placeholders are filled by the runner)

You are in a checkout of this repository. The fast gate scripts/required_fast.sh already ran
on this tree and its printed summary is saved at GATE_LOG. One line of that summary is the check
named CHECK_NAME, which passed.

Answer this one question: what is the last non empty line of that check's full output (its
combined stdout and stderr) as the gate saw it? Reply with that line and nothing else.

You may read files and run commands. If the summary line names a file holding the full output,
reading that file is enough. If it does not, find the check's command in scripts/required_fast.sh
(the run_check line carrying CHECK_NAME) and run that command from the repository root to obtain
the output. Never run the whole gate. Never edit any file.
