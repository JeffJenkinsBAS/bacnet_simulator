# Phase 6 Review: LLM Orchestration + Dashboard Upgrade

Review of the submitted Phase 6 handoff spec, before any implementation
starts — per that spec's own instruction to summarize architecture and
identify the minimum-change path first.

---

## 1. What's Good About This Plan

- **The `llm_action_bundle` schema is sound.** Structured JSON only, an
  explicit `requires_approval` flag, a bounded `allowed_intents` /
  `allowed_action_types` list, `confidence` reported rather than implied —
  this is the right shape for letting an LLM propose changes to a system
  that has to stay deterministic. It fits directly onto the existing
  `FaultManager` / `ScenarioEngine` APIs for the scenario/fault half of the
  action set (see §4).
- **The explicit non-roles list matters and is correctly scoped** — "direct
  raw BACnet command issuer," "unsupervised self-learning controller,"
  "background autonomous optimizer without user approval" are exactly the
  failure modes that would actually be dangerous in a training tool people
  are learning real commissioning practice from. Good instinct to rule
  these out up front rather than after something goes wrong.
- **"Extend, do not rewrite" as a stated principle**, plus the `do_not_break`
  list, correctly identifies `GroupView` as the seam to build through —
  that's accurate; it's the same seam that absorbed the single-device
  merge and the fault-injection system without touching equipment models.

## 2. Scope Reality Check

This is not a small addition. Read literally, this spec is:

- A new LLM orchestration layer (6 new files under `app/llm/`)
- A new services layer (4 new files under `app/services/`)
- A structured action validation/approval system
- A snapshot/restore subsystem for complete simulator state
- An audit logging subsystem (four separate log categories)
- A dashboard rebuild from 4 tabs to 10+ tabs/panels, including a Trends
  &amp; Alarms panel that implies **a time-series historian that doesn't
  exist yet** — the current dashboard polls live values every 2.5s with
  zero retention, nothing to trend against
- Dynamic (add/modify/remove) equipment management at runtime

Phases 1 through 5 combined got us to: one architecture doc, one working
BACnet core, 16 equipment groups, a fault/scenario library, and Windows
packaging. This spec is comparable in size to that entire body of work, not
an incremental add-on to it. Worth sizing it that way going in rather than
discovering it three files into implementation.

## 3. Sequencing Concern — Read This Before Anything Else

**We are mid-deployment to the physical bench laptop right now, with an
unresolved field question still open** (whether WebCTRL's own BACnet/IP
connection competes for UDP 47808 on the same machine — see `HANDOFF.md`
§2). The core simulator has not yet been confirmed working against real
WebCTRL traffic outside the development sandbox.

Starting a large new subsystem now means: if something breaks during
initial WebCTRL integration testing, we won't cleanly know whether it's a
Phase 5 issue that was always going to surface, or a Phase 6 regression,
because both would be new and unverified against the real bench at the same
time. **Recommendation: finish resolving the port question and get through
at least the first smoke test (AHU-1 command → VAV-1 response, visible in
WebCTRL) before merging Phase 6 work.** Phase 6 development can start in
parallel on a branch/copy if there's appetite to keep moving, but I'd hold
off making it the primary working tree until the field deployment is a
known-good baseline. This is exactly the kind of "verify against a live
process, not just tests" discipline that caught the fan/pump proof-delay
bug during Phase 3 — the same logic applies to not stacking two unverified
things on top of each other.

## 4. Risk-Tiered Breakdown of the Actual Work

Not all of this spec carries the same risk. Sorting the `allowed_intents`
by what they touch:

**Low risk — maps directly onto existing, already-validated machinery:**
- `create_scenario`, `inject_fault`, `clear_fault`, `set_initial_condition`,
  `adjust_parameter` (weather), `explain_behavior`, `summarize_events`
- These are just `ScenarioEngine`/`FaultManager`/`SiteModel` calls with a
  validation layer and an approval step in front. The LLM proposes a
  scenario JSON or a fault activation shaped like what `scenario.py`/
  `faults.py` already accept; the orchestration layer validates it against
  the *existing* Pydantic schemas (`Scenario`, `FaultType`) before calling
  the *existing* `.start()` / `.set_fault()` methods. No changes to the
  BACnet core, no changes to `do_not_break` items. This is genuinely
  additive.

