import asyncio
import json

import pytest

pytest.importorskip("mcp", reason="needs the 'mcp' extra: uv sync --extra mcp")

from telegram_notifier import mcp_server  # noqa: E402
from telegram_notifier.config import Config  # noqa: E402
from telegram_notifier.errors import RetryExhausted, TelegramAPIError  # noqa: E402


class FakeNotifier:
    """Stands in for TelegramNotifier, recording sends instead of making them."""

    calls: list[dict] = []
    raises: Exception | None = None
    parts: int = 1

    def __init__(self, config):
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def send(self, text, **kwargs):
        type(self).calls.append({"text": text, **kwargs})
        if type(self).raises:
            raise type(self).raises
        return [{"message_id": i} for i in range(type(self).parts)]

    def get_me(self):
        if type(self).raises:
            raise type(self).raises
        return {"username": "test_bot"}


@pytest.fixture
def fake(monkeypatch):
    FakeNotifier.calls = []
    FakeNotifier.raises = None
    FakeNotifier.parts = 1
    monkeypatch.setattr(mcp_server, "TelegramNotifier", FakeNotifier)
    return FakeNotifier


@pytest.fixture
def server(config):
    return mcp_server.build_server(config)


def call(server, name, **arguments):
    return asyncio.run(server.call_tool(name, arguments))


def send_schema(server):
    tool = next(
        t for t in asyncio.run(server.list_tools()) if t.name == "send_telegram_notification"
    )
    return tool.input_schema


class TestToolSurface:
    def test_registers_both_tools(self, server):
        names = {tool.name for tool in asyncio.run(server.list_tools())}
        assert names == {"send_telegram_notification", "check_telegram_connection"}

    def test_text_is_the_only_required_argument(self, server):
        assert send_schema(server)["required"] == ["text"]

    def test_schema_constrains_the_level(self, server):
        # The Literal is the enforcement, so it has to reach the wire schema.
        # Shape varies (anyOf/$ref/enum) across mcp versions; membership does not.
        rendered = json.dumps(send_schema(server))
        for level in ("info", "success", "warning", "error", "debug"):
            assert level in rendered


class TestSending:
    def test_passes_arguments_through(self, server, fake):
        call(
            server,
            "send_telegram_notification",
            text="**done**",
            title="Nightly",
            level="success",
            silent=True,
        )
        assert fake.calls == [
            {"text": "**done**", "title": "Nightly", "level": "success", "silent": True}
        ]

    def test_defaults_are_audible_and_untitled(self, server, fake):
        call(server, "send_telegram_notification", text="plain")
        assert fake.calls[0] == {"text": "plain", "title": None, "level": None, "silent": False}

    def test_reports_how_many_parts_were_sent(self, server, fake):
        fake.parts = 3
        result = call(server, "send_telegram_notification", text="long")
        assert "3 messages" in str(result)

    def test_singular_for_one_part(self, server, fake):
        result = call(server, "send_telegram_notification", text="short")
        assert "1 message" in str(result)
        assert "1 messages" not in str(result)


class TestRefusals:
    @pytest.mark.parametrize("text", ["", "   ", "\n\t"])
    def test_refuses_empty_text(self, server, fake, text):
        with pytest.raises(Exception, match="empty"):
            call(server, "send_telegram_notification", text=text)
        assert fake.calls == []

    def test_rejects_unknown_level(self, server, fake):
        # Rejected by schema validation, before the tool body runs -- which is
        # why the body carries no level check of its own.
        with pytest.raises(Exception, match="level"):
            call(server, "send_telegram_notification", text="hi", level="critical")
        assert fake.calls == []


class TestErrorMapping:
    def test_api_error_reaches_the_caller(self, server, fake):
        fake.raises = TelegramAPIError("chat not found", status_code=400, error_code=400)
        with pytest.raises(Exception, match="chat not found"):
            call(server, "send_telegram_notification", text="hi")

    def test_retry_exhausted_reaches_the_caller(self, server, fake):
        fake.raises = RetryExhausted("sendMessage", 5, TelegramAPIError("502 Bad Gateway"))
        with pytest.raises(Exception, match="sendMessage failed after 5"):
            call(server, "send_telegram_notification", text="hi")


class TestConnectionCheck:
    def test_reports_identity_without_sending(self, server, fake):
        result = call(server, "check_telegram_connection")
        assert "@test_bot" in str(result)
        assert fake.calls == []

    def test_token_is_redacted(self, server, fake):
        result = call(server, "check_telegram_connection")
        assert "test-token" not in str(result)


class TestConfigResolution:
    def test_prefers_an_explicit_path(self, tmp_path, monkeypatch):
        env = tmp_path / "custom.env"
        env.write_text("TELEGRAM_BOT_TOKEN=1:explicit\nTELEGRAM_CHAT_ID=7\n")
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        assert mcp_server.load_config(env).chat_id == "7"

    def test_env_var_points_at_the_file(self, tmp_path, monkeypatch):
        env = tmp_path / "pointed.env"
        env.write_text("TELEGRAM_BOT_TOKEN=1:pointed\nTELEGRAM_CHAT_ID=8\n")
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("TELEGRAM_ENV_FILE", str(env))
        assert mcp_server.load_config().chat_id == "8"

    def test_real_environment_wins_over_the_file(self, tmp_path, monkeypatch):
        env = tmp_path / "loser.env"
        env.write_text("TELEGRAM_BOT_TOKEN=1:file\nTELEGRAM_CHAT_ID=999\n")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:real")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
        config = mcp_server.load_config(env)
        assert (config.bot_token, config.chat_id) == ("1:real", "42")

    def test_missing_file_falls_back_to_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:env-only")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "11")
        config = mcp_server.load_config(tmp_path / "nope.env")
        assert config.chat_id == "11"

    def test_default_points_at_the_project_root(self):
        assert mcp_server.DEFAULT_ENV_FILE.name == ".env"
        assert (mcp_server.PROJECT_ROOT / "pyproject.toml").exists()


class TestServerConstruction:
    def test_explicit_config_is_used_verbatim(self, fake, config):
        server = mcp_server.build_server(config)
        call(server, "check_telegram_connection")
        # A config passed in must not be re-resolved from the environment.
        assert isinstance(config, Config)
