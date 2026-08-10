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


def write_group(
    group_id,
    ordinal,
    description,
    points,
    label=None,
    model_parameters=None,
):
    """
    `label` is prefixed onto every point's object_name before writing --
    BACnet requires object NAMES to be unique within a device too, not just
    object identifiers, and once everything merged into one supervisory
    device, generic names like "Boiler OK" collided across all three
    boilers. Prefixing also makes the object browser usable with the full
    objects under one device instead of a wall of identical-looking names.
    """
    label = label or group_id.replace("ACI-SIM-", "")
    for p in points:
        p["object_name"] = f"{label} {p['object_name']}"

    group = {
        "group_id": group_id,
        "instance_offset": ordinal * 1000,
        "description": description,
        "model_parameters": model_parameters or {},
        "points": points,
    }
    filename = group_id.replace("ACI-SIM-", "").replace("-", "_").lower() + ".json"
    path = CONFIG_DIR / filename
    with open(path, "w") as f:
        json.dump(group, f, indent=2)
        f.write("\n")
    print(f"wrote {path} (offset {group['instance_offset']}, {len(points)} points)")


# ---------------------------------------------------------------- SITE ----
write_group("ACI-SIM-SITE", 0, "Outside air and whole-building pressure conditions.", [
    av("oa_temp", 80, "Outside Air Temperature", "Simulated outside air temperature, adjustable for seasonal training",
       units="degrees-fahrenheit", initial=70.0, minimum=-20.0, maximum=130.0, cov=0.5),
    av("oa_humidity", 81, "Outside Air Humidity", "Simulated outside air relative humidity, adjustable for seasonal training",
       units="percent-relative-humidity", initial=50.0, minimum=0.0, maximum=100.0, cov=1.0),
    ai("building_pressure", 82, "Building Pressure",
       "Simulated indoor-to-outdoor building pressure driven by AHU supply and EF-1 exhaust trim",
       units="inches-of-water", initial=0.0, minimum=-0.25, maximum=0.25,
       normal_low=0.03, normal_high=0.10, cov=0.005),
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
       initial=0.0, minimum=0.0, maximum=1000.0, cov=2.0),
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
        ao("chws_stpt_reset", 21, "CHWS Stpt Reset", f"Chiller {n} chilled-water-supply setpoint reset",
           units="degrees-fahrenheit", minimum=38.0, maximum=54.0, initial=44.0, relinquish=44.0),
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
        ao("hws_stpt_reset", 20, "HWS Stpt Reset", f"Boiler {n} hot-water-supply setpoint reset",
           units="degrees-fahrenheit", minimum=100.0, maximum=200.0, initial=180.0, relinquish=180.0),
    ])

