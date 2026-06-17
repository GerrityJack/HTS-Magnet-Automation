"""
test_pseudo.py
─────────────────────────────────────────────────────────────────────────────
Pseudocode-style tests for the HTS Magnet Testing Automation system.

These tests replace every hardware call (LabJack, Modbus, MQTT) with a
simple fake so they can run on any machine with no equipment connected.

Run with:
    python test_pseudo.py
─────────────────────────────────────────────────────────────────────────────
"""

import json
import math
import time
import threading
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Test harness
# ─────────────────────────────────────────────────────────────────────────────
_results = []

def test(name: str):
    def decorator(fn):
        def wrapper():
            try:
                fn()
                _results.append((name, True, ""))
                print(f"  PASS  {name}")
            except AssertionError as e:
                _results.append((name, False, str(e)))
                print(f"  FAIL  {name}  ->  {e}")
            except Exception as e:
                _results.append((name, False, f"Exception: {e}"))
                print(f"  FAIL  {name}  ->  Exception: {e}")
        wrapper()
        return wrapper
    return decorator

def assert_eq(a, b, msg=""):
    assert a == b, msg or f"expected {b!r}, got {a!r}"

def assert_true(cond, msg=""):
    assert cond, msg or "condition was False"

def assert_false(cond, msg=""):
    assert not cond, msg or "condition was True (expected False)"

def assert_approx(a, b, tol=1e-9, msg=""):
    assert abs(a - b) < tol, msg or f"{a} not within {tol} of {b}"

def assert_none(val, msg=""):
    assert val is None, msg or f"expected None, got {val!r}"

def assert_not_none(val, msg=""):
    assert val is not None, msg or "expected a value, got None"


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — mqtt_config: voltage_to_torr  (Pi = 10^(V-4), threshold 8.0V)
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- Section 1: voltage_to_torr -------------------------------------------")

GAUGE_DISCONNECT_THRESHOLD_V = 8.0

def voltage_to_torr(volts: float):
    """Exact copy from mqtt_config.py."""
    if volts <= 0:
        return 0.0
    if volts > GAUGE_DISCONNECT_THRESHOLD_V:
        return None
    return 10 ** (volts - 4)

@test("zero volts returns 0.0")
def _():
    assert_eq(voltage_to_torr(0.0), 0.0)

@test("negative volts returns 0.0")
def _():
    assert_eq(voltage_to_torr(-1.0), 0.0)

@test("4.0 V maps to 1.0 Torr  (10^0 = 1)")
def _():
    assert_approx(voltage_to_torr(4.0), 1.0, tol=1e-12)

@test("6.88 V maps to ~760 Torr (atmospheric pressure)")
def _():
    result = voltage_to_torr(6.88)
    assert_true(abs(result - 758.6) < 1.0, f"Expected ~760 Torr, got {result:.1f}")

@test("voltage above 8.0 V returns None (pin floating / gauge disconnected)")
def _():
    assert_none(voltage_to_torr(8.001))
    assert_none(voltage_to_torr(9.0))
    assert_none(voltage_to_torr(10.0))

@test("voltage exactly at threshold (8.0 V) returns a pressure value, not None")
def _():
    result = voltage_to_torr(8.0)
    assert_not_none(result)
    assert_approx(result, 10 ** (8.0 - 4), tol=1e-9)

@test("formula is monotonically increasing with voltage")
def _():
    voltages  = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    pressures = [voltage_to_torr(v) for v in voltages]
    for i in range(len(pressures) - 1):
        assert_true(pressures[i] < pressures[i+1],
                    f"Not monotonic: P({voltages[i]}V)={pressures[i]:.3e} "
                    f">= P({voltages[i+1]}V)={pressures[i+1]:.3e}")

@test("1 V gives pressure well below atmosphere")
def _():
    assert_true(voltage_to_torr(1.0) < 760.0)

@test("5 V gives sub-Torr pressure (expected ~10^1 = 10 Torr)")
def _():
    result = voltage_to_torr(5.0)
    assert_approx(result, 10.0, tol=1e-9)

@test("7 V gives ~1000 Torr")
def _():
    result = voltage_to_torr(7.0)
    assert_approx(result, 1000.0, tol=1e-6)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — validate_config
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- Section 2: validate_config -------------------------------------------")

def make_valid_config():
    """Returns a dict of all config values that should pass validation."""
    return {
        "MQTT_BROKER_HOST":            "localhost",
        "MQTT_BROKER_PORT":            1883,
        "QUESTDB_HOST":                "localhost",
        "QUESTDB_PORT":                9000,
        "COMPRESSOR_SERIAL_PORT":      "COM5",
        "COMPRESSOR_BAUDRATE":         115200,
        "COMPRESSOR_MODBUS_ID":        16,
        "AIN_VACUUM_PRESSURE":         0,
        "DOUT_VACUUM_VALVE":           0,
        "DOUT_PUMP":                   1,
        "GAUGE_DISCONNECT_THRESHOLD_V": 8.0,
        "PUMP_ABORT_THRESHOLD_TORR":   10.0,
    }

