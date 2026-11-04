import "./style.css";
import * as api from "./api";
import { BrowserEngine, type ForecastResult } from "./engine";
import { EditableLayout } from "./layout";
import { CATEGORY_ICON, ParkRenderer, fmtClock } from "./renderer";
import type {
  AttractionFrame,
  Category,
  DayReport,
  Frame,
  LogEntry,
  SimSettings,
  Template,
  Tile,
  Validation,
} from "./types";

type ToolKind = "inspect" | "path" | "erase" | "gate" | "place";
interface Tool {
  kind: ToolKind;
  template?: string;
}

const TOOLS: { kind: ToolKind; icon: string; label: string; hint: string }[] = [
  { kind: "inspect", icon: "👆", label: "Select", hint: "Click a building to see its details or rename it." },
  { kind: "path", icon: "🟫", label: "Path", hint: "Click and drag to lay paths. Guests can only walk on paths." },
  { kind: "erase", icon: "🧽", label: "Erase", hint: "Drag over paths to remove them, or click a building to demolish it." },
  { kind: "gate", icon: "🚪", label: "Gate", hint: "Click a path tile to move the park's main entrance." },
];

const GROUPS: [string, Category[]][] = [
  ["Rides", ["ride"]],
  ["Shows", ["show"]],
  ["Food & drink", ["food"]],
  ["Guest services", ["restroom", "shop", "first_aid"]],
];

const WEATHER_TEXT: Record<Frame["weather"], string> = {
  clear: "☀️ Sunny",
  cloudy: "⛅ Cloudy",
  rain: "🌧️ Rain",
  thunderstorm: "⛈️ Thunderstorm, lightning hold",
};

const THOUGHT_TEXT: Record<string, string> = {
  lines_too_long: "The lines are too long",
  waited_too_long: "I waited way longer than I wanted to",
  arrived_to_closed: "I walked all the way there and it was closed",
  ride_closed_while_waiting: "The ride closed while I was in line",
  no_restroom: "I can't find a restroom",
  no_drinks: "I'm thirsty and there's nothing to drink nearby",
  no_food: "I'm hungry and there's no food nearby",
  show_full: "The show was full",
  show_cancelled: "The show I was waiting for was cancelled",
  felt_sick: "That ride made me feel sick",
  great_ride: "That ride was amazing!",
  great_show: "What a great show!",
  too_expensive: "I can't afford anything here",
  no_first_aid: "Nobody could help me when I got hurt",
  leaving_unhappy: "I'm not having fun, I'm going home",
  weather: "This storm is ruining my day",
};

const $ = <T extends HTMLElement = HTMLElement>(selector: string) =>
  document.querySelector(selector) as T;
const esc = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
  );
const num = (n: number) => n.toLocaleString("en-US");

// ---- state ---------------------------------------------------------------------------
const renderer = new ParkRenderer();
const catalog = new Map<string, Template>();
let layout = EditableLayout.blank();
let parkId: string | null = null;
let token: string | null = null;
let mode: "design" | "operate" = "design";
let tool: Tool = { kind: "path" };
let selected: string | null = null;
let broken = new Set<string>();
let dirty = false;
let validation: Validation | null = null;
let validateTimer = 0;
let redrawQueued = false;
let hover: Tile | null = null;

// The Python engine runs in two places: on the server (WebSocket) or in this browser
// (Pyodide in a Web Worker). Browser is the default, so viewers cost no server CPU.
const engine = new BrowserEngine();
let runOn: "browser" | "server" = "browser";
let layoutVersion = 0;
interface ForecastState {
  state: "idle" | "running" | "done" | "error";
  progress: number;
  clock: string;
  version: number;
  attendance: number;
  result?: ForecastResult;
  error?: string;
}
let forecast: ForecastState = { state: "idle", progress: 0, clock: "", version: -1, attendance: 0 };

let live: api.LiveRun | null = null;
let lastFrame: Frame | null = null;
let lastFrameAt = 0;
let logEntries: LogEntry[] = [];
let speeds: number[] = [1, 10, 60, 300, 1200];
let liveSeed: number | null = null;
let liveWhere: "browser" | "server" = "browser";
let lastReport: DayReport | null = null;
const settings: SimSettings = {
  attendance: 8000,
  storm_chance: 0.35,
  high_temp_f: 91,
  breakdowns: true,
  incidents: true,
};

// ---- boot & routing -------------------------------------------------------------------
async function boot() {
  await renderer.init($("#stage"));
  try {
    for (const t of await api.getCatalog()) catalog.set(t.key, t);
  } catch (error) {
    $("#panel").innerHTML = `<div class="card"><p class="bad">Can't reach the ParkSim server (${esc(
      (error as Error).message,
    )}). Is <code>parksim-server</code> running?</p></div>`;
    return;
  }
  renderPalette();
  bindTopbar();
  bindStage();
  bindPanel();
  bindKeys();
  await route();
  window.addEventListener("hashchange", route);
}

