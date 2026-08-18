import pytest

from telegram_notifier.markdown import markdown_to_blocks, markdown_to_telegram


def html(source: str) -> str:
    return markdown_to_telegram(source, "HTML")


def mdv2(source: str) -> str:
    return markdown_to_telegram(source, "MarkdownV2")


class TestEscaping:
    def test_html_special_characters_are_escaped(self):
        assert html("a < b & c > d") == "a &lt; b &amp; c &gt; d"

    def test_markdown_v2_specials_are_escaped(self):
        assert mdv2("cost: $5.00 (50% off!)") == r"cost: $5\.00 \(50% off\!\)"

    def test_code_keeps_html_special_characters_readable(self):
        assert html("`<script>`") == "<code>&lt;script&gt;</code>"

    def test_code_escapes_backticks_in_markdown_v2(self):
        assert mdv2("`a\\b`") == "`a\\\\b`"


class TestInline:
    def test_bold_italic_strike(self):
        assert html("**b** _i_ ~~s~~") == "<b>b</b> <i>i</i> <s>s</s>"
        assert mdv2("**b** _i_ ~~s~~") == "*b* _i_ ~s~"

    def test_links(self):
        assert html("[docs](https://example.com/a?b=1&c=2)") == (
            '<a href="https://example.com/a?b=1&amp;c=2">docs</a>'
        )
        assert mdv2("[docs](https://example.com/x_y)") == "[docs](https://example.com/x_y)"

    def test_images_become_links(self):
        assert html("![chart](https://example.com/c.png)") == (
            '<a href="https://example.com/c.png">chart</a>'
        )

    def test_spoilers(self):
        assert html("the answer is ||42||") == "the answer is <tg-spoiler>42</tg-spoiler>"

    def test_nested_emphasis(self):
        assert html("**bold with `code`**") == "<b>bold with <code>code</code></b>"


class TestBlocks:
    def test_headings_become_bold(self):
        assert html("# Title") == "<b><u>Title</u></b>"
        assert html("### Sub") == "<b>Sub</b>"

    def test_bullet_list(self):
        assert html("- one\n- two") == "• one\n• two"

    def test_ordered_list_keeps_numbering(self):
        assert html("3. three\n4. four") == "3. three\n4. four"

    def test_nested_list_is_indented_with_a_different_bullet(self):
        assert html("- a\n    - b") == "• a\n  ◦ b"

    def test_task_lists_become_checkboxes(self):
        assert html("- [x] done\n- [ ] todo") == "• ☑ done\n• ☐ todo"

    def test_fenced_code_keeps_its_language(self):
        assert html("```python\nx = 1\n```") == (
            '<pre><code class="language-python">x = 1</code></pre>'
        )
        assert mdv2("```python\nx = 1\n```") == "```python\nx = 1\n```"

    def test_blockquote(self):
        assert html("> quoted") == "<blockquote>quoted</blockquote>"
        assert mdv2("> quoted") == ">quoted"

    def test_horizontal_rule(self):
        assert "─" in html("---")

    def test_table_becomes_monospace(self):
        rendered = html("| a | bb |\n| - | -- |\n| 1 | 2 |")
        assert rendered.startswith("<pre>")
        assert "a  bb" in rendered
        assert "1  2" in rendered

    def test_blocks_are_split_at_top_level(self):
        blocks = markdown_to_blocks("# T\n\npara\n\n- a\n- b")
        assert blocks == ["<b><u>T</u></b>", "para", "• a\n• b"]


class TestRobustness:
    @pytest.mark.parametrize(
        "source",
        [
            "",
            "   \n\n  ",
            "*unclosed",
            "```\nno closing fence",
            "<div>raw html</div>",
            "| broken | table\n|---|",
            "a" * 5000,
        ],
    )
    def test_never_raises(self, source):
        assert isinstance(html(source), str)
        assert isinstance(mdv2(source), str)

    def test_raw_html_is_neutralised(self):
        assert "<div>" not in html("<div>raw html</div>")


class TestMarkdownV2Strictness:
    """MarkdownV2 rejects *any* unescaped special character, markers included."""

    SPECIALS = set(r"_*[]()~`>#+-=|{}.!")

    def unescaped(self, rendered: str) -> set[str]:
        found, escaped = set(), False
        for char in rendered:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
            elif char in self.SPECIALS:
                found.add(char)
        return found

    def test_ordered_list_markers_are_escaped(self):
        assert mdv2("1. one\n2. two") == "1\\. one\n2\\. two"

    def test_plain_prose_has_no_unescaped_specials(self):
        rendered = mdv2("Cost: $5.00 (50% off!) - see item #3 [ref] {x} a=b|c~d")
        assert self.unescaped(rendered) == set()

    def test_only_intended_markers_survive_in_formatted_text(self):
        # The emphasis markers themselves are the only specials left standing.
        assert self.unescaped(mdv2("**b**")) == {"*"}
        assert self.unescaped(mdv2("a. b! c#")) == set()
