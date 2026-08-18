"""An MCP server wrapping the notifier, so agents can reach Telegram.

Why this exists: an agent's shell is often a sandbox with an allow-listed
network, and ``api.telegram.org`` is not on that list -- so ``uv run tg-notify``
cannot work from there no matter how correct it is. MCP servers are spawned by
the client on the *host*, with the host's Python and the host's network, which
is the one path out.

Run it over stdio::

    uv run --extra mcp tg-notify-mcp

The tool surface is deliberately the CLI's, minus the parts an agent has no
business setting: one send, one chat, one direction.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .client import Level, TelegramNotifier
from .config import Config
from .errors import ConfigError, NotifierError

# The package lives at <project>/src/telegram_notifier/mcp_server.py, so the
# project root -- and its .env -- is three parents up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def load_config(env_file: str | Path | None = None) -> Config:
    """Resolve configuration, preferring an explicit path over the CWD.

    A stdio server is spawned by the MCP client, not by a shell, so its working
    directory is whatever the client happened to choose. ``Config.from_env()``
    walks up from the CWD looking for ``.env``; starting from ``/`` that finds
    nothing. We therefore point at the ``.env`` beside this package.
    ``TELEGRAM_ENV_FILE`` overrides it, and real environment variables still win
    over the file, so supplying secrets through the MCP client's ``env`` block
    instead of a file keeps working.
    """
    candidate = env_file or os.environ.get("TELEGRAM_ENV_FILE") or DEFAULT_ENV_FILE
    path = Path(candidate)
    if path.exists():
        return Config.from_env(path)
    # A missing file is not an error: the client may have supplied the variables
    # directly, and Config will complain clearly enough if it did not.
    return Config.from_env()


def build_server(config: Config | None = None) -> Any:
    """Create the MCP server.

    ``config`` is resolved per call rather than captured here, so a fixed
    ``.env`` typo can be corrected without restarting the client -- except when
    a config is passed explicitly, which is what the tests do.
    """
    try:
        from mcp.server.mcpserver import MCPServer
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
        raise ConfigError("The MCP server needs the 'mcp' extra: uv sync --extra mcp") from exc

    mcp = MCPServer(
        "telegram-notifier",
        instructions=(
            "Sends a Telegram notification to the user's own chat. Use it to "
            "report the result of a long-running or scheduled task. Send one "
            "message per task rather than a running commentary."
        ),
    )

    @mcp.tool()
    def send_telegram_notification(
        text: str,
        title: str | None = None,
        level: Level | None = None,
        silent: bool = False,
    ) -> str:
        """Send a notification to the user's Telegram chat.

        The text is GitHub-flavoured Markdown -- headings, lists, tables, fenced
        code -- and is converted to what Telegram accepts, then split if it runs
        past the 4096-character limit. Formatting failures fall back to plain
        text rather than dropping the message.

        Args:
            text: Message body, as Markdown.
            title: Optional bold heading placed above the body.
            level: Prefixes an emoji to mark the outcome at a glance.
            silent: Deliver without a sound. Right for a scheduled digest,
                wrong for an alert that needs to be seen now.
        """
        # `level` needs no check: it is a Literal, so the generated schema
        # rejects anything else before this body runs. `text` does, because
        # "whitespace only" is not something a schema can express.
        if not text.strip():
            raise ValueError("Refusing to send an empty notification.")

        with TelegramNotifier(config or load_config()) as notifier:
            try:
                sent = notifier.send(text, title=title, level=level, silent=silent)
            except NotifierError as exc:
                # Surface the notifier's own diagnosis: it distinguishes a bad
                # token from a bad chat id from Telegram being down, and the
                # caller can act on the difference.
                raise ValueError(f"Telegram send failed: {exc}") from exc

        return f"Sent {len(sent)} message{'s' if len(sent) != 1 else ''} to Telegram."

    @mcp.tool()
    def check_telegram_connection() -> str:
        """Verify the bot token and chat id without sending a message."""
        with TelegramNotifier(config or load_config()) as notifier:
            try:
                me = notifier.get_me()
            except NotifierError as exc:
                raise ValueError(f"Telegram check failed: {exc}") from exc
            return (
                f"Connected as @{me.get('username', '?')} "
                f"(token {notifier.config.redacted_token()}, "
                f"chat {notifier.config.chat_id})."
            )

    return mcp


def main() -> None:
    """Entry point for ``tg-notify-mcp``."""
    build_server().run()


if __name__ == "__main__":
    main()
