"""Convert the Markdown Claude writes into what Telegram can render.

Claude produces ordinary GitHub-flavoured Markdown: headings, nested lists,
tables, fenced code, task lists. Telegram supports none of those structures -
only a flat set of inline entities. So instead of escaping the text and hoping,
we parse it properly and *re-render* it:

    heading      -> bold (underlined for h1/h2)
    list         -> unicode bullets / numbers, indented per level
    task list    -> checkbox glyphs
    table        -> aligned monospace block
    rule         -> a line of box-drawing characters
    image        -> a link
    everything else -> the matching Telegram entity

Rendering yields a list of top-level *blocks*, which lets
:mod:`telegram_notifier.splitting` cut long documents on sane boundaries.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from .formatting import Formatter, get_formatter

BULLETS = ("•", "◦", "▪", "‣")
RULE = "─" * 12
CHECKED = "☑"
UNCHECKED = "☐"

# `- [x] item` is not CommonMark, so normalise it before parsing.
_TASK_ITEM_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)\[([ xX])\]\s+", re.MULTILINE)
# ||spoiler|| is Telegram-specific and has no Markdown equivalent.
_SPOILER_RE = re.compile(r"\|\|(.+?)\|\|", re.DOTALL)

_PARSER = MarkdownIt("default").disable("linkify")


def markdown_to_blocks(source: str, parse_mode: str | None = "HTML") -> list[str]:
    """Render Markdown into a list of formatted top-level blocks."""
    return _Renderer(get_formatter(parse_mode)).render(source)


def markdown_to_telegram(source: str, parse_mode: str | None = "HTML") -> str:
    """Render Markdown into a single formatted string."""
    return "\n\n".join(markdown_to_blocks(source, parse_mode))


def _preprocess(source: str) -> str:
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    return _TASK_ITEM_RE.sub(
        lambda m: f"{m.group(1)}{CHECKED if m.group(2) in 'xX' else UNCHECKED} ",
        source,
    )


class _Renderer:
    """Walks a Markdown syntax tree and emits one Telegram output format."""

    def __init__(self, formatter: Formatter) -> None:
        self.fmt = formatter
        self._list_depth = 0

    # ------------------------------------------------------------------ blocks

    def render(self, source: str) -> list[str]:
        tokens = _PARSER.parse(_preprocess(source))
        root = SyntaxTreeNode(tokens)
        blocks = []
        for node in root.children:
            rendered = self.block(node)
            if rendered.strip():
                blocks.append(rendered)
        return blocks

    def block(self, node: SyntaxTreeNode) -> str:
        handler = getattr(self, f"_block_{node.type}", None)
        if handler is not None:
            return handler(node)
        # Unknown block: render whatever inline content it carries.
        return self._children_blocks(node)

    def _children_blocks(self, node: SyntaxTreeNode, *, separator: str = "\n\n") -> str:
        return separator.join(
            rendered for child in node.children if (rendered := self.block(child)).strip()
        )

    def _list_item_body(self, item: SyntaxTreeNode) -> str:
        """A list item's content, with nested lists kept tight against it."""
        parts: list[str] = []
        for child in item.children:
            rendered = self.block(child)
            if not rendered.strip():
                continue
            tight = child.type in ("bullet_list", "ordered_list")
            parts.append(rendered if not parts else ("\n" if tight else "\n\n") + rendered)
        return "".join(parts)

    def _block_paragraph(self, node: SyntaxTreeNode) -> str:
        return "".join(self.inline(child) for child in node.children)

    def _block_inline(self, node: SyntaxTreeNode) -> str:
        return self.inline(node)

    def _block_heading(self, node: SyntaxTreeNode) -> str:
        content = self._block_paragraph(node)
        level = int(node.tag[1:] or 3)
        if level <= 2:
            return self.fmt.bold(self.fmt.underline(content))
        return self.fmt.bold(content)

    def _block_hr(self, node: SyntaxTreeNode) -> str:
        return RULE

    def _block_fence(self, node: SyntaxTreeNode) -> str:
        info = (node.info or "").strip().split()
        language = info[0] if info else None
        return self.fmt.pre(node.content.rstrip("\n"), language)

    def _block_code_block(self, node: SyntaxTreeNode) -> str:
        return self.fmt.pre(node.content.rstrip("\n"), None)

    def _block_blockquote(self, node: SyntaxTreeNode) -> str:
        # Telegram cannot nest quotes, so a nested quote is flattened into one.
        return self.fmt.quote(self._children_blocks(node))

    def _block_bullet_list(self, node: SyntaxTreeNode) -> str:
        return self._render_list(node, ordered=False)

    def _block_ordered_list(self, node: SyntaxTreeNode) -> str:
        return self._render_list(node, ordered=True)

    def _block_html_block(self, node: SyntaxTreeNode) -> str:
        # Raw HTML is almost certainly not Telegram-legal; show it as text.
        return self.fmt.text(node.content.strip())

    def _render_list(self, node: SyntaxTreeNode, *, ordered: bool) -> str:
        # No indent of our own: a nested list is indented by the padding its
        # parent item applies to continuation lines, which keeps it aligned
        # under the parent's text however wide that marker was.
        start = int(node.attrs.get("start", 1)) if ordered else 1
        bullet = BULLETS[self._list_depth % len(BULLETS)]

        self._list_depth += 1
        try:
            lines: list[str] = []
            for offset, item in enumerate(node.children):
                marker = f"{start + offset}." if ordered else bullet
                body = self._list_item_body(item)
                head, *rest = body.split("\n")
                # Escape the marker (MarkdownV2 needs `1\.`) but measure the
                # raw one, so the padding below still lines up visually.
                lines.append(f"{self.fmt.text(marker)} {head}")
                # Continuation lines line up under the item text, not the marker.
                padding = " " * (len(marker) + 1)
                lines.extend(padding + line if line else "" for line in rest)
            return "\n".join(lines)
        finally:
            self._list_depth -= 1

    def _block_table(self, node: SyntaxTreeNode) -> str:
        rows: list[list[str]] = []
        header_rows = 0
        for section in node.children:
            for row in section.children:
                rows.append([_plain_text(cell) for cell in row.children])
            if section.type == "thead":
                header_rows = len(rows)
        if not rows:
            return ""

        columns = max(len(row) for row in rows)
        rows = [row + [""] * (columns - len(row)) for row in rows]
        widths = [max(len(row[i]) for row in rows) for i in range(columns)]

        lines = []
        for index, row in enumerate(rows):
            lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
            if index + 1 == header_rows:
                lines.append("  ".join("─" * width for width in widths))
        return self.fmt.pre("\n".join(lines), None)

    # ------------------------------------------------------------------ inline

    def inline(self, node: SyntaxTreeNode) -> str:
        return "".join(self._inline_node(child) for child in node.children)

    def _inline_node(self, node: SyntaxTreeNode) -> str:
        match node.type:
            case "text" | "text_special":
                return self._text(node.content)
            case "code_inline":
                return self.fmt.code(node.content)
            case "strong":
                return self.fmt.bold(self.inline(node))
            case "em":
                return self.fmt.italic(self.inline(node))
            case "s":
                return self.fmt.strike(self.inline(node))
            case "link":
                url = node.attrs.get("href", "")
                label = self.inline(node) or self.fmt.text(str(url))
                return self.fmt.link(label, str(url))
            case "image":
                alt = _plain_text(node) or "image"
                return self.fmt.link(self.fmt.text(alt), str(node.attrs.get("src", "")))
            case "softbreak" | "hardbreak":
                # Chat is line-oriented: keep the author's line breaks.
                return "\n"
            case "html_inline":
                return self.fmt.text(node.content)
            case _:
                return self.inline(node) if node.children else self._text(node.content)

    def _text(self, raw: str) -> str:
        """Escape literal text, honouring Telegram's ||spoiler|| syntax."""
        out: list[str] = []
        position = 0
        for match in _SPOILER_RE.finditer(raw):
            out.append(self.fmt.text(raw[position : match.start()]))
            out.append(self.fmt.spoiler(self.fmt.text(match.group(1))))
            position = match.end()
        out.append(self.fmt.text(raw[position:]))
        return "".join(out)


def _plain_text(node: SyntaxTreeNode) -> str:
    """All the literal text under a node, with markup dropped."""
    if node.type in ("text", "text_special", "code_inline"):
        return node.content
    if node.type == "image":
        return str(node.attrs.get("alt", "")) or "".join(
            _plain_text(child) for child in node.children
        )
    if node.type in ("softbreak", "hardbreak"):
        return " "
    return "".join(_plain_text(child) for child in node.children)
