"""
lab_gui.py
-----------------------------------------------------------------------------
PySide6 GUI for the HTS Magnet Testing Automation.

Pages:
  1. Status     -- live sensor readings, driver health, device on/off controls
  2. Recipe     -- build and save pump-down recipes (JSON files)
  3. Run        -- load and execute a recipe with live step progress + leak rate
  4. Plots      -- live scrolling plots of pressure and compressor values

Architecture:
  * MqttWorker runs in a QThread and emits Qt signals with parsed data.
  * The main thread (GUI) only ever receives signals -- it never touches MQTT
    sockets directly, keeping the UI responsive.
  * Outgoing commands (valve open, pump on, etc.) are sent via
    mqtt_client.publish() calls from the worker thread, which is safe in
    paho-mqtt with loop_start().

Dependencies:
  pip install PySide6 paho-mqtt pyqtgraph numpy
"""

import json
import os
import sys
import time
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
import paho.mqtt.client as mqtt
import subprocess

from PySide6.QtCore import (
    Qt, QThread, Signal, QObject, QTimer, Slot
)
from PySide6.QtGui import QFont, QColor, QPalette, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QStackedWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QDoubleSpinBox, QSpinBox, QLineEdit,
    QGroupBox, QFileDialog, QMessageBox, QFrame, QScrollArea,
    QSizePolicy, QListWidget, QListWidgetItem, QSplitter,
    QTextEdit, QTabWidget,
)

try:
    import mqtt_config as cfg
    cfg.validate_config()
except ValueError as e:
    print(f"[CONFIG] FATAL: {e}")
    sys.exit(1)
except ImportError:
    print("ERROR: mqtt_config.py not found. Place it in the same folder as lab_gui.py.")
    sys.exit(1)

# Anchor these to the folder this script lives in, not the current
# working directory -- so they land in the right place whether the GUI
# is launched via startup.bat, double-clicked, or started from the
# Background Processes panel.
_SCRIPT_DIR = Path(__file__).resolve().parent
RECIPE_DIR  = _SCRIPT_DIR / "recipes"
RUN_LOG_DIR = _SCRIPT_DIR / "run_logs"
RECIPE_DIR.mkdir(exist_ok=True)
RUN_LOG_DIR.mkdir(exist_ok=True)

# -----------------------------------------------------------------------------
# Design tokens
# -----------------------------------------------------------------------------
CLR_BG          = "#1a1e24"
CLR_PANEL       = "#22272f"
CLR_BORDER      = "#2e3440"
CLR_ACCENT      = "#4a9eff"
CLR_ACCENT_DIM  = "#1d3a5e"
CLR_TEXT        = "#d8dde6"
CLR_TEXT_DIM    = "#6c7a8a"
CLR_GREEN       = "#3ddc84"
CLR_AMBER       = "#f0a830"
CLR_RED         = "#e05c5c"
CLR_MONO        = "Courier New"
CLR_SANS        = "Arial"

STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {CLR_BG};
    color: {CLR_TEXT};
    font-family: {CLR_SANS};
    font-size: 13px;
}}
QGroupBox {{
    background-color: {CLR_PANEL};
    border: 1px solid {CLR_BORDER};
    border-radius: 6px;
    margin-top: 10px;
    padding: 8px;
    font-weight: bold;
    font-size: 12px;
    color: {CLR_TEXT_DIM};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {CLR_TEXT_DIM};
    font-size: 11px;
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QPushButton {{
    background-color: {CLR_PANEL};
    border: 1px solid {CLR_BORDER};
    border-radius: 4px;
    color: {CLR_TEXT};
    padding: 6px 16px;
    font-size: 13px;
}}
QPushButton:hover {{
    border-color: {CLR_ACCENT};
    color: {CLR_ACCENT};
}}
QPushButton:pressed {{
    background-color: {CLR_ACCENT_DIM};
}}
QPushButton:disabled {{
    color: {CLR_TEXT_DIM};
    border-color: {CLR_BORDER};
}}
QPushButton#btnAccent {{
    background-color: {CLR_ACCENT};
    border-color: {CLR_ACCENT};
    color: white;
    font-weight: bold;
}}
QPushButton#btnAccent:hover {{
    background-color: #5aadff;
}}
QPushButton#btnAccent:disabled {{
    background-color: {CLR_BG};
    border-color: {CLR_BORDER};
    color: {CLR_TEXT_DIM};
}}
QPushButton#btnDanger {{
    background-color: {CLR_RED};
    border-color: {CLR_RED};
    color: white;
    font-weight: bold;
}}
QPushButton#btnDanger:hover {{
    background-color: #f07070;
}}
QPushButton#btnDanger:disabled {{
    background-color: {CLR_BG};
    border-color: {CLR_BORDER};
    color: {CLR_TEXT_DIM};
}}
QPushButton#btnWarning {{
    background-color: {CLR_AMBER};
    border-color: {CLR_AMBER};
    color: #1a1e24;
    font-weight: bold;
}}
QLabel {{
    background: transparent;
}}
QDoubleSpinBox, QSpinBox, QLineEdit {{
    background-color: {CLR_BG};
    border: 1px solid {CLR_BORDER};
    border-radius: 4px;
    color: {CLR_TEXT};
    padding: 4px 8px;
    font-size: 13px;
}}
QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus {{
    border-color: {CLR_ACCENT};
}}
QScrollArea {{
    border: none;
}}
QTextEdit {{
    background-color: {CLR_BG};
    border: 1px solid {CLR_BORDER};
    border-radius: 4px;
    color: {CLR_TEXT};
    font-family: {CLR_MONO};
    font-size: 12px;
}}
QListWidget {{
    background-color: {CLR_BG};
    border: 1px solid {CLR_BORDER};
    border-radius: 4px;
    color: {CLR_TEXT};
}}
QListWidget::item:selected {{
    background-color: {CLR_ACCENT_DIM};
    color: {CLR_ACCENT};
}}
QSplitter::handle {{
    background-color: {CLR_BORDER};
}}
"""

# -----------------------------------------------------------------------------
# Reusable widgets
# -----------------------------------------------------------------------------
class StatusDot(QLabel):
    """A small coloured circle used as an LED-style indicator."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.set_state("unknown")

    def set_state(self, state: str):
        """state: 'ok', 'warn', 'error', 'unknown'"""
        colours = {
            "ok":      CLR_GREEN,
            "warn":    CLR_AMBER,
            "error":   CLR_RED,
            "unknown": CLR_TEXT_DIM,
        }
        c = colours.get(state, CLR_TEXT_DIM)
        self.setStyleSheet(
            f"background:{c}; border-radius:7px; border:1px solid #00000066;"
        )


class DataReadout(QWidget):
    """
    A labelled numeric readout: small grey label on top, large mono value below.
    """
    def __init__(self, label: str, unit: str = "", parent=None):
        super().__init__(parent)
        self._unit = unit
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.lbl_name = QLabel(label.upper())
        self.lbl_name.setStyleSheet(
            f"color:{CLR_TEXT_DIM}; font-size:10px; letter-spacing:1px;"
        )

        self.lbl_value = QLabel("--")
        self.lbl_value.setStyleSheet(
            f"color:{CLR_TEXT}; font-family:{CLR_MONO}; font-size:20px;"
        )

        layout.addWidget(self.lbl_name)
        layout.addWidget(self.lbl_value)

    def set_value(self, value, fmt: str = ".4g"):
        if value is None:
            self.lbl_value.setText("--")
        else:
            try:
                txt = format(float(value), fmt)
            except (ValueError, TypeError):
                txt = str(value)
            self.lbl_value.setText(f"{txt} {self._unit}".strip())

    def set_alarm(self, active: bool):
        colour = CLR_RED if active else CLR_TEXT
        self.lbl_value.setStyleSheet(
            f"color:{colour}; font-family:{CLR_MONO}; font-size:20px;"
        )


class NavButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setFixedHeight(40)
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-left: 3px solid transparent;
                color: {CLR_TEXT_DIM};
                font-size: 13px;
                text-align: left;
                padding-left: 16px;
                border-radius: 0;
            }}
            QPushButton:hover {{
                color: {CLR_TEXT};
                background: {CLR_PANEL};
            }}
            QPushButton:checked {{
                border-left-color: {CLR_ACCENT};
                color: {CLR_ACCENT};
                background: {CLR_PANEL};
                font-weight: bold;
            }}
        """)


# -----------------------------------------------------------------------------
# MQTT Worker -- lives in its own QThread
# -----------------------------------------------------------------------------
class MqttWorker(QObject):
    sig_compressor_data   = Signal(dict)
    sig_labjack_data      = Signal(dict)
    sig_compressor_alive  = Signal(bool)   # True/False
    sig_labjack_alive     = Signal(bool)
    sig_error             = Signal(str, str, str)  # (source, message, time_str)
    sig_valve_state       = Signal(str)   # "OPEN" | "CLOSED"
    sig_pump_state        = Signal(str)   # "ON" | "OFF"
    sig_compressor_state  = Signal(dict)  # {commanded, confirmed, timestamp}

    # Track when the last heartbeat was received to detect stale drivers
    _HEARTBEAT_TIMEOUT_S  = 15

    def __init__(self):
        super().__init__()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

        # Last-seen timestamps for heartbeats
        self._last_hb = {
            "compressor": 0.0,
            "labjack":    0.0,
        }
        self._prev_alive = {"compressor": None, "labjack": None}
        # Latest compressor running state (updated from metrics)
        self._compressor_running: bool = False

        # LWT: tell broker to publish OFFLINE if we disconnect unexpectedly
        self._client.will_set(cfg.TOPIC_GUI_STATUS, "OFFLINE", retain=True)

    def start(self):
        try:
            self._client.connect(cfg.MQTT_BROKER_HOST,
                                  cfg.MQTT_BROKER_PORT,
                                  cfg.MQTT_KEEPALIVE)
        except Exception as e:
            print(f"[MQTT] Could not connect: {e}")
            return
        self._client.loop_start()
        self._client.publish(cfg.TOPIC_GUI_STATUS, "ONLINE", retain=True)

        # Poll heartbeat freshness on a background timer
        self._hb_timer = threading.Thread(target=self._check_heartbeats, daemon=True)
        self._hb_timer.start()

    def stop(self):
        self._client.publish(cfg.TOPIC_GUI_STATUS, "OFFLINE", retain=True)
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, topic: str, payload: str, retain: bool = False):
        self._client.publish(topic, payload, retain=retain)

    def is_compressor_running(self) -> bool:
        """Return the last known compressor running state."""
        return self._compressor_running

    # -- Private --------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print("[MQTT-GUI] Connected to broker.")
            client.subscribe(cfg.TOPIC_COMPRESSOR_METRICS)
            client.subscribe(cfg.TOPIC_LABJACK_METRICS)
            client.subscribe(cfg.TOPIC_COMPRESSOR_HEARTBEAT)
            client.subscribe(cfg.TOPIC_LABJACK_HEARTBEAT)
            client.subscribe(cfg.TOPIC_ERRORS)
            client.subscribe(cfg.TOPIC_VALVE_STATE)
            client.subscribe(cfg.TOPIC_PUMP_STATE)
            client.subscribe(cfg.TOPIC_COMPRESSOR_STATE)
            # Subscribe to compressor metrics so we can check state before commands
            client.subscribe(cfg.TOPIC_COMPRESSOR_METRICS)

    def _on_message(self, client, userdata, msg):
        topic   = msg.topic
        payload = msg.payload.decode("utf-8").strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return

        if topic == cfg.TOPIC_COMPRESSOR_METRICS:
            self.sig_compressor_data.emit(data)
            # Track running state so recipe runner can pre-check it
            if "compressor_running" in data:
                self._compressor_running = bool(data["compressor_running"])
        elif topic == cfg.TOPIC_LABJACK_METRICS:
            self.sig_labjack_data.emit(data)
        elif topic == cfg.TOPIC_COMPRESSOR_HEARTBEAT:
            self._last_hb["compressor"] = time.time()
        elif topic == cfg.TOPIC_LABJACK_HEARTBEAT:
            self._last_hb["labjack"] = time.time()
        elif topic == cfg.TOPIC_ERRORS:
            source  = data.get("source",  "unknown")
            message = data.get("message", str(data))
            time_str = time.strftime(
                "%H:%M:%S",
                time.localtime(data.get("timestamp", time.time())))
            self.sig_error.emit(source, message, time_str)
        elif topic == cfg.TOPIC_VALVE_STATE:
            self.sig_valve_state.emit(data.get("state", "UNKNOWN"))
        elif topic == cfg.TOPIC_PUMP_STATE:
            self.sig_pump_state.emit(data.get("state", "UNKNOWN"))
        elif topic == cfg.TOPIC_COMPRESSOR_STATE:
            self.sig_compressor_state.emit(data)

    def _check_heartbeats(self):
        """Background thread: emit alive signals whenever status changes."""
        while True:
            time.sleep(2)
            now = time.time()
            for key in ("compressor", "labjack"):
                alive = (now - self._last_hb[key]) < self._HEARTBEAT_TIMEOUT_S
                if alive != self._prev_alive[key]:
                    self._prev_alive[key] = alive
                    if key == "compressor":
                        self.sig_compressor_alive.emit(alive)
                    else:
                        self.sig_labjack_alive.emit(alive)


# -----------------------------------------------------------------------------
# Page 1 -- Status
# -----------------------------------------------------------------------------
class ProcessMonitor(QGroupBox):
    """
    Shows whether each background Python script is currently running,
    and provides Start/Stop buttons for each one.

    Start launches the script in a new minimized console window using the
    same Python interpreter and working directory as the GUI itself, which
    mirrors exactly what startup.bat does for each driver. This means if a
    driver crashes or was never started, it can be relaunched without going
    back to a terminal.

    Refreshes every 3 seconds automatically.
    """

    PROCESSES = [
        ("LabJack Driver",      "labjack_mqtt_driver.py"),
        ("Compressor Driver",   "compressor_mqtt_driver.py"),
        ("Lab Logger",          "lab_logger.py"),
    ]

    def __init__(self, parent=None):
        super().__init__("Background Processes", parent)
        self._rows: dict[str, tuple] = {}   # name -> (dot, lbl, btn_start, btn_stop)
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(3000)
        self.refresh()

    def _build_ui(self):
        grid = QGridLayout(self)
        grid.setSpacing(10)
        grid.setColumnStretch(1, 1)

        for i, (name, script) in enumerate(self.PROCESSES):
            dot = StatusDot()
            lbl = QLabel(f"{name}: checking...")
            lbl.setStyleSheet(f"color:{CLR_TEXT_DIM};")

            btn_start = QPushButton("Start")
            btn_start.setObjectName("btnAccent")
            btn_start.setFixedWidth(60)
            btn_start.clicked.connect(
                lambda checked, s=script, n=name: self._start_process(s, n))

            btn_stop = QPushButton("Stop")
            btn_stop.setObjectName("btnDanger")
            btn_stop.setFixedWidth(60)
            btn_stop.setEnabled(False)
            btn_stop.clicked.connect(lambda checked, s=script: self._stop_process(s))

            grid.addWidget(dot, i, 0)
            grid.addWidget(lbl, i, 1)
            grid.addWidget(btn_start, i, 2)
            grid.addWidget(btn_stop, i, 3)
            self._rows[script] = (dot, lbl, btn_start, btn_stop)

    def _find_pids(self, script_name: str) -> list[int]:
        """
        Return PIDs of all python processes running this script.
        Uses a PowerShell one-liner which handles paths with spaces correctly
        and works on all modern Windows versions.
        """
        pids = []
        try:
            cmd = (
                "Get-WmiObject Win32_Process | "
                "Where-Object { "
                "($_.Name -eq 'python.exe' -or $_.Name -eq 'python3.exe') -and "
                f"$_.CommandLine -like '*{script_name}*'"
                " } | Select-Object -ExpandProperty ProcessId"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=8
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
        except Exception:
            pass
        return pids

    def _start_process(self, script_name: str, display_name: str):
        """
        Launch the script in a new minimized console window.
        Uses sys.executable so the same Python interpreter (and venv) that
        is running the GUI is also used for the driver -- this avoids any
        mismatch between a system Python and the project's virtual env.
        """
        if self._find_pids(script_name):
            QMessageBox.information(
                self, "Already Running",
                f"{display_name} is already running.")
            return

        script_path = Path(__file__).resolve().parent / script_name
        if not script_path.exists():
            QMessageBox.warning(
                self, "Not Found",
                f"Could not find {script_name} in:\n{script_path.parent}")
            return

        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_CONSOLE
            subprocess.Popen(
                [sys.executable, str(script_path)],
                cwd=str(script_path.parent),
                creationflags=creationflags,
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Failed to Start", f"Could not start {display_name}:\n{e}")
            return

        # Give it a moment to register, then refresh
        QTimer.singleShot(2000, self.refresh)

    def _stop_process(self, script_name: str):
        """Kill all instances of the given script."""
        pids = self._find_pids(script_name)
        if not pids:
            return
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, timeout=5)
            except Exception:
                pass
        # Refresh after a short delay
        QTimer.singleShot(1500, self.refresh)

    @Slot()
    def refresh(self):
        """Check each process and update indicators."""
        for script, (dot, lbl, btn_start, btn_stop) in self._rows.items():
            pids = self._find_pids(script)
            name = next(n for n, s in self.PROCESSES if s == script)
            if pids:
                count = len(pids)
                suffix = f"  ({count} instance{'s' if count > 1 else ''})"
                if count > 1:
                    dot.set_state("warn")
                    lbl.setText(f"{name}: RUNNING{suffix}")
                    lbl.setStyleSheet(f"color:{CLR_AMBER};")
                else:
                    dot.set_state("ok")
                    lbl.setText(f"{name}: RUNNING")
                    lbl.setStyleSheet(f"color:{CLR_GREEN};")
                btn_start.setEnabled(False)
                btn_stop.setEnabled(True)
            else:
                dot.set_state("error")
                lbl.setText(f"{name}: NOT RUNNING")
                lbl.setStyleSheet(f"color:{CLR_TEXT_DIM};")
                btn_start.setEnabled(True)
                btn_stop.setEnabled(False)


class ErrorPanel(QGroupBox):
    """
    Displays the last N errors received from any driver script via MQTT.
    Shows source, time, and message. New errors appear at the top.
    Clears automatically when the user clicks Clear.
    """
    MAX_ERRORS = 20

    def __init__(self, parent=None):
        super().__init__("System Errors / Warnings", parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self._count_lbl = QLabel("No errors")
        self._count_lbl.setStyleSheet(f"color:{CLR_TEXT_DIM}; font-size:12px;")
        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(60)
        btn_clear.clicked.connect(self.clear)
        header.addWidget(self._count_lbl)
        header.addStretch()
        header.addWidget(btn_clear)
        layout.addLayout(header)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(130)
        self._log.setStyleSheet(
            f"background:{CLR_BG}; border:1px solid {CLR_BORDER}; "
            f"font-family:{CLR_MONO}; font-size:11px; color:{CLR_TEXT};")
        layout.addWidget(self._log)
        self._error_count = 0

    @Slot(str, str, str)
    def add_error(self, source: str, message: str, time_str: str):
        self._error_count += 1
        # Prepend so newest is always at the top
        existing = self._log.toPlainText()
        line = f"[{time_str}] {source}: {message}"
        nl = chr(10)
        self._log.setPlainText(line + (nl + existing if existing else ""))
        self._count_lbl.setText(
            f"{self._error_count} error(s) since startup")
        self._count_lbl.setStyleSheet(
            f"color:{CLR_RED}; font-size:12px; font-weight:bold;")
        lines = self._log.toPlainText().split(nl)
        if len(lines) > self.MAX_ERRORS:
            self._log.setPlainText(nl.join(lines[:self.MAX_ERRORS]))

    def clear(self):
        self._log.clear()
        self._error_count = 0
        self._count_lbl.setText("No errors")
        self._count_lbl.setStyleSheet(f"color:{CLR_TEXT_DIM}; font-size:12px;")


class StatusPage(QWidget):
    def __init__(self, worker: MqttWorker, parent=None):
        super().__init__(parent)
        self._worker = worker
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # -- Page title --------------------------------------------------------
        title = QLabel("System Status")
        title.setStyleSheet(
            f"font-size:22px; font-weight:bold; color:{CLR_TEXT};"
        )
        root.addWidget(title)

        # -- Driver health row -------------------------------------------------
        health_box = QGroupBox("Driver Status")
        hrow = QHBoxLayout(health_box)
        hrow.setSpacing(24)

        self._compressor_dot  = StatusDot()
        self._labjack_dot     = StatusDot()
        self._compressor_lbl  = QLabel("Compressor driver: checking...")
        self._labjack_lbl     = QLabel("LabJack driver: checking...")
        self._compressor_lbl.setStyleSheet(f"color:{CLR_TEXT_DIM};")
        self._labjack_lbl.setStyleSheet(f"color:{CLR_TEXT_DIM};")

        for dot, lbl in [(self._compressor_dot, self._compressor_lbl),
                         (self._labjack_dot,    self._labjack_lbl)]:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(dot)
            row.addWidget(lbl)
            hrow.addLayout(row)
        hrow.addStretch()
        root.addWidget(health_box)

        # -- Process monitor --------------------------------------------------
        self._proc_monitor = ProcessMonitor()
        root.addWidget(self._proc_monitor)

        # -- Error panel -------------------------------------------------------
        self._error_panel = ErrorPanel()
        root.addWidget(self._error_panel)

        # -- Main content: two columns -----------------------------------------
        cols = QHBoxLayout()
        cols.setSpacing(16)
        root.addLayout(cols)

        # Left: vacuum + device controls
        left_col = QVBoxLayout()
        left_col.setSpacing(16)
        cols.addLayout(left_col, 1)

        # Vacuum pressure readout
        vac_box = QGroupBox("Vacuum Chamber")
        vac_lay = QVBoxLayout(vac_box)
        self._pressure_readout = DataReadout("Chamber Pressure", "Torr")
        self._pressure_readout.lbl_value.setStyleSheet(
            f"color:{CLR_ACCENT}; font-family:{CLR_MONO}; font-size:32px; font-weight:bold;"
        )
        vac_lay.addWidget(self._pressure_readout)
        left_col.addWidget(vac_box)

        # Device controls
        ctrl_box = QGroupBox("Device Controls")
        ctrl_lay = QGridLayout(ctrl_box)
        ctrl_lay.setSpacing(12)
        ctrl_lay.setColumnStretch(0, 1)
        ctrl_lay.setColumnStretch(1, 1)

        # -- Roughing Pump + Vacuum Valve (single arm interlock) -------------
        # One "Arm Pump Controls" gate covers both the pump and the valve,
        # since they are the same vacuum subsystem. Arming either set of
        # buttons requires this gate; disarming happens automatically after
        # any pump OR valve action.
        ctrl_lay.addWidget(QLabel("Roughing Pump"), 0, 0)
        self._pump_status = QLabel("* OFF")
        self._pump_status.setStyleSheet(f"color:{CLR_TEXT_DIM}; font-weight:bold;")
        ctrl_lay.addWidget(self._pump_status, 0, 1)

        self._btn_pump_arm = QPushButton("Arm Pump Controls")
        self._btn_pump_arm.setObjectName("btnWarning")
        self._btn_pump_arm.setToolTip(
            "Click to arm, then use the pump and valve buttons below.\n"
            "Controls disarm automatically after each action.")
        self._btn_pump_arm.setCheckable(True)
        self._btn_pump_arm.clicked.connect(self._on_pump_arm_toggled)
        ctrl_lay.addWidget(self._btn_pump_arm, 1, 0, 1, 2)

        pump_btns = QHBoxLayout()
        self._btn_pump_on  = QPushButton("Turn ON")
        self._btn_pump_off = QPushButton("Turn OFF")
        self._btn_pump_on.setObjectName("btnAccent")
        self._btn_pump_off.setObjectName("btnDanger")
        self._btn_pump_on.setEnabled(False)
        self._btn_pump_off.setEnabled(False)
        self._btn_pump_on.clicked.connect(self._pump_on_clicked)
        self._btn_pump_off.clicked.connect(self._pump_off_clicked)
        pump_btns.addWidget(self._btn_pump_on)
        pump_btns.addWidget(self._btn_pump_off)
        ctrl_lay.addLayout(pump_btns, 2, 0, 1, 2)

        ctrl_lay.addWidget(_separator(), 3, 0, 1, 2)

        # Vacuum Valve -- gated by the same arm button as the pump above.
        # The LabJack driver's soft interlock still warns on dangerous
        # combinations (e.g. opening with the pump off) but the arm gate
        # here is what prevents an accidental click in the first place.
        ctrl_lay.addWidget(QLabel("Vacuum Valve"), 4, 0)
        self._valve_status = QLabel("* CLOSED")
        self._valve_status.setStyleSheet(f"color:{CLR_TEXT_DIM}; font-weight:bold;")
        ctrl_lay.addWidget(self._valve_status, 4, 1)
        valve_btns = QHBoxLayout()
        self._btn_valve_open  = QPushButton("OPEN")
        self._btn_valve_close = QPushButton("CLOSE")
        self._btn_valve_open.setObjectName("btnAccent")
        self._btn_valve_close.setObjectName("btnDanger")
        self._btn_valve_open.setEnabled(False)
        self._btn_valve_close.setEnabled(False)
        self._btn_valve_open.clicked.connect(self._valve_open_clicked)
        self._btn_valve_close.clicked.connect(self._valve_close_clicked)
        valve_btns.addWidget(self._btn_valve_open)
        valve_btns.addWidget(self._btn_valve_close)
        ctrl_lay.addLayout(valve_btns, 5, 0, 1, 2)

        ctrl_lay.addWidget(_separator(), 6, 0, 1, 2)

        # -- Helium Compressor (with arm interlock) ---------------------------
        ctrl_lay.addWidget(QLabel("Helium Compressor"), 7, 0)
        self._comp_run_status = QLabel("--")
        self._comp_run_status.setStyleSheet(f"color:{CLR_TEXT_DIM}; font-weight:bold;")
        ctrl_lay.addWidget(self._comp_run_status, 7, 1)

        self._btn_comp_arm = QPushButton("Arm Compressor Controls")
        self._btn_comp_arm.setObjectName("btnWarning")
        self._btn_comp_arm.setToolTip(
            "Click to arm, then use ON/OFF buttons.\n"
            "WARNING: Do not start the compressor unless the\n"
            "helium loop is connected and the system is under vacuum.\n"
            "Controls disarm automatically after each action.")
        self._btn_comp_arm.setCheckable(True)
        self._btn_comp_arm.clicked.connect(self._on_comp_arm_toggled)
        ctrl_lay.addWidget(self._btn_comp_arm, 8, 0, 1, 2)

        comp_btns = QHBoxLayout()
        self._btn_comp_on  = QPushButton("Turn ON")
        self._btn_comp_off = QPushButton("Turn OFF")
        self._btn_comp_on.setObjectName("btnAccent")
        self._btn_comp_off.setObjectName("btnDanger")
        self._btn_comp_on.setEnabled(False)
        self._btn_comp_off.setEnabled(False)
        self._btn_comp_on.clicked.connect(self._comp_on_clicked)
        self._btn_comp_off.clicked.connect(self._comp_off_clicked)
        comp_btns.addWidget(self._btn_comp_on)
        comp_btns.addWidget(self._btn_comp_off)
        ctrl_lay.addLayout(comp_btns, 9, 0, 1, 2)

        left_col.addWidget(ctrl_box)
        left_col.addStretch()

        # Right: compressor telemetry grid
        right_col = QVBoxLayout()
        right_col.setSpacing(16)
        cols.addLayout(right_col, 1)

        comp_box = QGroupBox("Compressor Telemetry")
        comp_grid = QGridLayout(comp_box)
        comp_grid.setSpacing(16)

        self._r = {}
        telemetry_fields = [
            ("helium_pressure",  "He Pressure",    "PSI"),
            ("low_pressure",     "Low Pressure",   "PSI"),
            ("delta_pressure",   "dP",             "PSI"),
            ("helium_temp",      "He Temp",        "deg C"),
            ("oil_temp",         "Oil Temp",       "deg C"),
            ("coolant_in_temp",  "Coolant In",     "deg C"),
            ("coolant_out_temp", "Coolant Out",    "deg C"),
            ("motor_current",    "Motor Current",  "A"),
        ]
        for i, (key, label, unit) in enumerate(telemetry_fields):
            ro = DataReadout(label, unit)
            self._r[key] = ro
            comp_grid.addWidget(ro, i // 2, i % 2)

        right_col.addWidget(comp_box)

        # Compressor state
        state_box = QGroupBox("Compressor State")
        state_lay = QHBoxLayout(state_box)
        self._op_state_lbl = QLabel("--")
        self._op_state_lbl.setStyleSheet(
            f"font-family:{CLR_MONO}; font-size:15px; color:{CLR_GREEN};"
        )
        self._warn_lbl = QLabel("No warnings")
        self._warn_lbl.setStyleSheet(f"color:{CLR_TEXT_DIM}; font-size:12px;")
        self._alarm_lbl = QLabel("No alarms")
        self._alarm_lbl.setStyleSheet(f"color:{CLR_TEXT_DIM}; font-size:12px;")
        state_lay.addWidget(self._op_state_lbl)
        state_lay.addStretch()
        warn_col = QVBoxLayout()
        warn_col.addWidget(self._warn_lbl)
        warn_col.addWidget(self._alarm_lbl)
        state_lay.addLayout(warn_col)
        right_col.addWidget(state_box)
        right_col.addStretch()

    # -- Slots -----------------------------------------------------------------
    # -- Arm/disarm handlers -----------------------------------------------

    def _on_pump_arm_toggled(self, checked: bool):
        """
        Toggle the pump/valve arm state. This single gate covers both the
        roughing pump and the vacuum valve, since they are the same vacuum
        subsystem -- arming once unlocks all four buttons below.
        Shows a confirmation dialog when arming.
        """
        if checked:
            reply = QMessageBox.question(
                self,
                "Arm Pump Controls",
                "Are you sure you want to arm the pump and valve controls?\n\n"
                "Ensure it is safe to start or stop the roughing pump, "
                "or open or close the vacuum valve, before proceeding.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self._btn_pump_arm.setChecked(False)
                return
            self._btn_pump_arm.setText("ARMED -- Click again to disarm")
            self._btn_pump_arm.setStyleSheet(
                f"background:{CLR_RED}; color:white; font-weight:bold; "
                f"border:none; padding:6px 16px; border-radius:4px;")
            self._btn_pump_on.setEnabled(True)
            self._btn_pump_off.setEnabled(True)
            self._btn_valve_open.setEnabled(True)
            self._btn_valve_close.setEnabled(True)
        else:
            self._disarm_pump()

    def _disarm_pump(self):
        """
        Return pump AND valve controls to the disarmed (locked) state.
        Called automatically after any pump or valve action, or when the
        user clicks the arm button again to cancel.
        """
        self._btn_pump_arm.setChecked(False)
        self._btn_pump_arm.setText("Arm Pump Controls")
        self._btn_pump_arm.setStyleSheet("")  # revert to stylesheet default
        self._btn_pump_arm.setObjectName("btnWarning")
        self._btn_pump_arm.style().unpolish(self._btn_pump_arm)
        self._btn_pump_arm.style().polish(self._btn_pump_arm)
        self._btn_pump_on.setEnabled(False)
        self._btn_pump_off.setEnabled(False)
        self._btn_valve_open.setEnabled(False)
        self._btn_valve_close.setEnabled(False)

    def _pump_on_clicked(self):
        self._worker.publish(cfg.TOPIC_PUMP_CONTROL, "ON")
        self._disarm_pump()

    def _pump_off_clicked(self):
        self._worker.publish(cfg.TOPIC_PUMP_CONTROL, "OFF")
        self._disarm_pump()

    def _valve_open_clicked(self):
        self._worker.publish(cfg.TOPIC_VALVE_CONTROL, "OPEN")
        self._disarm_pump()

    def _valve_close_clicked(self):
        self._worker.publish(cfg.TOPIC_VALVE_CONTROL, "CLOSE")
        self._disarm_pump()

    def _on_comp_arm_toggled(self, checked: bool):
        """Toggle compressor arm state. Shows a stronger warning when arming."""
        if checked:
            reply = QMessageBox.warning(
                self,
                "Arm Compressor Controls",
                "WARNING: The helium compressor must only be started when:\n\n"
                "  1. The helium loop is fully connected\n"
                "  2. The system is under vacuum\n"
                "  3. Coolant water flow is confirmed\n\n"
                "Running the compressor dry WILL cause damage.\n\n"
                "Are you sure you want to arm the compressor controls?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self._btn_comp_arm.setChecked(False)
                return
            self._btn_comp_arm.setText("ARMED -- Click again to disarm")
            self._btn_comp_arm.setStyleSheet(
                f"background:{CLR_RED}; color:white; font-weight:bold; "
                f"border:none; padding:6px 16px; border-radius:4px;")
            self._btn_comp_on.setEnabled(True)
            self._btn_comp_off.setEnabled(True)
        else:
            self._disarm_comp()

    @Slot()
    def disarm_all(self):
        """Disarm both interlocks. Called automatically when a recipe run starts."""
        self._disarm_pump()
        self._disarm_comp()

    def _disarm_comp(self):
        """Return compressor controls to the disarmed (locked) state."""
        self._btn_comp_arm.setChecked(False)
        self._btn_comp_arm.setText("Arm Compressor Controls")
        self._btn_comp_arm.setStyleSheet("")
        self._btn_comp_arm.setObjectName("btnWarning")
        self._btn_comp_arm.style().unpolish(self._btn_comp_arm)
        self._btn_comp_arm.style().polish(self._btn_comp_arm)
        self._btn_comp_on.setEnabled(False)
        self._btn_comp_off.setEnabled(False)

    def _comp_on_clicked(self):
        self._worker.publish(cfg.TOPIC_COMPRESSOR_CONTROL, "COMPRESSOR_ON")
        self._disarm_comp()

    def _comp_off_clicked(self):
        self._worker.publish(cfg.TOPIC_COMPRESSOR_CONTROL, "COMPRESSOR_OFF")
        self._disarm_comp()

    @Slot(dict)
    def on_compressor_data(self, data: dict):
        for key, ro in self._r.items():
            ro.set_value(data.get(key))

        state = data.get("operating_state")
        if state is not None:
            self._op_state_lbl.setText(str(state))

        running = data.get("compressor_running")
        if running is not None:
            on = bool(running)
            self._comp_run_status.setText("* ON" if on else "* OFF")
            self._comp_run_status.setStyleSheet(
                f"color:{CLR_GREEN if on else CLR_TEXT_DIM}; font-weight:bold;")

        warnings = data.get("warning_state")
        alarms   = data.get("alarm_state")
        if warnings is not None:
            has_w = (warnings != 0 and warnings is not None)
            self._warn_lbl.setText(f"Warnings: {warnings}" if has_w else "No warnings")
            self._warn_lbl.setStyleSheet(
                f"color:{CLR_AMBER if has_w else CLR_TEXT_DIM}; font-size:12px;")
        if alarms is not None:
            has_a = (alarms != 0 and alarms is not None)
            self._alarm_lbl.setText(f"Alarms: {alarms}" if has_a else "No alarms")
            self._alarm_lbl.setStyleSheet(
                f"color:{CLR_RED if has_a else CLR_TEXT_DIM}; font-size:12px;")

    @Slot(dict)
    def on_labjack_data(self, data: dict):
        p = data.get("vacuum_pressure_torr")
        connected = data.get("gauge_connected", True)
        if not connected or p is None:
            self._pressure_readout.lbl_value.setText("NO SIGNAL")
            self._pressure_readout.lbl_value.setStyleSheet(
                f"color:{CLR_TEXT_DIM}; font-family:{CLR_MONO}; font-size:32px; font-weight:bold;")
        else:
            self._pressure_readout.lbl_value.setStyleSheet(
                f"color:{CLR_ACCENT}; font-family:{CLR_MONO}; font-size:32px; font-weight:bold;")
            self._pressure_readout.set_value(p, ".4g")

    @Slot(bool)
    def on_compressor_alive(self, alive: bool):
        self._compressor_dot.set_state("ok" if alive else "error")
        self._compressor_lbl.setText(
            f"Compressor driver: {'RUNNING' if alive else 'OFFLINE'}")
        self._compressor_lbl.setStyleSheet(
            f"color:{CLR_GREEN if alive else CLR_RED};")

    @Slot(bool)
    def on_labjack_alive(self, alive: bool):
        self._labjack_dot.set_state("ok" if alive else "error")
        self._labjack_lbl.setText(
            f"LabJack driver: {'RUNNING' if alive else 'OFFLINE'}")
        self._labjack_lbl.setStyleSheet(
            f"color:{CLR_GREEN if alive else CLR_RED};")

    @Slot(str)
    def on_valve_state(self, state: str):
        """Called whenever the LabJack driver publishes a valve state change."""
        is_open = (state == "OPEN")
        colour  = CLR_GREEN if is_open else CLR_TEXT_DIM
        self._valve_status.setText(f"* {state}")
        self._valve_status.setStyleSheet(
            f"color:{colour}; font-weight:bold;")

    @Slot(str)
    def on_pump_state(self, state: str):
        """Called whenever the LabJack driver publishes a pump state change."""
        is_on  = (state == "ON")
        colour = CLR_GREEN if is_on else CLR_TEXT_DIM
        self._pump_status.setText(f"* {state}")
        self._pump_status.setStyleSheet(
            f"color:{colour}; font-weight:bold;")

    @Slot(dict)
    def on_compressor_state(self, data: dict):
        """Called after a compressor command with readback confirmation."""
        commanded = data.get("commanded", "?")
        confirmed = data.get("confirmed", False)
        if not confirmed:
            # Readback disagreed -- surface this prominently
            self._error_panel.add_error(
                "compressor_driver",
                f"Command {commanded} sent but state readback did not confirm.",
                time.strftime("%H:%M:%S"))


# -----------------------------------------------------------------------------
# Page 2 -- Recipe Editor
# -----------------------------------------------------------------------------
class RecipePage(QWidget):
    """
    Build a pump-down recipe and save it as a JSON file.

    Recipe JSON structure:
    {
      "name": "Standard pump-down",
      "foreline_pump_duration_s": 30,
      "target_pressure_torr": 1e-3,
      "leak_check_duration_s": 60,
      "max_leak_rate_torr_per_s": 5e-5,
      "compressor_delay_s": 5
    }
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # -- Left: file list ---------------------------------------------------
        left = QGroupBox("Saved Recipes")
        left.setFixedWidth(220)
        left_lay = QVBoxLayout(left)
        self._recipe_list = QListWidget()
        self._recipe_list.itemClicked.connect(self._load_selected)
        left_lay.addWidget(self._recipe_list)
        btn_new = QPushButton("+ New Recipe")
        btn_new.setObjectName("btnAccent")
        btn_new.clicked.connect(self._new_recipe)
        btn_del = QPushButton("Delete")
        btn_del.setObjectName("btnDanger")
        btn_del.clicked.connect(self._delete_selected)
        left_lay.addWidget(btn_new)
        left_lay.addWidget(btn_del)
        root.addWidget(left)

        # -- Right: editor -----------------------------------------------------
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setSpacing(16)
        root.addWidget(right, 1)

        title = QLabel("Recipe Editor")
        title.setStyleSheet(f"font-size:22px; font-weight:bold; color:{CLR_TEXT};")
        right_lay.addWidget(title)

        form_box = QGroupBox("Recipe Parameters")
        form = QFormLayout(form_box)
        form.setSpacing(12)
        form.setContentsMargins(16, 16, 16, 16)

        self._fld_name = QLineEdit("New Recipe")

        self._fld_foreline_s = QSpinBox()
        self._fld_foreline_s.setRange(1, 3600)
        self._fld_foreline_s.setValue(30)
        self._fld_foreline_s.setSuffix(" s")

        self._fld_target_p = QDoubleSpinBox()
        self._fld_target_p.setRange(1e-9, 760)
        self._fld_target_p.setDecimals(6)
        self._fld_target_p.setValue(1e-3)
        self._fld_target_p.setSuffix(" Torr")

        self._fld_leak_dur = QSpinBox()
        self._fld_leak_dur.setRange(10, 600)
        self._fld_leak_dur.setValue(60)
        self._fld_leak_dur.setSuffix(" s")

        self._fld_max_leak = QDoubleSpinBox()
        self._fld_max_leak.setRange(1e-9, 1.0)
        self._fld_max_leak.setDecimals(8)
        self._fld_max_leak.setValue(5e-5)
        self._fld_max_leak.setSuffix(" Torr/s")

        self._fld_comp_delay = QSpinBox()
        self._fld_comp_delay.setRange(0, 300)
        self._fld_comp_delay.setValue(5)
        self._fld_comp_delay.setSuffix(" s")

        form.addRow("Recipe Name:", self._fld_name)
        form.addRow(_form_sep("-- PUMP-DOWN -----------------------------"), QWidget())
        form.addRow("Foreline pump duration:", self._fld_foreline_s)
        form.addRow("Target chamber pressure:", self._fld_target_p)
        form.addRow(_form_sep("-- LEAK CHECK ----------------------------"), QWidget())
        form.addRow("Leak check duration:", self._fld_leak_dur)
        form.addRow("Max allowed leak rate:", self._fld_max_leak)
        form.addRow(_form_sep("-- COOLING -------------------------------"), QWidget())
        form.addRow("Delay before compressor on:", self._fld_comp_delay)

        right_lay.addWidget(form_box)

        # Description panel
        desc_box = QGroupBox("Process Flow (read-only summary)")
        desc_lay = QVBoxLayout(desc_box)
        self._desc = QTextEdit()
        self._desc.setReadOnly(True)
        self._desc.setMaximumHeight(120)
        desc_lay.addWidget(self._desc)
        right_lay.addWidget(desc_box)

        # Save button
        btn_save = QPushButton("Save Recipe")
        btn_save.setObjectName("btnAccent")
        btn_save.setFixedWidth(160)
        btn_save.clicked.connect(self._save_recipe)
        right_lay.addWidget(btn_save, alignment=Qt.AlignLeft)
        right_lay.addStretch()

        # Connect fields to auto-update description
        for fld in [self._fld_foreline_s, self._fld_target_p,
                    self._fld_leak_dur, self._fld_max_leak, self._fld_comp_delay]:
            fld.valueChanged.connect(self._update_description)
        self._fld_name.textChanged.connect(self._update_description)

        self._update_description()
        self._refresh_list()

    def _update_description(self):
        f   = self._fld_foreline_s.value()
        tp  = self._fld_target_p.value()
        ld  = self._fld_leak_dur.value()
        ml  = self._fld_max_leak.value()
        cd  = self._fld_comp_delay.value()
        txt = (
            f"1. Pump foreline for {f} s with valve CLOSED.\n"
            f"2. Open valve -- pump chamber to {tp:.2e} Torr.\n"
            f"3. Close valve -- measure leak rate for {ld} s.\n"
            f"   ➜ If avg leak rate > {ml:.1e} Torr/s: ABORT with warning.\n"
            f"4. Wait {cd} s, then start the helium compressor."
        )
        self._desc.setPlainText(txt)

    def _get_recipe(self) -> dict:
        return {
            "name":                       self._fld_name.text().strip() or "Unnamed",
            "foreline_pump_duration_s":   self._fld_foreline_s.value(),
            "target_pressure_torr":       self._fld_target_p.value(),
            "leak_check_duration_s":      self._fld_leak_dur.value(),
            "max_leak_rate_torr_per_s":   self._fld_max_leak.value(),
            "compressor_delay_s":         self._fld_comp_delay.value(),
        }

    def _save_recipe(self):
        recipe = self._get_recipe()
        errors = []
        tp = recipe["target_pressure_torr"]
        if tp <= 0:
            errors.append("Target pressure must be greater than zero.")
        elif tp >= 760:
            errors.append("Target pressure must be below 760 Torr.")
        elif tp < 1e-8:
            errors.append("Target below 1e-8 Torr is not achievable with this system.")
        if recipe["max_leak_rate_torr_per_s"] <= 0:
            errors.append("Max leak rate must be greater than zero.")
        if recipe["foreline_pump_duration_s"] < 5:
            errors.append("Foreline pump duration should be at least 5 seconds.")
        if errors:
            QMessageBox.warning(self, "Validation Error",
                                chr(10).join(errors))
            return
        safe_name = recipe["name"].replace(" ", "_").replace("/", "-")
        path = RECIPE_DIR / f"{safe_name}.json"
        with open(path, "w") as f:
            json.dump(recipe, f, indent=2)
        QMessageBox.information(self, "Saved",
                                f"Recipe saved to:{chr(10)}{path}")
        self._refresh_list()

    def _open_logs_folder(self):
        """Open the run_logs folder in Windows Explorer."""
        import subprocess as _sp
        RUN_LOG_DIR.mkdir(exist_ok=True)
        _sp.Popen(["explorer", str(RUN_LOG_DIR.resolve())])

    def _refresh_list(self):
        self._recipe_list.clear()
        for p in sorted(RECIPE_DIR.glob("*.json")):
            item = QListWidgetItem(p.stem.replace("_", " "))
            item.setData(Qt.UserRole, str(p))
            self._recipe_list.addItem(item)

    def _load_selected(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        try:
            with open(path) as f:
                recipe = json.load(f)
            self._fld_name.setText(recipe.get("name", ""))
            self._fld_foreline_s.setValue(recipe.get("foreline_pump_duration_s", 30))
            self._fld_target_p.setValue(recipe.get("target_pressure_torr", 1e-3))
            self._fld_leak_dur.setValue(recipe.get("leak_check_duration_s", 60))
            self._fld_max_leak.setValue(recipe.get("max_leak_rate_torr_per_s", 5e-5))
            self._fld_comp_delay.setValue(recipe.get("compressor_delay_s", 5))
        except Exception as e:
            QMessageBox.warning(self, "Load Error", str(e))

    def _new_recipe(self):
        self._fld_name.setText("New Recipe")
        self._fld_foreline_s.setValue(30)
        self._fld_target_p.setValue(1e-3)
        self._fld_leak_dur.setValue(60)
        self._fld_max_leak.setValue(5e-5)
        self._fld_comp_delay.setValue(5)

    def _delete_selected(self):
        item = self._recipe_list.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        reply = QMessageBox.question(self, "Delete", f"Delete '{item.text()}'?")
        if reply == QMessageBox.Yes:
            os.remove(path)
            self._refresh_list()


# -----------------------------------------------------------------------------
# Page 3 -- Recipe Runner
# -----------------------------------------------------------------------------
class RunPage(QWidget):
    sig_disarm_all = Signal()  # Emitted before a recipe run starts
    """
    Load and execute a recipe. Runs the sequence in a background QThread,
    emitting progress signals so the GUI stays responsive.
    """
    def __init__(self, worker: MqttWorker, parent=None):
        super().__init__(parent)
        self._worker         = worker
        self._current_recipe: Optional[dict] = None
        self._run_thread: Optional[RecipeRunThread] = None
        self._latest_pressure: float = 760.0
        self._build_ui()

        # Keep pressure fresh via the worker signal
        worker.sig_labjack_data.connect(self._on_labjack_data)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        title = QLabel("Run Recipe")
        title.setStyleSheet(f"font-size:22px; font-weight:bold; color:{CLR_TEXT};")
        root.addWidget(title)

        top = QHBoxLayout()
        root.addLayout(top)

        # Recipe selector
        sel_box = QGroupBox("Recipe")
        sel_lay = QVBoxLayout(sel_box)
        self._recipe_list = QListWidget()
        self._recipe_list.setMaximumHeight(180)
        self._recipe_list.itemClicked.connect(self._select_recipe)
        sel_lay.addWidget(self._recipe_list)
        btn_refresh = QPushButton("~  Refresh List")
        btn_refresh.clicked.connect(self._refresh_list)
        sel_lay.addWidget(btn_refresh)
        top.addWidget(sel_box, 1)

        # Recipe summary
        sum_box = QGroupBox("Selected Recipe")
        sum_lay = QVBoxLayout(sum_box)
        self._recipe_summary = QTextEdit()
        self._recipe_summary.setReadOnly(True)
        self._recipe_summary.setPlainText("No recipe selected.")
        sum_lay.addWidget(self._recipe_summary)
        top.addWidget(sum_box, 1)

        # -- Step progress -----------------------------------------------------
        prog_box = QGroupBox("Run Progress")
        prog_lay = QVBoxLayout(prog_box)

        self._step_labels = []
        step_names = [
            "1.  Pump foreline",
            "2.  Open valve / pump to target",
            "3.  Leak rate check",
            "4.  Close valve",
            "5.  Start compressor",
        ]
        for name in step_names:
            row = QHBoxLayout()
            dot = StatusDot()
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color:{CLR_TEXT_DIM};")
            row.addWidget(dot)
            row.addWidget(lbl)
            row.addStretch()
            prog_lay.addLayout(row)
            self._step_labels.append((dot, lbl))

        root.addWidget(prog_box)

        # -- Live readouts during run -------------------------------------------
        live_row = QHBoxLayout()
        root.addLayout(live_row)

        self._live_pressure = DataReadout("Live Pressure", "Torr")
        self._live_pressure.lbl_value.setStyleSheet(
            f"color:{CLR_ACCENT}; font-family:{CLR_MONO}; font-size:24px;")
        self._live_leak_rate = DataReadout("Leak Rate", "Torr/s")
        self._live_leak_rate.lbl_value.setStyleSheet(
            f"color:{CLR_AMBER}; font-family:{CLR_MONO}; font-size:24px;")
        self._status_log = QTextEdit()
        self._status_log.setReadOnly(True)
        self._status_log.setMaximumHeight(100)

        live_box = QGroupBox("Live Values")
        live_inner = QHBoxLayout(live_box)
        live_inner.addWidget(self._live_pressure)
        live_inner.addWidget(self._live_leak_rate)
        live_row.addWidget(live_box, 1)

        log_box = QGroupBox("Event Log")
        log_inner = QVBoxLayout(log_box)
        log_inner.addWidget(self._status_log)
        live_row.addWidget(log_box, 1)

        # -- Run / Abort buttons ------------------------------------------------
        btn_row = QHBoxLayout()
        self._btn_run   = QPushButton("[play]  Run Recipe")
        self._btn_run.setObjectName("btnAccent")
        self._btn_run.setFixedHeight(40)
        self._btn_run.clicked.connect(self._start_run)

        self._btn_abort = QPushButton("[stop]  Abort")
        self._btn_abort.setObjectName("btnDanger")
        self._btn_abort.setFixedHeight(40)
        self._btn_abort.setEnabled(False)
        self._btn_abort.clicked.connect(self._abort_run)

        btn_open_logs = QPushButton("📂  Open Run Logs")
        btn_open_logs.setFixedHeight(40)
        btn_open_logs.clicked.connect(self._open_logs_folder)

        btn_row.addWidget(self._btn_run)
        btn_row.addWidget(self._btn_abort)
        btn_row.addStretch()
        btn_row.addWidget(btn_open_logs)
        root.addLayout(btn_row)

        self._refresh_list()

    def _open_logs_folder(self):
        """Open the run_logs folder in Windows Explorer."""
        import subprocess as _sp
        RUN_LOG_DIR.mkdir(exist_ok=True)
        _sp.Popen(["explorer", str(RUN_LOG_DIR.resolve())])

    def _refresh_list(self):
        self._recipe_list.clear()
        for p in sorted(RECIPE_DIR.glob("*.json")):
            item = QListWidgetItem(p.stem.replace("_", " "))
            item.setData(Qt.UserRole, str(p))
            self._recipe_list.addItem(item)

    def _select_recipe(self, item):
        path = item.data(Qt.UserRole)
        try:
            with open(path) as f:
                self._current_recipe = json.load(f)
            r = self._current_recipe
            self._recipe_summary.setPlainText(
                f"Name: {r['name']}\n"
                f"Foreline pump: {r['foreline_pump_duration_s']} s\n"
                f"Target pressure: {r['target_pressure_torr']:.2e} Torr\n"
                f"Leak check: {r['leak_check_duration_s']} s\n"
                f"Max leak rate: {r['max_leak_rate_torr_per_s']:.1e} Torr/s\n"
                f"Compressor delay: {r['compressor_delay_s']} s"
            )
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    @Slot(dict)
    def _on_labjack_data(self, data: dict):
        p = data.get("vacuum_pressure_torr")
        if p is not None:
            self._latest_pressure = p
            self._live_pressure.set_value(p, ".4g")

    def _log(self, msg: str):
        self._status_log.append(
            f"[{time.strftime('%H:%M:%S')}] {msg}"
        )

    def _start_run(self):
        if not self._current_recipe:
            QMessageBox.warning(self, "No Recipe", "Please select a recipe first.")
            return
        # Reset step indicators
        for dot, lbl in self._step_labels:
            dot.set_state("unknown")
            lbl.setStyleSheet(f"color:{CLR_TEXT_DIM};")
        self._status_log.clear()
        self._live_leak_rate.set_value(None)

        self._run_thread = RecipeRunThread(
            self._current_recipe, self._worker
        )
        self._run_thread.sig_step_update.connect(self._on_step_update)
        self._run_thread.sig_log.connect(self._log)
        self._run_thread.sig_leak_rate.connect(
            lambda v: self._live_leak_rate.set_value(v, ".4g"))
        self._run_thread.sig_finished.connect(self._on_run_finished)

        self._btn_run.setEnabled(False)
        self._btn_abort.setEnabled(True)
        # Emit signal to disarm status page interlocks before the recipe
        # starts controlling the pump and compressor
        self.sig_disarm_all.emit()
        self._run_thread.start()

    def _abort_run(self):
        if self._run_thread and self._run_thread.isRunning():
            self._run_thread.abort()
            self._log("[!] ABORTED by user.")

    def _on_step_update(self, step_idx: int, state: str):
        if 0 <= step_idx < len(self._step_labels):
            dot, lbl = self._step_labels[step_idx]
            dot.set_state(state)
            colours = {"ok": CLR_GREEN, "warn": CLR_AMBER,
                       "error": CLR_RED, "unknown": CLR_TEXT_DIM}
            lbl.setStyleSheet(f"color:{colours.get(state, CLR_TEXT_DIM)};")

    def _on_run_finished(self, success: bool):
        self._btn_run.setEnabled(True)
        self._btn_abort.setEnabled(False)
        if success:
            self._log("✓ Recipe completed successfully.")
        else:
            self._log("✗ Recipe ended with errors or was aborted.")


# -----------------------------------------------------------------------------
# Recipe execution thread
# -----------------------------------------------------------------------------
class RunLogger:
    """
    Writes a timestamped log file for each recipe run to RUN_LOG_DIR.

    File name format:  run_YYYYMMDD_HHMMSS_<recipe_name>.txt
    Writes a header with recipe parameters, then appends each event line
    as it happens. The file stays open for the duration of the run and is
    flushed after every write so no data is lost if the process crashes.
    """

    def __init__(self, recipe: dict):
        timestamp   = time.strftime("%Y%m%d_%H%M%S")
        safe_name   = recipe.get("name", "unnamed").replace(" ", "_").replace("/", "-")
        filename    = f"run_{timestamp}_{safe_name}.txt"
        self._path  = RUN_LOG_DIR / filename
        self._file  = open(self._path, "w", encoding="utf-8")
        self._write_header(recipe)

    def _write_header(self, recipe: dict):
        lines = [
            "=" * 60,
            f"HTS Magnet Testing Automation -- Run Log",
            f"Started:  {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Recipe:   {recipe.get('name', 'unnamed')}",
            "-" * 60,
            f"  Foreline pump duration : {recipe.get('foreline_pump_duration_s')} s",
            f"  Target pressure        : {recipe.get('target_pressure_torr'):.2e} Torr",
            f"  Leak check duration    : {recipe.get('leak_check_duration_s')} s",
            f"  Max leak rate          : {recipe.get('max_leak_rate_torr_per_s'):.1e} Torr/s",
            f"  Compressor delay       : {recipe.get('compressor_delay_s')} s",
            "=" * 60,
            "",
        ]
        self._file.write("\n".join(lines) + "\n")
        self._file.flush()

    def log(self, message: str):
        """Write a timestamped event line."""
        line = f"[{time.strftime('%H:%M:%S')}] {message}\n"
        self._file.write(line)
        self._file.flush()

    def close(self, success: bool):
        """Write a footer and close the file."""
        result = "SUCCESS" if success else "FAILED / ABORTED"
        footer = [
            "",
            "-" * 60,
            f"Result:  {result}",
            f"Ended:   {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
        ]
        self._file.write("\n".join(footer) + "\n")
        self._file.close()
        print(f"[LOGGER] Run log saved to: {self._path}")

    @property
    def path(self) -> Path:
        return self._path


class RecipeRunThread(QThread):
    """
    Executes a pump-down recipe in a background thread.

    Pressure readings come directly from a dedicated MQTT client owned by
    this thread -- not bounced through the GUI thread. This means readings
    are as fresh as the LabJack driver publishes them (~0.5 s latency).

    Leak rate is calculated using a linear regression over all samples
    collected during the leak check window, not just two end-points. This
    makes the result far more robust against individual noisy readings.
    """
    sig_step_update = Signal(int, str)   # (step_index, state)
    sig_log         = Signal(str)
    sig_leak_rate   = Signal(float)
    sig_finished    = Signal(bool)

    # How long to wait for the first pressure reading before giving up
    _PRESSURE_TIMEOUT_S = 15
    # Minimum samples needed for a valid leak rate calculation
    _MIN_LEAK_SAMPLES   = 5

    def __init__(self, recipe: dict, worker: MqttWorker):
        super().__init__()
        self._recipe   = recipe
        self._worker   = worker
        self._aborted  = False

        # Thread-local pressure state -- updated directly by our own MQTT client
        self._pressure: Optional[float] = None
        self._pressure_lock  = threading.Lock()
        self._pressure_event = threading.Event()

        # Private MQTT client for this thread -- subscribes to labjack metrics
        self._mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._mqtt.on_connect = self._on_mqtt_connect
        self._mqtt.on_message = self._on_mqtt_message

    # -- MQTT callbacks (called on paho's network thread) ---------------------
    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe(cfg.TOPIC_LABJACK_METRICS)

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
            p    = data.get("vacuum_pressure_torr")
            if p is not None:
                with self._pressure_lock:
                    self._pressure = float(p)
                self._pressure_event.set()
        except Exception:
            pass

    # -- Helpers ---------------------------------------------------------------
    def abort(self):
        self._aborted = True

    def _get_pressure(self) -> Optional[float]:
        """
        Block until a fresh pressure reading arrives (up to _PRESSURE_TIMEOUT_S).
        Clears the event first so we always wait for a new message rather than
        reusing a stale one.
        Returns None on timeout.
        """
        self._pressure_event.clear()
        arrived = self._pressure_event.wait(timeout=self._PRESSURE_TIMEOUT_S)
        if not arrived:
            return None
        with self._pressure_lock:
            return self._pressure

    def _sleep(self, seconds: float) -> bool:
        """
        Sleep in 0.1 s increments so abort is responsive.
        Returns False if aborted before the time elapses.
        """
        end = time.time() + seconds
        while time.time() < end:
            if self._aborted:
                return False
            time.sleep(0.1)
        return True

    def _collect_leak_samples(self, duration_s: float) -> list[tuple[float, float]]:
        """
        Collect (time, pressure) samples over duration_s seconds.
        Waits for each new MQTT message so sample rate matches the driver.
        Returns list of (elapsed_seconds, pressure_torr) tuples.
        """
        samples   = []
        t_start   = time.time()
        deadline  = t_start + duration_s

        while time.time() < deadline:
            if self._aborted:
                break
            self._pressure_event.clear()
            # Wait for next reading, but don't exceed the window
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            arrived = self._pressure_event.wait(timeout=min(remaining, 2.0))
            if arrived:
                with self._pressure_lock:
                    p = self._pressure
                if p is not None:
                    samples.append((time.time() - t_start, p))

        return samples

    @staticmethod
    def _linear_leak_rate(samples: list[tuple[float, float]]) -> float:
        """
        Fit a line to (time, pressure) samples and return the slope in Torr/s.
        Using least-squares over all samples is much more robust than
        a simple (p_end - p_start) / elapsed calculation.
        """
        if len(samples) < 2:
            return 0.0
        times     = [s[0] for s in samples]
        pressures = [s[1] for s in samples]
        n    = len(times)
        t_mean = sum(times) / n
        p_mean = sum(pressures) / n
        num  = sum((t - t_mean) * (p - p_mean) for t, p in zip(times, pressures))
        den  = sum((t - t_mean) ** 2 for t in times)
        return num / den if den > 0 else 0.0

    # -- Main execution --------------------------------------------------------
    def run(self):
        STEP_FORELINE   = 0
        STEP_PUMP       = 1
        STEP_LEAK       = 2
        STEP_CLOSE      = 3
        STEP_COMPRESSOR = 4

        # Start our private MQTT connection
        try:
            self._mqtt.connect(cfg.MQTT_BROKER_HOST,
                               cfg.MQTT_BROKER_PORT,
                               cfg.MQTT_KEEPALIVE)
            self._mqtt.loop_start()
        except Exception as e:
            self.sig_log.emit(f"FATAL: Could not connect to MQTT broker -- {e}")
            self.sig_finished.emit(False)
            return

        r          = self._recipe
        run_logger = RunLogger(r)
        self.sig_log.emit(f"Run log: {run_logger.path}")

        def log(msg: str):
            """Emit to GUI and write to disk simultaneously."""
            self.sig_log.emit(msg)
            run_logger.log(msg)

        try:
            # -- Step 1: Pump foreline ------------------------------------------
            self.sig_step_update.emit(STEP_FORELINE, "warn")
            log(f"Step 1: Pump foreline for {r['foreline_pump_duration_s']} s...")
            self._worker.publish(cfg.TOPIC_PUMP_CONTROL, "ON")
            if not self._sleep(r["foreline_pump_duration_s"]):
                run_logger.close(False); self._safe_shutdown(); return
            self.sig_step_update.emit(STEP_FORELINE, "ok")
            log("Step 1 complete.")

            # -- Step 2: Open valve, pump to target pressure --------------------
            self.sig_step_update.emit(STEP_PUMP, "warn")
            target          = r["target_pressure_torr"]
            timeout_s       = cfg.PUMP_TIMEOUT_S
            abort_threshold = cfg.PUMP_ABORT_THRESHOLD_TORR
            log(f"Step 2: Opening valve -- pumping to {target:.4g} Torr "
                f"(timeout {timeout_s}s, abort if >{abort_threshold} Torr at timeout)...")
            self._worker.publish(cfg.TOPIC_VALVE_CONTROL, "OPEN")

            t_start = time.time()
            target_reached = False
            while True:
                if self._aborted:
                    self._safe_shutdown(); return
                p = self._get_pressure()
                if p is None:
                    log("  [!] No pressure reading -- is the LabJack driver running?")
                    run_logger.close(False); self._safe_shutdown()
                    self.sig_finished.emit(False)
                    return
                elapsed = time.time() - t_start
                log(f"  {elapsed:.0f}s | {p:.4g} Torr (target <= {target:.4g})")
                if p <= target:
                    target_reached = True
                    break
                if elapsed >= timeout_s:
                    if p > abort_threshold:
                        log(f"  [!] TIMEOUT: {p:.4g} Torr exceeds abort threshold "
                            f"{abort_threshold} Torr -- closing valve and pump to protect equipment.")
                        self.sig_step_update.emit(STEP_PUMP, "error")
                        run_logger.close(False); self._safe_shutdown()
                        self.sig_finished.emit(False)
                        return
                    else:
                        log(f"  [!] TIMEOUT: {p:.4g} Torr did not reach target "
                            f"{target:.4g} Torr, but is below abort threshold "
                            f"{abort_threshold} Torr. Proceeding to leak check.")
                        break

            if target_reached:
                self.sig_step_update.emit(STEP_PUMP, "ok")
                log("Step 2 complete -- target pressure reached.")
            else:
                self.sig_step_update.emit(STEP_PUMP, "warn")
                log("Step 2 timed out -- proceeding with caution.")

            # -- Step 3: Leak rate check ----------------------------------------
            self.sig_step_update.emit(STEP_LEAK, "warn")
            duration = r["leak_check_duration_s"]
            max_rate = r["max_leak_rate_torr_per_s"]
            log(f"Step 3: Closing valve -- collecting leak samples for {duration} s...")
            self._worker.publish(cfg.TOPIC_VALVE_CONTROL, "CLOSE")

            samples = self._collect_leak_samples(duration)
            if self._aborted:
                self._safe_shutdown(); return

            if len(samples) < self._MIN_LEAK_SAMPLES:
                log(f"  [!] Only {len(samples)} samples collected (need {self._MIN_LEAK_SAMPLES})."
                    " Check LabJack driver.")
                run_logger.close(False); self._safe_shutdown()
                self.sig_finished.emit(False)
                return

            leak_rate = self._linear_leak_rate(samples)
            self.sig_leak_rate.emit(leak_rate)
            log(f"  Samples: {len(samples)}  |  "
                f"Leak rate: {leak_rate:.4g} Torr/s  (limit: {max_rate:.4g} Torr/s)")

            if leak_rate > max_rate:
                self.sig_step_update.emit(STEP_LEAK, "error")
                log(f"[!] LEAK CHECK FAILED -- {leak_rate:.4g} Torr/s exceeds "
                    f"limit {max_rate:.4g} Torr/s.")
                run_logger.close(False); self._safe_shutdown()
                self.sig_finished.emit(False)
                return

            self.sig_step_update.emit(STEP_LEAK, "ok")
            log(f"Step 3 complete -- leak rate within spec ({leak_rate:.4g} Torr/s).")

            # -- Step 4: Valve already closed in step 3 ------------------------
            self.sig_step_update.emit(STEP_CLOSE, "ok")
            log("Step 4: Valve confirmed closed.")

            # -- Step 5: Start compressor ---------------------------------------
            delay = r["compressor_delay_s"]
            self.sig_step_update.emit(STEP_COMPRESSOR, "warn")

            # Pre-check: skip COMPRESSOR_ON if it is already running
            if self._worker.is_compressor_running():
                log("Step 5: Compressor is already running -- skipping start command.")
                log("  (It was already on before this recipe started.)")
                self.sig_step_update.emit(STEP_COMPRESSOR, "ok")
            else:
                log(f"Step 5: Waiting {delay} s then starting compressor...")
                if not self._sleep(delay):
                    run_logger.close(False); self._safe_shutdown(); return
                self._worker.publish(cfg.TOPIC_COMPRESSOR_CONTROL, "COMPRESSOR_ON")
                self.sig_step_update.emit(STEP_COMPRESSOR, "ok")
                log("Compressor started.")
            run_logger.close(True)
            self._worker.publish(
                cfg.TOPIC_RUN_EVENT,
                json.dumps({
                    "event":     "END",
                    "recipe":    r.get("name", "unnamed"),
                    "outcome":   "SUCCESS",
                    "timestamp": time.time(),
                }))
            self.sig_finished.emit(True)

        except Exception as e:
            log(f"ERROR: {e}")
            run_logger.close(False)
            self._worker.publish(
                cfg.TOPIC_RUN_EVENT,
                json.dumps({
                    "event":     "END",
                    "recipe":    r.get("name", "unnamed"),
                    "outcome":   "FAILED",
                    "timestamp": time.time(),
                }))
            self._safe_shutdown()
            self.sig_finished.emit(False)

        finally:
            self._worker.publish(cfg.TOPIC_LOGGER_CONTROL, "HIGH_SPEED_STOP")
            self._mqtt.loop_stop()
            self._mqtt.disconnect()

    def _safe_shutdown(self):
        """Close valve and turn off pump on any failure or abort."""
        self._worker.publish(cfg.TOPIC_VALVE_CONTROL, "CLOSE")
        self._worker.publish(cfg.TOPIC_PUMP_CONTROL,  "OFF")
        self.sig_log.emit("Safe shutdown: valve closed, pump off.")
        # Note: run_logger.log() is called by the caller before _safe_shutdown
        # so we don't need access to it here.


# -----------------------------------------------------------------------------
# Page 4 -- Live Plots
# -----------------------------------------------------------------------------
class PlotsPage(QWidget):
    """
    Live plotting page with unlimited buffer, start/stop control, and CSV export.

    Data is accumulated in Python lists (no fixed size) while recording is active.
    Stopping recording freezes the plots so you can inspect them.
    Export saves all recorded data to a timestamped CSV in the lab folder.
    """

    def __init__(self, worker: MqttWorker, parent=None):
        super().__init__(parent)
        self._worker    = worker
        self._recording = False
        self._t0        = time.time()

        # Unbounded data lists -- appended to while recording
        self._data: dict[str, list] = {
            "t_vac": [], "p_vac": [],
            "t_he":  [], "p_he":  [],
            "t_oil": [], "v_oil": [],
        }
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)

        title = QLabel("Live Plots")
        title.setStyleSheet(f"font-size:22px; font-weight:bold; color:{CLR_TEXT};")
        root.addWidget(title)

        # Controls row
        ctrl = QHBoxLayout()
        self._btn_start = QPushButton("[play]  Start Recording")
        self._btn_start.setObjectName("btnAccent")
        self._btn_start.clicked.connect(self._start_recording)

        self._btn_stop = QPushButton("[stop]  Stop Recording")
        self._btn_stop.setObjectName("btnDanger")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_recording)

        self._btn_export = QPushButton("v  Export CSV")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._export_csv)

        self._btn_clear = QPushButton("Clear")
        self._btn_clear.clicked.connect(self._clear_data)

        self._status_lbl = QLabel("Not recording")
        self._status_lbl.setStyleSheet(f"color:{CLR_TEXT_DIM}; font-size:12px;")

        ctrl.addWidget(self._btn_start)
        ctrl.addWidget(self._btn_stop)
        ctrl.addWidget(self._btn_export)
        ctrl.addWidget(self._btn_clear)
        ctrl.addStretch()
        ctrl.addWidget(self._status_lbl)
        root.addLayout(ctrl)

        pg.setConfigOption("background", CLR_BG)
        pg.setConfigOption("foreground", CLR_TEXT_DIM)

        # Vacuum pressure (log scale)
        self._plot_vac = pg.PlotWidget(title="Chamber Pressure")
        self._plot_vac.setLabel("left",   "Pressure", units="Torr")
        self._plot_vac.setLabel("bottom", "Elapsed",  units="s")
        self._plot_vac.setLogMode(y=True)
        self._plot_vac.showGrid(x=True, y=True, alpha=0.2)
        self._curve_vac = self._plot_vac.plot(pen=pg.mkPen(CLR_ACCENT, width=2))
        root.addWidget(self._plot_vac, 1)

        # Helium pressure
        self._plot_he = pg.PlotWidget(title="Helium Pressure")
        self._plot_he.setLabel("left",   "Pressure", units="PSI")
        self._plot_he.setLabel("bottom", "Elapsed",  units="s")
        self._plot_he.showGrid(x=True, y=True, alpha=0.2)
        self._curve_he = self._plot_he.plot(pen=pg.mkPen(CLR_GREEN, width=2))
        root.addWidget(self._plot_he, 1)

        # Oil temperature
        self._plot_oil = pg.PlotWidget(title="Oil Temperature")
        self._plot_oil.setLabel("left",   "Temp", units="deg C")
        self._plot_oil.setLabel("bottom", "Elapsed", units="s")
        self._plot_oil.showGrid(x=True, y=True, alpha=0.2)
        self._curve_oil = self._plot_oil.plot(pen=pg.mkPen(CLR_AMBER, width=2))
        root.addWidget(self._plot_oil, 1)

    # -- Controls --------------------------------------------------------------
    def _start_recording(self):
        self._t0        = time.time()
        self._recording = True
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_export.setEnabled(False)
        self._status_lbl.setText("Recording...")
        self._status_lbl.setStyleSheet(f"color:{CLR_GREEN}; font-size:12px;")

    def _stop_recording(self):
        self._recording = False
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        n = len(self._data["t_vac"])
        has_data = n > 0
        self._btn_export.setEnabled(has_data)
        self._status_lbl.setText(
            f"Stopped -- {n} pressure samples recorded." if has_data
            else "Stopped -- no data recorded.")
        self._status_lbl.setStyleSheet(f"color:{CLR_AMBER}; font-size:12px;")

    def _clear_data(self):
        self._recording = False
        for key in self._data:
            self._data[key].clear()
        self._curve_vac.setData([], [])
        self._curve_he.setData([], [])
        self._curve_oil.setData([], [])
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_export.setEnabled(False)
        self._status_lbl.setText("Cleared.")
        self._status_lbl.setStyleSheet(f"color:{CLR_TEXT_DIM}; font-size:12px;")

    def _export_csv(self):
        """Export all recorded data to a timestamped CSV file."""
        import csv
        timestamp  = time.strftime("%Y%m%d_%H%M%S")
        default    = str(Path("run_logs") / f"plot_export_{timestamp}.csv")
        path, _    = QFileDialog.getSaveFileName(
            self, "Export Plot Data", default, "CSV Files (*.csv)")
        if not path:
            return
        try:
            # Merge all series by elapsed time into one CSV
            rows = []
            for i, t in enumerate(self._data["t_vac"]):
                rows.append({
                    "elapsed_s":           round(t, 3),
                    "vacuum_pressure_torr": self._data["p_vac"][i]
                    if i < len(self._data["p_vac"]) else "",
                    "helium_pressure_psi":  "",
                    "oil_temp_c":           "",
                })
            for i, t in enumerate(self._data["t_he"]):
                if i < len(rows):
                    rows[i]["helium_pressure_psi"] = self._data["p_he"][i]
                    rows[i]["oil_temp_c"]          = (self._data["v_oil"][i]
                                                      if i < len(self._data["v_oil"])
                                                      else "")
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["elapsed_s", "vacuum_pressure_torr",
                                   "helium_pressure_psi", "oil_temp_c"])
                writer.writeheader()
                writer.writerows(rows)
            QMessageBox.information(self, "Exported",
                                    f"Data exported to:{chr(10)}{path}")
        except Exception as e:
            QMessageBox.warning(self, "Export Error", str(e))

    # -- Data slots -------------------------------------------------------------
    @Slot(dict)
    def on_labjack_data(self, data: dict):
        if not self._recording:
            return
        p = data.get("vacuum_pressure_torr")
        if p is None:
            return
        p = max(p, 1e-10)
        t = time.time() - self._t0
        self._data["t_vac"].append(t)
        self._data["p_vac"].append(p)
        self._curve_vac.setData(self._data["t_vac"], self._data["p_vac"])
        n = len(self._data["t_vac"])
        self._status_lbl.setText(f"Recording -- {n} samples")

    # -- Arm/disarm handlers -----------------------------------------------

    def _on_pump_arm_toggled(self, checked: bool):
        """
        Toggle the pump/valve arm state. This single gate covers both the
        roughing pump and the vacuum valve, since they are the same vacuum
        subsystem -- arming once unlocks all four buttons below.
        Shows a confirmation dialog when arming.
        """
        if checked:
            reply = QMessageBox.question(
                self,
                "Arm Pump Controls",
                "Are you sure you want to arm the pump and valve controls?\n\n"
                "Ensure it is safe to start or stop the roughing pump, "
                "or open or close the vacuum valve, before proceeding.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self._btn_pump_arm.setChecked(False)
                return
            self._btn_pump_arm.setText("ARMED -- Click again to disarm")
            self._btn_pump_arm.setStyleSheet(
                f"background:{CLR_RED}; color:white; font-weight:bold; "
                f"border:none; padding:6px 16px; border-radius:4px;")
            self._btn_pump_on.setEnabled(True)
            self._btn_pump_off.setEnabled(True)
            self._btn_valve_open.setEnabled(True)
            self._btn_valve_close.setEnabled(True)
        else:
            self._disarm_pump()

    def _disarm_pump(self):
        """
        Return pump AND valve controls to the disarmed (locked) state.
        Called automatically after any pump or valve action, or when the
        user clicks the arm button again to cancel.
        """
        self._btn_pump_arm.setChecked(False)
        self._btn_pump_arm.setText("Arm Pump Controls")
        self._btn_pump_arm.setStyleSheet("")  # revert to stylesheet default
        self._btn_pump_arm.setObjectName("btnWarning")
        self._btn_pump_arm.style().unpolish(self._btn_pump_arm)
        self._btn_pump_arm.style().polish(self._btn_pump_arm)
        self._btn_pump_on.setEnabled(False)
        self._btn_pump_off.setEnabled(False)
        self._btn_valve_open.setEnabled(False)
        self._btn_valve_close.setEnabled(False)

    def _pump_on_clicked(self):
        self._worker.publish(cfg.TOPIC_PUMP_CONTROL, "ON")
        self._disarm_pump()

    def _pump_off_clicked(self):
        self._worker.publish(cfg.TOPIC_PUMP_CONTROL, "OFF")
        self._disarm_pump()

    def _valve_open_clicked(self):
        self._worker.publish(cfg.TOPIC_VALVE_CONTROL, "OPEN")
        self._disarm_pump()

    def _valve_close_clicked(self):
        self._worker.publish(cfg.TOPIC_VALVE_CONTROL, "CLOSE")
        self._disarm_pump()

    def _on_comp_arm_toggled(self, checked: bool):
        """Toggle compressor arm state. Shows a stronger warning when arming."""
        if checked:
            reply = QMessageBox.warning(
                self,
                "Arm Compressor Controls",
                "WARNING: The helium compressor must only be started when:\n\n"
                "  1. The helium loop is fully connected\n"
                "  2. The system is under vacuum\n"
                "  3. Coolant water flow is confirmed\n\n"
                "Running the compressor dry WILL cause damage.\n\n"
                "Are you sure you want to arm the compressor controls?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self._btn_comp_arm.setChecked(False)
                return
            self._btn_comp_arm.setText("ARMED -- Click again to disarm")
            self._btn_comp_arm.setStyleSheet(
                f"background:{CLR_RED}; color:white; font-weight:bold; "
                f"border:none; padding:6px 16px; border-radius:4px;")
            self._btn_comp_on.setEnabled(True)
            self._btn_comp_off.setEnabled(True)
        else:
            self._disarm_comp()

    @Slot()
    def disarm_all(self):
        """Disarm both interlocks. Called automatically when a recipe run starts."""
        self._disarm_pump()
        self._disarm_comp()

    def _disarm_comp(self):
        """Return compressor controls to the disarmed (locked) state."""
        self._btn_comp_arm.setChecked(False)
        self._btn_comp_arm.setText("Arm Compressor Controls")
        self._btn_comp_arm.setStyleSheet("")
        self._btn_comp_arm.setObjectName("btnWarning")
        self._btn_comp_arm.style().unpolish(self._btn_comp_arm)
        self._btn_comp_arm.style().polish(self._btn_comp_arm)
        self._btn_comp_on.setEnabled(False)
        self._btn_comp_off.setEnabled(False)

    def _comp_on_clicked(self):
        self._worker.publish(cfg.TOPIC_COMPRESSOR_CONTROL, "COMPRESSOR_ON")
        self._disarm_comp()

    def _comp_off_clicked(self):
        self._worker.publish(cfg.TOPIC_COMPRESSOR_CONTROL, "COMPRESSOR_OFF")
        self._disarm_comp()

    @Slot(dict)
    def on_compressor_data(self, data: dict):
        if not self._recording:
            return
        hp  = data.get("helium_pressure")
        oil = data.get("oil_temp")
        if hp is None and oil is None:
            return
        t = time.time() - self._t0
        if hp is not None:
            self._data["t_he"].append(t)
            self._data["p_he"].append(float(hp))
            self._curve_he.setData(self._data["t_he"], self._data["p_he"])
        if oil is not None:
            self._data["t_oil"].append(t)
            self._data["v_oil"].append(float(oil))
            self._curve_oil.setData(self._data["t_oil"], self._data["v_oil"])


# -----------------------------------------------------------------------------
# Main Window
# -----------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HTS Magnet Testing Automation")
        self.resize(1200, 800)
        self.setStyleSheet(STYLESHEET)

        # -- MQTT worker in its own thread -------------------------------------
        self._mqtt_thread = QThread()
        self._worker      = MqttWorker()
        self._worker.moveToThread(self._mqtt_thread)
        self._mqtt_thread.started.connect(self._worker.start)
        self._mqtt_thread.start()

        # -- Central layout: sidebar + pages -----------------------------------
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        sidebar = QWidget()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet(f"background:{CLR_PANEL}; border-right:1px solid {CLR_BORDER};")
        sidebar_lay = QVBoxLayout(sidebar)
        sidebar_lay.setContentsMargins(0, 16, 0, 16)
        sidebar_lay.setSpacing(4)

        app_title = QLabel("LAB CONTROL")
        app_title.setStyleSheet(
            f"color:{CLR_ACCENT}; font-size:12px; letter-spacing:2px; "
            f"font-weight:bold; padding:0 16px 16px 16px;"
        )
        sidebar_lay.addWidget(app_title)

        self._nav_btns = []
        nav_items = [
            ("⬛  Status",      0),
            ("⚙  Recipe Editor", 1),
            ("[play]  Run Recipe",   2),
            ("📈  Live Plots",   3),
        ]
        for label, idx in nav_items:
            btn = NavButton(label)
            btn.clicked.connect(lambda checked, i=idx: self._show_page(i))
            sidebar_lay.addWidget(btn)
            self._nav_btns.append(btn)

        sidebar_lay.addStretch()
        layout.addWidget(sidebar)

        # Pages
        self._pages = QStackedWidget()
        layout.addWidget(self._pages)

        self._status_page  = StatusPage(self._worker)
        self._recipe_page  = RecipePage()
        self._run_page     = RunPage(self._worker)
        self._plots_page   = PlotsPage(self._worker)

        self._pages.addWidget(self._status_page)
        self._pages.addWidget(self._recipe_page)
        self._pages.addWidget(self._run_page)
        self._pages.addWidget(self._plots_page)

        # -- Wire up signals ---------------------------------------------------
        self._worker.sig_compressor_data.connect(self._status_page.on_compressor_data)
        self._worker.sig_labjack_data.connect(self._status_page.on_labjack_data)
        self._worker.sig_compressor_alive.connect(self._status_page.on_compressor_alive)
        self._worker.sig_labjack_alive.connect(self._status_page.on_labjack_alive)
        self._worker.sig_error.connect(self._status_page._error_panel.add_error)
        self._worker.sig_valve_state.connect(self._status_page.on_valve_state)
        self._worker.sig_pump_state.connect(self._status_page.on_pump_state)
        self._worker.sig_compressor_state.connect(
            self._status_page.on_compressor_state)
        self._run_page.sig_disarm_all.connect(
            self._status_page.disarm_all)
        self._worker.sig_labjack_data.connect(self._plots_page.on_labjack_data)
        self._worker.sig_compressor_data.connect(self._plots_page.on_compressor_data)

        self._show_page(0)

    def _show_page(self, idx: int):
        self._pages.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == idx)

    def closeEvent(self, event):
        self._worker.stop()
        self._mqtt_thread.quit()
        self._mqtt_thread.wait(3000)
        event.accept()


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------
def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color:{CLR_BORDER};")
    return line


def _form_sep(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color:{CLR_TEXT_DIM}; font-size:10px; letter-spacing:1px;")
    return lbl


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
