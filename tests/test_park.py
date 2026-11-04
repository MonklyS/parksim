import copy

import pytest

from parksim import Park, ParkError
from parksim.attractions import Category, Ride
from parksim.grid import UNREACHABLE, Grid


def test_distance_field_and_route_on_an_l_shaped_path():
    g = Grid(5, 5)
    g.add_path_line(0, 0, 4, 0)
    g.add_path_line(4, 0, 4, 4)
    field = g.distance_field((4, 4))
    assert field[g.index(0, 0)] == 8
    assert field[g.index(2, 2)] == UNREACHABLE  # not a path tile
    route = g.route(field, (0, 0))
    assert route[0] == (0, 0) and route[-1] == (4, 4)
    assert len(route) == 9


def test_paths_must_be_straight_and_in_bounds():
    g = Grid(5, 5)
    with pytest.raises(ParkError):
        g.add_path_line(0, 0, 2, 2)
    with pytest.raises(ParkError):
        g.add_path_line(0, 0, 9, 0)


def test_sample_park_loads_with_no_design_warnings(sample_park):
    assert len(sample_park.attractions) == 21
    assert sample_park.warnings() == []
    for a in sample_park.attractions.values():
        assert sample_park.distance(a.id, sample_park.entrance) > 0


def test_round_trip_preserves_layout(sample_park):
    clone = Park.from_dict(sample_park.to_dict())
    assert clone.to_dict() == sample_park.to_dict()


def test_template_values_and_overrides(tiny_park_data):
    data = copy.deepcopy(tiny_park_data)
    data["attractions"][0]["overrides"] = {"capacity": 30, "cycle_seconds": 60}
    ride = Park.from_dict(data).attractions["coaster"]
    assert isinstance(ride, Ride)
    assert ride.category is Category.RIDE
    assert ride.throughput_per_hour == 1800


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"entrance": [3, 4]}, "must be on a path"),
        ({"entrance": [10, 2]}, "must touch"),
        ({"x": 6, "entrance": [6, 2]}, "overlaps"),
        ({"template": "time_machine"}, "unknown template"),
        ({"overrides": {"warp_speed": 9}}, "invalid override"),
    ],
)
def test_invalid_placements_are_rejected(tiny_park_data, change, message):
    data = copy.deepcopy(tiny_park_data)
    data["attractions"][0].update(change)
    with pytest.raises(ParkError, match=message):
        Park.from_dict(data)


def test_duplicate_ids_and_missing_fields_are_rejected(tiny_park_data):
    data = copy.deepcopy(tiny_park_data)
    data["attractions"].append(dict(data["attractions"][1], x=10, y=3, entrance=[10, 2]))
    with pytest.raises(ParkError, match="duplicate"):
        Park.from_dict(data)
    with pytest.raises(ParkError):
        Park.from_dict({"name": "x"})


def test_warnings_flag_missing_services_and_unreachable_buildings(tiny_park_data):
    data = copy.deepcopy(tiny_park_data)
    data["attractions"] = [a for a in data["attractions"] if a["id"] == "coaster"]
    data["paths"].append([11, 0, 11, 0])  # isolated path tile
    data["attractions"].append(
        {"id": "island", "template": "snack_cart", "x": 10, "y": 0, "entrance": [11, 0]}
    )
    park = Park.from_dict(data)
    warnings = "\n".join(park.warnings())
    assert "cannot be reached" in warnings
    assert "no restrooms" in warnings
    assert "First Aid" in warnings


def test_remove_attraction_frees_its_tiles(tiny_park_data):
    park = Park.from_dict(tiny_park_data)
    park.remove_attraction("wc")
    assert "wc" not in park.attractions
    assert park.grid.owner(7, 3) is None
    with pytest.raises(ParkError):
        park.remove_attraction("wc")


def test_save_and_load(tmp_path, sample_park):
    path = tmp_path / "park.json"
    sample_park.save(path)
    assert Park.load(path).to_dict() == sample_park.to_dict()
