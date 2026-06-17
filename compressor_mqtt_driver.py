"""
compressor_mqtt_driver.py
─────────────────────────────────────────────────────────────────────────────
Background driver for the Cryomech AL630 Helium Compressor.

Responsibilities:
  • Poll all compressor registers every COMPRESSOR_POLL_RATE_S seconds.
  • Publish sensor readings as JSON to TOPIC_COMPRESSOR_METRICS.
  • Publish a heartbeat every HEARTBEAT_INTERVAL_S so the GUI knows alive.
  • Listen on TOPIC_COMPRESSOR_CONTROL for:
      "START_MONITORING"  → resume polling
      "PAUSE_MONITORING"  → idle (stop polling but keep running)
      "COMPRESSOR_ON"     → write holding register to start the compressor
      "COMPRESSOR_OFF"    → write holding register to stop the compressor

Dependencies:
  pip install pymodbus paho-mqtt
"""

import json
import time
import sys
import threading

import paho.mqtt.client as mqtt

try:
    from modbus_client import ModbusDeviceClient, set_error_publisher
    import mqtt_config as cfg
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────
is_monitoring = True


# ─────────────────────────────────────────────────────────────────────────────
# MQTT callbacks
# ─────────────────────────────────────────────────────────────────────────────
_mqtt_pub = None   # Set after MQTT connects

def _publish_error(source: str, message: str):
    """Publish a non-fatal error to TOPIC_ERRORS for the GUI."""
    global _mqtt_pub
    print(f"[ERROR] {source}: {message}")
    if _mqtt_pub is not None:
        try:
            payload = json.dumps({
                "source":    source,
                "message":   message,
                "timestamp": time.time(),
            })
            _mqtt_pub.publish(cfg.TOPIC_ERRORS, payload)
        except Exception:
            pass


def _publish_compressor_state(mqtt_client, compressor: ModbusDeviceClient,
                              commanded: str, write_ok: bool):
    """
    After a COMPRESSOR_ON/OFF command, read back the running state register
    to confirm the compressor actually changed state, then publish the result.
    """
    confirmed = False
    try:
        import time as _time
        _time.sleep(2.0)   # Give the compressor time to respond
        raw = compressor.read_all_input_registers()
        if raw:
            running = raw.get("compressor_running")
            if commanded == "ON":
                confirmed = bool(running)
            else:
                confirmed = not bool(running)
    except Exception as e:
        _publish_error("compressor_driver",
                       f"Readback failed after {commanded} command: {e}")

    if not confirmed:
        msg = (f"Compressor {commanded} command sent but readback shows "
               f"state did not change as expected.")
        _publish_error("compressor_driver", msg)

    try:
        payload = json.dumps({
            "commanded":  commanded,
            "confirmed":  confirmed,
            "timestamp":  time.time(),
        })
        mqtt_client.publish(cfg.TOPIC_COMPRESSOR_STATE, payload, retain=True)
        print(f"[DRIVER] Compressor state published: "
              f"commanded={commanded} confirmed={confirmed}")
    except Exception as e:
        print(f"[DRIVER] Warning: could not publish compressor state: {e}")


def make_callbacks(compressor: ModbusDeviceClient):
    """Returns on_connect and on_message callbacks with access to the client."""

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f"[MQTT] Connected to {cfg.MQTT_BROKER_HOST}:{cfg.MQTT_BROKER_PORT}")
            client.subscribe(cfg.TOPIC_COMPRESSOR_CONTROL)
            print(f"[MQTT] Subscribed to: {cfg.TOPIC_COMPRESSOR_CONTROL}")
        else:
            print(f"[MQTT] Connection failed — code {reason_code}")

    def on_message(client, userdata, msg):
        global is_monitoring
        payload = msg.payload.decode("utf-8").strip()
        print(f"[MQTT] Received on '{msg.topic}': {payload}")

        if payload == "PAUSE_MONITORING":
            is_monitoring = False
            print("[DRIVER] Monitoring PAUSED.")

        elif payload == "START_MONITORING":
            is_monitoring = True
            print("[DRIVER] Monitoring RESUMED.")

        elif payload == "COMPRESSOR_ON":
            ok = compressor.set_compressor_state("on")
            print(f"[DRIVER] Compressor ON command → {'OK' if ok else 'FAILED'}")
            _publish_compressor_state(client, compressor, "ON", ok)

        elif payload == "COMPRESSOR_OFF":
            ok = compressor.set_compressor_state("off")
            print(f"[DRIVER] Compressor OFF command → {'OK' if ok else 'FAILED'}")
            _publish_compressor_state(client, compressor, "OFF", ok)

    return on_connect, on_message


