# telegramcleaner

Powerful Telegram channel cleaner with CLI + userbot control.

## Install

```bash
pip install dtvanshul-telegram-cleaner
```

## Features

- Delete all messages from channel
- Delete last N messages
- Interactive channel selection
- Telegram command control (userbot)
- Progress tracking
- Pause / Resume / Stop support
- FloodWait handling
- Batch deletion (100 messages)

## Usage

### List channels

```bash
telegramcleaner list
```

### Delete all messages

```bash
telegramcleaner deleteall @channel
```

### Delete last N messages

```bash
telegramcleaner delete 1000 @channel
```

### Clean (alias)

```bash
telegramcleaner clean @channel
```

### Telegram command mode

```bash
telegramcleaner command-mode
```

Then inside Telegram:

```text
da -> delete all
d 1000 -> delete last 1000
c @channel -> clean channel
s -> status
p -> pause
r -> resume
x -> stop
```

## First Run

You will be asked for:

- Telegram API ID
- Telegram API HASH

Get them from:
https://my.telegram.org

## Important

- You must be admin in the channel
- Uses your Telegram account (userbot)
- Works only while script is running

## Author

GitHub: https://github.com/dtvanshulll
Telegram: https://t.me/dtvanshul

## License

MIT License