# ----------------------------------------------------------------- AHU --
write_group("ACI-SIM-AHU-1", 9, "Simulated AHU valves/dampers/fans, sensors, and hard interlocks (High Static Pressure, Freezestat).", [
    ao("cooling_valve", 20, "Cooling Valve", "AHU-1 cooling coil valve position command"),
    ao("heating_valve", 21, "Heating Valve", "AHU-1 heating coil valve position command"),
    ao("preheat_valve", 22, "Preheat Valve", "AHU-1 preheat coil valve position command"),
    ao("economizer", 23, "Economizer", "AHU-1 outside-air/return-air damper position command"),
    av("sa_temp_setpoint", 1, "SA Temperature Setpoint",
       "Single WebCTRL-reset supply-air-temperature setpoint used for both cooling and heating operation",
       units="degrees-fahrenheit", initial=55.0, minimum=45.0, maximum=95.0,
       cov=0.5, direction="webctrl_to_sim", commandable=True, relinquish=55.0),
    av("duct_static_pressure_setpoint", 2, "Duct Static Pressure Setpoint",
       "WebCTRL-reset supply-duct static-pressure setpoint used by the simulated supply-fan PID",
       units="inches-of-water", initial=1.0, minimum=0.25, maximum=2.0,
       cov=0.01, direction="webctrl_to_sim", commandable=True, relinquish=1.0),
    av("duct_static_pressure", 3, "Duct Static Pressure",
       "Simulated sensor two-thirds down the common supply trunk, upstream of the first VAV takeoff",
       units="inches-of-water", initial=0.0, minimum=0.0, maximum=10.0,
       cov=0.01),
    av("sa_fan_speed_feedback", 4, "SA Fan Speed Feedback",
       "Simulated supply-fan VFD speed feedback generated by the duct-static PID",
       units="percent", initial=0.0, minimum=0.0, maximum=100.0, cov=1.0),
    bo("ra_fan_ss", 60, "RA Fan SS", "AHU-1 return air fan start/stop command"),
    bo("sa_fan_ss", 61, "SA Fan SS", "AHU-1 supply air fan start/stop command"),
    ai("ahu_ma_temp", 1, "AHU MA Temp", "Simulated AHU-1 mixed air temperature", initial=60.0, minimum=-20.0, maximum=120.0),
    ai("ahu_ra_temp", 2, "AHU RA Temp", "Simulated AHU-1 return air temperature", initial=72.0, minimum=40.0, maximum=100.0),
    ai("ahu_ra_humidity", 3, "AHU RA Humidity", "Simulated AHU-1 return air humidity", units="percent-relative-humidity",
       initial=50.0, minimum=0.0, maximum=100.0),
    ai("ahu_sa_temp", 4, "AHU SA Temp", "Simulated AHU-1 supply air temperature -- also referenced by VAV-1..17's AHU SA Temp fallback",
       initial=55.0, minimum=40.0, maximum=120.0, normal_low=52.0, normal_high=58.0),
    ai("ahu_ma_humidity", 5, "AHU MA Humidity", "Simulated AHU-1 mixed-air relative humidity at the OA/RA mixing plenum",
       units="percent-relative-humidity", initial=50.0, minimum=0.0, maximum=100.0),
    ai("ahu_sa_humidity", 6, "AHU SA Humidity", "Simulated AHU-1 supply-air relative humidity after the fan",
       units="percent-relative-humidity", initial=50.0, minimum=0.0, maximum=100.0),
    ai("cooling_coil_entering_air_temp", 7, "Cooling Coil Entering Air Temp",
       "Simulated air temperature after the preheat coil and immediately before the cooling coil/freezestat",
       initial=60.0, minimum=-20.0, maximum=120.0),
    bv_interlock("high_static_pressure_trip", 100, "High Static Pressure Trip",
                 "Real physical duct static safety switch relayed to the simulator -- hard interlock, forces immediate AHU shutdown"),
    bv_interlock("freezestat_trip", 101, "Freezestat Trip",
                 "Real physical freezestat switch relayed to the simulator -- hard interlock, forces heating/mixing response and fan shutdown"),
    bi("ra_smoke_detector", 40, "RA Smoke Detector", "Simulated return air smoke alarm", alarm=True),
    bi("sa_smoke_detector", 41, "SA Smoke Detector", "Simulated supply air smoke alarm", alarm=True),
    bi("sa_fan_status", 42, "SA Fan Status", "Simulated AHU-1 supply fan run proof"),
    bi("ra_fan_status", 43, "RA Fan Status", "Simulated AHU-1 return fan run proof"),
    bi("automatic_high_static_trip", 44, "Automatic High Static Trip",
       "Latched automatic 4.0 in. H2O high-static safety trip; Restart is the manual reset", alarm=True),
    bi("duct_structural_failure", 45, "Duct Structural Failure",
       "Latched training failure after pressure exceeds the configured 5.0 in. H2O duct limit while the automatic safety is bypassed", alarm=True),
    bi("automatic_freezestat_trip", 46, "Automatic Freezestat Trip",
       "Latched automatic low-temperature cutout after cooling-coil entering air remains at or below 35 F for 10 simulated seconds", alarm=True),
    bi("cooling_coil_freeze_condition", 47, "Cooling Coil Freeze Condition",
       "Subfreezing cooling-coil exposure/ice condition while the automatic freezestat safety is bypassed", alarm=True),
    bi("cooling_coil_rupture_flood", 48, "Cooling Coil Rupture Flood",
       "Latched cooling-coil rupture and water-release training alarm after the configured freeze exposure", alarm=True),
])

