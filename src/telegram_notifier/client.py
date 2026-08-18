"""The Telegram client: retries, rate limits, and a plain-text safety net."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import random
import time
from pathlib import Path
from typing import Any, Literal, Self

import httpx

from .config import Config
from .errors import RetryExhausted, TelegramAPIError
from .formatting import get_formatter, strip_markup
from .markdown import markdown_to_blocks
from .splitting import CAPTION_LIMIT, MESSAGE_LIMIT, pack_blocks

log = logging.getLogger("telegram_notifier")

Level = Literal["info", "success", "warning", "error", "debug"]

LEVEL_EMOJI: dict[str, str] = {
    "info": "ℹ️",
    "success": "✅",
    "warning": "⚠️",
    "error": "❌",
    "debug": "🐛",
}

# Errors worth trying again: Telegram hiccups and transport failures.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def render_document(
    text: str,
    *,
    markdown: bool = True,
    parse_mode: str | None = "HTML",
    title: str | None = None,
    level: Level | None = None,
    limit: int = MESSAGE_LIMIT,
) -> list[str]:
    """Turn a document into a list of ready-to-send message bodies."""
    formatter = get_formatter(parse_mode)
    text, title = _decorate(text, title, level)

    if markdown:
        blocks = markdown_to_blocks(text, parse_mode)
    else:
        blocks = [formatter.text(part) for part in text.split("\n\n") if part.strip()]
    if title:
        blocks.insert(0, formatter.bold(formatter.text(title)))
    if not blocks:
        return []
    return pack_blocks(blocks, limit=limit, parse_mode=parse_mode)


def _decorate(text: str, title: str | None, level: Level | None) -> tuple[str, str | None]:
    """Attach the level emoji to the title, or to the text when there is none."""
    if not level:
        return text, title
    emoji = LEVEL_EMOJI.get(level, "")
    if title:
        return text, f"{emoji} {title}".strip()
    return f"{emoji} {text}".lstrip(), None


class _Base:
    """Everything the sync and async clients agree on."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.from_env()

    # -- request shaping ---------------------------------------------------

    def _url(self, method: str) -> str:
        return f"{self.config.api_url}/{method}"

    def _message_payload(
        self,
        text: str,
        *,
        parse_mode: str | None,
        silent: bool | None,
        preview: bool | None,
        chat_id: str | None,
        thread_id: int | None,
        reply_to: int | None,
    ) -> dict[str, Any]:
        cfg = self.config
        payload: dict[str, Any] = {
            "chat_id": chat_id or cfg.chat_id,
            "text": text,
            "disable_notification": cfg.disable_notification if silent is None else silent,
            "link_preview_options": {
                "is_disabled": cfg.disable_web_page_preview if preview is None else not preview
            },
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        thread = thread_id if thread_id is not None else cfg.message_thread_id
        if thread is not None:
            payload["message_thread_id"] = thread
        if reply_to is not None:
            payload["reply_parameters"] = {"message_id": reply_to}
        return payload

    # -- retry policy ------------------------------------------------------

    def _interpret(
        self, response: httpx.Response, method: str
    ) -> tuple[Any, TelegramAPIError | None]:
        """Return ``(result, None)`` on success or ``(None, error)`` on failure."""
        try:
            body = response.json()
        except ValueError:
            body = {}

        if response.is_success and body.get("ok"):
            return body.get("result"), None

        parameters = body.get("parameters") or {}
        error = TelegramAPIError(
            body.get("description") or f"HTTP {response.status_code}",
            status_code=response.status_code,
            error_code=body.get("error_code"),
            retry_after=parameters.get("retry_after"),
            method=method,
        )
        return None, error

    def _should_retry(self, error: TelegramAPIError) -> bool:
        return error.status_code in _RETRYABLE_STATUS

    def _delay_for(self, error: TelegramAPIError | None, attempt: int) -> float:
        """How long to wait before attempt ``attempt + 1``."""
        if error is not None and error.retry_after:
            # Telegram told us exactly how long to wait; add a small cushion.
            return min(float(error.retry_after) + 0.5, self.config.max_retry_after)
        backoff = self.config.backoff_base * (2 ** (attempt - 1))
        return min(backoff, self.config.backoff_max) * (0.5 + random.random())

    def _document_files(
        self, document: str | Path | bytes, filename: str | None
    ) -> dict[str, tuple[str, bytes, str]]:
        if isinstance(document, bytes):
            name = filename or "message.txt"
            data = document
        else:
            path = Path(document)
            name = filename or path.name
            data = path.read_bytes()
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return {"document": (name, data, content_type)}


class TelegramNotifier(_Base):
    """Send Markdown notifications to Telegram.

    >>> with TelegramNotifier() as notifier:
    ...     notifier.send("## Backup finished\\n\\n- 12 GB\\n- 4m 21s")
    """

    def __init__(self, config: Config | None = None, *, client: httpx.Client | None = None) -> None:
        super().__init__(config)
        self._client = client or httpx.Client(timeout=self.config.timeout)
        self._owns_client = client is None
        self._last_send = 0.0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- public API --------------------------------------------------------

    def send(
        self,
        text: str,
        *,
        title: str | None = None,
        level: Level | None = None,
        markdown: bool = True,
        parse_mode: str | None = None,
        silent: bool | None = None,
        preview: bool | None = None,
        chat_id: str | None = None,
        thread_id: int | None = None,
        reply_to: int | None = None,
    ) -> list[dict[str, Any]]:
        """Send ``text`` (Markdown by default), splitting it if it is too long.

        Returns the raw Telegram ``Message`` object for every part sent.
        """
        mode = parse_mode if parse_mode is not None else self.config.parse_mode
        bodies = render_document(text, markdown=markdown, parse_mode=mode, title=title, level=level)
        results = []
        for index, body in enumerate(bodies):
            if index:
                self._pace()
            results.append(
                self._send_one(body, mode, silent, preview, chat_id, thread_id, reply_to)
            )
        return results

    def send_document(
        self,
        document: str | Path | bytes,
        *,
        filename: str | None = None,
        caption: str | None = None,
        title: str | None = None,
        level: Level | None = None,
        markdown: bool = True,
        parse_mode: str | None = None,
        silent: bool | None = None,
        chat_id: str | None = None,
        thread_id: int | None = None,
    ) -> dict[str, Any]:
        """Upload a file - the right move for logs, diffs and long transcripts."""
        mode = parse_mode if parse_mode is not None else self.config.parse_mode
        cfg = self.config
        data: dict[str, Any] = {
            "chat_id": chat_id or cfg.chat_id,
            "disable_notification": str(
                cfg.disable_notification if silent is None else silent
            ).lower(),
        }
        thread = thread_id if thread_id is not None else cfg.message_thread_id
        if thread is not None:
            data["message_thread_id"] = str(thread)
        if caption or title:
            # Captions cap at 1024 chars; keep the first part and drop the rest
            # rather than sending a second, captionless message.
            rendered = render_document(
                caption or "",
                markdown=markdown,
                parse_mode=mode,
                title=title,
                level=level,
                limit=CAPTION_LIMIT,
            )
            if rendered:
                data["caption"] = rendered[0]
                if mode:
                    data["parse_mode"] = mode

        return self._request(
            "sendDocument", data=data, files=self._document_files(document, filename)
        )

    def get_me(self) -> dict[str, Any]:
        """The bot's own profile - the cheapest way to validate a token."""
        return self._request("getMe")

    def get_updates(self, *, limit: int = 100, timeout: int = 0) -> list[dict[str, Any]]:
        """Recent updates. Used by ``tg-notify chat-id`` to discover chat ids."""
        return self._request("getUpdates", json={"limit": limit, "timeout": timeout})

    # -- internals ---------------------------------------------------------

    def _send_one(
        self,
        body: str,
        mode: str | None,
        silent: bool | None,
        preview: bool | None,
        chat_id: str | None,
        thread_id: int | None,
        reply_to: int | None,
    ) -> dict[str, Any]:
        payload = self._message_payload(
            body,
            parse_mode=mode,
            silent=silent,
            preview=preview,
            chat_id=chat_id,
            thread_id=thread_id,
            reply_to=reply_to,
        )
        try:
            return self._request("sendMessage", json=payload)
        except TelegramAPIError as error:
            if not error.is_parse_error:
                raise
            # Never lose a notification to a formatting bug: resend as plain text.
            log.warning("telegram rejected our formatting (%s); resending as plain text", error)
            payload["text"] = strip_markup(body, mode)
            payload.pop("parse_mode", None)
            return self._request("sendMessage", json=payload)

    def _pace(self) -> None:
        """Keep under Telegram's ~1 message/second per-chat limit."""
        wait = self._last_send + self.config.send_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def _request(self, method: str, **kwargs: Any) -> Any:
        last_error: Exception | None = None

        for attempt in range(1, self.config.max_attempts + 1):
            error: TelegramAPIError | None = None
            try:
                response = self._client.post(self._url(method), **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                log.debug("%s attempt %d: transport error: %s", method, attempt, exc)
            else:
                result, error = self._interpret(response, method)
                self._last_send = time.monotonic()
                if error is None:
                    return result
                last_error = error
                if not self._should_retry(error):
                    raise error
                log.debug("%s attempt %d: %s", method, attempt, error)

            if attempt < self.config.max_attempts:
                time.sleep(self._delay_for(error, attempt))

        assert last_error is not None
        raise RetryExhausted(method, self.config.max_attempts, last_error)


class AsyncTelegramNotifier(_Base):
    """Async twin of :class:`TelegramNotifier`, for asyncio schedulers."""

    def __init__(
        self, config: Config | None = None, *, client: httpx.AsyncClient | None = None
    ) -> None:
        super().__init__(config)
        self._client = client or httpx.AsyncClient(timeout=self.config.timeout)
        self._owns_client = client is None
        self._last_send = 0.0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send(
        self,
        text: str,
        *,
        title: str | None = None,
        level: Level | None = None,
        markdown: bool = True,
        parse_mode: str | None = None,
        silent: bool | None = None,
        preview: bool | None = None,
        chat_id: str | None = None,
        thread_id: int | None = None,
        reply_to: int | None = None,
    ) -> list[dict[str, Any]]:
        mode = parse_mode if parse_mode is not None else self.config.parse_mode
        bodies = render_document(text, markdown=markdown, parse_mode=mode, title=title, level=level)
        results = []
        for index, body in enumerate(bodies):
            if index:
                await self._pace()
            payload = self._message_payload(
                body,
                parse_mode=mode,
                silent=silent,
                preview=preview,
                chat_id=chat_id,
                thread_id=thread_id,
                reply_to=reply_to,
            )
            try:
                results.append(await self._request("sendMessage", json=payload))
            except TelegramAPIError as error:
                if not error.is_parse_error:
                    raise
                log.warning("telegram rejected our formatting (%s); resending as plain text", error)
                payload["text"] = strip_markup(body, mode)
                payload.pop("parse_mode", None)
                results.append(await self._request("sendMessage", json=payload))
        return results

    async def send_document(
        self,
        document: str | Path | bytes,
        *,
        filename: str | None = None,
        caption: str | None = None,
        title: str | None = None,
        markdown: bool = True,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict[str, Any]:
        mode = parse_mode if parse_mode is not None else self.config.parse_mode
        data: dict[str, Any] = {"chat_id": chat_id or self.config.chat_id}
        if caption or title:
            rendered = render_document(
                caption or "",
                markdown=markdown,
                parse_mode=mode,
                title=title,
                limit=CAPTION_LIMIT,
            )
            if rendered:
                data["caption"] = rendered[0]
                if mode:
                    data["parse_mode"] = mode
        return await self._request(
            "sendDocument", data=data, files=self._document_files(document, filename)
        )

    async def get_me(self) -> dict[str, Any]:
        return await self._request("getMe")

    async def _pace(self) -> None:
        wait = self._last_send + self.config.send_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

    async def _request(self, method: str, **kwargs: Any) -> Any:
        last_error: Exception | None = None

        for attempt in range(1, self.config.max_attempts + 1):
            error: TelegramAPIError | None = None
            try:
                response = await self._client.post(self._url(method), **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                log.debug("%s attempt %d: transport error: %s", method, attempt, exc)
            else:
                result, error = self._interpret(response, method)
                self._last_send = time.monotonic()
                if error is None:
                    return result
                last_error = error
                if not self._should_retry(error):
                    raise error
                log.debug("%s attempt %d: %s", method, attempt, error)

            if attempt < self.config.max_attempts:
                await asyncio.sleep(self._delay_for(error, attempt))

        assert last_error is not None
        raise RetryExhausted(method, self.config.max_attempts, last_error)


def notify(text: str, **kwargs: Any) -> list[dict[str, Any]]:
    """One-shot helper: build a client from the environment, send, close.

    >>> notify("Nightly job finished", title="cron")
    """
    with TelegramNotifier() as notifier:
        return notifier.send(text, **kwargs)
