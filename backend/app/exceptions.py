"""Domain exception hierarchy.

Services raise these; the API layer maps them to HTTP status codes in one
place (api/errors.py). This keeps business logic free of HTTP concerns and
stops status codes from being scattered across route handlers.
"""

from __future__ import annotations


class AppError(Exception):
    """Base for every error this application raises deliberately."""

    def __init__(self, message: str, *, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail


class NotFoundError(AppError):
    """A requested resource does not exist (or isn't visible to this caller)."""


class ConflictError(AppError):
    """The request collides with existing state (e.g. duplicate email)."""


class ValidationError(AppError):
    """The request is well-formed but semantically invalid."""


class AuthenticationError(AppError):
    """Credentials are missing or wrong."""


class PermissionError_(AppError):
    """Authenticated, but not allowed to touch this resource."""


class FilingNotReadyError(ConflictError):
    """The filing exists but hasn't finished ingesting."""


class LLMError(AppError):
    """The configured LLM provider could not be reached or returned an error."""


class LLMNotConfiguredError(LLMError):
    """No API key is configured, so generation cannot run at all."""


class LLMRateLimitedError(LLMError):
    """The provider's usage limit is exhausted.

    Distinct from a generic LLMError because it is temporary, is nobody's
    bug, and the user can act on it (wait, or raise their plan) - so the UI
    says so plainly instead of showing a generic failure.
    """

    def __init__(self, message: str, *, detail: str | None = None, retry_after: float | None = None):
        super().__init__(message, detail=detail)
        self.retry_after = retry_after


class IngestionError(AppError):
    """A filing could not be parsed or indexed."""