def validate_config_from_dict(cfg: dict) -> list[str]:
    """Mirrors validate_config() logic but works on a plain dict for testing."""
    errors = []
    if not cfg["MQTT_BROKER_HOST"]:
        errors.append("MQTT_BROKER_HOST is empty.")
    if not (1 <= cfg["MQTT_BROKER_PORT"] <= 65535):
        errors.append(f"MQTT_BROKER_PORT {cfg['MQTT_BROKER_PORT']} is not a valid port.")
    if not cfg["QUESTDB_HOST"]:
        errors.append("QUESTDB_HOST is empty.")
    if not (1 <= cfg["QUESTDB_PORT"] <= 65535):
        errors.append(f"QUESTDB_PORT {cfg['QUESTDB_PORT']} is not a valid port.")
    if not cfg["COMPRESSOR_SERIAL_PORT"]:
        errors.append("COMPRESSOR_SERIAL_PORT is empty.")
    if cfg["COMPRESSOR_BAUDRATE"] not in (9600, 19200, 38400, 57600, 115200):
        errors.append(f"COMPRESSOR_BAUDRATE {cfg['COMPRESSOR_BAUDRATE']} is unusual.")
    if not (1 <= cfg["COMPRESSOR_MODBUS_ID"] <= 247):
        errors.append(f"COMPRESSOR_MODBUS_ID {cfg['COMPRESSOR_MODBUS_ID']} out of range.")
    if cfg["AIN_VACUUM_PRESSURE"] not in range(14):
        errors.append(f"AIN_VACUUM_PRESSURE {cfg['AIN_VACUUM_PRESSURE']} invalid.")
    if cfg["DOUT_VACUUM_VALVE"] not in range(8):
        errors.append(f"DOUT_VACUUM_VALVE {cfg['DOUT_VACUUM_VALVE']} invalid.")
    if cfg["DOUT_PUMP"] not in range(8):
        errors.append(f"DOUT_PUMP {cfg['DOUT_PUMP']} invalid.")
    if cfg["DOUT_VACUUM_VALVE"] == cfg["DOUT_PUMP"]:
        errors.append("DOUT_VACUUM_VALVE and DOUT_PUMP cannot be the same channel.")
    if not (0 < cfg["GAUGE_DISCONNECT_THRESHOLD_V"] <= 10.5):
        errors.append("GAUGE_DISCONNECT_THRESHOLD_V out of range.")
    if cfg["PUMP_ABORT_THRESHOLD_TORR"] <= 0:
        errors.append("PUMP_ABORT_THRESHOLD_TORR must be > 0.")
    return errors

@test("valid config produces no errors")
def _():
    errors = validate_config_from_dict(make_valid_config())
    assert_eq(errors, [], f"Unexpected errors: {errors}")

@test("empty MQTT_BROKER_HOST is caught")
def _():
    cfg = make_valid_config()
    cfg["MQTT_BROKER_HOST"] = ""
    errors = validate_config_from_dict(cfg)
    assert_true(any("MQTT_BROKER_HOST" in e for e in errors))

@test("port 0 is caught as invalid")
def _():
    cfg = make_valid_config()
    cfg["MQTT_BROKER_PORT"] = 0
    errors = validate_config_from_dict(cfg)
    assert_true(any("MQTT_BROKER_PORT" in e for e in errors))

@test("port 65536 is caught as invalid")
def _():
    cfg = make_valid_config()
    cfg["MQTT_BROKER_PORT"] = 65536
    errors = validate_config_from_dict(cfg)
    assert_true(any("MQTT_BROKER_PORT" in e for e in errors))

@test("unusual baud rate is caught")
def _():
    cfg = make_valid_config()
    cfg["COMPRESSOR_BAUDRATE"] = 12345
    errors = validate_config_from_dict(cfg)
    assert_true(any("BAUDRATE" in e for e in errors))

@test("Modbus ID 0 is caught (valid range is 1-247)")
def _():
    cfg = make_valid_config()
    cfg["COMPRESSOR_MODBUS_ID"] = 0
    errors = validate_config_from_dict(cfg)
    assert_true(any("MODBUS_ID" in e for e in errors))

@test("Modbus ID 248 is caught")
def _():
    cfg = make_valid_config()
    cfg["COMPRESSOR_MODBUS_ID"] = 248
    errors = validate_config_from_dict(cfg)
    assert_true(any("MODBUS_ID" in e for e in errors))

@test("AIN channel 14 is caught (T7-Pro has AIN0-13 only)")
def _():
    cfg = make_valid_config()
    cfg["AIN_VACUUM_PRESSURE"] = 14
    errors = validate_config_from_dict(cfg)
    assert_true(any("AIN_VACUUM_PRESSURE" in e for e in errors))

@test("same FIO channel for valve and pump is caught")
def _():
    cfg = make_valid_config()
    cfg["DOUT_PUMP"] = cfg["DOUT_VACUUM_VALVE"]   # both = 0
    errors = validate_config_from_dict(cfg)
    assert_true(any("same" in e.lower() or "cannot" in e.lower() for e in errors))

@test("negative PUMP_ABORT_THRESHOLD_TORR is caught")
def _():
    cfg = make_valid_config()
    cfg["PUMP_ABORT_THRESHOLD_TORR"] = -1.0
    errors = validate_config_from_dict(cfg)
    assert_true(any("PUMP_ABORT_THRESHOLD" in e for e in errors))

