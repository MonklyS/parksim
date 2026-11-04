import random

from parksim.guest import Guest
from parksim.timeutil import HOUR, MINUTE, clock, parse_clock
from parksim.weather import Condition, WeatherForecast


def test_clock_helpers():
    assert clock(parse_clock("09:30")) == "09:30"
    assert clock(21 * HOUR + 5 * MINUTE) == "21:05"


def test_storm_overlay_and_rain_tail():
    w = WeatherForecast()
    w.add_storm(15 * HOUR, 60)
    assert w.condition_at(14 * HOUR) is Condition.CLEAR
    assert w.condition_at(15.5 * HOUR) is Condition.THUNDERSTORM
    assert w.condition_at(16 * HOUR + 10 * MINUTE) is Condition.RAIN
    assert w.condition_at(17 * HOUR) is Condition.CLEAR
    segs = w.segments(9 * HOUR, 21 * HOUR)
    assert [s.condition for s in segs] == [
        Condition.CLEAR,
        Condition.THUNDERSTORM,
        Condition.RAIN,
        Condition.CLEAR,
    ]


def test_temperature_peaks_mid_afternoon_and_storms_cool_it():
    w = WeatherForecast(high_temp_f=92)
    assert w.temperature_at(15 * HOUR) == 92
    assert w.temperature_at(9 * HOUR) < w.temperature_at(13 * HOUR)
    w.add_storm(15 * HOUR, 30)
    assert w.temperature_at(15 * HOUR) < 92


def test_generated_weather_is_deterministic_per_seed():
    a = WeatherForecast.generate(random.Random(3), 9 * HOUR, 21 * HOUR, storm_chance=1.0)
    b = WeatherForecast.generate(random.Random(3), 9 * HOUR, 21 * HOUR, storm_chance=1.0)
    assert a == b
    assert any(p.condition is Condition.THUNDERSTORM for p in a.periods)


def _guest(**kw) -> Guest:
    base = dict(
        id=1,
        arrived_at=0,
        leave_by=10 * HOUR,
        pos=(0, 0),
        thrill=0.5,
        patience_minutes=60,
        budget=100,
    )
    return Guest(**(base | kw))


def test_needs_grow_over_time_and_heat_makes_guests_thirstier():
    cool, hot = _guest(), _guest()
    cool.advance(2 * HOUR, heat=1.0)
    hot.advance(2 * HOUR, heat=1.5)
    assert cool.hunger > 0.2 and cool.energy < 1.0
    assert hot.thirst > cool.thirst


def test_unmet_urgent_needs_hurt_mood_and_effects_are_clamped():
    g = _guest(bladder=0.95, happiness=80)
    g.advance(HOUR)
    assert g.happiness < 80
    g.apply({"bladder": -5, "not_a_need": 1})
    assert g.bladder == 0.0
    g.mood(500)
    assert g.happiness == 100
