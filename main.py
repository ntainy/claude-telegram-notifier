"""Demo: send one richly formatted notification, to check your setup.

    uv run main.py

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.
For everyday use reach for the CLI (`uv run tg-notify ...`) or import
`telegram_notifier` from your own script.
"""

from telegram_notifier import NotifierError, TelegramNotifier

REPORT = """
# Nightly maintenance

Finished in **4m 21s**. Everything below renders natively in Telegram.

## What ran

| Step     | Result | Time |
|----------|--------|------|
| backup   | ok     | 2m10s|
| vacuum   | ok     | 1m03s|
| reindex  | ok     | 1m08s|

## Follow-ups

- [x] Snapshot uploaded to object storage
- [ ] Rotate credentials
  - expires in 6 days
  - owner: platform team

> Disk usage crossed 80%. Worth a look this week.

```sql
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 5;
```

Full log: [ci.example.com/482](https://ci.example.com/482)
"""


def main() -> int:
    try:
        with TelegramNotifier() as notifier:
            bot = notifier.get_me()
            print(f"Connected as @{bot['username']}")
            parts = notifier.send(REPORT, title="Demo notification", level="success")
            print(f"Delivered in {len(parts)} message(s).")
    except NotifierError as error:
        print(f"Failed: {error}")
        print("Check your .env - `uv run tg-notify doctor` explains what is wrong.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
