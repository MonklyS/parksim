"""Simulation time is measured in seconds since midnight of the simulated day."""

MINUTE = 60.0
HOUR = 3600.0


def clock(t: float) -> str:
    """Format seconds-since-midnight as HH:MM."""
    total = int(t)
    hours, rem = divmod(total, 3600)
    return f"{hours:02d}:{rem // 60:02d}"


def parse_clock(value: str) -> float:
    """Parse HH:MM into seconds since midnight."""
    hours, minutes = value.split(":")
    return int(hours) * HOUR + int(minutes) * MINUTE
