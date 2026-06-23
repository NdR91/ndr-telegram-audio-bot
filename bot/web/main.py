#!/usr/bin/env python3
"""
Web frontend entry point for the Telegram Audio Bot control plane.

Starts the Uvicorn HTTP server with the FastAPI application.  The web
server manages the Telegram bot lifecycle through the RuntimeManager and
serves the administration frontend.

Usage
-----
Direct Python::

    python -m bot.web.main

Via uvicorn (advanced)::

    uvicorn bot.web.app:create_app --factory --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os

import uvicorn

HOST = os.getenv("WEB_HOST", "0.0.0.0")
PORT = int(os.getenv("WEB_PORT", "8080"))


def main() -> None:
    uvicorn.run(
        "bot.web.app:create_app",
        factory=True,
        host=HOST,
        port=PORT,
        log_level=os.getenv("WEB_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
