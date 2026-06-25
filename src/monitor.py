import can
import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from can_defs import NODE_REGISTRY, NodeID, ArbID, validate_checksum, LIMITS

class SpacecraftState(Enum):
    NOMINAL   = auto()
    DEGRADED  = auto()
    SAFE_MODE = auto()

class FaultType(Enum):
    HEARTBEAT_TIMEOUT = "HEARTBEAT_TIMEOUT"
    CHECKSUM_ERROR    = "CHECKSUM_ERROR"
    SEQUENCE_GAP      = "SEQUENCE_GAP"
    RANGE_VIOLATION   = "RANGE_VIOLATION"
    BUS_FLOOD         = "BUS_FLOOD"
    NODE_FLOOD        = "NODE_FLOOD"
    SAFE_MODE_ENTRY   = "SAFE_MODE_ENTRY"
    SAFE_MODE_EXIT    = "SAFE_MODE_EXIT"
    NODE_RECOVERED    = "NODE_RECOVERED"

@dataclass
class FaultEvent:
    timestamp:  float
    fault_type: FaultType
    node_id:    Optional[int]
    arb_id:     Optional[int]
    detail:     str
    severity:   str  # "INFO" | "WARNING" | "CRITICAL"

class HealthMonitor(threading.Thread):
    def __init__(self, bus: can.BusABC, event_queue: deque):
        super().__init__(daemon=True)
        self._timed_out_nodes = set()
        self.bus = bus
        self.event_queue = event_queue
        self._stop_evt = threading.Event()
        self._state = SpacecraftState.NOMINAL
        self._state_lock = threading.Lock()

        # per-node last heartbeat time
        self._last_heartbeat = {int(nid): 0.0 for nid in NODE_REGISTRY}

        # per-node last sequence number
        self._last_seq = {}

        # flood detection — rolling windows
        self._bus_msg_times = deque()
        self._arb_msg_times = defaultdict(deque)

        # heartbeat arb_id → node_id lookup
        self._hb_arb_to_node = {
            int(meta["heartbeat_arb_id"]): int(nid)
            for nid, meta in NODE_REGISTRY.items()
        }

        # stats
        self.total_frames = 0
        self.fault_frames = 0

    @property
    def state(self):
        return self._state

    def stop(self):
        self._stop_evt.set()

    def _emit_fault(self, event: FaultEvent):
        self.event_queue.append(event)

    def run(self):
        last_hb_check = time.time()
        while not self._stop_evt.is_set():
            msg = self.bus.recv(timeout=0.05)
            now = time.time()
            if msg:
                self._process_message(msg, now)
            if now - last_hb_check >= 0.1:
                self._check_heartbeat_timeouts(now)
                last_hb_check = now

    def _process_message(self, msg: can.Message, now: float):
        self.total_frames += 1

        # step 1 — flood detection
        self._check_flood(msg, now)

        # step 2 — checksum
        if len(msg.data) == 8:
            if not validate_checksum(bytes(msg.data)):
                self._emit_fault(FaultEvent(
                    timestamp  = now,
                    fault_type = FaultType.CHECKSUM_ERROR,
                    node_id    = int(msg.data[0]),
                    arb_id     = msg.arbitration_id,
                    detail     = f"Bad checksum arb_id=0x{msg.arbitration_id:03X}",
                    severity   = "WARNING"
                ))
                self.fault_frames += 1
                return  # drop corrupted frame

        # step 3 — heartbeat tracking
        if msg.arbitration_id in self._hb_arb_to_node:
            node_id = self._hb_arb_to_node[msg.arbitration_id]
            self._last_heartbeat[node_id] = now

        # step 4 — sequence gap
        self._check_sequence(msg, now)
        
    def _check_sequence(self, msg: can.Message, now: float):
        arb = msg.arbitration_id
        if len(msg.data) < 7:
            return
        seq = msg.data[6]
        if arb in self._last_seq:
            expected = (self._last_seq[arb] + 1) % 256
            if seq != expected:
                self._emit_fault(FaultEvent(
                    timestamp  = now,
                    fault_type = FaultType.SEQUENCE_GAP,
                    node_id    = int(msg.data[0]),
                    arb_id     = arb,
                    detail     = f"Gap on 0x{arb:03X}: expected {expected} got {seq}",
                    severity   = "WARNING"
                ))
        self._last_seq[arb] = seq

    def _check_flood(self, msg: can.Message, now: float):
        cutoff = now - 1.0
        while self._bus_msg_times and self._bus_msg_times[0] < cutoff:
            self._bus_msg_times.popleft()
        self._bus_msg_times.append(now)

        arb = msg.arbitration_id
        while self._arb_msg_times[arb] and self._arb_msg_times[arb][0] < cutoff:
            self._arb_msg_times[arb].popleft()
        self._arb_msg_times[arb].append(now)

        if len(self._bus_msg_times) > 50:
            self._emit_fault(FaultEvent(
                timestamp  = now,
                fault_type = FaultType.BUS_FLOOD,
                node_id    = None,
                arb_id     = arb,
                detail     = f"Bus flood: {len(self._bus_msg_times)} msgs/s",
                severity   = "CRITICAL"
            ))
            self._transition_state(SpacecraftState.SAFE_MODE, None)

        elif len(self._arb_msg_times[arb]) > 30:
            self._emit_fault(FaultEvent(
                timestamp  = now,
                fault_type = FaultType.NODE_FLOOD,
                node_id    = int(msg.data[0]) if msg.data else None,
                arb_id     = arb,
                detail     = f"Node flood: 0x{arb:03X} {len(self._arb_msg_times[arb])} msgs/s",
                severity   = "WARNING"
            ))

    def _check_heartbeat_timeouts(self, now: float):
        for node_id, meta in NODE_REGISTRY.items():
            nid = int(node_id)
            last = self._last_heartbeat[nid]
            if last == 0.0:
                continue
            timeout = meta["heartbeat_interval_s"] * meta["timeout_multiplier"]
            if now - last > timeout and nid not in self._timed_out_nodes:
                self._timed_out_nodes.add(nid)
                self._emit_fault(FaultEvent(
                    timestamp  = now,
                    fault_type = FaultType.HEARTBEAT_TIMEOUT,
                    node_id    = nid,
                    arb_id     = int(meta["heartbeat_arb_id"]),
                    detail     = f"{meta['name']} silent for {timeout:.1f}s",
                    severity   = "CRITICAL" if meta["critical"] else "WARNING"
                ))
                if meta["critical"]:
                    self._transition_state(SpacecraftState.SAFE_MODE, nid)
                else:
                    self._transition_state(SpacecraftState.DEGRADED, nid)

    def _transition_state(self, new_state: SpacecraftState, node_id: Optional[int]):
        with self._state_lock:
            old_state = self._state
            if new_state.value > old_state.value:
                self._state = new_state
                self._emit_fault(FaultEvent(
                    timestamp  = time.time(),
                    fault_type = FaultType.SAFE_MODE_ENTRY,
                    node_id    = node_id,
                    arb_id     = None,
                    detail     = f"{old_state.name} -> {new_state.name}",
                    severity   = "CRITICAL"
                ))