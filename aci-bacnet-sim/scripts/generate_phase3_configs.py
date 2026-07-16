"""
Generator for the equipment group config files. Run with:

    PYTHONPATH=. python scripts/generate_phase3_configs.py

This exists so the ~140-object fleet described in the Phase 1 architecture
addenda is captured in code once, correctly, rather than hand-typed across
15+ large JSON files with room for transcription errors. Re-run it any time
the object model changes -- it overwrites config/devices/*.json (except
vav_1.json, which remains hand-authored from Phase 2 and is regenerated here
too now that vav_1.json's shape needs to match the current schema -- see the
bottom of this file).

Instance-offset convention (see config_models.EquipmentGroupConfig docstring):
    instance_offset = group_ordinal * 1000
so, e.g., AHU-1 (ordinal 9) = 9000, meaning its local AI:1 becomes the real,
global AI:9001 on the one supervisory device every group's objects now live
under. Every group's points keep small local instance numbers (AI:1, AO:20,
etc.) exactly as before -- only the offset is new.
"""
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "devices"


def ai(alias, inst, name, desc, units="degrees-fahrenheit", initial=55.0, minimum=-20.0, maximum=250.0,
       normal_low=None, normal_high=None, cov=0.5, direction="sim_to_webctrl"):
    p = {
        "alias": alias, "object_type": "analog-input", "object_instance": inst,
        "object_name": name, "description": desc, "units": units,
        "signal_direction": direction, "writable": False,
        "initial_value": initial, "minimum": minimum, "maximum": maximum,
        "update_interval_seconds": 1, "cov_increment": cov,
    }
    if normal_low is not None:
        p["normal_range"] = {"low": normal_low, "high": normal_high}
    return p


def ao(alias, inst, name, desc, units="percent", initial=0.0, minimum=0.0, maximum=100.0, relinquish=0.0):
    return {
        "alias": alias, "object_type": "analog-output", "object_instance": inst,
        "object_name": name, "description": desc, "units": units,
        "signal_direction": "webctrl_to_sim", "writable": True, "commandable": True,
        "initial_value": initial, "minimum": minimum, "maximum": maximum,
        "update_interval_seconds": 1, "relinquish_default": relinquish,
    }


def av(alias, inst, name, desc, units="cubic-feet-per-minute", initial=0.0, minimum=0.0, maximum=2000.0,
       cov=5.0, direction="sim_to_webctrl", commandable=False, relinquish=None, instructor=False):
    p = {
        "alias": alias, "object_type": "analog-value", "object_instance": inst,
        "object_name": name, "description": desc, "units": units,
        "signal_direction": "instructor_only" if instructor else direction,
        "writable": commandable, "commandable": commandable,
        "initial_value": initial, "minimum": minimum, "maximum": maximum,
        "update_interval_seconds": 1, "cov_increment": cov,
    }
    if commandable:
        p["relinquish_default"] = relinquish if relinquish is not None else initial
    return p


def bi(alias, inst, name, desc, direction="sim_to_webctrl", alarm=False, initial=0):
    return {
        "alias": alias, "object_type": "binary-input", "object_instance": inst,
        "object_name": name, "description": desc, "units": "no-units",
        "signal_direction": "alarm_fault" if alarm else direction,
        "writable": False, "initial_value": initial, "update_interval_seconds": 1,
    }


def bo(alias, inst, name, desc, initial=0):
    return {
        "alias": alias, "object_type": "binary-output", "object_instance": inst,
        "object_name": name, "description": desc, "units": "no-units",
        "signal_direction": "webctrl_to_sim", "writable": True, "commandable": True,
        "initial_value": initial, "update_interval_seconds": 1,
    }


def bv_interlock(alias, inst, name, desc, initial=0):
    return {
        "alias": alias, "object_type": "binary-value", "object_instance": inst,
        "object_name": name, "description": desc, "units": "no-units",
        "signal_direction": "webctrl_to_sim", "writable": True, "commandable": True,
        "initial_value": initial, "update_interval_seconds": 1, "interlock": True,
    }


def write_group(group_id, ordinal, description, points, label=None):
    """
    `label` is prefixed onto every point's object_name before writing --
    BACnet requires object NAMES to be unique within a device too, not just
    object identifiers, and once everything merged into one supervisory
    device, generic names like "Boiler OK" collided across all three
    boilers. Prefixing also makes the object browser usable with 143
    objects under one device instead of a wall of identical-looking names.
    """
    label = label or group_id.replace("ACI-SIM-", "")
    for p in points:
        p["object_name"] = f"{label} {p['object_name']}"

    group = {
        "group_id": group_id,
        "instance_offset": ordinal * 1000,
        "description": description,
        "points": points,
    }
    filename = group_id.replace("ACI-SIM-", "").replace("-", "_").lower() + ".json"
    path = CONFIG_DIR / filename
    with open(path, "w") as f:
        json.dump(group, f, indent=2)
        f.write("\n")
    print(f"wrote {path} (offset {group['instance_offset']}, {len(points)} points)")


