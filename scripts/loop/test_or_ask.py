"""What the OpenRouter bridge must keep true when a model hangs or answers
with nothing. Driven in process with urlopen and the keychain replaced, so no
key is read and no request leaves the machine.

  python3 ~/.claude/bin/test_or_ask.py
  OR_ASK_UNDER_TEST=/path/to/other/or_ask.py python3 ~/.claude/bin/test_or_ask.py
"""
import contextlib, importlib.util, io, json, os, sys, unittest, urllib.error, urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.environ.get("OR_ASK_UNDER_TEST") or os.path.join(HERE, "or_ask.py")
#: run() swaps the process-wide urlopen for a Fake; keep the real one to restore.
REAL_URLOPEN = urllib.request.urlopen
#: What this suite never inherits: the run's own choices (loop_switches.RUN_KNOBS, the one list the landing suites drop)
#: and the bridge's own environment inputs. FX-31.2 round 0 (2026-10-03): the grader ran this suite under a Claude only
#: run's BROTHER_TRANSPORTS=claude, so every case that drives the bridge refused with exit 46 before its fake was asked.
BRIDGE_INPUTS = ("BROTHER_EFFORT_FLOOR", "BROTHER_PROOF_PHASE", "BROTHER_PROOF_BASELINE", "BROTHER_PROOF_BASELINE_SHA256",
                 "BROTHER_DISPATCH_RESERVATION")
_INHERITED = {}


def setUpModule():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import loop_switches
    for name in sorted(loop_switches.RUN_KNOBS) + list(BRIDGE_INPUTS):
        if name in os.environ:
            _INHERITED[name] = os.environ.pop(name)


def tearDownModule():
    os.environ.update(_INHERITED)
    _INHERITED.clear()


def load():
    spec = importlib.util.spec_from_file_location("or_ask_under_test", TARGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.read_key = lambda account=None: "\x73k-or-v1-test-not-a-real-key"
    return mod


def answer(model, content, finish="stop"):
    return {"model": model, "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "choices": [{"message": {"content": content}, "finish_reason": finish}]}


class Fake(object):
    """Plays urlopen: one scripted outcome per request, every request recorded."""

    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        self.calls.append({"model": body["model"], "max": body["max_tokens"],
                           "timeout": timeout, "usage": body.get("usage")})
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        data = json.dumps(out).encode("utf-8")

        class Resp(object):
            def read(self_inner):
                return data

        return contextlib.nullcontext(Resp())


def run(mod, fake, argv):
    mod.urllib.request.urlopen = fake
    out, err = io.StringIO(), io.StringIO()
    old = sys.argv
    sys.argv = ["or_ask.py"] + argv + ["what", "is", "two", "plus", "two"]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main()
    finally:
        sys.argv = old
        mod.urllib.request.urlopen = REAL_URLOPEN
    return code, out.getvalue(), err.getvalue()


class TheRegistryIsTheOneSourceOfBridgeNames(unittest.TestCase):
    """FX-31.6: MODEL_ALIASES is a projection of the registry's bridge rows, read when a name must be resolved. A
    registry the router refuses names no model: the plan refuses, the call exits 46 with REFUSED, nothing is sent."""

    def test_an_unreadable_registry_refuses_the_call_with_exit_46_and_sends_nothing(self):
        import tempfile
        mod = load()
        corrupt = os.path.join(tempfile.mkdtemp(prefix="or-ask-registry-"), "registry.json")
        with open(corrupt, "w", encoding="utf-8") as f:
            f.write("{not json")
        R = mod._router()
        saved, saved_env = R.REGISTRY, os.environ.get("BROTHER_MODEL_REGISTRY")
        R.REGISTRY, os.environ["BROTHER_MODEL_REGISTRY"] = None, corrupt
        fake = Fake([answer("deepseek/deepseek-v4.1-flash", "4")])
        try:
            with self.assertRaises(mod.RegistryUnreadable):
                mod.attempt_plan("deepseek", 100, "low")
            code, _out, err = run(mod, fake, ["--model", "deepseek"])
        finally:
            R.REGISTRY = saved
            if saved_env is None:
                os.environ.pop("BROTHER_MODEL_REGISTRY", None)
            else:
                os.environ["BROTHER_MODEL_REGISTRY"] = saved_env
        self.assertEqual(code, 46, err)
        self.assertIn("REFUSED", err)
        self.assertIn("not valid JSON", err)
        self.assertEqual(fake.calls, [])


class AModelThatDoesNotAnswer(unittest.TestCase):
    def test_model_aliases_resolve_to_catalog_ids(self):
        mod = load()
        for alias, model in (("muse", "meta/muse-spark-1.3-contributor"),
                             ("deepseek", "deepseek/deepseek-v4.1-flash")):
            fake = Fake([answer(model, "4")])
            code, _out, err = run(mod, fake, ["--model", alias])
            self.assertEqual(code, 0, err)
            self.assertEqual(fake.calls[0]["model"], model)

    def test_a_network_failure_moves_to_the_next_model(self):
        mod = load()
        fake = Fake([urllib.error.URLError("timed out"), answer("second", "4")])
        code, out, err = run(mod, fake, [])
        self.assertEqual(code, 0, err)
        self.assertIn("4", out)
        self.assertEqual(len(fake.calls), 2, "the fallback chain was never tried")

    def test_a_dropped_chunked_response_moves_to_the_next_model(self):
        # 2026-09-12: http.client.IncompleteRead is an HTTPException, not an
        # OSError, so it escaped the fallback and killed a build lane attempt.
        import http.client
        mod = load()
        fake = Fake([http.client.IncompleteRead(b"partial"), answer("second", "4")])
        code, out, err = run(mod, fake, [])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(fake.calls), 2, "the fallback chain was never tried")

    def test_a_socket_timeout_moves_to_the_next_model(self):
        mod = load()
        fake = Fake([TimeoutError("read timed out"), answer("second", "4")])
        code, out, err = run(mod, fake, [])
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.calls[1]["model"], mod.FALLBACK_MODELS[0])

    def test_the_wait_per_model_is_bounded_and_adjustable(self):
        mod = load()
        fake = Fake([answer("m", "4")])
        run(mod, fake, [])
        # the stream keeps SETTLE_S (20 s) of the call's wall back to settle a lost attempt (2026-09-27)
        self.assertEqual(fake.calls[0]["timeout"], 300 - mod.SETTLE_S)
        fake = Fake([answer("m", "4")])
        run(mod, fake, ["--timeout", "45"])
        self.assertEqual(fake.calls[0]["timeout"], 45 - mod.SETTLE_S)


