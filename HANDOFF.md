# ACI BACnet Building Simulation Platform — Handoff Document (v3)

Prepared for Jeff Jenkins, Automated Controls Inc. Written to be
self-contained for a fresh agent session with no prior context — if
you're picking this up cold, this document plus the codebase itself
should be enough to continue safely.

---

## 0. Update — 2026-07-17 Session (read this first)

A full working session on Jeff's machine (`JEFF-JENKINS`, which hosts
WebCTRL 8.0–10.0 installs and is the likely bench laptop) moved the
project substantially. The repo lives at
`github.com/JeffJenkinsBAS/bacnet_simulator` (private) and is the source
of truth — the phase zips in §8 are historical. **Test suite: 55/55.**
Highlights, roughly in order:

- **Windows batch scripts were broken and are now fixed.** All seven
  `scripts\windows\*.bat` resolved paths one directory too shallow
  (`%~dp0\..` from `scripts\windows\` lands in `scripts\`), so
  `install.bat`/`run.bat`/service install could never have worked. Fixed,
  plus a new `run_headless.bat` because Task Scheduler provides no working
  directory. The fixed installer was executed end-to-end on this machine.
- **A full audit of every equipment model and the BACnet layer** was
  performed against the `webctrl-skill` domain references —
  `SIMULATION_AUDIT.md` is the report. **Every HIGH/MEDIUM finding is
  fixed**: manager groups are now serviced by aggregator models
  (`equipment/managers.py` — chillerN_ok/boilerN_ok mirrors, live CHW
  common header, plant remote_shutdown, Boiler Manager enables), VAV
  reheat discharge is clamped at hot-water temp, cooling-tower physics
  now track wet-bulb + approach and climb toward a high-head trip when
  the fan stops, chillers/boilers have flow-proving interlocks, the
  freezestat closes the OA damper, and `reliability_fail` faults set the
  real BACnet Reliability property.
- **CRITICAL Windows bug found and fixed: the deaf BACnet device.** The
  startup duplicate-instance check broadcast a Who-Is to 127.0.0.255; on
  Windows that send kills the asyncio UDP transport — socket stays bound,
  but the app never receives another BACnet packet. Every instance ever
  launched on this machine had been deaf (`messages_in` permanently 0).
  The check now skips on loopback binds. Two more live-caught bugs: the
  engine-start API endpoint corrupted state when called (sync-def
  threadpool, no event loop — mutating endpoints are now async), and
  priority-array forces on binary points always raised TypeError (now
  written as typed `BinaryPV`).
- **COV is RESOLVED** (was §5's open item): confirmed AND unconfirmed
  notification delivery verified live, cross-process, on the real port.
  All three WebCTRL refresh strategies work on every point — polling
  (refresh < 31 s), UnconfirmedCOV (>= 31 s), ConfirmedCOV (>= 1 min
  ending :01). `/api/cov/subscriptions` + a dashboard panel show live
  subscriptions per mode for training. Transport faults and the traffic
  counter now also intercept ReadPropertyMultiple / WritePropertyMultiple
  / SubscribeCOV — what WebCTRL actually sends.
- **Network isolation and verified bench topology (authoritative).** The
  bench is an isolated `192.168.168.0/24` segment with exactly two hosts:
  - **Simulator** (this app): `192.168.168.201`, listening on **UDP
    47808**.
  - **WebCTRL**: `192.168.168.200`, BACnet connection on **UDP 47809**,
    targeting the simulator at `192.168.168.201:47808`.
  - **Device instance**: `242000`.

  A `peer_allowlist` in `config/network.json` silently drops (no reply)
  every BACnet request from a source IP not in it, counting it in a
  `messages_blocked` counter on the dashboard; `write_source_allowlist`
  does the same for writes. Both are set to the WebCTRL host
  `192.168.168.200` so only WebCTRL can reach the simulator.

  > **Supersedes the 2026-07-17 "bench standard is UDP 47809 / never move
  > back to 47808" note below.** That earlier decision assumed a
  > co-resident WebCTRL sharing the simulator's NIC and an office WebCTRL
  > squatting on 47808. The verified bench instead separates the two hosts
  > (`.200` WebCTRL / `.201` simulator) on an isolated subnet: the
  > simulator now binds **47808** and WebCTRL uses **47809**. Any remaining
  > references to a simulator on 47809, a `192.168.168.100` bench IP, or a
  > `192.168.68.0/24` subnet are stale.
- **The dashboard was rebuilt twice** (war-room pass, then Apple
  liquid-glass design per Jeff's preference): glass rail/command bar,
  capsule controls, engine power toggle, ×1–×60 time-rate control, live
  point search with value-change flashes, toasts, confirm modals, COV
  panel, peer-allowlist/blocked-requests readouts, and the ACI round logo
  (static/logo.png) as the top-left brand mark. Still one self-contained
  HTML file, offline-safe.
- **IT/network coordination**: the simulator binds the bench NIC directly
  at the verified static IP `192.168.168.201/24` (machine `JEFF-JENKINS`;
  wired Killer E3100G MAC `D8-BB-C1-F7-89-38`, USB Realtek
  `E0-EF-25-01-BD-C1`). The bench WebCTRL host is `192.168.168.200` on the
  same isolated `/24`. (An earlier manual `192.168.168.100` on the wired
  port is superseded by `192.168.168.201`.)

---

## 1. What This Is

A locally-running BACnet/IP simulation application that stands in for real
HVAC field equipment on the WebCTRL training test bench. It exposes ~143
BACnet objects under one simulated supervisory device, publishing
realistic sensor/status values and accepting commands from Jeff's existing
WebCTRL/EIKON programs (AHU, Chiller Manager, Boiler Manager, VAV-1,
VAV-2, Simulation Manager) exactly as real field hardware would. Built for
technician training, controls programming practice, and commissioning/
troubleshooting exercises.

---

## 2. Status Right Now

**Phases 1–6a complete, plus the 2026-07-17 hardening pass (§0)**:
architecture, equipment models (AHU, 3 chillers, 3 boilers, exhaust fan,
5 VAV zones, site conditions, and now 2 plant-manager aggregators), fault
injection (11 mechanics), scenario engine (6 shipped scenarios), the
liquid-glass dashboard, LLM orchestration (6a), and Windows
packaging/service scripts (now actually working — see §0). **55 automated
tests passing.** Core behavior verified against live BACnet/IP traffic
across OS processes on this Windows machine, including both COV modes.

**Physical deployment to the bench laptop, in progress:**
- ✅ Project on the laptop, synced with the private GitHub repo
  (`JeffJenkinsBAS/bacnet_simulator`).
- ✅ Found and disabled a conflicting service (a previously-purchased SCADA
  Systems BACnet Simulator was squatting on UDP 47808).
- ✅ venv installed via the FIXED `install.bat`; suite green on this machine.
- ✅ **Verified bench topology (see §0)**: simulator `192.168.168.201`
  on **UDP 47808**, WebCTRL `192.168.168.200` on UDP 47809, device
  instance `242000`, `peer_allowlist` and `write_source_allowlist` both
  `["192.168.168.200"]`. `config/network.json` now reflects this. (This
  supersedes the earlier 2026-07-17 "simulator on 47809" decision.)
- ⬜ **Still not confirmed complete on the physical bench**: NIC set to
  `192.168.168.201/24`, the Windows Firewall rule (UDP 47808, scoped to
  `192.168.168.200` — see `scripts\windows\add_firewall_47808.bat`), NSSM
  service installation, the post-reboot auto-start check, WebCTRL
  Discovery against `192.168.168.201:47808`, and the first real read/write
  smoke test (AHU-1 command → VAV-1 response visible in WebCTRL). The full
  sequence is in `NEXT_STEPS_INTEGRATION_TESTING.md`.

**Phase 6 direction has been reviewed and phased** (see §7) — a large
LLM/Ollama orchestration + dashboard upgrade spec was submitted and
critiqued before any implementation started, per that spec's own
instruction to plan before making major changes. Full review in
`PHASE6_REVIEW.md`. **Phase 6a is now built and verified live** — see §7.5.

---

## 3. Architecture Summary

- **One BACnet device** (`ACI-SIM-SUPERVISOR`) hosts everything — not one
  device per equipment group. This was a direct field correction from Jeff
  after an earlier draft used 16 separate devices/ports.
- **Verified device instance: 242000.** Confirmed for the bench, matching
  the `2420xx` block Jeff designated.
- **16 equipment groups, 143 BACnet objects.** Each group has an
  `instance_offset` (position × 1000 — e.g. AHU-1 is offset 9000); a
  point's real global object instance is `offset + local_instance`. Full
  table in `ACI_BACnet_Simulator_Point_Mapping.xlsx`.
- **Five-layer design:** BACnet Transport (`transport.py`, one bacpypes3
  Application) → Simulation Engine (`engine.py`, 1Hz tick loop) →
  Equipment Models (`equipment/*.py`) → Point Registry
  (`registry.py`: `PointRegistry` + `GroupView`) → FastAPI/REST (`api.py`)
  + dashboard (`static/index.html`).
- **`GroupView` is the critical extension seam** — every equipment model
  reads/writes through it, and it's what let the single-device merge and
  fault-injection system get added without touching any equipment model
  file. **Any Phase 6 work should extend through this same seam wherever
  possible**, not add parallel access paths to the registry.
- **Fault injection**: 11 generic mechanics (frozen/offset/drift/
  reliability-fail for sensors; stuck/reversed for actuators; forced-status
  for booleans; device-offline/slow-response/write-rejected/intermittent-
  comm at the transport level).
- **Scenario engine**: 6 shipped scenarios, timed against simulated time.
  `set_value` distinguishes writable (real BACnet write, priority 3) vs.
  non-writable (fault-layer override) targets — a design correction made
  mid-build; see `scenario.py`'s docstring for why.
- **Instructor Panel UI**: tabbed dashboard (Dashboard / Equipment &
  Points / Instructor Panel / Logs), fault injection, force/release,
  scenario controls, a confirmed "Stop All Simulation."
- **No authentication anywhere in the API.** Accepted so far because the
  blast radius was bounded to simulated point values, reversible via Stop
  All Simulation. **This assumption needs to be revisited before Phase 6
  ships anything that can structurally add/remove equipment** — see §7.

---

## 4. Project Structure Reference

```
app/
  config_models.py      Pydantic schema: EquipmentGroupConfig, SupervisoryDeviceConfig, NetworkConfig
  registry.py             PointRegistry (all 143 objects) + GroupView (per-group scoping, fault-aware)
  transport.py             The one bacpypes3 Application; network safety + transport-level faults
  engine.py                 1Hz tick loop; ticks FaultManager, ScenarioEngine, then every equipment model
  faults.py                  FaultManager, FaultType enum, the named-fault-to-mechanic mapping table
  scenario.py                 Scenario/ScenarioEvent schema, ScenarioEngine (+ register_scenario for Phase 6a)
  equipment/                    ahu.py, chiller.py, boiler.py, exhaust_fan.py, site.py, vav_single_duct.py
  llm/                             action_schema.py, action_validator.py, ollama_client.py, prompt_templates.py (Phase 6a)
  services/                          orchestration_service.py, audit_service.py (Phase 6a)
  api.py                          FastAPI REST endpoints (+ /api/llm/* in Phase 6a)
  main.py                          Entry point — loads config, wires everything, starts uvicorn
config/
  network.json                     verified: bind_address 192.168.168.201, udp_port 47808, peer_allowlist + write_source_allowlist = [192.168.168.200]
  supervisory_device.json            The one device's instance/name/description
  devices/*.json                       16 equipment group configs (generated — see script below)
  scenarios/*.json                       6 shipped training scenarios
  llm/                                     models.json, system_prompts.json, policies.json (Phase 6a)
scripts/
  generate_phase3_configs.py               Source of truth for config/devices/*.json — re-run after object-model changes
  generate_point_mapping_workbook.py         Generates the point-mapping Excel workbook from live config
  windows/install.bat, install_offline.bat, download_offline_packages.bat, run.bat   Setup/run
  windows/install_service.bat, uninstall_service.bat, install_scheduled_task.bat      Auto-start on boot
static/index.html            Tabbed dashboard (Dashboard / Points / Instructor Panel / LLM Console / Logs)
tests/                          40 tests: unit, integration (real BACnet/IP), proof-delay regression, faults/scenarios, LLM orchestration (Phase 6a)
PACKAGING.md                      Full install/service/firewall/troubleshooting guide
NEXT_STEPS_INTEGRATION_TESTING.md   Bench laptop deployment step sequence
PHASE6_REVIEW.md                      Architecture review + phased plan for the LLM/dashboard expansion
README.md                            Phase-by-phase technical narrative
```

---

## 5. What's Been Verified Live (not just unit tests)

- Real BACnet reads/writes/priority-array resolution, over loopback and
  across separate OS processes.
- Object-list discovery (`objectList` property enumerates all 145 objects
  correctly — matches WebCTRL's own Discovery mechanism).
- Cross-equipment coupling: AHU-1 cooling valve/fan commands visibly
  changing VAV-1's discharge temp in real time via the in-process
  `AhuModel` reference.
- Hard interlocks: tripping Freezestat Trip forces the fan off and heating
  valve open, confirmed via real BACnet writes and reads.
- **A real bug found this way and fixed**: fan/pump proof-delay states were
  silently never proving on, because a boolean was fed back into a
  first-order lag calculation every tick, discarding fractional progress.
  Fixed; regression tests added specifically for this bug class
  (`tests/test_equipment_proof_delays.py`).
- The single-device merge: unique object *instances* and unique object
  *names* (BACnet requires both, independently — discovered when all
  three boilers originally shared the literal name "Boiler OK" and broke
  startup).
- Fault mechanics tested against real equipment models and real BACnet
  objects, not mocks.
- A full scenario run end-to-end (`vav1_reheat_valve_stuck`): the real
  BACnet object accepted a new command (showed 90%) while the equipment's
  actual behavior (discharge temp) stayed flat — the exact mismatch the
  scenario exists to teach.
- **COV fully verified (2026-07-17, supersedes the earlier open item):**
  subscription, acknowledgment, AND change-driven notification delivery —
  both **confirmed** and **unconfirmed** modes — proven live across OS
  processes against the production port, plus covered by automated tests
  (`tests/test_audit_fixes_and_cov.py`). All three WebCTRL refresh
  strategies are usable on every point.
- **Single-point connection enforcement**: a non-allowlisted source gets
  pure silence (no reply, counted in `messages_blocked`) while an
  allowlisted one reads normally — verified live and by test.
- The audit-fix behaviors (flow-proving interlocks, tower high-head
  climb, VAV reheat clamp, manager mirrors, reliability flagging) each
  carry a dedicated test; the manager mirrors and COV panel were also
  exercised against the live running instance.

---

## 6. Known Open Items Carried Forward

| Item | Status |
|---|---|
| Supervisory device instance 242000 | **Verified** for the bench |
| ~~COV notification delivery~~ | **RESOLVED 2026-07-17** — both modes verified live + tested; see §0/§5 |
| Duplicate BACnet instance / incorrect network number / incorrect units faults | Not implemented — flagged in `faults.py`'s docstring |
| Occupancy modeling | Not implemented |
| Completion criteria / student objectives (scenarios) | Informational text only, not auto-graded |
| "Status Indicator" relay (Simulation Manager) | Assumed out of scope, same category as confirmed-out-of-scope "Safety Trip" — **not explicitly confirmed** the same way |
| NSSM binary | Not shipped, must be downloaded manually (`tools/nssm/PUT_NSSM_EXE_HERE.txt`) |
| ~~Windows batch/service scripts~~ | **FIXED 2026-07-17** — path bug corrected in all seven; `install.bat` executed end-to-end on this machine; service/scheduled-task install still unexercised |
| Full bench deployment (bind_address 192.168.168.201 + allowlists → Firewall UDP 47808 → service → reboot → WebCTRL Discovery against 192.168.168.201:47808 → smoke test) | **Not confirmed complete** — see §2 |
| Ollama install on this laptop | Still not installed (nothing on 11434) — AI Console reports NOT REACHABLE until then |
| Duplicate-instance startup check on a real NIC | Skipped on loopback (Windows deaf-device bug, §0); behavior on the bench NIC's real broadcast domain still unverified — if BACnet goes silent after startup on the bench, disable `startup_duplicate_instance_check` first |
| Minor audit leftovers (audit §2.4/§3.6) | Chiller `chw_iso_valve`/`ct_vfd_output`/`byp_vlv_output`/`manager_reset` ignored by the model; freezestat doesn't self-trip on low MA temp; documented, not wired |

---

## 7. Phase 6 Direction — Reviewed and Phased

A full LLM/Ollama orchestration + dashboard overhaul spec was submitted.
Full critique in `PHASE6_REVIEW.md`; summary and actionable guidance below.

### What's approved conceptually
The structured `llm_action_bundle` schema (mandatory JSON, explicit
`requires_approval`, bounded intent list, reported `confidence`), the
explicit non-roles list (no raw BACnet command issuance, no unsupervised
autonomous control), and "extend through `GroupView`, don't rewrite" are
all sound and should be followed as designed.

### Sequencing decision
**Confirm the remaining Phase 5 bench deployment steps (§2) are actually
complete — including the first real WebCTRL smoke test — before merging
any Phase 6 work into the primary tree.** If something breaks during that
first integration test after Phase 6 work has started, it needs to be
possible to know whether it's a pre-existing Phase 5 issue or a Phase 6
regression. Phase 6 work can happen on a branch/copy in parallel if
desired, but shouldn't become the working tree until Phase 5 is a
confirmed-good baseline.

### Risk-tiered phasing (build in this order)

- **Phase 6a — LLM-assisted scenario/fault generation.** `app/llm/`
  (Ollama client, action schema, validator), `app/services/
  orchestration_service.py` + `audit_service.py`, an LLM Console panel.
  Wired ONLY to `create_scenario` / `inject_fault` / `clear_fault` /
  `set_initial_condition` / `adjust_parameter` / `explain_behavior` /
  `summarize_events` — every one of these maps directly onto the
  *existing, already-tested* `ScenarioEngine` and `FaultManager` APIs with
  a validation layer in front. No BACnet object model changes. **Start
  here.**
- **Phase 6b — Dashboard upgrade.** Tab/panel expansion per the spec,
  *excluding* Trends & Alarms (needs a historian that doesn't exist yet)
  and excluding anything equipment-topology-related. Can run in parallel
  with 6a.
- **Phase 6c — Snapshot/restore, audit trail, trend historian.** New,
  self-contained subsystems that don't touch the live BACnet transport.
  Define precisely what a "snapshot" captures (object present-values? active
  faults? scenario state? each equipment model's internal state like AHU's
  MA/RA/SA temps or a chiller's proven state?) before building — this
  isn't specified in the original spec and needs an explicit answer, not
  an assumption.
- **Phase 6d — Dynamic equipment management** (`add_equipment` /
  `modify_equipment` / `remove_equipment`). **Do not start this until the
  five open questions below have answers.** This is the piece that
  directly intersects the `do_not_break` list's instance-mapping and
  name-uniqueness guarantees, and needs its own test suite proving those
  invariants survive runtime mutation, not just startup-time validation.

### Five questions that need answers before Phase 6d (not before 6a/6b)

1. **Ollama feasibility on this laptop** — RAM/CPU available alongside
   WebCTRL and this simulator already running? Confirm before assuming any
   particular local model is usable.
2. **Persistence model for LLM-added equipment** — survives restart
   (written back to `config/devices/*.json`, re-validated normally) or
   session-only (in-memory, gone on restart)? Changes the implementation
   substantially.
3. **Hot-reload vs. restart-required** — bacpypes3's support for adding/
   removing objects on a *running* Application is untested in this
   project. WebCTRL also caches its own Discovery results, so a live-added
   object may not be visible until WebCTRL re-runs Discovery regardless.
   A "stage the change, apply requires restart + re-Discovery" model may
   be the correct v1 answer rather than true hot-reload — decide this
   deliberately.
4. **Auth boundary** — the current no-auth design was accepted for a
   bounded blast radius (point values, reversible). Structural
   equipment changes are a bigger blast radius. Does the LLM Console (or
   at minimum, `requires_approval` confirmations) need instructor-only
   gating now?
5. **Which model(s)** are actually available via this Ollama install? Affects
   prompt design and realistic expectations for `explain_behavior` /
   `generate_training_lab` quality.

### Explicit instructions carried forward from the original spec (still apply)
- Treat this as in-place evolution, not a rewrite.
- Preserve all verified BACnet behavior unless a change is specifically
  required and tested.
- The LLM is an assistant/orchestrator; the simulator core remains the
  deterministic source of truth. LLM output never bypasses validation to
  touch state directly.
- Do not attempt to resolve the five open questions above by assumption —
  surface them (a Settings/Diagnostics panel showing current bind
  address/port/COV status was specifically requested in the original spec
  and is a reasonable place to also surface Phase 6 open items).
- All existing tests must keep passing; add new tests for every new
  pathway, especially validation/rejection cases.

## 7.5. Phase 6a — Built and Verified

**Status: complete.** `app/llm/` (action schema, Ollama client, prompt
templates, action validator) and `app/services/` (orchestration service,
audit service) are wired into `main.py`/`api.py`, with a new LLM Console
tab in the dashboard (connection status, prompt submission, action preview/
approval, audit trail).

**What's verified live** (not just the 16 new unit/integration tests, all
passing, 40/40 total in the suite):
- `POST /api/llm/apply` with a hand-crafted bundle (simulating a real
  Ollama response) genuinely activated a `frozen_value` fault through
  `FaultManager` — confirmed via `/api/faults` immediately after.
- The Phase 6d safety boundary was tested through the live API, not just
  unit tests: an `add_equipment` bundle was cleanly rejected with a clear
  error and zero partial application — `/api/faults` still showed exactly
  the one fault from the prior successful call, nothing added.
- The audit trail correctly recorded both the successful application and
  would record a rejection the same way.
- The core app (143 objects, 16 groups, dashboard) is completely
  unaffected — confirmed via `/api/status` before and after.

**What's honestly NOT verified**: no real Ollama server is available in
the development sandbox this was built in. `ollama_client.py` is written
and tested against Ollama's documented REST API shape (`/api/generate`
with `format: "json"`, `/api/tags` for model listing) with a fully mocked
HTTP layer (`pytest-httpx`) covering success, connection failure, malformed
JSON, and schema-mismatch cases — but the first real end-to-end call to an
actual running Ollama instance has not happened. `/api/llm/status` on this
laptop, once Ollama is installed and running, is the one-click way to find
out — that's specifically why it exists.

**What was deliberately deferred, per the phasing plan**: no dashboard
tab/panel expansion beyond the LLM Console itself (Phase 6b), no snapshot/
audit-trail-beyond-LLM/trend historian (Phase 6c), no dynamic equipment
management (Phase 6d — still gated on the five open questions above). The
`generate_training_lab` and `add_equipment`/`modify_equipment`/
`remove_equipment`/`propose_dashboard_layout` intents exist in the schema
(matching the original spec's full contract) but are not in
`PHASE_6A_ALLOWED_INTENTS` — attempting them is cleanly rejected, not
silently ignored or partially handled.

---

## 8. Delivered Artifacts (chronological — use the latest)

| File | Contents |
|---|---|
| ~~`aci-bacnet-sim-phase2.zip`~~ through ~~`aci-bacnet-sim-phase5-service.zip`~~ | Superseded — historical drafts; do not deploy from zips |
| **`github.com/JeffJenkinsBAS/bacnet_simulator` (private), `main` branch** | **Current app baseline** — the repo is the single source of truth as of 2026-07-17 |
| `SIMULATION_AUDIT.md` (in repo) | Full equipment/BACnet audit + fix status |
| `ACI_BACnet_Simulator_Point_Mapping.xlsx` | Every BACnet object, address, direction, description — generated from live config |
| `NEXT_STEPS_INTEGRATION_TESTING.md` | Bench laptop deployment step sequence |
| `PACKAGING.md` (inside the zip) | Full install/firewall/service/troubleshooting reference |
| `PHASE6_REVIEW.md` | Full architecture review and phasing rationale for §7 |
| **This file** | Current authoritative status — start here |

---

## 9. Immediate Next Steps

1. **Finish the bench deployment** per `NEXT_STEPS_INTEGRATION_TESTING.md`
   (verified topology): set the simulator NIC to `192.168.168.201/24` and
   confirm `peer_allowlist` + `write_source_allowlist` = `192.168.168.200`,
   add the UDP 47808 firewall rule (scoped to `192.168.168.200`), confirm
   the bench WebCTRL (`192.168.168.200`, port 47809) targets
   `192.168.168.201:47808`, install the service, reboot, run Discovery,
   and finish with the AHU-1 → VAV-1
   smoke test visible in both WebCTRL and the dashboard. Watch the
   dashboard's Blocked-requests counter and the COV Subscriptions panel
   during Discovery — they now tell you exactly what WebCTRL is doing.
2. **Verify the duplicate-instance startup check behaves on the real
   bench NIC** (see §6) — first boot on the bench, confirm `messages_in`
   climbs during Discovery; if BACnet is silent, disable the check and
   report back.
3. **Install Ollama and pull a model** on this laptop, then use the AI
   Console's connection test (`/api/llm/status` → `connected: true`) —
   this also answers Phase 6d open questions #1 and #5.
4. Then: use Phase 6a for real training, and take Phase 6b's remaining
   scope (trends/alarms need the 6c historian). The GUI foundation for
   6b is done (liquid-glass rebuild, §0).
5. Do not start Phase 6d (dynamic equipment management) until the five
   questions in §7 have real answers — surface them rather than guess.
