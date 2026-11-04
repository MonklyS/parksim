"""The JSON bridge that the browser (Pyodide) uses to drive the engine."""

import json
from pathlib import Path

from parksim.bridge import Forecast, start_live, validate_json
from parksim.live import DaySettings

SAMPLE = (Path(__file__).resolve().parent.parent / "examples" / "sample_park.json").read_text(
    encoding="utf-8"
)


def test_validate_json_matches_server_shape():
    assert json.loads(validate_json(SAMPLE)) == {"ok": True, "error": None, "warnings": []}
    layout = json.loads(SAMPLE)
    layout["attractions"][0]["x"] = 999
    bad = json.loads(validate_json(json.dumps(layout)))
    assert not bad["ok"] and "thunder_ridge" in bad["error"]
    assert not json.loads(validate_json("not json"))["ok"]


def test_day_settings_are_clamped_like_the_api():
    s = DaySettings.from_dict(
        {"attendance": 10**9, "storm_chance": 7, "high_temp_f": "hot", "seed": -5}
    )
    assert s.attendance == 30_000
    assert s.storm_chance == 1
    assert s.high_temp_f == 91.0
    assert s.seed == 0
    assert DaySettings.from_dict(None) == DaySettings()


def test_forecast_runs_in_steps_and_reports():
    settings = json.dumps({"seed": 4, "attendance": 1500, "storm_chance": 0})
    forecast = Forecast(SAMPLE, settings)
    progress = []
    while not forecast.done:
        progress.append(forecast.step(3 * 3600))
    assert progress == sorted(progress) and progress[-1] == 1.0
    assert forecast.clock == "24:00"
    report = json.loads(forecast.report_json())
    assert report["summary"]["attendance"] == 1500
    assert report["seed"] == 4


def test_live_bridge_speaks_the_same_protocol_as_the_websocket():
    session = start_live(SAMPLE, json.dumps({"seed": 9, "attendance": 800}))
    init = json.loads(session.init_json())
    assert init["type"] == "init" and init["seed"] == 9
    session.command_json('{"type": "speed", "value": 1200}')
    session.command_json('{"type": "storm", "minutes": 30}')
    session.command_json("{oops")
    session.tick(1.0)
    frame = json.loads(session.frame_json())
    assert frame["type"] == "frame" and frame["speed"] == 1200
    assert frame["weather"] == "thunderstorm"
    assert frame["errors"] == ["could not parse command"]
    while not session.done:
        session.tick(1.0)
    report = json.loads(session.report_json())
    assert report["type"] == "report"
    assert report["report"]["summary"]["attendance"] == 800