# ---------------------------------------------------------- EXHAUST FAN --
write_group("ACI-SIM-EF-1", 10, "Simulated exhaust fan.", [
    ao("exh_air_damper", 20, "Exh Air Damper", "Exhaust air damper position command"),
    ao("vfd_speed_command", 21, "VFD Speed Command",
       "WebCTRL exhaust-fan VFD speed command used to trim occupied building pressure",
       initial=35.0, relinquish=35.0),
    bo("exh_fan_ss", 60, "Exh Fan S/S", "Exhaust fan start/stop command"),
    bi("fan_status", 40, "Fan Status", "Simulated exhaust fan run status"),
])

# ------------------------------------------------------------------ VAV --
VAV_PROFILES = {
    1: {
        "space_name": "Lobby / reception",
        "floor_area_sqft": 980.0,
        "max_airflow_cfm": 1100.0,
        "occupied_minimum_airflow_cfm": 300.0,
        "heating_maximum_airflow_cfm": 550.0,
    },
    2: {
        "space_name": "Training classroom",
        "floor_area_sqft": 1250.0,
        "max_airflow_cfm": 1500.0,
        "occupied_minimum_airflow_cfm": 450.0,
        "heating_maximum_airflow_cfm": 750.0,
    },
    3: {
        "space_name": "Small core office", "floor_area_sqft": 600.0,
        "max_airflow_cfm": 400.0, "occupied_minimum_airflow_cfm": 120.0,
        "heating_maximum_airflow_cfm": 200.0,
        "zone_thermal_capacitance_btuper_f": 5400.0, "zone_envelope_ua_btuh_per_f": 35.0,
        "zone_peak_solar_gain_btuh": 0.0, "zone_solar_peak_hour": 12.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 7.2, "zone_occupants_per_1000_sqft": 5.0,
        "zone_humidity_capacitance_multiplier": 12.0, "zone_initial_humidity_pct": 43.0,
        "zone_adjacent_mixing_cfm": 18.0,
        "zone_temp_initial_f": 71.6, "airflow_default_cfm": 120.0, "damper_default_pct": 38.0,
    },
    4: {
        "space_name": "North perimeter office", "floor_area_sqft": 750.0,
        "max_airflow_cfm": 550.0, "occupied_minimum_airflow_cfm": 165.0,
        "heating_maximum_airflow_cfm": 275.0,
        "zone_thermal_capacitance_btuper_f": 7500.0, "zone_envelope_ua_btuh_per_f": 150.0,
        "zone_peak_solar_gain_btuh": 800.0, "zone_solar_peak_hour": 13.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 7.2, "zone_occupants_per_1000_sqft": 5.0,
        "zone_humidity_capacitance_multiplier": 12.0, "zone_initial_humidity_pct": 42.0,
        "zone_adjacent_mixing_cfm": 20.0,
        "zone_temp_initial_f": 70.9, "airflow_default_cfm": 165.0, "damper_default_pct": 38.0,
    },
    5: {
        "space_name": "West perimeter office", "floor_area_sqft": 900.0,
        "max_airflow_cfm": 720.0, "occupied_minimum_airflow_cfm": 215.0,
        "heating_maximum_airflow_cfm": 360.0,
        "zone_thermal_capacitance_btuper_f": 9900.0, "zone_envelope_ua_btuh_per_f": 250.0,
        "zone_peak_solar_gain_btuh": 4500.0, "zone_solar_peak_hour": 16.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 7.2, "zone_occupants_per_1000_sqft": 5.0,
        "zone_humidity_capacitance_multiplier": 12.0, "zone_initial_humidity_pct": 46.0,
        "zone_adjacent_mixing_cfm": 22.0,
        "zone_temp_initial_f": 72.7, "airflow_default_cfm": 430.0, "damper_default_pct": 68.0,
    },
    6: {
        "space_name": "East conference / lobby", "floor_area_sqft": 1225.0,
        "max_airflow_cfm": 950.0, "occupied_minimum_airflow_cfm": 285.0,
        "heating_maximum_airflow_cfm": 475.0,
        "zone_thermal_capacitance_btuper_f": 13475.0, "zone_envelope_ua_btuh_per_f": 260.0,
        "zone_peak_solar_gain_btuh": 3000.0, "zone_solar_peak_hour": 9.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 11.7, "zone_occupants_per_1000_sqft": 25.0,
        "zone_humidity_capacitance_multiplier": 15.0, "zone_initial_humidity_pct": 48.0,
        "zone_adjacent_mixing_cfm": 35.0,
        "zone_temp_initial_f": 73.2, "airflow_default_cfm": 665.0, "damper_default_pct": 78.0,
    },
    7: {
        "space_name": "North perimeter office", "floor_area_sqft": 850.0,
        "max_airflow_cfm": 620.0, "occupied_minimum_airflow_cfm": 185.0,
        "heating_maximum_airflow_cfm": 310.0,
        "zone_thermal_capacitance_btuper_f": 9350.0, "zone_envelope_ua_btuh_per_f": 180.0,
        "zone_peak_solar_gain_btuh": 900.0, "zone_solar_peak_hour": 13.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 7.2, "zone_occupants_per_1000_sqft": 5.0,
        "zone_humidity_capacitance_multiplier": 12.0, "zone_initial_humidity_pct": 43.0,
        "zone_adjacent_mixing_cfm": 22.0,
        "zone_temp_initial_f": 70.7, "airflow_default_cfm": 185.0, "damper_default_pct": 38.0,
    },
    8: {
        "space_name": "West perimeter office", "floor_area_sqft": 1000.0,
        "max_airflow_cfm": 780.0, "occupied_minimum_airflow_cfm": 235.0,
        "heating_maximum_airflow_cfm": 390.0,
        "zone_thermal_capacitance_btuper_f": 11000.0, "zone_envelope_ua_btuh_per_f": 280.0,
        "zone_peak_solar_gain_btuh": 5000.0, "zone_solar_peak_hour": 16.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 7.2, "zone_occupants_per_1000_sqft": 5.0,
        "zone_humidity_capacitance_multiplier": 12.0, "zone_initial_humidity_pct": 45.0,
        "zone_adjacent_mixing_cfm": 25.0,
        "zone_temp_initial_f": 72.4, "airflow_default_cfm": 390.0, "damper_default_pct": 58.0,
    },
    9: {
        "space_name": "Core open office", "floor_area_sqft": 1500.0,
        "max_airflow_cfm": 1100.0, "occupied_minimum_airflow_cfm": 330.0,
        "heating_maximum_airflow_cfm": 550.0,
        "zone_thermal_capacitance_btuper_f": 13500.0, "zone_envelope_ua_btuh_per_f": 60.0,
        "zone_peak_solar_gain_btuh": 0.0, "zone_solar_peak_hour": 12.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 7.2, "zone_occupants_per_1000_sqft": 5.0,
        "zone_humidity_capacitance_multiplier": 12.0, "zone_initial_humidity_pct": 47.0,
        "zone_adjacent_mixing_cfm": 45.0,
        "zone_temp_initial_f": 71.8, "airflow_default_cfm": 330.0, "damper_default_pct": 38.0,
    },
    10: {
        "space_name": "Core training room", "floor_area_sqft": 1700.0,
        "max_airflow_cfm": 1400.0, "occupied_minimum_airflow_cfm": 420.0,
        "heating_maximum_airflow_cfm": 700.0,
        "zone_thermal_capacitance_btuper_f": 18700.0, "zone_envelope_ua_btuh_per_f": 70.0,
        "zone_peak_solar_gain_btuh": 0.0, "zone_solar_peak_hour": 12.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 10.8, "zone_occupants_per_1000_sqft": 15.0,
        "zone_humidity_capacitance_multiplier": 15.0, "zone_initial_humidity_pct": 49.0,
        "zone_adjacent_mixing_cfm": 50.0,
        "zone_temp_initial_f": 73.0, "airflow_default_cfm": 980.0, "damper_default_pct": 78.0,
    },
    11: {
        "space_name": "South showcase / training", "floor_area_sqft": 2400.0,
        "max_airflow_cfm": 2120.0, "occupied_minimum_airflow_cfm": 635.0,
        "heating_maximum_airflow_cfm": 1060.0,
        "zone_thermal_capacitance_btuper_f": 26400.0, "zone_envelope_ua_btuh_per_f": 600.0,
        "zone_peak_solar_gain_btuh": 9000.0, "zone_solar_peak_hour": 13.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 10.8, "zone_occupants_per_1000_sqft": 15.0,
        "zone_humidity_capacitance_multiplier": 15.0, "zone_initial_humidity_pct": 50.0,
        "zone_adjacent_mixing_cfm": 65.0,
        "zone_temp_initial_f": 74.0, "airflow_default_cfm": 1695.0, "damper_default_pct": 88.0,
    },
    12: {
        "space_name": "East perimeter office", "floor_area_sqft": 1050.0,
        "max_airflow_cfm": 850.0, "occupied_minimum_airflow_cfm": 255.0,
        "heating_maximum_airflow_cfm": 425.0,
        "zone_thermal_capacitance_btuper_f": 11550.0, "zone_envelope_ua_btuh_per_f": 300.0,
        "zone_peak_solar_gain_btuh": 4500.0, "zone_solar_peak_hour": 9.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 7.2, "zone_occupants_per_1000_sqft": 5.0,
        "zone_humidity_capacitance_multiplier": 12.0, "zone_initial_humidity_pct": 47.0,
        "zone_adjacent_mixing_cfm": 26.0,
        "zone_temp_initial_f": 72.9, "airflow_default_cfm": 595.0, "damper_default_pct": 78.0,
    },
    13: {
        "space_name": "Top-floor north office", "floor_area_sqft": 700.0,
        "max_airflow_cfm": 500.0, "occupied_minimum_airflow_cfm": 150.0,
        "heating_maximum_airflow_cfm": 250.0,
        "zone_thermal_capacitance_btuper_f": 8400.0, "zone_envelope_ua_btuh_per_f": 210.0,
        "zone_peak_solar_gain_btuh": 1000.0, "zone_solar_peak_hour": 13.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 7.2, "zone_occupants_per_1000_sqft": 5.0,
        "zone_humidity_capacitance_multiplier": 12.0, "zone_initial_humidity_pct": 40.0,
        "zone_adjacent_mixing_cfm": 18.0,
        "zone_temp_initial_f": 69.9, "airflow_default_cfm": 150.0, "damper_default_pct": 38.0,
    },
    14: {
        "space_name": "Top-floor core office", "floor_area_sqft": 1180.0,
        "max_airflow_cfm": 900.0, "occupied_minimum_airflow_cfm": 270.0,
        "heating_maximum_airflow_cfm": 450.0,
        "zone_thermal_capacitance_btuper_f": 12980.0, "zone_envelope_ua_btuh_per_f": 100.0,
        "zone_peak_solar_gain_btuh": 0.0, "zone_solar_peak_hour": 12.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 7.2, "zone_occupants_per_1000_sqft": 5.0,
        "zone_humidity_capacitance_multiplier": 12.0, "zone_initial_humidity_pct": 42.0,
        "zone_adjacent_mixing_cfm": 35.0,
        "zone_temp_initial_f": 71.3, "airflow_default_cfm": 270.0, "damper_default_pct": 38.0,
    },
    15: {
        "space_name": "Top-floor west training", "floor_area_sqft": 2000.0,
        "max_airflow_cfm": 1600.0, "occupied_minimum_airflow_cfm": 480.0,
        "heating_maximum_airflow_cfm": 800.0,
        "zone_thermal_capacitance_btuper_f": 24000.0, "zone_envelope_ua_btuh_per_f": 560.0,
        "zone_peak_solar_gain_btuh": 8000.0, "zone_solar_peak_hour": 16.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 10.8, "zone_occupants_per_1000_sqft": 15.0,
        "zone_humidity_capacitance_multiplier": 15.0, "zone_initial_humidity_pct": 48.0,
        "zone_adjacent_mixing_cfm": 55.0,
        "zone_temp_initial_f": 73.8, "airflow_default_cfm": 1280.0, "damper_default_pct": 88.0,
    },
    16: {
        "space_name": "Top-floor controls lab", "floor_area_sqft": 1100.0,
        "max_airflow_cfm": 1200.0, "occupied_minimum_airflow_cfm": 360.0,
        "heating_maximum_airflow_cfm": 600.0,
        "zone_thermal_capacitance_btuper_f": 11000.0, "zone_envelope_ua_btuh_per_f": 120.0,
        "zone_peak_solar_gain_btuh": 0.0, "zone_solar_peak_hour": 12.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 13.3, "zone_occupants_per_1000_sqft": 4.0,
        "zone_humidity_capacitance_multiplier": 10.0, "zone_initial_humidity_pct": 45.0,
        "zone_adjacent_mixing_cfm": 32.0,
        "zone_temp_initial_f": 74.3, "airflow_default_cfm": 960.0, "damper_default_pct": 88.0,
    },
    17: {
        "space_name": "Top-floor east conference", "floor_area_sqft": 920.0,
        "max_airflow_cfm": 680.0, "occupied_minimum_airflow_cfm": 205.0,
        "heating_maximum_airflow_cfm": 340.0,
        "zone_thermal_capacitance_btuper_f": 11040.0, "zone_envelope_ua_btuh_per_f": 270.0,
        "zone_peak_solar_gain_btuh": 3500.0, "zone_solar_peak_hour": 9.0,
        "zone_internal_sensible_gain_btuh_per_sqft": 11.7, "zone_occupants_per_1000_sqft": 25.0,
        "zone_humidity_capacitance_multiplier": 15.0, "zone_initial_humidity_pct": 46.0,
        "zone_adjacent_mixing_cfm": 28.0,
        "zone_temp_initial_f": 72.1, "airflow_default_cfm": 340.0, "damper_default_pct": 58.0,
    },
}


