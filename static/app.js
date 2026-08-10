"use strict";

const $ = (id) => document.getElementById(id);
const CORE_POLL_MS = 3000;
const STALE_AFTER_MS = 10000;
const REQUEST_TIMEOUT_MS = 7000;
const LONG_REQUEST_TIMEOUT_MS = 70000;

const VIEW_TITLES = {
  twin: "Digital Twin Command Center",
  "duct-static": "AHU-1 Command Center",
  equipment: "Equipment Explorer",
  operations: "Instructor Operations",
  ai: "AI Orchestration Console",
  logs: "System Logs",
};

const HASH_TO_VIEW = {
  "#digital-twin": "twin",
  "#duct-static": "duct-static",
  "#equipment": "equipment",
  "#operations": "operations",
  "#ai-console": "ai",
  "#logs": "logs",
};

const VIEW_TO_HASH = Object.fromEntries(
  Object.entries(HASH_TO_VIEW).map(([hash, view]) => [view, hash]),
);

const ALLOWED_STATES = new Set(["idle", "running", "starting", "tracking", "inhibited", "failure"]);
const AIR_MODES = new Set(["off", "ventilation", "cooling", "heating"]);
const TRANSPORT_FAULTS = new Set([
  "device_offline",
  "slow_response",
  "write_rejected",
  "intermittent_comm",
]);

const FAULT_SPECS = {
  frozen_value: {
    label: "Frozen value",
    note: "Capture and hold the selected point at its current reading.",
  },
  offset: {
    label: "Sensor offset",
    note: "Add a fixed engineering-unit offset to the selected output.",
    parameter: { key: "offset", label: "OFFSET AMOUNT", help: "Signed engineering-unit offset.", step: "0.1" },
  },
  drift: {
    label: "Sensor drift",
    note: "Accumulate a gradual signed error on the selected output.",
    parameter: { key: "rate_per_second", label: "DRIFT RATE / SIM SECOND", help: "Signed engineering units per simulated second.", step: "0.001" },
  },
  reliability_fail: {
    label: "Reliability failure",
    note: "Return a defined bad reading and flag the point reliability.",
    parameter: { key: "value", label: "FAILED READING", help: "Out-of-range value shown during failure.", step: "0.1" },
  },
  stuck_value: {
    label: "Stuck value",
    note: "Pin the selected input or output at the entered value.",
    parameter: { key: "value", label: "STUCK VALUE", help: "Required fixed value.", step: "0.1" },
  },
  reversed_actuator: {
    label: "Reversed actuator",
    note: "Reverse a 0\u2013100% actuator command.",
  },
  forced_status: {
    label: "Forced status",
    note: "Force a simulated binary status independently of its command.",
    parameter: { key: "value", label: "STATUS (0 OR 1)", help: "Use 1 for on/proved or 0 for off/not proved.", min: "0", max: "1", step: "1" },
  },
  safety_bypass: {
    label: "Safety bypass / failed device",
    note: "Training-only fault: defeat one AHU automatic safety so its catastrophic consequence can be demonstrated. The separate manual trip inputs remain effective.",
  },
  device_offline: {
    label: "Device offline",
    note: "Transport-level fault: the entire BACnet device stops responding.",
  },
  slow_response: {
    label: "Slow response",
    note: "Transport-level fault: delay all BACnet responses.",
    parameter: { key: "delay_seconds", label: "RESPONSE DELAY (SECONDS)", help: "Delay added to every response.", min: "0.1", max: "30", step: "0.1" },
  },
  write_rejected: {
    label: "Write rejected",
    note: "Transport-level fault: reject every BACnet write.",
  },
  intermittent_comm: {
    label: "Intermittent communication",
    note: "Transport-level fault: randomly drop a share of requests.",
    parameter: { key: "drop_probability", label: "DROP PROBABILITY", help: "Decimal probability from 0.01 to 1.00.", min: "0.01", max: "1", step: "0.01" },
  },
};

const FALLBACK_POSITIONS = {
  "ACI-SIM-SITE": { floor: "roof", x: 69, y: 14 },
  "ACI-SIM-EF-1": { floor: "roof", x: 73, y: 23 },
  "ACI-SIM-CHW-PLANT": { floor: "plant", x: 24, y: 71 },
  "ACI-SIM-CHILLER-1": { floor: "plant", x: 17, y: 61 },
  "ACI-SIM-CHILLER-2": { floor: "plant", x: 26, y: 58 },
  "ACI-SIM-CHILLER-3": { floor: "plant", x: 34, y: 55 },
  "ACI-SIM-BOILER-MGR": { floor: "plant", x: 37, y: 76 },
  "ACI-SIM-BOILER-1": { floor: "plant", x: 20, y: 78 },
  "ACI-SIM-BOILER-2": { floor: "plant", x: 29, y: 76 },
  "ACI-SIM-BOILER-3": { floor: "plant", x: 38, y: 72 },
  "ACI-SIM-AHU-1": { floor: "level-1", x: 52, y: 61 },
  "ACI-SIM-VAV-1": { floor: "level-1", x: 69, y: 62 },
  "ACI-SIM-VAV-2": { floor: "level-2", x: 54, y: 46 },
  "ACI-SIM-VAV-3": { floor: "level-2", x: 70, y: 44 },
  "ACI-SIM-VAV-4": { floor: "level-3", x: 52, y: 31 },
  "ACI-SIM-VAV-5": { floor: "level-3", x: 72, y: 33 },
};

const state = {
  activeView: "twin",
  floor: "all",
  status: null,
  points: [],
  faults: [],
  commandCenter: null,
  ductStatic: null,
  locations: [],
  summary: { running: 0, failures: 0, tracking: 0, starting: 0, inhibited: 0, idle: 0 },
  scenarios: [],
  faultTypes: [],
  selectedLocationId: null,
  lastUpdatedAt: 0,
  online: null,
  coreInFlight: false,
  ductStaticInFlight: false,
  ductStaticErrorMessage: "",
  ahuTelemetryEndpoint: "",
  ahuSelectedComponent: "supply-fan",
  ahuMotionPaused: false,
  ahuPreviousAlarmKey: "",
  ahuCatastrophicTimer: null,
  logsInFlight: false,
  auditInFlight: false,
  llmBundle: null,
  weatherDirty: false,
  pidTuningDirty: false,
  groupSignature: "",
  modalResolve: null,
  modalInvoker: null,
};
const airflowNodes = new Map();

function createElement(tag, options = {}, children = []) {
  const element = document.createElement(tag);
  if (options.className) element.className = options.className;
  if (options.text !== undefined && options.text !== null) element.textContent = String(options.text);
  if (options.title) element.title = options.title;
  if (options.type) element.type = options.type;
  if (options.attrs) {
    for (const [name, value] of Object.entries(options.attrs)) {
      if (value !== undefined && value !== null) element.setAttribute(name, String(value));
    }
  }
  for (const child of Array.isArray(children) ? children : [children]) {
    if (child instanceof Node) element.appendChild(child);
    else if (child !== undefined && child !== null) element.appendChild(document.createTextNode(String(child)));
  }
  return element;
}

function createIcon(iconName, className = "") {
  return createElement("i", {
    className: `fa-solid ${iconName}${className ? ` ${className}` : ""}`,
    attrs: { "aria-hidden": "true" },
  });
}

function replaceChildren(element, children = []) {
  element.replaceChildren(...(Array.isArray(children) ? children : [children]));
}

function setText(id, value, fallback = "\u2014") {
  const element = $(id);
  if (element) element.textContent = value === undefined || value === null || value === "" ? fallback : String(value);
}

function clampNumber(value, minimum, maximum, fallback) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.min(maximum, Math.max(minimum, numeric));
}

function normalizeState(value) {
  const normalized = String(value || "idle").trim().toLowerCase().replace(/[\s_]+/g, "-");
  return ALLOWED_STATES.has(normalized) ? normalized : "idle";
}

function normalizeFloor(value) {
  const normalized = String(value || "").trim().toLowerCase().replace(/[\s_]+/g, "-");
  const aliases = {
    all: "all",
    roof: "roof",
    rooftop: "roof",
    "level-3": "level-3",
    level3: "level-3",
    "floor-3": "level-3",
    "3": "level-3",
    "level-2": "level-2",
    level2: "level-2",
    "floor-2": "level-2",
    "2": "level-2",
    "level-1": "level-1",
    level1: "level-1",
    "floor-1": "level-1",
    ground: "level-1",
    "1": "level-1",
    plant: "plant",
    mechanical: "plant",
    basement: "plant",
  };
  return aliases[normalized] || "level-1";
}

function titleCase(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const days = Math.floor(seconds / 86400);
  const hours = String(Math.floor((seconds % 86400) / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const remainder = String(seconds % 60).padStart(2, "0");
  return `${days ? `${days}d ` : ""}${hours}:${minutes}:${remainder}`;
}

function formatValue(value, digits = 1) {
  if (value === true) return "ON";
  if (value === false) return "OFF";
  if (value === null || value === undefined || value === "") return "\u2014";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : String(value);
}

function shortValue(value) {
  if (value === null || value === undefined) return "\u2014";
  if (Array.isArray(value)) return value.map(shortValue).join(", ");
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, nestedValue]) => `${titleCase(key)}: ${shortValue(nestedValue)}`)
      .join("; ");
  }
  return String(value);
}