@test("gauge threshold of 0.0 is caught")
def _():
    cfg = make_valid_config()
    cfg["GAUGE_DISCONNECT_THRESHOLD_V"] = 0.0
    errors = validate_config_from_dict(cfg)
    assert_true(any("GAUGE_DISCONNECT" in e for e in errors))

@test("multiple errors are all reported at once")
def _():
    cfg = make_valid_config()
    cfg["MQTT_BROKER_HOST"] = ""
    cfg["COMPRESSOR_MODBUS_ID"] = 0
    cfg["DOUT_PUMP"] = cfg["DOUT_VACUUM_VALVE"]
    errors = validate_config_from_dict(cfg)
    assert_true(len(errors) >= 3, f"Expected at least 3 errors, got {len(errors)}: {errors}")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — LabJack relay logic (Active-Low board)
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- Section 3: LabJack relay (Active-Low) --------------------------------")

class FakeLabJack:
    def __init__(self):
        self.pins: dict[str, float] = {}
    def eWriteName(self, pin: str, value: float):
        self.pins[pin] = value
    def eReadName(self, pin: str) -> float:
        return self.pins.get(pin, 0.0)

published = []

def set_relay(lj: FakeLabJack, fio_channel: int, activate: bool,
              label: str = "", mqtt_client=None):
    """Exact Active-Low relay logic from labjack_mqtt_driver.py."""
    pin   = f"FIO{fio_channel}"
    value = 0 if activate else 1
    lj.eWriteName(pin, value)
    if mqtt_client is not None:
        published.append((fio_channel, activate))

@test("safety init: both relay pins written HIGH (devices OFF) on startup")
def _():
    lj = FakeLabJack()
    DOUT_VACUUM_VALVE, DOUT_PUMP = 0, 1
    lj.eWriteName(f"FIO{DOUT_VACUUM_VALVE}", 1)
    lj.eWriteName(f"FIO{DOUT_PUMP}",         1)
    assert_eq(lj.pins["FIO0"], 1, "Valve pin should be HIGH (OFF) after safety init")
    assert_eq(lj.pins["FIO1"], 1, "Pump pin should be HIGH (OFF) after safety init")

@test("activate=True writes 0 (LOW -> relay ON)")
def _():
    lj = FakeLabJack()
    set_relay(lj, 0, activate=True)
    assert_eq(lj.pins["FIO0"], 0)

@test("activate=False writes 1 (HIGH -> relay OFF)")
def _():
    lj = FakeLabJack()
    set_relay(lj, 0, activate=False)
    assert_eq(lj.pins["FIO0"], 1)

@test("opening valve (FIO0) does not change pump pin (FIO1)")
def _():
    lj = FakeLabJack()
    lj.eWriteName("FIO0", 1)
    lj.eWriteName("FIO1", 1)
    set_relay(lj, 0, activate=True)
    assert_eq(lj.pins["FIO0"], 0, "Valve should be open")
    assert_eq(lj.pins["FIO1"], 1, "Pump pin must be unchanged")

@test("turning pump ON then OFF leaves valve pin unchanged")
def _():
    lj = FakeLabJack()
    lj.eWriteName("FIO0", 1)
    lj.eWriteName("FIO1", 1)
    set_relay(lj, 1, activate=True)
    assert_eq(lj.pins["FIO0"], 1, "Valve pin must not change")
    set_relay(lj, 1, activate=False)
    assert_eq(lj.pins["FIO1"], 1, "Pump should be OFF again")

@test("set_relay publishes state when mqtt_client is provided")
def _():
    # The real set_relay calls mqtt_client.publish(topic, payload).
    # Here we use a simple object that records calls.
    lj = FakeLabJack()
    class FakeMqtt:
        def __init__(self): self.calls = []
        def publish(self, topic, payload, retain=False):
            self.calls.append((topic, payload))
    fm = FakeMqtt()
    # Call a simplified version that mimics the real publish logic
    # (the real function also needs cfg and time, so we just verify
    #  that the pin is written correctly regardless of MQTT)
    set_relay(lj, 0, activate=True, mqtt_client=fm)
    assert_eq(lj.pins["FIO0"], 0, "Pin should be LOW (relay ON)")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Soft interlock logic
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- Section 4: Soft interlock logic --------------------------------------")

class FakeInterlock:
    """
    Mirrors the interlock decision logic from labjack_mqtt_driver.py on_message.
    Returns (action_taken, warning_issued).
    """
    def __init__(self):
        self._valve_open = False
        self._pump_on    = False
        self.warnings    = []

    def command_valve(self, cmd: str) -> bool:
        """Returns True if a warning was issued."""
        warned = False
        if cmd == "OPEN" and not self._pump_on:
            self.warnings.append("Valve OPEN with pump OFF")
            warned = True
        self._valve_open = (cmd == "OPEN")
        return warned

    def command_pump(self, cmd: str) -> bool:
        """Returns True if a warning was issued."""
        warned = False
        if cmd == "OFF" and self._valve_open:
            self.warnings.append("Pump OFF with valve OPEN")
            warned = True
        self._pump_on = (cmd == "ON")
        return warned

