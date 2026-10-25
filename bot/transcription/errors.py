"""Safe domain errors for V2 transcription workflows."""


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
