# test_labjack_hardware.py
# Just reads AIN0 and prints it. No MQTT, no broker needed.

from labjack import ljm
import mqtt_config as cfg
import time

print("Opening LabJack...")
handle = ljm.openS("T7", "ANY", "ANY")
info   = ljm.getHandleInfo(handle)
print(f"Connected — Serial: {info[2]}\n")
print("Reading AIN0 every second. Press Ctrl+C to stop.\n")

try:
    while True:
        volts    = ljm.eReadName(handle, "AIN0")
        pressure = cfg.voltage_to_torr(volts)
        print(f"AIN0: {volts:.4f} V  →  {pressure:.3e} Torr")
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    ljm.close(handle)
    print("Done.")