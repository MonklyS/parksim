# How the simulation works

## Discrete-event simulation
`Scheduler` is a priority queue of `(time, callback)` events. The loop pops the next
event, sets the clock to that time and runs it; callbacks schedule further events. Nothing
happens "between" events, so idle time costs nothing. A day with 8,000 guests is roughly
290,000 events.

Guests use a **token** to cancel stale wake-ups. Every time a guest's plan changes, their
token increases, and any older scheduled step sees the mismatch and does nothing. This
replaces deleting entries from the middle of a heap.

## Pathfinding
Each attraction gets a **BFS distance field** over the path tiles: for every tile, the
number of steps to that attraction's entrance. Travel time is a single lookup
(`tiles × tile_seconds × guest walk speed`), and the route can be recovered by stepping
"downhill" through the field (for drawing guests in the web view). Fields are cached and
rebuilt only when the layout changes.

## Ride throughput and wait times
- **Theoretical hourly capacity** = `capacity × 3600 / cycle_seconds`
- Every `cycle_seconds` a ride dispatches up to `capacity` guests from the front of its line.
- **Estimated wait** for someone joining now = `ceil(line / capacity) × cycle`. This is
  Little's law (wait = people in line ÷ throughput), counted in whole dispatches.
- **Posted wait** = estimate × 1.1, rounded *up* to the next 5 minutes, at least 5.
  It refreshes every 5 minutes, so guests decide on slightly old information, as in real life.
- The report's **posted bias** is the average of (posted − actual) per rider. A positive
  number means the sign over-promised the wait, which parks often do on purpose.

## Guest decisions (`behavior.py`)
1. **Leave** if the park is closed, the guest's planned stay is over, energy or happiness
   is very low, or (sometimes) during a thunderstorm.
2. **Urgent needs** (restroom > drink > food): go to the facility with the lowest walk + wait cost.
3. **Fun:** score each open ride by appeal, how well its intensity matches the guest's
   thrill preference, posted wait relative to the guest's patience, walk time, and rides
   already taken. Score each show by how soon it starts. Then pick from the top 5 with a
   **softmax**, so crowds spread out naturally instead of everyone picking the same ride.

## Closures
An attraction has a *set* of closure reasons (`breakdown`, `inspection`, `weather`,
`manual`) and opens only when the set is empty. A ride that breaks down during a lightning
hold correctly stays closed until both clear. Downtime is counted only within operating hours.

- **Breakdowns:** time to next failure ~ Exponential(MTBF); repair ~ LogNormal with mean = MTTR.
- **Lightning:** a thunderstorm closes every outdoor ride and show. When the storm ends,
  a 30-minute all-clear timer starts. A new storm invalidates the timer (token pattern again).
- **Line behavior on closure:** 50–70% of guests leave immediately. The rest wait, and
  are re-checked every 20 minutes.

## Incidents
Each loaded dispatch has a small chance of an incident, scaled by ride intensity:
guest illness (sent to First Aid), minor injury (First Aid plus a 20–45 min inspection
closure), or ride evacuation (45–90 min closure). Guests can also trip on paths or get
heat exhaustion when it is over 90°F and they are very thirsty. If the park has no
reachable First Aid, injured guests leave untreated, and the report says so.
