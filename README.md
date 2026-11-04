# ParkSim: Theme Park Designer & Operations Simulator

Design a theme park in your browser, then watch it operate: guests arrive, lines build,
wait times update live, afternoon storms put coasters on lightning hold, rides break
down, and guests who get hurt need a First Aid station you remembered to build.

> **Status:** Phase 2.5. There's a browser-based 2D park editor and a live operations
> view, backed by a Python simulation engine. The same engine runs **in the browser
> through Pyodide** (Python compiled to WebAssembly) or on a FastAPI server. See the
> [roadmap](docs/ROADMAP.md).

## Python on both sides

The simulation engine (`parksim/`) is plain Python with no dependencies, so one codebase
runs in two places:

| Where | How | Used for |
|---|---|---|
| **Browser** | Pyodide (CPython 3.14 compiled to WebAssembly) in a Web Worker | Default for Operate mode, plus the Design-mode **Forecast** that simulates a full day in about 5 s |
| **Server** | FastAPI + WebSocket | Optional engine choice; saving parks, share links, validation |

Vite copies the engine's `.py` files into the build, and the worker loads them into
Pyodide's virtual filesystem. That makes it impossible for the browser and server copies
to drift apart, and the same seed produces the identical day in both. Running in the
browser means a viewer costs the server no CPU, so the site can be hosted very cheaply.

## Run it

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"
cd web && npm install && npm run build && cd ..

parksim-server                    # open http://localhost:8000
```

**Design mode:** lay paths, then place rides, shows, food, restrooms, shops and First Aid
from the palette. Each building's entrance attaches to an adjacent path automatically. The
park check (validated by the Python engine) flags problems as you build, such as an
unreachable ride or a park with no restrooms. **Save** stores the park and **Share**
copies a link. Anyone with the link can view it and run it, but only you can save over it.

**Forecast:** one click simulates a full day of your current layout in the browser (fixed
seed, clear skies) and points out bottlenecks, like a ride peaking at a 60-minute wait or
guests who can't find a restroom. Since the seed is fixed, you can compare layout changes fairly.

**Operate mode:** open the gates and watch the day run at 1× to 1200× speed. Guest
dots move along the paths, colored by mood. Wait-time badges update live, and you can
close a ride, force a breakdown or call in a thunderstorm while it runs. At closing you
get an end-of-day report.

For front-end development with hot reload, run `parksim-server` and `npm run dev` in
`web/` (Vite proxies `/api` to the Python server).

### Command-line simulation

```bash
parksim validate examples/sample_park.json
parksim run examples/sample_park.json --seed 42 --attendance 8000
parksim run examples/sample_park.json --storm-at 15:00 --storm-minutes 60 --json day.report.json
```

A simulated day with 8,000 guests takes about 4 seconds and produces an hour-by-hour
posted wait board, ride performance stats (throughput, uptime, posted-vs-actual wait
accuracy), show attendance, food and restroom service levels, an operations log of
closures and incidents, and what guests were thinking.

## What the engine models

| Area | How it's simulated |
|---|---|
| **Wait times** | Each ride has a capacity per dispatch and a cycle time, giving a theoretical hourly capacity. Posted waits = line length ÷ throughput, padded ~10% and rounded up to 5 min like real parks. Each rider's actual wait is recorded so you can see how accurate the posted numbers were. |
| **Guests** | Each guest has hunger, thirst, restroom needs, energy, happiness, thrill preference, patience and budget. Decisions use the *posted* waits, the way real visitors check an app. |
| **Crowds** | Arrivals follow a curve that peaks at opening. Guests leave when they're done, tired, unhappy or caught in a storm. |
| **Weather** | Florida-style days: a chance of a pop-up afternoon thunderstorm. Lightning closes outdoor rides and shows until a 30-minute all-clear, which restarts if lightning strikes again. Rain pushes guests indoors and heat makes them thirstier. |
| **Breakdowns** | Rides fail at random based on a mean time between failures (MTBF) and are repaired over a mean time to repair (MTTR). People in line leave over time. |
| **Incidents** | Guest illness, minor injuries (ride closed for inspection), ride evacuations, trips and falls, heat exhaustion. Guests go to First Aid; if the park has none, they leave untreated. |
| **Services** | Food, drinks, restrooms and shops are modelled as multi-server queues. Guests skip long food lines but will wait for a restroom. |
| **Operations controls** | `close_attraction`, `reopen_attraction`, `force_breakdown` and `trigger_storm` let a designer change a park while it's running. |

## Architecture

```
parksim/            pure-Python engine, no dependencies (can also run in the browser via Pyodide)
  scheduler.py      discrete-event scheduler (priority queue of timed callbacks)
  grid.py           tile map + BFS distance fields for pathfinding
  catalog.py        templates for rides, shows, food, restrooms, shops, First Aid
  attractions.py    Ride / Show / Facility models and closure reasons
  guest.py          guest needs and mood
  behavior.py       guest decision-making (needs first, then softmax over fun options)
  weather.py        forecast generation, storms, temperature curve
  simulation.py     the engine that connects everything above + operations controls
  report.py         JSON report + text summary
  cli.py            `parksim run` / `parksim validate`
  view.py           compact live frames: guest positions interpolated along their routes
  live.py           a real-time session (speed, pause, ops commands): shared by server and browser
  bridge.py         JSON-string API that the browser's Pyodide worker calls
