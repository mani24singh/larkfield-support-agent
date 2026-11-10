"""Application entry point.

Run the service with::

    python -m src.solution
    uvicorn src.solution:app --port 8000

The module exposes ``app`` (FastAPI) for ASGI servers and ``main()`` which
honours the ``PORT`` environment variable, defaulting to 8000.
"""
from __future__ import annotations

import uvicorn

from src.api import create_app
from src.config import get_settings

app = create_app()


def main() -> None:
    """Start the HTTP server on ``$PORT`` (default 8000)."""
    settings = get_settings()
    uvicorn.run(
        "src.solution:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