class ACallSharesOneWallAndTheCallerDecidesSubstitutes(unittest.TestCase):
    """2026-09-27: every caller kills the bridge at the --timeout it passed, but each attempt got that whole timeout,
    so one call could run four timeouts inside a caller waiting one (501 loop calls ended STALLED_AFTER_DEADLINE); and
    the dispatcher refuses any substitute answer while the bridge tried one after every failure (140 thrown away)."""

    def clocked(self, mod, spend):
        """A Fake whose every request advances a fake monotonic clock by `spend` seconds, then fails."""
        clock = [1000.0]
        fake = Fake([TimeoutError("read timed out")] * 4)
        real_call = fake.__call__
        def call(req, timeout=None):
            clock[0] += spend
            return real_call(req, timeout)
        return clock, call, fake

    def test_a_second_attempt_gets_only_what_is_left_of_the_call(self):
        mod = load()
        clock, call, fake = self.clocked(mod, 200)
        with mock.patch.object(mod.time, "monotonic", lambda: clock[0]):
            code, _out, err = run(mod, call, ["--timeout", "300"])
        self.assertEqual(code, 44, err)
        self.assertEqual([c["timeout"] for c in fake.calls], [300 - mod.SETTLE_S, 100 - mod.SETTLE_S], err)

    def test_no_attempt_starts_without_time_to_finish(self):
        mod = load()
        clock, call, fake = self.clocked(mod, 290)
        with mock.patch.object(mod.time, "monotonic", lambda: clock[0]):
            code, _out, err = run(mod, call, ["--timeout", "300"])
        self.assertEqual(code, 44, err)
        self.assertEqual(len(fake.calls), 1, err)
        self.assertIn("not enough to start", err)

    def test_only_model_never_asks_a_substitute(self):
        mod = load()
        fake = Fake([urllib.error.URLError("timed out"), answer("second", "4")])
        code, _out, err = run(mod, fake, ["--model", "deepseek", "--only-model"])
        self.assertEqual(code, 44, err)
        self.assertEqual([c["model"] for c in fake.calls], ["deepseek/deepseek-v4.1-flash"], err)
        self.assertEqual({m for m, _ in mod.attempt_plan("deepseek", 1000, "low", only_model=True)}, {"deepseek/deepseek-v4.1-flash"})


