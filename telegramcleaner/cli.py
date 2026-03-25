from __future__ import annotations

import asyncio
import argparse

from telebridge.errors import AuthenticationError, ConfigurationError, TeleBridgeError

from .cleaner import TelegramCleaner, configure_logging, list_channels, run_command_mode
from .config import CleanerConfig, config_from_dict, get_default_paths, load_config, save_env_config
from .wizard import run_setup_wizard

COMMAND_ALIASES = {
    "da": "deleteall",
    "d": "delete",
    "c": "clean",
}


def build_runtime_config() -> CleanerConfig:
    env_path, _ = get_default_paths()

    if env_path.is_file():
        try:
            return load_config(env_file=env_path, channels_file=None)
        except (FileNotFoundError, ValueError) as error:
            print(f"Unable to load saved credentials from {env_path}: {error}")
            print("Starting first-run setup.\n")

    print("Telegram Cleaner Setup")
    wizard_config = run_setup_wizard(include_channels=False)
    config = config_from_dict(wizard_config, env_path=env_path, channels_path=None)
    saved_env_path = save_env_config(config, env_path=env_path)
    print(f"Saved credentials to {saved_env_path}")
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Telegram Cleaner CLI",
        prog="telegramcleaner",
    )
    subparsers = parser.add_subparsers(dest="command")

    deleteall_parser = subparsers.add_parser(
        "deleteall",
        aliases=["da"],
        help="Delete all messages from a channel or group.",
    )
    deleteall_parser.add_argument("channel", help="Channel username, invite reference, or chat identifier.")

    delete_parser = subparsers.add_parser(
        "delete",
        aliases=["d"],
        help="Delete the last N messages from a channel or group.",
    )
    delete_parser.add_argument("value", type=_parse_positive_int, help="Number of recent messages to delete.")
    delete_parser.add_argument("channel", help="Channel username, invite reference, or chat identifier.")

    clean_parser = subparsers.add_parser("clean", aliases=["c"], help="Alias for deleteall.")
    clean_parser.add_argument("channel", help="Channel username, invite reference, or chat identifier.")

    subparsers.add_parser("list", help="List admin channels/groups and select one interactively.")
    subparsers.add_parser(
        "command-mode",
        help="Run the Telegram-controlled userbot mode and listen for outgoing cleanup commands.",
    )

    return parser


def _parse_positive_int(raw_value: str) -> int:
    try:
        parsed = int(raw_value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Invalid number: {raw_value}") from error

    if parsed <= 0:
        raise argparse.ArgumentTypeError("Number must be greater than zero.")
    return parsed


async def _run_with_cleaner(
    config: CleanerConfig,
    operation,
) -> int:
    cleaner = TelegramCleaner(config)
    await cleaner.start()
    try:
        return await operation(cleaner)
    finally:
        await cleaner.stop()


async def _run_deleteall(config: CleanerConfig, channel: str) -> int:
    async def operation(cleaner: TelegramCleaner) -> int:
        result = await cleaner.clean_channel(channel)
        return _print_cleanup_result(result)

    return await _run_with_cleaner(config, operation)


async def _run_delete(config: CleanerConfig, value: int, channel: str) -> int:
    async def operation(cleaner: TelegramCleaner) -> int:
        result = await cleaner.clean_last_n(channel, value)
        return _print_cleanup_result(result)

    return await _run_with_cleaner(config, operation)


async def _run_list(config: CleanerConfig) -> int:
    async def operation(cleaner: TelegramCleaner) -> int:
        channels = await list_channels(cleaner)
        if not channels:
            print("No admin channels or groups found for this account.")
            return 1

        for index, channel in enumerate(channels, start=1):
            print(f"[{index}] {channel.label}")

        while True:
            raw_choice = input("Select channel number: ").strip()
            try:
                choice = int(raw_choice)
            except ValueError:
                print("Please enter a valid number.")
                continue

            if 1 <= choice <= len(channels):
                break

            print(f"Please select a number between 1 and {len(channels)}.")

        selected = channels[choice - 1]
        print(f"\nCleaning {selected.label}")
        result = await cleaner.clean_channel(selected.reference)
        return _print_cleanup_result(result)

    return await _run_with_cleaner(config, operation)


def _print_cleanup_result(result) -> int:
    if result.error:
        print(
            f"\nCleanup failed for {result.channel}\n"
            f"Deleted: {result.deleted_messages}\n"
            f"Failed: {result.failed_messages}\n"
            f"Error: {result.error}"
        )
        return 1

    if result.failed_messages:
        print(
            f"\nFinished cleanup for {result.channel} with issues\n"
            f"Deleted: {result.deleted_messages}\n"
            f"Failed: {result.failed_messages}"
        )
        return 1

    print(
        f"\nFinished cleanup for {result.channel}\n"
        f"Deleted: {result.deleted_messages}\n"
        f"Failed: {result.failed_messages}"
    )
    return 0


def _confirm_deleteall(channel: str) -> bool:
    confirm = input(f"Delete all messages from {channel}? (y/n): ").strip().lower()
    return confirm in {"y", "yes"}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        args.command = "list"

    args.command = COMMAND_ALIASES.get(args.command, args.command)

    configure_logging()

    try:
        config = build_runtime_config()
        if args.command == "command-mode":
            return asyncio.run(run_command_mode(config))
        if args.command in {"deleteall", "clean"}:
            if not _confirm_deleteall(args.channel):
                print("Cleanup cancelled.")
                return 1
            return asyncio.run(_run_deleteall(config, args.channel))
        if args.command == "delete":
            return asyncio.run(_run_delete(config, args.value, args.channel))
        if args.command == "list":
            return asyncio.run(_run_list(config))
        parser.print_help()
        return 1
    except KeyboardInterrupt:
        message = "\nCommand mode interrupted by user." if args.command == "command-mode" else "\nCleanup interrupted by user."
        print(message)
        return 130
    except (AuthenticationError, ConfigurationError, TeleBridgeError, FileNotFoundError, ValueError) as error:
        print(f"\nError: {error}")
        return 1