def _model_parameters(profile):
    return {
        key: value
        for key, value in profile.items()
        if key not in {
            "zone_temp_initial_f",
            "airflow_default_cfm",
            "damper_default_pct",
        }
    }


def vav_points(
    include_zone_temp: bool,
    *,
    include_zone_humidity: bool = False,
    airflow_default_cfm: float = 0.0,
    damper_default_pct: float = 0.0,
    maximum_airflow_cfm: float = 2000.0,
    occupied_minimum_airflow_cfm: float = 300.0,
    heating_maximum_airflow_cfm: float = 600.0,
    zone_temp_initial_f: float = 72.0,
    zone_humidity_initial_pct: float = 45.0,
):
    pts = [
        ai("discharge_temp", 1, "Discharge Temp", "Simulated discharge air temperature",
           initial=55.0, minimum=40.0, maximum=120.0, normal_low=55.0, normal_high=95.0, cov=0.5),
        ao("damper_position_command", 20, "Damper Position Command",
           "Calculated primary-air damper position, sent to the simulator",
           initial=damper_default_pct, relinquish=damper_default_pct),
        ao("hw_valve_command", 21, "HW Valve", "Reheat valve position command"),
        ao("airflow_setpoint", 22, "Airflow Setpoint",
           "Pressure-independent target airflow; actual flow is bounded by damper capacity and AHU duct pressure",
           units="cubic-feet-per-minute", initial=airflow_default_cfm,
           maximum=maximum_airflow_cfm, relinquish=airflow_default_cfm),
        av(
            "airflow", 80, "Airflow", "Simulated actual measured airflow",
            maximum=maximum_airflow_cfm, cov=5.0,
        ),
        av(
            "heating_min_airflow", 81, "Heating Min Airflow",
            "Read-only heating-mode minimum airflow design value",
            initial=occupied_minimum_airflow_cfm,
            maximum=maximum_airflow_cfm,
        ),
        av(
            "heating_max_airflow", 82, "Heating Max Airflow",
            "Read-only heating-mode maximum airflow design value",
            initial=heating_maximum_airflow_cfm,
            maximum=maximum_airflow_cfm,
        ),
        av(
            "cooling_min_airflow", 83, "Cooling Min Airflow",
            "Read-only cooling-mode minimum airflow design value",
            initial=occupied_minimum_airflow_cfm,
            maximum=maximum_airflow_cfm,
        ),
        av(
            "cooling_max_airflow", 84, "Cooling Max Airflow",
            "Read-only cooling-mode maximum airflow design value",
            initial=maximum_airflow_cfm,
            maximum=maximum_airflow_cfm,
        ),
        av(
            "damper_position_feedback", 85, "Damper Position Feedback",
            "Read-only simulated effective VAV damper position feedback",
            units="percent", initial=damper_default_pct,
            minimum=0.0, maximum=100.0, cov=0.5,
        ),
    ]
    if include_zone_temp:
        pts.append(ai("zone_temp", 2, "Zone Temp", "Simulated zone temperature (virtual zone, no physical ZS thermostat)",
                       initial=zone_temp_initial_f, minimum=45.0, maximum=105.0,
                       normal_low=68.0, normal_high=76.0, cov=0.2))
    if include_zone_humidity:
        pts.append(ai(
            "zone_humidity", 3, "Zone Humidity",
            "Simulated zone relative humidity for this virtual zone",
            units="percent-relative-humidity", initial=zone_humidity_initial_pct,
            minimum=0.0, maximum=100.0, normal_low=30.0, normal_high=60.0, cov=1.0,
        ))
    return pts


