"""
Web frontend package for the Telegram Audio Bot control plane.

Provides a FastAPI application with server-rendered Jinja2 templates,
session-based authentication, CSRF protection, and integration with the
RuntimeManager for Telegram bot lifecycle management.

Routes
------
- ``/`` — auto-redirect to setup or dashboard
- ``/setup`` — setup wizard (code + admin creation)
- ``/login`` / ``/logout`` — authentication
- ``/admin/*`` — administration pages
- ``/api/*`` — JSON API endpoints
"""

from bot.web.app import create_app

__all__ = ["create_app"]
