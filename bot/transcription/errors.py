"""Safe domain errors for V2 transcription workflows."""

import httpx


_FALLBACK_ELIGIBLE_CODES = frozenset({
    "provider_timeout",
    "provider_network_error",
    "provider_rate_limited",
    "provider_server_error",
})
_PROVIDER_FAILURE_MESSAGE = "Il servizio AI non ha completato la richiesta."


class RevisionConflict(Exception):
    """The caller attempted to update a stale configuration revision."""


class ConfigurationInvalid(Exception):
    """A configuration or encrypted credential is invalid or unavailable."""


class TestRequired(Exception):
    """A matching successful test is required before activation."""

    __test__ = False


class PrerequisiteMissing(Exception):
    """One or more activation prerequisites are missing."""


class ServiceFailure(Exception):
    """A provider failure with a sanitized stage and user-facing message."""

    def __init__(self, code: str, stage: str, safe_message: str):
        super().__init__(safe_message)
        self.code = code
        self.stage = stage
        self.safe_message = safe_message

    @property
    def fallback_eligible(self) -> bool:
        """Whether the typed failure category permits a configured fallback."""
        return self.code in _FALLBACK_ELIGIBLE_CODES


def classify_provider_failure(exc: Exception, stage: str) -> ServiceFailure:
    """Map typed provider/network failures to safe, stable domain categories.

    Only timeouts, network failures, HTTP 429, and HTTP 5xx are fallback eligible.
    Provider response bodies and exception text are deliberately ignored.
    """
    if _is_timeout(exc):
        code = "provider_timeout"
    elif _is_network_failure(exc):
        code = "provider_network_error"
    else:
        status = _provider_status_code(exc)
        if status == 408:
            code = "provider_timeout"
        elif status == 429:
            code = "provider_rate_limited"
        elif status is not None and 500 <= status <= 599:
            code = "provider_server_error"
        else:
            code = "provider_error"
    return ServiceFailure(code, stage, _PROVIDER_FAILURE_MESSAGE)


def _is_timeout(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return True
    try:
        from openai import APITimeoutError
    except ImportError:
        return False
    return isinstance(exc, APITimeoutError)


def _is_network_failure(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, httpx.RequestError)):
        return True
    try:
        from openai import APIConnectionError
    except ImportError:
        return False
    return isinstance(exc, APIConnectionError)


def _provider_status_code(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    try:
        from openai import APIStatusError
    except ImportError:
        APIStatusError = ()
    try:
        from google.genai.errors import APIError
    except ImportError:
        APIError = ()

    if APIStatusError and isinstance(exc, APIStatusError):
        status = exc.status_code
    elif APIError and isinstance(exc, APIError):
        status = exc.code
    else:
        return None
    return status if isinstance(status, int) else None
