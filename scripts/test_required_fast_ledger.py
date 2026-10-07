#!/usr/bin/env python3
"""D4.d checker: the durable gate ledger written by scripts/required_fast.sh.

REQ-GL. run_check appends one durable three field line, check_name TAB
exit_code TAB duration_ms, per check execution, BESIDE the two field
name TAB code file the obligation step already reads; that file's shape is
byte identical to before D4.d.

REQ-ND. A missing ledger is NO-DATA and never a pass, so check_ledger returns
False for an absent path. A torn or corrupt line blocks: check_ledger raises
ValueError rather than skipping it. A field that is not a string at all
(None, an int, bytes) is refused with ValueError BEFORE the decimal regex
runs, because re.match on a non string raises TypeError, and a raw
interpreter exception reaching the caller is a crash, not a refusal.

The behavioural tests extract the script's own now_ms and run_check
definitions verbatim from scripts/required_fast.sh and run them in a temp
directory against stub commands, so M-D4-LEDGER-SKIP (dropping the durable
append) turns TestLedgerShape red. Standard library only, no network, every
file read as bytes.
"""
import os
import re
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED_FAST = os.path.join(HERE, "required_fast.sh")

#: The line that opens required_fast.sh's main body. The extracted shell block
#: ends just before it.
MAIN_ECHO = 'echo "Brother: required-fast, the pre-merge contract"'

_DECIMAL = re.compile(r"^-?[0-9]+$")


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def _script_text():
    try:
        return _read_bytes(REQUIRED_FAST).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("required_fast.sh is not valid utf-8: %s" % exc)


def _decimal_field(text, label, number):
    """`text` as an int, or ValueError.

    A field that is not a string is REFUSED here, before any regex runs:
    re.match raises TypeError on None, an int or bytes, and a raw interpreter
    exception is not a refusal. The message reads type(text).__name__ rather
    than repr(text) so that a hostile object with a raising __repr__ cannot
    crash the refusal itself.
    """
    if not isinstance(text, str):
        raise ValueError("line %d: %s must be a string, got %s"
                         % (number, label, type(text).__name__))
    if not _DECIMAL.match(text):
        raise ValueError("line %d: %s is not a decimal integer: %r"
                         % (number, label, text))
    return int(text)


def check_ledger(path):
    """Return True when every line of the ledger at path is three TAB
    separated fields with an exit code in 0..255 and a duration at zero or
    above.

    A missing ledger is NO-DATA and returns False, never True. A line that is
    not three TAB fields, a code outside 0..255, a negative duration, or a
    field that is not a decimal integer raises ValueError: corrupt input
    blocks rather than being skipped.
    """
    if not isinstance(path, str):
        raise ValueError("ledger path must be a string, got %s"
                         % type(path).__name__)
    path = path.strip()
    if not path:
        raise ValueError("ledger path must not be empty")
    if not os.path.exists(path):
        return False
    if os.path.isdir(path):
        raise ValueError("ledger path is a directory, not a file: %s" % path)
    try:
        raw = _read_bytes(path)
    except OSError as exc:
        raise ValueError("ledger could not be read: %s" % exc)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("ledger is not valid utf-8: %s" % exc)
    for number, line in enumerate(text.split("\n"), 1):
        if line == "":
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError("line %d: expected three TAB separated fields, "
                             "found %d" % (number, len(fields)))
        check_name, code_text, duration_text = fields
        if not check_name:
            raise ValueError("line %d: check name is empty" % number)
        code = _decimal_field(code_text, "exit_code", number)
        duration_ms = _decimal_field(duration_text, "duration_ms", number)
        if code < 0 or code > 255:
            raise ValueError("line %d: exit_code outside 0..255: %d"
                             % (number, code))
        if duration_ms < 0:
            raise ValueError("line %d: duration_ms is negative: %d"
                             % (number, duration_ms))
    return True


def _shell_functions(script_text):
    """The verbatim now_ms and run_check definitions from
    scripts/required_fast.sh, sliced from the now_ms header to the first line
    of the script's main body. Raising here, rather than returning a partial
    block, keeps the checker honest when the script is reshaped."""
    lines = script_text.split("\n")
    start = None
    for index, line in enumerate(lines):
        if line == "now_ms() {":
            start = index
            break
    if start is None:
        raise ValueError("required_fast.sh does not define now_ms() at column 0")
    end = None
    for index in range(start, len(lines)):
        if lines[index] == MAIN_ECHO:
            end = index
            break
    if end is None:
        raise ValueError("required_fast.sh never reaches its main-body echo")
    return "\n".join(lines[start:end])


