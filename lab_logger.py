"""
lab_logger.py
─────────────────────────────────────────────────────────────────────────────
Subscribes to MQTT metric topics and writes data to QuestDB every 5 seconds.
Publishes errors to TOPIC_ERRORS so the GUI can display them.

Error handling:
  - DB write errors are caught per-row and published to MQTT, never crash thread
  - Flush errors are caught and reported
  - MQTT reconnects automatically via loop_forever
  - QuestDB sender context is properly managed
"""

import json
import sys
import time
import threading
from typing import Optional

import paho.mqtt.client as mqtt

try:
    from questdb.ingress import Sender, TimestampNanos
    from questdb_client import get_sender
    import mqtt_config as cfg
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

# ── Shared state ──────────────────────────────────────────────────────────────
_lock               = threading.Lock()
_high_speed_mode    = False   # True during recipe runs (1s flush)
_current_flush_interval = None   # Set dynamically in flush_loop
_latest_labjack:    Optional[dict] = None
_latest_compressor: Optional[dict] = None
_labjack_new        = False
_compressor_new     = False
_mqtt_client: Optional[mqtt.Client] = None   # set in main(), used by error publisher


def _publish_error(message: str):
    """Publish an error string to the MQTT error topic for the GUI to display."""
    global _mqtt_client
    if _mqtt_client is not None:
        try:
            payload = json.dumps({
                "source":    "lab_logger",
                "message":   message,
                "timestamp": time.time(),
            })
            _mqtt_client.publish(cfg.TOPIC_ERRORS, payload)
        except Exception:
            pass   # Don't let error reporting itself crash anything
    print(f"[ERROR] {message}")


# ── MQTT callbacks ─────────────────────────────────────────────────────────────
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"[MQTT] Connected to broker at "
              f"{cfg.MQTT_BROKER_HOST}:{cfg.MQTT_BROKER_PORT}")
        client.subscribe(cfg.TOPIC_LABJACK_METRICS)
        client.subscribe(cfg.TOPIC_COMPRESSOR_METRICS)
        client.subscribe(cfg.TOPIC_LOGGER_CONTROL)
        client.subscribe(cfg.TOPIC_RUN_EVENT)
        print("[MQTT] Subscribed to labjack and compressor metric topics.")
    else:
        print(f"[MQTT] Connection failed — code {reason_code}")


def on_disconnect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print(f"[MQTT] Unexpected disconnect (code {reason_code}) — "
              f"paho will retry automatically.")


def on_message(client, userdata, msg):
    global _latest_labjack, _latest_compressor, _labjack_new, _compressor_new
    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except json.JSONDecodeError as e:
        _publish_error(f"Bad JSON on topic {msg.topic}: {e}")
        return

    global _high_speed_mode

    # Handle control messages (plain text, not JSON)
    raw_payload = msg.payload.decode('utf-8').strip()
    if msg.topic == cfg.TOPIC_LOGGER_CONTROL:
        if raw_payload == 'HIGH_SPEED_START':
            _high_speed_mode = True
            print(f'[LOGGER] Switched to HIGH-SPEED mode '
                  f'({cfg.QUESTDB_FLUSH_INTERVAL_FAST_S}s flush)')
        elif raw_payload == 'HIGH_SPEED_STOP':
            _high_speed_mode = False
            print(f'[LOGGER] Returned to NORMAL mode '
                  f'({cfg.QUESTDB_FLUSH_INTERVAL_S}s flush)')
        return

    # Handle run events — write to QuestDB directly
    if msg.topic == cfg.TOPIC_RUN_EVENT:
        try:
            _write_run_event(data)
        except Exception as e:
            _publish_error(f'Failed to write run event: {e}')
        return

    with _lock:
        if msg.topic == cfg.TOPIC_LABJACK_METRICS:
            _latest_labjack = data
            _labjack_new    = True
        elif msg.topic == cfg.TOPIC_COMPRESSOR_METRICS:
            _latest_compressor = data
            _compressor_new    = True


# ── QuestDB flush ──────────────────────────────────────────────────────────────
_sender_ref = None   # Set in main() so _write_run_event can access it


def _write_run_event(data: dict):
    """
    Write a run event row to QuestDB immediately (not buffered).
    Called when TOPIC_RUN_EVENT is received.
    """
    if _sender_ref is None:
        return
    try:
        event   = data.get("event",   "UNKNOWN")
        recipe  = data.get("recipe",  "unknown")
        outcome = data.get("outcome", "")
        _sender_ref.row(
            cfg.QUESTDB_TABLE_RUN_EVENTS,
            symbols={"event": event, "recipe": recipe, "outcome": outcome},
            columns={"timestamp_unix": float(data.get("timestamp", time.time()))},
            at=TimestampNanos.now(),
        )
        _sender_ref.flush()
        print(f"[DB] run_events | {event} | recipe={recipe} | outcome={outcome}")
    except Exception as e:
        _publish_error(f"Failed to write run event to QuestDB: {e}")


