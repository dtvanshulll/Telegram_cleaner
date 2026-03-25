from __future__ import annotations

from typing import Any

from .config import DEFAULT_SESSION_NAME, parse_channels_input


def run_setup_wizard(*, include_channels: bool = True) -> dict[str, Any]:
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
        answer = input(prompt).strip().lower()
        if not answer and default is not None:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer with y or n.")


def _prompt_api_id(prompt: str) -> int:
    while True:
        raw_value = input(prompt).strip()
        try:
            parsed = int(raw_value)
        except ValueError:
            print("Telegram API ID must be an integer.")
            continue

        if parsed <= 0:
            print("Telegram API ID must be greater than zero.")
            continue
        return parsed


def _prompt_non_empty(prompt: str) -> str:
    while True:
        raw_value = input(prompt).strip()
        if raw_value:
            return raw_value
        print("This value is required.")


def _prompt_with_default(label: str, default: str) -> str:
    raw_value = input(f"{label} [{default}]: ").strip()
    return raw_value or default


def _prompt_channels() -> tuple[str, ...]:
    while True:
        print("Channel usernames or IDs (comma separated)")
        print("Example: @channel1,@channel2,-100123456789")
        raw_value = input("> ").strip()
        channels = parse_channels_input(raw_value)
        if channels:
            return channels
        print("Enter at least one valid channel username or ID.")
