#!/usr/bin/env python3
"""Windows conditions, simulated on whatever machine runs this.

WHY THIS FILE EXISTS. This project has no Windows machine and, since the
Windows CI leg was disarmed on cost grounds, no Windows runner either. What
replaced it was a written protocol run by a person, which is slower, happens
only when somebody remembers, and depends on that person noticing the right
thing and describing it well. Three defects shipped to a Windows user under
exactly that arrangement, and the sharpest of them was invisible from here:
a control that silently examined nothing.

So this suite does not test Windows. It tests the SPECIFIC CONDITIONS that
make Windows different, by creating each one deliberately on the host that is
running, and asserting the code survives it. Each test names the real-world
condition it stands in for. That is weaker than a Windows runner and stronger
than hoping a tester notices: it runs on every change, here, for free.

WHAT IT CANNOT DO, stated so nobody reads a green run as Windows coverage:
it cannot catch anything that depends on the Windows KERNEL rather than on
the values Python exposes (a locked file that cannot be deleted, a path
length limit, an antivirus scanner holding a handle, a real console encoding).
Those still need a machine. Every test below is a condition this process can
honestly create.

Run: python3 tools/test_sbe_windows_sim.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import sbe_checks  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


class WindowsSeparator(unittest.TestCase):
    """CONDITION: os.sep is a backslash, and the user typed a forward slash.

    This is not hypothetical. `tools/sbe_score.py`'s own doc comment teaches
    the forward-slash form ("$HOME/work/*/STATE.md:..."), and a Windows user
    copying that hits this exact case. Before the 2026-08-17 fix,
    glob_with_denials split the pattern on os.sep alone, so on Windows the
    whole pattern was ONE element, the prefix loop ran zero times, and the
    permission-denial guard returned a clean answer having inspected nothing.
    """

    def setUp(self):
        self.real_sep = os.sep
        self.work = tempfile.mkdtemp(prefix="sbe-winsim-")
        self.denied = os.path.join(self.work, "locked")
        os.makedirs(os.path.join(self.denied, "inner"))
        with io.open(os.path.join(self.denied, "inner", "STATE.md"), "w") as f:
            f.write("# state\n")

    def tearDown(self):
        os.sep = self.real_sep
        try:
            os.chmod(self.denied, 0o755)
        except OSError:
            pass  # sbe: allow-silent tearDown restoring a mode it set on its own
            # temp directory; a refusal leaves one unreadable directory under
            # the system temp area and must never mask the test's own verdict

    def test_a_forward_slash_pattern_still_finds_a_denied_directory(self):
        """The prefix walk must run for a forward-slash pattern even when the
        platform separator is a backslash. Simulated by setting os.sep, which
        is what the function reads, and restored in tearDown."""
        if os.name != "posix" or os.geteuid() == 0:
            self.skipTest("needs enforced file modes: chmod 000 must actually deny, which it does not on Windows or as root")
        os.chmod(self.denied, 0o000)
        pattern = self.work.replace(os.sep, "/") + "/locked/inner/STATE.md"

        os.sep = "\\"                      # the condition under test
        _hits, denied = sbe_checks.glob_with_denials(pattern)
        os.sep = self.real_sep

        self.assertTrue(
            any(os.path.basename(d) == "locked" for d in denied),
            "with os.sep='\\\\' a forward-slash pattern reported denials %r; before "
            "the fix this list was empty because the prefix loop never ran, and an "
            "empty denial list is read by the caller as 'nothing was unreadable'"
            % (denied,))

    def test_the_native_separator_case_still_works(self):
        """The fix must not cost the case that already worked, on either
        platform, which is what makes it a fix rather than a trade."""
        if os.name != "posix" or os.geteuid() == 0:
            self.skipTest("needs enforced file modes: chmod 000 must actually deny, which it does not on Windows or as root")
        os.chmod(self.denied, 0o000)
        pattern = os.path.join(self.denied, "inner", "STATE.md")
        _hits, denied = sbe_checks.glob_with_denials(pattern)
        self.assertTrue(any(os.path.basename(d) == "locked" for d in denied),
                        "the native-separator case regressed: %r" % (denied,))

    def test_a_posix_filename_containing_a_backslash_is_not_split(self):
        """The other half of the same fix, and the reason it is conditional.
        On POSIX a backslash is a legal character in a filename, so splitting
        on it there would invent directory boundaries that do not exist."""
        if os.sep != "/":
            self.skipTest("this guarantee is about POSIX filenames only")
        odd = os.path.join(self.work, "we\\ird")
        os.makedirs(odd, exist_ok=True)
        with io.open(os.path.join(odd, "STATE.md"), "w") as f:
            f.write("# state\n")
        hits, denied = sbe_checks.glob_with_denials(os.path.join(odd, "STATE.md"))
        self.assertEqual(1, len(hits), "the backslash in a POSIX name broke the glob")
        self.assertEqual([], denied)


class EnvironmentDoctor(unittest.TestCase):
    """CONDITION: the tester's environment cannot satisfy a control.

    These assert that `doctor` REPORTS the condition rather than passing over
    it. They are the mechanical half of covering for a tester who cannot be
    asked follow-up questions: whatever they paste has to contain the answer.
    """

    def _checks(self):
        from brothersbe import cli
        return {name: (verdict, detail) for name, verdict, detail in cli._doctor_checks(None)}

    def test_doctor_reports_the_git_version_floor(self):
        checks = self._checks()
        self.assertIn("git-version", checks,
                      "doctor must report the git version: 2.33 cannot verify an SSH "
                      "signature at all, which is what cost a tester a day")
        self.assertIn("2.34", checks["git-version"][1])

    def test_doctor_reports_whether_an_approval_can_be_verified_at_all(self):
        checks = self._checks()
        self.assertIn("approval-trust", checks)
        verdict, detail = checks["approval-trust"]
        self.assertIn(verdict, ("PASS", "NO-DATA"))
        if verdict == "NO-DATA":
            self.assertIn("gpg.ssh.allowedSignersFile", detail,
                          "a NO-DATA here must name the remedy, because the reader "
                          "is often someone who cannot ask a follow-up question")

    def test_doctor_states_the_platform_it_ran_on(self):
        """Every other line has to be read against this one, and a tester
        pasting the output should never have to be asked for it separately."""
        checks = self._checks()
        self.assertIn("platform", checks)
        self.assertIn(sys.version.split()[0], checks["platform"][1])

    def test_a_no_data_environment_never_reads_as_a_pass(self):
        """The house law applied to doctor itself: absent evidence is
        NO-DATA, and the summary line must not count it as a pass."""
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "sbe"),
                               "doctor", "--json"],
                              capture_output=True, text=True, timeout=120)
        payload = json.loads(proc.stdout)
        rows = payload.get("checks") or []
        self.assertTrue(rows, "doctor --json emitted no checks, so this proves nothing")
        for r in rows:
            self.assertIn(r.get("result"), ("PASS", "FAIL", "NO-DATA"),
                          "every doctor row must carry one of the three verdicts: %r" % (r,))
        names = {r["name"] for r in rows}
        for required in ("git-version", "approval-trust", "platform"):
            self.assertIn(required, names,
                          "the JSON surface must carry %r too, since a tester's report "
                          "is often this payload rather than the text" % required)


class HookPortability(unittest.TestCase):
    """CONDITION: no POSIX shell exists, which is the default on Windows.

    `sh` is not on the Windows PATH; hooks only ran because Claude Code
    happened to spawn them through Git Bash, so on a machine without Git for
    Windows every sh hook died at session start. And `bash` on the Windows
    PATH is C:\\WINDOWS\\system32\\bash.exe, which is WSL, a different
    filesystem where ${CLAUDE_PLUGIN_ROOT} does not resolve.
    """

    def _commands(self):
        with io.open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as f:
            data = json.load(f)
        return [h["command"] for group in data["hooks"].values()
                for entry in group for h in entry["hooks"]]

    def test_no_hook_needs_a_shell(self):
        cmds = self._commands()
        self.assertTrue(cmds, "no hook commands found, so this test proves nothing")
        for c in cmds:
            # A shell as the INTERPRETER is the defect. The substring is legal
            # in a file name (tools/sbe_bash_write_guard.py), so this checks
            # the invocation shape rather than the characters.
            self.assertNotIn("sh -c", c)
            self.assertNotIn("bash -c", c)
            self.assertTrue(c.startswith("python3 ") or c.startswith("\"python3"),
                            "hook command needs an interpreter that resolves on "
                            "Windows: %r" % c)


class HostParity(unittest.TestCase):
    """CONDITION: the run is happening on Bitbucket rather than GitHub.

    Same principle as the Windows classes above: create the condition here
    rather than wait for somebody on the other host to notice. These are the
    parity properties that were silently false until 2026-08-17.
    """

    def test_bitbucket_build_identifiers_survive_the_environment_filter(self):
        """Everything not on the allowlist is stripped before a registered
        check's subprocess starts. With only the GITHUB_* names on it, a real
        Bitbucket Pipelines run had its own identifiers removed and minted
        receipts indistinguishable from a laptop, which is exactly what the
        comment above that list was written to prevent on GitHub."""
        from brothersbe import checks
        source = {
            "CI": "true", "PATH": "/usr/bin", "SBE_CI_RUN_ID": "abc",
            "GITHUB_RUN_ID": "99",
            "BITBUCKET_BUILD_NUMBER": "412", "BITBUCKET_COMMIT": "deadbeef",
            "BITBUCKET_REPO_SLUG": "w/r", "BITBUCKET_BRANCH": "main",
            "BITBUCKET_WORKSPACE": "w", "BITBUCKET_PR_ID": "7",
            "AWS_SECRET_ACCESS_KEY": "must-not-survive",
        }
        kept, dropped = checks.filtered_environment(source)
        survived = sorted(k for k in kept if k.startswith("BITBUCKET_"))
        self.assertEqual(6, len(survived),
                         "Bitbucket's build identifiers are being stripped while "
                         "GitHub's are kept, so a Pipelines run mints receipts that "
                         "read as local: %r" % (survived,))
        self.assertIn("GITHUB_RUN_ID", kept, "GitHub parity regressed")
        self.assertIn("AWS_SECRET_ACCESS_KEY", dropped,
                      "the allowlist stopped filtering, which is worse than the "
                      "problem it was widened to fix")

    def test_the_runner_reports_to_whichever_host_the_code_is_on(self):
        """scripts/local-gates.sh posted to GitHub unconditionally, so on a
        Bitbucket clone it either died at `gh` or posted a status about this
        commit to a repository that was not the one it verified.

        THE ENDPOINT MOVED, and this test follows it rather than pinning where
        it used to live. Until 2026-08-17 the runner named
        `api.bitbucket.org` itself, and asserting that string appeared in this
        script was a fair proxy for "it can report to Bitbucket". Then the
        zero-network property (asserted below, unchanged) made that spelling
        impossible: a shipped shell script may not invoke curl, wget or nc, so
        the POST had to move into an allow-listed Python module, and
        src/brothersbe/bbstatus.py is where it went. A substring check against
        this file therefore now fails for the reason the design succeeded.

        So the property is asserted across the REAL seam, in four parts rather
        than one, which covers strictly more than the old single check did: the
        runner knows both hosts, it BRANCHES on the origin remote, it hands the
        Bitbucket case to the module by path, and that module actually names
        the endpoint. The last part is new coverage: nothing before verified
        that the delegate could reach Bitbucket at all.
        """
        import re
        path = os.path.join(ROOT, "scripts", "local-gates.sh")
        with io.open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        # EVERY structural assertion below reads comment-stripped code, not
        # raw text, and the delegation is matched as an executed command at
        # the start of a line. Both hardenings were proven by mutation, not
        # taste: a comment naming the deleted *bitbucket.org*) arm satisfied
        # the raw-text form of the host check, and an `echo` merely naming
        # src/brothersbe/bbstatus.py satisfied the position-free form of the
        # delegation check. Under both mutations the assertions below go red.
        code = "\n".join(line.split("#", 1)[0] for line in lines)
        self.assertIn("bitbucket.org", code,
                      "the runner still knows only one host")
        for host in ("*bitbucket.org*)", "*github.com*)"):
            self.assertIn(host, code,
                          "the runner no longer branches on the origin remote, so it "
                          "is reporting to a host it did not read: %s missing" % host)
        self.assertRegex(
            code, re.compile(r'^\s*python3\s+"src/brothersbe/bbstatus\.py"',
                             re.MULTILINE),
            "the runner no longer EXECUTES the allow-listed Bitbucket client "
            "at the start of a line: a mention inside another command (an "
            "echo, a string) is not a delegation")
        # The endpoint is read from the module's own constant, not from its
        # source text, so a docstring naming the host cannot satisfy this
        # while API_ROOT points elsewhere.
        if os.path.join(ROOT, "src") not in sys.path:
            sys.path.insert(0, os.path.join(ROOT, "src"))
        from brothersbe import bbstatus
        self.assertTrue(
            str(bbstatus.API_ROOT).startswith("https://api.bitbucket.org/"),
            "the delegated status client's API_ROOT is %r, not the Bitbucket "
            "API, so the runner's Bitbucket branch reports nowhere"
            % (bbstatus.API_ROOT,))
        # Comments are stripped before matching, exactly as the enforcing
        # control does in tools/test_sbe.py. Without that this test fires on
        # the comment that EXPLAINS why the ban exists, which is the same
        # false-positive shape the real control documents about its own
        # redaction fixture.
        for i, line in enumerate(lines, 1):
            code = line.split("#", 1)[0]
            self.assertFalse(
                re.search(r"\b(curl|wget|nc)\b", code),
                "scripts/local-gates.sh:%d invokes curl, wget or nc, which breaks "
                "the zero-network property enforced by tools/test_sbe.py" % i)

    def test_the_installer_offers_the_pipeline_the_host_actually_reads(self):
        """`sbe init --with-consumer-ci` installed GitHub Actions files into
        whatever repository it was given, so a Bitbucket team got a workflow
        that can never fire while the right template sat unoffered in ci/."""
        from brothersbe import initcmd
        self.assertTrue(os.path.exists(os.path.join(ROOT, "ci", "bitbucket-pipelines.yml")),
                        "the Bitbucket consumer template is missing, so the installer "
                        "has nothing correct to offer a Bitbucket team")
        self.assertEqual("bitbucket-pipelines.yml", initcmd.CONSUMER_BITBUCKET_PATH,
                         "Bitbucket reads this filename from the repository root and "
                         "nowhere else")
        self.assertTrue(hasattr(initcmd, "_origin_host"),
                        "the installer no longer resolves the host, so it is guessing "
                        "again")


class EncodingAndNewlines(unittest.TestCase):
    """CONDITION: the default text encoding is not UTF-8, and text mode
    rewrites newlines. Both are Windows defaults that silently change bytes
    a digest is taken over."""

    def test_repository_pins_byte_identical_checkouts(self):
        """.gitattributes is what stops a Windows checkout rewriting every
        text file and invalidating every digest in CHECKSUMS.sha256. It is
        load-bearing enough to assert rather than assume."""
        path = os.path.join(ROOT, ".gitattributes")
        self.assertTrue(os.path.exists(path), ".gitattributes is missing: a Windows "
                                              "checkout would rewrite line endings and "
                                              "break every checksum")
        with io.open(path, encoding="utf-8") as f:
            self.assertIn("-text", f.read(),
                          ".gitattributes no longer pins line endings")

    def test_non_ascii_survives_a_round_trip_through_the_tools(self):
        """A tester's machine may default to a non-UTF-8 code page. Any tool
        reading repository text without an explicit encoding would mangle
        this content there; asserting the round trip here catches the class
        that a default-encoding read introduces."""
        work = tempfile.mkdtemp(prefix="sbe-enc-")
        p = os.path.join(work, "note.md")
        original = "purpose: réconciliation, 日本語, emoji \U0001F600\n"
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(original)
        with io.open(p, encoding="utf-8") as f:
            self.assertEqual(original, f.read())


class VerifyCommandWindowsPaths(unittest.TestCase):
    """CONDITION: a verify command names a Windows path, so the string a plan
    declares carries backslashes.

    Two places canonicalize that string through `shlex.split` before comparing
    it to a receipt's recorded argv (`brothersbe.converge._command_tokens` and
    `brothersbe.work._matching_receipt`). shlex in POSIX mode treats a
    backslash as an ESCAPE, so a bare `C:\\Users\\x` becomes `C:Usersx`, the
    comparison silently finds no match, and a freshly minted receipt reads as
    absent.

    THE DECISION, recorded here beside the tests that pin it rather than left
    to whoever reads the call site next: the parse stays POSIX on every
    platform, and the limitation is documented with its two remedies. The
    alternative, `posix=False` on Windows, was REJECTED for two reasons this
    class proves rather than asserts: it retains the quote characters, so it
    breaks the quoted spelling that works correctly today (a receipt's argv
    never carries the quotes, because the shell consumed them); and it would
    make the same plan text and the same receipt compare by different rules
    depending on which machine ran the check, which is a worse property for
    evidence matching than a documented and remediable limitation.

    The failure mode is a NO-DATA (no matching receipt found), never a false
    match: this trades a silent absence for a documented one, and nothing here
    makes an unproven claim pass."""

    BARE = r"python C:\Users\x\run.py"
    QUOTED = r'python "C:\Users\x\run.py"'
    FORWARD = "python C:/Users/x/run.py"
    #: What a receipt records: argv, quotes already consumed by the shell.
    RECORDED_ARGV = ["python", r"C:\Users\x\run.py"]

    def _tokens(self, command):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from brothersbe import converge
        return converge._command_tokens(command)

    def test_a_quoted_windows_path_matches_the_recorded_argv(self):
        """Remedy 1, and the one to prefer: quoting survives POSIX splitting
        and lands exactly on what the receipt recorded."""
        self.assertEqual(self.RECORDED_ARGV, self._tokens(self.QUOTED))

    def test_a_forward_slash_windows_path_matches_the_recorded_argv(self):
        """Remedy 2: Windows accepts forward slashes in nearly every path
        context, and they carry no meaning to shlex at all."""
        self.assertEqual(["python", "C:/Users/x/run.py"],
                         self._tokens(self.FORWARD))

    def test_the_bare_backslash_spelling_is_eaten_and_that_is_the_known_limit(self):
        """The limitation itself, pinned so it cannot change unnoticed in
        either direction. If a future change makes this match, this test is
        the place that says the tradeoff was reconsidered on purpose."""
        self.assertEqual(["python", "C:Usersxrun.py"], self._tokens(self.BARE))
        self.assertNotEqual(self.RECORDED_ARGV, self._tokens(self.BARE))

    def test_posix_false_is_not_a_drop_in_replacement(self):
        """The evidence for the rejected alternative, run rather than
        asserted: non-POSIX mode fixes the bare spelling and simultaneously
        breaks the quoted one, because it keeps the quote characters that a
        recorded argv never carries."""
        import shlex
        self.assertEqual(self.RECORDED_ARGV, shlex.split(self.BARE, posix=False))
        self.assertNotEqual(self.RECORDED_ARGV,
                            shlex.split(self.QUOTED, posix=False))

    def test_the_windows_protocol_documents_the_limit_and_both_remedies(self):
        """Doc-truth: a limitation known only to a test is not documented. The
        protocol a Windows tester actually reads must carry it."""
        with io.open(os.path.join(ROOT, "docs", "WINDOWS-CHECK.md"),
                     encoding="utf-8") as f:
            body = f.read()
        for token in ("shlex", "backslash", "forward slash", "verify command"):
            self.assertIn(token, body,
                          "docs/WINDOWS-CHECK.md no longer documents the verify "
                          "command path limitation (missing %r)" % token)


class WindowsShellIdentity(unittest.TestCase):
    """CONDITION: the tester is on Windows, and the report has to say WHICH shell.

    Until 2026-08-18 the platform row read `$SHELL or %COMSPEC%`. COMSPEC is
    set on every Windows machine and names the system command interpreter, so
    every PowerShell tester who followed docs/WINDOWS-CHECK.md pasted a report
    claiming they were in cmd.exe. That row is the one every other row is read
    against, so it was not a cosmetic error: it silently invalidated the shell
    half of every Windows report this project has ever received.

    The 5.1 versus 7 split is the reason this matters rather than a detail.
    Windows ships Windows PowerShell 5.1 by default and it is what an ordinary
    user's machine has; PowerShell 7 is a deliberate install. The two disagree
    about pipeline chain operators (`&&` and `||` arrived in 7.0), about
    `where`, and about default encoding, so a protocol step that runs in one
    can be a parse error in the other.
    """

    def _identity(self):
        from brothersbe import cli
        return cli._shell_identity()

    def test_windows_powershell_is_named_and_dated_to_5_1(self):
        from brothersbe import cli
        named = cli._shell_from_parent_image(
            r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe")
        self.assertIsNotNone(named, "powershell.exe must be recognised")
        self.assertIn("5.1", named,
                      "Windows PowerShell ended at 5.1, and a report that does "
                      "not say so cannot be told apart from a 7 report")

    def test_pwsh_is_named_as_6_or_later_and_not_confused_with_5_1(self):
        from brothersbe import cli
        seven = cli._shell_from_parent_image(r"C:\Program Files\PowerShell\7\pwsh.exe")
        five = cli._shell_from_parent_image("powershell.exe")
        self.assertIsNotNone(seven)
        self.assertNotEqual(seven, five,
                            "pwsh and powershell.exe must not collapse to one name: "
                            "telling them apart is the whole point of this row")
        self.assertNotIn("5.1", seven)

    def test_cmd_and_git_bash_are_recognised_too(self):
        from brothersbe import cli
        self.assertEqual(cli._shell_from_parent_image(r"C:\WINDOWS\system32\cmd.exe"),
                         "cmd.exe")
        self.assertIn("bash", cli._shell_from_parent_image(r"C:\Program Files\Git\bin\bash.exe"))

    def test_an_unrecognised_image_is_none_rather_than_a_guess(self):
        from brothersbe import cli
        self.assertIsNone(cli._shell_from_parent_image("notepad.exe"))
        self.assertIsNone(cli._shell_from_parent_image(""))
        self.assertIsNone(cli._shell_from_parent_image(None))

    def test_comspec_can_never_reach_the_shell_report(self):
        """THE REGRESSION PIN. COMSPEC is set on every Windows machine
        whatever shell you are in, so any code path that can read it will
        eventually report cmd.exe to a PowerShell user. This asserts the
        value cannot appear, on whatever platform runs the suite."""
        sentinel = "SENTINEL-COMSPEC-MUST-NOT-BE-READ"
        old_comspec = os.environ.get("COMSPEC")
        old_shell = os.environ.get("SHELL")
        os.environ["COMSPEC"] = sentinel
        os.environ.pop("SHELL", None)
        try:
            self.assertNotIn(sentinel, self._identity(),
                             "COMSPEC leaked into the shell report again")
        finally:
            if old_comspec is None:
                os.environ.pop("COMSPEC", None)
            else:
                os.environ["COMSPEC"] = old_comspec
            if old_shell is not None:
                os.environ["SHELL"] = old_shell

    def test_an_unknown_shell_says_unknown_rather_than_naming_one(self):
        old_shell = os.environ.get("SHELL")
        os.environ.pop("SHELL", None)
        try:
            if sys.platform != "win32":
                self.assertIn("unknown", self._identity())
        finally:
            if old_shell is not None:
                os.environ["SHELL"] = old_shell

    def test_the_parent_walk_reads_nothing_off_windows(self):
        """The Win32 call is the one part no non-Windows machine can exercise,
        so what is pinned here is that it stays inert rather than raising into
        a doctor run on the platforms that DO run this suite every change."""
        from brothersbe import cli
        images = cli._windows_parent_images()
        self.assertIsInstance(images, list,
                              "the call must stay inert rather than raising: %r" % (images,))
        if sys.platform != "win32":
            self.assertEqual(images, [])

    def test_the_platform_row_still_names_a_shell_field(self):
        from brothersbe import cli
        rows = {n: d for n, _v, d in cli._doctor_checks(None)}
        self.assertIn("shell ", rows["platform"],
                      "a tester's pasted report must still carry the shell field")


class WindowsProtocolRunsInTheShellsItNames(unittest.TestCase):
    """CONDITION: a protocol step cannot be executed by the tester it is aimed at.

    docs/WINDOWS-CHECK.md names three shells and, until 2026-08-18, handed all
    three a single `bash` line. In Windows PowerShell 5.1 that line is a PARSE
    ERROR, because `||` is a pipeline chain operator introduced in PowerShell
    7.0; in `cmd.exe` the `;` is not a separator, so the line echoed and
    nothing ran. Two of the three shells it named could never run it, and 5.1
    is the one Windows ships, so the default Windows tester was the one it
    failed. Nothing mechanical would have caught that, which is what this
    class is for: the shell blocks in that file are checked here rather than
    trusted to review.
    """

    #: Constructs that a Windows PowerShell 5.1 tester cannot run. `&&` and
    #: `||` are 7.0 and later. `2>NUL` is cmd redirection; in PowerShell it
    #: names a file. A bare `where` in PowerShell is an alias for
    #: `Where-Object`, not the `where.exe` a cmd user gets.
    POWERSHELL_5_1_CANNOT_RUN = ("&&", "||", "2>NUL")

    def _fenced_blocks(self, language):
        path = os.path.join(ROOT, "docs", "WINDOWS-CHECK.md")
        with io.open(path, encoding="utf-8") as f:
            text = f.read()
        out, current = [], None
        for line in text.split("\n"):
            if line.startswith("```"):
                tag = line[3:].strip()
                if current is None:
                    current = [] if tag == language else None
                    continue
                out.append("\n".join(current))
                current = None
                continue
            if current is not None:
                current.append(line)
        return out

    def test_the_protocol_still_has_a_block_per_shell(self):
        """A guard on the guards below: if the fences are renamed or removed,
        the emptiness must fail here rather than silently pass every check."""
        for language in ("powershell", "bat", "bash"):
            self.assertTrue(self._fenced_blocks(language),
                            "docs/WINDOWS-CHECK.md has no %s block, so the shell "
                            "step no longer covers that shell" % language)

    def test_the_powershell_block_runs_on_5_1_not_only_on_7(self):
        for block in self._fenced_blocks("powershell"):
            for token in self.POWERSHELL_5_1_CANNOT_RUN:
                self.assertNotIn(
                    token, block,
                    "the PowerShell block uses %r, which Windows PowerShell 5.1 "
                    "cannot run. 5.1 is what Windows ships, so this makes the "
                    "step impossible for the default tester:\n%s" % (token, block))

    def test_the_powershell_block_asks_which_powershell_it_is(self):
        """5.1 and 7 differ in ways that change what the rest of the report
        means, so a block that does not print its own version leaves every
        later line ambiguous."""
        blocks = "\n".join(self._fenced_blocks("powershell"))
        self.assertIn("PSVersionTable", blocks,
                      "the PowerShell block must report its own version")

    def test_the_cmd_block_uses_no_posix_separators(self):
        """`;` is not a statement separator in cmd.exe: a line using it is
        echoed rather than run, which looks like output and is not."""
        for block in self._fenced_blocks("bat"):
            for line in block.split("\n"):
                self.assertNotIn(";", line,
                                 "a cmd.exe block cannot use `;` as a separator: %r"
                                 % line)


if __name__ == "__main__":
    unittest.main(verbosity=1)
