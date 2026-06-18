# mqtt_config.py
# Central configuration for the HTS Magnet Testing Automation system.
# Edit this file to change hardware settings, pin assignments, or topic names.
# Run validate_config() at the top of every script to catch mistakes early.

# ── MQTT Broker ───────────────────────────────────────────────────────────────
MQTT_BROKER_HOST = "localhost"
MQTT_BROKER_PORT = 1883
MQTT_KEEPALIVE   = 60

# ── MQTT Topics: Compressor ───────────────────────────────────────────────────
TOPIC_COMPRESSOR_METRICS  = "lab/basement/compressor1/metrics"
TOPIC_COMPRESSOR_CONTROL  = "lab/basement/compressor1/control"
# Payload: "START_MONITORING" | "PAUSE_MONITORING" | "COMPRESSOR_ON" | "COMPRESSOR_OFF"
TOPIC_COMPRESSOR_STATE    = "lab/basement/compressor1/state"
# Payload: JSON {"commanded": "ON"|"OFF", "confirmed": bool, "timestamp": float}

# ── MQTT Topics: LabJack ──────────────────────────────────────────────────────
TOPIC_LABJACK_METRICS     = "lab/basement/labjack1/metrics"
TOPIC_VALVE_CONTROL       = "lab/basement/labjack1/valve/control"
# Payload: "OPEN" | "CLOSE"
TOPIC_VALVE_STATE         = "lab/basement/labjack1/valve/state"
# Payload: JSON {"state": "OPEN"|"CLOSED", "timestamp": float}
TOPIC_PUMP_CONTROL        = "lab/basement/labjack1/pump/control"
# Payload: "ON" | "OFF"
TOPIC_PUMP_STATE          = "lab/basement/labjack1/pump/state"
# Payload: JSON {"state": "ON"|"OFF", "timestamp": float}

# ── MQTT Topics: Heartbeats ───────────────────────────────────────────────────
TOPIC_COMPRESSOR_HEARTBEAT = "lab/basement/compressor1/heartbeat"
TOPIC_LABJACK_HEARTBEAT    = "lab/basement/labjack1/heartbeat"
HEARTBEAT_INTERVAL_S = 5

# ── MQTT Topics: Errors ───────────────────────────────────────────────────────
TOPIC_ERRORS = "lab/errors"
# Payload: JSON {"source": "script_name", "message": "...", "timestamp": float}

# ── MQTT Topics: Run Events ───────────────────────────────────────────────────
TOPIC_RUN_EVENT = "lab/run_event"
# Payload: JSON {"event": "START"|"END", "recipe": str,
#                "outcome": str, "timestamp": float}
# Published by the GUI at recipe start/end.
# The logger writes these to QuestDB so Grafana can show run boundaries.

# ── MQTT Topics: Logger Control ───────────────────────────────────────────────
TOPIC_LOGGER_CONTROL = "lab/logger/control"
# Payload: "HIGH_SPEED_START" | "HIGH_SPEED_STOP"
# Switches the logger between 5s (normal) and 1s (recipe run) flush intervals.

# ── MQTT Topics: GUI ──────────────────────────────────────────────────────────
TOPIC_GUI_STATUS = "lab/basement/pyside_gui/status"
# Payload: "ONLINE" | "OFFLINE"  (OFFLINE sent as Last Will and Testament)

# ── Compressor / Modbus Serial ────────────────────────────────────────────────
COMPRESSOR_SERIAL_PORT  = "COM5"
# *** If Windows reassigns the COM port after a USB reconnect or reboot,
#     update this value. Check Device Manager -> Ports (COM & LPT). ***
COMPRESSOR_BAUDRATE     = 115200
COMPRESSOR_MODBUS_ID    = 16
COMPRESSOR_POLL_RATE_S  = 1

# ── LabJack T7-Pro Pin Assignments ────────────────────────────────────────────
AIN_VACUUM_PRESSURE     = 0    # AIN0 -> vacuum pressure gauge output voltage

# Digital outputs -> SunFounder relay board (ACTIVE-LOW)
# Writing 0 (LOW)  -> relay ON  -> device ON
# Writing 1 (HIGH) -> relay OFF -> device OFF  <- safe default on boot
DOUT_VACUUM_VALVE       = 0    # FIO0 -> Relay CH1 -> vacuum line valve
DOUT_PUMP               = 1    # FIO1 -> Relay CH2 -> roughing pump

LABJACK_POLL_RATE_S     = 0.5

# ── QuestDB ───────────────────────────────────────────────────────────────────
# QuestDB runs locally, launched by startup.bat from:
# %USERPROFILE%\.local\bin\questdb\bin\questdb.exe
# Data is stored at: %USERPROFILE%\.local\bin\questdb\data
QUESTDB_HOST                  = "localhost"
QUESTDB_PORT                  = 9000
QUESTDB_TABLE_LABJACK         = "labjack_metrics"
QUESTDB_TABLE_COMPRESSOR      = "compressor_metrics"
QUESTDB_TABLE_RUN_EVENTS      = "run_events"
QUESTDB_FLUSH_INTERVAL_S      = 5    # Normal flush interval (seconds)
QUESTDB_FLUSH_INTERVAL_FAST_S = 1    # High-speed mode during recipe runs

