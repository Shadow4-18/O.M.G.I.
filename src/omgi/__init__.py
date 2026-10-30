"""O.M.G.I. — Ophelia Mobile Game Integration.

Lets an agent play mobile games through the game itself instead of through
pixels: a mod or bridge inside the game speaks a small JSON protocol, and
this package is the client, the per-game renderers, and the patch tooling.
"""

from omgi.client import Client, OMGIError
from omgi.protocol import PROTOCOL_VERSION
from omgi.render import render_state

__version__ = "0.1.0"

__all__ = ["Client", "OMGIError", "PROTOCOL_VERSION", "render_state", "__version__"]