# ---------------------------------------------------------------- SITE ----
write_group("ACI-SIM-SITE", 0, "Outside air conditions, instructor/scenario-adjustable for seasonal training.", [
    av("oa_temp", 80, "Outside Air Temperature", "Simulated outside air temperature, adjustable for seasonal training",
       units="degrees-fahrenheit", initial=70.0, minimum=-20.0, maximum=130.0, cov=0.5),
    av("oa_humidity", 81, "Outside Air Humidity", "Simulated outside air relative humidity, adjustable for seasonal training",
       units="percent-relative-humidity", initial=50.0, minimum=0.0, maximum=100.0, cov=1.0),
])

# ------------------------------------------------------------ CHW-PLANT --
write_group("ACI-SIM-CHW-PLANT", 1, "Chiller Manager's plant-level status/common-header points, plus Emerg/Refrig Shutdown hard interlocks.", [
    bi("ch1_power_loss", 40, "CH 1 Power Loss", "Simulated chiller 1 power loss alarm", alarm=True),
    bi("ch2_power_loss", 41, "CH 2 Power Loss", "Simulated chiller 2 power loss alarm", alarm=True),
    bi("ch3_power_loss", 42, "CH 3 Power Loss", "Simulated chiller 3 power loss alarm", alarm=True),
    bi("chiller1_ok", 43, "Chiller 1 OK", "Plant-level mirror of Chiller-1's own status (separate BACnet address per Jeff's correction)"),
    bi("chiller2_ok", 44, "Chiller 2 OK", "Plant-level mirror of Chiller-2's own status"),
    bi("chiller3_ok", 45, "Chiller 3 OK", "Plant-level mirror of Chiller-3's own status"),
    ai("chwr_temp_common", 1, "CHWR Temp", "Common chilled-water-return header temperature", units="degrees-fahrenheit",
       initial=54.0, minimum=32.0, maximum=90.0, normal_low=50.0, normal_high=58.0),
    ai("chws_flow_common", 2, "CHWS Flow", "Common chilled-water-supply header flow", units="gallons-per-minute",
       initial=0.0, minimum=0.0, maximum=500.0, cov=2.0),
    ai("chws_temp_common", 3, "CHWS Temp", "Common chilled-water-supply header temperature", units="degrees-fahrenheit",
       initial=44.0, minimum=32.0, maximum=90.0, normal_low=42.0, normal_high=48.0),
    bo("remote_shutdown", 60, "Remote Shutdown", "Plant-wide remote shutdown command"),
    bv_interlock("emerg_shutdown_trip", 100, "Emerg Shutdown Trip",
                 "Real physical Emerg Shutdn safety switch relayed to the simulator -- hard interlock, forces immediate plant shutdown"),
    bv_interlock("refrig_shutdown_trip", 101, "Refrig Shutdown Trip",
                 "Real physical Refrig Shutdn safety switch relayed to the simulator -- hard interlock, forces immediate plant shutdown"),
])

