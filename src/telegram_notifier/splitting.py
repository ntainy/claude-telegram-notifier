"""Split long documents into messages Telegram will accept.

Telegram caps a text message at 4096 UTF-16 code units. Cutting formatted text
at an arbitrary offset breaks it - you can land inside an HTML tag, inside an
entity like ``&amp;``, or between the halves of a bold marker - and Telegram
answers with "can't parse entities" instead of delivering anything.

So we cut in order of preference: between blocks, then between lines, then
between words, and never inside a tag or entity. Any HTML tags left open at a
cut are closed on the way out and reopened on the way in.
"""

from __future__ import annotations

import re

MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024

# Room reserved for the closing/reopening tags a mid-entity cut needs.
_TAG_RESERVE = 96

_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z][-a-zA-Z0-9]*)([^>]*)>")
# Ordered most-specific first: a single alternation would let the engine pair
# a `<pre><code ...>` prefix with a bare `</pre>` suffix and drop `</code>`.
_CODE_PATTERNS = {
    "HTML": (
        re.compile(r"^(<pre><code[^>]*>)(.*)(</code></pre>)$", re.DOTALL),
        re.compile(r"^(<pre>)(.*)(</pre>)$", re.DOTALL),
    ),
    "MarkdownV2": (re.compile(r"^(```[^\n]*\n)(.*)(\n```)$", re.DOTALL),),
}


def pack_blocks(
    blocks: list[str],
    *,
    limit: int = MESSAGE_LIMIT,
    parse_mode: str | None = "HTML",
    separator: str = "\n\n",
) -> list[str]:
    """Greedily pack rendered blocks into as few messages as possible."""
    messages: list[str] = []
    current = ""

    for block in blocks:
        candidate = f"{current}{separator}{block}" if current else block
        if _length(candidate) <= limit:
            current = candidate
            continue

        if current:
            messages.append(current)
            current = ""
        if _length(block) <= limit:
            current = block
        else:
            pieces = split_text(block, limit=limit, parse_mode=parse_mode)
            messages.extend(pieces[:-1])
            current = pieces[-1]

    if current:
        messages.append(current)
    return messages


def split_text(
    text: str, *, limit: int = MESSAGE_LIMIT, parse_mode: str | None = "HTML"
) -> list[str]:
    """Split one oversized block into several valid messages."""
    if _length(text) <= limit:
        return [text]

    wrapper = _code_wrapper(text, parse_mode)
    if wrapper is not None:
        return _split_code(wrapper, limit)

    if parse_mode == "HTML":
        return _split_html(text, limit)
    return _split_plain(text, limit)


# --------------------------------------------------------------------- helpers


def _length(text: str) -> int:
    """Telegram counts UTF-16 code units, so astral characters cost two."""
    return len(text.encode("utf-16-le")) // 2


def _code_wrapper(text: str, parse_mode: str | None) -> tuple[str, str, str] | None:
    """Return (prefix, body, suffix) when the block is a single code block."""
    for pattern in _CODE_PATTERNS.get(parse_mode or "", ()):
        match = pattern.match(text)
        if match is not None:
            return match.group(1), match.group(2), match.group(3)
    return None


def _split_code(wrapper: tuple[str, str, str], limit: int) -> list[str]:
    """Split a code block line-by-line, re-fencing every piece."""
    prefix, body, suffix = wrapper
    budget = limit - _length(prefix) - _length(suffix)
    if budget <= 0:  # pathological limit; fall back to a dumb split
        return _split_plain(prefix + body + suffix, limit)

    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for line in body.split("\n"):
        for chunk in _hard_wrap(line, budget):
            addition = _length(chunk) + (1 if current else 0)
            if size + addition > budget and current:
                pieces.append(prefix + "\n".join(current) + suffix)
                current, size = [], 0
                addition = _length(chunk)
            current.append(chunk)
            size += addition
    if current:
        pieces.append(prefix + "\n".join(current) + suffix)
    return pieces


def _split_html(text: str, limit: int) -> list[str]:
    """Split HTML, closing open tags at each cut and reopening them after."""
    pieces: list[str] = []
    remainder = text

    while _length(remainder) > limit:
        cut = _cut_index(remainder, limit - _TAG_RESERVE)
        head, tail = remainder[:cut], remainder[cut:]
        open_tags = _open_tags(head)
        closing = "".join(f"</{tag}>" for tag, _ in reversed(open_tags))
        reopening = "".join(f"<{tag}{attrs}>" for tag, attrs in open_tags)
        pieces.append(head.rstrip() + closing)
        remainder = reopening + tail.lstrip("\n")

    if remainder.strip():
        pieces.append(remainder)
    return pieces


def _split_plain(text: str, limit: int) -> list[str]:
    pieces: list[str] = []
    remainder = text
    while _length(remainder) > limit:
        cut = _cut_index(remainder, limit)
        pieces.append(remainder[:cut].rstrip())
        remainder = remainder[cut:].lstrip("\n")
    if remainder.strip():
        pieces.append(remainder)
    return pieces


def _cut_index(text: str, limit: int) -> int:
    """The best index to cut ``text`` at, at or before ``limit``."""
    hard = _last_safe_index(text, limit)
    if hard <= 0:
        return min(limit, len(text))

    window = text[:hard]
    for separator in ("\n\n", "\n", " "):
        index = window.rfind(separator)
        # Only honour a boundary that keeps the message reasonably full.
        if index > hard // 2:
            return index + len(separator)
    return hard


def _last_safe_index(text: str, limit: int) -> int:
    """Largest index <= limit that is not inside an HTML tag or entity."""
    safe = 0
    in_tag = False
    in_entity = False
    for index, char in enumerate(text):
        if index > limit:
            break
        if not in_tag and not in_entity:
            safe = index
        if char == "<":
            in_tag = True
        elif char == ">":
            in_tag = False
        elif char == "&" and not in_tag:
            in_entity = True
        elif in_entity and (char == ";" or not char.isalnum() and char != "#"):
            in_entity = False
    return safe if safe > 0 else min(limit, len(text))


def _open_tags(fragment: str) -> list[tuple[str, str]]:
    """Tags opened but not closed in ``fragment``, outermost first."""
    stack: list[tuple[str, str]] = []
    for match in _HTML_TAG_RE.finditer(fragment):
        closing, tag, attrs = match.group(1), match.group(2), match.group(3)
        if closing:
            for position in range(len(stack) - 1, -1, -1):
                if stack[position][0] == tag:
                    del stack[position]
                    break
        else:
            stack.append((tag, attrs))
    return stack


def _hard_wrap(line: str, width: int) -> list[str]:
    if _length(line) <= width:
        return [line]
    return [line[i : i + width] for i in range(0, len(line), width)]