# ── Recipe safety limits ──────────────────────────────────────────────────────
# Max time to wait for target pressure before evaluating the pump-down state.
PUMP_TIMEOUT_S            = 1800   # 30 minutes
# If pressure is above this at timeout, the leak is serious: abort and close.
# Below this, the system has pumped well enough to proceed cautiously.
PUMP_ABORT_THRESHOLD_TORR = 10.0   # Torr

# ── Vacuum Gauge: Voltage -> Torr Conversion ──────────────────────────────────
# Formula from gauge datasheet: Pi = 10^(V-4) Torr
# Gauge output range: 0-10 V
# A floating unconnected AIN pin on the T7-Pro typically reads 8-10V.
# Anything above GAUGE_DISCONNECT_THRESHOLD_V is treated as no signal.

GAUGE_DISCONNECT_THRESHOLD_V = 8.0   # Volts

def voltage_to_torr(volts: float):
    """
    Convert pressure gauge voltage to Torr.  Pi = 10^(V-4)
    Returns None if voltage > GAUGE_DISCONNECT_THRESHOLD_V (gauge not connected).
    """
    if volts <= 0:
        return 0.0
    if volts > GAUGE_DISCONNECT_THRESHOLD_V:
        return None
    return 10 ** (volts - 4)


# ── Config validation ─────────────────────────────────────────────────────────
def validate_config():
    """
    Validate all configuration values at startup.
    Call this at the top of every script's main() before touching any hardware.
    Raises ValueError with a clear, descriptive message if anything is wrong.
    """
    errors = []

    # MQTT broker
    if not MQTT_BROKER_HOST:
        errors.append("MQTT_BROKER_HOST is empty.")
    if not (1 <= MQTT_BROKER_PORT <= 65535):
        errors.append(f"MQTT_BROKER_PORT {MQTT_BROKER_PORT} is not a valid port (1-65535).")

    # QuestDB
    if not QUESTDB_HOST:
        errors.append("QUESTDB_HOST is empty.")
    if not (1 <= QUESTDB_PORT <= 65535):
        errors.append(f"QUESTDB_PORT {QUESTDB_PORT} is not a valid port (1-65535).")

    # Serial / Modbus
    if not COMPRESSOR_SERIAL_PORT:
        errors.append(
            "COMPRESSOR_SERIAL_PORT is empty. "
            "Check Device Manager -> Ports (COM & LPT) for the correct port.")
    if COMPRESSOR_BAUDRATE not in (9600, 19200, 38400, 57600, 115200):
        errors.append(
            f"COMPRESSOR_BAUDRATE {COMPRESSOR_BAUDRATE} is not a standard rate. "
            f"Expected one of: 9600, 19200, 38400, 57600, 115200.")
    if not (1 <= COMPRESSOR_MODBUS_ID <= 247):
        errors.append(
            f"COMPRESSOR_MODBUS_ID {COMPRESSOR_MODBUS_ID} is out of range (1-247).")

    # LabJack pins
    if AIN_VACUUM_PRESSURE not in range(14):
        errors.append(
            f"AIN_VACUUM_PRESSURE {AIN_VACUUM_PRESSURE} is not a valid "
            f"T7-Pro AIN channel (0-13).")
    if DOUT_VACUUM_VALVE not in range(8):
        errors.append(
            f"DOUT_VACUUM_VALVE {DOUT_VACUUM_VALVE} is not a valid FIO channel (0-7).")
    if DOUT_PUMP not in range(8):
        errors.append(
            f"DOUT_PUMP {DOUT_PUMP} is not a valid FIO channel (0-7).")
    if DOUT_VACUUM_VALVE == DOUT_PUMP:
        errors.append(
            f"DOUT_VACUUM_VALVE and DOUT_PUMP are both set to FIO{DOUT_VACUUM_VALVE}. "
            f"They must be different channels.")

    # Thresholds
    if not (0 < GAUGE_DISCONNECT_THRESHOLD_V <= 10.5):
        errors.append(
            f"GAUGE_DISCONNECT_THRESHOLD_V {GAUGE_DISCONNECT_THRESHOLD_V} "
            f"is outside the expected range (0 < V <= 10.5).")
    if PUMP_ABORT_THRESHOLD_TORR <= 0:
        errors.append("PUMP_ABORT_THRESHOLD_TORR must be greater than zero.")

    if errors:
        raise ValueError(
            "mqtt_config.py has invalid settings:\n" +
            "\n".join(f"  - {e}" for e in errors)
        )
