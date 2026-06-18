"""
verify_environment.py
─────────────────────────────────────────────────────────────────────────────
Checks that every required package is installed and importable, and that
external services (Mosquitto, QuestDB) are reachable.

Run after setup_environment.bat, or any time something seems broken:
    python verify_environment.py

Does NOT require any lab hardware to be connected.
─────────────────────────────────────────────────────────────────────────────
"""

import sys
import os
import importlib
import importlib.util
import subprocess
import urllib.request
import socket
from pathlib import Path as _Path

# This script locates itself automatically using its own file path,
# so it works no matter where the project folder is placed.
LAB_DIR = str(_Path(__file__).resolve().parent)

# ── Colour helpers ────────────────────────────────────────────────────────────
# Works on Windows 10+ with ANSI support. Falls back to plain text if not.
try:
    import ctypes
    ctypes.windll.kernel32.SetConsoleMode(
        ctypes.windll.kernel32.GetStdHandle(-11), 7)
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
except Exception:
    GREEN = YELLOW = RED = RESET = BOLD = ""

def ok(msg):   print(f"  {GREEN}PASS{RESET}  {msg}")
def warn(msg): print(f"  {YELLOW}WARN{RESET}  {msg}")
def fail(msg): print(f"  {RED}FAIL{RESET}  {msg}")
def header(msg): print(f"\n{BOLD}{msg}{RESET}")

passes = 0
warnings = 0
failures = 0

def check(label, fn):
    global passes, warnings, failures
    try:
        result = fn()
        if result is True or result is None:
            ok(label)
            passes += 1
        elif result == "warn":
            warnings += 1
        else:
            ok(f"{label}  ({result})")
            passes += 1
    except AssertionError as e:
        fail(f"{label}  →  {e}")
        failures += 1
    except Exception as e:
        fail(f"{label}  →  {e}")
        failures += 1

# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Python version
# ─────────────────────────────────────────────────────────────────────────────
header("── Python ───────────────────────────────────────────────")

def check_python():
    v = sys.version_info
    assert v.major == 3 and v.minor >= 11, \
        f"Expected Python 3.11 or newer, got {v.major}.{v.minor}"
    return f"Python {v.major}.{v.minor}.{v.micro}"

check("Python 3.11+", check_python)

# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Required packages
# ─────────────────────────────────────────────────────────────────────────────
header("── Required packages ────────────────────────────────────")

PACKAGES = [
    # (import_name, pip_name, test_expression)
    ("PySide6",         "PySide6",      "from PySide6.QtCore import Qt"),
    ("pyqtgraph",       "pyqtgraph",    "import pyqtgraph"),
    ("numpy",           "numpy",        "import numpy as np; np.array([1,2,3])"),
    ("paho.mqtt",       "paho-mqtt",    "import paho.mqtt.client as mqtt"),
    ("pymodbus",        "pymodbus",     "from pymodbus.client import ModbusTcpClient"),
    ("questdb.ingress", "questdb",      "from questdb.ingress import Sender"),
]

OPTIONAL_PACKAGES = [
    ("labjack.ljm",    "labjack-ljm",  "from labjack import ljm"),
]

for import_name, pip_name, test_expr in PACKAGES:
    def make_check(expr, pname):
        def fn():
            try:
                exec(expr)
            except ImportError:
                raise AssertionError(
                    f"not installed — run: pip install {pname}")
        return fn
    check(f"{import_name}", make_check(test_expr, pip_name))

header("── Optional packages ─────────────────────────────────────")

for import_name, pip_name, test_expr in OPTIONAL_PACKAGES:
    try:
        exec(test_expr)
        ok(f"{import_name}  (LabJack hardware support available)")
        passes += 1
    except ImportError:
        warn(f"{import_name}  — not installed")
        print(f"         Install the LJM system driver first, then: pip install {pip_name}")
        warnings += 1
    except Exception as e:
        warn(f"{import_name}  — installed but LJM system driver may be missing: {e}")
        warnings += 1

# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Package versions
# ─────────────────────────────────────────────────────────────────────────────
header("── Package versions ─────────────────────────────────────")

VERSION_CHECKS = [
    ("PySide6",   "6."),
    ("pyqtgraph", "0."),
    ("numpy",     "2."),
    ("paho-mqtt", "2."),
    ("pymodbus",  "3."),
    ("questdb",   "4."),
]

import importlib.metadata as meta

for pkg, min_prefix in VERSION_CHECKS:
    def make_version_check(p, prefix):
        def fn():
            try:
                v = meta.version(p)
                if not v.startswith(prefix):
                    raise AssertionError(
                        f"version {v} may be outdated — expected {prefix}x")
                return f"{p}=={v}"
            except meta.PackageNotFoundError:
                raise AssertionError(f"{p} not installed")
        return fn
    check(pkg, make_version_check(pkg, min_prefix))

# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — External services
# ─────────────────────────────────────────────────────────────────────────────
header("── External services ─────────────────────────────────────")

def check_mosquitto():
    """Try connecting a TCP socket to the MQTT broker port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(("localhost", 1883))
        s.close()
        return "Mosquitto reachable on localhost:1883"
    except Exception:
        raise AssertionError(
            "MQTT broker not reachable on localhost:1883\n"
            "         Start Mosquitto: net start mosquitto")

def check_questdb():
    """Check the remote QuestDB instance at 198.125.227.226:9000.
    Reported as a warning since the machine may not always be on."""
    try:
        req = urllib.request.urlopen(
            "http://198.125.227.226:9000", timeout=5)
        return f"QuestDB reachable at 198.125.227.226:9000  (HTTP {req.status})"
    except Exception:
        # Report as warning not failure — startup.bat starts QuestDB automatically
        warn("QuestDB  ->  not reachable at 198.125.227.226:9000")
        print("         Check that the QuestDB machine is powered on")
        print("         and connected to the lab network.")
        global warnings
        warnings += 1
        return "warn"

# QuestDB runs on a separate lab machine -- the binary check is not
# needed here. check_questdb() above already verifies network reachability.

check("Mosquitto MQTT broker", check_mosquitto)
check_questdb()

# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — Local lab scripts importable
# ─────────────────────────────────────────────────────────────────────────────
header("── Lab scripts ──────────────────────────────────────────")


LAB_SCRIPTS = [
    ("mqtt_config",     "mqtt_config.py"),
    ("questdb_client",  "questdb_client.py"),
]

for module_name, filename in LAB_SCRIPTS:
    filepath = os.path.join(LAB_DIR, filename)
    def make_script_check(mname, fpath, fname):
        def fn():
            if not os.path.exists(fpath):
                raise AssertionError(
                    f"{fname} not found in:\n"
                    f"         {LAB_DIR}\n"
                    f"         Make sure all lab scripts are in that folder.")
            spec   = importlib.util.spec_from_file_location(mname, fpath)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return f"{fname} loaded OK"
        return fn
    check(module_name, make_script_check(module_name, filepath, filename))

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
total = passes + warnings + failures
print(f"\n{'─'*60}")
print(f"  {GREEN}{passes} passed{RESET}  "
      f"{YELLOW}{warnings} warnings{RESET}  "
      f"{RED}{failures} failed{RESET}  "
      f"(of {total} checks)")

if failures > 0:
    print(f"\n  {RED}Some checks failed — fix the issues above before running the lab scripts.{RESET}")
elif warnings > 0:
    print(f"\n  {YELLOW}All required checks passed. Warnings are for optional components.{RESET}")
else:
    print(f"\n  {GREEN}All checks passed — environment is ready.{RESET}")

print(f"{'─'*60}\n")

sys.exit(0 if failures == 0 else 1)
