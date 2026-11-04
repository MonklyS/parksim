"""ParkSim: a discrete-event theme park simulation engine.

The engine is pure Python with no third-party dependencies, so the same package can
run on a server (FastAPI) or in the browser (Pyodide) later.
"""

from .errors import ParkError
from .park import Park
from .simulation import SimConfig, Simulation

__all__ = ["Park", "ParkError", "SimConfig", "Simulation"]
__version__ = "0.1.0"
