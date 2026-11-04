"""HTTP + WebSocket API for ParkSim: save/load park layouts and stream live simulations."""

from .app import create_app

__all__ = ["create_app"]
