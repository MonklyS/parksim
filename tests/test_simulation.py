import copy
from pathlib import Path

import pytest

from parksim import Park, ParkError, SimConfig, Simulation
from parksim.attractions import Ride
from parksim.cli import main
from parksim.report import format_report
from parksim.timeutil import HOUR, MINUTE
from parksim.weather import WeatherForecast

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample_park.json"
CALM = dict(storm_chance=0.0, breakdowns=False, incidents=False)


def _run(park, **overrides):
    return Simulation(park, SimConfig(**overrides)).run()


def test_same_seed_gives_identical_results(sample_park):
    a = _run(sample_park, seed=11, attendance=1500)
    b = _run(sample_park, seed=11, attendance=1500)
    c = _run(sample_park, seed=12, attendance=1500)
    assert a == b
    assert a != c


def test_every_guest_arrives_and_leaves(sample_park):
    sim = Simulation(sample_park, SimConfig(seed=5, attendance=2000))
    report = sim.run()
    assert report["summary"]["attendance"] == 2000
    assert sum(report["exit_reasons"].values()) == 2000
    assert sim.guests == {}


def test_rides_never_exceed_their_hourly_capacity(sample_park):
    report = _run(sample_park, seed=3, attendance=12000, **CALM)
    operating_hours = 12 + 3  # queues keep running after close
    for ride in report["rides"]:
        assert ride["riders"] <= ride["capacity_per_hour"] * operating_hours
        assert ride["uptime_pct"] == 100.0


def test_more_guests_means_longer_waits(sample_park):
    light = _run(sample_park, seed=8, attendance=1000, **CALM)
    heavy = _run(sample_park, seed=8, attendance=10000, **CALM)
    avg = lambda r: sum(x["avg_wait_min"] for x in r["rides"]) / len(r["rides"])  # noqa: E731
    assert avg(heavy) > avg(light) * 2


def test_posted_waits_round_to_five_minutes(sample_park):
    sim = Simulation(sample_park, SimConfig(seed=1, attendance=6000, **CALM))
    sim.advance(4 * HOUR)
    for ride in sim.rides:
        posted = sim.posted[ride.id]
        assert posted is not None and posted >= 5 and posted % 5 == 0


def test_lightning_closes_outdoor_rides_but_not_indoor_ones(sample_park):
    weather = WeatherForecast()
    weather.add_storm(14 * HOUR, 60)
    sim = Simulation(sample_park, SimConfig(seed=2, attendance=3000, **CALM), weather)
    sim.advance(5 * HOUR + 30 * MINUTE)  # 14:30, mid-storm
    statuses = {a.id: a.status for a in sim.park.attractions.values()}
    assert statuses["thunder_ridge"] == "weather"
    assert statuses["lagoon_lights"] == "weather"
    assert statuses["haunted_hollow"] == "open"  # indoor
    assert statuses["starbound"] == "open"
    sim.advance(2 * HOUR)  # 16:30 - storm + rain tail + 30 min lightning hold have passed
    assert sim.park.attractions["thunder_ridge"].status == "open"


def test_storm_mid_hold_resets_the_all_clear(sample_park):
    weather = WeatherForecast()
    weather.add_storm(12 * HOUR, 30)
    sim = Simulation(sample_park, SimConfig(seed=2, attendance=500, **CALM), weather)
    sim.advance(3 * HOUR + 40 * MINUTE)  # 12:40 - storm over, rain tail, hold running
    sim.trigger_storm(minutes=20)  # new strike resets the hold
    sim.advance(45 * MINUTE)  # 13:25: the original 13:00 all-clear must have been ignored
    assert sim.park.attractions["thunder_ridge"].status == "weather"