parksim_server/     FastAPI app
  app.py            REST endpoints + /api/live WebSocket; also serves the built front end
  models.py         Pydantic request schemas and limits
  storage.py        SQLite park store; edit tokens stored only as SHA-256 hashes
  live.py           one simulation per WebSocket: speed control, operations commands
web/                TypeScript + Vite + PixiJS
  src/layout.ts     editable tile grid, placement rules, entrance auto-attach
  src/renderer.ts   PixiJS map, buildings, wait badges, smoothly animated guest dots
  src/main.ts       editor tools, panels, forecast, live operations view, end-of-day report
  src/engine.worker.ts  loads Pyodide + the parksim .py files; runs forecasts and live days
  src/engine.ts     page-side handle: same live-run interface as the server WebSocket
```

```
Browser (TypeScript + PixiJS)          Python server (FastAPI)
  design ──POST /api/validate────────▶ Pydantic schema ▶ parksim.Park rules
         ──POST/PUT /api/parks───────▶ SQLite (id + hashed edit token)
  operate ═══ WebSocket /api/live ═══▶ LiveSession ─ Simulation.advance(dt × speed)
  (or, by default, the same LiveSession in a Pyodide Web Worker: no server involved)
          ◀══ frame every 0.5 s ═════  parksim.view.frame(): waits, statuses, guests
          ═══ {"type":"storm"} ══════▶ sim.trigger_storm() …
```

The engine is a **discrete-event simulation**. Instead of updating every guest every
tick, it jumps from one scheduled event to the next (a train dispatch, a guest reaching
a ride, a storm starting). That's why a full day with ~290,000 events runs in a few
seconds of pure Python. More detail in [docs/SIMULATION.md](docs/SIMULATION.md).

## Engineering practices

- **Tests:** pytest suite (52 tests, ~95% branch coverage). Covers determinism, capacity
  limits, lightning holds, breakdown recovery, First Aid coverage, every API endpoint,
  edit-token checks, the live WebSocket protocol and the browser bridge.
- **Linting and formatting:** ruff for Python. The TypeScript is strict-mode typechecked.
- **CI:** GitHub Actions runs lint, tests with a coverage gate and a smoke-run of a
  simulated day on Python 3.12–3.14, plus a front-end typecheck and build.
- **Security basics:** schema limits on every input, HTML-escaped user text, edit tokens
  stored only as hashes and compared in constant time.
- **Reproducible:** the same seed always gives the same day.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Simulation | Python 3.12+, stdlib only | Portable: the same code runs on the server and in the browser |
| Python in the browser | Pyodide 314.0.7 (CPython 3.14 → WebAssembly) in a Web Worker | Forecasts and live days with zero server load |
| API | FastAPI, Pydantic, WebSockets | Typed REST endpoints and live wait-time streaming |
| Storage | SQLite (Postgres-ready) | Saved parks and shareable links |
| Front end | TypeScript, Vite, PixiJS 8 | Fast 2D canvas for thousands of moving guests |
| Hosting | One container (FastAPI serves the built front end) on Fly.io/Railway | |
