const view = { selected: null, robots: [], events: [], labEvents: [], query: "", activeView: "fleet" };
const byId = (id) => document.getElementById(id);
const text = (value) => value === null || value === undefined || value === "" ? "-" : String(value);
const normalize = (value) => String(value || "unknown").replaceAll("_", "-");

function eventSummary(event) {
  const payload = event.payload || {};
  return payload.summary || payload.description || payload.activity || payload.goal_id || payload.plan_id || payload.assignment_id || "Protocol message";
}

function batterySummary(battery) {
  if (!battery) return "Unknown";
  if (battery.status === "not_present") return "Not present";
  const level = battery.level === null || battery.level === undefined ? "Unknown" : `${Math.round(battery.level * 100)}%`;
  return `${level} / ${String(battery.status || "unknown").replaceAll("_", " ")}`;
}

function freshness(observedAt) {
  if (observedAt === null || observedAt === undefined) return { label: "Unknown", tone: "neutral", fresh: false };
  const age = Math.max(0, Date.now() - observedAt);
  if (age < 5000) return { label: `${Math.round(age / 1000)}s`, tone: "good", fresh: true };
  if (age < 30000) return { label: `${Math.round(age / 1000)}s`, tone: "warning", fresh: false };
  const minutes = Math.floor(age / 60000);
  return { label: minutes > 99 ? ">99m" : `${minutes}m`, tone: "danger", fresh: false };
}

function toneFor(value) {
  const normalized = normalize(value);
  if (["healthy", "normal", "idle", "available", "completed", "passed", "full"].includes(normalized)) return "good";
  if (["degraded", "charging", "waiting", "paused", "unknown"].includes(normalized)) return "warning";
  if (["faulted", "emergency-stop", "protective-stop", "recovery-required", "offline", "rejected", "failed"].includes(normalized)) return "danger";
  if (["working", "starting", "running"].includes(normalized)) return "info";
  return "neutral";
}

function statusLabel(value) {
  const span = document.createElement("span");
  span.className = `status-label ${toneFor(value)}`;
  const dot = document.createElement("span"); dot.className = "status-dot"; dot.setAttribute("aria-hidden", "true");
  const label = document.createElement("span"); label.textContent = String(value || "unknown").replaceAll("_", " ");
  span.append(dot, label);
  return span;
}

function definitionList(target, values) {
  target.replaceChildren(...values.flatMap(([label, value]) => {
    const dt = document.createElement("dt"); dt.textContent = label;
    const dd = document.createElement("dd"); dd.textContent = text(value);
    return [dt, dd];
  }));
}

function selectRobot(robotId) { view.selected = robotId; renderFleet(); }

function filteredRobots() {
  const query = view.query.trim().toLowerCase();
  if (!query) return view.robots;
  return view.robots.filter((robot) => [robot.robot_id, robot.manifest?.manufacturer, robot.manifest?.robot_class, robot.integration?.source_standard]
    .some((value) => String(value || "").toLowerCase().includes(query)));
}

function renderMetrics() {
  const available = view.robots.filter((robot) => robot.state?.mode === "idle" || robot.state?.availability === "available").length;
  const attention = view.robots.filter((robot) => ["degraded", "faulted"].includes(robot.state?.health) || ["emergency_stop", "protective_stop", "recovery_required"].includes(robot.state?.safety)).length;
  const fresh = view.robots.filter((robot) => freshness(robot.state_observed_at_ms).fresh).length;
  byId("metric-robots").textContent = view.robots.length;
  byId("metric-available").textContent = available;
  byId("metric-attention").textContent = attention;
  byId("metric-events").textContent = view.events.length;
  byId("metric-freshness").textContent = view.robots.length ? `${fresh} / ${view.robots.length}` : "-";
}

