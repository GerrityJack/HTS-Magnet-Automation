# HTS Magnet Testing Automation

A decoupled, fail-safe automation and data logging system for high-temperature superconductor magnet testing at PPPL. The system automates vacuum pump-down sequences, monitors a Cryomech AL630 helium compressor, and logs all telemetry to a time-series database for long-term analysis in Grafana.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Hardware](#2-hardware)
3. [LabJack Wiring Reference](#3-labjack-wiring-reference)
4. [Software Architecture](#4-software-architecture)
5. [File Reference](#5-file-reference)
6. [First-Time Setup](#6-first-time-setup)
7. [Moving to a New Computer](#7-moving-to-a-new-computer)
8. [Daily Operation](#8-daily-operation)
9. [The GUI — Page by Page](#9-the-gui--page-by-page)
10. [Recipes — How They Work](#10-recipes--how-they-work)
11. [Configuration Reference](#11-configuration-reference)
12. [Data Pipeline](#12-data-pipeline)
13. [Adding to the System](#13-adding-to-the-system)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. System Overview

The system is built around a single principle: **no single script controls everything**. Each piece of hardware has its own lightweight driver script that runs independently in the background. These scripts communicate exclusively through an MQTT message broker (Mosquitto). The GUI subscribes to the same broker and never touches hardware directly. The data logger does the same, writing to QuestDB without any knowledge of where the data came from.

This means:
- The GUI can crash without affecting hardware operation
- The compressor driver can restart without affecting the LabJack
- Any computer on the same network can monitor the system
- Individual components can be tested independently

```
[labjack_mqtt_driver.py]  ──\
[compressor_mqtt_driver.py] ──>  [Mosquitto MQTT Broker]  ──>  [lab_gui.py]
                                                           ──>  [lab_logger.py]
                                                                     |
                                                               [QuestDB]
                                                                     |
                                                               [Grafana]
```

---

## 2. Hardware

| Device | Role | Connection |
|---|---|---|
| LabJack T7-Pro | DAQ and relay control | USB to PC |
| SunFounder 2-Channel Relay Board | Switches power to valve and pump | 5V logic from LabJack FIO pins |
| ALITOVE 24V 5A Power Supply | Powers electromechanical valve | 110V AC wall outlet |
| Electromechanical Vacuum Valve | Opens/closes vacuum line | 24V DC via relay |
| Roughing Pump | Evacuates the system | 110V AC via relay |
| Cryomech AL630 Compressor | Helium cooling | Modbus RTU serial via USB adapter |
| Vacuum Pressure Gauge | Measures chamber pressure | 0-10V analog signal to LabJack AIN0 |
| Emergency Stop Button | Physical safety interlock | Hardwired inline on 24V positive rail |

**Critical relay note:** The SunFounder relay board is **Active-Low**. Writing a LOW (0V) signal to the FIO pin energizes the relay coil and closes the circuit (device ON). Writing HIGH (5V) de-energizes it (device OFF). All relays are wired to COM and NO (Normally Open) terminals so that a power loss puts everything in a safe OFF state. The software enforces the same on boot by writing HIGH to all relay pins before any other action.

---

## 3. LabJack Wiring Reference

```
LabJack T7-Pro
--------------

ANALOG INPUTS
  AIN0  <--  Vacuum gauge analog output (0-10V)
             Gauge formula: Pressure (Torr) = 10^(V - 4)
             At atmosphere: ~6.88V
             Disconnect detected above 8.0V (pin floating)

DIGITAL OUTPUTS (Active-Low relay board)
  FIO0  -->  Relay CH1  -->  Vacuum line valve (Normally Closed)
             LOW  = relay ON  = valve OPEN
             HIGH = relay OFF = valve CLOSED  <-- safe default

  FIO1  -->  Relay CH2  -->  Roughing pump power
             LOW  = relay ON  = pump ON
             HIGH = relay OFF = pump OFF       <-- safe default

POWER
  VS   -->  Relay board VCC (5V)
  GND  -->  Relay board GND

RELAY BOARD OUTPUT (COM + NO terminals only, NC left empty)
  CH1 COM/NO  -->  24V loop for vacuum valve
  CH2 COM/NO  -->  110V loop for roughing pump (via appropriately rated relay)

EMERGENCY STOP
  E-Stop button hardwired inline between 24V PSU positive terminal
  and the COM terminal of the relay board. Physically cuts power
  to both valve and pump instantly, bypassing all software.
```

**To add a new device:** Connect its power circuit to an unused relay channel (CH3 on an expanded board). Add a new `DOUT_` constant in `mqtt_config.py`, a new control topic, and handle it in `labjack_mqtt_driver.py`'s `on_message` handler. See [Adding to the System](#13-adding-to-the-system).

---

## 4. Software Architecture

### Scripts and their responsibilities

| Script | What it does | Talks to |
|---|---|---|
| `labjack_mqtt_driver.py` | Reads AIN0, controls relay FIO pins, publishes state | MQTT only |
| `compressor_mqtt_driver.py` | Reads AL630 registers via Modbus serial, publishes metrics | MQTT only |
| `lab_logger.py` | Subscribes to all metrics, writes to QuestDB every 5s (1s during runs) | MQTT + QuestDB |
| `lab_gui.py` | Displays data, sends commands, runs recipes | MQTT only |
| `modbus_client.py` | Low-level Modbus TCP/RTU communication library | Serial port |
| `mqtt_config.py` | All configuration constants and `validate_config()` | Imported by all scripts |
| `questdb_client.py` | Creates a configured QuestDB sender | Imported by logger |

### MQTT topic tree

```
lab/
  basement/
    labjack1/
      metrics          --> {"vacuum_pressure_torr": ..., "vacuum_gauge_volts": ..., "gauge_connected": ...}
      valve/control    <-- "OPEN" | "CLOSE"
      valve/state      --> {"state": "OPEN"|"CLOSED", "timestamp": ...}
      pump/control     <-- "ON" | "OFF"
      pump/state       --> {"state": "ON"|"OFF", "timestamp": ...}
      heartbeat        --> {"alive": true, "timestamp": ...}
    compressor1/
      metrics          --> {"helium_pressure": ..., "oil_temp": ..., ...}
      control          <-- "COMPRESSOR_ON" | "COMPRESSOR_OFF" | "START_MONITORING" | "PAUSE_MONITORING"
      state            --> {"commanded": "ON"|"OFF", "confirmed": bool, "timestamp": ...}
      heartbeat        --> {"alive": true, "timestamp": ...}
    pyside_gui/
      status           --> "ONLINE" | "OFFLINE"  (Last Will and Testament)
  errors               --> {"source": "...", "message": "...", "timestamp": ...}
  run_event            --> {"event": "START"|"END", "recipe": "...", "outcome": "...", "timestamp": ...}
  logger/control       <-- "HIGH_SPEED_START" | "HIGH_SPEED_STOP"
```

`-->` means published by a driver or GUI
`<--` means subscribed to and acted on

---

## 5. File Reference

```
Automation System Files/
  mqtt_config.py              All hardware settings, pin numbers, topic names,
                              thresholds, and validate_config()
  labjack_mqtt_driver.py      LabJack hardware driver
  compressor_mqtt_driver.py   Cryomech AL630 Modbus driver
  modbus_client.py            Low-level Modbus library (do not modify casually)
  lab_logger.py               MQTT -> QuestDB data logger
  lab_gui.py                  PySide6 control GUI
  questdb_client.py           QuestDB connection helper
  requirements.txt            Python package list for pip
  setup_environment.bat       One-time environment setup (run on new machines)
  startup.bat                 Launch everything (run daily)
  verify_environment.py       Check that all dependencies and services are working
  test_pseudo.py              76 unit tests covering all core logic
  test_labjack_hardware.py    Standalone LabJack AIN0 read test (no MQTT needed)
  test_questdb_connection.py  Write test rows to QuestDB to verify pipeline
  grafana_dashboard.json      Import this into Grafana to get the dashboard
  venv/                       Python virtual environment (created by setup, not committed)
  recipes/                    JSON recipe files (created by GUI)
  run_logs/                   Text run logs (created by GUI during recipe runs)
```

---

## 6. First-Time Setup

### Prerequisites (install manually before running anything)

1. **Python 3.13** — https://www.python.org/downloads/release/python-3130/
   Tick "Add Python to PATH" during installation.

2. **Docker Desktop** — https://www.docker.com/products/docker-desktop/
   Used to run QuestDB in a container.

3. **Mosquitto MQTT Broker** — https://mosquitto.org/download/
   Install with default settings.

4. **LabJack LJM Driver** — https://labjack.com/pages/support
   Navigate to Software/Driver -> LJM Software Installers.
   Install before running `setup_environment.bat`.

### Environment setup

Place all project files in:
```
C:\Users\gerri\Desktop\PPPL\Automation System Files\
```

Then double-click `setup_environment.bat`. It will:
- Create a Python virtual environment at `...\Automation System Files\venv\`
- Install all packages from `requirements.txt`
- Ask whether to install LabJack LJM Python bindings
- Run `verify_environment.py` automatically at the end

A successful setup ends with all checks showing PASS (QuestDB will show WARN since it starts on-demand, not always — this is expected).

### Grafana setup

```
1. Start QuestDB (startup.bat does this automatically)
2. Open http://localhost:3000 in a browser
3. Connections -> Data Sources -> Add new -> PostgreSQL
   Host:     localhost:8812
   Database: qdb
   User:     admin
   Password: quest
   SSL Mode: disable
4. Save & Test (should show green checkmark)
5. Dashboards -> New -> Import -> Upload grafana_dashboard.json
```

---

## 7. Moving to a New Computer

1. Copy the entire `Automation System Files` folder to the new machine
2. **Delete the `venv` folder** from the copy — it contains compiled binaries that only work on the machine it was built on
3. Install all prerequisites listed in Section 6 on the new machine
4. Run `setup_environment.bat` to rebuild the venv
5. Plug in the USB-to-serial adapter for the compressor, then open Device Manager -> Ports (COM & LPT) and note the assigned COM port
6. Update `COMPRESSOR_SERIAL_PORT` in `mqtt_config.py` to match
7. Run `verify_environment.py` to confirm everything is working
8. The LabJack does **not** need a COM port update — the LJM driver finds it by serial number automatically

**Note on COM port reassignment:** Windows assigns COM numbers based on which USB port the adapter is plugged into and the history on that machine. If you unplug and replug the adapter into a different USB port, Windows may assign a new number. Always check Device Manager if the compressor driver fails to connect.

---

## 8. Daily Operation

Double-click `startup.bat`. It will:

1. Activate the Python virtual environment
2. Start Mosquitto (as a Windows service, or launch the exe if not installed as a service)
3. Start QuestDB Docker container (creates it on first run, restarts it thereafter)
4. Launch the LabJack driver in a minimized window
5. Launch the compressor driver in a minimized window
6. Launch the data logger in a minimized window
7. Open the GUI in the foreground

The startup script checks whether each driver is already running before launching it, so running it a second time is safe — it will skip any processes that are already active.

When you close the GUI, a prompt asks whether to shut down the background driver windows. QuestDB is always left running to protect data.

**To run the tests:**
```
cd "C:\Users\gerri\Desktop\PPPL\Automation System Files"
call venv\Scripts\activate.bat
python test_pseudo.py
```
All 76 tests should pass. Run these after making any changes to the core logic files.

---

## 9. The GUI — Page by Page

### Status Page

The main monitoring and control page. Divided into four areas:

**Driver Status** (top) — Two dots show whether the LabJack driver and compressor driver are actively publishing heartbeats. A dot turns red if no heartbeat has been received in 15 seconds, indicating the script has crashed or the machine lost network connectivity.

**Background Processes** — Shows whether each background Python script is actually running on this machine (checked every 3 seconds via PowerShell). Each has a Stop button to kill that process if needed.

**System Errors / Warnings** — Displays errors published by any script over MQTT in real time. Errors include source, timestamp, and message. Includes a Clear button. This is where interlock warnings, Modbus communication failures, and QuestDB write errors appear.

**Device Controls** (left column):

- *Roughing Pump* — Has an arm interlock. Click "Arm Pump Controls", confirm the dialog, then use Turn ON / Turn OFF. Controls disarm automatically after each action. This prevents accidental pump toggling.

- *Vacuum Valve* — Direct OPEN / CLOSE buttons with no arm requirement. The valve is a routine operational control. The LabJack driver will publish a warning to the error panel if the valve is opened while the pump is off, or if the pump is turned off while the valve is open, but will execute the command anyway (soft interlock with override).

- *Helium Compressor* — Has a stronger arm interlock with a warning dialog reminding you that the helium loop must be connected, the system must be under vacuum, and coolant water flow must be confirmed before starting. Controls disarm automatically after each action.

**Compressor Telemetry** (right column) — Live readouts of helium pressure, low pressure, delta pressure, temperatures, and motor current. State row shows operating state, warnings, and alarms.

### Recipe Editor Page

Build and save pump-down recipes. Parameters:

- **Recipe Name** — Used as the filename and in run logs
- **Foreline pump duration** — How long to run the pump with the valve closed before opening the valve (clears the foreline of air)
- **Target chamber pressure** — The pressure the system will pump to before closing the valve for the leak check
- **Leak check duration** — How long to hold the valve closed while measuring pressure rise
- **Max allowed leak rate** — If the measured rise rate (Torr/s) exceeds this, the run aborts with a warning
- **Delay before compressor on** — Wait time after leak check passes before starting the compressor

The process flow summary below the form updates live as you adjust parameters. Recipes are saved as JSON files in the `recipes/` folder. The editor validates the recipe before saving — it will refuse to save a target pressure of zero or above atmosphere.

### Run Recipe Page

Load a saved recipe and execute it. Panels:

**Recipe selector** (left) — Lists all saved recipe files. Click one to load its parameters into the summary panel.

**Selected Recipe** (right) — Shows the loaded recipe parameters. Click Refresh List if you added a recipe in the editor while this page was open.

**Run Progress** — Five step indicators, each with a coloured dot:
- Amber = currently running
- Green = completed successfully
- Red = failed or aborted
- Grey = not yet reached

**Live Values** — Chamber pressure and leak rate update in real time during the run. Pressure comes directly from the LabJack driver's MQTT stream (not the 5-second logger).

**Event Log** — Timestamped log of every step event, including pressure readings during pump-down and the measured leak rate. This log is also written to a file in `run_logs/` for permanent reference.

**Run / Abort buttons** — Run starts the recipe (and auto-disarms any armed interlocks on the Status page). Abort stops the run at the next safe point and executes a safe shutdown (valve closed, pump off).

**Open Run Logs** — Opens the `run_logs/` folder in Windows Explorer.

### Live Plots Page

Manual live data visualization. Not connected to QuestDB — data is held in memory for the current session only.

- **Start Recording** — Begins accumulating data from the MQTT stream. The sample counter increments with every new reading.
- **Stop Recording** — Freezes the plots for inspection. The data is kept in memory.
- **Export CSV** — Saves all recorded data to a timestamped CSV file in `run_logs/`. Opens a save dialog so you can choose the location.
- **Clear** — Discards all data and resets the plots.

Three plots are shown: chamber pressure (log scale), helium pressure, and oil temperature. More channels can be added by extending the `on_compressor_data` slot.

---

## 10. Recipes — How They Work

A recipe is a JSON file in the `recipes/` folder. You can edit them directly or use the Recipe Editor in the GUI.

### Recipe file format

```json
{
  "name": "Standard pump-down",
  "foreline_pump_duration_s": 30,
  "target_pressure_torr": 1e-3,
  "leak_check_duration_s": 60,
  "max_leak_rate_torr_per_s": 5e-5,
  "compressor_delay_s": 5
}
```

### Execution sequence

```
Step 1: PUMP ON (valve closed)
        Run for foreline_pump_duration_s seconds.
        Purpose: clear atmospheric air from the roughing pump's foreline.

Step 2: VALVE OPEN
        Wait until vacuum_pressure_torr <= target_pressure_torr.
        Timeout: PUMP_TIMEOUT_S (default 30 minutes, set in mqtt_config.py).
        If timeout occurs and pressure is above PUMP_ABORT_THRESHOLD_TORR
        (default 10 Torr): close valve, turn off pump, ABORT.
        If timeout occurs and pressure is below threshold: proceed with warning.

Step 3: VALVE CLOSE
        Measure pressure rise over leak_check_duration_s seconds.
        Leak rate = linear regression slope over all samples (Torr/s).
        If leak_rate > max_leak_rate_torr_per_s: close valve, pump off, ABORT.

Step 4: (valve already closed from step 3)

Step 5: Wait compressor_delay_s seconds, then COMPRESSOR ON.
        If the compressor was already running before the recipe started,
        this step is skipped (pre-check prevents double-start).
```

At any point, pressing Abort closes the valve and turns the pump off before stopping.

At the start and end of every run, an event is published to MQTT and written to the `run_events` table in QuestDB. This lets Grafana show run boundaries as annotations on historical plots.

---

## 11. Configuration Reference

All configuration lives in `mqtt_config.py`. Key values to know:

| Setting | Default | What it does |
|---|---|---|
| `MQTT_BROKER_HOST` | `"localhost"` | Address of the Mosquitto broker |
| `COMPRESSOR_SERIAL_PORT` | `"COM5"` | Serial port for compressor. Check Device Manager after moving machines. |
| `COMPRESSOR_MODBUS_ID` | `16` | Modbus device address of the AL630 |
| `AIN_VACUUM_PRESSURE` | `0` | LabJack AIN channel for the pressure gauge |
| `DOUT_VACUUM_VALVE` | `0` | LabJack FIO channel for the valve relay |
| `DOUT_PUMP` | `1` | LabJack FIO channel for the pump relay |
| `GAUGE_DISCONNECT_THRESHOLD_V` | `8.0` | Voltages above this are treated as "gauge not connected" |
| `PUMP_TIMEOUT_S` | `1800` | Maximum time to wait for target pressure (30 minutes) |
| `PUMP_ABORT_THRESHOLD_TORR` | `10.0` | If pressure is above this at timeout, abort to protect the pump |
| `QUESTDB_FLUSH_INTERVAL_S` | `5` | Normal logging interval |
| `QUESTDB_FLUSH_INTERVAL_FAST_S` | `1` | Logging interval during recipe runs |

**After changing `mqtt_config.py`, restart all running scripts.**

`validate_config()` is called automatically at startup by every script and will print a clear error message and exit if any setting is out of range.

---

## 12. Data Pipeline

```
LabJack driver (0.5s poll)
    |
    v
MQTT broker  ──> GUI (live display, 0.5s latency)
    |
    v
lab_logger.py
    |
    |-- Normal mode:    writes every 5s  -> labjack_metrics table
    |-- High-speed:     writes every 1s  -> labjack_metrics table  (during recipe runs)
    |-- Run events:     writes immediately -> run_events table
    |
    v
QuestDB (localhost:9000, PostgreSQL on port 8812)
    |
    v
Grafana (localhost:3000)
```

### QuestDB tables

**`labjack_metrics`**
| Column | Type | Description |
|---|---|---|
| timestamp | TIMESTAMP | Auto-set by logger |
| vacuum_pressure_torr | DOUBLE | Calculated from gauge voltage |
| vacuum_gauge_volts | DOUBLE | Raw AIN0 reading |

**`compressor_metrics`**
| Column | Type | Description |
|---|---|---|
| timestamp | TIMESTAMP | Auto-set by logger |
| helium_pressure | DOUBLE | High-side helium pressure (PSI) |
| low_pressure | DOUBLE | Low-side pressure (PSI) |
| delta_pressure | DOUBLE | Differential pressure (PSI) |
| helium_temp | DOUBLE | Helium temperature (deg C) |
| oil_temp | DOUBLE | Oil temperature (deg C) |
| coolant_in_temp | DOUBLE | Coolant inlet temperature (deg C) |
| coolant_out_temp | DOUBLE | Coolant outlet temperature (deg C) |
| motor_current | DOUBLE | Motor current (A) |
| operating_state | SYMBOL | Text state from compressor |
| compressor_running | SYMBOL | "True" or "False" |
| warning_state | SYMBOL | Active warning description |
| alarm_state | SYMBOL | Active alarm description |

**`run_events`**
| Column | Type | Description |
|---|---|---|
| timestamp | TIMESTAMP | Auto-set by logger |
| event | SYMBOL | "START" or "END" |
| recipe | SYMBOL | Recipe name |
| outcome | SYMBOL | "SUCCESS", "FAILED", or "" (for START) |
| timestamp_unix | DOUBLE | Unix timestamp from the GUI clock |

To query all successful runs from the last month:
```sql
SELECT timestamp, recipe, outcome
FROM run_events
WHERE event = 'END'
  AND outcome = 'SUCCESS'
  AND timestamp > dateadd('d', -30, now())
ORDER BY timestamp DESC;
```

---

## 13. Adding to the System

### Adding a new relay-controlled device

1. Wire the device to an unused relay channel (e.g. CH3)
2. In `mqtt_config.py`:
   ```python
   DOUT_NEW_DEVICE       = 2    # FIO2 -> Relay CH3 -> New device
   TOPIC_NEW_DEVICE_CONTROL = "lab/basement/labjack1/new_device/control"
   TOPIC_NEW_DEVICE_STATE   = "lab/basement/labjack1/new_device/state"
   ```
3. In `labjack_mqtt_driver.py`, add to the `on_message` handler:
   ```python
   elif topic == cfg.TOPIC_NEW_DEVICE_CONTROL:
       if payload == "ON":
           set_relay(handle, cfg.DOUT_NEW_DEVICE, activate=True,
                     label="New device", mqtt_client=client)
           _new_device_on = True
       elif payload == "OFF":
           set_relay(handle, cfg.DOUT_NEW_DEVICE, activate=False,
                     label="New device", mqtt_client=client)
           _new_device_on = False
   ```
4. Add the initial state to the safety init block in `main()`
5. Add a subscribe call and control buttons in `lab_gui.py`
6. Add tests for the new device's relay logic in `test_pseudo.py`

### Adding a new analog sensor

1. Wire the sensor output to an unused AIN channel
2. In `mqtt_config.py`, add the channel constant and a conversion function
3. In `labjack_mqtt_driver.py`, read the channel in the sensor polling loop and include it in the MQTT payload
4. In `lab_gui.py`, add a `DataReadout` widget for it on the Status page
5. In `lab_logger.py`, add the new field to the `labjack_metrics` write block

### Adding a new plot channel

In `PlotsPage` in `lab_gui.py`:
1. Add a new data buffer dict entry in `__init__`: `"t_new": [], "v_new": []`
2. Add a new `pg.PlotWidget` and curve in `_build_ui`
3. Append to the buffer and call `setData` in `on_labjack_data` or `on_compressor_data`
4. Add the new column to `_export_csv`

### Changing the MQTT topic structure

All topic strings are in `mqtt_config.py`. Both the driver scripts and the GUI import from there, so changing a topic in one place updates all scripts. After changing any topic, restart all running scripts — retained messages on the old topic will not be automatically cleared from Mosquitto.

### Adding a second vacuum chamber or compressor

The current topic structure already has device numbers (`labjack1`, `compressor1`). To add a second device:
1. Add a second set of topic constants for `labjack2` or `compressor2`
2. Copy the driver script and configure it for the new hardware
3. Add a second set of controls to the GUI Status page
4. The logger will need a second set of table write blocks

---

## 14. Troubleshooting

**startup.bat closes immediately without showing anything**
Run it via `cmd /k` to keep the window open:
```
cmd /k "C:\Users\gerri\Desktop\PPPL\Automation System Files\startup.bat"
```

**"[MQTT] Could not connect: No connection could be made"**
Mosquitto is not running. Start it:
```
net start mosquitto
```
Or install from https://mosquitto.org/download/ if not yet installed.

**LabJack error 1298 LJME_ATTR_LOAD_COMM_FAILURE**
A previous Python process is holding the LabJack handle open. Kill all Python processes:
```powershell
Get-WmiObject Win32_Process |
  Where-Object { $_.CommandLine -like "*labjack*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
Then unplug and replug the USB cable before retrying.

**Compressor driver: "Could not connect... check Device Manager"**
The COM port has changed. Open Device Manager -> Ports (COM & LPT), note the current port number, and update `COMPRESSOR_SERIAL_PORT` in `mqtt_config.py`.

**"table does not exist [table=labjack_metrics]" in QuestDB console**
The logger has not yet written any real data (gauge not connected, or logger not running). Run `test_questdb_connection.py` to create the table with dummy data and verify the pipeline works.

**Gauge reads ~900,000 Torr or "NO SIGNAL" in the GUI**
The pressure gauge is not physically connected to AIN0. The pin is floating near 8-10V. This is normal during bench testing. The system correctly shows "NO SIGNAL" and skips writing to QuestDB when the voltage exceeds the disconnect threshold (8.0V, configurable in `mqtt_config.py`).

**Grafana shows "${DS_QUESTDB}" warning on all panels**
The datasource name in Grafana does not match what the dashboard expects. Go to Connections -> Data Sources and confirm the PostgreSQL source is named exactly `QuestDB` (capital Q, capital D, capital B). If you named it differently, edit the name to match.

**Recipe run aborts at step 2 (pump-down timeout)**
The system could not reach the target pressure within `PUMP_TIMEOUT_S` seconds (default 30 minutes). Check for leaks in the system. If the abort threshold (`PUMP_ABORT_THRESHOLD_TORR`, default 10 Torr) was exceeded, the valve and pump were closed to protect the pump. If the system was pumping well but just not reaching a very low target, consider raising the target pressure slightly or increasing the timeout.

**Tests fail after modifying a core file**
Run `python test_pseudo.py` from the lab folder. Failed tests print the specific assertion that failed. The test file mirrors the exact logic in the production code, so a test failure means the logic change needs to be reflected in the test (or vice versa).

---

*Last updated: June 2026*
*System maintained by PPPL HTS Magnet Testing Group*