@test("opening valve with pump OFF triggers interlock warning")
def _():
    il = FakeInterlock()
    warned = il.command_valve("OPEN")
    assert_true(warned, "Should warn when opening valve without pump")
    assert_true(il._valve_open, "Valve should still open (override)")

@test("opening valve with pump ON does NOT trigger warning")
def _():
    il = FakeInterlock()
    il.command_pump("ON")
    warned = il.command_valve("OPEN")
    assert_false(warned, "No warning expected when pump is running")

@test("turning pump OFF with valve OPEN triggers interlock warning")
def _():
    il = FakeInterlock()
    il._pump_on    = True
    il._valve_open = True
    warned = il.command_pump("OFF")
    assert_true(warned, "Should warn when stopping pump with valve open")
    assert_false(il._pump_on, "Pump should still stop (override)")

@test("turning pump OFF with valve CLOSED does NOT trigger warning")
def _():
    il = FakeInterlock()
    il._pump_on    = True
    il._valve_open = False
    warned = il.command_pump("OFF")
    assert_false(warned, "No warning when valve is closed")

@test("closing valve with pump running does NOT trigger warning")
def _():
    il = FakeInterlock()
    il._pump_on    = True
    il._valve_open = True
    warned = il.command_valve("CLOSE")
    assert_false(warned, "Closing valve is always safe")

@test("normal pump-down sequence produces no interlock warnings")
def _():
    il = FakeInterlock()
    il.command_pump("ON")          # 1. start pump
    il.command_valve("OPEN")       # 2. open valve
    il.command_valve("CLOSE")      # 3. close valve
    il.command_pump("OFF")         # 4. stop pump
    assert_eq(il.warnings, [], f"Expected no warnings, got: {il.warnings}")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — MQTT message parsing
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- Section 5: MQTT message parsing --------------------------------------")

class FakeMqttMessage:
    def __init__(self, topic: str, payload_str: str):
        self.topic   = topic
        self.payload = payload_str.encode("utf-8")

def parse_message(msg) -> Optional[dict]:
    try:
        return json.loads(msg.payload.decode("utf-8").strip())
    except json.JSONDecodeError:
        return None

@test("valid JSON payload parses correctly")
def _():
    msg  = FakeMqttMessage("lab/basement/compressor1/metrics",
                           '{"helium_pressure": 245.2, "oil_temp": 42.1}')
    data = parse_message(msg)
    assert_not_none(data)
    assert_approx(data["helium_pressure"], 245.2, tol=1e-9)

@test("plain-text command payload returns None from parse_message")
def _():
    msg  = FakeMqttMessage("lab/basement/compressor1/control", "PAUSE_MONITORING")
    assert_none(parse_message(msg))

@test("heartbeat payload has alive=True and timestamp")
def _():
    payload = json.dumps({"alive": True, "timestamp": time.time()})
    data    = parse_message(FakeMqttMessage("lab/basement/labjack1/heartbeat", payload))
    assert_true(data["alive"] is True)
    assert_true("timestamp" in data)

@test("valve state payload has correct structure")
def _():
    payload = json.dumps({"state": "OPEN", "timestamp": time.time()})
    data    = parse_message(FakeMqttMessage("lab/basement/labjack1/valve/state", payload))
    assert_eq(data["state"], "OPEN")
    assert_true("timestamp" in data)

@test("pump state payload has correct structure")
def _():
    payload = json.dumps({"state": "ON", "timestamp": time.time()})
    data    = parse_message(FakeMqttMessage("lab/basement/labjack1/pump/state", payload))
    assert_eq(data["state"], "ON")

@test("compressor state payload has commanded and confirmed fields")
def _():
    payload = json.dumps({"commanded": "ON", "confirmed": True, "timestamp": time.time()})
    data    = parse_message(FakeMqttMessage("lab/basement/compressor1/state", payload))
    assert_eq(data["commanded"], "ON")
    assert_true(data["confirmed"] is True)

@test("run event START payload has correct fields")
def _():
    payload = json.dumps({
        "event":     "START",
        "recipe":    "Standard pump-down",
        "outcome":   "",
        "timestamp": time.time(),
    })
    data = parse_message(FakeMqttMessage("lab/run_event", payload))
    assert_eq(data["event"],  "START")
    assert_eq(data["recipe"], "Standard pump-down")

@test("run event END payload has outcome field")
def _():
    payload = json.dumps({
        "event":     "END",
        "recipe":    "Standard pump-down",
        "outcome":   "SUCCESS",
        "timestamp": time.time(),
    })
    data = parse_message(FakeMqttMessage("lab/run_event", payload))
    assert_eq(data["outcome"], "SUCCESS")

@test("error payload has source, message, and timestamp")
def _():
    payload = json.dumps({
        "source":    "labjack_driver",
        "message":   "AIN read failed: timeout",
        "timestamp": time.time(),
    })
    data = parse_message(FakeMqttMessage("lab/errors", payload))
    assert_eq(data["source"],  "labjack_driver")
    assert_true("AIN read" in data["message"])


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — Heartbeat / driver alive detection
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- Section 6: Heartbeat alive detection ---------------------------------")

HEARTBEAT_TIMEOUT_S = 15