async function route() {
  stopLive();
  const match = location.hash.match(/^#\/park\/([\w-]+)/);
  if (match) {
    try {
      const record = await api.getPark(match[1]);
      parkId = record.id;
      token = api.loadToken(record.id);
      setLayout(EditableLayout.from(record.layout, catalog));
      if (!token) toast("Viewing a shared park. Edit freely, then “Save a copy” to keep your version.");
      return;
    } catch (error) {
      toast(`Couldn't load that park: ${(error as Error).message}`);
    }
  }
  if (location.hash === "#/new") newPark();
  else await loadSample();
}

function goTo(hash: string) {
  history.replaceState(null, "", hash || location.pathname);
}

function newPark() {
  parkId = token = null;
  setLayout(EditableLayout.blank());
  tool = { kind: "path" };
  renderPalette();
}

async function loadSample() {
  parkId = token = null;
  setLayout(EditableLayout.from(await api.getSample(), catalog));
}

function setLayout(next: EditableLayout) {
  layout = next;
  selected = null;
  broken = layout.repairEntrances(catalog);
  dirty = false;
  validation = null;
  layoutVersion++;
  engine.cancelForecast();
  forecast = { state: "idle", progress: 0, clock: "", version: -1, attendance: 0 };
  renderer.drawLayout(layout, catalog, { selected, broken });
  renderer.fit();
  settings.attendance = suggestedAttendance();
  updateTopbar();
  renderPanel();
  scheduleValidate();
}

// ---- editing ------------------------------------------------------------------------------
function redraw() {
  if (redrawQueued) return;
  redrawQueued = true;
  requestAnimationFrame(() => {
    redrawQueued = false;
    renderer.drawLayout(layout, catalog, { selected, broken });
    drawOverlay();
  });
}

function markDirty() {
  dirty = true;
  layoutVersion++;
  if (forecast.state === "running") {
    engine.cancelForecast();
    forecast.state = "idle";
  }
  updateTopbar();
  scheduleValidate();
}

function scheduleValidate() {
  window.clearTimeout(validateTimer);
  validateTimer = window.setTimeout(async () => {
    try {
      validation = await api.validate(layout.toLayout());
    } catch (error) {
      validation = { ok: false, error: (error as Error).message, warnings: [] };
    }
    if (mode === "design") renderPanel();
    else updateStartButton();
  }, 300);
}

function templateFor(t: Tool) {
  return t.kind === "place" && t.template ? catalog.get(t.template) : undefined;
}

function anchorFor(template: Template, tile: Tile): Tile {
  return [tile[0] - Math.floor(template.size[0] / 2), tile[1] - Math.floor(template.size[1] / 2)];
}

function drawOverlay() {
  const template = templateFor(tool);
  if (mode === "operate" || !hover) return renderer.drawOverlay(null, null, "inspect");
  if (template) {
    const [x, y] = anchorFor(template, hover);
    const check = layout.checkPlacement(template, x, y);
    return renderer.drawOverlay(hover, check, "place");
  }
  renderer.drawOverlay(hover, null, tool.kind);
}

function paint(tile: Tile, on: boolean) {
  if (layout.setPath(tile[0], tile[1], on)) redraw();
}

/** 4-connected steps between two tiles, so fast drags still make walkable paths. */
function* steps(from: Tile, to: Tile): Generator<Tile> {
  let [x, y] = from;
  while (x !== to[0] || y !== to[1]) {
    if (Math.abs(to[0] - x) >= Math.abs(to[1] - y)) x += Math.sign(to[0] - x);
    else y += Math.sign(to[1] - y);
    yield [x, y];
  }
}

function afterPathEdit() {
  broken = layout.repairEntrances(catalog);
  redraw();
  markDirty();
}

function placeAt(tile: Tile) {
  const template = templateFor(tool);
  if (!template) return;
  const [x, y] = anchorFor(template, tile);
  const check = layout.checkPlacement(template, x, y);
  if (!check.ok) return toast(check.reason ?? "Can't build there");
  const spec = layout.place(template, x, y, catalog);
  if (!spec) return;
  selected = spec.id;
  redraw();
  markDirty();
  renderPanel();
}

function removeBuilding(id: string) {
  layout.remove(id, catalog);
  broken.delete(id);
  if (selected === id) selected = null;
  redraw();
  markDirty();
  renderPanel();
}

function select(id: string | null) {
  selected = id;
  redraw();
  if (mode === "design") renderPanel();
  else if (lastFrame) updateLive(lastFrame);
}

// ---- stage input ------------------------------------------------------------------------
let spaceHeld = false;

function bindStage() {
  const canvas = renderer.app.canvas;
  let painting: "path" | "erase" | null = null;
  let lastTile: Tile | null = null;
  let panFrom: { x: number; y: number } | null = null;

  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    if (e.button === 1 || e.button === 2 || spaceHeld) {
      panFrom = { x: e.clientX, y: e.clientY };
      return;
    }
    if (e.button !== 0) return;
    const tile = renderer.toTile(e.clientX, e.clientY);
    if (mode === "operate") return select(layout.ownerAt(...tile));
    switch (tool.kind) {
      case "inspect":
        return select(layout.ownerAt(...tile));
      case "path":
        painting = "path";
        lastTile = tile;
        return paint(tile, true);
      case "erase": {
        const owner = layout.ownerAt(...tile);
        if (owner) return removeBuilding(owner);
        painting = "erase";
        lastTile = tile;
        return paint(tile, false);
      }
      case "gate":
        if (layout.setGate(...tile)) {
          redraw();
          markDirty();
        } else toast("The gate has to be on a path tile.");
        return;
      case "place":
        return placeAt(tile);
    }
  });
  canvas.addEventListener("pointermove", (e) => {
    if (panFrom) {
      renderer.panBy(e.clientX - panFrom.x, e.clientY - panFrom.y);
      panFrom = { x: e.clientX, y: e.clientY };
      return;
    }
    const tile = renderer.toTile(e.clientX, e.clientY);
    if (painting && lastTile) {
      for (const step of steps(lastTile, tile)) paint(step, painting === "path");
      lastTile = tile;
    }
    if (!hover || hover[0] !== tile[0] || hover[1] !== tile[1]) {
      hover = tile;
      drawOverlay();
    }
  });
  const finish = () => {
    if (painting) afterPathEdit();
    painting = null;
    lastTile = null;
    panFrom = null;
  };
  canvas.addEventListener("pointerup", finish);
  canvas.addEventListener("pointercancel", finish);
  canvas.addEventListener("pointerleave", () => {
    hover = null;
    drawOverlay();
  });
  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      renderer.zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.15 : 1 / 1.15);
    },
    { passive: false },
  );
  $("#btn-fit").addEventListener("click", () => renderer.fit());
}