profile = VAV_PROFILES[1]
write_group("ACI-SIM-VAV-1", 11,
            "Simulated reheat/airflow/discharge-temp side of VAV-1 (real controller OF141-E2 / 240006). "
            "Zone temperature is not modeled here -- real communicating ZS thermostat.",
            vav_points(
                include_zone_temp=False,
                maximum_airflow_cfm=profile["max_airflow_cfm"],
                occupied_minimum_airflow_cfm=profile["occupied_minimum_airflow_cfm"],
                heating_maximum_airflow_cfm=profile["heating_maximum_airflow_cfm"],
            ),
            model_parameters=_model_parameters(profile))

profile = VAV_PROFILES[2]
write_group("ACI-SIM-VAV-2", 12,
            "Simulated reheat/airflow/discharge-temp side of VAV-2 (real controller OF342-E2 / 240007). "
            "Zone temperature is not modeled here -- real communicating ZS thermostat.",
            vav_points(
                include_zone_temp=False,
                maximum_airflow_cfm=profile["max_airflow_cfm"],
                occupied_minimum_airflow_cfm=profile["occupied_minimum_airflow_cfm"],
                heating_maximum_airflow_cfm=profile["heating_maximum_airflow_cfm"],
            ),
            model_parameters=_model_parameters(profile))