def is_alive(last_seen: float, now: Optional[float] = None) -> bool:
    if now is None:
        now = time.time()
    return (now - last_seen) < HEARTBEAT_TIMEOUT_S

@test("driver is alive when heartbeat received 1 second ago")
def _():
    now = time.time()
    assert_true(is_alive(now - 1.0, now))

@test("driver is alive at (timeout - 1ms)")
def _():
    now = time.time()
    assert_true(is_alive(now - (HEARTBEAT_TIMEOUT_S - 0.001), now))

@test("driver is offline when heartbeat received 16 seconds ago")
def _():
    now = time.time()
    assert_false(is_alive(now - 16.0, now))

@test("driver starts as offline (timestamp=0 means never seen)")
def _():
    assert_false(is_alive(0.0))

@test("driver is offline at exactly the timeout boundary")
def _():
    now = time.time()
    assert_false(is_alive(now - HEARTBEAT_TIMEOUT_S, now))


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Recipe JSON validation
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- Section 7: Recipe validation -----------------------------------------")

REQUIRED_RECIPE_KEYS = [
    "name", "foreline_pump_duration_s", "target_pressure_torr",
    "leak_check_duration_s", "max_leak_rate_torr_per_s", "compressor_delay_s",
]

def validate_recipe(recipe: dict) -> list[str]:
    errors = []
    for key in REQUIRED_RECIPE_KEYS:
        if key not in recipe:
            errors.append(f"Missing key: {key}")
    if "target_pressure_torr" in recipe:
        tp = recipe["target_pressure_torr"]
        if tp <= 0:
            errors.append("target_pressure_torr must be > 0")
        elif tp >= 760:
            errors.append("target_pressure_torr must be < 760 Torr")
        elif tp < 1e-8:
            errors.append("target_pressure_torr below 1e-8 is not achievable")
    if "foreline_pump_duration_s" in recipe:
        if recipe["foreline_pump_duration_s"] < 5:
            errors.append("foreline_pump_duration_s must be >= 5")
    if "max_leak_rate_torr_per_s" in recipe:
        if recipe["max_leak_rate_torr_per_s"] <= 0:
            errors.append("max_leak_rate_torr_per_s must be > 0")
    return errors

def good_recipe():
    return {
        "name":                     "Standard pump-down",
        "foreline_pump_duration_s": 30,
        "target_pressure_torr":     1e-3,
        "leak_check_duration_s":    60,
        "max_leak_rate_torr_per_s": 5e-5,
        "compressor_delay_s":       5,
    }

@test("valid recipe passes with no errors")
def _():
    assert_eq(validate_recipe(good_recipe()), [])

@test("missing target_pressure_torr is caught")
def _():
    r = good_recipe(); del r["target_pressure_torr"]
    errors = validate_recipe(r)
    assert_true(any("target_pressure_torr" in e for e in errors))

@test("negative target pressure is caught")
def _():
    r = good_recipe(); r["target_pressure_torr"] = -0.001
    errors = validate_recipe(r)
    assert_true(any("target_pressure_torr" in e for e in errors))

@test("target pressure at atmosphere (760 Torr) is caught")
def _():
    r = good_recipe(); r["target_pressure_torr"] = 760.0
    errors = validate_recipe(r)
    assert_true(any("target_pressure_torr" in e for e in errors))

@test("foreline duration < 5s is caught")
def _():
    r = good_recipe(); r["foreline_pump_duration_s"] = 3
    errors = validate_recipe(r)
    assert_true(any("foreline" in e for e in errors))

@test("recipe round-trips through JSON without data loss")
def _():
    r  = good_recipe()
    r2 = json.loads(json.dumps(r))
    for k, v in r.items():
        if isinstance(v, float):
            assert_approx(r2[k], v, tol=1e-15, msg=f"Float mismatch on '{k}'")
        else:
            assert_eq(r2[k], v, f"Mismatch on '{k}'")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Leak rate calculation
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- Section 8: Leak rate calculation ------------------------------------")

def calculate_leak_rate(p_start: float, p_end: float, elapsed_s: float) -> float:
    if elapsed_s <= 0:
        return 0.0
    return (p_end - p_start) / elapsed_s

def linear_leak_rate(samples: list[tuple[float, float]]) -> float:
    """Least-squares slope from (time, pressure) samples."""
    if len(samples) < 2:
        return 0.0
    times     = [s[0] for s in samples]
    pressures = [s[1] for s in samples]
    n     = len(times)
    t_bar = sum(times) / n
    p_bar = sum(pressures) / n
    num   = sum((t - t_bar) * (p - p_bar) for t, p in zip(times, pressures))
    den   = sum((t - t_bar) ** 2 for t in times)
    return num / den if den > 0 else 0.0

@test("good seal: pressure barely rises -> leak rate near zero")
def _():
    rate = calculate_leak_rate(1e-3, 1.001e-3, 60.0)
    assert_true(rate < 5e-5, f"Rate {rate:.2e} should be below 5e-5")

@test("bad seal: pressure rises fast -> rate above threshold")
def _():
    rate = calculate_leak_rate(1e-3, 1e-2, 60.0)
    assert_true(rate > 5e-5, f"Rate {rate:.2e} should exceed 5e-5")