function bindKeys() {
  window.addEventListener("keydown", (e) => {
    const typing = (e.target as HTMLElement).closest("input, select, textarea");
    if (e.code === "Space" && !typing) {
      spaceHeld = true;
      e.preventDefault();
    }
    if (typing || mode !== "design") return;
    if (e.key === "Escape") {
      setTool({ kind: "inspect" });
      select(null);
    } else if ((e.key === "Delete" || e.key === "Backspace") && selected) {
      removeBuilding(selected);
    }
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space") spaceHeld = false;
  });
}

// ---- top bar ------------------------------------------------------------------------------
function bindTopbar() {
  const name = $<HTMLInputElement>("#park-name");
  name.addEventListener("input", () => {
    layout.name = name.value;
    markDirty();
  });
  document.querySelectorAll<HTMLButtonElement>(".seg button").forEach((b) =>
    b.addEventListener("click", () => setMode(b.dataset.mode as "design" | "operate")),
  );
  $("#btn-new").addEventListener("click", () => {
    if (confirmDiscard()) {
      goTo("#/new");
      stopLive();
      newPark();
    }
  });
  $("#btn-sample").addEventListener("click", async () => {
    if (confirmDiscard()) {
      goTo("");
      stopLive();
      await loadSample();
    }
  });
  $("#btn-save").addEventListener("click", save);
  $("#btn-share").addEventListener("click", share);
}

function confirmDiscard() {
  return !dirty || confirm("Discard your unsaved changes?");
}

function updateTopbar() {
  const name = $<HTMLInputElement>("#park-name");
  if (document.activeElement !== name) name.value = layout.name;
  const state = $("#save-state");
  state.textContent = dirty
    ? "● Unsaved changes"
    : parkId
      ? token
        ? "Saved"
        : "Shared park (view copy)"
      : "Not saved yet";
  state.classList.toggle("dirty", dirty);
  $("#btn-save").textContent = parkId && !token ? "Save a copy" : "Save";
  $<HTMLButtonElement>("#btn-share").disabled = !parkId;
  document.title = `${layout.name || "Untitled"} · ParkSim`;
}

async function save() {
  const data = layout.toLayout();
  try {
    if (parkId && token) {
      await api.updatePark(parkId, token, data);
    } else {
      const created = await api.createPark(data);
      parkId = created.id;
      token = created.edit_token;
      api.saveToken(parkId, token);
      goTo(`#/park/${parkId}`);
    }
    dirty = false;
    updateTopbar();
    toast("Park saved. Use Share to send someone a link.");
  } catch (error) {
    toast(`Couldn't save: ${(error as Error).message}`);
  }
}

async function share() {
  if (!parkId) return;
  const url = `${location.origin}${location.pathname}#/park/${parkId}`;
  try {
    await navigator.clipboard.writeText(url);
    toast(dirty ? "Link copied. Save to include your latest changes." : "Share link copied!");
  } catch {
    prompt("Copy this link to share your park:", url);
  }
}

function setMode(next: "design" | "operate") {
  if (next === mode) return;
  mode = next;
  document.body.dataset.mode = mode;
  document
    .querySelectorAll<HTMLButtonElement>(".seg button")
    .forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  if (mode === "design") stopLive();
  else {
    if (forecast.state === "running") {
      engine.cancelForecast();
      forecast.state = "idle";
    }
    if (runOn === "browser") engine.warmup();
  }
  hover = null;
  drawOverlay();
  renderPanel();
  // The canvas column changes width; let the layout settle before re-fitting.
  requestAnimationFrame(() => renderer.fit());
}

// ---- palette ------------------------------------------------------------------------------
function renderPalette() {
  const tools = TOOLS.map(
    (t) =>
      `<button class="tool" data-tool="${t.kind}" title="${esc(t.hint)}"><span class="ico">${t.icon}</span><span>${t.label}</span></button>`,
  ).join("");
  const groups = GROUPS.map(([title, categories]) => {
    const items = [...catalog.values()]
      .filter((t) => categories.includes(t.category))
      .map(
        (t) =>
          `<button class="tool" data-tool="place" data-template="${t.key}" title="${esc(t.description)}">
            <span class="ico">${CATEGORY_ICON[t.category]}</span><span>${esc(t.label)}</span><small>${t.size[0]}×${t.size[1]}</small>
          </button>`,
      )
      .join("");
    return `<h4>${title}</h4>${items}`;
  }).join("");
  const palette = $("#palette");
  palette.innerHTML = `<h4>Tools</h4>${tools}${groups}`;
  palette.onclick = (e) => {
    const button = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-tool]");
    if (button)
      setTool({ kind: button.dataset.tool as ToolKind, template: button.dataset.template });
  };
  highlightTool();
}