function formatTimestamp(epochSeconds) {
  const numeric = Number(epochSeconds);
  if (!Number.isFinite(numeric)) return "\u2014";
  return new Date(numeric * 1000).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

async function fetchJSON(url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      cache: "no-store",
      ...options,
      signal: controller.signal,
    });
    const raw = await response.text();
    let data = {};
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch {
        data = { error: raw };
      }
    }
    if (!response.ok) {
      const detail = data.error || data.detail || `${response.status} ${response.statusText}`;
      throw new Error(typeof detail === "string" ? detail : shortValue(detail));
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)} seconds`);
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

function toast(message, kind = "success") {
  const region = $("toast-region");
  const iconName = kind === "error" ? "fa-circle-xmark" : kind === "warning" ? "fa-triangle-exclamation" : "fa-circle-check";
  const item = createElement("div", {
    className: `toast toast-${kind}`,
    attrs: { role: kind === "error" ? "alert" : "status" },
  });
  const close = createElement("button", { type: "button", title: "Dismiss notification", attrs: { "aria-label": "Dismiss notification" } }, createIcon("fa-xmark"));
  close.addEventListener("click", () => item.remove());
  item.append(createIcon(iconName), createElement("span", { text: message }), close);
  region.appendChild(item);
  while (region.children.length > 4) region.firstElementChild.remove();
  window.setTimeout(() => item.remove(), 6000);
}

function confirmAction(title, message, confirmLabel = "CONFIRM") {
  const dialog = $("confirm-dialog");
  if (dialog.open) dialog.close("cancel");
  state.modalInvoker = document.activeElement;
  setText("confirm-title", title);
  setText("confirm-message", message);
  setText("confirm-accept", confirmLabel);
  dialog.returnValue = "cancel";
  dialog.showModal();
  $("confirm-cancel").focus();
  return new Promise((resolve) => {
    state.modalResolve = resolve;
  });
}

function closeConfirmation(result) {
  const dialog = $("confirm-dialog");
  if (dialog.open) dialog.close(result ? "confirm" : "cancel");
}

function finishConfirmation() {
  const result = $("confirm-dialog").returnValue === "confirm";
  if (state.modalResolve) {
    const resolve = state.modalResolve;
    state.modalResolve = null;
    resolve(result);
  }
  if (state.modalInvoker instanceof HTMLElement && state.modalInvoker.isConnected) {
    state.modalInvoker.focus();
  }
  state.modalInvoker = null;
}

async function runAction(button, action, successMessage, options = {}) {
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  }
  try {
    const result = await action();
    if (successMessage) toast(successMessage);
    if (options.refreshLibrary) await loadLibraries();
    if (options.refresh !== false) await refreshCore(true);
    return result;
  } catch (error) {
    toast(`Action failed: ${error.message}`, "error");
    return null;
  } finally {
    if (button) {
      button.disabled = !state.online;
      button.removeAttribute("aria-busy");
    }
  }
}

function setMutationAvailability(enabled) {
  const ids = [
    "stop-all", "restart-simulation", "engine-control", "speed-control", "scenario-stop", "scenario-reset",
    "fault-clear-all", "force-apply", "force-release", "proposal-apply",
    "pid-apply", "pid-reset-memory", "pid-restore-defaults",
  ];
  for (const id of ids) {
    const element = $(id);
    if (element) element.disabled = !enabled;
  }
  for (const formId of ["weather-form", "fault-form", "force-form", "pid-tuning-form"]) {
    const form = $(formId);
    if (!form) continue;
    for (const control of form.elements) control.disabled = !enabled;
  }
  renderActiveScenario();
}

function setConnection(online, message = "") {
  const changed = state.online !== online;
  state.online = online;
  $("side-status-dot").className = `status-dot ${online ? "is-online" : "is-offline"}`;
  setText("side-link-label", online ? "LINK ONLINE" : "LINK DOWN");
  setMutationAvailability(online);
  if (changed && online) toast("Live simulator telemetry restored");
  updateStaleBanner(message);
}

function updateStaleBanner(message = "") {
  const banner = $("stale-banner");
  const ageMs = state.lastUpdatedAt ? Date.now() - state.lastUpdatedAt : Infinity;
  const isStale = state.online === false || (state.lastUpdatedAt > 0 && ageMs > STALE_AFTER_MS);
  if (!isStale) {
    banner.hidden = true;
    return;
  }
  const ageText = state.lastUpdatedAt ? `${Math.floor(ageMs / 1000)} seconds ago` : "never";
  setText("stale-message", message || `Telemetry is stale. Last successful update: ${ageText}. Controls are disabled until the link recovers.`);
  banner.hidden = false;
}

function updateClock() {
  setText("local-clock", new Date().toLocaleTimeString([], { hour12: false }));
  if (state.lastUpdatedAt) {
    setText("last-sync", new Date(state.lastUpdatedAt).toLocaleTimeString([], { hour12: false }));
  }
  updateStaleBanner();
}

function activateView(view, updateHash = true) {
  if (!VIEW_TITLES[view]) return;
  state.activeView = view;
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.viewPanel !== view;
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  setText("view-title", VIEW_TITLES[view]);
  document.title = `${VIEW_TITLES[view]} \u00B7 ACI BACnet`;
  if (updateHash && window.location.hash !== VIEW_TO_HASH[view]) {
    history.replaceState(null, "", VIEW_TO_HASH[view]);
  }
  if (view === "equipment") renderEquipment();
  if (view === "duct-static") refreshDuctStatic();
  if (view === "operations") {
    renderScenarioLibrary();
    renderFaults();
  }
  if (view === "ai") {
    refreshLlmStatus();
    refreshAudit();
  }
  if (view === "logs") refreshLogs();
}

function fallbackPosition(groupId, index) {
  if (FALLBACK_POSITIONS[groupId]) return FALLBACK_POSITIONS[groupId];
  return {
    floor: index % 2 ? "level-2" : "level-1",
    x: 45 + ((index * 9) % 38),
    y: 30 + ((index * 11) % 36),
  };
}

function inferComponentType(groupId) {
  const id = String(groupId || "").toLowerCase();
  if (id.includes("vav")) return "vav";
  if (id.includes("chiller")) return "chiller";
  if (id.includes("boiler")) return "boiler";
  if (id.includes("ahu")) return "ahu";
  if (id.includes("ef")) return "exhaust_fan";
  if (id.includes("plant")) return "plant";
  if (id.includes("site")) return "site";
  return "equipment";
}

function inferFallbackState(groupId) {
  const groupPoints = state.points.filter((point) => point.group === groupId);
  const hasFault = state.faults.some((fault) => fault.group_id === groupId);
  if (hasFault) return "failure";
  const runningSignal = groupPoints.find((point) => {
    const alias = String(point.alias || "").toLowerCase();
    return /(run|status|proof|enable)/.test(alias) && Number(point.present_value) > 0;
  });
  if (runningSignal) return "running";
  return "idle";
}

function deriveFallbackLocations() {
  const groups = state.status?.groups || [];
  return groups.map((group, index) => {
    const position = fallbackPosition(group.group_id, index);
    return {
      id: group.group_id,
      label: group.group_id.replace("ACI-SIM-", "").replaceAll("-", " "),
      groupId: group.group_id,
      componentType: inferComponentType(group.group_id),
      floor: position.floor,
      x: position.x,
      y: position.y,
      state: inferFallbackState(group.group_id),
      diagnosticType: null,
      mismatchSeconds: 0,
      thresholdSeconds: 0,
      values: {},
      message: group.description || "Fallback equipment location derived from the simulator registry.",
    };
  });
}

function normalizeCommandCenter(raw) {
  const building = raw && typeof raw.building === "object" ? raw.building : {};
  const rawLocations = Array.isArray(raw?.locations) ? raw.locations : [];
  const locations = rawLocations.map((location, index) => {
    const groupId = String(location.group_id || location.groupId || location.id || `location-${index}`);
    const fallback = fallbackPosition(groupId, index);
    const rawAir = location.air_delivery && typeof location.air_delivery === "object"
      ? location.air_delivery
      : null;
    const rawSpace = location.space && typeof location.space === "object" ? location.space : {};
    const airMode = String(rawAir?.mode || "off").toLowerCase();
    return {
      id: String(location.id || groupId),
      label: String(location.label || groupId),
      groupId,
      componentType: String(location.component_type || location.componentType || inferComponentType(groupId)),
      floor: normalizeFloor(location.floor || fallback.floor),
      x: clampNumber(location.x, 2, 98, fallback.x),
      y: clampNumber(location.y, 2, 98, fallback.y),
      space: {
        width: clampNumber(rawSpace.width, 1, 40, 9),
        height: clampNumber(rawSpace.height, 1, 40, 7),
        angle: clampNumber(rawSpace.angle, -180, 180, 0),
        offsetX: clampNumber(rawSpace.offset_x ?? rawSpace.offsetX, -20, 20, 0),
        offsetY: clampNumber(rawSpace.offset_y ?? rawSpace.offsetY, -20, 20, 0),
      },
      state: normalizeState(location.state),
      diagnosticType: location.diagnostic_type || location.diagnosticType || null,
      mismatchSeconds: clampNumber(location.mismatch_seconds ?? location.mismatchSeconds, 0, Number.MAX_SAFE_INTEGER, 0),
      thresholdSeconds: clampNumber(location.threshold_seconds ?? location.thresholdSeconds, 0, Number.MAX_SAFE_INTEGER, 0),
      values: location.values && typeof location.values === "object" ? location.values : {},
      airDelivery: rawAir ? {
        active: Boolean(rawAir.active),
        mode: AIR_MODES.has(airMode) ? airMode : "off",
        source: String(rawAir.conditioning_source || "neutral"),
        airflowCfm: clampNumber(rawAir.airflow_cfm, 0, 100000, 0),
        airflowFraction: clampNumber(rawAir.airflow_fraction, 0, 1, 0),
        dischargeTempF: Number.isFinite(Number(rawAir.discharge_temp_f)) ? Number(rawAir.discharge_temp_f) : null,
        zoneTempF: Number.isFinite(Number(rawAir.zone_temp_f)) ? Number(rawAir.zone_temp_f) : null,
        zoneHumidityPct: Number.isFinite(Number(rawAir.zone_humidity_pct)) ? Number(rawAir.zone_humidity_pct) : null,
        temperatureDeltaF: Number.isFinite(Number(rawAir.temperature_delta_f)) ? Number(rawAir.temperature_delta_f) : null,
        sensibleBtuh: Number.isFinite(Number(rawAir.sensible_btuh)) ? Number(rawAir.sensible_btuh) : null,
        zoneAreaSqft: Number.isFinite(Number(rawAir.zone_area_sqft)) ? Number(rawAir.zone_area_sqft) : null,
        designMaxAirflowCfm: Number.isFinite(Number(rawAir.design_max_airflow_cfm)) ? Number(rawAir.design_max_airflow_cfm) : null,
        occupiedMinimumAirflowCfm: Number.isFinite(Number(rawAir.occupied_minimum_airflow_cfm)) ? Number(rawAir.occupied_minimum_airflow_cfm) : null,
        heatingMinimumAirflowCfm: Number.isFinite(Number(rawAir.heating_minimum_airflow_cfm)) ? Number(rawAir.heating_minimum_airflow_cfm) : null,
        heatingMaximumAirflowCfm: Number.isFinite(Number(rawAir.heating_maximum_airflow_cfm)) ? Number(rawAir.heating_maximum_airflow_cfm) : null,
        coolingMinimumAirflowCfm: Number.isFinite(Number(rawAir.cooling_minimum_airflow_cfm)) ? Number(rawAir.cooling_minimum_airflow_cfm) : null,
        coolingMaximumAirflowCfm: Number.isFinite(Number(rawAir.cooling_maximum_airflow_cfm)) ? Number(rawAir.cooling_maximum_airflow_cfm) : null,
        damperCommandPct: Number.isFinite(Number(rawAir.damper_command_pct)) ? Number(rawAir.damper_command_pct) : null,
        damperFeedbackPct: Number.isFinite(Number(rawAir.damper_position_feedback_pct)) ? Number(rawAir.damper_position_feedback_pct) : null,
        dependencies: rawAir.dependencies && typeof rawAir.dependencies === "object" ? rawAir.dependencies : {},
      } : null,
      message: String(location.message || ""),
    };
  });

  const effectiveLocations = locations.length ? locations : deriveFallbackLocations();
  const computedSummary = {
    running: effectiveLocations.filter((location) => location.state === "running").length,
    failures: effectiveLocations.filter((location) => location.state === "failure").length,
    tracking: effectiveLocations.filter((location) => location.state === "tracking").length,
    starting: effectiveLocations.filter((location) => location.state === "starting").length,
    inhibited: effectiveLocations.filter((location) => location.state === "inhibited").length,
    idle: effectiveLocations.filter((location) => location.state === "idle").length,
  };
  const rawSummary = raw && typeof raw.summary === "object" ? raw.summary : {};
  const summary = {};
  for (const key of Object.keys(computedSummary)) {
    summary[key] = clampNumber(rawSummary[key], 0, Number.MAX_SAFE_INTEGER, computedSummary[key]);
  }

  const rawPressure = building.pressure && typeof building.pressure === "object" ? building.pressure : {};
  const pressure = {
    value: Number.isFinite(Number(rawPressure.value)) ? Number(rawPressure.value) : null,
    normalLow: Number.isFinite(Number(rawPressure.normal_low)) ? Number(rawPressure.normal_low) : null,
    normalHigh: Number.isFinite(Number(rawPressure.normal_high)) ? Number(rawPressure.normal_high) : null,
    state: String(rawPressure.state || "unknown"),
  };

  return {
    building: {
      name: String(building.name || state.status?.device?.name || "ACI Training Facility"),
      asset: String(building.asset || "/static/assets/building-digital-twin.png"),
      pressure,
    },
    failureDelaySeconds: clampNumber(raw?.failure_delay_seconds, 0, Number.MAX_SAFE_INTEGER, 0),
    summary,
    airSummary: raw?.air_summary && typeof raw.air_summary === "object" ? raw.air_summary : {},
    systems: raw?.systems && typeof raw.systems === "object" ? raw.systems : {},
    locations: effectiveLocations,
    compatibilityMode: !rawLocations.length,
  };
}

function iconForLocation(location) {
  const type = String(location.componentType || "").toLowerCase();
  if (type.includes("chiller")) return "fa-snowflake";
  if (type.includes("boiler")) return "fa-fire-flame-curved";
  if (type.includes("vav")) return "fa-wind";
  if (type.includes("ahu") || type.includes("fan")) return "fa-fan";
  if (type.includes("pump") || type.includes("plant")) return "fa-water";
  if (type.includes("site") || type.includes("weather")) return "fa-cloud-sun";
  return "fa-microchip";
}

function labelForFloor(floor) {
  const labels = { roof: "Roof", "level-3": "Level 3", "level-2": "Level 2", "level-1": "Level 1", plant: "Plant" };
  return labels[floor] || titleCase(floor);
}

function findPoint(group, aliases) {
  const lowered = aliases.map((alias) => alias.toLowerCase());
  return state.points.find((point) => {
    if (group && point.group !== group) return false;
    return lowered.includes(String(point.alias || "").toLowerCase());
  });
}

function findPointByAliases(aliases) {
  const lowered = aliases.map((alias) => alias.toLowerCase());
  return state.points.find((point) => lowered.includes(String(point.alias || "").toLowerCase()));
}

function renderTwinSummary() {
  const command = state.commandCenter || normalizeCommandCenter({});
  const pressure = command.building.pressure;
  setText("building-name", command.building.name);
  if (pressure.value === null) {
    setText("metric-pressure", "\u2014");
    setText("metric-pressure-state", "Telemetry unavailable");
  } else {
    setText("metric-pressure", `${formatValue(pressure.value, 2)} in. w.c.`);
    const range = pressure.normalLow !== null && pressure.normalHigh !== null
      ? `${formatValue(pressure.normalLow, 2)}\u2013${formatValue(pressure.normalHigh, 2)} normal`
      : titleCase(pressure.state);
    setText("metric-pressure-state", range);
  }
  const oaTemp = findPointByAliases(["oa_temp", "outside_air_temperature", "outdoor_air_temp"]);
  const oaHumidity = findPointByAliases(["oa_humidity", "outside_air_humidity", "outdoor_air_humidity"]);
  setText("metric-oa-temp", oaTemp ? `${formatValue(oaTemp.present_value)} \u00B0F` : "\u2014");
  setText("metric-oa-humidity", oaHumidity ? `Humidity ${formatValue(oaHumidity.present_value)} %RH` : "Humidity \u2014");
  setText("metric-running", command.summary.running);
  setText(
    "metric-tracking",
    `${command.summary.tracking} tracking / ${command.summary.inhibited} inhibited`,
  );
  setText("metric-failures", command.summary.failures);
  setText("metric-faults", `${state.faults.length} injected fault${state.faults.length === 1 ? "" : "s"}`);
  $("building-stage").classList.toggle("has-failure", command.summary.failures > 0);
  setText("location-count", `${command.locations.length} location${command.locations.length === 1 ? "" : "s"}`);
  setText("failure-count", command.summary.failures);
  const modelMode = $("model-mode");
  modelMode.lastElementChild.textContent = command.compatibilityMode ? "COMPATIBILITY MODEL" : "LIVE MODEL";
}

function renderMarkers() {
  const layer = $("marker-layer");
  const fragment = document.createDocumentFragment();
  for (const location of state.locations) {
    const marker = createElement("button", {
      className: `equipment-marker state-${location.state}${location.id === state.selectedLocationId ? " is-selected" : ""}${state.floor !== "all" && location.floor !== state.floor ? " is-filtered" : ""}`,
      type: "button",
      title: `${location.label} \u00B7 ${titleCase(location.state)}${location.airDelivery?.active ? ` \u00B7 ${titleCase(location.airDelivery.mode)} air` : ""}`,
      attrs: {
        "aria-label": `${location.label}, ${labelForFloor(location.floor)}, ${titleCase(location.state)}`,
        "aria-pressed": location.id === state.selectedLocationId ? "true" : "false",
      },
    });
    marker.style.left = `${location.x}%`;
    marker.style.top = `${location.y}%`;
    marker.append(
      createIcon(iconForLocation(location)),
      createElement("span", { className: "marker-label", text: location.label }),
    );
    marker.addEventListener("click", () => {
      state.selectedLocationId = location.id;
      renderMarkers();
      renderInspector();
      renderFailureList();
    });
    fragment.appendChild(marker);
  }
  replaceChildren(layer, fragment);
}

function renderAirflow() {
  const layer = $("airflow-layer");
  const desiredIds = new Set();
  for (const location of state.locations) {
    if (location.componentType !== "vav" || !location.airDelivery) continue;
    desiredIds.add(location.id);
    let plume = airflowNodes.get(location.id);
    if (!plume) {
      plume = createElement("div", {
        className: "space-airflow mode-off",
        attrs: { "aria-hidden": "true" },
      });
      const image = createElement("img", {
        attrs: {
          src: "/static/assets/airflow-wisp.png",
          alt: "",
          draggable: "false",
        },
      });
      plume.appendChild(image);
      layer.appendChild(plume);
      airflowNodes.set(location.id, plume);
    }
    const mode = location.airDelivery.active ? location.airDelivery.mode : "off";
    const filtered = state.floor !== "all" && location.floor !== state.floor;
    plume.className = `space-airflow mode-${mode}${filtered ? " is-filtered" : ""}`;
    plume.style.left = `${location.x + location.space.offsetX}%`;
    plume.style.top = `${location.y + location.space.offsetY}%`;
    plume.style.width = `${location.space.width}%`;
    plume.style.height = `${location.space.height}%`;
    plume.style.setProperty("--air-angle", `${location.space.angle}deg`);
    plume.style.setProperty(
      "--air-opacity",
      String(location.airDelivery.active ? 0.12 + location.airDelivery.airflowFraction * 0.22 : 0),
    );
    plume.style.setProperty(
      "--air-duration",
      `${Math.max(3.2, 6.5 - location.airDelivery.airflowFraction * 2.5).toFixed(2)}s`,
    );
  }
  for (const [id, plume] of airflowNodes) {
    if (desiredIds.has(id)) continue;
    plume.remove();
    airflowNodes.delete(id);
  }
}

function appendTelemetryRow(list, label, value) {
  const wrapper = createElement("div");
  wrapper.append(createElement("dt", { text: label }), createElement("dd", { text: value }));
  list.appendChild(wrapper);
}

function formatDiagnosticValue(key, value) {
  if (key.endsWith("_humidity") || key.endsWith("_humidity_pct")) {
    return value === null || value === undefined ? "\u2014" : `${formatValue(value)} %RH`;
  }
  if (key.endsWith("_pct")) return value === null || value === undefined ? "\u2014" : `${formatValue(value)} %`;
  if (key.endsWith("_temp_f") || key.endsWith("_setpoint_f") || ["discharge_temp", "zone_temp"].includes(key)) {
    return value === null || value === undefined ? "\u2014" : `${formatValue(value)} \u00B0F`;
  }
  if (key.includes("airflow")) return value === null || value === undefined ? "\u2014" : `${formatValue(value, 0)} cfm`;
  return formatValue(value);
}

function renderInspector() {
  const selected = state.locations.find((location) => location.id === state.selectedLocationId) || null;
  const inspector = $("location-inspector");
  const statePill = $("inspector-state");
  const values = $("inspector-values");
  replaceChildren(values);
  inspector.classList.toggle("is-failure", selected?.state === "failure");

  if (!selected) {
    setText("inspector-title", "Building Overview");
    statePill.className = "state-pill state-idle";
    statePill.textContent = "IDLE";
    setText("inspector-message", "Select a marker to inspect live values and diagnostics.");
    appendTelemetryRow(values, "Locations", state.locations.length);
    appendTelemetryRow(values, "Running", state.summary.running);
    appendTelemetryRow(values, "Failures", state.summary.failures);
    $("diagnostic-block").hidden = true;
    $("inspect-equipment").disabled = true;
    return;
  }

  setText("inspector-title", selected.label);
  statePill.className = `state-pill state-${selected.state}`;
  statePill.textContent = selected.state.toUpperCase();
  setText("inspector-message", selected.message || `${selected.label} is ${selected.state}.`);
  appendTelemetryRow(values, "Group", selected.groupId);
  appendTelemetryRow(values, "Floor", labelForFloor(selected.floor));
  appendTelemetryRow(values, "Component", titleCase(selected.componentType));
  if (selected.airDelivery) {
    appendTelemetryRow(values, "Air mode", titleCase(selected.airDelivery.mode));
    appendTelemetryRow(values, "Conditioning source", titleCase(selected.airDelivery.source));
    appendTelemetryRow(
      values,
      "Thermal effect",
      selected.airDelivery.temperatureDeltaF === null
        ? "\u2014"
        : `${formatValue(selected.airDelivery.temperatureDeltaF)} \u00B0F vs zone`,
    );
    if (selected.airDelivery.zoneHumidityPct !== null) {
      appendTelemetryRow(values, "Zone humidity", `${formatValue(selected.airDelivery.zoneHumidityPct)} %RH`);
    }
    if (selected.airDelivery.zoneAreaSqft !== null) {
      appendTelemetryRow(values, "Zone area", `${formatValue(selected.airDelivery.zoneAreaSqft, 0)} ft\u00B2`);
    }
    if (selected.airDelivery.coolingMinimumAirflowCfm !== null) {
      appendTelemetryRow(values, "Cooling minimum", `${formatValue(selected.airDelivery.coolingMinimumAirflowCfm, 0)} cfm`);
    }
    if (selected.airDelivery.coolingMaximumAirflowCfm !== null) {
      appendTelemetryRow(values, "Cooling maximum", `${formatValue(selected.airDelivery.coolingMaximumAirflowCfm, 0)} cfm`);
    }
    if (selected.airDelivery.heatingMinimumAirflowCfm !== null) {
      appendTelemetryRow(values, "Heating minimum", `${formatValue(selected.airDelivery.heatingMinimumAirflowCfm, 0)} cfm`);
    }
    if (selected.airDelivery.heatingMaximumAirflowCfm !== null) {
      appendTelemetryRow(values, "Heating maximum", `${formatValue(selected.airDelivery.heatingMaximumAirflowCfm, 0)} cfm`);
    }
    if (selected.airDelivery.damperCommandPct !== null) {
      appendTelemetryRow(values, "Damper command", `${formatValue(selected.airDelivery.damperCommandPct)} %`);
    }
    if (selected.airDelivery.damperFeedbackPct !== null) {
      appendTelemetryRow(values, "Damper feedback", `${formatValue(selected.airDelivery.damperFeedbackPct)} %`);
    }
  }

  const valueLabels = {
    command: "Command",
    status: "Status",
    airflow: "Airflow",
    airflow_setpoint: "Airflow setpoint",
    heating_min_airflow: "Heating minimum airflow",
    heating_max_airflow: "Heating maximum airflow",
    cooling_min_airflow: "Cooling minimum airflow",
    cooling_max_airflow: "Cooling maximum airflow",
    damper_position_command: "Damper position command",
    damper_position_feedback: "Damper position feedback",
    cooling_valve_command_pct: "Cooling valve command",
    heating_valve_command_pct: "Heating valve command",
    cooling_valve_effective_pct: "Cooling valve effective",
    heating_valve_effective_pct: "Heating valve effective",
    supply_air_temp_f: "Supply-air temperature",
    supply_air_temp_setpoint_f: "Supply-air setpoint",
    valve_overlap_pct: "Simultaneous valve overlap",
    valve_changeover_active: "Valve changeover active",
    discharge_temp: "Discharge-air temperature",
    zone_temp: "Zone temperature",
    zone_humidity: "Zone humidity",
  };
  for (const [key, value] of Object.entries(selected.values || {})) {
    if (
      selected.airDelivery
      && [
        "heating_min_airflow",
        "heating_max_airflow",
        "cooling_min_airflow",
        "cooling_max_airflow",
        "damper_position",
        "damper_position_command",
        "damper_position_feedback",
      ].includes(key)
    ) {
      continue;
    }
    appendTelemetryRow(
      values,
      valueLabels[key] || titleCase(key),
      formatDiagnosticValue(key, value),
    );
  }

  const diagnostic = $("diagnostic-block");
  diagnostic.hidden = selected.state !== "failure";
  if (!diagnostic.hidden) {
    setText("diagnostic-title", titleCase(selected.diagnosticType || "Command status mismatch"));
    setText("diagnostic-message", selected.message || "The expected status did not follow its command.");
    setText("diagnostic-elapsed", `${formatValue(selected.mismatchSeconds, 0)}s mismatch`);
    setText("diagnostic-threshold", `threshold ${formatValue(selected.thresholdSeconds, 0)}s`);
  }
  $("inspect-equipment").disabled = !selected.groupId;
}

function renderFailureList() {
  const list = $("failure-list");
  const failures = state.locations.filter((location) => location.state === "failure");
  if (!failures.length) {
    replaceChildren(list, createElement("div", { className: "empty-state compact" }, [
      createIcon("fa-shield-halved"),
      createElement("span", { text: "No active failures" }),
    ]));
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const location of failures) {
    const button = createElement("button", {
      className: `failure-item${location.id === state.selectedLocationId ? " is-selected" : ""}`,
      type: "button",
      attrs: { "aria-label": `Inspect failure at ${location.label}` },
    });
    const copy = createElement("span", { className: "failure-item-copy" });
    copy.append(
      createElement("strong", { text: location.label }),
      createElement("small", { text: titleCase(location.diagnosticType || "Command status mismatch") }),
    );
    button.append(
      createIcon("fa-triangle-exclamation"),
      copy,
      createElement("span", { text: `${formatValue(location.mismatchSeconds, 0)}s` }),
    );
    button.addEventListener("click", () => {
      state.selectedLocationId = location.id;
      state.floor = "all";
      updateFloorButtons();
      renderMarkers();
      renderInspector();
      renderFailureList();
    });
    fragment.appendChild(button);
  }
  replaceChildren(list, fragment);
}

function updateFloorButtons() {
  document.querySelectorAll("[data-floor]").forEach((button) => {
    const active = button.dataset.floor === state.floor;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function renderDigitalTwin() {
  const normalized = normalizeCommandCenter(state.commandCenter || {});
  state.commandCenter = normalized;
  state.locations = normalized.locations;
  state.summary = normalized.summary;
  if (state.selectedLocationId && !state.locations.some((location) => location.id === state.selectedLocationId)) {
    state.selectedLocationId = null;
  }
  if (!state.selectedLocationId) {
    state.selectedLocationId = state.locations.find((location) => location.state === "failure")?.id || null;
  }
  const asset = normalized.building.asset;
  if (asset.startsWith("/static/")) $("building-stage").querySelector("img").src = asset;
  renderTwinSummary();
  renderAirflow();
  renderMarkers();
  renderInspector();
  renderFailureList();
  $("stage-loading").hidden = true;
}

function renderStatus() {
  const status = state.status;
  if (!status) return;
  const simulation = status.simulation || {};
  setText("side-device-instance", status.device?.instance);
  const running = Boolean(simulation.running);
  const engineButton = $("engine-control");
  engineButton.classList.toggle("button-primary", running);
  engineButton.classList.toggle("button-secondary", !running);
  engineButton.querySelector("span").textContent = running ? "ENGINE RUNNING" : "START ENGINE";
  $("speed-control").value = String(Math.round(Number(simulation.speed_multiplier) || 1));
  const faultCount = Number(status.active_fault_count) || 0;
  $("nav-fault-count").hidden = faultCount === 0;
  setText("nav-fault-count", faultCount);
  renderActiveScenario();

  const oaTemp = findPointByAliases(["oa_temp", "outside_air_temperature", "outdoor_air_temp"]);
  const oaHumidity = findPointByAliases(["oa_humidity", "outside_air_humidity", "outdoor_air_humidity"]);
  if (!state.weatherDirty) {
    if (oaTemp && !$("weather-temp").value) $("weather-temp").value = String(oaTemp.present_value);
    if (oaHumidity && !$("weather-humidity").value) $("weather-humidity").value = String(oaHumidity.present_value);
  }
  populateGroupControls();
  if (state.activeView === "equipment") renderEquipment();
  if (state.activeView === "operations") renderFaults();
}

function firstValue(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "");
}

function pathValue(source, path) {
  if (!source || typeof source !== "object") return undefined;
  return path.split(".").reduce((value, key) => (
    value && typeof value === "object" ? value[key] : undefined
  ), source);
}

function valueFrom(source, paths, fallback = undefined) {
  return firstValue(...paths.map((path) => pathValue(source, path)), fallback);
}

function numericValue(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function booleanValue(value) {
  if (typeof value === "string") {
    return ["1", "true", "on", "active", "alarm", "tripped", "proved"].includes(value.toLowerCase());
  }
  return Boolean(value);
}

function pointValue(aliases, fallback = undefined) {
  const point = findPointByAliases(Array.isArray(aliases) ? aliases : [aliases]);
  return point ? point.present_value : fallback;
}

function formatTemperature(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(1)}\u00B0F` : "--";
}