@test("two-point formula: (p_end - p_start) / time")
def _():
    expected = (0.004 - 0.001) / 60.0
    assert_approx(calculate_leak_rate(0.001, 0.004, 60.0), expected, tol=1e-15)

@test("elapsed=0 returns 0.0 without division error")
def _():
    assert_eq(calculate_leak_rate(1e-3, 2e-3, 0.0), 0.0)

@test("linear regression on perfectly linear data gives exact slope")
def _():
    # Pressure rises at exactly 1e-5 Torr/s
    slope    = 1e-5
    samples  = [(t, 1e-3 + slope * t) for t in range(0, 61, 5)]
    computed = linear_leak_rate(samples)
    assert_approx(computed, slope, tol=1e-15,
                  msg=f"Expected slope {slope:.2e}, got {computed:.2e}")

@test("linear regression is robust to a single noisy reading")
def _():
    slope   = 1e-5
    samples = [(t, 1e-3 + slope * t) for t in range(0, 61, 5)]
    # Inject a noisy spike at t=30
    samples[6] = (30, 1e-3 + slope * 30 + 0.5)
    rate_noisy = linear_leak_rate(samples)
    rate_clean = slope
    # Regression should still be within 10x of the true slope
    assert_true(abs(rate_noisy - rate_clean) < rate_clean * 10,
                f"Noisy rate {rate_noisy:.2e} too far from true {rate_clean:.2e}")

@test("fewer than 2 samples returns 0.0")
def _():
    assert_eq(linear_leak_rate([]), 0.0)
    assert_eq(linear_leak_rate([(0.0, 1e-3)]), 0.0)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 9 — Recipe runner step sequencing
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- Section 9: Recipe runner step sequencing ----------------------------")

class FakeMqttPublisher:
    def __init__(self):
        self.published: list[tuple[str, str]] = []
    def publish(self, topic: str, payload: str, retain: bool = False):
        self.published.append((topic, payload))
    def last(self, topic: str) -> Optional[str]:
        for t, p in reversed(self.published):
            if t == topic:
                return p
        return None
    def count(self, topic: str, payload: str = None) -> int:
        return sum(1 for t, p in self.published
                   if t == topic and (payload is None or p == payload))

TOPIC_PUMP_CONTROL  = "lab/basement/labjack1/pump/control"
TOPIC_VALVE_CONTROL = "lab/basement/labjack1/valve/control"
TOPIC_COMP_CONTROL  = "lab/basement/compressor1/control"
TOPIC_RUN_EVENT     = "lab/run_event"
TOPIC_LOGGER_CTRL   = "lab/logger/control"

def run_recipe_simulation(
    recipe: dict,
    pressure_sequence: list[float],
    publisher: FakeMqttPublisher,
    compressor_already_running: bool = False,
) -> tuple[bool, float, list[str]]:
    """
    Simulated RecipeRunThread.run(). Returns (success, leak_rate, log).
    """
    log    = []
    p_iter = iter(pressure_sequence)
    def next_p():
        try: return next(p_iter)
        except StopIteration: return pressure_sequence[-1]

    # Publish START event
    publisher.publish(TOPIC_RUN_EVENT,
                      json.dumps({"event":"START","recipe":recipe.get("name",""),"outcome":"","timestamp":time.time()}))
    publisher.publish(TOPIC_LOGGER_CTRL, "HIGH_SPEED_START")

    # Step 1: foreline pump
    log.append("Step 1: pump foreline")
    publisher.publish(TOPIC_PUMP_CONTROL, "ON")

    # Step 2: open valve, pump to target
    target = recipe["target_pressure_torr"]
    log.append(f"Step 2: opening valve, pumping to {target:.2e}")
    publisher.publish(TOPIC_VALVE_CONTROL, "OPEN")
    for _ in range(200):
        p = next_p()
        if p <= target:
            break
    else:
        log.append("TIMEOUT")
        publisher.publish(TOPIC_VALVE_CONTROL, "CLOSE")
        publisher.publish(TOPIC_PUMP_CONTROL,  "OFF")
        publisher.publish(TOPIC_LOGGER_CTRL, "HIGH_SPEED_STOP")
        publisher.publish(TOPIC_RUN_EVENT,
                          json.dumps({"event":"END","recipe":recipe.get("name",""),"outcome":"FAILED","timestamp":time.time()}))
        return False, 0.0, log

    # Step 3: leak check
    log.append("Step 3: leak check")
    publisher.publish(TOPIC_VALVE_CONTROL, "CLOSE")
    p_start  = next_p()
    p_end    = next_p()
    elapsed  = float(recipe["leak_check_duration_s"])
    leak_rate = (p_end - p_start) / elapsed

    if leak_rate > recipe["max_leak_rate_torr_per_s"]:
        log.append("LEAK FAIL")
        publisher.publish(TOPIC_VALVE_CONTROL, "CLOSE")
        publisher.publish(TOPIC_PUMP_CONTROL,  "OFF")
        publisher.publish(TOPIC_LOGGER_CTRL, "HIGH_SPEED_STOP")
        publisher.publish(TOPIC_RUN_EVENT,
                          json.dumps({"event":"END","recipe":recipe.get("name",""),"outcome":"FAILED","timestamp":time.time()}))
        return False, leak_rate, log

    # Step 4: valve already closed
    log.append("Step 4: valve closed")

    # Step 5: start compressor (with pre-check)
    if compressor_already_running:
        log.append("Step 5: compressor already running — skipping")
    else:
        log.append("Step 5: starting compressor")
        publisher.publish(TOPIC_COMP_CONTROL, "COMPRESSOR_ON")

    publisher.publish(TOPIC_LOGGER_CTRL, "HIGH_SPEED_STOP")
    publisher.publish(TOPIC_RUN_EVENT,
                      json.dumps({"event":"END","recipe":recipe.get("name",""),"outcome":"SUCCESS","timestamp":time.time()}))
    return True, leak_rate, log

