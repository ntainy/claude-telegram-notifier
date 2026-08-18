import json

import httpx
import pytest

from telegram_notifier.client import TelegramNotifier
from telegram_notifier.errors import RetryExhausted, TelegramAPIError


def make_notifier(config, handler):
    transport = httpx.MockTransport(handler)
    return TelegramNotifier(config, client=httpx.Client(transport=transport))


def ok(result=None):
    return httpx.Response(200, json={"ok": True, "result": result or {"message_id": 1}})


def api_error(status, description, **parameters):
    body = {"ok": False, "error_code": status, "description": description}
    if parameters:
        body["parameters"] = parameters
    return httpx.Response(status, json=body)


class TestSending:
    def test_sends_rendered_html(self, config):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return ok()

        with make_notifier(config, handler) as notifier:
            notifier.send("**hi** there")

        assert seen[0]["text"] == "<b>hi</b> there"
        assert seen[0]["parse_mode"] == "HTML"
        assert seen[0]["chat_id"] == "42"
        assert seen[0]["link_preview_options"] == {"is_disabled": True}

    def test_title_and_level(self, config):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return ok()

        with make_notifier(config, handler) as notifier:
            notifier.send("body", title="Backup", level="success")

        assert seen[0]["text"].startswith("<b>✅ Backup</b>")

    def test_long_message_is_split_into_several_requests(self, config):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return ok()

        with make_notifier(config, handler) as notifier:
            notifier.send("word " * 3000)

        assert len(seen) > 1
        assert all(len(call["text"]) <= 4096 for call in seen)

    def test_thread_id_is_forwarded(self, config):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return ok()

        with make_notifier(config.evolve(message_thread_id=7), handler) as notifier:
            notifier.send("hi")

        assert seen[0]["message_thread_id"] == 7


class TestRetries:
    def test_retries_on_rate_limit_and_honours_retry_after(self, config, monkeypatch):
        slept = []
        monkeypatch.setattr("telegram_notifier.client.time.sleep", slept.append)
        responses = [api_error(429, "Too Many Requests", retry_after=3), ok()]

        with make_notifier(config, lambda r: responses.pop(0)) as notifier:
            notifier.send("hi")

        assert slept and slept[0] == pytest.approx(0.0)  # capped by max_retry_after=0

    def test_retries_on_server_error(self, config, monkeypatch):
        monkeypatch.setattr("telegram_notifier.client.time.sleep", lambda _: None)
        responses = [api_error(502, "Bad Gateway"), api_error(500, "Internal"), ok()]

        with make_notifier(config, lambda r: responses.pop(0)) as notifier:
            assert notifier.send("hi")
        assert not responses

    def test_retries_on_transport_error(self, config, monkeypatch):
        monkeypatch.setattr("telegram_notifier.client.time.sleep", lambda _: None)
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("boom")
            return ok()

        with make_notifier(config, handler) as notifier:
            notifier.send("hi")
        assert calls["n"] == 3

    def test_gives_up_after_max_attempts(self, config, monkeypatch):
        monkeypatch.setattr("telegram_notifier.client.time.sleep", lambda _: None)

        with make_notifier(config.evolve(max_attempts=3), lambda r: api_error(503, "nope")) as tg:
            with pytest.raises(RetryExhausted) as excinfo:
                tg.send("hi")
        assert excinfo.value.attempts == 3

    def test_permanent_errors_are_not_retried(self, config):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return api_error(400, "Bad Request: chat not found")

        with make_notifier(config, handler) as notifier:
            with pytest.raises(TelegramAPIError) as excinfo:
                notifier.send("hi")

        assert calls["n"] == 1
        assert "chat not found" in str(excinfo.value)


class TestParseFallback:
    def test_falls_back_to_plain_text_when_entities_are_rejected(self, config):
        seen = []

        def handler(request):
            payload = json.loads(request.content)
            seen.append(payload)
            if "parse_mode" in payload:
                return api_error(400, "Bad Request: can't parse entities: unexpected tag")
            return ok()

        with make_notifier(config, handler) as notifier:
            notifier.send("**hi** there")

        assert len(seen) == 2
        assert seen[1]["text"] == "hi there"
        assert "parse_mode" not in seen[1]


class TestDocuments:
    def test_uploads_a_file(self, config, tmp_path):
        path = tmp_path / "run.log"
        path.write_text("line one\nline two\n")
        seen = {}

        def handler(request):
            seen["content"] = request.content
            seen["content_type"] = request.headers["content-type"]
            return ok()

        with make_notifier(config, handler) as notifier:
            notifier.send_document(path, caption="**log**")

        assert seen["content_type"].startswith("multipart/form-data")
        assert b"line one" in seen["content"]
        assert b"<b>log</b>" in seen["content"]
