"""Stable FastAPI application factory used by the V2 web entrypoint."""


def create_app(settings=None, services=None):
    from bot.web.v2_app import create_app as create_v2_app

    return create_v2_app(settings=settings, services=services)
