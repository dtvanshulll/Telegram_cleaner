from __future__ import annotations

import argparse
import asyncio
from typing import Awaitable, Callable

from telebridge.errors import AuthenticationError, ConfigurationError, TeleBridgeError

from .cleaner import TelegramCleaner, configure_logging, list_channels, run_command_mode
from .config import (
    CleanerConfig,
    config_from_dict,
    get_default_paths,
    invalid_credentials_message,
    load_config,
    save_env_config,
)
from .console import (
    initialize_console,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
    prompt_text,
)
from .wizard import prompt_yes_no, run_setup_wizard

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
            print_warning(f"Unable to load saved credentials from {env_path}: {error}")
            print_info("Starting first-run setup.\n")

    wizard_config = run_setup_wizard(include_channels=False)
    config = config_from_dict(wizard_config, env_path=env_path, channels_path=None)
    saved_env_path = save_env_config(config, env_path=env_path)
    print_success(f"Saved credentials to {saved_env_path}")
    print_info("Your Telegram session will be reused automatically on future runs.")
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Powerful Telegram channel cleaner CLI with userbot control and progress tracking.",
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

    subparsers.add_parser("list", help="List admin channels/groups, select one, and clean it.")
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
    operation: Callable[[TelegramCleaner], Awaitable[int]],
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
            print_warning("No admin channels or groups found for this account.")
            return 1

        print_header("Available Admin Channels")
        for index, channel in enumerate(channels, start=1):
            print_info(f"[{index}] {channel.label}")

        while True:
            raw_choice = prompt_text("Select channel number (or q to cancel): ").strip()
            if raw_choice.lower() in {"q", "quit", "exit"}:
                print_warning("Selection cancelled.")
                return 1

            try:
                choice = int(raw_choice)
            except ValueError:
                print_warning("Please enter a valid number.")
                continue

            if 1 <= choice <= len(channels):
                break

            print_warning(f"Please select a number between 1 and {len(channels)}.")

        selected = channels[choice - 1]
        if not _confirm_deleteall(selected.label):
            print_warning("Cleanup cancelled.")
            return 1

        print_info(f"\nCleaning {selected.label}")
        result = await cleaner.clean_channel(selected.reference)
        return _print_cleanup_result(result)

    return await _run_with_cleaner(config, operation)


def _print_cleanup_result(result) -> int:
    if result.error:
        print_error(
            f"\nCleanup failed for {result.channel}\n"
            f"Deleted: {result.deleted_messages}\n"
            f"Failed: {result.failed_messages}\n"
            f"Error: {result.error}"
        )
        return 1

    if result.failed_messages:
        print_warning(
            f"\nFinished cleanup for {result.channel} with issues\n"
            f"Deleted: {result.deleted_messages}\n"
            f"Failed: {result.failed_messages}"
        )
        return 1

    print_success(
        f"\nFinished cleanup for {result.channel}\n"
        f"Deleted: {result.deleted_messages}\n"
        f"Failed: {result.failed_messages}"
    )
    return 0


def _confirm_deleteall(channel: str) -> bool:
    return prompt_yes_no(f"Are you sure you want to delete all messages from {channel}? (y/n): ")


def _looks_like_invalid_credentials(error: Exception) -> bool:
    message = str(error).casefold()
    credential_markers = (
        "api_id",
        "api hash",
        "api_hash",
        "api id",
        "api credentials",
        "api_key",
        "auth key",
    )
    return any(marker in message for marker in credential_markers)


def main() -> int:
    initialize_console()
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
                print_warning("Cleanup cancelled.")
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
        print_warning(message)
        return 130
    except AuthenticationError:
        print_error(f"\nError: {invalid_credentials_message()}")
        return 1
    except (ConfigurationError, TeleBridgeError) as error:
        if _looks_like_invalid_credentials(error):
            print_error(f"\nError: {invalid_credentials_message()}")
            return 1
        print_error(f"\nError: {error}")
        return 1
    except (FileNotFoundError, ValueError) as error:
        print_error(f"\nError: {error}")
        return 1
