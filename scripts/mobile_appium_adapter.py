#!/usr/bin/env python3
"""EPIC M3.05 Appium Driver Adapter: translates M3.02's canonical mobile
action vocabulary (mobile-canonical-action-v1) into real Appium WebDriver
protocol calls via the Appium-Python-Client library, self-describing itself
against M3.01's mobile-driver-contract-v1 schema -- same shape as
mobile_native_ios_adapter.py (M3.03) and mobile_visual_fallback_adapter.py
(M3.06): one handler per action in ACTION_HANDLERS, describe() built from
that dict rather than a second hand-typed list, UNSUPPORTED_REASONS naming
the real gap for every action this driver does not implement.

REALITY CHECK ON THIS MACHINE (2026-09-15), per this unit's own brief:
  - `which appium` finds the Node-based Appium CLI/server at
    ~/.local/bin/appium (v3.5.2), but `appium driver list
    --installed` reports no drivers installed, and nothing is listening on
    the default port 4723 (`curl -m 2 http://localhost:4723/status`
    connection-refused). So even the CLI's own server cannot host a real
    iOS/Android session tonight.
  - `pip show Appium-Python-Client` / `python3 -c "import appium"`: not
    installed, ModuleNotFoundError.
  - This session's available MCP tools were checked for anything
    Appium-related: none exist.
So EVERY real network/session call this module can make tonight ends in a
structural NO-DATA, never a fake PASS -- see start_session()/_client() below
and execute_action()'s NO_SESSION_ACTIONS split.

EVERY Appium-Python-Client name this module imports or calls was verified
against the real source at github.com/appium/python-client (master branch,
fetched 2026-09-15), never guessed from memory, because DeepSeek hallucinated
non-existent schema fields for a sibling unit earlier tonight (M3.06). Real,
fetched signatures this module relies on:
  - appium/webdriver/__init__.py: `from .webdriver import WebDriver as Remote`
    (__all__ = ['Remote', 'WebElement']) -- so `from appium import webdriver`
    then `webdriver.Remote(command_executor=..., options=...)` is the real
    public entry point, not a guessed alias.
  - appium/webdriver/webdriver.py: `class WebDriver(Remote, ActionHelpers,
    Activities, Applications, Clipboard, Context, Common, DeviceTime,
    Display, ExecuteDriver, ExecuteMobileCommand, Gsm, HardwareActions,
    ImagesComparison, Keyboard, Location, LogEvent, Logs, Network,
    Performance, Power, RemoteFS, ScreenRecord, Session, Settings, Sms,
    SystemBars)` where `Remote` is selenium's own
    `selenium.webdriver.remote.webdriver.WebDriver` -- confirming
    find_element/click/send_keys/back/quit/get_screenshot_as_file/
    get_window_size/orientation/switch_to.active_element are the real,
    unmodified selenium API, not an Appium reinvention.
  - appium/webdriver/common/appiumby.py: `AppiumBy.ACCESSIBILITY_ID =
    "accessibility id"` (AppiumBy has no plain-text or testid strategy --
    see _locate()'s honest gaps below).
  - appium/options/common/base.py: `AppiumOptions.load_capabilities(self,
    caps: dict) -> Self`.
  - appium/webdriver/extensions/applications.py: `activate_app(self, app_id:
    str) -> Self`, `terminate_app(self, app_id: str, **options) -> bool`,
    `background_app(self, seconds: int) -> Self`.
  - appium/webdriver/extensions/action_helpers.py: `tap(self, positions:
    list[tuple[int, int]], duration: int | None = None) -> Self`,
    `swipe(self, start_x: int, start_y: int, end_x: int, end_y: int,
    duration: int = 0) -> Self`.
  - appium/webdriver/extensions/location.py: `set_location(self, latitude,
    longitude, altitude=None, speed=None, satellites=None) -> Self`.
  - appium/webdriver/extensions/android/network.py: `set_network_connection
    (self, connection_type: int) -> int` -- Android only (module path says
    so), bitmask 0/1/2/4/6 for none/airplane/wifi/data/all.
  - appium/webdriver/extensions/hw_actions.py (HardwareActions): lock,
    unlock, is_locked, shake, touch_id, toggle_touch_id_enrollment,
    finger_print -- confirmed NO home/back/rotate method exists here, which
    is why HOME is UNSUPPORTED below rather than guessed.

Selector-priority convention: accessibility-id-first (this unit's brief; no
docs/plan/MOBILE-PLATFORM-ROADMAP-1.0.18.md section 10.3 exists in this
checkout to override it with something more specific -- checked directly,
not assumed).
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

#: Matches the userinfo segment of a URL (scheme://user:pass@host or just
#: user@host), so it can be stripped from anything that might carry a
#: server_url before that text reaches returned evidence, a log line, or a
#: saved file. Security-review finding: a connection failure interpolated
#: the complete operator-provided server_url, unredacted, into both the
#: raised error and the caller's returned "detail" field; a URL with HTTP
#: basic-auth userinfo therefore leaked credentials into evidence.
_URL_USERINFO_RE = re.compile(r"(://)[^/@\s]+@")


def _redact_url_userinfo(text):
    """Strip `user:pass@` (or `user@`) from every URL-shaped substring in
    `text`. Applied to the formatted server_url AND to str(exc), since the
    underlying client/library exception can also echo the raw URL back."""
    return _URL_USERINFO_RE.sub(r"\1[redacted]@", text)

import mobile_canonical_action as ACT
import mobile_driver_contract as DC
import mobile_workflow as MW

DRIVER_ID = "appium"
DRIVER_NAME = "Appium adapter (WebDriver protocol via Appium-Python-Client)"
ACTION_VOCABULARY_REF = "mobile-canonical-action-v1"
DEFAULT_SERVER_URL = "http://127.0.0.1:4723"

# Loaded once at import, same reasoning as the sibling adapters: fixed repo
# files, and a caller (a future M3.07 router) may drive many actions in a
# loop.
_ACTION_SCHEMA = ACT.CC.load_json(ACT.DEFAULT_SCHEMA, "canonical action schema")
_DRIVER_SCHEMA = DC.CC.load_json(DC.DEFAULT_SCHEMA, "driver contract schema")

#: action_id is caller-controlled data, not a filesystem-safe token -- CAPTURE
#: builds a path from it, so it is sanitized before that use (mirrors
#: mobile_native_ios_adapter.py's own _SAFE_FILENAME).
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]")

#: Matches mobile_canonical_action.py's own coordinates pattern; that module
#: already enforces this shape on any record that reaches here, so this is a
#: second, defensive parse at the point of use, not the first validation.
_COORDINATES_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


#: Deadline applied to this adapter's remote calls when a record names no
#: timeout_ms of its own. The canonical schema deliberately names no default
#: ("this schema does not name a default so one canonical vocabulary never
#: silently encodes one execution backend's timing assumptions"), so the
#: default belongs here, in the backend. Matches mobile_workflow.invoke()'s
#: own 120s, the deadline every subprocess-driven sibling adapter already
#: gets, so one journey does not have two wildly different time budgets
#: depending on which driver executed a step.
DEFAULT_TIMEOUT_MS = 120000


def _timeout_seconds(timeout_ms):
    """Whole seconds for selenium's ClientConfig(timeout=...), which is
    documented in seconds. Falls back to DEFAULT_TIMEOUT_MS for anything
    unusable (absent, non-int, bool -- an int subclass in Python -- zero or
    negative), since a zero or negative socket timeout is not a shorter
    deadline, it is a different and broken mode. Never returns 0: a
    sub-second timeout_ms rounds up to 1s rather than down to no wait."""
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
        timeout_ms = DEFAULT_TIMEOUT_MS
    return max(1, int(round(timeout_ms / 1000.0)))


def _require_valid_capabilities(capabilities):
    """None if `capabilities` is a shape that can safely become a remote
    session-creation payload, else a FAIL reason string. capabilities
    crosses a trust boundary -- it is the equivalent of process arguments
    for a remote executor -- and reached AppiumOptions.load_capabilities()
    with no shape check at all. Operator-supplied via --capabilities today,
    so this is not yet exploitable, but it becomes so the moment a caller
    (M3.07's router) wires capabilities from a journey or plan record."""
    if capabilities is None:
        return None
    if not isinstance(capabilities, dict):
        return ("capabilities must be a JSON object, got %s; refused before any remote "
                "session-creation call" % type(capabilities).__name__)
    for key in capabilities:
        if not isinstance(key, str):
            return ("capabilities keys must all be strings, got %r; refused before any remote "
                    "session-creation call" % (key,))
    return None


class AppiumUnavailable(Exception):
    """Raised, and always caught inside execute_action(), for anything that
    means "no real Appium session tonight": the appium package is not
    importable, or the server refuses the connection/times out. Never a
    guessed pass, and never leaks past execute_action() as a raw
    traceback."""


def _client():
    """Lazy-import the Appium-Python-Client so importing this module (e.g.
    to call describe(), or to validate a record with no live session) never
    requires the package to be installed. Mirrors
    mobile_visual_fallback_adapter.py's _call_vision_model seam: the real
    integration point either works for real or returns one named,
    structural reason, never a crash on import."""
    try:
        from appium import webdriver as appium_webdriver
        from appium.options.common.base import AppiumOptions
        from appium.webdriver.common.appiumby import AppiumBy
    except ImportError as exc:
        raise AppiumUnavailable(
            "Appium-Python-Client not installed (import appium failed: %s)" % exc) from exc
    return appium_webdriver, AppiumOptions, AppiumBy


def start_session(capabilities, server_url=DEFAULT_SERVER_URL, timeout_ms=None):
    """Real session start: appium_webdriver.Remote(command_executor,
    options=AppiumOptions().load_capabilities(capabilities),
    client_config=AppiumClientConfig(remote_server_addr, timeout=seconds)).
    Returns a live driver on success. Raises AppiumUnavailable -- never lets
    a bare exception through -- for a missing client library or an
    unreachable/refusing server: Appium/selenium/urllib3 raise a wide
    variety of exception types for "could not reach or start a session"
    (connection refused, selenium.common.exceptions.WebDriverException and
    its many subclasses, a timeout); none of those types is guessed here,
    all are folded into one honest, named NO-DATA outcome.

    THE DEADLINE IS THE POINT OF THIS SEAM. Every handler that needs a
    session routes through here, so this is the one place a bound on remote
    calls can be applied. Without it a hung Appium server (one that accepts
    the TCP connection and never answers) blocked here forever: no bound, no
    NO-DATA, no evidence, and a journey driven through this adapter could
    hang its whole caller indefinitely. selenium's ClientConfig.timeout
    defaults to socket.getdefaulttimeout(), which is normally None, i.e. no
    timeout at all -- so the deadline has to be passed explicitly, it is
    never inherited."""
    appium_webdriver, AppiumOptions, _ = _client()
    # A second, independent lazy import, deliberately not folded into
    # _client(): same reasoning as _locate()'s selenium By import below, and
    # it keeps _client()'s existing 3-tuple shape (which this module's test
    # suite stubs) unchanged.
    try:
        from appium.webdriver.client_config import AppiumClientConfig
    except ImportError as exc:
        # Refuse rather than silently fall back to an unbounded session: a
        # timeout that is quietly not applied is a false guarantee, which is
        # worse than an honest NO-DATA.
        raise AppiumUnavailable(
            "Appium-Python-Client has no webdriver.client_config.AppiumClientConfig, so no "
            "request deadline can be applied; refusing rather than opening an unbounded "
            "session (%s)" % exc) from exc
    options = AppiumOptions().load_capabilities(capabilities or {})
    timeout_s = _timeout_seconds(timeout_ms)
    try:
        client_config = AppiumClientConfig(remote_server_addr=server_url, timeout=timeout_s)
        return appium_webdriver.Remote(command_executor=server_url, options=options,
                                        client_config=client_config)
    except Exception as exc:
        raise AppiumUnavailable(_redact_url_userinfo(
            "no Appium server reachable at %s within %ss: %s"
            % (server_url, timeout_s, exc))) from exc


def end_session(driver):
    """Teardown. Never raises: an exception while closing a session must
    never mask the result of the action that ran before it, and must never
    stop execute_action()'s finally: block (see there) from completing."""
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


def _parse_coordinates(value):
    match = _COORDINATES_RE.match(value or "")
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


#: Selector types this adapter does NOT translate into a real Appium
#: locator, each with the real reason -- never a guessed predicate/uiautomator
#: query syntax. "coordinates" is not listed here: it is not an element
#: locator at all, and every caller that reaches a target handles it
#: directly before calling _locate().
_SELECTOR_UNSUPPORTED_REASONS = {
    "text": "no single cross-platform 'find by visible text' locator strategy is verified in "
            "AppiumBy; visible-text matching needs a platform-specific predicate/UiSelector query "
            "this adapter does not build without a verified syntax",
    "image": "image-based targets route through the visual fallback adapter (M3.06), not a "
             "literal Appium element locator",
    "testid": "no dedicated Appium locator strategy is verified for 'testid'; many apps expose it "
              "via accessibility_id instead, but this adapter does not guess that app-specific "
              "equivalence",
}


def _locate(AppiumBy, target):
    """Translate a canonical target (selector_type/value) into a real
    (by, value) pair for driver.find_element(by=..., value=...) -- the two
    Appium locator strategies actually verified for this unit tonight:
    accessibility_id (AppiumBy.ACCESSIBILITY_ID, accessibility-id-first per
    this repo's selector-priority convention) and xpath (selenium's own
    By.XPATH, unchanged since Appium's WebDriver subclasses selenium's
    RemoteWebDriver). Returns (pair, None) on success or (None, reason) for
    any selector_type this unit does not translate -- never a guess."""
    selector_type = target.get("selector_type")
    value = target.get("value")
    if selector_type == "accessibility_id":
        return (AppiumBy.ACCESSIBILITY_ID, value), None
    if selector_type == "xpath":
        # A second, independent lazy import (not folded into _client()):
        # this function is also exercised directly, and selenium being
        # absent is its own honest, real reason to decline -- never a
        # crash, exactly like _client()'s own appium import.
        try:
            from selenium.webdriver.common.by import By
        except ImportError as exc:
            return None, "selenium not installed (import failed: %s); xpath selector unavailable" % exc
        return (By.XPATH, value), None
    return None, _SELECTOR_UNSUPPORTED_REASONS.get(
        selector_type, "unrecognized selector_type %r" % (selector_type,))


def _open_app(record, driver, AppiumBy, evidence_root):
    app_id = record["params"]["app_id"]
    driver.activate_app(app_id)
    return {"status": "PASS", "detail": "activated %s" % app_id}


def _deeplink(record, driver, AppiumBy, evidence_root):
    uri = record["params"]["uri"]
    driver.get(uri)
    return {"status": "PASS", "detail": "opened %s via driver.get()" % uri}


def _set_location(record, driver, AppiumBy, evidence_root):
    params = record["params"]
    driver.set_location(params["latitude"], params["longitude"])
    return {"status": "PASS",
            "detail": "set location to %s,%s" % (params["latitude"], params["longitude"])}


def _capture(record, driver, AppiumBy, evidence_root):
    safe_id = _SAFE_FILENAME.sub("_", record.get("action_id") or "capture")[:80] or "capture"
    out_path = Path(evidence_root) / ("%s-capture.png" % safe_id)
    driver.get_screenshot_as_file(str(out_path))
    return {"status": "PASS", "detail": "captured screenshot",
            "screenshot": MW.screenshot_identity(out_path)}


def _tap_target(record, driver, AppiumBy, evidence_root):
    target = record["target"]
    if target.get("selector_type") == "coordinates":
        point = _parse_coordinates(target["value"])
        driver.tap([point])
        return {"status": "PASS", "detail": "tapped coordinates %s" % (point,)}
    located, reason = _locate(AppiumBy, target)
    if located is None:
        return {"status": "UNSUPPORTED", "detail": reason}
    by, value = located
    driver.find_element(by=by, value=value).click()
    return {"status": "PASS", "detail": "tapped element located by %s=%r" % (by, value)}


def _long_press_target(record, driver, AppiumBy, evidence_root):
    target = record["target"]
    duration_ms = (record.get("params") or {}).get("duration_ms", 1000)
    if target.get("selector_type") == "coordinates":
        point = _parse_coordinates(target["value"])
    else:
        located, reason = _locate(AppiumBy, target)
        if located is None:
            return {"status": "UNSUPPORTED", "detail": reason}
        by, value = located
        rect = driver.find_element(by=by, value=value).rect
        point = (rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
    driver.tap([point], duration=int(duration_ms))
    return {"status": "PASS", "detail": "long-pressed %s for %sms" % (point, duration_ms)}


def _type_text(record, driver, AppiumBy, evidence_root):
    text = record["params"]["text"]
    target = record.get("target")
    if isinstance(target, dict):
        located, reason = _locate(AppiumBy, target)
        if located is None:
            return {"status": "UNSUPPORTED", "detail": reason}
        by, value = located
        driver.find_element(by=by, value=value).send_keys(text)
        return {"status": "PASS", "detail": "typed into element located by %s=%r" % (by, value)}
    driver.switch_to.active_element.send_keys(text)
    return {"status": "PASS", "detail": "typed into the currently focused/active element"}


#: Fraction of the window kept as a margin so a swipe never starts or ends
#: flush against an edge (where OS gesture zones live on a real device).
_SWIPE_MARGIN = 0.15


def _swipe(record, driver, AppiumBy, evidence_root):
    direction = record["params"]["direction"]
    size = driver.get_window_size()
    w, h = size["width"], size["height"]
    cx, cy = w / 2, h / 2
    vectors = {
        "up": (cx, h * (1 - _SWIPE_MARGIN), cx, h * _SWIPE_MARGIN),
        "down": (cx, h * _SWIPE_MARGIN, cx, h * (1 - _SWIPE_MARGIN)),
        "left": (w * (1 - _SWIPE_MARGIN), cy, w * _SWIPE_MARGIN, cy),
        "right": (w * _SWIPE_MARGIN, cy, w * (1 - _SWIPE_MARGIN), cy),
    }
    start_x, start_y, end_x, end_y = (int(v) for v in vectors[direction])
    driver.swipe(start_x, start_y, end_x, end_y, duration=200)
    return {"status": "PASS",
            "detail": "swiped %s across the window (%s,%s)->(%s,%s)"
                      % (direction, start_x, start_y, end_x, end_y)}


def _back(record, driver, AppiumBy, evidence_root):
    driver.back()
    return {"status": "PASS",
            "detail": "called driver.back() (Android hardware back; iOS has no true equivalent, "
                      "and XCUITest does not guarantee this acts on anything there)"}


#: portrait_upside_down/landscape_left/landscape_right collapse to the same
#: W3C orientation value as their plain counterpart: the orientation
#: property this adapter uses (selenium's, inherited unmodified by Appium's
#: WebDriver) only distinguishes PORTRAIT/LANDSCAPE, confirmed from the
#: fetched source -- never a guessed finer enum.
_ORIENTATION_MAP = {
    "portrait": "PORTRAIT",
    "portrait_upside_down": "PORTRAIT",
    "landscape": "LANDSCAPE",
    "landscape_left": "LANDSCAPE",
    "landscape_right": "LANDSCAPE",
}


def _rotate(record, driver, AppiumBy, evidence_root):
    orientation = record["params"]["orientation"]
    mapped = _ORIENTATION_MAP.get(orientation)
    if mapped is None:
        return {"status": "UNSUPPORTED", "detail": "unrecognized orientation %r" % orientation}
    driver.orientation = mapped
    detail = "set orientation to %s" % mapped
    if orientation != mapped.lower():
        detail += (" (the orientation property this driver uses only distinguishes "
                    "PORTRAIT/LANDSCAPE, not the finer %r requested)" % orientation)
    return {"status": "PASS", "detail": detail}


#: Android-only bitmask (network.py's own docstring: "Android only"); "slow"
#: has no verified bitmask value -- Appium's set_network_speed is a separate,
#: Android-emulator-only call this unit does not wire in.
_NETWORK_CONDITION_TO_BITMASK = {"offline": 0, "wifi_only": 2, "cellular_only": 4, "online": 6}


def _set_network(record, driver, AppiumBy, evidence_root):
    condition = record["params"]["condition"]
    bitmask = _NETWORK_CONDITION_TO_BITMASK.get(condition)
    if bitmask is None:
        return {"status": "UNSUPPORTED",
                "detail": "%r has no verified Android network_connection bitmask (only offline/"
                          "wifi_only/cellular_only/online do); no set_network_speed value is "
                          "verified for 'slow' tonight" % condition}
    driver.set_network_connection(bitmask)
    return {"status": "PASS",
            "detail": "set network_connection bitmask to %d for %r (Android only -- "
                      "set_network_connection has no iOS support)" % (bitmask, condition)}


def _assert_visible(record, driver, AppiumBy, evidence_root):
    target = record["target"]
    if target.get("selector_type") == "coordinates":
        return {"status": "UNSUPPORTED",
                "detail": "coordinates is a raw screen point, not an element whose visibility "
                          "can be asserted"}
    located, reason = _locate(AppiumBy, target)
    if located is None:
        return {"status": "UNSUPPORTED", "detail": reason}
    by, value = located
    try:
        element = driver.find_element(by=by, value=value)
    except Exception as exc:
        # Real Appium/selenium raise a NoSuchElementException subclass here;
        # not imported by name (see _locate()'s own selenium-absent note) --
        # any lookup failure is an honest FAIL either way.
        return {"status": "FAIL", "detail": "no element located by %s=%r: %s" % (by, value, exc)}
    if element.is_displayed():
        return {"status": "PASS", "detail": "element located by %s=%r is visible" % (by, value)}
    return {"status": "FAIL",
            "detail": "element located by %s=%r exists but is not displayed" % (by, value)}


def _wait_for(record, driver, AppiumBy, evidence_root):
    params = record.get("params") or {}
    duration_ms = params.get("duration_ms")
    if duration_ms is not None:
        time.sleep(duration_ms / 1000.0)
        return {"status": "PASS", "detail": "waited %sms" % duration_ms}
    # A target-based WAIT_FOR (record.get("target") set, no duration_ms) needs
    # a polling wait (selenium.webdriver.support.ui.WebDriverWait), which is
    # real and verified to exist but is not wired into this unit tonight.
    return {"status": "UNSUPPORTED",
            "detail": "target-based WAIT_FOR needs a polling wait "
                      "(selenium.webdriver.support.ui.WebDriverWait) this adapter does not wire "
                      "tonight; only params.duration_ms is implemented"}


def _finish(record, driver, AppiumBy, evidence_root):
    status = record["params"]["status"]
    return {"status": "PASS",
            "detail": "journey finished with status=%s (bookkeeping only, no device call)" % status}


def _need_human(record, driver, AppiumBy, evidence_root):
    return {"status": "PASS", "detail": "escalated to a human: %s" % record.get("reason")}


def _impossible(record, driver, AppiumBy, evidence_root):
    return {"status": "PASS", "detail": "marked impossible: %s" % record.get("reason")}


#: Actions that need no live Appium session at all -- pure bookkeeping,
#: exactly like mobile_native_ios_adapter.py's own _finish/_need_human/
#: _impossible. Kept as a separate set (rather than folding into
#: ACTION_HANDLERS with a "device" argument nobody uses, the sibling
#: adapters' pattern) because unlike a UDID string, starting an Appium
#: session has a real cost and a real failure mode: attempting one before
#: EVERY action, including these three, would turn a working bookkeeping
#: PASS into a false NO-DATA whenever no Appium server exists -- the exact
#: kind of dishonesty this unit is told to avoid, just in the other
#: direction.
NO_SESSION_ACTIONS = frozenset({"FINISH", "NEED_HUMAN", "IMPOSSIBLE"})

#: One handler per action this driver actually implements, session-needing
#: and session-less together -- describe() below builds supported_actions
#: from this dict's keys, never a second, hand-typed list.
ACTION_HANDLERS = {
    "OPEN_APP": _open_app,
    "TAP_TARGET": _tap_target,
    "LONG_PRESS_TARGET": _long_press_target,
    "TYPE_TEXT": _type_text,
    "SWIPE": _swipe,
    "BACK": _back,
    "ROTATE": _rotate,
    "SET_NETWORK": _set_network,
    "SET_LOCATION": _set_location,
    "DEEPLINK": _deeplink,
    "WAIT_FOR": _wait_for,
    "ASSERT_VISIBLE": _assert_visible,
    "CAPTURE": _capture,
    "FINISH": _finish,
    "NEED_HUMAN": _need_human,
    "IMPOSSIBLE": _impossible,
}

#: One specific, real reason per canonical action this driver does NOT
#: implement -- never a generic "not implemented" -- so a caller learns
#: something, and the conformance gauntlet (M3.08) can tell a real gap from
#: a bug. Kept in sync with mobile_canonical_action's action enum by
#: test_mobile_appium_adapter.py (ACTION_HANDLERS | UNSUPPORTED_REASONS ==
#: the full 20-verb vocabulary, both directions).
UNSUPPORTED_REASONS = {
    "SCROLL_TO": "scrolling to an off-screen target needs repeated swipe-and-check-visibility; "
                 "driver.scroll() itself takes two already-located elements, not a single target "
                 "to search for, and this adapter does not implement that search loop.",
    "HOME": "no home-button convenience method is verified in Appium-Python-Client's mixin set "
            "(HardwareActions has lock/unlock/shake/touch_id/finger_print only, no home/back); "
            "Android's own KEYCODE_HOME could go through press_keycode(3), but this driver's "
            "primary target platform (per M3.03's precedent) is iOS, which has no verified "
            "equivalent, so this action is left honestly unsupported rather than half-wired.",
    "SET_PERMISSION": "no dedicated Permissions extension is verified in Appium-Python-Client's "
                       "mixin set; changing app permissions needs execute_script('mobile: "
                       "changePermissions'/'mobile: grantPermissions') with platform-specific, "
                       "driver-plugin-defined parameters this unit did not verify against real "
                       "docs tonight.",
    "ASSERT_STATE": "app-internal state is not observable through the WebDriver protocol; this "
                     "would need an app-specific bridge (a custom mobile: command, or reading "
                     "app state via clipboard/log), none of which is generic or verified here.",
}


#: One risk_class per ACTION_HANDLERS entry (enum values are safe/reversible/
#: destructive/irreversible per docs/schema/mobile-driver-contract-v1.json).
#: State-changing actions are classed "reversible", never "destructive"/
#: "irreversible": this driver has no app-specific knowledge of what a given
#: tap or swipe actually does inside the app under test, so it cannot
#: honestly claim a finer class than "changes state, generically undoable by
#: navigating back" -- describe() asserts this stays in lockstep with
#: ACTION_HANDLERS.
_RISK_CLASSES = {
    "OPEN_APP": "reversible",
    "TAP_TARGET": "reversible",
    "LONG_PRESS_TARGET": "reversible",
    "TYPE_TEXT": "reversible",
    "SWIPE": "reversible",
    "BACK": "reversible",
    "ROTATE": "reversible",
    "SET_NETWORK": "reversible",
    "SET_LOCATION": "reversible",
    "DEEPLINK": "reversible",
    "WAIT_FOR": "safe",
    "ASSERT_VISIBLE": "safe",
    "CAPTURE": "safe",
    "FINISH": "safe",
    "NEED_HUMAN": "safe",
    "IMPOSSIBLE": "safe",
}


def describe():
    """This driver's self-description, checked against
    docs/schema/mobile-driver-contract-v1.json by main()'s "describe"
    subcommand before it is ever printed, and by the test suite. Built from
    ACTION_HANDLERS/_RISK_CLASSES, never a second hand-typed action list, so
    the description and the real implementation cannot silently drift."""
    assert set(_RISK_CLASSES) == set(ACTION_HANDLERS), \
        "_RISK_CLASSES has drifted from ACTION_HANDLERS"
    supported = sorted(ACTION_HANDLERS)
    return {
        "schema_version": "mobile-driver-contract-v1",
        "driver_id": DRIVER_ID,
        "driver_name": DRIVER_NAME,
        "action_vocabulary_ref": ACTION_VOCABULARY_REF,
        "supported_actions": supported,
        "platforms": ["ios", "android"],
        "device_modes": ["simulator", "real_device"],
        "remote_sessions_supported": True,
        "observations": ["screenshot"],
        "deterministic_selector_support": True,
        "visual_grounding_support": False,
        "risk_classes": [{"action": a, "risk_class": _RISK_CLASSES[a]} for a in supported],
    }


def execute_action(record, capabilities, evidence_root, stages, server_url=DEFAULT_SERVER_URL):
    """Validate `record` against mobile-canonical-action-v1 first (refusing,
    with no session ever started, on any problem), then dispatch to the
    matching handler, or return a structural UNSUPPORTED result -- never a
    silent no-op -- when this driver has none.

    Session lifecycle, the part of this unit's brief called out for
    adversarial self-review ("hunt specifically for a session-teardown path
    that leaks a live Appium session on an exception"): `driver` starts as
    None; a session is only ever assigned to it after start_session()
    returns successfully; and the outer try/finally guarantees
    end_session(driver) runs on every exit path from the point a session
    might exist onward -- a handler raising, a NoSuchElementException, an
    OSError creating evidence_root, or any other exception a real
    Appium/selenium call can raise. There is exactly one place a session is
    opened (start_session, inside the try) and exactly one place it is
    closed (the finally below), so a session cannot be opened twice or
    leaked past this function."""
    if not isinstance(record, dict):
        return {"action_id": None, "action": None, "status": "FAIL",
                "detail": "canonical action record must be a JSON object, got %s"
                          % type(record).__name__}

    action = record.get("action")
    action_id = record.get("action_id")
    problems = ACT.check(record, _ACTION_SCHEMA)
    if problems:
        return {"action_id": action_id, "action": action, "status": "FAIL",
                "detail": "invalid canonical action record: " + "; ".join(problems)}

    handler = ACTION_HANDLERS.get(action)
    if handler is None:
        return {"action_id": action_id, "action": action, "status": "UNSUPPORTED",
                "detail": UNSUPPORTED_REASONS.get(
                    action, "no appium implementation for %s" % action)}

    driver = None
    try:
        if action in NO_SESSION_ACTIONS:
            result = handler(record, None, None, Path(evidence_root))
        else:
            reason = _require_valid_capabilities(capabilities)
            if reason is not None:
                return {"action_id": action_id, "action": action, "status": "FAIL",
                        "detail": reason}
            try:
                _, _, AppiumBy = _client()
                driver = start_session(capabilities, server_url,
                                        timeout_ms=record.get("timeout_ms"))
            except AppiumUnavailable as exc:
                return {"action_id": action_id, "action": action, "status": "NO-DATA",
                        "detail": str(exc)}
            evidence_root = Path(evidence_root)
            evidence_root.mkdir(parents=True, exist_ok=True)
            result = handler(record, driver, AppiumBy, evidence_root)
    except MW.Refusal as exc:
        result = {"status": getattr(exc, "status", "FAIL"), "detail": str(exc)}
    except OSError as exc:
        result = {"status": "NO-DATA", "detail": "evidence directory unavailable: %s" % exc}
    except Exception as exc:
        # A real Appium/selenium call can raise many exception types this
        # module does not enumerate one by one (WebDriverException and its
        # many subclasses, a NoSuchElementException from a handler that did
        # not already catch it, a mid-session network error). Caught here,
        # last resort, so a live session is never leaked past this function
        # (the finally: below still runs) and a caller never sees a raw
        # traceback in place of a structured result.
        result = {"status": "FAIL", "detail": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        end_session(driver)

    result.setdefault("action_id", action_id)
    result.setdefault("action", action)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("describe")
    run = sub.add_parser("run")
    run.add_argument("--record", required=True, help="path to a mobile-canonical-action-v1 JSON record")
    run.add_argument("--server", default=DEFAULT_SERVER_URL, help="Appium server URL")
    run.add_argument("--capabilities", default=None,
                      help="path to a JSON file of Appium session capabilities (default: {})")
    run.add_argument("--out", required=True, help="evidence directory (created if needed)")
    args = parser.parse_args(argv)

    if args.command == "describe":
        self_description = describe()
        problems = DC.check(self_description, _DRIVER_SCHEMA)
        if problems:
            print("mobile_appium_adapter: FAIL: self-description violates mobile-driver-contract-v1:")
            for p in problems:
                print(" -", p)
            return 1
        print(json.dumps(self_description, indent=2, sort_keys=True))
        return 0

    try:
        record = ACT.CC.load_json(args.record, "canonical action record")
    except ACT.CC.NoData as exc:
        print("mobile_appium_adapter: NO-DATA: %s" % exc)
        return 2
    capabilities = {}
    if args.capabilities:
        try:
            capabilities = ACT.CC.load_json(args.capabilities, "Appium capabilities")
        except ACT.CC.NoData as exc:
            print("mobile_appium_adapter: NO-DATA: %s" % exc)
            return 2
    stages = []
    result = execute_action(record, capabilities, Path(args.out), stages, server_url=args.server)
    result["stages"] = stages
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return {"PASS": 0, "FAIL": 1, "UNSUPPORTED": 1, "NO-DATA": 2}.get(result["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
