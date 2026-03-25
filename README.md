# telegramcleaner

`telegramcleaner` is an installable Python library and CLI for cleaning Telegram channels through `telebridge`. It keeps the original behavior intact, including multi-channel cleanup, 100-message batch deletion, `tqdm` progress bars, and automatic FloodWait retry handling.

## Features

- Installable with `pip install -e .`
- CLI entry point via `telegramcleaner`
- Importable library API via `from telegramcleaner import TelegramCleaner`
- Interactive setup wizard for credentials and channels
- Optional `.env` and `channels.json` persistence
- Sequential multi-channel cleaning
- Telegram command mode for controlling cleanup directly from Telegram
- Batch deletion with per-message fallback on failure
- Async Python 3.10+ implementation

## Requirements

- Python 3.10 or newer
- A Telegram account with permission to delete messages in the target channels
- Telegram API credentials from https://my.telegram.org

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## CLI Usage

Run the installed command from the project root or any working directory where you want `.env` and `channels.json` to be created:

```bash
telegramcleaner
```

With no arguments, the CLI now opens interactive channel selection mode. You can also run direct commands:

```bash
telegramcleaner list
telegramcleaner deleteall @channel
telegramcleaner da @channel
telegramcleaner delete 1000 @channel
telegramcleaner d 1000 @channel
telegramcleaner clean @channel
telegramcleaner c @channel
```

`deleteall`, `da`, `clean`, and `c` ask for confirmation before removing everything from the target chat.

To run the Telegram-controlled userbot mode instead:

```bash
telegramcleaner command-mode
```

The setup wizard asks for:

- Telegram API ID
- Telegram API HASH
- Session name (default: `telegramcleaner`)
- Channel usernames or IDs as a comma-separated list

Example channel input:

```text
@channel1,@channel2,-100123456789
```

## Command Mode

Command mode listens for your own outgoing Telegram messages and turns them into cleanup actions. This keeps the control surface limited to your logged-in account.

Typical flow:

1. Run `telegramcleaner command-mode`
2. Complete login if the session is not already authorized
3. Open the target chat or channel in Telegram
4. Send one of the supported commands from your account

Supported commands:

- `da` or `deleteall` to delete all messages in the current chat
- `d 1000` or `delete 1000` to delete the last `1000` messages in the current chat
- `c @anotherchannel` or `clean @anotherchannel` to clean a specific channel
- `s` or `status` to show the current cleanup status
- `h` or `help` to list available commands
- `p` or `pause` to pause the active job
- `r` or `resume` to resume a paused job
- `x` or `stop` to stop the active job

The cleaner protects its own command and progress messages from being deleted while a job is running in the same chat.

If you choose to save the configuration, the CLI writes:

### `.env`

```env
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_telegram_api_hash_here
SESSION_NAME=telegramcleaner
```

### `channels.json`

```json
{
  "channels": [
    "@channel1",
    "@channel2",
    "-100123456789"
  ]
}
```

## Library Usage

```python
from telegramcleaner import TelegramCleaner, load_config

config = load_config()
cleaner = TelegramCleaner(config)
```

See [`examples/example_usage.py`](examples/example_usage.py) for a complete async example.

## Project Structure

```text
cleaner/
|-- telegramcleaner/
|   |-- __init__.py
|   |-- cleaner.py
|   |-- cli.py
|   |-- config.py
|   `-- wizard.py
|-- examples/
|   `-- example_usage.py
|-- .env.example
|-- .gitignore
|-- LICENSE
|-- README.md
|-- pyproject.toml
`-- requirements.txt
```

## Notes

- Deletion runs channel by channel to reduce rate-limit pressure.
- Each delete request uses a maximum batch size of 100 messages.
- If batch deletion fails, the cleaner falls back to single-message deletion for that batch.
- Flood wait handling is retried automatically before continuing.

## Author

GitHub: https://github.com/dtvanshulll
Telegram: https://t.me/dtvanshul

## License

This project is licensed under the MIT License. See `LICENSE` for details.
