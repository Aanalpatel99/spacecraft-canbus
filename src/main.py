import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import can
import time
import threading
import csv
from collections import deque
from pathlib import Path
from datetime import datetime

from can_defs import NodeID
from nodes import PowerNode, AttitudeNode, ThermalNode, PropulsionNode, FaultMode
from monitor import HealthMonitor, SpacecraftState, FaultEvent

def run_csv_logger(event_queue: deque, csv_path: str, monitor: HealthMonitor, stop_evt: threading.Event):
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_utc", "elapsed_s", "fault_type", 
                         "severity", "node_id", "arb_id", "detail", "state"])
        t0 = time.time()
        while not stop_evt.is_set() or event_queue:
            try:
                event: FaultEvent = event_queue.popleft()
                writer.writerow([
                    datetime.utcfromtimestamp(event.timestamp).strftime("%H:%M:%S.%f"),
                    f"{event.timestamp - t0:.3f}",
                    event.fault_type.value,
                    event.severity,
                    event.node_id or "",
                    f"0x{event.arb_id:03X}" if event.arb_id else "",
                    event.detail,
                    monitor.state.name
                ])
                f.flush()
            except IndexError:
                time.sleep(0.01)


def main():
    Path("logs").mkdir(exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    csv_path = f"logs/telemetry_{ts}.csv"

    tx_bus = can.interface.Bus(channel="vcan0", interface="virtual", receive_own_messages=False)
    rx_bus = can.interface.Bus(channel="vcan0", interface="virtual", receive_own_messages=True)

    event_queue = deque(maxlen=10000)
    monitor = HealthMonitor(rx_bus, event_queue)

    nodes = {
        NodeID.POWER:      PowerNode(tx_bus),
        NodeID.ATTITUDE:   AttitudeNode(tx_bus),
        NodeID.THERMAL:    ThermalNode(tx_bus),
        NodeID.PROPULSION: PropulsionNode(tx_bus),
    }

    stop_evt = threading.Event()
    logger_thread = threading.Thread(
        target=run_csv_logger,
        args=(event_queue, csv_path, monitor, stop_evt),
        daemon=True
    )

    monitor.start()
    logger_thread.start()
    for node in nodes.values():
        node.start()

    print(f"Running... CSV → {csv_path}")
    print("Injecting faults in 5s...")

    time.sleep(5)
    print("FAULT: TCS going silent")
    nodes[NodeID.THERMAL].inject_fault(FaultMode.SILENT)

    time.sleep(8)
    print("RECOVERY: TCS back online")
    nodes[NodeID.THERMAL].inject_fault(FaultMode.NORMAL)

    time.sleep(5)
    print("FAULT: ADCS corrupt frames")
    nodes[NodeID.ATTITUDE].inject_fault(FaultMode.CORRUPT)

    time.sleep(5)
    print("FAULT: ADCS silent (CRITICAL)")
    nodes[NodeID.ATTITUDE].inject_fault(FaultMode.SILENT)

    time.sleep(10)
    print("RECOVERY: ADCS back")
    nodes[NodeID.ATTITUDE].inject_fault(FaultMode.NORMAL)

    time.sleep(5)
    print("FAULT: PMS flooding bus")
    nodes[NodeID.POWER].inject_fault(FaultMode.FLOOD)

    time.sleep(6)
    print("RECOVERY: PMS flood cleared")
    nodes[NodeID.POWER].inject_fault(FaultMode.NORMAL)

    time.sleep(5)
    print("Done.")

    stop_evt.set()
    for node in nodes.values():
        node.stop()
    monitor.stop()
    tx_bus.shutdown()
    rx_bus.shutdown()
    print(f"Frames: {monitor.total_frames} | Faults: {monitor.fault_frames}")
    print(f"CSV saved → {csv_path}")


if __name__ == "__main__":
    main()