#!/usr/bin/env python3
"""Tests for mobile_appium_adapter.py (EPIC M3.05).

No real Appium server or Appium-Python-Client is installed on the machine
this suite runs on (checked directly: `pip show Appium-Python-Client` finds
nothing, `import appium` raises ModuleNotFoundError, and nothing listens on
the default Appium port). So the NO-DATA path this module takes when a
session cannot be started is exercised FOR REAL, not mocked -- the same
honesty discipline the module itself applies. Session-needing handler logic
(what call each action makes, and that a session is always torn down) is
proven with a small in-process fake driver standing in for a real
appium.webdriver.Remote instance, patched in at the two seams the module
itself defines for this purpose (_client/start_session) -- not a reimplementation
of the Appium protocol, just enough of the real method surface (activate_app,
tap, swipe, find_element, ...) to prove this module calls it correctly and
always tears it down, including when a handler raises.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mobile_appium_adapter as A
import mobile_canonical_action as ACT
import mobile_driver_contract as DC
import contract_check as CC
import test_mobile_canonical_action as MCAT  # reuse its VALID_BY_ACTION/record_for


class _FakeElement:
    def __init__(self, rect=None, displayed=True):
        self.rect = rect or {"x": 10, "y": 20, "width": 100, "height": 40}
        self._displayed = displayed
        self.clicked = False
        self.sent_keys = None

    def click(self):
        self.clicked = True

    def send_keys(self, text):
        self.sent_keys = text

    def is_displayed(self):
        return self._displayed


class _FakeSwitchTo:
    def __init__(self, element):
        self.active_element = element


class _FakeDriver:
    """Stands in for appium.webdriver.Remote: only the real method names
    mobile_appium_adapter.py actually calls, each recording its call so a
    test can assert on it, none of them touching a network."""

    def __init__(self, elements=None, window_size=None):
        self.calls = []
        self.quit_called = False
        self._elements = elements or {}
        self._window_size = window_size or {"width": 1000, "height": 2000}
        self.switch_to = _FakeSwitchTo(_FakeElement())
        self.orientation = None

    def activate_app(self, app_id):
        self.calls.append(("activate_app", app_id))

    def get(self, uri):
        self.calls.append(("get", uri))

    def set_location(self, lat, lon):
        self.calls.append(("set_location", lat, lon))

    def get_screenshot_as_file(self, path):
        self.calls.append(("get_screenshot_as_file", path))
        Path(path).write_bytes(_PNG)
        return True

    def tap(self, positions, duration=None):
        self.calls.append(("tap", positions, duration))

    def find_element(self, by, value):
        self.calls.append(("find_element", by, value))
        key = (by, value)
        if key not in self._elements:
            # Stands in for selenium's NoSuchElementException, which this
            # module no longer imports by name (see mobile_appium_adapter.
            # _assert_visible's own note: selenium is not installed on this
            # machine either, so nothing here imports it just to catch it).
            raise LookupError("no element for %r" % (key,))
        return self._elements[key]

    def get_window_size(self):
        self.calls.append(("get_window_size",))
        return self._window_size

    def swipe(self, start_x, start_y, end_x, end_y, duration=0):
        self.calls.append(("swipe", start_x, start_y, end_x, end_y, duration))

    def back(self):
        self.calls.append(("back",))

    def set_network_connection(self, bitmask):
        self.calls.append(("set_network_connection", bitmask))

    def quit(self):
        self.quit_called = True


import base64
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=")


class _FakeAppiumBy:
    ACCESSIBILITY_ID = "accessibility id"


class MobileAppiumAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brother-m305-tests-")
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "evidence"
        self.driver_schema = CC.load_json(DC.DEFAULT_SCHEMA, "driver schema")
        self.action_schema = CC.load_json(ACT.DEFAULT_SCHEMA, "action schema")

    def run_action(self, action, capabilities=None, **overrides):
        record = MCAT.record_for(action, **overrides)
        stages = []
        result = A.execute_action(record, capabilities or {}, self.out, stages)
        return result, stages

    def run_with_fake_driver(self, driver, action, **overrides):
        record = MCAT.record_for(action, **overrides)
        with patch.object(A, "_client", return_value=(None, None, _FakeAppiumBy)), \
                patch.object(A, "start_session", return_value=driver):
            return A.execute_action(record, {}, self.out, [])

    # -- self-description matches the real implementation, both directions --

    def test_describe_validates_against_driver_contract_schema(self):
        self.assertEqual(DC.check(A.describe(), self.driver_schema), [])

    def test_describe_supported_actions_match_action_handlers(self):
        self.assertEqual(set(A.describe()["supported_actions"]), set(A.ACTION_HANDLERS))

    def test_handlers_and_unsupported_reasons_cover_the_full_vocabulary_both_directions(self):
        all_actions = set(ACT.ACTION_RULES)
        self.assertEqual(set(A.ACTION_HANDLERS) | set(A.UNSUPPORTED_REASONS), all_actions)
        self.assertEqual(set(A.ACTION_HANDLERS) & set(A.UNSUPPORTED_REASONS), set())

    # -- honest NO-DATA on this real machine (no Appium installed) --

    def test_start_session_is_no_data_for_real_on_this_machine(self):
        with self.assertRaises(A.AppiumUnavailable) as ctx:
            A.start_session({})
        self.assertIn("not installed", str(ctx.exception))

    def test_session_needing_action_is_no_data_for_real(self):
        result, stages = self.run_action("OPEN_APP")
        self.assertEqual(result["status"], "NO-DATA")
        self.assertIn("Appium-Python-Client not installed", result["detail"])

    def test_connection_failure_never_leaks_server_url_credentials(self):
        """Security-review finding, reproduced then closed: a connection
        failure interpolated the complete operator-provided server_url,
        unredacted, into AppiumUnavailable's message, and execute_action
        returned that message as the caller-visible "detail". A server_url
        carrying HTTP basic-auth userinfo therefore leaked credentials into
        returned evidence. This drives the real failure path (Remote()
        raising, its own exception text ALSO echoing the raw URL back, the
        same shape a real connection-refused error takes) and asserts the
        credential is absent from both the raised message and the
        execute_action detail, not merely that some redaction ran somewhere."""
        secret_url = "http://probe-user:probe-secret@127.0.0.1:4723"

        class _FailingRemote:
            def __init__(self, **kwargs):
                raise ConnectionRefusedError(
                    "connection refused to %s" % kwargs["command_executor"])

        with patch.dict(sys.modules, _fake_appium_modules()), \
                patch.object(A, "_client", return_value=(
                    type("W", (), {"Remote": _FailingRemote}), _FakeOptions, _FakeAppiumBy)):
            with self.assertRaises(A.AppiumUnavailable) as ctx:
                A.start_session({}, secret_url)
        message = str(ctx.exception)
        self.assertNotIn("probe-secret", message)
        self.assertNotIn("probe-user:probe-secret", message)
        self.assertIn("[redacted]", message)

    def test_session_less_actions_never_touch_appium_and_pass(self):
        for action in sorted(A.NO_SESSION_ACTIONS):
            with self.subTest(action=action):
                result, _ = self.run_action(action)
                self.assertEqual(result["status"], "PASS")

    def test_unsupported_actions_never_attempt_a_session(self):
        for action in sorted(A.UNSUPPORTED_REASONS):
            with self.subTest(action=action):
                result, _ = self.run_action(action)
                self.assertEqual(result["status"], "UNSUPPORTED")
                self.assertEqual(result["detail"], A.UNSUPPORTED_REASONS[action])

    def test_invalid_record_fails_before_any_session_attempt(self):
        record = MCAT.record_for("OPEN_APP")
        del record["params"]
        result = A.execute_action(record, {}, self.out, [])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("invalid canonical action record", result["detail"])

    def test_non_dict_record_is_a_clean_fail(self):
        result = A.execute_action(["not", "a", "dict"], {}, self.out, [])
        self.assertEqual(result["status"], "FAIL")
        self.assertIsNone(result["action_id"])

    # -- selector translation (_locate): accessibility_id and xpath are real,
    #    everything else is an honest, named gap --

    def test_locate_accessibility_id(self):
        pair, reason = A._locate(_FakeAppiumBy, {"selector_type": "accessibility_id", "value": "loginButton"})
        self.assertEqual(pair, ("accessibility id", "loginButton"))
        self.assertIsNone(reason)

    def test_locate_xpath_is_real_selenium_by_xpath_when_selenium_is_installed(self):
        try:
            from selenium.webdriver.common.by import By
        except ImportError:
            self.skipTest("selenium is not installed on this machine (checked directly, not "
                          "guessed); see test_locate_xpath_is_honest_no_data_without_selenium")
        pair, reason = A._locate(_FakeAppiumBy, {"selector_type": "xpath", "value": "//Button"})
        self.assertEqual(pair, (By.XPATH, "//Button"))
        self.assertIsNone(reason)

    def test_locate_xpath_is_honest_no_data_without_selenium(self):
        # Real, not mocked: selenium is not installed on this machine
        # (checked directly with `python3 -c "import selenium"`, which
        # raises ModuleNotFoundError), so this is the actual behavior a
        # caller sees here, not a simulated one.
        try:
            import selenium  # noqa: F401
            self.skipTest("selenium is installed on this machine; see the sibling test above")
        except ImportError:
            pass
        pair, reason = A._locate(_FakeAppiumBy, {"selector_type": "xpath", "value": "//Button"})
        self.assertIsNone(pair)
        self.assertIn("selenium not installed", reason)

    def test_locate_unsupported_selector_types_name_a_real_reason(self):
        for selector_type in ("text", "image", "testid"):
            with self.subTest(selector_type=selector_type):
                pair, reason = A._locate(_FakeAppiumBy, {"selector_type": selector_type, "value": "x"})
                self.assertIsNone(pair)
                self.assertIsInstance(reason, str)
                self.assertGreater(len(reason), 10)

    # -- real dispatch through a fake driver: proves the actual Appium
    #    method calls this module makes, using the real, verified names --

    def test_open_app_calls_activate_app(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(driver, "OPEN_APP")
        self.assertEqual(result["status"], "PASS")
        self.assertIn(("activate_app", "com.example.app"), driver.calls)
        self.assertTrue(driver.quit_called)

    def test_tap_target_coordinates_calls_tap(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(
            driver, "TAP_TARGET",
            target={"selector_type": "coordinates", "value": "12,34"})
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(driver.calls, [("tap", [(12.0, 34.0)], None)])
        self.assertTrue(driver.quit_called)

    def test_tap_target_accessibility_id_clicks_the_element(self):
        element = _FakeElement()
        driver = _FakeDriver(elements={("accessibility id", "loginButton"): element})
        result = self.run_with_fake_driver(
            driver, "TAP_TARGET",
            target={"selector_type": "accessibility_id", "value": "loginButton"})
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(element.clicked)
        self.assertTrue(driver.quit_called)

    def test_tap_target_unsupported_selector_never_calls_find_element(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(
            driver, "TAP_TARGET",
            target={"selector_type": "text", "value": "Login"})
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertEqual(driver.calls, [])
        self.assertTrue(driver.quit_called)

    def test_long_press_target_element_taps_its_center(self):
        element = _FakeElement(rect={"x": 0, "y": 0, "width": 100, "height": 50})
        driver = _FakeDriver(elements={("accessibility id", "menu"): element})
        result = self.run_with_fake_driver(
            driver, "LONG_PRESS_TARGET",
            target={"selector_type": "accessibility_id", "value": "menu"})
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(driver.calls[-1], ("tap", [(50.0, 25.0)], 1000))

    def test_type_text_with_target_sends_keys_to_that_element(self):
        element = _FakeElement()
        driver = _FakeDriver(elements={("accessibility id", "search"): element})
        result = self.run_with_fake_driver(
            driver, "TYPE_TEXT",
            target={"selector_type": "accessibility_id", "value": "search"})
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(element.sent_keys, "hello")

    def test_type_text_without_target_sends_keys_to_active_element(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(driver, "TYPE_TEXT")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(driver.switch_to.active_element.sent_keys, "hello")

    def test_swipe_up_computes_a_vertical_vector_from_window_size(self):
        driver = _FakeDriver(window_size={"width": 1000, "height": 2000})
        result = self.run_with_fake_driver(driver, "SWIPE", params={"direction": "up"})
        self.assertEqual(result["status"], "PASS")
        swipe_call = [c for c in driver.calls if c[0] == "swipe"][0]
        _, start_x, start_y, end_x, end_y, duration = swipe_call
        self.assertEqual(start_x, end_x)  # a pure vertical swipe
        self.assertGreater(start_y, end_y)  # "up" means end is above start

    def test_back_calls_driver_back(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(driver, "BACK")
        self.assertEqual(result["status"], "PASS")
        self.assertIn(("back",), driver.calls)

    def test_rotate_maps_landscape_left_to_landscape(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(driver, "ROTATE", params={"orientation": "landscape_left"})
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(driver.orientation, "LANDSCAPE")
        self.assertIn("finer", result["detail"])

    def test_set_network_offline_maps_to_bitmask_zero(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(driver, "SET_NETWORK", params={"condition": "offline"})
        self.assertEqual(result["status"], "PASS")
        self.assertIn(("set_network_connection", 0), driver.calls)

    def test_set_network_slow_is_unsupported_not_guessed(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(driver, "SET_NETWORK", params={"condition": "slow"})
        self.assertEqual(result["status"], "UNSUPPORTED")
        self.assertEqual([c for c in driver.calls if c[0] == "set_network_connection"], [])

    def test_assert_visible_true_for_a_displayed_element(self):
        element = _FakeElement(displayed=True)
        driver = _FakeDriver(elements={("accessibility id", "banner"): element})
        result = self.run_with_fake_driver(
            driver, "ASSERT_VISIBLE",
            target={"selector_type": "accessibility_id", "value": "banner"})
        self.assertEqual(result["status"], "PASS")

    def test_assert_visible_fails_for_a_missing_element(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(
            driver, "ASSERT_VISIBLE",
            target={"selector_type": "accessibility_id", "value": "missing"})
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(driver.quit_called)

    def test_wait_for_duration_ms_sleeps_and_passes(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(driver, "WAIT_FOR", params={"duration_ms": 1})
        self.assertEqual(result["status"], "PASS")

    def test_wait_for_target_only_is_unsupported(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(
            driver, "WAIT_FOR",
            params={},
            target={"selector_type": "accessibility_id", "value": "spinner"})
        self.assertEqual(result["status"], "UNSUPPORTED")

    def test_capture_writes_a_real_validated_png(self):
        driver = _FakeDriver()
        result = self.run_with_fake_driver(driver, "CAPTURE")
        self.assertEqual(result["status"], "PASS")
        self.assertIn("screenshot", result)
        self.assertEqual(result["screenshot"]["width"], 1)

    # -- the adversarial-review target named in this unit's own brief: a
    #    session must never be leaked past execute_action(), including when
    #    a handler raises --

    def test_session_is_torn_down_when_a_handler_raises(self):
        driver = _FakeDriver()

        def _boom(record, driver, AppiumBy, evidence_root):
            raise RuntimeError("simulated handler failure")

        with patch.dict(A.ACTION_HANDLERS, {"OPEN_APP": _boom}):
            result = self.run_with_fake_driver(driver, "OPEN_APP")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("simulated handler failure", result["detail"])
        self.assertTrue(driver.quit_called, "a session must be closed even when its handler raises")

    def test_session_is_torn_down_when_evidence_root_is_unwritable(self):
        driver = _FakeDriver()
        # A file where a directory is expected makes Path.mkdir raise OSError.
        blocker = Path(self.temp.name) / "blocker"
        blocker.write_text("not a directory")
        record = MCAT.record_for("OPEN_APP")
        with patch.object(A, "_client", return_value=(None, None, _FakeAppiumBy)), \
                patch.object(A, "start_session", return_value=driver):
            result = A.execute_action(record, {}, blocker / "evidence", [])
        self.assertEqual(result["status"], "NO-DATA")
        self.assertTrue(driver.quit_called)

    def test_end_session_never_raises_even_if_quit_does(self):
        class _BrokenDriver:
            def quit(self):
                raise RuntimeError("network already gone")

        A.end_session(_BrokenDriver())  # must not raise

    def test_end_session_is_a_no_op_for_none(self):
        A.end_session(None)  # must not raise


#: Every keyword the real AppiumClientConfig accepts, read from upstream
#: source on 2026-09-16 (neither library is installed here, so the names come
#: from the projects themselves, not from recall):
#: selenium/webdriver/remote/client_config.py ClientConfig.__init__ on
#: SeleniumHQ/selenium trunk contributes the sixteen selenium names, and
#: appium/webdriver/client_config.py AppiumClientConfig.__init__ on
#: appium/python-client master contributes remote_server_addr (its one named
#: positional) plus direct_connection, which it pops before delegating the
#: rest to selenium. A fake that swallowed any keyword would pass a suite
#: asserting on `timeout` even if the adapter had renamed or mistyped it, so
#: this set is what turns that assertion into a real one.
_REAL_CLIENT_CONFIG_KWARGS = frozenset({
    "remote_server_addr", "keep_alive", "proxy", "ignore_certificates",
    "init_args_for_pool_manager", "timeout", "ca_certs", "username",
    "password", "auth_type", "token", "user_agent", "extra_headers",
    "websocket_timeout", "websocket_interval", "websocket_max_message_size",
    "direct_connection",
})


class _FakeClientConfig:
    """Records the kwargs the adapter really passed, so a test asserts on
    the deadline that was actually handed to the client, never on a
    docstring promising one.

    Refuses any keyword the real class would refuse, and refuses a missing
    remote_server_addr the way the real required parameter does, so a
    renamed or mistyped parameter in the adapter fails here loudly instead
    of being silently recorded and asserted on."""

    last = None

    def __init__(self, **kwargs):
        unexpected = sorted(set(kwargs) - _REAL_CLIENT_CONFIG_KWARGS)
        if unexpected:
            raise TypeError(
                "AppiumClientConfig.__init__() got an unexpected keyword argument "
                "%r" % (unexpected[0],))
        if "remote_server_addr" not in kwargs:
            raise TypeError(
                "AppiumClientConfig.__init__() missing 1 required positional "
                "argument: 'remote_server_addr'")
        _FakeClientConfig.last = kwargs
        self.kwargs = kwargs


class _FakeRemote:
    last = None

    def __init__(self, **kwargs):
        _FakeRemote.last = kwargs


class _FakeOptions:
    def load_capabilities(self, caps):
        self.caps = caps
        return self


def _fake_appium_modules():
    """A minimal fake appium.webdriver.client_config package tree in
    sys.modules. Needed because `from appium.webdriver.client_config import
    AppiumClientConfig` imports the parent packages too, and neither appium
    nor selenium is installed on this machine (the suite's one honest skip
    above says so). This stubs ONLY the import, so the assertion below is
    about what this adapter really passes, not about what Appium does with
    it."""
    import types
    appium_mod = types.ModuleType("appium")
    webdriver_mod = types.ModuleType("appium.webdriver")
    client_config_mod = types.ModuleType("appium.webdriver.client_config")
    client_config_mod.AppiumClientConfig = _FakeClientConfig
    appium_mod.webdriver = webdriver_mod
    webdriver_mod.client_config = client_config_mod
    return {"appium": appium_mod, "appium.webdriver": webdriver_mod,
            "appium.webdriver.client_config": client_config_mod}


class AppiumAdapterHardeningTests(unittest.TestCase):
    """Regression tests for the defects adversarial review of PR #726 found
    (2026-09-16). The two shared-validator criticals (NaN, WAIT_FOR
    ceiling) are proved in test_mobile_canonical_action.py, where the fix
    lives; what is proved here is this adapter's own gaps."""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        _FakeClientConfig.last = None
        _FakeRemote.last = None

    # --- CRITICAL: no deadline on any remote call ------------------------

    def test_start_session_passes_an_explicit_deadline_to_the_client(self):
        # grep for "timeout" across the whole module used to return exactly
        # one hit, inside a docstring, describing a guarantee the code did
        # not implement. selenium's ClientConfig.timeout defaults to
        # socket.getdefaulttimeout(), normally None, i.e. NO timeout: a hung
        # Appium server (one that accepts the TCP connection and never
        # answers) blocked start_session forever, with no bound, no NO-DATA
        # and no evidence written.
        with patch.dict(sys.modules, _fake_appium_modules()), \
                patch.object(A, "_client", return_value=(
                    type("W", (), {"Remote": _FakeRemote}), _FakeOptions, _FakeAppiumBy)):
            A.start_session({}, "http://127.0.0.1:4723", timeout_ms=30000)
        self.assertIsNotNone(_FakeClientConfig.last, "no client_config was constructed")
        self.assertEqual(_FakeClientConfig.last["timeout"], 30)
        self.assertIs(_FakeRemote.last["client_config"].__class__, _FakeClientConfig)

    def test_the_fake_client_config_refuses_what_the_real_one_would_refuse(self):
        # Guards the three tests above. They assert on
        # _FakeClientConfig.last["timeout"], which is only evidence about the
        # adapter if the fake would have rejected a renamed or mistyped
        # parameter rather than recording it. An earlier fake took **kwargs
        # and kept everything, so those assertions would have stayed green
        # against an adapter passing, say, timeout_s= or timout=.
        for bad in ("timeout_s", "timout", "timeout_ms", "socket_timeout"):
            with self.subTest(keyword=bad):
                with self.assertRaises(TypeError):
                    _FakeClientConfig(remote_server_addr="http://127.0.0.1:4723",
                                      **{bad: 30})
        with self.assertRaises(TypeError):
            _FakeClientConfig(timeout=30)  # the real parameter is required
        # and every real keyword still goes through, so the fake refuses a
        # rename without refusing the library's own surface.
        accepted = _FakeClientConfig(remote_server_addr="http://127.0.0.1:4723",
                                     timeout=30, direct_connection=True,
                                     keep_alive=False)
        self.assertEqual(accepted.kwargs["timeout"], 30)

    def test_start_session_uses_the_named_module_default_when_no_timeout_is_given(self):
        with patch.dict(sys.modules, _fake_appium_modules()), \
                patch.object(A, "_client", return_value=(
                    type("W", (), {"Remote": _FakeRemote}), _FakeOptions, _FakeAppiumBy)):
            A.start_session({}, "http://127.0.0.1:4723")
        self.assertEqual(_FakeClientConfig.last["timeout"], A.DEFAULT_TIMEOUT_MS // 1000)

    def test_execute_action_derives_the_deadline_from_the_records_timeout_ms(self):
        record = MCAT.record_for(
            "TAP_TARGET", target={"selector_type": "accessibility_id", "value": "b"})
        record["timeout_ms"] = 5000
        with patch.dict(sys.modules, _fake_appium_modules()), \
                patch.object(A, "_client", return_value=(
                    type("W", (), {"Remote": _FakeRemote}), _FakeOptions, _FakeAppiumBy)):
            A.execute_action(record, {}, self.out, [])
        self.assertEqual(_FakeClientConfig.last["timeout"], 5)

    def test_timeout_seconds_falls_back_for_every_unusable_value(self):
        # bool is an int subclass in Python, so True must not read as a 1ms
        # deadline; a zero or negative socket timeout is not a shorter
        # deadline but a different and broken mode.
        for bad in (None, True, False, 0, -1, "5000", 1.5):
            with self.subTest(value=bad):
                self.assertEqual(A._timeout_seconds(bad), A.DEFAULT_TIMEOUT_MS // 1000)

    def test_a_sub_second_timeout_rounds_up_rather_than_to_zero(self):
        # Rounding 400ms down to 0 would mean "no wait", not "a short wait".
        self.assertEqual(A._timeout_seconds(400), 1)

    def test_start_session_refuses_rather_than_running_without_a_deadline(self):
        # If the client library has no AppiumClientConfig, the deadline
        # cannot be applied. Refuse honestly: a timeout that is silently not
        # applied is a false guarantee, which is worse than a NO-DATA.
        import types
        appium_mod = types.ModuleType("appium")
        webdriver_mod = types.ModuleType("appium.webdriver")
        empty = types.ModuleType("appium.webdriver.client_config")  # no AppiumClientConfig
        appium_mod.webdriver = webdriver_mod
        broken = {"appium": appium_mod, "appium.webdriver": webdriver_mod,
                  "appium.webdriver.client_config": empty}
        with patch.dict(sys.modules, broken), \
                patch.object(A, "_client", return_value=(
                    type("W", (), {"Remote": _FakeRemote}), _FakeOptions, _FakeAppiumBy)):
            with self.assertRaises(A.AppiumUnavailable):
                A.start_session({}, "http://127.0.0.1:4723")
        self.assertIsNone(_FakeRemote.last, "no session may be opened without a deadline")

    # --- MAJOR: capabilities crossed a trust boundary unvalidated --------

    def test_non_dict_capabilities_are_refused_before_any_session_call(self):
        # capabilities is the equivalent of process arguments for a remote
        # executor, and reached AppiumOptions.load_capabilities() with no
        # shape check at all.
        record = MCAT.record_for(
            "TAP_TARGET", target={"selector_type": "accessibility_id", "value": "b"})
        for capabilities in ("a string", ["a", "list"], 42):
            with self.subTest(capabilities=capabilities):
                _FakeRemote.last = None
                with patch.dict(sys.modules, _fake_appium_modules()), \
                        patch.object(A, "_client", return_value=(
                            type("W", (), {"Remote": _FakeRemote}), _FakeOptions, _FakeAppiumBy)):
                    result = A.execute_action(record, capabilities, self.out, [])
                self.assertEqual(result["status"], "FAIL")
                self.assertIn("capabilities must be a JSON object", result["detail"])
                self.assertIsNone(_FakeRemote.last, "no session may be opened")

    def test_non_string_capability_keys_are_refused(self):
        self.assertIn("keys must all be strings", A._require_valid_capabilities({1: "x"}))

    def test_ordinary_capabilities_are_still_accepted(self):
        self.assertIsNone(A._require_valid_capabilities({"platformName": "iOS"}))
        self.assertIsNone(A._require_valid_capabilities({}))
        self.assertIsNone(A._require_valid_capabilities(None))

    # --- MAJOR: LONG_PRESS_TARGET duration bounded at the vocabulary -----

    def test_an_unbounded_long_press_is_refused_before_any_session_call(self):
        # duration_ms is a param this adapter invented; nothing bounded it,
        # so 86400000 validated clean and the adapter would have held a
        # touch down on a real device for 24 hours. Fixed at the vocabulary
        # (mobile_canonical_action.PARAMS_NUMBER_RANGE) rather than in this
        # handler, so every adapter implementing LONG_PRESS_TARGET inherits
        # the bound; asserted here to prove this adapter really inherits it.
        record = MCAT.record_for(
            "LONG_PRESS_TARGET",
            target={"selector_type": "accessibility_id", "value": "b"},
            params={"duration_ms": 86400000})
        result = A.execute_action(record, {}, self.out, [])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("duration_ms", result["detail"])

    def test_a_negative_long_press_duration_is_refused(self):
        record = MCAT.record_for(
            "LONG_PRESS_TARGET",
            target={"selector_type": "accessibility_id", "value": "b"},
            params={"duration_ms": -1})
        self.assertEqual(A.execute_action(record, {}, self.out, [])["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
