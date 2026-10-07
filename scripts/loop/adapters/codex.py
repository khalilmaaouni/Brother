"""The Codex adapter (FX-31.4): the `codex exec` command line, verdict and cost behind one object.

  argv   model_call's codex command line, unchanged: the program, exec, the row's id as -m, -C and the trusted code
         root, --sandbox read-only, then the prompt as the last element. stdin is "", a CLOSED input: with the prompt
         already on argv, codex still prints "Reading additional input from stdin" and hangs on an open one (measured
         2026-09-21). The timeout is checked here and enforced by the runner's wall; codex exec takes no flag for it.
  judge  the contract's one judge (adapters.judge): a non zero exit, an EMPTY stdout at exit zero and a provider
         refusal all fail, never an answer; the verdict carries the call's cost.
  cost   codex exec prints no bill, so its stdout and stderr are never read for one. A cost_usd the result carries is
         the contract's normalize_cost (a priced zero stays 0.0); an absent one is NOT_MEASURED, never 0.0.

THE PROGRAM AND THE CODE ROOT ARE THE CALLER'S TO NAME. model_call resolves the binary (model_router.codex_bin) and
the trusted directory (its _codex_root) and hands both in. Run from a directory it does not trust, codex exits 1 with
"Not inside a trusted directory" (measured 2026-09-21), so an absent, relative or missing root REFUSES the call: this
adapter never falls back to the child's cwd. Nothing here starts a process.
"""
import os

import adapters as A
from adapters.bridge import MODEL_NAME

#: model_call._argv's codex subcommand and sandbox pair. READ ONLY, always: the brief is the whole input, and a headless
#: child that can write edited two tracked files in the launch worktree (measured 2026-09-22).
EXEC = "exec"
SANDBOX = ("--sandbox", "read-only")
#: The closed input: "" is written and the pipe closes, so codex reads end of input instead of waiting on the caller.
CLOSED_STDIN = ""


def _directory(path):
    """True for an absolute path naming an existing directory. A relative path would resolve against the child's
    cwd, which is the runner's scratch and not a directory codex trusts."""
    return isinstance(path, str) and "\0" not in path and os.path.isabs(path) and os.path.isdir(path)


class CodexAdapter(object):
    """The codex transport's argv, judge and cost.

    program  the Codex binary's absolute path, handed in by the caller (model_router.codex_bin).
    root     the trusted code root codex runs in (-C), handed in by the caller (model_call._codex_root).
    Both are checked when the command line is built, so a binary or a root that disappeared refuses at call time.
    """

    transport = "codex"

    def __init__(self, program=None, root=None):
        self.program, self.root = program, root

    def argv(self, model, prompt, timeout, row):
        """(argv, stdin, None), the shape model_call._argv returns, with stdin closed. Refused before any process starts
        for a row of another transport, a row id or model that is not a plain name, an empty prompt, one holding a NUL
        or one that starts with a dash (codex would read it as a flag, --sandbox included), a timeout that is not a
        whole number of seconds of at least 1 (a bool is not one), a program that is not an executable file named by
        absolute path, or a code root that is not an absolute, existing directory."""
        if not isinstance(row, dict) or row.get("transport") != self.transport:
            raise A.Refused("the Codex adapter builds only a codex row's command line, not %.120r" % (row,))
        model_id = row.get("id")
        if not isinstance(model_id, str) or not MODEL_NAME.fullmatch(model_id):
            raise A.Refused("a codex row's id must be a plain model id, not %.120r" % (model_id,))
        if not isinstance(model, str) or not MODEL_NAME.fullmatch(model):
            raise A.Refused("model must be a plain registry name, not %.120r" % (model,))
        if not isinstance(prompt, str) or not prompt.strip() or "\0" in prompt:
            raise A.Refused("the prompt must be non-empty text without a NUL byte")
        if prompt.startswith("-"):
            raise A.Refused("a prompt that starts with a dash rides on codex's argv and would be read as a flag")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise A.Refused("timeout must be a whole number of seconds, at least 1, not %.60r" % (timeout,))
        program = self.program
        if not isinstance(program, str) or "\0" in program or not os.path.isabs(program) \
                or not os.path.isfile(program) or not os.access(program, os.X_OK):
            raise A.Refused("no Codex program: the caller names an executable by absolute path and this adapter "
                            "never guesses one (%.120r)" % (program,))
        root = self.root
        if not _directory(root):
            raise A.Refused("no trusted code root: codex runs in an absolute, existing directory the caller names, "
                            "never in the child's cwd (%.120r)" % (root,))
        argv = [program, EXEC, "-m", model_id, "-C", root] + list(SANDBOX) + [prompt]
        return argv, CLOSED_STDIN, None

    def judge(self, model, row, result):
        """The contract's AdapterResult for one finished codex run, with cost_usd from cost. A row of another transport,
        a result that is not a mapping and a returncode that is not an int (False is not exit 0) are refused, so no
        other transport's output is ever judged by the codex rules."""
        if not (isinstance(row, dict) and row.get("transport") == self.transport):
            raise ValueError("the Codex adapter judges only a codex row's result, not %.120r" % (row,))
        if not isinstance(result, dict):
            raise ValueError("a transport result must be a mapping, not %s" % type(result).__name__)
        code = result.get("returncode")
        if isinstance(code, bool) or not isinstance(code, int):
            raise ValueError("a Codex result's returncode must be an int, not %.40r" % (code,))
        verdict = A.judge(self, model, row, result)
        verdict.cost_usd = self.cost(result)
        return verdict

    def cost(self, result, usage=None):
        """The result's own cost_usd through the contract's normalize_cost, or NOT_MEASURED when it carries none. The
        answer on stdout and the progress on stderr are never read for a figure: codex prints no bill."""
        if not isinstance(result, dict):
            raise ValueError("a transport result must be a mapping, not %s" % type(result).__name__)
        return A.normalize_cost(result.get("cost_usd"), usage)
