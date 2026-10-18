#!/usr/bin/env python3
"""S3 (1.1.6): a seat lost mid run parks only its transport's units, says so once, and a codex role may take one.

THE GAP. runner_pool.py parked CONFIG_WAIT like EXHAUSTED and WITHHELD and had no notion of transport, so a lost Claude
login parked every unit whatever host would build it, and nothing told the owner. These cases drive runner_pool.main()
over a fixture plan with one claude role and one codex role (both hosts, U8 a): the claude seat is lost at pass 2.

  REQ-S3A-01 test_losing_claude_parks_only_claude_units   M-S3A-PARK-ALL      transport_parked answers every unit
  REQ-S3A-02 test_one_row_per_cause_and_run               M-S3A-ROW-EACH-PASS the row is written every pass
  REQ-S3A-03 test_a_fresh_proof_reseats                   (the loss clear)
  REQ-S3A-04 test_codex_takes_a_parked_unit               M-S3A-NO-HANDOVER   the codex hand over is skipped
  REQ-S3A-05 test_unreadable_roles_park_as_today          M-S3A-BLANK-RUNS    an empty transport is admitted
  S2 round 3 (MixedRound, at the real unit_runner.py): M-S2R3-MIXED-* in ~/.claude/evidence/1.1.6-s2r3/mutate.py

The pool is loaded with sys.modules stand ins for the siblings that decide nothing here (the seats suite's harness
shape, scripts/loop/test_runner_pool_seats.py); runner_pool's own parking, roles, registry, breaker and notify code
run for real. No process is started and no model is called: Popen is a recorder.
Run from the repository root: python3 -B scripts/loop/test_seat_lost_parks_once.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_PATH = os.path.join(HERE, "runner_pool.py")
sys.path.insert(0, HERE)

_SAVED = {}
_POOL = None
_STATE = {"wbs": None}

LOGIN_WAIT = ("CONFIG_WAIT round 1: every worker call was refused because the program does not know its model; no build "
              "was made and no attempt was spent; re-seated when the resolved program changes (loop_intake.py prepare proves it)")
LOGIN_ERROR = "CONFIG_WAIT: the login is rejected (401): Failed to authenticate. API Error: 401"
MODEL_ERROR = "CONFIG_WAIT: the program does not know this model: [claude-code:unrecognized_model]"
# the wired S2 holder's refusals: native_worker's seat (brother_login.ask_holder) and model_call's role session
HOLDER_ERRORS = ("CONFIG_WAIT: The run's login holder did not answer. Restart the run.",
                 "BROTHER_LOOP_TOKEN is on but the loop login cannot be used (The run's login holder refused this "
                 "request.); sign in once with python3 scripts/loop/brother_login.py")



def ended_holder_error():
    """The seat's CONFIG_WAIT when the run's named holder has died (S2 round 1, review B), from the real ask_holder
    against a closed peer: the cause runner_pool must class as a lost login, so the unit parks once and a row is
    written. A holder answer of "" (the behaviour this replaced) makes the cause empty, which classes as nothing."""
    import socket
    import brother_login as BL
    mine, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    mine.close()
    try:
        return "CONFIG_WAIT: " + BL.ask_holder({BL.HOLD_FD_VAR: str(theirs.fileno())})
    except BL.Failure as exc:
        return "CONFIG_WAIT: " + str(exc)
    finally:
        theirs.close()


ROLES = {"roles": {
    "worker": {"does": "builds", "when": "inside", "kind": "build", "content": "public", "must_be_chosen": False,
               "setting": "BROTHER_S3_WORKER", "default": "opus55"},
    "codex_worker": {"does": "builds the codex units", "when": "inside", "kind": "build", "content": "public",
                     "must_be_chosen": False, "setting": "BROTHER_S3_CODEX", "default": "luna", "builds": ["UX"]},
    "checker": {"does": "grades", "when": "inside", "kind": "grade", "content": "public", "must_be_chosen": False,
                "setting": None, "default": None}}}
REGISTRY = {"models": {
    "opus55": {"id": "claude-opus-5-5", "transport": "claude", "privacy": "private",
               "quality": {"build": 10, "grade": 9, "prose": 9, "plan": 10}, "cost": 35.0},
    "luna": {"id": "gpt-6-luna", "transport": "codex", "privacy": "public",
             "quality": {"build": 8, "grade": 8, "prose": 9, "plan": 8}, "cost": 12.0}}}


class _CP(object):
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class _FakeSubprocess(object):
    """The pool's subprocess use, replaced: run() answers the money guard 5, Popen records and starts nothing."""
    SubprocessError = Exception
    STDOUT = -2

    def __init__(self):
        self.started, self.fds = [], []

    def run(self, argv, **kw):
        return _CP("5\n" if any("burn_guard" in str(x) for x in argv) else "", "", 0)

    def Popen(self, argv, **kw):
        self.started.append((argv[-1], kw.get("env")))
        self.fds.append(kw.get("pass_fds"))
        return object()