class Sse(object):
    """Plays urlopen for streamed chat calls and GET /generation: one scripted (body, headers) per request, every
    request recorded. A list body is the stream's lines; a dict body is a JSON reply."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, req, timeout=None):
        self.calls.append({"url": req.full_url, "method": req.get_method(), "timeout": timeout,
                           "body": json.loads(req.data.decode("utf-8")) if req.data else None})
        out = self.replies.pop(0)
        if isinstance(out, BaseException):
            raise out
        body, headers = out

        class Resp(object):
            def __init__(self_inner):
                self_inner.headers = headers
            def read(self_inner):
                return json.dumps(body).encode("utf-8")
            def __iter__(self_inner):
                return iter(body)

        return contextlib.nullcontext(Resp())


def sse(*chunks, done=True):
    return ([b": OPENROUTER PROCESSING\n"] + [("data: " + json.dumps(c) + "\n").encode("utf-8") for c in chunks]
            + ([b"data: [DONE]\n"] if done else []))


def streamed(gid):
    return {"Content-Type": "text/event-stream", "X-Generation-Id": gid}


def refused(code):
    return urllib.error.HTTPError("https://openrouter.ai/api/v1/generation", code, "refused", {}, io.BytesIO(b"{}"))


class ALostCallIsSettledFromTheProvidersRecord(unittest.TestCase):
    """2026-09-27, for the proof pair: every call's cost must be known from the provider. A call streams, names its
    generation the moment the headers arrive, and a lost attempt is settled by GET /generation."""
    DS = "deepseek/deepseek-v4.1-flash"

    def test_a_stream_is_assembled_and_its_generation_named(self):
        mod = load()
        fake = Sse([(sse({"id": "gen-1", "model": self.DS, "choices": [{"delta": {"content": "4"}}]},
                         {"id": "gen-1", "choices": [{"delta": {"content": ""}, "finish_reason": "stop"}], "usage": {"cost": 0.001}}),
                     streamed("gen-1"))])
        code, out, err = run(mod, fake, ["--model", "deepseek", "--only-model"])
        self.assertEqual(code, 0, err)
        self.assertIn("4", out)
        self.assertIn("[attempt] 1 model=" + self.DS, err)
        self.assertIn("[generation] id=gen-1 attempt=1", err)
        self.assertIn("[billed] usd=0.001000 attempts=1 known=yes", err)
        self.assertIs(fake.calls[0]["body"]["stream"], True, "the chat call must stream, or its headers come only at the end")

    def test_a_cut_stream_is_settled_from_the_providers_record(self):
        mod = load()
        fake = Sse([(sse({"id": "gen-2", "model": self.DS, "choices": [{"delta": {"content": "par"}}]}, done=False), streamed("gen-2")),
                    ({"data": {"id": "gen-2", "total_cost": 0.0042, "cancelled": True}}, {})])
        code, _out, err = run(mod, fake, ["--model", "deepseek", "--only-model"])
        self.assertEqual(code, 44, err)
        self.assertEqual(fake.calls[1]["method"], "GET")
        self.assertIn("/generation?id=gen-2", fake.calls[1]["url"])
        self.assertIn("[billed] usd=0.004200 attempts=1 known=yes", err)

    def test_without_the_providers_record_the_cost_stays_unknown(self):
        mod = load()
        fake = Sse([(sse({"id": "gen-3", "model": self.DS, "choices": [{"delta": {"content": "p"}}]}, done=False), streamed("gen-3")),
                    refused(403)])
        code, _out, err = run(mod, fake, ["--model", "deepseek", "--only-model"])
        self.assertEqual(code, 44, err)
        self.assertIn("known=no", err)

    def test_an_error_event_is_a_failed_attempt_never_an_answer(self):
        mod = load()
        fake = Sse([(sse({"id": "gen-4", "model": self.DS, "error": {"message": "Provider disconnected"},
                          "choices": [{"delta": {"content": ""}, "finish_reason": "error"}], "usage": {"cost": 0.0}}), streamed("gen-4"))])
        code, out, err = run(mod, fake, ["--model", "deepseek", "--only-model"])
        self.assertEqual(code, 44, err)
        self.assertEqual(out, "")

    def test_settle_mode_reads_one_generation(self):
        mod = load()
        code, _out, err = run(mod, Sse([({"data": {"total_cost": 0.01}}, {})]), ["--settle", "gen-5"])
        self.assertEqual((code, "[billed] usd=0.010000 attempts=1 known=yes" in err), (0, True), err)
        code, _out, err = run(mod, Sse([refused(403)]), ["--settle", "gen-6"])
        self.assertEqual((code, "known=no" in err), (44, True), err)
        code, _out, err = run(mod, Sse([]), ["--settle", "not a generation id"])
        self.assertEqual((code, "known=no" in err), (44, True), err)


class ARunCanLowerTheEffortFloorForItsAB(unittest.TestCase):
    """Owner 2026-09-27, "A/B effort": BROTHER_EFFORT_FLOOR lowers the xhigh floor for one run; unset or unknown keeps it."""

    def effort_sent(self, floor, asked):
        env = {} if floor is None else {"BROTHER_EFFORT_FLOOR": floor}
        with mock.patch.dict(os.environ, env, clear=False):
            if floor is None:
                os.environ.pop("BROTHER_EFFORT_FLOOR", None)
            mod = load()
            fake = Sse([(sse({"id": "gen-e", "model": "m", "choices": [{"delta": {"content": "4"}}]},
                             {"id": "gen-e", "choices": [{"delta": {"content": ""}, "finish_reason": "stop"}], "usage": {"cost": 0.0}}),
                         streamed("gen-e"))])
            run(mod, fake, ["--effort", asked, "--only-model"])
        return fake.calls[0]["body"]["reasoning"]["effort"]

    def test_the_floor_holds_by_default_and_a_run_can_lower_it(self):
        self.assertEqual(self.effort_sent(None, "high"), "xhigh")
        self.assertEqual(self.effort_sent("high", "high"), "high")
        self.assertEqual(self.effort_sent("high", "xhigh"), "xhigh")
        self.assertEqual(self.effort_sent("turbo", "high"), "xhigh")


class AnAnsweringModelDifferentFromTheRequestedOneIsLabeled(unittest.TestCase):
    """2026-09-19: caught twice the same night this test was written --
    a prompt phrased as a direct address to one model got answered in that
    model's own voice ("my 8.5") by a completely different model that
    actually responded, and the only place the real responder was named was
    the trailing [usage] line on stderr, easy to miss reading a long real
    answer. stdout itself must say so, unmissably, whenever this happens."""

    def test_same_model_answering_prints_no_provenance_line(self):
        mod = load()
        fake = Fake([answer("deepseek/deepseek-v4.1-flash", "the answer")])
        code, out, err = run(mod, fake, ["--model", "deepseek"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("PROVENANCE", out)

    def test_a_different_model_answering_prints_a_provenance_line_naming_both(self):
        mod = load()
        # Requested deepseek; the network failure moves to whatever
        # FALLBACK_MODELS[0] actually is -- a different model answering.
        fake = Fake([urllib.error.URLError("timed out"),
                     answer("nvidia/nemotron-3-ultra-550b-a55b:free", "the answer")])
        code, out, err = run(mod, fake, ["--model", "deepseek"])
        self.assertEqual(code, 0, err)
        self.assertIn("PROVENANCE", out)
        self.assertIn("deepseek/deepseek-v4.1-flash", out)
        self.assertIn("nvidia/nemotron-3-ultra-550b-a55b:free", out)
        # The provenance line must appear BEFORE the actual content, never
        # buried after it where a reader skimming the top would miss it.
        self.assertLess(out.index("PROVENANCE"), out.index("the answer"))


class TrickleServer(object):
    """A real local HTTP server whose reply dribbles one keepalive byte every
    half second for `seconds`, then a complete answer. urlopen's own timeout
    bounds each read, so this defeats it; only a wall-clock deadline holds."""

    def __init__(self, seconds):
        import http.server, threading, time

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(h):
                h.rfile.read(int(h.headers["Content-Length"]))
                h.send_response(200)
                h.send_header("Content-Type", "application/json")
                h.end_headers()
                end = time.time() + seconds
                try:
                    while time.time() < end:
                        h.wfile.write(b" ")
                        h.wfile.flush()
                        time.sleep(0.5)
                    h.wfile.write(json.dumps(answer("slow", "4")).encode("utf-8"))
                except OSError:  # sbe: allow-silent the client gave up mid body, which is exactly what this slow server exists to provoke
                    pass

            def log_message(h, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = "http://127.0.0.1:%d/" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TheTimeoutIsAWallClockDeadline(unittest.TestCase):
    """2026-09-18: --timeout 300 on a deepseek xhigh call ran about 20 minutes
    until SIGTERM, with nothing on stderr. --timeout reached urlopen only as a
    per-read socket timeout, and the test above only checked that number."""

    def test_a_trickling_reply_is_cut_at_the_calls_timeout(self):
        import time
        mod = load()
        srv = trickle_server(8)
        mod.ENDPOINT = srv.url
        try:
            t0 = time.time()
            code, out, err = run(mod, REAL_URLOPEN, ["--timeout", "2"])
            elapsed = time.time() - t0
        finally:
            srv.close()
        # ONE deadline for the whole call (2026-09-27): the next model is not started once the call's 2 s are spent
        self.assertLess(elapsed, 2 + 2, "ran %.1fs past the call's 2s deadline" % elapsed)
        self.assertEqual(code, 44, err)
        self.assertEqual(out, "")
        self.assertIn("no answer from", err)

    def test_a_trickling_decision_is_cut_at_the_timeout(self):
        import time
        mod = load()
        srv = trickle_server(8)
        mod.DECISIONS_ENDPOINT = srv.url
        try:
            t0 = time.time()
            code, _out, err = run_decision(mod, REAL_URLOPEN, NOUL_Q,
                                           argv=("--model", "typesafe", "--timeout", "2"))
            elapsed = time.time() - t0
        finally:
            srv.close()
        self.assertLess(elapsed, 4, "ran %.1fs past a 2s deadline" % elapsed)
        self.assertEqual(code, 45, err)

    def test_a_hung_keychain_read_is_no_data_not_a_hang(self):
        import subprocess
        spec = importlib.util.spec_from_file_location("or_ask_keychain", TARGET)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def hang(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))

        mod.subprocess.run = hang
        try:
            self.assertIsNone(mod.read_key())
        finally:
            mod.subprocess.run = subprocess.run

    def test_a_kill_signal_leaves_a_diagnostic_and_a_nonzero_exit(self):
        import signal, subprocess, time
        srv = trickle_server(30)
        env = dict(os.environ, OR_ASK_TEST_ENDPOINT=srv.url)
        shim = ("import importlib.util,sys;"
                "s=importlib.util.spec_from_file_location('m',%r);"
                "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                "m.read_key=lambda account=None:'sk-or-v1-test';"
                "import os;m.ENDPOINT=os.environ['OR_ASK_TEST_ENDPOINT'];"
                "sys.argv=['or_ask.py','--timeout','60','hi'];sys.exit(m.main())" % TARGET)
        p = subprocess.Popen([sys.executable, "-c", shim], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(1.5)
            p.send_signal(signal.SIGTERM)
            out, err = p.communicate(timeout=10)
        finally:
            srv.close()
        self.assertEqual(p.returncode, 128 + signal.SIGTERM, err)
        self.assertEqual(out, "")
        self.assertIn("NO-DATA", err)
        self.assertIn("SIGTERM", err)


class AnEmptyAnswerIsNeverASuccess(unittest.TestCase):
    def test_json_mode_does_not_stop_on_an_empty_answer(self):
        mod = load()
        fake = Fake([answer("first", ""), answer("second", "4")])
        code, out, err = run(mod, fake, ["--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["model"], "second")

    def test_every_model_empty_is_no_data_in_both_modes(self):
        for argv in ([], ["--json"]):
            mod = load()
            n = 1 + len(mod.FALLBACK_MODELS)
            fake = Fake([answer("m%d" % i, "   ") for i in range(n)])
            code, out, err = run(mod, fake, argv)
            self.assertEqual(code, 44, "argv=%r exited %r: %s" % (argv, code, err))
            self.assertIn("NO-DATA", err)

    def test_a_spent_reasoning_budget_retries_once_with_double_the_budget(self):
        mod = load()
        fake = Fake([answer("first", "", finish="length"), answer("first", "4")])
        # every call runs at xhigh (owner 2026-09-26), so the doubling starts above the xhigh floor
        code, out, err = run(mod, fake, ["--max", "40000"])
        self.assertEqual(code, 0, err)
        default_id = mod.bridge_aliases()[mod.DEFAULT_MODEL]   # the default is a registry name; the wire carries its id
        self.assertEqual([c["model"] for c in fake.calls], [default_id, default_id])
        self.assertEqual([c["max"] for c in fake.calls], [40000, 80000])

    def test_a_low_max_is_raised_to_the_effort_floor(self):
        # 2026-09-13: --effort medium --max 450 emptied out on real muse and
        # deepseek calls even after the auto-double to 900; a floor stops the
        # bridge from making that same doomed attempt.
        mod = load()
        fake = Fake([answer("first", "4")])
        # medium is lifted to xhigh (owner 2026-09-26), so the xhigh floor applies
        code, _out, err = run(mod, fake, ["--effort", "medium", "--max", "450"])
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.calls[0]["max"], mod.EFFORT_MAX_FLOOR["xhigh"])
        self.assertIn("NOTE", err)

    def test_a_max_already_above_the_floor_is_left_alone(self):
        mod = load()
        fake = Fake([answer("first", "4")])
        code, _out, err = run(mod, fake, ["--effort", "xhigh", "--max", "40000"])
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.calls[0]["max"], 40000)
        self.assertNotIn("NOTE", err)

    def test_no_effort_means_xhigh_and_its_floor(self):
        # CONTRACT CHANGED 2026-09-26 by owner order ("For all openrouter calls set them to xhigh for all models"):
        # no effort named used to mean no floor; it now means xhigh, so the xhigh floor applies to a low --max.
        mod = load()
        fake = Fake([answer("first", "4")])
        code, _out, err = run(mod, fake, ["--max", "50"])
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.calls[0]["max"], mod.EFFORT_MAX_FLOOR["xhigh"])

    def test_the_budget_is_doubled_once_and_not_forever(self):
        mod = load()
        n = 1 + len(mod.FALLBACK_MODELS)
        fake = Fake([answer("x", "", finish="length")] * (2 * n))
        code, _out, _err = run(mod, fake, ["--max", "1000"])
        self.assertEqual(code, 44)
        self.assertEqual(len(fake.calls), 2 * n, "each model: one normal try, one doubled")


class FakeDecision(object):
    """Plays urlopen for --decisions: records url and body, one outcome each."""

    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def __call__(self, req, timeout=None):
        self.calls.append({"url": req.full_url,
                           "body": json.loads(req.data.decode("utf-8"))})
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        data = json.dumps(out).encode("utf-8")

        class Resp(object):
            def read(self_inner):
                return data

        return contextlib.nullcontext(Resp())


def run_decision(mod, fake, stdin_obj, argv=("--model", "typesafe"), explicit=True):
    mod.urllib.request.urlopen = fake
    out, err = io.StringIO(), io.StringIO()
    old_argv, old_in = sys.argv, sys.stdin
    sys.argv = ["or_ask.py"] + (["--decisions"] if explicit else []) + list(argv)
    sys.stdin = io.StringIO(stdin_obj if isinstance(stdin_obj, str) else json.dumps(stdin_obj))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main()
    finally:
        sys.argv, sys.stdin = old_argv, old_in
        mod.urllib.request.urlopen = REAL_URLOPEN
    return code, out.getvalue(), err.getvalue()


NOUL_Q = {"state": {"text": "7 + 5 = 12"},
          "questions": {"correct": {"type": "noul", "instructions": "Is the sum right?"}}}


class ADecisionModelIsNeverAFallbackChatAnswer(unittest.TestCase):
    """--decisions (2026-09-18): TypeSafe's Jev only answers on the decisions
    endpoint; the chat path quietly answered as a free fallback model instead.
    A decision must come from the requested model or not at all."""

    def test_request_goes_to_the_decisions_endpoint_with_the_resolved_model(self):
        mod = load()
        fake = FakeDecision([{"model": "typesafe/jev-1.13",
                              "answers": {"correct": {"type": "noul", "noul": 0.97}},
                              "usage": {"input_tokens": 9, "output_tokens": 2}}])
        code, out, err = run_decision(mod, fake, NOUL_Q)
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls[0]["url"], mod.DECISIONS_ENDPOINT)
        self.assertEqual(fake.calls[0]["body"]["model"], "typesafe/jev-1.13")
        self.assertIn("correct=0.97", err)
        self.assertIn("[usage] input=9 output=2", err)

    def test_criteria_values_are_sent_as_strings(self):
        mod = load()
        q = {"state": "s", "questions": {"route": {
            "type": "choice", "instructions": "Pick one",
            "criteria": {"a": {"why": "structured"}, "b": "plain"}}}}
        fake = FakeDecision([{"answers": {"route": {"type": "choice", "choice": "a"}}}])
        code, _o, _e = run_decision(mod, fake, q)
        self.assertEqual(code, 0)
        sent = fake.calls[0]["body"]["questions"]["route"]["criteria"]
        self.assertEqual(sent, {"a": json.dumps({"why": "structured"}), "b": "plain"})

    def test_a_choice_without_criteria_is_refused_before_any_request(self):
        mod = load()
        fake = FakeDecision([])
        q = {"state": "s", "questions": {"route": {"type": "choice", "instructions": "Pick"}}}
        code, _o, err = run_decision(mod, fake, q)
        self.assertEqual(code, 45)
        self.assertEqual(fake.calls, [])
        self.assertIn("needs criteria", err)

    def test_an_http_error_exits_45_and_never_tries_another_model(self):
        mod = load()
        fake = FakeDecision([urllib.error.HTTPError(
            "u", 400, "bad", {}, io.BytesIO(b'{"error":"nope"}'))])
        code, out, _err = run_decision(mod, fake, NOUL_Q)
        self.assertEqual(code, 45)
        self.assertEqual(len(fake.calls), 1, "a decision never falls back")
        self.assertEqual(out, "")

    def test_a_missing_answer_for_a_question_is_no_data(self):
        mod = load()
        fake = FakeDecision([{"answers": {}}])
        code, _o, err = run_decision(mod, fake, NOUL_Q)
        self.assertEqual(code, 45)
        self.assertIn("answered no decision for correct", err)

    def test_input_that_is_not_json_is_no_data(self):
        mod = load()
        code, _o, err = run_decision(mod, FakeDecision([]), "not json")
        self.assertEqual(code, 45)
        self.assertIn("needs a JSON object", err)

    def test_typesafe_and_jev_aliases_resolve(self):
        mod = load()
        self.assertEqual(mod.MODEL_ALIASES["typesafe"], "typesafe/jev-1.13")
        self.assertEqual(mod.MODEL_ALIASES["jev"], "typesafe/jev-1.13")

    def test_a_json_decision_auto_routes_without_the_flag(self):
        mod = load()
        fake = FakeDecision([{"model": "typesafe/jev-1.13",
                              "answers": {"correct": {"type": "noul", "noul": 1.0}}}])
        code, _out, err = run_decision(mod, fake, NOUL_Q, explicit=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.calls[0]["url"], mod.DECISIONS_ENDPOINT)

    def test_a_plain_jev_prompt_is_refused_without_a_request(self):
        mod = load()
        fake = FakeDecision([])
        code, _out, err = run_decision(mod, fake, "is this safe?",
                                       explicit=False)
        self.assertEqual(code, 45)
        self.assertEqual(fake.calls, [])
        self.assertIn("typed decision model", err)



def billed(model, content, cost, finish="stop"):
    a = answer(model, content, finish); a["usage"]["cost"] = cost; return a


class TheBilledCharge(unittest.TestCase):
    """A/B/C test 2026-09-23: the account meter read 9.21 USD while the ledger booked 2.22, because drained, retried
    and fallen back attempts are billed and only the final attempt's tokens were reported."""
    def test_every_attempt_asks_openrouter_for_its_charge(self):
        fake = Fake([billed("m", "4", 0.01)]); run(load(), fake, [])
        self.assertEqual(fake.calls[0]["usage"], {"include": True})

    def test_a_drained_attempt_and_its_retry_are_both_billed(self):
        fake = Fake([billed("m", "", 0.03, finish="length"), billed("m", "4", 0.05)])
        code, _o, err = run(load(), fake, [])
        self.assertEqual(code, 0, err); self.assertIn("[billed] usd=0.080000 attempts=2 known=yes", err)

    def test_an_attempt_without_a_figure_makes_the_sum_a_floor(self):
        fake = Fake([answer("m", "", finish="length"), billed("m", "4", 0.05)])
        _c, _o, err = run(load(), fake, [])
        self.assertIn("[billed] usd=0.050000 attempts=2 known=no", err)

    def test_a_call_where_no_model_answered_still_prints_its_bill(self):
        mod = load(); n = 1 + len([m for m in mod.FALLBACK_MODELS])
        fake = Fake([billed("m", "", 0.02, finish="stop")] * (n + 2))
        code, _o, err = run(mod, fake, [])
        self.assertEqual(code, 44); self.assertRegex(err, r"\[billed\] usd=0\.0[0-9]+ attempts=\d+ known=yes")

    def test_the_ledger_takes_a_complete_billed_charge(self):
        import types, sys as _s
        _s.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
        from plugin.runtime.brother.core import openrouter_dispatch as D
        r = types.SimpleNamespace(stderr="[usage] prompt=1 completion=1 model=x\n[billed] usd=0.123400 attempts=3 known=yes")
        self.assertEqual(D.measured_cost(r, "/nonexistent")[0], 0.1234)
        r2 = types.SimpleNamespace(stderr="[billed] usd=0.123400 attempts=3 known=no")
        self.assertIsNone(D.measured_cost(r2, "/nonexistent")[0])   # a floor is not the charge: falls to the token path, here unmeasured

