"""``tg-notify`` - pipe anything into Telegram.

tg-notify "Deploy finished"
claude -p "summarise today's commits" | tg-notify --title "Daily digest"
pytest 2>&1 | tg-notify --code --title "Test run" --level error
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .client import LEVEL_EMOJI, TelegramNotifier, render_document
from .config import Config
from .errors import ConfigError, NotifierError

EXIT_OK = 0
EXIT_SEND_FAILED = 1
EXIT_CONFIG_ERROR = 2

COMMANDS = ("send", "doctor", "chat-id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tg-notify",
        description="Send Markdown notifications to Telegram.",
        epilog=(
            "Commands: send (default), doctor, chat-id.\n"
            "Message text comes from the argument, --file, or stdin."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("message", nargs="?", help="message text; omit to read stdin")
    parser.add_argument("-t", "--title", help="bold heading prepended to the message")
    parser.add_argument(
        "-l", "--level", choices=sorted(LEVEL_EMOJI), help="prefix the message with a status emoji"
    )
    parser.add_argument("-f", "--file", type=Path, help="read the message body from a file")
    parser.add_argument(
        "-d", "--document", type=Path, help="upload a file instead of (or captioned by) the message"
    )
    parser.add_argument(
        "--code",
        nargs="?",
        const="",
        metavar="LANG",
        help="wrap the input in a code block (optionally tagged with a language)",
    )
    parser.add_argument(
        "--plain", action="store_true", help="do not interpret the input as Markdown"
    )
    parser.add_argument("--parse-mode", choices=("HTML", "MarkdownV2"), help="override parse mode")
    parser.add_argument("--chat-id", help="override TELEGRAM_CHAT_ID")
    parser.add_argument("--thread-id", type=int, help="forum topic id in the target group")
    parser.add_argument("-s", "--silent", action="store_true", help="deliver without a sound")
    parser.add_argument("--preview", action="store_true", help="enable link previews")
    parser.add_argument("--env", type=Path, help="path to a .env file")
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="render and print, but do not send"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log retries and API errors")
    parser.add_argument("--version", action="version", version=f"tg-notify {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = "send"
    if argv and argv[0] in COMMANDS:
        command = argv.pop(0)

    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        if command == "chat-id":
            return _cmd_chat_id(args)
        if command == "doctor":
            return _cmd_doctor(args)
        return _cmd_send(args)
    except ConfigError as error:
        print(f"config error: {error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except NotifierError as error:
        print(f"send failed: {error}", file=sys.stderr)
        return EXIT_SEND_FAILED
    except KeyboardInterrupt:  # pragma: no cover
        return 130


# ------------------------------------------------------------------ commands


def _cmd_send(args: argparse.Namespace) -> int:
    body = _read_message(args)
    if args.document is None and not body.strip():
        print("nothing to send (empty message)", file=sys.stderr)
        return EXIT_OK

    if args.code is not None:
        body = f"```{args.code}\n{body.rstrip()}\n```"
        args.plain = False

    config = _load_config(args)

    if args.dry_run:
        return _preview(body, config, args)

    with TelegramNotifier(config) as notifier:
        if args.document is not None:
            notifier.send_document(
                args.document,
                caption=body or None,
                title=args.title,
                level=args.level,
                markdown=not args.plain,
                silent=args.silent or None,
                chat_id=args.chat_id,
                thread_id=args.thread_id,
            )
        else:
            notifier.send(
                body,
                title=args.title,
                level=args.level,
                markdown=not args.plain,
                silent=args.silent or None,
                preview=args.preview or None,
                chat_id=args.chat_id,
                thread_id=args.thread_id,
            )
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = _load_config(args)
    print(f"token      {config.redacted_token()}")
    print(f"chat id    {args.chat_id or config.chat_id}")
    print(f"parse mode {config.parse_mode}")
    print(f"api base   {config.api_base}")

    with TelegramNotifier(config) as notifier:
        me = notifier.get_me()
        print(f"bot        @{me.get('username')} ({me.get('first_name')})")
        if args.dry_run:
            return EXIT_OK
        notifier.send(
            "`tg-notify doctor` says hello. Formatting check: "
            "**bold**, _italic_, ~~struck~~, `code`, [a link](https://core.telegram.org/bots/api).",
            title="Connection OK",
            level="success",
            chat_id=args.chat_id,
            thread_id=args.thread_id,
        )
        print("sent       test message delivered")
    return EXIT_OK


def _cmd_chat_id(args: argparse.Namespace) -> int:
    """Discover chat ids from recent updates - the usual onboarding stumble."""
    config = _load_config(args, require_chat_id=False)
    with TelegramNotifier(config) as notifier:
        updates = notifier.get_updates()

    seen: dict[str, str] = {}
    for update in updates:
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if chat:
                title = chat.get("title") or " ".join(
                    filter(None, [chat.get("first_name"), chat.get("last_name")])
                )
                username = f" @{chat['username']}" if chat.get("username") else ""
                seen[str(chat["id"])] = f"{chat.get('type', '?')}: {title}{username}"

    if not seen:
        print(
            "No chats found. Send your bot a message (or add it to the group and post there),\n"
            "then run this again. Note: Telegram only keeps updates for 24h, and this will\n"
            "return nothing if a webhook is set for the bot.",
            file=sys.stderr,
        )
        return EXIT_SEND_FAILED

    print("Add one of these to .env as TELEGRAM_CHAT_ID:\n")
    for chat_id, description in seen.items():
        print(f"  {chat_id:<16} {description}")
    return EXIT_OK


# ------------------------------------------------------------------- helpers


def _load_config(args: argparse.Namespace, *, require_chat_id: bool = True) -> Config:
    overrides = {"parse_mode": args.parse_mode}
    if not require_chat_id:
        # `chat-id` runs before the user knows their chat id.
        overrides["chat_id"] = args.chat_id or "pending"
    return Config.from_env(args.env, **overrides)


def _read_message(args: argparse.Namespace) -> str:
    if args.file is not None:
        return args.file.read_text(encoding="utf-8")
    if args.message is not None and args.message != "-":
        return args.message
    if args.message == "-" or not _stdin_is_a_terminal():
        try:
            return sys.stdin.read()
        except OSError, ValueError:
            # No usable stdin (detached cron job, closed pipe): not an error,
            # there is simply no message body from that direction.
            return ""
    return ""


def _stdin_is_a_terminal() -> bool:
    try:
        return sys.stdin.isatty()
    except OSError, ValueError:
        return False


def _preview(body: str, config: Config, args: argparse.Namespace) -> int:
    """Print exactly what would be sent, using the real rendering path."""
    parse_mode = args.parse_mode or config.parse_mode
    messages = render_document(
        body,
        markdown=not args.plain,
        parse_mode=parse_mode,
        title=args.title,
        level=args.level,
    )
    for index, message in enumerate(messages, start=1):
        print(f"--- message {index}/{len(messages)} ({len(message)} chars, {parse_mode}) ---")
        print(message)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
