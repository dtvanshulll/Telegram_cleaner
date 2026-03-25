from __future__ import annotations

from .cleaner import (
    AvailableChannel,
    ChannelCleanupResult,
    TeleBridgeChannelCleaner,
    TelegramCleaner,
    configure_logging,
    configure_logging_with_file,
    list_channels,
    run_cleaner,
    run_command_mode,
)
from .config import CleanerConfig, DEFAULT_LOG_FILE, config_from_dict, load_config, save_config, save_env_config

__all__ = [
    "AvailableChannel",
    "ChannelCleanupResult",
    "CleanerConfig",
    "DEFAULT_LOG_FILE",
    "TeleBridgeChannelCleaner",
    "TelegramCleaner",
    "config_from_dict",
    "configure_logging",
    "configure_logging_with_file",
    "list_channels",
    "load_config",
    "run_cleaner",
    "run_command_mode",
    "save_env_config",
    "save_config",
]

__version__ = "0.1.1"