PROOF = {"BROTHER_PROOF_PHASE": "RB", "BROTHER_PROOF_BASELINE": "/fixture/baseline.json",
         "BROTHER_PROOF_BASELINE_SHA256": "0" * 64}


class AProofPhaseCallSpendsOnlyThroughTheDispatcher(unittest.TestCase):
    """Objection 5 and B5-21: in a proof phase the bridge answers only a call the dispatcher reserved, so a checker,
    model_call's bridge route or jev_decide cannot spend outside the ledger. Each refusal happens before the key is
    read and before any request. Each case changes ONE thing from test_a_reserved_call_with_a_real_ceiling_is_made."""

    def call(self, argv, **env):
        mod, keys = load(), []
        mod.read_key = lambda account=None: keys.append(account) or "\x73k-or-v1-test-not-a-real-key"
        fake = Fake([answer(mod.bridge_aliases()[mod.DEFAULT_MODEL], "4")])
        with mock.patch.dict(os.environ, dict(PROOF, **env)):
            if "BROTHER_DISPATCH_RESERVATION" not in env:
                os.environ.pop("BROTHER_DISPATCH_RESERVATION", None)
            code, out, err = run(mod, fake, argv)
        return code, err, keys, fake.calls

    def test_a_reserved_call_with_a_real_ceiling_is_made(self):
        code, err, keys, calls = self.call(["--max", "40000"], BROTHER_DISPATCH_RESERVATION="holder-1-2-abc")
        self.assertEqual(code, 0, err); self.assertEqual(len(keys), 1); self.assertEqual(len(calls), 1)

    def test_without_a_reservation_it_exits_44_before_the_key_is_read(self):
        code, err, keys, calls = self.call(["--max", "40000"])
        self.assertEqual(code, 44, err); self.assertEqual(keys, []); self.assertEqual(calls, [])
        self.assertIn("reservation", err)

    def test_a_ceiling_below_the_effort_floor_is_refused_not_raised(self):
        code, err, keys, calls = self.call(["--max", "1000"], BROTHER_DISPATCH_RESERVATION="holder-1-2-abc")
        self.assertEqual(code, 44, err); self.assertEqual(keys, []); self.assertEqual(calls, [])