# ------------------------------------------------------------- CHILLERS --
for n, ordinal in ((1, 2), (2, 3), (3, 4)):
    write_group(f"ACI-SIM-CHILLER-{n}", ordinal, f"Simulated chiller {n} unit, condenser water, and cooling tower.", [
        bo("chiller_enable", 60, "Chiller Enable", f"Chiller {n} enable command"),
        bo("chiller_ss", 61, "Chiller S/S", f"Chiller {n} start/stop command"),
        bi("chiller_status", 40, "Chiller Status", f"Chiller {n} run status (own address, separate from plant-level OK)"),
        ao("byp_vlv_output", 20, "Byp Vlv Output", f"Chiller {n} bypass valve position command"),
        bo("chw_iso_valve", 62, "CHW Iso Valve", f"Chiller {n} CHW isolation valve open/close command"),
        bi("chw_iso_vlv_sts", 41, "CHW Iso Vlv Sts", f"Chiller {n} CHW isolation valve status"),
        bo("chw_pump_ss", 63, "CHW Pump S/S", f"Chiller {n} CHW pump start/stop command"),
        bi("chw_pump_status", 42, "CHW Pump Status", f"Chiller {n} CHW pump run status"),
        ai("chwr_temp", 1, "CHWR Temp", f"Chiller {n} chilled-water-return temperature (per-unit)",
           initial=54.0, minimum=32.0, maximum=90.0, normal_low=50.0, normal_high=58.0),
        ao("chws_stpt_reset", 21, "CHWS Stpt Reset", f"Chiller {n} chilled-water-supply setpoint reset"),
        ai("chws_temp", 2, "CHWS Temp", f"Chiller {n} chilled-water-supply temperature (per-unit)",
           initial=44.0, minimum=32.0, maximum=90.0, normal_low=42.0, normal_high=48.0),
        bo("ct_fan_ss", 64, "CT Fan S/S", f"Chiller {n} cooling tower fan start/stop command"),
        bi("ct_fan_status", 43, "CT Fan Status", f"Chiller {n} cooling tower fan run status"),
        bi("ct_vfd_fault", 44, "CT VFD Fault", f"Chiller {n} cooling tower VFD fault", alarm=True),
        ao("ct_vfd_output", 22, "CT VFD Output", f"Chiller {n} cooling tower VFD speed command"),
        bo("cw_pump_ss", 65, "CW Pump S/S", f"Chiller {n} condenser water pump start/stop command"),
        bi("cw_pump_status", 45, "CW Pump Status", f"Chiller {n} condenser water pump run status"),
        ai("cwr_temp", 3, "CWR Temp", f"Chiller {n} condenser-water-return temperature",
           initial=85.0, minimum=40.0, maximum=110.0),
        ai("cws_basin_temp", 4, "CWS Basin Temp", f"Chiller {n} cooling tower basin temperature (freeze-protection relevant)",
           initial=70.0, minimum=-20.0, maximum=110.0),
        ai("cws_temp", 5, "CWS Temp", f"Chiller {n} condenser-water-supply temperature",
           initial=75.0, minimum=40.0, maximum=110.0),
        bo("manager_reset", 66, "Manager Reset", f"Chiller {n} alarm/lockout reset pulse"),
    ])

# ---------------------------------------------------------- BOILER-MGR --
write_group("ACI-SIM-BOILER-MGR", 5, "Boiler Manager's plant-level status/enable points.", [
    bi("boiler1_ok", 40, "Boiler 1 OK", "Plant-level mirror of Boiler-1's own status (separate address per Jeff's correction)"),
    bi("boiler2_ok", 41, "Boiler 2 OK", "Plant-level mirror of Boiler-2's own status"),
    bi("boiler3_ok", 42, "Boiler 3 OK", "Plant-level mirror of Boiler-3's own status"),
    bo("enable_boiler1", 60, "Enable Boiler1", "Boiler 1 enable command"),
    bo("enable_boiler2", 61, "Enable Boiler2", "Boiler 2 enable command"),
    bo("enable_boiler3", 62, "Enable Boiler3", "Boiler 3 enable command"),
])

# -------------------------------------------------------------- BOILERS --
for n, ordinal in ((1, 6), (2, 7), (3, 8)):
    write_group(f"ACI-SIM-BOILER-{n}", ordinal, f"Simulated boiler {n} unit.", [
        bi("boiler_ok", 40, "Boiler OK", f"Boiler {n} run status (own address, separate from Mgr-level)"),
        bo("boiler_ss", 60, "Boiler S/S", f"Boiler {n} start/stop command"),
        bo("circ_pump_ss", 61, "Circ Pump S/S", f"Boiler {n} circulator pump start/stop command"),
        bo("hw_pump_ss", 62, "HW Pump S/S", f"Boiler {n} hot water pump start/stop command"),
        ao("hws_stpt_reset", 20, "HWS Stpt Reset", f"Boiler {n} hot-water-supply setpoint reset"),
    ])

