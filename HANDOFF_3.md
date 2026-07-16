# ACI BACnet Building Simulation Platform — Handoff Document (v2)

Prepared for Jeff Jenkins, Automated Controls Inc. Written to be
self-contained for a fresh agent session with no prior context — if
you're picking this up cold, this document plus the codebase itself
should be enough to continue safely.

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

**Phases 1–5 are complete**: architecture, equipment models (AHU, 3
chillers, 3 boilers, exhaust fan, 5 VAV zones, site conditions), fault
injection (11 mechanics), scenario engine (6 shipped scenarios), Instructor
Panel dashboard, and Windows packaging/service scripts. 24 automated tests
passing. All of it verified against a live running instance during
development, not just the test suite — see §5.

**Physical deployment to the bench laptop, in progress:**
- ✅ Project copied to the laptop.
- ✅ Found and disabled a conflicting service (a previously-purchased SCADA
  Systems BACnet Simulator was squatting on UDP 47808).
- ✅ **The WebCTRL-vs-simulator port question is resolved: WebCTRL and the
  simulator share standard UDP 47808 on the same laptop with no conflict.**
  No port change needed — `config/network.json`'s `udp_port` should stay at
  the default `47808`, and the standard automatic WebCTRL Discovery
  workflow applies (no manual SiteBuilder device entry required).
- ⬜ **Not confirmed complete as of this handoff**: `bind_address` set to
  the laptop's real NIC IP, Python install check,
  `install.bat`/`install_offline.bat`, the manual `run.bat` smoke
  test, the Windows Firewall rule, NSSM service installation, the
  post-reboot auto-start check, WebCTRL-side Discovery, and the first
  real read/write smoke test (AHU-1 command → VAV-1 response visible in
  WebCTRL). **Do not assume these are done — confirm before treating the
  bench deployment as finished.** The full sequence is in
  `NEXT_STEPS_INTEGRATION_TESTING.md`.

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
- **Proposed device instance: 242000.** A proposed default, matching the
  `2420xx` block Jeff designated — **still not explicitly confirmed as
  final.**
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
  network.json                     bind_address / udp_port — udp_port confirmed 47808 (shared with WebCTRL, no conflict); bind_address still needs to be the laptop's real NIC IP
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
- BACnet COV subscription **setup/acknowledgment** confirmed working. COV
  **notification delivery on value change** could not be confirmed working
  in the time spent on it. Jeff's plan to run low refresh timers (forcing
  polling) sidesteps this in practice — not fixed, worked around by
  design choice.

---

## 6. Known Open Items Carried Forward

| Item | Status |
|---|---|
| Supervisory device instance 242000 | Proposed default, **not confirmed** by Jeff |
| COV notification delivery | Subscribe/ack confirmed; notification-on-change not confirmed working |
| Duplicate BACnet instance / incorrect network number / incorrect units faults | Not implemented — flagged in `faults.py`'s docstring |
| Occupancy modeling | Not implemented |
| Completion criteria / student objectives (scenarios) | Informational text only, not auto-graded |
| "Status Indicator" relay (Simulation Manager) | Assumed out of scope, same category as confirmed-out-of-scope "Safety Trip" — **not explicitly confirmed** the same way |
| NSSM binary | Not shipped, must be downloaded manually (`tools/nssm/PUT_NSSM_EXE_HERE.txt`) |
| Windows batch/service scripts | Written and reviewed, execution-tested only as part of the current live deployment |
| Full bench deployment (Firewall rule → service → reboot → WebCTRL Discovery → smoke test) | **Not confirmed complete** — see §2 |

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
| ~~`aci-bacnet-sim-phase2.zip`~~ through ~~`aci-bacnet-sim-phase4.zip`~~ | Superseded drafts |
| ~~`aci-bacnet-sim-phase4-packaging.zip`~~ | Superseded — pre-service-scripts |
| **`aci-bacnet-sim-phase5-service.zip`** | **Current app baseline.** Full app + fault/scenario library + Instructor Panel + Windows packaging/service scripts |
| `ACI_BACnet_Simulator_Point_Mapping.xlsx` | Every BACnet object, address, direction, description — generated from live config |
| `NEXT_STEPS_INTEGRATION_TESTING.md` | Bench laptop deployment step sequence |
| `PACKAGING.md` (inside the zip) | Full install/firewall/service/troubleshooting reference |
| `PHASE6_REVIEW.md` | Full architecture review and phasing rationale for §7 |
| **This file** | Current authoritative status — start here |

---

## 9. Immediate Next Steps

1. **Confirm the remaining Phase 5 deployment items in §2 are actually
   done** — if not, finish `NEXT_STEPS_INTEGRATION_TESTING.md` first,
   ending with the AHU-1/VAV-1 smoke test visible in both WebCTRL and the
   simulator dashboard.
2. **On the actual laptop, install Ollama and pull a model**, then use the
   LLM Console's connection test to confirm `/api/llm/status` reports
   `connected: true` — this is the first real-world verification the dev
   sandbox couldn't do (see §7.5).
3. Once both of those are confirmed, Phase 6b (dashboard tab/panel
   expansion, minus Trends & Alarms) is the next reasonable slice, or
   start using Phase 6a for real — it's functional now.
4. Do not start Phase 6d (dynamic equipment management) until the five
   questions in §7 have real answers — surface them rather than guess.
