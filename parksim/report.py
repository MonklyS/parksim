"""Turn a finished simulation into a JSON-friendly report and a readable text summary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .metrics import THOUGHT_TEXT
from .timeutil import HOUR, clock

if TYPE_CHECKING:
    from .simulation import Simulation

STATUS_CODE = {"breakdown": "DN", "inspection": "IN", "weather": "WX", "manual": "CL"}


def build_report(sim: Simulation) -> dict[str, Any]:
    cfg, m = sim.config, sim.metrics
    operating = cfg.close_time - cfg.open_time

    rides = [
        {
            "id": r.id,
            "name": r.name,
            "template": r.template,
            "riders": r.stats.guests_served,
            "capacity_per_hour": round(r.throughput_per_hour),
            "avg_wait_min": round(r.stats.avg_wait_minutes, 1),
            "max_wait_min": round(r.stats.wait_max / 60, 1),
            "peak_posted_min": r.stats.peak_posted,
            "posted_bias_min": round(r.stats.posted_error_total / r.stats.posted_samples, 1)
            if r.stats.posted_samples
            else 0.0,
            "uptime_pct": round(100 * (1 - r.stats.downtime / operating), 1),
            "closures": dict(r.stats.closures),
        }
        for r in sim.rides
    ]
    shows = [
        {
            "id": s.id,
            "name": s.name,
            "performances": s.stats.performances,
            "cancelled": s.stats.shows_cancelled,
            "guests": s.stats.guests_served,
            "avg_audience": round(s.stats.guests_served / s.stats.performances)
            if s.stats.performances
            else 0,
            "capacity": s.capacity,
        }
        for s in sim.shows
    ]
    facilities = [
        {
            "id": f.id,
            "name": f.name,
            "category": f.category.value,
            "served": f.stats.guests_served,
            "avg_wait_min": round(f.stats.avg_wait_minutes, 1),
            "max_wait_min": round(f.stats.wait_max / 60, 1),
            "revenue": round(f.stats.revenue, 2),
        }
        for f in sim.facilities
    ]

    by_time = {s.time: s for s in m.samples}
    hours = [cfg.open_time + h * HOUR for h in range(int(operating // HOUR))]
    hourly: dict[str, list[int | str | None]] = {}
    for r in sim.rides:
        row: list[int | str | None] = []
        for t in hours:
            sample = by_time.get(t)
            if sample is None:
                row.append(None)
            elif sample.posted.get(r.id) is None:
                row.append(STATUS_CODE.get(sample.status.get(r.id, ""), "--"))
            else:
                row.append(sample.posted[r.id])
        hourly[r.id] = row

    attendance = m.arrivals
    exits = m.exit_happiness
    return {
        "park": sim.park.name,
        "seed": cfg.seed,
        "summary": {
            "attendance": attendance,
            "turned_away": m.turned_away,
            "peak_in_park": m.peak_in_park,
            "avg_exit_happiness": round(sum(exits) / len(exits), 1) if exits else 0.0,
            "rides_per_guest": round(m.total_rides / attendance, 2) if attendance else 0.0,
            "revenue": round(m.revenue, 2),
            "revenue_per_guest": round(m.revenue / attendance, 2) if attendance else 0.0,
            "incidents": dict(m.incidents),
            "guests_treated": m.treated,
            "unserved_medical": m.unserved_medical,
            "events_processed": sim.clock.processed,
        },
        "weather": {
            "high_temp_f": sim.weather.high_temp_f,
            "segments": [
                seg.to_dict() for seg in sim.weather.segments(cfg.open_time, cfg.close_time)
            ],
        },
        "wait_board": {"hours": [clock(t) for t in hours], "rides": hourly},
        "rides": rides,
        "shows": shows,
        "facilities": facilities,
        "thoughts": dict(m.thoughts.most_common()),
        "exit_reasons": dict(m.exit_reasons.most_common()),
        "log": m.log_dicts(),
    }


def _table(headers: list[str], rows: list[list[Any]], first_width: int = 24) -> list[str]:
    widths = [first_width] + [max(len(h), 6) for h in headers[1:]]
    lines = [
        "  ".join(
            h.ljust(w) if i == 0 else h.rjust(w)
            for i, (h, w) in enumerate(zip(headers, widths, strict=True))
        )
    ]
    for row in rows:
        lines.append(
            "  ".join(
                str(v)[:w].ljust(w) if i == 0 else str(v).rjust(w)
                for i, (v, w) in enumerate(zip(row, widths, strict=True))
            )
        )
    return lines


def format_report(report: dict[str, Any], log_limit: int = 40) -> str:
    s = report["summary"]
    names = {r["id"]: r["name"] for r in report["rides"]}
    out = [f"=== {report['park']}: simulated day (seed {report['seed']}) ===", ""]

    weather = " | ".join(
        f"{clock(seg['start'])}-{clock(seg['end'])} {seg['condition']}"
        for seg in report["weather"]["segments"]
    )
    out += [f"Weather (high {report['weather']['high_temp_f']:.0f}°F): {weather}", ""]
    out += [
        f"Attendance {s['attendance']:,}  |  Peak in park {s['peak_in_park']:,}  |  "
        f"Rides/guest {s['rides_per_guest']}  |  Avg happiness at exit {s['avg_exit_happiness']}",
        f"Food/shop revenue ${s['revenue']:,.0f} (${s['revenue_per_guest']:.2f}/guest)  |  "
        f"Incidents {sum(s['incidents'].values())}  |  Treated at First Aid {s['guests_treated']}"
        + (f"  |  UNTREATED {s['unserved_medical']}" if s["unserved_medical"] else ""),
        f"({s['events_processed']:,} simulation events processed)",
        "",
        "POSTED WAIT TIMES (minutes, top of each hour; DN=down IN=inspection WX=weather CL=closed)",
    ]
    board = report["wait_board"]
    out.append("Attraction".ljust(24) + "".join(h[:2].rjust(5) for h in board["hours"]))
    for ride_id, row in board["rides"].items():
        out.append(
            names[ride_id][:24].ljust(24)
            + "".join(("" if v is None else str(v)).rjust(5) for v in row)
        )

    out += ["", "RIDE PERFORMANCE"]
    out += _table(
        ["Attraction", "Riders", "Cap/hr", "AvgWait", "MaxWait", "PeakPost", "PostBias", "Uptime%"],
        [
            [
                r["name"],
                f"{r['riders']:,}",
                f"{r['capacity_per_hour']:,}",
                r["avg_wait_min"],
                r["max_wait_min"],
                r["peak_posted_min"],
                f"{r['posted_bias_min']:+}",
                r["uptime_pct"],
            ]
            for r in report["rides"]
        ],
    )
    if report["shows"]:
        out += ["", "SHOWS"]
        out += _table(
            ["Show", "Shows", "Cancel", "AvgAud", "Capacity"],
            [
                [
                    x["name"],
                    x["performances"],
                    x["cancelled"],
                    f"{x['avg_audience']:,}",
                    f"{x['capacity']:,}",
                ]
                for x in report["shows"]
            ],
        )
    if report["facilities"]:
        out += ["", "FOOD & GUEST SERVICES"]
        out += _table(
            ["Facility", "Served", "AvgWait", "MaxWait", "Revenue"],
            [
                [
                    f["name"],
                    f"{f['served']:,}",
                    f["avg_wait_min"],
                    f["max_wait_min"],
                    f"${f['revenue']:,.0f}",
                ]
                for f in report["facilities"]
            ],
        )

    notable = [e for e in report["log"] if e["severity"] != "info" or e["kind"] == "weather"]
    out += ["", f"OPERATIONS LOG ({len(notable)} notable events)"]
    for e in notable[:log_limit]:
        out.append(f"  {clock(e['time'])}  [{e['severity'].upper():8}] {e['message']}")
    if len(notable) > log_limit:
        out.append(f"  ... {len(notable) - log_limit} more")

    out += ["", "WHAT GUESTS ARE SAYING"]
    for key, count in list(report["thoughts"].items())[:8]:
        out.append(f'  {count:>7,}  "{THOUGHT_TEXT.get(key, key)}"')
    out += ["", "WHY GUESTS LEFT"]
    for reason, count in report["exit_reasons"].items():
        out.append(f"  {count:>7,}  {reason.replace('_', ' ')}")
    return "\n".join(out)