class _FakePlanPath(object):
    def selected(self):
        return _STATE["wbs"]


class _FakePlanStore(object):
    def sub_landed(self, sub, evidence=""):
        return sub in (evidence or "").split()

    def release_refusal(self, state):
        return None

    def supply_hold(self, sub):
        return ""


class _FakeSpecCheck(object):
    def gate_refusal(self, text, sub):
        return ""


class _FakePlanLint(object):
    COUNCIL_PATH = "/nonexistent/council.json"

    def lint_mode(self):
        return ("report", "")

    def admission(self, plan, unit, sub, root, env, council=None):
        return ([], "")


class _FakeBriefCheck(object):
    SPEC_PATH = None


class _FakeProofLedger(object):
    def draining(self):
        return False


class _FakeUnitLedger(object):
    def fail_class(self, note):
        return "SUITE"

    def last_rows(self, path):
        return {}


class _FakeLoopSwitches(object):
    def native_on(self, env=None, which=None):
        return False

    def on(self, name, env=None):
        return False


def _load():
    global _POOL
    if _POOL is not None:
        return _POOL
    for name, module in (("plan_path", _FakePlanPath()), ("plan_store", _FakePlanStore()),
                         ("plan_lint", _FakePlanLint()), ("spec_check", _FakeSpecCheck()),
                         ("brief_check", _FakeBriefCheck()), ("unit_ledger", _FakeUnitLedger()),
                         ("proof_ledger", _FakeProofLedger()), ("loop_switches", _FakeLoopSwitches())):
        _SAVED[name] = sys.modules.get(name)
        sys.modules[name] = module
    spec = importlib.util.spec_from_file_location("runner_pool_under_s3", POOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.reconcile_dead = lambda d, pid: None
    module.remaining_of = lambda u: 0
    module.busy_paths = lambda alive, spec_of: ({}, [])
    module.parked_fact = lambda *a, **k: 0.0
    module.sunk = lambda rec: False
    module.reseated_too_soon = lambda word, at, now: False
    module.ranked = lambda units, rec, ip, explore, buildable: list(units)
    module.unit_record = lambda rows: {}
    module.touch_set = lambda sub, spec: set()
    module.write_set = lambda sub, spec: []
    module.reviewed_route = lambda ws: ""
    module.touch_conflict = lambda ts, busy, unknown: ""
    module.admissible = lambda plan, sub: ""
    module.finish_first_hold = lambda *a, **k: ""
    module.wip_mode = lambda value: "off"
    module.spawned = lambda proc, sub, *a, **k: ""
    _POOL = module
    return module


def tearDownModule():
    for name, module in _SAVED.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class _Pass(unittest.TestCase):
    """One fixture estate per case: a HOME, a runs folder, a run folder, the roles and the registry."""
    UNITS = (("UA", "OPEN"), ("UB", "OPEN"), ("UC", "OPEN"), ("UX", "OPEN"))

    def setUp(self):
        self.pool = _load()
        self.root = tempfile.mkdtemp(prefix="s3-seat-lost-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = os.path.join(self.root, "home")
        self.ev = os.path.join(self.home, ".claude", "evidence")
        os.makedirs(os.path.join(self.ev, "loop-intake"))
        self.runs = os.path.join(self.ev, "unit-runs")
        os.makedirs(self.runs)
        self.run_dir = os.path.join(self.root, "run")
        os.makedirs(self.run_dir)
        units, scores = [], {}
        for uid, state in self.UNITS:
            spec = os.path.join(self.root, uid + ".md")
            with open(spec, "w", encoding="utf-8") as fh:
                fh.write("# %s\n" % uid)
            units.append({"id": uid, "state": state, "spec": spec, "sub_units": [uid + ".a", uid + ".b"], "evidence": ""})
            scores[uid + ".a"] = scores[uid + ".b"] = {"score": 9}
        self.wbs = os.path.join(self.root, "wbs.json")
        self._write(self.wbs, {"units": units})
        self._write(os.path.join(self.ev, "spec-scores.json"), scores)
        self.roles_path = os.path.join(self.root, "loop-roles.json")
        self._write(self.roles_path, ROLES)
        self.reg_path = os.path.join(self.root, "model-registry.json")
        self._write(self.reg_path, REGISTRY)
        self.env = {"HOME": self.home, "BROTHER_SCOPE": ".", "BROTHER_WIP": "9", "BROTHER_RUN_DIR": self.run_dir,
                    "BROTHER_LOOP_ROLES": self.roles_path, "BROTHER_MODEL_REGISTRY": self.reg_path,
                    "INTAKE_EVIDENCE": self.ev, "BROTHER_WIP_GATE": "off", "BROTHER_PIN_MODEL": "",
                    "BROTHER_S3_WORKER": "", "BROTHER_S3_CODEX": "", "BROTHER_OR_STATE_ROOT": os.path.join(self.root, "breaker")}
        _STATE["wbs"] = self.wbs

    @staticmethod
    def _write(path, doc):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def lose(self, sub, status=LOGIN_WAIT, error=LOGIN_ERROR, stamp="000001"):
        """A run of `sub` that ended CONFIG_WAIT, its round's every call carrying `error`: the seat lost at that time."""
        d = os.path.join(self.runs, "%s-%s" % (sub, stamp))
        os.makedirs(os.path.join(d, "round1", "out"), exist_ok=True)
        self._write(os.path.join(d, "round1", "results.json"), [{"id": "w1", "ok": False, "config": True, "error": error}])
        with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as fh:
            fh.write(status + "\n")
        return d

    def prove(self, transports, epoch):
        """An intake record whose seat gated reach holds an OK binding per transport, written at epoch."""
        self._write(os.path.join(self.ev, "loop-intake", "CURRENT.json"),
                    {"verdict": "READY", "epoch": epoch, "reach": [{"transport": t, "model": "m"} for t in transports]})

    def run_pass(self, extra=None):
        env = dict(self.env, **(extra or {}))
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.pool.RUNS = self.runs
        fake = self.fake = _FakeSubprocess()
        self.pool.subprocess = fake
        argv, buf = sys.argv, io.StringIO()
        try:
            sys.argv = ["runner_pool.py"]
            with contextlib.redirect_stdout(buf):
                self.pool.main()
        finally:
            sys.argv = argv
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return buf.getvalue(), fake.started

    def rows(self):
        try:
            with open(os.path.join(self.run_dir, "notify-request.jsonl"), encoding="utf-8") as fh:
                return [json.loads(l) for l in fh if l.strip()]
        except FileNotFoundError:
            return []

    @staticmethod
    def started_subs(started):
        return sorted(s for s, _env in started)


class SeatLost(_Pass):
    def test_losing_claude_parks_only_claude_units(self):
        """M-S3A-PARK-ALL: pass 1 starts all four; at pass 2 the claude seat is lost on UA.a. Exactly the claude bound
        units park (UA.a by its own wait, UB.a and UC.a by transport), the codex lane unit UX.a is admitted, none dropped."""
        out1, started1 = self.run_pass()
        self.assertEqual(self.started_subs(started1), ["UA.a", "UB.a", "UC.a", "UX.a"], out1)
        self.lose("UA.a")
        out2, started2 = self.run_pass()
        self.assertEqual(self.started_subs(started2), ["UX.a"], out2)
        parked = [l for l in out2.splitlines() if " skip: " in l and ("claude seat" in l or "needs a fact" in l)]
        self.assertEqual(sorted(l.split()[1] for l in parked), ["UA.a", "UB.a", "UC.a"], out2)
        self.assertEqual(sum(" skip: " in l for l in out2.splitlines()), 3, out2)   # zero dropped: 3 parked + 1 start = 4 units
        self.assertIn("SEATS   claude lost its login", out2)

    def test_one_row_per_cause_and_run(self):
        """M-S3A-ROW-EACH-PASS: the loss at pass 2 writes exactly one row; pass 3, the same lost seat, adds none."""
        self.lose("UA.a")
        out2, _ = self.run_pass()
        rows = self.rows()
        self.assertEqual(len(rows), 1, out2)
        self.assertEqual(rows[0]["key"], "seat-lost:login:claude")
        self.assertEqual(rows[0]["step"], "seat")
        out3, started3 = self.run_pass()
        self.assertEqual(len(self.rows()), 1, out3)
        self.assertEqual(self.started_subs(started3), ["UX.a"], out3)
        self.assertIn("row already there", out3)

    def test_a_held_login_refusal_is_a_login_loss(self):
        """The wired S2 holder: a seat whose run's login holder refuses (or has ended) lost its login exactly like a 401,
        so the claude bound units park, the codex lane unit runs, and one row is written."""
        for i, error in enumerate(HOLDER_ERRORS + (ended_holder_error(),)):
            with self.subTest(error=error[:40]):
                shutil.rmtree(self.runs); os.makedirs(self.runs)
                with contextlib.suppress(FileNotFoundError):
                    os.remove(os.path.join(self.run_dir, "notify-request.jsonl"))
                self.lose("UA.a", error=error, stamp="00000%d" % (i + 1))
                out, started = self.run_pass()
                self.assertEqual(self.started_subs(started), ["UX.a"], out)
                self.assertIn("SEATS   claude lost its login", out)
                self.assertEqual([r["key"] for r in self.rows()], ["seat-lost:login:claude"], out)

    def test_every_refusal_of_the_login_itself_is_a_login_loss(self):
        """S2 round 2 (review claim 7): every refusal brother_login raises for the login itself, MISSING by reference, as
        a seat surfaces it. Red at d98550631: the locked Keychain's MISSING ("Run brother-login ...") missed the
        lowercase hint, and two refusals had no hint at all, so a lost login parked nothing and nobody was told."""
        import brother_login as BL
        texts = (BL.MISSING, "Saved login is not a Claude token. Run brother-login again.",
                 "Brother could not hand over its login. Try again.",
                 "The held login is not a Claude token. Restart the run.",
                 "Keychain did not respond. Unlock it and try again.")
        for i, text in enumerate(texts):
            with self.subTest(text=text[:40]):
                self.assertTrue(self.pool.login_class("CONFIG_WAIT round 1: x\nCONFIG_WAIT: " + text), text)
                shutil.rmtree(self.runs); os.makedirs(self.runs)
                with contextlib.suppress(FileNotFoundError):
                    os.remove(os.path.join(self.run_dir, "notify-request.jsonl"))
                self.lose("UA.a", error="CONFIG_WAIT: " + text, stamp="00001%d" % i)
                out, started = self.run_pass()
                self.assertEqual(self.started_subs(started), ["UX.a"], out)
                self.assertEqual([r["key"] for r in self.rows()], ["seat-lost:login:claude"], out)

    def test_a_model_fault_is_not_a_login_loss(self):
        """A CONFIG_WAIT whose cause is an unknown model parks only itself, as before S3: no transport loss, no row."""
        self.lose("UA.a", error=MODEL_ERROR)
        out, started = self.run_pass()
        self.assertEqual(self.started_subs(started), ["UB.a", "UC.a", "UX.a"], out)
        self.assertEqual(self.rows(), [], out)
        self.assertNotIn("lost its login", out)

    def test_a_fresh_proof_reseats(self):
        """A seat proof newer than the loss clears it: every claude unit, the one that waited included, comes back.
        A proof OLDER than the loss (stale), or one for the other transport, clears nothing."""
        self.lose("UA.a")
        self.prove(["claude", "codex"], time.time() - 3600)
        out, started = self.run_pass()
        self.assertEqual(self.started_subs(started), ["UX.a"], out)
        self.prove(["codex"], time.time() + 60)
        out, started = self.run_pass()
        self.assertEqual(self.started_subs(started), ["UX.a"], out)
        self.prove(["claude"], time.time() + 60)
        out, started = self.run_pass()
        self.assertEqual(self.started_subs(started), ["UA.a", "UB.a", "UC.a", "UX.a"], out)
        self.assertIn("SEATS   claude re-seated", out)

    def test_hostile_status_and_roles_never_crash(self):
        """A 1 MB status, a role value that is a list and a registry row with no transport: refused or parked, never run."""
        self.lose("UA.a", status="CONFIG_WAIT " + "x" * (1 << 20))
        out, started = self.run_pass()
        self.assertEqual(self.started_subs(started), ["UX.a"], out)   # the login cause still read from the round
        pool = self.pool
        self.assertFalse(pool.login_class("CONFIG_WAIT " + "x" * (1 << 20)))
        self.assertTrue(pool.login_class("CONFIG_WAIT round 1\n" + LOGIN_ERROR))
        self.assertFalse(pool.login_class("EXHAUSTED " + LOGIN_ERROR))
        for bad in (None, 0, [], {}, b"CONFIG_WAIT 401"):
            self.assertFalse(pool.login_class(bad))
        roles = json.loads(json.dumps(ROLES["roles"]))
        reg = json.loads(json.dumps(REGISTRY["models"]))
        self.assertEqual(pool.sub_transport("UA.a", roles, reg), "claude")
        self.assertEqual(pool.sub_transport("UX.b", roles, reg), "codex")
        self.assertEqual(pool.sub_transport("UA.a", dict(roles, worker=["opus55"]), reg), "")
        self.assertEqual(pool.sub_transport("UA.a", roles, dict(reg, opus55={"id": "x"})), "")
        self.assertEqual(pool.sub_transport("UA.a", roles, dict(reg, opus55={"transport": "carrier-pigeon"})), "")
        for bad in (None, [], "x", 3):
            self.assertEqual(pool.sub_transport("UA.a", bad, reg), "")
            self.assertEqual(pool.sub_transport("UA.a", roles, bad), "")
        for bad in (None, [], "", 3, b"UA.a"):
            self.assertEqual(pool.sub_transport(bad, roles, reg), "")
        self.assertEqual(pool.transport_parked(["UA.a", "UX.a"], set(), roles, reg), set())
        self.assertEqual(pool.transport_parked([], {"claude"}, roles, reg), set())
        with self.assertRaises(ValueError):
            pool.transport_parked("UA.a", {"claude"}, roles, reg)
        with self.assertRaises(ValueError):
            pool.transport_parked(["UA.a"], "claude", roles, reg)

    def test_a_lost_codex_seat_parks_the_codex_units_only(self):
        """The other host: a lost codex login parks UX.a and leaves the three claude units admitted."""
        self.lose("UX.a", error="CONFIG_WAIT: the codex login could not be probed; a Codex pin needs this machine's Codex login")
        out, started = self.run_pass()
        self.assertEqual(self.started_subs(started), ["UA.a", "UB.a", "UC.a"], out)
        self.assertEqual([r["key"] for r in self.rows()], ["seat-lost:login:codex"], out)


class TheCopiesAgree(unittest.TestCase):
    """runner_pool keeps three small copies; each is held equal to the file it copies, so none drifts."""

    def test_the_copies_agree_with_their_sources(self):
        pool = _load()
        import model_router
        self.assertEqual(tuple(pool.KNOWN_TRANSPORTS), tuple(model_router.KNOWN_TRANSPORTS))
        self.assertEqual(dict(pool.CONTENT_ORDER), dict(model_router.ORDER))
        sources = ""
        for name in ("native_worker.py", "loop_intake.py", "brother_login.py", "model_call.py"):
            with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                sources += fh.read()
        for hint in pool.LOGIN_HINTS:
            self.assertIn(hint, sources, "a login hint no seat remedy prints any more: %r" % hint)


class Handover(_Pass):
    """The codex lane's own unit has landed, so the codex role is open (D-S5, "Codex takes any role")."""
    UNITS = (("UA", "OPEN"), ("UB", "OPEN"), ("UC", "OPEN"), ("UX", "DONE"))

    def test_codex_takes_a_parked_unit(self):
        """M-S3A-NO-HANDOVER: exactly one parked claude bound sub unit starts, with the claude role's setting pinned to
        the codex role's model in the child's environment; the other parked ones stay parked."""
        self.lose("UA.a")
        out, started = self.run_pass()
        self.assertEqual(self.started_subs(started), ["UB.a"], out)
        env = started[0][1]
        self.assertIsNotNone(env, out)
        self.assertEqual(env.get("BROTHER_S3_WORKER"), "luna", out)
        self.assertIn("HANDOVER UB.a", out)

    def test_the_handed_over_unit_keeps_the_runs_login_holder(self):
        """The wired S2 holder survives the hand over: the codex pinned child is told the run's holder and given its
        descriptor, like every other unit runner this pool starts, so its pin never drops the hop."""
        import socket
        a, b = socket.socketpair()
        self.addCleanup(a.close); self.addCleanup(b.close)
        self.lose("UA.a")
        out, started = self.run_pass({"BROTHER_LOGIN_HOLD_FD": str(a.fileno())})
        self.assertEqual(self.started_subs(started), ["UB.a"], out)
        env = started[0][1]
        self.assertEqual(env.get("BROTHER_S3_WORKER"), "luna", out)
        self.assertEqual(env.get("BROTHER_LOGIN_HOLD_FD"), str(a.fileno()), out)
        self.assertEqual(tuple(self.fake.fds[0]), (a.fileno(),), out)

    def test_a_claimed_unit_is_never_handed_over(self):
        """The concurrent second actor: a builder claims UB.a first; the codex role takes UC.a, never the claimed one."""
        import builder_claim
        self.lose("UA.a")
        builder_claim.claim("UB.a", "builder-2", os.getpid(), "build/x", runs_dir=self.runs)
        out, started = self.run_pass()
        self.assertEqual(self.started_subs(started), ["UC.a"], out)

    def test_private_content_is_never_handed_to_codex(self):
        """The trust boundary: a claude role whose content is private, or unreadable (a list), is not handed to a
        public only codex model."""
        for content in ("private", ["public"]):
            doc = json.loads(json.dumps(ROLES))
            doc["roles"]["worker"]["content"] = content
            self._write(self.roles_path, doc)
            self.lose("UA.a")
            out, started = self.run_pass()
            self.assertEqual(started, [], out)
            self.assertIn("HANDOVER refused", out)


class Unreadable(_Pass):
    def test_unreadable_roles_park_as_today(self):
        """M-S3A-BLANK-RUNS: with loop-roles.json unparseable no transport is known, so a lost login parks every unit
        (today's behaviour) with a note, and nothing is handed over. With no loss, the same unreadable roles admit all."""
        with open(self.roles_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        out0, started0 = self.run_pass()
        self.assertEqual(len(started0), 4, out0)
        self.lose("UA.a")
        out, started = self.run_pass()
        self.assertEqual(started, [], out)
        self.assertIn("SEATS   NO-DATA", out)
        self.assertEqual([r["key"] for r in self.rows()], ["seat-lost:login:unknown"], out)

    def test_an_unknown_transport_parks_beside_a_known_loss(self):
        """M-S3A-BLANK-RUNS, the edge the whole file case cannot see: the claude seat is lost, and UC's role names a
        model the registry does not know. UC's transport is unknown, so it parks with the claude units; only the codex
        unit runs. Unknown never reads as the other host."""
        doc = json.loads(json.dumps(ROLES))
        doc["roles"]["mystery"] = dict(doc["roles"]["worker"], setting="BROTHER_S3_MYSTERY", default="ghost", builds=["UC"])
        self._write(self.roles_path, doc)
        self.lose("UA.a")
        out, started = self.run_pass({"BROTHER_S3_MYSTERY": ""})
        self.assertEqual(self.started_subs(started), ["UX.a"], out)
        self.assertIn("UC.a", [l.split()[1] for l in out.splitlines() if "unknown transport" in l], out)
        self.assertEqual(self.pool.transport_parked(["UC.a", "UX.a"], {"claude"}, doc["roles"], REGISTRY["models"]), {"UC.a"})

    def test_unreadable_registry_parks_as_today(self):
        with open(self.reg_path, "w", encoding="utf-8") as fh:
            fh.write("[]")
        self.lose("UA.a")
        out, started = self.run_pass()
        self.assertEqual(started, [], out)
        self.assertIn("SEATS   NO-DATA", out)


#: S2 round 3 (finding 4): a fan out whose first arm is a Claude model and whose others are bridge models. Prepended to
#: the straggler suite's fake fan out; FAKE_FANOUT_MODE picks the rows: mixed_login (the Claude arm refused its login,
#: the bridge arms timed out), mixed_timeout (every arm timed out), two_claude (two Claude arms, one refused, one timed out).
MIXED_FANOUT = r'''
import json as _j, os as _o, sys as _s
_mode = _o.environ.get("FAKE_FANOUT_MODE", "")
if _mode in ("mixed_login", "mixed_timeout", "two_claude"):
    _a = _s.argv[1:]
    with open(_a[0]) as _fh:
        _jobs = _j.load(_fh)
    _rows = []
    for _k, _job in enumerate(_jobs):
        _claude = _k == 0 or (_mode == "two_claude" and _k == 1)
        _row = {"id": _job["id"], "model": "opus55" if _claude else "deepseek", "ok": False, "error": "timeout after 600s"}
        if _k == 0 and _mode != "mixed_timeout":
            _row.update(config=True, error="CONFIG_WAIT: " + _o.environ["FAKE_CLAUDE_ERROR"])
        _rows.append(_row)
    with open(_a[_a.index("--results") + 1], "w") as _fh:
        _j.dump(_rows, _fh)
    _s.exit(1)
'''


class MixedRound(_Pass):
    """S2 round 3 (finding 4), at the real unit_runner.py: a round whose every Claude arm was refused its login, beside
    bridge arms that failed otherwise, ends CONFIG_WAIT, so the pool parks the Claude units once and writes one row.
    Red at 12a64cf96: fanout_verdict.all_config_wait answered False for the mixed round, so the round was graded, an
    attempt spent, and the run reached EXHAUSTED with nobody told."""

    def runner(self, mode):
        sys.path.insert(0, os.path.dirname(HERE))
        import test_unit_runner_native as TUN
        import test_runner_straggler_settles as TRS
        import brother_login as BL
        from unittest import mock
        with mock.patch.object(TRS, "FAKE_FANOUT", MIXED_FANOUT + TRS.FAKE_FANOUT):
            r = TUN.Run(dict(TUN.OFF, FAKE_FANOUT_MODE=mode, FAKE_CLAUDE_ERROR=BL.HOLDER_ENDED))
        self.addCleanup(r.cleanup)
        return r

    def test_a_mixed_round_whose_claude_arm_lost_its_login_parks_once(self):
        r = self.runner("mixed_login")
        self.assertEqual(r.rc, 6, r.out)
        self.assertTrue(r.status().startswith("CONFIG_WAIT"), r.status())
        self.assertTrue(self.pool.login_class(r.status()), "the STATUS alone names the login cause: " + r.status())
        shutil.copytree(r.run, os.path.join(self.runs, "UA.a-000001"))
        out, started = self.run_pass()
        self.assertEqual(self.started_subs(started), ["UX.a"], out)
        self.assertIn("SEATS   claude lost its login", out)
        out2, _ = self.run_pass()
        self.assertEqual([x["key"] for x in self.rows()], ["seat-lost:login:claude"], out2)

    def test_a_round_that_merely_timed_out_is_graded_as_before(self):
        r = self.runner("mixed_timeout")
        self.assertFalse(r.status().startswith("CONFIG_WAIT"), r.status())

    def test_a_claude_arm_that_did_not_lose_its_login_keeps_the_round(self):
        """Every Claude arm, not any: one refused its login, another Claude arm timed out, so the login is not shown lost."""
        r = self.runner("two_claude")
        self.assertFalse(r.status().startswith("CONFIG_WAIT"), r.status())

    def test_the_verdict_over_hostile_rounds(self):
        import fanout_verdict as FV
        import brother_login as BL
        t = {"opus55": "claude", "deepseek": "bridge"}.get
        lost = {"id": "a", "model": "opus55", "ok": False, "error": "CONFIG_WAIT: " + BL.HOLDER_ENDED}
        late = {"id": "b", "model": "deepseek", "ok": False, "error": "timeout after 600s"}
        for rows, want in (([lost, late], True), ([lost], True), ([late], False), ([lost, dict(late, ok=True)], False),
                           ([lost, dict(late, model="opus55")], False), ([dict(lost, model="ghost"), late], True),
                           ([dict(late, model="opus55", error="RuntimeError: run brother-login")], False),
                           ([], None), ({"a": 1}, None), ([lost, "x"], None)):
            path = os.path.join(self.root, "results.json")
            self._write(path, rows)
            with self.subTest(rows=str(rows)[:80]):
                self.assertEqual(FV.logins_lost(path, t), want)
        self.assertIsNone(FV.logins_lost(os.path.join(self.root, "absent.json"), t))


if __name__ == "__main__":
    unittest.main()