function formatHumidity(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(1)}% RH` : "--";
}

function formatPercent(value, digits = 1) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(digits)}%` : "--";
}

function formatExposure(seconds) {
  const value = Math.max(0, numericValue(seconds));
  const minutes = Math.floor(value / 60);
  const remaining = Math.floor(value % 60);
  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

function normalizeSafetyState(rawState, options = {}) {
  const normalized = String(rawState || "").trim().toLowerCase().replace(/[\s_]+/g, "-");
  if (options.burst || options.flooded || ["burst", "ruptured", "catastrophic", "failed"].includes(normalized)) {
    return options.kind === "freeze" ? "burst" : "ruptured";
  }
  if (options.frozen || ["frozen", "ice", "freeze"].includes(normalized)) return "frozen";
  if (options.tripped || options.latched || ["trip", "tripped", "latched", "lockout"].includes(normalized)) return "tripped";
  if (["warning", "risk", "approaching", "exposure"].includes(normalized)) return "warning";
  return "normal";
}

function normalizeAhuSnapshot(data) {
  const raw = data && typeof data === "object" ? data : {};
  const operating = state.commandCenter?.systems?.air_handler || {};
  const operation = raw.operation || operating;
  const sensors = raw.sensors || {};
  const actuatorSnapshot = raw.actuators || {};
  const economizer = raw.economizer || {};
  const airPath = raw.air_path || raw.ahu?.air_path || {};
  const safetyRoot = raw.safeties || raw.safety || raw.ahu?.safeties || {};
  const highRaw = safetyRoot.high_static || safetyRoot.high_static_pressure || {};
  const freezeRaw = safetyRoot.freezestat || safetyRoot.freeze || {};

  const actual = numericValue(firstValue(
    raw.actual_inwc,
    raw.duct_static_pressure_inwc,
    operation.duct_static_pressure_inwc,
    pointValue("duct_static_pressure"),
  ));
  const highTrip = numericValue(firstValue(
    highRaw.trip_threshold_inwc,
    highRaw.trip_setpoint_inwc,
    safetyRoot.high_static_trip_threshold_inwc,
    raw.high_static_trip_threshold_inwc,
  ), 4.0);
  const ductLimit = numericValue(firstValue(
    highRaw.structural_limit_inwc,
    highRaw.duct_limit_inwc,
    safetyRoot.duct_failure_limit_inwc,
    raw.duct_structural_limit_inwc,
  ), 5.0);
  const highTripped = booleanValue(firstValue(
    highRaw.auto_trip_latched,
    highRaw.tripped,
    safetyRoot.high_static_trip_active,
    safetyRoot.automatic_high_static_trip,
    raw.high_static_trip_latched,
    pointValue("high_static_trip_status"),
    pointValue("high_static_pressure_trip"),
  ));
  const ductRuptured = booleanValue(firstValue(
    highRaw.ruptured,
    highRaw.structural_failure,
    safetyRoot.duct_structural_failure,
    raw.duct_structural_failure,
    raw.duct_ruptured,
    pointValue(["duct_structural_failure", "duct_failure"]),
  )) || actual > ductLimit;
  let highState = normalizeSafetyState(firstValue(highRaw.state, raw.high_static_state), {
    kind: "high",
    tripped: highTripped,
    latched: booleanValue(highRaw.latched),
    burst: ductRuptured,
  });
  if (highState === "normal" && actual >= highTrip * 0.875) highState = "warning";

  const freezeTemp = firstValue(
    freezeRaw.element_temp_f,
    freezeRaw.coil_entering_air_temp_f,
    safetyRoot.cooling_coil_entering_air_temp_f,
    raw.cooling_coil_entering_air_temp_f,
    raw.coil_entering_air_temp_f,
    pointValue(["cooling_coil_entering_air_temp", "coil_entering_air_temp"]),
  );
  const freezeExposure = numericValue(firstValue(
    freezeRaw.exposure_seconds,
    freezeRaw.freeze_exposure_seconds,
    safetyRoot.freezestat_exposure_seconds,
    raw.freeze_exposure_seconds,
  ));
  const freezeLimit = numericValue(firstValue(
    freezeRaw.exposure_limit_seconds,
    freezeRaw.freeze_limit_seconds,
    safetyRoot.freeze_failure_limit_seconds,
    raw.freeze_exposure_limit_seconds,
  ), 1200);
  const freezeTripped = booleanValue(firstValue(
    freezeRaw.auto_trip_latched,
    freezeRaw.tripped,
    safetyRoot.freezestat_trip_active,
    safetyRoot.automatic_freezestat_trip,
    raw.freezestat_trip_latched,
    pointValue("freezestat_trip_status"),
    pointValue("freezestat_trip"),
  ));
  const coilFrozen = booleanValue(firstValue(
    freezeRaw.frozen,
    freezeRaw.freeze_condition,
    safetyRoot.cooling_coil_freeze_condition,
    raw.cooling_coil_frozen,
    pointValue(["cooling_coil_freeze_condition", "coil_freeze_condition"]),
  ));
  const coilBurst = booleanValue(firstValue(
    freezeRaw.coil_burst,
    freezeRaw.ruptured,
    freezeRaw.flooded,
    safetyRoot.cooling_coil_rupture_flood,
    raw.cooling_coil_burst,
    raw.flooded,
    pointValue(["cooling_coil_burst", "coil_burst_flood"]),
  ));
  let freezeState = normalizeSafetyState(firstValue(freezeRaw.state, raw.freezestat_state), {
    kind: "freeze",
    tripped: freezeTripped,
    latched: booleanValue(freezeRaw.latched),
    frozen: coilFrozen,
    burst: coilBurst,
    flooded: booleanValue(freezeRaw.flooded),
  });
  if (
    freezeState === "normal"
    && Number.isFinite(Number(freezeTemp))
    && Number(freezeTemp) < 35.0
  ) {
    freezeState = "warning";
  }

  const outside = airPath.outside_air || {};
  const returnAir = airPath.return_air || {};
  const mixed = airPath.mixed_air || {};
  const preheat = airPath.preheat || airPath.preheat_coil || {};
  const cooling = airPath.cooling_coil || {};
  const reheat = airPath.reheat || airPath.reheat_coil || {};
  const supply = airPath.supply_air || {};

  return {
    raw,
    fanCommand: booleanValue(firstValue(raw.fan_command, supply.fan_command, pointValue("sa_fan_ss"))),
    fanStatus: booleanValue(firstValue(raw.fan_status, supply.fan_status, operation.fan_proven, pointValue("sa_fan_status"))),
    raFanCommand: booleanValue(firstValue(returnAir.fan_command, raw.ra_fan_command, pointValue("ra_fan_ss"))),
    raFanStatus: booleanValue(firstValue(returnAir.fan_status, raw.ra_fan_status, pointValue("ra_fan_status"))),
    pidActive: booleanValue(firstValue(raw.pid_active, operation.duct_static_pid_active)),
    actual,
    physical: numericValue(firstValue(raw.physical_inwc, actual)),
    setpoint: numericValue(firstValue(raw.setpoint_inwc, operation.duct_static_pressure_setpoint_inwc, pointValue("duct_static_pressure_setpoint")), 1.0),
    speed: numericValue(firstValue(raw.fan_speed_pct, supply.fan_speed_pct, operation.sa_fan_speed_feedback_pct, pointValue("sa_fan_speed_feedback"))),
    output: numericValue(firstValue(raw.pid_output_pct, raw.fan_speed_pct, operation.sa_fan_speed_feedback_pct)),
    frequency: numericValue(firstValue(raw.vfd_frequency_hz, operation.sa_fan_vfd_frequency_hz)),
    requestedFrequency: numericValue(firstValue(raw.vfd_requested_frequency_hz)),
    minimumFrequency: numericValue(firstValue(raw.vfd_minimum_frequency_hz), 20.0),
    maximumFrequency: numericValue(firstValue(raw.vfd_maximum_frequency_hz), 60.0),
    demand: numericValue(firstValue(raw.aggregate_vav_damper_pct, operation.aggregate_vav_damper_pct)),
    error: numericValue(firstValue(raw.error_inwc, raw.setpoint_inwc !== undefined ? raw.setpoint_inwc - actual : undefined)),
    tuning: raw.tuning || {},
    history: Array.isArray(raw.history) ? raw.history : [],
    temperatures: {
      oa: firstValue(outside.temp_f, sensors.outside_air_temp_f, raw.outside_air_temp_f, pointValue(["oa_temp", "outside_air_temperature"])),
      ra: firstValue(returnAir.temp_f, sensors.return_air_temp_f, raw.return_air_temp_f, operation.return_air_temp_f, pointValue("ahu_ra_temp")),
      ma: firstValue(mixed.temp_f, sensors.mixed_air_temp_f, raw.mixed_air_temp_f, operation.mixed_air_temp_f, pointValue("ahu_ma_temp")),
      preheatLeaving: firstValue(preheat.leaving_temp_f, sensors.cooling_coil_entering_air_temp_f, raw.preheat_leaving_air_temp_f, freezeTemp),
      coolingEntering: freezeTemp,
      coolingLeaving: firstValue(cooling.leaving_temp_f, raw.cooling_coil_leaving_air_temp_f),
      sa: firstValue(supply.temp_f, sensors.supply_air_temp_f, raw.supply_air_temp_f, operation.supply_air_temp_f, pointValue("ahu_sa_temp")),
      saSetpoint: firstValue(supply.temp_setpoint_f, raw.supply_air_temp_setpoint_f, operation.supply_air_temp_setpoint_f, pointValue("sa_temp_setpoint")),
    },
    humidity: {
      oa: firstValue(outside.humidity_pct, raw.outside_air_humidity_pct, pointValue(["oa_humidity", "outside_air_humidity"])),
      ra: firstValue(returnAir.humidity_pct, sensors.return_air_humidity_pct, raw.return_air_humidity_pct, pointValue("ahu_ra_humidity")),
      ma: firstValue(mixed.humidity_pct, sensors.mixed_air_humidity_pct, raw.mixed_air_humidity_pct, pointValue("ahu_ma_humidity")),
      sa: firstValue(supply.humidity_pct, sensors.supply_air_humidity_pct, raw.supply_air_humidity_pct, operation.supply_air_humidity_pct, pointValue("ahu_sa_humidity")),
    },
    actuators: {
      economizer: numericValue(firstValue(economizer.requested_pct, outside.economizer_command_pct, actuatorSnapshot.economizer_pct, raw.economizer_command_pct, pointValue("economizer"))),
      economizerEffective: numericValue(firstValue(economizer.effective_pct, actuatorSnapshot.economizer_effective_pct, economizer.requested_pct, pointValue("economizer"))),
      outsideFraction: numericValue(firstValue(economizer.outside_air_fraction, outside.outside_air_fraction, raw.outside_air_fraction, operation.outside_air_fraction)),
      preheat: numericValue(firstValue(preheat.valve_command_pct, actuatorSnapshot.preheat_valve_pct, raw.preheat_valve_command_pct, pointValue("preheat_valve"))),
      cooling: numericValue(firstValue(cooling.valve_command_pct, actuatorSnapshot.cooling_valve_pct, raw.cooling_valve_command_pct, operation.cooling_valve_command_pct, pointValue("cooling_valve"))),
      coolingEffective: numericValue(firstValue(cooling.valve_effective_pct, raw.cooling_valve_effective_pct, operation.cooling_valve_effective_pct)),
      reheat: numericValue(firstValue(reheat.valve_command_pct, actuatorSnapshot.reheat_valve_pct, raw.heating_valve_command_pct, operation.heating_valve_command_pct, pointValue("heating_valve"))),
      reheatEffective: numericValue(firstValue(reheat.valve_effective_pct, raw.heating_valve_effective_pct, operation.heating_valve_effective_pct)),
    },
    economizer: {
      state: String(firstValue(economizer.state, "off")),
      method: String(firstValue(economizer.suitability_method, "--")),
      freeCoolingAvailable: booleanValue(economizer.free_cooling_available),
      coolingBeneficial: booleanValue(economizer.cooling_beneficial),
      oaEnthalpy: numericValue(economizer.oa_enthalpy_btu_lb, NaN),
      raEnthalpy: numericValue(economizer.ra_enthalpy_btu_lb, NaN),
      enthalpyDelta: numericValue(economizer.enthalpy_delta_btu_lb, NaN),
      oaDewPoint: numericValue(economizer.oa_dew_point_f, NaN),
      lowLimitActive: booleanValue(economizer.mixed_air_low_limit_active),
      lowLimit: numericValue(economizer.mixed_air_low_limit_f, 45),
      limitingReason: String(firstValue(economizer.limiting_reason, economizer.sensor_fallback_reason, "none")),
      proofSeconds: numericValue(economizer.full_open_seconds),
      integratedAllowed: booleanValue(economizer.integrated_cooling_allowed),
      fddFlags: Array.isArray(economizer.fdd_flags) ? economizer.fdd_flags : [],
    },
    smoke: {
      ra: booleanValue(firstValue(returnAir.smoke_alarm, raw.ra_smoke_alarm, pointValue("ra_smoke_detector"))),
      sa: booleanValue(firstValue(supply.smoke_alarm, raw.sa_smoke_alarm, pointValue("sa_smoke_detector"))),
    },
    high: {
      state: highState,
      trip: highTrip,
      limit: ductLimit,
      bypassed: booleanValue(firstValue(highRaw.bypassed, highRaw.safety_bypassed, safetyRoot.high_static_safety_bypassed, raw.high_static_safety_bypassed)),
      latched: highTripped,
      ruptured: ductRuptured,
    },
    freeze: {
      state: freezeState,
      temp: freezeTemp,
      exposure: freezeExposure,
      limit: freezeLimit,
      chwFlow: booleanValue(firstValue(cooling.chw_flow_proven, freezeRaw.chw_flow_proven, safetyRoot.chilled_water_flow_proven, raw.chw_flow_proven)),
      bypassed: booleanValue(firstValue(freezeRaw.bypassed, freezeRaw.safety_bypassed, safetyRoot.freezestat_safety_bypassed, raw.freezestat_safety_bypassed)),
      latched: freezeTripped,
      frozen: coilFrozen,
      burst: coilBurst,
    },
  };
}

function drawDuctStaticTrend() {
  const canvas = $("pid-trend");
  const empty = $("pid-trend-empty");
  if (!canvas) return;
  const history = Array.isArray(state.ductStatic?.history)
    ? state.ductStatic.history.slice(-180)
    : [];
  empty.hidden = history.length >= 2;
  if (!history.length) {
    setText("pid-trend-summary", "Waiting for duct-static trend samples.");
    setText("pid-trend-time-span", "LAST 0 SIMULATED SECONDS");
  }

  const rect = canvas.getBoundingClientRect();
  const width = Math.max(320, Math.round(rect.width || 1200));
  const height = Math.max(220, Math.round(rect.height || 310));
  const ratio = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  const renderWidth = Math.round(width * ratio);
  const renderHeight = Math.round(height * ratio);
  if (canvas.width !== renderWidth || canvas.height !== renderHeight) {
    canvas.width = renderWidth;
    canvas.height = renderHeight;
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);

  const padding = { left: 48, right: 48, top: 18, bottom: 30 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const pressureValues = history.flatMap((sample) => [
    Number(sample.setpoint_inwc) || 0,
    Number(sample.actual_inwc) || 0,
  ]);
  pressureValues.push(Number(state.ahuNormalized?.actual) || 0);
  let pressureMaximum = Math.max(
    2.5,
    Math.ceil(Math.max(0, ...pressureValues) * 2) / 2,
  );
  if (pressureMaximum >= 3.5) pressureMaximum = Math.max(5.5, pressureMaximum);
  setText(
    "pid-trend-pressure-scale",
    `PRESSURE: 0\u2013${pressureMaximum.toFixed(1)} in. H2O`,
  );
  $("pid-trend-pressure-scale").setAttribute(
    "aria-label",
    `Pressure scale zero to ${pressureMaximum.toFixed(1)} inches of water column`,
  );

  const timeFor = (sample, index) => {
    const value = Number(sample.sim_seconds);
    return Number.isFinite(value) ? value : index;
  };
  const firstTime = history.length ? timeFor(history[0], 0) : 0;
  const lastTime = history.length
    ? timeFor(history[history.length - 1], history.length - 1)
    : 0;
  const timeSpan = Math.max(0, lastTime - firstTime);
  if (history.length) {
    const latest = history[history.length - 1];
    setText(
      "pid-trend-summary",
      `${history.length} samples spanning ${timeSpan.toFixed(0)} simulated seconds. `
      + `Current setpoint ${Number(latest.setpoint_inwc || 0).toFixed(3)} in. H2O, `
      + `actual ${Number(latest.actual_inwc || 0).toFixed(3)} in. H2O, `
      + `fan speed ${Number(latest.fan_speed_pct || 0).toFixed(1)} percent.`,
    );
    setText(
      "pid-trend-time-span",
      `LAST ${timeSpan.toFixed(0)} SIMULATED SECONDS`,
    );
  }

  context.lineWidth = 1;
  context.font = "11px Segoe UI, Arial, sans-serif";
  context.textBaseline = "middle";
  for (let index = 0; index <= 5; index += 1) {
    const y = padding.top + (chartHeight * index) / 5;
    context.strokeStyle = "rgba(49, 93, 119, 0.45)";
    context.setLineDash(index === 5 ? [] : [3, 5]);
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    const pressureLabel = (pressureMaximum * (1 - index / 5)).toFixed(1);
    const speedLabel = Math.round(100 * (1 - index / 5));
    context.fillStyle = "#7d94aa";
    context.textAlign = "right";
    context.fillText(pressureLabel, padding.left - 9, y);
    context.textAlign = "left";
    context.fillText(`${speedLabel}%`, width - padding.right + 9, y);
  }
  context.setLineDash([]);

  const highSafety = state.ahuNormalized?.high;
  if (highSafety && pressureMaximum >= highSafety.trip) {
    const drawSafetyReference = (value, color, label) => {
      if (value <= 0 || value > pressureMaximum) return;
      const y = padding.top + chartHeight * (1 - value / pressureMaximum);
      context.strokeStyle = color;
      context.lineWidth = 1.2;
      context.setLineDash([4, 4]);
      context.beginPath();
      context.moveTo(padding.left, y);
      context.lineTo(width - padding.right, y);
      context.stroke();
      context.setLineDash([]);
      context.fillStyle = color;
      context.textAlign = "left";
      context.fillText(label, padding.left + 7, Math.max(padding.top + 7, y - 7));
    };
    drawSafetyReference(highSafety.trip, "#ffc857", `${highSafety.trip.toFixed(1)} TRIP`);
    drawSafetyReference(highSafety.limit, "#ff5d68", `${highSafety.limit.toFixed(1)} DUCT LIMIT`);
  }

  if (history.length < 2) return;
  const xFor = (sample, index) => {
    if (timeSpan <= 0) {
      return padding.left + (chartWidth * index) / Math.max(1, history.length - 1);
    }
    return padding.left + chartWidth * (
      (timeFor(sample, index) - firstTime) / timeSpan
    );
  };
  const pressureY = (value) => (
    padding.top + chartHeight * (1 - Math.max(0, Math.min(pressureMaximum, value)) / pressureMaximum)
  );
  const speedY = (value) => (
    padding.top + chartHeight * (1 - Math.max(0, Math.min(100, value)) / 100)
  );
  const drawSeries = (field, color, yFor, dashed = false) => {
    context.strokeStyle = color;
    context.lineWidth = field === "actual_inwc" ? 2.4 : 1.8;
    context.lineJoin = "round";
    context.lineCap = "round";
    context.setLineDash(dashed ? [7, 5] : []);
    context.beginPath();
    history.forEach((sample, index) => {
      const x = xFor(sample, index);
      const y = yFor(Number(sample[field]) || 0);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
  };
  drawSeries("setpoint_inwc", "#ffc857", pressureY, true);
  drawSeries("actual_inwc", "#2dd4f2", pressureY);
  drawSeries("fan_speed_pct", "rgba(167, 139, 250, 0.78)", speedY);
  context.setLineDash([]);

  context.fillStyle = "#7d94aa";
  context.textAlign = "left";
  context.fillText("OLDER", padding.left, height - 11);
  context.textAlign = "right";
  context.fillText("NOW", width - padding.right, height - 11);
}

function renderDuctStatic() {
  const data = state.ductStatic;
  if (!data) return;
  const ahu = normalizeAhuSnapshot(data);
  state.ahuNormalized = ahu;
  const command = ahu.fanCommand;
  const proof = ahu.fanStatus;
  const active = ahu.pidActive;
  const actual = ahu.actual;
  const setpoint = ahu.setpoint;
  const speed = ahu.speed;
  const output = ahu.output;
  const frequency = ahu.frequency;
  const requestedFrequency = ahu.requestedFrequency;
  const demand = ahu.demand;
  const error = ahu.error;

  setText("pid-run-command", command ? "ON" : "OFF");
  setText("pid-fan-status", proof ? "ON" : "OFF");
  setText("pid-static-actual", actual.toFixed(3));
  setText("pid-static-setpoint", setpoint.toFixed(3));
  setText("pid-fan-speed", `${speed.toFixed(1)}% \u00B7 ${frequency.toFixed(1)} Hz`);
  setText(
    "pid-output-copy",
    `PID signal ${output.toFixed(1)}% (${requestedFrequency.toFixed(1)} Hz) \u00B7 Drive min ${ahu.minimumFrequency.toFixed(0)} Hz`,
  );
  setText("pid-vav-demand", `${demand.toFixed(1)}%`);
  setText("pid-error-value", `${error.toFixed(3)} in. w.c.`);
  setText("pid-sensor-bubble", actual.toFixed(3));
  setText("pid-graphic-speed", `${speed.toFixed(1)}% \u00B7 ${frequency.toFixed(1)} Hz`);
  setText("pid-vav-bank-copy", `17 terminals \u00B7 ${demand.toFixed(1)}% weighted open`);
  setText("ahu-sa-temperature", formatTemperature(ahu.temperatures.sa));
  setText("ahu-sa-setpoint", `Setpoint ${formatTemperature(ahu.temperatures.saSetpoint)}`);
  setText(
    "pid-mode-caption",
    `CONTROL MODE \u00B7 ${Number(state.status?.simulation?.speed_multiplier || 1)}\u00D7`,
  );

  const modeDot = $("pid-mode-dot");
  const modeLabel = $("pid-mode-label");
  const loopPill = $("pid-loop-state");
  if (active) {
    modeDot.className = "status-dot is-online";
    modeLabel.textContent = "PID ACTIVE";
    loopPill.className = "state-pill state-running";
    loopPill.textContent = "CONTROLLING";
  } else if (command !== proof) {
    modeDot.className = "status-dot is-offline";
    modeLabel.textContent = command ? "AWAITING PROOF" : "FAN COASTING";
    loopPill.className = "state-pill state-starting";
    loopPill.textContent = command ? "STARTING" : "STOPPING";
  } else {
    modeDot.className = "status-dot";
    modeLabel.textContent = "LOOP OFF";
    loopPill.className = "state-pill state-idle";
    loopPill.textContent = "LOOP OFF";
  }

  const fanGraphic = $("pid-fan-graphic");
  fanGraphic.classList.toggle("is-running", active && speed > 0.5);
  fanGraphic.style.setProperty(
    "--pid-fan-duration",
    `${Math.max(0.32, 2.25 - speed * 0.018).toFixed(2)}s`,
  );
  renderAhuAirPath(ahu);
  renderAhuSafeties(ahu);
  renderAhuInspector(ahu);
  $("ahu-telemetry-state").hidden = true;
  $("ahu-command-stage").classList.remove("is-stale");

  const tuning = ahu.tuning;
  if (!state.pidTuningDirty) {
    $("pid-kp").value = formatValue(tuning.kp, 1);
    $("pid-ki").value = formatValue(tuning.ki, 2);
    $("pid-kd").value = formatValue(tuning.kd, 1);
    $("pid-interval").value = formatValue(tuning.interval_seconds, 1);
  }
  const defaults = Boolean(tuning.is_default);
  const tuningPill = $("pid-tuning-state");
  tuningPill.className = `state-pill ${defaults ? "state-idle" : "state-tracking"}`;
  tuningPill.textContent = defaults ? "DEFAULTS" : "CUSTOM";
  drawDuctStaticTrend();
}

function setAhuLayer(id, active, intensity = 1) {
  const layer = $(id);
  if (!layer) return;
  layer.classList.toggle("is-active", Boolean(active));
  layer.style.setProperty("--active-opacity", String(Math.max(0.15, Math.min(1, intensity))));
}

function setHotspotState(component, options = {}) {
  const hotspot = document.querySelector(`[data-ahu-component="${component}"]`);
  if (!hotspot) return;
  hotspot.classList.toggle("is-active", Boolean(options.active));
  hotspot.classList.toggle("is-running", Boolean(options.running));
  hotspot.classList.toggle("is-alarm", Boolean(options.alarm));
}

function renderAhuAirPath(ahu) {
  const stage = $("ahu-command-stage");
  const coolingActive = ahu.actuators.cooling > 0.5 || ahu.actuators.coolingEffective > 0.5;
  const preheatActive = ahu.actuators.preheat > 0.5;
  const reheatActive = ahu.actuators.reheat > 0.5 || ahu.actuators.reheatEffective > 0.5;
  const economizerActive = ahu.actuators.economizerEffective > 0.5 && ahu.fanStatus;
  const returnActive = ahu.raFanStatus;
  const supplyActive = ahu.fanStatus && ahu.speed > 0.5;
  const operatingMode = reheatActive || preheatActive
    ? "heating"
    : coolingActive
      ? "cooling"
      : supplyActive
        ? "ventilation"
        : "off";
  stage.dataset.operatingMode = operatingMode;
  stage.classList.toggle("motion-paused", state.ahuMotionPaused);
  stage.style.setProperty(
    "--pid-fan-duration",
    `${Math.max(0.32, 2.25 - ahu.speed * 0.018).toFixed(2)}s`,
  );

  setAhuLayer("ahu-economizer-active-layer", economizerActive, 0.35 + ahu.actuators.economizerEffective / 155);
  setAhuLayer("ahu-return-active-layer", returnActive, 0.92);
  setAhuLayer("ahu-preheat-active-layer", preheatActive, 0.28 + ahu.actuators.preheat / 140);
  setAhuLayer("ahu-cooling-active-layer", coolingActive, 0.28 + Math.max(ahu.actuators.cooling, ahu.actuators.coolingEffective) / 140);
  setAhuLayer("ahu-reheat-active-layer", reheatActive, 0.28 + Math.max(ahu.actuators.reheat, ahu.actuators.reheatEffective) / 140);
  setAhuLayer("ahu-supply-active-layer", supplyActive, 0.88);

  setHotspotState("economizer", { active: economizerActive });
  setHotspotState("return-fan", { active: returnActive, running: returnActive });
  setHotspotState("preheat-coil", { active: preheatActive });
  setHotspotState("cooling-coil", { active: coolingActive, alarm: ahu.freeze.frozen || ahu.freeze.burst });
  setHotspotState("reheat-coil", { active: reheatActive });
  setHotspotState("supply-fan", { active: supplyActive, running: supplyActive });
  setHotspotState("return-sensors", { alarm: ahu.smoke.ra });
  setHotspotState("supply-sensors", { alarm: ahu.smoke.sa });
  setHotspotState("freezestat", { alarm: ahu.freeze.state !== "normal" });
  setHotspotState("high-static", { alarm: ahu.high.state !== "normal" });
  setHotspotState("duct-break", { alarm: ahu.high.ruptured });

  setText("ahu-economizer-label", `ECON ${formatPercent(ahu.actuators.economizerEffective, 0)} EFF`);
  setText("ahu-ra-fan-label", `RA FAN ${ahu.raFanStatus ? "ON" : "OFF"}`);
  setText("ahu-preheat-label", `PREHEAT ${formatPercent(ahu.actuators.preheat, 0)}`);
  setText("ahu-cooling-label", `COOLING ${formatPercent(ahu.actuators.cooling, 0)}`);
  setText("ahu-reheat-label", `REHEAT ${formatPercent(ahu.actuators.reheat, 0)}`);
  setText("ahu-ra-label", `RA ${formatTemperature(ahu.temperatures.ra)}`);
  setText("ahu-ma-label", `MA ${formatTemperature(ahu.temperatures.ma)}`);
  setText("ahu-sa-label", `SA ${formatTemperature(ahu.temperatures.sa)}`);
  setText("ahu-freezestat-label", `FREEZESTAT ${ahu.freeze.state.toUpperCase()}`);
  setText("ahu-high-static-label", `HIGH STATIC ${ahu.high.state.toUpperCase()}`);

  setText("ahu-oa-temp", formatTemperature(ahu.temperatures.oa));
  setText("ahu-oa-rh", formatHumidity(ahu.humidity.oa));
  setText("ahu-ra-temp", formatTemperature(ahu.temperatures.ra));
  setText("ahu-ra-rh", formatHumidity(ahu.humidity.ra));
  setText("ahu-ma-temp", formatTemperature(ahu.temperatures.ma));
  setText("ahu-ma-rh", formatHumidity(ahu.humidity.ma));
  setText("ahu-sa-temp", formatTemperature(ahu.temperatures.sa));
  setText("ahu-sa-rh", formatHumidity(ahu.humidity.sa));
  renderAhuEconomizer(ahu);

  const raSmoke = $("ahu-ra-smoke");
  const saSmoke = $("ahu-sa-smoke");
  raSmoke.textContent = ahu.smoke.ra ? "RA SMOKE ALARM" : "RA SMOKE NORMAL";
  saSmoke.textContent = ahu.smoke.sa ? "SA SMOKE ALARM" : "SA SMOKE NORMAL";
  raSmoke.classList.toggle("is-alarm", ahu.smoke.ra);
  saSmoke.classList.toggle("is-alarm", ahu.smoke.sa);
}

function formatOptionalNumber(value, digits = 1, suffix = "") {
  return Number.isFinite(Number(value)) ? `${Number(value).toFixed(digits)}${suffix}` : "--";
}

function renderAhuEconomizer(ahu) {
  const econ = ahu.economizer;
  const fddActive = econ.fddFlags.length > 0;
  const limited = econ.lowLimitActive || [
    "safety-shutdown",
    "mixed-air-low-limit",
    "unavailable-sensor",
    "unavailable-weather",
  ].includes(econ.state.toLowerCase());
  const card = $("ahu-economizer-card");
  card.classList.toggle("is-limited", limited && !fddActive);
  card.classList.toggle("is-fdd-alarm", fddActive);
  setText("ahu-economizer-state", econ.state.replace(/-/g, " ").toUpperCase());
  setText("ahu-economizer-requested", formatPercent(ahu.actuators.economizer));
  setText("ahu-economizer-effective", formatPercent(ahu.actuators.economizerEffective));
  setText(
    "ahu-economizer-suitability",
    econ.freeCoolingAvailable
      ? econ.coolingBeneficial ? "AVAILABLE / BENEFICIAL" : "AVAILABLE / NO LOAD"
      : "NOT AVAILABLE",
  );
  setText("ahu-economizer-method", econ.method.replace(/-/g, " ").toUpperCase());
  setText(
    "ahu-economizer-enthalpy",
    `${formatOptionalNumber(econ.oaEnthalpy, 1)} / ${formatOptionalNumber(econ.raEnthalpy, 1)} Btu/lb`,
  );
  setText(
    "ahu-economizer-delta",
    `${formatOptionalNumber(econ.enthalpyDelta, 1)} Btu/lb / ${formatTemperature(econ.oaDewPoint)}`,
  );
  setText(
    "ahu-economizer-low-limit",
    `${formatTemperature(econ.lowLimit)} \u00B7 ${econ.lowLimitActive ? "ACTIVE" : "CLEAR"}`,
  );
  setText("ahu-economizer-limit-reason", econ.limitingReason.replace(/-/g, " ").toUpperCase());
  setText(
    "ahu-economizer-proof",
    `${econ.proofSeconds.toFixed(0)} seconds \u00B7 ${econ.integratedAllowed ? "INTEGRATED" : "PROVING"}`,
  );
  const flags = $("ahu-economizer-flags");
  flags.textContent = fddActive
    ? `FDD: ${econ.fddFlags.map((flag) => flag.replace(/-/g, " ").toUpperCase()).join(" \u00B7 ")}`
    : "FDD NORMAL";
  flags.classList.toggle("is-alarm", fddActive);
}

function safetyPathText(safety) {
  if (safety.bypassed) return "BYPASSED / FAILED";
  if (safety.latched) return "TRIPPED / LATCHED";
  return "Monitoring";
}

function renderAhuSafeties(ahu) {
  const stage = $("ahu-command-stage");
  stage.dataset.highStaticState = ahu.high.state;
  stage.dataset.freezeState = ahu.freeze.state;

  const highCard = $("ahu-high-static-card");
  const freezeCard = $("ahu-freeze-card");
  highCard.classList.toggle("is-warning", ahu.high.state === "warning");
  highCard.classList.toggle("is-alarm", ["tripped", "ruptured"].includes(ahu.high.state));
  freezeCard.classList.toggle("is-warning", ahu.freeze.state === "warning");
  freezeCard.classList.toggle("is-alarm", ["tripped", "frozen", "burst"].includes(ahu.freeze.state));

  setText("ahu-high-static-state", ahu.high.state.toUpperCase());
  setText("ahu-high-static-actual", `${ahu.actual.toFixed(3)} in. H2O`);
  setText("ahu-high-static-threshold", `${ahu.high.trip.toFixed(3)} in. H2O`);
  setText("ahu-duct-limit", `${ahu.high.limit.toFixed(3)} in. H2O`);
  setText("ahu-high-static-path", safetyPathText(ahu.high));
  const pressureScaleMaximum = Math.max(6.5, ahu.high.limit + 1);
  $("ahu-pressure-fill").style.setProperty(
    "--pressure-progress",
    `${Math.max(0, Math.min(100, ahu.actual / pressureScaleMaximum * 100)).toFixed(1)}%`,
  );

  setText("ahu-freeze-state", ahu.freeze.state.toUpperCase());
  setText("ahu-freeze-temp", formatTemperature(ahu.freeze.temp));
  setText("ahu-freeze-timer", `${formatExposure(ahu.freeze.exposure)} / ${formatExposure(ahu.freeze.limit)}`);
  setText("ahu-chw-flow", ahu.freeze.chwFlow ? "PROVEN" : "NOT PROVEN");
  setText("ahu-freeze-path", safetyPathText(ahu.freeze));
  $("ahu-freeze-fill").style.setProperty(
    "--freeze-progress",
    `${Math.max(0, Math.min(100, ahu.freeze.exposure / Math.max(1, ahu.freeze.limit) * 100)).toFixed(1)}%`,
  );

  const catastrophic = ahu.high.state === "ruptured" || ahu.freeze.state === "burst";
  const alarmKey = `${ahu.high.state}:${ahu.freeze.state}:${ahu.smoke.ra ? 1 : 0}:${ahu.smoke.sa ? 1 : 0}:${ahu.economizer.fddFlags.join(",")}`;
  if (catastrophic && alarmKey !== state.ahuPreviousAlarmKey) {
    stage.classList.remove("is-catastrophic");
    void stage.offsetWidth;
    if (!state.ahuMotionPaused) stage.classList.add("is-catastrophic");
    if (state.ahuCatastrophicTimer) window.clearTimeout(state.ahuCatastrophicTimer);
    state.ahuCatastrophicTimer = window.setTimeout(() => {
      stage.classList.remove("is-catastrophic");
      state.ahuCatastrophicTimer = null;
    }, 3000);
  }
  state.ahuPreviousAlarmKey = alarmKey;

  const summary = $("ahu-alarm-summary");
  const title = $("ahu-alarm-title");
  const copy = $("ahu-alarm-copy");
  summary.className = "ahu-alarm-summary";
  summary.setAttribute("aria-live", catastrophic ? "assertive" : "polite");
  if (ahu.high.state === "ruptured") {
    summary.classList.add("state-failure");
    title.textContent = "CATASTROPHIC DUCT FAILURE";
    copy.textContent = `Supply static exceeded the ${ahu.high.limit.toFixed(1)} in. H2O training duct limit after the safety path failed or was bypassed.`;
  } else if (ahu.freeze.state === "burst") {
    summary.classList.add("state-failure");
    title.textContent = "COOLING COIL BURST / FLOOD";
    copy.textContent = "The freeze exposure limit was exceeded after the freezestat safety path failed or was bypassed.";
  } else if (ahu.high.state === "tripped" || ahu.freeze.state === "tripped" || ahu.freeze.state === "frozen") {
    summary.classList.add("state-failure");
    title.textContent = "AHU SAFETY TRIPPED";
    copy.textContent = `High static: ${ahu.high.state.toUpperCase()} \u00B7 Freezestat: ${ahu.freeze.state.toUpperCase()}`;
  } else if (ahu.high.state === "warning" || ahu.freeze.state === "warning") {
    summary.classList.add("state-starting");
    title.textContent = "SAFETY EXPOSURE WARNING";
    copy.textContent = `High static: ${ahu.actual.toFixed(3)} in. H2O \u00B7 Coil entering air: ${formatTemperature(ahu.freeze.temp)}`;
  } else if (ahu.smoke.ra || ahu.smoke.sa) {
    summary.classList.add("state-failure");
    title.textContent = "DUCT SMOKE ALARM";
    copy.textContent = `${ahu.smoke.ra ? "Return-air" : "Supply-air"} smoke detector is in alarm.`;
  } else if (ahu.economizer.fddFlags.length) {
    summary.classList.add("state-starting");
    title.textContent = "ECONOMIZER FDD NOTICE";
    copy.textContent = ahu.economizer.fddFlags.map((flag) => flag.replace(/-/g, " ")).join("; ");
  } else {
    summary.classList.add("state-idle");
    title.textContent = "SAFETIES NORMAL";
    copy.textContent = `High-static trip ${ahu.high.trip.toFixed(1)} in. H2O \u00B7 Training duct limit ${ahu.high.limit.toFixed(1)} in. H2O \u00B7 Freezestat monitoring active`;
  }
}

function renderAhuInspector(ahu) {
  const component = state.ahuSelectedComponent;
  const componentData = {
    "outside-air": {
      name: "Outside-Air Intake",
      description: "Outdoor air enters through the economizer assembly and blends with return air at the mixing section.",
      status: ahu.fanStatus ? "AVAILABLE" : "UNIT OFF",
      primary: formatTemperature(ahu.temperatures.oa),
      secondary: formatHumidity(ahu.humidity.oa),
    },
    economizer: {
      name: "Economizer Damper",
      description: "The requested WebCTRL position is qualified by weather suitability, mixed-air low limit, fan proof, and active safeties before becoming the effective damper position.",
      status: ahu.economizer.state.replace(/-/g, " ").toUpperCase(),
      primary: `${formatPercent(ahu.actuators.economizer)} requested`,
      secondary: `${formatPercent(ahu.actuators.economizerEffective)} effective`,
    },
    prefilter: {
      name: "Prefilter",
      description: "The first filter section protects downstream coils and airside components. Differential pressure is not presently exposed.",
      status: "SIMULATED",
      primary: "Installed",
      secondary: "No DP point",
    },
    "return-sensors": {
      name: "Return-Air Sensors & Smoke Detector",
      description: "Return temperature, humidity, and duct smoke are sampled upstream of the return fan.",
      status: ahu.smoke.ra ? "SMOKE ALARM" : "NORMAL",
      primary: formatTemperature(ahu.temperatures.ra),
      secondary: formatHumidity(ahu.humidity.ra),
    },
    "return-fan": {
      name: "Return Fan",
      description: "Return-air fan motion follows the simulated status proof, independently of the incoming start command.",
      status: ahu.raFanStatus ? "PROVEN ON" : ahu.raFanCommand ? "AWAITING PROOF" : "OFF",
      primary: `Command ${ahu.raFanCommand ? "ON" : "OFF"}`,
      secondary: `Status ${ahu.raFanStatus ? "ON" : "OFF"}`,
    },
    "mixed-air": {
      name: "Mixed-Air Section",
      description: "Outside and return air blend here before crossing the preheat and cooling coils.",
      status: ahu.fanStatus ? "AIRFLOW PROVEN" : "NO AIRFLOW",
      primary: formatTemperature(ahu.temperatures.ma),
      secondary: formatHumidity(ahu.humidity.ma),
    },
    "preheat-coil": {
      name: "Preheat Coil",
      description: "The hot-water preheat coil protects the downstream cooling coil during cold outside-air conditions.",
      status: ahu.actuators.preheat > 0.5 ? "HEATING" : "CLOSED",
      primary: formatPercent(ahu.actuators.preheat),
      secondary: `${formatTemperature(ahu.temperatures.preheatLeaving)} leaving`,
    },
    freezestat: {
      name: "Freezestat Safety",
      description: "A serpentine low-temperature element monitors the coil face. A normal trip protects the unit; bypassed protection permits freeze damage training.",
      status: ahu.freeze.state.toUpperCase(),
      primary: formatTemperature(ahu.freeze.temp),
      secondary: `${formatExposure(ahu.freeze.exposure)} exposure`,
    },
    "cooling-coil": {
      name: "Cooling Coil",
      description: "Cooling capacity depends on chilled-water availability, valve position, entering-air conditions, and proven airflow.",
      status: ahu.freeze.burst ? "BURST / FLOODED" : ahu.freeze.frozen ? "FROZEN" : ahu.actuators.cooling > 0.5 ? "COOLING" : "CLOSED",
      primary: formatPercent(ahu.actuators.cooling),
      secondary: `${ahu.freeze.chwFlow ? "CHW flow proven" : "No CHW flow proof"}`,
    },
    "reheat-coil": {
      name: "Reheat Coil",
      description: "The reheat valve raises supply-air temperature after the cooling section and must not fight the cooling valve.",
      status: ahu.actuators.reheat > 0.5 ? "HEATING" : "CLOSED",
      primary: formatPercent(ahu.actuators.reheat),
      secondary: `${formatPercent(ahu.actuators.reheatEffective)} effective`,
    },
    "supply-fan": {
      name: "Supply Fan & VFD",
      description: "The duct-static PID modulates a 0-100% speed signal to maintain the WebCTRL pressure setpoint. The drive maps that signal to 0-60 Hz and enforces a 20 Hz physical minimum whenever it is commanded on.",
      status: ahu.fanStatus ? "PROVEN ON" : ahu.fanCommand ? "AWAITING PROOF" : "OFF",
      primary: `${ahu.frequency.toFixed(1)} Hz \u00B7 ${formatPercent(ahu.speed)}`,
      secondary: `PID ${formatPercent(ahu.output)} \u00B7 ${ahu.actual.toFixed(3)} in. H2O`,
    },
    "supply-sensors": {
      name: "Supply-Air Sensors & Smoke Detector",
      description: "Supply temperature, humidity, and duct smoke are measured downstream of the fan.",
      status: ahu.smoke.sa ? "SMOKE ALARM" : "NORMAL",
      primary: formatTemperature(ahu.temperatures.sa),
      secondary: formatHumidity(ahu.humidity.sa),
    },
    "high-static": {
      name: "High-Static Safety",
      description: "The safety switch is intended to trip at 4.0 in. H2O before the simulator's representative 5.0-in. H2O duct failure limit.",
      status: ahu.high.state.toUpperCase(),
      primary: `${ahu.actual.toFixed(3)} in. H2O`,
      secondary: safetyPathText(ahu.high),
    },
    "duct-break": {
      name: "Supply-Duct Jump",
      description: "The jagged break indicates a jump ahead to the conceptual two-thirds trunk pressure-sensor location.",
      status: ahu.high.ruptured ? "STRUCTURAL FAILURE" : "INTACT",
      primary: `${ahu.high.limit.toFixed(1)} in. H2O limit`,
      secondary: ahu.high.ruptured ? "Failure latched" : "Training section",
    },
    "duct-static": {
      name: "Two-Thirds Duct Static Sensor",
      description: "The simulated probe is located on a stable straight section before the summarized VAV terminal bank.",
      status: ahu.pidActive ? "CONTROLLING" : "IDLE",
      primary: `${ahu.actual.toFixed(3)} in. H2O`,
      secondary: `${ahu.setpoint.toFixed(3)} in. H2O SP`,
    },
  }[component];
  if (!componentData) return;
  document.querySelectorAll("[data-ahu-component]").forEach((button) => {
    const selected = button.dataset.ahuComponent === component;
    button.classList.toggle("is-selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  setText("ahu-component-name", componentData.name);
  setText("ahu-component-description", componentData.description);
  setText("ahu-component-status", componentData.status);
  setText("ahu-component-primary", componentData.primary);
  setText("ahu-component-secondary", componentData.secondary);
}

async function refreshDuctStatic(force = false) {
  if (state.ductStaticInFlight && !force) return;
  state.ductStaticInFlight = true;
  try {
    let data;
    try {
      data = await fetchJSON("/api/ahu/command-center?history_limit=180");
      state.ahuTelemetryEndpoint = "command-center";
    } catch (commandCenterError) {
      data = await fetchJSON("/api/ahu/duct-static");
      state.ahuTelemetryEndpoint = "duct-static-fallback";
    }
    state.ductStatic = data;
    state.ductStaticErrorMessage = "";
    renderDuctStatic();
  } catch (error) {
    const message = `AHU telemetry unavailable: ${error.message}`;
    $("ahu-telemetry-state").hidden = false;
    $("ahu-command-stage").classList.add("is-stale", "motion-paused");
    if (
      state.activeView === "duct-static"
      && state.ductStaticErrorMessage !== message
    ) {
      state.ductStaticErrorMessage = message;
      toast(message, "error");
    }
  } finally {
    state.ductStaticInFlight = false;
  }
}

function appendCell(row, value, className = "") {
  row.appendChild(createElement("td", { text: value, className }));
}

function pointStatusElements(point) {
  const wrapper = createElement("span");
  if (point.interlock) wrapper.appendChild(createElement("span", { className: "status-badge status-bad", text: "INTERLOCK" }));
  if (point.forced) wrapper.appendChild(createElement("span", { className: "status-badge state-tracking", text: "FORCED" }));
  for (const fault of point.active_faults || []) {
    wrapper.appendChild(createElement("span", { className: "status-badge status-warning", text: titleCase(fault) }));
  }
  if (!wrapper.childElementCount) wrapper.appendChild(createElement("span", { className: "status-badge status-good", text: "NORMAL" }));
  return wrapper;
}

function renderEquipment() {
  const group = $("equipment-group-filter").value;
  const search = $("equipment-search").value.trim().toLowerCase();
  const filtered = state.points.filter((point) => {
    if (group && point.group !== group) return false;
    if (!search) return true;
    const haystack = [
      point.group, point.alias, point.object_name, point.object_type,
      `${point.object_type}:${point.object_instance}`,
    ].join(" ").toLowerCase();
    return haystack.includes(search);
  });
  setText("equipment-result-count", filtered.length);
  $("equipment-empty").hidden = filtered.length > 0;
  const fragment = document.createDocumentFragment();
  for (const point of filtered) {
    const row = createElement("tr");
    appendCell(row, point.group, "cell-mono");
    appendCell(row, point.alias, "cell-primary cell-mono");
    appendCell(row, point.object_name, "cell-primary");
    appendCell(row, `${point.object_type}:${point.object_instance}`, "cell-mono");
    const directionCell = createElement("td");
    const outgoing = point.direction === "sim_to_webctrl";
    directionCell.appendChild(createElement("span", {
      className: `direction-badge${outgoing ? "" : " direction-in"}`,
      text: outgoing ? "SIM \u2192 WEBCTRL" : "WEBCTRL \u2192 SIM",
    }));
    row.appendChild(directionCell);
    appendCell(row, formatValue(point.present_value), "cell-primary cell-mono");
    appendCell(row, point.units || "\u2014");
    const statusCell = createElement("td");
    statusCell.appendChild(pointStatusElements(point));
    row.appendChild(statusCell);
    const actionCell = createElement("td");
    const target = createElement("button", {
      className: "icon-button table-action",
      type: "button",
      title: `Target ${point.group}.${point.alias} in Operations`,
      attrs: { "aria-label": `Target ${point.group}.${point.alias} in Operations` },
    }, createIcon("fa-crosshairs"));
    target.addEventListener("click", () => targetPointInOperations(point));
    actionCell.appendChild(target);
    row.appendChild(actionCell);
    fragment.appendChild(row);
  }
  replaceChildren($("equipment-table-body"), fragment);
}

function optionFor(value, label) {
  return createElement("option", { text: label, attrs: { value } });
}

function rebuildSelect(select, options, placeholder, preserve = true) {
  const previous = preserve ? select.value : "";
  const fragment = document.createDocumentFragment();
  if (placeholder !== null) fragment.appendChild(optionFor("", placeholder));
  for (const option of options) fragment.appendChild(optionFor(option.value, option.label));
  replaceChildren(select, fragment);
  if (previous && options.some((option) => option.value === previous)) select.value = previous;
}

function populateGroupControls() {
  const groups = state.status?.groups || [];
  const signature = groups.map((group) => `${group.group_id}:${group.point_count}`).join("|");
  if (signature === state.groupSignature) return;
  state.groupSignature = signature;
  const options = groups.map((group) => ({ value: group.group_id, label: `${group.group_id} \u00B7 ${group.point_count} points` }));
  rebuildSelect($("equipment-group-filter"), options, "All equipment groups");
  rebuildSelect($("fault-group"), options, "Select a group");
  rebuildSelect($("force-group"), options, "Select a group");
  updatePointSelect("fault-group", "fault-point");
  updatePointSelect("force-group", "force-point");
}

function updatePointSelect(groupSelectId, pointSelectId, preferredAlias = "") {
  const group = $(groupSelectId).value;
  const safetyBypass = pointSelectId === "fault-point" && $("fault-type")?.value === "safety_bypass";
  const safetyAliases = new Set(["automatic_high_static_trip", "automatic_freezestat_trip"]);
  const points = state.points
    .filter((point) => point.group === group)
    .filter((point) => !safetyBypass || (group === "ACI-SIM-AHU-1" && safetyAliases.has(point.alias)))
    .sort((a, b) => String(a.alias).localeCompare(String(b.alias)));
  const options = points.map((point) => ({
    value: point.alias,
    label: `${point.alias} \u00B7 ${point.object_name}${point.writable ? " \u00B7 writable" : ""}`,
  }));
  rebuildSelect($(pointSelectId), options, group ? "Select a point alias" : "Select a group first");
  if (preferredAlias && options.some((option) => option.value === preferredAlias)) $(pointSelectId).value = preferredAlias;
}

function targetPointInOperations(point) {
  activateView("operations");
  $("fault-group").value = point.group;
  $("force-group").value = point.group;
  updatePointSelect("fault-group", "fault-point", point.alias);
  updatePointSelect("force-group", "force-point", point.alias);
  $("fault-point").focus();
  toast(`Targeted ${point.group}.${point.alias} in Operations`);
}

function renderActiveScenario() {
  const scenario = state.status?.scenario || {};
  const running = Boolean(scenario.running);
  const stop = $("scenario-stop");
  const reset = $("scenario-reset");
  if (stop) stop.disabled = !state.online || !running;
  if (reset) reset.disabled = !state.online;
  const container = $("active-scenario");
  if (!container) return;
  if (!running && scenario.status !== "completed") {
    replaceChildren(container, createElement("div", { className: "empty-state compact" }, [
      createIcon("fa-flask"),
      createElement("span", { text: "No scenario is running." }),
    ]));
    return;
  }
  const card = createElement("div", { className: "scenario-live-card" });
  const head = createElement("div", { className: "scenario-live-head" });
  head.append(
    createElement("h4", { text: scenario.title || scenario.scenario_id || "Scenario" }),
    createElement("span", { className: `state-pill ${running ? "state-running" : "state-idle"}`, text: running ? "RUNNING" : "COMPLETED" }),
  );
  const total = Number(scenario.events_total) || 0;
  const fired = Number(scenario.events_fired) || 0;
  const percentage = total ? Math.min(100, Math.round((fired / total) * 100)) : running ? 0 : 100;
  const meta = createElement("div", { className: "scenario-meta" });
  meta.append(
    createElement("span", { text: `Elapsed ${formatDuration(scenario.elapsed_seconds)}` }),
    createElement("span", { text: `${fired}/${total} events` }),
  );
  const progress = createElement("div", { className: "scenario-progress", attrs: { role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": percentage } });
  const fill = createElement("span");
  fill.style.width = `${percentage}%`;
  progress.appendChild(fill);
  card.append(head, meta, progress);
  if (scenario.next_event) {
    card.appendChild(createElement("p", {
      className: "form-note",
      text: `Next at t+${scenario.next_event.time_seconds}s \u00B7 ${scenario.next_event.description}`,
    }));
  }
  replaceChildren(container, card);
}

function renderScenarioLibrary() {
  const list = $("scenario-list");
  if (!state.scenarios.length) {
    replaceChildren(list, createElement("div", { className: "empty-state compact" }, [
      createIcon("fa-circle-notch", "fa-spin"),
      createElement("span", { text: "Loading scenario library\u2026" }),
    ]));
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const scenario of state.scenarios) {
    const card = createElement("article", { className: "scenario-card" });
    card.append(
      createElement("h4", { text: scenario.title || scenario.scenario_id }),
      createElement("p", { text: scenario.description || "No scenario description provided." }),
    );
    if (Array.isArray(scenario.student_objectives) && scenario.student_objectives.length) {
      const objectives = createElement("ul", { className: "scenario-objectives" });
      for (const objective of scenario.student_objectives.slice(0, 2)) {
        objectives.appendChild(createElement("li", { text: objective }));
      }
      card.appendChild(objectives);
    }
    const footer = createElement("div", { className: "scenario-card-footer" });
    footer.appendChild(createElement("span", { text: `${scenario.event_count || 0} TIMED EVENTS` }));
    const run = createElement("button", { className: "button button-primary button-small", type: "button", text: "RUN SCENARIO" });
    run.disabled = !state.online;
    run.addEventListener("click", async () => {
      const current = state.status?.scenario || {};
      if (current.running) {
        const confirmed = await confirmAction(
          "Replace Running Scenario",
          `Starting \u201C${scenario.title}\u201D will stop \u201C${current.title || current.scenario_id}\u201D and clear the faults it created.`,
          "REPLACE & RUN",
        );
        if (!confirmed) return;
      }
      await runAction(
        run,
        () => fetchJSON(`/api/scenarios/${encodeURIComponent(scenario.scenario_id)}/start`, { method: "POST" }),
        `Scenario started: ${scenario.title}`,
      );
    });
    footer.appendChild(run);
    card.appendChild(footer);
    fragment.appendChild(card);
  }
  replaceChildren(list, fragment);
}

function formatFaultParameters(parameters) {
  const entries = Object.entries(parameters || {}).filter(([key]) => !key.startsWith("_"));
  if (!entries.length) return "No parameters";
  return entries.map(([key, value]) => `${titleCase(key)} ${formatValue(value)}`).join(" \u00B7 ");
}

function renderFaults() {
  const list = $("fault-list");
  $("fault-clear-all").disabled = !state.online || state.faults.length === 0;
  if (!state.faults.length) {
    replaceChildren(list, createElement("div", { className: "empty-state compact" }, [
      createIcon("fa-shield-halved"),
      createElement("span", { text: "No active faults or overrides." }),
    ]));
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const fault of state.faults) {
    const item = createElement("div", { className: "fault-item" });
    const target = fault.alias ? `${fault.group_id}.${fault.alias}` : "Whole BACnet device";
    const copy = createElement("div", { className: "fault-copy" });
    copy.append(
      createElement("strong", { text: `${titleCase(fault.fault_type)} \u00B7 ${target}` }),
      createElement("small", { text: formatFaultParameters(fault.parameters) }),
    );
    const clear = createElement("button", { className: "button button-secondary button-small", type: "button", text: "CLEAR" });
    clear.disabled = !state.online;
    clear.addEventListener("click", () => runAction(
      clear,
      () => fetchJSON(`/api/faults/clear?fault_id=${encodeURIComponent(fault.fault_id)}`, { method: "POST" }),
      `Cleared ${titleCase(fault.fault_type)}`,
    ));
    item.append(copy, clear);
    fragment.appendChild(item);
  }
  replaceChildren(list, fragment);
}

function updateFaultForm() {
  const faultType = $("fault-type").value;
  const spec = FAULT_SPECS[faultType] || { label: titleCase(faultType), note: "Configure the selected simulator fault." };
  const transport = TRANSPORT_FAULTS.has(faultType);
  $("fault-target-fields").hidden = transport;
  $("fault-group").required = !transport;
  $("fault-point").required = !transport;
  if (faultType === "safety_bypass") {
    $("fault-group").value = "ACI-SIM-AHU-1";
    updatePointSelect("fault-group", "fault-point");
  } else if (!transport) {
    updatePointSelect("fault-group", "fault-point");
  }
  const parameterField = $("fault-parameter-field");
  const input = $("fault-parameter");
  parameterField.hidden = !spec.parameter;
  input.required = Boolean(spec.parameter);
  if (spec.parameter) {
    setText("fault-parameter-label", spec.parameter.label);
    setText("fault-parameter-help", spec.parameter.help);
    for (const attribute of ["min", "max", "step"]) {
      if (spec.parameter[attribute] !== undefined) input.setAttribute(attribute, spec.parameter[attribute]);
      else input.removeAttribute(attribute);
    }
  } else {
    input.value = "";
    input.removeAttribute("min");
    input.removeAttribute("max");
    input.removeAttribute("step");
  }
  setText("fault-scope-note", spec.note);
}

function populateFaultTypes() {
  const source = state.faultTypes.length ? state.faultTypes : Object.keys(FAULT_SPECS);
  const options = source.map((value) => ({ value, label: FAULT_SPECS[value]?.label || titleCase(value) }));
  rebuildSelect($("fault-type"), options, null, false);
  updateFaultForm();
}

async function loadLibraries() {
  const [scenarioResult, typeResult] = await Promise.allSettled([
    fetchJSON("/api/scenarios"),
    fetchJSON("/api/fault-types"),
  ]);
  if (scenarioResult.status === "fulfilled" && Array.isArray(scenarioResult.value)) {
    state.scenarios = scenarioResult.value;
    renderScenarioLibrary();
  }
  if (typeResult.status === "fulfilled" && Array.isArray(typeResult.value)) {
    state.faultTypes = typeResult.value;
    populateFaultTypes();
  } else if (!$("fault-type").options.length) {
    populateFaultTypes();
  }
}

async function refreshCore(force = false) {
  if (state.coreInFlight && !force) return;
  state.coreInFlight = true;
  try {
    const [statusResult, pointsResult, faultsResult, commandResult] = await Promise.allSettled([
      fetchJSON("/api/status"),
      fetchJSON("/api/points"),
      fetchJSON("/api/faults"),
      fetchJSON("/api/command-center"),
    ]);

    if (statusResult.status !== "fulfilled") {
      setConnection(false, `Simulator API unavailable: ${statusResult.reason.message}. Existing values may be stale.`);
      return;
    }

    state.status = statusResult.value;
    if (pointsResult.status === "fulfilled" && Array.isArray(pointsResult.value)) state.points = pointsResult.value;
    if (faultsResult.status === "fulfilled" && Array.isArray(faultsResult.value)) state.faults = faultsResult.value;
    state.commandCenter = commandResult.status === "fulfilled" ? commandResult.value : null;
    state.lastUpdatedAt = Date.now();
    setConnection(true);
    renderStatus();
    renderDigitalTwin();
  } finally {
    state.coreInFlight = false;
  }
}

async function refreshLogs() {
  if (state.activeView !== "logs" || state.logsInFlight) return;
  state.logsInFlight = true;
  try {
    const [appResult, bacnetResult] = await Promise.allSettled([
      fetchJSON("/api/logs/app?limit=120"),
      fetchJSON("/api/logs/bacnet?limit=120"),
    ]);
    if (appResult.status === "fulfilled") renderLog("application-log", "follow-app", appResult.value);
    if (bacnetResult.status === "fulfilled") renderLog("bacnet-log", "follow-bacnet", bacnetResult.value);
  } finally {
    state.logsInFlight = false;
  }
}

function renderLog(outputId, followId, lines) {
  const output = $(outputId);
  const text = Array.isArray(lines) ? lines.join("\n") : String(lines || "");
  if (output.textContent !== text) {
    const previousTop = output.scrollTop;
    output.textContent = text || "No entries.";
    output.scrollTop = $(followId).checked ? output.scrollHeight : previousTop;
  }
}

async function refreshLlmStatus() {
  const button = $("llm-test");
  const pill = $("llm-state");
  button.disabled = true;
  button.querySelector("i").classList.add("fa-spin");
  pill.className = "state-pill state-starting";
  pill.textContent = "CHECKING";
  try {
    const result = await fetchJSON("/api/llm/status", {}, 15000);
    pill.className = `state-pill ${result.connected ? "state-running" : "state-failure"}`;
    pill.textContent = result.connected ? "CONNECTED" : "NOT REACHABLE";
    setText("llm-host", result.host);
    setText("llm-model", result.configured_model);
    setText("llm-models", Array.isArray(result.available_models) && result.available_models.length
      ? result.available_models.join(", ")
      : result.error || "None found");
  } catch (error) {
    pill.className = "state-pill state-failure";
    pill.textContent = "ERROR";
    setText("llm-models", error.message);
  } finally {
    button.disabled = false;
    button.querySelector("i").classList.remove("fa-spin");
  }
}

function appendProposalSummary(label, value) {
  const card = createElement("div", { className: "proposal-summary-card" });
  card.append(createElement("span", { text: label }), createElement("strong", { text: value }));
  $("proposal-summary").appendChild(card);
}

function renderProposal(bundle, valid, errors = []) {
  state.llmBundle = bundle;
  $("proposal-panel").hidden = false;
  const pill = $("proposal-state");
  pill.className = `state-pill ${valid ? "state-running" : "state-failure"}`;
  pill.textContent = valid ? "VALIDATED" : "REJECTED";
  replaceChildren($("proposal-summary"));
  appendProposalSummary("Intent", titleCase(bundle.intent || "Action bundle"));
  appendProposalSummary("Confidence", formatValue(bundle.confidence, 2));
  appendProposalSummary("Summary", bundle.summary || "No summary provided.");

  const actionsContainer = $("proposal-actions");
  const fragment = document.createDocumentFragment();
  for (const [index, action] of (bundle.actions || []).entries()) {
    const card = createElement("article", { className: "proposal-action-card" });
    card.append(
      createElement("span", { text: `ACTION ${index + 1}` }),
      createElement("h4", { text: titleCase(action.action || action.action_type || action.type || "Simulator action") }),
    );
    const details = createElement("div", { className: "proposal-action-details" });
    for (const [key, value] of Object.entries(action)) {
      if (["action", "action_type", "type"].includes(key)) continue;
      const row = createElement("div");
      row.append(createElement("strong", { text: titleCase(key) }), createElement("span", { text: shortValue(value) }));
      details.appendChild(row);
    }
    card.appendChild(details);
    fragment.appendChild(card);
  }
  if (!(bundle.actions || []).length) {
    fragment.appendChild(createElement("div", { className: "empty-state compact" }, [
      createIcon("fa-circle-info"),
      createElement("span", { text: "This is an explanatory response; there are no actions to apply." }),
    ]));
  }
  if (!valid && errors.length) {
    const rejected = createElement("article", { className: "proposal-action-card" });
    rejected.append(
      createElement("span", { text: "VALIDATION ERRORS" }),
      createElement("h4", { text: errors.join(" \u00B7 ") }),
    );
    fragment.appendChild(rejected);
  }
  replaceChildren(actionsContainer, fragment);
  $("proposal-apply").disabled = !valid || !(bundle.actions || []).length || !state.online;
  $("proposal-panel").scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    block: "nearest",
  });
}

function dismissProposal() {
  state.llmBundle = null;
  $("proposal-panel").hidden = true;
  replaceChildren($("proposal-summary"));
  replaceChildren($("proposal-actions"));
}

async function refreshAudit() {
  if (state.activeView !== "ai" || state.auditInFlight) return;
  state.auditInFlight = true;
  try {
    const entries = await fetchJSON("/api/llm/audit?limit=40");
    const fragment = document.createDocumentFragment();
    for (const entry of [...(Array.isArray(entries) ? entries : [])].reverse()) {
      const row = createElement("tr");
      appendCell(row, formatTimestamp(entry.timestamp), "cell-mono");
      appendCell(row, titleCase(entry.event_type), "cell-primary");
      appendCell(row, entry.request_id || "\u2014", "cell-mono");
      const detail = entry.error
        || entry.bundle?.summary
        || shortValue(entry.action_results)
        || "\u2014";
      appendCell(row, detail);
      fragment.appendChild(row);
    }
    if (!fragment.childNodes.length) {
      const row = createElement("tr");
      const cell = createElement("td", { text: "No AI audit entries yet.", attrs: { colspan: "4" } });
      row.appendChild(cell);
      fragment.appendChild(row);
    }
    replaceChildren($("audit-table-body"), fragment);
  } catch {
    // Audit is optional and must not affect the live-link state.
  } finally {
    state.auditInFlight = false;
  }
}

function parseForceValue(raw) {
  const trimmed = String(raw).trim();
  if (!trimmed) throw new Error("A force value is required");
  const lower = trimmed.toLowerCase();
  if (lower === "true") return true;
  if (lower === "false") return false;
  const numeric = Number(trimmed);
  if (!Number.isFinite(numeric)) throw new Error("Force value must be a number, true, or false");
  return numeric;
}

function bindNavigation() {
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => activateView(button.dataset.view));
  });
  window.addEventListener("hashchange", () => {
    activateView(HASH_TO_VIEW[window.location.hash] || "twin", false);
  });
}

function bindTwinControls() {
  document.querySelectorAll("[data-floor]").forEach((button) => {
    button.addEventListener("click", () => {
      state.floor = button.dataset.floor;
      updateFloorButtons();
      renderAirflow();
      renderMarkers();
    });
  });

  $("inspect-equipment").addEventListener("click", () => {
    const selected = state.locations.find((location) => location.id === state.selectedLocationId);
    if (!selected?.groupId) return;
    activateView("equipment");
    $("equipment-group-filter").value = selected.groupId;
    $("equipment-search").value = "";
    renderEquipment();
    $("equipment-search").focus();
  });

  $("engine-control").addEventListener("click", async () => {
    const running = Boolean(state.status?.simulation?.running);
    if (running) {
      const confirmed = await confirmAction(
        "Stop Simulation Engine",
        "Stopping the engine freezes simulated values at their last state. Active faults and scenarios remain configured.",
        "STOP ENGINE",
      );
      if (!confirmed) return;
    }
    await runAction(
      $("engine-control"),
      () => fetchJSON(`/api/simulation/${running ? "stop" : "start"}`, { method: "POST" }),
      running ? "Simulation engine stopped" : "Simulation engine started",
    );
  });

  $("speed-control").addEventListener("change", () => {
    const speed = $("speed-control").value;
    runAction(
      $("speed-control"),
      () => fetchJSON(`/api/simulation/speed/${encodeURIComponent(speed)}`, { method: "POST" }),
      `Simulation time rate set to ${speed}\u00D7`,
    );
  });
}

function bindDuctStaticControls() {
  document.querySelectorAll("[data-ahu-component]").forEach((button) => {
    button.addEventListener("click", () => {
      state.ahuSelectedComponent = button.dataset.ahuComponent;
      if (state.ahuNormalized) renderAhuInspector(state.ahuNormalized);
    });
  });

  $("ahu-motion-toggle").addEventListener("click", () => {
    state.ahuMotionPaused = !state.ahuMotionPaused;
    const button = $("ahu-motion-toggle");
    button.setAttribute("aria-pressed", String(state.ahuMotionPaused));
    button.querySelector("i").className = `fa-solid ${state.ahuMotionPaused ? "fa-play" : "fa-pause"}`;
    button.querySelector("span").textContent = state.ahuMotionPaused ? "RESUME MOTION" : "PAUSE MOTION";
    $("ahu-command-stage").classList.toggle("motion-paused", state.ahuMotionPaused);
    if (state.ahuMotionPaused) $("ahu-command-stage").classList.remove("is-catastrophic");
  });

  for (const id of ["pid-kp", "pid-ki", "pid-kd", "pid-interval"]) {
    $(id).addEventListener("input", () => {
      state.pidTuningDirty = true;
      $("pid-tuning-state").className = "state-pill state-starting";
      $("pid-tuning-state").textContent = "UNSAVED";
    });
  }

  $("pid-tuning-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const tuning = {
      kp: Number($("pid-kp").value),
      ki: Number($("pid-ki").value),
      kd: Number($("pid-kd").value),
      interval_seconds: Number($("pid-interval").value),
    };
    if (Object.values(tuning).some((value) => !Number.isFinite(value))) {
      toast("Enter a valid number for every PID setting", "warning");
      return;
    }
    const result = await runAction(
      event.submitter,
      () => fetchJSON("/api/ahu/duct-static/pid", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(tuning),
      }),
      "Duct-static PID tuning applied",
      { refresh: false },
    );
    if (result) {
      state.pidTuningDirty = false;
      state.ductStatic = result;
      renderDuctStatic();
    }
  });

  $("pid-reset-memory").addEventListener("click", async () => {
    const result = await runAction(
      $("pid-reset-memory"),
      () => fetchJSON("/api/ahu/duct-static/pid/reset", { method: "POST" }),
      "PID integral and derivative memory reset",
      { refresh: false },
    );
    if (result) {
      state.ductStatic = result;
      renderDuctStatic();
    }
  });

  $("pid-restore-defaults").addEventListener("click", async () => {
    const confirmed = await confirmAction(
      "Restore Recommended PID Tuning",
      "This restores the recommended P, I, D, and calculation interval and clears controller memory. It does not restart the simulation or change the WebCTRL pressure setpoint.",
      "RESTORE DEFAULTS",
    );
    if (!confirmed) return;
    const result = await runAction(
      $("pid-restore-defaults"),
      () => fetchJSON("/api/ahu/duct-static/pid/defaults", { method: "POST" }),
      "Recommended PID defaults restored",
      { refresh: false },
    );
    if (result) {
      state.pidTuningDirty = false;
      state.ductStatic = result;
      renderDuctStatic();
    }
  });

  window.addEventListener("resize", () => {
    if (state.activeView === "duct-static") drawDuctStaticTrend();
  });
}

function bindOperations() {
  $("equipment-group-filter").addEventListener("change", renderEquipment);
  $("equipment-search").addEventListener("input", renderEquipment);
  $("fault-group").addEventListener("change", () => updatePointSelect("fault-group", "fault-point"));
  $("force-group").addEventListener("change", () => updatePointSelect("force-group", "force-point"));
  $("fault-type").addEventListener("change", updateFaultForm);

  $("weather-temp").addEventListener("input", () => { state.weatherDirty = true; });
  $("weather-humidity").addEventListener("input", () => { state.weatherDirty = true; });
  $("weather-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const temperature = Number($("weather-temp").value);
    const humidity = Number($("weather-humidity").value);
    if (!Number.isFinite(temperature) || temperature < -40 || temperature > 130) {
      toast("Outdoor-air target must be between -40\u00B0F and 130\u00B0F", "warning");
      $("weather-temp").focus();
      return;
    }
    if (!Number.isFinite(humidity) || humidity < 0 || humidity > 100) {
      toast("Humidity target must be between 0% and 100%", "warning");
      $("weather-humidity").focus();
      return;
    }
    await runAction(
      event.submitter,
      () => fetchJSON(`/api/site/weather?oa_temp_f=${encodeURIComponent(temperature)}&oa_humidity_pct=${encodeURIComponent(humidity)}`, { method: "POST" }),
      `Site targets set to ${temperature}\u00B0F / ${humidity}%RH`,
    );
    state.weatherDirty = false;
  });

  $("fault-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const faultType = $("fault-type").value;
    const spec = FAULT_SPECS[faultType] || {};
    const transport = TRANSPORT_FAULTS.has(faultType);
    const groupId = transport ? null : $("fault-group").value;
    const alias = transport ? null : $("fault-point").value;
    if (!transport && (!groupId || !alias)) {
      toast("Select a group and point alias", "warning");
      return;
    }
    const parameters = {};
    if (spec.parameter) {
      const numeric = Number($("fault-parameter").value);
      if (!Number.isFinite(numeric)) {
        toast(`${spec.parameter.label} is required`, "warning");
        $("fault-parameter").focus();
        return;
      }
      parameters[spec.parameter.key] = numeric;
    }
    await runAction(
      event.submitter,
      () => fetchJSON("/api/faults/set", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fault_type: faultType, group_id: groupId, alias, parameters }),
      }),
      `${FAULT_SPECS[faultType]?.label || titleCase(faultType)} activated`,
    );
  });

  $("force-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const groupId = $("force-group").value;
    const alias = $("force-point").value;
    let value;
    try {
      value = parseForceValue($("force-value").value);
    } catch (error) {
      toast(error.message, "warning");
      $("force-value").focus();
      return;
    }
    await runAction(
      event.submitter,
      () => fetchJSON("/api/force", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: groupId, alias, value }),
      }),
      `Forced ${groupId}.${alias} to ${formatValue(value)}`,
    );
  });

  $("force-release").addEventListener("click", async () => {
    const groupId = $("force-group").value;
    const alias = $("force-point").value;
    if (!groupId || !alias) {
      toast("Select a group and point alias to release", "warning");
      return;
    }
    await runAction(
      $("force-release"),
      () => fetchJSON("/api/release", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: groupId, alias }),
      }),
      `Released ${groupId}.${alias}`,
    );
  });

  $("fault-clear-all").addEventListener("click", async () => {
    const confirmed = await confirmAction(
      "Clear All Faults & Overrides",
      "Every manual fault and forced value will be removed. Equipment returns to normal simulated behavior.",
      "CLEAR ALL",
    );
    if (!confirmed) return;
    await runAction(
      $("fault-clear-all"),
      () => fetchJSON("/api/faults/clear-all", { method: "POST" }),
      "All faults and overrides cleared",
    );
  });

  $("scenario-stop").addEventListener("click", () => runAction(
    $("scenario-stop"),
    () => fetchJSON("/api/scenarios/stop", { method: "POST" }),
    "Scenario stopped",
  ));

  $("scenario-reset").addEventListener("click", async () => {
    const confirmed = await confirmAction(
      "Reset Scenario Engine",
      "This stops the current scenario and clears every fault or force it created, including manual instructor overrides.",
      "RESET ENGINE",
    );
    if (!confirmed) return;
    await runAction(
      $("scenario-reset"),
      () => fetchJSON("/api/scenarios/reset", { method: "POST" }),
      "Scenario engine reset",
    );
  });
}