function renderFleet() {
  const robots = filteredRobots();
  const hasRobots = view.robots.length > 0;
  byId("fleet-empty").hidden = hasRobots;
  byId("fleet-layout").hidden = !hasRobots;
  byId("fleet-result-count").textContent = `${robots.length} of ${view.robots.length} observed`;
  if (robots.length && !robots.some((robot) => robot.robot_id === view.selected)) view.selected = robots[0].robot_id;
  byId("robots").replaceChildren(...(robots.length ? robots.map((robot) => {
    const row = document.createElement("tr");
    row.className = robot.robot_id === view.selected ? "selected" : "";
    row.tabIndex = 0; row.setAttribute("aria-selected", String(robot.robot_id === view.selected));
    row.onclick = () => selectRobot(robot.robot_id);
    row.onkeydown = (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectRobot(robot.robot_id); } };
    const identity = document.createElement("td");
    const id = document.createElement("span"); id.className = "primary-cell"; id.textContent = robot.robot_id;
    const manufacturer = document.createElement("span"); manufacturer.className = "secondary-cell"; manufacturer.textContent = robot.manifest?.manufacturer || "Manifest pending";
    identity.append(id, manufacturer);
    const values = [robot.manifest?.robot_class, robot.state?.activity, statusLabel(robot.state?.health), batterySummary(robot.state?.battery), freshness(robot.state_observed_at_ms).label, robot.integration?.source_standard || "UMP"];
    row.append(identity);
    values.forEach((value, index) => { const cell = document.createElement("td"); index === 2 ? cell.append(value) : cell.textContent = text(value); row.append(cell); });
    return row;
  }) : [Object.assign(document.createElement("tr"), { innerHTML: '<td colspan="7" class="empty">No robots match this search</td>' })]));

  document.querySelector(".detail-pane").hidden = !robots.length;
  let robot = robots.find((item) => item.robot_id === view.selected);
  if (!robot && view.robots.length) { view.selected = view.robots[0].robot_id; robot = view.robots[0]; }
  const state = robot?.state;
  byId("selected-name").textContent = robot?.robot_id || "-";
  const health = byId("selected-health");
  health.className = `status-label ${toneFor(state?.health)}`;
  health.replaceChildren(...statusLabel(state?.health).childNodes);
  definitionList(byId("state-detail"), [
    ["Activity", state?.activity], ["Intent", state?.intent], ["Summary", state?.summary],
    ["Mode", state?.mode], ["Safety", state?.safety], ["Battery", batterySummary(state?.battery)],
    ["Progress", state ? `${Math.round((state.progress || 0) * 100)}%` : null], ["Assignment", state?.assignment_id],
    ["Pose", state?.pose ? `${state.pose.frame_id} / ${state.pose.position_m.join(", ")} m` : null],
    ["State age", robot ? freshness(robot.state_observed_at_ms).label : null], ["Reconnects", robot?.reconnect_count]
  ]);
  const capabilities = robot?.manifest?.capabilities || [];
  byId("capabilities").replaceChildren(...(capabilities.length ? capabilities.map((item) => {
    const row = document.createElement("div"); row.className = "capability";
    const name = document.createElement("strong"); name.textContent = item.name;
    const description = document.createElement("span"); description.textContent = `${item.availability} / ${item.description}`;
    row.append(name, description); return row;
  }) : [Object.assign(document.createElement("p"), { className: "empty", textContent: "No disclosed capabilities" })]));
  definitionList(byId("integration-detail"), [
    ["Source", robot?.integration?.source_standard || "Native UMP"], ["Version", robot?.integration?.source_version],
    ["External ID", robot?.integration?.external_id],
    ["Mapping", robot?.integration?.report?.passed === undefined ? "Not applicable" : robot.integration.report.passed ? "Passed" : "Rejected"],
    ["Warnings", robot?.integration?.report?.warnings?.join(", ") || "None"], ["Session", robot?.session_id]
  ]);
}

function renderEvents() {
  byId("events").replaceChildren(...(view.events.length ? view.events.map((event) => {
    const row = document.createElement("tr");
    const values = [new Date(event.timestamp_ms).toLocaleTimeString(), event.message_type, event.source_id, event.correlation_id, eventSummary(event)];
    values.forEach((value, index) => { const cell = document.createElement("td"); cell.textContent = text(value); if (index === 1) cell.className = "event-type"; row.append(cell); });
    return row;
  }) : [Object.assign(document.createElement("tr"), { innerHTML: '<td colspan="5" class="empty">No protocol events recorded</td>' })]));
  byId("lab-events").replaceChildren(...(view.labEvents.length ? view.labEvents.map((event) => {
    const row = document.createElement("tr");
    [new Date(event.observed_at_ms).toLocaleTimeString(), event.event_type, event.robot_id, event.status, JSON.stringify(event.detail)].forEach((value) => {
      const cell = document.createElement("td"); cell.textContent = text(value); row.append(cell);
    });
    return row;
  }) : [Object.assign(document.createElement("tr"), { innerHTML: '<td colspan="5" class="empty">No validation events recorded</td>' })]));
  byId("event-count").textContent = view.events.length;
  byId("lab-event-count").textContent = view.labEvents.length;
}

function render() { renderMetrics(); renderFleet(); renderEvents(); }

function setActiveView(name) {
  view.activeView = name;
  document.querySelectorAll(".view-tabs button").forEach((button) => {
    const active = button.dataset.view === name; button.classList.toggle("active", active); button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".view-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `view-${name}`));
}

async function refresh() {
  try {
    const response = await fetch("/api/snapshot?limit=200", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    view.robots = data.robots; view.events = data.events; view.labEvents = data.lab_events || [];
    if (!view.selected || !view.robots.some((item) => item.robot_id === view.selected)) view.selected = view.robots[0]?.robot_id || null;
    byId("connection").textContent = "Recorder live"; byId("connection-dot").className = "status-dot good";
    byId("last-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    render();
  } catch (error) {
    byId("connection").textContent = "Recorder unavailable"; byId("connection-dot").className = "status-dot danger";
    byId("last-updated").textContent = "Update failed";
  }
}

document.querySelectorAll(".view-tabs button").forEach((button) => button.addEventListener("click", () => setActiveView(button.dataset.view)));
byId("robot-search").addEventListener("input", (event) => { view.query = event.target.value; renderFleet(); });
refresh();
setInterval(refresh, 1000);