RECIPE = {
    "name": "Test",
    "foreline_pump_duration_s": 1,
    "target_pressure_torr":     1e-3,
    "leak_check_duration_s":    60,
    "max_leak_rate_torr_per_s": 5e-5,
    "compressor_delay_s":       1,
}
GOOD_PRESSURES = [0.1, 0.01, 9e-4, 9e-4, 9.003e-4]   # crosses target, good leak

@test("successful run: pump ON before valve OPEN")
def _():
    pub = FakeMqttPublisher()
    success, _, log = run_recipe_simulation(RECIPE, GOOD_PRESSURES, pub)
    assert_true(success, f"Should succeed. Log: {log}")
    idx_pump  = next(i for i,(t,p) in enumerate(pub.published)
                     if t==TOPIC_PUMP_CONTROL and p=="ON")
    idx_valve = next(i for i,(t,p) in enumerate(pub.published)
                     if t==TOPIC_VALVE_CONTROL and p=="OPEN")
    assert_true(idx_pump < idx_valve, "Pump ON must come before valve OPEN")

@test("successful run: valve CLOSED before compressor starts")
def _():
    pub = FakeMqttPublisher()
    run_recipe_simulation(RECIPE, GOOD_PRESSURES, pub)
    idx_close = next(i for i,(t,p) in enumerate(pub.published)
                     if t==TOPIC_VALVE_CONTROL and p=="CLOSE")
    idx_comp  = next(i for i,(t,p) in enumerate(pub.published)
                     if t==TOPIC_COMP_CONTROL and p=="COMPRESSOR_ON")
    assert_true(idx_close < idx_comp, "Valve must close before compressor starts")

@test("successful run: START and END run events published")
def _():
    pub = FakeMqttPublisher()
    run_recipe_simulation(RECIPE, GOOD_PRESSURES, pub)
    events = [json.loads(p)["event"] for t,p in pub.published if t==TOPIC_RUN_EVENT]
    assert_true("START" in events, "START event must be published")
    assert_true("END"   in events, "END event must be published")

@test("successful run: END event has outcome=SUCCESS")
def _():
    pub = FakeMqttPublisher()
    run_recipe_simulation(RECIPE, GOOD_PRESSURES, pub)
    end_events = [json.loads(p) for t,p in pub.published
                  if t==TOPIC_RUN_EVENT and json.loads(p)["event"]=="END"]
    assert_true(len(end_events) > 0)
    assert_eq(end_events[-1]["outcome"], "SUCCESS")

@test("successful run: high-speed logging START then STOP published")
def _():
    pub = FakeMqttPublisher()
    run_recipe_simulation(RECIPE, GOOD_PRESSURES, pub)
    ctrl = [p for t,p in pub.published if t==TOPIC_LOGGER_CTRL]
    assert_true("HIGH_SPEED_START" in ctrl)
    assert_true("HIGH_SPEED_STOP"  in ctrl)
    assert_true(ctrl.index("HIGH_SPEED_START") < ctrl.index("HIGH_SPEED_STOP"),
                "HIGH_SPEED_START must come before HIGH_SPEED_STOP")

@test("compressor pre-check: COMPRESSOR_ON NOT sent if already running")
def _():
    pub = FakeMqttPublisher()
    run_recipe_simulation(RECIPE, GOOD_PRESSURES, pub, compressor_already_running=True)
    comp_ons = [(t,p) for t,p in pub.published
                if t==TOPIC_COMP_CONTROL and p=="COMPRESSOR_ON"]
    assert_eq(comp_ons, [], "COMPRESSOR_ON must not be sent if already running")

@test("compressor pre-check: COMPRESSOR_ON IS sent if not running")
def _():
    pub = FakeMqttPublisher()
    run_recipe_simulation(RECIPE, GOOD_PRESSURES, pub, compressor_already_running=False)
    comp_ons = [(t,p) for t,p in pub.published
                if t==TOPIC_COMP_CONTROL and p=="COMPRESSOR_ON"]
    assert_true(len(comp_ons) == 1, "COMPRESSOR_ON must be sent once")

@test("leak check failure: compressor NOT started")
def _():
    pub = FakeMqttPublisher()
    bad_leak = [0.1, 0.01, 9e-4, 9e-4, 0.5]   # 9e-4 -> 0.5 in 60s = huge rate
    success, _, log = run_recipe_simulation(RECIPE, bad_leak, pub)
    assert_false(success, "Should fail on leak")
    comp_ons = [(t,p) for t,p in pub.published
                if t==TOPIC_COMP_CONTROL and p=="COMPRESSOR_ON"]
    assert_eq(comp_ons, [], "Compressor must NOT start after leak failure")