function setTool(next: Tool) {
  tool = next;
  highlightTool();
  drawOverlay();
  renderPanel();
}

function highlightTool() {
  document.querySelectorAll<HTMLButtonElement>("#palette .tool").forEach((b) => {
    b.classList.toggle(
      "active",
      b.dataset.tool === tool.kind && (tool.kind !== "place" || b.dataset.template === tool.template),
    );
  });
}

// ---- side panel ---------------------------------------------------------------------------
function card(title: string, body: string, id = "") {
  return `<section class="card"${id ? ` id="${id}"` : ""}><h3>${title}</h3>${body}</section>`;
}

function statsRows(t: Template) {
  const s = t.stats as Record<string, number & string[] & boolean>;
  const rows: [string, string][] = [];
  if (t.category === "ride") {
    rows.push(
      ["Capacity", `${num(Math.round((s.capacity * 3600) / s.cycle_seconds))} riders/hr`],
      ["Ride length", `${Math.round(s.ride_seconds / 60)} min`],
      ["Intensity", `${s.intensity}/10`],
      ["Setting", s.indoor ? "Indoor" : "Outdoor (lightning holds)"],
      ["Reliability", `~1 breakdown / ${s.mtbf_hours} operating hrs`],
    );
  } else if (t.category === "show") {
    rows.push(
      ["Seats", num(s.capacity)],
      ["Length", `${s.duration_minutes} min`],
      ["Showtimes", (s.showtimes as string[]).join(", ")],
      ["Setting", s.indoor ? "Indoor" : "Outdoor (cancelled for lightning)"],
    );
  } else {
    rows.push(["Servers", String(s.servers)], ["Service time", `${s.service_seconds} s`]);
    if (s.spend) rows.push(["Average spend", `$${s.spend}`]);
  }
  rows.push(["Build cost", `$${num(t.build_cost)}`]);
  return `<table class="stats-table">${rows
    .map(([k, v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`)
    .join("")}</table>`;
}

function rideCapacityPerHour() {
  let total = 0;
  for (const b of layout.buildings) {
    const t = catalog.get(b.template);
    if (t?.category === "ride") {
      const s = t.stats as Record<string, number>;
      total += (s.capacity * 3600) / s.cycle_seconds;
    }
  }
  return total;
}

/** Rough comfortable attendance: ride capacity over ~10 hours, ~1.4 rides/guest/hour. */
function suggestedAttendance() {
  const value = Math.round((rideCapacityPerHour() * 10) / 14 / 500) * 500;
  return Math.min(Math.max(value, 500), 20000);
}

function renderPanel() {
  if (mode === "operate") return renderOperatePanel();
  const parts: string[] = [];
  const template = templateFor(tool);
  if (template) {
    parts.push(
      card(
        `${CATEGORY_ICON[template.category]} ${esc(template.label)}`,
        `<p>${esc(template.description)}</p>${statsRows(template)}
         <p class="muted">Click the map to build. It needs a path touching one side; the green dot shows where its entrance will go.</p>`,
      ),
    );
  } else {
    const info = TOOLS.find((t) => t.kind === tool.kind)!;
    parts.push(card(`${info.icon} ${info.label}`, `<p>${info.hint}</p>`));
  }

  const spec = selected ? layout.building(selected) : undefined;
  const selTemplate = spec && catalog.get(spec.template);
  if (spec && selTemplate) {
    const [w, h] = layout.sizeOf(spec, catalog);
    parts.push(
      card(
        "Selected",
        `<label class="field">Name<input id="sel-name" maxlength="60" value="${esc(
          spec.name ?? selTemplate.label,
        )}"></label>
         <p class="muted">${CATEGORY_ICON[selTemplate.category]} ${esc(selTemplate.label)} · ${w}×${h} tiles</p>
         ${broken.has(spec.id) ? `<p class="bad">⚠ Its entrance has no path. Connect a path to one side.</p>` : ""}
         ${statsRows(selTemplate)}
         <button class="danger" data-action="delete">🗑 Demolish</button>`,
      ),
    );
  }

  let check = `<p class="muted">Checking…</p>`;
  if (validation && !validation.ok) check = `<p class="bad">✖ ${esc(validation.error)}</p>`;
  else if (validation && validation.warnings.length)
    check = `<p>Opens, but:</p><ul class="warn-list">${validation.warnings
      .map((w) => `<li>${esc(w)}</li>`)
      .join("")}</ul>`;
  else if (validation) check = `<p class="good">✓ Ready to open. Switch to Operate to run a day.</p>`;
  parts.push(card("Park check", check));
  parts.push(forecastCard());

  const count = (c: Category) =>
    layout.buildings.filter((b) => catalog.get(b.template)?.category === c).length;
  const cost = layout.buildings.reduce((s, b) => s + (catalog.get(b.template)?.build_cost ?? 0), 0);
  parts.push(
    card(
      "At a glance",
      `<div class="glance">
        <div><b>${count("ride")}</b><span>rides</span></div>
        <div><b>${num(Math.round(rideCapacityPerHour()))}</b><span>riders / hour</span></div>
        <div><b>${count("show")}</b><span>shows</span></div>
        <div><b>${count("food") + count("restroom") + count("shop") + count("first_aid")}</b><span>services</span></div>
        <div><b>$${num(cost)}</b><span>build cost</span></div>
        <div><b>${num(suggestedAttendance())}</b><span>suggested guests/day</span></div>
      </div>`,
    ),
  );
  $("#panel").innerHTML = parts.join("");
  $<HTMLInputElement>("#sel-name")?.addEventListener("change", (e) => {
    const value = (e.target as HTMLInputElement).value.trim();
    if (spec && value) {
      spec.name = value;
      redraw();
      markDirty();
    }
  });
}

// ---- forecast (Python in the browser) ----------------------------------------------------
// Thought counts are events (one guest can think it many times), so each hint has its own
// threshold as a share of attendance.
const FORECAST_HINTS: Record<string, [number, string]> = {
  no_restroom: [0.15, "Guests couldn't find a restroom. Add restrooms closer to the busy areas."],
  no_food: [0.15, "Hungry guests couldn't find food nearby."],
  no_drinks: [0.15, "Thirsty guests couldn't find drinks. Try a drink stand on a main path."],
  lines_too_long: [3.0,"Many guests found every line too long. The park needs more ride capacity."],
  too_expensive: [1.0, "Lots of guests ran out of money. Prices may be too high for their budget."],
};

function engineStatusHtml() {
  const tone = engine.status.stage === "error" ? "bad" : "muted";
  return `<p class="${tone} engine-status">🐍 ${esc(engine.status.message)}</p>`;
}

function forecastCard() {
  const f = forecast;
  const ready = validation?.ok ?? false;
  let body: string;
  if (f.state === "running") {
    const loading = engine.status.stage === "loading";
    body = `<div class="progress"><span style="width:${Math.round(f.progress * 100)}%"></span></div>
      <p class="muted">${loading ? esc(engine.status.message) : `Simulating ${esc(f.clock)}…`}</p>
      <button data-action="cancel-forecast">Cancel</button>`;
  } else if (f.state === "done" && f.result) {
    const { report, seconds } = f.result;
    const s = report.summary;
    const longest = [...report.rides].sort((a, b) => b.avg_wait_min - a.avg_wait_min).slice(0, 3);
    const hints: string[] = report.rides
      .filter((r) => r.peak_posted_min >= 60)
      .map((r) => `${r.name} peaks at ${r.peak_posted_min} min. It needs more capacity nearby.`);
    for (const [key, [share, text]] of Object.entries(FORECAST_HINTS))
      if ((report.thoughts[key] ?? 0) > s.attendance * share) hints.push(text);
    if (s.avg_exit_happiness < 70)
      hints.push("Guests leave unhappy on average. Try fewer guests or more capacity.");
    body = `
      ${f.version !== layoutVersion ? `<p class="warn">Layout changed since this forecast.</p>` : ""}
      <div class="glance">
        <div><b>${s.avg_exit_happiness}</b><span>happiness at exit</span></div>
        <div><b>${s.rides_per_guest}</b><span>rides per guest</span></div>
      </div>
      <table class="stats-table">${longest
        .map(
          (r) =>
            `<tr><td>${esc(r.name)}</td><td>avg ${r.avg_wait_min} min · peak ${r.peak_posted_min}</td></tr>`,
        )
        .join("")}</table>
      ${
        hints.length
          ? `<ul class="warn-list">${hints.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>`
          : `<p class="good">✓ No bottlenecks at ${num(f.attendance)} guests.</p>`
      }
      <p class="muted">Simulated a full day for ${num(f.attendance)} guests in ${seconds.toFixed(1)} s, right here in your browser.</p>
      <button class="wide" data-action="forecast" ${ready ? "" : "disabled"}>🔮 Run again</button>`;
  } else {
    body = `<p class="muted">Simulate a whole day with this layout using the real Python engine,
      running in your browser through Pyodide. Same seed, clear skies and no breakdowns
      every time, so you can compare layout changes fairly.</p>
      ${f.state === "error" ? `<p class="bad">${esc(f.error)}</p>` : ""}
      <button class="wide" data-action="forecast" ${ready ? "" : "disabled"}>🔮 Forecast a day (${num(
        suggestedAttendance(),
      )} guests)</button>`;
  }
  return `<section class="card" id="forecast-card"><h3>Forecast</h3>${body}${engineStatusHtml()}</section>`;
}

function updateForecastCard() {
  const el = document.querySelector("#forecast-card");
  if (el) el.outerHTML = forecastCard();
}

async function runForecast() {
  if (!validation?.ok) return toast("Fix the park check first.");
  const attendance = suggestedAttendance();
  forecast = { state: "running", progress: 0, clock: "09:00", version: layoutVersion, attendance };
  updateForecastCard();
  try {
    const result = await engine.forecast(
      layout.toLayout(),
      { seed: 1, attendance, storm_chance: 0, high_temp_f: 91, breakdowns: false, incidents: false },
      (progress, clock) => {
        forecast.progress = progress;
        forecast.clock = clock;
        updateForecastCard();
      },
    );
    forecast = { ...forecast, state: "done", result };
  } catch (error) {
    if ((error as Error).message === "cancelled") return;
    forecast = { ...forecast, state: "error", error: (error as Error).message };
  }
  updateForecastCard();
}

engine.onStatus = () => {
  document.querySelectorAll(".engine-status").forEach((el) => {
    el.outerHTML = engineStatusHtml();
  });
  if (forecast.state === "running") updateForecastCard();
};

// Panel buttons are re-rendered while a simulation streams, so actions fire on
// pointerdown (a click could be lost if the button is replaced mid-click).
// Keyboard activation arrives as a click with detail === 0.
function bindPanel() {
  const panel = $("#panel");
  const run = (e: Event) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-action]");
    if (!el || (el as HTMLButtonElement).disabled) return;
    handleAction(el.dataset.action!, el.dataset);
  };
  panel.addEventListener("pointerdown", (e) => {
    if (e.button === 0) run(e);
  });
  panel.addEventListener("click", (e) => {
    if (e.detail === 0) run(e);
  });
  panel.addEventListener("input", (e) => {
    const el = e.target as HTMLInputElement;
    if (el.id === "set-attendance") {
      settings.attendance = Number(el.value);
      $("#att-out").textContent = num(settings.attendance);
    }
  });
  panel.addEventListener("change", (e) => {
    const el = e.target as HTMLInputElement;
    if (el.name === "run-on") {
      runOn = el.value as typeof runOn;
      if (runOn === "browser") engine.warmup();
    }
  });
}

function handleAction(action: string, data: DOMStringMap) {
  switch (action) {
    case "delete":
      if (selected) removeBuilding(selected);
      break;
    case "start":
      startDay();
      break;
    case "report":
      if (lastReport) showReport(lastReport);
      break;
    case "forecast":
      runForecast();
      break;
    case "cancel-forecast":
      engine.cancelForecast();
      forecast.state = "idle";
      updateForecastCard();
      break;
    case "speed":
      live?.send({ type: "speed", value: Number(data.value) });
      break;
    case "pause":
      live?.send({ type: lastFrame?.paused ? "resume" : "pause" });
      break;
    case "storm":
      live?.send({ type: "storm", minutes: 45 });
      toast("⛈ Storm cell moving in: outdoor attractions go on lightning hold.");
      break;
    case "stop":
      stopLive();
      renderPanel();
      break;
    case "select":
      select(data.id ?? null);
      break;
    case "close":
    case "reopen":
      live?.send({ type: action, id: data.id });
      break;
    case "breakdown":
      live?.send({ type: "breakdown", id: data.id, minutes: 30 });
      break;
  }
}

// ---- operate mode -------------------------------------------------------------------------
function renderOperatePanel() {
  const panel = $("#panel");
  if (!live) {
    $("#legend").hidden = true;
    panel.innerHTML = `
      ${card(
        "Run a park day",
        `<p class="muted">Opens at 09:00 and closes at 21:00. Guests arrive throughout the morning; you can step in as operations at any time.</p>
        <label class="field">Expected guests <output id="att-out">${num(settings.attendance)}</output>
          <input type="range" id="set-attendance" min="500" max="20000" step="500" value="${settings.attendance}"></label>
        <label class="field">Weather
          <select id="set-storm">
            <option value="0">Clear skies all day</option>
            <option value="0.35" selected>Florida summer: 35% chance of storms</option>
            <option value="1">Afternoon thunderstorm guaranteed</option>
          </select></label>
        <label class="field">Daily high
          <select id="set-temp">
            <option value="78">78°F</option><option value="85">85°F</option>
            <option value="91" selected>91°F</option><option value="97">97°F, heat advisory</option>
          </select></label>
        <label class="check"><input type="checkbox" id="set-breakdowns" checked> Random ride breakdowns</label>
        <label class="check"><input type="checkbox" id="set-incidents" checked> Guest incidents and injuries</label>
        <label class="field">Seed (same seed = same day)
          <input type="number" id="set-seed" min="0" placeholder="random"></label>
        <fieldset class="engine-choice">
          <legend>Where the Python engine runs</legend>
          <label class="check"><input type="radio" name="run-on" value="browser" ${
            runOn === "browser" ? "checked" : ""
          }> In this browser (Pyodide)</label>
          <label class="check"><input type="radio" name="run-on" value="server" ${
            runOn === "server" ? "checked" : ""
          }> On the ParkSim server</label>
          ${engineStatusHtml()}
        </fieldset>
        <button class="primary wide" data-action="start" id="btn-start">▶ Open the gates</button>
        <p class="bad" id="start-error"></p>`,
      )}
      ${lastReport ? card("Last day", `<button class="wide" data-action="report">📊 View report</button>`) : ""}`;
    $<HTMLSelectElement>("#set-storm").value = String(settings.storm_chance);
    $<HTMLSelectElement>("#set-temp").value = String(settings.high_temp_f);
    $<HTMLInputElement>("#set-breakdowns").checked = settings.breakdowns;
    $<HTMLInputElement>("#set-incidents").checked = settings.incidents;
    updateStartButton();
    return;
  }
  $("#legend").hidden = false;
  panel.innerHTML = `
    <section class="card clock-card">
      <div class="clock" id="lv-clock">09:00</div>
      <div class="weather" id="lv-weather">Opening…</div>
      <div class="stats">
        <div><b id="lv-guests">0</b><span>in park</span></div>
        <div><b id="lv-mood">–</b><span>avg happiness</span></div>
        <div><b id="lv-arrivals">0</b><span>arrivals</span></div>
      </div>
    </section>
    <section class="card">
      <div class="speed" id="lv-speed"></div>
      <div class="row">
        <button data-action="storm">⛈ Trigger storm</button>
        <button data-action="stop">■ End day</button>
      </div>
      <p class="muted" id="lv-seed"></p>
    </section>
    <section class="card" id="lv-selected" hidden></section>
    <section class="card"><h3>Wait times</h3><ol class="waits" id="lv-waits"></ol></section>
    <section class="card"><h3>Operations log</h3><ol class="log" id="lv-log"></ol></section>`;
  if (lastFrame) updateLive(lastFrame);
}

function updateStartButton() {
  const button = document.querySelector<HTMLButtonElement>("#btn-start");
  if (!button) return;
  const error = $("#start-error");
  if (!validation) {
    button.disabled = true;
    error.textContent = "";
  } else if (!validation.ok) {
    button.disabled = true;
    error.textContent = `Fix the layout first: ${validation.error}`;
  } else {
    button.disabled = false;
    error.textContent = "";
  }
}

function readSettings() {
  settings.storm_chance = Number($<HTMLSelectElement>("#set-storm").value);
  settings.high_temp_f = Number($<HTMLSelectElement>("#set-temp").value);
  settings.breakdowns = $<HTMLInputElement>("#set-breakdowns").checked;
  settings.incidents = $<HTMLInputElement>("#set-incidents").checked;
  const seed = $<HTMLInputElement>("#set-seed").value;
  return { ...settings, ...(seed ? { seed: Number(seed) } : {}) };
}

function startDay() {
  const chosen = readSettings();
  stopLive();
  logEntries = [];
  lastFrame = null;
  lastFrameAt = performance.now();
  liveWhere = runOn;
  const handlers: api.LiveHandlers = {
    onInit: (m) => {
      speeds = m.speeds;
      liveSeed = m.seed;
    },
    onFrame: (f) => {
      const now = performance.now();
      const interval = Math.min(Math.max(now - lastFrameAt, 200), 1000);
      lastFrameAt = now;
      lastFrame = f;
      renderer.setGuests(f.guests, interval);
      renderer.showStatuses(f.attractions, layout, catalog, f.time);
      for (const message of f.errors) toast(message);
      if (f.log.length) logEntries = [...f.log.reverse(), ...logEntries].slice(0, 80);
      updateLive(f);
    },
    onReport: (report) => {
      lastReport = report;
      showReport(report);
    },
    onError: (message) => {
      toast(message);
      if (!lastFrame) {
        stopLive();
        renderPanel();
      }
    },
    onClose: () => {
      if (live && !lastReport) toast("The simulation connection closed.");
    },
  };
  live =
    runOn === "browser"
      ? engine.startLive(layout.toLayout(), chosen, handlers)
      : new api.LiveConnection(layout.toLayout(), chosen, handlers);
  lastReport = null;
  renderPanel();
}

function stopLive() {
  live?.close();
  live = null;
  lastFrame = null;
  renderer.clearLive();
}

function waitBadge(f: AttractionFrame, template: Template, clock: number) {
  if (f.status !== "open") {
    const label: Record<string, string> = {
      weather: "⚡ Hold",
      breakdown: "Down",
      inspection: "Inspect",
      manual: "Closed",
      closed: "Closed",
    };
    return `<span class="wait w-closed">${label[f.status] ?? f.status}</span>`;
  }
  if (template.category === "show")
    return f.next_show != null && f.next_show >= clock
      ? `<span class="wait w-show">${fmtClock(f.next_show)}</span>`
      : `<span class="wait w-closed">Done</span>`;
  const wait = f.posted ?? 5;
  const tone = wait <= 20 ? "w-green" : wait <= 45 ? "w-amber" : "w-red";
  return `<span class="wait ${tone}">${wait} min</span>`;
}

function updateLive(f: Frame) {
  const clockEl = document.querySelector("#lv-clock");
  if (!clockEl) return;
  clockEl.textContent = f.clock;
  $("#lv-weather").textContent = `${WEATHER_TEXT[f.weather]} · ${f.temperature_f}°F${
    f.open ? "" : f.time < 12 * 3600 ? "" : " · Park closed"
  }`;
  $("#lv-guests").textContent = num(f.in_park);
  $("#lv-mood").textContent = f.avg_happiness == null ? "–" : String(Math.round(f.avg_happiness));
  $("#lv-arrivals").textContent = num(f.arrivals);
  $("#lv-seed").textContent = `${liveSeed != null ? `Seed ${liveSeed} · ` : ""}${
    liveWhere === "browser" ? "🐍 Python running in your browser" : "Running on the ParkSim server"
  }`;

  $("#lv-speed").innerHTML =
    `<button data-action="pause" title="Pause / resume">${f.paused ? "▶" : "⏸"}</button>` +
    speeds
      .map(
        (s) =>
          `<button data-action="speed" data-value="${s}" class="${
            !f.paused && f.speed === s ? "active" : ""
          }">${s}×</button>`,
      )
      .join("");

  const byId = new Map(f.attractions.map((a) => [a.id, a]));
  const rows = layout.buildings
    .map((b) => ({ b, t: catalog.get(b.template)!, f: byId.get(b.id) }))
    .filter((r) => r.f && (r.t.category === "ride" || r.t.category === "show"))
    .sort((a, b) => {
      const score = (r: typeof a) =>
        r.f!.status !== "open" ? -1 : r.t.category === "show" ? -0.5 : (r.f!.posted ?? 0);
      return score(b) - score(a);
    });
  $("#lv-waits").innerHTML = rows
    .map(
      ({ b, t, f: af }) =>
        `<li data-action="select" data-id="${esc(b.id)}" class="${b.id === selected ? "sel" : ""}">
          <span class="ico">${CATEGORY_ICON[t.category]}</span>
          <span class="name">${esc(b.name ?? t.label)}</span>
          ${waitBadge(af!, t, f.time)}
        </li>`,
    )
    .join("");

  $("#lv-log").innerHTML =
    logEntries
      .slice(0, 40)
      .map(
        (e) =>
          `<li class="sev-${e.severity}"><time>${fmtClock(e.time)}</time>${esc(e.message)}</li>`,
      )
      .join("") || `<li class="muted">All quiet so far.</li>`;

  const sel = $("#lv-selected");
  const spec = selected ? layout.building(selected) : undefined;
  const af = spec ? byId.get(spec.id) : undefined;
  const t = spec ? catalog.get(spec.template) : undefined;
  if (!spec || !af || !t) {
    sel.hidden = true;
    return;
  }
  sel.hidden = false;
  const statusText: Record<string, string> = {
    open: "Operating",
    weather: "Lightning hold",
    breakdown: "Down: technical issue",
    inspection: "Closed for safety inspection",
    manual: "Closed by operations",
    closed: "Closed for the day",
  };
  let detail = "";
  if (t.category === "ride")
    detail =
      af.posted == null
        ? `Not loading guests · ${num(af.queue ?? 0)} still in line`
        : `Posted wait <b>${af.posted} min</b> · ${num(af.queue ?? 0)} in line`;
  else if (t.category === "show")
    detail = `${num(af.seated ?? 0)} seated · next show ${af.next_show != null ? fmtClock(af.next_show) : "none today"}`;
  else detail = `${num(af.queue ?? 0)} in line · ~${af.wait ?? 0} min wait`;
  const id = esc(spec.id);
  const buttons =
    af.status === "manual"
      ? `<button data-action="reopen" data-id="${id}">✅ Reopen</button>`
      : af.status === "open"
        ? `<button data-action="close" data-id="${id}">⛔ Close</button>` +
          (t.category === "ride"
            ? `<button data-action="breakdown" data-id="${id}">🔧 Break down</button>`
            : "")
        : "";
  sel.innerHTML = `<h3>${CATEGORY_ICON[t.category]} ${esc(spec.name ?? t.label)}</h3>
    <p><b>${statusText[af.status] ?? af.status}</b></p><p class="muted">${detail}</p>
    ${buttons ? `<div class="row">${buttons}</div>` : ""}`;
}

