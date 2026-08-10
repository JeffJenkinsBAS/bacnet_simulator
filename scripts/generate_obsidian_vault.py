"""Generate a source-backed Obsidian knowledge pack for the Test Bench vault.

The script writes to a staging directory. It does not modify the live vault.
Generated equipment, scenario, API, test, deployment, and agent notes are
derived from the working checkout plus the dated hardware inventory recorded
during the 2026-07-23 bench audit.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


VERIFIED = "2026-07-24"


def frontmatter(note_type: str, source_path: str = "", **fields: object) -> str:
    lines = [
        "---",
        f"type: {note_type}",
        "status: active",
        "environment: test-bench",
        "owner: Test Bench",
        f"last_verified: {VERIFIED}",
    ]
    if source_path:
        lines.append(f'source_path: "{source_path}"')
    for key, value in fields.items():
        if isinstance(value, bool):
            rendered = str(value).lower()
        elif isinstance(value, (int, float)):
            rendered = str(value)
        else:
            rendered = f'"{str(value)}"'
        lines.append(f"{key}: {rendered}")
    lines.extend(["tags:", f"  - {note_type}", "---", ""])
    return "\n".join(lines)


def write(output: Path, relative: str, body: str) -> None:
    destination = output / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(body.rstrip() + "\n", encoding="utf-8")


def equipment_notes(repo: Path, output: Path) -> tuple[list[str], int]:
    links: list[str] = []
    total_points = 0
    for config_path in sorted((repo / "config" / "devices").glob("*.json")):
        config = json.loads(config_path.read_text(encoding="utf-8"))
        group_id = config["group_id"]
        title = group_id.removeprefix("ACI-SIM-")
        points = config["points"]
        model_parameters = config.get("model_parameters", {})
        total_points += len(points)
        links.append(f"[[{title}]]")
        writable = sum(bool(point.get("writable")) for point in points)
        rows = []
        for point in points:
            instance = config["instance_offset"] + point["object_instance"]
            rows.append(
                "| `{alias}` | {name} | `{kind},{instance}` | {direction} | {writable} | {units} |".format(
                    alias=point["alias"],
                    name=point["object_name"],
                    kind=point["object_type"],
                    instance=instance,
                    direction=point["signal_direction"],
                    writable="yes" if point.get("writable") else "no",
                    units=point.get("units", ""),
                )
            )
        model_section = ""
        if model_parameters:
            model_rows = "\n".join(
                f"| {key.replace('_', ' ').title()} | {value} |"
                for key, value in sorted(model_parameters.items())
            )
            model_section = f"""
## Physical model parameters

| Parameter | Configured value |
|---|---:|
{model_rows}
"""
        body = frontmatter(
            "equipment",
            str(config_path.relative_to(repo)).replace("\\", "/"),
            group_id=group_id,
            instance_offset=config["instance_offset"],
            point_count=len(points),
        )
        body += f"""# {title}

> [!info] Source-backed equipment sheet
> {config["description"]}

| Attribute | Value |
|---|---|
| Supervisory device | `ACI-SIM-SUPERVISOR` / instance `242000` |
| Group ID | `{group_id}` |
| Instance offset | `{config["instance_offset"]}` |
| Points | {len(points)} total / {writable} writable |
| Source | `{config_path.relative_to(repo)}` |

{model_section}
## Point catalog

| Alias | BACnet object name | Object identifier | Direction | Writable | Units |
|---|---|---|---|---|---|
{chr(10).join(rows)}

## Training use

- Verify the object and alias here before forcing, releasing, or injecting a fault.
- WebCTRL writes are accepted only from the verified allowlist in [[Test Bench Network]].
- Record exercises and results in [[Test Run Index]].

## Related

- [[Equipment Catalog]]
- [[BACnet Simulator Building Design Plan]]
- [[BACnet Point Allocation Standard]]
- [[BACnet Device Model]]
- [[BACnet Command and Priority Flow]]
"""
        write(output, f"08 Equipment Templates/{title}.md", body)
    return links, total_points


def vav_schedule(repo: Path, output: Path) -> None:
    """Generate the readable zone/airflow schedule from VAV JSON configs."""
    rows = []
    exposure_rows = []
    for number in range(1, 18):
        path = repo / "config" / "devices" / f"vav_{number}.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        params = config.get("model_parameters", {})
        point_by_alias = {point["alias"]: point for point in config["points"]}
        zone_temp = point_by_alias.get("zone_temp", {}).get("initial_value", "external")
        zone_humidity = point_by_alias.get("zone_humidity", {}).get(
            "initial_value",
            "not published",
        )
        airflow_default = point_by_alias["airflow_setpoint"].get(
            "relinquish_default",
            0,
        )
        humidity_id = (
            f"AI:{config['instance_offset'] + 3}"
            if "zone_humidity" in point_by_alias
            else "—"
        )
        rows.append(
            "| VAV-{n} | {space} | {area:,.0f} | {maximum:,.0f} | {minimum:,.0f} | "
            "{heating:,.0f} | {temp} | {humidity} | {fallback:,.0f} | `{humidity_id}` |".format(
                n=number,
                space=params.get("space_name", "VAV zone"),
                area=float(params.get("floor_area_sqft", 0)),
                maximum=float(params.get("max_airflow_cfm", 0)),
                minimum=float(params.get("occupied_minimum_airflow_cfm", 0)),
                heating=float(params.get("heating_maximum_airflow_cfm", 0)),
                temp=zone_temp,
                humidity=zone_humidity,
                fallback=float(airflow_default or 0),
                humidity_id=humidity_id,
            )
        )
        offset = int(config["instance_offset"])
        exposure_rows.append(
            "| VAV-{n} | `AV:{heat_min}` ({minimum:,.0f} CFM) | "
            "`AV:{heat_max}` ({heating:,.0f} CFM) | "
            "`AV:{cool_min}` ({minimum:,.0f} CFM) | "
            "`AV:{cool_max}` ({maximum:,.0f} CFM) | "
            "`AV:{feedback}` (%) |".format(
                n=number,
                heat_min=offset + 81,
                heat_max=offset + 82,
                cool_min=offset + 83,
                cool_max=offset + 84,
                feedback=offset + 85,
                minimum=float(params.get("occupied_minimum_airflow_cfm", 0)),
                heating=float(params.get("heating_maximum_airflow_cfm", 0)),
                maximum=float(params.get("max_airflow_cfm", 0)),
            )
        )

    body = frontmatter(
        "equipment-schedule",
        "config/devices/vav_*.json",
    )
    body += f"""# VAV Design and Zone Schedule

This is a representative training schedule, not a stamped mechanical design.
WebCTRL remains command authority. Cooling maximum, occupied minimum, heating
maximum, zone load, and thermal/moisture storage are deliberately varied so
terminal behavior does not look cloned.

| VAV | Representative space | Area ft² | Cool max CFM | Occ min CFM | Heat max CFM | Initial °F | Initial %RH | Airflow fallback CFM | Humidity object |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
{chr(10).join(rows)}

## WebCTRL-exposed design and feedback objects

These five simulator-owned analog values are read-only. Their local numbers
are consecutive on every VAV, and no previously live identifier moved:

| VAV | Heating min AV:81 | Heating max AV:82 | Cooling min AV:83 | Cooling max AV:84 | Damper feedback AV:85 |
|---|---|---|---|---|---|
{chr(10).join(exposure_rows)}

The four flow values publish the exact configured design limits. Damper
feedback publishes the effective simulated shaft position after actuator
faults; AO:20 remains the independent WebCTRL damper command.

## Physical response

- Zone temperature comes from an analytical heat balance driven by actual CFM
  and DAT plus envelope, infiltration, solar, internal, and adjacent-space
  loads.
- A stopped/unproven AHU contributes zero supply airflow to the zone balance.
- A 0% damper position overrides minimum-flow logic: an AHU that is proven
  on produces only 1.0 CFM of modeled blade/casing leakage, while an
  unproven or stopped AHU publishes exactly 0.00 CFM.
- Zone humidity is stored as humidity ratio and intentionally moves over
  hours. Terminal reheat does not remove moisture.
- VAV-1 and VAV-2 retain their physical zone sensors.
- Zone Humidity exists only on VAV-3 through VAV-15 at local AI:3.

## Sizing basis

