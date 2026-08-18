import re

from telegram_notifier.markdown import markdown_to_blocks
from telegram_notifier.splitting import pack_blocks, split_text

TAG_RE = re.compile(r"<(/?)([a-zA-Z][-a-zA-Z0-9]*)[^>]*>")


def tags_balanced(text: str) -> bool:
    stack = []
    for closing, tag in TAG_RE.findall(text):
        if closing:
            if not stack or stack.pop() != tag:
                return False
        else:
            stack.append(tag)
    return not stack


class TestPacking:
    def test_short_blocks_are_merged(self):
        assert pack_blocks(["a", "b", "c"]) == ["a\n\nb\n\nc"]

    def test_packs_up_to_the_limit(self):
        messages = pack_blocks(["x" * 30] * 4, limit=70)
        assert all(len(m) <= 70 for m in messages)
        assert len(messages) == 2

    def test_oversized_block_is_split(self):
        messages = pack_blocks(["word " * 500], limit=200)
        assert len(messages) > 1
        assert all(len(m) <= 200 for m in messages)


class TestHtmlSplitting:
    def test_open_tags_are_closed_and_reopened(self):
        text = "<b>" + "word " * 400 + "</b>"
        parts = split_text(text, limit=300, parse_mode="HTML")
        assert len(parts) > 1
        assert all(len(p) <= 300 for p in parts)
        assert all(tags_balanced(p) for p in parts)

    def test_never_cuts_inside_a_tag_or_entity(self):
        text = "".join(f'<a href="https://example.com/{i}">link {i}</a> &amp; ' for i in range(200))
        for part in split_text(text, limit=400, parse_mode="HTML"):
            assert tags_balanced(part)
            assert "&am;" not in part and not part.rstrip().endswith("&")
            assert part.count("<") == part.count(">")

    def test_code_blocks_are_refenced_per_piece(self):
        block = '<pre><code class="language-py">' + "line\n" * 300 + "</code></pre>"
        parts = split_text(block, limit=400, parse_mode="HTML")
        assert len(parts) > 1
        for part in parts:
            assert part.startswith('<pre><code class="language-py">')
            assert part.endswith("</code></pre>")
            assert len(part) <= 400

    def test_markdown_v2_fences_are_reopened(self):
        block = "```py\n" + "line\n" * 300 + "```"
        parts = split_text(block, limit=400, parse_mode="MarkdownV2")
        assert len(parts) > 1
        for part in parts:
            assert part.startswith("```py\n") and part.endswith("\n```")


class TestEndToEnd:
    def test_long_claude_style_document(self):
        source = "\n\n".join(
            [
                "# Report",
                "Some **bold** narrative with a [link](https://example.com).",
                "```python\n" + "\n".join(f"value_{i} = {i}" for i in range(400)) + "\n```",
                "- item\n" * 200,
            ]
        )
        messages = pack_blocks(markdown_to_blocks(source, "HTML"), parse_mode="HTML")
        assert len(messages) > 1
        for message in messages:
            assert len(message) <= 4096
            assert tags_balanced(message)
