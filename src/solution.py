"""Application entry point.

Run the service with::

    python -m src.solution
    uvicorn src.solution:app --port 8000

The FastAPI application is built by the ``create_app`` factory in ``src.api``.
That module creates one canonical instance named ``app``; this module re-exports
the same object so ``uvicorn src.solution:app`` always resolves a module-level
attribute. ``main()`` serves that instance and honours ``$PORT`` (default 8000).
"""
from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from src.api import app, create_app
from src.config import get_settings

__all__ = ["app", "create_app", "main"]

# Re-exported explicitly for ASGI servers: the target is a module-level object,
# not something built lazily inside main().
assert isinstance(app, FastAPI)


def main() -> None:
    """Start the HTTP server on ``$PORT`` (default 8000)."""
    settings = get_settings()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