def _run_harness(script_text, calls, preamble=(), stdout_path=None):
    """Run the script's own now_ms and run_check against stub commands in a
    temp directory. Returns (codes_path, ledger_path)."""
    work = tempfile.mkdtemp(prefix="required-fast-ledger-")
    codes = os.path.join(work, "codes.txt")
    ledger = os.path.join(work, "gates.tsv")
    lines = (['exec > "%s"' % stdout_path] if stdout_path else []) + [
        "worktree_key=probe-$$",
        "pass=0; fail=0; nodata=0",
        'failed_names=""',
        'nodata_names=""',
        "summary_printed=0",
        "REQUIRED_FAST_PRINT_FAILURES=0",
        "GITHUB_ACTIONS=",
        'export TMPDIR="%s"' % work,
        'export GATE_LEDGER_PATH="%s"' % os.path.join(work, "gate-ledger.jsonl"),
        'codes_file="%s"' % codes,
        'gate_ledger="%s"' % ledger,
        ': > "$codes_file"',
        ': > "$gate_ledger"',
        _shell_functions(script_text),
        # ACC2: run_check is now the dispatcher in front of the executor
        # check_now; one job in the first phase sends each call straight
        # through it, so these calls cross the real dispatch path.
        "jobs=1",
        "phase=parallel",
    ]
    lines.extend(preamble)
    lines.extend(calls)
    completed = subprocess.run(["sh", "-c", "\n".join(lines)], cwd=work,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        raise AssertionError("the run_check harness exited %d: %s"
                             % (completed.returncode,
                                completed.stderr.decode("utf-8", "replace")))
    return codes, ledger


class TestLedgerShape(unittest.TestCase):
    """The durable ledger the shell writes and the reader that checks it."""

    def test_run_check_writes_one_ledger_line_per_check(self):
        codes, ledger = _run_harness(_script_text(), [
            "run_check probe-zero sh -c 'exit 0'",
            "run_check probe-two sh -c 'exit 2'",
            "run_check probe-one sh -c 'exit 1'",
        ])
        rows = [row for row in _read_bytes(ledger).decode("utf-8").split("\n")
                if row]
        self.assertEqual(len(rows), 3, rows)
        self.assertEqual(rows[0].split("\t")[0:2], ["probe-zero", "0"])
        self.assertEqual(rows[1].split("\t")[0:2], ["probe-two", "2"])
        self.assertEqual(rows[2].split("\t")[0:2], ["probe-one", "1"])
        self.assertTrue(check_ledger(ledger))
        self.assertEqual(
            _read_bytes(codes).decode("utf-8"),
            "probe-zero\t0\nprobe-two\t2\nprobe-one\t1\n")

    def test_a_refused_ledger_append_reaches_the_summary_line(self):
        """2026-09-30: the JSON ledger append's stderr went to /dev/null, so its
        "NO-DATA: gate ledger append refused" line reached nobody. It now rides
        on the check's own summary line; the verdict and exit code stay."""
        out = tempfile.mkdtemp(prefix="required-fast-stdout-")
        stdout_path = os.path.join(out, "stdout.txt")
        codes, _ = _run_harness(_script_text(), ["run_check probe-zero sh -c 'exit 0'"], preamble=[
            'ln -s "%s" scripts' % HERE,
            'export GATE_LEDGER_PATH="%s"' % os.path.join(out, "no-such-dir", "gate-ledger.jsonl"),
        ], stdout_path=stdout_path)
        text = _read_bytes(stdout_path).decode("utf-8")
        self.assertIn("NO-DATA: gate ledger append refused", text)
        self.assertRegex(text, r"PASS +exit 0 +probe-zero")
        self.assertEqual(_read_bytes(codes).decode("utf-8"), "probe-zero\t0\n")

    def test_backward_clock_is_clamped_and_never_changes_the_verdict(self):
        preamble = [
            "now_ms() {",
            '  _tick_file="$TMPDIR/tick"',
            "  _tick=0",
            '  [ -f "$_tick_file" ] && _tick="$(cat "$_tick_file")"',
            "  _tick=$(( _tick + 1 ))",
            '  echo "$_tick" > "$_tick_file"',
            '  if [ "$_tick" -eq 1 ]; then echo 5000; else echo 1000; fi',
            "}",
        ]
        codes, ledger = _run_harness(
            _script_text(),
            ["run_check back-step sh -c 'exit 0'"],
            preamble=preamble)
        rows = [row for row in _read_bytes(ledger).decode("utf-8").split("\n")
                if row]
        self.assertEqual(rows, ["back-step\t0\t0"], rows)
        self.assertTrue(check_ledger(ledger))
        self.assertEqual(_read_bytes(codes).decode("utf-8"), "back-step\t0\n")

    def test_missing_ledger_is_no_data(self):
        work = tempfile.mkdtemp(prefix="required-fast-ledger-")
        self.assertFalse(check_ledger(os.path.join(work, "absent.tsv")))

    def test_two_field_codes_file_is_not_a_ledger(self):
        work = tempfile.mkdtemp(prefix="required-fast-ledger-")
        path = os.path.join(work, "codes.txt")
        with open(path, "wb") as handle:
            handle.write(b"version-truth\t0\n")
        with self.assertRaises(ValueError):
            check_ledger(path)

    def test_torn_ledger_line_blocks(self):
        work = tempfile.mkdtemp(prefix="required-fast-ledger-")
        path = os.path.join(work, "gates.tsv")
        with open(path, "wb") as handle:
            handle.write(b"version-truth\t0\t812\nbroken line\t0\n")
        with self.assertRaises(ValueError):
            check_ledger(path)

    def test_well_formed_ledger_is_accepted(self):
        work = tempfile.mkdtemp(prefix="required-fast-ledger-")
        path = os.path.join(work, "gates.tsv")
        with open(path, "wb") as handle:
            handle.write(b"version-truth\t0\t812\nbundle-runtime\t2\t0\n")
        self.assertTrue(check_ledger(path))

    def test_non_decimal_and_out_of_range_fields_block(self):
        work = tempfile.mkdtemp(prefix="required-fast-ledger-")
        bad = (
            b"probe\t0\tnan\n",
            b"probe\tnan\t1\n",
            b"probe\t1.0\t1\n",
            b"probe\t1\t1.0\n",
            b"probe\t-1\t1\n",
            b"probe\t256\t0\n",
            b"probe\t0\t-5\n",
            b"probe\t0\t0\t0\n",
            b"\t0\t0\n",
            b"probe\t0\n",
            b"probe\t 0\t1\n",
            b"probe\t0\t1\r\n",
        )
        for index, payload in enumerate(bad):
            path = os.path.join(work, "bad-%d.tsv" % index)
            with open(path, "wb") as handle:
                handle.write(payload)
            with self.assertRaises(ValueError, msg=payload):
                check_ledger(path)

    def test_hostile_ledger_input_is_refused(self):
        work = tempfile.mkdtemp(prefix="required-fast-ledger-")
        for bad in (None, 17, 1.5, b"gates.tsv", ["gates.tsv"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                check_ledger(bad)
        with self.assertRaises(ValueError):
            check_ledger("")
        with self.assertRaises(ValueError):
            check_ledger(work)          # a directory where a file belongs
        binary = os.path.join(work, "binary.tsv")
        with open(binary, "wb") as handle:
            handle.write(b"probe\t0\t1\n\xff\xfe\n")
        with self.assertRaises(ValueError):
            check_ledger(binary)

    def test_decimal_field_refuses_non_string_text(self):
        """The hostiles finding: _decimal_field(None, ...) reached re.match
        and raised TypeError. Every non string field now refuses with the
        module's own ValueError, and the good path is unchanged."""
        for bad in (None, 17, 1.5, True, b"0", bytearray(b"0"), [0], ("0",),
                    {"0": 0}, object()):
            with self.assertRaises(ValueError, msg=type(bad).__name__):
                _decimal_field(bad, "exit_code", 1)
        self.assertEqual(_decimal_field("812", "duration_ms", 1), 812)
        with self.assertRaises(ValueError):
            _decimal_field("nan", "exit_code", 1)

    def test_required_fast_defines_now_ms_used_twice_by_run_check(self):
        # ACC2: the timing lives in the executor, check_now, which run_check
        # (now the dispatcher) calls; its body ends at the next function.
        text = _script_text()
        self.assertIn("now_ms() {", text)
        self.assertLess(text.index("now_ms() {"), text.index("check_now() {"))
        executor = _shell_functions(text).split("check_now() {", 1)[1].split("\n}\n", 1)[0]
        self.assertEqual(executor.count("now_ms"), 2, executor)

    def test_required_fast_never_deletes_and_pid_keys_the_ledger(self):
        text = _script_text()
        self.assertIn("required-fast-gates-$worktree_key.tsv", text)
        self.assertIn("${BROTHER_RUN_DIR:-${TMPDIR:-/tmp}}", text)
        self.assertNotIn('rm -f "$gate_ledger"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
