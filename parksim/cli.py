"""Command line: run a park day headlessly or validate a park file.

parksim run examples/sample_park.json --seed 7 --attendance 9000
parksim run examples/sample_park.json --storm-at 15:00 --json day.report.json
parksim validate examples/sample_park.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import ParkError
from .park import Park
from .report import format_report
from .simulation import SimConfig, Simulation
from .timeutil import parse_clock
from .weather import WeatherForecast


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="parksim", description="Theme park simulation engine")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="simulate one park day")
    run.add_argument("park", type=Path)
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--attendance", type=int, default=8000)
    run.add_argument("--high-temp", type=float, default=91.0, help="daily high in °F")
    run.add_argument("--storm-chance", type=float, default=0.35)
    run.add_argument("--storm-at", metavar="HH:MM", help="force a thunderstorm at this time")
    run.add_argument("--storm-minutes", type=float, default=60.0)
    run.add_argument("--no-breakdowns", action="store_true")
    run.add_argument("--no-incidents", action="store_true")
    run.add_argument("--json", type=Path, help="also write the full report as JSON")

    val = sub.add_parser("validate", help="check a park layout for errors and design warnings")
    val.add_argument("park", type=Path)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        park = Park.load(args.park)
    except (OSError, json.JSONDecodeError, ParkError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        warnings = park.warnings()
        print(f"{park.name}: {len(park.attractions)} attractions, layout OK")
        for w in warnings:
            print(f"  warning: {w}")
        return 0

    config = SimConfig(
        seed=args.seed,
        attendance=args.attendance,
        high_temp_f=args.high_temp,
        storm_chance=0.0 if args.storm_at else args.storm_chance,
        breakdowns=not args.no_breakdowns,
        incidents=not args.no_incidents,
    )
    weather = None
    if args.storm_at:
        weather = WeatherForecast(high_temp_f=args.high_temp)
        weather.add_storm(parse_clock(args.storm_at), args.storm_minutes)
    report = Simulation(park, config, weather).run()
    print(format_report(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nFull report written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