function bindAi() {
  $("llm-test").addEventListener("click", refreshLlmStatus);
  $("llm-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const request = $("llm-prompt").value.trim();
    if (!request) return;
    const button = event.submitter;
    button.disabled = true;
    setText("llm-propose-status", "Consulting the configured model\u2026");
    try {
      const result = await fetchJSON("/api/llm/propose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instructor_request: request }),
      }, LONG_REQUEST_TIMEOUT_MS);
      setText("llm-propose-status", "");
      renderProposal(result.bundle || {}, Boolean(result.valid), result.validation_errors || []);
      await refreshAudit();
    } catch (error) {
      setText("llm-propose-status", `Failed: ${error.message}`);
      toast(`Proposal failed: ${error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  });

  $("proposal-dismiss").addEventListener("click", dismissProposal);
  $("proposal-apply").addEventListener("click", async () => {
    if (!state.llmBundle) return;
    const result = await runAction(
      $("proposal-apply"),
      () => fetchJSON("/api/llm/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bundle: state.llmBundle }),
      }),
      "AI action bundle applied",
      { refreshLibrary: true },
    );
    if (result?.applied) {
      dismissProposal();
      $("llm-prompt").value = "";
    }
    await refreshAudit();
  });
}

function bindSafetyControls() {
  $("restart-simulation").addEventListener("click", async () => {
    const confirmed = await confirmAction(
      "Restart Simulation & BACnet Link",
      "This rebuilds the simulation models, clears faults, safety latches, instructor forces, and scenarios, then restores 1\u00D7 speed with default PID tuning. WebCTRL-owned BACnet command priorities, the live object graph, and COV subscriptions stay attached while an I-Am announcement refreshes the binding.",
      "RESTART SIMULATION",
    );
    if (!confirmed) return;
    const result = await runAction(
      $("restart-simulation"),
      () => fetchJSON(
        "/api/simulation/restart",
        { method: "POST" },
        20000,
      ),
      "Simulation restarted; WebCTRL BACnet/COV session preserved",
      { refreshLibrary: true },
    );
    if (result?.restarted) {
      state.pidTuningDirty = false;
      await refreshDuctStatic(true);
    }
  });

  $("stop-all").addEventListener("click", async () => {
    const confirmed = await confirmAction(
      "Stop All Simulation",
      "This stops the engine and clears every active fault, forced value, and running scenario. Connected controllers will see values freeze at their last state.",
      "STOP EVERYTHING",
    );
    if (!confirmed) return;
    await runAction(
      $("stop-all"),
      () => fetchJSON("/api/simulation/stop-all", { method: "POST" }),
      "All simulation stopped; faults, forces, and scenarios cleared",
    );
  });

  $("confirm-cancel").addEventListener("click", () => closeConfirmation(false));
  $("confirm-accept").addEventListener("click", () => closeConfirmation(true));
  $("confirm-dialog").addEventListener("close", finishConfirmation);
  $("confirm-dialog").addEventListener("cancel", () => {
    $("confirm-dialog").returnValue = "cancel";
  });
}

function bindLogs() {
  $("logs-refresh").addEventListener("click", refreshLogs);
}

function bindVisibility() {
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refreshCore(true);
      if (state.activeView === "logs") refreshLogs();
      if (state.activeView === "ai") refreshAudit();
    }
  });
}

function startPolling() {
  window.setInterval(() => {
    if (!document.hidden) refreshCore();
  }, CORE_POLL_MS);
  window.setInterval(() => {
    if (!document.hidden && state.activeView === "logs") refreshLogs();
  }, CORE_POLL_MS);
  window.setInterval(() => {
    if (!document.hidden && state.activeView === "ai") refreshAudit();
  }, 5000);
  window.setInterval(() => {
    if (!document.hidden && state.activeView === "duct-static") refreshDuctStatic();
  }, 1000);
  window.setInterval(updateClock, 1000);
}

async function boot() {
  bindNavigation();
  bindTwinControls();
  bindDuctStaticControls();
  bindOperations();
  bindAi();
  bindSafetyControls();
  bindLogs();
  bindVisibility();
  activateView(HASH_TO_VIEW[window.location.hash] || "twin", false);
  updateClock();
  await Promise.all([refreshCore(true), loadLibraries()]);
  startPolling();
}

boot().catch((error) => {
  setConnection(false, `Interface startup failed: ${error.message}`);
  toast(`Interface startup failed: ${error.message}`, "error");
});