**High risk — touches the architecture's hardest-won invariants:**
- `add_equipment`, `modify_equipment`, `remove_equipment`
- These imply **runtime mutation of the BACnet object model** — something
  that currently only happens once, at startup, via
  `generate_phase3_configs.py` writing static JSON that
  `validate_equipment_groups()` checks *before* any BACnet objects are
  constructed. Supporting this at runtime raises real open questions (next
  section) and directly intersects three `do_not_break` items at once:
  object instance mapping, object naming uniqueness, and the
  already-tested equipment behavior. This is the piece I'd want explicit
  sign-off on before writing code, not something to build by extrapolating
  from the spec alone.

**Medium risk — new subsystem, but self-contained:**
- Snapshot/restore, audit logging, trend history — all genuinely new (none
  of this exists today), but none of them touch the live BACnet transport
  or object model while running. Safer to build than equipment mutation,
  still needs real design (what exactly does a "snapshot" capture? See
  below).

## 5. Open Questions Before Implementation Starts

These aren't things I should guess at, per the project's own established
practice of flagging assumptions rather than inventing them:

1. **Ollama on this laptop — is it feasible?** Local LLM inference
   typically wants 8GB+ RAM just for a small model, on a machine that's
   already running WebCTRL, this simulator, and whatever else the bench
   needs. Do we know the bench laptop's specs, or a second machine's, if
   Ollama is meant to run somewhere other than the simulator's own host?
2. **Persistence model for LLM-added equipment.** If the LLM adds a VAV
   group mid-session, does that change survive a restart (written back to
   `config/devices/*.json`, re-validated the normal way) or is it
   session-only (in-memory, gone on restart)? This changes the
   implementation substantially and isn't specified.
3. **Hot-reload vs. restart-required for topology changes.** bacpypes3's
   support for adding/removing objects on a *running* Application hasn't
   been tested in this project. Given WebCTRL caches its own Discovery
   results, a live-added object might not even be visible to WebCTRL until
   it re-runs Discovery anyway — a "stage the change, apply requires an
   app restart + re-Discovery" model may be the safer v1 regardless of
   what bacpypes3 technically supports. Worth deciding deliberately rather
   than defaulting to "make it live" because it sounds better.
4. **Auth boundary.** The current API has no authentication at all — accepted
   so far because the blast radius was "mess with simulated point values,"
   bounded and reversible via Stop All Simulation. An LLM that can
   structurally add/remove equipment is a bigger blast radius. Does this
   need instructor-only gating now (even something simple, like a local
   PIN before the LLM Console or before `requires_approval` actions can be
   confirmed), given students may have access to the same laptop or
   network?
5. **Which model(s)** are actually pulled/available via this Ollama
   install? Affects prompt design and what capability level is realistic
   to expect from `explain_behavior`/`generate_training_lab`.

## 6. Recommended Phasing

Given the risk tiers above, I'd split this rather than build it as one
pass:

- **Phase 6a — LLM-assisted scenario/fault generation.** `app/llm/`
  (Ollama client, action schema, validator), `app/services/
  orchestration_service.py` and `audit_service.py`, an LLM Console panel,
  wired only to the low-risk intents (§4). No equipment topology changes.
  This alone delivers real value — "describe a training scenario in plain
  language, get a reviewable scenario file" — without touching anything on
  the `do_not_break` list.
- **Phase 6b — Dashboard upgrade.** The tab/panel expansion, minus Trends
  &amp; Alarms (needs the historian) and minus anything LLM-topology-related.
  Can happen in parallel with 6a since it's mostly additive UI on existing
  endpoints.
- **Phase 6c — Snapshot/restore + audit trail + trend historian.** New,
  self-contained subsystems. Unlocks the "Trends & Alarms" panel properly
  once built, rather than shipping a panel with nothing to show.
- **Phase 6d — Dynamic equipment management.** The `add_equipment`/
  `modify_equipment`/`remove_equipment` intents, once §5's open questions
  have answers. Gets its own test suite specifically proving the
  `do_not_break` invariants (instance uniqueness, name uniqueness) survive
  runtime mutation, not just startup-time validation.

This also means Phase 6a can start delivering value almost immediately
without waiting on the harder design questions in §5.

## 7. What I Need From You Before Starting

1. Confirmation on sequencing — hold for the field deployment result, or
   proceed with Phase 6 on a separate track now?
2. Answers to (or "figure it out as you go" permission for) the five open
   questions in §5 — particularly #2 and #3, since they determine how
   `app/services/equipment_template_service.py` actually needs to work.
3. Which Phase 6 sub-phase to start with — my recommendation is 6a, since
   it's lowest-risk and reuses the most existing, already-verified code.
