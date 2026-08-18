"""Configuration, loaded from the environment / a .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

from .errors import ConfigError

ParseMode = Literal["HTML", "MarkdownV2"]

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off", ""}


@dataclass(frozen=True, slots=True)
class Config:
    """Everything the notifier needs to talk to Telegram."""

    bot_token: str
    chat_id: str
    message_thread_id: int | None = None
    parse_mode: ParseMode = "HTML"
    disable_notification: bool = False
    disable_web_page_preview: bool = True

    # Networking / retry behaviour.
    timeout: float = 15.0
    max_attempts: int = 5
    backoff_base: float = 0.5
    backoff_max: float = 30.0
    max_retry_after: float = 120.0
    send_interval: float = 0.5

    api_base: str = "https://api.telegram.org"

    def __post_init__(self) -> None:
        if not self.bot_token:
            raise ConfigError(
                "TELEGRAM_BOT_TOKEN is empty. Get one from @BotFather and put it in .env"
            )
        if not self.chat_id:
            raise ConfigError(
                "TELEGRAM_CHAT_ID is empty. Run `tg-notify chat-id` to discover yours."
            )
        if self.parse_mode not in ("HTML", "MarkdownV2"):
            raise ConfigError(
                f"Unsupported parse mode {self.parse_mode!r}; use 'HTML' or 'MarkdownV2'."
            )
        if self.max_attempts < 1:
            raise ConfigError("max_attempts must be at least 1.")

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = None,
        *,
        override: bool = False,
        **overrides: Any,
    ) -> Config:
        """Build a config from ``.env`` + the process environment.

        Real environment variables win over the ``.env`` file (unless
        ``override=True``), and explicit keyword ``overrides`` win over both.
        """
        _load_env_file(env_file, override=override)

        values: dict[str, Any] = {
            "bot_token": _str("TELEGRAM_BOT_TOKEN", ""),
            "chat_id": _str("TELEGRAM_CHAT_ID", ""),
            "message_thread_id": _optional_int("TELEGRAM_MESSAGE_THREAD_ID"),
            "parse_mode": _str("TELEGRAM_PARSE_MODE", "HTML").strip() or "HTML",
            "disable_notification": _bool("TELEGRAM_DISABLE_NOTIFICATION", False),
            "disable_web_page_preview": _bool("TELEGRAM_DISABLE_WEB_PAGE_PREVIEW", True),
            "timeout": _float("TELEGRAM_TIMEOUT", 15.0),
            "max_attempts": _int("TELEGRAM_MAX_ATTEMPTS", 5),
            "send_interval": _float("TELEGRAM_SEND_INTERVAL", 0.5),
            "api_base": _str("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/"),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    def evolve(self, **changes: Any) -> Config:
        """Return a copy with ``changes`` applied, ignoring ``None`` values."""
        return replace(self, **{k: v for k, v in changes.items() if v is not None})

    @property
    def api_url(self) -> str:
        return f"{self.api_base}/bot{self.bot_token}"

    def redacted_token(self) -> str:
        """The token with its secret half masked, safe to print."""
        bot_id, _, secret = self.bot_token.partition(":")
        if len(secret) < 12:  # too short to reveal any of it safely
            return f"{bot_id}:***" if secret else "***"
        return f"{bot_id}:{secret[:3]}...{secret[-3:]}"


def _load_env_file(env_file: str | Path | None, *, override: bool) -> None:
    if env_file is not None:
        path = Path(env_file)
        if not path.exists():
            raise ConfigError(f"env file not found: {path}")
        load_dotenv(path, override=override)
        return
    # Walk up from the CWD so the CLI works from anywhere inside the project.
    load_dotenv(override=override)


def _str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


def _optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return _int(name, 0)


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