def flush_to_questdb(sender: Sender):
    """
    Write one row per table if new data has arrived since the last flush.
    All errors are caught individually so one bad row never stops other writes.
    """
    global _labjack_new, _compressor_new
    wrote_anything = False

    with _lock:

        # ── LabJack row ───────────────────────────────────────────────────────
        if _labjack_new and _latest_labjack is not None:
            try:
                data     = _latest_labjack
                pressure = data.get("vacuum_pressure_torr")
                volts    = data.get("vacuum_gauge_volts")

                if pressure is None:
                    # Gauge not connected — skip row, no error
                    print("[DB] labjack_metrics skipped — "
                          "gauge not connected (AIN0 floating)")
                else:
                    columns = {"vacuum_pressure_torr": float(pressure)}
                    if volts is not None:
                        columns["vacuum_gauge_volts"] = float(volts)

                    sender.row(
                        cfg.QUESTDB_TABLE_LABJACK,
                        columns=columns,
                        at=TimestampNanos.now(),
                    )
                    wrote_anything = True
                    print(f"[DB] labjack_metrics  | "
                          f"pressure={pressure:.3e} Torr")

            except Exception as e:
                _publish_error(f"DB write failed for labjack_metrics: {e}")
            finally:
                _labjack_new = False

        # ── Compressor row ────────────────────────────────────────────────────
        if _compressor_new and _latest_compressor is not None:
            try:
                data = _latest_compressor

                float_fields = [
                    "helium_pressure", "low_pressure", "delta_pressure",
                    "helium_temp", "oil_temp", "coolant_in_temp",
                    "coolant_out_temp", "motor_current",
                ]
                columns = {}
                for key in float_fields:
                    val = data.get(key)
                    if val is not None:
                        try:
                            columns[key] = float(val)
                        except (TypeError, ValueError) as e:
                            _publish_error(
                                f"Bad value for compressor field "
                                f"'{key}': {val!r} — {e}")

                symbols = {}
                for key in ("operating_state", "compressor_running",
                            "warning_state", "alarm_state"):
                    val = data.get(key)
                    if val is not None:
                        symbols[key] = str(val)

                if columns or symbols:
                    sender.row(
                        cfg.QUESTDB_TABLE_COMPRESSOR,
                        symbols=symbols,
                        columns=columns,
                        at=TimestampNanos.now(),
                    )
                    wrote_anything = True
                    hp = columns.get("helium_pressure", "—")
                    ot = columns.get("oil_temp", "—")
                    print(f"[DB] compressor_metrics | "
                          f"he_pressure={hp}  oil_temp={ot}")
                else:
                    print("[DB] compressor_metrics skipped — no valid fields")

            except Exception as e:
                _publish_error(f"DB write failed for compressor_metrics: {e}")
            finally:
                _compressor_new = False

    if wrote_anything:
        try:
            sender.flush()
        except Exception as e:
            _publish_error(f"QuestDB flush failed — is QuestDB running? {e}")


def flush_loop(sender: Sender, stop_event: threading.Event):
    """Background thread: call flush_to_questdb on a timer.
    Interval switches between QUESTDB_FLUSH_INTERVAL_S (normal)
    and QUESTDB_FLUSH_INTERVAL_FAST_S (high-speed during recipe runs).
    """
    while not stop_event.is_set():
        interval = (cfg.QUESTDB_FLUSH_INTERVAL_FAST_S
                    if _high_speed_mode
                    else cfg.QUESTDB_FLUSH_INTERVAL_S)
        stop_event.wait(interval)
        if not stop_event.is_set():
            try:
                flush_to_questdb(sender)
            except Exception as e:
                _publish_error(f"Unexpected error in flush loop: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global _mqtt_client

    try:
        cfg.validate_config()
    except ValueError as e:
        print(f"[CONFIG] FATAL: {e}")
        sys.exit(1)

    print(f"[LOGGER] Starting — will write to QuestDB at "
          f"{cfg.QUESTDB_HOST}:{cfg.QUESTDB_PORT} "
          f"every {cfg.QUESTDB_FLUSH_INTERVAL_S}s")
    print(f"[LOGGER] Tables: '{cfg.QUESTDB_TABLE_LABJACK}' "
          f"and '{cfg.QUESTDB_TABLE_COMPRESSOR}'")

    # ── Open QuestDB sender ───────────────────────────────────────────────────
    try:
        sender_ctx = get_sender()
    except Exception as e:
        print(f"[DB] FATAL: Could not create QuestDB sender — {e}")
        print(f"     Is QuestDB running at "
              f"{cfg.QUESTDB_HOST}:{cfg.QUESTDB_PORT}?")
        sys.exit(1)

    with sender_ctx as sender:
        global _sender_ref
        _sender_ref = sender

        # ── Start flush timer ─────────────────────────────────────────────────
        stop_event   = threading.Event()
        flush_thread = threading.Thread(
            target=flush_loop,
            args=(sender, stop_event),
            daemon=True,
            name="flush_loop",
        )
        flush_thread.start()

        # ── Connect to MQTT ───────────────────────────────────────────────────
        _mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        _mqtt_client.on_connect    = on_connect
        _mqtt_client.on_disconnect = on_disconnect
        _mqtt_client.on_message    = on_message

        try:
            _mqtt_client.connect(
                cfg.MQTT_BROKER_HOST,
                cfg.MQTT_BROKER_PORT,
                cfg.MQTT_KEEPALIVE,
            )
        except Exception as e:
            print(f"[MQTT] FATAL: Could not connect to broker — {e}")
            stop_event.set()
            sys.exit(1)

        print(f"[LOGGER] Running. Press Ctrl+C to stop.\n")

        try:
            _mqtt_client.loop_forever()
        except KeyboardInterrupt:
            print("\n[LOGGER] Stopping...")
        except Exception as e:
            _publish_error(f"Unexpected error in MQTT loop: {e}")
            print(f"[LOGGER] Unexpected error: {e}")
        finally:
            stop_event.set()
            flush_thread.join(timeout=10)
            # Final flush before sender context closes
            try:
                flush_to_questdb(sender)
            except Exception:
                pass
            _mqtt_client.disconnect()
            print("[LOGGER] Shutdown complete.")


if __name__ == "__main__":
    main()