# ----------------------------------------------------------------- AHU --
write_group("ACI-SIM-AHU-1", 9, "Simulated AHU valves/dampers/fans, sensors, and hard interlocks (High Static Pressure, Freezestat).", [
    ao("cooling_valve", 20, "Cooling Valve", "AHU-1 cooling coil valve position command"),
    ao("heating_valve", 21, "Heating Valve", "AHU-1 heating coil valve position command"),
    ao("preheat_valve", 22, "Preheat Valve", "AHU-1 preheat coil valve position command"),
    ao("economizer", 23, "Economizer", "AHU-1 outside-air/return-air damper position command"),
    bo("ra_fan_ss", 60, "RA Fan SS", "AHU-1 return air fan start/stop command"),
    bo("sa_fan_ss", 61, "SA Fan SS", "AHU-1 supply air fan start/stop command"),
    ai("ahu_ma_temp", 1, "AHU MA Temp", "Simulated AHU-1 mixed air temperature", initial=60.0, minimum=-20.0, maximum=120.0),
    ai("ahu_ra_temp", 2, "AHU RA Temp", "Simulated AHU-1 return air temperature", initial=72.0, minimum=40.0, maximum=100.0),
    ai("ahu_ra_humidity", 3, "AHU RA Humidity", "Simulated AHU-1 return air humidity", units="percent-relative-humidity",
       initial=50.0, minimum=0.0, maximum=100.0),
    ai("ahu_sa_temp", 4, "AHU SA Temp", "Simulated AHU-1 supply air temperature -- also referenced by VAV-1..5's AHU SA Temp fallback",
       initial=55.0, minimum=40.0, maximum=120.0, normal_low=52.0, normal_high=58.0),
    bv_interlock("high_static_pressure_trip", 100, "High Static Pressure Trip",
                 "Real physical duct static safety switch relayed to the simulator -- hard interlock, forces immediate AHU shutdown"),
    bv_interlock("freezestat_trip", 101, "Freezestat Trip",
                 "Real physical freezestat switch relayed to the simulator -- hard interlock, forces heating/mixing response and fan shutdown"),
    bi("ra_smoke_detector", 40, "RA Smoke Detector", "Simulated return air smoke alarm", alarm=True),
    bi("sa_smoke_detector", 41, "SA Smoke Detector", "Simulated supply air smoke alarm", alarm=True),
])

# ---------------------------------------------------------- EXHAUST FAN --
write_group("ACI-SIM-EF-1", 10, "Simulated exhaust fan.", [
    ao("exh_air_damper", 20, "Exh Air Damper", "Exhaust air damper position command"),
    bo("exh_fan_ss", 60, "Exh Fan S/S", "Exhaust fan start/stop command"),
    bi("fan_status", 40, "Fan Status", "Simulated exhaust fan run status"),
])

# ------------------------------------------------------------------ VAV --
def vav_points(include_zone_temp: bool):
    pts = [
        ai("discharge_temp", 1, "Discharge Temp", "Simulated discharge air temperature",
           initial=55.0, minimum=40.0, maximum=120.0, normal_low=55.0, normal_high=95.0, cov=0.5),
        ao("damper_position_command", 20, "Damper Position Command", "Calculated primary-air damper position, sent to the simulator"),
        ao("hw_valve_command", 21, "HW Valve", "Reheat valve position command"),
        ao("airflow_setpoint", 22, "Airflow Setpoint",
           "Calculated target airflow -- context for the simulator, not a hard override of the simulated Airflow value",
           units="cubic-feet-per-minute", maximum=2000.0),
        av("airflow", 80, "Airflow", "Simulated actual measured airflow", cov=5.0),
    ]
    if include_zone_temp:
        pts.append(ai("zone_temp", 2, "Zone Temp", "Simulated zone temperature (virtual zone, no physical ZS thermostat)",
                       initial=72.0, minimum=50.0, maximum=100.0, normal_low=68.0, normal_high=76.0, cov=0.2))
    return pts


write_group("ACI-SIM-VAV-1", 11,
            "Simulated reheat/airflow/discharge-temp side of VAV-1 (real controller OF141-E2 / 240006). "
            "Zone temperature is not modeled here -- real communicating ZS thermostat.",
            vav_points(include_zone_temp=False))

write_group("ACI-SIM-VAV-2", 12,
            "Simulated reheat/airflow/discharge-temp side of VAV-2 (real controller OF342-E2 / 240007). "
            "Zone temperature is not modeled here -- real communicating ZS thermostat.",
            vav_points(include_zone_temp=False))

for n, ordinal in ((3, 13), (4, 14), (5, 15)):
    write_group(f"ACI-SIM-VAV-{n}", ordinal,
                f"Simulated virtual VAV zone {n} -- no physical controller exists yet. Same point set as "
                f"VAV-1/VAV-2 plus a simulated Zone Temp, since there's no real ZS thermostat for this zone.",
                vav_points(include_zone_temp=True))

print("\nDone -- all equipment groups regenerated with group_id/instance_offset. vav_1.json now regenerated "
      "by this script too (previously hand-authored; the schema changed enough this round that keeping it "
      "hand-maintained separately was more error-prone than just generating it identically to vav_2.json).")
