from __future__ import annotations

import asyncio

from telegramcleaner import CleanerConfig, TelegramCleaner


async def main() -> None:
    config = CleanerConfig(
        api_id=123456,
        api_hash="your_telegram_api_hash_here",
        session_name="telegramcleaner",
        channels=("@channel1", "@channel2", "-100123456789"),
    )

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
