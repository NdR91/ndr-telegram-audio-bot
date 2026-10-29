#!/usr/bin/env python3
"""Start the V2 FastAPI application with Uvicorn."""

from __future__ import annotations

import uvicorn
from bot.deployment import DeploymentSettings


def main() -> None:
    settings = DeploymentSettings.from_environment()
    uvicorn.run(
        "bot.web.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
