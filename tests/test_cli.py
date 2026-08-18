import json

import httpx
import pytest

from telegram_notifier import cli


@pytest.fixture
def telegram(monkeypatch, tmp_path):
    """Wire the CLI to a fake Telegram and capture what it sends."""
    calls = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        if request.headers.get("content-type", "").startswith("multipart/"):
            calls.append((method, request.content))
        else:
            calls.append((method, json.loads(request.content) if request.content else {}))
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "test_bot"}})
        if method == "getUpdates":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {"message": {"chat": {"id": 7, "type": "private", "first_name": "Ada"}}}
                    ],
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler))
    )
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=1:abc\nTELEGRAM_CHAT_ID=42\n")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    return calls, ["--env", str(env)]


class TestSend:
    def test_sends_an_argument(self, telegram):
        calls, base = telegram
        assert cli.main(["**hi**", *base]) == cli.EXIT_OK
        assert calls[0][1]["text"] == "<b>hi</b>"

    def test_reads_stdin(self, telegram, monkeypatch, capsys):
        calls, base = telegram
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("# From stdin"))
        assert cli.main(["-", *base]) == cli.EXIT_OK
        assert calls[0][1]["text"] == "<b><u>From stdin</u></b>"

    def test_code_flag_wraps_input(self, telegram):
        calls, base = telegram
        assert cli.main(["x = 1", "--code", "python", *base]) == cli.EXIT_OK
        assert calls[0][1]["text"] == '<pre><code class="language-python">x = 1</code></pre>'

    def test_level_and_title(self, telegram):
        calls, base = telegram
        cli.main(["body", "-t", "Deploy", "-l", "error", *base])
        assert calls[0][1]["text"].startswith("<b>❌ Deploy</b>")

    def test_document_keeps_title_and_level(self, telegram, tmp_path):
        calls, base = telegram
        log = tmp_path / "run.log"
        log.write_text("some output")
        assert (
            cli.main(["", "-d", str(log), "-t", "Build log", "-l", "warning", *base]) == cli.EXIT_OK
        )
        method, content = calls[0]
        assert method == "sendDocument"
        assert "<b>⚠️ Build log</b>".encode() in content
        assert b"some output" in content

    def test_dry_run_sends_nothing(self, telegram, capsys):
        calls, base = telegram
        assert cli.main(["# Title", "--dry-run", *base]) == cli.EXIT_OK
        assert calls == []
        assert "<b><u>Title</u></b>" in capsys.readouterr().out

    def test_empty_input_is_not_an_error(self, telegram, monkeypatch):
        calls, base = telegram
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("   \n"))
        assert cli.main(["-", *base]) == cli.EXIT_OK
        assert calls == []


class TestOtherCommands:
    def test_doctor(self, telegram, capsys):
        calls, base = telegram
        assert cli.main(["doctor", *base]) == cli.EXIT_OK
        out = capsys.readouterr().out
        assert "@test_bot" in out
        assert "1:abc" not in out  # the token is redacted
        assert [method for method, _ in calls] == ["getMe", "sendMessage"]

    def test_chat_id_lists_chats(self, telegram, capsys):
        calls, base = telegram
        assert cli.main(["chat-id", *base]) == cli.EXIT_OK
        assert "7" in capsys.readouterr().out


class TestStdin:
    def test_unreadable_stdin_is_survivable(self, telegram, monkeypatch):
        """Detached cron jobs have no usable stdin; that must not crash."""
        calls, base = telegram

        class Broken:
            def isatty(self):
                raise OSError("no tty")

            def read(self):
                raise OSError("nothing to read")

        monkeypatch.setattr("sys.stdin", Broken())
        assert cli.main(["explicit message", *base]) == cli.EXIT_OK
        assert calls[0][1]["text"] == "explicit message"


class TestFailureModes:
    def test_missing_config_exits_two(self, monkeypatch, capsys):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.chdir("/")
        assert cli.main(["hi"]) == cli.EXIT_CONFIG_ERROR
        assert "config error" in capsys.readouterr().err

    def test_send_failure_exits_one(self, monkeypatch, tmp_path, capsys):
        real_client = httpx.Client
        monkeypatch.setattr(
            httpx,
            "Client",
            lambda **kw: real_client(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(
                        400, json={"ok": False, "error_code": 400, "description": "chat not found"}
                    )
                )
            ),
        )
        env = tmp_path / ".env"
        env.write_text("TELEGRAM_BOT_TOKEN=1:abc\nTELEGRAM_CHAT_ID=42\n")
        assert cli.main(["hi", "--env", str(env)]) == cli.EXIT_SEND_FAILED
        assert "chat not found" in capsys.readouterr().err