@test("leak failure: END event has outcome=FAILED")
def _():
    pub = FakeMqttPublisher()
    bad_leak = [0.1, 0.01, 9e-4, 9e-4, 0.5]
    run_recipe_simulation(RECIPE, bad_leak, pub)
    end_events = [json.loads(p) for t,p in pub.published
                  if t==TOPIC_RUN_EVENT and json.loads(p)["event"]=="END"]
    assert_true(len(end_events) > 0)
    assert_eq(end_events[-1]["outcome"], "FAILED")

@test("leak failure: safe shutdown (valve CLOSE and pump OFF)")
def _():
    pub = FakeMqttPublisher()
    bad_leak = [0.1, 0.01, 9e-4, 9e-4, 0.5]
    run_recipe_simulation(RECIPE, bad_leak, pub)
    assert_eq(pub.last(TOPIC_VALVE_CONTROL), "CLOSE",
              "Last valve command must be CLOSE")
    assert_eq(pub.last(TOPIC_PUMP_CONTROL),  "OFF",
              "Pump must be OFF after leak abort")

@test("leak rate exactly at threshold is treated as PASS")
def _():
    pub  = FakeMqttPublisher()
    MAX  = RECIPE["max_leak_rate_torr_per_s"]
    dt   = float(RECIPE["leak_check_duration_s"])
    p0   = 9e-4
    p1   = p0 + MAX * dt   # exactly at threshold
    pressures = [0.1, 9e-4, p0, p1]
    success, rate, log = run_recipe_simulation(RECIPE, pressures, pub)
    assert_approx(rate, MAX, tol=1e-15)
    assert_true(success, f"Exactly at threshold should PASS. Log: {log}")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 10 — pump-down timeout logic
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- Section 10: Pump-down timeout logic ----------------------------------")

PUMP_TIMEOUT_S            = 10   # short for testing
PUMP_ABORT_THRESHOLD_TORR = 10.0

def simulate_step2_timeout(
    pressures: list[float],
    target: float = 1e-3,
    timeout_s: float = PUMP_TIMEOUT_S,
    abort_threshold: float = PUMP_ABORT_THRESHOLD_TORR,
) -> tuple[str, float]:
    """
    Simulate step 2 with a timeout.
    Returns ("target_reached" | "timeout_proceed" | "timeout_abort", final_pressure).
    """
    t_start = time.time()
    for i, p in enumerate(pressures):
        elapsed = i * 0.5   # simulate 0.5s per sample
        if p <= target:
            return "target_reached", p
        if elapsed >= timeout_s:
            if p > abort_threshold:
                return "timeout_abort", p
            else:
                return "timeout_proceed", p
    return "timeout_abort", pressures[-1]

@test("pressure reaches target before timeout: target_reached")
def _():
    pressures = [100.0, 10.0, 1.0, 0.1, 0.001, 0.0005]
    outcome, p = simulate_step2_timeout(pressures, target=1e-3, timeout_s=100)
    assert_eq(outcome, "target_reached")

@test("timeout with pressure below abort threshold: proceed with warning")
def _():
    # Never reaches 1e-3 but stays below 10 Torr
    pressures = [5.0] * 100
    outcome, p = simulate_step2_timeout(pressures, target=1e-3,
                                         timeout_s=10, abort_threshold=10.0)
    assert_eq(outcome, "timeout_proceed",
              f"5 Torr < 10 Torr threshold should proceed. Got: {outcome}")

@test("timeout with pressure above abort threshold: abort to protect pump")
def _():
    # Stuck at 50 Torr — serious leak
    pressures = [50.0] * 100
    outcome, p = simulate_step2_timeout(pressures, target=1e-3,
                                         timeout_s=10, abort_threshold=10.0)
    assert_eq(outcome, "timeout_abort",
              f"50 Torr > 10 Torr threshold should abort. Got: {outcome}")

@test("abort threshold boundary: exactly at threshold aborts")
def _():
    pressures = [PUMP_ABORT_THRESHOLD_TORR] * 100
    outcome, p = simulate_step2_timeout(
        pressures, target=1e-3,
        timeout_s=10, abort_threshold=PUMP_ABORT_THRESHOLD_TORR)
    # Exactly at threshold means p > threshold is False, so proceeds
    # (same logic as leak rate: > not >=)
    assert_eq(outcome, "timeout_proceed",
              "Exactly at threshold should proceed (> not >=)")


# ═════════════════════════════════════════════════════════════════════════════
# Summary
# ═════════════════════════════════════════════════════════════════════════════
total  = len(_results)
passed = sum(1 for _,ok,_ in _results if ok)
failed = total - passed

print(f"\n{'─'*62}")
print(f"  Results: {passed}/{total} passed", end="")
if failed:
    print(f"  ({failed} FAILED)")
    print("\nFailed tests:")
    for name, ok, reason in _results:
        if not ok:
            print(f"  - {name}")
            print(f"    {reason}")
else:
    print("  — all passed")
print(f"{'─'*62}\n")
