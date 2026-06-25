import can
import threading
import time
import struct
from enum import Enum, auto
from can_defs import NodeID, ArbID, MsgType, NODE_REGISTRY, compute_checksum

class FaultMode(Enum):
    NORMAL  = auto()
    SILENT  = auto()
    CORRUPT = auto()
    FLOOD   = auto()

class BaseNode(threading.Thread):
    def __init__(self, node_id: NodeID, bus: can.BusABC):
        super().__init__(daemon=True)
        self.meta = NODE_REGISTRY[node_id]
        self.hb_interval = self.meta["heartbeat_interval_s"]    
        self.node_id = node_id
        self.bus = bus
        self._last_heartbeat = 0.0
        self._last_telemetry = 0.0
        self.fault_mode = FaultMode.NORMAL
        self._stop_event = threading.Event()
        self._seq = 0

    def stop(self):
        # set the stop event
        self._stop_event.set()

    def inject_fault(self, mode: FaultMode):
        # set self.fault_mode
        self.fault_mode = mode

    def _next_seq(self) -> int:
        # return current, increment, wrap at 255
        current = self._seq
        self._seq = (self._seq + 1) % 256
        return current
    
    def _build_frame(self, arb_id: int, msg_type: int, payload: bytes) -> bytes:
        if len(payload) != 4:
            raise ValueError("Payload must be exactly 4 bytes")
        seq = self._next_seq()
        frame = bytes([self.node_id.value, msg_type]) + payload + bytes([seq])
        checksum = compute_checksum(frame)
        return frame + bytes([checksum])
    
    def _send(self, arb_id: int, data: bytes):
        if self.fault_mode == FaultMode.SILENT:
            return
        elif self.fault_mode == FaultMode.CORRUPT:
            data = data[:-1] + bytes([data[-1] ^ 0xFF])
        elif self.fault_mode == FaultMode.FLOOD:
            for _ in range(100):
                self.bus.send(can.Message(arbitration_id=arb_id, data=data, is_extended_id=False))
            return
        self.bus.send(can.Message(arbitration_id=arb_id, data=data, is_extended_id=False))

    def run(self):
        while not self._stop_event.is_set():
            now = time.time()
            
            if now - self._last_heartbeat >= self.hb_interval:
                self._send_heartbeat()
                self._last_heartbeat = now

            if now - self._last_telemetry >= self.hb_interval * 2:
                self._send_telemetry()
                self._last_telemetry = now
            
            time.sleep(0.01)


    def _send_heartbeat(self):
        # payload = current timestamp as 4-byte big-endian integer
        # hint: struct.pack(">I", int(time.time()) & 0xFFFFFFFF)
        # build the frame and send it
        timestamp = int(time.time()) & 0xFFFFFFFF
        payload = struct.pack(">I", timestamp)
        frame = self._build_frame(self.meta["heartbeat_arb_id"], MsgType.HEARTBEAT.value, payload)
        self._send(self.meta["heartbeat_arb_id"], frame)

    def _send_telemetry(self):
        raise NotImplementedError("Telemetry sending not implemented for base node")
    
class PowerNode(BaseNode):
    def __init__(self, bus):
        super().__init__(NodeID.POWER, bus)
        self._voltage_V = 28.0
        self._current_A = 3.2
        self._soc_pct   = 92.0

    def _send_telemetry(self):
        # slowly discharge soc
        self._soc_pct = max(0.0, self._soc_pct - 0.01)
        self._voltage_V = 22.0 + (self._soc_pct / 100.0) * 11.6

        payload = struct.pack(">I", int(self._voltage_V * 100))
        frame = self._build_frame(ArbID.PMS_VOLTAGE, MsgType.TELEMETRY.value, payload)
        self._send(ArbID.PMS_VOLTAGE, frame)
        
        payload = struct.pack(">I", int(self._current_A * 100))
        frame = self._build_frame(ArbID.PMS_CURRENT, MsgType.TELEMETRY.value, payload)
        self._send(ArbID.PMS_CURRENT, frame)
        
        payload = struct.pack(">I", int(self._soc_pct * 100))
        frame = self._build_frame(ArbID.PMS_SOC, MsgType.TELEMETRY.value, payload)
        self._send(ArbID.PMS_SOC, frame)

class AttitudeNode(BaseNode):
    def __init__(self, bus):
        super().__init__(NodeID.ATTITUDE, bus)
        self._t0 = time.time()
    
    def _send_telemetry(self):
        # generate a quaternion that rotates around the Z-axis over time
        t = time.time() - self._t0
        angle = (t % 10) / 10 * 2 * 3.141592653589793  # full rotation every 10 seconds
        q0 = int((0.7071 * 10000))  # cos(45°)
        q1 = int((0.0 * 10000))     # sin(45°) * axis_x
        q2 = int((0.0 * 10000))     # sin(45°) * axis_y
        q3 = int((0.7071 * 10000))  # sin(45°) * axis_z

        payload = struct.pack(">HH", q0, q3)
        frame = self._build_frame(ArbID.ADCS_QUATERNION, MsgType.TELEMETRY.value, payload)
        self._send(ArbID.ADCS_QUATERNION, frame)

class ThermalNode(BaseNode):
    def __init__(self, bus):
        super().__init__(NodeID.THERMAL, bus)
        self._temperature_C = 25.0

    def _send_telemetry(self):
        # slowly increase temperature
        self._temperature_C += 0.05
        if self._temperature_C > 85.0:
            self._temperature_C = -40.0  # reset to simulate cooling

        payload = struct.pack(">I", int((self._temperature_C + 273.15) * 100))  # convert to Kelvin and pack as unsigned int
        frame = self._build_frame(ArbID.TCS_TEMP_ZONE_A, MsgType.TELEMETRY.value, payload)
        self._send(ArbID.TCS_TEMP_ZONE_A, frame)

        payload = struct.pack(">I", int((self._temperature_C + 273.15) * 100))
        frame = self._build_frame(ArbID.TCS_TEMP_ZONE_B, MsgType.TELEMETRY.value, payload)
        self._send(ArbID.TCS_TEMP_ZONE_B, frame)    

class PropulsionNode(BaseNode):
    def __init__(self, bus):
        super().__init__(NodeID.PROPULSION, bus)
        self._fuel_mass_kg = 3.0
        self._thrust_mN = 0.0

    def _send_telemetry(self):
        # slowly decrease fuel mass and increase thrust
        self._fuel_mass_kg = max(0.0, self._fuel_mass_kg - 0.001)
        self._thrust_mN = min(220.0, self._thrust_mN + 0.5)

        payload = struct.pack(">I", int(self._fuel_mass_kg * 1000))  # convert to grams and pack as unsigned int
        frame = self._build_frame(ArbID.PROP_FUEL_MASS, MsgType.TELEMETRY.value, payload)
        self._send(ArbID.PROP_FUEL_MASS, frame)

        payload = struct.pack(">I", int(self._thrust_mN * 100))  # convert to centi-milliNewtons and pack as unsigned int
        frame = self._build_frame(ArbID.PROP_THRUST, MsgType.TELEMETRY.value, payload)
        self._send(ArbID.PROP_THRUST, frame)