// ---- report -----------------------------------------------------------------------------
function showReport(report: DayReport) {
  const s = report.summary;
  const incidents = Object.values(s.incidents).reduce((a, b) => a + b, 0);
  const dialog = $<HTMLDialogElement>("#report");
  const thoughts = Object.entries(report.thoughts)
    .slice(0, 6)
    .map(([k, n]) => `<tr><td>“${esc(THOUGHT_TEXT[k] ?? k)}”</td><td>${num(n)}</td></tr>`)
    .join("");
  dialog.innerHTML = `
    <h2>📊 End of day: ${esc(report.park)}</h2>
    <p class="muted">Seed ${report.seed}. Run the same seed again to replay this exact day.</p>
    <div class="kpis">
      <div><b>${num(s.attendance)}</b><span>guests</span></div>
      <div><b>${s.avg_exit_happiness}</b><span>happiness at exit</span></div>
      <div><b>${s.rides_per_guest}</b><span>rides per guest</span></div>
      <div><b>$${num(Math.round(s.revenue))}</b><span>food &amp; shop revenue</span></div>
      <div><b>${num(s.peak_in_park)}</b><span>peak in park</span></div>
      <div><b>${incidents}</b><span>incidents</span></div>
      <div><b>${s.guests_treated}</b><span>treated at First Aid</span></div>
      <div><b class="${s.unserved_medical ? "bad" : ""}">${s.unserved_medical}</b><span>untreated</span></div>
    </div>
    <table>
      <thead><tr><th>Ride</th><th>Riders</th><th>Avg wait</th><th>Peak posted</th><th>Posted vs actual</th><th>Uptime</th></tr></thead>
      <tbody>${report.rides
        .map(
          (r) =>
            `<tr><td>${esc(r.name)}</td><td>${num(r.riders)}</td><td>${r.avg_wait_min} min</td><td>${r.peak_posted_min} min</td><td>${
              r.posted_bias_min > 0 ? "+" : ""
            }${r.posted_bias_min} min</td><td>${r.uptime_pct}%</td></tr>`,
        )
        .join("")}</tbody>
    </table>
    <table><thead><tr><th>What guests said</th><th>Guests</th></tr></thead><tbody>${thoughts}</tbody></table>
    <div class="row">
      <button id="report-download">⬇ Download full report (JSON)</button>
      <button class="primary" id="report-close">Close</button>
    </div>`;
  $("#report-close").onclick = () => dialog.close();
  $("#report-download").onclick = () => {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${report.park.replace(/\W+/g, "-").toLowerCase()}-seed${report.seed}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  if (!dialog.open) dialog.showModal();
}

// ---- toast ------------------------------------------------------------------------------
let toastTimer = 0;
function toast(message: string) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el.classList.remove("show"), 3200);
}

boot();
