from __future__ import annotations

import asyncio

from telegramcleaner import TelegramCleaner, configure_logging, load_config


async def main() -> None:
    configure_logging(log_level="INFO")
    config = load_config()
    cleaner = TelegramCleaner(config)

    try:
        await cleaner.start()
        results = await cleaner.clean_channels()
    finally:
        await cleaner.stop()

    for result in results:
        print(
            f"{result.channel}: deleted={result.deleted_messages} "
            f"failed={result.failed_messages} total={result.total_messages}"
        )


if __name__ == "__main__":
    asyncio.run(main())
