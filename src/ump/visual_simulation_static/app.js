const state = {
  scenario: null,
  elapsed: 0,
  playing: false,
  speed: 1,
  lastFrame: 0,
  width: 0,
  height: 0,
  dpr: 1,
};

const byId = (id) => document.getElementById(id);
const canvas = byId("simulation-canvas");
const context = canvas.getContext("2d");

const phases = [
  { start: 0, end: 1800, name: "awareness" },
  { start: 1800, end: 4800, name: "inspect" },
  { start: 4800, end: 8000, name: "carry" },
  { start: 8000, end: 10500, name: "place" },
  { start: 10500, end: 12000, name: "complete" },
];

function clamp(value, minimum = 0, maximum = 1) {
  return Math.min(maximum, Math.max(minimum, value));
}

function ease(value) {
  const t = clamp(value);
  return t * t * (3 - 2 * t);
}

function phaseAt(time) {
  return phases.find((phase) => time >= phase.start && time < phase.end) || phases.at(-1);
}

function phaseProgress(name, time) {
  const phase = phases.find((item) => item.name === name);
  return ease((time - phase.start) / (phase.end - phase.start));
}

function resizeCanvas() {
  const bounds = canvas.getBoundingClientRect();
  state.dpr = Math.min(window.devicePixelRatio || 1, 2);
  state.width = Math.max(320, bounds.width);
  state.height = Math.max(320, bounds.height);
  canvas.width = Math.round(state.width * state.dpr);
  canvas.height = Math.round(state.height * state.dpr);
  context.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
}

function point(x, y) {
  return { x: state.width * x, y: state.height * y };
}

function line(from, to, color, width = 1, dash = []) {
  context.save();
  context.strokeStyle = color;
  context.lineWidth = width;
  context.setLineDash(dash);
  context.beginPath();
  context.moveTo(from.x, from.y);
  context.lineTo(to.x, to.y);
  context.stroke();
  context.restore();
}

function warehouse() {
  context.clearRect(0, 0, state.width, state.height);
  context.fillStyle = "#101619";
  context.fillRect(0, 0, state.width, state.height);

  const grid = Math.max(32, Math.min(52, state.width / 18));
  context.strokeStyle = "rgba(77, 94, 103, .18)";
  context.lineWidth = 1;
  for (let x = 0; x < state.width; x += grid) line({ x, y: 0 }, { x, y: state.height }, "rgba(77,94,103,.18)");
  for (let y = 0; y < state.height; y += grid) line({ x: 0, y }, { x: state.width, y }, "rgba(77,94,103,.18)");

  zone(0.08, 0.18, 0.2, 0.62, "INTAKE", "#2b3940");
  zone(0.77, 0.15, 0.15, 0.68, "STORAGE", "#353a34");
  zone(0.35, 0.12, 0.28, 0.17, "INSPECTION AISLE", "#27343a");

  const routeStart = point(0.24, 0.58);
  const routeMiddle = point(0.52, 0.58);
  const routeEnd = point(0.8, 0.58);
  line(routeStart, routeMiddle, "rgba(85,194,217,.7)", 3, [8, 8]);
  line(routeMiddle, routeEnd, "rgba(85,194,217,.7)", 3, [8, 8]);
  context.fillStyle = "rgba(85,194,217,.7)";
  context.beginPath();
  context.moveTo(routeEnd.x, routeEnd.y);
  context.lineTo(routeEnd.x - 12, routeEnd.y - 7);
  context.lineTo(routeEnd.x - 12, routeEnd.y + 7);
  context.closePath();
  context.fill();
}

function zone(x, y, width, height, label, color) {
  const left = state.width * x;
  const top = state.height * y;
  const zoneWidth = state.width * width;
  const zoneHeight = state.height * height;
  context.fillStyle = color;
  context.fillRect(left, top, zoneWidth, zoneHeight);
  context.strokeStyle = "#4a585f";
  context.strokeRect(left, top, zoneWidth, zoneHeight);
  context.fillStyle = "#829198";
  context.font = "700 10px system-ui";
  context.fillText(label, left + 10, top + 19);
}

function robotPosition(robotClass, time) {
  if (robotClass === "quadruped") {
    const progress = phaseProgress("inspect", time);
    return { ...point(0.28 + progress * 0.42, 0.42), heading: 0 };
  }
  if (robotClass === "humanoid") {
    const progress = phaseProgress("carry", time);
    return { ...point(0.24 + progress * 0.52, 0.68), heading: 0 };
  }
  const progress = phaseProgress("place", time);
  return { ...point(0.82, 0.7 - progress * 0.28), heading: -Math.PI / 2 };
}

