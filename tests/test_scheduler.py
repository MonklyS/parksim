from parksim.scheduler import Scheduler


def test_events_run_in_time_order_with_stable_ties():
    s = Scheduler()
    seen = []
    s.at(5, seen.append, "b")
    s.at(1, seen.append, "a")
    s.at(5, seen.append, "c")
    s.run_until(10)
    assert seen == ["a", "b", "c"]
    assert s.now == 10
    assert s.processed == 3


def test_run_until_leaves_future_events_and_never_schedules_in_past():
    s = Scheduler(start=100)
    seen = []
    s.at(50, seen.append, "past")  # clamped to now
    s.after(30, seen.append, "later")
    s.run_until(110)
    assert seen == ["past"]
    assert len(s) == 1
    s.run_until(200)
    assert seen == ["past", "later"]


def test_events_can_schedule_more_events():
    s = Scheduler()
    ticks = []

    def tick():
        ticks.append(s.now)
        if s.now < 30:
            s.after(10, tick)

    s.at(0, tick)
    s.run_until(100)
    assert ticks == [0, 10, 20, 30]
