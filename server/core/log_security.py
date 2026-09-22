"""Helpers for keeping untrusted values from forging log records."""

from __future__ import annotations


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
