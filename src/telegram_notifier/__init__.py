"""Send Claude's Markdown output to Telegram.

Quick start::

    from telegram_notifier import notify

    notify("## Done\\n\\n- 42 files changed\\n- `pytest` green")

Or keep a client around when you send more than once::

    from telegram_notifier import TelegramNotifier

    with TelegramNotifier() as tg:
        tg.send("Starting backup...", level="info")
        tg.send_document("backup.log", caption="Full log")
"""

from __future__ import annotations

from .client import AsyncTelegramNotifier, TelegramNotifier, notify
from .config import Config
from .errors import ConfigError, NotifierError, RetryExhausted, TelegramAPIError
from .formatting import escape
from .markdown import markdown_to_blocks, markdown_to_telegram
from .splitting import MESSAGE_LIMIT, pack_blocks, split_text

__version__ = "0.1.0"

__all__ = [
    "MESSAGE_LIMIT",
    "AsyncTelegramNotifier",
    "Config",
    "ConfigError",
    "NotifierError",
    "RetryExhausted",
    "TelegramAPIError",
    "TelegramNotifier",
    "escape",
    "markdown_to_blocks",
    "markdown_to_telegram",
    "notify",
    "pack_blocks",
    "split_text",
]
