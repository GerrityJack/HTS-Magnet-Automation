"""
A wrapper class for Modbus communication to a specific device.

This class supports both TCP and serial connections and provides methods to
read and write specific registers as defined by the protocol documentation.
It handles data types, scaling, and value decoding for a more user-friendly
experience.
"""

import sys
from collections import namedtuple
from enum import Enum, IntFlag
from pymodbus.client import ModbusTcpClient, ModbusSerialClient
from pymodbus.exceptions import ModbusIOException

# --- Enum Definitions for Constants ---


class OperatingState(Enum):
    """Maps integer state codes to human-readable descriptions."""

    IDLING = 0
    STARTING = 2
    RUNNING = 3
    STOPPING = 5
    ERROR_LOCKOUT = 6
    ERROR = 7
    HELIUM_COOL_DOWN = 8
    POWER_RELATED_ERROR = 9
    RECOVERED_FROM_ERROR = 16


class CompressorRunning(Enum):
    """Maps integer state codes for the compressor running status."""

    OFF = 0
    ON = 1


class PressureScale(Enum):
    """Maps integer codes to pressure units."""

    PSI = 0
    BAR = 1
    KPA = 2


class TempScale(Enum):
    """Maps integer codes to temperature units."""

    FAHRENHEIT = 0
    CELSIUS = 1
    KELVIN = 2


class CompressorControl(Enum):
    """Maps hex values to compressor control states."""

    OFF = 0x00FF
    ON = 0x0001


class WarningBits(IntFlag):
    """Maps bit flags to specific warning conditions."""

    COOLANT_IN_HIGH = 1
    COOLANT_IN_LOW = 2
    COOLANT_OUT_HIGH = 4
    COOLANT_OUT_LOW = 8
    OIL_HIGH = 16
    OIL_LOW = 32
    HELIUM_HIGH = 64
    HELIUM_LOW = 128
    LOW_PRESSURE_HIGH = 256
    LOW_PRESSURE_LOW = 512
    HIGH_PRESSURE_HIGH = 1024
    HIGH_PRESSURE_LOW = 2048
    DELTA_PRESSURE_HIGH = 4096
    DELTA_PRESSURE_LOW = 8192
    STATIC_PRESSURE_HIGH = 131072
    STATIC_PRESSURE_LOW = 262144
    COLD_HEAD_MOTOR_STALL = 524288


class ErrorBits(IntFlag):
    """Maps bit flags to specific error conditions."""

    COOLANT_IN_HIGH = 1
    COOLANT_IN_LOW = 2
    COOLANT_OUT_HIGH = 4
    COOLANT_OUT_LOW = 8
    OIL_HIGH = 16
    OIL_LOW = 32
    HELIUM_HIGH = 64
    HELIUM_LOW = 128
    LOW_PRESSURE_HIGH = 256
    LOW_PRESSURE_LOW = 512
    HIGH_PRESSURE_HIGH = 1024
    HIGH_PRESSURE_LOW = 2048
    DELTA_PRESSURE_HIGH = 4096
    DELTA_PRESSURE_LOW = 8192
    MOTOR_CURRENT_LOW = 16384
    THREE_PHASE_ERROR = 32768
    POWER_SUPPLY_ERROR = 65536
    STATIC_PRESSURE_HIGH = 131072
    STATIC_PRESSURE_LOW = 262144


ModelNumbers = namedtuple("ModelNumbers", ["major", "minor"])