class ADecisionBodyThatIsNotAnObjectIsNoData(unittest.TestCase):
    """--decisions reads JSON on stdin; valid JSON that is not an object (a bare number, a list) is NO-DATA exit 45
    before any request, never a traceback."""

    def test_a_number_or_a_list_is_refused_without_a_request(self):
        for body in ("5", "[\"state\"]"):
            mod = load(); fake = Fake([])
            code, _out, err = run_decision(mod, fake, body)
            self.assertEqual(code, 45, "%s: %s" % (body, err)); self.assertEqual(fake.calls, [])
            self.assertIn("NO-DATA", err)


class TheAttemptPlanIsTheLoop(unittest.TestCase):
    """Objection 11: the dispatcher prices the bridge's worst case from or_ask.attempt_plan, and the chat loop walks
    the same plan, so the priced attempts and the attempts made cannot diverge."""

    def test_the_plan_is_every_model_at_max_then_doubled_after_the_raise(self):
        mod = load()
        ds, fb = mod.MODEL_ALIASES["deepseek"], mod.FALLBACK_MODELS[0]
        floor = mod.EFFORT_MAX_FLOOR["xhigh"]
        self.assertEqual(mod.attempt_plan("deepseek", 1000, "low"),
                         [(ds, floor), (ds, 2 * floor), (fb, floor), (fb, 2 * floor)])

    def test_the_loop_tries_exactly_the_plan_when_every_answer_runs_out_of_budget(self):
        mod = load()
        n = 1 + len(mod.FALLBACK_MODELS)
        fake = Fake([answer("x", "", finish="length")] * (2 * n))
        code, _out, err = run(mod, fake, ["--model", "deepseek", "--max", "40000"])
        self.assertEqual(code, 44, err)
        self.assertEqual([(c["model"], c["max"]) for c in fake.calls], mod.attempt_plan("deepseek", 40000, None))

    def test_a_non_length_empty_answer_skips_that_models_doubled_attempt(self):
        mod = load()
        fake = Fake([answer("x", "", finish="stop"), answer("y", "4")])
        code, _out, err = run(mod, fake, ["--model", "deepseek", "--max", "40000"])
        self.assertEqual(code, 0, err)
        self.assertEqual([c["max"] for c in fake.calls], [40000, 40000])
        self.assertEqual([c["model"] for c in fake.calls], [mod.MODEL_ALIASES["deepseek"], mod.FALLBACK_MODELS[0]])


