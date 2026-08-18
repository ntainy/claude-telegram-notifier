"""Output formats Telegram understands.

Telegram accepts a *tiny* subset of HTML and its own MarkdownV2 dialect.
Neither has headings, lists, tables or horizontal rules, and both are strict
about escaping. Each formatter below knows how to escape text and how to build
the handful of entities Telegram actually supports; :mod:`telegram_notifier.markdown`
drives them from a parsed Markdown tree.
"""

from __future__ import annotations

import html
import re
from typing import Protocol

# Characters MarkdownV2 requires to be backslash-escaped in ordinary text.
_MDV2_SPECIALS = r"_*[]()~`>#+-=|{}.!"
_MDV2_TEXT_RE = re.compile(f"([{re.escape(_MDV2_SPECIALS)}])")
# Inside code spans only the backslash and the backtick are special.
_MDV2_CODE_RE = re.compile(r"([`\\])")
# Inside a link target only the closing paren and the backslash are special.
_MDV2_URL_RE = re.compile(r"([)\\])")


class Formatter(Protocol):
    """Turns pieces of a document into one concrete Telegram output format."""

    parse_mode: str | None

    def text(self, raw: str) -> str:
        """Escape literal text so Telegram renders it verbatim."""

    def bold(self, formatted: str) -> str: ...
    def italic(self, formatted: str) -> str: ...
    def underline(self, formatted: str) -> str: ...
    def strike(self, formatted: str) -> str: ...
    def spoiler(self, formatted: str) -> str: ...

    def code(self, raw: str) -> str:
        """Inline code. Takes *unescaped* source."""

    def pre(self, raw: str, language: str | None) -> str:
        """A code block. Takes *unescaped* source."""

    def link(self, formatted_label: str, url: str) -> str: ...

    def quote(self, formatted_body: str) -> str: ...


class HtmlFormatter:
    """Telegram's HTML mode - the default, because escaping is unambiguous."""

    parse_mode = "HTML"

    def text(self, raw: str) -> str:
        return html.escape(raw, quote=False)

    def bold(self, formatted: str) -> str:
        return f"<b>{formatted}</b>"

    def italic(self, formatted: str) -> str:
        return f"<i>{formatted}</i>"

    def underline(self, formatted: str) -> str:
        return f"<u>{formatted}</u>"

    def strike(self, formatted: str) -> str:
        return f"<s>{formatted}</s>"

    def spoiler(self, formatted: str) -> str:
        return f"<tg-spoiler>{formatted}</tg-spoiler>"

    def code(self, raw: str) -> str:
        return f"<code>{self.text(raw)}</code>"

    def pre(self, raw: str, language: str | None) -> str:
        body = self.text(raw)
        if not language:
            return f"<pre>{body}</pre>"
        tag = html.escape(language, quote=True)
        return f'<pre><code class="language-{tag}">{body}</code></pre>'

    def link(self, formatted_label: str, url: str) -> str:
        return f'<a href="{html.escape(url, quote=True)}">{formatted_label}</a>'

    def quote(self, formatted_body: str) -> str:
        return f"<blockquote>{formatted_body}</blockquote>"


class MarkdownV2Formatter:
    """Telegram's MarkdownV2 dialect."""

    parse_mode = "MarkdownV2"

    def text(self, raw: str) -> str:
        return _MDV2_TEXT_RE.sub(r"\\\1", raw)

    def bold(self, formatted: str) -> str:
        return f"*{formatted}*"

    def italic(self, formatted: str) -> str:
        return f"_{formatted}_"

    def underline(self, formatted: str) -> str:
        return f"__{formatted}__"

    def strike(self, formatted: str) -> str:
        return f"~{formatted}~"

    def spoiler(self, formatted: str) -> str:
        return f"||{formatted}||"

    def code(self, raw: str) -> str:
        return f"`{_MDV2_CODE_RE.sub(r'\\\1', raw)}`"

    def pre(self, raw: str, language: str | None) -> str:
        body = _MDV2_CODE_RE.sub(r"\\\1", raw)
        return f"```{language or ''}\n{body}\n```"

    def link(self, formatted_label: str, url: str) -> str:
        return f"[{formatted_label}]({_MDV2_URL_RE.sub(r'\\\1', url)})"

    def quote(self, formatted_body: str) -> str:
        # MarkdownV2 quotes are per-line, and blank lines end the quote,
        # so every line - including empty ones - needs the marker.
        return "\n".join(">" + line for line in formatted_body.split("\n"))


class PlainFormatter:
    """No markup at all.

    Used as the safety net: if Telegram ever rejects our entities we resend the
    same document as plain text rather than dropping the notification.
    """

    parse_mode = None

    def text(self, raw: str) -> str:
        return raw

    def bold(self, formatted: str) -> str:
        return formatted

    italic = underline = strike = spoiler = bold

    def code(self, raw: str) -> str:
        return raw

    def pre(self, raw: str, language: str | None) -> str:
        return raw

    def link(self, formatted_label: str, url: str) -> str:
        return formatted_label if formatted_label == url else f"{formatted_label} ({url})"

    def quote(self, formatted_body: str) -> str:
        return "\n".join("| " + line for line in formatted_body.split("\n"))


FORMATTERS: dict[str | None, type[Formatter]] = {
    "HTML": HtmlFormatter,
    "MarkdownV2": MarkdownV2Formatter,
    None: PlainFormatter,
}


def get_formatter(parse_mode: str | None) -> Formatter:
    try:
        return FORMATTERS[parse_mode]()  # type: ignore[abstract]
    except KeyError:
        raise ValueError(
            f"Unknown parse mode {parse_mode!r}; expected 'HTML', 'MarkdownV2' or None."
        ) from None


def escape(raw: str, parse_mode: str | None = "HTML") -> str:
    """Escape a plain string for direct interpolation into a Telegram message."""
    return get_formatter(parse_mode).text(raw)


def strip_markup(formatted: str, parse_mode: str | None) -> str:
    """Best-effort reverse of a formatter, for the plain-text fallback path."""
    if parse_mode == "HTML":
        return html.unescape(re.sub(r"<[^>]+>", "", formatted))
    if parse_mode == "MarkdownV2":
        return re.sub(r"\\(.)", r"\1", formatted)
    return formatted
