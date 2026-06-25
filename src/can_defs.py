from enum import IntEnum

class NodeID(IntEnum):
    POWER      = 0x01
    ATTITUDE   = 0x02
    THERMAL    = 0x03
    PROPULSION = 0x04

class ArbID(IntEnum):
    # Power Management System
    PMS_HEARTBEAT  = 0x100
    PMS_VOLTAGE    = 0x101
    PMS_CURRENT    = 0x102
    PMS_SOC        = 0x103 # State of Charge

    # Attitude Determination and Control System
    ADCS_HEARTBEAT = 0x200
    ADCS_QUATERNION  = 0x201
    ADCS_RATE      = 0x202

    # Thermal Control System
    TCS_HEARTBEAT  = 0x300
    TCS_TEMP_ZONE_A  = 0x301
    TCS_TEMP_ZONE_B  = 0x302
    TCS_HEATER_STATE = 0x303

    # Propulsion System
    PROP_HEARTBEAT = 0x400
    PROP_THRUST      = 0x401
    PROP_FUEL_MASS   = 0x402
    PROP_TANK_PRESS  = 0x403


class MsgType(IntEnum):
    HEARTBEAT = 0x00
    TELEMETRY = 0x01
    COMMAND   = 0x02
    FAULT     = 0x03


NODE_REGISTRY = {
    NodeID.POWER: {
        "name": "Power Management (PMS)",
        "heartbeat_arb_id": ArbID.PMS_HEARTBEAT,
        "heartbeat_interval_s": 1.0,
        "timeout_multiplier": 3.0,
        "critical": True,
    },

    NodeID.ATTITUDE: {
        "name": "Attitude Determination and Control (ADCS)",
        "heartbeat_arb_id": ArbID.ADCS_HEARTBEAT,
        "heartbeat_interval_s": 0.5,
        "timeout_multiplier": 3.0,
        "critical": True,
    },

    NodeID.THERMAL: {
        "name": "Thermal Control (TCS)",
        "heartbeat_arb_id": ArbID.TCS_HEARTBEAT,
        "heartbeat_interval_s": 2.0,
        "timeout_multiplier": 3.0,
        "critical": False,
    },

    NodeID.PROPULSION: {
        "name": "Propulsion (PROP)",
        "heartbeat_arb_id": ArbID.PROP_HEARTBEAT,
        "heartbeat_interval_s": 1.0,
        "timeout_multiplier": 3.0,
        "critical": False,
    }
}


LIMITS = {
    "bus_voltage_V":    (22.0, 33.6),  # 3S Li-ion range
    "bus_current_A":    (0.0,  15.0),
    "soc_percent":      (0.0,  100.0),
    "quaternion":       (-1.0, 1.0),  
    "temperature_C":    (-40.0, 85.0),  
    "fuel_mass_kg":     (0.0,  3.0),  
    "thrust_mN":        (0.0,  220.0), 
}

def compute_checksum(data: bytes) -> int:
    # XOR bytes 0 through 6
    # return the result
    checksum = 0
    for byte in data[:7]:
        checksum ^= byte    
    return checksum

def validate_checksum(data: bytes) -> bool:
    # check length first — must be 8 bytes
    # compare byte 7 against compute_checksum result
    if len(data) != 8:
        return False
    return data[7] == compute_checksum(data)