function drawRobot(robot, time, active) {
  const position = robotPosition(robot.robot_class, time);
  context.save();
  context.translate(position.x, position.y);
  context.rotate(position.heading);
  context.shadowColor = active ? "rgba(53,208,127,.6)" : "transparent";
  context.shadowBlur = active ? 15 : 0;
  if (robot.robot_class === "humanoid") drawHumanoid();
  else if (robot.robot_class === "quadruped") drawQuadruped();
  else drawArm();
  context.restore();

  context.fillStyle = active ? "#eef3f4" : "#a7b3b8";
  context.font = "600 10px system-ui";
  context.textAlign = "center";
  context.fillText(robot.robot_id.replace("robot-", ""), position.x, position.y + 38);
  context.textAlign = "left";
}

function drawHumanoid() {
  context.strokeStyle = "#55c2d9";
  context.fillStyle = "#15282d";
  context.lineWidth = 3;
  context.beginPath(); context.arc(0, -13, 6, 0, Math.PI * 2); context.fill(); context.stroke();
  context.strokeRect(-7, -5, 14, 18);
  line({ x: -7, y: 0 }, { x: -15, y: 10 }, "#55c2d9", 3);
  line({ x: 7, y: 0 }, { x: 15, y: 10 }, "#55c2d9", 3);
  line({ x: -4, y: 13 }, { x: -8, y: 25 }, "#55c2d9", 3);
  line({ x: 4, y: 13 }, { x: 8, y: 25 }, "#55c2d9", 3);
}

function drawQuadruped() {
  context.strokeStyle = "#35d07f";
  context.fillStyle = "#172920";
  context.lineWidth = 3;
  context.fillRect(-15, -8, 28, 16);
  context.strokeRect(-15, -8, 28, 16);
  context.fillRect(13, -6, 8, 11);
  context.strokeRect(13, -6, 8, 11);
  [-11, 8].forEach((x) => {
    line({ x, y: 8 }, { x: x - 3, y: 20 }, "#35d07f", 3);
    line({ x: x + 5, y: 8 }, { x: x + 8, y: 20 }, "#35d07f", 3);
  });
}

function drawArm() {
  context.strokeStyle = "#e98b56";
  context.fillStyle = "#2b211b";
  context.lineWidth = 4;
  context.beginPath(); context.arc(0, 10, 13, 0, Math.PI * 2); context.fill(); context.stroke();
  line({ x: 0, y: 4 }, { x: 0, y: -12 }, "#e98b56", 5);
  line({ x: 0, y: -12 }, { x: 13, y: -23 }, "#e98b56", 5);
  line({ x: 13, y: -23 }, { x: 20, y: -14 }, "#e98b56", 4);
  line({ x: 17, y: -18 }, { x: 24, y: -23 }, "#e98b56", 2);
  line({ x: 19, y: -15 }, { x: 26, y: -11 }, "#e98b56", 2);
}

function packagePosition(time) {
  if (time < 4800) return point(0.22, 0.68);
  if (time < 8000) {
    const robot = robotPosition("humanoid", time);
    return { x: robot.x + 18, y: robot.y };
  }
  const progress = phaseProgress("place", time);
  return point(0.8 + progress * 0.03, 0.67 - progress * 0.25);
}

function drawPackage(time) {
  const position = packagePosition(time);
  context.fillStyle = "#f3bd4d";
  context.strokeStyle = "#ffe19a";
  context.lineWidth = 1;
  context.fillRect(position.x - 9, position.y - 8, 18, 16);
  context.strokeRect(position.x - 9, position.y - 8, 18, 16);
  line({ x: position.x, y: position.y - 8 }, { x: position.x, y: position.y + 8 }, "#9b7124");
}

function activeRobotClass(phase) {
  return { inspect: "quadruped", carry: "humanoid", place: "mobile_arm" }[phase] || null;
}

function renderCanvas() {
  if (!state.scenario) return;
  warehouse();
  drawPackage(state.elapsed);
  const phase = phaseAt(state.elapsed).name;
  state.scenario.robots.forEach((robot) => drawRobot(robot, state.elapsed, robot.robot_class === activeRobotClass(phase)));
}

function activityFor(robotClass, phase) {
  if (phase === "complete") return "Completed";
  if (phase === "awareness") return "Sharing state";
  const active = activeRobotClass(phase) === robotClass;
  if (active) return { inspect: "Inspecting route", carry: "Moving package", place: "Placing package" }[phase];
  return phase === "inspect" ? "Awaiting route" : "Awaiting dependency";
}

