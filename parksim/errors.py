class ParkError(ValueError):
    """Raised when a park layout is invalid (overlaps, bad entrances, unknown templates...)."""
