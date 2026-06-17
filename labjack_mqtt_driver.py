"""
labjack_mqtt_driver.py
─────────────────────────────────────────────────────────────────────────────
Background driver for the LabJack T7-Pro DAQ.

Responsibilities:
  • On boot: immediately set all relay pins HIGH (valves/pump OFF — Active-Low
    relay board means HIGH = relay coil de-energized = safe state).
  • Poll AIN0 for vacuum pressure every LABJACK_POLL_RATE_S seconds.
  • Publish sensor readings as JSON to TOPIC_LABJACK_METRICS.
  • Publish a heartbeat every HEARTBEAT_INTERVAL_S so the GUI knows this
    script is alive.
  • Listen for OPEN/CLOSE commands on TOPIC_VALVE_CONTROL.
  • Listen for ON/OFF commands on TOPIC_PUMP_CONTROL.

Relay pin map (Active-Low):
  FIO0 → Relay CH1 → Vacuum valve   (0=OPEN, 1=CLOSED)
  FIO1 → Relay CH2 → Roughing pump  (0=ON,   1=OFF)

Dependencies:
  pip install labjack-ljm paho-mqtt
  + LabJack LJM driver from https://labjack.com/ljm
"""

import json
import time
import sys
import threading

import paho.mqtt.client as mqtt

try:
    from labjack import ljm
    import mqtt_config as cfg
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Relay helper
# ─────────────────────────────────────────────────────────────────────────────
_mqtt_pub = None   # Set after MQTT connects; used by error publisher

# Current relay states — tracked here so interlocks can check them
_valve_open = False
_pump_on    = False

def _publish_error(source: str, message: str):
    """Publish a non-fatal error to TOPIC_ERRORS so the GUI can display it."""
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


def set_relay(handle, fio_channel: int, activate: bool, label: str,
              mqtt_client=None):
    """
    Drive a relay output and publish the new state to MQTT.
      activate=True  → write 0 (LOW)  → relay ON
      activate=False → write 1 (HIGH) → relay OFF
    """
    pin   = f"FIO{fio_channel}"
    value = 0 if activate else 1
    ljm.eWriteName(handle, pin, value)
    state = "ON" if activate else "OFF"
    print(f"[LABJACK] {pin} ({label}) → {state}")

    # Publish the new state so the GUI always knows reality
    if mqtt_client is not None:
        try:
            if fio_channel == cfg.DOUT_VACUUM_VALVE:
                topic   = cfg.TOPIC_VALVE_STATE
                payload = json.dumps({
                    "state": "OPEN" if activate else "CLOSED",
                    "timestamp": time.time(),
                })
            elif fio_channel == cfg.DOUT_PUMP:
                topic   = cfg.TOPIC_PUMP_STATE
                payload = json.dumps({
                    "state": "ON" if activate else "OFF",
                    "timestamp": time.time(),
                })
            else:
                return
            mqtt_client.publish(topic, payload, retain=True)
        except Exception as e:
            print(f"[LABJACK] Warning: could not publish state: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MQTT callbacks
# ─────────────────────────────────────────────────────────────────────────────
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"[MQTT] Connected to {cfg.MQTT_BROKER_HOST}:{cfg.MQTT_BROKER_PORT}")
        client.subscribe(cfg.TOPIC_VALVE_CONTROL)
        client.subscribe(cfg.TOPIC_PUMP_CONTROL)
        print(f"[MQTT] Subscribed to valve and pump control topics.")
    else:
        print(f"[MQTT] Connection failed — code {reason_code}")


def make_on_message(handle):
    """Closure so the callback can reach the LabJack handle."""
    def on_message(client, userdata, msg):
        global _valve_open, _pump_on
        topic   = msg.topic
        payload = msg.payload.decode("utf-8").strip()
        print(f"[MQTT] Received on '{topic}': {payload}")

        if topic == cfg.TOPIC_VALVE_CONTROL:
            if payload == "OPEN":
                # Soft interlock: warn if pump is not running
                # (opening valve without pump could backfill the foreline)
                if not _pump_on:
                    _publish_error(
                        "labjack_driver",
                        "INTERLOCK WARNING: Valve OPEN commanded but pump is OFF. "
                        "Opening valve anyway (override). "
                        "Ensure this is intentional.")
                set_relay(handle, cfg.DOUT_VACUUM_VALVE, activate=True,
                          label="Vacuum valve", mqtt_client=client)
                _valve_open = True
            elif payload == "CLOSE":
                set_relay(handle, cfg.DOUT_VACUUM_VALVE, activate=False,
                          label="Vacuum valve", mqtt_client=client)
                _valve_open = False

        elif topic == cfg.TOPIC_PUMP_CONTROL:
            if payload == "ON":
                set_relay(handle, cfg.DOUT_PUMP, activate=True,
                          label="Roughing pump", mqtt_client=client)
                _pump_on = True
            elif payload == "OFF":
                # Soft interlock: warn if valve is open
                # (turning off pump with valve open could damage pump)
                if _valve_open:
                    _publish_error(
                        "labjack_driver",
                        "INTERLOCK WARNING: Pump OFF commanded but valve is OPEN. "
                        "Turning pump off anyway (override). "
                        "Consider closing the valve first.")
                set_relay(handle, cfg.DOUT_PUMP, activate=False,
                          label="Roughing pump", mqtt_client=client)
                _pump_on = False

    return on_message


