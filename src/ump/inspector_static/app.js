const view = { selected: null, robots: [], events: [] };
const byId = (id) => document.getElementById(id);
const text = (value) => value === null || value === undefined || value === "" ? "-" : String(value);

function eventSummary(event) {
  const payload = event.payload || {};
  return payload.summary || payload.description || payload.activity || payload.goal_id || payload.plan_id || payload.assignment_id || "Protocol message";
}

function selectRobot(robotId) {
  view.selected = robotId;
  render();
}

function render() {
  byId("robot-count").textContent = view.robots.length;
  byId("robots").replaceChildren(...view.robots.map((robot) => {
    const button = document.createElement("button");
    button.className = robot.robot_id === view.selected ? "active" : "";
    button.onclick = () => selectRobot(robot.robot_id);
    const name = document.createElement("span");
    name.className = "robot-name";
    name.textContent = robot.robot_id;
    const meta = document.createElement("span");
    meta.className = "robot-meta";
    meta.textContent = robot.manifest ? `${robot.manifest.manufacturer} · ${robot.manifest.robot_class}` : "Manifest pending";
    button.append(name, meta);
    return button;
  }));
  const robot = view.robots.find((item) => item.robot_id === view.selected);
  const state = robot?.state;
  byId("selected-name").textContent = robot?.robot_id || "No robot observed";
  byId("selected-mode").textContent = text(state?.mode);
  byId("selected-safety").textContent = text(state?.safety);
  byId("selected-progress").textContent = state ? `${Math.round(state.progress * 100)}%` : "-";
  const details = [
    ["Activity", state?.activity], ["Intent", state?.intent], ["Summary", state?.summary],
    ["Assignment", state?.assignment_id],
    ["Pose", state?.pose ? `${state.pose.frame_id} · ${state.pose.position_m.join(", ")} m` : null],
    ["Sensor refs", state?.sensor_references?.length],
    ["Resources", state?.resources?.join(", ")], ["Blockers", state?.blockers?.join(", ")]
  ];
  byId("state-detail").replaceChildren(...details.flatMap(([label, value]) => {
    const dt = document.createElement("dt"); dt.textContent = label;
    const dd = document.createElement("dd"); dd.textContent = text(value);
    return [dt, dd];
  }));
  const capabilities = robot?.manifest?.capabilities || [];
  byId("capabilities").replaceChildren(...(capabilities.length ? capabilities.map((item) => {
    const row = document.createElement("div"); row.className = "capability";
    const name = document.createElement("strong"); name.textContent = item.name;
    const description = document.createElement("span"); description.textContent = `${item.availability} · ${item.description}`;
    row.append(name, description); return row;
  }) : [Object.assign(document.createElement("p"), { className: "empty", textContent: "No disclosed capabilities" })]));
  byId("events").replaceChildren(...view.events.map((event) => {
    const row = document.createElement("tr");
    const values = [new Date(event.timestamp_ms).toLocaleTimeString(), event.message_type, event.source_id, event.correlation_id, eventSummary(event)];
    values.forEach((value, index) => { const cell = document.createElement("td"); cell.textContent = text(value); if (index === 1) cell.className = "event-type"; row.append(cell); });
    return row;
  }));
}

async function refresh() {
  try {
    const response = await fetch("/api/snapshot?limit=200", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    view.robots = data.robots; view.events = data.events;
    if (!view.selected || !view.robots.some((item) => item.robot_id === view.selected)) view.selected = view.robots[0]?.robot_id || null;
    byId("event-count").textContent = `${data.event_count} events`;
    byId("connection").textContent = "Live"; byId("connection-dot").className = "online";
    render();
  } catch (error) {
    byId("connection").textContent = "Unavailable"; byId("connection-dot").className = "";
  }
}

refresh();
setInterval(refresh, 1000);
