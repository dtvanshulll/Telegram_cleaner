from __future__ import annotations

from typing import Any

from .config import DEFAULT_SESSION_NAME, parse_channels_input
from .console import print_header, print_info, print_warning, prompt_text


def run_setup_wizard(*, include_channels: bool = True) -> dict[str, Any]:
    print_header("Telegram Cleaner Setup")
    print_info("Enter your Telegram API credentials. You can get them from https://my.telegram.org")

    api_id = _prompt_api_id("Telegram API ID: ")
    api_hash = _prompt_non_empty("Telegram API HASH: ")
    session_name = _prompt_with_default("Session name", DEFAULT_SESSION_NAME)

    config: dict[str, Any] = {
        "api_id": api_id,
        "api_hash": api_hash,
        "session_name": session_name,
    }

    if include_channels:
        config["channels"] = list(_prompt_channels())

    return config


def prompt_yes_no(prompt: str, *, default: bool | None = None) -> bool:
    while True:
        answer = prompt_text(prompt).strip().lower()
        if not answer and default is not None:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print_warning("Please answer with y or n.")


def _prompt_api_id(prompt: str) -> int:
    while True:
        raw_value = prompt_text(prompt).strip()
        try:
            parsed = int(raw_value)
        except ValueError:
            print_warning("Telegram API ID must be an integer.")
            continue

        if parsed <= 0:
            print_warning("Telegram API ID must be greater than zero.")
            continue
        return parsed


def _prompt_non_empty(prompt: str) -> str:
    while True:
        raw_value = prompt_text(prompt).strip()
        if raw_value:
            return raw_value
        print_warning("This value is required.")


def _prompt_with_default(label: str, default: str) -> str:
    raw_value = prompt_text(f"{label} [{default}]: ").strip()
    return raw_value or default


def _prompt_channels() -> tuple[str, ...]:
    while True:
        print_info("Channel usernames or IDs (comma separated)")
        print_info("Example: @channel1,@channel2,-100123456789")
        raw_value = prompt_text("> ").strip()
        channels = parse_channels_input(raw_value)
        if channels:
            return channels
        print_warning("Enter at least one valid channel username or ID.")