def test_forced_breakdown_closes_then_reopens(sample_park):
    sim = Simulation(sample_park, SimConfig(seed=4, attendance=4000, **CALM))
    sim.advance(2 * HOUR)
    ride = sim.park.attractions["gator_run"]
    assert isinstance(ride, Ride)
    sim.force_breakdown("gator_run", minutes=30)
    assert ride.status == "breakdown"
    assert sim.posted  # posted waits exist
    sim.advance(10 * MINUTE)
    assert sim.posted["gator_run"] is None
    sim.advance(30 * MINUTE)
    assert ride.status == "open"
    report = sim.run()
    gator = next(r for r in report["rides"] if r["id"] == "gator_run")
    assert gator["closures"] == {"breakdown": 1}
    assert 95 < gator["uptime_pct"] < 96


def test_manual_close_overlaps_with_other_closures(sample_park):
    sim = Simulation(sample_park, SimConfig(seed=4, attendance=500, **CALM))
    sim.advance(HOUR)
    sim.close_attraction("starbound")
    assert sim.park.attractions["starbound"].status == "manual"
    sim.close_attraction("market_hall")
    sim.reopen_attraction("starbound")
    assert sim.park.attractions["starbound"].status == "open"
    with pytest.raises(ParkError):
        sim.force_breakdown("market_hall")
    with pytest.raises(ParkError):
        sim.close_attraction("nope")


def test_without_first_aid_injured_guests_go_home_untreated(tiny_park_data):
    data = copy.deepcopy(tiny_park_data)
    data["attractions"] = [a for a in data["attractions"] if a["id"] != "aid"]
    report = _run(
        Park.from_dict(data),
        seed=9,
        attendance=1500,
        storm_chance=0,
        incident_rate=0.05,
        trip_rate=0.0,
    )
    assert sum(report["summary"]["incidents"].values()) > 0
    assert report["summary"]["unserved_medical"] > 0
    assert report["summary"]["guests_treated"] == 0
    assert report["thoughts"].get("no_first_aid", 0) > 0


def test_with_first_aid_incidents_are_treated(tiny_park_data):
    report = _run(
        Park.from_dict(tiny_park_data), seed=9, attendance=1500, storm_chance=0, incident_rate=0.05
    )
    assert report["summary"]["guests_treated"] > 0
    assert report["summary"]["unserved_medical"] == 0


def test_capacity_limit_turns_guests_away(tiny_park_data):
    report = _run(Park.from_dict(tiny_park_data), seed=1, attendance=3000, max_capacity=200)
    assert report["summary"]["turned_away"] > 0
    assert report["summary"]["peak_in_park"] <= 200


def test_snapshot_shape(sample_park):
    sim = Simulation(sample_park, SimConfig(seed=1, attendance=800))
    sim.advance(HOUR)
    snap = sim.snapshot()
    assert snap["clock"] == "10:00"
    assert snap["guests_in_park"] == len(snap["guests"]) > 0
    assert {a["id"] for a in snap["attractions"]} == set(sample_park.attractions)


def test_simulation_does_not_mutate_the_input_park(sample_park):
    before = sample_park.to_dict()
    _run(sample_park, seed=1, attendance=500)
    assert sample_park.to_dict() == before
    assert all(a.stats.guests_served == 0 for a in sample_park.attractions.values())


def test_text_report_mentions_key_sections(sample_park):
    text = format_report(_run(sample_park, seed=6, attendance=1000))
    for heading in ("POSTED WAIT TIMES", "RIDE PERFORMANCE", "OPERATIONS LOG", "WHY GUESTS LEFT"):
        assert heading in text


def test_cli_run_validate_and_errors(tmp_path, capsys):
    out_json = tmp_path / "day.json"
    assert (
        main(
            [
                "run",
                str(SAMPLE),
                "--attendance",
                "800",
                "--storm-at",
                "13:00",
                "--json",
                str(out_json),
            ]
        )
        == 0
    )
    assert out_json.exists()
    assert "lightning hold" in capsys.readouterr().out
    assert main(["validate", str(SAMPLE)]) == 0
    assert main(["validate", str(tmp_path / "missing.json")]) == 2
