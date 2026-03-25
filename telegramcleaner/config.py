from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values

DEFAULT_SESSION_NAME = "telegramcleaner"
DEFAULT_ENV_FILE = ".env"
DEFAULT_CHANNELS_FILE = "channels.json"
DEFAULT_LOG_FILE = "telegramcleaner.log"


@dataclass(frozen=True, slots=True)
class CleanerConfig:
    api_id: int
    api_hash: str
    session_name: str
    channels: tuple[str, ...] = ()
    env_path: Path | None = None
    channels_path: Path | None = None


def get_default_paths(base_dir: str | Path | None = None) -> tuple[Path, Path]:
    root = Path.cwd() if base_dir is None else Path(base_dir).expanduser().resolve()
    return root / DEFAULT_ENV_FILE, root / DEFAULT_CHANNELS_FILE


def config_from_dict(
    values: Mapping[str, Any],
    *,
    env_path: str | Path | None = None,
    channels_path: str | Path | None = None,
) -> CleanerConfig:
    raw_channels = values.get("channels")
    if raw_channels is None:
        channels: tuple[str, ...] = ()
    elif isinstance(raw_channels, str):
        channels = parse_channels_input(raw_channels)
    elif isinstance(raw_channels, (list, tuple)):
        channels = parse_channels_input(",".join(str(channel) for channel in raw_channels))
    else:
        raise ValueError("Channels must be provided as a string, list, or tuple.")

    api_id = _parse_api_id(values.get("api_id") or values.get("TELEGRAM_API_ID"))
    api_hash = _require_non_empty(values.get("api_hash") or values.get("TELEGRAM_API_HASH"), "TELEGRAM_API_HASH")
    session_name = _normalize_session_name(values.get("session_name") or values.get("SESSION_NAME"))

    return CleanerConfig(
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        channels=channels,
        env_path=_resolve_optional_path(env_path),
        channels_path=_resolve_optional_path(channels_path),
    )


def load_config(
    *,
    env_file: str | Path = DEFAULT_ENV_FILE,
    channels_file: str | Path | None = DEFAULT_CHANNELS_FILE,
) -> CleanerConfig:
    env_path = Path(env_file).expanduser().resolve()
    channels_path = _resolve_optional_path(channels_file)

    env_values = _load_env_file(env_path)
    channels = ()
    if channels_path is not None and channels_path.is_file():
        channels = _load_channels(channels_path)

    return CleanerConfig(
        api_id=_parse_api_id(env_values.get("TELEGRAM_API_ID")),
        api_hash=_require_non_empty(env_values.get("TELEGRAM_API_HASH"), "TELEGRAM_API_HASH"),
        session_name=_normalize_session_name(env_values.get("SESSION_NAME")),
        channels=channels,
        env_path=env_path,
        channels_path=channels_path,
    )


def save_env_config(
    config: CleanerConfig,
    *,
    env_path: str | Path | None = None,
) -> Path:
    resolved_env_path = _resolve_optional_path(env_path) or config.env_path
    if resolved_env_path is None:
        resolved_env_path, _ = get_default_paths()

    resolved_env_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_env_path.write_text(
        "\n".join(
            (
                f"TELEGRAM_API_ID={config.api_id}",
                f"TELEGRAM_API_HASH={config.api_hash}",
                f"SESSION_NAME={config.session_name}",
                "",
            )
        ),
        encoding="utf-8",
    )

    return resolved_env_path


def save_config(
    config: CleanerConfig,
    *,
    env_path: str | Path | None = None,
    channels_path: str | Path | None = None,
) -> tuple[Path, Path]:
    resolved_env_path = _resolve_optional_path(env_path) or config.env_path
    resolved_channels_path = _resolve_optional_path(channels_path) or config.channels_path

    if resolved_env_path is None or resolved_channels_path is None:
        resolved_env_path, resolved_channels_path = get_default_paths()

    resolved_channels_path.parent.mkdir(parents=True, exist_ok=True)
    save_env_config(config, env_path=resolved_env_path)

    resolved_channels_path.write_text(
        json.dumps({"channels": list(config.channels)}, indent=2) + "\n",
        encoding="utf-8",
    )

    return resolved_env_path, resolved_channels_path


def parse_channels_input(raw_value: str) -> tuple[str, ...]:
    normalized_channels: list[str] = []
    seen_channels: set[str] = set()

    for value in raw_value.split(","):
        normalized = str(value).strip()
        if not normalized or normalized in seen_channels:
            continue
        normalized_channels.append(normalized)
        seen_channels.add(normalized)

    return tuple(normalized_channels)


def _load_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.is_file():
        raise FileNotFoundError(
            f"Environment file not found: {env_path}. Run 'telegramcleaner' to launch the setup wizard."
        )

    raw_values = dotenv_values(env_path)
    normalized = {key: value for key, value in raw_values.items() if value is not None}

    required_keys = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "SESSION_NAME")
    missing_keys = [key for key in required_keys if not normalized.get(key, "").strip()]
    if missing_keys:
        joined = ", ".join(missing_keys)
        raise ValueError(f"Missing required environment values in {env_path}: {joined}")

    return normalized


def _load_channels(channels_path: Path) -> tuple[str, ...]:
    if not channels_path.is_file():
        raise FileNotFoundError(
            f"Channel file not found: {channels_path}. Create it or run the CLI wizard to generate one."
        )

    try:
        payload = json.loads(channels_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {channels_path}: {error}") from error

    channels = payload.get("channels")
    if not isinstance(channels, list):
        raise ValueError(f"{channels_path} must contain a top-level 'channels' array.")

    normalized_channels = parse_channels_input(",".join(str(channel) for channel in channels))
    if not normalized_channels:
        raise ValueError(f"{channels_path} must define at least one channel.")

    return normalized_channels


def _parse_api_id(raw_value: str | int | None) -> int:
    value = _require_non_empty(None if raw_value is None else str(raw_value), "TELEGRAM_API_ID")
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError("TELEGRAM_API_ID must be an integer.") from error

    if parsed <= 0:
        raise ValueError("TELEGRAM_API_ID must be greater than zero.")
    return parsed


def _normalize_session_name(raw_value: str | None) -> str:
    if raw_value is None or not raw_value.strip():
        return DEFAULT_SESSION_NAME
    return raw_value.strip()


def invalid_credentials_message() -> str:
    return "Invalid Telegram API credentials. Get them from https://my.telegram.org"


def _require_non_empty(raw_value: str | None, key: str) -> str:
    if raw_value is None or not raw_value.strip():
        raise ValueError(f"Missing required value: {key}")
    return raw_value.strip()


def _resolve_optional_path(path_value: str | Path | None) -> Path | None:
    if path_value is None:
        return None
    return Path(path_value).expanduser().resolve()
