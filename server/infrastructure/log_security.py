"""Helpers for keeping untrusted values from forging log records."""

from __future__ import annotations

import logging
from typing import Any


def safe_log_value(value: object, *, max_length: int = 512) -> str:
    """Return a bounded, single-line representation suitable for log arguments."""
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    escaped = "".join(
        character
        if (
            character >= " "
            and character != "\x7f"
            and character not in {"\x85", "\u2028", "\u2029"}
        )
        else f"\\x{ord(character):02x}"
        for character in text
    )
    if len(escaped) <= max_length:
        return escaped
    return f"{escaped[:max_length]}...[truncated]"


def _safe_log_argument(value: Any) -> Any:
    """Sanitize a logging argument without breaking numeric format specifiers."""
    if value is None or isinstance(value, (bool, int, float, complex)):
        return value
    if isinstance(value, dict):
        return {key: _safe_log_argument(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        container = type(value)
        items = [_safe_log_argument(item) for item in value]
        if container is tuple:
            return tuple(items)
        if container is set:
            return set(items)
        if container is frozenset:
            return frozenset(items)
        return items
    return safe_log_value(value)


class SafeLogFilter(logging.Filter):
    """Sanitize formatted messages and arguments before handlers emit them."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = safe_log_value(record.msg, max_length=4096)
        if record.args:
            record.args = _safe_log_argument(record.args)
        if hasattr(record, "extra_data"):
            record.extra_data = _safe_log_argument(record.extra_data)
        return True


def install_safe_log_filter(handler: logging.Handler) -> None:
    """Attach the log-injection defense to a handler exactly once."""
    if not any(isinstance(item, SafeLogFilter) for item in handler.filters):
        handler.addFilter(SafeLogFilter())