# Define register addresses and their types.
# Addresses are now the logical register number (e.g., 30001 -> 1).
# The library will handle the 0-based indexing for the Modbus client.
REGISTER_MAP = {
    "operating_state": {"address": 1, "count": 1, "type": "int"},
    "compressor_running": {"address": 2, "count": 1, "type": "int"},
    "pressure_scale": {"address": 29, "count": 1, "type": "int"},
    "temp_scale": {"address": 30, "count": 1, "type": "int"},
    "panel_serial_number": {"address": 31, "count": 1, "type": "int"},
    "model_numbers": {"address": 32, "count": 1, "type": "int"},
    "software_rev": {"address": 33, "count": 1, "type": "int"},
    "detected_rpm": {"address": 34, "count": 1, "type": "int", "scale": 100},
    "software_variant": {"address": 35, "count": 1, "type": "int"},
    "inverter_frequency": {"address": 36, "count": 1, "type": "int", "scale": 10},
    "inverter_current": {"address": 37, "count": 1, "type": "int", "scale": 10},
    "build_order_number": {"address": 38, "count": 2, "type": "int32"},
    "coolant_in_temp": {"address": 40, "count": 1, "type": "int", "scale": 10},
    "coolant_out_temp": {"address": 41, "count": 1, "type": "int", "scale": 10},
    "oil_temp": {"address": 42, "count": 1, "type": "int", "scale": 10},
    "helium_temp": {"address": 43, "count": 1, "type": "int", "scale": 10},
    "low_pressure": {"address": 44, "count": 1, "type": "int", "scale": 10},
    "low_pressure_avg": {"address": 45, "count": 1, "type": "int", "scale": 10},
    "high_pressure": {"address": 46, "count": 1, "type": "int", "scale": 10},
    "high_pressure_avg": {"address": 47, "count": 1, "type": "int", "scale": 10},
    "delta_pressure_avg": {"address": 48, "count": 1, "type": "int", "scale": 10},
    "motor_current": {"address": 49, "count": 1, "type": "int", "scale": 10},
    "hours_of_operation": {"address": 50, "count": 2, "type": "int32", "scale": 10},
    "warning_state": {"address": 52, "count": 2, "type": "int32"},
    "alarm_state": {"address": 54, "count": 2, "type": "int32"},
}

# Define holding register
HOLDING_REGISTER = {
    "compressor_enable": {"address": 1, "count": 1, "type": "int"},
}


# ── Error publisher ───────────────────────────────────────────────────────────
# Set this from outside (e.g. compressor_mqtt_driver.py) after MQTT connects
# so ModbusDeviceClient can publish errors through MQTT as well as printing them.
_error_publisher = None   # callable(source: str, message: str) or None

def set_error_publisher(fn):
    """Call this with _publish_error from the driver script after MQTT connects."""
    global _error_publisher
    _error_publisher = fn

def _report_error(source: str, message: str):
    """Print error and optionally publish to MQTT."""
    print(f"[MODBUS ERROR] {source}: {message}")
    if _error_publisher is not None:
        try:
            _error_publisher(source, message)
        except Exception:
            pass


