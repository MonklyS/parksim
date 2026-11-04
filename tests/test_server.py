import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parksim import Park
from parksim.view import frame
from parksim_server import create_app
from parksim_server.live import LiveSession
from parksim_server.models import SimSettings

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample_park.json"


@pytest.fixture
def layout() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "test.db", static_dir=None)) as c:
        yield c


def test_health_catalog_and_sample(client):
    assert client.get("/api/health").json()["status"] == "ok"
    catalog = client.get("/api/catalog").json()
    coaster = next(t for t in catalog if t["key"] == "coaster_steel")
    assert coaster["category"] == "ride"
    assert coaster["stats"]["capacity"] == 24
    assert "label" not in coaster["stats"]
    assert client.get("/api/examples/sample").json()["name"]


def test_validate_reports_errors_and_warnings(client, layout):
    ok = client.post("/api/validate", json=layout).json()
    assert ok == {"ok": True, "error": None, "warnings": []}

    layout["attractions"] = [a for a in layout["attractions"] if a["template"] != "first_aid"]
    warn = client.post("/api/validate", json=layout).json()
    assert warn["ok"] and any("First Aid" in w for w in warn["warnings"])

    layout["attractions"][0]["entrance"] = [0, 0]
    bad = client.post("/api/validate", json=layout).json()
    assert not bad["ok"] and "entrance" in bad["error"]


def test_schema_limits_are_enforced(client, layout):
    assert client.post("/api/validate", json={**layout, "width": 5000}).status_code == 422
    assert client.post("/api/validate", json={**layout, "hacker": True}).status_code == 422
    layout["attractions"][0]["id"] = "bad id with spaces"
    assert client.post("/api/validate", json=layout).status_code == 422


def test_create_get_and_update_with_edit_token(client, layout):
    created = client.post("/api/parks", json=layout)
    assert created.status_code == 201
    park_id, token = created.json()["id"], created.json()["edit_token"]

    fetched = client.get(f"/api/parks/{park_id}").json()
    assert fetched["layout"] == Park.from_dict(layout).to_dict()

    layout["name"] = "Renamed Park"
    assert client.put(f"/api/parks/{park_id}", json=layout).status_code == 401
    wrong = client.put(f"/api/parks/{park_id}", json=layout, headers={"X-Edit-Token": "nope"})
    assert wrong.status_code == 403
    ok = client.put(f"/api/parks/{park_id}", json=layout, headers={"X-Edit-Token": token})
    assert ok.status_code == 200
    assert client.get(f"/api/parks/{park_id}").json()["layout"]["name"] == "Renamed Park"

    missing = client.put("/api/parks/zzzz", json=layout, headers={"X-Edit-Token": token})
    assert missing.status_code == 404
    assert client.get("/api/parks/zzzz").status_code == 404


def test_invalid_layout_is_not_saved(client, layout):
    layout["attractions"][0]["x"] = 500
    response = client.post("/api/parks", json=layout)
    assert response.status_code == 422
    assert "thunder_ridge" in response.json()["detail"]


def test_live_websocket_streams_frames_and_accepts_commands(client, layout):
    with client.websocket_connect("/api/live") as ws:
        ws.send_json(
            {"type": "start", "layout": layout, "settings": {"seed": 3, "attendance": 500}}
        )
        init = ws.receive_json()
        assert init["type"] == "init" and init["seed"] == 3
        ws.send_json({"type": "speed", "value": 1200})
        ws.send_json({"type": "storm", "minutes": 30})
        ws.send_json({"type": "teleport"})
        frames = [ws.receive_json() for _ in range(3)]
        assert all(f["type"] == "frame" for f in frames)
        assert any(f["errors"] for f in frames)
        assert frames[-1]["speed"] == 1200
        assert frames[-1]["weather"] == "thunderstorm"
        assert len(frames[-1]["attractions"]) == len(layout["attractions"])


def test_live_websocket_rejects_bad_start(client, layout):
    with client.websocket_connect("/api/live") as ws:
        ws.send_json({"type": "hello"})
        assert ws.receive_json()["type"] == "error"
    layout["attractions"][0]["template"] = "warp_drive"
    with client.websocket_connect("/api/live") as ws:
        ws.send_json({"type": "start", "layout": layout})
        message = ws.receive_json()
        assert message["type"] == "error" and "warp_drive" in message["message"]


def test_live_session_runs_a_full_day_and_handles_commands(layout):
    session = LiveSession(Park.from_dict(layout), SimSettings(seed=1, attendance=1500))
    session.handle({"type": "speed", "value": 1200})
    session.handle({"type": "speed", "value": 7})
    session.handle({"type": "close", "id": "thunder_ridge"})
    session.handle({"type": "breakdown", "id": "golden_carousel", "minutes": "abc"})
    session.handle({"type": "breakdown", "id": "market_hall"})
    session.handle("not a dict")
    first = session.frame_message()
    assert len(first["errors"]) == 3
    statuses = {a["id"]: a["status"] for a in first["attractions"]}
    assert statuses["thunder_ridge"] == "manual"
    assert statuses["golden_carousel"] == "breakdown"

    session.handle({"type": "reopen", "id": "thunder_ridge"})
    session.handle({"type": "pause"})
    session.tick(1.0)
    assert session.sim.now == session.sim.config.open_time
    session.handle({"type": "resume"})
    seen_guests = False
    while not session.done:
        session.tick(5.0)  # clamped to 1 s of wall time per tick
        f = session.frame_message()
        seen_guests |= bool(f["guests"])
    assert seen_guests
    after_close = {a["id"]: a for a in session.frame_message()["attractions"]}
    assert after_close["thunder_ridge"]["status"] == "closed"
    assert after_close["thunder_ridge"]["posted"] is None
    assert after_close["market_hall"]["status"] == "open"
    report = session.report_message()["report"]
    assert report["summary"]["attendance"] == 1500


def test_frame_positions_walking_guests_on_paths(layout):
    from parksim import SimConfig, Simulation

    sim = Simulation(Park.from_dict(layout), SimConfig(seed=2, attendance=3000))
    sim.advance(3600)
    data = frame(sim)
    guests = data["guests"]
    assert len(guests) % 4 == 0 and guests
    grid = sim.park.grid
    for i in range(0, len(guests), 4):
        x, y = guests[i + 1] / 10, guests[i + 2] / 10
        assert grid.in_bounds(int(x), int(y))
        assert 0 <= guests[i + 3] <= 9
    assert data["clock"] == "10:00"
    assert data["log"]
    assert frame(sim, log_from=len(sim.metrics.log))["log"] == []