# ─────────────────────────────────────────────────────────────────────────────
# Heartbeat thread
# ─────────────────────────────────────────────────────────────────────────────
def heartbeat_loop(mqtt_client: mqtt.Client, stop_event: threading.Event):
    """Publishes a heartbeat JSON every HEARTBEAT_INTERVAL_S seconds."""
    while not stop_event.is_set():
        payload = json.dumps({"alive": True, "timestamp": time.time()})
        mqtt_client.publish(cfg.TOPIC_LABJACK_HEARTBEAT, payload)
        stop_event.wait(cfg.HEARTBEAT_INTERVAL_S)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    # ── Open LabJack ──────────────────────────────────────────────────────────
    try:
        cfg.validate_config()
    except ValueError as e:
        print(f"[CONFIG] FATAL: {e}")
        sys.exit(1)

    print("[LABJACK] Opening connection to T7-Pro...")
    try:
        handle = ljm.openS("T7", "ANY", "ANY")
        info   = ljm.getHandleInfo(handle)
        print(f"[LABJACK] Connected — Serial: {info[2]}")
    except ljm.LJMError as e:
        print(f"[LABJACK] FATAL: {e}")
        sys.exit(1)

    # ── SAFETY INIT: all relays OFF (HIGH) before anything else ──────────────
    print("[LABJACK] Safety init: setting all outputs HIGH (devices OFF)...")
    ljm.eWriteName(handle, f"FIO{cfg.DOUT_VACUUM_VALVE}", 1)
    ljm.eWriteName(handle, f"FIO{cfg.DOUT_PUMP}",         1)
    print("[LABJACK] Safety init complete.")

    # ── Connect to MQTT ───────────────────────────────────────────────────────
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = make_on_message(handle)
    mqtt_client.connect(cfg.MQTT_BROKER_HOST, cfg.MQTT_BROKER_PORT, cfg.MQTT_KEEPALIVE)
    mqtt_client.loop_start()
    _mqtt_pub = mqtt_client

    # Publish initial relay states so GUI knows everything is OFF on startup
    # Use retain=True so the GUI gets the state immediately on connect
    def _publish_initial_states():
        import time as _time
        _time.sleep(1.0)   # Wait for MQTT connection to settle
        try:
            mqtt_client.publish(
                cfg.TOPIC_VALVE_STATE,
                json.dumps({"state": "CLOSED", "timestamp": time.time()}),
                retain=True)
            mqtt_client.publish(
                cfg.TOPIC_PUMP_STATE,
                json.dumps({"state": "OFF", "timestamp": time.time()}),
                retain=True)
            print("[LABJACK] Initial relay states published to MQTT.")
        except Exception as e:
            print(f"[LABJACK] Warning: could not publish initial states: {e}")
    threading.Thread(target=_publish_initial_states, daemon=True).start()

    # ── Start heartbeat thread ────────────────────────────────────────────────
    stop_event = threading.Event()
    hb_thread  = threading.Thread(target=heartbeat_loop,
                                  args=(mqtt_client, stop_event),
                                  daemon=True)
    hb_thread.start()

    # ── Sensor polling loop ───────────────────────────────────────────────────
    print(f"[DRIVER] Polling every {cfg.LABJACK_POLL_RATE_S}s. Press Ctrl+C to stop.\n")
    try:
        while True:
            try:
                volts_vacuum  = ljm.eReadName(
                    handle, f"AIN{cfg.AIN_VACUUM_PRESSURE}")
                pressure_torr = cfg.voltage_to_torr(volts_vacuum)

                payload = json.dumps({
                    "vacuum_pressure_torr": pressure_torr,
                    "vacuum_gauge_volts":   round(volts_vacuum, 5),
                    "gauge_connected":      pressure_torr is not None,
                })
                mqtt_client.publish(cfg.TOPIC_LABJACK_METRICS, payload)
                if pressure_torr is None:
                    print(f"[DRIVER] AIN0 = {volts_vacuum:.4f} V — "
                          "gauge not connected (pin floating)")
                else:
                    print(f"[MQTT] → {cfg.TOPIC_LABJACK_METRICS}: "
                          f"{payload}")
            except ljm.LJMError as e:
                _publish_error("labjack_driver",
                               f"AIN read failed: {e}")
            except Exception as e:
                _publish_error("labjack_driver",
                               f"Unexpected error in poll loop: {e}")

            time.sleep(cfg.LABJACK_POLL_RATE_S)

    except KeyboardInterrupt:
        print("\n[DRIVER] Stopping...")
    finally:
        # Safe shutdown: turn everything off before exiting
        stop_event.set()
        try:
            ljm.eWriteName(handle, f"FIO{cfg.DOUT_VACUUM_VALVE}", 1)
            ljm.eWriteName(handle, f"FIO{cfg.DOUT_PUMP}",         1)
            mqtt_client.publish(
                cfg.TOPIC_VALVE_STATE,
                json.dumps({"state": "CLOSED", "timestamp": time.time()}),
                retain=True)
            mqtt_client.publish(
                cfg.TOPIC_PUMP_STATE,
                json.dumps({"state": "OFF", "timestamp": time.time()}),
                retain=True)
        except Exception:
            pass
        ljm.close(handle)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print("[DRIVER] Shutdown complete.")


if __name__ == "__main__":
    main()
