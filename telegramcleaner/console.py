from __future__ import annotations

from typing import Final

try:
    from colorama import Fore, Style, just_fix_windows_console
except ImportError:  # pragma: no cover - optional dependency fallback
    Fore = Style = None

    def just_fix_windows_console() -> None:
        return None


COLOR_ENABLED: Final[bool] = Fore is not None and Style is not None


def initialize_console() -> None:
    just_fix_windows_console()


def print_info(message: str) -> None:
    print(_colorize(message, color=getattr(Fore, "CYAN", "")))


def print_success(message: str) -> None:
    print(_colorize(message, color=getattr(Fore, "GREEN", "")))


def print_warning(message: str) -> None:
    print(_colorize(message, color=getattr(Fore, "YELLOW", "")))


def print_error(message: str) -> None:
    print(_colorize(message, color=getattr(Fore, "RED", "")))


def print_header(message: str) -> None:
    print(_colorize(message, color=getattr(Fore, "BLUE", ""), bright=True))


def prompt_text(message: str) -> str:
    return input(_colorize(message, color=getattr(Fore, "MAGENTA", ""), bright=True))


def _colorize(message: str, *, color: str = "", bright: bool = False) -> str:
    if not COLOR_ENABLED:
        return message

    prefix = color
    if bright:
        prefix += getattr(Style, "BRIGHT", "")

    return f"{prefix}{message}{getattr(Style, 'RESET_ALL', '')}"
