# claude-telegram-notifier

Send Claude's output — or any script's output — to Telegram, formatted properly.

Claude writes GitHub-flavoured Markdown: headings, nested lists, tables, fenced
code, task lists. Telegram supports none of those. Piping one into the other
gives you either a wall of `**asterisks**` or, worse, a `400: can't parse
entities` and a notification that never arrives.

This project parses the Markdown and re-renders it into what Telegram actually
understands, then handles the unglamorous parts: retries, rate limits, the
4096-character cap, and a plain-text fallback so a formatting bug can never cost
you a notification.

It is a **send-only, personal notifier**. There is no command handling, no
polling loop, no database, nothing to monetize. One bot, one chat, one direction.

```bash
claude -p "summarise today's commits" | uv run tg-notify --title "Daily digest"
```

---

## Setup

**1. Create a bot.** Message [@BotFather](https://t.me/BotFather) → `/newbot` →
copy the token.

**2. Install.**

```bash
uv sync
```

**3. Find your chat id.** Send your new bot any message first (a bot cannot
start a conversation), then:

```bash
cp .env.example .env          # paste your token into TELEGRAM_BOT_TOKEN
uv run tg-notify chat-id
```

```
Add one of these to .env as TELEGRAM_CHAT_ID:

  987654321        private: Иерархиус Николай Вселенович
  -1001234567890   supergroup: Государственный НИИ "Зла"
```

**4. Verify.**

```bash
uv run tg-notify doctor
```

It prints the bot identity and sends a formatting test message. If something is
wrong, it says what.

### `.env`

Only the first two are required:

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | From @BotFather |
| `TELEGRAM_CHAT_ID` | — | User id, group id (negative), or `@channelname` |
| `TELEGRAM_MESSAGE_THREAD_ID` | — | Forum topic id, for groups that use topics |
| `TELEGRAM_PARSE_MODE` | `HTML` | `HTML` or `MarkdownV2` |
| `TELEGRAM_DISABLE_NOTIFICATION` | `false` | Deliver silently by default |
| `TELEGRAM_DISABLE_WEB_PAGE_PREVIEW` | `true` | Link previews off, so alerts stay compact |
| `TELEGRAM_TIMEOUT` | `15` | Per-request timeout, seconds |
| `TELEGRAM_MAX_ATTEMPTS` | `5` | Attempts before giving up |
| `TELEGRAM_SEND_INTERVAL` | `0.5` | Pause between parts of a split message |
| `TELEGRAM_API_BASE` | `https://api.telegram.org` | Point at a local bot API server |

`.env` is gitignored. Real environment variables win over the file, so CI can
just set secrets normally.

---

## Use cases

### Claude Code notifies you when it finishes

Long agent runs are the whole reason this exists — start one, walk away, get a
phone buzz when it needs you. Add a hook to `.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "cd /path/to/claude-telegram-notifier && uv run tg-notify 'Claude finished — your turn' --title 'Claude Code' --level success"
      }]
    }],
    "Notification": [{
      "hooks": [{
        "type": "command",
        "command": "cd /path/to/claude-telegram-notifier && uv run tg-notify 'Claude needs input' --level warning"
      }]
    }]
  }
}
```

### A scheduled Claude task reports in

Anything Claude prints on a schedule — a digest, a triage pass, a review — goes
straight through. The Markdown survives intact:

```bash
#!/usr/bin/env bash
# ~/bin/morning-digest.sh — run from cron at 08:00
set -euo pipefail
cd ~/Developer/PycharmProjects/claude-telegram-notifier

claude -p "Summarise yesterday's commits across ~/work/*, grouped by repo.
Flag anything that touched auth or billing." \
  | uv run tg-notify --title "Morning digest" --silent
```

```cron
0 8 * * * ~/bin/morning-digest.sh
```

`--silent` delivers without a sound: right for a scheduled digest, wrong for an
alert you need to see.

### Report a job's outcome, whichever way it went

```bash
if output=$(./nightly-backup.sh 2>&1); then
    printf '%s' "$output" | uv run tg-notify --title "Backup OK" --level success --silent
else
    printf '%s' "$output" | uv run tg-notify --code --title "Backup failed" --level error
fi
```

`--code` wraps the input in a code block, which is what you want for logs and
tracebacks — no Markdown interpretation, monospace, line breaks preserved.

### Send a failing test run

```bash
uv run pytest 2>&1 | uv run tg-notify --code --title "Test run" --level error
```

Output longer than 4096 characters is split across several messages, cut on
paragraph and line boundaries rather than mid-word. For something genuinely
long, send the file instead:

```bash
uv run tg-notify --document build.log --title "Full build log"
```

### From your own Python

```python
from telegram_notifier import notify

notify("## Deploy complete\n\n- API `v2.4.1`\n- 0 errors in 5m", title="prod")
```

Reuse one client when you send more than once:

```python
from telegram_notifier import TelegramNotifier

with TelegramNotifier() as tg:
    tg.send("Starting import…", level="info", silent=True)
    try:
        rows = run_import()
    except Exception as error:
        tg.send(f"Import failed:\n\n```\n{error}\n```", level="error")
        raise
    tg.send(f"Imported **{rows:,}** rows", level="success")
    tg.send_document("import.log", caption="Full log")
```

Async, for asyncio schedulers:

```python
from telegram_notifier import AsyncTelegramNotifier

async with AsyncTelegramNotifier() as tg:
    await tg.send("Worker pool drained", level="warning")
```

### GitHub Actions

```yaml
- run: uv sync
- run: uv run tg-notify --file report.md --title "Nightly run"
  env:
    TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
    TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

### Route different sources to different topics

One group, several forum topics, one bot:

```bash
uv run tg-notify "deploy finished" --thread-id 12   # #deploys
uv run tg-notify "disk at 91%"     --thread-id 34   # #alerts
```

---

## CLI reference

```
tg-notify [MESSAGE] [options]     send (the default command)
tg-notify doctor                  check the token, then send a test message
tg-notify chat-id                 list chat ids from recent updates
```

Message text comes from the argument, from `--file`, or from stdin — whichever
you provide. `-` also means stdin.

| Option | Effect |
|---|---|
| `-t, --title TEXT` | Bold heading above the message |
| `-l, --level LEVEL` | Prefix an emoji: `info`, `success`, `warning`, `error`, `debug` |
| `-f, --file PATH` | Read the message body from a file |
| `-d, --document PATH` | Upload a file; the message becomes its caption |
| `--code [LANG]` | Wrap the input in a code block |
| `--plain` | Do not interpret the input as Markdown |
| `--parse-mode MODE` | `HTML` or `MarkdownV2` |
| `--chat-id ID` | Override the configured chat |
| `--thread-id ID` | Target a forum topic |
| `-s, --silent` | Deliver without a sound |
| `--preview` | Enable link previews |
| `--env PATH` | Use a specific `.env` file |
| `-n, --dry-run` | Render and print, send nothing |
| `-v, --verbose` | Log retries and API errors |

Exit codes: `0` sent, `1` send failed, `2` configuration problem. Useful in a
shell — `tg-notify ... || echo "notification failed"`.

Preview exactly what Telegram will receive, including where long input gets
split:

```bash
uv run tg-notify --file report.md --dry-run
```

---

## What it does with your Markdown

Telegram has no headings, lists, tables or rules — only a flat set of inline
entities. Rather than escaping the text and hoping, the Markdown is parsed and
re-rendered:

| Claude writes | Telegram gets |
|---|---|
| `# Heading` | bold + underlined (`###` and deeper: bold) |
| `- a` / `1. a` | `•` `◦` `▪` bullets or numbers, nested levels aligned |
| `- [x] done` | `☑ done` |
| `\| a \| b \|` table | aligned monospace block |
| ` ```py ` fence | code block, language preserved for highlighting |
| `> quote` | native blockquote |
| `---` | `────────────` |
| `![alt](url)` | a link labelled `alt` |
| `**b**` `_i_` `~~s~~` `` `c` `` `[t](u)` | the matching entity |
| `\|\|secret\|\|` | spoiler |

`HTML` is the default parse mode because its escaping is unambiguous — one
character, one meaning. `MarkdownV2` is fully supported (`--parse-mode
MarkdownV2`), including the escaping of all sixteen special characters wherever
they appear, list markers included. That is exactly the fiddly part MarkdownV2
gets wrong by hand.

Anything Telegram would reject — raw HTML in the source, a stray `<`, an
ampersand in a URL — is escaped rather than dropped.

---

## Reliability

The parts you only notice when they are missing:

- **Retries.** Transport errors and `5xx` responses are retried with
  exponential backoff and jitter, up to `TELEGRAM_MAX_ATTEMPTS`.
- **Rate limits.** On `429`, Telegram's own `retry_after` is honoured rather
  than guessed at. Multi-part messages are paced to stay under the
  ~1 message/second per-chat limit.
- **Permanent errors fail fast.** `chat not found` is not retried five times —
  it raises immediately, because no amount of waiting will fix it.
- **Splitting that does not corrupt.** Messages are cut between blocks, then
  lines, then words — never inside an HTML tag or an `&entity;`. Tags left open
  at a cut are closed and reopened on the other side. Code blocks are re-fenced
  per piece, so every part is independently valid.
- **A fallback that cannot lose a message.** If Telegram ever rejects the
  formatting, the same content is immediately resent as plain text. A rendering
  bug degrades the message; it does not delete it.
- **Length counted the way Telegram counts it** — UTF-16 code units, so emoji
  and CJK text do not silently overflow the cap.

---

## Development

```bash
uv sync
uv run pytest          # 61 tests, no network required
uv run main.py         # send yourself a formatting demo
```

Layout:

```
src/telegram_notifier/
├── config.py       .env / environment loading
├── formatting.py   HTML, MarkdownV2 and plain-text escaping
├── markdown.py     Markdown -> Telegram, via a parsed syntax tree
├── splitting.py    packing and splitting within the 4096 cap
├── client.py       sync + async clients, retries, fallback
└── cli.py          tg-notify
```

Tests use `httpx.MockTransport`, so the suite runs offline and covers the paths
that are awkward to reach in real life: rate limits, transport failures, retry
exhaustion, and the plain-text fallback.

---

## Notes and limits

- A bot cannot message you first. Send it something once, or add it to your
  group, before anything can be delivered.
- `tg-notify chat-id` reads `getUpdates`. It returns nothing if a webhook is
  set on the bot, and Telegram only retains updates for 24 hours.
- To post to a channel, add the bot as an administrator and use `@channelname`
  or the numeric id as `TELEGRAM_CHAT_ID`.
- Telegram's caption limit is 1024 characters, so `--document` captions are
  truncated to fit — put the detail in the file, not the caption.
- Keep `.env` out of git. It is already in `.gitignore`; the token in it is
  full control of the bot.