# ─────────────────────────────────────────────────────────────────────────────
# Heartbeat thread
# ─────────────────────────────────────────────────────────────────────────────
def heartbeat_loop(mqtt_client: mqtt.Client, stop_event: threading.Event):
    while not stop_event.is_set():
        payload = json.dumps({"alive": True, "timestamp": time.time()})
        mqtt_client.publish(cfg.TOPIC_COMPRESSOR_HEARTBEAT, payload)
        stop_event.wait(cfg.HEARTBEAT_INTERVAL_S)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    global is_monitoring

    # ── Connect to compressor (retry loop) ───────────────────────────────────
    # If the compressor is not plugged in, keep retrying every 30 seconds
    # rather than crashing. The heartbeat thread keeps running so the GUI
    # knows this driver is alive even when hardware is not connected.
    # Validate config before touching any hardware
    try:
        cfg.validate_config()
    except ValueError as e:
        print(f"[CONFIG] FATAL: {e}")
        sys.exit(1)

    print(f"[MODBUS] Connecting to compressor on {cfg.COMPRESSOR_SERIAL_PORT}...")
    compressor = ModbusDeviceClient(
        protocol    = "serial",
        modbus_id   = cfg.COMPRESSOR_MODBUS_ID,
        serial_port = cfg.COMPRESSOR_SERIAL_PORT,
        baudrate    = cfg.COMPRESSOR_BAUDRATE,
    )

    RETRY_INTERVAL_S = 30
    while True:
        try:
            connected = compressor.connect()
        except Exception as e:
            connected = False
            print(f"[MODBUS] Connection error: {e}")

        if connected:
            print("[MODBUS] Connected to compressor.")
            break
        else:
            print(f"[MODBUS] Could not connect to compressor on "
                  f"{cfg.COMPRESSOR_SERIAL_PORT}. "
                  f"Retrying in {RETRY_INTERVAL_S}s... "
                  f"(Is it plugged in and powered on?)")
            time.sleep(RETRY_INTERVAL_S)

    # ── Connect to MQTT ────────────────────────────────────────────────────────
    on_connect, on_message = make_callbacks(compressor)
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.connect(cfg.MQTT_BROKER_HOST, cfg.MQTT_BROKER_PORT, cfg.MQTT_KEEPALIVE)
    mqtt_client.loop_start()
    _mqtt_pub = mqtt_client
    set_error_publisher(_publish_error)

    # ── Start heartbeat thread ─────────────────────────────────────────────────
    stop_event = threading.Event()
    hb_thread  = threading.Thread(target=heartbeat_loop,
                                  args=(mqtt_client, stop_event),
                                  daemon=True)
    hb_thread.start()

    # ── Main polling loop ──────────────────────────────────────────────────────
    print(f"[DRIVER] Polling every {cfg.COMPRESSOR_POLL_RATE_S}s. Press Ctrl+C to stop.\n")
    try:
        while True:
            if is_monitoring:
                try:
                    raw = compressor.read_all_input_registers()
                    if raw:
                        payload = {
                            "helium_pressure":    raw.get("high_pressure"),
                            "low_pressure":       raw.get("low_pressure"),
                            "delta_pressure":     raw.get("delta_pressure_avg"),
                            "helium_temp":        raw.get("helium_temp"),
                            "oil_temp":           raw.get("oil_temp"),
                            "coolant_in_temp":    raw.get("coolant_in_temp"),
                            "coolant_out_temp":   raw.get("coolant_out_temp"),
                            "motor_current":      raw.get("motor_current"),
                            "operating_state":    raw.get("operating_state"),
                            "compressor_running": raw.get("compressor_running"),
                            "warning_state":      raw.get("warning_state"),
                            "alarm_state":        raw.get("alarm_state"),
                        }
                        mqtt_client.publish(
                            cfg.TOPIC_COMPRESSOR_METRICS,
                            json.dumps(payload))
                        print(f"[MQTT] → {cfg.TOPIC_COMPRESSOR_METRICS}")
                    else:
                        _publish_error(
                            "compressor_driver",
                            "No data returned from compressor poll")
                except Exception as e:
                    _publish_error(
                        "compressor_driver",
                        f"Poll error: {e}")
            else:
                print("[DRIVER] Monitoring paused — waiting for START_MONITORING...")

            time.sleep(cfg.COMPRESSOR_POLL_RATE_S)

    except KeyboardInterrupt:
        print("\n[DRIVER] Stopping...")
    finally:
        stop_event.set()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        compressor.disconnect()
        print("[DRIVER] Shutdown complete.")


if __name__ == "__main__":
    main()