function renderInterface() {
  if (!state.scenario) return;
  const phase = phaseAt(state.elapsed);
  const timelineItem = [...state.scenario.timeline].reverse().find((item) => state.elapsed >= item.at_ms);
  byId("phase-label").textContent = timelineItem?.label || "Peers discovered";
  byId("goal-description").textContent = state.scenario.goal.description;
  byId("goal-progress-bar").style.width = `${clamp(state.elapsed / state.scenario.duration_ms) * 100}%`;
  byId("goal-status").textContent = phase.name === "complete" ? "Succeeded" : state.playing ? "Active" : "Ready";
  byId("elapsed").textContent = `00:${String(Math.floor(state.elapsed / 1000)).padStart(2, "0")}`;
  byId("play-icon").textContent = state.playing ? "Ⅱ" : "▶";
  byId("play-label").textContent = state.playing ? "Pause" : state.elapsed >= state.scenario.duration_ms ? "Replay" : "Run";

  byId("robot-list").replaceChildren(...state.scenario.robots.map((robot) => {
    const row = document.createElement("div");
    const active = robot.robot_class === activeRobotClass(phase.name);
    row.className = `robot-row ${active ? "active" : ""}`;
    const symbol = document.createElement("span");
    symbol.className = `robot-symbol ${robot.robot_class}`;
    symbol.textContent = { humanoid: "H", quadruped: "Q", mobile_arm: "A" }[robot.robot_class];
    const info = document.createElement("div"); info.className = "robot-info";
    const name = document.createElement("strong"); name.textContent = robot.robot_id;
    const meta = document.createElement("span"); meta.textContent = `${robot.manufacturer} · ${robot.model}`;
    info.append(name, meta);
    const status = document.createElement("span"); status.className = "robot-state"; status.textContent = activityFor(robot.robot_class, phase.name);
    row.append(symbol, info, status);
    return row;
  }));

  const stepPhases = ["inspect", "carry", "place"];
  [...byId("plan-steps").children].forEach((element, index) => {
    const stepPhase = phases.find((item) => item.name === stepPhases[index]);
    element.className = `plan-step ${state.elapsed >= stepPhase.end ? "complete" : state.elapsed >= stepPhase.start ? "active" : ""}`;
  });

  const visibleEvents = state.scenario.events.slice(0, Math.floor(clamp(state.elapsed / 10500) * state.scenario.events.length));
  const recent = visibleEvents.slice(-4).reverse();
  byId("events").replaceChildren(...recent.map((event) => {
    const row = document.createElement("div"); row.className = "event";
    const type = document.createElement("b"); type.textContent = event.type;
    const summary = document.createElement("span"); summary.textContent = event.summary;
    row.append(type, summary); return row;
  }));
  byId("event-summary").textContent = recent[0]?.summary || "Waiting for run";
  byId("message-count").textContent = visibleEvents.length;
  const pulseTrack = byId("message-pulses");
  pulseTrack.replaceChildren(...recent.map((event) => {
    const pulse = document.createElement("span"); pulse.className = "pulse";
    pulse.style.left = `${clamp((event.index + 1) / state.scenario.events.length) * 100}%`;
    return pulse;
  }));
}

function frame(timestamp) {
  if (!state.lastFrame) state.lastFrame = timestamp;
  const delta = Math.min(100, timestamp - state.lastFrame);
  state.lastFrame = timestamp;
  if (state.playing && state.scenario) {
    state.elapsed = Math.min(state.scenario.duration_ms, state.elapsed + delta * state.speed);
    if (state.elapsed >= state.scenario.duration_ms) state.playing = false;
  }
  renderCanvas();
  renderInterface();
  requestAnimationFrame(frame);
}

function renderPlan() {
  byId("plan-steps").replaceChildren(...state.scenario.plan.steps.map((step, index) => {
    const item = document.createElement("article"); item.className = "plan-step";
    const number = document.createElement("span"); number.className = "step-number"; number.textContent = `0${index + 1}`;
    const title = document.createElement("strong"); title.textContent = step.description;
    const meta = document.createElement("p"); meta.textContent = `${step.robot_id} · ${step.capability}`;
    const lineElement = document.createElement("span"); lineElement.className = "step-line";
    item.append(number, title, meta, lineElement); return item;
  }));
}

async function loadScenario() {
  try {
    const response = await fetch("/api/scenario", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.scenario = await response.json();
    byId("connection-dot").className = "status-dot online";
    byId("connection-label").textContent = "Scenario ready";
    byId("robot-count").textContent = state.scenario.robots.length;
    renderPlan();
    renderInterface();
  } catch (error) {
    byId("connection-label").textContent = "Scenario unavailable";
  }
}

byId("play-button").addEventListener("click", () => {
  if (!state.scenario) return;
  if (state.elapsed >= state.scenario.duration_ms) state.elapsed = 0;
  state.playing = !state.playing;
});
byId("reset-button").addEventListener("click", () => { state.elapsed = 0; state.playing = false; });
byId("speed-select").addEventListener("change", (event) => { state.speed = Number(event.target.value); });
window.addEventListener("resize", resizeCanvas);

resizeCanvas();
loadScenario();
requestAnimationFrame(frame);
