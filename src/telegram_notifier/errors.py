"""Exception hierarchy for the notifier."""

from __future__ import annotations


class NotifierError(Exception):
    """Base class for every error raised by this package."""


class ConfigError(NotifierError):
    """The bot token / chat id could not be resolved."""


class TelegramAPIError(NotifierError):
    """Telegram answered, but said no.

    ``retry_after`` is set when Telegram rate limited us (HTTP 429) and told
    us how long to wait.
    """

    def __init__(
        self,
        description: str,
        *,
        status_code: int | None = None,
        error_code: int | None = None,
        retry_after: float | None = None,
        method: str | None = None,
    ) -> None:
        super().__init__(description)
        self.description = description
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after = retry_after
        self.method = method

    @property
    def is_parse_error(self) -> bool:
        """True when Telegram rejected our formatting rather than our request."""
        text = self.description.lower()
        return "parse entities" in text or "unsupported start tag" in text

    def __str__(self) -> str:
        parts = [self.description]
        if self.method:
            parts.append(f"method={self.method}")
        if self.status_code is not None:
            parts.append(f"http={self.status_code}")
        if self.error_code is not None:
            parts.append(f"code={self.error_code}")
        return " ".join(parts) if len(parts) == 1 else f"{parts[0]} ({', '.join(parts[1:])})"


class RetryExhausted(NotifierError):
    """Every attempt failed with a retryable error."""

    def __init__(self, method: str, attempts: int, last_error: Exception) -> None:
        super().__init__(f"{method} failed after {attempts} attempt(s): {last_error}")
        self.method = method
        self.attempts = attempts
        self.last_error = last_error