- [Trane Intelligent VAV Systems](https://www.trane.com/content/dam/Trane/Commercial/north-america/products-systems/systems/APP-PRC010-EN.pdf)
- [Price SDV installation manual](https://priceindustries.com/wp-content/uploads/Assets/literature/manuals/section%20a/sdv-manual.pdf)
- [PNNL medium-office prototype](https://www.pnnl.gov/main/publications/external/technical_reports/pnnl-20214.pdf)
- [ASHRAE nonresidential load calculations](https://handbook.ashrae.org/Handbooks/F25/SI/F25_Ch18/f25_ch18_si.aspx)

## Related

- [[BACnet Simulator Building Design Plan]]
- [[BACnet Point Allocation Standard]]
- [[HVAC Realism and Parent Dependencies]]
"""
    write(output, "08 Equipment Templates/VAV Design and Zone Schedule.md", body)


def building_design_plan(repo: Path, output: Path) -> None:
    """Generate the master building/system design plan from VAV configs."""
    floor_totals: dict[int, dict[str, float]] = {
        1: {"zones": 0, "area": 0, "cool": 0, "minimum": 0, "heat": 0},
        2: {"zones": 0, "area": 0, "cool": 0, "minimum": 0, "heat": 0},
        3: {"zones": 0, "area": 0, "cool": 0, "minimum": 0, "heat": 0},
    }
    for number in range(1, 18):
        config = json.loads(
            (repo / "config" / "devices" / f"vav_{number}.json").read_text(
                encoding="utf-8"
            )
        )
        params = config["model_parameters"]
        floor = 1 if number <= 6 else 2 if number <= 12 else 3
        totals = floor_totals[floor]
        totals["zones"] += 1
        totals["area"] += float(params["floor_area_sqft"])
        totals["cool"] += float(params["max_airflow_cfm"])
        totals["minimum"] += float(params["occupied_minimum_airflow_cfm"])
        totals["heat"] += float(params["heating_maximum_airflow_cfm"])

    floor_rows = "\n".join(
        "| {floor} | {zones:.0f} | {area:,.0f} | {cool:,.0f} | {minimum:,.0f} | {heat:,.0f} |".format(
            floor=floor,
            **floor_totals[floor],
        )
        for floor in (1, 2, 3)
    )
    building = {
        key: sum(floor_totals[floor][key] for floor in (1, 2, 3))
        for key in ("zones", "area", "cool", "minimum", "heat")
    }

    body = frontmatter(
        "design-plan",
        "config/building_layout.json; config/devices/*.json; app/equipment/",
    )
    body += f"""# BACnet Simulator Building Design Plan

> [!important] Governing design note
> Start here before changing equipment, flow values, point numbers, parent
> dependencies, or the command-center building layout. This is a
> representative training design, not a stamped mechanical or life-safety
> design.

## Design summary

| Attribute | Current design |
|---|---|
| Building | ACI Building Automation Training Center |
| Floors represented | 3 occupied floors, roof equipment, and central plant |
| Approximate modeled area | {building["area"]:,.0f} ft2 |
| Air terminals | 17 single-duct VAVs with hot-water reheat |
| Aggregate cooling maximum | {building["cool"]:,.0f} CFM |
| Aggregate occupied minimum | {building["minimum"]:,.0f} CFM |
| Aggregate heating maximum | {building["heat"]:,.0f} CFM |
| Central air system | AHU-1 with OA/economizer, preheat, CHW cooling, HW heating, supply/return fans |
| Cooling plant | 3 chillers, 3 CHW pumps, 3 CW pumps, 3 cooling towers |
| Heating plant | 3 boilers with circulation and distribution pumps |
| Pressure control | AHU duct-static PID resets supply-fan VFD speed; AHU supply creates positive building pressure; EF-1 trims excess pressure |
| BACnet topology | One supervisory device, instance 242000, 28 groups / 329 objects verified live |

## Building air schedule by floor

| Floor | Zones | Area ft2 | Cooling max CFM | Occupied min CFM | Heating max CFM |
|---:|---:|---:|---:|---:|---:|
{floor_rows}
| **Building** | **{building["zones"]:.0f}** | **{building["area"]:,.0f}** | **{building["cool"]:,.0f}** | **{building["minimum"]:,.0f}** | **{building["heat"]:,.0f}** |

The full per-space schedule, initial temperatures, humidity values, and
published humidity object identifiers are maintained in
[[VAV Design and Zone Schedule]].

## System flow

```mermaid
flowchart LR
  OA["Site weather / outdoor air"] --> AHU["AHU-1 mixed-air and coil section"]
  CH["3 chillers / 900 GPM CHW header"] -->|44 F nominal CHWS| AHU
  CT["3 cooling towers"] --> CH
  HW["3 boilers / 180 GPM HW header"] -->|180 F nominal HWS| AHU
  HW -->|Terminal reheat| VAV["VAV-1 through VAV-17"]
  AHU -->|Conditioned supply air| VAV
  VAV --> Z["17 modeled spaces"]
  Z -->|Return-air load| AHU
  AHU --> BP["Building pressure"]
  EF["EF-1 exhaust trim"] -->|Reduces pressure| BP
```

## Design operating values

| System | Design/calibration values | Physical dependency |
|---|---|---|
| CHW plant | 300 GPM per proven chiller; 900 GPM total; 44 F nominal CHWS; 54 F nominal CHWR; reset 38-54 F | Chiller command, isolation valve, CHW pump and CW pump proof |
| Condenser water | Tower target is outdoor wet-bulb + 7 F; loaded CWR is CWS + 8 F; 105 F high-head threshold | CW pump and tower fan proof |
| HW plant | 60 GPM per proven distribution boiler; 180 GPM total; 180 F nominal HWS; reset 100-200 F | Boiler proof, circulation pump and HW distribution pump |
| AHU-1 | 15% minimum OA; SAT setpoint 45-95 F, default 55 F; 10 F cooling-coil approach; 2 F fan heat | Supply fan plus available CHW/HW parent plant |
| AHU duct-static PID | AV:9002 setpoint 0.25-2.00 in. H2O, default 1.00; AV:9003 actual 0.00-10.00; AV:9004 VFD feedback; two-thirds main-trunk sensor; P/I/D/interval training controls | Supply-fan command and proof plus design-CFM-weighted feedback of all 17 VAV dampers |
| AHU high-static safety | Automatic trip 4.0 in. H2O; representative training duct-class limit 5.0 in. H2O; structural failure only with explicit safety bypass | Healthy automatic safety, or restricted instructor safety-bypass lesson |
| AHU freezestat safety | Cooling-coil entering air AI:9007; 20 simulated minutes below 32 F without useful CHW flow or 60 with valve open and CHW flow proven | Healthy automatic safety, or restricted instructor safety-bypass lesson |
| AHU heating calibration | About 50% heating valve maintains about 85 F SAT at normal OA; cold OA may require about 72% | Proven hot-water capacity and current mixed-air load |
| EF-1 / pressure | 35% VFD relinquish default; target training pressure 0.03-0.10 in. w.c.; exhaust reduces pressure | AHU supply proof, EF command, damper and VFD |
| VAV zones | 400-2,120 CFM cooling maximum; occupied minimum near 30%; heating maximum near 50%; 70 F heating / 72 F cooling reference setpoints; 1.0 CFM closed-damper leakage with AHU proven and exact 0.00 CFM with AHU off | AHU airflow/static; CHW for cooling; HW for reheat |

## Parent-equipment rules

| Result | Required parent state |
|---|---|
| Duct static / VFD speed | AHU supply-fan command and proof plus WebCTRL static setpoint and downstream VAV damper relief |
| VAV airflow | AHU supply-fan proof, duct static, open damper, and nonzero airflow target; 0% damper overrides occupied minimum |
| Cooling air | Proven CHW plant, usable CHWS, AHU fan, cooling valve, VAV airflow |
| Neutral ventilation | AHU fan and VAV airflow without useful heating/cooling |
| Terminal heating | Proven HW distribution, VAV airflow, open reheat valve |
| Zone temperature | Actual airflow and DAT plus area, thermal mass, envelope, infiltration, solar, people, and adjacent-space loads |
| Zone humidity | Supply/OA moisture, infiltration, people, mixing, and moisture capacitance |
| Positive pressure | Net AHU outdoor/supply air greater than relief/exhaust/leakage |

## Diagnostic and animation rules

- Command/status mismatches become failures after 15 real seconds.
- VAV airflow is normal within an inclusive +/-25% of setpoint for 15 seconds.
- Blue space animation means useful cooling, red means useful HW reheat,
  white/gray means neutral ventilation, and no plume means no proven airflow.
- Material simultaneous AHU cooling and heating is an energy-waste failure
  after the actuator changeover grace period.
- Healthy AHU safeties latch protective shutdown. Structural duct failure or
  cooling-coil freeze/burst requires an explicit restricted safety bypass.
- Safety exposure timers use simulated time; command/status diagnostics use
  wall-clock time.
- WebCTRL owns command points. The simulator calculates physical consequences
  and never writes the BAS command sequence for the operator.

## Change-control rules

1. Review [[BACnet Point Allocation Standard]] before adding any point.
2. Preserve every existing BACnet identifier already mapped in WebCTRL.
3. Update source configuration first, then model behavior, diagnostics,
   building layout, workbook, tests, and this vault in the same change.
4. Validate parent dependencies at 1x and accelerated simulation speed.
5. Run all automated tests and a controlled live read/write/COV acceptance.
6. Record rollback evidence before a service restart.

## Detailed references

- [[VAV Design and Zone Schedule]]
- [[Equipment Catalog]]
- [[HVAC Realism and Parent Dependencies]]
- [[BACnet Point Allocation Standard]]
- [[Interactive Command Center]]
- [[HVAC Realism Feature Backlog]]
"""
    write(output, "02 Project/BACnet Simulator Building Design Plan.md", body)


def point_allocation_standard(repo: Path, output: Path) -> None:
    """Generate a live point-number allocation register and immutable rules."""
    type_specs = (
        ("analog-input", "AI", 1),
        ("analog-output", "AO", 20),
        ("analog-value", "AV", 80),
        ("binary-input", "BI", 40),
        ("binary-output", "BO", 60),
        ("binary-value", "BV", 100),
    )
    groups = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((repo / "config" / "devices").glob("*.json"))
    ]
    allocation_rows = []
    block_rows = []
    for group in sorted(groups, key=lambda item: item["instance_offset"]):
        offset = int(group["instance_offset"])
        block_rows.append(
            f"| `{group['group_id']}` | {offset:,} | {offset:,}-{offset + 999:,} | {len(group['points'])} |"
        )
        next_cells = []
        for object_type, abbreviation, first_instance in type_specs:
            used = [
                int(point["object_instance"])
                for point in group["points"]
                if point["object_type"] == object_type
            ]
            next_local = max(used) + 1 if used else first_instance
            next_global = offset + next_local
            next_cells.append(f"`{abbreviation}:{next_local}` -> `{abbreviation}:{next_global}`")
        allocation_rows.append(
            f"| `{group['group_id']}` | " + " | ".join(next_cells) + " |"
        )
    next_offset = max(int(group["instance_offset"]) for group in groups) + 1000

    body = frontmatter(
        "governance-standard",
        "config/devices/*.json; app/config_models.py",
    )
    body += f"""# BACnet Point Allocation Standard

> [!danger] Existing point numbers are immutable
> Once a point is mapped in WebCTRL, never renumber it, reuse it for a
> different meaning, or move it to another BACnet object type. Retired
> identifiers remain reserved.

## Number construction

`global object instance = equipment instance_offset + local object_instance`

Uniqueness is enforced on the pair `(object type, global instance)`. BACnet
permits an AI and AO to share the same numeric instance because they are
different object types; two AIs may not share it.

## Local numbering convention

| Signal family | BACnet type | Normal starting local instance |
|---|---|---:|
| Analog sensors / calculated inputs | AI | 1 |
| Analog commands | AO | 20 |
| Analog values / software telemetry | AV | 80 |
| Binary status / alarms | BI | 40 |
| Binary commands | BO | 60 |
| Binary values / hard interlocks | BV | 100 |

Local sequences are maintained independently for each object type. For
example, VAV-3 uses AI:1 DAT, AI:2 Zone Temp, AI:3 Zone Humidity; its next AI
is AI:4. The current next unused number is shown below, but it is not approved
until the change workflow is completed.

AHU-1's SA Temperature Setpoint at AV:1 is a preserved, WebCTRL-mapped
exception. Duct-static setpoint AV:2, actual pressure AV:3, and supply-fan
speed feedback AV:4 are the next consecutive AHU values. Do not move any of
these points to the AV:80 sequence. The next unused AHU analog value is AV:5.
AHU sensor additions use AI:5 through AI:7, so the next AHU AI is AI:8.
Automatic safety/failure additions use BI:44 through BI:48, so the next AHU
BI is BI:49.

## Equipment offset blocks

Every equipment group owns a 1,000-instance numeric block.

| Group | Offset | Reserved numeric block | Current points |
|---|---:|---:|---:|
{chr(10).join(block_rows)}

The next unassigned equipment-group offset is **{next_offset:,}**. Allocate it
only for a genuinely new equipment group; do not consume it for points added
to an existing group.

## Current next-in-line register

| Group | Next AI | Next AO | Next AV | Next BI | Next BO | Next BV |
|---|---|---|---|---|---|---|
{chr(10).join(allocation_rows)}

## Required workflow for every new point

1. State the training purpose, equipment group, alias, object type, units,
   direction, writable/commandable behavior, min/max, normal range, COV
   increment, and relinquish default.
2. Use the next unused local instance for that object type in that equipment
   group. Do not fill an old gap or reuse a retired identifier.
3. Calculate the global instance and search every device config for the exact
   `(object type, global instance)` pair.
4. Confirm the alias is unique within the group and the BACnet object name is
   unique across the supervisory device.
5. Add the point in `scripts/generate_phase3_configs.py` when the group is
   generator-owned; regenerate `config/devices/*.json` rather than hand-editing
   generated output.
6. Wire the point into the physical equipment model or clearly label it
   monitor/scenario-only. Never publish a writable point that the model ignores.
7. Preserve the compatibility fingerprint for every previously verified
   identifier (currently 321), and assert the exact new identifier plus the
   new configured catalog total.
8. Regenerate `ACI_BACnet_Simulator_Point_Mapping.xlsx`, equipment sheets,
   this allocation register, and the building design plan.
9. Run the complete test suite, take a backup, restart once, then verify
   WebCTRL read/write/release/COV behavior.
10. Record the WebCTRL mapping work required before declaring the point live.

## Reserved VAV conventions

- VAV-1/2 retain physical zone sensors outside this simulator.
- Virtual VAV DAT is AI:1 and Zone Temp is AI:2.
- Zone Humidity is AI:3 on VAV-3 through VAV-15.
- Keep AI:3 reserved for Zone Humidity if it is later added to VAV-16/17.
- Damper command is AO:20, HW valve command AO:21, airflow setpoint AO:22,
  and measured airflow AV:80.
- Every VAV publishes read-only design/feedback telemetry in a consecutive AV
  sequence: heating minimum AV:81, heating maximum AV:82, cooling minimum
  AV:83, cooling maximum AV:84, and effective damper-position feedback AV:85.
- AV:81 through AV:84 use CFM; AV:85 uses percent. None is writable or
  commandable. The next available VAV analog-value number is AV:86.

## Verification

- Startup validation rejects duplicate aliases, object names, and global
  `(object type, instance)` collisions.
- The automated identifier fingerprint protects all identifiers that existed
  before the humidity expansion.
- [[Equipment Catalog]] is the current source-backed point inventory.
- [[BACnet Simulator Building Design Plan]] governs design intent and flow.
"""
    write(
        output,
        "04 BACnet Conformance/BACnet Point Allocation Standard.md",
        body,
    )


def scenario_catalog(repo: Path, output: Path) -> tuple[list[str], int]:
    rows: list[str] = []
    links: list[str] = []
    for path in sorted((repo / "config" / "scenarios").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        links.append(f"[[{data['title']}]]")
        event_times = [event.get("time_seconds", 0) for event in data.get("events", [])]
        duration = max(event_times, default=0)
        rows.append(
            f"| `{data['scenario_id']}` | {data['title']} | {len(data.get('events', []))} | {duration}s |"
        )
    body = frontmatter("scenario-catalog", "config/scenarios/*.json")
    body += f"""# Scenario Catalog

Six source-backed scenarios ship in `config/scenarios`. The running service
may also contain scenarios created through the AI workflow; those runtime
entries are not durable across a service restart unless exported into source
control. Starting a scenario replaces any currently running scenario and
clears its scenario-owned state.

| Scenario ID | Title | Events | Last event |
|---|---|---:|---:|
{chr(10).join(rows)}

## Operating rule

Preview the objectives, tell the class what normal state should look like,
then start the scenario from **Operations**. Use **Reset** after the exercise
and verify faults/forces are clear.

## Related

- [[07 Scenarios]]
- [[Instructor Guide]]
- [[Student Exercise Catalog]]
"""
    write(output, "07 Scenarios/Scenario Catalog.md", body)
    return links, len(rows)


def api_catalog(repo: Path, output: Path) -> None:
    source = (repo / "app" / "api.py").read_text(encoding="utf-8")
    endpoints = re.findall(
        r'@app\.(get|post|put|patch|delete)\("([^"]+)"\)',
        source,
    )
    rows = [
        f"| `{method.upper()}` | `{path}` | {'Read-only' if method == 'get' else 'State-changing'} |"
        for method, path in endpoints
    ]
    body = frontmatter("architecture", "app/api.py")
    body += f"""# API Catalog

The FastAPI dashboard API is intentionally bound to loopback at
`127.0.0.1:8001`. Do not publish it directly to the bench LAN or Internet.

| Method | Path | Class |
|---|---|---|
{chr(10).join(rows)}

## Safety boundaries

- Read endpoints may be used by the read-only Hermes probe.
- POST, PUT, PATCH, and DELETE endpoints require an operator workflow.
- `/api/llm/apply` requires a short-lived, one-time proposal token whose
  recorded bundle hash matches the submitted bundle.
- A remote agent should receive a narrow read-only gateway, never this
  complete API.

## Related

- [[LLM Guardrails]]
- [[Remote Agent Threat Model]]
- [[Ports Services and Firewall Matrix]]
"""
    write(output, "02 Architecture/API Catalog.md", body)


def test_catalog(repo: Path, output: Path) -> None:
    rows: list[str] = []
    total = 0
    for path in sorted((repo / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        count = len(re.findall(r"^def test_|^async def test_", text, flags=re.MULTILINE))
        total += count
        rows.append(f"| `{path.name}` | {count} |")
    body = frontmatter("test-evidence", "tests/")
    body += f"""# Automated Test Catalog

The live 329-point checkout passed **155 tests** on {VERIFIED}. The historical
321-point baseline passed 129 tests. The source contains
{total} directly declared test functions; parametrization can make the
executed case count higher.

| Test module | Declared tests |
|---|---:|
{chr(10).join(rows)}

Run from the repository root:

```powershell
venv\\Scripts\\python.exe -m pytest -q
```

The suite includes real BACnet/IP integration, COV delivery, peer-allowlist,
equipment interlocks, scenario mechanics, configuration validation, LLM
validation, and one-time proposal approval tests.
"""
    write(output, "10 Test Evidence/Automated Test Catalog.md", body)


def build_static_notes(output: Path) -> None:
    notes: dict[str, str] = {
        "Welcome.md": frontmatter("dashboard")
        + """# Test Bench BACnet Simulator Knowledge Base

This vault is the operating and design record for the ACI Building Automation
Training Center simulator.

## Start here

1. [[Training and Showcase Dashboard]] - current operating status and navigation
2. [[BACnet Simulator Building Design Plan]] - building, plant, air, flow, and dependency design
3. [[BACnet Point Allocation Standard]] - immutable point-number and next-in-line rules
4. [[VAV Design and Zone Schedule]] - every zone's area and airflow values
5. [[Equipment Catalog]] - complete 28-group / 329-object configured inventory
6. [[AHU Duct Static PID Lab]] - detailed AHU, safeties, process model, tuning, restart, and cutover plan
7. [[HVAC Realism and Parent Dependencies]] - how physical response is calculated
8. [[Instructor Guide]] - safe training operation
9. [[HVAC Realism Cutover Checklist]] - live acceptance and rollback evidence

> [!important] Before adding or changing a point
> Read [[BACnet Point Allocation Standard]]. Existing WebCTRL-mapped object
> identifiers are immutable. New points use the next unused local instance
> for their BACnet object type and equipment group.

## Knowledge map

```mermaid
flowchart TD
  W["Welcome"] --> D["Building Design Plan"]
  W --> P["Point Allocation Standard"]
  W --> O["Operations / Instructor Guide"]
  D --> V["VAV Design and Zone Schedule"]
  D --> E["Equipment Catalog"]
  D --> H["HVAC Realism and Dependencies"]
  D --> PID["AHU Duct Static PID Lab"]
  P --> E
  O --> T["Test Evidence and Cutover"]
```
""",
        "Simulator Device Goes Offline.md": frontmatter("redirect")
        + """# Simulator Device Goes Offline

This historical root note redirects to the maintained scenario note:
[[Simulator Device Comms Loss]].
""",
        "00 Dashboard/Training and Showcase Dashboard.md": frontmatter("dashboard")
        + """# Training and Showcase Dashboard

> [!success] Current release verified live — 2026-07-24
> `ACIBACnetSimulator` is running as an automatic Windows service. WebCTRL
> and the verified BACnet peers are communicating with device `242000` at
> **28 equipment groups / 329 BACnet objects**. The live cutover, WebCTRL
> writes/COV recovery, safety exercises, Restart cleanup, workbook, and vault
> merge are complete.

> [!success] Duct-static PID cutover complete
> AHU-1 AV:9002-9004, the Duct Static PID Lab, and the guarded Restart
> control are live. The service and GUI cold restarts both recovered WebCTRL
> writes/COV traffic with zero blocked messages, default PID tuning, and 1x.

> [!success] VAV point-exposure cutover complete
> All 85 read-only VAV AV:81-85 objects are live. Acceptance verified 85/85
> object allocations, 68/68 design values, command/feedback tracking, and
> cleanup to zero forces at 1x.

> [!success] VAV realism cutover complete
> The 105-test build is live. All 13 Zone Humidity AIs publish, WebCTRL
> traffic resumed, the engine tick advances, and the realism acceptance
> completed with zero remaining instructor overrides.

> [!success] VAV closed-damper behavior verified
> A 0% damper produces 1.0 CFM modeled leakage with AHU proof, and AHU proof
> off produces exactly 0.00 CFM. The focused live acceptance passed and
> returned the simulator to zero forces at 1x.

## Start here

- [[Current Status]] — authoritative project status
- [[BACnet Simulator Building Design Plan]] — governing building and flow design
- [[BACnet Point Allocation Standard]] — unique next-in-line point rules
- [[AHU Duct Static PID Lab]] — detailed airflow path, safety state machines, point contract, PID tuning, and restart
- [[Project MOC]] — project knowledge map
- [[Architecture MOC]] — how data and commands move
- [[HVAC Realism and Parent Dependencies]] — plant-to-zone physical behavior
- [[Equipment Catalog]] — 28 groups / 329 configured objects
- [[VAV Design and Zone Schedule]] — varied zone size, CFM, temperature, and humidity
- [[Scenario Catalog]] — instructor exercises
- [[Instructor Guide]] — safe class operation
- [[HVAC Realism Cutover Checklist]] — completed acceptance record and runbook
- [[Troubleshooting MOC]] — recovery paths
- [[LLM Operations MOC]] — local, cloud, and remote agent guidance
- [[Open Actions]] — remaining work

## Live endpoints

| Surface | Address | Exposure |
|---|---|---|
| Dashboard/API | `127.0.0.1:8001` | laptop only |
| BACnet/IP | `192.168.168.201:47808/UDP` | verified peers only |
| Ollama | `127.0.0.1:11434` | laptop user session only |

> [!danger] Never expose
> BACnet UDP 47808, Ollama 11434, SMB, raw PowerShell, or
> `/api/llm/apply` to the public Internet.
""",
        "00 Dashboard/Open Actions.md": frontmatter("action-register")
        + """# Open Actions

| Priority | Action | Owner | Verification |
|---|---|---|---|
| P1 | Discover/map AHU-1 AI:9005-9007 and BI:9044-9048 | WebCTRL programmer | Eight new read-only values and alarms visible |
| P1 | Refresh AV:9003 metadata | WebCTRL programmer | Actual-pressure maximum displays 10.00 in. H2O |
| P1 | Discover/map AHU-1 AV:9002, AV:9003, and AV:9004 | WebCTRL programmer | Setpoint write, actual pressure, and VFD feedback proven |
| P1 | Run the instructor damper-demand PID tuning exercise | Controls instructor | Opening/closing demand response captured with selected gains |
| P0 | Verify the installed Windows Firewall rule matches `.1-.7,.200` | Administrator | Rule scope and live traffic both correct |
| P1 | Perform a controlled reboot/autostart recovery test | Bench operator | Service and WebCTRL recover without manual launch |
| P1 | Rediscover/map VAV-3 through VAV-15 Zone Humidity AIs in WebCTRL as needed | WebCTRL programmer | AI:13003 through AI:25003 bound and trended |
| P1 | Review and prioritize [[HVAC Realism Feature Backlog]] | Project owner | Selected package and WebCTRL effort recorded |
| P1 | Add matching 8 GB RAM at minimum; prefer 2×16 GB | Hardware owner | Dual-channel memory and reduced paging |
| P1 | Keep Hermes state-changing simulator access disabled | Agent owner | Read-only probe only |
| P2 | Evaluate 3B/4B local agents only after RAM upgrade | Agent owner | 64K effective context proven, not advertised max |
| P2 | Split dashboard CSS/JS into offline-safe modules | Maintainer | Visual regression and 1024×768 checks pass |
""",
        "02 Project/Project Charter.md": frontmatter("project", "README.md")
        + """# Project Charter

## Purpose

Provide a deterministic, safe, instructor-controlled BACnet/IP building
simulation that WebCTRL can discover, bind, command, trend, alarm, and use
for training or demonstrations without physical HVAC equipment.

## Success criteria

- One supervisory BACnet device, instance `242000`
- 28 realistic equipment groups and 329 configured objects
- Verified WebCTRL reads, writes, polling, ConfirmedCOV, and UnconfirmedCOV
- Repeatable fault and scenario exercises
- Local dashboard remains usable at 1024×768
- No dependency on Internet access for core simulation
- State-changing AI actions remain previewed, validated, and operator-approved

## Non-goals

- Controlling physical equipment
- Exposing BACnet or simulator administration to the public Internet
- Allowing an autonomous agent to command the bench
""",
        "02 Project/Current Status.md": frontmatter(
            "status",
            "artifacts/live-vav-point-exposure-acceptance-20260723-195858",
        )
        + """# Current Status

> [!success] Current live release - 2026-07-24
> `ACIBACnetSimulator` is running at 28 equipment groups / 329 BACnet
> objects on device instance 242000. Every 321-point identifier is preserved.

> [!success] Duct-static PID and Restart acceptance complete
> The checkout and live service both contain 28 groups / 321 objects and the
> suite passes 129 tests. AHU-1 AV:9002-9004, the Duct Static PID Lab, and
> the guarded Restart control are live. WebCTRL writes and confirmed COV
> subscriptions recovered after both the service and GUI cold restarts.

> [!success] VAV point-exposure acceptance complete
> AV:81-85 is live on every VAV without changing any of the prior 233
> identifiers. The controlled restart and dedicated acceptance completed
> successfully on 2026-07-23.

| Check | Live result |
|---|---|
| Dashboard | `http://127.0.0.1:8001` |
| BACnet bind | `192.168.168.201:47808/UDP` |
| Engine | Running, 1x speed, advancing tick |
| Automated suite | 155 passed |
| Active instructor overrides after acceptance | 0 |
| Injected faults | 0 |
| Blocked BACnet messages | 0 at cutover handoff |
| Zone Humidity | 13 live AIs, AI:13003 through AI:25003 |
| Current COV recovery | Confirmed subscriptions renewed after service cutover; Restart now preserves the live BACnet object graph/COV subscriptions |
| Rollback backup | `artifacts/pre-vav-point-exposure-cutover-20260723-180858` |
| Acceptance evidence | `artifacts/live-ahu-command-center-acceptance-20260724.md` |
| Final COV evidence | `artifacts/live-vav-point-exposure-acceptance-20260723-195858/03-webctrl-cov-recovery.json` |
| AHU SAT evidence | `artifacts/live-ahu-sat-acceptance-20260723-173703` |
| VAV airflow evidence | `artifacts/live-vav-airflow-acceptance-20260723-174258` |
| Live VAV point package | 85 read-only AVs; local AV:81 through AV:85 on VAV-1 through VAV-17 |
| VAV point acceptance | `artifacts/live-vav-point-exposure-acceptance-20260723-195858` |
| Pre-cutover backup | `artifacts/pre-vav-point-exposure-cutover-20260723-180858` |

The acceptance proved upstream VAV inhibition, 15-second command/proof
failure, neutral ventilation, chilled-water cooling, hot-water reheat, and
complete instructor-priority rollback.

## Next operator work

- Discover/map AI:9005-9007 and BI:9044-9048; refresh AV:9003 maximum
  metadata to 10.00 in. H2O.
- Discover/map AHU-1 AV:9002 Duct Static Setpoint, AV:9003 Duct Static
  Pressure, and AV:9004 Supply Fan Speed Feedback.
- Run the instructor-selected VAV damper-demand PID tuning exercise and record
  the selected gains.
- Rediscover/map only the 85 new VAV AV objects in WebCTRL; existing bindings
  remain unchanged.
- Map/trend the new humidity AIs in WebCTRL where desired.
- Complete a controlled Windows reboot/autostart recovery test.
- Select the next package from [[HVAC Realism Feature Backlog]].
- Use [[BACnet Point Allocation Standard]] before adding any point.

## Related

- [[BACnet Simulator Building Design Plan]]
- [[Training and Showcase Dashboard]]
- [[HVAC Realism Cutover Checklist]]
""",
        "02 Project/Project MOC.md": frontmatter("moc")
        + """# Project MOC

- [[Project Charter]]
- [[BACnet Simulator Building Design Plan]]
- [[BACnet Point Allocation Standard]]
- [[Current Status]]
- [[Roadmap]]
- [[Risk Register]]
- [[Source Inventory]]
- [[Architecture MOC]]
- [[Windows Deployment]]
- [[Conformance Matrix]]
- [[WebCTRL Acceptance Test]]
- [[Training and Showcase Dashboard]]
""",
        "02 Project/Roadmap.md": frontmatter("roadmap")
        + """# Roadmap

## Operational baseline

- Preserve the verified live 329-point topology and all 321 prior identifiers.
- Run the complete suite and record its final count for every release.
- The checkout and service are verified live at 28 groups / 329 points. The
  2026-07-24 cutover recovered WebCTRL writes and confirmed COV traffic with
  zero blocked requests. Restart preserves the BACnet object graph and COV
  subscriptions while resetting model state, priorities, tuning, and time.
- The preceding 2026-07-23 point-exposure acceptance verified 85/85 new AV
  addresses, 68/68 VAV design-flow values, and 50 active COV subscriptions
  (29 confirmed / 21 unconfirmed) across `.2`, `.5`, `.6`, and `.7`.
- The 28/220 and 28/233 packages are preserved as historical accepted
  baselines; neither is the current service catalog.
- Complete a full reboot/autostart evidence check after this maintenance pass.

## Next reliability pass

- Add authenticated instructor approval or a local PIN for LLM apply.
- Add explicit read-only API/runtime profile for agents.
- Add service/Ollama dependency status to the dashboard.
- Add log pause, filters, export, and stable scroll.

## Hardware-dependent exploration

- Upgrade RAM to 32 GB before testing local Hermes at 64K effective context.
- Continue using local Hermes runtime with cloud inference on current hardware.
- Use a private overlay and read-only gateway if a remote agent is added.
""",
        "02 Project/Risk Register.md": frontmatter("risk-register")
        + """# Risk Register

| ID | Level | Risk | Current control | Next control |
|---|---|---|---|---|
| R-01 | High | Live topology overwritten by stale `.200`-only docs | Working checkout authoritative; docs corrected | Require live traffic capture before network edits |
| R-02 | High | Agent bypasses browser approval | One-time proposal token + bundle hash | Add instructor PIN and read-only runtime profile |
| R-03 | High | Public exposure of admin/API surfaces | Dashboard/Ollama on loopback | Private overlay plus narrow read-only gateway only |
| R-04 | Medium | Service starts before per-user Ollama | Core simulator independent | Show dependency status and document login behavior |
| R-05 | Medium | 8 GB RAM causes paging/model instability | Use cloud inference | Upgrade to 2×16 GB |
| R-06 | Medium | Firewall script/document scope drifts | Script now mirrors live allowlist | Verify installed rule after change |
| R-07 | Medium | Single HTML dashboard becomes hard to maintain | Regression screenshots/tests | Split CSS/JS in later pass |
| R-08 | Low | Port 8000 sends operator to unrelated service | All current launch docs use 8001 | Keep port in one environment setting |
| R-09 | Low | Rapid stop/start creates overlapping tick loops | Async lifecycle lock + cancel/await | Retain lifecycle regression test |
""",
        "02 Project/Source Inventory.md": frontmatter("source-inventory")
        + """# Source Inventory

| Source | Knowledge produced |
|---|---|
| `README.md`, `HANDOFF.md`, `DEVELOPMENT_HISTORY.md` | Charter, standing, chronology |
| `app/main.py`, `engine.py`, `registry.py`, `transport.py` | Runtime architecture |
| `app/equipment/*.py` | Mechanical behavior |
| `config/devices/*.json` | Equipment and point sheets |
| `config/scenarios/*.json` | Scenario catalog |
| `app/api.py` | API catalog and safety boundary |
| `app/llm/*`, `app/services/*`, `config/llm/*` | LLM policy and orchestration |
| `tests/*.py` | Conformance and regression evidence |
| `scripts/windows/*` | Install, service, firewall, recovery |
| Windows/WMI inspection | Laptop hardware and service inventory |

Large raw logs remain in the project log folder. The vault stores dated
summaries and evidence, not log copies.
""",
        "02 Architecture/System Architecture.md": frontmatter("architecture", "app/main.py")
        + """# System Architecture

```mermaid
flowchart LR
  W["WebCTRL and verified BACnet peers"] -->|"BACnet/IP UDP 47808"| T["BACpypes3 transport"]
  T --> R["Point registry — 329 configured objects"]
  R --> E["Deterministic equipment engine — 28 groups"]
  S["Scenario and fault engines"] --> E
  D["Local dashboard — 127.0.0.1:8001"] -->|"REST"| A["FastAPI"]
  A --> R
  A --> S
  O["Ollama hermes3:3b — user session"] -->|"structured proposal"| A
  H["Hermes Agent — cloud inference"] -->|"read-only local skill"| A
```

The simulator core does not require Ollama or Hermes. The Windows service can
start before a user logs in; Ollama and Hermes are per-user processes.
""",
        "02 Architecture/Architecture MOC.md": frontmatter("moc")
        + """# Architecture MOC

- [[System Architecture]]
- [[Simulation Data Flow]]
- [[HVAC Realism and Parent Dependencies]]
- [[Interactive Command Center]]
- [[BACnet Device Model]]
- [[BACnet Command and Priority Flow]]
- [[API Catalog]]
- [[Point Naming and Instance Allocation]]
- [[Remote Agent Architecture]]
""",
        "02 Architecture/Simulation Data Flow.md": frontmatter("architecture", "app/engine.py")
        + """# Simulation Data Flow

```mermaid
sequenceDiagram
  participant W as WebCTRL
  participant T as BACnet Transport
  participant R as Registry
  participant F as Fault/Scenario Engines
  participant E as Equipment Models
  W->>T: Read, Write, SubscribeCOV
  T->>R: Resolve object and priority
  F->>E: Apply timed conditions and mechanics
  E->>R: Publish deterministic point values
  R-->>T: Present value, status flags, COV change
  T-->>W: Response or notification
```

The engine ticks once per simulated second. Time-rate changes affect
simulation time and scenario timing, not wall-clock REST polling.
""",
        "02 Architecture/Interactive Command Center.md": frontmatter(
            "architecture", "config/building_layout.json"
        )
        + """# Interactive Command Center

The dashboard presents the simulated system as an isometric three-story
training building. Equipment markers are defined in
`config/building_layout.json`; the graphic is a navigation surface, while the
BACnet registry remains the source of truth.

## Diagnostic rules

| Equipment | Condition | Delay | Display |
|---|---|---:|---|
| Chillers, towers, pumps, boilers, AHU, EF | Command is on while proof/status is off | 15 wall-clock seconds | Red failure outline |
| VAV-1 through VAV-17 | Airflow is outside 75-125% of airflow setpoint | 15 wall-clock seconds | Red failure outline |

The timer uses real elapsed time, so changing simulation speed does not
shorten the operator's diagnostic window. A restored proof or in-range
airflow clears the timer.

When the AHU cannot provide air, VAV diagnostics are `inhibited` rather than
failed so an upstream outage does not create seventeen misleading terminal
alarms.

## Animated space air

Each VAV publishes a physical air-delivery snapshot. A subtle looping image
plume is blue for useful cooling, red for useful hot-water reheat, white/gray
for ventilation-only flow, and hidden with no proven delivery. The display is
derived from actual CFM, DAT, zone temperature, valve position, and parent
equipment; it is not an independent color command.

## Building pressure and exhaust

`ACI-SIM-SITE.building_pressure` publishes simulated building pressure in
inches of water column. The target training band is `0.03-0.10 in. w.c.`.
`ACI-SIM-EF-1.vfd_speed_command` represents the WebCTRL exhaust-fan VFD
signal. Occupied scheduling and normal fan enable remain WebCTRL
responsibilities. Increasing exhaust speed relieves building pressure created
by the AHU supply fan; it does not create positive pressure by itself.

## AHU command-center and duct-static PID training page

The left navigation includes a dedicated [[AHU Duct Static PID Lab]]. It shows
the WebCTRL fan command, simulated fan proof, AV:9002 pressure setpoint,
AV:9003 actual pressure, AV:9004 supply-fan VFD feedback, and the
design-CFM-weighted feedback of all 17 VAV dampers. Editable P, I, D, and
calculation interval controls drive an actual-versus-setpoint trend and an
animated detailed AHU and two-thirds-main-trunk sensor graphic.

At fixed fan speed, opening VAV dampers lowers static pressure and closing
dampers raises it. The direct-acting PID increases VFD speed as opening
terminal demand pulls pressure below setpoint. When fan command and proof are
not both present, the process and trend are exactly zero.

## Layout boundary

The equipment placement is a representative BAS training layout informed by
common office-building arrangements. It is not a construction, life-safety,
or mechanical-design drawing.

## Related

- [[Building Pressure and Exhaust Control]]
- [[HVAC Realism and Parent Dependencies]]
- [[Equipment Catalog]]
- [[BACnet Command and Priority Flow]]
""",
        "02 Project/AHU Duct Static PID Lab.md": frontmatter(
            "design-plan", "docs/DUCT_STATIC_PID_LAB.md; app/equipment/ahu.py"
        )
        + """# AHU Command Center and Duct Static PID Lab

> [!success] Current release verified live
> The checkout and Windows service contain 28 groups / 329 objects. The
> 2026-07-24 cutover and safety acceptance are complete.

## BACnet point contract

| Local/global object | Alias | Direction | Units | Range/default |
|---|---|---|---|---|
| `AV:2` / `AV:9002` | `duct_static_pressure_setpoint` | WebCTRL to simulator | in. H2O | 0.25-2.00 / 1.00 |
| `AV:3` / `AV:9003` | `duct_static_pressure` | Simulator to WebCTRL | in. H2O | 0.00-10.00 |
| `AV:4` / `AV:9004` | `sa_fan_speed_feedback` | Simulator to WebCTRL | percent | 0-100 |
| `AI:5` / `AI:9005` | `ahu_ma_humidity` | Simulator to WebCTRL | percent RH | 0-100 |
| `AI:6` / `AI:9006` | `ahu_sa_humidity` | Simulator to WebCTRL | percent RH | 0-100 |
| `AI:7` / `AI:9007` | `cooling_coil_entering_air_temp` | Simulator to WebCTRL | degrees F | -20 to 120 |
| `BI:44` / `BI:9044` | `automatic_high_static_trip` | Simulator to WebCTRL | no units | inactive/active |
| `BI:45` / `BI:9045` | `duct_structural_failure` | Simulator to WebCTRL | no units | inactive/active |
| `BI:46` / `BI:9046` | `automatic_freezestat_trip` | Simulator to WebCTRL | no units | inactive/active |
| `BI:47` / `BI:9047` | `cooling_coil_freeze_condition` | Simulator to WebCTRL | no units | inactive/active |
| `BI:48` / `BI:9048` | `cooling_coil_rupture_flood` | Simulator to WebCTRL | no units | inactive/active |

All 321 earlier identifiers are preserved. The next unused AHU AI is AI:8,
AV is AV:5, and BI is BI:49. P/I/D/interval settings remain
dashboard/API-only.

## Sensor and physical relationship

The training sensor is a conceptual two-thirds station on a straight common
trunk before a summarized VAV terminal bank. The bank represents aggregate
demand; it is not a literal claim that every physical takeoff is downstream.

At fixed fan speed:

- closing VAV dampers reduces relief and raises duct static pressure;
- opening VAV dampers increases relief and lowers pressure; and
- the direct-acting PID increases fan speed when pressure falls below the
  WebCTRL setpoint.

Aggregate relief is weighted by each VAV's configured design maximum CFM, so
a large zone has more influence than a small zone at the same damper position.

## Graphic device order

Outside air enters from the left through the economizer and prefilter.
Return-air temperature/humidity and smoke are upstream of the return fan in
the top return duct. The streams meet at the mixing plenum with MA
temperature/humidity, followed by preheat, a downstream serpentine
freezestat element, cooling coil, reheat coil, supply fan, SA
temperature/humidity and smoke, the high-static switch, a jagged duct break,
and the two-thirds duct-static sensor.

## Safety state machines

- A healthy high-static switch latches at 4.0 in. H2O and stops both fans.
- Only the restricted instructor safety-bypass/failure mechanic permits
  pressure above the representative 5.0-in. H2O training duct-class limit.
  Structural failure then latches, with damaged-duct and AHU red-flash
  graphics.
- A healthy freezestat latches protective fan/OA/cooling/heating response.
- With the freezestat bypassed, cooling-coil entering air below 32 F uses a
  20-simulated-minute timer without useful CHW flow or 60 simulated minutes
  with cooling valve open and CHW flow proven.
- Freeze and burst/flood latch. Clearing the bypass does not erase the
  consequence; guarded Restart is the manual-reset boundary.
- Five inches is a representative configured training limit, not a universal
  duct rupture rating. The freeze timers are training approximations, not an
  engineering freeze calculation.

## Economizer availability

`AO:9023` remains the raw WebCTRL request. The simulator independently
calculates an effective position, so a locked command remains visible while
unsuitable outside air is limited.

- Dual enthalpy is preferred: enable at OA minus RA enthalpy `<= -1 Btu/lb`
  and disable at `>= +1 Btu/lb`.
- OA dry bulb is limited to 75 F and OA dew point uses 55/57 F
  enable/disable limits.
- Sensor fallback is dual enthalpy, single enthalpy (28 +/- 1 Btu/lb),
  differential dry bulb (65/67 F), fixed dry bulb, then unavailable when
  OAT is unreliable.
- Unsuitable weather returns to the 15% operating ventilation minimum.
  Fan-off, hard-safety, and mixed air below 45 F close OA fully; release is
  47 F.
- After 180 simulated seconds at >=95% effective stroke with SAT above
  setpoint, integrated mechanical cooling is allowed.

The command center shows requested/effective position, suitability, method,
OA/RA enthalpy, delta, OA dew point, mixed-air limit, proof, integrated
state, limiting reason, and FDD flags. These are computed diagnostics, not
new BACnet objects.

## Default controller calibration

| Setting | Default | Adjustable range |
|---|---:|---:|
| P | 30.0 | 0-100 |
| I | 0.25 | 0-1 |
| D | 0.0 | 0-20 |
| Calculation interval | 1.0 s | 0.5-10 s |
| Output bias | 55% | fixed |
| Minimum proven speed | 25% | fixed |
| Pressure deadband | 0.01 in. H2O | fixed |
| Output slew | 3% per simulated second | fixed |

The PID uses derivative on measured pressure, filtering, integral anti-windup,
and output slew. Fan, duct, and sensor time constants prevent instantaneous
response. Fan command and proof must both be present; otherwise pressure,
speed, and the trend are exactly zero.

## Dashboard workflow

1. Confirm WebCTRL supply-fan command and simulated proof.
2. Observe AV:9002 setpoint, AV:9003 actual, AV:9004 VFD feedback, and
   aggregate VAV demand.
3. Change terminal damper demand and compare the pressure transient with the
   PID speed response.
4. Adjust P, I, D, or calculation interval and use the trend to evaluate
   rise time, overshoot, hunting, and steady-state error.
5. Use **Reset Loop** to clear integral/derivative memory or **Defaults** to
   restore the training calibration. Neither clears a latched safety.
6. Use Operations to compare healthy high-static/freezestat behavior with a
   deliberate safety-bypass lesson, then use Restart between lessons.

## Restart control

The guarded command-bar Restart action stops the engine, resets scenarios,
drains instructor priority writes, clears faults and all command priorities,
resets reliability, recreates equipment/PID/safety state, restores 1x and
zero elapsed time, announces I-Am, and restarts the engine. The HTTP
dashboard and BACnet object graph remain online so existing COV
subscriptions stay attached.

## Release acceptance and remaining WebCTRL work

- [x] Run the complete suite: 155 passed for the live 329-point checkout
- [x] Back up checkout, workbook, and live vault
- [x] Rebuild and inspect the 329-point workbook and vault staging pack
- [x] Obtain operator approval for the administrator restart
- [x] Verify fresh `/api/status` at 28 groups / 329 points
- [ ] Discover/map AI:9005-9007 and BI:9044-9048; refresh AV:9003 metadata
- [x] Exercise healthy and bypassed high-static/freezestat lessons
- [ ] Discover/map AV:9002, AV:9003, and AV:9004 in WebCTRL
- [ ] Verify pressure/VFD response to opening and closing VAV demand
- [x] Exercise Restart and verify the BACnet session/COV subscriptions are preserved
- [x] Leave 1x, zero faults, zero forces, no scenario, and zero blocked messages

## Related

- [[BACnet Simulator Building Design Plan]]
- [[BACnet Point Allocation Standard]]
- [[Interactive Command Center]]
- [[HVAC Realism and Parent Dependencies]]
- [[HVAC Realism Cutover Checklist]]
""",
        "02 Architecture/HVAC Realism and Parent Dependencies.md": frontmatter(
            "architecture", "docs/HVAC_REALISM_MODEL.md"
        )
        + """# HVAC Realism and Parent Dependencies

WebCTRL remains command authority. The simulator models the physical
response and does not overwrite airflow, damper, valve, plant-enable, or
reset commands.

AHU-1 exposes one writable `SA Temperature Setpoint` at BACnet
`analog-value:9001` for both cooling and heating. WebCTRL owns that setpoint
and both coil-valve commands; the simulator turns the actual commands and
available plant conditions into a physical supply-air response.

## Dependency matrix

| Result | Required parent state | Unavailable behavior |
|---|---|---|
| AHU cooling | Proven chilled-water capacity and usable CHWS | Cooling-coil command cannot create cooling |
| AHU heating | Proven hot-water capacity and usable HWS | Heating-coil command cannot create heat |
| AHU economizer | Reliable OA/RA conditions, suitable enthalpy, cooling benefit, and no safety/low-limit override | Raw WebCTRL request remains visible while effective position is limited |
| Duct static | AHU supply-fan command/proof, VFD speed, and design-CFM-weighted VAV damper relief | Pressure and VFD feedback are exactly zero while inactive |
| High-static safety | Healthy automatic switch | 4.0-in. H2O trip latches and stops both fans |
| Freezestat protection | Healthy automatic low-temperature cutout | Protective fan/OA/cooling/heating response latches |
| VAV airflow | AHU proof, static pressure, damper opening, and airflow target | AHU off forces exact zero; a 0% damper leaves only 1.0 CFM leakage when the AHU is proven |
| VAV reheat | Proven hot-water distribution and usable HWS | Valve command cannot create heat |
| Zone temperature | Actual airflow/DAT, thermal mass, envelope, infiltration, solar, internal load, and mixing | Zone follows physical loads instead of drifting to fixed 72 F |
| Zone humidity | Supply/OA moisture, infiltration, mixing, people, and moisture storage | RH changes slowly; reheat changes dry bulb but not moisture ratio |

## Training ranges

- CHWS reset: 38-54 F; nominal 44 F
- AHU cooling SAT: approximately 52-58 F with nominal chilled water
- AHU SAT setpoint: one AV, 45-95 F, 55 F relinquish default
- AHU duct-static setpoint: AV:9002, 0.25-2.00 in. H2O, 1.00 default
- AHU duct-static actual: AV:9003, 0.00-10.00 in. H2O; supply-fan speed feedback: AV:9004
- AHU humidity/coil-entering sensors: AI:9005 mixed-air RH, AI:9006
  supply-air RH, AI:9007 cooling-coil entering air
- High-static training: 4.0-in. H2O healthy trip; representative 5.0-in.
  H2O duct-class failure only with explicit safety bypass
- Freezestat training: 20 simulated minutes below 32 F without useful CHW
  flow or 60 simulated minutes with cooling valve open and flow proven,
  only when the automatic safety is explicitly bypassed
- AHU heating: 48-52% valve settles near 85 F at normal OA; colder OA needs more valve
- HWS reset: 100-200 F; nominal 180 F
- VAV discharge-air cap: 95 F
- Virtual-zone area: 600-2,400 square feet
- VAV cooling maximum: 400-2,120 CFM
- Closed VAV damper: 1.0 CFM leakage with AHU proven; exactly 0.00 CFM
  whenever the AHU is stopped or unproven
- Zone setpoints: configurable; default 70 F heating / 72 F cooling

The analytical zone heat balance uses
`1.08 x actual CFM x (DAT - zone temperature)` and exact first-order
integration, so it remains stable at accelerated simulator speeds. VAV-3
through VAV-15 publish slowly changing Zone Humidity AIs at local AI:3.
See [[VAV Design and Zone Schedule]] for every configured space.

## Economizer suitability

AO:9023 remains the raw WebCTRL request. The effective stroke uses dual
enthalpy first (enable OA minus RA <= -1 Btu/lb; disable >= +1), with a
75-F OAT ceiling and 55/57-F OA dew-point limits. Reliability fallback is
single enthalpy at 28 +/- 1 Btu/lb, differential dry bulb at 65/67 F, fixed
dry bulb, then unavailable if OAT is unreliable.

Unsuitable OA means 0% effective economizer stroke and the normal 15%
ventilation minimum while the fan is proven. Fan-off, hard safety, and mixed
air below 45 F close OA fully; release is 47 F. After 180 simulated seconds
at >=95% effective stroke with SAT above setpoint, integrated mechanical
cooling is allowed. The command center shows psychrometrics, requested versus
effective position, state, limiting reason, proof, and FDD flags. These are
computed diagnostics and do not add BACnet points.

## Air-delivery colors

| Mode | Meaning |
|---|---|
| Blue | Active airflow and useful cooling from mechanical or economizer source |
| Red | Active airflow and useful heat with hot-water distribution available |
| White/gray | Active ventilation without material conditioning |
| Off | No proven/meaningful airflow |

The active threshold is the greater of 50 CFM or 10% of design flow. A
2-degree-F temperature difference plus hysteresis prevents color chatter.

## Simultaneous heating and cooling

Material AHU cooling/heating-valve overlap is an energy-waste diagnostic.
A real cross-ramp gets one actuator-travel window. If both commands remain
above 10%, the AHU enters a 15-real-second diagnostic timer and then outlines
red with both valve commands and a WebCTRL priority-lock warning.

## Verification boundary

The checkout and Windows service contain 28 groups / 329 points and pass 155
automated tests. The 2026-07-24 controlled cutover and live acceptance are
complete. WebCTRL writes and confirmed COV traffic recovered with zero
blocked requests; guarded Restart preserves the BACnet session/COV
subscriptions while clearing model and training state.
The preceding 318-point acceptance verified 50 active COV subscriptions
(29 confirmed / 21 unconfirmed), upstream inhibition, cooling, heating,
ventilation, UI air states, and full override rollback.

The final closed-damper rule is live and verified: 1.0 CFM modeled leakage
at 0% with AHU proof and exact zero without proof.

See the source document `docs/HVAC_REALISM_MODEL.md` for sequence guidance,
reference links, and the remaining realism backlog. See
[[AHU Duct Static PID Lab]] for the 329-point release checklist and the
historical 321-point cutover.

## Related

- [[Interactive Command Center]]
- [[ADR-002 WebCTRL Command Authority]]
- [[Equipment Catalog]]
""",
        "02 Project/HVAC Realism Feature Backlog.md": frontmatter(
            "roadmap", "docs/HVAC_REALISM_MODEL.md"
        )
        + """# HVAC Realism Feature Backlog

This list is the result of a two-pass adversarial simulation audit on
2026-07-23. It separates existing point-contract defects from optional
features so WebCTRL work can be chosen deliberately.

## Staged cutover corrections already complete

- VAV-11 airflow metadata covers its 2,120-CFM design value.
- Analog commands publish and enforce configured BACnet bounds.
- Stop/reset relinquishes tracked priority-3 overrides and exposes them as
  forced points.
- Virtual zones retain a physical temperature independent of sensor faults.
- VAV sizing and physical parameters receive cross-field validation.

## Existing points to make functional first

| Priority | Work | New BACnet points | WebCTRL work |
|---|---|---:|---|
| 1 | Wire chiller tower VFD, bypass, reset, power-loss, and VFD-fault behavior | None | None/low |
| 2 | Aggregate VAV zone air into real AHU return temperature and humidity | None | None |
| 3 | Feed AHU and terminal coil loads into CHW/HW return temperatures and capacity | None initially | None initially |
| 4 | Add per-equipment last-good tick, error count, and degraded health | None | None |

## Highest-value optional training packages

| Package | Training value | Simulator effort | New points | WebCTRL burden |
|---|---:|---:|---:|---:|
| Fan/duct network with SA VFD, static pressure, leakage, and trim/respond | Very high | Large | About 5 | High |
| Operating modes: occupied, standby, warm-up, cooldown, setup/setback | Very high | Medium | One MSV or several BVs | High |
| Zone CO2, population diversity, and demand-controlled ventilation | Very high | Large | CO2 and occupancy by selected zone | High |
| CHW variable flow, differential pressure, tons, low-delta-T, and staging limits | Very high | Large | Medium set | Medium/high |
| HW return temperature, firing rate, outdoor lockout, and short-cycle protection | Very high | Large | Medium set | Medium/high |
| OA/RA/relief/exhaust mass balance, DCV, and climate-specific economizer profiles | Very high | Large | About 6-9 | Medium/high |
| Internal weather, orientation, solar, adjacent-zone, and plenum coupling | High | Medium/large | None | None |
| Fault degradation: leakage, stiction, fouling, filter loading, sensor noise | High | Medium | None initially | None/low |
| Energy and comfort KPIs in the command center | Very high | Medium | None initially | None |
| Automatically scored, deterministic training scenarios | Very high | Medium | None | None |

## Guardrails

- Positive building pressure comes from net outdoor/supply air exceeding
  return, relief, and exhaust. EF-1 can trim excess pressure; it cannot
  create positive pressure.
- Keep WebCTRL as command authority. Internal physics may calculate
  consequences but must not overwrite BAS command points.
- Do not invent smoke-control, refrigerant emergency, flame-safeguard, or
  other life-safety sequences. Use approved project/AHJ training sequences
  and label BAS indications separately from hardwired safeties.
- Keep the single BACnet-device topology unless a separate remapping project
  is explicitly approved.
- Keep the dashboard loopback-only until authentication or an authenticated
  VPN/reverse proxy is designed for remote-agent access.

## References

- [ASHRAE Guideline 36 addenda](https://www.ashrae.org/technical-resources/standards-and-guidelines/guideline-36-high-performance-sequences-of-operation-for-hvac-systems)
- [DOE EnergyPlus zone and air-system integration](https://bigladdersoftware.com/epx/docs/24-1/engineering-reference/basis-for-the-zone-and-air-system-integration.html)
- [DOE BOPTEST](https://www.energy.gov/cmei/buildings/boptest-building-operations-testing-framework)
- [NFPA 92](https://link.nfpa.org/all-publications/92/2024)

## Related

- [[HVAC Realism and Parent Dependencies]]
- [[VAV Design and Zone Schedule]]
- [[Interactive Command Center]]
- [[Risk Register]]
""",
        "02 Architecture/BACnet Device Model.md": frontmatter("architecture", "config/supervisory_device.json")
        + """# BACnet Device Model

| Attribute | Value |
|---|---|
| Device name | `ACI-SIM-SUPERVISOR` |
| Device instance | `242000` |
| Vendor identifier | `999` |
| Bind | `192.168.168.201:47808/UDP` |
| Groups | 28 |
| Configured objects | 329 |
| Last verified live baseline | 329 |

All equipment objects live under one supervisory BACnet device. Group IDs and
instance offsets provide human and numeric separation without creating
multiple BACnet device applications.

See [[BACnet Point Allocation Standard]], [[Point Naming and Instance Allocation]],
and [[Equipment Catalog]].
""",
        "02 Architecture/BACnet Command and Priority Flow.md": frontmatter("architecture", "app/registry.py")
        + """# BACnet Command and Priority Flow

```mermaid
flowchart TD
  W["Allowlisted BACnet write"] --> V{"Object exists and is writable?"}
  V -- no --> X["Reject and log"]
  V -- yes --> P["BACnet priority array"]
  P --> C["Effective commanded value"]
  C --> F["Fault/force boundary"]
  F --> E["Equipment model response"]
  E --> O["Published outputs, status and COV"]
```

- WebCTRL remains command authority.
- A release removes the selected priority value; it does not force zero.
- Manual forces and scenarios must be cleared/reset after training.
- Agent access is read-only unless an instructor explicitly approves through
  the local operator workflow.
""",
        "02 Architecture/Point Naming and Instance Allocation.md": frontmatter("architecture", "config/devices/")
        + """# Point Naming and Instance Allocation

Each equipment config defines:

- `group_id` — stable equipment scope used by the API and fault engine
- `alias` — stable programmatic point name
- `object_name` — display name seen by BACnet clients
- local `object_instance` — added to the group's `instance_offset`

The resulting object identifier is `object_type,(instance_offset +
object_instance)`. Global uniqueness is validated at startup and by tests.
Never renumber objects in the live build without a WebCTRL binding-impact
review.

The authoritative next-in-line register and change workflow are maintained in
[[BACnet Point Allocation Standard]].
""",
        "03 Deployment/Test Bench Network.md": frontmatter("deployment", "config/network.json")
        + """# Test Bench Network

> [!danger] Authoritative live configuration
> Do not narrow this list from older `.200`-only documentation.

| Role | Address |
|---|---|
| Simulator BACnet/IP | `192.168.168.201:47808/UDP` |
| Verified BACnet peers | `192.168.168.1`–`192.168.168.7`, `192.168.168.200` |
| WebCTRL BACnet connection | `.200`, using UDP `47809` |
| Dashboard/API | `127.0.0.1:8001` |
| Ollama | `127.0.0.1:11434` |

`peer_allowlist` and `write_source_allowlist` contain the same eight verified
source addresses. Unknown sources are silently dropped and counted.
""",
        "03 Deployment/Windows Deployment.md": frontmatter("deployment", "PACKAGING.md")
        + """# Windows Deployment

| Item | Current state |
|---|---|
| Service | `ACIBACnetSimulator` |
| Startup | Automatic |
| Account | LocalSystem |
| Application | `venv\\Scripts\\python.exe -m app.main` |
| Working directory | `C:\\bacnet_simulator-main` |
| Dashboard | `127.0.0.1:8001` |
| Service logs | `logs\\service_stdout.log`, `logs\\service_stderr.log` |

The current virtual environment points to the compatible Python 3.11.7
runtime bundled with pgAdmin. Preserve or replace that runtime deliberately
before removing pgAdmin.

See [[Startup and Recovery Dependencies]] and [[Backup Restore and Upgrade Runbook]].
""",
        "03 Deployment/HVAC Realism Cutover Checklist.md": frontmatter(
            "deployment", "docs/REALISM_CUTOVER_CHECKLIST.md"
        )
        + """# HVAC Realism Cutover Checklist

The controlled 329-point cutover completed successfully on 2026-07-24.
The checkout and Windows service are verified at 28/329.

## Live addendum — AHU command center and safeties (28/329)

- [x] Preserve all 321 prior identifiers and add only AI:9005-9007 and
  BI:9044-9048
- [x] Confirm AV:9003 keeps its identifier and publishes 0.00-10.00 in. H2O
- [x] Verify graphic device order, component animation, and readable sensor
  values at 1024x768 and 1920x1080
- [x] Verify dual-enthalpy economizer suitability, requested/effective
  position, sensor fallbacks, mixed-air low limit, and integrated proof
- [x] Verify healthy 4.0-in. H2O high-static trip before the representative
  5.0-in. H2O training duct-class limit
- [x] Verify restricted bypass permits the structural-failure animation and
  latched alarm
- [x] Verify healthy freezestat protection and bypassed 20/60-simulated-minute
  below-freezing exposure paths
- [x] Verify Restart clears latched safety/failure state and all training
  state
- [x] Run the complete test suite: 155 passed
- [x] Rebuild/inspect workbook and generate/review vault staging
- [x] Back up the live vault, checkout, and workbook
- [x] Merge only reviewed generator-owned notes without replacing
  `.obsidian/**` or live-only notes
- [x] Obtain UAC approval; restart; verify 28/329, advancing ticks, WebCTRL/COV
  recovery, zero faults/forces/scenario/blocked traffic, and 1x handoff
- [ ] Discover/map AI:9005-9007 and BI:9044-9048; refresh AV:9003 metadata

## Live addendum — AHU duct-static PID and Restart

- [x] Checkout and service validate at 28 groups / 321 objects
- [x] All 318 previously live identifiers remain unchanged
- [x] AHU-1 AV:9002 setpoint, AV:9003 actual pressure, and AV:9004 fan-speed
  feedback are live
- [x] 129 automated tests pass
- [x] Point-mapping workbook and Obsidian design pack regenerated
- [x] Fresh project and full live-vault backups created
- [x] Operator approved the Windows UAC service restart
- [x] Fresh `/api/status` returned 28 groups / 321 points at 1x
- [x] AHU-1 command/proof enabled the PID and pressure tracked 1.000 in. H2O
- [x] GUI Restart restored default tuning, cleared training state, and rebound
  BACnet
- [x] WebCTRL writes and confirmed COV subscriptions recovered with zero
  blocked traffic
- [ ] Discover/map AV:9002, AV:9003, and AV:9004 in WebCTRL
- [ ] Run the selected VAV damper-demand PID tuning exercise

## Live addendum — VAV design limits and damper feedback

- [x] Checkout validates at 28 groups / 318 objects
- [x] All 233 previously live identifiers remain unchanged
- [x] Exactly 85 read-only points are allocated at AV:81 through AV:85 on
  VAV-1 through VAV-17
- [x] AV:81/82 publish heating minimum/maximum CFM
- [x] AV:83/84 publish cooling minimum/maximum CFM
- [x] AV:85 publishes effective damper-position feedback independently of
  WebCTRL damper command AO:20
- [x] 107 automated tests pass, including transport rejection of writes to
  all five new VAV objects
- [x] Point-mapping workbook and vault documentation regenerated
- [x] Fresh project and live-vault backup:
  `artifacts/pre-vav-point-exposure-cutover-20260723-180858`
- [x] Obtain operator approval for the Windows UAC service restart
- [x] Verify live `/api/status` returns 28 groups / 318 points
- [x] Run `scripts/live_vav_point_exposure_acceptance.ps1`
- [x] Verify WebCTRL writes and 50 active COV subscriptions recover with zero
  blocked traffic: 29 confirmed and 21 unconfirmed across `.2`, `.5`, `.6`,
  and `.7`
- [ ] Discover/map only the 85 new read-only AVs in WebCTRL

Evidence:
`artifacts/live-vav-point-exposure-acceptance-20260723-195858`.

Final COV evidence:
`artifacts/live-vav-point-exposure-acceptance-20260723-195858/03-webctrl-cov-recovery.json`.

## Historical accepted addendum — VAV diversity and humidity (28/233)

- [x] 105 automated tests pass
- [x] Configured catalog validates at 28 groups / 233 points
- [x] All 220 previous BACnet identifiers remain unchanged
- [x] Zone Humidity is allocated only at VAV-3 through VAV-15 AI:3
- [x] Point-mapping workbook and vault staging were regenerated
- [x] Fresh project/vault backup:
  `artifacts/pre-vav-realism-cutover-20260723-170746`
- [x] Reviewed administrator restart completed
- [x] Live API reports 28/233 and an advancing engine tick
- [x] All 13 humidity AIs publish at AI:13003 through AI:25003
- [x] WebCTRL writes resumed from verified peers with zero blocked messages
- [x] Live realism acceptance passed all eight checks and released all forces
- [x] Reload the final closed-damper correction with one administrator restart
- [x] Run `scripts/live_vav_airflow_acceptance.ps1`

VAV airflow evidence:
`artifacts/live-vav-airflow-acceptance-20260723-174258`.

Evidence: `artifacts/live-realism-acceptance-20260723-170906`.

## Historical staged 28/220 package

- 92 automated tests pass
- 28 groups / 220 points remain configured
- 41 JSON files parse
- `static/app.js` passes syntax validation
- Point-mapping workbook and Obsidian staging are regenerated

## Historical 28/220 controlled cutover

1. Confirmed no class, demo, fault, or scenario was active.
2. Captured current status, forces/faults, and COV summary.
3. Backed up the project and live vault to
   `artifacts/pre-cutover-20260723-133633`.
4. Used the reviewed administrator service script for one controlled restart.
5. Verified `/api/status` and `/api/command-center`.
6. Confirmed AHU-off VAV inhibition, 52-58 F cooling SAT, 88-95 F reheat DAT,
   and all four air-delivery modes.
7. Verified WebCTRL reads/writes, priority release, and 26 active COV
   subscriptions with zero blocked requests.

## Rollback

If the service does not recover cleanly, stop after one attempt. Restore the
reviewed current-package backup at
`artifacts/pre-vav-point-exposure-cutover-20260723-180858`, then rerun the
318-point acceptance. A deliberate catalog downgrade requires a separately
reviewed change plan; do not silently return to an older 219-, 220-, or
233-point catalog. Do not change device instance `242000`, UDP `47808`, or the
verified peer allowlists as a troubleshooting shortcut.

The detailed source checklist is `docs/REALISM_CUTOVER_CHECKLIST.md`.

## Related

- [[Windows Deployment]]
- [[Backup Restore and Upgrade Runbook]]
- [[HVAC Realism and Parent Dependencies]]
""",
        "03 Deployment/Laptop Hardware Profile.md": frontmatter("hardware")
        + """# Laptop Hardware Profile

| Component | Verified result |
|---|---|
| System | Lenovo ThinkPad L14 Gen 4 AMD, type `21H6S0A900` |
| OS | Windows 11 Pro 64-bit, build `26200` |
| BIOS | Lenovo `R25ET48W (1.29)`, 2026-03-19 |
| CPU | Ryzen 3 PRO 7330U, 4 cores / 8 threads, AVX2 |
| RAM | One 8 GB DDR4-3200 SO-DIMM; 6.80 GiB usable |
| Audit memory pressure | ~0.98 GiB free; pagefile already active |
| GPU | Integrated AMD Radeon; no NVIDIA GPU |
| Storage | WD PC SN740 256 GB NVMe; ~96 GB free |
| Wired network | Realtek GbE, negotiated 100 Mbps during audit |
| Virtualization | Firmware virtualization enabled; hypervisor present |
| Displays | Physical/current report 1024×768; TeamViewer virtual 1920×1080 |

## Upgrade guidance

Lenovo documents two SO-DIMM slots and up to 64 GB. Add a matching second
8 GB module as the minimum improvement; **2×16 GB DDR4-3200** is the
recommended target for local agent experimentation.

Sources: [Lenovo PSREF](https://psref.lenovo.com/syspool/Sys/PDF/ThinkPad/ThinkPad_L14_Gen_4_AMD/ThinkPad_L14_Gen_4_AMD_Spec.pdf) and
[AMD Ryzen 3 PRO 7330U](https://www.amd.com/en/products/processors/laptop/ryzen/7000-series/amd-ryzen-3-7330u.html).
""",
        "03 Deployment/Software and Runtime Inventory.md": frontmatter("deployment")
        + """# Software and Runtime Inventory

| Runtime | Version / state |
|---|---|
| Project Python | 3.11.7 |
| FastAPI | 0.139.2 |
| Uvicorn | 0.51.0 |
| BACpypes3 | 0.0.106 |
| Pydantic | 2.13.4 |
| HTTPX | 0.28.1 |
| Ollama | 0.32.1, running |
| Hermes Agent | v0.18.2 |
| WSL | Binary present; no distribution installed |
| Docker | Not installed |

Installed Ollama models:

- `hermes3:3b` — 2.0 GB Q4_K_M; simulator model
- `qwen3:4b-instruct` — 2.5 GB Q4_K_M
- `qwen3.6:latest` — 23 GB; not viable on current RAM

Do not remove a model without operator approval.
""",
        "03 Deployment/Ports Services and Firewall Matrix.md": frontmatter("deployment")
        + """# Ports Services and Firewall Matrix

| Port | Protocol | Bind | Purpose | Allowed exposure |
|---|---|---|---|---|
| 47808 | UDP | `192.168.168.201` | BACnet/IP simulator | `.1-.7,.200` only |
| 47809 | UDP | WebCTRL `.200` | WebCTRL BACnet connection | Bench only |
| 8001 | TCP | `127.0.0.1` | Dashboard/FastAPI | Laptop only |
| 11434 | TCP | `127.0.0.1` | Ollama | Laptop user session only |
| 8000 | TCP | system HTTPAPI | Unrelated service | Do not use for simulator |

The bundled firewall script mirrors the verified BACnet allowlist. Verify the
installed Windows rule separately; changing a script does not update an
already-installed rule.
""",
        "03 Deployment/Startup and Recovery Dependencies.md": frontmatter("deployment")
        + """# Startup and Recovery Dependencies

```mermaid
flowchart TD
  B["Windows boot"] --> S["LocalSystem simulator service"]
  L["User login"] --> O["Ollama user process"]
  L --> H["Hermes user process when launched"]
  S --> C["BACnet core + dashboard"]
  O --> A["AI Console available"]
  H --> R["Read-only simulator probe + Obsidian"]
```

The BACnet simulator must remain healthy when Ollama or Hermes is absent.
After boot, verify the service and WebCTRL first; then verify Ollama/AI
status after user login.
""",
        "03 Deployment/Backup Restore and Upgrade Runbook.md": frontmatter("runbook")
        + """# Backup Restore and Upgrade Runbook

1. Record `/api/status`, current allowlists, service state, and active COV peers.
2. Stop class activity; clear scenarios, faults, and forces.
3. Snapshot the repository and Obsidian vault.
4. Run `venv\\Scripts\\python.exe -m pytest -q`.
5. Apply the upgrade without changing `config/network.json`.
6. Restart the service during a maintenance window.
7. Confirm dashboard at port 8001, device instance 242000, and the release
   catalog count (329 for the current AHU safety release).
8. Verify WebCTRL read, write, release, polling, ConfirmedCOV, and UnconfirmedCOV.
9. Record the result in a Bench Session note.

Rollback by restoring the snapshot and the previously verified virtual
environment. Never use a broad recursive delete against the project or vault.
""",
        "03 Deployment/Remote Agent Architecture.md": frontmatter("architecture")
        + """# Remote Agent Architecture

Recommended now: run Hermes tools locally and use cloud inference over
outbound HTTPS. This keeps simulator and vault access on the laptop.

```mermaid
flowchart LR
  C["Cloud model API"] <-->|"Outbound HTTPS"| H["Hermes runtime on laptop"]
  H -->|"Read-only skill"| G["Local read-only gateway"]
  G --> A["Simulator GET endpoints"]
  H --> V["Obsidian vault"]
```

For a fully remote agent, use Tailscale/WireGuard and directional policy to a
narrow local read-only gateway. Never expose BACnet, Ollama, SMB, raw shell,
or the complete simulator API.

See [[Hybrid Cloud Agent Runbook]] and [[Remote Agent Threat Model]].
""",
        "04 BACnet Conformance/Conformance Matrix.md": frontmatter("conformance", "tests/")
        + """# Conformance Matrix

| Capability | Status | Evidence |
|---|---|---|
| Who-Is / I-Am discovery | Verified | integration tests + live WebCTRL |
| ReadProperty / RPM | Verified | integration tests + traffic logs |
| WriteProperty / WPM | Verified | integration tests + live commands |
| Command priority / release | Verified | tests and dashboard operations |
| UnconfirmedCOV | Verified | tests + live subscription panel |
| ConfirmedCOV | Verified | tests + live subscription panel |
| Peer/write allowlists | Verified | config tests + blocked counter |
| Reliability/status flags | Implemented | fault and equipment tests |
| Duplicate instance startup check | Implemented | retain live monitoring |
| Duplicate-instance fault mechanic | Not implemented | tracked limitation |
| Incorrect network/units faults | Not implemented | tracked limitation |
""",
        "04 BACnet Conformance/BACnet Object Support.md": frontmatter("conformance", "config/devices/")
        + """# BACnet Object Support

The device exposes analog input/output/value and binary input/output/value
objects under device `242000`. Each config declares direction, units,
writability, commandability, ranges, update interval, and relinquish default.

Use [[Equipment Catalog]] for the generated point-level inventory and
[[BACnet Point Allocation Standard]] for the immutable numbering rules.
Use [[Conformance Matrix]] for protocol evidence.
""",
        "04 BACnet Conformance/COV and Subscription Behavior.md": frontmatter("conformance", "app/transport.py")
        + """# COV and Subscription Behavior

- Polling is used for refresh intervals below 31 seconds.
- UnconfirmedCOV is used for intervals at or above 31 seconds.
- ConfirmedCOV is used for intervals at or above one minute ending in `:01`.
- The dashboard displays active subscriptions by mode and peer.
- Transport faults intercept subscriptions and multi-property services too.

Verify all three modes after service or network changes.
""",
        "04 BACnet Conformance/Command Priority Test Matrix.md": frontmatter("test-case")
        + """# Command Priority Test Matrix

| Case | Action | Expected result |
|---|---|---|
| Normal command | Write valid value at WebCTRL priority | Effective value changes |
| Competing priority | Write at stronger priority | Stronger value wins |
| Release | Write NULL/release selected priority | Next active priority/default wins |
| Non-writable point | Attempt write | Rejected and logged |
| Unknown peer | Attempt write | Silently dropped and blocked count rises |
| Manual force | Force through dashboard | Forced badge and effective value shown |
| Clear/release | Release force | Equipment resumes command/model behavior |
""",
        "05 WebCTRL Testing/WebCTRL Acceptance Test.md": frontmatter("acceptance-test")
        + """# WebCTRL Acceptance Test

1. Discover `ACI-SIM-SUPERVISOR`, instance `242000`.
2. Confirm expected group/object bindings and engineering units.
3. Read representative AI, BI, AV, and BV values.
4. Command representative AO/BO objects and observe physical-model response.
5. Release commands and confirm priority fallback.
6. Exercise polling, UnconfirmedCOV, and ConfirmedCOV.
7. Verify alarms from freezestat/reliability scenarios.
8. Verify trends and graphics follow simulated behavior.
9. Confirm unknown sources are blocked.
10. Reset scenario/fault/force state and record evidence.
""",
        "05 WebCTRL Testing/Discovery Binding and Commanding.md": frontmatter("runbook")
        + """# Discovery Binding and Commanding

Use `192.168.168.201:47808` as the simulator target. WebCTRL `.200` uses its
BACnet connection on UDP 47809. Do not change the simulator to port 8000;
that is unrelated HTTP service traffic.

When binding, use object identifiers and names from [[Equipment Catalog]].
After a command, verify the dashboard's last-command source, property,
priority, and timestamp.
""",
        "05 WebCTRL Testing/Graphics Trends and Alarm Tests.md": frontmatter("test-case")
        + """# Graphics Trends and Alarm Tests

- Trend outdoor air, supply air, zone temperature, command, and proof points.
- Compare polling against both COV modes.
- Run freezestat, sensor drift/frozen, failed ignition/proof, and comm-loss scenarios.
- Confirm graphics distinguish command, status/proof, reliability, fault, and force.
- Verify recovery and alarm normalization after Reset.
""",
        "06 Troubleshooting/Discover Error 2026-07-20.md": frontmatter("incident")
        + """# Discover Error 2026-07-20

Historical placeholder completed for chronology. Earlier discovery failures
were associated with topology/port assumptions and a Windows receive-path
problem. The current verified baseline is:

- simulator `192.168.168.201:47808`
- peers `.1-.7,.200`
- dashboard `127.0.0.1:8001`
- device instance `242000`

Use [[BACnet Device Deaf or Offline]] and do not revert to stale `.200`-only
or simulator-port-47809 instructions.
""",
        "06 Troubleshooting/Troubleshooting MOC.md": frontmatter("moc")
        + """# Troubleshooting MOC

- [[Port 8000 Conflict]]
- [[BACnet Device Deaf or Offline]]
- [[Write Rejected or Ignored]]
- [[Windows Service Startup Failure]]
- [[Ollama Unavailable]]
- [[Memory Pressure and Slow Agents]]
- [[BACnet Traffic Observation 2026-07-22]]
""",
        "06 Troubleshooting/Port 8000 Conflict.md": frontmatter("runbook")
        + """# Port 8000 Conflict

**Symptom:** browser on port 8000 shows an unrelated Microsoft HTTPAPI page
or a method error.

**Resolution:** use `http://127.0.0.1:8001`. The simulator default is 8001
and current Windows launch/service documentation has been corrected.
Do not stop PID 4 or the unrelated HTTP service just to free port 8000.
""",
        "06 Troubleshooting/BACnet Device Deaf or Offline.md": frontmatter("runbook")
        + """# BACnet Device Deaf or Offline

1. Confirm `ACIBACnetSimulator` is running.
2. Check `/api/status` at port 8001.
3. Confirm bind `192.168.168.201:47808`.
4. Check active `device_offline`, `slow_response`, or `intermittent_comm` faults.
5. Confirm the source is in `.1-.7,.200`.
6. Review blocked and receive counters plus `bacnet_traffic.log`.
7. Run `scripts\\verify_live_receive.py` from a separate process if safe.
8. If the socket is bound but deaf after startup, investigate the duplicate
   instance check before changing topology.
""",
        "06 Troubleshooting/Write Rejected or Ignored.md": frontmatter("runbook")
        + """# Write Rejected or Ignored

Check, in order: source allowlist, point existence, writability,
`write_rejected` transport fault, BACnet priority array, active manual force,
active scenario, then equipment interlocks. A command can be accepted yet
not produce the expected mechanical output when a proof or safety condition
is intentionally missing.
""",
        "06 Troubleshooting/Windows Service Startup Failure.md": frontmatter("runbook")
        + """# Windows Service Startup Failure

1. Check NSSM service path, parameters, and working directory.
2. Inspect `service_stdout.log` and `service_stderr.log`.
3. Run `venv\\Scripts\\python.exe --version`; expected 3.11.7.
4. Verify `venv\\pyvenv.cfg` still points to an installed base Python.
5. Run the test suite interactively.
6. Do not repeatedly restart if UDP 47808 is held by another process.
""",
        "06 Troubleshooting/Ollama Unavailable.md": frontmatter("runbook")
        + """# Ollama Unavailable

The BACnet simulator remains operational without Ollama.

1. Confirm a user is logged in; Ollama is not a LocalSystem service.
2. Check `127.0.0.1:11434` and the dashboard AI status.
3. Confirm `hermes3:3b` is installed.
4. Do not bind Ollama to the bench LAN or Internet.
5. Restart only Ollama when possible; avoid disrupting the BACnet service.
""",
        "06 Troubleshooting/Memory Pressure and Slow Agents.md": frontmatter("runbook")
        + """# Memory Pressure and Slow Agents

With 8 GB installed and 6.8 GiB usable, this laptop was already paging before
a model was loaded. Close unnecessary applications, keep local model use to
3B/4B quantized models, and prefer cloud inference. Do not load the 23 GB
`qwen3.6:latest` model. Upgrade to 2×16 GB before 64K-context local agent
testing.
""",
        "08 Equipment Templates/Equipment Catalog.md": frontmatter("equipment-catalog", "config/devices/*.json")
        + """# Equipment Catalog

The working checkout and installed Windows service are verified at **28
equipment groups and 329 BACnet objects** under device `242000`.

## Equipment sheets

_Generated equipment links are appended during vault generation._
""",
        "08 Equipment Templates/Building Pressure and Exhaust Control.md": frontmatter(
            "equipment", "config/devices/site.json"
        )
        + """# Building Pressure and Exhaust Control

## Control intent

- WebCTRL asserts the general exhaust-fan start/stop command when its building
  schedule is occupied.
- WebCTRL modulates `ACI-SIM-EF-1.vfd_speed_command` from 0-100%.
- The AHU supply fan produces positive building pressure.
- The exhaust fan trims or relieves that pressure toward the intended
  `0.03-0.10 in. w.c.` band.
- The simulator publishes `ACI-SIM-SITE.building_pressure` as an analog input.

## Training checks

1. Start the AHU supply fan and observe building pressure rise.
2. Enable EF-1 and increase its VFD command; pressure should fall.
3. Command EF-1 on without proof for more than 15 seconds; the command center
   should flag the equipment in red.
4. Return valid proof and verify that the diagnostic clears.

This loop is intentionally simplified for training and showcase use. It does
not implement smoke control, stair pressurization, or life-safety sequences.
""",
        "09 LLM Operations/Approved Action Schema.md": frontmatter("llm-policy", "app/llm/action_schema.py")
        + """# Approved Action Schema

Model output must validate as `LlmActionBundle`: request ID, allowed intent,
summary, typed action list, warnings, `requires_approval`, and confidence.
Each action is revalidated against the current registry, scenario policy,
and allowed Phase 6 action set before execution.

The API now records a short-lived proposal token and bundle hash. Apply
requires the matching one-time token, preventing a stale or modified preview
from being replayed. This is defense-in-depth, not remote authentication.
""",
        "09 LLM Operations/LLM Guardrails.md": frontmatter("llm-policy", "app/llm/")
        + """# LLM Guardrails

- Core BACnet simulation runs without an LLM.
- The model returns structured JSON, never executable code.
- Allowed intents/actions are explicit.
- Targets must exist in the current registry.
- A proposal is previewed before apply.
- Apply revalidates the bundle and consumes a matching one-time proposal token.
- Audit records proposals, validation, application, and errors.
- Hermes autonomous access remains read-only.
- Dashboard and Ollama stay on loopback.

## Residual risk

A local process with user/session access can call local endpoints. Add an
instructor PIN/authentication layer before granting untrusted local users or
remote clients access to state-changing endpoints.
""",
        "09 LLM Operations/LLM Operations MOC.md": frontmatter("moc")
        + """# LLM Operations MOC

- [[Computer and Model Feasibility]]
- [[Model Evaluation Matrix]]
- [[Hybrid Cloud Agent Runbook]]
- [[Remote Agent Threat Model]]
- [[Prompt and Policy Change Log]]
- [[Approved Action Schema]]
- [[LLM Guardrails]]
- [[Hermes Vault Skills]]
""",
        "09 LLM Operations/Computer and Model Feasibility.md": frontmatter("agent-feasibility")
        + """# Computer and Model Feasibility

## Decision

| Option | Feasibility now | Recommendation |
|---|---|---|
| Simulator AI Console + local `hermes3:3b` | Viable | Keep |
| Full Hermes Agent + local Ollama on 8 GB | Not reliable | Do not use for unattended work |
| Hermes local tools + cloud inference | Viable | **Recommended now** |
| Fully remote agent + private gateway | Viable with controls | Second choice |

Current Hermes requires at least 64K effective context for tool-agent use.
Ollama defaults to a much smaller effective context on low-VRAM systems, and
model-advertised maximum context is not proof of configured runtime context.
The laptop is already memory constrained with no model loaded.

After a 32 GB RAM upgrade, 3B/4B local testing is plausible but CPU-only
long-context work will remain slow. The integrated Radeon is not a supported
Windows ROCm target for Ollama production use.

Sources:

- [Hermes quickstart](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/getting-started/quickstart.md)
- [Hermes provider guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md)
- [Ollama FAQ and context memory](https://docs.ollama.com/faq)
- [Ollama GPU support](https://docs.ollama.com/gpu)
""",
        "09 LLM Operations/Model Evaluation Matrix.md": frontmatter("model-evaluation")
        + """# Model Evaluation Matrix

| Model/provider | Disk/model size | Current role | Decision |
|---|---:|---|---|
| `hermes3:3b` / Ollama | 2.0 GB | Simulator structured proposals | Keep |
| `qwen3:4b-instruct` / Ollama | 2.5 GB | Controlled comparison | Test only |
| `qwen3.6:latest` / Ollama | 23 GB | None | Do not load on this laptop |
| Claude Sonnet / Anthropic | Cloud | Hermes active provider | Recommended current agent inference |

Evaluate JSON validity, tool calling, effective context, latency, peak memory,
recovery after failure, and safety-policy adherence. Record actual runtime
context, not advertised maximum context.
""",
        "09 LLM Operations/Hybrid Cloud Agent Runbook.md": frontmatter("runbook")
        + """# Hybrid Cloud Agent Runbook

1. Keep Hermes runtime and read-only skills on this laptop.
2. Keep the active cloud provider in Hermes `config.yaml`.
3. Use outbound HTTPS only; no inbound public listener.
4. Restrict simulator skill to approved GET endpoints.
5. Allow vault reads/writes only inside this Obsidian vault.
6. Require an operator to perform simulator state changes.
7. Rotate provider credentials and keep them out of notes/repository.
8. Record agent changes in [[Prompt and Policy Change Log]].
""",
        "09 LLM Operations/Remote Agent Threat Model.md": frontmatter("threat-model")
        + """# Remote Agent Threat Model

| Threat | Control |
|---|---|
| Public BACnet access | Never route UDP 47808 outside the private bench |
| Public Ollama/API access | Keep 11434 and 8001 on loopback |
| Agent commands simulator | Read-only gateway; omit POST/apply routes |
| Shell reaches arbitrary files | Allowlist vault/project read paths |
| Stolen cloud credential | Outbound-only secret store and rotation |
| Lateral movement over SMB | Do not expose SMB; use scoped gateway |
| Prompt injection from notes/logs | Treat content as data; fixed tool policy |
| Replay/tamper of AI proposal | One-time token plus bundle hash |

Use Tailscale/WireGuard directional policy if a remote server is added.
[Tailscale access controls](https://tailscale.com/kb/1100/services)
""",
        "09 LLM Operations/Prompt and Policy Change Log.md": frontmatter("change-log")
        + """# Prompt and Policy Change Log

| Date | Change | Reason | Verification |
|---|---|---|---|
| 2026-07-23 | Added one-time proposal token and bundle hash | Prevent apply of unrecorded/tampered bundle | API safety tests |
| 2026-07-23 | Preserved Hermes read-only simulator boundary | Protect live training state | Skill/config inspection |
| 2026-07-23 | Marked 4K local-Hermes guidance obsolete | Current Hermes requires 64K effective context | Official docs review |
""",
        "11 Training and Showcase/Instructor Guide.md": frontmatter("training")
        + """# Instructor Guide

## Before class

1. Confirm dashboard LINK OK and engine running. Expect 28 groups / 329
   points after the current release cutover.
2. Confirm no active scenario, faults, or forces.
3. Confirm WebCTRL reads and COV subscriptions.
4. Select one exercise and review its expected results.

## During class

- Students diagnose from WebCTRL; the instructor owns the simulator dashboard.
- Explain command versus proof/status and priority versus release.
- Use Overview for health, Duct Static PID for fan-control training, and
  Operations for controlled fault/scenario work.
- Keep STOP ALL for recovery, not routine scenario completion.

## After class

Reset scenario state, clear faults/forces, verify normal values and WebCTRL
communications, then create a Bench Session evidence note.
""",
        "11 Training and Showcase/Student Exercise Catalog.md": frontmatter("training")
        + """# Student Exercise Catalog

| Exercise | Skills |
|---|---|
| AHU freezestat trip | Safety interlock diagnosis |
| VAV reheat valve stuck | Command versus physical response |
| Chiller CHWS sensor drift | Trend-based sensor diagnosis |
| Boiler ignition failure | Sequence/proof troubleshooting |
| Frozen zone sensor | Reliability and plausibility |
| Device communications loss | Network versus equipment diagnosis |
| Outside-air location link | Upstream/downstream data relationships |
| Duct-static demand disturbance | Observe pressure fall and VFD recovery as VAV dampers open |
| Excess proportional gain | Recognize overshoot and hunting without assuming one universal correct gain |
| Integral recovery | Compare steady-state error, integral correction, reset-memory behavior, and windup protection |
| PID calculation interval | Compare response smoothness and stability at different intervals and simulation rates |
| Restart recovery | Verify values, faults, forces, speed, and time reset while the WebCTRL BACnet/COV session remains attached |

Use [[Scenario Catalog]] for event timing and expected results.
""",
        "11 Training and Showcase/Demo Script.md": frontmatter("training")
        + """# Demo Script

1. Show one supervisory device and the equipment/point catalog.
2. Change site weather and show deterministic system response.
3. Command an AHU or plant point from WebCTRL and show last-command detail.
4. Open Duct Static PID, change aggregate VAV demand, and compare setpoint,
   actual pressure, and fan-speed response.
5. Adjust one PID gain, discuss overshoot/settling tradeoffs, then restore
   default tuning.
6. Compare polling, UnconfirmedCOV, and ConfirmedCOV.
7. Start a short scenario; ask attendees to diagnose only from WebCTRL.
8. Reveal the Operations state and explain the root cause.
9. Reset, prove recovery, and show the AI proposal preview without applying
   unless the instructor explicitly wants the demonstration.
""",
        "11 Training and Showcase/Reset and Recovery Checklist.md": frontmatter("checklist")
        + """# Reset and Recovery Checklist

- [ ] Stop/reset the active scenario
- [ ] Clear all faults
- [ ] Release manual forces
- [ ] Set time rate to 1×
- [ ] Start the simulation engine
- [ ] Restore expected site weather
- [ ] Confirm LINK OK and no stale banner
- [ ] Confirm WebCTRL reads/writes
- [ ] Confirm COV subscriptions
- [ ] Record unexpected behavior in Test Evidence
""",
        "90 Decisions/ADR-001 Deterministic BACnet Core.md": frontmatter("decision", "app/engine.py")
        + """# ADR-001 Deterministic BACnet Core

**Status:** Accepted.

The BACnet and equipment simulation remains deterministic and independent of
LLM availability. Scenarios and faults use typed mechanics; an LLM may only
propose schema-valid actions through the same orchestration path as the
instructor UI.

This preserves repeatability, offline operation, and meaningful tests.
""",
        "90 Decisions/ADR-002 WebCTRL Command Authority.md": frontmatter("decision", "app/registry.py")
        + """# ADR-002 WebCTRL Command Authority

**Status:** Accepted.

WebCTRL and verified BACnet peers remain the command authority for normal
operation. Simulator forces/scenarios are instructor tools. Autonomous agents
receive read-only access and may write proposals, not BACnet or simulator
state.
""",
        "90 Decisions/Decision Index.md": frontmatter("moc")
        + """# Decision Index

- [[ADR-001 Deterministic BACnet Core]]
- [[ADR-002 WebCTRL Command Authority]]

Add future decisions with context, decision, alternatives, consequences,
verification, and rollback criteria.
""",
    }
    for relative, body in notes.items():
        write(output, relative, body)

    templates = {
        "Equipment": "group_id:\ninstance_offset:\npoint_count:\n",
        "Scenario": "scenario_id:\nduration_seconds:\nobjectives:\n",
        "Test Case": "result: pending\nevidence:\n",
        "Troubleshooting Incident": "severity:\nsymptom:\nroot_cause:\n",
        "Risk": "risk_level:\nowner:\nmitigation:\n",
        "Configuration Change": "change_window:\nrollback:\nverification:\n",
        "Model Evaluation": "provider:\nmodel:\neffective_context:\npeak_memory:\n",
        "Training Exercise": "audience:\nduration:\nlearning_objectives:\n",
        "Release Deployment": "version:\nrollback:\nsmoke_test:\n",
        "Source Snapshot": "source_path:\nsource_revision:\n",
    }
    for name, fields in templates.items():
        body = (
            "---\n"
            f"type: {name.lower().replace(' ', '-')}\n"
            "status: draft\n"
            f"date: {VERIFIED}\n"
            f"{fields}"
            "tags:\n  - template\n"
            "---\n\n"
            f"# {name} — {{title}}\n\n## Context\n\n## Evidence\n\n## Result\n"
        )
        write(output, f"98 Templates/{name}.md", body)

    bases = {
        "08 Equipment Templates/Equipment Catalog.base": "equipment",
        "02 Project/Risk Register.base": "risk",
        "90 Decisions/Decision Register.base": "decision",
        "11 Training and Showcase/Training Exercise Catalog.base": "training-exercise",
        "09 LLM Operations/Model Evaluation Catalog.base": "model-evaluation",
    }
    for relative, note_type in bases.items():
        folder = str(Path(relative).parent).replace("\\", "/")
        content = f"""filters:
  and:
    - file.inFolder("{folder}")
    - 'type == "{note_type}"'

views:
  - type: table
    name: "{Path(relative).stem}"
    order:
      - file.name
      - status
      - last_verified
"""
        write(output, relative, content)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    build_static_notes(output)
    equipment_links, point_count = equipment_notes(repo, output)
    vav_schedule(repo, output)
    building_design_plan(repo, output)
    point_allocation_standard(repo, output)
    _, scenario_count = scenario_catalog(repo, output)
    api_catalog(repo, output)
    test_catalog(repo, output)

    catalog_path = output / "08 Equipment Templates" / "Equipment Catalog.md"
    catalog = catalog_path.read_text(encoding="utf-8")
    catalog = catalog.replace(
        "_Generated equipment links are appended during vault generation._",
        "\n".join(f"- {link}" for link in equipment_links),
    )
    catalog_path.write_text(catalog, encoding="utf-8")

    files = [
        path
        for path in output.rglob("*")
        if path.is_file() and path.name != "VAULT_BUILD_MANIFEST.md"
    ]
    manifest = f"""# Vault Build Manifest

- Generated: {VERIFIED}
- Source: `{repo}`
- Files staged: {len(files)}
- Equipment groups: {len(equipment_links)}
- BACnet points: {point_count}
- Scenarios: {scenario_count}
- Merge policy: copy new files and replace zero-byte placeholders only
"""
    write(output, "VAULT_BUILD_MANIFEST.md", manifest)
    print(
        json.dumps(
            {
                "output": str(output),
                "files": len(files) + 1,
                "equipment_groups": len(equipment_links),
                "points": point_count,
                "scenarios": scenario_count,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