class ModbusDeviceClient:
    """
    Wrapper class for Modbus TCP or Serial communication.
    """

    def __init__(
        self,
        protocol,
        modbus_id=16,
        host=None,
        port=502,
        serial_port=None,
        baudrate=9600,
    ):
        """
        Initializes the Modbus client.

        Args:
            protocol (str): 'tcp' or 'serial'.
            modbus_id (int): The Modbus slave ID of the device. Defaults to 16.
            host (str, optional): IP address of the device for TCP. Required for TCP.
            port (int, optional): Port number for TCP. Defaults to 502.
            serial_port (str, optional): COM port for serial. Required for serial.
            baudrate (int, optional): Baud rate for serial. Defaults to 9600.
        """
        self.protocol = protocol
        self.modbus_id = modbus_id
        self.client = None
        self.host = host
        self.port = port
        self.serial_port = serial_port
        self.baudrate = baudrate

    def connect(self):
        """
        Establish a connection to the Modbus device.
        Returns True on success, False on failure.
        Errors are printed and also published via the MQTT error publisher
        if one has been registered with set_error_publisher().
        """
        if self.protocol == "tcp":
            if not self.host:
                _report_error("modbus_client",
                              "Host IP is required for TCP protocol.")
                return False
            self.client = ModbusTcpClient(self.host, port=self.port)

        elif self.protocol == "serial":
            if not self.serial_port:
                _report_error("modbus_client",
                              "Serial port not specified.")
                return False
            try:
                self.client = ModbusSerialClient(
                    port=self.serial_port, baudrate=self.baudrate
                )
            except RuntimeError as e:
                # pyserial not installed
                _report_error("modbus_client",
                              f"Cannot create serial client: {e}. "
                              f"Run: pip install pyserial")
                return False
            except Exception as e:
                _report_error("modbus_client",
                              f"Serial client init error: {e}")
                return False

        else:
            _report_error("modbus_client",
                          f"Invalid protocol '{self.protocol}'. Use 'tcp' or 'serial'.")
            return False

        try:
            if not self.client.connect():
                msg = (f"Could not connect to Modbus device on "
                       f"{self.serial_port if self.protocol == 'serial' else self.host}. "
                       f"For serial: check the port is correct in Device Manager "
                       f"(Ports / COM & LPT). The port may have been reassigned "
                       f"after a USB reconnect or reboot.")
                _report_error("modbus_client", msg)
                return False
            print(f"[MODBUS] Connected via {self.protocol} to "
                  f"{self.serial_port if self.protocol == 'serial' else self.host}.")
            return True
        except Exception as e:
            msg = (f"Connection exception on {self.serial_port if self.protocol == 'serial' else self.host}: {e}")
            _report_error("modbus_client", msg)
            return False

    def disconnect(self):
        """Closes the connection to the Modbus device."""
        if self.client:
            self.client.close()
            print("Disconnected from Modbus device.")

    def _read_input_registers(self, address, count):
        """
        A private helper to read input registers.
        """
        if not self.client or not self.client.connected:
            print("Not connected to a Modbus device.")
            return None

        try:
            response = self.client.read_input_registers(
                address=address, count=count, device_id=self.modbus_id
            )
            if response.isError():
                print(f"Modbus Error reading address {address}: {response}")
                return None
            return response.registers
        except ModbusIOException as e:
            print(f"Modbus IO Error: {e}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return None

    def _read_holding_registers(self, address, count):
        """
        A private helper to read holding registers.
        """
        if not self.client or not self.client.connected:
            print("Not connected to a Modbus device.")
            return None

        try:
            response = self.client.read_holding_registers(
                address, count, device_id=self.modbus_id
            )
            if response.isError():
                print(f"Modbus Error reading address {address}: {response}")
                return None
            return response.registers
        except ModbusIOException as e:
            print(f"Modbus IO Error: {e}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return None

    def read_all_input_registers(self):
        """
        Read all input registers. Returns a dict of values, or None on failure.
        Errors are reported via _report_error for both console and MQTT visibility.
        """
        if self.client is None:
            _report_error("modbus_client",
                          "read_all_input_registers called but client is not connected.")
            return None
        data = {}
        for name, reg_info in REGISTER_MAP.items():
            reg_address = reg_info["address"]
            reg_count = reg_info["count"]

            registers = self._read_input_registers(reg_address, reg_count)
            if registers is None:
                continue

            value = None
            if reg_info["type"] == "int":
                if len(registers) >= 1:
                    value = registers[0]
                else:
                    print(f"Error: Not enough registers returned for {name}.")
                    continue
            elif reg_info["type"] == "int32":
                if len(registers) >= 2:
                    # Manually decode 32-bit integer from two 16-bit registers (Big Endian)
                    value = (registers[0] << 16) | registers[1]
                else:
                    print(f"Error: Not enough registers returned for {name}.")
                    continue

            if "scale" in reg_info and value is not None:
                value /= reg_info["scale"]

            data[name] = value

        return data

    def get_decoded_data(self, raw_data):
        """
        Decodes raw register data into human-readable values using Enums.

        Args:
            raw_data (dict): Dictionary of raw register values from read_all_input_registers.

        Returns:
            dict: Dictionary with decoded values.
        """
        decoded_data = raw_data.copy()

        # Decode specific registers based on Enums
        if "operating_state" in decoded_data:
            state_value = decoded_data["operating_state"]
            try:
                decoded_data["operating_state"] = (
                    OperatingState(state_value).name.replace("_", " ").title()
                )
            except ValueError:
                decoded_data["operating_state"] = "Unknown"

        if "compressor_running" in decoded_data:
            state_value = decoded_data["compressor_running"]
            try:
                decoded_data["compressor_running"] = (
                    CompressorRunning(state_value).name.replace("_", " ").title()
                )
            except ValueError:
                decoded_data["compressor_running"] = "Unknown"

        if "pressure_scale" in decoded_data:
            scale_value = decoded_data["pressure_scale"]
            try:
                decoded_data["pressure_scale"] = PressureScale(scale_value).name
            except ValueError:
                decoded_data["pressure_scale"] = "Unknown"

        if "temp_scale" in decoded_data:
            scale_value = decoded_data["temp_scale"]
            try:
                decoded_data["temp_scale"] = TempScale(scale_value).name
            except ValueError:
                decoded_data["temp_scale"] = "Unknown"

        if "model_numbers" in decoded_data:
            model_value = decoded_data["model_numbers"]
            major = (model_value >> 8) & 0xFF
            minor = model_value & 0xFF
            decoded_data["model_numbers"] = ModelNumbers(major=major, minor=minor)

        if "warning_state" in decoded_data:
            warnings = []
            warning_value = decoded_data["warning_state"]
            if warning_value == 0:
                warnings.append("No warnings")
            else:
                for bit in WarningBits:
                    if bit.value & warning_value:
                        warnings.append(bit.name.replace("_", " ").title())
            decoded_data["warning_state"] = warnings

        if "alarm_state" in decoded_data:
            alarms = []
            alarm_value = decoded_data["alarm_state"]
            if alarm_value == 0:
                alarms.append("No errors")
            else:
                for bit in ErrorBits:
                    if bit.value & alarm_value:
                        alarms.append(bit.name.replace("_", " ").title())
            decoded_data["alarm_state"] = alarms

        return decoded_data

    def set_compressor_state(self, state):
        """
        Sets the compressor state (ON/OFF) using the CompressorControl enum.

        Args:
            state (str): 'on' or 'off'.

        Returns:
            bool: True on success, False otherwise.
        """
        if not self.client or not self.client.connected:
            print("Not connected to a Modbus device.")
            return False

        register_address = HOLDING_REGISTER["compressor_enable"]["address"]

        if state.lower() == "on":
            value = CompressorControl.ON.value
        elif state.lower() == "off":
            value = CompressorControl.OFF.value
        else:
            print("Invalid state. Use 'on' or 'off'.")
            return False

        try:
            response = self.client.write_register(
                register_address, value, device_id=self.modbus_id
            )
            if response.isError():
                print(
                    f"Modbus Error writing to register {register_address}: {response}"
                )
                return False
            print(f"Successfully set compressor state to {state.upper()}.")
            return True
        except ModbusIOException as e:
            print(f"Modbus IO Error: {e}")
            return False
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return False


if __name__ == "__main__":
    # Example usage:
    # To connect via TCP:
    # client = ModbusDeviceClient(protocol='tcp', host='127.0.0.1')

    # To connect via Serial (replace with your COM port):
    # client = ModbusDeviceClient(protocol='serial', serial_port='/dev/ttyUSB0')

    # For this example, we'll simulate a TCP connection.
    # Note: This will only work if a Modbus TCP slave is running at the specified host and port.

    # Use 'tcp' for network communication or 'serial' for serial port communication.
    protocol_type = "serial"

    if protocol_type == "tcp":
        client = ModbusDeviceClient(protocol=protocol_type, host="localhost", port=5020)
    elif protocol_type == "serial":
        # You need to specify your serial port here.
        # e.g., 'COM3' on Windows or '/dev/ttyUSB0' on Linux
        client = ModbusDeviceClient(
            protocol=protocol_type, serial_port="COM5", baudrate=115200
        )
    else:
        print("Invalid protocol type specified.")
        sys.exit(1)

    if client.connect():
        print("\n--- Reading All Input Registers ---")
        raw_data = client.read_all_input_registers()
        if raw_data:
            print("Raw Data:", raw_data)
            decoded_data = client.get_decoded_data(raw_data)
            print("\nDecoded Data:")
            for key, value in decoded_data.items():
                print(f"  {key.replace('_', ' ').title()}: {value}")

        # Example of writing to a holding register
        # print("\n--- Attempting to set compressor state to ON ---")
        # client.set_compressor_state('on')

        # In a real-world scenario, you would then read the holding register
        # to confirm the write was successful.

        # Example of setting compressor state to OFF
        # print("\n--- Attempting to set compressor state to OFF ---")
        # client.set_compressor_state('off')

        client.disconnect()
    else:
        print("Could not connect. Please check your connection details.")