for n in range(3, 18):
    ordinal = 10 + n
    profile = VAV_PROFILES[n]
    humidity_note = (
        " plus simulated Zone Temp and Zone Humidity"
        if n <= 15
        else " plus simulated Zone Temp"
    )
    write_group(f"ACI-SIM-VAV-{n}", ordinal,
                f"Simulated {profile['space_name']} served by VAV-{n}; no physical controller exists yet. "
                f"Same core terminal points as VAV-1/VAV-2{humidity_note}.",
                vav_points(
                    include_zone_temp=True,
                    include_zone_humidity=n <= 15,
                    airflow_default_cfm=profile["airflow_default_cfm"],
                    damper_default_pct=profile["damper_default_pct"],
                    maximum_airflow_cfm=profile["max_airflow_cfm"],
                    occupied_minimum_airflow_cfm=profile["occupied_minimum_airflow_cfm"],
                    heating_maximum_airflow_cfm=profile["heating_maximum_airflow_cfm"],
                    zone_temp_initial_f=profile["zone_temp_initial_f"],
                    zone_humidity_initial_pct=profile["zone_initial_humidity_pct"],
                ),
                model_parameters=_model_parameters(profile))

print("\nDone -- all equipment groups regenerated with group_id/instance_offset. vav_1.json now regenerated "
      "by this script too (previously hand-authored; the schema changed enough this round that keeping it "
      "hand-maintained separately was more error-prone than just generating it identically to vav_2.json). "
      "Virtual VAV-3 through VAV-17 are included for the interactive building floor plan.")
