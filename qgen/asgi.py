"""The ASGI entry point, for a server that expects `module:app`.

`python -m qgen serve` builds the application itself and is the way to run it locally. A
deployment usually wants a module-level object instead:

    uvicorn qgen.asgi:app --host 0.0.0.0 --port $PORT

The application is built at import time, so a missing database URL fails the deploy on startup
rather than on the first request somebody makes.
"""

from __future__ import annotations

from .web import create_app

app = create_app()
