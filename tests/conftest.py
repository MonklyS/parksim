from pathlib import Path

import pytest

from parksim import Park

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample_park.json"


@pytest.fixture
def sample_park() -> Park:
    return Park.load(SAMPLE)


@pytest.fixture
def tiny_park_data() -> dict:
    """A straight path with one ride, one restroom, one food stand and First Aid."""
    return {
        "name": "Tiny",
        "width": 12,
        "height": 5,
        "entrance": [0, 2],
        "paths": [[0, 2, 11, 2]],
        "attractions": [
            {
                "id": "coaster",
                "template": "coaster_family",
                "x": 2,
                "y": 3,
                "w": 4,
                "h": 2,
                "entrance": [3, 2],
            },
            {"id": "wc", "template": "restroom", "x": 7, "y": 3, "entrance": [7, 2]},
            {
                "id": "food",
                "template": "quick_service",
                "x": 2,
                "y": 0,
                "w": 4,
                "h": 2,
                "entrance": [4, 2],
            },
            {"id": "aid", "template": "first_aid", "x": 9, "y": 0, "entrance": [9, 2]},
        ],
    }
