import pytest

from telegram_notifier.config import Config


@pytest.fixture
def config() -> Config:
    return Config(
        bot_token="123456:test-token",
        chat_id="42",
        send_interval=0.0,
        backoff_base=0.0,
        backoff_max=0.0,
        max_retry_after=0.0,
    )
