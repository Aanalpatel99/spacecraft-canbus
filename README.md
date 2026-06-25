# Spacecraft CAN Bus Health Monitor

A fault-tolerant spacecraft subsystem monitor simulating multi-node CAN bus telemetry with real-time fault detection and safe-state transitions.

Built in Python using `python-can` in virtual/loopback mode — no hardware required.

---

## What It Does

Simulates four spacecraft subsystems communicating over a CAN bus and detects three classes of faults:

| Fault | Detection Method |
|-------|-----------------|
| Silent node | Heartbeat timeout — node misses 3× expected interval |
| Corrupted frame | XOR checksum mismatch on byte 7 |
| Dropped frames | Sequence number gap detection per ArbID |
| Bus flooding | Rolling 1-second message rate > 50 msgs/s |
| Node flooding | Rolling 1-second rate > 30 msgs/s on same ArbID |

When a critical node fails, the spacecraft transitions to **SAFE_MODE** automatically.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Virtual CAN Bus (vcan0)              │
└──────────┬──────────┬──────────┬────────────────────┘
           │          │          │          │
    ┌──────▼──┐ ┌─────▼──┐ ┌────▼───┐ ┌───▼──────┐
    │  PMS    │ │  ADCS  │ │  TCS  │ │  PROP    │
    │ Power   │ │Attitude│ │Thermal│ │Propulsion│
    │CRITICAL │ │CRITICAL│ │       │ │          │
    │ARB:0x1xx│ │ARB:0x2x│ │ARB:0x3│ │ARB:0x4xx │
    │ HB: 1s  │ │ HB:0.5s│ │HB: 2s │ │ HB: 1s   │
    └─────────┘ └────────┘ └───────┘ └──────────┘
                                │
              ┌─────────────────▼──────────────────┐
              │         Health Monitor Thread        │
              │  1. Flood detection (rolling window) │
              │  2. Checksum validation (XOR)        │
              │  3. Heartbeat timeout tracking       │
              │  4. Sequence gap detection           │
              │  5. Safe-state FSM                   │
              └─────────────────┬──────────────────┘
                                │
              ┌─────────────────▼──────────────────┐
              │    Event Queue → CSV Logger          │
              │    logs/telemetry_YYYYMMDD.csv       │
              └────────────────────────────────────┘
```

---

## CAN Frame Format

```
Byte 0   NodeID      Who sent it        0x01 = PMS
Byte 1   MsgType     Frame kind         0x00 = HEARTBEAT
Byte 2-5 Payload     Sensor data        encoding varies
Byte 6   Sequence    Frame counter      wraps 0-255
Byte 7   Checksum    XOR of bytes 0-6   integrity check
```

## Arbitration ID Scheme

Lower arbitration ID = higher CAN bus priority.

| Range | Subsystem | Critical |
|-------|-----------|---------|
| 0x100–0x17F | Power Management (PMS) | Yes |
| 0x200–0x27F | Attitude Control (ADCS) | Yes |
| 0x300–0x37F | Thermal Control (TCS) | No |
| 0x400–0x47F | Propulsion (PROP) | No |
| 0x001 | SAFE_MODE broadcast | Highest priority |

---

## Safe-State FSM

```
NOMINAL ──[critical timeout / bus flood]──► SAFE_MODE
NOMINAL ──[non-critical timeout]──────────► DEGRADED
DEGRADED ──[critical timeout]─────────────► SAFE_MODE
SAFE_MODE ──[all critical nodes recover]──► NOMINAL
```

State only escalates automatically. De-escalation requires node recovery.

---

## Fault Injection Scenario

The demo runs a scripted 60-second scenario:

```
T+0s  : All 4 nodes NOMINAL
T+5s  : TCS silent          → DEGRADED (non-critical)
T+13s : TCS recovers        → NOMINAL
T+18s : ADCS corrupt frames → CHECKSUM_ERROR events
T+23s : ADCS silent         → SAFE_MODE (critical node)
T+33s : ADCS recovers       → NOMINAL
T+38s : PMS floods bus      → SAFE_MODE
T+44s : Flood clears
T+52s : Simulation ends
```

---

## Project Structure

```
spacecraft-canbus/
├── src/
│   ├── can_defs.py   # Protocol definitions — NodeID, ArbID, MsgType, limits, checksum
│   ├── nodes.py      # Node simulators — BaseNode + 4 concrete subsystem nodes
│   ├── monitor.py    # Health monitor — fault detection pipeline + safe-state FSM
│   └── main.py       # Orchestrator — wires everything, runs fault scenario
├── tests/
│   └── test_monitor.py
├── logs/             # CSV telemetry output (gitignored)
├── requirements.txt
└── README.md
```

---

## Installation & Run

```bash
git clone https://github.com/YOUR_USERNAME/spacecraft-canbus.git
cd spacecraft-canbus

python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows

pip install -r requirements.txt
python src/main.py
```

---

## Output

Terminal prints fault events live as they occur. CSV is written to `logs/telemetry_YYYYMMDD_HHMMSS.csv` with columns:

```
timestamp_utc, elapsed_s, fault_type, severity, node_id, arb_id, detail, state
```

---

## Known Limitations

- **Python, not C/C++** — this is a simulation and architecture demonstrator. Production flight software would be implemented in C with an RTOS. The protocol design, frame encoding, FSM logic, and fault detection architecture map directly to a C implementation.
- **Event de-duplication not implemented** — heartbeat timeout and flood events re-fire every detection cycle while the fault persists. A production system would rate-limit to one event per second with a running counter.
- **Binary criticality model** — real spacecraft FDIR uses dependency graphs, not simple critical/non-critical flags. A thermal failure can indirectly cause a power failure via battery temperature. This implementation documents that limitation explicitly.
- **No SAFE_MODE bus command** — safe mode entry is currently a state flag only. A production implementation would broadcast a SAFE_MODE command frame at ARB 0x001 so all nodes can respond.

---

## Skills Demonstrated

- CAN bus protocol design — arbitration ID scheme, frame encoding, priority model
- Fault detection — heartbeat monitoring, checksum validation, sequence gap detection, flood detection
- Safe-state FSM — one-way escalation, recovery-triggered de-escalation
- Multithreaded systems — daemon threads, threading.Event, thread-safe queue
- Embedded systems patterns — timer-based loops, watchdog-style monitoring, telemetry logging

---

*Built as a portfolio project for spacecraft avionics and embedded firmware roles.*