def trickle_server(seconds):
    """A TrickleServer, or this case skipped by name where the sandbox denies a loopback socket (sandbox.sb: deny
    network*). A case that needs a real server is never passed without one (FX-31.2 round 0, 2026-10-03)."""
    try:
        return TrickleServer(seconds)
    except PermissionError as exc:
        raise unittest.SkipTest("NO-DATA: this sandbox denies a loopback socket (%s)" % exc)


BRIDGE_ROW = {"id": "deepseek/deepseek-v4.1-flash", "transport": "bridge", "privacy": "public"}


def bridge_adapter_module():
    """scripts/loop/adapters/bridge.py, imported the way the loop names it: adapters.bridge, scripts/loop on the path."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from adapters import bridge
    return bridge


class TheBridgeAdapterKeepsTheBridgesCommandLine(unittest.TestCase):
    """FX-31.2: model_call's bridge argv and verdict moved behind adapters.bridge.BridgeAdapter. R-FX-31-1: an unknown or
    malformed call is refused before any process starts; R-FX-31-3: a failure, a refusal or an empty answer is never an
    answer. The argv is fed to this bridge's own parser and main, so the adapter and the bridge cannot drift apart."""

    def setUp(self):
        self.B = bridge_adapter_module()
        self.adapter = self.B.BridgeAdapter(TARGET, env={})

    def test_the_argv_is_model_calls_bridge_line(self):
        argv, stdin, extra = self.adapter.argv("deepseek", "what is 7 plus 5", 300, BRIDGE_ROW)
        self.assertEqual(argv, [sys.executable, TARGET, "--model", "deepseek", "--effort", "xhigh", "--max", "384000",
                                "--timeout", "300", "--", "what is 7 plus 5"])
        self.assertEqual((stdin, extra), ("", None))
        ceilings = {name: self.adapter.argv(name, "p", 60, BRIDGE_ROW)[0][7] for name in ("muse", "jev", "other/model")}
        self.assertEqual(ceilings, {"muse": "943718", "jev": "32000", "other/model": "64000"})
        lifted = self.B.BridgeAdapter(TARGET, env={"BROTHER_BRIDGE_EFFORT": " XHIGH "})
        self.assertEqual(lifted.argv("deepseek", "p", 1, BRIDGE_ROW)[0][4:6], ["--effort", "xhigh"])

    def test_the_ceilings_are_model_calls_own(self):
        import ast
        with open(os.path.join(HERE, "model_call.py"), encoding="utf-8") as fh:
            src = fh.read()
        found = [ast.literal_eval(node.value) for node in ast.parse(src).body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "BRIDGE_MAX" for t in node.targets)]
        self.assertEqual(found, [self.B.BRIDGE_MAX])
        self.assertIn("BRIDGE_MAX.get(name, %d)" % self.B.DEFAULT_MAX, src)

    def test_a_prompt_that_looks_like_a_flag_reaches_the_bridge_as_the_prompt(self):
        mod = load()
        for prompt in ("--json", "--settle=gen-abc", "--only-model", "-h"):
            argv, _stdin, _ = self.adapter.argv("deepseek", prompt, 300, BRIDGE_ROW)
            a = mod.build_parser().parse_args(argv[2:])
            self.assertEqual((a.prompt, a.json, a.settle, a.only_model), ([prompt], False, None, False), prompt)
            self.assertEqual((a.model, a.effort, a.max, a.timeout), ("deepseek", "xhigh", 384000, 300), prompt)

    def test_the_bridge_sends_the_call_as_built_and_the_adapter_judges_its_output(self):
        mod = load()
        argv, _stdin, _ = self.adapter.argv("deepseek", "--json", 300, BRIDGE_ROW)
        fake = Sse([(sse({"id": "gen-a", "model": "deepseek/deepseek-v4.1-flash", "choices": [{"delta": {"content": "12"}}]},
                         {"id": "gen-a", "choices": [{"delta": {"content": ""}, "finish_reason": "stop"}],
                          "usage": {"cost": 0.002}}), streamed("gen-a"))])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(mod.urllib.request, "urlopen", fake), mock.patch.object(sys, "argv", argv[1:]), \
                mock.patch.object(sys, "stdin", io.StringIO("")), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = mod.main()
        self.assertEqual(code, 0, err.getvalue())
        body = fake.calls[0]["body"]
        self.assertEqual(body["messages"][-1], {"role": "user", "content": "--json"})
        self.assertEqual((body["model"], body["max_tokens"], body["reasoning"]["effort"]),
                         ("deepseek/deepseek-v4.1-flash", 384000, "xhigh"))
        self.assertEqual(fake.calls[0]["timeout"], 300 - mod.SETTLE_S)
        got = self.adapter.judge("deepseek", BRIDGE_ROW, {"returncode": code, "stdout": out.getvalue(), "stderr": err.getvalue()})
        self.assertEqual((got.ok, got.answer, got.status, got.cost_usd), (True, "12", "OK", 0.002))

    def test_an_unknown_or_malformed_call_is_refused_before_any_process(self):
        Refused = self.B.A.Refused
        cases = [("deepseek", "p", 60, row) for row in (None, [], "bridge", {1, 2}, {}, {"transport": "claude"},
                                                        {"transport": ["bridge"]})]
        cases += [(m, "p", 60, BRIDGE_ROW) for m in (None, "", 5, True, ["deepseek"], b"deepseek", "--json", "-x",
                                                     "deep seek", "a\nb", float("nan"))]
        cases += [("deepseek", p, 60, BRIDGE_ROW) for p in (None, "", "   ", 5, ["p"], b"p", "a\0b")]
        cases += [("deepseek", "p", t, BRIDGE_ROW) for t in (None, True, False, 0, -1, 2.5, float("nan"), "300", [300])]
        for model, prompt, timeout, row in cases:
            with self.assertRaises(Refused, msg=repr((model, prompt, timeout, row))):
                self.adapter.argv(model, prompt, timeout, row)
        for program in (None, "", 5, b"/x", "relative/bridge.py", os.path.join(HERE, "no-such-bridge.py"), TARGET + "\0"):
            with self.assertRaises(Refused, msg=repr(program)):
                self.B.BridgeAdapter(program, env={}).argv("deepseek", "p", 60, BRIDGE_ROW)
        for effort in ("low", "high", "max", "turbo", "  ", 5):
            with self.assertRaises(Refused, msg=repr(effort)):
                self.B.BridgeAdapter(TARGET, env={"BROTHER_BRIDGE_EFFORT": effort}).argv("deepseek", "p", 60, BRIDGE_ROW)
        for env in (["x"], "BROTHER_BRIDGE_EFFORT=xhigh", 5):
            with self.assertRaises(ValueError, msg=repr(env)):
                self.B.BridgeAdapter(TARGET, env=env)

    def test_an_empty_answer_at_exit_zero_is_a_failed_attempt(self):
        for stdout in ("", "   \n", None):
            got = self.adapter.judge("deepseek", BRIDGE_ROW, {"returncode": 0, "stdout": stdout, "stderr": ""})
            self.assertEqual((got.ok, got.answer, got.status, got.detail),
                             (False, "", "FAILED", "exit 0 with an EMPTY answer"), repr(stdout))

    def test_a_failure_or_a_refusal_is_never_an_answer(self):
        judge = lambda result: self.adapter.judge("deepseek", BRIDGE_ROW, result)
        ok = judge({"returncode": 0, "stdout": "12\n", "stderr": ""})
        self.assertEqual((ok.ok, ok.answer, ok.status, ok.detail, ok.cost_usd), (True, "12", "OK", "answered", None))
        bad = judge({"returncode": 44, "stdout": "", "stderr": "NO-DATA: no model answered (tried deepseek/x).\n"})
        self.assertEqual((bad.ok, bad.status, bad.detail),
                         (False, "FAILED", "exit 44: NO-DATA: no model answered (tried deepseek/x)."))
        refused = judge({"returncode": 0, "stdout": "I will not", "stderr": "provider refused this prompt"})
        self.assertEqual((refused.ok, refused.status), (False, "PROVIDER_REFUSED"))
        for code in (False, True, None, "0", 0.0, [0]):
            with self.assertRaises(ValueError, msg=repr(code)):
                judge({"returncode": code, "stdout": "12", "stderr": ""})
        for row in (None, {}, {"transport": "claude"}, [BRIDGE_ROW], {"transport": None}):
            with self.assertRaises(ValueError, msg=repr(row)):
                self.adapter.judge("deepseek", row, {"returncode": 0, "stdout": "12", "stderr": ""})
        for result in (None, [], "12", 0, {"returncode": 0, "stdout": b"12"}):
            with self.assertRaises(ValueError, msg=repr(result)):
                judge(result)
        with self.assertRaises(ValueError):
            self.adapter.judge(None, BRIDGE_ROW, {"returncode": 0, "stdout": "12", "stderr": ""})

    def test_the_cost_is_the_bridges_own_billed_line(self):
        mod = load()
        cost = lambda err, out="": self.adapter.cost({"returncode": 0, "stdout": out, "stderr": err})
        self.assertEqual(cost("[usage] prompt=1 completion=1 model=m\n" + mod.billed_line(0.0123, 2, True) + "\n"), 0.0123)
        zero = cost(mod.billed_line(0.0, 0, True))
        self.assertEqual((zero, type(zero)), (0.0, float), "a measured zero stays a number, never NOT_MEASURED")
        self.assertIsNone(cost(mod.billed_line(0.05, 2, False)), "known=no is a floor, never the charge")
        self.assertIsNone(cost(mod.billed_line(float("inf"), 2, True)), "an overflowed sum is no charge")
        self.assertIsNone(cost(""))
        self.assertIsNone(self.adapter.cost({"returncode": 0, "stdout": "12"}))
        self.assertIsNone(cost("", out=mod.billed_line(9.0, 1, True)), "the answer is never read for the bill")
        for err in (mod.billed_line(0.01, 1, True) + "\n" + mod.billed_line(0.01, 1, True),
                    "[billed] usd=abc attempts=1 known=yes", "[billed] usd=0.500000 attempts=0 known=yes",
                    "[billed] usd=-0.500000 attempts=1 known=yes", "[billed] usd=0.5",
                    b"[billed] usd=0.010000 attempts=1 known=yes"):
            with self.assertRaises(ValueError, msg=repr(err)):
                self.adapter.cost({"returncode": 0, "stdout": "", "stderr": err})
        for result in (None, [], "x", 5):
            with self.assertRaises(ValueError, msg=repr(result)):
                self.adapter.cost(result)
        with self.assertRaises(ValueError):
            self.adapter.cost({"stderr": ""}, usage="tokens")
        got = self.adapter.judge("deepseek", BRIDGE_ROW, {"returncode": 0, "stdout": "12", "stderr": mod.billed_line(0.002, 1, True)})
        self.assertEqual(got.cost_usd, 0.002)


if __name__ == "__main__":
    print("under test: %s" % TARGET)
    unittest.main(verbosity=2)
