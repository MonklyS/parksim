# Roadmap

Each phase ends with something that works and can be demoed.

## Phase 1: Simulation engine ✅
- Discrete-event engine, tile grid and pathfinding, guest needs and behavior
- Rides, shows, food, restrooms, shops, First Aid
- Wait-time model with posted-vs-actual tracking
- Weather with lightning holds, breakdowns (MTBF/MTTR), incidents
- CLI, JSON report, tests, lint, CI

## Phase 2: API + 2D park editor ✅
- `parksim_server/`: FastAPI app
  - `POST /api/parks`, `GET/PUT /api/parks/{id}`: save and load layouts (Pydantic-validated, edit tokens)
  - `GET /api/catalog`, `POST /api/validate`, `GET /api/examples/sample`
  - `WS /api/live`: one simulation per viewer, frames every 0.5 s, operations commands
- `web/`: TypeScript + Vite + PixiJS
  - Top-down tile editor: paint paths, place buildings, move the gate, rename, demolish, live park check
  - Live view: mood-colored guest dots gliding along routes, wait badges, lightning holds, ops controls
  - End-of-day report with JSON download
- Share links (view and run anyone's park; only the owner can save over it)

## Phase 2.5: Python in the browser ✅
- `parksim` runs in a Web Worker via Pyodide; Vite ships the engine's `.py` files with the build
- Design-mode **Forecast**: a full day in about 5 s with bottleneck hints, using a fixed seed for fair comparisons
- Operate mode runs in the browser by default, with the server as an option (same protocol, same results)
- Happiness model tuned: diminishing returns on repeat rides and time spent in line

## Next up
- Deploy: Dockerfile and Fly.io config; link from matthewfeyler.dev
- Undo/redo, rotate buildings, resize the park from the editor
- Binary or delta-encoded frames for very large crowds

## Phase 3: "Park app" + operations dashboard
- Guest-facing page styled like a theme-park phone app: live wait times, map, showtimes, closures
- Operations dashboard: wait-time charts over the day, uptime, incident log, guest sentiment
- Simulation speed controls (1x, 10x, 60x) and a "what-if" panel to compare two runs side by side

## Phase 4: More realism
- Guest groups and families (shared decisions, height requirements, rider switch)
- Paid skip-the-line passes and virtual queues
- Parades along a path route; path crowding slowing walk speed
- Staffing (operators, custodial, security) and seasonal/holiday crowd calendars
- Accessibility features (accessible queue entrances, return-time passes) and their effect on flow

## Phase 5: Game mode (optional)
- Money: build costs (already in the catalog), ticket pricing, wages, loans
- Scenarios and goals ("reach 85% guest satisfaction with under 45-min average waits")
- Park rating and awards

## Process
- Work tracked as user stories on a GitHub Projects board, in two-week sprints
- Every change goes through a PR with CI (lint, tests, coverage gate)
