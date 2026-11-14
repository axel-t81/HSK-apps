class DigestError(RuntimeError):
    """Expected operational error with a user-readable message."""


class ConfigurationError(DigestError):
    """Invalid or missing local configuration."""


class FetchError(DigestError):
    """A public source could not be fetched safely."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class SourceError(DigestError):
    """A source response did not contain the expected public data."""


class SummaryError(DigestError):
    """The model could not produce a safe, valid digest."""


class StateError(DigestError):
    """Persistent run state prevented an unsafe operation."""
