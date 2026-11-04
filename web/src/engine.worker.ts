// Runs the ParkSim Python engine inside the browser with Pyodide (CPython compiled to
// WebAssembly). It lives in a Web Worker so a busy simulation never freezes the page.

const PYODIDE_VERSION = "314.0.7"; // Python 3.14
const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
const ENGINE_URL = `${import.meta.env.BASE_URL}py/parksim/`;
const ENGINE_DIR = "/home/pyodide/parksim";
const LIVE_TICK_MS = 500;
const FORECAST_STEP_SECONDS = 1800;

interface Pyodide {
  FS: { mkdirTree(path: string): void; writeFile(path: string, data: string): void };
  runPython(code: string): unknown;
  pyimport(name: string): PyObject;
}
// Python objects come through as proxies; we only call methods that take and return
// plain numbers, booleans and strings.
type PyObject = { [name: string]: any; destroy(): void };

export type Inbound =
  | { type: "warmup" }
  | { type: "forecast"; id: number; layout: string; settings: string }
  | { type: "cancel-forecast" }
  | { type: "start"; layout: string; settings: string }
  | { type: "command"; command: string }
  | { type: "stop" };

export type Outbound =
  | { type: "status"; stage: "loading" | "ready" | "error"; message: string }
  | { type: "live"; message: string }
  | { type: "progress"; id: number; progress: number; clock: string }
  | { type: "forecast"; id: number; report: string; seconds: number }
  | { type: "forecast-error"; id: number; message: string };

const ctx = self as unknown as {
  postMessage(message: Outbound): void;
  onmessage: ((event: MessageEvent<Inbound>) => void) | null;
};
const post = (message: Outbound) => ctx.postMessage(message);

/** Last line of a Python traceback, e.g. "ParkError: 'x' overlaps a path at (3, 4)". */
function pyError(error: unknown) {
  const text = error instanceof Error ? error.message : String(error);
  const lines = text.trim().split("\n");
  return lines[lines.length - 1] || text;
}

let booting: Promise<PyObject> | null = null;

function boot(): Promise<PyObject> {
  booting ??= (async () => {
    try {
      post({ type: "status", stage: "loading", message: "Downloading Python (Pyodide)…" });
      const { loadPyodide } = await import(/* @vite-ignore */ `${PYODIDE_URL}pyodide.mjs`);
      const py: Pyodide = await loadPyodide({ indexURL: PYODIDE_URL });

      post({ type: "status", stage: "loading", message: "Loading the ParkSim engine…" });
      const manifest: { version: string; files: string[] } = await (
        await fetch(`${ENGINE_URL}manifest.json`, { cache: "no-cache" })
      ).json();
      py.FS.mkdirTree(ENGINE_DIR);
      await Promise.all(
        manifest.files.map(async (file) => {
          const source = await (await fetch(`${ENGINE_URL}${file}?v=${manifest.version}`)).text();
          py.FS.writeFile(`${ENGINE_DIR}/${file}`, source);
        }),
      );
      py.runPython("import sys\nif '/home/pyodide' not in sys.path: sys.path.insert(0, '/home/pyodide')");
      const bridge = py.pyimport("parksim.bridge");
      const version = py.runPython("import sys; sys.version.split()[0]");
      post({ type: "status", stage: "ready", message: `Python ${version} running in your browser` });
      return bridge;
    } catch (error) {
      booting = null; // allow a retry
      post({ type: "status", stage: "error", message: `Couldn't start Python: ${pyError(error)}` });
      throw error;
    }
  })();
  return booting;
}

// ---- live day -------------------------------------------------------------------------
let live: PyObject | null = null;
let liveTimer = 0;
let liveGeneration = 0;

function stopLive() {
  liveGeneration++;
  clearInterval(liveTimer);
  liveTimer = 0;
  live?.destroy();
  live = null;
}

async function startLive(layout: string, settings: string) {
  stopLive();
  const generation = liveGeneration;
  let bridge: PyObject;
  try {
    bridge = await boot();
  } catch {
    post({ type: "live", message: JSON.stringify({ type: "error", message: "Python failed to load." }) });
    return;
  }
  if (generation !== liveGeneration) return; // stopped while Python was loading
  try {
    live = bridge.start_live(layout, settings) as PyObject;
  } catch (error) {
    post({ type: "live", message: JSON.stringify({ type: "error", message: pyError(error) }) });
    return;
  }
  const session = live;
  post({ type: "live", message: session.init_json() });
  let last = performance.now();
  liveTimer = self.setInterval(() => {
    const now = performance.now();
    session.tick((now - last) / 1000);
    last = now;
    post({ type: "live", message: session.frame_json() });
    if (session.done) {
      post({ type: "live", message: session.report_json() });
      clearInterval(liveTimer);
      liveTimer = 0;
    }
  }, LIVE_TICK_MS);
}

// ---- forecast -------------------------------------------------------------------------
let forecastGeneration = 0;

async function forecast(id: number, layout: string, settings: string) {
  const generation = ++forecastGeneration;
  let job: PyObject | null = null;
  try {
    const bridge = await boot();
    job = bridge.Forecast(layout, settings) as PyObject;
    const started = performance.now();
    while (!job.done) {
      if (generation !== forecastGeneration) return; // cancelled
      const progress = job.step(FORECAST_STEP_SECONDS) as number;
      post({ type: "progress", id, progress, clock: job.clock });
      await new Promise((resolve) => setTimeout(resolve, 0)); // let cancel messages in
    }
    post({
      type: "forecast",
      id,
      report: job.report_json(),
      seconds: (performance.now() - started) / 1000,
    });
  } catch (error) {
    post({ type: "forecast-error", id, message: pyError(error) });
  } finally {
    job?.destroy();
  }
}

ctx.onmessage = ({ data }) => {
  switch (data.type) {
    case "warmup":
      boot().catch(() => undefined);
      break;
    case "forecast":
      forecast(data.id, data.layout, data.settings);
      break;
    case "cancel-forecast":
      forecastGeneration++;
      break;
    case "start":
      startLive(data.layout, data.settings);
      break;
    case "command":
      live?.command_json(data.command);
      break;
    case "stop":
      stopLive();
      break;
  }
};
